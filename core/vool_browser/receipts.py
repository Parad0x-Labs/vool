"""Receipts for browser operations: one JSONL file per session, hash-chained.

A receipt is the operator's ground truth for what the browser actually did:
op id, operation, session, origin, final URL, bounds and outcome, with the
byte size and sha256 for anything written to disk. Each line carries a digest
of its canonical content so a line edited after the fact is detectable
(`verify_receipts_file`).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

_DIGEST_KEY = "_digest"


def new_op_id(op: str) -> str:
    return f"{op}-{uuid.uuid4().hex[:12]}"


def _canonical_line(row: dict[str, Any]) -> str:
    clean = {k: v for k, v in row.items() if k != _DIGEST_KEY}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _line_digest(row: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_line(row).encode("utf-8")).hexdigest()


def build_receipt(
    *,
    op: str,
    session: str,
    outcome: str,
    origin: str = "",
    final_url: str = "",
    bounds: dict[str, Any] | None = None,
    **evidence: Any,
) -> dict[str, Any]:
    """One operation's receipt. `evidence` carries op-specific, bounded facts."""

    receipt: dict[str, Any] = {
        "op_id": new_op_id(op),
        "op": op,
        "session": session,
        "ts": round(time.time(), 3),
        "origin": origin,
        "final_url": final_url,
        "outcome": outcome,
    }
    if bounds:
        receipt["bounds"] = dict(bounds)
    for key, value in evidence.items():
        if value is not None:
            receipt[key] = value
    receipt[_DIGEST_KEY] = _line_digest(receipt)
    return receipt


def append_receipt(receipts_path: Path, receipt: dict[str, Any]) -> None:
    receipts_path.parent.mkdir(parents=True, exist_ok=True)
    with receipts_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True) + "\n")


def verify_receipts_file(receipts_path: Path) -> tuple[int, list[str]]:
    """Return (lines_ok, problems). A tampered or edited line is a problem."""

    problems: list[str] = []
    ok = 0
    if not receipts_path.is_file():
        return 0, ["receipts file missing"]
    for index, line in enumerate(receipts_path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append(f"line {index}: not JSON")
            continue
        digest = row.get(_DIGEST_KEY)
        if not digest or _line_digest(row) != digest:
            problems.append(f"line {index}: digest mismatch")
            continue
        ok += 1
    return ok, problems


def read_receipts(receipts_path: Path) -> list[dict[str, Any]]:
    if not receipts_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in receipts_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows
