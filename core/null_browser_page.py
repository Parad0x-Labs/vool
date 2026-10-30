"""
core/null_browser_page.py
=========================
Renders the Web0 (.null) browser — an HTML UI that resolves a .null NAME to its
Arweave content and loads it in a sandboxed frame, with an address bar, back/
forward, favorites, and history. Also keeps a collapsible null:// task-dispatch
panel for the compute market.

Served at GET /web0 (and /null-browser) by the VOOL API server on whatever port it runs.
Resolution goes to GET /api/web0/resolve?name=…; task dispatch to POST /api/null, both on the
origin that served the page (falling back to localhost:11435 when that origin has no VOOL API).

Every sentence the page shows goes through the i18n catalog (web0.* keys, 16 locales). The
locale's bootstrap body is prepended INSIDE this single <script> (the chat-page pattern), so
VOOLT is defined before any page statement runs and the page keeps exactly one script block —
the node harness of tests/test_no_fake_empty_phone.py executes block 0 alone.
"""
from __future__ import annotations

from core.ui_palette import DARK_PALETTE_CSS

# A raw string: the page script carries "\n" escapes inside JS string literals, and a plain
# Python string turned them into real line breaks, which stopped the whole script from parsing.
_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title data-i18n="web0.title">VOOL · .null browser</title>
<style>
__VOOL_PALETTE_CSS__
  :root { --panel2:var(--field); --green:#4c4; --amber:#da3; --red:#f44;
    --purple:#a8f; --radius:6px; --font:-apple-system,"Inter",system-ui,"Segoe UI",Roboto,sans-serif;
    --mono:ui-monospace,Menlo,monospace; }
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
  html,body{height:100%;}
  body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;
    display:flex;flex-direction:column;height:100vh;}
  .toolbar{display:flex;align-items:center;gap:6px;padding:8px 10px;
    border-bottom:1px solid var(--border);background:var(--panel);}
  .navbtn{background:var(--panel2);color:var(--text);border:1px solid var(--border);
    border-radius:var(--radius);width:34px;height:32px;font-size:15px;cursor:pointer;
    transition:border-color .15s,opacity .15s;}
  .navbtn:disabled{opacity:.35;cursor:default;}
  .navbtn:hover:not(:disabled){border-color:var(--accent);}
  #addr{flex:1;background:var(--panel2);border:1px solid var(--border);color:var(--text);
    font-family:var(--font);font-size:14px;border-radius:var(--radius);padding:8px 12px;
    outline:none;transition:border-color .15s;}
  #addr:focus{border-color:var(--accent);}
  /* The address field shrinks instead of pushing the buttons off a narrow screen. */
  #addr{min-width:0;}
  .toolbar>button,.toolbar>.spinner{flex-shrink:0;}
  /* Phone width: the buttons keep the first row, the address and Go get a full row of their own. */
  @media (max-width:480px){
    .toolbar{flex-wrap:wrap;}
    #addr{order:1;flex:1 1 calc(100% - 80px);}
    #spin{order:2;}
    #go{order:3;}
  }
  #go{background:var(--accent);color:#000;border:none;border-radius:var(--radius);
    padding:8px 16px;font-family:var(--font);font-size:13px;font-weight:bold;cursor:pointer;}
  #go:hover{opacity:.85;}
  #star,#opentab{background:var(--panel2);border:1px solid var(--border);border-radius:var(--radius);
    width:34px;height:32px;font-size:15px;cursor:pointer;color:var(--muted);}
  #star.on{color:var(--amber);border-color:var(--amber);}
  #opentab:hover:not(:disabled){border-color:var(--accent);color:var(--accent);}
  #opentab:disabled{opacity:.35;cursor:default;}
  .navbtn:focus-visible,#go:focus-visible,#star:focus-visible,#opentab:focus-visible,
  .chip:focus-visible,.chipwrap .x:focus-visible,.adv summary:focus-visible,
  .status summary:focus-visible{outline:2px solid var(--accent);outline-offset:1px;}
  .favbar{display:flex;gap:6px;flex-wrap:wrap;padding:6px 10px;border-bottom:1px solid var(--border);
    background:var(--panel);min-height:34px;align-items:center;}
  .favbar .lbl{font-size:12px;color:var(--muted);margin-right:4px;}
  /* A favourite is two real buttons (navigate + remove) in one wrap span: keyboard focus,
     Enter/Space, and a visible focus ring come with the platform button, not a span-onclick. */
  .chipwrap{display:inline-flex;align-items:center;}
  .chip{background:var(--panel2);border:1px solid var(--border);border-radius:20px;
    padding:3px 10px;font-family:var(--font);font-size:12px;color:var(--text);cursor:pointer;
    transition:border-color .15s;display:inline-flex;align-items:center;text-align:left;
    max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .chip:hover{border-color:var(--accent);}
  .chipwrap .x{background:none;border:none;color:var(--muted);margin-left:2px;font-family:var(--font);
    font-size:12px;cursor:pointer;padding:3px 5px;border-radius:10px;}
  .chipwrap .x:hover{color:var(--red);}
  /* The status line is a column: the plain standing row, then the details fold under it. */
  .status{display:flex;flex-direction:column;gap:2px;padding:5px 12px;font-size:11px;color:var(--muted);
    border-bottom:1px solid var(--border);background:var(--panel);}
  .status .line{display:flex;gap:14px;flex-wrap:wrap;align-items:center;}
  .status b{color:var(--green);font-weight:normal;}
  .status .k{color:var(--muted);}
  .status details{display:inline;}
  .status summary{cursor:pointer;list-style:none;color:var(--muted);display:inline;}
  .status summary::-webkit-details-marker{display:none;}
  .status .s-rows{display:flex;gap:14px;flex-wrap:wrap;padding:4px 0 2px;}
  .stage{flex:1;position:relative;background:#000;overflow:hidden;}
  #frame{width:100%;height:100%;border:0;background:#fff;}
  #placeholder{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:10px;color:var(--muted);text-align:center;padding:24px;}
  #placeholder h2{color:var(--ink);font-size:20px;font-weight:650;
    max-width:min(88vw,640px);overflow-wrap:anywhere;}
  #placeholder details{font-size:12px;max-width:520px;line-height:1.7;display:block;}
  #placeholder summary{cursor:pointer;color:var(--muted);}
  #placeholder .hint{font-size:12px;max-width:520px;line-height:1.7;}
  .spinner{width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);
    border-radius:50%;animation:spin .6s linear infinite;display:none;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .adv{border-top:1px solid var(--border);background:var(--panel);}
  .adv summary{cursor:pointer;padding:8px 12px;font-size:12px;color:var(--muted);list-style:none;}
  .adv summary::-webkit-details-marker{display:none;}
  .adv .body{padding:10px 12px;display:flex;flex-direction:column;gap:8px;}
  .adv .row{display:flex;gap:8px;}
  .adv input,.adv textarea{flex:1;background:var(--panel2);border:1px solid var(--border);
    color:var(--text);font-family:var(--font);font-size:12px;border-radius:var(--radius);padding:7px 10px;outline:none;}
  .adv button{background:var(--accent);color:#000;border:none;border-radius:var(--radius);
    padding:7px 14px;font-family:var(--font);font-size:12px;font-weight:bold;cursor:pointer;white-space:nowrap;}
  #dispatch-out{font-family:var(--mono);background:#000;border:1px solid var(--border);border-radius:var(--radius);
    padding:10px;min-height:60px;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.6;}
