"""Sequential, evidence-based channel checks; no credentials in report state."""
from __future__ import annotations
import asyncio
import json
import math
import statistics
import time
import uuid
from datetime import datetime, timezone
import httpx
from .adapters import TOOL, TEXT_LIMIT, StreamResult, build_payload, normalize_usage, parse_result
from .catalog import infer_protocol
from . import fingerprints, identity
from .security import api_path, pinned_request, pinned_stream, TargetError

CASES = [
    ('connect', '连接与协议', '请仅输出 PING，不要附加其他文字。', 80),
    ('digit_format', '单字符约束', '在 0 到 9 之间任选一个数字。只输出这个数字。', 40),
    ('json', '结构化输出', '只输出一个合法 JSON 对象，且 code 必须为 OK，count 必须为 3。', 120),
    ('stream', '流式性能', '请用一句短话解释为何蓝天看起来是蓝色。', 140),
    ('tool', '工具调用', '请调用 convert_length 工具，将 12 厘米转换为毫米，不要自行计算。', 160),
    ('reasoning', '算术推理与思考响应', '先分析后回答：小明有 3 个盒子，每盒 4 支笔，送出 5 支后剩几支？仅给简短答案。', 180),
    ('instruction', '指令遵循', '只能输出三行文本：第一行 RED，第二行 GREEN，第三行 BLUE。不要其他内容。', 90),
    ('logic', '基础推理', '已知所有鲸鱼都是哺乳动物，有些哺乳动物生活在陆地上。能否推出有些鲸鱼生活在陆地上？只回答 能 或 不能。', 100),
    ('exact_word', '精确文本约束', '只输出单词 READY，不要其他内容。', 50),
    ('arithmetic', '多步计算', '计算 (37 × 19 - 58) ÷ 5。只输出结果数字。', 80),
    ('array_order', '数组排序', '将 [8,3,5,1] 按从小到大排序，只输出 JSON 数组。', 80),
    ('extract_value', '字段提取', '从 {"alpha":17,"beta":29} 中提取 beta 的值，只输出数字。', 50),
    ('stream2', '流式复测', '请简短说明为什么铁会生锈。', 140),
    ('json2', 'JSON 复测', '严格输出 JSON：{"valid":true,"items":[1,2,3]}，不可输出 Markdown。', 110),
    ('tool2', '工具调用复测', '调用 convert_length 工具，换算 25 厘米到毫米。', 160),
    ('logic2', '基础推理复测', '甲比乙高，乙比丙高。甲与丙谁更高？仅输出 甲 或 丙。', 90),
]
DISTILL = ('structured_sample', '授权数据生成样例', '生成一条关于基础数学的原创训练问答，严格输出 JSON，字段为 instruction、input、output、explanation，内容简短。', 200)


def cases_for(mode: str, distill: bool = False, challenges=None):
    if mode=='fingerprint':return fingerprints.cases()
    n = {'quick': 4, 'standard': 8, 'deep': 16}[mode]
    cases = CASES[:n] + (identity.cases(challenges) if mode in ('standard','deep') else [])
    return cases + ([DISTILL] if distill and mode != 'quick' else [])


EXPECTED_TOOLS = {'tool': {'centimeters': 12}, 'tool2': {'centimeters': 25}}
EXPECTED_JSON = {'json': {'code': 'OK', 'count': 3}, 'json2': {'valid': True, 'items': [1, 2, 3]}}
RULES = {
    'connect': '完整响应，正文严格等于 PING。',
    'digit_format': '仅输出 0～9 中的一个数字，验证格式约束，不认证模型身份。',
    'exact_word': '正文严格等于 READY。',
    'arithmetic': '结果应为 129。',
    'array_order': '正文应为 JSON 数组 [1,3,5,8]。',
    'extract_value': '正文应为 29。',
    'json': '正文可直接解析为 JSON，字段及值符合要求。',
    'stream': '收到正文、协议结束标记及可解析事件；另外记录 Usage 是否完整。',
    'tool': '仅调用指定函数，函数名及参数与题目要求一致；不实际执行工具。',
    'reasoning': '计算答案应为 7 支笔；公开推理字段另行记录，不能据此认证模型身份。',
    'instruction': '仅输出 RED、GREEN、BLUE 三行。',
    'logic': '严格回答“不能”。',
    'logic2': '严格回答“甲”。',
    'structured_sample': 'JSON 包含 instruction、input、output、explanation 四个非空字符串字段。',
}
USAGE_FIELDS = ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','reasoning_tokens')
CASE_TIMEOUT_SECONDS = 90


