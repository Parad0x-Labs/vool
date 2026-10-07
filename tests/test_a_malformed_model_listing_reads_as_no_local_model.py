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
        ({"data": []}, False),
        (None, False),
    ],
)
def test_a_local_lane_counts_only_a_listing_of_models(payload, expected):
    assert local_model_presence._lane_lists_models(payload) is expected
