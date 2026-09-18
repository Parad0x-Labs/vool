from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import idna
import pytest

from core.routing_authority_v2 import (
    ENDPOINT_CANONICALIZATION_REVISION,
    IDNA_CANONICALIZER_IMPLEMENTATION,
    IDNA_CANONICALIZER_VERSION,
    PRODUCTION_INGRESS_GENERATION,
    AuthorityRecord,
    AuthorityState,
    BillingState,
    CandidateLocality,
    ContextAttestationV2,
    ContextEnvelopeV2,
    ContractValidationError,
    CostAttestationV2,
    CostChargeV2,
    CostConflictStatus,
    CostDimension,
    DataClassification,
    DeliveryState,
    ExecutableCandidateRefV2,
    ExecutionGeneration,
    FailureClass,
    FailureStage,
    OpaqueAuthorityRefV2,
    PayloadProvenanceEntryV1,
    PicoUSD,
    ProviderInvocationManifestV2,
    ProviderNetworkOperationPermitV2,
    ProviderOperation,
    ProviderPayloadProvenanceMapV1,
    ReceiptOutcome,
    RecoveryAction,
    RemoteAutoRulesV2,
    RemoteAutoRuleV2,
    RemoteRuleKind,
    RetryDisposition,
    RoutingAttemptPermitV2,
    RoutingFailureV2,
    RoutingIntentMode,
    RoutingIntentV2,
    RoutingPermissionSnapshotV2,
    RoutingPlanV2,
    RoutingReasonCode,
    RoutingReceiptV2,
    SpendReservationV2,
    SubjectBindingV2,
    TaskRequirementsV2,
    TurnRoutingEnvelopeV2,
    UnknownExternalAcknowledgementV2,
    UnknownExternalAttemptGrantV2,
    UnknownExternalAuthorityKind,
    UnknownExternalGrantScope,
    parse_authority_json,
    parse_authority_record,
)


def _digest(character: str) -> str:
    return character * 64


def _candidate(
    *,
    provider: str = "provider-a",
    model: str = "model-a",
    locality: CandidateLocality = CandidateLocality.REMOTE_PROVIDER,
    cost_digest: str | None = None,
) -> ExecutableCandidateRefV2:
    return ExecutableCandidateRefV2(
        provider_instance_id=provider,
        native_model_id=model,
        billing_scope_id="billing-main",
        credential_generation=7,
        provider_manifest_revision="manifest-r1",
        provider_manifest_digest=_digest("1"),
        endpoint_origin="https://api.example.test",
        endpoint_base_path="/v1",
        tls_policy_digest=_digest("2"),
        proxy_policy_digest=_digest("3"),
        redirect_policy_digest=_digest("4"),
        adapter_contract_revision="adapter-r1",
        adapter_contract_digest=_digest("5"),
        catalog_id="catalog-main",
        catalog_revision="catalog-r1",
        catalog_digest=_digest("6"),
        cost_attestation_digest=cost_digest or _digest("7"),
        capability_evidence_digest=_digest("8"),
        locality=locality,
        locality_evidence_digest=_digest("9"),
        local_artifact_digest=_digest("a") if locality is CandidateLocality.LOCAL_MACHINE else None,
    )


