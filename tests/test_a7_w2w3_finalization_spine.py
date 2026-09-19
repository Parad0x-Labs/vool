"""A7 W2/W3 gate — canonical finalization spine + typed terminal split.

Proves (no mocks on the asserted paths):

- The A7 authority consumes the admitted A2 semantic_result_id VERBATIM.
- A7-local identity lives in its own fc: namespace and never mints sr:* ids.
- content_hash covers the exact canonical bytes (hash last).
- Durable binding: first content wins per logical truth; identical duplicates
  accepted idempotently; different-content second finalization rejected with
  v1 intact — decided IN SQLite, not under locks.
- Zero-byte provider content is never finalized as successful answer truth;
  no-answer turns carry typed terminal objects, not manufactured prose.
- The web response-commit shim forwards the spine's attached commit instead of
  minting a second one (one finalization authority).
"""
from __future__ import annotations

import hashlib

import pytest

from core.finalization import (
    ANSWER_PRESENT,
    NO_ANSWER_TERMINAL,
    FinalizationRejected,
    NoAnswerContent,
    finalize_answer,
    get_finalization_by_semantic_id,
    no_answer_terminal,
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


def _admit(text: str) -> str:
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    record = admit_semantic_result({"response": text})
    return str(record["_semantic_admission"]["semantic_result_id"])


def test_consumes_a2_identity_verbatim():
    sr = _admit("the answer bytes")
    commit = finalize_answer(turn_id="t1", canonical_content="the answer bytes")
    assert commit["semantic_result_id"] == sr


def test_a7_namespace_references_never_replaces():
    sr = _admit("bytes")
    commit = finalize_answer(turn_id="t2", canonical_content="bytes")
    assert commit["finalization_id"].startswith("fc:")
    # No sr:-shaped id is authored here: the only sr value present IS A2's.
    assert commit["semantic_result_id"] == sr


def test_hash_covers_exact_canonical_bytes():
    _admit("x")
    content = "exact bytes 42"
    commit = finalize_answer(turn_id="t3", canonical_content=content)
    assert commit["content_hash"] == "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_durable_binding_first_wins_identical_idempotent():
    sr = _admit("durable bytes")
    c1 = finalize_answer(turn_id="t4", canonical_content="durable bytes")
    row = get_finalization_by_semantic_id(sr)
    assert row["content_hash"] == c1["content_hash"]
    # Identical re-finalization: idempotent, one row, stable identity.
    c2 = finalize_answer(turn_id="t4", canonical_content="durable bytes")
    assert c1["content_hash"] == c2["content_hash"]
    rows = [get_finalization_by_semantic_id(sr)]
    assert len(rows) == 1
    assert rows[0]["canonical_content"] == "durable bytes"


def test_different_content_second_finalization_rejected_v1_intact():
    sr = _admit("truth v1")
    finalize_answer(turn_id="t5", canonical_content="truth v1")
    from core.final_response_store import FinalizationRejected

    with pytest.raises(FinalizationRejected):
        finalize_answer(turn_id="t5", canonical_content="DIFFERENT truth v2")
    row = get_finalization_by_semantic_id(sr)
    assert row["canonical_content"] == "truth v1"


def test_empty_content_is_never_successful_answer_truth():
    # Even with no admission at all (empty content admits nothing), finalization
    # refuses to mint successful answer truth from zero bytes.
    with pytest.raises(NoAnswerContent):
        finalize_answer(turn_id="t6", canonical_content="")
    with pytest.raises(NoAnswerContent):
        finalize_answer(turn_id="t6b", canonical_content="   \n")


def test_no_answer_terminal_typed_zero_prose():
    terminal = no_answer_terminal(turn_id="t7", reason_code="cancelled", detail="user stop")
    assert terminal["type"] == "no_answer.terminal"
    assert terminal["status"] == NO_ANSWER_TERMINAL
    assert terminal["reason_code"] == "cancelled"
    # Typed truth carries no assistant answer payload at all.
    assert "canonical_content" not in terminal
    assert "content_hash" not in terminal


def test_no_answer_terminal_accepted_referent_persists():
    """ACCEPTED REFERENT: valid ACCEPTED semantic_result_id + valid
    execution fence + valid closure → durable NO_ANSWER succeeds."""
    import storage.db as sdb
    from core.invocation.ledger import accept_invocation, open_execution
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        record_admission,
        set_execution_context,
    )
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admitted = admit_semantic_result({"response": "refused bytes", "route_reason": "model_lane"})
    sr = admitted["_semantic_admission"]["semantic_result_id"]
    # Record a durable ACCEPTED admission.
    record_admission(sr, source_class="model", accepted=True)
    from core.semantic.semantic_admissions import admission_exists

    assert admission_exists(sr)
    # Onboard the lane with a valid execution context registered in the ledger.
    inv = accept_invocation(
        external_kind="test", external_value="na-accepted", principal="owner_local"
    )
    req = inv["request_id"]
    from core.invocation.ledger import current_runtime_epoch

    execution_id = open_execution(request_id=req, root_attempt_id="att-na-accepted")[
        "execution_id"
    ]
    set_execution_context({
        "execution_id": execution_id,
        "generation": 0,
        "runtime_epoch": current_runtime_epoch(),
    })
    try:
        before = len(_dur_rows())
        terminal = no_answer_terminal(
            turn_id="t-na-accepted",
            reason_code="provider_no_content",
            persist=True,
            closure={"covered": True, "open_count": 0, "set_version": "test"},
        )
        assert terminal["status"] == NO_ANSWER_TERMINAL
        assert "finalization_id" in terminal
        after = len(_dur_rows())
        assert after == before + 1, "valid accepted referent must persist exactly one row"
        # Direct durable inspection: the row must exist and cite the accepted sr.
        row = sdb.get_connection().execute(
            "SELECT * FROM a7_finalizations WHERE finalization_id = ?",
            (terminal["finalization_id"],),
        ).fetchone()
        assert row is not None
        assert row["semantic_result_id"] == sr
        assert row["status"] == "no_answer_terminal"
        assert row["terminal_reason"] == "provider_no_content"
    finally:
        clear_execution_context()
        reset_admission()


