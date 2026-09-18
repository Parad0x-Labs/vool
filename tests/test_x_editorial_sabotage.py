"""Sabotage proofs for the X editorial studio: every guard is load-bearing.

Each test neutralizes exactly ONE guard and asserts that the protected observable CHANGES —
i.e. the guard is what stands between the operator and the failure. If a refactor quietly
removes a guard, the matching test here fails and names it.
"""
from __future__ import annotations

import json

import pytest

from tests.test_x_editorial_engine import CLEAN_POST, CORPUS  # noqa: F401


@pytest.fixture()
def iso_home(tmp_path, monkeypatch):
    """Isolated VOOL_HOME (same contract as the engine pack's fixture)."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    from core import native_skill_library

    native_skill_library.reset_contract_caches()
    yield home
    native_skill_library.reset_contract_caches()


def _hostile_envelope() -> str:
    draft = dict(CLEAN_POST)
    draft["posts"] = [
        '94% of teams agree. "This changes everything." https://invented.example.com/x'
    ]
    return "===XDRAFT===\n" + json.dumps(draft) + "\n===XDRAFT-END==="


def test_S1_selection_rules_are_load_bearing(iso_home, monkeypatch) -> None:
    """SABOTAGE: neutralize the demand-signal rules → direct requests stop selecting."""
    from core import native_skill_library
    from core.tool_demand_signals import DemandSignals

    monkeypatch.setattr(
        "core.tool_demand_signals.resolve_demand_signals", lambda _text: DemandSignals()
    )
    selection = native_skill_library.select_native_skills(
        task_class="creative_ideation", user_text="write an X article"
    )
    assert "x-editorial-studio" not in {c.id for c in selection.selected}, (
        "the demand-signal rules are the ONLY thing selecting this skill; "
        "if selection survives their removal, selection is not running through the typed gate"
    )


def test_S2_claim_guard_is_load_bearing(iso_home, monkeypatch) -> None:
    """SABOTAGE: blind the claim extractor → a hostile provider's fabrications would be
    served as clean copy. The test PROVES the guard (not luck) prevents that."""
    from core import x_editorial

    monkeypatch.setattr(x_editorial, "extract_claims", lambda _text: ())
    app = x_editorial.apply_x_editorial_output(
        _hostile_envelope(), user_text="announce our release", state={}
    )
    assert app.compliant is True and app.record["copy_ready_served"] is True, (
        "with the claim guard removed the fabricated draft must validate clean — "
        "which is exactly why the guard must exist"
    )


def test_S3_voice_scope_isolation_is_load_bearing(iso_home, monkeypatch) -> None:
    """SABOTAGE: collapse scope resolution to global → a project-scoped turn silently gets
    the wrong (global) voice evidence. The swap must be observable, proving the scope guard
    is what keeps project evidence isolated."""
    from core import x_voice_profile

    x_voice_profile.add_samples(
        "global", ["Shipped it. Small diff, big difference."], source="operator"
    )
    x_voice_profile.add_samples(
        "project:proj-b",
        ["Deep dive: here is how the scheduler actually picks its next victim."],
        source="operator",
    )
    ctx = {"workspace": "proj-b"}

    honest = x_voice_profile.voice_segment_for_turn("make it sound like me", source_context=ctx)
    assert "scheduler actually picks" in honest, honest
    assert "Small diff, big difference" not in honest, "global leaked into a project scope"

    monkeypatch.setattr(x_voice_profile, "turn_scope", lambda _ctx: "global")
    sabotaged = x_voice_profile.voice_segment_for_turn("make it sound like me", source_context=ctx)
    assert "scheduler actually picks" not in sabotaged
    assert "Small diff, big difference" in sabotaged
    assert sabotaged != honest, (
        "scope collapse must change the served voice evidence; if it does not, "
        "scope isolation is not load-bearing"
    )


def test_S4_no_publish_body_law_is_load_bearing(iso_home) -> None:
    """SABOTAGE: strip the no-publish law from the contract body → the library must REFUSE
    the contract at load, never inject it."""
    from core.native_skill_library import contract_from_frontmatter, contract_violations

    real = None
    from core.native_skill_library import load_native_library

    library = load_native_library()
    for contract in library.contracts:
        if contract.id == "x-editorial-studio":
            real = contract
            break
    assert real is not None, "the shipped skill must load"

    stripped = contract_from_frontmatter(
        {
            "id": real.id,
            "version": real.version,
            "risk-class": real.risk_class,
            "capability-families": list(real.capability_families),
            "tool-intents": list(real.tool_intents),
            "permitted-tools": list(real.permitted_tools),
            "expected-outputs": list(real.expected_outputs),
            "verification": list(real.verification),
            "stopping-conditions": ["stop whenever the model feels like it"],
        },
        path=real.path,
        body="A body with the no-publish law and the no-files law quietly edited away.",
    )
    violations = contract_violations(stripped, known_skill_ids=frozenset({real.id}))
    assert violations, (
        "a contract without the no-publish/no-files body law must be refused at load"
    )
    assert any("body law" in v for v in violations), violations


def test_S5_response_edge_application_is_load_bearing(iso_home, monkeypatch) -> None:
    """SABOTAGE: make the response-edge transform an identity → hostile provider output is
    served completely unvalidated. The observable must change."""
    from core import x_editorial

    monkeypatch.setattr(
        x_editorial,
        "apply_x_editorial_output",
        lambda text, *, user_text, state: x_editorial.XEditorialApplication(
            text=text,
            changed=False,
            compliant=True,
            record={"bypassed": True},
        ),
    )
    app = x_editorial.apply_x_editorial_output(
        _hostile_envelope(), user_text="announce our release", state={}
    )
    assert app.record.get("bypassed") is True and "copy-ready withheld" not in app.text, (
        "with the response edge bypassed the hostile draft reaches the operator "
        "unvalidated — proving the seam (not the model) is the guard"
    )
