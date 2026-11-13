"""The wallet surface of the chat page: a Wallet entry under Home, the wallet panel, a Settings
section and approval cards in the transcript.

Fragment law: one `<style>` + one `<script>` IIFE, one namespace (`window.VoolWallet`), the page
touched only through `window.VoolPageActions`. Everything rendered is `textContent`, never
`innerHTML` with server or model text. The recovery phrase is placed in ONE detachable node
and removed on dismissal; it is never assigned to storage, history, a cookie, a cache, the
console, or any request. Approval happens here (PIN, or the injected Phantom-compatible
provider) and never in the model's turn.

The Wallet home follows the same law: the entry under Home and the panel exist only while the
backend's wallet preference says enabled (one owner, no second flag), the panel reads the same
status/balance/transfer doors Settings reads, and a send is only ever a proposal that the
existing review sheet approves. The vendored offline QR generator (mobile_vendor/qrcode.js,
qrcode-generator 2.0.4, MIT; license URLs normalized http->https for the no-http
    page contract) is spliced INSIDE the IIFE so it adds no global; if it
is absent the Receive view says so and offers the address and copy instead.
"""
from __future__ import annotations

from pathlib import Path

_WALLET_CSS = """
.vw-sec{border-top:1px solid var(--border,#262b35);padding:12px 0}
.vw-sec h4{margin:0 0 6px;font-size:13px}
.vw-line{font-size:12px;color:var(--muted,#9aa1af);margin:4px 0;word-break:break-all}
.vw-btn{font:inherit;font-size:12px;padding:5px 10px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0);cursor:pointer;margin:4px 6px 4px 0}
.vw-btn.primary{border-color:var(--accent,#5eead4);color:var(--accent,#5eead4)}
.vw-form{margin:8px 0;padding:8px;border:1px solid var(--border,#262b35);border-radius:8px}
.vw-form label{display:block;font-size:12px;margin:6px 0 2px}
.vw-form input[type=text],.vw-form input[type=password]{width:100%;box-sizing:border-box;font:inherit;font-size:12px;padding:5px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0)}
.vw-warn{font-size:12px;line-height:1.45;padding:8px;border-radius:8px;background:rgba(255,196,0,.08);border:1px solid rgba(255,196,0,.35)}
.vw-msg{font-size:12px;min-height:16px;margin-top:4px}
.vw-msg.err{color:#f87171}
#vwReveal{position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:9999}
#vwRevealBox{background:var(--panel,#151922);color:var(--ink,#e8eaf0);max-width:520px;width:92%;padding:18px;border-radius:12px;border:1px solid var(--border,#262b35)}
#vwWords{font-family:ui-monospace,Menlo,monospace;font-size:15px;line-height:1.8;letter-spacing:.02em;padding:10px;border:1px dashed var(--border,#262b35);border-radius:8px;user-select:text}
.vw-card{border:1px solid var(--accent,#5eead4);border-radius:10px;padding:10px 12px;margin:6px 0;font-size:13px}
.vw-card .vw-title{font-weight:600;margin-bottom:4px}
.vw-card .vw-row{margin:3px 0;overflow-wrap:normal;word-break:normal}
.vw-card input.vw-pin{font:inherit;font-size:12px;padding:4px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0);width:120px;margin-right:6px}
.vw-card .vw-result{margin-top:6px;font-size:12px;overflow-wrap:anywhere}
.vw-card select.vw-chain,.vw-form select.vw-chain{font:inherit;font-size:12px;padding:4px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0);margin-right:6px}
.vw-badge{display:inline-block;font-size:10px;letter-spacing:.04em;padding:1px 6px;border-radius:999px;border:1px solid var(--accent,#5eead4);color:var(--accent,#5eead4);margin-left:6px;vertical-align:1px}
.vw-badge.testnet{border-color:#fbbf24;color:#fbbf24}
.vw-soft-warning{color:#fbbf24;font-size:12px}
.vw-meaning{border-left:3px solid var(--muted,#9aa1af);padding:6px 10px;margin:6px 0;font-size:12.5px;line-height:1.35}
.vw-meaning.green{border-color:#34d399}.vw-meaning.amber{border-color:#fbbf24}.vw-meaning.red{border-color:#f87171;background:rgba(248,113,113,.08)}
.vw-meaning .vw-headline{font-weight:600}.vw-meaning .vw-sub{color:var(--muted,#9aa1af);font-size:12px;margin-top:2px}
.vw-ack{display:flex;gap:8px;align-items:flex-start;margin-top:8px;font-size:12.5px}.vw-ack input{margin-top:3px}
.vw-export{margin:8px 0;padding:8px 10px;border:1px dashed var(--border,#262b35);border-radius:8px}
#vwExportValue{font-family:ui-monospace,Menlo,monospace;word-break:break-all;padding:8px;border:1px solid var(--border,#262b35);border-radius:6px;margin:8px 0;user-select:all}
.vw-steps{font-size:12.5px;padding-left:18px}
.vw-badge.mode{border-color:var(--muted,#9aa1af);color:var(--muted,#9aa1af)}
.vw-toggle{font:inherit;font-size:10px;padding:0 4px;border:none;background:transparent;color:var(--muted,#9aa1af);cursor:pointer;text-decoration:underline}
.vw-accounts{margin:6px 0}
.vw-account{font-size:12px;color:var(--muted,#9aa1af);margin:2px 0;word-break:break-all}
.vw-receipts{margin:6px 0;border-top:1px dashed var(--border,#262b35);padding-top:6px}
.vw-receipt{font-size:11px;color:var(--muted,#9aa1af);margin:2px 0;word-break:break-all}
.vw-sheet-overlay{position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:9998}
.vw-sheet{background:var(--panel,#151922);color:var(--ink,#e8eaf0);max-width:560px;width:94%;max-height:92vh;overflow:auto;padding:18px;border-radius:12px;border:1px solid var(--border,#262b35)}
.vw-sheet-head{display:flex;align-items:flex-start;gap:16px;justify-content:space-between}
.vw-sheet-head .vw-btn{flex-shrink:0;margin:0}
.vw-sheet h3{margin:0 0 10px;font-size:17px}
.vw-sheet-row{display:grid;grid-template-columns:150px 1fr;gap:8px;font-size:12.5px;margin:5px 0}
.vw-sheet-label{color:var(--muted,#9aa1af)}
.vw-sheet-value{overflow-wrap:normal;word-break:normal;user-select:text}
.vw-sheet-value.vw-ident,.vw-ident{overflow-wrap:anywhere;word-break:break-all;font-family:ui-monospace,Menlo,monospace;font-size:12px}
.vw-purpose{margin:4px 0 10px;font-size:13px;line-height:1.4}
.vw-purpose .vw-mechanism{display:inline-block;font-size:11px;letter-spacing:.03em;padding:1px 7px;border-radius:999px;border:1px solid var(--muted,#9aa1af);color:var(--muted,#9aa1af);margin-left:6px;vertical-align:1px}
.vw-purpose .vw-purpose-note{font-size:12px;color:#fbbf24;margin-top:3px}
.vw-details{margin-top:10px;font-size:12px}
.vw-details summary{cursor:pointer;color:var(--muted,#9aa1af)}
.vw-details .vw-sheet-row{font-size:12px}
.vw-forgot{font:inherit;font-size:11px;padding:0 4px;border:none;background:transparent;color:var(--muted,#9aa1af);cursor:pointer;text-decoration:underline;margin-left:6px}
.vw-recovery h3{margin:0 0 6px;font-size:16px}
.vw-recovery h4{margin:12px 0 4px;font-size:13px}
.vw-recovery .vw-option{padding:8px 10px;border:1px solid var(--border,#262b35);border-radius:8px;margin:8px 0}
.vw-recovery .vw-option.muted{color:var(--muted,#9aa1af)}
.vw-recovery textarea{width:100%;box-sizing:border-box;font:inherit;font-size:12px;font-family:ui-monospace,Menlo,monospace;padding:5px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0);min-height:56px}
.vw-recovery input[type=password]{width:100%;box-sizing:border-box;font:inherit;font-size:12px;padding:5px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0)}
.vw-recovery label{display:block;font-size:12px;margin:6px 0 2px}
.vw-recovery ul{margin:4px 0;padding-left:18px;font-size:12px}
.vw-recovery a{color:var(--accent,#5eead4)}
.vw-cx{font-size:12.5px}
.vw-cx-head{margin:6px 0 10px}
.vw-cx-env{font-size:12px;color:var(--muted,#9aa1af);margin:4px 0}
.vw-cx-row{border:1px solid var(--border,#262b35);border-radius:10px;padding:10px 12px;margin:8px 0}
.vw-cx-row.inactive{opacity:.85;border-style:dashed}
.vw-cx-title{font-weight:600;font-size:13.5px}
.vw-cx-channel{margin:8px 0}
.vw-cx-channel label{font-weight:600;margin-left:6px}
.vw-cx-caps{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.vw-cx-cap{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--border,#262b35);color:var(--muted,#9aa1af)}
.vw-cx-cap.on{border-color:#34d399;color:#34d399}
.vw-cx-account{margin:8px 0;padding:8px 10px;border:1px solid var(--border,#262b35);border-radius:8px}
.vw-cx-address{font-family:ui-monospace,Menlo,monospace;font-size:12px;overflow-wrap:anywhere;word-break:break-all;user-select:text}
.vw-cx-balance{font-size:12px;margin:4px 0;color:var(--muted,#9aa1af)}
.vw-cx-balance.read{color:var(--ink,#e8eaf0)}
.vw-cx-actions{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-top:6px}
.vw-cx-form{margin:8px 0;padding:8px 10px;border:1px solid var(--accent,#5eead4);border-radius:8px}
.vw-cx-form label{display:block;font-size:12px;margin:6px 0 2px}
.vw-cx-form input[type=password],.vw-cx-form input[type=text]{width:100%;box-sizing:border-box;font:inherit;font-size:12px;padding:5px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--ink,#e8eaf0)}
.vw-cx-next{margin:10px 0;padding:10px 14px;border-radius:10px;border:1px solid var(--accent,#b0812b);background:rgba(176,129,43,.08);font-weight:600;line-height:1.45}.vw-cx-fold{margin:6px 0;font-size:12.5px}.vw-cx-fold>summary{cursor:pointer;opacity:.8}
.vw-cx-warn{font-size:12.5px;line-height:1.45;padding:8px;border-radius:8px;background:rgba(255,196,0,.08);border:1px solid rgba(255,196,0,.35);margin:6px 0}
.vw-cx-history{font-size:11.5px;color:var(--muted,#9aa1af);margin:4px 0}
#vwCxBackupValue{font-family:ui-monospace,Menlo,monospace;overflow-wrap:anywhere;word-break:break-all;padding:8px;border:1px dashed var(--border,#262b35);border-radius:6px;margin:8px 0;user-select:all;font-size:12px}
.vw-cx details{margin:10px 0}
.vw-cx details summary{cursor:pointer;color:var(--muted,#9aa1af);font-size:12.5px}
.vw-cx-dev{padding:8px 10px;border:1px dashed var(--border,#262b35);border-radius:8px}
.vw-copy{font:inherit;font-size:11px;padding:1px 6px;border:1px solid var(--border,#262b35);border-radius:6px;background:transparent;color:var(--muted,#9aa1af);cursor:pointer;margin-left:6px;vertical-align:1px}
.vw-sheet-note{font-size:12px;margin:6px 0;color:#fbbf24}
.vw-sheet-actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:10px}
.vw-review{margin:4px 0 8px}
.vw-review-line{font-size:13px;margin:4px 0;line-height:1.35}
.vw-review-line.primary{font-size:14.5px;font-weight:600}
.vw-review-line.muted{color:var(--muted,#9aa1af);font-size:12.5px}
.vw-review-addr{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;word-break:break-all;user-select:text}
.vw-review-warning{font-size:12.5px;margin:6px 0;padding:6px 8px;border:1px solid #fbbf24;border-radius:6px;color:#fbbf24}
.vw-details-toggle{font:inherit;font-size:12.5px;background:transparent;border:none;color:var(--muted,#9aa1af);cursor:pointer;padding:4px 0;text-decoration:underline}
.vw-details-body[hidden]{display:none}
.vw-details-body{border-top:1px dashed var(--border,#262b35);margin-top:6px;padding-top:6px}
.vw-transfer-detail{font-size:12px;color:var(--muted,#9aa1af);margin-top:2px}
.vw-transfer-id{font-size:11.5px;overflow-wrap:anywhere;word-break:break-all;user-select:text;margin-top:2px;font-family:ui-monospace,Menlo,monospace}
.vw-explorer{display:inline-block;margin-top:4px;font-size:12.5px}
.vw-safety{border:1px solid var(--border,#262b35);border-radius:8px;padding:8px 10px;margin:6px 0}
.vw-safety .vw-point{font-size:12.5px;line-height:1.45;margin:2px 0}
.vw-learn{font-size:12.5px;margin:4px 0 6px}
.vw-learn summary{cursor:pointer;color:var(--accent,#5eead4)}
.vw-learn[open] summary{margin-bottom:6px}
.vw-sheet-warning{color:#fbbf24}
/* ---- the Wallet home panel (Home -> Wallet): one overlay, the page's dark language -------- */
#vwHomeOverlay{position:fixed;inset:0;background:rgba(0,0,0,.72);display:flex;align-items:center;justify-content:center;z-index:9997}
.vw-wh{background:var(--panel,#151922);color:var(--ink,#e8eaf0);width:min(660px,94vw);max-height:92vh;overflow:auto;padding:18px;border-radius:12px;border:1px solid var(--border,#262b35)}
.vw-wh-head{display:flex;align-items:center;gap:10px}
.vw-wh-title{font-size:17px;font-weight:600;margin-right:auto}
.vw-wh-body{margin-top:10px}
.vw-wh-selects{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.vw-wh-selects label{font-size:12px;color:var(--muted,#9aa1af);margin-right:-2px}
.vw-wh select{font:inherit;font-size:12.5px;padding:5px 8px;border:1px solid var(--border,#262b35);border-radius:8px;background:var(--panel,#151922);color:var(--ink,#e8eaf0);max-width:240px}
.vw-wh-balance{border:1px solid var(--border,#262b35);border-radius:10px;padding:12px 14px;margin:8px 0}
.vw-wh-amount{font-size:26px;font-weight:600;letter-spacing:.01em}
.vw-wh-amount .vw-wh-unit{font-size:15px;color:var(--muted,#9aa1af);margin-left:6px;font-weight:400}
.vw-wh-balstate{font-size:12px;color:var(--muted,#9aa1af);margin-top:4px}
.vw-wh-balstate.unavailable{color:#fbbf24}
.vw-wh-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.vw-wh-actions .vw-btn{font-size:13px;padding:7px 14px}
.vw-wh-sec{font-size:12px;color:var(--muted,#9aa1af);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 6px}
.vw-wh-row{border:1px solid var(--border,#262b35);border-radius:10px;padding:8px 12px;margin:6px 0;font-size:12.5px}
.vw-wh-row .vw-wh-rowhead{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline}
.vw-wh-row .vw-wh-amt{font-weight:600}
.vw-wh-row .vw-wh-when{margin-left:auto;color:var(--muted,#9aa1af);font-size:11.5px}
.vw-wh-state{display:inline-block;font-size:10.5px;letter-spacing:.04em;padding:1px 7px;border-radius:999px;border:1px solid var(--muted,#9aa1af);color:var(--muted,#9aa1af);vertical-align:1px}
.vw-wh-state.pending{border-color:#fbbf24;color:#fbbf24}
.vw-wh-state.confirmed{border-color:#34d399;color:#34d399}
.vw-wh-state.failed,.vw-wh-state.cancelled{border-color:#f87171;color:#f87171}
.vw-wh-row details{margin-top:6px}
.vw-wh-row details summary{cursor:pointer;color:var(--muted,#9aa1af);font-size:12px}
.vw-wh-note{font-size:12px;color:var(--muted,#9aa1af);margin:8px 0;line-height:1.45}
.vw-wh-warn{font-size:12.5px;line-height:1.45;padding:8px;border-radius:8px;background:rgba(255,196,0,.08);border:1px solid rgba(255,196,0,.35);margin:8px 0}
.vw-wh-empty{color:var(--muted,#9aa1af);font-size:12.5px;padding:10px 0}
.vw-wh-setup-actions{display:flex;flex-direction:column;gap:8px;margin:10px 0}
.vw-wh-setup-actions .vw-btn{text-align:left;padding:8px 12px}
.vw-wh-addr{font-family:ui-monospace,Menlo,monospace;font-size:13px;overflow-wrap:anywhere;word-break:break-all;user-select:text;margin:8px 0}
.vw-qr{background:#fff;padding:12px;border-radius:10px;width:fit-content;margin:10px 0}
.vw-wh-form{border:1px solid var(--accent,#5eead4);border-radius:10px;padding:10px 12px;margin:8px 0}
.vw-wh-form label{display:block;font-size:12px;margin:8px 0 2px;color:var(--muted,#9aa1af)}
.vw-wh-form input[type=text],.vw-wh-form input[type=password]{width:100%;box-sizing:border-box;font:inherit;font-size:12.5px;padding:6px 8px;border:1px solid var(--border,#262b35);border-radius:8px;background:transparent;color:var(--ink,#e8eaf0)}
.vw-wh-form .vw-wh-sub{font-size:12px;color:var(--muted,#9aa1af);margin-top:2px}
.vw-wh-contacts{border:1px dashed var(--border,#262b35);border-radius:8px;margin:6px 0;max-height:180px;overflow:auto}
.vw-wh-contact{display:block;width:100%;text-align:left;font:inherit;font-size:12px;padding:6px 8px;border:none;border-bottom:1px solid var(--border,#262b35);background:transparent;color:var(--ink,#e8eaf0);cursor:pointer}
.vw-wh-contact:hover{background:var(--active,#1b2130)}
.vw-wh-contact .vw-wh-ident{font-family:ui-monospace,Menlo,monospace;font-size:11px;overflow-wrap:anywhere;word-break:break-all;display:block;color:var(--muted,#9aa1af)}
.vw-wh-msg{font-size:12px;min-height:16px;margin-top:6px}
.vw-wh-msg.err{color:#f87171}
@media (max-width:720px){.vw-wh-selects{flex-direction:column;align-items:stretch}.vw-wh select{max-width:none}}
"""

