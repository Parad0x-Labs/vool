"""Transport setup and process creation spend the enclosing deadline budget."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from adapters import local_subprocess_adapter as transport
from adapters.base_adapter import ModelRequest
from core import runtime_active_clock
from core.provider_call_deadline import PROVIDER_DEADLINE_KEY


@pytest.mark.parametrize("seal_cost,spawn_cost", [(0.30, 0.0), (0.15, 0.15)])
def test_setup_never_restarts_the_enclosing_deadline(monkeypatch, seal_cost, spawn_cost):
    now = [100.0]
    calls = []
    monkeypatch.setattr(transport, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(runtime_active_clock, "monotonic", lambda: now[0])

    def seal(**kwargs):
        now[0] += seal_cost
        return SimpleNamespace(consume=lambda: kwargs["payload"])

    class Process:
        def communicate(self, *, input, timeout):
            calls.append(("communicate", timeout))
            now[0] += timeout
            raise subprocess.TimeoutExpired("blocked", timeout)

    process = Process()

    def spawn(*args, **kwargs):
        calls.append(("spawn", now[0]))
        now[0] += spawn_cost
        return process

    monkeypatch.setattr(transport, "seal_provider_invocation", seal)
    monkeypatch.setattr(transport.subprocess, "Popen", spawn)
    monkeypatch.setattr(transport, "_terminate_process_tree", lambda child: calls.append(("terminate", child)))
    adapter = transport.LocalSubprocessAdapter(SimpleNamespace(
        provider_name="bounded-local", provider_id="bounded-local", model_name="bounded-local",
        runtime_config={"command": ["blocked"], "timeout_seconds": 180.0},
    ))
    request = ModelRequest(task_kind="chat", prompt="hello", metadata={PROVIDER_DEADLINE_KEY: 100.25})
    with pytest.raises(subprocess.TimeoutExpired):
        adapter._invoke_with_env(request, extra_env={})
    assert not any(kind == "communicate" for kind, _ in calls), calls
    if seal_cost >= 0.25:
        assert calls == [], "expired preparation must not launch a process"
    else:
        assert calls[-1] == ("terminate", process), "a process created after expiry must be stopped"
