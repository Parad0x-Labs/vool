"""Card provider abstraction: VOOL holds provider TOKENS only. A primary account number or a
security code is refused at the door, filed as a security-relevant fault, and never stored --
the refusal's own record carries the field name, not the value."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.cards"
_FORBIDDEN_FIELDS = frozenset({"pan", "card_number", "number", "cvv", "cvc", "cvv2", "security_code", "track"})
_DIGITS = re.compile(r"[0-9]")


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def looks_like_pan(value: Any) -> bool:
    text = str(value or "")
    stripped = re.sub(r"[\s-]", "", text)
    if not stripped.isdigit() or not 13 <= len(stripped) <= 19:
        return False
    return _luhn(stripped)


@dataclass(frozen=True)
class CardToken:
    token_id: str
    provider: str
    token_ref: str
    last4: str
    brand: str
    exp_month: int
    exp_year: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"token_id": self.token_id, "provider": self.provider, "token_ref": self.token_ref, "last4": self.last4, "brand": self.brand, "exp_month": self.exp_month, "exp_year": self.exp_year, "created_at": self.created_at}


def register_card_token(*, provider: str, token_ref: str, last4: str, brand: str = "", exp_month: int, exp_year: int, source_context: dict[str, Any] | None = None, **extra: Any) -> CardToken:
    for field_name in extra:
        if str(field_name).lower() in _FORBIDDEN_FIELDS:
            raise wallet_fault("wallet_card_data_refused", authority=AUTHORITY, context={"field": str(field_name), "reason": "raw_card_data_field"}, source_context=source_context)
    for field_name, value in (("token_ref", token_ref), ("brand", brand), ("provider", provider), *extra.items()):
        if looks_like_pan(value):
            raise wallet_fault("wallet_card_data_refused", authority=AUTHORITY, context={"field": str(field_name), "reason": "value_shaped_like_a_card_number"}, source_context=source_context)
    clean_last4 = str(last4 or "").strip()
    if not (clean_last4.isdigit() and len(clean_last4) == 4):
        raise wallet_fault("wallet_card_data_refused", authority=AUTHORITY, context={"field": "last4", "reason": "last4_must_be_four_digits"}, source_context=source_context)
    if len(_DIGITS.findall(str(token_ref))) >= 13:
        raise wallet_fault("wallet_card_data_refused", authority=AUTHORITY, context={"field": "token_ref", "reason": "token_carries_a_long_digit_run"}, source_context=source_context)
    token = CardToken(token_id=f"card-{uuid.uuid4().hex[:12]}", provider=str(provider).strip(), token_ref=str(token_ref).strip(), last4=clean_last4, brand=str(brand or "").strip(), exp_month=int(exp_month), exp_year=int(exp_year), created_at=utcnow())
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_card_tokens (token_id, provider, token_ref, last4, brand, exp_month, exp_year, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (token.token_id, token.provider, token.token_ref, token.last4, token.brand, token.exp_month, token.exp_year, token.created_at),
        )
    return token


def list_card_tokens() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT token_id, provider, token_ref, last4, brand, exp_month, exp_year, created_at FROM wallet_card_tokens ORDER BY created_at ASC").fetchall()
    return [CardToken(*row).to_dict() for row in rows]
