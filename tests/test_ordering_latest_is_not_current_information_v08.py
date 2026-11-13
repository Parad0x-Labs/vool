""""latest" that orders the person's own rows is not a want of current information.

Database run 16h on the v0.8 line (Mac, 2026-11-13): in a pantry database chat, "list the items,
latest expiry first" got "No. Web lookups are off.": the word "latest" held a sort order of the
person's own rows to a web lookup. The wordings below are new, except the measured one named.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.online_asks import requirements_want_a_lookup


@pytest.mark.parametrize("asked", (
    "list the items, latest expiry first",        # measured
    "show the rows sorted by latest date",
    "which item has the latest expiry date",
    "list the members by the latest join date",
    "show loans, latest due date on top",
    "list the books, latest first",
    "order the orders latest delivery date to earliest",
))
def test_latest_as_an_ordering_is_not_a_web_lookup(asked):
    need = requirements_for(asked, source_context={})
    assert not need.current_information_required, need
    assert not requirements_want_a_lookup(asked)


@pytest.mark.parametrize("asked", (
    "latest news first",
    "what is the latest version of python",
    "whats the latest on the strike",
    "show me the latest exchange rate",
    "what is the latest macos release",
))
def test_latest_about_the_world_still_needs_current_information(asked):
    assert requirements_for(asked, source_context={}).current_information_required
