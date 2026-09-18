"""The typed vocabulary of semantic routing. Types only -- there is no resolver in this phase.

Nothing here calls a model, and nothing here is authoritative over a routing decision yet. This
module exists so that the contract the future resolver must satisfy is written down, testable, and
pinned by sabotage tests BEFORE any behaviour depends on it. A contract added after the mechanism
is a description of whatever got built; a contract added before it is a constraint.

The shape of the pipeline these types describe:

    user text
      -> CERTAIN formal recognizer  (zero model calls; Certain | Abstain, never CHAT)
      -> or IntentProposal          (a model proposes structure -- Phase 1)
      -> ValidatedIntent            (the runtime validates against the registry)
      -> AdmissionResult            (the runtime decides permission, every time)
      -> execution / CONDUCTOR
      -> receipts

Four invariants are enforced by these types rather than by anyone remembering them:

1. **There is no CHAT anywhere in this module.** Chat is a lane the runtime may choose; it is not
   a semantic verdict, and above all it is not what a recognizer produces when it fails to
   recognize. A lexical miss that certifies CHAT is the defect this whole architecture exists to
   remove, so `ResolutionState` simply has no member it could be spelled with.
2. **A proposal cannot carry authority.** `IntentProposal` has no side-effect class, no permission
   field, no approval requirement. Those come from the registry, which the model does not write.
   `ValidatedIntent.side_effect_class` is copied from the contract, never from the proposal.
3. **Admission always re-enters the real permission policy.** `AdmissionResult.admitted` is not a
   caller-set field or mutable-registry membership; it is bound to the exact completed production
   `admit` frame that created that exact result after policy and atomic consume.
4. **Nothing disappears.** A clause that cannot be served becomes a REJECTED admission with a typed
   reason, and `ResolutionOutcome` refuses to exist unless it accounts for every clause it was
   handed.
"""
from __future__ import annotations

import hashlib
import struct
import sys
import threading
import weakref
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError, dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from core.semantic.canonical_text import CanonicalText, EntitySpan


class ResolutionState(str, Enum):
    """What resolution concluded about a turn.

    Deliberately **without** a CHAT member. "The recognizer did not recognize this" is `ABSTAIN`,
    which says nothing about what should happen next -- the runtime decides that. Collapsing the two
    is how a missing keyword becomes a confident "this is just conversation".
    """

    #: A formal recognizer matched with certainty. Zero model calls by construction.
    CERTAIN = "certain"
    #: The formal recognizer declined. NOT a verdict about the turn -- only about the recognizer.
    ABSTAIN = "abstain"
    #: A semantic proposal exists and has not yet been validated. Phase 1.
    PROPOSED = "proposed"
    #: Several readings survive. Each candidate must pass permission independently (see
    #: `mode_permission_policy.decide_tool_call`), so this is not a licence to run both.
    AMBIGUOUS = "ambiguous"
    #: The request has a shape this phase does not attempt. Stated, not a failure.
    UNSUPPORTED = "unsupported"
    #: Resolution was attempted and produced nothing usable.
    UNRESOLVED = "unresolved"


#: The only two states a CERTAIN recognizer may return. Pinned as data so a test can assert the set
#: rather than trusting a docstring, and so widening it is a visible diff.
CERTAIN_RECOGNIZER_STATES = frozenset({ResolutionState.CERTAIN, ResolutionState.ABSTAIN})


class RequestShape(str, Enum):
    """The structural shape of the request -- the receipt's `shape` slot.

    Recorded from Phase 0 so the corpus can report how often the out-of-scope shapes actually occur
    before anyone designs for them. Guessing that distribution is how a rare shape gets an elaborate
    mechanism and a common one gets none.
    """

    SINGLE = "single"
    MULTI_CLAUSE = "multi_clause"
    #: "if X then Y" -- branching on a condition the runtime would have to evaluate first.
    CONDITIONAL = "conditional"
    #: "no wait, make that Tallinn" -- a correction whose target is earlier in the same turn.
    MID_TURN_CORRECTION = "mid_turn_correction"
    #: "do that for the other one too" -- an entity that only exists in an earlier turn.
    CROSS_TURN_REFERENCE = "cross_turn_reference"
    #: Not classified. The correct default; never a synonym for SINGLE.
    UNKNOWN = "unknown"


#: Shapes explicitly out of scope. Represented, never silently treated as SINGLE and answered
#: partially -- a conditional request answered as if unconditional is a wrong answer, not a partial.
OUT_OF_SCOPE_SHAPES = frozenset(
    {
        RequestShape.CONDITIONAL,
        RequestShape.MID_TURN_CORRECTION,
        RequestShape.CROSS_TURN_REFERENCE,
    }
)


