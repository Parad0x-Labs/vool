"""Settlement prices a paid cloud call from token counts and the model's published rate.

The defect these pin: settlement read ``usage.cost``, a field only OpenRouter returns, so calls
to the other seven provider slots settled at $0.00 and the USD caps never moved. Nothing here
uses a real key or makes a network call — token counts are replayed into the pricing functions.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import memory_first_router as mfr
from core import model_pricing
from core.cloud_providers import PROVIDERS, config_for
from core.model_pricing import (
    BASIS_NO_USAGE,
    BASIS_PROVIDER_REPORTED,
    BASIS_PUBLISHED_PRICE,
    BASIS_UNKNOWN_CEILING,
    estimate_call_usd,
    lookup_model_price,
    provider_slot,
    provider_slot_for_manifest,
    settlement_usd,
    unknown_price_ceiling,
    usage_tokens,
)
from core.openrouter_catalog import OpenRouterModel
from storage.model_provider_manifest import ModelProviderManifest

IN, OUT = 12_000, 900


def _published(prompt_per_m: float, completion_per_m: float) -> float:
    return round(IN * prompt_per_m / 1e6 + OUT * completion_per_m / 1e6, 8)


def _byok_manifest(provider_id: str, model_name: str) -> ModelProviderManifest:
    """A BYOK lane shaped exactly like core.runtime_provider_defaults registers it."""
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


# --- the published-rate replay ----------------------------------------------------------------

# (provider slot, model id as the provider names it, its usage dialect, published $/1M in/out)
PUBLISHED_CASES = [
    ("anthropic", "claude-sonnet-4-5", {"input_tokens": IN, "output_tokens": OUT}, 3.00, 15.00),
    ("anthropic", "claude-opus-4-1", {"input_tokens": IN, "output_tokens": OUT}, 15.00, 75.00),
    ("openai", "gpt-4.1-mini", {"prompt_tokens": IN, "completion_tokens": OUT}, 0.40, 1.60),
    ("openai", "gpt-4.1", {"prompt_tokens": IN, "completion_tokens": OUT}, 2.00, 8.00),
    ("groq", "llama-3.3-70b-versatile", {"prompt_tokens": IN, "completion_tokens": OUT}, 0.59, 0.79),
    ("groq", "llama-3.1-8b-instant", {"prompt_tokens": IN, "completion_tokens": OUT}, 0.05, 0.08),
    ("google", "gemini-2.5-flash", {"prompt_tokens": IN, "completion_tokens": OUT}, 0.30, 2.50),
    ("deepseek", "deepseek-chat", {"prompt_tokens": IN, "completion_tokens": OUT}, 0.27, 1.10),
    ("moonshot", "kimi-k2-0711-preview", {"prompt_tokens": IN, "completion_tokens": OUT}, 0.60, 2.50),
]


@pytest.mark.parametrize("slot,model_id,usage,prompt_per_m,completion_per_m", PUBLISHED_CASES)
def test_settles_at_the_published_rate(slot, model_id, usage, prompt_per_m, completion_per_m):
    estimate = settlement_usd(provider_id=slot, model_id=model_id, usage=usage)
    assert estimate.basis == BASIS_PUBLISHED_PRICE
    assert estimate.known is True
    assert estimate.usd == pytest.approx(_published(prompt_per_m, completion_per_m), abs=1e-9)


@pytest.mark.parametrize("slot,model_id,usage,prompt_per_m,completion_per_m", PUBLISHED_CASES)
def test_router_settlement_prices_a_real_manifest(slot, model_id, usage, prompt_per_m, completion_per_m):
    """The router's settlement hook must price a REAL manifest, whose ``provider_id`` is the
    composite ``"anthropic-byok:claude-sonnet-4-5"`` and not the billing slot."""
    manifest = _byok_manifest(slot, model_id)
    assert manifest.provider_id != slot  # the trap this guards
    usd = mfr._response_actual_usd(manifest, SimpleNamespace(usage=usage))
    assert usd == pytest.approx(_published(prompt_per_m, completion_per_m), abs=1e-9)


def test_thirty_anthropic_calls_move_the_ledger_instead_of_settling_at_zero():
    """The measured defect: 30 Claude Sonnet calls settled $0.00 against a $5.00 daily cap."""
    manifest = _byok_manifest("anthropic", "claude-sonnet-4-5")
    response = SimpleNamespace(usage={"input_tokens": IN, "output_tokens": OUT})
    total = sum(mfr._response_actual_usd(manifest, response) for _ in range(30))
    assert total > 0.0
    assert total == pytest.approx(30 * _published(3.00, 15.00), abs=1e-6)


def test_anthropic_usage_carries_no_cost_field_so_the_old_reading_gave_zero():
    """Guards the premise: the providers this fixes genuinely do not report a cost."""
    usage = {"input_tokens": IN, "output_tokens": OUT}
    assert model_pricing.provider_reported_usd(usage) is None
    assert usage_tokens(usage) == (IN, OUT)


# --- provider slot resolution -----------------------------------------------------------------


@pytest.mark.parametrize("slot", sorted(PROVIDERS))
def test_every_provider_slot_resolves_from_its_manifest(slot):
    assert provider_slot_for_manifest(_byok_manifest(slot, "some-model")) == slot


@pytest.mark.parametrize(
    "hint,expected",
    [
        ("anthropic", "anthropic"),
        ("anthropic-byok", "anthropic"),
        ("anthropic-byok:claude-sonnet-4-5", "anthropic"),
        ("OpenRouter", "openrouter"),
        ("not-a-provider", ""),
        ("", ""),
    ],
)
def test_provider_slot_normalizes_the_forms_settlement_actually_sees(hint, expected):
    assert provider_slot(hint) == expected


# --- model id shapes --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slot,model_id",
    [
        ("anthropic", "claude-sonnet-4-5"),
        ("anthropic", "claude-sonnet-4-5-20250929"),  # dated release id
        ("anthropic", "anthropic/claude-sonnet-4-5"),  # vendor-prefixed id
    ],
)
def test_dated_and_prefixed_ids_price_off_the_same_entry(slot, model_id):
    price = lookup_model_price(slot, model_id)
    assert price.known is True
    assert price.prompt_usd_per_token == pytest.approx(3.00 / 1e6)
    assert price.completion_usd_per_token == pytest.approx(15.00 / 1e6)


def test_a_longer_key_wins_over_a_shorter_prefix():
    mini = lookup_model_price("openai", "gpt-4.1-mini")
    full = lookup_model_price("openai", "gpt-4.1")
    assert mini.prompt_usd_per_token == pytest.approx(0.40 / 1e6)
    assert full.prompt_usd_per_token == pytest.approx(2.00 / 1e6)


def test_a_prefix_only_matches_on_a_separator_boundary():
    """"gpt-4.15-turbo" is not a "gpt-4.1" variant and must not be priced as one."""
    assert lookup_model_price("openai", "gpt-4.15-turbo").known is False


# --- the unknown case: never free ---------------------------------------------------------------


@pytest.mark.parametrize(
    "slot,model_id",
    [
        ("anthropic", "claude-model-nobody-has-priced-yet"),
        ("openai", "gpt-6-unreleased"),
        ("custom", "whatever-they-run"),  # a user-supplied endpoint: unknowable by definition
        ("", "orphaned-model"),  # provider could not be resolved at all
        ("not-a-provider", "anything"),
    ],
)
def test_an_unknown_price_never_settles_at_zero(slot, model_id):
    estimate = settlement_usd(
        provider_id=slot, model_id=model_id, usage={"prompt_tokens": IN, "completion_tokens": OUT}
    )
    assert estimate.usd > 0.0, "an unpriced model must never read as free"
    assert estimate.known is False
    assert estimate.basis == BASIS_UNKNOWN_CEILING


def test_the_unknown_ceiling_is_at_least_the_dearest_model_we_do_know():
    ceiling_in, ceiling_out = unknown_price_ceiling()
    opus = lookup_model_price("anthropic", "claude-opus-4-1")
    assert ceiling_in >= opus.prompt_usd_per_token
    assert ceiling_out >= opus.completion_usd_per_token
    for slot, model_id, _usage, _prompt_per_m, _completion_per_m in PUBLISHED_CASES:
        known = lookup_model_price(slot, model_id)
        assert ceiling_in >= known.prompt_usd_per_token
        assert ceiling_out >= known.completion_usd_per_token


def test_an_unknown_model_is_charged_more_than_any_known_one():
    """The ceiling has to bias against the wallet, or it is just another way to under-report."""
    usage = {"prompt_tokens": IN, "completion_tokens": OUT}
    unknown = settlement_usd(provider_id="anthropic", model_id="brand-new-thing", usage=usage)
    for slot, model_id, case_usage, _p, _c in PUBLISHED_CASES:
        known = settlement_usd(provider_id=slot, model_id=model_id, usage=case_usage)
        assert unknown.usd >= known.usd


def test_an_unknown_price_is_not_refused_it_is_ceilinged():
    """The deliberate choice: settle high rather than refuse a model the table has not caught up
    with. A refusal would turn a stale price table into an outage."""
    estimate = settlement_usd(
        provider_id="anthropic", model_id="claude-next", usage={"input_tokens": 10, "output_tokens": 10}
    )
    assert estimate.usd > 0.0
    assert estimate.describe().startswith("up to $")


def test_no_usage_at_all_settles_at_zero_and_says_so():
    """Distinct from an unknown price: there is no response to charge for, so the ceiling would
    invent a charge rather than bound one."""
    estimate = settlement_usd(provider_id="anthropic", model_id="claude-sonnet-4-5", usage={})
    assert estimate.usd == 0.0
    assert estimate.basis == BASIS_NO_USAGE
    assert estimate.known is False


# --- OpenRouter: usage.cost is authoritative where it exists -------------------------------------


def test_openrouter_reported_cost_wins_over_the_token_estimate():
    estimate = settlement_usd(
        provider_id="openrouter",
        model_id="deepseek/deepseek-chat-v3",
        usage={"prompt_tokens": IN, "completion_tokens": OUT, "cost": 0.004123},
    )
    assert estimate.basis == BASIS_PROVIDER_REPORTED
    assert estimate.usd == pytest.approx(0.004123)
    assert estimate.known is True


def test_a_byok_upstream_charge_is_added_on_top_of_the_reported_cost():
    estimate = settlement_usd(
        provider_id="openrouter",
        model_id="deepseek/deepseek-chat-v3",
        usage={
            "prompt_tokens": IN,
            "completion_tokens": OUT,
            "cost": 0.001,
            "cost_details": {"is_byok": True, "upstream_inference_cost": 0.004},
        },
    )
    assert estimate.usd == pytest.approx(0.005)


def test_a_stray_upstream_cost_is_ignored_when_the_call_was_not_byok():
    estimate = settlement_usd(
        provider_id="openrouter",
        model_id="deepseek/deepseek-chat-v3",
        usage={"cost": 0.001, "cost_details": {"upstream_inference_cost": 0.004}, "prompt_tokens": 10},
    )
    assert estimate.usd == pytest.approx(0.001)


def _catalog_row(model_id: str, prompt: float | None, completion: float | None) -> OpenRouterModel:
    return OpenRouterModel(
        model_id=model_id,
        name=model_id,
        context_length=163_840,
        prompt_usd_per_token=prompt,
        completion_usd_per_token=completion,
        request_usd=None,
        supported_parameters=(),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-07-20T00:00:00+00:00",
    )


def test_an_openrouter_free_model_settles_at_zero_because_zero_is_published(monkeypatch):
    row = _catalog_row("vendor/free-chat:free", 0.0, 0.0)
    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", lambda **_kw: ((row,), 0.0))
    estimate = settlement_usd(
        provider_id="openrouter",
        model_id="vendor/free-chat:free",
        usage={"prompt_tokens": IN, "completion_tokens": OUT},
    )
    assert estimate.usd == 0.0
    assert estimate.known is True
    assert estimate.basis == BASIS_PUBLISHED_PRICE


def test_an_openrouter_model_priced_from_the_live_catalog(monkeypatch):
    row = _catalog_row("vendor/paid-chat", 3.0e-6, 15.0e-6)
    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", lambda **_kw: ((row,), 0.0))
    estimate = settlement_usd(
        provider_id="openrouter", model_id="vendor/paid-chat", usage={"prompt_tokens": IN, "completion_tokens": OUT}
    )
    assert estimate.usd == pytest.approx(_published(3.00, 15.00), abs=1e-9)
    assert "catalog" in estimate.source


def test_an_openrouter_model_with_unpublished_pricing_hits_the_ceiling_not_zero(monkeypatch):
    row = _catalog_row("vendor/mystery", None, None)
    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", lambda **_kw: ((row,), 0.0))
    estimate = settlement_usd(
        provider_id="openrouter", model_id="vendor/mystery", usage={"prompt_tokens": IN, "completion_tokens": OUT}
    )
    assert estimate.usd > 0.0
    assert estimate.known is False


def test_openrouter_pricing_never_reaches_the_network(monkeypatch):
    """Settlement runs after the money is spent; it must not block on a catalog fetch."""
    seen: list[dict] = []

    def _spy(**kwargs):
        seen.append(kwargs)
        return ((), None)

    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", _spy)
    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda **_kw: pytest.fail("settlement must not fetch the catalog"),
    )
    settlement_usd(provider_id="openrouter", model_id="vendor/x", usage={"prompt_tokens": 10})
    assert seen and seen[0].get("allow_network") is False


# --- retries -------------------------------------------------------------------------------------


def test_a_retried_call_is_billed_for_every_attempt():
    one = estimate_call_usd(
        provider_id="anthropic", model_id="claude-sonnet-4-5", prompt_tokens=IN, completion_tokens=OUT
    )
    two = estimate_call_usd(
        provider_id="anthropic",
        model_id="claude-sonnet-4-5",
        prompt_tokens=IN,
        completion_tokens=OUT,
        attempts=2,
    )
    assert two.usd == pytest.approx(2 * one.usd)
    assert two.attempts == 2


def test_attempts_below_one_do_not_zero_the_charge():
    estimate = estimate_call_usd(
        provider_id="anthropic", model_id="claude-sonnet-4-5", prompt_tokens=IN, completion_tokens=OUT, attempts=0
    )
    assert estimate.usd > 0.0
    assert estimate.attempts == 1


def test_a_reported_cost_is_multiplied_by_attempts_too():
    estimate = settlement_usd(
        provider_id="openrouter", model_id="vendor/x", usage={"cost": 0.002, "prompt_tokens": 10}, attempts=3
    )
    assert estimate.usd == pytest.approx(0.006)


# --- the rendered surface ------------------------------------------------------------------------


def test_the_estimate_is_renderable_by_a_caller():
    estimate = settlement_usd(
        provider_id="anthropic", model_id="claude-sonnet-4-5", usage={"input_tokens": IN, "output_tokens": OUT}
    )
    payload = estimate.as_dict()
    assert payload["usd"] == pytest.approx(_published(3.00, 15.00), abs=1e-9)
    assert payload["known"] is True
    assert payload["basis"] == BASIS_PUBLISHED_PRICE
    assert "claude-sonnet-4-5" in payload["source"]
    assert "$0.049500" in estimate.describe()


def test_the_router_exposes_the_estimate_on_the_turn_usage_summary():
    manifest = _byok_manifest("anthropic", "claude-sonnet-4-5")
    summary = mfr._record_response_usage(
        manifest, SimpleNamespace(usage={"input_tokens": IN, "output_tokens": OUT})
    )
    assert summary is not None
    assert summary["cost_class"] == "paid_cloud"
    # usd_actual stays what the PROVIDER reported (nothing, here) so the meter's own "actual"
    # column keeps meaning exactly that; the priced figure is a separate, labelled field.
    assert summary["usd_actual"] is None
    assert summary["usd_priced"] == pytest.approx(_published(3.00, 15.00), abs=1e-9)
    assert summary["usd_priced_known"] is True
    assert summary["cost_estimate"]["basis"] == BASIS_PUBLISHED_PRICE
    assert summary["usd_priced_note"]


def test_the_unknown_case_is_labelled_as_an_upper_bound_for_the_ui():
    manifest = _byok_manifest("anthropic", "claude-not-yet-priced")
    summary = mfr._record_response_usage(
        manifest, SimpleNamespace(usage={"input_tokens": IN, "output_tokens": OUT})
    )
    assert summary is not None
    assert summary["usd_priced"] > 0.0
    assert summary["usd_priced_known"] is False
    assert summary["usd_priced_note"].startswith("up to $")


# --- the price table is a dated record, not a guess -----------------------------------------------


def test_the_price_table_is_dated_and_every_entry_is_a_positive_pair():
    assert model_pricing.PRICE_TABLE_READ_ON
    for slot, table in model_pricing._DIRECT_PRICE_TABLE.items():
        assert slot in PROVIDERS, f"{slot} is not a real provider slot"
        for model_id, rate in table.items():
            prompt_per_m, completion_per_m = rate
            assert prompt_per_m > 0 and completion_per_m > 0, f"{slot}/{model_id} priced at zero"
            assert completion_per_m >= prompt_per_m, f"{slot}/{model_id} output cheaper than input"


def test_every_model_the_picker_offers_has_a_price():
    """The dropdown must not be able to offer a model that settles at the ceiling."""
    from core.cloud_model_catalog import curated_models

    for slot in PROVIDERS:
        for row in curated_models(slot):
            price = lookup_model_price(slot, str(row["id"]))
            assert price.known is True, f"{slot}/{row['id']} is offered but has no published price"


def test_settlement_charges_what_the_picker_displays():
    """A user who reads $x per 1M in the model picker must not be billed at a different rate for
    an ordinary turn. The picker's row and the settlement table are two hand-maintained copies of
    the same published number, so they are pinned against each other."""
    from core.cloud_model_catalog import curated_models

    for slot in PROVIDERS:
        for row in curated_models(slot):
            price = lookup_model_price(slot, str(row["id"]), prompt_tokens=1_000)
            assert price.prompt_usd_per_token * 1e6 == pytest.approx(float(row["prompt_usd_per_m"]), abs=1e-6), (
                f"{slot}/{row['id']} prompt rate disagrees with the picker"
            )
            assert price.completion_usd_per_token * 1e6 == pytest.approx(
                float(row["completion_usd_per_m"]), abs=1e-6
            ), f"{slot}/{row['id']} completion rate disagrees with the picker"


def test_a_tiered_model_steps_up_above_its_prompt_threshold():
    """Gemini 2.5 Pro is $1.25/$10.00 up to a 200k prompt and $2.50/$15.00 above it. Quoting one
    tier for both would either overcharge every ordinary turn or undercharge every long one."""
    standard = lookup_model_price("google", "gemini-2.5-pro", prompt_tokens=100_000)
    large = lookup_model_price("google", "gemini-2.5-pro", prompt_tokens=250_000)
    assert standard.prompt_usd_per_token == pytest.approx(1.25 / 1e6)
    assert standard.completion_usd_per_token == pytest.approx(10.00 / 1e6)
    assert large.prompt_usd_per_token == pytest.approx(2.50 / 1e6)
    assert large.completion_usd_per_token == pytest.approx(15.00 / 1e6)
    assert lookup_model_price("google", "gemini-2.5-pro", prompt_tokens=200_000).prompt_usd_per_token == (
        standard.prompt_usd_per_token
    )


def test_a_tiered_models_settlement_follows_the_prompt_size():
    small = settlement_usd(
        provider_id="google", model_id="gemini-2.5-pro", usage={"prompt_tokens": 100_000, "completion_tokens": 1_000}
    )
    big = settlement_usd(
        provider_id="google", model_id="gemini-2.5-pro", usage={"prompt_tokens": 250_000, "completion_tokens": 1_000}
    )
    assert small.usd == pytest.approx(100_000 * 1.25 / 1e6 + 1_000 * 10.00 / 1e6, abs=1e-9)
    assert big.usd == pytest.approx(250_000 * 2.50 / 1e6 + 1_000 * 15.00 / 1e6, abs=1e-9)


def test_settlement_never_raises_on_a_malformed_usage_block():
    for usage in (None, "nonsense", {"prompt_tokens": "x"}, {"cost": "free"}, {"cost": -1}):
        estimate = settlement_usd(provider_id="anthropic", model_id="claude-sonnet-4-5", usage=usage)
        assert estimate.usd >= 0.0
