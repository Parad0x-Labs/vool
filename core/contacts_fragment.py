"""The Contacts surface: one overlay, opened from Home and the command palette, to search, read, add, edit and delete saved
people and services, confirm or dismiss suggested destinations, import from Apple Contacts, Google or Microsoft through a
preview the owner chooses from, and put one exact entry into the chat composer.

Protected changes: adding someone new is immediate. A change to a saved contact, a deletion, and a new name that is the same
as or looks like a saved one come back from the doors as a pending change. The owner opens it (Pending changes -- nothing
opens by itself), reads the saved contact as it is and the exact change, the characters and scripts of every name that
looks alike, and confirms with the PIN or password in the review, or cancels. The secret field is cleared before the
request leaves and after every answer. Protection sets, changes or resets the PIN or password; the operating system asks
for its own confirmation, never this page.

Fragment law (core.vool_chat_page._page_fragments): one <style> plus one <script> IIFE exposing ONE namespace,
``window.VoolContacts``, touching the page only through ``window.VoolPageActions``. Every value from the server is written
with textContent; nothing builds markup from data (contact names, notes and imported fields are untrusted text). Reads and
writes go through the owner-local /api/contacts routes (core.command_registry.groups.contacts_group). Nothing here sends,
messages or pays.
"""
from __future__ import annotations

_CSS = r"""
#vcOverlay .vc-modal { width:min(920px,95vw); max-height:90vh; display:flex; flex-direction:column; }
#vcOverlay .vc-head-actions { display:flex; align-items:center; gap:8px; }
#vcOverlay .vc-help { margin:10px 16px 0; }
#vcOverlay .vc-status { min-height:18px; margin:6px 16px 0; font-size:12.5px; color:var(--muted); }
#vcOverlay .vc-status.vc-bad, .vc-form .vc-status.vc-bad { color:var(--bad, #f2545b); }
#vcOverlay .vc-body { display:grid; grid-template-columns:minmax(240px, 320px) 1fr; min-height:440px; overflow:hidden; border-top:1px solid var(--border); margin-top:8px; }
#vcOverlay .vc-side { display:flex; flex-direction:column; border-right:1px solid var(--border); overflow:auto; padding:12px; gap:8px; }
#vcOverlay .vc-search { margin:0; }
#vcOverlay .vc-list { display:flex; flex-direction:column; gap:2px; }
.vc-row { display:flex; flex-direction:column; align-items:flex-start; gap:2px; width:100%; text-align:left; padding:8px 10px; background:transparent; color:var(--ink); border:1px solid transparent; border-radius:9px; cursor:pointer; font:inherit; }
.vc-row:hover, .vc-row:focus-visible { background:var(--active); outline:none; border-color:var(--border); }
.vc-row.vc-on { background:var(--active); border-color:var(--accent); }
.vc-row.vc-pending-row { border-color:var(--warn, #f0b429); }
.vc-name { font-weight:600; font-size:14px; }
.vc-meta { font-size:12px; color:var(--muted); overflow-wrap:anywhere; }
.vc-empty { color:var(--muted); font-size:13px; padding:10px 4px; }
#vcOverlay .vc-detail { padding:16px 18px; overflow:auto; }
.vc-title { margin:0 0 4px; font-size:18px; }
.vc-sub { color:var(--muted); font-size:13px; margin-bottom:10px; }
.vc-notes { white-space:pre-wrap; font-size:13px; margin:0 0 12px; }
.vc-section { font-size:12px; font-weight:700; letter-spacing:.02em; text-transform:uppercase; color:var(--muted); margin:14px 0 6px; }
.vc-endpoints { display:flex; flex-direction:column; gap:10px; }
.vc-endpoint, .vc-suggestion, .vc-step { border:1px solid var(--border); border-radius:11px; padding:10px 12px; background:var(--field); }
.vc-ep-top { display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; }
.vc-kind { font-weight:600; font-size:13px; }
.vc-badge { font-size:11.5px; color:var(--muted); border:1px solid var(--border); border-radius:999px; padding:1px 8px; }
.vc-badge.vc-badge-warn { color:var(--warn, #f0b429); border-color:var(--warn, #f0b429); }
.vc-value { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:13px; margin:6px 0 2px; overflow-wrap:anywhere; word-break:break-all; user-select:all; }
.vc-value.vc-old { color:var(--muted); text-decoration:line-through; }
.vc-arrow { color:var(--muted); font-size:12px; }
.vc-where { font-size:12.5px; color:var(--muted); }
.vc-note { font-size:12px; color:var(--muted); margin-top:4px; }
.vc-note.vc-warn { color:var(--warn, #f0b429); }
.vc-ep-actions, .vc-actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
.vc-actions { margin-top:16px; }
.vc-btn { background:var(--active); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:6px 12px; font:inherit; font-size:13px; cursor:pointer; }
.vc-btn:hover, .vc-btn:focus-visible { border-color:var(--accent); outline:none; }
.vc-btn.vc-primary { background:var(--accent); color:#0b1216; border-color:var(--accent); font-weight:600; }
.vc-btn.vc-danger { color:var(--bad, #f2545b); }
.vc-btn[disabled] { opacity:.55; cursor:default; }
.vc-form label { display:block; font-size:12.5px; color:var(--muted); margin:10px 0 4px; }
.vc-form input, .vc-form select, .vc-form textarea { width:100%; box-sizing:border-box; background:var(--bg); color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:8px 10px; font:inherit; font-size:14px; }
.vc-form textarea { min-height:64px; resize:vertical; }
.vc-form input:focus, .vc-form select:focus, .vc-form textarea:focus { outline:none; border-color:var(--accent); }
.vc-ep-rows { display:flex; flex-direction:column; gap:10px; }
.vc-ep-row { border:1px solid var(--border); border-radius:10px; padding:8px 10px 10px; }
.vc-ep-row.vc-removed { opacity:.5; }
.vc-ep-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:0 10px; }
.vc-confirm { border:1px solid var(--bad, #f2545b); border-radius:11px; padding:12px; margin-top:12px; }
.vc-chooser { position:fixed; left:50%; bottom:110px; transform:translateX(-50%); width:min(460px, 92vw); max-height:60vh; overflow:auto; z-index:60; background:var(--panel-solid); border:1px solid var(--border); border-radius:12px; box-shadow:0 12px 40px rgba(0,0,0,.5); padding:10px; }
.vc-chooser .plugins-search { margin:0 0 8px; }
.vc-chooser-title { font-weight:600; font-size:13px; margin:0 0 8px; }
.vc-import, .vc-review { display:flex; flex-direction:column; gap:10px; }
.vc-card { border:1px solid var(--border); border-radius:11px; padding:10px 12px; background:var(--field); }
.vc-item { border:1px solid var(--border); border-radius:10px; padding:8px 10px; margin-top:8px; }
.vc-counts { font-size:12.5px; color:var(--muted); margin-top:8px; }
.vc-form .vc-check, .vc-check { display:flex; gap:8px; align-items:flex-start; font-size:13px; color:var(--ink); margin:12px 0 4px; }
.vc-form .vc-check input { width:auto; margin:2px 0 0; }
.vc-diff { display:grid; grid-template-columns:minmax(90px, 150px) 1fr; gap:4px 10px; align-items:start; margin-top:8px; }
.vc-diff-label { font-size:12px; color:var(--muted); padding-top:6px; }
.vc-chars { font-size:11.5px; color:var(--muted); font-family:ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap:anywhere; }
.vc-chars summary { cursor:pointer; }
.vc-conflict { border:1px solid var(--warn, #f0b429); border-radius:11px; padding:10px 12px; }
.vc-recovery { border:1px solid var(--accent); border-radius:11px; padding:12px; }
.vc-recovery .vc-value { font-size:16px; letter-spacing:.06em; }
@media (max-width:720px) { #vcOverlay .vc-body { grid-template-columns:1fr; } #vcOverlay .vc-side { border-right:none; border-bottom:1px solid var(--border); max-height:40vh; } .vc-diff { grid-template-columns:1fr; } }
"""