class ReasonCode(str, Enum):
    """Why admission concluded what it concluded. Every rejection has one; none of them is CHAT."""

    ADMITTED = "admitted"
    #: The proposal did not parse, or violated its own declared shape.
    MALFORMED_PROPOSAL = "malformed_proposal"
    #: No registered operation serves this name. Registration is the registry's word, not the model's.
    UNKNOWN_OPERATION = "unknown_operation"
    #: A span did not bind to the declared canonical representation.
    BAD_SPAN = "bad_span"
    #: A dependency index/id that does not exist, is self-referential, or is not yet resolved.
    INVALID_DEPENDENCY = "invalid_dependency"
    #: A dependency that exists and was REFUSED. Distinct from INVALID_DEPENDENCY, which is about a
    #: reference that never made sense; this one is about a reference that made sense and failed.
    #: Separated because a dependent admitted after its prerequisite was rejected is a clause running
    #: on an answer that does not exist -- a hostile review found exactly that admitting.
    PREREQUISITE_FAILED = "prerequisite_failed"
    #: The operation's own expander could not turn the clause into arguments.
    ARGUMENT_EXPANSION_FAILED = "argument_expansion_failed"
    #: The real permission policy said no. Recorded as a refusal, never retried around.
    PERMISSION_DENIED = "permission_denied"
    #: The operation is registered but the runtime cannot currently serve it.
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    #: The request shape is out of scope for this phase.
    UNSUPPORTED_SHAPE = "unsupported_shape"
    #: The resolver could not be consulted. NOT a verdict about the turn.
    RESOLVER_UNAVAILABLE = "resolver_unavailable"
    #: The contract does not declare a side-effect class, so nothing may authorize it.
    SIDE_EFFECT_CLASS_UNDECLARED = "side_effect_class_undeclared"


#: Reason codes that mean "not admitted". Complement of `{ADMITTED}`; kept as data so a test can
#: assert every member is accounted for and a newly added code cannot silently mean "fine".
REJECTION_REASONS = frozenset(code for code in ReasonCode if code is not ReasonCode.ADMITTED)

_EFFECT_ALLOW = "allow"


class _Grant:
    """Proof that the production permission path allowed ONE operation, on ONE occasion.

    Three properties, each closing a forgery a hostile review demonstrated:

    * **There is no importable constant.** The previous design used a module-level `_POLICY_GRANT`
      sentinel; importing it and passing it in produced an allowing record. Each grant is now a
      fresh object, and only instances registered in `_ISSUED_GRANTS` count -- a private registry
      that only `core.semantic.admission` writes to, after the real policy has answered.
    * **A grant names its operation.** It authorizes `operation` and nothing else, so a grant
      obtained for `machine.inspect_specs` cannot be carried onto `evil.delete_everything`.
    * **Constructing one is not the same as being able to use one.** `_Grant(...)` built by hand is
      not in the registry, so `PermissionRecord.allowed` is False. Importing `_issue_grant` and
      minting a registered one DOES produce an allowing record -- a reviewer demonstrated it, and
      the earlier comment claiming otherwise was wrong. The boundary that holds is elsewhere and is
      the one worth stating: `admit()` exposes no permission parameter, so no such value reaches an
      admission. These grants are a coherence check on the admission path, not an unforgeable token.
    """

    # `__weakref__` so the registry below can hold these weakly.
    __slots__ = ("__weakref__", "_lock", "arguments_digest", "operation", "spent")

    def __init__(self, operation: str, arguments_digest: str = "") -> None:
        self.operation = str(operation)
        #: The arguments the policy was actually asked about. A grant obtained for
        #: `read_file(safe.txt)` is not authority for `read_file(/etc/shadow)`: same operation,
        #: different call, and the policy never saw the second one.
        self.arguments_digest = str(arguments_digest)
        #: One admission, one grant. A permission answer describes an OCCASION, and replaying it on
        #: a later turn is asserting that a question asked once was asked again.
        self.spent = False
        self._lock = threading.Lock()


#: Grants the production permission path has actually issued. A `WeakSet` so a grant that goes out
#: of scope stops counting; membership is identity-based, which is the point.
_ISSUED_GRANTS: weakref.WeakSet = weakref.WeakSet()


def _argument_frame(tag: bytes, payload: bytes = b"") -> bytes:
    """Length-frame one typed argument node so concatenation cannot erase structure."""
    return tag + len(payload).to_bytes(8, "big") + payload


