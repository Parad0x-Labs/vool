"""D1 -- SWITCHBOARD-locked candidate-count-aware reserve policy.

ARGUS proved a candidate can consume almost the entire fallback budget when it hangs until its
own capped timeout, leaving nothing for later ranked candidates even though the between-attempts
deadline check (core.memory_first_router.py) is working exactly as designed -- the FIRST
candidate's own ceiling was, itself, (almost) the whole budget.

SWITCHBOARD's read-only policy decision (locked, not redesigned here): reserve
`_FALLBACK_ATTEMPT_FLOOR_SECONDS * n_after` seconds off the current candidate's own cap, where
`n_after` is the count of genuinely reachable ranked candidates still queued behind it (0 when
none remain, or when this candidate's own failure would trip `_planned_heavy_manifest_failed` and
end the loop outright regardless of what's ranked behind it -- no point reserving time for
candidates policy will refuse to try anyway). See `core.memory_first_router.
_fallback_reserve_seconds` for the exact rule and `_fallback_deadline`'s per-call cap site for
where it is applied.

Uses the same fake, fully-controllable monotonic clock as the prior characterization pass (no real
wall-clock waiting) patched into core.memory_first_router.time.monotonic, advanced only inside a
mocked candidate adapter call to simulate "this call took N seconds" without waiting N seconds.
Drives the real core.memory_first_router.MemoryFirstRouter._execute_provider_task end to end, so
the real resolve_fallback_budget_seconds call, the real _start_fallback_deadline arming, the real
_fallback_reserve_seconds computation, the real capped_timeout arithmetic, and the real
between-attempts deadline check all execute against the fake clock -- and the actual capped
`timeout_seconds` handed to each candidate's adapter is captured directly (via the manifest
`build_adapter` receives, which IS the reserve-adjusted copy `_invoke_manifest` passes through),
not re-derived by hand.

IMPORTANT EXISTING TEST NOTE: a prior version of this file asserted that a full-budget (60.0s) A
hang prevents B from running, as the then-current-policy baseline (see git history for
`test_hang_for_the_full_budget_prevents_candidate_b_from_running`). That assertion's literal
75-line scenario still holds under the NEW policy too if replayed unchanged, precisely because it
simulated a raw 60s wall-clock hang without regard to A's own (now smaller) cap -- the
between-attempts check is unchanged, so an actual 60s elapsed still trips it regardless of what
A's nominal ceiling was. What DOES change is A's own ceiling: under the new policy A can no longer
be capped to the full 60s when a usable successor exists at all (its cap is now
`60 - FLOOR*n_after`). `test_candidate_a_can_no_longer_claim_the_full_budget_when_a_successor_
exists` below is the deliberate inversion the operator asked for: it drives A to hang for exactly
its OWN NEW cap (55.0s in the two-candidate case, not the old 60.0s), and asserts B is now
attempted where the old policy would have left it capped at exactly the boundary that blocked B.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelResponse
from core.memory_first_router import (
    _FALLBACK_ATTEMPT_FLOOR_SECONDS,
    MemoryFirstRouter,
    _fallback_reserve_seconds,
    _planned_heavy_manifest_failed,
)
from core.model_health import reset_provider_health
from storage.db import get_connection
from storage.migrations import run_migrations
from storage.model_provider_manifest import ModelProviderManifest

FLOOR = _FALLBACK_ATTEMPT_FLOOR_SECONDS
BUDGET = 60.0


class _FakeClock:
    """A controllable stand-in for time.monotonic() -- advances only when told to, so a
    "provider call that takes N seconds" is simulated by advancing the clock by N inside the
    mocked adapter call, without the test actually waiting N real seconds."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start
        self.start = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def elapsed(self) -> float:
        return self.now - self.start


