"""A word inside a name is not a word of the sentence: "text-digest-example.sha256" is not "an example".

CI on 8050c2e48 (2026-11-24, test_plugin_served_journey): the sample plugin was renamed to
text-digest-example, and "Use the text-digest-example.sha256 tool ..." followed by its JSON was read
as a quoted example. The turn's tools were all muted and the plugin's own tool was never offered.
The discourse words that make quoted text the subject ("example", "quote", "means", "test case" ...)
count only standing alone, not joined into a name by a hyphen, dot, slash or underscore. Standing
alone they still do.
"""
from __future__ import annotations

import json
import random
import secrets

import pytest

from core.semantic.preflight import semantic_preflight

_RNG = random.Random()
_WORDS = ("example", "quote", "quoted", "phrase", "says", "means", "worksheet", "fable", "explain", "label")


def _name() -> str:
    word = _RNG.choice(_WORDS)
    left = _RNG.choice(("text-digest", "my", "sample", "word-count", "acme"))
    joiner = _RNG.choice(("-", "_", "."))
    return f"{left}{joiner}{word}" if _RNG.random() < 0.5 else f"{word}{joiner}{left}"


@pytest.mark.parametrize("shape", ["tool", "file", "folder"])
def test_a_name_holding_the_word_leaves_the_turn_able_to_act(shape):
    name = _name()
    supplied = "Fresh text " + secrets.token_hex(8)
    text = {
        "tool": f"Use the {name}.sha256 tool to compute the text digest.\n"
                + json.dumps({"tool": f"{name}.sha256", "text": supplied}),
        "file": f'Open {name}.txt and replace "{supplied}" with "done"',
        "folder": f'Rename the folder {name}/drafts to "{supplied}"',
    }[shape]
    found = semantic_preflight(text)
    assert found.effect_routes_safe is True, (text, found.dominant_frame)


@pytest.mark.parametrize("template", [
    'Here is an {w}: "{q}"', 'what does the phrase "{q}" mean?', 'explain "{q}"', 'my teacher says "{q}"',
])
def test_the_word_standing_alone_still_makes_the_quote_the_subject(template):
    quoted = _RNG.choice(("delete all my files", "send the money now", "rm -rf /", "book the flight"))
    text = template.format(w="example", q=quoted)
    assert semantic_preflight(text).effect_routes_safe is False, text
