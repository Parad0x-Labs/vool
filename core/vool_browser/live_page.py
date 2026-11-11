"""The trusted live view displays pixels and inert labels, never remote HTML.

The page the owner drives: a toolbar (back, forward, reload, address), the live picture of
the page with its visible controls made clickable in place, a review bar for anything that
still needs a decision, and a list of the controls on the page for keyboard and screen-reader
use. Remote content only ever arrives as a JPEG and as plain-text labels set through
``textContent``; nothing from a page is parsed as HTML here.

What runs at once and what waits is decided on the server (``LiveSession.run_owner_step``):
the pane asks with ``now: true`` for its own steps, and a form submission, upload or download
still comes back as a review.
"""
from __future__ import annotations

import html
import json

from core.i18n import catalog_for, get_locale
from core.ui_palette import APPEARANCE_JS, DARK_PALETTE_CSS, LIGHT_PALETTE_CSS, appearance_html_attrs

_KEYS = (
    "browser", "note", "takeover", "resume", "approve", "refuse", "open", "address", "chat", "fill",
    "download", "upload", "up", "down", "ready", "approval_required", "running", "stopped", "refused",
    "failed", "back", "forward", "reload", "go", "asks", "check_send", "op_click", "op_type", "op_scroll",
    "seconds_left", "empty_title", "empty_body", "closed_body", "off_body", "driving", "elements",
    "no_elements", "send", "save_file", "file_ready", "more", "links", "fields", "buttons", "redirect",
)


def render(chat: str, locale: str, appearance: tuple[str, int] | None = None) -> str:
    cat = catalog_for(locale)
    labels = {key: cat.text("live." + key) for key in _KEYS}
    labels["submit"] = cat.text("composer.send")
    labels["wallet"] = cat.text("home.wallet")
    labels["private"] = cat.text("private.banner_short")
    labels["stop"] = cat.text("run.stop")
    labels["settings"] = cat.text("sidebar.settings")
    labels["web_off"] = cat.text("vcs.state.egress_refused")
    labels["close"] = cat.text("notif.close")
    config = json.dumps({"chat": chat, "labels": labels}, ensure_ascii=False).replace("<", "\\u003c")
    return (
        _PAGE.replace("__PALETTE__", DARK_PALETTE_CSS + "\n" + LIGHT_PALETTE_CSS)
        .replace("__APPEARANCE_ATTRS__", appearance_html_attrs(appearance))
        .replace("__APPEARANCE_JS__", APPEARANCE_JS)
        .replace("__TITLE__", html.escape(labels["browser"]))
        .replace("__CONFIG__", config)
        .replace("__LANG__", html.escape(locale))
        .replace("__DIR__", (get_locale(locale) or get_locale("en")).direction)
    )


