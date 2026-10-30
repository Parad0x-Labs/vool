"""A failed load never renders as the page's "nothing here" state.

One test per fixed loader in core/vool_chat_page.py, each proving both directions of the law:

* a fetch that throws, answers non-OK, or carries an unreadable body paints the calm retry row
  (or the surface's own "unavailable"/kept-content state) -- never its empty state;
* a real OK answer with a genuinely empty list DOES paint the true empty state;
* after a first success, a later transient failure keeps the last good content.

The page boots itself in the node harness (tests/chat_page_js_harness.py), the same driving
pattern as tests/test_restart_shows_loading.py: globalThis.fetch is re-routed per URL, every
other endpoint answers a benign empty payload, and each scenario drives the loader the UI
itself calls (openFiles/openPlugins/refreshQueue/...).
"""
from __future__ import annotations

from tests.chat_page_js_harness import DOM, run_node, script

# Shared JS preamble: response builders (OK, non-OK with a readable JSON error body, OK with an
# unreadable body), the benign payload for every endpoint not under test, and a per-path router.
_RETRY = "Can\u2019t reach VOOL right now"

_PREAMBLE = """
const __ok = (payload) => ({ ok: true, status: 200, headers: { get: () => null },
  json: async () => payload, text: async () => '{}', blob: async () => ({}) });
const __fail = (status) => ({ ok: false, status: status, headers: { get: () => null },
  json: async () => ({ detail: 'starting' }) });
const __badBody = { ok: true, status: 200, headers: { get: () => null },
  json: async () => { throw new Error('unreadable body'); } };
const __benign = () => __ok({ sessions: [], messages: [], queue: [], pins: [], projects: [],
  events: [], next_after: 0, models: [], connections: [], credentials: [], keys: [],
  providers: [], runs: [], reports: [], attachments: [], receipts: [] });
const __route = (map) => async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  const hit = Object.prototype.hasOwnProperty.call(map, path) ? map[path] : null;
  if (hit) return hit();
  return __benign();
};
const __text = (el) => String((el && el.textContent) || '');
const RETRY = 'Can\\u2019t reach VOOL right now';
"""


def _run(body: str) -> dict:
    return run_node(DOM + script() + _PREAMBLE + body)


def test_plugins_panel_failure_is_not_an_empty_catalog() -> None:
    result = _run(
        """
const __body = document.getElementById('pluginsBody');
globalThis.fetch = __route({ '/api/plugins': () => __fail(503) });
await openPlugins();
const __afterFail = __text(__body);
globalThis.fetch = __route({ '/api/plugins': () => __badBody });
await openPlugins();
const __afterBad = __text(__body);
globalThis.fetch = __route({ '/api/plugins': () => __ok({ installed: true,
  plugins: [{ id: 'echo', name: 'Echo plugin', version: '1', enabled: true, skills: [] }],
  plugin_count: 1, skill_count: 0 }) });
await openPlugins();
out({
  afterFail: __afterFail,
  afterBad: __afterBad,
  afterOk: __text(__body),
});
"""
    )
    assert result["errors"] == []
    assert _RETRY in result["afterFail"]
    assert "No plugins installed yet" not in result["afterFail"]
    assert _RETRY in result["afterBad"], "an unreadable body is not a catalog either"
    assert "Echo plugin" in result["afterOk"]
    assert _RETRY not in result["afterOk"]
    assert "No plugins installed yet" not in result["afterOk"]


def test_plugin_rescan_failure_keeps_the_loaded_cards() -> None:
    result = _run(
        """
const __body = document.getElementById('pluginsBody');
const __good = () => __ok({ installed: true,
  plugins: [{ id: 'echo', name: 'Echo plugin', version: '1', enabled: true, skills: [] }],
  plugin_count: 1, skill_count: 0 });
globalThis.fetch = __route({ '/api/plugins': __good });
await openPlugins();
globalThis.fetch = __route({ '/api/plugins': () => __fail(503), '/api/plugins/rescan': () => __ok({ ok: true }) });
await rescanPlugins();
out({ afterRescanFail: __text(__body) });
"""
    )
    assert result["errors"] == []
    assert "Echo plugin" in result["afterRescanFail"], "a failed re-read must keep the cards"
    assert _RETRY not in result["afterRescanFail"]
    assert "No plugins installed yet" not in result["afterRescanFail"]


