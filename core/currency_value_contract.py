"""Deterministic boundary between currency identity and currency value.

Currency names and contextual symbol meanings are stable identity facts.  Exchange rates,
purchasing-power comparisons, and value rankings are observations: they require evidence from the
current turn.  A local model must never bridge that boundary from its weights.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

STATIC_CURRENCY_IDENTITIES: dict[str, str] = {
    "ALL": "Albanian lek",
    "MAD": "Moroccan dirham",
    "GEL": "Georgian lari",
}

_IDENTITY_CONTEXT_RE = re.compile(
    r"\b(?:currency|currencies|currency\s+code|code\s+for|stand\s+for|mean|called|name)\b",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"\b(?:compar(?:e|ing|ison)|rank(?:ing)?|worth|value|exchange\s+rate|fx|ppp|purchasing\s+power|travell?er)\b",
    re.IGNORECASE,
)
_NO_LIVE_FOLLOWUP_RE = re.compile(
    r"^\s*(?:local|offline)\s+only[.!]?\s*$"
    r"|\b(?:without|no)\s+(?:live|current)\s+(?:data|rates?)\b"
    r"|\bdo\s+not\s+(?:use|search|browse)\s+(?:the\s+)?(?:web|internet)\b",
    re.IGNORECASE,
)
_SUPPLIED_FX_RATE_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:[A-Z]{3}|[$¥€£])\s*"
    r"(?:=|equals?|is)\s*\d+(?:[.,]\d+)?\s*(?:[A-Z]{3}|[$¥€£])\b"
)
_MONEY_MENTION_RE = re.compile(
    r"(?:[$¥€£]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*(?:kr\b|[A-Z]{3}\b))"
)
_NON_IDENTITY_CURRENCY_WORK_RE = re.compile(
    r"\b(?:i\s+have|travel(?:led|ing)?\s+to|buy|bought|purchase|spend|spent|costs?|"
    r"left(?:\s+over)?|remainder|deficit|short\s+by|enough\s+money|calculate|show\s+(?:the\s+)?math)\b"
    r"|\b\d+(?:[.,]\d+)?\s*(?:[+×*/-]|minus|plus|multiplied\s+by|divided\s+by)\s*"
    r"\d+(?:[.,]\d+)?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CurrencyValueReply:
    response: str
    reason: str


def asks_for_dynamic_currency_value(text: str) -> bool:
    """Whether text requests a conversion, PPP claim, or cross-currency value comparison."""

    raw = str(text or "")
    if not _COMPARISON_RE.search(raw):
        return False
    mentions = _MONEY_MENTION_RE.findall(raw)
    return len(mentions) >= 2 or bool(
        re.search(r"\b(?:exchange\s+rates?|fx|ppp|purchasing\s+power)\b", raw, re.IGNORECASE)
    )


def has_user_supplied_fx_rates(text: str) -> bool:
    """True only for an explicit numeric rate equation in the user's own text."""

    return bool(_SUPPLIED_FX_RATE_RE.search(str(text or "")))


def _identity_codes(text: str) -> list[str]:
    raw = str(text or "")
    return [code for code in STATIC_CURRENCY_IDENTITIES if re.search(rf"\b{code}\b", raw)]


def _has_non_identity_currency_work(text: str) -> bool:
    raw = str(text or "")
    if has_user_supplied_fx_rates(raw) or _NON_IDENTITY_CURRENCY_WORK_RE.search(raw):
        return True
    from core.currency_travel_spend import travel_spend_intent

    return travel_spend_intent(raw) is not None


def static_currency_identity_admitted(text: str) -> bool:
    """Whether the whole request is bounded to stable code identity only.

    Mentioning one registered code is evidence for that token's identity, not whole-turn coverage.
    Complete travel, purchase, conversion, or arithmetic work must continue to its owning lane even
    when the same text also asks to name currencies.
    """

    raw = str(text or "")
    if not raw.strip() or not _identity_codes(raw) or not _IDENTITY_CONTEXT_RE.search(raw):
        return False
    return not _has_non_identity_currency_work(raw)


def _identity_reply(text: str) -> CurrencyValueReply | None:
    if not static_currency_identity_admitted(text):
        return None
    codes = _identity_codes(text)
    lines = [f"{code} = {STATIC_CURRENCY_IDENTITIES[code]}" for code in codes]
    return CurrencyValueReply("\n".join(lines), "static_currency_identity")


