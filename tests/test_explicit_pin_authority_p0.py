"""P0-B — explicit-model-pin authority.

Invariant under test: when the user EXPLICITLY selects model X, either X runs under the existing
deterministic gates, or VOOL visibly explains why X could not run. No other model silently answers
instead. Auto and sticky-Auto preferences keep their normal free-cloud fallback.

The real routing path is exercised (``MemoryFirstRouter._execute_provider_task``); only the provider
HTTP transport is sealed (a recorder standing in for ``requests.post``). The free-cloud fallback
boundary is NOT mocked away -- a real free OpenRouter lane is registered so "did a different free
model answer" is measured, not assumed. No real network, no real spend, no model launch.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

DUMMY_KEY = "sk-ant-DUMMY-NOT-A-REAL-KEY"
ANTHROPIC_HOST = "https://api.anthropic.com"
OPENROUTER_HOST = "https://openrouter.ai"


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import os_consent_gate, runtime_paths
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    yield
    os_consent_gate.set_consent_override_for_tests(None)
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


class _PostRecorder:
    """Records provider POSTs; never calls out. Returns a canned success."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url, *args, **kwargs) -> Any:
        self.urls.append(str(url))
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "choices": [{"message": {"content": "cloud answer"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
            status_code=200,
        )

    @property
    def posted_to_anthropic(self) -> bool:
        return any(url.startswith(ANTHROPIC_HOST) for url in self.urls)

    @property
    def posted_to_openrouter(self) -> bool:
        return any(OPENROUTER_HOST in url for url in self.urls)


@pytest.fixture
def posts(monkeypatch) -> _PostRecorder:
    recorder = _PostRecorder()
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", recorder)
    return recorder


@pytest.fixture
def anthropic_lane(monkeypatch):
    from unittest import mock

    import core.runtime_provider_defaults as rpd

    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    with mock.patch("core.credential_store.has_credential", lambda slot: slot == "llm.cloud.anthropic"):
        provider_id = rpd.activate_provider_byok("anthropic", env={"ANTHROPIC_API_KEY": DUMMY_KEY})
    assert provider_id, "the Anthropic BYOK lane must register from a key"
    from storage.model_provider_manifest import list_provider_manifests

    return next(m for m in list_provider_manifests(enabled_only=True) if m.provider_name == "anthropic-byok")


@pytest.fixture
def free_openrouter_lane(monkeypatch):
    """A catalog-verified-free OpenRouter manifest and a key, so the free-cloud boost is REACHABLE.

    This is the substitute that must NOT answer for a failed explicit pin, and that MUST answer for
    Auto. Its presence is what makes "no other model answered" a real measurement.
    """
    from unittest import mock

    from core import openrouter_catalog
    from core.openrouter_catalog import OpenRouterModel
    from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

    free_model = OpenRouterModel(
        model_id="vendor/free-chat:free",
        name="Free Chat",
        context_length=262144,
        prompt_usd_per_token=0.0,
        completion_usd_per_token=0.0,
        request_usd=None,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-07-20T00:00:00+00:00",
    )
    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **kw: ((free_model,), 0.0))
    monkeypatch.setattr(openrouter_catalog, "safe_free_models", lambda **kw: ((free_model,), 0.0))
    # A present key so the free-cloud broker is buildable and owner-local free routing is allowed.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-DUMMY-NOT-A-REAL-KEY")
    monkeypatch.setattr("core.memory_first_router._openrouter_key_present", lambda: True)
    monkeypatch.setattr("core.credential_store.has_credential", lambda slot: slot == CLOUD_OR_SLOT)
    manifest = ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name="vendor/free-chat:free",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "api_path": "/chat/completions"},
        metadata={"cost_class": "paid_cloud"},
    )
    upsert_provider_manifest(manifest)
    return manifest


CLOUD_OR_SLOT = "llm.cloud.openrouter"


def _set_policy(**kw):
    from core import cloud_escalation_policy as cep

    return cep.save_policy(replace(cep.load_policy(), **kw))


def _task():
    return SimpleNamespace(task_id="task-1", task_summary="estimate a used car price for me")


