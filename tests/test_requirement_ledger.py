"""The requirement ledger: capture from a proof, resolve, realize -- and what none of them may do.

    prove -> CAPTURE -> RESOLVE -> REALIZE -> bind -> execute -> reduce

The invariants under test are the ones that survived the F2 authority replacement unchanged,
because they were never about how a role was recognised:

* requirement existence precedes entity resolution -- `Palladium` is a requirement whatever the
  resolver says, and nothing is ever fetched for it;
* the ledger is immutable and cannot gain or lose an entry after capture;
* dependency identity is the requirement id, so `BTC` and `Bitcoin` are one edge;
* slot-level narrowing reduces which prerequisites matter, never whether one exists;
* a planner's omission is not an input to any of the above.

What changed is the front door. Capture reads a validated `SemanticTurnProof` and nothing else, so
every test here that needs an open-domain role supplies a proposer -- the same seam production
injects a model into.
"""
from __future__ import annotations

import pytest

from core.conductor.realization import (
    CapabilityLookupState,
    RealizationState,
    lookup_capability,
    plan_realizations,
    resolve_capabilities,
)
from core.conductor.registry import OperationSpec, register_operation, unregister_operation
from core.conductor.requirement_projection import resolve_ledger
from core.conductor.requirements import (
    Polarity,
    RequirementLedger,
    SlotResolution,
    UserRequirement,
    capture_requirements,
)
from tests.semantic_proposer import coordinated, frame, proposer, role


def _market(*assets: str, scope: str = "", polarity: str = "affirmed"):
    return frame(
        "market_quote",
        scope=scope or f"the price of {' and '.join(assets)}",
        predicate="price",
        roles=coordinated("asset_subject", *assets),
        polarity=polarity,
    )


def _weather(*places: str, scope: str = "", polarity: str = "affirmed"):
    return frame(
        "weather_lookup",
        scope=scope or f"the weather in {' and '.join(places)}",
        predicate="weather",
        roles=coordinated("location", *places),
        polarity=polarity,
    )


def _subjects(ledger: RequirementLedger, family: str, role_name: str) -> list[str]:
    return [
        slot.surface
        for requirement in ledger.of_family(family)
        for slot in requirement.slots
        if slot.role == role_name
    ]


# --- capture ------------------------------------------------------------------------------------


def test_a_coordinated_subject_list_is_captured_member_by_member():
    ledger = capture_requirements(
        "Get the price of Gold and Silver",
        propose=proposer(_market("Gold", "Silver")),
    )
    assert _subjects(ledger, "market_quote", "asset_subject") == ["Gold", "Silver"]
    assert len(ledger) == 1, "one frame is one requirement covering both subjects"


def test_capture_invents_nothing_for_a_message_no_path_proved():
    """NEGATIVE CONTROL. Positive frames only -- no leftover-noun rule, no unknown-token rule."""
    for text in (
        "Thanks, that was helpful.",
        "Gold Silver Palladium",
        "Vilnius",
        "please",
        "?!",
    ):
        assert len(capture_requirements(text)) == 0, text


def test_two_conversions_are_two_requirements():
    """Captured by the closed grammar. Two conversions are two requirements, executable or not."""
    ledger = capture_requirements("convert 500 EUR to JPY and 300 GBP to USD")
    assert len(ledger.of_family("fx_quote")) == 2
    assert [r.surface for r in ledger.of_family("fx_quote")] == [
        "500 EUR to JPY",
        "300 GBP to USD",
    ]


def test_two_expressions_are_two_requirements():
    ledger = capture_requirements("What is 137 x 29 and 84 / 7?")
    assert [r.surface for r in ledger.of_family("calculation")] == ["137 x 29", "84 / 7"]


