"""Draw the prompt caching charts in docs/benchmarks/ from prompt-caching-results.json.

Standard library only, same look as make_charts.py (light and dark theme). Run from the
repository root:

    python docs/benchmarks/make_prompt_caching_charts.py
"""

from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from make_charts import OUT, _bar, _open

EXTRA_STYLE = (
    "<style>"
    "svg{--acc:#2a78d6;--acc-soft:#b8d3f3;--box:#fcfcfb;--grey:#c3c2b7}"
    "@media (prefers-color-scheme: dark){svg{--acc:#3987e5;--acc-soft:#2b4a70;--box:#1a1a19;--grey:#55544f}}"
    ".n{font-size:13px;font-weight:600}.q{font-size:11.5px;fill:var(--ink2)}"
    "</style>"
)


def _svg(height: int, title: str, subtitle: str, desc: str) -> list[str]:
    parts = _open(height, title, subtitle, desc)
    parts.insert(3, EXTRA_STYLE)
    return parts


def _write(name: str, parts: list[str]) -> None:
    parts.append("</svg>")
    (OUT / name).write_text("\n".join(parts) + "\n", encoding="utf-8")


def how_caching_works() -> None:
    parts = _svg(
        300,
        "Caching: Claude charges a tenth of the price for text it has already seen",
        "What VOOL sends to Claude with each message.",
        "First message: setup and rules at full price plus 25% to save them, then the new question at full "
        "price. Next message: the saved setup at a tenth of the price, then the chat so far and the new "
        "question at full price.",
    )

    def box(x: int, y: int, w: int, name: str, price: str, saved: bool = False) -> None:
        fill, stroke, width = ("var(--acc-soft)", "var(--acc)", 2) if saved else ("var(--box)", "var(--axis)", 1.25)
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="56" rx="8" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{width}"/>'
        )
        parts.append(f'<text class="n" x="{x + 12}" y="{y + 24}">{escape(name)}</text>')
        parts.append(f'<text class="q" x="{x + 12}" y="{y + 42}">{escape(price)}</text>')

    row_a, row_b = 76, 176
    parts.append(f'<text class="n" x="24" y="{row_a + 32}">First message</text>')
    parts.append(f'<text class="n" x="24" y="{row_b + 32}">Next message</text>')
    box(180, row_a, 220, "Setup and rules", "full price, plus 25% to save it")
    box(416, row_a, 160, "New question", "full price")
    parts.append(
        f'<path d="M290 {row_a + 58}V{row_b - 6}" stroke="var(--acc)" stroke-width="1.25" '
        'stroke-dasharray="4 4" fill="none"/>'
    )
    parts.append(f'<path d="M285 {row_b - 10}l5 8l5 -8z" fill="var(--acc)"/>')
    parts.append(f'<text class="q" x="302" y="{row_a + 84}">Claude keeps a copy for about 5 minutes</text>')
    box(180, row_b, 220, "Setup and rules", "saved copy: a tenth of the price", saved=True)
    box(416, row_b, 152, "Chat so far", "full price")
    box(584, row_b, 152, "New question", "full price")
    parts.append(
        '<text class="q" x="24" y="268">Claude only saves a start of 1,024 tokens or more (roughly 750 words). '
        "Many VOOL messages are shorter,</text>"
    )
    parts.append(
        '<text class="q" x="24" y="286">so the saving was about 10%. Long chats and big documents save more.</text>'
    )
    _write("prompt-caching-how-it-works.svg", parts)


