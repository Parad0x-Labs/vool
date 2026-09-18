"""The scenario-group registry -- deliberately small, and deliberately fixed.

The unit of process isolation is the GROUP, not the test. A registry with a hard cap is what stops
that drifting back to one child per assertion: bootstrapping the runtime costs seconds, and 22
children would be both slow and wrong, because a scenario whose turns are split across interpreters
is no longer testing continuity.

Each group is a function of no arguments that returns a JSON-serialisable evidence dict. It runs
INSIDE the child, after the infrastructure self-test has already passed.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

# MNEMOSYNE's target shape is roughly 5-7 groups. The cap is asserted by
# `test_hermetic_boundary.py::test_the_group_registry_cannot_drift_to_one_child_per_test`.
#
# Raised 9 -> 10 for `g13_state_hygiene`, deliberately and as a whole FAMILY, not as one more
# test: it asks what the conversation remembers the user wants, which G1 (does a follow-up reach
# the right lane?) does not cover. It has to run here because the defect it pins -- a composed
# prompt being persisted as `current_user_goal` -- only exists once the real seam composes that
# prompt; a test calling the adapter directly stays green with the fix reverted, which is exactly
# the vacuous shape this boundary exists to prevent.
MAX_GROUPS = 10


def _cross_chat() -> dict[str, Any]:
    """ALPHA, BETA and an ALPHA follow-up -- all on ONE bootstrapped runtime, by construction.

    Splitting these across children would make the isolation claim meaningless: two processes share
    no history, so "B did not see A" would be true of any implementation, including a broken one.
    Keeping them together is what makes the assertion about the product.
    """
    from tests.gauntlet import harness as H  # noqa: N812  (reads as the module name at every call site)

    alpha_marker = "ALPHA_731"
    beta_marker = "BETA_924"

    chat_a = H.Conversation("hermetic-alpha")
    chat_b = H.Conversation("hermetic-beta")

    turns: list[dict[str, Any]] = []

    def record(label: str, turn) -> None:
        turns.append(
            {
                "label": label,
                "session_id": turn.session_id,
                "status": turn.status,
                "reply": turn.reply,
                "prompt_seen_by_model": "\n".join(call.prompt for call in turn.model_calls),
            }
        )

    record("A1", chat_a.say(f"Remember this project code: {alpha_marker}. Acknowledge it."))
    record("B1", chat_b.say(f"Remember this project code: {beta_marker}. Acknowledge it."))
    record("A2", chat_a.say("What was the project code I gave you?"))
    record("B2", chat_b.say("What was the project code I gave you?"))
    record("A3", chat_a.say("Summarise everything we have discussed so far."))

    return {
        "alpha_marker": alpha_marker,
        "beta_marker": beta_marker,
        "alpha_session": chat_a.session_id,
        "beta_session": chat_b.session_id,
        "turns": turns,
        # Proof the group really shared one runtime: one bootstrap, one process.
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


def _weather_evidence() -> dict[str, Any]:
    """A live-data group: what the PLANNER asked the transports for, across several turns."""
    from tests.gauntlet import harness as H  # noqa: N812  (reads as the module name at every call site)

    convo = H.Conversation("hermetic-weather")
    first = convo.say("Get weather for Kaunas.")
    second = convo.say("Check the current weather in Kaunas and summarize it.")
    return {
        "first_weather_requests": first.weather_requests,
        "second_weather_requests": second.weather_requests,
        "second_planned": [(i["operation"], i["entity"]) for i in second.planned_subtasks],
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


def _mutation_probe() -> dict[str, Any]:
    """Reads a value straight out of production source, so an on-disk mutation is observable.

    This exists to prove the MUTATION MECHANISM across the process boundary, not to test a product
    behaviour: a parent-side monkeypatch cannot reach a child interpreter, so the only honest way to
    mutate for a child is to edit production source on disk and let the fresh import pick it up.
    """
    from core.runtime_paths import active_config_home_dir
    from tests.gauntlet import harness as H  # noqa: N812  (reads as the module name at every call site)

    return {
        "gauntlet_machine_ram_gb": H.GAUNTLET_MACHINE_RAM_GB,
        "config_home": str(active_config_home_dir()),
        "activity_window": _activity_window(),
        "pid": __import__("os").getpid(),
    }


def _activity_window() -> int:
    from core.runtime_evidence import _ACTIVITY_WINDOW

    return int(_ACTIVITY_WINDOW)


def _hang_forever() -> dict[str, Any]:
    """Deliberately never returns. Used only to prove the parent's timeout is real."""
    import time

    while True:
        time.sleep(3600)


from tests.gauntlet.hermetic import scenario_groups as _S  # noqa: N812  (module alias)

GROUPS: dict[str, Callable[[], dict[str, Any]]] = {
    "cross_chat": _cross_chat,
    # The migrated release gate: one group per scenario FAMILY.
    "harness_control": _S.harness_control,
    "harness_self_defence": _S.harness_self_defence,
    "g1_continuation": _S.g1_continuation,
    "g3_g4_domain": _S.g3_g4_domain,
    "g5_g8_g9_weather": _S.g5_g8_g9_weather,
    "g12_project_facts": _S.g12_project_facts,
    "g13_state_hygiene": _S.g13_state_hygiene,
    "weather_evidence": _weather_evidence,
    "mutation_probe": _mutation_probe,
    # Not a product scenario; excluded from the cap check below by name.
    "_hang_forever": _hang_forever,
}

PRODUCT_GROUPS = tuple(name for name in GROUPS if not name.startswith("_"))


__all__ = ["GROUPS", "MAX_GROUPS", "PRODUCT_GROUPS"]
