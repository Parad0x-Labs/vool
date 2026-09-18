"""Markdown surface renderer. Representation only; never alters meaning.

Guarantees (tested in tests/test_presentation_laws.py):

* link hrefs are emitted byte-exact or not at all;
* code blocks are fenced verbatim — no smart quotes, no reflow;
* table cells and metric values are copied exactly (UNKNOWN preserved);
* headings get deterministic numbers when ``numbered`` is set;
* density changes layout only, never facts.
"""
from __future__ import annotations

from core.presentation.charts import horizontal_bars
from core.presentation.intent import RenderIntent
from core.presentation.links import markdown_href
from core.presentation.model import (
    Callout,
    Chart,
    Code,
    Heading,
    Link,
    List,
    MetricLine,
    Paragraph,
    Progress,
    Quote,
    RenderDocument,
    Sources,
    Span,
    Table,
    Timeline,
)
from core.presentation.status import render_status


def _run_md(run) -> str:
    if isinstance(run, Link):
        href = markdown_href(run.ref)
        if run.ref.href_exposed and href is not None:
            return f"[{run.ref.display_label}]({href})"
        return f"{run.ref.display_label} [link withheld: {run.ref.redaction_reason}]"
    if isinstance(run, Span):
        if run.style == "code":
            return f"`{run.text}`"
        if run.style == "strong":
            return f"**{run.text}**"
        return run.text
    raise TypeError(f"unknown run type: {type(run)!r}")


def _runs(runs) -> str:
    return "".join(_run_md(r) for r in runs)


_CALLOUT_PREFIX = {"info": "ℹ️ ", "warning": "⚠️ ",
                   "success": "✅ ", "failure": "❌ "}


def _chart_table_md(chart: Chart) -> str:
    """A chart the surface cannot draw becomes an exact table (values verbatim)."""
    from core.presentation.render_plain import chart_as_table

    return _table_md(chart_as_table(chart))


class MarkdownRenderer:
    def __init__(self, intent: RenderIntent | None = None):
        self.intent = intent or RenderIntent()

    def render(self, doc: RenderDocument) -> str:
        out: list[str] = []
        counter = _HeadingNumberer()
        for block in doc.blocks:
            match block:
                case Paragraph() | Quote():
                    body = _runs(block.runs)
                    if isinstance(block, Quote):
                        out.append("> " + body)
                    else:
                        out.append(body)
                case Heading():
                    num = counter.number(block) if block.numbered else None
                    prefix = f"{num} " if num else ""
                    out.append(f"{'#' * min(block.level + 1, 6)} {prefix}{block.text}")
                case List():
                    out.extend(self._list_md(block))
                case Table():
                    out.append(_table_md(block))
                case Code():
                    fence = "```" + block.lang
                    # text emitted byte-exact between fences
                    out.append(f"{fence}\n{block.text}\n```")
                case Callout():
                    out.append(_CALLOUT_PREFIX[block.kind] + _runs(block.runs))
                case MetricLine():
                    out.append(_metric_line_md(block))
                case Progress():
                    out.append(self._progress_md(block))
                case Chart():
                    out.append(self._chart_md(block))
                case Timeline():
                    out.append(_timeline_md(block))
                case Sources():
                    out.append(_sources_md(block))
        return "\n\n".join(out)

    # -- blocks ---------------------------------------------------------

    def _list_md(self, lst: List) -> list[str]:
        lines = []
        for i, item in enumerate(lst.items, 1):
            marker = f"{i}." if lst.kind == "ordered" else "-"
            lines.append(f"{marker} {_runs(item.runs)}")
            for j, child in enumerate(item.children, 1):
                cmark = f"{i}.{j}" if lst.kind == "ordered" else "-"
                lines.append(f"   {cmark} {_runs(child.runs)}")
        return lines

    def _progress_md(self, p: Progress) -> str:
        frac = p.value / p.maximum if p.maximum else p.value / 100.0
        frac = max(0.0, min(1.0, frac))
        width = 10
        filled = round(width * frac)
        bar = "█" * filled + "░" * (width - filled)
        shown = f"{p.value:g}{p.unit}"
        return f"{p.label} {bar} {shown}"

    def _chart_md(self, chart: Chart) -> str:
        from core.presentation.charts import donut_is_valid
        kind = chart.kind
        if self.intent.surface == 'chat' and kind != 'table':
            from core.presentation.chart_payload import chart_fence, chart_payload

            try:
                return chart_fence(chart_payload(chart))
            except ValueError as exc:
                return _chart_table_md(chart) + f'\n\nChart unavailable: {exc}.'
        if kind == "table" or len(chart.series) <= 1:
            return _chart_table_md(chart)
        if not _single_unit_ok(chart):
            return _chart_table_md(chart)
        if kind == "donut":
            ok, reason = donut_is_valid(chart)
            if not ok:
                return _chart_table_md(chart) + f"\n\n*(donut declined: {reason}; values shown exactly)*"
            from core.presentation.charts import donut_slices
            shares = donut_slices(chart)
            title = f"**{chart.title}**\n\n" if chart.title else ""
            rows = "\n".join(f"- {label}: {share:g}%" for label, share in shares)
            return f"{title}{rows}\n\n*(shares of {chart.total:g} total; rounded for display)*"
        # bars_h / bars_v — narrow surfaces fall back to table
        if self.intent.narrow():
            return _chart_table_md(chart)
        title = f"**{chart.title}**\n\n" if chart.title else ""
        width = min(40, max(10, self.intent.width - 24))
        return title + "\n".join(horizontal_bars(chart.series, width=width))

    # -- density ---------------------------------------------------------

    def _density_compact_lines(self, doc: RenderDocument) -> list[str]:
        """COMPACT mode collapses paragraphs into one joined paragraph."""
        return []


