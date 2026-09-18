"""Deterministic chart geometry and representation selection.

Presentation calculation only: bars/donuts visualize EXACT typed values.
Rounding affects display geometry and labels only; the exact value stays
on the block. Any dataset that cannot be visualized without risking a
lie degrades to table/text (``select_representation`` returns ``table``).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from core.presentation.model import Chart, SeriesPoint

BarChars = "█░"

MAX_DONUT_POINTS = 12
MIN_BARS = 2


def _all_finite_nonneg(series: Sequence[SeriesPoint]) -> bool:
    return all(
        isinstance(p.value, (int, float)) and math.isfinite(p.value)
        and p.value >= 0 for p in series
    )


def _single_unit(series: Sequence[SeriesPoint]) -> bool:
    return len({p.unit for p in series}) == 1


def donut_is_valid(chart: Chart) -> tuple[bool, str]:
    """A donut requires parts of one defined whole sharing one unit."""
    s = chart.series
    if chart.kind != "donut":
        return False, "not-a-donut-request"
    if not _single_unit(s):
        return False, "mixed-units"
    if not _all_finite_nonneg(s):
        return False, "invalid-values"
    if len(s) < 2 or len(s) > MAX_DONUT_POINTS:
        return False, "point-count"
    if chart.total is None or chart.total <= 0 or not math.isfinite(chart.total):
        return False, "missing-or-bad-total"
    part_sum = sum(p.value for p in s)
    if part_sum <= 0:
        return False, "zero-sum"
    # Parts must correspond to the declared whole (small rounding slack).
    if abs(part_sum - chart.total) > max(1e-9, 0.02 * chart.total):
        return False, "parts-do-not-sum-to-total"
    return True, "ok"


def select_representation(series: Sequence[SeriesPoint],
                          want_part_of_whole: bool = False,
                          width: int = 80) -> str:
    """Deterministic representation choice. Never uses aesthetics alone.

    Returns one of ``donut | bars_h | table``. Vertical bars are chosen by
    the renderer for narrow few-category comparisons; here we pick the
    safest default family and let the surface renderer adapt orientation.
    """
    if not series:
        return "table"
    if len(series) == 1:
        return "table"                      # single number is not a chart
    if not _single_unit(series):
        return "table"                      # mixed units must never share an axis
    if not _all_finite_nonneg(series):
        return "table"                      # negatives/NaN/inf degrade honestly
    if want_part_of_whole:
        ok, _ = donut_is_valid(Chart(list(series), kind="donut",
                                     total=sum(p.value for p in series)))
        if ok:
            return "donut"
    if len(series) > MIN_BARS:
        return "bars_h"                     # category magnitude / rankings
    return "table"


def horizontal_bars(series: Sequence[SeriesPoint], width: int = 40,
                    decimals: int = 1) -> list[str]:
    """Render exact-valued horizontal bars.

    Bar length derives deterministically from the values; labels show the
    EXACT value rounded only for display (see rounding law in model docs).
    """
    if not series or not _single_unit(series) or not _all_finite_nonneg(series):
        raise ValueError("horizontal_bars requires finite non-negative single-unit series")
    vmax = max(p.value for p in series)
    lines = []
    label_w = max(len(p.label) for p in series)
    for p in series:
        filled = 0 if vmax == 0 else round(width * p.value / vmax)
        bar = BarChars[0] * filled + BarChars[1] * (width - filled)
        shown = _display_number(p.value, decimals)
        suffix = f"{p.status.name}" if p.status else ""
        head = f"{p.label.ljust(label_w)} {bar} {shown} {p.unit}".rstrip()
        lines.append(f"{head} {suffix}".rstrip())
    return lines


def vertical_bars(series: Sequence[SeriesPoint], height: int = 6,
                  decimals: int = 1) -> list[str]:
    """Compact ASCII column chart; falls back caller-side when too wide."""
    if not series or not _single_unit(series) or not _all_finite_nonneg(series):
        raise ValueError("vertical_bars requires finite non-negative single-unit series")
    vmax = max(p.value for p in series)
    cols = []
    for p in series:
        h = 0 if vmax == 0 else max(1, round(height * p.value / vmax))
        cols.append((h, p))
    lines = []
    for row in range(height, 0, -1):
        cells = [" █ " if h >= row else "   " for h, _ in cols]
        prefix = f"{vmax * row / height:>4.0f} ┤" if row == height else "     ┤"
        lines.append(prefix + "".join(cells))
    axis_value = " ".join(_display_number(p.value, decimals).rjust(3) for _, p in cols)
    lines.append("     ┴" + "───" * len(cols))
    lines.append("      " + axis_value)
    labels = " ".join(p.label.center(3) for p, in [(p,) for p in series])
    lines.append("      " + labels)
    return lines


def donut_slices(chart: Chart, decimals: int = 1) -> list[tuple[str, float]]:
    """Exact percentage shares of a validated donut (presentation math).

    Shares are computed from exact values against the declared total and
    rounded ONLY for display; the pair sums may differ from 100 by ≤0.1
    per slice due rounding — callers must keep the honest note attached.
    """
    ok, reason = donut_is_valid(chart)
    if not ok:
        raise ValueError(f"donut invalid: {reason}")
    total = float(chart.total)
    return [(p.label, round(100.0 * p.value / total, decimals)) for p in chart.series]


def _display_number(value: float, decimals: int) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.{decimals}f}"
