"""MILESTONE 4 — the one ordered lane registry and the kernel's claim decision.

WHAT THIS MODULE OWNS
---------------------
ONE ordered registry over the lane implementations that serve VOOL turns, and
ONE pure decision function: given the typed LaneProposal stream every serving
route now emits (M3) and the turn's canonical demand units, derive exactly one
KERNEL decision per obligation. No lane participates in the decision; lanes
propose, the kernel decides — the module is the audit side of that law.

THE REGISTRY ORDER is the precedence the runtime already executes (frontdoor
fast paths before the typed live-data lane, the conductor above the model
fallback), now written in ONE auditable place instead of being implied by the
call cascade. It decides CONTESTED claims (two lanes claiming one unit): the
earlier lane in registry order owns it; the later claim is recorded as
superseded, not discarded. Unknown lane ids (future lanes, test doubles) rank
AFTER the registry — never first.

THE DECISION VOCABULARY (M4):
* ``claimed``        — exactly one lane claimed it (or the registry resolved
                       the contest); ``owner`` names the lane.
* ``model_required`` — no deterministic lane claimed it; the turn's fallback
                       route claimed the whole set (the model path is the
                       answer of last resort).
* ``unclaimed``      — NO proposal claimed it, not even the fallback. An audit
                       anomaly: with M3's fallback seam this should not occur
                       on a served turn; the decision names it rather than
                       hiding it.

SLICING NOTE (why this is additive): the executed precedence cascade still
runs — this decision is computed AT THE SPINE from what the lanes actually
recorded, so it is the audit record of who took what. Later M4 slices move
claim *mediation* here (lanes ask the kernel before serving); the decision
function is the seam they will call, so the registry is load-bearing now (the
TurnState carries its output) rather than a parallel path.

REMOVAL CONDITION for the compatibility note: when lanes are kernel-mediated,
the cascade's implicit ordering is deleted and this registry becomes the only
order — the ``_REGISTRY`` tuple below is then edited, not duplicated.
"""
from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from core.turn_contract import LaneProposal

#: The scoped-catalog ContextVar — the pure-injection seam for tests and
#: embedders. Empty means "the production catalog is in force".
_CATALOG_SCOPE: ContextVar[tuple[LaneSpec, ...] | None] = ContextVar(
    "lane_catalog_scope", default=None
)

# =====================================================================
# R1f — the one typed lane catalog.
#
# Before this, precedence lived here (as a bare id tuple) while demand
# coverage lived in a private `_LANE_PROBES` list inside
# `core.agent_runtime.demand_ownership` — two registries a new whole-turn
# lane had to be edited into, and an omitted lane could still swallow mixed
# demand. The catalog is now the single authority: every production lane
# capable of ending an external turn declares a `LaneSpec` stating its
# precedence, its terminal eligibility, its demand-coverage CAPABILITY (a
# name resolved lazily by demand_ownership — never an import here), and its
# role. `LANE_REGISTRY` below is DERIVED from it; there is no second list.
# =====================================================================

#: Lane roles. Deterministic lanes answer requests of their own; composite
#: planners own a COMPLETE unit plan (they may finalize multi-unit turns by
#: construction); the fallback is the escalation of last resort.
ROLE_DETERMINISTIC = "deterministic"
ROLE_COMPOSITE_PLANNER = "composite_planner"
ROLE_FALLBACK = "fallback"

#: Coverage capability names that deliberately declare NO per-unit coverage:
#: "none" marks a lane limited to single-unit turns by its own admission;
#: composite planners and fallbacks own whole plans, not units.
COVERAGE_NONE = "none"
COVERAGE_COMPOSITE_PLAN = "composite_plan"
COVERAGE_FALLBACK_PLAN = "fallback_plan"

