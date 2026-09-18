"""One fence grammar for presentation blocks, and the canonical labels a block must carry.

The chat page draws a chart only from a fenced block whose opening line is ```chart and a
diagram only from ```mermaid (core/chat_visuals_fragment.py reads ``lang-chart`` /
``lang-mermaid``); the PDF exporter and the chart validator read the same labels.  Measured on
a651de73 (2026-09-11, depot and lab fixtures, nvidia/nemotron-3.5-lightning:free): both drafts
carried a chart payload that satisfied the grammar byte for byte, fenced as ```json or as a
bare ``` block, and one draft nested its ```mermaid block inside a second, unlabelled fence.
Nothing had told the model which label the renderer reads, and the validator rejected usable
answers twice per turn before shipping the fallback table.

This module is the single reading of "a fenced block" shared by the validator, the repair and
the renderers (the page's own parser uses the same opener/closer grammar), plus one
value-preserving canonicalisation: one unambiguous wrapper around a requested visual can be
unwrapped, and one eligible unlabelled or ``json``-labelled block whose body IS the requested
visual can be relabelled. Longer fences contain literal examples and are never repaired.
The bytes inside a block are never changed, and nothing is fenced that
the model did not fence -- a bare JSON line stays prose and still fails the format check, so the
repair instruction (which now names the exact label) is what fixes that case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: The only fence labels the shipped renderers read for a chart and a diagram.
CHART_FENCE_TAG = "chart"
MERMAID_FENCE_TAG = "mermaid"
#: Labels a model plausibly writes for a chart payload it was asked to fence: none, or the
#: language of the body.  A block under any other label was labelled on purpose and is left alone.
_CHART_RELABEL_TAGS = frozenset({"", "json"})
_MERMAID_RELABEL_TAGS = frozenset({""})
#: Supported diagram declarations. Declaration preflight does not replace the browser's
#: Mermaid parser; the browser remains the authority on successful rendering.
MERMAID_DIAGRAM_KEYWORDS: tuple[str, ...] = (
    "graph", "flowchart", "sequenceDiagram", "classDiagram", "stateDiagram", "stateDiagram-v2",
    "erDiagram", "gantt", "journey", "gitGraph", "mindmap", "timeline", "pie", "quadrantChart",
    "requirementDiagram", "C4Context", "C4Container", "C4Component", "sankey-beta",
    "xychart-beta", "block-beta", "kanban", "architecture-beta", "packet-beta",
)
# Match the page's fenced containers, including longer fences used to quote visual source.
# A closer uses the same character and at least the opener's length.
_OPENER_RE = re.compile(r"^\s*(`{3,}|~{3,})([A-Za-z0-9_]*)\s*$")
_CLOSER_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
_MERMAID_OPENING_RE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(keyword) for keyword in MERMAID_DIAGRAM_KEYWORDS) + r")\b"
)


@dataclass(frozen=True)
class FencedBlock:
    """A top-level fenced block as the page would read it."""

    tag: str
    body: str
    start: int  # line index of the opening fence
    end: int  # line index of the closing fence; -1 when the block never closes
    body_lines: tuple[str, ...] = field(default_factory=tuple)
    marker: str = "```"

    @property
    def terminated(self) -> bool:
        return self.end >= 0


def fenced_blocks(text: str) -> list[FencedBlock]:
    """Every top-level fenced block of `text`, in order, with the page's opener/closer reading."""
    lines = str(text or "").splitlines()
    blocks: list[FencedBlock] = []
    index = 0
    while index < len(lines):
        opener = _OPENER_RE.match(lines[index])
        if opener is None:
            index += 1
            continue
        start = index
        body: list[str] = []
        index += 1
        end = -1
        while index < len(lines):
            closer = _CLOSER_RE.match(lines[index])
            if closer and closer.group(1)[0] == opener.group(1)[0] and len(closer.group(1)) >= len(opener.group(1)):
                end = index
                break
            body.append(lines[index])
            index += 1
        blocks.append(FencedBlock(
            tag=opener.group(2).lower(), body="\n".join(body), start=start, end=end,
            body_lines=tuple(body), marker=opener.group(1),
        ))
        index += 1
    return blocks


@dataclass(frozen=True)
class NormalizedFences:
    text: str
    actions: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def _wrapper_opener_index(block: FencedBlock) -> int | None:
    """The body-line index of the fence opener a bare wrapper block starts with, or None.

    Under the page's grammar the FIRST bare ``` line after the inner opener closes the OUTER
    block, so a wrapped fence never reads as "a block whose body is a block": it reads as a bare
    block whose first body line is itself an opener, terminated by the inner closer, followed by
    the wrapper's own closer as a stray bare opener.  That is the shape the lab draft took on
    a651de73 (2026-09-11) and the shape the page would have rendered as code plus a swallowed
    final sentence.
    """
    if block.tag or block.marker != "```":
        return None
    for index, line in enumerate(block.body_lines):
        if not line.strip():
            continue
        opener = _OPENER_RE.match(line)
        return index if opener and opener.group(1) == "```" and opener.group(2) else None
    return None


