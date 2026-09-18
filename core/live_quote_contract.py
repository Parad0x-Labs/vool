from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "EUR ",
    "GBP": "GBP ",
}


def format_quote_timestamp(timestamp_utc: int | float | None) -> str:
    if timestamp_utc in {None, ""}:
        return ""
    try:
        dt = datetime.fromtimestamp(float(timestamp_utc), tz=timezone.utc)
    except Exception:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


@dataclass(frozen=True)
class LiveQuoteResult:
    asset_key: str
    asset_name: str
    symbol: str
    value: float
    currency: str
    as_of: str
    source_label: str
    source_url: str
    kind: str = "market"
    unit_label: str = ""
    change_percent: float | None = None
    change_window: str = ""
    market_cap: float | None = None
    timestamp_utc: int | None = None
    exchange: str = ""
    confidence: float = 0.95

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> LiveQuoteResult:
        return cls(
            asset_key=str(payload.get("asset_key") or "").strip(),
            asset_name=str(payload.get("asset_name") or "").strip(),
            symbol=str(payload.get("symbol") or "").strip(),
            value=float(payload.get("value") or 0.0),
            currency=str(payload.get("currency") or "").strip().upper(),
            as_of=str(payload.get("as_of") or "").strip(),
            source_label=str(payload.get("source_label") or "").strip(),
            source_url=str(payload.get("source_url") or "").strip(),
            kind=str(payload.get("kind") or "market").strip(),
            unit_label=str(payload.get("unit_label") or "").strip(),
            change_percent=None if payload.get("change_percent") in {None, ""} else float(payload.get("change_percent")),
            change_window=str(payload.get("change_window") or "").strip(),
            market_cap=None if payload.get("market_cap") in {None, ""} else float(payload.get("market_cap")),
            timestamp_utc=None if payload.get("timestamp_utc") in {None, ""} else int(float(payload.get("timestamp_utc"))),
            exchange=str(payload.get("exchange") or "").strip(),
            confidence=float(payload.get("confidence") or 0.95),
        )

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    def to_note(self) -> dict[str, Any]:
        return {
            "result_title": f"{self.asset_name} quote",
            "result_url": self.source_url,
            "origin_domain": self._origin_domain(),
            "summary": self.summary_text(),
            "confidence": self.confidence,
            "source_profile_label": self.source_label,
            "page_text": self.summary_text(),
            "live_quote": self.to_payload(),
        }

    def summary_text(self) -> str:
        parts = [f"{self.asset_name}: {self._price_text()}"]
        if self.unit_label:
            parts[-1] += f" {self.unit_label}"
        if self.change_percent is not None:
            change_label = f"{self.change_window} change" if self.change_window else "change"
            parts.append(f"{change_label}: {self.change_percent:+.2f}%")
        if self.as_of:
            parts.append(f"as of {self.as_of}")
        return " | ".join(parts)

    def answer_text(self) -> str:
        line = f"{self.asset_name} is {self._price_text()}"
        if self.unit_label:
            line += f" {self.unit_label}"
        if self.as_of:
            line += f" as of {self.as_of}."
        else:
            line += "."
        if self.change_percent is not None:
            change_label = f"{self.change_window} change" if self.change_window else "change"
            line += f" {change_label.capitalize()}: {self.change_percent:+.2f}%."
        line += f" Source: [{self.source_label}]({self.source_url})."
        return line

    def _price_text(self) -> str:
        currency = (self.currency or "").upper()
        prefix = _CURRENCY_SYMBOLS.get(currency, f"{currency} " if currency else "")
        if abs(self.value) >= 1:
            value_text = f"{self.value:,.2f}"
        elif abs(self.value) >= 0.0001 or self.value == 0:
            value_text = f"{self.value:,.4f}"
        else:
            # A fixed 4-decimal floor printed every micro-priced token as "$0.0000" -- PEPE
            # (~$0.000008) reported a real, sourced, correctly-fetched quote as zero. Those coins
            # only became answerable once symbols started resolving against the live index, so the
            # formatter had never met one. Scale the precision to the number instead: four
            # significant digits, which reads correctly from PEPE down to SHIB and BONK.
            import math

            leading_zeros = -math.floor(math.log10(abs(self.value))) - 1
            decimals = min(leading_zeros + 4, 18)
            value_text = f"{self.value:,.{decimals}f}".rstrip("0")
        if prefix.endswith(" "):
            return f"{prefix}{value_text}".strip()
        suffix = f" {currency}".rstrip() if currency else ""
        return f"{prefix}{value_text}{suffix}".strip()

    def _origin_domain(self) -> str:
        try:
            from urllib.parse import urlparse

            netloc = str(urlparse(self.source_url).netloc or "").lower()
            return netloc[4:] if netloc.startswith("www.") else netloc
        except Exception:
            return ""


def validate_live_quote_payload(payload: Mapping[str, Any]) -> tuple[bool, str]:
    required_fields = ("asset_name", "value", "currency", "as_of", "source_label", "source_url")
    missing = [field for field in required_fields if not str(payload.get(field) or "").strip()]
    if missing:
        return False, f"missing required live quote fields: {', '.join(missing)}"
    try:
        value = float(payload.get("value") or 0.0)
    except Exception:
        return False, "live quote value must be numeric"
    if value <= 0:
        return False, "live quote value must be positive"
    source_url = str(payload.get("source_url") or "").strip()
    if not source_url.startswith(("http://", "https://")):
        return False, "live quote source_url must be absolute"
    return True, "ok"
