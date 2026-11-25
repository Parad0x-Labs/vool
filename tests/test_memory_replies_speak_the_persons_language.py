"""The memory lane's own replies are in the language the person wrote in.

Fresh-question audit of 02ff11496 (local qwen3:8b): "įsimink, kad mano mašina ..." and "Merk dir bitte: ..." were
answered "Locked in. I'll remember that.", "pamiršk viską apie mano mašiną" with "Forget applied. Removed 2 memory
entries", "kokia mano mašina?" with "I have no record of that; it was forgotten.", and a German recall came back under
"You told me:". Facts, leads and languages are drawn at random; the turn's judge is its record.
"""
from __future__ import annotations

import random

import pytest

import core.persistent_memory as pm
from core.agent_runtime.memory_runtime import maybe_handle_memory_fast_path
from core.i18n.lane_receipts import in_the_persons_language
from tests.test_memory_asked_in_any_language_v08 import (
    CHAT,
    OWNER,
    _fresh,
    _judged,
    home,
    isolated_home,
)
from tests.test_memory_forget_resurrection_p0 import _policy

__all__ = ["_fresh", "home", "isolated_home"]  # fixtures this module uses

KEEPS = {
    "lt": ("Įsimink, kad mano mašina yra {colour} Škoda ir stovi kieme prie tvoros", "Įsiminiau", ["žalia", "mėlyna"]),
    "de": ("Merk dir bitte, dass mein Fahrrad {colour} ist und im Keller hinter der Tür steht", "Gemerkt",
           ["grün", "blau"]),
    "pl": ("Zapamiętaj, że mój rower jest {colour} i stoi w piwnicy obok drzwi", "Zapisane", ["zielony", "niebieski"]),
}


class _Agent:
    def _fast_path_result(self, *, response, **_kwargs):
        return {"response": response}

    def _maybe_handle_companion_memory_fast_path(self, *_a, **_k):
        return None


def _turn(text):
    return maybe_handle_memory_fast_path(_Agent(), text, session_id=CHAT, source_context=dict(OWNER),
                                         access_policy=_policy(CHAT),
                                         maybe_handle_memory_command_fn=pm.maybe_handle_memory_command)


@pytest.mark.parametrize("seed", range(6))
def test_a_keep_is_answered_in_the_language_it_was_asked_in(home, seed):
    rng = random.Random(f"keep-words-{seed}")
    lang = rng.choice(sorted(KEEPS))
    shape, receipt, colours = KEEPS[lang]
    text = shape.format(colour=rng.choice(colours)) + rng.choice([".", "", "!"])
    _judged(text, "keep")
    result = _turn(text)
    assert result is not None
    reply = str(result["response"])
    assert receipt in reply and "Locked in" not in reply, reply


def test_an_english_keep_keeps_its_english_receipt(home):
    text = "Keep in mind that my kayak is yellow and lives in the garage."
    _judged(text, "keep")
    result = _turn(text)
    assert result is not None and str(result["response"]) == pm.KEPT_REPLY


@pytest.mark.parametrize("seed", range(4))
def test_fixed_lines_follow_the_message(seed):
    rng = random.Random(f"lines-{seed}")
    asked, words = rng.choice([
        ("kokia mano mašina? pasakyk, ką žinai", ("Apie tai neturiu įrašo", "Pamiršta.", "Jūs man sakėte:")),
        ("Welche Nummer hat Jörg? Sag mir bitte, was du weißt", ("keinen Eintrag", "Vergessen.", "Du hast mir gesagt:")),
    ])
    count = rng.randint(1, 5)
    assert words[0] in in_the_persons_language(pm.JUDGED_RECALL_FORGOTTEN, asked)
    applied = in_the_persons_language(f"Forget applied. Removed {count} memory entr{'y' if count == 1 else 'ies'}.", asked)
    assert applied.startswith(words[1]) and str(count) in applied
    fact = rng.choice(["Mein Zahnarzt heißt Dr. Albrecht", "mano mašina žalia"])
    told = in_the_persons_language(f"You told me: {fact}", asked)
    assert told.startswith(words[2]) and told.endswith(fact)
    # Anything else is left as it was.
    assert in_the_persons_language("Here is a poem.", asked) == "Here is a poem."


@pytest.mark.parametrize("seed", range(6))
def test_one_fact_is_shown_once_in_a_recall(seed, monkeypatch):
    """02ff11496 audit: "what's the code for my locker at the gym?" listed the saved sentence and two learned lines
    that say the same thing. Lines that say a different number are both shown: they disagree."""
    rng = random.Random(f"once-{seed}")
    code = str(rng.randint(1000, 9999))
    said = rng.choice([f"my gym locker code is {code}", f"mano sporto klubo spintelės kodas {code}"])
    copies = [said, said.replace("my ", "").replace("mano ", "").capitalize(), said.replace(" is ", ": ")]
    other = rng.choice(["My bike is blue", "Mano dviratis mėlynas"])
    wrong = said.replace(code, str(int(code) + 1))
    held = [*copies, other, wrong]
    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(pm, "_held_for_judgement", lambda **_k: list(held))
    monkeypatch.setattr(pm, "_judged_items_named", lambda _q, items, **_k: [i for i in items if i != other])
    reply = pm._judged_recall_reply("locker code?", session_id="once", policy=None, source_context={},
                                    forgotten_too=False)
    assert reply is not None
    assert sum(code in line for line in reply.splitlines()) == 1, reply
    assert wrong.split()[-1] in reply


@pytest.mark.parametrize("seed", range(4))
def test_an_ask_for_everything_remembered_lists_what_is_held(seed, monkeypatch):
    """02ff11496 audit: "what are all the things you remember about me?" with 40 facts held got "I don't have that
    saved". The recall judge names none of them; the whole-memory question says the message asks for all."""
    import core.intent_arbiter as intent_arbiter

    rng = random.Random(f"whole-{seed}")
    held = rng.sample(["my gym locker code is 4471", "I am allergic to walnuts", "Mein Zahnarzt heißt Dr. Albrecht",
                       "mano mašina žalia", "my landlord is Mr. Okafor"], 4)
    asked = rng.choice(["what are all the things you remember about me?", "ką apie mane atsimeni?",
                        "Was weißt du alles über mich?"])
    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(pm, "_held_for_judgement", lambda **_k: list(held))
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [])
    monkeypatch.setattr(intent_arbiter, "arbitrate_whole_memory_ask", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_particular_ask", lambda *_a, **_k: "no")
    reply = pm._judged_recall_reply(asked, session_id="whole", policy=None, source_context={}, forgotten_too=False)
    assert reply is not None and all(fact in reply for fact in held), reply
    monkeypatch.setattr(intent_arbiter, "arbitrate_whole_memory_ask", lambda *_a, **_k: "no")
    assert pm._judged_recall_reply(asked, session_id="whole", policy=None, source_context={},
                                   forgotten_too=False) is None
