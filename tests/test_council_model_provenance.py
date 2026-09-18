"""Model identity, family resolution and reviewer independence.

A council seat's model is replaceable; its FAMILY is the independence unit. Two seats
running aliases of one model — a dated snapshot, a provider-prefixed route, a quantized
or `:free` variant — are one model wearing two name tags, and a bench that counts them
as two reviewers has fabricated independence. These tests pin the alias collapses the
provenance module performs and the one law built on top of them: independence is
counted over distinct FAMILIES, never over distinct name strings.
"""

from __future__ import annotations

import pytest

from core.council.model_provenance import (
    ModelIdentity,
    ModelProvenanceError,
    ProvenanceRecord,
    collapse_by_family,
    independent_family_count,
    resolve_family,
)


class TestFamilyResolution:
    def test_dated_snapshots_are_aliases_of_the_base_model(self):
        assert resolve_family("gpt-4o-2024-05-13") == resolve_family("gpt-4o")

    def test_provider_prefixes_do_not_create_a_new_family(self):
        assert resolve_family("openai/gpt-4o") == resolve_family("gpt-4o")

    def test_provider_prefix_plus_date_is_still_one_family(self):
        assert (
            resolve_family("openai/gpt-4o-2024-05-13")
            == resolve_family("gpt-4o")
            == resolve_family("openai/gpt-4o")
        )

    def test_free_route_suffix_is_an_alias(self):
        assert (
            resolve_family("meta-llama/llama-3.1-8b-instruct:free")
            == resolve_family("llama-3.1-8b-instruct")
        )

    def test_dot_and_dash_decimal_forms_are_one_family(self):
        assert (
            resolve_family("anthropic/claude-3.5-sonnet")
            == resolve_family("claude-3-5-sonnet")
            == resolve_family("claude-3-5-sonnet-20240620")
        )

    def test_latest_suffix_is_an_alias(self):
        assert resolve_family("claude-3-5-sonnet-latest") == resolve_family(
            "claude-3-5-sonnet"
        )

    def test_bare_llama_name_resolves_to_its_canonical_provider(self):
        # The family carries the canonical provider so "who trained this" survives
        # a route that hid it.
        assert resolve_family("llama-3.1-8b-instruct").startswith("meta-llama/")
        assert resolve_family("llama-3.1-8b-instruct") == resolve_family(
            "meta-llama/llama-3.1-8b-instruct"
        )

    def test_distinct_models_stay_distinct_families(self):
        assert resolve_family("gpt-4o") != resolve_family("gpt-4o-mini")
        assert resolve_family("claude-3-5-sonnet") != resolve_family("claude-3-5-haiku")
        assert resolve_family("gpt-4o") != resolve_family("claude-3-5-sonnet")

    def test_resolution_is_case_insensitive_and_whitespace_tolerant(self):
        assert resolve_family("  OpenAI/GPT-4o ") == resolve_family("gpt-4o")

    def test_garbage_is_refused_not_guessed(self):
        with pytest.raises(ModelProvenanceError):
            resolve_family("")


class TestModelIdentity:
    def test_identity_carries_provider_model_family_and_harness(self):
        identity = ModelIdentity("openai", "gpt-4o", harness="app-chat")
        assert identity.provider == "openai"
        assert identity.model == "gpt-4o"
        assert identity.family == "openai/gpt-4o"
        assert identity.harness == "app-chat"

    def test_identity_family_is_derived_not_declared(self):
        # A caller cannot NAME a family into existence: passing one that disagrees
        # with the resolution of the model id is a construction error.
        identity = ModelIdentity("openai", "gpt-4o")
        assert identity.family == resolve_family("gpt-4o")
        with pytest.raises(ModelProvenanceError):
            ModelIdentity("openai", "gpt-4o", family="anthropic/claude-3-5-sonnet")

    def test_empty_provider_or_model_is_refused(self):
        with pytest.raises(ModelProvenanceError):
            ModelIdentity("", "gpt-4o")
        with pytest.raises(ModelProvenanceError):
            ModelIdentity("openai", "")

    def test_key_identifies_the_exact_requested_model(self):
        assert ModelIdentity("openai", "gpt-4o").key == "openai/gpt-4o"


class TestProvenanceRecord:
    def test_requested_and_actual_are_separate_facts(self):
        requested = ModelIdentity("openai", "gpt-4o", harness="app-chat")
        actual = ModelIdentity("openai", "gpt-4o-mini", harness="app-chat")
        record = ProvenanceRecord(requested=requested, actual=actual)
        assert record.requested is requested
        assert record.actual is actual

    def test_actual_none_means_unproven_not_echoed(self):
        requested = ModelIdentity("openai", "gpt-4o")
        record = ProvenanceRecord(requested=requested, actual=None)
        assert record.proven is False
        # The unproven record must never manufacture an actual identity out of the
        # request: what was asked for is not evidence of what answered.
        assert record.actual is None

    def test_proven_when_actual_identity_is_established(self):
        record = ProvenanceRecord(
            requested=ModelIdentity("openai", "gpt-4o"),
            actual=ModelIdentity("openai", "gpt-4o"),
        )
        assert record.proven is True

    def test_provider_family_and_harness_survive_on_both_sides(self):
        record = ProvenanceRecord(
            requested=ModelIdentity("openai", "gpt-4o", harness="app-chat"),
            actual=ModelIdentity("openai", "gpt-4o", harness="app-chat"),
        )
        assert record.requested.harness == "app-chat"
        assert record.actual.family == "openai/gpt-4o"


class TestIndependence:
    def test_aliases_of_one_family_count_as_one(self):
        seats = [
            ModelIdentity("openai", "gpt-4o"),
            ModelIdentity("openai", "gpt-4o-2024-05-13"),
        ]
        assert independent_family_count(seats) == 1

    def test_distinct_families_count_separately(self):
        seats = [
            ModelIdentity("openai", "gpt-4o"),
            ModelIdentity("openai", "gpt-4o-2024-05-13"),
            ModelIdentity("anthropic", "claude-3-5-sonnet"),
        ]
        assert independent_family_count(seats) == 2

    def test_route_variants_do_not_fabricate_independence(self):
        seats = [
            ModelIdentity("meta-llama", "llama-3.1-8b-instruct", harness="local"),
            ModelIdentity("meta-llama", "llama-3.1-8b-instruct:free", harness="openrouter"),
        ]
        assert independent_family_count(seats) == 1

    def test_provenance_records_count_by_actual_family_when_proven(self):
        records = [
            ProvenanceRecord(requested=ModelIdentity("openai", "gpt-4o")),
            ProvenanceRecord(
                requested=ModelIdentity("openai", "gpt-4o"),
                actual=ModelIdentity("anthropic", "claude-3-5-sonnet"),
            ),
        ]
        # One unproven request (counted by its requested family, labelled as such)
        # and one PROVEN actual that landed on a different family: two families.
        assert independent_family_count(records) == 2

    def test_collapse_by_family_groups_aliases_under_one_key(self):
        grouped = collapse_by_family(
            [
                ModelIdentity("openai", "gpt-4o"),
                ModelIdentity("openai", "gpt-4o-2024-05-13"),
                ModelIdentity("anthropic", "claude-3-5-sonnet"),
            ]
        )
        assert set(grouped) == {"openai/gpt-4o", "anthropic/claude-3-5-sonnet"}
        assert len(grouped["openai/gpt-4o"]) == 2