def test_files_panel_failure_is_not_the_no_files_row() -> None:
    result = _run(
        """
const __body = document.getElementById('filesBody');
globalThis.fetch = __route({ '/api/files': () => __fail(503) });
await openFiles();
const __afterFail = __text(__body);
globalThis.fetch = __route({ '/api/files': () => __ok({ count: 0, shown: 0, files: [] }) });
await openFiles();
out({ afterFail: __afterFail, afterOk: __text(__body) });
"""
    )
    assert result["errors"] == []
    assert _RETRY in result["afterFail"]
    assert "Nothing generated yet" not in result["afterFail"]
    assert "Nothing generated yet" in result["afterOk"], "a real OK empty answer is the only empty"
    assert _RETRY not in result["afterOk"]


def test_transcript_boot_failure_is_not_the_greeting() -> None:
    result = _run(
        """
globalThis.fetch = __route({ '/api/chat/history': () => __fail(503) });
await restoreCurrent();
const __afterFail = __text(logEl);
logEl.innerHTML = '';   // the stub keeps old children through a non-empty innerHTML write
globalThis.fetch = __route({ '/api/chat/history': () => __ok({ messages: [], private_chat: false }) });
await restoreCurrent();
out({ afterFail: __afterFail, afterOk: __text(logEl) });
"""
    )
    assert result["errors"] == []
    assert "Couldn\u2019t load this chat right now" in result["afterFail"]
    assert "What can you do?" not in result["afterFail"], "a failed read must not greet like a new chat"
    assert "What can you do?" in result["afterOk"], "a real OK empty answer still greets"
    assert "Couldn\u2019t load this chat right now" not in result["afterOk"]


def test_open_session_failure_is_not_the_greeting() -> None:
    result = _run(
        """
const __id = 'openclaw:a1b2c3d4e5f6a7b8c9d0';
globalThis.fetch = __route({ '/api/chat/history': () => __fail(503) });
await openSession(__id);
const __afterFail = __text(logEl);
logEl.innerHTML = '';
globalThis.fetch = __route({ '/api/chat/history': () => __ok({
  messages: [{ role: 'user', content: 'Seeded survivor message' }] }) });
await openSession(__id);
out({ afterFail: __afterFail, afterOk: __text(logEl) });
"""
    )
    assert result["errors"] == []
    assert "Couldn\u2019t load this chat right now" in result["afterFail"]
    assert "Seeded survivor message" not in result["afterFail"]
    assert "Seeded survivor message" in result["afterOk"]
    assert "Couldn\u2019t load this chat right now" not in result["afterOk"]


def test_pin_panel_failure_is_not_no_pins_and_the_retry_lands() -> None:
    result = _run(
        """
const __body = document.getElementById('pinBody');
pinsRetry.delayMs = 10;   // a plain binding, set before the failure schedules its timer
globalThis.fetch = __route({ '/api/chat/pins': () => __fail(503) });
openPinPanel();
await loadPins();
const __afterFail = __text(__body);
globalThis.fetch = __route({ '/api/chat/pins': () => __ok({ pins: [] }) });
await new Promise((resolve) => _st(resolve, 150));
out({ afterFail: __afterFail, afterRetry: __text(__body) });
"""
    )
    assert result["errors"] == []
    assert _RETRY in result["afterFail"]
    assert "No pinned messages" not in result["afterFail"]
    assert "No pinned messages in this chat yet" in result["afterRetry"], "the retried real answer repaints"
    assert _RETRY not in result["afterRetry"]


