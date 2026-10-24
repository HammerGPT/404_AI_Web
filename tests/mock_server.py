"""Isolated browser acceptance fixture. Never copied into the production image."""
import asyncio
import json
import time
import uvicorn
from app import engine
from app.main import app

calls = []


class Response:
    status_code = 200


def answer(prompt):
    if 'code' in prompt:return '{"code":"OK","count":3}'
    if 'valid' in prompt:return '{"valid":true,"items":[1,2,3]}'
    if 'RED' in prompt:return 'RED\nGREEN\nBLUE'
    if '鲸鱼' in prompt:return '不能'
    if '甲比乙' in prompt:return '甲'
    if '数字' in prompt or '整数' in prompt:return '7'
    if '盒子' in prompt:return '7 支笔'
    return 'PING'


async def request(method,url,headers,payload=None,**kwargs):
    calls.append(url)
    await asyncio.sleep(.8)
    if method=='GET':return Response(),json.dumps({'data':[{'id':'channel-alias'}]}).encode()
    is_anthropic='x-api-key' in headers
    is_gemini='contents' in payload
    is_responses='input' in payload
    prompt=payload.get('input') or (payload['contents'][0]['parts'][0]['text'] if is_gemini else payload['messages'][0]['content'])
    text=answer(prompt);tool=bool(payload.get('tools'));reasoning='盒子' in prompt
    arguments={'centimeters':25 if '25 厘米' in prompt else 12}
    if is_anthropic:
        content=[{'type':'tool_use','name':'convert_length','input':arguments}] if tool else [{'type':'text','text':text}]
        if reasoning:content.insert(0,{'type':'thinking','thinking':'公开推理摘要：3 × 4 − 5 = 7。'})
        data={'model':payload['model'],'content':content,'stop_reason':'end_turn','usage':{'input_tokens':20,'output_tokens':12}}
    elif is_gemini:
        parts=[{'functionCall':{'name':'convert_length','args':arguments}}] if tool else [{'text':text}]
        if reasoning:parts.insert(0,{'thought':True,'text':'公开推理摘要：3 × 4 − 5 = 7。'})
        data={'modelVersion':'gemini-test','candidates':[{'content':{'parts':parts},'finishReason':'STOP'}],
              'usageMetadata':{'promptTokenCount':20,'candidatesTokenCount':12,'thoughtsTokenCount':3 if reasoning else 0}}
    elif is_responses:
        output=[{'type':'function_call','name':'convert_length','arguments':json.dumps(arguments)}] if tool else [{'type':'message','content':[{'type':'output_text','text':text}]}]
        if reasoning:output.insert(0,{'type':'reasoning','summary':[{'type':'summary_text','text':'公开推理摘要：3 × 4 − 5 = 7。'}]})
        data={'model':payload['model'],'status':'completed','output':output,'usage':{'input_tokens':20,'output_tokens':12}}
    else:
        message={'tool_calls':[{'function':{'name':'convert_length','arguments':json.dumps(arguments)}}]} if tool else {'content':text}
        if reasoning:message['reasoning_content']='公开推理摘要：3 × 4 − 5 = 7。'
        data={'model':payload['model'],'choices':[{'message':message,'finish_reason':'stop'}],'usage':{'prompt_tokens':20,'completion_tokens':12}}
    return Response(),json.dumps(data,ensure_ascii=False).encode()


async def stream(url,headers,payload,**kwargs):
    calls.append(url);start=time.perf_counter()
    if 'x-api-key' in headers:
        events=[{'type':'message_start','message':{'model':payload['model'],'usage':{'input_tokens':20}}},
                {'type':'content_block_delta','delta':{'text':'这是模拟流式正文，用于验证实时进度和指标。'}},
                {'type':'message_delta','usage':{'output_tokens':24}}, {'type':'message_stop'}]
    elif 'contents' in payload:
        events=[{'candidates':[{'content':{'parts':[{'text':'这是模拟流式正文。'}]}}]},
                {'candidates':[{'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':20,'candidatesTokenCount':24}}]
    elif 'input' in payload:
        events=[{'type':'response.output_text.delta','delta':'这是模拟流式正文，用于验证实时进度和指标。'},
                {'type':'response.completed','response':{'model':payload['model'],'usage':{'input_tokens':20,'output_tokens':24}}}]
    else:
        events=[{'model':payload['model'],'choices':[{'delta':{'content':'这是模拟流式正文。'}}]},
                {'usage':{'prompt_tokens':20,'completion_tokens':24}}, '[DONE]']
    for ev in events:
        await asyncio.sleep(.35)
        yield ('line',time.perf_counter()-start,'data: '+(ev if isinstance(ev,str) else json.dumps(ev,ensure_ascii=False)))


engine.pinned_request=request
engine.pinned_stream=stream


@app.get('/__test__/calls')
async def call_count():
    return {'count':len(calls)}


if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=8000,access_log=False)
