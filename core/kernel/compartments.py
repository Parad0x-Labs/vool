"""Compartment-gated lanes — need-to-know execution over a user's own words.

Every ordinary agent runtime has the same privacy shape: the WHOLE prompt goes to
every model in the loop. A user asking a cloud model a public question while their
message also carries a salary, a balance, or an API key is shipping those bytes to a
third party by default. Nothing in the prompt says so; nothing can prove it didn't.

VOOL's kernel already owns the parts needed to make data minimization STRUCTURAL
rather than promised:

- **Law 3 capabilities** — exact-token grants, subset-enforced child forks, auditable
  denial receipts. Compartments ARE capabilities: ``compartment.salary`` is a token,
  and a lane that lacks it cannot receive those bytes, full stop.
- **The capsule credential scan** (`core.model_handoff_capsule.SECRET_SCAN`) — the one
  canonical definition of "credential-shaped", reused here so two regexes can never
  drift apart.
- **Law 2 evidence typing** — a rendered claim must ground its numbers in the receipt
  it cites. A claim attributing a private number to the CLOUD lane's receipt is a
  type error, because the cloud lane's tape never held those digits. Provenance
  enforcement falls out of an existing law for free.

The rules, all fail-closed:

1. Input text may declare compartments: ``[private:salary] … [/private]``. Markers are
   parsed deterministically; unclosed, nested, or duplicate compartments refuse.
2. Each lane runs in a fork holding explicit compartment grants (plus its tool caps).
   The outbound view for a lane is built ONLY from its granted compartments plus the
   public frame; denied compartment bytes are structurally absent.
3. Before any outbound call, :func:`leak_scan` verifies no DENIED compartment's text
   appears anywhere in the view — including smuggled into the frame — and no
   credential pattern appears even in GRANTED content. A hit refuses the call.
4. Every rendered claim cites its lane; Law 2 makes cross-lane attribution of private
   numbers impossible to render.

This module adds NO new semantic authority: grants/denials are Law 3 receipts, the
secret scan is THE capsule scan, grounding is THE evidence law. It contributes only
the deterministic compartment grammar and the pre-call leak gate.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from core.kernel.capabilities import CapabilitySet, ForkContext
from core.model_handoff_capsule import SECRET_SCAN

__all__ = [
    "Compartment",
    "CompartmentLeakError",
    "MalformedCompartmentError",
    "NeedToKnowLane",
    "leak_scan",
    "minimized_view",
    "parse_compartments",
]

_MARKER_OPEN = re.compile(r"\[private:([a-z][a-z0-9_]*)\]")


class MalformedCompartmentError(ValueError):
    """The compartment markup itself is broken — refuse rather than guess."""


class CompartmentLeakError(RuntimeError):
    """Denied bytes reached an outbound view, or credentials reached any view."""


@dataclass(frozen=True)
class Compartment:
    id: str
    text: str


def parse_compartments(text: str) -> tuple[str, dict[str, Compartment]]:
    """Split marked text into (public_frame, {id: Compartment}).

    Deterministic grammar: ``[private:id] body [/private]`` with lowercase ids.
    Unclosed markers, nested markers, duplicate ids, and empty bodies refuse —
    a parser that guesses would decide, byte by byte, what leaves the machine.
    """
    body = str(text or "")
    compartments: dict[str, Compartment] = {}
    public_parts: list[str] = []
    cursor = 0
    while True:
        match = _MARKER_OPEN.search(body, cursor)
        if match is None:
            tail = body[cursor:]
            if "[/private]" in tail:
                raise MalformedCompartmentError(
                    f"a [/private] close appears with no matching open at offset {cursor}"
                )
            public_parts.append(tail)
            break
        if "[/private]" in body[cursor:match.start()]:
            raise MalformedCompartmentError(
                f"a [/private] close appears before its open at offset {match.start()}"
            )
        public_parts.append(body[cursor:match.start()])
        comp_id = match.group(1)
        if comp_id in compartments:
            raise MalformedCompartmentError(f"duplicate compartment {comp_id!r}")
        close = body.find("[/private]", match.end())
        if close == -1:
            raise MalformedCompartmentError(f"compartment {comp_id!r} is never closed")
        inner = body[match.end():close]
        if _MARKER_OPEN.search(inner):
            raise MalformedCompartmentError(f"nested marker inside compartment {comp_id!r}")
        if not inner.strip():
            raise MalformedCompartmentError(f"compartment {comp_id!r} is empty")
        compartments[comp_id] = Compartment(id=comp_id, text=inner.strip())
        cursor = close + len("[/private]")
    return "".join(public_parts).strip(), compartments


@dataclass(frozen=True)
class NeedToKnowLane:
    """One lane's need-to-know contract: a fork whose grants name its compartments."""

    fork: ForkContext
    lane_id: str

    @classmethod
    def create(cls, *, lane_id: str, parent: ForkContext | None = None,
               compartments: tuple[str, ...] = (), tool_caps: tuple[str, ...] = ()) -> NeedToKnowLane:
        tokens = [f"compartment.{c}" for c in compartments] + list(tool_caps)
        caps = CapabilitySet(tokens)
        if parent is not None:
            fork = parent.spawn_child(f"lane.{lane_id}", caps)  # subset-enforced by Law 3
        else:
            fork = ForkContext(fork_id=f"lane.{lane_id}", caps=caps, parent_id=None)
        return cls(fork=fork, lane_id=lane_id)


