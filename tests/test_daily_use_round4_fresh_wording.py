"""Round-4 daily-use classes, proven on wording that is not the sentence that failed.

Measured through the served rig on 2026-09-07 (scripted rig provider, certified; fixtures/daily_use_prompts_round1.json):

* "my code says IndexError: list index out of range, why does that happen" -- classified debugging; the
  adaptive-research lane asked the retrieval door, the door widened the turn to current-information, two
  searches burned 97 s on engines that returned nothing, and the publication gate refused the model's
  answer for lacking retrieved support. The requirements authority had read the turn as stable knowledge.
* "write me a snake game i can run in the browser, one html file" -- a build instruction with no
  destination fell to the builder's canned "I do not have a real bounded builder path" while
  "lets build a tetris game in python" was answered by the model.
* "how many solana one eth buys" -- a bare ratio was promoted to a purchase derivation that binds to
  nothing (the operand-roles law leaves it to the expression path).
"""

from __future__ import annotations

from unittest import mock

import pytest

STABLE_DEBUGGING = (
    "why do i get a KeyError in python when the key is clearly there",
    "what does 'segmentation fault (core dumped)' mean and how do i start debugging it",
)
CURRENT_INFORMATION = "what did the python steering council announce this week"


def _widen_by(source_context: dict, text: str, lane: str) -> None:
    from core.retrieval_authority_gate import authorize_retrieval

    assert authorize_retrieval(source_context, text, lane=lane, proposal_reason="test"), lane


@pytest.mark.parametrize("question", STABLE_DEBUGGING)
def test_a_roam_that_finds_nothing_retracts_its_own_widening(question: str) -> None:
    """The lane's ask widens the turn (that is the door's law, unchanged); when the retrieval
    finds nothing the widening is retracted before synthesis, so the authority's stable-knowledge
    reading decides publication and the model's answer is not refused for missing support."""
    from core.execution_requirements import (
        current_requirement_record,
        requirements_for,
        retract_provisional_escalation,
    )
    from core.grounding_lifecycle import lifecycle_for_context

    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    assert requirements_for(question, source_context=context).current_information_required is False
    _widen_by(context, question, "adaptive_research")
    assert current_requirement_record(context).requirements.current_information_required is True
    assert lifecycle_for_context(context) is not None, "the widening opened a lifecycle row"

    assert retract_provisional_escalation(context, source="adaptive_research", reason="no_evidence", text=question)

    record = current_requirement_record(context)
    assert record.requirements.current_information_required is False
    assert any(code.endswith("retracted:adaptive_research:no_evidence") for code in record.requirements.reason_codes)
    assert lifecycle_for_context(context) is None, "the empty lifecycle row is withdrawn with it"


def test_a_widening_another_lane_also_holds_is_not_retracted() -> None:
    from core.execution_requirements import current_requirement_record, retract_provisional_escalation

    question = STABLE_DEBUGGING[0]
    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    _widen_by(context, question, "adaptive_research")
    _widen_by(context, question, "reasoning_fallback_search")

    assert retract_provisional_escalation(context, source="adaptive_research", reason="no_evidence") is False
    assert current_requirement_record(context).requirements.current_information_required is True


def test_a_turn_current_on_its_own_reading_is_never_retracted() -> None:
    from core.execution_requirements import current_requirement_record, retract_provisional_escalation

    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    _widen_by(context, CURRENT_INFORMATION, "adaptive_research")

    assert retract_provisional_escalation(context, source="adaptive_research", reason="no_evidence") is False
    assert current_requirement_record(context).requirements.current_information_required is True


def test_nothing_is_retracted_after_the_synthesis_freeze() -> None:
    from core.execution_requirements import (
        begin_synthesis_freeze,
        current_requirement_record,
        retract_provisional_escalation,
    )

    question = STABLE_DEBUGGING[1]
    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    _widen_by(context, question, "adaptive_research")
    begin_synthesis_freeze(context, question)

    assert retract_provisional_escalation(context, source="adaptive_research", reason="no_evidence") is False
    assert current_requirement_record(context).requirements.current_information_required is True


WORLD_FACTS = (
    "Now compare the VW Passat and the VW Golf in detail: production periods, sales, regions, engines and prices.",
    "verify whether telegram allows unlimited bots per phone number",
    "Compare Vilnius and Riga: population, area, founding year and airport passengers.",
)
CONCEPTS = (
    "compare a list and a tuple in python",
    "what's the difference between TCP and UDP",
    *STABLE_DEBUGGING,
)


