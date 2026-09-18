"""Deterministic Mermaid flowchart projection for the local PDF lane.

No scripts, no fetches, no headless browser: the flowchart family (``flowchart`` /
``graph``) is parsed with a bounded scanner and laid out as a layered digraph drawn
with the same reportlab vector shapes the chart lane already uses. Labels, edge
texts and Unicode travel byte-for-byte into the PDF (the caller's per-glyph font
coverage law still applies); styling directives (``classDef``/``class``/``style``)
are presentation-only and are dropped, exactly like inline HTML is dropped elsewhere
in this lane. Interaction directives (``click``) are refused: a paper diagram has no
navigation, and pretending otherwise would misrepresent the source. Every other
diagram family keeps its typed refusal -- nothing is silently replaced by source.
"""
from __future__ import annotations

import math
import re
import heapq
from dataclasses import dataclass, field
from typing import Callable

from core.presentation.render_pdf import PdfRefused

MAX_NODES = 80
MAX_EDGES = 160
MAX_LABEL_CHARS = 240
MIN_SCALE = 0.7

_DECLARATION = re.compile(r'^\s*(flowchart|graph)\s+(TB|TD|BT|RL|LR)\b', re.IGNORECASE)
_SUPPORTED_KINDS = ('flowchart', 'graph')
# Link grammar at bracket depth 0: dashes / equals / dotted, optional inline label
# (open token ... close token), optional pipe label, optional arrow head.
_LINK_START = re.compile(r'(-{2,}>?|-\.->|-\.|\.-|={2,}>?)')
_LINK_CLOSE = re.compile(r'(-{2,}>?|-\.->|-\.>|\.-?>|={2,}>?)')
_INLINE_LABEL = re.compile(r'\s*((?:[^-=.]|-(?!-)|=(?!=)|\.(?!\-))*)')
_PIPE_LABEL = re.compile(r'\s*\|([^|]*)\|')
_SUBGRAPH = re.compile(r'^subgraph\s+([^\s\[\]]+)(?:\s+\[([^\]]*)\]|\s+"([^"]*)")?\s*$', re.IGNORECASE)
_STYLE_DIRECTIVES = frozenset({'classdef', 'class', 'style'})

_SHAPES = (
    ('[[', ']]', 'subroutine'),
    ('[(', ')]', 'cylinder'),
    ('([', '])', 'stadium'),
    ('((', '))', 'circle'),
    ('{{', '}}', 'hexagon'),
    ('[', ']', 'rect'),
    ('(', ')', 'rounded'),
    ('{', '}', 'diamond'),
)


@dataclass
class _Node:
    node_id: str
    label: str
    shape: str = 'rect'
    cluster: str = ''
    order: int = 0


@dataclass
class _Edge:
    source: str
    target: str
    label: str = ''
    kind: str = 'dashes'   # dashes | equals | dotted
    arrow: bool = True
    back: bool = False


@dataclass
class _Diagram:
    direction: str = 'TD'
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    clusters: dict = field(default_factory=dict)   # cluster id -> title


def _strip_comment(line: str) -> str:
    out, quote = [], False
    for char in line:
        if char == '"':
            quote = not quote
        if char == '%' and not quote and out and out[-1] == '%':
            out.pop()
            return ''.join(out)
        out.append(char)
    return ''.join(out)


