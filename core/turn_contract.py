"""MILESTONE 2 — the canonical typed turn contract. Slice 1: TurnRequest at ingress.

THE INVARIANT
-------------
Every turn, from every surface (HTTP /api/chat, the OpenClaw-compatible API, the CLI
one-shot/REPL, in-process library callers), begins as ONE typed request whose
identity, exact text, surface, and trust principal were derived by SERVER-side code —
never read back from caller-claimable fields. Before this module, turn truth started
as scattered dictionary keys (`request_id` in a ContextVar, `session_id` as a kwarg,
`chat_id`/`surface`/`_owner_local` as loose source_context entries) that every seam
re-assembled by hand — the M2 weakness: "turn truth is carried through mutable
dictionaries ... and lane-specific shapes."

AUTHORITIES THIS SLICE DOES AND DOES NOT MOVE
---------------------------------------------
This slice INTRODUCES the contract; it does not yet displace any consumer. The
existing derivation seams remain the authorities for their fields and the contract
READS them, so behavior is unchanged:
* request identity  — the A0 door / invocation ledger (`current_request_id`);
* trust principal   — `core.request_trust.request_is_owner_local` (the server-stamped
  ``_owner_local`` for HTTP; the trusted in-process surface fallback otherwise);
* session/turn ids  — the door-stamped canonical keys, else the interior mint.
Fields whose single owner does not exist yet (model pin, network/spend policy,
cancellation token, reasoning policy) are carried as empty defaults with named
constants — later milestones (M5/M8/M9) fill them by DISPLACING the scattered reads,
which is the only way this contract is allowed to grow.

The contract rides the turn under the reserved `turn_request` key (stripped from
inbound HTTP bodies with the other trust keys, then written only by this code), so
ingress may not forge it. REMOVAL CONDITION for the dict projection: when lanes
consume the typed object directly, `to_dict()` and the context key shrink to the
egress adapters that still serialize them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.request_trust import request_is_owner_local
from core.turn_prohibitions import (
    NO_PROHIBITIONS,
    TurnProhibitions,
    prohibitions_from_text,
)

#: The source_context key the server-written contract rides under. Reserved: an
#: inbound body carrying it is stripped at the door exactly like the trust keys.
TURN_REQUEST_KEY = "turn_request"

#: Trust principals, per `core.request_trust`'s two-valued reading of the world.
PRINCIPAL_OWNER_LOCAL = "owner_local"
PRINCIPAL_REMOTE_UNTRUSTED = "remote_untrusted"

#: Placeholder for policy fields whose single owning seam does not exist yet. A lane
#: reading these today must treat "" as "no ingress-level restriction declared" —
#: exactly what the scattered reads it replaces already yield.
POLICY_UNDECLARED = ""


@dataclass(frozen=True)
class TurnRequest:
    """One turn, as it entered the runtime — typed, immutable, server-derived.

    Construction is `from_ingress` ONLY (the single constructor every surface shares);
    the field list is the M2 contract's request half. Later slices add TurnState,
    LaneProposal, and TurnResult beside it in this module — one contract module, not
    one per lane.
    """

    request_id: str
    turn_id: str
    session_id: str
    #: The user's text, VERBATIM — typos, unicode, newlines, empty string included.
    #: Normalization is a downstream concern; the contract preserves the input.
    user_text: str
    conversation_ref: str = ""
    surface: str = ""
    platform: str = ""
    trust_principal: str = PRINCIPAL_REMOTE_UNTRUSTED
    operating_mode: str = ""
    #: Policy fields whose owners converge in M5/M8/M9 — see the module docstring.
    model_pin: str = POLICY_UNDECLARED
    locality: str = POLICY_UNDECLARED
    autonomy_override: str = POLICY_UNDECLARED
    #: P0 POLICY CONSERVATION — the prohibitions frozen from the WHOLE user
    #: text at ingress (typed families/toolsets/reason codes/digest; see
    #: `core.turn_prohibitions`). Derived truth, deliberately outside equality
    #: and the egress projection: it is a function of `user_text`, and the
    #: typed object is its only home. Children run under this same request
    #: object, so a child may never see a wider constraint set than its parent.
    prohibitions: TurnProhibitions = field(default=NO_PROHIBITIONS, compare=False)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The ingress-compat projection (egress adapters and receipts only)."""
        return {
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "user_text": self.user_text,
            "conversation_ref": self.conversation_ref,
            "surface": self.surface,
            "platform": self.platform,
            "trust_principal": self.trust_principal,
            "operating_mode": self.operating_mode,
            "model_pin": self.model_pin,
            "locality": self.locality,
            "autonomy_override": self.autonomy_override,
        }

    @classmethod
    def from_ingress(
        cls,
        *,
        user_text: str,
        source_context: dict[str, Any] | None,
        request_id: str,
        turn_id: str,
        session_id: str,
    ) -> TurnRequest:
        """Build the one canonical request from server-derived inputs.

        `source_context` is the POST-STRIP context (the HTTP door strips reserved
        trust keys before the runtime sees them; in-process callers are trusted local
        code by the same stance R-2 documents). The trust principal is DERIVED here
        via `request_is_owner_local` — a caller-supplied principal string is never
        read, which is the non-elevation guarantee the contract tests pin.
        """
        context = source_context if isinstance(source_context, dict) else {}
        return cls(
            request_id=str(request_id or ""),
            turn_id=str(turn_id or ""),
            session_id=str(session_id or ""),
            user_text=str(user_text or ""),
            conversation_ref=str(context.get("chat_id") or ""),
            surface=str(context.get("surface") or ""),
            platform=str(context.get("platform") or ""),
            trust_principal=(
                PRINCIPAL_OWNER_LOCAL
                if request_is_owner_local(context)
                else PRINCIPAL_REMOTE_UNTRUSTED
            ),
            operating_mode=str(context.get("operating_mode") or ""),
            # Captured verbatim from the ingress-declared values only; no policy
            # derivation lives here (that would be a second authority).
            autonomy_override=str(context.get("autonomy_override") or ""),
            # P0 policy conservation: freeze the prohibitions from the WHOLE
            # text the user actually sent — the one mint every child inherits.
            # VOOL School: the server-stamped lesson/class policy UNIONs its
            # tool floors into the same frozen set, so a web-blocked lesson
            # cannot reach the web through a child lane even when the student's
            # own words never mentioned it (goal §26: machine-enforced).
            prohibitions=_school_union(
                prohibitions_from_text(str(user_text or "")), context
            ),
        )


