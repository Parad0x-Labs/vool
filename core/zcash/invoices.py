"""Shielded ZEC payment requests (ZIP 321) and the store that remembers them.

An invoice is an amount, a label and a memo ``VOOL-<id>`` addressed to the watch wallet's shielded receiving
address. The memo is what the matcher looks for in received shielded notes, so it is fixed at creation and
never edited. Amounts are integers in zatoshis (1 ZEC = 100,000,000); decimal input is parsed exactly.
"""
from __future__ import annotations

import base64
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.zcash import config

ZAT_PER_ZEC = 100_000_000
MAX_ZAT = 21_000_000 * ZAT_PER_ZEC
MEMO_PREFIX = "VOOL-"
STORE_FILE = "invoices.sqlite"
_LABEL_MAX = 120


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

    def to_dict(self) -> dict[str, Any]:
        return {"invoice_id": self.invoice_id, "network": self.network, "address": self.address, "amount_zat": self.amount_zat,
                "amount": format_zec(self.amount_zat), "label": self.label, "payer": self.payer, "memo": self.memo,
                "created_at": self.created_at, "paid_txid": self.paid_txid, "paid_receipt_id": self.paid_receipt_id,
                "paid": format_zec(self.paid_zat), "uri": payment_uri(self)}


def parse_amount(text: Any) -> int:
    """'0.05' -> 5_000_000 zatoshis. More than 8 decimals, zero, negative or absurd amounts are refused."""
    raw = str(text if text is not None else "").strip().replace(",", ".")
    for suffix in (" zec", " taz", "zec", "taz"):
        if raw.lower().endswith(suffix):
            raw = raw[: -len(suffix)].strip()
            break
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise InvoiceError("amount_invalid", "Give the amount in ZEC, for example 0.05.") from exc
    if not value.is_finite() or value <= 0:
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
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else config.data_dir() / STORE_FILE
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS zcash_invoices (invoice_id TEXT PRIMARY KEY, network TEXT NOT NULL, address TEXT NOT NULL,"
                " amount_zat INTEGER NOT NULL, label TEXT NOT NULL, payer TEXT NOT NULL, memo TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,"
                " paid_txid TEXT NOT NULL DEFAULT '', paid_receipt_id TEXT NOT NULL DEFAULT '', paid_zat INTEGER NOT NULL DEFAULT 0)"
            )

    @contextmanager
    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row(row: tuple) -> Invoice:
        return Invoice(*row)

    _COLUMNS = "invoice_id, network, address, amount_zat, label, payer, memo, created_at, paid_txid, paid_receipt_id, paid_zat"

    def create(self, *, address: str, amount_zat: int, label: str = "", payer: str = "", network: str | None = None) -> Invoice:
        if not address:
            raise InvoiceError("address_missing", "There is no shielded receiving address yet.")
        if not 0 < int(amount_zat) <= MAX_ZAT:
            raise InvoiceError("amount_invalid", "The amount must be more than zero.")
        invoice_id = secrets.token_hex(5)
        invoice = Invoice(
            invoice_id=invoice_id, network=network or config.network(), address=address, amount_zat=int(amount_zat),
            label=_clean_text(label, _LABEL_MAX), payer=_clean_text(payer, 80), memo=MEMO_PREFIX + invoice_id,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), paid_txid="", paid_receipt_id="", paid_zat=0,
        )
        with self._conn() as conn:
            conn.execute(f"INSERT INTO zcash_invoices ({self._COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (invoice.invoice_id, invoice.network, invoice.address, invoice.amount_zat, invoice.label, invoice.payer,
                          invoice.memo, invoice.created_at, "", "", 0))
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

    def mark_paid(self, invoice_id: str, *, txid: str, receipt_id: str, received_zat: int) -> bool:
        """Record the paying transaction once. Returns False when it was already recorded (idempotent)."""
        if not txid or int(received_zat) <= 0:
            raise InvoiceError("payment_evidence_missing", "A payment cannot be recorded without its transaction and amount.")
        with self._conn() as conn:
            cur = conn.execute("UPDATE zcash_invoices SET paid_txid = ?, paid_receipt_id = ?, paid_zat = ? WHERE invoice_id = ? AND paid_txid = ''",
                               (txid, receipt_id, int(received_zat), invoice_id))
            return cur.rowcount == 1
