"""Settings carries a token-usage panel with Today/Week/Month/All tabs, driven by the real
/api/runtime/usage endpoint (per-model, calendar windows) — no invented figures."""
from __future__ import annotations

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def test_usage_panel_markup_present():
    assert 'id="usageBody"' in HTML
    assert 'id="usageTabs"' in HTML
    for r in ("today", "week", "month", "all"):
        assert 'data-range="' + r + '"' in HTML, r


def test_usage_panel_reads_the_real_endpoint_with_per_model():
    assert "function renderUsage" in HTML
    assert "/api/runtime/usage" in HTML
    assert "per_model=1" in HTML
    assert "range=" in HTML


def test_usage_panel_opens_with_settings():
    assert "renderUsage(usageRange)" in HTML


def test_usage_panel_is_failsoft_and_local_vs_paid():
    assert "Usage is unavailable right now." in HTML
    assert "Local (free) tokens" in HTML
    assert "Cloud (free) tokens" in HTML
    assert "Cloud (paid) tokens" in HTML
