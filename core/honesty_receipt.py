"""Signed Honesty Receipts - tamper-evident, offline-verifiable proof of honest agent work.

Every agent turn can emit a receipt that binds, for that turn:
  - what the model CLAIMED it did (action kinds: files written, funds sent, message posted, ...),
  - what tools ACTUALLY ran (ground truth from the runtime tool-receipt store),
  - the deterministic honesty verdict (clean / no_action_claimed / blocked_false_claim),
  - hashes of the prompt and response (content stays private; only hashes are recorded),
  - a hash-chain link to the previous receipt in the session (tamper-evident ledger),
  - an Ed25519 signature over the canonical content, from the node's local key.

A third party can re-verify a receipt (and the whole session chain) fully offline with only
the public peer id - no network, no cloud, no trusted server. This is the honest, shippable
form of "verifiable AI": it proves what the agent DID and whether it lied about it, not a
zero-knowledge proof of what the model computed (which is not deliverable on consumer GPUs).

The module is pure/deterministic except for signing (local key) and the optional JSONL ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCHEMA = "vool.honesty_receipt.v1"

# Verdicts.
VERDICT_CLEAN = "clean"  # model claimed actions and every claim is backed by a real execution
VERDICT_NO_CLAIM = "no_action_claimed"  # nothing side-effecting was claimed; nothing to back
VERDICT_BLOCKED = "blocked_false_claim"  # a fabricated action claim was caught and blocked
# A claim with no execution behind it that ALSO was not blocked -- the guard did not reach this
# turn, or reached it and let the wording through. Three verdicts could not express that, so the
# worst state in the system was recorded as the most innocent one: the fabricated build of
# 2026-07-29 was signed `no_action_claimed`. An unbacked claim is now its own fact in the ledger.
VERDICT_UNBACKED = "unbacked_claim"
# The reply asserted a runtime-observable fact that the durable trace contradicts -- a denial of an
# action Activity recorded, or a provider attestation the runtime never carried -- and the evidence
# binder repaired it. Distinct from `blocked_false_claim`, which is a claim with NOTHING behind it:
# here there IS a record and the answer said the opposite of it, which is the worse failure and the
# one the ledger previously could not express. Folding it into `clean` (a receipt existed!) or into
# `no_action_claimed` (no positive claim was made!) would file the contradiction as the quietest
# state in the system, which is exactly how the GitHub denial went unrecorded.
VERDICT_CONTRADICTED = "evidence_contradicted"


@dataclass(frozen=True)
class HonestyReceipt:
    receipt_id: str
    session_id: str
    turn_index: int
    issued_at: float
    prev_hash: str
    prompt_hash: str
    response_hash: str
    claimed_actions: list[str]
    executed_tools: list[dict[str, Any]]
    verdict: str
    verdict_detail: str
    content_hash: str
    signer_peer_id: str
    signature: str
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode("utf-8")).hexdigest()


def _keyed_payload_digest(text: str) -> str:
    """PASS003 (CE08): prompt/response digests stored AT REST are KEYED
    (HMAC-SHA256 under the server-local A8 key) — never an unsalted sha256 of
    payload-bearing text. An attacker who later reads the ledger offline can
    no longer confirm a low-entropy guess against an erased response.
    Deliberately THE SAME keying as core.finalization's erasure digest
    tombstones so serve-time gates can reconcile receipts against governed
    erasure state deterministically. Key-store failure refuses the receipt
    entirely — fail closed, never a silent downgrade to an unsalted oracle."""
    from core.finalization import _keyed_tombstone_value

    return _keyed_tombstone_value(_sha256_hex(str(text or "")))


def _canonical_bytes(content: dict[str, Any]) -> bytes:
    """Deterministic serialization used for both the content hash and the signature."""
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _content_for(
    *,
    schema: str,
    session_id: str,
    turn_index: int,
    issued_at: float,
    prev_hash: str,
    prompt_hash: str,
    response_hash: str,
    claimed_actions: list[str],
    executed_tools: list[dict[str, Any]],
    verdict: str,
    verdict_detail: str,
) -> dict[str, Any]:
    # The signed/hashed content - everything except content_hash + signature themselves.
    return {
        "schema": schema,
        "session_id": str(session_id),
        "turn_index": int(turn_index),
        "issued_at": round(float(issued_at), 6),
        "prev_hash": str(prev_hash or ""),
        "prompt_hash": str(prompt_hash or ""),
        "response_hash": str(response_hash or ""),
        "claimed_actions": sorted(str(a) for a in (claimed_actions or [])),
        "executed_tools": [
            {
                "tool": str(t.get("tool") or t.get("tool_name") or ""),
                "status": str(t.get("status") or t.get("mode") or ""),
                "receipt_key": str(t.get("receipt_key") or t.get("receipt_id") or ""),
            }
            for t in (executed_tools or [])
        ],
        "verdict": str(verdict),
        "verdict_detail": str(verdict_detail or ""),
    }


def issue_honesty_receipt(
    *,
    session_id: str,
    turn_index: int,
    prompt_text: str = "",
    response_text: str = "",
    claimed_actions: list[str] | None = None,
    executed_tools: list[dict[str, Any]] | None = None,
    verdict: str = VERDICT_NO_CLAIM,
    verdict_detail: str = "",
    prev_hash: str = "",
    issued_at: float | None = None,
) -> HonestyReceipt:
    """Build, hash-chain, and Ed25519-sign a single honesty receipt for one turn."""
    ts = time.time() if issued_at is None else float(issued_at)
    content = _content_for(
        schema=SCHEMA,
        session_id=session_id,
        turn_index=turn_index,
        issued_at=ts,
        prev_hash=prev_hash,
        prompt_hash=_keyed_payload_digest(prompt_text) if prompt_text else "",
        response_hash=_keyed_payload_digest(response_text) if response_text else "",
        claimed_actions=claimed_actions or [],
        executed_tools=executed_tools or [],
        verdict=verdict,
        verdict_detail=verdict_detail,
    )
    content_bytes = _canonical_bytes(content)
    content_hash = hashlib.sha256(content_bytes).hexdigest()

    signer_peer_id, signature = "", ""
    try:
        from network import signer  # local Ed25519 identity

        signature = signer.sign(content_bytes)
        signer_peer_id = signer.get_local_peer_id()
    except Exception:
        # No key available (or signer backend missing) -> unsigned receipt. Still content-hashed
        # and chain-linked; verify() will flag it as unsigned rather than silently "valid".
        signer_peer_id, signature = "", ""

    return HonestyReceipt(
        receipt_id=f"hr-{uuid.uuid4().hex}",
        content_hash=content_hash,
        signer_peer_id=signer_peer_id,
        signature=signature,
        **content,  # type: ignore[arg-type]
    )


def _receipt_content(receipt: dict[str, Any]) -> dict[str, Any]:
    return _content_for(
        schema=str(receipt.get("schema") or SCHEMA),
        session_id=receipt.get("session_id", ""),
        turn_index=receipt.get("turn_index", 0),
        issued_at=receipt.get("issued_at", 0.0),
        prev_hash=receipt.get("prev_hash", ""),
        prompt_hash=receipt.get("prompt_hash", ""),
        response_hash=receipt.get("response_hash", ""),
        claimed_actions=receipt.get("claimed_actions", []),
        executed_tools=receipt.get("executed_tools", []),
        verdict=receipt.get("verdict", ""),
        verdict_detail=receipt.get("verdict_detail", ""),
    )


def verify_honesty_receipt(receipt: dict[str, Any] | HonestyReceipt) -> tuple[bool, str]:
    """Re-verify one receipt offline: content hash integrity + Ed25519 signature.

    Returns (ok, reason). ok=False for any tampering, a broken content hash, a bad/missing
    signature, or an unsigned receipt.
    """
    data = receipt.to_dict() if isinstance(receipt, HonestyReceipt) else dict(receipt)
    content_bytes = _canonical_bytes(_receipt_content(data))
    if hashlib.sha256(content_bytes).hexdigest() != str(data.get("content_hash") or ""):
        return False, "content_hash_mismatch (tampered or malformed)"
    signature = str(data.get("signature") or "")
    peer_id = str(data.get("signer_peer_id") or "")
    if not signature or not peer_id:
        return False, "unsigned"
    try:
        from network import signer

        if not signer.verify(content_bytes, signature, peer_id):
            return False, "bad_signature"
    except Exception as exc:  # pragma: no cover - signer backend missing
        return False, f"verify_error:{type(exc).__name__}"
    return True, "ok"


#: What a passing chain verification actually establishes, in one sentence, so no
#: surface has to compose its own wording and drift.
CHAIN_PROVEN_CLAIM = (
    "every receipt present is individually valid and correctly linked to the one before it"
)
#: What it does NOT establish. Stated as plainly as the claim, because this is the
#: gap a reader would otherwise fill in with the stronger reading.
CHAIN_UNPROVEN_CLAIM = (
    "receipts deleted from the END of the chain leave no trace, so completeness is not proven"
)


def verify_honesty_chain(receipts: list[dict[str, Any]]) -> tuple[bool, str]:
    """Verify the receipts PRESENT: each is individually valid and links to the one before it.

    THIS DOES NOT PROVE THE CHAIN IS COMPLETE. The walk starts at the empty
    ``prev_hash`` and follows the links it is given, so:

    - a MUTATED receipt fails its own signature check;
    - a receipt removed from the MIDDLE breaks the next one's ``prev_hash`` link;
    - a FORGED signature fails verification;
    - **receipts truncated from the END are undetectable** -- the surviving prefix is
      a perfectly valid chain, and nothing here knows how long the chain should be.

    Detecting truncation needs an expected head persisted somewhere the truncation
    cannot reach; there is no such anchor today. Until there is, every surface
    reporting this result must say "the receipts present are consistent", never
    "the history is intact". ``CHAIN_PROVEN_CLAIM`` / ``CHAIN_UNPROVEN_CLAIM`` carry
    that wording so the CLI, the API and the UI cannot drift apart.
    """
    prev = ""
    for i, r in enumerate(receipts):
        ok, reason = verify_honesty_receipt(r)
        if not ok:
            return False, f"receipt[{i}] {reason}"
        if str(r.get("prev_hash") or "") != prev:
            return False, f"receipt[{i}] broken_chain (prev_hash != prior content_hash)"
        prev = str(r.get("content_hash") or "")
    return True, "ok"


# --- Optional per-session JSONL ledger (append-only, tamper-evident by the hash chain) ---

def _ledger_dir() -> Path:
    home = os.environ.get("VOOL_HOME") or str(Path.home() / ".vool")
    return Path(home) / "data" / "honesty_receipts"


def _ledger_path(session_id: str) -> Path:
    safe = _sha256_hex(session_id)[:24]
    return _ledger_dir() / f"{safe}.jsonl"


def list_honesty_receipts(session_id: str) -> list[dict[str, Any]]:
    path = _ledger_path(session_id)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def latest_receipt_hash(session_id: str) -> str:
    receipts = list_honesty_receipts(session_id)
    return str(receipts[-1].get("content_hash") or "") if receipts else ""


def record_honesty_receipt(receipt: HonestyReceipt) -> None:
    """Append a receipt to its session's append-only JSONL ledger (best-effort)."""
    try:
        path = _ledger_path(receipt.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt.to_dict(), sort_keys=True) + "\n")
    except Exception:
        pass