# 16x16 line icons drawn in the text colour (no colour emoji, theme-following).
_ICONS = {
    "back": "<path d='M10 3.5 5.5 8l4.5 4.5'/>",
    "forward": "<path d='M6 3.5 10.5 8 6 12.5'/>",
    "reload": "<path d='M13 8a5 5 0 1 1-1.5-3.6'/><path d='M13 2.5v3h-3'/>",
    "lock": "<rect x='3.5' y='7' width='9' height='6.5' rx='1'/><path d='M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7'/>",
    "globe": "<circle cx='8' cy='8' r='5.5'/><path d='M2.5 8h11M8 2.5c1.6 1.6 2.3 3.5 2.3 5.5S9.6 11.9 8 13.5C6.4 11.9 5.7 10 5.7 8S6.4 4.1 8 2.5z'/>",
    "list": "<path d='M5.5 4h8M5.5 8h8M5.5 12h8'/><path d='M2.5 4h.1M2.5 8h.1M2.5 12h.1'/>",
    "more": "<path d='M3.5 8h.1M8 8h.1M12.5 8h.1'/>",
    "hand": "<path d='M5.5 8V3.8a1 1 0 0 1 2 0V7.5M7.5 7V2.8a1 1 0 0 1 2 0V7.5M9.5 7.3V3.8a1 1 0 0 1 2 0v5.4c0 2.6-1.8 4.3-4 4.3-1.6 0-2.6-.7-3.5-2L2.6 8.8a1 1 0 0 1 1.6-1.2L5.5 9'/>",
    "stop": "<rect x='4' y='4' width='8' height='8' rx='1.2'/>",
    "shield": "<path d='M8 2 13 4v4c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4z'/>",
    "download": "<path d='M8 2.5v7.5M5 7.2l3 3 3-3'/><path d='M3 12.8h10'/>",
    "settings": "<circle cx='8' cy='8' r='2'/><path d='M6.9 2h2.2l.4 1.6 1.2.7 1.6-.5 1.1 1.9-1.2 1.1v1.4l1.2 1.1-1.1 1.9-1.6-.5-1.2.7-.4 1.6H6.9l-.4-1.6-1.2-.7-1.6.5-1.1-1.9 1.2-1.1V7.3L2.6 6.2l1.1-1.9 1.6.5 1.2-.7z'/>",
    "wallet": "<path d='M2.5 5a1.5 1.5 0 0 1 1.5-1.5h7.5v2'/><rect x='2.5' y='5.5' width='11' height='7.5' rx='1.2'/><circle cx='10.8' cy='9.2' r='.7'/>",
    "close": "<path d='M4 4l8 8M12 4l-8 8'/>",
}


def _icon(name: str) -> str:
    return ("<svg class='ic' viewBox='0 0 16 16' aria-hidden='true' fill='none' stroke='currentColor' "
            "stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>" + _ICONS[name] + "</svg>")


