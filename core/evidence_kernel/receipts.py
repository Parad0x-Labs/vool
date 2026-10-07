"""Receipt envelope v2 (v14.2 evidence kernel, docs/ARCHITECTURE-v14.2.md section "Contracts").

One envelope shape for every receipt kind. The envelope is an index and a proof, never the evidence payload: it
carries references (occurrence id, span, digest) and digests, so a chat turn never produces a large signed object
and the exact text lives once, in the occurrence store. Kinds in this build:

  vool.memory.turn.v1    one per stored occurrence: the memory receipt's facts digest and the occurrence id
  vool.memory.packet.v1  one per memory answer: obligation, completeness, the facts delivered (refs), packet digest
  vool.memory.claim.v1   one per guarded memory answer: the claim bindings (values, status, evidence refs)
  vool.honesty.v1        the existing HonestyReceipt, viewed through the adapter below (v1 stays verifiable as is)

Differences from the honesty v1 receipt, on purpose: the receipt id is INSIDE the signed body; evidence is a list of
references with digests, not a reduced pointer; every ledger has a head file (a checkpoint) so a deleted tail is
detectable; a write that fails raises to the caller when the caller asked for commit semantics. Assurance levels:
L1 hashed (always), L2 chained (always, per ledger), L3 signed (when the node's signer is available), L4
checkpointed (the head file). Signing is never required for an internal memory lookup.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "vool.receipt.envelope.v2"
_LOCK = threading.RLock()


def _sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class EvidenceRef:
    occurrence_id: str
    digest: str = ""          # sha256 of the exact evidence text (or of the span)
    span: str = ""            # "start-end" character offsets inside the occurrence body, or "" for the whole turn
    role: str = ""            # user | assistant
    kind: str = ""            # fact value kind, or "turn"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReceiptEnvelopeV2:
    receipt_id: str
    kind: str
    issued_at: float
    request_id: str
    session_id: str
    turn_id: str
    parents: list[str]
    subject_type: str
    subject_digest: str
    evidence_refs: list[dict[str, Any]]
    status: str
    reason_codes: list[str]
    previous_digest: str
    content_digest: str
    signer_peer_id: str = ""
    signature: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def assurance(self) -> str:
        return "L3" if self.signature else ("L2" if self.previous_digest or self.content_digest else "L1")


def signed_body(d: Mapping[str, Any]) -> dict[str, Any]:
    """Everything the digest and the signature cover: the id included, the digest and signature excluded."""
    return {k: d[k] for k in ("receipt_id", "kind", "issued_at", "request_id", "session_id", "turn_id", "parents", "subject_type",
                              "subject_digest", "evidence_refs", "status", "reason_codes", "previous_digest", "metadata", "schema")}


def _ledger_dir() -> Path:
    home = os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME") or str(Path.home() / ".vool")
    return Path(home) / "data" / "receipts_v2"


def ledger_path(kind: str, session_id: str) -> Path:
    return _ledger_dir() / kind / (_sha256(str(session_id or ""))[:24] + ".jsonl")


def head_path(kind: str, session_id: str) -> Path:
    return ledger_path(kind, session_id).with_suffix(".head.json")


def latest_digest(kind: str, session_id: str) -> str:
    p = head_path(kind, session_id)
    try:
        return str(json.loads(p.read_text()).get("content_digest") or "")
    except Exception:
        return ""


ENV_NAMES = ("VOOL_EVIDENCE_KERNEL", "NULLA_EVIDENCE_KERNEL")


def kernel_enabled(env: Mapping[str, str] | None = None) -> bool:
    """VOOL_EVIDENCE_KERNEL=1 (NULLA_ honoured): envelopes v2 and commit semantics on the occurrence write.
    Off: no ledger is touched and the write path behaves exactly as v14.1 (off-equivalence test)."""
    env = os.environ if env is None else env
    for name in ENV_NAMES:
        value = str(env.get(name, "") or "").strip().lower()
        if value:
            return value in ("1", "true", "yes", "on")
    return False


def _try_sign(content: bytes) -> tuple[str, str]:
    """The node's local Ed25519 identity (network.signer), the same key the honesty v1 receipts use."""
    try:
        from network import signer

        return str(signer.get_local_peer_id() or ""), str(signer.sign(content) or "")
    except Exception:
        return "", ""


