"""The SEC vocabulary: one observation per security-relevant fault, derived not duplicated.

The fault catalog DECLares which failure families are security-relevant
(``security_relevant`` on the ``FaultSpec``); this module declares, for each of those,
the observation an operator sees: its stable SEC code, its observation severity, the
class of resource affected, the source that observed it, and a summary that names the
MECHANISM. The vocabulary validates itself against the fault catalog at import: a SEC
entry whose fault is not declared security-relevant (or a security-relevant fault with
no SEC entry) is a construction error, so the two sides cannot drift apart quietly.

THE WORDING LAW. A security event records what was OBSERVED about a mechanism -- a
denial, a verification that failed, a secret that could not be read. It never
characterizes a person or an intent: "a write outside the authorized scope was
refused" is a fact the runtime can prove; "someone attacked the workspace" is an
accusation it cannot. Summaries here are written in the passive, mechanism-naming
voice, and the test pack holds every summary and every export against the banned
accusation vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.faults.catalog import (
    FAULT_CONFINEMENT_REFUSAL,
    FAULT_CREDENTIAL_FAILURE,
    FAULT_EVIDENCE_CORRUPTION,
    FAULT_INTEGRITY_VERIFICATION_FAILURE,
    FAULT_PERMISSION_DENIED,
    get_spec,
)

#: The schema identifier carried by every durable security event and every export.
SEC_SCHEMA = "vool.security_event.v1"

# --- states (the event's own lifecycle; the store enforces the machine) ---
STATE_OBSERVED = "observed"
STATE_ACKNOWLEDGED = "acknowledged"
STATE_RESOLVED = "resolved"

# --- the stable SEC codes. Names are the contract; string values are the wire form. ---
SEC_PERMISSION_DENIED = "SEC_PERMISSION_DENIED"
SEC_CONFINEMENT_REFUSAL = "SEC_CONFINEMENT_REFUSAL"
SEC_CREDENTIAL_FAILURE = "SEC_CREDENTIAL_FAILURE"
SEC_INTEGRITY_VERIFICATION_FAILURE = "SEC_INTEGRITY_VERIFICATION_FAILURE"
SEC_EVIDENCE_CORRUPTION = "SEC_EVIDENCE_CORRUPTION"
SEC_WALLET_NETWORK_DISABLED = "SEC_WALLET_NETWORK_DISABLED"
SEC_WALLET_SIGNATURE_INVALID = "SEC_WALLET_SIGNATURE_INVALID"
SEC_WALLET_LIMIT_EXCEEDED = "SEC_WALLET_LIMIT_EXCEEDED"
SEC_WALLET_DUPLICATE_PAYMENT = "SEC_WALLET_DUPLICATE_PAYMENT"
SEC_WALLET_APPROVAL_REJECTED = "SEC_WALLET_APPROVAL_REJECTED"
SEC_WALLET_X402_CAP_EXCEEDED = "SEC_WALLET_X402_CAP_EXCEEDED"
SEC_WALLET_CARD_DATA_REFUSED = "SEC_WALLET_CARD_DATA_REFUSED"
SEC_WALLET_LEGACY_SURFACE_RETIRED = "SEC_WALLET_LEGACY_SURFACE_RETIRED"
SEC_WALLET_EXPORT_REFUSED = "SEC_WALLET_EXPORT_REFUSED"
SEC_WALLET_CHAIN_IDENTITY_MISMATCH = "SEC_WALLET_CHAIN_IDENTITY_MISMATCH"
SEC_WALLET_OUTBOUND_REFUSED = "SEC_WALLET_OUTBOUND_REFUSED"
SEC_WALLET_ENVIRONMENT_INACTIVE = "SEC_WALLET_ENVIRONMENT_INACTIVE"
SEC_WALLET_CALLER_REFUSED = "SEC_WALLET_CALLER_REFUSED"
SEC_WALLET_UNLOCK_THROTTLED = "SEC_WALLET_UNLOCK_THROTTLED"
SEC_WALLET_BACKUP_UNAVAILABLE = "SEC_WALLET_BACKUP_UNAVAILABLE"
SEC_WALLET_QUOTE_MISMATCH = "SEC_WALLET_QUOTE_MISMATCH"
SEC_WALLET_RECOVERY_REFUSED = "SEC_WALLET_RECOVERY_REFUSED"


class SecurityVocabularyError(Exception):
    """Base class for security-vocabulary violations."""


class UnknownSecurityCodeError(SecurityVocabularyError):
    """A SEC code nobody declared was looked up."""


@dataclass(frozen=True)
class SecSpec:
    """One security observation's contract. ``fault_code`` is the one fault it observes."""

    sec_code: str
    fault_code: str
    #: How much attention the OBSERVATION deserves -- not a judgment about anyone.
    severity: str
    #: The class of thing affected ("workspace_path", "credential", "receipt_chain", ...).
    resource_class: str
    #: The observing authority. Written down where the code can check it.
    source: str
    #: The mechanism-naming, accusation-free summary the event carries.
    summary: str


