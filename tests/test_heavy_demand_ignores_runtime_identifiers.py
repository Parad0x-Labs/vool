"""A runtime identifier is not a model-size demand.

Measured on `e4f7ad2a` (served comparison family, three different tests across three runs, ~1 turn in 6):
the autopilot plan carried `explicit_heavy: true` for "Compare the VW Passat and the VW Golf in detail:
sales and production periods." The text it sized was the composed prompt, whose grounding-observations
JSON named the evidence note `evnote-e50180cf65f5f48b879b`; the size regex read `879b` as an 879-billion-
parameter request, and with no provider lane selectable in that instant the router blocked the turn before
any adapter call ("I couldn't get a usable model response"). Hex identifiers, UUIDs and hashes are never
size markers; model names ("qwen3:32b", "llama-3.1-405b", "nemotron-3-ultra-550b-a55b") still are.
"""
from __future__ import annotations

import pytest

from core.local_inference_autopilot import _explicit_heavy_requested, _largest_parameter_size_b

SCAFFOLDED = (
    'Task: Compare the VW Passat and the VW Golf in detail: sales and production periods. Grounding observations '
    'for this turn. Use them as evidence, not as a template:{"actions_taken":["initial_search","facet_search"],'
    '"evidence_note_ids":["evnote-4276e88b12f7bba019ab","evnote-e50180cf65f5f48b879b"],'
    '"request_id":"787c9fb5c0e549a5a43747f9823ffc60","turn_id":"turn-e50180cf-65f5-48b8-179b-0123456789ab"}'
)


def test_the_measured_evidence_note_id_is_not_a_size() -> None:
    assert _largest_parameter_size_b("evnote-e50180cf65f5f48b879b") == 0.0
    assert _largest_parameter_size_b("turn-e50180cf-65f5-48b8-179b-0123456789ab") == 0.0
    assert _largest_parameter_size_b("787c9fb5c0e549a5a43747f9823ffc60") == 0.0


def test_a_scaffolded_prompt_with_identifiers_is_not_a_heavy_demand() -> None:
    assert _explicit_heavy_requested(user_text=SCAFFOLDED, source_context={}) is False


@pytest.mark.parametrize(
    ("user_text", "requested_model"),
    [
        ("please run this on a 70b model", ""),
        ("", "qwen3:32b"),
        ("", "nemotron-3-ultra-550b-a55b:free"),
        ("", "llama-3.1-405b"),
        ("", "gemma-4-26b-a4b"),
        ("", "mixtral-8x22b"),
        ("use the heavy model for this", ""),
    ],
)
def test_real_size_markers_and_the_heavy_word_still_qualify(user_text: str, requested_model: str) -> None:
    assert _explicit_heavy_requested(user_text=user_text, source_context={"requested_model": requested_model}) is True


def test_a_small_model_name_is_not_heavy() -> None:
    assert _explicit_heavy_requested(user_text="", source_context={"requested_model": "qwen3:4b"}) is False