def _manifest(name: str, *, deployment_class: str = "local", model_name: str | None = None) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=f"fixture-{name}", model_name=model_name or f"candidate-{name}", source_type="http",
        adapter_type="local_qwen_provider", license_name="Apache-2.0",
        license_reference="https://example.invalid", weight_location="external",
        runtime_dependency="ollama", capabilities=["summarize", "format", "structured_json"],
        # timeout_seconds=180 matches core/runtime_provider_defaults.py's real production
        # default. Without an explicit, independent value here, `existing_timeout` in the cap
        # computation falls back to the SAME remaining-budget value being capped against, so the
        # `capped_timeout < existing_timeout` check never trips and the reserve-adjusted manifest
        # copy this test needs to observe is never actually produced.
        # Off the loopback address on purpose: the local tool-certification probe reaches only
        # loopback endpoints, and an uncertified loopback author is refused before any attempt
        # ("author_not_certified_for_final_answer", 2026-09-07). These fixtures are budget
        # scenarios, not local models; outside the probe's reach their operator configuration is
        # the attestation, exactly as for a remote lane.
        runtime_config={"base_url": "http://fixture.invalid:9", "timeout_seconds": 180},
        metadata={"deployment_class": deployment_class, "orchestration_role": "queen"},
    )


class _Scenario:
    """Drives MemoryFirstRouter._execute_provider_task against N candidates with per-candidate
    scripted delays (each candidate "hangs" for its own delay before failing, or succeeds
    instantly if delay is None), a fake clock, and a mocked autopilot plan. Records, per
    candidate: whether it was attempted, and the exact `timeout_seconds` cap the adapter actually
    received (read off the manifest `build_adapter` is called with -- the reserve-adjusted copy
    `_invoke_manifest` passes straight through, not re-derived by this test)."""

    def __init__(self, *, delays: list[float | None], budget_seconds: float = BUDGET, explicit_heavy: bool = False):
        self.clock = _FakeClock()
        self.manifests = [
            # Candidate A's model name must parse to >=24B when explicit_heavy=True, so
            # _planned_heavy_manifest_failed(plan, A) is actually reachable (model_parameter_
            # billions falls back to 8.0 for a name with no B-suffix, e.g. "candidate-a").
            _manifest("a", model_name="candidate-a-32b") if explicit_heavy and i == 0 else _manifest(chr(ord("a") + i))
            for i in range(len(delays))
        ]
        self.delays = delays
        self.budget_seconds = budget_seconds
        self.explicit_heavy = explicit_heavy
        self.attempted: list[str] = []
        self.captured_caps: dict[str, float | None] = {}
        self.decision = None

    def _adapter_for(self, index: int, *, cap: float | None) -> mock.Mock:
        adapter = mock.Mock()
        adapter.health_check.return_value = {"ok": True}
        adapter.get_license_metadata.return_value = {}
        delay = self.delays[index]
        # A real HTTP client honors ITS OWN configured timeout -- a "hang" that requests more
        # time than the cap actually allows gets cut off AT the cap, exactly like
        # adapters.openai_compatible_adapter.py's real requests.post(timeout=...) would. Without
        # this, a mocked "999s hang" would consume 999s of fake-clock time regardless of what cap
        # was computed for it, which is not what a real timeout-bound provider call does.
        effective_delay = None if delay is None else (delay if cap is None else min(delay, cap))

        def _side_effect(*args, **kwargs):
            self.attempted.append(self.manifests[index].provider_id)
            if effective_delay is None:
                return ModelResponse(
                    output_text=f"{self.manifests[index].model_name} answered.",
                    provider_id=self.manifests[index].provider_id,
                    model_name=self.manifests[index].model_name,
                    output_mode="plain_text",
                )
            self.clock.advance(effective_delay)
            raise RuntimeError("provider_http_500:internal server error")

        adapter.run_text_task.side_effect = _side_effect
        return adapter

    def run(self) -> None:
        run_migrations()
        reset_provider_health()
        conn = get_connection()
        try:
            for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        finally:
            conn.close()

        router = MemoryFirstRouter()
        # The A9 routing plan builds its fallback ladder from the REGISTRY inventory and fences the
        # ranking with it; candidates the registry never saw are dropped as "no_ranked_provider".
        # The fixtures therefore enter through the registry's real door, exactly as a real provider
        # does, instead of existing only in the patched ranker's return value.
        for manifest in self.manifests:
            router.registry.register_manifest(manifest)
        index_by_provider_id = {m.provider_id: i for i, m in enumerate(self.manifests)}

        def build_adapter(manifest: ModelProviderManifest) -> mock.Mock:
            # The manifest received here IS the reserve-adjusted copy _invoke_manifest passes
            # through when a cap applied -- capture its timeout_seconds directly, don't re-derive
            # it, and build the adapter's simulated delay from THIS real cap so a "hang" is bound
            # by it exactly like a real HTTP client would be.
            cap = (manifest.runtime_config or {}).get("timeout_seconds")
            self.captured_caps[manifest.provider_id] = cap
            index = index_by_provider_id.get(manifest.provider_id)
            if index is None:
                raise AssertionError(f"unexpected manifest {manifest.provider_id}")
            return self._adapter_for(index, cap=cap)

        fake_plan = SimpleNamespace(
            lane="daily",
            selected_provider_id=self.manifests[0].provider_id,
            to_dict=lambda: {
                "schema": "vool.local_inference_autopilot.v1", "lane": "daily",
                "selected_provider_id": self.manifests[0].provider_id, "selected_model": self.manifests[0].model_name,
                "verifier_required": False, "verifier_provider_id": None, "verifier_model": None,
                "explicit_heavy": self.explicit_heavy,
                "prefix_cache": {"backend": "none", "supported": False}, "warnings": [],
            },
        )

        with mock.patch(
            "core.memory_first_router.rank_provider_candidates", return_value=list(self.manifests)
        ), mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), mock.patch(
            "core.memory_first_router.build_local_inference_autopilot_plan", return_value=fake_plan
        ), mock.patch(
            "core.memory_first_router.resolve_fallback_budget_seconds", return_value=self.budget_seconds
        ), mock.patch("core.memory_first_router.time.monotonic", side_effect=self.clock.monotonic):
            self.decision = router._execute_provider_task(
                task=SimpleNamespace(task_id="budget-char-task", task_summary="hi"),
                classification={"task_class": "daily"},
                interpretation=SimpleNamespace(reconstructed_text="hi"),
                context_result=SimpleNamespace(
                    retrieval_confidence_score=0.2,
                    report=SimpleNamespace(
                        retrieval_confidence=0.2,
                        to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
                    ),
                ),
                persona=SimpleNamespace(),
                task_hash="budget-char-hash",
                task_kind="chat",
                output_mode="plain_text",
                allow_paid_fallback=False,
                provider_role="queen",
                surface="openclaw",
                # The A9 routing plan fails closed without a turn identity ("routing_identity_missing"):
                # the scenario carries the server-stamped turn and session keys the door publishes.
                source_context={
                    "surface": "openclaw",
                    "_canonical_user_turn_id": "budget-char-turn",
                    "session_id": "budget-char-session",
                    "runtime_session_id": "budget-char-session",
                },
            )

    def cap(self, index: int) -> float | None:
        return self.captured_caps.get(self.manifests[index].provider_id)

    def was_attempted(self, index: int) -> bool:
        return self.manifests[index].provider_id in self.attempted


