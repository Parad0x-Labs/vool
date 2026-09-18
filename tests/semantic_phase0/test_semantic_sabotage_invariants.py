"""The permanent invariants. Each one is a way the architecture could be quietly undone.

These are not tests of features. Every one of them pins a property that must survive Phase 1, Phase 2
and whoever is holding this codebase in a year -- written now, while the contract is small enough to
state exactly, rather than after a mechanism exists and the test has to be reverse-engineered from
it.

Two kinds of invariant live here, and the difference is stated per test:

* **Enforced now** -- there is code today that would break if the property were violated, and the
  test breaks with it.
* **Pinned as a type/contract** -- the mechanism is Phase 1, so what is asserted is that the TYPES
  make the violation unspellable. A future resolver that wants to violate one of these has to change
  a type and a test to do it, which is the point: it becomes a visible decision instead of a
  plausible-looking line in a diff.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from core.semantic import admission as admission_module
from core.semantic import reach as semantic_reach
from core.semantic.admission import admit, admit_all
from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.semantic.types import (
    CERTAIN_RECOGNIZER_STATES,
    AdmissionResult,
    CertainResult,
    IntentProposal,
    PermissionRecord,
    ReasonCode,
    RequestShape,
    ResolutionOutcome,
    ResolutionState,
    ValidatedIntent,
)

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (  # noqa: F401
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)

CANONICAL = CanonicalText.of("read the config and tell me the timeout")


#: A real, registered, read-only intent. Tests needing an ADMIT use this and let the REAL policy
#: allow it: `admit` has no injectable permission checker any more, by design.
REAL_READ_ONLY_INTENT = "workspace.list_tree"


class _Contract:
    intent = "probe.sabotage"
    description = "a disposable operation"
    supported = True
    side_effect_class = "read_only"
    input_schema = {"target": "string optional"}
    output_schema = {"value": "string"}
    approval_requirement = "none"
    unsupported_reason = ""


def _lookup(name: str):
    return _Contract() if name == "probe.sabotage" else None


# ======================================================================================
# 1. A lexical miss must never certify CHAT
# ======================================================================================


def test_a_lexical_miss_cannot_be_spelled_as_chat() -> None:
    """PINNED AS TYPE. `ResolutionState` has no CHAT member, so "no keyword matched" cannot be
    written as "this is conversation". The recognizer's failure is ABSTAIN, which says something
    about the recognizer and nothing about the turn."""
    assert not any("chat" in state.value.lower() for state in ResolutionState)
    assert ResolutionState.ABSTAIN in CERTAIN_RECOGNIZER_STATES
    with pytest.raises(ValueError):
        CertainResult(state=ResolutionState.UNRESOLVED)


def test_a_certain_recognizer_can_only_return_certain_or_abstain() -> None:
    """PINNED AS TYPE. Widening this set is the change that would let a formal recognizer start
    guessing; it now requires editing a frozenset that this test reads."""
    assert {ResolutionState.CERTAIN, ResolutionState.ABSTAIN} == CERTAIN_RECOGNIZER_STATES
    for state in ResolutionState:
        if state in CERTAIN_RECOGNIZER_STATES:
            continue
        with pytest.raises(ValueError, match="may only return"):
            CertainResult(state=state)


def test_a_certain_verdict_must_name_its_recognizer() -> None:
    """ENFORCED. An unattributed certainty cannot be re-measured, and hostile-review correction 12
    requires a recognizer's precision gate to be re-run when it changes -- which needs to know which
    recognizer answered."""
    intent = ValidatedIntent(
        operation="probe.sabotage", request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent="probe.sabotage",
    )
    with pytest.raises(ValueError, match="recognizer"):
        CertainResult(state=ResolutionState.CERTAIN, intent=intent, recognizer="")
    assert CertainResult.certain(intent, recognizer="formal.timeout_reader").recognizer


def test_abstain_cannot_smuggle_an_intent() -> None:
    """ENFORCED. An ABSTAIN carrying an intent is a certainty that declined to say so."""
    intent = ValidatedIntent(
        operation="probe.sabotage", request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent="probe.sabotage",
    )
    with pytest.raises(ValueError, match="must not carry an intent"):
        CertainResult(state=ResolutionState.ABSTAIN, intent=intent)


# ======================================================================================
# 2. Entity presence must never certify an operation
# ======================================================================================


def test_a_span_over_an_entity_does_not_admit_anything_by_itself() -> None:
    """ENFORCED. "Tallinn is in the sentence" is not "this is a weather request". A proposal whose
    only content is a resolvable span still has to name a registered operation and pass permission."""
    canonical = CanonicalText.of("i once lived in Tallinn and it was fine")
    span = canonical.find("Tallinn", kind="location")
    assert span is not None and span.resolve(canonical) == "Tallinn"

    result = admit(
        IntentProposal(index=0, request_text="i once lived in Tallinn", operation="weather.lookup", spans=(span,)),
        canonical=canonical,
    )
    assert result.admitted is False
    assert result.reason is ReasonCode.UNKNOWN_OPERATION


# ======================================================================================
# 3. An unknown operation must never bypass the registry
# ======================================================================================


def test_an_unregistered_operation_cannot_be_admitted_by_any_argument() -> None:
    """ENFORCED. Not by well-formed arguments, not by a permission stub that would allow, not by
    spans that bind. The registry lookup happens before all of them."""
    result = admit(
        IntentProposal(
            index=0, request_text="do the thing", operation="not.registered.anywhere",
            arguments={"target": "x"},
        ),
        canonical=CANONICAL,
    )
    assert result.reason is ReasonCode.UNKNOWN_OPERATION
    assert result.intent is None, "nothing may be validated against a contract that does not exist"


# ======================================================================================
# 4. The model must never supply the side-effect class
# ======================================================================================


def test_a_proposal_has_no_field_that_could_declare_authority() -> None:
    """PINNED AS TYPE. The highest-consequence thing a model must not be able to say is "this
    destructive operation is harmless". `IntentProposal` gives it nowhere to write that."""
    fields = set(IntentProposal.__dataclass_fields__)
    for forbidden in (
        "side_effect_class", "approval_requirement", "permission_actions", "read_only",
        "permission", "allowed", "supported", "authority", "trusted",
    ):
        assert forbidden not in fields, f"IntentProposal must not carry {forbidden!r}"