def case_spec(case):
    kind, title, prompt, _ = case
    if kind.startswith('identity_'):return identity.specification(case)
    if kind in fingerprints.BY_KIND:return fingerprints.specification(kind)
    family = kind.rstrip('0123456789')
    return {'kind': kind, 'title': title, 'prompt': prompt,
            'expected': RULES.get(kind, RULES.get(family, '记录实际响应。')),
            'expected_tool': {'name': TOOL['name'], 'arguments': EXPECTED_TOOLS[kind]} if kind in EXPECTED_TOOLS else None}


def resolve_protocol(protocol: str, base: str, model: str) -> str:
    return infer_protocol(model, base) if protocol == 'auto' else protocol


def redact(value, secrets, keys=False):
    """Redact before clipping, including upstream echoes and custom header values."""
    if isinstance(value, str):
        for secret in sorted(set(s for s in secrets if s), key=len, reverse=True):
            value = value.replace(secret, '[已脱敏]')
        return value
    if isinstance(value, dict):
        return {(redact(k,secrets) if keys else k):redact(v,secrets,keys) for k,v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets, keys) for v in value]
    return value


def estimate_cost(totals: dict, rates: dict | None) -> float | None:
    if not rates or rates.get('input') is None or rates.get('output') is None:
        return None
    inp, out = totals.get('input_tokens'), totals.get('output_tokens')
    if inp is None or out is None:
        return None
    cr, cw = totals.get('cache_read_tokens') or 0, totals.get('cache_write_tokens') or 0
    price = (max(0, inp-cr-cw)*rates['input'] + cr*rates.get('cache_read',rates['input']) +
             cw*rates.get('cache_write',rates['input']) + out*rates['output']) / 1_000_000
    return round(price*rates.get('group_ratio',1),8)


def check_assertion(kind: str, text: str, tool=False) -> tuple[str,str]:
    value = text.strip()
    if kind == 'connect':
        return ('pass' if value == 'PING' else 'warn', '正文与 PING 严格匹配' if value == 'PING' else '正文不等于要求的 PING')
    if kind=='digit_format':
        ok=len(value)==1 and value in '0123456789'
        return ('pass' if ok else 'warn','符合单字符格式要求；不代表模型身份验证。' if ok else '未按要求只输出一位数字。')
    if kind=='reasoning':
        normalized=value.strip('。.!！').replace(' ','')
        ok=normalized in ('7','7支','7支笔','剩7支笔','剩下7支笔','还剩7支笔','七','七支笔')
        return ('pass' if ok else 'warn','答案为 7，计算检查通过；公开推理证据见明细。' if ok else '预期为 7 支笔，实际答案或格式未符合要求。')
    if kind in ('exact_word','arithmetic','array_order','extract_value'):
        expected={'exact_word':'READY','arithmetic':'129','array_order':[1,3,5,8],'extract_value':'29'}[kind]
        try:ok=json.loads(value)==expected if kind=='array_order' else value==expected
        except ValueError:ok=False
        return ('pass' if ok else 'warn','响应符合预期。' if ok else '响应与确定答案不一致。')
    if kind.startswith('stream'):
        return ('pass' if value else 'warn', '正文输出检查')
    if kind in EXPECTED_TOOLS:
        ok = (isinstance(tool, list) and len(tool) == 1 and tool[0].get('name') == TOOL['name']
              and tool[0].get('arguments') == EXPECTED_TOOLS[kind])
        return ('pass' if ok else 'warn', '函数名与参数均符合预期' if ok else '未产生要求的函数调用，或函数名/参数不符合预期')
    if kind == 'instruction':
        ok = value.splitlines() == ['RED','GREEN','BLUE']
    elif kind in ('logic','logic2'):
        ok = value == ('不能' if kind == 'logic' else '甲')
    elif kind in (*EXPECTED_JSON, 'structured_sample'):
        try:
            obj = json.loads(value)
            if kind == 'structured_sample':
                ok = isinstance(obj,dict) and all(isinstance(obj.get(k),str) and obj[k].strip() for k in ('instruction','input','output','explanation'))
            else:
                ok = obj == EXPECTED_JSON[kind]
        except (ValueError,TypeError):
            return 'warn', '正文不是可直接解析的 JSON'
    else:
        return 'warn', '该检查尚无可执行判定规则，未标记为通过。'
    return ('pass' if ok else 'warn', '响应符合预期' if ok else '响应与指定内容或字段要求不一致')


