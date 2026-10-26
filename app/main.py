from __future__ import annotations
import asyncio
import json
import math
import os
import re
import secrets
import time
import sqlite3
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator
from . import store, auth, fingerprints, identity
from .catalog import PROTOCOLS, BRANDS, REASONING_LABELS, metadata, validate_reasoning_profile
from .reports import render_report, pdf_renderer
from .runs import manager, BusyError
from .engine import cases_for, resolve_protocol, request_headers
from .security import validate_target, api_path, pinned_request, TargetError

APP_DIR = Path(__file__).parent
LOGIN_ATTEMPTS = defaultdict(deque)
PASSWORD_WORKERS = asyncio.Semaphore(2)
COOKIE_SECURE = os.getenv('COOKIE_SECURE','0') == '1'

@asynccontextmanager
async def lifespan(app):
    store.init()
    await asyncio.to_thread(auth.init,os.getenv('ADMIN_PASSWORD',''))
    store.interrupt_runs()
    yield
    await manager.close()
    await pdf_renderer.close()

app = FastAPI(title='404 AI · Token Channel Tester', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount('/static',StaticFiles(directory=APP_DIR/'static'), name='static')

@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Pydantic's default errors include input values, which may contain API keys.
    return JSONResponse({'detail':'请求参数不合法，请检查字段格式、长度及数值范围。'},status_code=422)

@app.middleware('http')
async def secure_headers(request: Request, call_next):
    response = await call_next(request)
    if getattr(request.state,'authenticated',False) and response.status_code<400:
        token=request.cookies.get('tester_session','')
        session=auth.renew_session(token)
        if session:
            set_session_cookie(response,token,session)
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
    password: str = Field(min_length=1,max_length=auth.PASSWORD_MAX_LENGTH)


def set_session_cookie(response,token,session):
    response.set_cookie('tester_session',token,httponly=True,secure=COOKIE_SECURE,samesite='strict',
                        max_age=auth.lifetime(session['session_days']),
                        expires=datetime.fromtimestamp(session['expires_at'],timezone.utc),path='/')


def rate_limit(request, purpose):
    addr=request.client.host if request.client else 'unknown'
    attempts=LOGIN_ATTEMPTS[(purpose,addr)]
    timestamp=time.time()
    while attempts and timestamp-attempts[0]>900:attempts.popleft()
    if len(attempts)>=8:
        raise HTTPException(429,'尝试过多，请稍后重试')
    attempts.append(timestamp)
    return attempts


async def password_work(function,*args):
    async with PASSWORD_WORKERS:
        return await asyncio.to_thread(function,*args)


def get_session(request:Request):
    token=request.cookies.get('tester_session','')
    session=auth.read_session(token)
    if session is None:
        raise HTTPException(401,'请先登录')
    request.state.authenticated=True
    return session


def require_csrf(request:Request, session=Depends(get_session)):
    if not secrets.compare_digest(request.headers.get('x-csrf-token','').encode('utf-8'),session['csrf'].encode('ascii')):
        raise HTTPException(403,'请求校验失败，请刷新网页')
    return session

@app.post('/api/login')
async def login(request:Request, payload:LoginRequest, response:Response):
    attempts=rate_limit(request,'login')
    version=await password_work(auth.verify_password,payload.password)
    if version is None:
        raise HTTPException(401,'密码错误')
    try:
        token,session=auth.create_session(version,request.cookies.get('tester_session',''))
    except auth.InvalidSession:
        raise HTTPException(401,'密码已变更，请重新登录。') from None
    attempts.clear()
    set_session_cookie(response,token,session)
    return {'ok':True,'csrf':session['csrf']}

@app.get('/api/me')
async def me(session=Depends(get_session)):
    return {'ok':True,'csrf':session['csrf']}

@app.post('/api/logout')
async def logout(request:Request,response:Response,session=Depends(require_csrf)):
    auth.revoke_session(request.cookies.get('tester_session',''))
    response.delete_cookie('tester_session',path='/')
    return {'ok':True}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1,max_length=auth.PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=auth.PASSWORD_MIN_LENGTH,max_length=auth.PASSWORD_MAX_LENGTH)
    confirm_password: str = Field(min_length=1,max_length=auth.PASSWORD_MAX_LENGTH)

    @field_validator('current_password','new_password','confirm_password')
    @classmethod
    def valid_password_text(cls,value):
        try:value.encode('utf-8')
        except UnicodeError:raise ValueError('密码字符不合法') from None
        return value