</style>
</head>
<body>
<div class="toolbar">
  <button class="navbtn" id="back" title="Back" data-i18n-title="web0.nav.back"
          aria-label="Back" data-i18n-aria-label="web0.nav.back">&#9664;</button>
  <button class="navbtn" id="fwd" title="Forward" data-i18n-title="web0.nav.forward"
          aria-label="Forward" data-i18n-aria-label="web0.nav.forward">&#9654;</button>
  <button class="navbtn" id="reload" title="Reload" data-i18n-title="web0.nav.reload"
          aria-label="Reload" data-i18n-aria-label="web0.nav.reload">&#8635;</button>
  <input id="addr" type="text" value="web0.null" autocomplete="off" spellcheck="false"
         placeholder="A .null name, for example web0.null" data-i18n-placeholder="web0.addr.placeholder"
         aria-label=".null name" data-i18n-aria-label="web0.addr.label"/>
  <div class="spinner" id="spin"></div>
  <button id="go" data-i18n="web0.go">Go</button>
  <button id="star" title="Add to favourites" data-i18n-title="web0.star.add"
          aria-label="Add to favourites" data-i18n-aria-label="web0.star.add">&#9733;</button>
  <button id="opentab" title="Open this site in a normal browser tab. Use this when the site needs your wallet: wallets cannot connect inside the preview here." data-i18n-title="web0.open_tab.hint"
          aria-label="Open in a browser tab" data-i18n-aria-label="web0.open_tab.label" disabled>&#8599;</button>
</div>

<div class="favbar" id="favbar"><span class="lbl" data-i18n="web0.fav.label">Favourites</span></div>

