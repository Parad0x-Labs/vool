"""The local model picker: one fit-aware list of free local models, used wherever a person gets one.

First-run setup shows it on its first step; Settings > Local models and the chat's model menu open the
same picker later ("Find local models"), so a person who skipped setup gets the same list, the same
lag warnings and the same one-click download. The list comes from GET /api/models/local/choices
(core/local_model_fit.py over the one suggestion table). Nothing is downloaded until the person
presses Download; then one model at a time goes through the doors Settings already uses: download
(POST /api/models/local/pull), add (POST /api/models/local/register) and check
(POST /api/model-tool-certification/run). Only a model that passed the check is called ready.

``window.VoolModelPicker.mount(host, {onFinished})`` returns the picker's own node. The node keeps
its state, so a page that redraws can append the same node again instead of starting over.
"""
from __future__ import annotations

LOCAL_MODEL_PICKER_CSS = r"""
.mp { margin:0 0 14px; border:1px solid var(--border); border-radius:10px; padding:12px 14px; background:var(--panel); color:var(--ink); }
.mp h3 { margin:0 0 2px; font-size:15px; }
.mp .mp-hint { color:var(--muted); font-size:13px; margin:0 0 6px; }
.mp-row { display:flex; gap:10px; align-items:flex-start; padding:8px 0; border-top:1px solid var(--border); cursor:pointer; }
.mp-row:first-of-type { border-top:0; }
.mp-row input { margin-top:3px; width:16px; height:16px; accent-color:var(--accent); flex:0 0 auto; }
.mp-row b { display:block; font-size:14px; }
.mp-row span { display:block; color:var(--muted); font-size:13px; }
.mp .mp-lag { color:var(--warn); font-weight:600; }
.mp-row.have { cursor:default; opacity:.75; }
.mp details { margin-top:4px; }
.mp summary { cursor:pointer; color:var(--muted); font-size:13px; padding:6px 0; }
.mp .mp-line { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:10px; }
.mp .mp-btn { background:transparent; color:var(--ink); border:1px solid var(--border); border-radius:8px; padding:8px 14px; font:inherit; font-weight:600; cursor:pointer; }
.mp .mp-btn.primary { background:var(--accent); color:var(--accent-ink, #fff); border-color:var(--accent); }
.mp .mp-btn:disabled { opacity:.55; cursor:not-allowed; }
.mp .mp-status { font-size:13.5px; margin-top:8px; min-height:1em; }
.mp .mp-status.failed { color:var(--bad); font-weight:600; }
"""

