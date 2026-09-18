from __future__ import annotations

import core.creative_set as cs
from core.runtime_execution_tools import _set_save, _set_use, execute_runtime_tool


def test_set_save_and_use_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "sets.json")
    save = _set_save({
        "name": "SET1", "character": "Aria", "appearance": "silver-streaked hair, green eyes",
        "wardrobe": "a torn charcoal cloak", "voice": "low and resolute",
        "setting": "a ruined bridge over a storm valley", "style": "dark fantasy", "aspect": "21:9",
    })
    assert save.ok and "SET1" in save.response_text
    use = _set_use({"name": "set1", "script": "she raises the cracked staff", "medium": "video"})
    assert use.ok
    assert "Aria" in use.response_text and "silver-streaked hair" in use.response_text
    assert "she raises the cracked staff" in use.response_text
    assert "EXACTLY consistent" in use.response_text and "21:9" in use.response_text


def test_set_save_needs_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    assert _set_save({}).ok is False


def test_set_use_unknown_set_lists_available(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    r = _set_use({"name": "ghost", "script": "x"})
    assert r.ok is False and "No set named" in r.response_text


def test_set_save_supports_characters_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    _set_save({"name": "duo", "characters": [
        {"name": "A", "appearance": "tall in white"}, {"name": "B", "appearance": "short in black"}]})
    r = _set_use({"name": "duo", "script": "they meet"})
    assert "A - tall in white" in r.response_text and "B - short in black" in r.response_text


def test_set_tools_dispatch_via_execute(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    saved = execute_runtime_tool("set.save", {"name": "S", "appearance": "a lone android in chrome"})
    assert saved is not None and saved.ok
    used = execute_runtime_tool("set.use", {"name": "S", "script": "it powers on"})
    assert used is not None and used.ok and "it powers on" in used.response_text


def test_set_save_from_description_paragraph(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    save = _set_save({"description":
        "NOVA: a chrome android with glowing blue eyes, wearing a tattered pilot jacket. "
        "The setting is a derelict space station. Style: cinematic sci-fi. Aspect 21:9."})
    assert save.ok
    use = _set_use({"name": "nova", "script": "it reboots in the dark"})
    assert use.ok
    assert "chrome android" in use.response_text and "tattered pilot jacket" in use.response_text
    assert "derelict space station" in use.response_text and "21:9" in use.response_text
    assert "it reboots in the dark" in use.response_text


def test_set_save_explicit_args_override_description(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    _set_save({"name": "MIX", "description": "a knight in a forest. Style: watercolor.",
               "style": "dark fantasy noir"})
    got = cs.get_set("MIX")
    assert got is not None and got.style == "dark fantasy noir"      # explicit style wins over parsed


def test_set_save_explicit_character_args_override_parsed_character(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cs, "_sets_path", lambda: tmp_path / "s.json")
    # description parses the character; explicit wardrobe/voice must still override field-by-field
    _set_save({"name": "T", "wardrobe": "a golden gown", "voice": "bright and cheery",
               "description": "Aria is a tall woman with green eyes, wearing a black trench coat. "
                              "She has a calm voice."})
    c = cs.get_set("T").characters[0]
    assert c.name == "Aria" and "green eyes" in c.appearance         # parsed identity kept
    assert c.wardrobe == "a golden gown" and c.voice == "bright and cheery"   # explicit args win
    use = _set_use({"name": "T", "script": "she turns"})
    assert "golden gown" in use.response_text and "bright and cheery" in use.response_text
