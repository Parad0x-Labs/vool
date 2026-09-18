"""Plain arithmetic is computed, never guessed.

Found by the seeded hostile driver against the live app (seed 4242, 2026-08-15) -- a phrasing no
test contained:

    U: calc 80*81 for me
    A: 648

80 x 81 is 6480. A digit was dropped, and the wrong number was presented with no hedge. Re-driving
the identical prompt returned 6480, so the answer was not reproducibly wrong -- it was
NON-DETERMINISTIC, which is worse: the same question can be right or wrong per turn, and nothing in
the reply distinguishes the two.

The runtime already owned a deterministic calculator and had done the whole time. It simply was not
reached: `looks_like_direct_math_request` recognised "what is 80*81" and "80*81" but not "calc
80*81 for me", so that phrasing fell through to a local model. This is not a model-capability
finding. A weaker model is allowed to write worse prose; the product is not allowed to hand plain
arithmetic to a stochastic lane when it can compute it exactly.

The repair widens the filler the recognizer already strips -- a leading calculation verb, chained
trailing politeness ("for me thx"), and the trailing "= ?" people use to ask for a result -- rather
than adding a second mechanism beside it.

Negative controls carry the real risk here. "calculate my mortgage" and "calc the risk for me" are
requests to REASON, not expressions to evaluate; firing the calculator on them would turn a
thoughtful answer into a parse failure.
"""

from __future__ import annotations

import pytest

from core.task_router import evaluate_direct_math_request, looks_like_direct_math_request


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        # The measured miss, verbatim.
        ("calc 80*81 for me", "6480"),
        # Chained trailing filler -- one pass left "for me" behind and the parse failed.
        ("calc 35*18 for me thx", "630"),
        # "= ?" is how the result is ASKED for, not part of the expression.
        ("80 times 81 = ?", "6480"),
        ("144 / 12 =", "12"),
        # Other calculation verbs, other operators, other spellings of multiply.
        ("compute 12*12 please", "144"),
        ("work out 45+55", "100"),
        ("calculate 7 x 8 for me", "56"),
        ("solve 100/4 thx", "25"),
        ("figure out 19*3", "57"),
        ("evaluate 250 - 175", "75"),
    ),
)
def test_arithmetic_is_computed_deterministically(text: str, expected: str) -> None:
    answer = evaluate_direct_math_request(text)

    assert answer is not None, f"fell through to a model: {text!r}"
    assert expected in answer, f"{text!r} -> {answer!r}"


def test_the_phrasings_that_already_worked_still_work() -> None:
    """The recognizer was widened, never rerouted."""

    for text, expected in (("what is 80*81", "6480"), ("whats 91*69", "6279"), ("80*81", "6480")):
        answer = evaluate_direct_math_request(text)
        assert answer is not None and expected in answer, text


@pytest.mark.parametrize(
    "text",
    (
        # Reasoning requests that merely start with a calculation verb.
        "calculate my mortgage",
        "calc the risk for me",
        "compute the best route to the airport",
        "work out what went wrong with the deploy",
        # No two numbers to combine.
        "x times y",
        "how many minutes is 5h",
        # Not arithmetic at all.
        "what is the capital of france",
        "what time is it in tokyo in 3 hours",
    ),
)
def test_a_request_to_reason_is_never_hijacked_by_the_calculator(text: str) -> None:
    """The direction that would trade one defect for a worse one: a thoughtful answer replaced by
    a failed parse."""

    assert evaluate_direct_math_request(text) is None, text
    assert looks_like_direct_math_request(text) is False, text


def test_the_calculator_refuses_anything_that_is_not_a_bare_expression() -> None:
    """No calls, names, attributes or subscripts may ever reach evaluation."""

    for text in ("calc __import__('os').system('ls')", "compute open('/etc/passwd').read()"):
        assert looks_like_direct_math_request(text) is False, text
        assert evaluate_direct_math_request(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        # Measured live (hostile seed 31337), inside a retraction turn: the withdrawal worked and
        # the live remainder was "just tell me 31+51 asap" -- which then missed the calculator,
        # reached a model, and came back as a truncation notice containing no number at all.
        ("just tell me 31+51 asap", "82"),
        ("tell me 31+51", "82"),
        ("give me 12*12", "144"),
        ("show me 45+55 now", "100"),
        ("just tell me the answer to 7*8", "56"),
    ),
)
def test_a_request_opener_does_not_hide_the_expression(text: str, expected: str) -> None:
    answer = evaluate_direct_math_request(text)

    assert answer is not None, f"fell through to a model: {text!r}"
    assert expected in answer


