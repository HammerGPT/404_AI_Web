import sqlite3
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
import pytest
from fastapi.testclient import TestClient
from app import auth,store,main

PASSWORD='TEST_ONLY_PASSWORD_123456'
NEW_PASSWORD='新的测试密码用于会话验证2026'


def login(client,password=PASSWORD):
    response=client.post('/api/login',json={'password':password})
    assert response.status_code==200
    return {'X-CSRF-Token':response.json()['csrf']}


def test_password_and_session_secrets_are_not_stored_in_plaintext():
    with TestClient(main.app) as client:
        headers=login(client)
        token=client.cookies.get('tester_session')
        raw=store.DB.read_bytes()
        for secret in (PASSWORD,token,headers['X-CSRF-Token']):assert secret.encode() not in raw
        with sqlite3.connect(store.DB) as db:
            assert db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]==auth.token_hash(token)
            encoded=db.execute('SELECT password_hash FROM admin_account').fetchone()[0]
        assert auth.password_matches(PASSWORD,encoded)
        assert not auth.password_matches('wrong-password',encoded)


def test_password_hashes_use_distinct_salts():
    first=auth.password_hash(NEW_PASSWORD);second=auth.password_hash(NEW_PASSWORD)
    assert first!=second
    assert auth.password_matches(NEW_PASSWORD,first) and auth.password_matches(NEW_PASSWORD,second)


def test_session_survives_restart_and_environment_does_not_override(monkeypatch):
    with TestClient(main.app) as client:
        headers=login(client);token=client.cookies.get('tester_session')
    monkeypatch.setenv('ADMIN_PASSWORD','REPLACE_WITH_SOMETHING_ELSE')
    with TestClient(main.app) as restarted:
        restarted.cookies.set('tester_session',token,domain='testserver.local',path='/')
        assert restarted.get('/api/me').json()['csrf']==headers['X-CSRF-Token']
        assert restarted.get('/api/account').json()['session_days']==7
        assert restarted.post('/api/logout',json={},headers=headers).status_code==200
        assert restarted.get('/api/me').status_code==401
        login(restarted)


def test_cookie_and_server_expiration_renew_together(monkeypatch):
    clock=[auth.now()];monkeypatch.setattr(auth,'now',lambda:clock[0])
    monkeypatch.setattr(main,'COOKIE_SECURE',True)
    with TestClient(main.app,base_url='https://testserver') as client:
        login(client);token=client.cookies.get('tester_session')
        original=auth.read_session(token)['expires_at']
        clock[0]+=3600
        response=client.get('/api/me');cookie=SimpleCookie();cookie.load(response.headers['set-cookie'])
        current=auth.read_session(token)
        assert current['expires_at']==clock[0]+7*86400 and current['expires_at']>original
        value=cookie['tester_session']
        assert int(value['max-age'])==7*86400 and value['httponly'] and value['secure']
        assert value['samesite']=='strict'
        assert int(parsedate_to_datetime(value['expires']).timestamp())==current['expires_at']
        clock[0]=current['expires_at']
        response=client.get('/api/me');assert response.status_code==401
        assert 'set-cookie' not in response.headers


def test_custom_response_also_renews_cookie(monkeypatch):
    async def pdf(data):return b'%PDF-MOCK'
    monkeypatch.setattr(main.pdf_renderer,'render',pdf)
    store.save({'id':'auth-pdf','created_at':'2026-10-24','model':'test','mode':'quick','protocol':'openai_chat','status':'pass'})
    with TestClient(main.app) as client:
        login(client)
        response=client.get('/api/reports/auth-pdf/pdf')
        assert response.status_code==200 and 'max-age=604800' in response.headers['set-cookie'].lower()


def test_password_change_requires_current_password_and_csrf():
    with TestClient(main.app) as first, TestClient(main.app) as other:
        headers=login(first);login(other)
        token=first.cookies.get('tester_session')
        body={'current_password':PASSWORD,'new_password':NEW_PASSWORD,'confirm_password':NEW_PASSWORD}
        assert first.post('/api/account/password',json=body).status_code==403
        assert first.post('/api/account/password',json={**body,'current_password':'wrong'},headers=headers).status_code==400
        assert first.get('/api/me').status_code==200
        assert first.post('/api/account/password',json={**body,'confirm_password':'not-the-same'},headers=headers).status_code==400
        response=first.post('/api/account/password',json=body,headers=headers)
        assert response.status_code==200
        assert len(response.headers.get_list('set-cookie'))==1
        assert 'max-age=0' in response.headers['set-cookie'].lower()
        assert auth.read_session(token) is None and auth.renew_session(token) is None
        assert first.get('/api/me').status_code==401 and other.get('/api/me').status_code==401
        assert first.post('/api/login',json={'password':PASSWORD}).status_code==401
        login(first,NEW_PASSWORD)
        assert NEW_PASSWORD.encode() not in store.DB.read_bytes()
    with TestClient(main.app) as restarted:
        assert restarted.post('/api/login',json={'password':PASSWORD}).status_code==401
        login(restarted,NEW_PASSWORD)


def test_stale_password_verification_cannot_create_a_session():
    auth.init(PASSWORD);version=auth.verify_password(PASSWORD)
    token,_=auth.create_session(version)
    auth.change_password(token,PASSWORD,NEW_PASSWORD)
    with pytest.raises(auth.InvalidSession):auth.create_session(version)