def _parse_node(part: str, cluster: str, sequence: int, nodes: dict) -> _Node:
    text = part.strip()
    if not text:
        raise PdfRefused('Mermaid statement has an empty node')
    quoted = re.match(r'^"([^"]+)"(.*)$', text, re.DOTALL)
    if quoted:
        node_id, rest = quoted.group(1), quoted.group(2).strip()
    else:
        match = re.match(r'^([^\s\[\](){}>]+)(.*)$', text, re.DOTALL)
        if not match:
            raise PdfRefused(f'Mermaid node is not representable in PDF: {text[:60]!r}')
        node_id, rest = match.group(1), match.group(2).strip()
    label, shape = node_id, 'rect'
    if not quoted and not re.fullmatch(r"[\w-]+", node_id):
        raise PdfRefused('Mermaid node syntax is not supported for PDF; no node identity was changed')
    if rest:
        for opener, closer, name in _SHAPES:
            if rest.startswith(opener) and rest.endswith(closer) and len(rest) >= len(opener) + len(closer):
                label, shape = rest[len(opener):-len(closer)].strip(), name
                break
        else:
            if rest.startswith('>') and rest.endswith(']') and len(rest) >= 2:
                label, shape = rest[1:-1].strip(), 'asymmetric'
            else:
                raise PdfRefused(f'Mermaid node shape is not supported for PDF: {rest[:60]!r}')
    label = label.strip().strip('"').strip()
    if len(node_id) > MAX_LABEL_CHARS or len(label) > MAX_LABEL_CHARS:
        raise PdfRefused('Mermaid label exceeds the PDF diagram bound')
    existing = nodes.get(node_id)
    if existing is not None:
        # A bare later mention (`I` after `I[Inspection]`) refers to the node as declared;
        # only two DIFFERENT explicit shapes/labels for one id are a real conflict.
        if rest and (existing.label != label or existing.shape != shape):
            raise PdfRefused(f'Mermaid node {node_id!r} is redeclared with a different label')
        return existing
    node = _Node(node_id=node_id, label=label, shape=shape, cluster=cluster, order=sequence)
    nodes[node_id] = node
    return node


def _scan_statement(statement: str, cluster: str, sequence: int, diagram: _Diagram) -> None:
    """Split one statement into chunks and link tokens at bracket depth 0.

    ``A & B --> C`` means every node of chunk i links to every node of chunk i+1;
    chained links (``A --> B --> C``) are adjacent chunk pairs. Every byte outside
    link tokens must belong to a node spec or the statement is refused -- a
    mis-parse may never silently produce a differently-shaped diagram.
    """
    text = statement
    pos, chunk_start, depth, quote = 0, 0, 0, False
    chunks: list = []
    links: list = []
    while pos < len(text):
        char = text[pos]
        if char == '"':
            quote = not quote
            pos += 1
            continue
        if not quote:
            if char in '([{':
                depth += 1
            elif char in ')]}':
                depth = max(0, depth - 1)
            elif depth == 0 and char in '-=.':
                match = _LINK_START.match(text, pos)
                if match:
                    token = match.group(1)
                    chunks.append(text[chunk_start:pos])
                    pos = match.end()
                    kind = 'equals' if token.startswith('=') else ('dotted' if '.' in token else 'dashes')
                    arrow = token.endswith('>')
                    label = ''
                    if not arrow:
                        inline = _INLINE_LABEL.match(text, pos)
                        closer = _LINK_CLOSE.match(text, inline.end())
                        if not closer:
                            raise PdfRefused(f'Mermaid link is missing its closing token: {statement[:60]!r}')
                        label = inline.group(1).strip()
                        arrow = closer.group(1).endswith('>')
                        pos = closer.end()
                    pipe = _PIPE_LABEL.match(text, pos)
                    if pipe:
                        if label:
                            raise PdfRefused('Mermaid link carries both an inline and a pipe label')
                        label = pipe.group(1).strip()
                        pos = pipe.end()
                    if len(label) > MAX_LABEL_CHARS:
                        raise PdfRefused('Mermaid edge label exceeds the PDF diagram bound')
                    links.append(_Edge('', '', label=label, kind=kind, arrow=arrow))
                    chunk_start = pos
                    continue
        pos += 1
    chunks.append(text[chunk_start:])

    def split_branches(chunk: str) -> list:
        # `&` separates parallel nodes, but only OUTSIDE quotes and shape brackets --
        # a label like "Commas & dashes -- kept" is one node, not two.
        parts, current, depth, quote = [], '', 0, False
        for char in chunk:
            if char == '"':
                quote = not quote
                current += char
            elif quote:
                current += char
            elif char in '([{':
                depth += 1
                current += char
            elif char in ')]}':
                depth = max(0, depth - 1)
                current += char
            elif char == '&' and depth == 0:
                parts.append(current)
                current = ''
            else:
                current += char
        parts.append(current)
        return [n for n in (piece.strip() for piece in parts) if n]

    groups = [split_branches(chunk) for chunk in chunks]
    if not links:
        if any(len(members) != 1 for members in groups) or not groups or not groups[0]:
            raise PdfRefused(f'Mermaid statement is not a node/link sequence: {statement[:60]!r}')
    if len(groups) != len(links) + 1 or any(not members for members in groups):
        raise PdfRefused(f'Mermaid statement is not a node/link sequence: {statement[:60]!r}')
    parsed = []
    for members in groups:
        row = []
        for member in members:
            row.append(_parse_node(member, cluster, sequence, diagram.nodes))
            sequence += 1
        parsed.append(row)
    for index, edge in enumerate(links):
        for left in parsed[index]:
            for right in parsed[index + 1]:
                diagram.edges.append(_Edge(left.node_id, right.node_id, edge.label, edge.kind, edge.arrow))
    if len(diagram.nodes) > MAX_NODES or len(diagram.edges) > MAX_EDGES:
        raise PdfRefused('Mermaid diagram exceeds the PDF diagram bounds')


