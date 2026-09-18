"""PB05 — the chat page's export/copy surface, executed where possible.

The page is a large inline script; tests here execute the shipped functions under node (the same
approach as the renderer suites) rather than grepping for strings, except where the claim IS a
string (a disclosure must literally exist to be honest).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()

NODE = shutil.which("node")

# A minimal DOM/c Window shim: enough for the sliced functions to EXECUTE, not enough to pretend
# a browser happened. El's textContent->innerHTML sync is load-bearing: the page's `esc` escapes
# through a detached element's innerHTML, exactly as it does in the browser.
SHIM = """
class El {
  constructor() { this._t = ''; this._h = ''; this.dataset = {}; this.style = {}; this.classList = { add(){}, remove(){}, toggle(){} }; }
  set textContent(v) {
    this._t = String(v == null ? '' : v);
    this._h = this._t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  get textContent() { return this._t; }
  set innerHTML(v) { this._h = String(v); }
  get innerHTML() { return this._h; }
  appendChild(c) { return c; }
  querySelector() { return null; }
  addEventListener() {}
  remove() {}
}
const document = { createElement: () => new El() };
"""


def _page_script() -> str:
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    return scripts[0]


def _run_node(program: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"page slice failed under node:\n{result.stderr}"
    return result.stdout


def _slice(start: str, end: str, *, include_end: bool = False) -> str:
    """Slice the page script at `start`. With include_end=False the `end` marker is
    excluded (the extracted JavaScript stops just before it); with include_end=True the
    marker itself terminates the extracted function and is kept."""
    source = _page_script()
    lo = source.index(start)
    hi = source.index(end, lo)
    if include_end:
        hi += len(end)
    return source[lo:hi].rstrip() + "\n"


requires_node = pytest.mark.skipif(NODE is None, reason="node not available")


# --------------------------------------------------------------------------- #
# The surface exists and discloses honestly
# --------------------------------------------------------------------------- #


def test_export_control_and_dialog_exist() -> None:
    assert 'id="exportBtn"' in HTML
    assert 'id="exportOverlay"' in HTML
    assert "/api/chat/export" in HTML
    # The dialog names the inclusion controls explicitly.
    assert 'id="exportFormat"' in HTML
    assert 'id="exportTimestamps"' in HTML
    assert 'id="exportAttachments"' in HTML
    assert 'id="exportPreview"' in HTML


def test_export_disclosures_are_literally_present() -> None:
    # No hidden cut-off: the promise and its exclusions are stated on the surface.
    assert "every message, not just what is on screen" in HTML
    assert "no hidden cut-off" in HTML
    assert "model reasoning" in HTML
    assert "tool/activity traces" in HTML
    # Timestamps are disclosed as assistant-side completion times only.
    assert "assistant completion times only" in HTML
    # Attachment references never carry the bytes.
    assert "never file bytes" in HTML


def test_copy_controls_declare_their_representation() -> None:
    assert "Copy message source (raw Markdown, exactly as stored)" in HTML
    assert "Copy as plain text (Markdown formatting removed)" in HTML
    assert "msg-copy-text" in HTML
    assert "msg-copy-rich" in HTML
    assert "function mdToPlainText" in HTML
    assert "function richClipboardSupported" in HTML
    assert "function copyRichMessage" in HTML
    # The rich copy says plainly that the receiving app decides rendering.
    assert "the receiving app decides how it renders" in HTML


def test_code_block_copy_button_is_rendered_by_the_renderer() -> None:
    assert "chat-code-wrap" in HTML
    assert 'class="code-copy"' in HTML
    assert "Copy this code block exactly" in HTML


# --------------------------------------------------------------------------- #
# Executed under node
# --------------------------------------------------------------------------- #


@requires_node
def test_renderer_wraps_fenced_code_with_copy_button_and_exact_body() -> None:
    from urllib.parse import quote

    markdown = "before\n\n```python\n\tkeep `\tthis\n```\n\nafter"
    # Fence-boundary rule: the span is the lines strictly between the markers — the newline
    # before the closing ``` is NOT part of the payload.
    encoded_body = quote("\tkeep `\tthis", safe="")
    body = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    out = _run_node(
        SHIM
        + body
        + "\nconst el = new El();\n"
        + f"renderRichText(el, {json.dumps(markdown)});\n"
        + "process.stdout.write(el.innerHTML);\n"
    )
    assert (
        f'<div class="chat-code-wrap" data-raw-code="{encoded_body}">'
        '<button type="button" class="code-copy" title="Copy this code block exactly">Copy</button>'
        '<pre class="chat-code">' in out
    )
    assert "\tkeep `\tthis" in out  # exact body, tabs and inner backtick intact
    assert "</code></pre></div>" in out


@requires_node
def test_md_to_plain_text_strips_decoration_keeps_content() -> None:
    raw = (
        "# Heading\n"
        "bold **words** and `code span` and a [link](https://x.example/a)\n"
        "- bullet one\n"
        "```\ncode\tline\n```\n"
        "> quoted thought\n"
        "| A | B |\n"
    )
    body = _slice("function mdToPlainText(raw) {", "  return out.join('\\n');\n}", include_end=True)
    out = _run_node(SHIM + body + f"\nprocess.stdout.write(mdToPlainText({json.dumps(raw)}));\n")
    assert "Heading" in out and "# Heading" not in out
    assert "bold words" in out and "**" not in out
    assert "code span" in out and "`" not in out.replace("code\tline", "")
    assert "a link (https://x.example/a)" in out
    assert "- bullet one" in out
    assert "code\tline" in out and "```" not in out
    assert "quoted thought" in out
    assert "| A | B |" in out


@requires_node
@pytest.mark.parametrize("marker", ["````", "~~~~"])
def test_plain_copy_keeps_inner_visual_fences_literal(marker) -> None:
    body_text = '```mermaid\r\nflowchart LR\r\nCedar --> Birch\r\n```'
    raw = marker + 'markdown\n' + body_text + '\n' + marker
    body = _slice("function mdToPlainText(raw) {", "  return out.join('\\n');\n}", include_end=True)
    out = _run_node(SHIM + body + f"\nprocess.stdout.write(JSON.stringify(mdToPlainText({json.dumps(raw)})));\n")
    assert json.loads(out) == body_text  # JSON avoids subprocess text-mode CRLF translation.


@requires_node
def test_code_copy_carries_the_original_source_span() -> None:
    """The copy payload is the ORIGINAL fenced source — exact \r, tabs, Unicode — carried on the
    wrapper from the raw string at render time, never the browser-normalized textContent. The
    value travels base64 so the subprocess pipe's newline translation cannot eat the \r bytes
    this test exists to protect."""
    raw_body = "a\r\nb\ttabbed `tick` 中文\r"
    markdown = "before\n\n```text\n" + raw_body + "\n```\n\nafter"
    body = _slice("function esc(", "\n  el.innerHTML = html;\n}", include_end=True)
    out = _run_node(
        SHIM
        + body
        + "\nconst el = new El();\n"
        + f"renderRichText(el, {json.dumps(markdown)});\n"
        + "const m = el.innerHTML.match(/data-raw-code=\"([^\"]*)\"/);\n"
        + "process.stdout.write(m ? Buffer.from(decodeURIComponent(m[1]), 'utf8').toString('base64') : 'NO-ATTRIBUTE');\n"
    )
    import base64

    assert out != "NO-ATTRIBUTE"
    decoded = base64.b64decode(out).decode("utf-8")
    assert decoded == raw_body, f"copy span lost the source bytes: {decoded!r}"


@requires_node
def test_rich_payload_strips_generated_controls() -> None:
    """The rich clipboard payload is the message, not the page chrome: richHtmlFor renders with
    the page's own renderer and strips the generated controls. A string-stub DOM cannot parse
    HTML, so this pins the stripping CONTRACT (selector + removal); the real payload check is
    served-browser (amendment proof reads the actual text/html back off the clipboard)."""
    markdown = "code:\n\n```js\nx()\n```\n\ntext after"
    body = _slice("function richHtmlFor(raw) {", "\nfunction copyRichMessage")
    program = (
        SHIM
        + body
        + "\nfunction esc(t) { const d = document.createElement('div'); d.textContent = (t == null ? '' : String(t)); return d.innerHTML; }\n"
        + "function safeUrl(u) { return ''; }\n"
        + _slice("function renderInline(html) {", "\n  el.innerHTML = html;\n}", include_end=True)
        + "\nconst calls = [];\n"
        + "El.prototype.querySelectorAll = function (sel) { calls.push(sel); return [{ remove() { calls.push('removed'); } }, { remove() { calls.push('removed'); } }]; };\n"
        + f"const html = richHtmlFor({json.dumps(markdown)});\n"
        + "process.stdout.write(JSON.stringify({ selector: calls[0], removed: calls.filter((c) => c === 'removed').length, rendered: html.length > 0 }));\n"
    )
    out = json.loads(_run_node(program))
    assert out["selector"] == "button, .code-copy"
    assert out["removed"] == 2
    assert out["rendered"] is True


@requires_node
def test_copy_failure_is_reported_not_celebrated() -> None:
    """A failed copy (writeText rejects, execCommand false) must never show 'Copied'."""
    source = _page_script()
    lo = source.index("function copyText(text, btn) {")
    hi = source.index("\n// Styled-clipboard support is probed", lo)
    body = source[lo:hi].rstrip() + "\n"
    program = (
        "class El {\n"
        "  constructor() { this._t = ''; this._h = ''; this.dataset = {}; this.style = {}; }\n"
        "  set textContent(v) { this._t = String(v == null ? '' : v); } get textContent() { return this._t; }\n"
        "  set innerHTML(v) { this._h = String(v); } get innerHTML() { return this._h; }\n"
        "  appendChild(c) { return c; } removeChild(c) { return c; } remove() {} focus() {} select() {}\n"
        "}\n"
        "const body = new El();\n"
        "const document = { createElement: () => new El(), body: body, execCommand: () => false };\n"
        "const navigator = { clipboard: { writeText: () => Promise.reject(new Error('denied')) } };\n"
        "const window = {};\nglobalThis.window = window;\n"
        + body
        + "\nconst btn = new El();\n"
        + "btn.textContent = 'Copy';\n"
        + "copyText('payload', btn);\n"
        + "setTimeout(() => { process.stdout.write(btn.textContent); }, 50);\n"
    )
    out = _run_node(program)
    assert out == "Copy failed", f"failed copy reported as {out!r}"


@requires_node
def test_textarea_fallback_refuses_cr_payloads() -> None:
    """The textarea fallback normalizes CR to LF (HTML spec), so a \r-bearing payload would be
    copied MUTATED and reported Copied. That copy must be refused as the failure it is."""
    source = _page_script()
    lo = source.index("function copyText(text, btn) {")
    hi = source.index("\n// Styled-clipboard support is probed", lo)
    body = source[lo:hi].rstrip() + "\n"
    program = (
        "class El {\n"
        "  constructor() { this._t = ''; this._h = ''; this.dataset = {}; this.style = {}; }\n"
        "  set textContent(v) { this._t = String(v == null ? '' : v); } get textContent() { return this._t; }\n"
        "  set innerHTML(v) { this._h = String(v); } get innerHTML() { return this._h; }\n"
        "  appendChild(c) { return c; } removeChild(c) { return c; } remove() {} focus() {} select() {}\n"
        "}\n"
        "const body = new El();\n"
        "const document = { createElement: () => new El(), body: body, execCommand: () => true };\n"
        "const navigator = { clipboard: { writeText: () => Promise.reject(new Error('denied')) } };\n"
        "const window = {};\nglobalThis.window = window;\n"
        + body
        + "\nconst btn = new El();\n"
        + "btn.textContent = 'Copy';\n"
        + "copyText('line1\\r\\nline2', btn);\n"
        + "setTimeout(() => { process.stdout.write(btn.textContent + '|exec:' + String(document.execCommandCalled || 'n/a')); }, 50);\n"
    )
    out = _run_node(program)
    assert out.startswith("Copy failed"), f"CR payload was fallback-copied and reported as {out!r}"


@requires_node
def test_rich_copy_support_probe_is_honest() -> None:
    # Slice THROUGH copyRichMessage so the extracted script is complete JavaScript (the
    # marker itself terminates the slice), ending before the next function.
    body = _slice("function richClipboardSupported() {", "\nfunction mdToPlainText(raw) {")
    without = (
        "const navigator = {};\nconst window = {};\nglobalThis.window = window;\n"
        + body
        + "\nprocess.stdout.write(String(richClipboardSupported()));\n"
    )
    assert _run_node(without).strip() == "false"
    with_support = (
        "const navigator = { clipboard: { write: () => Promise.resolve() } };\n"
        "class CI {}\nconst window = { ClipboardItem: CI };\nglobalThis.window = window;\n"
        + body
        + "\nprocess.stdout.write(String(richClipboardSupported()));\n"
    )
    assert _run_node(with_support).strip() == "true"