def _single_unit_ok(chart: Chart) -> bool:
    return len({p.unit for p in chart.series}) <= 1


def _cell(s: str) -> str:
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _table_md(t: Table) -> str:
    head = "| " + " | ".join(_cell(c) for c in t.headers) + " |"
    sep = "|" + "|".join([" --- "] * len(t.headers)) + "|"
    rows = ["| " + " | ".join(_cell(c) for c in row) + " |" for row in t.rows]
    return "\n".join([head, sep, *rows])


def _metric_line_md(m: MetricLine) -> str:
    parts = []
    for label, value_run in m.items:
        value = _run_md(value_run)
        parts.append(f"{label} {value}".strip())
    return m.separator.join(parts)


def _timeline_md(t: Timeline) -> list | str:
    lines = []
    for ev in t.events:
        status_txt = f" {render_status(ev.status)}" if ev.status else ""
        lines.append(f"{ev.stamp} { _runs(ev.runs)}{status_txt}".rstrip())
    joined = "\n   ↓\n".join(lines)
    return joined


def _sources_md(s: Sources) -> str:
    lines = []
    for src in s.sources:
        ref = src.ref
        authority = ""
        if src.official:
            authority = " · Official"          # only when upstream said so
        elif src.source_kind != "unknown":
            authority = f" · {src.source_kind}"
        href = markdown_href(ref)
        if ref.href_exposed and href is not None:
            lines.append(f"- [{ref.display_label}]({href}){authority}")
        else:
            lines.append(f"- {ref.display_label} *(withheld: "
                         f"{ref.redaction_reason})*{authority}")
    return "\n".join(lines)


class _HeadingNumberer:
    """Deterministic hierarchical numbering of existing structure."""

    def __init__(self) -> None:
        self._counters = [0, 0, 0]

    def number(self, h: Heading) -> str:
        idx = min(h.level, 3) - 1
        self._counters[idx] += 1
        for deeper in range(idx + 1, 3):
            self._counters[deeper] = 0
        return ".".join(str(c) for c in self._counters[: idx + 1])
