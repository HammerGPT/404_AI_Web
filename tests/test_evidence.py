import json
import pytest
from app import engine
from app.adapters import StreamResult, normalize_usage, parse_result


@pytest.mark.parametrize('model,expected', [('claude-alias','anthropic'),('gemini-alias','gemini'),('custom-name','openai_chat')])
def test_protocol_follows_model_with_manual_override(model,expected):
    assert engine.resolve_protocol('auto','https://relay.example.com',model)==expected
    assert engine.resolve_protocol('openai_responses','https://relay.example.com',model)=='openai_responses'


@pytest.mark.parametrize('protocol,data', [
    ('openai_chat',{'choices':[{'message':{'tool_calls':[{'function':{'name':'convert_length','arguments':'{"centimeters":12}'}}]}}]}),
    ('openai_responses',{'output':[{'type':'function_call','name':'convert_length','arguments':'{"centimeters":12}'}]}),
    ('anthropic',{'content':[{'type':'tool_use','name':'convert_length','input':{'centimeters':12}}]}),
    ('gemini',{'candidates':[{'content':{'parts':[{'functionCall':{'name':'convert_length','args':{'centimeters':12}}}]}}]}),
])
def test_tool_name_and_arguments_are_checked(protocol,data):
    result=parse_result(data,protocol,'tool')
    calls=result['tool_calls']
    assert engine.check_assertion('tool','',calls)[0]=='pass'
    calls[0]['arguments']['centimeters']=25
    assert engine.check_assertion('tool','',calls)[0]=='warn'
    assert engine.check_assertion('tool2','',calls)[0]=='pass'
    calls[0]['name']='wrong_tool'
    assert engine.check_assertion('tool2','',calls)[0]=='warn'
    assert engine.check_assertion('tool','',True)[0]=='warn'


@pytest.mark.parametrize('protocol', ['openai_chat','openai_responses','anthropic','gemini'])
def test_missing_usage_remains_unknown(protocol):
    usage=normalize_usage({},protocol)
    assert usage['output_tokens'] is None and usage['reasoning_tokens'] is None
    assert not usage['reported'] and not usage['complete']


def test_partial_usage_and_zero_are_distinct():
    usage=normalize_usage({'prompt_tokens':0},'openai_chat')
    assert usage['input_tokens']==0 and usage['output_tokens'] is None and not usage['complete']
    assert normalize_usage({'prompt_tokens':True,'completion_tokens':-1},'openai_chat')['reported'] is False


@pytest.mark.parametrize('protocol,events', [
    ('openai_chat',[{'choices':[{'delta':{'content':'PING'}}]}, {'usage':{'prompt_tokens':2,'completion_tokens':3}}, '[DONE]']),
    ('openai_responses',[{'type':'response.output_text.delta','delta':'PING'}, {'type':'response.completed','response':{'usage':{'input_tokens':2,'output_tokens':3}}}]),
    ('anthropic',[{'type':'message_start','message':{'usage':{'input_tokens':2}}},{'type':'content_block_delta','delta':{'text':'PING'}},{'type':'message_delta','usage':{'output_tokens':3}}, {'type':'message_stop'}]),
    ('gemini',[{'candidates':[{'content':{'parts':[{'text':'PING'}]}}]}, {'candidates':[{'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':2,'candidatesTokenCount':3}}]),
])
def test_protocol_specific_stream_completion(protocol,events):
    stream=StreamResult(protocol)
    for event in events:
        stream.feed(event if isinstance(event,str) else json.dumps(event),.2)
    result=stream.result(lambda value:value)
    assert result['response_complete'] and result['usage']['complete']
    assert result['text']=='PING' and result['ttft_ms']==200
    assert result['stream']['invalid_events']==0


def test_reasoning_and_text_have_separate_first_arrival():
    stream=StreamResult('openai_chat')
    for at,delta in [(0.1,{}),(0.2,{'reasoning_content':'公开摘要'}),(0.8,{'content':'答案'})]:
        stream.feed(json.dumps({'choices':[{'delta':delta}]}),at)
    result=stream.result(lambda value:value)
    assert result['ttft_ms']==800 and result['reasoning_ttft_ms']==200
    assert not result['response_complete']
    assert result['reasoning_text']=='公开摘要'


@pytest.mark.asyncio
async def test_tps_uses_tokens_and_whole_request_time(monkeypatch):
    ticks=iter([1.0,3.0])
    monkeypatch.setattr(engine.time,'perf_counter',lambda:next(ticks))
    async def stream(*args,**kwargs):
        yield ('line',.2,'data: '+json.dumps({'choices':[{'delta':{'content':'HELLO'}}]}))
        yield ('line',1.8,'data: '+json.dumps({'usage':{'prompt_tokens':10,'completion_tokens':100}}))
        yield ('line',2,'data: [DONE]')
    monkeypatch.setattr(engine,'pinned_stream',stream)
    result=await engine.run_one({'mode':'quick','model':'test','base_url':'https://example.com','key':'FAKE_KEY'},'openai_chat',engine.CASES[3])
    assert result['latency_ms']==2000 and result['ttft_ms']==200
    assert result['tps']==50 and result['status']=='pass'


@pytest.mark.asyncio
async def test_incomplete_stream_no_tps_and_split_echo_redacted(monkeypatch):
    async def stream(*args,**kwargs):
        for part in ('FAKE_','PRIVATE_KEY'):
            yield ('line',.1,'data: '+json.dumps({'choices':[{'delta':{'content':part}}]}))
        yield ('line',.2,'data: '+json.dumps({'usage':{'prompt_tokens':1,'completion_tokens':2}}))
    monkeypatch.setattr(engine,'pinned_stream',stream)
    item=await engine.run_one({'mode':'quick','model':'test','base_url':'https://example.com','key':'FAKE_PRIVATE_KEY'},'openai_chat',engine.CASES[3])
    assert item['status']=='warn' and item['tps'] is None
    assert 'FAKE_PRIVATE_KEY' not in json.dumps(item)
    assert item['text']=='[已脱敏]'


def test_exact_grading_does_not_pass_substrings():
    assert engine.check_assertion('connect','NOT PING',False)[0]=='warn'
    assert engine.check_assertion('logic','不能确定，但可以推出',False)[0]=='warn'


def test_redaction_does_not_mutate_structural_keys():
    assert engine.redact({'text':'x','model':'x'},['x'])=={'text':'[已脱敏]','model':'[已脱敏]'}