def _run_turn(*, source_context, allow_paid_fallback, task_kind="reasoning", rank_override=None):
    """Drive the real routing path once. Identity is stamped (the A9 gate is real, not bypassed)."""
    from unittest import mock

    from core.memory_first_router import MemoryFirstRouter

    ctx = {
        "turn_id": "pin-turn",
        "session_id": "pin-session",
        **(source_context or {}),
    }
    interpretation = SimpleNamespace(reconstructed_text="estimate a used car price for me")
    report = SimpleNamespace(
        retrieval_confidence=0.2,
        to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
    )
    context_result = SimpleNamespace(
        retrieval_confidence_score=0.2,
        report=report,
        assembled_context=lambda: "",
        context_snippets=lambda: [],
    )
    patches = [
        mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None),
        mock.patch("core.memory_first_router.should_probe_health", return_value=False),
    ]
    if rank_override is not None:
        patches.append(mock.patch("core.memory_first_router.rank_provider_candidates", return_value=rank_override))
    with patches[0], patches[1], (patches[2] if len(patches) > 2 else _null_ctx()):
        return MemoryFirstRouter()._execute_provider_task(
            task=_task(),
            classification={"task_class": "chat_conversation"},
            interpretation=interpretation,
            context_result=context_result,
            persona=SimpleNamespace(tone="neutral"),
            task_hash="hash",
            task_kind=task_kind,
            output_mode="plain_text",
            allow_paid_fallback=allow_paid_fallback,
            provider_role="queen",
            surface="cli",
            source_context=ctx,
        )


class _null_ctx:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _paid_events(events):
    return [
        e["event_type"]
        for e in events
        if e.get("event_type") in {"paid_call.reserved", "paid_call.settled", "paid_call.released"}
    ]


def _capture_events():
    from core.runtime_task_events import new_runtime_event_stream_id, register_runtime_event_sink

    events: list[dict] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))
    return stream_id, events


# ============================================================================================
# 1. Paid pin succeeds: exactly the selected model executes.
# ============================================================================================
def test_case1_paid_pin_succeeds_only_the_selected_model_runs(anthropic_lane, free_openrouter_lane, posts) -> None:
    _set_policy(mode="off", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id, "model_selection": "pin"},
        allow_paid_fallback=False,
    )
    assert decision.used_model is True and decision.output_text == "cloud answer"
    assert decision.model_name == anthropic_lane.model_name
    assert posts.posted_to_anthropic, posts.urls
    assert not posts.posted_to_openrouter, f"the free model must not run when the pin succeeds: {posts.urls}"


# ============================================================================================
# 2. Unreadable policy refuses paid reservation; a free model is available.
# ============================================================================================
def test_case2_unreadable_policy_denies_paid_pin_no_free_substitution(anthropic_lane, free_openrouter_lane, posts, monkeypatch) -> None:
    _set_policy(mode="off", daily_cap=0, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    monkeypatch.setattr("core.cloud_escalation_policy.counter_store_is_corrupt", lambda: True)
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id, "model_selection": "pin"},
        allow_paid_fallback=False,
    )
    assert decision.used_model is False
    assert decision.source == "selected_model_blocked", decision.source
    assert decision.details.get("block_reason") == "cloud_policy_unreadable", decision.details
    assert not posts.posted_to_anthropic and not posts.posted_to_openrouter, posts.urls


# ============================================================================================
# 3. Paid reservation denied by a USD spend gate.
# ============================================================================================
def test_case3_usd_spend_cap_denies_paid_pin_no_free_substitution(anthropic_lane, free_openrouter_lane, posts, monkeypatch) -> None:
    from core import paid_call_reservation
    from core.model_spend_ledger import SpendLimits

    _set_policy(mode="off", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    monkeypatch.setattr(
        paid_call_reservation,
        "spend_limits",
        lambda: SpendLimits(per_call_usd=0.25, per_task_usd=0.25, daily_usd=0.0, monthly_usd=25.0, daily_call_cap=25),
    )
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id, "model_selection": "pin"},
        allow_paid_fallback=False,
    )
    assert decision.used_model is False and decision.source == "selected_model_blocked"
    assert str(decision.details.get("block_reason") or "").endswith("_spend_cap_exceeded"), decision.details
    assert not posts.posted_to_anthropic and not posts.posted_to_openrouter, posts.urls