def _school_union(prohibitions, context: dict | None):
    """Union the frozen prohibitions with the school policy's tool floors.

    Fail-soft and additive-only: a school layer can freeze MORE (web, live
    retrieval toolsets), never less. Reads the reserved ``school_policy`` key
    the chat ingress gate stamped after the signed session verified.
    """
    try:
        school_ctx = (context or {}).get("school_policy")
        policy = (school_ctx or {}).get("policy") if isinstance(school_ctx, dict) else None
        if not isinstance(policy, dict) or not policy:
            return prohibitions
        allowed = policy.get("tool_families")
        if not isinstance(allowed, list):
            return prohibitions
        allowed_lower = {str(f).strip().lower() for f in allowed}
        families = set(prohibitions.families)
        toolsets = set(prohibitions.prohibited_toolsets)
        reasons = list(prohibitions.reason_codes)
        if "web" not in allowed_lower:
            families.add("web")
            toolsets.add("web_fetch")
            reasons.append("school_policy_web_blocked")
        if not allowed_lower & {"web", "browser"}:
            toolsets.update({"market_prices", "weather", "news"})
            reasons.append("school_policy_live_data_blocked")
        from core.turn_prohibitions import TurnProhibitions

        return TurnProhibitions(
            families=frozenset(families),
            prohibited_toolsets=frozenset(toolsets),
            reason_codes=tuple(dict.fromkeys(reasons)),
            clauses=prohibitions.clauses,
        )
    except Exception:
        return prohibitions


