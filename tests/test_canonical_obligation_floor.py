"""The user's obligations outlive the decomposition that was supposed to serve them.

Two properties, one class. A conductor turn passes through two stages that each get to say what
work exists -- a model planner proposing clauses, and an operation's recognizer expanding a clause
into per-subject nodes -- and at base BOTH stages may return less than the user asked for with no
record anywhere that anything was dropped. `compose_answer.complete` then reports True, because it
compares the plan against itself: every node it planned has an outcome, so nothing is missing.

    INVARIANT A. Every obligation the user's request establishes terminates in SATISFIED or
    EXPLICITLY_UNSERVED. A stage that declines part of the request converts it into a reported
    gap, never into an absence. Completion is measured against the user's obligation set, never
    against the decomposition's own output.

    INVARIANT B. A dependency that succeeded contributes its declared typed values to every node
    that declares an edge to it; one that did not succeed contributes nothing. What a dependent
    node sees is a function of the declared graph and the producer's declared export contract --
    never of a result key that only some operations happen to emit.

Nothing here injects an answer. The generation stub writes the expression a model would write --
names it expects the runtime to have bound -- and states no number of its own, so whether a step
evaluates is decided entirely by the runtime's dependency plumbing. Every asserted figure is
computed by the runtime from a fetcher result.

The reported reproduction is one case among many and is deliberately not the specification: the
subjects, domains, phrasings and obligation counts below were chosen to survive deleting it.
"""
from __future__ import annotations

import json
from dataclasses import replace
from unittest import mock

import pytest

from core.conductor.obligations import ObligationSource
from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.live_quote_contract import LiveQuoteResult
from core.weather_result_contract import WeatherResult
from tests.conductor_product import compose_product

# --- fetcher stand-ins -------------------------------------------------------------------------
#
# Patched at the same seam `tests/test_conductor_multi_intent.py` uses: the network functions, not
# the runner above them, so every layer this file is about is the real one.

_TEMPS = {
    "riga": 19.0,
    "oslo": 11.0,
    "porto": 26.0,
    "reykjavik": 4.0,
}
_CRYPTO = {
    "ethereum": (3100.0, -3.8),
    "solana": (155.0, 2.1),
}
_COMMODITY = {
    "silver": (31.0, 0.4),
    "gold": (2400.0, 0.9),
}


def _weather(location, **_kwargs):
    key = str(location or "").strip().casefold()
    if key not in _TEMPS:
        return None
    return WeatherResult(
        location=key,
        place_label=key.title(),
        condition="Clear",
        temperature_c=_TEMPS[key],
        feels_like_c=_TEMPS[key],
        humidity_pct=50.0,
        wind_kmph=10.0,
        observed_at="12:00 PM",
        source_label="wttr.in",
        source_url=f"https://wttr.in/{key}",
    )


def _crypto(coin_ids, **_kwargs):
    out = []
    for coin in coin_ids:
        if coin not in _CRYPTO:
            continue
        value, change = _CRYPTO[coin]
        out.append(
            LiveQuoteResult(
                asset_key=coin,
                asset_name=coin.title(),
                symbol=coin[:3].upper(),
                value=value,
                currency="USD",
                as_of="",
                source_label="stub",
                source_url="https://example.invalid",
                kind="crypto",
                change_percent=change,
            )
        )
    return out


def _commodity(_query, targets, **_kwargs):
    out = []
    for target in targets:
        if target.asset_key not in _COMMODITY:
            continue
        value, change = _COMMODITY[target.asset_key]
        out.append(
            LiveQuoteResult(
                asset_key=target.asset_key,
                asset_name=target.asset_name,
                symbol=target.symbol,
                value=value,
                currency="USD",
                as_of="",
                source_label="stub",
                source_url="https://example.invalid",
                kind="commodity",
                change_percent=change,
                unit_label=target.unit_label,
            )
        )
    return out


@pytest.fixture()
def fetchers():
    with (
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather),
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_commodity),
    ):
        yield