_JS = r"""
(function(){
  'use strict';
  if (window.VoolContacts) return;
  function CT(key, fallback){
    try { if (typeof VOOLT === 'function') { var t = VOOLT(key); if (t && t !== key) return t; } } catch (e) {}
    return fallback;
  }

  var KIND_LABELS = {email: 'Email', phone: 'Phone', messaging: 'Messaging', wallet: 'Wallet address'};
  var VERIFICATION = {user_entered: 'Added by you', user_confirmed: 'Confirmed by you', imported_unverified: 'Imported, not verified'};
  var PROVIDERS = {apple_contacts: 'Apple Contacts', google: 'Google Contacts', microsoft: 'Microsoft Outlook'};
  var ORIGINS = {chat_unconfirmed: 'a chat request (not in your own words)', email_message: 'an email', retrieved_text: 'retrieved text', provider_profile: 'a provider profile', import: 'an import'};
  var STATES = {committed: 'Saved.', cancelled: 'Cancelled; nothing changed.', expired: 'Expired; nothing changed.', stale: 'No longer applies as reviewed; nothing changed.'};
  var state = {contacts: [], selected: null, options: null, suggestions: [], pending: [], credential: null, query: '', view: ''};
  var overlay = null, listEl = null, detailEl = null, searchEl = null, statusEl = null, suggestEl = null, pendingEl = null, chooserEl = null, lastFocus = null;

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = String(text); return n; }
  function button(label, cls, onClick) { var b = el('button', 'vc-btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; b.addEventListener('click', onClick); return b; }
  function actions() { return window.VoolPageActions || null; }
  function say(text, bad) { if (!statusEl) return; statusEl.textContent = text || ''; statusEl.className = 'vc-status' + (bad ? ' vc-bad' : ''); }
  function debounce(fn, ms) { var t = null; return function() { var args = arguments; clearTimeout(t); t = setTimeout(function() { fn.apply(null, args); }, ms); }; }
  function kindLabel(kind) { return KIND_LABELS[kind] || kind || ''; }
  function request(method, url, body) {
    var init = {method: method, headers: {'Accept': 'application/json'}};
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    return fetch(url, init).then(function(r) {
      return r.json().catch(function() { return {}; }).then(function(j) { j = j || {}; return {ok: r.ok && j.ok !== false, status: r.status, body: j}; });
    }).catch(function() { return {ok: false, status: 0, body: {message: 'Contacts could not be reached.'}}; });
  }
  function why(res, fallback) { var b = (res && res.body) || {}; return b.message || b.error || fallback; }
  function copyText(text) {
    var done = function() { var a = actions(); if (a && a.toast) a.toast('Copied.'); };
    try { if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(String(text)).then(done, function(){}); return; } } catch (e) {}
    var area = el('textarea'); area.value = String(text); document.body.appendChild(area); area.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    area.remove();
  }

  function build() {
    overlay = el('div', 'modal-overlay'); overlay.id = 'vcOverlay'; overlay.hidden = true;
    var modal = el('div', 'modal vc-modal'); modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); modal.setAttribute('aria-labelledby', 'vcTitle');
    var head = el('div', 'modal-head'); var title = el('span', null, CT('contacts.title', 'Contacts')); title.id = 'vcTitle'; head.appendChild(title);
    var headActions = el('div', 'vc-head-actions');
    var protectBtn = button(CT('contacts.protection', 'Protection'), '', function() { renderProtection(); }); protectBtn.id = 'vcProtect'; headActions.appendChild(protectBtn);
    var importBtn = button(CT('contacts.import', 'Import'), '', function() { renderImport(); }); importBtn.id = 'vcImport'; headActions.appendChild(importBtn);
    var add = button(CT('contacts.add', 'Add contact'), 'vc-primary', function() { renderForm(null); }); add.id = 'vcAdd'; headActions.appendChild(add);
    var x = el('button', 'modal-x', '×'); x.type = 'button'; x.setAttribute('aria-label', CT('contacts.close_aria', 'Close contacts')); x.addEventListener('click', close); headActions.appendChild(x);
    head.appendChild(headActions); modal.appendChild(head);
    modal.appendChild(el('p', 'set-help vc-help', CT('contacts.help', 'People and services you save once. Email drafts, payment proposals and invitations use the exact entry you pick; nothing here sends or pays. Changing a saved contact asks for your PIN or password.')));
    statusEl = el('div', 'vc-status'); statusEl.setAttribute('role', 'status'); statusEl.setAttribute('aria-live', 'polite'); modal.appendChild(statusEl);
    var body = el('div', 'vc-body'); var side = el('div', 'vc-side');
    searchEl = el('input', 'plugins-search vc-search'); searchEl.type = 'search'; searchEl.id = 'vcSearch'; searchEl.placeholder = CT('contacts.search_placeholder', 'Search by name, alias or address');
    searchEl.setAttribute('aria-label', CT('contacts.search_aria', 'Search contacts')); searchEl.autocomplete = 'off'; searchEl.spellcheck = false;
    searchEl.addEventListener('input', debounce(function() { state.query = searchEl.value.trim(); refresh(); }, 160));
    side.appendChild(searchEl);
    pendingEl = el('div', 'vc-pending'); pendingEl.id = 'vcPending'; side.appendChild(pendingEl);
    suggestEl = el('div', 'vc-suggestions'); suggestEl.id = 'vcSuggestions'; side.appendChild(suggestEl);
    side.appendChild(el('div', 'vc-section', CT('contacts.saved', 'Saved')));
    listEl = el('div', 'vc-list'); listEl.id = 'vcList'; listEl.setAttribute('role', 'list'); side.appendChild(listEl);
    detailEl = el('div', 'vc-detail'); detailEl.id = 'vcDetail';
    body.appendChild(side); body.appendChild(detailEl); modal.appendChild(body); overlay.appendChild(modal);
    overlay.addEventListener('click', function(e) { if (e.target === overlay) close(); });
    overlay.addEventListener('keydown', function(e) { if (e.key === 'Escape') { e.stopPropagation(); close(); } });
    document.body.appendChild(overlay);
  }

  function open(contactId) {
    if (!overlay) build();
    lastFocus = document.activeElement;
    overlay.hidden = false; say(''); state.view = '';
    return refresh().then(function() { if (contactId) select(contactId); if (searchEl) searchEl.focus(); });
  }
  function close() {
    clearSecrets();
    if (overlay) overlay.hidden = true;
    closeChooser();
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
  }
  function isOpen() { return !!overlay && !overlay.hidden; }
  function clearSecrets() {
    if (!overlay) return;
    Array.prototype.forEach.call(overlay.querySelectorAll('input[type=password]'), function(input) { input.value = ''; });
    var code = document.getElementById('vcRecoveryCode'); if (code) code.remove();
  }

  function refresh() {
    return Promise.all([
      request('GET', '/api/contacts' + (state.query ? '?q=' + encodeURIComponent(state.query) : '')),
      request('GET', '/api/contacts/suggestions'),
      state.options ? Promise.resolve(null) : request('GET', '/api/contacts/options'),
      request('GET', '/api/contacts/operations')
    ]).then(function(res) {
      if (!res[0].ok) { say(why(res[0], 'Contacts could not be read.'), true); return; }
      state.contacts = res[0].body.contacts || [];
      state.suggestions = (res[1] && res[1].ok && res[1].body.suggestions) || [];
      if (res[2] && res[2].ok) state.options = res[2].body;
      if (res[3] && res[3].ok) { state.pending = res[3].body.operations || []; state.credential = res[3].body.credential || null; }
      renderList(); renderPending(); renderSuggestions();
      if (!state.selected && !state.view) renderEmptyDetail();
    });
  }

  function renderEmptyDetail() {
    if (!detailEl) return;
    state.view = ''; detailEl.textContent = '';
    detailEl.appendChild(el('div', 'vc-empty', state.contacts.length ? CT('contacts.choose_hint', 'Choose a contact to see every saved entry.') : CT('contacts.empty', 'No contacts yet. Add one here, or ask VOOL in chat to save someone.')));
  }

  function renderList() {
    listEl.textContent = '';
    if (!state.contacts.length) { listEl.appendChild(el('div', 'vc-empty', state.query ? CT('contacts.no_match', 'No saved contact matches.') : CT('contacts.nothing_saved', 'Nothing saved yet.'))); return; }
    state.contacts.forEach(function(c) {
      var row = el('button', 'vc-row' + (c.contact_id === state.selected ? ' vc-on' : '')); row.type = 'button'; row.setAttribute('role', 'listitem');
      row.dataset.contactId = c.contact_id;
      row.appendChild(el('span', 'vc-name', c.display_name));
      var counts = {}; (c.endpoints || []).forEach(function(e) { counts[e.kind] = (counts[e.kind] || 0) + 1; });
      var summary = Object.keys(counts).map(function(k) { return counts[k] + ' ' + kindLabel(k).toLowerCase(); }).join(' · ') || 'no entries';
      row.appendChild(el('span', 'vc-meta', (c.kind === 'service' ? 'Service · ' : '') + summary));
      if (c.match === 'confusable') row.appendChild(el('span', 'vc-meta vc-note vc-warn', 'Looks like your search but is spelled with different characters'));
      row.addEventListener('click', function() { select(c.contact_id); });
      listEl.appendChild(row);
    });
  }

  function renderPending() {
    pendingEl.textContent = '';
    if (!state.pending.length) return;
    pendingEl.appendChild(el('div', 'vc-section', 'Pending changes (' + state.pending.length + ')'));
    state.pending.forEach(function(op) {
      var row = el('button', 'vc-row vc-pending-row'); row.type = 'button'; row.dataset.operationId = op.operation_id;
      row.appendChild(el('span', 'vc-name', op.title));
      row.appendChild(el('span', 'vc-meta', (op.source_label || op.source) + ' · waits for your PIN or password'));
      row.addEventListener('click', function() { renderReview(op); });
      pendingEl.appendChild(row);
    });
  }

  function select(contactId) {
    return request('GET', '/api/contacts/detail?id=' + encodeURIComponent(contactId)).then(function(res) {
      if (!res.ok) { say(why(res, 'That contact could not be read.'), true); return; }
      state.selected = contactId; state.view = ''; renderList(); renderDetail(res.body.contact);
    });
  }

  function verificationText(e) {
    var text = VERIFICATION[e.verification] || e.verification || '';
    if (e.source && e.source.provider) text += ' · from ' + (PROVIDERS[e.source.provider] || e.source.provider) + (e.source.account_label ? ' (' + e.source.account_label + ')' : '');
    return text;
  }

  function charactersBlock(identity, always) {
    // what tells two names apart: every script and code point, never markup
    var box = el('div');
    if (!identity) return box;
    var unusual = identity.mixed_script || identity.non_ascii || identity.visible_characters !== identity.display;
    if (!unusual && !always) return box;
    var parts = [];
    if ((identity.scripts || []).length) parts.push((identity.mixed_script ? 'Mixed scripts: ' : 'Script: ') + identity.scripts.join(', '));
    if (identity.visible_characters !== identity.display) parts.push('Exact characters: ' + identity.visible_characters);
    if (parts.length) box.appendChild(el('div', 'vc-chars', parts.join(' · ')));
    var details = el('details', 'vc-chars'); details.appendChild(el('summary', null, 'Characters'));
    (identity.code_points || []).forEach(function(cp) { details.appendChild(el('div', null, cp)); });
    box.appendChild(details);
    return box;
  }

  function endpointCard(contact, e) {
    var card = el('div', 'vc-endpoint'); card.dataset.endpointId = e.endpoint_id;
    var top = el('div', 'vc-ep-top');
    top.appendChild(el('span', 'vc-kind', kindLabel(e.kind) + (e.label ? ' · ' + e.label : '')));
    top.appendChild(el('span', 'vc-badge' + (e.verification === 'imported_unverified' ? ' vc-badge-warn' : ''), verificationText(e)));
    card.appendChild(top);
    var value = el('div', 'vc-value', e.value); value.setAttribute('translate', 'no'); card.appendChild(value);
    var where = [];
    if (e.kind === 'wallet') where.push((e.network_display || e.chain_network) + (e.chain_environment === 'testnet' ? ' · test network' : ''));
    if (e.kind === 'messaging') { where.push(e.channel_label || e.channel); if (e.provider_account) where.push(e.provider_account); }
    if (where.length) card.appendChild(el('div', 'vc-where', where.join(' · ')));
    if (e.kind === 'messaging' && e.delivery && e.delivery.message) card.appendChild(el('div', 'vc-note', e.delivery.message));
    var bar = el('div', 'vc-ep-actions');
    bar.appendChild(button('Copy', '', function() { copyText(e.value); }));
    bar.appendChild(button('Use in chat', '', function() { insertEndpoint(contact, e); }));
    if (e.verification === 'imported_unverified') bar.appendChild(button('Confirm entry', '', function() { confirmImported(contact, e); }));
    card.appendChild(bar);
    return card;
  }

  function showOutcome(res, doneText) {
    // a door answers applied (with the contact), pending (with its review) or unchanged; never a silent change
    var b = res.body || {};
    if (b.status === 'pending_authentication' || b.status === 'already_pending') { say(b.message || 'This change waits for your PIN or password.'); refresh(); renderReview(b.operation); return; }
    if (b.status === 'unchanged') { say(b.message || 'Nothing changed.'); return; }
    say(doneText(b));
    refresh().then(function() { if (b.contact && b.contact.contact_id) select(b.contact.contact_id); });
  }

  function confirmImported(contact, e) {
    // the owner checks an imported entry: a change to a saved contact, so it is reviewed and confirmed like any other
    return request('POST', '/api/contacts/update', {contact_id: contact.contact_id, expected_revision: contact.revision, confirm_endpoint_ids: [e.endpoint_id]}).then(function(res) {
      if (!res.ok) { say(why(res, 'Nothing changed.'), true); return; }
      showOutcome(res, function() { return 'Confirmed ' + e.value + '.'; });
    });
  }

  function renderDetail(contact) {
    detailEl.textContent = '';
    detailEl.appendChild(el('h3', 'vc-title', contact.display_name));
    detailEl.appendChild(charactersBlock(contact.identity, false));
    var sub = (contact.kind === 'service' ? 'Service' : 'Person') + (contact.aliases && contact.aliases.length ? ' · also known as ' + contact.aliases.join(', ') : '');
    detailEl.appendChild(el('div', 'vc-sub', sub));
    if (contact.notes) detailEl.appendChild(el('p', 'vc-notes', contact.notes));
    detailEl.appendChild(el('div', 'vc-section', 'Entries'));
    var list = el('div', 'vc-endpoints');
    (contact.endpoints || []).forEach(function(e) { list.appendChild(endpointCard(contact, e)); });
    if (!(contact.endpoints || []).length) list.appendChild(el('div', 'vc-empty', CT('contacts.no_entries', 'No entries saved.')));
    detailEl.appendChild(list);
    var bar = el('div', 'vc-actions');
    bar.appendChild(button('Edit', '', function() { renderForm(contact); }));
    bar.appendChild(button('Delete contact', 'vc-danger', function() { confirmDelete(contact); }));
    detailEl.appendChild(bar);
  }

  function field(parent, label, tag, value, id) {
    var l = el('label', null, label); var input = el(tag); if (tag === 'input') input.type = 'text';
    input.value = value || ''; if (id) { input.id = id; l.htmlFor = id; }
    parent.appendChild(l); parent.appendChild(input); return input;
  }
  function secretField(parent, label, id, numeric) {
    var l = el('label', null, label); var input = el('input'); input.type = 'password'; input.id = id; l.htmlFor = id;
    input.autocomplete = 'off'; input.spellcheck = false; if (numeric) input.inputMode = 'numeric';
    parent.appendChild(l); parent.appendChild(input); return input;
  }
  function selectField(parent, label, choices, value, id) {
    var l = el('label', null, label); var select = el('select'); if (id) { select.id = id; l.htmlFor = id; }
    choices.forEach(function(c) { var o = el('option', null, c[1]); o.value = c[0]; if (c[0] === value) o.selected = true; select.appendChild(o); });
    parent.appendChild(l); parent.appendChild(select); return select;
  }

  var rowSeq = 0;
  function endpointRow(e) {
    rowSeq += 1;
    var options = state.options || {kinds: [], channels: [], networks: []};
    var row = el('div', 'vc-ep-row'); if (e) row.dataset.endpointId = e.endpoint_id;
    var grid = el('div', 'vc-ep-grid');
    var kinds = (options.kinds || []).map(function(k) { return [k.id, k.label]; });
    if (!kinds.length) kinds = [['email', 'Email'], ['phone', 'Phone'], ['messaging', 'Messaging'], ['wallet', 'Wallet address']];
    var cell = function() { var d = el('div'); grid.appendChild(d); return d; };
    var kind = selectField(cell(), 'Kind', kinds, e ? e.kind : 'email', 'vcKind' + rowSeq);
    if (e) kind.disabled = true;
    var value = field(cell(), 'Address or identity', 'input', e ? e.value : '', 'vcValue' + rowSeq);
    value.spellcheck = false; value.autocomplete = 'off';
    var label = field(cell(), 'Label (work, personal, …)', 'input', e ? e.label : '', 'vcLabel' + rowSeq);
    var networkCell = cell();
    var networks = (options.networks || []).map(function(n) { return [n.network, n.display_name + (n.environment === 'testnet' ? ' (test network)' : '')]; });
    var network = selectField(networkCell, 'Network', [['', 'Choose the network']].concat(networks), e ? e.chain_network : '', 'vcNetwork' + rowSeq);
    var channelCell = cell();
    var channels = (options.channels || []).map(function(c) { return [c.id, c.label]; });
    var channel = selectField(channelCell, 'Service', [['', 'Choose the service']].concat(channels), e ? e.channel : '', 'vcChannel' + rowSeq);
    var accountCell = cell();
    var account = field(accountCell, 'Account or workspace', 'input', e ? e.provider_account : '', 'vcAccount' + rowSeq);
    var sync = function() {
      networkCell.hidden = kind.value !== 'wallet';
      channelCell.hidden = kind.value !== 'messaging'; accountCell.hidden = kind.value !== 'messaging';
    };
    kind.addEventListener('change', sync); sync();
    row.appendChild(grid);
    var remove = button(e ? 'Remove this entry' : 'Discard', 'vc-danger', function() {
      if (!e) { row.remove(); return; }
      var removing = !row.classList.contains('vc-removed'); row.classList.toggle('vc-removed', removing);
      remove.textContent = removing ? 'Keep this entry' : 'Remove this entry';
    });
    row.appendChild(remove);
    row._read = function() {
      return {endpoint_id: e ? e.endpoint_id : '', removed: row.classList.contains('vc-removed'), kind: kind.value, value: value.value.trim(), label: label.value.trim(),
              network: kind.value === 'wallet' ? network.value : '', channel: kind.value === 'messaging' ? channel.value : '', account: kind.value === 'messaging' ? account.value.trim() : '', original: e};
    };
    return row;
  }

  function splitAliases(text) { return String(text || '').split(',').map(function(s) { return s.trim(); }).filter(Boolean); }

  function renderForm(contact) {
    if (!overlay) build();
    state.view = 'form'; detailEl.textContent = '';
    var form = el('form', 'vc-form'); form.noValidate = true; form.id = 'vcForm';
    form.appendChild(el('h3', 'vc-title', contact ? 'Edit ' + contact.display_name : 'Add contact'));
    if (contact) form.appendChild(el('div', 'vc-note', 'Saving shows you the exact change first; it applies only after your PIN or password.'));
    var name = field(form, 'Name', 'input', contact ? contact.display_name : '', 'vcName');
    var kind = selectField(form, 'Type', [['person', 'Person'], ['service', 'Service']], contact ? contact.kind : 'person', 'vcType');
    var aliases = field(form, 'Other names (comma separated)', 'input', contact ? (contact.aliases || []).join(', ') : '', 'vcAliases');
    var notes = field(form, 'Notes', 'textarea', contact ? contact.notes : '', 'vcNotes');
    form.appendChild(el('div', 'vc-section', 'Entries'));
    var rows = el('div', 'vc-ep-rows'); form.appendChild(rows);
    var existing = contact ? (contact.endpoints || []) : [];
    existing.forEach(function(e) { rows.appendChild(endpointRow(e)); });
    if (!existing.length) rows.appendChild(endpointRow(null));
    var addEntry = button('Add an entry', '', function() { var r = endpointRow(null); rows.appendChild(r); });
    form.appendChild(addEntry);
    var err = el('div', 'vc-status'); err.setAttribute('role', 'alert'); form.appendChild(err);
    var bar = el('div', 'vc-actions');
    var save = el('button', 'vc-btn vc-primary', contact ? CT('contacts.review_changes', 'Review changes') : CT('contacts.save', 'Save contact')); save.type = 'submit'; save.id = 'vcSave'; bar.appendChild(save);
    bar.appendChild(button('Cancel', '', function() { if (contact) select(contact.contact_id); else { state.selected = null; renderEmptyDetail(); } }));
    form.appendChild(bar);
    form.addEventListener('submit', function(ev) {
      ev.preventDefault();
      save.disabled = true; err.textContent = ''; err.className = 'vc-status';
      var entries = Array.prototype.map.call(rows.children, function(r) { return r._read ? r._read() : null; }).filter(Boolean);
      var promise;
      if (!contact) {
        promise = request('POST', '/api/contacts/create', {
          display_name: name.value.trim(), kind: kind.value, aliases: splitAliases(aliases.value), notes: notes.value,
          endpoints: entries.filter(function(x) { return x.value; }).map(function(x) { return {kind: x.kind, value: x.value, label: x.label, network: x.network, channel: x.channel, account: x.account}; })
        });
      } else {
        var before = contact.aliases || []; var after = splitAliases(aliases.value);
        var payload = {contact_id: contact.contact_id, expected_revision: contact.revision,
          add_aliases: after.filter(function(a) { return before.indexOf(a) < 0; }), remove_aliases: before.filter(function(a) { return after.indexOf(a) < 0; }),
          add_endpoints: [], change_endpoints: [], remove_endpoint_ids: []};
        if (name.value.trim() !== contact.display_name) payload.display_name = name.value.trim();
        if (kind.value !== contact.kind) payload.kind = kind.value;
        if (notes.value !== (contact.notes || '')) payload.notes = notes.value;
        entries.forEach(function(x) {
          if (x.original) {
            if (x.removed) { payload.remove_endpoint_ids.push(x.endpoint_id); return; }
            var o = x.original;
            if (x.value !== o.value || x.label !== (o.label || '') || x.network !== (o.chain_network || '') || x.channel !== (o.channel || '') || x.account !== (o.provider_account || '')) {
              payload.change_endpoints.push({endpoint_id: x.endpoint_id, value: x.value, label: x.label, network: x.network, channel: x.channel, account: x.account});
            }
          } else if (x.value) {
            payload.add_endpoints.push({kind: x.kind, value: x.value, label: x.label, network: x.network, channel: x.channel, account: x.account});
          }
        });
        promise = request('POST', '/api/contacts/update', payload);
      }
      promise.then(function(res) {
        save.disabled = false;
        if (!res.ok) { err.textContent = why(res, 'Nothing was saved.'); err.className = 'vc-status vc-bad'; return; }
        var saved = res.body.contact || {};
        if (res.body.status === 'committed') state.selected = saved.contact_id || null;
        showOutcome(res, function() { return 'Saved ' + (saved.display_name || '') + '.'; });
      });
    });
    detailEl.appendChild(form); name.focus();
  }

  function confirmDelete(contact) {
    var box = el('div', 'vc-confirm'); box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-label', 'Delete ' + contact.display_name);
    var count = (contact.endpoints || []).length;
    box.appendChild(el('div', 'vc-name', 'Delete ' + contact.display_name + '?'));
    box.appendChild(el('div', 'vc-note', 'Its name, other names, notes and ' + count + ' saved ' + (count === 1 ? 'entry are' : 'entries are') + ' erased from Contacts after you review it and confirm with your PIN or password. Drafts, payment proposals and receipts that already recorded an address keep their own record, and anything not yet approved asks again.'));
    var bar = el('div', 'vc-ep-actions');
    var yes = button('Review deletion', 'vc-danger', function() {
      yes.disabled = true;
      request('POST', '/api/contacts/delete', {contact_id: contact.contact_id, expected_revision: contact.revision, confirm: true}).then(function(res) {
        yes.disabled = false;
        if (!res.ok) { say(why(res, 'The contact was not deleted.'), true); return; }
        showOutcome(res, function() { return 'Deleted ' + contact.display_name + '.'; });
      });
    });
    bar.appendChild(yes); bar.appendChild(button('Cancel', '', function() { box.remove(); }));
    box.appendChild(bar); detailEl.appendChild(box); yes.focus();
  }

  // --- pending changes: the review the owner opens, and the only place the PIN or password is typed --------------------

  function recordLine(parent, r, cls) {
    if (!r) return;
    parent.appendChild(el('div', 'vc-kind', kindLabel(r.kind) + (r.label ? ' · ' + r.label : '')));
    var v = el('div', 'vc-value' + (cls ? ' ' + cls : ''), r.value); v.setAttribute('translate', 'no'); parent.appendChild(v);
    var where = [];
    if (r.network_display) where.push(r.network_display + (r.chain_environment === 'testnet' ? ' · test network' : ''));
    if (r.channel_label) where.push(r.channel_label);
    if (r.provider_account) where.push(r.provider_account);
    if (where.length) parent.appendChild(el('div', 'vc-where', where.join(' · ')));
  }

  function diffRow(box, label, before, after) {
    var grid = el('div', 'vc-diff');
    grid.appendChild(el('div', 'vc-diff-label', label));
    var cell = el('div');
    if (before) { before(cell); }
    if (before && after) cell.appendChild(el('div', 'vc-arrow', 'becomes'));
    if (after) { after(cell); }
    grid.appendChild(cell); box.appendChild(grid);
  }

  function nameCell(text, identity, old) { return function(cell) { cell.appendChild(el('div', 'vc-value' + (old ? ' vc-old' : ''), text)); cell.appendChild(charactersBlock(identity, false)); }; }

  function changeView(box, c) {
    var b = c.before || {}, a = c.after || {};
    var line = function(r, old) { return function(cell) { recordLine(cell, r, old ? 'vc-old' : ''); }; };
    if (c.action === 'renamed') diffRow(box, 'Name', nameCell(c.before, c.before_identity, true), nameCell(c.after, c.after_identity, false));
    else if (c.action === 'kind_changed') diffRow(box, 'Type', function(cell) { cell.appendChild(el('div', 'vc-value vc-old', c.before)); }, function(cell) { cell.appendChild(el('div', 'vc-value', c.after)); });
    else if (c.action === 'notes_changed') diffRow(box, 'Notes', function(cell) { cell.appendChild(el('div', 'vc-notes vc-old', c.before || '(none)')); }, function(cell) { cell.appendChild(el('div', 'vc-notes', c.after || '(none)')); });
    else if (c.action === 'alias_added') diffRow(box, 'Add other name', null, nameCell(c.alias, c.identity, false));
    else if (c.action === 'alias_removed') diffRow(box, 'Remove other name', nameCell(c.alias, c.identity, true), null);
    else if (c.action === 'endpoint_added') diffRow(box, 'Add ' + kindLabel(a.kind).toLowerCase(), null, line(a, false));
    else if (c.action === 'endpoint_removed') diffRow(box, 'Remove ' + kindLabel(b.kind).toLowerCase(), line(b, true), null);
    else if (c.action === 'endpoint_changed') diffRow(box, 'Change ' + kindLabel(b.kind).toLowerCase(), line(b, true), line(a, false));
    else if (c.action === 'endpoint_relabelled') diffRow(box, 'Change label', function(cell) { cell.appendChild(el('div', 'vc-value vc-old', b.label || '(no label)')); }, function(cell) { cell.appendChild(el('div', 'vc-value', a.label || '(no label)')); cell.appendChild(el('div', 'vc-where', 'on ' + (b.value || ''))); });
    else if (c.action === 'endpoint_confirmed') diffRow(box, 'Mark as confirmed', null, line(b, false));
  }

  function stepView(step) {
    var box = el('div', 'vc-step'); box.dataset.action = step.action;
    if (step.action === 'create') {
      box.appendChild(el('div', 'vc-kind', 'New ' + (step.kind === 'service' ? 'service' : 'contact')));
      diffRow(box, 'Name', null, nameCell(step.display_name, step.identity, false));
      (step.aliases || []).forEach(function(al) { diffRow(box, 'Other name', null, nameCell(al.alias, al.identity, false)); });
      (step.endpoints || []).forEach(function(r) { diffRow(box, 'Entry', null, function(cell) { recordLine(cell, r, ''); }); });
      if (step.notes) diffRow(box, 'Notes', null, function(cell) { cell.appendChild(el('div', 'vc-notes', step.notes)); });
    } else if (step.action === 'update') {
      box.appendChild(el('div', 'vc-kind', 'Saved contact: ' + step.display_name));
      box.appendChild(charactersBlock(step.identity, false));
      if (step.current_revision !== null && step.current_revision !== step.reviewed_revision) box.appendChild(el('div', 'vc-note vc-warn', 'This contact changed after this was proposed; the change cannot be applied as shown.'));
      (step.changes || []).forEach(function(c) { changeView(box, c); });
    } else if (step.action === 'delete') {
      box.appendChild(el('div', 'vc-kind', 'Delete ' + step.display_name));
      box.appendChild(charactersBlock(step.identity, false));
      (step.aliases || []).forEach(function(al) { diffRow(box, 'Other name', function(cell) { cell.appendChild(el('div', 'vc-value vc-old', al)); }, null); });
      (step.endpoints || []).forEach(function(r) { diffRow(box, 'Erased entry', function(cell) { recordLine(cell, r, 'vc-old'); }, null); });
    } else if (step.action === 'accept_suggestion') {
      var s = step.suggestion || {};
      box.appendChild(el('div', 'vc-kind', 'Take a suggested ' + kindLabel(s.kind).toLowerCase() + ' from ' + (ORIGINS[s.origin] || s.origin || 'another source')));
    }
    return box;
  }

  function conflictView(c) {
    var box = el('div', 'vc-conflict');
    box.appendChild(el('div', 'vc-note vc-warn', c.explanation));
    diffRow(box, 'In this change', null, nameCell(c.text, c.proposed, false));
    if (c.existing && c.existing.display_name) {
      diffRow(box, 'Already saved', null, function(cell) {
        cell.appendChild(el('div', 'vc-value', c.existing.display_name)); cell.appendChild(charactersBlock(c.existing.identity, true));
        (c.existing.endpoints || []).forEach(function(r) { recordLine(cell, {kind: r.kind, label: r.label, value: r.value, network_display: r.network_display, channel_label: r.channel_label, provider_account: r.provider_account}, ''); });
      });
    } else if (c.with === 'recently_deleted') {
      box.appendChild(el('div', 'vc-note', 'The deleted contact’s name is not kept; only that it matched.'));
    }
    return box;
  }

  function attemptsText(body) {
    var d = (body && body.details) || {};
    if (d.retry_after_seconds) return ' Try again in ' + d.retry_after_seconds + ' seconds.';
    if (typeof d.attempts_before_lock === 'number') return ' ' + d.attempts_before_lock + ' attempt' + (d.attempts_before_lock === 1 ? '' : 's') + ' left before a pause.';
    return '';
  }

  function afterCommit(body) {
    var result = (body && body.result) || {};
    var touched = ((result.created || [])[0] || (result.updated || [])[0] || {}).contact_id;
    state.view = ''; state.selected = touched || null;
    return refresh().then(function() { if (touched) select(touched); else renderEmptyDetail(); });
  }

  function cancelOperation(op) {
    request('POST', '/api/contacts/operations/cancel', {operation_id: op.operation_id}).then(function(res) {
      if (!res.ok) { say(why(res, 'The change was not cancelled.'), true); return; }
      say('Cancelled. Nothing changed.'); state.view = ''; refresh().then(renderEmptyDetail);
    });
  }

  function confirmForm(op) {
    var form = el('form', 'vc-form'); form.noValidate = true; form.id = 'vcConfirmForm';
    var cred = state.credential || {};
    var bar = el('div', 'vc-actions');
    var cancel = button('Cancel this change', 'vc-danger', function() { cancelOperation(op); }); cancel.id = 'vcCancelChange';
    if (!cred.enrolled) {
      form.appendChild(el('div', 'vc-note vc-warn', 'Set up a PIN or password first (' + (cred.setup_path || 'Protection') + '). Until then this change waits and nothing is changed.'));
      bar.appendChild(button('Set up protection', '', function() { renderProtection(); })); bar.appendChild(cancel); form.appendChild(bar);
      return form;
    }
    var pin = secretField(form, cred.kind === 'password' ? 'Your password' : 'Your PIN', 'vcPin', cred.kind !== 'password');
    var err = el('div', 'vc-status'); err.setAttribute('role', 'alert'); form.appendChild(err);
    var go = el('button', 'vc-btn vc-primary', 'Confirm this change'); go.type = 'submit'; go.id = 'vcConfirmChange';
    bar.appendChild(go); bar.appendChild(cancel);
    bar.appendChild(button('Close', '', function() { pin.value = ''; state.view = ''; renderEmptyDetail(); }));
    form.appendChild(bar);
    form.addEventListener('submit', function(ev) {
      ev.preventDefault();
      var secret = pin.value; pin.value = '';
      if (!secret) { err.textContent = 'Enter your PIN or password.'; err.className = 'vc-status vc-bad'; return; }
      go.disabled = true; err.textContent = ''; err.className = 'vc-status';
      var sent = request('POST', '/api/contacts/operations/confirm', {operation_id: op.operation_id, digest: op.digest, secret: secret});
      secret = '';
      sent.then(function(res) {
        go.disabled = false; pin.value = '';
        if (res.status === 503 && res.body.retry) {
          err.textContent = why(res, 'The change was not saved yet.'); err.className = 'vc-status vc-bad';
          var retry = button('Save it again', 'vc-primary', function() {
            retry.disabled = true;
            request('POST', '/api/contacts/operations/commit', res.body.retry).then(function(again) {
              retry.remove();
              if (!again.ok) { err.textContent = why(again, 'Nothing changed.'); return; }
              say('Saved: ' + op.title + '.'); afterCommit(again.body);
            });
          });
          bar.appendChild(retry); return;
        }
        if (!res.ok) { err.textContent = why(res, 'Nothing changed.') + attemptsText(res.body); err.className = 'vc-status vc-bad'; if (res.body.reason && res.body.reason.indexOf('operation_') === 0) refresh(); return; }
        say('Saved: ' + op.title + '.'); afterCommit(res.body);
      });
    });
    setTimeout(function() { try { pin.focus(); } catch (e) {} }, 0);
    return form;
  }

  function renderReview(op) {
    if (!overlay) build();
    if (!op) return;
    state.view = 'review'; state.selected = null; renderList();
    detailEl.textContent = '';
    var box = el('div', 'vc-review'); box.id = 'vcReview'; box.dataset.operationId = op.operation_id;
    box.appendChild(el('h3', 'vc-title', op.title));
    box.appendChild(el('div', 'vc-sub', 'Asked by: ' + (op.source_label || op.source) + (op.state !== 'pending' ? ' · ' + (STATES[op.state] || op.state) : '')));
    (op.reason_text || []).forEach(function(t) { box.appendChild(el('div', 'vc-note vc-warn', 'Needs your PIN or password: ' + t)); });
    (op.steps || []).forEach(function(step) { box.appendChild(stepView(step)); });
    var conflicts = (op.identity && op.identity.conflicts) || [];
    if (conflicts.length) { box.appendChild(el('div', 'vc-section', 'Names that look alike')); conflicts.forEach(function(c) { box.appendChild(conflictView(c)); }); }
    ((op.identity && op.identity.similar) || []).forEach(function(s) { box.appendChild(el('div', 'vc-note', s.text + ' is spelled close to the saved contact ' + s.display_name + '. That alone does not make them the same person.')); });
    box.appendChild(el('div', 'vc-note', op.note || ''));
    if (op.state === 'pending') box.appendChild(confirmForm(op));
    detailEl.appendChild(box);
  }

  function openOperation(operationId) {
    return request('GET', '/api/contacts/operations/detail?id=' + encodeURIComponent(operationId)).then(function(res) {
      if (!res.ok) { say(why(res, 'That change could not be read.'), true); return; }
      if (res.body.credential) state.credential = res.body.credential;
      renderReview(res.body.operation);
    });
  }

  // --- protection: set, change or reset the PIN or password; the operating system confirms it is you -------------------

  function secretPair(form, label, idBase, numeric) {
    return [secretField(form, label, idBase, numeric), secretField(form, 'Type it again', idBase + 'Repeat', numeric)];
  }

  function showRecovery(panel, code, note) {
    var old = document.getElementById('vcRecoveryCode'); if (old) old.remove();
    var box = el('div', 'vc-recovery'); box.id = 'vcRecoveryCode';
    box.appendChild(el('div', 'vc-kind', 'Recovery code'));
    box.appendChild(el('div', 'vc-value', code));
    box.appendChild(el('div', 'vc-note', note));
    box.appendChild(button('I saved it', 'vc-primary', function() { box.remove(); }));
    panel.insertBefore(box, panel.firstChild.nextSibling);
  }

  function protectionForm(panel, cred, mode) {
    var form = el('form', 'vc-form vc-card'); form.noValidate = true; form.id = 'vcProtect' + mode;
    var titles = {Enroll: 'Set up a PIN or password', Change: 'Change your PIN or password', Reset: 'Forgot it? Reset with your recovery code'};
    form.appendChild(el('div', 'vc-kind', titles[mode]));
    var kind = selectField(form, 'Kind', [['pin', 'PIN (' + ((cred.pin_digits || [6, 12]).join(' to ')) + ' digits)'], ['password', 'Password (' + ((cred.password_chars || [10, 128]).join(' to ')) + ' characters)']], cred.kind || 'pin', 'vcProtectKind' + mode);
    var current = mode === 'Change' ? secretField(form, 'Current PIN or password', 'vcCurrentSecret', false) : null;
    var recovery = mode === 'Reset' ? field(form, 'Recovery code', 'input', '', 'vcRecoveryInput') : null;
    if (recovery) { recovery.autocomplete = 'off'; recovery.spellcheck = false; }
    var pair = secretPair(form, mode === 'Enroll' ? 'New PIN or password' : 'New PIN or password', 'vcNewSecret' + mode, false);
    var err = el('div', 'vc-status'); err.setAttribute('role', 'alert'); form.appendChild(err);
    var go = el('button', 'vc-btn' + (mode === 'Reset' ? '' : ' vc-primary'), mode === 'Enroll' ? 'Set up' : mode === 'Change' ? 'Change' : 'Reset'); go.type = 'submit'; go.id = 'vcProtectGo' + mode;
    var bar = el('div', 'vc-ep-actions'); bar.appendChild(go); form.appendChild(bar);
    form.addEventListener('submit', function(ev) {
      ev.preventDefault(); err.textContent = ''; err.className = 'vc-status';
      var first = pair[0].value, second = pair[1].value, now = current ? current.value : '', code = recovery ? recovery.value : '';
      pair[0].value = ''; pair[1].value = ''; if (current) current.value = ''; if (recovery) recovery.value = '';
      if (!first || first !== second) { err.textContent = 'The two entries are not the same; nothing changed.'; err.className = 'vc-status vc-bad'; return; }
      go.disabled = true;
      err.textContent = 'This Mac asks you to confirm it is you in its own window.';
      var body = {kind: kind.value};
      var url = '/api/contacts/credential/enroll';
      if (mode === 'Enroll') body.secret = first;
      if (mode === 'Change') { url = '/api/contacts/credential/change'; body.current_secret = now; body.new_secret = first; }
      if (mode === 'Reset') { url = '/api/contacts/credential/reset'; body.recovery_code = code; body.new_secret = first; }
      var sent = request('POST', url, body);
      first = ''; second = ''; now = ''; code = ''; body = null;
      sent.then(function(res) {
        go.disabled = false;
        if (!res.ok) { err.textContent = why(res, 'Nothing changed.') + attemptsText(res.body); err.className = 'vc-status vc-bad'; return; }
        err.textContent = '';
        say(mode === 'Enroll' ? 'Protection is set up.' : mode === 'Change' ? 'Changed.' : 'Reset.');
        renderProtection().then(function(panelNow) { if (res.body.recovery_code && panelNow) showRecovery(panelNow, res.body.recovery_code, res.body.recovery_note || ''); });
      });
    });
    return form;
  }

  function renderProtection() {
    if (!overlay) build();
    state.view = 'protection'; state.selected = null; renderList();
    detailEl.textContent = '';
    var panel = el('div', 'vc-import'); panel.id = 'vcProtection';
    panel.appendChild(el('h3', 'vc-title', 'Protection'));
    panel.appendChild(el('div', 'vc-note', 'A PIN or password protects every change to a saved contact, every deletion, and every new name that is the same as or looks like a saved one. Adding someone new does not need it. It never approves a payment, an email or an invitation. Setting, changing or resetting it asks this Mac to confirm it is you in its own window.'));
    detailEl.appendChild(panel);
    return request('GET', '/api/contacts/credential').then(function(res) {
      var cred = res.ok ? res.body : {}; state.credential = cred;
      panel.appendChild(el('div', 'vc-sub', cred.enrolled ? ('Set: a ' + (cred.kind === 'password' ? 'password' : 'PIN') + (cred.retry_after_seconds ? ' · paused for ' + cred.retry_after_seconds + ' seconds after wrong attempts' : '')) : 'Not set up yet.'));
      if (!cred.enrolled) panel.appendChild(protectionForm(panel, cred, 'Enroll'));
      else { panel.appendChild(protectionForm(panel, cred, 'Change')); panel.appendChild(protectionForm(panel, cred, 'Reset')); }
      return panel;
    });
  }

  function renderSuggestions() {
    suggestEl.textContent = '';
    if (!state.suggestions.length) return;
    suggestEl.appendChild(el('div', 'vc-section', 'Waiting for your confirmation (' + state.suggestions.length + ')'));
    state.suggestions.forEach(function(s) {
      var card = el('div', 'vc-suggestion'); card.dataset.suggestionId = s.suggestion_id;
      card.appendChild(el('div', 'vc-name', s.contact_display_name || s.display_name || 'A new contact'));
      var what = kindLabel(s.kind).toLowerCase() + (s.network_display ? ' on ' + s.network_display : '') + (s.channel_label ? ' on ' + s.channel_label : '');
      card.appendChild(el('div', 'vc-meta', (s.replaces ? 'Replace the saved ' : 'Add ') + what + ' · from ' + (ORIGINS[s.origin] || s.origin)));
      if (s.replaces) { card.appendChild(el('div', 'vc-value vc-old', s.replaces.value)); card.appendChild(el('div', 'vc-arrow', 'becomes')); }
      card.appendChild(el('div', 'vc-value', s.value));
      card.appendChild(el('div', 'vc-note vc-warn', 'Compare every character with a source you trust before taking it.'));
      var bar = el('div', 'vc-ep-actions');
      bar.appendChild(button(s.contact_display_name ? 'Review' : 'Take it', '', function() { decide(s, 'accept'); }));
      bar.appendChild(button('Dismiss', '', function() { decide(s, 'dismiss'); }));
      card.appendChild(bar); suggestEl.appendChild(card);
    });
  }

  function decide(s, how) {
    var body = {suggestion_id: s.suggestion_id};
    request('POST', '/api/contacts/suggestions/' + how, body).then(function(res) {
      if (!res.ok) { say(why(res, 'Nothing changed.'), true); return; }
      if (how === 'dismiss') { say('Dismissed.'); refresh(); return; }
      showOutcome(res, function() { return 'Saved.'; });
    });
  }

  function referenceFor(contact, e) {
    // a name the resolver maps back to exactly this entry keeps the Contacts binding; otherwise the exact value is used
    var name = contact.display_name + (e.label ? ' (' + e.label + ')' : '');
    var url = '/api/contacts/resolve?name=' + encodeURIComponent(name) + '&kind=' + encodeURIComponent(e.kind)
      + (e.kind === 'wallet' && e.chain_network ? '&network=' + encodeURIComponent(e.chain_network) : '')
      + (e.kind === 'messaging' && e.channel ? '&channel=' + encodeURIComponent(e.channel) : '');
    return request('GET', url).then(function(res) {
      var snapshot = res.ok && res.body && res.body.snapshot;
      return snapshot && snapshot.endpoint_id === e.endpoint_id ? name : e.value;
    });
  }

  function insertEndpoint(contact, e) {
    return referenceFor(contact, e).then(function(text) {
      var a = actions();
      if (a && a.insertComposerText) { a.insertComposerText(text); close(); if (a.toast) a.toast('Added ' + text + ' to your message.'); }
      else copyText(text);
      return text;
    });
  }

  function closeChooser() { if (chooserEl) { chooserEl.remove(); chooserEl = null; } }

  function choose(opts) {
    opts = opts || {};
    return new Promise(function(resolve) {
      closeChooser();
      chooserEl = el('div', 'vc-chooser'); chooserEl.id = 'vcChooser'; chooserEl.setAttribute('role', 'dialog'); chooserEl.setAttribute('aria-label', 'Choose a saved entry');
      chooserEl.appendChild(el('div', 'vc-chooser-title', opts.kind ? 'Choose a saved ' + kindLabel(opts.kind).toLowerCase() : 'Choose a saved entry'));
      var input = el('input', 'plugins-search'); input.type = 'search'; input.placeholder = 'Find a contact'; input.setAttribute('aria-label', 'Find a contact'); chooserEl.appendChild(input);
      var results = el('div', 'vc-list'); chooserEl.appendChild(results);
      var finish = function(value) { closeChooser(); resolve(value); };
      var render = function() {
        var q = input.value.trim();
        request('GET', '/api/contacts' + (q ? '?q=' + encodeURIComponent(q) : '')).then(function(res) {
          results.textContent = '';
          var items = [];
          ((res.ok && res.body.contacts) || []).forEach(function(c) {
            (c.endpoints || []).forEach(function(e) {
              if (opts.kind && e.kind !== opts.kind) return;
              if (opts.network && e.network !== opts.network) return;
              items.push({contact: c, endpoint: e});
            });
          });
          if (!items.length) { results.appendChild(el('div', 'vc-empty', 'No matching saved entry.')); return; }
          items.slice(0, 40).forEach(function(item) {
            var b = el('button', 'vc-row'); b.type = 'button';
            b.appendChild(el('span', 'vc-name', item.contact.display_name + (item.endpoint.label ? ' · ' + item.endpoint.label : '')));
            b.appendChild(el('span', 'vc-meta', kindLabel(item.endpoint.kind) + ' ' + item.endpoint.value + (item.endpoint.network_display ? ' · ' + item.endpoint.network_display : '')));
            if (item.contact.match === 'confusable') b.appendChild(el('span', 'vc-meta vc-note vc-warn', 'Looks like your search but is spelled with different characters'));
            b.addEventListener('click', function() { finish(item); });
            results.appendChild(b);
          });
        });
      };
      input.addEventListener('input', debounce(render, 140));
      chooserEl.addEventListener('keydown', function(e) { if (e.key === 'Escape') { e.stopPropagation(); finish(null); } });
      document.body.appendChild(chooserEl); input.focus(); render();
    });
  }

  function chooseIntoComposer(opts) {
    return choose(opts).then(function(item) {
      if (!item) return null;
      var endpoint = item.endpoint;
      var contact = {display_name: item.contact.display_name};
      var full = {endpoint_id: endpoint.endpoint_id, kind: endpoint.kind, label: endpoint.label, value: endpoint.value, chain_network: endpoint.network, channel: endpoint.channel};
      return insertEndpoint(contact, full);
    });
  }

  var IMPORT_CATEGORIES = {'new': 'New contact', adds_to_existing: 'Adds entries to a saved contact', same_name_different_entries: 'Same name as a saved contact, different entries',
    looks_like_saved_contact: 'Looks like a saved contact’s name', already_saved: 'Already saved', no_usable_entries: 'Nothing usable to import', no_name: 'No name'};
  var SOURCE_STATES = {connected: 'Connected', attention: 'Needs attention', removed: 'Removed'};
  var AUTH_STATES = {authorized: 'Access allowed', ready: 'Ready', denied: 'Access not allowed', restricted: 'Access restricted on this Mac',
    not_determined: 'macOS asks for access on the first preview', unavailable: 'Not available in this build', binding_required: 'Needs a verified account connection'};
  function humanReason(reason) { return String(reason || '').split('_').join(' '); }
  function providerById(list, id) { return (list || []).filter(function(p) { return p.provider === id; })[0] || {}; }

  function renderImport() {
    if (!overlay) build();
    state.view = 'import'; state.selected = null; renderList();
    detailEl.textContent = '';
    var panel = el('div', 'vc-import'); panel.id = 'vcImportPanel';
    panel.appendChild(el('h3', 'vc-title', 'Import contacts'));
    panel.appendChild(el('div', 'vc-note', 'VOOL reads the account once to show you a preview. Nothing is saved until you choose, nothing in the source account is changed, and imported entries stay marked as imported and not verified. Adding to a saved contact, or a name that looks like a saved one, waits for your PIN or password.'));
    var body = el('div', 'vc-import'); body.appendChild(el('div', 'vc-empty', 'Reading import sources…'));
    panel.appendChild(body); detailEl.appendChild(panel);
    return request('GET', '/api/contacts/import/sources').then(function(res) {
      body.textContent = '';
      if (!res.ok) { body.appendChild(el('div', 'vc-note vc-warn', why(res, 'Import sources could not be read.'))); return; }
      body.appendChild(importConnectForm(res.body));
      body.appendChild(el('div', 'vc-section', 'Sources'));
      var list = el('div', 'vc-endpoints'); list.id = 'vcImportSources';
      var sources = (res.body.sources || []).filter(function(s) { return s.status !== 'removed'; });
      if (!sources.length) list.appendChild(el('div', 'vc-empty', 'No source connected yet.'));
      sources.forEach(function(s) { list.appendChild(importSourceCard(s)); });
      body.appendChild(list);
    });
  }

  function importConnectForm(data) {
    var providers = data.providers || [], bindings = data.bindings || [];
    var form = el('form', 'vc-form vc-card'); form.noValidate = true; form.id = 'vcImportForm';
    form.appendChild(el('div', 'vc-kind', 'Connect a source'));
    var provider = selectField(form, 'Import from', providers.map(function(p) { return [p.provider, p.label]; }), providers.length ? providers[0].provider : '', 'vcImportProvider');
    var auth = el('div', 'vc-note'); form.appendChild(auth);
    var bindingCell = el('div'); form.appendChild(bindingCell);
    var binding = selectField(bindingCell, 'Account connection', [], '', 'vcImportBinding');
    var label = field(form, 'Name for this source (optional)', 'input', '', 'vcImportAccount');
    var consentRow = el('label', 'vc-check'); consentRow.htmlFor = 'vcImportConsent';
    var consent = el('input'); consent.type = 'checkbox'; consent.id = 'vcImportConsent';
    consentRow.appendChild(consent); consentRow.appendChild(el('span', null, 'Allow VOOL to read the contacts in this account for a preview. Nothing is imported until I choose.'));
    form.appendChild(consentRow);
    var err = el('div', 'vc-status'); err.setAttribute('role', 'alert'); form.appendChild(err);
    var go = el('button', 'vc-btn vc-primary', 'Connect'); go.type = 'submit'; go.id = 'vcImportConnect';
    var bar = el('div', 'vc-ep-actions'); bar.appendChild(go); form.appendChild(bar);
    var sync = function() {
      var p = providerById(providers, provider.value), a = p.authorization || {};
      bindingCell.hidden = !p.needs_binding;
      // only a connection stored for this source is offered; the server refuses any other one
      var usable = bindings.filter(function(b) { return (p.binding_providers || []).indexOf(b.provider_id) >= 0; });
      binding.textContent = '';
      [['', usable.length ? 'Choose a verified connection' : 'No verified connection for ' + (p.label || 'this source') + ' yet']].concat(usable.map(function(b) {
        return [b.binding_id, (b.provider_label || b.provider_id || '') + (b.account ? ' · ' + b.account : '')];
      })).forEach(function(c) { var o = el('option', null, c[1]); o.value = c[0]; binding.appendChild(o); });
      auth.textContent = [AUTH_STATES[a.state] || a.state || '', a.recovery || ''].filter(Boolean).join(' · ');
      auth.className = 'vc-note' + (a.recovery ? ' vc-warn' : '');
    };
    provider.addEventListener('change', sync); sync();
    form.addEventListener('submit', function(ev) {
      ev.preventDefault(); err.textContent = ''; err.className = 'vc-status';
      var p = providerById(providers, provider.value);
      go.disabled = true;
      // consent is decided by the server door: an unticked box is sent as false and refused there
      request('POST', '/api/contacts/import/connect', {provider: provider.value, consent: consent.checked === true, account_label: label.value.trim(), auth_binding: p.needs_binding ? binding.value : ''}).then(function(res) {
        go.disabled = false;
        if (!res.ok) { err.textContent = why(res, 'Nothing was connected.'); err.className = 'vc-status vc-bad'; return; }
        var s = res.body.source || {};
        say('Connected ' + (s.account_label || s.provider_label || 'the source') + '. Preview it to choose what to import.');
        renderImport();
      });
    });
    return form;
  }

  function importSourceCard(s) {
    var card = el('div', 'vc-card'); card.dataset.sourceId = s.source_id;
    var top = el('div', 'vc-ep-top');
    top.appendChild(el('span', 'vc-kind', s.provider_label + (s.account_label && s.account_label !== s.provider_label ? ' · ' + s.account_label : '')));
    top.appendChild(el('span', 'vc-badge' + (s.status === 'attention' ? ' vc-badge-warn' : ''), SOURCE_STATES[s.status] || s.status));
    card.appendChild(top);
    if (s.last_read_at) card.appendChild(el('div', 'vc-where', 'Last read ' + String(s.last_read_at).slice(0, 16).replace('T', ' ') + ' UTC'));
    if (s.recovery) card.appendChild(el('div', 'vc-note vc-warn', s.recovery));
    var out = el('div');
    var bar = el('div', 'vc-ep-actions');
    var preview = button('Preview', 'vc-primary', function() { previewImport(s, out, preview); });
    bar.appendChild(preview);
    bar.appendChild(button('Remove source', 'vc-danger', function() { confirmRemoveSource(s, out); }));
    card.appendChild(bar); card.appendChild(out);
    return card;
  }

  function previewImport(s, out, btn) {
    btn.disabled = true; out.textContent = '';
    out.appendChild(el('div', 'vc-empty', 'Reading ' + (s.account_label || s.provider_label) + '…'));
    return request('POST', '/api/contacts/import/preview', {source_id: s.source_id}).then(function(res) {
      btn.disabled = false; out.textContent = '';
      if (!res.ok) {
        out.appendChild(el('div', 'vc-note vc-warn', why(res, 'The source could not be read; nothing was imported.')));
        var run = res.body && res.body.run, partial = run && run.error && run.error.partial_read;
        if (partial) out.appendChild(el('div', 'vc-note', partial + ' contacts were read before it stopped; none were saved.'));
        return;
      }
      out.appendChild(importPreview(res.body.run));
    });
  }

  function pendingLinks(box, pending) {
    (pending || []).forEach(function(p) {
      var line = el('div', 'vc-note vc-warn', (p.title || 'A change') + ' waits for your PIN or password (' + (p.items || []).length + ' item' + ((p.items || []).length === 1 ? '' : 's') + ').');
      box.appendChild(line);
      if (p.operation_id) box.appendChild(button('Review it', '', function() { openOperation(p.operation_id); }));
    });
  }

  function importPreview(run) {
    var box = el('div', 'vc-form'); box.id = 'vcImportPreview'; box.dataset.runId = run.run_id;
    var counts = run.counts || {};
    box.appendChild(el('div', 'vc-counts', 'Read ' + (run.read || 0) + (Object.keys(counts).length ? ': ' : '')
      + Object.keys(counts).map(function(k) { return counts[k] + ' ' + (IMPORT_CATEGORIES[k] || k).toLowerCase(); }).join(' · ')));
    var picks = [];
    (run.items || []).forEach(function(item) {
      var card = el('div', 'vc-item'); card.dataset.itemId = item.item_id;
      card.appendChild(el('div', 'vc-name', item.display_name || 'No name'));
      if (item.identity) card.appendChild(charactersBlock(item.identity, false));
      card.appendChild(el('div', 'vc-meta', (IMPORT_CATEGORIES[item.category] || item.category) + (item.organization ? ' · ' + item.organization : '')));
      (item.entries || []).forEach(function(e) {
        card.appendChild(el('div', 'vc-where', kindLabel(e.kind) + (e.label ? ' · ' + e.label : '') + (e.channel ? ' · ' + e.channel : '') + (e.provider_account ? ' · ' + e.provider_account : '')));
        var v = el('div', 'vc-value', e.value); v.setAttribute('translate', 'no'); card.appendChild(v);
      });
      (item.already_saved || []).forEach(function(e) {
        card.appendChild(el('div', 'vc-note', kindLabel(e.kind) + ' ' + e.value + ' is already saved on ' + (e.saved_on || []).map(function(c) { return c.display_name; }).join(', ') + '.'));
      });
      (item.same_name || []).forEach(function(c) {
        card.appendChild(el('div', 'vc-note vc-warn', 'You already have a contact named ' + c.display_name + ' with different entries. A second contact with this name, or adding to that one, waits for your PIN or password.'));
      });
      (item.lookalike_of || []).forEach(function(c) {
        card.appendChild(el('div', 'vc-note vc-warn', 'This name looks like your saved contact ' + c.display_name + ' but is spelled with different characters. Creating it waits for your PIN or password.'));
      });
      (item.rejected || []).forEach(function(r) {
        card.appendChild(el('div', 'vc-note', 'One ' + (r.kind === 'name' ? 'name' : kindLabel(r.kind).toLowerCase()) + ' was not kept: ' + humanReason(r.reason) + '.'));
      });
      if ((item.repeated_in_import || []).length) card.appendChild(el('div', 'vc-note', 'Part of this also appears on another contact in this import.'));
      var hasNew = !!(item.entries || []).length;
      var choices = [['skip', 'Skip']];
      if (hasNew && item.display_name) choices.push(['create', 'Create a new contact']);
      if (hasNew) (item.matches || []).concat(item.same_name || []).forEach(function(c) { choices.push(['merge:' + c.contact_id, 'Add the new entries to ' + c.display_name]); });
      var select = selectField(card, 'Choose', choices, item.suggested_action, 'vcPick-' + item.item_id);
      select.disabled = choices.length === 1;
      picks.push({item_id: item.item_id, select: select});
      box.appendChild(card);
    });
    if (!picks.length) box.appendChild(el('div', 'vc-empty', 'This source has no contacts to import.'));
    var err = el('div', 'vc-status'); err.setAttribute('role', 'alert'); box.appendChild(err);
    var apply = el('button', 'vc-btn vc-primary', ''); apply.type = 'button'; apply.id = 'vcImportApply';
    var summarize = function() {
      var created = 0, merged = 0;
      picks.forEach(function(p) { if (p.select.value === 'create') created += 1; else if (p.select.value.indexOf('merge:') === 0) merged += 1; });
      apply.textContent = created + merged ? 'Import ' + created + ' new, add to ' + merged + ' saved' : 'Nothing chosen';
      apply.disabled = !(created + merged);
    };
    picks.forEach(function(p) { p.select.addEventListener('change', summarize); });
    summarize();
    apply.addEventListener('click', function() {
      apply.disabled = true; err.textContent = ''; err.className = 'vc-status';
      var selections = {}; picks.forEach(function(p) { selections[p.item_id] = p.select.value; });
      request('POST', '/api/contacts/import/apply', {run_id: run.run_id, selections: selections}).then(function(res) {
        if (!res.ok) { summarize(); err.textContent = why(res, 'Nothing was imported.'); err.className = 'vc-status vc-bad'; return; }
        var done = res.body.applied || {}, failed = done.failed || [];
        box.textContent = '';
        box.appendChild(el('div', 'vc-note', 'Imported ' + (done.created || []).length + ' new and added entries to ' + (done.merged || []).length + ' saved. Imported entries stay marked as imported and not verified.'));
        pendingLinks(box, done.pending);
        failed.forEach(function(f) { box.appendChild(el('div', 'vc-note vc-warn', f.message || humanReason(f.reason))); });
        say('Import applied.', !!failed.length);
        refresh();
      });
    });
    var bar = el('div', 'vc-actions'); bar.appendChild(apply); box.appendChild(bar);
    return box;
  }

  function confirmRemoveSource(s, out) {
    out.textContent = '';
    var name = s.account_label || s.provider_label;
    var box = el('div', 'vc-confirm'); box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-label', 'Confirm removing ' + name);
    box.appendChild(el('div', 'vc-name', 'Remove ' + name + '?'));
    box.appendChild(el('div', 'vc-note', 'VOOL stops using this source and discards its open previews. Nothing in the source account is changed. Erasing entries from saved contacts waits for your PIN or password.'));
    var bar = el('div', 'vc-ep-actions');
    var remove = function(erase) {
      return request('POST', '/api/contacts/import/remove', {source_id: s.source_id, erase_imported: erase}).then(function(res) {
        if (!res.ok) { say(why(res, 'The source was not removed.'), true); return; }
        var r = res.body.removed || {};
        say('Removed ' + name + (erase ? '; erasing the entries still marked imported waits for your PIN or password' : '; imported entries were kept') + '.');
        refresh();
        if (r.erase_pending && r.erase_pending.operation_id) openOperation(r.erase_pending.operation_id); else renderImport();
      });
    };
    var keep = button('Remove, keep imported entries', '', function() { remove(false); }); keep.id = 'vcImportRemoveKeep';
    var erase = button('Remove and erase entries still marked imported', 'vc-danger', function() { remove(true); }); erase.id = 'vcImportRemoveErase';
    bar.appendChild(keep); bar.appendChild(erase); bar.appendChild(button('Cancel', '', function() { box.remove(); }));
    box.appendChild(bar); out.appendChild(box); keep.focus();
  }

  function openImport() {
    var ready = isOpen() ? Promise.resolve() : open();
    return ready.then(function() { return renderImport(); });
  }

  function openPending(operationId) {
    var ready = isOpen() ? Promise.resolve() : open();
    return ready.then(function() { return operationId ? openOperation(operationId) : null; });
  }

  // Home -> Contacts: the fragment binds its own Home entry, so the page is touched only by the button markup
  var homeButton = document.getElementById('contactsBtn');
  if (homeButton) homeButton.addEventListener('click', function() { open(); });

  window.VoolContacts = Object.freeze({open: open, close: close, isOpen: isOpen, refresh: refresh, choose: choose, chooseIntoComposer: chooseIntoComposer, openImport: openImport,
    openPending: openPending, openProtection: function() { var ready = isOpen() ? Promise.resolve() : open(); return ready.then(renderProtection); }});
})();
"""


def render_contacts_fragment() -> str:
    return "<style>" + _CSS + "</style>\n<script>" + _JS + "</script>\n"


__all__ = ["render_contacts_fragment"]
