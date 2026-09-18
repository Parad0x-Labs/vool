"""A letter named in prose is not this user's social profile.

Measured live 2026-08-15 16:13 EEST (run 3, session 8692259e...): "String X is 'VOOL'. String Y is
'AI'. Concatenate them to 'VOOLAI'. Replace 'O' with '0'. Reverse the string. Output ONLY the final
string." -- a pure string-manipulation turn, correct answer IAL00V -- was answered "You need a
VoolBook profile first." The `has_twitter` arm in classify_voolbook_intent matched the bare
`\\bx\\b\\s*is` in "String X is", read it as a Twitter/X-handle declaration, and the twitter flow
gated on a profile. The module's own closing comment cited "the bare-`x` arm in `has_twitter`" as
already fixed -- a stale completeness claim; the declarative alternative still had `my` optional.

The rule: only the possessive ("my x is @foo") or the word "handle" ("twitter handle: @foo") makes
a bare platform word a profile declaration.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.voolbook import classify_voolbook_intent


@pytest.mark.parametrize(
    "turn",
    (
        # The measured reproduction and its family: standalone letters in ordinary prose.
        "string x is 'vool'. string y is 'ai'. concatenate them. output only the final string.",
        "variable x is 12 and y is 30, what is x times y",
        "in this equation x is the unknown, solve for x",
        "the answer to question 3 x is: false",
        "column x is empty in the csv, why",
    ),
)
def test_a_prose_letter_x_never_becomes_a_twitter_intent(turn: str) -> None:
    assert classify_voolbook_intent(turn) != "twitter", turn


@pytest.mark.parametrize(
    "turn",
    (
        # Negative controls: real profile declarations keep working.
        "my x is @sls0x",
        "my twitter is @parad0x",
        "my x handle is @sls0x",
        "twitter handle: @parad0x",
        "set my twitter handle to @sls0x",
        "update my x to @newname",
    ),
)
def test_real_handle_declarations_still_classify(turn: str) -> None:
    assert classify_voolbook_intent(turn) == "twitter", turn


def test_bio_compound_still_pairs_with_a_real_twitter_declaration() -> None:
    assert (
        classify_voolbook_intent("set my bio to builder and my x is @sls0x")
        == "compound_bio_twitter"
    )
