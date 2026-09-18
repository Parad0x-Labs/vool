"""Adversarial input to the planner and the graph.

A planner reply is untrusted input. It arrives from a model that may be small, quantized, having a
bad day, or repeating something from its context, so the interesting cases are all malformed:
invented entities, cycles, duplicate ids, fenced JSON, prose wrappers, absurd sizes. A greeting-
shaped test proves none of it.

The single most important property here is the containment check. `verify_plan` -- reused rather
than reimplemented -- requires every content word of every clause to already appear in the user's
message, which is what makes trusting a model-authored plan defensible at all. Without it a model
that answers instead of splitting, or that hallucinates a third city, produces a plan the runtime
would faithfully execute.
"""
from __future__ import annotations

import json

import pytest

from core.conductor.graph import GraphRejectionError, build_graph
from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.planner import (
    MAX_PLANNED_CLAUSES,
    build_plan_from_clauses,
    parse_clauses,
    plan_conductor_turn,
)
from core.conductor.registry import UNRESOLVED_OPERATION, operation_spec, unregister_operation

MIXED = (
    "What is 137 x 29? Also get the current weather for Kaunas and Tallinn "
    "and tell me which city is warmer."
)


def _reply(*clauses: dict) -> str:
    return json.dumps(list(clauses))


def _plan(reply: str, text: str = MIXED):
    return plan_conductor_turn(text, ask_model=lambda _s, _p: reply, plan_id="t")


GOOD = _reply(
    {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
    {
        "request": "get the current weather for Kaunas and Tallinn",
        "operation": "weather_lookup",
        "depends_on": [],
    },
    {"request": "which city is warmer", "operation": "comparison", "depends_on": [1]},
)


# --------------------------------------------------------------------------------------
# Parsing untrusted replies
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "I think the user wants three things.",
        "[]",
        "{}",
        "null",
        "[1, 2, 3]",
        '[{"request": "", "operation": "calculation"}]',
        '[{"request": "x", "operation": ""}]',
        '[{"operation": "calculation"}]',
        '[{"request": "a", "operation": "calculation", "depends_on": ["x"]}]',
        '[{"request": "a", "operation": "calculation", "depends_on": [0]}]',   # self
        '[{"request": "a", "operation": "calculation", "depends_on": [1]}]',   # forward
        '[{"request": "a", "operation": "calculation", "depends_on": [-1]}]',  # negative
    ],
)
def test_unusable_planner_replies_parse_to_nothing(raw: str) -> None:
    assert parse_clauses(raw) == []


def test_a_fenced_reply_still_parses_because_models_emit_fences_they_were_told_not_to() -> None:
    fenced = "Sure!\n```json\n" + GOOD + "\n```\nHope that helps."
    clauses = parse_clauses(fenced)
    assert [clause.operation for clause in clauses] == [
        "calculation",
        "weather_lookup",
        "comparison",
    ]


def test_a_plan_larger_than_the_cap_is_rejected_whole_not_truncated() -> None:
    """Truncating would silently drop requests -- the exact failure this slice removes."""
    oversized = _reply(
        *(
            {"request": f"request {index}", "operation": "calculation", "depends_on": []}
            for index in range(MAX_PLANNED_CLAUSES + 1)
        )
    )
    assert parse_clauses(oversized) == []


# --------------------------------------------------------------------------------------
# The containment property
# --------------------------------------------------------------------------------------


