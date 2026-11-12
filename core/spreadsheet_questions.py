"""Answer plain money questions about one table file by reading the file itself.

An ask like "how much did i spend in october, not counting the salary, in my bank csv" is a
question about the owner's own rows, and only the file can answer it — anything else would be a
guess dressed up as a total. This module is that answer: it opens one csv read-only, works out
from the cells themselves which columns hold the dates and the money (bank exports arrive under
logo lines, with semicolons, decimal commas and dd/mm dates), adds the rows the ask points at
with :class:`~decimal.Decimal` so a cent is never silently rounded away, and answers in plain
words that end with a Rule line naming the columns, the date filter and the left-out words, so
the owner can check every number against the file.

When the file does not say — no date column behind a month ask, two columns that could both be
the money, no rows in the month asked — the engine fails closed with a plain question back
instead of a total.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from core.execution.constants import SpreadsheetQuestion

# Encodings in the order bank exports actually arrive: a utf-8 file with a BOM, a Windows export,
# and finally latin-1, which decodes anything left and only loses on truly exotic files.
_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_DELIMITERS = (",", ";", "\t", "|")
# A column speaks for a role when this share of its filled cells parse; below that it is prose.
_QUALIFY = 0.6
# The reply lists at most this many rows, then says how many more there are.
_MAX_SHOWN_LINES = 15
# Bank exports love a logo line and an account line above the column names; that much preamble is
# dropped when the first row plans no date and no money column.
_MAX_PREAMBLE_ROWS = 3
# Sampling caps: a role decision never needs more than this many filled cells, and huge exports
# should not pay for scanning a whole column just to learn its shape.
_SAMPLE_CELLS = 400

_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December")
_MONTHS = {name.lower(): number + 1 for number, name in enumerate(_MONTH_NAMES)}


class ColumnPlan(NamedTuple):
    """What the columns of one sheet turned out to be, and how that file writes money.

    ``amount_column`` is the single signed column when there is one; a debit/credit pair is
    carried as ``debit_column`` (money out) plus ``credit_column`` (money in) with
    ``amount_column`` left empty, because the two sides are the file's own split of the money.
    ``currency``/``decimals``/``decimal_comma``/``symbol_suffix``/``grouped`` record the file's
    money style so the answer renders the way the file does.
    """

    date_column: str | None
    amount_column: str | None
    debit_column: str | None
    credit_column: str | None
    description_column: str | None
    currency: str | None
    decimals: int
    decimal_comma: bool
    symbol_suffix: bool
    grouped: bool


@dataclass(frozen=True)
class SheetSummary:
    """The complete answer to one spreadsheet question; ``reply`` is ready to show as-is.

    ``ok`` is False only for the clarify/miss replies. ``counted_lines`` holds one short line per
    counted row (the reply shows the first 15 of them and then "and N more"). Both money texts
    are always filled on a success — exact sums over the rows that pass the date and exclusion
    filters, written in the file's own symbol, decimals and separator style — while the reply
    shows only the side(s) the ask asked for.
    """

    ok: bool
    reply: str
    file_label: str
    rows_read: int
    rows_counted: int
    counted_lines: tuple[str, ...]
    total_in_text: str | None
    total_out_text: str | None
    needs_column_choice: bool
    date_column: str | None
    amount_column: str | None


class _MoneyStyle(NamedTuple):
    currency: str | None
    decimals: int
    comma: bool
    suffix: bool
    grouped: bool


class _Amount(NamedTuple):
    value: Decimal
    decimals: int
    comma: bool
    grouped: bool
    currency: str | None
    suffix: bool


class _RowEntry(NamedTuple):
    when: date | None
    stems: frozenset[str]
    out: Decimal | None
    incoming: Decimal | None
    raw_date: str
    description: str


class _ColumnPlan(NamedTuple):
    """Column roles by index; the public :class:`ColumnPlan` carries the same plan by name."""

    date_idx: int | None
    amount_idx: int | None
    debit_idx: int | None
    credit_idx: int | None
    description_idx: int | None
    style: _MoneyStyle


# ---------------------------------------------------------------------------------------------
# Cell lexers: dates, amounts, stems
# ---------------------------------------------------------------------------------------------


def _stem(word: str) -> str:
    """The classifier's light stem, mirrored here so both sides of an exclusion compare the same
    way: the ask's "member" must meet the file's "members"."""
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


_DATE_LIKE_RE = re.compile(r"^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$|^\d{4}-\d{1,2}-\d{1,2}$")
# dd/mm is assumed over mm/dd: "13/10" only reads one way, and the exports this serves are
# European. A trailing time in the same cell ("2026-10-03 14:22") still parses.
_DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y")


def _parse_date(raw: str) -> tuple[date, str] | None:
    """One cell as ``(date, the file's own format)``, or ``None`` when it is not a date."""
    token = raw.strip().split(" ")[0] if raw.strip() else ""
    if not _DATE_LIKE_RE.match(token):
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(token, fmt).date(), fmt
        except ValueError:
            continue
    return None


_CURRENCY_CHARS = "€£$¥"
_CURRENCY_RE_FRAGMENT = "[€£$¥]|EUR|USD|GBP"
_CURRENCY_PREFIX_RE = re.compile(rf"^(?P<cur>{_CURRENCY_RE_FRAGMENT})\s*", re.IGNORECASE)
_CURRENCY_SUFFIX_RE = re.compile(rf"\s*(?P<cur>{_CURRENCY_RE_FRAGMENT})$", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d+")
_SEPARATORS_RE = re.compile(r"[.,]")
_NUMERIC_BODY_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _strip_currency(cell: str) -> tuple[str, str | None, bool]:
    """Lift a leading or trailing currency mark off a cell; ``suffix`` says which side it sat on."""
    if (match := _CURRENCY_PREFIX_RE.match(cell)) is not None:
        return cell[match.end():].strip(), _currency_name(match.group("cur")), False
    if (match := _CURRENCY_SUFFIX_RE.search(cell)) is not None:
        return cell[:match.start()].strip(), _currency_name(match.group("cur")), True
    return cell, None, False


def _currency_name(mark: str) -> str:
    return mark if mark in _CURRENCY_CHARS else mark.upper()


def _parse_amount(raw: str) -> _Amount | None:
    """One cell as an exact amount, or ``None`` when it holds no money.

    Separator law, straight from how bank exports write money: with both separators present the
    LAST one is the decimal one ("1.234,56"); a single separator followed by one or two digits is
    a decimal ("41,20"); anything else ("1,234", "1.234,567") is grouping. Parentheses and a
    trailing minus ("41,20-") both mean negative, as does the usual leading minus.
    """
    cell = raw.strip()
    if not cell:
        return None
    negative = False
    if len(cell) >= 3 and cell.startswith("(") and cell.endswith(")"):
        cell, negative = cell[1:-1].strip(), True
    cell, currency, suffix = _strip_currency(cell)
    if cell.endswith("-") and len(cell) > 1:
        cell, negative = cell[:-1].strip(), True
    if len(cell) > 1 and cell[0] in "+-":
        negative = negative or cell[0] == "-"
        cell = cell[1:].strip()
    # A mark can sit inside the signs too ("-€41.20").
    if currency is None:
        cell, currency, suffix = _strip_currency(cell)
    if not _NUMERIC_BODY_RE.fullmatch(cell):
        return None
    groups = _DIGITS_RE.findall(cell)
    separators = _SEPARATORS_RE.findall(cell)
    digits = "".join(groups)
    decimals, comma, grouped = 0, False, False
    if separators:
        if len(set(separators)) == 2:
            decimals = len(groups[-1])
            comma = separators[-1] == ","
            grouped = len(separators) > 1
        elif len(separators) == 1:
            if len(groups[-1]) <= 2:
                decimals = len(groups[-1])
                comma = separators[0] == ","
            else:
                grouped = True
        else:
            grouped = True
    value = Decimal(digits).scaleb(-decimals)
    return _Amount(-value if negative else value, decimals, comma, grouped, currency, suffix)


# ---------------------------------------------------------------------------------------------
# Column shape
# ---------------------------------------------------------------------------------------------


def _cells(rows: list[list[str]], idx: int):
    for row in rows:
        if idx < len(row):
            cell = (row[idx] or "").strip()
            if cell:
                yield cell


def _date_fill(rows: list[list[str]], idx: int) -> tuple[int, int]:
    parsed = filled = 0
    for cell in _cells(rows, idx):
        filled += 1
        if _parse_date(cell) is not None:
            parsed += 1
        if filled >= _SAMPLE_CELLS:
            break
    return parsed, filled


def _amount_fill(rows: list[list[str]], idx: int) -> tuple[int, int]:
    parsed = filled = 0
    for cell in _cells(rows, idx):
        filled += 1
        if _parse_amount(cell) is not None:
            parsed += 1
        if filled >= _SAMPLE_CELLS:
            break
    return parsed, filled


def _qualifies(parsed: int, filled: int) -> bool:
    return filled > 0 and parsed / filled >= _QUALIFY


_DATE_HEADER_RE = re.compile(
    r"\b(?:dates?|day|when|time|times|timestamp|timestamps|booking|booked|posted|valuta)\b",
    re.IGNORECASE,
)


def _find_date_column(names: list[str], rows: list[list[str]]) -> int | None:
    """The date column: a header hint decides between several columns that parse (bank exports
    also carry a "value date" nobody asked about); otherwise the first column that parses wins."""
    hinted = plain = None
    for idx in range(len(names)):
        parsed, filled = _date_fill(rows, idx)
        if not _qualifies(parsed, filled):
            continue
        if plain is None:
            plain = idx
        if hinted is None and _DATE_HEADER_RE.search(names[idx]):
            hinted = idx
    return hinted if hinted is not None else plain


_AMOUNT_HEADER_RE = re.compile(r"\b(?:amounts?|values?|sum)\b", re.IGNORECASE)
# Each pair is (money out side, money in side) in the words bank exports use.
_PAIR_HINTS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"\bdebits?\b", re.IGNORECASE), re.compile(r"\bcredits?\b", re.IGNORECASE)),
    (re.compile(r"\bpaid\s+out\b", re.IGNORECASE), re.compile(r"\bpaid\s+in\b", re.IGNORECASE)),
    (re.compile(r"\bwithdraw\w*\b", re.IGNORECASE), re.compile(r"\bdeposit\w*\b", re.IGNORECASE)),
    (re.compile(r"\baf\b", re.IGNORECASE), re.compile(r"\bbij\b", re.IGNORECASE)),
    (re.compile(r"\bout\b", re.IGNORECASE), re.compile(r"\bin\b", re.IGNORECASE)),
)
_PAIR_SIDE_RES = [pattern for pair in _PAIR_HINTS for pattern in pair]


