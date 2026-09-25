"""PRODUCT-SERVED proofs in a real browser against the real daemon: the one-time recovery display,
the chat flow (user asks -> model proposes -> visible approval card -> PIN -> receipt), and the
Phantom-compatible external signing path through the injected provider surface.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests import served_browser
from tests.wallet._rig import DESTINATION, ExtensionSigner, ScriptedRpc, fake_phantom_init_script, phrase_leaked

pytestmark = [pytest.mark.safety, pytest.mark.served]
PIN = "246810"


@pytest.fixture(scope="module")
def browser():
    ctx, browser = served_browser.launch_chromium()
    try:
        yield browser
    finally:
        browser.close()
        ctx.stop()


@pytest.fixture
def daemon(tmp_path: Path):
    from tests._blackbox_served_rig import ServedDaemon
    from tests.wallet._rig_provider import MODEL, PromptRoutedProvider, seed_daemon

    home = tmp_path / "home"
    store_dir = tmp_path / "blackbox-store"
    provider = PromptRoutedProvider()
    rpc = ScriptedRpc()
    served = ServedDaemon(home, env_extra={"VOOL_ALWAYS_ON_CATALOG": "1", "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "VOOL_WALLET_TESTNET_RPC_URL": rpc.url, "VOOL_BLACKBOX_DIR": str(store_dir), "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url, "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        # The scripted provider loads no weights; the load-gate's headroom floor is for
        # real model loads, and a busy machine must not gate the stub out (same pattern
        # as tests/test_vool_database_served_proof.py).
        "VOOL_MODEL_LOAD_FLOOR_GB": "0.1"})
    provider.__enter__()
    rpc.__enter__()
    try:
        try:
            served.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment
            pytest.skip(f"served daemon could not boot here: {exc}")
        seed_daemon(home, provider.base_url)
        provider.reset()
        yield {"base": f"http://127.0.0.1:{served.port}", "home": home, "store": store_dir, "daemon": served, "provider": provider, "rpc": rpc, "model": MODEL}
    finally:
        served.stop()
        rpc.__exit__(None, None, None)
        provider.__exit__(None, None, None)


REPLY_SELECTOR = ".msg.assistant:not(.pending):not(.vw-card)"


def _wait_replies(page, count: int, *, daemon: dict[str, Any] | None = None, timeout_s: float = 180.0) -> None:
    """Wait for `count` real assistant replies (approval cards are not replies). On timeout, dump what the page
    and the stand-in model saw so a failure carries its own evidence."""
    import time as _time

    deadline = _time.time() + timeout_s
    while _time.time() < deadline:
        if page.evaluate(f"() => document.querySelectorAll('{REPLY_SELECTOR}').length") >= count:
            return
        page.wait_for_timeout(500)
    body = page.evaluate("() => document.body.innerText")
    msgs = page.evaluate("() => Array.from(document.querySelectorAll('.msg')).map(e => e.className + ' :: ' + (e.innerText || '').slice(0, 120))")
    calls = [(c.get("path"), len(c.get("tools") or []), str(c.get("last_user") or "")[:60]) for c in (daemon or {}).get("provider").calls] if daemon else []
    log_tail = daemon["daemon"].log_tail(lines=40) if daemon else ""
    diagnostics = f"no reply #{count} within {timeout_s}s | msgs={msgs} | provider={calls} | body_len={len(body)} | body_tail={body[-400:]!r} | log_tail={log_tail[-1500:]!r}"
    print("WALLET-BROWSER-DIAG:", diagnostics)
    raise AssertionError(diagnostics)


def _page(browser, base: str, *, init_script: str = ""):
    page = browser.new_page()
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: console.append(m.text))
    if init_script:
        page.add_init_script(init_script)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{base}/chat", wait_until="networkidle")
    page.wait_for_timeout(300)
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    return page, errors, console


def _open_wallet_settings(page, base: str) -> None:
    """Integrated UI (2026-09-07): Settings is its own page; the Wallet section is /settings#wallet."""
    page.goto(f"{base}/settings#wallet", wait_until="networkidle")
    page.wait_for_selector("#vwStatus", state="visible", timeout=20000)


def _back_to_chat(page, base: str) -> None:
    page.goto(f"{base}/chat", wait_until="networkidle")
    page.wait_for_selector("#input", timeout=30000)


