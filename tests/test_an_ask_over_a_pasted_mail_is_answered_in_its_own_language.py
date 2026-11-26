"""An ask over a pasted mail is answered in the language the person wrote the ask in, named for the model.

Measured on the installed app (iMac gate 30 on 5a8cd0abf, qwen3:8b): a Swedish ask over a pasted English mail was
answered in English. Its few letters name no single language, so the reply line only said "the language of the
person's own words", and the model went with the mail. Named ("Respond in Spanish.") the same model answered in the
person's language (gate 26). The turn's judge names the language of the person's own lines, in any language.
All names and addresses are invented.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter
from core.response_language_policy import response_language_policy_for_text, response_language_provider_instruction

_MAIL = ("\n\nFrom: Lakeside Rowing Club <desk@example.org>\nSubject: Boat shed keys\n\n"
         "The boat shed gets new locks on Thursday. Please collect your new key from the front desk before the "
         "weekend; the old keys stop working on Friday morning.")


@pytest.fixture(autouse=True)
def _a_turn_with_no_local_judge(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda source_context: "")
    monkeypatch.setattr(intent_arbiter, "_turn_key", lambda source_context: "turn-ask-over-a-mail")
    intent_arbiter.bind_turn_judge({}, None)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _line(text: str, context: dict | None) -> str:
    return response_language_provider_instruction(
        response_language_policy_for_text(text).to_dict(), earlier_user_texts=[], latest_text=text,
        source_context=context)


@pytest.mark.parametrize(("ask", "code", "name"), [
    ("Mikä tässä viestissä on tärkeintä?", "fi", "Finnish"),
    ("Hva er poenget med denne?", "nb", "Norwegian"),
    ("Kan du kort förklara vad de vill?", "sv", "Swedish"),
])
def test_the_judged_language_of_the_ask_is_named_on_the_reply_line(ask, code, name):
    asked: list[str] = []
    intent_arbiter.bind_turn_judge({}, lambda prompt: asked.append(prompt) or '{"answer": "%s"}' % code)
    line = _line(ask + _MAIL, {"session_id": "chat-1"})
    assert line.startswith(f"Respond in {name}, ")
    # Only the person's own lines are put to the judge, never the mail.
    assert len(asked) == 1 and ask in asked[0] and "boat shed" not in asked[0].lower()


def test_with_no_judge_the_line_still_says_whose_language_counts():
    text = "Kan du kort förklara vad de vill?" + _MAIL
    line = _line(text, {"session_id": "chat-1"})
    assert "own words in front of the pasted text" in line and not line.startswith("Respond in Swedish")


def test_an_unknown_code_names_nothing():
    intent_arbiter.bind_turn_judge({}, lambda prompt: '{"answer": "xx"}')
    line = _line("Kan du kort förklara vad de vill?" + _MAIL, {"session_id": "chat-1"})
    assert line.startswith("Respond in the language of the person's own words")


def test_no_turn_context_asks_no_judge():
    intent_arbiter.bind_turn_judge({}, lambda prompt: pytest.fail("judged with no turn"))
    assert "own words in front of the pasted text" in _line("Kan du kort förklara vad de vill?" + _MAIL, None)


def test_the_reader_takes_only_a_listed_code():
    assert intent_arbiter._own_language_reader('{"answer": "sv"}') == "sv"
    assert intent_arbiter._own_language_reader('{"answer": "klingon"}') is None