def _all_records() -> tuple[AuthorityRecord, ...]:
    subject = SubjectBindingV2(
        authenticated_subject_id="subject-1",
        authenticated_session_id="session-1",
        turn_id="turn-1",
        context_digest=_digest("a"),
        credential_generation=7,
    )
    intent = RoutingIntentV2(subject_binding_digest=subject.digest(), mode=RoutingIntentMode.AUTO)
    rule = RemoteAutoRuleV2(
        kind=RemoteRuleKind.EXACT_MODEL,
        provider_instance_id="provider-a",
        native_model_id="model-a",
    )
    rules = RemoteAutoRulesV2(configured=True, rules=(rule,))
    candidate_without_cost = _candidate()
    charge = CostChargeV2(
        dimension=CostDimension.REQUEST,
        pico_usd_per_unit=PicoUSD.parse("0.000001"),
    )
    cost = CostAttestationV2(
        candidate_binding_digest=candidate_without_cost.cost_binding_digest(),
        catalog_id="catalog-main",
        catalog_revision="catalog-r1",
        pricing_revision="pricing-r1",
        retrieved_at_unix_ms=1_000,
        valid_until_unix_ms=2_000,
        currency="USD",
        charges=(charge,),
        promotion_id=None,
        quota_id=None,
        provider_manifest_digest=_digest("1"),
        conflict_status=CostConflictStatus.CLEAR,
    )
    candidate = replace(candidate_without_cost, cost_attestation_digest=cost.digest())
    permission = RoutingPermissionSnapshotV2(
        subject_binding_digest=subject.digest(),
        routing_intent_digest=intent.digest(),
        remote_auto_rules_digest=rules.digest(),
        allowed_localities=(CandidateLocality.LOCAL_MACHINE, CandidateLocality.REMOTE_PROVIDER),
        remote_auto_candidate_digests=(candidate.digest(),),
    )
    requirements = TaskRequirementsV2(
        required_capabilities=("structured_output", "text"),
        minimum_context_tokens=2_000,
        expected_output_tokens=500,
        data_classification=DataClassification.PUBLIC,
        allowed_localities=(CandidateLocality.LOCAL_MACHINE, CandidateLocality.REMOTE_PROVIDER),
    )
    context = ContextEnvelopeV2(
        turn_id="turn-1",
        context_digest=_digest("a"),
        ordered_item_digests=(_digest("b"), _digest("c")),
        utf8_byte_length=123,
    )
    context_attestation = ContextAttestationV2(
        context_envelope_digest=context.digest(),
        context_policy_revision="context-r1",
        attestor_id="context-policy",
        attested_at_unix_ms=1_000,
        admitted=True,
        admitted_item_digests=(_digest("b"), _digest("c")),
        reason_codes=("policy_match",),
    )
    envelope = TurnRoutingEnvelopeV2(
        subject_binding_digest=subject.digest(),
        routing_intent_digest=intent.digest(),
        permission_snapshot_digest=permission.digest(),
        task_requirements_digest=requirements.digest(),
        context_attestation_digest=context_attestation.digest(),
    )
    plan = RoutingPlanV2(
        turn_routing_envelope_digest=envelope.digest(),
        permission_snapshot_digest=permission.digest(),
        ordered_candidate_digests=(candidate.digest(),),
        reason_codes=("remote_rule_match",),
    )
    attempt = RoutingAttemptPermitV2(
        permit_id="attempt-shadow-1",
        routing_plan_digest=plan.digest(),
        subject_binding_digest=subject.digest(),
        candidate_digest=candidate.digest(),
        operation=ProviderOperation.TEXT_GENERATION,
        attempt_ordinal=0,
        expires_at_unix_ms=2_000,
    )
    provenance_entry = PayloadProvenanceEntryV1(
        provider_field_path="/messages/0/content",
        source_kind="context-envelope",
        source_digest=context.digest(),
    )
    provenance = ProviderPayloadProvenanceMapV1(entries=(provenance_entry,))
    network_permit = ProviderNetworkOperationPermitV2(
        permit_id="network-shadow-1",
        routing_attempt_permit_digest=attempt.digest(),
        candidate_digest=candidate.digest(),
        payload_provenance_map_digest=provenance.digest(),
        operation=ProviderOperation.TEXT_GENERATION,
        expires_at_unix_ms=2_000,
    )
    spend = SpendReservationV2(
        reservation_id="spend-shadow-1",
        subject_binding_digest=subject.digest(),
        candidate_digest=candidate.digest(),
        operation=ProviderOperation.TEXT_GENERATION,
        reserved_amount=PicoUSD.parse("0.01"),
        expires_at_unix_ms=2_000,
    )
    acknowledgement = UnknownExternalAcknowledgementV2(
        acknowledgement_id="unknown-cost-ack-1",
        authority_kind=UnknownExternalAuthorityKind.EXPLICIT_UNKNOWN_EXTERNAL_COST,
        authenticated_subject_id="subject-1",
        turn_id="turn-1",
        context_digest=_digest("a"),
        exact_candidate_digest=candidate.digest(),
        credential_generation=7,
        operation=ProviderOperation.TEXT_GENERATION,
        expires_at_unix_ms=2_000,
    )
    grant = UnknownExternalAttemptGrantV2(
        grant_id="unknown-cost-grant-1",
        acknowledgement_digest=acknowledgement.digest(),
        authority_kind=UnknownExternalAuthorityKind.EXPLICIT_UNKNOWN_EXTERNAL_COST,
        scope=UnknownExternalGrantScope.SINGLE_EXACT_CANDIDATE_OPERATION,
        authenticated_subject_id="subject-1",
        turn_id="turn-1",
        context_digest=_digest("a"),
        exact_candidate_digest=candidate.digest(),
        credential_generation=7,
        operation=ProviderOperation.TEXT_GENERATION,
        expires_at_unix_ms=2_000,
    )
    manifest = ProviderInvocationManifestV2(
        manifest_id="manifest-shadow-1",
        candidate_digest=candidate.digest(),
        network_operation_permit_digest=network_permit.digest(),
        payload_digest=_digest("d"),
        payload_provenance_map_digest=provenance.digest(),
        context_digest=_digest("a"),
        operation=ProviderOperation.TEXT_GENERATION,
    )
    authority_ref = OpaqueAuthorityRefV2(
        authority_kind="routing-plan",
        authority_digest=plan.digest(),
    )
    failure = RoutingFailureV2(
        stage=FailureStage.PERMISSION,
        failure_class=FailureClass.POLICY_DENIED,
        delivery_state=DeliveryState.NOT_SENT,
        billing_state=BillingState.NOT_CHARGED,
        retry_disposition=RetryDisposition.FORBIDDEN,
        recovery_action=RecoveryAction.REMAIN_LOCAL,
        reason_code=RoutingReasonCode.REMOTE_RULE_DENIED,
        safe_message_key="routing.remote_rule_denied",
        authority_refs=(authority_ref,),
    )
    receipt = RoutingReceiptV2(
        receipt_id="receipt-shadow-1",
        turn_routing_envelope_digest=envelope.digest(),
        routing_plan_digest=plan.digest(),
        ordered_attempt_digests=(attempt.digest(),),
        selected_candidate_digest=None,
        failure_digest=failure.digest(),
        observed_cost_attestation_digest=cost.digest(),
        outcome=ReceiptOutcome.SHADOW_REJECTED,
        observed_at_unix_ms=2_000,
    )
    assert candidate.cost_binding_digest() == cost.candidate_binding_digest
    assert grant.matches_acknowledgement(acknowledgement)
    return (
        subject,
        intent,
        rule,
        rules,
        permission,
        requirements,
        context,
        context_attestation,
        envelope,
        candidate,
        charge,
        cost,
        plan,
        attempt,
        network_permit,
        spend,
        acknowledgement,
        grant,
        provenance_entry,
        provenance,
        manifest,
        authority_ref,
        failure,
        receipt,
    )