LOCAL_MODEL_PICKER_JS = r"""
(function () {
  if (window.VoolModelPicker) return;
  const T = (key, fallback, params) => {
    let t = fallback;
    try { if (typeof VOOLT === 'function') { const v = VOOLT(key); if (v && v !== key) t = v; } } catch (e) {}
    return params ? String(t).replace(/\{(\w+)\}/g, (_, k) => (params[k] == null ? '' : params[k])) : t;
  };
  const MP = (k, f, p) => T('setup.picker.' + k, f, p);
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function getJSON(url) {
    const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
    const j = await r.json().catch(() => null);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return j || {};
  }
  async function postJSON(url, body) {
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
      const j = await r.json().catch(() => null);
      return { ok: r.ok && !(j && (j.error || j.ok === false)), status: r.status, json: j };
    } catch (e) { return { ok: false, status: 0, offline: true, json: null }; }
  }
  function plainModelName(tag) {
    const t = String(tag || '').trim();
    const parts = t.slice(t.lastIndexOf('/') + 1).split(':');
    const words = (parts[0] + ' ' + (parts[1] || '')).split(/[-_\s:]+/).filter(Boolean).map((w) => {
      const lw = w.toLowerCase();
      if (/^\d+(\.\d+)?[bm]$/.test(lw)) return lw.toUpperCase();
      return w.charAt(0).toUpperCase() + w.slice(1);
    });
    return words.join(' ') || t;
  }
  function roleText(o) {
    if (o.role === 'best') return MP('role.best', 'Best for this computer: quick, with good answers.');
    if (o.role === 'helper') return MP('role.helper', 'Small helper for quick checks, so everyday answers stay fast.');
    if (o.role === 'smaller') return MP('role.smaller', 'Smaller and faster, with simpler answers.');
    return MP('role.bigger', 'Smarter, but slower.');
  }
  function lagText(lag) {
    if (lag === 'too_big') return MP('lag.too_big', 'Bigger than this computer runs smoothly. It may make the whole computer lag.');
    if (lag === 'beside_loaded') return MP('lag.beside_loaded', 'It does not fit next to the models already running. It may make the computer lag.');
    if (lag === 'heavy') return MP('lag.heavy', 'Heavy for everyday chat. It may make the computer lag. Good for trying out.');
    return '';
  }

  function mount(host, opts) {
    opts = opts || {};
    const st = { data: null, loadFailed: false, chosen: new Set(), openMore: false, running: false, note: null };
    const box = el('div', 'mp'); box.id = opts.id || 'modelPicker';

    async function load() {
      try { st.data = await getJSON('/api/models/local/choices'); st.loadFailed = false; }
      catch (e) { st.data = null; st.loadFailed = true; }
      if (st.data && Array.isArray(st.data.options)) st.chosen = new Set(st.data.options.filter((o) => o.chosen).map((o) => o.model_name));
      draw();
    }
    function draw() {
      box.textContent = '';
      box.appendChild(el('h3', null, MP('title', 'Get a free private model')));
      if (!st.data && !st.loadFailed) { box.appendChild(el('p', 'mp-hint', MP('loading', 'Looking at what fits this computer…'))); return; }
      if (st.data && st.data.local_models_disabled) { box.hidden = true; return; }
      const options = (st.data && Array.isArray(st.data.options)) ? st.data.options : [];
      if (!options.length) { box.appendChild(el('p', 'mp-hint', MP('none', 'VOOL couldn’t find a model that fits this computer.'))); return; }
      box.appendChild(el('p', 'mp-hint', MP('lead', 'These fit this computer. VOOL ticked the best one for speed and quality. Nothing downloads until you press Download.')));
      const rowFor = (o) => {
        const row = el('label', 'mp-row' + (o.installed ? ' have' : ''));
        row.setAttribute('data-model', o.model_name);
        const tick = document.createElement('input'); tick.type = 'checkbox';
        tick.checked = !!o.installed || st.chosen.has(o.model_name);
        tick.disabled = !!o.installed || st.running;
        tick.addEventListener('change', () => { if (tick.checked) st.chosen.add(o.model_name); else st.chosen.delete(o.model_name); draw(); });
        const text = el('div');
        text.appendChild(el('b', null, MP('row', '{model} · {gb} GB', { model: plainModelName(o.model_name), gb: o.download_gb })));
        text.appendChild(el('span', null, o.installed ? MP('installed', 'Already on this computer.') : roleText(o)));
        const lag = lagText(o.lag);
        if (lag) { const w = el('span', 'mp-lag', lag); w.setAttribute('data-lag', o.lag); text.appendChild(w); }
        row.appendChild(tick); row.appendChild(text);
        return row;
      };
      const main = options.filter((o) => o.role === 'best' || o.role === 'helper');
      const more = options.filter((o) => o.role !== 'best' && o.role !== 'helper');
      main.forEach((o) => box.appendChild(rowFor(o)));
      if (more.length) {
        const fold = document.createElement('details');
        if (st.openMore || more.some((o) => st.chosen.has(o.model_name))) fold.open = true;
        fold.addEventListener('toggle', () => { st.openMore = fold.open; });
        fold.appendChild(el('summary', null, MP('more', 'Other models')));
        more.forEach((o) => fold.appendChild(rowFor(o)));
        box.appendChild(fold);
      }
      const picked = options.filter((o) => st.chosen.has(o.model_name) && !o.installed);
      const total = Math.round(picked.reduce((n, o) => n + Number(o.download_gb || 0), 0) * 10) / 10;
      const line = el('div', 'mp-line');
      if (st.running) {
        line.appendChild(el('span', 'mp-status', st.checking
          ? MP('checking', 'Getting {model} ready…', { model: plainModelName(st.checking) })
          : MP('progress', 'Downloading {model}… {percent}% ({n} of {total})', { model: plainModelName(st.current), percent: st.percent || 0, n: st.doneCount + 1, total: st.total })));
      } else {
        const go = el('button', 'mp-btn primary', picked.length ? MP('download', 'Download ({gb} GB)', { gb: total }) : MP('download_none', 'Download'));
        go.type = 'button'; go.id = (opts.id || 'modelPicker') === 'modelPicker' ? 'pickerDownload' : (opts.id + 'Download');
        go.setAttribute('data-picker-download', '1');
        go.disabled = !picked.length;
        go.addEventListener('click', run);
        line.appendChild(go);
        if (picked.some((o) => o.lag)) line.appendChild(el('span', 'mp-lag', MP('lag_picked', 'You picked a model that may make this computer lag. You can remove it later in Settings.')));
      }
      box.appendChild(line);
      if (st.note) { const n = el('div', 'mp-status' + (st.note.failed ? ' failed' : ''), st.note.text); n.setAttribute('role', 'status'); box.appendChild(n); }
    }
    async function run() {
      const queue = ((st.data && st.data.options) || []).filter((o) => st.chosen.has(o.model_name) && !o.installed).map((o) => o.model_name);
      if (!queue.length) return;
      st.running = true; st.failed = ''; st.total = queue.length; st.doneCount = 0; st.readiness = {}; st.note = null; draw();
      for (const tag of queue) {
        st.current = tag; st.percent = 0; draw();
        const start = await postJSON('/api/models/local/pull', { model_name: tag });
        if (!start.ok) { st.failed = tag; break; }
        let status = '';
        for (;;) {
          await sleep(1500);
          let s = null;
          try { s = await getJSON('/api/models/local/pull?model_name=' + encodeURIComponent(tag)); } catch (e) { s = null; }
          status = s ? String(s.status || '') : 'failed';
          if (status === 'ready' || status === 'failed' || status === 'idle' || status === 'skipped') break;
          st.percent = Number(s.percent || 0); draw();
        }
        if (status !== 'ready') { st.failed = tag; break; }
        st.current = ''; st.checking = tag; draw();
        // Added and checked like Settings does it: only a model that passes the check may answer.
        await postJSON('/api/models/local/register', { model_name: tag });
        const check = await postJSON('/api/model-tool-certification/run', { provider_name: 'ollama-local', model_name: tag, timeout_seconds: 150 });
        st.readiness[tag] = String((check.json && check.json.readiness) || 'check_unfinished');
        st.checking = ''; st.doneCount += 1;
        const row = st.data.options.find((o) => o.model_name === tag); if (row) row.installed = true;
        st.chosen.delete(tag);
      }
      st.running = false; st.current = ''; st.checking = '';
      // Only a model that passed VOOL's check is called ready; the words never run ahead of the check.
      // The small helper only runs quick checks and never writes answers: its result is not the verdict.
      const roleOf = (t) => { const o = st.data.options.find((x) => x.model_name === t); return o ? o.role : ''; };
      const states = Object.keys(st.readiness).map((t) => st.readiness[t]);
      const refused = Object.keys(st.readiness).find((t) => st.readiness[t] === 'not_suitable' && roleOf(t) !== 'helper' && !states.includes('ready'));
      const result = { failed: st.failed, ready: states.includes('ready'), refused: refused || '' };
      if (st.failed) st.note = { failed: true, text: MP('failed', 'The download of {model} didn’t finish. Check your internet connection, then press Download again.', { model: plainModelName(st.failed) }) };
      else if (result.ready) st.note = { failed: false, text: opts.readyText ? opts.readyText() : MP('ready_any', 'Your private model is ready. VOOL answers with it on this computer.') };
      else if (refused) st.note = { failed: true, text: MP('not_suitable', '{model} can’t use VOOL’s tools well enough, so VOOL won’t answer with it. Pick another model and press Download.', { model: plainModelName(refused) }) };
      else st.note = { failed: false, text: MP('downloaded', 'Downloaded. VOOL is still getting the model ready. This can take a minute.') };
      draw();
      if (typeof opts.onFinished === 'function') { try { opts.onFinished(result); } catch (e) {} }
    }
    draw();
    load();
    if (host) host.appendChild(box);
    return box;
  }
  window.VoolModelPicker = { mount: mount, plainModelName: plainModelName };
})();
"""

__all__ = ["LOCAL_MODEL_PICKER_CSS", "LOCAL_MODEL_PICKER_JS"]
