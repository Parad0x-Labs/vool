"""The external-signer transport contract.

VOOL builds the exact transaction bytes and hands out ONE signing request bound to one proposal.
The wallet signs outside this process -- Phantom's injected provider via
``request({method: 'signTransaction', params: {message: <base58>}})`` (returns a base58
signature), or a WalletConnect relay via ``solana_signTransaction`` with the base64 transaction
(returns a signature, optionally the signed transaction). Both answers land on one submit door,
which accepts nothing but a signature that verifies over the stored message under the wallet's
registered key; a submitted transaction whose message differs by one byte is refused. A request
is consumed exactly once (compare-and-set), so a replayed answer cannot broadcast twice, and it
expires. Models and plugins never see this module: it is reached only from the owner-local API.
"""
from __future__ import annotations

import base64
import time
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.vool_wallet import b58decode, b58encode, decode_solana_pubkey
from core.wallet.security import wallet_fault
from core.wallet.store import connection

AUTHORITY = "core.wallet.external_signing"
STATE_OPEN = "open"
STATE_CONSUMED = "consumed"
STATE_EXPIRED = "expired"
REQUEST_TTL_SECONDS = 600.0
TRANSPORT_PHANTOM = "phantom_injected"
TRANSPORT_WALLETCONNECT = "walletconnect"
TRANSPORT_EIP1193 = "eip1193"

_COLS = "request_id, proposal_id, wallet_id, public_key, network, message_b64, unsigned_tx_b64, state, created_at, expires_at, consumed_at, family, typed_data_b64"

FAMILY_SVM = "svm"
FAMILY_EVM = "evm"


def _now() -> float:
    return time.time()


def _row(row: Any) -> dict[str, Any]:
    return {
        "request_id": row[0], "proposal_id": row[1], "wallet_id": row[2], "public_key": row[3], "network": row[4], "message_b64": row[5],
        "unsigned_tx_b64": row[6], "state": row[7], "created_at": float(row[8]), "expires_at": float(row[9]), "consumed_at": float(row[10] or 0),
        "family": row[11] or FAMILY_SVM, "typed_data_b64": row[12] or "",
    }


def open_signing_request(proposal: Any, *, public_key: str, message: bytes, unsigned_transaction: bytes, family: str = FAMILY_SVM, typed_data_b64: str = "") -> dict[str, Any]:
    now = _now()
    record = {
        "request_id": f"sreq-{uuid.uuid4().hex[:20]}", "proposal_id": proposal.proposal_id, "wallet_id": proposal.wallet_id, "public_key": str(public_key),
        "network": proposal.network, "message_b64": base64.b64encode(bytes(message)).decode("ascii"), "unsigned_tx_b64": base64.b64encode(bytes(unsigned_transaction)).decode("ascii"),
        "state": STATE_OPEN, "created_at": now, "expires_at": now + REQUEST_TTL_SECONDS, "consumed_at": 0.0,
        "family": str(family), "typed_data_b64": str(typed_data_b64),
    }
    with connection() as conn:
        conn.execute(
            f"INSERT INTO wallet_signing_requests ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            tuple(record[k] for k in _COLS.split(", ")),
        )
    return record


