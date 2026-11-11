from __future__ import annotations

from unittest import mock

from core.local_ollama_inventory import installed_ollama_model_inventory


def test_inventory_preserves_exact_ollama_artifact_size_bytes() -> None:
    payload = [
        {"name": "qwen2.5:7b", "size": 4_683_087_332},
        {"name": "vool-qwen3-30b-a3b:nothink", "size": 18_556_698_002},
    ]
    with mock.patch("core.local_ollama_inventory._ollama_models_payload", return_value=payload):
        inventory = installed_ollama_model_inventory(env={})

    assert [(item.name, item.size_bytes) for item in inventory] == [
        ("qwen2.5:7b", 4_683_087_332),
        ("vool-qwen3-30b-a3b:nothink", 18_556_698_002),
    ]


def test_explicit_name_only_inventory_does_not_fabricate_measured_bytes() -> None:
    inventory = installed_ollama_model_inventory(
        env={"VOOL_INSTALLED_OLLAMA_MODELS": "qwen2.5:7b,vool-qwen3-30b-a3b:nothink"}
    )

    assert [(item.name, item.size_bytes) for item in inventory] == [
        ("qwen2.5:7b", 0),
        ("vool-qwen3-30b-a3b:nothink", 0),
    ]


def test_the_local_ollama_version_is_read_through_the_inventory_transport() -> None:
    # The Settings suggestion asks the local Ollama its version (older than 0.40 cannot pull the
    # current tags). It goes through the same reader as the model list, so no module grows a
    # second way out; an unreadable answer is "" and never blocks.
    from core.local_ollama_inventory import local_ollama_version

    with mock.patch("core.local_ollama_inventory._ollama_json", return_value={"version": "0.40.3"}) as read:
        assert local_ollama_version(base_url="http://127.0.0.1:11434") == "0.40.3"
    assert read.call_args.kwargs["path"] == "/api/version"
    with mock.patch("core.local_ollama_inventory._ollama_json", return_value=None):
        assert local_ollama_version(base_url="http://127.0.0.1:11434") == ""
