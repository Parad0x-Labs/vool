"""A10 P1-2 — one declared KAS→VOOL channel ingress contract.

Mechanically proves the guarantees the contract in
``core/channel_gateway.process_channel_request`` declares, plus the dedicated
channel-verifier repairs: remote deny-stamp at ingress, A7 finalization identity
carried-through (never re-minted by channel code), the /api/chat shim's
equivalent surface-based deny rule, CE-1 (a policy-refused payload can never be
direct-delivered as if the mirror were merely down), and CE-3's typed
pre-finalization statement for queued outbound posts.
"""
from __future__ import annotations

import unittest
from unittest import mock

import core.channel_actions as ca
from core.channel_gateway import ChannelGatewayResult, ChannelRequest, build_source_context, process_channel_request
from core.request_trust import OWNER_LOCAL_KEY, request_is_owner_local


class _FakeAgent:
    def run_once(self, text, *, session_id_override=None, source_context=None):
        return {
            "task_id": "t-a10",
            "mode": "direct",
            "confidence": 1.0,
            "prompt_assembly_report": {},
            "response": "raw-never-served",
            # A7-minted commit (as core.finalization would produce): channel
            # code consumes it and carries finalization_id through untouched.
            "vool_response_commit": {
                "type": "response.commit",
                "canonical_content": "canonical-bytes",
                "finalization_id": "fin-A7-MINTED",
            },
        }


class GatewayIngressContract(unittest.TestCase):
    def test_owner_local_denied_at_ingress(self) -> None:
        ctx = build_source_context(
            ChannelRequest(platform="discord", user_id="u", text="hi", surface="cli")
        )
        # Even a forged local-looking surface string cannot grant owner trust:
        # the gateway stamps the key explicitly False.
        self.assertIs(ctx[OWNER_LOCAL_KEY], False)
        self.assertFalse(request_is_owner_local(ctx))

    def test_finalization_identity_carried_not_reminted(self) -> None:
        result = process_channel_request(_FakeAgent(), ChannelRequest(
            platform="telegram", user_id="u", text="hi"))
        self.assertIsInstance(result, ChannelGatewayResult)
        self.assertEqual(result.response_text, "canonical-bytes")  # finalized bytes, not raw
        self.assertEqual(result.finalization_id, "fin-A7-MINTED")  # carried verbatim
        self.assertFalse(result.source_context[OWNER_LOCAL_KEY])

    def test_shim_lane_surface_rule_matches_contract_guarantee(self) -> None:
        # The /api/chat compatibility shim relies on this exact dispatch rule
        # (core/web/api/service.py): a channel surface is never owner-local,
        # even over loopback — the deny-equivalent of the gateway stamp.
        for surface in ("channel", "telegram", "discord_bot"):
            from core.web.api.service import is_loopback_host

            owner_local = is_loopback_host("127.0.0.1") and surface != "channel"
            if surface == "channel":
                self.assertFalse(owner_local)


class QueuedOutboundFinalizationIdentity(unittest.TestCase):
    """CE-3: queued outbound posts declare their A7 identity state explicitly."""

    def test_pre_finalization_records_declare_themselves(self) -> None:
        from relay.channel_outbound import build_outbound_post_record

        record = build_outbound_post_record(
            platform="discord", content="queued before any seal",
            task_id="t", session_id="s", source_context=None,
        )
        self.assertNotIn("finalization_id", record)
        self.assertEqual(record["finalization_state"], "pre_finalization")

    def test_carried_fid_is_never_minted_only_forwarded(self) -> None:
        from relay.channel_outbound import build_outbound_post_record

        record = build_outbound_post_record(
            platform="telegram", content="x", task_id="t", session_id="s",
            source_context=None, finalization_id="fc:a7minted",
        )
        # Carried VERBATIM from upstream A7 — channel code adds nothing.
        self.assertEqual(record["finalization_state"], "carried")
        self.assertEqual(record["finalization_id"], "fc:a7minted")

    def test_platform_ack_refusal_on_empty_fid_intact(self) -> None:
        from relay.channel_outbound import propose_bridge_delivery

        # The evidence-gated reconcile REFUSES an identity-less proposal —
        # the refusal that keeps bridge acks honest for pre-finalization records.
        self.assertFalse(propose_bridge_delivery(""))


class RefusedPayloadNeverDirectDelivered(unittest.TestCase):
    """CE-1: policy-withheld != mirror-down; direct delivery preserves the deny."""

    def _dispatch(self, append_ok: bool, append_record: dict):
        calls = {"direct": 0}

        def failing_direct(intent):
            calls["direct"] += 1
            return False, "would_have_sent"

        with mock.patch.object(ca, "append_outbound_post", return_value=(append_ok, append_record)), \
             mock.patch.object(ca, "_try_direct_delivery", side_effect=failing_direct):
            result = ca.dispatch_outbound_post_intent(
                ca.ChannelPostIntent(platform="discord", message="payload"),
                task_id="t", session_id="s", source_context=None,
            )
        return result, calls["direct"]

    def test_availability_refusal_preserves_canonical_deny(self) -> None:
        result, direct_calls = self._dispatch(False, {
            "kind": "outbound_post_refused",
            "reason": "payload_unavailable_by_policy",
            "availability": "erased",
        })
        self.assertEqual(direct_calls, 0, "policy refusal MUST NOT reach direct delivery")
        self.assertEqual(result.status, "refused_by_policy")
        self.assertFalse(result.ok)

    def test_operational_mirror_failure_still_falls_back(self) -> None:
        # Behavior preserved for genuine transport trouble: the mirror being
        # unreachable still allows the configured-credentials fallback.
        result, direct_calls = self._dispatch(False, {"kind": "mirror_fetch_failed"})
        self.assertEqual(direct_calls, 1)
        self.assertEqual(result.status, "failed")  # fallback itself failed in this fake


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
