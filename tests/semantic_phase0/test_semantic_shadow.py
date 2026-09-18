"""The SHADOW rung observes and records; it never dispatches and never breaks a turn."""
from __future__ import annotations

import pytest

from core.semantic import shadow
from core.semantic.authority_mode import SemanticAuthorityMode
from core.semantic.canonical_text import CanonicalText
from core.semantic.types import IntentProposal


class _Resolver:
    """Records that it was consulted; returns a fixed proposal set. No side effects beyond that."""

    def __init__(self, proposals, *, boom: bool = False) -> None:
        self._proposals = proposals
        self._boom = boom
        self.consulted = 0

    def propose(self, canonical, *, operations):
        self.consulted += 1
        if self._boom:
            raise RuntimeError("resolver exploded")
        return self._proposals


def _canonical(text: str) -> CanonicalText:
    return CanonicalText.of(text)


def _props(n: int, op: str = "unknown") -> tuple[IntentProposal, ...]:
    return tuple(IntentProposal(index=i, request_text=f"clause {i}", operation=op) for i in range(n))


@pytest.fixture(autouse=True)
def _clean_ring():
    shadow.clear_observations()
    yield
    shadow.clear_observations()


# -- the differential ---------------------------------------------------------


def test_agreement_when_counts_match() -> None:
    obs = shadow.build_observation(
        _canonical("a and b"),
        _props(2, "market.quote"),
        mode=SemanticAuthorityMode.SHADOW,
        resolver_available=True,
        heuristic_request_count=2,
        heuristic_answer_mode="GROUNDED",
    )
    assert obs.clause_count_agrees is True
    assert obs.resolver_clause_count == 2
    assert obs.resolver_operations == ("market.quote",)  # de-duplicated
    assert obs.resolver_operation_list == ("market.quote", "market.quote")  # whole multiset kept
    assert obs.dispatched is False


def test_disagreement_when_counts_differ_is_the_over_under_split_signal() -> None:
    obs = shadow.build_observation(
        _canonical("weather in oslo and how did tesla close"),
        _props(1),  # resolver saw ONE thing; heuristic saw two → under-split
        mode=SemanticAuthorityMode.SHADOW,
        resolver_available=True,
        heuristic_request_count=2,
        heuristic_answer_mode="LIVE_DATA",
    )
    assert obs.clause_count_agrees is False


def test_observation_records_the_digest_not_the_text() -> None:
    canonical = _canonical("secret sauce recipe")
    obs = shadow.build_observation(
        canonical, _props(1), mode=SemanticAuthorityMode.SHADOW, resolver_available=True,
        heuristic_request_count=1, heuristic_answer_mode="DIRECT",
    )
    payload = obs.to_dict()
    assert payload["request_digest"] == canonical.digest
    assert "secret sauce" not in str(payload)


# -- the seam -----------------------------------------------------------------


def test_off_mode_is_a_no_op() -> None:
    resolver = _Resolver(_props(1))
    out = shadow.observe_turn_in_shadow(
        _canonical("hi"), resolver=resolver, operations=("x",),
        heuristic_request_count=1, heuristic_answer_mode="DIRECT",
        mode=SemanticAuthorityMode.OFF,
    )
    assert out is None
    assert resolver.consulted == 0        # the resolver is not even consulted in OFF
    assert shadow.recent_observations() == ()


def test_shadow_mode_consults_and_records() -> None:
    resolver = _Resolver(_props(2, "weather.forecast"))
    out = shadow.observe_turn_in_shadow(
        _canonical("a and b"), resolver=resolver, operations=("weather.forecast",),
        heuristic_request_count=2, heuristic_answer_mode="LIVE_DATA",
        mode=SemanticAuthorityMode.SHADOW,
    )
    assert resolver.consulted == 1
    assert out is not None and out.resolver_clause_count == 2 and out.dispatched is False
    assert len(shadow.recent_observations()) == 1


def test_a_raising_resolver_is_recorded_as_unavailable_not_propagated() -> None:
    resolver = _Resolver(_props(1), boom=True)
    out = shadow.observe_turn_in_shadow(
        _canonical("a"), resolver=resolver, operations=("x",),
        heuristic_request_count=1, heuristic_answer_mode="DIRECT",
        mode=SemanticAuthorityMode.SHADOW,
    )
    assert out is not None
    assert out.resolver_available is False
    assert out.resolver_clause_count == 0
    assert out.dispatched is False


# -- the never-dispatch invariant --------------------------------------------


def test_recording_a_dispatched_observation_is_refused() -> None:
    """SABOTAGE SENTINEL: SHADOW cannot act on a turn. A dispatched observation is a broken invariant."""
    bad = shadow.SemanticShadowObservation(
        request_digest="d", mode="shadow", resolver_available=True,
        resolver_clause_count=1, resolver_operations=("x",),
        heuristic_request_count=1, heuristic_answer_mode="DIRECT",
        clause_count_agrees=True, dispatched=True,
    )
    with pytest.raises(ValueError):
        shadow.record_observation(bad)


def test_abstain_and_fallback_grants_no_authority() -> None:
    """ABSTAIN-AND-FALLBACK contract: a resolver that abstains (returns ()) records zero proposals,
    never dispatches, and produces nothing that could be admitted — so a failed/abstaining resolver
    cannot grant tool or action authority. 'Couldn't resolve' never becomes 'execute anyway'."""
    resolver = _Resolver(())  # abstains: zero proposals
    out = shadow.observe_turn_in_shadow(
        _canonical("do the dangerous thing"), resolver=resolver, operations=("x",),
        heuristic_request_count=1, heuristic_answer_mode="DIRECT",
        mode=SemanticAuthorityMode.SHADOW,
    )
    assert out is not None
    assert out.resolver_clause_count == 0      # nothing proposed
    assert out.resolver_operations == ()        # nothing to admit → no authority obtainable
    assert out.dispatched is False              # and nothing acted on


def test_ring_is_bounded() -> None:
    for _ in range(shadow._MAX_OBSERVATIONS + 50):
        shadow.record_observation(
            shadow.build_observation(
                _canonical("x"), _props(1), mode=SemanticAuthorityMode.SHADOW,
                resolver_available=True, heuristic_request_count=1, heuristic_answer_mode="DIRECT",
            )
        )
    assert len(shadow.recent_observations()) == shadow._MAX_OBSERVATIONS