#: What the model lane PROVIDES when it actually executes one demand.
#:
#: `coverage` and `executes` answer two different questions and must not be
#: conflated. Coverage is a CLAIM declaration — "may this lane take this unit off
#: another lane?" — and the fallback declares `fallback_plan` precisely because it
#: claims nothing per-unit (a fallback that claimed every unit would make every
#: mixed turn read as covered by one lane, and no turn would ever decompose).
#: `executes` is the EXECUTION fact — "what served this demand?" — and for the
#: model lane the answer is model reasoning, not "fallback plan" and certainly not
#: "unsupported". Measured: a served interpretation was recorded
#: `capability=unsupported` beside `dispatch=SUCCEEDED` and an answer receipt, which
#: is three statements that cannot all be true.
CAPABILITY_MODEL_REASONING = "model_reasoning"

_ROLES = frozenset({ROLE_DETERMINISTIC, ROLE_COMPOSITE_PLANNER, ROLE_FALLBACK})


@dataclass(frozen=True)
class LaneSpec:
    """One lane's declaration in the catalog — typed, immutable.

    `coverage` is a capability NAME, not an implementation: this module must
    stay importable from anywhere (agent_runtime imports it eagerly), so the
    implementations resolve lazily in
    `core.agent_runtime.demand_ownership`, which raises a typed error for a
    name nobody implements — an unknown capability must never silently read
    as "covers everything".
    """

    lane_id: str
    #: Precedence on a contested claim — the ONLY ordering authority.
    precedence: int
    #: Whether this lane may END (finalize) an external turn at all.
    terminal: bool
    #: Demand-coverage capability name ("live_data", "currency", "none", ...).
    coverage: str
    #: ROLE_DETERMINISTIC | ROLE_COMPOSITE_PLANNER | ROLE_FALLBACK.
    role: str = ROLE_DETERMINISTIC
    #: The capability this lane PROVIDES when it executes one demand, when that
    #: differs from its coverage CLAIM. Empty means "the same as `coverage`",
    #: which is right for every lane whose claim and execution are the same act.
    executes: str = ""
    # Some parsers return exactly one action even when several units match.
    max_units: int | None = None

    def __post_init__(self) -> None:
        if self.max_units is not None and (type(self.max_units) is not int or self.max_units < 1):
            raise ValueError("LaneSpec.max_units must be a positive integer or None")
        if not isinstance(self.lane_id, str) or not self.lane_id.strip():
            raise ValueError("LaneSpec.lane_id must be a non-empty string")
        if not isinstance(self.precedence, int) or isinstance(self.precedence, bool):
            raise ValueError("LaneSpec.precedence must be an int")
        if not isinstance(self.terminal, bool):
            raise ValueError("LaneSpec.terminal must be a bool")
        if not isinstance(self.coverage, str) or not self.coverage.strip():
            raise ValueError(
                f"LaneSpec for {self.lane_id!r} declares no coverage capability — "
                "every terminal lane must state one (a capability name, 'none' "
                "for single-unit-limited, or a composite/fallback plan role)"
            )
        if self.role not in _ROLES:
            raise ValueError(f"LaneSpec.role must be one of {sorted(_ROLES)}")


