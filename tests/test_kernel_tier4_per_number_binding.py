"""TIER 4 (typed tuple / per-number binding) — block A, review-20260820-141451.

t16: "Energy X kWh, cost 419.52 NOK" combines a COMPUTED value (87.4 from a calc
receipt) and a USER-STATED value (419.52). LAW 2's single-source form rejected the
sentence, so nothing shipped. A claim may bind each number to its own relevant
receipt and ship with the union of refs — but a number is NEVER filled from an
unrelated session fact (the cross-source fabrication the rule exists to stop).
"""
import pytest

from core.kernel import repl
from core.kernel.evidence_types import EvidenceTypeError, TypedClaim, render_typed_answer, validate_claims


def test_per_number_binding_grounds_computed_plus_stated():
    receipts = {
        "ob1-calc": "computed locally: 460 * 19 / 100 = 87.4; inputs from user",
        "user": "output one sentence: Energy X kWh, cost 419.52 NOK",
    }
    homes = repl._bind_numbers_per_source({"87.4", "419.52"}, ["ob1-calc", "user"], receipts)
    assert homes == {"ob1-calc", "user"}


def test_per_number_binding_none_when_a_number_grounds_nowhere():
    receipts = {"ob1-calc": "computed locally: 460 * 19 / 100 = 87.4", "user": "cost 419.52 NOK"}
    assert repl._bind_numbers_per_source({"87.4", "999"}, ["ob1-calc", "user"], receipts) is None


def test_per_number_binding_refuses_cross_source_fill_from_unrelated_fact():
    # 419.52 lives ONLY in an unrelated fact s9, which is NOT an eligible candidate ref.
    receipts = {"ob1-calc": "= 87.4", "s9": "EV charge was 419.52 NOK", "user": "energy please"}
    assert repl._bind_numbers_per_source({"87.4", "419.52"}, ["ob1-calc", "user"], receipts) is None


def test_multi_ref_claim_validates_and_renders_with_union_marker():
    receipts = {
        "ob1-calc": "computed locally: 460 * 19 / 100 = 87.4; inputs from user",
        "user": "cost 419.52 NOK",
    }
    claim = TypedClaim(text="Energy 87.4 kWh, cost 419.52 NOK", ctype="observed",
                       refs=("ob1-calc", "user"))
    validate_claims([claim], receipts)                    # must not raise
    rendered = render_typed_answer([claim], receipts)
    assert "87.4" in rendered and "419.52" in rendered
    assert "ob1-calc+user" in rendered                    # union citation is visible


def test_single_ref_still_rejects_a_number_it_lacks():
    # The safeguard is intact: one receipt cannot ground a number it does not contain.
    receipts = {"ob1-calc": "computed locally: 460 * 19 / 100 = 87.4"}
    claim = TypedClaim(text="Energy 87.4 kWh, cost 419.52 NOK", ctype="observed", ref="ob1-calc")
    with pytest.raises(EvidenceTypeError):
        validate_claims([claim], receipts)