class _Generation:
    """A model that proposes structure and never a value.

    It writes one step per requested figure, naming the symbols it expects the runtime to have
    bound from earlier nodes. If the runtime bound them the step evaluates; if not the runtime
    records the expression as ungrounded. Nothing in this class knows any answer, and it states no
    literal that is not already a fact of the message.
    """

    def __init__(self, *steps: tuple[str, str, str]) -> None:
        self._steps = steps
        self.briefings: list[str] = []

    def __call__(self, _system: str, prompt: str) -> str:
        self.briefings.append(prompt)
        return json.dumps(
            {
                "steps": [
                    {"label": label, "expression": expression, "unit": unit}
                    for label, expression, unit in self._steps
                ],
                "cannot_determine": [],
            }
        )


def _clause(request: str, operation: str, depends_on: tuple[int, ...] = ()) -> dict:
    return {"request": request, "operation": operation, "depends_on": list(depends_on)}


def _drive(
    prompt: str,
    *clauses: dict,
    generation: _Generation | None = None,
    propose_semantics=None,
):
    """Plan, run, reduce and compose one turn through the real conductor."""
    plan = plan_conductor_turn(
        prompt,
        ask_model=lambda _s, _p: json.dumps(list(clauses)),
        plan_id="obl",
        propose_semantics=propose_semantics,
    )
    assert plan is not None, "the conductor declined a turn it must claim"
    outcomes = run_conductor_plan(
        plan, context=NodeContext(timeout_s=5.0, run_generation=generation)
    )
    return plan, outcomes, compose_product(plan, outcomes)


def _served_subjects(outcomes, operation: str) -> set[str]:
    return {
        str(outcome.node.arguments.get("entity") or "").casefold()
        for outcome in outcomes
        if outcome.node.operation == operation
    }


def _accounted_for(subject: str, outcomes, composed) -> bool:
    """Whether `subject` reached a terminal obligation state rather than vanishing.

    SATISFIED -- a node whose own ``entity`` is this subject succeeded -- or EXPLICITLY_UNSERVED --
    a node that DECLARES an obligation id carries this subject as its request. Nothing else counts.

    Deliberately mechanical, and the earlier version of this helper is why. It also accepted the
    subject appearing anywhere in the composed text beside a dash, which every one of these turns
    satisfies for free: the clause a subject was dropped FROM still quotes that subject, and it is
    rendered next to the sibling that did run. Three sabotages that removed the floor entirely
    survived against that reading -- the tests stayed green with the guard deleted, which is a
    failure of the proof, not evidence of a repair.

    The composed answer is still required to SHOW the gap, so a node that is reported internally
    and hidden from the reader does not pass either.
    """
    needle = " ".join(str(subject).casefold().split())
    for outcome in outcomes:
        entity = " ".join(str(outcome.node.arguments.get("entity") or "").casefold().split())
        if entity == needle:
            # A node of its own, whether it succeeded or failed. Both are terminal states the
            # reader is shown; only "no node at all" is the absence under repair.
            if outcome.succeeded:
                return True
            return outcome.node.node_id in composed.provenance
        if not outcome.node.obligation_id:
            continue
        if " ".join(str(outcome.node.request_text or "").casefold().split()) != needle:
            continue
        # Declared as an obligation AND visible to the reader.
        return outcome.node.node_id in composed.provenance
    return False


# --- INVARIANT A: an obligation the decomposition declines is reported, never absent ------------


REPORTED = (
    "Search the live web for the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold "
    "in USD. Once you have both live prices, calculate exactly how many ounces of Gold it would "
    "take to buy exactly one Bitcoin today. Present the live prices and final ratio clearly."
)


