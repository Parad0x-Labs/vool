"""The last words of a reply, read at the one seam every served result passes (`_seal_semantic_result`).

Advice beside today's prices, with no web (Mac answer-quality row 10 on d5d044b0, real qwen3:8b, clean loop
off and on): "suggest a good tripod for travel and tell me what it costs today" came back with the advice and
"you can expect to pay between $100 and $150", and no word that the prices need Web lookups. The front door
lets that turn through for the advice (`core.online_asks.advice_beside_live_prices`); here the reply loses
every sentence that states a price no lookup backed, and says plainly that prices need Web lookups and where
to turn them on. Read only on the reply text; never raises.
"""
from __future__ import annotations

import re
from typing import Any

WEB_OFF_PRICES_LINE = (
    "Current prices need Web lookups, which are off, so I've left them out. "
    "You can turn them on in Settings, Privacy & Permissions."
)
_MONEY_RE = re.compile(
    r"(?:[$£€¥₹]\s?\d|\d[\d,.]*\s?(?:usd|eur|gbp|dollars?|euros?|pounds?|bucks)\b)", re.IGNORECASE
)
_SENTENCE_RE = re.compile(r"[^.!?\n]*(?:[.!?]+|$)")
# The model's own "I can't check prices / check a retailer" sentences: the plain line says it once, correctly.
_PRICE_DEFLECTION_RE = re.compile(
    r"\b(?:real[- ]time\s+pric|live\s+pric|current\s+pric|latest\s+pric|check(?:ing)?\s+(?:online\s+)?retailers?|"
    r"prices?\s+(?:can|may|might|will)\s+vary)",
    re.IGNORECASE,
)
_WEB_OFF_NOTE_RE = re.compile(
    r"\n*Web lookups are off, so this is from what I already know and may be out of date\. "
    r"You can turn them on in Settings, Privacy & Permissions\.\s*$"
)


_BUT_RE = re.compile(r",?\s+(?:but|however|though)\s+", re.IGNORECASE)


def _after_deflection(sentence: str) -> str:
    """The sentence without the model's own "I can't check prices" clause; "" when that is all it says."""
    if not _PRICE_DEFLECTION_RE.search(sentence):
        return sentence
    parts = _BUT_RE.split(sentence, maxsplit=1)
    if len(parts) == 2 and not _PRICE_DEFLECTION_RE.search(parts[1]):
        rest = parts[1].strip()
        return " " + rest[:1].upper() + rest[1:] if rest else ""
    return ""


def _without_prices(text: str) -> str:
    kept_lines: list[str] = []
    for line in str(text or "").split("\n"):
        if "|" in line and _MONEY_RE.search(line):
            # A table row: blank the cells that state a price, keep the row's other cells.
            cells = line.split("|")
            line = "|".join("—" if _MONEY_RE.search(cell) else cell for cell in cells)
            kept_lines.append(line)
            continue
        sentences = [s for s in _SENTENCE_RE.findall(line) if s]
        kept = [_after_deflection(s) for s in sentences if not _MONEY_RE.search(s)]
        kept = [s for s in kept if s.strip()]
        if sentences and not kept and line.strip():
            continue
        kept_lines.append("".join(kept).rstrip() if sentences else line)
    out = "\n".join(kept_lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    # A sentence that opened "..., but" loses its lead with the deflection; keep what remains readable.
    return re.sub(r"^\s*(?:but|however|that said)[,\s]+", "", out, flags=re.IGNORECASE)


def web_off_prices_reply(text: str, request: str, source_context: dict[str, Any] | None) -> str:
    """`text` as sent for an advice-and-prices ask on a turn with no web; `text` unchanged otherwise."""
    try:
        from core.remote_fetch_policy import effective_web_available

        if effective_web_available(source_context):
            return text
        from core.online_asks import advice_beside_live_prices

        if not advice_beside_live_prices(request, source_context):
            return text
        body = _WEB_OFF_NOTE_RE.sub("", str(text or ""))
        from core.model_output_guard import web_off_live_value_notice

        body = web_off_live_value_notice(body)
        if WEB_OFF_PRICES_LINE in body:
            body = body.replace(WEB_OFF_PRICES_LINE, "")
        body = _without_prices(body)
        if not body:
            return text
        return f"{body}\n\n{WEB_OFF_PRICES_LINE}"
    except Exception:
        return text


def edge_reply(result: dict[str, Any], request: str, source_context: dict[str, Any] | None) -> dict[str, Any]:
    """The result with its reply read at the edge. Never raises; returns `result` itself when nothing changes."""
    try:
        response = str((result or {}).get("response") or "")
        if not response.strip():
            return result
        changed = web_off_prices_reply(response, request, source_context)
        if changed == response:
            return result
        return {**result, "response": changed}
    except Exception:
        return result


__all__ = ["WEB_OFF_PRICES_LINE", "edge_reply", "web_off_prices_reply"]
