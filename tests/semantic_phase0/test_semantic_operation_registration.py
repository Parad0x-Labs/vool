"""A newly registered capability is describable immediately and runnable only if allowed. Proof 8.

The two halves are opposite failures and both are real:

* A capability the registry knows about that the planner is never *told* about is a runtime that
  cannot grow -- every new tool needs someone to remember a vocabulary edit, and a plugin's tools
  can never be offered at all.
* A capability that becomes runnable by being registered is a runtime where installing something is
  the same as authorizing it.

So: register a disposable operation nobody has ever named, and assert it reaches the catalog with no
edit to any resolver vocabulary -- and that it still cannot execute without `decide_tool_call`.
"""
from __future__ import annotations

import pytest

from core import tool_registry
from core.conductor.registry import NodeContext, NotAuthorizedError, OperationSpec
from core.runtime_tool_contracts import RuntimeToolContract
from core.semantic.admission import admit
from core.semantic.canonical_text import CanonicalText
from core.semantic.operation_catalog import (
    catalog_entries,
    catalog_names,
    catalog_report,
    catalog_text,
    projected_operations,
)
from core.semantic.types import IntentProposal, ReasonCode

# Imported rather than discovered -- see `_fixtures` for why this is not a conftest.
from tests.semantic_phase0._fixtures import (
    block_outbound_network,
    keep_the_checkout_clean,
    make_agent_module,
    pin_the_signing_key_passphrase,
    reseal_network_after_function_fixtures,
)

NOVEL_INTENT = "probe.quantise_widget"
NOVEL_WORD = "quantise"


def _novel_contract(**overrides) -> RuntimeToolContract:
    fields = {
        "intent": NOVEL_INTENT,
        "description": "quantise a widget into discrete buckets for the operator",
        "tool_surface": "probe",
        "capability_id": "probe.quantise",
        "capability_claim": "quantises a widget",
        "supported": True,
        "unsupported_reason": "",
        "input_schema": {"widget": "string", "buckets": "integer optional"},
        "output_schema": {"buckets": "list", "residual": "number optional"},
        "side_effect_class": "read_only",
        "approval_requirement": "none",
        "timeout_policy": "10s",
        "retry_policy": "none",
        "artifact_emission": "none",
        "error_contract": "structured",
        "source": "plugin:probe",
    }
    fields.update(overrides)
    return RuntimeToolContract(**fields)  # type: ignore[arg-type]


@pytest.fixture
def registered_novel_capability():
    contract = _novel_contract()
    tool_registry.register(contract)
    try:
        yield contract
    finally:
        tool_registry.unregister(NOVEL_INTENT)


def test_the_novel_capability_is_absent_before_it_is_registered() -> None:
    # Without this the whole proof could pass against a name that was already there.
    assert NOVEL_INTENT not in catalog_names()
    assert NOVEL_WORD not in catalog_text(include_unsupported=True)


def test_registration_alone_puts_it_in_the_catalog(registered_novel_capability) -> None:
    assert NOVEL_INTENT in catalog_names()
    row = next(entry for entry in catalog_entries() if entry["name"] == NOVEL_INTENT)
    assert row["description"] == registered_novel_capability.description
    assert row["source"] == "plugin:probe"
    assert row["side_effect_class"] == "read_only"


def test_it_reaches_the_catalog_without_editing_any_resolver_vocabulary(registered_novel_capability) -> None:
    """The generalization claim, stated mechanically.

    No file under the declared routing-authority set mentions the new capability, and it is offered
    anyway. That is the difference between a registry and a hand-maintained list beside one.
    """
    from pathlib import Path

    from core.semantic.lexical_authority import ROUTING_AUTHORITY_FILES

    repo_root = Path(__file__).resolve().parents[2]
    mentions = [
        relative
        for relative in ROUTING_AUTHORITY_FILES
        if (repo_root / relative).exists()
        and NOVEL_WORD in (repo_root / relative).read_text(encoding="utf-8", errors="replace")
    ]
    assert not mentions, f"the novel capability was hard-coded into routing vocabulary: {mentions}"
    assert NOVEL_WORD in catalog_text(), "and yet it must still be offered"