def _unwrap_wrapper_fences(lines: list[str], requested: set[str]) -> tuple[list[str], int]:
    unwrapped = 0
    for _round in range(3):  # bounded: a wrapper of a wrapper is the deepest nesting worth reading
        changed = False
        blocks = fenced_blocks("".join(lines))
        candidates: dict[str, list[tuple[FencedBlock, int]]] = {}
        for block in blocks:
            inner_index = _wrapper_opener_index(block)
            if inner_index is None or not block.terminated:
                continue
            tag = _OPENER_RE.match(block.body_lines[inner_index]).group(2).lower()
            body = "\n".join(block.body_lines[inner_index + 1:])
            if tag not in requested or tag not in {CHART_FENCE_TAG, MERMAID_FENCE_TAG}:
                continue
            if any(b.tag == tag for b in blocks):
                continue
            if not (_is_chart_body(body) if tag == CHART_FENCE_TAG else _is_mermaid_body(body)):
                continue
            following = block.end + 1
            while following < len(lines) and not lines[following].strip():
                following += 1
            if following >= len(lines) or lines[following].strip() != "```":
                continue
            candidates.setdefault(tag, []).append((block, following))
        for options in candidates.values():
            if len(options) != 1:
                continue  # Multiple examples are ambiguous; ask for canonical output instead.
            block, following = options[0]
            for index in (following, block.start):
                del lines[index]
            unwrapped += 1
            changed = True
            break  # line indexes shifted: re-read before touching another block
        if not changed:
            break
    return lines, unwrapped


def _is_chart_body(body: str) -> bool:
    from core.presentation.chart_payload import parse_chart_body

    try:
        parse_chart_body(body)
    except (ValueError, TypeError, OverflowError):
        return False
    return True


def mermaid_body_has_declaration(body: str) -> bool:
    """Reject missing declarations and configuration the sandbox refuses to render.

    This is a necessary preflight, not a full Mermaid syntax validator. Never rewrite
    diagram content to guess missing nodes, labels, or graph direction.
    """
    if len(body) > 20000 or re.search(r"%%\s*\{", body) or body.lstrip().startswith("---"):
        return False
    for line in body.splitlines():
        if line.strip() and not line.lstrip().startswith("%%"):
            return _MERMAID_OPENING_RE.match(line) is not None
    return False


def _is_mermaid_body(body: str) -> bool:
    return mermaid_body_has_declaration(body)


def normalize_presentation_fences(text: str, *, requested_formats: tuple[str, ...] | list[str] = ()) -> NormalizedFences:
    """Canonicalise the fences of `text` for the formats the turn requested.

    Only a requested visual with no canonical block and one eligible candidate is repaired.
    A wrapper must have both closing fences and enclose the requested visual, not other code.
    Longer/tilde containers and ambiguous candidates stay literal. Block bodies and line endings
    are never edited. Text without eligible fences is returned unchanged.
    """
    value = str(text or "")
    if "```" not in value:
        return NormalizedFences(text=value, actions=())
    requested = {str(item or "").strip().lower() for item in requested_formats}
    if not requested.intersection({CHART_FENCE_TAG, MERMAID_FENCE_TAG}):
        return NormalizedFences(text=value, actions=())
    lines = value.splitlines(keepends=True)
    actions: list[str] = []
    lines, unwrapped = _unwrap_wrapper_fences(lines, requested)
    actions.extend(["unwrapped_wrapper_fence"] * unwrapped)
    blocks = fenced_blocks("".join(lines))
    if CHART_FENCE_TAG in requested and not any(b.tag == CHART_FENCE_TAG for b in blocks):
        candidates = [b for b in blocks if b.terminated and b.marker == "```"
                      and b.tag in _CHART_RELABEL_TAGS and _is_chart_body(b.body)]
        if len(candidates) == 1:
            block = candidates[0]
            ending = lines[block.start][len(lines[block.start].rstrip("\r\n")):]
            lines[block.start] = f"```{CHART_FENCE_TAG}" + ending
            actions.append("relabelled_chart_fence")
        blocks = fenced_blocks("".join(lines))
    if MERMAID_FENCE_TAG in requested and not any(b.tag == MERMAID_FENCE_TAG for b in blocks):
        candidates = [b for b in blocks if b.terminated and b.marker == "```"
                      and b.tag in _MERMAID_RELABEL_TAGS and _is_mermaid_body(b.body)]
        if len(candidates) == 1:
            block = candidates[0]
            ending = lines[block.start][len(lines[block.start].rstrip("\r\n")):]
            lines[block.start] = f"```{MERMAID_FENCE_TAG}" + ending
            actions.append("relabelled_mermaid_fence")
    result = "".join(lines)
    if not actions:
        return NormalizedFences(text=value, actions=())
    return NormalizedFences(text=result, actions=tuple(actions))


def has_terminated_block(text: str, tag: str) -> bool:
    """Whether `text` holds a closed top-level block under exactly this label."""
    wanted = str(tag or "").lower()
    return any(block.terminated and block.tag == wanted for block in fenced_blocks(text))


__all__ = [
    "CHART_FENCE_TAG",
    "MERMAID_DIAGRAM_KEYWORDS",
    "MERMAID_FENCE_TAG",
    "FencedBlock",
    "NormalizedFences",
    "fenced_blocks",
    "has_terminated_block",
    "normalize_presentation_fences",
]