# --- the pure reserve helper, directly ------------------------------------------------------


def test_reserve_helper_is_zero_with_no_successor() -> None:
    a = _manifest("a")
    reserve = _fallback_reserve_seconds(
        ranked_manifests=[a], skipped_provider_ids=set(), current_index=0,
        autopilot_plan={"explicit_heavy": False, "selected_provider_id": a.provider_id}, manifest=a,
    )
    assert reserve == 0.0


def test_reserve_helper_scales_with_untried_successors() -> None:
    a, b, c = _manifest("a"), _manifest("b"), _manifest("c")
    reserve = _fallback_reserve_seconds(
        ranked_manifests=[a, b, c], skipped_provider_ids=set(), current_index=0,
        autopilot_plan={"explicit_heavy": False, "selected_provider_id": a.provider_id}, manifest=a,
    )
    assert reserve == FLOOR * 2


def test_reserve_helper_ignores_already_skipped_successors() -> None:
    a, b, c = _manifest("a"), _manifest("b"), _manifest("c")
    reserve = _fallback_reserve_seconds(
        ranked_manifests=[a, b, c], skipped_provider_ids={b.provider_id}, current_index=0,
        autopilot_plan={"explicit_heavy": False, "selected_provider_id": a.provider_id}, manifest=a,
    )
    assert reserve == FLOOR * 1


