"""Typed semantic fact identity and ownership for the kernel.

One requested semantic fact may have ONE committed semantic owner.
Identity comes from typed obligation structure, not from generated text.

The rejected R19 attempt used descriptor token containment and fixed-vocabulary
lists to decide ownership. This module replaces those text-based heuristics with
typed structural ownership:

- ``SemanticFactId`` identifies a requested semantic fact by (obligation_id, kind).
  Two facts are the same only when they share the same typed obligation structure.
- ``OwnershipModel`` declares whether an obligation OWNS its fact or COORDINATES
  its children's owned facts.
- ``determine_ownership`` and ``coordinator_children`` use typed obligation KINDS
  (``compose`` vs everything else) rather than comparing description text.

Why kind-based coordination works:

  A ``compose`` lane obligation that has non-compose siblings (``knowledge``,
  ``web_lookup``, ``arithmetic``, ``machine``, etc.) is structurally a parent:
  it composes the siblings' realizations into a coherent answer. No text
  comparison is needed — the lane classification itself establishes the
  relationship.

  A solo obligation (or a compose-only set) OWNS its fact because there are no
  children to coordinate.

  This is the SMALLEST typed identity sufficient for the current kernel path.
  It does not solve every semantic-identity problem (that is not its brief);
  it replaces three text-based mechanisms that were independently rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SemanticFactId:
    """Typed identity for one requested semantic fact.

    Identity comes from obligation structure, not from generated text.
    Two facts with the same (obligation_id, kind) refer to the same
    semantic obligation.
    """

    obligation_id: str
    kind: str

    def __str__(self) -> str:
        return f"{self.obligation_id}:{self.kind}"


class OwnershipModel(str, Enum):
    """How an obligation relates to the semantic facts it realizes."""

    OWNS = "owns"
    """The obligation is the sole semantic owner of its fact.
    It may produce a claim that ships as the definitive answer."""

    COORDINATES = "coordinates"
    """The obligation composes its children's realizations.
    It may NOT produce a claim that competes with children's owned facts.
    It settles by citing what it composed — never as a competing author,
    never as a dishonest "unanswerable"."""


def determine_ownership(obligations: list) -> dict[str, OwnershipModel]:
    """Determine each obligation's ownership model from typed structure.

    A ``compose`` obligation that has non-compose siblings is a COORDINATOR:
    it organizes those siblings' realizations into the answer without
    independently authoring overlapping content.

    A solo compose or a compose-only set (no non-compose siblings) means
    each obligation OWNS its own fact.

    All non-compose obligations always OWNS their own fact — this is I11
    (every requested fact has exactly one canonical owner).
    """
    if not obligations:
        return {}

    models: dict[str, OwnershipModel] = {}
    non_compose_ids = [ob.id for ob in obligations if ob.kind != "compose"]

    has_non_compose = bool(non_compose_ids)

    for ob in obligations:
        if ob.kind == "compose" and has_non_compose:
            models[ob.id] = OwnershipModel.COORDINATES
        else:
            models[ob.id] = OwnershipModel.OWNS

    return models


def coordinator_children(obligations: list) -> dict[str, list[str]]:
    """Return ``coordinator_id -> [child_id, ...]`` for typed ownership.

    A compose obligation's children are the non-compose obligations
    in the same turn, identified by typed kind — never by comparing
    obligation descriptions.

    If multiple compose obligations exist, each one coordinates all
    non-compose siblings (the compose obligations share the coordination).
    """
    result: dict[str, list[str]] = {}
    compose_ids = [ob.id for ob in obligations if ob.kind == "compose"]
    non_compose_ids = [ob.id for ob in obligations if ob.kind != "compose"]

    if compose_ids and non_compose_ids:
        for cid in compose_ids:
            result[cid] = list(non_compose_ids)

    return result


def collect_coordinated_set(
    coordinator_children_map: dict[str, list[str]],
) -> set[str]:
    """All obligation ids whose semantics are shipped through coordination.

    This includes both coordinators and their children — every obligation
    that is covered by the typed ownership structure is a member of the
    coordinated set and will never be backstopped with UNKNOWN.
    """
    covered: set[str] = set()
    for cid, children in coordinator_children_map.items():
        covered.add(cid)
        covered.update(children)
    return covered


def is_coordinator(ob_id: str, obligations: list) -> bool:
    """Whether *ob_id* is a coordinator (compose with non-compose siblings)."""
    ob = next((o for o in obligations if o.id == ob_id), None)
    if ob is None or ob.kind != "compose":
        return False
    return any(o.id != ob_id and o.kind != "compose" for o in obligations)


__all__ = [
    "OwnershipModel",
    "SemanticFactId",
    "collect_coordinated_set",
    "coordinator_children",
    "determine_ownership",
    "is_coordinator",
]
