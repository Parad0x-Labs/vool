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
  return 'Phantom is not available here: its provider is injected only by browsers with the Phantom extension (Chrome or Brave) and by Phantom\'s own mobile browser — this app window cannot load extensions. To connect a Phantom account, open this app in Chrome or Brave with the extension installed; on a phone, use the Phantom app\'s browser. Signing then stays in Phantom. A watch-only address or a VOOL Wallet on this device also works without Phantom.';
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
var EVM_CHAIN_NAMES = { 'eip155:84532': 'Base Sepolia', 'eip155:11155111': 'Ethereum Sepolia', 'eip155:97': 'BNB Smart Chain Testnet' };
function evmChainLabel(caip2){ return EVM_CHAIN_NAMES[caip2] || caip2; }
function isEvmNetwork(network){ return String(network || '').indexOf('eip155:') === 0; }
function answer_result(v){ return v && typeof v === 'object' ? v.result : null; }

// ---- settings section ------------------------------------------------------------------------
var sec = null, statusEl = null, msgEl = null, lastStatus = null;
function setMsg(text, isErr){ if (!msgEl) return; msgEl.textContent = text || ''; msgEl.className = 'vw-msg' + (isErr ? ' err' : ''); }
var CHAIN_LABELS = { 'eip155:84532': 'Base Sepolia', 'eip155:11155111': 'Ethereum Sepolia', 'eip155:97': 'BNB Smart Chain Testnet', 'solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1': 'Solana Devnet' };
function chainLabel(caip2){ return CHAIN_LABELS[caip2] || caip2; }
var MODE_LABELS = { watch_only: 'watch-only (observe and propose; cannot sign)', pocket_sealed: 'VOOL Wallet (key sealed on this device, approved by you at payment)', external_signer: 'external wallet (signing stays in your wallet)' };
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
    statusEl.appendChild(el('div', 'vw-line', 'crypto is optional: turn it off any time in Settings → Crypto; nothing is deleted.'));
    var offBtn = el('button', 'vw-toggle', 'turn off'); offBtn.type = 'button'; offBtn.id = 'vwOffBtn';
    offBtn.addEventListener('click', function(){ setWalletEnabled(false); });
    statusEl.appendChild(offBtn);
    var head = el('div', 'vw-line');
    head.appendChild(document.createTextNode('custody: ' + (MODE_LABELS[st.custody_mode] || st.custody_mode || 'none — no wallet yet (watch-only is the recommended start)')));
    var mainnetBadge = el('span', 'vw-badge' + (st.mainnet_enabled ? '' : ' testnet'), st.mainnet_enabled ? 'MAINNET ENABLED' : 'testnet only');
    head.appendChild(mainnetBadge);
    statusEl.appendChild(head);
    (st.soft_warnings || []).forEach(function(w){ var note = el('div', 'vw-line vw-soft-warning', (w && w.text) || ''); note.setAttribute('role', 'note'); statusEl.appendChild(note); });
    if (st.public_key) statusEl.appendChild(maskedLine('public key', st.public_key, 'vwPubKey'));
    statusEl.appendChild(el('div', 'vw-line', 'pending approvals: ' + (st.pending_approvals || 0) + ' · signing preference: ' + (st.signing_preference || '') + ' · x402 cap (minor): ' + (st.x402_cap_minor != null ? st.x402_cap_minor : '')));
    if (st.custody_mode === 'pocket_sealed' && st.wallet_id) statusEl.appendChild(exportControls(st));
    var lim = st.limits || {};
    if (lim.per_tx_minor) { var limLine = el('div', 'vw-line', 'limits (minor units): per tx ' + lim.per_tx_minor + ', daily ' + lim.daily_minor + ', per destination/day ' + lim.per_destination_daily_minor); limLine.id = 'vwLimitsLine'; statusEl.appendChild(limLine); }
    var fees = st.dna_fees || null;
    if (fees && fees.policy) {
      var feeLine = el('div', 'vw-line'); feeLine.id = 'vwDnaFeesLine';
      var ids = fees.identities || [];
      var summary = ids.length ? ids.map(function(i){ return i.owed_exact + ' ' + i.asset + ' owed (' + i.collectible_exact + ' collectible, carry ' + i.carry_exact + ')' + (i.open_collection ? ' · collection ' + i.open_collection.state + ' with payment ' + short(i.open_collection.payment_proposal_id || '') : ''); }).join(' · ') : 'nothing accrued yet';
      feeLine.textContent = 'DNA service fee ' + (fees.policy.rate_label || '0.1%') + ' on native x402 payments · ' + summary + ' · treasury ' + ((fees.treasury || {}).state || 'unknown') + ((fees.treasury || {}).source === 'operator_override_disposable_recipient' ? ' (operator override)' : '');
      statusEl.appendChild(feeLine);
      if (st.wallet_id) statusEl.appendChild(dnaFeeControls(st, fees));
    }
    if (st.wallet_id) statusEl.appendChild(limitsControls(st));
    if (st.custody_mode === 'pocket_sealed' && st.wallet_id) statusEl.appendChild(approvalControls(st));
    var accounts = st.accounts || [];
    if (accounts.length) {
      var box = el('div', 'vw-accounts');
      accounts.forEach(function(a){
        var line = el('div', 'vw-account');
        line.appendChild(document.createTextNode((a.label ? a.label + ' · ' : '') + chainLabel(a.chain)));
        line.appendChild(el('span', 'vw-badge mode', a.mode));
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
      if (!res.ok) { setMsg((res.data && (res.data.message || (res.data.fault && res.data.fault.context && res.data.fault.context.remediation) || res.data.error)) || 'Not exported.', true); return; }
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
    postJson('/api/wallet/dna-fees/policy', { asset: asset.value, collect_min_atomic: parseInt(v, 10) }).then(function(res){
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
  var save = el('button', 'vw-btn', 'Save spending caps'); save.id = 'vwLimitsSave'; save.type = 'button';
  save.addEventListener('click', function(){
    var body = { wallet_id: st.wallet_id, asset: (asset.value || 'SOL').trim().toUpperCase() };
    var bad = false;
    [['per_tx_minor', perTx], ['daily_minor', daily], ['per_destination_daily_minor', perDest]].forEach(function(f){
      var v = String(f[1].value || '').trim(); if (v === '') return;
      if (!/^\d+$/.test(v)) { bad = true; return; } body[f[0]] = parseInt(v, 10);
    });
    if (bad) { setMsg('Caps are whole numbers of minor units.', true); return; }
    save.disabled = true;
    postJson('/api/wallet/limits', body).then(function(res){
      save.disabled = false;
      if (!res.ok) { setMsg((res.data && (res.data.message || (res.data.fault && res.data.fault.context && res.data.fault.context.remediation) || res.data.error)) || 'Caps not saved.', true); return; }
      setMsg('Spending caps saved.');
      renderStatus();
    }).catch(function(){ save.disabled = false; setMsg('The caps request did not complete.', true); });
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
      if (!res.ok) { setMsg((res.data && (res.data.message || (res.data.fault && res.data.fault.context && res.data.fault.context.remediation) || res.data.error)) || 'Approval method not changed.', true); return; }
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
  var createBtn = el('button', 'vw-btn', 'Create VOOL Wallet…'); createBtn.id = 'vwCreateBtn'; createBtn.type = 'button';
  var restoreBtn = el('button', 'vw-btn', 'Restore from phrase…'); restoreBtn.id = 'vwRestoreBtn'; restoreBtn.type = 'button';
  var phantomBtn = el('button', 'vw-btn primary', 'Connect Phantom'); phantomBtn.id = 'vwConnectPhantom'; phantomBtn.type = 'button';
  var evmChain = el('select', 'vw-chain'); evmChain.id = 'vwEvmChain'; evmChain.title = 'EVM testnet for the external wallet';
  var evmBtn = el('button', 'vw-btn primary', 'Connect EVM wallet'); evmBtn.id = 'vwConnectEvm'; evmBtn.type = 'button';
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
  var learn = el('details', 'vw-learn'); learn.id = 'vwLearnMore';
  learn.appendChild(el('summary', null, 'Learn more before you create a wallet'));
  var warn = el('div', 'vw-warn'); warn.id = 'vwWarning'; learn.appendChild(warn);
  learn.appendChild(el('div', 'vw-line', 'The tricks that empty wallets, explained shortly: Settings → Crypto → Stay safe.'));
  form.appendChild(learn);
  form.appendChild(el('label', null, 'Type this exact phrase to confirm:'));
  var expected = el('div', 'vw-line'); expected.id = 'vwPhraseExpected'; form.appendChild(expected);
  var ackLabel = el('label'); var ack = el('input'); ack.type = 'checkbox'; ack.id = 'vwAck'; ackLabel.appendChild(ack); ackLabel.appendChild(document.createTextNode(' I understand this device will hold the key')); form.appendChild(ackLabel);
  var phrase = el('input'); phrase.type = 'text'; phrase.id = 'vwPhrase'; phrase.autocomplete = 'off'; phrase.placeholder = 'confirmation phrase'; form.appendChild(phrase);
  form.appendChild(el('label', null, 'How you approve payments'));
  var method = el('select', 'vw-chain'); method.id = 'vwApprovalMethod';
  [['pin', 'PIN (4–8 digits)'], ['password', 'Wallet password (10+ characters)'], ['device', 'Touch ID or my Mac password']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
  form.appendChild(method);
  var pinLabel = el('label', null, 'PIN (4–8 digits)'); form.appendChild(pinLabel);
  var pin = el('input'); pin.type = 'password'; pin.id = 'vwPin'; pin.autocomplete = 'new-password'; pin.inputMode = 'numeric'; form.appendChild(pin);
  var pwLabel = el('label', null, 'Wallet password (10+ characters)'); pwLabel.hidden = true; form.appendChild(pwLabel);
  var pw = el('input'); pw.type = 'password'; pw.id = 'vwPassword'; pw.autocomplete = 'new-password'; pw.hidden = true; form.appendChild(pw);
  var devNote = el('div', 'vw-line', 'Every payment and every export will ask for Touch ID or your Mac password. Nothing is stored behind a PIN.'); devNote.hidden = true; form.appendChild(devNote);
  method.addEventListener('change', function(){ var m = method.value; pinLabel.hidden = pin.hidden = (m !== 'pin'); pwLabel.hidden = pw.hidden = (m !== 'password'); devNote.hidden = (m !== 'device'); });
  var submit = el('button', 'vw-btn primary', 'Create'); submit.id = 'vwCreateSubmit'; submit.type = 'button'; form.appendChild(submit);
  sec.appendChild(form);

  var rform = el('div', 'vw-form'); rform.id = 'vwRestoreForm'; rform.hidden = true;
  rform.appendChild(el('label', null, 'Recovery phrase (12 or 24 words)'));
  var rphrase = el('input'); rphrase.type = 'password'; rphrase.id = 'vwRestorePhrase'; rphrase.autocomplete = 'off'; rform.appendChild(rphrase);
  rform.appendChild(el('label', null, 'How you approve payments'));
  var rmethod = el('select', 'vw-chain'); rmethod.id = 'vwRestoreMethod';
  [['pin', 'PIN (4–8 digits)'], ['password', 'Wallet password (10+ characters)'], ['device', 'Touch ID or my Mac password']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; rmethod.appendChild(opt); });
  rform.appendChild(rmethod);
  rform.appendChild(el('label', null, 'New PIN or password (none for Touch ID)'));
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
  renderStatus().then(function(st){
    var declared = (st && st.declared_networks) || [];
    evmChain.textContent = '';
    var evmNetworks = declared.filter(function(n){ return isEvmNetwork(n); });
    evmNetworks.forEach(function(n){
      var opt = el('option'); opt.value = n; opt.textContent = evmChainLabel(n); evmChain.appendChild(opt);
    });
    evmChain.hidden = evmNetworks.length === 0;
    evmBtn.hidden = evmNetworks.length === 0;
  });
  evmBtn.addEventListener('click', function(){
    var eth = evmProvider();
    if (!eth) { setMsg('No EIP-1193 wallet (window.ethereum) is injected in this browser.', true); return; }
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
var cryptoHost = null, cryptoStatus = null, cryptoCreationKeys = {}, cryptoOpenForm = null;
var CAP_REASONS = {
  environment_inactive: 'not on the active network environment',
  evm_dependencies_missing: 'EVM support is missing on this runtime',
  pilot_lane_incomplete: 'not complete in this build',
  no_account_on_this_network: 'no account on this network yet',
  no_ready_signing_account: 'no wallet ready to sign here',
  no_setup_awaiting_backup: 'no setup waiting for its backup',
  storage_class_refused: 'key storage is not lasting on this install'
};
var CAP_LABELS = { create: 'Create', balance: 'Balance', quote: 'Preview', sign: 'Sign', send: 'Send', backup: 'Backup', receipt: 'Receipts', list: 'List' };
var SETUP_TEXT = {
  ready: 'ready',
  awaiting_backup: 'setup in progress: the backup has not been shown yet',
  backup_revealed: 'setup in progress: the backup was shown once and is not yet confirmed as saved',
  cancelled: 'setup cancelled: the wallet and its key are kept',
  generating: 'setup in progress'
};
function cxMsg(node, text, isErr){ node.textContent = text || ''; node.className = 'vw-msg' + (isErr ? ' err' : ''); }
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
  var env = el('div', 'vw-cx-env'); env.id = 'vwCxEnv';
  env.textContent = 'Active network environment: ' + (cp.environment_label || cp.active_environment || '') + ' · ' + (cp.environment_source_label || cp.environment_source || '');
  head.appendChild(env);
  if (cp.asset_support && cp.asset_support.note) { var assets = el('div', 'vw-cx-env', cp.asset_support.note); assets.id = 'vwCxAssets'; head.appendChild(assets); }
  if (cp.frozen) head.appendChild(el('div', 'vw-cx-warn', 'Payments are frozen by the panic control; nothing is signed or sent until it is lifted.'));
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
  box.appendChild(devOptions(cp, inactiveEmpty.sort(byOrder)));
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
  card.appendChild(el('div', 'vw-cx-env', (r.value_note || '') + ' · native coin ' + (r.native_display_symbol || r.native_symbol) + (r.chain_key === 'robinhood' ? ' · Robinhood Chain is a public network, not the Robinhood brokerage or its wallet' : '')));
  card.appendChild(capChips(r));
  var accounts = r.accounts || [];
  var caps = r.capabilities || {};
  accounts.forEach(function(a){ card.appendChild(accountCard(a, r, cp, st)); });
  var readyPilot = accounts.filter(function(a){ return a.mode === 'pocket_sealed' && a.setup_state === 'ready'; });
  var inProgress = accounts.filter(function(a){ return a.mode === 'pocket_sealed' && a.setup_state !== 'ready'; });
  var msg = el('div', 'vw-msg'); msg.className = 'vw-msg';
  if (r.active && caps.create && caps.create.available && !readyPilot.length && !inProgress.length) {
    var create = el('button', 'vw-btn primary vw-cx-create', 'Create wallet'); create.type = 'button';
    create.setAttribute('aria-label', 'create a VOOL wallet on ' + r.display_name);
    create.addEventListener('click', function(){ openCreateFlow(card, r, cp, msg, create); });
    card.appendChild(create);
  } else if (r.active && !(caps.create && caps.create.available) && !accounts.length) {
    card.appendChild(el('div', 'vw-cx-env', 'Create wallet is unavailable here: ' + (CAP_REASONS[(caps.create || {}).reason] || (caps.create || {}).reason || 'unavailable') + '.'));
  }
  card.appendChild(msg);
  return card;
}
function accountCard(a, r, cp, st){
  var box = el('div', 'vw-cx-account'); box.setAttribute('data-wallet', a.wallet_id); box.setAttribute('data-setup-state', a.setup_state || 'ready'); box.setAttribute('data-mode', a.mode || '');
  var line = el('div');
  line.appendChild(el('span', 'vw-line', (a.label ? a.label + ' · ' : '') + (a.mode_label || a.mode)));
  box.appendChild(line);
  var addr = el('div', 'vw-cx-address', a.address || ''); box.appendChild(addr);
  var setupState = a.setup_state || 'ready';
  var stateLine = el('div', 'vw-cx-env', 'State: ' + (SETUP_TEXT[setupState] || setupState)); stateLine.className += ' vw-cx-state'; box.appendChild(stateLine);
  var balance = el('div', 'vw-cx-balance', 'Balance not read yet.'); balance.setAttribute('data-balance-state', 'unread'); box.appendChild(balance);
  var actions = el('div', 'vw-cx-actions');
  var msg = el('div', 'vw-msg');
  if (setupState === 'ready' || !a.mode || a.mode !== 'pocket_sealed') {
    var receive = el('button', 'vw-btn vw-cx-receive', 'Receive'); receive.type = 'button';
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
    if (a.mode === 'pocket_sealed') { var forgot = forgotLink(a.wallet_id, 'Forgot PIN or password?'); forgot.className += ' vw-cx-forgot'; actions.appendChild(forgot); }
    box.appendChild(actions); box.appendChild(historyBox);
  } else {
    // a Crypto Pilot setup that has not finished: exactly the actions its durable state allows
    setupActions(actions, msg, a, r, cp);
    box.appendChild(actions);
  }
  box.appendChild(msg);
  return box;
}
function credentialPrompt(host, prefix, cp, opts){
  // the credential fields for one action; cleared by the caller after every request
  var policy = (cp && cp.credential_policy) || {};
  var methods = [['pin', 'PIN (' + ((policy.pin && policy.pin.text) || 'digits') + ')'], ['password', 'Wallet password (' + ((policy.password && policy.password.text) || '') + ')']];
  if (policy.device && policy.device.available) methods.push(['device', policy.device.text || 'Touch ID or your Mac password']);
  var mLabel = el('label', null, 'How you approve payments'); mLabel.setAttribute('for', prefix + 'Method'); host.appendChild(mLabel);
  var method = el('select', 'vw-chain'); method.id = prefix + 'Method';
  methods.forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
  if (!(policy.device && policy.device.available)) { var devNote = el('div', 'vw-cx-env', 'Touch ID / Mac password approval: ' + ((policy.device && policy.device.text) || 'not available on this machine') + '.'); devNote.id = prefix + 'DeviceNote'; host.appendChild(method); host.appendChild(devNote); } else host.appendChild(method);
  var cLabel = el('label', null, 'PIN'); cLabel.setAttribute('for', prefix + 'Credential'); host.appendChild(cLabel);
  var cred = el('input'); cred.type = 'password'; cred.id = prefix + 'Credential'; cred.autocomplete = 'new-password'; cred.inputMode = 'numeric'; host.appendChild(cred);
  var fLabel = el('label', null, 'Repeat the PIN'); fLabel.setAttribute('for', prefix + 'Confirm'); host.appendChild(fLabel);
  var conf = el('input'); conf.type = 'password'; conf.id = prefix + 'Confirm'; conf.autocomplete = 'new-password'; conf.inputMode = 'numeric'; host.appendChild(conf);
  function sync(){ var m = method.value; cLabel.hidden = cred.hidden = fLabel.hidden = conf.hidden = (m === 'device'); cLabel.textContent = m === 'pin' ? 'PIN' : 'Wallet password'; fLabel.textContent = m === 'pin' ? 'Repeat the PIN' : 'Repeat the password'; cred.inputMode = conf.inputMode = (m === 'pin' ? 'numeric' : 'text'); }
  method.addEventListener('change', sync); sync();
  if (opts && opts.single) { fLabel.hidden = true; conf.hidden = true; }
  return { method: method, cred: cred, conf: conf, clear: function(){ cred.value = ''; conf.value = ''; } };
}
function showBackup(wallet, backup, onDone){
  // ONE detachable node: the issued chain-specific backup, shown once. No storage, no history, no console, no request.
  var overlay = el('div', 'vw-sheet-overlay'); overlay.id = 'vwCxBackup';
  var box = el('div', 'vw-sheet'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-labelledby', 'vwCxBackupTitle');
  box.appendChild(el('h3', null, 'Your backup — shown once')).id = 'vwCxBackupTitle';
  box.appendChild(el('div', 'vw-cx-warn', (backup.warning || 'Anyone who has this backup owns the wallet.') + ' Save it offline now. VOOL cannot show it again.'));
  box.appendChild(el('div', 'vw-line', 'Wallet: ' + (wallet.display_name || wallet.network) + ' · address ' + (backup.address || wallet.address)));
  box.appendChild(el('div', 'vw-line', 'Format: ' + (backup.backup_format || '') + ' · ' + (backup.backup_target || '')));
  var value = el('div'); value.id = 'vwCxBackupValue'; value.textContent = backup.backup_value || ''; box.appendChild(value);
  var actions = el('div', 'vw-sheet-actions');
  var copy = el('button', 'vw-btn', 'Copy backup'); copy.id = 'vwCxBackupCopy'; copy.type = 'button';
  copy.addEventListener('click', function(){
    try { navigator.clipboard.writeText(value.textContent).then(function(){ copy.textContent = 'Copied — clear your clipboard after saving it'; }, function(){ copy.textContent = 'Clipboard unavailable — select the text and copy it by hand'; }); }
    catch (e) { copy.textContent = 'Clipboard unavailable — select the text and copy it by hand'; }
  });
  var saved = el('button', 'vw-btn primary', 'I have saved my backup'); saved.id = 'vwCxBackupSaved'; saved.type = 'button';
  var later = el('button', 'vw-btn', 'Close without confirming'); later.id = 'vwCxBackupClose'; later.type = 'button';
  later.title = 'The backup stays shown once: it will not be shown again. Confirm later with your PIN under the account.';
  var state = el('div', 'vw-msg'); state.id = 'vwCxBackupState';
  actions.appendChild(copy); actions.appendChild(saved); actions.appendChild(later); box.appendChild(actions); box.appendChild(state);
  overlay.appendChild(box); document.body.appendChild(overlay);
  function close(){ value.textContent = ''; backup = null; document.removeEventListener('keydown', onKey, true); overlay.remove(); }
  function onKey(ev){ if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); close(); if (onDone) onDone(false); } }
  document.addEventListener('keydown', onKey, true);
  later.addEventListener('click', function(){ close(); if (onDone) onDone(false); });
  saved.addEventListener('click', function(){
    saved.disabled = true; cxMsg(state, 'Confirming…');
    postJson('/api/wallet/setup/acknowledge', { wallet_id: wallet.wallet_id, ack_token: backup && backup.ack_token }).then(function(res){
      if (!res.ok) { saved.disabled = false; cxMsg(state, faultMessage(res, 'Not confirmed.'), true); return; }
      close(); if (onDone) onDone(true);
    }).catch(function(){ saved.disabled = false; cxMsg(state, 'The confirmation request did not complete.', true); });
  });
  saved.focus();
}
function openCreateFlow(card, r, cp, msg, createBtn){
  if (cryptoOpenForm) { cryptoOpenForm.remove(); cryptoOpenForm = null; }
  createBtn.hidden = true;
  var form = el('div', 'vw-cx-form'); form.id = 'vwCxCreateForm'; form.setAttribute('data-network', r.network);
  cryptoOpenForm = form;
  // step 1: the warning, then a choice; closing here changes nothing
  var warn = el('div', 'vw-cx-warn'); warn.id = 'vwCxWarning';
  warn.textContent = 'Pilot feature. Keep small test amounts and do not use this as a main wallet. The backup is shown once, right after creation, and must be saved offline. The wallet lives on ' + r.display_name + ' (' + (r.badge || '') + ') and sends its native coin only.';
  form.appendChild(warn);
  var step1 = el('div', 'vw-sheet-actions');
  var cont = el('button', 'vw-btn primary', 'Continue'); cont.id = 'vwCxContinue'; cont.type = 'button';
  var notNow = el('button', 'vw-btn', 'Not now'); notNow.id = 'vwCxNotNow'; notNow.type = 'button';
  step1.appendChild(cont); step1.appendChild(notNow); form.appendChild(step1);
  var step2 = el('div'); step2.hidden = true; form.appendChild(step2);
  function abandon(){ form.remove(); cryptoOpenForm = null; createBtn.hidden = false; cxMsg(msg, ''); }
  notNow.addEventListener('click', abandon);
  cont.addEventListener('click', function(){
    step1.hidden = true; step2.hidden = false;
    step2.appendChild(el('div', 'vw-line', 'Choose how you will approve payments. The wallet key is created only after this credential is confirmed, and is sealed with it from the start.'));
    var creds = credentialPrompt(step2, 'vwCx', cp);
    var lLabel = el('label', null, 'Label (optional)'); lLabel.setAttribute('for', 'vwCxLabel'); step2.appendChild(lLabel);
    var label = el('input'); label.type = 'text'; label.id = 'vwCxLabel'; label.autocomplete = 'off'; step2.appendChild(label);
    var row = el('div', 'vw-sheet-actions');
    var create = el('button', 'vw-btn primary', 'Create wallet'); create.id = 'vwCxCreate'; create.type = 'button';
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
      var body = { network: r.network, method: method, credential: credential, credential_confirmation: creds.conf.value, creation_key: cryptoCreationKeys[r.network], label: label.value || '' };
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
      postJson('/api/wallet/environment', { environment: choice.value }).then(function(res){
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
function cardFor(p){
  var card = el('div', 'msg assistant vw-card'); card.setAttribute('data-proposal', p.proposal_id);
  card.appendChild(el('div', 'vw-title', WT('wallet.review_title', 'Payment approval needed')));
  card.appendChild(purposeBlock(p.purpose, WT('wallet.review_title', 'Payment approval needed')));
  var chainRow = el('div', 'vw-row');
  chainRow.appendChild(document.createTextNode('network: ' + chainLabel(p.network) + ' · '));
  chainRow.appendChild(el('span', 'vw-badge testnet', WT('wallet.testnet_badge', 'testnet funds, not real money')));
  card.appendChild(chainRow);
  card.appendChild(el('div', 'vw-row', p.amount_minor + ' ' + p.asset + ' ' + WT('wallet.minor_units', '(minor units)')));
  card.appendChild(maskedLine('destination', p.destination, 'vwDest-' + p.proposal_id));
  card.appendChild(el('div', 'vw-row', WT('wallet.proposed_by', 'proposed by ') + (p.origin || 'unknown') + (p.memo ? ' · ' + p.memo : '') + ' · ' + p.proposal_id));
  var ack = renderMeaning(card, p.meaning);
  function ackText(){ return (p.meaning && p.meaning.tier === 'red') ? p.meaning.ack_text : undefined; }
  var controls = el('div', 'vw-row');
  var watchOnly = lastStatus && lastStatus.custody_mode === 'watch_only';
  var method = (lastStatus && lastStatus.approval_method) || 'pin';
  var pin = el('input', 'vw-pin'); pin.type = 'password'; pin.autocomplete = 'off'; pin.setAttribute('aria-label', method === 'password' ? WT('wallet.pw_aria', 'wallet password') : WT('wallet.pin_aria', 'wallet PIN'));
  if (method === 'password') { pin.placeholder = WT('wallet.pw_aria', 'wallet password'); } else { pin.placeholder = WT('wallet.pin_short', 'PIN'); pin.inputMode = 'numeric'; }
  var approve = el('button', 'vw-btn primary vw-approve' + (method === 'device' ? ' vw-approve-device' : ''), method === 'device' ? WT('wallet.approve_device', 'Approve with Touch ID / password') : (method === 'password' ? WT('wallet.approve_password', 'Approve with password') : WT('wallet.approve_pin', 'Approve with PIN'))); approve.type = 'button';
  var reject = el('button', 'vw-btn vw-reject', WT('wallet.reject', 'Reject')); reject.type = 'button'; reject.setAttribute('aria-label', WT('wallet.reject_aria', 'reject the payment proposal'));
  if (watchOnly) {
    // a watch-only wallet has no signer: offering a PIN box would be a dead end
    controls.appendChild(el('span', 'vw-line', WT('wallet.watch_only_hint', 'watch-only: this proposal can be approved in an external wallet, not signed here')));
  } else {
    if (method !== 'device') controls.appendChild(pin);
    controls.appendChild(approve);
  }
  var ext = null, evm = null;
  if (lastStatus && lastStatus.custody_mode === 'external_signer') {
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
  if (!watchOnly) approve.addEventListener('click', function(){
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
    if (!eth) { finish('No EIP-1193 wallet (window.ethereum) is injected in this browser.'); return; }
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
  var btn = el('button', 'vw-copy', 'Copy'); btn.type = 'button'; btn.setAttribute('aria-label', 'copy the full ' + (what || 'value'));
  btn.addEventListener('click', function(){
    try { navigator.clipboard.writeText(String(value || '')).then(function(){ btn.textContent = 'Copied'; }, function(){ btn.textContent = 'Select and copy'; }); }
    catch (e) { btn.textContent = 'Select and copy'; }
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
function transferLine(t){
  var line = el('div', 'vw-transfer');
  line.setAttribute('data-transfer-state', t.state || '');
  line.appendChild(el('span', 'vw-transfer-label', 'Transfer ' + (t.state_label || t.state || 'submitted') + ' on ' + (t.display_name || chainLabel(t.network))));
  if (t.detail) line.appendChild(el('div', 'vw-transfer-detail', t.detail));
  if (t.purpose) line.appendChild(purposeBlock(t.purpose, ''));
  if (t.to_address) { var to = el('div', 'vw-transfer-detail'); to.appendChild(document.createTextNode('To ')); to.appendChild(el('span', 'vw-ident', t.to_address)); line.appendChild(to); }
  if (t.charged_fee_label) line.appendChild(el('div', 'vw-transfer-detail', 'Fee ' + (t.charged_fee_display || t.charged_fee_label) + (t.charged_fee_display && t.charged_fee_human ? ' (exactly ' + t.charged_fee_human + ' ' + t.display_symbol + ')' : '')));
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
      if (res.ok && res.data && res.data.transfer) { close(); renderTransfer(host.parentNode || host, res.data.transfer); return; }
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
function syncTransfers(st){
  var seen = {};
  (st.in_flight || []).concat(st.transfers || []).forEach(function(t){
    if (!t || !t.proposal_id || seen[t.proposal_id]) return;
    seen[t.proposal_id] = true;
    var host = transferCards[t.proposal_id];
    if (host && host.getAttribute('data-transfer-state') !== t.state) { renderTransfer(host, t); host.setAttribute('data-transfer-state', t.state); }
  });
}
var pilotCards = {};
var ENDED_TEXT = {
  rejected: 'Cancelled elsewhere. Nothing was signed or sent.',
  expired: 'This request expired before a decision. Nothing was signed or sent.',
  failed: 'This request ended without a payment. Nothing was sent.'
};
function syncPilotCards(st){
  // A pending card belongs to the request, not to this page: when the request is approved, cancelled or expired on
  // another surface (another window, the API, a restart), the card follows the authoritative status -- a transfer
  // record replaces the Review action with the receipt line; an ended proposal says how it ended. Reading only:
  // nothing here approves, rejects, quotes or pays.
  var pending = {};
  (st.pending || []).forEach(function(p){ if (p && p.proposal_id) pending[p.proposal_id] = true; });
  var rows = {};
  (st.in_flight || []).concat(st.transfers || []).forEach(function(t){ if (t && t.proposal_id && !rows[t.proposal_id]) rows[t.proposal_id] = t; });
  Object.keys(pilotCards).forEach(function(pid){
    if (pending[pid]) return;
    var entry = pilotCards[pid];
    var t = rows[pid];
    if (t) {
      entry.controls.remove(); renderTransfer(entry.result, t); transferCards[pid] = entry.result; entry.result.setAttribute('data-transfer-state', t.state || '');
      delete pilotCards[pid];
      return;
    }
    if (entry.resolving) return;
    entry.resolving = true;
    getJson('/api/wallet/proposals/' + encodeURIComponent(pid)).then(function(j){
      var proposal = (j && j.proposal) || null;
      if (!proposal || !proposal.state) { entry.resolving = false; return; }
      if (proposal.state === 'pending_approval' || proposal.state === 'awaiting_signature') { entry.resolving = false; return; }  // a snapshot raced the record
      entry.controls.remove();
      entry.result.textContent = ENDED_TEXT[proposal.state] || ('This request ended (' + proposal.state + '). Nothing was signed or sent.');
      entry.result.setAttribute('data-proposal-state', proposal.state);
      delete pilotCards[pid];
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
    var ok = !!quote && !invalid && !expired() && !busy;
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
    if (f.token_transfer && f.recipient_token_account) to.appendChild(document.createTextNode(' · Recipient token account ' + f.recipient_token_account));
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
    var details = renderDetailsToggle();
    sheetRow(details, 'value_note', 'Funds', f.value_note);
    sheetRow(details, 'from', 'From', (f.from_label ? f.from_label + ' · ' : '') + f.from_address);
    sheetRow(details, 'to', 'To', f.to_address);
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
    cred.placeholder = method === 'password' ? 'wallet password' : 'wallet PIN';
    cred.setAttribute('aria-label', method === 'password' ? 'wallet password for this approval only' : 'wallet PIN for this approval only');
    if (method === 'pin') cred.inputMode = 'numeric';
    var primary = r.primary_action || 'Approve and send';
    approve.textContent = method === 'device' ? 'Approve with Touch ID / password: ' + primary.replace(/^Approve and /, '') : primary;
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
    decisionStarted = true; busy = true; gate(); setState(f.approval_method === 'device' ? 'Confirm with Touch ID or your Mac password…' : 'Checking the approval…');
    postJson('/api/wallet/approve', body).then(function(res){
      busy = false; body = null;
      if (res.ok) {
        var transfer = (res.data && (res.data.transfer || res.data.receipt)) || {};
        delete pilotCards[p.proposal_id];
        close(); cardControls.remove(); renderTransfer(cardResult, transfer); transferCards[p.proposal_id] = cardResult;
        cardResult.setAttribute('data-transfer-state', transfer.state || '');
        var companion = res.data && res.data.companion;
        if (companion && companion.collection_id) {
          var line = el('div', 'vw-transfer-detail', 'DNA fee collection with this payment: ' + companion.amount_exact + ' ' + (companion.asset || '') + ' · ' + (companion.state_label || companion.state) + (companion.tx_signature ? ' · transaction ' + companion.tx_signature : ''));
          line.setAttribute('data-companion-outcome', companion.state || ''); cardResult.appendChild(line);
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
      var data = res.data || {};
      if (res.ok && (data.cancelled === true || data.transfer)) delete pilotCards[p.proposal_id];
      if (res.ok && data.cancelled === true) {
        cardControls.remove();
        var t = data.transfer || null;
        // the copy follows what was true when the request was cancelled: nothing signed, or signed and never sent
        cardResult.textContent = (t && t.state !== 'cancelled') ? 'Cancel requested. Nothing will be sent.' : (t && t.state_label && data.reason !== 'rejected_before_claim' ? 'Cancelled. Nothing was sent.' : 'Cancelled. Nothing was signed or sent.');
        if (t) { transferCards[p.proposal_id] = cardResult; cardResult.setAttribute('data-transfer-state', t.state || ''); }
      } else if (res.ok && data.transfer) {
        cardControls.remove(); renderTransfer(cardResult, data.transfer); transferCards[p.proposal_id] = cardResult;
        cardResult.setAttribute('data-transfer-state', data.transfer.state || '');
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
function placeCards(){
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
    host.appendChild(transferCardFor(t));
    host.scrollTop = host.scrollHeight;
  });
  syncTransfers(lastStatus);
  syncPilotCards(lastStatus);
}
function refreshStatus(){
  return getJson('/api/wallet/status').then(function(j){
    var st = (j && j.status) || null;
    if (!st) return null;
    lastStatus = st;
    if (st.enabled) placeCards();
    syncSheet(st);
    walletHome.onStatus(st);
    // the Settings surface follows the switch above it: on, the rows appear; off, they go
    if (cryptoHost && cryptoStatus && !!st.enabled !== !!cryptoStatus.enabled && !cryptoOpenForm && !document.getElementById('vwCxBackup')) { cryptoStatus = st; drawCrypto(st); }
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
  function timeClock(epochSec){ try { return new Date(Number(epochSec) * 1000).toLocaleString(); } catch (e) { return ''; } }
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
      btn = el('button', 'side-foot-btn'); btn.type = 'button'; btn.id = 'vwHomeBtn';
      btn.title = 'VOOL Wallet — accounts, balance, receive, send and activity';
      btn.appendChild(document.createTextNode('\uD83D\uDC5B Wallet'));
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
      var what = t.amount_human + ' ' + t.display_symbol + ' on ' + (t.display_name || chainLabel(t.network));
      if (t.state === 'confirmed') toastWallet('VOOL Wallet: payment confirmed — ' + what);
      else if (t.state === 'failed') toastWallet('VOOL Wallet: payment failed — ' + what + '; nothing was sent');
    });
  }
  function noticeBalanceRise(walletId, b){
    if (b.state !== 'read') return;
    var prev = lastBalanceMinor[walletId];
    lastBalanceMinor[walletId] = { minor: String(b.balance_minor || ''), human: b.balance_human };
    if (!prev || !prev.minor) return;
    try {
      if (BigInt(String(b.balance_minor || '0')) > BigInt(prev.minor)) {
        var line = 'Received — the balance rose from ' + prev.human + ' to ' + b.balance_human + ' ' + b.asset +
          ' between looks. Noticed on this refresh; VOOL does not watch the chain in the background.';
        toastWallet('VOOL Wallet: ' + line);
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
    if (byDisable) toastWallet('VOOL Wallet is off. Wallets, balances and history are kept.');
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
    lastFocus = null;
  }
  function buildPanel(){
    overlay = el('div'); overlay.id = 'vwHomeOverlay';
    panel = el('div', 'vw-wh'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true'); panel.setAttribute('aria-labelledby', 'vwHomeTitle');
    var head = el('div', 'vw-wh-head');
    var title = el('span', 'vw-wh-title', 'VOOL Wallet'); title.id = 'vwHomeTitle'; head.appendChild(title);
    var envBadge = el('span', 'vw-badge mode'); envBadge.id = 'vwHomeEnv'; envBadge.hidden = true; head.appendChild(envBadge);
    var manage = el('button', 'vw-btn', 'Manage'); manage.type = 'button'; manage.id = 'vwHomeManage';
    manage.title = 'Settings → Crypto: enable/disable, setup, spending caps, environment';
    manage.addEventListener('click', function(){ closePanel(false); var a = actions(); if (a && a.openSettings) a.openSettings(); else window.location.href = '/settings#wallet'; });
    head.appendChild(manage);
    var x = el('button', 'vw-btn', 'Close'); x.type = 'button'; x.id = 'vwHomeClose'; x.setAttribute('aria-label', 'close the wallet panel');
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
    }).catch(function(){ if (isOpen) { bodyEl.textContent = ''; bodyEl.appendChild(el('div', 'vw-wh-empty', 'The wallet status could not be read. Try again from Home.')); } return null; });
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
    if (envBadge) { envBadge.hidden = !cp.environment_label; envBadge.textContent = (cp.environment_label || '') + (cp.frozen ? ' · payments frozen' : ''); }
    bodyEl.textContent = '';
    var accounts = accountsOf(st);
    if (!accounts.length) { drawSetup(st); return; }

    // account + network selectors
    var selects = el('div', 'vw-wh-selects');
    selects.appendChild(el('label', null, 'Account')).setAttribute('for', 'vwHomeAccount');
    var accountSel = el('select'); accountSel.id = 'vwHomeAccount'; accountSel.setAttribute('aria-label', 'wallet account');
    var order = cp.chain_order || [];
    function byOrder(a, b){ return order.indexOf(a.chain_key) - order.indexOf(b.chain_key); }
    activeRows(st).sort(byOrder).forEach(function(r){
      var group = el('optgroup'); group.label = rowName(r);
      rowAccounts(st, r.network).forEach(function(a){
        var opt = el('option'); opt.value = a.wallet_id;
        opt.textContent = ((a.label ? a.label + ' · ' : '') + short(a.public_key) + ' · ' + (a.mode === 'watch_only' ? 'watch-only' : (a.mode === 'external_signer' ? 'external' : 'VOOL Wallet')));
        if (a.wallet_id === selWalletId) opt.selected = true;
        group.appendChild(opt);
      });
      if (group.children.length) accountSel.appendChild(group);
    });
    var otherRows = rowsOf(st).filter(function(r){ return !r.active && rowAccounts(st, r.network).length; });
    if (otherRows.length) {
      var other = el('optgroup'); other.label = 'Other network environment (read-only here)';
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
    selects.appendChild(el('label', null, 'Network')).setAttribute('for', 'vwHomeNetwork');
    var networkSel = el('select'); networkSel.id = 'vwHomeNetwork'; networkSel.setAttribute('aria-label', 'network of the selected account');
    activeRows(st).sort(byOrder).forEach(function(r){
      var opt = el('option'); opt.value = r.network; opt.textContent = rowName(r) + (rowAccounts(st, r.network).length ? '' : ' — no account yet');
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
    var receive = el('button', 'vw-btn', 'Receive'); receive.type = 'button'; receive.id = 'vwHomeReceive';
    receive.disabled = !account;
    actionsRow.appendChild(receive);
    var send = el('button', 'vw-btn primary', 'Send'); send.type = 'button'; send.id = 'vwHomeSend';
    var caps = (row && row.capabilities) || {};
    var sendReason = '';
    if (!account) sendReason = 'no account on this network yet';
    else if (account.mode === 'watch_only') sendReason = 'watch-only accounts can observe and propose, but never sign here';
    else if (account.setup_state && account.setup_state !== 'ready') sendReason = 'finish this wallet\'s setup before sending';
    else if (!(caps.send && caps.send.available)) sendReason = CAP_REASONS[(caps.send || {}).reason] || (caps.send || {}).reason || 'unavailable';
    send.disabled = !!sendReason;
    if (sendReason) { send.title = 'Send: ' + sendReason; send.setAttribute('aria-label', 'Send — ' + sendReason); }
    actionsRow.appendChild(send);
    var manageSmall = el('button', 'vw-btn', 'Settings / Manage'); manageSmall.type = 'button';
    manageSmall.addEventListener('click', function(){ closePanel(false); var a = actions(); if (a && a.openSettings) a.openSettings(); });
    actionsRow.appendChild(manageSmall);
    bodyEl.appendChild(actionsRow);
    var addRow = el('div');
    var add = el('button', 'vw-toggle', 'Add, restore or connect a wallet'); add.type = 'button'; add.id = 'vwHomeAddWallet';
    add.title = 'The supported choices only: create on a network that offers it, restore from a phrase, connect an external wallet, or add a watch-only address';
    add.addEventListener('click', function(){ view = 'setup'; drawSetup(st); });
    addRow.appendChild(add);
    bodyEl.appendChild(addRow);
    receive.addEventListener('click', function(){ view = 'receive'; drawReceive(st); });
    send.addEventListener('click', function(){ if (!send.disabled) { view = 'send'; sendKey = newSendKey(); drawSend(st); } });

    // recent activity
    bodyEl.appendChild(el('div', 'vw-wh-sec', 'Activity'));
    var activity = el('div'); activity.id = 'vwHomeActivity';
    bodyEl.appendChild(activity);
    paintActivity(st, activity);

    var notes = el('div', 'vw-wh-note');
    notes.textContent = (cp.pilot_notice || '') + ' Payments are noticed here and as toasts while VOOL is open; incoming payments are seen when this view refreshes, not by a background watcher.';
    bodyEl.appendChild(notes);
    if (account) loadBalance(st);
  }

  function paintNoAccount(st){
    var box = document.getElementById('vwHomeBalance');
    if (!box) return;
    box.textContent = '';
    var row = rowFor(st, selNetwork);
    box.appendChild(el('div', 'vw-wh-amount', 'No account on ' + (row ? rowName(row) : chainLabel(selNetwork))));
    var line = el('div', 'vw-wh-balstate', 'Selecting a network never creates an account. ');
    var setup = el('button', 'vw-toggle', 'Set one up'); setup.type = 'button';
    setup.addEventListener('click', function(){ view = 'setup'; drawSetup(st); });
    line.appendChild(setup); box.appendChild(line);
  }
  function paintBalanceLoading(account, row){
    var box = document.getElementById('vwHomeBalance');
    if (!box) return;
    box.textContent = '';
    var amount = el('div', 'vw-wh-amount', 'Reading the balance…');
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
      stateLine.textContent = (isZero ? 'Balance 0 — confirmed zero as observed' : 'Observed ' + (b.ref || '') + ' · ' + timeClock(b.observed_at)) +
        ' · native coin only (tokens are not sent by this wallet)';
      var refresh = el('button', 'vw-toggle', 'refresh'); refresh.type = 'button'; refresh.style.marginLeft = '6px';
      refresh.addEventListener('click', function(){ loadBalance(lastStatus); });
      stateLine.appendChild(refresh);
    } else {
      amount.textContent = 'Balance unavailable';
      amount.appendChild(el('span', 'vw-wh-unit', (row && row.native_display_symbol) || ''));
      stateLine.className = 'vw-wh-balstate unavailable';
      stateLine.textContent = 'The balance could not be read (' + (CAP_REASONS[b.reason] || b.reason || 'not read') + ') — it is not zero.';
      var retry = el('button', 'vw-toggle', 'try again'); retry.type = 'button'; retry.style.marginLeft = '6px';
      retry.addEventListener('click', function(){ loadBalance(lastStatus); });
      stateLine.appendChild(retry);
    }
    box.appendChild(amount); box.appendChild(stateLine);
    var kind = account.mode === 'watch_only' ? 'watch-only: observe and propose; signing happens elsewhere' :
      (account.mode === 'external_signer' ? 'external wallet: signing stays in your wallet' :
      (account.setup_state && account.setup_state !== 'ready' ? 'setup not finished: ' + (SETUP_TEXT[account.setup_state] || account.setup_state) : 'VOOL Wallet: key sealed on this device'));
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
    d.appendChild(el('summary', null, 'View details'));
    var line = function(label, value){
      var r = el('div', 'vw-sheet-row'); r.appendChild(el('span', 'vw-sheet-label', label));
      var cell = el('span', 'vw-sheet-value');
      if (value !== null && value !== undefined) { var idn = el('span', 'vw-ident', String(value)); cell.appendChild(idn); cell.appendChild(copyButton(String(value), label.toLowerCase())); }
      r.appendChild(cell); d.appendChild(r);
    };
    line('To', t.to_address);
    line('From', t.from_address);
    line('Amount (exact)', t.amount_human + ' ' + t.display_symbol);
    if (t.charged_fee_label) line('Fee', t.charged_fee_label);
    else if (t.fee_max_display) line('Fee (at most)', t.fee_max_display);
    if (t.balance_after_human && t.state === 'confirmed') line('Balance after', t.balance_after_human + ' ' + t.display_symbol);
    if (t.tx_id) line('Transaction', t.tx_id);
    if (t.detail) d.appendChild(el('div', 'vw-transfer-detail', t.detail));
    if (t.purpose && t.purpose.kind && t.purpose.kind !== 'unknown') d.appendChild(purposeBlock(t.purpose, ''));
    else if (t.origin === 'dna_fee') d.appendChild(el('div', 'vw-transfer-detail', 'DNA service fee collection'));
    if (t.explorer_url) { var link = el('a', 'vw-explorer', t.explorer_link_text || 'View on the explorer'); link.href = t.explorer_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; d.appendChild(link); }
    return d;
  }
  function activityRowFor(t, isPending){
    var rowEl = el('div', 'vw-wh-row'); rowEl.setAttribute('data-pid', t.proposal_id || '');
    var head = el('div', 'vw-wh-rowhead');
    head.appendChild(stateChip(t));
    head.appendChild(el('span', 'vw-wh-amt', (t.amount_human || ((t.amount_minor || 0) + ' minor')) + ' ' + (t.display_symbol || t.asset || '')));
    head.appendChild(el('span', null, (t.display_name || chainLabel(t.network)) + (t.to_address ? ' → ' + short(t.to_address) : '')));
    head.appendChild(el('span', 'vw-wh-when', timeClock(t.created_at || t.updated_at)));
    rowEl.appendChild(head);
    if (isPending) {
      var review = el('button', 'vw-btn vw-wh-review', t.pilot_transfer === false ? 'Approve in chat' : 'Review'); review.type = 'button';
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
      host.appendChild(el('div', 'vw-wh-empty', 'No payments yet. Pending approvals, in-flight transfers and receipts appear here.'));
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
    var back = el('button', 'vw-toggle', '← Back to the wallet'); back.type = 'button';
    back.addEventListener('click', function(){ view = 'home'; drawHome(st); });
    bodyEl.appendChild(back);
    bodyEl.appendChild(el('div', 'vw-wh-sec', 'Receive'));
    var line = el('div', 'vw-wh-note');
    line.textContent = 'Receive ' + ((row && row.native_display_symbol) || '') + ' on ' + (row ? rowName(row) : chainLabel(account.network)) +
      ' (' + (row && row.badge ? row.badge + ' · ' + (row.value_note || '') : '') + '). Send only this network\'s native coin to this address.';
    bodyEl.appendChild(line);
    var addrBox = el('div');
    addrBox.appendChild(el('div', 'vw-wh-sec', 'Your address'));
    var addr = el('div', 'vw-wh-addr', account.public_key); addr.id = 'vwHomeAddress';
    addrBox.appendChild(addr);
    var copyRow = el('div');
    var copy = copyButton(account.public_key, 'address'); copy.id = 'vwHomeCopyAddress'; copy.className = 'vw-btn';
    copyRow.appendChild(copy);
    addrBox.appendChild(copyRow);
    bodyEl.appendChild(addrBox);
    var qr = qrCanvas(account.public_key);
    if (qr) { var box = el('div', 'vw-qr'); box.id = 'vwHomeQr'; box.appendChild(qr); bodyEl.appendChild(box); }
    else bodyEl.appendChild(el('div', 'vw-wh-note', 'No QR here: the offline QR generator is unavailable on this build. Copy the address instead.'));
    bodyEl.appendChild(el('div', 'vw-wh-note', account.mode === 'watch_only'
      ? 'Watch-only address: funds sent here are controlled by the wallet that owns it, not by VOOL. VOOL can observe and propose, never sign.'
      : (account.mode === 'external_signer'
        ? 'External wallet: funds are controlled by your connected wallet. VOOL never holds this key.'
        : 'VOOL Wallet: the key is sealed on this device and every payment asks for your approval.')));
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
      canvas.setAttribute('aria-label', 'QR code of the receiving address');
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
    var back = el('button', 'vw-toggle', '← Back to the wallet'); back.type = 'button';
    back.addEventListener('click', function(){ view = 'home'; drawHome(st); });
    bodyEl.appendChild(back);
    bodyEl.appendChild(el('div', 'vw-wh-sec', 'Send'));
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeSendForm';
    var netLine = el('div', 'vw-wh-note');
    netLine.textContent = 'From ' + (account.label ? account.label + ' · ' : '') + short(account.public_key) + ' on ' + (row ? rowName(row) : chainLabel(account.network));
    form.appendChild(netLine);
    var toLabel = el('label', null, 'Recipient address'); toLabel.setAttribute('for', 'vwHomeSendTo'); form.appendChild(toLabel);
    var to = el('input'); to.type = 'text'; to.id = 'vwHomeSendTo'; to.autocomplete = 'off'; to.spellcheck = false;
    to.setAttribute('aria-label', 'recipient address on ' + (row ? rowName(row) : selNetwork));
    form.appendChild(to);
    var contactNote = el('div', 'vw-wh-sub'); form.appendChild(contactNote);
    var pickBtn = el('button', 'vw-toggle', 'choose from contacts'); pickBtn.type = 'button'; pickBtn.id = 'vwHomePickContact';
    var list = el('div', 'vw-wh-contacts'); list.id = 'vwHomeContactList'; list.hidden = true;
    pickBtn.addEventListener('click', function(){
      list.hidden = !list.hidden;
      if (list.hidden || list.children.length) return;
      list.appendChild(el('div', 'vw-wh-sub', 'Reading saved wallet addresses…'));
      getJson('/api/contacts').then(function(j){
        list.textContent = '';
        var contacts = (j && j.contacts) || [];
        var found = 0;
        contacts.forEach(function(c){
          (c.endpoints || []).forEach(function(ep){
            if (ep.kind !== 'wallet') return;
            found += 1;
            var btn = el('button', 'vw-wh-contact'); btn.type = 'button';
            btn.appendChild(el('span', null, (c.display_name || 'Saved contact') + (ep.network_display ? ' · ' + ep.network_display : '')));
            btn.appendChild(el('span', 'vw-wh-ident', ep.value || ''));
            btn.addEventListener('click', function(){
              if (ep.chain_network && row && ep.chain_network !== row.network) {
                // a name saved for another network is not this payment's destination; nothing is filled silently
                contactNote.className = 'vw-wh-sub';
                contactNote.textContent = c.display_name + '\'s address is saved for ' + (ep.network_display || ep.chain_network) +
                  ', not ' + rowName(row) + '. Sending on ' + rowName(row) + ' needs an address on this network.';
                return;
              }
              to.value = ep.value || '';
              contactNote.className = 'vw-wh-sub';
              contactNote.textContent = 'Paying the saved contact ' + c.display_name + ' — the exact destination is the address shown in the field.';
              list.hidden = true;
            });
            list.appendChild(btn);
          });
        });
        if (!found) list.appendChild(el('div', 'vw-wh-sub', 'No saved wallet addresses yet. Settings → Contacts saves them; typing an address directly always works.'));
      }).catch(function(){ list.textContent = ''; list.appendChild(el('div', 'vw-wh-sub', 'Contacts could not be read; type the address directly.')); });
    });
    form.appendChild(pickBtn); form.appendChild(list);
    var amountLabel = el('label', null, 'Amount (' + ((row && row.native_display_symbol) || 'SOL') + ')'); amountLabel.setAttribute('for', 'vwHomeSendAmount'); form.appendChild(amountLabel);
    var amount = el('input'); amount.type = 'text'; amount.id = 'vwHomeSendAmount'; amount.autocomplete = 'off'; amount.inputMode = 'decimal';
    amount.placeholder = '0.0'; amount.setAttribute('aria-label', 'amount in whole ' + ((row && row.native_display_symbol) || 'SOL') + ' units');
    form.appendChild(amount);
    form.appendChild(el('div', 'vw-wh-sub', 'Available: the balance line above (the exact maximum debit is stated on the review before anything is approved).'));
    var memoLabel = el('label', null, 'Note (optional, stays with your request)'); memoLabel.setAttribute('for', 'vwHomeSendMemo'); form.appendChild(memoLabel);
    var memo = el('input'); memo.type = 'text'; memo.id = 'vwHomeSendMemo'; memo.autocomplete = 'off'; form.appendChild(memo);
    var go = el('button', 'vw-btn primary', 'Preview send'); go.type = 'button'; go.id = 'vwHomeSendGo';
    if (!(caps.quote && caps.quote.available)) {
      go.disabled = true;
      go.title = 'Preview: ' + (CAP_REASONS[(caps.quote || {}).reason] || (caps.quote || {}).reason || 'unavailable');
      form.appendChild(el('div', 'vw-wh-warn', 'Previewing a payment from this account is unavailable here: ' + (CAP_REASONS[(caps.quote || {}).reason] || (caps.quote || {}).reason || 'unavailable') + '.'));
    }
    form.appendChild(go);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    var decimals = (row && row.native_decimals) || 9;
    // editing the intent after a refusal is a NEW intent: the idempotency key rotates, so an honest
    // retry of the unchanged request stays deduplicated while an edited one is never refused as a duplicate
    function rotate(){ sendKey = newSendKey(); }
    to.addEventListener('input', rotate); amount.addEventListener('input', rotate); memo.addEventListener('input', rotate);
    go.addEventListener('click', function(){
      var dest = String(to.value || '').trim();
      if (!dest) { state.className = 'vw-wh-msg err'; state.textContent = 'Type the recipient address.'; return; }
      var minor = parseAmountToMinor(amount.value, decimals);
      if (minor === null) {
        state.className = 'vw-wh-msg err';
        state.textContent = 'The amount must be a positive number with at most ' + decimals + ' decimals — exact, not rounded.';
        return;
      }
      var minorJson = minorForJson(minor);
      if (minorJson === null) {
        state.className = 'vw-wh-msg err';
        state.textContent = 'This amount is too large to carry exactly through this form. Enter a smaller amount.';
        return;
      }
      go.disabled = true; state.className = 'vw-wh-msg'; state.textContent = 'Preparing the request… (nothing is sent yet)';
      var body = { wallet_id: account.wallet_id, destination: dest, amount_minor: minorJson, asset: (row && row.native_symbol) || 'SOL', origin: 'user', memo: String(memo.value || ''), network: selNetwork, idempotency_key: sendKey };
      postJson('/api/wallet/propose', body).then(function(res){
        if (!res.ok) {
          go.disabled = false; state.className = 'vw-wh-msg err';
          state.textContent = faultMessage(res, 'The request was refused. Nothing was sent.');
          return null;
        }
        sendKey = newSendKey();  // the intent exists as a proposal now; a new form is a new intent
        var pid = (res.data.proposal || {}).proposal_id || '';
        state.textContent = 'Request prepared. Opening the review…';
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
            say(prow ? 'The request is prepared — its card in the chat below carries the approval.' : 'The request is prepared. Check Activity and the chat card below.');
          }
          return null;
        });
      }).catch(function(){ go.disabled = false; state.className = 'vw-wh-msg err'; state.textContent = 'The request did not complete; nothing was sent.'; });
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
      back = el('button', 'vw-toggle', '← Back to the wallet'); back.type = 'button';
      back.addEventListener('click', function(){ view = 'home'; drawHome(st); });
      bodyEl.appendChild(back);
    }
    bodyEl.appendChild(el('div', 'vw-wh-sec', 'Set up your wallet'));
    var points = el('div', 'vw-safety');
    points.appendChild(el('div', 'vw-point', '1. Protect your recovery material: a backup is shown once and VOOL cannot recover it for you.'));
    points.appendChild(el('div', 'vw-point', '2. Check the destination and network before sending: transfers are irreversible.'));
    bodyEl.appendChild(points);
    var learn = el('details', 'vw-learn');
    learn.appendChild(el('summary', null, 'Learn more'));
    learn.appendChild(el('div', 'vw-wh-note', (cp.pilot_notice || '') + ' Watch-only is the safest start: VOOL observes and proposes, and nothing can ever be signed for that address here. Full management (spending caps, backup confirmation, the network environment) lives in Settings → Crypto.'));
    bodyEl.appendChild(learn);
    var acts = el('div', 'vw-wh-setup-actions'); acts.id = 'vwHomeSetupActions';
    var targetRow = rowFor(st, selNetwork) || activeRows(st)[0] || null;
    var caps = (targetRow && targetRow.capabilities) || {};
    var create = el('button', 'vw-btn primary', 'Create a VOOL Wallet' + (targetRow ? ' on ' + rowName(targetRow) : ''));
    create.type = 'button'; create.id = 'vwHomeCreate';
    if (caps.create && caps.create.available) create.addEventListener('click', function(){ panelCreateFlow(st, targetRow, cp); });
    else {
      create.disabled = true;
      var why = CAP_REASONS[(caps.create || {}).reason] || (caps.create || {}).reason || 'no network row available';
      create.title = 'Create: ' + why;
      create.setAttribute('aria-label', create.title);
      acts.appendChild(el('div', 'vw-wh-note', 'Creating a wallet is unavailable here: ' + why + '.'));
    }
    acts.appendChild(create);
    var watch = el('button', 'vw-btn', 'Add a watch-only address'); watch.type = 'button'; watch.id = 'vwHomeWatch';
    watch.addEventListener('click', function(){ panelWatchFlow(st); });
    acts.appendChild(watch);
    var phantom = el('button', 'vw-btn', 'Connect Phantom'); phantom.type = 'button'; phantom.id = 'vwHomePhantom';
    phantom.addEventListener('click', function(){
      var p = provider();
      if (!p || typeof p.connect !== 'function') { say(noPhantomMessage(), true); return; }
      p.connect().then(function(r){
        var key = (r && r.publicKey && r.publicKey.toString()) || (p.publicKey && p.publicKey.toString()) || '';
        if (!key) throw new Error('the wallet returned no account');
        bindPhantomEvents();
        return postJson('/api/wallet/external', { public_key: key, label: 'phantom' }).then(function(res){
          if (!res.ok) { say(faultMessage(res, 'Not connected.'), true); return; }
          say('Phantom connected: ' + short(key) + '.'); view = 'home'; renderFromStatus();
        });
      }).catch(function(err){ say('Phantom did not connect: ' + (err && err.message ? err.message : 'no answer'), true); });
    });
    acts.appendChild(phantom);
    var evmNetworks = activeRows(st).filter(function(r){ return isEvmNetwork(r.network); });
    if (evmNetworks.length) {
      var evm = el('button', 'vw-btn', 'Connect an EVM wallet'); evm.type = 'button'; evm.id = 'vwHomeEvm';
      evm.addEventListener('click', function(){
        var eth = evmProvider();
        if (!eth) { say('No EIP-1193 wallet (window.ethereum) is injected in this browser. Open this app in a browser with the wallet extension to connect it.', true); return; }
        var chain = evmNetworks[0].network;
        evm.disabled = true;
        eth.request({ method: 'eth_requestAccounts' }).then(function(accounts){
          var accountAddr = (accounts && accounts[0] || '').toLowerCase();
          if (!accountAddr) throw new Error('no account');
          return eth.request({ method: 'eth_chainId' }).then(function(chainIdHex){
            var wanted = parseInt(chain.split(':')[1], 10), got = parseInt(String(chainIdHex), 16);
            if (wanted !== got) throw new Error('the wallet is on chain ' + got + ', not ' + wanted + '; switch it and retry');
            return postJson('/api/wallet/external', { public_key: accountAddr, network: chain, label: 'evm' });
          });
        }).then(function(res){
          evm.disabled = false;
          if (!res || !res.ok) { say((res && faultMessage(res, 'Not connected.')) || 'Not connected.', true); return; }
          say('EVM wallet registered on ' + evmChainLabel(chain) + '.'); view = 'home'; renderFromStatus();
        }).catch(function(err){ evm.disabled = false; say('The EVM wallet did not connect: ' + (err && err.message ? err.message : 'unknown error'), true); });
      });
      acts.appendChild(evm);
    }
    var restore = el('button', 'vw-btn', 'Restore from recovery phrase'); restore.type = 'button'; restore.id = 'vwHomeRestore';
    restore.addEventListener('click', function(){ panelRestoreFlow(st); });
    acts.appendChild(restore);
    bodyEl.appendChild(acts);
    bodyEl.appendChild(el('div', 'vw-wh-note', 'Everything above stays optional and nothing is created by opening this panel. Full setup, spending caps and the network environment live in Settings → Crypto.'));
  }
  function panelCreateFlow(st, row, cp){
    var acts = document.getElementById('vwHomeSetupActions');
    if (!acts) return;
    acts.hidden = true;
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeCreateForm';
    form.appendChild(el('div', 'vw-wh-warn', 'Pilot feature. Keep small test amounts and do not use this as a main wallet. The backup is shown once, right after creation, and must be saved offline. The wallet lives on ' + rowName(row) + ' (' + (row.badge || '') + ') and sends its native coin only.'));
    form.appendChild(el('div', 'vw-wh-note', 'Choose how you will approve payments. The key is created only after this credential is confirmed, and is sealed with it from the start.'));
    var creds = credentialPrompt(form, 'vwHomeC', cp);
    var lLabel = el('label', null, 'Label (optional)'); lLabel.setAttribute('for', 'vwHomeLabel'); form.appendChild(lLabel);
    var label = el('input'); label.type = 'text'; label.id = 'vwHomeLabel'; label.autocomplete = 'off'; form.appendChild(label);
    var btnRow = el('div', 'vw-wh-actions');
    var go = el('button', 'vw-btn primary', 'Create wallet'); go.type = 'button'; go.id = 'vwHomeCreateGo';
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.type = 'button';
    btnRow.appendChild(go); btnRow.appendChild(cancel); form.appendChild(btnRow);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    function abandon(){ creds.clear(); form.remove(); acts.hidden = false; say(''); }
    cancel.addEventListener('click', abandon);
    var creationKey = 'home-' + Math.random().toString(16).slice(2) + Date.now().toString(16);
    go.addEventListener('click', function(){
      var method = creds.method.value;
      if (method !== 'device' && creds.cred.value !== creds.conf.value) { state.className = 'vw-wh-msg err'; state.textContent = 'The two entries differ. Type the same PIN or password twice.'; return; }
      go.disabled = true; state.className = 'vw-wh-msg'; state.textContent = 'Creating the wallet…';
      var credential = creds.cred.value;
      var body = { network: row.network, method: method, credential: credential, credential_confirmation: creds.conf.value, creation_key: creationKey, label: label.value || '' };
      postJson('/api/wallet/setup/create', body).then(function(res){
        body = null;
        if (!res.ok) { go.disabled = false; creds.clear(); state.className = 'vw-wh-msg err'; state.textContent = faultMessage(res, 'Not created. Nothing changed.'); return null; }
        var setup = res.data.setup || {};
        state.textContent = 'Wallet created (' + short(setup.address) + '). Preparing the one-time backup…';
        return postJson('/api/wallet/setup/reveal', { wallet_id: setup.wallet_id, credential: credential }).then(function(rev){
          creds.clear(); credential = null;
          if (!rev.ok) {
            form.remove(); acts.hidden = false;
            say('The wallet exists but its backup could not be shown now: ' + faultMessage(rev, 'reveal refused') + '. Use the actions in Settings → Crypto.', true);
            renderFromStatus(); return null;
          }
          form.remove();
          showBackup(setup, rev.data.backup || {}, function(){ selWalletId = setup.wallet_id || ''; selNetwork = row.network; view = 'home'; renderFromStatus(); });
          return null;
        });
      }).catch(function(){ go.disabled = false; creds.clear(); body = null; state.className = 'vw-wh-msg err'; state.textContent = 'The request did not complete. If a wallet was created, it is listed in Settings → Crypto; the same Create retries safely.'; });
    });
    creds.cred.focus();
  }
  function panelWatchFlow(st){
    var acts = document.getElementById('vwHomeSetupActions');
    if (!acts) return;
    acts.hidden = true;
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeWatchForm';
    form.appendChild(el('div', 'vw-wh-note', 'Watch-only is the recommended start: VOOL can observe, simulate and PROPOSE payments for this address, but nothing can ever be signed here — there is no key to steal.'));
    var aLabel = el('label', null, 'Public address'); aLabel.setAttribute('for', 'vwHomeWatchAddr'); form.appendChild(aLabel);
    var addr = el('input'); addr.type = 'text'; addr.id = 'vwHomeWatchAddr'; addr.autocomplete = 'off'; form.appendChild(addr);
    var nLabel = el('label', null, 'Network'); nLabel.setAttribute('for', 'vwHomeWatchNet'); form.appendChild(nLabel);
    var net = el('select'); net.id = 'vwHomeWatchNet';
    var sol = activeRows(st).filter(function(r){ return !isEvmNetwork(r.network); })[0];
    if (sol) { var o = el('option'); o.value = sol.network; o.textContent = rowName(sol); net.appendChild(o); }
    activeRows(st).filter(function(r){ return isEvmNetwork(r.network); }).forEach(function(r){
      var opt = el('option'); opt.value = r.network; opt.textContent = rowName(r); net.appendChild(opt);
    });
    form.appendChild(net);
    var lLabel = el('label', null, 'Label (optional)'); lLabel.setAttribute('for', 'vwHomeWatchLabel'); form.appendChild(lLabel);
    var label = el('input'); label.type = 'text'; label.id = 'vwHomeWatchLabel'; label.autocomplete = 'off'; form.appendChild(label);
    var btnRow = el('div', 'vw-wh-actions');
    var go = el('button', 'vw-btn primary', 'Add'); go.type = 'button'; go.id = 'vwHomeWatchGo';
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.type = 'button';
    btnRow.appendChild(go); btnRow.appendChild(cancel); form.appendChild(btnRow);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    function abandon(){ form.remove(); acts.hidden = false; say(''); }
    cancel.addEventListener('click', abandon);
    go.addEventListener('click', function(){
      var address = String(addr.value || '').trim();
      if (!address) { state.className = 'vw-wh-msg err'; state.textContent = 'Type the public address.'; return; }
      go.disabled = true;
      postJson('/api/wallet/watch-only', { public_key: address, network: net.value, label: String(label.value || '') }).then(function(res){
        go.disabled = false;
        if (!res.ok) { state.className = 'vw-wh-msg err'; state.textContent = faultMessage(res, 'Not added.'); return; }
        say('Watch-only address added. It can observe and propose; approvals will explain that signing is unavailable.');
        view = 'home'; renderFromStatus();
      }).catch(function(){ go.disabled = false; state.className = 'vw-wh-msg err'; state.textContent = 'Not added.'; });
    });
    addr.focus();
  }
  function panelRestoreFlow(st){
    var acts = document.getElementById('vwHomeSetupActions');
    if (!acts) return;
    acts.hidden = true;
    var form = el('div', 'vw-wh-form'); form.id = 'vwHomeRestoreForm';
    var sol = activeRows(st).filter(function(r){ return !isEvmNetwork(r.network); })[0];
    form.appendChild(el('div', 'vw-wh-note', 'Restores a ' + (sol ? rowName(sol) : 'Solana Devnet') + ' wallet from its 12- or 24-word recovery phrase. The phrase goes to the wallet\'s own restore door and nowhere else.'));
    var pLabel = el('label', null, 'Recovery phrase (12 or 24 words)'); pLabel.setAttribute('for', 'vwHomeRestorePhrase'); form.appendChild(pLabel);
    var phrase = el('input'); phrase.type = 'password'; phrase.id = 'vwHomeRestorePhrase'; phrase.autocomplete = 'off'; form.appendChild(phrase);
    var mLabel = el('label', null, 'How you approve payments'); mLabel.setAttribute('for', 'vwHomeRestoreMethod'); form.appendChild(mLabel);
    var method = el('select'); method.id = 'vwHomeRestoreMethod';
    [['pin', 'PIN (4–8 digits)'], ['password', 'Wallet password (10+ characters)']].forEach(function(o){ var opt = el('option'); opt.value = o[0]; opt.textContent = o[1]; method.appendChild(opt); });
    form.appendChild(method);
    var cLabel = el('label', null, 'New PIN or password'); cLabel.setAttribute('for', 'vwHomeRestorePin'); form.appendChild(cLabel);
    var pin = el('input'); pin.type = 'password'; pin.id = 'vwHomeRestorePin'; pin.autocomplete = 'new-password'; form.appendChild(pin);
    var btnRow = el('div', 'vw-wh-actions');
    var go = el('button', 'vw-btn primary', 'Restore'); go.type = 'button'; go.id = 'vwHomeRestoreGo';
    var cancel = el('button', 'vw-btn', 'Cancel'); cancel.type = 'button';
    btnRow.appendChild(go); btnRow.appendChild(cancel); form.appendChild(btnRow);
    var state = el('div', 'vw-wh-msg'); form.appendChild(state);
    bodyEl.appendChild(form);
    function abandon(){ phrase.value = ''; pin.value = ''; form.remove(); acts.hidden = false; say(''); }
    cancel.addEventListener('click', abandon);
    go.addEventListener('click', function(){
      go.disabled = true;
      postJson('/api/wallet/pocket/restore', { recovery_phrase: phrase.value, approval_method: method.value, pin: method.value === 'pin' ? pin.value : '', password: method.value === 'password' ? pin.value : '' }).then(function(res){
        go.disabled = false; phrase.value = ''; pin.value = '';
        if (!res.ok) { state.className = 'vw-wh-msg err'; state.textContent = faultMessage(res, 'Not restored.'); return; }
        say('Wallet restored: ' + short(res.data.wallet && res.data.wallet.public_key) + '.');
        view = 'home'; renderFromStatus();
      }).catch(function(){ go.disabled = false; phrase.value = ''; pin.value = ''; state.className = 'vw-wh-msg err'; state.textContent = 'Not restored.'; });
    });
    phrase.focus();
  }

  // reopening a pending decision: the SAME review sheet the proposal was born with. If the request
  // is no longer waiting, its transfer row (or its absence) is the honest answer — nothing is minted.
  function openReviewFor(pid){
    var st = lastStatus;
    if (!st) return;
    var row = (st.pending || []).filter(function(x){ return x && x.proposal_id === pid; })[0] || null;
    if (!row) { say('This request is no longer waiting for a decision. Its latest state is in Activity.'); return; }
    var entry = pilotCards[pid];
    if (!entry || !document.body.contains(entry.result)) {
      var a = actions(); var host = a && a.chatLog ? a.chatLog() : null;
      if (!host) { say('The chat log is not available here.'); return; }
      if (!host.querySelector('[data-proposal="' + cssEscape(pid) + '"]')) host.appendChild(pilotCardFor(row));
      entry = pilotCards[pid];
    }
    if (row.pilot_transfer !== false && entry) openSheet(row, entry.controls, entry.result, entry.opener || null);
    else say('This request carries its approval controls on its card in the chat below.');
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
    if (envBadge) envBadge.textContent = (cp.environment_label || '') + (cp.frozen ? ' · payments frozen' : '');
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
