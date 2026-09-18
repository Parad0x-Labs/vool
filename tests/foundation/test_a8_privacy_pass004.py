"""A8 canonical privacy repair — PASS 004 targeted detectors (NCE-F1/F2).

A multi-chunk streamed governed answer leaves per-chunk FRAGMENTS in
denormalized runtime_sessions.last_message / event messages. Neither the
erasure blanket nor a whole-payload hash lookup can see a proper fragment, so
after ERASED or under WITHHELD those bytes were served verbatim through
/api/runtime/sessions (independent PASS003 reproof, 3/3 deterministic).
Repair under test: canonical derivative-lineage binding (a8_governed_derivatives)
+ fragment-aware derived-copy blanket at ERASE + live-lineage serve gate.
"""
from __future__ import annotations

import sqlite3

import pytest

import storage.db as sdb


@pytest.fixture()
def a8_env(tmp_path, monkeypatch):
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
    from core.semantic.semantic_result_seam import reset_admission

    reset_admission()
    yield home
    from core.semantic.semantic_admissions import clear_execution_context

    clear_execution_context()
    sdb.configure_default_db_path(None)
    configure_runtime_home(None)




def _finalize(text, request_id="req-p4", turn_id="t-p4"):
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    with _request_scope(request_id):
        return finalize_answer(turn_id=turn_id, canonical_content=text)


def _erase(fid):
    from core.finalization import erase_finalization_payload

    return erase_finalization_payload(fid)


def _request_scope(request_id):
    from contextlib import contextmanager as _cm
    from core.semantic.semantic_admissions import set_request_context

    @_cm
    def _scope(rid):
        set_request_context(rid)
        try:
            yield
        finally:
            set_request_context("")

    return _scope(request_id)


HEAD = "first half. "
TAIL = "WA-vault passphrase is 7F3-QX9-2210"
FULL = HEAD + TAIL


def _chunks(session_id="s-p4"):
    from core.runtime_continuity import append_runtime_event

    append_runtime_event(session_id=session_id, event_type="model_output_chunk", message=HEAD)
    append_runtime_event(session_id=session_id, event_type="model_output_chunk", message=TAIL)


def _last_message(session_id):
    from core.runtime_continuity import list_runtime_sessions

    for row in list_runtime_sessions(limit=50):
        if str(row.get("session_id")) == session_id:
            return str(row.get("last_message") or "")
    return None


def _events_messages(session_id):
    from core.runtime_continuity import list_runtime_session_events

    return [str(row.get("message") or "") for row in
            list_runtime_session_events(session_id, after_seq=0, limit=200)]


# ===========================================================================
# NCE-F1 — multi-chunk governed response, ERASE, no governed fragment servable
# ===========================================================================

def test_f1_multi_chunk_erased_fragment_not_servable(a8_env):
    commit = _finalize(FULL)
    fid = commit["finalization_id"]
    _chunks()
    out = _erase(fid)
    assert out.get("sweep_complete") is True
    # serve surface denies every governing form
    lm = _last_message("s-p4")
    assert lm is not None
    assert TAIL not in lm and FULL not in lm and HEAD.strip() not in lm.split(". ")[0]
    from core.finalization import (
        AVAILABILITY_ERASED,
        payload_availability_for_finalization_id,
        payload_availability_for_text,
    )

    assert payload_availability_for_text(TAIL) == AVAILABILITY_ERASED
    # NOTE: append_runtime_event strips trailing whitespace before persisting,
    # so the STORED derivative is the stripped head form
    assert payload_availability_for_text(HEAD.strip()) == AVAILABILITY_ERASED
    assert payload_availability_for_text(FULL) == AVAILABILITY_ERASED
    # verdicts are live lineage reads (no read-once-trust): repeat stays denied
    assert payload_availability_for_text(TAIL) == AVAILABILITY_ERASED
    assert payload_availability_for_finalization_id(fid) == AVAILABILITY_ERASED


# ===========================================================================
# NCE-F2 — same fragment under WITHHELD; serve-time enforcement is the boundary
# ===========================================================================

def test_f2_withheld_fragment_not_servable(a8_env):
    commit = _finalize(FULL)
    fid = commit["finalization_id"]
    _chunks()
    from core.finalization import (
        AVAILABILITY_WITHHELD,
        payload_availability_for_text,
        set_availability,
    )

    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="p4-f2") is True
    # lineage identity was durably bound at the transition (registration leg)
    conn = sdb.get_connection(sdb.active_default_db_path())
    reg = conn.execute(
        "SELECT finalization_id FROM a8_governed_derivatives"
    ).fetchall()
    keys = {str(r["finalization_id"]) for r in reg}
    assert keys == {fid}
    # WITHHELD performs no destructive traversal by design; the serve gate is
    # the boundary and must refuse the fragment
    assert payload_availability_for_text(TAIL) == AVAILABILITY_WITHHELD
    lm = _last_message("s-p4")
    assert lm is not None
    assert TAIL not in lm and FULL not in lm
    from core.runtime_continuity import _servable_event_message

    assert _servable_event_message(TAIL) == ""
    assert all(TAIL not in m for m in _events_messages("s-p4")) or all(
        _servable_event_message(m) != m for m in _events_messages("s-p4") if m == TAIL
    )