def test_every_phase0_contract_round_trips_through_the_strict_parser() -> None:
    records = _all_records()
    required_names = {
        "SubjectBindingV2",
        "RoutingIntentV2",
        "RoutingPermissionSnapshotV2",
        "TaskRequirementsV2",
        "ContextEnvelopeV2",
        "ContextAttestationV2",
        "TurnRoutingEnvelopeV2",
        "ExecutableCandidateRefV2",
        "CostAttestationV2",
        "RoutingPlanV2",
        "RoutingAttemptPermitV2",
        "ProviderNetworkOperationPermitV2",
        "SpendReservationV2",
        "UnknownExternalAcknowledgementV2",
        "UnknownExternalAttemptGrantV2",
        "ProviderPayloadProvenanceMapV1",
        "ProviderInvocationManifestV2",
        "RoutingFailureV2",
        "RoutingReceiptV2",
    }
    assert required_names <= {record.RECORD_TYPE for record in records}
    for record in records:
        reparsed = parse_authority_json(record.canonical_bytes())
        assert reparsed == record
        assert reparsed.digest() == record.digest()
        assert parse_authority_record(record.to_record()) == record
    assert len({record.HASH_DOMAIN for record in records}) == len({record.RECORD_TYPE for record in records})


def test_v1_schema_version_cannot_be_smuggled_as_bool_and_money_encoding_is_minimal() -> None:
    provenance = ProviderPayloadProvenanceMapV1(entries=())
    bool_version = provenance.to_record()
    bool_version["schema_version"] = True
    with pytest.raises(ContractValidationError, match="version"):
        parse_authority_record(bool_version)
    charge = CostChargeV2(dimension=CostDimension.REQUEST, pico_usd_per_unit=PicoUSD.from_picos(1))
    noncanonical_money = charge.to_record()
    noncanonical_money["pico_usd_per_unit"] = "01"
    with pytest.raises(ContractValidationError, match="shortest"):
        parse_authority_record(noncanonical_money)


