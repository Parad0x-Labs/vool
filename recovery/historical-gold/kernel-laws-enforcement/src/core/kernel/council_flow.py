"""Council × information-flow privacy — role authority never widens information
authority.

The council wants many models deliberating one task. The naive shape leaks by design:
every seat sees the shared context, so the judge — often the most privileged
PROCEDURAL role — becomes the most privileged READER. This module composes two
existing experimental systems so that cannot happen:

- **Seats are Law-3 forks.** A seat's procedural role (CHALLENGER, VOTER, ADVISOR,
  JUDGE) is a LABEL WITH ZERO CAPABILITY SEMANTICS. Everything a seat may read or
  publish is exactly its ``compartment.*`` / ``declassify.*`` token set. A judge with
  no ``compartment.salary`` token mechanically receives bytes that never contained
  the salary — JUDGE != ROOT ACCESS.
- **Capsules assemble through the information-flow boundary** (`core.kernel.flow`).
  ``build_capsule`` accepts only plain strings, granted compartment views, and
  PublicArtifacts; any seat's Private-derived material — its own challenges included
  — raises mechanically rather than crossing. Cross-seat taint therefore survives the
  council boundary by construction: seat A's private-derived challenge cannot be
  placed into seat B's capsule unless A typed-and-disclosed it.
- **Evidence visibility is grant arithmetic** (`EvidenceVault`). The vault
  distinguishes EVIDENCE EXISTS from THIS SEAT MAY SEE EVIDENCE: registration keeps
  everything, capsule building filters by the seat's grants, and a verdict citing an
  out-of-scope evidence id is refused at mint time — a judge cannot "resolve" a
  dispute using evidence it was never authorized to consume.
- **Anonymization is not declassification.** Relabeling attribution ("Ox said…" ->
  "Candidate A said…") is just another derive() on a Private value: the label rides,
  the bytes still refuse to cross. Identity attribution and information authority are
  different axes.
- **One final-byte owner, privacy-aware.** The final renderer serves the USER, who is
  authorized for every compartment of their own turn. But exporting the transcript to
  a destination without those grants filters to public-provenance claims only — final
  rendering never becomes a bypass.

No new semantic authority exists in this file: grants/denials are Law 3 receipts,
crossing rules are the flow module's, grounding is Law 2. This is composition only.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from core.kernel.compartments import NeedToKnowLane
from core.kernel.evidence_types import TypedClaim
from core.kernel.flow import PublicArtifact, Private, cloud_view

__all__ = [
    "CouncilSeat",
    "EvidenceNotInScope",
    "EvidenceVault",
    "Verdict",
    "build_capsule",
]


@dataclass(frozen=True)
class CouncilSeat:
    """One deliberation seat: a procedural ROLE plus an independent INFORMATION grant
    set. The role is carried for the protocol's benefit only — it appears nowhere in
    any access decision."""

    lane: NeedToKnowLane
    role: str

    @property
    def seat_id(self) -> str:
        return self.lane.fork.fork_id

    def holds(self, token: str) -> bool:
        return self.lane.fork.caps.allows(token)


def build_capsule(
    seat: CouncilSeat,
    public_frame: str,
    compartments: Mapping[str, object],
    *materials: object,
) -> str:
    """The EXACT outbound bytes for this seat: its granted view plus explicitly
    passed materials — each of which must already be public-typed. A Private value
    from ANY seat (including this one) refuses here."""
    view, _included = __import__(
        "core.kernel.compartments", fromlist=["minimized_view"]
    ).minimized_view(seat.lane, public_frame, compartments)
    return cloud_view(view, *materials)


class EvidenceNotInScope(RuntimeError):
    """A seat cited evidence it was never authorized to consume."""


@dataclass(frozen=True)
class _Entry:
    content: object                      # str | PublicArtifact | Private
    compartments: frozenset[str]         # which sources' grants its consumption needs


class EvidenceVault:
    """EVIDENCE EXISTS != THIS SEAT MAY SEE EVIDENCE.

    Registration accepts anything (the vault is the audit record). Visibility is
    computed per seat from its grants; Private-derived entries are visible to NO seat
    through this vault — they cross only as disclosed PublicArtifacts.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def register(self, evidence_id: str, content: object, *,
                 compartments: frozenset[str] = frozenset()) -> None:
        if isinstance(content, Private):
            # A Private value is registered for AUDIT only: no seat sees it here,
            # whatever its grants — disclosure goes through the typed gate first.
            self._entries[evidence_id] = _Entry(content=content, compartments=frozenset())
            return
        self._entries[evidence_id] = _Entry(content=content, compartments=frozenset(compartments))

    def visible_to(self, seat: CouncilSeat) -> tuple[str, ...]:
        out: list[str] = []
        for eid, entry in self._entries.items():
            if isinstance(entry.content, Private):
                continue
            if all(seat.holds(f"compartment.{c}") for c in entry.compartments):
                out.append(eid)
        return tuple(out)

    def material_for_capsule(self, seat: CouncilSeat, evidence_id: str) -> object:
        entry = self._entries.get(evidence_id)
        if entry is None:
            raise KeyError(evidence_id)
        if isinstance(entry.content, Private):
            raise EvidenceNotInScope(
                f"evidence {evidence_id!r} is Private-derived — disclose it through "
                "the typed gate before citing it across seats"
            )
        if not all(seat.holds(f"compartment.{c}") for c in entry.compartments):
            raise EvidenceNotInScope(
                f"seat {seat.seat_id} ({seat.role}) lacks "
                f"{[f'compartment.{c}' for c in sorted(entry.compartments - self._grants_of(seat))]}"
                f" needed for evidence {evidence_id!r}"
            )
        return entry.content

    @staticmethod
    def _grants_of(seat: CouncilSeat) -> frozenset[str]:
        return frozenset(t.split(".", 1)[1] for t in seat.lane.fork.caps.tokens
                         if t.startswith("compartment."))


@dataclass(frozen=True)
class Verdict:
    """A seat's adjudication. Minted ONLY with every citation proven in-scope."""

    seat_id: str
    role: str
    choice: str
    cites: tuple[str, ...]

    @classmethod
    def mint(cls, *, seat: CouncilSeat, vault: EvidenceVault, choice: str,
             cites: tuple[str, ...]) -> "Verdict":
        visible = set(vault.visible_to(seat))
        out_of_scope = [cid for cid in cites if cid not in visible]
        if out_of_scope:
            raise EvidenceNotInScope(
                f"seat {seat.seat_id} ({seat.role}) may not cite {out_of_scope} — "
                "role authority does not widen information authority"
            )
        return cls(seat_id=seat.seat_id, role=seat.role, choice=choice, cites=cites)


def export_public_claims(claims: list[TypedClaim],
                         private_refs: set[str]) -> list[TypedClaim]:
    """The final-byte owner renders for the USER, who is authorized for their own
    turn's compartments. Exporting the answer to a destination WITHOUT those grants
    keeps only claims whose provenance is fully public — rendering never becomes a
    privacy bypass."""
    return [c for c in claims if not (set(c.cited()) & private_refs)]
