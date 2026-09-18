"""The server-side paid-call reservation — the six invariants that bound it.

Background: ``core/memory_first_router.py`` refuses every paid-cloud manifest unless
``source_context`` carries an ``authorized_paid_call``. That key is stripped from every inbound
body (RESERVED_TRUST_KEYS) and nothing in production built one, so an owner who added a key and
picked ``claude-*`` never reached the provider — the turn fell back to local.
``core/paid_call_reservation.py`` builds that reservation server-side; this file pins what it may
and may not authorize.

NO REAL CALL IS MADE ANYWHERE HERE. The key is a dummy string and ``requests.post`` inside the
adapter is replaced with a recorder, so "did this turn POST to api.anthropic.com" is measured
rather than asserted about.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

DUMMY_KEY = "sk-ant-DUMMY-NOT-A-REAL-KEY"
ANTHROPIC_HOST = "https://api.anthropic.com"


# --------------------------------------------------------------------------------------------
# isolation: a private runtime home + db per test, so the escalation counter, the spend ledger,
# and the provider manifests never leak between tests (or out of the tmp dir).
# --------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import os_consent_gate, runtime_paths
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # The `ask` escalation branch calls the real OS consent prompt (Windows Hello). Stand in a
    # DENY so the suite never opens a live prompt, and so "ask mode did not spend" is measured
    # through the real gate rather than by skipping it.
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    yield
    os_consent_gate.set_consent_override_for_tests(None)
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


class _PostRecorder:
    """Stands in for ``requests.post`` inside the provider adapter. Records, never calls out."""

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


@pytest.fixture
def posts(monkeypatch) -> _PostRecorder:
    recorder = _PostRecorder()
    monkeypatch.setattr("adapters.openai_compatible_adapter.requests.post", recorder)
    return recorder


@pytest.fixture
def anthropic_lane(monkeypatch):
    """Register the real Anthropic BYOK manifest from a DUMMY key, exactly as the UI does."""
    from unittest import mock

    import core.runtime_provider_defaults as rpd

    monkeypatch.setenv("ANTHROPIC_API_KEY", DUMMY_KEY)
    with mock.patch("core.credential_store.has_credential", lambda slot: slot == "llm.cloud.anthropic"):
        provider_id = rpd.activate_provider_byok("anthropic", env={"ANTHROPIC_API_KEY": DUMMY_KEY})
    assert provider_id, "the Anthropic BYOK lane must register from a key"

    from storage.model_provider_manifest import list_provider_manifests

    manifest = next(m for m in list_provider_manifests(enabled_only=True) if m.provider_name == "anthropic-byok")
    return manifest


def _set_policy(**kw):
    from core import cloud_escalation_policy as cep

    return cep.save_policy(replace(cep.load_policy(), **kw))


def _task():
    return SimpleNamespace(task_id="task-1", task_summary="explain this repository in depth")


def _run_turn(*, source_context, allow_paid_fallback, posts, task_kind="reasoning"):
    """Drive the real routing path for one turn and return its decision."""
    from unittest import mock

    from core.memory_first_router import MemoryFirstRouter

    # The A9 plan-mint gate (core/turn_routing) fails a turn closed unless it carries a
    # server-stamped turn_id AND session_id (core/memory_first_router._routing_scope_identity).
    # These tests predate that gate and assert paid-pin / reservation / boost behaviour, not
    # identity, so supply a valid stamped identity where the test did not -- the gate still runs on
    # its real path and now passes, rather than being bypassed. A test that provides its own
    # turn_id / session_id / runtime_session_id keeps them (they win over these defaults).
    source_context = {
        "turn_id": "owner-pick-turn",
        "session_id": "owner-pick-session",
        **(source_context or {}),
    }

    interpretation = SimpleNamespace(reconstructed_text="explain this repository in depth")
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
    with mock.patch("core.memory_first_router._cached_free_vram_gb", return_value=None), mock.patch(
        "core.memory_first_router.should_probe_health", return_value=False
    ):
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
            source_context=source_context,
        )


# --------------------------------------------------------------------------------------------
# The defect itself: an owner-local explicit pick must actually execute.
# --------------------------------------------------------------------------------------------


def test_owner_local_explicit_pick_reaches_the_provider(anthropic_lane, posts) -> None:
    _set_policy(mode="off", daily_cap=25)
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )
    assert posts.posted_to_anthropic, f"the picked paid lane must execute; posted: {posts.urls}"
    assert decision.used_model and decision.output_text == "cloud answer"
    assert decision.model_name == anthropic_lane.model_name


def test_ordinary_paid_answer_emits_one_joinable_reservation_and_settlement_receipt(
    anthropic_lane,
    posts,
) -> None:
    from core.runtime_task_events import (
        new_runtime_event_stream_id,
        register_runtime_event_sink,
        unregister_runtime_event_sink,
    )

    _set_policy(mode="off", daily_cap=25)
    events: list[dict] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))
    try:
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": anthropic_lane.provider_id,
                "runtime_event_stream_id": stream_id,
                "runtime_session_id": "paid-answer-receipt-success",
            },
            allow_paid_fallback=False,
            posts=posts,
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert decision.used_model and posts.posted_to_anthropic
    paid = [
        event
        for event in events
        if event.get("event_type")
        in {"paid_call.reserved", "paid_call.settled", "paid_call.released"}
    ]
    assert [event["event_type"] for event in paid] == [
        "paid_call.reserved",
        "paid_call.settled",
    ]
    reserved, settled = paid
    assert reserved["call_role"] == settled["call_role"] == "answer_generation"
    assert reserved["model_call_id"] == settled["model_call_id"]
    assert reserved["reservation_id"] == settled["reservation_id"]
    assert reserved["model_id"] == settled["model_id"] == anthropic_lane.model_name
    assert reserved["provider_id"] == settled["provider_id"] == anthropic_lane.provider_id
    assert 0 < float(reserved["reserved_usd"]) <= float(reserved["per_call_cap_usd"])
    assert int(reserved["daily_call_count"]) >= 1
    assert reserved["daily_call_cap"] is None
    assert 0 <= float(settled["actual_usd"]) <= float(reserved["reserved_usd"])

    from core.model_orchestration_state import ModelOrchestrationState
    from storage.db import get_connection

    conn = get_connection()
    try:
        runs = conn.execute(
            "SELECT state FROM model_orchestration_runs WHERE task_id = 'task-1'"
        ).fetchall()
    finally:
        conn.close()
    assert [row["state"] for row in runs] == [ModelOrchestrationState.COMPLETED]

    model_events = [
        event
        for event in events
        if event.get("event_type") in {"model.call_started", "model.call_completed"}
    ]
    assert [event["event_type"] for event in model_events] == [
        "model.call_started",
        "model.call_completed",
    ]
    assert all(event["call_role"] == "answer_generation" for event in model_events)
    assert all(event["model_call_id"] == reserved["model_call_id"] for event in model_events)


def test_failed_ordinary_paid_answer_emits_one_release_and_never_substitutes(
    anthropic_lane,
    monkeypatch,
    posts,
) -> None:
    from core.runtime_task_events import (
        new_runtime_event_stream_id,
        register_runtime_event_sink,
        unregister_runtime_event_sink,
    )

    _set_policy(mode="off", daily_cap=25)
    events: list[dict] = []
    stream_id = new_runtime_event_stream_id()
    register_runtime_event_sink(stream_id, lambda event: events.append(dict(event)))
    monkeypatch.setattr(
        "adapters.openai_compatible_adapter.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("scripted provider failure")),
    )
    try:
        decision = _run_turn(
            source_context={
                "_owner_local": True,
                "requested_model": anthropic_lane.provider_id,
                "runtime_event_stream_id": stream_id,
                "runtime_session_id": "paid-answer-receipt-failure",
            },
            allow_paid_fallback=False,
            posts=posts,
        )
    finally:
        unregister_runtime_event_sink(stream_id)

    assert decision.used_model is False
    paid = [
        event
        for event in events
        if event.get("event_type")
        in {"paid_call.reserved", "paid_call.settled", "paid_call.released"}
    ]
    assert [event["event_type"] for event in paid] == [
        "paid_call.reserved",
        "paid_call.released",
    ]
    assert paid[0]["model_call_id"] == paid[1]["model_call_id"]
    assert paid[0]["reservation_id"] == paid[1]["reservation_id"]
    assert paid[1]["call_role"] == "answer_generation"
    completed = [event for event in events if event.get("event_type") == "model.call_completed"]
    assert completed == [], "an exact failed pin must not complete on a substitute model"

    from core.model_orchestration_state import ModelOrchestrationState
    from storage.db import get_connection

    conn = get_connection()
    try:
        runs = conn.execute(
            "SELECT state FROM model_orchestration_runs WHERE task_id = 'task-1'"
        ).fetchall()
    finally:
        conn.close()
    assert [row["state"] for row in runs] == [ModelOrchestrationState.FAILED_SAFE]


def test_a_paid_call_is_recorded_in_the_usage_meter(anthropic_lane, posts) -> None:
    from core import usage_meter

    _set_policy(mode="off", daily_cap=25)
    _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )
    assert posts.posted_to_anthropic
    summary = usage_meter.usage_summary(window_seconds=3600)
    paid = summary["paid_cloud"]
    assert paid["responses"] == 1, summary
    assert paid["prompt_tokens"] == 11 and paid["output_tokens"] == 7, summary
    assert summary["free_local"]["responses"] == 0, "the paid call must not be metered as free"


def test_a_granted_reservation_is_settled_not_left_standing(anthropic_lane, posts) -> None:
    """A settled call frees its reserved ceiling, so a second turn is not blocked by the first."""
    from storage.db import get_connection

    _set_policy(mode="off", daily_cap=25)
    _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )
    conn = get_connection()
    try:
        rows = conn.execute("SELECT status, reserved_usd FROM model_spend_reservations").fetchall()
    finally:
        conn.close()
    assert [r["status"] for r in rows] == ["settled"], rows
    assert [float(r["reserved_usd"]) for r in rows] == [0.0]


# --------------------------------------------------------------------------------------------
# INVARIANT 1 — auto fallback never spends.
# --------------------------------------------------------------------------------------------


def test_invariant_1_auto_fallback_never_reaches_a_paid_model(anthropic_lane, posts) -> None:
    """No explicit pick -> no reservation -> the paid lane is unreachable, under ANY policy."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    for mode in ("off", "ask", "auto"):
        _set_policy(mode=mode, daily_cap=25)
        _run_turn(
            source_context={"_owner_local": True},  # owner, but nothing was picked
            allow_paid_fallback=True,
            posts=posts,
        )
        assert not posts.posted_to_anthropic, f"auto fallback spent under mode={mode}: {posts.urls}"

    # And directly at the seam: no explicitly picked manifest means no reservation, ever.
    assert reserve_owner_pick_paid_call(
        manifest=None, task=_task(), source_context={"_owner_local": True}
    ) is None


