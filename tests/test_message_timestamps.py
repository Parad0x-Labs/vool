"""Guard: chat message timestamps are real and honest, never fabricated.

SHOWRUNNER slice 2: subtle HH:mm on user/assistant bubbles, sourced only from a genuine send
instant, a server-emitted completion `ts`, or the per-turn `ts` already persisted by
append_conversation_event(). A render-time Date.now() may never stand in for a HISTORICAL
message's time -- these tests extract the real formatMsgTime/setMsgTime/addMsg/finishRun source
out of the emitted page and execute it under node, plus drive the real /api/chat/history route,
rather than re-implementing the logic in Python and asserting against a copy of itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def _script() -> str:
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    return scripts[0]


def _slice(source: str, start: str, end: str) -> str:
    lo = source.index(start)
    hi = source.index(end, lo)
    return source[lo:hi]


def _timestamp_js() -> str:
    """The real formatMsgTime()/setMsgTime() source, lifted verbatim out of the page."""
    source = _script()
    return _slice(
        source,
        "// HH:mm in the viewer's own local timezone",
        "function addMsg(role, text, tsIso, displayMetadata",
    )


# Minimal DOM stand-in covering exactly what setMsgTime touches: element creation, appendChild,
# a querySelector that finds a previously-appended .msg-time child, and a settable textContent.
DOM_STUB = """
class FakeElement {
  constructor(tag) {
    this.tagName = String(tag || 'DIV').toUpperCase();
    this.className = '';
    this.children = [];
    this._text = '';
    this.title = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  querySelector(sel) {
    if (sel === '.msg-time') return this.children.find((c) => c.className === 'msg-time') || null;
    return null;
  }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
}
globalThis.document = { createElement: (tag) => new FakeElement(tag) };
// The lifted timestamp slice now calls appLocale() (the page's i18n locale tag, defined
// outside the slice). Mirror the page's own definition: no VOOL_I18N tag -> undefined,
// which leaves toLocaleString on the runtime default exactly as before the i18n work.
globalThis.appLocale = function () {
  try { return (globalThis.window && globalThis.window.VOOL_I18N && globalThis.window.VOOL_I18N.tag) || undefined; } catch (e) { return undefined; }
};
"""


def _run_node(harness: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat-page timestamp script")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(DOM_STUB + _timestamp_js() + "\n" + harness)
        path = handle.name
    # Pin the viewer's own timezone to UTC so HH:mm assertions are deterministic regardless of
    # which machine runs the suite -- formatMsgTime is REQUIRED to read local time (per spec, the
    # viewer's own timezone), this only fixes what "local" means for this test process.
    env = dict(os.environ)
    env["TZ"] = "UTC"
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60, env=env)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat-page timestamp JS failed under node:\n{result.stderr}"
    return json.loads(result.stdout)


def _format(ts) -> str:
    arg = "null" if ts is None else json.dumps(ts)
    return _run_node(f"console.log(JSON.stringify({{ v: formatMsgTime({arg}) }}));")["v"]


def _set_msg_time_result(ts) -> dict:
    arg = "null" if ts is None else json.dumps(ts)
    harness = f"""
const el = document.createElement('div');
setMsgTime(el, {arg});
const t = el.querySelector('.msg-time');
console.log(JSON.stringify({{ hasTimeEl: !!t, text: t ? t.textContent : null }}));
"""
    return _run_node(harness)


# --------------------------------------------------------------------------- #
# formatMsgTime: HH:mm, no seconds, local timezone, quiet on garbage
# --------------------------------------------------------------------------- #
def test_format_msg_time_is_hh_mm() -> None:
    assert _format("2026-08-07T13:58:42.123456Z") == "13:58"


def test_format_msg_time_pads_single_digit_hour_and_minute() -> None:
    assert _format("2026-08-07T04:05:00Z") == "04:05"


def test_format_msg_time_never_includes_seconds() -> None:
    for ts in ("2026-08-07T13:58:00Z", "2026-08-07T13:58:59.999Z"):
        assert _format(ts) == "13:58", ts


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "banana", "   "])
def test_format_msg_time_degrades_quietly_on_malformed_or_missing(bad) -> None:
    assert _format(bad) == ""


# --------------------------------------------------------------------------- #
# setMsgTime: the element addMsg()/finishRun() actually mutate
# --------------------------------------------------------------------------- #
def test_set_msg_time_creates_the_time_element_for_a_valid_timestamp() -> None:
    result = _set_msg_time_result("2026-08-07T13:58:00Z")
    assert result["hasTimeEl"] is True
    assert result["text"] == "13:58"


def test_set_msg_time_creates_no_element_when_the_timestamp_is_absent() -> None:
    # This is the exact shape of the streaming/pending assistant bubble: addMsg('assistant', '...')
    # is called with no third argument at all. No element must appear -- never a fake "now".
    result = _set_msg_time_result(None)
    assert result["hasTimeEl"] is False


def test_set_msg_time_degrades_quietly_on_a_malformed_timestamp() -> None:
    result = _set_msg_time_result("not-a-real-date")
    assert result["hasTimeEl"] is False


def test_set_msg_time_updates_in_place_without_duplicating_the_element() -> None:
    # finishRun() calls setMsgTime a second time once the turn truly ends -- it must correct the
    # SAME element (e.g. after a provisional stamp), never grow a second time label.
    harness = """
const el = document.createElement('div');
setMsgTime(el, '2026-08-07T09:00:00Z');
setMsgTime(el, '2026-08-07T14:30:00Z');
const times = el.children.filter((c) => c.className === 'msg-time');
console.log(JSON.stringify({ count: times.length, text: times[0] ? times[0].textContent : null }));
"""
    result = _run_node(harness)
    assert result["count"] == 1
    assert result["text"] == "14:30"


def test_a_historical_timestamp_survives_being_reapplied_by_a_rerender() -> None:
    # Simulates the same persisted value being re-applied by an unrelated later render pass; the
    # displayed time must not drift, blank out, or change.
    harness = """
const el = document.createElement('div');
setMsgTime(el, '2026-08-07T14:03:00Z');
const before = el.querySelector('.msg-time').textContent;
setMsgTime(el, '2026-08-07T14:03:00Z');
const after = el.querySelector('.msg-time').textContent;
console.log(JSON.stringify({ before, after }));
"""
    result = _run_node(harness)
    assert result["before"] == "14:03"
    assert result["after"] == "14:03"


# --------------------------------------------------------------------------- #
# Wiring guards: the call sites that decide WHEN a real timestamp reaches the DOM
# --------------------------------------------------------------------------- #
def test_add_msg_wires_the_incoming_timestamp_through_to_set_msg_time() -> None:
    source = _script()
    signature = "function addMsg(role, text, tsIso, displayMetadata"
    assert signature in source
    body = _slice(source, signature, "logEl.appendChild(el);")
    assert "setMsgTime(el, tsIso);" in body


def test_the_streaming_assistant_placeholder_is_created_with_no_timestamp_argument() -> None:
    # Must not silently grow a third argument later -- that would show a fake time before the
    # answer is real.
    source = _script()
    # Still exactly two arguments -- a third would show a fake time before the answer is real.
    # Phase 1 made the bubble conditional on this run's chat being on screen; phase 2 hands it
    # straight to attachRunDom so the same call also serves a mid-turn re-open.
    assert re.search(
        r"attachRunDom\(run, addMsg\('assistant', '[^']*'\)\);", source
    ), "assistant placeholder must be created with exactly two arguments"
    # ...and the re-open path seeds from run.text with the same two-argument shape.
    assert re.search(
        r"attachRunDom\(run, addMsg\('assistant', run\.text \|\| '[^']*'\)\);", source
    ), "the reattach placeholder must also carry no timestamp argument"


def test_run_turn_stamps_the_user_bubble_with_the_real_send_instant() -> None:
    source = _script()
    # One instant, captured once: painted on the bubble AND persisted to the chat's transcript, so
    # re-opening the chat later shows the time the message was actually sent rather than losing it.
    assert "const sentAt = new Date().toISOString();" in source
    # History: the trailing null/turnAttachments arguments are the bubble's attachment chips
    # (draft spend / queue / resend), added after this pin was written; the CONTRACT — the
    # bubble is stamped with the one captured send instant — is unchanged.
    assert "addMsg('user', text, sentAt, null, turnAttachments);" in source
    assert "recordUserMessage(chatId, text, sentAt, turnAttachments);" in source


def test_finish_run_stamps_the_assistant_bubble_only_once_the_turn_truly_ends() -> None:
    source = _script()
    assert "function finishRun(run, kind, summaryText, tsIso) {" in source
    body = _slice(source, "function finishRun(run, kind, summaryText, tsIso) {", "paintFinishedCard(run);")
    # Prefers the server's own ts on the terminal event; only falls back to "now" for the
    # client-detected paths (abort/error) that have no server event to read from.
    # The stamp is recorded on the run first (so a run whose chat is off screen still carries its
    # real end time) and then painted onto the bubble -- same value, same precedence.
    assert "run.endedAt = tsIso || new Date().toISOString();" in body
    assert "if (run.assistantMsgEl) setMsgTime(run.assistantMsgEl, run.endedAt);" in body


def test_terminal_task_events_pass_their_server_ts_through_to_finish_run() -> None:
    source = _script()
    assert "finishRun(run, 'completed', ev.summary, ev.ts);" in source
    assert "finishRun(run, 'failed', ev.summary, ev.ts);" in source
    assert "finishRun(run, 'cancelled', ev.summary, ev.ts);" in source


def test_reload_paths_pass_the_persisted_ts_from_history_into_add_msg() -> None:
    source = _script()
    # DISPATCHER phase 2 collapsed the two reload paths into one: renderChat() is how a chat reaches
    # the screen, whether from boot, from the sidebar, or from returning to a chat mid-turn. So the
    # ts is passed through in ONE place, and both callers are asserted to go through it.
    # (The trailing argument is the row's request_id — the Proof Chip's binding identity; the ts
    # position is unchanged.)
    assert source.count("addMsg(m.role, m.content, m.ts, m.display_metadata, m.attachments, m.request_id);") == 1, "renderChat is the single reload path"
    assert "st.history.forEach((m, i) => {" in source
    for caller in ("async function openSession(id) {", "async function restoreCurrent() {"):
        body = source[source.index(caller):]
        body = body[: body.index("\n}\n")]
        assert "renderChat(" in body, f"{caller} must render through renderChat"
    # And a live turn's own messages carry their ts into the transcript, so the re-render has one.
    # History: the push moved into recordUserMessage() when attachments began riding the entry;
    # the entry still carries the same ts-or-empty contract.
    assert "const entry = { role: 'user', content: text, ts: tsIso || '' };" in source
    assert "chatState(chatId).history.push(entry);" in source
    assert "_owner.history[run.historyIndex].ts = run.endedAt;" in source


# --------------------------------------------------------------------------- #
# .msg-time is always visible (unlike the hover-gated action row) but subtle
# --------------------------------------------------------------------------- #
def test_msg_time_css_is_not_hover_gated() -> None:
    assert ".msg-time { display:block;" in HTML
    assert ".msg:hover .msg-time" not in HTML
    assert ".msg:focus-within .msg-time" not in HTML


def test_msg_time_css_is_visually_secondary() -> None:
    css = _slice(HTML, ".msg-time { display:block;", "}")
    assert "var(--muted)" in css
    assert "font-size:10px" in css


# --------------------------------------------------------------------------- #
# The chat page still ships valid JavaScript
# --------------------------------------------------------------------------- #
def test_the_chat_page_script_is_valid_javascript() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to syntax-check the page script")
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(_script())
        path = handle.name
    try:
        result = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"chat-page JS syntax error:\n{result.stderr}"


# --------------------------------------------------------------------------- #
# /api/chat/history: the real persisted-ts contract, driven end to end
# --------------------------------------------------------------------------- #
def test_history_endpoint_attaches_the_persisted_ts_to_the_assistant_message(tmp_path) -> None:
    from core.memory.files import conversation_log_path
    from core.runtime_paths import configure_runtime_home
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    configure_runtime_home(tmp_path / "runtime-home")
    try:
        conversation_log_path().parent.mkdir(parents=True, exist_ok=True)
        conversation_log_path().write_text(
            json.dumps(
                {
                    "session_id": "ts-hist-1",
                    "user": "hello",
                    "assistant": "hi there",
                    "ts": "2026-08-07T14:03:00.000000Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        res = dispatch_get(
            path="/api/chat/history",
            query={"session": ["ts-hist-1"]},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
        )
        assert res.status == 200
        body = json.loads(res.body.decode("utf-8"))
        # History: legacy-conversation assistant rows now carry the a7 provenance marker
        # (service.py: legacy rows have no A7 chain to verify, and the surface says so
        # instead of implying a verification that never ran).
        assert body["messages"] == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there", "ts": "2026-08-07T14:03:00.000000Z",
             "a7": {"status": "legacy_unverified"}},
        ]
    finally:
        configure_runtime_home(None)


def test_history_endpoint_never_invents_a_ts_for_the_user_message(tmp_path) -> None:
    # No distinct send-time is persisted for the user side of a turn (append_conversation_event
    # writes one shared `ts`, stamped at completion). Attaching it to the user message would
    # mislabel a completion-adjacent time as a send time -- the exact kind of fabrication the
    # timestamp contract must not do.
    from core.memory.files import conversation_log_path
    from core.runtime_paths import configure_runtime_home
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    configure_runtime_home(tmp_path / "runtime-home")
    try:
        conversation_log_path().parent.mkdir(parents=True, exist_ok=True)
        conversation_log_path().write_text(
            json.dumps(
                {"session_id": "ts-hist-2", "user": "q", "assistant": "a", "ts": "2026-08-07T14:03:00.000000Z"}
            )
            + "\n",
            encoding="utf-8",
        )
        res = dispatch_get(
            path="/api/chat/history",
            query={"session": ["ts-hist-2"]},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
        )
        body = json.loads(res.body.decode("utf-8"))
        user_msg = next(m for m in body["messages"] if m["role"] == "user")
        assert "ts" not in user_msg, user_msg
    finally:
        configure_runtime_home(None)


def test_history_endpoint_degrades_quietly_when_the_persisted_row_lacks_ts(tmp_path) -> None:
    # An older row written before this contract existed has no `ts` key at all.
    from core.memory.files import conversation_log_path
    from core.runtime_paths import configure_runtime_home
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    configure_runtime_home(tmp_path / "runtime-home")
    try:
        conversation_log_path().parent.mkdir(parents=True, exist_ok=True)
        conversation_log_path().write_text(
            json.dumps({"session_id": "ts-hist-3", "user": "old row", "assistant": "old answer"}) + "\n",
            encoding="utf-8",
        )
        res = dispatch_get(
            path="/api/chat/history",
            query={"session": ["ts-hist-3"]},
            runtime=RuntimeServices(display_name="VOOL"),
            model_name="vool",
        )
        assert res.status == 200
        body = json.loads(res.body.decode("utf-8"))
        assistant_msg = next(m for m in body["messages"] if m["role"] == "assistant")
        assert assistant_msg.get("ts") is None
    finally:
        configure_runtime_home(None)


def test_history_endpoint_survives_reload_with_the_same_ts_across_repeated_reads(tmp_path) -> None:
    # "Reload/reopen" in practice is just calling this same read endpoint again -- the persisted
    # row does not change, so the returned ts must not either.
    from core.memory.files import conversation_log_path
    from core.runtime_paths import configure_runtime_home
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    configure_runtime_home(tmp_path / "runtime-home")
    try:
        conversation_log_path().parent.mkdir(parents=True, exist_ok=True)
        conversation_log_path().write_text(
            json.dumps(
                {"session_id": "ts-hist-4", "user": "q", "assistant": "a", "ts": "2026-08-07T14:03:00.000000Z"}
            )
            + "\n",
            encoding="utf-8",
        )
        first = json.loads(
            dispatch_get(
                path="/api/chat/history",
                query={"session": ["ts-hist-4"]},
                runtime=RuntimeServices(display_name="VOOL"),
                model_name="vool",
            ).body.decode("utf-8")
        )
        second = json.loads(
            dispatch_get(
                path="/api/chat/history",
                query={"session": ["ts-hist-4"]},
                runtime=RuntimeServices(display_name="VOOL"),
                model_name="vool",
            ).body.decode("utf-8")
        )
        assert first["messages"] == second["messages"]
        assistant_msg = next(m for m in first["messages"] if m["role"] == "assistant")
        assert assistant_msg["ts"] == "2026-08-07T14:03:00.000000Z"
    finally:
        configure_runtime_home(None)


def test_the_chat_route_still_serves_the_page() -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(path="/chat", query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool")
    assert res.status == 200
    assert b"function formatMsgTime" in res.body
