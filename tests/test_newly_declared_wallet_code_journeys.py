"""The six wallet codes declared for v0.8, proven end to end at their real seams.

Before the catalog declared them, ``wallet_password_invalid`` and its five siblings were
emitted by core/wallet but refused by the closed catalog: ``wallet_fault`` swallowed the
``UnknownFaultCodeError`` and returned a WalletFault with an EMPTY user_message and NO
fault record, so the wallet API served a bare code with ``"message": ""``, nothing was
filed, no security observation opened, and the error reference had no entry. These
journeys prove the repaired contract through the real helper, the real recorder, the
security plane and the real API renderer, and pin the recovery and documentation laws.
"""
from __future__ import annotations

import json
import pathlib

from core.faults.catalog import all_codes, doc_anchor, get_spec
from core.faults.recorder import list_faults
from core.wallet.security import wallet_fault

NEWLY_DECLARED = (
    "wallet_password_invalid",
    "wallet_device_auth_unavailable",
    "wallet_device_auth_denied",
    "wallet_export_unavailable",
    "wallet_export_pin_not_accepted",
    "wallet_acknowledgement_required",
)

BOOK = pathlib.Path(__file__).resolve().parents[1] / "docs" / "ERROR_BOOK.md"


def test_the_formerly_bare_codes_now_carry_a_full_served_contract() -> None:
    """The exact regression: through wallet_fault, each code yields a non-empty safe
    message, a filed fault id, and a durable record whose wording is the catalog's."""
    for code in NEWLY_DECLARED:
        fault = wallet_fault(
            code,
            authority="core.wallet.custody",
            context={"wallet_id": "w1", "reason": "test fixture"},
            source_context={"session_id": "sess-newly-declared"},
        )
        assert code in all_codes()
        assert fault.user_message, f"{code}: served message is empty"
        assert fault.fault_id, f"{code}: no fault record was filed"
        assert fault.user_message == get_spec(code).user_message

        recorded = [row for row in list_faults(code=code, limit=5) if row.fault_id == fault.fault_id]
        assert recorded, f"{code}: the fault id is not readable back from the store"
        assert recorded[0].user_message == fault.user_message
        assert recorded[0].code == code


def test_the_security_relevant_codes_open_their_observation() -> None:
    """Export refusals are security-relevant: filing the fault opens its SEC observation."""
    from core.security_events.store import list_security_events

    for code in ("wallet_export_unavailable", "wallet_export_pin_not_accepted"):
        assert get_spec(code).security_relevant
        fault = wallet_fault(code, authority="core.wallet.custody", context={"wallet_id": "w1"},
                             source_context={"session_id": "sess-sec"})
        events = list_security_events(limit=50)
        assert any(fault.fault_id in tuple(getattr(e, "evidence_refs", ()) or ()) or
                   fault.fault_id in json.dumps(getattr(e, "__dict__", {}), default=str)
                   for e in events), f"{code}: no security observation references the fault"


def test_the_api_renderer_serves_the_catalog_sentence() -> None:
    """The wallet API's _fault() renders code + message + fault payload with the mapped
    HTTP status; the message is the catalog's sentence, never an empty string."""
    from core.wallet.errors import WalletFault
    from core.web.api.wallet_api import _STATUS_FOR_CODE, _fault

    for code in NEWLY_DECLARED:
        assert code in _STATUS_FOR_CODE, f"{code}: no HTTP status mapping"
        exc = WalletFault(code, context={"reason": "fixture"}, fault_id="fault-abc123",
                          message=get_spec(code).user_message)
        response = _fault(exc)
        body = getattr(response, "body", b"")
        served = json.loads(body if isinstance(body, (bytes, str)) else b"{}")
        assert response.status == _STATUS_FOR_CODE[code]
        assert served["error"] == code
        assert served["message"] == get_spec(code).user_message
        assert served["fault"]["code"] == code
        assert served["fault"]["fault_id"] == "fault-abc123"


def test_every_new_code_contract_never_claims_an_external_effect() -> None:
    """All six stop before anything is unlocked, revealed, signed or sent; the effect
    state must say so. A wrong credential, a declined prompt or a missing acknowledgement
    is change-then-retry; a wallet that structurally holds no exportable key here is
    never retryable as-is (the recovery is a different wallet or the setup backup)."""
    intended_retry = {
        "wallet_password_invalid": "retry_after_change",
        "wallet_device_auth_unavailable": "retry_after_change",
        "wallet_device_auth_denied": "retry_after_change",
        "wallet_export_unavailable": "never",
        "wallet_export_pin_not_accepted": "retry_after_change",
        "wallet_acknowledgement_required": "retry_after_change",
    }
    for code in NEWLY_DECLARED:
        spec = get_spec(code)
        assert spec.effect == "none", f"{code}: declared effect drifted from its boundary"
        assert spec.retry == intended_retry[code], f"{code}: retry policy drifted"
        assert spec.title.strip(), f"{code}: no title"


def test_every_new_code_has_its_section_in_the_error_book() -> None:
    """The generated error reference has a per-code section headed by the code, and the
    category table links to it by the anchor the docs site gives that heading."""
    text = BOOK.read_text(encoding="utf-8")
    for code in NEWLY_DECLARED:
        assert text.splitlines().count(f"### {code}") == 1, f"{code}: no per-code section"
        assert f"(#{doc_anchor(code)})" in text, f"{code}: the summary table does not link its section"
        assert get_spec(code).user_message in text


def test_recovery_actions_never_bypass_authority() -> None:
    """Each operator action points at a deliberate human action (choose a PIN, use device
    unlock, acknowledge exactly), never an automatic grant, unlock or reveal."""
    for code in NEWLY_DECLARED:
        action = get_spec(code).operator_action.lower()
        assert not any(
            phrase in action for phrase in ("automatically", "bypass", "without approval", "force")
        ), f"{code}: operator action suggests an authority bypass: {get_spec(code).operator_action}"
