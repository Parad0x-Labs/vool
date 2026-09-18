"""Per-model limits come from what the provider published, not from one number typed five times.

Incident this is groundwork for: ``core.prompt_normalizer._max_output_tokens`` is the entire
output-budget policy and takes a single argument, the output_mode -- 240 tokens for plain_text,
220 for summary_block and json_object, 320 for action_plan, 700 for tool_intent. It cannot see
which provider serves the turn, so a 550B free cloud model is handed the same 240 tokens as
qwen3:0.6b; a reasoning model spent all 240 of them thinking and returned an empty completion,
and the user was shown "I couldn't get a live model response".

The context side is the same defect written five times: ``metadata["context_window"] = 128000``
is a literal at five sites in core.runtime_provider_defaults, while OpenRouter's own
``context_length`` and ``top_provider.max_completion_tokens`` sat parsed-but-unread.

These pin the read side. Two properties are load-bearing throughout:
  * 0 means UNPUBLISHED, and must stay distinguishable from a real cap of, say, 4096. A 0 read as
    a cap is the empty-completion failure again.
  * the resolution is OFFLINE. It runs on a boot path and, later, per turn.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.model_capability_catalog import (
    SOURCE_CATALOG_LIVE,
    SOURCE_CATALOG_STALE,
    SOURCE_CURATED,
    SOURCE_UNKNOWN,
    capability_for,
)
from core.openrouter_catalog import OpenRouterModel, parse_openrouter_catalog

PAYLOAD = {
    "data": [
        {
            "id": "deepseek/deepseek-chat-v3:free",
            "name": "DeepSeek V3 Free",
            "context_length": 163_840,
            "top_provider": {"max_completion_tokens": 16_384},
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "vendor/tight-cap",
            "name": "Tight Cap",
            "context_length": 8_192,
            "top_provider": {"max_completion_tokens": 4_096},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        },
        {
            "id": "vendor/no-published-cap",
            "name": "No Published Cap",
            "context_length": 32_768,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        },
    ]
}


class _RefusingFetch:
    """Stands in for the ONE outbound door so any network attempt is recorded, then fails.

    The catalog stopped using ``requests`` long ago; the old requests-module double therefore
    never intercepted anything and failed at setup (no such attribute). The current fetch
    boundary is ``open_remote_url`` imported into ``core.openrouter_catalog``; replacing THAT
    keeps the zero-network isolation and the ``calls`` assertions meaningful again."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, url, *args, **kwargs):
        self.calls.append(str(url))
        raise RuntimeError("this test may not reach the network")


@pytest.fixture
def catalog_cache(monkeypatch, tmp_path):
    """Write the fixture payload as the on-disk catalog cache and forbid the network.

    Returns a setter so a test can re-age the cache; the catalog's own ``open_remote_url``
    binding is replaced, so an attempted fetch is recorded rather than sent.
    """
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    previous_home = runtime_paths._VOOL_HOME_OVERRIDE
    runtime_paths.configure_runtime_home(tmp_path)
    import core.openrouter_catalog as cat

    refusing = _RefusingFetch()
    monkeypatch.setattr(cat, "open_remote_url", refusing)
    path = tmp_path / "openrouter_models_cache.json"
    monkeypatch.setattr(cat, "_cache_path", lambda: path)

    def write_cache(age_seconds: float) -> None:
        fetched_at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        path.write_text(json.dumps({"fetched_at": fetched_at, "payload": PAYLOAD}), encoding="utf-8")

    write_cache(60.0)
    yield write_cache, refusing, path
    runtime_paths.configure_runtime_home(previous_home)


# --- the OpenRouter lane ---------------------------------------------------------------------


def test_an_openrouter_lane_reports_the_published_window_and_cap_instead_of_the_hardcoded_128k(catalog_cache):
    capability = capability_for("openrouter-byok", "deepseek/deepseek-chat-v3:free")
    assert capability.context_window == 163_840, "the manifest literal 128000 is not this model's window"
    assert capability.max_output_tokens == 16_384
    assert capability.source == SOURCE_CATALOG_LIVE


def test_a_cache_past_the_freshness_window_is_reported_stale_so_a_wrong_budget_is_traceable(catalog_cache):
    write_cache, _refusing, _path = catalog_cache
    write_cache(9.0 * 24 * 3600)  # nine days
    capability = capability_for("openrouter-byok", "vendor/tight-cap")
    assert capability.context_window == 8_192, "a stale cache is still real published data"
    assert capability.max_output_tokens == 4_096
    assert capability.source == SOURCE_CATALOG_STALE


def test_resolving_an_openrouter_model_never_reaches_the_network(catalog_cache):
    _write_cache, refusing, _path = catalog_cache
    capability_for("openrouter-byok", "deepseek/deepseek-chat-v3:free")
    assert refusing.calls == [], "a per-turn budget lookup may not make an HTTP request"