def _first_header_match(names: list[str], pattern: re.Pattern[str], skip: set[int]) -> int | None:
    for idx, name in enumerate(names):
        if idx not in skip and pattern.search(name):
            return idx
    return None


def _hinted_amount_column(names: list[str], rows: list[list[str]], date_idx: int | None) -> int | None:
    """The one signed money column its own header names ("Amount", "Value", "Sum").

    A header like "Amount out" names one SIDE of a pair, not the whole money story, so pair-side
    names are left to the pair check instead of being read as the signed column.
    """
    for idx, name in enumerate(names):
        if idx == date_idx or not _AMOUNT_HEADER_RE.search(name):
            continue
        if any(pattern.search(name) for pattern in _PAIR_SIDE_RES):
            continue
        if _qualifies(*_amount_fill(rows, idx)):
            return idx
    return None


def _find_pair(names: list[str], rows: list[list[str]], date_idx: int | None) -> tuple[int, int] | None:
    """A debit/credit-style pair of columns: money out in one, money in in the other."""
    for out_re, in_re in _PAIR_HINTS:
        out_idx = _first_header_match(names, out_re, {date_idx})
        in_idx = _first_header_match(names, in_re, {date_idx, out_idx})
        if out_idx is None or in_idx is None:
            continue
        if _qualifies(*_amount_fill(rows, out_idx)) and _qualifies(*_amount_fill(rows, in_idx)):
            return out_idx, in_idx
    return None


