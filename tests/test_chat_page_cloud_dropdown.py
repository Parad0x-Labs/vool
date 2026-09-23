"""The live OpenRouter model dropdown: free-first, orderable, price-optional, and every switch
goes through the tested /api/cloud/model endpoint — with the static popover untouched and a
fail-soft path when the catalog cannot be fetched."""
from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def test_dropdown_pulls_the_live_catalog():
    assert "function renderCloudModels" in HTML
    assert "/api/cloud/models?order=" in HTML


def test_free_models_are_pinned_above_paid():
    # Group labels are now provider-aware ("FREE · <provider label>"); free renders before paid.
    assert "FREE · ' + provLabel" in HTML
    assert "PAID · ' + provLabel" in HTML
    assert HTML.index("(m) => m.free)") < HTML.index("(m) => !m.free)")


def test_ordering_is_user_selectable_and_persisted():
    for order in ("featured", "name", "context"):
        assert order in HTML, order
    assert "vool_model_order" in HTML


def test_prices_are_optional_and_persisted():
    assert "vool_model_prices" in HTML
    assert "per 1M" in HTML
    assert "function priceText" in HTML


def test_switch_goes_through_the_guarded_endpoint():
    assert "function switchCloudModel" in HTML
    assert "'/api/cloud/model'" in HTML
    # only a real ok response updates the selection
    assert "if (r.ok && j.ok)" in HTML
    assert "rowEl.dataset.selectionError = message" in HTML
    assert "hint.textContent = message" in HTML  # the server refusal remains visible


def test_static_popover_is_untouched_and_failsoft():
    # Auto is the only static model row; exact free and paid pins come from the live catalog.
    assert 'data-model="vool"' in HTML
    assert 'data-model="openrouter-auto"' not in HTML
    assert 'data-model="openrouter-frontier"' not in HTML
    # dynamic rows are a separate class so the static click handler skips them
    assert ":not(.cloud-dyn)" in HTML
    assert "function clearCloudModels" in HTML
    # paid list is capped
    assert ".slice(0, 30)" in HTML


def test_persisted_cloud_id_survives_reload():
    # a well-formed vendor/name[:tag] value is kept, not reset to 'vool'
    assert "CLOUD_MODEL_ID_RE.test(modelValue)" in HTML


def test_removing_the_key_reverts_a_cloud_model_selection():
    # When the key goes away, a previously-selected cloud model must revert to local Auto so a
    # send never targets an unusable model while the pill reads 'Local only'.
    assert "modelValue = 'vool'" in HTML
    assert "modelValue.indexOf('openrouter') === 0" in HTML


def test_no_banned_language_in_the_touched_region():
    for banned in ("stay honest", "honestly", "brutal", "goblin"):
        assert banned not in HTML, banned


def test_auto_fallback_is_separate_from_explicit_model_pins():
    assert "Auto fallback" in HTML
    assert "/api/cloud/auto-model" in HTML
    assert "Best verified free" in HTML
    assert "used only by VOOL Auto; never paid" in HTML
    assert "free · exact pin" in HTML
    assert "paid · exact pin" in HTML


def test_paid_pin_is_explicit_and_project_memory_does_not_silently_reset_it():
    assert "window.VoolPriceGate.review({ kind: 'pin'" in HTML
    assert "modelSelectionPost(Object.assign({}, pinBody, { confirm_paid: true }))" in HTML
    assert "setModelValue(remembered)" in HTML
    assert "switched to local free" not in HTML