def test_no_cache_at_all_is_unknown_and_still_makes_no_request(catalog_cache):
    """safe_all_models(allow_network=False) is cache-only only while a cache exists.

    Measured: with the cache file absent it falls through to a live refresh and swallows the
    failure into ((), None), so an unguarded lookup pays a 15s timeout on a first boot.
    """
    _write_cache, refusing, path = catalog_cache
    path.unlink()
    capability = capability_for("openrouter-byok", "deepseek/deepseek-chat-v3:free")
    assert capability.source == SOURCE_UNKNOWN
    assert (capability.context_window, capability.max_output_tokens) == (0, 0)
    assert refusing.calls == [], "no catalog is not a reason to go and fetch one here"


def test_a_model_the_catalog_does_not_carry_is_unknown_rather_than_a_neighbouring_row(catalog_cache):
    capability = capability_for("openrouter-byok", "vendor/never-heard-of-it")
    assert capability.source == SOURCE_UNKNOWN
    assert capability.context_window == 0, "must not inherit another model's 163840"


def test_the_bare_openrouter_lane_name_resolves_the_same_as_the_byok_one(catalog_cache):
    assert capability_for("openrouter", "vendor/tight-cap").context_window == 8_192


# --- zero means unpublished, not "no output allowed" -------------------------------------------


def test_an_unpublished_cap_is_zero_and_a_real_cap_of_4096_is_not_confused_with_it(catalog_cache):
    published = capability_for("openrouter-byok", "vendor/tight-cap")
    unpublished = capability_for("openrouter-byok", "vendor/no-published-cap")
    assert published.max_output_tokens == 4_096 and published.max_output_tokens_is_known is True
    assert unpublished.max_output_tokens == 0 and unpublished.max_output_tokens_is_known is False
    # The window is published for both, so "unknown cap" must not drag the window down with it.
    assert unpublished.context_window == 32_768 and unpublished.context_window_is_known is True
    assert unpublished.source == SOURCE_CATALOG_LIVE, "the row was found; only the cap is missing"


def test_an_unknown_result_reports_both_numbers_as_unknown_not_as_limits():
    capability = capability_for("ollama-local", "qwen3:0.6b")
    assert capability.source == SOURCE_UNKNOWN
    assert capability.context_window_is_known is False
    assert capability.max_output_tokens_is_known is False


# --- the direct-provider lanes -----------------------------------------------------------------


def test_a_direct_provider_lane_reads_its_curated_window_and_leaves_the_cap_unknown():
    capability = capability_for("anthropic-byok", "claude-sonnet-4-5")
    assert capability.context_window == 200_000
    assert capability.max_output_tokens == 0, "the curated rows publish no output cap; do not invent one"
    assert capability.source == SOURCE_CURATED


def test_a_curated_window_beats_the_128000_literal_the_manifests_ship_with():
    assert capability_for("openai-byok", "gpt-4.1-mini").context_window == 1_047_576
    assert capability_for("google-byok", "gemini-2.5-pro").context_window == 1_048_576
    assert capability_for("deepseek-byok", "deepseek-chat").context_window == 65_536


def test_the_kimi_lane_reads_moonshots_curated_row_because_it_is_the_same_endpoint():
    """kimi-remote registers base_url https://api.moonshot.ai/v1, identical to the moonshot entry
    in core.cloud_providers, so its model ids are moonshot's ids."""
    capability = capability_for("kimi-remote", "kimi-k2-0711-preview")
    assert capability.context_window == 131_072
    assert capability.source == SOURCE_CURATED


def test_a_model_missing_from_a_providers_curated_list_is_unknown():
    capability = capability_for("anthropic-byok", "claude-not-in-the-list")
    assert capability.source == SOURCE_UNKNOWN
    assert capability.context_window == 0


def test_a_user_pointed_endpoint_is_never_attributed_to_a_providers_catalog():
    """tether-remote and openai-compatible-remote are aimed at whatever host the user configures,
    so an id that happens to look like OpenAI's must not pick up OpenAI's 1M-token window."""
    for lane in ("tether-remote", "openai-compatible-remote"):
        capability = capability_for(lane, "gpt-4.1-mini")
        assert capability.source == SOURCE_UNKNOWN, lane
        assert capability.context_window == 0, lane


def test_an_empty_or_unrecognised_lane_resolves_to_unknown_without_raising():
    for lane, model in (("", "gpt-4.1-mini"), ("something-new", "x"), ("openrouter-byok", "")):
        assert capability_for(lane, model).source == SOURCE_UNKNOWN, lane


# --- the catalog parse the resolution depends on -----------------------------------------------


def test_the_catalog_parses_the_output_cap_the_provider_publishes():
    models = {m.model_id: m for m in parse_openrouter_catalog(PAYLOAD, fetched_at="2026-07-28T00:00:00+00:00")}
    assert models["deepseek/deepseek-chat-v3:free"].max_output_tokens == 16_384
    assert models["vendor/tight-cap"].max_output_tokens == 4_096


