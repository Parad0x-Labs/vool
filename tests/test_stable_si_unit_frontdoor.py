from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime.turn_frontdoor import closed_semantic_contract_covers_turn


def test_si_membership_frontdoor_has_zero_model_tool_or_web_calls(tmp_path) -> None:
    prompt = "Which of N, blip, Sv, and wobble are recognized SI unit symbols?"
    agent = VoolAgent(backend_name="test-backend", device="si-reference-test", persona_id="default")

    assert closed_semantic_contract_covers_turn(
        prompt,
        session_id="si-reference-frontdoor",
        source_context={"workspace": str(tmp_path)},
    )

    with mock.patch.object(agent, "_execute_tool_intent") as execute_tool:
        outcome = agent._handle_turn_frontdoor(
            raw_user_input=prompt,
            effective_input=prompt,
            normalized_input=prompt.casefold(),
            source_surface="api",
            session_id="si-reference-frontdoor",
            source_context={
                "workspace": str(tmp_path),
                "workspace_root": str(tmp_path),
                "surface": "api",
                "operating_mode": "auto",
                "requested_model": "vool-local-only",
                "local_only": True,
            },
            persona=None,
            interpreted=None,
        )

    execute_tool.assert_not_called()
    result = (outcome or {}).get("result")
    assert result is not None
    assert result["route_reason"] == "stable_si_unit_reference_contract"
    assert "N (newton)" in result["response"]
    assert "Sv (sievert)" in result["response"]
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert {"model", "web", "tool_loop"}.issubset(result["route_skips"])
