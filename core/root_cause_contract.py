"""THE ROOT-CAUSE CONTRACT — the operator's debugging doctrine as typed runtime law.

For years the doctrine lived as prose: "fix root causes, not symptoms", "never
call a containment patch a repair", "say unresolved when the cause is unknown".
Prose constrains nothing — a model can restate it while reporting a symptom
patch as a completed repair. This module is that doctrine as a STATE MACHINE
with a completion gate, the same way `core.runtime_task_outcome` made
fulfilment truth typed and `audit_verdict.py` made report honesty typed.

ONE AUTHORITY, NO SECOND TRUTH
------------------------------
* The six states and their lawful transitions live HERE.
* The completion gate (seam evidence + recurrence test + cumulative regression)
  lives HERE; no lane may hand-assemble a "repaired" record that the validator
  at `validate_publication` would accept.
* The diagnosis registry lives HERE: identity and evidence survive retries and
  resumes within the process (rule 6). The durable cross-restart store is a
  named gap — see the module docs in docs/root-cause-contract.md.

The turn context carries the contract under the reserved ``root_cause_contract``
key (stripped from inbound HTTP bodies with the other trust keys). The key
holds a frozen snapshot; the registry holds the live truth, so a context COPY
(runtime tool lanes copy the dict) still resolves the latest state through
`current_contract`. Writers go through this module only — a lane that wants a
different state must come through `advance`, which is where the law is.

STATES
------
``symptom_observed``      the problem is on record; nothing established yet.
``root_cause_hypothesis`` a causal claim exists, attributed to its source.
``root_cause_verified``   the hypothesis is confirmed AT THE OWNING SEAM by a
                          passed recurrence test. NOT completion: completion
                          additionally requires cumulative regression.
``root_cause_repaired``   completion: a root repair is declared AND verified AND
                          the wider regression suite is green. Terminal.
``containment_only``      a containment action exists and is the honest label;
                          may be terminal when the user asked for a workaround,
                          and never relabels as repair.
``unresolved``            the cause is NOT established. Unknown stays unknown.

The published operator status is DERIVED from the state, never set by hand:
"Root cause repaired" · "Root cause verified" · "Root cause hypothesis — not
verified" · "Containment only" · "Symptom observed — cause not established" ·
"Unresolved".
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

#: The six contract states (exact operator vocabulary).
SYMPTOM_OBSERVED = "symptom_observed"
CONTAINMENT_ONLY = "containment_only"
ROOT_CAUSE_HYPOTHESIS = "root_cause_hypothesis"
ROOT_CAUSE_VERIFIED = "root_cause_verified"
ROOT_CAUSE_REPAIRED = "root_cause_repaired"
UNRESOLVED = "unresolved"

ROOT_CAUSE_STATES: frozenset[str] = frozenset(
    {
        SYMPTOM_OBSERVED,
        CONTAINMENT_ONLY,
        ROOT_CAUSE_HYPOTHESIS,
        ROOT_CAUSE_VERIFIED,
        ROOT_CAUSE_REPAIRED,
        UNRESOLVED,
    }
)

#: Lawful transitions. The completion path is one-way through verification:
#: containment and hypothesis may never leap to repaired, and a repaired
#: diagnosis is terminal (new symptoms are a new diagnosis id).
_LAWFUL_TRANSITIONS: dict[str, frozenset[str]] = {
    SYMPTOM_OBSERVED: frozenset(
        {ROOT_CAUSE_HYPOTHESIS, CONTAINMENT_ONLY, UNRESOLVED}
    ),
    ROOT_CAUSE_HYPOTHESIS: frozenset(
        {ROOT_CAUSE_VERIFIED, CONTAINMENT_ONLY, UNRESOLVED}
    ),
    ROOT_CAUSE_VERIFIED: frozenset(
        {ROOT_CAUSE_REPAIRED, CONTAINMENT_ONLY, UNRESOLVED}
    ),
    ROOT_CAUSE_REPAIRED: frozenset(),
    # A containment outcome may still be followed by the real repair — through
    # verification, never around it. An unresolved diagnosis may reopen.
    CONTAINMENT_ONLY: frozenset(
        {ROOT_CAUSE_HYPOTHESIS, ROOT_CAUSE_VERIFIED, UNRESOLVED}
    ),
    UNRESOLVED: frozenset({ROOT_CAUSE_HYPOTHESIS}),
}

#: Derived, compact, operator-visible status per state (rule 8). One mapping,
#: consumed by the finalizer stamp and the served payload — never set by hand.
_OPERATOR_STATUS: dict[str, str] = {
    ROOT_CAUSE_REPAIRED: "Root cause repaired",
    ROOT_CAUSE_VERIFIED: "Root cause verified",
    ROOT_CAUSE_HYPOTHESIS: "Root cause hypothesis — not verified",
    CONTAINMENT_ONLY: "Containment only",
    SYMPTOM_OBSERVED: "Symptom observed — cause not established",
    UNRESOLVED: "Unresolved",
}

#: The reserved source_context key the contract rides. Stripped from inbound
#: HTTP bodies (see RESERVED_TRUST_KEYS); written only by this module.
ROOT_CAUSE_CONTRACT_KEY = "root_cause_contract"

#: Evidence kinds the contract distinguishes. Open vocabulary but the gate
#: keys on "seam": verification requires evidence AT THE OWNING SEAM.
SEAM_EVIDENCE_KIND = "seam"


class RootCauseStateError(ValueError):
    """An unlawful transition or a completion claim the gate refuses.

    The message names the missing requirement so the refusal is attributable
    (the caller surfaces it; it never silently downgrades).
    """


class RootCauseContractError(ValueError):
    """A published contract record that is inconsistent or unknown (wire)."""


@dataclass(frozen=True)
class DiagnosisEvidence:
    """One piece of recorded evidence, with its kind and a stable reference."""

    kind: str
    ref: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail}


@dataclass(frozen=True)
class ValidationOutcome:
    """The outcome of one validation run: recurrence test or regression suite.

    `scope` is how many checks the identity covered (>=2 means a suite). The
    gate uses it to refuse the single reproduction recycled as a "regression
    suite" — a vacuous completion claim (rule 2's anti-fabrication half).
    """

    ref: str
    passed: bool
    scope: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "passed": bool(self.passed), "scope": int(self.scope)}


@dataclass(frozen=True)
class RootCauseContract:
    """One diagnosis: identity, the whole evidence record, and its state.

    Frozen; every mutation goes through `advance`/the writer helpers, which
    enforce the transition law. `root_repair` non-empty means a root repair
    has been DECLARED (described) — the claim only becomes state
    ``root_cause_repaired`` when the full validation triple holds.
    """

    diagnosis_id: str
    problem_statement: str
    state: str = SYMPTOM_OBSERVED
    symptoms: tuple[str, ...] = ()
    evidence: tuple[DiagnosisEvidence, ...] = ()
    causal_hypothesis: str = ""
    hypothesis_source: str = ""
    owning_seam: str = ""
    containment_actions: tuple[str, ...] = ()
    root_repair: str = ""
    recurrence_result: ValidationOutcome | None = None
    regression_result: ValidationOutcome | None = None
    remaining_uncertainty: str = ""
    workaround_requested: bool = False
    #: Recovery scoping (amendment gap 2): which session/workspace opened this
    #: diagnosis, so a post-restart continuation can find it without the
    #: in-process registry. Derived at open from the turn context, never set
    #: by hand on the wire (a mismatch fails validation like any other field).
    session_id: str = ""
    workspace: str = ""

    # -- derived truth ----------------------------------------------------

    def operator_status(self) -> str:
        """The compact operator status DERIVED from the state (rule 8)."""
        status = _OPERATOR_STATUS.get(self.state)
        if status is None:
            raise RootCauseContractError(
                f"unknown root-cause state {self.state!r} has no operator status"
            )
        return status

    def has_seam_evidence(self) -> bool:
        return any(item.kind == SEAM_EVIDENCE_KIND for item in self.evidence)

    def _verification_requirements_met(self) -> list[str]:
        """Missing requirements for ``root_cause_verified``, cause-named."""
        missing: list[str] = []
        if not self.causal_hypothesis.strip():
            missing.append("causal_hypothesis")
        if not self.owning_seam.strip():
            missing.append("owning_seam")
        if not self.has_seam_evidence():
            missing.append("evidence_at_owning_seam")
        if self.recurrence_result is None or not self.recurrence_result.passed:
            missing.append("recurrence_test_passed")
        return missing

    def _repair_requirements_met(self) -> list[str]:
        """Missing requirements for ``root_cause_repaired``, cause-named."""
        missing = self._verification_requirements_met()
        if self.state != ROOT_CAUSE_VERIFIED:
            # Reaching repaired requires passing through verification;
            # containment/symptom/unresolved must first verify.
            missing.append(f"verification_must_hold_first(current={self.state})")
        if not self.root_repair.strip():
            missing.append("root_repair_declared")
        if self.regression_result is None or not self.regression_result.passed:
            missing.append("cumulative_regression_passed")
        elif self._regression_is_vacuous():
            missing.append("cumulative_regression_broader_than_recurrence")
        return missing

    def _regression_is_vacuous(self) -> bool:
        """The regression entry is the recurrence test recycled, not a suite."""
        if self.regression_result is None or self.recurrence_result is None:
            return False
        same_ref = self.regression_result.ref.strip() == self.recurrence_result.ref.strip()
        single_scope = int(self.regression_result.scope or 1) < 2
        return bool(same_ref and single_scope)

    # -- egress -----------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The JSON-safe egress projection (the wire law: dicts, never objects)."""
        return {
            "diagnosis_id": self.diagnosis_id,
            "problem_statement": self.problem_statement,
            "state": self.state,
            "operator_status": self.operator_status(),
            "symptoms": list(self.symptoms),
            "evidence": [item.to_dict() for item in self.evidence],
            "causal_hypothesis": self.causal_hypothesis,
            "hypothesis_source": self.hypothesis_source,
            "owning_seam": self.owning_seam,
            "containment_actions": list(self.containment_actions),
            "root_repair": self.root_repair,
            "recurrence_result": (
                self.recurrence_result.to_dict()
                if self.recurrence_result is not None
                else None
            ),
            "regression_result": (
                self.regression_result.to_dict()
                if self.regression_result is not None
                else None
            ),
            "remaining_uncertainty": self.remaining_uncertainty,
            "workaround_requested": bool(self.workaround_requested),
            "session_id": self.session_id,
            "workspace": self.workspace,
        }


