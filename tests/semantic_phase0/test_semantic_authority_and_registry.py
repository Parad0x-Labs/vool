"""The authority ladder clamps fail-closed, and the resolver registry is a clean swappable seam."""
from __future__ import annotations

import pytest

from core.semantic import authority_mode as am
from core.semantic import resolver_registry as rr
from core.semantic.canonical_text import CanonicalText
from core.semantic.types import IntentProposal


class _StubResolver:
    def propose(self, canonical: CanonicalText, *, operations):
        return (IntentProposal(index=0, request_text=canonical.text, operation="unknown"),)


# -- authority ladder ---------------------------------------------------------


def test_default_is_off() -> None:
    assert am.resolve_semantic_authority_mode({}) is am.SemanticAuthorityMode.OFF


@pytest.mark.parametrize(
    "value,expected",
    [
        ("shadow", am.SemanticAuthorityMode.SHADOW),
        ("1", am.SemanticAuthorityMode.SHADOW),
        ("on", am.SemanticAuthorityMode.SHADOW),
        ("off", am.SemanticAuthorityMode.OFF),
        ("", am.SemanticAuthorityMode.OFF),
    ],
)
def test_alias_resolution(value: str, expected: am.SemanticAuthorityMode) -> None:
    assert am.resolve_semantic_authority_mode({"VOOL_SEMANTIC_RESOLVER": value}) is expected


def test_modes_above_the_implemented_rung_clamp_down() -> None:
    # Rung 1 implements SHADOW; asking for anything higher must return SHADOW, never the higher rung.
    for value in ("dual", "dual_run", "authoritative_with_fallback", "authoritative"):
        assert (
            am.resolve_semantic_authority_mode({"VOOL_SEMANTIC_RESOLVER": value})
            is am.SemanticAuthorityMode.SHADOW
        )


def test_unknown_value_falls_to_off_not_a_guess() -> None:
    assert (
        am.resolve_semantic_authority_mode({"VOOL_SEMANTIC_RESOLVER": "banana"})
        is am.SemanticAuthorityMode.OFF
    )


def test_no_backend_floors_to_off_even_when_shadow_requested() -> None:
    assert (
        am.resolve_semantic_authority_mode(
            {"VOOL_SEMANTIC_RESOLVER": "shadow"}, backend_available=False
        )
        is am.SemanticAuthorityMode.OFF
    )


def test_local_models_disabled_floors_to_off() -> None:
    assert (
        am.resolve_semantic_authority_mode(
            {"VOOL_SEMANTIC_RESOLVER": "authoritative"}, local_models_disabled=True
        )
        is am.SemanticAuthorityMode.OFF
    )


def test_clamp_never_exceeds_implemented() -> None:
    assert am.clamp_to_implemented(am.SemanticAuthorityMode.AUTHORITATIVE) is am.highest_implemented_mode()
    assert am.highest_implemented_mode() is am.SemanticAuthorityMode.SHADOW


def test_mode_predicates() -> None:
    assert not am.mode_runs_resolver(am.SemanticAuthorityMode.OFF)
    assert am.mode_runs_resolver(am.SemanticAuthorityMode.SHADOW)
    assert not am.mode_is_authoritative(am.SemanticAuthorityMode.SHADOW)
    assert am.mode_is_authoritative(am.SemanticAuthorityMode.AUTHORITATIVE)
    assert am.mode_is_authoritative(am.SemanticAuthorityMode.AUTHORITATIVE_WITH_FALLBACK)


# -- resolver registry --------------------------------------------------------


def test_registry_starts_and_ends_empty() -> None:
    rr.clear_resolver()
    try:
        assert rr.active_resolver() is None
        assert rr.has_resolver() is False
        resolver = _StubResolver()
        rr.register_resolver(resolver)
        assert rr.active_resolver() is resolver
        assert rr.has_resolver() is True
    finally:
        rr.clear_resolver()
    assert rr.active_resolver() is None


def test_registry_rejects_a_non_resolver() -> None:
    with pytest.raises(TypeError):
        rr.register_resolver(object())  # type: ignore[arg-type]
