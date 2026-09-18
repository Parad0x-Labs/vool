"""Import atomicity: a crash at ANY point, followed by a retry, yields either the complete
import or no import — never half a session, never duplicated rows.

Crash points are injected through the importer's documented test seams (`CRASH_POINTS`), then
the SAME bundle is imported again: idempotent staging, idempotent publication, deduplicated
JSONL appends and a single INSERT-OR-IGNORE database transaction converge on exactly one copy.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.session_portability import importer as importer_module
from core.session_portability.paths import scoped_home
from tests.session_portability import support
from tests.session_portability.support import SESSION


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.set_session_meta()
    support.seed_tool_receipt()
    support.seed_obligations("req:test:launch")
    support.seed_attachment()
    support.seed_profile_item()


@pytest.fixture()
def _restore_crash_points():
    yield
    importer_module.CRASH_POINTS.clear()


def _counts(home, session_id):
    with scoped_home(home):
        from core.memory.entries import recent_conversation_events
        from core.runtime_continuity import list_runtime_tool_receipts

        turns = list(recent_conversation_events(session_id, limit=100, include_artifacts=True))
        receipts = list_runtime_tool_receipts(session_id, limit=100)
        stage = home / "data" / "chat_attachments"
        attachments = list(stage.glob("*.json")) if stage.is_dir() else []
        conn = sqlite3.connect(home / "data" / "vool_web0_v2.db")
        try:
            dialogue = conn.execute(
                "SELECT COUNT(*) FROM dialogue_turns WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
            obligations = conn.execute("SELECT COUNT(*) FROM obligation_sets").fetchone()[0]
            ledger = conn.execute(
                "SELECT state FROM session_bundle_imports"
            ).fetchall()
        finally:
            conn.close()
        return {
            "turns": len(turns),
            "receipts": len(receipts),
            "attachments": len(attachments),
            "dialogue": dialogue,
            "obligations": obligations,
            "ledger": ledger,
        }


@pytest.mark.parametrize("crash_point", ["after_intent", "after_publish", "before_db_commit"])
def test_crash_then_retry_completes_exactly_once(tmp_path, crash_point):
    from core.session_portability import api

    bundle = tmp_path / "launch.voolsession"
    api.export_session(SESSION, bundle)
    fresh = tmp_path / "fresh-home"

    importer_module.CRASH_POINTS[crash_point] = True
    with pytest.raises(importer_module.ImportCrash):
        api.import_bundle(bundle, home=fresh)
    importer_module.CRASH_POINTS.clear()

    # A crash-free CONTROL import into a separate home defines "exactly once".
    control_home = tmp_path / "control-home"
    control = api.import_bundle(bundle, home=control_home)
    control_counts = _counts(control_home, control["imported_session_id"])

    receipt = api.import_bundle(bundle, home=fresh)
    assert receipt["ok"] is True
    assert receipt["resumed"] is True

    counts = _counts(fresh, receipt["imported_session_id"])
    assert counts["turns"] == control_counts["turns"], "exactly one copy of the transcript"
    assert counts["receipts"] == control_counts["receipts"]
    assert counts["attachments"] == control_counts["attachments"]
    assert counts["dialogue"] == control_counts["dialogue"]
    assert counts["obligations"] == control_counts["obligations"]
    assert [row[0] for row in counts["ledger"]] == ["COMPLETE"]


def test_crash_before_intent_leaves_no_import(tmp_path):
    from core.session_portability import api

    bundle = tmp_path / "launch.voolsession"
    api.export_session(SESSION, bundle)
    fresh = tmp_path / "fresh-home"

    importer_module.CRASH_POINTS["after_stage"] = True
    with pytest.raises(importer_module.ImportCrash):
        api.import_bundle(bundle, home=fresh)
    importer_module.CRASH_POINTS.clear()

    # No ledger row survived -> nothing was imported; the retry runs the FIRST import again.
    counts = _counts(fresh, SESSION)
    assert counts["turns"] == 0
    assert counts["ledger"] == []

    control_home = tmp_path / "control-home"
    control = api.import_bundle(bundle, home=control_home)
    control_counts = _counts(control_home, control["imported_session_id"])

    receipt = api.import_bundle(bundle, home=fresh)
    assert receipt["resumed"] is False
    counts = _counts(fresh, receipt["imported_session_id"])
    assert counts["turns"] == control_counts["turns"]
    assert counts["receipts"] == control_counts["receipts"]
    assert counts["attachments"] == control_counts["attachments"]


def test_crash_after_commit_then_retry_is_idempotent(tmp_path):
    from core.session_portability import api

    bundle = tmp_path / "launch.voolsession"
    api.export_session(SESSION, bundle)
    fresh = tmp_path / "fresh-home"

    importer_module.CRASH_POINTS["after_commit"] = True
    with pytest.raises(importer_module.ImportCrash):
        api.import_bundle(bundle, home=fresh)
    importer_module.CRASH_POINTS.clear()

    # The retry completes the ledger and must NOT duplicate anything.
    control_home = tmp_path / "control-home"
    control = api.import_bundle(bundle, home=control_home)
    control_counts = _counts(control_home, control["imported_session_id"])

    receipt = api.import_bundle(bundle, home=fresh)
    counts = _counts(fresh, receipt["imported_session_id"])
    assert counts["turns"] == control_counts["turns"]
    assert counts["receipts"] == control_counts["receipts"]
    assert counts["attachments"] == control_counts["attachments"]
    assert [row[0] for row in counts["ledger"]] == ["COMPLETE"]

    # And a THIRD attempt is the ordinary already-imported refusal.
    with pytest.raises(api.PortabilityRefused) as err:
        api.import_bundle(bundle, home=fresh)
    assert err.value.code == "BUNDLE_ALREADY_IMPORTED"
