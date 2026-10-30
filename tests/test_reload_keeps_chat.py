"""A page reloaded mid-answer keeps the running chat, its queued message and its sidebar row.

Fresh-test item 14 / tester row p1: a long ask was executing, a second message sat in the queue
("Queued 1: ..."), and the person reloaded the page. The reloaded page then showed a brand-new
empty chat, an orphan "1 message is waiting to send" banner, and the running chat had left the
sidebar. After a VOOL restart the sidebar also sat empty for many seconds before the chats
appeared, which reads like the chats were lost.

Every test here boots the REAL page script under node (tests/chat_page_js_harness.py) with fetch
stubbed to the exact server shapes of that moment -- the chat's history still empty because the
running turn has not persisted anything yet, the queue still holding the second message, the
runtime summary reporting the chat as running with a live worker and the first message as its
request preview -- and then drives the page's own lifecycle entry points (refreshSidebarActivity,
adoptServerRunningTurn, renderSessions, loadSessions, paintQueueWaiting). Nothing is
re-implemented in Python; every assertion is about what the page's code actually painted.

The four scenarios, in the order a person meets them:

* reload mid-answer: the running chat opens, shows the ask and a working answer row, keeps the
  queued message as ITS chip, and never raises the orphan waiting banner;
* when the server's turn stops running, the adopted working row is dropped and the transcript is
  re-read, while the queued message stays this chat's chip (never a new chat's banner);
* before the first chat-list answer the sidebar says it is loading, not "No past chats yet.";
* when the first chat-list read fails the sidebar says so and a retry repaints the real list.
"""

from __future__ import annotations

import json

import pytest

from tests.chat_page_js_harness import DOM, run_node, script

SRC = script()

# The reload fix adds these to the page. On a tree that predates it each scenario below drives a
# missing function or paints the old way, so the test fails; strict xfail keeps the file green on
# such a tree WITHOUT weakening the assertions, and stops applying the moment the code exists.
_HAS_SERVER_TURN_CODE = "serverRunningRow" in SRC and "adoptServerRunningTurn" in SRC
_HAS_SIDEBAR_LOAD_GATE = "sessionsFirstLoadDone" in SRC

# The chat that was running when the page was reloaded. 20 hex chars: a canonical chat address,
# so boot restores it from the persisted last-open chat instead of minting a new one.
CHAT = "openclaw:1a2b3c4d5e6f7a8b9c0d"
OTHER_CHAT = "openclaw:0f9e8d7c6b5a4f3e2d1c"
FIRST_MESSAGE = "Please summarise the quarterly report and list every action item with its owner."
SECOND_MESSAGE = "Also add the budget table to the summary."


def _program(page_script: str, prelude: str, driver: str, *, exit_after_ms: int = 900) -> str:
    """DOM stand-in + server doubles + the real page script + the driver, as one node program."""
    return (
        DOM
        + "\n"
        + prelude
        + "\n"
        + page_script
        + "\n;(async function () {\n"
        + driver
        + "\n})();\n"
        + f"_st(() => {{ __report(); process.exit(0); }}, {exit_after_ms});\n"
    )


def _declare_constants() -> str:
    return (
        f"const CHAT = {json.dumps(CHAT)};\n"
        f"const OTHER_CHAT = {json.dumps(OTHER_CHAT)};\n"
        f"const FIRST_MESSAGE = {json.dumps(FIRST_MESSAGE)};\n"
        f"const SECOND_MESSAGE = {json.dumps(SECOND_MESSAGE)};\n"
    )


