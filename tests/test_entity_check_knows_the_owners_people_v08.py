"""The entity check does not ask "which X?" about a person the owner's saved facts name as theirs.

Measured on the real local model (held-out set, fresh home): with "my brother Tobiah lives in Quillon" saved, "whats
Tobiah's phone number?" was asked back "Tobiah (biblical figure), Tobiah (character in The Lion King), Tobiah (a
person's name)?". No saved fact holds the number, but the owner means their brother. All names are invented.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.cross_chat_recall import saved_memory_names
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("my brother Tobiah lives in Quillon", "whats Tobiah's phone number?"),
        ("my manager is Pieter and the quarterly review is on december 2", "what's pieter's last name?"),
        ("our landlord is called Feodora Lask", "does Feodora have a cat?"),
    ],
)
def test_a_name_the_owner_saved_as_theirs_is_theirs(isolated_home, saved, asked):
    assert _say(f"keep in mind that {saved}", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert saved_memory_names(asked, session_id="ask", source_context={})


@pytest.mark.parametrize(
    ("saved", "asked"),
    [
        ("my cousin lives in Springfield", "what is the population of Springfield?"),
        ("my brother Tobiah lives in Quillon", "who was Anselm of Canterbury?"),
    ],
)
def test_a_name_no_saved_fact_ties_to_the_owner_is_still_checked(isolated_home, saved, asked):
    assert _say(f"keep in mind that {saved}", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert not saved_memory_names(asked, session_id="ask", source_context={})
