from __future__ import annotations

import unittest

from core.null_protocol import _price_for, parse_null_uri, resolve_null_request
from core.web0_capability_broadcast import (
    DEFAULT_PRICE_PER_TOKEN,
    build_manifest_from_env,
    resolve_announced_price_usdc,
    resolve_privacy_mode,
)


class PerTokenMeterTests(unittest.TestCase):
    PPT = 0.000002

    def test_measured_tokens_bill_as_tokens_times_price(self) -> None:
        uri = parse_null_uri("null://task/code-review")
        amount = _price_for(uri, price_per_token_usdc=self.PPT, measured_output_tokens=500)
        self.assertAlmostEqual(amount, 500 * self.PPT)

    def test_explicit_price_overrides_measured(self) -> None:
        uri = parse_null_uri("null://task/x?price=0.01")
        amount = _price_for(uri, price_per_token_usdc=self.PPT, measured_output_tokens=9999)
        self.assertAlmostEqual(amount, 0.01)

    def test_falls_back_to_flat_table_without_measurement(self) -> None:
        uri = parse_null_uri("null://embed/search")
        amount = _price_for(uri, price_per_token_usdc=self.PPT, measured_output_tokens=None)
        self.assertAlmostEqual(amount, 0.0001)  # _BASE_PRICES["embed"]

    def test_zero_or_negative_measurement_is_ignored(self) -> None:
        uri = parse_null_uri("null://task/x")
        self.assertAlmostEqual(
            _price_for(uri, price_per_token_usdc=self.PPT, measured_output_tokens=0),
            0.0005,  # _BASE_PRICES["task"]
        )

    def test_resolve_null_request_threads_measured_tokens(self) -> None:
        req = resolve_null_request(
            "null://task/code-review",
            price_per_token_usdc=self.PPT,
            measured_output_tokens=1000,
        )
        self.assertIsNotNone(req.quote)
        self.assertAlmostEqual(req.quote.amount_usdc, 1000 * self.PPT)


class ManifestPricePrivacyTests(unittest.TestCase):
    def test_price_resolved_from_env(self) -> None:
        self.assertAlmostEqual(
            resolve_announced_price_usdc({"VOOL_WEB0_PRICE_PER_TOKEN": "0.000005"}),
            0.000005,
        )

    def test_price_defaults_when_absent_or_garbage(self) -> None:
        self.assertEqual(resolve_announced_price_usdc({}), DEFAULT_PRICE_PER_TOKEN)
        self.assertEqual(resolve_announced_price_usdc({"VOOL_WEB0_PRICE_PER_TOKEN": "abc"}), DEFAULT_PRICE_PER_TOKEN)

    def test_negative_price_clamped(self) -> None:
        self.assertEqual(resolve_announced_price_usdc({"VOOL_WEB0_PRICE_PER_TOKEN": "-1"}), 0.0)

    def test_privacy_mode_valid_and_invalid(self) -> None:
        self.assertEqual(resolve_privacy_mode({"VOOL_WEB0_PRIVACY_MODE": "zk_ready"}), "zk_ready")
        self.assertEqual(resolve_privacy_mode({"VOOL_WEB0_PRIVACY_MODE": "bogus"}), "plain")
        self.assertEqual(resolve_privacy_mode({}), "plain")

    def test_manifest_from_env_carries_both(self) -> None:
        m = build_manifest_from_env(
            {"VOOL_WEB0_PRICE_PER_TOKEN": "0.000003", "VOOL_WEB0_PRIVACY_MODE": "zk_active"}
        )
        self.assertAlmostEqual(m.price_per_token_usdc, 0.000003)
        self.assertEqual(m.privacy_mode, "zk_active")


if __name__ == "__main__":
    unittest.main()
