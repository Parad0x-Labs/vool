"""A person's own project is not a public entity to look up.

CI #681: "Fix the failing check in this project ..." read as a public lookup ("check" beside
"project"), so with Web lookups off the turn was refused before the code task could open.
"""
from __future__ import annotations

import pytest

from core.task_router import looks_like_public_entity_lookup_request


@pytest.mark.parametrize(
    "ask",
    (
        "Fix the failing check in this project by renaming the weight conversion and updating its caller.",
        "fix the check in this project",
        "check my project for unused imports",
        "check our repo for the failing test",
    ),
)
def test_the_persons_own_project_is_not_a_public_lookup(ask: str) -> None:
    assert not looks_like_public_entity_lookup_request(ask)


@pytest.mark.parametrize(
    "ask",
    (
        "check the helius project on x",
        "who is the founder of the project jupiter",
        "check out this project on twitter",
    ),
)
def test_a_public_project_is_still_a_lookup(ask: str) -> None:
    assert looks_like_public_entity_lookup_request(ask)
