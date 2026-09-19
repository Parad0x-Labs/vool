"""Every way admission can refuse has its own code, and none of them is chat. Proof 10.

The rule being pinned: **admission has no fallthrough.** A proposal either runs, or is refused with a
typed reason naming what was wrong with it. "We could not work out what this was, so treat it as
conversation" is the behaviour the semantic architecture exists to remove, and the way it comes back
is not a design decision but an unlabelled `return None` somewhere in a chain of checks. So the
refusals are enumerated, and the enumeration is asserted to be complete.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import FrozenInstanceError
from types import FunctionType

import pytest

from core.semantic.admission import admit, admit_all
from core.semantic.canonical_text import CanonicalText
from core.semantic.types import (
    REJECTION_REASONS,
    AdmissionResult,
    IntentProposal,
    PermissionRecord,
    ReasonCode,
    RequestShape,
    ResolutionOutcome,
    ResolutionState,
    ValidatedIntent,
)

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)


class _Contract:
    """A stand-in registered contract. Only the fields admission actually reads."""

    def __init__(
        self,
        *,
        intent: str = "test.operation",
        supported: bool = True,
        side_effect_class: str = "read_only",
        input_schema: dict | None = None,
        approval_requirement: str = "none",
        unsupported_reason: str = "",
    ) -> None:
        self.intent = intent
        self.description = "a disposable operation used by this test"
        self.supported = supported
        self.side_effect_class = side_effect_class
        self.input_schema = dict(input_schema or {"target": "string optional"})
        self.output_schema = {"value": "string"}
        self.approval_requirement = approval_requirement
        self.unsupported_reason = unsupported_reason


#: A real, registered, read-only intent. Tests that need an ADMIT use this and let the REAL policy
#: allow it -- there is deliberately no way to hand admission a substitute permission checker.
REAL_READ_ONLY_INTENT = "workspace.list_tree"


def refuse_via_the_real_policy(monkeypatch) -> list[str]:
    """Drive the REAL permission engine into refusing, and return the intents it was asked about.

    Patching `decide_tool_call` to return the engine's own `PermissionDecision` with a DENY effect
    is the only accurate way to test a refusal now: `admit` has no injectable checker, because an
    injectable authority seam is an optional authority seam.
    """
    import core.mode_permission_policy as policy_module

    asked: list[str] = []
    real = policy_module.decide_tool_call

    def _refuse(*, intent, arguments, task_id, source_context):
        asked.append(intent)
        decision = real(intent=intent, arguments=arguments, task_id=task_id, source_context=source_context)
        return policy_module.PermissionDecision(
            effect=policy_module.PermissionEffect.DENY, mode=decision.mode,
            actions=decision.actions, reason="refused by this test, through the real policy type",
        )

    monkeypatch.setattr(policy_module, "decide_tool_call", _refuse)
    return asked


def _lookup(contract: _Contract):
    return lambda name: contract if name == contract.intent else None


CANONICAL = CanonicalText.of("run the test operation on the target")


def _proposal(**overrides: object) -> IntentProposal:
    fields: dict = {
        "index": 0,
        "request_text": "run the test operation",
        "operation": "test.operation",
        "arguments": {},
    }
    fields.update(overrides)
    return IntentProposal(**fields)  # type: ignore[arg-type]


def test_a_malformed_proposal_is_typed_not_ignored() -> None:
    result = admit("not a proposal at all", canonical=CANONICAL)  # type: ignore[arg-type]
    assert result.reason is ReasonCode.MALFORMED_PROPOSAL
    assert result.admitted is False


def test_a_proposal_with_no_operation_is_malformed() -> None:
    result = admit(_proposal(operation="   "), canonical=CANONICAL)
    assert result.reason is ReasonCode.MALFORMED_PROPOSAL


def test_an_unregistered_operation_is_refused_by_the_registry_not_guessed_at() -> None:
    result = admit(_proposal(operation="totally.invented"), canonical=CANONICAL)
    assert result.reason is ReasonCode.UNKNOWN_OPERATION
    assert "totally.invented" in result.detail


def test_a_span_from_another_message_is_a_bad_span() -> None:
    other = CanonicalText.of("a completely different message about Riga")
    stray = other.find("Riga")
    assert stray is not None
    contract = _Contract()
    result = admit(
        _proposal(spans=(stray,)),
        canonical=CANONICAL,
        contract_lookup=_lookup(contract),
    )
    assert result.reason is ReasonCode.BAD_SPAN


def test_a_dependency_on_a_later_clause_is_invalid() -> None:
    contract = _Contract()
    result = admit(
        _proposal(index=1, depends_on=(5,)),
        canonical=CANONICAL,
        resolved_clause_count=1,
        contract_lookup=_lookup(contract),
    )
    assert result.reason is ReasonCode.INVALID_DEPENDENCY


def test_a_dependency_on_an_unresolved_clause_is_invalid() -> None:
    contract = _Contract()
    result = admit(
        _proposal(index=2, depends_on=(1,)),
        canonical=CANONICAL,
        resolved_clause_count=1,  # only clause 0 is resolved, so clause 1 cannot be depended on
        contract_lookup=_lookup(contract),
    )
    assert result.reason is ReasonCode.INVALID_DEPENDENCY


def test_an_undeclared_argument_fails_expansion_rather_than_being_dropped() -> None:
    contract = _Contract(input_schema={"target": "string optional"})
    result = admit(
        _proposal(arguments={"target": "x", "sudo": True}),
        canonical=CANONICAL,
        contract_lookup=_lookup(contract),
    )
    assert result.reason is ReasonCode.ARGUMENT_EXPANSION_FAILED
    assert "sudo" in result.detail, "the refusal must name the argument that caused it"


def test_a_registered_but_unsupported_capability_is_refused() -> None:
    contract = _Contract(supported=False, unsupported_reason="no credential configured")
    result = admit(
        _proposal(),
        canonical=CANONICAL,
        contract_lookup=_lookup(contract),
    )
    assert result.reason is ReasonCode.CAPABILITY_UNSUPPORTED
    assert "no credential configured" in result.detail


@pytest.mark.parametrize("declared", ["", "unknown", "none", "unspecified"])
def test_a_contract_declaring_no_side_effect_class_cannot_be_authorized(declared: str) -> None:
    # Unspecified must never resolve to safe. That direction of mistake is the unrecoverable one.
    contract = _Contract(side_effect_class=declared)
    result = admit(
        _proposal(),
        canonical=CANONICAL,
        contract_lookup=_lookup(contract),
    )
    assert result.reason is ReasonCode.SIDE_EFFECT_CLASS_UNDECLARED


def test_a_permission_refusal_is_recorded_with_what_was_asked_for(monkeypatch) -> None:
    asked = refuse_via_the_real_policy(monkeypatch)
    result = admit(
        _proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL,
    )
    assert asked == [REAL_READ_ONLY_INTENT]
    assert result.reason is ReasonCode.PERMISSION_DENIED
    assert result.admitted is False
    assert result.intent is not None, "a refusal should still show what was asked for"
    assert result.permission is not None and result.permission.allowed is False


def test_a_policy_that_returns_something_other_than_a_decision_denies(monkeypatch) -> None:
    """A substitute for the engine is not the engine. Anything that is not its own decision type
    refuses, rather than being read for a truthy `allowed`."""
    import core.mode_permission_policy as policy_module

    class _LooksLikeADecision:
        allowed = True
        effect = "allow"
        actions = ()
        reason = "trust me"
        approval_request = None

    monkeypatch.setattr(policy_module, "decide_tool_call", lambda **_k: _LooksLikeADecision())
    result = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert result.admitted is False
    assert result.reason is ReasonCode.PERMISSION_DENIED
    assert "PermissionDecision" in (result.permission.detail if result.permission else "")


def test_the_permission_path_denies_when_the_engine_raises(monkeypatch) -> None:
    # A gate that fails open is not a gate.
    import core.mode_permission_policy as policy_module

    def _explode(**_kwargs: object):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(policy_module, "decide_tool_call", _explode)
    results = admit_all([_proposal(operation=REAL_READ_ONLY_INTENT)], canonical=CANONICAL)
    assert len(results) == 1
    assert results[0].admitted is False
    assert results[0].reason is ReasonCode.PERMISSION_DENIED


def test_every_rejection_reason_is_reachable_from_the_enumeration() -> None:
    # If a code exists it must mean something. A code nothing can produce is a code that will
    # eventually be used for whatever is convenient.
    assert ReasonCode.ADMITTED not in REJECTION_REASONS
    assert len(REJECTION_REASONS) == len(ReasonCode) - 1
    assert ReasonCode.RESOLVER_UNAVAILABLE in REJECTION_REASONS


def test_no_reason_code_spells_chat() -> None:
    # The load-bearing absence. Admission cannot say "this is just conversation".
    spellings = {code.value.lower() for code in ReasonCode}
    for banned in ("chat", "conversation", "smalltalk", "fallback", "default"):
        assert banned not in spellings
        assert not any(banned in value for value in spellings)


def test_no_resolution_state_spells_chat() -> None:
    spellings = {state.value.lower() for state in ResolutionState}
    assert "chat" not in spellings
    assert not any("chat" in value for value in spellings)


def test_an_admitted_result_cannot_be_built_without_a_permission_decision() -> None:
    intent = ValidatedIntent(
        operation="test.operation", request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent="test.operation",
    )
    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(admitted=True, reason=ReasonCode.ADMITTED, intent=intent, permission=None)


def test_an_admitted_result_cannot_be_built_from_a_refusal() -> None:
    intent = ValidatedIntent(
        operation="test.operation", request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent="test.operation",
    )
    refused = PermissionRecord.refusal(policy="test.policy")
    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(admitted=True, reason=ReasonCode.ADMITTED, intent=intent, permission=refused)


def test_an_allowing_permission_record_cannot_be_written_by_hand() -> None:
    """The root forgery, and the two the SECOND hostile review found after the first repair.

    Round one: `PermissionRecord(allowed=True, policy="fabricated.policy")` admitted with no policy
    consulted. Round two broke the fix twice more -- the mint token `_POLICY_GRANT` was importable,
    and `grant_from_policy_decision` accepted the engine's decision type FROM A CALLER, which is
    constructible. Neither exists now.
    """
    import core.semantic.types as types_module

    assert not hasattr(types_module, "_POLICY_GRANT"), "an importable mint token is a forgery kit"
    assert not hasattr(types_module, "grant_from_policy_decision"), (
        "accepting a caller-supplied decision object as authority was the second forgery"
    )

    hand_built = PermissionRecord(policy="fabricated.policy", operation="x", effect="allow")
    assert hand_built.allowed is False, "declaring 'allow' is not the same as having been allowed"

    with pytest.raises(ValueError, match="issued"):
        PermissionRecord(policy="f", operation="x", effect="allow", grant=object())

    # A hand-built grant of the right TYPE is still not one this module issued.
    forged = types_module._Grant("x")
    assert PermissionRecord(policy="f", operation="x", effect="allow", grant=forged).allowed is False


def test_a_caller_built_permission_decision_cannot_mint_a_grant() -> None:
    """The engine's own decision type is constructible, so accepting one is accepting a forgery."""
    import core.mode_permission_policy as policy_module

    built = policy_module.PermissionDecision(
        effect=policy_module.PermissionEffect.ALLOW,
        mode=policy_module.OperatingMode.MANUAL,
        actions=(),
        reason="constructed by the caller",
    )
    assert built.allowed is True, "the engine's own type really does say allow -- that is the point"
    # There is no function anywhere in the package that turns this into authority.
    import core.semantic.admission as admission_module

    assert not any(
        callable(getattr(admission_module, name, None)) and "grant" in name
        for name in dir(admission_module)
        if not name.startswith("__")
    ), "no admission helper may convert a caller-supplied decision into a grant"


