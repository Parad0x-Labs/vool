"""The protected media page (``/media``): photos, videos and sound kept sealed on this computer, from the phone's
private camera or added here, and the USB drive backup. Every server value is written as text, never markup; the
files themselves are opened only through ``/api/media/item`` for this page."""
from __future__ import annotations

import html

CSS = r"""
.ml-page{font:16px/1.5 system-ui,sans-serif;max-width:1180px;margin:0 auto;padding:32px 24px;color:#21332f;background:#f7f8f4;min-height:100vh;box-sizing:border-box}
.ml-page *{box-sizing:border-box}.ml-page h1{font-size:34px;letter-spacing:-1px;margin:0}.ml-page h2{font-size:21px;margin:0 0 10px}.ml-page p{color:#50635c;margin:4px 0}
.ml-page button,.ml-page .ml-link,.ml-page select{font:inherit;background:#fff;color:#214c40;border:1px solid #a9bdb2;border-radius:9px;padding:8px 14px;cursor:pointer;text-decoration:none}
.ml-page button:hover{background:#edf2e9}.ml-page button:focus-visible,.ml-page input:focus-visible,.ml-page select:focus-visible,.ml-page a:focus-visible{outline:3px solid #b0812b;outline-offset:2px}
.ml-page button:disabled{opacity:.5;cursor:default}.ml-page .ml-primary{background:#275846;color:#fff}.ml-page .ml-danger{color:#8c302e;border-color:#c68b84}
.ml-page button[aria-pressed="true"]{background:#275846;color:#fff;border-color:#275846}
.ml-bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0}.ml-grow{flex:1}
.ml-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:22px;align-items:start}
.ml-card{border:1px solid #c9d4c8;border-radius:16px;background:#fff;padding:20px;min-width:0}
.ml-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-top:6px}
.ml-tile{position:relative;display:flex;flex-direction:column;gap:6px;text-align:start;padding:8px!important;border-radius:12px!important;min-width:0}
.ml-tile .ml-thumb{width:100%;aspect-ratio:1;border-radius:8px;background:#e8ede5;object-fit:cover;display:flex;align-items:center;justify-content:center;color:#60726a;font-size:14px;overflow:hidden}
.ml-tile .ml-name{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ml-tile .ml-meta{font-size:12px;color:#60726a}
.ml-tile[aria-checked="true"]{outline:3px solid #275846;outline-offset:1px}
.ml-page input[type=password]{font:inherit;border:1px solid #a9bdb2;border-radius:7px;padding:9px;width:100%;background:#fff;color:#21332f;margin:4px 0}
.ml-page label{display:block;font-size:14px;margin-top:8px}.ml-page label.ml-check{display:flex;gap:8px;align-items:flex-start}.ml-check input{margin-top:4px}
.ml-status{min-height:0;color:#50635c;overflow-wrap:anywhere;font-size:14px}.ml-status[data-error="true"]{color:#923729}
.ml-muted{font-size:14px;color:#60726a}.ml-meter{height:6px;border-radius:3px;background:#e1e7de;overflow:hidden;margin:8px 0}.ml-meter>div{height:100%;background:#275846;width:0}
.ml-viewer{position:fixed;inset:0;background:rgba(20,28,25,.92);display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px;z-index:10;gap:12px}
.ml-viewer img,.ml-viewer video{max-width:100%;max-height:78vh;border-radius:8px;background:#000}.ml-viewer audio{width:min(520px,100%)}
.ml-viewer .ml-bar{margin:0}.ml-viewer p{color:#e8ede5}
.ml-page [hidden]{display:none!important}
@media(max-width:860px){.ml-layout{grid-template-columns:1fr}}
@media(max-width:600px){.ml-page{padding:20px 14px}.ml-page h1{font-size:28px}.ml-grid{grid-template-columns:repeat(auto-fill,minmax(120px,1fr))}}
"""