class SessionPolicy(BaseModel):
    session_days: int = Field(strict=True,ge=auth.MIN_SESSION_DAYS,le=auth.MAX_SESSION_DAYS)


@app.get('/api/account')
async def account_settings(session=Depends(get_session)):
    return auth.account_settings()


@app.put('/api/account/session')
async def account_session(cfg:SessionPolicy,request:Request,session=Depends(require_csrf)):
    try:
        return auth.update_session_days(request.cookies.get('tester_session',''),cfg.session_days)
    except auth.InvalidSession:
        raise HTTPException(401,'登录已失效，请重新登录。') from None


@app.post('/api/account/password')
async def account_password(cfg:PasswordChange,request:Request,response:Response,session=Depends(require_csrf)):
    if not secrets.compare_digest(cfg.new_password.encode('utf-8'),cfg.confirm_password.encode('utf-8')):
        raise HTTPException(400,'两次新密码输入不一致。')
    attempts=rate_limit(request,'password-change')
    try:
        await password_work(auth.change_password,request.cookies.get('tester_session',''),cfg.current_password,cfg.new_password)
    except auth.InvalidSession:
        raise HTTPException(401,'登录已失效，请重新登录。') from None
    except (auth.InvalidPassword,auth.PasswordPolicyError) as error:
        raise HTTPException(400,str(error)) from None
    except Exception:
        raise HTTPException(503,'密码修改暂时不可用，请稍后重试。') from None
    attempts.clear()
    response.delete_cookie('tester_session',path='/')
    return {'ok':True,'message':'密码已更新，请使用新密码重新登录。'}

class ModelLookup(BaseModel):
    base_url: str = Field(max_length=1024)
    key: str = Field(min_length=1,max_length=512)
    protocol: str = 'auto'
    model: str = Field(default='',max_length=128)

    @field_validator('base_url')
    @classmethod
    def check_base(cls,v):
        return validate_target(v)

    @field_validator('protocol')
    @classmethod
    def check_protocol(cls,v):
        if v != 'auto' and v not in PROTOCOLS:
            raise ValueError('无效协议')
        return v

class Config(BaseModel):
    base_url: str = Field(max_length=1024)
    key: str = Field(min_length=1,max_length=512)
    model: str = Field(min_length=1,max_length=128)
    mode: str = 'standard'
    protocol: str = 'auto'
    label: str = Field(default='', max_length=100)
    reasoning: str | None = None
    token_field: str = 'auto'
    distill: bool = False
    authorized_distill: bool = False
    custom_headers: dict[str,str] = Field(default_factory=dict)
    rates: dict[str,float] | None = None
    billing_multiplier: float | None = Field(default=None,ge=0,le=1000000,allow_inf_nan=False)
    fingerprint_action: str = 'verify'
    fingerprint_reference: str = Field(default='',max_length=100)
    fingerprint_name: str = Field(default='',max_length=100)
    fingerprint_source: str = 'trusted'
    fingerprint_trusted: bool = False
    identity_enabled: bool | None = None

    @field_validator('fingerprint_action')
    @classmethod
    def check_fingerprint_action(cls,value):
        if value not in ('verify','reference'):raise ValueError('无效指纹操作')
        return value

    @field_validator('fingerprint_source')
    @classmethod
    def check_fingerprint_source(cls,value):
        if value not in ('official','trusted'):raise ValueError('无效参考来源')
        return value

    @field_validator('base_url')
    @classmethod
    def check_base(cls,v):
        return validate_target(v)

    @field_validator('mode')
    @classmethod
    def check_mode(cls,v):
        if v not in ('quick','standard','deep','fingerprint'): raise ValueError('无效测试模式')
        return v

    @field_validator('protocol')
    @classmethod
    def check_protocol(cls,v):
        if v != 'auto' and v not in PROTOCOLS: raise ValueError('无效协议')
        return v

    @field_validator('reasoning')
    @classmethod
    def check_reasoning(cls,v):
        if v is None:return v
        if v not in REASONING_LABELS and v not in ('on','off'): raise ValueError('无效思考模式')
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
                if name not in ('input','output','cache_read','cache_write','group_ratio') or not isinstance(val,(int,float)) or not math.isfinite(val) or val<0 or val>1000000:
                    raise ValueError('单价或倍率不合法')
        return v

    @model_validator(mode='after')
    def consistent_multiplier(self):
        old=(self.rates or {}).get('group_ratio')
        if self.billing_multiplier is not None and old is not None and old!=self.billing_multiplier:
            raise ValueError('倍率设置冲突')
        return self

