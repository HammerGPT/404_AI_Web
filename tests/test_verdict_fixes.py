"""Offline regressions from report answers; all upstream responses use fake data/keys."""
import json
import pytest
from app import engine, identity, main, store
from app.reports import present, render_report, impact
from fastapi.testclient import TestClient


@pytest.mark.parametrize('text',[
    '3×4＝12，12−5＝**7 支**。',
    '3×4=12 支，12−5=7 支。\n\n**答案：剩 7 支。**',
    '7 支（3×4=12，12−5=7）', '剩下七支笔。', '12-5=7', '７支',
])
def test_correct_answers_with_working_and_markdown(text):
    assert engine.check_assertion('reasoning',text)[0]=='pass'


@pytest.mark.parametrize('text',[
    '剩17支', '7×2=14，所以剩8支。', '不是7支。', '剩-7支',
    '7支不对，答案是8。', '过程得到7支，但最终答案为8。', '7支不正确。', '7.1支', '只是提到数字7，尚未算出答案',
])
def test_wrong_final_answers_are_not_passed_by_containing_seven(text):
    assert engine.check_assertion('reasoning',text)[0]=='warn'


@pytest.mark.parametrize('protocol',['anthropic','openai_chat','openai_responses','gemini'])
def test_auto_tools_keep_schema_without_forced_choice(protocol):
    payload=engine.build_payload(protocol,'claude-opus-5-5','tool','use convert_length',160)
    assert payload['tools']
    if protocol=='gemini':assert payload['toolConfig']=={'functionCallingConfig':{'mode':'AUTO'}}
    else:assert payload['tool_choice']==({'type':'auto'} if protocol=='anthropic' else 'auto')


CFG={'base_url':'https://example.com','model':'test-model','key':'FAKE_KEY','mode':'deep','protocol':'anthropic','identity_enabled':False}


@pytest.mark.asyncio
@pytest.mark.parametrize('broken',[False,True])
async def test_capped_stream_is_limited_only_when_protocol_is_complete(monkeypatch,broken):
    async def stream(*args,**kwargs):
        events=[{'type':'message_start','message':{'usage':{'input_tokens':23}}},
                {'type':'content_block_delta','delta':{'text':'铁锈结构疏松多孔，不能像致密的保护膜那样隔绝'}},
                {'type':'message_delta','delta':{'stop_reason':'max_tokens'},'usage':{'output_tokens':280}}]
        if not broken:events.append({'type':'message_stop'})
        for ev in events:yield ('line',.1,'data: '+json.dumps(ev))
    monkeypatch.setattr(engine,'pinned_stream',stream)
    item=await engine.run_one(CFG,'anthropic',engine.CASES[12])
    assert item['status']==('warn' if broken else 'limited')
    assert item['protocol_status']==('fail' if broken else 'pass')
    report=engine.make_report(CFG,[item],'budget-test','2026-10-26',state='completed')
    summary=present(report)['summary']
    if not broken:
        assert '0 项失败' in summary and '1 项预算受限' in summary
        html=render_report(report)
        assert '使用影响' in html and '完整工具往返兼容性尚未验证' in html
        assert '预算受限' in html and '不等于渠道或客户端不可用' in html


@pytest.mark.parametrize('reason,limited',[('max_output_tokens',True),('content_filter',False),('',False)])
def test_responses_incomplete_reason_is_not_always_a_budget_limit(reason,limited):
    stream=engine.StreamResult('openai_responses')
    stream.feed(json.dumps({'type':'response.incomplete','response':{'incomplete_details':{'reason':reason},'usage':{'input_tokens':1,'output_tokens':9}}}),.1)
    assert stream.error is (not limited)
    assert stream.complete is limited
    if limited:assert stream.finish=='max_output_tokens'


def test_checkbox_defaults_and_manual_override():
    for mode,base in [('quick',4),('standard',8),('deep',16)]:
        assert len(engine.cases_for(mode,identity_enabled=False))==base
        assert len(engine.cases_for(mode,identity_enabled=True))==base+3
        assert identity.analyze({**CFG,'mode':mode,'identity_enabled':False},[],'completed') is None
    assert len(engine.cases_for('fingerprint',identity_enabled=True))==99
    assert not identity.enabled({'mode':'fingerprint','identity_enabled':True})


def test_old_budget_warning_is_not_reinterpreted_as_protocol_failure():
    old={'kind':'stream2','status':'warn','finish_reason':'max_tokens',
         'stream':{'completed':True,'invalid_events':0}}
    assert '不代表流式协议故障' in impact(old)
    assert old['status']=='warn'


def test_api_checkbox_omits_all_identity_calls_and_is_saved(monkeypatch):
    observed=[]
    async def run_one(cfg,protocol,case):
        observed.append(case[0])
        return {**engine.case_spec(case),'status':'pass','latency_ms':1,'text':'fixture','usage':{}}
    monkeypatch.setattr(engine,'run_one',run_one)
    with TestClient(main.app) as client:
        csrf=client.post('/api/login',json={'password':'TEST_ONLY_PASSWORD_123456'}).json()['csrf']
        result=client.post('/api/run',json={**CFG,'mode':'standard'},headers={'X-CSRF-Token':csrf})
        assert result.status_code==200,result.text
        report=result.json()
        assert len(observed)==8 and not any(k.startswith('identity_') for k in observed)
        assert report['settings']['identity_enabled'] is False and 'identity' not in report
        assert store.get(report['id'])['settings']['identity_enabled'] is False
