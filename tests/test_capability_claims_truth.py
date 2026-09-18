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
    """The disclosure must say what the tool DOES, and that it is refused today.

    This asserted the description contains "never moves funds". It does not, and it
    must not: this tool moves real value off the machine the moment an operator
    enables the lane, so an unconditional "never moves funds" would be exactly the
    reassuring falsehood the rest of this file exists to prevent. The description
    says the true pair instead -- it moves real value, AND it is refused
    (`x402_spend_disabled`) unless the operator enables it -- and that is what is
    pinned here.
    """
    desc = _spec("pay.x402")["description"].lower()
    assert "disabled" in desc, desc
    assert "real value" in desc, desc
    assert "off this machine" in desc, desc
    assert "unless the operator enables it" in desc, desc
    # It must never read as harmless.
    assert "never moves funds" not in desc, (
        "an unconditional 'never moves funds' is false for a tool that spends when enabled"
    )
    assert "buy external x402-gated compute with usdc." not in desc  # the old unqualified claim