#: The source_context key the server-written turn state rides under. Reserved like
#: the request key: only core code may write one.
TURN_STATE_KEY = "turn_state"


@dataclass
class TurnState:
    """The turn's accumulated state, as ONE typed handle -- slice 2 of the contract.

    DESIGN LAW (what makes this not-a-second-authority): the state holds REFERENCES
    to the single durable/execution objects, never copies of their truth. The
    execution identity is the SAME dict R-3 published into the context; the
    obligation set is recorded as the (set_id, version) binding of the LEDGER's
    active set for this turn -- the ledger stays the sole writer of dispositions.
    Record-lists (attempts/effects/evidence/approvals/failures) start empty and
    gain their writers as later milestones displace the scattered reads that fill
    them today; a field with no writer yet is empty by construction, never guessed.

    Slice 2's displacement is concrete: run_once's post-turn read of the scattered
    ``source_context.get("_execution_identity")`` now goes through this object
    (`execution_identity` + `mark_turn_ok`), so the handle is load-bearing on day
    one rather than a parallel path.
    """

    request: TurnRequest
    #: REFERENCE to the R-3 execution-identity dict -- the one copy, typed here.
    execution_identity: dict[str, Any] | None = None
    #: The trace/execution key for this turn (``execution_id`` of the identity).
    trace_identity: str = ""
    #: The LEDGER binding for this turn's canonical obligation set -- ids only.
    obligation_set: tuple[str, str] | None = None
    #: Obligation IDs of the canonical set at binding time -- ids, never states.
    canonical_obligations: tuple[str, ...] = ()
    #: Attempt IDs this turn minted (root first). The durable rows stay in
    #: runtime_continuity; this is the typed index of them.
    attempts: list[str] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    pending_approvals: list[str] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    #: Lane proposals recorded during the turn (slice 3+), absorbed from the
    #: TURN_PROPOSALS_KEY transport at construction -- ids and intents only.
    proposals: list[LaneProposal] = field(default_factory=list)
    #: M4: the kernel's claim decisions derived from those proposals at the
    #: spine (core.lane_registry.decide_claims) -- one per canonical unit.
    claim_ledger: Any = None

    def mark_turn_ok(self, ok: bool) -> None:
        """The turn's own success report, written THROUGH the shared identity dict.

        Displaces run_once's direct ``identity["turn_ok"] = ...`` write: same
        object, same key, one typed door.
        """
        if isinstance(self.execution_identity, dict):
            self.execution_identity["turn_ok"] = bool(ok)

    @classmethod
    def from_intake(
        cls,
        *,
        request: TurnRequest,
        source_context: dict[str, Any] | None,
        obligation_set: tuple[str, str] | None = None,
        canonical_obligations: tuple[str, ...] = (),
    ) -> TurnState:
        """Bind the state to the request and the identity objects the turn published.

        `obligation_set`/`canonical_obligations` are the CALLER's reading of the
        ledger's binding for THIS turn (ids only); this constructor never queries
        the ledger itself -- the turn spine owns that read, where the binding's
        lifetime (bound at the R-6 mint, unbound in run_once's finally) is visible.
        """
        context = source_context if isinstance(source_context, dict) else {}
        identity = context.get("_execution_identity")
        identity = identity if isinstance(identity, dict) else None
        attempt_id = str((identity or {}).get("attempt_id") or "")
        proposals = [
            item for item in context.get(TURN_PROPOSALS_KEY) or []
            if isinstance(item, LaneProposal)
        ]
        return cls(
            request=request,
            execution_identity=identity,
            trace_identity=str((identity or {}).get("execution_id") or ""),
            obligation_set=obligation_set,
            canonical_obligations=tuple(canonical_obligations),
            attempts=[attempt_id] if attempt_id else [],
            proposals=proposals,
        )


