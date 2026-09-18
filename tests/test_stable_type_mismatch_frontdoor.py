from __future__ import annotations

import uuid
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime.turn_frontdoor import (
    _stable_category_error_admitted,
    closed_semantic_contract_covers_turn,
)
from core.semantic.preflight import semantic_preflight
from tests.live.runtime_model_gauntlet import load_cases


def test_frozen_html_mismatch_frontdoor_has_zero_model_tool_or_web_calls(tmp_path) -> None:
    prompt = load_cases((4,))[7].prompt
    preflight = semantic_preflight(prompt)
    agent = VoolAgent(backend_name="test-backend", device="type-mismatch-test", persona_id="default")

    assert _stable_category_error_admitted(preflight)
    assert closed_semantic_contract_covers_turn(
        prompt,
        session_id="type-mismatch-frontdoor",
        source_context={"workspace": str(tmp_path)},
        preflight=preflight,
    )

    with mock.patch.object(agent, "_execute_tool_intent") as execute_tool:
        outcome = agent._handle_turn_frontdoor(
            raw_user_input=prompt,
            effective_input=prompt,
            normalized_input=prompt.casefold(),
            source_surface="api",
            session_id="type-mismatch-frontdoor",
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
    assert result["route_reason"] == "stable_category_error_contract"
    assert "HTML is a markup language and document format" in result["response"]
    assert "not medicine or medication" in result["response"]
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0
    assert {"model", "web", "tool_loop"}.issubset(result["route_skips"])


def test_public_turn_reaches_html_mismatch_before_any_model_or_tool(
    tmp_path, monkeypatch
) -> None:
    prompt = load_cases((4,))[7].prompt
    monkeypatch.setenv("VOOL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("VOOL_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(
        "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("type mismatch reached planner")
        ),
    )
    agent = VoolAgent(
        backend_name="test-backend",
        device="type-mismatch-public",
        persona_id="default",
    )
    with mock.patch.object(
        agent,
        "_execute_tool_intent",
        side_effect=AssertionError("type mismatch reached tool"),
    ):
        result = agent.run_once(
            prompt,
            session_id_override=f"openclaw:type-mismatch-{uuid.uuid4().hex}",
            source_context={
                "workspace": str(tmp_path),
                "workspace_root": str(tmp_path),
                "surface": "api",
                "operating_mode": "auto",
                "requested_model": "vool-local-only",
                "local_only": True,
            },
        )

    assert result["route_reason"] == "stable_category_error_contract"
    assert "HTML is a markup language and document format" in result["response"]
    assert result["model_calls"] == 0
    assert result.get("web_calls", 0) == 0


def test_typed_mismatch_binding_resolves_only_its_own_deictic_reference() -> None:
    prompt = load_cases((4,))[7].prompt
    ambiguous = semantic_preflight(prompt, ambiguous_reference=True)

    assert not _stable_category_error_admitted(ambiguous)
    assert _stable_category_error_admitted(
        ambiguous,
        resolved_ambiguous_reference=True,
    )


def test_fictional_and_quoted_frames_do_not_receive_real_world_category_authority() -> None:
    fictional = semantic_preflight(
        "In a fictional game, HTML is explicitly a medicine. "
        "My doctor prescribed HTML for pain. Should I take it?"
    )
    quoted = semantic_preflight(
        'The broken test says "My doctor prescribed HTML for pain. Should I take it?" '
        "Explain why it failed."
    )

    assert not _stable_category_error_admitted(fictional)
    assert not _stable_category_error_admitted(quoted)
    assert not closed_semantic_contract_covers_turn(
        fictional.raw.text,
        session_id="fictional-type-mismatch",
        source_context={},
        preflight=fictional,
    )
    assert not closed_semantic_contract_covers_turn(
        quoted.raw.text,
        session_id="quoted-type-mismatch",
        source_context={},
        preflight=quoted,
    )
