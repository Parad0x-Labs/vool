"""A tool-loop answer is judged only against what a tool returned, and a refused one is said in plain words.

Mac memory run12 (2026-11-23, qwen3:8b, from source on the release line): with "prisimink, kad kaimyno šuo ėda vien
obuolius" saved, "ką mėgsta ėsti kaimyno šuo?" was judged to need no tool, yet the model searched the folders for
"obuolius"; the search failed ("None of the searchable local folders are readable right now."), the model's answer
from what it was given was refused for sharing no word with that failure, and the person was shown
"Incomplete — model synthesis failed (ungrounded). The grounded work collected so far is below; this is not a
completed audit." over "0 of 1 tool steps succeeded. Last result: ...". Every word of that is the runtime talking
to itself.

A step that failed returned nothing an answer could have used, so it is no evidence an answer ignored; an answer
that ignored what a tool did return is still refused, and what replaces it is said in the person's words. The
answers, results and failures are drawn at random from several languages and wordings.
"""
from __future__ import annotations

import random

import pytest

from core.agent_runtime.research_tool_loop_facade import (
    synthesis_grounding_observations,
    unwritten_answer_in_plain_words,
)
from core.model_output_guard import is_ungrounded

#: Answers a model writes from what it was given (a saved fact), sharing no five-letter word with any tool line below.
ANSWERS = (
    "Pagal tai, ką man sakėte, kaimyno šuo ėda vien obuolius ir nieko daugiau.",
    "Soweit bekannt bevorzugt das Tier hauptsächlich Äpfel, manchmal Karotten, niemals Fleischreste.",
    "Según recuerdo, prefiere manzanas frescas y zanahorias crujientes durante cualquier temporada.",
    "From what you told me, the neighbour's terrier only ever wants apples, never biscuits.",
)

#: A folder search that failed, in its own words.
FAILED = (
    "None of the searchable local folders are readable right now.",
    "The search could not start: no folder was chosen.",
    "That folder could not be read right now.",
)

#: A folder search that worked, in its own words.
FOUND = (
    "Found 2 matching files: zzqx-1.txt, zzqx-2.txt",
    "Found 1 matching file: zzqx-notes.md",
    "Files under zzqx: example.py",
)

_BANNER_WORDS = ("incomplete", "synthesis failed", "not a completed audit", "tool steps succeeded", "grounded work")


def _step(ok: bool, said: str, tool: str = "machine.find_file") -> dict:
    return {
        "tool_name": tool, "ok": ok, "status": "executed" if ok else "unavailable", "summary": said,
        "response_text": said,
        "observation": {"schema": "tool_observation_v1", "intent": tool, "ok": ok, "status": "executed" if ok else "unavailable"},
    }


@pytest.mark.parametrize("seed", range(8))
def test_an_answer_after_tools_that_all_failed_is_not_refused_for_ignoring_the_failures(seed) -> None:
    rng = random.Random(f"failed-only-{seed}")
    steps = [_step(False, rng.choice(FAILED)) for _ in range(rng.randint(1, 3))]

    assert not is_ungrounded(rng.choice(ANSWERS), synthesis_grounding_observations(steps))


@pytest.mark.parametrize("seed", range(8))
def test_an_answer_that_ignored_what_a_tool_returned_is_still_refused(seed) -> None:
    rng = random.Random(f"found-ignored-{seed}")
    steps = [_step(False, rng.choice(FAILED)), _step(True, rng.choice(FOUND))]
    rng.shuffle(steps)

    assert is_ungrounded(rng.choice(ANSWERS), synthesis_grounding_observations(steps))


@pytest.mark.parametrize("seed", range(8))
def test_a_refused_answer_is_replaced_in_plain_words(seed) -> None:
    rng = random.Random(f"plain-{seed}")
    found, failed = rng.choice(FOUND), rng.choice(FAILED)
    worked = [_step(False, failed), _step(True, found)]
    nothing = [_step(False, failed)]
    count = f"{rng.randint(0, 2)} of {rng.randint(1, 3)} tool steps succeeded. Last result: {failed}"

    with_result = unwritten_answer_in_plain_words(worked, count)
    without = unwritten_answer_in_plain_words(nothing, count)

    assert found in with_result and failed not in with_result, with_result
    assert failed in without, without
    for reply in (with_result, without, unwritten_answer_in_plain_words([], "")):
        assert reply.strip() and not any(word in reply.lower() for word in _BANNER_WORDS), reply


def test_what_runtime_code_already_composed_from_the_steps_stays_the_answer() -> None:
    composed = "I could not get a reading of your database for that, so I have no answer to give. Nothing was changed."

    assert unwritten_answer_in_plain_words([_step(True, "COUNT(*): 1", "vool-database.query")], composed) == composed
