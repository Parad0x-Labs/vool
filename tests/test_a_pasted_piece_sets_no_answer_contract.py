"""A piece pasted under one request to rework it is material: its words set no answer contract.

Measured served on the v0.8 round 6 candidate (11eef8dc), skills A/B: under "Turn this into an X
article:" the pasted article said "return checklist", one of the answer contract's plan words, so
the turn was asked of the model as a JSON plan ({"summary", "steps"}) and the x-article-builder's
article could not come back. "Translate this into German:" over the same article was a plan too,
and "Summarise the article below in three bullets" read one item ("one clear next step") instead
of the three bullets the ask names.
"""
from __future__ import annotations

import pytest

from core.reasoning_engine import explicit_planner_style_requested, resolve_requested_answer_contract

ARTICLE = """# How our tool library works

Maple Court started lending tools in spring. Every loan is written in the register, so anyone can
read who has the ladder and when it comes back. The return checklist is short enough to keep on
the shelf, and the volunteer workflow fits on one card.

There are three steps to borrow a drill: find it on the shelf map, sign the register, and bring it
back by Saturday. Volunteers check each item when it is returned: blades, cords and batteries.

[PICTURE 1: shelf.png]

We need two more people for Saturday mornings. Give people one clear next step: the joining page.
"""

HEADS = (
    "Turn this into an X article:",
    "Make this an X thread:",
    "Translate this into German:",
    "Rewrite this for our newsletter:",
    "Summarise the article below in three bullets",
    # The ask's own plan word still asks for a plan.
    "Turn this into a checklist:",
)


@pytest.mark.parametrize("head", HEADS)
def test_the_paste_changes_nothing_in_the_contract_the_ask_sets(head: str) -> None:
    turn = f"{head}\n\n{ARTICLE}"
    assert resolve_requested_answer_contract(turn) == resolve_requested_answer_contract(head)
    if head.endswith(":"):
        # The front door's normalized turn runs the paste into the ask's line; the colon still
        # ends the ask there (with no colon the line break was the only edge, and the whole turn
        # is read as before).
        flattened = " ".join(turn.split())
        assert resolve_requested_answer_contract(flattened) == resolve_requested_answer_contract(head)


def test_an_article_to_turn_into_an_x_article_is_not_asked_for_as_a_plan() -> None:
    turn = f"Turn this into an X article:\n\n{ARTICLE}"
    assert explicit_planner_style_requested(turn) is False
    assert resolve_requested_answer_contract(turn).kind == "answer"


@pytest.mark.parametrize(
    "turn",
    [
        "Make me a plan for launching the tool library, with a checklist",
        # Material first and the ask after it: the whole turn is read, as before.
        f"{ARTICLE}\n\nGive me a plan from this",
    ],
)
def test_a_plan_the_ask_itself_names_is_still_a_plan(turn: str) -> None:
    assert explicit_planner_style_requested(turn) is True
