import {$,api,setCsrf,show,escapeHTML as esc} from './api.js';
import {setupModels,loadCatalog} from './models.js';
import {setupReports,showReport,watchRun,stopWatching} from './report.js';
import {setupHelp} from './help.js';
import {basicPayload} from './basics.js';
import {setupAccount,loadAccount,clearAccountSecrets} from './account.js';
import {setupFingerprints,loadFingerprints,fingerprintPayload,fingerprintButtonLabel,updateFingerprintUI} from './fingerprints.js';

let running=false;
let activeId=null;
let submitting=false;
let checkingSession=false;
let currentPage='test';
const titles={test:'渠道检测',run:'检测过程与报告',history:'历史报告',settings:'模型配置',account:'账户设置'};
function showLogin(message=''){
  stopWatching();setCsrf('');$('apiKey').value='';clearAccountSecrets();
  show($('sessionCheck'),false);show($('loginScreen'));show($('workspace'),false);show($('networkNotice'),false);
  $('loginStatus').textContent=message;show($('loginStatus'),!!message);
}
function setPage(name){
  currentPage=name;
  for(const key of Object.keys(titles))show($(key+'Panel'),key===name);
  $('navTest').classList.toggle('active',name==='test'||name==='run');
  $('navHistory').classList.toggle('active',name==='history');
  $('navSettings').classList.toggle('active',name==='settings');
  $('navAccount').classList.toggle('active',name==='account');
  $('crumb').textContent=titles[name];
}
async function openRun(id){activeId=id;setPage('run');await watchRun(id);window.scrollTo({top:0});}
async function recoverRun(){
  const pending=sessionStorage.getItem('pendingRequest');
  if(pending){
    try{const found=await api('/api/runs/by-request/'+encodeURIComponent(pending));sessionStorage.removeItem('pendingRequest');await openRun(found.id);return;}
    catch(error){if(error.status!==404)throw error;sessionStorage.removeItem('pendingRequest');}
  }
  const current=await api('/api/runs/current');
  const id=current.id||sessionStorage.getItem('activeRunId');
  if(id){try{await openRun(id);}catch(error){if(error.status!==404)throw error;sessionStorage.removeItem('activeRunId');}}
}
async function enterWorkspace(){
  show($('sessionCheck'),false);show($('loginScreen'),false);show($('workspace'));setPage('test');
  try{await loadCatalog();await loadFingerprints();await recoverRun();$('runBtn').disabled=running||submitting;}
  catch(error){$('formError').textContent=error.message;show($('formError'));}
}
async function initialize(){
  if(checkingSession)return;checkingSession=true;
  show($('sessionCheck'));show($('loginScreen'),false);show($('workspace'),false);show($('retrySession'),false);
  $('sessionCheckMessage').textContent='正在恢复登录…';
  try{const me=await api('/api/me');setCsrf(me.csrf);await enterWorkspace();}
  catch(error){
    if(error.status===401)showLogin();
    else{$('sessionCheckMessage').textContent='连接暂时不可用，尚未确认登录状态。恢复连接后请重试。';show($('retrySession'));}
  }finally{checkingSession=false;}
}
function networkNotice(message){$('networkNoticeText').textContent=message;show($('networkNotice'));}
async function retryConnection(){
  $('retryConnection').disabled=true;
  try{
    const me=await api('/api/me');setCsrf(me.csrf);
    await loadCatalog();
    await loadFingerprints();
    if(currentPage==='account')await loadAccount();
    if(sessionStorage.getItem('pendingRequest'))await recoverRun();
    $('runBtn').disabled=running||submitting;show($('networkNotice'),false);
  }catch(error){if(error.status!==401)networkNotice(error.message);}
  finally{$('retryConnection').disabled=false;}
}
window.addEventListener('auth-required',event=>showLogin(event.detail?.message||(!$('workspace').classList.contains('hidden')?'登录已失效，请重新登录。':'')));
window.addEventListener('network-error',event=>{if(!$('workspace').classList.contains('hidden'))networkNotice(event.detail.message);});
window.addEventListener('network-restored',()=>show($('networkNotice'),false));
$('retrySession').addEventListener('click',initialize);
$('retryConnection').addEventListener('click',retryConnection);
window.addEventListener('online',()=>{if(!$('sessionCheck').classList.contains('hidden'))initialize();else if(!$('networkNotice').classList.contains('hidden'))retryConnection();});
window.addEventListener('run-state',event=>{
  running=event.detail.state==='running';activeId=event.detail.id;
  $('runBtn').disabled=running||submitting;$('newTest').disabled=running;
  if(!running)loadFingerprints().catch(()=>{});
});
window.addEventListener('fingerprint-capture',()=>{
  if(running){networkNotice('请先完成当前检测，再建立参考指纹。');return;}
  activeId=null;sessionStorage.removeItem('activeRunId');setPage('test');
  $('baseUrl').value='';$('apiKey').value='';$('fingerprintTrusted').checked=false;
  const mode=document.querySelector('input[name="mode"][value="fingerprint"]');mode.checked=true;mode.dispatchEvent(new Event('change'));
  $('fingerprintAction').value='reference';updateFingerprintUI();$('baseUrl').focus();window.scrollTo({top:0});
});
$('loginForm').addEventListener('submit',async event=>{
  event.preventDefault();show($('loginError'),false);$('loginBtn').disabled=true;
  try{const data=await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});setCsrf(data.csrf);$('password').value='';await enterWorkspace();}
  catch(error){$('loginError').textContent=error.message;show($('loginError'));}
  finally{$('loginBtn').disabled=false;}
});
$('logoutBtn').addEventListener('click',async()=>{try{await api('/api/logout',{method:'POST',body:'{}'});showLogin();}catch(error){if(error.status!==401)networkNotice('退出请求未完成，请恢复连接后再次退出。');}});
$('navTest').addEventListener('click',()=>setPage(activeId?'run':'test'));
$('navHistory').addEventListener('click',()=>{setPage('history');loadHistory();});
$('navSettings').addEventListener('click',()=>setPage('settings'));
$('navAccount').addEventListener('click',()=>{setPage('account');loadAccount();});
$('newTest').addEventListener('click',()=>{if(running)return;activeId=null;sessionStorage.removeItem('activeRunId');show($('formError'),false);setPage('test');$('fingerprintAction').value='verify';$('fingerprintTrusted').checked=false;updateFingerprintUI();window.scrollTo({top:0});});
$('refreshHistory').addEventListener('click',loadHistory);
$('closeHistoryDetail').addEventListener('click',()=>show($('historyDetail'),false));
$('toggleKey').addEventListener('click',()=>{$('apiKey').type=$('apiKey').type==='password'?'text':'password';});
$('advancedToggle').addEventListener('click',()=>{const expanded=$('advancedFields').classList.contains('hidden');show($('advancedFields'),expanded);$('advancedArrow').textContent=expanded?'−':'＋';$('advancedToggle').setAttribute('aria-expanded',String(expanded));});
$('distill').addEventListener('change',()=>show($('authorizeLine'),$('distill').checked));
show($('authorizeLine'),false);

