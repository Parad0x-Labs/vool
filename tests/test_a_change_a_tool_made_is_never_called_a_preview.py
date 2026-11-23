"""A change a tool really made is never rewritten as "only a preview ran".

Mac gate on f3771b6b7: an approved contacts.save stored "Lukas Berger"; the model's reply "Lukas Berger has been saved
as a contact" was replaced by "This change has not been made: only a preview ran, nothing was applied." The publish
check decided which tools change things from a list of verbs at the end of the tool's name, and "save" and
"schedule_reminder" are not on it. It now asks the tool's own contract first. Names and replies are built at random.
"""
from __future__ import annotations

import random

import pytest

import core.final_answer_authorship as authorship
import core.turn_model_call_ledger as ledger

CHANGES = (
    ("contacts.save", "{name} has been saved as a contact with the number +49 151 {n}."),
    ("operator.schedule_reminder", "Your reminder has been saved: {name} on Friday at {h}:00."),
    ("contacts.update", "I have updated {name}'s number to +370 6{n}."),
    ("operator.cancel_reminder", "The reminder about {name} has been removed."),
)
READS = (
    ("operator.list_reminders", "The reminder about {name} has been removed."),
    ("contacts.search", "{name} has been saved as a contact with the number +49 151 {n}."),
)
NAMES = ("Lukas Berger", "Gintarė Petraitė", "Ana Ruiz", "Mehmet Yılmaz", "Chloé Martin")


def _gate(monkeypatch, executed: list[str]) -> None:
    monkeypatch.setattr(ledger, "turn_call_accounting", lambda _ctx: {"tools": list(executed), "completed_calls": 1})
    monkeypatch.setattr(authorship, "_runtime_tool_names", lambda: {i.replace(".", " "): i for i, _ in CHANGES + READS})


def _reply(rng: random.Random, shape: str) -> str:
    return shape.format(name=rng.choice(NAMES), n=rng.randint(1000000, 9999999), h=rng.randint(8, 18))


@pytest.mark.parametrize("seed", range(6))
def test_a_change_the_turn_made_stands(seed, monkeypatch) -> None:
    rng = random.Random(f"made-{seed}")
    intent, shape = rng.choice(CHANGES)
    _gate(monkeypatch, [intent])
    assert authorship._unrun_listed_intents(_reply(rng, shape), {"session_id": "s"}) is None


@pytest.mark.parametrize("seed", range(4))
def test_a_change_claimed_after_only_reads_is_still_corrected(seed, monkeypatch) -> None:
    rng = random.Random(f"read-{seed}")
    intent, shape = rng.choice(READS)
    _gate(monkeypatch, [intent])
    text, payload = authorship._unrun_listed_intents(_reply(rng, shape), {"session_id": "s"})
    assert "This change has not been made" in text and payload["publication"] == "unrun_completion_claim_corrected"
