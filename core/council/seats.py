"""Council seats and role authority.

A council is a fixed set of seats over user-selected models. Role authority is
validated ONCE, structurally, before any model is invoked — a mis-declared
council (a voter named as final judge, an advisor expecting ballot weight)
is a construction error, never a runtime surprise.

Role semantics (closed set):

- JUDGE      adjudicates surviving disputes. Exactly one; the final judge.
- VOTER      casts ballots over R1 candidates. Never adjudicates.
- ADVISOR    supplies analysis to the judge. Non-binding, zero ballot weight.
- CHALLENGER generates typed challenges against disputed claims only.
- BUILDER    assembles the committed answer under a verbatim-fact contract.
- OBSERVER   receives nothing during the run; reads the sealed receipt after.

The judge does not vote and voters do not judge: adjudication and preference
are different authorities held by different seats, so no seat can both set
the ballot's options and pick the winner of the ballot.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from core.council.policy import EscalationPolicy


class Role(str, enum.Enum):
    JUDGE = "judge"
    VOTER = "voter"
    ADVISOR = "advisor"
    CHALLENGER = "challenger"
    BUILDER = "builder"
    OBSERVER = "observer"


class Tier(str, enum.Enum):
    LOCAL = "local"
    FREE_CLOUD = "free_cloud"
    PAID = "paid"


#: Roles whose seats answer the blind first pass. The JUDGE deliberately does
#: NOT answer R1: an adjudicator that competed in the first pass would both
#: bias its own adjudication toward its own text and burn a paid call on every
#: run regardless of whether any dispute survives. The judge meets the
#: candidates only as anonymized dispute records.
ANSWERING_ROLES = frozenset(
    {Role.VOTER, Role.ADVISOR, Role.CHALLENGER, Role.BUILDER}
)


class CouncilSpecError(ValueError):
    """The council declaration violates role authority."""


@dataclass(frozen=True)
class SeatSpec:
    """One model in one role at one cost tier."""

    seat_id: str
    display_name: str
    role: Role
    tier: Tier

    def __post_init__(self) -> None:
        if not str(self.seat_id or "").strip():
            raise CouncilSpecError("seat_id must be non-empty")
        if not str(self.display_name or "").strip():
            raise CouncilSpecError(f"display_name must be non-empty ({self.seat_id})")


@dataclass(frozen=True)
class CouncilSpec:
    """A validated council declaration."""

    name: str
    seats: tuple[SeatSpec, ...]
    final_judge_seat_id: str
    policy: EscalationPolicy = field(default_factory=EscalationPolicy)
    max_challenge_rounds: int = 1

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise CouncilSpecError("council name must be non-empty")
        if not self.seats:
            raise CouncilSpecError("a council needs at least one seat")
        ids = [s.seat_id for s in self.seats]
        if len(ids) != len(set(ids)):
            raise CouncilSpecError("duplicate seat_id")

        judges = [s for s in self.seats if s.role is Role.JUDGE]
        if len(judges) != 1:
            raise CouncilSpecError(f"exactly one JUDGE required, found {len(judges)}")
        if self.final_judge_seat_id != judges[0].seat_id:
            # A voter (or advisor, or challenger) can never be promoted to final
            # judge by naming it here: the pointer must name the JUDGE-role seat,
            # and there is exactly one of those.
            raise CouncilSpecError(
                f"final_judge_seat_id {self.final_judge_seat_id!r} is not the JUDGE seat "
                f"({judges[0].seat_id!r}); voters cannot become judges"
            )

        answering = [s for s in self.seats if s.role in ANSWERING_ROLES]
        if len(answering) < 2:
            raise CouncilSpecError(
                "a council needs at least two answering seats; one model cannot "
                "disagree with itself into a dispute worth adjudicating"
            )
        if not 1 <= int(self.max_challenge_rounds) <= 3:
            raise CouncilSpecError("max_challenge_rounds must be within 1..3")

    # -- convenience lookups -------------------------------------------------

    def seat(self, seat_id: str) -> SeatSpec:
        for s in self.seats:
            if s.seat_id == seat_id:
                return s
        raise KeyError(seat_id)

    @property
    def judge(self) -> SeatSpec:
        return self.seat(self.final_judge_seat_id)

    def by_role(self, role: Role) -> tuple[SeatSpec, ...]:
        return tuple(s for s in self.seats if s.role is role)

    def answering_seats(self) -> tuple[SeatSpec, ...]:
        return tuple(s for s in self.seats if s.role in ANSWERING_ROLES)


__all__ = [
    "ANSWERING_ROLES",
    "CouncilSpec",
    "CouncilSpecError",
    "Role",
    "SeatSpec",
    "Tier",
]
