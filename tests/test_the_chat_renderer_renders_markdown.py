"""A report that arrives as Markdown must not reach the operator as literal `##` and `**`.

`renderRichText` escaped HTML, linkified, and turned `\\n` into `<br>` — and nothing else. Every
heading, bold run, bullet, code fence and table in an answer was displayed as the raw characters, so
a well-structured report looked like a broken one. No prompt change could fix it: the model was
already emitting correct Markdown and the renderer was throwing it away.

These tests EXECUTE the shipped JavaScript under node with a minimal DOM, rather than asserting on
its source. A renderer test that only greps for `<strong>` proves the string is in the file; it
cannot tell a working parser from one that never runs.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "core" / "vool_chat_page.py"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# A DOM small enough to read and real enough to run the shipped code: `esc` round-trips text through
# an element, and `renderRichText` writes to `innerHTML` and `dataset`.
SHIM = """
class El {
  constructor() { this._text = ''; this._html = ''; this.dataset = {}; }
  set textContent(v) {
    this._text = String(v == null ? '' : v);
    this._html = this._text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  get textContent() { return this._text; }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
}
const document = { createElement: () => new El() };
const location = { origin: 'http://localhost:11435' };
"""


def _extract_js() -> str:
    source = PAGE.read_text(encoding="utf-8")
    start = source.index("function renderInline(html) {")
    end = source.index("\n  el.innerHTML = html;\n}", start) + len("\n  el.innerHTML = html;\n}")
    body = source[start:end]
    helpers = re.search(
        r"function esc\(t\)[^\n]*\n(?:.*?\n)?function safeUrl\(u\) \{.*?\n\}", source, re.S
    )
    assert helpers, "esc/safeUrl not found in the page"
    return SHIM + "\n" + helpers.group(0) + "\n" + body


@pytest.fixture(scope="module")
def render():
    js = _extract_js()

    def _run(markdown: str) -> str:
        script = (
            js
            + "\nconst el = new El();\n"
            + f"renderRichText(el, {json.dumps(markdown)});\n"
            + "process.stdout.write(el.innerHTML);\n"
        )
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    return _run


# --------------------------------------------------------------------------------------
# What the operator was actually seeing
# --------------------------------------------------------------------------------------


def test_a_heading_is_a_heading(render) -> None:
    html = render("## Audit\n\nSome text.")
    assert "<h2 class=\"chat-h\">Audit</h2>" in html
    assert "##" not in html


def test_bold_and_italic(render) -> None:
    html = render("This is **important** and this is *subtle*.")
    assert "<strong>important</strong>" in html
    assert "<em>subtle</em>" in html
    assert "**" not in html


def test_a_bullet_list(render) -> None:
    html = render("- first\n- second\n- third")
    assert html.count("<li>") == 3
    assert "<ul class=\"chat-list\">" in html
    assert "\n- " not in html


def test_a_numbered_list(render) -> None:
    html = render("1. first\n2. second")
    assert "<ol class=\"chat-list\">" in html
    assert html.count("<li>") == 2


def test_a_table(render) -> None:
    html = render("| File | Lines |\n|---|---|\n| a.py | 12 |\n| b.py | 40 |")
    assert "<table class=\"chat-table\">" in html
    assert '<th scope="col">File</th>' in html
    assert '<th scope="col">Lines</th>' in html
    assert '<caption class="sr-only">Data table</caption>' in html
    assert "<td>a.py</td>" in html
    assert html.count("<tr>") == 3


def test_a_wide_table_scrolls_inside_its_own_box(render) -> None:
    """The message column must never scroll sideways."""

    html = render("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<div class=\"chat-table-wrap\">" in html


def test_inline_code(render) -> None:
    html = render("Call `decompress()` on the blob.")
    assert "<code>decompress()</code>" in html
    assert "`" not in html


def test_a_fenced_code_block(render) -> None:
    html = render("Here:\n\n```python\ndef go():\n    return 1\n```\n")
    assert "<pre class=\"chat-code\">" in html
    assert "lang-python" in html
    assert "def go():" in html


@pytest.mark.parametrize("marker", ["````", "~~~~"])
def test_literal_fence_container_does_not_activate_inner_visual(render, marker):
    source = f"{marker}markdown\n```mermaid\nflowchart LR\nCedar --> Birch\n```\n{marker}"
    html = render(source)
    assert html.count('<pre class="chat-code">') == 1
    assert 'class="lang-mermaid"' not in html
    assert '```mermaid' in html
    assert 'Cedar --&gt; Birch' in html


def test_visual_fence_case_matches_server_validation(render):
    assert 'class="lang-mermaid"' in render("```MERMAID\nflowchart LR\nA --> B\n```")


def test_a_horizontal_rule_and_a_blockquote(render) -> None:
    assert "<hr class=\"chat-hr\">" in render("above\n\n---\n\nbelow")
    assert "<blockquote class=\"chat-quote\">note</blockquote>" in render("> note")


def test_the_whole_audit_report_shape(render) -> None:
    """The real thing: what a workspace audit actually emits."""

    html = render(
        "# Code audit\n\n"
        "**COMPLETE** - inspected 2 of 2 file(s).\n\n"
        "## Findings\n\n"
        "### [P1] Bare except\n"
        "- Evidence: `api/x.py:63`\n"
        "- Fix: narrow to the expected error types\n\n"
        "| File | Lines |\n|---|---|\n| api/x.py | 120 |\n"
    )
    for expected in ("<h1 class=\"chat-h\">", "<h2 class=\"chat-h\">", "<h3 class=\"chat-h\">",
                     "<strong>COMPLETE</strong>", "<ul class=\"chat-list\">",
                     "<code>api/x.py:63</code>", "<table class=\"chat-table\">"):
        assert expected in html, expected
    for literal in ("# ", "## ", "**COMPLETE**", "|---|"):
        assert literal not in html, literal


# --------------------------------------------------------------------------------------
# What must NOT change
# --------------------------------------------------------------------------------------


def test_html_in_the_answer_is_still_escaped(render) -> None:
    """Escaping happens once, up front. Page content can never inject markup."""

    html = render("Beware of <script>alert(1)</script> in user text.")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_a_javascript_url_never_becomes_a_link(render) -> None:
    html = render("[click](javascript:alert(1))")
    assert "javascript:" not in html.lower().replace("&#", "") or "<a href" not in html


def test_a_real_link_still_works(render) -> None:
    html = render("See [the docs](https://example.com/page) for details.")
    assert 'href="https://example.com/page"' in html
    assert 'rel="noopener noreferrer"' in html


def test_a_bare_url_is_still_auto_linked(render) -> None:
    html = render("Try https://example.com/some/page now.")
    assert "<a href=" in html and "example.com" in html


def test_an_image_still_renders(render) -> None:
    html = render("![a cat](https://example.com/cat.png)")
    assert 'class="chat-img"' in html
    assert 'src="https://example.com/cat.png"' in html


def test_the_raw_text_is_still_kept_for_copy(render) -> None:
    """`dataset.raw` is what the copy button and the transcript use."""

    js = _extract_js()
    script = (
        js
        + "\nconst el = new El();\nrenderRichText(el, '## Hi\\n\\nthere');\n"
        + "process.stdout.write(el.dataset.raw);\n"
    )
    proc = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "## Hi\n\nthere"


def test_markdown_inside_a_code_fence_is_left_alone(render) -> None:
    """A code sample that contains `**` or a URL must survive exactly as written."""

    html = render("```\nnot **bold** and https://example.com stays\n```")
    assert "<strong>" not in html
    assert "not **bold**" in html


def test_a_snake_case_name_is_not_italicised(render) -> None:
    """`_` is only markup between word boundaries; `some_function_name` is a name."""

    html = render("Call some_function_name in that module.")
    assert "<em>" not in html
    assert "some_function_name" in html


def test_plain_prose_is_unchanged_apart_from_being_a_paragraph(render) -> None:
    html = render("Just a sentence.")
    assert html == '<p class="chat-p">Just a sentence.</p>'


def test_empty_text_renders_nothing(render) -> None:
    assert render("") == ""


# --------------------------------------------------------------------------------------
# Wired
# --------------------------------------------------------------------------------------


def test_the_styles_for_every_emitted_class_exist() -> None:
    """A parser that emits classes the stylesheet does not know is a parser nobody can read."""

    page = PAGE.read_text(encoding="utf-8")
    for css_class in ("chat-p", "chat-h", "chat-hr", "chat-list", "chat-quote",
                      "chat-code", "chat-table", "chat-table-wrap"):
        assert f".{css_class} " in page or f".{css_class}," in page or f".{css_class}:" in page, css_class
