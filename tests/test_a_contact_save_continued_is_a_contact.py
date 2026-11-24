"""A message that continues a contact save is the next contact, not a memory line.

Fresh-question audit of 02ff11496: right after a contact was saved, "save another one too, Odette Marsh, +1 555 0177"
was read as a keep by both memory readings and answered "Locked in"; no contact was made. Names, numbers and languages
are drawn at random; the judges are scripted.
"""
from __future__ import annotations

import random

import pytest

import core.intent_arbiter as intent_arbiter
from core import plugin_ask


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugin_ask.forget()
    monkeypatch.setattr(intent_arbiter, "arbitrate_memory_kind", lambda *_a, **_k: "keep")
    yield
    plugin_ask.forget()


def _context(previous: str, text: str) -> dict:
    return {"conversation_history": [{"role": "user", "content": previous}, {"role": "assistant", "content": "ok"},
                                     {"role": "user", "content": text}]}


@pytest.mark.parametrize("seed", range(8))
def test_the_next_contact_after_a_contact_save_is_a_contact(seed):
    rng = random.Random(f"next-contact-{seed}")
    first = rng.choice(["save Odette Farrow, my physio, +1 555 0142", "išsaugok kontaktą Rūta, +370 612 34567",
                        "speichere Jörg Brandt mit der Nummer 0151 2345678"])
    value = rng.choice([f"+1 555 {rng.randint(1000, 9999)}", f"0{rng.randint(150, 179)} {rng.randint(1000000, 9999999)}",
                        f"{rng.choice(['odette', 'jonas', 'lena'])}@example.org"])
    text = rng.choice([f"save another one too, Odette Marsh, {value}", f"ir dar vieną: Jonas Petraitis {value}",
                       f"noch einen, Lena Vogt {value}"])
    plugin_ask._record_builtins(first, ("contacts",))
    picked = plugin_ask._memory_kind_confirmed(text, ("memory_keep",), _context(first, text), "chat")
    assert picked == ("contacts",)


@pytest.mark.parametrize("seed", range(4))
def test_a_keep_after_another_order_or_without_a_contact_value_stays_a_keep(seed):
    rng = random.Random(f"keep-stays-{seed}")
    first, key = rng.choice([("remind me at 9 to call the bank", "reminders"),
                             ("save Odette Farrow, my physio, +1 555 0142", "contacts")])
    text = ("remember my gym locker code is 4471" if key == "contacts"
            else "remember that my sister's number is +1 555 0199")
    plugin_ask._record_builtins(first, (key,))
    picked = plugin_ask._memory_kind_confirmed(text, ("memory_keep",), _context(first, text), "chat")
    assert picked == ("memory_keep",)
