"""The wallet surface carries one soft warning: no independent external security audit yet.

Operator decision (2026-09-07): mainnet use is the product's stance and the absence of an external audit
is stated as a soft warning, never a gate; users test on devnets or mainnets at their own pace. The
warning rides the same public-safe status payload the surface already polls, so every renderer
(fragment, Settings, API consumers) reads one authority.
"""
from __future__ import annotations

import re

import core.wallet_fragment as fragment_module
from core.wallet.status import SOFT_WARNINGS, wallet_status


def _fragment_source() -> str:
    for name in ("wallet_fragment", "render_wallet_fragment", "wallet_fragment_html", "WALLET_FRAGMENT"):
        value = getattr(fragment_module, name, None)
        if callable(value):
            return str(value())
        if isinstance(value, str):
            return value
    raise AssertionError("no fragment accessor found on core.wallet_fragment")


def test_status_carries_the_external_audit_soft_warning() -> None:
    status = wallet_status()
    codes = [item["code"] for item in status.get("soft_warnings", [])]
    assert "no_external_audit" in codes, status.get("soft_warnings")
    text = next(item["text"] for item in status["soft_warnings"] if item["code"] == "no_external_audit")
    assert "external" in text.lower() and "audit" in text.lower()
    assert re.search(r"devnet|testnet", text, re.IGNORECASE), text
    assert [dict(w) for w in SOFT_WARNINGS] == status["soft_warnings"]


def test_the_fragment_renders_soft_warnings_from_the_status_payload() -> None:
    source = _fragment_source()
    assert "soft_warnings" in source
    assert "vw-soft-warning" in source