def test_a_grant_is_bound_to_one_operation_and_cannot_be_carried_to_another() -> None:
    """The aliasing attack, at the record level.

    A grant obtained for `workspace.list_tree` must not authorize `machine.write_file`, however it
    is re-wrapped.
    """
    real = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert real.admitted is True and real.permission is not None
    grant = real.permission.grant

    carried = PermissionRecord(
        policy=real.permission.policy, operation="machine.write_file", effect="allow", grant=grant
    )
    assert carried.allowed is False, "a grant names its operation; it cannot be spent on another"
    assert real.permission.allows(REAL_READ_ONLY_INTENT) is True
    assert real.permission.allows("machine.write_file") is False


def test_an_admission_cannot_carry_a_grant_for_a_different_operation() -> None:
    """The call-level check must consult the intent, not merely trust the record's label."""
    from core.semantic import types as semantic_types

    grant = semantic_types._issue_grant(
        REAL_READ_ONLY_INTENT, semantic_types.arguments_digest({})
    )
    record = PermissionRecord(
        policy="p", operation=REAL_READ_ONLY_INTENT, effect="allow", grant=grant
    )
    different = ValidatedIntent(
        operation="machine.write_file", request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="workspace_write", tool_intent="machine.write_file",
    )
    ok, why = record.authorizes(different)
    assert ok is False and "different operation" in why

    matching_grant = semantic_types._issue_grant(
        REAL_READ_ONLY_INTENT, semantic_types.arguments_digest({})
    )
    matching_record = PermissionRecord(
        policy="p", operation=REAL_READ_ONLY_INTENT, effect="allow", grant=matching_grant
    )
    matching = ValidatedIntent(
        operation=REAL_READ_ONLY_INTENT, request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent=REAL_READ_ONLY_INTENT,
    )
    assert matching_record.authorizes(matching) == (True, ""), (
        "the negative case must not pass merely because all authorization is broken"
    )


