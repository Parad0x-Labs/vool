"""The user must never be shown the prompt's own scaffolding as an answer.

Asked "run the command: ls -la", the product replied with the literal words **final grounded reply** --
the example value from the tool-catalog instruction in `prompt_normalizer`, copied verbatim by a small
local model instead of being filled in. Two changes: the example is now an angle-bracket slot that
cannot pass for prose, and `tool_intent_direct_message` refuses to hand a placeholder back as a reply.

Returning None is deliberate: it is the same path an empty message already took, so the caller keeps
working the turn rather than printing scaffolding.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.response_policy_classification import tool_intent_direct_message


def _direct(message: str):
    return tool_intent_direct_message({"intent": "respond.direct", "arguments": {"message": message}})


@pytest.mark.parametrize(
    "echoed",
    [
        "final grounded reply",  # the exact string seen live
        "Final Grounded Reply.",
        "FINAL GROUNDED REPLY",
        '"final grounded reply"',
        "<your reply to the user, written out in full>",
        "<message>",
        "your answer here",
        "reply",
        "",
        "   ",
    ],
)
def test_placeholder_is_never_returned_as_a_reply(echoed: str) -> None:
    assert _direct(echoed) is None


@pytest.mark.parametrize(
    "real",
    [
        "Today is Tuesday, 2026-07-28.",
        "The final answer is 42.",
        "I ran it; here is the final reply from the server: 200 OK",
        "Your answer depends on which branch you are on.",
        "final grounded reply is the placeholder that used to leak — here is the real total: 17 files.",
    ],
)
def test_a_real_answer_is_passed_through(real: str) -> None:
    # The guard matches the WHOLE normalised message. An answer that merely contains one of these
    # phrases is a legitimate reply and must survive.
    assert _direct(real) == real


def test_guard_only_applies_to_direct_response_intents() -> None:
    assert tool_intent_direct_message({"intent": "workspace.read_file", "arguments": {"message": "x"}}) is None
    assert tool_intent_direct_message({"intent": "none", "arguments": {"message": "done"}}) == "done"


def test_the_catalog_prompt_no_longer_offers_a_copyable_sentence() -> None:
    # The second half of the fix: if the instruction goes back to a plausible sentence, a model will
    # copy it again and the guard is the only thing standing between that and the user.
    from pathlib import Path

    source = Path("core/prompt_normalizer.py").read_text(encoding="utf-8")
    catalog_line = next(
        line for line in source.splitlines() if '"intent":"respond.direct"' in line and '"message"' in line
    )
    assert '"message":"<' in catalog_line, "the example value must be an angle-bracket slot, not prose"
