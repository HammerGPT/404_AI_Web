import asyncio
import json
import sqlite3
import uuid
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app import store, main, engine, runs
from app.adapters import build_payload
from app.catalog import PROTOCOLS


@pytest.mark.parametrize('protocol',list(PROTOCOLS))
def test_auto_does_not_override_upstream_reasoning(protocol):
    payload=build_payload(protocol,'test-model','reasoning','test',80,reasoning='auto')
    assert not any(k in payload for k in ('reasoning_effort','reasoning','thinking','output_config'))
    assert 'thinkingConfig' not in payload.get('generationConfig',{})


@pytest.mark.parametrize('level',['low','medium','high'])
@pytest.mark.parametrize('protocol',list(PROTOCOLS))
def test_selected_level_matches_wire_payload(protocol,level):
    payload=build_payload(protocol,'custom-alias','reasoning','test',80,reasoning=level)
    if protocol=='openai_chat':assert payload['reasoning_effort']==level
    elif protocol=='openai_responses':assert payload['reasoning']['effort']==level
    elif protocol=='anthropic':
        assert payload['thinking']=={'type':'adaptive'}
        assert payload['output_config']['effort']==level
    else:assert payload['generationConfig']['thinkingConfig']=={'includeThoughts':True,'thinkingLevel':level}
    ordinary=build_payload(protocol,'custom-alias','connect','PING',80,reasoning=level)
    assert not any(k in ordinary for k in ('reasoning_effort','reasoning','thinking','output_config'))
    assert 'thinkingConfig' not in ordinary.get('generationConfig',{})


def test_adaptive_and_legacy_parameter_compatibility():
    assert build_payload('anthropic','claude-test','reasoning','test',80,reasoning='adaptive')['thinking']=={'type':'adaptive'}
    for protocol in PROTOCOLS:
        payload=build_payload(protocol,'test','reasoning','test',80,reasoning='off')
        assert 'thinking' not in payload and 'reasoning' not in payload and 'reasoning_effort' not in payload


def login(client):
    response=client.post('/api/login',json={'password':'TEST_ONLY_PASSWORD_123456'})
    return {'X-CSRF-Token':response.json()['csrf']}


def test_billing_choices_auth_validation_and_restart():
    data={'options':[0.5,1,2.5],'default':2.5}
    with TestClient(main.app) as client:
        assert client.put('/api/settings/billing',json=data).status_code==401
        headers=login(client)
        assert client.put('/api/settings/billing',json=data).status_code==403
        for invalid in ({'options':[1,1],'default':1},{'options':[1,2],'default':3},{'options':[-1,1],'default':1}):
            assert client.put('/api/settings/billing',json=invalid,headers=headers).status_code==422
        assert client.put('/api/settings/billing',json=data,headers=headers).status_code==200
    with TestClient(main.app) as client:
        login(client)
        assert client.get('/api/catalog').json()['billing']==data


def test_nonfinite_settings_are_rejected():
    with pytest.raises(ValidationError):main.BillingSettings(options=[float('nan')],default=1)
    with pytest.raises(ValidationError):main.Config(base_url='https://example.com',key='fake',model='m',billing_multiplier=float('inf'))
    with pytest.raises(ValidationError):main.Config(base_url='https://example.com',key='fake',model='m',billing_multiplier=2,rates={'input':1,'output':2,'group_ratio':3})


def test_model_reasoning_config_and_rollback_compatibility():
    model=store.list_models()[0]
    model.update(reasoning_options=['auto','low','high'],reasoning_default='high')
    store.put_model(model,model['id']);store.init()
    assert store.reasoning_profile(model['model_id'],model['protocol'])=={'options':['auto','low','high'],'default':'high'}
    with sqlite3.connect(store.DB) as db:
        assert len(db.execute('PRAGMA table_info(model_presets)').fetchall())==7
        assert len(db.execute('PRAGMA table_info(reports)').fetchall())==8
        # Changes made by an older image must not attach stale capabilities to a new model.
        db.execute('UPDATE model_presets SET model_id=? WHERE id=?',('different-alias',model['id']))
    assert store.reasoning_profile('different-alias',model['protocol'])=={'options':['auto'],'default':'auto'}