def _diagnosis_id_for(problem_key: str) -> str:
    """Stable identity per problem: retries/resumes derive the same id (rule 6)."""
    digest = hashlib.sha256(str(problem_key or "").encode("utf-8")).hexdigest()[:24]
    return f"rc:{digest}"


# --------------------------------------------------------------------------
# The turn home: context key (transport) + turn binding + registry + durable store
# --------------------------------------------------------------------------

#: diagnosis_id -> latest contract. In-process cache; the durable store below
#: is the surviving truth across restart.
_DIAGNOSIS_REGISTRY: OrderedDict[str, RootCauseContract] = OrderedDict()
_REGISTRY_CAP = 64

#: The turn-scoped diagnosis binding (amendment gap 1). Tool lanes work on
#: MERGE COPIES of the turn context, so a contract written to a copy never
#: reaches the sealing spine's own dict; the binding carries the diagnosis id
#: through the turn's call tree instead. The TURN SPINE clears it when the
#: turn ends (`clear_turn_diagnosis_binding` in run_once's finally) so a later
#: ordinary turn never inherits a repair diagnosis by adjacency (rule 5).
_TURN_DIAGNOSIS: ContextVar[str | None] = ContextVar(
    "vool_root_cause_turn_diagnosis", default=None
)


def clear_turn_diagnosis_binding() -> None:
    """The turn ended: drop the turn-scoped diagnosis binding.

    Owned by the run_once spine (its finally), exactly like the other per-turn
    resets; callers deeper in the tree never clear it mid-turn.
    """
    _TURN_DIAGNOSIS.set(None)


