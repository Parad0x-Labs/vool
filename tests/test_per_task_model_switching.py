"""Per-task model switching across the real 3-tier local bundle.

Every hardware bucket -- including bucket B (a 6-8GB "potato" GPU like a GTX 1080) --
installs a three-model bundle (tiny_fast / daily_accelerated / deep_overnight; see
core/local_model_bundles.py triple_bucket_*). Those register as lightweight_utility /
general / reasoning roles, and the router must send each task to the right one:

  - quick classification / tool / format  -> tiny lightweight model
  - ordinary chat                          -> general daily model
  - planning / research / reasoning        -> reasoning (deep) model

On a bigger GPU the deep tier runs live; on a 1080 it is honestly a slower batch/
overnight lane, but it is still the model the reasoning tasks route to. This locks the
observable behavior behind the public claim that VOOL picks a different model per task.
A degenerate single-model registry (fallback / mid-download) must still serve every task
on the one model without error.
"""
from __future__ import annotations

import pytest

from core.local_model_bundles import model_parameter_billions
from core.model_registry import ModelRegistry
from core.model_selection_policy import ModelSelectionRequest, rank_providers
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import list_provider_manifests

_COMMON = dict(
    source_type="http",
    adapter_type="local_qwen_provider",
    license_name="Apache-2.0",
    license_reference="https://www.apache.org/licenses/LICENSE-2.0",
    weight_location="user-supplied",
    weights_bundled=False,
    redistribution_allowed=True,
    runtime_dependency="ollama",
    capabilities=["summarize", "classify", "format", "structured_json", "code_complex", "long_context"],
    runtime_config={"base_url": "http://127.0.0.1:11434"},
    enabled=True,
)


@pytest.fixture(autouse=True)
def _isolate_manifests():
    """Give each test a clean manifest table and leave none behind (unsharded-run safe)."""
    run_migrations()
    _clear_manifests()
    yield
    _clear_manifests()


def _clear_manifests() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
    finally:
        conn.close()


def _register(model_name: str, bundle_role: str, orchestration_role: str = "drone") -> None:
    ModelRegistry().register_manifest(
        {
            **_COMMON,
            "provider_name": "ollama-local",
            "model_name": model_name,
            "metadata": {
                "deployment_class": "local",
                "bundle_role": bundle_role,
                "orchestration_role": orchestration_role,
            },
        }
    )


def _top_model(task_kind: str, output_mode: str) -> str:
    ranked = rank_providers(
        list_provider_manifests(enabled_only=True),
        ModelSelectionRequest(task_kind=task_kind, output_mode=output_mode),
    )
    assert ranked, f"no provider ranked for {task_kind}/{output_mode}"
    return ranked[0].model_name


def _install_bucket_b_three_tier() -> None:
    # Mirrors triple_bucket_b_no_gpu: what a 6-8GB GPU install actually pulls.
    _register("qwen3:0.6b", bundle_role="lightweight_utility")
    _register("qwen2.5:7b", bundle_role="general", orchestration_role="drone")
    _register("deepseek-r1:14b", bundle_role="reasoning", orchestration_role="queen")


def test_quick_classification_routes_to_the_tiny_model() -> None:
    _install_bucket_b_three_tier()
    # Cheap label work is what the tiny lane is for, and it keeps it.
    assert _top_model("classification", "plain_text") == "qwen3:0.6b"
    assert _top_model("tag", "plain_text") == "qwen3:0.6b"


def test_selecting_a_real_tool_does_not_route_to_the_tiny_model() -> None:
    # Measured, not assumed: replaying one captured tool_intent prompt ("run the command: ls -la",
    # ~17.5k chars, 57 tools) returned {"intent":"sandbox.run_command","arguments":{"command":"ls -la"}}
    # on qwen3:8b and respond.direct carrying invented prose on qwen3:0.6b -- the user was told
    # "I'm ready to help" and nothing ran. Choosing a tool is a different job from tagging a string.
    _install_bucket_b_three_tier()
    chosen = _top_model("tool_intent", "tool_intent")
    assert chosen != "qwen3:0.6b"
    assert model_parameter_billions(chosen) >= 4.0


def test_ordinary_chat_routes_to_the_general_model() -> None:
    _install_bucket_b_three_tier()
    assert _top_model("normalization_assist", "plain_text") == "qwen2.5:7b"


def test_plan_research_and_reasoning_route_to_the_reasoning_model() -> None:
    _install_bucket_b_three_tier()
    assert _top_model("action_plan", "action_plan") == "deepseek-r1:14b"
    assert _top_model("summarization", "summary_block") == "deepseek-r1:14b"
    assert _top_model("reasoning", "plain_text") == "deepseek-r1:14b"


def test_single_model_registry_serves_every_task_without_error() -> None:
    # Degenerate case (fallback, or mid-download before the deep tier lands): one model
    # must still answer every task rather than leaving a lane unrouted.
    _register("qwen2.5:7b", bundle_role="general")
    for task_kind, output_mode in (
        ("classification", "tool_intent"),
        ("normalization_assist", "plain_text"),
        ("action_plan", "action_plan"),
        ("summarization", "summary_block"),
    ):
        assert _top_model(task_kind, output_mode) == "qwen2.5:7b"