def _typed_argument_bytes(value: Any, active: set[int] | None = None) -> bytes:
    """Canonical, type-preserving bytes for a permission argument value.

    Permission identity is deliberately narrower than Python's general object model. Values with no
    declared canonical representation fail closed instead of falling back to ``repr`` (which can be
    unstable or attacker-controlled). Maps are unordered; sequence order is preserved; list and
    tuple, bytes and bytearray, set and frozenset remain different types.
    """
    if value is None:
        return _argument_frame(b"n")
    if type(value) is bool:
        return _argument_frame(b"b", b"1" if value else b"0")
    if type(value) is int:
        return _argument_frame(b"i", str(value).encode("ascii"))
    if type(value) is float:
        return _argument_frame(b"f", struct.pack(">d", value))
    if type(value) is str:
        # Exact code-point identity: NFC and NFD strings are different arguments unless the
        # operation's own validator explicitly normalizes them before permission consultation.
        return _argument_frame(b"s", value.encode("utf-8", "surrogatepass"))
    if type(value) is bytes:
        return _argument_frame(b"y", value)
    if type(value) is bytearray:
        return _argument_frame(b"a", bytes(value))

    if active is None:
        active = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise TypeError("cyclic mappings have no permission argument identity")
        active.add(identity)
        try:
            pairs = [
                _argument_frame(
                    b"p",
                    _typed_argument_bytes(key, active) + _typed_argument_bytes(item, active),
                )
                for key, item in value.items()
            ]
            pairs.sort()
            return _argument_frame(b"m", b"".join(pairs))
        finally:
            active.remove(identity)
    if type(value) in {list, tuple}:
        identity = id(value)
        if identity in active:
            raise TypeError("cyclic sequences have no permission argument identity")
        active.add(identity)
        try:
            payload = b"".join(
                _argument_frame(b"e", _typed_argument_bytes(item, active)) for item in value
            )
            return _argument_frame(b"l" if type(value) is list else b"t", payload)
        finally:
            active.remove(identity)
    if type(value) in {set, frozenset}:
        identity = id(value)
        if identity in active:
            raise TypeError("cyclic sets have no permission argument identity")
        active.add(identity)
        try:
            members = sorted(_argument_frame(b"e", _typed_argument_bytes(item, active)) for item in value)
            return _argument_frame(b"q" if type(value) is set else b"r", b"".join(members))
        finally:
            active.remove(identity)
    raise TypeError(f"unsupported permission argument type: {type(value).__module__}.{type(value).__qualname__}")


def arguments_digest(arguments: Any) -> str:
    """Stable typed identity for the exact arguments shown to permission policy.

    JSON is not used: its object keys are strings, so ``{1: value}`` and ``{"1": value}`` collapse
    before hashing. The canonical representation above preserves types and structure while making
    semantically unordered maps independent of insertion order.
    """
    return hashlib.sha256(_typed_argument_bytes(arguments)).hexdigest()


def _issue_grant(operation: str, arguments_digest_value: str = "") -> _Grant:
    """Mint and register a grant. Private to this module and `core.semantic.admission`.

    Deliberately NOT exported. `core.semantic.admission` reaches it by module attribute, which is
    visible in a diff; anything else that wants an allowing record has to add itself to that list of
    callers, and a test asserts the list is one entry long.
    """
    grant = _Grant(operation, arguments_digest_value)
    _ISSUED_GRANTS.add(grant)
    return grant


@dataclass(frozen=True)
class IntentProposal:
    """What a model proposes for one clause, before the runtime has validated anything.

    **This type deliberately cannot carry authority.** There is no `side_effect_class`, no
    `approval_requirement`, no `permission_actions`, no `read_only`. A proposal that could name its
    own side-effect class would let the model declare a destructive operation harmless, which is the
    single highest-consequence thing a model must not be able to say. Those fields exist on
    `ValidatedIntent`, where they are copied from the registry.

    `arguments` is whatever the model offered, unvalidated -- a `Mapping` of raw values. It is not
    trustworthy and is not meant to be; validation against the operation's declared schema is the
    runtime's job and happens on the way to `ValidatedIntent`.
    """

    #: Position of this clause in the proposal. Identity within one turn, not across turns.
    index: int
    #: The clause in the user's own words.
    request_text: str
    #: The operation the model believes serves this clause. A name only -- the registry decides
    #: whether it exists, and `UNKNOWN_OPERATION` is the answer when it does not.
    operation: str
    #: Raw, unvalidated arguments the model offered.
    arguments: Mapping[str, Any] = field(default_factory=dict)
    #: Where in the canonical text the entities are. Spans rather than strings so a repeated entity
    #: ("weather in Paris and Paris") is two distinct references rather than one deduplicated one.
    spans: tuple[EntitySpan, ...] = ()
    #: Indices of EARLIER clauses whose answers this one needs.
    depends_on: tuple[int, ...] = ()
    #: How the proposal was produced -- "model", "certain_recognizer", "test". Provenance, not trust.
    origin: str = "model"
    #: The RequestGraph identities this clause was projected from, when it was. Provenance only:
    #: the stable ids a downstream stage may carry instead of re-deriving the clause from text.
    request_id: str = ""
    slot_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "request_text": self.request_text,
            "operation": self.operation,
            "arguments": dict(self.arguments),
            "spans": [span.to_dict() for span in self.spans],
            "depends_on": list(self.depends_on),
            "origin": self.origin,
            "request_id": self.request_id,
            "slot_ids": list(self.slot_ids),
        }


