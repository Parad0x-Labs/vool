"""SCALPEL fixture repair, failure class F, 2026-08-06, then ARGUS repair D3, 2026-08-06: a
deterministic candidate-sanity primitive, narrowed to the one direction it can actually settle.

General on purpose -- no filename, no bug class, no per-fixture exception. `classify_exception_
expectation` answers one structural question about ANY Python source (does the cited range raise,
or does it sit inside a swallowing except?), and `structural_exception_mismatch` uses that answer
to catch exactly ONE genuinely narrow, deterministic contradiction: a claim that the code silently
continues at a spot where it demonstrably, unconditionally raises instead.

ARGUS's D3 finding: the ORIGINAL version of this module also filtered the opposite direction -- a
claim that the code raises an unwanted exception, whenever the cited code contained ANY explicit
`raise`, on the theory that an explicit raise is always correct. That reasoning is wrong: a wrong
triggering condition, the wrong exception type, a leaked secret in the message, or a violated
contract are all real defects an explicit `raise` can carry, and none of them are questions this
module's AST walk can answer. Every "claim of unwanted raise against a designed raise" test below
now asserts the SURVIVING behavior (the candidate reaches challenge, unfiltered) -- the exact
opposite of what this file asserted before this repair.
"""
from __future__ import annotations

from core.agent_runtime.candidate_sanity import (
    classify_exception_expectation,
    exception_claim_direction,
    structural_exception_mismatch,
)

CODEC_SOURCE = (
    "class Codec:\n"
    "    def decompress(self, blob):\n"
    "        if blob.startswith(b'RPT1'):\n"
    "            blob = blob[4:]\n"
    "        out = bytearray()\n"
    "        while blob:\n"
    "            try:\n"
    "                pid = blob[0]\n"
    "            except Exception:\n"
    "                break\n"
    "            out.extend(blob[:pid])\n"
    "            blob = blob[pid:]\n"
    "        return bytes(out)\n"
    "\n"
    "    def strict_decompress(self, blob):\n"
    "        if not blob:\n"
    "            raise ValueError('empty blob')\n"
    "        return blob\n"
    "\n"
    "    def reraising(self, blob):\n"
    "        try:\n"
    "            return blob[0]\n"
    "        except IndexError:\n"
    "            raise\n"
    "\n"
    "    def logs_and_continues(self, blob):\n"
    "        try:\n"
    "            return blob[0]\n"
    "        except IndexError:\n"
    "            return None\n"
    "\n"
    "    def decode_record(self, blob, token):\n"
    "        try:\n"
    "            return int(blob)\n"
    "        except ValueError:\n"
    "            try:\n"
    "                return int(blob, 16)\n"
    "            except ValueError:\n"
    "                pass\n"
    "            raise RuntimeError(f'decode failed for token={token}')\n"
    "\n"
    "    def validate_range(self, index, length):\n"
    "        if index <= length:\n"
    "            raise IndexError('index out of range')\n"
    "        return index\n"
)
# Line numbers, computed once and pinned so a fixture edit cannot silently desync a citation:
# 10 `break` (bare except, swallows) | 17 `raise ValueError(...)` (strict_decompress, designed) |
# 24 `raise` (bare re-raise) | 30 `return None` (swallows) | 39 `pass` (INNER handler, swallows --
# the outer `except ValueError:` at line 35-40 also spans this line and re-raises at line 40, the
# exact "inner swallows, outer re-raises something else" shape) | 40 `raise RuntimeError(f"...
# token={token}")` (outer re-raise, carries `token` verbatim into the message) | 44 `raise
# IndexError(...)` (fires on `index <= length` -- backwards; the boundary condition itself is wrong).


def test_a_bare_except_that_breaks_is_classified_as_swallowing() -> None:
    result = classify_exception_expectation(CODEC_SOURCE, 10, 10)
    assert result.verdict == "swallows"


def test_an_explicit_raise_is_classified_as_raising() -> None:
    result = classify_exception_expectation(CODEC_SOURCE, 17, 17)
    assert result.verdict == "raises"


def test_a_bare_reraise_inside_except_is_classified_as_raising_not_swallowing() -> None:
    """`except IndexError: raise` re-raises -- must not be conflated with a swallow just because
    it sits inside a handler."""
    result = classify_exception_expectation(CODEC_SOURCE, 24, 24)
    assert result.verdict == "raises"


def test_a_return_inside_except_is_classified_as_swallowing() -> None:
    result = classify_exception_expectation(CODEC_SOURCE, 30, 30)
    assert result.verdict == "swallows"


def test_a_line_with_no_try_except_nearby_is_unknown() -> None:
    result = classify_exception_expectation(CODEC_SOURCE, 4, 4)
    assert result.verdict == "unknown"


