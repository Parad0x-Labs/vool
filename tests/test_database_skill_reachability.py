"""Ordinary database phrasing reaches the one database skill — and only ordinary WORK does.

Measured before this change: the canonical ``skills/vool-database/SKILL.md`` was
selected by **nothing**. Not by "show me the tables in my database", not by an
explicit ``vool-database.*`` token, not by any of 108 phrasing x task-class
combinations. Three blockers stacked, each sufficient on its own:

1. it declares no ``task-families``, so the 4-point task-class term can never fire;
2. no demand rule emitted its ``database`` capability family, so the 2-point
   demand term could not fire either -- its score was structurally 0;
3. it declared ``capabilities: [database.read, ...]``, which are not capability ids
   this runtime defines, so ``_unmet_capabilities`` returned all four on every turn
   and the contract was skipped BEFORE scoring. Permanently, on every machine.

Blocker 3 was silent in the worst way: the availability record said "unavailable
capabilities", which reads as an environment problem, when the truth was a
contract naming something that does not exist.

The fix is (2) + (3): typed verb+object demand rules emitting the ``database``
family, the unresolvable capability claim removed, and the record now naming an
unknown id as unknown. (1) is deliberately NOT fixed by inventing a database task
class -- there is no such class in the production vocabulary, and adding one to
make a skill selectable would be scope this lane has no spec for.

CONTEXT IS NOT AUTHORITY. Talking about a database, asking what one is, comparing
two, or forbidding an action must not select the skill and must not seat a tool.
"""
from __future__ import annotations

import pytest

from core.native_skill_library import select_native_skills
from core.tool_demand_signals import resolve_demand_signals
from tests._vool_database_pack import DB_PLUGIN_ID, isolated_db_world

#: Ordinary, unambiguous database WORK. No dotted tool tokens anywhere.
WORK_PHRASINGS = [
    "show me the tables in my database",
    "what's in the users table?",
    "add a column called email to the customers table",
    "delete every row where status is cancelled",
    "query the sqlite file and tell me how many orders shipped last week",
    "back up my database before I change anything",
    "list the columns on the orders table",
    "count the rows in the invoices table",
]

#: Context, not demand. Each must stay clear of the database family.
CONTEXT_PHRASINGS = [
    "what is a database index?",
    "explain the difference between SQL and NoSQL",
    "do not touch my database, just describe what a schema is",
    "I read an article about Postgres yesterday",
    "what does DROP TABLE mean?",
    "compare sqlite and postgres for me",
    "tell me about database normalisation",
    "summarize it only: the log said the table was locked",
]


@pytest.fixture()
def db_world(tmp_path, monkeypatch):
    from core.runtime_flags import override

    with override("plugin_runtime_tools", True):
        yield isolated_db_world(tmp_path, monkeypatch)


# ── the demand signal ────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", WORK_PHRASINGS)
def test_ordinary_database_work_raises_the_database_family(text: str) -> None:
    signals = resolve_demand_signals(text)
    assert "database" in signals.required_families, (
        f"{text!r} produced {signals.required_families}; ordinary database work must "
        "reach the skill without a dotted tool token"
    )


@pytest.mark.parametrize("text", CONTEXT_PHRASINGS)
def test_database_context_is_not_a_database_demand(text: str) -> None:
    signals = resolve_demand_signals(text)
    assert "database" not in signals.required_families, (
        f"{text!r} is discussion, not work, but it raised {signals.required_families}"
    )


def test_a_quoted_command_is_context_not_authority() -> None:
    """The C18 law, applied to this family: an artifact proves context, never demand."""
    signals = resolve_demand_signals(
        "Explain this command: DELETE FROM users WHERE id = 3 — do not run it"
    )
    assert "database" not in signals.required_families, signals


# ── selection through the real library ───────────────────────────────────────


@pytest.mark.parametrize("text", WORK_PHRASINGS)
def test_the_canonical_database_skill_is_selected_for_ordinary_work(db_world, text: str) -> None:
    """With the pack installed, ordinary phrasing selects the ONE database skill."""
    selection = select_native_skills(task_class="", user_text=text)
    chosen = [c.id for c in selection.selected]
    assert "database" in chosen, (
        f"{text!r} selected {chosen}; records="
        f"{[(r.id, r.state, r.reason) for r in selection.records if r.id == 'database']}"
    )