@dataclass(frozen=True)
class ValidatedIntent:
    """A proposal the runtime has checked against the registry. Still not authorized to run.

    The separation from `AdmissionResult` is the point: validation asks "is this a real operation
    with arguments that fit its declared schema and spans that bind?", admission asks "may it run?".
    Collapsing them is how "the registry knows about this tool" becomes "therefore it may execute",
    which is exactly the confusion `registration != execution authorization` names.

    `side_effect_class`, `tool_intent` and `approval_requirement` are copied from the registered
    contract at construction by `core.semantic.admission`, never read from the proposal.
    """

    operation: str
    request_text: str
    #: Arguments after validation against the operation's declared schema.
    arguments: Mapping[str, Any]
    #: The canonical text every span in `spans` is measured against.
    canonical: CanonicalText
    spans: tuple[EntitySpan, ...] = ()
    #: From the registry. Empty is not "harmless" -- it is `SIDE_EFFECT_CLASS_UNDECLARED`.
    side_effect_class: str = ""
    #: The permission-checkable intent name this runs as, from the registry.
    tool_intent: str = ""
    #: From the registry.
    approval_requirement: str = ""
    depends_on: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "request_text": self.request_text,
            "arguments": dict(self.arguments),
            "canonical": self.canonical.to_dict(),
            "spans": [span.to_dict() for span in self.spans],
            "side_effect_class": self.side_effect_class,
            "tool_intent": self.tool_intent,
            "approval_requirement": self.approval_requirement,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class PermissionRecord:
    """A projection of the REAL permission policy's answer, carried as evidence it was asked.

    Deliberately not `core.mode_permission_policy.PermissionDecision` and deliberately not a
    replacement for it. This is a flattened record of what that policy said, built by
    `core.semantic.admission` from an actual `decide_tool_call` return. Keeping it separate means
    this module has no import of the permission engine -- and keeping it a *projection* means there
    is no second policy here that could drift into disagreeing with the first.

    `policy` names which policy answered. An admission whose `policy` is empty did not consult one;
    production issuance happens only after a real allowing record is atomically consumed.

    **An ALLOWING record cannot be written by hand.** A hostile review demonstrated the hole: this
    type used to be a plain dataclass, so `PermissionRecord(allowed=True, policy="fabricated.policy")`
    admitted an intent without any policy ever being consulted. Naming a policy is not evidence of
    having asked one. Now `allowed` is derived, not assigned, and it is True only for a record minted
    by `grant_from_policy_decision` out of a real `core.mode_permission_policy.PermissionDecision`
    whose own effect was ALLOW.

    Refusals stay freely constructible. A record that says "no" cannot be used to escalate, and
    forcing tests to obtain a genuine denial from the engine would buy nothing.
    """

    policy: str
    #: The canonical operation identity this record is about. A grant is valid ONLY for this
    #: operation -- a hostile review carried one operation's grant onto another through a contract
    #: alias, and the record itself now refuses to be read that way.
    operation: str = ""
    #: The policy's own effect string ("allow", "deny", "require_approval"), verbatim.
    effect: str = ""
    #: The permission actions the policy resolved this call to, as strings.
    actions: tuple[str, ...] = ()
    detail: str = ""
    #: Whether the policy would require an explicit human approval before this runs.
    requires_approval: bool = False
    #: The mint token. Private, defaulted, and checked -- see `grant_from_policy_decision`.
    grant: Any = None

    def __post_init__(self) -> None:
        if self.grant is not None and not isinstance(self.grant, _Grant):
            raise ValueError("a permission grant must be one this module issued, not an assembled object")

    def allows(self, operation: str) -> bool:
        """Whether this record authorizes exactly `operation`.

        Three conditions, all required. The grant must be an object this module ISSUED (registry
        membership, not a type check and not an importable constant); the effect must be allow; and
        the grant's own operation must be the one being asked about. The third is what stops a grant
        for one capability being spent on another.
        """
        grant = self.grant
        if not isinstance(grant, _Grant) or grant not in _ISSUED_GRANTS:
            return False
        if str(self.effect or "").lower() != _EFFECT_ALLOW:
            return False
        wanted = str(operation or "")
        return bool(wanted) and grant.operation == wanted and self.operation == wanted

    def authorizes(self, intent: ValidatedIntent) -> tuple[bool, str]:
        """Whether this record authorizes THIS call, on THIS occasion. Returns `(ok, why_not)`.

        `allows()` answers about an operation name. That was not enough, and a hostile review showed
        why twice: a grant obtained for one set of arguments was re-attached to a call with
        different ones -- same operation, a call the policy never saw -- and a grant from an earlier
        admission was replayed on a second occasion. A permission answer describes one question
        asked once, so it is bound to the arguments it was asked about and is spent when used.
        """
        if not self.allows(getattr(intent, "tool_intent", "")):
            return False, ("the permission policy refused this call, or the grant names a different "
                           "operation than the one about to execute")
        grant = self.grant
        if grant.spent:
            return False, ("this grant has already authorized an admission; a permission answer "
                           "describes one occasion and cannot be replayed on another")
        try:
            wanted = arguments_digest(getattr(intent, "arguments", {}))
        except TypeError as exc:
            return False, f"the call has no canonical permission argument identity: {exc}"
        if grant.arguments_digest != wanted:
            return False, ("the policy was asked about different arguments than the ones about to "
                           "run; same operation is not the same call")
        return True, ""

    def consume_authorization(self, intent: ValidatedIntent) -> tuple[bool, str]:
        """Atomically consume this record for exactly ``intent`` once.

        The old check lived in ``authorizes()`` and ``AdmissionResult.__post_init__`` set
        ``grant.spent`` afterwards. Two threads could therefore both observe ``False`` before
        either set it. The grant owns the lock because the grant is the replay unit.

        This method is not an admission API. It is used only after ``admission.admit`` has itself
        consulted the production policy; a caller-created record has no public route into an
        admitted result.
        """
        grant = self.grant
        if not isinstance(grant, _Grant):
            return False, "the permission record carries no issued grant"
        with grant._lock:
            authorized, why_not = self.authorizes(intent)
            if not authorized:
                return False, why_not
            grant.spent = True
            return True, ""

    @property
    def allowed(self) -> bool:
        """Whether this record allows the operation it names. Never a stored input."""
        return self.allows(self.operation)

    @classmethod
    def refusal(
        cls, *, policy: str, operation: str = "", effect: str = "deny", detail: str = "",
        actions: tuple[str, ...] = (),
    ) -> PermissionRecord:
        """A record that refuses. Constructible by anyone -- it can only ever narrow."""
        return cls(policy=policy, operation=operation, effect=effect, detail=detail, actions=tuple(actions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "policy": self.policy,
            "effect": self.effect,
            "actions": list(self.actions),
            "detail": self.detail,
            "requires_approval": self.requires_approval,
        }


#: `grant_from_policy_decision` USED to live here, taking a `PermissionDecision` from the caller and
#: minting a grant from it. A hostile review built the engine's own decision type by hand and minted
#: an allowing record with it. Accepting a caller-supplied object as authority is the defect, and no
#: amount of type-checking that object fixes it -- the type is constructible.
#:
#: Authority is now obtained only by CALLING the production path. See
#: `core.semantic.admission._consult_permission_policy`, which invokes `decide_tool_call` itself and
#: mints through `_issue_grant`. That function is importable -- see `_Grant` for why that is
#: stated rather than denied -- but nothing it returns can be handed to `admit()`.


def _admission_outcome_claim(owner: AdmissionResult):
    """Hold the one resolution-outcome identity allowed to consume ``owner``.

    Once the ``claimed`` generator fast-local is set, callers can inspect it but cannot replace or
    reset it. Direct ``send`` calls are ignored; only the exact contextual verifier sealed below
    may advance this claim.
    """
    expected_code, expected_globals = (
        "__VOOL_OUTCOME_CLAIM_CODE__",
        "__VOOL_OUTCOME_CLAIM_GLOBALS__",
    )
    while True:
        requested = yield None
        caller = sys._getframe(1)
        if caller.f_code is expected_code and caller.f_globals is expected_globals:
            break
    claimed = requested
    while True:
        yield claimed


def _new_admission_outcome_claim(owner: AdmissionResult):
    """Create and prime one claim frame; production binds it into its completed frame."""
    claim = _admission_outcome_claim(owner)
    next(claim)
    return claim


class AdmissionResult:
    """Whether one validated intent may run, and why.

    Public construction creates refusals only. Production admission materializes the one admitted
    shape after policy consultation and atomic grant consumption, then binds it to the exact
    completed production frame that created it. There is no authority registry to discover, alias
    or mutate; a copied frame belongs to its original result identity, not to the copy.

    There is no member, field or value that means "fall through to chat". Admission either lets an
    intent run or states, in a typed code, why it did not.
    """

    __slots__ = (
        "__weakref__",
        "_occasion",
        "_outcome_claim",
        "detail",
        "intent",
        "permission",
        "reason",
    )

    def __init__(
        self,
        admitted: bool,
        reason: ReasonCode,
        intent: ValidatedIntent | None = None,
        permission: PermissionRecord | None = None,
        detail: str = "",
    ) -> None:
        if admitted:
            raise ValueError(
                "admitted results are produced only inside core.semantic.admission after this "
                "occasion's production permission-policy consultation"
            )
        if reason is ReasonCode.ADMITTED:
            raise ValueError("a rejected result must carry a rejection reason")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "intent", intent)
        object.__setattr__(self, "permission", permission)
        object.__setattr__(self, "detail", str(detail))

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise FrozenInstanceError("cannot assign to field")

    @property
    def admitted(self) -> bool:
        """Whether the exact completed production occasion created this exact object.

        The constants below are replaced once, while ``core.semantic.admission`` is importing,
        with the production ``admit`` code object and CPython's frame type. They live in this
        getter's immutable code constants: there is no closure, helper global or mutable registry a
        caller can obtain and add itself to.
        """
        (
            expected_code,
            frame_type,
            expected_globals,
            expected_consultation,
            expected_claim_code,
            generator_type,
        ) = (
            "__VOOL_ADMIT_CODE__",
            "__VOOL_FRAME_TYPE__",
            "__VOOL_ADMIT_GLOBALS__",
            "__VOOL_PERMISSION_CONSULTATION__",
            "__VOOL_OUTCOME_CLAIM_GENERATOR_CODE__",
            "__VOOL_GENERATOR_TYPE__",
        )
        try:
            occasion = self._occasion
            claim = self._outcome_claim
        except AttributeError:
            return False
        if occasion.__class__ is not frame_type or occasion.f_code is not expected_code:
            return False
        if occasion.f_globals is not expected_globals:
            return False
        if claim.__class__ is not generator_type or claim.gi_code is not expected_claim_code:
            return False
        claim_frame = claim.gi_frame
        if claim_frame is None or claim_frame.f_locals.get("owner") is not self:
            return False
        locals_ = occasion.f_locals
        return (
            locals_.get("result") is self
            and locals_.get("claim") is claim
            and locals_.get("intent") is self.intent
            and locals_.get("permission") is self.permission
            and locals_.get("permission_consultation") is expected_consultation
            and locals_.get("authorized") is True
        )

    def _admitted_for(
        self,
        outcome: ResolutionOutcome,
        proposal: IntentProposal,
        canonical: CanonicalText,
    ) -> bool:
        """Whether this historical result belongs to this exact resolution occasion."""
        if not self.admitted:
            return False
        occasion_locals = self._occasion.f_locals
        if (
            occasion_locals.get("proposal") is not proposal
            or occasion_locals.get("canonical") is not canonical
        ):
            return False
        claim = self._outcome_claim
        claim_frame = claim.gi_frame
        if claim_frame is None:
            return False
        claimed = claim_frame.f_locals.get("claimed")
        if claimed is None:
            expected_code, expected_globals = (
                "__VOOL_RESOLUTION_POST_INIT_CODE__",
                "__VOOL_RESOLUTION_GLOBALS__",
            )
            caller = sys._getframe(1)
            if caller.f_code is not expected_code or caller.f_globals is not expected_globals:
                return False
            try:
                claim.send((outcome, proposal, canonical))
            except (StopIteration, TypeError, ValueError):
                return False
            claim_frame = claim.gi_frame
            if claim_frame is None:
                return False
            claimed = claim_frame.f_locals.get("claimed")
        if not isinstance(claimed, tuple) or len(claimed) != 3:
            return False
        claimed_outcome, claimed_proposal, claimed_canonical = claimed
        return (
            claimed_outcome is outcome
            and claimed_proposal is proposal
            and claimed_canonical is canonical
        )

    def __copy__(self):
        raise TypeError("admission results cannot be copied; retain the original historical result")

    def __deepcopy__(self, _memo):
        raise TypeError("admission results cannot be copied; retain the original historical result")

    def __repr__(self) -> str:
        return (
            f"AdmissionResult(admitted={self.admitted!r}, reason={self.reason!r}, "
            f"intent={self.intent!r}, permission={self.permission!r}, detail={self.detail!r})"
        )

    @classmethod
    def reject(
        cls,
        reason: ReasonCode,
        *,
        detail: str = "",
        intent: ValidatedIntent | None = None,
        permission: PermissionRecord | None = None,
    ) -> AdmissionResult:
        """A typed refusal. `intent`/`permission` are carried when they exist, so a permission
        denial can show what was asked for and who refused it."""
        if reason is ReasonCode.ADMITTED:
            raise ValueError("reject() requires a rejection reason")
        return cls(admitted=False, reason=reason, intent=intent, permission=permission, detail=detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason": self.reason.value,
            "detail": self.detail,
            "intent": self.intent.to_dict() if self.intent is not None else None,
            "permission": self.permission.to_dict() if self.permission is not None else None,
        }


