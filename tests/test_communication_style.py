"""Communication-style preference: persistence, directive mapping, command, prompt injection."""
from __future__ import annotations

import pytest

from core import runtime_paths
from core import user_preferences as up


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def test_default_is_casual_and_roundtrips() -> None:
    assert up.load_preferences().communication_style == "casual"
    prefs = up.load_preferences()
    prefs.communication_style = "cheeky"
    up.save_preferences(prefs)
    assert up.load_preferences().communication_style == "cheeky"


def test_normalize_rejects_unknown() -> None:
    assert up._normalize_communication_style("business") == "business"
    assert up._normalize_communication_style("CHEEKY") == "cheeky"
    assert up._normalize_communication_style("bogus") == "casual"
    assert up._normalize_communication_style("") == "casual"


def test_directive_per_style() -> None:
    assert "professional" in up.communication_style_directive("business").lower()
    assert "friend" in up.communication_style_directive("casual").lower()
    cheeky = up.communication_style_directive("cheeky").lower()
    assert "cheeky" in cheeky and "emoji" in cheeky
    assert up.communication_style_directive("unknown") == ""


def test_directive_reads_saved_pref() -> None:
    prefs = up.load_preferences()
    prefs.communication_style = "cheeky"
    up.save_preferences(prefs)
    assert "cheeky" in up.communication_style_directive().lower()  # None -> reads saved pref


def test_command_sets_style() -> None:
    ok, msg = up.maybe_handle_preference_command("communication style: cheeky")
    assert ok and "cheeky" in msg.lower()
    assert up.load_preferences().communication_style == "cheeky"
    ok2, _ = up.maybe_handle_preference_command("style: business")
    assert ok2 and up.load_preferences().communication_style == "business"


def test_bare_style_words_do_not_hijack_communication_style() -> None:
    # "be concise" is an Operator Profile response-style statement (a candidate the user
    # confirms); the preference surface neither saves it silently nor turns it into a persona,
    # and communication_style is untouched.
    ok, _ = up.maybe_handle_preference_command("be concise")
    assert ok is False
    prefs = up.load_preferences()
    assert prefs.communication_style == "casual"
    assert prefs.character_mode == ""
    assert prefs.style_notes == ""
    from core.operator_profile_interpretation import interpret_profile_turn

    proposal = interpret_profile_turn("be concise")[0]
    assert (proposal.category, proposal.value, proposal.strength) == ("response_style", "concise", "strong")


def test_prompt_helper_injects_active_style() -> None:
    from core import prompt_normalizer as pn

    prefs = up.load_preferences()
    prefs.communication_style = "cheeky"
    up.save_preferences(prefs)
    guide = pn._communication_style_guide()
    assert "cheeky" in guide.lower() and "emoji" in guide.lower()
