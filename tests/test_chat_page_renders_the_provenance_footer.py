"""The provenance footer must survive the renderer that draws the table above it.

The action response that exposed the missing footer is a MARKDOWN TABLE. The chat page's block
parser consumes table rows in a loop (`while (isTableRow(lines[i]))`), so a line appended under a
table is exactly the line most at risk of being eaten by it -- and a footer that is emitted by the
server and then swallowed by the client is indistinguishable, to the user, from the defect this
repair closes.

Every case below EXECUTES the shipped page script under node against the real
`renderRichText`, and asserts on the HTML it actually produced. Nothing here reads the source and
reasons about what it would do.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.app_version import VOOL_VERSION
from tests.chat_page_js_harness import DOM, script

ACTION_BODY = (
    "Files — workspace root\n\n"
    "| File | Count | State |\n"
    "| --- | --- | --- |\n"
    "| exact_one_file_6204.txt | 1 | written |\n\n"
    "Scope: workspace root\n"
)
FOOTER = f"`tool | exact_workspace_write | 1 file | no model | build {VOOL_VERSION}`"
CHAT_FOOTER = "`local | qwen2.5:7b | 930 tok`"


_SENTINEL = "__provenance_render__"


def _rendered(cases: dict[str, str]) -> dict:
    """Render each case through the real `renderRichText` under node.

    Runs the program directly rather than through `chat_page_js_harness.run_node`, which reads the
    LAST stdout line: the booted page emits a diagnostic JSON line of its own after ours, so
    "last line" is not this program's answer. Selecting by sentinel key is order-independent.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    program = (
        DOM
        + script()
        + """
const out = {};
const CASES = """
        + json.dumps(cases)
        + """;
for (const [name, raw] of Object.entries(CASES)) {
  const el = new El('div');
  renderRichText(el, raw);
  out[name] = el.innerHTML;
}
console.log(JSON.stringify({ """
        + json.dumps(_SENTINEL)
        + """: out }));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=90)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"the chat page harness failed under node:\n{result.stderr}"
    for line in result.stdout.splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and _SENTINEL in payload:
            return payload[_SENTINEL]
    raise AssertionError(f"no rendered output on stdout:\n{result.stdout}\n{result.stderr}")


def test_the_footer_under_a_table_is_rendered_and_not_swallowed() -> None:
    rendered = _rendered(
        {
            "table_then_footer": ACTION_BODY + "\n" + FOOTER,
            "table_only": ACTION_BODY,
            "plain_then_footer": "Here is the answer.\n\n" + CHAT_FOOTER,
        }
    )

    table_html = rendered["table_then_footer"]
    assert "<table class=\"chat-table\">" in table_html, table_html
    assert "exact_workspace_write" in table_html, (
        "the footer vanished under the table renderer -- the server emitted it and the client ate it"
    )
    assert "no model" in table_html
    # and the row above it is still a table row, not swallowed into the footer's paragraph
    assert "exact_one_file_6204.txt" in table_html

    assert "exact_workspace_write" not in rendered["table_only"]
    assert "qwen2.5:7b" in rendered["plain_then_footer"]


def test_the_footer_renders_as_a_code_span_not_as_prose() -> None:
    """It has to read as machine metadata. Rendered as prose it becomes a sentence the assistant
    appears to have written, which is a different and worse claim than no footer at all."""
    rendered = _rendered({"footer": "Answer.\n\n" + CHAT_FOOTER})["footer"]

    assert "<code" in rendered, rendered
    assert 'class="chat-provenance"' in rendered, rendered
    assert "qwen2.5:7b" in rendered


def test_the_footers_pipes_do_not_start_a_table() -> None:
    """The footer is pipe-separated and sits directly under a table. If the block parser read it as
    another row, the model/lane fields would silently become table cells."""
    rendered = _rendered({"footer_alone": FOOTER})["footer_alone"]

    assert "<table" not in rendered, rendered
    assert "exact_workspace_write" in rendered


def test_a_long_footer_stays_on_one_line_and_is_allowed_to_ellipsize() -> None:
    """Compact width: the line may be truncated for display, but it may not be absent, and it may
    not wrap the answer's layout by forcing the bubble wider than the pane."""
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    # The rule that makes an over-wide code span scroll or ellipsize inside the bubble rather than
    # widen it. Asserted on the shipped stylesheet, because this is a CSS-only guarantee.
    assert ".chat-provenance" in html, "the footer has no width rule of its own in the shipped page"


def test_an_ordinary_inline_code_span_is_not_treated_as_a_footer() -> None:
    rendered = _rendered({"code": "Run `ls -la` to check."})["code"]

    assert "ls -la" in rendered
    assert "chat-provenance" not in rendered
