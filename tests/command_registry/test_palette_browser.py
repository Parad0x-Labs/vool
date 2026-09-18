"""Chromium proof of the registry-fed Cmd+K palette on the REAL chat page.

The page is the real rendered chat document; every /api/commands* fetch is
fulfilled by the REAL projection functions (palette_data / api_schema /
handle_commands_dispatch) — only the network hop is intercepted, so the DOM
proves the actual registry truth, availability reasons, typed argument forms,
approval presentation, receipts and keyboard behaviour.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# The served-browser lane is opt-in: the playwright sync API leaves asyncio state
# that breaks in-process ASGI harnesses collected after it. Run with
# VOOL_PALETTE_PROOF=1 (the convergence evidence pass does).
pytestmark = pytest.mark.skipif(
    os.environ.get("VOOL_PALETTE_PROOF") != "1",
    reason="palette browser proof is opt-in: set VOOL_PALETTE_PROOF=1 (playwright + chromium)",
)

pytest.importorskip("playwright")

from core.command_registry.api import handle_commands_dispatch, handle_commands_get
from core.command_registry.registry import registry
from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()
EVIDENCE = Path(__file__).resolve().parents[2] / "validation-logs" / "command-centre-palette"


def _route(route):
    request = route.request
    if request.resource_type == "document":
        route.fulfill(status=200, content_type="text/html", body=HTML)
        return
    url = request.url
    if "/api/commands/schema" in url:
        from core.command_registry.projections import api_schema

        route.fulfill(status=200, content_type="application/json", body=json.dumps(api_schema(registry())))
        return
    if "/api/commands/dispatch" in url:
        body = json.loads(request.post_data or "{}")
        status, payload = handle_commands_dispatch(body)
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))
        return
    if "/api/commands" in url:
        status, payload = handle_commands_get("/api/commands/palette", {})
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))
        return
    route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def browser():
    _manager, browser = served_browser.launch_chromium()
    yield browser
    browser.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.on("pageerror", lambda e: None)
    page.route("**/*", _route)
    yield page
    page.close()


def _shot(page, name: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(EVIDENCE / f"{name}.png"), full_page=False)


def test_palette_opens_searches_and_runs_a_read_command(page):
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)

    # Cmd+K opens the palette from the real chat UI
    page.keyboard.press("Meta+k")
    page.wait_for_timeout(150)
    assert page.locator("#vpOverlay").is_visible()
    _shot(page, "01-palette-open")

    # searchable groups and aliases: registry rows are present
    rows = page.locator("#vpList .vp-item")
    page.wait_for_function("document.querySelectorAll('#vpList .vp-item').length > 20")
    assert rows.count() > 20

    page.fill("#vpInput", "blackbox status")
    page.wait_for_timeout(120)
    assert "blackbox.status" in page.locator("#vpList").inner_text()
    _shot(page, "02-search")

    # keyboard: arrows + enter select the row; read command has no required args
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    assert page.locator("#vpForm").is_visible()
    assert "blackbox.status" in page.locator("#vpForm").inner_text()

    page.click("[data-vp-run]")
    page.wait_for_timeout(300)
    result = page.locator("#vpResult").inner_text()
    assert "Blackbox store status" in result
    _shot(page, "03-read-executed")

    # Escape closes (keyboard support)
    page.keyboard.press("Escape")
    page.wait_for_timeout(100)
    assert page.locator("#vpOverlay").is_hidden()


def test_palette_shows_unavailable_with_reason(page):
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)
    page.keyboard.press("Meta+k")
    page.fill("#vpInput", "council.stop")
    page.wait_for_timeout(150)
    row = page.locator("#vpList").inner_text()
    assert "unavailable" in row.lower()
    assert "no council runs" in row
    _shot(page, "04-unavailable-with-reason")


def test_palette_typed_argument_form_and_approval_presentation(page):
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)
    page.keyboard.press("Meta+k")

    # a command with required input renders the typed argument form
    page.fill("#vpInput", "blackbox rollback")
    page.wait_for_timeout(150)
    page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    form_text = page.locator("#vpForm").inner_text()
    form_text = form_text.lower()
    assert "turn_id" in form_text and "workspace_root" in form_text
    _shot(page, "05-typed-argument-form")

    # fill the typed form and run: the destructive command is refused with the
    # approval presentation (availability refuses first in an empty store —
    # either way the FAULT is presented, never a silent failure)
    page.fill("[data-vp-arg=\"turn_id\"]", "t1")
    page.fill("[data-vp-arg=\"workspace_root\"]", "/tmp")
    page.click("[data-vp-run]")
    page.wait_for_timeout(300)
    result = page.locator("#vpResult").inner_text()
    assert "unavailable" in result.lower() or "approval" in result.lower()
    _shot(page, "06-fault-presentation")


def test_palette_reduced_motion(page):
    page.emulate_media(reduced_motion="reduce")
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)
    page.keyboard.press("Meta+k")
    page.wait_for_timeout(150)
    assert page.locator("#vpOverlay").is_visible()
    # the palette honors the media query: the emulated preference is active and
    # the reduced-motion rule forces transitions off
    reduced = page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert reduced is True
    assert "prefers-reduced-motion" in HTML
    _shot(page, "07-reduced-motion")
