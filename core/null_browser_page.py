"""
core/null_browser_page.py
=========================
Renders the Web0 (.null) browser — an HTML UI that resolves a .null NAME to its
Arweave content and loads it in a sandboxed frame, with an address bar, back/
forward, favorites, and history. Also keeps a collapsible null:// task-dispatch
panel for the compute market.

Served at GET /web0 (and /null-browser) on the always-on VOOL API server (11435).
Resolution goes to GET /api/web0/resolve?name=…; task dispatch to POST /api/null,
both same-origin.
"""
from __future__ import annotations

_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>web0.null · VOOL .null browser</title>
<style>
  :root {
    --bg:#0a0a0a; --panel:#111; --panel2:#141414; --border:#222; --accent:#6cf;
    --green:#4c4; --amber:#da3; --text:#ddd; --muted:#666; --radius:6px;
    --font:"Courier New",monospace;
  }
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
  #go{background:var(--accent);color:#000;border:none;border-radius:var(--radius);
    padding:8px 16px;font-family:var(--font);font-size:13px;font-weight:bold;cursor:pointer;}
  #go:hover{opacity:.85;}
  #star,#opentab{background:var(--panel2);border:1px solid var(--border);border-radius:var(--radius);
    width:34px;height:32px;font-size:15px;cursor:pointer;color:var(--muted);}
  #star.on{color:var(--amber);border-color:var(--amber);}
  #opentab:hover:not(:disabled){border-color:var(--accent);color:var(--accent);}
  #opentab:disabled{opacity:.35;cursor:default;}
  .favbar{display:flex;gap:6px;flex-wrap:wrap;padding:6px 10px;border-bottom:1px solid var(--border);
    background:var(--panel);min-height:34px;align-items:center;}
  .favbar .lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-right:4px;}
  .chip{background:var(--panel2);border:1px solid var(--border);border-radius:20px;
    padding:3px 10px;font-size:12px;color:var(--text);cursor:pointer;transition:border-color .15s;}
  .chip:hover{border-color:var(--accent);}
  .chip .x{color:var(--muted);margin-left:6px;}
  .status{display:flex;gap:14px;flex-wrap:wrap;padding:5px 12px;font-size:11px;color:var(--muted);
    border-bottom:1px solid var(--border);background:var(--panel);}
  .status b{color:var(--green);font-weight:normal;}
  .stage{flex:1;position:relative;background:#000;overflow:hidden;}
  #frame{width:100%;height:100%;border:0;background:#fff;}
  #placeholder{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
    justify-content:center;gap:10px;color:var(--muted);text-align:center;padding:24px;}
  #placeholder h2{color:var(--accent);font-size:18px;letter-spacing:1px;}
  #placeholder .hint{font-size:12px;max-width:520px;line-height:1.7;}
  .spinner{width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--accent);
    border-radius:50%;animation:spin .6s linear infinite;display:none;}
  @keyframes spin{to{transform:rotate(360deg);}}
  .adv{border-top:1px solid var(--border);background:var(--panel);}
  .adv summary{cursor:pointer;padding:8px 12px;font-size:11px;color:var(--muted);
    text-transform:uppercase;letter-spacing:.6px;list-style:none;}
  .adv summary::-webkit-details-marker{display:none;}
  .adv .body{padding:10px 12px;display:flex;flex-direction:column;gap:8px;}
  .adv .row{display:flex;gap:8px;}
  .adv input,.adv textarea{flex:1;background:var(--panel2);border:1px solid var(--border);
    color:var(--text);font-family:var(--font);font-size:12px;border-radius:var(--radius);padding:7px 10px;outline:none;}
  .adv button{background:var(--accent);color:#000;border:none;border-radius:var(--radius);
    padding:7px 14px;font-family:var(--font);font-size:12px;font-weight:bold;cursor:pointer;white-space:nowrap;}
  #dispatch-out{background:#000;border:1px solid var(--border);border-radius:var(--radius);
    padding:10px;min-height:60px;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.6;}
</style>
</head>
<body>
<div class="toolbar">
  <button class="navbtn" id="back" title="Back">&#9664;</button>
  <button class="navbtn" id="fwd" title="Forward">&#9654;</button>
  <button class="navbtn" id="reload" title="Reload">&#8635;</button>
  <input id="addr" type="text" value="web0.null" autocomplete="off" spellcheck="false"
         placeholder="type a .null name, e.g. web0.null"/>
  <div class="spinner" id="spin"></div>
  <button id="go">Go</button>
  <button id="star" title="Bookmark this name">&#9733;</button>
  <button id="opentab" title="Open this .null site in a real browser tab. Wallets like Phantom don't inject into the sandboxed preview frame (anti-phishing), so use this to connect a wallet." disabled>&#8599;</button>
</div>

<div class="favbar" id="favbar"><span class="lbl">favorites</span></div>

