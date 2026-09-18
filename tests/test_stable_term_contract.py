from __future__ import annotations

import json

import pytest

from core.stable_term_contract import stable_structured_definition


@pytest.mark.parametrize(
    ("intent", "words", "expected"),
    [
        ("define latency", "one word", "Delay"),
        ("define HTTP", "three words", "Hypertext Transfer Protocol"),
        ("define RAM", "three words", "Random Access Memory"),
        ("expand CPU", "three words", "Central Processing Unit"),
        ("spell out DNS", "three words", "Domain Name System"),
    ],
)
def test_registered_stable_terms_have_exact_local_answers(intent: str, words: str, expected: str) -> None:
    prompt = json.dumps(
        {
            "role": "user",
            "intent": intent,
            "format": words,
            "constraints": [f"Output exactly {words} and nothing else"],
        }
    )

    assert stable_structured_definition(prompt) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "assistant", "intent": "define HTTP", "format": "three words"},
        {"role": "user", "intent": "define a novel term", "format": "three words"},
        {"role": "user", "intent": "explain HTTP deeply", "format": "three words"},
        {"role": "user", "intent": "define HTTP", "format": "four words"},
        {"role": "user", "intent": "define HTTP"},
    ],
)
def test_unregistered_open_ended_or_untrusted_shapes_stay_with_the_model(payload: dict) -> None:
    assert stable_structured_definition(json.dumps(payload)) is None


@pytest.mark.parametrize(
    ("intent", "format_name", "expected"),
    [
        ("say hello world", "three words", "Hello brave world"),
        ("greet the user", "two words", "Hello there"),
        ("state the color of the sky", "one word", "Blue"),
    ],
)
def test_stable_exact_shape_phrases_finish_without_sampling(
    intent: str, format_name: str, expected: str
) -> None:
    prompt = json.dumps(
        {
            "role": "user",
            "intent": intent,
            "format": format_name,
            "constraints": ["Pure string only", "Do not wrap in braces"],
        }
    )

    assert stable_structured_definition(prompt) == expected