def test_a_proposal_that_names_a_side_effect_class_is_rejected_outright() -> None:
    """ENFORCED. Not merely ignored -- construction fails, so a Phase-1 parser that tried to pass one
    through gets an error rather than a silently dropped field."""
    with pytest.raises(TypeError):
        IntentProposal(  # type: ignore[call-arg]
            index=0, request_text="x", operation="probe.sabotage", side_effect_class="read_only",
        )


def test_the_side_effect_class_on_a_validated_intent_comes_from_the_contract() -> None:
    """ENFORCED. The proposal claims nothing; the admitted intent carries what the registry declared.

    Driven against a REAL registered intent and the REAL policy, because that is the only way to
    reach an admit now.
    """
    result = admit(
        IntentProposal(index=0, request_text="x", operation=REAL_READ_ONLY_INTENT, arguments={}),
        canonical=CANONICAL,
    )
    assert result.admitted is True
    assert result.intent is not None
    assert result.intent.side_effect_class == "read_only"
    assert result.permission is not None
    assert result.permission.policy == "core.mode_permission_policy.decide_tool_call"


def test_an_undeclared_side_effect_class_never_resolves_to_harmless() -> None:
    """ENFORCED. "Unspecified" must fail closed. The direction of this mistake is unrecoverable."""

    class _Undeclared(_Contract):
        side_effect_class = ""

    result = admit(
        IntentProposal(index=0, request_text="x", operation="probe.sabotage", arguments={}),
        canonical=CANONICAL,
        contract_lookup=lambda _name: _Undeclared(),
    )
    assert result.reason is ReasonCode.SIDE_EFFECT_CLASS_UNDECLARED


# ======================================================================================
# 5. An unsupported capability must never execute
# ======================================================================================


