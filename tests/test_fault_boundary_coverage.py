"""The coverage registry is complete and honest: every boundary classified, every effect legal.

"100% coverage" here has one meaning: every inventoried user-facing failure boundary has a
defined, tested outcome and a documented recovery path -- never a claim that all future failures
are predictable. These tests pin that meaning structurally.
"""

from __future__ import annotations

import pytest

from core.faults.boundaries import BOUNDARIES, FailureBoundary, boundary_names
from core.faults.catalog import (
    _VALID_EFFECTS,
    all_codes,
    all_specs,
    export_catalog,
)


def test_every_boundary_declares_a_complete_recovery_path() -> None:
    """No silently unclassified rows: each boundary names owners, a code space (or says none),
    where it is mapped, what the user reads, and a concrete recovery."""
    assert len(BOUNDARIES) >= 15, "the delivery inventory named 15 boundaries; the registry shrank"
    for boundary in BOUNDARIES:
        assert isinstance(boundary, FailureBoundary)
        assert boundary.name and boundary.owners, boundary
        assert boundary.codespace.kind, f"{boundary.name}: a boundary with no code space must say so explicitly"
        assert boundary.codespace.codes, f"{boundary.name}: a code space with no codes is not a classification"
        assert boundary.mapped_at and boundary.user_surface and boundary.recovery, boundary


def test_boundary_names_are_unique_and_cover_the_delivery_inventory() -> None:
    names = list(boundary_names())
    assert len(names) == len(set(names))
    expected = {
        "startup", "update", "project_workspace", "filesystem_tool_execution", "permissions",
        "provider_credentials", "provider_network_limits", "model_availability",
        "price_budget_approvals", "wallet_payment", "contacts", "plugin_skill_loading",
        "attachments", "storage", "voice", "reporting",
    }
    assert expected <= set(names), f"inventory boundaries missing from the registry: {sorted(expected - set(names))}"


def test_registry_codespaces_that_claim_fault_codes_really_exist() -> None:
    """A boundary claiming `vool.fault.v1` codes must name codes the catalog actually declares.

    A code space may deliberately sit beside the catalog (kind says "vool.fault.v1 + something");
    such a space must still anchor on at least one real catalog code."""
    declared = set(all_codes())
    for boundary in BOUNDARIES:
        kind = boundary.codespace.kind
        if not kind.startswith("vool.fault.v1"):
            continue
        own = set(boundary.codespace.codes)
        assert own, boundary.name
        if kind.strip() == "vool.fault.v1":
            assert own <= declared, f"{boundary.name}: unknown catalog codes {own - declared}"
        else:
            anchored = own & declared
            assert anchored, f"{boundary.name}: a mixed code space anchored on no real catalog code"


def test_every_catalog_code_carries_a_legal_effect_state() -> None:
    for spec in all_specs():
        assert spec.effect in _VALID_EFFECTS, f"{spec.code}: effect {spec.effect!r} outside the closed vocabulary"
    exported = {fault["code"]: fault["effect"] for fault in export_catalog()["faults"]}
    assert all(exported.values()), "the export must carry an effect for every code"


@pytest.mark.parametrize("effect,expect_word", [
    ("none", "Nothing was sent"),
    ("contacted", "reached an external service"),
    ("uncertain", "unknown"),
    ("local", "local"),
])
def test_uncertain_outcomes_are_worded_as_unknown_never_as_safe(effect: str, expect_word: str) -> None:
    from tools.generate_error_book import _EFFECT_WORDS

    assert expect_word in _EFFECT_WORDS[effect]
    assert "nothing" not in _EFFECT_WORDS["uncertain"].lower(), (
        "the uncertain wording must never read as a guarantee that nothing happened"
    )


def test_the_unknown_outcome_families_declare_uncertain() -> None:
    """The goal's named examples as registry facts: a timeout, a failed broadcast and an
    unclassifiable failure can never promise nothing happened."""
    exported = {fault["code"]: fault["effect"] for fault in export_catalog()["faults"]}
    assert exported["timeout"] == "uncertain"
    assert exported["wallet_broadcast_failed"] == "uncertain"
    assert exported["unknown"] == "uncertain"


def test_pre_send_refusals_declare_no_effect() -> None:
    """Spend-cap and policy refusals happen BEFORE the wire: their effect state must say so."""
    exported = {fault["code"]: fault["effect"] for fault in export_catalog()["faults"]}
    for code in ("permission_denied", "wallet_limit_exceeded", "wallet_x402_cap_exceeded",
                 "credential_failure", "wallet_disabled"):
        assert exported[code] == "none", code
