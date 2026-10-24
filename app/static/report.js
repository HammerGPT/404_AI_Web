import {$, api, download, show} from './api.js';
let stream=null;
export function stopWatching(){if(stream){stream.close();stream=null;}}
function detailKey(detail){return detail.id||detail.dataset.detailKey;}
function updateTimers(root=document){
  const now=Date.now();
  root.querySelectorAll('[data-live-elapsed]').forEach(el=>{const start=Date.parse(el.dataset.liveElapsed);if(Number.isFinite(start))el.textContent=`总耗时 ${Math.max(0,Math.floor((now-start)/1000))} 秒`;});
  root.querySelectorAll('[data-case-elapsed]').forEach(el=>{const start=Date.parse(el.dataset.caseElapsed)+Number(el.dataset.caseOffset||0);if(Number.isFinite(start))el.textContent=`${Math.max(0,Math.floor((now-start)/1000))}s`;});
}
export function mountReport(target,html){
  const state=new Map([...target.querySelectorAll('details')].filter(detailKey).map(d=>[detailKey(d),d.open]));
  const focused=document.activeElement?.matches('summary')?document.activeElement.closest('details'):null;
  const focusKey=focused&&target.contains(focused)?detailKey(focused):null;
  target.innerHTML=html;
  target.querySelectorAll('details').forEach(d=>{const key=detailKey(d);if(state.has(key))d.open=state.get(key);if(key&&key===focusKey)d.querySelector('summary')?.focus({preventScroll:true});});
  updateTimers(target);
}
export async function showReport(id,target){mountReport(target,await api('/api/reports/'+encodeURIComponent(id)+'/view',{},'text'));}
export async function watchRun(id){
  stopWatching();
  sessionStorage.setItem('activeRunId',id);
  await showReport(id,$('runReport'));
  const initial=$('runReport').querySelector('.report');
  if(initial?.dataset.state!=='running'){window.dispatchEvent(new CustomEvent('run-state',{detail:{id,state:initial?.dataset.state}}));return;}
  stream=new EventSource('/api/runs/'+encodeURIComponent(id)+'/events');
  stream.addEventListener('snapshot',event=>{
    const update=JSON.parse(event.data);mountReport($('runReport'),update.html);show($('connectionNotice'),false);
    window.dispatchEvent(new CustomEvent('run-state',{detail:update}));
    if(update.state!=='running')stopWatching();
  });
  stream.addEventListener('expired',()=>{stopWatching();window.dispatchEvent(new Event('auth-required'));});
  stream.onerror=async()=>{
    $('connectionNotice').textContent='进度连接暂时断开，正在恢复查看；不会重新发送检测请求。';show($('connectionNotice'));
    try{await api('/api/me');}catch(error){if(error.status===401)stopWatching();}
  };
}
export function setupReports(){
  document.addEventListener('click',async event=>{
    const article=event.target.closest('.report');if(!article)return;
    const button=event.target.closest('button');if(!button)return;
    if(!button.matches('[data-download-pdf],[data-export-json],[data-copy-id]'))return;
    const feedback=article.querySelector('.report-feedback');feedback.textContent='';button.disabled=true;
    try{
      if(button.dataset.downloadPdf){
        feedback.textContent='正在排版 PDF，请稍候…';
        const id=button.dataset.downloadPdf;const blob=await api('/api/reports/'+encodeURIComponent(id)+'/pdf',{},'blob');
        download(blob,`404-ai-report-${id}.pdf`);feedback.textContent='PDF 已下载。';
      }else if(button.dataset.exportJson){
        const id=button.dataset.exportJson;const data=await api('/api/reports/'+encodeURIComponent(id));
        download(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),`404-ai-report-${id}.json`);
      }else if(button.dataset.copyId){await navigator.clipboard.writeText(button.dataset.copyId);feedback.textContent='报告 ID 已复制。';}
    }catch(error){feedback.textContent=error.message;}finally{button.disabled=false;}
  });
  document.addEventListener('input',event=>{
    if(!event.target.matches('[data-reconcile-cost]'))return;
    const input=event.target;const output=input.closest('.reconcile').querySelector('small');
    if(input.value===''){output.textContent='仅在当前页面对比，不自动读取或保存账单。';return;}
    if(input.dataset.reconcileCost===''){output.textContent='缺少估算费用，无法比较差额。';return;}
    const difference=Number(input.value)-Number(input.dataset.reconcileCost);
    output.textContent=`实际 $${Number(input.value).toFixed(6)}，估算 $${Number(input.dataset.reconcileCost).toFixed(6)}，差额 ${difference>=0?'+':''}$${difference.toFixed(6)}。`;
  });
  setInterval(()=>updateTimers(),1000);
}
