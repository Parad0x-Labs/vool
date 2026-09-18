"""Live free-model surface: the list is real catalog data, and `auto` splits chat/coding lanes.

Locks down the fix for the hallucinated-model-list failure: asked "what free AIs are on offer",
the local model invented ids (`llama3:free`, `sdxl:free`). These pin the deterministic path — the
free list comes from the (cached) OpenRouter catalog, "auto" picks real general+coding models, and
with no data at all VOOL says so instead of guessing.
"""
from __future__ import annotations

import json

import pytest

from core.openrouter_catalog import (
    model_is_coding,
    model_is_free,
    parse_openrouter_catalog,
    pick_auto_free_models,
    safe_free_models,
)

PAYLOAD = {
    "data": [
        {"id": "bigco/mega-chat", "name": "Mega Chat", "context_length": 200000,
         "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"}},
        {"id": "deepseek/deepseek-chat-v3:free", "name": "DeepSeek V3 Free", "context_length": 163840,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "qwen/qwen3-coder:free", "name": "Qwen3 Coder Free", "context_length": 262144,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        {"id": "vendor/guardrail-safety:free", "name": "Safety Moderation", "context_length": 8192,
         "pricing": {"prompt": "0", "completion": "0", "request": "0"}},
        # Families that are also ordinary English words -- paid, so the free-list pins above
        # are unaffected. They exist to pin the graduated name-evidence rule.
        {"id": "upstage/solar-pro-3", "name": "Solar Pro 3", "context_length": 131072,
         "pricing": {"prompt": "0.00000015", "completion": "0.0000006", "request": "0"}},
        {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B", "context_length": 131072,
         "pricing": {"prompt": "0.0000001", "completion": "0.0000003", "request": "0"}},
        {"id": "nousresearch/hermes-4-70b", "name": "Hermes 4 70B", "context_length": 131072,
         "pricing": {"prompt": "0.0000001", "completion": "0.0000003", "request": "0"}},
        {"id": "tiiuae/falcon-180b", "name": "Falcon 180B", "context_length": 8192,
         "pricing": {"prompt": "0.000001", "completion": "0.000002", "request": "0"}},
        {"id": "ibm/granite-4-tiny", "name": "Granite 4 Tiny", "context_length": 131072,
         "pricing": {"prompt": "0.00000005", "completion": "0.0000002", "request": "0"}},
    ]
}


@pytest.fixture
def cached_catalog(monkeypatch, tmp_path):
    """Write the fixture payload as the on-disk cache and point the catalog at it (no network).

    The runtime home is redirected as well as the env var: setting VOOL_HOME alone leaves
    runtime_paths pointing at the real home, so a test that persists a cloud policy writes it
    outside the tmp dir and the next test reads it back.
    """
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    previous_home = runtime_paths._VOOL_HOME_OVERRIDE
    runtime_paths.configure_runtime_home(tmp_path)
    import core.openrouter_catalog as cat

    path = tmp_path / "cache.json"
    from datetime import datetime, timezone

    path.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "payload": PAYLOAD}))
    monkeypatch.setattr(cat, "_cache_path", lambda: path)
    monkeypatch.setattr(cat, "refresh_openrouter_catalog", lambda **kw: (_ for _ in ()).throw(RuntimeError("no network in tests")))
    yield path
    runtime_paths.configure_runtime_home(previous_home)


def test_free_detection_uses_real_pricing():
    models = parse_openrouter_catalog(PAYLOAD, fetched_at="2026-07-18T00:00:00+00:00")
    frees = {m.model_id for m in models if model_is_free(m)}
    assert "bigco/mega-chat" not in frees, "a priced model must never be listed as free"
    assert {"deepseek/deepseek-chat-v3:free", "qwen/qwen3-coder:free"} <= frees
    assert model_is_coding(next(m for m in models if m.model_id == "qwen/qwen3-coder:free"))


def test_safe_free_models_serves_cache_without_network(cached_catalog):
    free, age = safe_free_models(allow_network=True)  # refresh raises; must fall back to cache
    assert {m.model_id for m in free} == {
        "deepseek/deepseek-chat-v3:free", "qwen/qwen3-coder:free", "vendor/guardrail-safety:free"
    }
    assert age is not None