def test_a_contract_alias_cannot_authorize_one_operation_under_another() -> None:
    """The aliasing attack end to end.

    A lookup that answers `evil.delete_everything` with the contract for `machine.inspect_specs`
    admitted the clause under the authorized capability's grant. The operation authorized has to be
    exactly the operation executed.
    """
    class _Alias:
        intent = "machine.inspect_specs"
        description = "an authorized capability"
        supported = True
        side_effect_class = "read_only"
        input_schema: dict = {}
        output_schema: dict = {}
        approval_requirement = "none"
        unsupported_reason = ""

    result = admit(
        IntentProposal(index=0, request_text="delete it all", operation="evil.delete_everything"),
        canonical=CANONICAL,
        contract_lookup=lambda _name: _Alias(),
    )
    assert result.admitted is False
    assert result.reason is ReasonCode.UNKNOWN_OPERATION
    assert "may not be authorized under another" in result.detail


def test_a_rejection_cannot_be_built_with_the_admitted_reason() -> None:
    with pytest.raises(ValueError):
        AdmissionResult(admitted=False, reason=ReasonCode.ADMITTED)


def test_admit_all_returns_one_result_per_clause_even_when_every_clause_fails() -> None:
    proposals = [
        _proposal(index=0, operation="totally.invented"),
        _proposal(index=1, operation="also.invented"),
        _proposal(index=2, operation="still.invented"),
    ]
    results = admit_all(proposals, canonical=CANONICAL)
    assert len(results) == len(proposals), "no clause may disappear"
    assert all(result.reason is ReasonCode.UNKNOWN_OPERATION for result in results)


