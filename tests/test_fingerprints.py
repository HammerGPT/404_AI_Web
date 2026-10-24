import copy
from datetime import datetime,timezone,timedelta
import json
import uuid
import time
import pytest
from app import engine, fingerprints as fp, store, runs
from app.reports import render_report


def cfg(action='verify',host='relay.example.com'):
    return {'mode':'fingerprint','model':'test-model','protocol':'openai_chat','key':'FAKE_FINGERPRINT_KEY',
            'base_url':'https://'+host+'/v1','fingerprint_action':action,'fingerprint_source':'trusted',
            'fingerprint_name':'模拟可信来源','fingerprint_trusted':True}


def samples(variant='same'):
    result=[]
    counts={'anchor':7,'english':48,'chinese':96,'code':65,'unicode':83}
    for spec in fp.PLAN:
        kind=spec['kind'];group=spec['group']
        sample={**fp.specification(kind),'status':'pass','http_status':200,'latency_ms':100,'ttft_ms':None,'tps':120,
                'model_returned':'test-model','text':'','usage':{'input_tokens':20,'output_tokens':12,'reported':True,'complete':True},
                'response_complete':True,'finish_reason':'stop','thinking_observed':False,'tool_observed':False}
        if group=='token':
            value=counts[spec['probe_id']]
            if variant in ('replaced','mixed') and spec['probe_id']!='anchor' and (variant=='replaced' or spec['repeat']==2):value+=17
            sample['usage']['input_tokens']=value
        elif group=='behavior':sample['text']=spec['probe']['answers'][-1 if variant=='replaced' else 0]
        else:sample['text']=json.dumps(None if variant in ('replaced','weaker') else spec['probe']['expected'],ensure_ascii=False)
        sample['status'],sample['note']=fp.grade(kind,sample)
        result.append(sample)
    return result


def baseline():
    config=cfg('reference','reference.example.com')
    report=engine.make_report(config,samples(),'reference-report',datetime.now(timezone.utc).isoformat(),state='completed')
    return fp.baseline_document(config,report)


def test_profile_plan_has_distinct_measurement_ids():
    assert len(fp.PLAN)==99 and len(fp.BY_KIND)==99
    assert len(engine.cases_for('fingerprint'))==99
    assert all(p['limit']>0 for p in fp.PLAN)


def test_regular_checks_now_have_real_pass_fail_rules():
    assert engine.check_assertion('digit_format','7')[0]=='pass'
    assert engine.check_assertion('digit_format','我选7')[0]=='warn'
    assert engine.check_assertion('reasoning','7支笔')[0]=='pass'
    assert engine.check_assertion('reasoning','8支笔')[0]=='warn'
    for case in engine.CASES:assert not case[0].startswith('fingerprint')


def test_json_capability_checks_types_and_values():
    assert fp.equivalent([0,1], [0.0,1.0])
    assert not fp.equivalent([0,1],[False,True])
    assert not fp.equivalent(129,float('nan'))
    assert fp.normalized_answer(fp.PROFILE['behavior_probes'][0],'７。')=='7'
    assert fp.normalized_answer(fp.PROFILE['behavior_probes'][0],'七')=='7'
    assert fp.normalized_answer(fp.PROFILE['behavior_probes'][0],'数字是7') is None


def test_reference_requires_complete_stable_data():
    assert baseline()['measurement']['ready']
    for data in (samples()[:-1],samples('mixed'),samples('weaker')):
        report=engine.make_report(cfg('reference'),data,'bad-ref',datetime.now(timezone.utc).isoformat(),state='completed')
        assert report['fingerprint']['status']=='fail'
        assert fp.baseline_document(cfg('reference'),report) is None


def test_identical_reference_passes_without_identity_probability():
    config=cfg();config['_fingerprint_reference']=baseline()
    result=fp.analyze(config,samples(),'completed')
    assert result['status']=='pass'
    assert all(c['status']=='pass' for c in result['checks'])
    behavior=next(c for c in result['checks'] if c['name']=='行为分布对照')
    assert behavior['statistics']['p_value']==1
    assert 'probability' not in result


@pytest.mark.parametrize('variant,failed_check',[('replaced','行为分布对照'),('mixed','重复指纹一致性'),('weaker','能力基线对照')])
def test_detects_changed_features_even_when_model_id_is_forged(variant,failed_check):
    config=cfg();config['_fingerprint_reference']=baseline()
    data=samples(variant)
    assert {s['model_returned'] for s in data}=={'test-model'}
    result=fp.analyze(config,data,'completed')
    assert result['status']=='fail'
    assert next(c for c in result['checks'] if c['name']==failed_check)['status']=='fail'
    if variant=='replaced':
        assert next(c for c in result['checks'] if c['name']==failed_check)['statistics']['p_value']<=.005


def test_missing_usage_is_not_declared_model_substitution():
    data=samples();data[0]['fingerprint_measurement']['value']=None;data[0]['status']='warn'
    config=cfg();config['_fingerprint_reference']=baseline()
    result=fp.analyze(config,data,'completed')
    assert result['status']=='fail' and '数据不足' in result['title']
    assert '不能判断是否掺水' in result['detail']


def test_same_source_is_only_historical_consistency():
    config=cfg(host='reference.example.com');config['_fingerprint_reference']=baseline()
    result=fp.analyze(config,samples(),'completed')
    assert result['same_source'] and '同源历史一致性' in result['title']


