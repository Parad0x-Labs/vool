"""V2-pack root-cause repairs — regression families (2026-08-29).

Each family pins one repaired class from the behavioral pack's first run,
with the original failing wording plus paraphrase/negative controls
(CLAUDE.md §6b): the original string must never become load-bearing.

Class B — a bare hardware word in a definition frame must not route to the
machine-specs lane.        Class A — a slice co-claimed across domain groups
must not read as whole-turn coverage, and a conversion that leaves residue
must not claim the turn.   Class C — a clause a node actually served can
never be reported in "Could not be answered".   Class D — "JSON only" binds
the raw-output contract (fence unwrapped, no appended decoration).
"""

from __future__ import annotations

from core.agent_runtime.answer_coverage import FAMILY_CURRENCY, coverage_for
from core.agent_runtime.fast_paths_currency import currency_fast_path
from core.agent_runtime.fast_paths_machine import looks_like_machine_specs_question
from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.realization import (
    BoundExecutionPlan,
    NonExecutionEvidence,
    NonExecutionReason,
    RealizationBinding,
    RealizationLedger,
    RealizationState,
    RequirementRealization,
    reduce_bound_plan,
)
from core.execution.constants import asks_runtime_for_a_fact
from core.raw_output_contract import parse_raw_output_contract

# ── Class B: hardware word ≠ hardware question ──────────────────────────────


def test_the_original_hijack_compound_stands_the_specs_lane_down():
    assert (
        looks_like_machine_specs_question(
            "who made linux + capital mongolia + 47*19 + one alkaline-earth metal + what RAM means"
        )
        is False
    )


def test_definition_frames_stay_out_of_the_specs_lane():
    for text in (
        "what RAM means",
        "RAM meaning?",
        "what does GPU stand for",
        "explain what CPU means for gaming",
        "the meaning of VRAM",
    ):
        assert looks_like_machine_specs_question(text) is False, text


def test_real_spec_questions_still_reach_the_specs_lane():
    for text in (
        "how much ram do i have",
        "what gpu do i have",
        "what cpu is in this machine?",
        "how many cores does this laptop have",
        "system specs",
        "machine specs",
        "show me the hardware specs",
        "give me the pc specs",
        "my pc ram",
    ):
        assert looks_like_machine_specs_question(text) is True, text


def test_the_shared_general_knowledge_gate_covers_attribution_and_definitions():
    assert asks_runtime_for_a_fact.__module__  # gate lives here; frames verified below
    from core.execution.constants import _GENERAL_KNOWLEDGE_RE

    for text in (
        "who made linux",
        "who invented the telephone",
        "what RAM means",
        "what does GPU stand for",
    ):
        assert _GENERAL_KNOWLEDGE_RE.search(text), text
    for text in ("how much ram do i have", "what gpu do i have"):
        assert not _GENERAL_KNOWLEDGE_RE.search(text), text


# ── Class A: cross-domain co-claims and conversion residue ──────────────────


def test_the_original_mixed_message_no_longer_closes_the_turn_as_currency():
    # THE Q001 CONTRACT. The closed-contract decision must decline when a slice fuses two
    # kinds of work (currency ∥ live_info on one comma-run slice): the decomposing lanes
    # own genuinely fused turns. (A broader unclaimed-slice guard was tried and REVERTED:
    # it rerouted 114 frozen closed-contract flows — measured, not guessed. This guard
    # fires only on the fused shape the defect actually had.)
    from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn

    assert (
        closed_semantic_contract_covers_turn(
            "weather in rome rn, 100 usd to rub, 18^2, and name one graph db. all 4 pls no essay",
            session_id="v2-repair",
            source_context={"surface": "api"},
        )
        is False
    ), "a fused cross-domain slice must not close the turn as one conversion"


def test_a_clean_single_conversion_still_closes_the_turn():
    from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn

    assert (
        closed_semantic_contract_covers_turn(
            "1000 rub to eur",
            session_id="v2-repair",
            source_context={"surface": "api"},
        )
        is True
    ), "the guard must not touch a fully-covered single-request turn"


