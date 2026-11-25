"""A change call filled from an earlier message is handed back even when that message's own call failed.

iMac gate on b8d1c324a (qwen3:8b, from source): "primink šeštadienį 11 val. nupirkti gimtadienio tortą" went out as a
cancel that failed, and the next "primink sekmadienį 15 val. palaistyti pomidorus" went out as a set of the cake, with
the earlier message as its text and note and a wrong day; after Allow the cake was stored and the tomatoes never were.
Only calls that ran were compared, and the earlier text passed because both asks open with "primink ... val.".
Messages are drawn at random; no model and no network.
"""
from __future__ import annotations

import random

import pytest

PAIRS = (
    ("primink šeštadienį 11 val. nupirkti gimtadienio tortą", "nupirkti gimtadienio tortą",
     "primink sekmadienį 15 val. palaistyti pomidorus", "palaistyti pomidorus"),
    ("erinnere mich am Freitag um acht an den Zahnarzttermin", "den Zahnarzttermin bestätigen",
     "erinnere mich am Sonntag um zehn an die Gießkanne", "die Gießkanne füllen"),
    ("remind me on monday at 9 to book the car service", "book the car service",
     "remind me on tuesday at 6 to call the landlord", "call the landlord"),
)


@pytest.fixture(autouse=True)
def _fresh_ledger():
    from core.turn_model_call_ledger import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


def _guard(arguments: dict, question: str, history: list[dict]):
    from core.agent_runtime.research_tool_loop_facade import _change_call_carries_an_earlier_message

    context = {"session_id": "chat-1", "request_id": "req-now", "conversation_history": history}
    return _change_call_carries_an_earlier_message("operator.schedule_reminder",
                                                   {"intent": "operator.schedule_reminder", "arguments": arguments},
                                                   question, [], context)


@pytest.mark.parametrize("seed", range(6))
def test_the_earlier_asks_note_or_text_is_handed_back(seed) -> None:
    rng = random.Random(f"failed-earlier-{seed}")
    earlier, earlier_note, now, _note = rng.choice(PAIRS)
    history = [{"role": "user", "content": earlier}, {"role": "assistant", "content": "I could not find that."}]
    arguments = rng.choice(({"text": earlier, "note": earlier_note.split(" ", 1)[-1] if seed % 2 else earlier_note,
                             "due_iso": "2026-11-26T11:00"},
                            {"text": earlier, "note": _note, "due_iso": "2026-11-29T15:00"},
                            {"note": " ".join(earlier.split()[-3:]), "due_iso": "2026-11-26T11:00"}))

    refused = _guard(arguments, now, history)

    assert refused is not None and refused.status == "invalid_arguments", (arguments, now)
    assert now in refused.response_text and refused.details["executed"] is False


@pytest.mark.parametrize("seed", range(6))
def test_this_messages_own_call_runs(seed) -> None:
    rng = random.Random(f"failed-own-{seed}")
    earlier, _earlier_note, now, note = rng.choice(PAIRS)
    history = [{"role": "user", "content": earlier}, {"role": "assistant", "content": "I could not find that."}]

    assert _guard({"text": now, "note": note, "due_iso": "2026-11-29T15:00"}, now, history) is None
