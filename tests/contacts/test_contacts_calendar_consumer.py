"""The Contacts calendar-attendee consumer: saved names resolve before a proposal exists.

Wired when the calendar/notes chain and Contacts first coexisted in the release candidate
(Goal 2, 2026-09-18), through the attendees module the contacts lane shipped beside the
calendar owner. The laws: a named saved contact becomes exactly one saved email endpoint;
an ambiguous or address-less name makes the proposal ask (nothing scheduled); an explicit
address stays an attendee as written AND a fragment of one (the slot splitter can cut an
address at its @) never resolves as a separate name -- the fragment guard exists because
'with alex.chen@direct.example' yielded the part 'alex', which name-matched an unrelated
saved Alex and silently invited a second address.
"""
from __future__ import annotations

import pytest

from core.contacts.store import reset_contacts_for_tests

from core.contacts import store as contacts_store
from core.contacts.attendees import attendees_from_request


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_contacts_for_tests()
    yield
    reset_contacts_for_tests()


@pytest.fixture(autouse=True)
def _alex(_fresh_store):
    reset_contacts_for_tests()
    contacts_store.create_contact(
        display_name="Alex Chen", actor=contacts_store.ACTOR_OWNER,
        endpoints=[{"kind": "email", "label": "work", "value": "alex.chen@work.example"}],
    )
    contacts_store.create_contact(display_name="Sam Lee", actor=contacts_store.ACTOR_OWNER)
    contacts_store.create_contact(display_name="Sam Ortiz", actor=contacts_store.ACTOR_OWNER, aliases=["Sam"])


def test_a_named_saved_contact_resolves_to_its_exact_saved_endpoint():
    r = attendees_from_request("invite Alex Chen to planning tomorrow at 9am", explicit=[])
    assert r.addresses == ("alex.chen@work.example",) and not r.unresolved


def test_an_ambiguous_name_is_a_question():
    r = attendees_from_request("invite Sam to planning tomorrow at 9am", explicit=[])
    assert r.addresses == () and r.unresolved and "Which one" in r.question


def test_an_explicit_address_stands_alone_and_its_fragment_never_name_resolves():
    r = attendees_from_request("with alex.chen@direct.example at 3pm", explicit=["alex.chen@direct.example"])
    # one attendee: the address as written; the 'alex' fragment must not pull in the saved Alex
    assert r.addresses == ("alex.chen@direct.example",)


def test_a_literal_and_a_named_contact_are_both_attendees_when_both_are_named():
    r = attendees_from_request(
        "invite Alex Chen and alex.chen@direct.example to planning", explicit=["alex.chen@direct.example"])
    assert sorted(r.addresses) == ["alex.chen@direct.example", "alex.chen@work.example"]