@pytest.mark.usefixtures("fetchers")
def test_the_reported_turn_does_not_silently_drop_the_second_asset():
    """The human-observed failure. Two assets were asked for; one node was planned.

    At base the plan holds a single `market_quote` node for Bitcoin, Gold appears in no node,
    no outcome and no gap line, and `compose_answer` still reports `complete=True`.
    """
    generation = _Generation(("gold per bitcoin", "bitcoin_price / gold_price", "oz"))
    _plan, outcomes, composed = _drive(
        REPORTED,
        _clause(
            "the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold in USD",
            "market_quote",
        ),
        _clause(
            "calculate exactly how many ounces of Gold it would take to buy exactly one "
            "Bitcoin today",
            "quantitative_reasoning",
            (0,),
        ),
        generation=generation,
    )
    assert _accounted_for("gold", outcomes, composed), (
        "the second requested asset reached neither a node nor a reported gap -- it is absent, "
        "and the runtime called the turn complete anyway"
    )


@pytest.mark.usefixtures("fetchers")
def test_an_operation_that_carries_its_declined_subjects_forward_is_not_double_reported():
    """NEGATIVE CONTROL. The floor must add nothing where nothing was being dropped.

    `_weather_expand` already produces a node for every place it recognizes, including one no
    observation will come back for, so the place is reported by that node's own failure. A floor
    that also emitted a residue obligation would tell the reader twice, and a rule that fires where
    there is no defect is how a correctness check gets switched off.
    """
    generation = _Generation()
    _plan, outcomes, composed = _drive(
        "Give me the current weather in Riga, Oslo and Wakanda, and work out how many degrees "
        "warmer Riga is than Oslo.",
        _clause("the current weather in Riga, Oslo and Wakanda", "weather_lookup"),
        _clause("work out how many degrees warmer Riga is than Oslo", "quantitative_reasoning", (0,)),
        generation=generation,
    )
    mentions = [
        outcome
        for outcome in outcomes
        if "wakanda" in str(outcome.node.request_text or "").casefold()
        and outcome.node.operation != "weather_lookup"
    ]
    assert not mentions, (
        f"the floor invented a residue obligation for a subject the operation already carried "
        f"forward: {[m.node.node_id for m in mentions]}"
    )
    assert _accounted_for("wakanda", outcomes, composed), (
        "the place is reported by the failing node it already had"
    )


@pytest.mark.usefixtures("fetchers")
def test_the_residue_rule_is_not_market_shaped():
    """DOMAIN DISTANCE. The mechanism is the registry contract, not anything about assets.

    A synthetic operation in a domain this runtime has never had: it NAMES three parts and can
    SERVE only the ones it stocks. Nothing here shares a table, a recognizer or a word with the
    reproduction, so a repair that only worked for markets fails this outright.
    """
    from core.conductor.capabilities import OperationCapability, OperationEffect
    from core.conductor.registry import (
        OperationSpec,
        register_operation,
        unregister_operation,
    )
    from core.turn_ir import ClauseKind

    catalogue = {"flange": 4.0, "grommet": 9.0}

    def _names(clause: str) -> list[str]:
        return [part for part in ("flange", "grommet", "trunnion") if part in clause.casefold()]

    def _expand(clause: str) -> list[dict]:
        return [{"entity": part} for part in _names(clause) if part in catalogue]

    register_operation(
        OperationSpec(
            name="parts_lookup",
            description="stock level for one or more named parts",
            expand_arguments=_expand,
            run=lambda node, _ctx: {"stock": catalogue[str(node.arguments["entity"])]},
            render=lambda node, result: f"{node.arguments['entity']}: {result['stock']} in stock",
            required_result_fields=("stock",),
            exported_value_fields=("stock",),
            subjects_named=_names,
            capability=OperationCapability(
                effect=OperationEffect.WORKSPACE_EVIDENCE,
                domain="parts",
                accepted_kinds=frozenset({ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}),
            ),
        ),
        replace=True,
    )
    try:
        _plan, outcomes, composed = _drive(
            "Check the stock level of the flange, the grommet and the trunnion, and add up how "
            "many units that is in total.",
            _clause("the stock level of the flange, the grommet and the trunnion", "parts_lookup"),
            _clause("add up how many units that is in total", "quantitative_reasoning", (0,)),
            generation=_Generation(("total units", "flange_stock + grommet_stock", "units")),
        )
    finally:
        unregister_operation("parts_lookup")

    assert _accounted_for("trunnion", outcomes, composed), (
        "the part the operation named but could not serve vanished from the turn"
    )
    # This operation declares no `realize_subject`, so the part it named and could not serve comes
    # to rest in CAPABILITY_UNAVAILABLE -- and the total asked over three parts must not be answered
    # from two of them.
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded, (
        f"the dependent answered {derived.result!r} while a required part was unavailable"
    )