# The server exactly as the reloaded page finds it. `sessions_mode` picks the chat-list behaviour:
#   "empty"     -- /api/chat/sessions answers with no chats (the list does not know this chat yet);
#   "hang"      -- the first chat-list answer never arrives until the test resolves it;
#   "fail_once" -- the first chat-list read rejects, later ones answer with one chat.
# `with_running_turn` keeps (reload moment) or drops (sidebar-only scenarios) the running chat's
# lifecycle row and its queued second message.
def _server_doubles(sessions_mode: str, *, with_running_turn: bool = True) -> str:
    summary_rows = "[RUNNING_ROW]" if with_running_turn else "[]"
    queue_items = "[QUEUED_ITEM]" if with_running_turn else "[]"
    return (
        _declare_constants()
        + """
// The last-open chat the reloaded page comes back to: the one whose turn is still running.
localStorage.setItem('vool.sessionId', CHAT);
"""
        + """
const GENERIC = { ok: true, status: 200, sessions: [], messages: [], queue: [], pins: [], projects: [],
  models: [], events: [], receipts: [], counts: {}, connections: [], data: [], next_after: 0,
  grant: { token: 't', expires_at: 0 }, state: { mode: 'manual' }, approval: { scope: 'once' },
  release_version: '0.0.0', build_id: 'b', commit: 'c' };
const ok = (body) => ({ ok: true, status: 200, headers: { get: () => null }, json: async () => body });
const RUNNING_ROW = { session_id: CHAT, status: 'running', worker_live: true, request_preview: FIRST_MESSAGE };
const QUEUED_ITEM = { queue_item_id: 'qi-1', session_id: CHAT, status: 'pending', payload: { text: SECOND_MESSAGE } };
let sessionsCalls = 0;
globalThis.fetch = async (url, opts) => {
  const u = String(url || '');
  const method = (opts && opts.method) || 'GET';
  if (u.indexOf('/api/chat/queue') === 0 && method === 'POST') {
    // A claim against a chat whose slot the server still holds: no item to hand over.
    return ok({ turn_active: true });
  }
  if (u.indexOf('/api/chat/queue') === 0) {
    return ok({ queue: QUEUE_ITEMS, turn_active: TURN_ACTIVE });
  }
  if (u.indexOf('/api/chat/history') === 0) {
    return ok({ messages: [], private_chat: false });   // the running turn has persisted nothing yet
  }
  if (u.indexOf('/api/runtime/sessions?summary=1') === 0) {
    return ok({ sessions: SUMMARY_ROWS });
  }""" .replace("QUEUE_ITEMS", queue_items)
            .replace("TURN_ACTIVE", "true" if with_running_turn else "false")
            .replace("SUMMARY_ROWS", summary_rows)
        + """
  if (u === '/api/projects') {
    return ok({ projects: [] });
  }
  if (u === '/api/chat/sessions') {
"""
        + _sessions_route(sessions_mode)
        + """
  }
  return ok({ ...GENERIC });
};
"""
    )


def _sessions_route(sessions_mode: str) -> str:
    if sessions_mode == "empty":
        return "    return ok({ sessions: [], counts: {} });"
    if sessions_mode == "hang":
        return (
            "    return new Promise((resolve) => { globalThis.__resolveSessions = (body) => resolve(ok(body)); });"
        )
    if sessions_mode == "fail_once":
        return (
            "    sessionsCalls += 1;\n"
            "    if (sessionsCalls === 1) throw new Error('server unreachable');\n"
            "    return ok({ sessions: [{ session_id: OTHER_CHAT, title: 'Quarterly review',"
            " project_id: '', archived: false }], counts: { '': 1 } });"
        )
    raise AssertionError("unknown sessions mode " + sessions_mode)


DRIVER_HELPERS = """
const settle = (ms) => new Promise((r) => _st(r, ms));   // _st keeps node alive while we wait
const rowShape = (el) => ({ cls: String(el.className || ''), text: String(el.textContent || '') });
const sideRows = () => Array.from(sessionsEl.children).map(rowShape);
const sideText = () => sideRows().map((r) => r.text).join('\\n');
const sideRowIds = () => Array.from(sessionsEl.children).map((el) => String((el.dataset && el.dataset.id) || ''));
const msgList = () => Array.from(logEl.children).map(rowShape);
const banner = () => document.getElementById('queueWaiting');
"""