def issue(*, kind: str, session_id: str, subject_type: str, subject: Any, evidence_refs: Sequence[EvidenceRef | Mapping[str, Any]] = (),
          status: str = "recorded", reason_codes: Sequence[str] = (), request_id: str = "", turn_id: str = "", parents: Sequence[str] = (),
          metadata: Mapping[str, Any] | None = None, commit: bool = True, sign: bool = False) -> ReceiptEnvelopeV2:
    """Issue and append one envelope to its (kind, session) ledger, chained to that ledger's head.

    commit=True: a failed append RAISES (the caller must not treat the subject as recorded); commit=False keeps
    best-effort semantics and returns the envelope with status "unwritten" on failure."""
    refs = [r.as_dict() if isinstance(r, EvidenceRef) else dict(r) for r in evidence_refs]
    subject_digest = _sha256(_canonical(subject)) if not isinstance(subject, str) else _sha256(subject)
    body = {"receipt_id": "r2-" + uuid.uuid4().hex, "kind": str(kind), "issued_at": time.time(), "request_id": str(request_id or ""),
            "session_id": str(session_id or ""), "turn_id": str(turn_id or ""), "parents": [str(p) for p in parents], "subject_type": str(subject_type),
            "subject_digest": subject_digest, "evidence_refs": refs, "status": str(status), "reason_codes": [str(c) for c in reason_codes],
            "previous_digest": "", "metadata": dict(metadata or {}), "schema": SCHEMA}
    with _LOCK:
        body["previous_digest"] = latest_digest(kind, session_id)
        content = _canonical(signed_body(body))
        digest = _sha256(content)
        peer, sig = _try_sign(content) if sign else ("", "")
        env = ReceiptEnvelopeV2(**body, content_digest=digest, signer_peer_id=peer, signature=sig)
        try:
            p = ledger_path(kind, session_id); p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(env.to_dict(), ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
            hp = head_path(kind, session_id); tmp = hp.with_suffix(".tmp")
            tmp.write_text(json.dumps({"content_digest": digest, "receipt_id": env.receipt_id, "count": _count(p), "issued_at": env.issued_at}))
            os.replace(tmp, hp)
        except Exception:
            if commit:
                raise
            return ReceiptEnvelopeV2(**{**body, "status": "unwritten"}, content_digest=digest, signer_peer_id=peer, signature=sig)
    return env


def _count(p: Path) -> int:
    try:
        with p.open("rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def read_ledger(kind: str, session_id: str) -> list[dict[str, Any]]:
    p = ledger_path(kind, session_id)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"corrupt": True})
    return out


def verify(envelope: Mapping[str, Any]) -> tuple[bool, str]:
    """Content digest over the signed body (id included); the signature when present."""
    try:
        expected = _sha256(_canonical(signed_body(envelope)))
    except Exception as exc:
        return False, "malformed:" + type(exc).__name__
    if expected != str(envelope.get("content_digest") or ""):
        return False, "content_digest_mismatch"
    if envelope.get("signature"):
        try:
            from network import signer

            if not signer.verify(_canonical(signed_body(envelope)), str(envelope["signature"]), str(envelope.get("signer_peer_id") or "")):
                return False, "bad_signature"
            return True, "ok_signed"
        except Exception:
            return True, "ok_unverified_signature"
    return True, "ok"


def verify_chain(kind: str, session_id: str) -> tuple[bool, str]:
    """Every envelope valid, each chained to the previous digest, and the head file naming the last one (a deleted
    tail is detected because the head no longer matches)."""
    rows = read_ledger(kind, session_id)
    prev = ""
    for i, r in enumerate(rows):
        ok, why = verify(r)
        if not ok:
            return False, f"envelope[{i}] {why}"
        if str(r.get("previous_digest") or "") != prev:
            return False, f"envelope[{i}] broken_chain"
        prev = str(r.get("content_digest") or "")
    head = latest_digest(kind, session_id)
    if rows and head != prev:
        return False, "head_mismatch (tail deleted or head stale)"
    if not rows and head:
        return False, "ledger_missing_but_head_present"
    return True, f"ok ({len(rows)} envelopes)"


def from_honesty_v1(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """View an existing HonestyReceipt (v1, unchanged on disk) as an envelope v2 dict. The v1 record stays the
    authority and verifies with its own functions; this adapter only re-shapes it for readers of the envelope."""
    tools = list(receipt.get("executed_tools") or [])
    return {"receipt_id": str(receipt.get("receipt_id") or ""), "kind": "vool.honesty.v1", "issued_at": receipt.get("issued_at"),
            "request_id": "", "session_id": str(receipt.get("session_id") or ""), "turn_id": str(receipt.get("turn_index", "")),
            "parents": [], "subject_type": "turn", "subject_digest": str(receipt.get("response_hash") or ""),
            "evidence_refs": [{"occurrence_id": str(t.get("receipt_key") or ""), "digest": "", "span": "", "role": "tool", "kind": str(t.get("tool") or "")} for t in tools],
            "status": str(receipt.get("verdict") or ""), "reason_codes": [str(receipt.get("verdict_detail") or "")] if receipt.get("verdict_detail") else [],
            "previous_digest": str(receipt.get("prev_hash") or ""), "content_digest": str(receipt.get("content_hash") or ""),
            "signer_peer_id": str(receipt.get("signer_peer_id") or ""), "signature": str(receipt.get("signature") or ""),
            "metadata": {"v1_schema": str(receipt.get("schema") or ""), "prompt_hash": str(receipt.get("prompt_hash") or ""), "claimed_actions": list(receipt.get("claimed_actions") or [])},
            "schema": SCHEMA, "adapter": "honesty_v1->envelope_v2; verify with core.honesty_receipt.verify_honesty_receipt"}
