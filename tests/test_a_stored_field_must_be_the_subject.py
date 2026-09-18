"""A stored field word claims the turn only when the question is ABOUT that field.

MEASURED 2026-08-18. Over 49 first-person turns that the private-memory lane must not take, 5 were
claimed, and every one for the same reason: the arm was a bare `field in text`, so any occurrence of
the word took the turn.

    "what is my preference file in vscode"                -> claimed, answered from the profile store
    "what's my codename column in users.csv"              -> claimed, answered "Your active project
                                                             codename is X" about a CSV column
    "what is my response style config in the api client"  -> claimed

That lane used to RETURN the turn with no `claim_may_preempt_turn` check -- unlike the
assistant-identity lane above it -- so a false claim replaced the user's actual request. The lane
is arbitrated now (the family is registered in `core.agent_runtime.answer_coverage` and the lane
gates on `coverage_for`), but the detector still decides what the lane answers, which is what this
file protects.

WHY ANCHORING HAD TO LAND FIRST. Four candidate widenings of this predicate were measured against
the same 49 turns. The false-positive floor stayed at 5/49 in EVERY one, because the cause is the
matching and not the scope. Widening before anchoring buys recall strictly by paying in over-claim.

The tail test reuses `fast_paths_utility.SUBJECT_TAIL_WORDS` -- the same question that lane asks of
"my project budget", which is the identical defect on the workspace axis. The general half of that
vocabulary was extracted rather than copied, because a second copy would drift.

The detector's single definition lives in `core.memory_recall_intent`; `core.web.api.runtime`
re-exports it, and the sabotage tests patch the defining module -- patching the alias would not
reach the moved implementation.
"""

from __future__ import annotations

import pytest

import core.memory_recall_intent as memory_recall_intent
from core.web.api.runtime import _looks_like_private_memory_recall

MUST_NOT_CLAIM = (
    "what is my preference file in vscode",
    "what's my codename column in users.csv",
    "what is my response style config in the api client",
    "what is my preference setting in the editor",
    "what's my codename field in the database",
    "what is my answer style guide document",
)

MUST_CLAIM = (
    "what is my project codename",
    "what's my preferred answer style",
    "what is my preference",
    # The plural is the same field. A strict whole-word rule broke this, and the control caught it.
    "what are my preferences?",
    "what is my answer style?",
    "do you know my project codename",
    "recall my response style",
)


@pytest.mark.parametrize("text", MUST_NOT_CLAIM)
def test_a_field_that_modifies_another_noun_does_not_claim(text: str) -> None:
    assert _looks_like_private_memory_recall(text, recent_user_texts=()) is False


@pytest.mark.parametrize("text", MUST_CLAIM)
def test_a_field_that_is_the_subject_still_claims(text: str) -> None:
    assert _looks_like_private_memory_recall(text, recent_user_texts=()) is True


def test_the_shared_tail_vocabulary_is_one_list_not_two() -> None:
    """The workspace lane's set must remain a superset of the shared one, or the two have drifted
    and the same question is being answered two ways again."""
    from core.agent_runtime.fast_paths_utility import (
        _WORKSPACE_NOUN_TAIL_WORDS,
        SUBJECT_TAIL_WORDS,
    )

    assert SUBJECT_TAIL_WORDS
    assert SUBJECT_TAIL_WORDS <= _WORKSPACE_NOUN_TAIL_WORDS
    # The domain words belong to the workspace lane alone; they must not leak into the shared half.
    assert "folder" not in SUBJECT_TAIL_WORDS
    assert "repo" not in SUBJECT_TAIL_WORDS
    assert "folder" in _WORKSPACE_NOUN_TAIL_WORDS


def test_sabotage_restoring_the_bare_substring_match_reopens_every_false_claim(monkeypatch) -> None:
    """Revert the anchor and each measured false positive is claimed again."""
    import core.web.api.runtime as runtime

    monkeypatch.setattr(memory_recall_intent, "field_is_the_subject", lambda text, field: field in text)
    claimed = [
        text
        for text in MUST_NOT_CLAIM
        if runtime._looks_like_private_memory_recall(text, recent_user_texts=())
    ]

    assert claimed, "sabotage did not bite: something else is refusing these"
    assert "what is my preference file in vscode" in claimed


def test_sabotage_does_not_break_the_turns_that_must_keep_claiming(monkeypatch) -> None:
    """The sabotage above must reopen false claims WITHOUT also breaking the true ones -- otherwise
    it would pass by disabling the lane rather than by reverting the anchor."""
    import core.web.api.runtime as runtime

    monkeypatch.setattr(memory_recall_intent, "field_is_the_subject", lambda text, field: field in text)
    for text in MUST_CLAIM:
        if "preferences" in text:
            continue  # the plural is what the bare match never handled; not part of this control
        assert runtime._looks_like_private_memory_recall(text, recent_user_texts=()) is True