<div class="status" id="status" style="display:none">
  <div class="line">
    <!-- The standing row shows plain words only: the name the person typed, and whether the
         site takes payments. Raw ledger values (owner prefix, tx id, endpoint URL) sit behind
         the Details toggle; what the server records or pays is untouched. -->
    <span><span class="k" data-i18n="web0.status.site">Site name</span> <b id="s-name">-</b></span>
    <span id="s-pays" style="display:none" data-i18n="web0.status.pays">Takes payments</span>
    <details id="s-details">
      <summary data-i18n="web0.status.details">See details</summary>
      <div class="s-rows">
        <span><span class="k" data-i18n="web0.status.owner">Owner:</span> <b id="s-owner">-</b></span>
        <span><span class="k" data-i18n="web0.status.tx">Stored copy:</span> <b id="s-tx">-</b></span>
        <span id="s-x402-wrap" style="display:none"><span class="k" data-i18n="web0.status.payments">Payments:</span> <b id="s-x402">-</b></span>
      </div>
    </details>
  </div>
</div>

<div class="stage">
  <iframe id="frame" sandbox="allow-scripts allow-forms allow-popups allow-same-origin"
          referrerpolicy="no-referrer" style="display:none"></iframe>
  <div id="placeholder">
    <h2 data-i18n="web0.open.title">Open a .null site</h2>
    <div class="hint" data-i18n="web0.open.hint">Type its name, for example web0.null, and press Go.</div>
    <details><summary data-i18n="web0.how.summary">How it works</summary>
      <span data-i18n="web0.how.body">A .null name is looked up on the Solana network, which points to the site's pages stored on Arweave. VOOL fetches them and shows them here, in a preview that cannot touch your wallet. A name with no pages yet shows a note instead.</span></details>
  </div>
</div>

<details class="adv">
  <summary data-i18n="web0.adv.summary">Advanced: send a task to a null:// address</summary>
  <div class="body">
    <div class="row">
      <input id="uri-input" type="text" value="null://task/hello"
             placeholder="null://task/code-review?price=0.001" autocomplete="off" spellcheck="false"/>
      <button id="dispatch-btn" data-i18n="web0.adv.send">Send</button>
    </div>
    <textarea id="prompt-area" rows="2" placeholder="Optional: describe the task in your own words"
              data-i18n-placeholder="web0.adv.prompt_placeholder"></textarea>
    <div id="dispatch-out" data-i18n="web0.adv.out_idle">Type a null:// address and press Send.</div>
  </div>
</details>

<script>
// Talk to the server that served this page (whatever its port). A page opened from a file, or
// from a server without the VOOL API (the Meet server also serves /web0), falls back to the
// default local API port. window.__VOOL_API__ overrides both.
const VOOL_FALLBACK_API = "http://localhost:11435";
let VOOL_API = window.__VOOL_API__ || (/^https?:$/.test(window.location.protocol) ? "" : VOOL_FALLBACK_API);
async function voolFetch(path, options){
  const resp = await fetch(VOOL_API + path, options);
  const isJson = (resp.headers.get("content-type") || "").includes("json");
  if(!isJson && VOOL_API === "" && !window.__VOOL_API__){
    VOOL_API = VOOL_FALLBACK_API;
    return fetch(VOOL_API + path, options);
  }
  return resp;
}
const LS_FAV = "web0.favorites.v1", LS_HIST = "web0.history.v1";
let stack = [], stackPos = -1;
let currentGatewayUrl = "";   // real top-level URL of the loaded .null site (for "open in a real tab")

function normName(raw){
  let n = (raw||"").trim().toLowerCase();
  for(const p of ["null://","web0://","https://","http://"]){ if(n.startsWith(p)){ n=n.slice(p.length); break; } }
  n = n.split("/")[0].split("?")[0].trim();
  if(n.endsWith(".null")) n = n.slice(0,-5);
  return n.trim();
}
function loadJSON(key,def){ try{ return JSON.parse(localStorage.getItem(key)) || def; }catch(e){ return def; } }
function saveJSON(key,val){ try{ localStorage.setItem(key, JSON.stringify(val)); }catch(e){} }

