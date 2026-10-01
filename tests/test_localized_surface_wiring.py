"""The new public surfaces must resolve through the catalog, not inline English.

Pins (2026-10-01 localization completion):
- the bypass setup dialog, banner and readiness states carry catalog wiring; the
  API's workspace_reason stays language-independent and the page maps it to the
  ``bypass.readiness.<reason>`` presentation (never parsing prose);
- the provider guide resolves its category/provider copy through the catalog at
  the metadata owner, per requested locale;
- the settings key-entry form's group membership is decided by the optgroup's
  language-independent family marker, never by its translated label;
- the notification popover and model-radar chrome resolve through VOOLT-backed
  helpers whose fallback literals are the catalog English.
"""

from __future__ import annotations

import json
import re

from core.i18n.catalog import catalog_for, clear_catalog_cache
from core.notification_fragment import render_notification_fragment
from core.model_radar_fragment import render_model_radar_fragment
from core.provider_key_guide import provider_key_guide
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html

ENGLISH = catalog_for("en")


def teardown_function() -> None:
    clear_catalog_cache()


def test_bypass_dialog_markup_carries_catalog_attributes() -> None:
    html = render_vool_chat_html(build_commit="t")
    dialog = re.search(r'<div id="bypassOverlay".*?\n</div>\n', html, re.DOTALL)
    assert dialog, "bypass dialog markup missing"
    body = dialog.group(0)
    for attribute, key in (
        ("data-i18n", "bypass.modal_title"),
        ("data-i18n-html", "bypass.modal_warning"),
        ("data-i18n", "bypass.scope_label"),
        ("data-i18n", "bypass.duration_label"),
        ("data-i18n", "bypass.duration.until_off"),
        ("data-i18n", "bypass.setup.label"),
        ("data-i18n", "bypass.setup.help"),
        ("data-i18n", "bypass.custom_label"),
        ("data-i18n", "bypass.modal_limits"),
        ("data-i18n", "bypass.confirm"),
    ):
        assert f'{attribute}="{key}"' in body, f"dialog field {key} not wired"


def test_bypass_readiness_maps_reason_codes_not_prose() -> None:
    html = render_vool_chat_html(build_commit="t")
    # The page resolves by the API's typed reason code; the English message only rides
    # as the fallback argument.
    assert "pageT('bypass.readiness.' + String(data.workspace_reason || '')" in html
    assert "pageTF('bypass.readiness.with_folder'" in html
    # every reason the API can return has a catalog presentation
    for reason in {"project", "unbound", "protected_workspace", "project_missing", "unreadable", "deleted_chat", "missing_chat", "unavailable"}:
        assert f"bypass.readiness.{reason}" in ENGLISH.keys


def test_bypass_dialog_localizes_in_every_shipped_catalog_locale() -> None:
    from core.i18n.locales import ui_catalog_tags

    for tag in ui_catalog_tags():
        html = render_vool_chat_html(build_commit="t", ui_locale=tag)
        catalog = catalog_for(tag)
        title = catalog.text("bypass.modal_title")
        # The dialog markup carries data-i18n; the swap happens client-side, so the
        # localized title must ride the bootstrap bundle the page ships.
        assert f'data-i18n="bypass.modal_title"' in html
        assert json.dumps(title, ensure_ascii=False)[1:-1] in html, f"{tag}: bundle lacks {title!r}"
        # the readiness vocabulary must be present in the bundle for the same reason
        readiness = catalog.text("bypass.readiness.unbound")
        assert json.dumps(readiness, ensure_ascii=False)[1:-1] in html, tag


def test_provider_guide_resolves_copy_per_locale() -> None:
    from core.i18n.locales import ui_catalog_tags

    for tag in ui_catalog_tags():
        guide = provider_key_guide(tag)
        catalog = catalog_for(tag)
        by_id = {g["id"]: g for g in guide}
        assert by_id["security"]["title"] == catalog.text("keyguide.security.title"), tag
        assert by_id["models"]["description"] == catalog.text("keyguide.models.description"), tag
        eyebrow = by_id["security"]["providers"][0]
        assert eyebrow["description"] == catalog.text("keyguide.provider.eyebrow.description"), tag
        # brand identifiers never translate
        assert eyebrow["label"] == "Eyebrow"
        assert eyebrow["url"] == "https://eyebrow.cc/dashboard"


