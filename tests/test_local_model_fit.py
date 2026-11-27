"""The lag warning and the first-run picker read the router's own fit gate, never a new size rule.

Owner's 24 GB iMac, 2026-11-27: qwen3.8:27b (18 GB) ran the checks on every message and macOS
swapped 8 GB. A person may still pick a bigger model, but VOOL has to say it may lag.
"""

from __future__ import annotations

from core.hardware_tier import MachineProbe
from core.local_model_fit import (
    LAG_BESIDE_LOADED,
    LAG_HEAVY,
    LAG_TOO_BIG,
    daily_lane_keeps_resident,
    first_run_choices,
    local_model_lag,
)
from core.provider_routing import estimated_local_model_resident_gb


def _mac(gb: float) -> MachineProbe:
    return MachineProbe(cpu_cores=10, ram_gb=gb, gpu_name="Apple", vram_gb=gb, accelerator="mps")


def _cpu(gb: float) -> MachineProbe:
    return MachineProbe(cpu_cores=8, ram_gb=gb, gpu_name=None, vram_gb=None, accelerator="cpu")


def _choices(probe, **kw):
    kw.setdefault("free_disk_gb", 500.0)
    kw.setdefault("ollama_version", "0.40.3")
    kw.setdefault("installed", {})
    kw.setdefault("loaded", {})
    return first_run_choices(probe=probe, **kw)


def test_the_27b_that_lagged_the_24gb_imac_is_flagged_there_and_not_on_a_bigger_mac():
    assert local_model_lag("qwen3.8:27b", size_gb=17.7, probe=_mac(24.0)) == LAG_TOO_BIG
    # On a 64 GB Mac it fits, but the daily lane never keeps a dense 27B resident: still a warning.
    assert local_model_lag("qwen3.8:27b", size_gb=17.7, probe=_mac(64.0)) == LAG_HEAVY


def test_a_model_that_fits_alone_is_flagged_when_it_does_not_fit_next_to_what_is_loaded():
    probe = _mac(16.0)
    assert local_model_lag("qwen3.5:9b", size_gb=6.6, probe=probe) == ""
    assert local_model_lag("qwen3.5:9b", size_gb=6.6, probe=probe, loaded={"qwen3.5:4b": 3.3}) == LAG_BESIDE_LOADED
    # The model itself being loaded is not counted twice.
    assert local_model_lag("qwen3.5:9b", size_gb=6.6, probe=probe, loaded={"qwen3.5:9b": 6.6}) == ""


def test_the_resident_estimate_is_the_routers_own():
    from core.local_model_fit import resident_gb

    for tag, size in (("qwen3.5:9b", 6.6), ("qwen3:30b-instruct", 18.6), ("qwen3.8:27b", 0.0)):
        assert resident_gb(tag, size) == estimated_local_model_resident_gb(tag, artifact_size_gb=size)


def test_the_no_thinking_30b_is_kept_resident_and_the_dense_27b_and_35b_are_not():
    assert daily_lane_keeps_resident("qwen3:30b-instruct")
    assert daily_lane_keeps_resident("qwen3.5:9b")
    assert not daily_lane_keeps_resident("qwen3.8:27b")
    assert not daily_lane_keeps_resident("qwen3.6:35b")


def test_the_picker_ticks_a_middle_model_and_the_helper_not_the_smallest_or_the_biggest():
    for probe, best in ((_mac(8.0), "qwen3.5:2b"), (_mac(16.0), "qwen3.5:4b"), (_mac(24.0), "qwen3.5:9b"),
                        (_mac(36.0), "qwen3:30b-instruct"), (_mac(64.0), "qwen3:30b-instruct"), (_cpu(16.0), "qwen3.5:4b")):
        options = _choices(probe)["options"]
        chosen = [o["model_name"] for o in options if o["chosen"]]
        assert chosen == [best, "qwen3:0.6b"], (probe.ram_gb, chosen)
        first = options[0]
        assert first["role"] == "best" and first["lag"] == "", (probe.ram_gb, first)
        sizes = sorted(o["download_gb"] for o in options if o["role"] != "helper")
        assert len(sizes) > 1 and first["download_gb"] != sizes[0], (probe.ram_gb, sizes)