function renderFavs(){
  const favs = loadJSON(LS_FAV, []);
  const bar = document.getElementById("favbar");
  bar.innerHTML = "";
  const lbl = document.createElement("span"); lbl.className="lbl"; lbl.textContent=VOOLT("web0.fav.label"); bar.appendChild(lbl);
  if(!favs.length){ const s=document.createElement("span"); s.style.cssText="font-size:12px;color:var(--muted)"; s.textContent=VOOLT("web0.fav.none"); bar.appendChild(s); return; }
  favs.forEach(name=>{
    const wrap = document.createElement("span"); wrap.className = "chipwrap";
    const c = document.createElement("button"); c.type="button"; c.className="chip";
    c.appendChild(document.createTextNode(name + ".null"));
    const x = document.createElement("button"); x.type="button"; x.className="x"; x.textContent="×";
    x.setAttribute("aria-label", VOOLT("web0.fav.remove", {name: name+".null"}));
    x.onclick=(e)=>{ e.stopPropagation(); saveJSON(LS_FAV, loadJSON(LS_FAV,[]).filter(n=>n!==name)); renderFavs(); syncStar(); };
    c.onclick=()=>{ document.getElementById("addr").value = name+".null"; go(name); };
    wrap.appendChild(c); wrap.appendChild(x);
    bar.appendChild(wrap);
  });
}
function isFav(name){ return loadJSON(LS_FAV,[]).includes(name); }
function currentName(){ return normName(document.getElementById("addr").value); }
function syncStar(){ const s=document.getElementById("star"); s.classList.toggle("on", isFav(currentName())); }

document.getElementById("star").onclick=()=>{
  const name=currentName(); if(!name) return;
  let favs=loadJSON(LS_FAV,[]);
  if(favs.includes(name)) favs=favs.filter(n=>n!==name); else favs.push(name);
  saveJSON(LS_FAV,favs); renderFavs(); syncStar();
};

function pushHistory(name){
  let h=loadJSON(LS_HIST,[]); h=h.filter(n=>n!==name); h.unshift(name); h=h.slice(0,50); saveJSON(LS_HIST,h);
}
function setNav(){
  document.getElementById("back").disabled = stackPos<=0;
  document.getElementById("fwd").disabled = stackPos>=stack.length-1;
}
document.getElementById("back").onclick=()=>{ if(stackPos>0){ stackPos--; open_(stack[stackPos], false); } };
document.getElementById("fwd").onclick=()=>{ if(stackPos<stack.length-1){ stackPos++; open_(stack[stackPos], false); } };
document.getElementById("reload").onclick=()=>{ if(stackPos>=0) open_(stack[stackPos], false); };

async function go(nameArg){
  const name = normName(nameArg!==undefined ? nameArg : document.getElementById("addr").value);
  if(!name){ return; }
  // truncate forward history when navigating to a new name
  stack = stack.slice(0, stackPos+1); stack.push(name); stackPos = stack.length-1;
  await open_(name, true);
}

async function open_(name, record){
  const spin=document.getElementById("spin"), frame=document.getElementById("frame"),
        ph=document.getElementById("placeholder"), status=document.getElementById("status");
  document.getElementById("addr").value = name+".null";
  spin.style.display="inline-block"; setNav(); syncStar();
  try{
    const resp = await voolFetch("/api/web0/resolve?name=" + encodeURIComponent(name));
    const data = await resp.json().catch(() => null);   // a body that will not parse is a failed load too
    // A failed load must not read as "this site does not exist". Only the server's own not-found
    // answer (404) or a real OK answer may say anything about the name; every other error status
    // is a reach failure and gets the retry wording, same as a fetch that throws.
    if(!data || (!resp.ok && resp.status !== 404)){
      frame.style.display="none"; status.style.display="none"; ph.style.display="flex";
      ph.querySelector("h2").textContent = VOOLT("web0.err.reach.title", {name:name});
      ph.querySelector(".hint").textContent = VOOLT("web0.err.reach.hint");
      return;
    }
    if(!data.ok){
      frame.style.display="none"; status.style.display="none"; ph.style.display="flex";
      ph.querySelector("h2").textContent = VOOLT("web0.err.open.title", {name:name});
      ph.querySelector(".hint").textContent = VOOLT("web0.err.open.hint");
      return;
    }
    // Standing view stays plain (name + "takes payments"); the ledger details render inside the
    // closed Details fold and only show when the person opens it.
    document.getElementById("s-name").textContent = data.name+".null";
    document.getElementById("s-owner").textContent = (data.owner||"").slice(0,8)+"…";
    document.getElementById("s-tx").textContent = data.arweave_txid ? (data.arweave_txid.slice(0,10)+"…") : VOOLT("web0.status.tx.none");
    const xw=document.getElementById("s-x402-wrap");
    if(data.x402_endpoint){ xw.style.display=""; document.getElementById("s-x402").textContent=data.x402_endpoint; } else xw.style.display="none";
    document.getElementById("s-pays").style.display = data.x402_endpoint ? "" : "none";
    status.style.display="flex";
    if(data.has_content && data.gateway_url){
      ph.style.display="none"; frame.style.display="block"; frame.src=data.gateway_url;
      currentGatewayUrl = data.gateway_url; document.getElementById("opentab").disabled = false;
      if(record) pushHistory(name);
    }else{
      frame.style.display="none"; ph.style.display="flex";
      currentGatewayUrl = ""; document.getElementById("opentab").disabled = true;
      ph.querySelector("h2").textContent = VOOLT("web0.empty.title", {name:name});
      ph.querySelector(".hint").textContent = VOOLT("web0.empty.hint");
    }
  }catch(e){
    frame.style.display="none"; status.style.display="none"; ph.style.display="flex";
    currentGatewayUrl = ""; document.getElementById("opentab").disabled = true;
    ph.querySelector("h2").textContent = VOOLT("web0.err.reach.title", {name:name});
    ph.querySelector(".hint").textContent = VOOLT("web0.err.reach.hint");
  }finally{ spin.style.display="none"; }
}

