"""The currency story: a message whose facts live in one sentence and whose questions live in six.

THE MEASURED DEFECT. On the shipped build this message:

    A traveler says: "I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home
    with 2,100 kr." Explain whether the exchange was good or bad compared with the normal exchange
    rate. Calculate how much money they effectively spent during the trip, both in kr and USD. ...

produced a seven-node plan in which every single node failed closed. Reproduced exactly, before the
fix, by `test_the_shipped_seven_node_failure_is_reproduced_when_the_context_is_withheld`:

    conductor:unresolved:0  comparison needs earlier results and none were named
    conductor:unresolved:1  calculation found nothing to act on in this request
    conductor:unresolved:2  comparison found nothing to act on in this request
    conductor:unresolved:3  no registered operation named 'explanation'
    conductor:unresolved:4  calculation found nothing to act on in this request
    conductor:unresolved:5  no registered operation named 'suggestion'
    conductor:unresolved:6  no registered operation named 'assumptions'

Every one of those is the same cause. `expand_arguments` is handed the node's own clause and
nothing else -- correctly, and that scoping is what makes `location='price of bitcoin'` unreachable
-- but "Calculate how much money they effectively spent during the trip" contains no numbers. They
are all in the sentence before it. The arithmetic evaluator looked at its clause, found nothing,
and the runtime reported that honestly seven times over.

WHAT IS INJECTED AND WHAT IS NOT. Planner replies and node generations are injected, because these
tests exercise the RUNTIME's handling of a plan and of a model's proposed expressions. What is
never injected is a VALUE: every number asserted below -- 6.8548, 6,400, 933.65, 7,168, 768 -- is
computed by `evaluate_grounded_expression` from the message's own figures. A generation double that
tried to supply one would be refused by the same code path a real model's would be, and
`test_sabotage_*` proves it.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from core.conductor.node import NodeLifecycle
from core.conductor.planner import build_plan_from_clauses, parse_clauses, plan_conductor_turn
from core.conductor.registry import (
    UNRESOLVED_OPERATION,
    NodeContext,
    expand_clause,
    general_operations,
    operation_spec,
)
from core.conductor.scheduler import run_conductor_plan
from core.conductor.shared_context import (
    SharedTurnContext,
    UngroundedExpressionError,
    evaluate_grounded_expression,
    extract_shared_context,
)
from tests.conductor_product import compose_product

# --------------------------------------------------------------------------------------
# The message, its paraphrases and its sloppy forms
# --------------------------------------------------------------------------------------

STORY = (
    "A traveler says: “I exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came "
    "home with 2,100 kr.”"
)

TAIL = (
    " Explain whether the exchange was good or bad compared with the normal exchange rate. "
    "Calculate how much money they effectively spent during the trip, both in kr and USD. "
    "Compare the purchasing power of the two currencies and explain which is stronger. "
    "Explain why kr is used instead of a unique currency symbol and which countries use it. "
    "If they make the same trip next year and the dollar strengthens by 12% against kr, estimate "
    "how much more or less the trip would cost them. Suggest two ways they could reduce "
    "currency-conversion losses. Finally, identify every assumption you had to make to answer the "
    "questions above. If something cannot be determined from the information given, say exactly "
    "what is missing rather than choosing the most likely answer."
)

ORIGINAL = STORY + TAIL

#: (name, prompt, the clause that asks for the effective spend, in that prompt's own words)
#:
#: Ten families, as the QA rule requires: the original, five clean paraphrases, five sloppy or
#: user-style forms. Each carries the same arithmetic and must reach the same computed values, so a
#: fix that only recognises the original's phrasing fails nine of them.
VARIANTS: tuple[tuple[str, str, str], ...] = (
    (
        "original",
        ORIGINAL,
        "Calculate how much money they effectively spent during the trip, both in kr and USD",
    ),
    (
        "paraphrase_converted",
        "A traveler converted 8,500 kr into $1,240, spent $300 while in Singapore, and returned "
        "with 2,100 kr. Work out how much they effectively spent on the trip in both currencies, "
        "and say whether the exchange was good or bad against the normal exchange rate."
        + " If the dollar strengthens by 12% against kr next year, estimate the difference.",
        "Work out how much they effectively spent on the trip in both currencies",
    ),
    (
        "paraphrase_first_person",
        "I changed 8,500 kr and got $1,240 back, then I spent $300 in Singapore and flew home "
        "holding 2,100 kr. How much did I effectively spend, in kr and in dollars? Was that a good "
        "or bad rate compared with the normal exchange rate? What would the same trip cost if the "
        "dollar strengthens by 12%?",
        "How much did I effectively spend, in kr and in dollars",
    ),
    (
        "paraphrase_traded_received",
        "A traveler traded 8,500 kr and received $1,240, then spent $300 in Singapore and came "
        "home with 2,100 kr. Calculate the total they effectively spent, and explain whether the "
        "exchange was good or bad; also estimate the effect of the dollar strengthening by 12%.",
        "Calculate the total they effectively spent",
    ),
    (
        "paraphrase_sek_explicit",
        "A traveler exchanged 8,500 SEK for $1,240, then spent $300 in Singapore and came home "
        "with 2,100 SEK. Calculate how much money they effectively spent during the trip, in SEK "
        "and USD, and say whether the exchange was good or bad compared with the normal rate.",
        "Calculate how much money they effectively spent during the trip, in SEK and USD",
    ),
    (
        "paraphrase_dkk_explicit",
        "A traveler exchanged 8,500 DKK for $1,240, then spent $300 in Singapore and came home "
        "with 2,100 DKK. Calculate how much money they effectively spent during the trip, and "
        "explain whether the exchange was good or bad compared with the normal rate.",
        "Calculate how much money they effectively spent during the trip",
    ),
    (
        "sloppy_bucks",
        "so my mate swapped 8500kr for 1240 bucks, blew 300 bucks in Singapore and got back with "
        "2100kr. how much did he effectively spend, and was that a good or bad rate vs the normal "
        "exchange rate? also what if the dollar strengthens by 12%",
        "how much did he effectively spend",
    ),
    (
        "sloppy_no_commas",
        "exchanged 8500 kr for $1240 then spent $300 in Singapore and came home with 2100 kr "
        "calculate how much they effectively spent in kr and USD and whether the exchange was "
        "good or bad compared with the normal exchange rate",
        "calculate how much they effectively spent in kr and USD",
    ),
    (
        "sloppy_typo_singapore",
        "traveler exchanged 8,500 kr for $1,240 then spent $300 in Singaproe and came home with "
        "2,100 kr. calculate how much they effectively spent, and was the exchange good or bad "
        "compared with the normal exchange rate?",
        "calculate how much they effectively spent",
    ),
    (
        "sloppy_kr_money_thing",
        "he had 8,500 of that kr money thing, turned it into $1,240, spent $300 in Singapore, came "
        "back with 2,100 kr. how much did he effectively spend and was that a good or bad deal "
        "against the normal exchange rate?",
        "how much did he effectively spend",
    ),
)

#: Variants that change the arithmetic rather than the wording. Kept apart because they assert a
#: DIFFERENT number, and folding them into the table above would let a wrong answer hide in a
#: family that expects the same one everywhere.
DOLLAR_WEAKENS = (
    "A traveler exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home with "
    "2,100 kr. If the dollar weakens by 12% against kr next year, estimate how much more or less "
    "the same trip would cost them, and identify every assumption you had to make."
)

ONLY_EFFECTIVE_SPEND = (
    "A traveler exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home with "
    "2,100 kr. Calculate how much money they effectively spent during the trip, in kr and in "
    "dollars, and show the working."
)

ONLY_MISSING_ASSUMPTIONS = (
    "A traveler exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home with "
    "2,100 kr. Do not answer anything else: identify every assumption you would have to make, and "
    "say exactly what cannot be determined from the information given."
)

DOLLAR_MAYBE_SGD = (
    "A traveler exchanged 8,500 SEK for $1,240 at a Singapore counter, then spent $300 in "
    "Singapore and came home with 2,100 SEK. Calculate how much they effectively spent, and "
    "identify every assumption you had to make about which dollar is meant."
)


# --------------------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------------------


def _clause_under_answer(briefing: str) -> str:
    """The one clause a node was asked about, read back out of the briefing it was handed.

    The double dispatches on THIS rather than on the whole prompt, because the briefing carries the
    user's full message by design -- and a double that matched against the whole thing would answer
    every node identically while looking like it was routing. That bug happened; this is the fix.
    """
    marker = "The part of it you are answering:"
    if marker not in briefing:
        return ""
    return briefing.split(marker, 1)[1].strip().splitlines()[0].strip()


#: What a model proposes for a clause: expressions over `fact_N` and `step_N`, never values. The
#: runtime computes every number. `_generation` picks by clause cue, so one double serves every
#: variant's phrasing without any of them being matched exactly.
_EFFECTIVE_SPEND_STEPS = [
    {"label": "spent from the kr balance", "expression": "fact_1 - fact_4", "unit": "kr"},
    {"label": "implied rate", "expression": "fact_1 / fact_2", "unit": "kr per $"},
    {"label": "that in dollars", "expression": "step_1 / step_2", "unit": "$"},
    {"label": "beyond the stated Singapore spend", "expression": "step_3 - fact_3", "unit": "$"},
]

_STRENGTHENS_STEPS = [
    {"label": "implied rate", "expression": "fact_1 / fact_2", "unit": "kr per $"},
    {"label": "rate after the change", "expression": "step_1 * 1.12", "unit": "kr per $"},
    {"label": "same trip next year", "expression": "(fact_1 - fact_4) / step_1 * step_2", "unit": "kr"},
    {"label": "difference", "expression": "step_3 - (fact_1 - fact_4)", "unit": "kr"},
]

_WEAKENS_STEPS = [
    {"label": "implied rate", "expression": "fact_1 / fact_2", "unit": "kr per $"},
    {"label": "rate after the change", "expression": "step_1 * 0.88", "unit": "kr per $"},
    {"label": "same trip next year", "expression": "(fact_1 - fact_4) / step_1 * step_2", "unit": "kr"},
    {"label": "difference", "expression": "step_3 - (fact_1 - fact_4)", "unit": "kr"},
]


def _generation(system_prompt: str, briefing: str) -> str:
    clause = _clause_under_answer(briefing).lower()
    if "set up the arithmetic" in system_prompt:
        if "effectively spend" in clause or "effectively spent" in clause or "total they" in clause:
            return json.dumps({"steps": _EFFECTIVE_SPEND_STEPS})
        if "weaken" in clause:
            return json.dumps({"steps": _WEAKENS_STEPS})
        if "strengthen" in clause or "12%" in clause or "next year" in clause:
            return json.dumps({"steps": _STRENGTHENS_STEPS})
        if "purchasing power" in clause:
            return json.dumps({
                "steps": [{"label": "one kr in dollars", "expression": "fact_2 / fact_1", "unit": "$"}]
            })
        if "good or bad" in clause or "better or worse" in clause or "good or bad deal" in clause:
            return json.dumps({
                "steps": [{"label": "implied rate", "expression": "fact_1 / fact_2", "unit": "kr per $"}],
                "cannot_determine": [
                    "whether that implied rate beat the normal rate, which the message does not state"
                ],
            })
        return json.dumps({"steps": []})
    if "kr" in clause and ("countries" in clause or "symbol" in clause):
        return (
            "'kr' abbreviates krona or krone rather than being a dedicated symbol, and it is used "
            "by Sweden, Denmark, Norway and Iceland — which is exactly why it does not identify a "
            "single currency on its own."
        )
    return (
        "Change money at a bank or broker quoting the mid-market rate instead of an airport "
        "counter, and always pay in the local currency rather than accepting the till's own "
        "conversion."
    )


def _context(**overrides: Any) -> NodeContext:
    base = {"timeout_s": 5.0, "run_generation": _generation}
    base.update(overrides)
    return NodeContext(**base)


# The full seven-clause plan a planner produces for the original message. Written with the
# operation names a model actually chose on the live drive -- including the three the registry has
# never served -- because a plan of already-correct operation names would not exercise the
# resolution this fix is about.
SEVEN_CLAUSES = json.dumps([
    {"request": "Explain whether the exchange was good or bad compared with the normal exchange rate",
     "operation": "comparison", "depends_on": []},
    {"request": "Calculate how much money they effectively spent during the trip, both in kr and USD",
     "operation": "calculation", "depends_on": []},
    {"request": "Compare the purchasing power of the two currencies and explain which is stronger",
     "operation": "comparison", "depends_on": [1]},
    {"request": "Explain why kr is used instead of a unique currency symbol and which countries use it",
     "operation": "explanation", "depends_on": []},
    {"request": "estimate how much more or less the trip would cost them if the dollar strengthens "
                "by 12% against kr", "operation": "calculation", "depends_on": [1]},
    {"request": "Suggest two ways they could reduce currency-conversion losses",
     "operation": "suggestion", "depends_on": []},
    {"request": "identify every assumption you had to make to answer the questions above",
     "operation": "assumptions", "depends_on": []},
])


def _run_seven(prompt: str = ORIGINAL, *, context: NodeContext | None = None) -> tuple:
    plan = plan_conductor_turn(prompt, ask_model=lambda _s, _p: SEVEN_CLAUSES, plan_id="c")
    assert plan is not None, "the conductor declined the turn this whole file is about"
    outcomes = run_conductor_plan(plan, context=context or _context())
    return plan, outcomes, compose_product(plan, outcomes)


# --------------------------------------------------------------------------------------
# The defect, reproduced, and repaired
# --------------------------------------------------------------------------------------


def test_the_shipped_seven_node_failure_is_reproduced_when_the_context_is_withheld() -> None:
    """The control. Without it, the repair below proves only that some code produced a number.

    Withholding the shared context is the shipped build exactly: every operation sees its clause and
    nothing more. All seven clauses fail closed, and the two the planner sent to `calculation` fail
    with the message the defect report quotes verbatim.
    """
    clauses = parse_clauses(SEVEN_CLAUSES)
    plan = build_plan_from_clauses(
        clauses, original_request=ORIGINAL, plan_id="c", shared_context=SharedTurnContext()
    )

    assert len(plan.nodes) == 7
    assert all(node.operation == UNRESOLVED_OPERATION for node in plan.nodes)
    reasons = [node.unresolved_reason for node in plan.nodes]
    assert reasons.count("calculation found nothing to act on in this request") == 2
    assert "comparison needs earlier results and none were named" in reasons
    assert "no registered operation named 'explanation'" in reasons
    assert "no registered operation named 'suggestion'" in reasons
    assert "no registered operation named 'assumptions'" in reasons

    # And this is why the user saw a cloud model answer the message instead: a plan of nothing but
    # unresolved nodes is declined whole, so the turn falls through to the ordinary lane.
    assert plan_conductor_turn(
        ORIGINAL,
        ask_model=lambda _s, _p: SEVEN_CLAUSES,
        plan_id="c",
    ) is not None, "the repaired planner must now claim it"


def test_all_seven_clauses_are_served_and_none_reports_nothing_to_act_on() -> None:
    plan, outcomes, composed = _run_seven()

    assert len(plan.nodes) == 7
    assert not any(node.operation == UNRESOLVED_OPERATION for node in plan.nodes)
    assert all(outcome.succeeded for outcome in outcomes), [
        (o.node.node_id, o.failure_reason) for o in outcomes if not o.succeeded
    ]
    assert composed.complete and composed.unserved_count == 0
    assert "found nothing to act on" not in composed.text


def test_the_answer_carries_every_value_the_story_implies() -> None:
    """Each of these is computed by the runtime from the message's own figures, never phrased."""
    _plan, _outcomes, composed = _run_seven()

    assert "6.8548" in composed.text, "implied rate 8500/1240"
    assert "6,400" in composed.text, "kr spent from the balance"
    assert "933.6" in composed.text, "that in dollars"
    assert "633.6" in composed.text, "beyond the stated $300 Singapore spend"
    assert "0.1459" in composed.text, "one kr in dollars"
    assert "7.6774" in composed.text, "rate after a 12% stronger dollar"
    assert "7,168" in composed.text, "the same trip next year, in kr"
    assert "768" in composed.text, "the increase, in kr"


