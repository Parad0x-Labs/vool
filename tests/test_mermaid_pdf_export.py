"""Mermaid-to-PDF delivery: deterministic vector flowcharts, typed refusals elsewhere.

The flowchart family (flowchart/graph) renders through the same local reportlab lane
as charts -- no scripts, no fetches, no headless browser. Labels, edge texts and
Unicode must survive byte-for-byte into the extracted PDF text. Every other diagram
family, interactive directives and oversized diagrams keep the typed refusal: no
diagram is ever silently replaced by source or dropped.
"""
from io import BytesIO

import pytest
from pypdf import PdfReader

from core.presentation.mermaid_pdf import parse_flowchart
from core.presentation.render_pdf import PdfRefused, render_markdown_pdf
from tests import test_chat_export_api as export

DEPOT = """# Depot inventory

| Depot | Cartons |
| --- | --- |
| North depot | 144 |
| South depot | 216 |
| **Total** | **360** |

```mermaid
flowchart LR
  R[Receiving] --> I[Inspection]
  I --> N[North depot]
  I --> S[South depot]
```

North and South together hold 360 cartons.
"""

# Genuinely new shape: top-down direction, rounded/stadium/diamond nodes, a labeled
# thick edge, a dotted edge, a subgraph cluster, Unicode labels and a review cycle.
NOVEL = """## Λογιστική ροής — laboratorijas plūsma

```mermaid
flowchart TD
  S([Suāmljums — ienākšana]) --> Q{Prüfung ok?}
  Q -- ja ==> A[(Archive — αρχειοθέτηση)]
  Q -. nē .-> R[Atgriezt pie piegādātāja]
  A --> S
  subgraph lab [Laboratorija]
    L1[Amber 18] --> L2[Violet 42]
  end
  S --> L1
```

Violet pārsniedz Amber par 24 paraugus.
"""


def _pdf_text(markdown: str) -> str:
    body = render_markdown_pdf(markdown, title='t')
    assert body.startswith(b'%PDF-')
    return '\n'.join(page.extract_text() for page in PdfReader(BytesIO(body)).pages)


def test_original_depot_flowchart_exports_with_every_label():
    text = _pdf_text(DEPOT)
    for needle in ('Receiving', 'Inspection', 'North depot', 'South depot',
                   '144', '216', '360', 'North and South together hold 360 cartons.'):
        assert needle in text, needle


def test_novel_diagram_preserves_unicode_labels_edges_and_cluster():
    text = _pdf_text(NOVEL)
    for needle in ('Suāmljums — ienākšana', 'Prüfung ok?', 'Archive — αρχειοθέτηση',
                   'Atgriezt pie piegādātāja', 'Laboratorija', 'Amber 18', 'Violet 42',
                   'ja', 'nē', 'Violet pārsniedz Amber par 24 paraugus.'):
        assert needle in text, needle


def test_layout_is_deterministic():
    # The parser and layered layout are pure functions of the source: the same diagram
    # must produce the same shape list twice (PDF bytes also carry a creation timestamp).
    from core.presentation import render_pdf
    from core.presentation.mermaid_pdf import mermaid_pdf_flows
    render_pdf._font()
    def render():
        flows = mermaid_pdf_flows(NOVEL.split('```mermaid')[1].split('```')[0],
                                  available_width=500, available_height=640,
                                  label_check=lambda text: None)
        return [sorted(shape.getProperties().items()) for flow in flows for shape in flow.contents]
    assert render() == render()


@pytest.mark.parametrize('kind_body', [
    'sequenceDiagram\n  participant S as Sensor\n  S->>T: Level 420 litres\n',
    'erDiagram\n  CARTON ||--|| DEPOT : holds\n',
    'stateDiagram-v2\n  [*] --> Ready\n',
    'pie\n  "North" : 144\n  "South" : 216\n',
])
def test_other_diagram_families_refuse_with_the_kind_named(kind_body):
    with pytest.raises(PdfRefused) as raised:
        render_markdown_pdf('```mermaid\n' + kind_body + '```\n', title='t')
    assert 'no PDF renderer' in str(raised.value)


def test_click_interactions_refuse_instead_of_faking_navigation():
    with pytest.raises(PdfRefused) as raised:
        render_markdown_pdf('```mermaid\nflowchart LR\n A[Source] --> B[Docs]\n click B "https://example.invalid" "open"\n```\n', title='t')
    assert 'click' in str(raised.value)