# ===========================================================================
# Historical whole-payload behavior remains correct (NCE-A envelope intact)
# ===========================================================================

def test_f3_whole_payload_forms_still_blank_and_denied(a8_env):
    commit = _finalize(FULL)
    fid = commit["finalization_id"]
    from core.runtime_continuity import append_runtime_event

    append_runtime_event(session_id="s-p4w", event_type="model_output", message=FULL)
    _erase(fid)
    lm = _last_message("s-p4w")
    assert lm is not None
    assert FULL not in lm

    fid2 = _finalize("withheld whole payload body.", request_id="req-p4w2")["finalization_id"]
    append_runtime_event(session_id="s-p4w2", event_type="model_output",
                         message="withheld whole payload body.")
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    assert set_availability(fid2, AVAILABILITY_WITHHELD, reason="p4-w") is True
    lm2 = _last_message("s-p4w2")
    assert lm2 is not None
    assert "withheld whole payload body." not in lm2


# ===========================================================================
# AVAILABLE governed content remains normally servable
# ===========================================================================

def test_f4_available_content_still_served(a8_env):
    commit = _finalize(FULL)
    fid = commit["finalization_id"]
    append = __import__("core.runtime_continuity", fromlist=["append_runtime_event"])
    append.append_runtime_event(session_id="s-p4a", event_type="model_output", message=FULL)
    assert _last_message("s-p4a") == FULL
    _erase(fid)
    assert _last_message("s-p4a") == ""


# ===========================================================================
# Unrelated runtime session content unaffected
# ===========================================================================

def test_f5_unrelated_sessions_unaffected(a8_env):
    from core.runtime_continuity import append_runtime_event

    append_runtime_event(session_id="s-other", event_type="status",
                         message="Routine compile finished.")
    append_runtime_event(session_id="s-other", event_type="model_output",
                         message="An unrelated open answer.")
    fid = _finalize(FULL, request_id="req-p4g")["finalization_id"]
    _chunks()
    _erase(fid)
    lm = _last_message("s-other")
    assert lm == "An unrelated open answer."
    msgs = _events_messages("s-other")
    assert "Routine compile finished." in msgs


# ===========================================================================
# Privacy lookup failure fails closed for a registered governed derivative
# ===========================================================================

def test_f6_derivative_gate_fails_closed_on_lookup_crash(a8_env, monkeypatch):
    import hashlib as _hl

    import core.finalization as fin

    commit = _finalize(FULL)
    fid = commit["finalization_id"]
    _chunks()
    orig = fin.payload_availability_for_hash

    def boom(content_hash):
        frag_key = "sha256:" + _hl.sha256(TAIL.encode()).hexdigest()
        if content_hash == frag_key:
            raise RuntimeError("privacy store outage")
        return orig(content_hash)

    monkeypatch.setattr(fin, "payload_availability_for_hash", boom)
    # writer side fails closed
    assert fin.writer_may_persist_text(TAIL) is False
    # serve side fails closed
    from core.runtime_continuity import _servable_event_message

    assert _servable_event_message(TAIL) == ""
    # ungoverned traffic keeps flowing during the outage
    assert _servable_event_message("just status chatter") == "just status chatter"


# ===========================================================================
# Fragment lineage survives restart/persistence (durable registry, fresh conn)
# ===========================================================================

def test_f7_lineage_persists_across_reopen(a8_env, tmp_path):
    import hashlib as _hl

    commit = _finalize(FULL)
    fid = commit["finalization_id"]
    _chunks()
    from core.finalization import AVAILABILITY_WITHHELD, set_availability

    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="p4-f7") is True
    # independent raw connection = persisted state another process would see
    raw = sqlite3.connect(str(tmp_path / "a8.db"))
    try:
        rows = raw.execute(
            "SELECT value_key FROM a8_governed_derivatives WHERE finalization_id=?",
            (fid,),
        ).fetchall()
    finally:
        raw.close()
    expected = {"sha256:" + _hl.sha256(HEAD.strip().encode()).hexdigest(),
                "sha256:" + _hl.sha256(TAIL.encode()).hexdigest()}
    assert {r[0] for r in rows} >= expected
    from core.finalization import payload_availability_for_text

    assert payload_availability_for_text(TAIL) == AVAILABILITY_WITHHELD


# ===========================================================================
# Multiple fragments of one governed payload remain consistently governed
# ===========================================================================

def test_f8_multiple_fragments_consistently_governed(a8_env):
    a, b, c = "alpha part one. ", "bravo secret 4411-KK-9021 ", "charlie tail."
    full = a + b + c
    from core.runtime_continuity import append_runtime_event

    fid = _finalize(full, request_id="req-p4m")["finalization_id"]
    for chunk in (a, b, c):
        append_runtime_event(session_id="s-p4m", event_type="model_output_chunk", message=chunk)
    _erase(fid)
    from core.finalization import AVAILABILITY_ERASED, payload_availability_for_text

    for frag in (a.strip(), b.strip(), c.strip()):
        assert payload_availability_for_text(frag) == AVAILABILITY_ERASED, frag
    conn = sdb.get_connection(sdb.active_default_db_path())
    n = int(conn.execute(
        "SELECT COUNT(*) c FROM a8_governed_derivatives").fetchone()["c"])
    assert n >= 3
    assert _last_message("s-p4m") == ""
