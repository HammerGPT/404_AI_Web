"""A single report presentation for live/history pages and downloadable PDFs."""
from __future__ import annotations
import asyncio
import base64
import json
import os
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .catalog import PROTOCOLS, REASONING_LABELS

ROOT = Path(__file__).parent
TEMPLATES = Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html']))
STATUS = {'pass':'通过','warn':'异常','error':'失败','sample':'仅采样','unknown':'未验证',
          'pending':'等待','running':'检测中','skipped':'未执行','interrupted':'已中断','attention':'需核查'}
MODES = {'quick':'快速','standard':'标准','deep':'深度'}
USAGE_LABELS = {'input_tokens':'输入 Token','output_tokens':'输出 Token','cache_read_tokens':'缓存读取',
                'cache_write_tokens':'缓存写入','reasoning_tokens':'推理 Token'}


def number(value, digits=2):
    return '未记录' if value is None else f'{value:,.{digits}f}'.rstrip('0').rstrip('.') if digits else f'{value:,}'


def duration(value):
    return '未采集' if value is None else f'{value/1000:,.2f} s'


def timestamp(value):
    if not value:
        return '未记录'
    try:
        return datetime.fromisoformat(value).strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError,TypeError):
        return '未记录'


def present(report):
    state = report.get('run_state','completed')
    samples = report.get('samples',[])
    by_kind = {s['kind']:s for s in samples}
    plan = report.get('plan') or [{'kind':s['kind'],'title':s['title']} for s in samples]
    cases = []
    for index, spec in enumerate(plan):
        sample = by_kind.get(spec['kind'])
        item = {**spec, **(sample or {})}
        item['number'] = index+1
        if not sample:
            item['status'] = ('running' if report.get('current_case')==spec['kind'] else 'pending') if state=='running' else 'skipped'
            item['note'] = '等待本项请求完成后显示响应及指标。' if state=='running' else '本次未执行，不计为检测失败。'
        item['status_label'] = STATUS.get(item.get('status'),'未验证')
        item['anchor'] = f"case-{report['id']}-{index}"
        item['usage_rows'] = [(label,number((item.get('usage') or {}).get(key),0) if (item.get('usage') or {}).get('reported') else '未上报') for key,label in USAGE_LABELS.items()]
        item['request_json'] = json.dumps(item['request'],ensure_ascii=False,indent=2) if item.get('request') else None
        item['request_json_compact'] = json.dumps(item['request'],ensure_ascii=False) if item.get('request') else None
        item['tool_json'] = json.dumps(item['tool_calls'],ensure_ascii=False,indent=2) if item.get('tool_calls') else None
        item['expected_tool_json'] = json.dumps(item['expected_tool'],ensure_ascii=False,indent=2) if item.get('expected_tool') else None
        cases.append(item)
    stats = report.get('stats',{})
    counts = {s:sum(x.get('status')==s for x in samples) for s in ('pass','warn','error','sample')}
    issues = counts['warn']+counts['error']
    title = {'running':'正在检测','completed':'检测完成','failed':'检测中断','interrupted':'检测中断'}.get(state,'检测完成')
    summary = f"已完成 {len(samples)}/{len(plan)} 项 · {counts['pass']} 项通过 · {issues} 项异常 · {counts['sample']} 项仅采样"
    if state == 'running':
        current = next((c['title'] for c in cases if c['status']=='running'),None)
        result_line = '正在执行：'+current if current else '正在整理检测进度。'
    else:
        highlighted = [s['title'] for s in samples if s.get('status') in ('error','warn')]
        if highlighted:
            result_line = '需要核查：'+'、'.join(highlighted[:4])+('等项目。' if len(highlighted)>4 else '。')
        else:
            passed = [s['title'] for s in samples if s.get('status')=='pass']
            result_line = ('已验证：'+'、'.join(passed[:4])+('等项目。' if len(passed)>4 else '。')) if passed else '尚无通过的检测项，详见逐项记录。'
    ttft_n = stats.get('ttft_samples',sum(s.get('ttft_ms') is not None for s in samples))
    metrics = [
        {'label':'TTFT · 平均首字延迟','value':duration(stats.get('ttft_ms')),'hint':f'{ttft_n} 个有效流式样本 · 首段正文'},
        {'label':'TPS · 端到端输出速率','value':number(stats.get('tps'))+(' tok/s' if stats.get('tps') is not None else ''),'hint':f"{stats.get('tps_samples',0)} 个有效请求 · 含等待与推理用量"},
        {'label':'检测总耗时','value':duration(report.get('elapsed_ms')),'hint':'当前进度已保存' if state=='running' else '全部用例的检测过程'},
        {'label':'估算费用','value':f"${stats['estimated_cost_usd']:.6f}" if stats.get('estimated_cost_usd') is not None else '未核算','hint':'自填价格 × 上报用量'},
    ]
    usage = stats.get('usage',{})
    total_usage = []
    for key,label in USAGE_LABELS.items():
        value = usage.get(key) if usage.get('reported_requests') else None
        coverage = usage.get('coverage',{}).get(key)
        total_usage.append({'label':label,'value':number(value,0) if value is not None else '未上报',
                            'coverage':f'{coverage}/{len(samples)} 项上报' if coverage is not None else '旧报告未区分缺失与零'})
    return {'data':report,'state':state,'title':title,'summary':summary,'result_line':result_line,'cases':cases,'metrics':metrics,
            'usage':total_usage,'issues':issues,'protocol':PROTOCOLS.get(report.get('protocol'),report.get('protocol','未记录')),
            'mode':MODES.get(report.get('mode'),report.get('mode','未记录')),'created_at':timestamp(report.get('created_at')),
            'legacy':report.get('schema_version',1)<2,
            'reasoning_label':REASONING_LABELS.get(report.get('settings',{}).get('reasoning'),'兼容模式')}


TEMPLATES.globals.update(duration=duration,number=number,status_labels=STATUS)


def render_report(report, interactive=True):
    return TEMPLATES.get_template('report.html').render(report=present(report),interactive=interactive)


class PDFRenderer:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.playwright = None
        self.browser = None

    async def _render(self, report):
        from playwright.async_api import async_playwright
        async with self.lock:
            if self.playwright is None:
                self.playwright = await async_playwright().start()
            if self.browser is None or not self.browser.is_connected():
                self.browser = await self.playwright.chromium.launch(
                    headless=True,executable_path=os.getenv('CHROMIUM_EXECUTABLE_PATH') or None,
                    args=['--no-sandbox','--disable-dev-shm-usage'])
            context = await self.browser.new_context(java_script_enabled=False,service_workers='block',offline=True)
            try:
                await context.route('**/*',lambda route: route.abort())
                page = await context.new_page()
                logo = base64.b64encode((ROOT/'static/assets/logo-on-light.png').read_bytes()).decode()
                css = (ROOT/'static/report.css').read_text()
                content = TEMPLATES.get_template('pdf.html').render(css=css,logo=logo,report_fragment=render_report(report,False))
                await page.set_content(content,wait_until='load',timeout=15000)
                return await page.pdf(format='A4',print_background=True,prefer_css_page_size=True,
                                      display_header_footer=True,header_template='<span></span>',
                                      footer_template='<div style="font-size:9px;width:100%;text-align:center;color:#788792">404 AI · <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                                      margin={'top':'14mm','bottom':'18mm','left':'13mm','right':'13mm'})
            finally:
                await context.close()

    async def render(self, report):
        # Includes queue time; repeated downloads never call model APIs.
        return await asyncio.wait_for(self._render(report),timeout=45)

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.browser = self.playwright = None


pdf_renderer = PDFRenderer()
