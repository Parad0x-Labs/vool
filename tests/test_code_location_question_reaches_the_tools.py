"""Asking where a symbol lives must not be answered from model memory.

Reproduced live 2026-07-31 against the running daemon:

    "which file defines handle_inspect_processes"
    -> "The function `handle_inspect_processes` is defined in the file `workspace/process_inspector.py`."
       local | qwen3:8b | 601 tok

`workspace/process_inspector.py` exists nowhere in the tree. The real definition is at
`core/operator/handlers.py:70`. turn_trace for that session reported "no ledger events" and
"TOOL RECEIPTS (0)": no tool ran, so an 8b model answered a question about the user's own repo out of
its weights, in a confident voice, with a plausible path.

The cause is `explicit_runtime_workflow_request`, whose code-search gate requires a VERB from
{find, inspect, trace, locate, search, read, open} AND a container noun. "which file defines X"
supplies `file` and no verb, so the gate said no, `should_keep_ai_first_chat_lane` returned True, and
`research_tool_loop_facade` skipped the tool loop entirely. Measured, one word of phrasing decided it:

    which file defines handle_inspect_processes                      -> tool loop SKIPPED
    inspect the code and tell me which file defines handle_...       -> tool loop runs, correct answer

This is the recurring defect in this codebase -- a vocabulary list deciding a whole turn -- so the fix
matches the FRAME the sentence uses, not the words someone happened to type: an interrogative head, a
code-container noun, and a definition/containment relation to a named symbol.

Leaving the chat lane does not answer anything. It declines to SKIP the tool loop, so the model is
offered the tool catalogue and the loop's own containment still applies. That asymmetry is why the
positives below are worth a small false-positive risk and the negatives are still asserted.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.runtime_checkpoint_lane_policy import (
    _asks_where_code_lives,
    should_keep_ai_first_chat_lane,
)


class _Agent:
    def _is_chat_truth_surface(self, source_context) -> bool:
        return True

    def _looks_like_explicit_resume_request(self, user_input) -> bool:
        return False

    def __getattr__(self, name):
        return lambda *args, **kwargs: False


def _tool_loop_runs(text: str) -> bool:
    """True when the turn LEAVES the ai-first chat lane, i.e. the tool loop is allowed to run."""
    return should_keep_ai_first_chat_lane(
        _Agent(),
        user_input=text,
        classification={"task_class": "chat_conversation"},
        interpretation=None,
        source_context={},
        checkpoint_state=None,
    ) is False


# The reproduced failure, plus the phrasings a person actually uses for the same question. Every one
# of these was measured SKIPPING the tool loop before the fix.
@pytest.mark.parametrize(
    "text",
    [
        "which file defines handle_inspect_processes",
        "what file holds handle_inspect_processes",
        "in which file is handle_inspect_processes defined",
        "what file is handle_inspect_processes in",
        "which module contains the class PolicyEngine",
        "which module declares PolicyEngine",
        "which script implements retry_with_backoff",
        "what class implements RetryPolicy",
        "where is handle_inspect_processes defined",
        "where does handle_inspect_processes live",
        "where does PolicyEngine come from",
        "where does chunkify live in the repo",
        "point me at the file that defines chunkify",
        "show me the file that defines handle_inspect_processes",
    ],
)
def test_a_question_about_where_code_lives_reaches_the_tool_loop(text: str) -> None:
    assert _tool_loop_runs(text), text


# A question that cannot be answered by reading the repo must stay in the chat lane. "where does X
# live" is the sharp edge: it reads naturally about people and places too.
@pytest.mark.parametrize(
    "text",
    [
        "how should I position my B2B analytics product",
        "what defines a good API",
        "what defines success for this team",
        "what makes a good manager",
        "where is Paris",
        "where is the nearest coffee shop",
        "locate Ulaanbaatar",
        "where does my sister live",
        "where does he live",
        "tell me a joke",
    ],
)
def test_a_question_the_repo_cannot_answer_stays_in_the_chat_lane(text: str) -> None:
    assert not _asks_where_code_lives(text), text


def test_the_symbol_is_captured_not_guessed_by_position() -> None:
    """The relation trails the symbol in one frame and leads it in another.

    An earlier cut of this fix took "the last identifier in the matched span" as the symbol. That is
    correct for "which file defines X" and wrong for "where is X defined", where the last token is
    the word `defined` -- which sits in the not-a-symbol list, so the whole frame was rejected and the
    turn fell back to the model. Both orders must resolve to the symbol itself.
    """
    assert _asks_where_code_lives("which file defines handle_inspect_processes")
    assert _asks_where_code_lives("where is handle_inspect_processes defined")


def test_a_determiner_or_abstract_noun_in_the_symbol_slot_is_not_a_lookup() -> None:
    """"what defines a good API" names no symbol to find; nothing should go grep for one."""
    assert not _asks_where_code_lives("what defines a good API")
    assert not _asks_where_code_lives("which file defines the")


def test_a_bare_where_does_x_live_needs_code_shape_or_a_code_noun() -> None:
    """The boundary that keeps "where does my sister live" out, stated as an assertion.

    With no code-container noun in the sentence, only an identifier-SHAPED symbol (snake_case or an
    internal capital) makes it a code question. Name a container and the shape stops mattering.
    """
    assert _asks_where_code_lives("where does handle_inspect_processes live")   # snake_case
    assert _asks_where_code_lives("where does PolicyEngine come from")          # internal capital
    assert _asks_where_code_lives("where does chunkify live in the repo")       # container noun
    assert not _asks_where_code_lives("where does chunkify live")               # neither
    assert not _asks_where_code_lives("where does my sister live")
