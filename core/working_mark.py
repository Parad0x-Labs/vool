"""The working card's mark: VOOL's logo on a tiny black LED panel, lit bulb by bulb.

A 12x13 grid of square bulbs sits on a dark panel. Every bulb is drawn dim; the bulbs that make the
VOOL mark (the V, the two O eyes, the L-shaped smile) carry a lit layer that switches on one bulb
after another in drawing order -- the V stroke, then both eyes together, then the smile. The whole
mark holds, the eyes blink twice, the panel goes dark, and the cycle restarts.

Pure SVG + CSS on one shared clock: no canvas, no timers, no rAF. The resting markup IS the finished
lit mark (lit bulbs are fully on with no animation), so ``animation: none`` -- reduced motion, a
finished card -- shows the complete logo instantly, and removing the card removes the animation.

Each lit bulb gets its own keyframes because CSS cannot shift one keyframe's waypoints per element:
a shared keyframe with per-bulb delays would also delay each bulb's switch-off, so the mark would
never be whole at once.
"""

from __future__ import annotations

CYCLE_SECONDS = 4.0
CELL = 10            # viewBox units per grid cell
BULB = 7.4           # bulb edge; the rest of the cell is the gap between bulbs
PAD = 6              # panel margin around the grid
COLS, ROWS = 12, 13
LIGHT_FROM, LIGHT_TO = 2.0, 58.0   # % of the cycle over which the bulbs switch on, in order
HOLD_TO, DARK_BY = 84.0, 90.0      # whole mark holds until HOLD_TO, all dark by DARK_BY
BLINKS = ((66.0, 69.0), (72.0, 75.0))   # the eyes dim twice while the mark holds

# The mark's bulbs in drawing order, as (row, col). The V is one stroke from the top-left down to
# the tip and up to the top-right; the left (small) and right (large) eye are rings; the smile runs
# along the bottom from the left and turns up on the right, the L of the logo.
V = [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (4, 6), (3, 7), (2, 8), (1, 9), (0, 10)]
EYE_LEFT = [(6, 1), (6, 2), (7, 3), (8, 3), (9, 2), (9, 1), (8, 0), (7, 0)]
EYE_RIGHT = [(5, 8), (5, 9), (5, 10), (6, 11), (7, 11), (8, 11), (9, 10), (9, 9), (9, 8),
             (8, 7), (7, 7), (6, 7)]
SMILE = [(12, 1), (12, 2), (12, 3), (12, 4), (12, 5), (12, 6), (12, 7), (12, 8), (12, 9),
         (12, 10), (11, 11)]


def _eyes_together() -> list[tuple[tuple[int, int], str]]:
    """Both eyes light at the same pace: the shorter ring is spread across the longer one."""
    out: list[tuple[float, tuple[int, int]]] = []
    for ring in (EYE_LEFT, EYE_RIGHT):
        out += [((i + 0.5) / len(ring), cell) for i, cell in enumerate(ring)]
    out.sort(key=lambda item: item[0])
    return [(cell, "eye") for _, cell in out]


def lit_order() -> list[tuple[tuple[int, int], str]]:
    """Every lit bulb with its part, in the order it switches on."""
    return [(c, "v") for c in V] + _eyes_together() + [(c, "smile") for c in SMILE]


def _pct(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") + "%"


def mark_svg() -> str:
    """The panel, every dim bulb, and the lit layer, as one inline SVG string."""
    width, height = COLS * CELL + 2 * PAD, ROWS * CELL + 2 * PAD
    inset = (CELL - BULB) / 2

    def rect(row: int, col: int, cls: str, extra: str = "") -> str:
        x, y = PAD + col * CELL + inset, PAD + row * CELL + inset
        return (f'<rect class="{cls}"{extra} x="{x:g}" y="{y:g}" width="{BULB:g}" height="{BULB:g}" '
                f'rx="1.6"/>')

    parts = [f'<svg class="tc-mark" viewBox="0 0 {width} {height}" width="36" height="36" '
             'aria-hidden="true" focusable="false">',
             f'<rect class="tc-panel" x="0" y="0" width="{width}" height="{height}" rx="14"/>',
             '<g class="tc-dim">']
    parts += [rect(r, c, "tc-off") for r in range(ROWS) for c in range(COLS)]
    parts.append('</g><g class="tc-lit">')
    parts += [rect(r, c, f"tc-on tc-{part}", f' style="animation-name:tcBulb{i}"')
              for i, ((r, c), part) in enumerate(lit_order())]
    parts.append("</g></svg>")
    return "".join(parts)


def mark_css() -> str:
    """One keyframes block per lit bulb on the shared clock: off until its turn, a bright flash as
    it switches on, lit through the hold (eyes blink), then dark until the cycle restarts."""
    order = lit_order()
    step = (LIGHT_TO - LIGHT_FROM) / max(1, len(order) - 1)
    blocks = []
    for i, (_cell, part) in enumerate(order):
        on = LIGHT_FROM + i * step
        frames = [f"0%,{_pct(on)} {{ opacity:0; }}",
                  f"{_pct(on + 0.8)} {{ opacity:1; fill:var(--tc-flash); }}",
                  f"{_pct(on + 3.0)} {{ opacity:1; fill:currentColor; }}"]
        if part == "eye":
            for start, end in BLINKS:
                frames.append(f"{_pct(start)},{_pct(end)} {{ opacity:1; }}")
                frames.append(f"{_pct((start + end) / 2)} {{ opacity:.12; }}")
        frames.append(f"{_pct(HOLD_TO)} {{ opacity:1; }}")
        frames.append(f"{_pct(DARK_BY)},100% {{ opacity:0; }}")
        blocks.append(f"@keyframes tcBulb{i} {{ " + " ".join(frames) + " }")
    return "\n".join(blocks)