def test_same_group_co_claims_keep_whole_turn_coverage():
    # "gold price?" read by market_quote ∥ live_info is ONE request with two
    # readings — the same-group pair must not downgrade the claim.
    coverage = coverage_for("gold price?", "market_quote")
    assert coverage.covers_whole_turn is True


def test_conversion_inside_a_mixed_message_is_recognized_but_never_claims_whole_turn():
    # The recognizer still works and the fast path still renders — but the turn-level
    # DECISION belongs to the closed-contract check (see the test above).
    from core.currency_intent import fx_conversion_intent

    text = "weather in rome rn, 100 usd to rub, 18^2, and name one graph db. all 4 pls no essay"
    assert fx_conversion_intent(text) is not None
    assert currency_fast_path(text) is not None
    from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn

    assert (
        closed_semantic_contract_covers_turn(
            text, session_id="v2-repair", source_context={"surface": "api"}
        )
        is False
    )


def test_a_clean_conversion_still_claims():
    assert currency_fast_path("1000 rub to eur") is not None


# ── Class C: served truth outranks plan-time binding state ──────────────────


def _node_outcome(node_id: str, *, state=NodeLifecycle.SUCCEEDED) -> NodeOutcome:
    node = ConductorNode(node_id=node_id, operation="market_quote", request_text="x")
    return NodeOutcome(
        node=node, state=state, result={"ok": 1} if state is NodeLifecycle.SUCCEEDED else None
    )


def _binding_plan():
    r1 = RequirementRealization(
        realization_id="r1",
        requirement_id="req:1:market_quote",
        family="market_quote",
        source_surface="get berlin temp",
        display_subject="Berlin",
    )
    r2 = RequirementRealization(
        realization_id="r2",
        requirement_id="req:2:comparison",
        family="comparison",
        source_surface="tell me if its above 20c",
        display_subject="Berlin vs 20c",
        prerequisite_realization_ids=("r1",),
    )
    bindings = (
        RealizationBinding(
            "r1",
            non_execution=NonExecutionEvidence(NonExecutionReason.UNRESOLVED_SUBJECT),
        ),
        RealizationBinding("r2", bound_node_ids=("n-child",)),
    )
    return BoundExecutionPlan(RealizationLedger((r1, r2)), bindings)


def test_a_served_requirement_is_never_reported_as_refused():
    # THE CLASS C LAW. The plan-time binding said "unresolved subject", but a
    # node ran for the requirement and succeeded. Sabotage: make the reduction
    # trust the binding again and this test names the contradiction.
    plan = _binding_plan()
    reduced = reduce_bound_plan(
        plan,
        [_node_outcome("n-weather", state=NodeLifecycle.SUCCEEDED)],
        requirement_nodes={"req:1:market_quote": ("n-weather",)},
    )
    states = {o.realization.realization_id: o.state for o in reduced.outcomes}
    assert states["r1"] is RealizationState.SATISFIED
    assert reduced.failure_lines() == (), reduced.failure_lines()


def test_a_child_is_not_dependency_blocked_when_its_parent_was_served():
    plan = _binding_plan()
    reduced = reduce_bound_plan(
        plan,
        [
            _node_outcome("n-weather", state=NodeLifecycle.SUCCEEDED),
            # the child node genuinely failed — it must report ITS OWN failure,
            # not a phantom "prerequisite missing" under the served parent
            _node_outcome("n-child", state=NodeLifecycle.FAILED),
        ],
        requirement_nodes={
            "req:1:market_quote": ("n-weather",),
            "req:2:comparison": ("n-child",),
        },
    )
    states = {o.realization.realization_id: o.state for o in reduced.outcomes}
    assert states["r1"] is RealizationState.SATISFIED
    assert states["r2"] is RealizationState.EXECUTION_FAILED


def test_without_requirement_nodes_the_old_reduction_is_untouched():
    plan = _binding_plan()
    reduced = reduce_bound_plan(plan, [])
    states = {o.realization.realization_id: o.state for o in reduced.outcomes}
    assert states["r1"] is RealizationState.UNRESOLVED_SUBJECT