def test_admit_all_survives_a_clause_that_raises_without_shortening_the_list() -> None:
    def _explodes(_name: str):
        raise RuntimeError("registry on fire")

    results = admit_all([_proposal(index=0), _proposal(index=1)], canonical=CANONICAL, contract_lookup=_explodes)
    assert len(results) == 2
    assert all(not result.admitted for result in results)
    assert any("registry on fire" in result.detail for result in results)


def test_a_partial_admission_is_visible_as_partial() -> None:
    results = admit_all(
        [_proposal(index=0, operation=REAL_READ_ONLY_INTENT),
         _proposal(index=1, operation="unregistered.thing")],
        canonical=CANONICAL,
    )
    assert [result.admitted for result in results] == [True, False]
    assert results[1].reason is ReasonCode.UNKNOWN_OPERATION


def test_a_dependent_is_refused_when_its_prerequisite_was_rejected() -> None:
    """A hostile-review finding. `admit_all` counted POSITIONS, so a clause whose prerequisite had
    been refused still admitted -- and would run on an answer that does not exist."""
    proposals = [
        IntentProposal(index=0, request_text="first", operation="does.not.exist"),
        IntentProposal(index=1, request_text="second", operation=REAL_READ_ONLY_INTENT, depends_on=(0,)),
    ]
    results = admit_all(proposals, canonical=CANONICAL)
    assert results[0].reason is ReasonCode.UNKNOWN_OPERATION
    assert results[1].admitted is False
    assert results[1].reason is ReasonCode.PREREQUISITE_FAILED
    assert "did not produce an answer" in results[1].detail


def test_a_dependent_admits_when_its_prerequisite_really_did() -> None:
    """The control. If the dependency check refused everything, the test above would be vacuous."""
    proposals = [
        IntentProposal(index=0, request_text="first", operation=REAL_READ_ONLY_INTENT),
        IntentProposal(index=1, request_text="second", operation=REAL_READ_ONLY_INTENT, depends_on=(0,)),
    ]
    results = admit_all(proposals, canonical=CANONICAL)
    assert results[0].admitted is True
    assert results[1].admitted is True, "a satisfied prerequisite must still let its dependent run"


def test_direct_admitted_result_construction_cannot_bypass_policy() -> None:
    """No caller-held permission value is an input to admitted-result construction."""
    granted = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert granted.admitted is True and granted.permission is not None
    assert granted.intent is not None
    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(
            admitted=True,
            reason=ReasonCode.ADMITTED,
            intent=granted.intent,
            permission=granted.permission,
        )
    assert not hasattr(AdmissionResult, "admit"), (
        "the old classmethod was the direct production-policy bypass"
    )


