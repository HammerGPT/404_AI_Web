import pytest
import asyncio
from app import store, main
from app.runs import RunManager


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch, tmp_path):
    monkeypatch.setenv('ADMIN_PASSWORD','TEST_ONLY_PASSWORD_123456')
    monkeypatch.setattr(store,'DB',tmp_path/'test.db')
    monkeypatch.setattr(main,'COOKIE_SECURE',False)
    monkeypatch.setattr(main,'manager',RunManager())
    monkeypatch.setattr(main,'PASSWORD_WORKERS',asyncio.Semaphore(2))
    main.LOGIN_ATTEMPTS.clear()
    store.init()
