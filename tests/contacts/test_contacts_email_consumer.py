"""The Contacts email-draft consumer: one resolution per recipient before a draft exists.

Wired when the email lane and Contacts first coexisted in the release candidate (Goal 2,
2026-09-18). The law mirrors the wallet consumer: a literal address stays that address; a
saved contact resolves to exactly one saved email endpoint; a request that NAMED a contact
and cannot pick one address is a QUESTION for the user (nothing drafted); an unknown name is
a question too. A draft that IS saved carries exact addresses -- never a guessed payee.
"""
from __future__ import annotations

import pytest

from core.contacts import store as contacts_store
from core.contacts.store import reset_contacts_for_tests
from core.runtime_execution_tools import execute_runtime_tool


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_contacts_for_tests()
    yield
    reset_contacts_for_tests()


@pytest.fixture()
def email_lane(monkeypatch):
    from core import policy_engine

    base = dict(policy_engine.load())
    base["email"] = {**(base.get("email") or {}), "read_enabled": True, "send_enabled": True}
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", base)
    yield


def _draft(to: str, ctx: dict):
    return execute_runtime_tool(
        "email.draft.save", {"to": [to], "subject": "consumer pin", "body": "hello"},
        source_context=ctx,
    )


def test_a_literal_address_drafts_as_itself(email_lane, tmp_path):
    res = _draft("direct@example.org", {"session_id": "s1", "workspace_root": str(tmp_path)})
    assert res.ok and res.status == "saved", (res.status, res.response_text)
    assert res.details["draft"]["to"] == ["direct@example.org"]


def test_a_saved_contact_drafts_its_exact_saved_address(email_lane, tmp_path):
    contacts_store.create_contact(
        display_name="Alex Chen", actor=contacts_store.ACTOR_OWNER,
        endpoints=[{"kind": "email", "label": "work", "value": "alex.chen@work.example"}],
    )
    res = _draft("Alex Chen", {"session_id": "s2", "workspace_root": str(tmp_path)})
    assert res.ok and res.status == "saved", res.response_text
    # the draft carries the EXACT saved endpoint, never the display name
    assert res.details["draft"]["to"] == ["alex.chen@work.example"]


def test_an_ambiguous_name_is_a_question_and_nothing_is_drafted(email_lane, tmp_path):
    contacts_store.create_contact(display_name="Sam Lee", actor=contacts_store.ACTOR_OWNER)
    contacts_store.create_contact(display_name="Sam Ortiz", actor=contacts_store.ACTOR_OWNER, aliases=["Sam"])
    res = _draft("Sam", {"session_id": "s3", "workspace_root": str(tmp_path)})
    assert res.ok is True and res.status == "recipient_ambiguous", (res.status, res.response_text)
    assert "Nothing was drafted" in res.response_text


def test_an_unknown_name_is_a_question_and_nothing_is_drafted(email_lane, tmp_path):
    res = _draft("Nobody Here", {"session_id": "s4", "workspace_root": str(tmp_path)})
    assert res.ok is True and res.status == "recipient_not_found", (res.status, res.response_text)
    assert "Nothing was drafted" in res.response_text