# ============================================================================================
# 4. Non-owner paid request denied.
# ============================================================================================
def test_case4_non_owner_paid_pin_denied_no_substitution(anthropic_lane, free_openrouter_lane, posts) -> None:
    _set_policy(mode="auto", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    decision = _run_turn(
        source_context={"_owner_local": False, "requested_model": anthropic_lane.provider_id, "model_selection": "pin"},
        allow_paid_fallback=True,
    )
    assert decision.used_model is False and decision.source == "selected_model_blocked"
    assert decision.details.get("block_reason") == "not_owner_local", decision.details
    assert not posts.posted_to_anthropic and not posts.posted_to_openrouter, posts.urls


# ============================================================================================
# 5. Paid adapter fails after reservation succeeds: no substitution; reservation closes.
# ============================================================================================
def test_case5_paid_adapter_fails_after_reservation_releases_and_does_not_substitute(anthropic_lane, free_openrouter_lane, monkeypatch) -> None:
    from core.runtime_task_events import unregister_runtime_event_sink

    _set_policy(mode="off", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    urls: list[str] = []

    def _throw(url, *a, **k):
        urls.append(str(url))
        raise RuntimeError("scripted provider failure")

    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", _throw)
    stream_id, events = _capture_events()
    try:
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": anthropic_lane.provider_id,
                "model_selection": "pin",
                "runtime_event_stream_id": stream_id,
                "runtime_session_id": "case5",
            },
            allow_paid_fallback=False,
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert decision.used_model is False and decision.source == "selected_model_blocked"
    assert _paid_events(events) == ["paid_call.reserved", "paid_call.released"], _paid_events(events)
    assert not any(OPENROUTER_HOST in u for u in urls), f"no free substitute may run: {urls}"


# ============================================================================================
# 6. Selected pin excluded before invocation (identity gate): honest reason, no stranded reservation.
# ============================================================================================
def test_case6_excluded_before_invocation_releases_reservation(anthropic_lane, monkeypatch) -> None:
    from core.runtime_task_events import unregister_runtime_event_sink

    _set_policy(mode="off", daily_cap=25)
    # A real, spendable pin whose turn carries no turn_id: the A9 plan-mint gate excludes it before
    # any adapter runs. The reservation built just before the gate must be RELEASED, not stranded.
    stream_id, events = _capture_events()
    try:
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": anthropic_lane.provider_id,
                "model_selection": "pin",
                "turn_id": "",  # defeat the rig default: hit the identity gate on purpose
                "runtime_event_stream_id": stream_id,
                "runtime_session_id": "case6",
            },
            allow_paid_fallback=False,
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert decision.used_model is False and decision.source == "routing_identity_missing"
    assert _paid_events(events) == ["paid_call.reserved", "paid_call.released"], _paid_events(events)


# ============================================================================================
# 7. Explicit FREE-model pin fails: no different free model silently replaces it.
# ============================================================================================
def test_case7_explicit_free_pin_failure_is_not_replaced_by_the_boost(free_openrouter_lane) -> None:
    from unittest import mock

    from core.memory_first_router import MemoryFirstRouter

    _set_policy(mode="off", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    seen: list[dict] = []
    with mock.patch.object(MemoryFirstRouter, "_try_free_cloud_boost", side_effect=lambda **k: seen.append(1)), mock.patch.object(
        MemoryFirstRouter, "_invoke_manifest", return_value=(None, None, "forced-stop")
    ):
        decision = _run_turn(
            source_context={"_owner_local": True, "requested_model": free_openrouter_lane.provider_id, "model_selection": "pin"},
            allow_paid_fallback=False,
        )
    assert seen == [], "an explicit free pin that failed must not fall to a different free model"
    assert decision.used_model is False and decision.source == "selected_model_blocked"


# ============================================================================================
# 8. Unknown explicit pin fails visibly (terminal model_unavailable, no substitution).
# ============================================================================================
def test_case8_unknown_explicit_pin_fails_visibly(free_openrouter_lane, posts) -> None:
    _set_policy(mode="off", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": "vendor/does-not-exist:free", "model_selection": "pin"},
        allow_paid_fallback=False,
    )
    assert decision.used_model is False and decision.source == "model_unavailable"
    assert not posts.posted_to_openrouter, posts.urls


# ============================================================================================
# 9. Auto still uses the available free fallback (the control that must stay green under sabotage).
# ============================================================================================
def test_case9_auto_still_uses_the_free_boost(free_openrouter_lane, posts) -> None:
    from core.cloud_provider_contract import CloudTaskRequirements, PrivacyClass

    from unittest import mock

    from core.memory_first_router import MemoryFirstRouter

    _set_policy(mode="auto", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    # The boundary under test is "does the router CONSULT the free-cloud boost for Auto" -- the boost
    # is spied (not mocked away from the routing decision): it is reached, so Auto keeps its fallback.
    consulted: list[int] = []
    with mock.patch.object(MemoryFirstRouter, "_try_free_cloud_boost", side_effect=lambda **k: consulted.append(1)):
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "cloud_task_requirements": CloudTaskRequirements(
                    min_context_tokens=10, expected_output_tokens=100, required_capabilities=("text",), privacy_class=PrivacyClass.PUBLIC
                ),
            },
            allow_paid_fallback=False,
            rank_override=[],
        )
    assert consulted == [1], "Auto must still consult the free-cloud boost"
    assert decision.source != "selected_model_blocked", decision.source


