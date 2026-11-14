"""Served: Mac answer-quality rows 3, 4a-2 and 4b on cbc915ec (qwen3:8b ready, :11750, 2026-11-14).

* 4a-2, after a stretching routine: "keep that routine in my notes" -> "I already had that in memory." No tool ran and
  nothing was saved anywhere. It saves the routine as a note.
* 4b: "log today's session: 5k run, 28 minutes" -> "Got it ... How did it feel?" Nothing was kept. It saves the entry.
* 3: "show my freezer items, the ones expiring last at the top" with no freezer database -> "Let me check your freezer
  items ... Please confirm if you'd like to proceed." It says there is no freezer database.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
ROUTINE = ("Here's a short desk stretching routine:\n1. Neck rolls, 30 seconds.\n2. Shoulder shrugs, 10 times.\n"
           "3. Seated spinal twist, 20 seconds each side.")
FREEZER_ASK = "show my freezer items, the ones expiring last at the top"
ASKS_TO_PROCEED = ("I can help with that. Let me check your freezer items and sort them by expiration date, showing the "
                   "ones expiring last at the top. Please confirm if you'd like to proceed.")

_SEED = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''


def _text(payload: dict[str, Any]) -> str:
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(commit.get("canonical_content") or message.get("content") or "")


def _mac_chat(daemon: ServedDaemon, message: str, *, session_id: str, history: list[dict[str, str]] | None = None) -> str:
    """The body the Mac's driver sends: the desktop surface, auto model choice, the chat so far."""
    messages = [*(history or []), {"role": "user", "content": message}]
    return _text(daemon.chat(message, session_id=session_id, model="vool", model_selection="auto", surface="desktop",
                             messages=messages))


def _executed(home: Path) -> list[str]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT details_json FROM runtime_session_events WHERE event_type = 'tool_executed' ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    return [str(json.loads(row[0] or "{}").get("tool_name") or "") for row in rows]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    tmp_path = tmp_path_factory.mktemp("aq-cbc915ec")

    def reply_fn(body: dict[str, Any]) -> Any:
        messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
        prompt = " ".join(str(m.get("content") or "") for m in messages)
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        last_user = next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "user"), "")
        if "freezer" in last_user:
            return ASKS_TO_PROCEED
        if "log today's session" in last_user:
            return "Got it — 5k run in 28 minutes. That's a solid pace! How did it feel?"
        if "keep that routine" in last_user:
            return "Locked in."
        return ROUTINE

    home = tmp_path / "home"
    provider = ScriptedProvider({MODEL: ROUTINE}, reply_fn=reply_fn)
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(tmp_path / "owner-home"),
        "VOOL_PLUGIN_SCRATCH_ROOT": str(tmp_path / "plugin-scratch"),
    })
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the rows
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
        provider.reset()
        yield home, provider, daemon
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def test_keep_that_routine_in_my_notes_saves_the_routine(served) -> None:
    home, _provider, daemon = served
    ask = "could you put together a short stretching routine I can do at my desk?"
    first = _mac_chat(daemon, ask, session_id="row4a")
    assert "Neck rolls" in first, first
    before = list(_executed(home))
    text = _mac_chat(daemon, "keep that routine in my notes", session_id="row4a",
                     history=[{"role": "user", "content": ask}, {"role": "assistant", "content": first}])

    assert "already had that in memory" not in text, text
    assert _executed(home)[len(before):] == ["operator.save_note"], (text, _executed(home))
    notes = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in home.rglob("*.md") if "note" in str(p).lower())
    assert "Neck rolls" in notes, (text, notes[:400])


def test_a_log_of_todays_session_is_kept_as_a_note(served) -> None:
    home, _provider, daemon = served
    before = list(_executed(home))
    text = _mac_chat(daemon, "log today's session: 5k run, 28 minutes", session_id="row4b")

    assert _executed(home)[len(before):] == ["operator.save_note"], (text, _executed(home))
    notes = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in home.rglob("*.md") if "note" in str(p).lower())
    assert "5k run, 28 minutes" in notes, (text, notes[:400])


def test_freezer_items_with_no_freezer_database_says_so(served) -> None:
    home, _provider, daemon = served
    text = _mac_chat(daemon, FREEZER_ASK, session_id="row3")

    assert "Please confirm" not in text, text
    assert "There's no freezer database here" in text, text
    assert "I can't create or change a database from chat." in text, text