def _all_db_text(home: Path) -> str:
    """Read the daemon's database WITHOUT touching its locks: an immutable read-only connection. Reading the live
    file with an ordinary connection mid-run broke the daemon's own handle (sqlite disk I/O error, measured
    2026-09-03), so every scan of it happens at the end of a proof, through this door."""
    db = sqlite3.connect(f"file:{home / 'data' / 'vool_web0_v2.db'}?mode=ro&immutable=1", uri=True)
    try:
        chunks = []
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            chunks.append(json.dumps(db.execute(f"SELECT * FROM {name}").fetchall(), default=str))
        return "\n".join(chunks)
    finally:
        db.close()


def _select_model(page, model: str) -> None:
    """Pin the stub model and put the displayed chat in auto mode through the page's own /api/mode door
    (a Manual session parks tool work as pending approval and never offers tools -- measured on the
    blackbox served proof)."""
    page.evaluate("(m) => { modelValue = m; if (typeof reflectModel === 'function') reflectModel(); }", model)
    page.evaluate(
        "async () => { const r = await fetch('/api/mode', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ session_id: displayedChat, mode: 'auto' }) }); return r.status; }"
    )
    page.evaluate("() => { try { const o = chatState(displayedChat); if (o) o.mode = 'auto'; } catch (e) {} }")


def test_browser_one_time_recovery_display_leaves_no_trace(browser, daemon):
    from storage.blackbox.journal import Journal

    page, errors, console = _page(browser, daemon["base"])
    _open_wallet_settings(page, daemon["base"])
    page.wait_for_selector("#vwCreateBtn", timeout=15000)
    page.click("#vwCreateBtn")
    page.wait_for_selector("#vwCreateForm:not([hidden])")
    warning = page.inner_text("#vwWarning")
    assert "key" in warning.lower() and "device" in warning.lower()
    page.check("#vwAck")
    page.fill("#vwPhrase", page.inner_text("#vwPhraseExpected").strip())
    page.fill("#vwPin", PIN)
    page.click("#vwCreateSubmit")
    page.wait_for_selector("#vwReveal", timeout=30000)
    phrase = page.inner_text("#vwWords").strip()
    words = phrase.split()
    assert len(words) == 12
    page.click("#vwRevealDone")
    page.wait_for_selector("#vwReveal", state="detached")
    # nowhere in the page, nowhere in browser storage, nowhere in the console
    assert phrase not in page.content()
    stores = page.evaluate("() => JSON.stringify({l: Object.assign({}, localStorage), s: Object.assign({}, sessionStorage)})")
    assert phrase_leaked(phrase, stores, run=2) is None, phrase_leaked(phrase, stores, run=2)
    assert PIN not in stores.split('"')
    # a console line leaks the phrase when it carries the phrase itself or two of its words in order (a single word
    # such as "field" also occurs in Chromium's own DOM warnings); the PIN is an exact fixture secret
    leaked = [line for line in console if PIN in line or phrase_leaked(phrase, line, run=2)]
    assert not leaked, leaked
    # a reload cannot bring it back: the page, its API answers, the daemon's log, the DB, the journal
    page.reload(wait_until="networkidle")
    page.wait_for_timeout(300)
    assert phrase not in page.content()
    _open_wallet_settings(page, daemon["base"])
    status_text = page.inner_text("#vwStatus")
    assert "pocket_sealed" in status_text and phrase not in status_text
    api_surface = page.evaluate("async () => { const r = await fetch('/api/wallet/status'); return await r.text(); }")
    assert phrase not in api_surface
    # model context: an ordinary chat turn after creation carries nothing of it to the model
    _back_to_chat(page, daemon["base"])
    _select_model(page, daemon["model"])
    page.fill("#input", "hello there")
    page.click("#send")
    _wait_replies(page, 1, daemon=daemon)
    prompts = " ".join(str(c.get("prompt") or "") for c in daemon["provider"].calls)
    assert phrase_leaked(phrase, prompts) is None, phrase_leaked(phrase, prompts)
    assert PIN not in prompts
    assert errors == []
    page.close()
    # the durable surfaces, scanned last and read-only: the daemon's log, its database, the Blackbox journal
    for surface in (daemon["daemon"].log_tail(lines=600), _all_db_text(daemon["home"]), json.dumps([dict(e) for e in Journal(daemon["store"]).entries()])):
        assert phrase_leaked(phrase, surface, run=2) is None, phrase_leaked(phrase, surface, run=2)


