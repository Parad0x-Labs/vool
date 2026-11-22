"""A file whose name starts with a document word is a file to write here, not text to compose.

CI on 58f60bbf3 (shard 1): "write MARK_A85B8E1841 into memo-0302ea.txt and tell me what it holds" was read as an ask
to compose a memo, because the authoring reader matched "memo" inside the file name, and the model was never offered
the write tool. Names and nouns are built from parts at random.
"""

from __future__ import annotations

import itertools
import random

import pytest

from core.instructional_request import asks_for_instructions_not_execution

_NOUNS = ("memo", "note", "letter", "script", "summary", "template", "message", "post", "query", "poem")
_TAILS = ("-{h}.txt", ".txt", ".md", "-{h}.json", "_{h}.csv")
_VERBS = ("write", "please write", "can you write", "draft", "compose", "generate")
_RNG = random.Random(20261122)


def _file_asks() -> list[str]:
    rows = []
    for verb, noun, tail in itertools.product(_VERBS, _NOUNS, _TAILS):
        name = noun + tail.format(h=f"{_RNG.randrange(16 ** 6):06x}")
        marker = f"MARK_{_RNG.randrange(16 ** 10):010X}"
        rows.append(f"{verb} {marker} into {name} and tell me what it holds")
    return _RNG.sample(rows, 40)


@pytest.mark.parametrize("text", _file_asks())
def test_a_document_word_inside_a_file_name_is_not_an_authoring_ask(text):
    assert asks_for_instructions_not_execution(text) is False, text


@pytest.mark.parametrize(
    "text",
    _RNG.sample(
        [f"{verb} {article}{noun}{end}" for verb, article, noun, end in itertools.product(
            _VERBS, ("", "a ", "me a ", "the "), _NOUNS, (".", "", " for the team", "/reminder for Friday"))],
        30,
    ),
)
def test_asking_for_a_document_is_still_authoring(text):
    assert asks_for_instructions_not_execution(text) is True, text
