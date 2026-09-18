"""A text task never selects a non-text generation model -- one reading, used by every cloud seam.

Measured live on the isolated daemon (2026-09-02): the free-cloud broker's auto pick chose
`google/lyria-3-clip-preview` (OpenRouter modality "text+image->text+audio", a music generation
model) for a chat turn because "text" appeared in its output list, and the call failed with
reason `unknown`. The rule is `cloud_provider_contract.text_only_output`: every declared output
modality is text (a legacy row that declares none counts as text). The route planner rejects
anything else as `non_text_generation_model`, and the catalog's own "free text model" reading
uses the same function, so the two cannot disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.cloud_provider_contract import (
    CloudModelMetadata,
    CloudTaskRequirements,
    PricingState,
    PrivacyClass,
    is_text_generation_model,
    text_only_output,
)
from core.cloud_routing import CloudRouteMode, select_cloud_route

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)
REQ = CloudTaskRequirements(min_context_tokens=1000, expected_output_tokens=100, required_capabilities=(), privacy_class=PrivacyClass.PUBLIC)


def _model(model_id: str, output_modalities: list[str] | None) -> CloudModelMetadata:
    return CloudModelMetadata(
        provider_id="openrouter",
        model_id=model_id,
        display_name=model_id,
        pricing_state=PricingState.FREE,
        input_usd_per_token=0.0,
        output_usd_per_token=0.0,
        request_usd=0.0,
        context_window=1_000_000,
        capabilities=("text",),
        discovered_at="2026-09-02T00:00:00+00:00",
        expires_at="2026-09-03T00:00:00+00:00",
        health_state="ready",
        latency_ms=100.0,
        quota_remaining=10.0,
        metadata={"architecture": {"output_modalities": output_modalities}} if output_modalities is not None else {},
    )


def test_text_only_output_is_the_one_reading() -> None:
    assert text_only_output(["text"]) is True
    assert text_only_output([]) is True and text_only_output(None) is True, "a legacy row with no modalities is a text row"
    assert text_only_output(["text", "audio"]) is False, "Lyria's shape"
    assert text_only_output(["text", "image"]) is False and text_only_output(["audio"]) is False
    assert is_text_generation_model(_model("google/lyria-3-clip-preview", ["text", "audio"])) is False
    assert is_text_generation_model(_model("minimax/minimax-m3:free", ["text"])) is True


def test_the_route_planner_rejects_a_music_model_for_a_text_task_and_picks_the_text_one() -> None:
    lyria = _model("google/lyria-3-clip-preview", ["text", "audio"])
    minimax = _model("minimax/minimax-m3:free", ["text"])
    legacy = _model("legacy/text-row", None)
    plan = select_cloud_route(
        (lyria, minimax, legacy),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        enabled_providers=("openrouter",),
        network_allowed_providers=("openrouter",),
        privacy_allowed=True,
        now=NOW,
    )
    assert plan.primary is not None and plan.primary.model_id != "google/lyria-3-clip-preview"
    rejected = {row["model_id"]: row["reason"] for row in plan.rejected}
    assert rejected.get("google/lyria-3-clip-preview") == "non_text_generation_model", rejected
    assert {m.model_id for m in (plan.primary, *plan.fallbacks)} == {"minimax/minimax-m3:free", "legacy/text-row"}


def test_the_catalog_never_calls_a_generation_model_a_free_text_model() -> None:
    from core.openrouter_catalog import OpenRouterModel, model_is_free

    def row(model_id: str, output_modalities: tuple[str, ...]) -> OpenRouterModel:
        return OpenRouterModel(
            model_id=model_id,
            name=model_id,
            context_length=1_000_000,
            prompt_usd_per_token=0.0,
            completion_usd_per_token=0.0,
            request_usd=0.0,
            supported_parameters=(),
            input_modalities=("text",),
            output_modalities=output_modalities,
            fetched_at="2026-09-02T00:00:00+00:00",
        )

    assert model_is_free(row("minimax/minimax-m3:free", ("text",))) is True
    assert model_is_free(row("google/lyria-3-clip-preview:free", ("text", "audio"))) is False, "even a :free tag does not make a music model a text model"
