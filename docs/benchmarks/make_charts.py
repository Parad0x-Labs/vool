"""Draw the memory benchmark charts in docs/benchmarks/ from results.json.

Standard library only. Each chart is a static SVG that follows the reader's light or dark
theme, so GitHub renders it inline in the README. Run from the repository root:

    python docs/benchmarks/make_charts.py
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "charts"

# Fixed colour per system, whatever chart it appears in.
SERIES = {
    "VOOL": ("#2a78d6", "#3987e5"),
    "Hermes": ("#eb6834", "#d95926"),
    "OpenClaw": ("#1baf7a", "#199e70"),
    "Full transcript": ("#a3a29b", "#6f6e69"),
}
STATUS = {
    "Right": "#0ca30c",
    "Partly right": "#fab219",
    "Said it did not know": "#a3a29b",
    "Confidently wrong": "#d03b3b",
}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
WIDTH = 760


def _key(name: str) -> str:
    for key in SERIES:
        if name.startswith(key):
            return key
    raise KeyError(name)


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower())


def _style() -> str:
    light = "".join(f"--s-{_slug(k)}:{v[0]};" for k, v in SERIES.items())
    dark = "".join(f"--s-{_slug(k)}:{v[1]};" for k, v in SERIES.items())
    return (
        "<style>"
        f"svg{{--bg:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;{light}}}"
        f"@media (prefers-color-scheme: dark){{svg{{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;"
        f"--muted:#898781;--grid:#2c2c2a;--axis:#383835;{dark}}}}}"
        f"text{{font-family:{FONT};fill:var(--ink)}}"
        ".t{font-size:17px;font-weight:600}.st{font-size:12.5px;fill:var(--ink2)}"
        ".lb{font-size:13px}.v{font-size:12.5px;font-variant-numeric:tabular-nums}"
        ".ax{font-size:11.5px;fill:var(--muted);font-variant-numeric:tabular-nums}"
        ".g{stroke:var(--grid);stroke-width:1}.b{stroke:var(--axis);stroke-width:1}"
        "</style>"
    )


def _open(height: int, title: str, subtitle: str, desc: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape(desc)}</desc>',
        _style(),
        f'<rect width="{WIDTH}" height="{height}" rx="8" fill="var(--bg)"/>',
        f'<text class="t" x="24" y="34">{escape(title)}</text>',
        f'<text class="st" x="24" y="54">{escape(subtitle)}</text>',
    ]


def _bar(x: float, y: float, w: float, h: float, fill: str) -> str:
    """A horizontal bar anchored on the left with a 4px rounded data end."""
    if w <= 0:
        return ""
    r = min(4.0, w, h / 2)
    return (
        f'<path d="M{x:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - 2 * r:.1f}'
        f'a{r},{r} 0 0 1 -{r},{r}h-{w - r:.1f}z" fill="{fill}"/>'
    )


def _legend(names: list[str], y: int, x: int = 24) -> list[str]:
    parts = []
    for name in names:
        fill = f"var(--s-{_slug(_key(name))})"
        parts.append(f'<rect x="{x}" y="{y - 10}" width="12" height="12" rx="3" fill="{fill}"/>')
        parts.append(f'<text class="lb" x="{x + 18}" y="{y}">{escape(name)}</text>')
        x += 30 + int(len(name) * 7.4)
    return parts


def _grid(x0: int, x1: int, top: int, bottom: int, ticks: list[float], fmt) -> list[str]:
    parts = []
    span = ticks[-1]
    for t in ticks:
        x = x0 + (x1 - x0) * t / span
        cls = "b" if t == 0 else "g"
        parts.append(f'<line class="{cls}" x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}"/>')
        parts.append(f'<text class="ax" x="{x:.1f}" y="{bottom + 16}" text-anchor="middle">{fmt(t)}</text>')
    return parts


def grouped_bars(
    path: Path,
    title: str,
    subtitle: str,
    groups: list[tuple[str, list[tuple[str, float, str]]]],
    note: str = "",
) -> None:
    """Groups of horizontal bars on one 0-100% axis. Each bar is (series, percent, label)."""
    names: list[str] = []
    for _, bars in groups:
        for name, _, _ in bars:
            if name not in names:
                names.append(name)
    bar_h, gap, group_gap = 18, 2, 18
    x0, x1 = 200, WIDTH - 110
    top = 98
    height_bars = sum(len(b) * (bar_h + gap) - gap + group_gap for _, b in groups) - group_gap
    bottom = top + height_bars + 10
    height = bottom + 34 + (22 if note else 0)
    desc = "; ".join(f"{group}: " + ", ".join(f"{n} {lbl}" for n, _, lbl in bars) for group, bars in groups)
    parts = _open(height, title, subtitle, desc)
    parts += _legend(names, 82)
    parts += _grid(x0, x1, top - 6, bottom, [0, 25, 50, 75, 100], lambda t: f"{t:.0f}%")
    y = top
    for group, bars in groups:
        block = len(bars) * (bar_h + gap) - gap
        parts.append(
            f'<text class="lb" x="{x0 - 12}" y="{y + block / 2 + 4.5:.1f}" text-anchor="end">{escape(group)}</text>'
        )
        for name, pct, lbl in bars:
            w = (x1 - x0) * pct / 100
            parts.append(_bar(x0, y, w, bar_h, f"var(--s-{_slug(_key(name))})"))
            parts.append(f'<text class="v" x="{x0 + w + 6:.1f}" y="{y + 13.5}">{escape(lbl)}</text>')
            y += bar_h + gap
        y += group_gap - gap
    if note:
        parts.append(f'<text class="st" x="24" y="{height - 16}">{escape(note)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def small_multiples(
    path: Path,
    title: str,
    subtitle: str,
    panels: list[tuple[str, list[tuple[str, float, str]]]],
    note: str = "",
) -> None:
    """Side-by-side panels, each its own axis starting at zero (no shared or dual scale)."""
    n = len(panels)
    pad, inner_gap = 24, 28
    panel_w = (WIDTH - 2 * pad - (n - 1) * inner_gap) / n
    names = [name for name, _, _ in panels[0][1]]
    bar_h, gap = 22, 8
    top = 112
    bottom = top + len(names) * (bar_h + gap) - gap
    height = bottom + 30 + (22 if note else 0)
    desc = "; ".join(f"{p}: " + ", ".join(f"{nm} {lbl}" for nm, _, lbl in bars) for p, bars in panels)
    parts = _open(height, title, subtitle, desc)
    parts += _legend(names, 82)
    for i, (panel, bars) in enumerate(panels):
        px = pad + i * (panel_w + inner_gap)
        parts.append(f'<text class="lb" x="{px:.1f}" y="{top - 10}" font-weight="600">{escape(panel)}</text>')
        peak = max(v for _, v, _ in bars)
        span = panel_w - 70
        parts.append(f'<line class="b" x1="{px:.1f}" y1="{top - 2}" x2="{px:.1f}" y2="{bottom + 2}"/>')
        y = top
        for name, value, lbl in bars:
            w = span * value / peak
            parts.append(_bar(px, y, w, bar_h, f"var(--s-{_slug(_key(name))})"))
            parts.append(f'<text class="v" x="{px + w + 6:.1f}" y="{y + 15.5}">{escape(lbl)}</text>')
            y += bar_h + gap
    if note:
        parts.append(f'<text class="st" x="24" y="{height - 16}">{escape(note)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def stacked_outcomes(
    path: Path,
    title: str,
    subtitle: str,
    rows: list[tuple[str, dict[str, int]]],
    total: int,
) -> None:
    """One 100% bar per system, split into the four answer outcomes."""
    x0, x1 = 150, WIDTH - 24
    bar_h, gap = 26, 14
    top = 100
    bottom = top + len(rows) * (bar_h + gap) - gap
    height = bottom + 24
    desc = "; ".join(f"{name}: " + ", ".join(f"{k} {v}" for k, v in parts.items()) for name, parts in rows)
    out = _open(height, title, subtitle, desc)
    lx = 24
    for label, colour in STATUS.items():
        out.append(f'<rect x="{lx}" y="72" width="12" height="12" rx="3" fill="{colour}"/>')
        out.append(f'<text class="lb" x="{lx + 18}" y="82">{escape(label)}</text>')
        lx += 36 + int(len(label) * 7.2)
    y = top
    for name, counts in rows:
        out.append(f'<text class="lb" x="{x0 - 12}" y="{y + 17.5}" text-anchor="end">{escape(name)}</text>')
        x = float(x0)
        for label, colour in STATUS.items():
            value = counts.get(label, 0)
            if not value:
                continue
            w = (x1 - x0) * value / total
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{max(w - 2, 0.5):.1f}" height="{bar_h}" fill="{colour}"/>')
            if w >= 26:
                out.append(
                    f'<text class="v" x="{x + w / 2 - 1:.1f}" y="{y + 17.5}" text-anchor="middle" '
                    f'style="fill:#0b0b0b">{value}</text>'
                )
            x += w
        y += bar_h + gap
    out.append("</svg>")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _pct(right: int, n: int) -> tuple[float, str]:
    return 100 * right / n, f"{right:,}/{n:,} ({100 * right / n:.1f}%)"


def main() -> None:
    data = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    off, r3, dev, full = data["official_150"], data["round_3"], data["dev_200"], data["full_locomo"]

    # 1. Every head-to-head round, same reader for all three systems.
    rounds = []
    for run, label in ((off, "Official 150 · v12"), (r3, "Round 3, 130 · v12"), (dev, "Pilot 200 · v7")):
        n = run["questions"]
        bars = []
        for name, s in run["systems"].items():
            pct, lbl = _pct(s["right"], n)
            bars.append((_key(name), pct, lbl))
        rounds.append((label, bars))
    grouped_bars(
        OUT / "head-to-head-rounds.svg",
        "VOOL vs Hermes vs OpenClaw: every head-to-head round",
        "LoCoMo questions answered right; same reader model (GLM 5.3 flashx) for all three in each round.",
        rounds,
        note="Graded by GPT-4o (official 150) or GLM 5.3 flashx (others). VOOL was tuned on the pilot's 200 questions.",
    )

    # 2. Official 150: accuracy per LoCoMo category.
    cats = []
    for cat, row in off["categories"].items():
        n = row["n"]
        cats.append((f"{cat} (n={n})", [(name, *_pct(row[name], n)) for name in ("VOOL v12", "Hermes", "OpenClaw")]))
    grouped_bars(
        OUT / "official-150-categories.svg",
        "Official 150: right answers by question type",
        "150 never-used LoCoMo questions, GLM 5.3 flashx answering, GPT-4o 2024-08-06 grading.",
        cats,
    )

    # 3. Official 150: how each system's answers came out.
    outcomes = []
    for name, s in off["systems"].items():
        outcomes.append(
            (
                name,
                {
                    "Right": s["right"],
                    "Partly right": s["partly"],
                    "Said it did not know": s["abstained"],
                    "Confidently wrong": s["confident_wrong"],
                },
            )
        )
    stacked_outcomes(
        OUT / "official-150-answer-outcomes.svg",
        "Official 150: how the answers came out",
        "Each bar is all 150 answers. Confidently wrong = a definite answer that the grader marked wrong.",
        outcomes,
        off["questions"],
    )

    # 4. Official 150: cost, tokens and answer time, each on its own zero-based axis.
    systems = off["systems"]
    small_multiples(
        OUT / "official-150-cost-tokens-speed.svg",
        "Official 150: cost, tokens and answer time per question",
        "Billed answering cost, product-call tokens and median answer time. Shorter is better.",
        [
            ("Cost per question", [(n, s["cost_per_q"], f"${s['cost_per_q']:.4f}") for n, s in systems.items()]),
            (
                "Tokens per question",
                [(n, s["tokens_per_q"], f"{s['tokens_per_q'] / 1000:.1f}k") for n, s in systems.items()],
            ),
            ("Median answer time", [(n, s["median_s"], f"{s['median_s']:.1f} s") for n, s in systems.items()]),
        ],
        note="Answer time excludes ingestion. Median ingestion per conversation: VOOL 62.5 s, OpenClaw 12.5 s, Hermes 2.8 s.",
    )

    # 5. Full LoCoMo: VOOL v14 against the whole conversation pasted into the prompt.
    rows = []
    for cat, row in list(full["categories"].items()) + list(full["totals"].items()):
        n = row["n"]
        label = {"non-adversarial": "All non-adversarial", "all": "All questions"}.get(cat, cat)
        rows.append((f"{label} (n={n:,})", [(name, *_pct(row[name], n)) for name in ("VOOL v14", "Full transcript")]))
    grouped_bars(
        OUT / "full-locomo-vs-full-transcript.svg",
        "Full LoCoMo, 1,986 questions: VOOL v14 vs the whole chat in the prompt",
        "Same reader (GLM 5.3 flashx) and grader (GPT-4o). Adversarial = questions about things never said.",
        rows,
        note="Tokens per question: VOOL 9.7k, full transcript 21.3k. Billed: VOOL $6.65, full transcript $4.11 (cached).",
    )


if __name__ == "__main__":
    main()