def test_every_bigger_model_that_may_lag_is_offered_unticked_with_its_reason():
    options = _choices(_mac(24.0))["options"]
    by = {o["model_name"]: o for o in options}
    assert by["qwen3.8:27b"]["lag"] == LAG_TOO_BIG and not by["qwen3.8:27b"]["chosen"]
    assert by["qwen3.8:27b"]["role"] == "bigger"
    for o in options:
        if o["lag"]:
            assert not o["chosen"] and o["role"] == "bigger", o


def test_a_model_bigger_than_all_memory_or_the_free_disk_is_not_offered():
    names = {o["model_name"] for o in _choices(_mac(16.0))["options"]}
    assert "qwen3.6:35b" not in names and "qwen3.8:27b" not in names
    tight = {o["model_name"] for o in _choices(_mac(24.0), free_disk_gb=12.0)["options"]}
    assert "qwen3.8:27b" not in tight and "qwen3:30b-instruct" not in tight
    assert all(o["download_gb"] + 4.0 <= 12.0 for o in _choices(_mac(24.0), free_disk_gb=12.0)["options"] if not o["installed"])


def test_an_installed_model_is_not_ticked_for_download_again():
    options = _choices(_mac(24.0), installed={"qwen3.5:9b": 6.6})["options"]
    best = options[0]
    assert best["model_name"] == "qwen3.5:9b" and best["installed"] and not best["chosen"]


def test_an_older_ollama_is_offered_only_tags_it_can_pull():
    from core.local_model_suggestions import PREVIOUS_GENERATION_DOWNLOAD_GB

    options = _choices(_mac(24.0), ollama_version="0.21.2")["options"]
    assert options and all(o["model_name"] in PREVIOUS_GENERATION_DOWNLOAD_GB for o in options), options


def test_every_offered_model_is_one_the_pull_door_accepts():
    from core.local_model_readiness import is_recommended_tag

    for probe in (_mac(8.0), _mac(24.0), _mac(64.0), _cpu(16.0)):
        for o in _choices(probe)["options"]:
            assert is_recommended_tag(o["model_name"]), o


def test_an_unreadable_machine_invents_no_size_warning():
    from core import local_model_fit

    original = local_model_fit._probe
    local_model_fit._probe = lambda probe: None
    try:
        assert local_model_lag("qwen3.5:9b", size_gb=6.6) == ""
        assert local_model_lag("qwen3.8:27b", size_gb=17.7) == LAG_HEAVY
    finally:
        local_model_fit._probe = original


# ---- the model pill: the local model Auto answers the NEXT message with -------------------------


def _get(path: str) -> dict:
    import json

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    response = dispatch_get(path=path, query={}, runtime=RuntimeServices(), model_name="vool", client_host="127.0.0.1", headers={})
    assert response.status == 200
    return json.loads(response.body)


def test_the_pill_route_names_the_turns_own_auto_choice(monkeypatch):
    import core.clean_loop.brain as brain

    seen = {}

    def fake_resolve(router, context):
        seen["context"] = context
        seen["has_registry"] = hasattr(router, "registry")
        return type("M", (), {"model_name": "qwen3:8b"})()

    monkeypatch.setattr(brain, "resolve_local_manifest", fake_resolve)
    assert _get("/api/models/local/auto") == {"model": "qwen3:8b"}
    # An Auto request (no requested model), asked of the registry exactly as a turn asks it.
    assert seen == {"context": {}, "has_registry": True}


def test_the_pill_route_says_nothing_when_no_local_model_answers(monkeypatch):
    import core.clean_loop.brain as brain

    monkeypatch.setattr(brain, "resolve_local_manifest", lambda router, context: None)
    assert _get("/api/models/local/auto") == {"model": ""}

    def broken(router, context):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(brain, "resolve_local_manifest", broken)
    assert _get("/api/models/local/auto") == {"model": ""}


def test_the_chat_page_paints_the_pill_from_that_route_and_never_on_a_timer():
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html()
    assert "fetch('/api/models/local/auto')" in html
    assert "setInterval(refreshAutoLocalModel" not in html and "setTimeout(refreshAutoLocalModel" not in html
