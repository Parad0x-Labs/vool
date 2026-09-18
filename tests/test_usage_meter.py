"""Token usage meter: responses aggregated into free-local, free-cloud, and paid-cloud totals.

The meter records one row per served response and sums them by cost class so the user can
can distinguish local compute, verified-free remote compute, and billed remote compute. It must
be fail-soft (never break a turn) and must keep those classes cleanly separated.
"""
from __future__ import annotations

import sqlite3

import core.usage_meter as um
from core.usage_meter import (
    COST_FREE_CLOUD,
    COST_FREE_LOCAL,
    COST_PAID_CLOUD,
    COST_REMOTE_UNKNOWN,
    record_usage,
    usage_summary,
)

DAY1 = 1700000000.0  # 2023-11-14, a fixed timestamp for deterministic day/window tests


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


def test_summary_empty_with_no_rows():
    _reset()
    s = usage_summary()
    assert s["total_tokens"] == 0
    assert s[COST_FREE_LOCAL]["total_tokens"] == 0
    assert s[COST_PAID_CLOUD]["total_tokens"] == 0
    assert s[COST_PAID_CLOUD]["usd_estimate"] == 0.0


def test_records_split_local_vs_paid():
    _reset()
    assert record_usage(provider_id="ollama", model_id="qwen2.5:7b",
                        cost_class=COST_FREE_LOCAL, prompt_tokens=100, output_tokens=50) is True
    record_usage(provider_id="ollama", model_id="qwen2.5:7b",
                 cost_class=COST_FREE_LOCAL, prompt_tokens=20, output_tokens=10)
    record_usage(provider_id="openrouter-byok", model_id="openai/gpt-4.1-mini",
                 cost_class=COST_PAID_CLOUD, prompt_tokens=200, output_tokens=300)
    s = usage_summary()

    local = s[COST_FREE_LOCAL]
    assert local["responses"] == 2
    assert local["prompt_tokens"] == 120 and local["output_tokens"] == 60
    assert local["total_tokens"] == 180

    paid = s[COST_PAID_CLOUD]
    assert paid["responses"] == 1
    assert paid["total_tokens"] == 500
    assert paid["usd_estimate"] > 0.0            # dollar estimate on the paid side only
    assert s["total_tokens"] == 680


def test_zero_token_rows_are_skipped():
    _reset()
    assert record_usage(provider_id="p", model_id="m",
                        cost_class=COST_FREE_LOCAL, prompt_tokens=0, output_tokens=0) is False
    assert usage_summary()["total_tokens"] == 0


def test_unknown_cost_class_normalizes_to_remote_unknown():
    _reset()
    record_usage(provider_id="p", model_id="m", cost_class="mystery",
                 prompt_tokens=5, output_tokens=5)
    s = usage_summary()
    assert s[COST_REMOTE_UNKNOWN]["total_tokens"] == 10
    assert s[COST_FREE_LOCAL]["total_tokens"] == 0


def test_window_excludes_older_rows():
    _reset()
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=10, output_tokens=10, now=1000.0)
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=1, output_tokens=1, now=2000.0)
    # A 500s window ending at t=2000 keeps only the t=2000 row.
    s = usage_summary(window_seconds=500, now=2000.0)
    assert s[COST_PAID_CLOUD]["total_tokens"] == 2
    # No window sees both.
    assert usage_summary()[COST_PAID_CLOUD]["total_tokens"] == 22


def test_prune_drops_rows_past_retention(monkeypatch):
    _reset()
    monkeypatch.setenv("VOOL_USAGE_KEEP_SECONDS", "100")
    # Old row at t=1000; a later write at t=2000 prunes rows older than 2000-100=1900.
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=5, output_tokens=5, now=1000.0)
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=1, output_tokens=1, now=2000.0)
    # The t=1000 row was older than the 100s retention at write time -> pruned.
    assert usage_summary()[COST_PAID_CLOUD]["total_tokens"] == 2


def test_usd_rate_is_env_overridable(monkeypatch):
    _reset()
    monkeypatch.setenv("VOOL_CLOUD_USD_PER_TOKEN", "0.001")
    record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=500, output_tokens=500)
    s = usage_summary()
    assert s["usd_per_paid_token"] == 0.001
    assert s[COST_PAID_CLOUD]["usd_estimate"] == 1.0   # 1000 tokens * 0.001


