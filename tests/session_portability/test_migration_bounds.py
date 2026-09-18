"""Schema migration and bounded packs.

A bundle names its schema version. Older payloads migrate forward through registered steps;
a NEWER-than-runtime schema refuses closed rather than guessing. Every section is bounded: a
bundle over the cumulative identity/evidence/profile bounds refuses with a typed error.
"""

from __future__ import annotations

import json

import pytest

from tests.session_portability import support
from tests.session_portability.support import SESSION


@pytest.fixture(autouse=True)
def _seeded_home():
    support.seed_turns()
    support.seed_tool_receipt()
    support.seed_attachment()


def _wrap_bundle(tmp_path, name: str, payload: dict, attachments: dict[str, bytes]) -> object:
    """Build a bundle file from a RAW payload dict through the format's own writer."""
    from core.session_portability import bundle as bundle_format

    out = tmp_path / name
    bundle_format.write_bundle(out, payload, attachments, passphrase="")
    return out


def test_older_schema_migrates_forward_on_import(tmp_path):
    from core.session_portability import api

    payload = api.load_payload(_export_seeded(tmp_path))
    # Rewind the payload to the v0 shape: turns lived under `items`, there was no
    # evidence freshness policy yet, and no receipts envelope.
    legacy = dict(payload)
    legacy["schema_version"] = 0
    legacy["items"] = [
        {"session_id": turn["session_id"], "user": turn["user"], "assistant": turn["assistant"]}
        for turn in payload["turns"]
    ]
    legacy.pop("turns")
    legacy.pop("receipts", None)
    legacy.pop("evidence", None)

    out = _wrap_bundle(tmp_path, "legacy.voolsession", legacy, {})
    fresh = tmp_path / "fresh-home"
    receipt = api.import_bundle(out, home=fresh)

    assert receipt["ok"] is True
    assert receipt["migrated_from"] == 0
    assert receipt["counts"]["turns"] == 2


def test_newer_schema_refuses_closed(tmp_path):
    from core.session_portability import api

    payload = api.load_payload(_export_seeded(tmp_path))
    payload["schema_version"] = api.CURRENT_SCHEMA_VERSION + 100
    out = _wrap_bundle(tmp_path, "future.voolsession", payload, {})

    with pytest.raises(api.PortabilityRefused) as err:
        api.import_bundle(out, home=tmp_path / "fresh-home")
    assert err.value.code == "BUNDLE_SCHEMA_TOO_NEW"


def test_export_refuses_over_the_turn_bound(tmp_path, monkeypatch):
    from core.session_portability import api
    from core.session_portability import schema

    monkeypatch.setattr(schema, "BOUNDS", {**schema.BOUNDS, "turns": 1})

    with pytest.raises(api.PortabilityRefused) as err:
        api.preview_export(SESSION)
    assert err.value.code == "BUNDLE_LIMIT_EXCEEDED"


def test_import_refuses_over_the_profile_bound(tmp_path, monkeypatch):
    from core.session_portability import api

    payload = api.load_payload(_export_seeded(tmp_path))
    payload["profile_refs"]["items"] = [
        {"category": f"cat{i}", "scope": "global", "scope_key": "", "value_text": f"v{i}", "value_json": ""}
        for i in range(5)
    ]
    out = _wrap_bundle(tmp_path, "wide.voolsession", payload, {})

    from core.session_portability import schema

    monkeypatch.setattr(schema, "BOUNDS", {**schema.BOUNDS, "profile_items": 2})

    with pytest.raises(api.PortabilityRefused) as err:
        api.import_bundle(out, home=tmp_path / "fresh-home")
    assert err.value.code == "BUNDLE_LIMIT_EXCEEDED"


def test_import_refuses_over_the_embedded_bytes_bound(tmp_path, monkeypatch):
    from core.session_portability import api

    big = b"x" * (4 * 1024)
    from core.chat_attachments import stage_attachment

    staged = stage_attachment(
        session_id=SESSION, declared_name="big.txt", declared_type="text/plain", data=big
    )
    payload = api.load_payload(_export_seeded(tmp_path))
    payload["evidence"]["embedded"] = [
        {
            "attachment_id": staged["id"],
            "name": "big.txt",
            "kind": "text",
            "media_type": "text/plain",
            "size_bytes": len(big),
            "sha256": staged["sha256"],
            "path": f"attachments/{staged['sha256']}",
        }
    ]
    out = _wrap_bundle(tmp_path, "big.voolsession", payload, {f"attachments/{staged['sha256']}": big})

    from core.session_portability import schema

    monkeypatch.setattr(schema, "BOUNDS", {**schema.BOUNDS, "embedded_total_bytes": 1024})

    with pytest.raises(api.PortabilityRefused) as err:
        api.import_bundle(out, home=tmp_path / "fresh-home")
    assert err.value.code == "BUNDLE_LIMIT_EXCEEDED"


def _export_seeded(tmp_path):
    from core.session_portability import api

    out = tmp_path / "seeded.voolsession"
    api.export_session(SESSION, out)
    return out
