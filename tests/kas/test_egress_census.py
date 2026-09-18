"""The egress ledger: every module that can reach the network without the door, counted.

`core.remote_fetch_policy.open_remote` calls itself the ONE outbound HTTP door, and its docstring
records the day that claim was false. The repair was a door; what was missing was anything that
could tell you the door was still the only one. The two existing tests that pin the door's
property drive one lane and assert on that lane -- nothing was tree-scoped.

This is tree-scoped. The ledger below is the state of the repository as of this commit: exactly
these modules, with exactly these counts. A new direct-transport site anywhere in product code
fails this test, and so does removing one without updating the ledger -- which is the point. The
ledger is meant to SHRINK, and every step down should be a deliberate line in a diff.

Nothing here claims these modules are wrong to exist. Several are local-service probes that never
leave the machine. What the ledger claims is that the set is known, bounded and reviewed, instead
of being whatever accumulated.
"""

from __future__ import annotations

from core.kas.egress_census import modules_with_direct_egress

#: module -> (site count, why it is still here)
#:
#: `verified` means this lane's audit read the call sites. `unreviewed` means the census found it
#: and nobody in this lane has read it yet -- said plainly rather than assigned a reason that was
#: never checked.
KNOWN_DIRECT_EGRESS: dict[str, tuple[int, str]] = {
    # --- verified third-party egress outside the door. These are the real bypasses. ---
    "adapters/openai_compatible_adapter.py": (11, "verified: requests-named calls now delegate to core.provider_http; raw transport is owned by its worker"),
    "core/email_providers/base.py": (2, "verified: the email lane's own provider transport -- OAuth token refresh and provider REST calls to fixed endpoints, default TLS context, bounded timeouts, typed refusal mapping; read for this entry when the census flagged it after the email lane merge"),
    "core/provider_http.py": (3, "verified: requests imports provide the spawned cloud HTTP worker; admission stays with its caller"),
    "core/wallet/lifecycle.py": (1, "verified: Solana JSON-RPC client; constructor checks network and RPC URL allowlists before transport"),
    "tools/probe_openrouter_reasoning_budget.py": (1, "verified: explicit development CLI sends a credentialed completion to the fixed OpenRouter endpoint"),
    "core/cloud_transport.py": (1, "verified: re-implements the door's veto->report->open sequence with its own requests call"),
    "core/bootstrap_adapters.py": (2, "verified: the channel-outbound mirror transport, host settable from VOOL_MIRROR_URL"),
    "relay/bridge_workers/webhook_ingress.py": (1, "verified: the mirror forwarder's own urlopen"),
    "tools/web/coin_index.py": (1, "verified: reads the veto itself, then opens its own socket"),
    "core/mesh/task_router.py": (6, "verified: posts to arbitrary peer endpoints"),
    # --- verified local-service transport: reaches a service on this machine, not a third party ---
    "core/council/dispatch.py": (3, "verified: the local VOOL daemon on loopback"),
    "core/updater/health.py": (1, "verified: the local health endpoint on loopback"),
    "core/local_ollama_inventory.py": (1, "verified: the local Ollama inventory"),
    "core/gpu_inference_probe.py": (1, "verified: a local inference probe"),
    "core/system_resources.py": (1, "verified: a local resource probe"),
    "core/resource_governor.py": (5, "verified: local resource probes"),
    "core/llamacpp_capability_probe.py": (2, "verified: a local llama.cpp probe"),
    "tools/native_window_startup_drive.py": (1, "verified: development driver reads loopback native-runtime health"),
    "tools/profile_chat_turn_stream.py": (3, "verified: development driver reads chat, proof, and history from its isolated loopback daemon"),
    # --- found by the census, not yet read by this lane ---
    "core/agent_runtime/builder/controller.py": (2, "unreviewed"),
    "core/backend_acceleration_truth.py": (3, "unreviewed"),
    "core/brain_hive_research.py": (3, "unreviewed"),
    "core/conversation_summarizer.py": (2, "unreviewed"),
    "core/craft_upgrade.py": (3, "unreviewed"),
    "core/embedding_service.py": (2, "unreviewed"),
    "core/fact_extractor.py": (2, "unreviewed"),
    "core/intent_arbiter.py": (6, "unreviewed"),
    "core/kernel/repl.py": (1, "unreviewed"),
    "core/llm_eval/procedural_runner.py": (1, "unreviewed"),
    "core/local_media_render.py": (1, "unreviewed"),
    "core/normalized_provider_result.py": (1, "unreviewed"),
    "core/task_router.py": (1, "unreviewed"),
    "core/web/api/runtime.py": (1, "unreviewed"),
}


def test_the_set_of_modules_that_can_bypass_the_door_is_exactly_the_ledger() -> None:
    found = modules_with_direct_egress()
    appeared = sorted(set(found) - set(KNOWN_DIRECT_EGRESS))
    vanished = sorted(set(KNOWN_DIRECT_EGRESS) - set(found))
    assert not appeared, (
        "new direct-transport modules outside `core.remote_fetch_policy`. Either route them "
        "through the one door, or add them to KNOWN_DIRECT_EGRESS with the reason:\n  "
        + "\n  ".join(appeared)
    )
    assert not vanished, (
        "these modules no longer bypass the door -- delete their ledger entries, that is the "
        "ledger shrinking:\n  " + "\n  ".join(vanished)
    )


def test_no_module_grows_a_new_bypass_site_without_saying_so() -> None:
    found = modules_with_direct_egress()
    grown = {
        path: (KNOWN_DIRECT_EGRESS[path][0], count)
        for path, count in found.items()
        if path in KNOWN_DIRECT_EGRESS and count != KNOWN_DIRECT_EGRESS[path][0]
    }
    assert not grown, "direct-transport site counts changed (ledger, actual): " + repr(grown)


def test_the_repoops_and_kas_lanes_hold_no_direct_transport() -> None:
    """The lanes this work added must be clean, whatever the rest of the tree still carries."""

    found = modules_with_direct_egress()
    offenders = sorted(p for p in found if p.startswith(("core/kas/", "core/repoops/")))
    assert not offenders, offenders


def test_the_door_itself_is_excluded_deliberately() -> None:
    from pathlib import Path

    from core.kas.egress_census import DOOR_MODULES, scan_file

    # The door has raw transport calls, and that is the point of a door. It is excluded by name so
    # the exclusion is a named fact rather than an accident of the scan's shape.
    assert "core/remote_fetch_policy.py" in DOOR_MODULES
    root = Path(__file__).resolve().parents[2]
    assert scan_file(root / "core" / "remote_fetch_policy.py", repo_root=root), (
        "the door should itself contain raw transport; if it does not, this census is measuring "
        "the wrong construct"
    )
