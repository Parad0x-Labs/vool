"""A7 W5 gate — surface rebind (S13 replay marking, S14 derived binding, channels)."""
from __future__ import annotations

import hashlib

import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()
    yield
    reset_default_connection()
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


def test_s13_history_replay_marks_verified_vs_legacy():
    from core.finalization import finalize_answer, get_finalization_by_content

    sr = "sr:replay-probe:abc-1"
    text = "committed canonical answer bytes"
    finalize_answer(turn_id="t-replay", canonical_content=text)
    # Force a semantic id binding check via direct row (finalize used empty sr lane here).
    verified = get_finalization_by_content(text)
    assert verified is not None
    assert verified["content_hash"] == "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    # Old history text: no fabricated identity — simply unverified.
    assert get_finalization_by_content("some pre-A7 historical answer") is None


def test_s14_swarm_summary_rows_carry_a7_binding():
    from core.final_response_store import store_final_response
    from core.vool_user_summary import build_user_summary

    store_final_response("s14-task", raw="swarm truth", rendered="rendered swarm truth", status="completed", confidence=0.9)
    report = build_user_summary(limit_recent=5)
    rows = (report.get("memory") or {}).get("recent_final_responses") or []
    match = [r for r in rows if r.get("task_id") == "s14-task"]
    assert match, "finalized row missing from summary"
    a7 = match[0].get("a7") or {}
    assert a7.get("status") == "derived_display"
    assert a7.get("content_hash", "").startswith("sha256:")


def test_channel_gateway_consumes_canonical_bytes_framing_only():
    from core.channel_gateway import ChannelRequest, process_channel_request

    canonical = "PARIS IS THE ANSWER"
    commit = {
        "type": "response.commit",
        "version": 1,
        "revision": 1,
        "canonical_content": canonical,
        "content_hash": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
    }

    class FakeAgent:
        def run_once(self, *a, **k):
            return {
                "task_id": "t1",
                "mode": "chat",
                "confidence": 0.9,
                # raw decorated text DIFFERS from finalized truth on purpose:
                "response": canonical + "\n\n`local | m | 2 tok`",
                "vool_response_commit": commit,
            }

    request = ChannelRequest(platform="telegram", user_id="u1", text="hello", channel_id="c1")
    result = process_channel_request(FakeAgent(), request)
    assert result.response_text == canonical
    assert result.truncated is False
    assert "[truncated]" not in result.response_text
    assert "tok" not in result.response_text


def test_channel_gateway_long_content_splits_with_declared_flag_only():
    from core.channel_gateway import ChannelRequest, process_channel_request

    canonical = "x" * 5000
    commit = {
        "type": "response.commit",
        "version": 1,
        "revision": 1,
        "canonical_content": canonical,
        "content_hash": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
    }

    class FakeAgent:
        def run_once(self, *a, **k):
            return {"task_id": "t2", "mode": "chat", "confidence": 0.9,
                    "response": canonical, "vool_response_commit": commit}

    request = ChannelRequest(platform="telegram", user_id="u1", text="hi", channel_id="c1")
    result = process_channel_request(FakeAgent(), request)
    assert result.truncated is True
    # Exact-prefix framing: no invented marker inside the body.
    assert result.response_text == canonical[: len(result.response_text)]
    assert "[truncated]" not in result.response_text
