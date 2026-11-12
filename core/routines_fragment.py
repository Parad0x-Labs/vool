"""My routines: teach VOOL a task once in one folder, look at the exact list, run it.

The page (/routines) and two small entries elsewhere: a Home menu link and command palette rows on
the chat page, and a row in Settings. Every value from the server is written as text, never markup.
Nothing on the page changes a file without the person choosing Run on the list they are looking at.
"""
from __future__ import annotations

import html
from base64 import b64encode
from pathlib import Path

CSS = r"""
#routinesSetting{padding:12px 18px;border:1px solid var(--border,#c7cfd8);border-radius:10px;margin:12px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
#routinesSetting p{margin:2px 0 0;opacity:.8;font-size:14px}#routinesSetting a{display:inline-flex;padding:8px 12px;border:1px solid var(--border,#c7cfd8);border-radius:8px;color:inherit;text-decoration:none}
#routinesSetting a:focus-visible,#routinesLink:focus-visible{outline:2px solid var(--accent,#b0812b);outline-offset:2px}#routinesLink{text-decoration:none;color:inherit}
.rt-page{--rt-bg:#f7f8f4;--rt-fg:#21332f;--rt-muted:#56685f;--rt-card:#fff;--rt-line:#c9d4c8;--rt-accent:#275846;--rt-accent-fg:#fff;--rt-warn:#7a4310;--rt-danger:#8c302e;--rt-soft:#edf2e9;--rt-banner:#fff6dc;--rt-focus:#b0812b;
font:16px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:32px 24px 64px;color:var(--rt-fg);background:var(--rt-bg);min-height:100vh;box-sizing:border-box}
@media (prefers-color-scheme:dark){.rt-page{--rt-bg:#141a18;--rt-fg:#e4ebe7;--rt-muted:#a6b5ae;--rt-card:#1c2421;--rt-line:#34423d;--rt-accent:#86c9ab;--rt-accent-fg:#0f1a15;--rt-warn:#f0b56a;--rt-danger:#f3a196;--rt-soft:#24302c;--rt-banner:#2c2a1b;--rt-focus:#e2b45c}}
.rt-page *{box-sizing:border-box}.rt-page h1{font-size:32px;letter-spacing:-.5px;margin:6px 0 4px}.rt-page h2{font-size:21px;margin:0 0 10px}.rt-page h3{font-size:17px;margin:16px 0 6px}.rt-page p{margin:6px 0}
.rt-page button,.rt-page .rt-link{font:inherit;background:var(--rt-card);color:var(--rt-accent);border:1px solid var(--rt-line);border-radius:9px;padding:8px 14px;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}
.rt-page button:hover{background:var(--rt-soft)}.rt-page button:disabled{opacity:.55;cursor:wait}.rt-page .rt-primary{background:var(--rt-accent);color:var(--rt-accent-fg);border-color:var(--rt-accent)}.rt-page .rt-primary:hover{filter:brightness(1.08);background:var(--rt-accent)}.rt-page .rt-danger{color:var(--rt-danger);border-color:var(--rt-danger)}
.rt-page button:focus-visible,.rt-page a:focus-visible,.rt-page input:focus-visible,.rt-page select:focus-visible,.rt-page summary:focus-visible{outline:3px solid var(--rt-focus);outline-offset:2px}.rt-page [tabindex="-1"]:focus{outline:none}
.rt-card{border:1px solid var(--rt-line);border-radius:16px;background:var(--rt-card);padding:20px 22px;margin:16px 0;min-width:0}.rt-panel{border-width:2px;border-color:var(--rt-accent)}
.rt-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.rt-small button{padding:5px 10px;font-size:14px}
.rt-muted{color:var(--rt-muted);font-size:14px}.rt-warn{color:var(--rt-warn)}.rt-status{min-height:26px;margin:8px 0;overflow-wrap:anywhere}.rt-status:not(:empty){background:var(--rt-soft);border-radius:10px;padding:9px 14px}.rt-status[data-error="true"]{color:var(--rt-danger);border:1px solid var(--rt-danger)}
.rt-banner{background:var(--rt-banner);border-radius:12px;padding:14px 16px}.rt-notes,.rt-ops{padding-inline-start:20px;margin:6px 0;overflow-wrap:anywhere}.rt-ops li{margin:4px 0}.rt-ops small{display:block}
.rt-folders{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}.rt-where{font-weight:600;overflow-wrap:anywhere}
.rt-form{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}.rt-form>label,.rt-field{display:flex;flex-direction:column;gap:4px;font-size:14px;min-width:0}.rt-wide{grid-column:1/-1}.rt-field>button{align-self:flex-start}.rt-page .rt-bar-end{align-items:flex-end}
.rt-page input,.rt-page select{font:inherit;border:1px solid var(--rt-line);border-radius:7px;padding:7px 9px;background:var(--rt-card);color:var(--rt-fg);max-width:100%}.rt-page input[type=checkbox]{width:auto}
.rt-pieces{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.rt-piece{display:inline-flex;align-items:center;gap:2px;border:1px solid var(--rt-line);border-radius:9px;padding:3px;background:var(--rt-soft)}.rt-piece input,.rt-piece select{border:0;background:transparent;padding:4px 6px}
.rt-page .rt-x{border:0;background:transparent;padding:2px 8px;font-size:18px;line-height:1;color:var(--rt-muted)}.rt-column{border-top:1px solid var(--rt-line);padding-top:8px;margin-top:8px}
.rt-page fieldset{border:1px solid var(--rt-line);border-radius:10px;padding:10px 14px;display:flex;flex-wrap:wrap;gap:10px;align-items:end}.rt-page legend{padding:0 6px;font-size:14px}.rt-page fieldset label{display:flex;flex-direction:column;gap:4px;font-size:14px}.rt-page fieldset small{flex-basis:100%}
.rt-page .rt-check{display:flex;flex-direction:row;gap:8px;align-items:center;font-size:15px}.rt-space{font-size:12px;color:var(--rt-muted);padding-inline-end:4px}.rt-routine{border-top:1px solid var(--rt-line);padding:12px 0}.rt-routine:first-of-type{border-top:0}.rt-routine h3{margin:0 0 4px}.rt-lines{margin:0;padding:0;list-style:none;font-size:15px;overflow-wrap:anywhere}
.rt-page pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.45 ui-monospace,Menlo,Consolas,monospace;background:var(--rt-soft);border-radius:9px;padding:12px;max-height:340px;overflow:auto}
.rt-run{border-top:1px solid var(--rt-line);padding:8px 0}.rt-run summary{cursor:pointer}.rt-versions{list-style:none;padding:0}.rt-versions li{display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:6px 0;border-top:1px solid var(--rt-line)}
.rt-page [hidden]{display:none!important}
@media (max-width:680px){.rt-page{padding:18px 14px 48px}.rt-form{grid-template-columns:1fr}.rt-card{padding:16px}.rt-page h1{font-size:27px}}
"""

