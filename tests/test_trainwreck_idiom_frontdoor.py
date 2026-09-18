"""Frozen Set 2 trainwreck idiom stays on the reviewed local-reference front door."""

from __future__ import annotations

from tests.live.runtime_model_gauntlet import load_cases

FROZEN_SET2_28 = load_cases((2,))[27].prompt


def test_exact_frozen_trainwreck_prompt_is_closed_and_tool_free(tmp_path) -> None:
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="trainwreck-idiom", persona_id="default")
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=FROZEN_SET2_28,
        effective_input=FROZEN_SET2_28,
        normalized_input=FROZEN_SET2_28.casefold(),
        source_surface="api",
        session_id="trainwreck-idiom-frozen-set2-28",
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

    result = (outcome or {}).get("result")
    assert result is not None
    assert result["route_reason"] == "stable_metaphor_reference_contract"
    assert all(
        phrase in result["response"]
        for phrase in ("a disaster", "went very badly", "chaotic", "nonliteral")
    )
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert {"model", "web", "tool_loop"}.issubset(result["route_skips"])

