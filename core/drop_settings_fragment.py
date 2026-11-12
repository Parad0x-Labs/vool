"""Local Drop controls in Settings. Received content is displayed only as text."""

from __future__ import annotations


def render_drop_settings_fragment() -> str:
    return r'''<style>
.vd-root{display:flex;flex-direction:column;gap:12px;max-width:800px;min-width:0}
.vd-root label{display:flex;flex-direction:column;gap:6px}.vd-root input,.vd-root textarea,.vd-root select{max-width:100%;min-width:0}
.vd-root label.vd-toggle{flex-direction:row;align-items:flex-start;gap:8px}.vd-toggle input{flex:none;margin:4px 0 0}
.vd-root textarea{min-height:76px}.vd-root input[type=text],.vd-root select,.vd-root textarea{width:100%;padding:8px;border:1px solid var(--border,#343434);border-radius:6px;background:var(--bg,#171717);color:var(--ink,#eee);font:inherit}
.vd-root input[readonly]{font-family:monospace}.vd-panel{padding-top:12px;border-top:1px solid var(--border,#343434)}
.vd-root pre,.vd-root output{white-space:pre-wrap;overflow-wrap:anywhere}.vd-controls{display:flex;flex-wrap:wrap;gap:8px}
.vd-warning{position:sticky;top:0;z-index:1;background:var(--panel,#202020);border:1px solid var(--warn,#b38a30);padding:10px;border-radius:8px}.vd-status{min-height:22px}
</style><script>
(function(){
if(window.VoolDrop)return;
function t(k,f){return typeof VOOLT==='function'?VOOLT(k):f;}
function el(tag,text){var n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
window.VoolDrop={mountInto:function(host){
  var root=el('div');root.className='vd-root';host.appendChild(root);
  function line(key,fallback,tag){var n=el(tag||'div',t(key,fallback));root.appendChild(n);return n;}
  function input(key,fallback,type){var label=el('label',t(key,fallback));var n=el('input');n.type=type||'text';if(type==='checkbox'){label.className='vd-toggle';label.prepend(n);}else label.appendChild(n);root.appendChild(label);return n;}
  function button(parent,key,fallback,run){var n=el('button',t(key,fallback));n.type='button';n.className='btn';parent.appendChild(n);n.onclick=async function(){n.disabled=true;try{await run();}catch(e){clear();status.textContent=errorText(e.code);if(!panel.hidden&&select.value)await show().catch(function(){});}finally{n.disabled=false;}};return n;}
  function errorText(code){
    if(code==='DROP_DISABLED')return t('drop.off','Drop is off.');
    if(['IDENTITY_MISMATCH','SELF_SESSION','FINGERPRINT_CONFIRMATION_REQUIRED','INVALID_PEER'].includes(code))return t('drop.pin_error','Check the fingerprint with the other person before connecting.');
    if(['RELAY_REQUIRED','MISSING_RELAY','DIRECTORY_PERMISSIONS'].includes(code))return t('drop.relay_error','Choose a private shared folder owned by you.');
    if(code==='INPUT_LIMIT')return t('drop.limit_error','Choose one file of at most 10 MiB.');
    return t('drop.failed','This step could not finish. Check the settings and refresh the Drop before trying again.');
  }
  async function request(action,values){var r=await fetch('/api/drop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({action:action},values||{}))});var j=await r.json();if(!r.ok||!j.ok){var e=new Error();e.code=j.error;throw e;}return j;}
  line('drop.warning','Not reviewed. Do not use for real messages.').className='vd-warning';
  line('drop.boundary','One file up to 10 MiB. Tasks only calculate its SHA-256. Received text is data, never instructions.');
  var enabled=input('drop.enable','Enable Drop and make this VOOL folder private','checkbox');enabled.className='vd-enable';
  var relay=input('drop.relay','Shared folder');relay.className='vd-relay';
  var allow=input('drop.allow','Allow approved file checks','checkbox');allow.className='vd-allow';
  var status=line('drop.off','Drop is off.');status.className='vd-status';status.setAttribute('role','status');
  var controls=el('div');controls.className='vd-controls';root.appendChild(controls);
  button(controls,'notif.save','Save',async function(){clear();panel.hidden=true;await request('settings',{enabled:enabled.checked,relay:relay.value,allow_digest:allow.checked});await load();status.textContent=t('radar.saved','Saved.');});
  var panel=el('div');panel.className='vd-root vd-panel';panel.hidden=true;root.appendChild(panel);
  function field(key,fallback,type){var label=el('label',t(key,fallback));var n=el(type==='textarea'?'textarea':'input');if(type&&type!=='textarea')n.type=type;else if(!type)n.type='text';if(type==='checkbox'){label.className='vd-toggle';label.prepend(n);}else label.appendChild(n);panel.appendChild(label);return n;}
  var fingerprint=field('drop.fingerprint','Your fingerprint');fingerprint.readOnly=true;fingerprint.className='vd-fingerprint';
  var peer=field('drop.peer','Their fingerprint');peer.className='vd-peer';
  var checked=field('drop.check','I checked this fingerprint with the other person in person or on a call.','checkbox');checked.className='vd-confirm';
  peer.addEventListener('input',function(){checked.checked=false;});
  var link=field('drop.link','Invite link');link.className='vd-link';
  var pairButtons=el('div');pairButtons.className='vd-controls';panel.appendChild(pairButtons);
  button(pairButtons,'drop.invite','Create invite',async function(){link.value=(await request('invite',{fingerprint:peer.value,confirmed:checked.checked})).link;status.textContent=t('radar.saved','Saved.');});
  button(pairButtons,'notif.join','Join',async function(){await request('join',{fingerprint:peer.value,confirmed:checked.checked,link:link.value});await refresh();});
  var poll=el('div');poll.className='vd-controls';panel.appendChild(poll);
  button(poll,'drop.poll','Check for Drops',async function(){await request('poll');await refresh();});
  var select=el('select');select.className='vd-select';select.setAttribute('aria-label',t('drop.choose','Choose a Drop'));panel.appendChild(select);
  var expiry=el('div');expiry.className='vd-expiry';panel.appendChild(expiry);
  var note=el('pre');note.className='vd-note';panel.appendChild(note);
  var fileInfo=el('pre');fileInfo.className='vd-file-info';panel.appendChild(fileInfo);
  var taskState=el('div');taskState.className='vd-task-state';panel.appendChild(taskState);
  var result=el('output');result.className='vd-result';panel.appendChild(result);
  var draft=field('notif.row.note','Note','textarea');draft.className='vd-draft';draft.maxLength=4096;
  var file=field('drop.file','Select one file','file');file.className='vd-file';
  var actions=el('div');actions.className='vd-controls';panel.appendChild(actions);
  var shown=null,expiryTimer=null,generation=0;
  var sendButton=button(actions,'composer.send','Send',async function(){
    if(!select.value||file.files.length!==1)throw {code:'INVALID_INPUT'};
    var chosen=file.files[0];if(chosen.size>10485760)throw {code:'INPUT_LIMIT'};
    var r=await fetch('/api/drop/send',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Drop-Session':select.value,'X-Drop-Name':encodeURIComponent(chosen.name),'X-Drop-Note':encodeURIComponent(draft.value)},body:chosen});
    var j=await r.json();if(!r.ok||!j.ok)throw {code:j.error};file.value='';draft.value='';await show();status.textContent=t('drop.sent','Sent to the shared folder. The other person must check for Drops.');
  });
  var taskButton=button(actions,'drop.task','Request file check',async function(){await request('task',{sid:select.value});await show();});
  var approve=button(actions,'drop.run','Approve and run file check',async function(){
    if(!shown)throw {code:'INVALID_INPUT'};var current=shown,task=JSON.parse(current.task);
    if(current.task_state==='pending')await request('approve',{sid:current.sid,task_id:task.task_id});
    await request('execute',{sid:current.sid});await show();
  });
  var decline=button(actions,'xact.decline','Decline',async function(){await request('decline',{sid:shown.sid,task_id:JSON.parse(shown.task).task_id});await show();});
  button(actions,'dialog.delete','Delete',async function(){clear();await request('delete',{sid:select.value});await refresh();});
  function clear(){generation++;if(expiryTimer)clearTimeout(expiryTimer);expiryTimer=null;shown=null;note.textContent='';fileInfo.textContent='';expiry.textContent='';taskState.textContent='';result.textContent='';approve.hidden=true;decline.hidden=true;sendButton.hidden=true;taskButton.hidden=true;}
  async function show(){
    clear();var version=generation,sid=select.value;if(!sid)return;var j=await request('show',{sid:sid});
    if(version!==generation||select.value!==sid||panel.hidden||j.expires<=Date.now())return;shown=j;
    note.textContent=j.note;var manifest=j.manifest?JSON.parse(j.manifest):{};
    fileInfo.textContent=manifest.name?manifest.name+' · '+manifest.size+' B\nSHA-256: '+manifest.sha256:'';
    expiry.textContent=t('drop.expiry','Expires')+': '+new Date(j.expires).toLocaleString(document.documentElement.lang);
    result.textContent=j.result||'';
    var states={pending:['activity.ev.permission_required','Waiting for approval'],approved:['drop.approved','File check approved'],declined:['xact.state_declined','Declined'],refused:['drop.failed','This step could not finish. Check the settings and refresh the Drop before trying again.'],submitted:['drop.requested','File check requested'],complete:['run.complete','Complete']};
    var words=states[j.task_state];taskState.textContent=words?t(words[0],words[1]):'';
    approve.hidden=!['pending','approved'].includes(j.task_state);decline.hidden=approve.hidden;
    sendButton.hidden=Boolean(j.manifest);taskButton.hidden=!j.manifest||j.status!=='connected'||Boolean(j.task);
    expiryTimer=setTimeout(function(){clear();if(root.isConnected)refresh().catch(function(e){status.textContent=errorText(e.code);});},Math.max(0,j.expires-Date.now()));
  }
  select.onchange=function(){show().catch(function(e){status.textContent=errorText(e.code);});};
  async function refresh(){
    clear();status.textContent='';var version=generation,prior=select.value;var j=await request('list');if(version!==generation||panel.hidden)return;select.textContent='';
    (j.drops||[]).filter(function(d){return !['expired','deleted','incomplete'].includes(d.status);}).forEach(function(d){var o=el('option',d.sid);o.value=d.sid;select.appendChild(o);});
    if(Array.from(select.options).some(function(o){return o.value===prior;}))select.value=prior;
    await show();
  }
  async function load(){clear();panel.hidden=true;var version=generation,cfg=await request('status');if(version!==generation)return;enabled.checked=cfg.enabled;relay.value=cfg.relay;allow.checked=cfg.allow_digest;
    if(cfg.enabled){var identity=await request('identity');if(version!==generation)return;fingerprint.value=identity.fingerprint;panel.hidden=false;await refresh();}else{select.textContent='';fingerprint.value='';status.textContent=t('drop.off','Drop is off.');}}
  load().catch(function(e){status.textContent=errorText(e.code);});
  return true;
}};
})();
</script>'''