def test_deep_immutability_copies_mutable_inputs_and_freezes_authority() -> None:
    capabilities = ["text", "structured_output"]
    requirements = TaskRequirementsV2(
        required_capabilities=capabilities,  # type: ignore[arg-type]
        minimum_context_tokens=1,
        expected_output_tokens=1,
        data_classification=DataClassification.PUBLIC,
        allowed_localities=[CandidateLocality.LOCAL_MACHINE],  # type: ignore[arg-type]
    )
    digest_before = requirements.digest()
    capabilities.append("mutated")
    assert requirements.required_capabilities == ("structured_output", "text")
    assert requirements.digest() == digest_before
    with pytest.raises(FrozenInstanceError):
        requirements.minimum_context_tokens = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("mode", "preference", "exact"),
    [
        (RoutingIntentMode.AUTO, "provider-a", None),
        (RoutingIntentMode.AUTO_PREFER, None, None),
        (RoutingIntentMode.AUTO_PREFER, "provider-a", _digest("a")),
        (RoutingIntentMode.PINNED, "provider-a", _digest("a")),
        (RoutingIntentMode.PINNED, None, None),
        (RoutingIntentMode.LOCAL_ONLY, None, _digest("a")),
        (RoutingIntentMode.PRIVATE, "provider-a", None),
    ],
)
def test_preference_permission_and_exact_pin_cannot_collapse(
    mode: RoutingIntentMode,
    preference: str | None,
    exact: str | None,
) -> None:
    with pytest.raises(ContractValidationError):
        RoutingIntentV2(
            subject_binding_digest=_digest("b"),
            mode=mode,
            preferred_provider_id=preference,
            exact_candidate_digest=exact,
        )


def test_remote_auto_unconfigured_and_empty_both_grant_zero_remote_permission_without_disabling_local() -> None:
    local = _candidate(locality=CandidateLocality.LOCAL_MACHINE)
    remote = _candidate()
    for rules in (RemoteAutoRulesV2(configured=False), RemoteAutoRulesV2(configured=True, rules=())):
        assert not rules.grants_any_remote_auto
        assert not rules.permits(remote)
        assert rules.permitted_remote_candidates((local, remote)) == ()
        assert rules.permitted_candidates((local, remote)) == (local,)
        assert rules.local_machine_inventory_allowed is True


