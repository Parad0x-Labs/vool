"""Import: a bundle lands in a FRESH home with history preserved and evidence marked stale.

Laws under test:
- history is preserved (sessions list, transcript, namespace active, continue-chat hydration);
- imported evidence is NEVER fresh current evidence (fresh=False, lifecycle=restored);
- identity collisions never overwrite an existing session;
- a duplicate import of the same bundle is a typed refusal, not a silent second copy.

Verification runs the PRODUCTION readers inside the fresh home's scope, so what is asserted is
what any lane in a process bound to that home would see.
"""

from __future__ import annotations

import json

import pytest

from core.session_portability.paths import scoped_home
from tests.session_portability import support
from tests.session_portability.support import SESSION


@pytest.fixture(autouse=True)
def _seeded_source_home():
    support.seed_turns()
    support.set_session_meta()
    support.seed_tool_receipt()
    support.seed_session_event()
    support.seed_obligations("req:test:launch")
    support.seed_attachment()
    support.seed_profile_item()


@pytest.fixture()
def exported_bundle(tmp_path):
    from core.session_portability import api

    out = tmp_path / "launch.voolsession"
    api.export_session(SESSION, out)
    return out


def test_import_into_fresh_home_preserves_history(exported_bundle, tmp_path):
    from core.memory.entries import (
        list_conversation_sessions,
        recent_conversation_events,
    )
    from core.session_portability import api

    fresh = tmp_path / "fresh-home"
    receipt = api.import_bundle(exported_bundle, home=fresh)

    assert receipt["ok"] is True
    assert receipt["collision"] is False
    imported = receipt["imported_session_id"]
    assert imported == SESSION, "in an unclaimed fresh home the original canonical id survives"

    with scoped_home(fresh):
        sessions = {
            s["session_id"]
            for s in list_conversation_sessions(limit=100)
            if s["session_id"] == imported
        }
        assert sessions, "the imported session must appear in the sessions list"
        turns = list(recent_conversation_events(imported, limit=10, include_artifacts=True))
        assert len(turns) == 3
        assert any("launch window" in (t.get("user") or "") for t in turns)

        meta = json.loads((fresh / "data" / "chat_session_meta.json").read_text())
        assert meta[imported]["title"] == "Launch planning"


def test_imported_namespace_is_active_and_continue_chat_hydrates(exported_bundle, tmp_path):
    from core.context_namespace import load_chat_namespace
    from core.persistent_memory import augment_history_from_session_log
    from core.session_portability import api

    fresh = tmp_path / "fresh-home"
    receipt = api.import_bundle(exported_bundle, home=fresh)
    imported = receipt["imported_session_id"]

    with scoped_home(fresh):
        namespace = load_chat_namespace(imported)
        assert namespace is not None
        assert namespace.lifecycle_state == "active"

        hydrated = augment_history_from_session_log(
            [], session_id=imported, user_text="one more question about the launch"
        )
        flattened = json.dumps(hydrated)
        assert "Thursday 09:00 UTC" in flattened


def test_imported_receipts_and_obligations_land(exported_bundle, tmp_path):
    from core.runtime_continuity import list_runtime_tool_receipts
    from core.session_portability import api

    fresh = tmp_path / "fresh-home"
    receipt = api.import_bundle(exported_bundle, home=fresh)

    with scoped_home(fresh):
        receipts = list_runtime_tool_receipts(receipt["imported_session_id"], limit=50)
        assert len(receipts) == 1
        assert receipts[0]["tool_name"] == "http_fetch"

        assert receipt["counts"]["obligation_sets"] == 1
        from core.conductor.obligation_ledger import request_text

        for set_row in receipt["obligation_sets"]:
            assert (
                request_text(set_row["set_id"], set_row["version"])
                == "What did we decide about the launch window?"
            )
        # The terminal disposition traveled inside the snapshot.
        from core.conductor.obligation_ledger import _read_snapshot

        restored = _read_snapshot(set_row["set_id"], set_row["version"])
        assert restored["obligations"][0]["state"] == "satisfied"


def test_imported_evidence_is_marked_stale_history(exported_bundle, tmp_path):
    """The core portability law: imported evidence is history. Its persisted record carries the
    reader-facing non-fresh marks (fresh=False, lifecycle=restored), so the shared observation
    reader — the same one production lanes read — refuses it as a current observation."""
    from core.observation_evidence import records_a_usable_observation
    from core.session_portability import api

    fresh = tmp_path / "fresh-home"
    receipt = api.import_bundle(exported_bundle, home=fresh)
    assert receipt["evidence_marked_stale"] == 1

    with scoped_home(fresh):
        stage = fresh / "data" / "chat_attachments"
        manifests = [json.loads(p.read_text()) for p in sorted(stage.glob("*.json"))]
        assert manifests, "the authorized attachment must exist in the fresh home"
        record = manifests[0]
        assert record["session_id"] == receipt["imported_session_id"]
        assert record.get("fresh") is False
        assert str(record.get("lifecycle") or "") == "restored"
        assert record.get("imported_bundle_id") == receipt["bundle_id"]
        assert records_a_usable_observation(record) is False


def test_identity_collision_mints_new_session_and_never_overwrites(exported_bundle, tmp_path):
    """The target home ALREADY has a session with the bundle's id: the import lands under a new
    canonical id, and the existing session's rows are byte-for-byte untouched."""
    from core.memory.entries import list_conversation_sessions, recent_conversation_events
    from core.session_portability import api

    fresh = tmp_path / "fresh-home"
    with scoped_home(fresh):
        support.seed_turns()  # the resident session that owns the bundle's original id

    with scoped_home(fresh):
        before = list(recent_conversation_events(SESSION, limit=10, include_artifacts=True))

    receipt = api.import_bundle(exported_bundle, home=fresh)

    with scoped_home(fresh):
        after = list(recent_conversation_events(SESSION, limit=10, include_artifacts=True))
        assert receipt["collision"] is True
        assert receipt["imported_session_id"] != SESSION
        assert after == before, "the resident session's rows must be untouched by the import"

        sessions = {s["session_id"] for s in list_conversation_sessions(limit=200)}
        assert SESSION in sessions
        assert receipt["imported_session_id"] in sessions


def test_duplicate_import_is_a_typed_refusal_and_changes_nothing(exported_bundle, tmp_path):
    from core.memory.entries import list_conversation_sessions
    from core.session_portability import api

    fresh = tmp_path / "fresh-home"
    first = api.import_bundle(exported_bundle, home=fresh)

    with scoped_home(fresh):
        sessions_before = {s["session_id"]: s for s in list_conversation_sessions(limit=200)}

        with pytest.raises(api.PortabilityRefused) as err:
            api.import_bundle(exported_bundle, home=fresh)
        assert err.value.code == "BUNDLE_ALREADY_IMPORTED"

        sessions_after = {s["session_id"]: s for s in list_conversation_sessions(limit=200)}
        assert sessions_after.keys() == sessions_before.keys()
        assert sessions_after[first["imported_session_id"]]["turn_count"] == 3
