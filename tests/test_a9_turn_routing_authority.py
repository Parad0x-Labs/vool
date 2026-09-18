"""A9 P0 — ONE turn-scoped routing, context and retry authority (typed-contract pack).

The authority under test is `core.turn_routing`: one typed `TurnRoutingPlan` minted
BEFORE model spend, owned by the canonical turn, carrying the requested and selected
provider/model, locality/privacy ceiling, allowed providers/models, free/paid class,
cost ceiling, explicit pin, fallback ladder, retry policy, context identity and reason.

RED at base: the module does not exist yet, so every test that imports it fails at
collection-of-first-use. The contract below is the specification the implementation
must satisfy exactly -- no test here reaches for a network socket; the served-HTTP
proofs live in `test_a9_turn_routing_served.py`.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from storage.model_provider_manifest import ModelProviderManifest


def _pid(provider_name: str, model_name: str) -> str:
    """The composite provider id manifests actually carry (``provider_name:model_name``)."""
    return f"{provider_name}:{model_name}"


def _manifest(
    provider_name: str,
    model_name: str,
    *,
    base_url: str = "http://127.0.0.1:11434",
    cost_class: str = "free_local",
    enabled: bool = True,
) -> ModelProviderManifest:
    metadata = {
        "runtime_family": "ollama",
        "model_digest": "sha256:stub",
        "chat_template_hash": "tmpl",
        "quantization": "q4_K_M",
        "parameter_billions": 8.0,
    }
    if cost_class:
        metadata["cost_class"] = cost_class
    return ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://example.invalid/license",
        weight_location="external",
        runtime_dependency="stub",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={"base_url": base_url, "timeout_seconds": 30},
        metadata=metadata,
        enabled=enabled,
    )


class _RulesHomeTestCase(unittest.TestCase):
    """A private VOOL_HOME so temporary-rule persistence tests never touch real state."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        import os

        self._old_home = os.environ.get("VOOL_HOME")
        os.environ["VOOL_HOME"] = str(self.home)

    def tearDown(self) -> None:
        import os

        if self._old_home is None:
            os.environ.pop("VOOL_HOME", None)
        else:
            os.environ["VOOL_HOME"] = self._old_home
        self._tmp.cleanup()


class TestPlanIdentityFailsClosed(unittest.TestCase):
    """Missing/ambiguous routing identity must refuse BEFORE any spend is possible."""

    def test_missing_turn_id_refuses(self) -> None:
        from core.turn_routing import RoutingIdentityError, mint_turn_routing_plan

        with self.assertRaises(RoutingIdentityError):
            mint_turn_routing_plan(
                turn_id="",
                session_id="sess-1",
                manifests=[_manifest("local-a", "m1")],
            )

    def test_missing_session_id_refuses(self) -> None:
        from core.turn_routing import RoutingIdentityError, mint_turn_routing_plan

        with self.assertRaises(RoutingIdentityError):
            mint_turn_routing_plan(
                turn_id="turn-1",
                session_id="",
                manifests=[_manifest("local-a", "m1")],
            )

    def test_blank_identity_is_never_normalized_to_something(self) -> None:
        from core.turn_routing import RoutingIdentityError, mint_turn_routing_plan

        with self.assertRaises(RoutingIdentityError):
            mint_turn_routing_plan(
                turn_id="   ",
                session_id="sess-1",
                manifests=[_manifest("local-a", "m1")],
            )