def test_caller_assembled_admitted_result_is_not_production_admission_authority() -> None:
    """The exact ``object.__new__``/``object.__setattr__`` attack stays outside authority."""
    legitimate = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert legitimate.admitted is True, "the production policy path must remain able to issue admission"

    forged = object.__new__(AdmissionResult)
    object.__setattr__(forged, "reason", ReasonCode.ADMITTED)
    object.__setattr__(forged, "intent", legitimate.intent)
    object.__setattr__(forged, "permission", legitimate.permission)
    object.__setattr__(forged, "detail", "caller assembled every visible field")
    # Even copying both real authority objects does not transfer their owner relationships.
    object.__setattr__(forged, "_occasion", legitimate._occasion)
    object.__setattr__(forged, "_outcome_claim", legitimate._outcome_claim)
    with pytest.raises((AttributeError, FrozenInstanceError)):
        object.__setattr__(forged, "admitted", True)

    assert forged.admitted is False, (
        "visible field state is not proof that the production admission occasion issued this object"
    )
    outcome = ResolutionOutcome(
        state=ResolutionState.UNRESOLVED,
        shape=RequestShape.SINGLE,
        canonical=CANONICAL,
        proposals=(_proposal(operation=REAL_READ_ONLY_INTENT),),
        admissions=(forged,),
    )
    assert outcome.admitted_count == 0 and outcome.rejected_count == 1, (
        "a manually assembled result crossed the authority boundary consumed by production"
    )


def test_admission_authority_exposes_no_mutable_registration_graph(monkeypatch) -> None:
    """Closure, helper-global, frame-alias, copy and registry injection attacks all stay false."""
    import core.semantic.admission as admission_module
    from core.semantic import types as semantic_types

    legitimate = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert legitimate.admitted is True and legitimate.intent is not None
    assert legitimate.permission is not None

    getter = AdmissionResult.admitted.fget
    assert getter is not None and getter.__closure__ is None, (
        "the authority verifier must expose no closure cell containing registration state"
    )
    assert not hasattr(admission_module, "admission_was_issued"), (
        "a monkeypatchable helper must not decide whether a result is authoritative"
    )

    forged = object.__new__(AdmissionResult)
    for name, value in (
        ("reason", ReasonCode.ADMITTED),
        ("intent", legitimate.intent),
        ("permission", legitimate.permission),
        ("detail", "forged"),
    ):
        object.__setattr__(forged, name, value)

    real_consultation = admission_module._consult_permission_policy

    forged_claim = semantic_types._new_admission_outcome_claim(forged)
    object.__setattr__(forged, "_outcome_claim", forged_claim)

    def caller_occasion(result, permission_consultation, claim):
        intent = result.intent
        permission = result.permission
        authorized = True
        frame = sys._getframe()
        _ = intent, permission, authorized, claim
        return frame

    # A caller can manufacture a real Python frame with every expected local. It is still not the
    # exact production `admit` code object, so it carries no authority.
    # Use the real admission module globals too. Code identity -- not a filename, function name or
    # globals label a caller can reproduce -- remains the discriminator.
    forged_frame_factory = FunctionType(caller_occasion.__code__, admission_module.__dict__)
    object.__setattr__(
        forged, "_occasion", forged_frame_factory(forged, real_consultation, forged_claim)
    )
    assert forged.admitted is False

    # Recreating the removed helper global cannot affect the sealed verifier.
    monkeypatch.setattr(admission_module, "admission_was_issued", lambda _result: True, raising=False)
    assert forged.admitted is False

    # Rebinding the production consultation helper and returning a fresh internally-issued grant
    # can make the raw admit body reach its materialization site, but the completed frame records
    # the substitute helper identity and therefore cannot attest a real production consultation.
    substitute_grant = semantic_types._issue_grant(
        REAL_READ_ONLY_INTENT, semantic_types.arguments_digest({})
    )
    substitute_record = PermissionRecord(
        policy="substitute", operation=REAL_READ_ONLY_INTENT, effect="allow", grant=substitute_grant
    )
    monkeypatch.setattr(
        admission_module, "_consult_permission_policy", lambda **_kwargs: substitute_record
    )
    substituted = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert substituted.reason is ReasonCode.ADMITTED, (
        "the attack must reach admitted-result materialization or it proves only setup refusal"
    )
    assert substituted.admitted is False, (
        "a monkeypatched helper stood in for the real production permission consultation"
    )

    # The permission grant registry remains intentionally caller-visible for coherence checks, but
    # adding an AdmissionResult to it is unrelated to admission authority.
    semantic_types._ISSUED_GRANTS.add(forged)
    try:
        assert forged.admitted is False
    finally:
        semantic_types._ISSUED_GRANTS.discard(forged)

    # Aliasing the real production frame and trying to overwrite its fast-local snapshot cannot
    # transfer its result-identity relationship.
    object.__setattr__(forged, "_occasion", legitimate._occasion)
    legitimate._occasion.f_locals["result"] = forged
    assert forged.admitted is False

    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(legitimate)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(legitimate)

    # Historical admission is not a reusable permission occasion: its grant was already consumed.
    assert legitimate.permission.consume_authorization(legitimate.intent)[0] is False


