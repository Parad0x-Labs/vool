"""An answer to a question that asked for no tool is never rewritten as "This change has not been made".

iMac gate on 3fe69f744 (a chat bound to a project folder): "why does seasoned firewood burn hotter?" was judged to ask
for no tool, a folder listing ran anyway, and the model's right answer "Seasoned firewood burns hotter because the
moisture has been removed through drying." was shown and stored as "This change has not been made: only a preview ran,
nothing was applied." The passive "has been removed" read as a claim that this turn changed something. Questions,
answers and tools are drawn at random; the correction after a real change ask stays.
"""
from __future__ import annotations

import random

import pytest

import core.final_answer_authorship as authorship
import core.plugin_ask as plugin_ask
import core.turn_model_call_ledger as ledger

QUESTIONS = (
    ("why does seasoned firewood burn hotter?",
     "Seasoned firewood burns hotter because the moisture has been removed through drying."),
    ("kodėl duona sensta?", "Bread goes stale because water has moved out of the starch and it has been changed by it."),
    ("how is decaf coffee made?", "Decaf is coffee whose caffeine has been removed, usually with water or CO2."),
    ("warum ist der Himmel blau?", "Blue light has been scattered more than red by the air, so the sky looks blue."),
)
READS = ("workspace.list_files", "machine.list_directory", "operator.list_reminders")


def _gate(monkeypatch, executed: list[str]) -> None:
    monkeypatch.setattr(ledger, "turn_call_accounting", lambda _ctx: {"tools": list(executed), "completed_calls": 1})
    monkeypatch.setattr(authorship, "_runtime_tool_names", lambda: {i.replace(".", " "): i for i in READS})


def _judged(monkeypatch, question: str, builtins: tuple[str, ...], plugins: tuple[str, ...] = ()) -> None:
    key = plugin_ask._key(question)
    monkeypatch.setattr(plugin_ask, "judged_at_all", lambda text: plugin_ask._key(text) == key)
    monkeypatch.setattr(plugin_ask, "judged_builtins", lambda text: builtins if plugin_ask._key(text) == key else ())
    monkeypatch.setattr(plugin_ask, "judgement", lambda text: plugins if plugin_ask._key(text) == key else None)


@pytest.mark.parametrize("seed", range(8))
def test_the_answer_to_a_question_judged_to_ask_for_no_tool_stands(seed, monkeypatch) -> None:
    rng = random.Random(f"plain-{seed}")
    question, answer = rng.choice(QUESTIONS)
    _gate(monkeypatch, [rng.choice(READS)])
    _judged(monkeypatch, question, ())

    assert authorship._unrun_listed_intents(answer, {"session_id": "s"}, request_text=question) is None


@pytest.mark.parametrize("case", ["a_tool_judged", "a_plugin_judged", "never_judged", "no_request_text"])
def test_a_change_claim_after_only_reads_is_still_corrected(case, monkeypatch) -> None:
    question = "remove the dentist reminder"
    _gate(monkeypatch, ["operator.list_reminders"])
    _judged(monkeypatch, question, ("reminders",) if case == "a_tool_judged" else (),
            ("vool-notes",) if case == "a_plugin_judged" else ())
    if case == "never_judged":
        monkeypatch.setattr(plugin_ask, "judged_at_all", lambda _text: False)
    asked = "" if case == "no_request_text" else question

    text, payload = authorship._unrun_listed_intents(
        "The dentist reminder has been removed.", {"session_id": "s"}, request_text=asked)

    assert "This change has not been made" in text and payload["publication"] == "unrun_completion_claim_corrected"
