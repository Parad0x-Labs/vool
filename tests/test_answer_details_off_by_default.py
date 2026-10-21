"""The line under every answer that names what produced it is OFF by default.

Product rule: no dev/debug text for normal users by default. A fresh install must serve answers
with no `tool | smalltalk_fast_path | no model | build ...` line under them, while the
machine-readable `answer_provenance` block — what receipts and bug reports read — stays on every
response exactly as before. The operator can turn the visible line back on from Settings, and an
explicit VOOL_SHOW_USAGE_FOOTER in the environment overrides the preference in BOTH directions.

Every case here drives the real `_finalize_turn_usage` seam the buffered chat answer goes through.
"""
from __future__ import annotations

import pytest

from core.app_version import VOOL_VERSION


def _smalltalk_turn() -> dict:
    """The shape `fast_path_result` really returns for the greeting lane."""
    return {
        "response": "Hi! How can I help?",
        "model_execution": {"source": "fast_path", "used_model": False},
        "model_calls": 0,
        "model_selected": "",
        "route": "deterministic:smalltalk_fast_path",
        "route_reason": "smalltalk_fast_path",
        "route_skips": ["model", "web", "tool_loop"],
        "fast_path_hit": True,
    }


@pytest.fixture()
def fresh_home(tmp_path, monkeypatch):
    """An empty runtime home: no preferences file exists, like a just-installed machine."""
    from core.runtime_paths import configure_runtime_home

    monkeypatch.delenv("VOOL_SHOW_USAGE_FOOTER", raising=False)
    configure_runtime_home(tmp_path / "home" / "data")
    yield


@pytest.fixture()
def quiet_router(monkeypatch):
    import core.memory_first_router as router

    monkeypatch.setattr(router, "get_turn_usage", lambda: None)


def _finalize(result: dict) -> dict:
    from core.web.api.runtime import _finalize_turn_usage

    return _finalize_turn_usage(result)


def test_a_fresh_install_serves_no_details_line_under_the_answer(fresh_home, quiet_router) -> None:
    finalized = _finalize(_smalltalk_turn())

    assert finalized["response"] == "Hi! How can I help?"
    assert f"build {VOOL_VERSION}" not in finalized["response"]
    assert "smalltalk_fast_path" not in finalized["response"]
    # The receipt the API contract promises is still on the result, unhidden.
    provenance = finalized["answer_provenance"]
    assert provenance["answer_source"] == "deterministic"
    assert provenance["model_ran"] is False
    assert provenance["recorded"] is True


def test_the_preference_turns_the_line_back_on(fresh_home, quiet_router) -> None:
    from core.user_preferences import load_preferences, save_preferences

    prefs = load_preferences()
    assert prefs.show_answer_details is False
    prefs.show_answer_details = True
    save_preferences(prefs)

    finalized = _finalize(_smalltalk_turn())

    assert finalized["response"].endswith(
        f"`tool | smalltalk_fast_path | no model | build {VOOL_VERSION}`"
    )


def test_the_environment_override_wins_both_ways(fresh_home, quiet_router, monkeypatch) -> None:
    from core.user_preferences import load_preferences, save_preferences

    # Preference ON, environment OFF: the developer override keeps the line hidden.
    prefs = load_preferences()
    prefs.show_answer_details = True
    save_preferences(prefs)
    monkeypatch.setenv("VOOL_SHOW_USAGE_FOOTER", "0")
    finalized = _finalize(_smalltalk_turn())
    assert finalized["response"] == "Hi! How can I help?"
    monkeypatch.delenv("VOOL_SHOW_USAGE_FOOTER", raising=False)

    # Preference OFF again, environment ON: the override shows the line anyway.
    prefs = load_preferences()
    prefs.show_answer_details = False
    save_preferences(prefs)
    assert load_preferences().show_answer_details is False
    monkeypatch.setenv("VOOL_SHOW_USAGE_FOOTER", "1")
    finalized = _finalize(_smalltalk_turn())
    assert finalized["response"].endswith(
        f"`tool | smalltalk_fast_path | no model | build {VOOL_VERSION}`"
    )


def test_an_unreadable_preference_file_counts_as_off(fresh_home, quiet_router) -> None:
    from core.runtime_paths import data_path

    data_path("user_preferences.json").write_text("{not json", encoding="utf-8")

    finalized = _finalize(_smalltalk_turn())

    assert finalized["response"] == "Hi! How can I help?"


def test_the_settings_row_turns_the_line_on_and_off_through_the_real_doors(fresh_home, quiet_router) -> None:
    """The General > Show answer details row: its own read/write binding, served routes, real gate."""
    import json

    from core.vool_settings_page import settings_groups
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    general = next(group for group in settings_groups() if group["id"] == "general")
    ids = [row["id"] for row in general["rows"]]
    assert ids.index("show_answer_details") == ids.index("deep_reasoning") + 1
    row = general["rows"][ids.index("show_answer_details")]
    url, field = row["write"]["url"], row["write"]["field"]
    assert (row["read"]["url"], row["read"]["field"]) == (url, field)

    def _set(value: bool) -> None:
        response = dispatch_post(
            path=url, body={field: value}, headers={"Content-Type": "application/json"},
            runtime=RuntimeServices(display_name="VOOL"), model_name="vool", workspace_root_provider=lambda: None,
            client_host="127.0.0.1",
        )
        assert response.status == 200, response.body
        read = dispatch_get(path=row["read"]["url"], query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool", client_host="127.0.0.1")
        assert read.status == 200 and json.loads(read.body)[field] is value

    read = dispatch_get(path=row["read"]["url"], query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool", client_host="127.0.0.1")
    assert json.loads(read.body)[field] is False

    _set(True)
    finalized = _finalize(_smalltalk_turn())
    assert finalized["response"].endswith(f"`tool | smalltalk_fast_path | no model | build {VOOL_VERSION}`")

    _set(False)
    finalized = _finalize(_smalltalk_turn())
    assert finalized["response"] == "Hi! How can I help?"
    assert finalized["answer_provenance"]["answer_source"] == "deterministic"
