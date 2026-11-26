"""A picture ask that no engine here can make is answered with what to set up, never with an approval to allow.

iMac gate 43 on the installed 7eaa8608c (Offline, manual mode, no on-device engine): "draw me a little picture of a
lighthouse at dusk" came back "Generating an image needs your approval first", and nothing was there to approve."""
from __future__ import annotations

import pytest

from core.auto_local_only_mode import OFFLINE_KEY


class _Agent:
    def _emit_runtime_event(self, ctx, **kwargs):
        pass

    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason}


@pytest.fixture
def engines(monkeypatch):
    import core.authorized_tool_execution as ate
    from core import local_media_render as lmr
    from core import media_tools

    state = {"local": False, "key": False, "asked": 0}
    monkeypatch.setattr(lmr, "local_render_available", lambda: state["local"])
    monkeypatch.setattr(media_tools, "has_image_service", lambda *a, **k: state["key"])

    def _never(*a, **k):
        raise AssertionError("nothing may be generated in this test")

    monkeypatch.setattr(media_tools, "generate_image", _never)
    monkeypatch.setattr(ate, "execute_authorized_runtime_tool", _never)
    monkeypatch.setattr(lmr, "run_local_image_render", _never)
    real = ate.authorize_runtime_tool

    def _counted(*a, **k):
        state["asked"] += 1
        return real(*a, **k)

    monkeypatch.setattr(ate, "authorize_runtime_tool", _counted)
    return state


def _ask(text, context):
    from core.agent_runtime import fast_paths_media as fpm

    return fpm.maybe_handle_image_generation(_Agent(), text, session_id="s", source_context=context)


def test_offline_with_no_on_device_engine_names_the_engine_and_asks_nothing(engines):
    res = _ask("could you sketch a sailing boat on a calm lake", {"operating_mode": "manual", OFFLINE_KEY: True})
    assert res["reason"] == "image_generate_needs_setup"
    assert "approval" not in res["response"].lower()
    assert "Offline" in res["response"]
    assert engines["asked"] == 0


def test_online_with_no_engine_and_no_key_says_what_to_set_up_and_asks_nothing(engines):
    res = _ask("make me an image of a fox in the snow", {"operating_mode": "manual"})
    assert res["reason"] == "image_generate_needs_setup"
    assert "approval" not in res["response"].lower()
    assert engines["asked"] == 0


def test_a_cloud_key_online_still_goes_through_the_one_permission_decision(engines):
    engines["key"] = True
    res = _ask("make me an image of a fox in the snow", {"operating_mode": "manual"})
    assert engines["asked"] == 1
    assert res["reason"] != "image_generate_needs_setup"


def test_an_offline_chat_with_only_a_cloud_key_still_asks_nothing(engines):
    engines["key"] = True
    res = _ask("paint a little cottage by the sea", {"operating_mode": "manual", OFFLINE_KEY: True})
    assert res["reason"] == "image_generate_needs_setup" and engines["asked"] == 0