class TestOneEligibilityForRankerAndBroker(unittest.TestCase):
    """Ranker and broker must consume the SAME eligibility decision."""

    def test_plan_carries_eligibility_for_every_manifest(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        manifests = [
            _manifest("local-a", "m1"),
            _manifest("local-b", "m2"),
            _manifest("cloud-paid", "big", cost_class="paid_cloud"),
        ]
        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=manifests,
        )
        self.assertEqual(len(plan.eligibility), 3)
        by_provider = {row.provider_id: row for row in plan.eligibility}
        self.assertTrue(by_provider[_pid("local-a", "m1")].allowed)
        self.assertTrue(by_provider[_pid("local-b", "m2")].allowed)
        self.assertFalse(by_provider[_pid("cloud-paid", "big")].allowed)
        self.assertEqual(by_provider[_pid("cloud-paid", "big")].reason, "paid_not_permitted")

    def test_permits_reads_the_same_decision(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        manifests = [_manifest("local-a", "m1"), _manifest("cloud-paid", "big", cost_class="paid_cloud")]
        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=manifests,
        )
        allowed = plan.permits("local-a", "m1")
        refused = plan.permits("cloud-paid", "big")
        self.assertIsNotNone(allowed)
        self.assertTrue(allowed.allowed)
        self.assertIsNotNone(refused)
        self.assertFalse(refused.allowed)
        # A candidate the plan never evaluated is NOT permitted: absence is not consent.
        self.assertIsNone(plan.permits("never-seen", "m9"))

    def test_local_only_ceiling_excludes_every_remote_manifest(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        manifests = [
            _manifest("local-a", "m1"),
            _manifest("cloud-free", "cf", cost_class="free_cloud", base_url="https://api.example.invalid/v1"),
            _manifest("cloud-paid", "big", cost_class="paid_cloud", base_url="https://api.example.invalid/v1"),
        ]
        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=manifests,
            local_only=True,
        )
        self.assertEqual(plan.locality_ceiling, "LOCAL_MACHINE_ONLY")
        for row in plan.eligibility:
            if row.provider_id != _pid("local-a", "m1"):
                self.assertFalse(row.allowed, row)
                self.assertEqual(row.reason, "local_only_turn")

    def test_allowed_provider_list_is_a_fence_not_a_preference(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        manifests = [_manifest("local-a", "m1"), _manifest("local-b", "m2")]
        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=manifests,
            allowed_provider_ids=("local-a",),
        )
        refused = plan.permits("local-b", "m2")
        self.assertIsNotNone(refused)
        self.assertFalse(refused.allowed)
        self.assertEqual(refused.reason, "provider_not_allowed")


class TestNoSilentWidening(unittest.TestCase):
    """free→paid and local→cloud widening must be impossible without an explicit grant."""

    def test_paid_never_allowed_without_explicit_paid_grant(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("cloud-paid", "big", cost_class="paid_cloud")],
            allow_paid=False,
        )
        self.assertFalse(plan.paid_allowed)
        refused = plan.permits("cloud-paid", "big")
        self.assertFalse(refused.allowed)
        self.assertEqual(refused.reason, "paid_not_permitted")
        self.assertEqual(plan.selected_provider, "")
        self.assertIn("no_eligible", plan.reason)

    def test_local_only_beats_paid_grant(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("cloud-paid", "big", cost_class="paid_cloud")],
            allow_paid=True,
            local_only=True,
        )
        self.assertFalse(plan.paid_allowed)

    def test_explicit_pin_yields_a_ladder_of_one(self) -> None:
        """A pinned model is a strict execution contract: no fallback widening."""
        from core.turn_routing import mint_turn_routing_plan

        manifests = [_manifest("local-a", "m1"), _manifest("local-b", "m2")]
        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=manifests,
            requested_provider="local-b",
            requested_model="m2",
        )
        self.assertEqual(plan.pin_kind, "MODEL")
        self.assertEqual(plan.requested_provider, "local-b")
        self.assertEqual(plan.requested_model, "m2")
        self.assertEqual(plan.fallback_ladder, (_pid("local-b", "m2"),))
        self.assertEqual(plan.selected_provider, _pid("local-b", "m2"))
        self.assertEqual(plan.selected_model, "m2")

    def test_unresolvable_pin_fails_closed_rather_than_falling_back(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        manifests = [_manifest("local-a", "m1")]
        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=manifests,
            requested_provider="local-b",
            requested_model="m2",
        )
        self.assertEqual(plan.selected_provider, "")
        self.assertEqual(plan.fallback_ladder, ())
        self.assertIn("pin_unresolved", plan.reason)


class TestContextIdentity(unittest.TestCase):
    def test_plan_carries_context_identity_and_reason(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("local-a", "m1")],
            context_identity="ctx-manifest-42",
            context_reason="session_transcript_v2",
        )
        self.assertEqual(plan.context_identity, "ctx-manifest-42")
        self.assertEqual(plan.context_reason, "session_transcript_v2")


