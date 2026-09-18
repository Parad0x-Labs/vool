from io import BytesIO
import re

import pytest
from pypdf import PdfReader
from reportlab.graphics.shapes import String

from core.presentation.mermaid_pdf import mermaid_pdf_flows, parse_flowchart
from core.presentation.render_pdf import PdfRefused, _font, render_markdown_pdf


@pytest.mark.parametrize("direction", ["LR", "RL", "TD", "BT"])
def test_node_geometry_follows_direction_and_stays_on_page(direction):
    _font()
    drawing = mermaid_pdf_flows(
        f"flowchart {direction}\nA[Receiving cartons] --> B[Detailed inspection]\nB --> C[Archive records]",
        available_width=520, available_height=690, label_check=lambda _: None,
    )[0]
    labels = {s.text: (s.x, s.y) for s in drawing.contents if isinstance(s, String)}
    a, b, c = (labels[x] for x in ("Receiving cartons", "Detailed inspection", "Archive records"))
    if direction in {"LR", "RL"}:
        assert abs(a[1] - b[1]) < 1 and abs(b[1] - c[1]) < 1
        assert (a[0] < b[0] < c[0]) if direction == "LR" else (a[0] > b[0] > c[0])
    else:
        assert abs(a[0] - b[0]) < 1 and abs(b[0] - c[0]) < 1
        assert (a[1] > b[1] > c[1]) if direction == "TD" else (a[1] < b[1] < c[1])
    x0, y0, x1, y1 = drawing.getBounds()
    assert x0 >= -1 and y0 >= -1
    assert x1 <= drawing.width + 1 and y1 <= drawing.height + 1


@pytest.mark.parametrize("source", [
    "flowchart LR; A --> B\nB --> C",
    'flowchart TD\nA["One; two"] --> B[Three]\nB --> C[Four]',
])
def test_semicolons_do_not_discard_nodes_or_split_labels(source):
    diagram = parse_flowchart(source)
    assert set(diagram.nodes) == {"A", "B", "C"}
    assert [(e.source, e.target) for e in diagram.edges] == [("A", "B"), ("B", "C")]
    if "One; two" in source:
        assert diagram.nodes["A"].label == "One; two"


@pytest.mark.parametrize("source", ["flowchart LR\nA <--> B", "flowchart LR\nA:::ready --> B"])
def test_unimplemented_syntax_is_refused_instead_of_changing_node_identity(source):
    with pytest.raises(PdfRefused):
        parse_flowchart(source)


@pytest.mark.parametrize("label", [
    "Shipment requires a complete inspection before release to the receiving team",
    "Preserve every measured item including Violet 42 and Amber 18 without abbreviation",
])
def test_edge_labels_are_never_truncated(label):
    pdf = render_markdown_pdf(f"```mermaid\nflowchart TD\nA -->|{label}| B\n```", title="Review")
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
    assert label in re.sub(r"\s+", " ", text)


def test_cycle_keeps_declared_forward_order():
    _font()
    drawing = mermaid_pdf_flows('flowchart TD\nA[Receive] --> B[Check]\nB --> C[Archive]\nC --> A',
                               available_width=520, available_height=690, label_check=lambda _: None)[0]
    labels = {s.text: s.y for s in drawing.contents if isinstance(s, String)}
    assert labels['Receive'] > labels['Check'] > labels['Archive']


def test_return_edge_avoids_intervening_node_interiors():
    from core.presentation.mermaid_pdf import _return_route
    obstacles = [(0, 0, 80, 30), (100, 0, 180, 30), (200, 0, 280, 30), (0, 80, 80, 110)]
    route = _return_route((80, 15), (80, 95), obstacles)
    assert route[0] == (80, 15) and route[-1] == (80, 95)
    for (ax, ay), (bx, by) in zip(route, route[1:]):
        assert ax == bx or ay == by
        for x0, y0, x1, y1 in obstacles:
            if ax == bx:
                assert not (x0 < ax < x1 and max(min(ay, by), y0) < min(max(ay, by), y1))
            else:
                assert not (y0 < ay < y1 and max(min(ax, bx), x0) < min(max(ax, bx), x1))
