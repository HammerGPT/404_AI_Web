import asyncio
import os
import json
import pytest
from app.engine import (build_payload, normalize_usage, check_assertion, cases_for,
                        estimate_cost, parse_result, run_suite, resolve_protocol)
from app.security import validate_target,api_path,TargetError


def test_suite_sizes():
    assert len(cases_for('quick'))==4
    assert len(cases_for('standard'))==8
    assert len(cases_for('deep',True))==17


def test_api_paths():
    assert api_path('https://api.example.com/v1','openai_chat')=='https://api.example.com/v1/chat/completions'
    assert api_path('https://api.example.com','anthropic')=='https://api.example.com/v1/messages'
    assert api_path('https://api.example.com/v1','openai_responses')=='https://api.example.com/v1/responses'
    assert api_path('https://a.example.com/v1beta','gemini','gemini-2.5-flash')=='https://a.example.com/v1beta/models/gemini-2.5-flash:generateContent'


def test_url_rejections():
    for url in ['http://example.com','https://u:p@example.com','https://example.com?key=secret','https://example.com/#frag','file:///etc/passwd']:
        with pytest.raises(TargetError):validate_target(url)


def test_usage_openai_no_double_reasoning():
    usage=normalize_usage({'prompt_tokens':500,'completion_tokens':100,'prompt_tokens_details':{'cached_tokens':300},'completion_tokens_details':{'reasoning_tokens':40}},'openai_chat')
    assert usage['input_tokens']==500
    assert usage['reasoning_tokens']==40
    assert estimate_cost(usage,{'input':2,'output':8,'cache_read':0.2,'group_ratio':1.5})==round(((200*2+300*.2+100*8)/1e6)*1.5,8)


def test_usage_anthropic_cache():
    usage=normalize_usage({'input_tokens':100,'cache_creation_input_tokens':30,'cache_read_input_tokens':400,'output_tokens':10},'anthropic')
    assert usage['input_tokens']==530
    assert usage['cache_read_tokens']==400


def test_usage_gemini_thinking_in_output():
    usage=normalize_usage({'promptTokenCount':100,'candidatesTokenCount':30,'thoughtsTokenCount':70},'gemini')
    assert usage['output_tokens']==100


def test_build_payload():
    p=build_payload('anthropic','claude-x','reasoning','a',80)
    assert p['max_tokens']>=1600
    assert 'thinking' not in p
    assert build_payload('anthropic','claude-x','reasoning','a',80,reasoning='on')['thinking']['type']=='adaptive'
    p=build_payload('openai_chat','gpt-6','connect','a',80)
    assert 'max_completion_tokens' in p
    p=build_payload('openai_responses','gpt-6','tool','a',80)
    assert p['tool_choice']['name']=='convert_length'


def test_parse_openai():
    parsed=parse_result({'model':'m','choices':[{'message':{'content':'PING'}}], 'usage':{'prompt_tokens':10,'completion_tokens':3}},'openai_chat','connect')
    assert parsed['text']=='PING'
    assert parsed['usage']['input_tokens']==10


def test_assertion():
    assert check_assertion('logic','不能',False)[0]=='pass'
    assert check_assertion('json','{"code":"OK","count":3}',False)[0]=='pass'
    assert check_assertion('json','```json\n{}\n```',False)[0]=='warn'

@pytest.mark.asyncio
async def test_mock_suite(monkeypatch):
    import app.engine as engine
    class FakeResp:
        status_code=200
    async def fake_request(method,url,headers,payload=None,timeout=40):
        assert headers.get('Authorization')=='Bearer secret'
        message='PING'
        if 'code' in payload['messages'][0]['content']:message='{"code":"OK","count":3}'
        if '0 到 9' in payload['messages'][0]['content']:message='7'
        out={'model':'test-model','choices':[{'message':{'content':message}}],'usage':{'prompt_tokens':40,'completion_tokens':12}}
        return FakeResp(),json.dumps(out).encode()
    async def fake_stream(url,headers,payload,timeout=40,params=None):
        yield ('line',.015,'data: '+json.dumps({'model':'test-model','choices':[{'delta':{'content':'天空蓝色'}}]}))
        yield ('line',.022,'data: '+json.dumps({'usage':{'prompt_tokens':10,'completion_tokens':7},'choices':[]}))
    monkeypatch.setattr(engine,'pinned_request',fake_request)
    monkeypatch.setattr(engine,'pinned_stream',fake_stream)
    config={'base_url':'https://example.com/v1','key':'secret','model':'test-model','mode':'quick','protocol':'openai_chat','reasoning':'auto','rates':{'input':2,'output':6,'group_ratio':1}}
    report=await run_suite(config)
    assert report['stats']['requests']==4
    assert report['stats']['usage']['reported_requests']==4
    assert report['stats']['estimated_cost_usd'] is not None
    assert 'secret' not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', [401, 429, 502, 0])
async def test_stream_error_does_not_persist_provider_body(monkeypatch, tmp_path, status_code):
    import app.engine as engine
    from app import store

    fake_key = 'FAKE_KEY_FOR_ERROR_REGRESSION'

    class FakeResp:
        status_code = 200

    async def fake_request(*args, **kwargs):
        return FakeResp(), json.dumps({'choices': [{'message': {'content': 'PING'}}]}).encode()

    async def fake_stream(*args, **kwargs):
        yield ('error', status_code, 'provider-private-details: ' + fake_key)

    monkeypatch.setattr(engine, 'pinned_request', fake_request)
    monkeypatch.setattr(engine, 'pinned_stream', fake_stream)
    monkeypatch.setattr(store, 'DB', tmp_path / 'reports.db')
    report = await run_suite({'base_url': 'https://example.com/v1', 'key': fake_key,
                              'model': 'test-model', 'mode': 'quick', 'protocol': 'openai_chat'})
    sample = report['samples'][-1]
    assert sample['status'] == 'error'
    if status_code:
        assert f'HTTP {status_code}' in sample['note']
    else:
        assert '大小上限' in sample['note']
    store.init()
    store.save(report)
    saved = json.dumps(store.get(report['id']))
    assert fake_key not in saved
    assert 'provider-private-details' not in saved
