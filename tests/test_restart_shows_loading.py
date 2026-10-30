"""After a restart the sidebar shows a calm loading state, never a fake "no chats".

Page half of the restart contract (core/vool_chat_page.py loadSessions / paintSessionsState):

* before the first real answer the sidebar carries the loading row, never "No past chats yet.";
* a network-level failure paints the retry row and retries; when a retry lands a real answer the
  rows render and the loading/retry row is gone;
* a 503 "starting" answer keeps the retry wording and re-fetches -- it is never an empty list;
* "No past chats yet." appears ONLY on a real ready answer with an empty array;
* a real ready answer with rows renders the chat rows.

The page boots itself in the node harness (loadSessions() runs at boot). Each scenario re-routes
globalThis.fetch by URL and answers benign empty payloads for every other endpoint the page
touches at boot -- the same driving pattern as tests/test_activity_lifecycle_idempotency.py.
"""
from __future__ import annotations

from tests.chat_page_js_harness import DOM, run_node, script

# Shared JS preamble: a response builder, the benign payload for every non-sessions endpoint, and
# a reader for the rendered sidebar rows (the #sessions element the page paints into).
_PREAMBLE = """
const __ok = (payload) => ({ ok: true, status: 200, headers: { get: () => null },
  json: async () => payload, text: async () => '{}', blob: async () => ({}) });
const __benign = () => __ok({ sessions: [], messages: [], queue: [], pins: [], projects: [], events: [], next_after: 0 });
const __sidebarText = () => (document.getElementById('sessions').children || [])
  .map((child) => String(child.textContent || '')).join('\\n');
"""


def _run(body: str) -> dict:
    return run_node(DOM + script() + _PREAMBLE + body)


def test_before_the_first_answer_the_sidebar_shows_loading_not_empty() -> None:
    result = _run(
        """
// The sessions fetch never resolves: the backend is rebinding its port across a restart.
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/sessions') return new Promise(() => {});
  return __benign();
};
await new Promise(setImmediate);
const __text = __sidebarText();
out({
  text: __text,
  loading: __text.includes('Loading your chats'),
  empty: __text.includes('No past chats yet'),
  loaded: (typeof sessionsLoaded === 'boolean') ? sessionsLoaded : 'missing',
});
"""
    )
    assert result["errors"] == []
    assert result["loading"] is True
    assert result["empty"] is False
    assert result["loaded"] is False


def test_a_network_failure_retries_and_then_renders_the_real_rows() -> None:
    result = _run(
        """
// Fast retries first: the retry delay is a plain script binding, so this body can reassign it.
let __fastRetry = true;
try { sessionsRetryDelayMs = 10; } catch (e) { __fastRetry = false; }
let __calls = 0;
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/sessions') {
    __calls += 1;
    if (__calls === 1) throw new TypeError('Failed to fetch');
    return __ok({ state: 'ready',
      sessions: [{ session_id: 'chat:restart-1', title: 'Restart survivor chat' }],
      counts: { '': 1 }, total: 1 });
  }
  return __benign();
};
await new Promise(setImmediate);
const __afterFail = __sidebarText();
// Page timers are unref'd by the harness; _st is the real (ref-holding) setTimeout, so the loop
// stays alive while the page's fast retry timer fires. Same pattern as the queue-claim tests.
await new Promise((resolve) => _st(resolve, 120));
const __afterRetry = __sidebarText();
out({
  fastRetry: __fastRetry,
  calls: __calls,
  afterFail: __afterFail,
  afterRetry: __afterRetry,
  loaded: (typeof sessionsLoaded === 'boolean') ? sessionsLoaded : 'missing',
});
"""
    )
    assert result["errors"] == []
    assert result["fastRetry"] is True, "sessionsRetryDelayMs must stay reassignable for a fast retry"
    assert result["calls"] >= 2, "the failure was never retried"
    assert "Couldn't load your chats yet" in result["afterFail"]
    assert "No past chats yet" not in result["afterFail"]
    assert "Restart survivor chat" in result["afterRetry"]
    assert "Loading your chats" not in result["afterRetry"]
    assert "Couldn't load your chats yet" not in result["afterRetry"]
    assert result["loaded"] is True


def test_a_starting_answer_keeps_the_retry_wording_and_refetches() -> None:
    result = _run(
        """
let __fastRetry = true;
try { sessionsRetryDelayMs = 10; } catch (e) { __fastRetry = false; }
let __calls = 0;
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/sessions') {
    __calls += 1;
    return { ok: false, status: 503, headers: { get: () => null },
      json: async () => ({ ok: false, state: 'starting' }) };
  }
  return __benign();
};
await new Promise(setImmediate);
await new Promise((resolve) => _st(resolve, 150));   // give the retry loop time to re-ask
const __text = __sidebarText();
out({
  fastRetry: __fastRetry,
  calls: __calls,
  text: __text,
  empty: __text.includes('No past chats yet'),
  retrying: __text.includes("Couldn't load your chats yet"),
  loaded: (typeof sessionsLoaded === 'boolean') ? sessionsLoaded : 'missing',
});
"""
    )
    assert result["errors"] == []
    assert result["fastRetry"] is True
    assert result["calls"] >= 2, "a starting answer must be retried, not rendered as an empty list"
    assert result["retrying"] is True
    assert result["empty"] is False
    assert result["loaded"] is False


def test_a_real_empty_answer_is_the_only_path_to_the_empty_state() -> None:
    result = _run(
        """
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/sessions')
    return __ok({ state: 'ready', sessions: [], counts: {}, total: 0 });
  return __benign();
};
await new Promise(setImmediate);
const __text = __sidebarText();
out({
  empty: __text.includes('No past chats yet'),
  loading: __text.includes('Loading your chats'),
  loaded: (typeof sessionsLoaded === 'boolean') ? sessionsLoaded : 'missing',
});
"""
    )
    assert result["errors"] == []
    assert result["empty"] is True
    assert result["loading"] is False
    assert result["loaded"] is True


def test_a_real_answer_renders_the_chat_rows() -> None:
    result = _run(
        """
globalThis.fetch = async (url) => {
  const path = new URL(String(url), 'http://vool.test').pathname;
  if (path === '/api/chat/sessions')
    return __ok({ state: 'ready',
      sessions: [{ session_id: 'chat:seeded-1', title: 'Seeded restart chat' }],
      counts: { '': 1 }, total: 1 });
  return __benign();
};
await new Promise(setImmediate);
const __text = __sidebarText();
out({
  row: __text.includes('Seeded restart chat'),
  loading: __text.includes('Loading your chats'),
  empty: __text.includes('No past chats yet'),
  loaded: (typeof sessionsLoaded === 'boolean') ? sessionsLoaded : 'missing',
});
"""
    )
    assert result["errors"] == []
    assert result["row"] is True
    assert result["loading"] is False
    assert result["empty"] is False
    assert result["loaded"] is True
