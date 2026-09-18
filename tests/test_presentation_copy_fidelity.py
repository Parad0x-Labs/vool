"""Copy/paste fidelity for presentation-formatted answers (C19).

The presentation layer may reformat an answer into a table, a timeline, a tree, or fenced
diagram source — the COPY paths must carry every value byte-exact regardless of shape. This
pack executes the shipped chat-page functions under node (the harness of
tests/test_chat_export_copy_ui.py) against presentation-shaped markdown:

- a comparison TABLE renders every cell value into the markup (nothing dropped or rewritten);
- mdToPlainText keeps the table's pipe rows verbatim (the plain-text copy path);
- a mermaid source fence round-trips through the code-copy span byte-exact.
"""
from __future__ import annotations

import base64
import json

from tests.test_chat_export_copy_ui import HTML, SHIM, _page_script, _run_node, _slice, requires_node  # noqa: F401

_TABLE_MARKDOWN = (
    "Comparison of the three plans:\n\n"
    "| Plan | Price | Support |\n"
    "|---|---|---|\n"
    "| Alpha | €10 [1] | email |\n"
    "| Beta | €25 | 24/7 |\n"
    "| Gamma | €0 | none |\n\n"
    "Source: workspace/quote.md [1]"
)

_MERMAID_MARKDOWN = (
    "The flow:\n\n"
    "```mermaid\n"
    "graph TD; Order-->Pack; Pack-->Ship;\n"
    "```\n"
)


@requires_node
def test_a_table_answer_renders_every_cell_value_into_the_page() -> None:
    # The house slice: esc + renderInline + renderRichText are contiguous in the page script.
    body = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    out = _run_node(
        SHIM
        + body
        + "\nconst el = new El();\n"
        + f"renderRichText(el, {json.dumps(_TABLE_MARKDOWN)});\n"
        + "process.stdout.write(el.innerHTML);\n"
    )
    for cell in ("Alpha", "€10 [1]", "email", "Beta", "€25", "24/7", "Gamma", "€0", "none"):
        assert cell in out, f"the rendered table lost the cell value {cell!r}"
    assert "chat-table" in out, "the table did not render through the page's table path"


@requires_node
def test_the_plain_text_copy_path_keeps_table_rows_verbatim() -> None:
    body = _slice("function mdToPlainText(raw) {", "  return out.join('\\n');\n}", include_end=True)
    out = _run_node(
        SHIM + body + f"\nprocess.stdout.write(mdToPlainText({json.dumps(_TABLE_MARKDOWN)}));\n"
    )
    for row in ("| Plan | Price | Support |", "| Alpha | €10 [1] | email |", "| Beta | €25 | 24/7 |"):
        assert row in out, f"the plain-text copy path rewrote the table row {row!r}"
    assert "Source: workspace/quote.md [1]" in out, "the citation did not survive plain-text copy"


@requires_node
def test_mermaid_source_copies_byte_exact_through_the_code_span() -> None:
    """A mermaid 'presentation' on this surface IS its fenced source text — and the copy
    path must carry that source byte-exact, exactly like any other code block."""
    raw_body = "graph TD; Order-->Pack; Pack-->Ship;"
    markdown = "The flow:\n\n```mermaid\n" + raw_body + "\n```\n"
    body = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    out = _run_node(
        SHIM
        + body
        + "\nconst el = new El();\n"
        + f"renderRichText(el, {json.dumps(markdown)});\n"
        + "const m = el.innerHTML.match(/data-raw-code=\"([^\"]*)\"/);\n"
        + "process.stdout.write(m ? Buffer.from(decodeURIComponent(m[1]), 'utf8').toString('base64') : 'NO-ATTRIBUTE');\n"
    )
    assert out != "NO-ATTRIBUTE", "the mermaid fence did not render through the code-copy path"
    decoded = base64.b64decode(out).decode("utf-8")
    assert decoded == raw_body, f"mermaid source lost bytes on copy: {decoded!r}"