# ── Class D: "JSON only" binds the raw-output contract ──────────────────────


def test_json_only_engages_the_contract():
    contract = parse_raw_output_contract(
        'return JSON only {"x":1}; receipt must be separate metadata not appended text'
    )
    assert contract is not None
    assert contract.raw_only is True


def test_json_only_rejects_markdown_wrapping():
    # The Q172 defect: the model's fenced draft was served verbatim because no
    # contract engaged. With the contract bound, the fence is a violation the
    # repair path must unwrap.
    contract = parse_raw_output_contract('return JSON only {"x":1}')
    assert contract is not None and contract.no_markdown is True


def test_soft_json_phrasing_stays_soft():
    # Deliberately narrower than "as/in JSON": those remain preferences, not
    # byte-level contracts.
    assert parse_raw_output_contract("explain it as JSON with keys a and b") is None


# ── Operator watch-session repairs (2026-08-29 afternoon) ───────────────────


def test_multi_city_time_ask_answers_every_named_city():
    # THE operator's broken ask (watch session 2026-08-29T1150Z): answered
    # Rome only, Paris dropped. Sabotage: reduce the loop back to first-match
    # and this fails naming the dropped city.
    import datetime
    from zoneinfo import ZoneInfo

    from core.agent_runtime import fast_paths_utility as fpu

    frozen = datetime.datetime(2026, 8, 29, 14, 0, 0, tzinfo=ZoneInfo("Europe/Rome"))
    reply = fpu.date_time_fast_path(
        object(),
        "what time is in rome now and in paris?",
        source_surface="api",
        session_id="v2-repair",
        now_utc=frozen,
    )
    assert reply is not None
    assert "Rome" in reply and "Paris" in reply, reply


def test_multi_city_time_zone_math_is_per_city():
    import datetime
    from zoneinfo import ZoneInfo

    from core.agent_runtime import fast_paths_utility as fpu

    frozen = datetime.datetime(2026, 8, 29, 14, 0, 0, tzinfo=ZoneInfo("Europe/Rome"))
    reply = fpu.date_time_fast_path(
        object(),
        "what time is it in tokyo and in new york?",
        source_surface="api",
        session_id="v2-repair",
        now_utc=frozen,
    )
    assert "21:00 JST" in reply and "08:00 EDT" in reply, reply


def test_single_city_and_no_place_clock_answers_unchanged():
    import datetime
    from zoneinfo import ZoneInfo

    from core.agent_runtime import fast_paths_utility as fpu

    frozen = datetime.datetime(2026, 8, 29, 14, 0, 0, tzinfo=ZoneInfo("Europe/Rome"))
    single = fpu.date_time_fast_path(
        object(), "what time is it in rome now?",
        source_surface="api", session_id="v2-repair", now_utc=frozen,
    )
    assert single == "Current time in Rome is 14:00 CEST."
    assert fpu.date_time_fast_path(
        object(), "what time is the meeting in rome?",
        source_surface="api", session_id="v2-repair", now_utc=frozen,
    ) is not None  # event-time ask still served, not treated as a clock read


def test_number_only_phrasings_bind_the_strict_contract():
    from core.raw_output_contract import parse_raw_output_contract

    for text in (
        "what is 2 + 2? answer with just the number",
        "say one thousand fifty six time twenty five result in number only",
        "just the number no units: 37+18",
        "how much is 15 percent of 80? give just the number",
    ):
        contract = parse_raw_output_contract(text)
        assert contract is not None and contract.raw_only and contract.no_markdown, text


def test_number_only_quantity_references_stay_prose():
    from core.raw_output_contract import parse_raw_output_contract

    for text in (
        "only the number of votes matters here",
        "explain the numbers only policy",
        "we accept numbers only from trusted sources",
        "the numbers only rule applies to inputs",
    ):
        assert parse_raw_output_contract(text) is None, text
