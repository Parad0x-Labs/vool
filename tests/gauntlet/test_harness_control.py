"""The A/B/C/D/E control matrix, executed hermetically.

Meaning preserved exactly from the in-process version: every letter is still a full real
`/api/chat` turn, and the machine/ranking/capability facts are still produced by the real
`_device_probe`, `rank_provider_candidates(enforce_hardware_fit=True)`,
`provider_hardware_fit_rejection` and Autopilot. The only change is that they run in a child
interpreter, so the runtime state they establish cannot outlive the group.

The both-directions hardware-fit proof is kept: a capable machine accepts a 7.5 GB footprint and an
incapable one rejects the same capability. Without that pair, "everything ranks" would be
indistinguishable from "the fit gate was bypassed".
"""
from __future__ import annotations

import pytest

from tests.gauntlet.hermetic.protocol import EvidenceTurn
from tests.gauntlet.hermetic.runner import group_evidence

GROUP = "harness_control"
MARKER = "SENTINEL-WIRE-MARKER"


def _facts() -> dict:
    return group_evidence(GROUP)["facts"]


def _control(key: str) -> EvidenceTurn:
    return EvidenceTurn(group_evidence(GROUP)["controls"][key])


def _assert_chat(turn: EvidenceTurn, label: str) -> None:
    assert turn.status == 200, f"{label}: HTTP {turn.status}"
    assert MARKER in turn.reply, f"{label}: model wire not reached{turn.describe()}"
    assert "couldn't get a live model response" not in turn.low, label


def _assert_live(turn: EvidenceTurn, label: str) -> None:
    assert turn.status == 200, f"{label}: HTTP {turn.status}"
    assert turn.weather_requests == ["kaunas"], f"{label}: {turn.weather_requests}{turn.describe()}"


# --------------------------------------------------------------------- the probe is really in place


def test_the_deterministic_machine_is_installed() -> None:
    facts = _facts()
    assert facts["probe_ram_gb"] == facts["expected_ram_gb"]
    assert facts["probe_accelerator"] == "mps"
    assert facts["probe_driver"] == "gauntlet-fixture"


# ------------------------------------------------------------- the real fit logic is still deciding


def test_a_capable_machine_ranks_the_local_candidate() -> None:
    ranked = _facts()["ranked"]
    assert ranked, "no candidate survived ranking on a capable machine"
    assert any("qwen" in provider_id.lower() for provider_id in ranked), ranked


def test_an_incapable_machine_still_rejects_the_candidate() -> None:
    """The direction that proves the gate was determinized rather than bypassed."""
    facts = _facts()
    assert facts["fit_capable"] == "", f"capable machine wrongly rejected: {facts['fit_capable']!r}"
    assert facts["fit_incapable"] == "model_exceeds_hardware_budget", facts["fit_incapable"]


def test_the_fixed_probe_is_what_the_gate_reads() -> None:
    """With no explicit probe the gate falls through to `_device_probe()` -- the fixture must win."""
    facts = _facts()
    assert facts["fit_40g_via_installed_probe"] == "model_exceeds_hardware_budget"
    assert facts["fit_7g_via_installed_probe"] == ""


def test_capability_truth_is_derived_from_the_ranked_candidate() -> None:
    facts = _facts()
    assert facts["capability_provider_ids"] == sorted(facts["ranked"])
    assert facts["local_available"] is True


# ------------------------------------------------------------------------------ the control matrix


def test_control_A_normal_chat() -> None:
    _assert_chat(_control("A"), "A")


def test_control_B_live_data() -> None:
    _assert_live(_control("B"), "B")


def test_control_C_chat_after_live_data() -> None:
    _assert_live(_control("C_live"), "C/live")
    _assert_chat(_control("C_chat"), "C/chat")


def test_control_D_fresh_conversation_after_live_data_elsewhere() -> None:
    _assert_chat(_control("D"), "D")


@pytest.mark.parametrize(
    "order", ["ABCD", "DCBA", "BADC", "CDAB", "BBAA", "ADBC", "DBCA", "CABD"]
)
def test_control_E_every_order_in_one_process(order: str) -> None:
    """Order dependence was the whole symptom, so the orders are still the test."""
    for step in group_evidence(GROUP)["orders"][order]:
        turn = EvidenceTurn(step)
        label = str(step["label"])
        if turn.said.startswith("Get weather"):
            _assert_live(turn, label)
        else:
            _assert_chat(turn, label)


def test_control_repeats_without_drifting() -> None:
    """The same conversation shape, six times over, inside one runtime."""
    for step in group_evidence(GROUP)["repeats"]:
        turn = EvidenceTurn(step)
        label = str(step["label"])
        if turn.said.startswith("Get weather"):
            _assert_live(turn, label)
        else:
            _assert_chat(turn, label)


def test_the_whole_control_matrix_ran_on_one_runtime() -> None:
    """A/B/C/D/E only means anything if the letters shared a process."""
    evidence = group_evidence(GROUP)
    assert evidence["runtime_bootstraps"] == 1, evidence["runtime_bootstraps"]


def test_no_control_turn_hit_no_ranked_provider() -> None:
    """The explicit 0-`no_ranked_provider` requirement, asserted across every control turn."""
    evidence = group_evidence(GROUP)
    steps = (
        list(evidence["controls"].values())
        + [s for order in evidence["orders"].values() for s in order]
        + list(evidence["repeats"])
    )
    starved = [
        s["label"] for s in steps if "couldn't get a live model response" in str(s["low"])
    ]
    assert not starved, f"{len(starved)} turn(s) resolved no_ranked_provider: {starved[:6]}"
