"""C22's two named outcomes, declared and typed-unavailable rather than implied.

The Operator Command Centre blueprint promises two things this build does not
have:

* **§11.2** a six-stage repair flow — inspect → preview → snapshot → repair →
  verify → rollback — where *a repair run refuses to start without a snapshot
  receipt*;
* **§12** validated typed extension events, replacing arbitrary hook directories.

Neither exists. Verified by negative grep: no hits repo-wide for
``six.step|repair_flow|snapshot_required|requires_snapshot`` or for
``ExtensionEvent|extension_event|hooks_dir|hook_dir``.

Leaving them undeclared is the failure mode this file closes. An operator reading
the command centre sees the registry as the catalogue of what the product can do;
a promised capability that appears nowhere reads as "not offered yet" to a careful
reader and as "somewhere else" to everyone else. Declared here, `vool commands`
lists them, `--json` carries their contracts, and invoking one returns a typed
``unavailable`` naming the specification it is waiting on.

This is NOT a second command catalogue and NOT a stub that pretends to work. The
handlers do nothing except refuse, by name, with the blueprint section that owns
the missing behaviour.

There is also no hook-directory mechanism to replace: ``plugins/`` holds one
entry and nothing discovers executables from a directory. So §12's risk is
currently theoretical, which the refusal says rather than implying the guard is
in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.command_registry.registry import CommandRegistry
from core.command_registry.spec import (
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    OpenRead,
)


@dataclass(frozen=True)
class NoInput:
    """These commands accept nothing: there is no behaviour to parameterise."""


#: command_id -> (blueprint section, what is missing, what it is waiting on)
UNIMPLEMENTED: tuple[tuple[str, str, str, str], ...] = (
    (
        "repair.inspect",
        "Blueprint §11.2 stage 1 — read-only fault census for a repair run",
        "the six-stage repair flow does not exist in this build",
        "an authoritative acceptance definition for the repair flow's stages and receipts",
    ),
    (
        "repair.preview",
        "Blueprint §11.2 stage 2 — the proposed repair plan, execution-free",
        "the six-stage repair flow does not exist in this build",
        "an authoritative acceptance definition for the repair flow's stages and receipts",
    ),
    (
        "repair.snapshot",
        "Blueprint §11.2 stage 3 — the mandatory snapshot a repair run cannot start without",
        "no snapshot gate exists; core/repair/snapshot.py was never written",
        "the generalisation of the updater's SNAPSHOT_RELPATHS + journal pattern",
    ),
    (
        "repair.run",
        "Blueprint §11.2 stage 4 — typed repair steps, each emitting an effect receipt",
        "the six-stage repair flow does not exist in this build",
        "stage 3: a run must refuse to begin without a snapshot receipt",
    ),
    (
        "repair.verify",
        "Blueprint §11.2 stage 5 — re-run the inspect checks plus health and chain verify",
        "the six-stage repair flow does not exist in this build",
        "stages 1 and 4",
    ),
    (
        "repair.rollback",
        "Blueprint §11.2 stage 6 — restore the snapshot taken at stage 3",
        "no repair snapshot exists to restore; core/blackbox/rollback.py is a different, narrower flow",
        "stage 3",
    ),
    (
        "events.subscribe",
        "Blueprint §12 — register an in-process subscriber for a typed extension event",
        "typed extension events do not exist in this build",
        "the event_id / schema / visibility vocabulary and its P0 emission points",
    ),
    (
        "events.list",
        "Blueprint §12 — the declared extension events and their schemas",
        "typed extension events do not exist in this build",
        "the event_id / schema / visibility vocabulary",
    ),
)


def _never_available(missing: str, waiting_on: str):
    """Build this command's availability probe, carrying ITS dependency.

    Declaring these commands without a probe was a real defect, not a cosmetic one.
    ``_evaluate_availability`` returns ``(True, None)`` when ``spec.availability`` is
    None, so all eight rendered as ``available: true, unavailable_reason: null`` on
    ``/api/commands`` and ``/api/commands/palette`` -- and the Cmd+K palette badges a row
    only when ``!command.available``, so an operator saw eight ordinary, selectable,
    unmarked entries for capabilities that do not exist. Invoking one was always honest;
    the LISTING was not, and a listing is what a person reads *before* they choose.

    Per-command rather than shared, because the availability gate refuses before the
    handler runs: a single generic reason would satisfy the listing and silently drop the
    one thing that makes these declarations useful — which dependency each is waiting on.
    """

    def _probe(_context: dict) -> tuple[bool, str]:
        return False, f"{missing} — waiting on {waiting_on}"

    return _probe


def _unavailable(command_id: str, missing: str, waiting_on: str):
    def _run(_inp: Any, _ctx: Any) -> Any:
        return HandlerFault(
            fault_code="unavailable",
            summary=f"{command_id} is declared but not implemented: {missing}",
            detail={
                "reason": missing,
                "waiting_on": waiting_on,
                "implemented": False,
                # Named so an operator or an audit can find the requirement rather
                # than rediscovering that it is absent.
                "specification": "research/VOOL_OPERATOR_COMMAND_CENTRE_BLUEPRINT_2026-09-02.md",
            },
        )

    return _run


def register(reg: CommandRegistry) -> None:
    reg.add_group(
        GroupSpec(
            group_id="repair",
            description="snapshot-first repair flow (declared, not implemented — Blueprint §11.2)",
        )
    )
    reg.add_group(
        GroupSpec(
            group_id="events",
            description="typed extension events (declared, not implemented — Blueprint §12)",
        )
    )
    for command_id, description, missing, waiting_on in UNIMPLEMENTED:
        fn_name = "_h_" + command_id.replace(".", "_")
        globals()[fn_name] = _unavailable(command_id, missing, waiting_on)
        probe_name = "_p_" + command_id.replace(".", "_")
        globals()[probe_name] = _never_available(missing, waiting_on)
        reg.add(
            CommandSpec(
                command_id=command_id,
                group=command_id.split(".", 1)[0],
                description=f"{description} — NOT IMPLEMENTED",
                input_schema=NoInput,
                # read_only: they perform nothing. Declaring a mutating effect for a
                # command that cannot mutate would reserve budget for work that never
                # happens and imply a capability twice over.
                effects="read_only",
                permission=OpenRead(),
                handler=Handler(f"core.command_registry.groups.blueprint_gaps:{fn_name}"),
                lifecycle="preview",
                # Machine-readable unavailability, so every projection that enumerates
                # commands reports it without having to read prose.
                availability=Availability(
                    f"core.command_registry.groups.blueprint_gaps:{probe_name}"
                ),
                exit_codes=(0, 10),
            )
        )