function numberOrUndefined(id){const value=$(id).value.trim();return value===''?undefined:Number(value);}
function createConfig(){
  let custom={};const raw=$('customHeaders').value.trim();
  if(raw){try{custom=JSON.parse(raw);}catch{throw new Error('自定义 Header 需要填写合法 JSON');}if(typeof custom!=='object'||Array.isArray(custom)||!custom)throw new Error('自定义 Header 必须是 JSON 对象');}
  const input=numberOrUndefined('inputRate'),output=numberOrUndefined('outputRate');let rates=null;
  if(input!==undefined||output!==undefined){
    if(input===undefined||output===undefined)throw new Error('估算费用需要同时填写输入和输出单价');
    rates={input,output};
    const read=numberOrUndefined('cacheReadRate'),write=numberOrUndefined('cacheWriteRate');
    if(read!==undefined)rates.cache_read=read;if(write!==undefined)rates.cache_write=write;
  }
  return {base_url:$('baseUrl').value.trim(),key:$('apiKey').value.trim(),model:$('model').value.trim(),
    mode:document.querySelector('input[name="mode"]:checked').value,protocol:$('protocol').value,
    label:$('groupLabel').value.trim(),...basicPayload(),token_field:$('tokenField').value,
    distill:$('distill').checked,authorized_distill:$('distillAuth').checked,custom_headers:custom,rates,...fingerprintPayload()};
}
$('testForm').addEventListener('submit',async event=>{
  event.preventDefault();if(submitting||running)return;show($('formError'),false);
  let cfg;
  try{
    cfg=createConfig();if(cfg.distill&&!cfg.authorized_distill)throw new Error('请确认已取得训练数据用途授权');
    if(cfg.distill&&!['standard','deep'].includes(cfg.mode))throw new Error('训练样例仅适用于标准或深度模式');
    submitting=true;$('runBtn').disabled=true;$('runBtn').textContent='正在启动…';
    const requestId=crypto.randomUUID();sessionStorage.setItem('pendingRequest',requestId);
    const result=await api('/api/runs',{method:'POST',headers:{'Idempotency-Key':requestId},body:JSON.stringify(cfg)});
    sessionStorage.removeItem('pendingRequest');running=result.run_state==='running';await openRun(result.id);
  }catch(error){
    if(error.status===409){try{const current=await api('/api/runs/current');if(current.id){await openRun(current.id);return;}}catch{}}
    $('formError').textContent=error.message;show($('formError'));
    if(!error.status||error.isNetwork){try{await recoverRun();}catch{$('formError').textContent='启动结果尚未确认，请刷新恢复或查看历史；不会自动重发检测请求。';}}
    else sessionStorage.removeItem('pendingRequest');
  }finally{
    if(cfg){cfg.key='';cfg.custom_headers={};}
    $('apiKey').value='';submitting=false;$('runBtn').disabled=running;$('newTest').disabled=running;$('runBtn').textContent=fingerprintButtonLabel();
  }
});
const stateLabels={running:'检测中',interrupted:'已中断',failed:'已中断'};
const resultLabels={pass:'已完成',attention:'需核查'};
const modeLabels={quick:'快速',standard:'标准',deep:'深度',fingerprint:'指纹验货'};
async function loadHistory(){
  const target=$('historyRows');target.innerHTML='<div class="empty-state">正在加载…</div>';
  try{
    const data=await api('/api/reports');
    target.innerHTML=data.reports.length?data.reports.map(item=>`<button type="button" class="history-row" data-report="${esc(item.id)}" data-state="${esc(item.run_state)}"><span><strong>${esc(item.label||item.model)}</strong><small>${esc(item.model)}</small></span><span>${modeLabels[item.mode]||esc(item.mode)}</span><span>${esc(new Date(item.created_at).toLocaleString('zh-CN',{hour12:false}))}</span><span class="status-pill ${esc(item.status)}">${stateLabels[item.run_state]||resultLabels[item.status]||'未验证'}</span></button>`).join(''):'<div class="empty-state">还没有检测记录。</div>';
  }catch(error){target.textContent=error.message;}
}
$('historyRows').addEventListener('click',async event=>{
  const button=event.target.closest('[data-report]');if(!button)return;button.disabled=true;
  try{
    if(button.dataset.state==='running'){await openRun(button.dataset.report);return;}
    await showReport(button.dataset.report,$('historyReportBody'));show($('historyDetail'));$('historyDetail').scrollIntoView({behavior:'smooth'});
  }catch(error){$('historyNotice').textContent=error.message;}finally{button.disabled=false;}
});
setupModels();setupReports();setupHelp();setupAccount();setupFingerprints();initialize();
