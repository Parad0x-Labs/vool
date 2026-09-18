"""Plain-text / terminal renderer: width-aware, copy-paste safe.

Same canonical document as Markdown; layout degrades (tables stack,
charts shrink) but no fact is dropped. Copy/paste survival is a tested
law: URLs, SHAs, commands, numbers and statuses appear verbatim.
"""
from __future__ import annotations

import textwrap

from core.presentation.charts import horizontal_bars
from core.presentation.intent import RenderIntent
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


class PlainTextRenderer:
    def __init__(self, intent: RenderIntent | None = None):
        self.intent = intent or RenderIntent(surface="plain_text", width=72)

    def render(self, doc: RenderDocument) -> str:
        out: list[str] = []
        counter = _Numberer()
        for block in doc.blocks:
            match block:
                case Paragraph():
                    out.append(self._wrap(_runs_plain(block.runs)))
                case Quote():
                    out.append("\n".join("> " + l for l in
                                         self._wrap(_runs_plain(block.runs)).splitlines()))
                case Heading():
                    num = counter.number(block)
                    title = f"{num} {block.text}".strip()
                    out.append(title.upper())
                case List():
                    out.append(self._list(block))
                case Table():
                    out.append(self._table(block))
                case Code():
                    out.append(block.text.rstrip())   # byte-exact
                case Callout():
                    tag = {"info": "INFO", "warning": "WARNING",
                           "success": "OK", "failure": "FAILED"}[block.kind]
                    out.append(f"[{tag}] {self._wrap(_runs_plain(block.runs))}")
                case MetricLine():
                    out.append(_metric_line(block))
                case Progress():
                    out.append(self._progress(block))
                case Chart():
                    out.append(self._chart(block))
                case Timeline():
                    out.append(self._timeline(block))
                case Sources():
                    out.append(self._sources(block))
        return "\n\n".join(out)

    def _wrap(self, s: str) -> str:
        return "\n".join(textwrap.wrap(
            s, width=self.intent.width,
            break_long_words=False, break_on_hyphens=False)) or s

    def _list(self, lst: List) -> str:
        lines = []
        for i, item in enumerate(lst.items, 1):
            marker = f"{i}." if lst.kind == "ordered" else "*"
            lines.append(f"  {marker} {_runs_plain(item.runs)}")
            for j, child in enumerate(item.children, 1):
                cmark = f"{i}.{j}" if lst.kind == "ordered" else "-"
                lines.append(f"    {cmark} {_runs_plain(child.runs)}")
        return "\n".join(lines)

    def _table(self, t: Table) -> str:
        if self.intent.narrow() or len(t.headers) > 4:
            # stacked cards: every fact preserved
            cards = []
            for row in t.rows:
                cards.append("\n".join(f"{h}: {c}" for h, c in zip(t.headers, row, strict=False)))
            return "\n--\n".join(cards)
        widths = [max(len(t.headers[i]), *(len(r[i]) for r in t.rows))
                  for i in range(len(t.headers))]
        def fmt(cells): return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=False)).rstrip()
        sep = "  ".join("-" * w for w in widths)
        return "\n".join([fmt(t.headers), sep] + [fmt(r) for r in t.rows])

    def _progress(self, p: Progress) -> str:
        frac = p.value / p.maximum if p.maximum else p.value / 100.0
        frac = max(0.0, min(1.0, frac))
        bar = "█" * round(10 * frac) + "░" * (10 - round(10 * frac))
        return f"{p.label} {bar} {p.value:g}{p.unit}"

    def _chart(self, chart: Chart) -> str:
        from core.presentation.charts import donut_is_valid
        units = {p.unit for p in chart.series}
        if len(units) > 1 or len(chart.series) <= 1 or self.intent.narrow():
            return self._table(chart_as_table(chart))
        if chart.kind == "donut":
            ok, reason = donut_is_valid(chart)
            if not ok:
                tbl = self._table(chart_as_table(chart))
                return f"{tbl}\n(donut declined: {reason}; exact values above)"
            shares = ", ".join(f"{l} {s:g}%" for l, s in
                               __import__("core.presentation.charts",
                                          fromlist=["donut_slices"]).donut_slices(chart))
            return f"{chart.title or 'Shares'} of {chart.total:g}: {shares}"
        width = min(30, max(8, self.intent.width - 26))
        return "\n".join(horizontal_bars(chart.series, width=width))

    def _timeline(self, t: Timeline) -> str:
        lines = []
        for ev in t.events:
            status_txt = f" [{render_status(ev.status)}]" if ev.status else ""
            lines.append(f"{ev.stamp}  {_runs_plain(ev.runs)}{status_txt}")
        return "\n".join(lines)

    def _sources(self, s: Sources) -> str:
        lines = []
        for src in s.sources:
            ref = src.ref
            if ref.href_exposed:
                lines.append(f"- {ref.display_label} <{ref.href}>")
            else:
                lines.append(f"- {ref.display_label} [withheld: {ref.redaction_reason}]")
        return "\n".join(lines)


def chart_as_table(chart: Chart) -> Table:
    mixed_units = len({p.unit for p in chart.series}) > 1
    headers = ["Label", "Value" if mixed_units else f"Value ({chart.series[0].unit if chart.series else ''})"]
    rows = [[p.label, str(p.value) + (f" {p.unit}" if mixed_units else "")
             + (f" · {p.status.name}" if p.status else "")]
            for p in chart.series]
    if chart.total is not None:
        headers.append("Total")
        rows = [[*r, str(chart.total)] for r in rows]
    return Table(headers, rows)


def _runs_plain(runs) -> str:
    parts = []
    for r in runs:
        if isinstance(r, Link):
            if r.ref.href_exposed:
                parts.append(f"{r.ref.display_label} <{r.ref.href}>")
            else:
                parts.append(f"{r.ref.display_label} [withheld]")
        elif isinstance(r, Span):
            parts.append(r.text)          # styling dropped, bytes kept
        else:
            raise TypeError(type(r))
    return "".join(parts)


def _metric_line(m: MetricLine) -> str:
    parts = []
    for label, value_run in m.items:
        v = value_run.text if isinstance(value_run, Span) else _runs_plain([value_run])
        parts.append(f"{label} {v}".strip())
    return m.separator.join(parts)


class _Numberer:
    def __init__(self):
        self.c = [0, 0, 0]

    def number(self, h: Heading) -> str:
        i = min(h.level, 3) - 1
        self.c[i] += 1
        for d in range(i + 1, 3):
            self.c[d] = 0
        return ".".join(str(x) for x in self.c[: i + 1])