class TestFailureVocabulary(unittest.TestCase):
    """refusal/cancel/timeout/unavailable/partial stay DISTINCT typed kinds."""

    def _plan(self):
        from core.turn_routing import mint_turn_routing_plan

        return mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("local-a", "m1")],
        )

    def test_all_five_kinds_exist_and_are_distinct(self) -> None:
        from core.turn_routing import RoutingFailureKind

        kinds = {kind.value for kind in RoutingFailureKind}
        self.assertEqual(
            kinds,
            {"REFUSED", "CANCELLED", "TIMEOUT", "UNAVAILABLE", "PARTIAL"},
        )

    def test_failure_rows_round_trip_with_their_kind(self) -> None:
        from core.turn_routing import record_routing_failure, latest_routing_failure

        with tempfile.TemporaryDirectory() as tmp:
            import os

            old = os.environ.get("VOOL_HOME")
            os.environ["VOOL_HOME"] = tmp
            try:
                plan = self._plan()
                record_routing_failure(
                    turn_id="turn-1",
                    session_id="sess-1",
                    provider_id="local-a",
                    model_id="m1",
                    kind="TIMEOUT",
                    stage="provider_call",
                    detail="deadline exceeded after 30s",
                    plan_digest=plan.digest(),
                )
                row = latest_routing_failure("sess-1")
                self.assertIsNotNone(row)
                self.assertEqual(row["kind"], "TIMEOUT")
                self.assertEqual(row["provider_id"], "local-a")
                self.assertEqual(row["turn_id"], "turn-1")
                self.assertEqual(row["plan_digest"], plan.digest())
            finally:
                if old is None:
                    os.environ.pop("VOOL_HOME", None)
                else:
                    os.environ["VOOL_HOME"] = old

    def test_latest_failure_excludes_the_current_turn(self) -> None:
        from core.turn_routing import record_routing_failure, latest_routing_failure

        with tempfile.TemporaryDirectory() as tmp:
            import os

            old = os.environ.get("VOOL_HOME")
            os.environ["VOOL_HOME"] = tmp
            try:
                for turn_id, kind in (("turn-1", "TIMEOUT"), ("turn-2", "CANCELLED")):
                    record_routing_failure(
                        turn_id=turn_id,
                        session_id="sess-1",
                        provider_id="local-a",
                        model_id="m1",
                        kind=kind,
                        stage="provider_call",
                        detail="",
                        plan_digest="d",
                    )
                self.assertEqual(latest_routing_failure("sess-1")["kind"], "CANCELLED")
                self.assertEqual(
                    latest_routing_failure("sess-1", exclude_turn_id="turn-2")["kind"],
                    "TIMEOUT",
                )
            finally:
                if old is None:
                    os.environ.pop("VOOL_HOME", None)
                else:
                    os.environ["VOOL_HOME"] = old

    def test_explanation_names_the_exact_typed_execution(self) -> None:
        from core.turn_routing import render_routing_failure_explanation

        row = {
            "turn_id": "turn-7",
            "provider_id": "cloud-paid",
            "model_id": "big",
            "kind": "TIMEOUT",
            "stage": "provider_call",
            "detail": "deadline exceeded after 30s",
            "plan_digest": "d" * 64,
        }
        text = render_routing_failure_explanation(row)
        self.assertIn("cloud-paid", text)
        self.assertIn("big", text)
        self.assertIn("timed out", text.lower())
        # Distinct kinds render distinct operator text.
        row_cancel = dict(row, kind="CANCELLED")
        self.assertNotEqual(text, render_routing_failure_explanation(row_cancel))


