import {$, api, escapeHTML as esc, show} from './api.js';
import {setBasicCatalog,syncReasoning,renderReasoningEditor,reasoningEditorPayload,setupBasics} from './basics.js';
let catalog = null;
let saving = false;

function options(values) {
  return Object.entries(values).map(([key,label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join('');
}
function protocolFor(modelId) {
  const preset = catalog.models.find(m => m.enabled && m.model_id === modelId);
  return preset?.protocol || catalog.prefix_rules.find(r => modelId.toLowerCase().startsWith(r.prefix))?.protocol || catalog.fallback_protocol;
}
export function selectedProtocol() {
  return $('protocol').value === 'auto' ? protocolFor($('model').value.trim()) : $('protocol').value;
}
function updateProtocolNote() {
  if (!catalog) return;
  const recommended = protocolFor($('model').value.trim());
  $('protocol').options[0].textContent = `跟随模型 · ${catalog.protocols[recommended]}`;
  $('protocolNote').textContent = `本次使用 ${catalog.protocols[selectedProtocol()]}；每次只检测一种协议，可手动调整。`;
}
function refreshSelection() {
  const model = $('model').value.trim();
  $('modelOptions').querySelectorAll('[data-select-model]').forEach(button => {
    const selected = button.dataset.selectModel === model;
    button.classList.toggle('selected', selected); button.setAttribute('aria-pressed', String(selected));
  });
  updateProtocolNote();
}
function selectModel(modelId) {
  $('model').value = modelId;
  $('protocol').value = 'auto';
  refreshSelection();
  syncReasoning(modelId,selectedProtocol(),true);
}
function icon(brand) {
  return `<img src="/static/assets/models/${esc(brand)}.svg" alt="" width="20" height="20">`;
}
function renderChoices() {
  const query = $('modelSearch').value.trim().toLowerCase();
  const enabled = catalog.models.filter(m => m.enabled && `${m.name} ${m.model_id}`.toLowerCase().includes(query));
  $('modelOptions').innerHTML = enabled.map(m => `<button type="button" class="model-choice" data-select-model="${esc(m.model_id)}" aria-pressed="false" title="${esc(m.model_id)}" aria-label="${esc(m.name)} · ${esc(m.model_id)}">${icon(m.brand)}<span><strong>${esc(m.name)}</strong></span></button>`).join('') || '<p class="field-tip">没有匹配的预置模型，可在下方填写自定义模型 ID。</p>';
  refreshSelection();
}
function renderSettings() {
  $('presetRows').innerHTML = catalog.models.map(m => `<div class="preset-row"><div class="preset-brand">${icon(m.brand)}<span><strong>${esc(m.name)}</strong><small>${esc(m.model_id)}</small></span></div><span>${esc(catalog.protocols[m.protocol])}</span><span>${m.sort_order}</span><span class="status-pill ${m.enabled?'pass':'unknown'}">${m.enabled?'启用':'停用'}</span><div><button type="button" class="text-action" data-edit-preset="${esc(m.id)}">编辑</button><button type="button" class="text-action" data-toggle-preset="${esc(m.id)}">${m.enabled?'停用':'启用'}</button></div></div>`).join('');
}
export async function loadCatalog() {
  const initial=catalog===null;
  catalog = await api('/api/catalog');
  const selected = $('protocol').value || 'auto';
  const editorBrand = $('presetBrand').value;
  const editorProtocol = $('presetProtocol').value;
  $('protocol').innerHTML = '<option value="auto">跟随模型</option>'+options(catalog.protocols);
  $('protocol').value = selected;
  $('presetBrand').innerHTML = options(catalog.brands);
  $('presetProtocol').innerHTML = options(catalog.protocols);
  if(editorBrand in catalog.brands) $('presetBrand').value = editorBrand;
  if(editorProtocol in catalog.protocols) $('presetProtocol').value = editorProtocol;
  if (!$('model').value) $('model').value = catalog.models.find(m => m.enabled)?.model_id || '';
  renderChoices(); renderSettings();
  setBasicCatalog(catalog);
  syncReasoning($('model').value.trim(),selectedProtocol(),initial);
}
function editPreset(id) {
  const model = catalog.models.find(m => m.id === id);
  $('presetId').value = model?.id || '';
  $('presetName').value = model?.name || '';
  $('presetModel').value = model?.model_id || '';
  $('presetBrand').value = model?.brand || 'custom';
  $('presetProtocol').value = model?.protocol || catalog.fallback_protocol;
  $('presetOrder').value = model?.sort_order ?? (Math.max(-10,...catalog.models.map(m => m.sort_order))+10);
  $('presetEnabled').checked = model?.enabled ?? true;
  renderReasoningEditor($('presetProtocol').value,model?.reasoning_options||['auto'],model?.reasoning_default||'auto');
  $('presetEditorTitle').textContent = model ? '编辑模型' : '新增模型';
  show($('presetEditor')); show($('presetError'),false);
  $('presetEditor').scrollIntoView({behavior:'smooth',block:'start'}); $('presetName').focus();
}
function modelPayload() {
  return {name:$('presetName').value.trim(),model_id:$('presetModel').value.trim(),
    brand:$('presetBrand').value,protocol:$('presetProtocol').value,
    sort_order:Number($('presetOrder').value),enabled:$('presetEnabled').checked,...reasoningEditorPayload()};
}
export function setupModels() {
  setupBasics(loadCatalog);
  $('modelSearch').addEventListener('input',renderChoices);
  $('modelOptions').addEventListener('click',e => {const b=e.target.closest('[data-select-model]');if(b)selectModel(b.dataset.selectModel);});
  $('model').addEventListener('change',() => {if(catalog)selectModel($('model').value.trim());});
  $('protocol').addEventListener('change',()=>{updateProtocolNote();syncReasoning($('model').value.trim(),selectedProtocol(),true);});
  $('newPreset').addEventListener('click',() => editPreset());
  $('cancelPreset').addEventListener('click',() => show($('presetEditor'),false));
  $('presetRows').addEventListener('click',async e => {
    const edit=e.target.closest('[data-edit-preset]');if(edit){editPreset(edit.dataset.editPreset);return;}
    const toggle=e.target.closest('[data-toggle-preset]');if(!toggle || saving)return;
    const model=catalog.models.find(m=>m.id===toggle.dataset.togglePreset);
    saving=true;toggle.disabled=true;
    try {await api('/api/catalog/'+model.id,{method:'PUT',body:JSON.stringify({...model,enabled:!model.enabled})});await loadCatalog();$('catalogNotice').textContent='模型配置已保存。';}
    catch(error){$('catalogNotice').textContent=error.message;}
    finally{saving=false;toggle.disabled=false;}
  });
  $('presetForm').addEventListener('submit',async e => {
    e.preventDefault();if(saving)return;saving=true;$('savePreset').disabled=true;show($('presetError'),false);
    try {const id=$('presetId').value;await api('/api/catalog'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(modelPayload())});await loadCatalog();show($('presetEditor'),false);$('catalogNotice').textContent='模型配置已保存，检测页选项已更新。';}
    catch(error){$('presetError').textContent=error.message;show($('presetError'));}
    finally{saving=false;$('savePreset').disabled=false;}
  });
  $('loadModels').addEventListener('click',async () => {
    if(!$('baseUrl').value.trim()||!$('baseUrl').checkValidity()){$('modelNote').textContent='请先填写有效的 API 地址。';$('baseUrl').focus();return;}
    if(!$('apiKey').value.trim()){$('modelNote').textContent='请先填写 API Key，再获取渠道模型。';$('apiKey').focus();return;}
    $('loadModels').disabled=true;$('modelNote').textContent='正在获取渠道模型列表…';
    try {const data=await api('/api/models',{method:'POST',body:JSON.stringify({base_url:$('baseUrl').value.trim(),key:$('apiKey').value.trim(),model:$('model').value.trim(),protocol:selectedProtocol()})});
      $('modelList').replaceChildren();for(const model of data.models){const option=document.createElement('option');option.value=model;$('modelList').append(option);}
      $('modelNote').textContent=data.note+'；可在模型 ID 输入框中选择或手动填写。';
    }catch(error){$('modelNote').textContent=error.message;}finally{$('loadModels').disabled=false;}
  });
}