def test_the_answer_states_what_cannot_be_determined_instead_of_choosing() -> None:
    _plan, _outcomes, composed = _run_seven()
    lowered = composed.text.lower()

    assert "cannot be determined from what you gave" in lowered
    # The three structural gaps, each named for what it is.
    assert "which currency 'kr' means" in lowered
    assert "dkk, isk, nok, sek" in lowered
    assert "the normal exchange rate itself" in lowered
    assert "no fee, spread or commission" in lowered
    # And the thing a runtime that guessed would have said instead.
    assert "swedish krona" not in lowered
    assert "probably" not in lowered and "most likely" not in lowered


def test_arithmetic_nodes_are_handed_the_numbers_and_record_which_ones() -> None:
    """'Subnodes keep source facts', asserted on the node rather than on the prose it produced."""
    plan, _outcomes, _composed = _run_seven()
    quantitative = [n for n in plan.nodes if n.operation == "quantitative_reasoning"]
    assert len(quantitative) == 4

    for node in quantitative:
        values = dict(node.arguments.get("fact_values") or {})
        assert sorted(values.values()) == [12.0, 300.0, 1240.0, 2100.0, 8500.0], node.node_id
        assert node.arguments["fact_labels"] == ["fact_1", "fact_2", "fact_3", "fact_4", "fact_5"]


