"""One in-process runner with durable, credential-free report snapshots."""
from __future__ import annotations
import asyncio
import uuid
from datetime import datetime, timezone
from . import store
from .engine import make_report, run_suite, resolve_protocol, redact
from . import fingerprints, identity
from urllib.parse import urlsplit


class BusyError(Exception):
    pass


class RunManager:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.task = None
        self.active_id = None

    async def start(self, cfg, request_id):
        async with self.lock:
            existing = store.by_request(request_id)
            if existing:
                return existing
            if self.task and not self.task.done():
                raise BusyError()
            cfg = dict(cfg)
            if cfg['protocol'] == 'auto':
                cfg['protocol'] = store.model_protocol(cfg['model']) or cfg['protocol']
            cfg['protocol']=resolve_protocol(cfg['protocol'],cfg['base_url'],cfg['model'])
            profile=store.reasoning_profile(cfg['model'],cfg['protocol'])
            selected=cfg.get('reasoning') or profile['default']
            if selected not in profile['options'] and selected not in ('on','off'):
                raise ValueError('当前模型/协议未配置这个思考选项，请刷新或在模型配置中维护。')
            cfg['reasoning']=selected
            multiplier=cfg.get('billing_multiplier')
            if multiplier is None:
                multiplier=(cfg.get('rates') or {}).get('group_ratio',store.billing_settings()['default'])
            cfg['billing_multiplier']=multiplier
            if cfg.get('rates'):
                cfg['rates']={**cfg['rates'],'group_ratio':multiplier}
            if cfg['mode']=='fingerprint':
                if cfg.get('fingerprint_action','verify')=='reference':
                    if not cfg.get('fingerprint_trusted') or not cfg.get('fingerprint_name','').strip():
                        raise ValueError('建立基线需填写名称并确认这是你认可的独立可信参考来源。')
                    if cfg.get('fingerprint_source')=='official':
                        address=urlsplit(cfg['base_url'])
                        if address.hostname not in fingerprints.OFFICIAL_HOSTS or address.port not in (None,443):
                            raise ValueError('该地址不是当前识别的官方直连域名；云平台或其他可信来源请选择用户确认可信渠道。')
                else:
                    reference=store.get_fingerprint_baseline(cfg.get('fingerprint_reference',''))
                    if not reference or not reference['enabled']:raise ValueError('请先选择已建立并启用的可信指纹基线。')
                    if cfg['protocol']=='openai_chat' and cfg.get('token_field','auto')=='auto':
                        cfg['token_field']=reference['conditions']['token_field']
                    fingerprints.validate_reference(reference,cfg)
                    cfg['_fingerprint_reference']=reference
            cfg['identity_enabled']=identity.enabled(cfg)
            if cfg['identity_enabled']:
                cfg['_identity_challenges']=identity.generate_challenges(3)
            report_id = uuid.uuid4().hex[:14]
            created_at = datetime.now(timezone.utc).isoformat()
            report = make_report(cfg,[],report_id,created_at)
            report['revision'] = 1
            store.save(report,request_id)
            self.active_id = report_id
            self.task = asyncio.create_task(self._execute(cfg,report),name='channel-check-'+report_id)
            return report

    async def _execute(self, cfg, snapshot):
        async def update(report):
            nonlocal snapshot
            report['revision'] = snapshot['revision'] + 1
            baseline=None
            if report.get('run_state')=='completed' and cfg.get('fingerprint_action')=='reference' and cfg['mode']=='fingerprint':
                baseline=fingerprints.baseline_document(cfg,report)
                if baseline:
                    baseline=redact(baseline,[cfg['key'],*cfg.get('custom_headers',{}).values()])
                    report['fingerprint']['created_baseline']={k:baseline[k] for k in ('id','name','model')}
            store.save(report,baseline=baseline)
            snapshot = report
        try:
            result = await run_suite(cfg,update,report_id=snapshot['id'],created_at=snapshot['created_at'])
            await update(result)
        except asyncio.CancelledError:
            snapshot.update(run_state='interrupted',status='attention',current_case=None,
                            stop_reason='服务停止，已完成的结果保留，不会自动重试。',
                            finished_at=datetime.now(timezone.utc).isoformat())
            await update(snapshot.copy())
            raise
        except Exception:
            # Do not log exception bodies: transport/providers can echo credentials.
            snapshot.update(run_state='failed',status='attention',current_case=None,
                            stop_reason='检测中断，已完成的结果保留。请检查接口配置后重新发起。',
                            finished_at=datetime.now(timezone.utc).isoformat())
            await update(snapshot.copy())
        finally:
            cfg.clear()
            self.active_id = None

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


manager = RunManager()