@pytest.mark.xfail(not _HAS_SERVER_TURN_CODE, strict=True, reason="contract code pending")
def test_reload_mid_answer_keeps_the_running_chat_on_screen() -> None:
    """The reloaded page opens the chat that was running and shows the turn as running.

    History is still empty (the turn has not persisted), the queue holds the second message, and
    the runtime summary says this chat is running with a live worker. After the lifecycle poll
    lands, the chat's log must show the first message as the user's bubble plus a working answer
    row, the queued second message must be this chat's chip, and the orphan waiting banner must
    not appear -- all painted from the server's own record, with no local run fabricated.
    """
    driver = (
        DRIVER_HELPERS
        + """
const facts = {};
try {
  await settle(200);                       // let every boot fetch settle
  facts.displayedOnBoot = displayedChat;
  await refreshSidebarActivity();          // the lifecycle poll that lands the running row
  await settle(250);                       // let the pump/refresh chain the poll kicks off finish
  facts.msgs = msgList();
  facts.queueDisplay = String(queueEl.style.display || '');
  facts.queueText = String(queueEl.textContent || '');
  facts.waitingState = queueWaitingState(CHAT).length;
  paintQueueWaiting(CHAT);                 // drive the banner paint now, not on its delay
  facts.bannerShown = banner().classList.contains('show');
  facts.bannerText = String(banner().textContent || '');
  facts.chatRun = chatState(CHAT).run;
  facts.chatBusy = !!isChatBusy(CHAT);
  facts.sideRowIds = sideRowIds();
  facts.sideText = sideText();
} catch (e) { errors.push('driver: ' + ((e && e.stack) || e)); }
out(facts);
"""
    )
    data = run_node(_program(script(), _server_doubles("empty"), driver))

    assert data["errors"] == [], "\n".join(data["errors"])
    assert data["displayedOnBoot"] == CHAT, "the reload must open the chat that was running, not a new one"
    msgs = data["msgs"]
    user_idx = [i for i, m in enumerate(msgs) if "msg user" in m["cls"] and FIRST_MESSAGE in m["text"]]
    assert user_idx, f"the running turn's own ask is not on screen as a user bubble: {msgs}"
    working = [
        m for i, m in enumerate(msgs) if i > user_idx[0] and "msg assistant" in m["cls"] and "Working on your message" in m["text"]
    ]
    assert working, f"no working answer row follows the ask: {msgs}"
    # The queued second message is a chip under the composer, never painted as a sent bubble.
    assert not [m for m in msgs if "msg" in m["cls"] and SECOND_MESSAGE in m["text"]], msgs
    assert data["queueDisplay"] != "none" and "Queued" in data["queueText"], data["queueText"]
    assert SECOND_MESSAGE in data["queueText"], f"the queued message is not this chat's chip: {data['queueText']}"
    assert data["waitingState"] == 0, "a message waiting behind the server's own turn is not orphaned"
    assert data["bannerShown"] is False, "the waiting-to-send banner shows while the server turn runs"
    assert "waiting to send" not in data["bannerText"]
    # Nothing was invented locally: no run object, no busy slot -- the painting is the server's record.
    assert data["chatRun"] is None and data["chatBusy"] is False
    # And the chat did not leave the sidebar while the server's chat list does not know it.
    assert CHAT in data["sideRowIds"], f"the running chat has no sidebar row: {data['sideRowIds']}"
    assert "No past chats yet." not in data["sideText"]


@pytest.mark.xfail(not _HAS_SERVER_TURN_CODE, strict=True, reason="contract code pending")
def test_when_the_server_turn_ends_the_working_row_is_dropped_and_the_queue_stays_with_the_chat() -> None:
    """The queued message belongs to the running chat, and the adopted row leaves when the turn ends.

    While the runtime summary reports the chat as running, the waiting banner never shows. The
    moment that row stops running (status completed, no live worker) and adoption runs again, the
    server-turn painting is cleared and the transcript re-read; the queued message stays this
    same chat's chip and the display never moves to a new chat.
    """
    driver = (
        DRIVER_HELPERS
        + """
const facts = {};
try {
  await settle(200);
  await refreshSidebarActivity();
  await settle(250);
  paintQueueWaiting(CHAT);                 // drive the banner paint now, not on its delay
  facts.bannerShownWhileRunning = banner().classList.contains('show');
  facts.bannerTextWhileRunning = String(banner().textContent || '');
  facts.msgsWhileRunning = msgList();
  // The server finished the turn: its lifecycle row stops running and the worker goes away.
  sidebarActivityHistory = [{ session_id: CHAT, status: 'completed', worker_live: false, request_preview: FIRST_MESSAGE }];
  adoptServerRunningTurn(CHAT);
  await settle(300);                       // the clear path re-reads history and the chat list
  facts.serverTurnAfterEnd = chatState(CHAT).serverTurn;
  renderChat(CHAT);                        // the page's own re-render: what the screen shows from state now
  facts.msgsAfterEnd = msgList();
  facts.displayedAfterEnd = displayedChat;
  facts.queueTextAfterEnd = String(queueEl.textContent || '');
} catch (e) { errors.push('driver: ' + ((e && e.stack) || e)); }
out(facts);
"""
    )
    data = run_node(_program(script(), _server_doubles("empty"), driver))

    assert data["errors"] == [], "\n".join(data["errors"])
    assert data["bannerShownWhileRunning"] is False, "the waiting-to-send banner shows while the server turn runs"
    assert "waiting to send" not in data["bannerTextWhileRunning"]
    running = data["msgsWhileRunning"]
    assert [m for m in running if "msg user" in m["cls"] and FIRST_MESSAGE in m["text"]], running
    assert [m for m in running if "msg assistant" in m["cls"] and "Working on your message" in m["text"]], running
    # The turn ended: the adopted working row is gone and the server turn no longer claims the chat.
    assert data["serverTurnAfterEnd"] is None, data["serverTurnAfterEnd"]
    after = data["msgsAfterEnd"]
    assert not [m for m in after if "Working on your message" in m["text"]], after
    # The queued message never became some new chat's orphan banner: it is still this chat's chip.
    assert data["displayedAfterEnd"] == CHAT
    assert SECOND_MESSAGE in data["queueTextAfterEnd"], data["queueTextAfterEnd"]


