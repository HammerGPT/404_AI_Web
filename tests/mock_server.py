"""Isolated browser acceptance fixture. Never copied into the production image."""
import asyncio
import json
import time
from pathlib import Path
import uvicorn
from app import engine
from app import fingerprints
from app.main import app

IDENTITY_REPLIES=json.loads((Path(__file__).parent/'fixtures/identity-analyze.json').read_text())['cases'][0]['outputs']
identity_calls=0
calls = []
fingerprint_counts = {}
FINGERPRINT_PROMPTS={p['prompt']:p for p in fingerprints.PLAN}
TOKEN_COUNTS={'anchor':7,'english':48,'chinese':96,'code':65,'unicode':83}


class Response:
    status_code = 200


def answer(prompt):
    if 'READY' in prompt:return 'READY'
    if '37 × 19' in prompt:return '129'
    if 'beta' in prompt:return '29'
    if '[8,3,5,1]' in prompt:return '[1,3,5,8]'
    if 'code' in prompt:return '{"code":"OK","count":3}'
    if 'valid' in prompt:return '{"valid":true,"items":[1,2,3]}'
    if 'RED' in prompt:return 'RED\nGREEN\nBLUE'
    if '鲸鱼' in prompt:return '不能'
    if '甲比乙' in prompt:return '甲'
    if '数字' in prompt or '整数' in prompt:return '7'
    if '盒子' in prompt:return '7 支笔'
    return 'PING'


async def request(method,url,headers,payload=None,**kwargs):
    global identity_calls
    calls.append(url)
    if method=='GET':return Response(),json.dumps({'data':[{'id':'channel-alias'}]}).encode()
    is_anthropic='x-api-key' in headers
    is_gemini='contents' in payload
    is_responses='input' in payload
    prompt=payload.get('input') or (payload['contents'][0]['parts'][0]['text'] if is_gemini else payload['messages'][0]['content'])
    spec=FINGERPRINT_PROMPTS.get(prompt)
    is_identity=any(payload.get(k)==2048 for k in ('max_tokens','max_completion_tokens','max_output_tokens')) or payload.get('generationConfig',{}).get('maxOutputTokens')==2048
    await asyncio.sleep(.025 if spec else .8)
    text=answer(prompt);input_tokens=20;tool=bool(payload.get('tools'));reasoning='盒子' in prompt
    if spec:
        key=(url,prompt);fingerprint_counts[key]=fingerprint_counts.get(key,0)+1
        shifted='replaced.example.com' in url
        mixed='mixed.example.com' in url and fingerprint_counts[key]%2==0
        if spec['group']=='token':
            input_tokens=TOKEN_COUNTS[spec['probe_id']]+(17 if (shifted or mixed) and spec['probe_id']!='anchor' else 0)
            text='OK'
        elif spec['group']=='behavior':text=spec['probe']['answers'][-1 if shifted else 0]
        else:text=json.dumps(None if shifted else spec['probe']['expected'],ensure_ascii=False)
    if is_identity:
        text=IDENTITY_REPLIES[identity_calls%3]['text'];identity_calls+=1
    arguments={'centimeters':25 if '25 厘米' in prompt else 12}
    if is_anthropic:
        content=[{'type':'tool_use','name':'convert_length','input':arguments}] if tool else [{'type':'text','text':text}]
        if reasoning:content.insert(0,{'type':'thinking','thinking':'公开推理摘要：3 × 4 − 5 = 7。'})
        data={'model':payload['model'],'content':content,'stop_reason':'end_turn','usage':{'input_tokens':input_tokens,'output_tokens':12}}
    elif is_gemini:
        parts=[{'functionCall':{'name':'convert_length','args':arguments}}] if tool else [{'text':text}]
        if reasoning:parts.insert(0,{'thought':True,'text':'公开推理摘要：3 × 4 − 5 = 7。'})
        data={'modelVersion':'gemini-test','candidates':[{'content':{'parts':parts},'finishReason':'STOP'}],
              'usageMetadata':{'promptTokenCount':input_tokens,'candidatesTokenCount':12,'thoughtsTokenCount':3 if reasoning else 0}}
    elif is_responses:
        output=[{'type':'function_call','name':'convert_length','arguments':json.dumps(arguments)}] if tool else [{'type':'message','content':[{'type':'output_text','text':text}]}]
        if reasoning:output.insert(0,{'type':'reasoning','summary':[{'type':'summary_text','text':'公开推理摘要：3 × 4 − 5 = 7。'}]})
        data={'model':payload['model'],'status':'completed','output':output,'usage':{'input_tokens':input_tokens,'output_tokens':12}}
    else:
        message={'tool_calls':[{'function':{'name':'convert_length','arguments':json.dumps(arguments)}}]} if tool else {'content':text}
        if reasoning:message['reasoning_content']='公开推理摘要：3 × 4 − 5 = 7。'
        data={'model':payload['model'],'choices':[{'message':message,'finish_reason':'stop'}],'usage':{'prompt_tokens':input_tokens,'completion_tokens':12}}
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
    if 'budget.example.com' in url and 'input' in payload:
        events[-1]={'type':'response.incomplete','response':{'model':payload['model'],'incomplete_details':{'reason':'max_output_tokens'},'usage':{'input_tokens':20,'output_tokens':280}}}
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