def _recent_comparison(current_text: str, source_context: dict[str, Any] | None) -> str:
    if asks_for_dynamic_currency_value(current_text):
        return current_text
    if not _NO_LIVE_FOLLOWUP_RE.search(str(current_text or "")):
        return ""
    for item in reversed(list((source_context or {}).get("conversation_history") or [])):
        if not isinstance(item, dict) or str(item.get("role") or "").lower() != "user":
            continue
        candidate = str(item.get("content") or "")
        if candidate != current_text and asks_for_dynamic_currency_value(candidate):
            return candidate
    return ""


def _currency_bindings(text: str) -> list[str]:
    """Bind symbols only when the request itself supplies a geographic/code anchor."""

    lowered = str(text or "").lower()
    lines: list[str] = []
    if re.search(r"\b\d+(?:[.,]\d+)?\s*kr\b", text, re.IGNORECASE):
        if "copenhagen" in lowered or "denmark" in lowered or re.search(r"\bDKK\b", text):
            lines.append("`kr` binds to DKK (Danish krone) from the Copenhagen/Denmark anchor.")
        else:
            lines.append("`kr` is unresolved without a country anchor; several currencies use it.")
    if "¥" in text:
        if "shanghai" in lowered or "china" in lowered or re.search(r"\b(?:CNY|RMB)\b", text):
            lines.append("`¥` binds to CNY/RMB (Chinese yuan) from the Shanghai/China anchor.")
        elif "japan" in lowered or "tokyo" in lowered or re.search(r"\bJPY\b", text):
            lines.append("`¥` binds to JPY (Japanese yen) from the Japan/Tokyo anchor.")
        else:
            lines.append("`¥` is unresolved without a China/Japan anchor.")
    if "$" in text:
        explicit_codes = re.findall(r"\b(?:USD|CAD|AUD|NZD|SGD|HKD)\b", text)
        if len(set(explicit_codes)) == 1:
            code = explicit_codes[0]
            lines.append(f"`$` binds to {code} because that code is explicit in the request.")
        else:
            lines.append("`$` remains ambiguous; the prompt does not establish USD rather than another dollar currency.")
    return lines


def _unsupported_dynamic_reply(text: str) -> CurrencyValueReply:
    bindings = _currency_bindings(text)
    lines = ["Static currency identity:"]
    lines.extend(f"- {line}" for line in bindings)
    lines.extend(
        [
            "",
            "Dynamic value:",
            "- FX compares currencies using exchange rates at a stated time. No live or user-supplied FX rates are available here, so I cannot give exact conversions or rank these amounts.",
            "- PPP compares what money buys locally, not its market exchange value. A numeric PPP comparison needs a named PPP dataset or a user-supplied basket with local prices; neither is available here.",
        ]
    )
    return CurrencyValueReply("\n".join(lines), "dynamic_currency_value_unavailable")


def maybe_answer_currency_value(
    text: str,
    *,
    source_context: dict[str, Any] | None = None,
) -> CurrencyValueReply | None:
    """Answer stable identity locally; refuse unsupported dynamic values deterministically.

    An explicit rate equation is allowed to continue to the ordinary supplied-data lane.  This
    guard owns only the dangerous no-evidence case; it never overwrites rates the user provided.
    """

    # A code-shaped token can also be an ordinary English word.  When the user explicitly asks
    # for the phrase's plain-English meaning, that requested semantic frame is more specific than
    # the generic currency-identity guard below.  Resolve it here because this guard runs before
    # the ordinary stable-reference front door in the shipped agent.
    from core.stable_currency_reference import stable_currency_reference_response

    stable_reference = (
        None
        if _has_non_identity_currency_work(text)
        else stable_currency_reference_response(text)
    )
    if stable_reference is not None:
        return CurrencyValueReply(stable_reference, "stable_currency_reference_contract")

    identity = _identity_reply(text)
    if identity is not None and not asks_for_dynamic_currency_value(text):
        return identity

    comparison = _recent_comparison(text, source_context)
    if not comparison:
        return None
    if has_user_supplied_fx_rates(text) or has_user_supplied_fx_rates(comparison):
        return None
    return _unsupported_dynamic_reply(comparison)


__all__ = [
    "STATIC_CURRENCY_IDENTITIES",
    "CurrencyValueReply",
    "asks_for_dynamic_currency_value",
    "has_user_supplied_fx_rates",
    "maybe_answer_currency_value",
    "static_currency_identity_admitted",
]
