from __future__ import annotations
import asyncio
import html
import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from . import store
from .engine import run_suite, resolve_protocol, request_headers
from .security import validate_target, api_path, pinned_request, TargetError

APP_DIR = Path(__file__).parent
SESSIONS = {}
LOGIN_ATTEMPTS = defaultdict(deque)
RUN_LOCK = asyncio.Lock()
COOKIE_SECURE = os.getenv('COOKIE_SECURE','0') == '1'

@asynccontextmanager
async def lifespan(app):
    pw = os.getenv('ADMIN_PASSWORD','')
    if len(pw) < 12 or pw.startswith(('CHANGE_', 'REPLACE_')):
        raise RuntimeError('设置 ADMIN_PASSWORD（至少12位）后再启动。')
    store.init()
    yield

app = FastAPI(title='404 AI · Token Channel Tester', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount('/static',StaticFiles(directory=APP_DIR/'static'), name='static')

@app.middleware('http')
async def secure_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response

@app.get('/')
async def root():
    return FileResponse(APP_DIR/'static'/'index.html')

class LoginRequest(BaseModel):
    password: str


def get_session(request:Request):
    token=request.cookies.get('tester_session','')
    session=SESSIONS.get(token)
    if not session or session['exp'] < time.time():
        SESSIONS.pop(token,None)
        raise HTTPException(401,'请先登录')
    session['exp']=time.time()+3600*12
    return session


def require_csrf(request:Request, session=Depends(get_session)):
    if not secrets.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):
        raise HTTPException(403,'请求校验失败，请刷新网页')
    return session

@app.post('/api/login')
async def login(request:Request, payload:LoginRequest, response:Response):
    addr=request.client.host if request.client else 'unknown'
    q=LOGIN_ATTEMPTS[addr]
    now=time.time()
    while q and now-q[0]>900:q.popleft()
    if len(q)>=8:
        raise HTTPException(429,'登录尝试过多，请稍后重试')
    q.append(now)
    if not secrets.compare_digest(payload.password,os.environ['ADMIN_PASSWORD']):
        raise HTTPException(401,'密码错误')
    q.clear()
    token=secrets.token_urlsafe(32)
    csrf=secrets.token_urlsafe(30)
    SESSIONS[token]={'csrf':csrf,'exp':now+3600*12}
    response.set_cookie('tester_session',token,httponly=True,secure=COOKIE_SECURE,samesite='strict',max_age=3600*12,path='/')
    return {'ok':True,'csrf':csrf}

@app.get('/api/me')
async def me(session=Depends(get_session)):
    return {'ok':True,'csrf':session['csrf']}

@app.post('/api/logout')
async def logout(request:Request,response:Response,session=Depends(require_csrf)):
    SESSIONS.pop(request.cookies.get('tester_session',''),None)
    response.delete_cookie('tester_session',path='/')
    return {'ok':True}

class ModelLookup(BaseModel):
    base_url: str
    key: str = Field(min_length=1,max_length=512)
    protocol: str = 'auto'

class Config(BaseModel):
    base_url: str = Field(max_length=1024)
    key: str = Field(min_length=1,max_length=512)
    model: str = Field(min_length=1,max_length=128)
    mode: str = 'standard'
    protocol: str = 'auto'
    label: str = Field(default='', max_length=100)
    reasoning: str = 'auto'
    token_field: str = 'auto'
    distill: bool = False
    authorized_distill: bool = False
    custom_headers: dict[str,str] = Field(default_factory=dict)
    rates: dict[str,float] | None = None

    @field_validator('base_url')
    @classmethod
    def check_base(cls,v):
        return validate_target(v)

    @field_validator('mode')
    @classmethod
    def check_mode(cls,v):
        if v not in ('quick','standard','deep'): raise ValueError('无效测试模式')
        return v

    @field_validator('protocol')
    @classmethod
    def check_protocol(cls,v):
        if v not in ('auto','openai_chat','openai_responses','anthropic','gemini'): raise ValueError('无效协议')
        return v

    @field_validator('reasoning')
    @classmethod
    def check_reasoning(cls,v):
        if v not in ('auto','on','off'): raise ValueError('无效思考模式')
        return v

    @field_validator('token_field')
    @classmethod
    def check_token_field(cls,v):
        if v not in ('auto','max_tokens','max_completion_tokens'): raise ValueError('无效输出参数')
        return v

    @field_validator('custom_headers')
    @classmethod
    def check_headers(cls,v):
        if len(v)>8: raise ValueError('自定义 Header 最多8项')
        for k,val in v.items():
            if not re.fullmatch(r'[A-Za-z0-9-]{1,64}',k) or len(val)>256 or any(c in val for c in '\r\n'):
                raise ValueError('自定义 Header 不合法')
            if k.lower() in ('host','authorization','x-api-key','x-goog-api-key','cookie','proxy-authorization','content-length','transfer-encoding','connection'):
                raise ValueError('不能覆盖鉴权或网络关键 Header')
        return v

    @field_validator('rates')
    @classmethod
    def check_rates(cls,v):
        if v is not None:
            for name,val in v.items():
                if name not in ('input','output','cache_read','cache_write','group_ratio') or not isinstance(val,(int,float)) or val<0 or val>1000000:
                    raise ValueError('单价或倍率不合法')
        return v

