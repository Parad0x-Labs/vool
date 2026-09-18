"""Routing telemetry: owner-local decision log — append, redact, rotate, stats, fail-soft."""
from __future__ import annotations

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path):
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def test_record_and_read_back() -> None:
    rdl.record_decision(session_id="s1", user_input="check token hunter folder", family="machine_read_fast_path", handled=True)
    rdl.record_decision(session_id="s1", user_input="hello", family="model_lane", handled=False, claims=["folder_search", "machine_specs"], arbiter="picked:folder_search")
    rows = rdl.recent_decisions()
    assert len(rows) == 2
    assert rows[0]["family"] == "machine_read_fast_path" and rows[0]["handled"] is True
    assert rows[1]["claims"] == ["folder_search", "machine_specs"]
    assert rows[1]["arbiter"] == "picked:folder_search"


def test_message_is_truncated_and_secret_redacted() -> None:
    rdl.record_decision(session_id="s", user_input="my key is sk-or-v1-" + "a" * 40 + " " + "x" * 400, family="f", handled=True)
    row = rdl.recent_decisions()[0]
    assert len(row["message"]) <= 200
    assert "sk-or-v1-" + "a" * 40 not in row["message"]  # redacted, never stored verbatim


def test_rotation_keeps_newest_tail() -> None:
    for i in range(60):
        rdl.record_decision(session_id="s", user_input=f"msg {i} " + "pad " * 40, family="f", handled=True)
    # force a tiny rotate threshold by monkey-free direct call
    rdl._ROTATE_BYTES, saved = 1, rdl._ROTATE_BYTES
    try:
        rdl.record_decision(session_id="s", user_input="newest", family="f", handled=True)
    finally:
        rdl._ROTATE_BYTES = saved
    rows = rdl.recent_decisions(limit=5000)
    assert rows, "rotation must keep the newest tail"
    assert rows[-1]["message"] == "newest"


def test_stats_counts_families_and_ambiguity() -> None:
    rdl.record_decision(session_id="s", user_input="a", family="x", handled=True)
    rdl.record_decision(session_id="s", user_input="b", family="x", handled=True)
    rdl.record_decision(session_id="s", user_input="c", family="model_lane", handled=False, claims=["a", "b"])
    stats = rdl.decision_stats()
    assert stats["total"] == 3 and stats["families"]["x"] == 2 and stats["ambiguous"] == 1


def test_fail_soft_never_raises(monkeypatch) -> None:
    monkeypatch.setattr(rdl, "decisions_path", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    rdl.record_decision(session_id="s", user_input="x", family="f", handled=True)  # must not raise
    assert rdl.recent_decisions() == []