def test_record_is_fail_safe_and_never_raises(monkeypatch):
    _reset()

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("storage.db.get_connection", _boom)
    # Must not raise; returns False.
    assert record_usage(provider_id="p", model_id="m", cost_class=COST_PAID_CLOUD,
                        prompt_tokens=10, output_tokens=10) is False
    # Summary also degrades to empty rather than raising.
    assert usage_summary()["total_tokens"] == 0


def test_record_retries_only_confirmed_sqlite_contention(monkeypatch):
    attempts = 0

    def _busy_then_success(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return True

    monkeypatch.setattr(um, "_record_usage_once", _busy_then_success)
    monkeypatch.setattr(um.time, "sleep", lambda _seconds: None)

    assert record_usage(
        provider_id="p",
        model_id="m",
        cost_class=COST_PAID_CLOUD,
        prompt_tokens=1,
        output_tokens=1,
    ) is True
    assert attempts == 3


def test_record_does_not_retry_non_contention_sqlite_errors(monkeypatch):
    attempts = 0

    def _read_only(**_kwargs):
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(um, "_record_usage_once", _read_only)

    assert record_usage(
        provider_id="p",
        model_id="m",
        cost_class=COST_PAID_CLOUD,
        prompt_tokens=1,
        output_tokens=1,
    ) is False
    assert attempts == 1


# ── router integration: the capture helper attributes by cost class ──────────

def _stub_manifest(cost_class, provider, model):
    from types import SimpleNamespace

    return SimpleNamespace(
        provider_id=provider, model_name=model,
        metadata={"cost_class": cost_class}, adapter_type="openai_compatible",
        source_type="http", runtime_config={"base_url": "https://x"},
    )


def test_router_helper_records_by_cost_class_both_usage_formats():
    _reset()
    from types import SimpleNamespace

    from core.memory_first_router import _record_response_usage

    # Local (free) response — Ollama-style usage keys.
    free_summary = _record_response_usage(
        _stub_manifest(COST_FREE_LOCAL, "ollama", "qwen2.5:7b"),
        SimpleNamespace(usage={"prompt_eval_count": 40, "eval_count": 60}),
    )
    # Paid cloud response — OpenAI-style usage keys + real per-request cost.
    paid_summary = _record_response_usage(
        _stub_manifest(COST_PAID_CLOUD, "openrouter-byok", "openai/gpt-4.1-mini"),
        SimpleNamespace(usage={"prompt_tokens": 100, "completion_tokens": 200, "cost": 0.0042}),
    )
    s = usage_summary()
    assert s[COST_FREE_LOCAL]["prompt_tokens"] == 40 and s[COST_FREE_LOCAL]["output_tokens"] == 60
    assert s[COST_PAID_CLOUD]["prompt_tokens"] == 100 and s[COST_PAID_CLOUD]["output_tokens"] == 200
    assert s[COST_PAID_CLOUD]["usd_estimate"] > 0.0   # dollar estimate only on the paid side
    # The helper returns a per-response summary so a live model_usage event carries the numbers.
    assert free_summary["cost_class"] == COST_FREE_LOCAL
    assert free_summary["prompt_tokens"] == 40 and free_summary["output_tokens"] == 60
    assert free_summary["usd_actual"] is None
    assert paid_summary["cost_class"] == COST_PAID_CLOUD
    assert paid_summary["prompt_tokens"] == 100 and paid_summary["output_tokens"] == 200
    assert paid_summary["usd_actual"] == 0.0042


def test_verified_free_openrouter_response_is_reported_and_metered_as_free_cloud(monkeypatch):
    _reset()
    from types import SimpleNamespace

    from core.memory_first_router import _record_response_usage

    manifest = _stub_manifest(COST_PAID_CLOUD, "openrouter-byok:nvidia/free:free", "nvidia/free:free")
    manifest.provider_name = "openrouter-byok"
    monkeypatch.setattr("core.model_selection_policy.is_verified_free_cloud_manifest", lambda _manifest: True)

    summary = _record_response_usage(
        manifest,
        SimpleNamespace(usage={"prompt_tokens": 30, "completion_tokens": 20, "cost": 0.0}),
    )

    assert summary["cost_class"] == COST_FREE_CLOUD
    assert summary["usd_actual"] == 0.0
    aggregate = usage_summary()
    assert aggregate[COST_FREE_CLOUD]["total_tokens"] == 50
    assert aggregate[COST_PAID_CLOUD]["total_tokens"] == 0


def test_legacy_paid_row_for_catalog_verified_free_model_is_repaired_on_read(monkeypatch):
    _reset()
    from core.openrouter_catalog import OpenRouterModel

    model = OpenRouterModel(
        model_id="nvidia/legacy-free:free",
        name="Legacy Free",
        context_length=32000,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        supported_parameters=(),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-07-26T00:00:00Z",
    )
    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", lambda **_kwargs: ((model,), 0.0))
    record_usage(
        provider_id="openrouter-byok:nvidia/legacy-free:free",
        model_id=model.model_id,
        cost_class=COST_PAID_CLOUD,
        prompt_tokens=70,
        output_tokens=30,
        usd_actual=0.0,
    )
    record_usage(
        provider_id="openrouter-byok:nvidia/legacy-free:free",
        model_id=model.model_id,
        cost_class=COST_FREE_CLOUD,
        prompt_tokens=10,
        output_tokens=10,
        usd_actual=0.0,
    )

    aggregate = usage_summary()
    assert aggregate[COST_FREE_CLOUD]["total_tokens"] == 120
    assert aggregate[COST_PAID_CLOUD]["total_tokens"] == 0
    rows = um.usage_by_model()
    assert len(rows) == 1
    assert rows[0]["cost_class"] == COST_FREE_CLOUD
    assert rows[0]["total_tokens"] == 120
    assert "usd" not in rows[0]


def test_router_helper_is_fail_safe_on_bad_inputs():
    _reset()
    from types import SimpleNamespace

    from core.memory_first_router import _record_response_usage

    # Missing usage / manifest attrs must not raise and must record nothing measurable.
    _record_response_usage(SimpleNamespace(), SimpleNamespace(usage=None))
    assert usage_summary()["total_tokens"] == 0


def test_format_report_reads_human():
    _reset()
    from core.usage_meter import format_report

    record_usage(provider_id="ollama", model_id="m", cost_class=COST_FREE_LOCAL,
                 prompt_tokens=600, output_tokens=400)
    record_usage(provider_id="openrouter-byok", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=100, output_tokens=100)
    report = format_report()
    assert "Local (free)" in report and "Cloud (paid)" in report
    assert "1,000" in report                 # local total, thousands-formatted
    assert "free on local" in report


# ── real per-request cost (OpenRouter usage.cost) ────────────────────────────

def test_real_usd_actual_reports_exact():
    _reset()
    record_usage(provider_id="openrouter-byok", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=100, output_tokens=100, usd_actual=0.37)
    paid = usage_summary()[COST_PAID_CLOUD]
    assert paid["usd_actual"] == 0.37
    assert paid["usd"] == 0.37            # fully exact
    assert paid["all_actual"] is True


def test_mixed_actual_and_estimated_paid_rows():
    _reset()
    record_usage(provider_id="or", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=100, output_tokens=100, usd_actual=0.50)
    record_usage(provider_id="or", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=1000, output_tokens=0)   # no usd_actual -> estimated portion
    s = usage_summary()
    paid = s[COST_PAID_CLOUD]
    assert paid["usd_actual"] == 0.50
    assert paid["all_actual"] is False
    assert paid["usd"] == round(0.50 + 1000 * s["usd_per_paid_token"], 6)   # real + estimate


def test_router_helper_reads_openrouter_cost():
    _reset()
    from types import SimpleNamespace

    from core.memory_first_router import _record_response_usage

    mf = SimpleNamespace(provider_id="openrouter-byok", model_name="m",
                         metadata={"cost_class": COST_PAID_CLOUD}, adapter_type="openai_compatible",
                         source_type="http", runtime_config={"base_url": "https://x"})
    resp = SimpleNamespace(usage={"prompt_tokens": 194, "completion_tokens": 2, "total_tokens": 196, "cost": 0.95})
    _record_response_usage(mf, resp)
    paid = usage_summary()[COST_PAID_CLOUD]
    assert paid["usd_actual"] == 0.95
    assert paid["all_actual"] is True


def test_router_helper_adds_upstream_cost_for_byok_upstream_key():
    _reset()
    from types import SimpleNamespace

    from core.memory_first_router import _record_response_usage

    mf = _stub_manifest(COST_PAID_CLOUD, "openrouter-byok", "m")
    # Own-upstream-key path (is_byok=true): top-level cost is OpenRouter's fee charged to the
    # OpenRouter account, and cost_details.upstream_inference_cost is the separate charge to
    # the user's own upstream account — the meter sums them for the real out-of-pocket.
    resp = SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 10, "is_byok": True,
                                  "cost": 0.05, "cost_details": {"upstream_inference_cost": 0.20}})
    _record_response_usage(mf, resp)
    assert usage_summary()[COST_PAID_CLOUD]["usd_actual"] == 0.25