def test_every_node_runs_with_the_shared_context_available() -> None:
    """The plumbing itself: what the scheduler puts in front of each node, observed at the seam."""
    plan, _outcomes, _composed = _run_seven()
    seen: list[SharedTurnContext | None] = []

    def _probe(system_prompt: str, briefing: str) -> str:
        return _generation(system_prompt, briefing)

    original_run = operation_spec("factual_explanation").run

    def _capture(node, ctx):
        seen.append(ctx.shared_context)
        return original_run(node, ctx)

    from core.conductor.registry import register_operation

    spec = operation_spec("factual_explanation")
    register_operation(replace(spec, run=_capture), replace=True)
    try:
        outcomes = run_conductor_plan(plan, context=_context(run_generation=_probe))
    finally:
        register_operation(spec, replace=True)

    assert seen, "no explanation node ran"
    for context in seen:
        assert context is not None
        assert len(context.facts) == 5
        assert context.unresolved_ambiguities
    assert all(o.succeeded for o in outcomes)


# --------------------------------------------------------------------------------------
# The semantic family -- ten wordings, one arithmetic
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "prompt", "spend_clause"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_variant_extracts_the_same_five_numbers(name, prompt, spend_clause) -> None:
    context = extract_shared_context(prompt)
    values = sorted(fact.value for fact in context.facts)
    expected = [300.0, 1240.0, 2100.0, 8500.0]
    if "12%" in prompt:
        expected = sorted([*expected, 12.0])
    assert values == expected, f"{name}: {[f.raw for f in context.facts]}"


