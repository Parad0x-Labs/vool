"""The one fence grammar behind chart/mermaid delivery, pinned on the a651de73 captures.

Measured 2026-09-11 on the packaged a651de73 app (nvidia/nemotron-3.5-lightning:free, owner
profile, diagnostic capture at the adapter boundary): the depot draft carried a grammar-exact
chart payload under a ```json label; the lab draft carried it in a bare ``` block and nested its
```mermaid block inside a second bare fence.  The validator rejected both twice per turn and the
user received the fallback table.  These fixtures are those drafts, byte for byte.
"""
from __future__ import annotations

import pytest

from core.presentation.chart_payload import chart_bodies, chart_fence
from core.presentation.fences import (
    CHART_FENCE_TAG,
    MERMAID_FENCE_TAG,
    fenced_blocks,
    has_terminated_block,
    normalize_presentation_fences,
)

DEPOT_DRAFT = (
    "| Depot | Cartons |\n|-------|---------|\n| North depot | 144 |\n| South depot | 216 |\n"
    "| **Total** | **360** |\n\n"
    "```json\n"
    '{"type":"bar","data":{"labels":["North depot","South depot"],"datasets":[{"label":"cartons","data":[144,216]}]}}\n'
    "```\n\n"
    "```mermaid\nflowchart TD\n    A[Receiving] --> B[Inspection]\n    B --> C[North depot]\n"
    "    B --> D[South depot]\n```\n\n"
    "North depot holds 144 cartons, South depot holds 216 cartons, for a total of 360 cartons."
)
CHART_BODY_DEPOT = (
    '{"type":"bar","data":{"labels":["North depot","South depot"],"datasets":[{"label":"cartons","data":[144,216]}]}}'
)
LAB_DRAFT = (
    "| Batch  | Samples |\n|--------|---------|\n| Amber  | 18      |\n| Violet | 42      |\n\n"
    "```\n"
    '{"type":"bar","data":{"labels":["Amber","Violet"],"datasets":[{"label":"samples","data":[18,42]}]}}\n'
    "```\n\n"
    "```\n```mermaid\ngraph LR\nCollection --> Lab --> Archive\n```\n```\n\n"
    "Values retained."
)
LAB_RETRY_DRAFT = (
    "| Batch  | Samples |\n|--------|---------|\n| Amber  | 18      |\n| Violet | 42      |\n\n"
    "```\n```mermaid\ngraph LR\nCollection --> Lab --> Archive\n```\n```\n\n"
    '{"type":"bar","data":{"labels":["Amber","Violet"],"datasets":[{"label":"samples","data":[18,42]}]}}\n\n'
    "Values recorded."
)


def test_fence_reader_matches_the_page_grammar():
    blocks = fenced_blocks("intro\n```Json\nx\n```\ntext\n```\ny\n")
    assert [(b.tag, b.body, b.terminated) for b in blocks] == [("json", "x", True), ("", "y", False)]
    literals = fenced_blocks("~~~\nliteral one\n~~~\n````\nliteral two\n````")
    assert [(b.marker, b.body, b.tag) for b in literals] == [("~~~", "literal one", ""), ("````", "literal two", "")]
    assert chart_fence({"type": "bar", "data": {"labels": ["a"], "datasets": [{"label": "u", "data": [1]}]}}).startswith(
        "```" + CHART_FENCE_TAG + "\n"
    )


def test_depot_draft_json_label_is_relabelled_to_chart_with_bytes_untouched():
    result = normalize_presentation_fences(DEPOT_DRAFT, requested_formats=("table", "chart", "mermaid"))
    assert result.actions == ("relabelled_chart_fence",)
    assert "```json" not in result.text
    assert chart_bodies(result.text) == [CHART_BODY_DEPOT]
    assert has_terminated_block(result.text, MERMAID_FENCE_TAG)
    # Everything outside the one fence line is byte-identical.
    assert result.text.replace("```" + CHART_FENCE_TAG + "\n", "```json\n", 1) == DEPOT_DRAFT


def test_lab_draft_wrapper_fence_is_unwrapped_and_bare_chart_relabelled():
    result = normalize_presentation_fences(LAB_DRAFT, requested_formats=("table", "chart", "mermaid"))
    assert result.actions == ("unwrapped_wrapper_fence", "relabelled_chart_fence")
    tags = [(b.tag, b.terminated) for b in fenced_blocks(result.text)]
    assert tags == [(CHART_FENCE_TAG, True), (MERMAID_FENCE_TAG, True)]
    assert "graph LR\nCollection --> Lab --> Archive" in result.text
    assert result.text.rstrip().endswith("Values retained.")
    assert chart_bodies(result.text) == [
        '{"type":"bar","data":{"labels":["Amber","Violet"],"datasets":[{"label":"samples","data":[18,42]}]}}'
    ]