def test_an_invented_entity_rejects_the_whole_plan() -> None:
    """A third city the user never wrote must not become a real fetch."""
    invented = _reply(
        {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
        {"request": "weather for Kaunas and Riga", "operation": "weather_lookup", "depends_on": []},
    )
    assert _plan(invented) is None


def test_a_planner_that_answers_instead_of_splitting_is_rejected() -> None:
    answered = _reply(
        {"request": "137 times 29 equals 3973", "operation": "calculation", "depends_on": []},
        {"request": "weather for Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
    )
    assert _plan(answered) is None


def test_dropping_a_user_word_is_allowed_because_that_is_how_filler_is_stripped() -> None:
    """Containment is one-way on purpose: an incomplete plan is caught by unresolved nodes, not here."""
    assert _plan(GOOD) is not None


# --------------------------------------------------------------------------------------
# Claiming discipline -- no regression on turns the existing lanes serve
# --------------------------------------------------------------------------------------


def test_a_pure_weather_turn_is_left_to_the_live_data_lane() -> None:
    """The no-regression guard. That lane already runs these concurrently with its own proof.

    The hand-off is conditioned on PROOF (the lane's per-entity coverage probe says it serves every
    planned node); a membership-only decline dropped clauses in production, so with no probe -- or a
    probe that says the lane does NOT cover the plan -- the conductor keeps it.
    """
    text = "get the current weather for Kaunas and Tallinn and tell me which city is warmer"
    reply = _reply(
        {"request": "get the current weather for Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
        {"request": "which city is warmer", "operation": "comparison", "depends_on": [0]},
    )
    covered = plan_conductor_turn(text, ask_model=lambda _s, _p: reply, plan_id="t", lane_coverage_probe=lambda plan: True)
    assert covered is None
    kept = plan_conductor_turn(text, ask_model=lambda _s, _p: reply, plan_id="t", lane_coverage_probe=lambda plan: False)
    assert kept is not None and {node.operation for node in kept.nodes} >= {"weather_lookup", "comparison"}


def test_a_single_clause_plan_is_declined_without_running_anything() -> None:
    reply = _reply({"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []})
    assert _plan(reply) is None


def test_copying_the_whole_turn_does_not_prove_one_operation_covers_it() -> None:
    reply = _reply({"request": MIXED, "operation": "calculation", "depends_on": []})
    assert _plan(reply) is None


def test_a_planner_exception_declines_the_turn_rather_than_failing_it() -> None:
    def _boom(_system: str, _prompt: str) -> str:
        raise RuntimeError("planner provider is down")

    assert plan_conductor_turn(MIXED, ask_model=_boom) is None


def test_a_message_with_one_request_never_reaches_the_model_at_all() -> None:
    calls: list[str] = []

    def _record(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return GOOD

    assert plan_conductor_turn("hi", ask_model=_record) is None
    assert calls == []


# --------------------------------------------------------------------------------------
# Graph validation
# --------------------------------------------------------------------------------------


def _node(node_id: str, deps: tuple[str, ...] = ()) -> ConductorNode:
    return ConductorNode(
        node_id=node_id, operation="calculation", request_text="x", depends_on=deps
    )


def test_duplicate_node_ids_reject_the_plan() -> None:
    """Verified on main: the live-data runner keys outcomes by id in a plain dict, so two subtasks
    sharing one id return the SAME outcome object twice under different entity names, silently."""
    with pytest.raises(GraphRejectionError) as excinfo:
        build_graph([_node("a"), _node("a")])
    assert excinfo.value.reason == "duplicate_node_id"


def test_an_unknown_dependency_rejects_the_plan() -> None:
    with pytest.raises(GraphRejectionError) as excinfo:
        build_graph([_node("a", ("ghost",))])
    assert excinfo.value.reason == "unknown_dependency"


def test_a_self_dependency_rejects_the_plan() -> None:
    with pytest.raises(GraphRejectionError) as excinfo:
        build_graph([_node("a", ("a",))])
    assert excinfo.value.reason == "self_dependency"


def test_a_cycle_rejects_the_plan() -> None:
    with pytest.raises(GraphRejectionError) as excinfo:
        build_graph([_node("a", ("b",)), _node("b", ("a",))])
    assert excinfo.value.reason == "cyclic_plan"


def test_an_empty_plan_rejects() -> None:
    with pytest.raises(GraphRejectionError):
        build_graph([])


def test_the_ready_set_releases_a_node_the_moment_its_dependencies_are_terminal() -> None:
    graph = build_graph([_node("a"), _node("b"), _node("c", ("a", "b"))])
    outcomes: dict[str, NodeOutcome] = {}
    assert {n.node_id for n in graph.ready(outcomes, ())} == {"a", "b"}

    outcomes["a"] = NodeOutcome(node=graph.by_id["a"], state=NodeLifecycle.SUCCEEDED)
    assert {n.node_id for n in graph.ready(outcomes, {"a"})} == {"b"}

    outcomes["b"] = NodeOutcome(node=graph.by_id["b"], state=NodeLifecycle.FAILED)
    # Terminal, not successful -- 'c' is released so the scheduler can mark it DEPENDENCY_FAILED
    # rather than leaving it absent from the outcome map.
    assert {n.node_id for n in graph.ready(outcomes, {"a", "b"})} == {"c"}


# --------------------------------------------------------------------------------------
# The registry is the extensibility seam
# --------------------------------------------------------------------------------------


def test_removing_an_operation_makes_its_clause_unresolved_rather_than_absent() -> None:
    """The mutation that proves the registry is load-bearing AND that removal fails closed."""
    spec = operation_spec("calculation")
    assert spec is not None
    try:
        unregister_operation("calculation")
        plan = _plan(GOOD)
        assert plan is not None
        operations = [node.operation for node in plan.nodes]
        assert UNRESOLVED_OPERATION in operations
        unresolved = next(n for n in plan.nodes if n.operation == UNRESOLVED_OPERATION)
        assert unresolved.request_text == "What is 137 x 29?"
        assert "no registered operation named 'calculation'" in unresolved.unresolved_reason
    finally:
        from core.conductor.registry import register_operation

        register_operation(spec, replace=True)


def test_a_clause_whose_operation_finds_nothing_to_act_on_is_unresolved() -> None:
    """A weather clause naming no place cannot be served, and must say so."""
    reply = _reply(
        {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
        {"request": "which city is warmer", "operation": "weather_lookup", "depends_on": []},
    )
    plan = _plan(reply)
    assert plan is not None
    unresolved = [n for n in plan.nodes if n.operation == UNRESOLVED_OPERATION]
    assert len(unresolved) == 1
    assert "found nothing to act on" in unresolved[0].unresolved_reason


def test_an_adapter_that_raises_during_expansion_costs_only_its_own_clause() -> None:
    from core.conductor.capabilities import OperationCapability, OperationEffect
    from core.conductor.planner import ProposedClause
    from core.conductor.registry import OperationSpec, register_operation
    from core.turn_ir import ClauseKind

    def _explode(_text: str) -> list[dict]:
        raise RuntimeError("recognizer exploded")

    register_operation(
        OperationSpec(
            name="_exploding_test_op",
            description="test",
            expand_arguments=_explode,
            run=lambda _n, _c: {},
            render=lambda _n, _r: "",
            capability=OperationCapability(
                effect=OperationEffect.KNOWLEDGE_ANSWER,
                domain="test",
                accepted_kinds=frozenset({ClauseKind.UNKNOWN}),
            ),
        ),
        replace=True,
    )
    try:
        plan = build_plan_from_clauses(
            [
                ProposedClause(0, "What is 137 x 29?", "calculation", ()),
                ProposedClause(1, "boom", "_exploding_test_op", ()),
            ],
            original_request=MIXED,
            plan_id="t",
        )
        # The property: the exploding adapter costs its OWN clause and nothing else. The
        # calculation still stands, and exactly one node carries the recognizer's fault.
        #
        # Asserted by shape rather than by an exact node list. This call deliberately passes two
        # clauses against `MIXED`, whose other sentences those clauses do not carry, so the
        # canonical obligation floor now reports them as covered nowhere instead of letting them
        # disappear. Those reports are correct and are not this test's subject.
        # The calculation survives and exactly one node carries the recognizer's fault. Asserted
        # by shape rather than by an exact list: this call passes two clauses against `MIXED`,
        # whose weather request the clauses do not carry, so the requirement ledger captures it and
        # -- a planner omission being no reason not to serve a serviceable requirement -- executes
        # it. That is correct and is not this test's subject.
        assert "calculation" in [n.operation for n in plan.nodes]
        exploded = [n for n in plan.nodes if "recognizer exploded" in n.unresolved_reason]
        assert len(exploded) == 1
    finally:
        unregister_operation("_exploding_test_op")


# --- Projection admission: a proved family still has to be servable ----------------------------
#
# Measured 2026-09-09 on served acceptance turn 18 ("Answer all three: What is 5+5? What is the
# exact middle name of the current Emperor of Japan? In what year did the Berlin Wall fall?").
# The served plan carried FIVE nodes for three clauses and the Berlin Wall clause appeared twice:
# once unresolved, and once as `quantitative_reasoning` with `needs_generation: true`, EMPTY
# arguments and `obligation_id: "req:2:quantitative_reasoning"`. The projection built that node
# straight from the realization -- it read the operation spec for METADATA only and never asked
# whether the operation serves the request. The node then bought a model call asking for an
# ARITHMETIC PLAN for a history question and reported "the model returned no arithmetic plan for
# this clause". No correct model would have produced one; the request was never arithmetic.
# Runtime events for that turn: validation-logs/consolidation-continuation-20260909/
# evidence-opus-20260909/t18-runtime-events.json
#
# The frame is proven by the BOUNDED PROPOSER, not by the formal grammars, which is why these
# tests drive `propose_semantics` through the shared `tests.semantic_proposer` helper: without it
# the requirement is never captured, the projection never runs, and a test here proves nothing.

TURN_18 = (
    "Answer all three: What is 5 + 5? What is the exact middle name of the current Emperor of "
    "Japan? In what year did the Berlin Wall fall?"
)
_TURN_18_CLAUSES = [
    ("What is 5 + 5?", "calculation"),
    ("What is the exact middle name of the current Emperor of Japan?", "reviewed_safe_knowledge"),
    ("In what year did the Berlin Wall fall?", "market_quote"),
]


def _plan_with_proven_family(original: str, named, *, family: str, scope: str, predicate: str):
    from core.conductor.planner import ProposedClause
    from core.conductor.shared_context import extract_shared_context
    from tests.semantic_proposer import frame, proposer

    return build_plan_from_clauses(
        [ProposedClause(i, req, op, ()) for i, (req, op) in enumerate(named, 1)],
        original_request=original,
        plan_id="admission",
        shared_context=extract_shared_context(original),
        propose_semantics=proposer(frame(family, scope=scope, predicate=predicate)),
    )


def _turn_18_plan():
    return _plan_with_proven_family(
        TURN_18, _TURN_18_CLAUSES,
        family="quantitative_reasoning",
        scope="In what year did the Berlin Wall fall?",
        predicate="fall",
    )


def test_a_projected_family_its_operation_refuses_does_not_become_a_node() -> None:
    plan = _turn_18_plan()
    minted = [
        node for node in plan.nodes
        if node.operation == "quantitative_reasoning" and "Berlin Wall" in node.request_text
    ]
    assert minted == [], (
        "the projection minted a quantitative_reasoning node for a clause its own operation gate "
        f"declines: needs_generation={[n.needs_generation for n in minted]} "
        f"arguments={[dict(n.arguments) for n in minted]}"
    )
    # Declining to mint work is not dropping the request: the clause is still accounted for.
    berlin = [node for node in plan.nodes if "Berlin Wall" in node.request_text]
    assert berlin, "the Berlin Wall clause vanished from the plan entirely"
    # AMENDED for F41: this clause used to be pinned UNRESOLVED — the wrong projection
    # (quantitative_reasoning for a history question) refused, and nothing else could serve
    # a plain knowledge question. Since the named-path KNOW admission, the knowledge family
    # serves it instead of leaving it dead: served beats reported when the runtime's own
    # typing says the family can answer. The assertion that still guards the ORIGINAL point
    # — the projected family itself mints nothing — is the one above.
    assert all(node.operation in (UNRESOLVED_OPERATION, "factual_explanation") for node in berlin), [
        (node.operation, node.request_text) for node in berlin
    ]
    assert any(node.operation == "factual_explanation" for node in berlin), (
        "the F41 repair regressed: a plain KNOW clause died unresolved beside a refusing "
        f"projection: {[(n.operation, n.request_text) for node in berlin]}"
    )


def test_the_arithmetic_sibling_still_serves_after_the_admission_check() -> None:
    """The guard must not cost the clause that IS arithmetic: 5 + 5 keeps its calculation node."""
    plan = _turn_18_plan()
    calculation = [node for node in plan.nodes if node.operation == "calculation"]
    assert len(calculation) == 1, [(n.node_id, n.operation) for n in plan.nodes]
    assert calculation[0].arguments.get("expression_text")


def test_a_proven_generation_family_that_serves_its_clause_is_still_projected() -> None:
    """The check ASKS the operation; it does not refuse projected generation as a class.

    Same proven family, same projection path, a clause `_quantitative_expand` accepts -- a figure
    ask over numbers the message supplies. It must still be minted, or the guard would be
    satisfiable by never projecting a `needs_generation` family at all.
    """
    original = "I have 250 EUR and 4 people. Work out how much each person gets."
    plan = _plan_with_proven_family(
        original,
        [("Work out how much each person gets.", "quantitative_reasoning")],
        family="quantitative_reasoning",
        scope="Work out how much each person gets.",
        predicate="work out",
    )
    assert any(node.operation == "quantitative_reasoning" for node in plan.nodes), [
        (n.node_id, n.operation) for n in plan.nodes
    ]


def test_an_independent_knowledge_clause_survives_a_plan_that_serves_its_sibling() -> None:
    """A supported sibling must not take an independently answerable clause down with it.

    `factual_explanation` is the registry's general knowledge server and carries TWO resolvers: the
    broad `expand_named_arguments` and the narrow `expand_arguments` the unclaimed-clause chain
    uses. The narrow one is right WHERE IT IS USED, because that chain also decides whether the
    conductor CLAIMS a message -- widening it broke five guards on 2026-09-09 (FINDINGS F19),
    including both seven-node-failure sabotage pins. So the two decisions are split at the CALL
    SITE: a stranded clause is retried with the named resolver only after every clause is resolved,
    and only in a plan that already serves something else.
    """
    from core.conductor.planner import build_plan_from_clauses
    from core.conductor.planner import ProposedClause
    from core.conductor.registry import UNRESOLVED_OPERATION

    served_sibling = build_plan_from_clauses(
        (
            ProposedClause(index=0, request="What is 12 times 8?", operation="calculation", depends_on=()),
            ProposedClause(index=1, request="what is the chemical symbol for gold?", operation="", depends_on=()),
        ),
        original_request="What is 12 times 8? Also, what is the chemical symbol for gold?",
        plan_id="independent-knowledge",
    )
    knowledge_nodes = [
        node for node in served_sibling.nodes if "gold" in str(node.request_text or "").casefold()
    ]
    assert knowledge_nodes, "the knowledge clause produced no node at all"
    assert all(node.operation != UNRESOLVED_OPERATION for node in knowledge_nodes), (
        "an independently answerable clause was stranded beside a served sibling: "
        f"{[(n.operation, n.request_text, n.unresolved_reason) for n in knowledge_nodes]}"
    )


def test_a_dependent_clause_is_not_rescued_as_generic_prose() -> None:
    """The discriminator, and why it is needed: the two signals already available cannot tell these
    apart -- `_asks_for_explanation` and `_stable_knowledge_capability_accepts` both answer True for
    "Explain the calculation briefly." AND for an independent knowledge question. Serving the
    dependent one with none of its sibling's computed value is the harm the narrow gate prevents.
    """
    from core.conductor.planner import build_plan_from_clauses
    from core.conductor.planner import ProposedClause
    from core.conductor.registry import UNRESOLVED_OPERATION

    plan = build_plan_from_clauses(
        (
            ProposedClause(index=0, request="What is 137 x 29?", operation="calculation", depends_on=()),
            ProposedClause(index=1, request="Explain the calculation briefly.", operation="", depends_on=()),
        ),
        original_request="What is 137 x 29? Explain the calculation briefly.",
        plan_id="dependent-clause",
    )
    dependent = [
        node for node in plan.nodes if "explain" in str(node.request_text or "").casefold()
    ]
    assert dependent, "the dependent clause vanished from the plan entirely"
    assert all(node.operation == UNRESOLVED_OPERATION for node in dependent), (
        "a clause that points at a sibling's work was answered as standalone prose: "
        f"{[(n.operation, n.request_text) for n in dependent]}"
    )


def test_a_constraint_welded_to_a_demand_is_trimmed_before_the_plan_reads_it() -> None:
    """A planner may return ONE clause spanning a constraint and the demand after it.

    Measured on build a9618aae, acceptance turn 7: the proposed clause was
    "Do NOT search the web for this. From memory: what is the boiling point of water at sea level
    in Celsius?" and the whole span became one request. Two harms followed -- the reader was shown
    an instruction the runtime had OBEYED inside "Could not be answered", and the answerable half
    was judged by a text carrying the prohibition's "this", which reads as a reference to a
    sibling's work and so was skipped by the independence check.

    Turn IR already owns clause boundaries and already calls a prohibition ClauseKind.CONSTRAINT,
    so the trim only re-uses that verdict. It never invents: the result is a subset of the user's
    own words, which is what `_verify_no_invented_content` requires.
    """
    from core.conductor.planner import _demand_only

    welded = (
        "Do NOT search the web for this. From memory: what is the boiling point of water at sea "
        "level in Celsius?"
    )
    trimmed = _demand_only(welded)
    assert trimmed == "From memory: what is the boiling point of water at sea level in Celsius?", trimmed
    assert "do not search" not in trimmed.casefold()

    # A span that is ALL constraint is returned unchanged -- trimming it to nothing would erase the
    # only record that the user said it.
    assert _demand_only("Do NOT search the web for this.") == "Do NOT search the web for this."
    # An ordinary single demand is untouched.
    assert _demand_only("What is 12 times 8?") == "What is 12 times 8?"


def test_the_rescue_pass_does_not_loosen_admission_at_the_real_entry_point() -> None:
    """The safety property of the clause-rescue pass, checked where it is actually decided.

    The guards that constrain the unclaimed-clause chain call `expand_clause` DIRECTLY, so they
    prove the narrow resolver is unchanged and nothing more. Whether the new CALLER preserves the
    conductor's admission boundary is a property of `plan_conductor_turn`, and only driving that
    function can establish it: the pass runs after clause resolution and could, if it were wrong,
    turn a message the conductor must decline into one it claims.

    Each message below is one the conductor must NOT claim -- specific adapters own it, or it has
    no figures and belongs to the lanes that already answer it, or nothing in it is servable at all.
    """
    import json

    from core.conductor.planner import plan_conductor_turn

    must_decline = [
        (
            "specific adapters own it",
            "What is 137 x 29? Explain the calculation briefly. Also get the current weather for "
            "Kaunas and Tallinn and tell me which city is warmer.",
            [
                {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
                {"request": "Explain the calculation briefly.", "operation": "", "depends_on": []},
            ],
        ),
        (
            "no figures -- left to the lanes that already answer it",
            "Explain how currency conversion works and why gold is priced in USD.",
            [
                {"request": "Explain how currency conversion works", "operation": "", "depends_on": []},
                {"request": "why gold is priced in USD", "operation": "", "depends_on": []},
            ],
        ),
        (
            "nothing servable at all",
            "Fly me to Paris tomorrow. Also teleport my car to Berlin.",
            [
                {"request": "Fly me to Paris tomorrow.", "operation": "", "depends_on": []},
                {"request": "Also teleport my car to Berlin.", "operation": "", "depends_on": []},
            ],
        ),
    ]
    for label, text, proposed in must_decline:
        plan = plan_conductor_turn(
            text,
            ask_model=lambda _system, _user, _payload=json.dumps(proposed): _payload,
            plan_id=f"admission-{label[:12]}",
        )
        assert plan is None, (
            f"the conductor CLAIMED a message it must decline ({label}): "
            f"{[(n.operation, n.request_text) for n in plan.nodes]}"
        )


def test_a_requirement_is_served_by_the_node_that_covers_its_span() -> None:
    """Geometry, not wording, decides which node serves a requirement.

    `demands_this_plan_cannot_execute` already states the rule for demand units -- "the primary
    authority is the GEOMETRIC one ... because wording must never decide which node serves which
    demand" -- but requirement->node binding was family-keyed only.

    Measured on acceptance turn 7: the frame proposer labelled the boiling-point clause
    `quantitative_reasoning`; no node of that family exists, so the requirement bound to nothing and
    was reported to the reader as "is not something this runtime can look up" -- while a
    `factual_explanation` node covering the very same span had been planned and dispatched. The
    runtime told the user it could not do a thing it was at that moment doing.
    """
    import json

    from core.conductor.planner import plan_conductor_turn

    request = (
        "Do NOT search the web for this. From memory: what is the boiling point of water at sea "
        "level in Celsius? Also, what is 10 percent of 250?"
    )
    proposed_plan = json.dumps(
        [
            {
                "request": "what is the boiling point of water at sea level in Celsius?",
                "operation": "reviewed_safe_knowledge",
                "depends_on": [],
            },
            {"request": "what is 10 percent of 250?", "operation": "calculation", "depends_on": []},
        ]
    )
    frames = json.dumps(
        {
            "frames": [
                {
                    "family": "quantitative_reasoning",
                    "scope": "From memory: what is the boiling point of water at sea level in Celsius?",
                    "predicate": "",
                    "polarity": "affirmed",
                    "roles": [],
                }
            ]
        }
    )
    plan = plan_conductor_turn(
        request,
        ask_model=lambda _s, _u: proposed_plan,
        plan_id="geometric-binding",
        propose_semantics=lambda _s, _u: frames,
    )
    assert plan is not None
    bound = dict(plan.requirement_nodes)
    assert bound, (
        "no requirement bound to any node, so nothing can answer 'did a node SERVE this' -- "
        f"nodes were {[(n.operation, n.clause_span) for n in plan.nodes]}"
    )
    served_ids = {node_id for ids in bound.values() for node_id in ids}
    serving = [node for node in plan.nodes if node.node_id in served_ids]
    assert any(node.operation == "factual_explanation" for node in serving), (
        f"the requirement was not bound to the node covering its span: "
        f"{[(n.operation, n.node_id) for n in serving]}"
    )
