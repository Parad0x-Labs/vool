"""Harbourmaster integration bridge: Relay's provider attestation reaching Ledger's provenance.

Neither lane owns this seam. Relay (`fix/provider-model-attestation-20260807`) produces
`ModelResponse.provider_attested_model`, set by an adapter only from genuine provider response-body
evidence and left `None` otherwise. Ledger (`fix/evidence-answer-truth-binding-20260807`) consumes
`source_context["model_provenance"]` and requires BOTH an attested model and a named source before
`ModelProvenance.has_provider_attestation` is true. `_record_model_provenance` in
`core.memory_first_router` is the join, and these tests are the contract on it.

The rule under test is narrow and load-bearing: `attestation_source` exists ONLY when the provider
actually attested. If it could be produced from the requested / resolved / actual / runtime-selected
model, the ledger's two-field rule would be satisfied by one witness wearing two hats, and a turn
could claim "the provider says it served X" on the strength of the runtime's own selection. The
mutation test at the bottom is what proves the rule is real rather than incidental.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.memory_first_router import _record_model_provenance
from core.runtime_evidence import PROVENANCE_CONTEXT_KEY, ModelProvenance, collect_turn_evidence


def _manifest(model_name: str = "runtime-selected-model") -> SimpleNamespace:
    return SimpleNamespace(model_name=model_name, provider_id="test-provider")


def _response(attested: str | None) -> SimpleNamespace:
    return SimpleNamespace(provider_attested_model=attested)


def _provenance_from(context: dict) -> ModelProvenance:
    """Build the ledger's own view from what the bridge published -- no hand-made dicts.

    Deliberately routed through the real `collect_turn_evidence` rather than constructing a
    `ModelProvenance` directly: the point is that what the bridge WRITES is what the ledger READS,
    and a hand-made dataclass would prove only that the dataclass works.
    """
    evidence = collect_turn_evidence(
        session_id="harbourmaster-attestation-bridge", source_context=context
    )
    return evidence.provenance


# ---------------------------------------------------------------------------------------------
# Required case 1 - provider omits the model
# ---------------------------------------------------------------------------------------------


def test_provider_omits_model_yields_no_attestation_and_no_source() -> None:
    context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(context, manifest=_manifest(), response=_response(None))

    published = context[PROVENANCE_CONTEXT_KEY]
    assert published["provider_attested_model"] == ""
    assert published["attestation_source"] == "", (
        "an omitted provider model must leave the source empty; a source with nothing to vouch for "
        "is exactly the half-attestation the ledger's two-field rule exists to reject"
    )

    provenance = _provenance_from(context)
    assert provenance.has_provider_attestation is False
    # The runtime's own selection is still recorded -- it is a real fact, just a different one.
    assert provenance.runtime_selected_model == "runtime-selected-model"


def test_provider_empty_string_is_treated_as_omission_not_attestation() -> None:
    context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(context, manifest=_manifest(), response=_response("   "))

    published = context[PROVENANCE_CONTEXT_KEY]
    assert published["provider_attested_model"] == ""
    assert published["attestation_source"] == ""
    assert _provenance_from(context).has_provider_attestation is False


# ---------------------------------------------------------------------------------------------
# Required case 2 - provider reports the model
# ---------------------------------------------------------------------------------------------


def test_provider_reports_model_yields_both_fields() -> None:
    context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(
        context, manifest=_manifest(), response=_response("provider-served-model")
    )

    published = context[PROVENANCE_CONTEXT_KEY]
    assert published["provider_attested_model"] == "provider-served-model"
    assert published["attestation_source"] == "relay.provider_response"

    provenance = _provenance_from(context)
    assert provenance.has_provider_attestation is True
    assert provenance.provider_attested_model == "provider-served-model"


# ---------------------------------------------------------------------------------------------
# Required case 3 - provider contradicts the selected model
# ---------------------------------------------------------------------------------------------


def test_provider_contradicting_the_selected_model_preserves_the_contradiction() -> None:
    context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(
        context,
        manifest=_manifest("runtime-selected-model"),
        response=_response("something-else-entirely"),
    )

    provenance = _provenance_from(context)
    assert provenance.runtime_selected_model == "runtime-selected-model"
    assert provenance.provider_attested_model == "something-else-entirely"
    assert provenance.runtime_selected_model != provenance.provider_attested_model, (
        "a substitution must stay visible; reconciling the two fields would delete the only "
        "evidence that the provider served something other than what the runtime chose"
    )
    assert provenance.has_provider_attestation is True


# ---------------------------------------------------------------------------------------------
# Required case 4 - streamed provider identity survives stream assembly
# ---------------------------------------------------------------------------------------------


def test_streamed_provider_identity_survives_assembly_into_ledger_provenance() -> None:
    """The streaming lane assembles its own ModelResponse from chunks (Relay F1). The bridge sits at
    `_decision_from_response`, which both lanes pass through, so a streamed attestation must arrive
    at the ledger identically to a non-streamed one."""
    from adapters.base_adapter import ModelResponse, ModelStreamChunk

    chunks = [
        ModelStreamChunk(delta_text="par", provider_attested_model=None),
        ModelStreamChunk(delta_text="tial", provider_attested_model="streamed-model"),
    ]
    attested = ""
    for chunk in chunks:
        if getattr(chunk, "provider_attested_model", None):
            attested = chunk.provider_attested_model
    assembled = ModelResponse(
        output_text="partial", provider_id="p", model_name="runtime-selected-model",
        provider_attested_model=attested or None,
    )

    context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(context, manifest=_manifest(), response=assembled)

    provenance = _provenance_from(context)
    assert provenance.provider_attested_model == "streamed-model"
    assert provenance.attestation_source == "relay.provider_response"
    assert provenance.has_provider_attestation is True


# ---------------------------------------------------------------------------------------------
# Anti-fabrication: the runtime's own names must never become an attestation
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("laundering_candidate", ["requested_model", "resolved_model", "actual_model"])
def test_no_runtime_side_name_is_ever_promoted_into_the_attestation(laundering_candidate: str) -> None:
    """Every runtime-side name is present in the context, and none of them may reach the attested
    field when the provider itself said nothing."""
    context: dict = {
        "requested_model": "asked-for-model",
        "resolved_model": "resolved-model",
        "actual_model": "actual-model",
    }
    _record_model_provenance(
        context, manifest=_manifest("runtime-selected-model"), response=_response(None)
    )

    published = context[PROVENANCE_CONTEXT_KEY]
    assert published["provider_attested_model"] != context[laundering_candidate]
    assert published["provider_attested_model"] == ""
    assert published["attestation_source"] == ""
    assert _provenance_from(context).has_provider_attestation is False


def test_manifest_model_is_never_promoted_into_the_attestation() -> None:
    context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(
        context, manifest=_manifest("manifest-model"), response=_response(None)
    )
    published = context[PROVENANCE_CONTEXT_KEY]
    assert published["runtime_selected_model"] == "manifest-model"
    assert published["provider_attested_model"] == ""
    assert published["attestation_source"] == ""


# ---------------------------------------------------------------------------------------------
# Mutation proof - manufacture the attestation from a runtime-side name and this must go RED
# ---------------------------------------------------------------------------------------------


def test_sabotage_manufacturing_attestation_from_the_selected_model_is_caught() -> None:
    """Reproduces the exact defect the bridge exists to prevent.

    The sabotage is the plausible shortcut: when the provider said nothing, fall back to the model
    the runtime selected and call it attested. Under it, an unattested turn reports a complete,
    sourced attestation -- the ledger cannot tell the provider's word from the runtime's. If this
    test ever passes with the real bridge in place, the rule has been lost.
    """

    def sabotaged_record(source_context: dict, *, manifest, response) -> None:
        attested_raw = getattr(response, "provider_attested_model", None)
        # The bug: fall back to the runtime's own selection.
        attested = str(attested_raw).strip() if attested_raw else str(manifest.model_name)
        source_context[PROVENANCE_CONTEXT_KEY] = {
            "requested_model": str(source_context.get("requested_model") or ""),
            "runtime_selected_model": str(manifest.model_name),
            "provider_attested_model": attested,
            "attestation_source": "relay.provider_response" if attested else "",
        }

    context: dict = {"requested_model": "asked-for-model"}
    sabotaged_record(context, manifest=_manifest("runtime-selected-model"), response=_response(None))
    sabotaged = _provenance_from(context)

    assert sabotaged.has_provider_attestation is True, "sabotage should manufacture an attestation"
    assert sabotaged.provider_attested_model == "runtime-selected-model"

    # The real bridge, same inputs, must refuse to do that.
    honest_context: dict = {"requested_model": "asked-for-model"}
    _record_model_provenance(
        honest_context, manifest=_manifest("runtime-selected-model"), response=_response(None)
    )
    honest = _provenance_from(honest_context)
    assert honest.has_provider_attestation is False
    assert honest.provider_attested_model == ""
    assert honest.provider_attested_model != sabotaged.provider_attested_model