_WALLET_JS = r"""
(function(){
'use strict';
/*__VOOL_QR_VENDOR__*/
if (window.VoolWallet) return;

function actions(){ return window.VoolPageActions || null; }
function el(tag, className, text){
  var node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}
function postJson(path, body){
  return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) })
    .then(function(r){ return r.json().then(function(j){ return { ok: r.ok && j && j.ok !== false, status: r.status, data: j || {} }; }); });
}
function getJson(path){ return fetch(path, { cache: 'no-store' }).then(function(r){ return r.json(); }); }
function short(v){ v = String(v || ''); return v.length > 14 ? (v.slice(0, 8) + '…' + v.slice(-4)) : v; }
// Phantom injects its Solana provider ONLY where it actually runs: browsers with the Phantom
// extension (Chrome, Brave) and Phantom's own mobile in-app browser (docs.phantom.com,
// "Detect the provider": window.phantom?.solana, isPhantom marks the real one). A native macOS
// WKWebView cannot load browser extensions and Phantom has no desktop-app handoff protocol, so
// absence here is the truth: we say the supported route instead of pretending injection exists.
function provider(){
  var p = (window.phantom && window.phantom.solana) || window.solana || null;
  return (p && p.isPhantom) ? p : ((p && typeof p.connect === 'function') ? p : null);
}
function noPhantomMessage(){
  return WT('wallet.home.no_phantom', 'Phantom is not available here: its provider is injected only by browsers with the Phantom extension (Chrome or Brave) and by Phantom\'s own mobile browser — this app window cannot load extensions. To connect a Phantom account, open this app in Chrome or Brave with the extension installed; on a phone, use the Phantom app\'s browser. Signing then stays in Phantom. A watch-only address or a VOOL Wallet on this device also works without Phantom.');
}
var phantomBound = false;
function bindPhantomEvents(){
  // account changes and disconnects arrive as events (Phantom provider docs); handle each once,
  // through the same owner door as the first connect. No event ever fabricates a connected state.
  if (phantomBound) return;
  var p = provider();
  if (!p || typeof p.on !== 'function') return;
  phantomBound = true;
  p.on('accountChanged', function(publicKey){
    var key = (publicKey && publicKey.toString && publicKey.toString()) || '';
    if (!key) { setMsg('Phantom reports no account (locked or disconnected). The registered key stays until you change it.'); return; }
    postJson('/api/wallet/external', { public_key: key, label: 'phantom' }).then(function(res){
      if (!res.ok) { setMsg((res.data && (res.data.message || res.data.error)) || 'Phantom account change was not registered.', true); return; }
      setMsg('Phantom account changed: ' + short(key) + ' is now the registered external wallet.'); renderStatus();
    }).catch(function(){ setMsg('The account-change registration did not complete.', true); });
  });
  p.on('disconnectEvent', function(){
    setMsg('Phantom disconnected. The registered key stays until you connect again or remove it; nothing was signed.');
  });
}
function evmProvider(){ return (window.ethereum && typeof window.ethereum.request === 'function') ? window.ethereum : null; }
var EVM_CHAIN_NAMES = { 'eip155:84532': 'Base Sepolia', 'eip155:11155111': 'Ethereum Sepolia', 'eip155:97': 'BNB Smart Chain Testnet', 'eip155:46630': 'Robinhood Chain Testnet', 'eip155:4663': 'Robinhood Chain', 'eip155:8453': 'Base', 'eip155:1': 'Ethereum', 'eip155:56': 'BNB Smart Chain' };
function evmChainLabel(caip2){ return EVM_CHAIN_NAMES[caip2] || caip2; }
function isEvmNetwork(network){ return String(network || '').indexOf('eip155:') === 0; }
function answer_result(v){ return v && typeof v === 'object' ? v.result : null; }

// ---- settings section ------------------------------------------------------------------------
var sec = null, statusEl = null, msgEl = null, lastStatus = null;
function setMsg(text, isErr){ if (!msgEl) return; msgEl.textContent = text || ''; msgEl.className = 'vw-msg' + (isErr ? ' err' : ''); }
// A wallet-wide setting that loosens what can be paid (Mainnet, a payment channel or a pay-per-answer service on, the fee
// threshold) needs the wallet PIN or password, as raising a cap does (a Touch ID wallet asks on this Mac instead). When the
// door asks for it, one box under the message line takes it, the request goes again with it, and the box clears either way.
var pendingProof = null;
function postOwnerProved(path, body, anchor){
  return postJson(path, body).then(function(res){
    var fault = res && res.data && res.data.fault;
    var at = anchor || msgEl;
    if (res.ok || !fault || !fault.context || fault.context.reason !== 'credential_required' || !at || !at.parentNode) return res;
    var word = (res.data.error === 'wallet_password_invalid') ? 'password' : 'PIN';
    // a second ask while one box is open closes the first as cancelled, so its control is not left waiting
    if (pendingProof) pendingProof();
    var box = el('div', 'vw-form'); box.id = 'vwOwnerProof';
    var named = ' of ' + (fault.context.wallet_label || 'your first wallet');
    var label = el('label', null, 'This change needs the ' + word + named); label.setAttribute('for', 'vwOwnerProofSecret'); box.appendChild(label);
    var input = el('input', 'vw-pin'); input.type = 'password'; input.id = 'vwOwnerProofSecret'; input.autocomplete = 'off';
    input.setAttribute('aria-label', 'wallet ' + word); if (word === 'PIN') input.inputMode = 'numeric'; box.appendChild(input);
    var ok = el('button', 'vw-btn', 'Confirm'); ok.type = 'button'; ok.id = 'vwOwnerProofOk'; box.appendChild(ok);
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.type = 'button'; cancel.id = 'vwOwnerProofCancel'; box.appendChild(cancel);
    at.parentNode.insertBefore(box, at.nextSibling);
    input.focus();
    return new Promise(function(resolve){
      var settled = false;
      function done(result){
        if (settled) return; settled = true; pendingProof = null;
        input.value = ''; box.remove();
        var e = result && result.data && result.data.error;
        if (e === 'wallet_pin_invalid' || e === 'wallet_password_invalid' || e === 'wallet_unlock_throttled') result.data.message = (e === 'wallet_unlock_throttled' ? 'Too many wrong tries: the wallet is locked for a while.' : 'That ' + word + ' was not right.') + ' Nothing was changed.';
        resolve(result);
      }
      pendingProof = function(){ done({ ok: false, status: 0, data: { message: 'Nothing was changed.' } }); };
      input.addEventListener('keydown', function(e){ if (e.key === 'Enter') { e.preventDefault(); ok.click(); } });
      cancel.addEventListener('click', function(){ done({ ok: false, status: 0, data: { message: 'Nothing was changed.' } }); });
      ok.addEventListener('click', function(){
        var again = {}; Object.keys(body || {}).forEach(function(k){ again[k] = body[k]; }); again.credential = input.value;
        ok.disabled = true;
        postJson(path, again).then(done).catch(function(){ done({ ok: false, status: 0, data: { message: 'The request did not complete.' } }); });
      });
    });
  });
}
var CHAIN_LABELS = { 'eip155:84532': 'Base Sepolia', 'eip155:11155111': 'Ethereum Sepolia', 'eip155:97': 'BNB Smart Chain Testnet', 'eip155:46630': 'Robinhood Chain Testnet', 'eip155:4663': 'Robinhood Chain', 'eip155:8453': 'Base', 'eip155:1': 'Ethereum', 'eip155:56': 'BNB Smart Chain', 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1': 'Solana Devnet', 'solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp': 'Solana' };
function chainLabel(caip2){ return CHAIN_LABELS[caip2] || caip2; }
var MODE_LABELS = { watch_only: WT('wallet.mode.watch_only', 'Watch-only: you can see it here, but not pay from it'), pocket_sealed: WT('wallet.mode.pocket_sealed', 'VOOL wallet: its key stays on this computer and you approve each payment'), external_signer: WT('wallet.mode.external_signer', 'Connected wallet: you approve payments in your own wallet app') };
var MODE_SHORT = { watch_only: WT('wallet.mode_short.watch_only', 'watch-only'), pocket_sealed: WT('wallet.mode_short.pocket_sealed', 'VOOL wallet'), external_signer: WT('wallet.mode_short.external_signer', 'connected') };
function maskedLine(label, value, id){
  // public identifiers are public ON CHAIN; masked by default so a screenshot does not
  // glue a person's profile to their address — one click reveals the exact value
  var line = el('div', 'vw-line');
  line.appendChild(document.createTextNode(label + ': '));
  var span = el('span', null, short(value)); span.id = id;
  var toggle = el('button', 'vw-toggle', 'show'); toggle.type = 'button'; toggle.setAttribute('aria-label', 'show the full ' + label);
  var shown = false;
  toggle.addEventListener('click', function(){ shown = !shown; span.textContent = shown ? value : short(value); toggle.textContent = shown ? 'hide' : 'show'; });
  line.appendChild(span); line.appendChild(toggle);
  return line;
}
var legacyDevOpen = false, legacyShownEnabled = null, legacyEvmSync = null;
function setWalletEnabled(on){
  setMsg(on ? 'Enabling VOOL Wallet…' : 'Turning VOOL Wallet off…');
  return postJson('/api/settings/prefs', { wallet_enabled: !!on }).then(function(res){
    if (!res.ok) { setMsg((res.data && (res.data.error || res.data.message)) || 'Not changed.', true); return false; }
    setMsg('');
    renderStatus(); poll();
    return true;
  }).catch(function(){ setMsg('The request did not complete.', true); return false; });
}
function appendReceipts(host){
  // read-only history: available whether the wallet is on or off (a disabled wallet keeps its receipts)
  var receiptsBox = el('div', 'vw-receipts'); receiptsBox.id = 'vwReceipts';
  host.appendChild(receiptsBox);
  getJson('/api/wallet/receipts').then(function(rj){
    receiptsBox.textContent = '';
    var rows = (rj && rj.receipts) || [];
    if (!rows.length) { receiptsBox.appendChild(el('div', 'vw-receipt', WT('wallet.no_receipts', 'no payment receipts yet'))); return; }
    rows.slice(0, 5).forEach(function(r){
      var kind = r.origin === 'dna_fee' ? 'DNA fee collection ' : 'payment ';
      var feeState = (r.dna_fee_collection && r.dna_fee_collection.state) ? ' · fee ledger ' + r.dna_fee_collection.state : '';
      receiptsBox.appendChild(el('div', 'vw-receipt', kind + (r.state || '?') + ' · ' + (r.asset || '') + ' ' + (r.amount_minor || 0) + ' minor · ' + chainLabel(r.network) + (r.tx_signature ? ' · ' + short(r.tx_signature) : '') + feeState));
    });
  }).catch(function(){ receiptsBox.textContent = ''; });
}
function renderStatus(){
  if (!statusEl) return;
  return getJson('/api/wallet/status').then(function(j){
    var st = (j && j.status) || {};
    lastStatus = st;
    statusEl.textContent = '';
    statusEl.setAttribute('role', 'status'); statusEl.setAttribute('aria-live', 'polite');
    var actions = document.getElementById('vwActions');
    if (actions) actions.hidden = !st.enabled;
    legacyShownEnabled = !!st.enabled;
    if (!st.enabled) {
      // OFF is one fact; EXISTENCE is another. Short description, explicit Enable entry, and the
      // honest statement of what still exists. Nothing is created, unlocked or sent here.
      statusEl.appendChild(el('div', 'vw-line', 'VOOL Wallet is off. Crypto is optional: chat and every non-crypto tool work without it.'));
      if (st.has_wallet) {
        statusEl.appendChild(el('div', 'vw-line', 'Your wallet is still saved on this device and nothing was deleted: wallets, balances, history and pending-operation evidence are kept. Enabling restores your setup state.'));
      } else {
        statusEl.appendChild(el('div', 'vw-line', 'No wallet or key exists yet, and none is ever created automatically.'));
      }
      var offRow = el('div'); offRow.id = 'vwOffRow'; offRow.className = 'vw-line';
      var enableBtn = el('button', 'vw-btn primary', 'Enable VOOL Wallet'); enableBtn.id = 'vwEnableBtn'; enableBtn.type = 'button';
      enableBtn.addEventListener('click', function(){ setWalletEnabled(true); });
      offRow.appendChild(enableBtn);
      statusEl.appendChild(offRow);
      appendReceipts(statusEl);
      return st;
    }
    statusEl.appendChild(el('div', 'vw-line', 'Crypto is optional. Turn it off any time in Settings → Crypto; nothing is deleted.'));
    var offBtn = el('button', 'vw-toggle', 'turn off'); offBtn.type = 'button'; offBtn.id = 'vwOffBtn';
    offBtn.addEventListener('click', function(){ setWalletEnabled(false); });
    statusEl.appendChild(offBtn);
    // nothing here yet: one plain line, no account words
    var head = el('div', 'vw-line'); head.id = 'vwKindLine';
    head.appendChild(document.createTextNode((MODE_LABELS[st.custody_mode] || WT('wallet.legacy.none_yet', 'Nothing added here yet.')) + ' '));
    var mainnetBadge = el('span', 'vw-badge' + (st.mainnet_enabled ? '' : ' testnet'), st.mainnet_enabled ? WT('wallet.legacy.real_money', 'real money') : WT('wallet.legacy.test_coins', 'test coins only'));
    head.appendChild(mainnetBadge);
    statusEl.appendChild(head);
    (st.soft_warnings || []).forEach(function(w){ var note = el('div', 'vw-line vw-soft-warning', (w && w.text) || ''); note.setAttribute('role', 'note'); statusEl.appendChild(note); });
    if (st.public_key) statusEl.appendChild(maskedLine('public key', st.public_key, 'vwPubKey'));
    if (st.pending_approvals) statusEl.appendChild(el('div', 'vw-line', WTF('wallet.legacy.waiting', 'Payments waiting for your approval: {count}', { count: st.pending_approvals })));
    if (st.custody_mode === 'pocket_sealed' && st.wallet_id) statusEl.appendChild(exportControls(st));
    // the raw numbers (smallest coin units) and how the older wallet signs: for developers, folded away
    var dev = el('details', 'vw-dev'); dev.id = 'vwLegacyDev'; dev.open = legacyDevOpen;
    dev.addEventListener('toggle', function(){ legacyDevOpen = dev.open; });
    dev.appendChild(el('summary', null, WT('wallet.legacy.dev', 'Developer options: signing and spending caps')));
    dev.appendChild(el('div', 'vw-line', 'Signs with: ' + (MODE_LABELS[st.signing_preference] || st.signing_preference || 'not chosen') + ' · Most one paid web request may cost: ' + (st.x402_cap_minor != null ? st.x402_cap_minor + ' (in the coin’s smallest units)' : 'not set')));
    var lim = st.limits || {};
    if (lim.per_tx_minor) { var limLine = el('div', 'vw-line', 'Limits, in the coin’s smallest units: per payment ' + lim.per_tx_minor + ', per day ' + lim.daily_minor + ', per recipient per day ' + lim.per_destination_daily_minor); limLine.id = 'vwLimitsLine'; dev.appendChild(limLine); }
    var fees = st.dna_fees || null;
    // The DNA service fee was charged only on UsePod's paid answers, and UsePod has stopped: the line and its threshold
    // controls are shown only to a person with DNA fees on record, never as a fee anyone new is charged.
    if (fees && fees.policy && (fees.identities || []).length) {
      var feeLine = el('div', 'vw-line'); feeLine.id = 'vwDnaFeesLine';
      var ids = fees.identities || [];
      var summary = ids.length ? ids.map(function(i){ return i.owed_exact + ' ' + i.asset + ' owed (' + i.collectible_exact + ' collectible, carry ' + i.carry_exact + ')' + (i.open_collection ? ' · collection ' + i.open_collection.state + ' with payment ' + short(i.open_collection.payment_proposal_id || '') : ''); }).join(' · ') : 'nothing accrued yet';
      feeLine.textContent = 'DNA service fee ' + (fees.policy.rate_label || '0.1%') + ' on native x402 payments · ' + summary + ' · treasury ' + ((fees.treasury || {}).state || 'unknown') + ((fees.treasury || {}).source === 'operator_override_disposable_recipient' ? ' (operator override)' : '');
      statusEl.appendChild(feeLine);
      if (st.wallet_id) statusEl.appendChild(dnaFeeControls(st, fees));
    }
    if (st.wallet_id) dev.appendChild(limitsControls(st));
    if (st.wallet_id || dev.childNodes.length > 2) statusEl.appendChild(dev);
    if (st.custody_mode === 'pocket_sealed' && st.wallet_id) statusEl.appendChild(approvalControls(st));
    var accounts = st.accounts || [];
    if (accounts.length) {
      var box = el('div', 'vw-accounts');
      accounts.forEach(function(a){
        var line = el('div', 'vw-account');
        line.appendChild(document.createTextNode((a.label ? a.label + ' · ' : '') + chainLabel(a.chain)));
        line.appendChild(el('span', 'vw-badge mode', MODE_SHORT[a.mode] || a.mode));
        if (a.testnet) line.appendChild(el('span', 'vw-badge testnet', 'testnet'));
        line.appendChild(document.createTextNode(' ' + short(a.public_key)));
        if (a.mode === 'pocket_sealed' && a.wallet_id) { var fl = forgotLink(a.wallet_id, 'Forgot PIN or password?'); fl.className += ' vw-forgot-account'; fl.setAttribute('data-wallet', a.wallet_id); line.appendChild(fl); }
        box.appendChild(line);
      });
      statusEl.appendChild(box);
    }
    appendReceipts(statusEl);
    return st;
  }).catch(function(){ statusEl.textContent = 'Wallet status could not be read.'; return null; });
}

function showReveal(phrase){
  // ONE detachable node. Nothing else ever holds the phrase: no storage, no history, no console, no request.
  var overlay = el('div'); overlay.id = 'vwReveal'; overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true'); overlay.setAttribute('aria-label', 'recovery phrase, shown once');
  var box = el('div'); box.id = 'vwRevealBox';
  box.appendChild(el('h3', null, 'Your recovery phrase — shown once'));
  box.appendChild(el('div', 'vw-warn', 'Write these 12 words down now, in order, and keep them offline. VOOL does not store them and cannot show them again: closing this box is final.'));
  var words = el('div'); words.id = 'vwWords'; words.textContent = phrase; box.appendChild(words);
  var done = el('button', 'vw-btn primary', 'I have written it down'); done.id = 'vwRevealDone'; done.type = 'button';
  done.addEventListener('click', function(){ words.textContent = ''; overlay.remove(); phrase = null; renderStatus(); });
  box.appendChild(done); overlay.appendChild(box); document.body.appendChild(overlay);
  var onKey = function(ev){ if (ev.key === 'Escape') done.click(); };
  document.addEventListener('keydown', onKey);
  done.addEventListener('click', function(){ document.removeEventListener('keydown', onKey); });
}

function showExport(data){
  // ONE detachable node, like the phrase: the key lives nowhere else -- no storage, no history, no console.
  var overlay = el('div'); overlay.id = 'vwExportReveal'; overlay.setAttribute('role', 'dialog'); overlay.setAttribute('aria-modal', 'true'); overlay.setAttribute('aria-label', 'private key, shown once');
  var box = el('div'); box.id = 'vwRevealBox';
  var target = data.target === 'metamask' ? 'MetaMask' : 'Phantom';
  box.appendChild(el('h3', null, 'Private key for ' + target + ' — shown once'));
  box.appendChild(el('div', 'vw-warn', data.warning || 'Anyone who has this text owns the wallet.'));
  box.appendChild(el('div', 'vw-line', 'Address: ' + (data.address || '')));
  var value = el('div'); value.id = 'vwExportValue'; value.textContent = data.value || ''; box.appendChild(value);
  var steps = el('ol', 'vw-steps'); (data.import_steps || []).forEach(function(s){ steps.appendChild(el('li', null, s)); }); box.appendChild(steps);
  var copy = el('button', 'vw-btn', 'Copy'); copy.type = 'button'; copy.id = 'vwExportCopy';
  copy.addEventListener('click', function(){ try { navigator.clipboard.writeText(value.textContent).then(function(){ copy.textContent = 'Copied — clear your clipboard after importing'; }); } catch (e) { copy.textContent = 'Select the text and copy it'; } });
  var done = el('button', 'vw-btn primary', 'Done — I have imported it'); done.id = 'vwExportDone'; done.type = 'button';
  done.addEventListener('click', function(){ value.textContent = ''; overlay.remove(); data = null; });
  box.appendChild(copy); box.appendChild(done); overlay.appendChild(box); document.body.appendChild(overlay);
}
function exportControls(st){
  // Pocket wallets only. Unlocked by Touch ID or the Mac password (a Keychain user-presence item); the PIN cannot export.
  var row = el('div', 'vw-export'); row.id = 'vwExport';
  row.appendChild(el('div', 'vw-line', 'Export private key — unlocks with Touch ID or your Mac password only; the PIN cannot export it.'));
  var target = el('select', 'vw-chain'); target.id = 'vwExportTarget'; target.setAttribute('aria-label', 'wallet to export for');
  [['phantom', 'Phantom (Solana keypair)'], ['metamask', 'MetaMask (Ethereum key)']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; target.appendChild(opt); });
  var btn = el('button', 'vw-btn', 'Export private key…'); btn.id = 'vwExportBtn'; btn.type = 'button';
  btn.addEventListener('click', function(){
    btn.disabled = true; setMsg('Confirm with Touch ID or your Mac password…');
    postJson('/api/wallet/export', { wallet_id: st.wallet_id, target: target.value }).then(function(res){
      btn.disabled = false;
      if (!res.ok) { setMsg(failText(res, 'Not exported.'), true); return; }
      setMsg(''); showExport(res.data.export || {});
    }).catch(function(){ btn.disabled = false; setMsg('The export request did not complete.', true); });
  });
  row.appendChild(target); row.appendChild(btn);
  return row;
}

function dnaFeeControls(st, fees){
  // The owner's two trusted doors for the DNA service fee: the collection threshold (Settings owns it; the 0.1% rate and
  // the treasury are not settable here or anywhere) and an explicit "collect now". Neither signs or sends: a
  // collection is a pending card approved on its own sheet with the PIN.
  var box = el('div', 'vw-form vw-dna-fees'); box.id = 'vwDnaFees';
  box.appendChild(el('div', 'vw-line', 'DNA fee collection threshold (whole atomic units of the paid asset). Accrued fees ride your next native payment only when they reach it; the 0.1% rate, the treasury and the cost bound cannot be changed here.'));
  var assetLabel = el('label', null, 'Asset'); assetLabel.setAttribute('for', 'vwDnaFeeAsset'); box.appendChild(assetLabel);
  var asset = el('select', 'vw-limit'); asset.id = 'vwDnaFeeAsset'; asset.setAttribute('aria-label', 'asset the DNA fee threshold applies to');
  ['USDC', 'SOL'].forEach(function(a){ var opt = el('option'); opt.value = a; opt.textContent = a; asset.appendChild(opt); });
  box.appendChild(asset);
  var minLabel = el('label', null, 'Collect when accrued fees reach'); minLabel.setAttribute('for', 'vwDnaFeeMin'); box.appendChild(minLabel);
  var input = el('input', 'vw-limit'); input.type = 'number'; input.min = '1'; input.step = '1'; input.id = 'vwDnaFeeMin'; input.autocomplete = 'off'; input.setAttribute('aria-label', 'DNA fee collection threshold in atomic units');
  var effective = el('div', 'vw-line'); effective.id = 'vwDnaFeeEffective';
  function fill(){
    var c = (fees.collect_min || {})[asset.value] || {};
    // the box holds the OWNER's value; an operator override that outranks it is said beside it, never shown as the owner's choice
    input.value = c.settings_atomic != null ? String(c.settings_atomic) : (c.atomic != null ? String(c.atomic) : '');
    input.title = c.source ? 'effective source: ' + c.source : '';
    var text = 'Effective threshold now: ' + (c.atomic != null ? c.atomic : '?') + ' atomic units of ' + asset.value;
    if (c.source === 'operator_override') text += ' (an operator override is in force; ' + (c.settings_atomic != null ? 'your saved value of ' + c.settings_atomic + ' applies' : 'a value you save applies') + ' once it is removed)';
    else if (c.source === 'settings') text += ' (your saved value)';
    else text += ' (the documented default; save a value to change it)';
    effective.textContent = text + '.';
  }
  fill(); asset.addEventListener('change', fill); box.appendChild(input); box.appendChild(effective);
  var save = el('button', 'vw-btn', 'Save threshold'); save.id = 'vwDnaFeeSave'; save.type = 'button';
  save.addEventListener('click', function(){
    var v = String(input.value || '').trim();
    if (!/^\d+$/.test(v) || parseInt(v, 10) < 1) { setMsg('The threshold is a whole number of atomic units, at least 1.', true); return; }
    save.disabled = true;
    postOwnerProved('/api/wallet/dna-fees/policy', { asset: asset.value, collect_min_atomic: parseInt(v, 10) }).then(function(res){
      save.disabled = false;
      if (!res.ok) { setMsg((res.data && (res.data.message || res.data.error)) || 'Threshold not saved.', true); return; }
      var s = (res.data && res.data.saved) || {};
      var c = (res.data && res.data.collect_min) || {};
      var text = 'DNA fee threshold saved: ' + s.atomic + ' atomic units of ' + s.asset + '.';
      if (res.data && res.data.override_in_force) text += ' An operator override keeps the effective threshold at ' + c.atomic + ' atomic units until it is removed.';
      setMsg(text);
      renderStatus();
    }).catch(function(){ save.disabled = false; setMsg('The threshold request did not complete.', true); });
  });
  box.appendChild(save);
  var mode = el('div', 'vw-line'); mode.id = 'vwDnaFeeMode';
  var bound = (fees.collection || {}).cost_bound_bps;
  mode.textContent = 'Accrued fees are collected with your next native payment, under that payment\'s approval, only when the amount reaches this threshold and the collection\'s network cost is at most ' + (bound != null ? (Number(bound) / 100) + '%' : 'the cost bound') + ' of it. Nothing is collected without your approval of that payment.';
  box.appendChild(mode);
  return box;
}

function limitsControls(st){
  // Spending caps for this wallet (minor units). Saved through /api/wallet/limits; the status line then shows what the door STORED, not what was typed.
  var lim = st.limits || {};
  var box = el('div', 'vw-form vw-limits'); box.id = 'vwLimits';
  box.appendChild(el('div', 'vw-line', 'Spending caps (minor units, e.g. lamports for SOL). A proposal above a cap is refused before it ever reaches approval.'));
  function field(id, label, value){
    var l = el('label', null, label); l.setAttribute('for', id); box.appendChild(l);
    var i = el('input', 'vw-limit'); i.type = 'number'; i.min = '0'; i.step = '1'; i.id = id; i.autocomplete = 'off'; i.setAttribute('aria-label', label);
    i.value = (value === undefined || value === null) ? '' : String(value); box.appendChild(i); return i;
  }
  var assetLabel = el('label', null, 'Asset'); assetLabel.setAttribute('for', 'vwLimitAsset'); box.appendChild(assetLabel);
  var asset = el('input', 'vw-limit'); asset.type = 'text'; asset.id = 'vwLimitAsset'; asset.autocomplete = 'off'; asset.value = 'SOL'; asset.setAttribute('aria-label', 'asset the caps apply to'); box.appendChild(asset);
  var perTx = field('vwLimitPerTx', 'Per transaction', lim.per_tx_minor);
  var daily = field('vwLimitDaily', 'Per day', lim.daily_minor);
  var perDest = field('vwLimitPerDest', 'Per destination per day', lim.per_destination_daily_minor);
  // Raising a cap asks for the wallet's own PIN or password (a Touch ID wallet asks on this Mac instead); lowering never does.
  var secretWord = st.approval_method === 'password' ? 'password' : 'PIN';
  var secret = null;
  if (st.approval_method === 'pin' || st.approval_method === 'password') {
    var secretLabel = el('label', null, 'Wallet ' + secretWord + ' (needed only to raise a cap)'); secretLabel.setAttribute('for', 'vwLimitSecret'); box.appendChild(secretLabel);
    secret = el('input', 'vw-pin'); secret.type = 'password'; secret.id = 'vwLimitSecret'; secret.autocomplete = 'off';
    secret.setAttribute('aria-label', 'wallet ' + secretWord + ', needed only to raise a cap'); if (secretWord === 'PIN') secret.inputMode = 'numeric';
    box.appendChild(secret);
  }
  var save = el('button', 'vw-btn', 'Save spending caps'); save.id = 'vwLimitsSave'; save.type = 'button';
  save.addEventListener('click', function(){
    var body = { wallet_id: st.wallet_id, asset: (asset.value || 'SOL').trim().toUpperCase() };
    if (secret && secret.value) body.credential = secret.value;
    var bad = false;
    [['per_tx_minor', perTx], ['daily_minor', daily], ['per_destination_daily_minor', perDest]].forEach(function(f){
      var v = String(f[1].value || '').trim(); if (v === '') return;
      if (!/^\d+$/.test(v)) { bad = true; return; } body[f[0]] = parseInt(v, 10);
    });
    if (bad) { setMsg('Caps are whole numbers of minor units.', true); return; }
    save.disabled = true;
    postJson('/api/wallet/limits', body).then(function(res){
      save.disabled = false; if (secret) secret.value = '';
      if (!res.ok && res.data && res.data.fault && res.data.fault.context && res.data.fault.context.reason === 'credential_required') { setMsg('Raising a cap needs your wallet ' + secretWord + '. Type it above and save again.', true); return; }
      if (!res.ok) { setMsg(failText(res, 'Caps not saved.'), true); return; }
      setMsg('Spending caps saved.');
      renderStatus();
    }).catch(function(){ save.disabled = false; if (secret) secret.value = ''; setMsg('The caps request did not complete.', true); });
  });
  box.appendChild(save);
  return box;
}

function approvalControls(st){
  // Pocket wallets only: how payments are approved. The door verifies the current secret and re-seals; the status re-read shows the stored method.
  var METHOD_LABELS = { pin: 'PIN', password: 'password', device: 'Touch ID / Mac password' };
  var currentMethod = st.approval_method || 'pin';
  var box = el('div', 'vw-form vw-approval'); box.id = 'vwApproval';
  var head = el('div', 'vw-line', 'Payment approval: ' + (METHOD_LABELS[currentMethod] || currentMethod)); head.id = 'vwApprovalCurrent'; box.appendChild(head);
  var selLabel = el('label', null, 'Switch to'); selLabel.setAttribute('for', 'vwApprovalMethod'); box.appendChild(selLabel);
  var sel = el('select', 'vw-chain'); sel.id = 'vwApprovalMethod'; sel.setAttribute('aria-label', 'new approval method');
  ['pin', 'password', 'device'].forEach(function(m){ var o = el('option'); o.value = m; o.textContent = METHOD_LABELS[m]; if (m === currentMethod) o.selected = true; sel.appendChild(o); });
  box.appendChild(sel);
  var curLabel = el('label', null, 'Current ' + (METHOD_LABELS[currentMethod] || currentMethod)); curLabel.setAttribute('for', 'vwApprovalSecret');
  var cur = el('input', 'vw-pin'); cur.type = 'password'; cur.id = 'vwApprovalSecret'; cur.autocomplete = 'off';
  if (currentMethod === 'device') { cur.hidden = true; curLabel.hidden = true; }
  else { cur.setAttribute('aria-label', 'current wallet ' + METHOD_LABELS[currentMethod]); if (currentMethod === 'pin') cur.inputMode = 'numeric'; }
  box.appendChild(curLabel); box.appendChild(cur);
  var nwLabel = el('label', null, 'New PIN'); nwLabel.setAttribute('for', 'vwApprovalNew');
  var nw = el('input', 'vw-pin'); nw.type = 'password'; nw.id = 'vwApprovalNew'; nw.autocomplete = 'new-password';
  function syncNew(){
    var m = sel.value;
    nwLabel.textContent = m === 'pin' ? 'New PIN' : (m === 'password' ? 'New password' : '');
    nw.hidden = (m === 'device'); nwLabel.hidden = (m === 'device');
    nw.inputMode = m === 'pin' ? 'numeric' : 'text'; nw.setAttribute('aria-label', m === 'pin' ? 'new wallet PIN' : 'new wallet password');
  }
  sel.addEventListener('change', syncNew); syncNew();
  box.appendChild(nwLabel); box.appendChild(nw);
  var save = el('button', 'vw-btn', 'Change approval method'); save.id = 'vwApprovalSave'; save.type = 'button';
  save.addEventListener('click', function(){
    var m = sel.value;
    var body = { wallet_id: st.wallet_id, method: m };
    if (m !== 'device') body[m === 'pin' ? 'new_pin' : 'new_password'] = nw.value;
    if (currentMethod !== 'device') body[currentMethod === 'pin' ? 'pin' : 'password'] = cur.value;
    save.disabled = true;
    postJson('/api/wallet/approval-method', body).then(function(res){
      save.disabled = false; nw.value = ''; cur.value = '';
      if (!res.ok) { setMsg(failText(res, 'Approval method not changed.'), true); return; }
      setMsg('Payment approval now uses ' + (METHOD_LABELS[m] || m) + '.');
      renderStatus();
    }).catch(function(){ save.disabled = false; nw.value = ''; cur.value = ''; setMsg('The approval-method request did not complete.', true); });
  });
  box.appendChild(save);
  return box;
}

function buildSection(){
  sec = el('div', 'vw-sec'); sec.id = 'vwSec';
  sec.appendChild(el('h4', null, 'VOOL Wallet'));
  statusEl = el('div'); statusEl.id = 'vwStatus'; sec.appendChild(statusEl);
  // the setup choices. Hidden until the wallet is enabled (renderStatus decides): OFF shows the
  // description and the Enable entry instead, and the API refuses creation doors regardless.
  var row = el('div'); row.id = 'vwActions'; row.hidden = true;
  var createBtn = el('button', 'vw-btn', WT('wallet.legacy.create_older', 'Create an older 12-word wallet…')); createBtn.id = 'vwCreateBtn'; createBtn.type = 'button';
  var restoreBtn = el('button', 'vw-btn', 'Restore from phrase…'); restoreBtn.id = 'vwRestoreBtn'; restoreBtn.type = 'button';
  var phantomBtn = el('button', 'vw-btn primary', WT('wallet.home.connect_phantom', 'Connect Phantom or another Solana wallet')); phantomBtn.id = 'vwConnectPhantom'; phantomBtn.type = 'button';
  var evmChain = el('select', 'vw-chain'); evmChain.id = 'vwEvmChain'; evmChain.title = 'EVM testnet for the external wallet';
  var evmBtn = el('button', 'vw-btn primary', WT('wallet.home.connect_evm', 'Connect MetaMask or another wallet (EVM)')); evmBtn.id = 'vwConnectEvm'; evmBtn.type = 'button';
  row.appendChild(phantomBtn); row.appendChild(evmChain); row.appendChild(evmBtn); row.appendChild(createBtn); row.appendChild(restoreBtn); sec.appendChild(row);
  var watchBtn = el('button', 'vw-btn', 'Add watch-only address…'); watchBtn.id = 'vwWatchBtn'; watchBtn.type = 'button';
  row.appendChild(watchBtn);
  msgEl = el('div', 'vw-msg'); msgEl.id = 'vwMsg';

  var wform = el('div', 'vw-form'); wform.id = 'vwWatchForm'; wform.hidden = true;
  wform.appendChild(el('div', 'vw-line', 'Watch-only is the recommended start: VOOL can observe, simulate and PROPOSE payments for this address, but nothing can ever be signed here — there is no key to steal.'));
  wform.appendChild(el('label', null, 'Public address (Solana devnet or a declared EVM testnet)'));
  var waddr = el('input'); waddr.type = 'text'; waddr.id = 'vwWatchAddress'; waddr.autocomplete = 'off'; waddr.setAttribute('aria-label', 'watch-only public address'); wform.appendChild(waddr);
  wform.appendChild(el('label', null, 'Label (optional)'));
  var wlabel = el('input'); wlabel.type = 'text'; wlabel.id = 'vwWatchLabel'; wlabel.autocomplete = 'off'; wform.appendChild(wlabel);
  var wsubmit = el('button', 'vw-btn primary', 'Add'); wsubmit.id = 'vwWatchSubmit'; wsubmit.type = 'button'; wform.appendChild(wsubmit);
  sec.appendChild(wform);
  watchBtn.addEventListener('click', function(){ form.hidden = true; rform.hidden = true; wform.hidden = !wform.hidden; setMsg(''); });
  wsubmit.addEventListener('click', function(){
    wsubmit.disabled = true;
    var address = String(waddr.value || '').trim();
    var network = address.indexOf('0x') === 0 ? evmChain.value : 'solana-devnet';
    postJson('/api/wallet/watch-only', { public_key: address, network: network, label: String(wlabel.value || '') }).then(function(res){
      wsubmit.disabled = false;
      if (!res.ok) { setMsg((res.data && (res.data.message || res.data.error)) || 'Not added.', true); return; }
      waddr.value = ''; wlabel.value = ''; wform.hidden = true; setMsg('Watch-only address added. Approvals for it will explain that signing is unavailable.'); renderStatus();
    }).catch(function(){ wsubmit.disabled = false; setMsg('Not added.', true); });
  });

  var form = el('div', 'vw-form'); form.id = 'vwCreateForm'; form.hidden = true;
  // TWO short safety points, collapsed further guidance: education is compact, the authority facts
  // (the typed confirmation phrase, the approval method) stay at the decision itself.
  var points = el('div', 'vw-safety'); points.id = 'vwSafetyPoints';
  points.appendChild(el('div', 'vw-point', '1. Protect your recovery material: the 12 words are shown once and VOOL cannot recover them for you.'));
  points.appendChild(el('div', 'vw-point', '2. Check the destination and network before sending: transfers are irreversible.'));
  form.appendChild(points);
  // The custody warning is the authority fact of this decision, so it renders in the OPEN
  // form body — never folded behind a collapsed <details> (inner text of a closed details
  // is empty; the user must see the key/device warning before typing the confirmation).
  var warn = el('div', 'vw-warn'); warn.id = 'vwWarning'; form.appendChild(warn);
  var learn = el('details', 'vw-learn'); learn.id = 'vwLearnMore';
  learn.appendChild(el('summary', null, 'Learn more before you create a wallet'));
  learn.appendChild(el('div', 'vw-line', 'The tricks that empty wallets, explained shortly: Settings → Crypto → Stay safe.'));
  form.appendChild(learn);
  form.appendChild(el('label', null, 'Type this exact phrase to confirm:'));
  var expected = el('div', 'vw-line'); expected.id = 'vwPhraseExpected'; form.appendChild(expected);
  var ackLabel = el('label'); var ack = el('input'); ack.type = 'checkbox'; ack.id = 'vwAck'; ackLabel.appendChild(ack); ackLabel.appendChild(document.createTextNode(' I understand this device will hold the key')); form.appendChild(ackLabel);
  var phrase = el('input'); phrase.type = 'text'; phrase.id = 'vwPhrase'; phrase.autocomplete = 'off'; phrase.placeholder = 'confirmation phrase'; form.appendChild(phrase);
  form.appendChild(el('label', null, 'How you approve payments'));
  var method = el('select', 'vw-chain'); method.id = 'vwApprovalMethod';
  [['pin', 'PIN (4–8 digits)'], ['password', 'Wallet password (10+ characters)'], ['device', 'My computer’s own sign-in (such as Touch ID)']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
  form.appendChild(method);
  var pinLabel = el('label', null, 'PIN (4–8 digits)'); form.appendChild(pinLabel);
  var pin = el('input'); pin.type = 'password'; pin.id = 'vwPin'; pin.autocomplete = 'new-password'; pin.inputMode = 'numeric'; form.appendChild(pin);
  var pwLabel = el('label', null, 'Wallet password (10+ characters)'); pwLabel.hidden = true; form.appendChild(pwLabel);
  var pw = el('input'); pw.type = 'password'; pw.id = 'vwPassword'; pw.autocomplete = 'new-password'; pw.hidden = true; form.appendChild(pw);
  var devNote = el('div', 'vw-line', 'Every payment and every export will ask for your computer’s own sign-in (such as Touch ID). Nothing is stored behind a PIN.'); devNote.hidden = true; form.appendChild(devNote);
  method.addEventListener('change', function(){ var m = method.value; pinLabel.hidden = pin.hidden = (m !== 'pin'); pwLabel.hidden = pw.hidden = (m !== 'password'); devNote.hidden = (m !== 'device'); });
  var submit = el('button', 'vw-btn primary', 'Create'); submit.id = 'vwCreateSubmit'; submit.type = 'button'; form.appendChild(submit);
  sec.appendChild(form);

  var rform = el('div', 'vw-form'); rform.id = 'vwRestoreForm'; rform.hidden = true;
  rform.appendChild(el('label', null, 'Recovery phrase (12 or 24 words)'));
  var rphrase = el('input'); rphrase.type = 'password'; rphrase.id = 'vwRestorePhrase'; rphrase.autocomplete = 'off'; rform.appendChild(rphrase);
  rform.appendChild(el('label', null, 'How you approve payments'));
  var rmethod = el('select', 'vw-chain'); rmethod.id = 'vwRestoreMethod';
  [['pin', 'PIN (4–8 digits)'], ['password', 'Wallet password (10+ characters)'], ['device', 'My computer’s own sign-in (such as Touch ID)']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; rmethod.appendChild(opt); });
  rform.appendChild(rmethod);
  rform.appendChild(el('label', null, 'New PIN or password (none for your computer’s own sign-in)'));
  var rpin = el('input'); rpin.type = 'password'; rpin.id = 'vwRestorePin'; rpin.autocomplete = 'new-password'; rform.appendChild(rpin);
  var rsubmit = el('button', 'vw-btn primary', 'Restore'); rsubmit.id = 'vwRestoreSubmit'; rsubmit.type = 'button'; rform.appendChild(rsubmit);
  sec.appendChild(rform);
  sec.appendChild(msgEl);

  createBtn.addEventListener('click', function(){
    rform.hidden = true;
    getJson('/api/wallet/pocket/terms').then(function(j){
      warn.textContent = (j && j.warning) || ''; expected.textContent = (j && j.confirmation_phrase) || '';
      form.hidden = false; setMsg('');
    }).catch(function(){ setMsg('Could not load the wallet terms.', true); });
  });
  restoreBtn.addEventListener('click', function(){ form.hidden = true; rform.hidden = !rform.hidden; setMsg(''); });
  submit.addEventListener('click', function(){
    submit.disabled = true;
    postJson('/api/wallet/pocket/create', { acknowledged_warning: !!ack.checked, confirmation_phrase: phrase.value, approval_method: method.value, pin: pin.value, password: pw.value }).then(function(res){
      submit.disabled = false;
      if (!res.ok) { setMsg((res.data && (res.data.message || res.data.error)) || 'Not created.', true); return; }
      var words = res.data.recovery_phrase; res.data.recovery_phrase = null;
      pin.value = ''; pw.value = ''; phrase.value = ''; ack.checked = false; form.hidden = true; setMsg('VOOL Wallet created.');
      showReveal(words); words = null;
    }).catch(function(){ submit.disabled = false; setMsg('Not created.', true); });
  });
  rsubmit.addEventListener('click', function(){
    rsubmit.disabled = true;
    postJson('/api/wallet/pocket/restore', { recovery_phrase: rphrase.value, approval_method: rmethod.value, pin: rmethod.value === 'pin' ? rpin.value : '', password: rmethod.value === 'password' ? rpin.value : '' }).then(function(res){
      rsubmit.disabled = false; rphrase.value = ''; rpin.value = '';
      if (!res.ok) { setMsg((res.data && (res.data.message || res.data.error)) || 'Not restored.', true); return; }
      rform.hidden = true; setMsg('Wallet restored: ' + short(res.data.wallet && res.data.wallet.public_key)); renderStatus();
    }).catch(function(){ rsubmit.disabled = false; setMsg('Not restored.', true); });
  });
  phantomBtn.addEventListener('click', function(){
    var p = provider();
    if (!p || typeof p.connect !== 'function') { setMsg(noPhantomMessage(), true); return; }
    p.connect().then(function(r){
      var key = (r && r.publicKey && r.publicKey.toString()) || (p.publicKey && p.publicKey.toString()) || '';
      if (!key) throw new Error('the wallet returned no account');
      bindPhantomEvents();
      return postJson('/api/wallet/external', { public_key: key, label: 'phantom' }).then(function(res){
        if (!res.ok) { setMsg((res.data && (res.data.message || res.data.error)) || 'Not connected.', true); return; }
        setMsg('Phantom connected: ' + short(key) + '. Signing stays in Phantom; account changes are followed automatically.');
        renderStatus();
      });
    }).catch(function(err){ setMsg('Phantom did not connect: ' + (err && err.message ? err.message : 'no answer'), true); });
  });
  // the EVM choices come with the status; turning Crypto on later re-reads them (refreshStatus)
  legacyEvmSync = function(st){
    if (!st) return;
    var declared = (st && st.declared_networks) || [];
    evmChain.textContent = '';
    var evmNetworks = declared.filter(function(n){ return isEvmNetwork(n); });
    evmNetworks.forEach(function(n){
      var opt = el('option'); opt.value = n; opt.textContent = evmChainLabel(n); evmChain.appendChild(opt);
    });
    evmChain.hidden = evmNetworks.length === 0;
    evmBtn.hidden = evmNetworks.length === 0;
  };
  renderStatus().then(legacyEvmSync);
  evmBtn.addEventListener('click', function(){
    var eth = evmProvider();
    if (!eth) { setMsg(WT('wallet.home.no_evm', 'No MetaMask or other EVM wallet was found in this browser. Open VOOL in a browser that has the wallet extension to connect it.'), true); return; }
    var chain = evmChain.value;
    if (!chain) { setMsg('No EVM testnet is declared on this runtime.', true); return; }
    evmBtn.disabled = true;
    eth.request({ method: 'eth_requestAccounts' }).then(function(accounts){
      var account = (accounts && accounts[0] || '').toLowerCase();
      if (!account) throw new Error('no account');
      // the wallet must actually be ON the chosen testnet before we register the account
      return eth.request({ method: 'eth_chainId' }).then(function(chainIdHex){
        var wanted = parseInt(chain.split(':')[1], 10);
        var got = parseInt(String(chainIdHex), 16);
        if (wanted !== got) throw new Error('wallet is on chain ' + got + ', not ' + wanted + '; switch it and retry');
        return postJson('/api/wallet/external', { public_key: account, network: chain, label: 'evm' });
      });
    }).then(function(res){
      evmBtn.disabled = false;
      if (!res || !res.ok) { setMsg((res && res.data && (res.data.message || res.data.error)) || 'Not connected.', true); return; }
      setMsg('EVM wallet registered on ' + evmChainLabel(chain) + ': signing stays in your wallet.'); renderStatus();
    }).catch(function(err){ evmBtn.disabled = false; setMsg('The EVM wallet did not connect: ' + (err && err.message ? err.message : 'unknown error'), true); });
  });
  return sec;
}

// ---- the Crypto (Pilot) surface in Settings: registry rows, credential-first creation, one reveal, acknowledgement ----
// Every row, capability, account state, environment and policy text comes from /api/wallet/status (crypto_pilot): the
// page names no network, no capability and no rule of its own. The backup is placed in ONE detachable node, cleared
// on every exit, never stored, never logged, never copied without a deliberate click.
var cryptoHost = null, cryptoStatus = null, cryptoCreationKeys = {}, cryptoOpenForm = null, cryptoChannelsOpen = false;
// the reasons and setup states read in the app language (WT is hoisted; the i18n bootstrap runs first)
var CAP_REASONS = {
  environment_inactive: WT('wallet.reason.environment_inactive', 'not on the active network environment'),
  evm_dependencies_missing: WT('wallet.reason.evm_dependencies_missing', 'EVM support is missing on this runtime'),
  pilot_lane_incomplete: WT('wallet.reason.pilot_lane_incomplete', 'not complete in this build'),
  no_account_on_this_network: WT('wallet.reason.no_account_on_this_network', 'no account on this network yet'),
  no_ready_signing_account: WT('wallet.reason.no_ready_signing_account', 'no wallet ready to sign here'),
  no_setup_awaiting_backup: WT('wallet.reason.no_setup_awaiting_backup', 'no setup waiting for its backup'),
  storage_class_refused: WT('wallet.reason.storage_class_refused', 'key storage is not lasting on this install')
};
var CAP_LABELS = { create: 'Create', balance: 'Balance', quote: 'Preview', sign: 'Sign', send: 'Send', backup: 'Backup', receipt: 'Receipts', list: 'List' };
var SETUP_TEXT = {
  ready: WT('wallet.setup_state.ready', 'ready'),
  awaiting_backup: WT('wallet.setup_state.awaiting_backup', 'setup in progress: the backup has not been shown yet'),
  backup_revealed: WT('wallet.setup_state.backup_revealed', 'setup in progress: the backup was shown once and is not yet confirmed as saved'),
  cancelled: WT('wallet.setup_state.cancelled', 'setup cancelled: the wallet and its key are kept'),
  generating: WT('wallet.setup_state.generating', 'setup in progress')
};
function cxMsg(node, text, isErr){ node.textContent = text || ''; node.className = 'vw-msg' + (isErr ? ' err' : ''); }
// A refusal's catalog sentence, then the producer's specific next step when it names one (the same
// code is refused from several flows, so the sentence stays true for all of them and the step adds the detail).
function failText(res, fallback){
  var d = res && res.data, msg = (d && d.message) || '', ctx = d && d.fault && d.fault.context, rem = (ctx && ctx.remediation) || '';
  if (rem && msg.indexOf(rem) === -1) msg = msg ? msg + ' ' + rem : rem;
  return msg || (d && d.error) || fallback;
}
function faultMessage(res, fallback){ return (res && res.data && (res.data.message || (res.data.fault && res.data.fault.context && res.data.fault.context.reason) || res.data.error)) || fallback; }
function mountCrypto(host){
  // The Settings page repaints its pane whenever another widget's source arrives; the live surface (an open create
  // form, a credential prompt, the backup dialog) must survive that, so the ONE surface node moves into the new host
  // instead of being rebuilt -- exactly how the legacy section keeps its state. A fresh read happens only when no
  // surface exists yet or when an action asks for one.
  if (!host) return false;
  cryptoHost = host;
  var existing = document.getElementById('vwCx');
  if (existing) { if (existing.parentNode !== host) host.appendChild(existing); return true; }
  renderCrypto();
  return true;
}
function renderCrypto(){
  if (!cryptoHost) return Promise.resolve(null);
  return getJson('/api/wallet/status').then(function(j){
    cryptoStatus = (j && j.status) || {};
    drawCrypto(cryptoStatus);
    return cryptoStatus;
  }).catch(function(){ cryptoHost.textContent = ''; cryptoHost.appendChild(el('div', 'vw-line', 'The crypto status could not be read.')); return null; });
}
function drawCrypto(st){
  var host = cryptoHost; host.textContent = '';
  var box = el('div', 'vw-cx'); box.id = 'vwCx'; host.appendChild(box);
  if (!st.enabled) {
    var off = el('div', 'vw-line', 'Crypto is off. Turn on "Enable Crypto" above to see the supported networks and create a wallet. Turning it on creates, signs and pays nothing.');
    off.id = 'vwCxOff'; box.appendChild(off);
    return;
  }
  var cp = st.crypto_pilot || {};
  var head = el('div', 'vw-cx-head');
  var notice = el('div', 'vw-cx-warn', cp.pilot_notice || ''); notice.id = 'vwCxNotice'; head.appendChild(notice);
  if (cp.frozen) head.appendChild(el('div', 'vw-cx-warn', 'Payments are frozen by the panic control; nothing is signed or sent until it is lifted.'));
  var next = el('div', 'vw-cx-next', nextStepText(cp)); next.id = 'vwCxNext'; next.setAttribute('role', 'status'); head.appendChild(next);
  // the network environment and what can be sent: facts for the curious, folded so the first look stays short
  var facts = el('details', 'vw-cx-fold'); facts.id = 'vwCxFacts';
  facts.appendChild(el('summary', null, WT('wallet.cx.facts', 'Which coins can I send?')));
  if (cp.asset_support && cp.asset_support.note) { var assets = el('div', 'vw-cx-env', cp.asset_support.note); assets.id = 'vwCxAssets'; facts.appendChild(assets); }
  var env = el('div', 'vw-cx-env'); env.id = 'vwCxEnv';
  env.textContent = WT('wallet.cx.env', 'Networks shown') + ': ' + (cp.environment_label || cp.active_environment || '') + ' · ' + (cp.environment_source_label || cp.environment_source || '');
  facts.appendChild(env);
  head.appendChild(facts);
  box.appendChild(head);
  var rows = cp.networks || [];
  var order = cp.chain_order || [];
  var active = rows.filter(function(r){ return r.active; });
  var inactiveWithAccounts = rows.filter(function(r){ return !r.active && (r.accounts || []).length; });
  var inactiveEmpty = rows.filter(function(r){ return !r.active && !(r.accounts || []).length; });
  function byOrder(a, b){ return order.indexOf(a.chain_key) - order.indexOf(b.chain_key); }
  active.sort(byOrder).forEach(function(r){ box.appendChild(rowCard(r, cp, st)); });
  if (inactiveWithAccounts.length) {
    box.appendChild(el('div', 'vw-cx-env', 'Accounts on the other network environment stay listed with their own network; changing the environment moves nothing.'));
    inactiveWithAccounts.sort(byOrder).forEach(function(r){ box.appendChild(rowCard(r, cp, st)); });
  }
  box.appendChild(keychainRestoreBlock(cp));
  box.appendChild(usbRestoreBlock(cp));
  // how paid web requests are paid is a developer choice: folded with the other developer options' look
  var channels = el('details', 'vw-cx-fold vw-cx-dev-fold'); channels.id = 'vwCxChannelsFold'; channels.open = cryptoChannelsOpen;
  channels.addEventListener('toggle', function(){ cryptoChannelsOpen = channels.open; });
  channels.appendChild(el('summary', null, WT('wallet.cx.channels_dev', 'Developer options: payment channels')));
  channels.appendChild(channelControls(cp));
  box.appendChild(channels);
  box.appendChild(paidAiControls(cp));
  box.appendChild(agentControls(cp));
  box.appendChild(devOptions(cp, inactiveEmpty.sort(byOrder)));
}
function nextStepText(cp){
  var mine = [];
  (cp.networks || []).forEach(function(r){ if (r.active) (r.accounts || []).forEach(function(a){ if (!a.agent_id && a.mode === 'pocket_sealed') mine.push(a); }); });
  if (!mine.length) return WT('wallet.next.create', 'Next step: pick a network below (Solana is a good start) and press Create wallet.');
  if (mine.some(function(a){ return (a.setup_state || 'ready') !== 'ready'; })) return WT('wallet.next.finish', 'Next step: finish your new wallet below. Save its backup key, then confirm that you saved it.');
  if (mine.some(function(a){ return !a.keychain_backup && !a.usb_backup; })) return WT('wallet.next.second_copy', 'Your wallet is ready. Next step: keep a second copy of its backup. Under the wallet, press Save backup to a USB stick or drive, or Save backup in Keychain.');
  return WT('wallet.next.done', 'All set. To add money, press Receive under your wallet and send coins to that address from an exchange or another wallet.');
}
var CHANNEL_REASONS = {
  paykit_not_installed: 'the optional pay extra (Solana pay-kit) is not installed',
  python_below_3_11: 'Solana pay-kit needs Python 3.11 or newer',
  x402_sdk_not_installed: 'the optional x402 extra (x402 Foundation SDK) is not installed'
};
function channelControls(cp){
  // The owner's choice of how paid requests (x402, MPP) are paid, through the trusted Settings door. The wallet's own
  // cap, approval and receipt apply on every channel; a channel that is off pays nothing, and a payment already parked
  // on it waits for approval until the channel is on again.
  var box = el('div', 'vw-cx-row vw-cx-channels'); box.id = 'vwCxChannels';
  box.appendChild(el('div', 'vw-cx-title', 'Payment channels'));
  box.appendChild(el('div', 'vw-cx-env', 'Choose how VOOL may pay for paid requests. Whichever channel a payment goes through, it needs your approval, stays under your caps and gets a receipt. A channel that is off pays nothing: a payment already waiting on it cannot be approved until you turn the channel back on, and a request already sent to an external wallet still expires on its usual timer.'));
  var msg = el('div', 'vw-msg');
  (cp.payment_channels || []).forEach(function(c){
    if (c && c.error) { var unread = el('div', 'vw-cx-warn', 'The payment channels could not be shown right now. Reload this page to try again.'); unread.id = 'vwCxChannelsUnread'; box.appendChild(unread); return; }
    if (!c || !c.channel_id) return;
    var row = el('div', 'vw-cx-channel'); row.setAttribute('data-channel', c.channel_id); row.setAttribute('data-on', c.on ? 'true' : 'false');
    var toggle = el('input'); toggle.type = 'checkbox'; toggle.id = 'vwCh_' + c.channel_id; toggle.checked = !!c.on;
    var label = el('label', null, c.label); label.setAttribute('for', toggle.id);
    row.appendChild(toggle); row.appendChild(label);
    row.appendChild(el('div', 'vw-cx-env', c.description || ''));
    var facts = 'Built by ' + (c.built_by || '?') + ' · pays on ' + ((c.network_names || []).join(', ') || 'no network yet');
    if (!c.available) facts += ' · not usable on this install: ' + (CHANNEL_REASONS[c.reason] || c.reason || 'unavailable');
    row.appendChild(el('div', 'vw-cx-env', facts));
    toggle.addEventListener('change', function(){
      var wanted = toggle.checked; toggle.disabled = true;
      postOwnerProved('/api/wallet/channels', { channel: c.channel_id, on: wanted }, msg).then(function(res){
        toggle.disabled = false;
        if (!res.ok) { toggle.checked = !wanted; cxMsg(msg, faultMessage(res, 'The payment channel was not changed.'), true); return; }
        var saved = (res.data && res.data.channel) || {};
        toggle.checked = !!saved.on; row.setAttribute('data-on', saved.on ? 'true' : 'false');
        cxMsg(msg, (saved.label || c.label) + (saved.on ? ' is on.' : ' is off: nothing is paid through it.'));
      }).catch(function(){ toggle.disabled = false; toggle.checked = !wanted; cxMsg(msg, 'The payment channel request did not complete.', true); });
    });
    box.appendChild(row);
  });
  box.appendChild(msg);
  return box;
}
function paidAiControls(cp){
  // The owner's pay-per-answer AI endpoints: a model VOOL may ask through x402, the most one answer may cost, and
  // whether it is on. Asking happens from chat; every answer is paid only after the owner approves it in the wallet.
  var box = el('div', 'vw-cx-row vw-cx-paid-ai'); box.id = 'vwCxPaidAi';
  box.appendChild(el('div', 'vw-cx-title', 'Pay-per-answer AI'));
  box.appendChild(el('div', 'vw-cx-env', 'Add an AI service that charges a small fee per answer, in USDC on Base. Then ask it from any chat, for example "ask my paid AI: …", and name it when you have more than one. Only your question is sent, never the rest of the chat. You approve each payment first. It stays under your caps and this price limit, and you get a receipt.'));
  box.appendChild(el('div', 'vw-cx-env', 'VOOL checks that each answer comes from the model you chose. If the service names a different model, or no model, VOOL pauses it. Turn it back on here when you are ready. VOOL cannot see what really runs behind the service.'));
  var msg = el('div', 'vw-msg');
  var list = el('div', 'vw-cx-paid-ai-list'); list.id = 'vwPaidAiList';
  function draw(rows){
    list.textContent = '';
    if (rows && rows.error) { list.appendChild(el('div', 'vw-cx-warn', 'Your services could not be shown right now. Reload this page to try again.')); return; }
    if (!rows || !rows.length) { list.appendChild(el('div', 'vw-cx-env', 'No service added yet.')); return; }
    rows.forEach(function(r){
      var row = el('div', 'vw-cx-channel'); row.setAttribute('data-endpoint', r.id); row.setAttribute('data-on', r.on ? 'true' : 'false');
      var toggle = el('input'); toggle.type = 'checkbox'; toggle.id = 'vwAi_' + r.id; toggle.checked = !!r.on;
      var label = el('label', null, r.label); label.setAttribute('for', toggle.id);
      row.appendChild(toggle); row.appendChild(label);
      var price = (Number(r.max_price_minor || 0) / 1000000).toString();
      row.appendChild(el('div', 'vw-cx-env', r.model + ' · up to ' + price + ' USDC per answer'));
      row.appendChild(el('div', 'vw-cx-env', r.base_url));
      if (!r.on && r.paused_reason) row.appendChild(el('div', 'vw-cx-warn', 'Paused: ' + r.paused_reason + '. Tick the box to turn it back on.'));
      var remove = el('button', 'vw-toggle', 'Remove'); remove.type = 'button'; remove.setAttribute('aria-label', 'remove ' + r.label);
      row.appendChild(remove);
      toggle.addEventListener('change', function(){
        var wanted = toggle.checked; toggle.disabled = true;
        postOwnerProved('/api/wallet/ai-endpoints', { action: 'set_on', id: r.id, on: wanted }, msg).then(function(res){
          toggle.disabled = false;
          if (!res.ok) { toggle.checked = !wanted; cxMsg(msg, faultMessage(res, 'The service was not changed.'), true); return; }
          draw(res.data.ai_endpoints); cxMsg(msg, r.label + (wanted ? ' is on.' : ' is off: nothing is asked or paid there.'));
        }).catch(function(){ toggle.disabled = false; toggle.checked = !wanted; cxMsg(msg, 'The request did not complete.', true); });
      });
      remove.addEventListener('click', function(){
        remove.disabled = true;
        postJson('/api/wallet/ai-endpoints', { action: 'remove', id: r.id }).then(function(res){
          remove.disabled = false;
          if (!res.ok) { cxMsg(msg, faultMessage(res, 'The service was not removed.'), true); return; }
          draw(res.data.ai_endpoints); cxMsg(msg, r.label + ' was removed.');
        }).catch(function(){ remove.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
      });
      list.appendChild(row);
    });
  }
  draw(cp.ai_endpoints || []);
  box.appendChild(list);
  var form = el('div', 'vw-cx-actions'); form.id = 'vwPaidAiForm';
  function field(id, placeholder, aria){ var i = el('input', 'vw-limit'); i.type = 'text'; i.id = id; i.autocomplete = 'off'; i.placeholder = placeholder; i.setAttribute('aria-label', aria); form.appendChild(i); return i; }
  var name = field('vwPaidAiName', 'Name, e.g. Research', 'name for this service');
  var url = field('vwPaidAiUrl', 'Address, starting with https://', 'service address, starting with https://');
  var model = field('vwPaidAiModel', 'Model name, exactly as listed', 'exact model name the service lists');
  var max = field('vwPaidAiMax', 'Most per answer, e.g. 0.05', 'most one answer may cost, in USDC');
  var add = el('button', 'vw-btn', 'Add service'); add.type = 'button'; add.id = 'vwPaidAiAdd';
  form.appendChild(add);
  add.addEventListener('click', function(){
    add.disabled = true;
    postOwnerProved('/api/wallet/ai-endpoints', { action: 'add', label: name.value, base_url: url.value, model: model.value, max_price: max.value }, msg).then(function(res){
      add.disabled = false;
      if (!res.ok) { cxMsg(msg, faultMessage(res, 'The service was not added.'), true); return; }
      name.value = ''; url.value = ''; model.value = ''; max.value = '';
      draw(res.data.ai_endpoints); cxMsg(msg, 'Added. Ask it from any chat; nothing is paid until you approve an answer.');
    }).catch(function(){ add.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
  });
  box.appendChild(form);
  box.appendChild(msg);
  return box;
}
var AGENT_PERIOD_WORD = { hour: 'hour', day: 'day', week: 'week', month: 'month' };
var AGENT_PAUSE_TEXT = {
  owner: 'You paused it.',
  breaker_refusals: 'VOOL paused it after three requests in a row that looked wrong (a changed pay-to address, a sudden price jump or requests coming too fast).',
  breaker_undelivered: 'VOOL paused it after two payments whose service did not deliver.'
};
var agentsBlock = null, agentsJustRead = false;
function agentControls(cp){
  // Agent spending: an agent's own small wallet and the limits a payment must pass before it goes out without asking.
  // Every rule is the wallet's; this block only shows them and sends what the person typed to the trusted Settings doors.
  // The Crypto surface is redrawn whenever its status is read again (another control's action, the page's refresh).
  // This block is ONE node that survives those redraws: what the person is typing, an open PIN box and the message
  // line stay as they are, and its facts are read again in place only when nothing is open in it.
  if (agentsBlock) {
    // a create that just read the list itself asks this one redraw to leave it be: a second read landing a moment
    // later would wipe a form or PIN box the person has already opened
    if (agentsJustRead) agentsJustRead = false;
    else if (!agentsBlock.node.querySelector('[data-agent-form], #vwOwnerProof')) agentsBlock.reload();
    return agentsBlock.node;
  }
  var box = el('div', 'vw-cx-row vw-cx-agents'); box.id = 'vwCxAgents';
  box.appendChild(el('div', 'vw-cx-title', 'Agent spending'));
  box.appendChild(el('div', 'vw-cx-env', 'Let VOOL pay small amounts to services you name without asking you each time. It pays only from its own small wallet, which you fill. Your own wallets are never used without you. Anything outside the limits you set waits for your approval.'));
  var body = el('div', 'vw-cx-agents-body'); body.id = 'vwAgentsBody'; box.appendChild(body);
  var msg = el('div', 'vw-msg'); msg.id = 'vwAgentsMsg'; box.appendChild(msg);
  function secretWord(d){ return d.owner_method === 'password' ? 'password' : 'PIN'; }
  function proved(path, payload){ return postOwnerProved(path, payload, msg); }
  function load(){
    return getJson('/api/wallet/agents').then(function(j){
      if (!j || !j.ok || !j.agent_spending) { body.textContent = ''; body.appendChild(el('div', 'vw-cx-warn', 'Agent spending could not be shown right now. Reload this page to try again.')); return; }
      draw(j.agent_spending);
    }).catch(function(){ body.textContent = ''; body.appendChild(el('div', 'vw-cx-warn', 'Agent spending could not be shown right now. Reload this page to try again.')); });
  }
  function after(res, okText, failText){
    if (!res.ok) { cxMsg(msg, faultMessage(res, failText), true); return false; }
    cxMsg(msg, okText);
    if (res.data && res.data.agent_spending) draw(res.data.agent_spending); else load();
    return true;
  }
  function field(host, id, labelText, placeholder, value){
    var wrap = el('div', 'vw-cx-agent-field');
    var label = el('label', null, labelText); label.setAttribute('for', id); wrap.appendChild(label);
    var input = el('input', 'vw-limit'); input.type = 'text'; input.id = id; input.autocomplete = 'off'; input.placeholder = placeholder || ''; input.value = value || '';
    wrap.appendChild(input); host.appendChild(wrap);
    return input;
  }
  function allowanceForm(d, agent, wallet, coins, current){
    // one form for a new allowance and for changing one; a change that allows more asks for the PIN, a tighter one does not
    var key = current ? current.allowance_id : wallet.wallet_id;
    var form = el('div', 'vw-form vw-cx-agent-form'); form.setAttribute('data-agent-form', key);
    var coin = null;
    if (!current) {
      var cw = el('div', 'vw-cx-agent-field'); var cl = el('label', null, 'Coin'); cl.setAttribute('for', 'vwAgCoin_' + key); cw.appendChild(cl);
      coin = el('select', 'vw-limit'); coin.id = 'vwAgCoin_' + key;
      coins.forEach(function(c){ var o = el('option', null, c.symbol); o.value = c.symbol; coin.appendChild(o); });
      cw.appendChild(coin); form.appendChild(cw);
    }
    var per = field(form, 'vwAgPer_' + key, 'Most for one payment', 'e.g. 0.05', current && current.per_payment);
    var pw = el('div', 'vw-cx-agent-field'); var pl = el('label', null, 'Most per'); pl.setAttribute('for', 'vwAgPeriod_' + key); pw.appendChild(pl);
    var period = el('select', 'vw-limit'); period.id = 'vwAgPeriod_' + key;
    (d.periods || ['hour', 'day', 'week', 'month']).forEach(function(p){ var o = el('option', null, AGENT_PERIOD_WORD[p] || p); o.value = p; period.appendChild(o); });
    period.value = (current && current.period) || 'day'; pw.appendChild(period);
    var periodAmount = el('input', 'vw-limit'); periodAmount.type = 'text'; periodAmount.id = 'vwAgPeriodAmount_' + key; periodAmount.autocomplete = 'off'; periodAmount.placeholder = 'e.g. 1';
    periodAmount.setAttribute('aria-label', 'most in that period'); periodAmount.value = (current && current.period_amount) || ''; pw.appendChild(periodAmount); form.appendChild(pw);
    var total = field(form, 'vwAgTotal_' + key, 'Most in total', 'e.g. 5', current && current.total);
    var count = field(form, 'vwAgCount_' + key, 'Most payments in total', 'e.g. 100', current ? String(current.max_payments) : '');
    var days = field(form, 'vwAgDays_' + key, current ? 'Days from now until it ends (leave empty to keep the end date)' : 'Days until it ends (1 to ' + (d.max_days || 90) + ')', String(d.default_days || 30), '');
    var ask = field(form, 'vwAgAsk_' + key, 'Always ask me above (optional)', 'e.g. 0.5', current && current.ask_above);
    form.appendChild(el('div', 'vw-cx-env', 'Helper agents (optional). When VOOL splits a job between helper agents, each helper may spend this much out of this allowance, on the same services. Leave both empty and helpers pay nothing.'));
    var helperPer = field(form, 'vwAgHelperPer_' + key, 'Most for one helper payment', 'empty: helpers pay nothing', current && current.helper_per_payment);
    var helperTotal = field(form, 'vwAgHelperTotal_' + key, 'Most one helper may spend in total', 'empty: helpers pay nothing', current && current.helper_total);
    form.appendChild(el('div', 'vw-cx-env', 'Services it may pay. Type each service address, and the most it may get in total. The address a service is paid at is fixed when you approve its first payment; if it ever changes, VOOL asks you.'));
    var list = el('div', 'vw-cx-agent-services'); form.appendChild(list);
    var rows = [];
    function addService(address, limit){
      var n = rows.length;
      var row = el('div', 'vw-cx-actions');
      var a = el('input', 'vw-limit'); a.type = 'text'; a.autocomplete = 'off'; a.placeholder = 'Service address, starting with https://'; a.setAttribute('aria-label', 'service address ' + (n + 1)); a.id = 'vwAgSvc_' + key + '_' + n; a.value = address || '';
      var l = el('input', 'vw-limit'); l.type = 'text'; l.autocomplete = 'off'; l.placeholder = 'Most in total (empty: the full total)'; l.setAttribute('aria-label', 'most for service ' + (n + 1)); l.id = 'vwAgSvcMax_' + key + '_' + n; l.value = limit || '';
      row.appendChild(a); row.appendChild(l); list.appendChild(row); rows.push({ address: a, limit: l });
    }
    if (current && current.services && current.services.length) current.services.forEach(function(s){ addService(s.address, s.limit); }); else addService('', '');
    var acts = el('div', 'vw-cx-actions');
    var more = el('button', 'vw-btn', 'Add another service'); more.type = 'button'; more.addEventListener('click', function(){ addService('', ''); });
    var save = el('button', 'vw-btn primary', current ? 'Save changes' : 'Set allowance'); save.type = 'button'; save.id = 'vwAgSave_' + key;
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.type = 'button';
    acts.appendChild(more); acts.appendChild(save); acts.appendChild(cancel); form.appendChild(acts);
    // what a save says stays in the form, next to the fields, not at the foot of the whole section
    var fmsg = el('div', 'vw-msg'); fmsg.id = 'vwAgentMsg_form_' + key; fmsg.setAttribute('role', 'status'); form.appendChild(fmsg);
    cancel.addEventListener('click', function(){ form.remove(); cxMsg(msg, ''); });
    save.addEventListener('click', function(){
      var payload = { per_payment: per.value, period: period.value, period_amount: periodAmount.value, total: total.value, max_payments: count.value, days: days.value, ask_above: ask.value, helper_per_payment: helperPer.value, helper_total: helperTotal.value,
        services: rows.map(function(r){ return { address: r.address.value, limit: r.limit.value }; }).filter(function(s){ return String(s.address || '').trim(); }) };
      if (current) payload.allowance_id = current.allowance_id; else { payload.wallet_id = wallet.wallet_id; payload.asset = coin.value; }
      var typed = [per, periodAmount, total, count].some(function(i){ return String(i.value || '').trim(); }) || payload.services.length;
      if (!current && !typed) { cxMsg(fmsg, WT('wallet.agents.allowance_empty', 'Fill in the limits and at least one service address first. Nothing was saved.'), true); return; }
      save.disabled = true; cxMsg(fmsg, '');
      postOwnerProved('/api/wallet/agents/allowance', payload, fmsg).then(function(res){
        save.disabled = false;
        if (!res.ok) { cxMsg(fmsg, faultMessage(res, 'The allowance was not saved.'), true); return; }
        after(res, current ? 'Saved.' : 'The allowance is set. VOOL can now pay these services inside these limits without asking.', 'The allowance was not saved.');
      }).catch(function(){ save.disabled = false; cxMsg(fmsg, 'The request did not complete. Nothing was changed.', true); });
    });
    return form;
  }
  function allowanceCard(d, agent, wallet, a){
    var card = el('div', 'vw-cx-channel vw-cx-allowance'); card.setAttribute('data-allowance', a.allowance_id); card.setAttribute('data-state', a.unreadable ? 'unreadable' : a.state);
    if (a.unreadable) { card.appendChild(el('div', 'vw-cx-warn', 'This allowance could not be read, so nothing is paid on it. End it and set a new one.')); }
    else {
      var stateText = a.state === 'active' ? 'On' : (a.state === 'paused' ? 'Paused' : (a.state === 'expired' ? 'Ended on its end date' : 'Ended'));
      card.appendChild(el('div', 'vw-line', a.asset + ' allowance · ' + stateText));
      if (a.state === 'paused') card.appendChild(el('div', 'vw-cx-warn', (AGENT_PAUSE_TEXT[a.pause_reason] || 'It is paused.') + ' Nothing is paid on it until you turn it back on.'));
      var word = AGENT_PERIOD_WORD[a.period] || a.period;
      var facts = el('div', 'vw-cx-env', 'Up to ' + a.per_payment + ' ' + a.asset + ' for one payment · ' + a.period_used + ' of ' + a.period_amount + ' ' + a.asset + ' used this ' + word + ' · ' + a.total_used + ' of ' + a.total + ' ' + a.asset + ' used in total · ' + a.payments + ' of ' + a.max_payments + ' payments · ' + (a.state === 'expired' ? 'ended' : (a.days_left + (a.days_left === 1 ? ' day left' : ' days left'))) + (a.ask_above ? ' · asks you above ' + a.ask_above + ' ' + a.asset : ''));
      facts.setAttribute('data-allowance-facts', a.allowance_id); card.appendChild(facts);
      var helpers = el('div', 'vw-cx-env', a.helper_total ? ('Helper agents: each may spend up to ' + a.helper_per_payment + ' ' + a.asset + ' for one payment and ' + a.helper_total + ' ' + a.asset + ' in total, out of this allowance.') : 'Helper agents pay nothing.');
      helpers.setAttribute('data-allowance-helpers', a.helper_total ? 'on' : 'off'); card.appendChild(helpers);
      (a.services || []).forEach(function(s){
        var line = el('div', 'vw-cx-env', s.address + ' · ' + s.used + ' of ' + s.limit + ' ' + a.asset + ' used · ' + (s.pinned ? 'paid at ' + short(s.pay_to) : 'its first payment asks you'));
        line.setAttribute('data-service', s.address); card.appendChild(line);
      });
    }
    var acts = el('div', 'vw-cx-actions');
    function stateButton(text, action, okText){
      var b = el('button', 'vw-btn', text); b.type = 'button'; b.setAttribute('data-allowance-action', action);
      b.addEventListener('click', function(){
        b.disabled = true;
        (action === 'resume' ? proved : postJson)('/api/wallet/agents/allowance/state', { allowance_id: a.allowance_id, action: action }).then(function(res){ b.disabled = false; after(res, okText, 'The allowance was not changed.'); })
          .catch(function(){ b.disabled = false; cxMsg(msg, 'The request did not complete. Nothing was changed.', true); });
      });
      acts.appendChild(b);
    }
    if (!a.unreadable && a.state === 'active') stateButton('Pause', 'pause', 'Paused. Nothing is paid without asking you until you turn it back on.');
    if (!a.unreadable && a.state === 'paused') stateButton('Turn back on', 'resume', 'The allowance is on again.');
    if (!a.unreadable && (a.state === 'active' || a.state === 'paused')) {
      var change = el('button', 'vw-btn', 'Change'); change.type = 'button'; change.setAttribute('data-allowance-action', 'change');
      change.addEventListener('click', function(){
        var open = card.querySelector('[data-agent-form]'); if (open) { open.remove(); return; }
        card.appendChild(allowanceForm(d, agent, wallet, [], a));
      });
      acts.appendChild(change);
    }
    stateButton('End', 'end', 'The allowance is ended. Every payment asks you again.');
    card.appendChild(acts);
    return card;
  }
  function walletCard(d, agent, w){
    var card = el('div', 'vw-cx-account vw-cx-agent-wallet'); card.setAttribute('data-agent-wallet', w.wallet_id); card.setAttribute('data-setup-state', w.setup_state || '');
    card.appendChild(el('div', 'vw-line', 'Its wallet on ' + (w.network_name || w.network)));
    var addr = el('div', 'vw-cx-address', w.address || ''); card.appendChild(addr);
    var acts = el('div', 'vw-cx-actions'); acts.appendChild(copyButton(w.address, 'address')); card.appendChild(acts);
    if (!w.ready) {
      card.appendChild(el('div', 'vw-cx-warn', 'This wallet is not finished: its backup key has not been saved yet. It cannot pay until you do.'));
      if (w.setup_state === 'backup_revealed') {
        // shown once already and closed without confirming: it is not shown again, the owner confirms with their own PIN
        var saved = el('button', 'vw-btn primary', 'I have saved the backup key'); saved.type = 'button'; saved.setAttribute('data-agent-backup-saved', w.wallet_id);
        saved.addEventListener('click', function(){
          saved.disabled = true;
          proved('/api/wallet/agents/backup', { wallet_id: w.wallet_id, confirm_saved: true }).then(function(res){
            saved.disabled = false;
            if (!res.ok) { cxMsg(msg, faultMessage(res, 'Not confirmed.'), true); return; }
            cxMsg(msg, 'The wallet is ready. Send it only what you are willing to let VOOL spend.'); renderCrypto();
          }).catch(function(){ saved.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
        });
        acts.appendChild(saved);
        card.appendChild(el('div', 'vw-cx-env', 'The backup key was shown once and is not shown again. If you did not save it, do not fill this wallet.'));
        return card;
      }
      var show = el('button', 'vw-btn primary', 'Show the backup key'); show.type = 'button'; show.setAttribute('data-agent-backup', w.wallet_id);
      show.addEventListener('click', function(){
        show.disabled = true;
        proved('/api/wallet/agents/backup', { wallet_id: w.wallet_id }).then(function(res){
          show.disabled = false;
          if (!res.ok) { cxMsg(msg, faultMessage(res, 'The backup key was not shown.'), true); return; }
          cxMsg(msg, '');
          showBackup({ wallet_id: w.wallet_id, network: w.network, display_name: w.network_name, address: w.address }, res.data.backup, function(done){ cxMsg(msg, done ? 'The wallet is ready. Send it only what you are willing to let VOOL spend.' : 'The backup key will not be shown again.'); renderCrypto(); });
        }).catch(function(){ show.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
      });
      acts.appendChild(show);
      return card;
    }
    var held = el('div', 'vw-cx-balance', 'Reading what is in it…'); held.setAttribute('data-agent-balance', 'reading'); card.appendChild(held);
    getJson('/api/wallet/agents/balance?wallet_id=' + encodeURIComponent(w.wallet_id)).then(function(j){
      var rows = (j && j.ok && j.balances) || [];
      var read = rows.filter(function(b){ return b.read; });
      if (!read.length) { held.textContent = 'What is in it could not be read right now (this does not mean it is empty).'; held.setAttribute('data-agent-balance', 'unavailable'); return; }
      held.textContent = 'In it now: ' + read.map(function(b){ return b.amount + ' ' + b.asset; }).join(' · ') + (read.length < rows.length ? ' · the rest could not be read' : '');
      held.className = 'vw-cx-balance read'; held.setAttribute('data-agent-balance', 'read');
    }).catch(function(){ held.textContent = 'What is in it could not be read right now (this does not mean it is empty).'; held.setAttribute('data-agent-balance', 'unavailable'); });
    card.appendChild(el('div', 'vw-cx-env', 'To fill it, send coins to this address from your own wallet. Send only what you are willing to let VOOL spend: that amount is the most this wallet can ever lose. Its backup key lets you take the money back out with any wallet app.'));
    if (agent.agent_id === 'addons') return card;  // its limits are per plugin, in the list below it
    var network = (d.networks || []).filter(function(n){ return n.network === w.network; })[0];
    var coins = network ? network.coins : [];
    var mine = (agent.allowances || []).filter(function(a){ return a.wallet_id === w.wallet_id; });
    mine.forEach(function(a){ card.appendChild(allowanceCard(d, agent, w, a)); });
    var live = mine.filter(function(a){ return !a.unreadable && (a.state === 'active' || a.state === 'paused' || a.state === 'expired'); }).map(function(a){ return a.asset; });
    var free = coins.filter(function(c){ return live.indexOf(c.symbol) < 0; });
    if (!network) card.appendChild(el('div', 'vw-cx-env', 'This wallet is on the other network environment, so no new allowance can be set for it now.'));
    else if (free.length) {
      var add = el('button', 'vw-btn', mine.length ? 'Set an allowance for another coin' : 'Set an allowance'); add.type = 'button'; add.setAttribute('data-agent-allow', w.wallet_id);
      add.addEventListener('click', function(){
        var open = card.querySelector('[data-agent-form="' + w.wallet_id + '"]'); if (open) { open.remove(); return; }
        card.appendChild(allowanceForm(d, agent, w, free, null));
      });
      var row = el('div', 'vw-cx-actions'); row.appendChild(add); card.appendChild(row);
      if (!mine.length) card.appendChild(el('div', 'vw-cx-env', 'No allowance yet: every payment from this wallet asks you first.'));
    }
    return card;
  }
  function draw(d){
    body.textContent = '';
    if (d.frozen) body.appendChild(el('div', 'vw-cx-warn', 'Payments are frozen, so nothing is paid on any allowance until the freeze is lifted.'));
    if (!d.owner_wallet) { var need = el('div', 'vw-cx-env', 'Create your own wallet above first. An agent\'s wallet is created with your wallet\'s PIN or password, so only you can set this up.'); need.id = 'vwAgentsNeedOwner'; body.appendChild(need); return; }
    (d.agents || []).forEach(function(agent){ body.appendChild(agentBlock(d, agent)); });
    if (d.addons) body.appendChild(addonsBlock(d));
    drawTail(d);
  }
  // what a create said stays under the button that was pressed, across the redraws that follow it
  var blockSaid = {};
  function agentBlock(d, agent){
    {
      var block = el('div', 'vw-cx-agent'); block.setAttribute('data-agent', agent.agent_id);
      var bmsg = el('div', 'vw-msg'); bmsg.id = 'vwAgentMsg_' + agent.agent_id; bmsg.setAttribute('role', 'status');
      var said = blockSaid[agent.agent_id];
      if (said) cxMsg(bmsg, said.text, said.bad);
      function say(text, bad){ blockSaid[agent.agent_id] = text ? { text: text, bad: !!bad } : null; cxMsg(bmsg, text || '', bad); }
      if (agent.agent_id !== 'addons') block.appendChild(el('div', 'vw-line', agent.name));  // the Skills & plugins box has its own title
      (agent.wallets || []).forEach(function(w){ block.appendChild(walletCard(d, agent, w)); });
      var has = (agent.wallets || []).map(function(w){ return w.network; });
      var open = (d.networks || []).filter(function(n){ return has.indexOf(n.network) < 0; });
      if (open.length) {
        var row = el('div', 'vw-cx-actions');
        var pick = el('select', 'vw-limit'); pick.id = 'vwAgentNet_' + agent.agent_id; pick.setAttribute('aria-label', 'network for the wallet of ' + agent.name);
        open.forEach(function(n){ var o = el('option', null, n.name); o.value = n.network; pick.appendChild(o); });
        // two of these sit one above the other (the assistant's and the Skills & plugins one): each button names whose wallet it makes
        var create = el('button', 'vw-btn', (agent.wallets || []).length ? 'Create a wallet on another network'
          : (agent.agent_id === 'addons' ? WT('wallet.agents.create_addons', 'Create the Skills & plugins wallet') : WTF('wallet.agents.create_agent', 'Create a wallet for {name}', { name: agent.name })));
        create.type = 'button'; create.id = 'vwAgentCreate_' + agent.agent_id;
        create.addEventListener('click', function(){
          create.disabled = true; say('');
          var chosen = open.filter(function(n){ return n.network === pick.value; })[0] || open[0];
          var whose = agent.agent_id === 'addons' ? 'Skills & plugins' : agent.name;
          // the PIN box opens under this button, not at the foot of the whole section
          postOwnerProved('/api/wallet/agents/wallet', { agent_id: agent.agent_id, network: pick.value }, bmsg).then(function(res){
            create.disabled = false;
            // a refusal or a half-finished create still re-reads the list: a wallet that exists is shown, with its next step
            if (!res.ok) { say(faultMessage(res, 'The wallet was not created.'), true); load(); return; }
            var w = res.data.wallet || {};
            if (!res.data.backup) { load(); return; }
            showBackup({ wallet_id: w.wallet_id, network: w.network, display_name: chosen.name, address: w.address }, res.data.backup, function(done){
              say(done ? WTF('wallet.agents.ready', 'Done: {name} has its own wallet now, shown above. Send it only what you are willing to let VOOL spend.', { name: whose })
                : WTF('wallet.agents.unfinished', '{name} has its own wallet now, shown above, but it is not finished: its backup key was not confirmed and will not be shown again.', { name: whose }));
              // re-read the list here: the section's own refresh skips it while a form is open (an allowance form left
              // open on another wallet), and then the new wallet was not shown until the page was reloaded
              load(); agentsJustRead = true; renderCrypto();
            });
          }).catch(function(){ create.disabled = false; say('The request did not complete.', true); load(); });
        });
        row.appendChild(pick); row.appendChild(create); block.appendChild(row);
        if (!(agent.wallets || []).length) block.appendChild(el('div', 'vw-cx-env', 'It has no wallet yet, so it pays nothing on its own. Creating one needs your wallet ' + secretWord(d) + ' and moves no money.'));
      }
      block.appendChild(bmsg);
      return block;
    }
  }
  function addonsBlock(d){
    // the Skills & plugins wallet: its own wallet, and per plugin a tick and that plugin's own limits
    var a = d.addons;
    var box = el('div', 'vw-cx-addons'); box.id = 'vwAddons';
    box.appendChild(el('div', 'vw-cx-title', 'Skills & plugins wallet'));
    box.appendChild(el('div', 'vw-cx-env', 'A separate wallet for skills and plugins that pay for services. Your own wallets and the assistant\'s wallet are never used for them. Nothing is allowed until you tick it, and each plugin gets its own limits. A skill that is only instructions cannot pay by itself.'));
    box.appendChild(agentBlock(d, { agent_id: 'addons', name: a.name || 'Skills & plugins', wallets: a.wallets || [], allowances: [] }));
    var list = el('div', 'vw-cx-addon-list'); list.id = 'vwAddonList'; box.appendChild(list);
    var plugins = a.plugins || [];
    if (!plugins.length) { list.appendChild(el('div', 'vw-cx-env', 'No installed plugin asks to pay right now.')); return box; }
    var ready = (a.wallets || []).filter(function(w){ return w.ready; });
    plugins.forEach(function(p){ list.appendChild(addonRow(d, p, ready)); });
    return box;
  }
  function addonRow(d, p, ready){
    var row = el('div', 'vw-cx-channel vw-cx-addon'); row.setAttribute('data-addon', p.addon_id); row.setAttribute('data-allowed', p.allowed ? 'true' : 'false');
    var head = el('label', 'vw-line vw-cx-addon-head');
    var tick = el('input'); tick.type = 'checkbox'; tick.checked = !!p.allowed; tick.id = 'vwAddonTick_' + p.addon_id.replace(/[^a-z0-9]/gi, '_'); tick.setAttribute('data-addon-tick', p.addon_id);
    head.appendChild(tick); head.appendChild(document.createTextNode(' ' + p.name + (p.kind === 'skill' ? ' (skill)' : ' (plugin)')));
    row.appendChild(head);
    row.appendChild(el('div', 'vw-cx-env', 'Pays: ' + (p.services || []).join(', ') + ((p.skills || []).length ? ' · skills inside: ' + p.skills.join(', ') : '')));
    var al = p.allowance;
    if (al && !al.unreadable) {
      var word = AGENT_PERIOD_WORD[al.period] || al.period; var now = al.period === 'day' ? 'today' : 'this ' + word;
      var stateText = al.state === 'active' ? 'Allowed' : (al.state === 'paused' ? 'Paused' : 'Ended');
      var facts = el('div', 'vw-cx-env', stateText + ' · up to ' + al.per_payment + ' ' + al.asset + ' a payment · ' + al.period_used + ' of ' + al.period_amount + ' ' + al.asset + ' ' + now
        + (al.period_payments ? ' · ' + al.period_payments_used + ' of ' + al.period_payments + ' payments ' + now : '') + ' · ' + al.days_left + (al.days_left === 1 ? ' day left' : ' days left') + (al.ask_above ? ' · asks you above ' + al.ask_above + ' ' + al.asset : ''));
      facts.setAttribute('data-addon-facts', p.addon_id); row.appendChild(facts);
      if (al.state === 'paused') row.appendChild(el('div', 'vw-cx-warn', (AGENT_PAUSE_TEXT[al.pause_reason] || 'It is paused.') + ' It pays nothing until you turn it back on.'));
    }
    var acts = el('div', 'vw-cx-actions'); row.appendChild(acts);
    function state(action, okText){
      return (action === 'resume' ? proved : postJson)('/api/wallet/agents/allowance/state', { allowance_id: al.allowance_id, action: action }).then(function(res){ after(res, okText, 'Nothing was changed.'); })
        .catch(function(){ cxMsg(msg, 'The request did not complete. Nothing was changed.', true); });
    }
    tick.addEventListener('change', function(){
      if (!tick.checked && al && !al.unreadable) { tick.disabled = true; state('end', p.name + ' can no longer pay. Its limits were removed.'); return; }
      if (tick.checked) { tick.checked = false; if (!row.querySelector('[data-agent-form]')) row.appendChild(addonForm(d, p, ready, null)); }
    });
    if (al && !al.unreadable && (al.state === 'active' || al.state === 'paused')) {
      var change = el('button', 'vw-btn', 'Change limits'); change.type = 'button'; change.setAttribute('data-addon-action', 'change');
      change.addEventListener('click', function(){ var open = row.querySelector('[data-agent-form]'); if (open) { open.remove(); return; } row.appendChild(addonForm(d, p, ready, al)); });
      acts.appendChild(change);
      var pr = el('button', 'vw-btn', al.state === 'active' ? 'Pause' : 'Turn back on'); pr.type = 'button'; pr.setAttribute('data-addon-action', al.state === 'active' ? 'pause' : 'resume');
      pr.addEventListener('click', function(){ pr.disabled = true; state(al.state === 'active' ? 'pause' : 'resume', al.state === 'active' ? 'Paused.' : 'On again.'); });
      acts.appendChild(pr);
    }
    return row;
  }
  function addonForm(d, p, ready, current){
    var key = current ? current.allowance_id : p.addon_id;
    var form = el('div', 'vw-form vw-cx-agent-form'); form.setAttribute('data-agent-form', key);
    if (!current && !ready.length) { form.appendChild(el('div', 'vw-cx-warn', 'Create the Skills & plugins wallet above and save its backup key first. Then come back and allow this plugin.')); return form; }
    var walletPick = null, coin = null;
    if (!current) {
      var ww = el('div', 'vw-cx-agent-field'); var wl = el('label', null, 'Wallet'); wl.setAttribute('for', 'vwAdWallet_' + key); ww.appendChild(wl);
      walletPick = el('select', 'vw-limit'); walletPick.id = 'vwAdWallet_' + key;
      ready.forEach(function(w){ var o = el('option', null, (w.label || 'Skills & plugins') + ' · ' + (w.network_name || w.network)); o.value = w.wallet_id; walletPick.appendChild(o); });
      ww.appendChild(walletPick); form.appendChild(ww);
      var cw = el('div', 'vw-cx-agent-field'); var cl = el('label', null, 'Coin'); cl.setAttribute('for', 'vwAdCoin_' + key); cw.appendChild(cl);
      coin = el('select', 'vw-limit'); coin.id = 'vwAdCoin_' + key; cw.appendChild(coin); form.appendChild(cw);
      var fillCoins = function(){
        coin.textContent = '';
        var w = ready.filter(function(x){ return x.wallet_id === walletPick.value; })[0] || ready[0];
        var net = (d.networks || []).filter(function(n){ return n.network === w.network; })[0];
        ((net && net.coins) || []).forEach(function(c){ var o = el('option', null, c.symbol); o.value = c.symbol; coin.appendChild(o); });
      };
      walletPick.addEventListener('change', fillCoins); fillCoins();
    }
    var per = field(form, 'vwAdPer_' + key, 'Most for one payment', 'e.g. 0.05', current && current.per_payment);
    var pw = el('div', 'vw-cx-agent-field'); var pl = el('label', null, 'Most per'); pl.setAttribute('for', 'vwAdPeriod_' + key); pw.appendChild(pl);
    var period = el('select', 'vw-limit'); period.id = 'vwAdPeriod_' + key;
    ['hour', 'day', 'week'].forEach(function(x){ var o = el('option', null, AGENT_PERIOD_WORD[x] || x); o.value = x; period.appendChild(o); });
    period.value = (current && current.period) || 'day'; pw.appendChild(period);
    var amount = el('input', 'vw-limit'); amount.type = 'text'; amount.id = 'vwAdAmount_' + key; amount.autocomplete = 'off'; amount.placeholder = 'e.g. 1'; amount.setAttribute('aria-label', 'most in that period');
    amount.value = (current && current.period_amount) || ''; pw.appendChild(amount); form.appendChild(pw);
    var count = field(form, 'vwAdCount_' + key, 'Most payments in that period', 'e.g. 20', current && current.period_payments ? String(current.period_payments) : '');
    var days = field(form, 'vwAdDays_' + key, current ? 'Days from now until it ends (leave empty to keep the end date)' : 'Days until it ends (1 to ' + (d.max_days || 90) + ')', String(d.default_days || 30), '');
    var ask = field(form, 'vwAdAsk_' + key, 'Always ask me above (optional)', 'e.g. 0.5', current && current.ask_above);
    form.appendChild(el('div', 'vw-cx-env', 'It may pay only: ' + (p.services || []).join(', ') + '. The address each service is paid at is fixed when you approve its first payment.'));
    var acts = el('div', 'vw-cx-actions');
    var save = el('button', 'vw-btn primary', current ? 'Save limits' : 'Allow ' + p.name); save.type = 'button'; save.id = 'vwAdSave_' + key.replace(/[^a-z0-9]/gi, '_');
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.type = 'button';
    acts.appendChild(save); acts.appendChild(cancel); form.appendChild(acts);
    cancel.addEventListener('click', function(){ form.remove(); cxMsg(msg, ''); });
    save.addEventListener('click', function(){
      var payload = { addon_id: p.addon_id, per_payment: per.value, period: period.value, period_amount: amount.value, period_payments: count.value, days: days.value, ask_above: ask.value };
      if (!current) { payload.wallet_id = walletPick.value; payload.asset = coin.value; }
      save.disabled = true;
      proved('/api/wallet/addons/allowance', payload).then(function(res){
        save.disabled = false;
        after(res, current ? 'Saved.' : p.name + ' may now pay its services inside these limits.', 'Nothing was allowed.');
      }).catch(function(){ save.disabled = false; cxMsg(msg, 'The request did not complete. Nothing was changed.', true); });
    });
    return form;
  }
  function drawTail(d){
    if ((d.waiting || []).length) {
      var wait = el('div', 'vw-cx-agent-waiting'); wait.id = 'vwAgentsWaiting';
      wait.appendChild(el('div', 'vw-line', 'Waiting for your approval'));
      d.waiting.forEach(function(p){ var line = el('div', 'vw-cx-env', p.amount + ' ' + p.asset + ' to ' + short(p.to) + ' · approve or decline it on its card in the chat that asked'); line.setAttribute('data-waiting', p.proposal_id); wait.appendChild(line); });
      body.appendChild(wait);
    }
    if ((d.recent || []).length) {
      var recent = el('div', 'vw-cx-agent-recent'); recent.id = 'vwAgentsRecent';
      recent.appendChild(el('div', 'vw-line', 'Recent requests'));
      d.recent.forEach(function(r){
        var when = ''; try { when = new Date(Number(r.at) * 1000).toLocaleString(); } catch (e) { when = ''; }
        var line = el('div', 'vw-cx-env', (when ? when + ' · ' : '') + (r.service || 'a service') + ' · ' + (r.paid ? 'paid' : 'not paid') + (r.amount ? ' ' + r.amount + ' ' + r.asset : '') + ' · ' + r.why);
        line.setAttribute('data-paid', r.paid ? 'true' : 'false'); recent.appendChild(line);
      });
      body.appendChild(recent);
    }
  }
  body.appendChild(el('div', 'vw-cx-env', 'Reading…'));
  agentsBlock = { node: box, reload: load };
  load();
  return box;
}
function capChips(r){
  var caps = r.capabilities || {};
  var wrap = el('div', 'vw-cx-caps');
  ['create', 'balance', 'quote', 'sign', 'send'].forEach(function(name){
    var c = caps[name] || { available: false, reason: '' };
    var chip = el('span', 'vw-cx-cap' + (c.available ? ' on' : ''), CAP_LABELS[name] + (c.available ? '' : ' — ' + (CAP_REASONS[c.reason] || c.reason || 'unavailable')));
    chip.setAttribute('data-cap', name); chip.setAttribute('data-available', c.available ? 'true' : 'false');
    wrap.appendChild(chip);
  });
  return wrap;
}
// "Solana · Solana Devnet" names the chain and the network; a mainnet row whose network is named like its chain
// ("Solana · Solana") is the chain once.
function rowName(r){
  var chain = String(r.chain_label || ''), network = String(r.display_name || '');
  if (!chain) return network;
  if (!network || network === chain) return chain;
  return chain + ' · ' + network;
}

function rowCard(r, cp, st){
  var card = el('div', 'vw-cx-row' + (r.active ? '' : ' inactive')); card.setAttribute('data-network', r.network); card.setAttribute('data-chain', r.chain_key); card.setAttribute('data-active', r.active ? 'true' : 'false');
  var title = el('div', 'vw-cx-title');
  title.appendChild(document.createTextNode(rowName(r)));
  title.appendChild(el('span', 'vw-badge' + (r.environment === 'mainnet' ? '' : ' testnet'), r.badge));
  if (!r.active) title.appendChild(el('span', 'vw-badge mode', 'not active'));
  card.appendChild(title);
  if (r.chain_key === 'robinhood') card.appendChild(el('div', 'vw-cx-env', 'Robinhood Chain is a public network, not the Robinhood brokerage or its wallet.'));
  var works = el('details', 'vw-cx-fold vw-cx-works');
  works.appendChild(el('summary', null, WT('wallet.cx.works', 'What works here')));
  works.appendChild(el('div', 'vw-cx-env', (r.value_note || '') + ' · native coin ' + (r.native_display_symbol || r.native_symbol) + (r.explorer_label ? ' · explorer: ' + r.explorer_label : '')));
  works.appendChild(capChips(r));
  card.appendChild(works);
  var accounts = r.accounts || [];
  var caps = r.capabilities || {};
  accounts.forEach(function(a){ card.appendChild(accountCard(a, r, cp, st)); });
  var readyPilot = accounts.filter(function(a){ return a.mode === 'pocket_sealed' && a.setup_state === 'ready'; });
  var inProgress = accounts.filter(function(a){ return a.mode === 'pocket_sealed' && a.setup_state !== 'ready'; });
  var msg = el('div', 'vw-msg'); msg.className = 'vw-msg';
  if (r.active && caps.create && caps.create.available && !readyPilot.length && !inProgress.length) {
    var create = el('button', 'vw-btn primary vw-cx-create', WT('wallet.home.create_go', 'Create wallet')); create.type = 'button';
    create.setAttribute('aria-label', 'create a VOOL wallet on ' + r.display_name);
    create.addEventListener('click', function(){ openCreateFlow(card, r, cp, msg, create); });
    card.appendChild(create);
  } else if (r.active && caps.create && caps.create.available && readyPilot.length && !inProgress.length) {
    // another wallet of the person's on this network: its own key, backup and PIN; Main stays where it is
    var more = el('button', 'vw-btn vw-cx-add-wallet', 'Add another wallet'); more.type = 'button';
    more.setAttribute('aria-label', 'add another VOOL wallet on ' + r.display_name);
    more.addEventListener('click', function(){ openCreateFlow(card, r, cp, msg, more, { more: true }); });
    card.appendChild(more);
  } else if (r.active && !(caps.create && caps.create.available) && !accounts.length) {
    card.appendChild(el('div', 'vw-cx-env', 'Create wallet is unavailable here: ' + (CAP_REASONS[(caps.create || {}).reason] || (caps.create || {}).reason || 'unavailable') + '.'));
  }
  if (r.active && r.hardware_keys && caps.create && caps.create.available && String(r.network).indexOf('solana:') === 0 && !inProgress.length) card.appendChild(ledgerAdd(r, cp, msg));
  card.appendChild(msg);
  return card;
}
function ledgerAdd(r, cp, msg){
  // a Ledger's Solana account as a VOOL wallet: the key stays on the Ledger, every send is pressed there
  var box = el('div', 'vw-cx-ledger'); box.setAttribute('data-network', r.network);
  var open = el('button', 'vw-btn vw-cx-ledger-add', 'Add a Ledger'); open.type = 'button'; box.appendChild(open);
  var f = el('div', 'vw-cx-form vw-cx-ledger-form'); f.hidden = true; box.appendChild(f);
  f.appendChild(el('div', 'vw-line', 'Plug in your Ledger, unlock it and open its Solana app. To prove it is your Ledger, it shows a 0 SOL transfer to its own address that can never be sent: press approve on the Ledger. Nothing is sent. The key never leaves the Ledger. Every payment from it will ask for the PIN or password you choose here, then show on the Ledger for you to check and press. If you forget this PIN, add the same Ledger again.'));
  var nid = 'vwLd-' + r.network.replace(/[^a-zA-Z0-9]/g, '');
  var al = el('label', null, 'Ledger account (0 is the first)'); al.setAttribute('for', nid + 'Account'); f.appendChild(al);
  var account = el('input'); account.type = 'number'; account.min = '0'; account.max = '999'; account.value = '0'; account.id = nid + 'Account'; f.appendChild(account);
  var creds = credentialPrompt(f, nid, cp);
  var devOpt = creds.method.querySelector('option[value="device"]'); if (devOpt) devOpt.remove();
  var ll = el('label', null, 'Wallet name (optional)'); ll.setAttribute('for', nid + 'Label'); f.appendChild(ll);
  var label = el('input'); label.type = 'text'; label.id = nid + 'Label'; label.maxLength = 40; label.autocomplete = 'off'; f.appendChild(label);
  var row = el('div', 'vw-sheet-actions'); var go = el('button', 'vw-btn primary vw-cx-ledger-go', 'Add this Ledger'); go.type = 'button';
  var back = el('button', 'vw-btn', 'Cancel'); back.type = 'button'; row.appendChild(go); row.appendChild(back); f.appendChild(row);
  open.addEventListener('click', function(){
    f.hidden = !f.hidden; if (f.hidden) return;
    getJson('/api/wallet/ledger').then(function(j){
      var l = (j && j.ledger) || {};
      if (!l.supported) cxMsg(msg, 'Ledger support is not installed in this VOOL.', true);
      else if (!l.plugged_in) cxMsg(msg, 'No Ledger found yet. Plug it in, unlock it and open its Solana app.');
      else cxMsg(msg, 'Ledger found.');
    }).catch(function(){});
  });
  back.addEventListener('click', function(){ creds.clear(); f.hidden = true; });
  if (!cryptoCreationKeys['ledger:' + r.network]) cryptoCreationKeys['ledger:' + r.network] = 'ledger-' + Math.random().toString(16).slice(2) + Date.now().toString(16);
  go.addEventListener('click', function(){
    if (creds.cred.value !== creds.conf.value) { cxMsg(msg, 'The two entries differ. Type the same PIN or password twice.', true); return; }
    var body = { network: r.network, account: account.value || '0', method: creds.method.value, credential: creds.cred.value, credential_confirmation: creds.conf.value,
                 creation_key: cryptoCreationKeys['ledger:' + r.network], label: label.value || '' };
    creds.clear(); go.disabled = true; cxMsg(msg, 'On your Ledger: a 0 SOL transfer to its own address. Press approve to prove it is yours. Nothing is sent…');
    postJson('/api/wallet/ledger/add', body).then(function(res){
      body = null; go.disabled = false;
      if (!res.ok) { cxMsg(msg, faultMessage(res, 'Not added. Nothing changed.'), true); return; }
      delete cryptoCreationKeys['ledger:' + r.network];
      var w = (res.data && res.data.wallet) || {};
      cxMsg(msg, w.created ? 'Added. This wallet signs on your Ledger (' + short(w.address) + ').' : 'Done. The same Ledger wallet now uses the new PIN or password.');
      renderCrypto();
    }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
  });
  return box;
}
function accountCard(a, r, cp, st){
  var box = el('div', 'vw-cx-account'); box.setAttribute('data-wallet', a.wallet_id); box.setAttribute('data-setup-state', a.setup_state || 'ready'); box.setAttribute('data-mode', a.mode || '');
  var line = el('div');
  line.appendChild(el('span', 'vw-line', (a.label ? a.label + ' · ' : '') + (a.agent_id ? (a.agent_id === 'addons' ? 'the Skills & plugins wallet (see below)' : 'an agent\'s own wallet (see Agent spending below)') : (a.ledger ? 'Ledger wallet' : (a.mode_label || a.mode)))));
  if (a.agent_id) box.setAttribute('data-agent', a.agent_id);
  if (a.kind) {
    box.setAttribute('data-kind', a.kind);
    var badge = el('span', 'vw-badge vw-cx-kind', a.kind === 'main' ? 'Main' : (a.kind === 'burner' ? (a.burner_used ? 'Burner, used' : 'Burner') : 'Everyday'));
    badge.setAttribute('data-kind', a.kind);
    line.appendChild(badge);
  }
  box.appendChild(line);
  if (a.kind) box.appendChild(el('div', 'vw-cx-env vw-cx-kind-note', KIND_NOTES[a.kind === 'burner' && a.burner_used ? 'burner_used' : a.kind] || ''));
  if (a.ledger) {
    box.setAttribute('data-ledger', 'true');
    box.appendChild(el('div', 'vw-cx-env vw-cx-ledger-note', 'Signs on your Ledger. Its key never leaves the Ledger. Every payment asks for this wallet\'s PIN or password, then shows on the Ledger for you to check and press. Its backup is the Ledger\'s own recovery words.'));
  }
  var addr = el('div', 'vw-cx-address', a.address || ''); box.appendChild(addr);
  var setupState = a.setup_state || 'ready';
  var stateLine = el('div', 'vw-cx-env', 'State: ' + (SETUP_TEXT[setupState] || setupState)); stateLine.className += ' vw-cx-state'; box.appendChild(stateLine);
  var balance = el('div', 'vw-cx-balance', 'Balance not read yet.'); balance.setAttribute('data-balance-state', 'unread'); box.appendChild(balance);
  var actions = el('div', 'vw-cx-actions');
  var msg = el('div', 'vw-msg');
  if (setupState === 'ready' || !a.mode || a.mode !== 'pocket_sealed') {
    var receive = el('button', 'vw-btn vw-cx-receive', WT('wallet.home.receive', 'Receive')); receive.type = 'button';
    receive.addEventListener('click', function(){ cxMsg(msg, 'Receive on ' + r.display_name + ' only (' + (r.badge || '') + '): ' + a.address); });
    actions.appendChild(receive);
    actions.appendChild(copyButton(a.address, 'address'));
    var caps = r.capabilities || {};
    var refresh = el('button', 'vw-btn vw-cx-refresh', 'Refresh balance'); refresh.type = 'button';
    refresh.disabled = !(caps.balance && caps.balance.available);
    if (refresh.disabled) refresh.title = 'Balance: ' + (CAP_REASONS[(caps.balance || {}).reason] || (caps.balance || {}).reason || 'unavailable');
    refresh.addEventListener('click', function(){
      refresh.disabled = true; balance.textContent = 'Reading the balance…'; balance.setAttribute('data-balance-state', 'reading');
      getJson('/api/wallet/balance?wallet_id=' + encodeURIComponent(a.wallet_id)).then(function(j){
        refresh.disabled = false;
        var b = (j && j.balance) || {};
        if (b.state === 'read') { balance.textContent = 'Balance: ' + b.balance_human + ' ' + b.asset + ' · observed at ' + b.ref; balance.className = 'vw-cx-balance read'; balance.setAttribute('data-balance-state', 'read'); }
        else { balance.textContent = 'Balance unavailable: ' + (b.reason || 'not read') + ' (not zero).'; balance.className = 'vw-cx-balance'; balance.setAttribute('data-balance-state', 'unavailable'); }
      }).catch(function(){ refresh.disabled = false; balance.textContent = 'Balance unavailable: the request did not complete (not zero).'; balance.setAttribute('data-balance-state', 'unavailable'); });
    });
    actions.appendChild(refresh);
    var history = el('button', 'vw-btn vw-cx-history-btn', 'Payment history'); history.type = 'button';
    var historyBox = el('div', 'vw-cx-history'); historyBox.hidden = true;
    history.addEventListener('click', function(){
      historyBox.hidden = !historyBox.hidden;
      if (historyBox.hidden) return;
      historyBox.textContent = 'Reading…';
      getJson('/api/wallet/transfers?limit=20&wallet_id=' + encodeURIComponent(a.wallet_id)).then(function(j){
        historyBox.textContent = '';
        var rows = (j && j.transfers) || [];
        if (!rows.length) { historyBox.appendChild(el('div', null, 'No transfers from this account yet.')); return; }
        rows.forEach(function(t){
          var item = el('div');
          item.appendChild(document.createTextNode((t.state_label || t.state) + ' · ' + t.amount_human + ' ' + t.display_symbol + ' to ' + short(t.to_address) + (t.charged_fee_display ? ' · fee ' + t.charged_fee_display : '') + ' '));
          if (t.explorer_url) { var link = el('a', 'vw-explorer', t.explorer_link_text || 'View on the explorer'); link.href = t.explorer_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; item.appendChild(link); }
          historyBox.appendChild(item);
        });
      }).catch(function(){ historyBox.textContent = 'The history could not be read.'; });
    });
    actions.appendChild(history);
    if (a.mode === 'pocket_sealed' && !a.agent_id && !a.ledger) { var forgot = forgotLink(a.wallet_id, 'Forgot PIN or password?'); forgot.className += ' vw-cx-forgot'; actions.appendChild(forgot); }
    if (a.ledger) actions.appendChild(el('span', 'vw-cx-env vw-cx-ledger-forgot', 'Forgot the PIN? Add the same Ledger again below: same wallet, new PIN.'));
    box.appendChild(actions); box.appendChild(historyBox);
    if (!a.agent_id && a.kind) box.appendChild(walletRoleControls(a, cp, msg, r));
  } else {
    // a Crypto Pilot setup that has not finished: exactly the actions its durable state allows
    if (a.agent_id) cxMsg(msg, 'Finish this wallet under Agent spending below.'); else setupActions(actions, msg, a, r, cp);
    box.appendChild(actions);
  }
  box.appendChild(msg);
  return box;
}
var KIND_NOTES = {
  main: 'Your main wallet on this network. Payments VOOL starts for you (contacts, top-ups, chat payments) come from here unless you name another wallet.',
  everyday: 'A separate wallet with its own key and backup. VOOL uses it only when you name it.',
  burner: 'A wallet for one-off payments. VOOL never uses it by itself. Empty it and delete it whenever you like.',
  burner_used: 'This burner has made a payment. Send what is left to another wallet and delete it whenever you like.'
};
function keychainControls(a, cp, msg){
  // the backup key sealed with a backup password the person chooses, kept in this computer's secure store
  var box = el('div', 'vw-cx-keychain'); box.setAttribute('data-keychain', a.keychain_backup ? 'saved' : 'none');
  box.appendChild(el('div', 'vw-cx-env', a.keychain_backup
    ? 'Backup saved in this computer\'s Keychain, locked with your backup password. If you forget the PIN or reinstall VOOL, it brings this wallet back.'
    : 'No Keychain backup yet. You can keep this wallet\'s backup key in this computer\'s Keychain, locked with a backup password only you know. Writing the backup key down on paper is still the safest second copy.'));
  var bar = el('div', 'vw-cx-actions'); box.appendChild(bar);
  function form(title, fields, goText, door, extra, okText){
    var f = el('div', 'vw-cx-form'); f.hidden = true;
    f.appendChild(el('div', 'vw-line', title));
    var inputs = {};
    fields.forEach(function(x){
      var id = 'vwKc' + x[0] + '-' + a.wallet_id; var l = el('label', null, x[1]); l.setAttribute('for', id); f.appendChild(l);
      var i = el('input'); i.type = 'password'; i.id = id; i.autocomplete = 'new-password'; f.appendChild(i); inputs[x[0]] = i;
    });
    var creds = extra === 'new' ? credentialPrompt(f, 'vwKcNew-' + a.wallet_id, cp) : null;
    var row = el('div', 'vw-sheet-actions'); var go = el('button', 'vw-btn primary', goText); go.type = 'button'; var back = el('button', 'vw-btn', 'Cancel'); back.type = 'button';
    row.appendChild(go); row.appendChild(back); f.appendChild(row);
    function clear(){ Object.keys(inputs).forEach(function(k){ inputs[k].value = ''; }); if (creds) creds.clear(); }
    back.addEventListener('click', function(){ clear(); f.hidden = true; });
    go.addEventListener('click', function(){
      var body = { wallet_id: a.wallet_id };
      Object.keys(inputs).forEach(function(k){ body[k] = inputs[k].value; });
      if (creds) { body.method = creds.method.value; body.credential = creds.cred.value; body.credential_confirmation = creds.conf.value; }
      clear(); go.disabled = true; cxMsg(msg, 'Working… this takes a moment.');
      postJson(door, body).then(function(res){
        body = null; go.disabled = false;
        if (!res.ok) { cxMsg(msg, faultMessage(res, 'Nothing changed.'), true); return; }
        cxMsg(msg, okText + keyDroppedText(res)); renderCrypto();
      }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
    });
    return f;
  }
  if (!a.keychain_backup) {
    var save = el('button', 'vw-btn vw-cx-keychain-save', WT('wallet.keychain.save', 'Save backup in Keychain')); save.type = 'button'; bar.appendChild(save);
    var sf = form('Choose a backup password (at least 12 characters). It is different from your PIN and is never stored. Without it nobody, not even VOOL, can open the backup.',
      [['credential', 'This wallet\'s PIN or password'], ['backup_password', 'Backup password'], ['backup_password_confirmation', 'Repeat the backup password']],
      'Save the backup', '/api/wallet/keychain/save', null, 'The backup is saved in this computer\'s Keychain.');
    save.addEventListener('click', function(){ sf.hidden = !sf.hidden; });
    box.appendChild(sf);
  } else {
    var rec = el('button', 'vw-btn vw-cx-keychain-recover', 'Forgot PIN? Use the Keychain backup'); rec.type = 'button'; bar.appendChild(rec);
    var rf = form('Type the backup password, then choose a new PIN or password for this wallet.', [['backup_password', 'Backup password']],
      'Restore access', '/api/wallet/keychain/recover', 'new', 'Done. Use the new PIN or password from now on.');
    rec.addEventListener('click', function(){ rf.hidden = !rf.hidden; });
    box.appendChild(rf);
    var forget = el('button', 'vw-btn vw-cx-keychain-forget', 'Remove the Keychain backup'); forget.type = 'button'; bar.appendChild(forget);
    forget.addEventListener('click', function(){
      forget.disabled = true;
      postJson('/api/wallet/keychain/forget', { wallet_id: a.wallet_id }).then(function(res){ forget.disabled = false; if (!res.ok) { cxMsg(msg, faultMessage(res, 'Not removed.'), true); return; } cxMsg(msg, 'The Keychain backup was removed.'); renderCrypto(); })
        .catch(function(){ forget.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
    });
  }
  return box;
}
function keychainRestoreBlock(cp){
  // backups in the secure store whose wallet is not on this computer now (deleted, or a new install)
  var box = el('div', 'vw-cx-row vw-cx-keychain-restore'); box.id = 'vwKeychainRestore'; box.hidden = true;
  getJson('/api/wallet/keychain').then(function(j){
    var k = (j && j.keychain) || {}; var rows = k.restorable || [];
    if (!rows.length) return;
    box.hidden = false;
    box.appendChild(el('div', 'vw-cx-title', 'Wallets you can bring back from the Keychain'));
    rows.forEach(function(r){
      var line = el('div', 'vw-cx-account'); line.setAttribute('data-restore', r.address);
      line.appendChild(el('div', 'vw-line', (r.label || 'Wallet') + ' · ' + chainLabel(r.network)));
      line.appendChild(el('div', 'vw-cx-address', r.address));
      var msg = el('div', 'vw-msg');
      if (!r.can_restore) { line.appendChild(el('div', 'vw-cx-env', 'Restoring this one here is not available yet. Use its backup key.')); box.appendChild(line); return; }
      var f = el('div', 'vw-cx-form');
      var l = el('label', null, 'Backup password'); var pid = 'vwKcRestore-' + r.address; l.setAttribute('for', pid); f.appendChild(l);
      var pw = el('input'); pw.type = 'password'; pw.id = pid; pw.autocomplete = 'off'; f.appendChild(pw);
      var creds = credentialPrompt(f, 'vwKcRestoreNew-' + r.address, cp);
      var go = el('button', 'vw-btn primary', 'Bring it back'); go.type = 'button'; f.appendChild(go);
      go.addEventListener('click', function(){
        var body = { network: r.network, address: r.address, label: r.label, backup_password: pw.value, method: creds.method.value, credential: creds.cred.value, credential_confirmation: creds.conf.value };
        pw.value = ''; creds.clear(); go.disabled = true; cxMsg(msg, 'Working… this takes a moment.');
        postJson('/api/wallet/keychain/restore', body).then(function(res){ body = null; go.disabled = false; if (!res.ok) { cxMsg(msg, faultMessage(res, 'Nothing changed.'), true); return; } cxMsg(msg, 'The wallet is back.'); renderCrypto(); })
          .catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
      });
      line.appendChild(f); line.appendChild(msg); box.appendChild(line);
    });
  }).catch(function(){});
  return box;
}
function usbDateText(iso){ var d = new Date(iso || ''); return isNaN(d.getTime()) ? '' : d.toLocaleDateString(); }
function usbControls(a, cp, msg){
  // the same sealed backup as the Keychain one, written to a USB stick the person keeps offline
  var box = el('div', 'vw-cx-usb'); box.setAttribute('data-usb', a.usb_backup ? 'saved' : 'none');
  var u = a.usb_backup || null;
  box.appendChild(el('div', 'vw-cx-env', u
    ? 'Backup saved on "' + u.drive + '"' + (usbDateText(u.saved_at) ? ' on ' + usbDateText(u.saved_at) : '') + ', locked with your backup password. Keep that drive away from this computer.'
    : 'No USB backup yet. You can keep this wallet\'s backup on a USB stick or external drive in a drawer, locked with a backup password only you know.'));
  var bar = el('div', 'vw-cx-actions'); box.appendChild(bar);
  var open = el('button', 'vw-btn vw-cx-usb-save', u ? WT('wallet.usb.save_again', 'Save to a USB stick or drive again') : WT('wallet.usb.save', 'Save backup to a USB stick or drive')); open.type = 'button'; bar.appendChild(open);
  var f = el('div', 'vw-cx-form vw-cx-usb-form'); f.hidden = true; box.appendChild(f);
  f.appendChild(el('div', 'vw-line', 'Plug in a USB stick or external drive. Choose a backup password (at least 12 characters). It is different from your PIN and is never stored. Without it nobody, not even VOOL, can open the backup.'));
  var dl = el('label', null, 'USB stick or external drive'); var did = 'vwUsbDrive-' + a.wallet_id; dl.setAttribute('for', did); f.appendChild(dl);
  var drive = el('select', 'vw-chain vw-cx-usb-drive'); drive.id = did; f.appendChild(drive);
  var none = el('div', 'vw-cx-env vw-cx-usb-none', 'No USB stick or external drive found. Plug one in, then press Look again.'); none.hidden = true; f.appendChild(none);
  var again = el('button', 'vw-btn vw-cx-usb-rescan', 'Look again'); again.type = 'button'; f.appendChild(again);
  var inputs = {};
  [['credential', 'This wallet\'s PIN or password'], ['backup_password', 'Backup password'], ['backup_password_confirmation', 'Repeat the backup password']].forEach(function(x){
    var id = 'vwUsb' + x[0] + '-' + a.wallet_id; var l = el('label', null, x[1]); l.setAttribute('for', id); f.appendChild(l);
    var i = el('input'); i.type = 'password'; i.id = id; i.autocomplete = 'new-password'; f.appendChild(i); inputs[x[0]] = i;
  });
  var row = el('div', 'vw-sheet-actions'); var go = el('button', 'vw-btn primary vw-cx-usb-go', 'Save to this drive'); go.type = 'button';
  var back = el('button', 'vw-btn', 'Cancel'); back.type = 'button'; row.appendChild(go); row.appendChild(back); f.appendChild(row);
  function clear(){ Object.keys(inputs).forEach(function(k){ inputs[k].value = ''; }); }
  function scan(){
    while (drive.firstChild) drive.removeChild(drive.firstChild); go.disabled = true;
    getJson('/api/wallet/usb').then(function(j){
      var rows = ((j && j.usb) || {}).drives || [];
      rows.forEach(function(d){ var o = el('option'); o.value = d.drive_id; o.textContent = d.name + (d.kind === 'external_drive' ? ' (external drive)' : ' (USB stick)'); drive.appendChild(o); });
      none.hidden = rows.length > 0; drive.hidden = rows.length === 0; go.disabled = rows.length === 0;
    }).catch(function(){ none.hidden = false; drive.hidden = true; });
  }
  open.addEventListener('click', function(){ f.hidden = !f.hidden; if (!f.hidden) scan(); });
  again.addEventListener('click', scan);
  back.addEventListener('click', function(){ clear(); f.hidden = true; });
  go.addEventListener('click', function(){
    var body = { wallet_id: a.wallet_id, drive_id: drive.value };
    Object.keys(inputs).forEach(function(k){ body[k] = inputs[k].value; });
    clear(); go.disabled = true; cxMsg(msg, 'Working… this takes a moment.');
    postJson('/api/wallet/usb/save', body).then(function(res){
      body = null; go.disabled = false;
      if (!res.ok) { cxMsg(msg, faultMessage(res, 'Nothing was saved.'), true); return; }
      cxMsg(msg, 'Saved. Keep that drive somewhere safe, away from this computer.'); renderCrypto();
    }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
  });
  return box;
}
var USB_STATES = {
  on_this_computer: 'This wallet is on this computer. Forgot its PIN? Open the backup and choose a new one.',
  can_bring_back: 'This wallet is not on this computer. Open the backup to bring it back. Only bring back your own backups: whoever made a backup still knows its key.',
  use_backup_key: 'Bringing this one back here is not available yet. Use its backup key.'
};
function usbRestoreBlock(cp){
  // VOOL backups on the USB sticks plugged in now: forgot PIN, or bring a wallet back (new computer, deleted wallet)
  var box = el('div', 'vw-cx-row vw-cx-usb-restore'); box.id = 'vwUsbRestore';
  box.appendChild(el('div', 'vw-cx-title', 'Backups on a USB stick or external drive'));
  var lead = el('div', 'vw-cx-env', 'Plug in the USB stick or external drive with your VOOL backup, then press Look for backups.'); box.appendChild(lead);
  var look = el('button', 'vw-btn vw-cx-usb-look', 'Look for backups'); look.type = 'button'; box.appendChild(look);
  var list = el('div', 'vw-cx-usb-list'); box.appendChild(list);
  function render(rows){
    while (list.firstChild) list.removeChild(list.firstChild);
    if (!rows.length) { list.appendChild(el('div', 'vw-cx-env vw-cx-usb-empty', 'No VOOL backup found on a USB stick or external drive.')); return; }
    rows.forEach(function(r){
      var line = el('div', 'vw-cx-account'); line.setAttribute('data-usb-file', r.address); line.setAttribute('data-usb-state', r.state);
      line.appendChild(el('div', 'vw-line', (r.label || 'Wallet') + ' · ' + chainLabel(r.network) + ' · on ' + (r.drive_kind === 'external_drive' ? 'the external drive' : 'the USB stick') + ' "' + r.drive + '"' + (usbDateText(r.saved_at) ? ' · saved ' + usbDateText(r.saved_at) : '')));
      line.appendChild(el('div', 'vw-cx-address', r.address));
      line.appendChild(el('div', 'vw-cx-env', USB_STATES[r.state] || ''));
      var msg = el('div', 'vw-msg');
      if (r.state === 'use_backup_key') { line.appendChild(msg); list.appendChild(line); return; }
      var f = el('div', 'vw-cx-form');
      var l = el('label', null, 'Backup password'); var pid = 'vwUsbOpen-' + r.address; l.setAttribute('for', pid); f.appendChild(l);
      var pw = el('input'); pw.type = 'password'; pw.id = pid; pw.autocomplete = 'off'; f.appendChild(pw);
      var creds = credentialPrompt(f, 'vwUsbOpenNew-' + r.address, cp);
      var go = el('button', 'vw-btn primary vw-cx-usb-open', r.state === 'on_this_computer' ? 'Set the new PIN' : 'Bring it back'); go.type = 'button'; f.appendChild(go);
      go.addEventListener('click', function(){
        var body = { file_id: r.file_id, label: r.label, backup_password: pw.value, method: creds.method.value, credential: creds.cred.value, credential_confirmation: creds.conf.value };
        pw.value = ''; creds.clear(); go.disabled = true; cxMsg(msg, 'Working… this takes a moment.');
        postJson('/api/wallet/usb/open', body).then(function(res){
          body = null; go.disabled = false;
          if (!res.ok) { cxMsg(msg, faultMessage(res, 'Nothing changed.'), true); return; }
          cxMsg(msg, (r.state === 'on_this_computer' ? 'Done. Use the new PIN or password from now on.' : 'The wallet is back.') + keyDroppedText(res)); renderCrypto();
        }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
      });
      line.appendChild(f); line.appendChild(msg); list.appendChild(line);
    });
  }
  look.addEventListener('click', function(){
    look.disabled = true; while (list.firstChild) list.removeChild(list.firstChild);
    getJson('/api/wallet/usb').then(function(j){ look.disabled = false; render(((j && j.usb) || {}).backups || []); })
      .catch(function(){ look.disabled = false; list.appendChild(el('div', 'vw-cx-env', 'The USB sticks and drives could not be read.')); });
  });
  return box;
}
function keyDroppedText(res){
  // a backup recovery re-seals without the security key: the answer says so, and so does the page
  var d = (res && res.data) || {}, found = false;
  Object.keys(d).forEach(function(k){ if (d[k] && typeof d[k] === 'object' && d[k].security_key_removed === true) found = true; });
  return found ? ' This wallet no longer needs its security key. Add it again in Settings > Crypto.' : '';
}
function securityKeyControls(a, cp, msg){
  // a security key (YubiKey or similar) the wallet also needs: PIN AND key, every time it opens
  var box = el('div', 'vw-cx-seckey'); box.setAttribute('data-security-key', a.security_key ? 'added' : 'none');
  var k = a.security_key || null;
  var device = a.approval_method === 'device';
  box.appendChild(el('div', 'vw-cx-env', k
    ? 'Security key: ' + (k.name || 'Security key') + (usbDateText(k.added_at) ? ', added ' + usbDateText(k.added_at) : '') + '. This wallet opens only with your ' + (device ? 'Touch ID' : 'PIN') + ' and this key plugged in and touched. Lost the key? Your backup key brings the wallet back and removes the lost key.'
    : 'Add a security key (a YubiKey or similar). After that, this wallet opens only with your ' + (device ? 'Touch ID' : 'PIN') + ' and the key plugged in and touched. If you lose the key, your backup key still brings the wallet back. Copies or backups of VOOL\'s files made before you add the key still open with the ' + (device ? 'Touch ID copy' : 'PIN') + ' alone.'));
  var bar = el('div', 'vw-cx-actions'); box.appendChild(bar);
  var open = el('button', 'vw-btn ' + (k ? 'vw-cx-seckey-remove' : 'vw-cx-seckey-add'), k ? 'Remove the security key' : 'Add a security key'); open.type = 'button'; bar.appendChild(open);
  var f = el('div', 'vw-cx-form vw-cx-seckey-form'); f.hidden = true; box.appendChild(f);
  f.appendChild(el('div', 'vw-line', k
    ? 'Plug in this wallet\'s security key.' + (device ? '' : ' Type this wallet\'s PIN or password.') + ' Touch the key when it blinks. After this, the PIN alone opens the wallet again.'
    : 'Plug in ONE security key.' + (device ? '' : ' Type this wallet\'s PIN or password.') + ' Touch the key each time it blinks (twice).'));
  var cred = null, keyPin = null;
  if (!device) {
    var cl = el('label', null, 'This wallet\'s PIN or password'); var cid = 'vwSk-' + a.wallet_id; cl.setAttribute('for', cid); f.appendChild(cl);
    cred = el('input'); cred.type = 'password'; cred.id = cid; cred.autocomplete = 'off'; f.appendChild(cred);
  }
  if (!k) {
    var kl = el('label', null, 'The security key\'s own PIN (only if the key asks for one)'); var kid = 'vwSkPin-' + a.wallet_id; kl.setAttribute('for', kid); f.appendChild(kl);
    keyPin = el('input'); keyPin.type = 'password'; keyPin.id = kid; keyPin.autocomplete = 'off'; f.appendChild(keyPin);
  }
  var row = el('div', 'vw-sheet-actions'); var go = el('button', 'vw-btn primary vw-cx-seckey-go', k ? 'Remove the key' : 'Add the key'); go.type = 'button';
  var back = el('button', 'vw-btn', 'Cancel'); back.type = 'button'; row.appendChild(go); row.appendChild(back); f.appendChild(row);
  function clear(){ if (cred) cred.value = ''; if (keyPin) keyPin.value = ''; }
  open.addEventListener('click', function(){ f.hidden = !f.hidden; });
  back.addEventListener('click', function(){ clear(); f.hidden = true; });
  go.addEventListener('click', function(){
    var body = { wallet_id: a.wallet_id, credential: cred ? cred.value : '' };
    if (keyPin) body.key_pin = keyPin.value;
    clear(); go.disabled = true; cxMsg(msg, k ? 'Touch your security key when it blinks…' : 'Touch your security key when it blinks… (twice)');
    postJson(k ? '/api/wallet/security-key/remove' : '/api/wallet/security-key/add', body).then(function(res){
      body = null; go.disabled = false;
      if (!res.ok) { cxMsg(msg, faultMessage(res, 'Nothing changed.'), true); return; }
      cxMsg(msg, k ? 'The security key was removed. The PIN alone opens this wallet again.' : 'Done. This wallet now needs the security key as well. Keep the backup key safe: it is your way back if the key is lost.'); renderCrypto();
    }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
  });
  return box;
}
function walletRoleControls(a, cp, msg, r){
  // rename (no PIN: it loosens nothing) and Make main (this wallet's own PIN or password)
  var host = el('div', 'vw-cx-roles'); host.setAttribute('data-wallet', a.wallet_id);
  var bar = el('div', 'vw-cx-actions');
  var renameBtn = el('button', 'vw-btn vw-cx-rename', 'Rename'); renameBtn.type = 'button';
  bar.appendChild(renameBtn);
  var form = el('div', 'vw-cx-form vw-cx-rename-form'); form.hidden = true;
  var nLabel = el('label', null, 'Wallet name'); var nId = 'vwCxName-' + a.wallet_id; nLabel.setAttribute('for', nId); form.appendChild(nLabel);
  var name = el('input', 'vw-cx-name'); name.type = 'text'; name.id = nId; name.maxLength = 40; name.autocomplete = 'off'; name.value = a.label || ''; form.appendChild(name);
  var saveRow = el('div', 'vw-sheet-actions');
  var save = el('button', 'vw-btn primary vw-cx-rename-save', 'Save name'); save.type = 'button';
  var back = el('button', 'vw-btn', 'Cancel'); back.type = 'button';
  saveRow.appendChild(save); saveRow.appendChild(back); form.appendChild(saveRow);
  renameBtn.addEventListener('click', function(){ form.hidden = !form.hidden; if (!form.hidden) name.focus(); });
  back.addEventListener('click', function(){ name.value = a.label || ''; form.hidden = true; });
  save.addEventListener('click', function(){
    save.disabled = true; cxMsg(msg, 'Saving…');
    postJson('/api/wallet/wallets/name', { wallet_id: a.wallet_id, name: name.value }).then(function(res){
      save.disabled = false;
      if (!res.ok) { cxMsg(msg, faultMessage(res, 'Not renamed. Nothing changed.'), true); return; }
      cxMsg(msg, 'Renamed.'); renderCrypto();
    }).catch(function(){ save.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
  });
  // Delete: the person's own wallet, at any time; this wallet's own PIN and a confirmed backup (or an empty wallet)
  var delBtn = el('button', 'vw-btn vw-cx-remove', 'Delete wallet'); delBtn.type = 'button';
  var dForm = el('div', 'vw-cx-form vw-cx-remove-form'); dForm.hidden = true;
  dForm.appendChild(el('div', 'vw-cx-warn', a.ledger
    ? 'Deleting removes this wallet from VOOL. Its key stays on your Ledger, and you can add the same Ledger again at any time.'
    : 'Deleting removes this wallet and its key from this computer. Coins still in it stay at its address, and only its backup key can reach them again. Move them to another wallet first if you want to keep them.'));
  var okWrap = el('label', 'vw-line'); var ok = el('input'); ok.type = 'checkbox'; ok.className = 'vw-cx-remove-ok';
  okWrap.appendChild(ok); okWrap.appendChild(document.createTextNode(a.ledger ? ' I have my Ledger and its recovery words' : ' I have this wallet\'s backup key, or it is empty')); dForm.appendChild(okWrap);
  var dCreds = a.mode === 'pocket_sealed' ? credentialPrompt(dForm, 'vwCxDel-' + a.wallet_id, cp, { single: true }) : null;
  var dRow = el('div', 'vw-sheet-actions');
  var dGo = el('button', 'vw-btn primary vw-cx-remove-go', 'Delete this wallet'); dGo.type = 'button';
  var dBack = el('button', 'vw-btn', 'Cancel'); dBack.type = 'button';
  dRow.appendChild(dGo); dRow.appendChild(dBack); dForm.appendChild(dRow);
  delBtn.addEventListener('click', function(){ dForm.hidden = !dForm.hidden; });
  dBack.addEventListener('click', function(){ if (dCreds) dCreds.clear(); ok.checked = false; dForm.hidden = true; });
  dGo.addEventListener('click', function(){
    dGo.disabled = true; cxMsg(msg, 'Checking…');
    var body = { wallet_id: a.wallet_id, credential: dCreds ? dCreds.cred.value : '', backup_confirmed: !!ok.checked }; if (dCreds) dCreds.clear();
    postJson('/api/wallet/wallets/remove', body).then(function(res){
      body = null; dGo.disabled = false;
      if (!res.ok) { cxMsg(msg, faultMessage(res, 'Not deleted. Nothing changed.'), true); return; }
      cxMsg(msg, 'The wallet was deleted from this computer.'); renderCrypto();
    }).catch(function(){ body = null; dGo.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
  });
  bar.appendChild(delBtn);
  if (a.mode === 'pocket_sealed' && !a.ledger) { host.appendChild(keychainControls(a, cp, msg)); host.appendChild(usbControls(a, cp, msg)); if ((a.setup_state || 'ready') === 'ready' && ((r && r.hardware_keys) || a.security_key)) host.appendChild(securityKeyControls(a, cp, msg)); }
  if (a.kind === 'everyday' && a.mode === 'pocket_sealed' && (a.setup_state || 'ready') === 'ready') {
    var mainBtn = el('button', 'vw-btn vw-cx-make-main', 'Make main'); mainBtn.type = 'button';
    bar.appendChild(mainBtn);
    var mForm = el('div', 'vw-cx-form vw-cx-main-form'); mForm.hidden = true;
    mForm.appendChild(el('div', 'vw-line', 'Payments VOOL starts for you on this network will come from this wallet. Type this wallet\'s own PIN or password.'));
    var creds = credentialPrompt(mForm, 'vwCxMain-' + a.wallet_id, cp, { single: true });
    var mRow = el('div', 'vw-sheet-actions');
    var go = el('button', 'vw-btn primary vw-cx-make-main-go', 'Make this my main wallet'); go.type = 'button';
    var mBack = el('button', 'vw-btn', 'Cancel'); mBack.type = 'button';
    mRow.appendChild(go); mRow.appendChild(mBack); mForm.appendChild(mRow);
    mainBtn.addEventListener('click', function(){ mForm.hidden = !mForm.hidden; if (!mForm.hidden) creds.cred.focus(); });
    mBack.addEventListener('click', function(){ creds.clear(); mForm.hidden = true; });
    go.addEventListener('click', function(){
      go.disabled = true; cxMsg(msg, 'Checking…');
      var body = { wallet_id: a.wallet_id, credential: creds.cred.value }; creds.clear();
      postJson('/api/wallet/wallets/main', body).then(function(res){
        body = null; go.disabled = false;
        if (!res.ok) { cxMsg(msg, faultMessage(res, 'Refused. Nothing changed.'), true); return; }
        cxMsg(msg, 'This is now your main wallet on this network.'); renderCrypto();
      }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
    });
    host.appendChild(bar); host.appendChild(form); host.appendChild(mForm); host.appendChild(dForm);
    return host;
  }
  host.appendChild(bar); host.appendChild(form); host.appendChild(dForm);
  return host;
}
function credentialPrompt(host, prefix, cp, opts){
  // the credential fields for one action; cleared by the caller after every request
  var policy = (cp && cp.credential_policy) || {};
  // the PIN and password rules come as numbers, so the option reads in the page's language (the server's English
  // rule text stays the fallback when a policy carries no numbers)
  var pinPol = policy.digits_rule || policy.pin, pwPol = policy.password_rule || policy.password;
  var pinRule = pinPol && pinPol.min != null ? WTF('wallet.cred.pin_option', 'PIN ({min}–{max} digits)', { min: pinPol.min, max: pinPol.max }) : 'PIN (' + ((pinPol && pinPol.text) || 'digits') + ')';
  var pwRule = pwPol && pwPol.min != null ? WTF('wallet.cred.password_option', 'Wallet password ({min}–{max} characters, not only digits)', { min: pwPol.min, max: pwPol.max }) : 'Wallet password (' + ((pwPol && pwPol.text) || '') + ')';
  var methods = [['pin', pinRule], ['password', pwRule]];
  if (policy.device && policy.device.available) methods.push(['device', WT('wallet.cred.device_option', 'Touch ID or your Mac password')]);
  var mLabel = el('label', null, WT('wallet.home.approve_method', 'How you approve payments')); mLabel.setAttribute('for', prefix + 'Method'); host.appendChild(mLabel);
  var method = el('select', 'vw-chain'); method.id = prefix + 'Method';
  methods.forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
  if (!(policy.device && policy.device.available)) { var devNote = el('div', 'vw-cx-env', WT('wallet.cred.device_unavailable', 'Touch ID / Mac password approval: not available on this machine.')); devNote.id = prefix + 'DeviceNote'; host.appendChild(method); host.appendChild(devNote); } else host.appendChild(method);
  var cLabel = el('label', null, WT('wallet.pin_short', 'PIN')); cLabel.setAttribute('for', prefix + 'Credential'); host.appendChild(cLabel);
  var cred = el('input'); cred.type = 'password'; cred.id = prefix + 'Credential'; cred.autocomplete = 'new-password'; cred.inputMode = 'numeric'; host.appendChild(cred);
  var fLabel = el('label', null, WT('wallet.cred.repeat_pin', 'Repeat the PIN')); fLabel.setAttribute('for', prefix + 'Confirm'); host.appendChild(fLabel);
  var conf = el('input'); conf.type = 'password'; conf.id = prefix + 'Confirm'; conf.autocomplete = 'new-password'; conf.inputMode = 'numeric'; host.appendChild(conf);
  function sync(){ var m = method.value; cLabel.hidden = cred.hidden = fLabel.hidden = conf.hidden = (m === 'device'); cLabel.textContent = m === 'pin' ? WT('wallet.pin_short', 'PIN') : WT('wallet.cred.password', 'Wallet password'); fLabel.textContent = m === 'pin' ? WT('wallet.cred.repeat_pin', 'Repeat the PIN') : WT('wallet.cred.repeat_password', 'Repeat the password'); cred.inputMode = conf.inputMode = (m === 'pin' ? 'numeric' : 'text'); }
  method.addEventListener('change', sync); sync();
  if (opts && opts.single) { fLabel.hidden = true; conf.hidden = true; }
  return { method: method, cred: cred, conf: conf, clear: function(){ cred.value = ''; conf.value = ''; } };
}
function showBackup(wallet, backup, onDone){
  // ONE detachable node: the issued chain-specific backup, shown once. No storage, no history, no console, no request.
  var overlay = el('div', 'vw-sheet-overlay'); overlay.id = 'vwCxBackup';
  var box = el('div', 'vw-sheet'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-labelledby', 'vwCxBackupTitle');
  box.appendChild(el('h3', null, WT('wallet.backup.title', 'Your backup — shown once'))).id = 'vwCxBackupTitle';
  box.appendChild(el('div', 'vw-cx-warn', (backup.warning || WT('wallet.backup.default_warning', 'Anyone who has this backup owns the wallet.')) + ' ' + WT('wallet.backup.save_offline', 'Save it offline now. VOOL cannot show it again.')));
  box.appendChild(el('div', 'vw-line', WTF('wallet.backup.wallet_line', 'Wallet: {name} · address {address}', { name: wallet.display_name || wallet.network, address: backup.address || wallet.address })));
  box.appendChild(el('div', 'vw-cx-next', WT('wallet.backup.step', 'Step 2 of 2: write this backup key on paper, or save it in your password manager. Then press I have saved my backup.')));
  if (backup.backup_target) box.appendChild(el('div', 'vw-line', WTF('wallet.backup.opens_in', 'It also opens in: {target}', { target: backup.backup_target })));
  var value = el('div'); value.id = 'vwCxBackupValue'; value.textContent = backup.backup_value || ''; box.appendChild(value);
  var actions = el('div', 'vw-sheet-actions');
  var copy = el('button', 'vw-btn', WT('wallet.backup.copy', 'Copy backup')); copy.id = 'vwCxBackupCopy'; copy.type = 'button';
  copy.addEventListener('click', function(){
    try { navigator.clipboard.writeText(value.textContent).then(function(){ copy.textContent = WT('wallet.backup.copied', 'Copied — clear your clipboard after saving it'); }, function(){ copy.textContent = WT('wallet.backup.clipboard_unavailable', 'Clipboard unavailable — select the text and copy it by hand'); }); }
    catch (e) { copy.textContent = WT('wallet.backup.clipboard_unavailable', 'Clipboard unavailable — select the text and copy it by hand'); }
  });
  var saved = el('button', 'vw-btn primary', WT('wallet.backup.saved', 'I have saved my backup')); saved.id = 'vwCxBackupSaved'; saved.type = 'button';
  var later = el('button', 'vw-btn', WT('wallet.backup.close', 'Close without confirming')); later.id = 'vwCxBackupClose'; later.type = 'button';
  later.title = WT('wallet.backup.close_hint', 'The backup stays shown once: it will not be shown again. Confirm later with your PIN under the account.');
  var state = el('div', 'vw-msg'); state.id = 'vwCxBackupState';
  actions.appendChild(copy); actions.appendChild(saved); actions.appendChild(later); box.appendChild(actions); box.appendChild(state);
  overlay.appendChild(box); document.body.appendChild(overlay);
  function close(){ value.textContent = ''; backup = null; document.removeEventListener('keydown', onKey, true); overlay.remove(); }
  function onKey(ev){ if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); close(); if (onDone) onDone(false); } }
  document.addEventListener('keydown', onKey, true);
  later.addEventListener('click', function(){ close(); if (onDone) onDone(false); });
  saved.addEventListener('click', function(){
    saved.disabled = true; cxMsg(state, WT('wallet.backup.confirming', 'Confirming…'));
    postJson('/api/wallet/setup/acknowledge', { wallet_id: wallet.wallet_id, ack_token: backup && backup.ack_token }).then(function(res){
      if (!res.ok) { saved.disabled = false; cxMsg(state, faultMessage(res, WT('wallet.backup.not_confirmed', 'Not confirmed.')), true); return; }
      close(); if (onDone) onDone(true);
    }).catch(function(){ saved.disabled = false; cxMsg(state, WT('wallet.backup.request_failed', 'The confirmation request did not complete.'), true); });
  });
  saved.focus();
}
function openCreateFlow(card, r, cp, msg, createBtn, opts){
  var more = !!(opts && opts.more);
  if (cryptoOpenForm) { cryptoOpenForm.remove(); cryptoOpenForm = null; }
  createBtn.hidden = true;
  var form = el('div', 'vw-cx-form'); form.id = 'vwCxCreateForm'; form.setAttribute('data-network', r.network);
  cryptoOpenForm = form;
  // step 1: the warning, then a choice; closing here changes nothing
  var warn = el('div', 'vw-cx-warn'); warn.id = 'vwCxWarning';
  warn.textContent = (more ? WT('wallet.create.more', 'Another wallet, fully separate: its own key, its own backup and its own PIN or password. Your main wallet stays your main wallet.') + ' ' : '') + WTF('wallet.create.before', 'Pilot feature: keep small amounts in it. First you choose a PIN. Then your backup key is shown once: have paper and a pen, or your password manager, ready. This wallet is on {network} ({badge}) and sends its own coin.', { network: r.display_name, badge: r.badge || '' });
  form.appendChild(warn);
  var step1 = el('div', 'vw-sheet-actions');
  var cont = el('button', 'vw-btn primary', WT('wallet.create.continue', 'Continue')); cont.id = 'vwCxContinue'; cont.type = 'button';
  var notNow = el('button', 'vw-btn', 'Not now'); notNow.id = 'vwCxNotNow'; notNow.type = 'button';
  step1.appendChild(cont); step1.appendChild(notNow); form.appendChild(step1);
  var step2 = el('div'); step2.hidden = true; form.appendChild(step2);
  function abandon(){ form.remove(); cryptoOpenForm = null; createBtn.hidden = false; cxMsg(msg, ''); }
  notNow.addEventListener('click', abandon);
  cont.addEventListener('click', function(){
    step1.hidden = true; step2.hidden = false;
    step2.appendChild(el('div', 'vw-cx-next', WT('wallet.create.step_pin', 'Step 1 of 2: choose a PIN and type it twice. You type it each time you approve a payment. Then press Create wallet.')));
    var creds = credentialPrompt(step2, 'vwCx', cp);
    var lLabel = el('label', null, more ? 'Wallet name' : 'Wallet name (optional)'); lLabel.setAttribute('for', 'vwCxLabel'); step2.appendChild(lLabel);
    var label = el('input'); label.type = 'text'; label.id = 'vwCxLabel'; label.autocomplete = 'off'; label.maxLength = 40; step2.appendChild(label);
    var kind = null;
    if (more) {
      var kLabel = el('label', null, 'What is it for?'); kLabel.setAttribute('for', 'vwCxKind'); step2.appendChild(kLabel);
      kind = el('select', 'vw-chain'); kind.id = 'vwCxKind';
      [['everyday', 'Everyday: used only when you name it'], ['burner', 'Burner: for one-off payments, delete it when done']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; kind.appendChild(opt); });
      step2.appendChild(kind);
    }
    var row = el('div', 'vw-sheet-actions');
    var create = el('button', 'vw-btn primary', WT('wallet.home.create_go', 'Create wallet')); create.id = 'vwCxCreate'; create.type = 'button';
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.id = 'vwCxCancel'; cancel.type = 'button';
    row.appendChild(create); row.appendChild(cancel); step2.appendChild(row);
    var state = el('div', 'vw-msg'); state.id = 'vwCxState'; step2.appendChild(state);
    cancel.addEventListener('click', function(){ creds.clear(); abandon(); });
    if (!cryptoCreationKeys[r.network]) cryptoCreationKeys[r.network] = 'settings-' + Math.random().toString(16).slice(2) + Date.now().toString(16);
    create.addEventListener('click', function(){
      var method = creds.method.value;
      if (method !== 'device' && creds.cred.value !== creds.conf.value) { cxMsg(state, 'The two entries differ. Type the same PIN or password twice.', true); return; }
      create.disabled = true; cxMsg(state, 'Creating the wallet…');
      var credential = creds.cred.value;
      var body = { network: r.network, method: method, credential: credential, credential_confirmation: creds.conf.value, creation_key: cryptoCreationKeys[r.network], label: label.value || '', kind: kind ? kind.value : '' };
      postJson('/api/wallet/setup/create', body).then(function(res){
        body = null;
        if (!res.ok) { create.disabled = false; creds.clear(); cxMsg(state, faultMessage(res, 'Not created. Nothing changed.'), true); return null; }
        var setup = res.data.setup || {};
        cxMsg(state, 'Wallet created (' + short(setup.address) + '). Preparing the one-time backup…');
        return postJson('/api/wallet/setup/reveal', { wallet_id: setup.wallet_id, credential: credential }).then(function(rev){
          creds.clear(); credential = null; delete cryptoCreationKeys[r.network];
          if (!rev.ok) {
            cxMsg(state, 'The wallet exists but its backup could not be shown now: ' + faultMessage(rev, 'reveal refused') + '. Use the actions under the account.', true);
            form.remove(); cryptoOpenForm = null; renderCrypto(); return null;
          }
          form.remove(); cryptoOpenForm = null;
          showBackup(setup, rev.data.backup || {}, function(){ renderCrypto(); });
          return null;
        });
      }).catch(function(){ create.disabled = false; creds.clear(); credential = null; cxMsg(state, 'The request did not complete. If a wallet was created, it is listed under this network after a refresh; the same Create retries safely.', true); });
    });
    creds.cred.focus();
  });
  card.appendChild(form);
  cont.focus();
}
function setupActions(actions, msg, a, r, cp){
  var state = a.setup_state || '';
  function withCredential(labelText, id, door, extra, onOk){
    var form = el('div', 'vw-cx-form'); form.hidden = true;
    var creds = credentialPrompt(form, id, cp, { single: true });
    var go = el('button', 'vw-btn primary', labelText); go.id = id + 'Go'; go.type = 'button';
    var back = el('button', 'vw-btn', 'Back'); back.type = 'button';
    var row = el('div', 'vw-sheet-actions'); row.appendChild(go); row.appendChild(back); form.appendChild(row);
    var open = el('button', 'vw-btn', labelText + ' (needs your PIN or password)'); open.id = id; open.type = 'button';
    open.addEventListener('click', function(){ form.hidden = !form.hidden; if (!form.hidden) creds.cred.focus(); });
    back.addEventListener('click', function(){ creds.clear(); form.hidden = true; });
    go.addEventListener('click', function(){
      go.disabled = true; cxMsg(msg, 'Checking…');
      var body = Object.assign({ wallet_id: a.wallet_id, credential: creds.cred.value }, extra || {});
      creds.clear();
      postJson(door, body).then(function(res){
        body = null; go.disabled = false;
        if (!res.ok) { cxMsg(msg, faultMessage(res, 'Refused. Nothing changed.'), true); return; }
        cxMsg(msg, ''); onOk(res);
      }).catch(function(){ body = null; go.disabled = false; cxMsg(msg, 'The request did not complete.', true); });
    });
    actions.appendChild(open); actions.appendChild(form);
  }
  if (state === 'awaiting_backup') {
    withCredential('Show my backup', 'vwCxReveal', '/api/wallet/setup/reveal', {}, function(res){ showBackup(a, res.data.backup || {}, function(){ renderCrypto(); }); });
    withCredential('Cancel setup', 'vwCxCancelSetup', '/api/wallet/setup/cancel', {}, function(){ renderCrypto(); });
  } else if (state === 'backup_revealed') {
    actions.appendChild(el('div', 'vw-cx-warn', 'The backup key was released once and cannot be shown again. If you saved it, confirm below. If you did not see it, cancel setup and do not send funds to this address.'));
    withCredential('I saved my backup', 'vwCxAck', '/api/wallet/setup/acknowledge', {}, function(){ renderCrypto(); });
    withCredential('Cancel setup', 'vwCxCancelSetup', '/api/wallet/setup/cancel', {}, function(){ renderCrypto(); });
  } else if (state === 'cancelled') {
    withCredential('Resume setup', 'vwCxResume', '/api/wallet/setup/resume', {}, function(){ renderCrypto(); });
    actions.appendChild(el('div', 'vw-cx-env', 'A backup that was already shown is not shown again on resume.'));
  } else if (state === 'generating') {
    actions.appendChild(el('div', 'vw-cx-env', 'Setup is still generating; refresh in a moment.'));
  }
  if (a.mode === 'pocket_sealed') { var forgot = forgotLink(a.wallet_id, 'Forgot PIN or password?'); forgot.className += ' vw-cx-forgot'; actions.appendChild(forgot); }
}
function devOptions(cp, inactiveEmpty){
  var details = el('details'); details.id = 'vwCxDev';
  details.appendChild(el('summary', null, 'Developer options: network environment'));
  var box = el('div', 'vw-cx-dev');
  box.appendChild(el('div', 'vw-cx-env', cp.environment_note || ''));
  var stateLine = el('div', 'vw-line'); stateLine.id = 'vwCxEnvState';
  stateLine.textContent = 'Active: ' + (cp.environment_label || '') + ' · ' + (cp.environment_source_label || cp.environment_source || '');
  box.appendChild(stateLine);
  var msg = el('div', 'vw-msg'); msg.id = 'vwCxEnvMsg';
  (cp.environment_choices || []).forEach(function(choice){
    var wrap = el('label', 'vw-ack');
    var radio = el('input'); radio.type = 'radio'; radio.name = 'vwCxEnv'; radio.value = choice.value; radio.checked = (choice.value === cp.active_environment);
    radio.setAttribute('aria-label', choice.label);
    radio.addEventListener('change', function(){
      if (!radio.checked) return;
      cxMsg(msg, 'Switching to ' + choice.label + '…');
      postOwnerProved('/api/wallet/environment', { environment: choice.value }, msg).then(function(res){
        if (!res.ok) { cxMsg(msg, faultMessage(res, 'Not changed.'), true); renderCrypto(); return; }
        renderCrypto();
      }).catch(function(){ cxMsg(msg, 'The request did not complete.', true); renderCrypto(); });
    });
    wrap.appendChild(radio); wrap.appendChild(el('span', null, choice.label + (choice.note ? ' — ' + choice.note : '')));
    box.appendChild(wrap);
  });
  box.appendChild(msg);
  if (inactiveEmpty.length) {
    var list = el('div', 'vw-cx-env'); list.id = 'vwCxInactiveRows';
    list.textContent = 'Available on the other environment: ' + inactiveEmpty.map(rowName).join(', ') + '.';
    box.appendChild(list);
  }
  details.appendChild(box);
  return details;
}
function mountInto(host){
  // The integrated Settings page (/settings#wallet) renders a host for this section; the same
  // section object moves there so its state is never rebuilt twice. Returns whether it mounted.
  if (!host) return false;
  var existing = document.getElementById('vwSec');
  if (existing) { if (existing.parentNode !== host) host.appendChild(existing); }
  else host.appendChild(buildSection());
  renderStatus();
  return true;
}
function mount(){
  var direct = document.getElementById('walletHost');
  if (direct) { mountInto(direct); return; }
  var overlay = document.getElementById('settingsOverlay');
  if (!overlay || document.getElementById('vwSec')) return;
  var anchorHeading = Array.from(overlay.querySelectorAll('h3, h4, .set-sec-title, b')).find(function(node){ return /about this build/i.test(node.textContent || ''); });
  var host = anchorHeading ? (anchorHeading.closest('.set-sec') || anchorHeading.parentNode) : null;
  var section = buildSection();
  if (host && host.parentNode) host.parentNode.insertBefore(section, host); else overlay.appendChild(section);
  document.addEventListener('click', function(ev){
    if (ev.target && (ev.target.id === 'settingsBtn' || (ev.target.closest && ev.target.closest('#settingsBtn')))) setTimeout(renderStatus, 60);
  });
  renderStatus();
  // a provider Phantom already injected (extension present) gets its events from the start
  bindPhantomEvents();
}

// ---- approval cards in the transcript -------------------------------------------------------
var rendered = {};
// ---- what approving this does: one sentence and a tier from the decoded bytes; red needs the capability said back ----
function approvalAllowed(m, acked){ return !m || m.tier !== 'red' || !!acked; }
function renderMeaning(host, m){
  if (!host || !m) return null;
  var box = el('div', 'vw-meaning ' + (m.tier || '')); box.setAttribute('role', m.tier === 'red' ? 'alert' : 'note');
  box.appendChild(el('div', 'vw-headline', m.headline || ''));
  (m.lines || []).forEach(function(line){ box.appendChild(el('div', 'vw-sub', line)); });
  var ack = null;
  if (m.tier === 'red') {
    var wrap = el('label', 'vw-ack');
    ack = el('input'); ack.type = 'checkbox'; ack.className = 'vw-ack-box'; ack.setAttribute('aria-label', 'acknowledge the capability being granted');
    wrap.appendChild(ack); wrap.appendChild(el('span', null, m.ack_text || 'I understand what approving this does.'));
    box.appendChild(wrap);
    box.appendChild(el('div', 'vw-sub', 'How people lose their crypto this way: Settings → Crypto → Stay safe.'));
  }
  host.appendChild(box);
  return ack;
}
function WT(key, fallback){
  try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
  return fallback;
}
// The {param} twin of WT: the bundle's formatter fills the locale's placeholders; without a bundle the
// English fallback is filled the same way (every {name} the params carry, nothing else).
function WTF(key, fallback, params){
  var t = WT(key, fallback), p = params || {};
  try { if (typeof VOOLFMT === 'function') return VOOLFMT(t, p); } catch (e) {}
  Object.keys(p).forEach(function(k){ t = String(t).split('{' + k + '}').join(String(p[k])); });
  return t;
}
function cardFor(p){
  var card = el('div', 'msg assistant vw-card'); card.setAttribute('data-proposal', p.proposal_id);
  card.appendChild(el('div', 'vw-title', WT('wallet.review_title', 'Payment approval needed')));
  card.appendChild(purposeBlock(p.purpose, WT('wallet.review_title', 'Payment approval needed')));
  var chainRow = el('div', 'vw-row');
  chainRow.appendChild(document.createTextNode('network: ' + chainLabel(p.network) + ' · '));
  if (p.mainnet !== false && p.mainnet !== undefined) chainRow.appendChild(el('span', 'vw-badge', p.mainnet ? 'real money' : 'network not read'));
  else chainRow.appendChild(el('span', 'vw-badge testnet', WT('wallet.testnet_badge', 'testnet funds, not real money')));
  card.appendChild(chainRow);
  card.appendChild(el('div', 'vw-row', p.amount_human != null && p.amount_human !== '' ? p.amount_human + ' ' + (p.display_symbol || p.asset) : p.amount_minor + ' ' + p.asset + ' ' + WT('wallet.minor_units', '(minor units)')));
  card.appendChild(maskedLine('destination', p.destination, 'vwDest-' + p.proposal_id));
  card.appendChild(el('div', 'vw-row', WT('wallet.proposed_by', 'proposed by ') + (p.origin || 'unknown') + (p.memo ? ' · ' + p.memo : '') + ' · ' + p.proposal_id));
  if (p.payment_channel) {
    // a payment parked on a channel the owner turned off is refused at approval: say so before the PIN is typed
    var channelOn = p.payment_channel.on;
    var channelNote = channelOn === true ? '' : (channelOn === false ? ' (turned off: turn it on in Settings, Crypto, Payment channels to approve this payment)' : ' (could not be read right now)');
    var channelRow = el('div', 'vw-row' + (channelOn === true ? '' : ' vw-cx-warn'), 'payment channel: ' + p.payment_channel.label + channelNote);
    channelRow.setAttribute('data-channel', p.payment_channel.channel_id); channelRow.setAttribute('data-channel-on', channelOn === true ? 'true' : (channelOn === false ? 'false' : 'unknown'));
    card.appendChild(channelRow);
  }
  var ack = renderMeaning(card, p.meaning);
  function ackText(){ return (p.meaning && p.meaning.tier === 'red') ? p.meaning.ack_text : undefined; }
  var controls = el('div', 'vw-row');
  var watchOnly = lastStatus && lastStatus.custody_mode === 'watch_only';
  // on a Mainnet row a connected or watch-only account receives and shows its balance only: the card asks for no
  // credential and offers no signer, it says why (every door refuses it the same way) and keeps Reject
  var viewOnly = !!p.send_unavailable;
  var method = (lastStatus && lastStatus.approval_method) || 'pin';
  var pin = el('input', 'vw-pin'); pin.type = 'password'; pin.autocomplete = 'off'; pin.setAttribute('aria-label', method === 'password' ? WT('wallet.pw_aria', 'wallet password') : WT('wallet.pin_aria', 'wallet PIN'));
  if (method === 'password') { pin.placeholder = WT('wallet.pw_aria', 'wallet password'); } else { pin.placeholder = WT('wallet.pin_short', 'PIN'); pin.inputMode = 'numeric'; }
  var approve = el('button', 'vw-btn primary vw-approve' + (method === 'device' ? ' vw-approve-device' : ''), method === 'device' ? WT('wallet.approve_device', 'Approve with Touch ID / password') : (method === 'password' ? WT('wallet.approve_password', 'Approve with password') : WT('wallet.approve_pin', 'Approve with PIN'))); approve.type = 'button';
  var reject = el('button', 'vw-btn vw-reject', WT('wallet.reject', 'Reject')); reject.type = 'button'; reject.setAttribute('aria-label', WT('wallet.reject_aria', 'reject the payment proposal'));
  if (viewOnly) {
    var why = el('span', 'vw-line vw-cx-warn', p.send_unavailable.message || 'This account cannot send from VOOL on this network. Nothing was signed or sent.');
    why.setAttribute('data-send-unavailable', p.send_unavailable.code || ''); controls.appendChild(why);
  } else if (watchOnly) {
    // a watch-only wallet has no signer: offering a PIN box would be a dead end
    controls.appendChild(el('span', 'vw-line', WT('wallet.watch_only_hint', 'watch-only: this proposal can be approved in an external wallet, not signed here')));
  } else {
    if (method !== 'device') controls.appendChild(pin);
    controls.appendChild(approve);
  }
  var ext = null, evm = null;
  if (!viewOnly && lastStatus && lastStatus.custody_mode === 'external_signer') {
    if (isEvmNetwork(p.network)) {
      evm = el('button', 'vw-btn primary vw-sign-evm', WT('wallet.sign_evm', 'Sign with EVM wallet')); evm.type = 'button'; controls.appendChild(evm);
    } else {
      ext = el('button', 'vw-btn primary vw-sign-external', WT('wallet.sign_phantom', 'Sign with Phantom')); ext.type = 'button'; controls.appendChild(ext);
    }
  }
  controls.appendChild(reject); card.appendChild(controls);
  var result = el('div', 'vw-result'); card.appendChild(result);
  function gate(){ var ok = approvalAllowed(p.meaning, ack && ack.checked); approve.disabled = !ok; if (ext) ext.disabled = !ok; if (evm) evm.disabled = !ok; }
  if (ack) ack.addEventListener('change', gate); gate();
  function finish(text){ result.textContent = text; pin.value = ''; }
  function done(receipt){
    controls.remove();
    finish(WT('wallet.payment_state', 'Payment ') + receipt.state + ' · ' + WT('wallet.signature', 'signature') + ' ' + (receipt.tx_signature || '-') + ' · ' + receipt.network);
    var a = actions(); if (a && a.toast) a.toast(WT('wallet.payment_state', 'Payment ') + receipt.state);
  }
  if (!watchOnly && !viewOnly) approve.addEventListener('click', function(){
    approve.disabled = true;
    var approvalBody = method === 'device' ? { proposal_id: p.proposal_id, method: 'device', acknowledged_capability: ackText() } : (method === 'password' ? { proposal_id: p.proposal_id, password: pin.value, acknowledged_capability: ackText() } : { proposal_id: p.proposal_id, pin: pin.value, acknowledged_capability: ackText() });
    if (method === 'device') setMsg(WT('wallet.confirm_device', 'Confirm with Touch ID or your Mac password…'));
    postJson('/api/wallet/approve', approvalBody).then(function(res){
      approve.disabled = !approvalAllowed(p.meaning, ack && ack.checked);
      if (!res.ok) { finish((res.data && (res.data.message || res.data.error)) || 'Not approved.'); if (res.status === 409) controls.remove(); return; }
      done(res.data.receipt || {});
    }).catch(function(){ approve.disabled = false; finish('The approval request did not complete.'); });
  });
  reject.addEventListener('click', function(){
    postJson('/api/wallet/reject', { proposal_id: p.proposal_id }).then(function(){ controls.remove(); finish('Rejected. Nothing was signed or sent.'); });
  });
  if (evm) evm.addEventListener('click', function(){
    var eth = evmProvider();
    if (!eth) { finish(WT('wallet.home.no_evm', 'No MetaMask or other EVM wallet was found in this browser. Open VOOL in a browser that has the wallet extension to connect it.')); return; }
    evm.disabled = true;
    postJson('/api/wallet/approve', { proposal_id: p.proposal_id, method: 'external', acknowledged_capability: ackText() }).then(function(res){
      if (!res.ok) { evm.disabled = false; finish((res.data && (res.data.message || res.data.error)) || 'No signing request.'); return null; }
      var sr = res.data.signing_request;
      if (sr.meaning && sr.meaning.tier === 'red' && !(ack && ack.checked)) { renderMeaning(card, sr.meaning); evm.disabled = false; finish('Read what these bytes do and confirm the acknowledgement before signing.'); return null; }
      var call = sr.transports && sr.transports.eip1193 ? sr.transports.eip1193 : { method: 'eth_signTypedData_v4', params: [sr.public_key, ''] };
      return eth.request(call).then(function(signature){
        var sig = String(signature || '');
        if (sig.indexOf('0x') !== 0) throw new Error('the wallet returned no signature');
        return postJson('/api/wallet/external/submit', { request_id: sr.request_id, signature_hex: sig });
      });
    }).then(function(res){
      if (!res) return;
      evm.disabled = false;
      if (!res.ok) { finish((res.data && (res.data.message || res.data.error)) || 'The signature was not accepted.'); return; }
      done(res.data.receipt || {});
    }).catch(function(err){ evm.disabled = false; finish('The EVM wallet did not sign: ' + (err && err.message ? err.message : 'unknown error')); });
  });
  if (ext) ext.addEventListener('click', function(){
    var prov = provider();
    if (!prov || typeof prov.request !== 'function') { finish('Phantom is not connected: its provider is not injected here. Open this app in Chrome or Brave with the Phantom extension to sign there; nothing was signed.'); return; }
    ext.disabled = true;
    postJson('/api/wallet/approve', { proposal_id: p.proposal_id, method: 'external' }).then(function(res){
      if (!res.ok) { ext.disabled = false; finish((res.data && (res.data.message || res.data.error)) || 'No signing request.'); return null; }
      var sr = res.data.signing_request;
      var call = sr.transports && sr.transports.phantom_injected ? sr.transports.phantom_injected : { method: 'signTransaction', params: { message: sr.message_b58 } };
      return prov.request(call).then(function(answer){
        var signature = answer && (answer.signature || answer.result && answer.result.signature) || '';
        return postJson('/api/wallet/external/submit', { request_id: sr.request_id, signature_b58: String(signature || '') });
      });
    }).then(function(res){
      if (!res) return;
      ext.disabled = false;
      if (!res.ok) { finish((res.data && (res.data.message || res.data.error)) || 'The signature was not accepted.'); return; }
      done(res.data.receipt || {});
    }).catch(function(){ ext.disabled = false; finish('The external wallet did not sign.'); });
  });
  return card;
}
// ---- the Crypto Pilot approval sheet: one dialog at a time, every value from the typed quote ----------
// The server owns every number (human strings included); the page never does money arithmetic. The credential
// field exists only inside the dialog and is cleared on every answer and on close.
var sheetOpen = null;
var CUE_TEXT = {
  new_recipient_account: 'The recipient account does not exist yet; this transfer creates it.',
  non_system_owner_recipient: 'The recipient account is owned by a program, not a plain wallet.',
  contract_recipient: 'The recipient is a contract. Contracts can keep or forward what they receive.',
  eip7702_delegated_recipient: 'The recipient delegates its code to a contract (EIP-7702).',
  unchecked_lowercase_address: 'The recipient address has no checksum; check every character.'
};
function nowSec(){ return Date.now() / 1000; }
function purposeBlock(purpose, fallbackHeadline){
  // who receives what and why: the server's typed facts, verbatim. Unknown stays visibly unknown.
  var box = el('div', 'vw-purpose'); box.setAttribute('data-field', 'purpose');
  var pu = purpose || { kind: 'unknown', headline: fallbackHeadline || 'Purpose unknown', mechanism_label: 'Purpose unknown' };
  box.setAttribute('data-kind', pu.kind || 'unknown');
  var head = el('div');
  head.appendChild(el('span', 'vw-purpose-headline', pu.headline || fallbackHeadline || 'Purpose unknown'));
  head.appendChild(el('span', 'vw-mechanism', pu.mechanism_label || 'Purpose unknown'));
  box.appendChild(head);
  if (pu.provider) box.appendChild(el('div', 'vw-sub', 'Provider: ' + pu.provider + (pu.provider_source ? ' — ' + pu.provider_source : '')));
  if (pu.resource) { var res = el('div', 'vw-sub'); res.appendChild(document.createTextNode('Resource: ' + (pu.resource_method ? pu.resource_method + ' ' : ''))); res.appendChild(el('span', 'vw-ident', pu.resource)); box.appendChild(res); }
  if (pu.charge_scope) box.appendChild(el('div', 'vw-sub', 'Charge: ' + pu.charge_scope));
  if (pu.description) box.appendChild(el('div', 'vw-sub', 'Says: \u201c' + pu.description + '\u201d (' + (pu.description_source || 'provider text, untrusted') + ')'));
  if (pu.note) box.appendChild(el('div', 'vw-purpose-note', pu.note));
  return box;
}
function copyButton(value, what){
  var btn = el('button', 'vw-copy', WT('msg.copy', 'Copy')); btn.type = 'button';
  btn.setAttribute('aria-label', WTF('wallet.copy_aria', 'copy the full {what}', { what: what || WT('wallet.copy_value', 'value') }));
  btn.addEventListener('click', function(){
    try { navigator.clipboard.writeText(String(value || '')).then(function(){ btn.textContent = WT('msg.copied', 'Copied'); }, function(){ btn.textContent = WT('wallet.copy_select', 'Select and copy'); }); }
    catch (e) { btn.textContent = WT('wallet.copy_select', 'Select and copy'); }
  });
  return btn;
}
function identRow(host, name, label, value, opts){
  // an address or hash: shown in full, breakable anywhere, selectable, copyable; a label supplements it, never replaces
  // it. The value span carries exactly the field's text; the copy button and any note are siblings in the row.
  var row = el('div', 'vw-sheet-row'); row.setAttribute('data-field', name);
  row.appendChild(el('span', 'vw-sheet-label', label));
  var cell = el('span', 'vw-sheet-cell');
  var wrap = el('span', 'vw-sheet-value');
  if (opts && opts.prefix) wrap.appendChild(document.createTextNode(opts.prefix));
  var ident = el('span', 'vw-ident', value); wrap.appendChild(ident);
  if (opts && opts.badge) wrap.appendChild(opts.badge);
  cell.appendChild(wrap);
  cell.appendChild(copyButton(value, label.toLowerCase()));
  if (opts && opts.note) cell.appendChild(el('span', 'vw-sub vw-ident-note', ' ' + opts.note));
  row.appendChild(cell); host.appendChild(row);
  return wrap;
}
var recoveryOpen = null;
function forgotLink(walletId, text){
  var link = el('button', 'vw-forgot', text || 'Forgot PIN or password?'); link.type = 'button';
  link.setAttribute('aria-label', 'recover access to this wallet without the PIN or password');
  link.addEventListener('click', function(ev){ ev.preventDefault(); openRecovery(walletId); });
  return link;
}
function openRecovery(walletId){
  // A local recovery surface. Reading it changes nothing; closing it changes nothing. The private key typed here
  // goes to the wallet's own recovery door and nowhere else: never to the chat, a provider, a log or the clipboard.
  if (recoveryOpen) { recoveryOpen.focus(); return; }
  var overlay = el('div', 'vw-sheet-overlay'); overlay.id = 'vwRecovery';
  var box = el('div', 'vw-sheet vw-recovery'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-labelledby', 'vwRecoveryTitle');
  var title = el('h3', null, 'Forgot the PIN or password?'); title.id = 'vwRecoveryTitle'; box.appendChild(title);
  var identity = el('div', 'vw-line'); identity.id = 'vwRecoveryWallet'; box.appendChild(identity);
  var body = el('div'); body.id = 'vwRecoveryBody'; box.appendChild(body);
  var state = el('div', 'vw-msg'); state.id = 'vwRecoveryState'; state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite'); box.appendChild(state);
  var actions = el('div', 'vw-sheet-actions');
  var closeBtn = el('button', 'vw-btn', 'Close'); closeBtn.id = 'vwRecoveryClose'; closeBtn.type = 'button';
  actions.appendChild(closeBtn); box.appendChild(actions);
  overlay.appendChild(box); document.body.appendChild(overlay);
  var secretFields = [];
  function setState(text, isErr){ state.textContent = text || ''; state.className = 'vw-msg' + (isErr ? ' err' : ''); }
  function clearSecrets(){ secretFields.forEach(function(f){ f.value = ''; }); }
  function close(){
    document.removeEventListener('keydown', onKey, true);
    clearSecrets(); secretFields = []; body.textContent = ''; overlay.remove(); recoveryOpen = null;
  }
  function onKey(ev){ if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); close(); } }
  closeBtn.addEventListener('click', close);
  document.addEventListener('keydown', onKey, true);
  recoveryOpen = { walletId: walletId, focus: function(){ closeBtn.focus(); } };
  function faultText(res, fallback){ return (res && res.data && (res.data.message || (res.data.fault && res.data.fault.context && res.data.fault.context.reason) || res.data.error)) || fallback; }
  function credentialFields(host, prefix){
    var mLabel = el('label', null, 'New way to approve payments'); mLabel.setAttribute('for', prefix + 'Method'); host.appendChild(mLabel);
    var method = el('select', 'vw-chain'); method.id = prefix + 'Method';
    [['pin', 'PIN (6–12 digits)'], ['password', 'Wallet password (10+ characters)']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
    host.appendChild(method);
    var nLabel = el('label', null, 'New PIN'); nLabel.setAttribute('for', prefix + 'New'); host.appendChild(nLabel);
    var nw = el('input'); nw.type = 'password'; nw.id = prefix + 'New'; nw.autocomplete = 'new-password'; nw.inputMode = 'numeric'; host.appendChild(nw);
    var cLabel = el('label', null, 'Repeat the new PIN'); cLabel.setAttribute('for', prefix + 'Confirm'); host.appendChild(cLabel);
    var cf = el('input'); cf.type = 'password'; cf.id = prefix + 'Confirm'; cf.autocomplete = 'new-password'; cf.inputMode = 'numeric'; host.appendChild(cf);
    method.addEventListener('change', function(){ var pin = method.value === 'pin'; nLabel.textContent = pin ? 'New PIN' : 'New password'; cLabel.textContent = pin ? 'Repeat the new PIN' : 'Repeat the new password'; nw.inputMode = cf.inputMode = pin ? 'numeric' : 'text'; });
    secretFields.push(nw, cf);
    return { method: method, nw: nw, cf: cf };
  }
  function done(res){
    var setup = (res.data && res.data.setup) || {};
    clearSecrets(); body.textContent = '';
    body.appendChild(el('div', 'vw-warn', 'Access restored for ' + short(setup.address || '') + ' with your new ' + (setup.method === 'password' ? 'password' : 'PIN') + '. ' + (setup.note || '')));
    setState('');
    renderStatus(); poll();
  }
  postJson('/api/wallet/recovery/options', { wallet_id: walletId }).then(function(res){
    if (!res.ok) { setState(faultText(res, 'Recovery options could not be read.'), true); return; }
    var o = res.data.recovery || {};
    identity.textContent = (o.label ? o.label + ' · ' : '') + (o.display_name || o.network) + ' · ';
    identity.appendChild(el('span', 'vw-ident', o.address || ''));
    body.appendChild(el('div', 'vw-line', 'A PIN or password protects the key sealed on this device. VOOL cannot read it back without one of the proofs below; no waiting period, label or support request unlocks it.'));
    // 1. restore with the backup VOOL issued -- always offered
    var backup = el('div', 'vw-option'); backup.setAttribute('data-recovery', 'backup');
    backup.appendChild(el('h4', null, '1. Restore with your private-key backup'));
    backup.appendChild(el('div', 'vw-line', 'Paste ' + (o.backup && o.backup.hint ? o.backup.hint : 'the backup VOOL showed at setup') + '. It restores ' + (o.backup && o.backup.restores ? o.backup.restores : 'this wallet only') + '.'));
    var kLabel = el('label', null, 'Private-key backup'); kLabel.setAttribute('for', 'vwRecoveryKey'); backup.appendChild(kLabel);
    var key = el('textarea'); key.id = 'vwRecoveryKey'; key.autocomplete = 'off'; key.spellcheck = false; key.setAttribute('aria-label', 'private-key backup for this wallet'); backup.appendChild(key);
    secretFields.push(key);
    var creds = credentialFields(backup, 'vwRecovery');
    var restore = el('button', 'vw-btn primary', 'Restore access'); restore.id = 'vwRecoveryRestore'; restore.type = 'button'; backup.appendChild(restore);
    if (o.backup && o.backup.retry_after_seconds) { restore.disabled = true; backup.appendChild(el('div', 'vw-line vw-soft-warning', 'Too many wrong backups: try again in ' + o.backup.retry_after_seconds + ' s.')); }
    restore.addEventListener('click', function(){
      restore.disabled = true; setState('Checking the backup against this wallet…');
      var payload = { wallet_id: walletId, backup_value: key.value, method: creds.method.value, credential: creds.nw.value, credential_confirmation: creds.cf.value };
      key.value = '';  // the key leaves the field on submission; the door answers, the field never keeps it
      postJson('/api/wallet/recovery/backup', payload).then(function(res){
        payload = null; restore.disabled = false;
        if (!res.ok) { creds.nw.value = ''; creds.cf.value = ''; setState(faultText(res, 'Access was not restored. Nothing changed.'), true); return; }
        done(res);
      }).catch(function(){ payload = null; restore.disabled = false; creds.nw.value = ''; creds.cf.value = ''; setState('The recovery request did not complete. Nothing changed.', true); });
    });
    body.appendChild(backup);
    // 2. device recovery -- only when this wallet already keeps a device-protected copy
    var dev = el('div', 'vw-option'); dev.setAttribute('data-recovery', 'device');
    dev.appendChild(el('h4', null, '2. Recover with Touch ID or your Mac password'));
    var d = o.device_recovery || {};
    if (d.available) {
      dev.appendChild(el('div', 'vw-line', 'This wallet keeps a copy protected by this Mac. Confirming with Touch ID or your Mac password releases it and sets your new PIN or password.'));
      var dcreds = credentialFields(dev, 'vwRecoveryDevice');
      var devBtn = el('button', 'vw-btn primary', 'Confirm with Touch ID / Mac password'); devBtn.id = 'vwRecoveryDevice'; devBtn.type = 'button'; dev.appendChild(devBtn);
      devBtn.addEventListener('click', function(){
        devBtn.disabled = true; setState('Confirm with Touch ID or your Mac password…');
        postJson('/api/wallet/recovery/device', { wallet_id: walletId, challenge: d.challenge, method: dcreds.method.value, credential: dcreds.nw.value, credential_confirmation: dcreds.cf.value }).then(function(res){
          devBtn.disabled = false;
          if (!res.ok) { dcreds.nw.value = ''; dcreds.cf.value = ''; setState(faultText(res, 'Not recovered. Nothing changed.'), true); return; }
          done(res);
        }).catch(function(){ devBtn.disabled = false; dcreds.nw.value = ''; dcreds.cf.value = ''; setState('The recovery request did not complete. Nothing changed.', true); });
      });
    } else {
      dev.className += ' muted';
      dev.appendChild(el('div', 'vw-line', d.reason === 'not_enrolled' ? 'Device recovery was not set up for this wallet, so it is not offered. Use the backup above.' : 'Device recovery was set up, but this Mac cannot release the copy right now (' + (d.reason || 'unavailable') + '). Use the backup above.'));
      dev.appendChild(el('div', 'vw-line', d.note || ''));
    }
    body.appendChild(dev);
    // 3. use another compatible wallet -- guidance only, never a third-party form
    var ext = el('div', 'vw-option'); ext.setAttribute('data-recovery', 'external');
    ext.appendChild(el('h4', null, '3. Use another compatible wallet'));
    var x = o.external_import || {};
    ext.appendChild(el('div', 'vw-line', (x.note || 'The saved private key controls the address independently of VOOL’s PIN.') + ' Target: ' + (x.target || '')));
    var steps = el('ul'); (x.guidance || []).forEach(function(g){ steps.appendChild(el('li', null, g)); }); ext.appendChild(steps);
    var refs = el('ul'); (x.references || []).forEach(function(u){ var li = el('li'); var a = el('a', null, u); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; li.appendChild(a); refs.appendChild(li); }); ext.appendChild(refs);
    body.appendChild(ext);
    // 4. nothing else resets access
    var none = el('div', 'vw-option muted'); none.setAttribute('data-recovery', 'none');
    none.appendChild(el('h4', null, '4. No backup and no device copy'));
    none.appendChild(el('div', 'vw-line', (o.no_reset && o.no_reset.text) || 'Without the backup or an enrolled device secret, VOOL cannot reset access.'));
    none.appendChild(el('div', 'vw-line', o.pin_change_note || ''));
    body.appendChild(none);
    setState('');
    key.focus();
  }).catch(function(){ setState('Recovery options could not be read.', true); });
}
function sheetRow(host, name, label, value){
  var row = el('div', 'vw-sheet-row'); row.setAttribute('data-field', name);
  row.appendChild(el('span', 'vw-sheet-label', label));
  var value_el = el('span', 'vw-sheet-value', value); row.appendChild(value_el);
  host.appendChild(row);
  return value_el;
}
var transferCards = {};
// Status reads are numbered as the page asks them. A transfer card drawn from a newer answer (an approval's or a
// cancel's own reply, a later read) keeps it: a read asked before that, answering late, never takes the card back.
var statusAsked = 0;
function markAsked(host, asked){ host.setAttribute('data-status-asked', String(asked)); }
function askedBefore(host, asked){ return asked <= Number(host.getAttribute('data-status-asked') || 0); }
function transferLine(t){
  var line = el('div', 'vw-transfer');
  line.setAttribute('data-transfer-state', t.state || '');
  line.appendChild(el('span', 'vw-transfer-label', 'Transfer ' + (t.state_label || t.state || 'submitted') + ' on ' + (t.display_name || chainLabel(t.network))));
  if (t.detail) line.appendChild(el('div', 'vw-transfer-detail', t.detail));
  if (t.purpose) line.appendChild(purposeBlock(t.purpose, ''));
  if (t.to_address) { var to = el('div', 'vw-transfer-detail'); to.appendChild(document.createTextNode('To ')); to.appendChild(el('span', 'vw-ident', t.to_address)); line.appendChild(to); }
  if (t.charged_fee_label) line.appendChild(el('div', 'vw-transfer-detail', 'Fee ' + (t.charged_fee_display || t.charged_fee_label) + (t.charged_fee_display && t.charged_fee_human ? ' (exactly ' + t.charged_fee_human + ' ' + (t.fee_symbol || t.display_symbol) + ')' : '')));
  if (t.tx_id) line.appendChild(el('div', 'vw-transfer-id', 'Transaction ' + t.tx_id));
  if (t.explorer_url) {
    var link = el('a', 'vw-explorer', t.explorer_link_text || 'View on the explorer');
    link.href = t.explorer_url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    line.appendChild(link);
  }
  if (t.offered_exit && t.exit_terms && t.exit_terms.door) {
    var exit = el('button', 'vw-btn vw-exit', t.exit_terms.action || 'Review');
    exit.type = 'button'; exit.setAttribute('data-exit', t.offered_exit);
    exit.addEventListener('click', function(){ openExitSheet(t, line); });
    line.appendChild(exit);
  }
  return line;
}
function openExitSheet(t, host){
  if (sheetOpen) { sheetOpen.focus(); return; }
  var terms = t.exit_terms || {};
  var overlay = el('div', 'vw-sheet-overlay'); overlay.id = 'vwExitSheet';
  var box = el('div', 'vw-sheet'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('data-proposal', t.proposal_id); box.setAttribute('data-exit', t.offered_exit);
  box.appendChild(el('h3', null, terms.title || 'Transfer'));
  sheetRow(box, 'network', 'Network', (t.display_name || chainLabel(t.network)));
  sheetRow(box, 'amount', 'Amount', t.amount_human + ' ' + t.display_symbol);
  sheetRow(box, 'to', 'To', t.to_address);
  sheetRow(box, 'state', 'Current record', t.state_label || t.state);
  var risk = el('div', 'vw-sheet-note', terms.risk || ''); risk.setAttribute('data-field', 'risk'); box.appendChild(risk);
  var state = el('div', 'vw-msg'); state.id = 'vwExitState'; state.setAttribute('role', 'status'); box.appendChild(state);
  var credWrap = el('div', 'vw-sheet-actions');
  var cred = el('input', 'vw-pin'); cred.id = 'vwExitCredential'; cred.type = 'password'; cred.autocomplete = 'off'; cred.placeholder = 'wallet PIN or password';
  cred.setAttribute('aria-label', 'wallet credential for this decision only');
  credWrap.appendChild(cred); box.appendChild(credWrap);
  var actionsRow = el('div', 'vw-sheet-actions');
  var back = el('button', 'vw-btn', 'Back'); back.id = 'vwExitBack'; back.type = 'button';
  var confirm = el('button', 'vw-btn primary', terms.action || 'Confirm'); confirm.id = 'vwExitConfirm'; confirm.type = 'button';
  actionsRow.appendChild(back); actionsRow.appendChild(confirm); box.appendChild(actionsRow);
  overlay.appendChild(box); document.body.appendChild(overlay);
  function close(){ document.removeEventListener('keydown', onKey, true); cred.value = ''; overlay.remove(); sheetOpen = null; }
  function onKey(ev){ if (recoveryOpen) return; if (ev.key === 'Escape') { ev.preventDefault(); close(); } }
  back.addEventListener('click', close);
  confirm.addEventListener('click', function(){
    confirm.disabled = true; state.textContent = 'Checking the record and your credential…';
    var body = { proposal_id: t.proposal_id, pin: cred.value }; cred.value = '';
    postJson(terms.door, body).then(function(res){
      body = null;
      if (res.ok && res.data && res.data.transfer) { close(); renderTransfer(host.parentNode || host, res.data.transfer); markAsked(host.parentNode || host, statusAsked); return; }
      confirm.disabled = false; state.textContent = (res.data && (res.data.message || res.data.error)) || 'Not done.'; state.className = 'vw-msg err';
    }).catch(function(){ body = null; confirm.disabled = false; state.textContent = 'The request did not complete.'; state.className = 'vw-msg err'; });
  });
  document.addEventListener('keydown', onKey, true);
  sheetOpen = { proposalId: t.proposal_id, focus: function(){ cred.focus(); }, sync: function(){} };
  cred.focus();
}
function renderTransfer(host, t){
  host.textContent = '';
  host.appendChild(transferLine(t || {}));
}
function transferCardFor(t){
  var card = el('div', 'msg assistant vw-card'); card.setAttribute('data-proposal', t.proposal_id); card.setAttribute('data-transfer', '1');
  card.appendChild(el('div', 'vw-title', t.origin === 'dna_fee' ? 'DNA fee collection' + (t.dna_fee_collection && t.dna_fee_collection.payment_proposal_id ? ' (with payment ' + short(t.dna_fee_collection.payment_proposal_id) + ')' : '') : 'Transfer'));
  card.appendChild(el('div', 'vw-row', 'network: ' + (t.display_name || chainLabel(t.network))));
  var amtRow = el('div', 'vw-row'); amtRow.appendChild(document.createTextNode(t.amount_human + ' ' + t.display_symbol + ' to ')); amtRow.appendChild(el('span', 'vw-ident', t.to_address)); card.appendChild(amtRow);
  var result = el('div', 'vw-result');
  renderTransfer(result, t);
  card.appendChild(result);
  transferCards[t.proposal_id] = result;
  return card;
}
function syncTransfers(st, asked){
  var seen = {};
  (st.in_flight || []).concat(st.transfers || []).forEach(function(t){
    if (!t || !t.proposal_id || seen[t.proposal_id]) return;
    seen[t.proposal_id] = true;
    var host = transferCards[t.proposal_id];
    if (host && host.getAttribute('data-transfer-state') !== t.state && !askedBefore(host, asked)) { renderTransfer(host, t); host.setAttribute('data-transfer-state', t.state); markAsked(host, asked); }
  });
}
var pilotCards = {};
var ENDED_TEXT = {
  rejected: 'Cancelled elsewhere. Nothing was signed or sent.',
  expired: 'This request expired before a decision. Nothing was signed or sent.',
  failed: 'This request ended without a payment. Nothing was sent.'
};
function syncPilotCards(st, asked){
  // A pending card belongs to the request, not to this page: when the request is approved, cancelled or expired on
  // another surface (another window, the API, a restart), the card follows the authoritative status -- a transfer
  // record replaces the Review action with the receipt line; an ended proposal says how it ended. Reading only:
  // nothing here approves, rejects, quotes or pays.
  var pending = {};
  (st.pending || []).forEach(function(p){ if (p && p.proposal_id) pending[p.proposal_id] = true; });
  // The transfers read model also carries refusal receipts (record_kind 'proposal_receipt'): a request that ended
  // before any claim. That row is the receipt of an ended proposal, never a transfer, so it settles the card the
  // way an ended proposal does instead of rendering a transfer line.
  var rows = {}, ended = {};
  (st.in_flight || []).concat(st.transfers || []).forEach(function(t){
    if (!t || !t.proposal_id) return;
    if (t.record_kind === 'proposal_receipt') { if (!ended[t.proposal_id] && t.proposal_state) ended[t.proposal_id] = t.proposal_state; return; }
    if (!rows[t.proposal_id]) rows[t.proposal_id] = t;
  });
  function settleEnded(pid, entry, state){
    entry.controls.remove();
    entry.result.textContent = ENDED_TEXT[state] || ('This request ended (' + state + '). Nothing was signed or sent.');
    entry.result.setAttribute('data-proposal-state', state);
    delete pilotCards[pid];
  }
  Object.keys(pilotCards).forEach(function(pid){
    if (pending[pid]) return;
    var entry = pilotCards[pid];
    var t = rows[pid];
    if (t) {
      entry.controls.remove(); renderTransfer(entry.result, t); transferCards[pid] = entry.result; entry.result.setAttribute('data-transfer-state', t.state || ''); markAsked(entry.result, asked);
      delete pilotCards[pid];
      return;
    }
    if (ended[pid]) { settleEnded(pid, entry, ended[pid]); return; }
    if (entry.resolving) return;
    entry.resolving = true;
    getJson('/api/wallet/proposals/' + encodeURIComponent(pid)).then(function(j){
      var proposal = (j && j.proposal) || null;
      if (!proposal || !proposal.state) { entry.resolving = false; return; }
      if (proposal.state === 'pending_approval' || proposal.state === 'awaiting_signature') { entry.resolving = false; return; }  // a snapshot raced the record
      if (pilotCards[pid] !== entry) return;  // settled meanwhile by a later snapshot
      settleEnded(pid, entry, proposal.state);
    }).catch(function(){ entry.resolving = false; });
  });
}
function pilotCardFor(p){
  var card = el('div', 'msg assistant vw-card'); card.setAttribute('data-proposal', p.proposal_id); card.setAttribute('data-pilot', '1');
  var collection = p.dna_fee_collection || null;
  var companion = p.companion_collection && p.companion_collection.collection_id ? p.companion_collection : null;
  card.appendChild(el('div', 'vw-title', collection ? 'DNA service fee collection' : (p.purpose && p.purpose.kind === 'service' ? 'Service payment request' : (p.purpose && p.purpose.kind === 'credit' ? 'Provider credit request' : 'Transfer request'))));
  card.appendChild(purposeBlock(p.purpose, 'Transfer request'));
  card.appendChild(el('div', 'vw-row', 'network: ' + chainLabel(p.network)));
  var toRow = el('div', 'vw-row'); toRow.appendChild(document.createTextNode('to: ')); toRow.appendChild(el('span', 'vw-ident', p.destination || (p.purpose && p.purpose.beneficiary) || '')); card.appendChild(toRow);
  card.appendChild(el('div', 'vw-row', 'proposed by ' + (p.origin || 'unknown') + (p.memo ? ' · ' + p.memo : '') + ' · ' + p.proposal_id));
  if (collection) { var cRow = el('div', 'vw-row', 'fee payment: ' + collection.amount_exact + ' of accrued DNA service fees to the treasury · ' + (collection.state_label || collection.state)); cRow.setAttribute('data-dna-fee-collection', collection.collection_id); card.appendChild(cRow); }
  if (companion) { var kRow = el('div', 'vw-row', 'includes previously accrued DNA fees: ' + companion.amount_exact + ' ' + (companion.asset || '') + ' collected with this payment under its approval · ' + (companion.state_label || companion.state)); kRow.setAttribute('data-companion-collection', companion.collection_id); card.appendChild(kRow); }
  var controls = el('div', 'vw-row');
  var review = el('button', 'vw-btn primary vw-review', collection ? 'Review fee collection' : (p.origin === 'usepod' ? 'Review payment' : 'Review transfer')); review.type = 'button';
  review.setAttribute('aria-label', 'review transfer details; opening the preview does not approve it');
  var result = el('div', 'vw-result');
  review.addEventListener('click', function(){ openSheet(p, controls, result, review); });
  controls.appendChild(review); card.appendChild(controls); card.appendChild(result);
  // registering here (not only in placeCards) lets the Wallet panel open this same review sheet
  // for a proposal it just created, before the next poll places the transcript card
  pilotCards[p.proposal_id] = { controls: controls, result: result, resolving: false, opener: review };
  rendered[p.proposal_id] = true;
  return card;
}
function openSheet(p, cardControls, cardResult, opener){
  if (sheetOpen) { sheetOpen.focus(); return; }
  var overlay = el('div', 'vw-sheet-overlay'); overlay.id = 'vwSheet';
  var box = el('div', 'vw-sheet'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-labelledby', 'vwSheetTitle'); box.setAttribute('data-proposal', p.proposal_id);
  var heading = el('div', 'vw-sheet-head');
  var title = el('h3', null, 'Reading the network…'); title.id = 'vwSheetTitle'; heading.appendChild(title);
  var later = el('button', 'vw-btn', 'Decide later'); later.id = 'vwSheetLater'; later.type = 'button';
  later.title = 'Close this preview without approving or cancelling the request';
  heading.appendChild(later); box.appendChild(heading);
  var fields = el('div'); fields.id = 'vwSheetFields'; box.appendChild(fields);
  var state = el('div', 'vw-msg'); state.id = 'vwSheetState'; state.setAttribute('role', 'status'); state.setAttribute('aria-live', 'polite'); box.appendChild(state);
  var credWrap = el('div', 'vw-sheet-actions');
  var cred = el('input', 'vw-pin'); cred.id = 'vwSheetCredential'; cred.type = 'password'; cred.autocomplete = 'off';
  credWrap.appendChild(cred);
  var forgot = forgotLink('', 'Forgot PIN or password?'); forgot.id = 'vwSheetForgot'; forgot.hidden = true; credWrap.appendChild(forgot);
  box.appendChild(credWrap);
  var actionsRow = el('div', 'vw-sheet-actions');
  var refresh = el('button', 'vw-btn', 'Refresh preview'); refresh.id = 'vwSheetRefresh'; refresh.type = 'button';
  var cancel = el('button', 'vw-btn', 'Cancel request'); cancel.id = 'vwSheetCancel'; cancel.type = 'button';
  cancel.setAttribute('aria-label', 'cancel this transfer request; nothing is signed or sent');
  var approve = el('button', 'vw-btn primary', 'Approve and send'); approve.id = 'vwSheetApprove'; approve.type = 'button'; approve.disabled = true;
  actionsRow.appendChild(refresh); actionsRow.appendChild(cancel); actionsRow.appendChild(approve); box.appendChild(actionsRow);
  overlay.appendChild(box); document.body.appendChild(overlay);
  var quote = null, invalid = '', busy = false, closed = false, decisionStarted = false;
  function setState(text, isErr){ state.textContent = text || ''; state.className = 'vw-msg' + (isErr ? ' err' : ''); }
  function expired(){ return !!quote && nowSec() >= Number(quote.fields.expires_at || 0); }
  function gate(){
    if (closed) return;
    later.textContent = decisionStarted ? 'Close' : 'Decide later';
    var blocked = !!quote && !!(quote.fields || {}).approval_unavailable;
    var ok = !!quote && !invalid && !expired() && !busy && !blocked;
    approve.disabled = !ok;
    if (quote && !invalid && expired()) setState('This preview expired. Refresh it to read the current balance and fees before approving.', true);
  }
  var detailsOpen = false;
  function reviewLine(host, name, text, cls){ var line = el('div', 'vw-review-line' + (cls ? ' ' + cls : ''), text); line.setAttribute('data-review', name); host.appendChild(line); return line; }
  function renderReview(f, r){
    // the compact review: purpose, amount, recipient, badge, the DNA fee, any collection riding this approval, the
    // network cost, the per-asset maxima and remaining balances, every warning. Words and figures come from the
    // server's review block; the page adds nothing and sums nothing.
    var box = el('div', 'vw-review'); box.id = 'vwSheetReview';
    reviewLine(box, 'amount', r.amount_line || (f.amount_human + ' ' + f.display_symbol), 'primary');
    // the network row keeps the sheet's field shape (a value span with the badge inside it), visible above the fold
    var net = el('div', 'vw-review-line muted'); net.setAttribute('data-review', 'network'); net.setAttribute('data-field', 'network');
    var netValue = el('span', 'vw-sheet-value', (r.network_name || f.display_name) + ' ');
    netValue.appendChild(el('span', 'vw-badge' + (f.environment === 'mainnet' ? '' : ' testnet'), f.badge));
    net.appendChild(netValue); net.appendChild(document.createTextNode(' · ' + (f.value_note || ''))); box.appendChild(net);
    var to = reviewLine(box, 'recipient', r.recipient_line || ('To ' + f.to_address), r.recipient_kind === 'provider_pay_to' ? 'vw-review-addr' : '');
    if (f.token_transfer && f.recipient_token_account && !('create_recipient_account' in f)) to.appendChild(document.createTextNode(' · Recipient token account ' + f.recipient_token_account));
    reviewLine(box, 'source', r.source_line || '', 'muted');
    if (r.fee_line) reviewLine(box, 'dna_fee', r.fee_line);
    if (r.collection_line) reviewLine(box, 'collection', r.collection_line, f.companion ? 'primary' : 'muted');
    reviewLine(box, 'network_cost', r.network_line || '');
    (r.max_debits || []).forEach(function(d, i){ reviewLine(box, 'max_debit_' + i, 'Maximum wallet debit now: ' + d.human + ' ' + d.asset + ' (' + d.what + ')'); });
    (r.remaining || []).forEach(function(d, i){ reviewLine(box, 'remaining_' + i, 'Estimated remaining after: ' + d.human + ' ' + d.asset + ' (' + d.label + ')', 'muted'); });
    (r.warnings || []).forEach(function(w){ var warn = el('div', 'vw-review-warning', w.text); warn.setAttribute('data-warning', w.code); warn.setAttribute('role', 'alert'); box.appendChild(warn); });
    fields.appendChild(box);
  }
  function renderDetailsToggle(){
    var toggle = el('button', 'vw-details-toggle', detailsOpen ? 'Hide details' : 'View details'); toggle.id = 'vwSheetDetailsToggle'; toggle.type = 'button';
    toggle.setAttribute('aria-expanded', detailsOpen ? 'true' : 'false'); toggle.setAttribute('aria-controls', 'vwSheetDetails');
    var body = el('div', 'vw-details-body'); body.id = 'vwSheetDetails'; body.hidden = !detailsOpen;
    toggle.addEventListener('click', function(){
      // opening or closing the disclosure touches nothing else: not the quote, not the credential, not the request
      detailsOpen = !detailsOpen; body.hidden = !detailsOpen; toggle.textContent = detailsOpen ? 'Hide details' : 'View details'; toggle.setAttribute('aria-expanded', detailsOpen ? 'true' : 'false');
    });
    fields.appendChild(toggle); fields.appendChild(body);
    return body;
  }
  function render(q){
    quote = q; invalid = ''; fields.textContent = '';
    var f = q.fields || {};
    var r = f.review || {};
    title.textContent = r.headline || ('Send ' + f.amount_human + ' ' + f.display_symbol);
    renderReview(f, r);
    if (f.recipient_warning) {
      var reviewBox = document.getElementById('vwSheetReview');
      if (reviewBox) reviewBox.appendChild(el('div', 'vw-sheet-note vw-sheet-warning', f.recipient_warning));
    }
    if (f.mobile_review) {
      var mobileBox = document.getElementById('vwSheetReview');
      reviewLine(mobileBox, 'mobile_origin', WT('wallet.mobile.origin', 'Proposed from your phone or chat app. Approval stays on this computer.'), 'muted');
      (f.mobile_review.warnings || []).forEach(function(code) {
        var text = code === 'new_payee' ? WT('wallet.mobile.new_payee', 'New payee: check the full address with the recipient.') : WT('wallet.mobile.unusual_amount', 'Unusual amount: check this amount before approving.');
        var warning = el('div', 'vw-review-warning', text);
        warning.setAttribute('data-warning', code); warning.setAttribute('role', 'alert'); mobileBox.appendChild(warning);
      });
    }
    var details = renderDetailsToggle();
    sheetRow(details, 'value_note', 'Funds', f.value_note);
    identRow(details, 'from', 'From', f.from_address, {prefix: f.from_label ? f.from_label + ' · ' : ''});
    identRow(details, 'to', 'To', f.to_address, {note: f.recipient_saved ? f.recipient_label : 'unsaved address'});
    if (f.token_transfer) {
      // a token payment spends two assets: the principal in the token, the network fee in the gas asset
      sheetRow(details, 'balance', 'Current balance', f.principal_balance_human + ' ' + f.display_symbol + ' · ' + f.fee_balance_human + ' ' + f.gas_asset + ' for fees · observed at ' + f.balance_ref);
    } else {
      sheetRow(details, 'balance', 'Current balance', f.balance_human + ' ' + f.gas_asset + ' · observed at ' + f.balance_ref);
    }
    sheetRow(details, 'amount', 'Amount', f.amount_human + ' ' + f.display_symbol);
    if (f.token_transfer) sheetRow(details, 'to_token_account', 'Recipient token account', f.recipient_token_account);
    sheetRow(details, 'fee', 'Network fee', 'estimated ' + f.fee_estimate_human + ' ' + f.gas_asset + ' · at most ' + f.fee_max_human + ' ' + f.gas_asset);
    if (f.fee_parts && f.fee_parts.l1_is_estimate) details.appendChild(el('div', 'vw-sheet-note', 'The fee includes an Ethereum data-fee estimate that can move before inclusion; the maximum covers it.'));
    if (f.token_transfer) {
      sheetRow(details, 'max_total', 'Maximum total debit', f.max_total_human + ' ' + f.display_symbol + ' + at most ' + f.fee_max_human + ' ' + f.gas_asset + ' in fees');
      sheetRow(details, 'after', 'Estimated balance after', f.estimated_after_human + ' ' + f.display_symbol + ' after this payment · at least ' + f.fee_balance_after_minimum_human + ' ' + f.gas_asset + ' left for fees');
    } else {
      sheetRow(details, 'max_total', 'Maximum total debit', f.max_total_human + ' ' + f.gas_asset);
      sheetRow(details, 'after', 'Estimated balance after', f.estimated_after_human + ' ' + f.gas_asset + ' (estimate, not guaranteed) · at least ' + f.minimum_after_human + ' ' + f.gas_asset + ' if the fee reaches its maximum');
    }
    if (f.dna_fee) {
      // the native DNA service fee: exact, never a fake zero; its basis is the provider payment, not the network fee
      var d = f.dna_fee;
      var k = d.collection || {};
      sheetRow(details, 'dna_fee', 'DNA service fee (' + (d.rate_label || '0.1%') + ' of the provider payment)', d.fee_exact + ' ' + d.asset + ' exact (' + d.fee_exact_atomic + ' atomic units) · reserved at most ' + d.fee_reserved_ceiling_exact + ' ' + d.asset + ' · owed once the provider accepts the payment');
      if (Number(d.accrued_before_numerator || 0) > 0) sheetRow(details, 'dna_fee_accrued', 'DNA fees accrued so far', d.accrued_before_exact + ' ' + d.asset + ' · after this payment ' + d.accrued_after_exact + ' ' + d.asset);
      sheetRow(details, 'dna_fee_collection', 'Fee collection', k.with_this_payment ? k.amount_exact + ' ' + d.asset + ' of previously accrued fees collected with this payment under this approval (cost bound ' + (Number(k.cost_bound_bps || 0) / 100) + '% of the amount)' : 'none with this payment (' + String(k.reason || 'not planned').replace(/_/g, ' ') + ') · ' + d.collectible_now_exact + ' ' + d.asset + ' collectible, threshold ' + d.collect_min_exact + ' ' + d.asset + ' · collected with a later native payment when economical');
      sheetRow(details, 'dna_fee_treasury', 'DNA treasury', d.treasury_owner + (d.treasury_source === 'operator_override_disposable_recipient' ? ' · OPERATOR OVERRIDE (disposable recipient)' : ' · designated owner'));
      if (f.authorized_exposure_human) sheetRow(details, 'exposure', 'Total authorized exposure', f.authorized_exposure_human + ' ' + f.display_symbol + ' (payment + fee ceiling) + at most ' + f.fee_max_human + ' ' + f.gas_asset + ' network fee');
      var feeNote = el('div', 'vw-sheet-note', d.explanation || ''); feeNote.setAttribute('data-cue', 'dna_fee'); feeNote.setAttribute('role', 'note'); details.appendChild(feeNote);
    }
    if (f.companion) {
      // the collection riding this approval: its own transaction, its own network fee, the same PIN
      var c = f.companion;
      sheetRow(details, 'companion', 'Fee collection bound to this approval', c.collection_id + ' (version ' + c.state_version + ') · ' + c.amount_human + ' ' + c.asset + ' to ' + c.treasury_owner + (c.treasury_token_account ? ' · treasury token account ' + c.treasury_token_account : '') + ' · network fee at most ' + (c.fee_max_minor) + ' atomic ' + c.gas_asset + ' · offer expires ' + (c.expires_at ? new Date(Number(c.expires_at) * 1000).toISOString() : '?'));
      var cNote = el('div', 'vw-sheet-note', (c.purpose || 'fee collection') + '. ' + (c.authorization || '')); cNote.setAttribute('data-cue', 'dna_fee_collection'); cNote.setAttribute('role', 'note'); details.appendChild(cNote);
    }
    (r.details || []).forEach(function(pair, i){ sheetRow(details, 'detail_' + i, pair[0], pair[1]); });
    (f.cues || []).forEach(function(cue){ var note = el('div', 'vw-sheet-note', CUE_TEXT[cue] || cue); note.setAttribute('data-cue', cue); note.setAttribute('role', 'note'); fields.appendChild(note); });
    var method = f.approval_method || 'pin';
    cred.hidden = method === 'device';
    var payer = ((lastStatus && lastStatus.accounts) || []).filter(function(a){ return a.public_key === f.from_address; })[0];
    forgot.hidden = method === 'device' || !payer;
    if (payer) { var link = forgotLink(payer.wallet_id, 'Forgot PIN or password?'); link.id = 'vwSheetForgot'; forgot.replaceWith(link); forgot = link; }
    if (f.signs_on_ledger) forgot.hidden = true;  // a Ledger wallet's PIN is reset by adding the same Ledger again (Settings > Crypto)
    cred.placeholder = method === 'password' ? 'wallet password' : 'wallet PIN';
    cred.setAttribute('aria-label', method === 'password' ? 'wallet password for this approval only' : 'wallet PIN for this approval only');
    if (method === 'pin') cred.inputMode = 'numeric';
    var primary = r.primary_action || 'Approve and send';
    approve.textContent = method === 'device' ? 'Approve with Touch ID / password: ' + primary.replace(/^Approve and /, '') : primary;
    if (f.approval_unavailable) {
      // a connected or watch-only account: the preview is real, but this sheet cannot approve it, so no credential is
      // asked; the server's words say what is really wrong. Cancelling the request still works.
      cred.hidden = true; forgot.hidden = true; approve.hidden = true;
      setState(f.approval_unavailable.message || 'This account cannot send from here. Nothing was signed or sent.', true);
      state.setAttribute('data-approval-unavailable', f.approval_unavailable.code || '');
      gate(); return;
    }
    approve.hidden = false; state.removeAttribute('data-approval-unavailable');
    setState(f.companion ? 'This approval covers exactly the two actions shown: the payment and the collection of previously accrued fees. Nothing else, nothing later.' : 'This approval covers only this transfer, exactly as shown.');
    gate();
  }
  function mint(){
    busy = true; gate(); setState('Reading the network…');
    return postJson('/api/wallet/quote', { proposal_id: p.proposal_id }).then(function(res){
      if (closed) return;
      busy = false;
      if (!res.ok) {
        quote = null; fields.textContent = ''; title.textContent = 'This transfer cannot be approved right now';
        var ctx = (res.data && res.data.fault && res.data.fault.context) || {};
        var text = (res.data && (res.data.message || res.data.error)) || 'The preview could not be prepared.';
        if (ctx.shortfall_human) text += ' Short by ' + ctx.shortfall_human + ' ' + (ctx.gas_asset || '') + '.';
        setState(text, true); gate(); return;
      }
      render(res.data.quote);
    }).catch(function(){ busy = false; setState('The preview request did not complete.', true); gate(); });
  }
  function close(){
    if (closed) return;
    closed = true;
    clearInterval(timer); document.removeEventListener('keydown', onKey, true);
    cred.value = ''; overlay.remove(); sheetOpen = null;
    if (opener && document.body.contains(opener)) opener.focus();
  }
  function dismissPreview(){
    cardResult.textContent = decisionStarted ? 'Check this request for its latest status.' : 'Review this request again whenever you are ready.';
    close();
  }
  later.addEventListener('click', dismissPreview);
  function onKey(ev){
    if (recoveryOpen) return;  // the recovery surface above this preview owns the keys until it closes
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); dismissPreview(); return; }
    if (ev.key !== 'Tab') return;
    var focusables = Array.prototype.filter.call(box.querySelectorAll('button, input'), function(n){ return !n.disabled && !n.hidden; });
    if (!focusables.length) return;
    var first = focusables[0], last = focusables[focusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
    else if (!box.contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
  }
  refresh.addEventListener('click', function(){ mint(); });
  approve.addEventListener('click', function(){
    if (!quote || invalid || expired()) { gate(); return; }
    var f = quote.fields || {};
    var body = { proposal_id: p.proposal_id, quote_id: quote.quote_id, quote_digest: quote.digest };
    if (f.approval_method === 'device') body.method = 'device';
    else if (f.approval_method === 'password') body.password = cred.value;
    else body.pin = cred.value;
    cred.value = '';
    decisionStarted = true; busy = true; gate(); setState(f.approval_method === 'device' ? 'Confirm with Touch ID or your Mac password…' : (f.signs_on_ledger ? 'Now check the transfer on your Ledger and press to approve it (within 25 seconds)…' : 'Checking the approval…'));
    postJson('/api/wallet/approve', body).then(function(res){
      busy = false; body = null;
      if (res.ok) {
        var transfer = (res.data && (res.data.transfer || res.data.receipt)) || {};
        delete pilotCards[p.proposal_id];
        close(); cardControls.remove(); renderTransfer(cardResult, transfer); transferCards[p.proposal_id] = cardResult;
        cardResult.setAttribute('data-transfer-state', transfer.state || ''); markAsked(cardResult, statusAsked);
        var companion = res.data && res.data.companion;
        if (companion && companion.collection_id) {
          var line = el('div', 'vw-transfer-detail', 'DNA fee collection with this payment: ' + companion.amount_exact + ' ' + (companion.asset || '') + ' · ' + (companion.state_label || companion.state) + (companion.tx_signature ? ' · transaction ' + companion.tx_signature : ''));
          line.setAttribute('data-companion-outcome', companion.state || ''); cardResult.appendChild(line);
        }
        // a pay-per-answer AI answer that could not go into the chat log (a private chat, or a save that failed) is shown here
        var paidAi = res.data && res.data.paid_ai;
        if (paidAi && paidAi.text && !paidAi.posted) {
          var answerNode = el('div', 'vw-transfer-detail vw-paid-ai-answer', paidAi.text);
          answerNode.style.whiteSpace = 'pre-wrap'; answerNode.setAttribute('data-paid-ai', paidAi.verdict || '');
          cardResult.appendChild(answerNode);
        }
        return;
      }
      var code = res.data && res.data.error;
      if (code === 'wallet_quote_expired' || code === 'wallet_quote_mismatch') invalid = code;
      setState((res.data && (res.data.message || code)) || 'Not approved.', true);
      gate();
    }).catch(function(){ busy = false; body = null; setState('The approval request did not complete.', true); gate(); });
  });
  cancel.addEventListener('click', function(){
    decisionStarted = true; gate();
    cancel.disabled = true;
    postJson('/api/wallet/reject', { proposal_id: p.proposal_id }).then(function(res){
      close();
      if (res.ok) refreshStatus();  // one authoritative read now, so the open Wallet panel shows the outcome without waiting for the poll
      var data = res.data || {};
      if (res.ok && (data.cancelled === true || data.transfer)) delete pilotCards[p.proposal_id];
      if (res.ok && data.cancelled === true) {
        cardControls.remove();
        var t = data.transfer || null;
        // the copy follows what was true when the request was cancelled: nothing signed, or signed and never sent
        cardResult.textContent = (t && t.state !== 'cancelled') ? 'Cancel requested. Nothing will be sent.' : (t && t.state_label && data.reason !== 'rejected_before_claim' ? 'Cancelled. Nothing was sent.' : 'Cancelled. Nothing was signed or sent.');
        if (t) { transferCards[p.proposal_id] = cardResult; cardResult.setAttribute('data-transfer-state', t.state || ''); markAsked(cardResult, statusAsked); }
      } else if (res.ok && data.transfer) {
        cardControls.remove(); renderTransfer(cardResult, data.transfer); transferCards[p.proposal_id] = cardResult;
        cardResult.setAttribute('data-transfer-state', data.transfer.state || ''); markAsked(cardResult, statusAsked);
      } else cardResult.textContent = (data.message || data.error) || 'The request could not be cancelled.';
    }).catch(function(){ cancel.disabled = false; setState('The cancel request did not complete.', true); });
  });
  document.addEventListener('keydown', onKey, true);
  var timer = setInterval(gate, 1000);
  sheetOpen = {
    proposalId: p.proposal_id,
    focus: function(){ (cred.hidden ? approve : cred).focus(); },
    sync: function(row, snapshot){
      // a snapshot taken before this preview was minted says nothing about it
      if (!quote || invalid || Number((snapshot && snapshot.generated_at_epoch) || 0) < Number(quote.fields.quoted_at || 0)) return;
      if (!row) { invalid = 'gone'; setState('This request is no longer waiting for approval.', true); gate(); return; }
      if (row.open_quote_id !== quote.quote_id) { invalid = 'replaced'; setState('This preview was replaced or withdrawn (a refresh elsewhere or a network environment switch). Refresh it to review the current one.', true); gate(); }
    }
  };
  cred.focus();
  mint();
}
function syncSheet(st){
  if (!sheetOpen) return;
  var row = (st.pending || []).filter(function(x){ return x && x.proposal_id === sheetOpen.proposalId; })[0] || null;
  sheetOpen.sync(row, st);
}
function placeCards(asked){
  var a = actions();
  var host = a && a.chatLog ? a.chatLog() : null;
  if (!host || !lastStatus || !lastStatus.enabled) return;
  (lastStatus.pending || []).forEach(function(p){
    if (!p || !p.proposal_id || rendered[p.proposal_id]) return;
    rendered[p.proposal_id] = true;
    host.appendChild(p.pilot_transfer ? pilotCardFor(p) : cardFor(p));
    host.scrollTop = host.scrollHeight;
  });
  // a transfer still in flight after a reload keeps its card: it is in progress, never "gone"
  (lastStatus.in_flight || []).forEach(function(t){
    if (!t || !t.proposal_id || rendered[t.proposal_id]) return;
    rendered[t.proposal_id] = true;
    host.appendChild(transferCardFor(t)); markAsked(transferCards[t.proposal_id], asked);
    host.scrollTop = host.scrollHeight;
  });
  syncTransfers(lastStatus, asked);
  syncPilotCards(lastStatus, asked);
}
function refreshStatus(){
  var asked = ++statusAsked;
  return getJson('/api/wallet/status').then(function(j){
    var st = (j && j.status) || null;
    if (!st) return null;
    lastStatus = st;
    if (st.enabled) placeCards(asked);
    syncSheet(st);
    walletHome.onStatus(st);
    // the Settings surface follows the switch above it: on, the rows appear; off, they go
    if (cryptoHost && cryptoStatus && !!st.enabled !== !!cryptoStatus.enabled && !cryptoOpenForm && !document.getElementById('vwCxBackup')) { cryptoStatus = st; drawCrypto(st); }
    // the section below follows the same switch: it never keeps saying "off" after Crypto was turned on
    if (statusEl && legacyShownEnabled !== null && !!st.enabled !== legacyShownEnabled) renderStatus().then(function(fresh){ if (legacyEvmSync) legacyEvmSync(fresh); });
    return st;
  }).catch(function(){ return null; });
}
function poll(){ refreshStatus(); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
setTimeout(poll, 400);
setInterval(poll, 2500);

// ---- the Wallet home: one entry in Home, one panel, one selection ------------------------------
// A read-and-propose surface over the SAME doors Settings uses: /api/wallet/status (accounts, rows,
// capabilities), /api/wallet/balance (the typed read), /api/wallet/transfers (activity) and
// /api/wallet/propose feeding the existing review sheet. The enabled state has one owner — the
// backend preference — so the entry appears and disappears with it and the panel never keeps a
// second "crypto on" flag. Closing the panel mutates nothing: no proposal is approved, cancelled
// or extended by a close.
var walletHome = (function(){
  var overlay = null, panel = null, bodyEl = null, msgEl = null, lastFocus = null;
  var isOpen = false, epoch = 0, view = 'home';
  var selWalletId = '', selNetwork = '';
  var lastBalanceMinor = {};      // wallet_id -> {minor, human} of the last READ balance; the rise notice compares against it
  var seenTransferState = {};     // proposal_id -> state at the previous snapshot; the first snapshot is a baseline, not mail
  var openDetails = {};           // proposal_id -> details <details> were open (a live repaint must not fold them)
  var sendKey = '';               // one idempotency key per send intent; regenerated after a proposal exists

  function say(text, isErr){ if (msgEl) { msgEl.textContent = text || ''; msgEl.className = 'vw-wh-msg' + (isErr ? ' err' : ''); } }
  function toastWallet(text){ var a = actions(); if (a && a.toast) a.toast(text); }
  function nowClock(){ try { return new Date().toLocaleTimeString(); } catch (e) { return ''; } }
  function timeClock(when){
    // the wallet's read models stamp rows in ISO-8601 text (proposals, transfers, receipts) and observations in epoch
    // seconds (balances); an unreadable stamp shows nothing rather than "Invalid Date"
    if (when === null || when === undefined || String(when).trim() === '') return '';
    var n = Number(when);
    var d = isFinite(n) ? new Date(n * 1000) : new Date(String(when));
    return isNaN(d.getTime()) ? '' : d.toLocaleString();
  }
  function rowsOf(st){ return ((st && st.crypto_pilot && st.crypto_pilot.networks) || []); }
  function activeRows(st){ return rowsOf(st).filter(function(r){ return r.active; }); }
  function rowFor(st, network){ return rowsOf(st).filter(function(r){ return r.network === network; })[0] || null; }
  function accountsOf(st){ return (st && st.accounts) || []; }
  function accountFor(st, walletId){ return accountsOf(st).filter(function(a){ return a.wallet_id === walletId; })[0] || null; }
  function selectedAccount(st){ return selWalletId ? accountFor(st, selWalletId) : null; }
  function rowAccounts(st, network){ return accountsOf(st).filter(function(a){ return a.network === network; }); }
  function cssEscape(v){ return (window.CSS && typeof CSS.escape === 'function') ? CSS.escape(String(v)) : String(v); }

  // decimal string -> minor units, EXACT (BigInt; never floats, never rounded). More fraction digits
  // than the asset has is refused, not silently truncated; a nonzero amount cannot parse to zero.
  function parseAmountToMinor(text, decimals){
    var s = String(text || '').trim().replace(',', '.');
    if (!/^\d+(\.\d+)?$/.test(s)) return null;
    var parts = s.split('.');
    var frac = parts[1] || '';
    if (frac.length > decimals) return null;
    var digits = parts[0] + frac;
    var pad = decimals - frac.length;
    for (var i = 0; i < pad; i++) digits += '0';
    try {
      var minor = BigInt(digits);
      return minor <= 0n ? null : minor;
    } catch (e) { return null; }
  }
  // BigInt minor -> a JSON number ONLY when the round trip is exact; an amount this seam cannot carry
  // exactly is refused with its reason, never rounded (a rounded maximum or amount would be a lie).
  function minorForJson(minor){
    var n = Number(minor);
    try { if (BigInt(n) === minor) return n; } catch (e) {}
    return null;
  }
  function minorToInput(minorStr, decimals){
    // exact string shift for form display; never through a float
    var s = String(minorStr || '').replace(/^0+(?=\d)/, '');
    if (!s) return '0';
    if (decimals <= 0) return s;
    while (s.length <= decimals) s = '0' + s;
    var cut = s.length - decimals;
    var out = s.slice(0, cut) + '.' + s.slice(cut);
    out = out.replace(/0+$/, '');
    if (out.charAt(out.length - 1) === '.') out = out.slice(0, -1);
    return out || '0';
  }

  // ---- the Home entry: created and removed by the ONE enabled state the poll already reads ------
  function syncEntry(st){
    var host = document.getElementById('homeOptions');
    if (!host) return;  // the Settings page carries no Home menu; its wallet surface stays Settings → Crypto
    var btn = document.getElementById('vwHomeBtn');
    if (st.enabled && !btn) {
      btn = el('button', 'side-foot-btn vi'); btn.type = 'button'; btn.id = 'vwHomeBtn'; btn.dataset.ico = 'wallet';
      // The same line icon as every other Home row (core/ui_icons.py), never a colour emoji; the label
      // goes through the page's catalog like every other Home row, so it follows the app language.
      var tr = function(key, fallback){ return (typeof pageT === 'function') ? pageT(key, fallback) : fallback; };
      btn.title = tr('home.wallet.title', 'VOOL Wallet — accounts, balance, receive, send and activity');
      btn.appendChild(document.createTextNode(tr('home.wallet', 'Wallet')));
      btn.addEventListener('click', function(){ openPanel(); });
      var anchor = document.getElementById('settingsBtn') || host.querySelector('.home-support');
      if (anchor && anchor.parentNode === host) host.insertBefore(btn, anchor); else host.appendChild(btn);
    } else if (!st.enabled && btn) {
      btn.remove();
    }
  }

  // ---- payment-landed notices: derived from the poll the page already runs; no second loop -------
  // A transfer the owner made reaching Confirmed (or ending Failed) is said once, when it changes.
  // The first snapshot after a load baselines history instead of replaying it as new mail.
  function noticeTransfers(st){
    (st.transfers || []).concat(st.in_flight || []).forEach(function(t){
      if (!t || !t.proposal_id) return;
      var prev = seenTransferState[t.proposal_id];
      if (prev === undefined) { seenTransferState[t.proposal_id] = t.state; return; }
      if (prev === t.state) return;
      seenTransferState[t.proposal_id] = t.state;
      var what = { amount: t.amount_human, symbol: t.display_symbol, network: (t.display_name || chainLabel(t.network)) };
      if (t.state === 'confirmed') toastWallet(WTF('wallet.home.toast_confirmed', 'VOOL Wallet: payment confirmed — {amount} {symbol} on {network}', what));
      else if (t.state === 'failed') toastWallet(WTF('wallet.home.toast_failed', 'VOOL Wallet: payment failed — {amount} {symbol} on {network}; nothing was sent', what));
    });
  }
  function noticeBalanceRise(walletId, b){
    if (b.state !== 'read') return;
    var prev = lastBalanceMinor[walletId];
    lastBalanceMinor[walletId] = { minor: String(b.balance_minor || ''), human: b.balance_human };
    if (!prev || !prev.minor) return;
    try {
      if (BigInt(String(b.balance_minor || '0')) > BigInt(prev.minor)) {
        var line = WTF('wallet.home.balance_rose', 'Received — the balance rose from {before} to {after} {asset} between looks. Noticed on this refresh; VOOL does not watch the chain in the background.',
          { before: prev.human, after: b.balance_human, asset: b.asset });
        toastWallet(WTF('wallet.home.toast', 'VOOL Wallet: {text}', { text: line }));
        say(line);
      }
    } catch (e) { /* a non-numeric observation is not a payment claim */ }
  }

  // ---- panel shell ------------------------------------------------------------------------------
  function clearSecrets(){ if (overlay) Array.prototype.forEach.call(overlay.querySelectorAll('input[type=password]'), function(i){ i.value = ''; }); }
  function closePanel(byDisable){
    if (!isOpen) return;
    isOpen = false; view = 'home';
    clearSecrets();
    if (overlay) overlay.remove();
    overlay = panel = bodyEl = msgEl = null;
    if (byDisable) toastWallet(WT('wallet.home.off_toast', 'VOOL Wallet is off. Wallets, balances and history are kept.'));
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
    lastFocus = null;
  }
  function buildPanel(){
    overlay = el('div'); overlay.id = 'vwHomeOverlay';
    panel = el('div', 'vw-wh'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-labelledby', 'vwHomeTitle');
    var head = el('div', 'vw-wh-head');
    var title = el('span', 'vw-wh-title', WT('wallet.home.title', 'VOOL Wallet')); title.id = 'vwHomeTitle'; head.appendChild(title);
    var envBadge = el('span', 'vw-badge mode'); envBadge.id = 'vwHomeEnv'; envBadge.hidden = true; head.appendChild(envBadge);
    var manage = el('button', 'vw-btn', WT('wallet.home.manage', 'Manage')); manage.type = 'button'; manage.id = 'vwHomeManage';
    manage.title = WT('wallet.home.manage_title', 'Settings → Crypto: enable/disable, setup, spending caps, environment');
    manage.addEventListener('click', function(){ closePanel(false); var a = actions(); if (a && a.openSettings) a.openSettings(); else window.location.href = '/settings#wallet'; });
    head.appendChild(manage);
    var x = el('button', 'vw-btn', WT('wallet.home.close', 'Close')); x.type = 'button'; x.id = 'vwHomeClose'; x.setAttribute('aria-label', WT('wallet.home.close_aria', 'close the wallet panel'));
    x.addEventListener('click', function(){ closePanel(false); });
    head.appendChild(x);
    panel.appendChild(head);
    msgEl = el('div', 'vw-wh-msg'); msgEl.id = 'vwHomeMsg'; msgEl.setAttribute('role', 'status'); msgEl.setAttribute('aria-live', 'polite');
    panel.appendChild(msgEl);
    bodyEl = el('div', 'vw-wh-body'); bodyEl.id = 'vwHomeBody'; panel.appendChild(bodyEl);
    overlay.appendChild(panel);
    overlay.addEventListener('mousedown', function(ev){ if (ev.target === overlay) closePanel(false); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(overlay);
  }
  function onKey(ev){
    if (!isOpen) return;
    if (sheetOpen || recoveryOpen) return;  // the review sheet or the recovery dialog above owns the keys
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); closePanel(false); return; }
    if (ev.key !== 'Tab') return;
    var focusables = Array.prototype.filter.call(panel.querySelectorAll('button, input, select'), function(n){ return !n.disabled && !n.hidden && n.offsetParent !== null; });
    if (!focusables.length) return;
    var first = focusables[0], last = focusables[focusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  }
  function openPanel(){
    if (isOpen) { return; }
    lastFocus = document.activeElement;
    isOpen = true; view = 'home'; epoch += 1;
    buildPanel();
    renderFromStatus();
  }
  function renderFromStatus(){
    var myEpoch = epoch;
    return getJson('/api/wallet/status').then(function(j){
      if (!isOpen || myEpoch !== epoch) return null;
      var st = (j && j.status) || {};
      lastStatus = st;
      normalizeSelection(st);
      draw(st);
      return st;
    }).catch(function(){ if (isOpen) { bodyEl.textContent = ''; bodyEl.appendChild(el('div', 'vw-wh-empty', WT('wallet.home.status_unreadable', 'The wallet status could not be read. Try again from Home.'))); } return null; });
  }

  // ---- the selection: one account on one network row ----------------------------------------------
  function normalizeSelection(st){
    var accounts = accountsOf(st);
    if (selWalletId) {
      var kept = accountFor(st, selWalletId);
      if (kept) { selNetwork = kept.network; return; }
    }
    selWalletId = '';
    var ready = accounts.filter(function(a){ return a.setup_state === 'ready'; })[0] || accounts[0] || null;
    if (ready) { selWalletId = ready.wallet_id; selNetwork = ready.network; return; }
    var actives = activeRows(st);
    var withCreate = actives.filter(function(r){ return r.capabilities && r.capabilities.create && r.capabilities.create.available; })[0];
    selNetwork = (withCreate || actives[0] || {}).network || '';
  }

  function draw(st){
    if (view === 'receive') drawReceive(st);
    else if (view === 'send') drawSend(st);
    else if (view === 'setup') drawSetup(st);
    else drawHome(st);
  }

  function drawHome(st){
    view = 'home';
    var cp = st.crypto_pilot || {};
    var envBadge = document.getElementById('vwHomeEnv');
    if (envBadge) { envBadge.hidden = !cp.environment_label; envBadge.textContent = (cp.environment_label || '') + (cp.frozen ? ' · ' + WT('wallet.home.frozen', 'payments frozen') : ''); }
    bodyEl.textContent = '';
    var accounts = accountsOf(st);
    if (!accounts.length) { drawSetup(st); return; }

    // account + network selectors
    var selects = el('div', 'vw-wh-selects');
    selects.appendChild(el('label', null, WT('wallet.home.account', 'Account'))).setAttribute('for', 'vwHomeAccount');
    var accountSel = el('select'); accountSel.id = 'vwHomeAccount'; accountSel.setAttribute('aria-label', WT('wallet.home.account_aria', 'wallet account'));
    var order = cp.chain_order || [];
    function byOrder(a, b){ return order.indexOf(a.chain_key) - order.indexOf(b.chain_key); }
    activeRows(st).sort(byOrder).forEach(function(r){
      var group = el('optgroup'); group.label = rowName(r);
      rowAccounts(st, r.network).forEach(function(a){
        var opt = el('option'); opt.value = a.wallet_id;
        opt.textContent = ((a.label ? a.label + ' · ' : '') + short(a.public_key) + ' · ' + (a.mode === 'watch_only' ? WT('wallet.home.mode_watch_only', 'watch-only') : (a.mode === 'external_signer' ? WT('wallet.home.mode_external', 'external') : WT('wallet.home.title', 'VOOL Wallet'))));
        if (a.wallet_id === selWalletId) opt.selected = true;
        group.appendChild(opt);
      });
      if (group.children.length) accountSel.appendChild(group);
    });
    var otherRows = rowsOf(st).filter(function(r){ return !r.active && rowAccounts(st, r.network).length; });
    if (otherRows.length) {
      var other = el('optgroup'); other.label = WT('wallet.home.other_env', 'Other network environment (read-only here)');
      otherRows.forEach(function(r){
        rowAccounts(st, r.network).forEach(function(a){
          var opt = el('option'); opt.value = a.wallet_id;
          opt.textContent = rowName(r) + ' · ' + short(a.public_key);
          if (a.wallet_id === selWalletId) opt.selected = true;
          other.appendChild(opt);
        });
      });
      accountSel.appendChild(other);
    }
    accountSel.addEventListener('change', function(){
      var a = accountFor(st, accountSel.value);
      selWalletId = a ? a.wallet_id : '';
      selNetwork = a ? a.network : selNetwork;
      epoch += 1; drawHome(st);
    });
    selects.appendChild(accountSel);
    selects.appendChild(el('label', null, WT('wallet.home.network', 'Network'))).setAttribute('for', 'vwHomeNetwork');
    var networkSel = el('select'); networkSel.id = 'vwHomeNetwork'; networkSel.setAttribute('aria-label', WT('wallet.home.network_aria', 'network of the selected account'));
    activeRows(st).sort(byOrder).forEach(function(r){
      var opt = el('option'); opt.value = r.network; opt.textContent = rowName(r) + (rowAccounts(st, r.network).length ? '' : ' — ' + WT('wallet.home.no_account_yet', 'no account yet'));
      if (r.network === selNetwork) opt.selected = true;
      networkSel.appendChild(opt);
    });
    networkSel.addEventListener('change', function(){
      // choosing a network NEVER creates an account, imports a key or enables a chain: it selects the row
      selNetwork = networkSel.value;
      var sameRow = rowAccounts(st, selNetwork).filter(function(a){ return a.setup_state === 'ready'; })[0] || rowAccounts(st, selNetwork)[0];
      selWalletId = sameRow ? sameRow.wallet_id : '';
      epoch += 1; drawHome(st);
    });
    selects.appendChild(networkSel);
    bodyEl.appendChild(selects);

    var account = selectedAccount(st);
    var row = rowFor(st, selNetwork);

    // balance block
    var balBox = el('div', 'vw-wh-balance'); balBox.id = 'vwHomeBalance';
    bodyEl.appendChild(balBox);
    if (!account) paintNoAccount(st);
    else paintBalanceLoading(account, row);
    var actionsRow = el('div', 'vw-wh-actions');
    var receive = el('button', 'vw-btn', WT('wallet.home.receive', 'Receive')); receive.type = 'button'; receive.id = 'vwHomeReceive';
    receive.disabled = !account;
    actionsRow.appendChild(receive);
    var send = el('button', 'vw-btn primary', WT('wallet.home.send', 'Send')); send.type = 'button'; send.id = 'vwHomeSend';
    var caps = (row && row.capabilities) || {};
    var sendReason = '';
    if (!account) sendReason = CAP_REASONS.no_account_on_this_network;
    else if (account.mode === 'watch_only') sendReason = WT('wallet.home.send_reason_watch_only', 'watch-only accounts receive and show their balance; they never send from here');
    else if (account.mode === 'external_signer' && row && row.environment === 'mainnet') sendReason = WT('wallet.home.send_reason_connected', 'a connected wallet receives and shows its balance here; on this network only a VOOL Wallet created on this device sends');
    else if (account.setup_state && account.setup_state !== 'ready') sendReason = WT('wallet.home.send_reason_setup', 'finish this wallet\'s setup before sending');
    else if (!(caps.send && caps.send.available)) sendReason = CAP_REASONS[(caps.send || {}).reason] || (caps.send || {}).reason || WT('wallet.home.unavailable', 'unavailable');
    send.disabled = !!sendReason;
    if (sendReason) { send.title = WTF('wallet.home.send_title', 'Send: {reason}', { reason: sendReason }); send.setAttribute('aria-label', WTF('wallet.home.send_aria', 'Send — {reason}', { reason: sendReason })); }
    actionsRow.appendChild(send);
    var manageSmall = el('button', 'vw-btn', WT('wallet.home.settings_manage', 'Settings / Manage')); manageSmall.type = 'button';
    manageSmall.addEventListener('click', function(){ closePanel(false); var a = actions(); if (a && a.openSettings) a.openSettings(); });
    actionsRow.appendChild(manageSmall);
    bodyEl.appendChild(actionsRow);
    var addRow = el('div');
    var add = el('button', 'vw-toggle', WT('wallet.home.add_wallet', 'Add, restore or connect a wallet')); add.type = 'button'; add.id = 'vwHomeAddWallet';
    add.title = WT('wallet.home.add_wallet_title', 'The supported choices only: create on a network that offers it, restore from a phrase, connect an external wallet, or add a watch-only address');
    add.addEventListener('click', function(){ view = 'setup'; drawSetup(st); });
    addRow.appendChild(add);
    bodyEl.appendChild(addRow);
    receive.addEventListener('click', function(){ view = 'receive'; drawReceive(st); });
    send.addEventListener('click', function(){ if (!send.disabled) { view = 'send'; sendKey = newSendKey(); drawSend(st); } });

    // recent activity
    bodyEl.appendChild(el('div', 'vw-wh-sec', WT('wallet.home.activity', 'Activity')));
    var activity = el('div'); activity.id = 'vwHomeActivity';
    bodyEl.appendChild(activity);
    paintActivity(st, activity);

    var notes = el('div', 'vw-wh-note');
    notes.textContent = (cp.pilot_notice || '') + ' ' + WT('wallet.home.activity_note', 'Payments made through VOOL are noticed here and as toasts while VOOL is open. A rise in the network-coin balance is noticed when this view refreshes; VOOL keeps no full history of incoming transactions and does not watch the chain in the background.');
    bodyEl.appendChild(notes);
    if (account) loadBalance(st);
  }

  function paintNoAccount(st){
    var box = document.getElementById('vwHomeBalance');
    if (!box) return;
    box.textContent = '';
    var row = rowFor(st, selNetwork);
    box.appendChild(el('div', 'vw-wh-amount', WTF('wallet.home.no_account_on', 'No account on {network}', { network: (row ? rowName(row) : chainLabel(selNetwork)) })));
    var line = el('div', 'vw-wh-balstate', WT('wallet.home.select_never_creates', 'Selecting a network never creates an account.') + ' ');
    var setup = el('button', 'vw-toggle', WT('wallet.home.set_one_up', 'Set one up')); setup.type = 'button';
    setup.addEventListener('click', function(){ view = 'setup'; drawSetup(st); });
    line.appendChild(setup); box.appendChild(line);
  }
  function paintBalanceLoading(account, row){
    var box = document.getElementById('vwHomeBalance');
    if (!box) return;
    box.textContent = '';
    var amount = el('div', 'vw-wh-amount', WT('wallet.home.reading_balance', 'Reading the balance…'));
    amount.appendChild(el('span', 'vw-wh-unit', (row && row.native_display_symbol) || ''));
    box.appendChild(amount);
    box.appendChild(el('div', 'vw-wh-balstate', account ? ((account.label ? account.label + ' · ' : '') + (row ? rowName(row) : chainLabel(account.network))) : ''));
  }
  // the typed balance read: read (with zero said as zero), or unavailable with its reason — never a fake $0
  function paintBalance(b, account, row){
    var box = document.getElementById('vwHomeBalance');
    if (!box) return;
    box.textContent = '';
    var amount = el('div', 'vw-wh-amount');
    var stateLine = el('div', 'vw-wh-balstate');
    var sub = (account.label ? account.label + ' · ' : '') + (row ? rowName(row) : chainLabel(account.network));
    if (b.state === 'read') {
      amount.appendChild(document.createTextNode(b.balance_human + ' '));
      amount.appendChild(el('span', 'vw-wh-unit', b.asset));
      var isZero = String(b.balance_minor || '0') === '0';
      stateLine.textContent = (isZero ? WT('wallet.home.balance_zero', 'Balance 0 — confirmed zero as observed') : WTF('wallet.home.observed', 'Observed {ref} · {when}', { ref: b.ref || '', when: timeClock(b.observed_at) })) +
        ' · ' + WT('wallet.home.native_only', 'network coin only; token balances are not listed here');
      var refresh = el('button', 'vw-toggle', WT('wallet.home.refresh', 'refresh')); refresh.type = 'button'; refresh.style.marginLeft = '6px';
      refresh.addEventListener('click', function(){ loadBalance(lastStatus); });
      stateLine.appendChild(refresh);
    } else {
      amount.textContent = WT('wallet.home.balance_unavailable', 'Balance unavailable');
      amount.appendChild(el('span', 'vw-wh-unit', (row && row.native_display_symbol) || ''));
      stateLine.className = 'vw-wh-balstate unavailable';
      stateLine.textContent = WTF('wallet.home.balance_unreadable', 'The balance could not be read ({reason}) — it is not zero.', { reason: (CAP_REASONS[b.reason] || b.reason || WT('wallet.home.not_read', 'not read')) });
      var retry = el('button', 'vw-toggle', WT('wallet.home.try_again', 'try again')); retry.type = 'button'; retry.style.marginLeft = '6px';
      retry.addEventListener('click', function(){ loadBalance(lastStatus); });
      stateLine.appendChild(retry);
    }
    box.appendChild(amount); box.appendChild(stateLine);
    var viewOnly = !!row && row.environment === 'mainnet';
    var kind = account.mode === 'watch_only' ? (viewOnly ? WT('wallet.home.kind_watch_view', 'watch-only: receive and view only') : WT('wallet.home.kind_watch', 'watch-only: observe and propose; signing happens elsewhere')) :
      (account.mode === 'external_signer' ? (viewOnly ? WT('wallet.home.kind_connected_view', 'connected wallet: receive and view only in VOOL') : WT('wallet.home.kind_external', 'external wallet: signing stays in your wallet')) :
      (account.setup_state && account.setup_state !== 'ready' ? WTF('wallet.home.kind_setup', 'setup not finished: {state}', { state: (SETUP_TEXT[account.setup_state] || account.setup_state) }) : WT('wallet.home.kind_vool', 'VOOL Wallet: key sealed on this device')));
    box.appendChild(el('div', 'vw-wh-balstate', kind));
  }
  // one in-flight balance read per selection: a slower older answer can never repaint a newer selection
  function loadBalance(st){
    var account = st ? selectedAccount(st) : null;
    if (!account) return;
    var myEpoch = epoch;
    var row = rowFor(st, account.network);
    var caps = (row && row.capabilities) || {};
    if (!(caps.balance && caps.balance.available)) {
      paintBalance({ state: 'unavailable', reason: (caps.balance || {}).reason || 'environment_inactive' }, account, row);
      return;
    }
    getJson('/api/wallet/balance?wallet_id=' + encodeURIComponent(account.wallet_id)).then(function(j){
      if (!isOpen || myEpoch !== epoch) return;
      var b = (j && j.balance) || { state: 'unavailable', reason: 'not_read' };
      if (b.wallet_id && b.wallet_id !== account.wallet_id) return;  // an answer for another account is not this one
      paintBalance(b, account, row);
      noticeBalanceRise(account.wallet_id, b);
    }).catch(function(){
      if (!isOpen || myEpoch !== epoch) return;
      paintBalance({ state: 'unavailable', reason: 'request_failed' }, account, row);
    });
  }

  // ---- activity: pending decisions first, then the transfer rows, one authority (/api/wallet/transfers) ----
  function stateChip(t){
    var s = t.state === 'pending_approval' || t.state === 'awaiting_signature' ? 'pending' : t.state;
    return el('span', 'vw-wh-state ' + s, t.state_label || t.state || '');
  }
  function transferDetailsBox(t){
    var d = el('details');
    d.appendChild(el('summary', null, WT('wallet.home.view_details', 'View details')));
    var line = function(label, value){
      var r = el('div', 'vw-sheet-row'); r.appendChild(el('span', 'vw-sheet-label', label));
      var cell = el('span', 'vw-sheet-value');
      if (value !== null && value !== undefined) { var idn = el('span', 'vw-ident', String(value)); cell.appendChild(idn); cell.appendChild(copyButton(String(value), label.toLowerCase())); }
      r.appendChild(cell); d.appendChild(r);
    };
    line(WT('wallet.home.detail_to', 'To'), t.to_address);
    line(WT('wallet.home.detail_from', 'From'), t.from_address);
    line(WT('wallet.home.detail_amount', 'Amount (exact)'), t.amount_human + ' ' + t.display_symbol);
    if (t.charged_fee_label) line(WT('wallet.home.detail_fee', 'Fee'), t.charged_fee_label);
    else if (t.fee_max_display) line(WT('wallet.home.detail_fee_max', 'Fee (at most)'), t.fee_max_display);
    if (t.balance_after_human && t.state === 'confirmed') line(WT('wallet.home.detail_balance_after', 'Balance after'), t.balance_after_human + ' ' + t.display_symbol);
    if (t.tx_id) line(WT('wallet.home.detail_tx', 'Transaction'), t.tx_id);
    if (t.detail) d.appendChild(el('div', 'vw-transfer-detail', t.detail));
    if (t.purpose && t.purpose.kind && t.purpose.kind !== 'unknown') d.appendChild(purposeBlock(t.purpose, ''));
    else if (t.origin === 'dna_fee') d.appendChild(el('div', 'vw-transfer-detail', WT('wallet.home.dna_fee_collection', 'DNA service fee collection')));
    if (t.explorer_url) { var link = el('a', 'vw-explorer', t.explorer_link_text || WT('wallet.home.explorer', 'View on the explorer')); link.href = t.explorer_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; d.appendChild(link); }
    return d;
  }
  function activityRowFor(t, isPending){
    var rowEl = el('div', 'vw-wh-row'); rowEl.setAttribute('data-pid', t.proposal_id || '');
    var head = el('div', 'vw-wh-rowhead');
    head.appendChild(stateChip(t));
    var sym = (t.display_symbol || t.asset || '');
    head.appendChild(el('span', 'vw-wh-amt', t.amount_human ? (t.amount_human + ' ' + sym) : WTF('wallet.home.minor_units_of', '{minor} smallest units of {asset}', { minor: (t.amount_minor || 0), asset: sym })));
    head.appendChild(el('span', null, (t.display_name || chainLabel(t.network)) + (t.to_address ? ' → ' + short(t.to_address) : '')));
    head.appendChild(el('span', 'vw-wh-when', timeClock(t.created_at || t.updated_at)));
    rowEl.appendChild(head);
    if (isPending) {
      // a payment its account cannot send (a connected or watch-only account on a Mainnet row) is reviewed, never approved
      var viewOnlyRow = ((lastStatus && lastStatus.pending) || []).some(function(x){ return x && x.proposal_id === t.proposal_id && x.send_unavailable; });
      var review = el('button', 'vw-btn vw-wh-review', t.pilot_transfer === false ? (viewOnlyRow ? WT('wallet.home.review_in_chat', 'Review in chat') : WT('wallet.home.approve_in_chat', 'Approve in chat')) : WT('wallet.home.review', 'Review')); review.type = 'button';
      review.addEventListener('click', function(){ openReviewFor(t.proposal_id); });
      rowEl.appendChild(review);
    } else {
      var d = transferDetailsBox(t);
      if (openDetails[t.proposal_id]) d.open = true;
      d.addEventListener('toggle', function(){ openDetails[t.proposal_id] = d.open; });
      rowEl.appendChild(d);
    }
    return rowEl;
  }
  function paintActivity(st, host){
    host.textContent = '';
    var pending = (st.pending || []).slice();
    var seen = {};
    var inFlight = (st.in_flight || []).filter(function(t){ return t.proposal_id && !seen[t.proposal_id] && (seen[t.proposal_id] = true); });
    var transfers = (st.transfers || []).filter(function(t){ return t.proposal_id && !seen[t.proposal_id] && (seen[t.proposal_id] = true); });
    if (!pending.length && !inFlight.length && !transfers.length) {
      host.appendChild(el('div', 'vw-wh-empty', WT('wallet.home.no_payments', 'No payments yet. Pending approvals, in-flight transfers and receipts appear here.')));
      return;
    }
    pending.forEach(function(t){ host.appendChild(activityRowFor(t, true)); });
    inFlight.forEach(function(t){ host.appendChild(activityRowFor(t, false)); });
    transfers.slice(0, 12).forEach(function(t){ host.appendChild(activityRowFor(t, false)); });
  }

  // ---- receive -----------------------------------------------------------------------------------
  function drawReceive(st){
    view = 'receive';
    var account = selectedAccount(st);
    if (!account) { drawHome(st); return; }
    var row = rowFor(st, account.network);
    bodyEl.textContent = '';
    var back = el('button', 'vw-toggle', WT('wallet.home.back', '← Back to the wallet')); back.type = 'button';
    back.addEventListener('click', function(){ view = 'home'; drawHome(st); });
    bodyEl.appendChild(back);
    bodyEl.appendChild(el('div', 'vw-wh-sec', WT('wallet.home.receive', 'Receive')));
    var line = el('div', 'vw-wh-note');
    line.textContent = WTF('wallet.home.receive_line', 'Receive {symbol} on {network} ({badge}). Send only this network\'s native coin to this address.',
      { symbol: ((row && row.native_display_symbol) || ''), network: (row ? rowName(row) : chainLabel(account.network)), badge: (row && row.badge ? row.badge + ' · ' + (row.value_note || '') : '') });
    bodyEl.appendChild(line);
    var addrBox = el('div');
    addrBox.appendChild(el('div', 'vw-wh-sec', WT('wallet.home.your_address', 'Your address')));
    var addr = el('div', 'vw-wh-addr', account.public_key); addr.id = 'vwHomeAddress';
    addrBox.appendChild(addr);
    var copyRow = el('div');
    var copy = copyButton(account.public_key, WT('wallet.home.copy_what_address', 'address')); copy.id = 'vwHomeCopyAddress'; copy.className = 'vw-btn';
    copyRow.appendChild(copy);
    addrBox.appendChild(copyRow);
    bodyEl.appendChild(addrBox);
    var qr = qrCanvas(account.public_key);
    if (qr) { var box = el('div', 'vw-qr'); box.id = 'vwHomeQr'; box.appendChild(qr); bodyEl.appendChild(box); }
    else bodyEl.appendChild(el('div', 'vw-wh-note', WT('wallet.home.no_qr', 'No QR here: the offline QR generator is unavailable on this build. Copy the address instead.')));
    bodyEl.appendChild(el('div', 'vw-wh-note', account.mode === 'watch_only'
      ? WT('wallet.home.receive_watch_note', 'Watch-only address: funds sent here are controlled by the wallet that owns it, not by VOOL. VOOL can observe and propose, never sign.')
      : (account.mode === 'external_signer'
        ? WT('wallet.home.receive_external_note', 'External wallet: funds are controlled by your connected wallet. VOOL never holds this key.')
        : WT('wallet.home.receive_vool_note', 'VOOL Wallet: the key is sealed on this device and every payment asks for your approval.'))));
  }
  // a locally generated QR of exactly the displayed destination (vendored generator, no external service)
  function qrCanvas(text){
    if (typeof qrcode !== 'function') return null;
    try {
      var qr = qrcode(0, 'M');
      qr.addData(String(text));
      qr.make();
      var n = qr.getModuleCount(), px = 6, quiet = 4;
      var canvas = el('canvas');
      canvas.width = canvas.height = (n + quiet * 2) * px;
      var ctx = canvas.getContext('2d');
      if (!ctx) return null;
      ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#111318';
      for (var r = 0; r < n; r++) for (var c = 0; c < n; c++) if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * px, (r + quiet) * px, px, px);
      canvas.setAttribute('role', 'img');
      canvas.setAttribute('aria-label', WT('wallet.home.qr_aria', 'QR code of the receiving address'));
      return canvas;
    } catch (e) { return null; }
  }

  // ---- send: a proposal through the owner's door, then the SAME review sheet as every payment ----
  function newSendKey(){ return 'wh-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10); }
  function drawSend(st){
    view = 'send';
    var account = selectedAccount(st);
    if (!account || account.mode === 'watch_only' || (account.setup_state && account.setup_state !== 'ready')) { drawHome(st); return; }
    var row = rowFor(st, account.network);
    var caps = (row && row.capabilities) || {};
    bodyEl.textContent = '';
    var back = el('button', 'vw-toggle', WT('wallet.home.back', '← Back to the wallet')); back.type = 'button';
    back.addEventListener('click', function(){ view = 'home'; drawHome(st); });
    bodyEl.appendChild(back);
    bodyEl.appendChild(el('div', 'vw-wh-sec', WT('wallet.home.send', 'Send')));
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeSendForm';
    var netLine = el('div', 'vw-wh-note');
    netLine.textContent = WTF('wallet.home.send_from', 'From {account} on {network}', { account: (account.label ? account.label + ' · ' : '') + short(account.public_key), network: (row ? rowName(row) : chainLabel(account.network)) });
    form.appendChild(netLine);
    var toLabel = el('label', null, WT('wallet.home.recipient', 'Recipient address')); toLabel.setAttribute('for', 'vwHomeSendTo'); form.appendChild(toLabel);
    var to = el('input'); to.type = 'text'; to.id = 'vwHomeSendTo'; to.autocomplete = 'off'; to.spellcheck = false;
    to.setAttribute('aria-label', WTF('wallet.home.recipient_aria', 'recipient address on {network}', { network: (row ? rowName(row) : selNetwork) }));
    form.appendChild(to);
    var contactNote = el('div', 'vw-wh-sub'); form.appendChild(contactNote);
    // the note speaks only for the address it filled: any other text in the field (a typed name, a pasted address,
    // a cleared field) drops it, so an old wrong-network warning never sits beside a different recipient
    var filledFrom = null;
    to.addEventListener('input', function(){ if (filledFrom === null || to.value !== filledFrom) { filledFrom = null; contactNote.textContent = ''; } });
    var pickBtn = el('button', 'vw-toggle', WT('wallet.home.choose_contact', 'choose from contacts')); pickBtn.type = 'button'; pickBtn.id = 'vwHomePickContact';
    var list = el('div', 'vw-wh-contacts'); list.id = 'vwHomeContactList'; list.hidden = true;
    pickBtn.addEventListener('click', function(){
      list.hidden = !list.hidden;
      if (list.hidden || list.children.length) return;
      list.appendChild(el('div', 'vw-wh-sub', WT('wallet.home.reading_contacts', 'Reading saved wallet addresses…')));
      getJson('/api/contacts').then(function(j){
        list.textContent = '';
        var contacts = (j && j.contacts) || [];
        var found = 0;
        contacts.forEach(function(c){
          (c.endpoints || []).forEach(function(ep){
            if (ep.kind !== 'wallet') return;
            found += 1;
            var btn = el('button', 'vw-wh-contact'); btn.type = 'button';
            btn.appendChild(el('span', null, (c.display_name || WT('wallet.home.saved_contact', 'Saved contact')) + (ep.network_display ? ' · ' + ep.network_display : '')));
            btn.appendChild(el('span', 'vw-wh-ident', ep.value || ''));
            btn.addEventListener('click', function(){
              if (ep.chain_network && row && ep.chain_network !== row.network) {
                // a name saved for another network is not this payment's destination; nothing is filled silently
                contactNote.className = 'vw-wh-sub';
                contactNote.textContent = WTF('wallet.home.contact_other_network', '{name}\'s address is saved for {saved}, not {network}. Sending on {network} needs an address on this network.',
                  { name: c.display_name, saved: (ep.network_display || ep.chain_network), network: rowName(row) });
                return;
              }
              to.value = ep.value || ''; filledFrom = to.value;
              contactNote.className = 'vw-wh-sub';
              contactNote.textContent = WTF('wallet.home.contact_paying', 'Paying the saved contact {name} — the exact destination is the address shown in the field.', { name: c.display_name });
              list.hidden = true;
            });
            list.appendChild(btn);
          });
        });
        if (!found) list.appendChild(el('div', 'vw-wh-sub', WT('wallet.home.no_contacts', 'No saved wallet addresses yet. Typing an address directly always works.')));
      }).catch(function(){ list.textContent = ''; list.appendChild(el('div', 'vw-wh-sub', WT('wallet.home.contacts_unreadable', 'Contacts could not be read; type the address directly.'))); });
    });
    form.appendChild(pickBtn); form.appendChild(list);
    // what to send: the row's own coin, or a token a Crypto Pilot account may send on this row (USDT where it is carried)
    var nativeChoice = { symbol: (row && row.native_symbol) || 'SOL', label: (row && row.native_display_symbol) || 'SOL', decimals: (row && row.native_decimals) || 9 };
    var choices = [nativeChoice].concat(((row && row.send_tokens) || []).map(function(t){ return { symbol: t.symbol, label: t.symbol, decimals: t.decimals }; }));
    var chosen = nativeChoice;
    var assetPick = null;
    if (account.can_send_tokens === true && choices.length > 1) {
      var assetLabel = el('label', null, WT('wallet.home.what_to_send', 'What to send')); assetLabel.setAttribute('for', 'vwHomeSendAsset'); form.appendChild(assetLabel);
      assetPick = el('select'); assetPick.id = 'vwHomeSendAsset';
      choices.forEach(function(c){ var o = el('option', null, c.label); o.value = c.symbol; assetPick.appendChild(o); });
      form.appendChild(assetPick);
    }
    var amountLabel = el('label', null, WTF('wallet.home.amount_label', 'Amount ({symbol})', { symbol: nativeChoice.label })); amountLabel.setAttribute('for', 'vwHomeSendAmount'); form.appendChild(amountLabel);
    var amount = el('input'); amount.type = 'text'; amount.id = 'vwHomeSendAmount'; amount.autocomplete = 'off'; amount.inputMode = 'decimal';
    amount.placeholder = '0.0';
    form.appendChild(amount);
    if (assetPick) assetPick.addEventListener('change', function(){
      chosen = choices.filter(function(c){ return c.symbol === assetPick.value; })[0] || nativeChoice;
      amountLabel.textContent = WTF('wallet.home.amount_label', 'Amount ({symbol})', { symbol: chosen.label });
      available.textContent = chosen === nativeChoice ? availableNative
        : WTF('wallet.home.token_note', 'Your {token} balance is shown on the review before anything is approved. The network fee is paid in {coin}, so keep a little {coin} in this account too.', { token: chosen.label, coin: nativeChoice.label });
      sendKey = newSendKey();
    });
    var availableNative = WT('wallet.home.available', 'Available: the balance line above (the exact maximum debit is stated on the review before anything is approved).');
    var available = el('div', 'vw-wh-sub', availableNative); available.setAttribute('aria-live', 'polite');
    form.appendChild(available);
    var memoLabel = el('label', null, WT('wallet.home.memo', 'Note (optional, stays with your request)')); memoLabel.setAttribute('for', 'vwHomeSendMemo'); form.appendChild(memoLabel);
    var memo = el('input'); memo.type = 'text'; memo.id = 'vwHomeSendMemo'; memo.autocomplete = 'off'; form.appendChild(memo);
    var go = el('button', 'vw-btn primary', WT('wallet.home.preview', 'Preview send')); go.type = 'button'; go.id = 'vwHomeSendGo';
    if (!(caps.quote && caps.quote.available)) {
      go.disabled = true;
      var quoteWhy = CAP_REASONS[(caps.quote || {}).reason] || (caps.quote || {}).reason || WT('wallet.home.unavailable', 'unavailable');
      go.title = WTF('wallet.home.preview_title', 'Preview: {reason}', { reason: quoteWhy });
      form.appendChild(el('div', 'vw-wh-warn', WTF('wallet.home.preview_unavailable', 'Previewing a payment from this account is unavailable here: {reason}.', { reason: quoteWhy })));
    }
    form.appendChild(go);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    // editing the intent after a refusal is a NEW intent: the idempotency key rotates, so an honest
    // retry of the unchanged request stays deduplicated while an edited one is never refused as a duplicate
    function rotate(){ sendKey = newSendKey(); }
    to.addEventListener('input', rotate); amount.addEventListener('input', rotate); memo.addEventListener('input', rotate);
    go.addEventListener('click', function(){
      var dest = String(to.value || '').trim();
      if (!dest) { state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.type_recipient', 'Type the recipient address.'); return; }
      var decimals = chosen.decimals;
      var minor = parseAmountToMinor(amount.value, decimals);
      if (minor === null) {
        state.className = 'vw-wh-msg err';
        state.textContent = WTF('wallet.home.amount_invalid', 'The amount must be a positive number with at most {decimals} decimals — exact, not rounded.', { decimals: decimals });
        return;
      }
      var minorJson = minorForJson(minor);
      if (minorJson === null) {
        state.className = 'vw-wh-msg err';
        state.textContent = WT('wallet.home.amount_too_large', 'This amount is too large to carry exactly through this form. Enter a smaller amount.');
        return;
      }
      go.disabled = true; state.className = 'vw-wh-msg'; state.textContent = WT('wallet.home.preparing', 'Preparing the request… (nothing is sent yet)');
      var body = { wallet_id: account.wallet_id, destination: dest, amount_minor: minorJson, asset: chosen.symbol, origin: 'user', memo: String(memo.value || ''), network: selNetwork, idempotency_key: sendKey };
      postJson('/api/wallet/propose', body).then(function(res){
        if (!res.ok) {
          go.disabled = false; state.className = 'vw-wh-msg err';
          state.textContent = faultMessage(res, WT('wallet.home.refused', 'The request was refused. Nothing was sent.'));
          return null;
        }
        sendKey = newSendKey();  // the intent exists as a proposal now; a new form is a new intent
        var pid = (res.data.proposal || {}).proposal_id || '';
        state.textContent = WT('wallet.home.prepared_opening', 'Request prepared. Opening the review…');
        return refreshStatus().then(function(st2){
          st2 = st2 || lastStatus || {};
          var prow = (st2.pending || []).filter(function(x){ return x && x.proposal_id === pid; })[0] || null;
          var a = actions(); var host = a && a.chatLog ? a.chatLog() : null;
          if (prow && host && !host.querySelector('[data-proposal="' + cssEscape(pid) + '"]')) host.appendChild(pilotCardFor(prow));
          var entry = pilotCards[pid];
          if (prow && prow.pilot_transfer !== false && entry) {
            openSheet(prow, entry.controls, entry.result, entry.opener || null);
            view = 'home'; drawHome(st2);  // beneath the sheet: home with the request in Activity, ready for Decide later
          } else {
            view = 'home'; drawHome(st2);
            say(prow ? WT('wallet.home.prepared_card', 'The request is prepared — its card in the chat below carries the approval.') : WT('wallet.home.prepared_check', 'The request is prepared. Check Activity and the chat card below.'));
          }
          return null;
        });
      }).catch(function(){ go.disabled = false; state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.request_incomplete', 'The request did not complete; nothing was sent.'); });
    });
    to.focus();
  }

  // ---- setup / onboarding: only the choices this build actually supports ---------------------------
  function drawSetup(st){
    view = 'setup';
    var cp = st.crypto_pilot || {};
    bodyEl.textContent = '';
    var back = null;
    if (accountsOf(st).length) {
      back = el('button', 'vw-toggle', WT('wallet.home.back', '← Back to the wallet')); back.type = 'button';
      back.addEventListener('click', function(){ view = 'home'; drawHome(st); });
      bodyEl.appendChild(back);
    }
    bodyEl.appendChild(el('div', 'vw-wh-sec', WT('wallet.home.setup_title', 'Set up your wallet')));
    var points = el('div', 'vw-safety');
    points.appendChild(el('div', 'vw-point', WT('wallet.home.setup_point1', '1. Protect your recovery material: a backup is shown once and VOOL cannot recover it for you.')));
    points.appendChild(el('div', 'vw-point', WT('wallet.home.setup_point2', '2. Check the destination and network before sending: transfers are irreversible.')));
    bodyEl.appendChild(points);
    var learn = el('details', 'vw-learn');
    learn.appendChild(el('summary', null, WT('wallet.home.learn_more', 'Learn more')));
    learn.appendChild(el('div', 'vw-wh-note', (cp.pilot_notice || '') + ' ' + WT('wallet.home.learn_more_text', 'Watch-only is the safest start: VOOL observes and proposes, and nothing can ever be signed for that address here. Full management (spending caps, backup confirmation, the network environment) lives in Settings → Crypto.')));
    bodyEl.appendChild(learn);
    var acts = el('div', 'vw-wh-setup-actions'); acts.id = 'vwHomeSetupActions';
    var targetRow = rowFor(st, selNetwork) || activeRows(st)[0] || null;
    var caps = (targetRow && targetRow.capabilities) || {};
    var create = el('button', 'vw-btn primary', targetRow ? WTF('wallet.home.create_on', 'Create a VOOL Wallet on {network}', { network: rowName(targetRow) }) : WT('wallet.home.create', 'Create a VOOL Wallet'));
    create.type = 'button'; create.id = 'vwHomeCreate';
    if (caps.create && caps.create.available) create.addEventListener('click', function(){ panelCreateFlow(st, targetRow, cp); });
    else {
      create.disabled = true;
      var why = CAP_REASONS[(caps.create || {}).reason] || (caps.create || {}).reason || WT('wallet.home.no_row', 'no network row available');
      create.title = WTF('wallet.home.create_title', 'Create: {reason}', { reason: why });
      create.setAttribute('aria-label', create.title);
      acts.appendChild(el('div', 'vw-wh-note', WTF('wallet.home.create_unavailable', 'Creating a wallet is unavailable here: {reason}.', { reason: why })));
    }
    acts.appendChild(create);
    var watch = el('button', 'vw-btn', WT('wallet.home.add_watch', 'Add a watch-only address')); watch.type = 'button'; watch.id = 'vwHomeWatch';
    watch.addEventListener('click', function(){ panelWatchFlow(st); });
    acts.appendChild(watch);
    var phantom = el('button', 'vw-btn', WT('wallet.home.connect_phantom', 'Connect Phantom or another Solana wallet')); phantom.type = 'button'; phantom.id = 'vwHomePhantom';
    phantom.addEventListener('click', function(){
      var p = provider();
      if (!p || typeof p.connect !== 'function') { say(noPhantomMessage(), true); return; }
      p.connect().then(function(r){
        var key = (r && r.publicKey && r.publicKey.toString()) || (p.publicKey && p.publicKey.toString()) || '';
        if (!key) throw new Error(WT('wallet.home.phantom_no_account', 'the wallet returned no account'));
        bindPhantomEvents();
        return postJson('/api/wallet/external', { public_key: key, label: 'phantom' }).then(function(res){
          if (!res.ok) { say(faultMessage(res, WT('wallet.home.not_connected', 'Not connected.')), true); return; }
          say(WTF('wallet.home.phantom_connected', 'Phantom connected: {address}.', { address: short(key) })); view = 'home'; renderFromStatus();
        });
      }).catch(function(err){ say(WTF('wallet.home.phantom_failed', 'Phantom did not connect: {reason}', { reason: (err && err.message ? err.message : WT('wallet.home.no_answer', 'no answer')) }), true); });
    });
    acts.appendChild(phantom);
    var evmNetworks = activeRows(st).filter(function(r){ return isEvmNetwork(r.network); });
    if (evmNetworks.length) {
      var evm = el('button', 'vw-btn', WT('wallet.home.connect_evm', 'Connect MetaMask or another wallet (EVM)')); evm.type = 'button'; evm.id = 'vwHomeEvm';
      evm.addEventListener('click', function(){
        var eth = evmProvider();
        if (!eth) { say(WT('wallet.home.no_evm', 'No MetaMask or other EVM wallet was found in this browser. Open VOOL in a browser that has the wallet extension to connect it.'), true); return; }
        var chain = evmNetworks[0].network;
        evm.disabled = true;
        eth.request({ method: 'eth_requestAccounts' }).then(function(accounts){
          var accountAddr = (accounts && accounts[0] || '').toLowerCase();
          if (!accountAddr) throw new Error(WT('wallet.home.evm_no_account', 'no account'));
          return eth.request({ method: 'eth_chainId' }).then(function(chainIdHex){
            var wanted = parseInt(chain.split(':')[1], 10), got = parseInt(String(chainIdHex), 16);
            if (wanted !== got) throw new Error(WTF('wallet.home.evm_wrong_chain', 'the wallet is on chain {got}, not {wanted}; switch it and retry', { got: got, wanted: wanted }));
            return postJson('/api/wallet/external', { public_key: accountAddr, network: chain, label: 'evm' });
          });
        }).then(function(res){
          evm.disabled = false;
          if (!res || !res.ok) { say((res && faultMessage(res, WT('wallet.home.not_connected', 'Not connected.'))) || WT('wallet.home.not_connected', 'Not connected.'), true); return; }
          say(WTF('wallet.home.evm_registered', 'EVM wallet registered on {network}.', { network: evmChainLabel(chain) }) + (evmNetworks[0].environment === 'mainnet' ? ' ' + WT('wallet.home.evm_mainnet_note', 'Here it receives and shows its balance; sending on this network stays in a VOOL Wallet created on this device.') : '')); view = 'home'; renderFromStatus();
        }).catch(function(err){ evm.disabled = false; say(WTF('wallet.home.evm_failed', 'The EVM wallet did not connect: {reason}', { reason: (err && err.message ? err.message : WT('wallet.home.unknown_error', 'unknown error')) }), true); });
      });
      acts.appendChild(evm);
    }
    var restore = el('button', 'vw-btn', WT('wallet.home.restore_phrase', 'Restore from recovery phrase')); restore.type = 'button'; restore.id = 'vwHomeRestore';
    restore.addEventListener('click', function(){ panelRestoreFlow(st); });
    acts.appendChild(restore);
    bodyEl.appendChild(acts);
    bodyEl.appendChild(el('div', 'vw-wh-note', WT('wallet.home.setup_note', 'Everything above stays optional and nothing is created by opening this panel. Full setup, spending caps and the network environment live in Settings → Crypto.')));
  }
  function panelCreateFlow(st, row, cp){
    var acts = document.getElementById('vwHomeSetupActions');
    if (!acts) return;
    acts.hidden = true;
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeCreateForm';
    form.appendChild(el('div', 'vw-wh-warn', WTF('wallet.home.create_warning', 'Pilot feature. Keep small test amounts and do not use this as a main wallet. The backup is shown once, right after creation, and must be saved offline. The wallet lives on {network} ({badge}) and sends its native coin only.', { network: rowName(row), badge: (row.badge || '') })));
    form.appendChild(el('div', 'vw-wh-note', WT('wallet.home.create_choose', 'Choose how you will approve payments. The key is created only after this credential is confirmed, and is sealed with it from the start.')));
    var creds = credentialPrompt(form, 'vwHomeC', cp);
    var lLabel = el('label', null, WT('wallet.home.label_optional', 'Label (optional)')); lLabel.setAttribute('for', 'vwHomeLabel'); form.appendChild(lLabel);
    var label = el('input'); label.type = 'text'; label.id = 'vwHomeLabel'; label.autocomplete = 'off'; form.appendChild(label);
    var btnRow = el('div', 'vw-wh-actions');
    var go = el('button', 'vw-btn primary', WT('wallet.home.create_go', 'Create wallet')); go.type = 'button'; go.id = 'vwHomeCreateGo';
    var cancel = el('button', 'vw-btn', WT('dialog.cancel', 'Cancel')); cancel.type = 'button';
    btnRow.appendChild(go); btnRow.appendChild(cancel); form.appendChild(btnRow);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    function abandon(){ creds.clear(); form.remove(); acts.hidden = false; say(''); }
    cancel.addEventListener('click', abandon);
    var creationKey = 'home-' + Math.random().toString(16).slice(2) + Date.now().toString(16);
    go.addEventListener('click', function(){
      var method = creds.method.value;
      if (method !== 'device' && creds.cred.value !== creds.conf.value) { state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.entries_differ', 'The two entries differ. Type the same PIN or password twice.'); return; }
      go.disabled = true; state.className = 'vw-wh-msg'; state.textContent = WT('wallet.home.creating', 'Creating the wallet…');
      var credential = creds.cred.value;
      var body = { network: row.network, method: method, credential: credential, credential_confirmation: creds.conf.value, creation_key: creationKey, label: label.value || '' };
      postJson('/api/wallet/setup/create', body).then(function(res){
        body = null;
        if (!res.ok) { go.disabled = false; creds.clear(); state.className = 'vw-wh-msg err'; state.textContent = faultMessage(res, WT('wallet.home.not_created', 'Not created. Nothing changed.')); return null; }
        var setup = res.data.setup || {};
        state.textContent = WTF('wallet.home.created', 'Wallet created ({address}). Preparing the one-time backup…', { address: short(setup.address) });
        return postJson('/api/wallet/setup/reveal', { wallet_id: setup.wallet_id, credential: credential }).then(function(rev){
          creds.clear(); credential = null;
          if (!rev.ok) {
            form.remove(); acts.hidden = false;
            say(WTF('wallet.home.backup_not_shown', 'The wallet exists but its backup could not be shown now: {reason}. Use the actions in Settings → Crypto.', { reason: faultMessage(rev, WT('wallet.home.reveal_refused', 'reveal refused')) }), true);
            renderFromStatus(); return null;
          }
          form.remove();
          showBackup(setup, rev.data.backup || {}, function(){ selWalletId = setup.wallet_id || ''; selNetwork = row.network; view = 'home'; renderFromStatus(); });
          return null;
        });
      }).catch(function(){ go.disabled = false; creds.clear(); body = null; state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.create_incomplete', 'The request did not complete. If a wallet was created, it is listed in Settings → Crypto; the same Create retries safely.'); });
    });
    creds.cred.focus();
  }
  function panelWatchFlow(st){
    var acts = document.getElementById('vwHomeSetupActions');
    if (!acts) return;
    acts.hidden = true;
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeWatchForm';
    form.appendChild(el('div', 'vw-wh-note', WT('wallet.home.watch_intro', 'Watch-only is the recommended start: VOOL can observe, simulate and PROPOSE payments for this address, but nothing can ever be signed here — there is no key to steal.')));
    var aLabel = el('label', null, WT('wallet.home.public_address', 'Public address')); aLabel.setAttribute('for', 'vwHomeWatchAddr'); form.appendChild(aLabel);
    var addr = el('input'); addr.type = 'text'; addr.id = 'vwHomeWatchAddr'; addr.autocomplete = 'off'; form.appendChild(addr);
    var nLabel = el('label', null, WT('wallet.home.network', 'Network')); nLabel.setAttribute('for', 'vwHomeWatchNet'); form.appendChild(nLabel);
    var net = el('select'); net.id = 'vwHomeWatchNet';
    var sol = activeRows(st).filter(function(r){ return !isEvmNetwork(r.network); })[0];
    if (sol) { var o = el('option'); o.value = sol.network; o.textContent = rowName(sol); net.appendChild(o); }
    activeRows(st).filter(function(r){ return isEvmNetwork(r.network); }).forEach(function(r){
      var opt = el('option'); opt.value = r.network; opt.textContent = rowName(r); net.appendChild(opt);
    });
    form.appendChild(net);
    var lLabel = el('label', null, WT('wallet.home.label_optional', 'Label (optional)')); lLabel.setAttribute('for', 'vwHomeWatchLabel'); form.appendChild(lLabel);
    var label = el('input'); label.type = 'text'; label.id = 'vwHomeWatchLabel'; label.autocomplete = 'off'; form.appendChild(label);
    var btnRow = el('div', 'vw-wh-actions');
    var go = el('button', 'vw-btn primary', WT('wallet.home.add', 'Add')); go.type = 'button'; go.id = 'vwHomeWatchGo';
    var cancel = el('button', 'vw-btn', WT('dialog.cancel', 'Cancel')); cancel.type = 'button';
    btnRow.appendChild(go); btnRow.appendChild(cancel); form.appendChild(btnRow);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    function abandon(){ form.remove(); acts.hidden = false; say(''); }
    cancel.addEventListener('click', abandon);
    go.addEventListener('click', function(){
      var address = String(addr.value || '').trim();
      if (!address) { state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.type_public_address', 'Type the public address.'); return; }
      go.disabled = true;
      postJson('/api/wallet/watch-only', { public_key: address, network: net.value, label: String(label.value || '') }).then(function(res){
        go.disabled = false;
        if (!res.ok) { state.className = 'vw-wh-msg err'; state.textContent = faultMessage(res, WT('wallet.home.not_added', 'Not added.')); return; }
        say(WT('wallet.home.watch_added', 'Watch-only address added. It can observe and propose; approvals will explain that signing is unavailable.'));
        view = 'home'; renderFromStatus();
      }).catch(function(){ go.disabled = false; state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.not_added', 'Not added.'); });
    });
    addr.focus();
  }
  function panelRestoreFlow(st){
    var acts = document.getElementById('vwHomeSetupActions');
    if (!acts) return;
    acts.hidden = true;
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeRestoreForm';
    var sol = activeRows(st).filter(function(r){ return !isEvmNetwork(r.network); })[0];
    form.appendChild(el('div', 'vw-wh-note', WTF('wallet.home.restore_intro', 'Restores a {network} wallet from its 12- or 24-word recovery phrase. The phrase goes to the wallet\'s own restore door and nowhere else.', { network: (sol ? rowName(sol) : 'Solana Devnet') })));
    var pLabel = el('label', null, WT('wallet.home.phrase_label', 'Recovery phrase (12 or 24 words)')); pLabel.setAttribute('for', 'vwHomeRestorePhrase'); form.appendChild(pLabel);
    var phrase = el('input'); phrase.type = 'password'; phrase.id = 'vwHomeRestorePhrase'; phrase.autocomplete = 'off'; form.appendChild(phrase);
    var mLabel = el('label', null, WT('wallet.home.approve_method', 'How you approve payments')); mLabel.setAttribute('for', 'vwHomeRestoreMethod'); form.appendChild(mLabel);
    var method = el('select'); method.id = 'vwHomeRestoreMethod';
    [['pin', WT('wallet.home.method_pin', 'PIN (4–8 digits)')], ['password', WT('wallet.home.method_password', 'Wallet password (10+ characters)')]].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
    form.appendChild(method);
    var cLabel = el('label', null, WT('wallet.home.new_credential', 'New PIN or password')); cLabel.setAttribute('for', 'vwHomeRestorePin'); form.appendChild(cLabel);
    var pin = el('input'); pin.type = 'password'; pin.id = 'vwHomeRestorePin'; pin.autocomplete = 'new-password'; form.appendChild(pin);
    var btnRow = el('div', 'vw-wh-actions');
    var go = el('button', 'vw-btn primary', WT('wallet.home.restore', 'Restore')); go.type = 'button'; go.id = 'vwHomeRestoreGo';
    var cancel = el('button', 'vw-btn', WT('dialog.cancel', 'Cancel')); cancel.type = 'button';
    btnRow.appendChild(go); btnRow.appendChild(cancel); form.appendChild(btnRow);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    function abandon(){ phrase.value = ''; pin.value = ''; form.remove(); acts.hidden = false; say(''); }
    cancel.addEventListener('click', abandon);
    go.addEventListener('click', function(){
      go.disabled = true;
      postJson('/api/wallet/pocket/restore', { recovery_phrase: phrase.value, approval_method: method.value, pin: method.value === 'pin' ? pin.value : '', password: method.value === 'password' ? pin.value : '' }).then(function(res){
        go.disabled = false; phrase.value = ''; pin.value = '';
        if (!res.ok) { state.className = 'vw-wh-msg err'; state.textContent = faultMessage(res, WT('wallet.home.not_restored', 'Not restored.')); return; }
        say(WTF('wallet.home.restored', 'Wallet restored: {address}.', { address: short(res.data.wallet && res.data.wallet.public_key) }));
        view = 'home'; renderFromStatus();
      }).catch(function(){ go.disabled = false; phrase.value = ''; pin.value = ''; state.className = 'vw-wh-msg err'; state.textContent = WT('wallet.home.not_restored', 'Not restored.'); });
    });
    phrase.focus();
  }

  // reopening a pending decision: the SAME review sheet the proposal was born with. If the request
  // is no longer waiting, its transfer row (or its absence) is the honest answer — nothing is minted.
  function openReviewFor(pid){
    var st = lastStatus;
    if (!st) return;
    var row = (st.pending || []).filter(function(x){ return x && x.proposal_id === pid; })[0] || null;
    if (!row) { say(WT('wallet.home.no_longer_waiting', 'This request is no longer waiting for a decision. Its latest state is in Activity.')); return; }
    var entry = pilotCards[pid];
    if (!entry || !document.body.contains(entry.result)) {
      var a = actions(); var host = a && a.chatLog ? a.chatLog() : null;
      if (!host) { say(WT('wallet.home.no_chat_log', 'The chat log is not available here.')); return; }
      if (!host.querySelector('[data-proposal="' + cssEscape(pid) + '"]')) host.appendChild(pilotCardFor(row));
      entry = pilotCards[pid];
    }
    if (row.pilot_transfer !== false && entry) openSheet(row, entry.controls, entry.result, entry.opener || null);
    else say(WT('wallet.home.card_below', 'This request carries its approval controls on its card in the chat below.'));
  }

  // every status snapshot the page already polls drives: the Home entry, the landed-payment notices,
  // the open panel's live activity rows, and the off state closing the panel
  function onStatus(st){
    if (!st) return;
    syncEntry(st);
    if (st.enabled) noticeTransfers(st);
    if (!isOpen) return;
    if (!st.enabled) { closePanel(true); return; }
    var activity = document.getElementById('vwHomeActivity');
    if (activity && view === 'home') paintActivity(st, activity);
    var envBadge = document.getElementById('vwHomeEnv');
    var cp = st.crypto_pilot || {};
    if (envBadge) envBadge.textContent = (cp.environment_label || '') + (cp.frozen ? ' · ' + WT('wallet.home.frozen', 'payments frozen') : '');
  }
  return { open: openPanel, close: closePanel, isOpen: function(){ return isOpen; }, onStatus: onStatus, refresh: renderFromStatus };
})();

window.VoolWallet = Object.freeze({ refresh: function(){ renderStatus(); poll(); renderCrypto(); }, cards: function(){ return Object.keys(rendered); }, mountInto: mountInto, mountCrypto: mountCrypto, renderMeaning: renderMeaning, approvalAllowed: approvalAllowed, openHome: function(){ walletHome.open(); } });
})();
"""


