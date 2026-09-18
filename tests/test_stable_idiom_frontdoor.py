"""Frozen Set 3 idioms stay on the reviewed local-reference front door."""

from __future__ import annotations

import pytest

from tests.live.runtime_model_gauntlet import load_cases

FROZEN_IDIOMS = (
    (load_cases((3,))[26].prompt, ("deleting", "dropping", "wiping", "resetting", "destroying")),
    (load_cases((3,))[27].prompt, ("in the past", "settled", "no longer", "move on")),
)


@pytest.mark.parametrize(("prompt", "required"), FROZEN_IDIOMS)
def test_exact_frozen_idiom_frontdoors_return_complete_reviewed_meanings(
    tmp_path,
    prompt: str,
    required: tuple[str, ...],
) -> None:
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="stable-idiom-test", persona_id="default")
    outcome = agent._handle_turn_frontdoor(
        raw_user_input=prompt,
        effective_input=prompt,
        normalized_input=prompt.casefold(),
        source_surface="api",
        session_id=f"stable-idiom-{abs(hash(prompt))}",
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
    for phrase in required:
        assert phrase in result["response"]
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert {"model", "web", "tool_loop"}.issubset(result["route_skips"])

