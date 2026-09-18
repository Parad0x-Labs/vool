"""A stand-in for the bounded semantic proposer, for tests that need proven open-domain roles.

Open-ended natural-language roles are proven by a bounded model (`core.conductor.semantic_proof`,
path 2). A deterministic test cannot call one, so this emits the same JSON wire format a model
would, through the same `parse_proposal` -> `validate_frame` path production uses. Nothing here
short-circuits: a frame described with a surface the message does not contain is located, fails to
be found, and abstains -- exactly as a model's paraphrase would.

What this is NOT: an answer. It carries no operation parameters, no entity resolution, no
capability verdict and no outcome. It says "this span is a location, this one is the second member
of the same list, this frame is negated" and stops -- which is the entire authority the bounded
path has in production too.
"""
from __future__ import annotations

import json
from collections.abc import Sequence


def role(
    name: str, text: str, group: str = "", ordinal: int = 0
) -> dict[str, object]:
    """One role filler, pointed at by the substring the message contains."""
    return {"role": name, "text": text, "group": group or f"{name}:g0", "ordinal": ordinal}


def coordinated(name: str, *texts: str, group: str = "") -> list[dict[str, object]]:
    """A coordinated list of fillers for one role, numbered explicitly.

    The separator between the members is not mentioned here and is not mentioned anywhere in
    production either -- membership is asserted, which is why "Oslo and Tromso", "Oslo btw Tromso"
    and "Oslo/Tromso" cannot differ.
    """
    tag = group or f"{name}:g0"
    return [role(name, text, tag, index) for index, text in enumerate(texts)]


def frame(
    family: str,
    *,
    scope: str,
    predicate: str,
    roles: Sequence[dict[str, object]] = (),
    polarity: str = "affirmed",
    frame_id: str = "",
) -> dict[str, object]:
    return {
        "frame_id": frame_id or f"t:{family}:{scope[:12]}",
        "family": family,
        "scope": scope,
        "predicate": predicate,
        "polarity": polarity,
        "roles": list(roles),
    }


def reply(*frames: dict[str, object]) -> str:
    return json.dumps({"frames": list(frames)})


def proposer(*frames: dict[str, object]):
    """A `(system_prompt, user_text) -> str` callable, the shape the runtime injects."""

    def _propose(_system: str, _user: str) -> str:
        return reply(*frames)

    return _propose


def raw_proposer(payload: str):
    """A proposer returning `payload` verbatim -- for malformed and hostile replies."""

    def _propose(_system: str, _user: str) -> str:
        return payload

    return _propose


def failing_proposer(exc: BaseException | None = None):
    """A proposer that raises. Its frames must abstain, never become a guess."""

    def _propose(_system: str, _user: str) -> str:
        raise exc or RuntimeError("proposer unavailable")

    return _propose


__all__ = [
    "coordinated",
    "failing_proposer",
    "frame",
    "proposer",
    "raw_proposer",
    "reply",
    "role",
]
