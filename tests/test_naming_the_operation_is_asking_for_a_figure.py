"""A clause that NAMES an arithmetic operation is asking for a figure.

`_QUANTITY_ASK_CUES` (`core/conductor/operations.py`) listed nineteen ways of ASKING for a number --
`calculate`, `how much`, `worth`, `what would it cost` -- and no way of NAMING the operation. The gate
at `operations.py:912` refuses the clause when no cue matches, at PLAN time, before any dependency is
consulted.

Measured live at `50e61c70` on operator-supplied prompts, `model_ran=False`:

    "Look up the current Bitcoin price in USD. Multiply that exact fetched price by 3."
        -> route=live_data_typed_plan, 0.1s
        -> "Bitcoin: USD 64,079.00 ... Source: [CoinGecko](...)"
        -> the multiplication VANISHED. No answer, no "could not be answered", no trace.

    "Get the current gold price per ounce in USD. Divide that exact fetched value by 2."
        -> same shape, same silence.

The dependency machinery was never the problem, and neither was the pronoun. Both were measured and
refuted:

    "Multiply 7 by 5"                                     literals, no pronoun, no dependency -> REFUSED
    "Calculate that exact fetched price multiplied by 5"  pronoun AND dependency present      -> ADMITTED
    "calculate 64 / 8"                                    -> ADMITTED
    "Divide 64 by 8"      same arithmetic, same literals  -> REFUSED

The edge is present in the shipped plan (`depends_on=('...:market_quote:bitcoin',)`), the model emits
`expression: "step_0 * fact_1"` reaching correctly for the prior step, `market_quote` already exports
`price` via `exported_value_fields`, and hand-wiring the same plan computes `64054.0 * 5 = 320270.0`
exactly. Only admission failed. The single variable was the word "calculate".

WHY ONLY THREE VERBS. `_clause_has` (`operations.py:833-835`) is `any(cue in lowered ...)` -- plain
substring, no word boundary. Measured collisions that would admit clauses asking for no figure at
all: `ratio` inside "ope*ratio*n", `times` inside `"times_five"` and "how many times", `add` on "Add a
calendar event", `product` on "Product A costs $240", `plus` inside "surplus". Those are pinned below
so a later widening cannot land without noticing them.
"""

from __future__ import annotations

import pytest

from core.conductor.operations import _QUANTITY_ASK_CUES, _clause_has

# The measured failures. Each NAMES an operation and asks for no figure by any other wording.
NAMES_AN_OPERATION = {
    "multiply_fetched": "Multiply that exact fetched price by 3",
    "divide_fetched": "Divide that exact fetched value by 2",
    "multiply_literals": "Multiply 7 by 5",
    "divide_literals": "Divide 64 by 8",
    # NOT "subtract 12 from the total" -- `total` is a pre-existing cue, so that clause is admitted
    # with or without this change and the sabotage arm below could never bite on it.
    "subtract": "Subtract 12 from the fetched value",
}

# Already admitted before this change. Pinned so the widening cannot be mistaken for what fixed them.
ALREADY_ADMITTED = {
    "calculate": "calculate 64 / 8",
    "ratio_with_calculate": "Calculate the ratio of the fetched Bitcoin price to the Ethereum price",
    "how_much": "How much is 1000 EUR in USD",
}

# Clauses that ask for NO figure. The cue list is deliberately narrow because sending these to an
# arithmetic node produces a node that can only fail -- see the note above `_QUANTITY_ASK_CUES`.
ASKS_FOR_NO_FIGURE = {
    "explain_losses": "ways they could reduce currency-conversion losses",
    "calendar": "Add a calendar event for tomorrow",
    "file_operation": "Cancel the file operation before execution",
    "product_noun": "Product A costs $240 and Product B costs $160",
    # NOT "how many times did the build fail" -- that clause really does ask for a count, and
    # `how many` has always admitted it. The `times` collision is pinned by `json_key` instead.
    "json_key": '{"btc":[value],"times_five":[value]}',
    "surplus": "Explain what a trade surplus means",
}


@pytest.mark.parametrize("name", sorted(NAMES_AN_OPERATION))
def test_naming_an_operation_reaches_the_arithmetic_node(name: str) -> None:
    assert _clause_has(NAMES_AN_OPERATION[name], _QUANTITY_ASK_CUES), (
        f"{name}: the clause names an arithmetic operation and must not be refused at plan time"
    )


@pytest.mark.parametrize("name", sorted(ALREADY_ADMITTED))
def test_the_existing_cues_still_admit(name: str) -> None:
    assert _clause_has(ALREADY_ADMITTED[name], _QUANTITY_ASK_CUES)


@pytest.mark.parametrize("name", sorted(ASKS_FOR_NO_FIGURE))
def test_a_clause_asking_for_no_figure_is_still_refused(name: str) -> None:
    """The precision half. Each of these would be admitted by a cue this change deliberately omits."""
    assert not _clause_has(ASKS_FOR_NO_FIGURE[name], _QUANTITY_ASK_CUES), (
        f"{name}: admitted a clause that asks for no figure -- it would build a node that can only fail"
    )


def test_the_omitted_cues_would_each_break_a_real_clause() -> None:
    """Anti-vacuity for the OMISSIONS: prove every rejected candidate is rejected for a measured reason.

    Without this, a later change adds `ratio` or `add` for obvious-looking reasons and the precision
    tests above start failing with no explanation of why those words were ever left out.
    """
    collisions = {
        "ratio": "Cancel the file operation before execution",
        "times": '{"btc":[value],"times_five":[value]}',
        "add": "Add a calendar event for tomorrow",
        "product": "Product A costs $240 and Product B costs $160",
        "plus": "Explain what a trade surplus means",
    }
    for cue, victim in collisions.items():
        assert _clause_has(victim, (cue,)), (
            f"the substring collision for {cue!r} no longer reproduces -- if `_clause_has` gained word "
            f"boundaries, this omission can be revisited and this test rewritten"
        )
        assert cue not in _QUANTITY_ASK_CUES, f"{cue!r} was added despite a live substring collision"


def test_removing_the_new_cues_reproduces_the_silent_drop() -> None:
    """Sabotage: with the three verbs removed, the measured failures are refused again."""
    original = tuple(c for c in _QUANTITY_ASK_CUES if c not in {"multiply", "divide", "subtract"})
    assert len(original) == len(_QUANTITY_ASK_CUES) - 3

    still_admitted = [
        name for name, clause in NAMES_AN_OPERATION.items() if _clause_has(clause, original)
    ]
    assert not still_admitted, (
        "SABOTAGE DID NOT BITE: with the three verbs removed these must be refused again, so the "
        f"verbs are what admits them. Still admitted: {still_admitted}"
    )
    # And the pre-existing cues are untouched by the sabotage.
    for clause in ALREADY_ADMITTED.values():
        assert _clause_has(clause, original)
