"""Adversarial pins for Law 2 (core/kernel/evidence_types.py).

Every test here is built from a way the 2026-08-19 audit's fabrication class could sneak
past a weaker checker: a real receipt id glued to an invented number, digit-soup matching
('1.75' ~ '175'), an unverified claim rendering without its mark, or one bad claim hiding
inside an otherwise-valid answer. Assertions are exact-string where the output is the
contract, so a gutted renderer cannot pass by returning approximately the right prose.
"""
from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path

import pytest

try:
    from core.kernel.evidence_types import (
        CLAIM_TYPES,
        EvidenceTypeError,
        TypedClaim,
        render_typed_answer,
        validate_claims,
    )
except ModuleNotFoundError:
    # core/kernel/__init__.py imports all four law modules; in this PoC worktree the
    # sibling laws land from parallel lanes, so until the package assembles we load the
    # module file directly (it is stdlib-only). Once every sibling exists, the canonical
    # import above is what runs — this branch then never executes again.
    _path = Path(__file__).resolve().parents[1] / "core" / "kernel" / "evidence_types.py"
    _spec = importlib.util.spec_from_file_location("_kernel_evidence_types_poc", _path)
    assert _spec is not None and _spec.loader is not None
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    CLAIM_TYPES = _module.CLAIM_TYPES
    EvidenceTypeError = _module.EvidenceTypeError
    TypedClaim = _module.TypedClaim
    render_typed_answer = _module.render_typed_answer
    validate_claims = _module.validate_claims

# ---------------------------------------------------------------------------
# Surface contract
# ---------------------------------------------------------------------------


def test_claim_types_is_the_exact_pinned_tuple() -> None:
    # "conversational" added 2026-08-20: a greeting/question-back is not a world-claim;
    # forcing it through evidence typing refused "yo" as a fabrication, live.
    assert CLAIM_TYPES == ("timeless", "observed", "memory", "stipulated", "unverified", "conversational")


def test_typed_claim_is_frozen() -> None:
    claim = TypedClaim(text="water is wet", ctype="timeless")
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.ctype = "observed"  # type: ignore[misc]


def test_evidence_type_error_is_a_runtime_error_carrying_claim_and_reason() -> None:
    err = EvidenceTypeError("some claim", "some reason")
    assert isinstance(err, RuntimeError)
    assert err.claim_text == "some claim"
    assert err.reason == "some reason"
    assert "some reason" in str(err)


def test_validate_claims_returns_none_on_a_fully_valid_answer() -> None:
    claims = [
        TypedClaim("2 + 2 is 4", "timeless"),
        TypedClaim("EUR/USD is 1.17", "observed", ref="r1"),
        TypedClaim("you drive a diesel", "memory", ref="n1"),
        TypedClaim("assuming the fee is 2%", "stipulated"),
        TypedClaim("the 2019 facelift changed the grille", "unverified"),
    ]
    receipts = {"r1": "fx api returned 1.17 for EUR/USD"}
    nodes = {"n1": "user car: diesel"}
    assert validate_claims(claims, receipts, nodes) is None


# ---------------------------------------------------------------------------
# Refs: dangling, missing, and misplaced
# ---------------------------------------------------------------------------


def test_observed_with_dangling_ref_is_refused_naming_the_ref() -> None:
    claim = TypedClaim("the endpoint returned 200", "observed", ref="receipt-404")
    with pytest.raises(EvidenceTypeError, match="receipt-404") as exc:
        validate_claims([claim], receipts={"other": "the endpoint returned 200"})
    assert exc.value.claim_text == "the endpoint returned 200"
    assert "receipt-404" in exc.value.reason


def test_observed_with_empty_ref_is_refused() -> None:
    claim = TypedClaim("the file is 12 bytes", "observed", ref="")
    with pytest.raises(EvidenceTypeError):
        validate_claims([claim], receipts={"r1": "stat: 12 bytes"})


def test_memory_with_dangling_node_ref_is_refused_naming_the_ref() -> None:
    claim = TypedClaim("your name is Sam", "memory", ref="node-9")
    with pytest.raises(EvidenceTypeError, match="node-9"):
        validate_claims([claim], receipts={}, memory_nodes={"node-1": "name: Sam"})