def test_remote_auto_is_exact_intersection_and_provider_wildcard_is_explicit() -> None:
    exact = RemoteAutoRulesV2(
        configured=True,
        rules=(
            RemoteAutoRuleV2(
                kind=RemoteRuleKind.EXACT_MODEL,
                provider_instance_id="provider-a",
                native_model_id="model-a",
            ),
        ),
    )
    matching = _candidate(provider="provider-a", model="model-a")
    wrong_provider = _candidate(provider="provider-b", model="model-a")
    wrong_model = _candidate(provider="provider-a", model="model-b")
    local = _candidate(locality=CandidateLocality.LOCAL_MACHINE)
    assert exact.permitted_candidates((local, wrong_provider, matching, wrong_model)) == (local, matching)
    assert exact.permitted_remote_candidates((local, wrong_provider, matching, wrong_model)) == (matching,)
    wildcard = RemoteAutoRulesV2(
        configured=True,
        rules=(RemoteAutoRuleV2(kind=RemoteRuleKind.PROVIDER_WILDCARD, provider_instance_id="provider-a"),),
    )
    assert wildcard.permitted_candidates((local, wrong_provider, matching, wrong_model)) == (local, matching, wrong_model)
    with pytest.raises(ContractValidationError, match="wildcard"):
        RemoteAutoRuleV2(
            kind=RemoteRuleKind.PROVIDER_WILDCARD,
            provider_instance_id="provider-a",
            native_model_id="model-a",
        )


def test_local_only_intent_preserves_local_inventory_and_forbids_all_remote_candidates() -> None:
    local = _candidate(locality=CandidateLocality.LOCAL_MACHINE)
    remote = _candidate(provider="provider-a", model="model-a")
    rules = RemoteAutoRulesV2(
        configured=True,
        rules=(RemoteAutoRuleV2(kind=RemoteRuleKind.PROVIDER_WILDCARD, provider_instance_id="provider-a"),),
    )
    assert rules.permitted_candidates((remote, local), intent_mode=RoutingIntentMode.LOCAL_ONLY) == (local,)
    assert rules.permitted_remote_candidates((remote, local), intent_mode=RoutingIntentMode.LOCAL_ONLY) == ()


def test_candidate_identity_binds_provider_model_endpoint_adapter_manifest_and_cost() -> None:
    candidate = _candidate()
    mutations = (
        replace(candidate, provider_instance_id="provider-b"),
        replace(candidate, native_model_id="model-b"),
        replace(candidate, endpoint_origin="https://other.example.test"),
        replace(candidate, endpoint_base_path="/v2"),
        replace(candidate, adapter_contract_revision="adapter-r2"),
        replace(candidate, adapter_contract_digest=_digest("b")),
        replace(candidate, provider_manifest_revision="manifest-r2"),
        replace(candidate, provider_manifest_digest=_digest("c")),
        replace(candidate, cost_attestation_digest=_digest("d")),
    )
    assert all(mutated.digest() != candidate.digest() for mutated in mutations)
    assert _candidate(provider="provider-a", model="shared").digest() != _candidate(provider="provider-b", model="shared").digest()


def test_endpoint_authority_canonicalizes_scheme_host_default_port_idna_and_trailing_slash() -> None:
    candidate = _candidate()
    equivalent_origins = (
        "HTTPS://Example.COM",
        "https://example.com",
        "https://example.com:443",
        "https://example.com/",
    )
    equivalents = tuple(replace(candidate, endpoint_origin=origin) for origin in equivalent_origins)
    assert {item.endpoint_origin for item in equivalents} == {"https://example.com"}
    assert len({item.digest() for item in equivalents}) == 1
    assert replace(candidate, endpoint_origin="https://BÜCHER.example").endpoint_origin == "https://xn--bcher-kva.example"
    assert replace(candidate, endpoint_base_path="/v1/").digest() == candidate.digest()


def test_endpoint_authority_accepts_only_canonical_ipv4_and_canonicalizes_ipv6() -> None:
    candidate = _candidate()
    ipv4 = replace(candidate, endpoint_origin="https://127.0.0.1:443")
    assert ipv4.endpoint_origin == "https://127.0.0.1"
    expanded_ipv6 = replace(candidate, endpoint_origin="https://[2001:0db8:0:0:0:0:0:1]:443")
    compressed_ipv6 = replace(candidate, endpoint_origin="https://[2001:db8::1]")
    assert expanded_ipv6.endpoint_origin == "https://[2001:db8::1]"
    assert expanded_ipv6.digest() == compressed_ipv6.digest()