def test_unparseable_source_is_unknown_not_a_crash() -> None:
    result = classify_exception_expectation("def broken(:\n", 1, 1)
    assert result.verdict == "unknown"


def test_an_inner_swallowing_handler_wins_over_an_outer_reraising_one() -> None:
    """ARGUS repair D3: the OUTER `except ValueError:` (line 34) re-raises a RuntimeError at line
    39, and its body's line span numerically covers the INNER handler's `pass` at line 38 too --
    the exact shape where `ast.walk`'s traversal order could return the outer (wrong, "raises")
    verdict for a citation that structurally belongs to the inner (correct, "swallows") handler.
    The innermost/smallest-span handler must win."""
    result = classify_exception_expectation(CODEC_SOURCE, 39, 39)
    assert result.verdict == "swallows", (
        f"a citation inside the INNER handler must not be attributed to the outer one: {result}"
    )


def test_exception_claim_direction_recognizes_expects_raise() -> None:
    assert (
        exception_claim_direction("This raises an IndexError on malformed input.") == "expects_raise"
    )
    assert (
        exception_claim_direction("The function unexpectedly crashes on empty input.")
        == "expects_raise"
    )
    assert (
        exception_claim_direction("This raises the wrong exception type here.") == "expects_raise"
    )


def test_exception_claim_direction_recognizes_expects_silent() -> None:
    assert exception_claim_direction(
        "The function silently returns wrong data instead of raising."
    ) == "expects_silent"


def test_exception_claim_direction_is_empty_for_unrelated_claims() -> None:
    assert exception_claim_direction("The loop runs one extra iteration than necessary.") == ""


def test_exception_claim_direction_does_not_fire_on_the_data_loss_idiom() -> None:
    """ARGUS repair D3: "throws away bytes" is ordinary data-loss prose, not an exception claim --
    the bare-verb keyword match previously misclassified it as `expects_raise` purely because the
    word "throws" appears in it. Required to survive the filter: with no direction at all, the
    candidate never reaches `structural_exception_mismatch`'s comparison in the first place."""
    assert exception_claim_direction("The decoder throws away trailing bytes on overflow.") == ""


def test_a_claim_of_unwanted_raise_against_a_designed_raise_is_never_filtered() -> None:
    """ARGUS repair D3's central correction: a claim that the code raises an unwanted exception is
    NEVER filtered here, no matter how explicit and controlled the cited `raise` looks -- whether
    that raise is itself the bug is not a question this AST read can answer. This is the exact
    inverse of what this test asserted before the repair."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=17,
        line_end=17,
        failure_scenario="strict_decompress raises an unexpected ValueError on empty input.",
    )
    assert mismatch == "", (
        "a claim that the code raises must reach challenge, not be filtered on the theory that an "
        "explicit raise is always correct"
    )


def test_a_claim_of_wrong_exception_type_is_never_filtered() -> None:
    """Required-survive case: the code DOES raise, but the claim is that it raises the WRONG type
    -- a question this module's AST walk has no way to evaluate."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=44,
        line_end=44,
        failure_scenario="validate_range raises IndexError here, but the documented contract for "
        "this argument requires a ValueError instead, so callers cannot catch it correctly.",
    )
    assert mismatch == ""


def test_a_claim_of_a_wrong_boundary_condition_is_never_filtered() -> None:
    """Required-survive case: the raise fires on `index <= length`, which is backwards for a
    range check (an off-by-one/boundary defect) -- the AST sees only that a raise exists, not
    whether ITS OWN condition is correct."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=43,
        line_end=44,
        failure_scenario="validate_range raises for index <= length, which unexpectedly rejects "
        "the exact valid boundary index == length that callers rely on.",
    )
    assert mismatch == ""


def test_a_claim_that_a_message_leaks_a_secret_is_never_filtered() -> None:
    """Required-survive case: the RuntimeError at line 40 is real and designed, but the CLAIM is
    that its message leaks the raw `token` value verbatim -- a real, code-visible fact
    (`f'decode failed for token={token}'`) that has nothing to do with whether the raise itself is
    "correct", and that this module never evaluates either way."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=40,
        line_end=40,
        failure_scenario="decode_record raises RuntimeError with the raw token value embedded "
        "verbatim in the message, leaking it to any caller or log that captures the exception text.",
    )
    assert mismatch == ""