def test_safe_free_models_with_no_data_returns_empty_not_invented(monkeypatch, tmp_path):
    import core.openrouter_catalog as cat

    monkeypatch.setattr(cat, "_cache_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cat, "refresh_openrouter_catalog", lambda **kw: (_ for _ in ()).throw(RuntimeError("offline")))
    free, age = safe_free_models(allow_network=True)
    assert free == () and age is None


def test_auto_picks_split_general_and_coding_and_skip_utility(cached_catalog):
    picks = pick_auto_free_models(allow_network=False)
    assert picks["coding"] == "qwen/qwen3-coder:free"
    assert picks["general"] == "deepseek/deepseek-chat-v3:free", "safety/moderation rows must not win the general lane"


def test_cloud_models_command_lists_real_ids(cached_catalog):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_models_command as cmd

    reply = cmd("cloud models", owner_local=True)
    assert "deepseek/deepseek-chat-v3:free" in reply and "qwen/qwen3-coder:free" in reply
    assert "live catalog" in reply
    coding = cmd("cloud models coding", owner_local=True)
    assert "qwen/qwen3-coder:free" in coding


def test_free_models_intent_answers_from_catalog(cached_catalog):
    from core.agent_runtime.fast_command_surface import maybe_handle_free_models_intent as intent

    reply = intent("ok lets connect to it and see what  free ai's are on offer?", owner_local=True)
    assert reply is not None and "deepseek/deepseek-chat-v3:free" in reply
    # Guards: neither a question about VOOL nor a coding request may be hijacked.
    assert intent("is vool a free ai?", owner_local=True) is None
    assert intent("write code to list free models via the api", owner_local=True) is None


def test_no_data_reply_admits_it_cannot_check(monkeypatch, tmp_path):
    import core.openrouter_catalog as cat

    monkeypatch.setattr(cat, "_cache_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cat, "refresh_openrouter_catalog", lambda **kw: (_ for _ in ()).throw(RuntimeError("offline")))
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_models_command as cmd

    reply = cmd("cloud models", owner_local=True)
    assert "will not" in reply and "guess" in reply
    assert "could not reach openrouter.ai" in reply, "no catalog at all is the one true network claim"


# --- "no free models" must not be reported as "could not reach openrouter.ai" ---------
#
# safe_free_models returns age=None only when there is no usable catalog. An empty free list with
# a real age means the fetch SUCCEEDED and nothing happened to be free — a different fact.


def test_zero_free_models_does_not_claim_a_network_failure(monkeypatch):
    """The audited honesty defect: a successfully-read catalog with no free rows reported an
    openrouter.ai outage that never happened."""
    import core.openrouter_catalog as cat
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_models_command as cmd

    monkeypatch.setattr(cat, "safe_free_models", lambda **kw: ((), 30.0))
    reply = cmd("cloud models", owner_local=True)
    assert "could not reach" not in reply and "no cached catalog" not in reply
    assert "none of its models are free" in reply


def test_free_but_no_coding_match_says_so_instead_of_an_empty_list(cached_catalog, monkeypatch):
    """`cloud models coding` with no coding match previously printed a free-model count followed
    by nothing at all."""
    import core.openrouter_catalog as cat
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_models_command as cmd

    monkeypatch.setattr(cat, "model_is_coding", lambda m: False)
    reply = cmd("cloud models coding", owner_local=True)
    assert "none of them are coding models" in reply
    assert "could not reach" not in reply
    assert reply.rstrip().endswith(".")


def test_coding_header_counts_what_it_lists(cached_catalog):
    """The header counted every free model while the list was filtered to coding ones, so
    `cloud models coding` announced 17 and printed 1."""
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_models_command as cmd

    reply = cmd("cloud models coding", owner_local=True)
    listed = reply.count("\n- `")
    assert reply.startswith(f"{listed} free coding model"), reply.splitlines()[0]
    assert "3 free" not in reply, "must not report the unfiltered free total as the coding count"


def test_model_lookup_answers_from_catalog(cached_catalog):
    """'what about HY3?' previously fell to the local model, which wrongly said it was not free."""
    from core.agent_runtime.fast_command_surface import maybe_handle_model_lookup_intent as lookup

    reply = lookup("what about deepseek?", owner_local=True)
    assert reply is not None and "deepseek/deepseek-chat-v3:free" in reply and "FREE" in reply
    paid = lookup("is mega-chat free?", owner_local=True)
    assert paid is not None and "bigco/mega-chat" in paid and "paid" in paid
    # No catalog match / plain chat -> the model answers, no hijack.
    assert lookup("what about lunch?", owner_local=True) is None
    assert lookup("why is the sky blue?", owner_local=True) is None


def test_cloud_models_all_shows_every_free_model(cached_catalog):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_models_command as cmd

    reply = cmd("cloud models all", owner_local=True)
    assert "vendor/guardrail-safety:free" in reply, "`all` must include rows beyond the top cut"


def test_cloud_model_auto_persists(monkeypatch, tmp_path, cached_catalog):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_model_command as cmd

    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)
    reply = cmd("cloud model auto", owner_local=True)
    assert "auto" in reply and "qwen/qwen3-coder:free" in reply
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == "auto"


def _one_model(pricing_item: dict):
    from core.openrouter_catalog import parse_openrouter_catalog

    return parse_openrouter_catalog({"data": [pricing_item]}, fetched_at="2026-07-19T00:00:00Z")[0]


@pytest.mark.parametrize(
    "label, item",
    [
        ("no pricing block", {"id": "x/none"}),
        ("empty pricing block", {"id": "x/empty", "pricing": {}}),
        ("null prices", {"id": "x/nulls", "pricing": {"prompt": None, "completion": None, "request": None}}),
        ("malformed prices", {"id": "x/bad", "pricing": {"prompt": "n/a", "completion": "n/a", "request": "n/a"}}),
        ("blank strings", {"id": "x/blank", "pricing": {"prompt": "", "completion": "", "request": ""}}),
    ],
)
def test_unpublished_pricing_is_never_treated_as_free(label: str, item: dict) -> None:
    """Unknown pricing is not free -- the provider controls this payload.

    Coercing a missing price to 0.0 makes "no price published" identical to "free", so a catalog
    entry with no pricing block would be offered and auto-switched to as free while charging the
    user's key at a rate nobody read.
    """
    from core.openrouter_catalog import model_is_free, model_pricing_is_known

    model = _one_model(item)
    assert model_pricing_is_known(model) is False, label
    assert model_is_free(model) is False, label


def test_an_explicitly_zero_priced_model_is_still_free() -> None:
    from core.openrouter_catalog import model_is_free

    model = _one_model({"id": "x/honest:free", "pricing": {"prompt": "0", "completion": "0", "request": "0"}})
    assert model_is_free(model) is True


def test_unpublished_pricing_is_not_ranked_as_the_cheapest_option() -> None:
    """An unknown cost must not win a cost comparison by being read as zero."""
    from core.openrouter_catalog import parse_openrouter_catalog, recommend_openrouter_model

    models = parse_openrouter_catalog(
        {
            "data": [
                {"id": "x/unknown", "context_length": 8000, "supported_parameters": []},
                {
                    "id": "x/known",
                    "context_length": 8000,
                    "supported_parameters": [],
                    "pricing": {"prompt": "0.000001", "completion": "0.000001", "request": "0"},
                },
            ]
        },
        fetched_at="2026-07-19T00:00:00Z",
    )
    picked = recommend_openrouter_model(
        models, required_context=1000, required_parameters=(), input_tokens=100, output_tokens=100
    )
    assert picked is not None
    assert picked.model_id == "x/known"


def test_a_missing_per_request_fee_does_not_hide_the_whole_catalog() -> None:
    """OpenRouter publishes no per-request fee for any model.

    Requiring one classed the entire live catalog as unknown -- measured 0 of 338 readable,
    including all 14 ids ending ":free" -- which is fail-safe in direction and unusable in
    practice. Absent means no surcharge; the per-token pair is what must be published.
    """
    from core.openrouter_catalog import model_is_free, model_pricing_is_known

    model = _one_model({"id": "tencent/hy3:free", "pricing": {"prompt": "0", "completion": "0"}})
    assert model_pricing_is_known(model) is True
    assert model_is_free(model) is True


def test_a_negative_price_is_malformed_not_free() -> None:
    from core.openrouter_catalog import model_is_free

    model = _one_model({"id": "x/negative", "pricing": {"prompt": "-1", "completion": "0"}})
    assert model_is_free(model) is False


def test_the_cost_recommender_is_alive_on_the_live_catalog_shape() -> None:
    """An audit found the recommender returning None for every input and every live model.

    The cause was the per-request-fee requirement: no model publishes one, so estimate_cost was
    None everywhere and the recommender filtered out its whole candidate set. This pins the live
    payload shape -- per-token pair published, `request` absent -- so the recommender cannot
    silently go dead again while still looking functional.
    """
    from core.openrouter_catalog import parse_openrouter_catalog, recommend_openrouter_model

    models = parse_openrouter_catalog(
        {
            "data": [
                {
                    "id": "vendor/cheap",
                    "context_length": 262144,
                    "supported_parameters": ["tools", "response_format"],
                    "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
                },
                {
                    "id": "vendor/pricey",
                    "context_length": 262144,
                    "supported_parameters": ["tools", "response_format"],
                    "pricing": {"prompt": "0.00001", "completion": "0.00002"},
                },
            ]
        },
        fetched_at="2026-07-19T00:00:00Z",
    )
    assert all(m.estimate_cost(input_tokens=1000, output_tokens=500) is not None for m in models)
    picked = recommend_openrouter_model(
        models,
        required_context=32000,
        required_parameters=("tools", "response_format"),
        input_tokens=1000,
        output_tokens=500,
    )
    assert picked is not None, "the recommender must return a candidate for the live catalog shape"
    assert picked.model_id == "vendor/cheap"


def test_a_family_word_used_as_an_english_word_never_reaches_the_catalog(cached_catalog):
    """MEASURED on the s50 rig (3e1bf797): both "solar" questions below were answered from the
    OpenRouter catalog about `upstage/solar-pro-*`. The rule is structural and covers every
    family whose name is also an English word -- nothing here lists astronomy or birds."""
    from core.agent_runtime.fast_command_surface import maybe_handle_model_lookup_intent as lookup

    for text in (
        "What is the smallest planet in our solar system?",
        "How does solar power work?",
        "Is solar power free?",                 # catalog frame, but an attributive modifier
        "What's the price of a falcon?",        # catalog frame, but a common noun under 'a'
        "Is there a llama at the zoo?",
        "Why do llamas spit?",
        "Why is granite so heavy?",
        "Was Hermes the messenger god?",
        "Is the llama a good pet?",
    ):
        assert lookup(text, owner_local=True) is None, text


def test_a_family_word_used_as_a_name_still_reaches_the_catalog(cached_catalog):
    from core.agent_runtime.fast_command_surface import maybe_handle_model_lookup_intent as lookup

    for text, model_id in (
        ("what about solar?", "upstage/solar-pro-3"),
        ("is solar free?", "upstage/solar-pro-3"),
        ("is there a hermes model?", "nousresearch/hermes-4-70b"),
        ("what about llama pricing?", "meta-llama/llama-3.3-70b-instruct"),
        ("is falcon-180b available?", "tiiuae/falcon-180b"),   # name by shape: bare '?' suffices
        ("why granite?", "ibm/granite-4-tiny"),
        ("what about hermes on openrouter?", "nousresearch/hermes-4-70b"),
    ):
        reply = lookup(text, owner_local=True)
        assert reply is not None and model_id in reply, (text, reply)


def test_a_bare_question_mark_is_not_a_catalog_frame_for_a_bare_family_word(cached_catalog):
    """Weak name evidence needs a catalog frame; strong evidence (digit/hyphen) does not."""
    from core.agent_runtime.fast_command_surface import maybe_handle_model_lookup_intent as lookup

    assert lookup("solar?", owner_local=True) is None
    assert lookup("falcon-180b?", owner_local=True) is not None


def test_cache_only_read_with_no_cache_answers_no_data_without_network(monkeypatch, tmp_path):
    """"Cache-only" is the model-list GET's contract: with no cache file it answers "no data"
    instantly. It used to fall through to a BLOCKING 15-second refresh (the missing-file
    except-branch), so the first Quick Pick open after connecting a key stalled inside the
    popover fetch, and a vetoed or offline refresh then served ``models: []`` no differently
    from "the provider lists nothing" (measured on the owner's box, 2026-09-10)."""
    import core.openrouter_catalog as cat

    monkeypatch.setattr(cat, "_cache_path", lambda: tmp_path / "missing.json")

    def _no_network(**kw):
        raise AssertionError("a cache-only read must never reach the network")

    monkeypatch.setattr(cat, "refresh_openrouter_catalog", _no_network)
    models, age = cat.safe_all_models(allow_network=False)
    assert models == () and age is None
    free, free_age = cat.safe_free_models(allow_network=False)
    assert free == () and free_age is None


def test_cache_only_read_with_unreadable_cache_answers_no_data_without_network(monkeypatch, tmp_path):
    """A corrupt cache file is the same contract: no data, no smuggled refresh."""
    import core.openrouter_catalog as cat

    path = tmp_path / "corrupt.json"
    path.write_text("{not json")
    monkeypatch.setattr(cat, "_cache_path", lambda: path)
    monkeypatch.setattr(
        cat, "refresh_openrouter_catalog", lambda **kw: (_ for _ in ()).throw(AssertionError("network on a cache-only read"))
    )
    models, age = cat.safe_all_models(allow_network=False)
    assert models == () and age is None


def test_networked_read_still_refreshes_when_the_cache_is_absent(monkeypatch, tmp_path):
    """The allow_network=True contract is unchanged: missing cache still triggers the real
    refresh (fresh-or-refresh), which is what the explicit ?refresh=1 door exercises."""
    import core.openrouter_catalog as cat

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from datetime import datetime, timezone

    monkeypatch.setattr(
        cat, "_cache_path", lambda: tmp_path / "cache.json"
    )
    monkeypatch.setattr(
        cat,
        "refresh_openrouter_catalog",
        lambda **kw: parse_openrouter_catalog(PAYLOAD, fetched_at=datetime.now(timezone.utc).isoformat()),
    )
    models, age = cat.safe_all_models(allow_network=True)
    assert {m.model_id for m in models} == {m["id"] for m in PAYLOAD["data"]}
    assert age is not None