JS = r"""
(function(){'use strict';
var T=function(k,p){return window.VOOLT('routines.'+k,p);};
var C=function(k,p){return window.VOOLT(k,p);};
var ID=/^rt-[0-9a-f]{10}$/;
function el(tag,text,cls){var n=document.createElement(tag);if(text!=null&&text!=='')n.textContent=String(text);if(cls)n.className=cls;return n;}
function add(parent){for(var i=1;i<arguments.length;i++){if(arguments[i])parent.appendChild(arguments[i]);}return parent;}
var root=document.getElementById('routinesRoot');

// Outside the page: a Home menu entry and palette rows on the chat page, a row in Settings.
if(!root){
  var contacts=document.getElementById('contactsBtn');
  if(contacts){
    if(!document.getElementById('routinesLink')){
      var after=document.getElementById('thingsLink')||contacts;
      var link=el('a',T('title'));link.id='routinesLink';link.href='/routines';link.className=contacts.className;link.dataset.ico='retry';
      after.parentNode.insertBefore(link,after.nextSibling);
    }
    var palette=window.VoolPalette;
    if(palette&&palette.register){
      palette.register('routines',T('title')+'…',function(){window.location.href='/routines';},'',T('palette_words'));
      fetch('/api/routines/names',{cache:'no-store'}).then(function(r){return r.ok?r.json():null;}).then(function(data){
        ((data&&data.routines)||[]).slice(0,40).forEach(function(item){
          if(!ID.test(item.id))return;
          palette.register('routine:'+item.id,T('palette_run',{name:item.name}),function(){window.location.href='/routines?run='+item.id;},'',T('palette_words'));
        });
      }).catch(function(){});
    }
  }else if(!document.getElementById('routinesSetting')){
    var row=el('section');row.id='routinesSetting';
    var words=el('div');add(words,el('strong',T('title')),el('p',T('settings_help')));
    var open=el('a',T('open'));open.href='/routines';open.id='routinesSettingLink';
    add(row,words,open);
    var things=document.getElementById('myThingsSetting');
    if(things&&things.parentNode)things.parentNode.insertBefore(row,things.nextSibling);
    else{var host=document.querySelector('main')||document.body;host.insertBefore(row,host.firstChild);}
  }
  return;
}

var state={data:null,busy:false,poll:null,tick:null,ends:0};
var lang=document.documentElement.lang||undefined;
var status=el('div','','rt-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
function say(text,error){status.textContent=text||'';status.dataset.error=String(!!error);}
var ERRORS=['bad_name','name_taken','no_folder','bad_folder','folder_missing','folder_too_big','watch_running','no_watch','nothing_learned','bad_types','bad_text','text_too_long','bad_rule','bad_columns','no_steps','bad_max_files','bad_schedule','bad_log_file','list_missing','list_too_big','plan_changed','plan_stopped','nothing_to_do','run_busy','not_found','bad_share','share_changed','storage_unavailable','save_failed'];
function errorText(e){var code=(e&&e.code)||'';return ERRORS.indexOf(code)>=0?T('err.'+code):T('err.retry');}
async function request(path,body){
  var options=body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),cache:'no-store'};
  var res=await fetch(path,options),data=null;
  try{data=await res.json();}catch(e){data=null;}
  if(!res.ok||!data||!data.ok){var err=new Error('request');err.code=(data&&data.error)||'';throw err;}
  return data;
}
function post(action,body){return request('/api/routines/'+action,body||{});}
async function task(fn){
  if(state.busy)return;state.busy=true;say('');root.setAttribute('aria-busy','true');
  var controls=Array.prototype.slice.call(root.querySelectorAll('button,input,select')).map(function(n){return [n,n.disabled];});
  controls.forEach(function(pair){pair[0].disabled=true;});
  try{await fn();}catch(e){say(errorText(e),true);}
  finally{controls.forEach(function(pair){if(pair[0].isConnected)pair[0].disabled=pair[1];});root.removeAttribute('aria-busy');state.busy=false;}
}
function button(label,fn,cls){var b=el('button',label,cls);b.type='button';b.onclick=function(){task(fn);};return b;}
function plainButton(label,fn,cls){var b=el('button',label,cls);b.type='button';b.onclick=fn;return b;}
function when(seconds){var d=new Date(seconds*1000);if(isNaN(d.getTime()))return '';try{return d.toLocaleString(lang,{dateStyle:'medium',timeStyle:'short'});}catch(e){return d.toLocaleString();}}
function weekdayName(index){try{return new Intl.DateTimeFormat(lang,{weekday:'long'}).format(new Date(2024,0,1+index));}catch(e){return String(index+1);}}
function shortPath(path){
  var text=String(path||''),roots=(state.data&&state.data.roots)||[];
  for(var i=0;i<roots.length;i++){var base=roots[i].path;
    if(text===base)return roots[i].name;
    if(text.indexOf(base)===0&&/[\\/]/.test(text.charAt(base.length)))return [roots[i].name].concat(text.slice(base.length+1).split(/[\\/]/)).join(' › ');}
  return text;
}
function tokenLabel(token){return T('tok.'+token);}
function ruleWords(pieces){return (pieces||[]).map(function(p){return 'text' in p?p.text:'['+tokenLabel(p.token)+']';}).join('');}
function usesReceipts(routine){
  var receipt=(state.data&&state.data.tokens&&state.data.tokens.receipt)||[];
  var pieces=[].concat(routine.rename||[],routine.into||[]);((routine.log&&routine.log.columns)||[]).forEach(function(c){pieces=pieces.concat(c);});
  return pieces.some(function(p){return receipt.indexOf(p.token)>=0;});
}
function reasonText(code){
  code=String(code||'');
  if(code.indexOf('missing_value:')===0)return T('reason.missing_value',{value:tokenLabel(code.slice(14))});
  return ['target_exists','same_target','bad_target','unreadable','bad_new_name','bad_new_folder'].indexOf(code)>=0?T('reason.'+code):T('reason.other');
}
function stopText(code){return ['file_changed','bad_target','list_changed'].indexOf(code)>=0?T('stop.'+code):T('stop.other');}
function download(blob,name){var url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(url);},1000);}

var header=el('header');var back=el('a',C('setup.copy.back'),'rt-link');back.href='/chat';
add(header,back,el('h1',T('title')),el('p',T('intro')));
var panel=el('section','','rt-card rt-panel');panel.id='rtPanel';panel.hidden=true;
var teach=el('section','','rt-card');teach.id='rtTeach';
var list=el('section','','rt-card');list.id='rtList';
var bring=el('section','','rt-card');bring.id='rtImport';
add(root,header,status,panel,teach,list,bring);

function openPanel(title){
  panel.textContent='';panel.hidden=false;var h=el('h2',title);h.tabIndex=-1;add(panel,h);
  setTimeout(function(){h.focus({preventScroll:true});panel.scrollIntoView({block:'start'});},0);
  return panel;
}
function closePanel(){panel.hidden=true;panel.textContent='';}

async function load(){state.data=await request('/api/routines');paintTeach();paintList();paintImport();}

// --- choosing a folder: one of the three places, then folders inside it -------------------------
function folderChooser(actionLabel,onChoose,initial){
  var box=el('div','','rt-chooser'),where=el('p','','rt-where'),folders=el('div','','rt-folders'),bar=el('div','','rt-bar');
  add(box,where,folders,bar);
  async function show(path){
    var data=await request('/api/routines/folders'+(path?'?under='+encodeURIComponent(path):''));
    var current=data.folder||'';folders.textContent='';bar.textContent='';
    if(!current){where.textContent=T('places');data.roots.forEach(function(item){add(folders,folderButton(item));});return;}
    where.textContent=T('inside',{folder:shortPath(current)});
    var isRoot=data.roots.some(function(item){return item.path===current;});
    add(folders,button(T('up'),function(){return show(isRoot?'':current.replace(/[\\/][^\\/]+$/,''));}));
    if(!data.folders.length)add(folders,el('p',T('no_subfolders'),'rt-muted'));
    data.folders.forEach(function(item){add(folders,folderButton(item));});
    add(bar,button(actionLabel,function(){return onChoose(current);},'rt-primary'));
  }
  function folderButton(item){var b=button(item.name,function(){return show(item.path);},'vi');b.dataset.ico='files';return b;}
  show(initial||'').catch(function(e){if(initial)return show('').catch(function(){say(errorText(e),true);});say(errorText(e),true);});
  return box;
}

// --- teaching: watch one folder, then see what VOOL saw -------------------------------------------
function stopPolling(){if(state.poll)clearInterval(state.poll);if(state.tick)clearInterval(state.tick);state.poll=state.tick=null;}
function paintTeach(){
  stopPolling();teach.textContent='';add(teach,el('h2',T('teach_heading')));
  var watch=state.data.watch;
  if(!watch){add(teach,el('p',T('teach_help'),'rt-muted'),folderChooser(T('start_watch'),async function(folder){var r=await post('watch_start',{folder:folder});state.data.watch=r.watch;paintTeach();}));return;}
  if(watch.running){paintWatching(watch);return;}
  paintSaw(watch);
}
function paintWatching(watch){
  var box=el('div','','rt-banner');box.id='rtWatching';
  var count=el('p',T('changes_so_far',{n:watch.changes||0}));count.id='rtWatchCount';
  var left=el('p','','rt-muted');left.id='rtWatchLeft';
  var bar=el('div','','rt-bar');
  add(bar,button(T('done_watching'),async function(){var r=await post('watch_stop');state.data.watch=r.watch;paintTeach();},'rt-primary'),
    button(T('cancel_watch'),async function(){await post('watch_discard');state.data.watch=null;paintTeach();say(T('forgotten'));}));
  add(box,el('strong',T('watching',{folder:shortPath(watch.folder)})),el('p',T('watch_do')),count,left,bar);add(teach,box);
  state.ends=Date.now()+(watch.seconds_left||0)*1000;
  var tick=function(){var s=Math.max(0,Math.round((state.ends-Date.now())/1000));left.textContent=T('time_left',{time:Math.floor(s/60)+':'+String(s%60).padStart(2,'0')});};
  tick();state.tick=setInterval(tick,1000);
  state.poll=setInterval(async function(){
    var data;try{data=await request('/api/routines/watch');}catch(e){return;}
    var now=data.watch;
    if(!now||!now.running){state.data.watch=now;paintTeach();if(now&&now.end_reason==='time_limit')say(T('time_limit'));return;}
    count.textContent=T('changes_so_far',{n:now.changes||0});state.ends=Date.now()+(now.seconds_left||0)*1000;
  },2000);
}
function noteText(note){
  if(note.kind==='moved')return T('note_moved',{from:note.from,to:note.to});
  if(note.kind==='rows')return T('note_rows',{file:note.path,n:(note.rows||[]).length});
  if(note.kind==='added')return T('note_added',{file:note.path});
  if(note.kind==='changed')return T('note_changed',{file:note.path});
  return T('note_removed',{file:note.path});
}
function paintSaw(watch){
  var forget=button(T('discard'),async function(){await post('watch_discard');state.data.watch=null;paintTeach();say(T('forgotten'));});
  if(watch.end_reason==='expired'){add(teach,el('p',T('expired')),forget);return;}
  add(teach,el('h3',T('saw_heading')));
  var notes=watch.notes||[];
  if(!notes.length)add(teach,el('p',T('saw_nothing')));
  var ul=el('ul','','rt-notes');ul.id='rtNotes';notes.slice(0,200).forEach(function(note){add(ul,el('li',noteText(note)));});add(teach,ul);
  var bar=el('div','','rt-bar');
  if(notes.some(function(note){return note.kind==='moved';}))add(bar,button(T('make'),async function(){var r=await post('propose');openEditor(r.proposal.routine,r.proposal,false);},'rt-primary'));
  else if(notes.length)add(teach,el('p',T('err.nothing_learned'),'rt-warn'));
  add(bar,forget);add(teach,bar);
}

// --- the routine editor: what VOOL understood, changed by the person ------------------------------
function ruleEditor(initial,groups,emptyText,label){
  var pieces=(initial||[]).map(function(p){return 'text' in p?{text:p.text}:{token:p.token};});
  var box=el('div','','rt-rule'),row=el('div','','rt-pieces'),empty=el('p',emptyText,'rt-muted'),bar=el('div','','rt-bar rt-small');
  row.setAttribute('role','group');row.setAttribute('aria-label',label);
  function paint(){
    row.textContent='';empty.hidden=!emptyText||pieces.length>0;
    pieces.forEach(function(piece,index){
      var chip=el('span','','rt-piece'),input;
      var hint=null;
      if('text' in piece){input=el('input');input.type='text';input.value=piece.text;input.maxLength=60;input.size=Math.max(4,Math.min(28,piece.text.length+2));input.setAttribute('aria-label',T('piece_text'));
        // A part that is only a space would look like an empty box: say what it is.
        hint=el('span',T('piece_space'),'rt-space');hint.hidden=!(piece.text&&!piece.text.trim());
        input.oninput=function(){piece.text=input.value;hint.hidden=!(piece.text&&!piece.text.trim());};}
      else{input=el('select');input.setAttribute('aria-label',T('piece_value'));
        groups.forEach(function(group){var og=el('optgroup');og.label=T(group[0]);group[1].forEach(function(token){var o=el('option',tokenLabel(token));o.value=token;o.selected=token===piece.token;add(og,o);});add(input,og);});
        input.onchange=function(){piece.token=input.value;};}
      var x=plainButton('×',function(){pieces.splice(index,1);paint();},'rt-x');x.setAttribute('aria-label',T('remove_piece'));
      add(chip,input,hint,x);add(row,chip);
    });
  }
  add(bar,plainButton(T('add_text'),function(){pieces.push({text:''});paint();var all=row.querySelectorAll('input');if(all.length)all[all.length-1].focus();}),
    plainButton(T('add_value'),function(){pieces.push({token:groups[0][1][0]});paint();var all=row.querySelectorAll('select');if(all.length)all[all.length-1].focus();}));
  add(box,row,empty,bar);paint();
  return {node:box,pieces:function(){return pieces.filter(function(p){return 'token' in p||p.text!=='';}).map(function(p){return 'token' in p?{token:p.token}:{text:p.text};});}};
}
function nameGroups(){var t=state.data.tokens;return [['group_file',t.file],['group_receipt',t.receipt]];}
function columnGroups(){var t=state.data.tokens;return [['group_file',t.file],['group_receipt',t.receipt],['group_result',t.result]];}
function labelled(form,text,node){var label=el('label',text);add(label,node);add(form,label);return node;}
function grouped(form,text,node){var box=el('div','','rt-field rt-wide');add(box,el('span',text),node);add(form,box);return node;}
function parseTypes(text){return String(text||'').split(/[\s,;]+/).filter(Boolean).map(function(t){t=t.toLowerCase();return t.charAt(0)==='.'?t:'.'+t;});}

function openEditor(routine,proposal,existing){
  var r=JSON.parse(JSON.stringify(routine));
  var p=openPanel(existing?T('change_heading',{name:routine.name}):T('proposal_heading'));
  p.dataset.editing=existing?'saved':'new';
  if(!existing)add(p,el('p',T('proposal_help'),'rt-muted'));
  if(proposal){
    if((proposal.examples||[]).length){add(p,el('h3',T('examples')));var ex=el('ul','','rt-notes');proposal.examples.forEach(function(x){add(ex,el('li',T('note_moved',{from:x.from,to:x.to})));});add(p,ex);}
    (proposal.unclear||[]).forEach(function(code){add(p,el('p',T('unclear.'+code),'rt-warn'));});
    if((proposal.fixed_text||[]).length)add(p,el('p',T('fixed_text',{words:proposal.fixed_text.map(function(w){return '“'+w+'”';}).join(', ')}),'rt-warn'));
    if(proposal.uses_receipts&&!proposal.receipts_here)add(p,el('p',T('receipts_off'),'rt-warn'));
    if((proposal.left_out||[]).length)add(p,el('p',T('left_out',{n:proposal.left_out.length}),'rt-muted'));
  }
  var form=el('div','','rt-form');
  var name=el('input');name.type='text';name.id='rtName';name.value=r.name||'';name.maxLength=state.data.limits.name;name.required=true;name.autocomplete='off';labelled(form,T('name'),name);
  var types=el('input');types.type='text';types.id='rtTypes';types.value=(r.types||[]).join(', ');labelled(form,T('types'),types);add(types.parentNode,el('small',T('types_help'),'rt-muted'));
  var folderBox=el('div','','rt-field rt-wide'),folderText=el('p',T('works_in',{folder:shortPath(r.folder)}));folderText.id='rtFolder';
  var choose=plainButton(T('choose_other'),function(){choose.hidden=true;var chooser=folderChooser(T('use_folder'),function(path){r.folder=path;folderText.textContent=T('works_in',{folder:shortPath(path)});chooser.remove();choose.hidden=false;},r.folder);add(folderBox,chooser);});
  add(folderBox,folderText,choose);add(form,folderBox);
  var rename=ruleEditor(r.rename,nameGroups(),T('rename_none'),T('rename'));grouped(form,T('rename'),rename.node);
  var into=ruleEditor(r.into,nameGroups(),T('into_none'),T('into'));grouped(form,T('into'),into.node);
  var logOn=el('input');logOn.type='checkbox';logOn.id='rtLogOn';logOn.checked=!!r.log;
  var logLabel=el('label','','rt-check rt-wide');add(logLabel,logOn,document.createTextNode(T('log_on')));add(form,logLabel);
  var logBox=el('div','','rt-wide');add(form,logBox);
  var logFile=el('input');logFile.type='text';logFile.id='rtLogFile';logFile.value=(r.log&&r.log.file)||'';logFile.placeholder='list.csv';
  var columns=((r.log&&r.log.columns)||[[{token:'date'}]]).map(function(c){return ruleEditor(c,columnGroups(),'',T('log_on'));});
  function paintLog(){
    logBox.textContent='';logBox.hidden=!logOn.checked;if(!logOn.checked)return;
    var fileLabel=el('label',T('log_file'),'rt-field');add(fileLabel,logFile,el('small',T('log_file_help'),'rt-muted'));add(logBox,fileLabel);
    columns.forEach(function(editor,index){var wrap=el('div','','rt-column'),head=el('div','','rt-bar rt-small');add(head,el('strong',T('column',{n:index+1})));
      if(columns.length>1)add(head,plainButton(T('remove_column'),function(){columns.splice(index,1);paintLog();}));add(wrap,head,editor.node);add(logBox,wrap);});
    if(columns.length<12)add(logBox,plainButton(T('add_column'),function(){columns.push(ruleEditor([{token:'date'}],columnGroups(),'',T('log_on')));paintLog();}));
  }
  logOn.onchange=paintLog;paintLog();
  var max=el('input');max.type='number';max.id='rtMax';max.min='1';max.max=String(state.data.limits.max_files);max.value=String(r.max_files||25);labelled(form,T('max_files'),max);
  var sched=el('fieldset','','rt-wide');add(sched,el('legend',T('schedule')));
  var every=el('select');every.id='rtEvery';every.setAttribute('aria-label',T('schedule'));[['',T('every_none')],['daily',T('every_daily')],['weekly',T('every_weekly')]].forEach(function(o){var op=el('option',o[1]);op.value=o[0];add(every,op);});every.value=(r.schedule&&r.schedule.every)||'';
  var day=el('select');day.id='rtDay';for(var d=0;d<7;d++){var op=el('option',weekdayName(d));op.value=String(d);add(day,op);}day.value=String((r.schedule&&r.schedule.weekday)||0);
  var time=el('input');time.type='time';time.id='rtTime';time.value=(r.schedule&&r.schedule.time)||'09:00';
  var dayLabel=el('label',T('weekday'));add(dayLabel,day);var timeLabel=el('label',T('time_of_day'));add(timeLabel,time);
  function paintSchedule(){timeLabel.hidden=!every.value;dayLabel.hidden=every.value!=='weekly';}
  every.onchange=paintSchedule;add(sched,every,dayLabel,timeLabel,el('small',T('schedule_help'),'rt-muted'));paintSchedule();add(form,sched);
  add(p,form);
  function collect(){
    return {id:r.id,name:name.value.trim(),folder:r.folder,types:parseTypes(types.value),rename:rename.pieces(),into:into.pieces(),
      log:logOn.checked?{file:logFile.value.trim(),columns:columns.map(function(c){return c.pieces();})}:null,
      max_files:parseInt(max.value,10)||0,schedule:{every:every.value,time:time.value||'09:00',weekday:parseInt(day.value,10)||0}};
  }
  var result=el('div');result.id='rtTry';
  var bar=el('div','','rt-bar');
  add(bar,button(T('try'),async function(){var plan=(await post('try',{routine:collect()})).plan;showPlan(result,plan,collect());add(result,el('p',T('try_nothing_changed'),'rt-muted'));result.scrollIntoView({block:'nearest'});}),
    button(T('save'),async function(){await post(existing?'update':'save',{routine:collect(),confirm:true});closePanel();await load();say(T('saved'));},'rt-primary'),
    plainButton(C('dialog.cancel'),closePanel));
  add(p,el('p',T('try_help'),'rt-muted'),bar,result);
  name.focus({preventScroll:true});
}

// --- the exact list, the run and its receipt ------------------------------------------------------
function showPlan(target,plan,routine){
  target.textContent='';
  var ops=plan.operations||[],needs=plan.needs_you||[],left=plan.left_alone||{};
  if(plan.stop==='too_many')add(target,el('p',T('plan_too_many',{count:plan.count,max:routine.max_files}),'rt-warn'));
  else if(!ops.length&&!needs.length)add(target,el('p',T('plan_none')));
  if(ops.length){add(target,el('h3',T('plan_changes',{n:ops.length})));var ul=el('ul','','rt-ops');ul.id='rtOps';
    ops.forEach(function(op){var li=el('li');add(li,el('span',T('plan_move',{from:op.from,to:op.to})));
      (op.make||[]).forEach(function(folder){add(li,el('small',T('plan_folder',{folder:folder}),'rt-muted'));});
      if(op.row)add(li,el('small',T('plan_row',{file:(plan.log&&plan.log.file)||'',row:op.row.join(' | ')}),'rt-muted'));
      add(ul,li);});add(target,ul);}
  if(needs.length){add(target,el('h3',T('plan_needs_you')));var nu=el('ul','','rt-ops');needs.forEach(function(n){add(nu,el('li',T('plan_needs_line',{file:n.file,reason:reasonText(n.reason)})));});add(target,nu);}
  if(left.other_types)add(target,el('p',T('plan_left_other',{n:left.other_types}),'rt-muted'));
  if(left.already_done)add(target,el('p',T('plan_left_done',{n:left.already_done}),'rt-muted'));
  if(!plan.receipts_here&&usesReceipts(routine))add(target,el('p',T('receipts_off'),'rt-muted'));
}
async function openPlan(routine){
  var plan=(await post('plan',{id:routine.id})).plan;
  var p=openPanel(T('plan_heading',{name:routine.name}));
  var body=el('div');add(p,body);showPlan(body,plan,routine);
  var bar=el('div','','rt-bar');
  if((plan.operations||[]).length&&!plan.stop){
    add(p,el('p',T('run_help'),'rt-muted'));
    add(bar,button(T('run'),async function(){
      var receipt;
      try{receipt=(await post('run',{id:routine.id,digest:plan.digest,confirm:true})).receipt;}
      catch(e){if(e.code==='plan_changed'){await openPlan(routine);say(T('err.plan_changed'),true);return;}throw e;}
      await load();showReceipt(routine,receipt);
    },'rt-primary'));
  }
  add(bar,plainButton(C('dialog.cancel'),closePanel));add(p,bar);
}
function showReceipt(routine,receipt){
  var p=openPanel(T('result_heading',{name:routine.name}));
  add(p,el('p',T('result_count',{done:receipt.done.length,planned:receipt.planned||receipt.done.length})));
  if(receipt.checks)add(p,el('p',T('result_checked',{good:receipt.checks.moved_as_planned,moved:receipt.checks.moved}),'rt-muted'));
  if(receipt.stopped)add(p,el('p',T('result_stopped',{file:receipt.stopped_at,reason:stopText(receipt.stopped)}),'rt-warn'));
  var ul=el('ul','','rt-ops');ul.id='rtDone';
  receipt.done.forEach(function(d){var li=el('li',T('plan_move',{from:d.from,to:d.to}));if(d.row)add(li,el('small',T('row_added',{row:d.row.join(' | ')}),'rt-muted'));add(ul,li);});
  add(p,ul,el('p',T('no_undo'),'rt-muted'),plainButton(T('close'),closePanel));
}
async function openRuns(routine){
  var runs=(await post('runs',{id:routine.id})).runs;
  var p=openPanel(T('runs_heading',{name:routine.name}));
  if(!runs.length)add(p,el('p',T('no_runs')));
  runs.forEach(function(receipt){
    var d=el('details','','rt-run');add(d,el('summary',T('run_line',{when:when(receipt.started),done:receipt.done.length,planned:receipt.planned||receipt.done.length})));
    if(receipt.stopped)add(d,el('p',T('result_stopped',{file:receipt.stopped_at,reason:stopText(receipt.stopped)}),'rt-warn'));
    var ul=el('ul','','rt-ops');receipt.done.forEach(function(x){add(ul,el('li',T('plan_move',{from:x.from,to:x.to})));});add(d,ul);add(p,d);
  });
  add(p,plainButton(T('close'),closePanel));
}
async function openVersions(routine){
  var versions=(await post('history',{id:routine.id})).versions;
  var p=openPanel(T('versions_heading',{name:routine.name}));
  add(p,el('p',T('versions_help'),'rt-muted'));
  var ul=el('ul','','rt-versions');
  versions.forEach(function(v){
    var li=el('li');add(li,el('span',T('version_line',{n:v.version,when:v.at?when(Date.parse(v.at)/1000):''})));
    if(v.version===routine.version)add(li,el('small',T('version_current'),'rt-muted'));
    else add(li,plainButton(T('go_back'),function(){confirmRollback(routine,v);}));
    add(ul,li);
  });
  add(p,ul,plainButton(T('close'),closePanel));
}
function confirmRollback(routine,version){
  var p=openPanel(T('go_back_question',{n:version.version}));
  add(p,el('p',T('go_back_help'),'rt-muted'));
  var bar=el('div','','rt-bar');
  add(bar,button(T('go_back_yes'),async function(){await post('rollback',{id:routine.id,version:version.version,confirm:true});closePanel();await load();say(T('went_back'));},'rt-primary'),
    button(C('dialog.cancel'),function(){return openVersions(routine);}));
  add(p,bar);
}
async function openShare(routine){
  var share=(await post('share_preview',{id:routine.id})).share;
  var p=openPanel(T('share_heading',{name:routine.name}));
  add(p,el('p',T('share_help'),'rt-muted'),el('p',T('share_check'),'rt-muted'));
  if(share.replaced)add(p,el('p',T('share_replaced',{n:share.replaced}),'rt-warn'));
  if(!share.contacts_checked)add(p,el('p',T('share_contacts_unchecked'),'rt-warn'));
  Object.keys(share.files).forEach(function(name){add(p,el('h3',name));var pre=el('pre',share.files[name]);pre.dir='ltr';add(p,pre);});
  var bar=el('div','','rt-bar');
  add(bar,button(T('share_save'),async function(){
    var res=await fetch('/api/routines/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:routine.id,sha256:share.sha256}),cache:'no-store'});
    if(!res.ok){var data=null;try{data=await res.json();}catch(e){data=null;}var err=new Error('share');err.code=(data&&data.error)||'';throw err;}
    download(await res.blob(),(routine.name.replace(/[^\w '’-]/g,'').trim()||'routine')+'.zip');say(T('share_saved'));
  },'rt-primary'),plainButton(C('dialog.cancel'),closePanel));
  add(p,bar);
}
function confirmRemove(routine){
  var p=openPanel(T('remove_question',{name:routine.name}));
  add(p,el('p',T('remove_help')));
  var bar=el('div','','rt-bar');
  add(bar,button(T('remove_yes'),async function(){await post('remove',{id:routine.id,confirm:true});closePanel();await load();say(T('removed'));},'rt-danger'),plainButton(C('dialog.cancel'),closePanel));
  add(p,bar);
}

// --- the saved routines, and routines brought in from a shared file -------------------------------
function ruleLines(parent,routine){
  var ul=el('ul','','rt-lines');
  add(ul,el('li',T('line_kinds',{kinds:(routine.types||[]).join(', ')})),
    el('li',(routine.rename||[]).length?T('line_rename',{rule:ruleWords(routine.rename)}):T('rename_none')),
    el('li',(routine.into||[]).length?T('line_into',{rule:ruleWords(routine.into)}):T('into_none')));
  if(routine.log)add(ul,el('li',T('line_log',{file:routine.log.file})));
  add(parent,ul);return ul;
}
function paintList(){
  list.textContent='';add(list,el('h2',T('saved_heading')));
  var routines=state.data.routines||[];
  if(!routines.length){add(list,el('p',T('none_yet'),'rt-muted'));return;}
  routines.forEach(function(routine){
    var card=el('article','','rt-routine');card.dataset.routine=routine.id;add(card,el('h3',routine.name));
    var lines=ruleLines(card,routine);
    add(lines,el('li',T('works_in',{folder:shortPath(routine.folder)})));
    var s=routine.schedule||{};
    if(s.every==='daily')add(lines,el('li',T('schedule_daily',{time:s.time})));
    if(s.every==='weekly')add(lines,el('li',T('schedule_weekly',{day:weekdayName(s.weekday||0),time:s.time})));
    var last=routine.last_run;
    add(lines,el('li',last?T('last_run',{when:when(last.started),done:last.done.length,planned:last.planned||last.done.length}):T('never_run'),'rt-muted'));
    var bar=el('div','','rt-bar');
    add(bar,button(T('look'),function(){return openPlan(routine);},'rt-primary'),plainButton(T('change'),function(){openEditor(routine,null,true);}),
      button(T('runs'),function(){return openRuns(routine);}),button(T('versions'),function(){return openVersions(routine);}),
      button(T('share'),function(){return openShare(routine);}),plainButton(T('remove'),function(){confirmRemove(routine);},'rt-danger'));
    add(card,bar);add(list,card);
  });
}
function paintImport(){
  bring.textContent='';add(bring,el('h2',T('import_heading')),el('p',T('import_help'),'rt-muted'));
  var input=el('input');input.type='file';input.accept='.zip,application/zip';input.id='rtImportFile';
  var label=el('label',T('import_choose'),'rt-field');add(label,input);
  var row=el('div','','rt-bar rt-bar-end');
  add(row,label,button(T('import_button'),async function(){
    var file=input.files&&input.files[0];
    if(!file){say(T('import_pick_first'),true);return;}
    if(!file.size||file.size>64*1024){var big=new Error('size');big.code='bad_share';throw big;}
    var bytes=new Uint8Array(await file.arrayBuffer()),raw='';
    for(var i=0;i<bytes.length;i+=16384)raw+=String.fromCharCode.apply(null,bytes.subarray(i,i+16384));
    await post('import',{data:btoa(raw)});input.value='';await load();say(T('imported'));
  }));
  add(bring,row);
  (state.data.staged||[]).forEach(function(staged){
    var card=el('article','','rt-routine');card.dataset.staged=staged.id;add(card,el('h3',staged.name),el('p',T('staged_off'),'rt-warn'));
    ruleLines(card,staged);
    var name=el('input');name.type='text';name.value=staged.name;name.maxLength=state.data.limits.name;var nameLabel=el('label',T('name'),'rt-field');add(nameLabel,name);add(card,nameLabel);
    add(card,folderChooser(T('staged_save'),async function(folder){await post('staged_save',{id:staged.id,folder:folder,name:name.value.trim(),confirm:true});await load();say(T('saved'));}));
    add(card,button(T('staged_discard'),async function(){await post('staged_discard',{id:staged.id});await load();}));
    add(bring,card);
  });
}

task(async function(){
  await load();
  var wanted=new URLSearchParams(window.location.search).get('run');
  if(wanted){
    try{window.history.replaceState(null,'','/routines');}catch(e){}
    var routine=(state.data.routines||[]).filter(function(item){return item.id===wanted;})[0];
    if(routine)await openPlan(routine);else say(T('err.not_found'),true);
  }
});
window.VoolRoutines=Object.freeze({reload:function(){return task(load);}});
})();
"""


