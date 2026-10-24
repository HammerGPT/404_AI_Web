import {$,api,show,escapeHTML as esc,download} from './api.js';
import {selectedProtocol} from './models.js';
let data=null;
let lastModel='';
let saving=false;
export function fingerprintButtonLabel(){return isFingerprint()&&$('fingerprintAction').value==='reference'?'采集可信指纹 ↗':'开始检测 ↗';}
function isFingerprint(){return document.querySelector('input[name="mode"]:checked').value==='fingerprint';}
function updateReferenceOptions(){
  if(!data)return;
  const model=$('model').value.trim(),protocol=selectedProtocol();
  const references=data.references.filter(r=>r.enabled&&r.protocol===protocol);
  const previous=$('fingerprintReference').value;
  $('fingerprintReference').innerHTML='<option value="">请选择对应声明模型的基线</option>'+references.map(r=>`<option value="${esc(r.id)}">${esc(r.name)} · ${esc(r.model)} · ${esc(r.source_host)}</option>`).join('');
  const preferred=model===lastModel&&references.some(r=>r.id===previous)?previous:references.find(r=>r.model===model)?.id||'';
  $('fingerprintReference').value=preferred;lastModel=model;
  $('fingerprintReferenceNote').textContent=references.length?'可为渠道别名选择对应的声明模型；基线与待测协议必须相同。':'当前协议尚无可用基线，请先从你认可的独立参考接口采集。';
}
export function updateFingerprintUI(){
  const enabled=isFingerprint(),capture=enabled&&$('fingerprintAction').value==='reference';
  show($('fingerprintFields'),enabled);show($('fingerprintCapture'),capture);show($('fingerprintVerify'),enabled&&!capture);
  $('fingerprintName').required=capture;$('fingerprintTrusted').required=capture;$('fingerprintReference').required=enabled&&!capture;
  if(data)$('fingerprintPlanNote').textContent=`本档 ${data.requests} 次顺序请求，输出上限合计 ${data.max_output_tokens.toLocaleString()} Token；实际费用按上游计费。行为对照含 ${data.behavior_probes} 类探针、每类 ${data.behavior_repeats} 次。`;
  if(enabled){$('distill').checked=false;$('distillAuth').checked=false;show($('authorizeLine'),false);}
  $('distill').disabled=enabled;
  if(!$('runBtn').disabled)$('runBtn').textContent=fingerprintButtonLabel();
  updateReferenceOptions();
}
export async function loadFingerprints(){
  data=await api('/api/fingerprints');
  $('fingerprintCount').textContent=`${data.requests} 次`;
  $('fingerprintRows').innerHTML=data.references.length?data.references.map(r=>`<div class="fingerprint-library-row"><div><strong>${esc(r.name)}</strong><small>${esc(r.model)} · ${esc(r.protocol)}</small><small>${esc(r.source_host)} · ${r.source_kind==='official'?'官方域名直连':'用户确认可信'} · ${esc(r.created_at.slice(0,10))}</small></div><span class="status-pill ${r.enabled?'pass':'unknown'}">${r.enabled?'启用':'停用'}</span><button type="button" class="text-action" data-export-fingerprint="${esc(r.id)}">导出</button><button type="button" class="text-action" data-toggle-fingerprint="${esc(r.id)}">${r.enabled?'停用':'启用'}</button></div>`).join(''):'<p class="field-tip">还没有参考指纹。请使用自己的官方直连或可信渠道建立基线，不会将 Key 发送给第三方检测站。</p>';
  updateFingerprintUI();
}
export function fingerprintPayload(){
  if(!isFingerprint())return {};
  if(!data)throw new Error('指纹配置尚未加载，请重试连接。');
  const action=$('fingerprintAction').value;
  if(action==='reference'){
    if(!$('fingerprintName').value.trim()||!$('fingerprintTrusted').checked)throw new Error('请填写基线名称，并确认参考来源可信。');
    return {fingerprint_action:action,fingerprint_name:$('fingerprintName').value.trim(),fingerprint_source:$('fingerprintSource').value,fingerprint_trusted:true};
  }
  if(!$('fingerprintReference').value)throw new Error('请先建立并选择可信指纹基线。');
  return {fingerprint_action:action,fingerprint_reference:$('fingerprintReference').value};
}
export function setupFingerprints(){
  document.querySelectorAll('input[name="mode"]').forEach(el=>el.addEventListener('change',updateFingerprintUI));
  $('fingerprintAction').addEventListener('change',updateFingerprintUI);
  window.addEventListener('model-selection-changed',updateReferenceOptions);
  $('newFingerprint').addEventListener('click',()=>window.dispatchEvent(new Event('fingerprint-capture')));
  $('fingerprintRows').addEventListener('click',async event=>{
    const exportButton=event.target.closest('[data-export-fingerprint]');const toggle=event.target.closest('[data-toggle-fingerprint]');
    if((!exportButton&&!toggle)||saving)return;saving=true;const button=exportButton||toggle;button.disabled=true;
    try{
      const id=exportButton?.dataset.exportFingerprint||toggle.dataset.toggleFingerprint;
      if(exportButton){const reference=await api('/api/fingerprints/'+encodeURIComponent(id));download(new Blob([JSON.stringify(reference,null,2)],{type:'application/json'}),`404-ai-${id}.json`);}
      else{const reference=data.references.find(r=>r.id===id);await api('/api/fingerprints/'+encodeURIComponent(id),{method:'PUT',body:JSON.stringify({enabled:!reference.enabled})});await loadFingerprints();}
      $('fingerprintLibraryNotice').textContent=exportButton?'基线已导出，不包含 Key。':'基线状态已保存。';
    }catch(error){$('fingerprintLibraryNotice').textContent=error.message;}
    finally{saving=false;button.disabled=false;}
  });
}
