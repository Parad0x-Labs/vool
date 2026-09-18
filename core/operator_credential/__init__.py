"""The operator credential: the PIN or password a person sets to protect changes to saved contacts."""
from __future__ import annotations

from core.operator_credential.authority import (
    KIND_PASSWORD,
    KIND_PIN,
    PRINCIPAL,
    SCOPE_CONTACTS,
    SETUP_PATH,
    CredentialError,
    Verification,
    change,
    current_generation,
    enroll,
    reset_with_recovery,
    status,
    verify,
)

__all__ = [
    "KIND_PASSWORD", "KIND_PIN", "PRINCIPAL", "SCOPE_CONTACTS", "SETUP_PATH", "CredentialError", "Verification", "change", "current_generation",
    "enroll", "reset_with_recovery", "status", "verify",
]