def test_an_unsupported_capability_is_refused_even_when_permission_would_allow() -> None:
    """ENFORCED. The capability check sits ABOVE the permission check, so "allowed" cannot rescue
    "cannot be served"."""

    class _Unsupported(_Contract):
        supported = False
        unsupported_reason = "no service configured"

    result = admit(
        IntentProposal(index=0, request_text="x", operation="probe.sabotage", arguments={}),
        canonical=CANONICAL,
        contract_lookup=lambda _name: _Unsupported(),
    )
    assert result.reason is ReasonCode.CAPABILITY_UNSUPPORTED


# ======================================================================================
# 6. A clause must never silently disappear
# ======================================================================================


def test_an_outcome_cannot_be_built_that_loses_a_clause() -> None:
    """ENFORCED. A partial-failure bug looks exactly like a turn that had fewer clauses, so the count
    is checked at construction."""
    proposals = tuple(
        IntentProposal(index=i, request_text=f"clause {i}", operation="probe.sabotage") for i in range(3)
    )
    admissions = (AdmissionResult.reject(ReasonCode.UNKNOWN_OPERATION),)
    with pytest.raises(ValueError, match="must be accounted for"):
        ResolutionOutcome(
            state=ResolutionState.PROPOSED, shape=RequestShape.MULTI_CLAUSE, canonical=CANONICAL,
            proposals=proposals, admissions=admissions,
        )

    # ZERO admissions is the case the check used to skip entirely -- `if self.admissions and ...`
    # short-circuited on the empty tuple, so a turn that resolved nothing constructed cleanly and
    # read as success-shaped: admitted 0, rejected 0, is_partial False. A hostile review built it.
    with pytest.raises(ValueError, match="must be accounted for"):
        ResolutionOutcome(
            state=ResolutionState.PROPOSED, shape=RequestShape.MULTI_CLAUSE, canonical=CANONICAL,
            proposals=proposals, admissions=(),
        )


def test_admit_all_returns_one_result_per_clause_under_every_failure() -> None:
    """ENFORCED. Refusal, exception, unknown operation -- the list length never shrinks."""
    proposals = [
        IntentProposal(index=0, request_text="ok", operation=REAL_READ_ONLY_INTENT),
        IntentProposal(index=1, request_text="unknown", operation="nope.nope"),
        IntentProposal(index=2, request_text="bad dep", operation=REAL_READ_ONLY_INTENT, depends_on=(9,)),
    ]

    results = admit_all(proposals, canonical=CANONICAL)
    assert len(results) == 3
    assert [r.reason for r in results] == [
        ReasonCode.ADMITTED, ReasonCode.UNKNOWN_OPERATION, ReasonCode.INVALID_DEPENDENCY,
    ]


def test_a_partial_result_reports_itself_as_partial() -> None:
    """ENFORCED. Some admitted and some refused must never read as done."""
    proposals = (
        IntentProposal(index=0, request_text="ok", operation=REAL_READ_ONLY_INTENT),
        IntentProposal(index=1, request_text="no", operation="nope.nope"),
    )
    admissions = admit_all(proposals, canonical=CANONICAL)
    outcome = ResolutionOutcome(
        state=ResolutionState.PROPOSED, shape=RequestShape.MULTI_CLAUSE, canonical=CANONICAL,
        proposals=proposals, admissions=admissions,
    )
    assert outcome.is_partial is True
    assert outcome.admitted_count == 1 and outcome.rejected_count == 1
    assert outcome.to_dict()["is_partial"] is True


# ======================================================================================
# 7. A resolver being unavailable must never become CHAT
# ======================================================================================


def test_resolver_unavailable_is_its_own_reason_and_not_a_verdict_about_the_turn() -> None:
    """PINNED AS TYPE. When the Phase-1 resolver cannot be consulted, the accurate record is "we could
    not ask", not "this was only conversation". The code exists to say so."""
    assert ReasonCode.RESOLVER_UNAVAILABLE.value == "resolver_unavailable"
    result = AdmissionResult.reject(ReasonCode.RESOLVER_UNAVAILABLE, detail="model lane down")
    assert result.admitted is False
    assert "chat" not in result.reason.value