def _plausible_amount_columns(header: list[str], rows: list[list[str]],
                              date_idx: int | None) -> list[int]:
    """Every column that could be the money when no header names it — the columns the clarify
    reply has to offer the owner."""
    return [idx for idx in range(len(header))
            if idx != date_idx and _qualifies(*_amount_fill(rows, idx))]


_DESC_HEADER_RE = re.compile(
    r"\b(?:descriptions?|details?|narrative|memo|payee|merchant|name|reference|note|notes|"
    r"label|item|purpose|omschrijving|booking text)\b",
    re.IGNORECASE,
)


def _find_description(names: list[str], rows: list[list[str]], taken: set[int]) -> int | None:
    hinted = None
    fallback, fallback_fill = None, 0
    for idx in range(len(names)):
        if idx in taken:
            continue
        filled = sum(1 for _ in _cells(rows, idx))
        if not filled:
            continue
        if hinted is None and _DESC_HEADER_RE.search(names[idx]):
            hinted = idx
        if filled > fallback_fill:
            fallback, fallback_fill = idx, filled
    return hinted if hinted is not None else fallback


def _money_style(rows: list[list[str]], indexes: list[int]) -> _MoneyStyle:
    """How this file writes money, decided by majority vote of its own amount cells, so the
    answer's totals come out in the file's own hand."""
    decimals = 0
    comma = dot = suffix = prefix = 0
    grouped = False
    votes: dict[str, int] = {}
    for idx in indexes:
        seen = 0
        for cell in _cells(rows, idx):
            amount = _parse_amount(cell)
            if amount is None:
                continue
            seen += 1
            decimals = max(decimals, amount.decimals)
            comma, dot = comma + amount.comma, dot + (not amount.comma)
            grouped = grouped or amount.grouped
            if amount.currency is not None:
                votes[amount.currency] = votes.get(amount.currency, 0) + 1
                if amount.suffix:
                    suffix += 1
                else:
                    prefix += 1
            if seen >= _SAMPLE_CELLS:
                break
    currency = max(votes, key=votes.get) if votes else None
    return _MoneyStyle(currency, decimals, comma > dot, suffix > prefix, grouped)


