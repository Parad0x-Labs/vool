"""SERVED: what the Wallet panel's Activity and send form show is exact, current and in the chosen language.

Real daemon, the confined browser, a disposable Crypto Pilot wallet on a simulated Solana Devnet node.
A request cancelled before any claim leaves a refusal receipt; its label and detail read in the app
language the page chose (the ``vool_ui_locale`` cookie the language picker persists), on the raw wallet
API and in the rendered Activity, and in English when no language was chosen. Activity shows exact
amounts and real times, a cancel shows at once, and a wrong-network contact warning never outlives the
address it was about. Nothing is signed or sent.
"""
from __future__ import annotations

import json
import re
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import served_browser
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_provider import PromptRoutedProvider, seed_daemon

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
# the shipped catalogs' own words for the receipt (core/i18n/catalogs/<locale>.json)
WORDS = {
    "en": ("Cancelled", "Nothing was signed or sent."),
    "lt": ("Atšaukta", "Niekas nebuvo pasirašyta ar išsiųsta."),
    "de": ("Abgebrochen", "Es wurde nichts signiert oder gesendet."),
}


def _door(daemon, path: str, body: dict) -> tuple[int, dict]:
    request = Request(daemon.base_url + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _transfers(daemon, locale: str = "") -> list[dict]:
    headers = {"Cookie": f"vool_ui_locale={locale}"} if locale else {}
    with urlopen(Request(daemon.base_url + "/api/wallet/transfers", headers=headers), timeout=30) as response:
        return json.load(response)["transfers"]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("receiptlocale") / "home"
    sol = ScriptedRpc(genesis_hash=DEVNET_GENESIS)
    provider = PromptRoutedProvider()
    for node in (sol, provider):
        node.__enter__()
    daemon = ServedDaemon(home, env_extra={
        "VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_RPC_URLS": json.dumps({SOLANA_DEVNET: sol.url}),
        "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1", "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
        "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat", "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps", "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
    })
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        status, switched = _door(daemon, "/api/wallet/environment", {"environment": "testnet"})
        assert status == 200, switched
        status, created = _door(daemon, "/api/wallet/setup/create", {"network": SOLANA_DEVNET, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"locale-{uuid.uuid4().hex}", "label": "locale"})
        assert status == 200, created
        wallet_id = created["setup"]["wallet_id"]
        status, revealed = _door(daemon, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
        token = revealed["backup"]["ack_token"]
        revealed = None
        status, ready = _door(daemon, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": token})
        assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
        yield daemon, sol, wallet_id
    finally:
        daemon.stop()
        for node in (provider, sol):
            node.__exit__(None, None, None)


def test_a_cancelled_request_reads_in_the_chosen_language_on_the_api_and_in_activity(served):
    from core.vool_wallet import b58encode

    daemon, sol, wallet_id = served
    status, prepared = _door(daemon, "/api/wallet/propose", {"wallet_id": wallet_id, "network": SOLANA_DEVNET, "destination": b58encode(bytes([71]) * 32),
                                                            "amount_minor": 400_000, "asset": "SOL", "origin": "user", "idempotency_key": f"locale-{uuid.uuid4().hex}"})
    assert status == 200 and prepared["proposal"]["state"] == "pending_approval", prepared
    proposal_id = prepared["proposal"]["proposal_id"]
    status, rejected = _door(daemon, "/api/wallet/reject", {"proposal_id": proposal_id})
    assert status == 200 and rejected.get("cancelled") is True, rejected

    # the raw wallet API: the request's own language, English when none was chosen, never a bare key
    for locale, (label, detail) in WORDS.items():
        row = next(t for t in _transfers(daemon, "" if locale == "en" else locale) if t["proposal_id"] == proposal_id)
        assert (row["record_kind"], row["state"]) == ("proposal_receipt", "cancelled"), row
        assert (row["state_label"], row["detail"]) == (label, detail), (locale, row)
    # a language VOOL does not ship falls back to English rather than guessing
    row = next(t for t in _transfers(daemon, "xx-unknown") if t["proposal_id"] == proposal_id)
    assert (row["state_label"], row["detail"]) == WORDS["en"]

    # the rendered Activity in the confined browser, with the cookie the language picker writes
    manager, browser = served_browser.launch_chromium()
    try:
        for locale in ("lt", "de"):
            label, detail = WORDS[locale]
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies([{"name": "vool_ui_locale", "value": locale, "url": daemon.base_url}])
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error, sink=errors: sink.append(str(error)))
            try:
                page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
                answer = page.evaluate("fetch('/api/wallet/transfers').then(r => r.json())")
                row = next(t for t in answer["transfers"] if t["proposal_id"] == proposal_id)
                assert (row["state_label"], row["detail"]) == (label, detail), (locale, row)
                if page.locator("#homeMenu").get_attribute("open") is None:
                    page.locator("#homeToggle").click()
                page.locator("#vwHomeBtn").click()
                activity_row = page.locator(f'#vwHomeBody .vw-wh-row[data-pid="{proposal_id}"]')
                activity_row.wait_for(timeout=20_000)
                assert activity_row.locator(".vw-wh-state").text_content() == label
                activity_row.locator("summary").click()
                assert detail in activity_row.text_content()
                assert not errors, errors
            finally:
                context.close()
    finally:
        browser.close()
        manager.stop()
    assert sol.send_count() == 0


def test_activity_shows_exact_amounts_real_times_a_cancel_at_once_and_only_current_hints(served):
    from core.vool_wallet import b58encode

    daemon, sol, wallet_id = served
    status, saved = _door(daemon, "/api/contacts/create", {"display_name": "Wren", "endpoints": [{"kind": "wallet", "value": "0x" + "4" * 40, "network": "eip155:84532"}]})
    assert status == 200, saved
    status, prepared = _door(daemon, "/api/wallet/propose", {"wallet_id": wallet_id, "network": SOLANA_DEVNET, "destination": b58encode(bytes([72]) * 32),
                                                            "amount_minor": 400_000, "asset": "SOL", "origin": "user", "idempotency_key": f"activity-{uuid.uuid4().hex}"})
    assert status == 200 and prepared["proposal"]["state"] == "pending_approval", prepared
    waiting = prepared["proposal"]["proposal_id"]
    manager, browser = served_browser.launch_chromium()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(daemon.base_url) else route.abort())
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        # a controlled page clock: the 2.5 s status poll cannot repaint behind an assertion
        page.clock.install()
        page.goto(daemon.base_url + "/chat", wait_until="domcontentloaded")
        page.clock.run_for(3_000)
        # from here time stands still: no timer (the status poll included) fires unless this test advances it
        page.clock.pause_at(page.evaluate("Date.now()") + 1_000)
        if page.locator("#homeMenu").get_attribute("open") is None:
            page.locator("#homeToggle").click()
        page.locator("#vwHomeBtn").click()
        pending_row = page.locator(f'#vwHomeBody .vw-wh-row[data-pid="{waiting}"]')
        pending_row.wait_for(timeout=20_000)
        assert pending_row.locator(".vw-wh-amt").text_content() == "0.0004 SOL"
        when = pending_row.locator(".vw-wh-when").text_content()
        assert "Invalid" not in when and re.search(r"\d{4}", when), when

        # the send form: a contact saved for another network warns; typing an address drops that warning
        page.locator("#vwHomeSend").click()
        page.locator("#vwHomePickContact").click()
        page.locator("#vwHomeContactList button", has_text="Wren").click()
        assert "not Solana" in page.locator("#vwHomeBody").inner_text()
        page.locator("#vwHomeSendTo").fill(b58encode(bytes([73]) * 32))
        assert "not Solana" not in page.locator("#vwHomeBody").inner_text()
        assert page.locator("#vwHomeBody").get_by_text("saved for").count() == 0

        # a request cancelled on its review sheet (opened from Activity) shows as cancelled at once, not at the next poll
        page.locator("#vwHomeBody .vw-toggle", has_text="Back to the wallet").click()
        page.locator(f'#vwHomeActivity .vw-wh-row[data-pid="{waiting}"] .vw-wh-review').click()
        page.wait_for_selector("#vwSheet")
        page.wait_for_function("!!document.getElementById('vwSheetCancel') && !document.getElementById('vwSheetCancel').disabled")
        with page.expect_response(lambda r: r.url.endswith("/api/wallet/reject")):
            page.locator("#vwSheetCancel").click()
        page.wait_for_selector("#vwSheet", state="detached")
        page.wait_for_function(f"document.querySelector('#vwHomeActivity .vw-wh-row[data-pid=\"{waiting}\"] .vw-wh-state')?.textContent === 'Cancelled'", timeout=5_000)
        assert page.locator(f'#vwHomeActivity .vw-wh-row[data-pid="{waiting}"] .vw-wh-review').count() == 0
        assert not errors, errors
    finally:
        context.close()
        browser.close()
        manager.stop()
    assert sol.send_count() == 0