@pytest.mark.parametrize("question", WORLD_FACTS)
def test_a_world_facts_turn_keeps_its_widening_when_retrieval_finds_nothing(question: str) -> None:
    """The comparison's substance is facts in the world. An empty retrieval is then a fact the
    publication gate must weigh -- the model's memory of sales figures is not an answer -- so the
    provisional widening is NOT retracted. Routing is unchanged: the turn is still DIRECT."""
    from core.agent_runtime.grounded_mode import AnswerMode, answer_mode_for
    from core.execution_requirements import current_requirement_record, requirements_for, retract_provisional_escalation

    needs = requirements_for(question)
    assert needs.answer_mode == "DIRECT" and needs.external_evidence_required is False, needs
    assert needs.world_facts_requested is True
    assert answer_mode_for(question) is AnswerMode.DIRECT
    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    _widen_by(context, question, "adaptive_research")

    assert retract_provisional_escalation(context, source="adaptive_research", reason="no_evidence", text=question) is False
    assert current_requirement_record(context).requirements.current_information_required is True


@pytest.mark.parametrize("question", CONCEPTS)
def test_a_conceptual_question_is_not_read_as_world_facts(question: str) -> None:
    from core.execution_requirements import requirements_for

    assert requirements_for(question).world_facts_requested is False, question


def test_a_non_provisional_lane_cannot_retract() -> None:
    from core.execution_requirements import current_requirement_record, retract_provisional_escalation

    question = STABLE_DEBUGGING[0]
    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    _widen_by(context, question, "reasoning_fallback_search")

    assert retract_provisional_escalation(context, source="reasoning_fallback_search", reason="x") is False
    assert current_requirement_record(context).requirements.current_information_required is True


@pytest.mark.parametrize("question", STABLE_DEBUGGING)
def test_the_roamer_retracts_through_the_real_adaptive_research_path(make_agent, question: str) -> None:
    """End to end through `adaptive_research`: the door widens, the (patched) search returns nothing,
    the roam ends with no notes, and the requirement is the authority's own again."""
    from core.execution_requirements import current_requirement_record

    agent = make_agent()
    context = {"surface": "api", "platform": "api", "allow_remote_fetch": True}
    with mock.patch("core.curiosity_roamer.WebAdapter.planned_search_query", return_value=[]):
        result = agent.curiosity.adaptive_research(
            task_id="round4-roam",
            user_input=question,
            classification={"task_class": "debugging"},
            interpretation=None,
            source_context=context,
        )

    record = current_requirement_record(context)
    if result.rounds:
        assert "requirement_retracted" in result.actions_taken, result.to_dict()
        assert record is not None and record.requirements.current_information_required is False
    else:
        # The roamer's own decision declined to search; nothing widened, nothing to retract.
        assert record is None or record.requirements.current_information_required is False


def test_a_current_information_question_still_reaches_the_roamer(make_agent) -> None:
    from core.curiosity_roamer import AdaptiveResearchResult

    agent = make_agent()
    curiosity = mock.Mock(return_value=AdaptiveResearchResult(enabled=False, reason="control", strategy="not_needed"))
    agent.curiosity.adaptive_research = curiosity  # type: ignore[method-assign]

    agent._collect_adaptive_research(
        task_id="round4-current",
        query_text=CURRENT_INFORMATION,
        classification={"task_class": "research"},
        interpretation=None,
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": True},
    )

    curiosity.assert_called_once()


def test_a_stable_knowledge_debugging_turn_makes_no_web_call_end_to_end(make_agent) -> None:
    agent = make_agent()
    result = agent.run_once(
        STABLE_DEBUGGING[0],
        session_id_override="round4-debugging-no-web",
        source_context={"surface": "api", "platform": "api", "allow_remote_fetch": True},
    )
    assert result["web_calls"] == 0
    assert "it needed current information" not in str(result.get("response") or "")


# -- builder: an artifact request with no destination goes to the model, not to a canned refusal ------

DESTINATIONLESS_ARTIFACTS = (
    "write me a minesweeper game as one html file i can double click",
    "write a python script that renames all my photos by date taken, single file please",
    "generate a bash script that backs up my Documents folder to an external drive",
)


def _profile(agent, text: str) -> dict:
    return agent._builder_controller_profile(
        effective_input=text,
        classification={"task_class": "coding"},
        interpretation=None,
        source_context={"workspace": "/tmp/ws", "workspace_root": "/tmp/ws", "operating_mode": "auto"},
    )


@pytest.mark.parametrize("text", DESTINATIONLESS_ARTIFACTS)
def test_a_destinationless_artifact_request_is_not_the_builders_to_refuse(make_agent, text: str) -> None:
    profile = _profile(make_agent(), text)
    assert profile.get("should_handle") is False, profile
    assert profile.get("mode") != "unsupported"


@pytest.mark.parametrize(
    ("text", "mode"),
    [
        ("make a tiny flask app in one file that shows the time", "model_build"),
        ("build a todo app with react and tests in ./todo", "model_build"),
        ("Create notes.txt in this project containing hello.", "workflow"),
    ],
)
def test_builds_with_a_destination_or_a_product_keep_their_lane(make_agent, text: str, mode: str) -> None:
    profile = _profile(make_agent(), text)
    assert profile.get("should_handle") is True
    assert profile.get("mode") == mode


# -- purchase grammar: a bare ratio is the expression path's; a payer with a beneficiary is a purchase ---