@app.post('/api/models')
async def get_models(cfg:ModelLookup, session=Depends(require_csrf)):
    selected = store.model_protocol(cfg.model) if cfg.protocol == 'auto' else None
    proto=resolve_protocol(selected or cfg.protocol,cfg.base_url,cfg.model)
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
    except Exception:
        return {'models':[],'note':'获取失败，可手动输入模型 ID。'}


class ModelPreset(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    model_id: str = Field(min_length=1,max_length=128)
    brand: str = 'custom'
    protocol: str = 'openai_chat'
    enabled: bool = True
    sort_order: int = Field(default=0,ge=-10000,le=10000)
    reasoning_options: list[str] | None = Field(default=None,min_length=1,max_length=5)
    reasoning_default: str | None = None

    @model_validator(mode='after')
    def check_reasoning_profile(self):
        if (self.reasoning_options is None)!=(self.reasoning_default is None):
            raise ValueError('请同时设置思考选项和默认值')
        if self.reasoning_options is not None:
            validate_reasoning_profile(self.protocol,self.reasoning_options,self.reasoning_default)
        return self

    @field_validator('name','model_id')
    @classmethod
    def clean_text(cls,value):
        value = value.strip()
        if not value or any(ord(c)<32 for c in value):
            raise ValueError('字段为空或含控制字符')
        return value

    @field_validator('protocol')
    @classmethod
    def valid_protocol(cls,value):
        if value not in PROTOCOLS:
            raise ValueError('无效协议')
        return value

    @field_validator('brand')
    @classmethod
    def valid_brand(cls,value):
        if value not in BRANDS:
            raise ValueError('无效品牌')
        return value


@app.get('/api/catalog')
async def model_catalog(session=Depends(get_session)):
    return {**metadata(),'models':store.list_models(),'billing':store.billing_settings()}


@app.get('/api/fingerprints')
async def fingerprint_catalog(session=Depends(get_session)):
    fields=('id','name','model','protocol','source_host','source_kind','created_at','report_id','enabled')
    references=[{k:r[k] for k in fields} for r in store.list_fingerprint_baselines()]
    return {**fingerprints.catalog(),'references':references,'screening':identity.metadata(),'base_requests':{m:len(cases_for(m,identity_enabled=False)) for m in ('quick','standard','deep')}}


@app.get('/api/fingerprints/{reference_id}')
async def fingerprint_reference(reference_id:str,session=Depends(get_session)):
    reference=store.get_fingerprint_baseline(reference_id)
    if not reference:raise HTTPException(404,'参考指纹不存在')
    return reference


class ReferenceEnabled(BaseModel):
    enabled: bool


@app.put('/api/fingerprints/{reference_id}')
async def fingerprint_enabled(reference_id:str,cfg:ReferenceEnabled,session=Depends(require_csrf)):
    try:store.set_fingerprint_baseline_enabled(reference_id,cfg.enabled)
    except KeyError:raise HTTPException(404,'参考指纹不存在') from None
    return {'ok':True}


class BillingSettings(BaseModel):
    options: list[float] = Field(min_length=1,max_length=12)
    default: float = Field(ge=0,le=1000000,allow_inf_nan=False)

    @model_validator(mode='after')
    def valid_multipliers(self):
        if any(not math.isfinite(x) or not 0<=x<=1000000 for x in self.options):
            raise ValueError('倍率必须是有效的非负数')
        if len(set(self.options))!=len(self.options) or self.default not in self.options:
            raise ValueError('倍率不能重复，默认值必须在列表中')
        return self


@app.put('/api/settings/billing')
async def update_billing(cfg:BillingSettings,session=Depends(require_csrf)):
    store.save_billing(cfg.model_dump())
    return cfg.model_dump()


def write_preset(cfg, model_id=None):
    try:
        return {'id':store.put_model(cfg.model_dump(),model_id)}
    except sqlite3.IntegrityError:
        raise HTTPException(409,'这个模型 ID 已存在，请编辑原有配置。') from None
    except KeyError:
        raise HTTPException(404,'模型配置不存在') from None
    except ValueError:
        raise HTTPException(400,'思考选项与当前模型配置不一致，请重新编辑。') from None


@app.post('/api/catalog',status_code=201)
async def add_preset(cfg:ModelPreset,session=Depends(require_csrf)):
    return write_preset(cfg)


@app.put('/api/catalog/{model_id}')
async def edit_preset(model_id:str,cfg:ModelPreset,session=Depends(require_csrf)):
    return write_preset(cfg,model_id)


async def start_run(cfg, request, legacy=False):
    if cfg.distill and not cfg.authorized_distill:
        raise HTTPException(400,'训练用途测试需确认已取得授权')
    if cfg.distill and cfg.mode not in ('standard','deep'):
        raise HTTPException(400,'仅标准/深度档包含授权训练样例')
    request_id = request.headers.get('Idempotency-Key','') or (secrets.token_hex(16) if legacy else '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',request_id):
        raise HTTPException(400,'缺少有效的请求标识，请刷新页面。')
    try:
        return await manager.start(cfg.model_dump(),request_id)
    except BusyError:
        raise HTTPException(409,'已有检测进行中，请查看当前进度。') from None
    except ValueError as error:
        raise HTTPException(400,str(error)) from None


@app.post('/api/runs',status_code=202)
async def create_run(cfg:Config,request:Request,session=Depends(require_csrf)):
    report = await start_run(cfg,request)
    return {'id':report['id'],'run_state':report['run_state']}


@app.get('/api/runs/current')
async def current_run(session=Depends(get_session)):
    return {'id':manager.active_id}


@app.get('/api/runs/by-request/{request_id}')
async def find_run(request_id:str,session=Depends(get_session)):
    report = store.by_request(request_id)
    if not report:
        raise HTTPException(404,'未找到对应检测，请检查历史记录。')
    return {'id':report['id'],'run_state':report['run_state']}


@app.post('/api/run')
async def legacy_run(cfg:Config,request:Request,session=Depends(require_csrf)):
    # Keep the old synchronous endpoint while using the same runner and lock.
    report = await start_run(cfg,request,legacy=True)
    if report['run_state']=='running' and manager.task:
        await asyncio.shield(manager.task)
    return store.get(report['id'])


@app.get('/api/runs/{report_id}/events')
async def run_events(report_id:str,request:Request,session=Depends(get_session)):
    if not store.get(report_id):
        raise HTTPException(404,'检测不存在')
    token = request.cookies.get('tester_session','')
    async def events():
        last_revision = None
        heartbeat = time.monotonic()
        while True:
            if auth.read_session(token) is None:
                yield 'event: expired\ndata: {}\n\n'
                return
            report = store.get(report_id)
            if report is None:
                return
            revision = report.get('revision',0)
            if revision != last_revision:
                data = json.dumps({'id':report_id,'state':report.get('run_state','completed'),
                                   'html':render_report(report)},ensure_ascii=False)
                yield f'id: {revision}\nevent: snapshot\ndata: {data}\n\n'
                last_revision = revision
            if report.get('run_state') != 'running':
                return
            if time.monotonic()-heartbeat >= 10:
                yield ': keepalive\n\n'
                heartbeat = time.monotonic()
            await asyncio.sleep(.5)
    return StreamingResponse(events(),media_type='text/event-stream',
                             headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})


@app.get('/api/reports')
async def list_reports(session=Depends(get_session)):
    return {'reports':store.list_reports()}


def require_report(report_id):
    data = store.get(report_id)
    if data is None:
        raise HTTPException(404,'报告不存在')
    return data


@app.get('/api/reports/{report_id}')
async def report(report_id:str,session=Depends(get_session)):
    return require_report(report_id)


@app.get('/api/reports/{report_id}/view',response_class=HTMLResponse)
async def report_view(report_id:str,session=Depends(get_session)):
    return HTMLResponse(render_report(require_report(report_id)))


@app.get('/api/reports/{report_id}/pdf')
async def report_pdf(report_id:str,session=Depends(get_session)):
    data = require_report(report_id)
    if data.get('run_state') == 'running':
        raise HTTPException(409,'检测结束后可下载 PDF。')
    try:
        pdf = await pdf_renderer.render(data)
    except Exception:
        raise HTTPException(503,'PDF 生成暂时不可用，请稍后重试。') from None
    return Response(pdf,media_type='application/pdf',
                    headers={'Content-Disposition':f'attachment; filename="404-ai-report-{data["id"]}.pdf"'})
