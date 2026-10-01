"""The Wave-1 localization surfaces must resolve through the catalog, not inline English.

Pins the follow-up named by the 2026-10-01 localization delivery's "remaining gaps":
- the setup checklist presentation (settings widget + chat progress line) resolves
  through ``setup.step.*`` / ``setup.checklist.*`` / ``chat.setup_line_progress``,
  served per the requester's own UI locale;
- the chat empty-state greetings and rotating openers read ``first_run.greet_*`` /
  ``first_run.opener_*`` through VOOLT (the keys existed; the surface never read them);
- the settings nav chrome (back button, search placeholder/aria, section head) carries
  catalog wiring;
- the chats-panel project explainer and the four stray toasts resolve through keys;
- the palette shortcut hint translates the label words, never the <kbd> key caps.
"""

from __future__ import annotations

from core.i18n.catalog import catalog_for, clear_catalog_cache
from core.setup_progress import snapshot
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html

ENGLISH = catalog_for("en")


def teardown_function() -> None:
    clear_catalog_cache()


def test_setup_snapshot_localizes_step_presentation_per_locale() -> None:
    en = snapshot(locale="en")
    lt = snapshot(locale="lt")
    # The projection never depends on the locale: same steps, same shape.
    assert [s["id"] for s in en["steps"]] == [s["id"] for s in lt["steps"]]
    assert {k for s in en["steps"] for k in s} == {k for s in lt["steps"] for k in s}
    # The English authority is the source of truth for en.
    for step in en["steps"]:
        spec = next(s for s in __import__("core.setup_progress", fromlist=["STEPS"]).STEPS if s.id == step["id"])
        assert step["title"] == spec.title and step["sentence"] == spec.sentence and step["later"] == spec.later
    # A translated locale either localizes the presentation or falls back to the
    # English visibly — it must never invent a third text.
    for step in lt["steps"]:
        spec = next(s for s in __import__("core.setup_progress", fromlist=["STEPS"]).STEPS if s.id == step["id"])
        catalog_step = catalog_for("lt").text(f"setup.step.{step['id']}.title")
        expected = catalog_step if catalog_step and catalog_step != f"setup.step.{step['id']}.title" else spec.title
        assert step["title"] == expected


def test_settings_checklist_and_nav_resolve_through_catalog() -> None:
    html = render_vool_settings_html()
    # Nav chrome rides data-i18n attributes the bundle sweeps.
    assert 'data-i18n="settings.nav.back"' in html
    assert 'data-i18n-placeholder="settings.nav.search"' in html
    assert 'data-i18n-aria-label="settings.nav.search"' in html
    assert "T('settings.nav.results', 'Results')" in html
    assert "T('settings.nav.head', 'Settings')" in html
    # The checklist widget's pills/buttons/notes ride T()/tfmt() with the catalog
    # English as fallback literals.
    for call in (
        "T('setup.checklist.done', 'Done')",
        "T('setup.checklist.skipped', 'Skipped')",
        "T('setup.checklist.todo', 'To do')",
        "tfmt('setup.checklist.later_prefix', 'later: {later}'",
        "T('setup.checklist.continue', 'Continue setup')",
        "T('setup.checklist.start', 'Start setup')",
        "T('setup.checklist.dismiss'",
        "T('setup.checklist.reminder_hidden'",
        "tfmt('setup.checklist.not_saved', 'Not saved — {error}'",
        "T('setup.checklist.disappears'",
    ):
        assert call in html, call


def test_chat_greetings_openers_and_line_resolve_through_voolt() -> None:
    html = render_vool_chat_html()
    assert "function emptyT(key, fallback)" in html
    # Every greeting period and opener rides its catalog key with an English fallback.
    for key in ("first_run.greet_morning", "first_run.greet_afternoon", "first_run.greet_evening",
                "first_run.greet_late_night", "first_run.greet_working_late"):
        assert f"'{key}'" in html, key
    for n in range(1, 8):
        assert f"'first_run.opener_{n}'" in html
    # The dynamic setup progress line formats through the bundle's params twin.
    assert "'chat.setup_line_progress'" in html
    # The chats-panel explainer and the four stray toasts ride keys.
    assert "emptyT('chats.project_note'" in html
    for call in ("chat.toast_try_once_grant", "chat.toast_move_failed",
                 "chat.toast_project_failed", "chat.toast_copy_empty"):
        assert f"emptyT('{call}'" in html, call


def test_palette_hint_translates_labels_not_keycaps() -> None:
    from core.command_palette_fragment import render_palette_fragment

    fragment = render_palette_fragment()
    assert "function vpHintT(key, fallback)" in fragment
    for key in ("palette.hint_palette", "palette.hint_new_chat", "palette.hint_activity",
                "palette.hint_pet", "palette.hint_search_chats", "palette.hint_settings",
                "palette.hint_esc_stop"):
        assert f"'{key}'" in fragment, key
    # The <kbd> key caps stay structure: exactly seven of them, untranslated.
    assert fragment.count("<kbd>") >= 7
    assert "⌘K</kbd> '" in fragment and "<kbd>Esc</kbd> '" in fragment


def test_new_keys_exist_in_the_english_catalog() -> None:
    for key in (
        "chat.setup_line_progress", "chats.project_note", "settings.nav.back",
        "settings.nav.search", "settings.nav.head", "settings.nav.results",
        "setup.checklist.done", "setup.checklist.skipped", "setup.checklist.todo",
        "setup.checklist.later_prefix", "setup.checklist.continue", "setup.checklist.start",
        "setup.checklist.dismiss", "setup.checklist.saving", "setup.checklist.not_saved",
        "setup.checklist.reminder_hidden", "setup.checklist.disappears",
        "chat.toast_try_once_grant", "chat.toast_move_failed", "chat.toast_project_failed",
        "chat.toast_copy_empty", "palette.hint_palette", "palette.hint_new_chat",
        "palette.hint_activity", "palette.hint_pet", "palette.hint_search_chats",
        "palette.hint_settings", "palette.hint_esc_stop",
    ):
        assert ENGLISH.text(key) and ENGLISH.text(key) != key, key