def minimized_view(
    lane: NeedToKnowLane,
    public_frame: str,
    compartments: Mapping[str, Compartment],
) -> tuple[str, tuple[str, ...]]:
    """Build the ONLY bytes this lane may see, with provenance of what was included.

    Denied compartments contribute nothing — not redacted placeholders, nothing —
    because even a placeholder confirms structure. Returns (view, included_ids).
    """
    included: list[str] = []
    parts: list[str] = [public_frame.strip()] if public_frame.strip() else []
    for comp_id, comp in sorted(compartments.items()):
        token = f"compartment.{comp_id}"
        if lane.fork.caps.allows(token):
            parts.append(comp.text)
            included.append(comp_id)
        # Denial is silent here BY DESIGN at the byte level — the view must not hint
        # at what exists — but the grant decision itself is auditable via the fork.
    view = "\n".join(parts)
    leak_scan(view, compartments, included=tuple(included))
    return view, tuple(included)


def leak_scan(
    view: str,
    compartments: Mapping[str, Compartment],
    *,
    included: tuple[str, ...],
) -> None:
    """Fail-closed pre-flight: no denied compartment's text, no credentials anywhere.

    Credential-shaped content is refused EVEN when its compartment was granted —
    a lane authorized to see a field does not earn the right to transmit a secret
    to a third party.
    """
    for comp_id, comp in compartments.items():
        if comp_id in included:
            continue
        probe = comp.text.strip()
        if probe and probe in view:
            raise CompartmentLeakError(
                f"outbound view carries text from NON-granted compartment "
                f"{comp_id!r} — refused before any model call"
            )
        # Wholesale copying is not the only channel: a PARTIAL copy of a denied
        # number leaks too. Quantities come from THE canonical lexical-span
        # authority, so '4,200 EUR/month' forbids a bare '4,200' just as hard.
        from core.kernel.lexical_spans import quantity_values
        view_quantities = quantity_values(view)
        for quantity in quantity_values(comp.text):
            if (len(quantity.replace(",", "").split(".")[0]) >= 3
                    and quantity in view_quantities):
                raise CompartmentLeakError(
                    f"outbound view carries the quantity {quantity!r} from "
                    f"NON-granted compartment {comp_id!r} — refused"
                )
    hit = SECRET_SCAN.search(view)
    if hit is not None:
        raise CompartmentLeakError(
            "outbound view matches the canonical credential pattern — refused even "
            "though its compartment was granted; secrets do not travel"
        )
