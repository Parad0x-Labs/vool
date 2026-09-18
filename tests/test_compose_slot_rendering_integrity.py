"""AUD-20260829-003 pass 2 -- two composition defects BLUE-1 identified live over HTTP and handed
to BLUE-2 (compose.py is BLUE-2's file; the discriminator is that compose writes `- ` rows,
BLUE-1's finalization sweep writes `* ` rows -- every row below is `- `).

Both were reproduced against the served daemon (11437) on the canonical prompt "What is 1000 EUR
to RUB, how much gold can I buy with it, what is the weather in Rome, and what is the water
temperature in the Baltic Sea?" and confirmed via the daemon's own `runtime_session_events` log
(session a094e35eff79909d4da4) against the REAL 7-node plan the conductor built for it -- the
fixture data below is that plan, not an invented one.

C1 -- duplicated rows for one slot. The gold clause produced TWO separate
`quantitative_reasoning` nodes (`...with_it`, request_text "how much gold can I buy with it";
`...with_100`, request_text "How much gold can I buy with 1000 EUR?"), both failing with the same
exception. `compose_answer` merged node lines with `decision.failure_lines` via an exact-string
check (`if line not in unserved`), so the SAME slot rendered three rows: the two node lines plus a
requirement-ledger line ("was attempted and did not come back") naming the raw fragment again. Not
a substring match: the composer's own rewrite of one subject ("What is the water temperature in
the Baltic Sea?") did not exact-string-match the splitter's differently-punctuated phrasing of the
same clause either -- the same defect shape hit the water slot too.

C2 -- unrequested wrong-location rows served as answers. The SAME plan carried an `unresolved`
node for "What is the water temperature in the Baltic Sea?" (correctly refusing it -- no water-
temperature operation exists) ALONGSIDE two `weather_lookup` nodes for the bare fragments "Water"
and "Baltic Sea", each of which resolved to a real but irrelevant place (a nearby town's AIR
temperature) and rendered successfully. The composer's universe is the plan, so a node the planner
proposed that nobody asked for renders as answer content by default (R01's report: Layer 2) --
"Newberry Springs, United States" and "Össby, Sweden" were served as answers in the SAME body that
was, for the same underlying ask, listing "Could not be answered: What is the water temperature in
the Baltic Sea?". This is verdict property 5: served content must map back to minted demand.
"""

from __future__ import annotations

from core.conductor.compose import _row_subject, _subjects_overlap, compose_answer
from core.conductor.graph import build_graph
from core.conductor.node import ConductorNode, NodeFailureCode, NodeLifecycle, NodeOutcome
from core.conductor.planner import ConductorPlan
from tests.conductor_product import compose_product, decide

ORIGINAL_REQUEST = (
    "What is 1000 EUR to RUB, how much gold can I buy with it, what is the weather in Rome, "
    "and what is the water temperature in the Baltic Sea?"
)


def _node(node_id: str, request_text: str, operation: str = "probe", **kwargs) -> ConductorNode:
    return ConductorNode(node_id=node_id, operation=operation, request_text=request_text, **kwargs)


def _plan(*nodes: ConductorNode, original_request: str = ORIGINAL_REQUEST) -> ConductorPlan:
    return ConductorPlan(
        plan_id="p", original_request=original_request, graph=build_graph(list(nodes)), clause_count=len(nodes)
    )


def _failed(node: ConductorNode, receipt_id: str) -> NodeOutcome:
    """A node that failed with a generic exception -- the shape every C1/C2 fixture needs
    repeatedly, shortened so each test reads as the ONE thing it is actually varying."""
    return NodeOutcome(
        node=node, state=NodeLifecycle.FAILED, failure_code=NodeFailureCode.NODE_EXCEPTION, receipt_id=receipt_id
    )


