import asyncio
import json
import sqlite3
import time
import uuid
import pytest
from fastapi.testclient import TestClient
from app import store, main, engine, runs
from app.runs import RunManager, BusyError

CFG={'base_url':'https://example.com/v1','key':'FAKE_RUN_KEY','model':'test-model','mode':'quick','protocol':'openai_chat'}


@pytest.mark.asyncio
async def test_idempotency_busy_disconnect_and_secret_free_snapshot(monkeypatch):
    gate=asyncio.Event()
    calls=[]
    async def suite(cfg,update,report_id,created_at):
        calls.append(cfg['key'])
        await gate.wait()
        return engine.make_report(cfg,[],report_id,created_at,state='completed')
    monkeypatch.setattr(runs,'run_suite',suite)
    manager=RunManager()
    first=await manager.start(CFG,'request-unique-0001')
    same=await manager.start(CFG,'request-unique-0001')
    assert same['id']==first['id']
    with pytest.raises(BusyError):
        await manager.start(CFG,'request-unique-0002')
    await asyncio.sleep(0)
    assert len(calls)==1
    assert CFG['key'] not in store.DB.read_bytes().decode(errors='ignore')
    gate.set();await manager.task
    again=await manager.start(CFG,'request-unique-0001')
    assert again['run_state']=='completed' and len(calls)==1


@pytest.mark.asyncio
async def test_shutdown_preserves_completed_evidence(monkeypatch):
    ready=asyncio.Event()
    async def suite(cfg,update,report_id,created_at):
        report=engine.make_report(cfg,[],report_id,created_at)
        report['samples']=[{'kind':'connect','title':'连接与协议','status':'pass','text':'PING'}]
        await update(report);ready.set();await asyncio.Event().wait()
    monkeypatch.setattr(runs,'run_suite',suite)
    manager=RunManager();first=await manager.start(CFG,'request-shutdown-01')
    await ready.wait();await manager.close()
    saved=store.get(first['id'])
    assert saved['run_state']=='interrupted' and saved['samples'][0]['text']=='PING'
    assert manager.active_id is None


def test_schema_upgrade_keeps_old_report_and_models(monkeypatch,tmp_path):
    old=tmp_path/'old.db';monkeypatch.setattr(store,'DB',old)
    payload={'id':'old','created_at':'2025-01-01','model':'old','mode':'quick','protocol':'openai_chat','status':'pass','samples':[]}
    with sqlite3.connect(old) as db:
        db.execute('CREATE TABLE reports(id TEXT PRIMARY KEY,created_at TEXT,model TEXT,label TEXT,mode TEXT,protocol TEXT,status TEXT,payload TEXT)')
        db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?)',('old','2025-01-01','old','','quick','openai_chat','pass',json.dumps(payload)))
    store.init();assert store.get('old')==payload
    with sqlite3.connect(old) as db:
        assert len(db.execute('PRAGMA table_info(reports)').fetchall())==8
        # The previous image's positional INSERT must remain compatible on rollback.
        db.execute('INSERT INTO reports VALUES (?,?,?,?,?,?,?,?)',('rollback','2025-01-01','old','','quick','openai_chat','pass',json.dumps(payload)))
    preset=store.list_models()[0];preset['enabled']=False;store.put_model(preset,preset['id'])
    store.init();assert not next(m for m in store.list_models() if m['id']==preset['id'])['enabled']


def login(client):
    result=client.post('/api/login',json={'password':'TEST_ONLY_PASSWORD_123456'})
    return {'X-CSRF-Token':result.json()['csrf']}


def test_catalog_auth_validation_and_persistence():
    with TestClient(main.app) as client:
        assert client.get('/api/catalog').status_code==401
        headers=login(client)
        cfg={'name':'我的渠道模型','model_id':'custom-alias','brand':'custom','protocol':'anthropic','enabled':True,'sort_order':-1}
        assert client.post('/api/catalog',json=cfg).status_code==403
        created=client.post('/api/catalog',json=cfg,headers=headers);assert created.status_code==201
        assert store.model_protocol('custom-alias')=='anthropic'
        assert client.post('/api/catalog',json=cfg,headers=headers).status_code==409
        assert client.post('/api/catalog',json={**cfg,'brand':'https://evil.example'},headers=headers).status_code==422
        assert client.put('/api/catalog/'+created.json()['id'],json={**cfg,'enabled':False},headers=headers).status_code==200
        assert store.model_protocol('custom-alias') is None
    with TestClient(main.app) as client:
        login(client)
        row=next(m for m in client.get('/api/catalog').json()['models'] if m['model_id']=='custom-alias')
        assert row['enabled'] is False


def test_run_api_sse_reconnect_does_not_repeat_upstream(monkeypatch):
    calls=[]
    class Response:status_code=200
    async def request(*args,**kwargs):
        calls.append(1)
        return Response(),json.dumps({'choices':[{'message':{'content':'PING'}}],'usage':{'prompt_tokens':2,'completion_tokens':3}}).encode()
    async def stream(*args,**kwargs):
        calls.append(1)
        yield ('line',.1,'data: '+json.dumps({'choices':[{'delta':{'content':'答案'}}],'usage':{'prompt_tokens':2,'completion_tokens':3}}))
        yield ('line',.2,'data: [DONE]')
    monkeypatch.setattr(engine,'pinned_request',request);monkeypatch.setattr(engine,'pinned_stream',stream)
    with TestClient(main.app) as client:
        headers=login(client);headers['Idempotency-Key']=uuid.uuid4().hex
        started=client.post('/api/runs',json=CFG,headers=headers);assert started.status_code==202
        report_id=started.json()['id']
        for _ in range(100):
            report=client.get('/api/reports/'+report_id).json()
            if report['run_state']=='completed':break
            time.sleep(.01)
        assert report['run_state']=='completed' and len(calls)==4
        assert report['stats']['counts']['sample']==1
        for _ in range(2):
            events=client.get('/api/runs/'+report_id+'/events')
            assert events.status_code==200 and 'event: snapshot' in events.text
            assert events.headers['x-accel-buffering']=='no'
            assert CFG['key'] not in events.text
        assert client.post('/api/runs',json=CFG,headers=headers).json()['id']==report_id
        assert len(calls)==4
        assert 'PDF' in client.get('/api/reports/'+report_id+'/view').text


def test_validation_does_not_echo_sensitive_input():
    with TestClient(main.app) as client:
        headers=login(client)
        marker='FAKE_VALIDATION_SECRET'*100
        response=client.post('/api/runs',json={**CFG,'key':marker},headers=headers)
        assert response.status_code==422 and marker not in response.text