@pytest.mark.parametrize(
    "numeric_host",
    (
        "999.1.1.1",
        "256.256.256.256",
        "127.1",
        "01.02.03.04",
        "1.2.3",
        "1.2.3.4.5",
        "2130706433",
        "0177.0.0.1",
        "0x7f.0.0.1",
        "0o177.0.0.1",
        "0b1111111.0.0.1",
        "１２７.０.０.１",
        "127.0.0.1.",
    ),
)
def test_endpoint_authority_rejects_invalid_ipv4_like_hosts_instead_of_treating_them_as_dns(
    numeric_host: str,
) -> None:
    with pytest.raises(ContractValidationError, match="endpoint_origin"):
        replace(_candidate(), endpoint_origin=f"https://{numeric_host}")


def test_strict_nontransitional_idna_preserves_distinct_authority_and_canonical_equivalence() -> None:
    candidate = _candidate()
    sharp_s = replace(candidate, endpoint_origin="https://faß.de")
    ascii_ss = replace(candidate, endpoint_origin="https://fass.de")
    assert sharp_s.endpoint_origin == "https://xn--fa-hia.de"
    assert sharp_s.digest() != ascii_ss.digest()

    equivalents = tuple(
        replace(candidate, endpoint_origin=origin)
        for origin in (
            "https://BÜCHER.example",
            "https://BU\u0308CHER.example",
            "https://xn--bcher-kva.example",
        )
    )
    assert {item.endpoint_origin for item in equivalents} == {"https://xn--bcher-kva.example"}
    assert len({item.digest() for item in equivalents}) == 1


@pytest.mark.parametrize(
    "mixed_case_alabel",
    (
        "XN--BCHER-KVA.example",
        "Xn--bcher-kva.example",
        "xN--bcher-kva.example",
        "xn--BCHER-kva.example",
        "example\u3002XN--BCHER-KVA",
    ),
)
def test_mixed_case_alabel_input_is_rejected_before_hostname_normalization(mixed_case_alabel: str) -> None:
    with pytest.raises(ContractValidationError, match=r"A-label.*lowercase"):
        replace(_candidate(), endpoint_origin=f"https://{mixed_case_alabel}")


def test_endpoint_canonicalizer_implementation_and_revision_are_exact_candidate_authority() -> None:
    candidate = _candidate()
    assert IDNA_CANONICALIZER_IMPLEMENTATION == "idna"
    assert IDNA_CANONICALIZER_VERSION == idna.__version__ == "3.18"
    assert candidate.endpoint_canonicalization_revision == ENDPOINT_CANONICALIZATION_REVISION
    assert candidate.to_record()["endpoint_canonicalization_revision"] == ENDPOINT_CANONICALIZATION_REVISION

    mutated_record = candidate.to_record()
    mutated_record["endpoint_canonicalization_revision"] = "idna2008-uts46-nontransitional-idna-3.19-v1"
    with pytest.raises(ContractValidationError, match="endpoint_canonicalization_revision is fixed"):
        parse_authority_record(mutated_record)


@pytest.mark.parametrize(
    "invalid_host",
    (
        "ab\u200dcd.example",
        "☃.example",
        "xn--",
        "xn--abc-",
        f"{'a' * 64}.example",
        ".".join(("a" * 63,) * 4),
    ),
)
def test_strict_idna_rejects_context_invalid_symbols_malformed_alabels_and_length_overflow(
    invalid_host: str,
) -> None:
    with pytest.raises(ContractValidationError, match="endpoint_origin"):
        replace(_candidate(), endpoint_origin=f"https://{invalid_host}")


