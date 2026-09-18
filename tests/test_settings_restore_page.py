"""Source pins for the restored Settings surfaces (the served proofs live in test_settings_restore_served.py).

These catch drift a served drive would only report indirectly: a constant the page repeats from the
runtime, and a route the page links to that must keep existing.
"""
from __future__ import annotations

import re

from core.vool_settings_page import render_vool_settings_html, settings_groups


def test_the_page_names_the_same_base_url_slot_as_the_provider_table() -> None:
    """The keys list treats the custom endpoint's base URL as an address, not a key, by slot name. The
    name is the runtime's (core/cloud_providers.py); if it moves there, this page must move with it."""
    from core.cloud_providers import CUSTOM_BASE_URL_SLOT

    html = render_vool_settings_html()
    match = re.search(r"const CUSTOM_BASE_URL_SLOT = '([^']+)';", html)
    assert match, "the page no longer declares the base-URL slot"
    assert match.group(1) == CUSTOM_BASE_URL_SLOT


def test_every_advanced_link_is_a_route_this_runtime_serves() -> None:
    """A link in Settings -> Advanced must resolve on the runtime that serves Settings."""
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    html = render_vool_settings_html()
    hrefs = re.findall(r"\{ href: '(/[a-z0-9/-]+)', label:", html)
    assert set(hrefs) == {"/trace", "/web0"}, hrefs
    for href in hrefs:
        res = dispatch_get(path=href, query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool", client_host="127.0.0.1")
        assert res.status == 200, (href, res.status)
    assert any(g["id"] == "advanced" for g in settings_groups())


def test_memory_entries_route_lists_governed_rows_for_a_caller_with_no_chat(tmp_path, monkeypatch) -> None:
    """Settings has no chat. The route reads every active namespace under its own policy and merges the
    rows; before the repair a bare read raised inside the route and every caller got an empty list."""
    import json

    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    try:
        from core.context_namespace import ensure_chat_namespace
        from core.memory.entries import add_memory_fact
        from core.web.api.runtime import RuntimeServices
        from core.web.api.service import dispatch_get

        chat = "openclaw:" + "c" * 20
        ensure_chat_namespace(chat)
        assert add_memory_fact("the rehearsal is on Thursday", session_id=chat, scope="chat")
        runtime = RuntimeServices(display_name="VOOL")
        res = dispatch_get(path="/api/memory/entries", query={"limit": ["50"]}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
        body = json.loads(res.body)
        assert res.status == 200 and [e["fact"] for e in body["entries"]] == ["the rehearsal is on Thursday"], body
        # The same row through the chat's own handle.
        res2 = dispatch_get(path="/api/memory/entries", query={"session": [chat]}, runtime=runtime, model_name="vool", client_host="127.0.0.1")
        assert [e["record_id"] for e in json.loads(res2.body)["entries"]] == [body["entries"][0]["record_id"]]
    finally:
        runtime_paths.configure_runtime_home(None)
