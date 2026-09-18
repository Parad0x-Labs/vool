"""BLOCK-B seam: expression provenance by syntax — no magnitude whitelist.

Signed block (review-184040, consensus.md block c31a...bd4b): "structural constants/
operators parsed from the user's expression are typed AST nodes and need no magnitude
whitelist; evidence guards apply only to introduced factual values." Kills the
1..1000 band that refused t8's `% 7 == 0` and G3-S1's user-stated 2002/1001.
"""
from core.kernel import repl


def test_comparison_operands_are_structural():
    assert repl._structural_literal("0", "91 % 7 == 0")            # t8
    assert repl._structural_literal("102", "17 * 6 == 102")        # A9.1 predicate target
    assert repl._structural_literal("1001", "2002 / 2 == 1001")    # G3-S1
    assert repl._structural_literal("-1.20", "-0.75 > -1.20")      # A4.1 decimals compare


def test_scale_notation_is_structural():
    for lit in ("0", "1", "10", "100", "1000", "0.1", "0.01"):
        assert repl._structural_literal(lit, f"x * {lit}")
    assert repl._structural_literal("100", "460 * 19 / 100")       # per-100km


def test_factual_operands_are_not_structural():
    assert not repl._structural_literal("94.54", "10000 / 94.54")  # memory FX rate
    assert not repl._structural_literal("2002", "2002 / 2")        # arithmetic position:
    # 2002 must come from the USER (it does in G3-S1 — the stated-forms check admits
    # it); syntax alone does not bless arbitrary magnitudes in arithmetic positions.
    assert not repl._structural_literal("3.42", "1.55 * 3.42")     # the old tautology mint
    assert not repl._structural_literal("500", "1500 / 3 * 500")


def test_no_magnitude_band_survives_in_source():
    with open(repl.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert "abs(int(num)) <= 1000" not in src
    assert "abs(int(lit)) <= 1000" not in src