def _plan_columns(header: list[str], rows: list[list[str]]) -> _ColumnPlan:
    """The column roles of one sheet by index, from the header hints first and the cells second."""
    names = _display_names(header)
    date_idx = _find_date_column(names, rows)
    amount_idx = _hinted_amount_column(names, rows, date_idx)
    debit_idx = credit_idx = None
    if amount_idx is None:
        pair = _find_pair(names, rows, date_idx)
        if pair is not None:
            debit_idx, credit_idx = pair
        else:
            plausible = _plausible_amount_columns(header, rows, date_idx)
            if len(plausible) == 1:
                amount_idx = plausible[0]
    taken = {date_idx, amount_idx, debit_idx, credit_idx} - {None}
    description_idx = _find_description(names, rows, taken)
    money_idx = [idx for idx in (amount_idx, debit_idx, credit_idx) if idx is not None]
    return _ColumnPlan(date_idx, amount_idx, debit_idx, credit_idx, description_idx,
                       _money_style(rows, money_idx))


def _display_names(header: list[str]) -> list[str]:
    """Column names the way a person reads them; a bank export's nameless columns still need
    something to be called in the reply and the Rule line."""
    names = []
    for idx, cell in enumerate(header):
        name = (cell or "").strip()
        names.append(name if name else f"column {idx + 1}")
    return names


def plan_columns(header: list[str], rows: list[list[str]]) -> ColumnPlan:
    """Work out which columns of one parsed sheet hold the dates, the money and the description.

    Header hints lead ("Amount", a debit/credit pair); without them exactly one numeric column is
    the money, and when several could be, ``amount_column`` stays empty and the caller must ask
    the owner which one to add up.
    """
    header_names = _display_names(header)
    plan = _plan_columns(header, rows)
    style = plan.style
    return ColumnPlan(
        header_names[plan.date_idx] if plan.date_idx is not None else None,
        header_names[plan.amount_idx] if plan.amount_idx is not None else None,
        header_names[plan.debit_idx] if plan.debit_idx is not None else None,
        header_names[plan.credit_idx] if plan.credit_idx is not None else None,
        header_names[plan.description_idx] if plan.description_idx is not None else None,
        style.currency, style.decimals, style.comma, style.suffix, style.grouped,
    )


# ---------------------------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------------------------


def _display_label(path_str: str) -> str:
    """The path the way the owner would say it: home folds to ``~`` so a reply never shows more
    of the disk than the ask already did."""
    try:
        path = Path(path_str).expanduser()
        home = Path.home().expanduser()
        if path.is_relative_to(home):
            return "~/" + str(path.relative_to(home))
    except (OSError, RuntimeError, ValueError):
        pass
    return path_str


