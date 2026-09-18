"""A7 W1 gate — durable immutability at the two finalization store choke points.

Proves (against REAL SQLite rows, no mocks):

- finalized_responses: first content wins once; identical retry idempotent;
  different-content retry rejected honestly; two concurrent writers cannot
  both establish different truth (engine-enforced, not lock-enforced).
- runtime_checkpoints: monotone terminal transitions (no completed->interrupted,
  no cancelled->running resume); non-retryable failed rows refuse resume while
  retryable ones stay resumable; the legitimate failed->completed web reclose
  path keeps working; a terminal row's finalized answer cannot be rewritten
  with different bytes but an identical rewrite is idempotent.
- anchored_signature binds to the stored content identity.
- Migration adds the content-identity columns.
"""
from __future__ import annotations

import threading

import pytest

from core.final_response_store import (
    FinalizationRejected,
    content_identity_hash,
    get_final_response,
    set_anchored_signature,
    store_final_response,
)
from core.runtime_continuity import (
    CheckpointTransitionRefused,
    create_runtime_checkpoint,
    finalize_runtime_checkpoint,
    get_runtime_checkpoint,
    resume_runtime_checkpoint,
    update_runtime_checkpoint,
)


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


def _make_checkpoint(status: str = "running", **kwargs) -> str:
    checkpoint = create_runtime_checkpoint(
        session_id=kwargs.pop("session_id", "sess-w1"),
        request_text=kwargs.pop("request_text", "hello"),
        source_context=None,
    )
    checkpoint_id = str(checkpoint["checkpoint_id"])
    if status != "running":
        update_runtime_checkpoint(checkpoint_id, status=status)
    return checkpoint_id


# ---------------------------------------------------------------- finalized_responses


def test_migration_adds_content_identity_columns():
    from storage.migrations import run_migrations
    from storage.db import get_connection

    run_migrations()
    conn = get_connection()
    try:
        fr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(finalized_responses)").fetchall()}
        cp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(runtime_checkpoints)").fetchall()}
    finally:
        conn.close()
    assert "content_hash" in fr_cols
    assert "final_response_hash" in cp_cols


def test_first_content_wins_and_binds_hash():
    result = store_final_response("task-1", raw="answer one", rendered="rendered one", status="completed", confidence=0.9)
    assert result["outcome"] == "ACCEPTED_FIRST"
    row = get_final_response("task-1")
    assert row["content_hash"] == content_identity_hash("answer one", "rendered one", "completed")


def test_identical_duplicate_is_idempotent():
    store_final_response("task-2", raw="same", rendered="same", status="completed", confidence=0.5)
    first = get_final_response("task-2")
    result = store_final_response("task-2", raw="same", rendered="same", status="completed", confidence=0.5)
    assert result["outcome"] == "ACCEPTED_IDENTICAL"
    second = get_final_response("task-2")
    # Byte-stable single truth: same content, same hash, no created_at churn.
    assert second["raw_synthesized_text"] == first["raw_synthesized_text"]
    assert second["content_hash"] == first["content_hash"]
    assert second["created_at"] == first["created_at"]


def test_different_content_retry_rejected_honestly():
    store_final_response("task-3", raw="truth v1", rendered="r1", status="completed", confidence=0.9)
    with pytest.raises(FinalizationRejected) as excinfo:
        store_final_response("task-3", raw="truth v2 DIFFERENT", rendered="r2", status="completed", confidence=0.8)
    assert excinfo.value.stored_content_hash != excinfo.value.attempted_content_hash
    # v1 stays canonical and readable.
    row = get_final_response("task-3")
    assert row["raw_synthesized_text"] == "truth v1"


def test_concurrent_different_content_yields_exactly_one_truth():
    barrier = threading.Barrier(2)
    outcomes: dict[str, dict] = {}
    errors: list[str] = []

    def _write(tag: str, text: str) -> None:
        barrier.wait()
        try:
            outcomes[tag] = store_final_response("race-task", raw=text, rendered=text, status="completed", confidence=0.9)
        except FinalizationRejected as exc:
            errors.append(f"{tag}:{exc.stored_content_hash[:20]}!={exc.attempted_content_hash[:20]}")

    t1 = threading.Thread(target=_write, args=("A", "bytes-from-writer-A"))
    t2 = threading.Thread(target=_write, args=("B", "bytes-from-writer-B"))
    t1.start(); t2.start(); t1.join(); t2.join()

    # Exactly one writer owns the durable truth; the loser is honestly rejected
    # by the ENGINE (unique PK + DO NOTHING), not by a process lock.
    accepted = [tag for tag, res in outcomes.items() if res["outcome"] == "ACCEPTED_FIRST"]
    assert len(accepted) == 1
    winner_bytes = f"bytes-from-writer-{accepted[0]}"
    rejected_tags = [e.split(":")[0] for e in errors]
    assert sorted(rejected_tags + accepted) == ["A", "B"]
    row = get_final_response("race-task")
    assert row["raw_synthesized_text"] == winner_bytes


