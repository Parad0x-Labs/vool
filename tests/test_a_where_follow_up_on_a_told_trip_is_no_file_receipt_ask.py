"""A "where" follow-up the turn's judge read as a recall is not answered from file-write receipts.

iMac gate58 on 9a267b88d (qwen3:8b): after "when does <name> land?", "and from where?" was answered "I have no
file-write receipt in this session, so I cannot say where a file went", and the saved trip was never read. The judge had
read it as a recall. Wordings here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import plugin_ask
from core.agent_runtime import fast_paths_receipt_location as lane


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


class _Agent:
    def _fast_path_result(self, **kwargs):
        return dict(kwargs)


@pytest.mark.parametrize("text", ["and from where?", "where was it again?", "and where is that?"])
def test_a_judged_recall_with_no_receipt_is_left_to_the_memory_lane(text, monkeypatch):
    monkeypatch.setattr(lane, "latest_file_action_receipt", lambda *_a, **_k: None)
    monkeypatch.setattr(lane, "looks_like_location_ask", lambda _t: True)
    monkeypatch.setattr(lane, "location_followup_kind", lambda _t, receipt=None: "where")
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda _t: "recall")
    result = lane.maybe_handle_action_receipt_location_request(
        _Agent(), text, session_id="chat-1", source_surface="api", source_context={"surface": "api"})
    assert result is None


def test_a_where_ask_with_no_receipt_and_no_recall_still_gets_the_receipt_answer(monkeypatch):
    monkeypatch.setattr(lane, "latest_file_action_receipt", lambda *_a, **_k: None)
    monkeypatch.setattr(lane, "looks_like_location_ask", lambda _t: True)
    monkeypatch.setattr(lane, "location_followup_kind", lambda _t, receipt=None: "where")
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda _t: None)
    monkeypatch.setattr(lane, "_judged_a_find_on_disk", lambda _t: False)
    result = lane.maybe_handle_action_receipt_location_request(
        _Agent(), "where did the file go?", session_id="chat-1", source_surface="api", source_context={"surface": "api"})
    assert result is not None and "receipt" in str(result.get("response") or "").lower()
