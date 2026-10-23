'use strict';
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let csrf = '';
let currentReport = null;
function show(el, state=true) { el.classList.toggle('hidden', !state); }
function mode(){return document.querySelector('input[name="mode"]:checked').value}
async function api(path, options={}) {
  const response = await fetch(path, {credentials:'same-origin',cache:'no-store',...options,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...(csrf?{'X-CSRF-Token':csrf}:{}),...(options.headers||{})}});
  let data={};try{data=await response.json()}catch{data={detail:'服务器返回格式错误'}}
  if(!response.ok){if(response.status===401 && path!=='/api/login') showLogin();throw new Error(typeof data.detail==='string'?data.detail:`请求失败：HTTP ${response.status}`)}
  return data;
}
function showLogin(){show($('loginScreen'));show($('workspace'),false);csrf=''}
function showWorkspace(){show($('loginScreen'),false);show($('workspace'));}
async function initialize(){try{const me=await api('/api/me');csrf=me.csrf;showWorkspace();}catch{showLogin()}}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();show($('loginError'),false);$('loginBtn').disabled=true;try{const data=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});csrf=data.csrf;$('password').value='';showWorkspace();}catch(err){$('loginError').textContent=err.message;show($('loginError'))}finally{$('loginBtn').disabled=false;}});
$('logoutBtn').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'})}catch{}$('apiKey').value='';showLogin()});
function setPage(name){let test=name==='test';show($('testPanel'),test);show($('historyPanel'),!test);$('navTest').classList.toggle('active',test);$('navHistory').classList.toggle('active',!test);$('crumb').textContent=test?'渠道检测':'历史报告';if(!test)loadHistory()}
$('navTest').addEventListener('click',()=>setPage('test'));
$('navHistory').addEventListener('click',()=>setPage('history'));
$('refreshHistory').addEventListener('click',loadHistory);
$('toggleKey').addEventListener('click',()=>{$('apiKey').type=$('apiKey').type==='password'?'text':'password'});
$('advancedToggle').addEventListener('click',()=>{const expanded=$('advancedFields').classList.contains('hidden');show($('advancedFields'),expanded);$('advancedArrow').textContent=expanded?'−':'＋'});
$('clearResults').addEventListener('click',()=>show($('resultSection'),false));
$('closeHistoryDetail').addEventListener('click',()=>show($('historyDetail'),false));
$('distill').addEventListener('change',()=>show($('authorizeLine'),$('distill').checked));
show($('authorizeLine'),false);
const money = x => x === null||x===undefined ? '未核算' : '$'+(Number(x)<.01 ? Number(x).toFixed(6):Number(x).toFixed(4));
const fmt = x => Number(x??0).toLocaleString('zh-CN');
const STATUS = {pass:['通过','pass'],attention:['需核查','attention'],error:['失败','error'],unknown:['未验证','unknown'],warn:['存在异常','attention'],sample:['已采样','unknown']};
function statusBadge(status, override){const [label, css]=STATUS[status]||['未验证','unknown'];return `<span class="status-pill ${css}">${escapeHTML(override||label)}</span>`}
$('loadModels').addEventListener('click',async()=>{
  $('modelNote').textContent='正在获取模型列表…';
  try{const data=await api('/api/models',{method:'POST',body:JSON.stringify({base_url:$('baseUrl').value.trim(),key:$('apiKey').value.trim(),protocol:$('protocol').value})});
    const dl=$('modelList');dl.replaceChildren();for(const model of data.models){const o=document.createElement('option');o.value=model;dl.appendChild(o)}
    $('modelNote').textContent=data.note || '可直接手动输入模型 ID';
  }catch(err){$('modelNote').textContent=err.message}
});
function numberOrUndefined(id){const v=$(id).value.trim();return v===''?undefined:Number(v)}
function createConfig(){
  let custom={};const raw=$('customHeaders').value.trim();if(raw){try{custom=JSON.parse(raw)}catch{throw new Error('自定义 Header 需要填写合法 JSON')}if(typeof custom!=='object'||Array.isArray(custom)||!custom)throw new Error('自定义 Header 必须是 JSON 对象')}
  const input=numberOrUndefined('inputRate'),output=numberOrUndefined('outputRate');
  let rates=null;
  if(input!==undefined||output!==undefined){if(input===undefined||output===undefined)throw new Error('估算费用需要同时填写输入和输出单价');rates={input,output,group_ratio:numberOrUndefined('groupRatio')??1};const read=numberOrUndefined('cacheReadRate'),write=numberOrUndefined('cacheWriteRate');if(read!==undefined)rates.cache_read=read;if(write!==undefined)rates.cache_write=write}
  return {base_url:$('baseUrl').value.trim(),key:$('apiKey').value.trim(),model:$('model').value.trim(),mode:mode(),protocol:$('protocol').value,label:$('groupLabel').value.trim(),reasoning:$('reasoning').value,token_field:$('tokenField').value,distill:$('distill').checked,authorized_distill:$('distillAuth').checked,custom_headers:custom,rates};
}
function loading(visible){let overlay=$('runLoading');if(!overlay){overlay=document.createElement('div');overlay.id='runLoading';overlay.className='loading-mask';overlay.innerHTML='<div class="loading-card"><div class="spinner"></div><strong>正在运行检测用例</strong><p>测试按顺序执行，避免重复计费。<br>部分推理模型的响应可能较慢。</p><small>请保持当前页面开启，关闭页面可能中止结果接收。</small></div>';document.body.appendChild(overlay)}show(overlay,visible)}
$('testForm').addEventListener('submit',async e=>{
  e.preventDefault();show($('formError'),false);
  try{const cfg=createConfig();if(cfg.distill&&!cfg.authorized_distill)throw new Error('请确认已取得训练数据用途授权');
    if(cfg.distill&&cfg.mode==='quick')throw new Error('快速检测不包含训练数据样例，请选择标准或深度模式');
    $('runBtn').disabled=true;loading(true);
    const report=await api('/api/run',{method:'POST',body:JSON.stringify(cfg)});
    // Clear the key from the DOM after use; never save it in localStorage or history.
    $('apiKey').value='';currentReport=report;renderReport(report,$('resultBody'));show($('resultSection'));$('resultSection').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(err){$('formError').textContent=err.message;show($('formError'))}finally{loading(false);$('runBtn').disabled=false}
});
function summaryRows(report){return report.sections.map(s=>`<div class="report-line"><div><strong>${escapeHTML(s.name)}</strong><p>${escapeHTML(s.detail)}</p></div>${statusBadge(s.status)}</div>`).join('')}
function caseRows(report){return report.samples.map((s,i)=>{
  const usage=s.usage||{};
  return `<div class="sample-line"><button class="sample-trigger" type="button" data-toggle-case="${i}"><strong>${escapeHTML(s.title)}</strong><span>${escapeHTML(s.status)} · ${fmt(s.latency_ms)} ms　⌄</span></button><div class="sample-content hidden" id="case-${report.id}-${i}">说明：${escapeHTML(s.note)}\n模型返回：${escapeHTML(s.model_returned||'未提供')}\n首个内容 Token：${s.ttft_ms===null?'未采集':fmt(s.ttft_ms)+' ms'}\n上报输入 / 输出：${fmt(usage.input_tokens)} / ${fmt(usage.output_tokens)} Tokens\n缓存读取 / 推理：${fmt(usage.cache_read_tokens)} / ${fmt(usage.reasoning_tokens)} Tokens\n\n响应片段：\n${escapeHTML(s.text||'无内容（工具调用、失败或结构异常时可能为空）')}</div></div>`
}).join('')}
function renderReport(r,target){
 const st=r.stats,used=st.usage||{};
 let html=`<div class="report"><div class="report-header"><div><div class="report-title">${escapeHTML(r.label||r.base_host)} · ${escapeHTML(r.model)}</div><div class="report-meta">${escapeHTML(r.protocol)}　·　${escapeHTML(r.mode)}　·　${escapeHTML(r.created_at.replace('T',' ').slice(0,19))} UTC</div></div>${statusBadge(r.status)}</div>
 <div class="metric-strip"><div class="metric"><span>可用响应</span><strong>${st.successful}/${st.requests}</strong></div><div class="metric"><span>平均响应</span><strong>${st.avg_ms===null?'—':(st.avg_ms/1000).toFixed(2)+'s'}</strong></div><div class="metric"><span>上报 Token</span><strong>${fmt((used.input_tokens||0)+(used.output_tokens||0))}</strong></div><div class="metric"><span>估算费用</span><strong>${money(st.estimated_cost_usd)}</strong></div></div>
 <div class="report-subtitle">七项检测结果</div>${summaryRows(r)}
 <div class="disclaimer">${r.disclaimers.map(escapeHTML).join('<br>')}<br>费用依据：${escapeHTML(st.cost_basis)}</div>
 <div class="report-subtitle">本次请求记录</div>${caseRows(r)}
 <div class="reconcile"><label for="charge-${r.id}">手动核对实际扣费</label><input id="charge-${r.id}" type="number" min="0" step="any" placeholder="账单金额 USD"><small id="reconcile-${r.id}">输入供应商账单金额，与单价估算比较（不自动读取余额）。</small></div>
 <div class="report-actions"><a class="btn btn-primary" href="/api/reports/${encodeURIComponent(r.id)}/html" download>导出 HTML 报告 ↗</a><button class="btn btn-light" type="button" data-export-json="${escapeHTML(r.id)}">导出 JSON</button><button class="btn btn-light" type="button" data-copy-id="${escapeHTML(r.id)}">复制报告 ID</button></div></div>`;
 target.innerHTML=html;
 target.querySelectorAll('[data-toggle-case]').forEach(b=>b.addEventListener('click',()=>{const elem=target.querySelector(`[id="case-${r.id}-${b.dataset.toggleCase}"]`);if(elem)elem.classList.toggle('hidden')}));
 target.querySelector('[data-export-json]').addEventListener('click',()=>{const blob=new Blob([JSON.stringify(r,null,2)],{type:'application/json'});const u=URL.createObjectURL(blob);const a=document.createElement('a');a.href=u;a.download=`channel-report-${r.id}.json`;a.click();URL.revokeObjectURL(u)});
 target.querySelector('[data-copy-id]').addEventListener('click',async()=>{await navigator.clipboard.writeText(r.id)});
 target.querySelector(`#charge-${r.id}`).addEventListener('input',e=>{const output=target.querySelector(`#reconcile-${r.id}`);const actual=e.target.value;if(actual===''){output.textContent='输入实际扣费金额后显示差异';return}if(st.estimated_cost_usd===null){output.textContent='已输入账单扣费；尚无参考单价估算，无法计算倍率。';return}const expected=Number(st.estimated_cost_usd);const delta=Number(actual)-expected;output.textContent=`参考估算 ${money(expected)}，实际 ${money(actual)}，差额 ${delta>=0?'+':''}${money(delta)}。差额可能来自价格、缓存、额外费用或计算口径。`});
}
async function loadHistory(){const target=$('historyRows');target.innerHTML='<div class="empty-state">正在加载…</div>';try{const r=await api('/api/reports');if(!r.reports.length){target.innerHTML='<div class="empty-state">还没有检测记录，先完成一次标准检测。</div>';return}
 target.innerHTML=r.reports.map(x=>`<button type="button" class="history-row" data-report="${escapeHTML(x.id)}"><span><strong>${escapeHTML(x.label||x.model)}</strong><small>${escapeHTML(x.model)}</small></span><span>${escapeHTML(x.mode)}</span><span>${escapeHTML(x.created_at.slice(0,16).replace('T',' '))} UTC</span><span>${statusBadge(x.status)}</span></button>`).join('');
 target.querySelectorAll('[data-report]').forEach(b=>b.addEventListener('click',async()=>{try{const report=await api('/api/reports/'+encodeURIComponent(b.dataset.report));renderReport(report,$('historyReportBody'));show($('historyDetail'));$('historyDetail').scrollIntoView({behavior:'smooth'})}catch(err){alert(err.message)}}))
 }catch(err){target.textContent=err.message}}
initialize();
