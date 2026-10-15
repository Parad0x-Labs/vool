"""The trusted live view displays pixels and inert labels, never remote HTML."""
from __future__ import annotations

import html
import json

from core.i18n import catalog_for, get_locale
from core.ui_palette import DARK_PALETTE_CSS


def render(chat: str, locale: str) -> str:
    cat = catalog_for(locale)
    keys = ["browser", "note", "takeover", "resume", "approve", "refuse", "open", "address", "chat", "fill", "download", "upload", "up", "down", "ready", "approval_required", "running", "stopped", "refused", "failed"]
    labels = {key: cat.text("live." + key) for key in keys}
    labels["private"] = cat.text("private.banner_short")
    labels["stop"] = cat.text("run.stop")
    labels["settings"] = cat.text("sidebar.settings")
    labels["web_off"] = cat.text("vcs.state.egress_refused")
    config = json.dumps({"chat": chat, "labels": labels}, ensure_ascii=False).replace("<", "\\u003c")
    return _PAGE.replace("__PALETTE__", DARK_PALETTE_CSS).replace("__TITLE__", html.escape(labels["browser"])).replace("__CONFIG__", config).replace("__LANG__", html.escape(locale)).replace("__DIR__", (get_locale(locale) or get_locale("en")).direction)


_PAGE = r'''<!doctype html><html lang="__LANG__" dir="__DIR__"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>__PALETTE__
*{box-sizing:border-box}[hidden]{display:none!important}header{position:sticky;top:0;z-index:1;background:var(--panel)}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui}header,nav,section{padding:12px 16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--border)}h1{font-size:18px;margin:0 auto 0 0}p{margin:0;line-height:1.5}button,input,select{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--border);border-radius:7px;padding:9px;min-width:0}button{cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--accent)}button:disabled{opacity:.45}input{flex:1}main{display:grid;grid-template-columns:minmax(0,1fr) 300px}figure{margin:0;padding:12px;min-width:0}img{width:100%;border:1px solid var(--border);border-radius:8px;display:block}aside{padding:16px;border-left:1px solid var(--border);display:grid;align-content:start;gap:10px}aside p{display:flex;gap:8px}aside button{min-height:40px}#pending{white-space:pre-wrap;overflow-wrap:anywhere;padding:12px 0}#nodes{width:100%;margin-bottom:8px}#value{width:100%;margin-bottom:8px}#status{overflow-wrap:anywhere}#url{font:12px ui-monospace;overflow-wrap:anywhere;width:100%}@media(max-width:760px){main{grid-template-columns:1fr}aside{border-left:0;border-top:1px solid var(--border)}}@media(prefers-color-scheme:light){:root{--bg:#f7f8fa;--panel:#fff;--text:#18202a;--border:#cbd1d9;--accent:#2563eb}}
</style><header><h1 id="title"></h1><span id="private" hidden></span><a id="settings" href="/settings#permissions"></a><button id="takeover"></button><button id="resume"></button><button id="stop"></button></header>
<section><p id="note"></p></section><nav><input id="chat" type="hidden"><input id="address" type="url" autocomplete="off"><button id="open"></button></nav>
<section><span id="status" role="status" aria-live="polite"></span><div id="url"></div></section>
<main><figure><img id="frame" alt="" hidden></figure><aside><div id="redirect" hidden><p id="redirectUrl"></p><button id="reviewRedirect"></button></div><div id="pending" hidden></div><button id="approve" hidden></button><button id="refuse" hidden></button><hr><select id="nodes"></select><input id="value" autocomplete="off"><button id="fill"></button><button id="activate"></button><p><button id="up"></button><button id="down"></button></p><button id="download"></button><input id="file" type="file" hidden><button id="upload"></button><a id="save" hidden></a></aside></main>
<script>
const cfg=__CONFIG__, $=id=>document.getElementById(id), L=cfg.labels; let current=null, pending=null, frame='', error=''; L.stopping=L.running;
['title','takeover','resume','stop','note','approve','refuse','open','fill','download','upload','up','down'].forEach(id=>$(id).textContent=L[id==='title'?'browser':id]);
$('reviewRedirect').textContent=L.open; $('private').textContent=L.private; $('settings').textContent=L.settings; $('activate').textContent=L.open; $('save').textContent=L.download;
$('address').placeholder=L.address; $('address').setAttribute('aria-label',L.address);$('chat').placeholder=L.chat;$('chat').setAttribute('aria-label',L.chat);$('chat').value=cfg.chat;
$('nodes').setAttribute('aria-label',L.open);$('value').setAttribute('aria-label',L.fill);$('frame').alt=L.browser;
async function post(path,extra={}) { try {const r=await fetch('/api/browser/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat:$('chat').value,...extra})});const j=await r.json();error=r.ok?'':(j.error==='web_off'?L.web_off:L.failed);await poll();return j;}catch(e){$('status').textContent=L.failed;} }
function propose(op,args){return post('propose',{op,args});}
function paint(j){current=j;pending=j.pending; $('private').hidden=!j.private; $('status').textContent=error||L[j.state]||L.failed; $('url').textContent=j.url||'';
$('approve').hidden=$('refuse').hidden=$('pending').hidden=!pending; const redirect=j.result&&j.result.redirect; $('redirect').hidden=!redirect||!!pending||j.state==='running'; $('redirectUrl').textContent=redirect?redirect.text:'';
if(pending){const a=pending.args; const name=L[pending.op]||L.open; $('pending').textContent=[name,a.url||a.target||'',a.label||'',a.type==='password'?'••••••':(a.text||''),a.file?a.file.name:''].filter(Boolean).join('\n');}else $('pending').textContent='';
$('takeover').disabled=!!j.takeover;$('resume').disabled=!j.takeover;
if(j.frame && j.frame!==frame){frame=j.frame;$('frame').src='data:image/jpeg;base64,'+frame;$('frame').hidden=false;}if(!j.frame){frame='';$('frame').removeAttribute('src');$('frame').hidden=true;}
const selected=$('nodes').value;$('nodes').replaceChildren();(j.nodes||[]).forEach(n=>{const o=document.createElement('option');o.value=n.selector;o.textContent=n.label;$('nodes').appendChild(o);});if([...$('nodes').options].some(o=>o.value===selected))$('nodes').value=selected; const node=(j.nodes||[]).find(n=>n.selector===$('nodes').value);$('value').type=node&&node.type==='password'?'password':'text';
$('save').hidden=!j.download_ready;$('save').href='/api/browser/download?chat='+encodeURIComponent($('chat').value);
}
async function poll(){if(!$('chat').value)return;try{const r=await fetch('/api/browser/state?chat='+encodeURIComponent($('chat').value),{cache:'no-store'});if(r.ok)paint(await r.json());else $('status').textContent=L.failed;}catch(e){$('status').textContent=L.failed;}}
$('reviewRedirect').onclick=()=>{if(current&&current.result&&current.result.redirect)propose('navigate',{url:current.result.redirect.text});};
$('open').onclick=()=>propose('navigate',{url:$('address').value});$('address').onkeydown=e=>{if(e.key==='Enter')$('open').click();};
$('approve').onclick=()=>{if(pending)post('decide',{id:pending.id,approve:true});};$('refuse').onclick=()=>{if(pending)post('decide',{id:pending.id,approve:false});};
$('stop').onclick=()=>post('stop');$('takeover').onclick=()=>post('takeover',{value:true});$('resume').onclick=()=>post('takeover',{value:false});
$('activate').onclick=()=>propose('click',{selector:$('nodes').value});$('fill').onclick=()=>propose('type',{selector:$('nodes').value,text:$('value').value});
$('up').onclick=()=>propose('scroll',{direction:'up'});$('down').onclick=()=>propose('scroll',{direction:'down'});$('download').onclick=()=>propose('download',{url:$('address').value});
$('upload').onclick=()=>$('file').click();$('file').onchange=async()=>{const f=$('file').files[0];if(!f)return;if(f.size>1000000){$('status').textContent=L.failed;return;}const bytes=new Uint8Array(await f.arrayBuffer());let data='';for(const b of bytes)data+=String.fromCharCode(b);await propose('upload',{selector:$('nodes').value,file:{name:f.name,mimeType:f.type||'application/octet-stream',buffer:btoa(data)}});$('file').value='';};
$('nodes').onchange=()=>{const node=current&&(current.nodes||[]).find(n=>n.selector===$('nodes').value);$('value').type=node&&node.type==='password'?'password':'text';};
$('chat').onchange=poll; document.addEventListener('keydown',e=>{if(e.key==='Escape')post('stop');});
window.addEventListener('pagehide',()=>{fetch('/api/browser/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat:$('chat').value}),keepalive:true}).catch(()=>{});});
setInterval(poll,700);poll();
</script></html>'''