def test_invariant_1_a_failed_explicit_pin_does_not_reach_the_free_cloud_boost(anthropic_lane, posts) -> None:
    """P0-B invariant supersedes the earlier expectation.

    The earlier version asserted the free-cloud boost WAS consulted once the picked lane failed (it
    then only checked the boost did not inherit the reservation). That consultation is exactly the
    silent free substitution P0-B bans: when the user explicitly pins model X, X runs or the turn
    says why X could not run -- no other model answers in its place. So for a failed EXPLICIT pin
    the boost must not be reached at all, and the decision must name the selected model. (Auto and
    sticky routing still reach the boost -- see test_auto_still_uses_the_free_boost in
    tests/test_explicit_pin_authority_p0.py, which is the control that must stay green.)
    """
    from unittest import mock

    from core.memory_first_router import MemoryFirstRouter

    seen: list[dict] = []

    def _capture(**kwargs):
        seen.append(dict(kwargs.get("source_context") or {}))
        return None

    _set_policy(mode="auto", daily_cap=25)
    with mock.patch.object(MemoryFirstRouter, "_try_free_cloud_boost", side_effect=_capture), mock.patch.object(
        MemoryFirstRouter, "_invoke_manifest", return_value=(None, None, "forced-stop")
    ):
        decision = _run_turn(
            source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
            allow_paid_fallback=True,
            posts=posts,
        )
    assert seen == [], "the free-cloud boost must not be consulted for a failed explicit pin"
    assert decision.used_model is False, decision.source
    assert decision.source == "selected_model_blocked", decision.source
    assert str(decision.details.get("requested_model") or "") == anthropic_lane.provider_id