def render_routines_fragment() -> str:
    return "<style>" + CSS + "</style><script>" + JS + "</script>"


def render_routines_page(locale: str) -> str:
    from core.i18n.catalog import catalog_for
    from core.i18n.locales import get_locale
    from core.i18n.page_bundle import render_i18n_bootstrap
    from core.ui_icons import ICON_CSS

    direction = get_locale(locale).direction
    title = html.escape(catalog_for(locale).text("routines.title"))
    # The VOOL mark as the tab icon, inline, as Settings does: no request for a /favicon.ico that is not served.
    logo = Path(__file__).with_name("assets") / "vool-logo-dark.png"
    icon = ("data:image/png;base64," + b64encode(logo.read_bytes()).decode("ascii")) if logo.is_file() else "data:,"
    return ('<!doctype html><html lang="' + html.escape(locale, quote=True) + '" dir="' + direction + '"><head>'
            '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + title
            + '</title><link rel="icon" type="image/png" href="' + icon + '"><style>html{background:#f7f8f4}body{margin:0}@media (prefers-color-scheme:dark){html{background:#141a18}}'
            + ICON_CSS + '</style>' + render_i18n_bootstrap(locale) + '</head><body class="rt-page"><main id="routinesRoot"></main>'
            + render_routines_fragment() + '</body></html>')


__all__ = ["render_routines_fragment", "render_routines_page"]
