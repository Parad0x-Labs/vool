"""Which assembled context opens a cloud preview by itself.

VOOL's own per-turn instructions and this chat's dialogue are the conversation,
not files or remembered information. A page remembered from another chat, a tool
observation, a profile item or an unknown source type still opens the preview.
"""
from __future__ import annotations

import pytest

from core.disclosure.wire import request_context
from core.provider_invocation_gateway import DirectProviderRequest
from core.remote_fetch_policy import remote_fetch_policy_scope

_INSTRUCTIONS = [
    "persona", "policy", "conversation_policy", "session_policy", "session_state",
    "dialogue_continuity", "task_constraints", "dialogue_turn", "turn_frame",
    "context_understanding", "self_knowledge", "operating_doctrine", "input_quality",
]


def _has_files(items, chat_id="chat-a"):
    request = DirectProviderRequest(trace_id="turn-1", context={"items_included": items}, metadata={},
                                    max_output_tokens=10)
    with remote_fetch_policy_scope({"chat_id": chat_id, "request_id": "turn-1"}):
        return request_context(request, provider="cloud-provider", cloud=True)["has_files"]


def _page(origin):
    return {"source_type": "turn_context_page", "metadata": {"scope": "chat", "memory_origin_chat_id": origin}}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))


def test_plain_chat_instructions_and_its_own_pages_open_no_preview():
    items = [{"source_type": kind} for kind in _INSTRUCTIONS] + [_page("chat-a")]
    assert _has_files(items) is False


@pytest.mark.parametrize("item", [
    _page("chat-b"),
    _page(""),
    {"source_type": "turn_context_page"},
    {"source_type": "tool_observation"},
    {"source_type": "runtime_memory"},
    {"source_type": "user_preferences"},
    {"source_type": "privacy_pact"},
    {"source_type": "canonical_document"},
    {"source_type": "a_type_added_later"},
])
def test_files_memory_and_unknown_sources_still_open_the_preview(item):
    assert _has_files([{"source_type": "persona"}, item]) is True


def test_a_page_is_never_this_chat_when_the_chat_is_unknown():
    assert _has_files([_page("")], chat_id="") is True
