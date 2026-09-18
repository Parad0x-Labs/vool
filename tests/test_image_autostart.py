"""The image fast-path starts the on-device engine (ComfyUI) itself when it's installed but down."""

from __future__ import annotations

import core.resource_governor as gov
from core import generated_files as gf
from core import local_media_render as lmr
from core import media_tools
from core.agent_runtime import fast_paths_media as fpm

_FULL_TIER = gov.RENDER_TIERS[0]   # 1024px at full speed — what the fast path gets on a roomy Mac


def _pin_render_tier(monkeypatch, tier, *, free_gb: float) -> None:
    """Pin the resource governor: these tests assert the autostart/refusal wording, not live RAM.

    Unpinned, the same test passes at 7 GB free and fails at 0.8 GB, where plan_render() returns no
    tier and the fast path answers "extremely low on memory" instead of taking the engine path. Pass
    ``tier=None`` for the starved direction. Also stubs the post-render hygiene, which would
    otherwise SIGTERM a real ComfyUI and write the fake render into the real generated-files index.
    """
    monkeypatch.setattr(
        gov, "plan_render",
        lambda: (tier, gov.GovernorDecision(ok=tier is not None, usable_after_gb=free_gb)),
    )
    monkeypatch.setattr(gov, "reclaim_if_pressured", lambda: [])
    monkeypatch.setattr(gf, "record_generated_file", lambda *a, **k: None)


class _FakeAgent:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def _emit_runtime_event(self, ctx, **kwargs):
        self.events.append(kwargs)

    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason}


def _handle(agent, text):
    return fpm.maybe_handle_image_generation(agent, text, session_id="s", source_context={"operating_mode": "auto"})


def test_image_request_autostarts_comfyui_then_renders(monkeypatch) -> None:
    _pin_render_tier(monkeypatch, _FULL_TIER, free_gb=12.0)
    monkeypatch.setattr(lmr, "local_render_available", lambda: True)
    monkeypatch.setattr(media_tools, "has_image_service", lambda: False)
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: False)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: True)

    state = {"up": False, "started": 0}
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: state["up"])

    def _ensure(**kwargs):
        state["started"] += 1
        state["up"] = True
        return True, "ComfyUI is up"

    monkeypatch.setattr(lmr, "ensure_comfyui", _ensure)
    monkeypatch.setattr(lmr, "run_local_image_render", lambda prompt, **k: (True, "/U/Pictures/VOOL-Renders/out.png"))

    agent = _FakeAgent()
    res = _handle(agent, "generate an image of a cat")
    assert res is not None and "out.png" in res["response"]
    assert state["started"] == 1  # VOOL started the engine herself, no manual step
    assert any("Starting the on-device image engine" in (e.get("message") or "") for e in agent.events)


def test_autostart_disabled_returns_actionable_error_not_a_bluff(monkeypatch) -> None:
    _pin_render_tier(monkeypatch, _FULL_TIER, free_gb=12.0)
    monkeypatch.setattr(lmr, "local_render_available", lambda: True)
    monkeypatch.setattr(media_tools, "has_image_service", lambda: False)
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: False)
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: False)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: False)

    started = {"n": 0}
    monkeypatch.setattr(lmr, "ensure_comfyui", lambda **k: (started.__setitem__("n", started["n"] + 1), (True, "up"))[1])

    res = _handle(_FakeAgent(), "generate an image of a cat")
    assert started["n"] == 0  # respected the opt-out
    assert "On-device rendering isn't available" in res["response"]
    # No cloud key here, so the reply must name the way to get one rather than dead-ending.
    assert "image key" in res["response"]


def test_explicit_local_failure_does_not_silently_use_cloud(monkeypatch) -> None:
    _pin_render_tier(monkeypatch, _FULL_TIER, free_gb=12.0)
    monkeypatch.setattr(lmr, "local_render_available", lambda: True)
    monkeypatch.setattr(media_tools, "has_image_service", lambda: True)  # a cloud key exists...
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: True)  # ...but the user asked for local
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: False)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: True)
    monkeypatch.setattr(lmr, "ensure_comfyui", lambda **k: (False, "did not become ready within 180s"))

    cloud = {"n": 0}
    monkeypatch.setattr(media_tools, "generate_image", lambda **k: cloud.__setitem__("n", cloud["n"] + 1))

    res = _handle(_FakeAgent(), "generate an image of a cat locally")
    assert cloud["n"] == 0  # honored "locally"; never sent the prompt to the cloud
    assert "On-device rendering isn't available" in res["response"]
    # The engine's own reason is carried through, not swallowed behind a generic line.
    assert "did not become ready within 180s" in res["response"]
    # A configured cloud key is OFFERED (the user asked for local, so it is not used silently).
    assert "cloud" in res["response"].lower()


def test_starved_mac_says_so_instead_of_starting_the_engine(monkeypatch) -> None:
    # The other direction of the same pin: no tier fits, so VOOL says the Mac is out of memory and
    # never spends a minute starting an engine that would then freeze it.
    _pin_render_tier(monkeypatch, None, free_gb=0.8)   # too little even for a 512px low-memory render
    monkeypatch.setattr(lmr, "local_render_available", lambda: True)
    monkeypatch.setattr(media_tools, "has_image_service", lambda: False)
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: False)
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: False)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: True)

    started = {"n": 0}
    monkeypatch.setattr(lmr, "ensure_comfyui", lambda **k: (started.__setitem__("n", started["n"] + 1), (True, "up"))[1])

    res = _handle(_FakeAgent(), "generate an image of a cat")
    assert started["n"] == 0  # refused before the autostart, not after
    assert res["reason"] == "image_generate_low_memory"
    assert "~0.8 GB free" in res["response"]


def test_no_local_skill_still_uses_cloud(monkeypatch) -> None:
    monkeypatch.setattr(lmr, "local_render_available", lambda: False)
    monkeypatch.setattr(media_tools, "has_image_service", lambda: True)
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: False)
    monkeypatch.setattr(
        media_tools, "generate_image",
        lambda **k: type("R", (), {"ok": True, "image_ref": "https://x/y.png", "status": "", "message": ""})(),
    )
    res = _handle(_FakeAgent(), "generate an image of a cat")
    assert res is not None and "y.png" in res["response"]