def test_local_models_failure_is_not_the_no_model_row() -> None:
    result = _run(
        """
const __pop = document.getElementById('modelPop');
// The boot's own /api/tags read is still in flight when this body starts; let it land under the
// failing route (it returns early) and drop whatever the boot painted before the scenarios.
await new Promise(setImmediate);
__pop.innerHTML = '';
// A row's own text is masked in the stub once it carries a child span, so the row check walks
// the painted tree for the row's data-model attribute.
const __hasModelRow = (root, want) => {
  const stack = (root.children || []).slice();
  while (stack.length) {
    const n = stack.shift();
    if (String((n.attrs && n.attrs['data-model']) || '') === want) return true;
    if (n.children) for (const k of n.children) stack.push(k);
  }
  return false;
};
globalThis.fetch = __route({ '/api/tags': () => __fail(503) });
await renderLocalModels();
const __afterFail = __text(__pop);
const __failRow = __hasModelRow(__pop, 'qwen3:8b');
__pop.innerHTML = '';
globalThis.fetch = __route({ '/api/tags': () => __ok({ models: [{ name: 'qwen3:8b', size: 1234 }] }) });
await renderLocalModels();
const __afterOk = __text(__pop);
const __okRow = __hasModelRow(__pop, 'qwen3:8b');
__pop.innerHTML = '';
globalThis.fetch = __route({ '/api/tags': () => __ok({ models: [] }) });
await renderLocalModels();
out({ afterFail: __afterFail, failRow: __failRow, afterOk: __afterOk, okRow: __okRow, afterEmpty: __text(__pop) });
"""
    )
    assert result["errors"] == []
    assert "No model on this computer yet" not in result["afterFail"]
    assert result["failRow"] is False
    assert result["okRow"] is True, "a real OK answer renders the installed local model row"
    assert "No model on this computer yet" in result["afterEmpty"], "a real OK empty answer is the only empty"


def test_usage_failure_is_not_zero_usage() -> None:
    result = _run(
        """
globalThis.fetch = __route({ '/api/runtime/usage': () => __fail(503) });
await renderUsage('today');
const __afterFail = __text(document.getElementById('usageBody'));
globalThis.fetch = __route({ '/api/runtime/usage': () => __ok({
  free_local: {}, free_cloud: {}, paid_cloud: {}, by_model: [], total_tokens: 0 }) });
await renderUsage('today');
out({ afterFail: __afterFail, afterOk: __text(document.getElementById('usageBody')) });
"""
    )
    assert result["errors"] == []
    assert "Usage is unavailable right now." in result["afterFail"]
    assert "No usage recorded in this window yet." not in result["afterFail"]
    assert "No usage recorded in this window yet." in result["afterOk"]
    assert "Usage is unavailable right now." not in result["afterOk"]


def test_receipts_failure_is_not_no_receipts() -> None:
    result = _run(
        """
const __body = document.getElementById('xpBody');
globalThis.fetch = __route({ '/api/runtime/receipts': () => __fail(503) });
await renderReceipts(__body);
const __afterFail = __text(__body);
globalThis.fetch = __route({ '/api/runtime/receipts': () => __ok({ receipts: [] }) });
await renderReceipts(__body);
out({ afterFail: __afterFail, afterOk: __text(__body) });
"""
    )
    assert result["errors"] == []
    assert "Receipts unavailable." in result["afterFail"]
    assert "No signed receipts" not in result["afterFail"]
    assert "No signed receipts for this chat yet" in result["afterOk"]
    assert "Receipts unavailable." not in result["afterOk"]


def test_queue_read_failure_keeps_the_chips() -> None:
    result = _run(
        """
const __cid = displayedChat;
const __pending = () => __ok({ queue: [{ queue_item_id: 'q1', session_id: __cid,
  status: 'pending', payload: { text: 'Queued survivor message' } }], next_after: 0 });
globalThis.fetch = __route({ '/api/chat/queue': __pending });
await refreshQueue(__cid);
const __afterGood = { text: __text(queueEl), display: queueEl.style.display };
globalThis.fetch = __route({ '/api/chat/queue': () => __fail(503) });
await refreshQueue(__cid);
out({ afterGood: __afterGood, afterFail: { text: __text(queueEl), display: queueEl.style.display } });
"""
    )
    assert result["errors"] == []
    assert "Queued survivor message" in result["afterGood"]["text"]
    assert "Queued survivor message" in result["afterFail"]["text"], "a failed read is not an empty queue"
    assert result["afterFail"]["display"] != "none"


