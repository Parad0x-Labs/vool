"""The Settings key panel gains a provider selector + custom base-URL field, client-side provider
detection, and the model dropdown groups by the active provider's label."""
from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def test_provider_selector_and_custom_base_url_present():
    assert 'id="orProvider"' in HTML and "Auto-detect" in HTML
    assert 'id="orBaseUrl"' in HTML


def test_client_side_detect_mirrors_server_prefixes():
    assert "function detectProvider" in HTML
    for prefix in ("sk-or-", "sk-ant-", "sk-proj-", "gsk_", "AIza"):
        assert prefix in HTML, prefix
    assert "loadProviderOptions" in HTML and "/api/cloud/providers" in HTML


def test_save_sends_provider_and_blocks_ambiguous():
    assert "provider: provider" in HTML
    assert "ambiguous" in HTML  # ambiguous key blocks Save until a provider is chosen
    assert "payload.base_url" in HTML  # custom base URL is sent


def test_dropdown_groups_by_active_provider_label():
    assert "FREE · ' + provLabel" in HTML and "PAID · ' + provLabel" in HTML
    assert "FREE · OpenRouter (live)" not in HTML  # no longer hardcoded


def test_id_regex_accepts_bare_ids_but_local_tiers_excluded():
    # The relaxed regex matches bare ids; the key-removal revert must still exclude local tiers.
    assert "!MODEL_LABELS[modelValue] && CLOUD_MODEL_ID_RE.test(modelValue)" in HTML
