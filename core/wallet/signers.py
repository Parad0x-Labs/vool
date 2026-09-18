"""Signers: the only objects that can produce a signature, and none of them exposes a key.

* watch-only: no signer exists; asking is a typed refusal;
* external: the message goes OUT to the owner's wallet, a signature comes back, and it is
  verified against the registered public key before anything downstream trusts it;
* pocket: the seed is unsealed with the PIN inside the signing call and dropped afterwards.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from core.vool_wallet import decode_solana_pubkey
from core.wallet import custody
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.signers"


@dataclass(frozen=True)
class SigningRequest:
    """What an external wallet is asked to sign. Public key + bytes; nothing else."""

    proposal_id: str
    public_key: str
    message: bytes
    network: str

    def to_dict(self) -> dict[str, Any]:
        return {"proposal_id": self.proposal_id, "public_key": self.public_key, "message_hex": self.message.hex(), "network": self.network}


ExternalSignatureProvider = Callable[[SigningRequest], bytes]


class Signer(Protocol):
    public_key: str

    def sign(self, message: bytes) -> bytes: ...


def _verify(public_key: str, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(public_key)).verify(bytes(signature), bytes(message))
        return True
    except Exception:
        return False


class ExternalSigner:
    def __init__(self, public_key: str, provider: ExternalSignatureProvider, *, proposal_id: str, network: str) -> None:
        self.public_key = public_key
        self.mode = custody.MODE_EXTERNAL_SIGNER
        self._provider = provider
        self._proposal_id = proposal_id
        self._network = network

    def sign(self, message: bytes) -> bytes:
        signature = bytes(self._provider(SigningRequest(self._proposal_id, self.public_key, bytes(message), self._network)) or b"")
        if not _verify(self.public_key, message, signature):
            raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"proposal_id": self._proposal_id, "reason": "external_signature_mismatch"})
        return signature


class PocketSigner:
    def __init__(self, public_key: str, wallet_id: str, pin: str) -> None:
        self.public_key = public_key
        self.mode = custody.MODE_POCKET_SEALED
        self._wallet_id = wallet_id
        self._pin = pin  # the PIN, never the seed; unsealing happens per signature
        custody._unseal_seed(wallet_id, pin)  # fail fast on a wrong PIN, before any state changes

    def sign(self, message: bytes) -> bytes:
        seed = custody._unseal_seed(self._wallet_id, self._pin)
        try:
            signature = Ed25519PrivateKey.from_private_bytes(seed).sign(bytes(message))
        finally:
            del seed
        if not _verify(self.public_key, message, signature):
            raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"wallet_id": self._wallet_id, "reason": "pocket_signature_mismatch"})
        return signature


class DevicePocketSigner:
    """A pocket wallet whose approval method is the device: the Keychain user-presence item opens the seal at
    construction (the prompt), the secret lives only until the one signature it was released for."""

    def __init__(self, public_key: str, wallet_id: str, proposal_id: str) -> None:
        self.public_key = public_key
        self.mode = custody.MODE_POCKET_SEALED
        self._wallet_id = wallet_id
        self._secret = custody._unseal_signing_secret_with_device(wallet_id, reason=f"VOOL: approve payment {proposal_id[:16]} from wallet {public_key[:6]}…{public_key[-4:]}")

    def sign(self, message: bytes) -> bytes:
        secret = self._secret
        self._secret = b""
        try:
            signature = Ed25519PrivateKey.from_private_bytes(secret).sign(bytes(message))
        finally:
            del secret
        if not _verify(self.public_key, message, signature):
            raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={"wallet_id": self._wallet_id, "reason": "pocket_signature_mismatch"})
        return signature


def _signer_for_mode(profile: custody.WalletProfile, pin: str | None, provider: ExternalSignatureProvider | None, proposal_id: str) -> Signer:
    if profile.mode == custody.MODE_EXTERNAL_SIGNER:
        if provider is None:
            raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"wallet_id": profile.wallet_id, "reason": "external_signer_not_connected"})
        return ExternalSigner(profile.public_key, provider, proposal_id=proposal_id, network=profile.network)
    if profile.mode == custody.MODE_POCKET_SEALED:
        if getattr(profile, "approval_method", custody.APPROVAL_PIN) == custody.APPROVAL_DEVICE:
            return DevicePocketSigner(profile.public_key, profile.wallet_id, proposal_id)
        if pin is None:
            raise wallet_fault("wallet_pin_invalid", authority=AUTHORITY, context={"wallet_id": profile.wallet_id, "reason": "pin_required"})
        return PocketSigner(profile.public_key, profile.wallet_id, str(pin))
    raise wallet_fault("wallet_signing_unavailable", authority=AUTHORITY, context={"wallet_id": profile.wallet_id, "reason": "watch_only"})


def signer_for(profile: custody.WalletProfile, *, pin: str | None = None, external_signature_provider: ExternalSignatureProvider | None = None, proposal_id: str = "") -> Signer:
    """The guarded door. Watch-only never signs; the guard is the module attribute a sabotage must remove."""
    return _signer_for_mode(profile, pin, external_signature_provider, proposal_id)
