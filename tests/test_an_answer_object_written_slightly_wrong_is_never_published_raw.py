"""An answer object the model wrote slightly wrong is still that answer, never its braces and key.

Benchmark on d9a520dca (local qwen3:8b, a find + read ask on the owner's files): the clean loop's last step
returned an answer object with a trailing comma, it failed to decode, and the reply the person saw was the raw
`{"answer": ...}` text. Small models also put raw line breaks inside the string, or stop before the object closes.
"""
from __future__ import annotations

import pytest

from core.clean_loop.loop import parse_action


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"answer": "The kettle warranty runs until May.", }', {"answer": "The kettle warranty runs until May."}),
        ('```json\n{"answer": "Two invoices mention it.",\n}\n```', {"answer": "Two invoices mention it."}),
        ('{"answer": "First the boiler.\nThen the gas."}', {"answer": "First the boiler.\nThen the gas."}),
        ('{"answer": "Sąskaita už katilą yra 86,40 EUR', {"answer": "Sąskaita už katilą yra 86,40 EUR"}),
        ('{"tool": "find", "query": "Garantie",}', {"tool": "find", "query": "Garantie"}),
    ],
)
def test_a_badly_closed_object_is_read_as_that_object(text, expected):
    assert parse_action(text) == expected


@pytest.mark.parametrize("text", ["Your desktop has a to-do list.", "The file says {draft} at the top."])
def test_prose_stays_prose(text):
    assert parse_action(text) == {"answer": text}