def test_a_negated_frame_is_captured_and_is_not_executable():
    ledger = capture_requirements(
        "Never look up the price of Gold",
        propose=proposer(
            _market("Gold", scope="Never look up the price of Gold", polarity="negated")
        ),
    )
    assert len(ledger) == 1, "a refusal is evidence and must stay visible"
    assert ledger.requirements[0].polarity is Polarity.NEGATED
    assert ledger.executable == ()


def test_unclaimed_text_stays_visible_and_inherits_into_nothing():
    ledger = capture_requirements(
        "Get the weather in Oslo and open core/conductor/planner.py",
        propose=proposer(_weather("Oslo", scope="the weather in Oslo")),
    )
    assert _subjects(ledger, "weather_lookup", "location") == ["Oslo"]
    assert any("core/conductor/planner.py" in text for text in ledger.unowned_text())


# --- resolution attaches; it never decides existence --------------------------------------------


def test_an_unresolvable_subject_keeps_its_requirement_and_is_never_fetched():
    """Palladium. The requirement exists, the slot is UNRESOLVED_SUBJECT, no work is looked up."""
    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold and Palladium",
            propose=proposer(_market("Gold", "Palladium")),
        )
    )
    requirement = ledger.of_family("market_quote")[0]
    states = {slot.surface: slot.resolution for slot in requirement.slots}
    assert states["Gold"] is SlotResolution.RESOLVED
    assert states["Palladium"] is SlotResolution.UNRESOLVED_SUBJECT

    realizations = resolve_capabilities(plan_realizations(ledger))
    by_subject = {r.display_subject: r for r in realizations}
    assert by_subject["Gold"].lookup.state is CapabilityLookupState.AVAILABLE
    assert by_subject["Palladium"].lookup.state is CapabilityLookupState.UNRESOLVED_SUBJECT
    assert by_subject["Palladium"].arguments == {}, "an unresolved subject buys no arguments"


def test_a_resolver_cannot_add_or_remove_a_requirement():
    """The ledger refuses an entry it did not capture. Structural, not conventional."""
    ledger = capture_requirements("What is 137 x 29?")
    stranger = UserRequirement(
        requirement_id="req:99:market_quote", start=0, end=1, surface="x", family="market_quote"
    )
    with pytest.raises(KeyError):
        ledger.with_requirement(stranger)


def test_a_resolver_that_raises_is_a_defect_and_not_an_unknown_subject():
    """The two sentences are about different things and only one of them is ever true."""

    def _explode(_subject):
        raise RuntimeError("realizer is broken")

    register_operation(
        OperationSpec(
            name="market_quote_probe",
            description="probe",
            expand_arguments=lambda _c: [],
            run=lambda _n, _c: {},
            render=lambda _n, _r: "",
            realize_subject=_explode,
        ),
        replace=True,
    )
    from core.conductor.semantic_proof import FrameContract, register_frame_contract

    register_frame_contract(
        FrameContract(
            family="market_quote_probe",
            roles=("asset_subject",),
            required_roles=("asset_subject",),
            fan_out_role="asset_subject",
            display_roles=("asset_subject",),
        ),
        replace=True,
    )
    try:
        ledger = resolve_ledger(
            capture_requirements(
                "Get the price of Gold",
                propose=proposer(
                    frame(
                        "market_quote_probe",
                        scope="the price of Gold",
                        predicate="price",
                        roles=[role("asset_subject", "Gold")],
                    )
                ),
            )
        )
        slot = ledger.requirements[0].slots[0]
        assert slot.resolution is SlotResolution.RESOLVER_DEFECT
        realization = resolve_capabilities(plan_realizations(ledger)).realizations[0]
        assert realization.lookup.state is CapabilityLookupState.DEFECT
        assert realization.lookup.state is not CapabilityLookupState.UNAVAILABLE
    finally:
        unregister_operation("market_quote_probe")
        from core.conductor.semantic_proof import unregister_frame_contract

        unregister_frame_contract("market_quote_probe")


# --- dependency identity and narrowing ----------------------------------------------------------


