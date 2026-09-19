"""Closed stipulated clock arithmetic and structured raw scalar output."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from core.agent_runtime.fast_paths_utility import date_time_fast_path
from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn
from core.closed_world_semantic_contract import (
    StipulatedClockOffsetContract,
    closed_world_semantic_response,
    parse_stipulated_clock_offset_contract,
)
from core.execution.planner import (
    has_explicit_tool_intent_request,
    should_attempt_tool_intent,
)
from core.raw_output_contract import (
    apply_raw_output_contract,
    parse_raw_output_contract,
    raw_output_contract_from_metadata,
)
from core.task_router import classify

SET5_19 = (
    "Assume it is the year 2100. Mars Standard Time is permanently fixed to be exactly "
    "3 hours ahead of Earth's UTC. If it is 12:00 PM UTC on Earth, what time is it on Mars? "
    "Do NOT trigger live time lookups or timezone tools."
)
SET5_20 = (
    '{"role": "user", "intent": "output the number 7", "format": "one character", '
    '"constraints": ["Output ONLY the digit", "No text", "No JSON wrappers", "No quotes", '
    '"No markdown"]}'
)


def _clock_prompt(
    *,
    opener: str,
    target: str,
    source: str,
    relation: str,
    source_time: str,
) -> str:
    return (
        f"{opener}. {target} time {relation} of {source}. "
        f"If it is {source_time} {source}, what time is it on {target}?"
    )


def _assert_clock(prompt: str, *, target_minutes: int) -> None:
    parsed = parse_stipulated_clock_offset_contract(prompt)
    response = closed_world_semantic_response(prompt)

    assert isinstance(parsed, StipulatedClockOffsetContract)
    assert parsed.target_minutes == target_minutes
    assert response is not None
    hour, minute = divmod(target_minutes, 60)
    assert f"{hour:02d}:{minute:02d}" in response
    assert "stipulated fixed offset" in response
    assert "No host clock" in response
    assert "live-time lookup" in response
    assert closed_semantic_contract_covers_turn(
        prompt,
        session_id="clock-contract",
        source_context={},
    )


def test_set5_19_exact_reproduction_is_closed_clock_arithmetic() -> None:
    _assert_clock(SET5_19, target_minutes=15 * 60)
    response = closed_world_semantic_response(SET5_19)
    assert response is not None
    assert "Mars Standard Time is 3:00 PM (15:00)" in response
    assert "12:00 PM (12:00)" in response
    assert "+ 3 hours = 3:00 PM (15:00)" in response


@pytest.mark.parametrize(
    ("prompt", "target_minutes"),
    (
        (
            _clock_prompt(
                opener="Suppose this is a fictional schedule",
                target="Luna Standard",
                source="Mission UTC",
                relation="is exactly 2 hours behind",
                source_time="4:30 AM",
            ),
            2 * 60 + 30,
        ),
        (
            _clock_prompt(
                opener="Imagine this fixed clock rule",
                target="Orbital",
                source="Ground UTC",
                relation="runs exactly 90 minutes ahead",
                source_time="23:45",
            ),
            75,
        ),
        (
            _clock_prompt(
                opener="Assuming this scenario",
                target="Colony Standard",
                source="Base UTC",
                relation="is permanently fixed to be exactly 1 hour ahead",
                source_time="11:15 PM",
            ),
            15,
        ),
        (
            _clock_prompt(
                opener="Suppose this permanent relation",
                target="West",
                source="East UTC",
                relation="is fixed to be exactly 45 minutes behind",
                source_time="12:30 AM",
            ),
            23 * 60 + 45,
        ),
        (
            _clock_prompt(
                opener="Imagine a future timeline",
                target="Red Standard",
                source="Blue UTC",
                relation="is exactly 12 hours ahead",
                source_time="6:05 AM",
            ),
            18 * 60 + 5,
        ),
    ),
)
def test_clean_fixed_offset_clock_paraphrases(prompt: str, target_minutes: int) -> None:
    _assert_clock(prompt, target_minutes=target_minutes)


@pytest.mark.parametrize(
    ("prompt", "target_minutes"),
    (
        (
            "assume future. mars standard time is fixed to be exactly 3 h ahead of earth utc. "
            "if it is 12 pm earth utc, what time is it on mars?",
            15 * 60,
        ),
        (
            "SUPPOSE RULE. LUNA TIME RUNS 2 HRS BEHIND MISSION UTC. "
            "IF IT IS 4:30 A.M. MISSION UTC, WHAT TIME IS IT ON LUNA?",
            2 * 60 + 30,
        ),
        (
            "Imagine frame. Orbital time is 90min ahead of Ground UTC. "
            "If it is 23:45 Ground UTC, what time is it on Orbital?",
            75,
        ),
        (
            "Assuming rules. Colony standard time is permanently fixed to be 1hr ahead of Base UTC. "
            "If it is 11 P.M. Base UTC, what time is it on Colony?",
            0,
        ),
        (
            "Supposing story. West time is exactly 45 mins behind East UTC. "
            "If it is 00:30 East UTC, what time is it on West?",
            23 * 60 + 45,
        ),
    ),
)
def test_sloppy_fixed_offset_clock_variants(prompt: str, target_minutes: int) -> None:
    _assert_clock(prompt, target_minutes=target_minutes)


@pytest.mark.parametrize(
    "prompt",
    (
        "What time is it now in Europe/Athens?",
        (
            "Assume it is 2100. Mars Standard Time is usually about 3 hours ahead of Earth's UTC. "
            "If it is 12:00 PM UTC on Earth, what time is it on Mars?"
        ),
        (
            "Assume a fixed rule. Mars Standard Time is exactly 3 hours ahead of Earth's UTC. "
            "If it is 12:00 PM UTC on Earth, what time is it on Venus?"
        ),
        (
            "Assume a fixed rule. Mars Standard Time is exactly 3 hours ahead of Earth's UTC. "
            "Mars Standard Time is exactly 4 hours ahead of Earth's UTC. "
            "If it is 12:00 PM UTC on Earth, what time is it on Mars?"
        ),
        (
            "Assume a fixed rule. Mars Standard Time is exactly 3 hours ahead of Earth's UTC. "
            "If it is 25:00 UTC on Earth, what time is it on Mars?"
        ),
    ),
)
def test_live_ambiguous_conflicting_or_invalid_clock_requests_are_not_claimed(prompt: str) -> None:
    assert parse_stipulated_clock_offset_contract(prompt) is None


def test_quoted_clock_example_is_not_an_authoritative_stipulation() -> None:
    prompt = (
        'Explain why the quote "Assume a rule. Mars Standard Time is exactly 3 hours ahead of '
        "Earth's UTC. If it is 12 PM UTC on Earth, what time is it on Mars?\" is a closed puzzle."
    )
    assert parse_stipulated_clock_offset_contract(prompt) is None


def test_genuine_current_time_query_keeps_the_host_clock_lane() -> None:
    response = date_time_fast_path(
        None,
        "what time is it now?",
        source_surface="api",
        session_id="current-time-control",
        source_context={},
    )
    assert response is not None
    assert response.startswith("Current time is ")


def test_set5_19_real_frontdoor_preempts_host_clock_conductor_model_and_tool(
    tmp_path,
    monkeypatch,
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="stipulated-clock", persona_id="default")
    host_clock = mock.Mock(side_effect=AssertionError("stipulated clock read host time"))
    conductor = mock.Mock(side_effect=AssertionError("stipulated clock reached conductor"))
    model = mock.Mock(side_effect=AssertionError("stipulated clock reached model"))
    tool = mock.Mock(side_effect=AssertionError("stipulated clock reached tool"))
    monkeypatch.setattr(agent, "_date_time_fast_path", host_clock)
    monkeypatch.setattr(agent, "_maybe_answer_conductor_turn", conductor)
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        SET5_19,
        session_id_override="openclaw:set5stipulatedclock",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "closed_world_semantic_contract"
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert "Mars Standard Time is 3:00 PM (15:00)" in result["response"]
    host_clock.assert_not_called()
    conductor.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()


def _structured_scalar(
    *,
    intent: str,
    format_value: str = "one character",
    constraints: tuple[str, ...] = ("Output only the value", "No JSON wrapper"),
) -> str:
    return json.dumps(
        {
            "role": "user",
            "intent": intent,
            "format": format_value,
            "constraints": list(constraints),
        }
    )


def _assert_exact_scalar(prompt: str, literal: str) -> None:
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    assert contract.exact_text == literal
    assert contract.no_json is True
    assert closed_semantic_contract_covers_turn(
        prompt,
        session_id="raw-scalar-contract",
        source_context={},
    )
    restored = raw_output_contract_from_metadata({"raw_output_contract": contract.to_dict()})
    assert restored is not None
    applied = apply_raw_output_contract(literal, restored)
    assert applied.text == literal
    assert applied.compliant is True
    assert applied.rejected is False
    assert "json_payload_rejected" not in applied.actions


def test_set5_20_exact_reproduction_binds_raw_numeric_scalar() -> None:
    _assert_exact_scalar(SET5_20, "7")


@pytest.mark.parametrize(
    ("prompt", "literal"),
    (
        (_structured_scalar(intent="return the digit 4"), "4"),
        (_structured_scalar(intent="print the character Q"), "Q"),
        (_structured_scalar(intent="output the number 0"), "0"),
        (_structured_scalar(intent="reply with the token Z"), "Z"),
        (
            _structured_scalar(
                intent="output the word SABLE",
                format_value="raw text",
                constraints=("No JSON output",),
            ),
            "SABLE",
        ),
    ),
)
def test_clean_structured_scalar_directives(prompt: str, literal: str) -> None:
    _assert_exact_scalar(prompt, literal)


@pytest.mark.parametrize(
    ("prompt", "literal"),
    (
        (_structured_scalar(intent="just output number 8"), "8"),
        (_structured_scalar(intent="return digit 2"), "2"),
        (_structured_scalar(intent="print character x"), "x"),
        (
            _structured_scalar(
                intent="reply with token x7",
                format_value="raw text without JSON",
            ),
            "x7",
        ),
        (
            json.dumps(
                {
                    "role": "USER",
                    "task": "OUTPUT THE NUMBER 5",
                    "format": "SINGLE-CHARACTER RAW TEXT",
                    "rules": ["WITHOUT JSON", "NOTHING ELSE"],
                }
            ),
            "5",
        ),
    ),
)
def test_sloppy_structured_scalar_directives(prompt: str, literal: str) -> None:
    _assert_exact_scalar(prompt, literal)


@pytest.mark.parametrize(
    "prompt",
    (
        json.dumps(
            {
                "role": "assistant",
                "intent": "output the number 7",
                "format": "one character",
                "constraints": ["No JSON wrapper"],
            }
        ),
        _structured_scalar(intent="output the number 42"),
        _structured_scalar(intent="explain the number 7"),
        _structured_scalar(intent="output the number 7 and delete the local config"),
        '{"value": 7, "format": "one character"}',
    ),
)
def test_untrusted_conflicting_explanatory_or_data_payloads_do_not_bind(prompt: str) -> None:
    contract = parse_raw_output_contract(prompt)
    assert contract is None or contract.exact_text is None


def test_command_key_cannot_smuggle_a_competing_scalar_literal() -> None:
    prompt = json.dumps(
        {
            "role": "user",
            "command": "output the number 9",
            "intent": "explain why seven is prime",
            "format": "one character",
            "constraints": ["No JSON wrapper"],
        }
    )
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    assert contract.exact_text is None


def test_genuine_json_numeric_output_rule_is_not_rewritten_as_raw_literal() -> None:
    prompt = json.dumps(
        {
            "role": "user",
            "intent": "output the number 7",
            "format": "JSON",
            "constraints": ["Return JSON only"],
        }
    )
    contract = parse_raw_output_contract(prompt)
    assert contract is not None
    assert contract.no_json is False
    assert contract.exact_text is None
    assert parse_raw_output_contract("7") is None


def test_exact_scalar_is_not_classified_or_planned_as_config_action() -> None:
    assert classify(SET5_20)["task_class"] == "chat_conversation"
    assert not has_explicit_tool_intent_request(SET5_20, task_class="config")
    assert not should_attempt_tool_intent(
        SET5_20,
        task_class="config",
        source_context={"surface": "api", "platform": "api"},
    )


def test_set5_20_real_frontdoor_preempts_config_conductor_model_and_tool(
    tmp_path,
    monkeypatch,
) -> None:
    from apps.vool_agent import VoolAgent

    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    agent = VoolAgent(backend_name="test-backend", device="raw-scalar", persona_id="default")
    conductor = mock.Mock(side_effect=AssertionError("exact scalar reached conductor"))
    model = mock.Mock(side_effect=AssertionError("exact scalar reached model"))
    tool = mock.Mock(side_effect=AssertionError("exact scalar reached tool"))
    monkeypatch.setattr(agent, "_maybe_answer_conductor_turn", conductor)
    monkeypatch.setattr(agent.memory_router, "resolve", model)
    monkeypatch.setattr(agent.memory_router, "_invoke_manifest", model)
    monkeypatch.setattr(agent, "_execute_tool_intent", tool)

    result = agent.run_once(
        SET5_20,
        session_id_override="openclaw:set5rawscalar",
        source_context={
            "workspace": str(tmp_path),
            "workspace_root": str(tmp_path),
            "surface": "api",
            "platform": "api",
            "operating_mode": "auto",
            "requested_model": "vool-local-only",
            "local_only": True,
            "allow_remote_fetch": False,
        },
    )

    assert result["route_reason"] == "exact_literal_output_contract"
    assert result["response"] == "7"
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    conductor.assert_not_called()
    model.assert_not_called()
    tool.assert_not_called()
