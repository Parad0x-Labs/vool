"""Served: a new agent wallet shows at once, even with an allowance form left open (sls, Nov 13).

Measured on 9c86f47b on the Mac: the owner made the assistant's wallet, pressed "Set an allowance" and saved it
empty (the door refused it and the form stayed open), then made the Skills & plugins wallet with the PIN and
"I have saved my backup". The page said the wallet was ready, but the Skills & plugins box still said it had none:
the section's refresh skips the agent list while any form is open in it, and nothing else read the list again.
Real daemon, real Chromium, the owner's clicks only. No x402 extra and no chain are needed: creating an agent
wallet moves no money and reads no balance.
"""
from __future__ import annotations

import json
import os
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

pytestmark = [pytest.mark.safety, pytest.mark.served]

PIN = "482913"
BASE_SEPOLIA = "eip155:84532"
EMPTY_SAVE = "Fill in the limits and at least one service address first. Nothing was saved."


def _door(base: str, path: str, body: dict) -> tuple[int, dict]:
    request = Request(base + path, data=json.dumps(body).encode(), method="POST", headers={"Origin": base, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _agents(base: str) -> dict:
    with urlopen(base + "/api/wallet/agents", timeout=30) as response:
        return json.load(response)["agent_spending"]


def _owner_wallet(base: str) -> None:
    status, created = _door(base, "/api/wallet/setup/create", {"network": BASE_SEPOLIA, "method": "pin", "credential": PIN, "credential_confirmation": PIN,
                                                               "creation_key": f"owner-{uuid.uuid4().hex}", "label": "Main"})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(base, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200, revealed
    status, ready = _door(base, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": revealed["backup"]["ack_token"]})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready


def _create_agent_wallet(page, agent_id: str) -> None:
    page.select_option(f"#vwAgentNet_{agent_id}", BASE_SEPOLIA)
    page.click(f"#vwAgentCreate_{agent_id}")
    page.wait_for_selector("#vwOwnerProofSecret", state="visible", timeout=20_000)
    page.fill("#vwOwnerProofSecret", PIN)
    page.click("#vwOwnerProofOk")
    page.wait_for_selector("#vwCxBackup", timeout=30_000)
    page.click("#vwCxBackupSaved")
    page.wait_for_selector("#vwCxBackup", state="detached", timeout=20_000)


@pytest.mark.timeout(600)
def test_a_new_skills_wallet_shows_even_with_an_allowance_form_left_open(tmp_path):
    import tests._reader_served_rig as rig
    from tests.served_browser import launch_chromium

    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={
            "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet",
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        })
        daemon.register_provider()
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        try:
            manager, browser = launch_chromium()
        except BaseException:
            daemon.stop()
            raise
        try:
            base = daemon.base_url
            _owner_wallet(base)
            page = browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 1600})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            allowance_posts: list[str] = []
            page.on("request", lambda r: allowance_posts.append(r.method) if r.url.endswith("/api/wallet/agents/allowance") else None)
            page.goto(f"{base}/settings#wallet", wait_until="networkidle")
            page.wait_for_selector("#vwAgentCreate_assistant", state="visible", timeout=30_000)
            # each Create button says whose wallet it makes (two of them sit one above the other)
            assert page.inner_text("#vwAgentCreate_assistant").strip() == "Create a wallet for VOOL assistant"
            assert page.inner_text("#vwAgentCreate_addons").strip() == "Create the Skills & plugins wallet"

            # --- the assistant's wallet: shown at once, and the answer sits under its own button and names it
            _create_agent_wallet(page, "assistant")
            assistant = page.locator('.vw-cx-agent[data-agent="assistant"]')
            assistant.locator("[data-agent-wallet]").wait_for(timeout=20_000)
            page.wait_for_function("() => (document.getElementById('vwAgentMsg_assistant') || {textContent: ''}).textContent.indexOf('Done: VOOL assistant has its own wallet now') === 0", timeout=20_000)
            wallet_id = assistant.locator("[data-agent-wallet]").get_attribute("data-agent-wallet")
            page.wait_for_load_state("networkidle")

            # --- "Set an allowance" saved empty: a plain answer inside the form, nothing sent, the form stays open
            assistant.locator("button", has_text="Set an allowance").click()
            form = assistant.locator("[data-agent-form]")
            form.wait_for(timeout=10_000)
            form.locator("button.primary").click()
            page.wait_for_function("(t) => (document.getElementById('vwAgentMsg_form_' + t[0]) || {textContent: ''}).textContent.trim() === t[1]", arg=[wallet_id, EMPTY_SAVE], timeout=10_000)
            assert allowance_posts == [], allowance_posts
            assert form.count() == 1, "the empty form stays open for the owner to fill in"

            # --- the Skills & plugins wallet with that form still open: the box shows it without a reload
            assert "no wallet yet" in page.inner_text("#vwAddons")
            _create_agent_wallet(page, "addons")
            page.wait_for_function("() => { const x = document.getElementById('vwAddons'); return !!x && !/no wallet yet/.test(x.innerText) && !!x.querySelector('[data-agent-wallet]'); }", timeout=20_000)
            said = page.inner_text("#vwAgentMsg_addons")
            assert said.startswith("Done: Skills & plugins has its own wallet now, shown above."), said
            stored = _agents(base)
            assert [w["network"] for w in stored["addons"]["wallets"]] == [BASE_SEPOLIA], stored["addons"]
            assert [w["network"] for w in stored["agents"][0]["wallets"]] == [BASE_SEPOLIA], stored["agents"]
            assert errors == [], errors
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