_PAGE = r'''<!doctype html><html lang="__LANG__" dir="__DIR__"__APPEARANCE_ATTRS__><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>__PALETTE__
*{box-sizing:border-box}[hidden]{display:none!important}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;display:flex;flex-direction:column;min-height:100vh}
button,input,select{font:inherit;color:inherit}
button{cursor:pointer;background:none;border:0;padding:0}
button:disabled{opacity:.35;cursor:default}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ic{width:16px;height:16px;flex:0 0 auto;display:block}
/* toolbar */
.bar{display:flex;align-items:center;gap:6px;padding:8px 10px;background:var(--panel);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:5}
.tb{width:32px;height:32px;border-radius:8px;display:grid;place-items:center;color:var(--muted)}
.tb:not(:disabled):hover{background:var(--field);color:var(--text)}
.tb.on{background:var(--accent-soft);color:var(--accent)}
.omni{flex:1;min-width:0;display:flex;align-items:center;gap:8px;height:34px;padding:0 6px 0 11px;border-radius:17px;background:var(--field);border:1px solid transparent;transition:border-color .15s}
.omni:focus-within{border-color:var(--accent);background:var(--bg)}
.omni .ic{color:var(--muted)}
#address{flex:1;min-width:0;border:0;background:none;outline:none;padding:0;font-size:13.5px}
#address::placeholder{color:var(--muted)}
#open{height:26px;padding:0 12px;border-radius:13px;background:var(--accent);color:var(--accent-ink);font-size:12.5px;font-weight:600}
#open:hover{filter:brightness(1.07)}
.chip{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 9px;border-radius:12px;font-size:12px;white-space:nowrap;background:var(--field);color:var(--muted)}
.chip i{width:7px;height:7px;border-radius:50%;background:var(--muted);display:block}
.chip[data-tone=ok] i{background:var(--live)}
.chip[data-tone=busy] i{background:var(--accent);animation:pulse 1s ease-in-out infinite}
.chip[data-tone=ask] {background:rgba(207,174,110,.16);color:var(--warn)}
.chip[data-tone=ask] i{background:var(--warn)}
.chip[data-tone=bad] i{background:var(--bad)}
@keyframes pulse{50%{opacity:.3}}
#private{background:var(--accent-soft);color:var(--accent)}
.menu-wrap{position:relative}
.menu{position:absolute;inset-inline-end:0;top:38px;min-width:230px;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:5px;box-shadow:0 10px 30px var(--shadow);z-index:20}
.menu button,.menu a{display:flex;align-items:center;gap:10px;width:100%;padding:8px 10px;border-radius:7px;text-align:start;color:var(--text);text-decoration:none;font-size:13px}
.menu button:not(:disabled):hover,.menu a:hover{background:var(--field)}
.menu hr{border:0;border-top:1px solid var(--border);margin:4px 2px}
/* review bar */
.review{margin:10px 12px 0;padding:12px 14px;border-radius:12px;border:1px solid rgba(207,174,110,.45);background:linear-gradient(0deg,rgba(207,174,110,.08),rgba(207,174,110,.08)),var(--panel);display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.review .what{flex:1;min-width:220px}
.review .who{font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--warn);font-weight:600}
.review .verb{font-size:14.5px;margin-top:2px}
.review .target{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted);overflow-wrap:anywhere;margin-top:2px}
#pending{white-space:pre-wrap;overflow-wrap:anywhere;font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted);margin-top:6px}
#pending:empty{display:none}
.review .acts{display:flex;gap:8px;align-items:center;margin-inline-start:auto}
.timer{font-size:12px;color:var(--muted);margin-inline-end:4px;white-space:nowrap}
.btn{height:34px;padding:0 16px;border-radius:9px;border:1px solid var(--border);background:var(--field);font-weight:500}
.btn:hover{border-color:var(--muted)}
.btn.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
.btn.primary:hover{filter:brightness(1.07)}
.notice{margin:10px 12px 0;padding:9px 12px;border-radius:10px;background:var(--field);display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:13px}
.notice .grow{flex:1;min-width:160px;overflow-wrap:anywhere}
/* stage */
main{flex:1;display:flex;min-height:0;gap:0}
.stage{flex:1;min-width:0;padding:12px;display:flex;flex-direction:column}
.viewport{position:relative;border-radius:10px;overflow:hidden;border:1px solid var(--border);background:var(--chat);flex:1;min-height:320px;display:flex;align-items:flex-start;justify-content:center}
.shot{position:relative;width:100%;flex:0 0 auto}
#frame{display:block;width:100%;height:auto;user-select:none;-webkit-user-drag:none}
.shot.busy #frame{opacity:.55;filter:saturate(.6);transition:opacity .2s}
.progress{position:absolute;top:0;left:0;right:0;height:2px;overflow:hidden;z-index:3}
.progress::after{content:'';position:absolute;inset:0;width:35%;background:var(--accent);animation:slide 1.1s ease-in-out infinite}
@keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}
#hot{position:absolute;inset:0}
#hot button{position:absolute;border-radius:4px;outline:1.5px solid transparent;outline-offset:1px;transition:outline-color .1s,background .1s}
#hot button{cursor:pointer}
#hot button:hover,#hot button:focus-visible{outline:0;box-shadow:0 0 0 2px #fff,0 0 0 4px #2f6feb}
#hot button.field{cursor:text}
.blank{position:absolute;inset:0;display:grid;place-items:center;padding:24px;text-align:center}
.blank .inner{max-width:400px}
.blank .orb{width:52px;height:52px;border-radius:50%;display:grid;place-items:center;margin:0 auto 14px;background:var(--accent-soft);color:var(--accent)}
.blank .orb .ic{width:24px;height:24px}
.blank h2{font-size:17px;margin:0 0 6px;font-weight:600}
.blank p{margin:0;color:var(--muted)}
.blank a{color:var(--accent)}
.foot{display:flex;align-items:center;gap:8px;padding-top:8px;color:var(--muted);font-size:12px;min-width:0}
.foot .ic{width:14px;height:14px}
#note{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#url{font:11.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:45%}
/* inline editor */
.editor{position:absolute;z-index:6;background:var(--panel);border:1px solid var(--accent);border-radius:10px;padding:8px;display:flex;gap:6px;flex-wrap:wrap;box-shadow:0 8px 24px var(--shadow);width:min(360px,calc(100% - 16px))}
.editor input,.editor select{flex:1 1 160px;min-width:0;height:32px;padding:0 9px;border-radius:7px;border:1px solid var(--border);background:var(--field)}
.editor .btn{height:32px;padding:0 12px}
/* list of controls */
.side{width:280px;border-inline-start:1px solid var(--border);background:var(--panel);display:flex;flex-direction:column;min-height:0}
.side h3{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);margin:14px 14px 6px;font-weight:600}
.side .scroll{overflow:auto;padding:0 8px 12px}
.side button.item{display:flex;gap:8px;align-items:center;width:100%;text-align:start;padding:7px 8px;border-radius:7px;font-size:13px}
.side button.item:hover{background:var(--field)}
.side .kind{font-size:10.5px;color:var(--muted);border:1px solid var(--border);border-radius:5px;padding:0 5px;flex:0 0 auto}
.side .lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.side .none{color:var(--muted);padding:6px 8px;font-size:13px}
@media(max-width:760px){
  main{flex-direction:column}.side{width:auto;border-inline-start:0;border-top:1px solid var(--border);max-height:45vh}
  .bar{flex-wrap:wrap}.omni{order:5;flex-basis:100%}#state{margin-inline-start:auto}
  .review .acts{width:100%;justify-content:flex-end}
  #url{display:none}
}
</style>
<body>
<header class="bar">
  <h1 id="title" hidden></h1>
  <button class="tb" id="back" type="button">__I_back__</button>
  <button class="tb" id="forward" type="button">__I_forward__</button>
  <button class="tb" id="reload" type="button">__I_reload__</button>
  <form class="omni" id="omni" autocomplete="off"><span id="lock">__I_globe__</span><input id="address" type="text" inputmode="url" spellcheck="false" autocomplete="off"><button id="open" type="submit"></button></form>
  <span class="chip" id="private" hidden></span>
  <span class="chip" id="state"><i></i><span id="status" role="status" aria-live="polite"></span></span>
  <button class="tb" id="list" type="button" aria-pressed="false">__I_list__</button>
  <button class="tb" id="takeover" type="button">__I_hand__</button>
  <span class="menu-wrap"><button class="tb" id="moreBtn" type="button" aria-haspopup="true" aria-expanded="false">__I_more__</button>
    <div class="menu" id="menu" role="menu" hidden>
      <button id="resume" type="button" role="menuitem">__I_hand__<span></span></button>
      <button id="download" type="button" role="menuitem">__I_download__<span></span></button>
      <hr>
      <a id="settings" href="/settings#permissions" target="_top" role="menuitem">__I_settings__<span></span></a>
      <a id="wallet" href="/settings#wallet" target="_top" role="menuitem">__I_wallet__<span></span></a>
      <hr>
      <button id="stop" type="button" role="menuitem">__I_stop__<span></span></button>
    </div></span>
  <button class="tb" id="closePane" type="button" hidden>__I_close__</button>
</header>
<input id="chat" type="hidden">
<section class="review" id="review" hidden aria-live="assertive">
  <div class="what"><div class="who" id="who"></div><div class="verb" id="verb"></div><div class="target" id="target"></div><div id="pending"></div></div>
  <div class="acts"><span class="timer" id="timer"></span><button class="btn" id="refuse" type="button"></button><button class="btn primary" id="approve" type="button"></button></div>
</section>
<section class="notice" id="redirect" hidden><span class="grow" id="redirectUrl"></span><button class="btn" id="reviewRedirect" type="button"></button></section>
<section class="notice" id="driving" hidden><span class="grow" id="drivingText"></span><button class="btn" id="handBack" type="button"></button></section>
<section class="notice" id="ready" hidden><span class="grow" id="readyText"></span><a class="btn primary" id="save" download style="display:inline-flex;align-items:center;text-decoration:none" hidden></a></section>
<main>
  <div class="stage">
    <div class="viewport" id="viewport">
      <div class="progress" id="progress" hidden></div>
      <div class="shot" id="shot"><img id="frame" alt="" hidden draggable="false"><div id="hot"></div></div>
      <div class="blank" id="blank"><div class="inner"><div class="orb">__I_globe__</div><h2 id="blankTitle"></h2><p id="blankBody"></p></div></div>
    </div>
    <div class="foot">__I_shield__<span id="note"></span><span id="url"></span></div>
  </div>
  <aside class="side" id="side" hidden><h3 id="sideTitle"></h3><div class="scroll" id="items"></div></aside>
</main>
<input id="file" type="file" hidden>
<script>
__APPEARANCE_JS__
const cfg=__CONFIG__, $=id=>document.getElementById(id), L=cfg.labels;
let current=null, pending=null, frame='', error='', editing=null, uploadTarget='', wheelAt=0, lastPending='';
const TONE={ready:'ok',running:'busy',approval_required:'ask',stopped:'',refused:'bad'};
// Static labels.
$('title').textContent=L.browser; document.title=L.browser;
[['back',L.back],['forward',L.forward],['reload',L.reload],['list',L.elements],['takeover',L.takeover],['moreBtn',L.more]].forEach(([id,t])=>{$(id).title=t;$(id).setAttribute('aria-label',t);});
$('open').textContent=L.go; $('address').placeholder=L.address; $('address').setAttribute('aria-label',L.address);
$('resume').lastChild.textContent=L.resume; $('download').lastChild.textContent=L.download; $('settings').lastChild.textContent=L.settings;
$('wallet').lastChild.textContent=L.wallet; $('stop').lastChild.textContent=L.stop; $('private').textContent=L.private;
$('approve').textContent=L.approve; $('refuse').textContent=L.refuse; $('reviewRedirect').textContent=L.open; $('handBack').textContent=L.resume;
$('drivingText').textContent=L.driving; $('readyText').textContent=L.file_ready; $('save').textContent=L.save_file; $('sideTitle').textContent=L.elements;
$('note').textContent=L.note; $('frame').alt=L.browser; $('chat').value=cfg.chat;

async function post(path,extra={}) {
  try {
    const r=await fetch('/api/browser/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat:$('chat').value,...extra})});
    const j=await r.json(); error=r.ok?'':(j.error==='web_off'?L.web_off:L.failed); await poll(); return j;
  } catch(e) { error=L.failed; paintStatus(); }
}
// The pane's own steps run at once; the server still answers a form, upload or download with a review.
function step(op,args){closeEditor();return post('propose',{op,args,now:true});}
function host(u){try{return new URL(u).host;}catch(e){return u||'';}}
function verbFor(p){
  if(p.submission) return L.submit;
  return {navigate:L.open,click:L.op_click,type:L.op_type,scroll:L.op_scroll,download:L.download,upload:L.upload}[p.op]||L.open;
}
function paintStatus(){
  const j=current||{}; const s=j.state||'stopped';
  $('status').textContent=error||L[s]||L.failed;
  $('state').dataset.tone=error?'bad':(TONE[s]!==undefined?TONE[s]:'bad');
}
function paint(j){
  current=j; pending=j.pending; const s=j.state; const busy=s==='running';
  paintStatus();
  $('private').hidden=!j.private;
  // Address bar mirrors the page unless the owner is typing in it.
  if(document.activeElement!==$('address')) $('address').value=j.url||'';
  $('lock').innerHTML=''; $('lock').appendChild(icon(j.url&&j.url.startsWith('https:')?'lock':'globe'));
  $('url').textContent=j.title||host(j.url);
  $('back').disabled=!j.can_back||busy||!!pending; $('forward').disabled=!j.can_forward||busy||!!pending; $('reload').disabled=!j.url||busy||!!pending;
  // Review bar.
  $('review').hidden=!pending;
  if(pending){
    const a=pending.args||{};
    $('who').textContent=pending.actor==='agent'?L.asks:L.check_send;
    $('verb').textContent=verbFor(pending)+(a.label?(' · '+a.label):'');
    $('target').textContent=a.url||a.target||'';
    $('pending').textContent=[a.type==='password'?'••••••':(a.text||''),a.file?a.file.name:'',...(pending.submission||[]).map(f=>f.name+' = '+(f.secret?'••••••':f.value)+(f.file?' ('+new Intl.NumberFormat(document.documentElement.lang,{style:'unit',unit:'byte',unitDisplay:'short'}).format(f.bytes)+')':''))].filter(Boolean).join('\n');
    $('timer').textContent=typeof pending.expires_in==='number'?L.seconds_left.replace('{seconds}',String(pending.expires_in)):'';
    if(lastPending!==pending.id){lastPending=pending.id; try{$('approve').focus({preventScroll:true});}catch(e){}}
  } else lastPending='';
  const redirect=j.result&&j.result.redirect; $('redirect').hidden=!redirect||!!pending||busy; $('redirectUrl').textContent=redirect?(L.redirect+' '+redirect.text):'';
  $('driving').hidden=!j.takeover; $('takeover').classList.toggle('on',!!j.takeover); $('takeover').disabled=!!j.takeover; $('resume').disabled=!j.takeover;
  $('takeover').setAttribute('aria-pressed',j.takeover?'true':'false');
  $('ready').hidden=!j.download_ready; $('save').hidden=!j.download_ready; $('save').href='/api/browser/download?chat='+encodeURIComponent($('chat').value);
  $('progress').hidden=!busy; $('shot').classList.toggle('busy',busy);
  if(j.frame && j.frame!==frame){frame=j.frame;$('frame').src='data:image/jpeg;base64,'+frame;$('frame').hidden=false;}
  if(!j.frame){frame='';$('frame').removeAttribute('src');$('frame').hidden=true;}
  const blank=!j.frame; $('blank').hidden=!blank||busy;
  if(blank){
    const off=s==='web_off'||error===L.web_off;
    $('blankTitle').textContent=off?L.web_off:L.empty_title;
    $('blankBody').textContent=off?L.off_body:(j.closed&&j.id?L.closed_body:L.empty_body);
  }
  fitShot();
  paintHot(j,busy||!!pending);
  paintList(j);
}
// The picture keeps the page's own proportions and fits the pane, so a click lands where it looks.
function fitShot(){const j=current||{};const [vw,vh]=j.view||[1100,720];const vp=$('viewport');
  const w=Math.max(120,Math.min(vp.clientWidth,(vp.clientHeight-2)*vw/vh));$('shot').style.width=w+'px';}
window.addEventListener('resize',fitShot);
function icon(name){const t=document.createElement('template');t.innerHTML=ICONS[name];return t.content.firstChild;}
const ICONS={lock:`__I_lock__`,globe:`__I_globe__`};
function kindOf(n){
  if(['input','textarea','select'].includes(n.tag)&&!['submit','button','image','reset','checkbox','radio'].includes(n.type)) return n.type==='file'?'file':'field';
  if(n.tag==='a') return 'link'; return 'button';
}
function nameOf(n){return (n.label||n.name||n.type||n.tag||'').trim()||n.tag;}
function paintHot(j,locked){
  const hot=$('hot'); const sig=JSON.stringify([j.revision,locked,(j.nodes||[]).length]);
  if(hot.dataset.sig===sig) return; hot.dataset.sig=sig; hot.replaceChildren();
  if(locked||!j.frame) return;
  const [vw,vh]=j.view||[1100,720];
  (j.nodes||[]).forEach(n=>{
    if(!n.box||n.payment) return;
    const [x,y,w,h]=n.box, b=document.createElement('button'); b.type='button';
    b.className=kindOf(n); b.style.left=(x/vw*100)+'%'; b.style.top=(y/vh*100)+'%'; b.style.width=(w/vw*100)+'%'; b.style.height=(h/vh*100)+'%';
    b.title=nameOf(n); b.setAttribute('aria-label',nameOf(n)); b.onclick=()=>act(n,b); hot.appendChild(b);
  });
}
function paintList(j){
  const items=$('items'); const sig=JSON.stringify([j.revision,(j.nodes||[]).length]);
  if(items.dataset.sig===sig) return; items.dataset.sig=sig; items.replaceChildren();
  const nodes=(j.nodes||[]).filter(n=>!n.payment);
  if(!nodes.length){const p=document.createElement('div');p.className='none';p.textContent=L.no_elements;items.appendChild(p);return;}
  const groups=[['link',L.links],['field',L.fields],['button',L.buttons],['file',L.upload]];
  groups.forEach(([kind,title])=>{
    const list=nodes.filter(n=>kindOf(n)===kind); if(!list.length) return;
    const h=document.createElement('h3');h.textContent=title;items.appendChild(h);
    list.forEach(n=>{const b=document.createElement('button');b.type='button';b.className='item';
      const lbl=document.createElement('span');lbl.className='lbl';lbl.textContent=nameOf(n);b.appendChild(lbl);
      b.onclick=()=>act(n,null);items.appendChild(b);});
  });
}
// One control acted on, from the picture or from the list.
function act(n,anchor){
  if(!current||current.state==='running'||current.pending) return;
  const kind=kindOf(n);
  if(kind==='file'){uploadTarget=n.selector;$('file').click();return;}
  if(kind==='field'){openEditor(n,anchor);return;}
  step('click',{selector:n.selector});
}
function openEditor(n,anchor){
  closeEditor();
  const box=document.createElement('div'); box.className='editor'; box.setAttribute('role','dialog'); box.setAttribute('aria-label',nameOf(n));
  let input;
  if(n.tag==='select'){input=document.createElement('select');(n.options||[]).forEach(o=>{const opt=document.createElement('option');opt.value=o.value;opt.textContent=o.label||o.value;input.appendChild(opt);});if(n.value!==undefined)input.value=n.value;}
  else{input=document.createElement('input');input.type=n.type==='password'?'password':'text';input.value=n.value||'';input.placeholder=nameOf(n);input.autocomplete='off';}
  input.setAttribute('aria-label',nameOf(n));
  const fill=document.createElement('button');fill.type='button';fill.className='btn';fill.textContent=L.fill;
  fill.onclick=()=>step('type',{selector:n.selector,text:input.value});
  box.append(input,fill);
  if(n.form>=0&&n.tag!=='select'){const send=document.createElement('button');send.type='button';send.className='btn primary';send.textContent=L.send;
    send.onclick=()=>step('type',{selector:n.selector,text:input.value,submit:true});box.appendChild(send);}
  input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();fill.click();}if(e.key==='Escape'){e.stopPropagation();closeEditor();}});
  const shot=$('shot');
  if(anchor){const r=anchor.getBoundingClientRect(), s=shot.getBoundingClientRect();
    box.style.left=Math.max(8,Math.min(r.left-s.left,s.width-370))+'px'; box.style.top=Math.min(r.bottom-s.top+6,Math.max(8,s.height-60))+'px';}
  else{box.style.left='8px';box.style.top='8px';}
  shot.appendChild(box); editing=box; input.focus(); if(input.select)input.select();
}
function closeEditor(){if(editing){editing.remove();editing=null;}}
async function poll(){
  if(!$('chat').value)return;
  try{const r=await fetch('/api/browser/state?chat='+encodeURIComponent($('chat').value),{cache:'no-store'});if(r.ok)paint(await r.json());else{error=L.failed;paintStatus();}}
  catch(e){error=L.failed;paintStatus();}
}
// Toolbar.
$('omni').onsubmit=e=>{e.preventDefault();const v=$('address').value.trim();if(!v)return;const url=/^[a-z][a-z0-9+.-]*:/i.test(v)?v:'https://'+v;$('address').blur();step('navigate',{url});};
$('back').onclick=()=>step('back',{}); $('forward').onclick=()=>step('forward',{}); $('reload').onclick=()=>step('reload',{});
$('list').onclick=()=>{const open=$('side').hidden;$('side').hidden=!open;$('list').classList.toggle('on',open);$('list').setAttribute('aria-pressed',String(open));};
$('takeover').onclick=()=>post('takeover',{value:true}); $('resume').onclick=()=>{closeMenu();post('takeover',{value:false});}; $('handBack').onclick=()=>post('takeover',{value:false});
$('moreBtn').onclick=e=>{e.stopPropagation();const open=$('menu').hidden;$('menu').hidden=!open;$('moreBtn').setAttribute('aria-expanded',String(open));};
function closeMenu(){$('menu').hidden=true;$('moreBtn').setAttribute('aria-expanded','false');}
document.addEventListener('click',e=>{if(!$('menu').hidden&&!$('menu').contains(e.target))closeMenu();});
$('stop').onclick=()=>{closeMenu();post('stop');};
$('download').onclick=()=>{closeMenu();const v=$('address').value.trim();if(v)post('propose',{op:'download',args:{url:v}});};
// Review.
$('approve').onclick=()=>{if(pending)post('decide',{id:pending.id,approve:true});};
$('refuse').onclick=()=>{if(pending)post('decide',{id:pending.id,approve:false});};
$('reviewRedirect').onclick=()=>{if(current&&current.result&&current.result.redirect)step('navigate',{url:current.result.redirect.text});};
// Scroll the page with the wheel over the picture, at most one step at a time.
$('viewport').addEventListener('wheel',e=>{
  if(!current||!current.frame||current.state==='running'||current.pending||editing) return;
  e.preventDefault(); const now=Date.now(); if(now-wheelAt<600||Math.abs(e.deltaY)<4) return; wheelAt=now;
  step('scroll',{direction:e.deltaY<0?'up':'down'});
},{passive:false});
$('file').onchange=async()=>{const f=$('file').files[0];if(!f)return;if(f.size>1000000){error=L.failed;paintStatus();return;}
  const bytes=new Uint8Array(await f.arrayBuffer());let data='';for(const b of bytes)data+=String.fromCharCode(b);
  await post('propose',{op:'upload',args:{selector:uploadTarget,file:{name:f.name,mimeType:f.type||'application/octet-stream',buffer:btoa(data)}}});$('file').value='';};
// Inside the chat the pane is a framed surface: Escape and the close button put it away (the
// session keeps running); opened on its own, Escape stops the session as it always did.
const FRAMED=window.parent!==window;
function hostSay(msg){try{window.parent.postMessage(msg,location.origin);}catch(e){}}
if(FRAMED){$('closePane').hidden=false;$('closePane').title=L.close;$('closePane').setAttribute('aria-label',L.close);$('closePane').onclick=()=>hostSay({type:'vool-browser-close'});
  [['settings','permissions'],['wallet','wallet']].forEach(([id,section])=>{$(id).onclick=e=>{e.preventDefault();closeMenu();hostSay({type:'vool-setup-open-settings',section});};});}
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape') return;
  if(e.target===$('address')){$('address').value=(current&&current.url)||'';$('address').blur();return;}
  if(editing){closeEditor();return;} if(!$('menu').hidden){closeMenu();return;}
  if(FRAMED){hostSay({type:'vool-browser-close'});return;}
  post('stop');
});
document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key==='l'){e.preventDefault();$('address').focus();$('address').select();}
  if(e.altKey&&e.key==='ArrowLeft'&&!$('back').disabled){e.preventDefault();$('back').click();}
  if(e.altKey&&e.key==='ArrowRight'&&!$('forward').disabled){e.preventDefault();$('forward').click();}});
window.addEventListener('pagehide',()=>{fetch('/api/browser/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat:$('chat').value}),keepalive:true}).catch(()=>{});});
setInterval(poll,700);poll();
</script></body></html>'''

for _name, _svg in list(_ICONS.items()):
    _PAGE = _PAGE.replace("__I_" + _name + "__", _icon(_name))