def test_valid_input_incorrectly_reaching_an_explicit_raise_is_never_filtered() -> None:
    """Required-survive case: the claim IS about a real raise the code contains, and the claim IS
    that it fires on input that should be valid -- exactly the "wrong triggering condition" shape
    ARGUS names, and exactly what the pre-repair filter would have dismissed as "designed
    behavior"."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=44,
        line_end=44,
        failure_scenario="An empty (zero-length) blob is a documented valid input, but "
        "strict_decompress raises ValueError on it instead of returning the empty result.",
    )
    assert mismatch == ""


def test_an_inner_swallow_under_an_outer_reraise_still_supports_a_silent_claim() -> None:
    """The one surviving filter direction still works correctly THROUGH the innermost-handler fix:
    a silent-failure claim cited at the truly-swallowing inner line is not spuriously matched
    against the outer handler's raise."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=39,
        line_end=39,
        failure_scenario="decode_record silently falls through to the hex parse and swallows a "
        "malformed decimal value instead of raising.",
    )
    assert mismatch == "", (
        "the inner swallow is real and the claim is plausible -- it must reach challenge"
    )


def test_a_claim_of_unwanted_raise_against_a_swallowing_path_is_not_contradicted() -> None:
    """A real silent-failure candidate, cited at a genuinely swallowing line, is not filtered."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=10,
        line_end=10,
        failure_scenario="A malformed record silently truncates the output instead of raising.",
    )
    assert mismatch == "", "a plausible silent-failure claim must not be filtered"


def test_a_silent_claim_against_a_demonstrably_raising_line_is_still_the_one_filterable_shape() -> None:
    """The one case this module can still legitimately settle: a claim that the code silently
    continues, cited at a line that unconditionally, demonstrably raises instead."""
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=17,
        line_end=17,
        failure_scenario="strict_decompress silently accepts an empty blob and returns garbage "
        "instead of raising.",
    )
    assert mismatch, "a silent-failure claim genuinely contradicted by a real raise must be flagged"
    assert "does not silently continue" in mismatch


def test_a_claim_with_no_exception_language_is_never_flagged() -> None:
    mismatch = structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=17,
        line_end=17,
        failure_scenario="This runs a full O(n^2) scan for every lookup, which is slow.",
    )
    assert mismatch == ""


def test_sabotage_removing_the_reraise_check_misclassifies_a_bare_reraise() -> None:
    """Reverts ONLY the `ast.Raise`-inside-handler-body re-raise check -- treats every except
    clause as swallowing regardless of its body -- and proves that reproduces a misclassification
    on the exact `except IndexError: raise` shape; then proves the real code differs from it."""

    def _sabotaged_classify(source: str, line_start: int, line_end: int) -> str:
        import ast as _ast

        tree = _ast.parse(source)
        start, end = line_start, line_end
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Try):
                continue
            for handler in node.handlers:
                handler_start = handler.lineno
                if handler_start <= start <= end or handler_start <= end:
                    # SABOTAGE: never checks whether the body re-raises.
                    return "swallows"
        return "unknown"

    sabotaged = _sabotaged_classify(CODEC_SOURCE, 24, 24)
    assert sabotaged == "swallows", "sabotage setup failed to reproduce the misclassification"

    real = classify_exception_expectation(CODEC_SOURCE, 24, 24)
    assert real.verdict == "raises", (
        f"the real, fixed classifier must not reproduce the sabotaged misclassification: {real}"
    )


def test_sabotage_reintroducing_the_expects_raise_filter_over_filters(monkeypatch) -> None:
    """Reverts `structural_exception_mismatch` to the pre-D3 shape (also filters a claim that the
    code raises an unwanted exception, whenever the cited code contains any explicit raise) and
    proves the required-survive case above now gets wrongly filtered."""
    import core.agent_runtime.candidate_sanity as candidate_sanity

    def sabotaged_mismatch(*, source, line_start, line_end, failure_scenario):
        direction = candidate_sanity.exception_claim_direction(failure_scenario)
        if not direction:
            return ""
        expectation = candidate_sanity.classify_exception_expectation(source, line_start, line_end)
        if direction == "expects_raise" and expectation.verdict == "raises":
            # SABOTAGE: the exact pre-D3 branch, reintroduced.
            return "the claim describes an unwanted exception, but the cited code contains an " \
                "explicit, controlled `raise` at that exact location -- this is the code's " \
                "designed behavior, not an accidental crash"
        if direction == "expects_silent" and expectation.verdict == "raises":
            return (
                "the claim describes silent wrong output, but the cited code contains an explicit, "
                "controlled `raise` at that exact location -- it does not silently continue"
            )
        return ""

    monkeypatch.setattr(
        candidate_sanity, "structural_exception_mismatch", sabotaged_mismatch
    )

    mismatch = candidate_sanity.structural_exception_mismatch(
        source=CODEC_SOURCE,
        line_start=17,
        line_end=17,
        failure_scenario="strict_decompress raises an unexpected ValueError on empty input.",
    )
    assert mismatch, (
        "sabotage setup failed to reproduce the pre-D3 over-filtering — the real regression must "
        "fail once a raise-claim is filtered merely for matching an existing raise statement"
    )