def test_memory_with_empty_ref_is_refused() -> None:
    with pytest.raises(EvidenceTypeError):
        validate_claims([TypedClaim("your name is Sam", "memory", ref="")], receipts={},
                        memory_nodes={"node-1": "name: Sam"})


def test_memory_claim_when_no_memory_nodes_mapping_is_given_is_refused() -> None:
    # memory_nodes defaults to None; a memory claim then has nothing to resolve against
    # and must refuse, never silently pass.
    with pytest.raises(EvidenceTypeError, match="node-1"):
        validate_claims([TypedClaim("your name is Sam", "memory", ref="node-1")], receipts={})


@pytest.mark.parametrize("ctype", ["timeless", "stipulated", "unverified"])
def test_a_ref_smuggled_onto_a_refless_type_is_refused(ctype: str) -> None:
    # Mistyping an observed claim as e.g. unverified-with-a-ref would dodge the number
    # check while still looking cited. The ref itself is the type error.
    claim = TypedClaim("the price is 9.99", ctype, ref="r1")
    with pytest.raises(EvidenceTypeError):
        validate_claims([claim], receipts={"r1": "price: 9.99"})


def test_unknown_ctype_is_refused_not_rendered() -> None:
    claim = TypedClaim("trust me", "vibes")
    with pytest.raises(EvidenceTypeError, match="vibes"):
        validate_claims([claim], receipts={})
    with pytest.raises(EvidenceTypeError):
        render_typed_answer([claim], receipts={})


# ---------------------------------------------------------------------------
# Number containment: the fabrication check
# ---------------------------------------------------------------------------


def test_observed_number_absent_from_its_receipt_is_refused() -> None:
    # The audited currency shape: a real receipt, but the claim states a rate the
    # receipt never contained.
    claim = TypedClaim("1.75 EUR", "observed", ref="fx1")
    with pytest.raises(EvidenceTypeError, match=r"1\.75"):
        validate_claims([claim], receipts={"fx1": "rate lookup returned 1.62"})


def test_observed_number_present_in_its_receipt_renders() -> None:
    claim = TypedClaim("1.75 EUR", "observed", ref="fx1")
    out = render_typed_answer([claim], receipts={"fx1": "rate lookup returned 1.75"})
    assert out == "1.75 EUR [receipt:fx1]"


def test_thousands_separator_in_the_claim_matches_a_plain_receipt() -> None:
    claim = TypedClaim("the route is 1,420 km", "observed", ref="map1")
    out = render_typed_answer([claim], receipts={"map1": "distance: 1420 km"})
    assert out == "the route is 1,420 km [receipt:map1]"


def test_thousands_separator_in_the_receipt_matches_a_plain_claim() -> None:
    claim = TypedClaim("the route is 1420 km", "observed", ref="map1")
    out = render_typed_answer([claim], receipts={"map1": "distance: 1,420 km"})
    assert out == "the route is 1420 km [receipt:map1]"


def test_a_decimal_does_not_false_match_an_integer_with_the_same_digits() -> None:
    # '1.75' and '175' share digits but are different quantities; digit-soup matching
    # would let a fabricated decimal ride an unrelated integer.
    claim = TypedClaim("the rate is 1.75", "observed", ref="r1")
    with pytest.raises(EvidenceTypeError, match=r"1\.75"):
        validate_claims([claim], receipts={"r1": "count was 175 items"})


def test_an_integer_does_not_false_match_the_tail_of_a_decimal() -> None:
    # The reverse trap: '75' is a substring of '1.75' but the receipt never said 75.
    claim = TypedClaim("it costs 75 cents", "observed", ref="r1")
    with pytest.raises(EvidenceTypeError, match="'75'"):
        validate_claims([claim], receipts={"r1": "price: 1.75 EUR"})


def test_a_number_does_not_false_match_inside_a_larger_number() -> None:
    # '1420' is a digit-substring of '14200'; substring containment would accept a
    # ten-times-off fabrication.
    claim = TypedClaim("the route is 1,420 km", "observed", ref="r1")
    with pytest.raises(EvidenceTypeError, match="1420"):
        validate_claims([claim], receipts={"r1": "distance: 14,200 km"})


