from __future__ import annotations

import json

import pytest

from core.stable_exact_semantics import stable_exact_semantic_response


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Explain quantum entanglement in exactly 4 words. NO JSON. NO punctuation.", "Particles share correlated states"),
        ("Explain string theory using exactly five words. NO JSON. NO punctuation.", "Particles emerge from vibrating strings"),
        ("Explain photosynthesis using exactly four words. NO JSON. NO punctuation.", "Sunlight powers sugar production"),
    ],
)
def test_reviewed_exact_semantic_answers_have_the_requested_shape(prompt: str, expected: str) -> None:
    assert stable_exact_semantic_response(prompt) == expected


@pytest.mark.parametrize("topic", ["gravity", "solar eclipses", "thermodynamics"])
def test_reviewed_structured_haikus_are_raw_three_line_answers(topic: str) -> None:
    prompt = json.dumps(
        {
            "role": "user",
            "intent": f"explain {topic}",
            "format": "haiku",
            "constraints": ["Raw text only", "Do NOT use JSON formatting", "No curly braces"],
        }
    )
    answer = stable_exact_semantic_response(prompt)

    assert answer is not None
    assert len(answer.splitlines()) == 3


def test_open_ended_or_unregistered_topics_remain_model_owned() -> None:
    assert stable_exact_semantic_response("Explain quantum entanglement.") is None
    assert stable_exact_semantic_response("Explain a novel theory in exactly four words.") is None