JS = r"""
(function(){'use strict';
const root=document.getElementById('mediaRoot');if(!root)return;
const t=(k,p)=>window.VOOLT('medialib.'+k,p), common=k=>window.VOOLT(k);
const lang=document.documentElement.lang||'en';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null&&text!=='')n.textContent=text;if(cls)n.className=cls;return n;};
const add=(p,...c)=>{c.forEach(n=>n&&p.appendChild(n));return p;};
const size=n=>{const v=Number(n)||0;const [u,d]=v>=1e9?['gigabyte',v/1e9]:v>=1e6?['megabyte',v/1e6]:['kilobyte',v/1e3];try{return new Intl.NumberFormat(lang,{style:'unit',unit:u,maximumFractionDigits:1}).format(d);}catch(e){return d.toFixed(1)+' '+u;}};
const when=s=>{try{return new Date((Number(s)||0)*1000).toLocaleDateString(lang,{year:'numeric',month:'short',day:'numeric'});}catch(e){return '';}};
const ERR={wrong_password:1,password_short:1,password_mismatch:1,backup_damaged:1,drive_not_found:1,password_needed:1,no_backup:'password_needed',busy:1,unsupported:1,too_large:'unsupported',damaged:1,drive_gone:1,drive_full:1,cancelled:1,copy_check_failed:'drive_error',drive_error:1};
const word=code=>{const k=ERR[code];return t(k===1?code:(k||'retry'));};
async function call(action,data){let res,body;try{res=await fetch('/api/media',{method:'POST',headers:{'Content-Type':'application/json'},cache:'no-store',body:JSON.stringify(Object.assign({action:action},data||{}))});body=await res.json();}catch(e){const x=new Error('retry');x.code='retry';throw x;}if(!res.ok||!body.ok){const x=new Error(body.error||'retry');x.code=body.error||'retry';x.body=body;throw x;}return body;}
function status(node,text,error){node.textContent=text||'';node.dataset.error=String(!!error);}

/* --- the library --- */
let section='mobile',kind='photo',sort='new',items=[],selecting=false,chosen=new Set(),working=false;
const head=el('header');const back=el('a',common('setup.copy.back'),'ml-link');back.href='/chat';add(head,back,el('h1',t('title')),el('p',t('intro')));
const tabs=el('div','','ml-bar');tabs.setAttribute('role','group');
const kinds=el('div','','ml-bar');kinds.setAttribute('role','group');
const order=el('select');order.setAttribute('aria-label',t('sort_label'));[['new','sort_new'],['old','sort_old'],['big','sort_big'],['small','sort_small']].forEach(([v,k])=>{const o=el('option',t(k));o.value=v;order.appendChild(o);});
const picker=el('input');picker.type='file';picker.multiple=true;picker.hidden=true;picker.accept='image/*,video/*,audio/*';
const addBtn=el('button',t('add'),'ml-primary');addBtn.type='button';addBtn.onclick=()=>picker.click();
const selectBtn=el('button',t('select'));selectBtn.type='button';
const removeBtn=el('button',t('remove_selected'),'ml-danger');removeBtn.type='button';removeBtn.hidden=true;
const tools=el('div','','ml-bar');add(tools,kinds,el('span','','ml-grow'),order,selectBtn,removeBtn,addBtn,picker);
const libStatus=el('div','','ml-status');libStatus.setAttribute('role','status');libStatus.setAttribute('aria-live','polite');
const confirmBox=el('div','','ml-card');confirmBox.hidden=true;
const grid=el('div','','ml-grid');const empty=el('p','','ml-muted');
const libCard=el('section','','ml-card');add(libCard,tabs,tools,libStatus,confirmBox,empty,grid);
function chip(parent,label,isOn,fn){const b=el('button',label);b.type='button';b.setAttribute('aria-pressed',String(isOn()));b.onclick=()=>{fn();parent.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));};parent.appendChild(b);}
chip(tabs,t('tab_mobile'),()=>section==='mobile',()=>{section='mobile';load();});
chip(tabs,t('tab_local'),()=>section==='local',()=>{section='local';load();});
[['photo','kind_photo'],['video','kind_video'],['audio','kind_audio']].forEach(([v,k])=>chip(kinds,t(k),()=>kind===v,()=>{kind=v;load();}));
order.onchange=()=>{sort=order.value;load();};
selectBtn.onclick=()=>{selecting=!selecting;chosen.clear();selectBtn.textContent=t(selecting?'done_select':'select');confirmBox.hidden=true;paint();};
removeBtn.onclick=()=>{confirmBox.textContent='';const yes=el('button',common('dialog.delete'),'ml-danger');yes.type='button';const no=el('button',common('dialog.cancel'));no.type='button';
  add(confirmBox,el('p',t('remove_question',{count:chosen.size})),add(el('div','','ml-bar'),yes,no));confirmBox.hidden=false;no.onclick=()=>{confirmBox.hidden=true;};
  yes.onclick=async()=>{yes.disabled=true;const ids=[...chosen];try{const r=await call('remove',{ids:ids,confirm:ids.length});status(libStatus,t('removed',{count:r.removed}));chosen.clear();selecting=false;selectBtn.textContent=t('select');}catch(e){status(libStatus,word(e.code),true);}confirmBox.hidden=true;await load();};};
function tile(item){const b=el('button','', 'ml-tile');b.type='button';const face=item.kind==='photo'&&!item.damaged?el('img','','ml-thumb'):el('div',t('kind_'+item.kind),'ml-thumb');
  if(face.tagName==='IMG'){face.alt='';face.loading='lazy';face.src='/api/media/item?id='+encodeURIComponent(item.id)+'&thumb=1';face.onerror=()=>{face.replaceWith(el('div',t('kind_photo'),'ml-thumb'));};}
  add(b,face,el('span',item.name||'','ml-name'),el('span',when(item.taken_at||item.added_at)+' · '+size(item.size),'ml-meta'));b.setAttribute('aria-label',(item.name||'')+', '+when(item.taken_at||item.added_at)+', '+size(item.size));
  if(selecting){b.setAttribute('role','checkbox');b.setAttribute('aria-checked',String(chosen.has(item.id)));}
  b.onclick=()=>{if(selecting){chosen.has(item.id)?chosen.delete(item.id):chosen.add(item.id);paint();}else open(item);};return b;}
function paint(){grid.textContent='';items.forEach(i=>grid.appendChild(tile(i)));empty.textContent=items.length?'':(section==='mobile'?t('empty_mobile'):t('empty'));removeBtn.hidden=!selecting;removeBtn.disabled=!chosen.size;addBtn.hidden=section!=='local'||selecting;}
async function load(){try{const r=await call('list',{section:section,kind:kind,sort:(sort==='big'||sort==='small')?'size':'date',order:(sort==='old'||sort==='small')?'asc':'desc'});items=r.items;chosen.forEach(id=>{if(!items.some(i=>i.id===id))chosen.delete(id);});paint();}catch(e){status(libStatus,word(e.code),true);}}

/* --- the viewer: the file is opened for this page only, never saved anywhere else --- */
function open(item){if(item.damaged){status(libStatus,t('damaged'),true);return;}const shade=el('div','','ml-viewer');shade.setAttribute('role','dialog');shade.setAttribute('aria-modal','true');shade.setAttribute('aria-label',item.name||'');
  const src='/api/media/item?id='+encodeURIComponent(item.id);let media;
  if(item.kind==='photo'){media=el('img');media.alt=item.name||'';}else{media=el(item.kind==='video'?'video':'audio');media.controls=true;media.preload='metadata';media.setAttribute('controlsList','nodownload');}
  media.src=src;media.onerror=()=>{media.replaceWith(el('p',t('damaged')));};
  const close=el('button',t('close'));close.type='button';const shut=()=>{try{media.pause&&media.pause();}catch(e){}media.removeAttribute('src');shade.remove();document.removeEventListener('keydown',esc);};close.onclick=shut;
  const esc=e=>{if(e.key==='Escape')shut();};document.addEventListener('keydown',esc);
  add(shade,media,el('p',(item.name||'')+' · '+when(item.taken_at||item.added_at)+' · '+size(item.size)),add(el('div','','ml-bar'),close));document.body.appendChild(shade);close.focus();}

/* --- adding files from this computer: sent in slices, sealed as they arrive --- */
picker.onchange=async()=>{const files=[...picker.files];picker.value='';if(!files.length)return;addBtn.disabled=true;let done=0;
  for(const file of files){let id='';try{const b=await call('upload_begin',{name:file.name,media_type:file.type||'',size:file.size,taken_at:file.lastModified||Date.now()});id=b.upload_id;const step=b.slice_bytes;
      for(let off=0;off<file.size;off+=step){status(libStatus,file.name+' · '+Math.floor(off*100/file.size)+'%');const res=await fetch('/api/media/upload',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Media-Upload':id,'X-Media-Offset':String(off)},body:file.slice(off,off+step)});const r=await res.json().catch(()=>({}));if(!res.ok||!r.ok){const x=new Error('retry');x.code=r.error||'retry';throw x;}}
      await call('upload_finish',{upload_id:id});done++;}
    catch(e){if(id)call('upload_cancel',{upload_id:id}).catch(()=>{});status(libStatus,file.name+': '+word(e.code),true);addBtn.disabled=false;await load();return;}}
  addBtn.disabled=false;status(libStatus,t('added',{count:done}));await load();};

/* --- USB drive backup --- */
const bk=el('section','','ml-card');const drivePick=el('select');drivePick.setAttribute('aria-label',t('backup_title'));
const look=el('button',t('look_drives'));look.type='button';
const note=el('p','','ml-muted');const pw=el('input');pw.type='password';pw.autocomplete='off';const pw2=el('input');pw2.type='password';pw2.autocomplete='new-password';
const pwLabel=el('label');const pw2Label=el('label',t('repeat_password'));pw2Label.appendChild(pw2);
const mirrorBox=el('input');mirrorBox.type='checkbox';const mirror=add(el('label','','ml-check'),mirrorBox,el('span',t('mirror')));
const go=el('button',t('start'),'ml-primary');go.type='button';const verify=el('button',t('check'));verify.type='button';const bring=el('button',t('restore'));bring.type='button';
const stop=el('button',t('stop'));stop.type='button';stop.hidden=true;const meter=el('div','','ml-meter');const fill=el('div');meter.appendChild(fill);meter.hidden=true;
const plan=el('p','','ml-muted');const bkStatus=el('div','','ml-status');bkStatus.setAttribute('role','status');bkStatus.setAttribute('aria-live','polite');const ask=el('div');
add(bk,el('h2',t('backup_title')),el('p',t('backup_intro'),'ml-muted'),add(el('div','','ml-bar'),drivePick,look),note,pwLabel,pw2Label,mirror,plan,ask,add(el('div','','ml-bar'),go,verify,bring,stop),meter,bkStatus);
let drives=[];const current=()=>drives.find(d=>d.drive_id===drivePick.value);
function form(){const d=current();const fresh=d&&!d.has_backup;pwLabel.textContent=t(fresh?'new_password':'password');pwLabel.appendChild(pw);
  pwLabel.hidden=!d||(d.has_backup&&d.remembered);pw2Label.hidden=!fresh;note.textContent=!drives.length?t('no_drives'):(d&&d.has_backup&&d.remembered?t('remembered'):'');
  [go,verify,bring].forEach(b=>b.disabled=!d||working);verify.hidden=bring.hidden=!!fresh;drivePick.hidden=!drives.length;plan.textContent='';ask.textContent='';if(d&&d.has_backup&&d.remembered)showPlan();}
async function findDrives(){try{const r=await call('drives');drives=r.drives;mirrorBox.checked=!!r.settings.mirror_removals;const keep=drivePick.value||r.settings.last_drive;drivePick.textContent='';drives.forEach(d=>{const o=el('option',d.name+' · '+size(d.free_bytes));o.value=d.drive_id;drivePick.appendChild(o);});if(drives.some(d=>d.drive_id===keep))drivePick.value=keep;form();}catch(e){status(bkStatus,word(e.code),true);}}
const creds=()=>({drive_id:drivePick.value,password:pw.value,confirm_password:pw2.value});
async function showPlan(){try{const r=await call('plan',creds());const p=r.plan;plan.textContent=t('plan_line',{count:p.to_copy,size:size(p.bytes),have:p.on_drive});}catch(e){}}
drivePick.onchange=form;look.onclick=findDrives;
mirrorBox.onchange=async()=>{try{await call('settings',{mirror_removals:mirrorBox.checked});}catch(e){mirrorBox.checked=!mirrorBox.checked;}};
async function run(action,extra){const d=current();if(!d)return;status(bkStatus,'');ask.textContent='';
  try{await call(action,Object.assign(creds(),{create:action==='backup'&&!d.has_backup},extra||{}));}catch(e){status(bkStatus,word(e.code),true);return;}
  pw2.value='';working=true;stop.hidden=false;meter.hidden=false;fill.style.width='0';form();watch(action);}
async function watch(action){let job;try{job=(await call('job')).job;}catch(e){setTimeout(()=>watch(action),1500);return;}
  if(job.running){const total=job.total||0;fill.style.width=(total?Math.round(job.done*100/total):0)+'%';status(bkStatus,t('working',{done:job.done||0,total:total}));setTimeout(()=>watch(action),700);return;}
  working=false;stop.hidden=true;meter.hidden=true;pw.value='';const r=job.result||{};
  if(job.error){status(bkStatus,word(job.error),true);}
  else if(action==='backup'&&r.stopped==='confirm_removals'){status(bkStatus,'');const yes=el('button',t('mirror_yes'),'ml-danger');yes.type='button';const no=el('button',t('mirror_no'));no.type='button';
    add(ask,el('p',t('mirror_question',{count:r.removed_here})),add(el('div','','ml-bar'),yes,no));yes.onclick=()=>run('backup',{confirm_removals:r.removed_here});no.onclick=async()=>{mirrorBox.checked=false;await call('settings',{mirror_removals:false}).catch(()=>{});run('backup');};}
  else if(action==='backup'){let s=r.stopped?word(r.stopped):t('done_backup',{count:r.copied||0,total:r.on_drive||0});if(!r.stopped&&r.removed)s+=' '+t('done_removed',{count:r.removed});status(bkStatus,s,!!r.stopped);}
  else if(action==='check'){status(bkStatus,r.ok?t('check_ok',{total:r.total||0}):t('check_bad',{names:(r.damaged||[]).join(', ')}),!r.ok);}
  else{let s=r.stopped?word(r.stopped):t('restored',{count:r.restored||0,have:r.already||0});if((r.damaged||[]).length)s+=' '+t('check_bad',{names:r.damaged.join(', ')});status(bkStatus,s,!!r.stopped||!!(r.damaged||[]).length);await load();}
  await findDrives();}
go.onclick=()=>run('backup');verify.onclick=()=>run('check');bring.onclick=()=>run('restore');stop.onclick=()=>call('cancel').catch(()=>{});

const layout=el('div','','ml-layout');add(layout,libCard,bk);add(root,head,layout);
load();findDrives();call('job').then(r=>{if(r.job&&r.job.running){working=true;stop.hidden=false;meter.hidden=false;watch(r.job.kind);}}).catch(()=>{});
})();
"""


def render_media_page(locale: str) -> str:
    from core.i18n.catalog import catalog_for
    from core.i18n.locales import get_locale
    from core.i18n.page_bundle import render_i18n_bootstrap

    direction = get_locale(locale).direction
    title = html.escape(catalog_for(locale).text("medialib.title"))
    return ('<!doctype html><html lang="' + html.escape(locale, quote=True) + '" dir="' + direction + '"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>' + title + '</title>'
            + render_i18n_bootstrap(locale) + '<style>' + CSS + '</style></head><body class="ml-page"><main id="mediaRoot"></main>'
            '<script>' + JS + '</script></body></html>')