def test_bare_json_line_is_never_fenced_by_the_runtime():
    result = normalize_presentation_fences(LAB_RETRY_DRAFT, requested_formats=("table", "chart", "mermaid"))
    assert result.actions == ("unwrapped_wrapper_fence",)
    assert not has_terminated_block(result.text, CHART_FENCE_TAG)
    assert '\n{"type":"bar","data":{"labels":["Amber","Violet"]' in result.text


@pytest.mark.parametrize("text", [
    # A json block that is not a chart stays a json block.
    "```json\n{\"rows\": [1, 2]}\n```\n",
    # A chart-shaped body under a deliberate other label is left alone.
    "```yaml\n{\"type\":\"bar\",\"data\":{\"labels\":[\"a\"],\"datasets\":[{\"label\":\"u\",\"data\":[1]}]}}\n```\n",
    # An existing ```chart block means sibling json blocks were labelled on purpose.
    "```chart\n{\"type\":\"bar\",\"data\":{\"labels\":[\"a\"],\"datasets\":[{\"label\":\"u\",\"data\":[1]}]}}\n```\n"
    "```json\n{\"type\":\"bar\",\"data\":{\"labels\":[\"a\"],\"datasets\":[{\"label\":\"u\",\"data\":[1]}]}}\n```\n",
    # A legitimate bare code block after a diagram is not a wrapper.
    "```mermaid\ngraph LR\nA --> B\n```\n```\nsome code\n```\nafter\n",
    # An unterminated chart-shaped block is not a chart and is not relabelled.
    "```json\n{\"type\":\"bar\",\"data\":{\"labels\":[\"a\"],\"datasets\":[{\"label\":\"u\",\"data\":[1]}]}}\n",
    "no fences at all",
])
def test_normalisation_leaves_deliberate_or_invalid_shapes_untouched(text):
    result = normalize_presentation_fences(text, requested_formats=("chart", "mermaid"))
    assert result.actions == ()
    assert result.text == text


def test_unlabelled_diagram_block_is_relabelled_only_when_it_opens_as_mermaid():
    diagram = "```\nflowchart LR\nA --> B\n```\n"
    assert normalize_presentation_fences(diagram, requested_formats=("mermaid",)).actions == ("relabelled_mermaid_fence",)
    assert normalize_presentation_fences(diagram, requested_formats=("chart",)).actions == ()
    prose_block = "```\nA goes to B\n```\n"
    assert normalize_presentation_fences(prose_block, requested_formats=("mermaid",)).actions == ()


def test_page_renderer_reads_the_same_labels():
    from pathlib import Path

    source = Path("core/chat_visuals_fragment.py").read_text(encoding="utf-8")
    assert f'lang-{CHART_FENCE_TAG}' in source
    assert f'lang-{MERMAID_FENCE_TAG}' in source


@pytest.mark.parametrize("source,formats", [
    ("```\n```python\nprint(7)\n```\n```", ()),
    ("```\n```python\nprint(11)\n```\n```", ("chart", "mermaid")),
    ("```\n```mermaid\nflowchart LR\nCedar --> Birch\n```\n```", ("table",)),
    ("````markdown\n```json\n" + CHART_BODY_DEPOT + "\n```\n````", ("chart",)),
    ("~~~markdown\n```mermaid\nflowchart LR\nCedar --> Birch\n```\n~~~", ("mermaid",)),
    ("```\n```mermaid\nflowchart LR\nCedar --> Birch\n```", ("mermaid",)),
    ("```json\n" + CHART_BODY_DEPOT + "\n```\n```json\n" + CHART_BODY_DEPOT + "\n```", ("chart",)),
])
def test_literal_unrequested_or_ambiguous_blocks_are_not_repaired(source, formats):
    result = normalize_presentation_fences(source, requested_formats=formats)
    assert result.text == source
    assert not result.changed


@pytest.mark.parametrize("marker", ["````", "~~~~"])
def test_literal_container_hides_inner_visuals_from_validation(marker):
    source = f"{marker}markdown\n```chart\n{CHART_BODY_DEPOT}\n```\n{marker}"
    assert chart_bodies(source) == []
    assert not has_terminated_block(source, "chart")


def test_normalization_preserves_crlf_and_all_non_fence_bytes():
    source = DEPOT_DRAFT.replace("\n", "\r\n")
    result = normalize_presentation_fences(source, requested_formats=("chart", "mermaid"))
    assert result.text == source.replace("```json\r\n", "```chart\r\n", 1)
