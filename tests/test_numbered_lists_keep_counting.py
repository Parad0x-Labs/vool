"""Numbered steps split by the model's own commentary keep counting.

A model writes "1." on every item and puts a paragraph or a code fence between the steps.
The renderer closed the list at the paragraph and each later piece became its own <ol>
starting at 1, so the answer read "1. 1. 1.". Inside one message, an ordered list that
restarts at "1." right after an earlier ordered list — separated only by paragraphs or
code — now continues with `start=`. A heading or a horizontal rule starts the numbering
over, and an explicit number the model wrote is kept exactly as written.

The shipped renderer executes under node exactly as the markdown suite does; the extraction
helper is shared with it so the two cannot drift.
"""

from __future__ import annotations

import pytest

from tests.test_the_chat_renderer_renders_markdown import NODE, _extract_js

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture(scope="module")
def render():
    js = _extract_js()

    def _run(markdown: str) -> str:
        import json
        import subprocess

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


def test_paragraphs_between_steps_do_not_restart_the_numbering(render) -> None:
    html = render("1. First\n\nSome words.\n\n1. Second\n\nMore words.\n\n1. Third")
    assert html == (
        '<ol class="chat-list"><li>First</li></ol>'
        '<p class="chat-p">Some words.</p>'
        '<ol class="chat-list" start="2"><li>Second</li></ol>'
        '<p class="chat-p">More words.</p>'
        '<ol class="chat-list" start="3"><li>Third</li></ol>'
    )


def test_a_code_block_between_steps_does_not_restart_the_numbering(render) -> None:
    html = render("1. Install the tool\n\n```\nmake install\n```\n\n1. Restart the app")
    assert '<ol class="chat-list" start="2"><li>Restart the app</li></ol>' in html
    assert html.count('<ol class="chat-list">') == 1


def test_a_heading_resets_the_numbering(render) -> None:
    html = render("1. A\n2. B\n\n## Heading\n\n1. C")
    assert html.count('<ol class="chat-list">') == 2   # both lists start at 1
    assert "start=" not in html


def test_a_horizontal_rule_resets_the_numbering(render) -> None:
    html = render("1. A\n\n---\n\n1. B")
    assert html.count('<ol class="chat-list">') == 2
    assert "start=" not in html


def test_another_kind_of_list_resets_the_numbering(render) -> None:
    """The task's rule names paragraphs and code as the only separators that continue a
    list; a bulleted list in between starts the ordered numbering over."""
    html = render("1. a\n\n- x\n- y\n\n1. b")
    assert '<ol class="chat-list"><li>a</li></ol>' in html
    assert '<ol class="chat-list"><li>b</li></ol>' in html
    assert "start=" not in html


def test_a_number_the_model_wrote_is_kept_as_written(render) -> None:
    html = render("1. A\n```\ncode\n```\n2. B")
    assert '<ol class="chat-list" start="2"><li>B</li></ol>' in html
    assert html.count("start=") == 1


def test_the_first_list_of_a_message_still_starts_at_one(render) -> None:
    html = render("Intro.\n\n1. Only step")
    assert '<ol class="chat-list"><li>Only step</li></ol>' in html
    assert "start=" not in html
