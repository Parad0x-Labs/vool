"""A8 final freeze — independent sabotage campaign (mutation proofs).

Each sabotage DISABLES exactly one fence and proves the served detectors
catch the re-opened leak for the EXACT expected reason. A fence whose
removal changes nothing is not load-bearing; these tests pin that each one
is. Run only under pytest (monkeypatch restores every sabotage).
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from tests.foundation.test_a8_final_freeze_served import (
    AGENT,
    P_EXACT,
    P_QUOTE,
    _admit_finalize,
    _create_topic,
    _erase,
    _get,
    _MeetHandler,
    _post,
    _served,
    _withhold,
)


@pytest.fixture()
def a8_env(tmp_path, monkeypatch):
    """Same isolated-state law as the served suite's fixture (verbatim)."""
    home = tmp_path / "home"
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.setenv("VOOL_MIRROR_DATA_DIR", str(home / "relay_mirror"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(home)
    sdb.configure_default_db_path(tmp_path / "a8.db")
    from storage.migrations import run_migrations

    run_migrations()
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    configure_runtime_continuity_db_path(active_default_db_path())
    from core.conductor.obligation_ledger import clear_active_set

    clear_active_set()
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    yield home
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)


# ---------------------------------------------------------------------------
# S1 — sabotage the SERVE-TIME GATE: hive posts leak again (F01/F02 class)
# ---------------------------------------------------------------------------


def test_s1_sabotaged_serve_gate_reopens_hive_leak(a8_env, monkeypatch):
    from core import finalization as fin

    monkeypatch.setattr(
        fin, "writer_may_persist_text", lambda text: True, raising=True
    )
    commit = _admit_finalize(P_EXACT, request_id="sab-s1")
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        _post(
            meet,
            "/v1/hive/posts",
            {"topic_id": topic, "author_agent_id": AGENT, "body": P_QUOTE},
        )
        _withhold(commit["finalization_id"])
        _status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        # WITH the gate sabotaged the bytes LEAK — this is the mutation the
        # F01/F02 detectors must catch (they assert the opposite and go RED).
        assert P_EXACT in served, "sabotage failed: gate still holding with writer_may_persist_text disabled"


# ---------------------------------------------------------------------------
# S2 — sabotage the RESURRECTION FENCE (late-writer write fence): a post
#      written AFTER WITHHOLD persists and serves again (F14 class)
# ---------------------------------------------------------------------------


def test_s2_sabotaged_write_fence_reopens_resurrection(a8_env, monkeypatch):
    from core import finalization as fin

    monkeypatch.setattr(
        fin, "writer_may_publish_public_text", lambda text: True, raising=True
    )
    commit = _admit_finalize(P_EXACT, request_id="sab-s2")
    _withhold(commit["finalization_id"])
    with _served(_MeetHandler) as meet:
        topic = _create_topic(meet)
        _post(
            meet,
            "/v1/hive/posts",
            {"topic_id": topic, "author_agent_id": AGENT, "body": P_QUOTE},
        )
        _status, served = _get(meet, f"/v1/hive/topics/{topic}/posts")
        assert P_EXACT in served, "sabotage failed: late-writer fence still holding when disabled"


# ---------------------------------------------------------------------------
# S3 — sabotage the ERASURE RESUME: an incomplete sweep never completes
#      (F12 class) and the crash-window store keeps the bytes
# ---------------------------------------------------------------------------


def test_s3_sabotaged_resume_leaves_body_in_store(a8_env, monkeypatch):
    from core import finalization as fin

    monkeypatch.setattr(
        fin, "resume_incomplete_erasure_sweeps", lambda: [], raising=True
    )
    from tests.foundation.test_a8_final_freeze_served import _plant_task_result

    commit = _admit_finalize(P_EXACT, request_id="sab-s3")
    _plant_task_result(P_EXACT, result_id="res-sab-s3")

    def _boom(content_hash, plaintext=""):
        raise RuntimeError("simulated crash before task bodies sweep")

    monkeypatch.setattr(fin, "_sweep_step_task_result_bodies", _boom)
    result = _erase(commit["finalization_id"])
    assert result.get("sweep_complete") is False

    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT summary FROM task_results WHERE result_id = 'res-sab-s3'"
        ).fetchone()
    finally:
        conn.close()
    # WITH resume sabotaged the body SURVIVES in the store — exactly what
    # F12's post-resume assertion catches (it demands removal and goes RED).
    assert row is not None and str(row["summary"] or "") == P_EXACT, (
        "sabotage failed: sweep completed despite resume disabled"
    )
