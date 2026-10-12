"""A pinned transport fixture must preserve the adapter's real low-resource refusal."""
from types import SimpleNamespace
from unittest import mock

import pytest

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core.resource_governor import plan_model_load
from tests.provider_fixture_resources_20261007 import pin_fixture_host_memory


@pytest.mark.parametrize("available,expected", [(64.0, True), (4.0, False)])
def test_real_governor_decides_on_known_host_inputs(monkeypatch, available, expected):
    pin_fixture_host_memory(monkeypatch, available)
    assert plan_model_load("qwen2.5:14b").ok is expected


@pytest.mark.parametrize("path", ["run", "stream"])
def test_pinned_low_memory_still_refuses_before_provider_http(monkeypatch, path):
    pin_fixture_host_memory(monkeypatch, 4.0)
    adapter = OpenAICompatibleAdapter(SimpleNamespace(
        provider_id="fixture-ollama:qwen2.5:14b", model_name="qwen2.5:14b",
        metadata={"runtime_family": "ollama"},
        runtime_config={"base_url": "http://127.0.0.1:11434", "timeout_seconds": 5.0},
    ))
    request = ModelRequest(task_kind="conversation", prompt="hello", output_mode="plain_text")
    with mock.patch("adapters.openai_compatible_adapter.requests.post") as post:
        with pytest.raises(RuntimeError, match="model_load_gated_low_memory"):
            if path == "run":
                adapter.run_text_task(request)
            else:
                list(adapter.stream_text_task(request))
    post.assert_not_called()


@pytest.mark.parametrize("available,expected", [(64.0, True), (4.0, False)])
def test_owned_daemon_host_fixture_keeps_the_real_load_decision(monkeypatch, available, expected):
    from core import resource_governor, system_resources
    from tests.served_provider_fixture_20261007 import configure_fixture_host
    # Preserve the parent process inputs; only the explicitly opted-in child owns these pins.
    monkeypatch.setattr(system_resources, "snapshot", system_resources.snapshot)
    monkeypatch.setattr(resource_governor, "resident_models", resource_governor.resident_models)
    configure_fixture_host(available)
    assert plan_model_load("qwen2.5:14b").ok is expected
