"""pa_beta_gate -- revision-6 served recovery for contract 5: a disk request in a directory named like authoring.

Real agent turns (``VoolAgent.run_once``) on the served path the review's originals use -- the four
``OperatorActionTests`` that turned into advice under a TMPDIR containing ``review/02-calendar-notes``.
Here the scanned directories end in the review probe's short names, ``review/notes`` and ``draft/docs``.
The response mode, the cleanup preview and the pending approval store are read, not the prose; nothing is
deleted (a preview only stages an approval).
"""
from __future__ import annotations

from unittest import mock

import pytest

_SOURCE = {"surface": "openclaw", "platform": "openclaw"}


@pytest.fixture
def served_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()
    from storage.db import get_connection

    conn = get_connection()  # the same reset the review's served originals (OperatorActionTests.setUp) perform
    try:
        conn.execute("DELETE FROM operator_action_requests")
        conn.commit()
    finally:
        conn.close()
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="openclaw-test", persona_id="default")
    agent.start()
    return agent


def _pending_actions() -> int:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM operator_action_requests WHERE status = 'pending_approval'").fetchone()
        return int(row["n"] or 0)
    finally:
        conn.close()


@pytest.mark.parametrize("tail", [pytest.param(("review", "notes"), id="review-notes"), pytest.param(("draft", "docs"), id="draft-docs")])
def test_served_disk_request_in_a_directory_named_like_authoring_previews_the_cleanup(served_agent, tmp_path, tail):
    root = tmp_path.joinpath("scan", *tail)
    root.mkdir(parents=True)
    (root / "big.bin").write_bytes(b"x" * 4096)
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    (temp_root / "cache.bin").write_bytes(b"y" * 2048)
    before = _pending_actions()
    with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
        result = served_agent.run_once(f'find disk bloat in "{root}"', source_context=dict(_SOURCE))
    assert result["mode"] == "tool_preview", result
    assert "Safe temp cleanup preview" in result["response"] and _pending_actions() > before
    assert (temp_root / "cache.bin").exists(), "a preview deletes nothing"


@pytest.mark.parametrize("template", [
    pytest.param('review my notes about the disk bloat we found in "{path}"', id="imperative"),
    pytest.param('could you review my notes on the disk bloat in "{path}"?', id="ask-shaped"),
])
def test_served_request_to_review_notes_about_disk_bloat_is_not_a_scan(served_agent, tmp_path, template):
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    (temp_root / "cache.bin").write_bytes(b"y" * 2048)
    notes_dir = tmp_path / "review" / "notes"
    notes_dir.mkdir(parents=True)  # a real directory: a wrongful dispatch would scan it, not stop at a missing path
    (notes_dir / "agenda.md").write_text("disk bloat findings from the audit walk", encoding="utf-8")
    before = _pending_actions()
    with mock.patch("core.local_operator_actions.tempfile.gettempdir", return_value=str(temp_root)):
        result = served_agent.run_once(template.format(path=notes_dir), source_context=dict(_SOURCE))
    assert not str(result.get("route") or "").startswith("action:"), result
    assert result["mode"] != "tool_preview" and _pending_actions() == before, result
    assert (temp_root / "cache.bin").exists()
