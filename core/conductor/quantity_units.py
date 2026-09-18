"""Unit provenance for the existing grounded arithmetic evaluator.

This module does not calculate values or authorize numeric literals. It carries
the dimensions established by source quantities through already-admitted arithmetic.
Unknown dimensions stay unknown; they are not treated as dimensionless.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping

from core.conductor.shared_context import _CANONICAL_UNIT, SharedTurnContext
from core.semantic.request_graph import Quantity


class IncompatibleQuantityUnitsError(ValueError):
    pass


def explicit_physical_unit_pattern(alias: str, canonical: str) -> str | None:
    """Use the same explicit qualifications for source units and conversion symbols."""
    if alias in {"oz", "ounce", "ounces", "pound", "pounds", "ton", "tons"}:
        return None
    pattern = re.escape(alias).replace(r"\ ", r"\s+")
    if canonical == "barrel":
        return r"(?:oil|petroleum)\s+" + pattern
    if canonical == "gal":
        return r"(?:US|U\.S\.)\s+" + pattern
    return pattern


def source_quantities(context: SharedTurnContext, unit_table: Mapping) -> dict[str, Quantity]:
    result = {}
    resolutions = {a.token: a.resolved for a in context.ambiguities if a.resolved}
    currency_codes = {code for code in _CANONICAL_UNIT.values() if len(code) == 3 and code.isupper()}
    aliases = sorted(unit_table, key=len, reverse=True)
    def physical_prefix(text):
        for alias in aliases:
            unit_pattern = explicit_physical_unit_pattern(alias, unit_table[alias][0])
            if unit_pattern is None:
                continue
            pattern = r"\s*" + unit_pattern + r"(?!\w)"
            if re.match(pattern, text, re.IGNORECASE):
                return unit_table[alias][0]
        return ""

    for fact in context.facts:
        start = context.original_request.find(fact.raw, fact.position)
        end = start + len(fact.raw) if start >= 0 else fact.position
        before = context.original_request[:start if start >= 0 else fact.position]
        after = context.original_request[end:]
        currency = resolutions.get(fact.unit, fact.unit) if fact.unit_kind == "currency" else ""
        if fact.unit_kind != "percent":
            suffix = re.search(r"\b([A-Za-z]{3})$", fact.raw)
            adjacent = re.match(r"\s*([A-Za-z]{3})(?!\w)", after)
            if suffix and suffix[1].upper() in currency_codes:
                currency = suffix[1].upper()
            elif adjacent and adjacent[1].upper() in currency_codes:
                currency = adjacent[1].upper()
                after = after[adjacent.end():]
        unit = "ratio" if fact.unit_kind == "percent" else currency
        pair = re.search(r"\b([A-Z]{3})\s*/\s*([A-Z]{3})\s*=\s*$", before)
        if pair and not currency and pair[1] in currency_codes and pair[2] in currency_codes:
            # BASE/QUOTE states quote currency per one base currency.
            unit = pair[2] + "/" + pair[1]
        elif currency:
            per = re.match(r"\s*(?:per\b|/)\s*(.*)", after, re.IGNORECASE)
            denominator = physical_prefix(per[1]) if per else ""
            if denominator:
                unit = currency + "/" + denominator
        else:
            unit = unit or physical_prefix(after)
        result[fact.label] = Quantity(raw=fact.raw, exact=str(fact.value), unit=unit)
        if fact.unit_kind == "percent":
            result[fact.label + "_share"] = Quantity(
                raw=fact.raw, exact=str(fact.value / 100), unit="ratio")
    return result


def dimensions(unit: str) -> dict[str, int] | None:
    if not unit:
        return None
    if unit == "ratio":
        return {}
    numerator, _, denominator = unit.partition("/")
    result = {numerator: 1}
    if denominator:
        result[denominator] = result.get(denominator, 0) - 1
    return {name: exponent for name, exponent in result.items() if exponent}


def literal_dimensions(quantities: Mapping[str, Quantity]) -> dict[float, dict[str, int] | None]:
    result = {}
    for quantity in quantities.values():
        number = float(quantity.exact)
        unit = dimensions(quantity.unit)
        if number not in result:
            result[number] = unit
        elif result[number] != unit:
            result[number] = None
    return result


def expression_dimensions(expression: str, bindings: Mapping[str, dict[str, int] | None],
                          literals: Mapping[float, dict[str, int] | None] | None = None):
    def walk(node):
        if isinstance(node, ast.Name):
            return bindings.get(node.id)
        if isinstance(node, ast.Constant):
            if node.value in (0, 1, 100):
                return {}
            return (literals or {}).get(float(node.value), {})
        if isinstance(node, ast.UnaryOp):
            return walk(node.operand)
        if not isinstance(node, ast.BinOp):
            return None
        left, right = walk(node.left), walk(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if isinstance(node.left, ast.Constant) and node.left.value == 0:
                return dict(right)
            if isinstance(node.right, ast.Constant) and node.right.value == 0:
                return dict(left)
            if left != right:
                raise IncompatibleQuantityUnitsError("cannot add or subtract quantities with different units")
            return dict(left)
        if isinstance(node.op, (ast.Mult, ast.Div)):
            result = dict(left)
            sign = -1 if isinstance(node.op, ast.Div) else 1
            for name, exponent in right.items():
                result[name] = result.get(name, 0) + sign * exponent
            return {name: exponent for name, exponent in result.items() if exponent}
        return None
    return walk(ast.parse(expression, mode="eval").body)


def display_unit(value: dict[str, int]) -> str:
    names = {"troy oz": "troy ounces"}
    def term(name, exponent):
        label = names.get(name, name)
        return label if exponent == 1 else f"{label}^{exponent}"
    numerator = [term(name, exponent) for name, exponent in sorted(value.items()) if exponent > 0]
    denominator = [term(name, -exponent) for name, exponent in sorted(value.items()) if exponent < 0]
    top = " * ".join(numerator)
    return (top or "1") + " / " + " * ".join(denominator) if denominator else top
