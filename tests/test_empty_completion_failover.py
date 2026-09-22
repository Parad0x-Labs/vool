"""An empty model response is a failed lane, and a generated reply must reach the user.

Both live failures on 2026-07-28 shared one root: the runtime treated an empty provider response
as a *successful* turn.

* `_validate_contract("plain_text", "")` returns `ok=True` — there are no foreign markers in
  nothing — so the decision was built `validation_state="valid"`, `used_model=True`,
  `output_text=""` and returned immediately. The candidate loop escalates on
  `error / adapter is None / response is None` and on `contract_failed`; an HTTP 200 with no
  content is none of those, so no retry and no next candidate ever ran.
* The reason the completions were empty: `nvidia/nemotron-3-ultra-550b-a55b:free` reasons before
  answering and was sent plain chat turns at `max_tokens` 284 ("Hey"), 440 (the audit turn) and
  356. It spent the budget thinking. The reserve added earlier that day covered only `tool_intent`
  turns *carrying tools*, and its model-family check matched `qwen3` only — so the model that
  actually hit this matched nothing and got no reserve.
* Even then the runtime DID produce an honest fallback: the conversation log holds a 126-char reply
  at 21:02:39 that the user never saw. `saw_model_output` latches on the first chunk *event*, before
  any content check, and taking that branch discards the buffered response — along with every
  post-stream rewrite.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import runtime_flags
from core.agent_runtime.builder import controller

# The models from the failing session, plus a non-reasoning control.
FAILING_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
CONTROL_MODEL = "openai/gpt-5-nano"


def _adapter(model_name: str, *, metadata: dict | None = None) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id=f"openrouter-byok:{model_name}",
            model_name=model_name,
            metadata=dict(metadata or {}),
            runtime_config={"base_url": "https://openrouter.ai/api"},
        )
    )


def _budget(
    model_name: str,
    *,
    mode: str = "plain_text",
    tools=(),
    requested: int = 284,
    reasoning_mode: str = "auto",
) -> int:
    adapter = _adapter(model_name)
    request = ModelRequest(
        task_kind="chat",
        prompt="Hey",
        output_mode=mode,
        tools=tuple(tools),
        reasoning_mode=reasoning_mode,
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        return adapter._cloud_tool_output_budget(request, requested)


# --------------------------------------------------------------------------------------
# The reasoning reserve reaches ordinary chat, and the right models
# --------------------------------------------------------------------------------------


def test_the_model_that_failed_is_recognised_as_reasoning() -> None:
    """It matched nothing before, which is why the earlier fix missed it entirely."""

    assert _adapter(FAILING_MODEL)._is_thinking_capable_model() is True


@pytest.mark.parametrize(
    "model_name", ["qwen3:8b", FAILING_MODEL, "deepseek/deepseek-r1", "qwen/qwq-32b"]
)
def test_reasoning_families_are_recognised(model_name: str) -> None:
    assert _adapter(model_name)._is_thinking_capable_model() is True


@pytest.mark.parametrize("model_name", [CONTROL_MODEL, "vool-qwen3-30b-a3b:nothink", "qwen3-no-think:8b"])
def test_non_reasoning_and_opted_out_models_are_not(model_name: str) -> None:
    assert _adapter(model_name)._is_thinking_capable_model() is False


def test_a_provider_declaring_reasoning_support_is_believed_over_the_name() -> None:
    adapter = _adapter("some/unknown-model", metadata={"supported_parameters": ["reasoning"]})
    assert adapter._is_thinking_capable_model() is True


def test_a_plain_chat_turn_gets_room_to_think() -> None:
    """284 tokens was the actual budget on the "Hey" turn that came back empty."""

    assert _budget(FAILING_MODEL) > 2000


def test_a_non_reasoning_model_keeps_the_callers_budget() -> None:
    assert _budget(CONTROL_MODEL) == 284


def test_a_tool_turn_still_gets_the_larger_floor() -> None:
    from core.cloud_provider_contract import CloudToolDefinition

    tool = CloudToolDefinition(intent="x.y", name="x__y", description="d", parameters={})
    assert _budget(FAILING_MODEL, mode="tool_intent", tools=(tool,), requested=700) >= 3000


def test_the_reserve_is_off_when_the_flag_is_off() -> None:
    adapter = _adapter(FAILING_MODEL)
    request = ModelRequest(task_kind="chat", prompt="Hey", output_mode="plain_text")
    with runtime_flags.override("cloud_tool_reasoning_reserve", False):
        assert adapter._cloud_tool_output_budget(request, 284) == 284


# --------------------------------------------------------------------------------------
# The reserve must not be billed when the call explicitly disabled reasoning AND the wire
# actually honours that (2026-08-05 fix). It must still apply when the call leaves reasoning
# on auto/required, or when disabling it is only caller intent that never reaches the wire.
# --------------------------------------------------------------------------------------


def test_the_reserve_is_skipped_when_reasoning_is_explicitly_disabled_on_a_lane_that_honours_it() -> None:
    """`FAILING_MODEL`'s adapter is on the openrouter-byok lane (base_url host openrouter.ai), so
    `reasoning_mode="disabled"` really does reach the wire as `reasoning: {"enabled": False}` --
    the exact stepped-audit nominate/challenge shape (`stepped_audit.py` sets this on every call).
    Live-verified 2026-08-05: 12/12 real nominate trials against this exact model on this exact
    lane finished `finish_reason: "stop"` with no reasoning burn observed, so the reserve this
    skips was never once needed."""

    assert _budget(FAILING_MODEL, requested=3000, reasoning_mode="disabled") == 3000


def test_the_reserve_still_applies_when_disabling_reasoning_never_reaches_the_wire() -> None:
    """Intent alone is not enough. A lane that neither declares reasoning support nor is
    OpenRouter never receives the `reasoning: {"enabled": False}` field (see
    `_build_openai_payload`'s own gate), so the model can still spend the budget reasoning exactly
    as before -- dropping the reserve here would reopen the empty-completion failure at the top of
    this file. `qwen3` in the name makes `_is_thinking_capable_model()` true independent of the
    lane, isolating the lane as the only variable."""

    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_id="some-other-cloud:qwen3-remote",
            model_name="qwen3:8b-remote",
            metadata={},
            runtime_config={"base_url": "https://api.example.com/v1"},
        )
    )
    request = ModelRequest(
        task_kind="chat", prompt="Hey", output_mode="plain_text", reasoning_mode="disabled"
    )
    with runtime_flags.override("cloud_tool_reasoning_reserve", True):
        assert adapter._cloud_tool_output_budget(request, 3000) == 3000 + 2048


def test_the_reserve_still_applies_when_reasoning_is_left_on_auto() -> None:
    """A call that does not opt out of reasoning is untouched by the 2026-08-05 fix -- this is
    the pre-existing behaviour this test suite already pinned, restated here as the explicit
    control for the two tests above."""

    assert _budget(FAILING_MODEL, requested=3000, reasoning_mode="auto") == 3000 + 2048


# --------------------------------------------------------------------------------------
# One family definition, shared
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_name",
    ["qwen3:8b", "qwen2.5:7b", "deepseek-r1:14b", "vool-qwen3-30b-a3b:nothink", "nemotron-3-nano"],
)
def test_the_adapter_and_the_builder_agree(model_name: str) -> None:
    """Two places decide this; a drift between them is how a model gets missed."""

    assert controller.is_thinking_capable_model(model_name) == _adapter(model_name)._is_thinking_capable_model()


def test_the_family_list_is_shared_not_duplicated() -> None:
    assert "nemotron" in controller.THINKING_MODEL_MARKERS
    assert "qwen3" in controller.THINKING_MODEL_MARKERS


# --------------------------------------------------------------------------------------
# An empty completion escalates instead of being served
# --------------------------------------------------------------------------------------


def test_an_empty_completion_is_not_a_valid_contract_in_the_router() -> None:
    """The decision must be marked failed so the existing failover runs.

    Asserted on the source because the surrounding loop needs a live provider to exercise; the
    behaviour under test is the classification, not the HTTP call.
    """

    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "core" / "memory_first_router.py").read_text(
        encoding="utf-8"
    )
    assert "empty_completion" in source
    assert 'decision.validation_state = "contract_failed"' in source


def test_the_streaming_surface_tracks_delivered_content_not_chunk_events() -> None:
    """Empty chunk events cannot discard the final guarded answer or duplicate it."""
    from tests.test_streamed_monologue_never_reaches_the_screen import _drive_transport

    assert _drive_transport(["", ""], buffered_response="The guarded answer.") == "The guarded answer."
    assert _drive_transport(["The guarded answer."], buffered_response="The guarded answer.") == "The guarded answer."