@pytest.mark.usefixtures("fetchers")
def test_the_obligation_set_is_not_whatever_the_decomposition_emitted():
    """S3 as a property. The runtime holds an obligation the decomposition did not serve.

    Unconditional on purpose. Asserting this only when some other check already failed makes the
    test vacuous exactly when the guard is gone, which is when it is needed.
    """
    plan, _outcomes, _composed = _drive(
        REPORTED,
        _clause(
            "the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold in USD",
            "market_quote",
        ),
        _clause(
            "calculate exactly how many ounces of Gold it would take to buy exactly one "
            "Bitcoin today",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("gold per bitcoin", "bitcoin_price / gold_price", "oz")),
    )
    dropped = [
        item.text.casefold()
        for item in plan.obligations.obligations
        if item.source is ObligationSource.SUBJECT_RESIDUE
    ]
    assert "gold" in dropped, (
        f"the runtime holds no obligation for the asset the decomposition dropped; its obligation "
        f"set is {[o.text for o in plan.obligations.obligations]!r}"
    )


def test_the_obligation_floor_is_no_longer_a_second_shipping_authority():
    """S2's descendant. The obligation layer describes the PLAN; it does not decide the PRODUCT.

    A stranded obligation carried by no node used to force `complete` False, which made
    `CanonicalObligations` one of four rival verdicts over the same turn. Widening it now changes
    nothing about the decision, because the decision is computed from realizations and node
    outcomes and reads no obligation at all.

    The property that obligation exists to protect -- a request the runtime cannot account for must
    not ship as complete -- is not lost. It moved to the realization ledger, where the very next
    test forces the same shape and gets the refusal.
    """
    from core.conductor.obligations import CanonicalObligations, Obligation

    plan, outcomes, _composed = _drive(
        "Check the stock levels and the current price of Ethereum.",
        _clause("the current price of Ethereum", "market_quote"),
        _clause("Check the stock levels", "factual_explanation"),
    )
    before = compose_product(plan, outcomes).decision.disposition
    stranded = Obligation(
        obligation_id="stranded:obligation",
        text="something the user asked for that no node carries",
        source=ObligationSource.CLAUSE_RESIDUE,
    )
    widened = replace(
        plan,
        obligations=CanonicalObligations(
            request=plan.original_request,
            obligations=(*plan.obligations.obligations, stranded),
        ),
    )
    assert compose_product(widened, outcomes).decision.disposition is before


def test_a_requirement_the_runtime_cannot_account_for_still_refuses_to_ship():
    """The same property, at the boundary that now owns it.

    A realization bound to a node the report never accounted for is a missing REDUCTION. It is not
    a reportable failure and it is not a complete turn -- it is broken accounting, and the turn is
    claimed and refused rather than handed to another lane.
    """
    from core.conductor.product_decision import (
        ConductorClaim,
        ExecutionReport,
        ProductDisposition,
        reduce_execution_report,
    )

    plan, outcomes, _composed = _drive(
        "Check the stock levels and the current price of Ethereum.",
        _clause("the current price of Ethereum", "market_quote"),
        _clause("Check the stock levels", "factual_explanation"),
    )
    decision = reduce_execution_report(
        ExecutionReport(
            bound_plan=plan.bound_plan,
            node_outcomes=tuple(outcomes)[1:],
            planned_node_ids=tuple(node.node_id for node in plan.nodes),
        )
    )
    assert decision.disposition is ProductDisposition.INTEGRITY_FAILURE
    assert decision.claim is ConductorClaim.CLAIMED_INTEGRITY_FAILURE
    assert any(c.startswith("missing_node_outcome:") for c in decision.integrity_codes)