def request_headers(protocol: str, key: str, custom: dict | None = None):
    if protocol == 'anthropic':
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
    elif protocol == 'gemini':
        headers = {'x-goog-api-key': key}
    else:
        headers = {'Authorization': f'Bearer {key}'}
    headers.update({'Content-Type': 'application/json', 'Accept': 'application/json'})
    forbidden = {'host','authorization','x-api-key','x-goog-api-key','cookie','proxy-authorization','content-length','transfer-encoding','connection','accept-encoding'}
    headers.update({k:v for k,v in (custom or {}).items() if k.lower() not in forbidden})
    return headers


async def run_one(cfg: dict, protocol: str, case: tuple) -> dict:
    kind, title, prompt, max_output = case
    if cfg['mode'] == 'deep' and not kind.startswith('identity_'):
        max_output = min(320,max_output*2)
    secrets = [cfg['key'], *cfg.get('custom_headers',{}).values()]
    clean = lambda value: redact(value, secrets)
    payload = build_payload(protocol,cfg['model'],kind,prompt,max_output,cfg.get('reasoning','auto'),cfg.get('token_field','auto'))
    endpoint = api_path(cfg['base_url'],protocol,cfg['model'])
    streaming = kind.startswith('stream')
    if streaming and protocol == 'gemini':
        endpoint = endpoint.replace(':generateContent',':streamGenerateContent')
    headers = request_headers(protocol,cfg['key'],cfg.get('custom_headers'))
    item = {**case_spec(case), 'status':'error','note':'','latency_ms':None,'ttft_ms':None,
            'reasoning_ttft_ms':None,'tps':None,'model_returned':None,'thinking_observed':False,
            'tool_observed':False,'tool_calls':[], 'usage':normalize_usage({},protocol),'text':'',
            'reasoning_text':'','response_complete':False,'http_status':None,
            'request': {'method':'POST','path':httpx.URL(endpoint).path,'model':cfg['model'],
                        'protocol':protocol,'parameters':{k:v for k,v in payload.items() if k not in ('messages','input','contents','model')}}}
    start = time.perf_counter()
    stream = StreamResult(protocol) if streaming else None
    try:
        if streaming:
            async for typ, at, line in pinned_stream(endpoint,headers,payload,timeout=45,params={'alt':'sse'} if protocol=='gemini' else None):
                if typ == 'error':
                    item['http_status'] = at or None
                    item['note'] = f'HTTP {at}（流式请求失败，请检查接口配置）' if at else '流式响应超过大小上限'
                    break
                if line.startswith('data:') and line[5:].strip():
                    stream.feed(line[5:].strip(),at)
            item.update(stream.result(clean))
            if not item['note']:
                item['http_status'] = 200
                if stream.error:
                    item['note'] = '上游返回流式错误事件，正文已隐藏'
                else:
                    checks = [('正文',bool(stream.text)),('结束标记',stream.complete),('事件结构',stream.invalid_events == 0 and stream.events > 0),
                              ('输入/输出用量',item['usage']['complete'])]
                    item['status'] = 'pass' if all(ok for _,ok in checks) else 'warn'
                    item['note'] = '；'.join(name + ('已验证' if ok else '未验证') for name,ok in checks)
        else:
            response, raw = await pinned_request('POST',endpoint,headers,payload,timeout=55 if kind=='reasoning' else 40)
            item['http_status'] = response.status_code
            if response.status_code >= 300:
                item['note'] = f'HTTP {response.status_code}（请求失败，请检查接口配置）'
            else:
                item.update(parse_result(clean(json.loads(raw)),protocol,kind))
                item['status'],item['note'] = identity.grade(item) if kind.startswith('identity_') else fingerprints.grade(kind,item) if kind in fingerprints.BY_KIND else check_assertion(kind,item['text'],item['tool_calls'])
                for call in item['tool_calls']:
                    call['arguments'] = redact(call['arguments'],secrets,keys=True)
        if item.get('finish_reason') in ('length','max_tokens','MAX_TOKENS','incomplete') and item['status'] != 'error' and not kind.startswith('fp_token_'):
            item['status'] = 'warn'
            item['note'] += '；达到输出上限，内容可能不完整'
    except (httpx.HTTPError,TargetError,asyncio.TimeoutError) as error:
        item['note'] = f'请求失败：{type(error).__name__}'
    except (ValueError,TypeError,IndexError,KeyError,AttributeError):
        item['note'] = '响应结构不符合当前协议'
    # Preserve observed partial stream evidence on transport failures without passing it.
    if stream and item['status'] == 'error':
        item.update(stream.result(clean))
    elapsed = time.perf_counter()-start
    item['latency_ms'] = round(elapsed*1000,2)
    output = item['usage'].get('output_tokens')
    if item['status'] != 'error' and item['response_complete'] and output is not None and elapsed > 0:
        item['tps'] = round(output/elapsed,2)
    return clean(item)


