"""OpenRouter free-tag detection + model search (both surfaced from live-usage feedback)."""

from __future__ import annotations

from types import SimpleNamespace

from core import openrouter_catalog as orc
from core.agent_runtime import fast_command_surface as fcs


def _model(model_id: str, *, prompt=None, completion=None, ctx=8000, name="", request_usd=0.0):
    return SimpleNamespace(
        model_id=model_id, name=name or model_id, context_length=ctx,
        output_modalities=["text"], prompt_usd_per_token=prompt, completion_usd_per_token=completion,
        request_usd=request_usd,
    )


def test_free_suffix_counts_as_free_even_with_unpublished_pricing() -> None:
    # A ':free' variant with null prices (like the Tencent hy3 :free) must count as free.
    assert orc.model_is_free(_model("tencent/hunyuan-a13b-instruct:free")) is True
    # A model with explicit zero prices is still free.
    assert orc.model_is_free(_model("x/y", prompt=0.0, completion=0.0)) is True
    # A priced model is NOT free; an unknown-priced non-:free model stays paid.
    assert orc.model_is_free(_model("openai/gpt-4o", prompt=5e-6, completion=15e-6)) is False
    assert orc.model_is_free(_model("some/unknown-price-model")) is False


def test_search_finds_and_ranks(monkeypatch) -> None:
    catalog = (
        _model("tencent/hunyuan-a13b-instruct:free"),
        _model("tencent/hunyuan-large", prompt=1e-6, completion=2e-6),
        _model("deepseek/deepseek-v3:free"),
        _model("openai/gpt-4o", prompt=5e-6, completion=15e-6),
    )
    monkeypatch.setattr(orc, "load_openrouter_catalog", lambda *a, **k: catalog)
    out = fcs._search_models_text("hunyuan")
    assert "tencent/hunyuan-a13b-instruct:free" in out
    assert "tencent/hunyuan-large" in out
    assert "gpt-4o" not in out  # unrelated model excluded
    # free variant labelled free, priced one labelled paid
    assert "hunyuan-a13b-instruct:free` — free" in out
    assert "hunyuan-large` — paid" in out
    # too-short query is guarded
    assert "at least 2 characters" in fcs._search_models_text("h")


def test_cloud_models_routes_list_vs_search(monkeypatch) -> None:
    monkeypatch.setattr(orc, "load_openrouter_catalog", lambda *a, **k: (_model("deepseek/deepseek-v3:free"),))
    # a keyword filter still lists; a name searches
    assert fcs.maybe_handle_cloud_models_command("cloud models deepseek") is not None
    assert "deepseek" in fcs.maybe_handle_cloud_models_command("cloud models deepseek").lower()
    # partial on the setter searches instead of failing to switch
    assert "matching" in fcs.maybe_handle_cloud_model_command("cloud model deepseek", owner_local=True).lower()
