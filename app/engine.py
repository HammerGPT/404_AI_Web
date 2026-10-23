"""Low-cost black-box checks. Claims are deliberately evidence-limited."""
from __future__ import annotations
import asyncio
import json
import math
import statistics
import time
import uuid
from datetime import datetime, timezone
from typing import Any
import httpx
from .security import api_path, pinned_request, pinned_stream, TargetError


TOOL = {"name": "convert_length", "description": "Convert centimeters to millimeters.",
        "parameters": {"type": "object", "properties": {"centimeters": {"type": "number"}}, "required": ["centimeters"]}}

CASES = [
    ('connect', '连接与协议', '请仅输出 PING，不要附加其他文字。', 80),
    ('fingerprint', '行为采样', '在 0 到 9 之间任选一个数字。只输出这个数字。', 40),
    ('json', '结构化输出', '只输出一个合法 JSON 对象，且 code 必须为 OK，count 必须为 3。', 120),
    ('stream', '流式性能', '请用一句短话解释为何蓝天看起来是蓝色。', 140),
    ('tool', '工具调用', '请调用 convert_length 工具，将 12 厘米转换为毫米，不要自行计算。', 160),
    ('reasoning', '思考协议', '先分析后回答：小明有 3 个盒子，每盒 4 支笔，送出 5 支后剩几支？仅给简短答案。', 180),
    ('instruction', '指令遵循', '只能输出三行文本：第一行 RED，第二行 GREEN，第三行 BLUE。不要其他内容。', 90),
    ('logic', '基础推理', '已知所有鲸鱼都是哺乳动物，有些哺乳动物生活在陆地上。能否推出有些鲸鱼生活在陆地上？只回答 能 或 不能。', 100),
    ('fingerprint2', '行为复测 A', '任选一个 0-9 的整数，只返回数字。', 50),
    ('fingerprint3', '行为复测 B', '随机想一个一位数整数，只输出数字。', 50),
    ('fingerprint4', '行为复测 C', '请选择 0 至 9 中的一个数字回答。', 50),
    ('fingerprint5', '行为复测 D', '只回答一个你最先想到的数字（0-9）。', 50),
    ('stream2', '流式复测', '请简短说明为什么铁会生锈。', 140),
    ('json2', 'JSON 复测', '严格输出 JSON：{"valid":true,"items":[1,2,3]}，不可输出 Markdown。', 110),
    ('tool2', '工具调用复测', '调用 convert_length 工具，换算 25 厘米到毫米。', 160),
    ('logic2', '基础推理复测', '甲比乙高，乙比丙高。甲与丙谁更高？仅输出 甲 或 丙。', 90),
]
DISTILL = ('structured_sample', '授权数据生成样例', '生成一条关于基础数学的原创训练问答，严格输出 JSON，字段为 instruction、input、output、explanation，内容简短。', 200)


def cases_for(mode: str, distill: bool = False):
    n = {'quick': 4, 'standard': 8, 'deep': 16}[mode]
    cases = CASES[:n]
    return cases + ([DISTILL] if distill and mode != 'quick' else [])


def resolve_protocol(protocol: str, base: str, model: str) -> str:
    if protocol != 'auto':
        return protocol
    # The bulk of token intermediaries expose OpenAI-compatible endpoints.
    if 'generativelanguage.googleapis.com' in base:
        return 'gemini'
    if 'api.anthropic.com' in base:
        return 'anthropic'
    return 'openai_chat'