@pytest.mark.parametrize(
    "origin",
    (
        "https://example.com:abc",
        "https://example.com:0",
        "https://example.com:65536",
        "https://example.com:",
        "https://user@example.com",
        "https://user:password@example.com",
        "https://example.com?query=1",
        "https://example.com#fragment",
        "https:///missing-host",
        "https://example..com",
        "https://example.com.",
        "https://[::1]evil.example",
        "https://[::1]]",
    ),
)
def test_endpoint_authority_rejects_invalid_or_ambiguous_origins(origin: str) -> None:
    with pytest.raises(ContractValidationError, match="endpoint_origin"):
        replace(_candidate(), endpoint_origin=origin)


@pytest.mark.parametrize("base_path", ("/v1/./chat", "/v1/../chat", "/v1//chat", "/v1/%2e/chat"))
def test_endpoint_authority_rejects_dot_empty_and_encoded_path_segments(base_path: str) -> None:
    with pytest.raises(ContractValidationError, match="endpoint_base_path"):
        replace(_candidate(), endpoint_base_path=base_path)


def test_different_endpoint_authority_remains_digest_distinct() -> None:
    candidate = replace(_candidate(), endpoint_origin="https://example.com")
    assert replace(candidate, endpoint_origin="https://example.com:444").digest() != candidate.digest()
    assert replace(candidate, endpoint_origin="http://example.com").digest() != candidate.digest()
    assert replace(candidate, endpoint_base_path="/v2").digest() != candidate.digest()


def test_subject_binding_context_is_authoritative_and_unicode_equivalence_is_stable() -> None:
    original = SubjectBindingV2(
        authenticated_subject_id="caf\u00e9",
        authenticated_session_id="session-1",
        turn_id="turn-1",
        context_digest=_digest("a"),
        credential_generation=1,
    )
    equivalent = replace(original, authenticated_subject_id="cafe\u0301")
    changed_context = replace(original, context_digest=_digest("b"))
    assert original.digest() == equivalent.digest()
    assert original.digest() != changed_context.digest()


def test_set_authority_canonicalizes_while_ordered_candidate_arrays_do_not() -> None:
    first = TaskRequirementsV2(
        required_capabilities=("vision", "text"),
        minimum_context_tokens=1,
        expected_output_tokens=1,
        data_classification=DataClassification.PUBLIC,
        allowed_localities=(CandidateLocality.REMOTE_PROVIDER, CandidateLocality.LOCAL_MACHINE),
    )
    second = replace(
        first,
        required_capabilities=("text", "vision"),
        allowed_localities=(CandidateLocality.LOCAL_MACHINE, CandidateLocality.REMOTE_PROVIDER),
    )
    assert first.digest() == second.digest()
    plan = RoutingPlanV2(
        turn_routing_envelope_digest=_digest("a"),
        permission_snapshot_digest=_digest("b"),
        ordered_candidate_digests=(_digest("c"), _digest("d")),
        reason_codes=("fit",),
    )
    assert plan.digest() != replace(plan, ordered_candidate_digests=tuple(reversed(plan.ordered_candidate_digests))).digest()


def test_display_only_labels_cannot_enter_authoritative_candidate_record() -> None:
    candidate = _candidate(model="vendor/free:free")
    record = candidate.to_record()
    record["display_name"] = "free"
    with pytest.raises(ContractValidationError, match="extra"):
        parse_authority_record(record)
    assert "display_name" not in candidate.to_record()


def test_free_suffix_has_no_cost_authority() -> None:
    candidate = _candidate(model="vendor/free:free")
    positive = CostAttestationV2(
        candidate_binding_digest=candidate.cost_binding_digest(),
        catalog_id="catalog-main",
        catalog_revision="r1",
        pricing_revision="p1",
        retrieved_at_unix_ms=1,
        valid_until_unix_ms=2,
        currency="USD",
        charges=(CostChargeV2(dimension=CostDimension.REQUEST, pico_usd_per_unit=PicoUSD.from_picos(1)),),
        promotion_id=None,
        quota_id=None,
        provider_manifest_digest=_digest("1"),
        conflict_status=CostConflictStatus.CLEAR,
    )
    assert candidate.native_model_id.endswith(":free")
    assert positive.verified_zero_cost is False