def test_reserve_helper_is_zero_when_this_candidates_failure_ends_the_loop() -> None:
    a, b = _manifest("a"), _manifest("b")
    plan = {"explicit_heavy": True, "selected_provider_id": a.provider_id, "selected_model": "big-model-32b"}
    assert _planned_heavy_manifest_failed(plan, a) is True
    reserve = _fallback_reserve_seconds(
        ranked_manifests=[a, b], skipped_provider_ids=set(), current_index=0, autopilot_plan=plan, manifest=a,
    )
    assert reserve == 0.0


# --- 1: one candidate, hang -> full budget, no behavioral change ------------------------------


def test_one_candidate_hang_gets_the_full_budget() -> None:
    scenario = _Scenario(delays=[999.0])
    scenario.run()
    assert scenario.cap(0) == pytest.approx(BUDGET)
    assert scenario.decision.used_model is not True


# --- 2: two candidates, A hangs -> A capped to 55, B attempted with a 5s window ----------------


def test_two_candidates_a_hangs_b_gets_the_reserved_floor() -> None:
    scenario = _Scenario(delays=[999.0, None])
    scenario.run()
    assert scenario.cap(0) == pytest.approx(BUDGET - FLOOR)  # 55.0
    assert scenario.was_attempted(1) is True
    assert scenario.cap(1) == pytest.approx(FLOOR)  # 5.0
    assert scenario.decision.used_model is True


# --- 3: three candidates, A hangs -> tail windows preserved down the chain ---------------------


def test_three_candidates_a_and_b_hang_c_still_gets_a_window() -> None:
    scenario = _Scenario(delays=[999.0, 999.0, None])
    scenario.run()
    assert scenario.cap(0) == pytest.approx(BUDGET - 2 * FLOOR)  # 50.0
    assert scenario.was_attempted(1) is True
    assert scenario.cap(1) == pytest.approx(FLOOR)  # 5.0 (A used its full 50s cap; 10s remained, 1 successor -> reserve 5)
    assert scenario.was_attempted(2) is True
    assert scenario.cap(2) == pytest.approx(FLOOR)  # 5.0 remaining, 0 successors -> reserve 0, floored to 5
    assert scenario.decision.used_model is True


# --- 4: fast failure -> B is not permanently taxed by a reserve A never consumed --------------


def test_fast_failure_does_not_permanently_tax_b_by_as_reserve() -> None:
    scenario = _Scenario(delays=[0.001, None])
    scenario.run()
    # A's OWN cap was reduced by the reserve (55.0), but A only used 0.001s of it -- the reserve
    # was never actually "spent," so B must receive essentially the whole unused budget, not
    # 60 - FLOOR left over from A's cap ceiling.
    assert scenario.cap(0) == pytest.approx(BUDGET - FLOOR)
    assert scenario.was_attempted(1) is True
    assert scenario.cap(1) == pytest.approx(BUDGET - 0.001, abs=0.01)


# --- 5: half-budget failure -> B receives roughly the remaining half ---------------------------


def test_half_budget_failure_lets_b_receive_the_remaining_half() -> None:
    scenario = _Scenario(delays=[30.0, None])
    scenario.run()
    assert scenario.was_attempted(1) is True
    assert scenario.cap(1) == pytest.approx(BUDGET - 30.0, abs=0.01)  # ~30.0


# --- 6: explicit pin -> single candidate, full budget, no pin-specific heuristic needed --------