def test_an_unsupported_request_shape_is_represented_rather_than_answered_partially() -> None:
    """PINNED AS TYPE. Conditional, mid-turn-correction and cross-turn-reference shapes are out of
    scope for this phase. A conditional request answered as if unconditional is a WRONG answer, not
    a partial one, so the shapes exist in the enum and are named as out of scope."""
    from core.semantic.types import OUT_OF_SCOPE_SHAPES

    assert {
        RequestShape.CONDITIONAL, RequestShape.MID_TURN_CORRECTION, RequestShape.CROSS_TURN_REFERENCE,
    } == OUT_OF_SCOPE_SHAPES
    assert RequestShape.UNKNOWN not in OUT_OF_SCOPE_SHAPES, "unknown is not a synonym for unsupported"


# ======================================================================================
# 8 & 9. Registration puts a capability in the catalog, and grants nothing
# ======================================================================================


def test_registration_is_never_execution_authorization_in_the_admission_path() -> None:
    """ENFORCED. There is one admitted-result materialization site, after policy and consume."""
    source = inspect.getsource(admission_module)
    assert "AdmissionResult.admit(" not in source, (
        "a factory accepting caller-held permission state recreates the admission bypass"
    )
    assert source.count("object.__new__(AdmissionResult)") == 1, (
        "there must be exactly one materialization site for an admitted result"
    )
    admit_at = source.index("object.__new__(AdmissionResult)")
    permission_at = source.index("permission = permission_consultation(")
    consume_at = source.index("permission.consume_authorization(intent)")
    assert permission_at < consume_at < admit_at, (
        "production consultation and atomic occasion consume must precede admission"
    )


def test_admission_has_no_injectable_permission_checker_at_all(monkeypatch) -> None:
    """ENFORCED, and the root repair. An injectable authority seam is an optional authority seam.

    `admit` used to take `permission_check`, so any caller could hand in a callable returning an
    allowing record and be admitted with no policy consulted. The parameter is gone; passing it is a
    TypeError, and the only route to a grant is the real engine's own decision object.
    """
    import inspect

    assert "permission_check" not in inspect.signature(admit).parameters
    assert "permission_check" not in inspect.signature(admit_all).parameters
    with pytest.raises(TypeError):
        admit(  # type: ignore[call-arg]
            IntentProposal(index=0, request_text="x", operation=REAL_READ_ONLY_INTENT),
            canonical=CANONICAL,
            permission_check=lambda **_k: PermissionRecord(policy="fabricated", effect="allow"),
        )


def test_a_substitute_for_the_permission_engine_denies(monkeypatch) -> None:
    """ENFORCED. Anything that is not the engine's own decision type refuses."""
    import core.mode_permission_policy as policy_module

    for bogus in (None, True, "allowed", {"allowed": True}, 1):
        monkeypatch.setattr(policy_module, "decide_tool_call", lambda _b=bogus, **_k: _b)
        result = admit(
            IntentProposal(index=0, request_text="x", operation=REAL_READ_ONLY_INTENT, arguments={}),
            canonical=CANONICAL,
        )
        assert result.admitted is False, f"a policy returning {bogus!r} must not admit"


def test_a_permission_engine_that_cannot_answer_denies(monkeypatch) -> None:
    """ENFORCED. A gate that fails open is not a gate."""
    import core.mode_permission_policy as policy_module

    def _explode(**_kwargs: object):
        raise RuntimeError("engine down")

    monkeypatch.setattr(policy_module, "decide_tool_call", _explode)
    record = admission_module._consult_permission_policy(
        intent=REAL_READ_ONLY_INTENT, arguments={}, task_id="t", source_context={},
    )
    assert isinstance(record, PermissionRecord)
    assert record.allowed is False
    assert record.policy == admission_module.PERMISSION_POLICY


# ======================================================================================
# 12. A malformed proposal must never execute
# ======================================================================================