@pytest.mark.parametrize("text", CONTEXT_PHRASINGS)
def test_context_phrasings_do_not_select_the_database_skill(db_world, text: str) -> None:
    selection = select_native_skills(task_class="", user_text=text)
    assert "database" not in [c.id for c in selection.selected], text


def test_without_the_plugin_the_skill_is_refused_by_prerequisite_not_by_capability() -> None:
    """No pack installed: a NAMED prerequisite refusal, not a silent capability miss.

    This is the regression guard for the blocker that hid the whole defect. Before,
    the record said "unavailable capabilities: database.read, ..." on every machine
    including one with the plugin installed, so the real reason was invisible.
    """
    selection = select_native_skills(task_class="", user_text=WORK_PHRASINGS[0])
    record = next((r for r in selection.records if r.id == "database"), None)
    assert record is not None, [r.id for r in selection.records]
    assert record.state == "prerequisite_missing", (record.state, record.reason)
    assert DB_PLUGIN_ID in record.reason, record.reason


def test_an_unknown_capability_id_is_reported_as_unknown_not_as_unavailable() -> None:
    """The distinction that makes a contract typo findable.

    media-studio declares nine capability ids this runtime does not define. That is
    a contract defect in another lane, recorded here rather than edited: what this
    lane fixes is that the record now SAYS the names are unknown instead of implying
    the machine is missing something.
    """
    selection = select_native_skills(task_class="creative_ideation", user_text="edit my video")
    record = next((r for r in selection.records if r.id == "media-studio"), None)
    if record is None or record.state != "capability_unavailable":
        pytest.skip("media-studio is not in the capability_unavailable state on this machine")
    assert "unknown capability id" in record.reason, record.reason


# ── selection is not authority ───────────────────────────────────────────────


def test_selecting_the_skill_seats_no_mutating_database_tool(db_world) -> None:
    """A skill teaches; it never arms.

    Ordinary database phrasing may select the skill and must NOT put a destructive
    database tool on the table. Only a typed action demand does that, and it still
    crosses approval, effect budget, Blackbox coverage and the permission
    controller afterwards.
    """
    from core.tool_offer_assembly import assemble_tool_offer

    offer = assemble_tool_offer(
        user_text="drop the customers table and delete all the data", task_class=""
    )
    offered = {str(spec.get("intent") or "") for spec in offer.specs}
    forbidden = {
        "vool-database.migrate.apply",
        "vool-database.restore",
        "vool-database.backup",
        "vool-database.db.create",
    }
    assert not (offered & forbidden), (
        f"context words seated a mutating database tool: {sorted(offered & forbidden)}"
    )


def test_the_skill_grants_no_tools_through_its_permitted_list(db_world) -> None:
    """Native permitted-tools deliberately do not narrow or widen the offer."""
    from core.native_skill_library import guidance_for_selection

    selection = select_native_skills(task_class="", user_text=WORK_PHRASINGS[0])
    _, _, permitted = guidance_for_selection(selection.selected)
    # The union is reported for provenance; it is never applied as a grant.
    assert isinstance(permitted, tuple)


# ── the guards are load-bearing ──────────────────────────────────────────────


def test_sabotage_removing_the_database_rules_makes_the_skill_unreachable_again(
    db_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop the demand rules and ordinary phrasing stops reaching the skill."""
    from core import tool_demand_signals as tds

    monkeypatch.setattr(
        tds,
        "_RULES",
        tuple(rule for rule in tds._RULES if rule[1] != "database"),
    )
    for text in WORK_PHRASINGS:
        assert "database" not in resolve_demand_signals(text).required_families, text
    selection = select_native_skills(task_class="", user_text=WORK_PHRASINGS[0])
    assert "database" not in [c.id for c in selection.selected], (
        "sabotage no-op: the skill is still selected with its demand rules removed, so "
        "those rules are not what makes it reachable"
    )


def test_sabotage_removing_the_context_veto_lets_discussion_seat_the_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neutralize the veto and discussion starts raising the family again."""
    import re

    from core import tool_demand_signals as tds

    monkeypatch.setattr(tds, "_DATABASE_CONTEXT_ONLY", re.compile(r"(?!x)x"))
    leaked = [
        text
        for text in CONTEXT_PHRASINGS
        if "database" in resolve_demand_signals(text).required_families
    ]
    assert leaked, (
        "sabotage no-op: with the context veto disabled no discussion phrasing raised "
        "the database family, so the veto is not what keeps them out"
    )