def test_browser_chat_flow_user_asks_model_proposes_operator_approves_with_pin(browser, daemon):
    import urllib.request

    from core.wallet import custody

    req = urllib.request.Request(f"{daemon['base']}/api/wallet/pocket/create", data=json.dumps({"pin": PIN, "acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE}).encode(), headers={"Content-Type": "application/json", "Origin": daemon["base"]}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        created = json.loads(resp.read())
    page, errors, _console = _page(browser, daemon["base"])
    _select_model(page, daemon["model"])
    page.fill("#input", f"Please pay 1500 lamports to {DESTINATION} for the quarterly report")
    page.click("#send")
    _wait_replies(page, 1, daemon=daemon)
    reply = page.evaluate("() => document.body.innerText")
    assert "pending_approval" in reply and "pay-" in reply, reply
    assert daemon["rpc"].send_count() == 0
    # the visible approval step: a card with the amount and destination and a PIN field
    page.wait_for_selector(".vw-card", timeout=30000)
    card = page.inner_text(".vw-card")
    assert "1500" in card and DESTINATION[:8] in card and "model" in card.lower()
    page.fill(".vw-card .vw-pin", "000000")
    page.click(".vw-card .vw-approve")
    page.wait_for_function("() => document.querySelector('.vw-card .vw-result') && document.querySelector('.vw-card .vw-result').textContent.length > 0", timeout=30000)
    assert daemon["rpc"].send_count() == 0 and "not accepted" in page.inner_text(".vw-card .vw-result").lower()
    page.fill(".vw-card .vw-pin", PIN)
    page.click(".vw-card .vw-approve")
    page.wait_for_function("() => /confirmed/i.test((document.querySelector('.vw-card .vw-result') || {}).textContent || '')", timeout=60000)
    result = page.inner_text(".vw-card .vw-result")
    assert daemon["rpc"].send_count() == 1
    signature = daemon["rpc"].signatures()[0]
    assert signature[:12] in result
    proposal_id = next(w.strip(".,:") for w in reply.split() if w.startswith("pay-"))
    page.fill("#input", f"What is the status of payment {proposal_id}?")
    page.click("#send")
    _wait_replies(page, 2, daemon=daemon)
    followup = page.evaluate("() => document.body.innerText")
    assert "confirmed" in followup and signature in followup
    assert daemon["rpc"].send_count() == 1
    assert created["recovery_phrase"] not in page.content()
    assert errors == []
    page.close()


def test_browser_phantom_compatible_external_signing_path(browser, daemon):
    with ExtensionSigner() as ext:
        page, errors, _console = _page(browser, daemon["base"], init_script=fake_phantom_init_script(ext.url, ext.public_key))
        _open_wallet_settings(page, daemon["base"])
        page.wait_for_selector("#vwConnectPhantom", timeout=15000)
        page.click("#vwConnectPhantom")
        page.wait_for_function("() => /external_signer/.test(document.querySelector('#vwStatus').textContent)", timeout=30000)
        _back_to_chat(page, daemon["base"])
        _select_model(page, daemon["model"])
        page.fill("#input", f"Please pay 1200 lamports to {DESTINATION} for hosting")
        page.click("#send")
        _wait_replies(page, 1, daemon=daemon)
        page.wait_for_selector(".vw-card .vw-sign-external", timeout=30000)
        page.click(".vw-card .vw-sign-external")
        page.wait_for_function("() => /confirmed/i.test((document.querySelector('.vw-card .vw-result') || {}).textContent || '')", timeout=60000)
        calls = page.evaluate("() => window.__fakePhantomCalls")
        assert calls and calls[0]["method"] == "signTransaction" and "message" in calls[0]["params"]
        assert daemon["rpc"].send_count() == 1 and len(ext.signed) == 1
        sent = daemon["rpc"].sent[0]
        assert sent[65:] == ext.signed[0], "the broadcast bytes are exactly the message the extension signed"
        # sabotage from the wallet side: a tampering extension signs other bytes -> refused, no broadcast
        ext.tamper = True
        page.fill("#input", f"Please pay 1300 lamports to {DESTINATION} for storage")
        page.click("#send")
        _wait_replies(page, 2, daemon=daemon)
        page.wait_for_function("() => document.querySelectorAll('.vw-card').length >= 2", timeout=30000)
        page.click(".vw-card:last-of-type .vw-sign-external")
        page.wait_for_function("() => { const c = document.querySelectorAll('.vw-card'); const r = c[c.length-1].querySelector('.vw-result'); return r && r.textContent.length > 0; }", timeout=60000)
        last = page.evaluate("() => { const c = document.querySelectorAll('.vw-card'); return c[c.length-1].querySelector('.vw-result').textContent; }")
        assert "did not match" in last.lower() or "invalid" in last.lower()
        assert daemon["rpc"].send_count() == 1
        assert errors == []
        page.close()