@pytest.mark.parametrize(
    "text",
    [
        "work out how many ada one sol buys today",
        "how many litecoin one bitcoin buys",
        "tell me how many grams of silver one ounce of gold buys",
    ],
)
def test_a_bare_ratio_is_not_promoted_to_a_purchase(text: str) -> None:
    from core.conductor.operations import asks_for_a_purchasable_amount

    assert asks_for_a_purchasable_amount(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "2 eth or 5 sol buy me how many barrels of brent?",
        "how many litecoin would 0.5 btc get me",
        "how many litres of crude oil can 2 BNB buy?",
        "how many barrels of WTI would 3 SOL or 0.05 BTC get me today?",
    ],
)
def test_a_payer_with_a_beneficiary_or_a_choice_is_a_purchase(text: str) -> None:
    from core.conductor.operations import asks_for_a_purchasable_amount, resolve_purchase_roles

    assert asks_for_a_purchasable_amount(text) is True
    roles = resolve_purchase_roles(text)
    assert roles is not None and roles.shape == "direct" and roles.target is not None, roles


# -- project inspection in plain words routes to the audit lane; navigation stays navigation -------

PROJECT_AUDITS = (
    "look through my project and tell me which files are huge monoliths worth splitting",
    "scan this project for oversized files that should be broken into smaller modules",
    "review this codebase: which functions are never called and where is the logic broken?",
    "check my project, count the source files over 300 lines and say which to split",
    "run through my repo and flag anything that looks dead",
    # "monolith" alone must carry the verdict: no other judgement word in the sentence.
    "look through my project and list the monoliths",
)
NOT_AUDITS = (
    "where does this project start? walk me through the entry point and what it calls first",
    "find the implementation of the retry handler and explain how it works",
    "check my project plan for the garden and see if the timeline is sound",
    "review my grandmother's recipe folder for weaknesses",
    "my project is due friday, can you plan my week",
)


@pytest.mark.parametrize("text", PROJECT_AUDITS)
def test_plain_words_project_inspection_is_an_audit(text: str) -> None:
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    assert looks_like_code_audit_request(text) is True


@pytest.mark.parametrize("text", NOT_AUDITS)
def test_navigation_and_non_code_projects_are_not_audits(text: str) -> None:
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    assert looks_like_code_audit_request(text) is False


# -- the grain: a literal multi-file write is one demand; a constraint rides; a side ask splits -----


def test_a_literal_multi_file_write_mints_one_demand_the_write_lane_owns() -> None:
    from core.agent_runtime.answer_coverage import demand_units
    from core.agent_runtime.demand_ownership import demand_coverage

    text = "Create exactly two files: notes.txt, todo.txt. Put HELLO, WORLD respectively. Nothing else."
    assert len(demand_units(text)) == 1
    coverage = demand_coverage(text)
    assert coverage.mixed is False
    assert coverage.per_unit_lanes == (("workspace_write_workflow",),)


def test_a_write_beside_a_side_ask_still_splits_and_keeps_its_path() -> None:
    from core.agent_runtime.answer_coverage import demand_units
    from core.execution.write_demand import resolve_write_demand

    text = "Create a.txt containing hi. Also, what is the weather in Rome right now?"
    assert len(demand_units(text)) >= 2
    demand = resolve_write_demand(text)
    assert [p["arguments"]["path"] for p in demand.write_payloads()] == ["a.txt"]


def test_a_constraint_sentence_rides_the_request_before_it() -> None:
    """Contract 2026-09-08: a constraint is not a request. It mints as a `constraint` unit attached
    to the request it qualifies, so it can never become an unanswered slot -- and the request keeps
    it in its execution text."""
    from core.agent_runtime.answer_coverage import demand_units, interpret_request
    from core.agent_runtime.demand_ownership import execution_units

    text = "Rename the photos by date taken. Leave every other file alone."
    interpretation = interpret_request(text)
    requests = demand_units(text)
    assert [unit.text for unit in requests] == ["Rename the photos by date taken."]
    constraint = interpretation.constraints
    assert [unit.text for unit in constraint] == ["Leave every other file alone."]
    assert constraint[0].depends_on == (requests[0].unit_id,)
    assert execution_units(text)[0][1] == text


@pytest.mark.parametrize(
    "question",
    [
        "what is hogging all my storage?",
        "something is filling up the drive, what is it",
    ],
)
def test_disk_consumer_wordings_reach_the_largest_tool(question: str) -> None:
    from core.execution.constants import machine_largest_intent

    assert machine_largest_intent(question) == "machine.find_largest"


@pytest.mark.parametrize(
    "text",
    [
        "find the monolith files in my project and tell me which to split",
        "locate the modules that are bloated and point me to the functions nobody calls",
    ],
)
def test_a_navigation_shaped_ask_that_requests_a_code_verdict_is_not_pure_navigation(text: str) -> None:
    from core.agent_runtime.investigation_intent import investigation_overrides_audit

    assert investigation_overrides_audit(text) is False
