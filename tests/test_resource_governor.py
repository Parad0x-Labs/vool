"""The resource governor: reclaim, idle hygiene, adaptive render tiers, and the image-gen wiring."""
from __future__ import annotations

import core.resource_governor as gov
from core.system_resources import ResourceSnapshot


def _snap(usable: float, *, ollama: float = 0.0, comfy: bool = False, pressure: str = "normal",
          available: float | None = None) -> ResourceSnapshot:
    return ResourceSnapshot(
        total_ram_gb=24.0, available_ram_gb=(usable + 4.8 if available is None else available),
        used_ram_gb=0.0, free_fraction=0.5,
        memory_pressure=pressure, load_1m=1.0, cpu_count=10, load_per_core=0.1, swap_used_gb=0.0,
        ollama_loaded_gb=ollama, comfyui_running=comfy, reserve_gb=4.8, usable_gb=usable,
    )


# --------------------------------------------------------------------------- reclaim primitives

def test_reclaim_if_pressured_stops_idle_comfyui(monkeypatch) -> None:
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: _snap(1.0, comfy=True, pressure="warn"))
    calls = {"stop": 0}
    monkeypatch.setattr(gov, "stop_comfyui", lambda: (calls.__setitem__("stop", 1) or True))
    assert gov.reclaim_if_pressured() and calls["stop"] == 1


def test_reclaim_noop_when_healthy(monkeypatch) -> None:
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: _snap(10.0, comfy=True, pressure="normal"))
    monkeypatch.setattr(gov, "stop_comfyui", lambda: True)
    assert gov.reclaim_if_pressured() == []


def test_stop_comfyui_signals_the_process(monkeypatch) -> None:
    import subprocess

    class _R:
        stdout = "4559\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    killed = []
    monkeypatch.setattr(gov.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    assert gov.stop_comfyui() is True
    assert killed and killed[0][0] == 4559


# --------------------------------------------------------------------------- adaptive render tiers

def test_plan_render_full_quality_when_roomy(monkeypatch) -> None:
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: _snap(0.0, available=12.0))
    tier, d = gov.plan_render()
    assert d.ok and tier["low_memory"] == "" and tier["width"] == 1024 and d.actions == []


def test_plan_render_renders_at_6gb_free_instead_of_refusing(monkeypatch) -> None:
    # Regression: ~6 GB free was WRONGLY refused (the 20% reserve was subtracted first, leaving "1.4 GB").
    # A user-requested render may use the reserve -> gate on real free RAM, so 6 GB renders (1024px lowvram).
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: _snap(1.4, available=6.2, ollama=0.0))
    tier, d = gov.plan_render()
    assert d.ok and tier is not None
    assert tier["low_memory"] == "lowvram" and tier["width"] == 1024


def test_plan_render_degrades_to_low_memory_when_tight(monkeypatch) -> None:
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: _snap(0.0, available=4.0))   # 4 GB free -> 768px lowvram
    tier, d = gov.plan_render()
    assert d.ok and tier["low_memory"] == "lowvram" and tier["width"] <= 1024


def test_plan_render_reclaims_to_earn_a_better_tier(monkeypatch) -> None:
    snaps = iter([_snap(0.0, ollama=8.0, available=6.0), _snap(0.0, available=12.0)])   # tight -> unload -> roomy
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: next(snaps))
    monkeypatch.setattr(gov, "unload_idle_ollama", lambda: 8.0)
    tier, d = gov.plan_render()
    assert d.ok and tier["low_memory"] == "" and any("freed" in a for a in d.actions)


def test_plan_render_returns_none_only_when_truly_starved(monkeypatch) -> None:
    monkeypatch.setattr(gov.sr, "snapshot", lambda **k: _snap(0.0, available=1.0, ollama=0.0))   # < 1.5 GB free
    tier, d = gov.plan_render()
    assert tier is None and not d.ok


# --------------------------------------------------------------------------- image-gen wiring

def _wire_image(monkeypatch, *, has_fal=False):
    from core import generated_files as gf
    from core import local_media_render as lmr
    from core import media_tools
    monkeypatch.setattr(lmr, "local_render_available", lambda: True)
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: False)
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda: True)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: True)
    monkeypatch.setattr(media_tools, "has_image_service", lambda: has_fal)
    monkeypatch.setattr(gov, "reclaim_if_pressured", lambda: [])
    monkeypatch.setattr(gf, "record_generated_file", lambda *a, **k: None)
    return lmr


class _Agent:
    def _fast_path_result(self, **kw):
        return {"response": kw["response"], "reason": kw.get("reason", "image_generate")}
    def _emit_runtime_event(self, *a, **k):
        pass


def test_image_renders_at_the_low_memory_tier_when_tight(monkeypatch) -> None:
    from core.agent_runtime import fast_paths_media as fpm
    lmr = _wire_image(monkeypatch)
    ensured, rendered = {}, {}
    monkeypatch.setattr(lmr, "ensure_comfyui", lambda **k: (ensured.update(k) or (True, "")))
    monkeypatch.setattr(lmr, "run_local_image_render",
                        lambda prompt, **k: (rendered.update({**k, "prompt": prompt}) or (True, "/tmp/r.png")))
    tier = {"low_memory": "lowvram", "width": 512, "height": 512, "steps": 20, "min_usable": 1.6, "label": "512px, low-memory mode"}
    monkeypatch.setattr(gov, "plan_render", lambda: (tier, gov.GovernorDecision(ok=True, usable_after_gb=2.0)))

    r = fpm.maybe_handle_image_generation(_Agent(), "generate an image of a cat", session_id="s", source_context={"operating_mode": "auto"})
    assert r is not None
    assert ensured.get("low_memory") == "lowvram"            # engine brought up in low-memory mode
    assert rendered.get("width") == 512 and rendered.get("steps") == 20   # rendered at the tier's size
    assert "512px" in r["response"] and "![" in r["response"]            # an image (adapted), NOT a refusal


def test_image_offers_cloud_only_when_truly_starved(monkeypatch) -> None:
    from core.agent_runtime import fast_paths_media as fpm
    _wire_image(monkeypatch, has_fal=True)
    monkeypatch.setattr(gov, "plan_render",
                        lambda: (None, gov.GovernorDecision(ok=False, usable_after_gb=0.7)))
    r = fpm.maybe_handle_image_generation(_Agent(), "generate an image of a cat", session_id="s", source_context={"operating_mode": "auto"})
    assert r["reason"] == "image_generate_low_memory"
    assert "extremely low" in r["response"] and "cloud" in r["response"].lower()