_QR_VENDOR_PLACEHOLDER = "/*__VOOL_QR_VENDOR__*/"


def _qr_vendor_js() -> str:
    """The already-vendored offline QR generator, served inline and scoped INSIDE the fragment IIFE.

    The same file the mobile companion page serves (core/web/api/mobile_vendor/qrcode.js,
    qrcode-generator 2.0.4, MIT; only its license URLs are normalized http->https to
    satisfy the offline page's no-http contract). Splicing it just inside the IIFE keeps its
    ``var qrcode`` out of the global namespace (fragment law: one namespace). It never sends the
    encoded text anywhere: the QR is drawn locally from the matrix. An unreadable vendor file is
    not an error — the fragment degrades to address + copy and says so.
    """
    try:
        return (Path(__file__).resolve().parent / "web" / "api" / "mobile_vendor" / "qrcode.js").read_text(encoding="utf-8")
    except Exception:
        return ""


def render_wallet_fragment() -> str:
    vendor = _qr_vendor_js()
    js = _WALLET_JS.replace(_QR_VENDOR_PLACEHOLDER, vendor) if vendor else _WALLET_JS.replace(_QR_VENDOR_PLACEHOLDER, "")
    return "<style>" + _WALLET_CSS + "</style><script>" + js + "</script>"


__all__ = ["render_wallet_fragment"]