def build_sections(samples: list, protocol: str, model: str, distill_requested: bool) -> list:
    names = {x['kind']:x for x in samples}
    passed = [x for x in samples if x['status'] in ('pass','sample')]
    errors = [x for x in samples if x['status'] in ('error','warn')]
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
       'detail': f"本次已完成 {len(samples)} 项，其中 {len(errors)} 项出现异常；小样本不代表长期 SLA。"},
      {'name':'蒸馏用途适用性', 'status': ('pass' if names.get('structured_sample',{}).get('status') == 'pass' else 'attention' if distill_requested else 'unknown'),
       'detail': ('仅验证一条训练格式样例；必须另行核验原厂及渠道的训练用途授权。' if distill_requested else '未执行训练数据样本检测；普通能力测试不等于蒸馏授权。')}
    ]
    return sections


def make_report(cfg, samples, report_id, created_at, state='running', current_case=None, elapsed_ms=0, stop_reason=None):
    protocol = resolve_protocol(cfg['protocol'],cfg['base_url'],cfg['model'])
    plan = [case_spec(c) for c in cases_for(cfg['mode'],cfg.get('distill',False),cfg.get('_identity_challenges'))]
    coverage = {k:sum(x.get('usage',{}).get(k) is not None for x in samples) for k in USAGE_FIELDS}
    totals = {k:sum(x['usage'][k] for x in samples if x.get('usage',{}).get(k) is not None) if coverage[k] else None for k in USAGE_FIELDS}
    totals['coverage'] = coverage
    totals['reported_requests'] = sum(bool(x.get('usage',{}).get('reported')) for x in samples)
    complete_usage = bool(samples) and all(x.get('usage',{}).get('complete') for x in samples)
    estimate = estimate_cost(totals,cfg.get('rates')) if complete_usage else None
    ttft = [x['ttft_ms'] for x in samples if x.get('ttft_ms') is not None and x['status'] != 'error']
    measured = [x for x in samples if x.get('tps') is not None]
    duration = sum(x['latency_ms'] for x in measured)/1000
    counts = {k:sum(x['status'] == k for x in samples) for k in ('pass','warn','error','sample')}
    sections = build_sections(samples,protocol,cfg['model'],cfg.get('distill',False))
    abnormal = counts['warn'] + counts['error']
    status = 'running' if state == 'running' else 'attention' if abnormal or state != 'completed' else 'pass'
    report = {'schema_version':2,'id':report_id,'created_at':created_at,'model':cfg['model'],
              'label':cfg.get('label',''),'base_host':httpx.URL(cfg['base_url']).host,'mode':cfg['mode'],'protocol':protocol,
              'run_state':state,'status':status,'current_case':current_case,'plan':plan,'samples':samples,'sections':sections,
              'stop_reason':stop_reason, 'elapsed_ms':round(elapsed_ms),
              'settings':{'reasoning':cfg.get('reasoning','auto'),'reasoning_scope':'reasoning_case',
                          'reasoning_scheduled':any(c['kind']=='reasoning' for c in plan),
                          'billing_multiplier':cfg.get('billing_multiplier') if cfg.get('billing_multiplier') is not None else (cfg.get('rates') or {}).get('group_ratio',1)},
              'stats':{'requests':len(samples),'planned':len(plan),'counts':counts,
                       'unexecuted':len(plan)-len(samples),'successful':counts['pass'],'errors':counts['error'],
                       'avg_ms':round(statistics.mean(x['latency_ms'] for x in samples),2) if samples else None,
                       'ttft_ms':round(statistics.mean(ttft),2) if ttft else None,'ttft_samples':len(ttft),
                       'tps':round(sum(x['usage']['output_tokens'] for x in measured)/duration,2) if duration>0 else None,
                       'tps_samples':len(measured),'usage':totals,'estimated_cost_usd':estimate,
                       'cost_basis':'按自填单价及供应商上报用量估算；未上报的缓存量不单独计价。' if estimate is not None else '缺少完整用量或单价，无法估算。'},
              'disclaimers':['行为样本和返回模型 ID 不证明真实模型身份或上游来源。',
                             'Token 用量来自供应商上报；估算费用不等于实际账单。',
                             'TTFT 为本服务器请求开始至首段正文到达，推理片段单独记录；非流式请求不测 TTFT。',
                             'TPS 为上报输出 Token / 请求总秒数（含等待），可能包含推理 Token；汇总按有效请求总用量 / 总耗时计算。',
                             '小样本性能不代表长期 SLA；未记录的字段显示为未知。']}
    if state != 'running':
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
    if cfg['mode']=='fingerprint':
        report['fingerprint']=fingerprints.analyze(cfg,samples,state)
        if report['fingerprint']['status'] in ('fail','unknown') and state!='running':report['status']='attention'
    if cfg['mode'] in ('standard','deep'):
        report['identity']=identity.analyze(cfg,samples,state)
        if state!='running' and report['identity']['status']!='pass':report['status']='attention'
        report['sections'][0]={'name':'模型身份初筛','status':report['identity']['status'],'detail':report['identity']['detail']}
    return redact(report,[cfg['key'], *cfg.get('custom_headers',{}).values()])