def test_unknown_external_is_exactly_bound_and_never_serializes_broader_authority() -> None:
    acknowledgement = UnknownExternalAcknowledgementV2(
        acknowledgement_id="ack-1",
        authority_kind=UnknownExternalAuthorityKind.EXPLICIT_UNKNOWN_EXTERNAL_COST,
        authenticated_subject_id="subject-1",
        turn_id="turn-1",
        context_digest=_digest("a"),
        exact_candidate_digest=_digest("b"),
        credential_generation=2,
        operation=ProviderOperation.TEXT_GENERATION,
        expires_at_unix_ms=10,
    )
    grant = UnknownExternalAttemptGrantV2(
        grant_id="grant-1",
        acknowledgement_digest=acknowledgement.digest(),
        authority_kind=UnknownExternalAuthorityKind.EXPLICIT_UNKNOWN_EXTERNAL_COST,
        scope=UnknownExternalGrantScope.SINGLE_EXACT_CANDIDATE_OPERATION,
        authenticated_subject_id=acknowledgement.authenticated_subject_id,
        turn_id=acknowledgement.turn_id,
        context_digest=acknowledgement.context_digest,
        exact_candidate_digest=acknowledgement.exact_candidate_digest,
        credential_generation=acknowledgement.credential_generation,
        operation=acknowledgement.operation,
        expires_at_unix_ms=acknowledgement.expires_at_unix_ms,
    )
    assert grant.matches_acknowledgement(acknowledgement)
    assert not replace(grant, context_digest=_digest("c")).matches_acknowledgement(acknowledgement)
    assert not replace(grant, credential_generation=3).matches_acknowledgement(acknowledgement)
    forbidden_authority_fields = {"auto", "free", "budget", "retry", "fallback", "race", "mux"}
    assert forbidden_authority_fields.isdisjoint(grant.to_record())
    with pytest.raises(ContractValidationError):
        UnknownExternalAcknowledgementV2(
            acknowledgement_id="ack-2",
            authority_kind="AUTO",  # type: ignore[arg-type]
            authenticated_subject_id="subject-1",
            turn_id="turn-1",
            context_digest=_digest("a"),
            exact_candidate_digest=_digest("b"),
            credential_generation=2,
            operation=ProviderOperation.TEXT_GENERATION,
            expires_at_unix_ms=10,
        )


def test_failure_contract_accepts_only_safe_message_keys_and_opaque_digests() -> None:
    with pytest.raises(ContractValidationError, match="safe_message_key"):
        RoutingFailureV2(
            stage=FailureStage.PERMISSION,
            failure_class=FailureClass.POLICY_DENIED,
            delivery_state=DeliveryState.NOT_SENT,
            billing_state=BillingState.NOT_CHARGED,
            retry_disposition=RetryDisposition.FORBIDDEN,
            recovery_action=RecoveryAction.REMAIN_LOCAL,
            reason_code=RoutingReasonCode.REMOTE_RULE_DENIED,
            safe_message_key="/private/path with prompt text",
            authority_refs=(),
        )


def test_phase0_generation_and_every_permit_shaped_record_are_non_executable() -> None:
    assert PRODUCTION_INGRESS_GENERATION is ExecutionGeneration.LEGACY_V1
    permit_shaped = tuple(
        record
        for record in _all_records()
        if isinstance(
            record,
            (
                RoutingAttemptPermitV2,
                ProviderNetworkOperationPermitV2,
                SpendReservationV2,
                UnknownExternalAttemptGrantV2,
                ProviderInvocationManifestV2,
                RoutingPlanV2,
                RoutingReceiptV2,
                TurnRoutingEnvelopeV2,
            ),
        )
    )
    assert permit_shaped
    assert all(record.to_record()["authority_state"] == AuthorityState.NON_EXECUTABLE_SHADOW.value for record in permit_shaped)