def test_it_is_projected_into_an_operation_spec(registered_novel_capability) -> None:
    specs = {spec.name: spec for spec in projected_operations()}
    assert NOVEL_INTENT in specs
    spec = specs[NOVEL_INTENT]
    assert spec.tool_intent == NOVEL_INTENT
    assert spec.description == registered_novel_capability.description
    # `buckets` is declared without "optional"; `residual` carries it. Projected from the contract's
    # own words, not guessed.
    assert spec.required_result_fields == ("buckets",)
    assert spec.can_run_in_parallel is False, (
        "parallel safety is never inferred. read_only is a claim about side effects and says "
        "nothing about concurrency: a read-only tool can share a cursor, a rate limit or a cache."
    )
    assert spec.is_derived is False


def test_the_projection_invents_no_synonyms(registered_novel_capability) -> None:
    spec = next(s for s in projected_operations() if s.name == NOVEL_INTENT)
    # The description is carried verbatim. Nothing generates "bucketise", "discretise", "chunk".
    assert spec.description == registered_novel_capability.description
    for invented in ("bucketise", "discretise", "chunk", "split"):
        assert invented not in spec.description


def test_the_projection_cannot_expand_a_clause_into_arguments(registered_novel_capability) -> None:
    # The correct Phase-0 answer: turning a sentence into typed arguments needs a resolver reading
    # spans, or natural-language extraction. Guessing would be the hidden NLP this must not do, so
    # the clause fails CLOSED as unresolved instead.
    spec = next(s for s in projected_operations() if s.name == NOVEL_INTENT)
    assert spec.expand_arguments("quantise the widget into 4 buckets") == []


def test_registration_does_not_grant_execution_permission(registered_novel_capability, monkeypatch) -> None:
    """The load-bearing half. Being in the catalog is being describable, not being allowed.

    The refusal comes from the REAL policy, driven into refusing by patching `decide_tool_call`
    itself. There is deliberately no way to hand admission a substitute checker any more -- an
    injectable authority seam is an optional authority seam.
    """
    import core.mode_permission_policy as policy_module

    asked: list[str] = []
    real = policy_module.decide_tool_call

    def _watch_and_refuse(*, intent: str, arguments, task_id, source_context):
        asked.append(intent)
        decision = real(intent=intent, arguments=arguments, task_id=task_id, source_context=source_context)
        return policy_module.PermissionDecision(
            effect=policy_module.PermissionEffect.DENY, mode=decision.mode,
            actions=decision.actions, reason="refused by this test, through the real policy type",
        )

    monkeypatch.setattr(policy_module, "decide_tool_call", _watch_and_refuse)
    # The proposal satisfies the contract's OWN schema (`widget` is required by its prose), so the
    # only thing between it and running is the policy.
    result = admit(
        IntentProposal(index=0, request_text="quantise the widget", operation=NOVEL_INTENT, arguments={"widget": "the widget"}),
        canonical=CanonicalText.of("quantise the widget"),
    )
    assert result.admitted is False
    assert result.reason is ReasonCode.PERMISSION_DENIED
    assert asked == [NOVEL_INTENT], "the policy must have been asked about THIS intent"