class TestRetryGenerationLinkage(unittest.TestCase):
    """"retry that exact request" = a NEW GENERATION linked to the original, not a new query."""

    def _original(self):
        from core.turn_routing import mint_turn_routing_plan

        return mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("local-a", "m1")],
            context_identity="ctx-1",
            requested_model="m1",
        )

    def test_retry_plan_links_original_identity_and_generation(self) -> None:
        from core.turn_routing import mint_turn_routing_plan, mint_retry_plan

        original = self._original()
        retry = mint_retry_plan(
            original,
            turn_id="turn-2",
            session_id="sess-1",
            manifests=[_manifest("local-a", "m1")],
        )
        self.assertEqual(retry.retry_of_plan_id, original.plan_id)
        self.assertEqual(retry.generation, original.generation + 1)
        self.assertNotEqual(retry.plan_id, original.plan_id)
        # Context identity carries: the retry re-executes the SAME conversation, not a fresh one.
        self.assertEqual(retry.context_identity, "ctx-1")

    def test_retry_never_widens_the_original_fences(self) -> None:
        from core.turn_routing import mint_turn_routing_plan, mint_retry_plan

        original = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("local-a", "m1"), _manifest("cloud-paid", "big", cost_class="paid_cloud")],
        )
        self.assertFalse(original.paid_allowed)
        # The caller asks for a paid-armed retry; the original fence holds.
        retry = mint_retry_plan(
            original,
            turn_id="turn-2",
            session_id="sess-1",
            manifests=[
                _manifest("local-a", "m1"),
                _manifest("cloud-paid", "big", cost_class="paid_cloud"),
            ],
            allow_paid=True,
        )
        self.assertFalse(retry.paid_allowed)
        refused = retry.permits("cloud-paid", "big")
        self.assertFalse(refused.allowed)

    def test_retry_of_retry_chains_generations(self) -> None:
        from core.turn_routing import mint_retry_plan

        first = self._original()
        second = mint_retry_plan(
            first, turn_id="turn-2", session_id="sess-1", manifests=[_manifest("local-a", "m1")]
        )
        third = mint_retry_plan(
            second, turn_id="turn-3", session_id="sess-1", manifests=[_manifest("local-a", "m1")]
        )
        self.assertEqual(second.generation, 2)
        self.assertEqual(third.generation, 3)
        self.assertEqual(third.retry_of_plan_id, second.plan_id)


class TestBrokerEnforcement(unittest.TestCase):
    """The broker seam refuses a manifest outside the plan BEFORE any provider call."""

    def test_assert_within_plan_passes_for_ladder_member(self) -> None:
        from core.turn_routing import assert_manifest_within_plan, mint_turn_routing_plan

        plan = mint_turn_routing_plan(
            turn_id="turn-1", session_id="sess-1", manifests=[_manifest("local-a", "m1")]
        )
        assert_manifest_within_plan(_manifest("local-a", "m1"), plan=plan)

    def test_assert_within_plan_refuses_prohibited_manifest(self) -> None:
        from core.turn_routing import (
            RoutingPlanRefused,
            assert_manifest_within_plan,
            mint_turn_routing_plan,
        )

        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            manifests=[_manifest("local-a", "m1"), _manifest("cloud-paid", "big", cost_class="paid_cloud")],
        )
        with self.assertRaises(RoutingPlanRefused) as raised:
            assert_manifest_within_plan(_manifest("cloud-paid", "big", cost_class="paid_cloud"), plan=plan)
        self.assertIn("paid_not_permitted", str(raised.exception))

    def test_plan_rides_context_under_a_reserved_key(self) -> None:
        from core.turn_routing import (
            TURN_ROUTING_PLAN_KEY,
            mint_turn_routing_plan,
            plan_from_context,
        )

        plan = mint_turn_routing_plan(
            turn_id="turn-1", session_id="sess-1", manifests=[_manifest("local-a", "m1")]
        )
        context = {TURN_ROUTING_PLAN_KEY: plan}
        self.assertIs(plan_from_context(context), plan)
        self.assertIsNone(plan_from_context({"unrelated": 1}))
        # The key is reserved: it must appear in the door's stripped trust keys so an
        # inbound HTTP body can never forge a routing plan.
        from core.request_trust import RESERVED_TRUST_KEYS

        self.assertIn(TURN_ROUTING_PLAN_KEY, RESERVED_TRUST_KEYS)