def _btc_gold_ratio():
    return capture_requirements(
        "Get the price of BTC and Gold, then work out the ratio of Bitcoin to Gold.",
        propose=proposer(
            _market("BTC", "Gold", scope="the price of BTC and Gold"),
            frame(
                "quantitative_reasoning",
                scope="work out the ratio of Bitcoin to Gold",
                predicate="work out",
            ),
        ),
    )


def test_dependency_identity_is_the_requirement_id_not_the_surface():
    """BTC and Bitcoin are one identity: the edge points at an id, not at a spelling."""
    ledger = _btc_gold_ratio()
    derived = ledger.of_family("quantitative_reasoning")[0]
    producer = ledger.of_family("market_quote")[0]
    assert derived.requires == (producer.requirement_id,)


def test_narrowing_reduces_which_slots_matter_and_never_whether_the_edge_exists():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the weather in Porto, Hobart and Oslo, then work out how much warmer Porto is "
            "than Oslo.",
            propose=proposer(
                _weather("Porto", "Hobart", "Oslo", scope="the weather in Porto, Hobart and Oslo"),
                frame(
                    "quantitative_reasoning",
                    scope="work out how much warmer Porto is than Oslo",
                    predicate="work out",
                ),
            ),
        )
    )
    derived = ledger.of_family("quantitative_reasoning")[0]
    producer = ledger.of_family("weather_lookup")[0]
    assert derived.requires == (producer.requirement_id,), "the edge still exists"
    named = {
        slot.surface
        for slot in producer.slots
        if slot.slot_id in derived.input_bindings
    }
    assert named == {"Porto", "Oslo"}
    assert "Hobart" not in named, "a third city with no reading must not be required"


def test_an_anaphoric_derived_frame_requires_every_producer():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the weather in Porto and Oslo, then work out the difference between them.",
            propose=proposer(
                _weather("Porto", "Oslo", scope="the weather in Porto and Oslo"),
                frame(
                    "quantitative_reasoning",
                    scope="work out the difference between them",
                    predicate="work out",
                ),
            ),
        )
    )
    derived = ledger.of_family("quantitative_reasoning")[0]
    assert derived.input_bindings == (), "naming none of them means requiring all of them"
    assert derived.requires


def test_a_negated_producer_is_not_a_prerequisite():
    ledger = capture_requirements(
        "Do not look up the price of Gold, but work out the ratio anyway.",
        propose=proposer(
            _market("Gold", scope="Do not look up the price of Gold", polarity="negated"),
            frame(
                "quantitative_reasoning",
                scope="work out the ratio anyway",
                predicate="work out",
            ),
        ),
    )
    derived = ledger.of_family("quantitative_reasoning")[0]
    assert derived.requires == (), "a refused frame produces nothing to depend on"


# --- realization is a function of the requirement, not of its arguments -------------------------


def test_every_executable_requirement_gets_at_least_one_realization():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold and Palladium, then work out the ratio between them, and "
            "convert 500 EUR to JPY.",
            propose=proposer(
                _market("Gold", "Palladium", scope="the price of Gold and Palladium"),
                frame(
                    "quantitative_reasoning",
                    scope="work out the ratio between them",
                    predicate="work out",
                ),
            ),
        )
    )
    realizations = plan_realizations(ledger)
    covered = {r.requirement_id for r in realizations.realizations}
    assert covered == {r.requirement_id for r in ledger.executable}


def test_a_requirement_with_no_ordinary_subject_slots_still_gets_exactly_one():
    """The derived frame. Its realization exists BEFORE any node, which is the whole F4 repair."""
    ledger = resolve_ledger(_btc_gold_ratio())
    realizations = plan_realizations(ledger)
    derived = ledger.of_family("quantitative_reasoning")[0]
    theirs = realizations.of_requirement(derived.requirement_id)
    assert len(theirs) == 1
    assert theirs[0].owned_slot_ids == ()


def test_a_fan_out_family_realizes_once_per_coordinated_member():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold and Silver",
            propose=proposer(_market("Gold", "Silver")),
        )
    )
    realizations = plan_realizations(ledger)
    assert [r.display_subject for r in realizations.realizations] == ["Gold", "Silver"]


def _fx(base: str, quote: str, *, scope: str, amount: str = ""):
    """A conversion frame, PROVEN by the bounded path.

    A closed grammar may capture a conversion but may not affirm one -- it cannot see the sentence
    around the syntax, and the work reaches a network. Executable FX therefore needs a semantic
    proof, which is what this supplies.
    """
    roles = [role("base_currency", base), role("quote_currency", quote)]
    if amount:
        roles.append(role("amount", amount))
    return frame("fx_quote", scope=scope, predicate="to", roles=roles)


def test_a_family_with_no_fan_out_role_realizes_exactly_once():
    ledger = resolve_ledger(
        capture_requirements(
            "convert 500 EUR to JPY",
            propose=proposer(_fx("EUR", "JPY", scope="500 EUR to JPY", amount="500")),
        )
    )
    realizations = plan_realizations(ledger)
    assert len(realizations) == 1
    assert realizations.realizations[0].display_subject == "EUR to JPY"


def test_realization_ids_are_unique_by_construction():
    ledger = resolve_ledger(
        capture_requirements(
            "convert 500 EUR to JPY and 300 GBP to USD and 100 CHF to SEK",
            propose=proposer(
                _fx("EUR", "JPY", scope="500 EUR to JPY", amount="500"),
                _fx("GBP", "USD", scope="300 GBP to USD", amount="300"),
                _fx("CHF", "SEK", scope="100 CHF to SEK", amount="100"),
            ),
        )
    )
    realizations = plan_realizations(ledger)
    assert len(realizations.ids) == len(set(realizations.ids)) == 3


def test_capability_unavailable_comes_only_from_a_typed_lookup():
    """`structured_field` is a well-formed frame with no registered operation behind it.

    That separation is the reason the two registries are apart: the request is perfectly captured
    and perfectly resolved, and the runtime simply cannot serve it. Collapsing the two answers is
    how "no operation is registered" came to look like "the user did not ask for this".
    """
    ledger = resolve_ledger(capture_requirements("region: eu-west-1"))
    realization = resolve_capabilities(plan_realizations(ledger)).realizations[0]
    assert realization.family == "structured_field"
    assert realization.lookup.state is CapabilityLookupState.UNAVAILABLE
    assert "no registered operation" in realization.lookup.detail
    assert realization.lookup.non_execution.state is RealizationState.CAPABILITY_UNAVAILABLE


def test_a_registered_family_never_reports_itself_unavailable():
    """NEGATIVE CONTROL for the line above. UNAVAILABLE is a fact, not a default."""
    ledger = resolve_ledger(
        capture_requirements(
            "convert 500 EUR to JPY",
            propose=proposer(_fx("EUR", "JPY", scope="500 EUR to JPY", amount="500")),
        )
    )
    realization = resolve_capabilities(plan_realizations(ledger)).realizations[0]
    assert realization.lookup.state is CapabilityLookupState.AVAILABLE
    assert realization.arguments["base"] == "EUR"
    assert realization.arguments["quote"] == "JPY"


def test_the_planner_is_not_an_input_to_capture_at_all():
    """A planner's omission cannot change the ledger, because no planner output reaches it."""
    text = "Get the price of Gold and Silver"
    proposal = proposer(_market("Gold", "Silver"))
    first = capture_requirements(text, propose=proposal)
    second = capture_requirements(text, propose=proposal)
    assert first.to_dict() == second.to_dict()
    assert lookup_capability(
        plan_realizations(resolve_ledger(first)).realizations[0].work
    ).state is CapabilityLookupState.AVAILABLE