def cost_per_1000(data: dict) -> None:
    turns = data["turns_per_setup"]
    rows = [("Claude in Claude Code (run 4)", data["run4"]["setups"]["claude_code"], True)]
    for key in ("vool_claude_off", "vool_claude_system", "vool_kimi", "vool_gpt"):
        s = data["run5"]["setups"][key]
        rows.append((f"{s['name'].replace('VOOL, ', '')} in VOOL", s, False))
    x0, span, top, step = 260, 380, 84, 40
    peak = max(s["usd"] for _, s, _ in rows)
    parts = _svg(
        top + step * len(rows) + 16,
        "Claude cost about twice as much in Claude Code as in VOOL",
        "Cost per 1,000 messages, US dollars at OpenRouter prices, Oct 14-15, 2026.",
        "; ".join(f"{label}: ${s['usd'] / turns * 1000:.2f}" for label, s, _ in rows),
    )
    for i, (label, s, accent) in enumerate(rows):
        y = top + i * step
        w = max(span * s["usd"] / peak, 2)
        per = s["usd"] / turns * 1000
        parts.append(f'<text class="lb" x="{x0 - 12}" y="{y + 17}" text-anchor="end">{escape(label)}</text>')
        parts.append(_bar(x0, y, w, 24, "var(--acc)" if accent else "var(--grey)"))
        value = f"${per:,.2f}" if per < 10 else f"${per:,.0f}"
        parts.append(f'<text class="v" x="{x0 + w + 8:.1f}" y="{y + 17}">{value}</text>')
    _write("prompt-caching-cost.svg", parts)


def quality_by_task(data: dict) -> None:
    tasks = data["tasks"]
    cc = data["run4"]["setups"]["claude_code"]
    vool = data["run5"]["setups"]
    x0, x1, top, step = 220, 680, 132, 76
    height = top + step * len(tasks) + 4

    def x(p: float) -> float:
        return x0 + (x1 - x0) * p

    parts = _svg(
        height,
        "Plain chat is level; documents and files hold the whole gap",
        "Share of points per task. Blue: Claude Code (run 4). Grey: the five VOOL setups (run 5).",
        "; ".join(
            f"{t['name']}: Claude Code {cc['by_task'][k]} of {t['max_points']}, VOOL "
            f"{min(s['by_task'][k] for s in vool.values())}-{max(s['by_task'][k] for s in vool.values())}"
            for k, t in tasks.items()
        ),
    )
    for p in (0, 0.25, 0.5, 0.75, 1):
        parts.append(f'<line class="g" x1="{x(p):.1f}" y1="92" x2="{x(p):.1f}" y2="{height - 24}"/>')
        parts.append(f'<text class="ax" x="{x(p):.1f}" y="84" text-anchor="middle">{p * 100:.0f}%</text>')
    for i, (key, task) in enumerate(tasks.items()):
        y = top + i * step
        top_pts = task["max_points"]
        pts = [s["by_task"][key] for s in vool.values()]
        lo, hi = min(pts), max(pts)
        parts.append(f'<text class="n" x="24" y="{y + 4}">{escape(task["name"])}</text>')
        parts.append(f'<line class="g" x1="{x0}" y1="{y}" x2="{x1}" y2="{y}"/>')
        parts.append(
            f'<line x1="{x(lo / top_pts):.1f}" y1="{y}" x2="{x(hi / top_pts):.1f}" y2="{y}" '
            'stroke="var(--grey)" stroke-width="8" stroke-linecap="round"/>'
        )
        for p in sorted(set(pts)):
            parts.append(f'<circle cx="{x(p / top_pts):.1f}" cy="{y}" r="6" fill="var(--grey)" stroke="var(--axis)"/>')
        c = cc["by_task"][key]
        parts.append(f'<circle cx="{x(c / top_pts):.1f}" cy="{y}" r="8" fill="var(--acc)"/>')
        parts.append(f'<text class="n" x="{x(c / top_pts):.1f}" y="{y - 16}" text-anchor="middle">{c}/{top_pts}</text>')
        mid = (x(lo / top_pts) + x(hi / top_pts)) / 2
        parts.append(
            f'<text class="q" x="{mid:.1f}" y="{y + 26}" text-anchor="middle">VOOL {lo}-{hi} of {top_pts}</text>'
        )
    _write("prompt-caching-quality.svg", parts)


