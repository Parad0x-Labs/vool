"""The UsePod Settings panel, driven as a person drives it: a real daemon, a real browser.

The daemon is ``apps.vool_api_server`` in its own process and home, with NO monetary test double
installed, so the panel's dependency lines state the honest production facts (no monetary
authority, no wallet authority). The provider origin is the SYNTHETIC strict local service; the
token is generated per run. Everything asserted here is what the rendered DOM shows — this is
BROWSER-level evidence against the served page, not native-window evidence; no claim is made
about the packaged desktop host.

The tests run in file order against ONE shared daemon+browser session, mirroring one person's
session: unconfigured states first, then the token is saved through the real form, then discovery,
approval and the secret checks operate on that same configured daemon. Running a single test in
isolation is valid only for the first test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

from tests.served_browser import launch_chromium
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_served_flow import MODEL, UsePodServedDaemon, _free_port

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKET_ID = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
MARKET = (510_000, 1_530_000)
CENTRAL = ("groq", 700_000, 2_100_000)


class PlainServedDaemon(UsePodServedDaemon):
    """The same served daemon WITHOUT the monetary test double: the authorities the panel reports
    are the production ones (unavailable), which is what the dependency lines must say."""

    def start(self, timeout: float = 240.0) -> PlainServedDaemon:
        self.home.mkdir(parents=True, exist_ok=True)
        env = self.env()
        env.pop("USEPOD_SERVED_DOUBLE_JOURNAL", None)
        handle = self.log_path.open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"daemon exited {self.process.returncode}\n{self.log_tail()}")
            try:
                with urlopen(f"{self.base_url}/healthz", timeout=3) as response:
                    if response.status == 200:
                        return self
            except (URLError, HTTPError, OSError):
                time.sleep(1.0)
        raise TimeoutError(f"daemon never became healthy\n{self.log_tail()}")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    daemon = PlainServedDaemon(tmp_path_factory.mktemp("usepod-settings-ui") / "home")
    manager = None
    browser = None
    try:
        daemon.start()
        manager, browser = launch_chromium()
        yield daemon, browser, service, token
    finally:
        if browser is not None:
            browser.close()
        if manager is not None:
            manager.stop()
        daemon.stop()
        service.stop()


def _open_panel(browser, daemon):
    page = browser.new_page()
    page.goto(f"{daemon.base_url}/settings#models", wait_until="networkidle")
    page.wait_for_selector(".usepod-model-search", timeout=20000)
    # go() paints the pane twice (shell first, sources after), so the widget draws twice and the
    # first draw's DOM is replaced; let the second draw settle before driving any control.
    page.wait_for_timeout(600)
    return page


def test_the_panel_opens_with_honest_unconfigured_states(served) -> None:
    daemon, browser, _service, _token = served
    page = _open_panel(browser, daemon)
    try:
        text = page.inner_text("body")
        # No token stored: the panel says how to store one rather than pretending a lane exists.
        assert "No UsePod token stored" in text
        # The money law is installed (the merged authority); the honest facts are that NO spend
        # grant exists yet and that refusal is what a paid turn gets. The wallet authority is
        # still named as missing with its Settings door.
        assert "Monetary authority" in text and "effect_budget_money:v1" in text
        assert "NO active spend grant" in text and "MONEY_AUTHORITY_INVALID" in text
        # The wallet authority is installed at boot; with Crypto off it pays on no network and says so, with its door.
        assert "Wallet payment authority" in text and "core.wallet.usepod_x402:v1" in text and "wallet_payment_network_unverified" in text
        assert "Settings → Wallet" in text
        assert "Money state" in text
        # No price has been fetched: absent is named, never zero.
        assert "Never fetched here" in text and "price_feed_unavailable" in text
        # The lane controls exist and default to the stored lane.
        assert page.inner_text("select.usepod-protocol option:checked") .strip().startswith("OpenAI-compatible")
        # Cache-only: opening the panel fetched the public feed ZERO times.
        assert _service.requests_to("/v1/marketplace/models") == []
    finally:
        page.close()


def test_a_token_saved_through_the_real_form_binds_and_shows_facts_not_secrets(served) -> None:
    daemon, browser, service, token = served
    # The paste form lives in the API-keys group; the UsePod panel in Models & Providers.
    page = browser.new_page()
    page.goto(f"{daemon.base_url}/settings#keys", wait_until="networkidle")
    page.wait_for_selector("select[aria-label='Provider']", timeout=20000)
    page.wait_for_timeout(600)
    try:
        # The provider selector offers UsePod from the server's own table.
        page.select_option("select[aria-label='Provider']", "usepod")
        # The paste field relabels for a path-token provider and the origin field appears.
        assert page.get_attribute("input.key-origin", "hidden") is None or page.is_visible("input.key-origin")
        page.fill("input[aria-label='UsePod token or proxy URL']", token)
        # The origin is chosen EXPLICITLY: the strict local service stands in for UsePod, and the
        # token never leaves this machine's loopback. A paste alone must never re-point the token.
        page.fill("input[aria-label='Provider origin']", service.origin)
        page.click("button.key-save")
        page.wait_for_function(
            "() => /Stored, sealed on this machine/.test((document.querySelector('.key-save-state') || {textContent:''}).textContent || '')",
            timeout=20000,
        )
        note = page.inner_text(".key-save-state")
        # The confirmation states the binding facts the runtime holds — the CHOSEN origin and the
        # one-way fingerprint — and never the token.
        assert service.origin in note and "fingerprint upc_" in note
        assert token not in note
        # Save verified the token before storing it with ONE balance read: no inference, no spend.
        assert len(service.requests_to("/proxy/{token}/balance")) == 1
        assert service.requests_to("/proxy/{token}/v1/chat/completions") == []
    finally:
        page.close()
    page = _open_panel(browser, daemon)
    try:
        # The panel re-renders with the credential facts.
        page.wait_for_selector(".usepod-test", timeout=20000)
        text = page.inner_text("body")
        assert "Fingerprint" in text and "upc_" in text
        assert "(you chose this origin)" in text and service.origin in text
        assert token not in text
    finally:
        page.close()


def test_refresh_shows_prices_with_units_and_the_balance_observation(served) -> None:
    daemon, browser, _service, _token = served
    _status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    if not (view.get("credential") or {}).get("configured"):
        pytest.skip("requires the credential saved by the earlier test in this file's order")
    page = _open_panel(browser, daemon)
    try:
        balance_reads = len(_service.requests_to("/proxy/{token}/balance"))
        page.click("button.usepod-refresh")
        page.wait_for_selector(".usepod-model", timeout=30000)
        text = page.inner_text("body")
        # Human-readable prices preserve the integer feed's exact decimal value.
        assert "0.510000 input / 1.530000 output USDC per 1M tokens" in text
        assert "80.000000 USDC (80000000 µUSDC)" in text and "observed" in text
        assert "listed for this token" in text
        # Provenance: the feed's source and age, and the row count.
        assert "/v1/marketplace/models" in text and "of a" in text and "s TTL" in text
        # The refresh reached the feed exactly once for the marketplace, and the balance once.
        assert len(_service.requests_to("/v1/marketplace/models")) == 1
        assert len(_service.requests_to("/proxy/{token}/balance")) == balance_reads + 1
        # No inference path was touched by viewing or refreshing.
        assert _service.requests_to("/proxy/{token}/v1/chat/completions") == []
    finally:
        page.close()


def test_route_approval_and_withdrawal_through_the_panel(served) -> None:
    daemon, browser, _service, _token = served
    page = _open_panel(browser, daemon)
    try:
        page.wait_for_selector("button.usepod-route-approve", timeout=20000)
        page.once("dialog", lambda dialog: dialog.accept())
        page.click("button.usepod-route-approve")
        page.wait_for_selector("button.usepod-route-forget", timeout=30000)
        text = page.inner_text("body")
        # What is approved is ON the row: ceilings, bases, route classes, approval id, snapshot.
        assert f"in ≤ {MARKET[0]}" in text and f"out ≤ {MARKET[1]}" in text
        assert "marketplace" in text and "approval" in text.lower()
        # Withdrawal works immediately through the same authority.
        page.click("button.usepod-route-forget")
        page.wait_for_selector("button.usepod-route-approve", timeout=30000)
        status, model_pin = daemon.call("GET", "/api/cloud/usepod/discovery")
        assert model_pin["approved_routes"] == {} or MODEL not in model_pin["approved_routes"]
    finally:
        page.close()


def test_selecting_x402_without_a_wallet_network_warns_in_the_panel(served) -> None:
    daemon, browser, _service, _token = served
    page = _open_panel(browser, daemon)
    try:
        page.select_option("select.usepod-transport", "x402")
        # The wallet authority is installed at boot; Crypto is off in this home, so it pays on no network.
        page.wait_for_function(
            "() => /x402 selected, but the wallet pays on no network now/.test(document.body.innerText)",
            timeout=10000,
        )
        text = page.inner_text("body")
        assert "wallet_payment_network_unverified" in text and "before any reservation, quote or payment" in text and "Settings → Wallet" in text
        # The lane itself still saves: the lane is a preference, the refusal is a dependency fact.
        page.click("button.usepod-lane-save")
        page.wait_for_function(
            "() => /Lane saved/.test((document.querySelector('.usepod-note') || {textContent:''}).textContent || '')",
            timeout=20000,
        )
        status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
        assert view["lane"]["transport_mode"] == "x402"
        # Put it back so later tests see the default lane.
        page.select_option("select.usepod-transport", "prepaid_token")
        page.click("button.usepod-lane-save")
        page.wait_for_function(
            "() => /no spend grant exists yet|MONEY_AUTHORITY_INVALID/.test(document.body.innerText)",
            timeout=20000,
        )
    finally:
        page.close()


def test_no_secret_persists_in_dom_storage_logs_or_urls(served) -> None:
    daemon, browser, _service, token = served
    page = _open_panel(browser, daemon)
    try:
        page.wait_for_selector(".usepod-model", timeout=30000)
        # DOM text.
        assert token not in page.inner_text("body")
        # Browser storage: nothing the page wrote carries the token.
        storage = page.evaluate(
            "() => JSON.stringify({local: Object.entries(localStorage), session: Object.entries(sessionStorage)})"
        )
        assert token not in storage
        # The daemon's own log.
        assert token not in daemon.log_path.read_text("utf-8", "replace")
        # And the page's URL never grew a query carrying it.
        assert token not in page.url
    finally:
        page.close()


def test_every_usepod_control_is_reachable_and_labelled_for_the_keyboard(served) -> None:
    """Keyboard/focus: every control in the panel is a native focusable element carrying an
    accessible name, and the primary actions fire from the keyboard alone."""
    daemon, browser, _service, _token = served
    _status, _view = daemon.call("GET", "/api/cloud/usepod/discovery")
    if not (_view.get("credential") or {}).get("configured"):
        pytest.skip("requires the credential saved by the earlier test in this file's order")
    page = _open_panel(browser, daemon)
    try:
        # Self-sufficient once the credential is bound: refresh prices if this run's order has not.
        try:
            page.wait_for_selector(".usepod-model", timeout=4000)
        except Exception:
            page.click("button.usepod-refresh")
            page.wait_for_selector(".usepod-model", timeout=30000)
        unnamed = page.evaluate(
            """() => {
              // The panel's own controls: native focusable elements whose class or ancestor
              // marks them as the UsePod panel's.
              const scope = Array.from(document.querySelectorAll('button.usepod-test, button.usepod-refresh, button.usepod-lane-save, button.usepod-policy-save, button.usepod-route-approve, button.usepod-route-forget, select.usepod-protocol, select.usepod-transport, input.usepod-policy-pins, input.usepod-policy-ceiling-in, input.usepod-policy-ceiling-out, input.usepod-policy-fallback, input.usepod-model-search'));
              const bad = [];
              for (const el of scope) {
                // The accessible-name model the browser uses: aria-label, then a linked
                // <label for>, then text/placeholder -- an element named by its label is named.
                let name = (el.getAttribute('aria-label') || '').trim();
                if (!name && el.id) {
                  const lab = document.querySelector('label[for="' + el.id + '"]');
                  if (lab) name = (lab.textContent || '').trim();
                }
                if (!name) name = (el.textContent || el.placeholder || '').trim();
                if (!name) bad.push(el.tagName + '.' + el.className);
              }
              return {count: scope.length, bad};
            }"""
        )
        assert unnamed["count"] >= 12, unnamed   # test, refresh, protocol, transport, lane save, policy mode/pins/ceilings/fallback, search, approve/withdraw
        assert unnamed["bad"] == [], unnamed
        # Keyboard driving: Tab reaches the search field and typing filters the model list.
        panel_search = page.query_selector(".usepod-model-search")
        assert panel_search is not None
        panel_search.focus()
        page.keyboard.type("meridian")
        page.wait_for_timeout(200)
        assert page.evaluate("() => document.activeElement && document.activeElement.className.includes('usepod-model-search')")
        visible_models = page.evaluate("() => Array.from(document.querySelectorAll('.usepod-model .profile-value')).map(e => e.textContent)")
        assert visible_models and all("meridian" in v.lower() for v in visible_models), visible_models
        # No match is a named empty state, not a blank panel.
        panel_search.fill("zzz-not-a-model")
        page.wait_for_timeout(200)
        assert page.evaluate("() => document.body.innerText.includes('No model id matches')")
    finally:
        page.close()


def test_approval_withdrawal_fires_from_the_keyboard(served) -> None:
    daemon, browser, _service, _token = served
    _status, _view = daemon.call("GET", "/api/cloud/usepod/discovery")
    if not (_view.get("credential") or {}).get("configured"):
        pytest.skip("requires the credential saved by the earlier test in this file's order")
    page = _open_panel(browser, daemon)
    try:
        page.wait_for_selector("button.usepod-route-approve", timeout=30000)
        page.once("dialog", lambda d: d.accept())
        page.click("button.usepod-route-approve")
        page.wait_for_selector("button.usepod-route-forget", timeout=30000)
        forget = page.query_selector("button.usepod-route-forget")
        assert forget is not None
        forget.focus()
        assert page.evaluate("() => document.activeElement === document.querySelector('button.usepod-route-forget')")
        page.keyboard.press("Enter")
        page.wait_for_selector("button.usepod-route-approve", timeout=30000)
        status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
        assert MODEL not in (view.get("approved_routes") or {})
    finally:
        page.close()


def test_an_unreachable_discovery_read_renders_a_named_error_state(served) -> None:
    daemon, browser, _service, _token = served
    page = browser.new_page()
    # The cache-only read itself failing is the error branch: intercept it in the browser.
    page.route("**/api/cloud/usepod/discovery", lambda route: route.abort())
    try:
        page.goto(f"{daemon.base_url}/settings#models", wait_until="networkidle")
        page.wait_for_function(
            "() => /Unavailable —/.test(document.body.innerText)",
            timeout=20000,
        )
        text = page.inner_text("body")
        assert "Reading the UsePod caches" not in text   # not stuck loading either
    finally:
        page.close()


def test_evidence_screenshots_of_the_panel_states(served) -> None:
    """Browser-level visual evidence for the delivery (NOT native-window evidence). Only runs
    with the credential bound by the earlier test in this file's order: an unconfigured daemon
    would refresh against the REAL public origin, which this suite must not do."""
    daemon, browser, _service, _token = served
    out = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR")
    if not out:
        pytest.skip("no evidence directory requested")
    _status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    if not (view.get("credential") or {}).get("configured"):
        pytest.skip("requires the credential saved by the earlier test in this file's order")
    page = _open_panel(browser, daemon)
    try:
        page.wait_for_selector(".usepod-model", timeout=30000)
        page.screenshot(path=str(Path(out) / "settings_usepod_panel_configured.png"), full_page=True)
        page.click("button.usepod-refresh")
        page.wait_for_selector(".usepod-model", timeout=30000)
        page.screenshot(path=str(Path(out) / "settings_usepod_panel_after_refresh.png"), full_page=True)
    finally:
        page.close()
