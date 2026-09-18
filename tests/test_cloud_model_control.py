"""cloud_model_control: the ONE switch path every surface (command, NL intent, UI) delegates to."""
from __future__ import annotations

import pytest

from core.cloud_model_control import force_catalog_refresh, set_auto_free_model, set_cloud_model


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)  # re-point the already-resolved home too
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: False)


def test_remote_surface_is_refused(no_key):
    ok, message, chosen = set_cloud_model("a/b", owner_local=False)
    assert not ok and "local session" in message and chosen == ""


def test_garbage_id_is_rejected_without_persisting(no_key):
    # A genuinely malformed id (spaces/illegal chars) is rejected. A bare id like "gpt-4.1-mini"
    # is now VALID (a direct provider's model), so the invalid case must use illegal characters.
    ok, message, _ = set_cloud_model("not a real model!", owner_local=True)
    assert not ok and "does not look like" in message
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == ""


def test_bare_direct_provider_id_requires_the_same_paid_confirmation(no_key):
    """A11 pass002: direct providers obey the SAME authority law as OpenRouter.

    Every listed model of a BYOK vendor is metered by that vendor, so a concrete pin there
    refuses unconfirmed exactly like a catalog-known PAID OpenRouter id, and persists only
    with the request-scoped confirmation."""
    ok, message, chosen = set_cloud_model("gpt-4.1-mini", provider="openai", owner_local=True)
    assert not ok and "PAID_MODEL_CONFIRM_REQUIRED" in message and chosen == ""
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model != "gpt-4.1-mini"
    ok, _message, chosen = set_cloud_model(
        "gpt-4.1-mini", provider="openai", owner_local=True, confirm_paid=True
    )
    assert ok and chosen == "gpt-4.1-mini"
    p = load_policy()
    assert p.model == "gpt-4.1-mini" and p.provider == "openai"


def test_provider_prefix_is_parsed(no_key):
    ok, _m, chosen = set_cloud_model(
        "anthropic:claude-sonnet-4-5", owner_local=True, confirm_paid=True
    )
    assert ok and chosen == "claude-sonnet-4-5"
    from core.cloud_escalation_policy import load_policy

    assert load_policy().provider == "anthropic"


def test_direct_paid_unconfirmed_refusal_carries_stable_code(no_key):
    ok, message, chosen = set_cloud_model("anthropic:claude-sonnet-4-5", owner_local=True)
    assert not ok and chosen == "" and "PAID_MODEL_CONFIRM_REQUIRED:" in message


def test_unknown_cost_id_fails_closed_then_saves_confirmed(no_key):
    """A11 pass002 fail-closed law replaces the old unknown-persists-with-warning contract.

    With no cached catalog row (cold start), an OpenRouter pin is refused with the stable
    MODEL_COST_UNKNOWN reason BEFORE anything persists; the explicit request-scoped
    confirmation lets it through WITHOUT the policy ever claiming to know its price."""
    ok, message, chosen = set_cloud_model("openai/gpt-4.1", owner_local=True)
    assert not ok and "MODEL_COST_UNKNOWN:" in message and chosen == ""
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model != "openai/gpt-4.1"
    ok, message, chosen = set_cloud_model(
        "openai/gpt-4.1", owner_local=True, confirm_paid=True
    )
    assert ok and chosen == "openai/gpt-4.1"
    assert load_policy().model == "openai/gpt-4.1"


def test_free_id_gets_no_paid_warning(no_key):
    ok, message, chosen = set_cloud_model("deepseek/deepseek-chat-v3-0324:free", owner_local=True)
    assert ok and chosen.endswith(":free")
    assert "spend your credits" not in message


def test_auto_fallback_accepts_only_a_catalog_verified_free_model(no_key, monkeypatch):
    free = type("FreeModel", (), {"model_id": "novel/lark-code:free"})()
    monkeypatch.setattr("core.openrouter_catalog.safe_free_models", lambda **kwargs: ((free,), 0.0))

    ok, message, chosen = set_auto_free_model("novel/lark-code:free", owner_local=True)
    assert ok and chosen == "novel/lark-code:free"
    assert "local first" in message
    from core.cloud_escalation_policy import load_policy

    policy = load_policy()
    assert policy.auto_free_model == "novel/lark-code:free"
    assert policy.free_cloud_enabled is True

    ok, _message, chosen = set_auto_free_model("novel/lark-paid", owner_local=True)
    assert not ok and chosen == ""
    assert load_policy().auto_free_model == "novel/lark-code:free"


