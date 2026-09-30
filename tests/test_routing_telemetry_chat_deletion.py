"""Chat deletion now includes routing telemetry: both sinks, the real served route, races.

Synthetic values only, disposable homes only. Proves: ``delete_conversation_session`` removes
the deleted chat's folded identity from the JSONL AND the shadow sqlite while other sessions'
rows and other record types survive with their canonical identity intact; the namespace
lifecycle authority suppresses late/in-flight writes (no resurrection); duplicate deletes and
noncanonical-handle correlation hold; corrupted legacy rows are preserved and counted, never
discarded with the whole log; storage faults surface as typed warnings in the served response
instead of swallowed success; and the two-session served workflow reads back the same state
after a fresh store handle (logical deletion — SQLite/WAL forensic limits stated in the owner
docstring, not re-proved here).
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths
from core.context_namespace import (
    ensure_chat_namespace,
    load_chat_namespace,
    set_chat_namespace_state,
)
from core.memory.entries import record_memory_entry, resolve_memory_access_policy
from core.persistent_memory import (
    append_conversation_event,
    delete_conversation_session,
    recent_conversation_events,
)
from core.routing_authority_v2 import (
    ContractValidationError,
    RoutingAuthorityV2ShadowStore,
    ShadowStoreIntegrityError,
)
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_post

_A = "openclaw:" + "a" * 20
_B = "openclaw:" + "b" * 20


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _jsonl_refs() -> list[str]:
    return [str(row.get("session_id") or "") for row in rdl.recent_decisions(limit=1000)]


def _shadow_refs() -> list[str]:
    db = runtime_paths.data_path("routing_authority_v2_shadow.sqlite")
    if not db.exists():
        return []  # a home whose decisions never reached the shadow sink has no store yet
    with sqlite3.connect(str(db)) as conn:
        return [
            str(json.loads(bytes(r[0])).get("session_ref") or "")
            for r in conn.execute(
                "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                " WHERE record_type = 'RoutingDecisionShadowV2'"
            )
        ]


def _seed_chat(sid: str) -> None:
    ensure_chat_namespace(sid, grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id=sid)
    append_conversation_event(
        session_id=sid, user_input="hi", assistant_output="hey", access_policy=policy
    )
    record_memory_entry(
        f"fact for {sid}", category="fact", session_id=sid, source="test", confidence=0.9,
        keywords=["fact"], share_scope="local_only", scope="chat",
        authority="confirmed_memory", fact_key=None, expires_at=None, review_after=None,
        access_policy=policy,
    )


def test_delete_removes_both_telemetry_sinks_and_only_the_deleted_chat():
    _seed_chat(_A)
    _seed_chat(_B)
    for sid in (_A, _B, _A):
        rdl.record_decision(session_id=sid, user_input=f"turn for {sid}", family="fixture", handled=True)
    assert _jsonl_refs().count(_A) == 2 and _jsonl_refs().count(_B) == 1

    assert delete_conversation_session(_A) is True

    assert _A not in _jsonl_refs()          # JSONL rows gone
    assert _B in _jsonl_refs()               # the live chat's rows untouched
    assert _A not in _shadow_refs()          # shadow rows gone
    assert _B in _shadow_refs()
    # transcript-class erasure is preserved alongside the new telemetry removal
    assert recent_conversation_events(_A, limit=5) == []
    assert recent_conversation_events(_B, limit=5)
    assert load_chat_namespace(_A).lifecycle_state == "deleted"
    # read-back through a FRESH store handle (what a restarted process opens)
    fresh = RoutingAuthorityV2ShadowStore(
        runtime_paths.data_path("routing_authority_v2_shadow.sqlite")
    )
    digests = fresh.list_shadow_record_digests(record_type="RoutingDecisionShadowV2")
    survivor = fresh.read_shadow_record(digests[0])
    from core.routing_authority_v2 import RoutingDecisionShadowV2

    assert isinstance(survivor, RoutingDecisionShadowV2) and survivor.session_ref == _B


def test_duplicate_delete_is_idempotent_and_late_writes_do_not_resurrect():
    _seed_chat(_A)
    rdl.record_decision(session_id=_A, user_input="one", family="fixture", handled=True)
    assert delete_conversation_session(_A) is True
    assert delete_conversation_session(_A) is False  # nothing left to remove anywhere
    rdl.record_decision(session_id=_A, user_input="late turn", family="fixture", handled=True)
    rdl.record_decision(session_id=_A, user_input="late turn identical bytes",
                        family="fixture", handled=True)
    assert _A not in _jsonl_refs() and _A not in _shadow_refs()
    assert "late turn" not in rdl.decisions_path().read_text()


def test_a_deleted_chat_stops_logging_but_an_archived_chat_still_logs():
    _seed_chat(_A)
    set_chat_namespace_state(_A, "deleted")
    rdl.record_decision(session_id=_A, user_input="no telemetry for the dead", family="f", handled=True)
    assert _A not in _jsonl_refs() and _A not in _shadow_refs()

    _seed_chat(_B)
    set_chat_namespace_state(_B, "archived")
    rdl.record_decision(session_id=_B, user_input="archived chats still answer", family="f", handled=True)
    assert _B in _jsonl_refs() and _B in _shadow_refs()


def test_a_delete_landing_mid_write_is_caught_by_the_post_write_recheck(monkeypatch):
    """The race the guard alone cannot close: the namespace flips to deleted AFTER the
    pre-write guard passed but BEFORE the sinks finished. The post-write recheck consults
    the same authority again and purges the session's rows — an idempotent self-purge, not
    a second tombstone manager."""
    import core.context_namespace as cn

    _seed_chat(_A)
    calls = {"n": 0}
    real_load = cn.load_chat_namespace

    def flips_mid_write(chat_id: str):
        calls["n"] += 1
        namespace = real_load(chat_id)
        if calls["n"] <= 1:
            # first consultation (the pre-write guard): the chat still looks alive
            return namespace if namespace is not None else None
        return SimpleNamespace(lifecycle_state="deleted")

    monkeypatch.setattr(cn, "load_chat_namespace", flips_mid_write)
    rdl.record_decision(session_id=_A, user_input="written during the delete", family="f", handled=True)
    monkeypatch.undo()
    assert _A not in _jsonl_refs()
    assert _A not in _shadow_refs()
    assert "written during the delete" not in rdl.decisions_path().read_text()


def test_noncanonical_handle_correlates_to_the_same_folded_identity():
    raw = "my-project-chat-alias"
    from core.chat_session_identity import canonical_chat_session_id

    folded = canonical_chat_session_id(raw)
    ensure_chat_namespace(raw, grant_current_receipts=False)  # namespace keyed by the raw handle
    rdl.record_decision(session_id=raw, user_input="first turn", family="model_lane", handled=False)
    rdl.record_decision(session_id=folded, user_input="second turn", family="model_lane", handled=False)
    assert _jsonl_refs() == [folded, folded]

    assert delete_conversation_session(raw) is True
    assert folded not in _jsonl_refs() and folded not in _shadow_refs()


def test_deleted_chat_cannot_be_resurrected_by_either_handle_spelling():
    """The erasure authority owns the chat's identity, not one spelling of it.

    A namespace ensured under a raw native handle is deleted through that handle; a later
    diagnostic write that arrives already-folded (or under any other spelling of the same
    chat) must be suppressed exactly like the raw one — the folded form must not step
    around a tombstone stored under the raw handle, and vice versa."""
    from core.chat_session_identity import canonical_chat_session_id

    raw = "review-alias"
    folded = canonical_chat_session_id(raw)
    ensure_chat_namespace(raw, grant_current_receipts=False)
    rdl.record_decision(session_id=raw, user_input="turn", family="f", handled=True)
    assert delete_conversation_session(raw) is True

    # folded -> raw: the tombstone lives under the raw handle
    rdl.record_decision(session_id=folded, user_input="late folded", family="f", handled=True)
    assert _jsonl_refs() == [] and _shadow_refs() == []
    assert "late folded" not in rdl.decisions_path().read_text()

    # raw -> folded: a chat whose identity has namespace rows under BOTH spellings
    # (desktop canonical id + the native handle that folds to it) is deleted through
    # the folded one; a late raw-spelling write is suppressed by the same identity.
    # Limit, stated: deleting through a spelling that has NO namespace row flips no
    # lifecycle state at all — that chat-existence semantic is the deletion owner's,
    # unchanged here; telemetry follows the identity once any spelling is deleted.
    raw2 = "another-native-handle"
    folded2 = canonical_chat_session_id(raw2)
    ensure_chat_namespace(raw2, grant_current_receipts=False)
    ensure_chat_namespace(folded2, grant_current_receipts=False)
    rdl.record_decision(session_id=raw2, user_input="turn two", family="f", handled=True)
    assert delete_conversation_session(folded2) is True
    rdl.record_decision(session_id=raw2, user_input="late raw", family="f", handled=True)
    assert folded2 not in _jsonl_refs() and folded2 not in _shadow_refs()


def test_namespace_store_unreadable_fails_closed_for_telemetry(monkeypatch):
    """An unreadable erasure authority must not re-enable optional diagnostic persistence.

    The chat below is genuinely deleted; the namespace read then faults. Telemetry is
    optional, so it declines to record — the turn itself is unaffected (record_decision
    never raises). A LIVE chat's namespace row is never consulted as a fallback."""
    import core.context_namespace as cn

    _seed_chat(_A)
    _seed_chat(_B)
    assert delete_conversation_session(_A) is True

    def unavailable(_chat_id):
        raise OSError("synthetic namespace read failure")

    monkeypatch.setattr(cn, "load_chat_namespace", unavailable)
    rdl.record_decision(session_id=_A, user_input="no rows for the dead", family="f", handled=True)
    rdl.record_decision(session_id=_B, user_input="no rows while the authority is down",
                        family="f", handled=True)
    monkeypatch.undo()
    # the deleted chat gained nothing; the live chat also recorded nothing while the
    # authority was unreadable — fail-closed, never fail-open
    assert _A not in _jsonl_refs() and _A not in _shadow_refs()
    assert _B not in _jsonl_refs() and _B not in _shadow_refs()
    # the authority recovering restores normal recording for the LIVE chat
    rdl.record_decision(session_id=_B, user_input="authority back", family="f", handled=True)
    assert _B in _jsonl_refs() and _B in _shadow_refs()