def test_missing_glyph_in_a_label_refuses_without_replacement():
    with pytest.raises(PdfRefused) as raised:
        render_markdown_pdf('```mermaid\nflowchart LR\n A[Private-use glyph \ue000] --> B[Ok]\n```', title='t')
    assert 'cannot represent every source character' in str(raised.value)


def test_oversized_diagram_refuses_rather_than_shipping_an_unreadable_one():
    lines = ['flowchart TD']
    for index in range(120):
        lines.append(f'n{index}[Node {index}] --> n{index + 1}[Node {index + 1}]')
    with pytest.raises(PdfRefused):
        render_markdown_pdf('```mermaid\n' + '\n'.join(lines) + '\n```\n', title='t')


# ---------- parser laws (unit boundary: structure, not pixels) ----------

def test_parser_reads_shapes_links_labels_and_branching():
    diagram = parse_flowchart(
        'flowchart LR\n'
        ' A[Rect] & B(Round) -->|both| C{Choice}\n'
        ' C -.-> D((Circle))\n'
        ' C == thick ==> E[[Sub]]\n'
        ' D --> F[(Database)]\n')
    assert set(diagram.nodes) == {'A', 'B', 'C', 'D', 'E', 'F'}
    assert diagram.nodes['A'].label == 'Rect'
    assert diagram.nodes['B'].shape == 'rounded'
    assert diagram.nodes['C'].shape == 'diamond'
    assert diagram.nodes['D'].shape == 'circle'
    assert diagram.nodes['E'].shape == 'subroutine'
    assert diagram.nodes['F'].shape == 'cylinder'
    pairs = {(edge.source, edge.target, edge.label, edge.kind) for edge in diagram.edges}
    assert ('A', 'C', 'both', 'dashes') in pairs
    assert ('B', 'C', 'both', 'dashes') in pairs
    assert ('C', 'D', '', 'dotted') in pairs
    assert ('C', 'E', 'thick', 'equals') in pairs


def test_parser_keeps_quoted_labels_with_punctuation_verbatim():
    diagram = parse_flowchart('flowchart TD\n Q["Commas, (brackets) & dashes -- kept"] --> Z')
    assert diagram.nodes['Q'].label == 'Commas, (brackets) & dashes -- kept'


def test_parser_skips_comments_and_styling_directives_but_keeps_nodes():
    diagram = parse_flowchart(
        'flowchart LR\n'
        ' %% a comment with --> inside\n'
        ' classDef big fill:#f9f\n'
        ' A[One] --> B[Two]\n')
    assert set(diagram.nodes) == {'A', 'B'}
    assert len(diagram.edges) == 1


def test_cyles_mark_back_edges_instead_of_looping_forever():
    diagram = parse_flowchart('flowchart TD\n A[Alpha] --> B[Beta]\n B --> A\n')
    back = [edge for edge in diagram.edges if edge.back]
    assert len(back) == 1


def test_a_statement_that_is_not_nodes_and_links_refuses():
    with pytest.raises(PdfRefused):
        parse_flowchart('flowchart LR\n what is this even -->\n')


def test_conflicting_redeclaration_of_one_node_refuses():
    with pytest.raises(PdfRefused):
        parse_flowchart('flowchart LR\n A[First] --> B\n A[Different] --> C\n')


# ---------- served export door (the boundary the user actually drives) ----------

def test_answer_with_a_flowchart_exports_a_pdf_through_the_served_door():
    export._seed(user='Diagram please', assistant=DEPOT)
    response = export._export(format='pdf')
    assert response.status == 200, response.body
    assert response.body.startswith(b'%PDF-')
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.body)).pages)
    for needle in ('Receiving', 'Inspection', 'North depot', 'South depot', '360'):
        assert needle in text, needle


def test_answer_with_an_unsupported_family_still_refuses_at_the_door():
    export._seed(assistant='```mermaid\nsequenceDiagram\n  participant S\n  S->>T: level\n```\n')
    response = export._export(format='pdf')
    assert response.status == 422
    import json
    assert json.loads(response.body)['error'] == 'pdf_export_refused'
    assert 'no PDF renderer' in json.loads(response.body)['message']