def test_explicit_pin_single_candidate_receives_full_budget() -> None:
    """Exercises the REAL pin-narrowing code path in MemoryFirstRouter._execute_provider_task
    (source_context["requested_model"] resolves against the real, DB-backed registry, narrowing
    ranked_manifests to exactly the pinned provider BEFORE this loop even starts) -- not merely a
    coincidentally-singular mocked ranking list. `n_after` falls out to 0 naturally; no
    pin-specific timeout heuristic is added or needed."""
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    pinned = router.registry.register_manifest(
        {
            "provider_name": "fixture-pinned", "model_name": "pinned-model", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://example.invalid", "weight_location": "external",
            "runtime_dependency": "ollama", "capabilities": ["summarize", "format", "structured_json"],
            "runtime_config": {"base_url": "http://fixture.invalid:9", "timeout_seconds": 180}, "enabled": True,  # off loopback: outside the certification probe's reach, see _manifest
            "metadata": {"deployment_class": "local", "orchestration_role": "queen"},
        }
    )
    other = router.registry.register_manifest(
        {
            "provider_name": "fixture-other", "model_name": "other-model", "source_type": "http",
            "adapter_type": "local_qwen_provider", "license_name": "Apache-2.0",
            "license_reference": "https://example.invalid", "weight_location": "external",
            "runtime_dependency": "ollama", "capabilities": ["summarize", "format", "structured_json"],
            "runtime_config": {"base_url": "http://fixture.invalid:9", "timeout_seconds": 180}, "enabled": True,  # off loopback: outside the certification probe's reach, see _manifest
            "metadata": {"deployment_class": "local", "orchestration_role": "queen"},
        }
    )
    clock = _FakeClock()
    captured_caps: dict[str, float | None] = {}
    adapter = mock.Mock()
    adapter.health_check.return_value = {"ok": True}
    adapter.get_license_metadata.return_value = {}

    def _side_effect(*args, **kwargs):
        clock.advance(999.0)
        raise RuntimeError("provider_http_500:internal server error")

    adapter.run_text_task.side_effect = _side_effect

    def build_adapter(manifest: ModelProviderManifest) -> mock.Mock:
        captured_caps[manifest.provider_id] = (manifest.runtime_config or {}).get("timeout_seconds")
        if manifest.provider_id != pinned.provider_id:
            raise AssertionError(f"explicit pin must never invoke a different manifest, got {manifest.provider_id}")
        return adapter

    fake_plan = SimpleNamespace(
        lane="daily",
        selected_provider_id=pinned.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1", "lane": "daily",
            "selected_provider_id": pinned.provider_id, "selected_model": pinned.model_name,
            "verifier_required": False, "verifier_provider_id": None, "verifier_model": None,
            "explicit_heavy": False, "prefix_cache": {"backend": "none", "supported": False}, "warnings": [],
        },
    )

    with mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=[pinned, other]
    ), mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), mock.patch(
        "core.memory_first_router.build_local_inference_autopilot_plan", return_value=fake_plan
    ), mock.patch(
        "core.memory_first_router.resolve_fallback_budget_seconds", return_value=BUDGET
    ), mock.patch("core.memory_first_router.time.monotonic", side_effect=clock.monotonic):
        router._execute_provider_task(
            task=SimpleNamespace(task_id="budget-char-pin-task", task_summary="hi"),
            classification={"task_class": "daily"},
            interpretation=SimpleNamespace(reconstructed_text="hi"),
            context_result=SimpleNamespace(
                retrieval_confidence_score=0.2,
                report=SimpleNamespace(
                    retrieval_confidence=0.2,
                    to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
                ),
            ),
            persona=SimpleNamespace(),
            task_hash="budget-char-pin-hash",
            task_kind="chat",
            output_mode="plain_text",
            allow_paid_fallback=False,
            provider_role="queen",
            surface="openclaw",
            source_context={"requested_model": pinned.model_name, "surface": "openclaw", "_canonical_user_turn_id": "budget-pin-turn", "session_id": "budget-pin-session", "runtime_session_id": "budget-pin-session"},
        )

    assert captured_caps.get(pinned.provider_id) == pytest.approx(BUDGET)
    assert other.provider_id not in captured_caps