def _read_text(path: Path) -> str | None:
    """The file's text, or ``None`` when it cannot be opened at all. An empty but readable file
    returns ``""`` so the caller can say "empty" instead of "unreadable"."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _splits_enough(lines: list[str], delimiter: str) -> bool:
    hits = sum(1 for line in lines if delimiter in line)
    return lines and hits / len(lines) >= 0.5


def _delimiter_score(lines: list[str], delimiter: str) -> tuple[int, int]:
    """How well one delimiter explains these lines: the per-line count it shows most often, and
    on how many lines it shows that count.

    A real delimiter is on the data lines, consistently, the same number of times — so a modal
    count held by many lines wins. Preamble prose lines carry none of it and only dilute; a
    separator that merely sits inside some numbers ("2.100,00" inside a semicolon file) shows up
    on a line or two with no consistent count, and loses.
    """
    counts: dict[int, int] = {}
    for line in lines:
        stripped = re.sub(r'"[^"]*"', "", line)  # quoted fields may hold any separator
        if delimiter in stripped:
            count = stripped.count(delimiter)
            counts[count] = counts.get(count, 0) + 1
    if not counts:
        return (0, 0)
    count, lines_at_count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return (lines_at_count, count)


def _sniff_delimiter(text: str) -> str:
    """One of ``, ; \\t |`` for this file: the csv sniffer when it agrees with the lines,
    otherwise the delimiter with the most consistent per-line count over the first lines."""
    lines = [line for line in text.splitlines()[:40] if line.strip()][:20]
    if not lines:
        return ","
    try:
        guessed = csv.Sniffer().sniff("\n".join(lines), delimiters="".join(_DELIMITERS)).delimiter
    except csv.Error:
        guessed = None
    if guessed in _DELIMITERS and _splits_enough(lines, guessed):
        return guessed
    best, best_score = ",", (0, 0)
    for delimiter in _DELIMITERS:
        score = _delimiter_score(lines, delimiter)
        if score > (0, 0) and score > best_score:
            best, best_score = delimiter, score
    return best


def _csv_rows(text: str) -> list[list[str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = csv.reader(io.StringIO(normalized), delimiter=_sniff_delimiter(text))
    # Fully blank lines are not transactions; they are not read as rows either.
    return [row for row in rows if any((cell or "").strip() for cell in row)]


def _plan_with_preamble(rows: list[list[str]]) -> tuple[list[str], list[list[str]], int, _ColumnPlan]:
    """Find the real header row of a messy bank export.

    Two passes: first only header rows with at least two named columns may plan (an "Account
    12345" line above the real header otherwise plans by accident, reading data rows as its
    header); if nothing plans that way, any row that plans wins. At most three leading rows are
    dropped — a deeper offset is not a csv anyone meant to export.
    """
    fallback = None
    for want_names in (True, False):
        for skip in range(0, min(len(rows), _MAX_PREAMBLE_ROWS + 1)):
            header, data = rows[skip], rows[skip + 1:]
            named = sum(1 for cell in header if (cell or "").strip())
            if want_names and named < 2:
                continue
            plan = _plan_columns(header, data)
            if plan.date_idx is not None or plan.amount_idx is not None or plan.debit_idx is not None:
                return header, data, skip, plan
            if fallback is None:
                fallback = (header, data, skip, plan)
    assert fallback is not None  # rows is non-empty, so the first skip always runs
    return fallback


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------


def _human_day(day: date) -> str:
    return f"{day.day} {_MONTH_NAMES[day.month - 1]} {day.year}"


def _group_thousands(digits: str, separator: str) -> str:
    grouped = ""
    while len(digits) > 3:
        grouped = separator + digits[-3:] + grouped
        digits = digits[:-3]
    return digits + grouped


def _render_amount(value: Decimal, style: _MoneyStyle) -> str:
    """An exact amount in this file's own hand: its decimals, its separator, its symbol on the
    side it wrote it. Quantizing only pads zeros — every addend already has at most this many
    places — so nothing is rounded on the way out either."""
    quantum = Decimal(1).scaleb(-style.decimals)
    quantized = value.quantize(quantum)
    text = format(abs(quantized), f".{style.decimals}f") if style.decimals else format(abs(quantized), "f")
    whole, _, fraction = text.partition(".")
    if style.grouped:
        whole = _group_thousands(whole, "." if style.comma else ",")
    if fraction:
        text = whole + ("," if style.comma else ".") + fraction
    else:
        text = whole
    sign = "-" if quantized < 0 else ""
    if style.currency is None:
        return sign + text
    if style.suffix:
        return f"{sign}{text} {style.currency}"
    # Word currencies take a space; the signs lean against the number the way exports write them.
    if style.currency.isalpha():
        return f"{style.currency} {sign}{text}"
    return f"{style.currency}{sign}{text}"


def _row_line(entry: _RowEntry, money, date_sep: str) -> str:
    """One counted row as a short line: day/month in the file's own date style, the description,
    the amount. The date keeps the file's separator; the year is in the filter, not in every line."""
    if entry.when is not None:
        prefix = f"{entry.when.day:02d}{date_sep}{entry.when.month:02d}"
    else:
        prefix = entry.raw_date[:10]
    description = " ".join(entry.description.split()).upper()
    if len(description) > 30:
        description = description[:29].rstrip() + "..."
    amount = money(entry.out if entry.out is not None else entry.incoming)
    return " ".join(part for part in (prefix, description, amount) if part)