def test_reference_conditions_and_expiry_are_checked():
    ref=baseline();fp.validate_reference(ref,cfg())
    changed=copy.deepcopy(ref);changed['conditions']['profile_hash']='wrong'
    with pytest.raises(ValueError):fp.validate_reference(changed,cfg())
    changed=copy.deepcopy(ref);changed['created_at']=(datetime.now(timezone.utc)-timedelta(days=31)).isoformat()
    with pytest.raises(ValueError):fp.validate_reference(changed,cfg())


def test_permutation_test_is_repeatable_and_tolerates_small_variation():
    reference={'a':{'values':['a']*6+['b']*6},'b':{'values':['x']*6+['y']*6}}
    target={'a':{'values':['a']*7+['b']*5},'b':{'values':['x']*5+['y']*7}}
    one=fp.permutation_test(reference,target,'behavior')
    assert one==fp.permutation_test(reference,target,'behavior')
    assert one['p_value']>.005


@pytest.mark.asyncio
async def test_baseline_capture_is_explicit_atomic_and_idempotent(monkeypatch):
    async def suite(config,update,report_id,created_at):
        return engine.make_report(config,samples(),report_id,created_at,state='completed')
    monkeypatch.setattr(runs,'run_suite',suite)
    manager=runs.RunManager();config=cfg('reference','reference.example.com')
    with pytest.raises(ValueError):await manager.start({**config,'fingerprint_trusted':False},uuid.uuid4().hex)
    nonce=uuid.uuid4().hex;started=await manager.start(config,nonce);await manager.task
    references=store.list_fingerprint_baselines();assert len(references)==1
    saved=store.get(started['id'])
    assert saved['fingerprint']['created_baseline']['id']==references[0]['id']
    assert 'FAKE_FINGERPRINT_KEY' not in store.DB.read_bytes().decode(errors='ignore')
    assert (await manager.start(config,nonce))['id']==started['id']
    assert len(store.list_fingerprint_baselines())==1
    store.set_fingerprint_baseline_enabled(references[0]['id'],False)
    with pytest.raises(ValueError):await manager.start({**cfg(),'fingerprint_reference':references[0]['id']},uuid.uuid4().hex)


def test_fingerprint_report_explains_failures_and_includes_reference_evidence():
    config=cfg();config['_fingerprint_reference']=baseline()
    report=engine.make_report(config,samples('replaced'),'test-report',datetime.now(timezone.utc).isoformat(),state='completed')
    html=render_report(report)
    assert report['status']=='attention'
    assert '指纹核验失败' in html and '行为分布对照' in html and 'reference.example.com' in html
    assert '仅采样' not in html
    assert 'p=' in html and '真实概率' in html
    assert 'centimeters' not in html


@pytest.mark.asyncio
async def test_fingerprint_stops_on_rate_limit_without_retry(monkeypatch):
    calls=[]
    class Response:status_code=429
    async def request(*args,**kwargs):calls.append(1);return Response(),b'private upstream body'
    monkeypatch.setattr(engine,'pinned_request',request)
    report=await engine.run_suite(cfg('reference'))
    assert len(calls)==1 and report['stats']['requests']==1
    assert '限流' in report['stop_reason'] and report['fingerprint']['status']=='fail'
    assert 'private upstream body' not in json.dumps(report)


@pytest.mark.asyncio
async def test_tokens_can_be_measured_when_output_is_capped(monkeypatch):
    class Response:status_code=200
    async def request(*args,**kwargs):
        return Response(),json.dumps({'choices':[{'message':{'content':''},'finish_reason':'length'}],'usage':{'prompt_tokens':7,'completion_tokens':64}}).encode()
    monkeypatch.setattr(engine,'pinned_request',request)
    item=await engine.run_one(cfg('reference'),'openai_chat',fp.cases()[0])
    assert item['status']=='pass' and item['fingerprint_measurement']['value']==7


def test_reference_api_requires_session_csrf_and_reports_saved_baseline(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    async def suite(config,update,report_id,created_at):
        return engine.make_report(config,samples(),report_id,created_at,state='completed')
    monkeypatch.setattr(runs,'run_suite',suite)
    with TestClient(main.app) as client:
        assert client.get('/api/fingerprints').status_code==401
        csrf=client.post('/api/login',json={'password':'TEST_ONLY_PASSWORD_123456'}).json()['csrf']
        headers={'X-CSRF-Token':csrf,'Idempotency-Key':uuid.uuid4().hex}
        assert client.post('/api/runs',json=cfg('reference')).status_code==403
        assert client.post('/api/runs',json={**cfg('reference'),'fingerprint_trusted':False},headers=headers).status_code==400
        response=client.post('/api/runs',json=cfg('reference','reference.example.com'),headers=headers)
        assert response.status_code==202
        rid=response.json()['id']
        for _ in range(100):
            report=client.get('/api/reports/'+rid).json()
            if report['run_state']=='completed':break
            time.sleep(.01)
        references=client.get('/api/fingerprints').json()
        assert references['requests']==99 and len(references['references'])==1
        baseline_id=references['references'][0]['id']
        data=client.get('/api/fingerprints/'+baseline_id).json()
        assert data['measurement']['ready'] and data['source_kind']=='trusted'
        assert 'FAKE_FINGERPRINT_KEY' not in json.dumps(data)
        assert client.put('/api/fingerprints/'+baseline_id,json={'enabled':False}).status_code==403
        assert client.put('/api/fingerprints/'+baseline_id,json={'enabled':False},headers=headers).status_code==200


@pytest.mark.asyncio
async def test_arbitrary_host_cannot_be_labeled_official():
    with pytest.raises(ValueError):
        await runs.RunManager().start({**cfg('reference'),'fingerprint_source':'official'},uuid.uuid4().hex)