# ============================================================================================
# 10. Sticky Auto remains a preference (falls back like Auto, not held to the pin invariant).
# ============================================================================================
def test_case10_sticky_is_a_preference_and_still_reaches_the_boost(free_openrouter_lane) -> None:
    from unittest import mock

    from core.cloud_provider_contract import CloudTaskRequirements, PrivacyClass
    from core.memory_first_router import MemoryFirstRouter

    _set_policy(mode="auto", daily_cap=25, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    consulted: list[int] = []
    with mock.patch.object(MemoryFirstRouter, "_try_free_cloud_boost", side_effect=lambda **k: consulted.append(1)):
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": free_openrouter_lane.provider_id,
                "model_selection": "sticky",
                "cloud_task_requirements": CloudTaskRequirements(
                    min_context_tokens=10, expected_output_tokens=100, required_capabilities=("text",), privacy_class=PrivacyClass.PUBLIC
                ),
            },
            allow_paid_fallback=False,
            rank_override=[],
        )
    # A sticky preference keeps Auto's fallback: the boost IS consulted, it is NOT blocked as a pin.
    assert consulted == [1], "a sticky preference must keep Auto's free-cloud fallback"
    assert decision.source != "selected_model_blocked", decision.source


# ============================================================================================
# 11. Composer selection and persisted selection both preserve the invariant.
# ============================================================================================
@pytest.mark.parametrize("selection", ["pin", ""])  # "" = persisted default (ingress reads "pin")
def test_case11_composer_and_persisted_pin_both_preserve_the_invariant(anthropic_lane, free_openrouter_lane, posts, selection, monkeypatch) -> None:
    _set_policy(mode="off", daily_cap=0, free_cloud_enabled=True, auto_free_model="vendor/free-chat:free")
    from core.model_spend_ledger import SpendLimits
    monkeypatch.setattr("core.paid_call_reservation.spend_limits", lambda: SpendLimits(
        per_call_usd=0.25, per_task_usd=1, daily_usd=0, monthly_usd=25,
    ))
    ctx = {"_owner_local": True, "requested_model": anthropic_lane.provider_id}
    if selection:
        ctx["model_selection"] = selection
    decision = _run_turn(source_context=ctx, allow_paid_fallback=False)
    assert decision.used_model is False and decision.source == "selected_model_blocked"
    assert not posts.posted_to_openrouter, posts.urls


# ============================================================================================
# 12. User-facing failure names the requested model and the actual cause, without secrets.
# ============================================================================================
def test_case12_user_message_names_model_and_cause_without_secrets(anthropic_lane, monkeypatch) -> None:
    from core.agent_runtime import memory_runtime

    _set_policy(mode="off", daily_cap=0)
    from core.model_spend_ledger import SpendLimits
    monkeypatch.setattr("core.paid_call_reservation.spend_limits", lambda: SpendLimits(
        per_call_usd=0.25, per_task_usd=1, daily_usd=0, monthly_usd=25,
    ))
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id, "model_selection": "pin"},
        allow_paid_fallback=False,
    )
    assert decision.source == "selected_model_blocked"
    fake_agent = SimpleNamespace(_live_info_mode=lambda *a, **k: "")
    text = memory_runtime.chat_surface_honest_degraded_response(fake_agent, decision, user_input="estimate a used car price")
    assert anthropic_lane.provider_id in text, text
    assert "spend cap" in text, text
    assert "different model" in text, text
    assert DUMMY_KEY not in text and "sk-ant" not in text, "the failure text must never expose a key"


# ============================================================================================
# CORRECTION-PASS counterexamples (supervisor P0-B review): each FAILED before the correction.
# ============================================================================================