def _statements(text: str) -> list[str]:
    """Semicolons delimit statements outside node labels, not inside quoted text."""
    parts, current, depth, quoted = [], [], 0, False
    for char in text:
        if char == '"':
            quoted = not quoted
        elif not quoted:
            if char in '([{':
                depth += 1
            elif char in ')]}':
                depth -= 1
            elif char == ';' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
                continue
        current.append(char)
    parts.append(''.join(current).strip())
    return [part for part in parts if part]


def parse_flowchart(text: str) -> _Diagram:
    diagram = _Diagram()
    declaration = None
    cluster_stack: list = []
    sequence = 0
    for line in (statement for raw_line in text.splitlines() for statement in _statements(_strip_comment(raw_line))):
        if not line:
            continue
        if declaration is None:
            declaration = _DECLARATION.match(line)
            if declaration:
                if declaration.group(1).lower() not in _SUPPORTED_KINDS:
                    raise PdfRefused(f'Mermaid diagram kind {declaration.group(1)!r} has no PDF renderer')
                diagram.direction = declaration.group(2).upper()
                if line[declaration.end():].strip():
                    raise PdfRefused('Unexpected content after the Mermaid direction declaration')
                continue
            first = re.match(r'^([A-Za-z][A-Za-z0-9-]*)\b', line)
            raise PdfRefused(f'Mermaid diagram kind {(first.group(1) if first else line[:20])!r} has no PDF renderer')
        if line.lower() == 'end':
            if not cluster_stack:
                raise PdfRefused('Mermaid "end" without an open subgraph')
            cluster_stack.pop()
            continue
        subgraph = _SUBGRAPH.match(line)
        if subgraph:
            if cluster_stack:
                raise PdfRefused('Nested Mermaid subgraphs are not supported for PDF export')
            cluster_id = subgraph.group(1)
            title = subgraph.group(2) if subgraph.group(2) is not None else (subgraph.group(3) or cluster_id)
            diagram.clusters[cluster_id] = title.strip().strip('"')
            cluster_stack.append(cluster_id)
            continue
        head = re.match(r'^([A-Za-z]+)(\s|$)', line)
        if head and head.group(1).lower() in _STYLE_DIRECTIVES:
            continue   # presentation-only directive; values and labels live on nodes/edges
        if head and head.group(1).lower() == 'click':
            raise PdfRefused('Mermaid click interactions cannot be represented in PDF; no diagram was silently replaced')
        cluster = cluster_stack[-1] if cluster_stack else ''
        _scan_statement(line, cluster, sequence, diagram)
        sequence += 1
    if cluster_stack:
        raise PdfRefused('Mermaid subgraph is missing its "end"')
    if not diagram.edges and len(diagram.nodes) < 2:
        raise PdfRefused('Mermaid diagram has no nodes or links to draw')
    _layer(diagram)   # marks cycle edges (back=True) once, where the structure is complete
    return diagram