#: The source_context key lanes append their proposals to during the turn; the
#: TurnState absorbs the list at its construction (the reserved key is transport,
#: the typed state is the home). Reserved so an inbound body cannot forge one.
TURN_PROPOSALS_KEY = "turn_proposals"

#: The per-demand EXECUTOR transport: {task index -> {"route", "route_reason"}},
#: written by the planner hook from each sub-turn's own served result. The executor
#: is a FACT the sub-turn reports, never something the parent infers — which is why
#: a demand the deterministic registry does not claim can still be recorded with the
#: real capability that served it.
TURN_DEMAND_EXECUTORS_KEY = "turn_demand_executors"

#: The per-demand execution ledger transport (P0 mixed-demand). One row per demand
#: unit: stable id, selected capability, owning lane, whether execution was
#: attempted, and the typed terminal state it reached.
TURN_DEMAND_LEDGER_KEY = "turn_demand_ledger"


@dataclass(frozen=True)
class LaneProposal:
    """What one lane offers to do with this turn -- slice 3 of the contract.

    THE NO-VISIBLE-BYTES LAW (M2/M4): a proposal carries WORK, never answer
    content. Structurally enforced -- this class has no field that can hold
    rendered text, and `test_a_proposal_cannot_carry_answer_bytes` pins that
    adding one goes red. Final bytes belong to the finalizer (M6), reached only
    after the kernel acts on proposals (M4).

    Slice 3's displacement is concrete: the live-data lane -- the first producing
    lane -- sources its route identity and confidence FROM its proposal instead
    of the scattered literals it hand-passed to `_fast_path_result`, and its
    decline paths record a typed refusal reason instead of returning None
    silently (the shape M4's "record why every lane claimed or declined" builds
    on).
    """

    lane_id: str
    #: Demand-unit ids this lane claims (the unit-grain binding M3B built).
    obligations_claimed: tuple[str, ...] = ()
    #: Toolsets/capabilities the lane needs (requirements.allowed_toolsets).
    required_capabilities: tuple[str, ...] = ()
    #: Typed effects it intends (each subtask's checkable tool intent).
    effects_requested: tuple[str, ...] = ()
    #: Result fields it promises to evidence (each subtask's contract fields).
    evidence_expected: tuple[str, ...] = ()
    #: The lane's own confidence for this claim (its constant unless derived).
    confidence: float = 0.0
    #: Why the lane declined, when it did -- typed, never prose-guessed.
    refusal_reason: str = ""
    #: M3: canonical units this lane could not bind -- named, never silently
    #: absent. `obligations_claimed + unclaimed_obligations` must cover every
    #: unit the lane was handed (the conservation law the census checks).
    unclaimed_obligations: tuple[str, ...] = ()
    #: Whether this lane may finalize the turn if the kernel grants the claim.
    terminal_eligibility: str = "eligible"

    @property
    def claimed(self) -> bool:
        return not self.refusal_reason


