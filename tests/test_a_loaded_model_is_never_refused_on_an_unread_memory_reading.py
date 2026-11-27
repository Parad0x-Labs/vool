"""A memory reading that failed is not "no memory free", and a loaded model is never a new load.

Sealed benchmark run on 21d4d4046 (local qwen3:8b, 24 GB iMac): after two empty tool calls the turn fell back to
qwen3:4b, qwen3.5:9b and qwen3:8b, and the low-memory gate refused all three at "~0.0 GB free even after reclaiming"
while about 5.8 GB was inactive and qwen3:8b was already resident. The reply was "I can't publish this answer".
Model names here are the shipped local models; sizes are invented for the contract.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.resource_governor as gov
from core import system_resources as sr


def _snap(available: float, *, read: bool = True) -> SimpleNamespace:
    return SimpleNamespace(available_ram_gb=available, ram_read=read)


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(gov, "_local_ollama_base", lambda base_url=None: "http://127.0.0.1:11434")
    unloaded: list[list[str]] = []
    monkeypatch.setattr(gov, "_unload_models", lambda names, **_k: unloaded.append(list(names)) or list(names))
    monkeypatch.setattr(gov, "estimated_model_gb", lambda name, **_k: 5.2)
    return unloaded


def test_an_unreadable_list_of_loaded_models_never_refuses_the_load(local, monkeypatch):
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(0.5))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: None)
    assert gov.plan_model_load("qwen3:8b").ok is True
    assert local == []


@pytest.mark.parametrize("listed", ["qwen3:8b:latest", "QWEN3:8B", "qwen3:8b"])
def test_a_loaded_model_listed_by_its_full_tag_is_never_gated(listed, local, monkeypatch):
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(0.2))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: [{"name": listed, "size_gb": 7.7}])
    assert gov.plan_model_load("qwen3:8b").ok is True
    assert local == []


def test_a_failed_memory_reading_does_not_refuse_a_model_that_is_not_loaded(local, monkeypatch):
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(0.0, read=False))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: [{"name": "qwen3:8b", "size_gb": 7.7}])
    assert gov.plan_model_load("qwen3:4b").ok is True
    assert local == []  # nothing is unloaded on a reading that failed


def test_a_real_low_memory_reading_still_refuses(local, monkeypatch):
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(1.0))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: [])
    assert gov.plan_model_load("qwen3:4b").ok is False


def test_the_plan_names_no_model_unloadable_on_an_unread_machine(local, monkeypatch):
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(0.0, read=False))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: [])
    assert gov.models_that_cannot_load_now(["qwen3:4b", "qwen3.5:9b"]) == frozenset()
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(1.0))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: None)
    assert gov.models_that_cannot_load_now(["qwen3:4b"]) == frozenset()


def test_the_plan_never_names_the_loaded_model_by_its_full_tag(local, monkeypatch):
    monkeypatch.setattr(gov.sr, "snapshot", lambda **_k: _snap(1.0))
    monkeypatch.setattr(gov, "resident_models", lambda **_k: [{"name": "qwen3:8b:latest", "size_gb": 7.7}])
    assert gov.models_that_cannot_load_now(["qwen3:8b", "qwen3:4b"]) == frozenset({"qwen3:4b"})


_VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                5120.
Pages active:                            700000.
Pages inactive:                          380000.
Pages speculative:                         1000.
Pages wired down:                        300000.
Pages purgeable:                           2000.
"""


@pytest.mark.parametrize(("vm_stat", "read"), [(_VM_STAT, True), ("", False)])
def test_a_mac_snapshot_says_whether_its_memory_was_read(vm_stat, read, monkeypatch):
    def run(cmd, timeout=2.0):
        if cmd[0] == "vm_stat":
            return vm_stat
        if cmd[-1] == "hw.memsize":
            return str(24 * 1024 ** 3)
        return ""

    monkeypatch.setattr(sr.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sr, "_run", run)
    monkeypatch.setattr(sr, "_ollama_loaded_bytes", lambda **_k: 0.0)
    monkeypatch.setattr(sr, "_comfyui_running", lambda: False)
    snap = sr._build_snapshot()
    assert snap.ram_read is read
    if read:
        assert snap.available_ram_gb > 5.0  # inactive pages count as free, not only the strict free pages


def test_a_system_tool_is_found_by_its_full_path_when_the_path_is_bare(monkeypatch):
    seen: list[list[str]] = []

    def run(cmd, **_k):
        seen.append(cmd)
        return SimpleNamespace(stdout="ok")

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(sr.subprocess, "run", run)
    assert sr._run(["sh", "-c", "true"]) == "ok"
    assert seen[0][0].startswith("/")