def test_settings_key_form_uses_family_markers_not_labels() -> None:
    html = render_vool_settings_html(build_commit="t")
    assert "g.dataset.family = family;" in html
    assert "g.dataset.family) || ''" in html
    # group membership rides the marker, never a displayed (translated) label string
    assert "selectedGroup() === 'search'" in html
    assert "selectedGroup() === 'Web search'" not in html


def test_notification_and_radar_fragments_resolve_through_voolt() -> None:
    notify = render_notification_fragment()
    assert "function NTF(" in notify and "VOOLT" in notify
    # a previously raw literal now rides a key
    assert "NTF('notif.tab.needs', 'Needs you')" in notify
    assert "NTF('notif.foot_note'" in notify
    radar = render_model_radar_fragment()
    assert "function RT(" in radar and "VOOLT" in radar
    assert "RT('radar.try_once', 'Try once')" in radar


def test_changed_english_source_keys_have_fresh_transations_everywhere() -> None:
    """The four reworded keys must not serve their old translations: after the
    refresh every shipped catalog either carries the new wording's translation or
    (by the engine's law) visibly falls back — the old texts must be gone."""
    from core.i18n.locales import ui_catalog_tags

    old_texts = {
        "bypass.confirm": "Confirm limited bypass",
        "notif.empty": "No notifications yet",
    }
    for tag in ui_catalog_tags():
        catalog = catalog_for(tag)
        for key, old_prefix in old_texts.items():
            assert not catalog.text(key).startswith(old_prefix), (tag, key)


def test_setup_projection_resolves_presentation_per_locale() -> None:
    """The checklist snapshot keeps its authority fields locale-free while the
    step presentation resolves through the catalog at the projection owner."""
    from core import setup_progress

    snap_en = setup_progress.snapshot()
    snap_lt = setup_progress.snapshot(locale="lt")
    for a, b in zip(snap_en["steps"], snap_lt["steps"]):
        assert a["id"] == b["id"]  # the projection itself never depends on locale
        assert a["done"] == b["done"]
    assert "setup.step.thinking.title" in ENGLISH.keys
    lt = catalog_for("lt")
    if not lt.diagnostic.stale_source and "setup.step.thinking.title" in lt.locale_keys:
        assert snap_lt["steps"][0]["title"] == lt.text("setup.step.thinking.title")


def test_chrome_and_widget_states_resolve_through_catalog() -> None:
    html = render_vool_settings_html(build_commit="t")
    assert 'data-i18n="settings.nav.back"' in html
    assert 'data-i18n-placeholder="settings.nav.search"' in html
    assert "T('settings.nav.head', 'Settings')" in html
    assert "T('setup.checklist.continue', 'Continue setup')" in html
    assert "tfmt('setup.checklist.later_prefix'" in html
    # the transient widget states ride the generic keys, not raw literals
    assert "'Unavailable — ' + err" not in html
    assert "tfmt('settings.widget.unavailable'" in html


def test_chat_greetings_and_toasts_ride_keys() -> None:
    html = render_vool_chat_html(build_commit="t")
    for key, literal in (
        ("first_run.greet_afternoon", "Good afternoon"),
        ("first_run.opener_1", "What are we working on today?"),
        ("chats.project_note", "Chats inside a project work in that folder"),
        ("chat.toast_move_failed", "Could not move this chat"),
        ("chat.toast_project_failed", "Could not put this chat in the project"),
        ("chat.toast_try_once_grant", "for one turn"),
        ("chat.toast_copy_empty", "Nothing to copy in"),
        ("chat.setup_line_progress", "Finish setting up VOOL"),
    ):
        assert key in ENGLISH.keys, key
    assert "emptyT('first_run.greet_afternoon'" in html or "period[0], period[1]" in html
    assert "emptyT('chat.toast_move_failed'" in html
    assert "chats.project_note" in html
    assert "chat.setup_line_progress" in html


def test_shortcut_hint_resolves_through_the_bundle() -> None:
    from core.command_palette_fragment import render_palette_fragment

    rendered = render_palette_fragment()
    assert "vpHintT('palette.hint_palette', 'palette')" in rendered
    assert "vpHintT('palette.hint_esc_stop', 'stop')" in rendered
