"""Fence stripping must stay bounded on runs of openers that are never closers.

``fired_triggers`` and ``select_presentation`` strip fenced code blocks before prose
predicates run; a run of mid-line fences opens a candidate span every time but never
closes one, which previously re-walked the whole suffix once per opener.
"""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("script", [
    "from core.presentation_selection import fired_triggers; fired_triggers('note\\n' + 'x```js\\n' * 6000)",
    "from core.presentation_selection import _strip_fenced_blocks; _strip_fenced_blocks('x```js\\n' * 20000)",
    "from core.presentation_selection import _strip_fenced_blocks; _strip_fenced_blocks('```js\\nlet x = 1;\\n' * 20000)",
])
def test_unclosed_fence_openers_do_not_rescan_suffixes(script):
    subprocess.run([sys.executable, "-c", script], check=True, timeout=3,
                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.mark.parametrize("text,expected", [
    ("", ""),
    ("no fences, plain prose", "no fences, plain prose"),
    # A completed block is removed whole; the bytes around the fence stay.
    ("```js\nlet x = 1;\n```\nafter", "\nafter"),
    ("```js\nlet x = 1;\n   ```\nafter", "\nafter"),
    ("before\n```py\nx = 2\n```\nafter", "before\n\nafter"),
    # An unclosed opener keeps its content; mid-line fences are openers but never closers.
    ("x```js\n" * 3 + "tail", "x```js\n" * 3 + "tail"),
    ("```unclosed\ncontent", "```unclosed\ncontent"),
    # The first closer ends the first span; a later opener may become plain remaining text.
    ("```a\n```\n```b\n```\nrest", "b\n```\nrest"),
    # Whitespace lines between content and closer belong to the removed span.
    ("```a\nbody\n\n\n   ```", ""),
    ("  ```\n\n  ```  ", "    "),
    ("```py\nnested ```mid\nline```\n```", ""),
    ("````\ncode\n````", "`"),
    ("```a\n````", "```a\n````"),
])
def test_fence_stripping_preserves_span_boundaries(text, expected):
    from core.presentation_selection import _strip_fenced_blocks

    assert _strip_fenced_blocks(text) == expected


def test_prose_shape_triggers_still_fire_across_fences():
    from core.presentation_selection import fired_triggers

    fenced = (
        "intro line\n"
        "```json\n{\"a\": 1}\n```\n"
        "1. first step\n2. second step\n"
    )
    triggers = fired_triggers(fenced)
    assert "ordered_steps_ge2" in triggers