def _layer(diagram: _Diagram) -> dict:
    """Keep declared flow order, routing DFS cycle edges through return lanes."""
    outgoing = {node_id: [] for node_id in diagram.nodes}
    for edge in diagram.edges:
        edge.back = False
        outgoing[edge.source].append(edge)
    active, visited, order = set(), set(), []

    def visit(node_id):
        if node_id in visited:
            return
        active.add(node_id)
        for edge in outgoing[node_id]:
            if edge.target in active:
                edge.back = True
            else:
                visit(edge.target)
        active.remove(node_id)
        visited.add(node_id)
        order.append(node_id)

    for node_id in diagram.nodes:
        visit(node_id)
    layers = dict.fromkeys(diagram.nodes, 0)
    for node_id in reversed(order):
        for edge in outgoing[node_id]:
            if not edge.back:
                layers[edge.target] = max(layers[edge.target], layers[node_id] + 1)
    return layers


def _order_layers(diagram: _Diagram, layers: dict) -> list:
    by_layer: dict = {}
    for node_id, layer in layers.items():
        by_layer.setdefault(layer, []).append(node_id)
    for layer in by_layer:
        by_layer[layer].sort(key=lambda n: diagram.nodes[n].order)
    neighbours: dict = {node_id: [] for node_id in diagram.nodes}
    for edge in diagram.edges:
        neighbours[edge.source].append(edge.target)
        neighbours[edge.target].append(edge.source)
    ordered = [by_layer[layer] for layer in sorted(by_layer)]
    position = {node_id: index for row in ordered for index, node_id in enumerate(row)}
    for _ in range(4):   # bounded barycenter sweeps; first-seen order breaks ties deterministically
        for row in ordered:
            scored = []
            for node_id in row:
                values = [position[other] for other in neighbours[node_id] if other in position]
                scored.append((sum(values) / len(values) if values else position[node_id],
                               diagram.nodes[node_id].order, node_id))
            for index, (_, _, node_id) in enumerate(sorted(scored)):
                row[index] = node_id
                position[node_id] = index
    return ordered


def _return_route(start, end, obstacles):
    """Shortest orthogonal return path whose segments avoid every node interior."""
    xs = sorted({start[0], end[0], *(x for a, _, b, _ in obstacles for x in (a - 8, b + 8))})
    ys = sorted({start[1], end[1], *(y for _, a, _, b in obstacles for y in (a - 8, b + 8))})
    first, last = (xs.index(start[0]), ys.index(start[1])), (xs.index(end[0]), ys.index(end[1]))

    def clear(a, b):
        ax, ay, bx, by = xs[a[0]], ys[a[1]], xs[b[0]], ys[b[1]]
        for x0, y0, x1, y1 in obstacles:
            if ax == bx and x0 < ax < x1 and max(min(ay, by), y0) < min(max(ay, by), y1):
                return False
            if ay == by and y0 < ay < y1 and max(min(ax, bx), x0) < min(max(ax, bx), x1):
                return False
        return True

    queue = [(0, first, -1)]
    distance, parent = {(first, -1): 0}, {}
    while queue:
        cost, at, direction = heapq.heappop(queue)
        key = (at, direction)
        if cost != distance[key]:
            continue
        if at == last:
            route = []
            while True:
                route.append((xs[key[0][0]], ys[key[0][1]]))
                if key not in parent:
                    return list(reversed(route))
                key = parent[key]
        for dx, dy, axis in ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1)):
            nxt = (at[0] + dx, at[1] + dy)
            if not (0 <= nxt[0] < len(xs) and 0 <= nxt[1] < len(ys)) or not clear(at, nxt):
                continue
            length = abs(xs[nxt[0]] - xs[at[0]]) + abs(ys[nxt[1]] - ys[at[1]])
            value = cost + length + (12 if direction not in (-1, axis) else 0)
            next_key = (nxt, axis)
            if value < distance.get(next_key, float('inf')):
                distance[next_key] = value
                parent[next_key] = key
                heapq.heappush(queue, (value, nxt, axis))
    raise PdfRefused('Mermaid return edge cannot be routed without crossing a node')