@pytest.mark.parametrize(
    "label, top_provider",
    [
        ("no top_provider block", {}),
        ("explicit null block", {"top_provider": None}),
        ("null cap", {"top_provider": {"max_completion_tokens": None}}),
        ("blank cap", {"top_provider": {"max_completion_tokens": ""}}),
    ],
)
def test_an_absent_output_cap_parses_as_unknown_without_dropping_the_model(label: str, top_provider: dict):
    """A null top_provider must not take the whole catalog with it.

    safe_all_models swallows a parse failure, so raising here would empty the catalog for every
    model rather than lose one row.
    """
    item = {"id": "x/one", "context_length": 4_096, "pricing": {"prompt": "0", "completion": "0"}}
    item.update(top_provider)
    models = parse_openrouter_catalog({"data": [item]}, fetched_at="2026-07-28T00:00:00+00:00")
    assert len(models) == 1, label
    assert models[0].max_output_tokens == 0, label
    assert models[0].context_length == 4_096, label


def test_the_output_cap_defaults_to_unknown_so_constructions_written_before_it_still_build():
    """Five existing tests build OpenRouterModel by keyword without this field."""
    model = OpenRouterModel(
        model_id="x/legacy",
        name="Legacy",
        context_length=163_840,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=None,
        supported_parameters=(),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-07-20T00:00:00+00:00",
    )
    assert model.max_output_tokens == 0


# ------------------------------------------------------------------------------------------
# A provider-controlled row must never be able to empty the catalog
# ------------------------------------------------------------------------------------------


class TestOneBadRowCannotTakeDownTheCatalog:
    """`int(top_provider["max_completion_tokens"])` was undefended when this field was added.

    An adversarial review measured the blast radius: `"16384.0"`, `"unlimited"`, `[4096]` and
    `{"value": 4096}` each raised, and because `parse_openrouter_catalog` runs inside
    `safe_all_models` the exception is swallowed into an EMPTY catalog. One malformed row therefore
    took down free-model picking, pricing, the usage meter and escalation for every caller, and sent
    them to a blocking 15s network refresh. Measured through the runtime entry point:
    HEAD `models=2, network_attempts=[]` vs the change `models=0,
    network_attempts=['https://openrouter.ai/api/v1/models']`.

    OpenRouter controls this payload, so the shapes below are not hypothetical.
    """

    @staticmethod
    def _payload(cap):
        return {
            "data": [
                {"id": "vendor/good", "context_length": 8192, "top_provider": {"max_completion_tokens": 4096}},
                {"id": "vendor/hostile", "context_length": 8192, "top_provider": {"max_completion_tokens": cap}},
            ]
        }

    @pytest.mark.parametrize(
        "cap", ["unlimited", [4096], {"value": 4096}, object(), float("nan"), float("inf")]
    )
    def test_an_unparseable_cap_costs_one_field_not_the_whole_catalog(self, cap):
        from core.openrouter_catalog import parse_openrouter_catalog

        models = parse_openrouter_catalog(self._payload(cap), fetched_at="2026-07-29T00:00:00+00:00")

        assert len(models) == 2, "every row must survive one row's bad field"
        assert models[0].max_output_tokens == 4096, "the healthy row keeps its real cap"
        assert models[1].max_output_tokens == 0, "the hostile row reads as unpublished"

    def test_a_float_shaped_string_is_a_published_cap_not_an_absent_one(self):
        from core.openrouter_catalog import parse_openrouter_catalog

        models = parse_openrouter_catalog(
            self._payload("16384.0"), fetched_at="2026-07-29T00:00:00+00:00"
        )
        assert models[1].max_output_tokens == 16384, "carelessly written is not the same as absent"

    def test_a_boolean_is_never_a_cap(self):
        """`int(True)` is 1. A one-token ceiling is worse than admitting the cap is unknown."""
        from core.openrouter_catalog import parse_openrouter_catalog

        models = parse_openrouter_catalog(self._payload(True), fetched_at="2026-07-29T00:00:00+00:00")
        assert models[1].max_output_tokens == 0

    @pytest.mark.parametrize("cap", [-1, -4096, "-8192"])
    def test_a_negative_cap_reads_as_unpublished_not_as_a_negative_budget(self, cap):
        """A negative ceiling is worse than an unknown one.

        0 means "not published" and callers skip the cap. A negative value that survived would be
        applied as a real ceiling by a downstream `min(budget, cap)`, asking a provider for fewer
        than zero tokens. Unpinned until an adversarial pass removed the `max(0, ...)` and every
        test still passed.
        """
        from core.openrouter_catalog import parse_openrouter_catalog

        models = parse_openrouter_catalog(self._payload(cap), fetched_at="2026-07-29T00:00:00+00:00")
        assert models[1].max_output_tokens == 0