<div class="status" id="status" style="display:none">
  <span>name: <b id="s-name">-</b></span>
  <span>owner: <b id="s-owner">-</b></span>
  <span>arweave: <b id="s-tx">-</b></span>
  <span id="s-x402-wrap" style="display:none">x402: <b id="s-x402">-</b></span>
</div>

<div class="stage">
  <iframe id="frame" sandbox="allow-scripts allow-forms allow-popups allow-same-origin"
          referrerpolicy="no-referrer" style="display:none"></iframe>
  <div id="placeholder">
    <h2>&#8709; web0</h2>
    <div class="hint">The agent-native web. Type a <b>.null</b> name and press Go — it resolves
      on Solana, follows the pointer to Arweave, and loads the page here. No server, no DNS.
      Try the default <b>web0.null</b>. Names with no content yet show a note instead.</div>
  </div>
</div>

<details class="adv">
  <summary>&#9881; advanced &middot; null:// task dispatch</summary>
  <div class="body">
    <div class="row">
      <input id="uri-input" type="text" value="null://task/hello"
             placeholder="null://task/code-review?price=0.001" autocomplete="off" spellcheck="false"/>
      <button id="dispatch-btn">Dispatch &#8599;</button>
    </div>
    <textarea id="prompt-area" rows="2" placeholder="Optional natural-language prompt"></textarea>
    <div id="dispatch-out">Ready. Enter a null:// URI and Dispatch.</div>
  </div>
</details>

<script>
const VOOL_API = window.__VOOL_API__ || (window.location.port === "11435" ? "" : "http://localhost:11435");
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
  const lbl = document.createElement("span"); lbl.className="lbl"; lbl.textContent="favorites"; bar.appendChild(lbl);
  if(!favs.length){ const s=document.createElement("span"); s.style.cssText="font-size:11px;color:#555"; s.textContent="none yet — press ★"; bar.appendChild(s); return; }
  favs.forEach(name=>{
    const c = document.createElement("span"); c.className="chip";
    c.appendChild(document.createTextNode(name + ".null"));
    const x = document.createElement("span"); x.className="x"; x.textContent="×";
    x.onclick=(e)=>{ e.stopPropagation(); saveJSON(LS_FAV, loadJSON(LS_FAV,[]).filter(n=>n!==name)); renderFavs(); syncStar(); };
    c.appendChild(x);
    c.onclick=()=>{ document.getElementById("addr").value = name+".null"; go(name); };
    bar.appendChild(c);
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
    const resp = await fetch(VOOL_API + "/api/web0/resolve?name=" + encodeURIComponent(name));
    const data = await resp.json();
    if(!data.ok){
      frame.style.display="none"; status.style.display="none"; ph.style.display="flex";
      ph.querySelector(".hint").textContent = (data.error||"could not resolve") + " — " + name + ".null";
      return;
    }
    document.getElementById("s-name").textContent = data.name+".null";
    document.getElementById("s-owner").textContent = (data.owner||"").slice(0,8)+"…";
    document.getElementById("s-tx").textContent = data.arweave_txid ? (data.arweave_txid.slice(0,10)+"…") : "(none)";
    const xw=document.getElementById("s-x402-wrap");
    if(data.x402_endpoint){ xw.style.display=""; document.getElementById("s-x402").textContent=data.x402_endpoint; } else xw.style.display="none";
    status.style.display="flex";
    if(data.has_content && data.gateway_url){
      ph.style.display="none"; frame.style.display="block"; frame.src=data.gateway_url;
      currentGatewayUrl = data.gateway_url; document.getElementById("opentab").disabled = false;
      if(record) pushHistory(name);
    }else{
      frame.style.display="none"; ph.style.display="flex";
      currentGatewayUrl = ""; document.getElementById("opentab").disabled = true;
      ph.querySelector(".hint").textContent = name+".null is registered (owner "+(data.owner||"").slice(0,8)+"…) but has no Arweave content pointer yet.";
    }
  }catch(e){
    frame.style.display="none"; status.style.display="none"; ph.style.display="flex";
    currentGatewayUrl = ""; document.getElementById("opentab").disabled = true;
    ph.querySelector(".hint").textContent = "Network error resolving "+name+".null: "+e.message;
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
  const ok = window.confirm(
    "This opens the .null site directly in your browser.\n\n"+
    "The preview above is read-only and never touches your wallet. The site itself may ask "+
    "your wallet to connect or sign a transaction -- those prompts come from the site, not "+
    "from VOOL. Only approve them if you trust the site.\n\nOpen the site?"
  );
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
  if(!uri){ out.textContent="Enter a null:// URI first."; return; }
  out.textContent="Dispatching…";
  try{
    const body={uri}; if(prompt) body.prompt=prompt;
    const resp=await fetch(VOOL_API+"/api/null",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const data=await resp.json();
    out.textContent = resp.ok ? (data.result || "(empty response)") : ("Error "+resp.status+": "+(data.error||JSON.stringify(data)));
  }catch(e){ out.textContent="Network error: "+e.message; }
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


def render_null_browser_html() -> str:
    return _PAGE


__all__ = ["render_null_browser_html"]