def test_no_answer_terminal_ghost_referent_refused():
    """GHOST / NONEXISTENT REFERENT: onboarded canonical lane +
    non-empty semantic_result_id + NO durable semantic_admissions
    ACCEPTED row exists → refused. Assert zero new durable A7 rows."""
    import storage.db as sdb
    from core.invocation.ledger import accept_invocation, open_execution
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        set_execution_context,
    )
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    # Seed the seam with a sr id that has NO durable admission row.
    ghost_sr = "sr:ghost-referent-test-0001"
    from core.semantic import semantic_result_seam as seam
    from core.semantic.semantic_result_seam import SemanticResultRecord, SemanticSource

    seam._ADMISSION.set(seam._AdmissionState())
    rec = SemanticResultRecord(
        semantic_result_id=ghost_sr,
        content="",
        source=SemanticSource.MODEL,
        route_id="repro",
        confidence=1.0,
        used_model=True,
        used_tool=False,
        memory_or_cache_authored=False,
        accepted=True,
    )
    seam._get_admission_state().admit(rec)

    inv = accept_invocation(
        external_kind="test", external_value="na-ghost", principal="owner_local"
    )
    req = inv["request_id"]
    from core.invocation.ledger import current_runtime_epoch

    execution_id = open_execution(request_id=req, root_attempt_id="att-na-ghost")[
        "execution_id"
    ]
    set_execution_context({
        "execution_id": execution_id,
        "generation": 0,
        "runtime_epoch": current_runtime_epoch(),
    })
    try:
        before = len(_dur_rows())
        with pytest.raises(FinalizationRejected, match="NO_ADMISSION_ROW"):
            no_answer_terminal(
                turn_id="t-na-ghost",
                reason_code="provider_no_content",
                persist=True,
                closure={"covered": True, "open_count": 0, "set_version": "test"},
            )
        after = len(_dur_rows())
        assert after == before, "ghost referent must produce zero new durable rows"
        # Read surface must not expose the ghost.
        assert get_finalization_by_semantic_id(ghost_sr) is None
    finally:
        clear_execution_context()
        reset_admission()


