"""Missing usage must never collapse to "reported zero" in the ledger.

Every adapter defaults an absent `usage` field to a plain int via `usage.get(key) or 0` --
a provider that reports 0 prompt tokens and a provider that reports NO prompt-token field at all
both landed as `prompt_tokens=0` with nothing to tell them apart, all the way down to a SQLite
schema with no null/unknown state (`prompt_tokens INTEGER NOT NULL`). The Activity/spend UI's
token totals could not distinguish "confirmed free" from "we never found out".

The fix tracks presence separately from the numeric value: `_record_response_usage` checks
`usage.get(key) is not None` (true for an explicit 0, false for an absent key) and threads that
through `usage_meter.record_usage`'s new `prompt_tokens_reported`/`output_tokens_reported`
params into two new additive columns, surfaced in `usage_summary()` as `calls_missing_usage`.
"""
from __future__ import annotations

from types import SimpleNamespace

from core import memory_first_router as mfr
from core import usage_meter as um
from core.cloud_providers import config_for
from core.usage_meter import COST_FREE_LOCAL, COST_PAID_CLOUD, record_usage, usage_summary
from storage.model_provider_manifest import ModelProviderManifest


def _reset() -> None:
    um._SCHEMA_READY_PATHS.clear()
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _byok_manifest(provider_id: str, model_name: str) -> ModelProviderManifest:
    cfg = config_for(provider_id)
    return ModelProviderManifest(
        provider_name=f"{provider_id}-byok",
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={
            "base_url": cfg.base_url or "https://example.invalid/v1",
            "api_path": "/chat/completions",
            "credential_key": cfg.credential_slot,
        },
        metadata={"cost_class": "paid_cloud"},
    )


def test_a_genuinely_reported_zero_is_marked_reported() -> None:
    manifest = _byok_manifest("anthropic", "claude-sonnet-4-5")
    summary = mfr._record_response_usage(
        manifest, SimpleNamespace(usage={"input_tokens": 0, "output_tokens": 12})
    )
    assert summary is not None
    assert summary["prompt_tokens"] == 0
    assert summary["prompt_tokens_reported"] is True
    assert summary["output_tokens_reported"] is True


def test_an_absent_field_is_marked_not_reported_even_though_it_reads_as_zero() -> None:
    manifest = _byok_manifest("anthropic", "claude-sonnet-4-5")
    # completion_tokens/output_tokens/eval_count are all absent -- the provider told us nothing
    # about output usage, not that it was zero.
    summary = mfr._record_response_usage(manifest, SimpleNamespace(usage={"input_tokens": 500}))
    assert summary is not None
    assert summary["output_tokens"] == 0  # the numeric value still degrades to 0 for billing math
    assert summary["prompt_tokens_reported"] is True
    assert summary["output_tokens_reported"] is False  # but the ledger knows it was never told


def test_a_completely_empty_usage_dict_marks_both_fields_unreported() -> None:
    manifest = _byok_manifest("anthropic", "claude-sonnet-4-5")
    summary = mfr._record_response_usage(manifest, SimpleNamespace(usage={}))
    assert summary is not None
    assert summary["prompt_tokens_reported"] is False
    assert summary["output_tokens_reported"] is False


def test_usage_summary_counts_calls_missing_usage_separately_from_zero_token_calls() -> None:
    _reset()
    # A confirmed-zero call: both fields genuinely reported as 0.
    assert record_usage(
        provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
        prompt_tokens=0, output_tokens=0, usd_actual=0.01,
        prompt_tokens_reported=True, output_tokens_reported=True,
    ) is True
    # A call with a real prompt count but no reported output count.
    assert record_usage(
        provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
        prompt_tokens=500, output_tokens=0,
        prompt_tokens_reported=True, output_tokens_reported=False,
    ) is True

    summary = usage_summary()

    assert summary["calls_missing_usage"] == 1
    assert summary[COST_PAID_CLOUD]["calls_missing_usage"] == 1
    assert summary[COST_PAID_CLOUD]["responses"] == 2  # both rows still counted, nothing dropped


def test_default_callers_that_never_learned_about_the_new_params_still_record_as_reported() -> None:
    """Backward compatibility: every pre-existing call site keeps recording exactly as before."""
    _reset()
    assert record_usage(
        provider_id="ollama", model_id="qwen-local", cost_class=COST_FREE_LOCAL,
        prompt_tokens=10, output_tokens=5,
    ) is True

    summary = usage_summary()

    assert summary["calls_missing_usage"] == 0
    assert summary[COST_FREE_LOCAL]["calls_missing_usage"] == 0