#: One entry per fault the catalog declares security-relevant -- and no others.
_SEC_SPECS: tuple[SecSpec, ...] = (
    SecSpec(
        sec_code=SEC_PERMISSION_DENIED,
        fault_code=FAULT_PERMISSION_DENIED,
        severity="medium",
        resource_class="action",
        source="core.runtime_execution_tools",
        summary="A requested action was denied by permission policy.",
    ),
    SecSpec(
        sec_code=SEC_CONFINEMENT_REFUSAL,
        fault_code=FAULT_CONFINEMENT_REFUSAL,
        severity="high",
        resource_class="workspace_path",
        source="core.agent_runtime.builder.app_builder",
        summary="A write outside the authorized scope was refused.",
    ),
    SecSpec(
        sec_code=SEC_CREDENTIAL_FAILURE,
        fault_code=FAULT_CREDENTIAL_FAILURE,
        severity="high",
        resource_class="credential",
        source="core.credential_store",
        summary="A stored credential could not be decrypted and was left untouched.",
    ),
    SecSpec(
        sec_code=SEC_INTEGRITY_VERIFICATION_FAILURE,
        fault_code=FAULT_INTEGRITY_VERIFICATION_FAILURE,
        severity="critical",
        resource_class="receipt_chain",
        source="core.contribution_proof",
        summary="A stored receipt failed its tamper-evidence verification.",
    ),
    SecSpec(
        sec_code=SEC_EVIDENCE_CORRUPTION,
        fault_code=FAULT_EVIDENCE_CORRUPTION,
        severity="high",
        resource_class="execution_ledger",
        source="core.execution_truth",
        summary="A turn's execution records failed the independent witness check.",
    ),
)

#: Wallet observations. Mechanism-naming, passive voice: a refusal is recorded, never a verdict on a person.
_WALLET_SEC_SPECS: tuple[SecSpec, ...] = (
    SecSpec(sec_code=SEC_WALLET_NETWORK_DISABLED, fault_code="wallet_network_disabled", severity="high", resource_class="payment",
            source="core.wallet.custody", summary="A payment addressed to a network outside the enabled set was refused."),
    SecSpec(sec_code=SEC_WALLET_SIGNATURE_INVALID, fault_code="wallet_signature_invalid", severity="high", resource_class="payment",
            source="core.wallet.signers", summary="A signature that did not verify against the wallet key was refused before broadcast."),
    SecSpec(sec_code=SEC_WALLET_LIMIT_EXCEEDED, fault_code="wallet_limit_exceeded", severity="medium", resource_class="payment",
            source="core.wallet.limits", summary="A payment above a configured spending limit was refused."),
    SecSpec(sec_code=SEC_WALLET_DUPLICATE_PAYMENT, fault_code="wallet_duplicate_payment", severity="medium", resource_class="payment",
            source="core.wallet.proposals", summary="A repeated payment request was collapsed onto its earlier proposal and not sent again."),
    SecSpec(sec_code=SEC_WALLET_APPROVAL_REJECTED, fault_code="wallet_approval_rejected", severity="medium", resource_class="payment",
            source="core.wallet.lifecycle", summary="An approval that did not bind to its proposal or did not unlock the wallet was refused."),
    SecSpec(sec_code=SEC_WALLET_X402_CAP_EXCEEDED, fault_code="wallet_x402_cap_exceeded", severity="medium", resource_class="payment",
            source="core.wallet.x402", summary="A paid-resource request above the automatic payment cap was refused."),
    SecSpec(sec_code=SEC_WALLET_CARD_DATA_REFUSED, fault_code="wallet_card_data_refused", severity="high", resource_class="payment_card",
            source="core.wallet.cards", summary="Raw card data offered for storage was refused; only provider tokens are kept."),
    SecSpec(sec_code=SEC_WALLET_LEGACY_SURFACE_RETIRED, fault_code="wallet_legacy_surface_retired", severity="high", resource_class="payment",
            source="core.wallet.authority", summary="A retired money surface was called and refused; the canonical wallet lifecycle is the only path."),
    SecSpec(sec_code=SEC_WALLET_EXPORT_REFUSED, fault_code="wallet_export_refused", severity="high", resource_class="wallet_key",
            source="core.wallet.authority", summary="A private-key export was refused; no export door exists."),
    SecSpec(sec_code=SEC_WALLET_CHAIN_IDENTITY_MISMATCH, fault_code="wallet_chain_identity_mismatch", severity="high", resource_class="payment",
            source="core.wallet.chains", summary="A chain endpoint did not prove the network identity it claimed; nothing was signed or sent."),
    SecSpec(sec_code=SEC_WALLET_OUTBOUND_REFUSED, fault_code="wallet_outbound_refused", severity="high", resource_class="payment",
            source="core.wallet.outbound", summary="A request outside the wallet's approved endpoints or targets was refused before any connection."),
    SecSpec(sec_code=SEC_WALLET_ENVIRONMENT_INACTIVE, fault_code="wallet_environment_inactive", severity="medium", resource_class="payment",
            source="core.wallet.environment", summary="A wallet effect on a network outside the active network environment was refused before any key or socket was used."),
    SecSpec(sec_code=SEC_WALLET_CALLER_REFUSED, fault_code="wallet_caller_refused", severity="high", resource_class="payment",
            source="core.wallet.caller_binding", summary="A trusted wallet door was called without same-origin evidence from the app's own page and was refused before any wallet was read."),
    SecSpec(sec_code=SEC_WALLET_UNLOCK_THROTTLED, fault_code="wallet_unlock_throttled", severity="high", resource_class="wallet_key",
            source="core.wallet.pilot_custody", summary="A wallet unlock was refused because too many failed attempts were recorded; no key was opened."),
    SecSpec(sec_code=SEC_WALLET_BACKUP_UNAVAILABLE, fault_code="wallet_backup_unavailable", severity="high", resource_class="wallet_key",
            source="core.wallet.pilot_custody", summary="A second reveal of a wallet backup was refused; the backup is released once during setup."),
    SecSpec(sec_code=SEC_WALLET_QUOTE_MISMATCH, fault_code="wallet_quote_mismatch", severity="high", resource_class="payment",
            source="core.wallet.quotes", summary="An approval that did not match its open transfer quote was refused before any claim, signature or send."),
    SecSpec(sec_code=SEC_WALLET_RECOVERY_REFUSED, fault_code="wallet_recovery_refused", severity="high", resource_class="wallet_key",
            source="core.wallet.pilot_custody", summary="A wallet recovery proof did not fit the wallet (a backup for another address, a malformed or inconsistent key, or a stale device challenge) and was refused before any write."),
)
_SEC_SPECS = (*_SEC_SPECS, *_WALLET_SEC_SPECS)