def get_signing_request(request_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM wallet_signing_requests WHERE request_id = ?", (str(request_id or ""),)).fetchone()
    return _row(row) if row else None


def open_request_for_proposal(proposal_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(f"SELECT {_COLS} FROM wallet_signing_requests WHERE proposal_id = ? AND state = ? ORDER BY created_at DESC LIMIT 1", (str(proposal_id), STATE_OPEN)).fetchone()
    return _row(row) if row else None


def request_meaning(record: dict[str, Any]) -> dict[str, Any]:
    """What signing THESE bytes does, checked against the proposal the user was shown; red when they differ."""
    from core.wallet import meaning

    try:
        from core.wallet import proposals

        proposal = proposals.get_proposal(str(record.get("proposal_id") or ""))
        if proposal is None:
            return meaning.cannot_explain("the proposal behind this signing request is unknown").to_dict()
        try:
            from core.wallet import x402

            binding = x402.binding_for_proposal(proposal.proposal_id)
        except Exception:
            binding = None
        return meaning.describe_signing_request(record, proposal, binding=binding).to_dict()
    except Exception as exc:
        return meaning.cannot_explain(f"the signing request could not be described ({type(exc).__name__})").to_dict()


def request_view(record: dict[str, Any]) -> dict[str, Any]:
    """What crosses the wire to the page or relay: bytes and identity, never key material."""
    base = {
        "request_id": record["request_id"], "proposal_id": record["proposal_id"], "wallet_id": record["wallet_id"], "public_key": record["public_key"],
        "network": record["network"], "message_b64": record["message_b64"], "unsigned_transaction_b64": record["unsigned_tx_b64"],
        "expires_at": record["expires_at"], "state": record["state"], "family": record.get("family", FAMILY_SVM),
        "meaning": request_meaning(record),
    }
    if record.get("family") == FAMILY_EVM:
        typed_json = base64.b64decode(record["typed_data_b64"]).decode("utf-8") if record.get("typed_data_b64") else ""
        base["transports"] = {
            TRANSPORT_EIP1193: {"method": "eth_signTypedData_v4", "params": [record["public_key"], typed_json]},
        }
        return base
    message = base64.b64decode(record["message_b64"])
    base["message_b58"] = b58encode(message)
    base["transports"] = {
        TRANSPORT_PHANTOM: {"method": "signTransaction", "params": {"message": base["message_b58"]}},
        TRANSPORT_WALLETCONNECT: {"method": "solana_signTransaction", "params": {"transaction": record["unsigned_tx_b64"]}},
    }
    return base


def is_expired(record: dict[str, Any]) -> bool:
    return _now() > float(record.get("expires_at") or 0)


def expire_signing_request(request_id: str) -> bool:
    """Compare-and-set open -> expired. Returns True only when THIS call won the
    transition; False means the request was already consumed or expired (a consumed
    request is a submission in flight — the loser must not treat it as expired)."""
    with connection() as conn:
        cursor = conn.execute("UPDATE wallet_signing_requests SET state = ? WHERE request_id = ? AND state = ?", (STATE_EXPIRED, str(request_id), STATE_OPEN))
        return cursor.rowcount == 1


def _signature_verifies(public_key: str, message: bytes, signature: bytes) -> bool:
    """The fence: Ed25519 over the EXACT stored message under the registered key."""
    try:
        Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(public_key)).verify(bytes(signature), bytes(message))
        return True
    except Exception:
        return False


def verify_submission(record: dict[str, Any], *, signature_b58: str = "", signed_transaction_b64: str = "") -> bytes:
    """Return the 64-byte signature if, and only if, it is a valid answer to this exact request."""
    message = base64.b64decode(record["message_b64"])
    context = {"proposal_id": record["proposal_id"], "wallet_id": record["wallet_id"]}
    signature = b""
    if str(signed_transaction_b64 or "").strip():
        try:
            from solders.transaction import Transaction

            transaction = Transaction.from_bytes(base64.b64decode(str(signed_transaction_b64).strip()))
        except Exception:
            raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={**context, "reason": "transaction_undecodable"}) from None
        if bytes(transaction.message) != message:
            raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={**context, "reason": "transaction_bytes_mismatch"})
        signature = bytes(next(iter(transaction.signatures), b""))
    elif str(signature_b58 or "").strip():
        try:
            signature = b58decode(str(signature_b58).strip())
        except Exception:
            raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={**context, "reason": "signature_undecodable"}) from None
    else:
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={**context, "reason": "signature_missing"})
    if len(signature) != 64:
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={**context, "reason": "signature_length"})
    if not _signature_verifies(record["public_key"], message, signature):
        raise wallet_fault("wallet_signature_invalid", authority=AUTHORITY, context={**context, "reason": "signature_mismatch"})
    return signature


def consume_signing_request(request_id: str) -> dict[str, Any]:
    """Compare-and-set open -> consumed. The second caller loses, typed."""
    with connection() as conn:
        cursor = conn.execute("UPDATE wallet_signing_requests SET state = ?, consumed_at = ? WHERE request_id = ? AND state = ?", (STATE_CONSUMED, _now(), str(request_id), STATE_OPEN))
        if cursor.rowcount != 1:
            raise wallet_fault("wallet_duplicate_payment", authority=AUTHORITY, context={"reason": "signing_request_already_consumed", "request_id": str(request_id)})
    record = get_signing_request(request_id)
    return record or {}