def caching_saving(data: dict) -> None:
    keys = (
        ("vool_claude_off", "cache off"),
        ("vool_claude_system", "system cache"),
        ("vool_claude_history", "system + history"),
    )
    x0, span, top = 240, 380, 92
    parts = _svg(
        top + 2 * 150 - 8,
        "One cache mark on the system prompt cut cost by 9.5% and 9.6% in two runs",
        "VOOL on Claude Sonnet 4.5, cost of all 44 messages, US dollars.",
        "; ".join(
            f"{run}: " + ", ".join(f"{label} ${data[run]['setups'][k]['usd']:.4f}" for k, label in keys)
            for run in ("run4", "run5")
        ),
    )
    for g, (run, title) in enumerate((("run4", "Run 4"), ("run5", "Run 5"))):
        setups = data[run]["setups"]
        off = setups["vool_claude_off"]["usd"]
        gy = top + g * 150
        parts.append(f'<text class="n" x="24" y="{gy}">{title}</text>')
        parts.append(f'<line class="b" x1="{x0}" y1="{gy + 6}" x2="{x0}" y2="{gy + 126}"/>')
        for j, (key, label) in enumerate(keys):
            usd = setups[key]["usd"]
            y = gy + 12 + j * 36
            w = usd / 0.7 * span
            parts.append(f'<text class="lb" x="{x0 - 12}" y="{y + 17}" text-anchor="end">{label}</text>')
            parts.append(_bar(x0, y, w, 24, "var(--acc)" if key == "vool_claude_system" else "var(--grey)"))
            text = f"${usd:.2f}" if key == "vool_claude_off" else f"${usd:.2f} · {(1 - usd / off) * 100:.1f}% less"
            parts.append(f'<text class="v" x="{x0 + w + 8:.1f}" y="{y + 17}">{text}</text>')
    _write("prompt-caching-saving.svg", parts)


def prompt_tokens(data: dict) -> None:
    setups = data["run4"]["setups"]
    order = ("claude_code", "vool_claude_off", "vool_claude_system", "vool_claude_history", "vool_gpt", "vool_kimi")
    x0, span, top, step = 300, 300, 100, 40
    peak = max(setups[k]["prompt_tokens"] for k in order)
    ratio = setups["claude_code"]["prompt_tokens"] / setups["vool_claude_system"]["prompt_tokens"]
    parts = _svg(
        top + step * len(order) + 16,
        f"Claude Code sent {ratio:.0f} times as much text as VOOL for the same 44 messages",
        "Run 4, prompt tokens per setup. Light blue was read from cache at a discount.",
        "; ".join(
            f"{setups[k]['name']}: {setups[k]['prompt_tokens']:,} prompt tokens, {setups[k]['cached_tokens']:,} cached"
            for k in order
        ),
    )
    parts.append('<rect x="24" y="66" width="12" height="12" rx="3" fill="var(--acc-soft)"/>')
    parts.append('<text class="lb" x="42" y="76">read from cache at a discount (a tenth of the price on Claude)</text>')
    parts.append('<rect x="490" y="66" width="12" height="12" rx="3" fill="var(--grey)"/>')
    parts.append('<text class="lb" x="508" y="76">sent at full price</text>')
    for i, key in enumerate(order):
        s = setups[key]
        y = top + i * step
        cached = span * s["cached_tokens"] / peak
        full = max(span * (s["prompt_tokens"] - s["cached_tokens"]) / peak, 1)
        size = f"{s['prompt_tokens'] / 1e6:.2f}M" if s["prompt_tokens"] >= 1e6 else f"{s['prompt_tokens'] / 1000:.0f}k"
        share = 100 * s["cached_tokens"] / s["prompt_tokens"]
        parts.append(f'<text class="lb" x="{x0 - 12}" y="{y + 17}" text-anchor="end">{escape(s["name"])}</text>')
        if cached:
            parts.append(f'<rect x="{x0}" y="{y}" width="{cached:.1f}" height="24" fill="var(--acc-soft)"/>')
        parts.append(f'<rect x="{x0 + cached:.1f}" y="{y}" width="{full:.1f}" height="24" fill="var(--grey)"/>')
        parts.append(
            f'<text class="v" x="{x0 + cached + full + 8:.1f}" y="{y + 17}">{size} · {share:.0f}% cached</text>'
        )
    _write("prompt-caching-tokens.svg", parts)


def main() -> None:
    data = json.loads((HERE / "prompt-caching-results.json").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    how_caching_works()
    cost_per_1000(data)
    quality_by_task(data)
    caching_saving(data)
    prompt_tokens(data)


if __name__ == "__main__":
    main()
