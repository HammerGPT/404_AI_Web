from fastapi.testclient import TestClient
from app.main import app


def test_login_and_history():
    with TestClient(app) as client:
        assert client.get('/api/reports').status_code==401
        assert client.post('/api/login',json={'password':'wrong'}).status_code==401
        r=client.post('/api/login',json={'password':'TEST_ONLY_PASSWORD_123456'})
        assert r.status_code==200
        csrf=r.json()['csrf']
        assert client.get('/api/me').json()['csrf']==csrf
        assert client.post('/api/models',json={'base_url':'https://api.example.com','key':'x'}).status_code==403
        assert client.get('/api/reports').status_code==200
        assert client.post('/api/logout',json={},headers={'X-CSRF-Token':csrf}).status_code==200
        assert client.get('/api/reports').status_code==401