#: Durable persistence counters (observable, never raised through): the store
#: failing must not kill a repair turn, but it may not fail silently either.
DURABLE_WRITE_FAILURES = 0
DURABLE_READ_FAILURES = 0


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _durable_connect():
    from storage.db import get_connection

    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS root_cause_diagnoses (
            diagnosis_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL DEFAULT '',
            workspace TEXT NOT NULL DEFAULT '',
            record_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS root_cause_pending_mutations (
            session_id TEXT PRIMARY KEY,
            mutation_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def _persist(contract: RootCauseContract) -> None:
    """Record-time durability (amendment gap 2): every registry write also
    lands in the existing durable runtime store (storage.db), latest-wins per
    diagnosis id. Fail-open with a counter — a store outage degrades to the
    in-process registry, it never kills the repair turn."""
    global DURABLE_WRITE_FAILURES
    try:
        conn = _durable_connect()
        try:
            conn.execute(
                """
                INSERT INTO root_cause_diagnoses
                    (diagnosis_id, session_id, workspace, record_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(diagnosis_id) DO UPDATE SET
                    session_id=excluded.session_id,
                    workspace=excluded.workspace,
                    record_json=excluded.record_json,
                    updated_at=excluded.updated_at
                """,
                (
                    contract.diagnosis_id,
                    contract.session_id,
                    contract.workspace,
                    json.dumps(contract.to_dict(), ensure_ascii=True, default=str),
                    _utcnow(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        DURABLE_WRITE_FAILURES += 1


def _load_durable(diagnosis_id: str) -> RootCauseContract | None:
    global DURABLE_READ_FAILURES
    try:
        conn = _durable_connect()
        try:
            row = conn.execute(
                "SELECT record_json FROM root_cause_diagnoses WHERE diagnosis_id = ?",
                (str(diagnosis_id),),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        DURABLE_READ_FAILURES += 1
        return None
    if not row:
        return None
    try:
        return validate_publication(json.loads(row[0]))
    except (RootCauseContractError, ValueError, TypeError):
        # A hand-edited or corrupt durable row is refused by the same
        # wire-integrity law the live wire answers to — never trusted.
        return None


def recover_diagnosis(diagnosis_id: str) -> RootCauseContract | None:
    """Post-restart recovery: durable row -> registry (idempotent)."""
    clean = str(diagnosis_id or "").strip()
    if not clean:
        return None
    cached = _DIAGNOSIS_REGISTRY.get(clean)
    if cached is not None:
        return cached
    loaded = _load_durable(clean)
    if loaded is None:
        return None
    _DIAGNOSIS_REGISTRY[clean] = loaded
    _DIAGNOSIS_REGISTRY.move_to_end(clean)
    return loaded


def reset_diagnosis_registry_for_tests() -> None:
    _DIAGNOSIS_REGISTRY.clear()
    clear_turn_diagnosis_binding()


def _register(contract: RootCauseContract) -> RootCauseContract:
    _DIAGNOSIS_REGISTRY[contract.diagnosis_id] = contract
    _DIAGNOSIS_REGISTRY.move_to_end(contract.diagnosis_id)
    while len(_DIAGNOSIS_REGISTRY) > _REGISTRY_CAP:
        _DIAGNOSIS_REGISTRY.popitem(last=False)
    _persist(contract)
    return contract


def current_contract(source_context: Any) -> RootCauseContract | None:
    """The latest contract for THIS turn, or None.

    Resolution order: the context key (turn transport) -> the turn-scoped
    binding (loop-context copies) -> nothing. Both carry only the diagnosis
    ID; the registry/durable store holds the truth, so a stale context copy
    still resolves the latest state. Never invents a diagnosis for a context
    that carries none (rule 5).
    """
    riding = None
    if isinstance(source_context, dict):
        riding = source_context.get(ROOT_CAUSE_CONTRACT_KEY)
    diagnosis_id = ""
    if isinstance(riding, RootCauseContract):
        diagnosis_id = riding.diagnosis_id
    elif isinstance(riding, str) and riding.startswith("rc:"):
        diagnosis_id = riding
    if not diagnosis_id:
        bound = _TURN_DIAGNOSIS.get()
        if bound:
            diagnosis_id = str(bound)
    if not diagnosis_id:
        return None
    latest = _DIAGNOSIS_REGISTRY.get(diagnosis_id)
    if latest is not None:
        return latest
    return recover_diagnosis(diagnosis_id)


def _write(source_context: dict[str, Any] | None, contract: RootCauseContract) -> RootCauseContract:
    """Publish through the ONE home: durable + registry latest, with the
    context pointer and the turn binding beside it."""
    _register(contract)
    if isinstance(source_context, dict):
        source_context[ROOT_CAUSE_CONTRACT_KEY] = contract
    with contextlib.suppress(ValueError):
        _TURN_DIAGNOSIS.set(contract.diagnosis_id)
    return contract


def open_root_cause_scope(
    source_context: dict[str, Any] | None,
    *,
    problem_key: str,
    problem_statement: str,
    owning_seam: str = "",
    symptoms: tuple[str, ...] | list[str] = (),
) -> RootCauseContract:
    """Open (or resume) the diagnosis for this problem — idempotent per problem.

    Same problem_key resumes the SAME diagnosis with all prior evidence intact
    (rule 6) — from the registry, or post-restart from the durable store; a
    different problem is a different id.
    """
    diagnosis_id = _diagnosis_id_for(problem_key)
    ctx = source_context if isinstance(source_context, dict) else {}
    existing = _DIAGNOSIS_REGISTRY.get(diagnosis_id) or _load_durable(diagnosis_id)
    if existing is not None:
        return _write(ctx, existing)
    return _write(
        ctx,
        RootCauseContract(
            diagnosis_id=diagnosis_id,
            problem_statement=str(problem_statement or "").strip(),
            owning_seam=str(owning_seam or "").strip(),
            symptoms=tuple(str(item or "").strip() for item in symptoms if str(item or "").strip()),
            session_id=str(ctx.get("runtime_session_id") or ctx.get("session_id") or "").strip(),
            workspace=str(ctx.get("workspace") or ctx.get("workspace_root") or "").strip(),
        ),
    )


def _contract_for_write(
    source_context: dict[str, Any] | None, *, problem_key: str | None = None
) -> RootCauseContract:
    """The contract a writer targets: the turn's own, or an explicit resume.

    `problem_key` resumes by problem key OR by an exact ``rc:`` diagnosis id
    (a follow-up turn holding the id from the turn that opened the diagnosis).
    """
    contract = current_contract(source_context)
    if contract is not None:
        return contract
    if problem_key:
        key = str(problem_key).strip()
        if key.startswith("rc:"):
            resumed = _DIAGNOSIS_REGISTRY.get(key)
        else:
            resumed = _DIAGNOSIS_REGISTRY.get(_diagnosis_id_for(key))
        if resumed is not None:
            return resumed
    raise RootCauseStateError(
        "no root-cause contract is open for this turn (open_root_cause_scope first)"
    )


# --------------------------------------------------------------------------
# Writers — the only sanctioned mutations (all through `advance` where lawful)
# --------------------------------------------------------------------------


def record_symptom(
    source_context: dict[str, Any] | None, symptom: str, *, evidence_ref: str = ""
) -> RootCauseContract:
    contract = _contract_for_write(source_context)
    symptom_text = str(symptom or "").strip()
    symptoms = tuple(contract.symptoms) + (
        (symptom_text,) if symptom_text and symptom_text not in contract.symptoms else ()
    )
    evidence = tuple(contract.evidence)
    if evidence_ref.strip():
        evidence += (
            DiagnosisEvidence(
                kind="symptom", ref=str(evidence_ref).strip(), detail=symptom_text
            ),
        )
    return _write(source_context, replace(contract, symptoms=symptoms, evidence=evidence))


def record_evidence(
    source_context: dict[str, Any] | None, *, kind: str, ref: str, detail: str = ""
) -> RootCauseContract:
    contract = _contract_for_write(source_context)
    entry = DiagnosisEvidence(kind=str(kind or "").strip(), ref=str(ref or "").strip(), detail=str(detail or ""))
    if not entry.kind or not entry.ref:
        raise RootCauseStateError("evidence requires both a kind and a stable ref")
    return _write(
        source_context,
        replace(contract, evidence=(*contract.evidence, entry)),
    )


def record_seam_evidence(
    source_context: dict[str, Any] | None, *, ref: str, detail: str = ""
) -> RootCauseContract:
    """Evidence AT THE OWNING SEAM — the first third of the completion gate."""
    return record_evidence(source_context, kind=SEAM_EVIDENCE_KIND, ref=ref, detail=detail)


def set_hypothesis(
    source_context: dict[str, Any] | None, hypothesis: str, *, source: str = ""
) -> RootCauseContract:
    """Record the causal claim, attributed to WHO claims it (rule 4 honesty).

    Recording a hypothesis IS the transition into ``root_cause_hypothesis``
    when the diagnosis is still below it — the state means exactly "a causal
    claim exists". A diagnosis already verified/repaired keeps its state (a
    refined hypothesis does not unverify what evidence confirmed).
    """
    contract = _contract_for_write(source_context)
    text = str(hypothesis or "").strip()
    if not text:
        raise RootCauseStateError("a causal hypothesis cannot be empty")
    updated = replace(contract, causal_hypothesis=text, hypothesis_source=str(source or "").strip())
    if updated.state in {SYMPTOM_OBSERVED, CONTAINMENT_ONLY, UNRESOLVED}:
        updated = replace(updated, state=ROOT_CAUSE_HYPOTHESIS)
    return _write(source_context, updated)


def record_containment(
    source_context: dict[str, Any] | None, action: str
) -> RootCauseContract:
    contract = _contract_for_write(source_context)
    action_text = str(action or "").strip()
    if not action_text:
        raise RootCauseStateError("a containment action cannot be empty")
    return _write(
        source_context,
        replace(
            contract,
            containment_actions=(*contract.containment_actions, action_text),
        ),
    )


def declare_root_repair(
    source_context: dict[str, Any] | None,
    description: str,
    *,
    problem_key: str | None = None,
) -> RootCauseContract:
    """DECLARE the root repair (the claim). State only becomes repaired when
    `advance` admits it with the full validation triple — declaring alone
    never completes anything (rule 1)."""
    contract = _contract_for_write(source_context, problem_key=problem_key)
    text = str(description or "").strip()
    if not text:
        raise RootCauseStateError("a declared root repair cannot be empty")
    return _write(source_context, replace(contract, root_repair=text))


def record_recurrence_result(
    source_context: dict[str, Any] | None,
    *,
    ref: str,
    passed: bool,
    scope: int = 1,
    problem_key: str | None = None,
) -> RootCauseContract:
    contract = _contract_for_write(source_context, problem_key=problem_key)
    return _write(
        source_context,
        replace(
            contract,
            recurrence_result=ValidationOutcome(
                ref=str(ref or "").strip(), passed=bool(passed), scope=int(scope or 1)
            ),
        ),
    )


def record_regression_result(
    source_context: dict[str, Any] | None,
    *,
    ref: str,
    passed: bool,
    scope: int = 1,
    problem_key: str | None = None,
) -> RootCauseContract:
    contract = _contract_for_write(source_context, problem_key=problem_key)
    return _write(
        source_context,
        replace(
            contract,
            regression_result=ValidationOutcome(
                ref=str(ref or "").strip(), passed=bool(passed), scope=int(scope or 1)
            ),
        ),
    )


def set_remaining_uncertainty(
    source_context: dict[str, Any] | None, text: str
) -> RootCauseContract:
    contract = _contract_for_write(source_context)
    return _write(
        source_context, replace(contract, remaining_uncertainty=str(text or "").strip())
    )


def mark_workaround_requested(
    source_context: dict[str, Any] | None, *, evidence_ref: str
) -> RootCauseContract:
    """The user EXPLICITLY requested a temporary workaround (rule 3). The
    reference records where that request is visible — an assertion, not prose
    matching downstream."""
    contract = _contract_for_write(source_context)
    if not str(evidence_ref or "").strip():
        raise RootCauseStateError("workaround request requires its evidence reference")
    return _write(source_context, replace(contract, workaround_requested=True))


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def advance_state(
    source_context: dict[str, Any] | None,
    target_state: str,
    *,
    problem_key: str | None = None,
) -> RootCauseContract:
    """The one transition door. Lawful transitions only; entry requirements
    for verified/repaired enforced HERE (rules 1, 2, 4)."""
    contract = _contract_for_write(source_context, problem_key=problem_key)
    target = str(target_state or "").strip()
    if target not in ROOT_CAUSE_STATES:
        raise RootCauseStateError(f"unknown root-cause state {target_state!r}")
    if target == contract.state:
        return _write(source_context, contract)
    if target == ROOT_CAUSE_REPAIRED:
        # Checked BEFORE the structural transition map so the refusal always
        # names the missing validation, never only "unlawful transition" —
        # a symptom patch refused as "recurrence_test_passed missing" teaches;
        # the same refusal as a set-membership error does not.
        missing = contract._repair_requirements_met()
        if missing:
            raise RootCauseStateError(
                "repair completion refused — missing: " + ", ".join(missing)
            )
    allowed = _LAWFUL_TRANSITIONS.get(contract.state, frozenset())
    if target not in allowed:
        raise RootCauseStateError(
            f"unlawful root-cause transition {contract.state!r} -> {target!r} "
            f"(allowed: {sorted(allowed)})"
        )
    if target == ROOT_CAUSE_VERIFIED:
        missing = contract._verification_requirements_met()
        if missing:
            raise RootCauseStateError(
                "verification refused — missing: " + ", ".join(missing)
            )
    if target == CONTAINMENT_ONLY and not contract.containment_actions:
        raise RootCauseStateError(
            "containment requires at least one recorded containment action"
        )
    return _write(source_context, replace(contract, state=target))


# --------------------------------------------------------------------------
# Publication + wire integrity
# --------------------------------------------------------------------------


def publication_for(contract: RootCauseContract) -> dict[str, Any]:
    """The finalizer's stamp: state, derived status, identity, full record."""
    return {
        "state": contract.state,
        "operator_status": contract.operator_status(),
        "diagnosis_id": contract.diagnosis_id,
        "contract": contract.to_dict(),
    }


def _outcome_from_dict(raw: Any) -> ValidationOutcome | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RootCauseContractError(f"validation outcome must be a dict, got {type(raw).__name__}")
    ref = str(raw.get("ref") or "").strip()
    if not ref:
        raise RootCauseContractError("validation outcome requires a ref")
    return ValidationOutcome(
        ref=ref,
        passed=bool(raw.get("passed")),
        scope=int(raw.get("scope") or 1),
    )


def validate_publication(record: Any) -> RootCauseContract:
    """Reconstruct the typed contract from a published dict — fail closed.

    This is the wire-integrity half of the gate (the same law as
    `_validated_conductor_outcome`): a record claiming a state it does not
    carry the evidence for is refused, never repaired by guessing. Unknown
    states, unknown shapes, and states whose requirements the record itself
    contradicts all raise `RootCauseContractError`.
    """
    if not isinstance(record, dict):
        raise RootCauseContractError(
            f"root-cause publication must be a dict, got {type(record).__name__}"
        )
    state = str(record.get("state") or "").strip()
    if state not in ROOT_CAUSE_STATES:
        raise RootCauseContractError(f"unknown root-cause state {state!r} on the wire")
    declared_status = str(record.get("operator_status") or "").strip()
    if declared_status and declared_status != _OPERATOR_STATUS[state]:
        raise RootCauseContractError(
            f"operator_status {declared_status!r} contradicts state {state!r}"
        )
    evidence = tuple(
        DiagnosisEvidence(
            kind=str(item.get("kind") or ""),
            ref=str(item.get("ref") or ""),
            detail=str(item.get("detail") or ""),
        )
        for item in list(record.get("evidence") or [])
        if isinstance(item, dict)
    )
    try:
        contract = RootCauseContract(
            diagnosis_id=str(record.get("diagnosis_id") or "").strip(),
            problem_statement=str(record.get("problem_statement") or "").strip(),
            state=state,
            symptoms=tuple(str(item) for item in list(record.get("symptoms") or [])),
            evidence=evidence,
            causal_hypothesis=str(record.get("causal_hypothesis") or "").strip(),
            hypothesis_source=str(record.get("hypothesis_source") or "").strip(),
            owning_seam=str(record.get("owning_seam") or "").strip(),
            containment_actions=tuple(
                str(item) for item in list(record.get("containment_actions") or [])
            ),
            root_repair=str(record.get("root_repair") or "").strip(),
            recurrence_result=_outcome_from_dict(record.get("recurrence_result")),
            regression_result=_outcome_from_dict(record.get("regression_result")),
            remaining_uncertainty=str(record.get("remaining_uncertainty") or "").strip(),
            workaround_requested=bool(record.get("workaround_requested")),
            session_id=str(record.get("session_id") or "").strip(),
            workspace=str(record.get("workspace") or "").strip(),
        )
    except (TypeError, ValueError) as exc:
        raise RootCauseContractError(f"malformed root-cause record: {exc}") from exc
    if not contract.diagnosis_id:
        raise RootCauseContractError("root-cause record carries no diagnosis_id")
    # The state's own entry requirements re-checked against the record itself:
    # a wire claiming verified/repaired without the evidence that state means
    # is a forged completion — refused (rule 1 at the wire).
    if state == ROOT_CAUSE_VERIFIED and contract._verification_requirements_met():
        raise RootCauseContractError(
            "record claims root_cause_verified but is missing: "
            + ", ".join(contract._verification_requirements_met())
        )
    if state == ROOT_CAUSE_REPAIRED and contract._repair_requirements_met():
        raise RootCauseContractError(
            "record claims root_cause_repaired but is missing: "
            + ", ".join(contract._repair_requirements_met())
        )
    return contract


# --------------------------------------------------------------------------
# The approval-continuation writer (amendment gap 1)
# --------------------------------------------------------------------------

#: The intents this lane treats as a repair mutation / a validation run.
_MUTATION_INTENTS = frozenset(
    {
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
        "workspace.write_file",
    }
)
_VALIDATION_INTENTS = frozenset(
    {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter"}
)

_PENDING_MUTATION_KEY = "root_cause_pending_mutation"


def _validation_identity(intent: str, arguments: dict[str, Any] | None) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    return str(args.get("command") or "").strip() or str(intent or "").strip()


def _mutation_description(intent: str, arguments: dict[str, Any]) -> str:
    path = str(arguments.get("path") or "").strip()
    old_text = str(arguments.get("old_text") or "").strip()
    new_text = str(arguments.get("new_text") or "").strip()
    if old_text or new_text:
        return f"{intent} at {path or 'workspace'}: `{old_text}` -> `{new_text}`"
    return f"{intent} at {path or 'workspace'}"


def _store_pending_mutation(session_id: str, fact: dict[str, Any]) -> None:
    global DURABLE_WRITE_FAILURES
    if not session_id:
        return
    try:
        conn = _durable_connect()
        try:
            conn.execute(
                """
                INSERT INTO root_cause_pending_mutations (session_id, mutation_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    mutation_json=excluded.mutation_json,
                    updated_at=excluded.updated_at
                """,
                (session_id, json.dumps(fact, ensure_ascii=True, default=str), _utcnow()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        DURABLE_WRITE_FAILURES += 1


def _take_pending_mutation(session_id: str) -> dict[str, Any] | None:
    global DURABLE_READ_FAILURES
    if not session_id:
        return None
    try:
        conn = _durable_connect()
        try:
            row = conn.execute(
                "SELECT mutation_json FROM root_cause_pending_mutations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM root_cause_pending_mutations WHERE session_id = ?",
                    (session_id,),
                )
                conn.commit()
        finally:
            conn.close()
    except Exception:
        DURABLE_READ_FAILURES += 1
        return None
    if not row:
        return None
    try:
        fact = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return fact if isinstance(fact, dict) else None


def _open_from_mutation_fact(
    ctx: dict[str, Any], fact: dict[str, Any]
) -> RootCauseContract:
    """Open the durable diagnosis an approved mutation defines (or resume it)."""
    intent = str(fact.get("intent") or "")
    arguments = dict(fact.get("arguments") or {})
    path = str(arguments.get("path") or "").strip()
    old_text = str(arguments.get("old_text") or "").strip()
    new_text = str(arguments.get("new_text") or "").strip()
    problem_key = "approved-repair|" + "|".join(
        (intent, path, old_text[:200], new_text[:200])
    )
    description = _mutation_description(intent, arguments)
    contract = open_root_cause_scope(
        ctx,
        problem_key=problem_key,
        problem_statement=f"approved workspace repair: {description}",
        owning_seam=path or "workspace unified diff",
    )
    if not contract.causal_hypothesis:
        set_hypothesis(
            ctx,
            f"user-approved change: {description}",
            source="user_declared",
        )
    record_seam_evidence(
        ctx, ref=f"{intent}:{path or 'workspace'}", detail=description
    )
    latest = current_contract(ctx)
    if latest is not None and not latest.root_repair:
        declare_root_repair(ctx, description)
    return current_contract(ctx) or contract


def _record_validation_outcome(
    ctx: dict[str, Any],
    contract: RootCauseContract,
    identity: str,
    ok: bool,
) -> RootCauseContract:
    """Record one validation outcome as recurrence or DISTINCT regression.

    First distinct identity on a diagnosis is the recurrence claim; a
    DIFFERENT identity is cumulative-regression evidence (the gate's
    anti-vacuous check refuses the same identity recycled). Failures are
    recorded faithfully — a red suite must be able to keep the diagnosis at
    verified (rule 2).
    """
    recurrence = contract.recurrence_result
    if recurrence is None or recurrence.ref == identity:
        record_recurrence_result(ctx, ref=identity, passed=ok)
    else:
        record_regression_result(ctx, ref=identity, passed=ok)
    _advance_as_far_as_lawful(ctx)
    # The CURRENT truth, never the stale pre-advance local: the writers below
    # rebinding are what the state machine acted on.
    return current_contract(ctx) or contract


def _advance_as_far_as_lawful(ctx: dict[str, Any]) -> None:
    """Mechanical state advances the recorded evidence licenses — no further.

    A refusal is information (the evidence does not support the state yet),
    never an error the repair turn dies for."""
    with contextlib.suppress(RootCauseStateError):
        advance_state(ctx, ROOT_CAUSE_VERIFIED)
    with contextlib.suppress(RootCauseStateError):
        advance_state(ctx, ROOT_CAUSE_REPAIRED)


def _batch_carries_validation(ctx: dict[str, Any]) -> bool:
    """Whether the turn's CONCRETE plan (the controller's pending batch, or
    the restored checkpoint batch) also runs a validation intent.

    Repair shape from payloads, never prose: a gated mutation whose own plan
    includes a validation run is a repair; a gated edit alone is not."""
    try:
        from core.mode_permission_policy import PENDING_BATCH_CALLS_KEY
    except Exception:
        PENDING_BATCH_CALLS_KEY = "pending_batch_calls"
    batch = ctx.get(PENDING_BATCH_CALLS_KEY)
    if not isinstance(batch, list):
        return False
    return any(
        isinstance(item, dict) and str(item.get("intent") or "").strip() in _VALIDATION_INTENTS
        for item in batch
    )


def note_gated_repair(
    source_context: dict[str, Any] | None,
    *,
    intent: str,
    arguments: dict[str, Any] | None,
) -> RootCauseContract | None:
    """Open the durable diagnosis BEFORE an approved repair executes.

    Called at the permission gate the moment a workspace mutation is held for
    approval, when the turn's own concrete plan also carries a validation run
    (repair shape). The mutation has NOT executed yet — the diagnosis opens on
    the repair's declared content, so the approved execution, a restart, and a
    later continuation all bind to the same identity. Ordinary gated edits
    (no validation anywhere in the plan) open nothing (rule 5).
    """
    normalized = str(intent or "").strip()
    if normalized not in _MUTATION_INTENTS:
        return None
    ctx = source_context if isinstance(source_context, dict) else {}
    contract = current_contract(ctx)
    if contract is not None:
        return contract
    if not _batch_carries_validation(ctx):
        return None
    args = arguments if isinstance(arguments, dict) else {}
    contract = _open_from_mutation_fact(
        ctx, {"intent": normalized, "arguments": dict(args)}
    )
    # The plan's validation identities ride the durable record as evidence, so
    # a continuation in a NEW process (no binding, no stash) can find this
    # diagnosis BY the identity it runs — identity-matched recovery, never
    # session adjacency alone.
    try:
        from core.mode_permission_policy import PENDING_BATCH_CALLS_KEY
    except Exception:
        PENDING_BATCH_CALLS_KEY = "pending_batch_calls"
    for item in list(ctx.get(PENDING_BATCH_CALLS_KEY) or []):
        if not isinstance(item, dict):
            continue
        item_intent = str(item.get("intent") or "").strip()
        if item_intent in _VALIDATION_INTENTS:
            record_evidence(
                ctx,
                kind="planned_validation",
                ref=_validation_identity(item_intent, dict(item.get("arguments") or {})),
                detail="validation planned in the approved repair batch",
            )
    return current_contract(ctx) or contract


def recover_for_validation(
    session_id: str, identity: str
) -> RootCauseContract | None:
    """Recover the durable diagnosis whose plan named THIS validation identity.

    Identity-matched, session-scoped, newest first — the post-restart binding
    for a diagnosis opened at the gate (whose planned identities ride its
    durable evidence). Never binds by session adjacency alone."""
    clean_session = str(session_id or "").strip()
    clean_identity = str(identity or "").strip()
    if not clean_session or not clean_identity:
        return None
    global DURABLE_READ_FAILURES
    try:
        conn = _durable_connect()
        try:
            rows = conn.execute(
                """
                SELECT diagnosis_id, record_json FROM root_cause_diagnoses
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT 32
                """,
                (clean_session,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        DURABLE_READ_FAILURES += 1
        return None
    for diagnosis_id, record_json in rows:
        try:
            record = json.loads(record_json)
        except (ValueError, TypeError):
            continue
        evidence = [
            dict(item) for item in list(record.get("evidence") or []) if isinstance(item, dict)
        ]
        refs = {
            str(item.get("ref") or "").strip()
            for item in evidence
            if item.get("kind") in {"planned_validation", "validation"}
        }
        refs.add(str((record.get("recurrence_result") or {}).get("ref") or "").strip())
        if clean_identity in refs:
            return recover_diagnosis(str(diagnosis_id))
    return None


def record_repair_step(
    source_context: dict[str, Any] | None,
    *,
    intent: str,
    arguments: dict[str, Any] | None,
    ok: bool,
) -> RootCauseContract | None:
    """THE approval-continuation writer: one executed tool step, recorded.

    Called from the tool-intent executor seam after every dispatch. Repair
    shape is decided by CONCRETE intents, never prose (rule 5):

    * an APPROVAL-RESUMED mutation that ran is stashed durably for the
      session; it opens the diagnosis the moment a validation confirms the
      repair shape (this turn, or a later turn after a restart);
    * a validation binds to the turn's open diagnosis, or recovers the
      durable one (by turn binding, or by the session's pending mutation),
      and records recurrence / distinct regression with its real outcome.

    Ordinary traffic returns None and touches nothing: a non-approval edit,
    a validation with no diagnosis and no pending repair mutation, and every
    other intent are not this contract's business.
    """
    normalized = str(intent or "").strip()
    args = arguments if isinstance(arguments, dict) else {}
    ctx = source_context if isinstance(source_context, dict) else {}
    if normalized in _MUTATION_INTENTS:
        if not ok:
            return current_contract(ctx)
        if not str(ctx.get("mode_approval_token") or "").strip():
            # Not an approval continuation: the orchestrated lane owns its own
            # recording, and an ordinary edit is not a repair diagnosis.
            return current_contract(ctx)
        session_id = str(
            ctx.get("runtime_session_id") or ctx.get("session_id") or ""
        ).strip()
        contract = current_contract(ctx)
        if contract is None and _batch_carries_validation(ctx):
            # The plan itself is repair-shaped (mutation + validation): the
            # diagnosis exists before evidence, exactly as the gate note
            # would have opened it.
            contract = _open_from_mutation_fact(
                ctx, {"intent": normalized, "arguments": dict(args)}
            )
        if contract is not None:
            description = _mutation_description(normalized, args)
            record_seam_evidence(
                ctx, ref=f"{normalized}:{args.get('path') or ''}", detail=description
            )
            if not contract.root_repair:
                declare_root_repair(ctx, description)
            _advance_as_far_as_lawful(ctx)
            return current_contract(ctx)
        # No diagnosis yet: stash the approved mutation durably for the
        # session; the validation that confirms repair shape binds it.
        _store_pending_mutation(
            session_id,
            {"intent": normalized, "arguments": dict(args)},
        )
        return None
    if normalized in _VALIDATION_INTENTS:
        identity = _validation_identity(normalized, args)
        contract = current_contract(ctx)
        if contract is None:
            session_id = str(
                ctx.get("runtime_session_id") or ctx.get("session_id") or ""
            ).strip()
            # Recovery order: the session's stashed approved mutation (the
            # cross-turn flow), then identity-matched durable recovery (the
            # gate-opened flow, e.g. after a restart). None means an ordinary
            # test run — untouched (rule 5).
            fact = _take_pending_mutation(session_id)
            if fact is not None:
                contract = _open_from_mutation_fact(ctx, fact)
                record_evidence(
                    ctx,
                    kind="user_reported_failure",
                    ref=identity,
                    detail=(
                        "failure state reported by the user; the runtime captured "
                        "no pre-repair run of this identity"
                    ),
                )
            else:
                contract = recover_for_validation(session_id, identity)
                if contract is not None:
                    _write(ctx, contract)
        if contract is None:
            return None
        return _record_validation_outcome(ctx, contract, identity, bool(ok))
    return None


# --------------------------------------------------------------------------
# The orchestrated-repair recorder (production writer for the repair lane)
# --------------------------------------------------------------------------


def record_envelope_outcome(
    source_context: dict[str, Any] | None,
    *,
    envelope_arguments: dict[str, Any],
    envelope_result: Any,
) -> RootCauseContract | None:
    """Record what a repair envelope ACTUALLY produced — nothing invented.

    Reads the queen payload's subtask structure and the executor's child
    results (receipts included) and lands them as typed contract facts:

    * a preflight verifier whose validation FAILED (the failing-test capture)
      records the symptom and pins the recurrence identity;
    * coder tool receipts at the change site record seam evidence and the
      applied change is DECLARED as the root repair (attributed to the user
      whose request planned it);
    * the final verifier's green validation — when the same identity failed
      at preflight — records the passed recurrence test;
    * a `regression-verify-` child's green validation on a DISTINCT identity
      (amendment gap 3: the planner schedules it when the request names a
      broader suite) records cumulative-regression evidence;
    * the diagnosis mechanically advances as far as the recorded evidence
      licenses — verified, then repaired when the full triple holds.
    """
    contract = current_contract(source_context)
    if contract is None:
        return None
    ctx = source_context if isinstance(source_context, dict) else {}

    envelope = dict(envelope_arguments or {}).get("task_envelope")
    envelope = envelope if isinstance(envelope, dict) else {}
    subtasks = [dict(item) for item in list(dict(envelope.get("inputs") or {}).get("subtasks") or []) if isinstance(item, dict)]
    validation_steps: dict[str, dict[str, Any]] = {}
    for child in subtasks:
        child_id = str(child.get("task_id") or "")
        for step in list(dict(child.get("inputs") or {}).get("runtime_tools") or []):
            if isinstance(step, dict) and str(step.get("intent") or "").startswith("workspace.run_"):
                arguments = dict(step.get("arguments") or {})
                identity = str(arguments.get("command") or step.get("intent") or "").strip()
                if identity:
                    validation_steps[child_id] = {"identity": identity}

    details = getattr(envelope_result, "details", None)
    details = details if isinstance(details, dict) else {}
    child_results = [
        dict(item) for item in list(details.get("child_results") or []) if isinstance(item, dict)
    ]

    preflight_failure_identity = ""
    final_pass_identity = ""
    regression_pass_identity = ""
    regression_passed: bool | None = None
    seam_refs: list[str] = []

    for child in child_results:
        child_id = str(child.get("task_id") or "")
        role = str(child.get("role") or "")
        receipts = [dict(item) for item in list(child.get("receipts") or []) if isinstance(item, dict)]
        for receipt in receipts:
            kind = str(receipt.get("receipt_type") or "")
            if kind == "validation_result":
                identity = validation_steps.get(child_id, {}).get("identity", "")
                passed = bool(receipt.get("ok"))
                is_preflight = child_id.startswith("preflight-verify-")
                is_regression = child_id.startswith("regression-verify-")
                if is_preflight and not passed:
                    preflight_failure_identity = identity or preflight_failure_identity
                elif is_regression:
                    # The distinct broader-suite child: its outcome IS the
                    # cumulative-regression evidence, green or red.
                    regression_pass_identity = identity or regression_pass_identity
                    regression_passed = passed
                elif not is_preflight and passed and role == "verifier":
                    final_pass_identity = identity or final_pass_identity
            elif kind == "tool_receipt" and role == "coder":
                step_id = str(receipt.get("step_id") or "")
                if step_id.startswith(("apply-patch", "apply-replacement")):
                    seam_refs.append(f"{child_id}:{step_id}")

    if preflight_failure_identity:
        record_symptom(
            ctx,
            f"validation failed before repair: {preflight_failure_identity}",
            evidence_ref=preflight_failure_identity,
        )
    for ref in seam_refs:
        record_seam_evidence(ctx, ref=ref, detail="change applied at the owning seam")
    contract_now = current_contract(ctx)
    if contract_now is not None and seam_refs and not contract_now.root_repair:
        # The applied change IS the declared root repair — the user's request
        # planned it (hypothesis_source already says user_declared); declaring
        # it here is a fact about what ran, not an invented causal claim.
        declare_root_repair(
            ctx,
            f"workspace change applied at the owning seam: {contract_now.owning_seam or 'unified diff'}",
        )
    if (
        final_pass_identity
        and preflight_failure_identity
        and final_pass_identity == preflight_failure_identity
    ):
        # Recurrence is a claim about ONE identity: the validation that
        # reproduced the failure before the repair passes after it. A green
        # check on a DIFFERENT identity is evidence, not recurrence.
        record_recurrence_result(
            ctx,
            ref=final_pass_identity,
            passed=True,
            scope=1,
        )
        # Mechanical advance attempt: lawful only when hypothesis + seam
        # evidence + recurrence all hold; a refusal here is information (the
        # lane records what it has), not an error the turn dies for.
        contract_now = current_contract(ctx)
        if contract_now is not None:
            with contextlib.suppress(RootCauseStateError):
                advance_state(ctx, ROOT_CAUSE_VERIFIED)
    elif final_pass_identity:
        record_evidence(
            ctx,
            kind="validation",
            ref=final_pass_identity,
            detail="validation green after repair (no matching preflight capture)",
        )
    if regression_pass_identity:
        # Amendment gap 3: the distinct suite identity reaches the gate as
        # cumulative-regression evidence — its REAL outcome, so a red suite
        # can hold the diagnosis at verified (rule 2).
        record_regression_result(
            ctx,
            ref=regression_pass_identity,
            passed=bool(regression_passed),
            scope=1,
        )
        with contextlib.suppress(RootCauseStateError):
            advance_state(ctx, ROOT_CAUSE_REPAIRED)
    return current_contract(ctx)


__all__ = [
    "CONTAINMENT_ONLY",
    "ROOT_CAUSE_CONTRACT_KEY",
    "ROOT_CAUSE_HYPOTHESIS",
    "ROOT_CAUSE_REPAIRED",
    "ROOT_CAUSE_STATES",
    "ROOT_CAUSE_VERIFIED",
    "SEAM_EVIDENCE_KIND",
    "SYMPTOM_OBSERVED",
    "UNRESOLVED",
    "DiagnosisEvidence",
    "RootCauseContract",
    "RootCauseContractError",
    "RootCauseStateError",
    "ValidationOutcome",
    "advance_state",
    "clear_turn_diagnosis_binding",
    "current_contract",
    "declare_root_repair",
    "mark_workaround_requested",
    "open_root_cause_scope",
    "publication_for",
    "record_containment",
    "record_envelope_outcome",
    "record_evidence",
    "record_recurrence_result",
    "record_regression_result",
    "record_repair_step",
    "record_seam_evidence",
    "record_symptom",
    "recover_diagnosis",
    "set_hypothesis",
    "set_remaining_uncertainty",
    "validate_publication",
]
