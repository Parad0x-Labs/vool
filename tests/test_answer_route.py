"""The answer route reads only send receipts: destination and size from the sealed call, never words.

`dispatch_row` is fed the same objects the invocation gateway hands it (a sealed manifest and the
disclosure wire); `route_of_events` groups the turn's receipts the way the proof chip shows them.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.answer_route import EVENT_TYPE, WITHDRAWN_EVENT_TYPE, dispatch_row, flat_details, route_of_events


def _manifest(provider="ollama-local", model="qwen3:8b", manifest_id="m1", **flags):
    return SimpleNamespace(provider_id=provider, model_id=model, operation="chat", manifest_id=manifest_id,
                           selected_sources=flags.pop("sources", ()), **{
                               "user_profile_used": False, "semantic_retrieval_used": False, "history_used": False,
                               "project_context_used": False, "action_receipts_used": False, **flags})


def _wire(url, cloud=None):
    return SimpleNamespace(destination=url, cloud_destination=cloud)


def _event(row, seq):
    return {"event_type": EVENT_TYPE, "seq": seq, **flat_details(row)}


def test_destinations_are_read_from_the_address_and_never_default_to_this_computer():
    assert dispatch_row(_manifest(), _wire("http://127.0.0.1:11434/api/chat"), 10)["where"] == "this_computer"
    assert dispatch_row(_manifest(), _wire("http://localhost:11434/api/chat"), 10)["where"] == "this_computer"
    # A home-network address: link-local and IPv6 unique-local (the push scan keeps RFC 1918 literals out of the tree).
    assert dispatch_row(_manifest(), _wire("http://169.254.20.40:11434/api/chat"), 10)["where"] == "own_network"
    assert dispatch_row(_manifest(), _wire("http://[fd12:3456:789a::40]:11434/api/chat"), 10)["where"] == "own_network"
    online = dispatch_row(_manifest("openrouter"), _wire("https://203.0.113.7/v1/chat", cloud=True), 10)
    assert online["where"] == "online" and online["host"] == "203.0.113.7"
    # The disclosure wire's own cloud flag wins over a loopback-looking address (a relay on loopback).
    assert dispatch_row(_manifest(), _wire("http://127.0.0.1:9000/v1", cloud=True), 10)["where"] == "online"
    # No wire at all: unknown, never "this computer".
    assert dispatch_row(_manifest(), None, 10)["where"] == "unknown"


def test_what_the_payload_carried_comes_from_the_sealed_manifest():
    row = dispatch_row(_manifest(user_profile_used=True, history_used=True,
                                 sources=({"source_type": "tool_observation"}, {"source_type": "persona"})),
                       _wire("https://203.0.113.7/v1", cloud=True), 2048)
    assert row["carried"] == ["memory", "tool_results", "history"]
    assert dispatch_row(_manifest(sources=({"source_type": "persona"},)), _wire("http://127.0.0.1/x"), 1)["carried"] == []


def test_a_local_answer_over_a_cloud_check_is_mixed_and_counts_only_what_went_online():
    local = dispatch_row(_manifest(manifest_id="a"), _wire("http://127.0.0.1:11434/api/chat"), 900)
    cloud = dispatch_row(_manifest("openrouter", "glm-4.6", "b", user_profile_used=True),
                         _wire("https://203.0.113.7/v1", cloud=True), 3000)
    route = route_of_events([_event(local, 1), _event(cloud, 2)])
    assert route["summary"] == "mixed"
    assert route["online_bytes"] == 3000 and route["carried_online"] == ["memory"]
    assert [row["where"] for row in route["calls"]] == ["this_computer", "online"]


def test_repeat_calls_group_and_a_refused_send_is_withdrawn():
    first = dispatch_row(_manifest(manifest_id="a"), _wire("http://127.0.0.1:11434/api/chat"), 100)
    second = dispatch_row(_manifest(manifest_id="b"), _wire("http://127.0.0.1:11434/api/chat"), 50)
    refused = dispatch_row(_manifest("openrouter", "glm-4.6", "c"), _wire("https://203.0.113.7/v1", cloud=True), 4000)
    events = [_event(first, 1), _event(second, 2), _event(first, 3), _event(refused, 4),
              {"event_type": WITHDRAWN_EVENT_TYPE, "route_dispatch_id": "c", "seq": 5}]
    route = route_of_events(events)
    assert route["summary"] == "local"
    assert len(route["calls"]) == 1 and route["calls"][0]["calls"] == 2 and route["calls"][0]["bytes"] == 150


def test_no_receipts_is_none_and_an_unrecorded_destination_is_unknown():
    assert route_of_events([])["summary"] == "none"
    assert route_of_events([_event(dispatch_row(_manifest(), None, 5), 1)])["summary"] == "unknown"
