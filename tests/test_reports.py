import pytest
from fastapi.testclient import TestClient
from app import engine, store, main
from app.reports import render_report


def fixture_report():
    return engine.make_report({'base_url':'https://example.com','model':'<script>alert(1)</script>',
                              'key':'FAKE_PDF_KEY','mode':'quick','protocol':'openai_chat'},[],
                              'pdf-test','2026-10-24T00:00:00+00:00',state='completed')


def test_template_escapes_provider_content_and_shows_unknowns():
    report=fixture_report()
    html=render_report(report)
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html
    assert '无法计算' in html and '未上报' in html and 'TTFT' in html and 'TPS' in html
    assert '下载 PDF' in html and '下载 PDF' not in render_report(report,False)


def test_legacy_report_does_not_invent_evidence():
    old=fixture_report();old.pop('schema_version');old.pop('plan');old['samples']=[{'kind':'connect','title':'连接','status':'pass','text':'PING','usage':{}}]
    html=render_report(old)
    assert '旧版报告' in html and '旧报告未记录' in html
    assert '请仅输出 PING' not in html


def test_pdf_route_authentication_and_download_headers(monkeypatch):
    async def pdf(data):return b'%PDF-1.4\nMOCK'
    monkeypatch.setattr(main.pdf_renderer,'render',pdf)
    store.save(fixture_report())
    with TestClient(main.app) as client:
        assert client.get('/api/reports/pdf-test/pdf').status_code==401
        client.post('/api/login',json={'password':'TEST_ONLY_PASSWORD_123456'})
        response=client.get('/api/reports/pdf-test/pdf')
        assert response.content.startswith(b'%PDF') and response.headers['content-type']=='application/pdf'
        assert '.pdf' in response.headers['content-disposition']
        assert client.get('/api/reports/pdf-test/html').status_code==404