def mermaid_pdf_flows(text: str, *, available_width: float, available_height: float,
                      label_check: Callable[[str], None]) -> list:
    """Project a Mermaid flowchart into reportlab flowables, or refuse with a typed reason."""
    from reportlab.graphics.shapes import Drawing, Line, Polygon, PolyLine, Rect, String, Circle, Ellipse, Path
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics

    diagram = parse_flowchart(text)
    for node in diagram.nodes.values():
        label_check(node.label)
    for cluster_title in diagram.clusters.values():
        label_check(cluster_title)
    for edge in diagram.edges:
        if edge.label:
            label_check(edge.label)

    layers = _layer(diagram)
    ordered = _order_layers(diagram, layers)
    horizontal = diagram.direction in ('LR', 'RL')

    font, bold, font_size, line_height = 'VoolPDF', 'VoolPDFBold', 10, 12
    ink = colors.HexColor('#1c2b30')
    stroke = colors.HexColor('#527078')
    cluster_fill = colors.HexColor('#f2f5f6')
    cluster_stroke = colors.HexColor('#c7d0d3')

    def wrap(label: str, max_width: float) -> list:
        lines, current = [], ''
        for word in label.split():
            candidate = word if not current else current + ' ' + word
            if pdfmetrics.stringWidth(candidate, font, font_size) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or ['']

    pad_x, pad_y = 14, 9
    boxes = {}
    for node_id, node in diagram.nodes.items():
        lines = wrap(node.label, 220)
        text_width = max(pdfmetrics.stringWidth(line, font, font_size) for line in lines)
        w = max(64, text_width + 2 * pad_x)
        h = max(30, len(lines) * line_height + 2 * pad_y)
        if node.shape in ('diamond', 'hexagon'):
            w, h = w * 1.45, h * 1.6
        elif node.shape == 'circle':
            w = h = max(w, h)
        elif node.shape in ('cylinder', 'stadium'):
            w = max(w, h + 12)
        boxes[node_id] = (w, h, lines)

    # Conceptual space: x grows right, y grows DOWN (screen order). One transform maps it
    # onto the page frame at the end, so every shape below is written once, in page order.
    gap_major, gap_minor = 64 if horizontal else 48, 26
    axis_sizes = [max((boxes[node_id][0] if horizontal else boxes[node_id][1]) for node_id in row)
                  for row in ordered]
    centers = {}
    major = 0.0
    minor_extent = 0.0
    for row, axis_size in zip(ordered, axis_sizes):
        row_extent = sum((boxes[node_id][1] if horizontal else boxes[node_id][0]) + gap_minor
                         for node_id in row) - gap_minor
        minor_extent = max(minor_extent, row_extent)
        cross = -row_extent / 2
        for node_id in row:
            w, h, _ = boxes[node_id]
            span = (h if horizontal else w)
            along, across = major + axis_size / 2, cross + span / 2
            centers[node_id] = (along, across) if horizontal else (across, along)
            cross += span + gap_minor
        major += axis_size + gap_major
    total_major = max(major - gap_major, 1.0)
    total_minor = max(minor_extent, 1.0)
    if horizontal and diagram.direction == 'RL':
        for node_id, (cx, cy) in centers.items():
            centers[node_id] = (total_major - cx, cy)
    if not horizontal and diagram.direction == 'BT':
        for node_id, (cx, cy) in centers.items():
            centers[node_id] = (cx, total_major - cy)

    bounds = [(cx - boxes[n][0] / 2, cy - boxes[n][1] / 2,
               cx + boxes[n][0] / 2, cy + boxes[n][1] / 2)
              for n, (cx, cy) in centers.items()]
    cluster_boxes = {}
    for cluster_id, title in diagram.clusters.items():
        members = [n for n in diagram.nodes if diagram.nodes[n].cluster == cluster_id]
        if not members:
            continue
        x0 = min(centers[n][0] - boxes[n][0] / 2 for n in members) - 16
        y0 = min(centers[n][1] - boxes[n][1] / 2 for n in members) - 24
        x1 = max(centers[n][0] + boxes[n][0] / 2 for n in members) + 16
        y1 = max(centers[n][1] + boxes[n][1] / 2 for n in members) + 16
        x1 = max(x1, x0 + pdfmetrics.stringWidth(title, bold, 10) + 16)
        cluster_boxes[cluster_id] = (x0, y0, x1, y1)
        bounds.append((x0, y0, x1, y1))
    # Full edge labels and return lanes participate in page bounds too.
    return_x = max(b[2] for b in bounds) + 24
    for edge in diagram.edges:
        if edge.back:
            bounds.append((return_x, min(centers[edge.source][1], centers[edge.target][1]) - 18,
                           return_x + 12, max(centers[edge.source][1], centers[edge.target][1]) + 18))
        if edge.label:
            lines = wrap(edge.label, 220)
            label_w = max(pdfmetrics.stringWidth(line, font, font_size) for line in lines)
            cx = return_x if edge.back else (centers[edge.source][0] + centers[edge.target][0]) / 2
            cy = (centers[edge.source][1] + centers[edge.target][1]) / 2
            bounds.append((cx - label_w / 2, cy - len(lines) * line_height / 2,
                           cx + label_w / 2, cy + len(lines) * line_height / 2))
    x_min, y_min = min(b[0] for b in bounds) - 8, min(b[1] for b in bounds) - 8
    width, height = max(b[2] for b in bounds) - x_min + 8, max(b[3] for b in bounds) - y_min + 8

    scale = min(1.0, available_width / width, available_height / height)
    if scale < MIN_SCALE:
        raise PdfRefused('Mermaid diagram is too large for a readable PDF page; no diagram was silently replaced')

    # Emit in PAGE coordinates directly (y grows upward): a flipped Group transform would
    # also mirror String glyphs, so conceptual (x, y-down) maps through X()/Y() instead.
    drawing = Drawing(available_width, height * scale)

    def X(value: float) -> float:
        return (value - x_min) * scale

    def Y(value: float) -> float:   # conceptual y grows downward; the page's y grows upward
        return (height - (value - y_min)) * scale

    for cluster_id, title in diagram.clusters.items():
        if cluster_id not in cluster_boxes:
            continue
        x0, y0, x1, y1 = cluster_boxes[cluster_id]
        drawing.add(Rect(X(x0), Y(y1), (x1 - x0) * scale, (y1 - y0) * scale, fillColor=cluster_fill,
                         strokeColor=cluster_stroke, strokeWidth=0.8, rx=8 * scale))
        drawing.add(String(X(x0 + 8), Y(y0 + 15), title, fontName=bold,
                           fontSize=10 * scale, fillColor=stroke))

    for node_id, node in diagram.nodes.items():
        w, h, lines = boxes[node_id]
        cx, cy = centers[node_id]
        x0, y0 = cx - w / 2, cy - h / 2
        page_x0, page_y0 = X(x0), Y(y0 + h)
        page_w, page_h = w * scale, h * scale
        if node.shape in ('rounded', 'stadium'):
            radius = (h / 2 if node.shape == 'stadium' else 8) * scale
            drawing.add(Rect(page_x0, page_y0, page_w, page_h, rx=radius, ry=radius,
                             fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1))
        elif node.shape == 'diamond':
            drawing.add(Polygon([X(cx), Y(y0), X(x0 + w), Y(cy), X(cx), Y(y0 + h), X(x0), Y(cy)],
                                fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1))
        elif node.shape == 'hexagon':
            cut = w * 0.12
            drawing.add(Polygon([X(x0 + cut), Y(y0), X(x0 + w - cut), Y(y0), X(x0 + w), Y(cy),
                                 X(x0 + w - cut), Y(y0 + h), X(x0 + cut), Y(y0 + h), X(x0), Y(cy)],
                                fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1))
        elif node.shape == 'circle':
            drawing.add(Circle(X(cx), Y(cy), w * scale / 2,
                               fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1))
        elif node.shape == 'cylinder':
            # Large Rect corner radii self-intersect in reportlab's PDF renderer.
            # Use straight sides and shallow elliptical caps for the cylinder.
            cap = 5 * scale
            bottom, top = page_y0 + cap, page_y0 + page_h - cap
            outline = Path(fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1)
            outline.moveTo(page_x0, top)
            outline.lineTo(page_x0, bottom)
            outline.curveTo(page_x0, bottom - cap * 1.34, page_x0 + page_w, bottom - cap * 1.34, page_x0 + page_w, bottom)
            outline.lineTo(page_x0 + page_w, top)
            outline.closePath()
            drawing.add(outline)
            drawing.add(Ellipse(X(cx), Y(y0 + 5), page_w / 2, 5 * scale,
                                fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1))
        elif node.shape == 'asymmetric':
            drawing.add(Polygon([X(x0 + 10), Y(y0), X(x0 + w), Y(y0), X(x0 + w), Y(y0 + h),
                                 X(x0 + 10), Y(y0 + h), X(x0), Y(cy)],
                                fillColor=colors.white, strokeColor=stroke, strokeWidth=1.1))
        else:
            drawing.add(Rect(page_x0, page_y0, page_w, page_h, fillColor=colors.white,
                             strokeColor=stroke, strokeWidth=1.1))
            if node.shape == 'subroutine':
                for offset in (6, w - 6):
                    drawing.add(Line(X(x0 + offset), Y(y0), X(x0 + offset), Y(y0 + h),
                                     strokeColor=stroke, strokeWidth=0.8))
        size = font_size * scale
        text_y = cy - (len(lines) - 1) * line_height / 2 + font_size * 0.35
        for line in lines:
            drawing.add(String(X(cx), Y(text_y), line, fontName=font, fontSize=size,
                               fillColor=ink, textAnchor='middle'))
            text_y += line_height

    def boundary(node_id: str, towards: tuple) -> tuple:
        w, h, _ = boxes[node_id]
        cx, cy = centers[node_id]
        dx, dy = towards[0] - cx, towards[1] - cy
        if dx == 0 and dy == 0:
            return cx, cy
        shrink = min((w / 2) / abs(dx) if dx else float('inf'),
                     (h / 2) / abs(dy) if dy else float('inf'))
        return cx + dx * shrink, cy + dy * shrink

    for edge in diagram.edges:
        source_center, target_center = centers[edge.source], centers[edge.target]
        x1, y1 = boundary(edge.source, target_center)
        x2, y2 = boundary(edge.target, source_center)
        style = dict(strokeColor=stroke, strokeWidth=2.6 if edge.kind == 'equals' else 1.1)
        if edge.kind == 'dotted':
            style['strokeDashArray'] = [3, 3]
        px1, py1, px2, py2 = X(x1), Y(y1), X(x2), Y(y2)
        if edge.back:
            sx, sy = source_center
            tx, ty = target_center
            px1, py1 = X(sx + boxes[edge.source][0] / 2), Y(sy)
            px2, py2 = X(tx + boxes[edge.target][0] / 2), Y(ty)
            if edge.source == edge.target:
                py1, py2 = Y(sy - 8), Y(ty + 8)
            obstacles = [(X(cx - boxes[n][0] / 2), Y(cy + boxes[n][1] / 2),
                          X(cx + boxes[n][0] / 2), Y(cy - boxes[n][1] / 2))
                         for n, (cx, cy) in centers.items()]
            route = _return_route((px1, py1), (px2, py2), obstacles)
            drawing.add(PolyLine([v for point in route for v in point], **style))
        else:
            drawing.add(Line(px1, py1, px2, py2, **style))
        if edge.arrow:
            angle = math.atan2(py2 - route[-2][1], px2 - route[-2][0]) if edge.back else math.atan2(py2 - py1, px2 - px1)
            size = 6 * scale
            drawing.add(Polygon([px2, py2,
                                 px2 - size * math.cos(angle - 0.4), py2 - size * math.sin(angle - 0.4),
                                 px2 - size * math.cos(angle + 0.4), py2 - size * math.sin(angle + 0.4)],
                                fillColor=stroke, strokeColor=None))
        if edge.label:
            lines = wrap(edge.label, 220)
            label_x = X(return_x) if edge.back else (px1 + px2) / 2
            top = (py1 + py2) / 2 + len(lines) * line_height * scale / 2
            for index, label in enumerate(lines):
                drawing.add(String(label_x, top - (index + 1) * line_height * scale, label, fontName=font,
                                   fontSize=font_size * scale, fillColor=ink, textAnchor='middle'))
    return [drawing]