class TestTemporaryRules(_RulesHomeTestCase):
    """Rules arm, decrement, expire, cancel and survive restart EXACTLY."""

    def _arm(self, **overrides):
        from core.turn_routing import arm_rule

        kwargs = dict(
            scope_kind="session",
            scope_id="sess-1",
            directive="LOCAL_ONLY",
            turns_remaining=2,
        )
        kwargs.update(overrides)
        return arm_rule(**kwargs)

    def test_arm_then_decrement_per_turn_not_per_call(self) -> None:
        from core.turn_routing import consume_rules_for_mint

        self._arm()
        # Three "turns" each consuming once.
        first = consume_rules_for_mint(session_id="sess-1")
        second = consume_rules_for_mint(session_id="sess-1")
        third = consume_rules_for_mint(session_id="sess-1")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(third), 0, "a 2-turn rule must be gone after exactly 2 turns")

    def test_scoped_rule_does_not_touch_other_sessions(self) -> None:
        from core.turn_routing import consume_rules_for_mint

        self._arm(scope_kind="session", scope_id="sess-1")
        self.assertEqual(consume_rules_for_mint(session_id="sess-2"), [])
        self.assertEqual(len(consume_rules_for_mint(session_id="sess-1")), 1)

    def test_chat_scope_binds_to_conversation_ref(self) -> None:
        from core.turn_routing import consume_rules_for_mint

        self._arm(scope_kind="chat", scope_id="chat-9")
        # A different chat (and a turn with no chat binding at all) never consumes it.
        self.assertEqual(consume_rules_for_mint(session_id="sess-1", conversation_ref="chat-other"), [])
        self.assertEqual(consume_rules_for_mint(session_id="sess-1"), [])
        self.assertEqual(len(consume_rules_for_mint(session_id="sess-1", conversation_ref="chat-9")), 1)

    def test_expiry_is_enforced_even_without_consumption(self) -> None:
        from core.turn_routing import arm_rule, consume_rules_for_mint

        arm_rule(
            scope_kind="session",
            scope_id="sess-1",
            directive="LOCAL_ONLY",
            turns_remaining=None,
            expires_at_unix_ms=int(time.time() * 1000) - 1000,
        )
        self.assertEqual(consume_rules_for_mint(session_id="sess-1"), [])

    def test_cancel_kills_the_rule_immediately(self) -> None:
        from core.turn_routing import arm_rule, cancel_rule, consume_rules_for_mint

        rule = arm_rule(scope_kind="session", scope_id="sess-1", directive="LOCAL_ONLY", turns_remaining=5)
        cancel_rule(rule.rule_id)
        self.assertEqual(consume_rules_for_mint(session_id="sess-1"), [])

    def test_rules_survive_restart_by_reloading_from_disk(self) -> None:
        from core.turn_routing import consume_rules_for_mint, reload_rules

        self._arm(turns_remaining=1)
        # Simulate a process restart: drop every in-memory cache, then read again.
        reload_rules()
        consumed = consume_rules_for_mint(session_id="sess-1")
        self.assertEqual(len(consumed), 1)
        reload_rules()
        self.assertEqual(consume_rules_for_mint(session_id="sess-1"), [])

    def test_rule_armed_by_another_process_is_seen_without_restart(self) -> None:
        """A CLI/second process arming a rule must reach a running daemon's next mint."""
        import json as _json

        from core.turn_routing import arm_rule, consume_rules_for_mint, reload_rules

        # Warm this process's cache with an empty store.
        reload_rules()
        self.assertEqual(consume_rules_for_mint(session_id="sess-1"), [])
        # A DIFFERENT process writes the store (bypassing this process's writers).
        store = self.home / "data" / "turn_routing_rules.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        rule_id = "rr-external-01"
        store.write_text(
            _json.dumps(
                {
                    "schema": "vool.turn_routing_rules.v1",
                    "rules": [
                        {
                            "rule_id": rule_id,
                            "scope_kind": "session",
                            "scope_id": "sess-1",
                            "directive": "LOCAL_ONLY",
                            "target": "",
                            "turns_remaining": 1,
                            "expires_at_unix_ms": None,
                            "state": "ARMED",
                            "created_at_unix_ms": 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        consumed = consume_rules_for_mint(session_id="sess-1")
        self.assertEqual(len(consumed), 1, "the externally-armed rule must be visible")
        self.assertEqual(consumed[0].rule_id, rule_id)
        self.assertEqual(consume_rules_for_mint(session_id="sess-1"), [])

    def test_rule_store_is_json_on_disk(self) -> None:
        self._arm()
        store = self.home / "data" / "turn_routing_rules.json"
        self.assertTrue(store.exists())
        payload = json.loads(store.read_text())
        self.addCleanup(lambda: None)
        self.assertTrue(any(str(row.get("scope_id")) == "sess-1" for row in payload.get("rules", [])))


class TestPlanRecordRoundTrip(unittest.TestCase):
    def test_plan_record_round_trips_losslessly(self) -> None:
        from core.turn_routing import TurnRoutingPlan, mint_turn_routing_plan

        plan = mint_turn_routing_plan(
            turn_id="turn-1",
            session_id="sess-1",
            conversation_ref="chat-1",
            manifests=[_manifest("local-a", "m1"), _manifest("local-b", "m2")],
            requested_model="m1",
            context_identity="ctx-1",
        )
        record = plan.to_record()
        self.assertIsInstance(record, dict)
        clone = TurnRoutingPlan.from_record(record)
        self.assertEqual(clone.digest(), plan.digest())
        self.assertEqual(clone.fallback_ladder, plan.fallback_ladder)
        self.assertEqual(clone.eligibility, plan.eligibility)

    def test_provenance_row_is_written_per_turn(self) -> None:
        from core.turn_routing import mint_turn_routing_plan, provenance_for_turn, record_routing_provenance

        with tempfile.TemporaryDirectory() as tmp:
            import os

            old = os.environ.get("VOOL_HOME")
            os.environ["VOOL_HOME"] = tmp
            try:
                plan = mint_turn_routing_plan(
                    turn_id="turn-42",
                    session_id="sess-1",
                    manifests=[_manifest("local-a", "m1")],
                )
                record_routing_provenance(plan)
                row = provenance_for_turn("turn-42")
                self.assertIsNotNone(row)
                self.assertEqual(row["selected_provider"], _pid("local-a", "m1"))
                self.assertEqual(row["selected_model"], "m1")
                self.assertEqual(row["plan_digest"], plan.digest())
                self.assertEqual(row["session_id"], "sess-1")
            finally:
                if old is None:
                    os.environ.pop("VOOL_HOME", None)
                else:
                    os.environ["VOOL_HOME"] = old


class TestNoStickyContamination(unittest.TestCase):
    """The plan is minted from THIS turn's declared identity; nothing sticky persists."""

    def test_two_consecutive_mints_in_one_process_are_independent(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        manifests_a = [_manifest("local-a", "m1")]
        manifests_b = [_manifest("local-b", "m2")]
        plan_a = mint_turn_routing_plan(turn_id="t1", session_id="s1", manifests=manifests_a)
        plan_b = mint_turn_routing_plan(turn_id="t2", session_id="s2", manifests=manifests_b)
        self.assertEqual(plan_a.selected_provider, _pid("local-a", "m1"))
        self.assertEqual(plan_b.selected_provider, _pid("local-b", "m2"))
        # No module-level "last plan" survived the first mint.
        self.assertNotEqual(plan_a.plan_id, plan_b.plan_id)

    def test_minting_many_plans_in_parallel_keeps_every_scope_isolated(self) -> None:
        import threading

        from core.turn_routing import mint_turn_routing_plan

        results: dict[str, str] = {}
        lock = threading.Lock()

        def mint(session: str, provider: str) -> None:
            plan = mint_turn_routing_plan(
                turn_id=f"turn-{session}", session_id=session, manifests=[_manifest(provider, "m")]
            )
            with lock:
                results[session] = plan.selected_provider

        threads = [
            threading.Thread(target=mint, args=(f"sess-{i}", f"local-{i}")) for i in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(
            results,
            {f"sess-{i}": _pid(f"local-{i}", "m") for i in range(12)},
        )


class TestRetryPhraseFamilyPrecision(unittest.TestCase):
    """The widened retry phrase family must not capture non-retry turns."""

    def test_near_miss_phrases_do_not_classify_as_retry(self) -> None:
        from core.attempt_followup import classify_followup_intent

        for phrase in (
            "retry the exact questionnaire",        # not a request
            "what is an exact request",             # a question about requests
            "retry that idea someday",              # musing, not this-turn retry
            "explain the exact request format",     # explanation ask
            "run that exact request through review",  # review ask, not the anchored family
        ):
            with self.subTest(phrase=phrase):
                self.assertNotEqual(classify_followup_intent(phrase), "RETRY_ATTEMPT", phrase)

    def test_intended_retry_phrases_all_classify(self) -> None:
        from core.attempt_followup import classify_followup_intent

        for phrase in (
            "retry that exact request",
            "retry the exact failed request",
            "please retry this exact request",
            "retry that exact request now",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(classify_followup_intent(phrase), "RETRY_ATTEMPT", phrase)


class TestBootstrapContextRelevanceGates(unittest.TestCase):
    """bootstrap/self-knowledge context must be relevance-gated, not unconditional."""

    @staticmethod
    def _items_for(turn_text: str) -> dict[str, "object"]:
        from types import SimpleNamespace

        from core.bootstrap_context import build_bootstrap_context

        persona = SimpleNamespace(
            display_name="Test", tone="neutral", execution_style="direct", spirit_anchor="honesty",
        )
        task = SimpleNamespace(task_summary=turn_text)
        interpretation = SimpleNamespace(raw_text=turn_text, normalized_text=turn_text)
        items = build_bootstrap_context(
            persona=persona,
            task=task,
            classification={"task_class": "general_conversation"},
            interpretation=interpretation,
            session_id="a9-bootstrap-gate-test",
            include_private_context=False,
            include_history_context=False,
        )
        return {item.item_id: item for item in items}

    def test_self_knowledge_stays_gated_off_for_unrelated_turns(self) -> None:
        items = self._items_for("Will it rain in Vilnius tomorrow?")
        self.assertNotIn("bootstrap-self-knowledge", items)

    def test_self_knowledge_still_loaded_for_about_the_assistant_turns(self) -> None:
        items = self._items_for("What can you do?")
        self.assertIn("bootstrap-self-knowledge", items)

    def test_tool_doctrine_is_gated_off_for_purely_conversational_turns(self) -> None:
        items = self._items_for("Will it rain in Vilnius tomorrow?")
        self.assertNotIn("bootstrap-openclaw-doctrine", items)

    def test_tool_doctrine_still_loaded_for_tool_relevant_turns(self) -> None:
        items = self._items_for("Run this command in the terminal and show me the output.")
        self.assertIn("bootstrap-openclaw-doctrine", items)

    def test_conversational_prose_about_email_does_not_pull_tool_doctrine(self) -> None:
        """Venting/talking ABOUT email is not asking to USE email tooling."""
        items = self._items_for("I got so much email today and it drained me. What a day.")
        self.assertNotIn("bootstrap-openclaw-doctrine", items)

    def test_conversational_prose_about_calendar_does_not_pull_tool_doctrine(self) -> None:
        items = self._items_for("My calendar felt endless this week, I am exhausted.")
        self.assertNotIn("bootstrap-openclaw-doctrine", items)

    def test_conversational_workflow_prose_does_not_pull_tool_doctrine(self) -> None:
        items = self._items_for("My morning workflow is coffee then a walk, nothing fancy.")
        self.assertNotIn("bootstrap-openclaw-doctrine", items)

    def test_conversational_reminder_prose_does_not_pull_tool_doctrine(self) -> None:
        items = self._items_for("That film was a reminder of how good cinema used to be.")
        self.assertNotIn("bootstrap-openclaw-doctrine", items)

    def test_action_requests_still_pull_tool_doctrine(self) -> None:
        for text in (
            "Check my email and draft a short reply.",
            "Add a reminder for my dentist appointment.",
            "Set up a workflow that files my receipts weekly.",
            "Schedule a calendar event for the team review.",
            "Automate the backup of my notes folder.",
        ):
            with self.subTest(text=text):
                self.assertIn("bootstrap-openclaw-doctrine", self._items_for(text))


if __name__ == "__main__":
    unittest.main()