def _production_catalog() -> tuple[LaneSpec, ...]:
    """The inventoried production lanes that can end an external turn.

    THE INVENTORY (R1f requirement 5) — every lane below is terminal today and
    declares exactly one arm of the trichotomy:

    * coverage-declaring: live_info_fast_path / live_data_typed_plan (the live
      family, one capability), currency_value_contract / currency_frontdoor
      (the currency family, one capability);
    * single-unit-limited ('none'): turn_frontdoor_deterministic — the front
      door's deterministic fast paths (workspace identity/overview, mission
      render, voolbook, evaluative chat, the closed non-currency contracts),
      each bounded by its own whole-turn admission; attempt_followup — one
      resolved antecedent, one request;
    * composite planners owning a complete unit plan: the conductor, the
      R1e demand-owned seam itself, and the generic model-split planner
      (`planned_multi_request_turn` — R1g: it executes each part as a real
      sub-turn and finalizes the merged reply, so it was always a composite
      planner in fact; leaving it undeclared was the one false hole in this
      inventory, found by the R1g executable-call-site sweep);
    * the fallback of last resort: the model lane.
    """
    return (
        # P0 MIXED-DEMAND TERMINAL CLOSURE — the clock family gets the same
        # declaration the read/arithmetic/write families got: EXECUTION always
        # existed (the front door's date_time arm) but no coverage capability
        # did, so a multi-unit turn of clock + arithmetic minted `mixed=False`
        # (nobody claimed the clock unit), fell to the single plain answering
        # lane, and when that lane's model could not publish, BOTH
        # deterministic siblings erased (measured at base, the strict-xfail
        # class in tests/test_mixed_demand_terminal_closure_p0.py). Earliest
        # precedence: the clock arm sits above the live/currency arms in the
        # front door, and the catalog's ordering should say so.
        LaneSpec("date_time_fast_path", 8, True, "clock"),
        LaneSpec("live_info_fast_path", 10, True, "live_data"),
        LaneSpec("live_data_typed_plan", 20, True, "live_data"),
        LaneSpec("currency_value_contract", 30, True, "currency"),
        LaneSpec("currency_frontdoor", 40, True, "currency"),
        # P0 MIXED-DEMAND — the two deterministic families that had EXECUTION but
        # no DECLARATION. Both were served under `turn_frontdoor_deterministic`,
        # which the catalog declares single-unit-limited (COVERAGE_NONE), so the
        # registry's answer to "who covers this unit?" was *nobody* for a file read
        # and *nobody* for an arithmetic step. With no unit covered, `mixed` reads
        # False and the demand-owned plan never runs: one lane took the whole turn
        # and the other demands were never dispatched (measured on the untouched
        # base 840a2392 — a three-demand turn served an echo of the request and
        # executed none of them). Declaring them is what lets the kernel see a
        # mixed turn as mixed; it is also what stops them claiming a turn they
        # only partly cover.
        # M4 (contract 2026-09-08): two lanes that ADMITTED requests the catalog could not see --
        # the audit lane ("check my project, see how many monolith files it has") and the machine
        # fact tool ("whats eating up my disk space") -- so the whole-turn law read their turns as
        # unowned and a model answered with nothing read. A lane's admission IS its coverage.
        LaneSpec("workspace_audit_frontdoor", 41, True, "workspace_audit"),
        LaneSpec("workspace_read_fast_path", 42, True, "workspace_read"),
        LaneSpec("machine_fact_fast_path", 43, True, "machine_fact"),
        LaneSpec("direct_math_fast_path", 44, True, "arithmetic"),
        # P0 SIMPLE-FILE-WRITE — the write family gets the same declaration the read
        # and arithmetic families got above: EXECUTION existed (the builder's typed
        # workflow lane) but no DECLARATION, so a mixed turn of "create notes.txt
        # containing hello and what is 2 plus 2?" read `mixed` as False — no unit was
        # covered — and the whole turn fell to one lane, losing whichever demand that
        # lane could not serve. The capability probe is the family's own planner
        # extractor (`_extract_workspace_file_plan`), not a second recognizer.
        LaneSpec("workspace_write_workflow", 46, True, "workspace_write"),
        LaneSpec("operator_action_dispatch", 48, True, "operator_action", max_units=1),
        LaneSpec("turn_frontdoor_deterministic", 50, True, COVERAGE_NONE),
        LaneSpec(
            "conductor_multi_intent_plan",
            60,
            True,
            COVERAGE_COMPOSITE_PLAN,
            ROLE_COMPOSITE_PLANNER,
        ),
        LaneSpec(
            "demand_owned_mixed_turn",
            70,
            True,
            COVERAGE_COMPOSITE_PLAN,
            ROLE_COMPOSITE_PLANNER,
        ),
        LaneSpec(
            "planned_multi_request_turn",
            75,
            True,
            COVERAGE_COMPOSITE_PLAN,
            ROLE_COMPOSITE_PLANNER,
        ),
        LaneSpec("attempt_followup", 80, True, COVERAGE_NONE),
        LaneSpec(
            # R1g amendment — the typed secret intake. It consumes the
            # credential side of a message and, when the sanitized remainder
            # carries demand, executes it as owned outcomes of the same
            # canonical turn: a composite planner by construction (one
            # credential outcome + the remainder's unit plan), so it declares
            # the composite role like the seam it mirrors.
            "secret_intake_turn",
            90,
            True,
            COVERAGE_COMPOSITE_PLAN,
            ROLE_COMPOSITE_PLANNER,
        ),
        LaneSpec(
            "model_lane",
            100,
            True,
            COVERAGE_FALLBACK_PLAN,
            ROLE_FALLBACK,
            executes=CAPABILITY_MODEL_REASONING,
        ),
    )