def test_auto_fallback_is_owner_local_and_independent_from_explicit_pin(no_key, monkeypatch):
    monkeypatch.setattr("core.openrouter_catalog.safe_free_models", lambda **kwargs: ((), 0.0))
    set_cloud_model("paid/vendor-model", owner_local=True, confirm_paid=True)
    ok, _message, chosen = set_auto_free_model("auto", owner_local=True)
    assert ok and chosen == "auto"
    from core.cloud_escalation_policy import load_policy

    policy = load_policy()
    assert policy.model == "paid/vendor-model"
    assert policy.auto_free_model == "auto"
    assert set_auto_free_model("auto", owner_local=False)[0] is False


def test_default_resets_the_override(no_key):
    set_cloud_model("openai/gpt-4.1", owner_local=True, confirm_paid=True)
    ok, _message, chosen = set_cloud_model("default", owner_local=True)
    assert ok and chosen  # resolves to the concrete default id
    from core.cloud_escalation_policy import load_policy

    assert load_policy().model == ""


def test_failed_save_is_reported_not_claimed(no_key, monkeypatch):
    from core import cloud_escalation_policy as cep

    monkeypatch.setattr(cep, "save_policy", lambda policy: policy)  # write never lands
    ok, message, _ = set_cloud_model("a/b", owner_local=True, confirm_paid=True)
    assert not ok and "could not save" in message


def test_failed_default_reset_is_reported_not_claimed(no_key, monkeypatch):
    from core import cloud_escalation_policy as cep

    # Land a real override first, then make the write no-op so the reset cannot persist.
    set_cloud_model("openai/gpt-4.1", owner_local=True, confirm_paid=True)
    monkeypatch.setattr(cep, "save_policy", lambda policy: policy)
    ok, message, chosen = set_cloud_model("default", owner_local=True)
    assert not ok and "could not clear" in message and chosen == ""
    assert cep.load_policy().model == "openai/gpt-4.1", "the stale override must be reported, not claimed reset"


def test_refresh_wrapper_reports_real_counts(monkeypatch):
    import core.cloud_model_control as cmc

    class _M:
        def __init__(self, free):
            self._free = free
            self.fetched_at = "2026-07-18T00:00:00+00:00"

    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog", lambda **kw: (_M(True), _M(False), _M(True))
    )
    monkeypatch.setattr("core.openrouter_catalog.model_is_free", lambda m: m._free)
    result = cmc.force_catalog_refresh()
    assert result == {"ok": True, "total": 3, "free": 2, "fetched_at": "2026-07-18T00:00:00+00:00"}


def test_refresh_wrapper_fails_soft(monkeypatch):
    monkeypatch.setattr(
        "core.openrouter_catalog.refresh_openrouter_catalog",
        lambda **kw: (_ for _ in ()).throw(ConnectionError("down")),
    )
    result = force_catalog_refresh()
    assert result["ok"] is False and result["error"] == "ConnectionError"


def test_auto_refuses_for_a_direct_provider_instead_of_persisting_the_literal_auto(tmp_path, monkeypatch):
    """`auto` means "follow the live FREE catalog", which only OpenRouter publishes.

    Regression: when a previous turn (or a previous test in the same shard) left a DIRECT provider
    active, `auto` fell through and persisted the literal string "auto" as a model id — or stood in
    that provider's paid default. Both answer "pick a free model" by spending the user's key.
    """
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        from core import cloud_escalation_policy as cep
        from core.cloud_model_control import set_cloud_model

        for provider in ("custom", "openai", "groq"):
            cep.save_policy(cep.CloudEscalationPolicy(provider=provider, model=""))
            ok, message, chosen = set_cloud_model("auto", owner_local=True)
            assert ok is False, f"auto must refuse for {provider}, not stand in a paid model"
            assert chosen == ""
            assert "free" in message.lower() and "paid" in message.lower()
            assert cep.load_policy().model == "", "a refused switch must leave the model untouched"
    finally:
        runtime_paths.configure_runtime_home(None)


