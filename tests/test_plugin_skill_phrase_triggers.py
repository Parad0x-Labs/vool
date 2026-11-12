"""A plugin skill reaches the prompt only on strong overlap with the user's own words.

Measured 2026-10-06 on a gardening chat: "Summarise the whole plan", "What tools do I need" and
"would work in that layout" each pulled the 5,000-character database skill into the turn, through
the word "plan" from its "explain plan" trigger and single description words.
"""

from __future__ import annotations

from pathlib import Path

from core.plugin_skills import parse_skill, rank_skills

DATABASE_SKILL = Path("plugins/vool-database/skills/vool-database/SKILL.md")


def _database_skill():
    skill = parse_skill(DATABASE_SKILL, plugin_id="vool-database")
    assert skill is not None
    return (skill,)


def test_gardening_turns_do_not_pull_the_database_skill():
    skills = _database_skill()
    for text in (
        "Summarise the whole plan in five bullet points.",
        "What tools do I actually need to buy to start?",
        "What companion planting pairs would work in that layout?",
    ):
        assert rank_skills(skills, text) == (), text


def test_trigger_phrases_and_real_database_requests_still_match():
    skills = _database_skill()
    for text in (
        "show me the explain plan for this query",
        "inspect the orders database schema and answer a data question",
        "can you migrate the schema to add a column",
        "query the data in my sqlite file",
    ):
        ranked = rank_skills(skills, text)
        assert ranked and ranked[0].name == "vool-database", text