def _validated_catalog(catalog: Iterable[LaneSpec]) -> tuple[LaneSpec, ...]:
    """Type-check and order a catalog, refusing duplicates of id AND precedence.

    Duplicate ids are two declarations of one lane. Duplicate precedence values
    are subtler and just as fatal: `sorted()` would tie-break by arrival order,
    making contested-claim ordering depend on how the catalog was written down —
    exactly the non-determinism precedence exists to remove."""
    entries = tuple(catalog)
    for entry in entries:
        if not isinstance(entry, LaneSpec):
            raise TypeError(
                f"catalog entries must be LaneSpec, got {type(entry).__name__}"
            )
    ids = [entry.lane_id for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError(f"catalog has duplicate lane ids: {sorted(ids)}")
    precedences = [entry.precedence for entry in entries]
    if len(set(precedences)) != len(precedences):
        raise ValueError(
            f"catalog has duplicate precedence values — contested-claim ordering "
            f"would be non-deterministic: {sorted(precedences)}"
        )
    return tuple(sorted(entries, key=lambda spec: spec.precedence))


#: The production catalog — validated (unique ids AND precedences) and
#: precedence-sorted, exactly like any injected one.
LANE_CATALOG: tuple[LaneSpec, ...] = _validated_catalog(_production_catalog())

#: The one ordered registry, DERIVED from the catalog (never a second list):
#: every non-fallback lane id in precedence order. This preserves the
#  historical compatibility surface — same relative order for the lanes that
#: were listed before R1f — while coverage-participating lanes (currency) now
#: carry precedence too, so ordering and coverage cannot disagree.
LANE_REGISTRY: tuple[str, ...] = tuple(
    spec.lane_id for spec in LANE_CATALOG if spec.role != ROLE_FALLBACK
)

# =====================================================================
# R1g AMENDMENT, requirement 8 — the ROUTE REGISTRY and the mechanical
# finalization check.
#
# Before this, "which lanes can finalize an external turn" was a claim a TEST
# owned (a hand-maintained tuple in the R1g gauntlet): an undeclared lane kept
# the freedom to finalize until a developer remembered to update the tuple.
# The registry below is the runtime form of that claim: the family-DEFINING
# routes (the ones with their own catalog lanes) map explicitly; every other
# `deterministic:` route belongs to the deterministic front-door tier the
# catalog already declares as ONE single-unit-limited family
# (`turn_frontdoor_deterministic` — the fast intents, contracts and utility
# reads all live under it by the catalog's own design); the model lane's route
# prefixes map to the fallback. The COMMON FINALIZATION SEAM consults it and
# mechanically REFUSES a finalization whose family is not in the active
# catalog — undeclared finalization is rejected by the runtime, not by a tuple.
# =====================================================================

#: Family-defining routes — the route a lane finalizes its own turns under.
ROUTE_FAMILIES: dict[str, str] = {
    # the live family
    "live_info_fast_path": "live_info_fast_path",
    "live_data_plan_unavailable": "live_data_typed_plan",
    "live_data_typed_plan": "live_data_typed_plan",
    # the currency family
    "currency_multislice_fast_path": "currency_frontdoor",
    "currency_slice_answers": "currency_frontdoor",
    # the workspace-read and arithmetic families (P0 mixed-demand)
    "workspace_runtime_fast_path": "workspace_read_fast_path",
    "direct_math_fast_path": "direct_math_fast_path",
    # The route the math lane actually STAMPS on a served turn: `_fast_path_result`
    # records reason="direct_math_fast_path" and the lane then overwrites
    # route_reason with "pure_arithmetic_expression", so the served fact carries the
    # second name. Both map to the one family; leaving the served one out made the
    # resolver answer None for a lane that had plainly just run.
    "pure_arithmetic_expression": "direct_math_fast_path",
    # the conductor composite
    "conductor_multi_intent_plan": "conductor_multi_intent_plan",
    "conductor_integrity_failure": "conductor_multi_intent_plan",
    "conductor plan deadline": "conductor_multi_intent_plan",
    # the R1e demand-owned composite
    "demand_owned_mixed_turn": "demand_owned_mixed_turn",
    "demand_owned_mixed_turn_failed": "demand_owned_mixed_turn",
    "demand_owned_mixed_turn_degraded": "demand_owned_mixed_turn",
    # the generic model-split planner composite
    "planned_multi_request_turn": "planned_multi_request_turn",
    # the attempt-followup family
    "attempt_followup_correct_previous": "attempt_followup",
    "attempt_followup_explain_failure": "attempt_followup",
    "attempt_followup_list_entities": "attempt_followup",
    "attempt_followup_repeat_original": "attempt_followup",
    "attempt_followup_retry": "attempt_followup",
    "attempt_followup_retry_failed": "attempt_followup",
    "attempt_followup_retry_inflight": "attempt_followup",
    "attempt_followup_retry_no_turn_identity": "attempt_followup",
    # the typed secret intake (R1g amendment)
    "cloud_key_command": "secret_intake_turn",
    "image_key_command": "secret_intake_turn",
    "bare_secret_intercept": "secret_intake_turn",
    "secret_intake_turn": "secret_intake_turn",
    "secret_intake_turn_degraded": "secret_intake_turn",
    "secret_intake_failed": "secret_intake_turn",
    # the deterministic front-door tier: ONE declared family owns every
    # remaining `deterministic:` route (see `finalization_family`).
    "turn_frontdoor_deterministic": "turn_frontdoor_deterministic",
}

#: Route-label prefixes of the model fallback (the chat surface composes them
#: with the selected model id, e.g. ``model_minimal:qwen3:8b``).
_MODEL_ROUTE_PREFIXES: tuple[str, ...] = ("model_minimal:", "plain_task_minimal:")


def finalization_family(reason: str, *, route: str = "") -> str | None:
    """The lane family a finalization runs under, or None when the seam should
    not judge it (non-deterministic, unmapped routes — e.g. intake gates and
    internal envelopes — are outside the deterministic-tier law).

    Pure: the route/reason in, the family (or the deterministic default) out.
    A `deterministic:` route nobody mapped resolves to the front-door tier —
    the family the catalog declares for exactly those recognizers — so the
    check that follows is whether THAT family is declared, which is precisely
    the mechanical rejection of undeclared finalization."""
    reason = str(reason or "")
    route = str(route or "")
    if not reason and route.startswith("deterministic:"):
        reason = route.split(":", 1)[1]
    if reason in ROUTE_FAMILIES:
        return ROUTE_FAMILIES[reason]
    for prefix in _MODEL_ROUTE_PREFIXES:
        if route.startswith(prefix) or reason.startswith(prefix):
            return "model_lane"
    if route.startswith("deterministic:") or reason.startswith("deterministic:"):
        return "turn_frontdoor_deterministic"
    return None


def executed_capability(lane_id: str) -> str:
    """The capability `lane_id` PROVIDES when it executes a demand, or "".

    `find_spec(...).coverage` answers the claim question; this answers the
    execution question, which is the one a discharge ledger is recording. They
    agree for every lane whose claim and execution are the same act, and diverge
    exactly where a lane executes work it never claims per-unit — the model lane.
    """
    spec = find_spec(str(lane_id or ""))
    if spec is None:
        return ""
    return str(spec.executes or spec.coverage or "")


def active_catalog() -> tuple[LaneSpec, ...]:
    """The catalog in force on this context: the scoped one if a test or
    embedder injected it, else the production catalog."""
    scoped = _CATALOG_SCOPE.get()
    # `is not None`, not truthiness: an injected EMPTY catalog is a real
    # declaration ("no lanes") and must be in force, not silently swapped for
    # the production catalog because () is falsy.
    return scoped if scoped is not None else LANE_CATALOG


def find_spec(lane_id: str) -> LaneSpec | None:
    """The active catalog's declaration for `lane_id`, or None if unregistered."""
    wanted = str(lane_id or "")
    for spec in active_catalog():
        if spec.lane_id == wanted:
            return spec
    return None


@contextmanager
def scoped_catalog(catalog: Iterable[LaneSpec]):
    """Inject a catalog for the current context — PURE, no process-global state.

    Entries are validated (typed `LaneSpec`s, unique lane ids) and ordered by
    their declared precedence, so mediation inside the scope ranks by the
    injection's own order. Restores the previous catalog on exit.
    """
    ordered = _validated_catalog(catalog)
    token = _CATALOG_SCOPE.set(ordered)
    try:
        yield ordered
    finally:
        _CATALOG_SCOPE.reset(token)

#: Decisions a unit can receive (M4's vocabulary; the terminal/escalate states
#: arrive with M6's taxonomy — this slice records the routing decisions).
DECISION_CLAIMED = "claimed"
DECISION_MODEL_REQUIRED = "model_required"
DECISION_UNCLAIMED = "unclaimed"


@dataclass(frozen=True)
class ClaimDecision:
    """The kernel's one decision for one canonical obligation."""

    unit_id: str
    decision: str
    #: The lane that owns the unit when decision == claimed.
    owner: str = ""
    #: Every lane that claimed the unit, registry-ordered — the contest record.
    claimants: tuple[str, ...] = ()
    #: The lanes that DECLINED while holding it in scope, with their reasons.
    declines: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ClaimLedger:
    """The kernel's decisions for one turn — one per canonical unit."""

    decisions: tuple[ClaimDecision, ...] = field(default_factory=tuple)

    def for_unit(self, unit_id: str) -> ClaimDecision | None:
        for decision in self.decisions:
            if decision.unit_id == unit_id:
                return decision
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [
                {
                    "unit_id": d.unit_id,
                    "decision": d.decision,
                    "owner": d.owner,
                    "claimants": list(d.claimants),
                    "declines": [[lane, reason] for lane, reason in d.declines],
                }
                for d in self.decisions
            ]
        }


