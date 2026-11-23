"""A change that would change nothing asks nothing, and a judge's reminders-or-calendar pick offers both.

Mac gate on 561e3e9d0 (2026-11-23, installed app, qwen3:8b), two read asks that met a write:
- right after "Neuer Kontakt bitte: Lena Hoffmann, Telefon 0172 4455667" saved Lena, "do you have Lena's phone
  number?" was called as contacts.save of the same contact, and the person was asked to approve "save a contact"
  before being told anything. A save of a saved name carrying nothing that contact lacks is the lookup of it: it is
  answered through the same executor as contacts.search, with no card. A save carrying anything new still asks.
- "parodyk mano priminimus" ("show my reminders"), right after a reminder was set, was judged a calendar ask; with
  no reminder tool offered, the model wrote a calendar event out of the reminder. A pick of either seats both.

In process: the executor the served tool loop calls, a tmp home per test, no model and no network. Names, numbers
and which details the call repeats are drawn at random.
"""
from __future__ import annotations

import random

import pytest

from tests.pa_beta_gate._pc_calendar_rig import prepare_home
from tests.test_reminders_the_model_can_set import _model_call

PEOPLE = ("Lena Hoffmann", "Rūta Kazlauskienė", "Marco Bianchi", "Aoife Byrne", "Joaquín Pérez")


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _fresh_runtime_state():
    from core import capability_graph as cg
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    cg.reset()
    yield
    reset_mode_permission_state()
    cg.reset()


def _number(rng: random.Random) -> str:
    return f"+3706{rng.randint(1000000, 9999999)}"


def _saved(name: str, phone: str, email: str = "") -> None:
    from core.contacts import store

    endpoints = [{"kind": "phone", "value": phone}] + ([{"kind": "email", "value": email}] if email else [])
    store.create_contact(display_name=name, actor="owner", endpoints=endpoints)


def _contacts() -> list[dict]:
    from core.contacts import store

    return list(store.active_contacts())


@pytest.mark.parametrize("seed", range(6))
def test_a_save_of_a_saved_contact_with_nothing_new_is_answered_as_its_lookup(seed, home) -> None:
    rng = random.Random(f"save-nothing-{seed}")
    name, phone = rng.choice(PEOPLE), _number(rng)
    email = rng.choice(("", f"person{seed}@example.org"))
    _saved(name, phone, email)
    before = _contacts()
    arguments = {"name": rng.choice((name, name.upper(), f" {name} "))}
    if rng.random() < 0.7:
        arguments["phone"] = phone
    if email and rng.random() < 0.5:
        arguments["email"] = email

    answered = _model_call("contacts.save", arguments, session_id=f"chat-save-nothing-{seed}")

    assert answered.status != "pending_approval" and "approval_request" not in (answered.details or {}), answered.details
    assert answered.ok and answered.tool_name == "contacts.search", (answered.tool_name, answered.response_text)
    assert phone in answered.response_text, answered.response_text
    assert _contacts() == before


@pytest.mark.parametrize("seed", range(4))
def test_a_save_that_adds_anything_still_goes_through_the_save(seed, home) -> None:
    rng = random.Random(f"save-new-{seed}")
    name, phone = rng.choice(PEOPLE), _number(rng)
    _saved(name, phone)
    arguments = {"name": name, **rng.choice((
        {"phone": _number(rng)}, {"email": f"other{seed}@example.net"}, {"aliases": ["Nickname"]},
    ))}

    asked = _model_call("contacts.save", arguments, session_id=f"chat-save-new-{seed}")

    assert asked.tool_name == "contacts.save", (asked.tool_name, asked.response_text)


def test_a_new_name_is_still_a_save(home) -> None:
    asked = _model_call("contacts.save", {"name": "Brand New Person", "phone": "+37061234567"}, session_id="chat-save-fresh")

    assert asked.tool_name == "contacts.save", (asked.tool_name, asked.response_text)


@pytest.mark.parametrize("picked", ["reminders", "calendar"])
def test_a_reminders_or_calendar_pick_offers_the_tools_of_both(picked) -> None:
    from core import plugin_ask

    text = f"judged-{picked}-{random.Random(picked).getrandbits(32):08x}"
    plugin_ask._record_builtins(text, (picked,))
    try:
        seats = plugin_ask.judged_builtin_seats(text)
    finally:
        plugin_ask.forget()

    assert plugin_ask.judged_builtins(text) == ()
    assert {"operator.list_reminders", "operator.schedule_reminder", "operator.show_agenda"} <= set(seats), seats
    first = dict(plugin_ask._BUILTIN_SEATS)[picked][0]
    assert seats[0] == first, seats   # the judged pick leads


def test_another_pick_seats_only_its_own_tools() -> None:
    from core import plugin_ask

    text = "judged-contacts-only"
    plugin_ask._record_builtins(text, ("contacts",))
    try:
        seats = plugin_ask.judged_builtin_seats(text)
    finally:
        plugin_ask.forget()

    assert set(seats) == set(dict(plugin_ask._BUILTIN_SEATS)["contacts"]), seats
