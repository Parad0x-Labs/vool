"""Owner-local X ratings and approvals. UI state never joins chat history."""
from __future__ import annotations


def render_x_social_fragment() -> str:
    return r'''<style>
.vx-social{display:flex;flex-direction:column;gap:10px;border:1px solid var(--border);border-radius:12px;padding:14px;margin:10px 0;color:var(--ink);background:var(--panel)}
.vx-social label{display:flex;align-items:center;gap:8px}.vx-social p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.vx-social button,.vx-social input,.vx-social textarea,.vx-social select{font:inherit;border:1px solid var(--border);border-radius:8px;padding:7px 10px;background:var(--field,var(--bg));color:var(--ink)}
.vx-social strong,.x-action-card strong{overflow-wrap:anywhere;white-space:pre-wrap}.vx-social button{cursor:pointer}.vx-social button.vx-approve{background:var(--accent);color:var(--bg);font-weight:600}.vx-social button:disabled{opacity:.5;cursor:default}.vx-social .vx-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.vx-social textarea{width:100%;box-sizing:border-box;resize:vertical}.vx-social .vx-note{font-size:12px;color:var(--muted)}
</style><script>
(function(){'use strict';
function t(k,f){try{const v=VOOLT(k);if(v&&v!==k)return v;}catch(e){}return f;}

const summaryTemplates={"post": "Post on X", "reply": "Reply to @{author}", "quote": "Quote a post by @{author}", "delete": "Delete your post", "like": "Like a post by @{author}", "unlike": "Remove your like from a post by @{author}", "repost": "Repost a post by @{author}", "unrepost": "Undo your repost of a post by @{author}", "follow": "Follow {who}", "unfollow": "Unfollow {who}", "hide_reply": "Hide a reply by @{author}", "unhide_reply": "Unhide a reply by @{author}", "mute": "Mute {who}", "unmute": "Unmute {who}", "dm": "Send a direct message to {who}", "list_create": "Create the {privacy} list “{name}”{description}", "list_add": "Add {who} to your list “{name}”", "list_remove": "Remove {who} from your list “{name}”"};
function format(k,f,values){let text=t(k,f);if(document.documentElement.dir==='rtl')text=text.replace(/@\{author\}/g,'\u2068@{author}\u2069');return text.replace(/\{(\w+)\}/g,(_,key)=>String(values[key]??'?'));}
function summary(a){
 const isolate=value=>document.documentElement.dir==='rtl'?'\u2068'+value+'\u2069':String(value);
 const target=a.target||{};
 const username=String(target.username||'?');
 const handle='@'+username;
 const name=Array.from(String(target.name||'').replace(/[\p{Cf}\u{E0000}-\u{E007F}]/gu,'').trim()).slice(0,50).join('');
 const who=name&&name.toLowerCase()!==username.toLowerCase()?name+' ('+handle+')':handle;
 const values={author:target.author||'?',who:isolate(who),name:isolate(target.list_name||target.name||target.list_id||'?'),
  privacy:target.private?t('xs.private','private'):t('xs.public','public'),
  description:target.description?format('xs.description',', described as "{description}"',{description:isolate(target.description)}):''};
 return summaryTemplates[a.kind]?format('xs.summary_'+a.kind,summaryTemplates[a.kind],values):t('xs.summary_unknown','X action');
}
function state(a){if(a.state==='unknown')return t('xs.unknown_state','Unclear if it was sent. Check X before trying again.');if(a.state==='failed'&&a.message)return a.message;return t('xact.state_'+a.state,a.message||a.state);}
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;}
async function call(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();return {ok:r.ok&&j.ok!==false,data:j};}
function button(label,fn){const b=node('button','',label);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;b.setAttribute('aria-busy','true');try{await fn();}finally{b.disabled=false;b.removeAttribute('aria-busy');}});return b;}
function rating(host,a){
 if(!a.text||!['post','reply','quote'].includes(a.kind))return;
 const form=node('div','vx-social vx-rating');const label=node('label','',t('xs.rating','Your rating'));const score=node('select');score.setAttribute('aria-label',t('xs.rating','Your rating'));
 score.appendChild(new Option(t('xs.choose','Choose 1–10'),''));for(let i=1;i<=10;i++)score.appendChild(new Option(String(i),String(i)));label.appendChild(score);form.appendChild(label);
 const comment=node('textarea');comment.rows=2;comment.maxLength=500;comment.placeholder=t('xs.comment','Comment (optional)');comment.setAttribute('aria-label',comment.placeholder);form.appendChild(comment);
 const note=node('p','vx-note');note.setAttribute('role','status');const row=node('div','vx-row');
 row.appendChild(button(t('xs.save','Save rating'),async()=>{
   if(!score.value){score.focus();return;}
   try{const r=await call('/api/x/feedback',{action:'save',action_id:a.id,rating:Number(score.value),comment:comment.value});note.textContent=r.ok?t('xs.saved','Saved.'):t('xs.failed','Could not save this change. Try again.');
   if(r.ok){const previous=row.querySelector('[data-voice]');if(previous)previous.remove();}
   if(r.ok&&Number(score.value)>=9){const v=button(t('xs.voice','Use as a voice sample'),async()=>{note.textContent='';const o=await call('/api/x/feedback',{action:'voice',scope:a.feedback_scope||'global',id:r.data.id});note.textContent=o.ok?t('xs.saved','Saved.'):t('xs.failed','Could not save this change. Try again.');if(o.ok)v.dataset.voiceAdded='1';});v.dataset.voice='1';row.appendChild(v);}
   }catch(e){note.textContent=t('xs.failed','Could not save this change. Try again.');}
 }));form.append(row,note);host.appendChild(form);
}
async function manager(host,scope){
 const box=node('section','vx-social');box.dataset.feedbackScope=scope;host.appendChild(box);
 async function draw(){
  let out;try{out=await call('/api/x/feedback',{action:'list',scope:scope});}catch(e){return;}
  if(!out.ok){box.replaceChildren(node('p','vx-note',t('xs.failed','Could not save this change. Try again.')));return;}
  box.replaceChildren(node('strong','',t('xs.ratings','Your ratings')+' · '+(scope==='global'?t('settings.profile.scope_name.global','Everywhere'):t('settings.profile.scope_name.project','One project')+': '+scope.slice(8))));
  box.appendChild(node('p','vx-note',t('xs.forget_scope','Forgetting ratings removes their saved guidance and voice samples. Past chats may still contain the feedback.')));
  const data=out.data;const row=node('div','vx-row');const note=node('p','vx-note');note.setAttribute('role','status');
  async function change(payload){try{const r=await call('/api/x/feedback',Object.assign({scope:scope},payload));if(r.ok)await draw();else note.textContent=t('xs.failed','Could not save this change. Try again.');}catch(e){note.textContent=t('xs.failed','Could not save this change. Try again.');}}
  row.appendChild(button(data.paused?t('xs.resume','Resume ratings'):t('xs.pause','Pause ratings'),()=>change({action:'pause',paused:!data.paused})));
  row.appendChild(button(t('xs.reset','Forget all ratings'),()=>change({action:'reset'})));box.append(row,note);
  (data.entries||[]).forEach(e=>{const item=node('div','vx-social');item.append(node('strong','',e.rating+'/10'),node('p','',e.text),node('p','vx-note',e.comment));item.appendChild(button(t('xs.forget','Forget rating'),()=>change({action:'forget',id:e.id})));if(e.rating>=9)item.appendChild(button(t('xs.voice','Use as a voice sample'),()=>change({action:'voice',id:e.id})));box.appendChild(item);});
 }
 await draw();
}

const mounts=new Map();
function mount(host,session,request){
 if(!host||!session||!request)return;
 const key=session+':'+request;
 if(mounts.has(key)&&mounts.get(key).host===host)return;
 const entry={host:host,session:session,request:request,versions:new Map()};mounts.set(key,entry);refresh(entry);
}
async function refresh(entry){
 if(!entry.host.isConnected)return;
 let result;try{result=await call('/api/x/cards',{session_id:entry.session,request_id:entry.request});}catch(e){return;}
 if(!result.ok||!entry.host.isConnected)return;
 for(const a of result.data.cards||[]){
  if(entry.versions.get(a.id)===a.state)continue;
  entry.versions.set(a.id,a.state);
  const previous=entry.host.querySelector('[data-x-action="'+a.id+'"]');if(previous)previous.remove();
  const card=node('section','vx-social vx-approval');card.dataset.xAction=a.id;
  card.appendChild(node('strong','',summary(a)));
  const account=node('p','vx-note','@'+((a.account||{}).username||'?')+' · '+String(a.weighted_length||0)+'/280');account.dir='ltr';card.appendChild(account);
  if(a.text){const words=node('p','',a.text);words.dir='auto';card.appendChild(words);}
  if((a.target||{}).text){const words=node('p','vx-note',a.target.text);words.dir='auto';card.appendChild(words);}
  const targetUrl=String((a.target||{}).url||'');
  if(/^https:\/\/x\.com\/[A-Za-z0-9_\/]+$/.test(targetUrl)){const link=node('a','',t('xact.open_on_x','Open on X ↗'));link.href=targetUrl;link.target='_blank';link.rel='noopener noreferrer';card.appendChild(link);}
  const note=node('p','vx-note');note.setAttribute('role','status');card.appendChild(note);
  if(a.state==='pending'){
   const row=node('div','vx-row');
   async function act(action){
    row.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{const r=await call('/api/x/activity',{action:action,action_id:a.id,...(action==='approve'?{hash:a.hash}:{})});note.textContent=r.data.message||'';
     entry.versions.delete(a.id);await refresh(entry);
     if(!r.ok){const current=entry.host.querySelector('[data-x-action="'+a.id+'"]');const status=current&&current.querySelector('[role=status]');
      if(status&&current.querySelector('.vx-approve'))status.textContent=r.data.state==='usage_limit_reached'?t('xs.limit_reached','Your X send limit has been reached. Check your limits in Settings.'):(r.data.message||t('xact.failed','That did not work. Nothing was sent.'));}
    }catch(e){note.textContent=t('xact.lost_answer','VOOL did not get an answer. It may already have been sent: check Recent below and X before trying again.');}
   }
   const yes=button(t('xact.approve','Approve and send'),()=>act('approve'));yes.classList.add('vx-approve');row.appendChild(yes);
   row.appendChild(button(t('xact.decline','Decline'),()=>act('decline')));card.appendChild(row);rating(card,a);
  }else{note.textContent=state(a);if(a.state==='sent')rating(card,a);}
  entry.host.appendChild(card);
 }
}
setInterval(()=>{for(const [key,e] of mounts){if(!e.host.isConnected)mounts.delete(key);else refresh(e);}},3000);
async function managers(host,scopes){
 try{const stored=await call('/api/x/feedback',{action:'scopes'});if(stored.ok)for(const scope of stored.data.scopes||[])scopes.add(scope);}catch(e){}
 for(const scope of scopes)await manager(host,scope);
}
window.VoolXSocial={summary:summary,state:state,rating:rating,manager:manager,managers:managers,mount:mount};

})();
</script>'''