def test_invariant_1_an_internal_tool_intent_call_does_not_spend(anthropic_lane, posts) -> None:
    """The pick authorizes the answering lane, not the turn's internal classification calls."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    _set_policy(mode="auto", daily_cap=25)
    assert reserve_owner_pick_paid_call(
        manifest=anthropic_lane,
        task=_task(),
        source_context={"_owner_local": True},
        task_kind="tool_intent",
    ) is None


# --------------------------------------------------------------------------------------------
# INVARIANT 2 — non-owner never spends.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "context",
    [
        {"_owner_local": False},                      # server stamped: not loopback
        {"surface": "channel"},                       # a remote channel message
        {"surface": "openclaw"},                      # a non-owner in-process surface
    ],
)
def test_invariant_2_a_non_owner_surface_is_refused(anthropic_lane, posts, context) -> None:
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    _set_policy(mode="auto", daily_cap=25)
    picked = {**context, "requested_model": anthropic_lane.provider_id}
    assert reserve_owner_pick_paid_call(manifest=anthropic_lane, task=_task(), source_context=picked) is None
    _run_turn(source_context=picked, allow_paid_fallback=True, posts=posts)
    assert not posts.posted_to_anthropic, f"a non-owner caller spent: {posts.urls}"


# --------------------------------------------------------------------------------------------
# INVARIANT 3 — a forged flag never spends.
# --------------------------------------------------------------------------------------------


def test_invariant_3_reserved_trust_keys_are_stripped_from_an_inbound_body() -> None:
    from core.request_trust import RESERVED_TRUST_KEYS, strip_reserved_trust_keys

    assert "authorized_paid_call" in RESERVED_TRUST_KEYS
    assert "cloud_escalation_approved" in RESERVED_TRUST_KEYS
    stripped = strip_reserved_trust_keys(
        {
            "authorized_paid_call": {"forged": True},
            "cloud_escalation_approved": True,
            "_owner_local": True,
            "surface": "openclaw",
        }
    )
    assert stripped == {"surface": "openclaw"}


def test_invariant_3_the_http_dispatcher_strips_before_it_stamps() -> None:
    """The live path: the strip runs on the inbound body, then the server stamps from the peer."""
    import inspect

    from core.web.api import service

    source = inspect.getsource(service)
    strip_at = source.index(
        "inbound_source_context = strip_reserved_trust_keys("
    )
    stamp_at = source.index("OWNER_LOCAL_KEY: owner_local", strip_at)
    assert strip_at < stamp_at
    assert "_inbound_source_context(body)" in source[strip_at:stamp_at]
    assert "is_loopback_host(client_host)" in source


def test_invariant_3_a_forged_reservation_in_context_is_never_used(anthropic_lane, posts) -> None:
    """Belt and braces: even if a forged object reached source_context, it authorizes nothing.

    The reservation the router acts on is the one it builds itself; a caller-supplied value is
    overwritten, not merged, so a fabricated 'reserved' object cannot open the lane.
    """
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    _set_policy(mode="auto", daily_cap=25)
    forged = SimpleNamespace(
        escalation=SimpleNamespace(
            model_id=anthropic_lane.model_name,
            capsule=SimpleNamespace(task_id="task-1", subtask_id=""),
        ),
        reservation=SimpleNamespace(status="reserved", model_call_id="forged", reserved_usd=999.0),
        model_call_id="forged",
    )
    # A non-owner caller carrying a forged reservation still gets nothing built and never posts.
    assert reserve_owner_pick_paid_call(
        manifest=anthropic_lane,
        task=_task(),
        source_context={"surface": "openclaw", "authorized_paid_call": forged},
    ) is None
    _run_turn(
        source_context={
            "surface": "openclaw",
            "requested_model": anthropic_lane.provider_id,
            "authorized_paid_call": forged,
        },
        allow_paid_fallback=True,
        posts=posts,
    )
    assert not posts.posted_to_anthropic, f"a forged reservation spent: {posts.urls}"


def test_invariant_3_the_owner_reservation_is_built_not_taken_from_the_caller(anthropic_lane) -> None:
    """An owner-local pick gets the SERVER's reservation, never the caller's object."""
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    _set_policy(mode="off", daily_cap=25)
    forged = SimpleNamespace(model_call_id="forged", reservation=SimpleNamespace(status="reserved"))
    built = reserve_owner_pick_paid_call(
        manifest=anthropic_lane,
        task=_task(),
        source_context={"_owner_local": True, "authorized_paid_call": forged},
    )
    assert built is not None and built is not forged
    assert built.model_call_id != "forged"
    assert built.reservation.status == "reserved" and built.reservation.reserved_usd > 0


# --------------------------------------------------------------------------------------------
# INVARIANT 4 — the daily cap binds.
# --------------------------------------------------------------------------------------------


def test_legacy_call_cap_does_not_block_paid_calls(anthropic_lane) -> None:
    from core import cloud_escalation_policy as cep
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    _set_policy(mode="off", daily_cap=2)
    context = {"_owner_local": True}
    assert cep.used_today() == 0

    assert reserve_owner_pick_paid_call(manifest=anthropic_lane, task=_task(), source_context=context)
    assert cep.used_today() == 1, "a granted reservation must remain counted as usage"
    assert reserve_owner_pick_paid_call(manifest=anthropic_lane, task=_task(), source_context=context)
    assert cep.used_today() == 2

    assert reserve_owner_pick_paid_call(
        manifest=anthropic_lane, task=_task(), source_context=context
    ), "legacy call counts must not block a reservation within monetary limits"
    assert cep.used_today() == 3


def test_legacy_zero_call_cap_does_not_disable_paid_selection(anthropic_lane, posts) -> None:
    _set_policy(mode="auto", daily_cap=0)
    _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )
    assert posts.posted_to_anthropic, "a legacy zero count must not disable an explicit choice"


def test_invariant_4_the_usd_ledger_cap_also_refuses(anthropic_lane, monkeypatch) -> None:
    """The call-count cap is not the only bound: the spend ledger's USD caps refuse too."""
    from core import paid_call_reservation
    from core.model_spend_ledger import SpendLimits

    _set_policy(mode="off", daily_cap=25)
    monkeypatch.setattr(
        paid_call_reservation,
        "spend_limits",
        lambda: SpendLimits(per_call_usd=0.25, per_task_usd=0.25, daily_usd=5.0, monthly_usd=25.0),
    )
    context = {"_owner_local": True}
    assert paid_call_reservation.reserve_owner_pick_paid_call(
        manifest=anthropic_lane, task=_task(), source_context=context
    ) is not None
    # The second reservation for the same task would exceed per_task_usd -> refused, not raised.
    assert paid_call_reservation.reserve_owner_pick_paid_call(
        manifest=anthropic_lane, task=_task(), source_context=context
    ) is None


