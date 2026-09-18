"""Guard: smalltalk must not cost an 8B model call.

"hows going?" was answered by qwen3:8b at ~1,434 tokens to say "good, you?". The matcher knew four
phrasings ("how are you/ya/u", "how r u", "you alive"); every other natural form fell through to the
local model. This is a measured gate -- if the hit rate drops, the regression is visible as a number.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.fast_paths_utility import _GREETING_PHRASES, _STATUS_CHECK_RE

SMALLTALK = [
    "hows going?", "how are you?", "how is it going", "hows it going", "how's it going",
    "how you doing", "how are u", "you alive", "how have you been", "hows things",
    "whats good", "everything ok?", "you ok?", "how is your day", "how r u rn",
]

# Real work that merely starts with "how" must still reach the model.
REAL_QUESTIONS = [
    "how do i install python",
    "how does this code work",
    "how are the tests doing on main",
    "how big is this file",
    "what is going on with the build",
    "how should i structure this module",
]


def _hits_fast_path(text: str) -> bool:
    lowered = text.lower()
    return bool(_STATUS_CHECK_RE.search(lowered)) or lowered.strip("?!. ") in _GREETING_PHRASES


@pytest.mark.parametrize("text", SMALLTALK)
def test_smalltalk_never_reaches_the_model(text):
    assert _hits_fast_path(text), f"{text!r} would cost a full local-model call"


@pytest.mark.parametrize("text", REAL_QUESTIONS)
def test_real_questions_are_not_swallowed_by_the_greeting_path(text):
    assert not _hits_fast_path(text), f"{text!r} was mistaken for smalltalk"


def test_measured_hit_rate_gate():
    """A number, not a judgement call -- the thing that makes coverage rot visible."""
    hits = sum(1 for t in SMALLTALK if _hits_fast_path(t))
    assert hits == len(SMALLTALK), f"smalltalk fast-path reach fell to {hits}/{len(SMALLTALK)}"
    false_positives = sum(1 for t in REAL_QUESTIONS if _hits_fast_path(t))
    assert false_positives == 0, f"{false_positives} real questions swallowed by the greeting path"