def _the_real_seven_node_turn() -> tuple[ConductorPlan, list[NodeOutcome]]:
    """The exact plan measured live (AUD-20260829-003, session a094e35eff79909d4da4) -- see the
    module docstring. Every `node_id`, `request_text`, `arguments`, `clause_span`, and
    `obligation_id` below is copied from the daemon's own `conductor_plan_created` event, not
    invented."""
    fx = _node(
        "fx_quote:eur_rub", "What is 1000 EUR to RUB?", operation="fx_quote",
        arguments={"amount": "1000", "base": "EUR", "entity": "EUR/RUB", "quote": "RUB", "request_kind": "conversion"},
    )
    gold_rephrased = _node(
        "quantitative_reasoning:how_much_gold_can_i_buy_with_100",
        "How much gold can I buy with 1000 EUR?",
        operation="quantitative_reasoning",
        arguments={"clause": "How much gold can I buy with 1000 EUR?", "entity": "how_much_gold_can_i_buy_with_100"},
        required_result_fields=("steps", "values"),
    )
    weather_rome = _node(
        "weather_lookup:rome", "What is the weather in Rome?", operation="weather_lookup",
        arguments={"entity": "Rome", "location": "rome"},
    )
    water_unresolved = _node(
        "unresolved:3", "What is the water temperature in the Baltic Sea?", operation="unresolved",
        unresolved_reason="request is outside the weather capability domain",
    )
    weather_water_fragment = _node(
        "weather_lookup:water", "Water", operation="weather_lookup",
        arguments={"entity": "Water", "location": "water"},
        obligation_id="conductor-c0fb6ed50372:obligation:weather_lookup:water",
    )
    weather_baltic_fragment = _node(
        "weather_lookup:baltic_sea", "Baltic Sea", operation="weather_lookup",
        arguments={"entity": "Baltic Sea", "location": "baltic sea"},
        obligation_id="conductor-c0fb6ed50372:obligation:weather_lookup:baltic_sea",
    )
    gold_raw_fragment = _node(
        "quantitative_reasoning:how_much_gold_can_i_buy_with_it",
        "how much gold can I buy with it",
        operation="quantitative_reasoning",
        required_result_fields=("steps", "values"),
        clause_span=(25, 56),
        obligation_id="req:2:quantitative_reasoning",
    )
    nodes = [
        fx, gold_rephrased, weather_rome, water_unresolved,
        weather_water_fragment, weather_baltic_fragment, gold_raw_fragment,
    ]
    plan = _plan(*nodes)

    fx_rendered = (
        "EUR/RUB: 1000 EUR × 99.8 = 99800.0 RUB (source: Frankfurter public institutional "
        "rates; observed: 2026-08-29T00:00:00+00:00)."
    )
    outcomes = [
        NodeOutcome(node=fx, state=NodeLifecycle.SUCCEEDED, rendered=fx_rendered, result={}),
        NodeOutcome(
            node=gold_rephrased, state=NodeLifecycle.FAILED, failure_code=NodeFailureCode.NODE_EXCEPTION,
            receipt_id="ndf-9cfbeb00438e4ff8", failure_reason="ValueError: no evaluable arithmetic in this clause",
        ),
        NodeOutcome(
            node=weather_rome, state=NodeLifecycle.SUCCEEDED,
            rendered="Rome, Italy: Mainly clear, 28.2°C (source: open-meteo.com)", result={},
        ),
        NodeOutcome(node=water_unresolved, state=NodeLifecycle.UNRESOLVED, failure_code=NodeFailureCode.UNRESOLVED),
        NodeOutcome(
            node=weather_water_fragment, state=NodeLifecycle.SUCCEEDED,
            rendered="Newberry Springs, United States: Clear sky, 39.4°C (source: open-meteo.com)", result={},
        ),
        NodeOutcome(
            node=weather_baltic_fragment, state=NodeLifecycle.SUCCEEDED,
            rendered="Össby, Sweden: Thundery outbreaks in nearby, 16.0°C (source: wttr.in)", result={},
        ),
        NodeOutcome(
            node=gold_raw_fragment, state=NodeLifecycle.FAILED, failure_code=NodeFailureCode.NODE_EXCEPTION,
            receipt_id="ndf-8d80fb99a2124250", failure_reason="ValueError: no evaluable arithmetic in this clause",
        ),
    ]
    return plan, outcomes


# ---------------------------------------------------------------------------------------------
# The real turn, end to end: both defects reproduced together, both fixed together.
# ---------------------------------------------------------------------------------------------


def test_the_real_measured_turn_serves_exactly_two_answers_and_two_unavailable_rows():
    plan, outcomes = _the_real_seven_node_turn()
    composed = compose_product(plan, outcomes)

    assert composed.answered_node_ids == ("fx_quote:eur_rub", "weather_lookup:rome")
    assert "Newberry Springs" not in composed.text
    assert "Össby" not in composed.text
    assert "99800.0 RUB" in composed.text
    assert "Rome, Italy" in composed.text

    unserved_rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(unserved_rows) == 2, unserved_rows
    subjects = {_row_subject(line) for line in unserved_rows}
    assert subjects == {"How much gold can I buy with 1000 EUR?", "What is the water temperature in the Baltic Sea?"}


