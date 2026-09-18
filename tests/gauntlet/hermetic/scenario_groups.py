"""The migrated scenario groups: every turn the release gate drives, executed inside a child.

Each function here runs in the hermetic child, after its infrastructure self-test has passed, and
returns a JSON-serialisable evidence document. The parent asserts on that document and never boots
the runtime itself.

Grouping rule: one group per SCENARIO FAMILY, not per assertion. A family's turns share a runtime
because their meaning is cross-turn -- G1's follow-up only means something after G1's first turn,
and cross-chat isolation only means something when both chats share one bootstrap. Unrelated
families stay in separate children so one family's state cannot explain another's result.
"""
from __future__ import annotations

from typing import Any

from tests.gauntlet import harness as H  # noqa: N812  (reads as the module name at every call site)


def _turn(label: str, turn) -> dict[str, Any]:
    """One turn, reduced to everything any migrated assertion needs."""
    return {
        "label": label,
        "said": turn.said,
        "status": turn.status,
        "reply": turn.reply,
        "low": turn.low,
        "session_id": turn.session_id,
        "weather_requests": turn.weather_requests,
        "market_requests": turn.market_requests,
        "planned": [(item["operation"], item["entity"]) for item in turn.planned_subtasks],
        "model_calls": len(turn.model_calls),
        "prompt_seen_by_model": "\n".join(call.prompt for call in turn.model_calls),
        "tools": turn.tools_run(),
        "describe": turn.describe(),
        "numbers": turn.numbers(),
    }


# --------------------------------------------------------------------------- harness control


