from __future__ import annotations

from core.live_quote_contract import LiveQuoteResult, validate_live_quote_payload


def all_live_quotes(notes: list[dict[str, object]]) -> list[LiveQuoteResult]:
    """Every valid quote in the notes, in order — not just the one that happened to come first.

    Measured live 2026-08-03: the operator asked "btc price now? and sol price please" and received
    Bitcoin only. Asking for `sol price now?` alone answered Solana correctly, so the lookup was
    capable of both; `first_live_quote` returned on the first valid payload and the rest were
    discarded with nothing said.

    Same defect as the second filename dropped from a read, the second file in a batch, and the
    eight deferred tools — the first match wins and the remainder disappears in silence.
    """

    quotes: list[LiveQuoteResult] = []
    seen: set[str] = set()
    for note in list(notes or []):
        payload = note.get("live_quote")
        if not isinstance(payload, dict):
            continue
        ok, _reason = validate_live_quote_payload(payload)
        if not ok:
            continue
        try:
            quote = LiveQuoteResult.from_payload(payload)
        except Exception:
            continue
        key = f"{getattr(quote, 'asset', '')}|{getattr(quote, 'symbol', '')}".strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        quotes.append(quote)
    return quotes


def first_live_quote(notes: list[dict[str, object]]) -> LiveQuoteResult | None:
    for note in list(notes or []):
        payload = note.get("live_quote")
        if not isinstance(payload, dict):
            continue
        ok, _reason = validate_live_quote_payload(payload)
        if not ok:
            continue
        try:
            return LiveQuoteResult.from_payload(payload)
        except Exception:
            continue
    return None
