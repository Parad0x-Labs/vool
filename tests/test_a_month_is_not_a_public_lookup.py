"""Words that mark a public lookup are matched as whole words, never inside a month or the person's own words.

iMac gate35 on 584863ad5 (Offline): "find my gas bill from October" was read as a public lookup because "cto" sits inside
"october"; the turn was held to current information and the found bill was withheld as an unsupported web answer.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.task_router import looks_like_public_entity_lookup_request


@pytest.mark.parametrize("asked", [
    "find my water bill from october",
    "find my personal insurance letter",
    "find the scan of my tax letter from october 2025",
])
def test_own_things_are_not_public_lookups(asked: str) -> None:
    assert not looks_like_public_entity_lookup_request(asked)
    assert not requirements_for(asked, source_context={}).current_information_required


@pytest.mark.parametrize("asked", [
    "find the cto of the startup that makes the Pebble watch",
    "look up the founders of that crypto exchange",
    "find who owns the company behind Duolingo",
])
def test_public_lookups_stay_public(asked: str) -> None:
    assert looks_like_public_entity_lookup_request(asked)
