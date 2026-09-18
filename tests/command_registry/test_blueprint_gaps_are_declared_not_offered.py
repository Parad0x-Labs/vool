"""C22's eight declared-but-unimplemented commands must stay honest on every surface.

Declaring a promised capability that does not exist is better than leaving it invisible
— an operator reading the command centre can see that VOOL knows it owes them a
snapshot-first repair flow, and what it is waiting on. But a declaration is only better
if every surface that *lists* it says so. It was not: the specs carried no availability
probe, `_evaluate_availability` returns `(True, None)` for a None probe, and the Cmd+K
palette badges a row only when `!command.available` — so eight capabilities that cannot
run rendered as ordinary, selectable, unmarked entries. Invoking one was always honest.
The listing, which is what a person reads *before* they choose, was not.

Four laws, each pinned here, because before this file a repo-wide grep for
`blueprint_gaps` returned two hits and both were the implementation.
"""
from __future__ import annotations

import pytest

from core.command_registry import execute_command, registry
from core.command_registry.groups.blueprint_gaps import UNIMPLEMENTED

DECLARED = tuple(row[0] for row in UNIMPLEMENTED)


def test_the_eight_are_actually_declared() -> None:
    reg = registry()
    missing = [cid for cid in DECLARED if reg.lookup(cid) is None]
    assert not missing, f"declared in the table but absent from the registry: {missing}"
    assert len(DECLARED) == 8


# ── LAW A — never advertised to a model as an executable capability ──────────


def test_none_of_them_becomes_a_tool_a_model_can_be_offered() -> None:
    from core.command_registry.model_tools import project_model_tools, tool_intent_for

    offered = {str(getattr(c, "intent", "") or "") for c in project_model_tools()}
    leaked = [cid for cid in DECLARED if tool_intent_for(cid) in offered]
    assert not leaked, f"unimplemented commands offered to models as tools: {leaked}"


def test_none_of_them_is_marked_model_offerable() -> None:
    reg = registry()
    offerable = [cid for cid in DECLARED if getattr(reg.lookup(cid), "model_offerable", False)]
    assert not offerable, offerable


@pytest.mark.parametrize("projection", ["palette", "commands"])
def test_every_listing_says_unavailable_with_a_reason(projection: str) -> None:
    """The listing, not just the invocation, has to carry the truth."""
    from core.command_registry.projections import commands_json, palette_data

    reg = registry()
    rows = palette_data(reg) if projection == "palette" else commands_json(reg, live_availability=True)
    rows = rows if isinstance(rows, list) else (rows.get("commands") or rows.get("rows") or [])
    seen = {r["command_id"]: r for r in rows if r.get("command_id") in DECLARED}
    assert len(seen) == len(DECLARED), f"{projection} lists only {sorted(seen)}"
    for cid, row in sorted(seen.items()):
        assert row.get("available") is False, f"{projection}: {cid} is listed as available"
        assert row.get("unavailable_reason"), f"{projection}: {cid} gives no reason"


# ── LAW C — each names the dependency it is waiting on ───────────────────────


@pytest.mark.parametrize("row", UNIMPLEMENTED, ids=[r[0] for r in UNIMPLEMENTED])
def test_invoking_one_refuses_and_names_what_it_waits_on(row: tuple) -> None:
    """The refusal must carry THIS command's dependency, not a shared placeholder.

    The availability gate refuses before the handler runs, so the reason it renders is
    the only text most callers will ever see. A generic "not implemented" there would
    satisfy the listing and quietly drop the one thing that makes these declarations
    worth having.
    """
    command_id, _description, missing, waiting_on = row
    env = execute_command(command_id, {})
    assert env.ok is False
    assert env.fault is not None and env.fault.code == "unavailable"
    reason = str(dict(env.fault.detail or {}).get("reason") or "")
    assert missing in reason, f"{command_id} does not say what is missing: {reason!r}"
    assert waiting_on in reason, f"{command_id} does not say what it waits on: {reason!r}"


@pytest.mark.parametrize("command_id", DECLARED)
def test_the_handler_behind_the_gate_still_names_the_specification(command_id: str) -> None:
    """Bypass the availability gate and check the handler itself is not a bare stub."""
    from core.command_registry.groups import blueprint_gaps

    handler = getattr(blueprint_gaps, "_h_" + command_id.replace(".", "_"))
    fault = handler(None, None)
    detail = dict(fault.detail or {})
    assert detail.get("implemented") is False
    assert detail.get("reason") and detail.get("waiting_on")
    assert str(detail.get("specification", "")).endswith(".md")


def test_the_named_dependencies_are_specific_not_boilerplate() -> None:
    """A generic 'not implemented' teaches nobody what would unblock it."""
    reasons = {row[2] for row in UNIMPLEMENTED}
    waiting = {row[3] for row in UNIMPLEMENTED}
    assert len(reasons) >= 3, f"every command gives the same reason: {reasons}"
    assert len(waiting) >= 3, f"every command waits on the same thing: {waiting}"
    assert any("snapshot.py was never written" in r for r in reasons)


# ── LAW D — fail closed, zero effects ────────────────────────────────────────


def test_they_declare_no_effect_and_reserve_no_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arm every spending seam; a command that cannot act must not touch one."""
    import core.effect_budget as budget

    def _forbidden(name):
        def _fn(*_a, **_k):
            raise AssertionError(f"an unimplemented command reached {name}")

        return _fn

    for attr in ("reserve_effect_units", "consume_reservation", "release_reservation"):
        monkeypatch.setattr(budget, attr, _forbidden(f"core.effect_budget.{attr}"))

    reg = registry()
    for cid in DECLARED:
        assert reg.lookup(cid).effects == "read_only", cid
        env = execute_command(cid, {})
        assert env.ok is False
        assert not env.receipts, f"{cid} produced receipts for work it did not do"


# ── the availability probe is load-bearing ───────────────────────────────────


def test_sabotage_dropping_the_availability_probe_relists_them_as_available() -> None:
    """Without the probe the listing goes back to advertising eight phantom commands."""
    import dataclasses

    from core.command_registry.projections import palette_data
    from core.command_registry.registry import CommandRegistry

    live = registry()
    crippled = CommandRegistry()
    for group in live.groups():
        crippled.add_group(group)
    for spec in live.commands():
        crippled.add(
            dataclasses.replace(spec, availability=None)
            if spec.command_id in DECLARED
            else spec
        )
    rows = palette_data(crippled)
    rows = rows if isinstance(rows, list) else (rows.get("commands") or [])
    relisted = sorted(
        r["command_id"] for r in rows if r.get("command_id") in DECLARED and r.get("available")
    )
    assert relisted == sorted(DECLARED), (
        "sabotage no-op: the availability probe is not what makes these read as "
        f"unavailable; only {relisted} flipped back"
    )