@pytest.mark.xfail(not _HAS_SIDEBAR_LOAD_GATE, strict=True, reason="contract code pending")
def test_sidebar_says_loading_until_the_first_chat_list_answer() -> None:
    """Before the first chat-list answer the sidebar is unknown, not empty.

    With /api/chat/sessions hanging, any sidebar render paints exactly one quiet loading row and
    must not claim "No past chats yet." -- that is the restart window that read as data loss.
    Only after the server itself answers that there are no chats does the empty state appear.
    """
    driver = (
        DRIVER_HELPERS
        + """
const facts = {};
try {
  await settle(200);                       // the first chat-list answer is still hanging
  renderSessions([]);                      // any re-render inside the unknown-list window
  facts.rowsWhileLoading = sideRows();
  facts.textWhileLoading = sideText();
  __resolveSessions({ sessions: [], counts: {} });   // the first answer arrives: no chats
  await settle(300);
  facts.rowsAfterEmpty = sideRows();
  facts.textAfterEmpty = sideText();
} catch (e) { errors.push('driver: ' + ((e && e.stack) || e)); }
out(facts);
"""
    )
    data = run_node(_program(script(), _server_doubles("hang", with_running_turn=False), driver))

    assert data["errors"] == [], "\n".join(data["errors"])
    loading = data["rowsWhileLoading"]
    assert len(loading) == 1, f"the unknown list paints exactly one row, got {loading}"
    assert "side-loading" in loading[0]["cls"], loading
    assert loading[0]["text"] == "Loading your chats…", loading
    assert "No past chats yet." not in data["textWhileLoading"]
    after = data["rowsAfterEmpty"]
    assert not [r for r in after if "side-loading" in r["cls"]], after
    assert "No past chats yet." in data["textAfterEmpty"]
    assert "Loading your chats" not in data["textAfterEmpty"]


@pytest.mark.xfail(not _HAS_SIDEBAR_LOAD_GATE, strict=True, reason="contract code pending")
def test_sidebar_says_retrying_when_the_first_chat_list_read_fails() -> None:
    """A failed first chat-list read is said out loud and a retry repaints the real list.

    The first /api/chat/sessions call rejects (the server is restarting). The sidebar must say it
    could not load the chats yet and is retrying -- never "No past chats yet." -- and when the
    retry runs against a server that answers, the real chat row replaces the notice.
    """
    driver = (
        DRIVER_HELPERS
        + """
const facts = {};
try {
  await settle(200);                       // the boot read of the chat list has failed by now
  facts.rowsAfterFailure = sideRows();
  facts.textAfterFailure = sideText();
  await loadSessions();                    // the retry path (its own timer is unref'd under node)
  await settle(250);
  facts.rowsAfterRetry = sideRows();
  facts.textAfterRetry = sideText();
  facts.rowIdsAfterRetry = sideRowIds();
} catch (e) { errors.push('driver: ' + ((e && e.stack) || e)); }
out(facts);
"""
    )
    data = run_node(_program(script(), _server_doubles("fail_once", with_running_turn=False), driver))

    assert data["errors"] == [], "\n".join(data["errors"])
    failed = data["rowsAfterFailure"]
    assert len(failed) == 1, f"a failed first read paints one notice row, got {failed}"
    assert "side-loading" in failed[0]["cls"], failed
    assert failed[0]["text"] == "Could not load your chats yet, retrying", failed
    assert "No past chats yet." not in data["textAfterFailure"]
    assert "Loading your chats…" not in data["textAfterFailure"], failed
    # The retry answered with one chat: its row replaces the notice.
    assert OTHER_CHAT in data["rowIdsAfterRetry"], data["rowIdsAfterRetry"]
    assert "Quarterly review" in data["textAfterRetry"], data["textAfterRetry"]
    assert "Could not load your chats yet" not in data["textAfterRetry"]