@app.post('/api/models')
async def get_models(cfg:ModelLookup, session=Depends(require_csrf)):
    proto=resolve_protocol(cfg.protocol,cfg.base_url,'')
    url=api_path(cfg.base_url,'models')
    if proto=='gemini':
        from urllib.parse import urlsplit,urlunsplit
        u=urlsplit(validate_target(cfg.base_url))
        p = u.path.rstrip('/')
        url=urlunsplit((u.scheme,u.netloc,(p+'/models' if p.endswith('/v1beta') else p+'/v1beta/models'),'', ''))
    try:
        res,raw = await pinned_request('GET',url,request_headers(proto,cfg.key),timeout=15)
        if res.status_code>=300:return {'models':[],'note':f'模型列表返回 HTTP {res.status_code}；可手动输入模型 ID。'}
        data=json.loads(raw)
        models=data.get('data') or data.get('models') or []
        ids=[]
        for it in models[:500]:
            name=it.get('id') or it.get('name','') if isinstance(it,dict) else ''
            if name:
                ids.append(name.split('/')[-1] if proto=='gemini' else name)
        return {'models':sorted(set(ids)),'note':f'获取 {len(ids)} 个模型' if ids else '该渠道未提供可识别模型列表'}
    except (TargetError,ValueError,Exception) as e:
        return {'models':[],'note':'获取失败，可手动输入模型 ID。'}

@app.post('/api/run')
async def run_tests(cfg:Config,session=Depends(require_csrf)):
    if cfg.distill and not cfg.authorized_distill:
        raise HTTPException(400,'训练用途测试需确认已取得授权')
    if cfg.distill and cfg.mode=='quick':
        raise HTTPException(400,'快速模式不包含训练用途测试')
    if RUN_LOCK.locked():
        raise HTTPException(409,'已有测试进行中，请在完成后开始下一次')
    try:
        async with RUN_LOCK:
            result=await run_suite(cfg.model_dump())
            store.save(result)
            return result
    except TargetError as e:
        raise HTTPException(400,str(e)) from e

@app.get('/api/reports')
async def list_reports(session=Depends(get_session)):
    return {'reports':store.list_reports()}

@app.get('/api/reports/{report_id}')
async def report(report_id:str,session=Depends(get_session)):
    data=store.get(report_id)
    if data is None:raise HTTPException(404,'报告不存在')
    return data

@app.get('/api/reports/{report_id}/html')
async def report_html(report_id:str,session=Depends(get_session)):
    data=store.get(report_id)
    if data is None:raise HTTPException(404,'报告不存在')
    esc=html.escape
    rows=''.join(f'<tr><td>{esc(x["name"])}</td><td>{esc(x["status"])}</td><td>{esc(x["detail"])}</td></tr>' for x in data['sections'])
    samples=''.join(f'<tr><td>{esc(x["title"])}</td><td>{esc(x["status"])}</td><td>{x["latency_ms"]}ms</td><td>{esc(x["note"])}</td></tr>' for x in data['samples'])
    stats=data['stats']
    html_doc=f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>404 AI · 渠道检测报告</title><style>body{{font:15px/1.7 system-ui,sans-serif;max-width:960px;margin:36px auto;padding:0 18px;color:#263144}}h1{{font-size:26px}}h2{{margin-top:32px;font-size:19px}}p{{color:#586375}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}th{{background:#f3f5f8}}small{{color:#697386}}@media print{{body{{margin:10px auto}}}}</style></head><body><h1>404 AI · Token 渠道检测报告</h1><p>报告ID：{esc(data['id'])}　检测时间：{esc(data['created_at'])}<br>渠道：{esc(data.get('label') or data['base_host'])}　模型：{esc(data['model'])}　模式：{esc(data['mode'])}　协议：{esc(data['protocol'])}</p><p>有效请求：{stats['successful']}/{stats['requests']}　总上报输入Token：{stats['usage']['input_tokens']}　输出Token：{stats['usage']['output_tokens']}　估算费用：{esc(str(stats['estimated_cost_usd'])) if stats['estimated_cost_usd'] is not None else '未核实'}</p><h2>检测摘要</h2><table><thead><tr><th>项目</th><th>状态</th><th>结果与限制</th></tr></thead><tbody>{rows}</tbody></table><h2>测试样本</h2><table><thead><tr><th>用例</th><th>状态</th><th>耗时</th><th>说明</th></tr></thead><tbody>{samples}</tbody></table><h2>报告限制</h2><p>{'<br>'.join(map(esc,data['disclaimers']))}</p><small>敏感 API Key 不包含在报告中。可使用浏览器打印为 PDF。</small></body></html>'''
    return HTMLResponse(html_doc,headers={'Content-Disposition':f'attachment; filename="channel-report-{report_id}.html"'})
