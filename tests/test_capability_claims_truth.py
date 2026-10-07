"""The tool descriptions injected into the model's catalog must match what the wired code does —
no 'signed'/'wallet' where there is none, no 'buy with USDC' where the spend lane is disabled.
Guards the audit's advertised-vs-real findings."""
from __future__ import annotations

from core.execution.capabilities import runtime_tool_specs


def _spec(intent: str) -> dict:
    for s in runtime_tool_specs(allow_web_fallback_fn=lambda: False):
        if s.get("intent") == intent:
            return s
    raise AssertionError(f"{intent} not in catalog")


def test_sell_quote_does_not_claim_signing_or_a_real_wallet():
    desc = _spec("sell.quote")["description"].lower()
    assert "and signed quote hash" not in desc  # the old overclaim ("unsigned quote hash" is fine)
    assert "unsigned quote hash" in desc  # states the truth explicitly
    assert "not configured" in desc  # discloses no real wallet / signing key


def test_pay_x402_discloses_the_spend_lane_is_disabled():
    # the earlier reassurance "never moves funds" was itself the overclaim (core/execution/capabilities.py, measured
    # 2026-08-29: the contract says the tool moves real value); the truthful description says the lane is disabled
    # until the operator enables it and that a settled call moves real value off this machine
    desc = _spec("pay.x402")["description"].lower()
    assert "disabled" in desc and "moves real value" in desc
    assert "never moves funds" not in desc
    assert "buy external x402-gated compute with usdc." not in desc  # the old unqualified claim