@pytest.mark.parametrize(
    "bad",
    [
        "a string",
        42,
        None,
        {"operation": "probe.sabotage"},
        ["probe.sabotage"],
    ],
)
def test_a_malformed_proposal_is_refused_before_anything_is_looked_up(bad: object, monkeypatch) -> None:
    """ENFORCED. Shape is checked first, so a malformed proposal never reaches the registry or the
    permission policy at all."""
    looked_up: list[str] = []
    asked: list[str] = []
    import core.mode_permission_policy as policy_module

    monkeypatch.setattr(
        policy_module, "decide_tool_call",
        lambda **kwargs: asked.append(str(kwargs)) or None,
    )
    result = admit(
        bad,  # type: ignore[arg-type]
        canonical=CANONICAL,
        contract_lookup=lambda name: looked_up.append(name) or _Contract(),
    )
    assert result.reason is ReasonCode.MALFORMED_PROPOSAL
    assert looked_up == [] and asked == []


# ======================================================================================
# 14. A span must never index the wrong representation
# ======================================================================================


def test_a_span_measured_on_another_text_cannot_admit_a_clause() -> None:
    """ENFORCED. The span check sits above argument validation and permission, so a stale span
    cannot be carried into an admitted intent."""
    other = CanonicalText.of("a totally unrelated message about Riga")
    stray = other.find("Riga")
    assert stray is not None
    result = admit(
        IntentProposal(index=0, request_text="x", operation="probe.sabotage", spans=(stray,)),
        canonical=CANONICAL, contract_lookup=_lookup,
    )
    assert result.reason is ReasonCode.BAD_SPAN


def test_a_span_cannot_be_built_that_forgets_which_text_it_belongs_to() -> None:
    """PINNED AS TYPE. There is no constructor path that yields a span without a representation, a
    digest and a length -- all three are required fields."""
    signature = inspect.signature(EntitySpan)
    for name in ("start", "end", "representation", "text_digest", "text_length"):
        parameter = signature.parameters.get(name)
        assert parameter is not None, f"EntitySpan lost its {name!r} field"
        assert parameter.default is inspect.Parameter.empty, (
            f"{name!r} gained a default -- a span could then be built without knowing which text it "
            "belongs to, which is the whole failure this type exists to prevent"
        )
    # And the identity really is required at the call site, not merely declared.
    with pytest.raises(TypeError):
        EntitySpan(start=0, end=3)  # type: ignore[call-arg]


# ======================================================================================
# Instrumentation must stay observation
# ======================================================================================


def test_no_reach_function_returns_anything_a_caller_could_branch_on() -> None:
    """ENFORCED. The moment an observation function returns a decision, it stops being an
    observation. Every recording entry point returns None."""
    for name in ("record", "claimed", "declined", "blocked", "offered", "entered"):
        function = getattr(semantic_reach, name)
        assert function("some_gate", "declined") is None or function("some_gate") is None


def test_the_runtime_never_reads_the_recorder_back_to_make_a_decision() -> None:
    """ENFORCED. `core.semantic.reach.current()` is read by the observation seam and the tests, and
    by nothing in the turn path. A lane that branched on it would be routing on instrumentation."""
    repo_root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for relative in ("apps/vool_agent.py", "core/agent_runtime/research_tool_loop_facade.py",
                     "core/routing_decision_log.py"):
        text = (repo_root / relative).read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "semantic_reach.current(" in stripped or "reach.current(" in stripped:
                offenders.append(f"{relative}:{number}: {stripped}")
    assert not offenders, "the turn path must not read the reach recorder:\n" + "\n".join(offenders)


def test_observation_is_wrapped_so_a_fault_cannot_reach_the_turn() -> None:
    """ENFORCED. A recorder that raises must not take the turn with it.

    The exploding recorder is installed into the real ContextVar rather than patched over the
    module, so what is exercised is the same `record()` the turn path calls.
    """

    class _Exploding:
        def record(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("recorder on fire")

    token = semantic_reach._RECORDER.set(_Exploding())  # type: ignore[arg-type]
    try:
        for entry in (semantic_reach.claimed, semantic_reach.declined,
                      semantic_reach.blocked, semantic_reach.offered, semantic_reach.entered):
            assert entry("conductor") is None, "a recorder fault must be swallowed, not raised"
    finally:
        semantic_reach._RECORDER.reset(token)
