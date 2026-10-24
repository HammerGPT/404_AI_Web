import {$,api,escapeHTML as esc,show} from './api.js';
let catalog=null;
let billingInitialized=false;
let billingDirty=false;
let customMultiplier=false;
const ratioLabel=value=>`${Number(value)}×`;

export function setBasicCatalog(data){
  catalog=data;
  if(!billingInitialized || !billingDirty){$('groupRatio').value=data.billing.default;customMultiplier=false;}
  else if(!data.billing.options.includes(Number($('groupRatio').value)))customMultiplier=true;
  billingInitialized=true;renderMultipliers();
  // A catalog refresh must not replace unfinished edits in the settings form.
  if($('billingForm').dataset.dirty!=='true'){
    $('billingOptionsInput').value=data.billing.options.join(', ');
    renderBillingDefault(data.billing.default);
  }
}
function renderMultipliers(){
  const value=Number($('groupRatio').value);
  $('multiplierOptions').innerHTML=catalog.billing.options.map(r=>`<button type="button" class="setting-choice ${!customMultiplier&&r===value?'selected':''}" data-multiplier="${r}" aria-pressed="${!customMultiplier&&r===value}">${ratioLabel(r)}${r===catalog.billing.default?'<small>默认</small>':''}</button>`).join('')+`<button type="button" class="setting-choice ${customMultiplier?'selected':''}" data-custom-multiplier aria-pressed="${customMultiplier}">自定义</button>`;
  show($('groupRatio'),customMultiplier);
}
export function syncReasoning(modelId,protocol,reset=false){
  const model=catalog.models.find(m=>m.enabled&&m.model_id===modelId&&m.protocol===protocol);
  const choices=model?.reasoning_options||['auto'];const previous=$('reasoning').value;
  $('reasoning').innerHTML=choices.map(v=>`<option value="${esc(v)}">${esc(catalog.reasoning_labels[v])}${v===model?.reasoning_default?'（预设）':''}</option>`).join('');
  $('reasoning').value=!reset&&choices.includes(previous)?previous:(model?.reasoning_default||'auto');
  updateReasoningMode();
}
function updateReasoningMode(){
  const mode=document.querySelector('input[name="mode"]:checked').value;
  $('reasoning').disabled=!['standard','deep'].includes(mode);
  $('reasoningScope').textContent=mode==='fingerprint'?'指纹使用固定对照配置':mode==='quick'?'快速档不含思考专项':'仅用于思考专项';
}
export function basicPayload(){
  const raw=$('groupRatio').value.trim();const value=Number(raw);
  if(raw===''||!Number.isFinite(value)||value<0||value>1000000)throw new Error('请填写有效的计费倍率（0～1000000）。');
  return {reasoning:$('reasoning').disabled?'auto':$('reasoning').value,billing_multiplier:value};
}
function editorChoices(){return [...$('presetReasoningOptions').querySelectorAll('input:checked')].map(el=>el.value);}
function renderReasoningDefault(value){
  const choices=editorChoices();
  $('presetReasoningDefault').innerHTML=choices.map(v=>`<option value="${esc(v)}">${esc(catalog.reasoning_labels[v])}</option>`).join('');
  $('presetReasoningDefault').value=choices.includes(value)?value:'auto';
}
export function renderReasoningEditor(protocol,choices=['auto'],selected='auto'){
  const allowed=catalog.reasoning_by_protocol[protocol]||['auto'];
  $('presetReasoningOptions').innerHTML=allowed.map(v=>`<label class="reasoning-option"><input type="checkbox" value="${esc(v)}" ${v==='auto'||choices.includes(v)?'checked':''} ${v==='auto'?'disabled':''}>${esc(catalog.reasoning_labels[v])}</label>`).join('');
  renderReasoningDefault(selected);
}
export function reasoningEditorPayload(){return {reasoning_options:editorChoices(),reasoning_default:$('presetReasoningDefault').value};}
function parseMultiplierList(){
  const parts=$('billingOptionsInput').value.trim().split(/[,，\s]+/).filter(Boolean);
  const values=parts.map(Number);
  if(!parts[0]||values.length>12||values.some(v=>!Number.isFinite(v)||v<0||v>1000000)||new Set(values).size!==values.length)throw new Error('请输入 1～12 个不重复的非负倍率，用逗号分隔，最大 1000000。');
  return values;
}
function renderBillingDefault(value){
  try{
    const values=parseMultiplierList();
    $('billingDefault').innerHTML='<option value="" disabled>请选择默认倍率</option>'+values.map(v=>`<option value="${v}">${ratioLabel(v)}</option>`).join('');
    $('billingDefault').value=values.includes(Number(value))&&value!==''?String(value):'';
  }catch{$('billingDefault').innerHTML='<option value="">请先填写有效的倍率列表</option>';}
}
export function setupBasics(onSaved){
  $('multiplierOptions').addEventListener('click',event=>{
    const preset=event.target.closest('[data-multiplier]');const custom=event.target.closest('[data-custom-multiplier]');
    if(!preset&&!custom)return;billingDirty=true;customMultiplier=!!custom;
    if(preset)$('groupRatio').value=preset.dataset.multiplier;
    renderMultipliers();if(custom)$('groupRatio').focus();
  });
  $('groupRatio').addEventListener('input',()=>{billingDirty=true;});
  document.querySelectorAll('input[name="mode"]').forEach(el=>el.addEventListener('change',updateReasoningMode));
  $('presetProtocol').addEventListener('change',()=>renderReasoningEditor($('presetProtocol').value,editorChoices(),$('presetReasoningDefault').value));
  $('presetReasoningOptions').addEventListener('change',()=>renderReasoningDefault($('presetReasoningDefault').value));
  $('billingForm').addEventListener('input',()=>{$('billingForm').dataset.dirty='true';});
  $('billingOptionsInput').addEventListener('change',()=>renderBillingDefault($('billingDefault').value));
  $('billingForm').addEventListener('submit',async event=>{
    event.preventDefault();$('saveBilling').disabled=true;show($('billingError'),false);
    try{
      const options=parseMultiplierList();const selected=$('billingDefault').value;
      if(selected===''||!options.includes(Number(selected)))throw new Error('请从常用倍率中选择默认值。');
      await api('/api/settings/billing',{method:'PUT',body:JSON.stringify({options,default:Number(selected)})});
      $('billingForm').dataset.dirty='false';await onSaved();$('billingNotice').textContent='倍率选项已保存；新的页面使用该默认值，当前手动选择保持不变。';
    }catch(error){$('billingError').textContent=error.message;show($('billingError'));}
    finally{$('saveBilling').disabled=false;}
  });
}