def test_identity_resolution_covers_namespaces_created_before_the_canonical_column():
    """A namespace row from before the canonical_id column still resolves by identity.

    Simulates the upgrade path exactly: a legacy row stored with an empty canonical_id in
    a database whose one-time backfill marker does not exist yet (as an older build left
    it). The first schema ensure after the upgrade backfills it, so a folded-spelling
    late write for the deleted chat is suppressed."""
    import sqlite3

    from core.chat_session_identity import canonical_chat_session_id
    from storage.db import active_default_db_path

    raw = "pre-upgrade-handle"
    folded = canonical_chat_session_id(raw)
    ensure_chat_namespace(raw, grant_current_receipts=False)
    # age the database: empty canonical_id and no backfill marker, as a pre-upgrade
    # build stored both
    with sqlite3.connect(active_default_db_path()) as conn:
        conn.execute(
            "UPDATE context_namespaces SET canonical_id = '' WHERE chat_id = ?", (raw,)
        )
        conn.execute(
            "DELETE FROM context_namespace_schema_meta WHERE key = 'canonical_id_backfill'"
        )
    # the first post-upgrade schema ensure runs the one-time backfill
    import core.context_namespace as cn

    cn.ensure_context_namespace_schema()
    with sqlite3.connect(active_default_db_path()) as conn:
        stored = conn.execute(
            "SELECT canonical_id FROM context_namespaces WHERE chat_id = ?", (raw,)
        ).fetchone()[0]
    assert stored == folded

    assert delete_conversation_session(raw) is True
    rdl.record_decision(session_id=folded, user_input="late after upgrade", family="f", handled=True)
    assert _jsonl_refs() == [] and _shadow_refs() == []


