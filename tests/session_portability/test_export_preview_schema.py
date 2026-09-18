"""Typed session-bundle export: schema, preview exactness, deterministic identity.

The bundle is the ONLY contract here: a `.voolsession` file whose canonical payload carries the
conversation turns and canonical identities, obligation terminal states, receipt trail, evidence
references, model/provider metadata, operator profile references, attachment metadata and an
integrity manifest with a schema version. A preview must state EXACTLY what will export, and the
same content exported twice must carry the same bundle identity.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from tests.session_portability import support
from tests.session_portability.support import SESSION


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.set_session_meta()
    support.seed_foreign_session()
    support.seed_tool_receipt()
    support.seed_session_event()
    support.seed_obligations("req:test:launch")
    support.seed_attachment()
    support.seed_profile_item()


def test_export_writes_a_typed_bundle_with_schema_version(tmp_path):
    from core.session_portability import api

    out = tmp_path / "launch.voolsession"
    receipt = api.export_session(SESSION, out)

    assert receipt["ok"] is True
    assert out.is_file()
    summary = api.inspect_bundle(out)
    assert summary["schema"] == api.SCHEMA_NAME
    assert summary["schema_version"] == api.CURRENT_SCHEMA_VERSION
    assert isinstance(summary["schema_version"], int)
    assert summary["session"]["session_id"] == SESSION


def test_bundle_carries_every_required_section(tmp_path):
    from core.session_portability import api

    out = tmp_path / "launch.voolsession"
    api.export_session(SESSION, out)
    payload = api.load_payload(out)

    # Conversation turns and canonical identities.
    assert len(payload["turns"]) == 3
    assert {turn["user"] for turn in payload["turns"]} == {
        "What did we decide about the launch window?",
        "And the fallback?",
        "What did we decide about the launch window? (obligated)",
    }
    assert all(turn["session_id"] == SESSION for turn in payload["turns"])
    assert all(turn["event_id"] for turn in payload["turns"])

    # Receipt trail (tool/effect receipts + the activity event trail).
    assert len(payload["receipts"]["tool_receipts"]) == 1
    assert payload["receipts"]["tool_receipts"][0]["tool_name"] == "http_fetch"
    assert any(
        event["event_type"] == "turn.trace_completed"
        for event in payload["receipts"]["session_events"]
    )

    # Demand/obligation terminal states.
    assert len(payload["obligations"]) == 1
    obligations = payload["obligations"][0]["obligations"]
    assert obligations[0]["state"] == "satisfied"

    # Model/provider metadata.
    model_meta = payload["model_provider"]
    assert model_meta and model_meta[0]["model"] == "test-model"
    assert model_meta[0]["provider_id"] == "test-provider"

    # Attachment metadata + authorized embedded file.
    assert len(payload["attachment_metadata"]) == 1
    assert payload["attachment_metadata"][0]["name"] == "launch-checklist.txt"
    embedded = payload["evidence"]["embedded"]
    assert len(embedded) == 1
    assert embedded[0]["sha256"] == payload["attachment_metadata"][0]["sha256"]

    # Evidence freshness policy is stated, not implied.
    assert payload["evidence"]["freshness_policy"] == "imported_evidence_is_history"

    # Operator profile references.
    assert payload["profile_refs"]["items"], "profile item sourced from this session must export"

    # Integrity manifest.
    with zipfile.ZipFile(out) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["algorithm"] == "sha256"
    assert "bundle.json" in manifest["entries"]

    # Honest scope: served records only — the model's hidden reasoning is not portable cargo.
    assert "hidden" in payload["scope_note"] or "not part" in payload["scope_note"]


def test_export_is_scoped_to_the_one_session(tmp_path):
    from core.session_portability import api

    out = tmp_path / "launch.voolsession"
    api.export_session(SESSION, out)
    payload = api.load_payload(out)

    assert payload["session"]["session_id"] == SESSION
    assert all(turn["session_id"] == SESSION for turn in payload["turns"])
    flat = json.dumps(payload)
    assert "foreign answer" not in flat
    assert support.OTHER not in flat


def test_preview_states_exactly_what_will_export(tmp_path):
    from core.session_portability import api

    preview = api.preview_export(SESSION)
    out = tmp_path / "launch.voolsession"
    receipt = api.export_session(SESSION, out)

    assert preview["session_id"] == SESSION
    assert preview["counts"] == receipt["counts"]
    assert preview["redactions"] == receipt["redactions"]
    assert preview["embedded_files"], "preview must list the attachment files that will ship"
    assert all("path" in entry and "size_bytes" in entry for entry in preview["embedded_files"])
    assert preview["scope_note"] == receipt["scope_note"]
    assert preview["encrypted"] is False


def test_same_content_exports_to_the_same_bundle_identity(tmp_path):
    from core.session_portability import api

    first = tmp_path / "a.voolsession"
    second = tmp_path / "b.voolsession"
    receipt_a = api.export_session(SESSION, first)
    receipt_b = api.export_session(SESSION, second)

    assert receipt_a["bundle_id"] == receipt_b["bundle_id"]
    assert api.load_payload(first)["bundle_id"] == api.load_payload(second)["bundle_id"]


def test_export_refuses_an_unknown_session(tmp_path):
    from core.session_portability import api

    with pytest.raises(api.PortabilityRefused) as err:
        api.preview_export("openclaw:doesnotexist000000")
    assert err.value.code == "SESSION_NOT_FOUND"
