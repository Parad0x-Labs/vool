"""Emitted fault codes must be declared: the catalog is closed, emission is not a license.

``wallet_fault`` and the wallet's ``_fault`` helpers build ``FaultRecord.for_code`` from
string literals at their call sites. An undeclared code raises ``UnknownFaultCodeError``
inside those helpers, the helper swallows it, and the user is served a bare code with an
empty message and NO fault record, NO security observation and NO documentation entry:
the failure silently vanishes from every plane that exists to carry it. That is exactly
what happened to six wallet codes before v0.8 declared them.

The extraction is the one ``tools/check_error_reference.py`` runs (every module of the
wallet package plus its HTTP door), so the command and this test cannot disagree.
"""
from __future__ import annotations

from core.faults.catalog import all_codes, get_spec
from tools.check_error_reference import emitted_wallet_codes


def test_the_extraction_sees_the_wallet_emission_surface() -> None:
    emitted = emitted_wallet_codes()
    assert len(emitted) >= 40, f"the extraction found only {len(emitted)} wallet codes; the pattern is stale"
    for code in ("wallet_pin_invalid", "wallet_password_invalid", "wallet_export_unavailable"):
        assert code in emitted, f"{code}: a known emission site was not extracted"


def test_every_emitted_wallet_code_is_declared_in_the_catalog() -> None:
    emitted = emitted_wallet_codes()
    undeclared = sorted(set(emitted) - set(all_codes()))
    assert not undeclared, (
        "wallet modules emit codes the catalog does not declare (the user sees a bare code "
        f"and no fault record is filed): { {code: emitted[code] for code in undeclared} }"
    )


def test_every_declared_wallet_code_is_actually_emitted() -> None:
    """The inverse law: a declared-but-never-emitted wallet code is dead vocabulary, a
    documented failure that cannot happen. Retiring one is an owner decision made visibly."""
    emitted = set(emitted_wallet_codes())
    wallet_codes = {code for code in all_codes() if code.startswith(("wallet_", "evm_", "x402_"))}
    assert not sorted(wallet_codes - emitted), (
        f"declared wallet codes with no emission site: {sorted(wallet_codes - emitted)}"
    )


def test_every_emitted_wallet_code_carries_its_full_public_contract() -> None:
    for code in sorted(emitted_wallet_codes()):
        spec = get_spec(code)
        assert spec.title, f"{code}: no short title"
        assert spec.user_message, f"{code}: no user message"
        assert spec.operator_action, f"{code}: no operator action"
        assert spec.effect, f"{code}: no effect state"