# --------------------------------------------------------------------------------------------
# INVARIANT 5 — policy off means off for auto-escalation.
# --------------------------------------------------------------------------------------------


def test_invariant_5_policy_off_blocks_auto_but_not_an_explicit_pick(anthropic_lane, posts) -> None:
    _set_policy(mode="off", daily_cap=25)

    # Ambient auto-bursting: refused.
    _run_turn(source_context={"_owner_local": True}, allow_paid_fallback=True, posts=posts)
    assert not posts.posted_to_anthropic, f"policy off must block the auto burst: {posts.urls}"

    # The deliberate act: honored.
    _run_turn(
        source_context={"_owner_local": True, "requested_model": anthropic_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )
    assert posts.posted_to_anthropic, "an explicit owner pick is the act that authorizes"


def test_invariant_5_the_auto_gate_still_refuses_a_non_owner(anthropic_lane) -> None:
    """The pre-existing escalation gate is unchanged: auto authorizes only owner-local."""
    from unittest import mock

    from core import cloud_escalation_policy
    from core.memory_first_router import _gate_paid_by_cloud_escalation

    with mock.patch.object(
        cloud_escalation_policy,
        "decide_escalation",
        return_value=SimpleNamespace(action=cloud_escalation_policy.ACTION_CLOUD),
    ):
        assert _gate_paid_by_cloud_escalation(
            resolved_allow_paid=True, allow_paid_fallback=True,
            requested_paid_cloud=False, source_context={"_owner_local": True},
        ) == (True, True)
        assert _gate_paid_by_cloud_escalation(
            resolved_allow_paid=True, allow_paid_fallback=True,
            requested_paid_cloud=False, source_context={"surface": "openclaw"},
        ) == (False, False)


# --------------------------------------------------------------------------------------------
# INVARIANT 6 — the free lane is unaffected and does not consume the paid cap.
# --------------------------------------------------------------------------------------------


@pytest.fixture
def free_openrouter_lane(monkeypatch):
    """A catalog-verified-free OpenRouter manifest, priced from the real catalog reader."""
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


def test_invariant_6_a_verified_free_model_needs_no_reservation(free_openrouter_lane) -> None:
    from core import cloud_escalation_policy as cep
    from core.model_selection_policy import is_verified_free_cloud_manifest
    from core.paid_call_reservation import reserve_owner_pick_paid_call

    _set_policy(mode="off", daily_cap=25)
    assert is_verified_free_cloud_manifest(free_openrouter_lane), "fixture must be catalog-verified free"
    assert reserve_owner_pick_paid_call(
        manifest=free_openrouter_lane,
        task=_task(),
        source_context={"_owner_local": True, "requested_model": free_openrouter_lane.provider_id},
    ) is None, "a free model must not take a paid reservation"
    assert cep.used_today() == 0, "the free lane must not consume the paid daily cap"


def test_invariant_6_the_free_lane_still_routes_without_a_reservation(free_openrouter_lane, posts) -> None:
    from core import cloud_escalation_policy as cep

    _set_policy(mode="off", daily_cap=25)
    decision = _run_turn(
        source_context={"_owner_local": True, "requested_model": free_openrouter_lane.provider_id},
        allow_paid_fallback=False,
        posts=posts,
    )
    assert any("openrouter.ai" in url for url in posts.urls), f"the free lane must still route: {posts.urls}"
    assert decision.model_name == "vendor/free-chat:free"
    assert cep.used_today() == 0, "the free lane must not consume the paid daily cap"

    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_spend_reservations'"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [], "the free lane must create no spend reservation at all"


def test_invariant_6_a_paid_lookalike_is_still_refused(monkeypatch, posts) -> None:
    """Renaming does not make a model free: a `:free` model absent from the catalog stays paid."""
    from core import openrouter_catalog
    from core.model_selection_policy import is_verified_free_cloud_manifest
    from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

    monkeypatch.setattr(openrouter_catalog, "safe_all_models", lambda **kw: ((), 0.0))

    paid_lookalike = ModelProviderManifest(
        provider_name="openrouter-byok",
        model_name="vendor/not-in-catalog:free",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="User-managed",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={"base_url": "https://openrouter.ai/api/v1", "api_path": "/chat/completions"},
        metadata={"cost_class": "paid_cloud"},
    )
    upsert_provider_manifest(paid_lookalike)
    assert not is_verified_free_cloud_manifest(paid_lookalike)

    _set_policy(mode="auto", daily_cap=25)
    # A non-owner caller explicitly naming it, with auto policy and paid fallback on: still local.
    _run_turn(
        source_context={"surface": "openclaw", "requested_model": paid_lookalike.provider_id},
        allow_paid_fallback=True,
        posts=posts,
    )
    assert posts.urls == [], f"a paid lookalike must reach no cloud lane: {posts.urls}"
