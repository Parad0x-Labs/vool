"""Source-bound price assignments for existing arithmetic and dependency planning."""
from __future__ import annotations

import math
import re

from core.conductor.quantity_units import source_quantities
from core.semantic.canonical_text import CanonicalText
from core.semantic.preflight import _quote_spans


def price_source_scope(arguments, clause: str, context) -> str:
    span = arguments.get("price_source_scope")
    if (context is not None and isinstance(span, (list, tuple)) and len(span) == 2
            and all(type(offset) is int for offset in span)
            and 0 <= span[0] < span[1] <= len(context.original_request)):
        return context.original_request[span[0]:span[1]]
    return clause


def supplied_prices(context, clause: str) -> dict[str, dict]:
    from core.agent_runtime.live_data_plan import _resolve_price_alias
    from core.conductor.operations import (
        _COMMODITY_UNITS,
        _FRESH_OR_PLACE_KNOWLEDGE_RE,
        _QUANTITY_UNITS,
    )

    if context is None or _FRESH_OR_PLACE_KNOWLEDGE_RE.search(clause):
        return {}
    source = context.original_request
    quoted, unfinished_quote = _quote_spans(CanonicalText.of(source))
    quantities = source_quantities(context, _QUANTITY_UNITS)
    result = {}
    for fact in context.facts:
        start = source.find(fact.raw, fact.position)
        if start < 0:
            continue
        if any(span.start <= start < span.end for span in quoted) or (
            unfinished_quote is not None and unfinished_quote.start <= start < unfinished_quote.end
        ):
            continue
        line_start = source.rfind("\n", 0, start) + 1
        prefix = source[line_start:start]
        match = re.fullmatch(r"\s*(?:[-*+]\s+)?(?P<name>[^=\n]+?)\s*=\s*(?P<sign>[+-]?)", prefix)
        if match is None:
            continue
        role = _resolve_price_alias(match["name"].strip().casefold())
        if role is None:
            continue
        key, kind, entity = role
        quantity = quantities[fact.label]
        currency, separator, denominator = quantity.unit.partition("/")
        expected = _QUANTITY_UNITS.get(_COMMODITY_UNITS.get(key, ""))
        actual = next((entry for entry in _QUANTITY_UNITS.values() if entry[0] == denominator), None)
        problem = ""
        if (not separator or len(currency) != 3 or not currency.isupper()
                or expected is None or actual is None or actual[1] != expected[1]
                or fact.value <= 0 or not math.isfinite(fact.value) or match["sign"] == "-"):
            problem = f"the supplied price for {entity} needs a positive value with unambiguous currency and price unit"
        value = fact.value * expected[2] / actual[2] if not problem else None
        if value is not None and not math.isfinite(value):
            problem = f"the supplied price for {entity} exceeds the supported numeric range"
            value = None
        binding = {
            "asset_key": key, "entity": entity, "kind": kind,
            "price": value, "currency": currency, "source": "user_supplied",
            "source_url": "", "retrieved_at": "", "fact_label": fact.label,
            "source_span": [line_start, source.find("\n", start) if "\n" in source[start:] else len(source)],
            "source_unit": quantity.unit, "source_value": fact.value,
            "price_unit": expected[0] if expected else "", "problem": problem,
        }
        if key in result:
            binding["problem"] = f"more than one supplied price assignment names {entity}; clarify which one applies"
        result[key] = binding
    return result