def _lane_rank(lane_id: str) -> tuple[int, str]:
    """Active-catalog precedence; unknown lanes sort after it, alphabetically.

    Reads the ACTIVE catalog (scoped or production) so an injected lane
    participates in mediation by its declared precedence — the registry order
    stays the only contested-claim ordering there is."""
    catalog = active_catalog()
    for index, spec in enumerate(catalog):
        if spec.lane_id == lane_id:
            return (index, lane_id)
    return (len(catalog), lane_id)


@dataclass(frozen=True)
class MediationVerdict:
    """The kernel's answer to a lane asking 'may I serve these units?'.

    M4 slice 2 — claim MEDIATION. A lane consults the kernel BEFORE serving;
    the kernel compares the lane's registry rank against the claims already
    recorded this turn. ``allowed`` means the lane is the earliest-ranked
    claimant for every unit it wants; otherwise ``superseded_by`` names the
    earlier lane that owns the contested units (and ``contested`` lists them).
    """

    allowed: bool
    lane_id: str = ""
    superseded_by: str = ""
    contested: tuple[str, ...] = ()


def mediate(
    proposals: Iterable[LaneProposal],
    lane_id: str,
    unit_ids: Iterable[str],
) -> MediationVerdict:
    """One mediation decision for one lane's intended claim (pure).

    The registry order is the ONLY order consulted — arrival order of the
    recorded proposals is irrelevant, which is what retires the cascade's
    implicit sequencing for contested units: a lane that would have won by
    being called first loses here if a later-recorded lane ranks earlier.
    """
    proposals = [p for p in proposals if isinstance(p, LaneProposal)]
    wanted = tuple(dict.fromkeys(str(u) for u in unit_ids))
    my_rank = _lane_rank(lane_id)
    contested: list[str] = []
    superseder = ""
    for proposal in proposals:
        if proposal.lane_id == lane_id or not proposal.obligations_claimed:
            continue
        if _lane_rank(proposal.lane_id) < my_rank:
            overlap = tuple(u for u in wanted if u in proposal.obligations_claimed)
            if overlap:
                contested.extend(overlap)
                if not superseder:
                    superseder = proposal.lane_id
    if not contested:
        return MediationVerdict(allowed=True, lane_id=lane_id)
    return MediationVerdict(
        allowed=False,
        lane_id=lane_id,
        superseded_by=superseder,
        contested=tuple(dict.fromkeys(contested)),
    )


