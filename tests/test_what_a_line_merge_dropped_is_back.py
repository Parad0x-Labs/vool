"""Three fixes from the 0.7.7 train that the phone-on-chat merge (5e4fdf06) dropped without a decision.

That merge took the chat line's side file by file, and the chat line had branched before these were
made, so they fell out silently. Each guard here would fail on 247124ed.

- The verifier never picks a local model that is not ready while another is (Oct 29, fresh daemon:
  the unready qwen2.5:7b verifier loaded a second model and returned malformed verdicts).
- "What version are you on" names the model turns use, not the boot guess the check replaced.
- A phone that reached its own monthly cloud limit is told so, and where to raise it, instead of
  the generic sentence for an unknown code.
"""

from __future__ import annotations

from types import SimpleNamespace


def _local_truth(model_id: str, vram_gb: float):
    from core.provider_routing import ProviderCapabilityTruth

    return ProviderCapabilityTruth(
        provider_id=f"ollama-local:{model_id}", model_id=model_id, role_fit="drone", context_window=32768,
        tool_support=(), structured_output_support=True, tokens_per_second=20.0, ram_budget_gb=vram_gb,
        vram_budget_gb=vram_gb, quantization="q4", locality="local", privacy_class="local_private",
        queue_depth=0, max_safe_concurrency=1,
    )


def _plan(unready):
    from core.local_inference_autopilot import build_local_inference_autopilot_plan

    return build_local_inference_autopilot_plan(
        user_text="review this contract clause for risks", task_kind="chat", output_mode="plain_text",
        provider_role="drone", capability_truth=[_local_truth("qwen3:8b", 5.5), _local_truth("qwen2.5:7b", 5.0)],
        source_context={"requires_verifier": True}, default_model_tag="qwen3:8b", unready_local_models=unready,
    ).to_dict()


def test_the_verifier_never_picks_a_local_model_that_is_not_ready() -> None:
    plan = _plan(["qwen2.5:7b"])
    assert plan["selected_model"] == "qwen3:8b"
    # Before the fix this was the unready qwen2.5:7b; with no other candidate there is no verifier.
    assert plan["verifier_model"] is None
    # Control: with both ready, the other local model verifies, as before.
    both = _plan([])
    assert both["verifier_model"] and both["verifier_model"] != both["selected_model"]


def test_the_version_reply_names_the_model_turns_use(monkeypatch) -> None:
    from core.web.api import service

    monkeypatch.setattr("core.local_model_readiness.model_in_use", lambda boot: "qwen3:8b")
    runtime = SimpleNamespace(
        runtime_model_tag="qwen2.5:7b",
        runtime_version_stamp={"release_version": "0.8.0-beta", "build_id": "b", "channel_name": "beta"},
    )
    reply = service.runtime_version_response("what version of vool are you on?", runtime)
    assert reply is not None
    assert "qwen3:8b" in reply["response"] and "qwen2.5:7b" not in reply["response"]


def test_a_phone_over_its_own_monthly_cloud_limit_is_told_so() -> None:
    from core.agent_runtime.memory_runtime import _selected_model_block_cause

    cause = _selected_model_block_cause("device_monthly_spend_cap_exceeded", model_was_attempted=False)
    assert cause.startswith("this phone has reached its monthly cloud limit")
    assert "Settings on your computer" in cause
