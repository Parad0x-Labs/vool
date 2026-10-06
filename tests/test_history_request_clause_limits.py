"""Bounded grammar limits and retained explicit execution."""
from __future__ import annotations
import pytest
from core.agent_runtime.fast_paths_machine import _has_affirmative_machine_write_verb
from core.execution.constants import image_generation_intent
from core.instructional_request import asks_for_instructions_not_execution

@pytest.mark.parametrize(("text", "claimed"), (
    ("Tell me what I saved yesterday, then create a folder on my Desktop", False),
    ("Tell me what I saved yesterday, then please create a folder on my Desktop", True),
))
def test_bare_action_after_embedded_information_is_conservatively_unclaimed(text: str, claimed: bool) -> None:
    assert _has_affirmative_machine_write_verb(text) is claimed

@pytest.mark.parametrize(("text", "prompt"), (
    ("Tell me what Morgan painted, then draw a lighthouse", None),
    ("Tell me what Morgan painted, then please draw a lighthouse", "lighthouse"),
))
def test_an_explicit_second_image_request_disambiguates_the_clause(text: str, prompt: str | None) -> None:
    assert image_generation_intent(text) == prompt

@pytest.mark.parametrize("text", (
    "Write a script and run it",
    "Draft a query and execute it",
))
def test_requested_execution_retains_the_existing_execution_override(text: str) -> None:
    assert not asks_for_instructions_not_execution(text)