def decide_claims(units: Iterable[Any], proposals: Iterable[LaneProposal]) -> ClaimLedger:
    """ONE kernel decision per canonical unit, from the recorded proposals.

    Pure: units (objects with ``unit_id``) and the typed proposal stream in —
    decisions out. The fallback claim (a lane claiming the WHOLE set when no
    deterministic lane claimed anything, M3 slice 4) maps to
    ``model_required`` for the units it covers.
    """
    proposals = [p for p in proposals if isinstance(p, LaneProposal)]
    decisions: list[ClaimDecision] = []
    for unit in units:
        unit_id = str(getattr(unit, "unit_id", ""))
        claimants = [
            p.lane_id for p in proposals if unit_id in p.obligations_claimed
        ]
        declines = [
            (p.lane_id, p.refusal_reason)
            for p in proposals
            if not p.obligations_claimed and p.refusal_reason
        ]
        # The fallback claim (M3 slice 4): a NON-REGISTRY lane claiming the
        # WHOLE minted set with nothing unclaimed — the model path of last
        # resort. Its claim reads model_required, not claimed-by-a-lane: the
        # model is not an owner, it is the escalation.
        fallback_claims = {
            p.lane_id
            for p in proposals
            if p.obligations_claimed
            and not p.unclaimed_obligations
            and p.lane_id not in LANE_REGISTRY
        }
        lane_claimants = [c for c in set(claimants) if c not in fallback_claims]
        if lane_claimants:
            # MEDIATION resolves the contest: the winner is the lane the
            # mediator would allow — earliest registry rank (arrival order
            # irrelevant). The losers stay in the claimants record.
            winner = min(lane_claimants, key=_lane_rank)
            decisions.append(
                ClaimDecision(
                    unit_id=unit_id,
                    decision=DECISION_CLAIMED,
                    owner=winner,
                    claimants=tuple(sorted(set(claimants), key=_lane_rank)),
                    declines=tuple(declines),
                )
            )
            continue
        decisions.append(
            ClaimDecision(
                unit_id=unit_id,
                decision=(
                    DECISION_MODEL_REQUIRED if claimants else DECISION_UNCLAIMED
                ),
                owner=next(iter(sorted(claimants))) if claimants else "",
                declines=tuple(declines),
            )
        )
    return ClaimLedger(decisions=tuple(decisions))


__all__ = [
    "COVERAGE_COMPOSITE_PLAN",
    "COVERAGE_FALLBACK_PLAN",
    "COVERAGE_NONE",
    "DECISION_CLAIMED",
    "DECISION_MODEL_REQUIRED",
    "DECISION_UNCLAIMED",
    "LANE_CATALOG",
    "LANE_REGISTRY",
    "ROLE_COMPOSITE_PLANNER",
    "ROLE_DETERMINISTIC",
    "ROLE_FALLBACK",
    "ROUTE_FAMILIES",
    "ClaimDecision",
    "ClaimLedger",
    "LaneSpec",
    "MediationVerdict",
    "active_catalog",
    "decide_claims",
    "finalization_family",
    "find_spec",
    "mediate",
    "scoped_catalog",
]