async def run_suite(cfg: dict, update=None, report_id=None, created_at=None) -> dict:
    report_id = report_id or uuid.uuid4().hex[:14]
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    protocol = resolve_protocol(cfg['protocol'],cfg['base_url'],cfg['model'])
    if cfg['mode'] in ('standard','deep') and '_identity_challenges' not in cfg:
        cfg['_identity_challenges']=identity.generate_challenges(3)
    start, samples, stop_reason = time.perf_counter(), [], None
    for case in cases_for(cfg['mode'],cfg.get('distill',False),cfg.get('_identity_challenges')):
        if update:
            await update(make_report(cfg,samples,report_id,created_at,current_case=case[0],elapsed_ms=(time.perf_counter()-start)*1000))
        # Bound whole-case duration as well as individual network reads.
        try:
            result = await asyncio.wait_for(run_one(cfg,protocol,case),timeout=CASE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            result = {**case_spec(case),'status':'error','note':f'单项检测超过 {CASE_TIMEOUT_SECONDS} 秒，已停止该请求。',
                      'latency_ms':CASE_TIMEOUT_SECONDS*1000,'ttft_ms':None,'tps':None,'usage':normalize_usage({},protocol),
                      'text':'','model_returned':None,'thinking_observed':False,'tool_observed':False}
        samples.append(result)
        if update:
            await update(make_report(cfg,samples,report_id,created_at,elapsed_ms=(time.perf_counter()-start)*1000))
        if len(samples)==1 and result.get('http_status') in (401,403):
            stop_reason = '首项鉴权失败，剩余项目未执行。'
            break
        if result.get('http_status') in (401,403,429):
            stop_reason='鉴权或限流错误，已停止剩余请求，未自动重试。';break
        if result['kind'].startswith('identity_') and result['status']!='pass':
            stop_reason='指纹样本未能取得有效完整回答，已停止剩余请求，不自动追加。';break
        if cfg['mode']=='fingerprint':
            if result.get('http_status') in (401,403,404,429):
                stop_reason='鉴权、接口或限流错误，已停止剩余指纹请求，未自动重试。';break
            def unusable(item):
                return item['status']=='error' or (item.get('fingerprint_group')=='token' and item.get('fingerprint_measurement',{}).get('value') is None)
            if len(samples)>=3 and all(unusable(s) for s in samples[-3:]):
                stop_reason='连续三个请求无法提供有效测量，已停止剩余指纹请求。';break
    return make_report(cfg,samples,report_id,created_at,state='completed',elapsed_ms=(time.perf_counter()-start)*1000,stop_reason=stop_reason)
