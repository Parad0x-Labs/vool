"""Contacts requests at the runtime's front door: the text every lane reads keeps an email address whole, and the memory lane
hands a Contacts save to the model instead of storing it as a fact.

Both defects were measured on the served Contacts journey (stage green-08, 2026-09-15): the stored request read
"alex. chen @ example. test", and "Save Alex Chen as a contact: ..." was answered "Locked in. I'll remember that." with no
contact saved. What executes: core.input_normalizer.normalize_user_text (the text agent.run_once adopts as effective_input
and the checkpoint stores for request provenance), core.persistent_memory.maybe_handle_memory_command, and the Contacts
provenance check. No model, daemon or network.
"""
from __future__ import annotations

import pytest

from core.contacts.endpoints import normalize_endpoint
from core.contacts.tools import value_in_text
from core.input_normalizer import normalize_user_text
from core.persistent_memory import maybe_handle_memory_command

SESSION = "openclaw:contacts-ingress"


@pytest.mark.parametrize("text, address", [
    ("Save Alex Chen as a contact: work email alex.chen@example.test, Telegram alexchen_kiln.", "alex.chen@example.test"),
    ("email zoe.ng+kiln@fixture-mail.example.co.uk about the glaze order", "zoe.ng+kiln@fixture-mail.example.co.uk"),
    ("Please send it to (priya_nair@studio.example.test).", "priya_nair@studio.example.test"),
])
def test_the_normalized_request_keeps_an_email_address_whole(text: str, address: str) -> None:
    normalized = normalize_user_text(text).normalized_text
    assert address in normalized, normalized
    assert value_in_text(normalize_endpoint({"kind": "email", "value": address}), normalized)


def test_control_rewrites_around_a_protected_address_still_apply() -> None:
    normalized = normalize_user_text("can u email alex.chen@example.test then call the kiln shop").normalized_text
    assert "you" in normalized.split() and "alex.chen@example.test" in normalized and normalized.endswith("call the kiln shop"), normalized
    assert "@" not in normalize_user_text("the meeting is at noon").normalized_text


@pytest.mark.parametrize("text", [
    "Save Alex Chen as a contact: work email alex.chen@example.test.",
    "Store Zoë Ng in my address book with zoe.ng@fixture.test.",
    "save contact Priya Nair, phone +44 7700 900123",
])
def test_the_memory_lane_hands_a_contacts_save_to_the_model(text: str) -> None:
    assert maybe_handle_memory_command(text, session_id=SESSION) == (False, "")


@pytest.mark.parametrize("text", [
    "What is Alex Chen's Telegram handle?",
    "Show me Priya's Instagram and Telegram handles",
    "What's on the program for Alex's visit?",
])
def test_a_word_that_contains_a_hardware_word_plans_no_machine_specs_inspection(text: str) -> None:
    # measured on the served journey (green-14): "Telegram" carried "ram", and the planner answered with this host's hardware
    from core.execution.planner import _extract_machine_specs_request

    assert _extract_machine_specs_request(text) is None


@pytest.mark.parametrize("text", [
    "how much ram does this machine have?",
    "what cpu is in this machine",
    "How many CPU cores?",
    "what are my machine specifications",
])
def test_control_hardware_questions_still_plan_the_specs_inspection(text: str) -> None:
    from core.execution.planner import _extract_machine_specs_request

    assert _extract_machine_specs_request(text) == {}


@pytest.mark.parametrize("text", [
    "Save that my favourite glaze is celadon.",
    "Keep in mind my contact at the bank is Priya.",
])
def test_controls_statements_that_mention_a_contact_are_still_memories(text: str) -> None:
    # the memory lane still CLAIMS these; what it answers depends on whether this probe's chat is active, which is not the
    # question here (the served journey is where a claimed turn's reply is read)
    handled, reply = maybe_handle_memory_command(text, session_id=SESSION)
    assert handled is True and reply, reply
