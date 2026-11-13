"""Served, clean loop off: "my mac keeps saying the disk is full, what's eating it?" is a largest-items ask.

Mac answer-quality row 2 on d5d044b0 (real qwen3:8b): with the clean loop off no tool ran and the reply was "I
couldn't get a usable model response in this run"; with it on, the loop ran the storage scan. The machine-fact
lane now reads the ask, so both settings scan this computer.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
ASK = "my mac keeps saying the disk is full, what's eating it?"
REPLY = "STUB-REPLY"

_SEED = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
'''


def _text(payload: dict[str, Any]) -> str:
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(commit.get("canonical_content") or message.get("content") or "")


def _executed(home: Path) -> list[str]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT details_json FROM runtime_session_events WHERE event_type = 'tool_executed' ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    return [str(json.loads(row[0] or "{}").get("tool_name") or "") for row in rows]


@pytest.fixture
def served(tmp_path: Path):
    owner = tmp_path / "owner-home"
    (owner / "Movies").mkdir(parents=True)
    (owner / "Movies" / "holiday_2025.mov").write_bytes(b"\0" * (6 * 1024 * 1024))
    (owner / "Downloads").mkdir()
    (owner / "Downloads" / "installer_old.dmg").write_bytes(b"\0" * (3 * 1024 * 1024))
    home = tmp_path / "home"
    provider = ScriptedProvider({MODEL: REPLY})
    daemon = ServedDaemon(home, env_extra={
        "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "HOME": str(owner),
        "VOOL_CLEAN_LOCAL_LOOP": "0",
    })
    provider.__enter__()
    try:
        daemon.start(timeout=240)
    except Exception as exc:  # pragma: no cover - environment, not the reply
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL))
    provider.reset()
    yield home, daemon
    daemon.stop()
    provider.__exit__(None, None, None)


def test_the_full_disk_ask_scans_this_computer_with_the_loop_off(served):
    home, daemon = served
    text = _text(daemon.chat(ASK, session_id="served-disk-full", model=MODEL, mode="auto"))
    assert "usable model response" not in text, text
    assert "machine.find_largest" in _executed(home), text
    assert "Movies" in text, text
    assert str(Path.home()) not in text, text
