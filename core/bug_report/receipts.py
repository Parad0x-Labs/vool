"""Durable submission receipts: record WHAT left, never the content that left.

Each successful submission appends one JSONL row carrying only metadata -- ids, hashes,
sizes, names, destination, issue url, and the credential SOURCE LABEL (env var name), never
a credential value and never report content. Rows are chained: event_hash covers the
previous hash plus the canonical row body, so silent edits are detectable by
``verify_chain`` (the honesty-receipt pattern from core/honesty_receipt.py).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

_RECEIPT_KEYS = (
    "receipt_id", "report_id", "fingerprint", "payload_sha256", "destination",
    "issue_url", "issue_number", "submitted_at", "total_bytes", "field_names",
    "attachment_names", "credential_source",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReceiptStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def record_submission(
        self,
        *,
        report_id: str,
        fingerprint: str,
        payload_sha256: str,
        destination: str,
        issue_url: str,
        issue_number: int | None,
        total_bytes: int,
        field_names: list[str],
        attachment_names: list[str],
        credential_source: str,
    ) -> dict:
        body = {
            "receipt_id": "rcpt_" + uuid.uuid4().hex[:12],
            "report_id": str(report_id),
            "fingerprint": str(fingerprint),
            "payload_sha256": str(payload_sha256),
            "destination": str(destination),
            "issue_url": str(issue_url),
            "issue_number": issue_number,
            "submitted_at": _utc_now(),
            "total_bytes": int(total_bytes),
            "field_names": [str(x) for x in field_names],
            "attachment_names": [str(x) for x in attachment_names],
            "credential_source": str(credential_source),
        }
        rows = self._rows()
        prev_hash = rows[-1]["event_hash"] if rows else "genesis"
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True)
        event_hash = hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()
        row = {"seq": len(rows) + 1, "prev_hash": prev_hash, "event_hash": event_hash, **body}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row

    def rows(self) -> list[dict]:
        return self._rows()

    def find_by_fingerprint(self, fingerprint: str) -> dict | None:
        for row in reversed(self._rows()):
            if row.get("fingerprint") == fingerprint:
                return row
        return None

    def verify_chain(self) -> bool:
        rows = self._rows()
        prev = "genesis"
        for expected_seq, row in enumerate(rows, start=1):
            if set(row) != {"seq", "prev_hash", "event_hash", *_RECEIPT_KEYS}:
                return False
            if row["seq"] != expected_seq or row["prev_hash"] != prev:
                return False
            body = {key: row[key] for key in _RECEIPT_KEYS}
            canonical = json.dumps(body, ensure_ascii=False, sort_keys=True)
            if hashlib.sha256((prev + canonical).encode("utf-8")).hexdigest() != row["event_hash"]:
                return False
            prev = row["event_hash"]
        return True


__all__ = ["ReceiptStore"]