@pytest.mark.usefixtures("fetchers")
def test_a_dependent_node_computes_from_its_dependencies_live_values():
    """The dependent step evaluates because the runtime bound its dependencies' outputs.

    Fresh domain and fresh arithmetic: two crypto quotes, and a step that divides one by the
    other. The generation stub supplies only the shape `ethereum_price / solana_price`; if the
    scheduler did not project the two successful quotes into this node's symbols, the runtime
    reports the expression as ungrounded and no value exists to assert.
    """
    generation = _Generation(("solana per ethereum", "ethereum_price / solana_price", ""))
    _plan, outcomes, _composed = _drive(
        "Get the live prices of Ethereum and Solana in USD, then work out how many Solana one "
        "Ethereum buys.",
        _clause("the live prices of Ethereum and Solana in USD", "market_quote"),
        _clause("work out how many Solana one Ethereum buys", "quantitative_reasoning", (0,)),
        generation=generation,
    )
    derived = next(
        outcome
        for outcome in outcomes
        if outcome.node.operation == "quantitative_reasoning"
    )
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"], (
        f"the dependent node produced no evaluated step; it reported "
        f"{derived.result.get('cannot_determine')!r} while its dependencies succeeded"
    )
    value = derived.result["steps"][0]["value"]
    assert value == pytest.approx(_CRYPTO["ethereum"][0] / _CRYPTO["solana"][0]), (
        "the computed value is not the quotient of the two fetched prices"
    )


@pytest.mark.usefixtures("fetchers")
def test_a_dependent_node_sees_a_live_observation_from_another_domain():
    """The same projection, over weather rather than markets, so the contract is not price-shaped."""
    generation = _Generation(("degrees warmer", "riga_temperature_c - oslo_temperature_c", "C"))
    _plan, outcomes, _composed = _drive(
        "Check the current temperature in Riga and Oslo, then calculate how many degrees warmer "
        "Riga is than Oslo.",
        _clause("the current temperature in Riga and Oslo", "weather_lookup"),
        _clause("calculate how many degrees warmer Riga is than Oslo", "quantitative_reasoning", (0,)),
        generation=generation,
    )
    derived = next(
        outcome for outcome in outcomes if outcome.node.operation == "quantitative_reasoning"
    )
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"], (
        f"the dependent node produced no evaluated step; it reported "
        f"{derived.result.get('cannot_determine')!r} while its dependencies succeeded"
    )
    assert derived.result["steps"][0]["value"] == pytest.approx(
        _TEMPS["riga"] - _TEMPS["oslo"]
    )


@pytest.mark.usefixtures("fetchers")
def test_the_briefing_carries_the_dependencies_values():
    """What the dependent node is SHOWN, asserted directly rather than through its output.

    A step can evaluate for the wrong reason; the briefing is the seam the invariant is about.
    """
    generation = _Generation(("solana per ethereum", "ethereum_price / solana_price", ""))
    _drive(
        "Get the live prices of Ethereum and Solana in USD, then work out how many Solana one "
        "Ethereum buys.",
        _clause("the live prices of Ethereum and Solana in USD", "market_quote"),
        _clause("work out how many Solana one Ethereum buys", "quantitative_reasoning", (0,)),
        generation=generation,
    )
    assert generation.briefings, "the dependent node never reached the generation seam"
    briefing = generation.briefings[0]
    assert str(_CRYPTO["ethereum"][0]) in briefing, (
        "a dependency succeeded and its value never reached the dependent node's briefing"
    )
    assert str(_CRYPTO["solana"][0]) in briefing