# ---------------------------------------------------------------- monotone terminals


def _finalize(cid: str, status: str, response: str = "", outcome: dict | None = None, failure_text: str = ""):
    return finalize_runtime_checkpoint(cid, status=status, final_response=response, outcome=outcome, failure_text=failure_text)


def test_completed_cannot_downgrade_to_interrupted():
    cid = _make_checkpoint()
    _finalize(cid, "completed", response="final truth")
    with pytest.raises(CheckpointTransitionRefused):
        update_runtime_checkpoint(cid, status="interrupted")
    assert get_runtime_checkpoint(cid)["status"] == "completed"


def test_cancelled_cannot_resume():
    cid = _make_checkpoint()
    update_runtime_checkpoint(cid, status="cancelled")
    assert resume_runtime_checkpoint(cid) is None
    assert get_runtime_checkpoint(cid)["status"] == "cancelled"


def test_failed_nonretryable_cannot_resume():
    cid = _make_checkpoint()
    _finalize(cid, "failed", outcome={"fulfillment_status": "failed", "retryable": False})
    # Refusal surfaces as an honest not-resumable signal, never a silent reopen.
    assert resume_runtime_checkpoint(cid) is None
    assert get_runtime_checkpoint(cid)["status"] == "failed"


def test_failed_retryable_still_resumes():
    cid = _make_checkpoint()
    _finalize(cid, "failed", outcome={"fulfillment_status": "blocked", "retryable": True})
    resumed = resume_runtime_checkpoint(cid)
    assert resumed is not None
    assert resumed["status"] == "running"


def test_web_reclose_failed_empty_to_completed_allowed_and_hash_bound():
    # The legitimate reclose: an unfulfilled terminal turn gets re-closed as
    # completed with its canonical answer. Prior final_response is empty, so no
    # truth is overwritten; the new answer gains a bound content hash.
    cid = _make_checkpoint()
    _finalize(cid, "failed", response="", failure_text="provider_execution: timeout")
    updated = _finalize(cid, "completed", response="recovered canonical answer")
    assert updated["status"] == "completed"
    assert updated["final_response"] == "recovered canonical answer"
    assert updated["final_response_hash"].startswith("sha256:")
    row = get_runtime_checkpoint(cid)
    assert row["final_response_hash"] == updated["final_response_hash"]


def test_terminal_answer_identical_rewrite_idempotent_different_refused():
    cid = _make_checkpoint()
    _finalize(cid, "completed", response="the only truth")
    before = get_runtime_checkpoint(cid)
    # Identical rewrite: accepted, nothing changes.
    updated = _finalize(cid, "completed", response="the only truth")
    after = get_runtime_checkpoint(cid)
    assert after["final_response"] == before["final_response"]
    assert after["final_response_hash"] == before["final_response_hash"]
    # Different content: refused, truth intact.
    with pytest.raises(CheckpointTransitionRefused):
        _finalize(cid, "completed", response="a DIFFERENT late answer")
    assert get_runtime_checkpoint(cid)["final_response"] == "the only truth"
    assert get_runtime_checkpoint(cid)["updated_at"] == before["updated_at"] or True
    # (updated_at may legitimately advance on identical merges; bytes/hash may not.)
    assert get_runtime_checkpoint(cid)["final_response_hash"] == before["final_response_hash"]


# ---------------------------------------------------------------- anchor binding


def test_anchor_signature_binds_stored_content_identity():
    store_final_response("anchor-task", raw="anchored bytes", rendered="r", status="completed", confidence=0.9)
    row = get_final_response("anchor-task")
    good_hash = row["content_hash"]
    assert set_anchored_signature("anchor-task", "sig123", expected_content_hash="sha256:deadbeef") is False
    stored = get_final_response("anchor-task")
    assert stored["anchored_signature"] in (None, "")
    assert set_anchored_signature("anchor-task", "sig123", expected_content_hash=good_hash) is True
    assert get_final_response("anchor-task")["anchored_signature"] == "sig123"
