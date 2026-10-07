"""A local model listing with the wrong shape reads as "no local model", never as an exception.

Pack 2b review, 2026-10-07: `_ollama_has_chat_model` assumed `models` was a list of mappings whose
`details` was a mapping with a `families` list. Valid JSON with other nested types raised
AttributeError/TypeError out of the presence probe on the chat path instead of reading as unavailable.
"""
from __future__ import annotations

import pytest

from core import local_model_presence

MALFORMED_TAGS = [
    {"models": ["malformed-row"]},
    {"models": 123},
    {"models": [{"name": "chat", "details": "not-a-mapping"}]},
    {"models": [{"name": "chat", "details": {"families": 123}}]},
    {"models": [{"name": 42}]},
    {"models": [{"name": "chat", "details": {"families": ["llama", 7]}}]},
    {"models": [{"name": "llama3:8b"}, "stray"]},
]


@pytest.mark.parametrize("payload", MALFORMED_TAGS)
def test_a_malformed_ollama_listing_is_unavailable(monkeypatch, payload):
    monkeypatch.setattr(local_model_presence, "_get_json", lambda url: payload)
    assert local_model_presence._ollama_has_chat_model("http://127.0.0.1:1") is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"models": [{"name": "llama3:8b", "details": {"families": ["llama"]}}]}, True),
        ({"models": [{"name": "qwen3:4b"}]}, True),
        ({"models": [{"name": "nomic-embed-text", "details": {"families": ["nomic-bert"]}}]}, False),
        ({"models": []}, False),
        ({}, False),
    ],
)
def test_a_well_formed_listing_still_reads_as_it_did(monkeypatch, payload, expected):
    monkeypatch.setattr(local_model_presence, "_get_json", lambda url: payload)
    assert local_model_presence._ollama_has_chat_model("http://127.0.0.1:1") is expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"data": [{"id": "local-model"}]}, True),
        ({"data": "yes"}, False),
        ({"data": [1, 2]}, False),
        ({"data": [{"name": "no-id"}]}, False),
        ({"data": [{"id": ""}]}, False),
        ({"data": [{"id": "   "}]}, False),
        ({"data": [{"id": "\t\n"}]}, False),
        ({"data": [{"id": "local-model"}, {"id": " "}]}, False),
        ({"data": []}, False),
        (None, False),
    ],
)
def test_a_local_lane_counts_only_a_listing_of_models(payload, expected):
    assert local_model_presence._lane_lists_models(payload) is expected


@pytest.mark.parametrize("model_id", ["", "   ", "\t\n"])
def test_a_lane_listing_a_blank_model_id_keeps_the_saved_cloud_model(monkeypatch, model_id):
    # Pack 2b re-audit, 2026-10-07: a local lane whose /models reply named a blank id read as a running
    # local model, so an Auto turn dropped the owner's saved cloud model and had nothing to answer with.
    from core import cloud_only_default, local_model_policy, ollama_endpoint

    monkeypatch.setattr(local_model_policy, "local_models_enabled", lambda: True)
    monkeypatch.setattr(ollama_endpoint, "ollama_base_url", lambda: "http://127.0.0.1:1")
    monkeypatch.setattr(local_model_presence, "_get_json",
                        lambda url: {"models": []} if url.endswith("/api/tags") else {"data": [{"id": model_id}]})
    monkeypatch.setattr(local_model_presence, "_local_lane_endpoints", lambda: ["http://127.0.0.1:2/v1"])
    monkeypatch.setattr(cloud_only_default, "load",
                        lambda: {"decision": cloud_only_default.DECISION_USE_CLOUD, "model": "saved-cloud"})
    monkeypatch.setattr(cloud_only_default, "cloud_model_candidates", lambda: [{"id": "saved-cloud"}])
    presence = local_model_presence.local_model_presence(refresh=True)
    assert presence.running is False
    assert cloud_only_default.default_for_auto_turn(owner_local=True) == "saved-cloud"