def normalize_usage(raw: dict, protocol: str) -> dict:
    raw = raw or {}
    if protocol == 'anthropic':
        base = raw.get('input_tokens', 0) or 0
        cr = raw.get('cache_read_input_tokens', 0) or 0
        cw = raw.get('cache_creation_input_tokens', 0) or 0
        return {'input_tokens': base + cr + cw, 'output_tokens': raw.get('output_tokens', 0) or 0,
                'cache_read_tokens': cr, 'cache_write_tokens': cw,
                'reasoning_tokens': (raw.get('output_tokens_details') or {}).get('thinking_tokens', 0) or 0,
                'reported': bool(raw)}
    if protocol == 'gemini':
        return {'input_tokens': raw.get('promptTokenCount', 0) or 0,
                'output_tokens': (raw.get('candidatesTokenCount', 0) or 0) + (raw.get('thoughtsTokenCount', 0) or 0),
                'cache_read_tokens': raw.get('cachedContentTokenCount', 0) or 0,
                'cache_write_tokens': 0, 'reasoning_tokens': raw.get('thoughtsTokenCount', 0) or 0,
                'reported': bool(raw)}
    if protocol == 'openai_responses':
        details = raw.get('input_tokens_details') or {}
        out = raw.get('output_tokens_details') or {}
        return {'input_tokens': raw.get('input_tokens', 0) or 0, 'output_tokens': raw.get('output_tokens', 0) or 0,
                'cache_read_tokens': details.get('cached_tokens', 0) or 0,
                'cache_write_tokens': details.get('cache_write_tokens', 0) or 0,
                'reasoning_tokens': out.get('reasoning_tokens', 0) or 0, 'reported': bool(raw)}
    prompt = raw.get('prompt_tokens_details') or {}
    completion = raw.get('completion_tokens_details') or {}
    return {'input_tokens': raw.get('prompt_tokens', 0) or 0, 'output_tokens': raw.get('completion_tokens', 0) or 0,
            'cache_read_tokens': prompt.get('cached_tokens', 0) or 0,
            'cache_write_tokens': prompt.get('cache_write_tokens', 0) or 0,
            'reasoning_tokens': completion.get('reasoning_tokens', 0) or 0,
            'reported': bool(raw)}


def estimate_cost(totals: dict, rates: dict | None) -> float | None:
    if not rates or rates.get('input') is None or rates.get('output') is None:
        return None
    inp, out = totals.get('input_tokens', 0), totals.get('output_tokens', 0)
    cr, cw = totals.get('cache_read_tokens', 0), totals.get('cache_write_tokens', 0)
    uncached = max(0, inp - cr - cw)
    price = (uncached * float(rates['input']) + cr * float(rates.get('cache_read', rates['input'])) +
             cw * float(rates.get('cache_write', rates['input'])) + out * float(rates['output'])) / 1_000_000
    return round(price * float(rates.get('group_ratio', 1)), 8)