def _rows_word(count: int) -> str:
    return f"{count} row" if count == 1 else f"{count} rows"


def _miss(label: str, reply: str, *, rows_read: int = 0, needs_column_choice: bool = False,
          date_column: str | None = None, amount_column: str | None = None) -> SheetSummary:
    return SheetSummary(False, reply, label, rows_read, 0, (), None, None,
                        needs_column_choice, date_column, amount_column)


def _date_miss(label: str, names: list[str], rows_read: int) -> SheetSummary:
    if not any(names):
        return _miss(label, f'I could not find any column names in "{label}", '
                            f'so I cannot tell where the dates are.')
    listed = ", ".join(f'"{name}"' for name in names[:8]) + (", and more" if len(names) > 8 else "")
    return _miss(label, f'I could not find the dates in "{label}". The columns I found are: '
                        f'{listed}. Which one holds the dates?', rows_read=rows_read)


def _no_period_miss(label: str, rows_read: int, date_column: str | None, amount_column: str | None,
                    entries: list[_RowEntry], period: str) -> SheetSummary:
    days = [entry.when for entry in entries if entry.when is not None]
    covered = f' The dates in the file run from {_human_day(min(days))} to {_human_day(max(days))}.' if days else ""
    return _miss(label, f'There are no rows {period} in "{label}".{covered}', rows_read=rows_read,
                 date_column=date_column, amount_column=amount_column)


# ---------------------------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------------------------