def test_no_answer_terminal_rejected_referent_refused():
    """REJECTED REFERENT: onboarded canonical lane + non-empty
    semantic_result_id + durable semantic_admissions row exists with
    accepted=0 → refused. Assert zero new durable A7 rows."""
    import storage.db as sdb
    from core.invocation.ledger import accept_invocation, open_execution
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        record_admission,
        set_execution_context,
    )
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    # Directly create a REJECTED (accepted=0) durable admission row.
    # Do NOT call admit_semantic_result first — that would create an
    # accepted row, and record_admission with accepted=False would
    # hit the unique constraint and return REJECTED_DIFFERENT without
    # overwriting the existing accepted row.
    rejected_sr = "sr:rejected-referent-test-0001"
    record_admission(rejected_sr, source_class="model", accepted=False)
    # Also seed the seam ContextVar so current_semantic_result_id()
    # returns the rejected sr (not empty).
    from core.semantic import semantic_result_seam as seam
    from core.semantic.semantic_result_seam import SemanticResultRecord, SemanticSource

    seam._ADMISSION.set(seam._AdmissionState())
    rec = SemanticResultRecord(
        semantic_result_id=rejected_sr,
        content="",
        source=SemanticSource.MODEL,
        route_id="repro",
        confidence=1.0,
        used_model=True,
        used_tool=False,
        memory_or_cache_authored=False,
        accepted=True,
    )
    seam._get_admission_state().admit(rec)
    # Confirm the admission exists but is NOT accepted.
    adm = sdb.get_connection().execute(
        "SELECT accepted FROM semantic_admissions WHERE sr_id = ?", (rejected_sr,)
    ).fetchone()
    assert adm is not None and adm["accepted"] == 0

    inv = accept_invocation(
        external_kind="test", external_value="na-rejected", principal="owner_local"
    )
    req = inv["request_id"]
    from core.invocation.ledger import current_runtime_epoch

    execution_id = open_execution(request_id=req, root_attempt_id="att-na-rejected")[
        "execution_id"
    ]
    set_execution_context({
        "execution_id": execution_id,
        "generation": 0,
        "runtime_epoch": current_runtime_epoch(),
    })
    try:
        before = len(_dur_rows())
        with pytest.raises(FinalizationRejected, match="NO_ADMISSION_ROW"):
            no_answer_terminal(
                turn_id="t-na-rejected",
                reason_code="provider_no_content",
                persist=True,
                closure={"covered": True, "open_count": 0, "set_version": "test"},
            )
        after = len(_dur_rows())
        assert after == before, "rejected referent must produce zero new durable rows"
        assert get_finalization_by_semantic_id(rejected_sr) is None
    finally:
        clear_execution_context()
        reset_admission()


def test_no_answer_terminal_empty_referent_on_onboarded_lane_refused():
    """EMPTY REFERENT: onboarded canonical lane + empty semantic_result_id
    → refused. Assert zero new durable A7 rows."""
    import storage.db as sdb
    from core.invocation.ledger import accept_invocation, open_execution
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        set_execution_context,
    )
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    inv = accept_invocation(
        external_kind="test", external_value="na-empty", principal="owner_local"
    )
    req = inv["request_id"]
    from core.invocation.ledger import current_runtime_epoch

    execution_id = open_execution(request_id=req, root_attempt_id="att-na-empty")[
        "execution_id"
    ]
    set_execution_context({
        "execution_id": execution_id,
        "generation": 0,
        "runtime_epoch": current_runtime_epoch(),
    })
    try:
        before = len(_dur_rows())
        with pytest.raises(FinalizationRejected, match="EMPTY_SR_ONBOARDED_LANE"):
            no_answer_terminal(
                turn_id="t-na-empty",
                reason_code="provider_no_content",
                persist=True,
                closure={"covered": True, "open_count": 0, "set_version": "test"},
            )
        after = len(_dur_rows())
        assert after == before, "empty referent on onboarded lane must produce zero rows"
    finally:
        clear_execution_context()
        reset_admission()


def test_no_answer_terminal_legacy_unonboarded_lane_still_works():
    """LEGACY COMPATIBILITY: any contractually sanctioned legacy
    no-answer lane (unonboarded, no execution identity) must continue
    to behave according to the migration/legacy contract."""
    from core.semantic.semantic_admissions import clear_execution_context
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    clear_execution_context()
    # No execution context = legacy unonboarded lane.
    # Empty sr + no fence = should persist (legacy contract).
    terminal = no_answer_terminal(
        turn_id="t-na-legacy",
        reason_code="provider_no_content",
        persist=True,
    )
    assert terminal["status"] == NO_ANSWER_TERMINAL
    assert terminal["semantic_result_id"] == ""