def test_activity_panel_failure_is_not_no_activity() -> None:
    result = _run(
        """
document.body.classList.add('panel-open');
globalThis.fetch = __route({ '/api/runtime/events': () => __fail(503) });
await loadRecoveredLedger();
renderPanelBody();
const __afterFail = __text(xpBodyEl);
globalThis.fetch = __route({ '/api/runtime/events': () => __ok({ events: [], next_after: 0 }) });
await loadRecoveredLedger();
renderPanelBody();
out({ afterFail: __afterFail, afterOk: __text(xpBodyEl) });
"""
    )
    assert result["errors"] == []
    assert _RETRY in result["afterFail"]
    assert "No activity" not in result["afterFail"]
    assert "No activity for this chat yet" in result["afterOk"], "a real OK empty answer is the only empty"
    assert _RETRY not in result["afterOk"]


def test_keys_failure_keeps_the_list_and_real_empty_clears_it() -> None:
    result = _run(
        """
const __box = document.getElementById('keysList');
globalThis.fetch = __route({ '/api/cloud/keys': () => __ok({
  keys: [{ label: 'llm.cloud.openrouter', provider: 'openrouter', kind: 'openrouter' }] }) });
await renderKeys();
const __afterGood = __text(__box);
globalThis.fetch = __route({ '/api/cloud/keys': () => __fail(503) });
await renderKeys();
const __afterFail = __text(__box);
globalThis.fetch = __route({ '/api/cloud/keys': () => __ok({ keys: [] }) });
await renderKeys();
out({ afterGood: __afterGood, afterFail: __afterFail, afterEmpty: __text(__box) });
"""
    )
    assert result["errors"] == []
    assert "Your keys" in result["afterGood"]
    assert "Your keys" in result["afterFail"], "a failed read must not wipe the stored keys"
    assert "Your keys" not in result["afterEmpty"], "a real OK empty answer leaves the input as the story"


def test_council_failure_is_did_not_answer_not_no_run() -> None:
    result = _run(
        """
globalThis.fetch = __route({ '/api/council/runs': () => __fail(503) });
await refreshCouncilRuns();
const __afterFail = councilStateLabel();
globalThis.fetch = __route({ '/api/council/runs': () => __ok({ runs: [] }) });
await refreshCouncilRuns();
out({ afterFail: __afterFail, afterOk: councilStateLabel() });
"""
    )
    assert result["errors"] == []
    assert result["afterFail"] == "COUNCIL RUNTIME DID NOT ANSWER"
    assert result["afterOk"] == "NO COUNCIL RUN CONVENED"


def test_connections_failure_keeps_the_pill_not_no_key() -> None:
    result = _run(
        """
const __pill = document.getElementById('modelConnections');
globalThis.fetch = __route({ '/api/connections': () => __ok({
  connections: [{ id: 'cloud', provider: 'openrouter', label: 'OpenRouter', state: 'ok' }] }) });
await refreshCloudStatus(true);
const __afterGood = __text(__pill);
globalThis.fetch = __route({ '/api/connections': () => __fail(503) });
await refreshCloudStatus(true);
out({ afterGood: __afterGood, afterFail: __text(__pill) });
"""
    )
    assert result["errors"] == []
    assert "OpenRouter" in result["afterGood"]
    assert "OpenRouter" in result["afterFail"], "a failed read must keep the real connection state"
    assert "no key connected" not in result["afterFail"]


def test_staged_attachments_failure_retries_until_the_draft_is_back() -> None:
    result = _run(
        """
const __cid = displayedChat;
globalThis.fetch = __route({ '/api/chat/attachments': () => __fail(503) });
await restoreStagedAttachments(__cid);
const __afterFail = attachBucket(__cid).length;
stagedRetry.delayMs = 10;
globalThis.fetch = __route({ '/api/chat/attachments': () => __ok({
  attachments: [{ id: 'srv-a1', name: 'staged-note.txt', kind: 'doc', size_bytes: 12, document: true }] }) });
await new Promise((resolve) => _st(resolve, 150));
out({ afterFail: __afterFail, afterRetry: attachBucket(__cid).map((i) => i.name).join(',') });
"""
    )
    assert result["errors"] == []
    assert result["afterFail"] == 0
    assert "staged-note.txt" in result["afterRetry"], "the quiet retry brings the staged draft back"
