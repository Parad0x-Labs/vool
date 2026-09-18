"""The child: install seams below routing, boot the real runtime, prove it, run one group, exit.

Run as `python -m tests.gauntlet.hermetic.child <group>`. Everything this process establishes --
the provider registry, the daemon threads, `agent-bootstrap.json`, whatever else bootstrap caches --
dies with it. That is the entire point; nothing here tries to undo any of it.

The self-test below runs BEFORE the group. A gauntlet whose fixtures silently fell off would report
the real provider's answers, or no answer at all, as product behaviour -- so the child refuses to
run a scenario until it has proved its own fixtures are the ones in effect.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.gauntlet.hermetic.protocol import KIND_INFRA, KIND_RESULT, emit


def _self_test() -> dict[str, object]:
    """Prove the fixtures are installed and the REAL routing stack is still deciding.

    Every check here is about the harness, not the product. If any of them fails the child emits
    `infra_error` and the parent refuses to read a product verdict out of the run.
    """
    from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
    from core.model_registry import ModelRegistry
    from core.provider_routing import (
        _device_probe,
        provider_capability_truth_for_manifest,
        rank_provider_candidates,
    )
    from tests.gauntlet import harness as H  # noqa: N812  (reads as the module name at every call site)

    facts: dict[str, object] = {}

    # 1. the model wire is ours
    adapter = ModelRegistry().build_adapter(_first_manifest())
    facts["adapter_class"] = type(adapter).__name__
    if type(adapter).__name__ != "_Scripted":
        raise RuntimeError(f"provider adapter interception NOT installed: {type(adapter).__name__}")

    # 2. the hardware fixture is visible through the REAL probe seam
    probe = _device_probe()
    if probe is None or float(probe.ram_gb) != float(H.GAUNTLET_MACHINE_RAM_GB):
        raise RuntimeError(f"hardware fixture not visible through _device_probe(): {probe}")
    facts["probe_ram_gb"] = float(probe.ram_gb)

    # 3. required provider manifests exist
    manifests = list(ModelRegistry().list_manifests(enabled_only=True))
    facts["manifests"] = [m.provider_id for m in manifests]
    if not manifests:
        raise RuntimeError("no enabled provider manifests -- the registry is empty")

    # 4. REAL ranking runs, with the real hardware-fit gate on
    ranked = rank_provider_candidates(
        ModelRegistry(), task_kind="normalization_assist", output_mode="plain_text",
        role="auto", swarm_size=4, min_trust=0.45, enforce_hardware_fit=True,
    )
    facts["ranked"] = [m.provider_id for m in ranked]
    if not ranked:
        raise RuntimeError("real ranking returned no candidate")

    # 5. capability truth derives from it, and REAL Autopilot resolves a lane from that
    truth = hydrate_capability_truth_with_benchmarks(
        tuple(provider_capability_truth_for_manifest(m) for m in ranked)
    )
    if not any(t.locality == "local" and t.availability_state != "blocked" for t in truth):
        raise RuntimeError("no local capability survived -- Autopilot would resolve the human lane")
    from core.local_inference_autopilot import build_local_inference_autopilot_plan

    plan = build_local_inference_autopilot_plan(
        user_text="Explain RSA encryption to me.",
        task_kind="normalization_assist",
        output_mode="plain_text",
        provider_role="auto",
        capability_truth=truth,
    )
    facts["autopilot_lane"] = str(getattr(plan, "lane", ""))
    if str(getattr(plan, "lane", "")) == "human":
        raise RuntimeError("Autopilot resolved the human lane in the self-test")

    # 6. an end-to-end turn really reaches the scripted wire through /api/chat
    H.SCRIPT.default = "SELFTEST-WIRE"
    turn = H.Conversation("selftest").say("Explain RSA encryption to me.")
    facts["selftest_status"] = turn.status
    if "SELFTEST-WIRE" not in turn.reply:
        raise RuntimeError(f"model wire not reached on a real turn: {turn.reply[:200]!r}")

    # 7. the weather transport fixture is reachable
    weather_turn = H.Conversation("selftest-weather").say("Get weather for Kaunas.")
    facts["selftest_weather"] = weather_turn.weather_requests
    if weather_turn.weather_requests != ["kaunas"]:
        raise RuntimeError(f"weather transport fixture not reached: {weather_turn.weather_requests}")

    # 8. config home really is the isolated one, measured not assumed
    from core.runtime_paths import active_config_home_dir

    facts["config_home"] = str(active_config_home_dir())
    return facts


def _first_manifest():
    from core.model_registry import ModelRegistry

    manifests = list(ModelRegistry().list_manifests(enabled_only=True))
    if not manifests:
        raise RuntimeError("no manifests to build an adapter from")
    return manifests[0]


def main(argv: list[str]) -> int:
    group = argv[1] if len(argv) > 1 else ""
    try:
        from tests.gauntlet import harness as H  # noqa: N812  (reads as the module name at every call site)
        from tests.gauntlet.hermetic.groups import GROUPS

        if group not in GROUPS:
            print(emit(KIND_INFRA, group, {"reason": f"unknown group {group!r}"}), flush=True)
            return 3

        H.install_unmanaged()   # the seams; no monkeypatch, nothing to undo
        H.runtime()             # the real bootstrap
        H.reset_capture()

        facts = _self_test()
        H.reset_capture()

        evidence = GROUPS[group]()
        print(emit(KIND_RESULT, group, {"self_test": facts, "evidence": evidence}), flush=True)
        return 0
    except BaseException as exc:
        print(
            emit(
                KIND_INFRA,
                group,
                {
                    "reason": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc()[-4000:],
                },
            ),
            flush=True,
        )
        print(json.dumps({"child_pid": os.getpid()}), file=sys.stderr, flush=True)
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv))