def build_payload(protocol: str, model: str, kind: str, prompt: str, token_max: int, reasoning: str = 'auto', token_field='auto') -> dict:
    if kind == 'reasoning' and protocol == 'anthropic':
        token_max = max(token_max, 1600)
    elif kind == 'reasoning' and protocol in ('gemini','openai_chat','openai_responses'):
        token_max = max(token_max, 800)
    if protocol == 'openai_chat':
        field = ('max_completion_tokens' if model.lower().startswith(('o1', 'o3', 'o4', 'gpt-5', 'gpt-6')) else 'max_tokens') if token_field == 'auto' else token_field
        payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], field: token_max}
        if kind in ('stream', 'stream2'):
            payload['stream'] = True
            payload['stream_options'] = {'include_usage': True}
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'type': 'function', 'function': TOOL}]
            payload['tool_choice'] = {'type': 'function', 'function': {'name': 'convert_length'}}
        if kind == 'reasoning' and reasoning != 'off' and model.lower().startswith(('gpt-', 'o1', 'o3', 'o4')):
            payload['reasoning_effort'] = 'low'
        return payload
    if protocol == 'openai_responses':
        payload = {'model': model, 'input': prompt, 'max_output_tokens': token_max, 'store': False}
        if kind in ('stream', 'stream2'):
            payload['stream'] = True
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'type': 'function', **TOOL}]
            payload['tool_choice'] = {'type': 'function', 'name': 'convert_length'}
        if kind == 'reasoning' and reasoning != 'off':
            payload['reasoning'] = {'effort': 'low', 'summary': 'auto'}
        return payload
    if protocol == 'anthropic':
        payload = {'model': model, 'max_tokens': token_max, 'messages': [{'role': 'user', 'content': prompt}]}
        if kind in ('stream', 'stream2'):
            payload['stream'] = True
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'name': TOOL['name'], 'description': TOOL['description'], 'input_schema': TOOL['parameters']}]
            payload['tool_choice'] = {'type': 'tool', 'name': 'convert_length'}
        if kind == 'reasoning' and reasoning != 'off':
            payload['thinking'] = {'type': 'enabled', 'budget_tokens': 1024}
        return payload
    if protocol == 'gemini':
        payload = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                   'generationConfig': {'maxOutputTokens': token_max}}
        if kind in ('tool', 'tool2'):
            payload['tools'] = [{'functionDeclarations': [{'name': TOOL['name'], 'description': TOOL['description'], 'parameters': TOOL['parameters']}]}]
            payload['toolConfig'] = {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': ['convert_length']}}
        if kind == 'reasoning' and reasoning != 'off':
            payload['generationConfig']['thinkingConfig'] = {'includeThoughts': True}
        return payload
    raise ValueError('Unsupported API')


def parse_result(data: dict, protocol: str, kind: str) -> dict:
    model = data.get('model') or data.get('modelVersion')
    thinking = False
    has_tool = False
    content = ''
    raw_usage = data.get('usage') or data.get('usageMetadata') or {}
    if protocol == 'openai_chat':
        msg = ((data.get('choices') or [{}])[0].get('message') or {})
        text = msg.get('content') or ''
        content = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        has_tool = bool(msg.get('tool_calls'))
        thinking = bool(msg.get('reasoning_content') or msg.get('reasoning') or ((raw_usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)))
    elif protocol == 'openai_responses':
        content = ''
        for item in data.get('output') or []:
            if item.get('type') == 'function_call':
                has_tool = True
            if item.get('type') == 'reasoning':
                thinking = True
            for part in item.get('content') or []:
                if part.get('type') == 'output_text':
                    content += part.get('text', '')
        content = content or data.get('output_text') or ''
        thinking = thinking or bool((raw_usage.get('output_tokens_details') or {}).get('reasoning_tokens', 0))
    elif protocol == 'anthropic':
        for block in data.get('content') or []:
            if block.get('type') == 'text': content += block.get('text', '')
            elif block.get('type') == 'tool_use': has_tool = True
            elif block.get('type') in ('thinking', 'redacted_thinking'): thinking = True
        thinking = thinking or bool((raw_usage.get('output_tokens_details') or {}).get('thinking_tokens', 0))
    elif protocol == 'gemini':
        for candidate in data.get('candidates') or []:
            for part in (candidate.get('content') or {}).get('parts') or []:
                if 'functionCall' in part: has_tool = True
                if part.get('thought'): thinking = True
                elif 'text' in part: content += part['text']
        thinking = thinking or bool(raw_usage.get('thoughtsTokenCount'))
    return {'text': content[:2000], 'model_returned': model, 'thinking_observed': thinking,
            'tool_observed': has_tool, 'usage': normalize_usage(raw_usage, protocol),
            'usage_raw': raw_usage}


def check_assertion(kind: str, text: str, tool: bool) -> tuple[str, str]:
    value = text.strip()
    if kind == 'connect': return ('pass' if 'PING' in value.upper() else 'warn', '基础响应检查')
    if kind.startswith('fingerprint'): return ('sample', '行为样本：不单独证明模型身份')
    if kind.startswith('stream'): return ('pass' if value else 'warn', '流式内容输出')
    if kind.startswith('tool'): return ('pass' if tool else 'warn', '是否产生正确类型的工具调用')
    if kind == 'instruction': return ('pass' if value.splitlines() == ['RED', 'GREEN', 'BLUE'] else 'warn', '行数与格式约束')
    if kind == 'logic': return ('pass' if ('不能' in value or '无法' in value) else 'warn', '逻辑判断')
    if kind == 'logic2': return ('pass' if value == '甲' else 'warn', '排序推理')
    if kind in ('json', 'json2', 'structured_sample'):
        try:
            obj = json.loads(value)
            if kind == 'json': ok = obj.get('code') == 'OK' and obj.get('count') == 3
            elif kind == 'json2': ok = obj == {'valid': True, 'items': [1, 2, 3]}
            else: ok = all(isinstance(obj.get(k), str) and obj[k].strip() for k in ('instruction','input','output','explanation'))
            return ('pass' if ok else 'warn', '严格 JSON 及指定字段检查')
        except (ValueError, TypeError, AttributeError):
            return 'warn', '未返回有效 JSON'
    if kind == 'reasoning': return 'sample', '推理能力与内容字段单独核对'
    return 'sample', '样本记录'


def request_headers(protocol: str, key: str, custom: dict | None = None):
    if protocol == 'anthropic':
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    elif protocol == 'gemini':
        headers = {'x-goog-api-key': key}
    else:
        headers = {'Authorization': f'Bearer {key}'}
    headers['Content-Type'] = 'application/json'
    headers['Accept'] = 'application/json'
    # No auth/header override via custom headers.
    for k,v in (custom or {}).items():
        if k.lower() not in {'host','authorization','x-api-key','x-goog-api-key','cookie','proxy-authorization','content-length','transfer-encoding','connection','accept-encoding'}:
            headers[k] = v
    return headers


async def run_one(cfg: dict, protocol: str, case: tuple) -> dict:
    kind, title, prompt, max_output = case
    if cfg['mode'] == 'deep': max_output = min(320, max_output * 2)
    payload = build_payload(protocol, cfg['model'], kind, prompt, max_output, cfg.get('reasoning','auto'), cfg.get('token_field','auto'))
    endpoint = api_path(cfg['base_url'], protocol, cfg['model'])
    gemini_stream = protocol == 'gemini' and kind in ('stream', 'stream2')
    if gemini_stream:
        endpoint = endpoint.replace(':generateContent', ':streamGenerateContent')
    kind_for_transport = kind
    headers = request_headers(protocol, cfg['key'], cfg.get('custom_headers'))
    start = time.perf_counter()
    item = {'kind':kind,'title':title,'status':'error','note':'','latency_ms':0,'ttft_ms':None,
            'model_returned':None,'thinking_observed':False,'tool_observed':False,'usage':{},'text':''}
    try:
        if kind_for_transport in ('stream','stream2'):
            parts = []
            raw_usage = {}
            returned_model = None
            thinking = False
            tool = False
            first_content = None
            async for typ, at, line in pinned_stream(endpoint, headers, payload, timeout=45, params={'alt':'sse'} if gemini_stream else None):
                if typ == 'error':
                    item['note'] = f'HTTP {at}: {line[:260]}'
                    break
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if not data or data == '[DONE]': continue
                try: ev = json.loads(data)
                except ValueError: continue
                if protocol == 'openai_chat':
                    returned_model = ev.get('model') or returned_model
                    if ev.get('usage'): raw_usage = ev['usage']
                    ch = ((ev.get('choices') or [{}])[0].get('delta') or {})
                    piece = ch.get('content') or ''
                    thinking |= bool(ch.get('reasoning_content') or ch.get('reasoning'))
                    tool |= bool(ch.get('tool_calls'))
                elif protocol == 'openai_responses':
                    returned_model = (ev.get('response') or {}).get('model') or returned_model
                    if ev.get('type') == 'response.completed':
                        raw_usage = (ev.get('response') or {}).get('usage') or raw_usage
                    piece = ev.get('delta', '') if ev.get('type') == 'response.output_text.delta' else ''
                    thinking |= ev.get('type','').startswith('response.reasoning')
                    tool |= ev.get('type') == 'response.output_item.added' and (ev.get('item') or {}).get('type') == 'function_call'
                elif protocol == 'anthropic':
                    if ev.get('type') == 'message_start':
                        returned_model = (ev.get('message') or {}).get('model') or returned_model
                        raw_usage = (ev.get('message') or {}).get('usage') or raw_usage
                    if ev.get('type') == 'message_delta':
                        raw_usage.update(ev.get('usage') or {})
                    piece = ((ev.get('delta') or {}).get('text') or '') if ev.get('type') == 'content_block_delta' else ''
                    thinking |= (ev.get('type') == 'content_block_start' and (ev.get('content_block') or {}).get('type') == 'thinking')
                    tool |= (ev.get('type') == 'content_block_start' and (ev.get('content_block') or {}).get('type') == 'tool_use')
                elif protocol == 'gemini':
                    returned_model = ev.get('modelVersion') or returned_model
                    if ev.get('usageMetadata'): raw_usage = ev['usageMetadata']
                    piece = ''
                    for candidate in ev.get('candidates') or []:
                        for part in (candidate.get('content') or {}).get('parts') or []:
                            if part.get('thought'): thinking = True
                            elif 'text' in part: piece += part['text']
                            if 'functionCall' in part: tool = True
                else:
                    piece = ''
                if piece:
                    if first_content is None: first_content = at
                    if sum(map(len,parts)) < 2000: parts.append(piece[:2000])
            if item['note']:
                item['status'] = 'error'
            else:
                item.update({'text':''.join(parts)[:2000], 'model_returned':returned_model,
                             'ttft_ms': round(first_content*1000) if first_content is not None else None,
                             'thinking_observed':thinking, 'tool_observed':tool,
                             'usage':normalize_usage(raw_usage,protocol)})
                item['status'],item['note'] = check_assertion(kind,item['text'],tool)
        else:
            response, raw = await pinned_request('POST', endpoint, headers, payload, timeout=55 if kind=='reasoning' else 40)
            if response.status_code >= 300:
                # Sanitize error: model/provider may echo user secrets; never persist arbitrary error payload.
                item['note'] = f'HTTP {response.status_code}（可在服务器日志之外检查接口配置）'
            else:
                try:
                    parsed = parse_result(json.loads(raw),protocol,kind)
                except (ValueError,TypeError,IndexError,KeyError) as e:
                    item['note'] = f'响应格式不符合预期：{type(e).__name__}'
                else:
                    item.update(parsed)
                    item['status'],item['note'] = check_assertion(kind,item['text'],item['tool_observed'])
    except (httpx.HTTPError,TargetError,asyncio.TimeoutError) as e:
        item['note'] = f'请求失败：{type(e).__name__}'
    item['latency_ms'] = round((time.perf_counter()-start)*1000)
    # Raw provider data can contain extra information; store only normalized metrics.
    item.pop('usage_raw',None)
    return item


def build_sections(samples: list, protocol: str, model: str, distill_requested: bool) -> list:
    names = {x['kind']:x for x in samples}
    passed = [x for x in samples if x['status'] in ('pass','sample')]
    errors = [x for x in samples if x['status'] == 'error']
    returned = [x['model_returned'] for x in samples if x.get('model_returned')]
    model_different = returned and any(x.lower() != model.lower() for x in returned)
    usage_reported = [x for x in samples if x.get('usage',{}).get('reported')]
    stream = [x for x in samples if x['kind'].startswith('stream')]
    reasoning = names.get('reasoning')
    tool = [x for x in samples if x['kind'].startswith('tool')]
    structured = [x for x in samples if x['kind'] in ('json','json2','instruction','logic','logic2')]
    sections = [
      {'name':'模型身份与行为采样', 'status':'attention' if model_different else 'unknown',
       'detail': ('返回模型 ID 与请求名称不一致，需核实别名及路由。' if model_different else '已记录行为样本；没有可信官方基线，不能据此认证模型真伪。')},
      {'name':'Token 用量与计费', 'status':'pass' if usage_reported else 'unknown',
       'detail': ('已记录服务商报告的 Token 用量；实际余额扣费需独立账单核对。' if usage_reported else '接口未提供可用 Usage；无法完成 Token 核算。')},
      {'name':'协议与工具调用', 'status':('pass' if tool and all(x['status']=='pass' for x in tool) else 'attention' if tool else 'unknown'),
       'detail': ('工具调用测试已通过。' if tool and all(x['status']=='pass' for x in tool) else '工具调用未通过或未获支持；可能是模型或中转协议限制。' if tool else '当前模式未执行工具调用测试。')},
      {'name':'思考模式与推理内容', 'status':('pass' if reasoning and reasoning.get('thinking_observed') else 'unknown'),
       'detail': ('观察到协议支持的推理字段或用量证据；不代表取得完整内部思维链。' if reasoning and reasoning.get('thinking_observed') else '未观察到可见推理字段；可能是接口不开放、配置不支持或上游改写。' if reasoning else '当前模式未执行思考专项测试。')},
      {'name':'能力与输出完整性', 'status':('pass' if structured and all(x['status']=='pass' for x in structured) else 'attention' if structured else 'unknown'),
       'detail': (f"本次 {sum(x['status']=='pass' for x in structured)}/{len(structured)} 个约束测试通过；样本量有限。" if structured else '当前模式仅进行了基本内容采样。')},
      {'name':'基础性能与稳定性', 'status':'attention' if errors else 'pass' if samples else 'unknown',
       'detail': f"本次 {len(passed)}/{len(samples)} 次请求获得可用响应；小样本不代表长期 SLA。"},
      {'name':'蒸馏用途适用性', 'status': ('pass' if names.get('structured_sample',{}).get('status') == 'pass' else 'attention' if distill_requested else 'unknown'),
       'detail': ('仅验证一条训练格式样例；必须另行核验原厂及渠道的训练用途授权。' if distill_requested else '未执行训练数据样本检测；普通能力测试不等于蒸馏授权。')}
    ]
    return sections

async def run_suite(cfg: dict, update=None) -> dict:
    protocol = resolve_protocol(cfg['protocol'],cfg['base_url'],cfg['model'])
    samples=[]
    suite = cases_for(cfg['mode'],cfg.get('distill',False))
    for i,case in enumerate(suite):
        result = await run_one(cfg,protocol,case)
        samples.append(result)
        if update: await update(i+1,len(suite),result)
        # Stop if authentication failed; other failures are useful independent evidence.
        if i == 0 and result['status']=='error' and ('HTTP 401' in result['note'] or 'HTTP 403' in result['note']):
            break
    successful = [x for x in samples if x['status'] in ('pass','sample')]
    errors = [x for x in samples if x['status'] == 'error']
    ttft = [x['ttft_ms'] for x in samples if x['ttft_ms'] is not None]
    total = {k:sum((x.get('usage') or {}).get(k,0) or 0 for x in samples) for k in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','reasoning_tokens')}
    total['reported_requests'] = sum(bool((x.get('usage') or {}).get('reported')) for x in samples)
    sections = build_sections(samples,protocol,cfg['model'],cfg.get('distill',False))
    estimate = estimate_cost(total,cfg.get('rates')) if total['reported_requests']==len(samples) else None
    status='attention' if errors or any(x['status']=='attention' for x in sections) else 'pass'
    return {'id':uuid.uuid4().hex[:14],'created_at':datetime.now(timezone.utc).isoformat(),
        'label':cfg.get('label','')[:100], 'base_host':httpx.URL(cfg['base_url']).host,'model':cfg['model'],
        'mode':cfg['mode'],'protocol':protocol,'status':status,'samples':samples,'sections':sections,
        'stats':{'requests':len(samples),'successful':len(successful),'errors':len(errors),
                 'avg_ms':round(statistics.mean(x['latency_ms'] for x in samples)) if samples else None,
                 'ttft_ms':round(statistics.mean(ttft)) if ttft else None,
                 'usage':total,'estimated_cost_usd':estimate,'cost_basis':('用户填写的单价及倍率，按供应商 Usage 计算' if estimate is not None else '无完整用量或未设置单价，无法估算')},
        'disclaimers':['行为指纹样本不足以证明模型身份或上游来源','供应商 Usage 不是独立计费证明；需核对账单或余额','小样本性能不代表持续 SLA','训练数据用途需取得上游和中转商授权']}