_BY_SEC_CODE: dict[str, SecSpec] = {}
_BY_FAULT_CODE: dict[str, SecSpec] = {}
for _spec in _SEC_SPECS:
    if _spec.sec_code in _BY_SEC_CODE:
        raise SecurityVocabularyError(f"SEC code declared twice: {_spec.sec_code}")
    _declared = get_spec(_spec.fault_code)
    if not _declared.security_relevant:
        raise SecurityVocabularyError(
            f"{_spec.sec_code} observes {_spec.fault_code}, which the fault catalog "
            "does not declare security-relevant"
        )
    if _spec.fault_code in _BY_FAULT_CODE:
        raise SecurityVocabularyError(f"one fault observed by two SEC codes: {_spec.fault_code}")
    _BY_SEC_CODE[_spec.sec_code] = _spec
    _BY_FAULT_CODE[_spec.fault_code] = _spec

# Completeness: every security-relevant fault must have exactly one observation.
from core.faults.catalog import all_specs as _all_fault_specs

for _fault_spec in _all_fault_specs():
    if _fault_spec.security_relevant and _fault_spec.code not in _BY_FAULT_CODE:
        raise SecurityVocabularyError(
            f"the fault catalog declares {_fault_spec.code} security-relevant but no SEC entry observes it"
        )


def get_sec_spec(code: str) -> SecSpec:
    """The observation contract for one SEC code. Undeclared codes do not exist."""
    spec = _BY_SEC_CODE.get(str(code or "").strip())
    if spec is None:
        raise UnknownSecurityCodeError(f"no such security-event code: {code!r}")
    return spec


def sec_spec_for_fault_code(fault_code: str) -> SecSpec:
    """The observation that matches one fault code; non-security faults have none."""
    spec = _BY_FAULT_CODE.get(str(fault_code or "").strip())
    if spec is None:
        raise SecurityVocabularyError(
            f"no security observation exists for fault {fault_code!r} (it is not security-relevant)"
        )
    return spec


def all_sec_codes() -> tuple[str, ...]:
    """Every declared SEC code, sorted."""
    return tuple(sorted(_BY_SEC_CODE))


__all__ = [
    "SEC_CONFINEMENT_REFUSAL",
    "SEC_CREDENTIAL_FAILURE",
    "SEC_EVIDENCE_CORRUPTION",
    "SEC_INTEGRITY_VERIFICATION_FAILURE",
    "SEC_PERMISSION_DENIED",
    "SEC_SCHEMA",
    "STATE_ACKNOWLEDGED",
    "STATE_OBSERVED",
    "STATE_RESOLVED",
    "SecSpec",
    "SecurityVocabularyError",
    "UnknownSecurityCodeError",
    "all_sec_codes",
    "get_sec_spec",
    "sec_spec_for_fault_code",
]