# ---------------------------------------------------------------------------------------------
# C1 -- duplicate rows for one slot, isolated from C2.
# ---------------------------------------------------------------------------------------------


def test_a_raw_fragment_and_its_rephrasing_collapse_to_one_unserved_row():
    rephrased = _node("gold_a", "How much gold can I buy with 1000 EUR?", required_result_fields=("v",))
    raw = _node("gold_b", "how much gold can I buy with it", required_result_fields=("v",))
    plan = _plan(rephrased, raw)
    outcomes = [
        _failed(rephrased, "r1"),
        _failed(raw, "r2"),
    ]
    composed = compose_product(plan, outcomes)
    rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(rows) == 1, rows


def test_a_requirement_ledger_line_does_not_duplicate_a_node_line_for_the_same_slot():
    """Same slot, different REASON text -- the shape that broke the naive exact-string check."""
    node = _node("gold", "how much gold can I buy with it", required_result_fields=("v",))
    plan = _plan(node)
    outcome = _failed(node, "r1")

    decision = decide(plan, [outcome])
    # Simulate the requirement ledger naming the SAME slot with a DIFFERENT reason, exactly as
    # `core.conductor.realization.failure_lines` does in production.
    dup_line = "- how much gold can I buy with it — was attempted and did not come back"
    decision_with_dup = decision.__class__(**{**decision.__dict__, "failure_lines": (dup_line,)})
    composed = compose_answer(plan, [outcome], decision_with_dup)
    rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(rows) == 1, rows


def test_two_genuinely_different_failing_slots_both_survive_dedup():
    """Negative control: sharing an incidental token ('1000') must not collapse two real slots."""
    fx = _node("fx", "What is 1000 EUR to RUB?", operation="fx_quote")
    gold = _node("gold", "How much gold can I buy with 1000 EUR?", required_result_fields=("v",))
    plan = _plan(fx, gold)
    outcomes = [
        _failed(fx, "r1"),
        _failed(gold, "r2"),
    ]
    composed = compose_product(plan, outcomes)
    rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(rows) == 2, rows


# ---------------------------------------------------------------------------------------------
# C2 -- unrequested content served as an answer, isolated from C1.
# ---------------------------------------------------------------------------------------------


def test_a_succeeded_node_overlapping_an_unresolved_sibling_is_suppressed():
    unresolved = _node("water", "What is the water temperature in the Baltic Sea?", operation="unresolved")
    wrong_place = _node(
        "weather_water", "Water", operation="weather_lookup", arguments={"entity": "Water", "location": "water"}
    )
    plan = _plan(unresolved, wrong_place)
    outcomes = [
        NodeOutcome(node=unresolved, state=NodeLifecycle.UNRESOLVED, failure_code=NodeFailureCode.UNRESOLVED),
        NodeOutcome(
            node=wrong_place, state=NodeLifecycle.SUCCEEDED,
            rendered="Newberry Springs, United States: Clear sky, 39.4°C", result={},
        ),
    ]
    composed = compose_product(plan, outcomes)

    assert composed.answered_node_ids == ()
    assert "Newberry Springs" not in composed.text
    assert "What is the water temperature in the Baltic Sea?" in composed.text
    assert "weather_water" not in composed.provenance


def test_a_legitimate_answer_survives_alongside_an_unrelated_unresolved_slot():
    """Negative control: suppression must be scoped to overlap, not to 'any unserved row exists'."""
    unresolved = _node("water", "What is the water temperature in the Baltic Sea?", operation="unresolved")
    rome = _node("rome", "What is the weather in Rome?", operation="weather_lookup", arguments={"entity": "Rome"})
    plan = _plan(unresolved, rome)
    outcomes = [
        NodeOutcome(node=unresolved, state=NodeLifecycle.UNRESOLVED, failure_code=NodeFailureCode.UNRESOLVED),
        NodeOutcome(node=rome, state=NodeLifecycle.SUCCEEDED, rendered="Rome, Italy: Mainly clear, 28.2°C", result={}),
    ]
    composed = compose_product(plan, outcomes)

    assert composed.answered_node_ids == ("rome",)
    assert "Rome, Italy" in composed.text
    assert "What is the water temperature in the Baltic Sea?" in composed.text


