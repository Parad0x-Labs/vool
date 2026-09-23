from __future__ import annotations

import itertools
import random
import re
from dataclasses import replace
from decimal import localcontext

import pytest

import core.routing_authority_v2.contracts as contracts_module
from core.routing_authority_v2 import (
    CandidateLocality,
    ContractValidationError,
    DataClassification,
    ExecutableCandidateRefV2,
    PicoUSD,
    PicoUSDValidationError,
    RemoteAutoRulesV2,
    RemoteAutoRuleV2,
    RemoteRuleKind,
    RoutingIntentMode,
    RoutingIntentV2,
    SubjectBindingV2,
    TaskRequirementsV2,
    typed_sha256,
)
from core.routing_authority_v2.money import MAX_PICO_USD


def _digest(character: str) -> str:
    return character * 64


def _candidate(*, provider: str = "provider-a", model: str = "model-a") -> ExecutableCandidateRefV2:
    return ExecutableCandidateRefV2(
        provider_instance_id=provider,
        native_model_id=model,
        billing_scope_id="billing-main",
        credential_generation=1,
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
        cost_attestation_digest=_digest("7"),
        capability_evidence_digest=_digest("8"),
        locality=CandidateLocality.REMOTE_PROVIDER,
        locality_evidence_digest=_digest("9"),
    )


def test_mutant_remote_rule_intersection_replaced_by_wildcard_is_killed() -> None:
    rules = RemoteAutoRulesV2(
        configured=True,
        rules=(
            RemoteAutoRuleV2(
                kind=RemoteRuleKind.EXACT_MODEL,
                provider_instance_id="provider-a",
                native_model_id="model-a",
            ),
        ),
    )
    assert rules.permitted_candidates(
        (
            _candidate(provider="provider-b", model="model-a"),
            _candidate(provider="provider-a", model="model-b"),
            _candidate(provider="provider-a", model="model-a"),
        )
    ) == (_candidate(provider="provider-a", model="model-a"),)


def test_mutant_provider_stripped_from_candidate_identity_is_killed() -> None:
    assert _candidate(provider="provider-a", model="shared").digest() != _candidate(
        provider="provider-b", model="shared"
    ).digest()


def test_mutant_binary_float_price_is_killed() -> None:
    with pytest.raises(PicoUSDValidationError):
        PicoUSD.parse(0.000001)


def test_mutant_positive_tiny_price_rounded_to_zero_is_killed() -> None:
    for source in (
        "0.0000000000001",
        "0.00000000000001",
        "0.000000000000001",
        "0.0000000000000001",
        "0.00000000000000001",
        "0.000000000000000001",
    ):
        assert PicoUSD.parse(source).picos == 1


def test_mutant_manifest_revision_ignored_in_candidate_digest_is_killed() -> None:
    candidate = _candidate()
    assert candidate.digest() != replace(candidate, provider_manifest_revision="manifest-r2").digest()


def test_mutant_auto_preference_promoted_to_exact_candidate_is_killed() -> None:
    with pytest.raises(ContractValidationError, match="forbids exact"):
        RoutingIntentV2(
            subject_binding_digest=_digest("a"),
            mode=RoutingIntentMode.AUTO_PREFER,
            preferred_native_model_id="model-a",
            exact_candidate_digest=_digest("b"),
        )


def test_mutant_context_digest_dropped_from_subject_binding_is_killed() -> None:
    subject = SubjectBindingV2(
        authenticated_subject_id="subject-1",
        authenticated_session_id="session-1",
        turn_id="turn-1",
        context_digest=_digest("a"),
        credential_generation=1,
    )
    assert subject.digest() != replace(subject, context_digest=_digest("b")).digest()


def test_mutant_mathematical_set_serializer_made_order_dependent_is_killed() -> None:
    baseline: str | None = None
    for capabilities in itertools.permutations(("text", "vision", "tools")):
        requirements = TaskRequirementsV2(
            required_capabilities=capabilities,
            minimum_context_tokens=1,
            expected_output_tokens=1,
            data_classification=DataClassification.PUBLIC,
            allowed_localities=(CandidateLocality.LOCAL_MACHINE,),
        )
        baseline = baseline or requirements.digest()
        assert requirements.digest() == baseline