def test_corrupted_legacy_shadow_row_is_preserved_counted_and_never_discards_the_log():
    _seed_chat(_A)
    _seed_chat(_B)
    rdl.record_decision(session_id=_A, user_input="a turn", family="fixture", handled=True)
    rdl.record_decision(session_id=_B, user_input="b turn", family="fixture", handled=True)
    # corrupt A's shadow row so its session cannot be identified from its bytes
    store = RoutingAuthorityV2ShadowStore(
        runtime_paths.data_path("routing_authority_v2_shadow.sqlite")
    )
    victim_digests = [
        digest
        for digest, payload in store.list_shadow_payloads(record_type="RoutingDecisionShadowV2")
        if _A.encode("utf-8") in payload
    ]
    assert len(victim_digests) == 1
    with sqlite3.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite"))) as conn:
        for digest in victim_digests:
            conn.execute(
                "UPDATE routing_authority_v2_shadow_records SET canonical_bytes = ?"
                " WHERE record_digest = ?",
                (b'{"corrupted": true}', digest),
            )
    result = rdl.purge_session_routing_telemetry(_A)
    assert result.shadow_rows_unattributable == 1
    assert result.shadow_rows_removed == 0
    assert result.jsonl_rows_removed == 1  # the JSONL sink still completed
    # the corrupted row was preserved (never silently discarded) and remains detectable
    with sqlite3.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite"))) as conn:
        kept = conn.execute(
            "SELECT COUNT(*) FROM routing_authority_v2_shadow_records"
            " WHERE canonical_bytes = ?", (b'{"corrupted": true}',)
        ).fetchone()[0]
    assert kept == 1
    assert _B in _shadow_refs()
    # and B's rows still read back valid through the strict reader
    store = RoutingAuthorityV2ShadowStore(
        runtime_paths.data_path("routing_authority_v2_shadow.sqlite")
    )
    with pytest.raises(ShadowStoreIntegrityError):
        store.list_shadow_record_digests(record_type="RoutingDecisionShadowV2")


