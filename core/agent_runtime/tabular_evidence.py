"""Exact arithmetic over complete delimited records, without inferred column meanings."""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_COLUMNS = 32
MAX_GROUPS = 64
MAX_FACT_CHARS = 4000
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def file_question_requires_synthesis(text: str, path: str) -> bool:
    from core.turn_ir import ClauseKind, parse_turn_ir

    kinds = {clause.kind for clause in parse_turn_ir(text, response_shape_parser=None).clauses}
    kinds.discard(ClauseKind.CONSTRAINT)
    if kinds & {ClauseKind.COMPUTE, ClauseKind.TRANSFORM}:
        return True
    if str(path).casefold().endswith((".csv", ".tsv")):
        return bool(kinds - {ClauseKind.OBSERVE, ClauseKind.ACT})
    return False


def _number(value: str) -> Decimal | None:
    value = value.strip()
    if len(value) > 80 or not _NUMBER.fullmatch(value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() and abs(number.as_tuple().exponent) <= 32 else None


def _format_units(value: int, scale: int) -> str:
    if not scale:
        return str(value)
    digits = str(abs(value)).rjust(scale + 1, "0")
    rendered = (digits[:-scale] + "." + digits[-scale:]).rstrip("0").rstrip(".")
    return ("-" if value < 0 else "") + rendered


def _integer_units(values: list[Decimal]) -> tuple[list[int], int]:
    scale = max(0, *(-value.as_tuple().exponent for value in values))
    units = []
    for value in values:
        parts = value.as_tuple()
        coefficient = int("".join(map(str, parts.digits)) or "0")
        units.append((-1 if parts.sign else 1) * coefficient * 10 ** (scale + parts.exponent))
    return units, scale


def _equal_share_ledger(paid: dict[str, int], scale: int) -> dict[str, Any]:
    labels = sorted(paid)
    base, remainder = divmod(sum(paid.values()), len(labels))
    shares = {label: base + int(index < remainder) for index, label in enumerate(labels)}
    balances = {label: paid[label] - shares[label] for label in labels}
    debtors = [[label, -value] for label, value in balances.items() if value < 0]
    creditors = [[label, value] for label, value in balances.items() if value > 0]
    transfers = []
    debtor = creditor = 0
    while debtor < len(debtors) and creditor < len(creditors):
        amount = min(debtors[debtor][1], creditors[creditor][1])
        transfers.append({"from": debtors[debtor][0], "to": creditors[creditor][0],
                          "amount": _format_units(amount, scale)})
        debtors[debtor][1] -= amount
        creditors[creditor][1] -= amount
        debtor += int(debtors[debtor][1] == 0)
        creditor += int(creditors[creditor][1] == 0)
    assert not any(value for _label, value in debtors + creditors)
    return {"unit": _format_units(1, scale),
            "shares": {label: _format_units(value, scale) for label, value in shares.items()},
            "remainder_rule": "earlier sorted labels receive one remaining input-precision unit",
            "transfers": transfers}


def table_facts(text: str) -> dict[str, Any] | None:
    """Require a rectangular headered table and complete finite numeric columns.

    Amounts never pass through binary floats. Labels keep their exact spelling; no column
    is presumed to name a person, currency or obligation. Equal splits are alternatives
    under an explicit assumption, never an inferred liability.
    """
    text = text.removeprefix("\ufeff")
    if "\ufffd" in text:
        return None
    try:
        dialect = (csv.Sniffer().sniff(text[:16384], delimiters=",\t;|")
                   if any(delimiter in text.splitlines()[0] for delimiter in ",\t;|")
                   else csv.excel)
        rows = [row for row in csv.reader(io.StringIO(text), dialect, strict=True) if row]
    except (csv.Error, IndexError):
        return None
    if len(rows) < 2:
        return None
    headers, data = rows[0], rows[1:]
    if (not 1 <= len(headers) <= MAX_COLUMNS or len(set(headers)) != len(headers)
            or any(not header.strip() or len(header) > 128 or _NUMBER.fullmatch(header.strip()) is not None for header in headers)
            or any(len(row) != len(headers) for row in data)):
        return None
    numeric: dict[int, tuple[list[int], int]] = {}
    uncomputed = []
    seen_number = False
    for index, header in enumerate(headers):
        values = [_number(row[index]) for row in data]
        seen_number |= any(value is not None for value in values)
        if all(value is not None for value in values):
            numeric[index] = _integer_units([value for value in values if value is not None])
        else:
            uncomputed.append(header)
    if not seen_number:
        return None
    facts: dict[str, Any] = {
        "totals": {headers[index]: _format_units(sum(values), scale)
                   for index, (values, scale) in numeric.items()},
        "group_totals": {}, "hypothetical_equal_shares": {},
        "row_count": len(data), "header_row": 1, "uncomputed_columns": uncomputed,
        "assumption": "Equal splits treat distinct labels in one dimension as equally sharing its numeric total. Units are not converted.",
        "omitted_group_dimension_count": 0,
    }
    if len(json.dumps(facts, ensure_ascii=False)) > MAX_FACT_CHARS:
        return None
    dimensions = [(len({row[index] for row in data}), index) for index in range(len(headers))]
    for count, dimension in sorted(dimensions):
        labels = sorted({row[dimension] for row in data})
        measures = {index: value for index, value in numeric.items() if index != dimension}
        if count < 2 or not measures:
            continue
        name = headers[dimension]
        if count > MAX_GROUPS or any(not label.strip() or len(label) > 128 for label in labels):
            facts["omitted_group_dimension_count"] += 1
            continue
        groups = {label: {} for label in labels}
        ledgers = {}
        for measure, (values, scale) in measures.items():
            paid = {label: sum(value for row, value in zip(data, values, strict=True)
                               if row[dimension] == label) for label in labels}
            for label, value in paid.items():
                groups[label][headers[measure]] = _format_units(value, scale)
            ledgers[headers[measure]] = _equal_share_ledger(paid, scale)
        facts["group_totals"][name] = groups
        facts["hypothetical_equal_shares"][name] = ledgers
        if len(json.dumps(facts, ensure_ascii=False)) > MAX_FACT_CHARS:
            del facts["group_totals"][name]
            del facts["hypothetical_equal_shares"][name]
            facts["omitted_group_dimension_count"] += 1
    return facts if len(json.dumps(facts, ensure_ascii=False)) <= MAX_FACT_CHARS else None
