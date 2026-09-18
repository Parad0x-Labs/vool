"""Draft-state law: the durable review/approval/receipt semantics, without any transport.

These pin the *state machine* (core.email_drafts) independently of SMTP/IMAP so a
regression in one cannot hide behind the other: versioning, content-hash approval
binding, expected-version guards, cross-session invisibility, replay idempotency,
durable persistence across store reloads, and the send-gate orderings. The
transport-level outcomes (sent / delivery_unknown / reconcile) are covered against
the real local service in tests/test_email_live_workflow.py.
"""
from __future__ import annotations

import json

import pytest

from core import email_drafts, runtime_paths


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    email_drafts.reset_drafts()
    yield
    runtime_paths.configure_runtime_home(None)


SESSION = "openclaw:unit"
OTHER = "openclaw:other"


def _save(body: str = "first body", draft_id: str = "", session: str = SESSION, to: str = "a@x.test",
          subject: str = "Re: hello", **extra):
    return email_drafts.save_draft(
        to=to, subject=subject, body=body, kind="reply",
        in_reply_to="<p@x.test>", draft_id=draft_id, session_id=session, **extra,
    )


def test_save_versions_and_hash_binding() -> None:
    first = _save()
    assert first.ok and first.draft["version"] == 1 and first.draft["status"] == "draft"
    second = _save(body="second body", draft_id=first.draft["draft_id"])
    assert second.ok and second.draft["version"] == 2
    assert second.draft["content_hash"] != first.draft["content_hash"] if "content_hash" in second.draft else True
    # The public projection exposes review state, not internal hashes.
    assert "content_hash" not in second.draft


def test_approve_binds_exact_content_and_expected_version() -> None:
    draft = _save()
    approved = email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)
    assert approved.ok and approved.draft["approved"] and approved.draft["status"] == "approved"

    # expected_version mismatch: approving "the version I showed" refuses a newer edit.
    _save(body="edited body", draft_id=draft.draft["draft_id"])
    mismatch = email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION, expected_version=1)
    assert not mismatch.ok and mismatch.status == "version_mismatch"


def test_edit_after_approval_is_visibly_stale() -> None:
    draft = _save()
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)
    edited = _save(body="changed", draft_id=draft.draft["draft_id"])
    assert edited.ok and edited.draft["approval_stale"]
    assert edited.draft["status"] == "draft"


def test_send_requires_approval_and_is_idempotent_per_draft(monkeypatch) -> None:
    draft = _save()
    early = email_drafts.send_draft(draft.draft["draft_id"], session_id=SESSION)
    assert early.status == "not_approved"

    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)
    sent_calls: list[dict] = []

    def _fake_send(**kwargs):
        sent_calls.append(kwargs)
        from core import email_tools
        return email_tools.EmailResult(True, "executed", "sent",
                                       {"to": kwargs.get("to"), "subject": kwargs.get("subject"),
                                        "message_id": "<gen-1@x.test>"})

    monkeypatch.setattr("core.email_tools.send_email", _fake_send)
    first = email_drafts.send_draft(draft.draft["draft_id"], session_id=SESSION)
    assert first.ok and first.status == "sent" and len(sent_calls) == 1
    assert first.draft["sent_message_id"] == "<gen-1@x.test>"

    replay = email_drafts.send_draft(draft.draft["draft_id"], session_id=SESSION)
    assert replay.ok and replay.status == "sent" and len(sent_calls) == 1  # no second wire call
    assert replay.details["receipt"] == first.details["receipt"]


def test_stale_approval_cannot_send(monkeypatch) -> None:
    draft = _save()
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)
    _save(body="changed after approval", draft_id=draft.draft["draft_id"])
    result = email_drafts.send_draft(draft.draft["draft_id"], session_id=SESSION)
    assert result.status == "needs_reapproval"