@pytest.mark.parametrize(("name", "prompt", "spend_clause"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_variant_routes_its_spend_clause_to_the_arithmetic_that_can_serve_it(
    name, prompt, spend_clause
) -> None:
    """No wording reaches 'found nothing to act on'. This is the anti-overfit assertion.

    A fix keyed to the original's phrasing passes the first row and fails the other nine.
    """
    context = extract_shared_context(prompt)
    spec = operation_spec("quantitative_reasoning")
    expanded = expand_clause(spec, spend_clause, context)
    assert expanded, f"{name}: {spend_clause!r} expanded to nothing"
    assert sorted(dict(expanded[0]["fact_values"]).values())[-1] == 8500.0


@pytest.mark.parametrize(("name", "prompt", "spend_clause"), VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_variant_computes_the_same_effective_spend(name, prompt, spend_clause) -> None:
    """6,400 kr and ~$933.65, from ten different sentences. Computed, never matched."""
    context = extract_shared_context(prompt)
    spec = operation_spec("quantitative_reasoning")
    node_arguments = expand_clause(spec, spend_clause, context)[0]

    from core.conductor.node import ConductorNode

    node = ConductorNode(
        node_id="v", operation="quantitative_reasoning", request_text=spend_clause,
        arguments=node_arguments,
    )
    result = spec.run(node, _context(shared_context=context))
    values = result["values"]
    assert values["spent from the kr balance"] == pytest.approx(6400.0)
    assert values["implied rate"] == pytest.approx(8500 / 1240)
    assert values["that in dollars"] == pytest.approx(933.647, abs=0.01)
    rendered = spec.render(node, result)
    assert "6,400" in rendered and "933.6" in rendered
    assert "found nothing to act on" not in rendered


@pytest.mark.parametrize(
    ("name", "prompt", "expect_kr_unresolved"),
    [
        ("kr_alone_is_ambiguous", ORIGINAL, True),
        ("sek_pins_it", VARIANTS[4][1], False),
        ("dkk_pins_it", VARIANTS[5][1], False),
        ("kr_money_thing_is_still_ambiguous", VARIANTS[9][1], True),
        ("no_commas_is_still_ambiguous", VARIANTS[7][1], True),
    ],
)
def test_an_ambiguous_currency_is_never_silently_chosen(name, prompt, expect_kr_unresolved) -> None:
    context = extract_shared_context(prompt)
    kr = [a for a in context.ambiguities if a.token == "kr"]
    if expect_kr_unresolved:
        assert kr and kr[0].unresolved, name
        assert set(kr[0].candidates) == {"DKK", "ISK", "NOK", "SEK"}
        assert any(item.kind == "ambiguous_unit" for item in context.missing)
    else:
        # Named explicitly, so there is nothing to report -- and nothing was invented either way.
        assert not any(a.token == "kr" and a.unresolved for a in context.ambiguities), name
        assert not any("'kr' means" in item.statement for item in context.missing)


def test_the_dollar_is_ambiguous_until_the_message_names_which_dollar() -> None:
    """Singapore being mentioned does not make the dollar Singaporean. An ISO code does."""
    ambiguous = extract_shared_context(DOLLAR_MAYBE_SGD)
    dollar = next(a for a in ambiguous.ambiguities if a.token == "$")
    assert dollar.unresolved
    assert "SGD" in dollar.candidates and "USD" in dollar.candidates
    assert any("'$' means" in item.statement for item in ambiguous.missing)

    # The original says "both in kr and USD", which does name it.
    resolved = next(a for a in extract_shared_context(ORIGINAL).ambiguities if a.token == "$")
    assert resolved.resolved == "USD"
    assert not any("'$' means" in item.statement for item in extract_shared_context(ORIGINAL).missing)


def test_a_weakening_dollar_moves_the_estimate_the_other_way() -> None:
    """Same story, opposite direction. A fix that hardcoded 1.12 answers this one wrong."""
    context = extract_shared_context(DOLLAR_WEAKENS)
    spec = operation_spec("quantitative_reasoning")
    clause = "estimate how much more or less the same trip would cost them if the dollar weakens by 12%"
    arguments = expand_clause(spec, clause, context)[0]

    from core.conductor.node import ConductorNode

    node = ConductorNode(node_id="w", operation="quantitative_reasoning", request_text=clause,
                         arguments=arguments)
    values = spec.run(node, _context(shared_context=context))["values"]
    assert values["rate after the change"] == pytest.approx((8500 / 1240) * 0.88)
    # Fewer kr per dollar means the same dollar trip costs LESS in kr, not more.
    assert values["difference"] < 0
    assert values["same trip next year"] < 6400.0


def test_a_message_asking_only_for_the_spend_still_gets_the_spend() -> None:
    reply = json.dumps([
        {"request": "Calculate how much money they effectively spent during the trip, in kr and in dollars",
         "operation": "calculation", "depends_on": []},
        {"request": "show the working", "operation": "explanation", "depends_on": [0]},
    ])
    plan = plan_conductor_turn(ONLY_EFFECTIVE_SPEND, ask_model=lambda _s, _p: reply, plan_id="s")
    assert plan is not None
    composed = compose_product(plan, run_conductor_plan(plan, context=_context()))
    assert "6,400" in composed.text and "933.6" in composed.text


def test_a_message_asking_only_what_is_missing_gets_the_gaps_and_no_arithmetic() -> None:
    reply = json.dumps([
        {"request": "identify every assumption you would have to make", "operation": "assumptions",
         "depends_on": []},
        {"request": "say exactly what cannot be determined from the information given",
         "operation": "assumptions", "depends_on": []},
    ])
    plan = plan_conductor_turn(ONLY_MISSING_ASSUMPTIONS, ask_model=lambda _s, _p: reply, plan_id="m")
    assert plan is not None
    assert {node.operation for node in plan.nodes} == {"missing_information"}
    composed = compose_product(plan, run_conductor_plan(plan, context=_context()))
    assert "which currency 'kr' means" in composed.text
    assert "no fee, spread or commission" in composed.text


# --------------------------------------------------------------------------------------
# Negative controls -- what must NOT reach any of this
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt",
    [
        "what is the current SEK to USD rate now",
        "latest USD/DKK rate today",
        "what is USD?",
        "8500 - 2100",
        "write a README about currency exchange",
        "explain why USD moved this week",
    ],
)
def test_a_simple_question_never_reaches_the_conductor_or_costs_a_model_call(prompt) -> None:
    calls: list[str] = []

    def _spy(system_prompt: str, text: str) -> str:
        calls.append(text)
        return SEVEN_CLAUSES

    assert plan_conductor_turn(prompt, ask_model=_spy, plan_id="n") is None
    assert calls == [], f"{prompt!r} spent a planner call it should not have"


def test_the_general_operations_decline_a_message_with_no_shared_story() -> None:
    """The control for the fallback chain: it must not become a catch-all.

    "What is 137 x 29? … weather for Kaunas and Tallinn" has numbers, an explanation clause and a
    comparison clause, and NONE of the three general operations may take any of them -- there is no
    ambiguity, nothing undetermined, and the specific adapters already serve it.
    """
    prompt = (
        "What is 137 x 29? Explain the calculation briefly. Also get the current weather for "
        "Kaunas and Tallinn and tell me which city is warmer."
    )
    context = extract_shared_context(prompt)
    assert not context.unresolved_ambiguities and not context.missing

    for clause in (
        "Explain the calculation briefly",
        "What is 137 x 29?",
        "which city is warmer",
        "get the current weather for Kaunas and Tallinn",
    ):
        for spec in general_operations():
            assert expand_clause(spec, clause, context) == [], f"{spec.name} took {clause!r}"


def test_a_message_with_no_figures_is_left_to_the_lanes_that_already_answer_it() -> None:
    """The boundary on `factual_explanation`, which is the only general operation near a catch-all.

    "the average price" names a benchmark nothing supplies, so the gap rule fires -- and it still
    must not claim the turn, because a message with no figures in it has no shared numeric story to
    lose. Without this gate the conductor would start taking ordinary two-part questions and
    answering them with two model calls where the existing lane uses one.
    """
    prompt = "what is the average price of a used estate car and where would I go to buy one"
    context = extract_shared_context(prompt)
    assert any(item.kind == "external_benchmark" for item in context.missing)  # the rule did fire
    assert not context.has_numbers

    for spec in general_operations():
        for clause in ("what is the average price of a used estate car", "where would I go to buy one"):
            assert expand_clause(spec, clause, context) == [], f"{spec.name} took {clause!r}"


def test_a_clause_that_asks_for_no_figure_is_not_sent_to_arithmetic() -> None:
    """Money in the message is not a reason to do arithmetic to a request for advice."""
    context = extract_shared_context(ORIGINAL)
    spec = operation_spec("quantitative_reasoning")
    for clause in (
        "Suggest two ways they could reduce currency-conversion losses",
        "Explain why kr is used instead of a unique currency symbol and which countries use it",
    ):
        assert expand_clause(spec, clause, context) == [], clause


# --------------------------------------------------------------------------------------
# Adversarial near-misses
# --------------------------------------------------------------------------------------


def test_a_benchmark_the_message_actually_supplies_is_not_reported_as_missing() -> None:
    """The near-miss for the benchmark rule. It must fire on a GAP, not on the words 'normal rate'."""
    supplied = (
        "A traveler exchanged 8,500 kr for $1,240 on a day when the normal rate was 6.5 kr per "
        "dollar, then spent $300 in Singapore and came home with 2,100 kr. Was the exchange good "
        "or bad, and how much did they effectively spend?"
    )
    assert not any(item.kind == "external_benchmark" for item in extract_shared_context(supplied).missing)
    # Control: the SAME sentence still naming the normal rate, with only its value taken away. The
    # words are unchanged, so what the rule reacts to is the missing figure and not the phrase.
    withheld = supplied.replace("was 6.5 kr per dollar", "applied")
    assert "normal rate" in withheld
    assert any(item.kind == "external_benchmark" for item in extract_shared_context(withheld).missing)


def test_a_stated_fee_is_not_reported_as_an_unstated_one() -> None:
    """The near-miss for the conversion-cost rule."""
    with_fee = (
        "A traveler exchanged 8,500 kr for $1,240 after a 40 kr commission, then spent $300 in "
        "Singapore and came home with 2,100 kr. How much did they effectively spend?"
    )
    assert not any(item.kind == "conversion_cost" for item in extract_shared_context(with_fee).missing)
    without = with_fee.replace(" after a 40 kr commission", "")
    assert any(item.kind == "conversion_cost" for item in extract_shared_context(without).missing)


def test_two_disagreeing_currency_pins_leave_the_unit_less_determined_not_more() -> None:
    both = (
        "A traveler exchanged 8,500 kr for $1,240, then spent $300 in Singapore and came home with "
        "2,100 kr. Assume Swedish and Danish practice both apply. How much did they effectively spend?"
    )
    kr = next(a for a in extract_shared_context(both).ambiguities if a.token == "kr")
    assert kr.unresolved
    assert "SEK and DKK" in kr.why or "DKK and SEK" in kr.why


# --------------------------------------------------------------------------------------
# SABOTAGE -- every load-bearing invariant, each failing the assertion that NAMES it
# --------------------------------------------------------------------------------------


def test_sabotage_withholding_the_shared_context_brings_the_seven_node_failure_back() -> None:
    """Invariant: the planner extracts shared context and hands it to the operations that want it.

    Removing exactly that -- and nothing else -- restores the shipped defect byte for byte,
    including the "found nothing to act on" wording. The repair is therefore what produced the
    answer above, not something else that happened to run alongside it.
    """
    clauses = parse_clauses(SEVEN_CLAUSES)
    live = build_plan_from_clauses(clauses, original_request=ORIGINAL, plan_id="c")
    assert not any(n.operation == UNRESOLVED_OPERATION for n in live.nodes)  # control

    sabotaged = build_plan_from_clauses(
        clauses, original_request=ORIGINAL, plan_id="c", shared_context=SharedTurnContext()
    )
    assert all(n.operation == UNRESOLVED_OPERATION for n in sabotaged.nodes)
    assert [n.unresolved_reason for n in sabotaged.nodes].count(
        "calculation found nothing to act on in this request"
    ) == 2


def test_sabotage_removing_the_general_operations_makes_the_clauses_unresolved_again() -> None:
    """Invariant: the fallback chain is what serves a clause no named operation could."""
    from core.conductor.registry import register_operation, unregister_operation

    saved = [operation_spec(name) for name in
             ("quantitative_reasoning", "missing_information", "factual_explanation")]
    try:
        for spec in saved:
            unregister_operation(spec.name)
        plan = build_plan_from_clauses(
            parse_clauses(SEVEN_CLAUSES), original_request=ORIGINAL, plan_id="c"
        )
        assert all(n.operation == UNRESOLVED_OPERATION for n in plan.nodes)
    finally:
        for spec in saved:
            register_operation(spec, replace=True)


def test_sabotage_an_ungrounded_literal_is_refused_rather_than_computed() -> None:
    """Invariant: a model may propose which numbers to combine; it may not supply one.

    `6.8548` is the right answer to the previous step. Written as a LITERAL it is a number nobody
    established, and the runtime cannot tell a correct one from a fabricated one -- so it refuses
    both and reports the step as undetermined.
    """
    context = extract_shared_context(ORIGINAL)
    facts = context.facts
    assert evaluate_grounded_expression("fact_1 / fact_2", facts=facts) == pytest.approx(6.8548, abs=1e-4)

    for smuggled in ("6.8548 * fact_4", "9.2", "fact_1 / 6.5", "fact_9 + fact_1"):
        with pytest.raises(UngroundedExpressionError):
            evaluate_grounded_expression(smuggled, facts=facts)

    # And through the node, where it becomes a reported gap instead of a silent omission.
    spec = operation_spec("quantitative_reasoning")
    clause = "Calculate how much money they effectively spent during the trip, both in kr and USD"
    arguments = expand_clause(spec, clause, context)[0]

    from core.conductor.node import ConductorNode

    node = ConductorNode(node_id="g", operation="quantitative_reasoning", request_text=clause,
                         arguments=arguments)

    def _smuggling(_system: str, _briefing: str) -> str:
        return json.dumps({"steps": [
            {"label": "spent from the kr balance", "expression": "fact_1 - fact_4", "unit": "kr"},
            {"label": "that in dollars", "expression": "(fact_1 - fact_4) / 6.8548", "unit": "$"},
        ]})

    result = spec.run(node, _context(shared_context=context, run_generation=_smuggling))
    assert list(result["values"]) == ["spent from the kr balance"]
    assert any("not a number this message established" in item for item in result["cannot_determine"])
    assert "933" not in spec.render(node, result)


def test_sabotage_an_explanation_that_states_an_invented_number_is_refused_whole() -> None:
    """Invariant: prose from a node may not carry a figure the message never established.

    A wrong number inside fluent prose is harder to catch than a missing section, so the node fails
    rather than shipping it -- and the control proves the same node ships when it stays in words.
    """
    context = extract_shared_context(ORIGINAL)
    spec = operation_spec("factual_explanation")
    clause = "Explain why kr is used instead of a unique currency symbol and which countries use it"
    arguments = expand_clause(spec, clause, context)[0]

    from core.conductor.node import ConductorNode

    node = ConductorNode(node_id="e", operation="factual_explanation", request_text=clause,
                         arguments=arguments)

    grounded = spec.run(node, _context(shared_context=context))  # control
    assert "Sweden" in grounded["text"]

    def _inventing(_system: str, _briefing: str) -> str:
        return "kr is the Swedish krona, and the normal rate that week was 9.2 kr per dollar."

    with pytest.raises(ValueError, match="never established"):
        spec.run(node, _context(shared_context=context, run_generation=_inventing))


def test_sabotage_dropping_kr_from_the_ambiguity_table_loses_the_gap_it_reports() -> None:
    """Invariant: the missing-information list is produced by the unit table, not by prose."""
    from core.conductor import shared_context as module

    assert any("'kr' means" in item.statement for item in extract_shared_context(ORIGINAL).missing)

    saved = module._AMBIGUOUS_UNITS.pop("kr")
    try:
        assert not any("'kr' means" in item.statement for item in extract_shared_context(ORIGINAL).missing)
    finally:
        module._AMBIGUOUS_UNITS["kr"] = saved


def test_sabotage_reordering_the_general_operations_takes_the_gaps_clause_from_the_right_one() -> None:
    """Invariant: `general_priority`, not alphabetical order, decides who gets first refusal."""
    from core.conductor.registry import register_operation

    # A clause BOTH can serve: "explain" is an explanation cue and "cannot be determined" is a gaps
    # cue. Priority is the only thing that decides between them, so this is where reordering shows.
    clause = "explain exactly what cannot be determined from the information given"
    reply = json.dumps([
        {"request": clause, "operation": "assumptions", "depends_on": []},
        {"request": "Suggest two ways they could reduce currency-conversion losses",
         "operation": "suggestion", "depends_on": []},
    ])

    order = [spec.name for spec in general_operations()]
    # machine_observation goes first BY DECLARATION: its expander only fires on a clause the
    # runtime's own claim registry already owns, so its early slot can never take a gaps or
    # explanation clause -- but a machine claim must be offered before the refusal/catch-all
    # generals, because a deterministic host read outranks a model's guess.
    assert order == [
        # time_clock joins machine_observation in the deterministic-read tier
        # (the runtime's own clock outranks a model's guess by the same law);
        # measured in AUD-20260829-001's repair, 2026-08-29. water_temperature joined the same
        # tier with the Baltic water-temperature reader (a deterministic host read, declared).
        "time_clock",
        "water_temperature",
        "machine_observation",
        "missing_information",
        "quantitative_reasoning",
        "factual_explanation",
        "result_presentation",
    ]
    control = build_plan_from_clauses(parse_clauses(reply), original_request=ORIGINAL, plan_id="p")
    assert control.nodes[0].operation == "missing_information"

    spec = operation_spec("missing_information")
    last_priority = max(item.general_priority for item in general_operations()) + 1
    register_operation(replace(spec, general_priority=last_priority), replace=True)
    try:
        assert [s.name for s in general_operations()][-1] == "missing_information"
        plan = build_plan_from_clauses(parse_clauses(reply), original_request=ORIGINAL, plan_id="p")
        # The catch-all now takes the gaps clause and would answer it by asking a model what was
        # undetermined -- the component most likely to invent one. That is what the declared
        # priority prevents, and nothing alphabetical would have.
        assert plan.nodes[0].operation == "factual_explanation"
    finally:
        register_operation(spec, replace=True)


def test_sabotage_cutting_the_dependency_edge_empties_the_derived_facts_a_node_reads() -> None:
    """Invariant: derived values travel along declared edges, and only along them."""
    seen: list[dict] = []

    def _capture_generation(system_prompt: str, briefing: str) -> str:
        seen.append({"briefing": briefing})
        return _generation(system_prompt, briefing)

    linked = json.dumps([
        {"request": "Calculate how much money they effectively spent during the trip, both in kr and USD",
         "operation": "calculation", "depends_on": []},
        {"request": "identify every assumption you had to make to answer the questions above",
         "operation": "assumptions", "depends_on": [0]},
    ])
    cut = linked.replace('"depends_on": [0]', '"depends_on": []')

    def _gaps(reply: str) -> list[str]:
        plan = plan_conductor_turn(ORIGINAL, ask_model=lambda _s, _p: reply, plan_id="d")
        assert plan is not None
        outcomes = run_conductor_plan(plan, context=_context(run_generation=_capture_generation))
        gaps = next(o for o in outcomes if o.node.operation == "missing_information")
        return list(gaps.result["kinds"])

    assert "node_reported" not in _gaps(cut)  # nothing flows without an edge
    # Control: the identical plan WITH the edge carries the arithmetic node's own reported gap.
    with_edge_reply = linked.replace(
        '"request": "Calculate how much money they effectively spent during the trip, both in kr and USD"',
        '"request": "Explain whether the exchange was good or bad compared with the normal exchange rate"',
    )
    assert "node_reported" in _gaps(with_edge_reply)


def test_sabotage_a_clause_scoped_adapter_is_never_handed_the_shared_context() -> None:
    """Invariant: sharing is opt-in, so the contamination guard the conductor was built on holds.

    `_extract_weather_locations` over a whole message is what produced a real subtask fetching
    `wttr.in/price%20of%20bitcoin`. Every adapter that does entity recognition must still receive
    one argument, and the registry must be what decides that.
    """
    for name in ("weather_lookup", "market_quote", "calculation", "workspace_investigation",
                 "comparison", "conclusion"):
        assert operation_spec(name).wants_shared_context is False, name
        assert operation_spec(name).serves_unclaimed_clause is False, name

    received: list[tuple] = []

    def _one_argument_only(*args):
        received.append(args)
        return []

    from core.conductor.registry import OperationSpec, register_operation, unregister_operation

    register_operation(
        OperationSpec(name="probe_scoped", description="p", expand_arguments=_one_argument_only,
                      run=lambda n, c: {}, render=lambda n, r: ""),
        replace=True,
    )
    try:
        expand_clause(operation_spec("probe_scoped"), "a clause", extract_shared_context(ORIGINAL))
        assert received == [("a clause",)]
    finally:
        unregister_operation("probe_scoped")


def test_sabotage_a_node_with_no_generation_seam_fails_rather_than_inventing() -> None:
    """Invariant: absent means stated failure, never a scripted stand-in."""
    plan = plan_conductor_turn(ORIGINAL, ask_model=lambda _s, _p: SEVEN_CLAUSES, plan_id="c")
    assert plan is not None
    outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0, run_generation=None))

    roots = [o for o in outcomes if o.node.needs_generation and not o.node.depends_on]
    assert roots and all(o.state is NodeLifecycle.FAILED for o in roots)
    assert all("no generation seam available" in o.failure_reason for o in roots)
    # Their dependents are not attempted and say so, rather than running with nothing to run on.
    dependents = [o for o in outcomes if o.node.needs_generation and o.node.depends_on]
    assert dependents and all(o.state is NodeLifecycle.DEPENDENCY_FAILED for o in dependents)

    # The deterministic node still answers -- one missing seam must not cost the whole message.
    gaps = next(o for o in outcomes if o.node.operation == "missing_information")
    assert gaps.succeeded
    composed = compose_product(plan, outcomes)
    assert composed.complete
    # And this is the shape the agent refuses to ship: six unserved against one answered.
    assert composed.answered_count < composed.unserved_count