def test_router_helper_does_not_double_count_upstream_without_byok():
    _reset()
    from types import SimpleNamespace

    from core.memory_first_router import _record_response_usage

    mf = _stub_manifest(COST_PAID_CLOUD, "openrouter-byok", "m")
    # Normal credits-key response (not is_byok): usage.cost is already the full account charge.
    # A stray upstream_inference_cost must NOT be added on top or the spend is double counted.
    resp = SimpleNamespace(usage={"prompt_tokens": 10, "completion_tokens": 10,
                                  "cost": 0.95, "cost_details": {"upstream_inference_cost": 0.20}})
    _record_response_usage(mf, resp)
    assert usage_summary()[COST_PAID_CLOUD]["usd_actual"] == 0.95


def test_format_report_shows_actual_when_exact():
    _reset()
    from core.usage_meter import format_report

    record_usage(provider_id="or", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=50, output_tokens=50, usd_actual=0.12)
    assert "$0.120000 actual" in format_report()


def test_format_report_preserves_exact_zero_cost():
    _reset()
    from core.usage_meter import format_report

    record_usage(
        provider_id="openrouter-byok:free/model",
        model_id="free/model",
        cost_class=COST_PAID_CLOUD,
        prompt_tokens=50,
        output_tokens=50,
        usd_actual=0.0,
    )
    report = format_report()
    assert "$0.000000 actual" in report
    assert "~$0.000200 est." not in report


