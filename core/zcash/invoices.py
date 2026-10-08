"""Shielded ZEC payment requests (ZIP 321) and the store that remembers them.

An invoice is an amount, a label and a memo ``VOOL-<id>`` addressed to the watch wallet's shielded receiving
address. The memo is what the matcher looks for in received shielded notes, so it is fixed at creation and
never edited. Amounts are integers in zatoshis (1 ZEC = 100,000,000); decimal input is parsed exactly.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.zcash import config

ZAT_PER_ZEC = 100_000_000
MAX_ZAT = 21_000_000 * ZAT_PER_ZEC
MEMO_PREFIX = "VOOL-"
STORE_FILE = "invoices.sqlite"
_LABEL_MAX = 120
#: Plain digits with at most one decimal separator. Grouping ("1,000", "1 000", "1'000"), exponents and signs are
#: refused rather than guessed, and so is one separator followed by exactly three digits after a non-zero whole
#: part ("1,000", "12.500"), because half the world reads that as thousands. ASCII digits only (\d takes any script's digits).
_AMOUNT = re.compile(r"([0-9]*)([.,])?([0-9]*)")


class InvoiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    network: str
    address: str
    amount_zat: int
    label: str
    payer: str
    memo: str
    created_at: str
    paid_txid: str
    paid_receipt_id: str
    paid_zat: int = 0
    reorged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"invoice_id": self.invoice_id, "network": self.network, "address": self.address, "amount_zat": self.amount_zat,
                "amount": format_zec(self.amount_zat), "label": self.label, "payer": self.payer, "memo": self.memo,
                "created_at": self.created_at, "paid_txid": self.paid_txid, "paid_receipt_id": self.paid_receipt_id,
                "paid": format_zec(self.paid_zat), "reorged": self.reorged, "uri": payment_uri(self)}


def parse_amount(text: Any) -> int:
    """'0.05' -> 5_000_000 zatoshis. Ambiguous, malformed, zero, negative or absurd amounts are refused."""
    raw = str(text if text is not None else "").strip()
    for suffix in (" zec", " taz", "zec", "taz"):
        if raw.lower().endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            break
    match = _AMOUNT.fullmatch(raw)
    if not raw or match is None or not (match.group(1) or match.group(3)) or (match.group(2) and not match.group(3)):
        raise InvoiceError("amount_invalid", "Give the amount in ZEC as plain digits, for example 0.05 or 1000.")
    whole, separator, fraction = match.groups()
    if separator and len(fraction) == 3 and whole.strip("0"):
        raise InvoiceError("amount_ambiguous", f"'{raw[:24]}' could mean a decimal or thousands. Write it without a thousands separator, for example 1000 or 1.5.")
    value = Decimal(f"{whole or '0'}.{fraction or '0'}")
    if value <= 0:
        raise InvoiceError("amount_invalid", "The amount must be more than zero.")
    zat = value * ZAT_PER_ZEC
    if zat != zat.to_integral_value():
        raise InvoiceError("amount_invalid", "ZEC has at most 8 decimal places.")
    if zat > MAX_ZAT:
        raise InvoiceError("amount_invalid", "That amount is more than all ZEC that can exist.")
    return int(zat)


def format_zec(zat: int) -> str:
    whole, frac = divmod(int(zat), ZAT_PER_ZEC)
    text = f"{whole}.{frac:08d}".rstrip("0").rstrip(".")
    return text


def payment_uri(invoice: Invoice) -> str:
    """ZIP 321: zcash:<address>?amount=..&memo=<base64url, no padding>&message=.."""
    memo = base64.urlsafe_b64encode(invoice.memo.encode("utf-8")).decode("ascii").rstrip("=")
    parts = [f"amount={format_zec(invoice.amount_zat)}", f"memo={memo}"]
    if invoice.label:
        parts.append("message=" + quote(invoice.label, safe=""))
    return f"zcash:{invoice.address}?" + "&".join(parts)


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return "".join(ch for ch in text if ch.isprintable())[:limit]


class InvoiceStore:
    """The invoices and their receipt outbox (owner-only SQLite).

    A settled invoice moves through two committed steps: ``stage_receipt`` claims it with a receipt whose id is
    derived from the invoice and its transactions, then ``commit_paid`` marks it paid only once that receipt is in
    the journal. A crash in between leaves a staged receipt that the next refresh journals (idempotently) and
    commits, so a payment is receipted exactly once and "paid" never exists without its receipt.
    """

    _MIGRATIONS = (
        ("receipt_json", "TEXT NOT NULL DEFAULT ''"),
        ("remembered", "INTEGER NOT NULL DEFAULT 0"),
        ("reorged", "INTEGER NOT NULL DEFAULT 0"),
    )

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else config.data_dir() / STORE_FILE
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS zcash_invoices (invoice_id TEXT PRIMARY KEY, network TEXT NOT NULL, address TEXT NOT NULL,"
                " amount_zat INTEGER NOT NULL, label TEXT NOT NULL, payer TEXT NOT NULL, memo TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,"
                " paid_txid TEXT NOT NULL DEFAULT '', paid_receipt_id TEXT NOT NULL DEFAULT '', paid_zat INTEGER NOT NULL DEFAULT 0)"
            )
            have = {row[1] for row in conn.execute("PRAGMA table_info(zcash_invoices)")}
            for column, decl in self._MIGRATIONS:
                if column not in have:
                    conn.execute(f"ALTER TABLE zcash_invoices ADD COLUMN {column} {decl}")

    @contextmanager
    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():  # payer names, amounts and txids: owner-only from the first byte
            with contextlib.suppress(FileExistsError):
                os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row(row: tuple) -> Invoice:
        return Invoice(*row[:-1], reorged=bool(row[-1]))

    _COLUMNS = "invoice_id, network, address, amount_zat, label, payer, memo, created_at, paid_txid, paid_receipt_id, paid_zat, reorged"

    def create(self, *, address: str, amount_zat: int, label: str = "", payer: str = "", network: str | None = None) -> Invoice:
        if not address:
            raise InvoiceError("address_missing", "There is no shielded receiving address yet.")
        if not 0 < int(amount_zat) <= MAX_ZAT:
            raise InvoiceError("amount_invalid", "The amount must be more than zero.")
        invoice_id = secrets.token_hex(5)
        invoice = Invoice(
            invoice_id=invoice_id, network=network or config.network(), address=address, amount_zat=int(amount_zat),
            label=_clean_text(label, _LABEL_MAX), payer=_clean_text(payer, 80), memo=MEMO_PREFIX + invoice_id,
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), paid_txid="", paid_receipt_id="", paid_zat=0,
        )
        with self._conn() as conn:
            conn.execute("INSERT INTO zcash_invoices (invoice_id, network, address, amount_zat, label, payer, memo, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (invoice.invoice_id, invoice.network, invoice.address, invoice.amount_zat, invoice.label, invoice.payer,
                          invoice.memo, invoice.created_at))
        return invoice

    def get(self, invoice_id: str) -> Invoice | None:
        with self._conn() as conn:
            row = conn.execute(f"SELECT {self._COLUMNS} FROM zcash_invoices WHERE invoice_id = ?", (str(invoice_id or "").strip().lower(),)).fetchone()
        return self._row(row) if row else None

    def all(self, *, network: str | None = None) -> list[Invoice]:
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {self._COLUMNS} FROM zcash_invoices WHERE network = ? ORDER BY created_at, invoice_id",
                                (network or config.network(),)).fetchall()
        return [self._row(r) for r in rows]

    # --- the receipt outbox ------------------------------------------------------------------------------

    def stage_receipt(self, invoice_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Claim an unpaid invoice for this receipt. Returns the receipt that stands (an earlier staged one wins),
        or None when the invoice is already paid."""
        if not payload.get("txids") or int(payload.get("received_zat") or 0) <= 0 or not payload.get("receipt_id"):
            raise InvoiceError("payment_evidence_missing", "A payment cannot be recorded without its transaction and amount.")
        with self._conn() as conn:
            conn.execute("UPDATE zcash_invoices SET receipt_json = ? WHERE invoice_id = ? AND paid_txid = '' AND receipt_json = ''",
                         (json.dumps(payload, sort_keys=True), invoice_id))
            row = conn.execute("SELECT paid_txid, receipt_json FROM zcash_invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row is None or row[0]:
            return None
        return json.loads(row[1])

    def pending_receipts(self, *, network: str | None = None) -> list[dict[str, Any]]:
        """Staged receipts not yet committed (a crash or a failed journal write left them)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT receipt_json FROM zcash_invoices WHERE network = ? AND paid_txid = '' AND receipt_json != ''",
                                (network or config.network(),)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def commit_paid(self, receipt: dict[str, Any]) -> bool:
        """Mark paid from a journaled receipt. True only for the call that committed it."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE zcash_invoices SET paid_txid = ?, paid_receipt_id = ?, paid_zat = ? WHERE invoice_id = ? AND paid_txid = ''"
                " AND receipt_json != ''",
                (",".join(receipt["txids"]), receipt["receipt_id"], int(receipt["received_zat"]), receipt["invoice_id"]))
            return cur.rowcount == 1

    def unremembered(self, *, network: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT receipt_json FROM zcash_invoices WHERE network = ? AND paid_txid != '' AND remembered = 0"
                                " AND receipt_json != ''", (network or config.network(),)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def mark_remembered(self, invoice_id: str) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE zcash_invoices SET remembered = 1 WHERE invoice_id = ?", (invoice_id,))

    def set_reorged(self, invoice_id: str, reorged: bool) -> bool:
        """Flag (or clear) a receipted payment that is no longer on the chain. True when the flag changed."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE zcash_invoices SET reorged = ? WHERE invoice_id = ? AND paid_txid != '' AND reorged != ?",
                               (int(reorged), invoice_id, int(reorged)))
            return cur.rowcount == 1