def summarize(path_str: str, question: SpreadsheetQuestion, *,
              file_label: str | None = None) -> SheetSummary:
    """The full plain-words answer to one question about one csv file.

    Reads the file (never writes it), works out its columns, adds the rows the ask points at
    exactly, and returns the reply together with the totals and the rows behind them. Every miss
    — unreadable file, no dates behind a month ask, two possible money columns, an empty month —
    returns ``ok=False`` with a question in plain words instead of a number.
    """
    label = file_label or _display_label(path_str)
    text = _read_text(Path(path_str).expanduser())
    if text is None:
        return _miss(label, f'I could not open "{label}" — please check that the file is still there.')
    if not text.strip():
        return _miss(label, f'"{label}" is empty — there are no rows to add up.')
    rows = _csv_rows(text)
    if not rows:
        return _miss(label, f'"{label}" has no rows I can read.')
    header, data, skipped, plan = _plan_with_preamble(rows)
    if not data:
        return _miss(label, f'"{label}" has column names but no rows under them, '
                            f'so there is nothing to add up.')
    names = _display_names(header)
    rows_read = len(data)
    date_name = names[plan.date_idx] if plan.date_idx is not None else None
    amount_name = names[plan.amount_idx] if plan.amount_idx is not None else None
    pair_names = ([names[plan.debit_idx], names[plan.credit_idx]]
                  if plan.debit_idx is not None else [])

    month_no = _MONTHS.get(question.month.lower()) if question.month else None
    if question.month and month_no is None:
        return _miss(label, f'I could not work out which month "{question.month}" is.',
                     rows_read=rows_read)
    if (month_no or question.date_from or question.date_to) and plan.date_idx is None:
        return _date_miss(label, names, rows_read)
    if plan.amount_idx is None and plan.debit_idx is None:
        plausible = _plausible_amount_columns(header, data, plan.date_idx)
        if len(plausible) >= 2:
            listed = ", ".join(f'"{names[idx]}"' for idx in plausible[:6])
            return _miss(label, f'"{label}" has more than one column that could hold the money: '
                                f'{listed}. Which one should I add up?',
                         rows_read=rows_read, needs_column_choice=True, date_column=date_name)
        return _miss(label, f'I could not find a column with money amounts in "{label}".',
                     rows_read=rows_read, date_column=date_name)

    # Parse every row once: its date, its money, and the stems its text is matched against.
    entries: list[_RowEntry] = []
    for row in data:
        cells = [(cell or "").strip() for cell in row]
        joined = " ".join(cell for cell in cells if cell).lower()
        stems = frozenset(_stem(word) for word in re.findall(r"[a-z0-9']+", joined))
        when = None
        if plan.date_idx is not None and plan.date_idx < len(cells):
            parsed = _parse_date(cells[plan.date_idx])
            when = parsed[0] if parsed else None
        out = incoming = None
        if plan.amount_idx is not None and plan.amount_idx < len(cells):
            amount = _parse_amount(cells[plan.amount_idx])
            if amount is not None and amount.value < 0:
                out = -amount.value
            elif amount is not None and amount.value > 0:
                incoming = amount.value
        else:
            if plan.debit_idx is not None and plan.debit_idx < len(cells):
                amount = _parse_amount(cells[plan.debit_idx])
                if amount is not None and amount.value != 0:
                    out = abs(amount.value)
            if plan.credit_idx is not None and plan.credit_idx < len(cells):
                amount = _parse_amount(cells[plan.credit_idx])
                if amount is not None and amount.value != 0:
                    incoming = abs(amount.value)
        description = (cells[plan.description_idx]
                       if plan.description_idx is not None and plan.description_idx < len(cells) else "")
        raw_date = cells[plan.date_idx] if plan.date_idx is not None and plan.date_idx < len(cells) else ""
        entries.append(_RowEntry(when, stems, out, incoming, raw_date, description))

    # The date window: a month (its year taken from the ask, or from the file's own dates), an
    # inclusive range, or the whole file.
    multiple_years = False
    if month_no is not None:
        years = sorted({entry.when.year for entry in entries
                        if entry.when is not None and entry.when.month == month_no})
        if not years:
            return _no_period_miss(label, rows_read, date_name, amount_name or " ".join(pair_names),
                                   entries, f'in {_MONTH_NAMES[month_no - 1]}')
        year = question.year if question.year is not None else years[-1]
        multiple_years = question.year is None and len(years) > 1
        in_scope = [entry for entry in entries
                    if entry.when is not None and entry.when.month == month_no and entry.when.year == year]
        period = f'in {_MONTH_NAMES[month_no - 1]} {year}'
        if not in_scope:
            return _no_period_miss(label, rows_read, date_name, amount_name or " ".join(pair_names),
                                   entries, period)
    elif question.date_from or question.date_to:
        try:
            from_day = date.fromisoformat(question.date_from) if question.date_from else None
            to_day = date.fromisoformat(question.date_to) if question.date_to else None
        except ValueError:
            return _miss(label, 'I could not work out the dates in the question.',
                         rows_read=rows_read, date_column=date_name,
                         amount_column=amount_name or " ".join(pair_names))
        in_scope = [entry for entry in entries if entry.when is not None
                    and (from_day is None or entry.when >= from_day)
                    and (to_day is None or entry.when <= to_day)]
        if from_day is not None and to_day is not None:
            period = f'between {_human_day(from_day)} and {_human_day(to_day)}'
        elif from_day is not None:
            period = f'from {_human_day(from_day)} onwards'
        else:
            period = f'up to {_human_day(to_day)}'
        if not in_scope:
            return _no_period_miss(label, rows_read, date_name, amount_name or " ".join(pair_names),
                                   entries, period)
    else:
        in_scope = entries
        period = 'across the whole file'

    # Excluded rows are left out of every number, of both directions, before anything is added.
    exclude_phrases = [[_stem(word) for word in re.findall(r"[a-z0-9']+", term.lower())]
                       for term in question.exclude]
    exclude_phrases = [phrase for phrase in exclude_phrases if phrase]
    excluded, kept = [], []
    for entry in in_scope:
        bucket = excluded if any(all(stem in entry.stems for stem in phrase)
                                 for phrase in exclude_phrases) else kept
        bucket.append(entry)

    direction = question.direction if question.direction in ("in", "out", "both") else "both"

    def money(value: Decimal) -> str:
        return _render_amount(value, plan.style)

    out_rows = [entry for entry in kept if entry.out is not None]
    in_rows = [entry for entry in kept if entry.incoming is not None]
    total_out = sum((entry.out for entry in out_rows), Decimal(0))
    total_in = sum((entry.incoming for entry in in_rows), Decimal(0))
    if direction == "out":
        counted, lines = out_rows, [_row_line(entry, money, _date_sep(data, plan)) for entry in out_rows]
    elif direction == "in":
        counted, lines = in_rows, [_row_line(entry, money, _date_sep(data, plan)) for entry in in_rows]
    else:
        counted = [entry for entry in kept if entry.out is not None or entry.incoming is not None]
        lines = [_row_line(entry, money, _date_sep(data, plan)) for entry in counted]

    blocks = [f'In {label} I read {_rows_word(rows_read)}.']
    if multiple_years:
        blocks.append(f'The file has {_MONTH_NAMES[month_no - 1]} rows from more than one year; '
                      f'I used {year}, the latest one.')
    numbers = []
    if direction in ("out", "both"):
        numbers.append(f'Money out {period}: {money(total_out)} ({_rows_word(len(out_rows))}).')
    if direction in ("in", "both"):
        numbers.append(f'Money received {period}: {money(total_in)} ({_rows_word(len(in_rows))}).')
    blocks.append("\n".join(numbers))
    if lines:
        shown = lines[:_MAX_SHOWN_LINES]
        row_block = [f'Rows behind {"those numbers" if direction == "both" else "that number"}:', *shown]
        hidden = len(lines) - len(shown)
        if hidden > 0:
            row_block.append(f'and {hidden} more')
        blocks.append("\n".join(row_block))
    if exclude_phrases:
        words = " or ".join(f'"{term}"' for term in question.exclude)
        if excluded:
            blocks.append(f'Not counted: {_rows_word(len(excluded))} left out because '
                          f'the text mentions {words}.')
        else:
            # Scoped to the window: the excluded words may well sit in rows OUTSIDE it (the
            # salary lands in September, the ask is October), and "nothing in the rows I read"
            # would wrongly deny that those rows exist.
            blocks.append(f'No row {period} mentions {words}, so the rule left nothing else out.')

    rule = ["Rule:"]
    if date_name is not None:
        rule.append(f'dates from the "{date_name}" column')
    if amount_name is not None:
        rule.append(f'amounts from the "{amount_name}" column')
    elif pair_names:
        rule.append(f'amounts from the "{pair_names[0]}" and "{pair_names[1]}" columns')
    rule.append(f'rows {period}')
    if skipped:
        rule.append(f'after leaving out {skipped} line{"s" if skipped != 1 else ""} '
                    f'above the column names')
    if exclude_phrases:
        rule.append(f'not counting rows that mention {words}')
    blocks.append(rule[0] + " " + ", ".join(rule[1:]) + ".")

    return SheetSummary(
        ok=True,
        reply="\n\n".join(block for block in blocks if block),
        file_label=label,
        rows_read=rows_read,
        rows_counted=len(counted),
        counted_lines=tuple(lines),
        total_in_text=money(total_in),
        total_out_text=money(total_out),
        needs_column_choice=False,
        date_column=date_name,
        amount_column=amount_name,
    )


def _date_sep(data: list[list[str]], plan: _ColumnPlan) -> str:
    """The date separator this file itself uses, for the day/month prefix of a counted line."""
    if plan.date_idx is None:
        return "/"
    for row in data:
        if plan.date_idx < len(row):
            parsed = _parse_date((row[plan.date_idx] or "").strip())
            if parsed is not None:
                fmt = parsed[1]
                return "/" if "/" in fmt else "." if "." in fmt else "-"
    return "/"