def test_model_profile_rejects_protocol_mismatch_and_bad_default():
    base={'name':'custom','model_id':'custom','brand':'custom','protocol':'openai_chat'}
    with pytest.raises(ValidationError):main.ModelPreset(**base,reasoning_options=['auto','adaptive'],reasoning_default='auto')
    with pytest.raises(ValidationError):main.ModelPreset(**base,reasoning_options=['auto','low'],reasoning_default='high')


@pytest.mark.asyncio
async def test_multiplier_snapshot_and_cost_are_stable(monkeypatch):
    calls=[]
    class Response:status_code=200
    async def request(*args,**kwargs):
        calls.append(1)
        return Response(),json.dumps({'choices':[{'message':{'content':'PING'}}], 'usage':{'prompt_tokens':10,'completion_tokens':5}}).encode()
    async def stream(*args,**kwargs):
        calls.append(1)
        yield ('line',.1,'data: '+json.dumps({'choices':[{'delta':{'content':'PING'}}],'usage':{'prompt_tokens':10,'completion_tokens':5}}))
        yield ('line',.2,'data: [DONE]')
    monkeypatch.setattr(engine,'pinned_request',request);monkeypatch.setattr(engine,'pinned_stream',stream)
    cfg={'base_url':'https://example.com','model':'test','key':'FAKE_SETTINGS_KEY','protocol':'openai_chat','mode':'quick',
         'reasoning':'auto','billing_multiplier':2.5,'rates':{'input':1,'output':2}}
    manager=runs.RunManager();report=await manager.start(cfg,uuid.uuid4().hex)
    store.save_billing({'options':[1,9],'default':9})
    await manager.task;saved=store.get(report['id'])
    assert len(calls)==4
    assert saved['settings']['billing_multiplier']==2.5
    assert saved['stats']['estimated_cost_usd']==round((40+20*2)/1e6*2.5,8)
    cfg['rates']=None;next_report=await manager.start(cfg,uuid.uuid4().hex);await manager.task
    saved=store.get(next_report['id'])
    assert saved['settings']['billing_multiplier']==2.5 and saved['stats']['estimated_cost_usd'] is None
    from app.reports import render_report
    assert '2.5×' in render_report(saved) and '2.5×' in render_report(saved,False)


@pytest.mark.asyncio
async def test_unconfigured_reasoning_is_rejected_before_start():
    cfg={'base_url':'https://example.com','model':'unknown','key':'FAKE_KEY','protocol':'openai_chat','mode':'standard','reasoning':'high'}
    manager=runs.RunManager()
    with pytest.raises(ValueError):await manager.start(cfg,uuid.uuid4().hex)
    assert manager.task is None and not store.list_reports()


@pytest.mark.asyncio
async def test_model_default_and_explicit_auto_are_distinct(monkeypatch):
    preset={'name':'My model','model_id':'my-model','brand':'custom','protocol':'openai_responses',
            'sort_order':0,'enabled':True,'reasoning_options':['auto','high'],'reasoning_default':'high'}
    store.put_model(preset)
    async def suite(cfg,update,report_id,created_at):
        return engine.make_report(cfg,[],report_id,created_at,state='completed')
    monkeypatch.setattr(runs,'run_suite',suite)
    cfg={'base_url':'https://example.com','model':'my-model','key':'FAKE_KEY','protocol':'auto','mode':'standard'}
    manager=runs.RunManager()
    default=await manager.start(cfg,uuid.uuid4().hex);await manager.task
    assert store.get(default['id'])['settings']['reasoning']=='high'
    explicit=await manager.start({**cfg,'reasoning':'auto'},uuid.uuid4().hex);await manager.task
    assert store.get(explicit['id'])['settings']['reasoning']=='auto'
