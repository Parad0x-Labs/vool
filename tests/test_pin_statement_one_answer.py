"""A PIN typed as something to remember gets one answer, never "not saved" and "Locked in" together.

Seen on a v0.7.1 film build: "Remember that my bank card PIN is 4821." answered
"I didn't save your PIN. VOOL never keeps PINs, card numbers or passwords in memory, ..." and then
"Locked in. I'll remember that." in the same reply. The masked sentence ("... PIN is [redacted].")
still read as a statement to remember, so the memory lane stored the empty shell and said so.

The contract: the clause that only carried the secret is not remembered and not acknowledged. Any
other content in the same message is still kept, and the acknowledgement names what was kept.
Driven through VoolAgent.run_once, the same turn path the chat takes; every fact the memory lane
writes is recorded on its way to the real store.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core import persistent_memory
from core.agent_runtime.secret_intake import consume_secret_intake
from tests.test_memory_secret_filter_v071 import _chat_turn, harness  # fixture

NOT_SAVED = "I didn't save your PIN"


def _turn_and_writes(harness, text):
    written: list[str] = []
    real = persistent_memory.add_memory_fact

    def _recording(fact, *args, **kwargs):
        written.append(str(fact))
        return real(fact, *args, **kwargs)

    with mock.patch.object(persistent_memory, "add_memory_fact", side_effect=_recording):
        result, _seen, _stand_in = _chat_turn(harness, text)
    return str(result.get("response") or ""), written


def test_a_pin_to_remember_is_one_answer(harness):
    answer, written = _turn_and_writes(harness, "Remember that my bank card PIN is 4821.")
    assert NOT_SAVED in answer, answer
    assert "Locked in" not in answer and "remember that" not in answer.lower(), answer
    assert answer.count(NOT_SAVED) == 1, answer
    assert "4821" not in answer
    assert written == [], f"an empty shell of the PIN was remembered: {written}"


def test_other_content_is_kept_and_named(harness):
    answer, written = _turn_and_writes(
        harness, "Remember that my bank card PIN is 4821 and that my dentist is Dr Okafor."
    )
    assert NOT_SAVED in answer, answer
    assert 'Locked in. I’ll remember "my dentist is Dr Okafor".' in answer, answer
    assert answer.index(NOT_SAVED) < answer.index("Locked in")
    assert "4821" not in answer
    assert len(written) == 1 and "Okafor" in written[0], written
    assert not any("PIN" in fact or "[redacted" in fact for fact in written), written


def test_a_pin_then_remember_it_is_one_answer(harness):
    # Measured on the v0.7.1 Mac smoke: this answered "I didn't save your PIN ..." and then
    # 'Locked in. I'll remember "it".'
    answer, written = _turn_and_writes(harness, "My card PIN is 4821, please remember it.")
    assert NOT_SAVED in answer, answer
    assert answer.count(NOT_SAVED) == 1, answer
    assert "Locked in" not in answer and '"it"' not in answer, answer
    assert "4821" not in answer
    assert written == [], f"a back-reference to the PIN was remembered: {written}"


@pytest.mark.parametrize(
    ("said", "left"),
    [
        ("Remember that my bank card PIN is 4821.", ""),
        ("My PIN is 4821, also what is 2+2?", "what is 2+2?"),
        ("What is 2+2? My PIN is 4821.", "What is 2+2?"),
        ("Remember my card 4111 1111 1111 1111 is for Netflix, and that the dog eats at six.",
         "Remember the dog eats at six."),
        # A clause boundary is only where the secret's clause ends: "salt and pepper" stays whole.
        ("Remember that salt and pepper go in the left drawer and my PIN is 4821.",
         "Remember that salt and pepper go in the left drawer"),
        # A whole sentence that held the password goes; the next sentence stays.
        ("My email password is Qx7-plover-91. Also explain entropy briefly.", "Also explain entropy briefly."),
        # An ask that only points back at the secret goes with it: no "I'll remember "it"".
        ("My card PIN is 4821, please remember it.", ""),
        ("My PIN is 4821. Keep it in mind for me!", ""),
        ("My PIN is 4821, remember that the dog eats at six.", "remember that the dog eats at six."),
    ],
)
def test_only_the_secret_clause_leaves_the_turn(said, left):
    intake = consume_secret_intake(said, owner_local=True)
    assert intake is not None and NOT_SAVED.split(" your ")[0] in intake.reply
    assert intake.remainder == left
    assert intake.remainder_has_demand is bool(left)