@dataclass(frozen=True)
class ResolutionOutcome:
    """Every clause of one turn, and what became of each. Nothing may be missing.

    The count check is the whole value of this type. A partial-failure bug does not announce itself
    -- it looks exactly like a turn that had fewer clauses. Requiring one admission per proposed
    clause at construction time means a clause that vanished takes the outcome down here, loudly,
    instead of shipping an answer that quietly covers three of four requests.
    """

    state: ResolutionState
    shape: RequestShape
    canonical: CanonicalText
    proposals: tuple[IntentProposal, ...] = ()
    admissions: tuple[AdmissionResult, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        # Unconditional. The earlier `if self.admissions and ...` let a proposal set with ZERO
        # admissions construct cleanly and then read as success-shaped: `admitted_count` 0,
        # `rejected_count` 0, `is_partial` False -- a turn that resolved nothing, reporting nothing
        # wrong. A hostile review built exactly that. One clause in, one result out, always.
        if len(self.admissions) != len(self.proposals):
            raise ValueError(
                f"{len(self.proposals)} clauses proposed but {len(self.admissions)} admission "
                "results -- every clause must be accounted for, none may disappear"
            )
        # Claim each legitimate result for this exact outcome once. A mismatched or stale result
        # can still be retained as hostile evidence, but every production-facing count below reads
        # it as rejected rather than replaying an earlier permission occasion.
        for proposal, result in zip(self.proposals, self.admissions, strict=True):
            result._admitted_for(self, proposal, self.canonical)

    @property
    def admitted_count(self) -> int:
        return sum(
            1
            for proposal, result in zip(self.proposals, self.admissions, strict=True)
            if result._admitted_for(self, proposal, self.canonical)
        )

    @property
    def rejected_count(self) -> int:
        return len(self.admissions) - self.admitted_count

    @property
    def is_partial(self) -> bool:
        """Some clauses admitted and some did not -- the case that must never be reported as done."""
        return bool(self.admissions) and 0 < self.admitted_count < len(self.admissions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "shape": self.shape.value,
            "canonical": self.canonical.to_dict(),
            "clause_count": len(self.proposals),
            "admitted_count": self.admitted_count,
            "rejected_count": self.rejected_count,
            "is_partial": self.is_partial,
            "proposals": [proposal.to_dict() for proposal in self.proposals],
            "admissions": [result.to_dict() for result in self.admissions],
            "detail": self.detail,
        }


# Seal contextual claiming to the exact production outcome constructor. The claim generator's
# mutable state is caller-visible but not caller-authoritative: a direct ``send`` cannot set it,
# and a replacement generator is no longer the object retained by the completed admission frame.
_admitted_for = AdmissionResult._admitted_for
_admitted_for_constants = list(_admitted_for.__code__.co_consts)
_admitted_for_marker = (
    "__VOOL_RESOLUTION_POST_INIT_CODE__",
    "__VOOL_RESOLUTION_GLOBALS__",
)
if _admitted_for_constants.count(_admitted_for_marker) != 1:  # pragma: no cover
    raise RuntimeError("resolution authority marker is missing or ambiguous")
_admitted_for_constants[_admitted_for_constants.index(_admitted_for_marker)] = (
    ResolutionOutcome.__post_init__.__code__,
    globals(),
)
_admitted_for.__code__ = _admitted_for.__code__.replace(co_consts=tuple(_admitted_for_constants))

_claim_constants = list(_admission_outcome_claim.__code__.co_consts)
_claim_marker = (
    "__VOOL_OUTCOME_CLAIM_CODE__",
    "__VOOL_OUTCOME_CLAIM_GLOBALS__",
)
if _claim_constants.count(_claim_marker) != 1:  # pragma: no cover
    raise RuntimeError("outcome claim marker is missing or ambiguous")
_claim_constants[_claim_constants.index(_claim_marker)] = (_admitted_for.__code__, globals())
_admission_outcome_claim.__code__ = _admission_outcome_claim.__code__.replace(
    co_consts=tuple(_claim_constants)
)
del _admitted_for, _admitted_for_constants, _admitted_for_marker
del _claim_constants, _claim_marker


@dataclass(frozen=True)
class CertainResult:
    """The formal fast path's answer. `CERTAIN` with an intent, or `ABSTAIN` with nothing.

    Enforced at construction: no third state exists, `CERTAIN` requires an intent, and `ABSTAIN`
    must not carry one. This is the type-level form of "a CERTAIN recognizer emits Certain(intent)
    or Abstain -- never CHAT" and of "zero model calls": there is no field a model result could
    occupy and no branch that could consult one.
    """

    state: ResolutionState
    intent: ValidatedIntent | None = None
    #: Which recognizer answered, for the receipt and for the precision gate that governs changes
    #: to it. Required on CERTAIN: an unattributed certainty cannot be re-measured.
    recognizer: str = ""

    def __post_init__(self) -> None:
        if self.state not in CERTAIN_RECOGNIZER_STATES:
            raise ValueError(
                f"a certain recognizer may only return "
                f"{sorted(s.value for s in CERTAIN_RECOGNIZER_STATES)}, got {self.state.value!r}"
            )
        if self.state is ResolutionState.CERTAIN:
            if self.intent is None:
                raise ValueError("CERTAIN must carry the intent it is certain about")
            if not str(self.recognizer or "").strip():
                raise ValueError("CERTAIN must name the recognizer that produced it")
        elif self.intent is not None:
            raise ValueError("ABSTAIN must not carry an intent")

    @classmethod
    def certain(cls, intent: ValidatedIntent, *, recognizer: str) -> CertainResult:
        return cls(state=ResolutionState.CERTAIN, intent=intent, recognizer=recognizer)

    @classmethod
    def abstain(cls, *, recognizer: str = "") -> CertainResult:
        return cls(state=ResolutionState.ABSTAIN, intent=None, recognizer=recognizer)


@runtime_checkable
class SemanticResolver(Protocol):
    """The clause-projection interface: zero or more ``IntentProposal``s for a turn.

    Kept as the compatibility contract for the admission path. Note what the signature does NOT
    allow: no permission argument, no execution callback, no side-effect declaration. A resolver
    proposes; it cannot reach past admission. The PRIMARY contract is ``GraphSemanticResolver``.
    """

    def propose(self, canonical: CanonicalText, *, operations: Sequence[str]) -> tuple[IntentProposal, ...]:
        """Propose zero or more clauses for `canonical`, choosing only from `operations`."""
        ...


@runtime_checkable
class GraphSemanticResolver(Protocol):
    """The primary resolver contract: interpret a turn into a ``RequestGraph``, in TWO phases.

    ``prepare`` builds everything the call needs (prompts, provider schema, versions, cache key) and
    ``finish`` turns the raw reply into a graph -- both PURE. The transport that actually reaches a
    model lives outside ``core/semantic`` (the runtime's shadow seam, an evaluation script), so no
    frame of this package is ever on a provider call's stack, and the resolver itself carries no
    per-turn state. Types are ``Any`` here only to keep this module free of a graph import cycle;
    ``core.semantic.resolver`` names them precisely.
    """

    def prepare(self, canonical: CanonicalText, *, operations: Sequence[str], turn_id: str = "") -> Any:
        """A ``PreparedInterpretation`` (prompts + versions + schema), or None when there is nothing to ask."""
        ...

    def finish(self, prepared: Any, reply: Any) -> Any:
        """A ``RequestGraph`` for the reply, or None when the resolver abstains."""
        ...


__all__ = [
    "CERTAIN_RECOGNIZER_STATES",
    "OUT_OF_SCOPE_SHAPES",
    "REJECTION_REASONS",
    "AdmissionResult",
    "CertainResult",
    "GraphSemanticResolver",
    "IntentProposal",
    "PermissionRecord",
    "ReasonCode",
    "RequestShape",
    "ResolutionOutcome",
    "ResolutionState",
    "SemanticResolver",
    "ValidatedIntent",
]
