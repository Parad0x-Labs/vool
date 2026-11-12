"""MUTATION GAUNTLET — adversarial proof suite for claim alignment authority.

Attacks the mechanical matcher (core/council/claim_identity.py) across sixteen
semantic mutation classes. Every expectation is a TRUTH about the pair, not a
preference: DISPUTE means the council must treat them as conflicting claims,
UNRELATED means no alignment may be claimed (visible coverage gap), AGREE
means merging them is sound.
"""
from __future__ import annotations

import pytest

from core.council.claim_identity import (
    AGREE,
    UNRELATED,
    compare_semantic,
    extract_semantic_claim,
)


def verdict(a: str, b: str) -> tuple[str, str]:
    return compare_semantic(extract_semantic_claim(a), extract_semantic_claim(b))


def expect_dispute(kind: str):
    def check(pair: tuple[str, str]) -> None:
        v, k = verdict(*pair)
        assert (v, k) == ("dispute", kind), f"expected dispute/{kind}, got {v}/{k} for {pair}"
    return check


def expect_agree():
    def check(pair: tuple[str, str]) -> None:
        v, k = verdict(*pair)
        assert (v, k) == (AGREE, ""), f"expected agree, got {v}/{k} for {pair}"
    return check


def expect_unrelated():
    def check(pair: tuple[str, str]) -> None:
        v, k = verdict(*pair)
        assert (v, k) == (UNRELATED, ""), f"expected unrelated, got {v}/{k} for {pair}"
    return check


# 1 CAUSAL INVERSION ---------------------------------------------------------
def test_causal_inversion_stays_dispute():
    expect_dispute("causal_inversion")((
        "Cheap power causes desalination plants to appear.",
        "Desalination plants cause cheap power to appear.",
    ))


def test_causal_same_direction_agrees():
    expect_agree()((
        "Smoking causes cancer.",
        "Cigarette smoking causes cancer.",
    ))


# 2 NEGATION -----------------------------------------------------------------
def test_negation_is_dispute():
    expect_dispute("negation")((
        "The vaccine is safe for children.",
        "The vaccine is not safe for children.",
    ))


def test_double_negation_consistency():
    # both negated, same scope -> not a dispute on polarity
    v, _ = verdict("The system is not safe.", "This system isn't safe.")
    assert v != "dispute"  # "isn't" reads as the same negation, not a polarity flip
    v2, k2 = verdict("The system is not safe.", "The system is safe.")
    assert (v2, k2) == ("dispute", "negation")


# 3 QUANTIFIER ---------------------------------------------------------------
def test_quantifier_trio_all_distinct():
    pairs = [
        ("All requests fail under load.", "Some requests fail under load."),
        ("All requests fail under load.", "Most requests fail under load."),
        ("Some requests fail under load.", "Most requests fail under load."),
    ]
    for p in pairs:
        expect_dispute("quantifier")(p)


# 4 SAME NUMBER DIFFERENT FACT ----------------------------------------------
def test_same_number_different_fact_never_aligns():
    expect_unrelated()((
        "The latency is 45 ms.",
        "The memory footprint is 45 MB.",
    ))
    expect_unrelated()((
        "Uptime was 99 percent.",
        "CPU usage hit 99 percent.",
    ))


# 5 SAME ENTITY VALUE DIFFERENT ATTRIBUTE ------------------------------------
def test_same_value_different_attribute_distinct():
    expect_unrelated()((
        "The temperature is 22 celsius today.",
        "It feels like 22 celsius today.",
    ))


# 6 SYNONYMS (no dictionary authority — stated boundary) ---------------------
def test_synonyms_without_shared_vocabulary_do_not_merge():
    # Deterministic rule: no shared content token -> UNRELATED (coverage gap,
    # voter-visible). The system must NOT silently merge; that is the design.
    expect_unrelated()((
        "Latency increased after the deploy.",
        "Response time rose after the release.",
    ))


# 7 PARAPHRASE WITH NUMBER ANCHOR -------------------------------------------
def test_paraphrase_single_anchor_no_longer_merges():
    # TRIAL-3 RESIDUAL PIN TRADE-OFF: "Kafka costs ~$730/month" vs "estimated
    # monthly Kafka spend ~$730" share only {kafka}. Since the same shape also
    # merges "server latency 45ms" with "server timeout 45ms" (falsifying
    # defect), single-anchor pairs now stay UNRELATED — visible gap over
    # silent wrong merge. They route to voters as preference, never silently.
    expect_unrelated()((
        "Kafka costs approximately $730 month.",
        "Estimated monthly Kafka spend is around $730.",
    ))


