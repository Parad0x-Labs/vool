from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.proof_of_execution import ProofReceipt, create_proof_receipt
from core.x402.client import X402Client, X402Config, X402Mode, X402Receipt


@dataclass(frozen=True)
class Web0WorkReceipt:
    """
    Single receipt binding a completed task to its payment proof and optional
    ZK attestation.

    zk_proof is None by default — the Dark-Null-Protocol slot is intentionally
    left empty until the privacy layer is active. Nothing else changes when it
    gets filled in.
    """
    receipt_id: str
    task_id: str
    worker_id: str
    result_hash: str
    proof: ProofReceipt
    payment: X402Receipt
    zk_proof: str | None
    issued_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id":  self.receipt_id,
            "task_id":     self.task_id,
            "worker_id":   self.worker_id,
            "result_hash": self.result_hash,
            "proof": {
                "receipt_id":     self.proof.receipt_id,
                "task_id":        self.proof.task_id,
                "helper_peer_id": self.proof.helper_peer_id,
                "result_hash":    self.proof.result_hash,
                "started_at":     self.proof.started_at,
                "finished_at":    self.proof.finished_at,
                "proof_hash":     self.proof.proof_hash,
                "signer_peer_id": self.proof.signer_peer_id,
                "signature":      self.proof.signature,
            },
            "payment":   self.payment.to_dict(),
            "zk_proof":  self.zk_proof,
            "issued_at": self.issued_at,
        }


def issue_work_receipt(
    *,
    task_id: str,
    result: str | bytes,
    worker_id: str,
    x402_client: X402Client | None = None,
    amount_usdc: float = 0.0,
    recipient_wallet: str = "stub-wallet",
    started_at: str | None = None,
    finished_at: str | None = None,
    zk_proof_fn: Callable[[str], str] | None = None,
) -> Web0WorkReceipt:
    """
    Issue a Web0WorkReceipt for a completed task.

    Parameters
    ----------
    task_id         — Web0 task identifier
    result          — raw result payload (str or bytes)
    worker_id       — VOOL node/agent ID
    x402_client     — X402Client for payment; defaults to stub mode
    amount_usdc     — USDC to record; 0.0 = free task (stub receipt issued)
    recipient_wallet— worker's Solana wallet (base58)
    started_at      — ISO-8601 start timestamp; auto-generated if None
    finished_at     — ISO-8601 finish timestamp; auto-generated if None
    zk_proof_fn     — Dark-Null-Protocol hook: (result_hash: str) -> str.
                      Pass None (default) — slot stays empty until privacy
                      layer is active.
    """
    now = time.time()
    _started  = started_at  or _iso(now - 0.001)
    _finished = finished_at or _iso(now)
    result_hash = hashlib.sha256(
        result if isinstance(result, bytes) else str(result).encode()
    ).hexdigest()
    receipt_id = f"wr-{uuid.uuid4().hex}"

    proof = create_proof_receipt(
        receipt_id=receipt_id,
        task_id=task_id,
        helper_peer_id=worker_id,
        result_hash=result_hash,
        started_at=_started,
        finished_at=_finished,
    )

    client = x402_client or X402Client(X402Config(mode=X402Mode.STUB))
    if amount_usdc > 0:
        payment = client.pay(
            amount_usdc=amount_usdc,
            recipient_wallet=recipient_wallet,
            session_id=task_id,
        )
    else:
        payment = X402Receipt(
            session_id=task_id,
            payment_tx=f"nopay-{uuid.uuid4().hex[:8]}",
            amount_usdc=0.0,
            recipient_wallet=recipient_wallet,
            facilitator_sig="no-payment",
            timestamp=now,
            mode="stub",
        )

    zk_proof: str | None = None
    if zk_proof_fn is not None:
        try:
            zk_proof = zk_proof_fn(result_hash)
        except Exception:
            zk_proof = None

    return Web0WorkReceipt(
        receipt_id=receipt_id,
        task_id=task_id,
        worker_id=worker_id,
        result_hash=result_hash,
        proof=proof,
        payment=payment,
        zk_proof=zk_proof,
        issued_at=now,
    )


def _iso(ts: float) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).isoformat() + "Z"


__all__ = [
    "Web0WorkReceipt",
    "issue_work_receipt",
]
