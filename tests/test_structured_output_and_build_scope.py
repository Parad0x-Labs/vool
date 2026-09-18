"""Output shape and build scope must survive incidental validation prose."""

from pathlib import Path

import pytest

from core.agent_runtime.fast_paths_utility import looks_like_agentic_build_request
from core.ordinary_chat_response_guard import (
    constrain_ordinary_chat_output,
    ordinary_chat_output_policy,
)
from core.response_constraints import parse_clause_response_constraint

OWNER_BUILD = (Path(__file__).parent / "fixtures/pingkeeper_owner_build.txt").read_text()
OWNER_FORMAT = (Path(__file__).parent / "fixtures/portfolio_owner_format.txt").read_text()
NEW_BUILD = (
    "Create a compact but reliable inventory service called StockLedger.\n\n"
    "Acceptance conditions:\n\n"
    "- Store product counts in SQLite.\n"
    "- Commands:\n"
    "  - lookup: read the saved SKU.\n"
    "  - adjust: change the stock quantity.\n\n"
    "Validation procedure:\n\n"
    "1. Inspect the environment.\n"
    "2. Create the project and run its tests.\n"
    "3. Report expected versus observed persistence after restart.\n\n"
    "Actually build the service in the workspace."
)


@pytest.mark.parametrize("prompt", [OWNER_BUILD, NEW_BUILD])
def test_full_specification_has_one_owning_execution_lane(prompt):
    from core.agent_runtime.demand_ownership import demand_coverage

    coverage = demand_coverage(prompt)
    assert not coverage.mixed
    assert all("workspace_write_workflow" in lanes for lanes in coverage.per_unit_lanes)
    assert "versus" in " ".join(text for _, text in coverage.units)


def test_independent_question_after_specification_keeps_its_own_lane():
    from core.agent_runtime.demand_ownership import demand_coverage

    coverage = demand_coverage(NEW_BUILD + "\n\nAlso what is the weather in Berlin?")
    assert coverage.mixed


@pytest.mark.parametrize("prompt", [OWNER_BUILD, NEW_BUILD])
def test_runtime_does_not_dispatch_specification_as_separate_jobs(prompt, monkeypatch):
    from unittest.mock import Mock

    from apps.vool_agent import VoolAgent

    planner = Mock(return_value=[])
    monkeypatch.setattr("core.agent_runtime.demand_ownership.units_as_plan", planner)
    result = VoolAgent._maybe_answer_demand_owned_turn(
        object(), effective_input=" ".join(prompt.split()), raw_input=prompt,
        session_id="specification-gate", source_context={},
    )
    assert result is None
    planner.assert_not_called()


@pytest.mark.parametrize("prompt", [
    "Build a small but complete Telegram bot called PingKeeper and get it into a runnable state.\n"
    "Use Python 3, SQLite, and python-telegram-bot.\n"
    "Report exactly what was actually executed versus what could not be executed.\n"
    "Do not merely provide code snippets. Actually build and validate the project.",
    "Implement a CSV inventory service with persistent storage.\n"
    "Create the files and run the tests.\n"
    "Finish with a comparison of expected versus observed results.",
])
def test_build_instruction_survives_validation_comparison(prompt):
    assert looks_like_agentic_build_request(prompt)


@pytest.mark.parametrize("prompt", [
    "Should we build a bot or use an existing service?",
    "Compare building a CLI versus a web app. Do not write any code.",
    "Create notes.txt containing hello in this project.",
])
def test_discussion_and_single_file_do_not_promote_to_project(prompt):
    assert not looks_like_agentic_build_request(prompt)


@pytest.mark.parametrize("prompt", [
    OWNER_FORMAT,
    "Present the result in a compact table and finish with a one-paragraph explanation of the portfolio.",
    "Show warehouse quantities in a detailed comparison table. End with the stock shortfall.",
    "Use a compact table.",
])
def test_explicit_table_owns_shape_and_length(prompt):
    constraint = parse_clause_response_constraint(prompt)
    assert constraint is not None and constraint.presentation_format == "table"
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_conversation", output_mode="plain_text", user_text=prompt,
    )
    assert policy["max_words"] == ""
    response = "| Asset | Quantity |\n| --- | ---: |\n" + "\n".join(
        f"| Stock item {i} | {i + 100}.25 |" for i in range(24)
    ) + "\n\nEvery row retains its quantity."
    assert constrain_ordinary_chat_output(response, policy) == response
    from core.agent_runtime.response import _validate_final_chat_output

    context = {"ordinary_chat_output_policy": policy}
    assert _validate_final_chat_output(response, source_context=context) == response
    assert not context["response_control"]["final_ui"]["answer_completeness"]["incomplete"]


def test_ordinary_prose_still_has_a_bounded_default():
    policy = ordinary_chat_output_policy(
        prompt_profile="chat_conversation", output_mode="plain_text",
        user_text="Why is the sky blue?",
    )
    assert policy["max_words"] == "64"


@pytest.mark.parametrize("table", [
    "| Asset | Cost |\n| --- | --- |\n| EUR | $3,000.00 |\n| Gold | 0.9391 oz |",
    "| Sensor | Reading |\n| --- | --- |\n| Zone A | 18.375 C |\n| Zone B | 22.625 C |",
])
def test_prose_filters_preserve_retained_decimal_cells_and_line_breaks(table):
    from core.ordinary_chat_response_guard import (
        remove_unrequested_prior_turn_literals,
        remove_unsolicited_generic_follow_up,
    )

    assert remove_unsolicited_generic_follow_up(table) == table
    assert remove_unrequested_prior_turn_literals(
        table, {"prior_turn_literal_hashes": ["a" * 64]},
    ) == table
