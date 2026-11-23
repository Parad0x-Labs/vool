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
.vd-hint,.vd-person-facts,.vd-my-number,.vd-card-says{opacity:.8;font-size:.92em}.vd-heading{margin:14px 0 0;padding-top:12px;border-top:1px solid var(--border,#343434)}
.vd-person{padding:8px 0;display:flex;flex-direction:column;gap:4px}.vd-person-name{font-weight:600}.vd-unverified{color:var(--warn,#b38a30);opacity:1}
.vd-advanced summary{cursor:pointer}.vd-file-state{font-weight:600}
.vd-warning{position:sticky;top:0;z-index:1;background:var(--panel,#202020);border:1px solid var(--warn,#b38a30);padding:10px;border-radius:8px}.vd-status{min-height:22px}
</style><script>
(function(){
if(window.VoolDrop)return;
function t(k,f){if(typeof VOOLT!=='function')return f;var v=VOOLT(k);return v&&v!==k?v:f;}
function fmt(s,values){return s.replace(/\{(\w+)\}/g,function(m,k){return values&&values[k]!==undefined?String(values[k]):m;});}
function el(tag,text){var n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;}
function size(n){if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(n<10240?1:0)+' KB';return (n/1048576).toFixed(1)+' MB';}
var ERRORS={
  DROP_DISABLED:['drop.off','Drop is off.'],
  IDENTITY_MISMATCH:['drop.pin_error','Check the fingerprint with the other person before connecting.'],
  SELF_SESSION:['drop.pin_error','Check the fingerprint with the other person before connecting.'],
  FINGERPRINT_CONFIRMATION_REQUIRED:['drop.pin_error','Check the fingerprint with the other person before connecting.'],
  INVALID_PEER:['drop.pin_error','Check the fingerprint with the other person before connecting.'],
  INVALID_BASE64:['drop.pin_error','Check the fingerprint with the other person before connecting.'],
  INPUT_LIMIT:['drop.limit_error','Choose one file of at most 10 MiB.'],
  RELAY_REQUIRED:['drop.err.folder_required','Choose the shared folder first.'],
  MISSING_RELAY:['drop.err.folder_missing','This folder does not exist. Choose a folder that is already there.'],
  RELAY_NOT_FOLDER:['drop.err.folder_not_folder','That is not a real folder. Choose a folder.'],
  RELAY_NOT_YOURS:['drop.err.folder_not_yours','That folder belongs to another user of this computer. Choose a folder of your own.'],
  RELAY_READ_ONLY:['drop.err.folder_read_only','VOOL cannot write in that folder. Choose another one.'],
  DIRECTORY_PERMISSIONS:['drop.err.home','The folder VOOL keeps its own data in is not private to you, so Drop will not use it.'],
  UNSAFE_HOME:['drop.err.home','The folder VOOL keeps its own data in is not private to you, so Drop will not use it.'],
  INPUT_UNAVAILABLE:['drop.err.invite_not_here','That invite is not in the shared folder yet. Wait for the folder to sync, then try again.'],
  INVALID_INVITE_LINK:['drop.err.invite_link','That is not an invite link. Copy it again from the other VOOL.'],
  PACKET_CONFLICT:['drop.err.one_file','This Drop already carries a file. To send another, choose the person and press Send file.'],
  NOTE_LIMIT:['drop.err.note','The note is too long. Shorten it and try again.'],
  INVALID_FILENAME:['drop.err.filename','That file name cannot be sent. Rename the file and try again.'],
  EXPIRED:['drop.err.expired','This has expired. Ask for a new one.'],
  STORE_LIMIT:['drop.err.full','Too many invites or Drops are open. Delete Drops you no longer need, then try again.'],
  INVALID_CARD:['drop.err.card','That is not a VOOL card. Ask the person to copy their card again.'],
  SELF_CARD:['drop.err.self_card','That is your own card.'],
  CONTACT_EXISTS:['drop.err.contact_exists','You already have this person.'],
  CONTACT_LIMIT:['drop.err.full','Too many invites or Drops are open. Delete Drops you no longer need, then try again.'],
  NAME_TAKEN:['drop.err.name_taken','Stop: you already have someone with this name, and this card is a different VOOL. If they really changed computers, check with them on a call, remove the old one, then add this card. Otherwise use another name.'],
  NAME_LOOKALIKE:['drop.err.name_lookalike','Stop: this name looks like one you already have, and this card is a different VOOL. Use a name that cannot be mixed up.'],
  TAG_COLLISION:['drop.err.tag','Stop: this card was made to look like a person you already have. Do not use it.'],
  INVALID_NAME:['drop.err.name','Type a name of up to 40 characters.'],
  CONTACT_NOT_READY:['drop.err.not_ready','This person has not added your card yet, or the shared folder has not synced. Send them your card, then try again.'],
  CONTACT_BUSY:['drop.err.busy','This person has not opened VOOL since your last files. You can send more after their VOOL has been open once.'],
  UNKNOWN_CONTACT:['drop.err.unknown_contact','That person is not in your list.'],
  FILE_NOT_READY:['drop.err.not_arrived','The file has not fully arrived yet.'],
  SAVE_FAILED:['drop.err.save','The file could not be saved to Downloads.'],
  SAVE_FOLDER_MISSING:['drop.err.save','The file could not be saved to Downloads.'],
  SAVED_FILE_MOVED:['drop.err.moved','The saved file is no longer in Downloads.'],
  LOCAL_COPY_DAMAGED:['drop.err.damaged','The copy on this computer is damaged. Ask the person to send it again.'],
  FILE_INTEGRITY:['drop.err.damaged','The copy on this computer is damaged. Ask the person to send it again.'],
  VAULT_KEY_UNAVAILABLE:['drop.err.key','VOOL could not read the key that protects Drop on this computer. Unlock your keychain and try again.'],
  VAULT_KEY_CONFLICT:['drop.err.key','VOOL could not read the key that protects Drop on this computer. Unlock your keychain and try again.'],
  TASK_NOT_APPROVABLE:['drop.err.check','The file has to arrive fully before it can be checked.']
};
window.VoolDrop={mountInto:function(host){
  var root=el('div');root.className='vd-root';host.appendChild(root);
  function line(key,fallback,tag){var n=el(tag||'div',t(key,fallback));root.appendChild(n);return n;}
  function input(key,fallback,type){var label=el('label',t(key,fallback));var n=el('input');n.type=type||'text';if(type==='checkbox'){label.className='vd-toggle';label.prepend(n);}else label.appendChild(n);root.appendChild(label);return n;}
  function button(parent,key,fallback,run){var n=el('button',t(key,fallback));n.type='button';n.className='btn';parent.appendChild(n);n.onclick=async function(){n.disabled=true;try{await run();}catch(e){clear();status.textContent=errorText(e.code);if(!panel.hidden&&select.value)await show().catch(function(){});}finally{n.disabled=false;}};return n;}
  function errorText(code){var words=ERRORS[code];return words?t(words[0],words[1]):t('drop.failed','This step could not finish. Check the settings and refresh the Drop before trying again.');}
  async function request(action,values){var r=await fetch('/api/drop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({action:action},values||{}))});var j=await r.json();if(!r.ok||!j.ok){var e=new Error();e.code=j.error;throw e;}return j;}
  line('drop.warning','Early test version. Do not use it for private messages yet.').className='vd-warning';
  line('drop.boundary','One file at a time, up to 10 MB. VOOL only checks that a file arrived unchanged, and never follows instructions written in what it receives.');
  var enabled=input('drop.enable','Enable Drop and make this VOOL folder private','checkbox');enabled.className='vd-enable';
  var relay=input('drop.relay','Shared folder');relay.className='vd-relay';
  line('drop.relay_hint','A folder that both of you have: a shared Dropbox, iCloud Drive or similar folder. Only locked files go into it.').className='vd-hint';
  var places=el('div');places.className='vd-controls vd-places';root.appendChild(places);
  var created=false;
  relay.addEventListener('input',function(){created=false;});
  function nativeApi(){try{var w=window.pywebview&&window.pywebview.api?window:(window.parent&&window.parent!==window&&window.parent.pywebview&&window.parent.pywebview.api?window.parent:null);return w&&w.pywebview.api.pick_folder?w.pywebview.api:null;}catch(e){return null;}}
  var pick=button(places,'drop.choose_folder','Choose folder',async function(){var api=nativeApi();if(!api)return;var res=await api.pick_folder();if(res&&res.ok&&res.path){relay.value=res.path;created=false;}});
  pick.className='btn vd-pick';pick.hidden=!nativeApi();
  window.addEventListener('pywebviewready',function(){pick.hidden=!nativeApi();});
  var allow=input('drop.allow','Allow approved file checks','checkbox');allow.className='vd-allow';
  var status=line('drop.off','Drop is off.');status.className='vd-status';status.setAttribute('role','status');
  var controls=el('div');controls.className='vd-controls';root.appendChild(controls);
  button(controls,'notif.save','Save',async function(){clear();panel.hidden=true;var values={enabled:enabled.checked,relay:relay.value.trim(),allow_digest:allow.checked};if(created)values.create=true;await request('settings',values);await load();status.textContent=t('radar.saved','Saved.');});
  var panel=el('div');panel.className='vd-root vd-panel';panel.hidden=true;root.appendChild(panel);
  function section(key,fallback){var h=el('h4',t(key,fallback));h.className='vd-heading';panel.appendChild(h);return h;}
  function fieldIn(parent,key,fallback,type){var label=el('label',t(key,fallback));var n=el(type==='textarea'?'textarea':'input');if(type&&type!=='textarea')n.type=type;else if(!type)n.type='text';if(type==='checkbox'){label.className='vd-toggle';label.prepend(n);}else label.appendChild(n);parent.appendChild(label);return n;}
  function field(key,fallback,type){return fieldIn(panel,key,fallback,type);}
  function row(parent){var n=el('div');n.className='vd-controls';(parent||panel).appendChild(n);return n;}

  /* You: the name on your card, the card, and the number people check you by. */
  section('drop.you','You');
  var myName=field('drop.your_name','Your name');myName.className='vd-my-name';myName.maxLength=40;
  var myCard=field('drop.your_card','Your card');myCard.readOnly=true;myCard.className='vd-my-card';
  var cardHint=el('div',t('drop.card_hint','Your card is not a secret. Send it to a person any way you like so they can add you.'));cardHint.className='vd-hint';panel.appendChild(cardHint);
  var myNumber=el('div');myNumber.className='vd-my-number';panel.appendChild(myNumber);
  var youRow=row();
  button(youRow,'drop.save_name','Save name',async function(){showMe(await request('name',{name:myName.value}));status.textContent=t('radar.saved','Saved.');});
  button(youRow,'drop.copy_card','Copy card',async function(){myCard.select();try{await navigator.clipboard.writeText(myCard.value);}catch(e){document.execCommand('copy');}status.textContent=t('drop.card_copied','Copied.');});
  function showMe(me){myName.value=me.name;myCard.value=me.name?me.card:'';fingerprint.value=me.fingerprint;
    cardHint.textContent=me.name?t('drop.card_hint','Your card is not a secret. Send it to a person any way you like so they can add you.'):t('drop.card_first','Type your name and press Save name. Your card then appears here.');
    myNumber.textContent=t('drop.your_number','Your safety number')+': '+me.safety_number+' · '+me.tag;}

  /* People: add by card, see who is who, verify, remove. */
  section('drop.people','People');
  var theirCard=field('drop.their_card','Their card');theirCard.className='vd-their-card';
  var cardSays=el('div');cardSays.className='vd-card-says';cardSays.setAttribute('role','status');panel.appendChild(cardSays);
  var theirName=field('drop.their_name','Name for this person');theirName.className='vd-their-name';theirName.maxLength=40;
  var compared=field('drop.compared','We compared this safety number on a call or in person.','checkbox');compared.className='vd-compared';
  var previewRun=0;
  theirCard.addEventListener('input',function(){compared.checked=false;cardSays.textContent='';var mine=++previewRun,value=theirCard.value.trim();if(!value)return;
    request('card',{card:value}).then(function(j){if(mine!==previewRun)return;if(!theirName.value)theirName.value=j.known_as||j.suggested||'';
      cardSays.textContent=fmt(t('drop.card_says','This card: {tag}. Safety number: {number}'),{tag:j.tag,number:j.safety_number});
    }).catch(function(e){if(mine===previewRun)cardSays.textContent=errorText(e.code);});});
  var peopleRow=row();
  button(peopleRow,'drop.add_person','Add person',async function(){await request('contact_add',{card:theirCard.value,name:theirName.value,verified:compared.checked});theirCard.value='';theirName.value='';compared.checked=false;cardSays.textContent='';await people();status.textContent=t('radar.saved','Saved.');});
  var list=el('div');list.className='vd-people';panel.appendChild(list);
  var known=[];
  async function people(){
    var j=await request('contacts');known=j.contacts||[];list.textContent='';var prior=to.value;to.textContent='';
    if(!known.length)list.appendChild(el('div',t('drop.no_people','Nobody yet. Send your card to a person and add theirs.')));
    known.forEach(function(p){
      var item=el('div');item.className='vd-person';item.dataset.fingerprint=p.fingerprint;
      var who=el('div',p.name+' · '+p.tag);who.className='vd-person-name';item.appendChild(who);
      var facts=el('div',(p.verified?t('drop.verified','Verified'):t('drop.not_verified','Not verified'))+' · '+(p.ready?t('drop.person_ready','Ready'):t('drop.person_waiting','Has not added your card yet'))+' · '+t('drop.number_of','Safety number')+': '+p.safety_number);
      facts.className='vd-person-facts'+(p.verified?'':' vd-unverified');item.appendChild(facts);
      var acts=row(item);
      button(acts,p.verified?'drop.unverify':'drop.mark_verified',p.verified?'Mark as not verified':'We compared the number: mark as verified',async function(){await request('contact_verify',{fingerprint:p.fingerprint,verified:!p.verified});await people();});
      var armed=false;var remove=button(acts,'drop.remove_person','Remove',async function(){
        if(!armed){armed=true;remove.textContent=fmt(t('drop.remove_again','Press again to remove {name} and delete their Drops'),{name:p.name});return;}
        try{await request('contact_remove',{fingerprint:p.fingerprint});}finally{await people();await refresh();}});
      list.appendChild(item);
      var o=el('option',p.name+' · '+p.tag+(p.verified?'':' ('+t('drop.not_verified','Not verified')+')'));o.value=p.fingerprint;to.appendChild(o);
    });
    if(Array.from(to.options).some(function(o){return o.value===prior;}))to.value=prior;
    sendTo.hidden=!known.length;
  }

  /* Send to a person: no invite, no fingerprint typing. */
  var sendTo=el('div');sendTo.className='vd-root vd-send-to';panel.appendChild(sendTo);
  var toLabel=el('label',t('drop.send_to','Send to'));var to=el('select');to.className='vd-to';toLabel.appendChild(to);sendTo.appendChild(toLabel);
  var toNote=fieldIn(sendTo,'notif.row.note','Note','textarea');toNote.className='vd-to-note';toNote.maxLength=4096;
  var toFile=fieldIn(sendTo,'drop.file','Select one file','file');toFile.className='vd-to-file';
  button(row(sendTo),'drop.send_file','Send file',async function(){
    if(!to.value||toFile.files.length!==1)throw {code:'INPUT_LIMIT'};
    var chosen=toFile.files[0];if(chosen.size>10485760)throw {code:'INPUT_LIMIT'};
    var name=to.options[to.selectedIndex].textContent;
    var r=await fetch('/api/drop/send',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Drop-Contact':to.value,'X-Drop-Name':encodeURIComponent(chosen.name),'X-Drop-Note':encodeURIComponent(toNote.value)},body:chosen});
    var j=await r.json();if(!r.ok||!j.ok)throw {code:j.error};toFile.value='';toNote.value='';await refresh();if(j.sid){select.value=j.sid;await show();}
    status.textContent=fmt(t('drop.sent_to','Sent to {name}. It arrives when their VOOL is open.'),{name:name});
  });

  /* Drops: what came in and what went out. */
  section('drop.drops','Drops');
  var poll=el('div');poll.className='vd-controls';panel.appendChild(poll);
  button(poll,'drop.poll','Check for Drops',async function(){await request('poll');await people();await refresh();});
  var auto=el('div',t('drop.checking','VOOL also checks by itself while it is open.'));auto.className='vd-hint';panel.appendChild(auto);
  var select=el('select');select.className='vd-select';select.setAttribute('aria-label',t('drop.choose','Choose a Drop'));panel.appendChild(select);
  var unfinished=el('div');unfinished.className='vd-unfinished';unfinished.setAttribute('role','status');panel.appendChild(unfinished);
  var who=el('div');who.className='vd-who';panel.appendChild(who);
  var expiry=el('div');expiry.className='vd-expiry';panel.appendChild(expiry);
  var note=el('pre');note.className='vd-note';panel.appendChild(note);
  var fileInfo=el('pre');fileInfo.className='vd-file-info';panel.appendChild(fileInfo);
  var fileState=el('div');fileState.className='vd-file-state';fileState.setAttribute('role','status');panel.appendChild(fileState);
  var fileRow=row();
  var saveButton=button(fileRow,'drop.save_file','Save to Downloads',async function(){var sid=select.value;var j=await request('save',{sid:sid});await show();status.textContent=fmt(t('drop.saved_as','Saved to Downloads as {name}'),{name:j.saved});});
  saveButton.className='btn vd-save';
  var revealButton=button(fileRow,'drop.show_file','Show in folder',async function(){await request('reveal',{sid:select.value});});
  revealButton.className='btn vd-reveal';
  var taskState=el('div');taskState.className='vd-task-state';panel.appendChild(taskState);
  var result=el('output');result.className='vd-result';panel.appendChild(result);
  var draft=field('notif.row.note','Note','textarea');draft.className='vd-draft';draft.maxLength=4096;
  var file=field('drop.file','Select one file','file');file.className='vd-file';
  var draftLabel=draft.parentNode,fileLabel=file.parentNode;
  var actions=el('div');actions.className='vd-controls';panel.appendChild(actions);
  var shown=null,expiryTimer=null,generation=0,seen='';
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
  button(actions,'dialog.delete','Delete',async function(){clear();try{await request('delete',{sid:select.value});}finally{await refresh();}});

  /* The one-time invite: for a single Drop with someone you do not keep in your list. */
  var advanced=el('details');advanced.className='vd-advanced';panel.appendChild(advanced);
  advanced.appendChild(el('summary',t('drop.advanced','One-time invite, without adding the person')));
  var adv=el('div');adv.className='vd-root';advanced.appendChild(adv);
  var fingerprint=fieldIn(adv,'drop.fingerprint','Your fingerprint');fingerprint.readOnly=true;fingerprint.className='vd-fingerprint';
  var peer=fieldIn(adv,'drop.peer','Their fingerprint');peer.className='vd-peer';
  var checked=fieldIn(adv,'drop.check','I checked this fingerprint with the other person in person or on a call.','checkbox');checked.className='vd-confirm';
  peer.addEventListener('input',function(){checked.checked=false;});
  var link=fieldIn(adv,'drop.link','Invite link');link.className='vd-link';
  var pairButtons=row(adv);
  button(pairButtons,'drop.invite','Create invite',async function(){link.value=(await request('invite',{fingerprint:peer.value,confirmed:checked.checked})).link;status.textContent=t('radar.saved','Saved.');});
  button(pairButtons,'notif.join','Join',async function(){await request('join',{fingerprint:peer.value,confirmed:checked.checked,link:link.value});await refresh();});

  function clear(){generation++;if(expiryTimer)clearTimeout(expiryTimer);expiryTimer=null;shown=null;unfinished.textContent='';who.textContent='';note.textContent='';fileInfo.textContent='';fileState.textContent='';expiry.textContent='';taskState.textContent='';result.textContent='';approve.hidden=true;decline.hidden=true;sendButton.hidden=true;taskButton.hidden=true;saveButton.hidden=true;revealButton.hidden=true;draftLabel.hidden=true;fileLabel.hidden=true;}
  function personText(p,sent){var name=p&&p.known?p.name+' · '+p.tag:t('drop.unknown_person','Someone not in your list')+(p?' · '+p.tag:'');return name;}
  function stateText(f){
    if(!f)return '';
    if(f.state==='arriving')return fmt(t('drop.state_arriving','Arriving: {have} of {total} pieces. It is not complete yet.'),{have:f.have,total:f.total});
    if(f.state==='ready')return f.saved?fmt(t('drop.saved_as','Saved to Downloads as {name}'),{name:f.saved}):t('drop.state_ready','Arrived. Press Save to Downloads to keep it.');
    if(f.state==='sent')return t('drop.state_sent','Sent. Not received yet.');
    if(f.state==='delivered')return t('drop.state_delivered','Delivered.');
    if(f.state==='damaged')return t('drop.state_damaged','Did not arrive whole. Ask the person to send it again.');
    return '';
  }
  async function show(){
    clear();var version=generation,sid=select.value;if(!sid)return;
    var option=select.options[select.selectedIndex];
    // A Drop whose deletion did not finish keeps only Delete, which tries the deletion again.
    if(option&&option.dataset.status==='incomplete'){unfinished.textContent=t('drop.delete_unfinished','Deleting this Drop did not finish. Press Delete to try again.');return;}
    var j=await request('show',{sid:sid});
    if(version!==generation||select.value!==sid||panel.hidden||j.expires<=Date.now())return;shown=j;
    note.textContent=j.note;var manifest=j.manifest?JSON.parse(j.manifest):{};var f=j.file||{};
    who.textContent=personText(j.person);
    fileInfo.textContent=manifest.name?manifest.name+' · '+size(manifest.size):'';
    fileState.textContent=stateText(f);
    saveButton.hidden=f.state!=='ready';revealButton.hidden=!(f.state==='ready'&&f.saved);
    expiry.textContent=t('drop.expiry','Expires')+': '+new Date(j.expires).toLocaleString(document.documentElement.lang);
    result.textContent=j.result||'';
    var states={pending:['activity.ev.permission_required','Waiting for approval'],approved:['drop.approved','File check approved'],declined:['xact.state_declined','Declined'],refused:['drop.failed','This step could not finish. Check the settings and refresh the Drop before trying again.'],submitted:['drop.requested','File check requested'],complete:['run.complete','Complete']};
    var words=states[j.task_state];taskState.textContent=words?t(words[0],words[1]):'';
    approve.hidden=!['pending','approved'].includes(j.task_state);decline.hidden=approve.hidden;
    sendButton.hidden=Boolean(j.manifest);draftLabel.hidden=sendButton.hidden;fileLabel.hidden=sendButton.hidden;
    taskButton.hidden=!j.manifest||j.status!=='connected'||Boolean(j.task);
    expiryTimer=setTimeout(function(){clear();if(root.isConnected)refresh().catch(function(e){status.textContent=errorText(e.code);});},Math.max(0,j.expires-Date.now()));
  }
  select.onchange=function(){show().catch(function(e){status.textContent=errorText(e.code);});};
  function label(d){
    if(d.status==='incomplete')return t('drop.delete_unfinished','Deleting this Drop did not finish. Press Delete to try again.');
    var parts=[personText(d.person)];if(d.name)parts.push(d.name);
    if(d.created)parts.push(new Date(d.created).toLocaleString(document.documentElement.lang,{dateStyle:'medium',timeStyle:'short'}));
    var f=d.file||{};var words={arriving:t('drop.short_arriving','arriving'),ready:t('drop.short_ready','arrived'),sent:t('drop.short_sent','sent'),delivered:t('drop.short_delivered','delivered'),damaged:t('drop.short_damaged','failed')}[f.state];
    if(words)parts.push(words);return parts.join(' · ');
  }
  async function refresh(){
    clear();status.textContent='';var version=generation,prior=select.value;var j=await request('inbox');if(version!==generation||panel.hidden)return;select.textContent='';
    seen=JSON.stringify(j.drops||[]);
    (j.drops||[]).forEach(function(d){var o=el('option',label(d));o.value=d.sid;o.dataset.status=d.status;select.appendChild(o);});
    if(Array.from(select.options).some(function(o){return o.value===prior;}))select.value=prior;
    await show();
  }
  /* New Drops show up without a button. One request at a time, and nothing the person is
     typing or reading is touched unless the list really changed. */
  var ticking=false;
  setInterval(async function(){
    if(ticking||panel.hidden||!root.isConnected||document.hidden)return;ticking=true;
    try{var j=await request('inbox');if(JSON.stringify(j.drops||[])!==seen&&!panel.hidden){var note=status.textContent;await people();await refresh();status.textContent=note;}}catch(e){}finally{ticking=false;}
  },8000);
  async function load(){clear();panel.hidden=true;var version=generation,cfg=await request('status');if(version!==generation)return;enabled.checked=cfg.enabled;relay.value=cfg.relay;allow.checked=cfg.allow_digest;
    if(!cfg.enabled){request('places').then(function(j){Array.from(places.querySelectorAll('.vd-place')).forEach(function(n){n.remove();});(j.places||[]).forEach(function(place){var b=el('button',fmt(t('drop.use_place','Use {place}'),{place:place.label}));b.type='button';b.className='btn vd-place';b.onclick=function(){relay.value=place.path;created=true;};places.appendChild(b);});}).catch(function(){});}
    else Array.from(places.querySelectorAll('.vd-place')).forEach(function(n){n.remove();});
    if(cfg.enabled){var me=await request('me');if(version!==generation)return;showMe(me);panel.hidden=false;await people();await refresh();}else{select.textContent='';fingerprint.value='';status.textContent=t('drop.off','Drop is off.');}}
  load().catch(function(e){status.textContent=errorText(e.code);});
  return true;
}};
})();
</script>'''
