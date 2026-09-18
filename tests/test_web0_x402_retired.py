"""§3 / blind-sign harden: the server-built-transaction x402 pay path is retired, and the
read-only x402 helpers fail closed with no default Parad0x endpoint."""
from __future__ import annotations

import core.web0_tools as w


def test_blind_sign_pay_path_is_retired():
    # dna_pay_and_unlock previously signed a server-built transaction blindly; it now refuses.
    out = w.dna_pay_and_unlock("https://example.com/paid", object(), allow_spend=True)
    assert out["error"] == "blind_sign_path_retired"


def test_pay_still_requires_allow_spend():
    out = w.dna_pay_and_unlock("https://example.com/paid", object())
    assert out["error"] == "spend_requires_explicit_allow_spend"


def test_no_default_parad0x_endpoint():
    # No hard-coded default: an unconfigured deploy contacts no Parad0x server.
    assert w.DNA_X402_URL == "" or "parad0xlabs.com" not in w.DNA_X402_URL


def test_quote_and_builder_fail_closed_without_endpoint(monkeypatch):
    monkeypatch.setattr(w, "DNA_X402_URL", "")
    assert w.dna_get_quote("https://example.com/paid")["error"] == "x402_endpoint_not_configured"
    assert w.dna_create_builder_draft("hi")["error"] == "x402_endpoint_not_configured"