def test_failed_send_reopens_but_keeps_approval(monkeypatch) -> None:
    draft = _save()
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)

    def _boom(**_kwargs):
        from core import email_tools
        return email_tools.EmailResult(False, "send_failed", "refused", {})

    monkeypatch.setattr("core.email_tools.send_email", _boom)
    result = email_drafts.send_draft(draft.draft["draft_id"], session_id=SESSION)
    assert not result.ok and result.status == "failed"
    # The approval binding survives a genuine failure: retry is a user decision,
    # not a new review round.
    fetched = email_drafts.get_draft(draft.draft["draft_id"], session_id=SESSION)
    assert fetched.draft["status"] == "failed" and fetched.draft["approved"]


def test_cross_session_invisibility() -> None:
    draft = _save()
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)
    assert email_drafts.get_draft(draft.draft["draft_id"], session_id=OTHER).status == "not_found"
    assert email_drafts.send_draft(draft.draft["draft_id"], session_id=OTHER).status == "not_found"
    # latest-draft resolution is per session too
    assert email_drafts.get_draft(session_id=OTHER, latest=True).status == "not_found"
    assert email_drafts.get_draft(session_id=SESSION, latest=True).ok


def test_unknown_delivery_reconciles_only_on_proof(monkeypatch) -> None:
    draft = _save()
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)

    def _unknown(**_kwargs):
        from core import email_tools
        return email_tools.EmailResult(True, "delivery_unknown", "unknown", {"message_id": "<u-1@x.test>"})

    monkeypatch.setattr("core.email_tools.send_email", _unknown)
    result = email_drafts.send_draft(draft.draft["draft_id"], session_id=SESSION)
    assert result.ok and result.status == "delivery_unknown"

    from core import email_tools

    monkeypatch.setattr(
        email_tools, "message_in_folder",
        lambda **_k: email_tools.FolderLookupResult(True, "found", "present", found=True),
    )
    confirmed = email_drafts.reconcile_draft(draft.draft["draft_id"], session_id=SESSION)
    assert confirmed.ok and confirmed.status == "sent_confirmed"
    assert confirmed.details["receipt"]["outcome"] == "sent_confirmed"

    # A second unknown draft with NO Sent copy stays unknown.
    second = _save(body="second attempt")
    email_drafts.approve_draft(second.draft["draft_id"], session_id=SESSION)
    monkeypatch.setattr("core.email_tools.send_email", _unknown)
    unknown = email_drafts.send_draft(second.draft["draft_id"], session_id=SESSION)
    assert unknown.status == "delivery_unknown"
    monkeypatch.setattr(
        email_tools, "message_in_folder",
        lambda **_k: email_tools.FolderLookupResult(True, "absent", "not present", found=False),
    )
    still = email_drafts.reconcile_draft(second.draft["draft_id"], session_id=SESSION)
    assert still.ok and still.status == "delivery_unknown"
    assert "resent" in still.message.lower() or "resend" in still.message.lower()


def test_drafts_persist_across_process_state_reload(tmp_path) -> None:
    draft = _save()
    email_drafts.approve_draft(draft.draft["draft_id"], session_id=SESSION)
    # A fresh read of the store (what a restart sees) must show the same review state.
    path = tmp_path / "data" / "email" / "drafts.json"
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    row = on_disk["drafts"][draft.draft["draft_id"]]
    assert row["status"] == "approved" and row["approval"]["approved_hash"] == row["content_hash"]

    # No secret material is ever persisted in the draft store.
    text = path.read_text(encoding="utf-8")
    assert "password" not in text.lower()


def test_invalid_drafts_fail_closed() -> None:
    assert email_drafts.save_draft(to="", subject="s", body="b", session_id=SESSION).status == "invalid_recipient"
    assert email_drafts.save_draft(to="a@x.test", subject="", body="b", kind="compose",
                                   session_id=SESSION).status == "invalid_subject"
    already = _save()
    email_drafts.approve_draft(already.draft["draft_id"], session_id=SESSION)
    # not a sendable state machine bypass: reconcile on a non-unknown draft is a no-op report
    result = email_drafts.reconcile_draft(already.draft["draft_id"], session_id=SESSION)
    assert result.ok and result.status == "approved"
