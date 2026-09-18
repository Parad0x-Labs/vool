"""The contracts other authorities bind to at final convergence -- stated as data and one seam,
never as a second exporter or a second journal.

SESSION PORTABILITY -- `build/session-portability-p1-20260902`'s collector embeds bytes for
`staged`/`bound` attachments and treats `released` as a receipt whose bytes are gone. That is
true for a picked file and false for a retained document. The conformant rule is
`chat_attachments.portable_documents`: embed every record it returns, under the member path the
contract names, with bytes that hash to the record's sha256, carrying the document identity
fields. `verify_portable_export` is the checker an exporter's test calls.

BLACKBOX COVERAGE -- `build/blackbox-coverage-p1-20260902` registers a `MutationCapability` per
local mutation path. The document store's declaration is `BLACKBOX_DOCUMENT_STORE_CAPABILITY`:
machine scope, irreversible, post-image only, terminal-only receipts (the Activity rows every
write leaves), no rollback. When the coverage module is on the tree its own validator judges the
declaration; here its vocabulary is pinned so the two cannot drift.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from core import chat_attachments as ca
from core import runtime_paths

SESSION = "openclaw:d0c0d0c0d0c0d0c0c0c0"
OTHER = "openclaw:d0c0d0c0d0c0d0c0c0c1"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _sent_document(session: str, turn: str, text: str) -> dict:
    record = ca.stage_document(session_id=session, data=text.encode("utf-8"))
    ca.bind_to_turn(session_id=session, turn_id=turn, attachment_ids=[record["id"]])
    ca.release_turn(session_id=session, turn_id=turn, outcomes={record["id"]: "read"})
    return record


def _exporter_following_the_contract(session: str) -> tuple[list[dict], dict[str, bytes]]:
    """What a conformant collector produces: the seam's records, the member bytes it names."""
    embedded: list[dict] = []
    members: dict[str, bytes] = {}
    for record in ca.portable_documents(session):
        data = pathlib.Path(record["bytes_path"]).read_bytes()
        members[record["path"]] = data
        embedded.append({k: v for k, v in record.items() if k != "bytes_path"})
    return embedded, members


def _exporter_following_the_old_rule(session: str) -> tuple[list[dict], dict[str, bytes]]:
    """The collector's current rule: bytes only for staged/bound, released = receipt."""
    embedded: list[dict] = []
    members: dict[str, bytes] = {}
    for record in ca.portable_documents(session):
        if record["state"] not in ("staged", "bound"):
            continue
        data = pathlib.Path(record["bytes_path"]).read_bytes()
        members[record["path"]] = data
        embedded.append({k: v for k, v in record.items() if k != "bytes_path"})
    return embedded, members


def test_a_released_document_is_portable_with_its_bytes_and_identity() -> None:
    sent = _sent_document(SESSION, "turn-1", "2026-09-02T10:00:00Z INFO the exported log\n" * 200)
    staged_file = ca.stage_attachment(session_id=SESSION, declared_name="n.txt", declared_type="text/plain", data=b"draft file\n")
    picked = ca.stage_attachment(session_id=SESSION, declared_name="gone.txt", declared_type="text/plain", data=b"spent file\n")
    ca.bind_to_turn(session_id=SESSION, turn_id="turn-2", attachment_ids=[picked["id"]])
    ca.release_turn(session_id=SESSION, turn_id="turn-2", outcomes=None)
    _sent_document(OTHER, "turn-1", "another chat's document\n" * 100)
    portable = ca.portable_documents(SESSION)
    ids = {r["attachment_id"]: r for r in portable}
    assert set(ids) == {sent["id"], staged_file["id"]}, "the released document and the staged draft are portable; the spent file is a receipt"
    doc = ids[sent["id"]]
    assert doc["state"] == "released" and doc["document"] is True and doc["path"] == f"attachments/{sent['sha256']}"
    assert doc["turn_id"] == "turn-1" and doc["chars"] > 0 and doc["lines"] == 201 and doc["type_rule"] == "log"
    assert hashlib.sha256(pathlib.Path(doc["bytes_path"]).read_bytes()).hexdigest() == sent["sha256"]
    assert set(ca.PORTABILITY_CONTRACT["embedded_fields"]) <= set(doc)


def test_the_checker_passes_a_conformant_export_and_names_what_the_old_rule_drops() -> None:
    sent = _sent_document(SESSION, "turn-1", "line\n" * 500)
    assert ca.verify_portable_export(SESSION, *_exporter_following_the_contract(SESSION)) == []
    problems = ca.verify_portable_export(SESSION, *_exporter_following_the_old_rule(SESSION))
    assert problems and sent["id"] in problems[0] and "missing from the export" in problems[0]
    # Wrong bytes under the right member are named too.
    embedded, members = _exporter_following_the_contract(SESSION)
    members[embedded[0]["path"]] = b"tampered"
    assert any("do not hash" in p for p in ca.verify_portable_export(SESSION, embedded, members))


def test_an_erased_document_is_a_receipt_not_a_member() -> None:
    sent = _sent_document(SESSION, "turn-1", "line\n" * 500)
    ca.erase_document(session_id=SESSION, attachment_id=sent["id"])
    assert ca.portable_documents(SESSION) == []
    receipt = ca.receipt_for_turn(session_id=SESSION, turn_id="turn-1")[0]
    assert receipt["id"] == sent["id"] and receipt["state"] == "erased" and receipt["sha256"] == sent["sha256"]


# --------------------------------------------------------------------------- Blackbox coverage


def test_the_document_store_capability_is_a_valid_irreversible_machine_scope_declaration() -> None:
    declaration = ca.BLACKBOX_DOCUMENT_STORE_CAPABILITY
    assert declaration["tool"] == "chat.document_store" and declaration["recorder"] == "core.chat_attachments"
    assert declaration["scope"] == "machine" and declaration["effect_class"] == "irreversible"
    assert declaration["snapshot_strategy"] == "postimage_only" and declaration["receipt_lifecycle"] == "terminal_only"
    assert declaration["rollback_support"] == "none"
    assert {"attachment_staged", "attachment_erased", "attachment_released"} <= set(declaration["receipts"])
    assert all("{data_dir}/chat_attachments/{attachment_id}" in p for p in declaration["paths"])
    try:
        from core.blackbox.coverage.capability import MutationCapability
    except ImportError:
        # The coverage lane is not on this tree; its vocabulary is pinned above and final
        # convergence registers the declaration through `register_capability`.
        return
    capability = MutationCapability.from_dict({k: v for k, v in declaration.items() if k in {"tool", "scope", "effect_class", "snapshot_strategy", "receipt_lifecycle", "rollback_support", "recorder", "notes"}})
    assert capability is not None and capability.problems() == []


def test_every_document_write_leaves_the_receipt_the_declaration_names(monkeypatch) -> None:
    """The declaration's receipt list is the Activity vocabulary the served door emits."""
    from core.web.api import service

    emitted: list[str] = []
    monkeypatch.setattr(service, "emit_runtime_event", lambda ctx, *, event_type, message, details: emitted.append(event_type))
    service._attachment_event(SESSION, event_type="attachment_staged", message="m", details={})
    service._attachment_event(SESSION, event_type="attachment_erased", message="m", details={})
    assert set(emitted) <= set(ca.BLACKBOX_DOCUMENT_STORE_CAPABILITY["receipts"])