def test_every_number_in_an_observed_claim_must_be_in_the_receipt() -> None:
    # One backed number must not launder a second, invented one in the same sentence.
    claim = TypedClaim("it is 1.62 today, up from 1.20 last week", "observed", ref="fx1")
    with pytest.raises(EvidenceTypeError, match=r"1\.20"):
        validate_claims([claim], receipts={"fx1": "rate lookup returned 1.62"})


def test_grouped_decimal_matches_across_both_normalizations() -> None:
    claim = TypedClaim("revenue was 1,420.75", "observed", ref="r1")
    out = render_typed_answer([claim], receipts={"r1": "ledger total 1420.75"})
    assert out == "revenue was 1,420.75 [receipt:r1]"


# ---------------------------------------------------------------------------
# Rendering: markers are mandatory, atomic, and exact
# ---------------------------------------------------------------------------


def test_unverified_renders_wearing_the_mark_and_never_silently_without() -> None:
    out = render_typed_answer([TypedClaim("the 2019 facelift changed the grille", "unverified")],
                              receipts={})
    assert out == "the 2019 facelift changed the grille [unverified - model memory]"
    assert "[unverified - model memory]" in out  # the mark itself, not just any suffix


def test_stipulated_renders_with_its_mark() -> None:
    out = render_typed_answer([TypedClaim("assume the fee is waived", "stipulated")], receipts={})
    assert out == "assume the fee is waived [stipulated]"


def test_timeless_renders_bare_with_no_marker() -> None:
    out = render_typed_answer([TypedClaim("a week has 7 days", "timeless")], receipts={})
    assert out == "a week has 7 days"


def test_memory_renders_with_its_node_marker() -> None:
    out = render_typed_answer([TypedClaim("you prefer metric units", "memory", ref="n7")],
                              receipts={}, memory_nodes={"n7": "prefers metric"})
    assert out == "you prefer metric units [memory:n7]"


def test_claims_render_in_order_joined_with_newlines() -> None:
    claims = [
        TypedClaim("a week has 7 days", "timeless"),
        TypedClaim("EUR/USD is 1.17", "observed", ref="fx1"),
        TypedClaim("the trim names differ by market", "unverified"),
    ]
    out = render_typed_answer(claims, receipts={"fx1": "fx api: 1.17"})
    assert out == (
        "a week has 7 days\n"
        "EUR/USD is 1.17 [receipt:fx1]\n"
        "the trim names differ by market [unverified - model memory]"
    )


def test_one_invalid_claim_refuses_the_whole_render() -> None:
    # Atomicity: a partially-marked answer teaches the reader that unmarked sentences
    # are safe. Valid neighbors must not render around a fabrication.
    claims = [
        TypedClaim("a week has 7 days", "timeless"),
        TypedClaim("the rate is 1.75", "observed", ref="fx1"),  # receipt says 1.62
        TypedClaim("assume no fees", "stipulated"),
    ]
    with pytest.raises(EvidenceTypeError):
        render_typed_answer(claims, receipts={"fx1": "rate lookup returned 1.62"})


def test_empty_claims_list_renders_the_empty_string() -> None:
    assert render_typed_answer([], receipts={}) == ""


# ---------------------------------------------------------------------------
# The audited scenario, pinned by name
# ---------------------------------------------------------------------------


def test_AUDITED_SCENARIO_fabricated_car_fact_cannot_ship_as_observed_but_ships_marked() -> None:
    """2026-08-19 audit: 'the Mercedes-Benz Golf is better' shipped as flat prose.

    Under Law 2 that sentence typed observed is refused twice over — with no receipt,
    and with a receipt that never mentions it — and the only way it reaches the user
    is retyped unverified, wearing the mark.
    """
    fabricated = "the Mercedes-Benz Golf is better"

    # Typed observed with no receipt at all: refused.
    with pytest.raises(EvidenceTypeError):
        render_typed_answer([TypedClaim(fabricated, "observed", ref="")], receipts={})

    # Typed observed citing a receipt that does not exist: refused, naming the ref.
    with pytest.raises(EvidenceTypeError, match="car-search-1"):
        render_typed_answer([TypedClaim(fabricated, "observed", ref="car-search-1")], receipts={})

    # Retyped honestly as unverified: renders, wearing the mark.
    out = render_typed_answer([TypedClaim(fabricated, "unverified")], receipts={})
    assert out == "the Mercedes-Benz Golf is better [unverified - model memory]"