# --- 7: explicit heavy -> A gets the full budget, B never attempted regardless -----------------


def test_explicit_heavy_candidate_a_gets_full_budget_b_never_attempted() -> None:
    scenario = _Scenario(delays=[999.0, None], explicit_heavy=True)
    scenario.run()
    # explicit_heavy=True + selected_provider_id == A -> _planned_heavy_manifest_failed(A) is
    # True, so reserve is 0 for A even though B exists in the ranked pool: no point shrinking A's
    # window for a candidate policy will refuse to use.
    assert scenario.cap(0) == pytest.approx(BUDGET)
    assert scenario.was_attempted(1) is False
    assert scenario.decision.source == "explicit_heavy_lane_failed"


# --- 8: total latency stays within budget + at most one floor tolerance -----------------------


def test_total_latency_with_every_candidate_hanging_stays_within_budget_plus_one_floor() -> None:
    scenario = _Scenario(delays=[999.0, 999.0, 999.0])
    scenario.run()
    elapsed = scenario.clock.elapsed()
    assert elapsed <= BUDGET + FLOOR, f"elapsed {elapsed}s exceeded budget+floor tolerance ({BUDGET + FLOOR}s)"
    assert scenario.was_attempted(0) is True
    assert scenario.was_attempted(1) is True
    assert scenario.was_attempted(2) is True