def test_a_succeeded_node_overlapping_a_failed_sibling_is_also_suppressed():
    """The C2 gate checks the WHOLE unserved list, not only UNRESOLVED-state rows."""
    failed = _node("gold_a", "How much gold can I buy with 1000 EUR?", required_result_fields=("v",))
    wrong_answer = _node("gold_b", "how much gold", operation="quantitative_reasoning")
    plan = _plan(failed, wrong_answer)
    outcomes = [
        _failed(failed, "r1"),
        NodeOutcome(node=wrong_answer, state=NodeLifecycle.SUCCEEDED, rendered="Gold is a metal.", result={}),
    ]
    composed = compose_product(plan, outcomes)
    assert "wrong_answer" not in composed.provenance
    assert composed.answered_node_ids == ()


# ---------------------------------------------------------------------------------------------
# Unit-level pins on the two new primitives.
# ---------------------------------------------------------------------------------------------


def test_row_subject_strips_the_leading_marker_and_the_reason():
    assert _row_subject("- Rome — could not be answered: fault") == "Rome"
    assert _row_subject("no marker, no dash here") == "no marker, no dash here"


def test_subjects_overlap_is_order_independent():
    raw = "how much gold can I buy with it"
    rephrased = "How much gold can I buy with 1000 EUR?"
    assert _subjects_overlap(raw, rephrased)
    assert _subjects_overlap(rephrased, raw)


def test_subjects_overlap_does_not_collide_on_a_shared_incidental_token():
    assert not _subjects_overlap("What is 1000 EUR to RUB?", "How much gold can I buy with 1000 EUR?")


def test_subjects_overlap_is_false_for_empty_subjects():
    assert not _subjects_overlap("", "anything")
    assert not _subjects_overlap("anything", "")


# ---------------------------------------------------------------------------------------------
# Regression: entity identity must gate BEFORE subject-text overlap (found while verifying C1
# against the real regression suite -- tests/test_conductor_multi_intent.py's mutation test caught
# it first; this pins the shape directly).
# ---------------------------------------------------------------------------------------------


def test_two_different_entities_sharing_one_clauses_wording_do_not_collapse():
    """"get the current weather for Kaunas and Tallinn" splits into two weather_lookup nodes, ONE
    PER CITY, and both nodes carry that IDENTICAL clause-level request_text -- text-overlap alone
    would treat "Kaunas failed" and "Tallinn failed" as the same duplicate row instead of two real,
    independently-failing slots. Entity identity (`arguments["entity"]`) must decide instead."""
    shared_text = "get the current weather for Kaunas and Tallinn"
    kaunas = _node("weather:kaunas", shared_text, operation="weather_lookup", arguments={"entity": "Kaunas"})
    tallinn = _node("weather:tallinn", shared_text, operation="weather_lookup", arguments={"entity": "Tallinn"})
    plan = _plan(kaunas, tallinn)
    outcomes = [
        NodeOutcome(node=kaunas, state=NodeLifecycle.SUCCEEDED, rendered="ok", result={}),
        _failed(tallinn, "r1"),
    ]
    # Kaunas succeeds (answered); Tallinn fails (unserved) -- neither the C1 dedup nor the C2 gate
    # may treat Tallinn's failure as already covered by Kaunas's shared-wording success.
    composed = compose_product(plan, outcomes)
    assert composed.answered_node_ids == ("weather:kaunas",)
    rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(rows) == 1, rows


def test_two_different_entities_both_failing_with_shared_wording_both_render():
    shared_text = "get the current weather for Kaunas and Tallinn"
    kaunas = _node("weather:kaunas", shared_text, operation="weather_lookup", arguments={"entity": "Kaunas"})
    tallinn = _node("weather:tallinn", shared_text, operation="weather_lookup", arguments={"entity": "Tallinn"})
    plan = _plan(kaunas, tallinn)
    outcomes = [_failed(kaunas, "r1"), _failed(tallinn, "r2")]
    composed = compose_product(plan, outcomes)
    rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(rows) == 2, rows


def test_same_entity_and_overlapping_wording_still_dedups():
    """The entity gate must not accidentally DISABLE C1's dedup when the entity genuinely agrees."""
    node_a = _node("gold_a", "how much gold", operation="quantitative_reasoning", arguments={"entity": "gold"})
    node_b = _node(
        "gold_b", "how much gold can I buy", operation="quantitative_reasoning", arguments={"entity": "gold"}
    )
    plan = _plan(node_a, node_b)
    outcomes = [_failed(node_a, "r1"), _failed(node_b, "r2")]
    composed = compose_product(plan, outcomes)
    rows = [line for line in composed.text.splitlines() if line.startswith("- ")]
    assert len(rows) == 1, rows
