"""Guard: VOOL is what a user and a MODEL are told, while frozen identifiers stay frozen.

The "I am Vool" replies had a concrete root cause: the system prompt said "You are VOOL" and the
tool catalogue injected into every tool-intent turn named OpenClaw and VOOL. The model was told its
identity in the same breath as its tools. See docs/VOOL_IDENTITY_COMPATIBILITY_MAP.md.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_the_model_is_told_it_is_vool():
    prompt_src = (REPO / "core" / "prompt_normalizer.py").read_text(encoding="utf-8")
    assert "You are VOOL running on the user's own machine" in prompt_src
    assert "You are NULLA running on" not in prompt_src


def test_tool_catalogue_carries_no_foreign_or_legacy_branding():
    from core.prompt_normalizer import _tool_intent_catalog_text

    catalog = _tool_intent_catalog_text()
    # A second product's name in our own tool prompt is how a model learns to call itself OpenClaw.
    assert "OpenClaw" not in catalog
    assert not re.search(r"\bNULLA\b", catalog)


def test_display_name_falls_back_to_vool(monkeypatch):
    import core.onboarding as onboarding

    monkeypatch.setattr(onboarding, "load_identity", lambda: {})
    assert onboarding.get_agent_display_name() == "VOOL"


def test_legacy_stored_name_is_mapped_at_read_but_a_chosen_name_is_kept(monkeypatch):
    import core.onboarding as onboarding

    # An install bootstrapped before the rename -> mapped for display, store untouched.
    monkeypatch.setattr(onboarding, "load_identity", lambda: {"agent_name": "VOOL"})
    assert onboarding.get_agent_display_name() == "VOOL"
    monkeypatch.setattr(onboarding, "load_identity", lambda: {"agent_name": "vool"})
    assert onboarding.get_agent_display_name() == "VOOL"
    # A name the user deliberately chose is never overridden.
    monkeypatch.setattr(onboarding, "load_identity", lambda: {"agent_name": "Jarvis"})
    assert onboarding.get_agent_display_name() == "Jarvis"


def test_frozen_identifiers_are_not_renamed():
    """Production stays frozen while the local/demo wrapper is deliberately isolated."""
    plist_src = (REPO / "installer" / "bundle" / "build_macos_app.sh").read_text(encoding="utf-8")
    assert 'BUNDLE_IDENTIFIER="ai.nulla.desktop"' in plist_src, "release bundle identifier must stay frozen"
    assert 'BUNDLE_IDENTIFIER="ai.nulla.desktop.local"' in plist_src, "local builds must not collide with release"
    page = (REPO / "core" / "vool_chat_page.py").read_text(encoding="utf-8")
    assert "_VOOL_CHAT_HTML" in page, "client-side keys must stay frozen"


def test_compatibility_map_exists_and_states_the_rule():
    doc = (REPO / "docs" / "VOOL_IDENTITY_COMPATIBILITY_MAP.md").read_text(encoding="utf-8")
    assert "ai.nulla.desktop" in doc, "the map must document the frozen legacy bundle id"
    assert "agent_name_registry" in doc
    assert "rename what a user or a model reads" in doc.lower()
