"""An arithmetic ask on written numbers needs nothing current, however it is phrased.

Real-app check v3 on fe98e849 (installed app, local qwen3:8b, 2026-11-22): "alright how much is 3272*13" was computed
by the runtime ("3272 * 13 = 42536"), then refused because "how much is" read as a price lookup and nothing had been
retrieved to check the number against. The follow-up "Hmm ok now / 4 pls" then had no result to divide. The wording is
built from parts at random, so no fixed sentence is what passes.
"""
from __future__ import annotations

import random

import pytest

from core.execution_requirements import requirements_for

LEADS = ["", "alright ", "ok ", "hmm ", "quick q: ", "so "]
FRAMES = ["how much is {e}", "how much is {e}?", "what's {e}", "how much is {e} pls"]
OPS = ["*", "+", "-", "/", " x "]


@pytest.mark.parametrize("seed", range(8))
def test_a_sum_in_numbers_needs_no_current_information(seed):
    rng = random.Random(f"arith-{seed}")
    for _ in range(5):
        op = rng.choice(OPS)
        expression = f"{rng.randint(2, 9999)}{op}{rng.randint(2, 999)}"
        if rng.random() < 0.5:
            expression = expression.replace(op.strip(), f" {op.strip()} ")
        said = rng.choice(LEADS) + rng.choice(FRAMES).format(e=expression)
        assert not requirements_for(said, source_context={}).current_information_required, said


@pytest.mark.parametrize(
    "said",
    ["how much is bitcoin", "how much is 20 usd in eur", "how much is 5 + the price of gold", "how much is an ounce of gold today"],
)
def test_a_price_still_needs_current_information(said):
    assert requirements_for(said, source_context={}).current_information_required, said