def test_admitted_result_is_authority_for_one_exact_resolution_outcome_only() -> None:
    """A historical success cannot be attached to a later turn, retry, copy, or fallback."""
    proposal = _proposal(operation=REAL_READ_ONLY_INTENT)
    result = admit(proposal, canonical=CANONICAL)
    assert result.admitted is True

    first = ResolutionOutcome(
        state=ResolutionState.PROPOSED,
        shape=RequestShape.SINGLE,
        canonical=CANONICAL,
        proposals=(proposal,),
        admissions=(result,),
    )
    assert first.admitted_count == 1 and first.rejected_count == 0

    reconstructed_proposal = _proposal(operation=REAL_READ_ONLY_INTENT)
    different_arguments = _proposal(
        operation=REAL_READ_ONLY_INTENT, arguments={"target": "different"}
    )
    different_operation = _proposal(operation="machine.inspect_specs")
    later_canonical = CanonicalText.of(CANONICAL.text)
    stale_outcomes = (
        # Same exact input objects but a later outcome/retry is still a new occasion.
        ResolutionOutcome(
            state=ResolutionState.PROPOSED,
            shape=RequestShape.SINGLE,
            canonical=CANONICAL,
            proposals=(proposal,),
            admissions=(result,),
        ),
        ResolutionOutcome(
            state=ResolutionState.PROPOSED,
            shape=RequestShape.SINGLE,
            canonical=CANONICAL,
            proposals=(reconstructed_proposal,),
            admissions=(result,),
        ),
        ResolutionOutcome(
            state=ResolutionState.PROPOSED,
            shape=RequestShape.SINGLE,
            canonical=CANONICAL,
            proposals=(different_arguments,),
            admissions=(result,),
        ),
        ResolutionOutcome(
            state=ResolutionState.PROPOSED,
            shape=RequestShape.SINGLE,
            canonical=CANONICAL,
            proposals=(different_operation,),
            admissions=(result,),
        ),
        ResolutionOutcome(
            state=ResolutionState.PROPOSED,
            shape=RequestShape.SINGLE,
            canonical=later_canonical,
            proposals=(proposal,),
            admissions=(result,),
        ),
        copy.copy(first),
    )
    assert all(outcome is not first for outcome in stale_outcomes)
    claim_frame = result._outcome_claim.gi_frame
    assert claim_frame is not None
    claim_frame.f_locals["claimed"] = (stale_outcomes[0], proposal, CANONICAL)
    assert all(outcome.admitted_count == 0 for outcome in stale_outcomes)
    assert all(outcome.rejected_count == 1 for outcome in stale_outcomes)

    # The claim generator is visible because Python has no private object graph. Calling it is not
    # an authority boundary: only the real outcome constructor can advance it.
    fresh_proposal = _proposal(operation=REAL_READ_ONLY_INTENT)
    fresh_result = admit(fresh_proposal, canonical=CANONICAL)
    fabricated_outcome = object.__new__(ResolutionOutcome)
    assert fresh_result._outcome_claim.send(
        (fabricated_outcome, fresh_proposal, CANONICAL)
    ) is None
    real_outcome = ResolutionOutcome(
        state=ResolutionState.PROPOSED,
        shape=RequestShape.SINGLE,
        canonical=CANONICAL,
        proposals=(fresh_proposal,),
        admissions=(fresh_result,),
    )
    assert real_outcome.admitted_count == 1


# --------------------------------------------------------------------------------------------
# The boundary that actually holds, tested as itself.
#
# A hostile reviewer imported `_issue_grant` and minted an allowing record. That is true, and the
# documentation that said otherwise was wrong: Python has no private, and a module-level mint
# function is importable by anyone who wants it. What was NEVER true is that such a value can cause
# an admission -- and that, not object unforgeability, is the security property. These tests pin the
# real boundary so nobody is tempted to redesign grants to make a sentence true again.
# --------------------------------------------------------------------------------------------


