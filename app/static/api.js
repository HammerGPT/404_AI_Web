export const $ = id => document.getElementById(id);
export const show = (element, visible=true) => element.classList.toggle('hidden', !visible);
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let csrf = '';
export function setCsrf(value) { csrf = value; }
export async function api(path, options={}, format='json') {
  let response;
  try{
    response = await fetch(path, {
      credentials:'same-origin', cache:'no-store', ...options,
      headers:{...(options.body ? {'Content-Type':'application/json'} : {}),
        ...(csrf ? {'X-CSRF-Token':csrf} : {}), ...(options.headers || {})}
    });
  }catch{
    const error=new Error('暂时无法连接服务器，请恢复连接后重试。');error.isNetwork=true;
    window.dispatchEvent(new CustomEvent('network-error',{detail:{message:error.message}}));throw error;
  }
  if (!response.ok) {
    let data = {}; try { data = await response.json(); } catch {}
    if (response.status === 401 && path !== '/api/login') window.dispatchEvent(new Event('auth-required'));
    const error = new Error(typeof data.detail === 'string' ? data.detail : `请求失败：HTTP ${response.status}`);
    error.status = response.status;
    if(response.status>=500){error.isNetwork=true;window.dispatchEvent(new CustomEvent('network-error',{detail:{message:'服务器暂时不可用，当前登录未退出。'}}));}
    throw error;
  }
  window.dispatchEvent(new Event('network-restored'));
  if (format === 'text') return response.text();
  if (format === 'blob') return response.blob();
  try{return await response.json();}
  catch{const error=new Error('服务器返回格式异常，请重试。');error.isNetwork=true;window.dispatchEvent(new CustomEvent('network-error',{detail:{message:error.message}}));throw error;}
}
export function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
