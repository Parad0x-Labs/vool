"""C23: the projection must summarise the authorities without becoming one.

Four ways a "trust view" quietly turns into a lie, each pinned here:

1. it merges the hard effect budgets with the soft cloud-token guide, so the
   operator reads one number and believes a guide will stop something;
2. it renders an unreadable source as an empty one, so "I could not look" and
   "there is nothing to worry about" become the same pixels;
3. it restates a source's verdict in its own words, so the receipt chain's
   consistency claim widens into a completeness claim on the way through;
4. it writes.

The adversarial case is the second: a projection is at its most dangerous exactly
when its inputs are broken, because that is when a reassuring default is most
likely to be produced and least likely to be questioned.
"""
from __future__ import annotations

import pytest

from core.command_registry import execute_command
from core.trust_projection import EVIDENCE_POINTERS, trust_projection


def test_the_soft_cloud_guide_is_never_inside_the_enforced_budgets() -> None:
    """Different authority, different consequence, different key."""
    payload = trust_projection()
    enforced = payload["sections"]["enforced_budgets"]
    soft = payload["sections"]["soft_guides"]

    assert enforced["enforced"] is True
    assert soft["enforced"] is False
    assert enforced["authority"] == "core.effect_budget"
    assert soft["authority"] == "core.user_preferences"

    names = {g["name"] for g in soft["guides"]}
    assert "daily_token_budget" in names
    rule_names = {r["rule"] for r in enforced["rules"]}
    assert not (names & rule_names), "the soft guide appeared as an enforced budget rule"
    for guide in soft["guides"]:
        assert guide["enforced"] is False
        assert "enforced_budgets" in guide["not_the_same_as"]


def test_there_is_no_helper_that_flattens_the_two_kinds_of_limit() -> None:
    """A merged accessor is how the distinction gets lost one caller at a time."""
    import core.trust_projection as mod

    merged = [
        name
        for name in dir(mod)
        if not name.startswith("_")
        and any(token in name.lower() for token in ("all_limit", "all_budget", "limits", "combined"))
    ]
    assert not merged, f"a flattening accessor exists: {merged}"


def test_an_unreadable_budget_store_never_renders_as_no_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adversarial case. Absent data must not read as permissive."""
    import core.effect_budget as budget

    class StoreCorruptError(RuntimeError):
        """The realistic shape: the store is there and cannot be read."""

    def _boom(*_a, **_k):
        raise StoreCorruptError("database disk image is malformed")

    monkeypatch.setattr(budget, "budget_status", _boom)
    payload = trust_projection()
    section = payload["sections"]["enforced_budgets"]

    assert section["available"] is False
    assert "malformed" in section["unavailable_reason"]
    # No shape a renderer could mistake for "you have no limits".
    assert "rules" not in section
    assert "configured_rule_count" not in section
    assert "no_rules_configured" not in section
    assert "enforced_budgets" in payload["unavailable_sections"]


def test_an_empty_store_and_an_unreadable_store_are_different_facts() -> None:
    """Both are legitimate; conflating them is not."""
    payload = trust_projection()
    section = payload["sections"]["enforced_budgets"]
    assert section["available"] is True
    # Stated explicitly, not inferred by a caller counting rows.
    assert section["no_rules_configured"] is (section["configured_rule_count"] == 0)


def test_the_receipt_boundary_is_carried_verbatim_not_restated() -> None:
    """The verifier owns the words; the projection may not paraphrase them."""
    from core.honesty_receipt import CHAIN_PROVEN_CLAIM, CHAIN_UNPROVEN_CLAIM

    section = trust_projection()["sections"]["receipt_chain"]
    assert section["proven"] == CHAIN_PROVEN_CLAIM
    assert section["not_proven"] == CHAIN_UNPROVEN_CLAIM
    assert section["completeness_proven"] is False
    assert "intact" not in section["proven"].lower()


def test_every_section_names_the_raw_evidence_it_summarised() -> None:
    """A projection that hides its source is asking to be trusted on its own."""
    payload = trust_projection()
    for key, section in payload["sections"].items():
        assert section["raw_evidence"] == EVIDENCE_POINTERS[key][1]
        assert section["authority"] == EVIDENCE_POINTERS[key][0]


def test_the_projection_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arm every mutating seam of the sources; the projection must not touch one."""
    import core.effect_budget as budget
    import core.security_events.store as sec

    def _forbidden(name):
        def _fn(*_a, **_k):
            raise AssertionError(f"the projection called the mutating seam {name}")

        return _fn

    for mod, attr in (
        (budget, "reserve_effect_units"),
        (budget, "consume_reservation"),
        (budget, "release_reservation"),
        (budget, "apply_operator_adjustment"),
        (sec, "record_security_event"),
    ):
        monkeypatch.setattr(mod, attr, _forbidden(f"{mod.__name__}.{attr}"))

    payload = trust_projection()
    assert payload["reads_only"] is True
    assert payload["is_authority"] is False


def test_every_source_down_is_a_fault_not_an_all_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command must refuse rather than return a clean-looking empty view."""
    import core.trust_projection as mod

    def _dead(section):
        def _fn(*_a, **_k):
            return mod._unavailable(section, RuntimeError("source down"))

        return _fn

    monkeypatch.setattr(mod, "_enforced_budgets", _dead("enforced_budgets"))
    monkeypatch.setattr(mod, "_soft_guides", _dead("soft_guides"))
    monkeypatch.setattr(mod, "_security_events", _dead("security_events"))
    monkeypatch.setattr(mod, "_receipt_chain", _dead("receipt_chain"))
    monkeypatch.setattr(mod, "_fault_vocabulary", _dead("fault_vocabulary"))

    env = execute_command("trust.projection", {})
    assert env.ok is False
    assert env.fault is not None and env.fault.code == "fault_validation"
    # It names WHICH sources it could not read, so the refusal is actionable.
    assert sorted((env.fault.detail or {}).get("unavailable_sections") or []) == sorted(
        EVIDENCE_POINTERS
    )
    assert "not a clean state" in env.summary


def test_it_runs_through_the_canonical_dispatch_seam_read_only() -> None:
    from core.command_registry import registry

    spec = registry().lookup("trust.projection")
    assert spec is not None
    assert spec.effects == "read_only"
    env = execute_command("trust.projection", {})
    assert env.ok is True
    assert sorted(env.data["sections"]) == sorted(EVIDENCE_POINTERS)


# ── the unavailable branch is load-bearing ───────────────────────────────────


def test_sabotage_making_unavailable_look_available_reds_a_named_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace `_unavailable` with a reassuring empty section and prove it bites.

    This is the mutation the fix exists to survive: if a broken source can present
    as a healthy one with nothing in it, every assertion above about "unreadable"
    is decoration.
    """
    import core.effect_budget as budget
    import core.trust_projection as mod

    monkeypatch.setattr(
        mod,
        "_unavailable",
        lambda section, exc: {"available": True, "rules": [], "configured_rule_count": 0,
                              "authority": "", "raw_evidence": ""},
    )
    monkeypatch.setattr(budget, "budget_status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

    section = mod.trust_projection()["sections"]["enforced_budgets"]
    assert section["available"] is True and section["rules"] == [], (
        "sabotage no-op: _unavailable is not what produces the unreadable shape"
    )