def test_session_policy_auth_validation_and_persistence():
    with TestClient(main.app) as client:
        assert client.get('/api/account').status_code==401
        headers=login(client)
        assert client.put('/api/account/session',json={'session_days':14}).status_code==403
        for days in (0,31,1.5,True):
            assert client.put('/api/account/session',json={'session_days':days},headers=headers).status_code==422
        response=client.put('/api/account/session',json={'session_days':14},headers=headers)
        assert response.status_code==200 and response.json()['session_days']==14
        assert 'max-age=1209600' in response.headers['set-cookie'].lower()
    with TestClient(main.app) as restarted:
        login(restarted);assert restarted.get('/api/account').json()['session_days']==14


def test_shortening_expires_idle_sessions_and_extension_does_not_restore_them(monkeypatch):
    clock=[auth.now()];monkeypatch.setattr(auth,'now',lambda:clock[0]);auth.init(PASSWORD)
    current,_=auth.create_session(1);idle,_=auth.create_session(1)
    clock[0]+=2*86400
    auth.update_session_days(current,1)
    assert auth.read_session(current) and auth.read_session(idle) is None
    auth.update_session_days(current,30)
    assert auth.read_session(idle) is None
    clock[0]+=31*86400
    assert auth.renew_session(current) is None


def test_session_expiring_while_waiting_for_database_lock_is_not_renewed(monkeypatch):
    clock=[auth.now()];monkeypatch.setattr(auth,'now',lambda:clock[0]);auth.init(PASSWORD)
    token,session=auth.create_session(1)
    class DelayedLock:
        def __enter__(self):clock[0]=session['expires_at']
        def __exit__(self,*args):pass
    monkeypatch.setattr(store,'LOCK',DelayedLock())
    assert auth.renew_session(token) is None


def test_logout_cannot_renew_or_restore_cookie():
    with TestClient(main.app) as client:
        headers=login(client);token=client.cookies.get('tester_session')
        response=client.post('/api/logout',json={},headers=headers)
        assert response.status_code==200 and len(response.headers.get_list('set-cookie'))==1
        assert 'max-age=0' in response.headers['set-cookie'].lower()
        assert auth.renew_session(token) is None


def test_account_errors_never_echo_passwords():
    with TestClient(main.app) as client:
        headers=login(client);secret='FAKE_AUTH_SECRET'*30
        response=client.post('/api/account/password',json={'current_password':secret,'new_password':'x','confirm_password':secret},headers=headers)
        assert response.status_code==422 and secret not in response.text


def test_invalid_password_encoding_does_not_escape_validation():
    with TestClient(main.app) as client:
        headers=login(client)
        response=client.post('/api/account/password',content=b'{"current_password":"TEST_ONLY_PASSWORD_123456","new_password":"bad-secret-\\ud800","confirm_password":"bad-secret-\\ud800"}',headers={**headers,'Content-Type':'application/json'})
        assert response.status_code==422 and 'bad-secret' not in response.text
        assert client.get('/api/me').status_code==200


def test_unexpected_password_backend_error_is_not_exposed(monkeypatch):
    def failure(*args):raise ValueError('FAKE_PRIVATE_BACKEND_VALUE')
    monkeypatch.setattr(auth,'change_password',failure)
    with TestClient(main.app) as client:
        headers=login(client)
        response=client.post('/api/account/password',json={'current_password':PASSWORD,'new_password':NEW_PASSWORD,'confirm_password':NEW_PASSWORD},headers=headers)
        assert response.status_code==503 and 'FAKE_PRIVATE_BACKEND_VALUE' not in response.text


def test_local_password_recovery_revokes_sessions():
    auth.init(PASSWORD);token,_=auth.create_session(1)
    auth.reset_password(NEW_PASSWORD)
    assert auth.read_session(token) is None and auth.verify_password(PASSWORD) is None
    assert auth.verify_password(NEW_PASSWORD) is not None


def test_relogin_rotates_cookie_and_revokes_replaced_token():
    with TestClient(main.app) as client:
        login(client);old=client.cookies.get('tester_session')
        login(client);new=client.cookies.get('tester_session')
        assert old!=new and auth.read_session(old) is None
        assert auth.read_session(new) is not None


def test_report_stream_notifies_revoked_session(monkeypatch):
    report={'id':'stream-auth','created_at':'2026-10-24','model':'test','mode':'quick','protocol':'openai_chat',
            'status':'running','run_state':'running','revision':1,'samples':[],'stats':{'estimated_cost_usd':None,'usage':{}},'disclaimers':[]}
    store.save(report)
    with TestClient(main.app) as client:
        login(client);token=client.cookies.get('tester_session')
        original=auth.read_session;reads=[]
        def revoking_read(value):
            reads.append(1)
            if len(reads)==3:auth.revoke_session(value)
            return original(value)
        monkeypatch.setattr(auth,'read_session',revoking_read)
        # Startup correctly marks old running snapshots interrupted; emulate a live run.
        store.save(report)
        response=client.get('/api/runs/stream-auth/events')
        assert 'event: snapshot' in response.text and 'event: expired' in response.text
        assert token not in response.text
