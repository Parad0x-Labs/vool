"""Owner-local X ratings and approvals. UI state never joins chat history."""
from __future__ import annotations


def render_x_social_fragment() -> str:
    return r'''<style>
.vx-social{display:flex;flex-direction:column;gap:10px;border:1px solid var(--border);border-radius:12px;padding:14px;margin:10px 0;color:var(--ink);background:var(--panel)}
.vx-social p{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.vx-social button,.vx-social input,.vx-social textarea,.vx-social select{font:inherit;border:1px solid var(--border);border-radius:8px;padding:7px 10px;background:var(--field,var(--bg));color:var(--ink)}
.vx-social button{cursor:pointer}.vx-social button:disabled{opacity:.5;cursor:default}.vx-social .vx-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.vx-social textarea{width:100%;box-sizing:border-box;resize:vertical}.vx-social .vx-note{font-size:12px;color:var(--muted)}
</style><script>
(function(){'use strict';
function t(k,f){try{const v=VOOLT(k);if(v&&v!==k)return v;}catch(e){}return f;}
function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;}
async function call(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();return {ok:r.ok&&j.ok!==false,data:j};}
function button(label,fn){const b=node('button','',label);b.type='button';b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}finally{b.disabled=false;}});return b;}
function rating(host,a){
 if(!a.text||!['post','reply','quote'].includes(a.kind))return;
 const form=node('div','vx-social vx-rating');const label=node('label','',t('xs.rating','Your rating'));const score=node('select');score.setAttribute('aria-label',t('xs.rating','Your rating'));
 score.appendChild(new Option(t('xs.choose','Choose 1–10'),''));for(let i=1;i<=10;i++)score.appendChild(new Option(String(i),String(i)));label.appendChild(score);form.appendChild(label);
 const comment=node('textarea');comment.rows=2;comment.maxLength=500;comment.placeholder=t('xs.comment','Comment (optional)');comment.setAttribute('aria-label',comment.placeholder);form.appendChild(comment);
 const note=node('p','vx-note');note.setAttribute('role','status');const row=node('div','vx-row');
 row.appendChild(button(t('xs.save','Save rating'),async()=>{
   if(!score.value){score.focus();return;}
   try{const r=await call('/api/x/feedback',{action:'save',action_id:a.id,rating:Number(score.value),comment:comment.value});note.textContent=r.ok?t('xs.saved','Saved.'):t('xs.failed','Could not save this change. Try again.');
   if(r.ok&&Number(score.value)>=9&&!row.querySelector('[data-voice]')){const v=button(t('xs.voice','Use as a voice sample'),async()=>{const o=await call('/api/x/feedback',{action:'voice',scope:a.feedback_scope||'global',id:r.data.id});note.textContent=o.ok?t('xs.saved','Saved.'):t('xs.failed','Could not save this change. Try again.');});v.dataset.voice='1';row.appendChild(v);}
   }catch(e){note.textContent=t('xs.failed','Could not save this change. Try again.');}
 }));form.append(row,note);host.appendChild(form);
}
async function manager(host,scope){
 const box=node('section','vx-social');host.appendChild(box);
 async function draw(){
  let out;try{out=await call('/api/x/feedback',{action:'list',scope:scope});}catch(e){return;}
  if(!out.ok)return;box.replaceChildren(node('strong','',t('xs.ratings','Your ratings')));
  const data=out.data;const row=node('div','vx-row');const note=node('p','vx-note');note.setAttribute('role','status');
  async function change(payload){try{const r=await call('/api/x/feedback',Object.assign({scope:scope},payload));if(r.ok)await draw();else note.textContent=t('xs.failed','Could not save this change. Try again.');}catch(e){note.textContent=t('xs.failed','Could not save this change. Try again.');}}
  row.appendChild(button(data.paused?t('xs.resume','Resume ratings'):t('xs.pause','Pause ratings'),()=>change({action:'pause',paused:!data.paused})));
  row.appendChild(button(t('xs.reset','Forget all ratings'),()=>change({action:'reset'})));box.append(row,note);
  (data.entries||[]).forEach(e=>{const item=node('div','vx-social');item.append(node('strong','',e.rating+'/10'),node('p','',e.text),node('p','vx-note',e.comment));item.appendChild(button(t('xs.forget','Forget rating'),()=>change({action:'forget',id:e.id})));if(e.rating>=9)item.appendChild(button(t('xs.voice','Use as a voice sample'),()=>change({action:'voice',id:e.id})));box.appendChild(item);});
 }
 await draw();
}
window.VoolXSocial={rating:rating,manager:manager};
})();
</script>'''
