let nextId = 0;
function showHelp(help, visible) {
  const button=help.querySelector('.help-trigger'),content=help.querySelector('.help-content');
  if(!content.id)content.id='field-help-'+(++nextId);
  button.setAttribute('aria-describedby',content.id);
  button.setAttribute('aria-expanded',String(visible));help.classList.toggle('is-open',visible);
  if(visible){content.classList.remove('align-end');if(content.getBoundingClientRect().right>window.innerWidth-12)content.classList.add('align-end');}
}
function closeAll(){document.querySelectorAll('.help.is-open').forEach(help=>{help.dataset.pinned='';showHelp(help,false);});}
export function setupHelp(){
  document.addEventListener('pointerover',event=>{const help=event.target.closest('.help');if(help&&!help.contains(event.relatedTarget))showHelp(help,true);});
  document.addEventListener('pointerout',event=>{const help=event.target.closest('.help');if(help&&!help.contains(event.relatedTarget)&&!help.dataset.pinned&&!help.contains(document.activeElement))showHelp(help,false);});
  document.addEventListener('focusin',event=>{const help=event.target.closest('.help');if(help)showHelp(help,true);});
  document.addEventListener('focusout',event=>{const help=event.target.closest('.help');if(help&&!help.contains(event.relatedTarget)&&!help.dataset.pinned)showHelp(help,false);});
  document.addEventListener('click',event=>{
    const button=event.target.closest('.help-trigger');
    if(button){const help=button.closest('.help');const pinned=!!help.dataset.pinned;closeAll();help.dataset.pinned=pinned?'':'1';showHelp(help,!pinned);}
    else if(!event.target.closest('.help'))closeAll();
  });
  document.addEventListener('keydown',event=>{if(event.key==='Escape')closeAll();});
}