def test_an_imported_mint_can_build_an_allowing_record_and_still_cannot_admit() -> None:
    """The reviewer's exact attack, kept as a permanent regression -- both halves of it."""
    from core.semantic import types as semantic_types

    # Half one: yes, this works. Stated rather than denied.
    minted = semantic_types._issue_grant("machine.write_file")
    forged = PermissionRecord(
        policy="fabricated.policy", operation="machine.write_file", effect="allow", grant=minted
    )
    assert forged.allows("machine.write_file") is True, (
        "importing the mint really does produce an allowing record; the docs must not claim otherwise"
    )

    # Half two: it reaches nothing. `admit()` builds its own record from the real policy, and there
    # is no parameter through which this one could be supplied.
    import inspect

    parameters = set(inspect.signature(admit).parameters)
    assert not any("permission" in name for name in parameters), (
        f"admit() must expose no permission seam; it accepts {sorted(parameters)}"
    )

    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(
            admitted=True,
            reason=ReasonCode.ADMITTED,
            intent=ValidatedIntent(
                operation="machine.write_file", request_text="x", arguments={},
                canonical=CANONICAL, side_effect_class="workspace_write",
                tool_intent="machine.write_file",
            ),
            permission=forged,
        )

    result = admit(_proposal(operation="machine.write_file"), canonical=CANONICAL)
    assert result.admitted is False, "a forged record in the process must not admit anything"
    assert result.reason in REJECTION_REASONS
    assert result.permission is not minted
    assert result.permission is not forged


def test_a_minted_grant_still_cannot_authorize_a_second_operation() -> None:
    """Even holding the mint, a grant names one operation."""
    from core.semantic import types as semantic_types

    minted = semantic_types._issue_grant(REAL_READ_ONLY_INTENT)
    record = PermissionRecord(
        policy="p", operation="machine.write_file", effect="allow", grant=minted
    )
    assert record.allows("machine.write_file") is False
    assert record.allowed is False, "the record's own operation must match its grant's"


def test_caller_manipulation_of_the_issued_registry_cannot_admit() -> None:
    """Registry membership can make evidence look allowing; it is not an admission input."""
    from core.semantic import types as semantic_types

    grant = semantic_types._Grant(
        REAL_READ_ONLY_INTENT, semantic_types.arguments_digest({})
    )
    semantic_types._ISSUED_GRANTS.add(grant)
    forged = PermissionRecord(
        policy="fabricated", operation=REAL_READ_ONLY_INTENT, effect="allow", grant=grant
    )
    assert forged.allowed is True, "the attack must actually manipulate the registry"
    intent = ValidatedIntent(
        operation=REAL_READ_ONLY_INTENT, request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent=REAL_READ_ONLY_INTENT,
    )
    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(
            admitted=True, reason=ReasonCode.ADMITTED, intent=intent, permission=forged
        )


def test_admission_reaches_the_real_policy_for_the_exact_operation() -> None:
    """The positive half: authority is created inside the production consultation, for the operation
    that is about to run, and the grant that comes back names exactly it."""
    result = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert result.admitted is True
    assert result.permission is not None
    assert result.permission.policy == "core.mode_permission_policy.decide_tool_call", (
        "the record must name the production policy it was minted from"
    )
    assert result.intent is not None
    assert result.permission.grant.operation == result.intent.tool_intent == REAL_READ_ONLY_INTENT


def test_a_truthy_object_that_answers_allows_is_not_a_permission_decision() -> None:
    """`AdmissionResult` used to accept anything truthy with an `allows` method."""

    class TruthyPermission:
        policy = "fake.policy"
        operation = REAL_READ_ONLY_INTENT
        effect = "allow"
        detail = ""
        allowed = True

        def allows(self, _operation):
            return True

    intent = ValidatedIntent(
        operation=REAL_READ_ONLY_INTENT, request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent=REAL_READ_ONLY_INTENT,
    )
    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(admitted=True, reason=ReasonCode.ADMITTED, intent=intent,
                        permission=TruthyPermission())


def test_a_grant_is_bound_to_the_arguments_the_policy_was_asked_about() -> None:
    """Same operation is not the same call.

    A grant obtained for `read_file(safe.txt)` is not authority for `read_file(/etc/shadow)` -- the
    policy never saw the second one.
    """
    from core.semantic import types as semantic_types

    grant = semantic_types._issue_grant("test.read", semantic_types.arguments_digest({"path": "safe.txt"}))
    record = PermissionRecord(policy="p", operation="test.read", effect="allow", grant=grant)

    def _intent(arguments):
        return ValidatedIntent(operation="test.read", request_text="x", arguments=arguments,
                               canonical=CANONICAL, side_effect_class="read_only",
                               tool_intent="test.read")

    ok, why = record.authorizes(_intent({"path": "safe.txt"}))
    assert ok is True and why == ""
    ok, why = record.authorizes(_intent({"path": "/etc/shadow"}))
    assert ok is False and "different arguments" in why

    with pytest.raises(ValueError, match="produced only inside"):
        AdmissionResult(admitted=True, reason=ReasonCode.ADMITTED,
                        intent=_intent({"path": "/etc/shadow"}), permission=record)


