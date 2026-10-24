import {$,api,show} from './api.js';
let loaded=false;

export function clearAccountSecrets(){
  for(const id of ['currentPassword','newPassword','confirmPassword'])$(id).value='';
}
export async function loadAccount(){
  $('saveSessionDays').disabled=true;show($('accountLoadError'),false);
  try{
    const data=await api('/api/account');
    $('sessionDays').min=data.min_session_days;$('sessionDays').max=data.max_session_days;
    if($('sessionPolicyForm').dataset.dirty!=='true')$('sessionDays').value=data.session_days;
    for(const id of ['newPassword','confirmPassword']){$(id).minLength=data.min_password_length;$(id).maxLength=data.max_password_length;}
    $('currentPassword').maxLength=data.max_password_length;
    $('sessionPolicyHint').textContent=`当前保持 ${data.session_days} 天，可设置 ${data.min_session_days}～${data.max_session_days} 天。成功访问会自动续期，更新或重启不清除登录。`;
    loaded=true;$('saveSessionDays').disabled=false;
  }catch(error){$('accountLoadErrorText').textContent=error.message;show($('accountLoadError'));}
}
export function setupAccount(){
  $('retryAccount').addEventListener('click',loadAccount);
  $('sessionDays').addEventListener('input',()=>{$('sessionPolicyForm').dataset.dirty='true';});
  $('sessionPolicyForm').addEventListener('submit',async event=>{
    event.preventDefault();if(!loaded)return;$('saveSessionDays').disabled=true;show($('sessionPolicyError'),false);
    try{
      const data=await api('/api/account/session',{method:'PUT',body:JSON.stringify({session_days:Number($('sessionDays').value)})});
      $('sessionPolicyForm').dataset.dirty='false';$('sessionDays').value=data.session_days;
      $('sessionPolicyNotice').textContent=`已保存：登录保持 ${data.session_days} 天，当前会话已同步续期。`;
      await loadAccount();
    }catch(error){$('sessionPolicyError').textContent=error.message;show($('sessionPolicyError'));}
    finally{$('saveSessionDays').disabled=false;}
  });
  $('passwordChangeForm').addEventListener('submit',async event=>{
    event.preventDefault();show($('passwordChangeError'),false);
    if($('newPassword').value!==$('confirmPassword').value){$('passwordChangeError').textContent='两次新密码输入不一致。';show($('passwordChangeError'));return;}
    $('savePassword').disabled=true;
    try{
      const result=await api('/api/account/password',{method:'POST',body:JSON.stringify({
        current_password:$('currentPassword').value,new_password:$('newPassword').value,confirm_password:$('confirmPassword').value})});
      clearAccountSecrets();window.dispatchEvent(new CustomEvent('auth-required',{detail:{message:result.message}}));
    }catch(error){
      $('passwordChangeError').textContent=error.isNetwork?'连接异常，修改结果尚未确认。请恢复连接后重新验证登录，不要重复提交。':error.message;
      show($('passwordChangeError'));
    }finally{$('savePassword').disabled=false;}
  });
}
