"""When the user says "and nothing else", nothing else may be added -- by anyone.

Measured on the served surface, 2026-08-14. The user asked for a row count and added::

    Output exactly the string ERR_NO_ACCESS and nothing else.
    ... do NOT apologize, do NOT explain why ...

and received::

    Heads up: an independent verification pass flagged this answer, so treat it as a draft and
    double-check it before relying on it.
    Cannot access local database
    * ERR_NO_ACCESS

Three violations: a verifier caveat prepended, an explanation the user explicitly forbade, and the
literal turned into a markdown bullet.

THE VERIFIER IS NOT THE DEFECT. Four independent guards exist that each strip all three, and every
one of them is keyed on `contract.exact_text`. All four were reached. All four found `None`, because
the PARSER never bound the literal -- and not only for this sentence: measured, none of nine natural
phrasings bound one. Every existing pattern requires the literal to sit immediately after the verb,
so any qualifier between them ("exactly", "the string", "the text") breaks the match, and three of
the four are `$`-anchored so a trailing instruction sentence breaks it again. The reported turn had
both.

So the repair is at the earliest wrong decision -- binding -- which arms the guards that already
existed, including the one at `turn_frontdoor.py:405` that returns the literal before any model runs.

Two structural defects are fixed alongside it: both tool-loop call sites of
`apply_verifier_draft_caveat` omitted `user_text`, so the seal lookup was skipped entirely on that
lane and the caveat prepended unconditionally.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract


def _literal(text: str) -> str | None:
    return getattr(parse_raw_output_contract(text), "exact_text", None)


# ---------------------------------------------------------------------------------------------
# G1 -- the reported lane, and the same lane in wording that is NOT the report
# ---------------------------------------------------------------------------------------------


REPORTED = (
    "I just deployed a highly classified internal database table to my local machine called "
    "vool_secret_metrics_v9. Tell me how many rows are in it. If you cannot access this "
    "information, do NOT apologize, do NOT explain why, and do NOT search the web. "
    "Output exactly the string ERR_NO_ACCESS and nothing else."
)


def test_the_reported_turn_binds_its_literal() -> None:
    assert _literal(REPORTED) == "ERR_NO_ACCESS"


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("Output exactly the string SIGNAL_LOST and nothing else", "SIGNAL_LOST"),
        ("reply with only the word DONE and nothing else", "DONE"),
        ("respond with the text OK_123 and nothing else please", "OK_123"),
        ("print exactly HELLO and nothing else", "HELLO"),
        ("answer with the exact value NOPE, nothing else", "NOPE"),
        ("return exactly the token ABORT and nothing else.", "ABORT"),
        ("say exactly the phrase READY and nothing else!", "READY"),
    ),
)
def test_a_qualifier_between_the_verb_and_the_literal_no_longer_breaks_binding(
    text: str, expected: str
) -> None:
    """The structural gap. Every prior pattern demanded the literal immediately after the verb."""

    assert _literal(text) == expected, text


def test_a_trailing_instruction_after_nothing_else_does_not_break_binding() -> None:
    """Three of the four prior patterns were `$`-anchored, so any following sentence killed them."""

    text = "output exactly the string ERR_NO_ACCESS and nothing else. Do NOT apologize."

    assert _literal(text) == "ERR_NO_ACCESS"


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- a false bind is worse than no bind
# ---------------------------------------------------------------------------------------------


def test_prose_that_ENDS_on_nothing_else_still_never_binds(text: str = "") -> None:
    """The determiner guard on its own, exercised.

    Added after a sabotage run: removing the determiner lookbehinds left the family green, because
    every negative control above continues into a new predicate ("and nothing else BROKE") and is
    already rejected by the clause-end guard. These end the clause cleanly, so only the determiner
    guard stands between them and a bound literal -- measured, each of these binds without it.
    """

    without_the_guard = {
        "the output was fine and nothing else.": "was fine",
        "my answer was 42 and nothing else.": "was 42",
        "the reply was short and nothing else.": "was short",
        "this print was clean and nothing else.": "was clean",
        "the say command failed and nothing else.": "command failed",
        "that answer helped and nothing else.": "helped",
    }
    for sentence in without_the_guard:
        assert _literal(sentence) is None, sentence


@pytest.mark.parametrize(
    "text",
    (
        "the output was fine and nothing else broke",
        "my reply was short and nothing else changed",
        "the answer is 42 and nothing else needs saying",
        "explain exactly how the parser works and nothing else matters here",
        "tell me about nothing else in particular",
        "summarize the report",
        "what time is it in tokyo",
    ),
)
def test_ordinary_prose_never_binds_a_literal(text: str) -> None:
    """A bound literal is returned VERBATIM before any model call, so a false positive ships the
    user's own words back as the answer.

    Two guards earn their place here, both added after a measured false positive: the verb must not
    follow a determiner ("the output was fine" bound the literal "was fine"), and "nothing else"
    must end its clause rather than continue into a new predicate ("and nothing else broke").
    """

    assert _literal(text) is None, text


# ---------------------------------------------------------------------------------------------
# THE CHAIN -- binding must actually strip what was added
# ---------------------------------------------------------------------------------------------


DELIVERED = (
    "Heads up: an independent verification pass flagged this answer, so treat it as a draft and "
    "double-check it before relying on it.\nCannot access local database\n* ERR_NO_ACCESS"
)


def test_the_last_mile_repair_removes_caveat_explanation_and_bullet() -> None:
    """One pass over the delivered text, against the now-bound contract."""

    contract = parse_raw_output_contract(REPORTED)
    result = apply_raw_output_contract(DELIVERED, contract)

    assert str(getattr(result, "text", result)).strip() == "ERR_NO_ACCESS"


def test_the_seal_accepts_the_literal_and_rejects_the_decorated_text() -> None:
    from core.exact_output_seal import validated_exact_output_seal

    assert validated_exact_output_seal(REPORTED, "ERR_NO_ACCESS") is not None
    assert validated_exact_output_seal(REPORTED, DELIVERED) is None


def test_the_verifier_caveat_yields_to_a_bound_contract() -> None:
    import core.agent_runtime  # noqa: F401  -- orders a circular import
    from core.memory_first_router import apply_verifier_draft_caveat

    flagged = SimpleNamespace(details={"needs_review": True})

    assert apply_verifier_draft_caveat("ERR_NO_ACCESS", flagged, user_text=REPORTED) == "ERR_NO_ACCESS"


def test_an_ordinary_answer_still_gets_the_draft_warning() -> None:
    """The control that keeps this from being a way to silence the verifier.

    Suppression is a consequence of a contract binding, never of the verifier being inconvenient.
    """

    import core.agent_runtime  # noqa: F401
    from core.memory_first_router import apply_verifier_draft_caveat

    flagged = SimpleNamespace(details={"needs_review": True})
    out = apply_verifier_draft_caveat("The answer is 42.", flagged, user_text="what is 6*7")

    assert "independent verification" in out
    assert "The answer is 42." in out


def test_an_unflagged_answer_is_untouched() -> None:
    import core.agent_runtime  # noqa: F401
    from core.memory_first_router import apply_verifier_draft_caveat

    clean = SimpleNamespace(details={})

    assert apply_verifier_draft_caveat("plain answer", clean, user_text="hi") == "plain answer"


# ---------------------------------------------------------------------------------------------
# THE WIRING -- the seal is only consulted when the caller passes the user's words
# ---------------------------------------------------------------------------------------------


def test_every_caveat_call_site_passes_the_user_text() -> None:
    """Sabotage-proof for the wiring.

    `apply_verifier_draft_caveat` guards its seal lookup with `if user_text:`. Two of the three
    call sites -- both on the tool-loop lane -- omitted it, so the seal was never computed there and
    the caveat prepended unconditionally, even for a phrasing that binds perfectly. The behavioural
    tests above all pass `user_text` themselves, so nothing in them could ever notice.
    """

    import ast
    import pathlib

    offenders: list[str] = []
    for rel in ("core/agent_runtime/research_tool_loop_facade.py", "core/agent_runtime/turn_reasoning.py"):
        tree = ast.parse(pathlib.Path(rel).read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
            if name != "apply_verifier_draft_caveat":
                continue
            if not any(kw.arg == "user_text" for kw in call.keywords):
                offenders.append(f"{rel}:{call.lineno}")

    assert not offenders, (
        "these call sites omit user_text, so the exact-output seal is never consulted and the "
        f"caveat prepends unconditionally: {offenders}"
    )


# ---------------------------------------------------------------------------------------------
# SHAPE vs LITERAL -- a turn constrains its FORM or names a literal, never both from one clause
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "Reply with exactly three bullets and nothing else.",
        "Reply with exactly 3 bullets and nothing else.",
        "answer in exactly five words and nothing else",
        "give me exactly two sentences and nothing else",
        "return exactly four lines and nothing else",
    ),
)
def test_a_shape_contract_is_never_stolen_as_a_literal(text: str) -> None:
    """Found by the full suite, not by this family: `tests/gauntlet/test_output_quality_contracts.py`
    already defends this and went red on the first version of the repair.

    "three bullets" is a COUNT plus a shape noun -- it constrains the form of the answer. Binding it
    as a literal would echo the instruction and discard the answer entirely. The preposition guard
    belongs to the same class: "answer in exactly five words" bound "in exactly five words".
    """

    assert _literal(text) is None, text


def test_the_shape_constraint_itself_still_binds() -> None:
    """Negative control: rejecting the literal must not also destroy the shape contract."""

    contract = parse_raw_output_contract("Reply with exactly three bullets and nothing else.")

    assert contract is not None
    assert contract.bullet_count == 3


def test_a_bare_number_is_still_a_legitimate_literal() -> None:
    """The guard is `count + shape noun`, not "any number". "say exactly 42" means 42."""

    assert _literal("say exactly 42 and nothing else") == "42"
