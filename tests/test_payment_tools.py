"""Payment tools after the money-authority retirement.

``sell.quote`` is read-only and stays. ``pay.x402`` used to be the gated buy lane (opt-in +
approval + wallet + cap + spend brake -> ``dna_pay_and_unlock``); it is a retired money surface
now. Every ``pay.x402`` test therefore proves the same thing from a different angle: the tool
answers a typed, receipt-backed refusal (``status == wallet_legacy_surface_retired``), no payment
function is ever called, no quote is fetched for the caller's URL, and none of the old unlocks --
the env flag, the model-supplied flags, a trusted wallet, a granted spend brake -- opens anything.
The permission controller still decides a modeless ``pay.x402`` BEFORE the tool is reached.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from core.execution.payment_tools import execute_payment_tool
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent, runtime_tool_specs
from core.wallet.authority import LEGACY_RETIRED


def _offline_http(*args, **kwargs):
    # No network in tests: dna_get_quote returns an error envelope, so the
    # preview quote stays empty and no spend path is reachable.
    return {"error": True, "message": "offline"}


def _must_not_run(*args, **kwargs):
    raise AssertionError("a retired payment surface reached a network / payment function")


def _assert_typed_refusal(result, *, surface: str = "tool.pay.x402") -> dict:
    """The refusal shape: ok False, status = the fault code, a fault receipt on file, nothing executed."""
    from core.faults.recorder import fault_by_id
    from core.security_events.catalog import SEC_WALLET_LEGACY_SURFACE_RETIRED
    from core.security_events.store import list_security_events

    assert result.handled is True
    assert result.ok is False
    assert result.status == LEGACY_RETIRED
    assert result.mode == "tool_failed"
    assert result.tool_name == "pay.x402"
    assert result.details["executed"] is False
    fault = result.details["fault"]
    assert fault["code"] == LEGACY_RETIRED
    assert fault["fault_id"].startswith("fault-"), fault
    assert fault["context"]["surface"] == surface
    assert result.user_safe_response_text and result.response_text
    observation = result.details["observation"]
    assert observation["status"] == LEGACY_RETIRED and observation["ok"] is False
    assert observation["fault_id"] == fault["fault_id"]
    record = fault_by_id(fault["fault_id"])
    assert record is not None and record.code == LEGACY_RETIRED and record.context.get("surface") == surface
    observed = [e for e in list_security_events(limit=50) if e.fault_id == fault["fault_id"]]
    assert observed and observed[0].sec_code == SEC_WALLET_LEGACY_SURFACE_RETIRED
    return fault


class SellQuoteTests(unittest.TestCase):
    def test_sell_quote_is_read_only_and_returns_a_quote(self) -> None:
        result = execute_payment_tool("sell.quote", {"resource": "null://task/code-review"})

        self.assertTrue(result.handled)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "quoted")
        self.assertEqual(result.mode, "tool_executed")
        quote = result.details["quote"]
        self.assertGreater(float(quote["amount_usdc"]), 0.0)
        self.assertTrue(str(quote["quote_hash"]))
        self.assertEqual(quote["service"], "task")
        self.assertEqual(result.details["observation"]["tool_surface"], "x402_market")

    def test_sell_quote_resolves_null_target_endpoint(self) -> None:
        result = execute_payment_tool(
            "sell.quote",
            {"resource": "null://task/render", "null_name": "studio.null"},
            resolve_x402_endpoint_fn=lambda name, **kw: "https://studio.example.test/x402",
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.details["quote"]["resolved_x402_endpoint"], "https://studio.example.test/x402")

    def test_sell_quote_files_no_wallet_refusal(self) -> None:
        from core.faults.recorder import list_faults

        result = execute_payment_tool("sell.quote", {"resource": "null://task/quote"})
        self.assertEqual(result.status, "quoted")
        self.assertEqual(list_faults(code=LEGACY_RETIRED, limit=10), [])


class PayX402RetiredTests(unittest.TestCase):
    def setUp(self) -> None:
        # The env flag used to open the x402 USDC spend lane. It is set here to prove it opens
        # nothing any more: the surface refuses with the flag on exactly as it does with it off.
        self._prev_x402 = os.environ.get("VOOL_ENABLE_X402_SPEND")
        os.environ["VOOL_ENABLE_X402_SPEND"] = "1"

    def tearDown(self) -> None:
        if self._prev_x402 is None:
            os.environ.pop("VOOL_ENABLE_X402_SPEND", None)
        else:
            os.environ["VOOL_ENABLE_X402_SPEND"] = self._prev_x402

    def test_pay_refuses_without_opt_in_and_never_previews_a_quote(self) -> None:
        pay_spy = mock.Mock(side_effect=_must_not_run)
        quote_spy = mock.Mock(side_effect=_must_not_run)

        result = execute_payment_tool(
            "pay.x402",
            {"resource": "https://compute.example.test/job", "max_spend_usdc": 0.02, "approve": True},
            source_context={"vool_wallet": object()},
            dna_pay_and_unlock_fn=pay_spy,
            dna_get_quote_fn=quote_spy,
        )

        _assert_typed_refusal(result)
        pay_spy.assert_not_called()
        quote_spy.assert_not_called()  # the old preview fetched the caller's URL; nothing is fetched now

    def test_pay_refuses_when_fully_opted_in_approved_and_walleted(self) -> None:
        pay_spy = mock.Mock(side_effect=_must_not_run)

        result = execute_payment_tool(
            "pay.x402",
            {"resource": "https://compute.example.test/job", "allow_spend": True, "approve": True, "max_spend_usdc": 0.02},
            source_context={"vool_wallet": object(), "_owner_local": True, "owner_local": True},
            dna_pay_and_unlock_fn=pay_spy,
            dna_get_quote_fn=_offline_http,
        )

        fault = _assert_typed_refusal(result)
        pay_spy.assert_not_called()
        self.assertNotIn("amount_paid_usdc", result.details)
        self.assertNotIn("receipt_id", result.details)
        self.assertIn("wallet", result.response_text.lower())  # the reply points at the one remaining door
        self.assertTrue(fault["fault_id"])

    def test_a_granted_spend_brake_opens_nothing(self) -> None:
        # The OS-consent brake used to be the last gate before signing. Granting it changes nothing:
        # the surface refuses BEFORE any brake could matter, and the brake is never the reason.
        pay_spy = mock.Mock(side_effect=_must_not_run)
        with mock.patch("core.spend_authorization.require_spend_authorized", return_value=(True, "ok")):
            result = execute_payment_tool(
                "pay.x402",
                {"resource": "https://compute.example.test/job", "allow_spend": True, "approve": True, "max_spend_usdc": 999.0},
                source_context={"vool_wallet": object(), "_owner_local": True},
                dna_pay_and_unlock_fn=pay_spy,
                dna_get_quote_fn=_offline_http,
            )
        _assert_typed_refusal(result)
        self.assertNotEqual(result.status, "paid")
        pay_spy.assert_not_called()

    def test_a_denied_spend_brake_is_not_the_answer_either(self) -> None:
        pay_spy = mock.Mock(side_effect=_must_not_run)
        with mock.patch("core.spend_authorization.require_spend_authorized", return_value=(False, "OS consent was declined")):
            result = execute_payment_tool(
                "pay.x402",
                {"resource": "https://compute.example.test/job", "allow_spend": True, "approve": True, "max_spend_usdc": 0.02},
                source_context={"vool_wallet": object(), "_owner_local": True},
                dna_pay_and_unlock_fn=pay_spy,
                dna_get_quote_fn=_offline_http,
            )
        _assert_typed_refusal(result)
        self.assertNotEqual(result.status, "spend_not_authorized")
        pay_spy.assert_not_called()

    def test_pay_refuses_for_a_null_name_without_resolving_it(self) -> None:
        # The old lane resolved `null_name` on-chain to find a resource URL; the retired surface
        # answers before any resolution or quote.
        resolver = mock.Mock(side_effect=_must_not_run)
        result = execute_payment_tool(
            "pay.x402",
            {"null_name": "studio.null", "allow_spend": True, "approve": True, "max_spend_usdc": 0.02},
            source_context={"vool_wallet": object()},
            dna_pay_and_unlock_fn=mock.Mock(side_effect=_must_not_run),
            dna_get_quote_fn=mock.Mock(side_effect=_must_not_run),
            resolve_x402_endpoint_fn=resolver,
        )
        _assert_typed_refusal(result)
        resolver.assert_not_called()

    def test_every_refusal_is_its_own_receipt(self) -> None:
        first = execute_payment_tool("pay.x402", {"resource": "https://a.example/job"}, dna_get_quote_fn=_offline_http)
        second = execute_payment_tool("pay.x402", {"resource": "https://b.example/job"}, dna_get_quote_fn=_offline_http)
        a, b = _assert_typed_refusal(first), _assert_typed_refusal(second)
        self.assertNotEqual(a["fault_id"], b["fault_id"])


class PaymentToolWiringTests(unittest.TestCase):
    def test_runtime_specs_advertise_both_payment_tools(self) -> None:
        specs = {item["intent"]: item for item in runtime_tool_specs()}

        self.assertIn("sell.quote", specs)
        self.assertIn("pay.x402", specs)
        self.assertTrue(specs["sell.quote"]["read_only"])
        self.assertFalse(specs["pay.x402"]["read_only"])

    def test_pay_x402_without_a_mode_is_stopped_by_the_permission_controller(self) -> None:
        """A turn that names no mode does not get to spend money.

        This used to reach the payment tool directly, because a modeless call skipped the
        permission decision entirely and relied on the tool itself to refuse. The tool's own
        refusal still exists (below) -- it is now the second line, not the only one.
        """
        from core.faults.recorder import list_faults
        from core.mode_permission_policy import reset_mode_permission_state

        reset_mode_permission_state()
        self.addCleanup(reset_mode_permission_state)
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        with mock.patch("core.web0_tools.dna_pay_and_unlock") as paid, mock.patch(
            "core.web0_tools.dna_get_quote",
            side_effect=_offline_http,
        ):
            result = execute_tool_intent(
                {"intent": "pay.x402", "arguments": {"resource": "https://compute.example.test/job"}},
                task_id="task-1",
                session_id="session-1",
                source_context={"surface": "openclaw", "platform": "openclaw"},
                hive_activity_tracker=tracker,
            )

        self.assertTrue(result.handled)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "pending_approval")
        self.assertIs(result.details["executed"], False)
        paid.assert_not_called()
        # The controller answered first: the retired tool was never reached, so no refusal was filed.
        self.assertEqual(list_faults(code=LEGACY_RETIRED, limit=10), [])

    def test_an_approved_pay_x402_lands_on_the_typed_refusal(self) -> None:
        """Past the controller, the tool's own boundary must still hold.

        Approval is granted here so the call actually reaches `payment_tools` -- otherwise this
        would assert nothing about the tool and would pass just because the gate above it fired.
        """
        from core.mode_permission_policy import reset_mode_permission_state, resolve_approval

        reset_mode_permission_state()
        self.addCleanup(reset_mode_permission_state)
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        payload = {"intent": "pay.x402", "arguments": {"resource": "https://compute.example.test/job", "allow_spend": True, "approve": True, "max_spend_usdc": 0.02}}
        context = {"surface": "openclaw", "platform": "openclaw"}

        with mock.patch("core.web0_tools.dna_pay_and_unlock"), mock.patch(
            "core.web0_tools.dna_get_quote", side_effect=_offline_http
        ):
            pending = execute_tool_intent(
                payload, task_id="task-1", session_id="session-1", source_context=context,
                hive_activity_tracker=tracker,
            )
        token = str(pending.details["approval_request"]["approval_id"])
        self.assertIsNotNone(resolve_approval(token, decision="allow"))

        with mock.patch("core.web0_tools.dna_pay_and_unlock") as paid, mock.patch(
            "core.web0_tools.dna_get_quote", side_effect=_offline_http
        ):
            result = execute_tool_intent(
                payload,
                task_id="task-1",
                session_id="session-1",
                source_context={**context, "mode_approval_token": token},
                hive_activity_tracker=tracker,
            )

        _assert_typed_refusal(result)
        paid.assert_not_called()

    def test_executor_dispatches_sell_quote_read_only(self) -> None:
        tracker = HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))
        result = execute_tool_intent(
            {"intent": "sell.quote", "arguments": {"resource": "null://embed/search"}},
            task_id="task-1",
            session_id="session-1",
            source_context={"surface": "openclaw", "platform": "openclaw"},
            hive_activity_tracker=tracker,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "quoted")
        self.assertEqual(result.tool_name, "sell.quote")
        self.assertEqual(result.details["quote"]["service"], "embed")


if __name__ == "__main__":
    unittest.main()