def test_a_malformed_proposal_never_reaches_the_policy(registered_novel_capability, monkeypatch) -> None:
    """Check order is a law: arguments are validated against the contract's executable schema
    BEFORE the policy is consulted, so a call the runtime could never execute is refused as
    malformed and the policy is not asked -- and no approval token is ever consumed for it."""
    import core.mode_permission_policy as policy_module

    asked: list[str] = []
    real = policy_module.decide_tool_call

    def _watch(*, intent: str, arguments, task_id, source_context):
        asked.append(intent)
        return real(intent=intent, arguments=arguments, task_id=task_id, source_context=source_context)

    monkeypatch.setattr(policy_module, "decide_tool_call", _watch)
    result = admit(
        IntentProposal(index=0, request_text="quantise the widget", operation=NOVEL_INTENT, arguments={}),
        canonical=CanonicalText.of("quantise the widget"),
    )
    assert result.admitted is False
    assert result.reason is ReasonCode.ARGUMENT_EXPANSION_FAILED
    assert "widget" in result.detail
    assert result.permission is None
    assert asked == [], "a malformed call must be refused before the policy is asked"


def test_admission_reaches_the_real_permission_policy_for_a_novel_capability(
    registered_novel_capability,
) -> None:
    # Not a test double: the default path must find and call `decide_tool_call` itself.
    result = admit(
        IntentProposal(index=0, request_text="quantise the widget", operation=NOVEL_INTENT, arguments={"widget": "the widget"}),
        canonical=CanonicalText.of("quantise the widget"),
    )
    assert result.permission is not None
    assert result.permission.policy == "core.mode_permission_policy.decide_tool_call"


def test_a_registered_but_unsupported_capability_is_listed_yet_refuses_to_run() -> None:
    contract = _novel_contract(supported=False, unsupported_reason="no widget service configured")
    tool_registry.register(contract)
    try:
        # Listed, because "unsupported" and "not registered" must stay distinguishable to a reader.
        assert NOVEL_INTENT in catalog_names(include_unsupported=True)
        assert NOVEL_INTENT not in catalog_names(include_unsupported=False)

        # A projection carries no executable at all, so this refuses before it could even reach the
        # question of whether the capability is servable.
        spec = OperationSpec.from_contract(contract)
        with pytest.raises(NotAuthorizedError):
            spec.run(object(), NodeContext(run_tool_intent=lambda _payload: {"ok": True}))
    finally:
        tool_registry.unregister(NOVEL_INTENT)


def test_a_projection_has_no_executable_at_all(registered_novel_capability) -> None:
    spec = OperationSpec.from_contract(registered_novel_capability)
    with pytest.raises(NotAuthorizedError):
        spec.run(object(), NodeContext())  # nothing injected


def test_an_injected_runner_is_not_proof_of_authorization(registered_novel_capability) -> None:
    """The hostile-review finding, pinned.

    `NodeContext.run_tool_intent` is whatever the caller passed. Treating it as the permission-gated
    seam means "somebody handed us a function" stands in for "the policy said yes" -- which is
    `registration != authorization` reintroduced one layer down. The runner must never be called.
    """
    called: list[dict] = []

    class _Node:
        arguments = {"widget": "left"}

    spec = OperationSpec.from_contract(registered_novel_capability)
    with pytest.raises(NotAuthorizedError):
        spec.run(_Node(), NodeContext(run_tool_intent=lambda payload: called.append(payload) or {"ok": True}))
    assert called == [], "an injected runner must not be invoked by a description-only projection"


def test_unregistering_removes_it_from_the_catalog_again() -> None:
    tool_registry.register(_novel_contract())
    assert NOVEL_INTENT in catalog_names()
    tool_registry.unregister(NOVEL_INTENT)
    assert NOVEL_INTENT not in catalog_names()


def test_the_catalog_report_counts_builtins_and_plugins_apart(registered_novel_capability) -> None:
    report = catalog_report()
    assert report["by_source"]["plugin:probe"] == 1
    assert report["by_source"]["builtin"] >= 50
    assert report["total"] == sum(report["by_source"].values())
    assert report["supported"] + report["unsupported"] == report["total"]


def test_a_contract_with_no_intent_cannot_be_projected() -> None:
    with pytest.raises(ValueError, match="intent"):
        OperationSpec.from_contract(_novel_contract(intent="   "))