def test_deadline_boundary_at_exact_equality_blocks_the_next_candidate() -> None:
    """The between-attempts deadline check is `time.monotonic() >= _fallback_deadline` -- AT
    exact equality (zero raw budget remaining, not negative), the next candidate must NOT be
    attempted. The self-protecting reserve chain makes this exact boundary structurally hard to
    reach through ordinary per-candidate delays (a real reserve always leaves either strictly
    positive room for a genuine successor, or blocks it via a completely different code path --
    see the explicit-heavy tests), so this test drives candidate A's mocked call to jump the fake
    clock to PRECISELY the deadline value itself, isolating the boundary comparison from the cap
    arithmetic entirely."""
    run_migrations()
    reset_provider_health()
    conn = get_connection()
    try:
        for table in ("model_provider_manifests", "candidate_knowledge_lane", "local_tasks"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()

    router = MemoryFirstRouter()
    a, b = _manifest("a"), _manifest("b")
    clock = _FakeClock()
    b_attempted = {"value": False}

    adapter_a = mock.Mock()
    adapter_a.health_check.return_value = {"ok": True}
    adapter_a.get_license_metadata.return_value = {}

    def _a_side_effect(*args, **kwargs):
        clock.now = clock.start + BUDGET  # jump to EXACTLY the deadline, not past it
        raise RuntimeError("provider_http_500:internal server error")

    adapter_a.run_text_task.side_effect = _a_side_effect

    adapter_b = mock.Mock()
    adapter_b.health_check.return_value = {"ok": True}
    adapter_b.get_license_metadata.return_value = {}

    def _b_side_effect(*args, **kwargs):
        b_attempted["value"] = True
        return ModelResponse(output_text="B answered.", provider_id=b.provider_id, model_name=b.model_name, output_mode="plain_text")

    adapter_b.run_text_task.side_effect = _b_side_effect

    def build_adapter(manifest: ModelProviderManifest) -> mock.Mock:
        if manifest.provider_id == a.provider_id:
            return adapter_a
        if manifest.provider_id == b.provider_id:
            return adapter_b
        raise AssertionError(f"unexpected manifest {manifest.provider_id}")

    fake_plan = SimpleNamespace(
        lane="daily",
        selected_provider_id=a.provider_id,
        to_dict=lambda: {
            "schema": "vool.local_inference_autopilot.v1", "lane": "daily",
            "selected_provider_id": a.provider_id, "selected_model": a.model_name,
            "verifier_required": False, "verifier_provider_id": None, "verifier_model": None,
            "explicit_heavy": False, "prefix_cache": {"backend": "none", "supported": False}, "warnings": [],
        },
    )

    with mock.patch(
        "core.memory_first_router.rank_provider_candidates", return_value=[a, b]
    ), mock.patch.object(router.registry, "build_adapter", side_effect=build_adapter), mock.patch(
        "core.memory_first_router.build_local_inference_autopilot_plan", return_value=fake_plan
    ), mock.patch(
        "core.memory_first_router.resolve_fallback_budget_seconds", return_value=BUDGET
    ), mock.patch("core.memory_first_router.time.monotonic", side_effect=clock.monotonic):
        router._execute_provider_task(
            task=SimpleNamespace(task_id="budget-char-boundary-task", task_summary="hi"),
            classification={"task_class": "daily"},
            interpretation=SimpleNamespace(reconstructed_text="hi"),
            context_result=SimpleNamespace(
                retrieval_confidence_score=0.2,
                report=SimpleNamespace(
                    retrieval_confidence=0.2,
                    to_dict=lambda: {"retrieval_confidence": 0.2, "external_evidence_attachments": []},
                ),
            ),
            persona=SimpleNamespace(),
            task_hash="budget-char-boundary-hash",
            task_kind="chat",
            output_mode="plain_text",
            allow_paid_fallback=False,
            provider_role="queen",
            surface="openclaw",
            source_context={"surface": "openclaw"},
        )

    assert b_attempted["value"] is False, "B must not be attempted once raw remaining budget hits exactly zero"


# --- deliberate inversion of the pre-policy characterization baseline -------------------------


def test_candidate_a_can_no_longer_claim_the_full_budget_when_a_successor_exists() -> None:
    """Deliberate update of the pre-policy characterization (see module docstring): under the
    OLD policy, A's own cap was the full 60.0s budget regardless of a healthy successor waiting
    behind it. Under the LOCKED policy, A's cap is now bounded BELOW the full budget the moment a
    genuinely reachable successor exists -- proven directly on the captured cap, not merely on
    whether B eventually runs (test_two_candidates_a_hangs_b_gets_the_reserved_floor already
    covers that outcome)."""
    scenario = _Scenario(delays=[999.0, None])
    scenario.run()
    assert scenario.cap(0) < BUDGET
    assert scenario.cap(0) == pytest.approx(BUDGET - FLOOR)


def test_tight_budget_still_floors_a_middle_candidates_window() -> None:
    """The floor must be applied AFTER subtracting the reserve, not before -- otherwise a
    candidate reached when genuinely little raw budget remains (below FLOOR itself, before its
    own reserve is even subtracted) could be capped to zero or negative instead of the floor.

    Under ordinary budgets this ordering is rarely observable: the reserve math is self-
    protective, so a well-behaved chain never lets a later candidate's raw remaining budget dip
    below FLOOR while it still owes a reserve to a successor. A deliberately TIGHT budget
    (6.0s, smaller than the floor-scaled reserve two successors would want) forces it: A's own
    cap is floored hard to FLOOR (5.0s, since the "ideal" reserve-adjusted budget for A would be
    negative), consuming that on its own hang leaves B only 1.0s of genuinely raw remaining
    budget -- below FLOOR -- while B still owes a reserve to C. Correct (floor-after): B's cap is
    still floored to FLOOR (5.0s). Wrong (floor-before): B's cap would be
    max(FLOOR, 1.0) - FLOOR == 0.0."""
    scenario = _Scenario(delays=[999.0, 999.0, None], budget_seconds=6.0)
    scenario.run()
    assert scenario.cap(0) == pytest.approx(FLOOR)  # A: ideal would be negative, floored hard to 5.0
    assert scenario.cap(1) == pytest.approx(FLOOR)  # B: raw remaining (1.0) is below FLOOR -- must still floor to 5.0, not go to 0.0
    assert scenario.was_attempted(1) is True