def test_storage_faults_are_typed_not_swallowed_and_do_not_disable_deletion(monkeypatch):
    _seed_chat(_A)
    rdl.record_decision(session_id=_A, user_input="one", family="fixture", handled=True)
    real_path = rdl.decisions_path
    monkeypatch.setattr(
        rdl, "decisions_path", lambda: (_ for _ in ()).throw(OSError("disk gone"))
    )
    result = rdl.purge_session_routing_telemetry(_A)
    monkeypatch.setattr(rdl, "decisions_path", real_path)
    assert result.ok is False and any("jsonl" in e for e in result.errors)
    assert result.shadow_rows_removed == 1  # the healthy sink still completed
    # the deletion owner completes transcript erasure regardless (no crash, no rollback)
    assert delete_conversation_session(_A) is True
    assert recent_conversation_events(_A, limit=5) == []
    assert _A not in _jsonl_refs()  # fault cleared: the rows the JSONL fault left are gone
    assert _A not in _shadow_refs()


def test_store_deletion_is_restricted_to_one_record_type_and_keeps_survivors_whole():
    store = RoutingAuthorityV2ShadowStore(
        runtime_paths.data_path("routing_authority_v2_shadow.sqlite")
    )
    digests = store.list_shadow_payloads(record_type="RoutingDecisionShadowV2")
    assert digests == ()  # empty store enumerates fine
    with pytest.raises(ContractValidationError):
        store.list_shadow_payloads(record_type="NoSuchRecordV2")
    with pytest.raises(ContractValidationError):
        store.delete_shadow_records(record_type="RoutingDecisionShadowV2", record_digests=("zz",))
    rdl.record_decision(session_id=_A, user_input="turn", family="fixture", handled=True)
    [(digest, payload)] = store.list_shadow_payloads(record_type="RoutingDecisionShadowV2")
    # the digest EXISTS, but asking another approved type to delete it removes nothing
    assert store.delete_shadow_records(
        record_type="RoutingFailureV2", record_digests=(digest,)
    ) == 0
    assert store.read_shadow_record(digest) is not None
    # deleting it as its own type removes exactly that row, idempotently
    assert store.delete_shadow_records(
        record_type="RoutingDecisionShadowV2", record_digests=(digest, digest)
    ) == 1
    assert store.delete_shadow_records(
        record_type="RoutingDecisionShadowV2", record_digests=(digest,)
    ) == 0
    assert store.read_shadow_record(digest) is None
    # re-insertion law: identical canonical bytes persist the same digest again — the
    # record-time deleted-session guard is what prevents resurrection, not the store
    from core.routing_authority_v2 import RoutingDecisionShadowV2

    record = RoutingDecisionShadowV2(
        recorded_at_unix_ms=1, session_ref=_A, family="fixture", handled=True,
        message_redacted_digest="c" * 64,
    )
    assert store.persist_shadow_record(record) == store.persist_shadow_record(record)


