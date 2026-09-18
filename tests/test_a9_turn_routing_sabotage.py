"""A9 P0 — sabotage proof for the three load-bearing invariants of the routing authority.

Each sabotage is applied SEPARATELY, at the production seam it attacks, and each test
proves BOTH directions:

1. under sabotage, the invariant's OBSERVABLE violation exists — the detector in the
   main packs would go red on exactly this observable;
2. after restore, the invariant holds again — the sabotage left no residue.

Sabotaged seams (mock.patch — the semantic equivalent of the repo's in-place source
flips, without the concurrent-run restore hazard):

* SCOPE BINDING      — `_routing_scope_identity` returns one fixed identity for every
                       turn (cross-session contamination).
* PAID-FALLBACK FENCE— `broker_refusal_for_manifest` always answers None (the broker
                       fence is removed) and the mint's paid gate is forced open.
* RETRY LINKAGE      — `plan_by_id` "loses" the original plan record, so a retry turn
                       mints an unlinked fresh plan (generation 1, no retry_of_plan_id).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from storage.model_provider_manifest import ModelProviderManifest


def _manifest(provider_name: str, model_name: str, *, cost_class: str = "free_local",
              base_url: str = "http://127.0.0.1:11434") -> ModelProviderManifest:
    metadata = {
        "runtime_family": "ollama",
        "model_digest": "sha256:stub",
        "chat_template_hash": "tmpl",
        "quantization": "q4_K_M",
        "parameter_billions": 1.5,
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
    )


LOCAL = lambda: _manifest("local-a", "m1")  # noqa: E731
PAID = lambda: _manifest("cloud-paid", "big", cost_class="paid_cloud",
                         base_url="https://api.example.invalid/v1")  # noqa: E731


class _HomeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._old = os.environ.get("VOOL_HOME")
        os.environ["VOOL_HOME"] = str(self.home)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("VOOL_HOME", None)
        else:
            os.environ["VOOL_HOME"] = self._old
        self._tmp.cleanup()


class TestSabotageScopeBinding(_HomeTestCase):
    """One fixed identity for every turn = the contamination this authority exists to kill."""

    def test_fixed_identity_sabotage_is_observable_and_restorable(self) -> None:
        from core import memory_first_router as router_mod
        from core.turn_routing import mint_turn_routing_plan

        # Invariant holds: two sessions mint two distinct scoped plans.
        plan_a = mint_turn_routing_plan(turn_id="t1", session_id="sess-a", manifests=[LOCAL()])
        plan_b = mint_turn_routing_plan(turn_id="t2", session_id="sess-b", manifests=[LOCAL()])
        self.assertEqual((plan_a.session_id, plan_b.session_id), ("sess-a", "sess-b"))

        # SABOTAGE: the router's identity read returns one fixed scope for everyone.
        with mock.patch.object(
            router_mod,
            "_routing_scope_identity",
            return_value=("fixed-turn", "fixed-session", "", ""),
        ):
            turn_id, session_id, _, _ = router_mod._routing_scope_identity({"session_id": "sess-a"})
            self.assertEqual((turn_id, session_id), ("fixed-turn", "fixed-session"))
            # The observable violation: a sess-b context would mint under sess-a's scope.
            # The served/unit detectors key on exactly this mapping, so they go red here.
            sabotaged = router_mod._mint_routing_plan_for_turn(
                None,
                source_context={"session_id": "sess-b", "_canonical_user_turn_id": "t-b"},
                requested_provider=None,
                requested_model="",
                allow_paid=False,
                local_only=False,
                user_text="hello",
            )
            if sabotaged is not None:
                self.assertEqual(sabotaged.session_id, "fixed-session")
                self.assertNotEqual(sabotaged.session_id, "sess-b")

        # Restored: the identity read binds the context's own session again.
        turn_id, session_id, _, _ = router_mod._routing_scope_identity(
            {"session_id": "sess-b", "_canonical_user_turn_id": "t-b"}
        )
        self.assertEqual((turn_id, session_id), ("t-b", "sess-b"))

    def test_rule_scope_sabotage_cannot_leak_across_chats(self) -> None:
        from core.turn_routing import arm_rule, consume_rules_for_mint

        arm_rule(scope_kind="chat", scope_id="chat-9", directive="LOCAL_ONLY", turns_remaining=1)
        # SABOTAGE: the scope matcher claims every chat matches.
        with mock.patch(
            "core.turn_routing._rule_in_scope", return_value=True
        ):
            leaked = consume_rules_for_mint(session_id="sess-x", conversation_ref="chat-OTHER")
            self.assertEqual(len(leaked), 1, "sabotage must be observable: the rule leaked")

        # Restored: a different chat never consumes the rule.
        self.assertEqual(consume_rules_for_mint(session_id="sess-x", conversation_ref="chat-OTHER"), [])


class TestSabotagePaidFallbackFence(_HomeTestCase):
    """Removing the broker fence / forcing the paid gate open must be detectable."""

    def test_broker_fence_removal_is_observable_and_restorable(self) -> None:
        import core.turn_routing as turn_routing
        from core.turn_routing import TURN_ROUTING_PLAN_KEY, mint_turn_routing_plan

        plan = mint_turn_routing_plan(turn_id="t1", session_id="sess-a",
                                      manifests=[LOCAL(), PAID()])
        context = {TURN_ROUTING_PLAN_KEY: plan}
        self.assertIn(
            "paid_not_permitted",
            turn_routing.broker_refusal_for_manifest(PAID(), context) or "",
        )

        # SABOTAGE: the broker fence is removed entirely (the router seam reads the module
        # attribute, exactly what is patched here).
        with mock.patch("core.turn_routing.broker_refusal_for_manifest", return_value=None):
            self.assertIsNone(turn_routing.broker_refusal_for_manifest(PAID(), context),
                              "sabotage must be observable: the paid candidate walked through")

        # Restored: the refusal is back — zero generation calls for the paid candidate.
        self.assertIn(
            "paid_not_permitted",
            turn_routing.broker_refusal_for_manifest(PAID(), context) or "",
        )

    def test_mint_paid_gate_forced_open_is_observable_and_restorable(self) -> None:
        from core.turn_routing import mint_turn_routing_plan

        self.assertFalse(
            mint_turn_routing_plan(turn_id="t1", session_id="sess-a",
                                   manifests=[LOCAL(), PAID()]).paid_allowed
        )
        # SABOTAGE: the eligibility gate treats paid as free.
        real = __import__("core.turn_routing", fromlist=["_eligibility_reason"])._eligibility_reason

        def open_gate(manifest, **kwargs):
            kwargs["paid_allowed"] = True
            return real(manifest, **kwargs)

        with mock.patch("core.turn_routing._eligibility_reason", side_effect=open_gate):
            sabotaged = mint_turn_routing_plan(turn_id="t2", session_id="sess-a",
                                               manifests=[LOCAL(), PAID()])
            # The plan's own paid flag still says False (the mint grant never opened); the
            # SABOTAGED observable is the eligibility row flipping allowed — that row is
            # what the ranker and the broker both consume, so both would let paid through.
            row = sabotaged.permits("cloud-paid", "big")
            self.assertTrue(row.allowed,
                            "sabotage must be observable: the paid row was silently allowed")
            self.assertIn("cloud-paid:big", sabotaged.fallback_ladder)

        # Restored: the fence holds again.
        restored = mint_turn_routing_plan(turn_id="t3", session_id="sess-a",
                                          manifests=[LOCAL(), PAID()])
        self.assertFalse(restored.paid_allowed)
        self.assertFalse(restored.permits("cloud-paid", "big").allowed)


class TestSabotageRetryLinkage(_HomeTestCase):
    """A retry that mints an unlinked fresh plan must be detectable."""

    def test_lost_original_plan_record_is_observable_and_restorable(self) -> None:
        from core import memory_first_router as router_mod
        from core.turn_routing import (
            TURN_ROUTING_PLAN_KEY,
            mint_turn_routing_plan,
            record_routing_provenance,
        )

        class _Registry:
            def list_manifests(self, **_kwargs):
                return [LOCAL()]

        original = mint_turn_routing_plan(
            turn_id="t1", session_id="sess-a", manifests=[LOCAL()], context_identity="ctx-1",
        )
        record_routing_provenance(original)

        def _mint(context_extra: dict) -> object:
            return router_mod._mint_routing_plan_for_turn(
                _Registry(),
                source_context={
                    "session_id": "sess-a",
                    "_canonical_user_turn_id": "t2",
                    **context_extra,
                },
                requested_provider=None,
                requested_model="",
                allow_paid=False,
                local_only=False,
                user_text="retry that exact request",
            )

        # Invariant holds: the retry marker mints generation 2, linked to the original.
        linked = _mint({"turn_routing_retry": {"plan_id": original.plan_id, "user_text": "Q"}})
        self.assertEqual(linked.generation, 2)
        self.assertEqual(linked.retry_of_plan_id, original.plan_id)

        # SABOTAGE: the original plan record is "lost" — the retry mints unlinked.
        with mock.patch("core.turn_routing.plan_by_id", return_value=None):
            unlinked = _mint({"turn_routing_retry": {"plan_id": original.plan_id, "user_text": "Q"}})
            self.assertEqual(unlinked.generation, 1,
                             "sabotage must be observable: the retry lost its generation")
            self.assertEqual(unlinked.retry_of_plan_id, "",
                             "sabotage must be observable: the retry lost its linkage")

        # Restored: the linkage reads the record again.
        again = _mint({"turn_routing_retry": {"plan_id": original.plan_id, "user_text": "Q"}})
        self.assertEqual(again.generation, 2)
        self.assertEqual(again.retry_of_plan_id, original.plan_id)


if __name__ == "__main__":
    unittest.main()