def harness_control() -> dict[str, Any]:
    """A/B/C/D/E, preserved exactly, plus the machine/ranking facts that back them.

    These were the in-parent control suite. They still drive real `/api/chat`, real ranking, real
    capability truth and real Autopilot -- the only change is which interpreter they run in.
    """
    from dataclasses import replace

    from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
    from core.model_registry import ModelRegistry
    from core.provider_routing import (
        _device_probe,
        provider_capability_truth_for_manifest,
        provider_hardware_fit_rejection,
        rank_provider_candidates,
    )

    chat_a = "Explain RSA encryption to me."
    chat_b = "Explain the Diffie-Hellman key exchange."
    chat_c = "What is a Merkle tree, briefly?"
    live = "Get weather for Kaunas."
    H.SCRIPT.default = "SENTINEL-WIRE-MARKER"

    def rank(enforce: bool):
        return rank_provider_candidates(
            ModelRegistry(), task_kind="normalization_assist", output_mode="plain_text",
            role="auto", swarm_size=4, min_trust=0.45, enforce_hardware_fit=enforce,
        )

    probe = _device_probe()
    ranked = rank(True)
    truth = hydrate_capability_truth_with_benchmarks(
        tuple(provider_capability_truth_for_manifest(m) for m in ranked)
    )
    local = [
        m for m in ModelRegistry().list_manifests(enabled_only=True)
        if provider_capability_truth_for_manifest(m).locality == "local"
    ]
    base = provider_capability_truth_for_manifest(local[0]) if local else None
    fit_7g = replace(base, ram_budget_gb=7.5, vram_budget_gb=7.5) if base else None
    fit_40g = replace(base, ram_budget_gb=40.0, vram_budget_gb=40.0) if base else None

    facts: dict[str, Any] = {
        "probe_ram_gb": float(getattr(probe, "ram_gb", 0.0)),
        "probe_accelerator": str(getattr(probe, "accelerator", "")),
        "probe_driver": str(getattr(probe, "driver_version", "")),
        "expected_ram_gb": float(H.GAUNTLET_MACHINE_RAM_GB),
        "ranked": [m.provider_id for m in ranked],
        "capability_provider_ids": sorted(t.provider_id for t in truth),
        "local_available": any(t.locality == "local" and t.availability_state != "blocked" for t in truth),
        # capable vs incapable machine, through the REAL rejection function
        "fit_capable": provider_hardware_fit_rejection(fit_7g, probe=H.machine_probe()) if fit_7g else None,
        "fit_incapable": (
            provider_hardware_fit_rejection(
                fit_7g, probe=H.machine_probe(ram_gb=2.0, vram_gb=0.0, accelerator="cpu")
            ) if fit_7g else None
        ),
        # the installed fixture is what the gate reads when no probe is passed
        "fit_40g_via_installed_probe": provider_hardware_fit_rejection(fit_40g) if fit_40g else None,
        "fit_7g_via_installed_probe": provider_hardware_fit_rejection(fit_7g) if fit_7g else None,
    }

    controls: dict[str, Any] = {}
    controls["A"] = _turn("A", H.Conversation("ctlA").say(chat_a))
    controls["B"] = _turn("B", H.Conversation("ctlB").say(live))

    convo_c = H.Conversation("ctlC")
    controls["C_live"] = _turn("C_live", convo_c.say(live))
    controls["C_chat"] = _turn("C_chat", convo_c.say(chat_b))

    H.Conversation("ctlD1").say(live)
    controls["D"] = _turn("D", H.Conversation("ctlD2").say(chat_a))

    orders: dict[str, list[dict[str, Any]]] = {}
    for order in ("ABCD", "DCBA", "BADC", "CDAB", "BBAA", "ADBC", "DBCA", "CABD"):
        steps: list[dict[str, Any]] = []
        for index, step in enumerate(order):
            tag = f"{order}[{index}]={step}"
            if step == "A":
                steps.append(_turn(tag, H.Conversation(f"e{order}{index}").say(chat_a)))
            elif step == "B":
                steps.append(_turn(tag, H.Conversation(f"e{order}{index}").say(live)))
            elif step == "C":
                convo = H.Conversation(f"e{order}{index}")
                steps.append(_turn(tag + "/live", convo.say(live)))
                steps.append(_turn(tag + "/chat", convo.say(chat_c)))
            else:
                H.Conversation(f"e{order}{index}a").say(live)
                steps.append(_turn(tag, H.Conversation(f"e{order}{index}b").say(chat_b)))
        orders[order] = steps

    repeats: list[dict[str, Any]] = []
    for iteration in range(6):
        convo = H.Conversation(f"rep{iteration}")
        repeats.append(_turn(f"it{iteration}/chat1", convo.say(chat_a)))
        repeats.append(_turn(f"it{iteration}/live", convo.say(live)))
        repeats.append(_turn(f"it{iteration}/chat2", convo.say(chat_b)))
        repeats.append(_turn(f"it{iteration}/fresh", H.Conversation(f"rep{iteration}f").say(chat_c)))

    return {
        "marker": "SENTINEL-WIRE-MARKER",
        "facts": facts,
        "controls": controls,
        "orders": orders,
        "repeats": repeats,
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


# ------------------------------------------------------------------------ harness self-defence


def harness_self_defence() -> dict[str, Any]:
    """The three interception self-tests that used to live at the top of the gauntlet file."""
    H.SCRIPT.default = "WIRE-REACHED"
    weather = H.Conversation("selfdef-weather").say("What is the current weather in Kaunas?")
    wire = H.Conversation("selfdef-wire").say("Explain what a hash function is.")
    convo = H.Conversation("selfdef-session")
    first = convo.say("Explain what a hash function is.")
    second = convo.say("And what is a salt?")
    return {
        "weather": _turn("weather", weather),
        "wire": _turn("wire", wire),
        "session_first": _turn("first", first),
        "session_second": _turn("second", second),
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


# ------------------------------------------------------------------------------------- G1


def g1_continuation() -> dict[str, Any]:
    """Grounded entities must survive a follow-up, a comparison, and a typo-heavy clarification."""
    convo = H.Conversation("g1")
    turns = [
        _turn("kaunas", convo.say("Get weather for Kaunas.")),
        _turn("tallinn", convo.say("What about Tallinn?")),
        _turn("warmer", convo.say("Which one is warmer?")),
        _turn(
            "typos",
            convo.say(
                "I asked about Kauans and talling weather, last question was follow up for them "
                "two right?"
            ),
        ),
    ]
    # Same family, other shape: the obligation is opened by ONE MULTIPART turn rather than
    # accumulated over two. The turns above never exercise the multipart lane's own record of what
    # it grounded, so removing that record left the whole gauntlet green -- measured, which is why
    # this conversation exists.
    multipart = H.Conversation("g1-multipart")
    multipart_turns = [
        _turn("both_at_once", multipart.say("Get the current weather for Riga and Warsaw.")),
        _turn("compare", multipart.say("which one is warmer?")),
    ]
    return {
        "turns": turns,
        "multipart_turns": multipart_turns,
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


# ---------------------------------------------------------------------------------- G3 + G4


def g3_g4_domain() -> dict[str, Any]:
    """Specialist state must release (G3), and a lexical market token must not seize a turn (G4)."""
    H.SCRIPT.default = "GENERAL-ANSWER"
    standalone = H.Conversation("g3-alone")
    rsa_alone = _turn("rsa_alone", standalone.say("Explain RSA encryption to me."))

    after_live = H.Conversation("g3-after")
    after_live.say("Get weather for Kaunas.")
    rsa_after = _turn("rsa_after", after_live.say("Explain RSA encryption to me."))

    gold = H.Conversation("g4")
    gold_structure = _turn("gold_structure", gold.say("Explain gold structure."))
    gold_correction = _turn(
        "gold_correction", gold.say("Not price — I mean the chemical structure of gold.")
    )
    water = _turn(
        "water", H.Conversation("g4-control").say("Explain the crystal structure of water ice.")
    )
    return {
        "rsa_alone": rsa_alone,
        "rsa_after": rsa_after,
        "gold_structure": gold_structure,
        "gold_correction": gold_correction,
        "water": water,
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


# ----------------------------------------------------------------------------- G5 + G8 + G9


def g5_g8_g9_weather() -> dict[str, Any]:
    """The completeness and entity-extraction family -- all weather-adjacent, all one runtime."""
    mixed = _turn(
        "mixed",
        H.Conversation("g5").say(
            "What is 137 × 29? Explain the calculation briefly. "
            "Also get the current weather for Kaunas and Tallinn and tell me which city is warmer."
        ),
    )
    phantom = _turn(
        "phantom",
        H.Conversation("g8").say("Check the current weather in Kaunas and summarize it."),
    )
    abstract = _turn(
        "abstract",
        H.Conversation("g9").say(
            "Compare the current weather in 8 European capitals and rank them warmest to coldest."
        ),
    )
    return {
        "mixed": mixed,
        "phantom": phantom,
        "abstract": abstract,
        "known_cities": sorted(H.KNOWN_WEATHER),
        "runtime_bootstraps": H.bootstrap_count(),
        "pid": __import__("os").getpid(),
    }


# ------------------------------------------------------------------------------------- G13


def g13_state_hygiene() -> dict[str, Any]:
    """What the conversation REMEMBERS the user wants, after turns that build a prompt from evidence.

    A separate family from G1. G1 asks whether a follow-up reaches the right lane; this asks whether
    the state a follow-up will later resolve against is the user's own words or the runtime's.

    `adapt_user_input` persists -- dialogue turn, `current_user_goal`, active-mission slots -- and two
    call sites handed it a composed PROMPT. The turns below are the measured reproduction: an offer,
    an acceptance that takes the research path, then an unrelated request. The evidence is the
    persisted dialogue row after each, because that is what `bootstrap_context` replays into every
    later turn as "Current user goal: ...".

    Driven here rather than by calling the adapter directly for one reason: with the production fix
    reverted, a direct-call test stays green. Only the real seam composes the prompt.
    """
    from storage.dialogue_memory import get_dialogue_session

    H.SCRIPT.default = "MODEL-ANSWER"
    H.SCRIPT.when_prompt_contains(
        "llama 3.1",
        "Which pair do you want me to compare?\n"
        "1. Qwen3 8B vs Llama 3.1 8B\n2. Qwen3 8B vs Mistral 7B\n3. Something else",
    )
    convo = H.Conversation("g13")
    said = [
        "which local model should i run, qwen3 8b or llama 3.1 8b",
        "yes ok my bad, do compare those models",
        "how do I sort a list in python",
    ]
    turns: list[dict[str, Any]] = []
    for index, text in enumerate(said):
        turn = convo.say(text)
        row = get_dialogue_session(convo.session_id) or {}
        turns.append(
            {
                **_turn(f"h{index}", turn),
                "persisted_goal": str(row.get("current_user_goal") or ""),
                "persisted_subject": str(row.get("last_subject") or ""),
            }
        )
    return {"turns": turns, "runtime_bootstraps": H.bootstrap_count(), "pid": __import__("os").getpid()}


# ------------------------------------------------------------------------------------ G12


def g12_project_facts() -> dict[str, Any]:
    """An exact-count request must not be answered with a repository tree."""
    turn = _turn(
        "exact",
        H.Conversation("g12").say(
            "Count how many Python files exist in this project, determine which Python file has "
            "the most lines, and give me its exact path and line count. Do not estimate."
        ),
    )
    return {"exact": turn, "runtime_bootstraps": H.bootstrap_count(), "pid": __import__("os").getpid()}


__all__ = [
    "g1_continuation",
    "g3_g4_domain",
    "g5_g8_g9_weather",
    "g12_project_facts",
    "g13_state_hygiene",
    "harness_control",
    "harness_self_defence",
]