def test_mutant_generic_hash_domain_reused_for_distinct_authority_is_killed() -> None:
    payload = b'{"schema_version":2}'
    assert typed_sha256("VOOL_ROUTING_INTENT_V2", payload) != typed_sha256("VOOL_SUBJECT_BINDING_V2", payload)


def test_money_arithmetic_property_holds_across_checked_random_values() -> None:
    generator = random.Random(0xA170)
    for _index in range(500):
        left = generator.randrange(0, 2**96)
        right = generator.randrange(0, 2**96)
        multiplier = generator.randrange(0, 1_000)
        assert (PicoUSD.from_picos(left) + PicoUSD.from_picos(right)).picos == left + right
        larger, smaller = max(left, right), min(left, right)
        assert (PicoUSD.from_picos(larger) - PicoUSD.from_picos(smaller)).picos == larger - smaller
        assert (PicoUSD.from_picos(left) * multiplier).picos == left * multiplier


def test_mutant_ambient_decimal_precision_affects_pico_conversion_is_killed() -> None:
    source = "340282366920938463463374607.431768211455"
    for precision in (6, 10, 28, 50, 100):
        with localcontext() as context:
            context.prec = precision
            assert PicoUSD.parse(source).picos == MAX_PICO_USD


def test_mutant_endpoint_authority_canonicalization_removed_is_killed() -> None:
    candidate = _candidate()
    equivalents = tuple(
        replace(candidate, endpoint_origin=origin)
        for origin in ("HTTPS://Example.COM", "https://example.com", "https://example.com:443")
    )
    assert len({item.digest() for item in equivalents}) == 1
    with pytest.raises(ContractValidationError, match="endpoint_origin"):
        replace(candidate, endpoint_origin="https://example.com:abc")


def test_mutant_invalid_numeric_ip_falls_through_to_dns_is_killed() -> None:
    candidate = _candidate()
    for host in ("999.1.1.1", "256.256.256.256", "127.1"):
        with pytest.raises(ContractValidationError, match="endpoint_origin"):
            replace(candidate, endpoint_origin=f"https://{host}")


def test_mutant_legacy_transitional_idna_mapping_is_killed() -> None:
    candidate = _candidate()
    sharp_s = replace(candidate, endpoint_origin="https://faß.de")
    ascii_ss = replace(candidate, endpoint_origin="https://fass.de")
    assert sharp_s.endpoint_origin == "https://xn--fa-hia.de"
    assert sharp_s.digest() != ascii_ss.digest()
    for host in ("ab\u200dcd.example", "☃.example"):
        with pytest.raises(ContractValidationError, match="endpoint_origin"):
            replace(candidate, endpoint_origin=f"https://{host}")


def test_mutant_mixed_case_alabel_silently_lowercased_is_killed() -> None:
    candidate = _candidate()
    for host in (
        "XN--BCHER-KVA.example",
        "Xn--bcher-kva.example",
        "xN--bcher-kva.example",
        "xn--BCHER-kva.example",
        "example\u3002XN--BCHER-KVA",
    ):
        with pytest.raises(ContractValidationError, match=r"A-label.*lowercase"):
            replace(candidate, endpoint_origin=f"https://{host}")
    assert replace(candidate, endpoint_origin="https://xn--bcher-kva.example").endpoint_origin == (
        "https://xn--bcher-kva.example"
    )


def test_mutant_unsupported_canonicalizer_version_accepted_as_equivalent_authority_is_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate()
    monkeypatch.setattr(contracts_module.idna, "__version__", "3.19")
    # The gate must refuse ANY version other than the one the contracts pin -- the pattern is
    # built from the pin constant so a bump cannot leave this test guarding the old version.
    expected = re.escape(f"idna=={contracts_module.IDNA_CANONICALIZER_VERSION}")
    with pytest.raises(ContractValidationError, match=f"canonicalization requires {expected}"):
        replace(candidate, endpoint_origin="https://example.com")


def test_mutant_remote_rule_helper_filters_local_candidates_is_killed() -> None:
    local = replace(
        _candidate(),
        locality=CandidateLocality.LOCAL_MACHINE,
        local_artifact_digest=_digest("a"),
    )
    remote = _candidate()
    for rules in (RemoteAutoRulesV2(configured=False), RemoteAutoRulesV2(configured=True, rules=())):
        assert rules.permitted_candidates((local, remote)) == (local,)
        assert rules.permitted_remote_candidates((local, remote)) == ()