@dataclass(frozen=True)
class TurnResult:
    """The turn's terminal truth, typed -- slice 4, the contract's final half.

    Wired at THE finalization seam (`finalize_answer`): the values the commit
    envelope assembled from scattered locals -- canonical bytes, content hash,
    status, the demand census -- are computed INTO this object first, and the
    commit's own fields are then PROJECTED from it (`to_commit_fields`). That
    ordering is the slice's displacement: the typed object is the computed-once
    home, the dict the compatibility projection egress adapters keep.

    Obligation buckets follow the LEDGER's own three-valued census (satisfied /
    indeterminate / unanswered) -- no new taxonomy, which is M6's to build.
    Evidence references, effect receipts, provider/model attribution, token-usage
    completeness and retry classification are carried as typed slots whose
    writers displace in M5/M6/M9; empty means "not yet declared", never guessed.
    """

    turn_id: str = ""
    request_id: str = ""
    finalization_id: str = ""
    terminal_state: str = ""
    committed_answer: str = ""
    final_content_hash: str = ""
    fulfilled_obligations: int = 0
    unresolved_obligations: int = 0
    refused_obligations: int = 0
    evidence_references: tuple[str, ...] = ()
    #: M5/R2 — the turn's typed effect receipts, as the JSON-safe dicts the
    #: turn's effect ledger finalized. One entry per ATTEMPTED effect, denials
    #: included; empty means the turn attempted none, which is a fact only when
    #: a ledger was open (see `effect_receipts_truncated` for the other half of
    #: the account's honesty).
    effect_receipts: tuple[dict[str, Any], ...] = ()
    #: True when the turn made more effect attempts than the ledger keeps
    #: detail for. Without it a bounded list reads as a complete account.
    effect_receipts_truncated: bool = False
    #: R2b1 — exactly how many receipts the ledger dropped when the account
    #: truncated. The flag alone is honesty; the count is the checkable half:
    #: kept + dropped must equal the turn's real attempt total.
    effect_receipts_dropped: int = 0
    #: R2b2b — the per-effect OUTCOME account, derived from the ledger's
    #: registry: what was attempted, whether transport ran, and how it ended —
    #: one entry per LOGICAL effect, denials included. Where `effect_receipts`
    #: is the append-only detail (and truncates), this is the answer key: it
    #: stays complete under receipt truncation and says `unresolved` when an
    #: attempt began and never ended, so "authorized" can never read as
    #: "succeeded" downstream.
    effect_outcomes: tuple[dict[str, Any], ...] = ()
    provider_attribution: str = ""
    model_attribution: str = ""
    token_usage_complete: bool | None = None
    retry_classification: str = ""
    #: ROOT-CAUSE CONTRACT — the turn's repair-diagnosis truth, DERIVED from
    #: the typed `core.root_cause_contract.RootCauseContract` at the
    #: finalization seam (never set by hand): the state vocabulary and the
    #: operator status travel with the terminal truth so the served surface
    #: and the commit cannot disagree. Empty means no diagnosis contract was
    #: open for this turn — a fact, never a default repair claim.
    root_cause_state: str = ""
    root_cause_status: str = ""

    def to_commit_fields(self) -> dict[str, Any]:
        """The egress projection the commit envelope reads its truth from."""
        return {
            "status": self.terminal_state,
            "canonical_content": self.committed_answer,
            "content_hash": self.final_content_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        """The JSON-safe egress projection (the commit envelope carries THIS;
        `TurnResult(**d)` reconstructs the typed object losslessly)."""
        return {
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "finalization_id": self.finalization_id,
            "terminal_state": self.terminal_state,
            "committed_answer": self.committed_answer,
            "final_content_hash": self.final_content_hash,
            "fulfilled_obligations": self.fulfilled_obligations,
            "unresolved_obligations": self.unresolved_obligations,
            "refused_obligations": self.refused_obligations,
            "evidence_references": list(self.evidence_references),
            "effect_receipts": [dict(receipt) for receipt in self.effect_receipts],
            "effect_receipts_truncated": bool(self.effect_receipts_truncated),
            "effect_receipts_dropped": int(self.effect_receipts_dropped),
            "effect_outcomes": [dict(outcome) for outcome in self.effect_outcomes],
            "provider_attribution": self.provider_attribution,
            "model_attribution": self.model_attribution,
            "token_usage_complete": self.token_usage_complete,
            "retry_classification": self.retry_classification,
            "root_cause_state": self.root_cause_state,
            "root_cause_status": self.root_cause_status,
        }


__all__ = [
    "POLICY_UNDECLARED",
    "PRINCIPAL_OWNER_LOCAL",
    "PRINCIPAL_REMOTE_UNTRUSTED",
    "TURN_DEMAND_EXECUTORS_KEY",
    "TURN_DEMAND_LEDGER_KEY",
    "TURN_PROPOSALS_KEY",
    "TURN_REQUEST_KEY",
    "TURN_STATE_KEY",
    "LaneProposal",
    "TurnRequest",
    "TurnResult",
    "TurnState",
]
