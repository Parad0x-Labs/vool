"""Local-model certification on a slow CPU, and the Intel build running on an Apple chip.

v0.7.0 Intel DMG under Rosetta on an Apple Silicon Mac (2026-10-13, attempt 2c): the bundled
model runner loaded its SSE4.2 CPU kernel, certifying qwen3:8b ran out the Settings button's 150 s
per call with zero exchanges, and recall timed out. These pin (a) that certification's per-call
ceiling follows the same CPU-aware rule as the runtime's other local budgets, GPU numbers
unchanged, and (b) that the Intel build on an Apple chip says so in plain words where local
models are managed.
"""

from __future__ import annotations

import ctypes
import json

import pytest

from core import local_compute_budget
from core.hardware_tier import MachineProbe
from core.local_model_tool_certification import run_local_model_tool_certification
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get
from tests.test_local_model_tool_certification import ProbeExchange, _manifest


@pytest.fixture(autouse=True)
def _fresh_caches():
    local_compute_budget.local_inference_is_cpu_only.cache_clear()
    local_compute_budget.running_translated_on_apple_silicon.cache_clear()
    yield
    local_compute_budget.local_inference_is_cpu_only.cache_clear()
    local_compute_budget.running_translated_on_apple_silicon.cache_clear()


def _machine(monkeypatch, accelerator: str) -> None:
    monkeypatch.setattr(
        "core.hardware_tier.probe_machine",
        lambda: MachineProbe(cpu_cores=8, ram_gb=16.0, gpu_name=None, vram_gb=None, accelerator=accelerator),
    )


@pytest.mark.parametrize("accelerator", ["mps", "cuda", "directml"])
def test_certification_on_a_gpu_machine_keeps_the_requested_per_call_ceiling(monkeypatch, tmp_path, accelerator):
    _machine(monkeypatch, accelerator)
    exchange = ProbeExchange()
    result = run_local_model_tool_certification(
        _manifest(), exchange=exchange, db_path=tmp_path / "cert.db", timeout_seconds=150
    )
    assert result["state"] == "verified"
    assert exchange.calls and {call["timeout_seconds"] for call in exchange.calls} == {150.0}


def test_certification_on_a_cpu_only_machine_scales_every_probe_call(monkeypatch, tmp_path):
    _machine(monkeypatch, "cpu")
    exchange = ProbeExchange()
    result = run_local_model_tool_certification(
        _manifest(), exchange=exchange, db_path=tmp_path / "cert.db", timeout_seconds=150
    )
    assert result["state"] == "verified"
    scaled = 150 * local_compute_budget.CPU_ONLY_FACTOR
    assert {call["timeout_seconds"] for call in exchange.calls} == {scaled}
    # The measured Rosetta call that ran out 150 s fits the scaled ceiling.
    assert scaled > 150.061


def test_a_cpu_only_timeout_still_ends_as_a_typed_degraded_run(monkeypatch, tmp_path):
    _machine(monkeypatch, "cpu")
    result = run_local_model_tool_certification(
        _manifest(), exchange=ProbeExchange(timeout=True), db_path=tmp_path / "cert.db", timeout_seconds=150
    )
    assert result["state"] == "degraded"
    assert result["successful"] is False
    assert result["stages"]["transport_acceptance"]["failure_code"] == "timeout"


class _Libc:
    def __init__(self, translated: int | None):
        self.translated = translated
        self.names: list[bytes] = []

    def sysctlbyname(self, name, value, size, new, new_size):
        self.names.append(name)
        if self.translated is None:
            return -1  # ENOENT: an Intel Mac has no such key
        value.contents.value = self.translated
        return 0


def _darwin(monkeypatch, libc: _Libc) -> None:
    monkeypatch.setattr("core.local_compute_budget.platform.system", lambda: "Darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: libc)


def test_the_intel_build_translated_on_an_apple_chip_gets_the_plain_notice(monkeypatch):
    libc = _Libc(translated=1)
    _darwin(monkeypatch, libc)
    notice = local_compute_budget.apple_silicon_build_notice()
    assert notice == local_compute_budget.APPLE_SILICON_BUILD_NOTICE
    assert "Apple Silicon version of VOOL" in notice
    assert libc.names == [b"sysctl.proc_translated"]


@pytest.mark.parametrize("translated", [0, None])
def test_a_native_mac_gets_no_notice(monkeypatch, translated):
    _darwin(monkeypatch, _Libc(translated=translated))
    assert local_compute_budget.apple_silicon_build_notice() == ""


def test_an_unreadable_flag_or_another_os_gets_no_notice(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no libc")

    monkeypatch.setattr("core.local_compute_budget.platform.system", lambda: "Darwin")
    monkeypatch.setattr(ctypes, "CDLL", broken)
    assert local_compute_budget.apple_silicon_build_notice() == ""
    local_compute_budget.running_translated_on_apple_silicon.cache_clear()
    monkeypatch.setattr("core.local_compute_budget.platform.system", lambda: "Linux")
    assert local_compute_budget.apple_silicon_build_notice() == ""


def _local_models(monkeypatch) -> dict:
    monkeypatch.setattr("core.web.api.runtime.installed_ollama_model_inventory", lambda **kwargs: ())
    response = dispatch_get(
        path="/api/models/local",
        query={},
        runtime=RuntimeServices(),
        model_name="vool",
        client_host="127.0.0.1",
        headers={},
    )
    assert response.status == 200
    return json.loads(bytes(response.body or b"{}").decode("utf-8"))


def test_the_local_models_listing_carries_the_notice_only_when_translated(monkeypatch):
    _darwin(monkeypatch, _Libc(translated=1))
    assert _local_models(monkeypatch)["apple_silicon_notice"] == local_compute_budget.APPLE_SILICON_BUILD_NOTICE
    local_compute_budget.running_translated_on_apple_silicon.cache_clear()
    _darwin(monkeypatch, _Libc(translated=0))
    assert _local_models(monkeypatch)["apple_silicon_notice"] == ""


def test_the_settings_local_models_widget_shows_the_notice():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[1] / "core" / "vool_settings_page.py").read_text(encoding="utf-8")
    widget = page[page.index("function widgetLocalModels"):]
    widget = widget[: widget.index("\n}\n")]
    assert (
        "if (data.apple_silicon_notice) body.appendChild(el('div', 'state failed', "
        "T('settings.local_models.apple_silicon_notice', data.apple_silicon_notice)));"
    ) in widget


def test_the_notice_is_in_every_language_catalog():
    from core.i18n.catalog import CATALOGS_DIR

    key = "settings.local_models.apple_silicon_notice"
    catalogs = sorted(CATALOGS_DIR.glob("*.json"))
    assert len(catalogs) > 1
    english = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"][key]
    assert english == local_compute_budget.APPLE_SILICON_BUILD_NOTICE
    for path in catalogs:
        text = json.loads(path.read_text(encoding="utf-8"))["messages"].get(key, "")
        assert "VOOL" in text and "Apple" in text, path.name
        if path.stem != "en":
            assert text != english, f"{path.name} carries the English text"