# ---- :free pins verify through the catalog; the free lane is never switched off in silence -----
#
# 2026-09-06, private profile: pinning `nvidia/nemotron-3.5-lightning:free` on a cold catalog cache
# saved the pin and turned `free_cloud_enabled` OFF (the suffix short-circuited before any row was
# read), so the grounded-synthesis lane declined every later turn with `free_cloud_disabled` and
# nothing in the reply said so. The pin door now verifies explicitly (one bounded refresh) and, when
# it cannot, leaves the lane as it was and says which of the two things happened.


def _free_row(model_id: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        model_id=model_id,
        name=model_id,
        output_modalities=("text",),
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=0.0,
        fetched_at="2026-09-06T00:00:00+00:00",
    )


def _catalog(monkeypatch, *, cached, fresh, calls):
    def _safe_all_models(*, allow_network=True):
        calls.append(allow_network)
        rows = tuple(fresh if allow_network else cached)
        return rows, (0.0 if rows else None)

    monkeypatch.setattr("core.openrouter_catalog.safe_all_models", _safe_all_models)


def test_free_pin_on_a_cold_cache_verifies_through_one_bounded_refresh_and_enables_the_lane(no_key, monkeypatch):
    calls: list[bool] = []
    _catalog(monkeypatch, cached=(), fresh=(_free_row("nvidia/nemotron-3.5-lightning:free"),), calls=calls)
    from core.cloud_escalation_policy import load_policy

    assert load_policy().free_cloud_enabled is False
    ok, message, chosen = set_cloud_model("nvidia/nemotron-3.5-lightning:free", owner_local=True)
    assert ok and chosen == "nvidia/nemotron-3.5-lightning:free"
    assert load_policy().free_cloud_enabled is True
    assert "left" not in message
    # cache first, then exactly one network verification
    assert calls == [False, True]


def test_free_pin_with_an_unreachable_catalog_leaves_the_lane_exactly_as_it_was_and_says_so(no_key, monkeypatch):
    calls: list[bool] = []
    _catalog(monkeypatch, cached=(), fresh=(), calls=calls)
    from core import cloud_escalation_policy as cep

    cep.set_free_cloud_enabled(True)
    ok, message, chosen = set_cloud_model("nvidia/nemotron-3.5-lightning:free", owner_local=True)
    assert ok and chosen == "nvidia/nemotron-3.5-lightning:free"
    # The defect: this read False here -- the lane switched off with nothing in the reply.
    assert cep.load_policy().free_cloud_enabled is True
    assert "could not be reached" in message and "left on as it was" in message
    assert calls == [False, True]


def test_free_pin_whose_variant_the_catalog_does_not_list_is_named_unlisted_and_never_enables_the_lane(no_key, monkeypatch):
    calls: list[bool] = []
    _catalog(monkeypatch, cached=(), fresh=(_free_row("deepseek/deepseek-chat-v3-0324:free"),), calls=calls)
    from core.cloud_escalation_policy import load_policy

    ok, message, chosen = set_cloud_model("vendor/pasted-suffix:free", owner_local=True)
    assert ok and chosen == "vendor/pasted-suffix:free"
    assert load_policy().free_cloud_enabled is False
    assert "lists no `vendor/pasted-suffix:free` row" in message and "left off as it was" in message


def test_a_confirmed_paid_pin_still_turns_the_free_lane_off_on_purpose(no_key, monkeypatch):
    _catalog(monkeypatch, cached=(), fresh=(), calls=[])
    from core import cloud_escalation_policy as cep

    cep.set_free_cloud_enabled(True)
    ok, _message, chosen = set_cloud_model("openai/gpt-4.1", owner_local=True, confirm_paid=True)
    assert ok and chosen == "openai/gpt-4.1"
    assert cep.load_policy().free_cloud_enabled is False


def test_the_classifier_touches_the_network_only_when_asked(monkeypatch):
    from core.cloud_model_control import classify_cloud_model_cost

    calls: list[bool] = []
    _catalog(monkeypatch, cached=(), fresh=(_free_row("x/y:free"),), calls=calls)
    cold = classify_cloud_model_cost(provider_id="openrouter", model_id="x/y:free")
    assert cold["cost_state"] == "free" and cold["row_verified_free"] is False
    assert cold["catalog_state"] == "unavailable"
    assert calls == [False]
    warm = classify_cloud_model_cost(provider_id="openrouter", model_id="x/y:free", allow_network=True)
    assert warm["row_verified_free"] is True and warm["catalog_state"] == "row"
    assert calls == [False, False, True]