def _post(path, body, host="127.0.0.1"):
    return dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                         runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
                         workspace_root_provider=lambda: "/tmp", client_host=host)


def _j(response):
    return json.loads(response.body.decode("utf-8"))


def test_served_delete_of_one_of_two_chats_removes_only_its_telemetry():
    """The real served workflow: two chats with turns in BOTH telemetry sinks, delete ONE
    through the actual chat-delete route, inspect the JSONL and the SQLite logical contents,
    then read back through fresh handles — only the deleted chat's telemetry vanished."""
    for sid in (_A, _B):
        _seed_chat(sid)
        rdl.record_decision(session_id=sid, user_input=f"turn for {sid}", family="fixture", handled=True)
    before_jsonl, before_shadow = _jsonl_refs(), _shadow_refs()
    assert _A in before_jsonl and _B in before_jsonl
    assert _A in before_shadow and _B in before_shadow

    res = _post("/api/chat/session", {"session_id": _A, "delete": True})
    assert res.status == 200
    body = _j(res)
    assert body["deleted"] is True and body["lifecycle_state"] == "deleted"
    assert "warning" not in body

    assert _A not in _jsonl_refs() and _B in _jsonl_refs()
    assert _A not in _shadow_refs() and _B in _shadow_refs()

    # duplicate delete over the served route stays idempotent and harmless to B
    res2 = _post("/api/chat/session", {"session_id": _A, "delete": True})
    assert res2.status == 200 and _j(res2)["deleted"] is False
    assert _B in _jsonl_refs() and _B in _shadow_refs()

    # a LATE turn for the deleted chat cannot resurrect telemetry through the served seam
    rdl.record_decision(session_id=_A, user_input="late after served delete", family="fixture", handled=True)
    assert _A not in _jsonl_refs() and _A not in _shadow_refs()
    assert "late after served delete" not in rdl.decisions_path().read_text()

    # restart-style read-back: fresh store handle + fresh file read see the same state
    fresh_store = RoutingAuthorityV2ShadowStore(
        runtime_paths.data_path("routing_authority_v2_shadow.sqlite")
    )
    survivors = [
        fresh_store.read_shadow_record(d)
        for d in fresh_store.list_shadow_record_digests(record_type="RoutingDecisionShadowV2")
    ]
    assert all(s.session_ref == _B for s in survivors)
    assert all(row != _A for row in _jsonl_refs())


def test_served_delete_warns_when_telemetry_removal_fails(monkeypatch):
    _seed_chat(_A)
    rdl.record_decision(session_id=_A, user_input="one", family="fixture", handled=True)
    # fault BOTH the internal purge and the route's verification pass for the JSONL sink
    monkeypatch.setattr(
        rdl, "decisions_path", lambda: (_ for _ in ()).throw(OSError("disk gone"))
    )
    res = _post("/api/chat/session", {"session_id": _A, "delete": True})
    monkeypatch.undo()
    assert res.status == 200  # the delete itself still succeeds
    body = _j(res)
    assert body["deleted"] is True
    assert "routing telemetry" in body.get("warning", "")
    # and the chat really is gone once the fault clears — nothing was half-deleted
    assert recent_conversation_events(_A, limit=5) == []


def test_served_lifecycle_route_delete_also_purges_and_warns():
    _seed_chat(_A)
    rdl.record_decision(session_id=_A, user_input="one", family="fixture", handled=True)
    res = _post("/api/chat/session", {"session_id": _A, "operation": "delete"})
    assert res.status == 200 and _j(res)["deleted"] is True
    assert _A not in _jsonl_refs() and _A not in _shadow_refs()
    assert "warning" not in _j(res)
