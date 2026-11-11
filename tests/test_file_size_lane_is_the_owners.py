"""The size lane measures and searches as the owner, so a turn from a phone or a chat app never takes it.

Independent review 2026-10-30 (0.7.7 red-team P1 fix): ``size_answer_for_ask`` stats a typed path, walks a folder
and finds names across the home folders with no source context, so "how big is ~/.ssh/id_ed25519" from a phone with
no computer rights would have told it what exists on the computer and how big it is. A remote turn now goes on to
the model lane, where every tool passes the remote-turn fence and the phone's folder limit.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import request_trust as rt
from core.agent_runtime import fast_paths_machine


class _Agent(SimpleNamespace):
    def _fast_path_result(self, **kwargs):
        return {"fast_path": kwargs["reason"], "response": kwargs["response"]}


@pytest.fixture
def sized(monkeypatch):
    calls = []
    monkeypatch.setattr(fast_paths_machine, "size_answer_for_ask",
                        lambda text: calls.append(text) or {"response_text": "id_ed25519 is 411 B."})
    return calls


@pytest.mark.parametrize("context", [
    {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: False},
    {rt.HTTP_INGRESS_KEY: True},
])
def test_a_remote_turn_never_reaches_the_size_lane(sized, context):
    result = fast_paths_machine.maybe_handle_direct_machine_read_request(
        _Agent(), "how big is ~/.ssh/id_ed25519", session_id="s", source_surface="channel", source_context=context)
    assert sized == []
    assert not (isinstance(result, dict) and result.get("fast_path") == "file_size_fast_path")


@pytest.mark.parametrize("context", [{}, {rt.HTTP_INGRESS_KEY: True, rt.OWNER_LOCAL_KEY: True}])
def test_the_owners_turn_still_gets_the_size(sized, context):
    result = fast_paths_machine.maybe_handle_direct_machine_read_request(
        _Agent(), "how big is ~/.ssh/id_ed25519", session_id="s", source_surface="channel", source_context=context)
    assert sized == ["how big is ~/.ssh/id_ed25519"]
    assert result == {"fast_path": "file_size_fast_path", "response": "id_ed25519 is 411 B."}