# ── notices (cap + spend) ────────────────────────────────────────────────────

def _isolate_cloud_policy(monkeypatch, tmp_path):
    import core.cloud_escalation_policy as cep

    monkeypatch.setattr(cep, "_store_path", lambda: tmp_path / "cloud_escalation.json")
    return cep


def test_legacy_call_limit_does_not_raise_quota_notice(monkeypatch, tmp_path):
    _reset()
    cep = _isolate_cloud_policy(monkeypatch, tmp_path)
    cep.save_policy(cep.CloudEscalationPolicy(mode="auto", daily_cap=5))
    for _ in range(5):
        cep.record_escalation(now=DAY1)
    from core.usage_meter import usage_notices

    assert usage_notices(now=DAY1) == []


def test_legacy_call_limit_does_not_raise_approaching_notice(monkeypatch, tmp_path):
    _reset()
    cep = _isolate_cloud_policy(monkeypatch, tmp_path)
    cep.save_policy(cep.CloudEscalationPolicy(mode="auto", daily_cap=10))
    for _ in range(8):   # 80% of 10
        cep.record_escalation(now=DAY1)
    from core.usage_meter import usage_notices

    assert usage_notices(now=DAY1) == []


def test_notices_spend_line(monkeypatch, tmp_path):
    _reset()
    _isolate_cloud_policy(monkeypatch, tmp_path)   # no policy saved -> off, no cap notice
    record_usage(provider_id="or", model_id="m", cost_class=COST_PAID_CLOUD,
                 prompt_tokens=100, output_tokens=100, usd_actual=0.42)
    from core.usage_meter import usage_notices

    ns = usage_notices()
    assert any("cloud spend" in n.lower() and "0.42" in n for n in ns)


def test_notices_empty_when_off_and_no_paid(monkeypatch, tmp_path):
    _reset()
    _isolate_cloud_policy(monkeypatch, tmp_path)
    from core.usage_meter import usage_notices

    assert usage_notices() == []