def test_same_subject_same_value_different_attribute_never_agrees():
    expect_unrelated()((
        "The server latency is 45 ms.",
        "The server timeout is 45 ms.",
    ))


# 8 CLOSE BUT DIFFERENT VALUES ----------------------------------------------
def test_close_values_are_disputes():
    expect_dispute("value_mismatch")((
        "Kafka costs $730 per month.",
        "Kafka costs $750 per month.",
    ))
    # approximate vs exact: different commitment strength -> mismatch
    expect_dispute("value_mismatch")((
        "Kafka costs approximately $700 per month.",
        "Kafka costs exactly $730 per month.",
    ))
    # range vs point: different shape
    expect_dispute("value_mismatch")((
        "Kafka costs between $700 and $800 per month.",
        "Kafka costs $730 per month.",
    ))


# 9 CAUSAL STRENGTH ----------------------------------------------------------
def test_causal_strength_not_collapsed():
    expect_dispute("causal_strength")((
        "Cache misses correlate with latency spikes.",
        "Cache misses cause latency spikes.",
    ))
    expect_dispute("causal_strength")((
        "Churn correlates with poor onboarding.",
        "Poor onboarding contributes to churn.",
    ))


# 10 CONDITIONAL SCOPE -------------------------------------------------------
def test_conditional_scope_distinct():
    expect_dispute("conditional_scope")((
        "X is safe.",
        "X is safe only when replicas >= 3.",
    ))


# 11 TEMPORAL ----------------------------------------------------------------
def test_temporal_distinct():
    expect_dispute("temporal")((
        "The export job failed yesterday.",
        "The export job currently fails.",
    ))


# 12 MODALITY ----------------------------------------------------------------
def test_modality_distinct():
    expect_dispute("modality")((
        "The node can fail under load.",
        "The node will fail under load.",
    ))
    expect_dispute("modality")((
        "The migration will finish tonight.",
        "The migration did finish tonight.",
    ))


# 13 ENTITY ALIASING ---------------------------------------------------------
def test_aliasing_partial_by_prefix_tokens():
    # deepseek-v4-flash tokenizes into deepseek/v4/flash -> shares anchor with
    # "DeepSeek V4" -> alignable.
    v, k = verdict(
        "DeepSeek V4 costs $0.01 per request.",
        "deepseek-v4-flash costs $0.01 per request.",
    )
    assert v in {AGREE}, f"alias with shared tokens should align, got {v}/{k}"
    # PostgreSQL vs Postgres: only shared predicate token is "supports"
    # (connections is a unit word) -> SINGLE-ANCHOR GUARD keeps them UNRELATED.
    # Alias alignment through one shared predicate was sacrificed when the same
    # shape silently merged different attributes; visible gap wins.
    expect_unrelated()((
        "PostgreSQL supports 999 connections here.",
        "Postgres supports 999 connections here.",
    ))


# 14 CROSS-LANGUAGE ----------------------------------------------------------
def test_cross_language_does_not_silently_merge():
    expect_unrelated()((
        "El sistema falla bajo carga.",
        "The system fails under load.",
    ))


# 15 ADVERSARIAL HIGH OVERLAP ------------------------------------------------
def test_high_overlap_one_operator_flip():
    s = "The billing service processes 12000 events every day without loss."
    expect_dispute("negation")((
        s,
        "The billing service does not process 12000 events every day without loss.",
    ))
    expect_dispute("modality")((
        "The cluster will survive a broker failure during rebalancing.",
        "The cluster can survive a broker failure during rebalancing.",
    ))


# 16 LOW-OVERLOAD SEMANTIC EQUIVALENTS --------------------------------------
def test_low_overlap_equivalents_state_their_outcome():
    # number-anchored equivalent via MULTI-token anchor still merges
    expect_agree()((
        "Throughput peaked at 12000 events/sec on node7.",
        "node7 peak throughput was 12000 events/sec.",
    ))
    # purely verbal equivalents without shared vocabulary stay UNRELATED
    expect_unrelated()((
        "Our spend doubled year over year.",
        "Costs are twice what they were twelve months ago.",
    ))


# regression guards from trial #1 ---------------------------------------------
def test_trial_one_cases_still_hold():
    expect_agree()((
        "The scanned subtotal is 10.75 EUR because the items sum to it.",
        "The scanned subtotal is 10.75 EUR because items sum to it.",
    ))
    expect_dispute("value_mismatch")((
        "With the 8% fee the total is 11.61 EUR.",
        "Applying the 10% fee the total is 11.83 EUR.",
    ))


GAUNTLET_CASES = [
    (("Cheap power causes desalination plants to appear.",
      "Desalination plants cause cheap power to appear."), ("dispute", "causal_inversion")),
]