# CX-1. An autopilot rejection returns before the invoke loop; a granted reservation must be
# released there, not left standing against the caps.
def test_cx1_autopilot_rejection_releases_the_paid_reservation(anthropic_lane, posts, monkeypatch) -> None:
    from core.runtime_task_events import unregister_runtime_event_sink

    _set_policy(mode="off", daily_cap=25)
    # Force the autopilot heavy-lane block deterministically, with no ranked heavy manifest to clear
    # it -- the real early-return path that sits AFTER the reservation is built and before any adapter.
    monkeypatch.setattr("core.memory_first_router._autopilot_block_reason", lambda plan: "explicit_heavy_lane_unavailable")
    monkeypatch.setattr("core.memory_first_router._ranked_heavy_manifest", lambda ranked: None)
    stream_id, events = _capture_events()
    try:
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": anthropic_lane.provider_id,
                "model_selection": "pin",
                "runtime_event_stream_id": stream_id,
                "runtime_session_id": "cx1",
            },
            allow_paid_fallback=False,
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert decision.used_model is False and decision.source == "autopilot_blocked", decision.source
    assert _paid_events(events) == ["paid_call.reserved", "paid_call.released"], _paid_events(events)


# CX-2. A reservation the invoke loop already terminalized (released as call_failed, recording an
# unpriced attempt) must NOT be re-released by the pin terminal: doing so runs forget_paid_attempts
# and erases the accounting for a paid call that reached the provider.
def test_cx2_terminal_does_not_erase_a_dispatched_attempt_record(anthropic_lane) -> None:
    import core.paid_call_reservation as pcr
    from core.memory_first_router import _release_undispatched_pin_reservation

    _set_policy(mode="off", daily_cap=25)
    ctx = {"_owner_local": True, "task_id": "task-1", "turn_id": "cx2", "session_id": "cx2"}
    auth = pcr.reserve_owner_pick_paid_call(manifest=anthropic_lane, task=_task(), source_context=ctx)
    assert auth is not None, "the paid pick must reserve"
    mcid = str(getattr(auth, "model_call_id", ""))
    # The invoke loop's own lifecycle for a dispatched failure: record the unpriced attempt and
    # release as call_failed (this preserves the attempt for a retry-settle).
    pcr.note_unbilled_paid_attempt(auth)
    pcr.release_owner_pick_paid_call(auth, reason="call_failed", source_context=ctx)
    assert pcr._ATTEMPTS.get(mcid, {}).get("unpriced_attempts", 0) >= 1, "invoke path must record the attempt"
    # The guarded terminal cleanup must see the reservation already terminalized and do NOTHING --
    # it must not forget the attempt record.
    _release_undispatched_pin_reservation(auth, source_context=ctx)
    assert pcr._ATTEMPTS.get(mcid, {}).get("unpriced_attempts", 0) >= 1, "terminal must not erase the attempt accounting"


# CX-3. A gate rejection where the model NEVER ran must not be shown as "did not return a usable
# response" (which implies it ran). The internal-call policy rejection is the reported case.
def test_cx3_internal_call_rejection_message_does_not_imply_the_model_ran(anthropic_lane) -> None:
    """Repaired law (2026-09-18): an internal tool-selection call under an explicit paid pin is
    not the selected model failing. The pick never applied to the turn's internal classification
    calls, so the decision must not blame it, and the honest degraded surface must not imply the
    selected model was asked and could not run. In this sealed environment no ordinary-lane
    provider serves the internal step either, so the truthful source is no_provider_available."""
    from core.agent_runtime import memory_runtime

    _set_policy(mode="off", daily_cap=25)
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id, "model_selection": "pin"},
        allow_paid_fallback=False,
        task_kind="tool_intent",
    )
    assert decision.source != "selected_model_blocked", decision.source
    assert decision.details.get("block_reason") != "internal_tool_intent_call", decision.details
    assert decision.details.get("model_was_attempted") is not True, decision.details
    fake_agent = SimpleNamespace(_live_info_mode=lambda *a, **k: "")
    text = memory_runtime.chat_surface_honest_degraded_response(fake_agent, decision, user_input="do a thing")
    assert "did not return a usable response" not in text, text
    # The pick is not named as having been asked and failed -- it never was, for this step.
    assert f"{anthropic_lane.provider_id} could not run" not in text, text
    assert "could not run" not in text, text