def load_receipts_file(path: str | Path) -> list[dict[str, Any]]:
    """Load a receipt bundle: a single receipt JSON, a JSON array, or a `.jsonl` ledger."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def _latest_ledger_path() -> Path | None:
    """Most-recently-written session ledger, across all sessions (for `verify-last`)."""
    ledger_dir = _ledger_dir()
    if not ledger_dir.exists():
        return None
    ledgers = sorted(ledger_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return ledgers[0] if ledgers else None


# --- Terminal color (stdlib only; auto-off when piped, dumb, or NO_COLOR set) ---

_ANSI = {"green": "\033[32m", "red": "\033[31m", "bold": "\033[1m", "dim": "\033[2m", "cyan": "\033[36m", "reset": "\033[0m"}
_COLOR_ENABLED: bool | None = None


def _color_enabled() -> bool:
    global _COLOR_ENABLED
    if _COLOR_ENABLED is None:
        import sys

        enabled = os.environ.get("NO_COLOR") is None and bool(getattr(sys.stdout, "isatty", lambda: False)())
        if enabled and os.name == "nt":
            try:  # enable VT100 processing on Windows 10+ terminals
                os.system("")
            except Exception:
                enabled = False
        _COLOR_ENABLED = enabled
    return _COLOR_ENABLED


def _c(text: str, *styles: str) -> str:
    if not _color_enabled():
        return text
    return "".join(_ANSI.get(s, "") for s in styles) + text + _ANSI["reset"]


_VERDICT_LABEL = {
    VERDICT_CLEAN: "clean (every claim backed by a real tool run)",
    VERDICT_NO_CLAIM: "no side-effecting action claimed",
    VERDICT_BLOCKED: "BLOCKED - agent's false claim was caught",
    VERDICT_UNBACKED: "UNBACKED - claim recorded with no execution behind it",
    VERDICT_CONTRADICTED: "CONTRADICTED - the answer denied what the execution trace records",
}


def _render_verification(receipts: list[dict[str, Any]], *, source: str = "") -> int:
    """Verify a list of receipts + the chain, print a human-readable report, return exit code."""
    if source:
        print(_c(source, "dim"))
    all_ok = True
    for i, r in enumerate(receipts):
        ok, reason = verify_honesty_receipt(r)
        all_ok = all_ok and ok
        mark = _c("PASS", "green", "bold") if ok else _c("FAIL", "red", "bold")
        verdict = str(r.get("verdict") or "")
        label = _VERDICT_LABEL.get(verdict, verdict or "(none)")
        signer = str(r.get("signer_peer_id") or "")[:16] or "(unsigned)"
        tail = "" if ok else _c(f"   <- {reason}", "red")
        print(f"  [{i}] {mark}  {label:46} signer={signer}{tail}")
    chain_ok, chain_reason = verify_honesty_chain(receipts)
    all_ok = all_ok and chain_ok
    print(
        "  chain: "
        + (_c("CONSISTENT", "green") if chain_ok else _c("BROKEN - " + chain_reason, "red"))
    )
    if all_ok:
        print(
            _c("RESULT: EVERY RECEIPT PRESENT VERIFIED", "green", "bold")
            + _c("  (signed + correctly linked, fully offline)", "dim")
        )
        print(_c(f"  NOT PROVEN: {CHAIN_UNPROVEN_CLAIM}", "dim"))
    else:
        print(_c("RESULT: VERIFICATION FAILED", "red", "bold"))
    return 0 if all_ok else 1


def _run_demo() -> int:
    """Self-contained, zero-setup demo: sign a clean + a caught-lying receipt, verify them,
    then show that any tampering is caught. Nothing is written to a real ledger."""
    print(_c("VOOL - Signed Honesty Receipt demo", "bold", "cyan"))
    print(_c("Cryptographic proof of what an AI agent DID. Verify it yourself, offline. No server.\n", "dim"))

    sid = "demo-session"
    clean = issue_honesty_receipt(
        session_id=sid, turn_index=0,
        prompt_text="write a summary to notes.md",
        response_text="Done - I wrote the summary to notes.md.",
        claimed_actions=["wrote file notes.md"],
        executed_tools=[{"tool": "write_file", "status": "executed", "receipt_key": "tr-001"}],
        verdict=VERDICT_CLEAN, verdict_detail="claim backed by a real write_file execution",
        prev_hash="",
    )
    lied = issue_honesty_receipt(
        session_id=sid, turn_index=1,
        prompt_text="send 5 SOL to the vendor",
        response_text="I've sent 5 SOL to the vendor.",  # the model asserted an action...
        claimed_actions=["sent 5 SOL"],
        executed_tools=[],  # ...but ground truth: no transfer tool ran
        verdict=VERDICT_BLOCKED, verdict_detail="claimed 'sent 5 SOL' but no transfer tool executed",
        prev_hash=clean.content_hash,
    )

    print(_c("Two agent turns happened. VOOL signed a receipt for each:", "bold"))
    print(f"   turn 0: wrote notes.md                       -> verdict {_c('clean', 'green', 'bold')}")
    print(f"   turn 1: SAID 'sent 5 SOL' but nothing ran    -> verdict {_c('blocked', 'red', 'bold')}  (lie caught + stopped)\n")

    receipts = [clean.to_dict(), lied.to_dict()]
    print(_c("Anyone can re-verify the receipts offline - this is the whole trust model:", "bold"))
    _render_verification(receipts)

    print(_c("\nNow watch tampering get caught two ways.", "bold"))
    forged = dict(receipts[1])
    forged["verdict"] = VERDICT_CLEAN
    forged["claimed_actions"] = []
    ok1, why1 = verify_honesty_receipt(forged)
    print(f"   1) edit the receipt to hide the block  -> {_c('FAIL', 'red', 'bold')}  {_c(why1, 'red')}")

    forged2 = dict(forged)
    forged2["content_hash"] = hashlib.sha256(_canonical_bytes(_receipt_content(forged))).hexdigest()
    ok2, why2 = verify_honesty_receipt(forged2)
    print(f"   2) also recompute the hash to cover it -> {_c('FAIL', 'red', 'bold')}  {_c(why2, 'red')}  (no private key = no forgery)")

    if not ok1 and not ok2:
        print(_c("\nProof of what the agent did, lies caught, impossible to forge - all offline, no trust required.", "green", "bold"))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Offline verifier CLI. Subcommands:
      verify <path>   verify a receipt .json / JSON array / .jsonl ledger
      verify-last     find and verify this machine's most recent session ledger
      demo            zero-setup demo: sign, verify, and try (and fail) to forge a receipt
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="vool-honesty",
        description="Verify VOOL signed honesty receipts fully offline (no network, no server).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="verify a receipt file or session ledger")
    v.add_argument("path", help="path to a receipt .json, a JSON array, or a session .jsonl ledger")
    vl = sub.add_parser("verify-last", help="verify this machine's most recent session ledger")
    vl.add_argument("--session", default="", help="verify a specific session id instead of the latest")
    sub.add_parser("demo", help="run a self-contained sign/verify/forge-attempt demo")
    args = parser.parse_args(argv)

    if args.cmd == "demo":
        return _run_demo()

    if args.cmd == "verify-last":
        path = _ledger_path(args.session) if args.session else _latest_ledger_path()
        if path is None or not path.exists():
            print(_c("No honesty receipts found yet.", "bold"))
            print(_c("Run a VOOL turn first (receipts are written as the agent acts), or try:  python -m core.honesty_receipt demo", "dim"))
            return 1
        receipts = load_receipts_file(path)
        if not receipts:
            print("no receipts found")
            return 1
        return _render_verification(receipts, source=f"ledger: {path}")

    receipts = load_receipts_file(args.path)
    if not receipts:
        print("no receipts found")
        return 1
    return _render_verification(receipts, source=f"file: {args.path}")


__all__ = [
    "CHAIN_PROVEN_CLAIM",
    "CHAIN_UNPROVEN_CLAIM",
    "SCHEMA",
    "VERDICT_BLOCKED",
    "VERDICT_CLEAN",
    "VERDICT_CONTRADICTED",
    "VERDICT_NO_CLAIM",
    "VERDICT_UNBACKED",
    "HonestyReceipt",
    "issue_honesty_receipt",
    "latest_receipt_hash",
    "list_honesty_receipts",
    "load_receipts_file",
    "record_honesty_receipt",
    "verify_honesty_chain",
    "verify_honesty_receipt",
]


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