@pytest.mark.parametrize(
    "text",
    (
        "tell me about the mortgage",
        "give me the news now",
        "show me the workspace files",
        "just tell me what time it is",
        "tell me a joke asap",
    ),
)
def test_a_request_opener_alone_never_summons_the_calculator(text: str) -> None:
    """Stripping the opener must not make every "tell me X" look like arithmetic."""

    assert evaluate_direct_math_request(text) is None, text


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        # Measured live (hostile seed 8675): 34 x 95 is 3230, and a model answered 2995.
        ("34 tiems 95 = ?", "3230"),
        ("91 tiems 69 = ", "6279"),
        ("what is 12 tmies 12", "144"),
        ("calc 45 pluss 55", "100"),
        ("what is 100 divded by 4", "25"),
        ("compute 60 mulitplied by 3", "180"),
    ),
)
def test_a_mistyped_operator_word_still_computes(text: str, expected: str) -> None:
    """Nobody proofreads a calculation, and a typo must not drop it into a stochastic lane.

    TWO guards, both required, and the second was learned the hard way. Edit distance alone is far
    too loose on short words: with only the between-two-numbers rule, "3 plums 4" computed 7,
    "5 limes 6" computed 30 and "2 mines 1" computed 1, because each of those nouns is ONE edit
    from an operator. Rewriting prose into a sum is a worse defect than the mistyped calculation
    the repair exists for, so the correction is also gated on the TURN carrying an explicit
    calculation signal -- an equals sign or a calculation verb.
    """

    answer = evaluate_direct_math_request(text)

    assert answer is not None, f"fell through to a model: {text!r}"
    assert expected in answer, f"{text!r} -> {answer!r}"


@pytest.mark.parametrize(
    "text",
    (
        # A real word between two numbers that is NOT an operator must stay a real word.
        "3 list 4 items",
        "the 5 files and 6 folders",
        "2 apples 3 oranges",
        "meet 3 people by 5 pm",
        # No numbers around it at all.
        "x times y",
        "tell me about the mortgage",
    ),
)
def test_a_word_between_numbers_is_not_rewritten_into_arithmetic(text: str) -> None:
    """The direction that would be catastrophic: prose silently reinterpreted as a sum."""

    assert evaluate_direct_math_request(text) is None, text


def test_the_edit_bound_is_tight_enough_to_be_safe() -> None:
    """`_within_one_edit` is the whole safety argument, so it is asserted directly."""

    from core.task_router import _within_one_edit

    assert _within_one_edit("tiems", "times")      # transposition
    assert _within_one_edit("pluss", "plus")       # insertion
    assert _within_one_edit("mius", "minus")       # deletion
    assert _within_one_edit("tomes", "times")      # substitution
    assert not _within_one_edit("list", "plus")
    assert not _within_one_edit("apples", "plus")
    assert not _within_one_edit("people", "times")


@pytest.mark.parametrize(
    "text",
    (
        # One edit from an operator, but the turn never says it wants a number.
        "3 plums 4",
        "5 limes 6",
        "2 mines 1",
        "i ate 3 plums 4 days ago",
        "7 oven 2",
    ),
)
def test_a_near_miss_noun_is_not_an_operator_without_a_calculation_signal(text: str) -> None:
    """The false positive this repair introduced on its first attempt, pinned so it cannot return.

    "plums", "limes" and "mines" are each a single edit from "plus", "times" and "minus". Without
    the calculation-signal gate every one of these prose fragments was answered with a number.
    """

    assert evaluate_direct_math_request(text) is None, text


def test_an_explicit_calculation_signal_does_license_the_correction() -> None:
    """The other side of that boundary, stated rather than hidden.

    "3 plums 4 = ?" DOES compute 7, and that is the intended reading: the user wrote an equals
    sign, so they asked for a number, and "plums" one edit from "plus" is then the obvious
    interpretation. The gate is about ambiguity, not about the word.
    """

    answer = evaluate_direct_math_request("3 plums 4 = ?")

    assert answer is not None and "7" in answer


# ---------------------------------------------------------------------------------------------
# Named operations -- arithmetic people write in words rather than symbols
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        # Measured live 2026-08-15: this exact question reached a cloud model and returned
        # "echo fourtytwo" -- wrong by thirty, misspelled, and 16,212 tokens spent on it.
        ("what is the square root of 144", "12"),
        ("square root of 144", "12"),
        ("sqrt(144)", "12"),
        ("√144", "12"),
        # Not a perfect square: rounded, not shown to fifteen places.
        ("the square root of 145", "12.0416"),
        ("12 squared", "144"),
        ("5 cubed", "125"),
        ("5 percent of 200", "10"),
        ("15% of 80", "12"),
    ),
)
def test_a_named_operation_is_computed_not_guessed(text: str, expected: str) -> None:
    answer = evaluate_direct_math_request(text)

    assert answer is not None, f"fell through to a model: {text!r}"
    assert expected in answer, f"{text!r} -> {answer!r}"


@pytest.mark.parametrize(
    "text",
    (
        # The operand is what makes the operation real. Without a number these are metaphors and
        # ordinary prose, and answering them with a decimal would be worse than not answering.
        "square root of the problem",
        "tell me about square roots",
        "the root cause of the outage",
        "5 percent of the time",
        "what is the capital of france",
    ),
)
def test_a_named_operation_without_an_operand_is_left_alone(text: str) -> None:
    assert evaluate_direct_math_request(text) is None, text


def test_the_symbol_path_is_untouched_by_the_named_path() -> None:
    """The named forms are a FALLBACK; every phrasing that already computed must still compute."""

    for text, expected in (
        ("calc 80*81 for me", "6480"),
        ("just tell me 31+51 asap", "82"),
        ("what is 80*81", "6480"),
        ("34 tiems 95 = ?", "3230"),
    ):
        answer = evaluate_direct_math_request(text)
        assert answer is not None and expected in answer, text
