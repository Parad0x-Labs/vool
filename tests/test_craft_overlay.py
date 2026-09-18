from __future__ import annotations

import core.craft_overlay as craft_overlay
from core.visual_playbooks import (
    VISUAL_PLAYBOOKS,
    resolve_visual_playbook,
    visual_playbook_directive,
)
from core.writing_craft import (
    GENRE_CRAFT,
    build_writing_system_prompt,
    resolve_genre_craft,
)


def _patch_section(monkeypatch, section: str, mapping: dict) -> None:
    def fake(sec: str, key: str) -> dict:
        return dict(mapping.get(key, {})) if sec == section else {}
    monkeypatch.setattr(craft_overlay, "overlay_for", fake)


def test_no_overlay_returns_the_shipped_object(monkeypatch) -> None:
    monkeypatch.setattr(craft_overlay, "overlay_for", lambda s, k: {})
    assert resolve_genre_craft("horror") is GENRE_CRAFT["horror"]
    assert resolve_visual_playbook("horror") is VISUAL_PLAYBOOKS["horror"]


def test_none_key_resolves_to_none() -> None:
    assert resolve_genre_craft(None) is None
    assert resolve_visual_playbook(None) is None


def test_writing_overlay_merges_and_preserves_other_fields(monkeypatch) -> None:
    _patch_section(monkeypatch, "writing_craft", {"horror": {"craft_directive": "OVERLAID DIRECTIVE"}})
    r = resolve_genre_craft("horror")
    assert r.craft_directive == "OVERLAID DIRECTIVE"
    assert r.register == GENRE_CRAFT["horror"].register        # untouched
    assert r.pitfalls == GENRE_CRAFT["horror"].pitfalls        # untouched
    assert "OVERLAID DIRECTIVE" in build_writing_system_prompt("horror")


def test_visual_overlay_merges_into_directive(monkeypatch) -> None:
    _patch_section(
        monkeypatch, "visual_playbooks",
        {"cyberpunk": {"camera_language": "OVERLAID CAMERA", "negative_prompt": "OVERLAID NEG"}},
    )
    r = resolve_visual_playbook("cyberpunk")
    assert r.camera_language == "OVERLAID CAMERA"
    assert r.negative_prompt == "OVERLAID NEG"
    d = visual_playbook_directive("cyberpunk")
    assert "OVERLAID CAMERA" in d and "OVERLAID NEG" in d


def test_unknown_and_nonstring_overlay_fields_ignored(monkeypatch) -> None:
    _patch_section(
        monkeypatch, "writing_craft",
        {"horror": {"bogus": "x", "craft_directive": 123, "register": "   "}},
    )
    # Nothing valid to apply -> the shipped object is returned unchanged.
    assert resolve_genre_craft("horror") is GENRE_CRAFT["horror"]


def test_overlay_for_parses_sections(monkeypatch) -> None:
    monkeypatch.setattr(craft_overlay, "_load", lambda: {"writing_craft": {"horror": {"register": "R"}}})
    assert craft_overlay.overlay_for("writing_craft", "horror") == {"register": "R"}
    assert craft_overlay.overlay_for("writing_craft", "missing") == {}
    assert craft_overlay.overlay_for("visual_playbooks", "horror") == {}
    assert craft_overlay.overlay_for("writing_craft", "") == {}