document.getElementById("opentab").onclick=()=>{
  // Open the .null site's real Arweave URL as a TOP-LEVEL tab. Phantom (and other wallet
  // extensions) refuse to inject into the sandboxed cross-origin preview iframe as an
  // anti-phishing measure, so a wallet "Connect" there dead-ends at "Get Phantom". A real
  // top-level page gets the injected provider, so the connect works.
  if(!currentGatewayUrl) return;
  // The preview above is read-only and never touches the wallet. Opening the site directly
  // hands it to the browser, where the SITE's own code (not VOOL) may prompt the wallet.
  // Warn so a scary wallet dialog from the external Arweave site is not mistaken for VOOL
  // asking to sign.
  const ok = window.confirm(VOOLT("web0.confirm.body"));
  if(!ok) return;
  try{ window.open(currentGatewayUrl, "_blank", "noopener"); }catch(e){}
};
document.getElementById("go").onclick=()=>go();
document.getElementById("addr").addEventListener("keydown",e=>{ if(e.key==="Enter"){ e.preventDefault(); go(); } });
document.getElementById("addr").addEventListener("input",syncStar);

// --- advanced: null:// task dispatch (compute market) ---
async function dispatch(){
  const uri=document.getElementById("uri-input").value.trim();
  const prompt=document.getElementById("prompt-area").value.trim();
  const out=document.getElementById("dispatch-out");
  if(!uri){ out.textContent=VOOLT("web0.dispatch.no_uri"); return; }
  out.textContent=VOOLT("web0.dispatch.sending");
  try{
    const body={uri}; if(prompt) body.prompt=prompt;
    const resp=await voolFetch("/api/null",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const data=await resp.json();
    // One plain sentence in words; the raw answer/refusal detail belongs in the console, not
    // pasted into the panel.
    if(resp.ok){
      console.info("[web0] dispatch answer", data && data.result);
      out.textContent=VOOLT("web0.dispatch.done");
    }else{
      console.warn("[web0] dispatch refused", (data && (data.message || data.error)) || "");
      out.textContent=VOOLT("web0.dispatch.failed");
    }
  }catch(e){ console.warn("[web0] dispatch failed", e); out.textContent=VOOLT("web0.dispatch.reach"); }
}
document.getElementById("dispatch-btn").onclick=dispatch;

renderFavs(); syncStar(); setNav();
// Auto-open the default web0.null on load so the browser lands on a live page instead of an
// empty "type an address" placeholder. The address bar already defaults to web0.null; go()
// resolves + loads it. If it can't resolve, the placeholder shows the reason (not a blank frame).
go();
</script>
</body>
</html>
"""


def render_null_browser_html(locale: str = "en") -> str:
    """Render the page for one UI locale (the serving door resolves ?ui_locale= > cookie > en).

    The locale's i18n bootstrap body is prepended INSIDE the page's single <script> (the
    chat-page pattern, core/i18n/page_bundle.i18n_bootstrap_js): VOOLT exists before any
    page statement runs, and the document keeps exactly one script block — the node harness
    of tests/test_no_fake_empty_phone.py executes block 0 alone, so a separate <head>
    element would change which code that harness runs.
    """
    from core.i18n.locales import get_locale
    from core.i18n.page_bundle import i18n_bootstrap_js

    spec = get_locale(locale)
    tag = spec.tag if spec else "en"
    page = _PAGE.replace("__VOOL_PALETTE_CSS__", DARK_PALETTE_CSS)
    page = page.replace('<html lang="en">', f'<html lang="{tag}" dir="{spec.direction if spec else "ltr"}">')
    return page.replace("<script>\n", "<script>\n" + i18n_bootstrap_js(tag) + "\n", 1)


__all__ = ["render_null_browser_html"]
