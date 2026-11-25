"""A forget answers once, and in the language the person wrote in.

Measured on the installed app (Mac gate 15 on 06a3f89fc): a Lithuanian forget erased the fact and was answered in
English with two opposite outcomes, "Forget applied. Removed 1 memory entry. ..." and then "Nothing was removed ..."
for the person's own earlier sentence that held the same fact. The judge had named both the saved row and the
sentence; the second found nothing because the first had erased it. The reply was English because the forget list's
note behind the outcome made the line unknown to the localizer.

All names and facts here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core.i18n.lane_receipts import in_the_persons_language
from core.memory.copies import FORGET_LIST_NOTE
from core.persistent_memory import _FORGOTTEN_WORDS, _combined_forget_result


def _result(status: str, words: str, removed: int = 0) -> tuple[str, str, dict]:
    reply = {
        "applied": f"Forget applied. Removed {removed} memory entr{'y' if removed == 1 else 'ies'}. {FORGET_LIST_NOTE}",
        "no_match": f'Nothing was removed. Forget found no memory in this chat that matched "{words}". {FORGET_LIST_NOTE}',
    }[status]
    return reply, status, {_FORGOTTEN_WORDS: words, "stores": {}}


def test_the_same_thing_named_twice_is_one_outcome():
    reply, status, _ = _combined_forget_result([
        _result("applied", "my neighbour Odile has a tortoise called Bramble", 1),
        _result("no_match", "Please keep in mind: my neighbour Odile has a tortoise called Bramble"),
    ])
    assert status == "applied"
    assert reply.startswith("Forget applied. Removed 1 memory entry.")
    assert "Nothing was removed" not in reply


def test_a_second_thing_that_was_never_held_is_still_said():
    reply, status, _ = _combined_forget_result([
        _result("applied", "my neighbour Odile has a tortoise called Bramble", 1),
        _result("no_match", "the code of the bike shed"),
    ])
    assert "Removed 1 memory entry" in reply and 'matched "the code of the bike shed"' in reply


@pytest.mark.parametrize(("said", "head", "note_word"), [
    ("Ačiū. Dabar pamiršk, ką sakiau apie kaimynės vėžlį.", "Pamiršta. Pašalinta atminties įrašų: 1.", "pamiršimų"),
    ("Danke. Vergiss bitte, was ich über Odiles Schildkröte gesagt habe.", "Vergessen.", "Vergessen-Liste"),
    ("Dzięki. Zapomnij, co mówiłem o żółwiu sąsiadki.", "1", "zapomnianych"),
])
def test_the_outcome_and_the_note_are_in_the_persons_language(said, head, note_word):
    english = f"Forget applied. Removed 1 memory entry. {FORGET_LIST_NOTE}"
    shown = in_the_persons_language(english, said)
    assert head in shown and note_word in shown
    assert "Forget applied" not in shown and "forget list" not in shown


def test_a_no_match_names_the_target_in_the_persons_language():
    english = f'Nothing was removed. Forget found no memory in this chat that matched "Dr. Vale". {FORGET_LIST_NOTE}'
    shown = in_the_persons_language(english, "Pamiršk, ką sakiau apie gydytoją Vale.")
    assert "Niekas nepašalinta" in shown and "„Dr. Vale“" in shown and "Nothing was removed" not in shown


def test_english_stays_english():
    english = f"Forget applied. Removed 2 memory entries. {FORGET_LIST_NOTE}"
    assert in_the_persons_language(english, "please forget the tortoise thing") == english


def test_the_final_check_keeps_the_verified_outcome_in_the_persons_language(monkeypatch):
    """The final check puts the executed outcome back over any other reply; it puts it back in the person's language
    (Mac gate 16 on 0680a8678: a Polish forget was rendered in Polish and then put back into English there)."""
    from types import SimpleNamespace

    import core.memory.command_result as command_result
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    executed = f"Forget applied. Removed 1 memory entry. {FORGET_LIST_NOTE}"
    execution = SimpleNamespace(response=executed, outcome=executed, status="applied")
    monkeypatch.setattr(command_result, "verified_forget_outcome", lambda *a, **k: execution)
    order = "Dzięki. A teraz zapomnij proszę, co mówiłem o żółwiu sąsiadki."
    shown = in_the_persons_language(executed, order)
    assert shown.startswith("Zapomniane")
    final = enforce_final_action_honesty({"response": shown}, user_input=order, session_id="pl-chat")
    assert final["response"] == shown and "action_honesty_validator" not in final
    replaced = enforce_final_action_honesty({"response": "Usunąłem wszystko."}, user_input=order, session_id="pl-chat")
    assert replaced["response"] == shown  # a reply that is not the outcome is still replaced by it
    english = enforce_final_action_honesty({"response": "done"}, user_input="forget the tortoise", session_id="en")
    assert english["response"] == executed