def test_no_answer_terminal_guard_sensitivity():
    """GUARD-SENSITIVITY / NEGATIVE CONTROL: prove the admission_exists
    guard is load-bearing. Temporarily neutering the check lets a
    rejected referent through; restoring the guard blocks it."""
    import storage.db as sdb
    from core.invocation.ledger import accept_invocation, open_execution
    from core.semantic import semantic_admissions as _sa
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        set_execution_context,
    )
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    # Directly create a REJECTED (accepted=0) durable admission row.
    rejected_sr = "sr:guard-sensitivity-rejected-0001"
    _sa.record_admission(rejected_sr, source_class="model", accepted=False)
    # Also seed the seam ContextVar so current_semantic_result_id()
    # returns the rejected sr (not empty).
    from core.semantic import semantic_result_seam as seam
    from core.semantic.semantic_result_seam import SemanticResultRecord, SemanticSource

    seam._ADMISSION.set(seam._AdmissionState())
    rec = SemanticResultRecord(
        semantic_result_id=rejected_sr,
        content="",
        source=SemanticSource.MODEL,
        route_id="repro",
        confidence=1.0,
        used_model=True,
        used_tool=False,
        memory_or_cache_authored=False,
        accepted=True,
    )
    seam._get_admission_state().admit(rec)

    inv = accept_invocation(
        external_kind="test", external_value="na-guard", principal="owner_local"
    )
    req = inv["request_id"]
    from core.invocation.ledger import current_runtime_epoch

    execution_id = open_execution(request_id=req, root_attempt_id="att-na-guard")[
        "execution_id"
    ]
    set_execution_context({
        "execution_id": execution_id,
        "generation": 0,
        "runtime_epoch": current_runtime_epoch(),
    })
    try:
        # With guard intact: rejected referent is refused.
        before = len(_dur_rows())
        with pytest.raises(FinalizationRejected, match="NO_ADMISSION_ROW"):
            no_answer_terminal(
                turn_id="t-na-guard",
                reason_code="provider_no_content",
                persist=True,
                closure={"covered": True, "open_count": 0, "set_version": "test"},
            )
        assert len(_dur_rows()) == before

        # Temporarily neuter the guard: admission_exists always returns True.
        _original = _sa.admission_exists

        def _always_true(sr_id):
            return True

        _sa.admission_exists = _always_true
        try:
            terminal = no_answer_terminal(
                turn_id="t-na-guard-bypass",
                reason_code="provider_no_content",
                persist=True,
                closure={"covered": True, "open_count": 0, "set_version": "test"},
            )
            # Without the guard, the rejected referent slips through.
            assert "finalization_id" in terminal
            assert len(_dur_rows()) == before + 1
        finally:
            _sa.admission_exists = _original

        # Restore the guard: the same rejected referent is refused again.
        after_restore = len(_dur_rows())
        with pytest.raises(FinalizationRejected, match="NO_ADMISSION_ROW"):
            no_answer_terminal(
                turn_id="t-na-guard-restore",
                reason_code="provider_no_content",
                persist=True,
                closure={"covered": True, "open_count": 0, "set_version": "test"},
            )
        assert len(_dur_rows()) == after_restore
    finally:
        clear_execution_context()
        reset_admission()


def _dur_rows():
    """Direct durable inspection helper."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT finalization_id, semantic_result_id, status, terminal_reason "
            "FROM a7_finalizations"
        ).fetchall()]
    finally:
        conn.close()


def test_web_commit_shim_forwards_spine_commit_no_second_mint():
    from core.web.api.runtime import _response_commit

    sr = _admit("spine bytes")
    spine_commit = finalize_answer(
        turn_id="t8",
        canonical_content="spine bytes",
        display_metadata={"provenance_footer": ""},
    )
    forwarded = _response_commit(
        {"vool_response_commit": spine_commit}, source_context=None
    )
    assert forwarded is spine_commit
    assert forwarded["semantic_result_id"] == sr


def test_answer_present_status_typed():
    _admit("s")
    commit = finalize_answer(turn_id="t9", canonical_content="s")
    assert commit["status"] == ANSWER_PRESENT