def test_permission_argument_identity_preserves_types_structure_and_map_semantics() -> None:
    """Typed calls never alias merely because JSON would stringify their keys or values."""
    from core.semantic import types as semantic_types

    distinct = (
        ({1: "same"}, {"1": "same"}),
        ({True: "same"}, {"True": "same"}),
        ({1: "same"}, {1.0: "same"}),
        ({True: "same"}, {1: "same"}),
        ({None: "same"}, {"null": "same"}),
        ({"value": 1}, {"value": "1"}),
        ({"value": 1}, {"value": 1.0}),
        ({"value": None}, {"value": "null"}),
        ({"outer": {1: ["same", {"leaf": True}]}},
         {"outer": {"1": ["same", {"leaf": True}]}}),
        ({"items": [1, "2"]}, {"items": (1, "2")}),
        ({"text": "\u00e9"}, {"text": "e\u0301"}),
    )
    for left, right in distinct:
        assert semantic_types.arguments_digest(left) != semantic_types.arguments_digest(right), (
            f"semantically distinct typed arguments collapsed: {left!r} vs {right!r}"
        )

    reordered_left = {
        "outer": {"beta": (2, None), "alpha": [1, True]},
        "tail": "same",
    }
    reordered_right = {
        "tail": "same",
        "outer": {"alpha": [1, True], "beta": (2, None)},
    }
    assert semantic_types.arguments_digest(reordered_left) == semantic_types.arguments_digest(reordered_right)

    operation = "test.typed_identity"
    grant = semantic_types._issue_grant(operation, semantic_types.arguments_digest({1: "same"}))
    record = PermissionRecord(policy="p", operation=operation, effect="allow", grant=grant)

    def _intent(arguments):
        return ValidatedIntent(
            operation=operation, request_text="x", arguments=arguments, canonical=CANONICAL,
            side_effect_class="read_only", tool_intent=operation,
        )

    assert record.authorizes(_intent({1: "same"})) == (True, ""), (
        "the exact typed arguments shown to policy must remain authorized"
    )
    authorized, why_not = record.authorizes(_intent({"1": "same"}))
    assert authorized is False and "different arguments" in why_not, (
        "a grant for an integer-keyed map authorized the string-keyed map"
    )


def test_a_grant_authorizes_one_occasion_and_cannot_be_replayed() -> None:
    """A permission answer describes one question asked once."""
    from core.semantic import types as semantic_types

    grant = semantic_types._issue_grant("test.read", semantic_types.arguments_digest({}))
    record = PermissionRecord(policy="p", operation="test.read", effect="allow", grant=grant)
    intent = ValidatedIntent(operation="test.read", request_text="x", arguments={},
                             canonical=CANONICAL, side_effect_class="read_only",
                             tool_intent="test.read")

    assert grant.spent is False
    assert record.consume_authorization(intent) == (True, "")
    assert grant.spent is True, "the admission must consume the grant it used"
    ok, why = record.consume_authorization(intent)
    assert ok is False and "already authorized" in why


def test_concurrent_same_occasion_replay_has_exactly_one_winner() -> None:
    """The grant's lock closes the old check-then-set race."""
    import threading

    from core.semantic import types as semantic_types

    grant = semantic_types._issue_grant("test.read", semantic_types.arguments_digest({}))
    record = PermissionRecord(policy="p", operation="test.read", effect="allow", grant=grant)
    intent = ValidatedIntent(
        operation="test.read", request_text="x", arguments={}, canonical=CANONICAL,
        side_effect_class="read_only", tool_intent="test.read",
    )
    class _CountingLock:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.entries = 0

        def __enter__(self):
            self.entries += 1
            return self._lock.__enter__()

        def __exit__(self, *args):
            return self._lock.__exit__(*args)

    counting_lock = _CountingLock()
    grant._lock = counting_lock
    barrier = threading.Barrier(3)
    results: list[bool] = []

    def _consume() -> None:
        barrier.wait(timeout=5)
        results.append(record.consume_authorization(intent)[0])

    threads = [threading.Thread(target=_consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
    assert counting_lock.entries == 2, "both consumers must pass through the grant-owned lock"
    assert sorted(results) == [False, True]


def test_the_real_admission_path_still_admits_and_spends_exactly_once() -> None:
    """The control for all three: the production path must still work, and each admission gets its
    own grant rather than sharing one."""
    first = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    second = admit(_proposal(operation=REAL_READ_ONLY_INTENT), canonical=CANONICAL)
    assert first.admitted is True and second.admitted is True
    assert first.permission is not None and second.permission is not None
    assert first.permission.grant is not second.permission.grant, (
        "two admissions must not share one occasion"
    )
    assert first.permission.grant.spent is True and second.permission.grant.spent is True
