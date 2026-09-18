"""The funded-token journey a person takes, in ONE production served process.

Settings Save (the composed verify-before-store intake) -> Test (a balance read) -> discovery (observed
listing and exact prices) -> choose a genuinely listed model in the chat model menu (the paid gate asks)
-> route approval -> spend consent -> an ordinary chat turn -> the answer and an Activity receipt that
correlates with the money law's own liability. An original and a novel model and prompt; a funded balance
is liquidity, not permission; late changes cannot consume old authority; the token stays in its sealed
store.

SYNTHETIC PROVIDER: the strict local UsePod stand-in on loopback, a random token minted here and synthetic
funds. The daemon is ``apps.vool_api_server`` unchanged, with the production money law (no monetary
double) and the production credential intake. Evidence levels: the Settings, chat and Activity steps are
browser-level (served pages in Chromium, every non-daemon URL aborted); the late-change controls and the
leak scan use the served HTTP doors and are API-level.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.served_browser import launch_chromium
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService, default_reply
from tests.usepod.test_usepod_served_flow import INFERENCE_PATHS, MARKET, MARKET_ID, MODEL, _completed_receipts, _keep, _rejections, _session
from tests.usepod.test_usepod_settings_ui import CENTRAL, PlainServedDaemon
from tests.usepod.test_usepod_spend_approval_served import _resolve_approval

NOVEL_MODEL = "meridian-synth-chat-mini"
NOVEL_MARKET_ID = "7e8f9a0b-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
NOVEL_MARKET = (330_000, 880_000)
LAW_LABEL = "effect_budget_money:v1"
#: /api/chat request bodies each page sent, by page identity (a Playwright page takes no attributes).
_SENDS: dict[int, list[str]] = {}


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={
            MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)],
            NOVEL_MODEL: [Listing("marketplace", NOVEL_MARKET_ID, *NOVEL_MARKET)],
        },
    ).start()
    daemon = PlainServedDaemon(tmp_path_factory.mktemp("usepod-funded-journey") / "home")
    manager = browser = None
    try:
        daemon.start()
        manager, browser = launch_chromium()
        yield SimpleNamespace(service=service, daemon=daemon, browser=browser, token=token, secrets=[token], sessions=[], state={})
    finally:
        if browser is not None:
            browser.close()
        if manager is not None:
            manager.stop()
        daemon.stop()
        service.stop()


# --- browser helpers -----------------------------------------------------------------------------------


def _page(journey, path: str):
    page = journey.browser.new_page()
    base = journey.daemon.base_url
    # The browser reaches only the daemon under test: every other URL is aborted.
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
    sends = _SENDS.setdefault(id(page), [])
    page.on("request", lambda request: sends.append(request.post_data) if request.url == base + "/api/chat" else None)
    page.goto(base + path, wait_until="networkidle")
    return page


def _usepod_panel(journey):
    page = _page(journey, "/settings#models")
    page.wait_for_selector(".usepod-model-search", timeout=20000)
    # the pane paints twice (shell, then sources); drive the second draw
    page.wait_for_timeout(600)
    return page


def _model_row(page, model_id: str):
    exact = re.compile(rf"^{re.escape(model_id)}$")
    return page.locator(".usepod-model").filter(has=page.locator("span.profile-value.mono", has_text=exact))


def _inference_requests(service) -> int:
    return sum(len(service.requests_to(path)) for path in INFERENCE_PATHS.values())


def _selection(model_id: str) -> str:
    """The selection identity the chat page carries for a non-OpenRouter provider's pin: ``provider:model``."""
    return f"usepod:{model_id}"


def _pick_in_chat(page, model_id: str) -> None:
    """The person opens the model menu, picks the listed row, and answers the paid gate(s) the page raises."""
    pins: list[dict] = []
    problems: list[str] = []
    page.on("response", lambda response: pins.append({"status": response.status, "url": response.url}) if "/api/cloud/model" in response.url and response.request.method == "POST" else None)
    page.on("console", lambda message: problems.append(f"{message.type}: {message.text}"[:300]) if message.type in {"error", "warning"} else None)
    page.on("pageerror", lambda error: problems.append(f"pageerror: {error}"[:300]))
    page.click("#modelBtn")
    row = page.locator(f'#modelPop .cloud-dyn.pop-item[data-model="{model_id}"]')
    row.wait_for(state="visible", timeout=30000)
    row.click()
    gates: list[str] = []
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        gate = page.locator(".vg-btn.vg-danger")
        if gate.count() and gate.first.is_visible():
            gates.append(page.locator(".vg-btn.vg-danger").first.evaluate("b => (b.closest('[class]') || b).innerText")[:400])
            gate.first.click()
        if page.evaluate("() => { try { return modelValue; } catch (e) { return '<no modelValue>'; } }") == _selection(model_id):
            return
        page.wait_for_timeout(200)
    state = {
        "model_value": page.evaluate("() => { try { return String(modelValue); } catch (e) { return '<no modelValue>'; } }"),
        "label": page.evaluate("() => (document.getElementById('modelLbl') || {}).textContent || ''"),
        "pin_posts": pins, "gates_answered": gates, "problems": problems[-20:],
        "toasts": page.evaluate("() => Array.from(document.querySelectorAll('.toast, [role=status]')).map(n => n.textContent).slice(-8)"),
    }
    _keep(f"journey_pick_failure_{model_id}.json", state)
    if _evidence_dir():
        page.screenshot(path=str(Path(_evidence_dir()) / f"journey_pick_failure_{model_id}.png"), full_page=True)
    raise AssertionError(f"the chat page never pinned {model_id}: {json.dumps(state)[:1500]}")


def _send_in_chat(page, text: str) -> tuple[str, dict, list[str]]:
    """Send like a person: answer the page's own per-turn paid confirmation when it asks, then wait for THIS turn to
    be released. Returns the visible answer, the request body and the confirmation texts the page showed."""
    previous = page.evaluate("() => (view.run && view.run.turnId) || ''")
    sends = _SENDS[id(page)]
    sent_before = len(sends)
    page.fill("#input", text)
    page.click("#send")
    confirmations: list[str] = []
    released = "(prev) => !!(view.run && view.run.turnId && view.run.turnId !== prev && view.run.released)"
    deadline = time.monotonic() + 240.0
    while time.monotonic() < deadline:
        gate = page.locator(".vg-btn.vg-danger")
        if gate.count() and gate.first.is_visible():
            confirmations.append(gate.first.evaluate("b => (b.parentElement && b.parentElement.parentElement ? b.parentElement.parentElement : b).innerText")[:600])
            gate.first.click()
        if page.evaluate(released, previous):
            break
        page.wait_for_timeout(200)
    else:
        state = {
            "run": page.evaluate("() => view.run ? {turnId: view.run.turnId, status: view.run.status, released: view.run.released} : null"),
            "requests_sent": len(sends) - sent_before, "confirmations": confirmations,
        }
        _keep("journey_send_timeout.json", state)
        if _evidence_dir():
            page.screenshot(path=str(Path(_evidence_dir()) / "journey_send_timeout.png"), full_page=True)
        raise AssertionError(f"the turn was never released: {json.dumps(state)[:1200]}")
    bodies = [json.loads(data) for data in sends[sent_before:] if data]
    assert bodies, "the page sent no /api/chat request"
    return page.locator(".msg.assistant .msg-text").last.inner_text(), bodies[-1], confirmations


def _open_receipt_in_activity(page) -> str:
    """Open Activity and expand the finished turn's work log the way a person does (the page's own Expand all),
    then read what is visible. A finished turn collapses to one line, so the receipt sits behind a disclosure."""
    page.click("#panelBtn")
    expand = page.locator('button[data-activity-action="expand"]').first
    expand.wait_for(state="visible", timeout=30000)
    expand.click()
    page.wait_for_function("() => /UsePod receipt/.test(document.body.innerText)", timeout=30000)
    return page.inner_text("body")


def _consent_in_panel(journey, *, per_call: int, fresh: bool) -> str:
    page = _usepod_panel(journey)
    try:
        page.wait_for_selector(".usepod-spend-approval", timeout=20000)
        if fresh:
            page.click("button.usepod-spend-propose-again")
        else:
            page.fill("input.usepod-spend-percall", str(per_call))
            page.fill("input.usepod-spend-total", str(per_call))
            page.fill("input.usepod-spend-hours", "2")
            page.click("button.usepod-spend-propose")
        page.wait_for_selector("button.usepod-spend-allow", timeout=20000)
        pending = page.inner_text(".usepod-spend-approval")
        page.click("button.usepod-spend-allow")
        page.wait_for_selector("button.usepod-spend-confirm", timeout=20000)
        page.click("button.usepod-spend-confirm")
        page.wait_for_function("() => /Enabled:/.test((document.querySelector('.usepod-note') || {textContent:''}).textContent || '')", timeout=20000)
        return pending
    finally:
        page.close()


def _approve_route_in_panel(journey, model_id: str) -> None:
    page = _usepod_panel(journey)
    try:
        row = _model_row(page, model_id)
        row.locator("button.usepod-route-approve").wait_for(state="visible", timeout=20000)
        page.once("dialog", lambda dialog: dialog.accept())
        row.locator("button.usepod-route-approve").click()
        row.locator("button.usepod-route-forget").wait_for(state="visible", timeout=30000)
    finally:
        page.close()


def _correlated_liability(journey, session_id: str, model_id: str) -> tuple[dict, dict]:
    daemon = journey.daemon
    receipts = [item for item in _completed_receipts(daemon.events(session_id)) if item.get("provider") == "usepod"]
    assert len(receipts) == 1, receipts
    receipt = receipts[0]
    status, found = daemon.call("GET", f"/api/money/liabilities/{receipt['reservation_id']}")
    assert status == 200, found
    liability = found["liability"]
    assert liability["operation_id"] == receipt["operation_id"]
    assert (liability["state"], liability["provider_id"], liability["model_id"]) == ("settled", "usepod", model_id), liability
    assert liability["provider_account"] == receipt["credential_fingerprint"] == journey.state["fingerprint"]
    assert receipt["monetary_authority"] == LAW_LABEL
    assert receipt["settlement"]["recording"] == "settled_with_evidence"
    return receipt, liability


# --- the journey ---------------------------------------------------------------------------------------


def test_save_test_and_discovery_observe_without_spending(journey) -> None:
    daemon, service, token = journey.daemon, journey.service, journey.token
    page = _page(journey, "/settings#keys")
    try:
        page.wait_for_selector("select[aria-label='Provider']", timeout=20000)
        page.wait_for_timeout(600)
        page.select_option("select[aria-label='Provider']", "usepod")
        page.fill("input[aria-label='UsePod token or proxy URL']", f"{service.origin}/proxy/{token}/v1")
        page.fill("input[aria-label='Provider origin']", service.origin)
        page.click("button.key-save")
        page.wait_for_function("() => /Stored, sealed on this machine/.test((document.querySelector('.key-save-state') || {textContent:''}).textContent || '')", timeout=30000)
        note = page.inner_text(".key-save-state")
        assert service.origin in note and token not in note
    finally:
        page.close()
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert status == 200 and view["credential"]["configured"] is True, view["credential"]
    journey.state["fingerprint"] = view["credential"]["fingerprint"]

    page = _usepod_panel(journey)
    try:
        page.wait_for_selector("button.usepod-test", timeout=20000)
        page.click("button.usepod-test")
        page.wait_for_function("() => /Connection verified/.test((document.querySelector('.usepod-test-state') || {textContent:''}).textContent || '')", timeout=30000)
        page.click("button.usepod-refresh")
        page.wait_for_selector(".usepod-model", timeout=30000)
        _model_row(page, NOVEL_MODEL).wait_for(state="visible", timeout=30000)
        text = page.inner_text("body")
        _keep("journey_settings_after_refresh.txt", text)
        if _evidence_dir():
            page.screenshot(path=str(Path(_evidence_dir()) / "journey_settings_after_refresh.png"), full_page=True)
        assert f"in {MARKET[0]} + out {MARKET[1]} µUSDC per Mtok" in text
        assert f"in {NOVEL_MARKET[0]} + out {NOVEL_MARKET[1]} µUSDC per Mtok" in text
        assert token not in text
    finally:
        page.close()

    # Discovery: observed listing and exact integer prices with provenance; nothing is priced at zero.
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    rows = {row["model_id"]: row for row in view["models"]}
    assert rows[MODEL]["listed_for_this_credential"] is True and rows[NOVEL_MODEL]["listed_for_this_credential"] is True
    assert rows[NOVEL_MODEL]["marketplace"]["input_microunits_per_million"] == NOVEL_MARKET[0]
    assert all(int(row["marketplace"]["input_microunits_per_million"]) > 0 for row in rows.values() if row.get("marketplace"))
    assert view["documented_facts"], "documented capability facts are named separately from observations"
    _keep("journey_discovery.json", {"marketplace": view["marketplace"], "models": view["models"], "capabilities": view["capabilities"]})

    # Save, Test and discovery read the token's balance and the public feed only: nothing was spent.
    assert _inference_requests(service) == 0
    assert len(service.requests_to("/proxy/{token}/balance")) >= 2
    status, liabilities = daemon.call("GET", "/api/money/liabilities")
    assert status == 200 and liabilities["liabilities"] == [], liabilities


def test_the_original_model_answers_in_chat_with_a_correlated_receipt(journey) -> None:
    daemon, service = journey.daemon, journey.service
    assert journey.state.get("fingerprint"), "requires the credential saved by the first journey test"
    chat = _page(journey, "/chat")
    try:
        chat.wait_for_selector("#input", timeout=20000)
        _pick_in_chat(chat, MODEL)

        # Before any route approval the pinned lane refuses, and says so; nothing reaches the provider.
        before = _inference_requests(service)
        answer, body, asked = _send_in_chat(chat, "Write a short thank-you note to a neighbor who watered my plants.")
        # The page paused for the person's per-turn confirmation of the paid pin before sending anything.
        assert asked and "CONFIRM THIS TURN" in asked[0], asked
        session_id = str(body["session_id"])
        assert body["model"] == _selection(MODEL) and body["model_selection"] == "pin", body
        assert "usepod_route_not_approved" in _rejections(daemon.events(session_id))
        assert _inference_requests(service) == before and MODEL in answer

        _approve_route_in_panel(journey, MODEL)

        # A funded balance and an approved route are not permission: without consent it still refuses.
        answer, body, _asked = _send_in_chat(chat, "Write a short thank-you note to a neighbor who watered my plants.")
        assert "MONEY_AUTHORITY_INVALID" in json.dumps(daemon.events(str(body["session_id"])))
        assert _inference_requests(service) == before

        pending = _consent_in_panel(journey, per_call=500_000, fresh=False)
        assert "ONE call" in pending and journey.state["fingerprint"] in pending and MODEL in pending

        start = len(service.requests_to(INFERENCE_PATHS["openai"]))
        answer, body, _asked = _send_in_chat(chat, "Write a short message inviting my team to a Friday retrospective.")
        session_id = str(body["session_id"])
        journey.sessions.append(session_id)
        arrived = [json.loads(request["body"]) for request in service.requests_to(INFERENCE_PATHS["openai"])[start:]]
        assert len(arrived) == 1, "exactly one answer request reached the provider"
        reply = default_reply(arrived[0], "openai").text
        assert reply in answer, (answer, reply)

        activity = _open_receipt_in_activity(chat)
        assert "settled with evidence" in activity and f"money: {LAW_LABEL}" in activity
        if _evidence_dir():
            chat.screenshot(path=str(Path(_evidence_dir()) / "journey_chat_original_receipt.png"), full_page=True)
        receipt, liability = _correlated_liability(journey, session_id, MODEL)
        assert (receipt["route"]["class"], receipt["route"]["provider_id"]) == ("marketplace", MARKET_ID)
        _keep("journey_original.json", {"answer": answer, "receipt": receipt, "liability": liability})

        # Single-call consent is single-use: the next turn refuses before any byte.
        after = _inference_requests(service)
        answer, body, _asked = _send_in_chat(chat, "Write a two-line limerick about a cat.")
        assert "MONEY_AUTHORITY_" in json.dumps(daemon.events(str(body["session_id"])))
        assert _inference_requests(service) == after
    finally:
        chat.close()


def test_a_novel_model_and_prompt_take_the_same_journey(journey) -> None:
    daemon, service = journey.daemon, journey.service
    assert journey.sessions, "requires the original journey"
    chat = _page(journey, "/chat")
    try:
        chat.wait_for_selector("#input", timeout=20000)
        _pick_in_chat(chat, NOVEL_MODEL)
        _approve_route_in_panel(journey, NOVEL_MODEL)
        _consent_in_panel(journey, per_call=400_000, fresh=True)
        start = len(service.requests_to(INFERENCE_PATHS["openai"]))
        answer, body, asked = _send_in_chat(chat, "Draft three friendly subject lines for a neighborhood garden-swap newsletter.")
        assert asked and "CONFIRM THIS TURN" in asked[0], asked
        session_id = str(body["session_id"])
        journey.sessions.append(session_id)
        assert body["model"] == _selection(NOVEL_MODEL), body
        arrived = [json.loads(request["body"]) for request in service.requests_to(INFERENCE_PATHS["openai"])[start:]]
        assert len(arrived) == 1 and arrived[0]["model"] == NOVEL_MODEL, arrived
        assert default_reply(arrived[0], "openai").text in answer
        _open_receipt_in_activity(chat)
        if _evidence_dir():
            chat.screenshot(path=str(Path(_evidence_dir()) / "journey_chat_novel_receipt.png"), full_page=True)
        receipt, liability = _correlated_liability(journey, session_id, NOVEL_MODEL)
        assert (receipt["route"]["class"], receipt["route"]["provider_id"]) == ("marketplace", NOVEL_MARKET_ID)
        _keep("journey_novel.json", {"answer": answer, "receipt": receipt, "liability": liability})
    finally:
        chat.close()


def _chat_api(daemon, model_id: str, text: str) -> tuple[str, int, object]:
    session_id = _session(f"late-{uuid.uuid4()}")
    payload = {"messages": [{"role": "user", "content": text}], "stream": False, "session_id": session_id, "model": f"usepod-byok:{model_id}", "mode": "auto"}
    status, answer = daemon.call("POST", "/api/chat", payload, timeout=300.0)
    return session_id, status, answer


def _consent_api(daemon, per_call: int) -> str:
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": per_call, "max_total_atomic": per_call})
    assert status == 200, proposed
    status, resolved = _resolve_approval(daemon, proposed["approval_id"], "allow")
    assert status == 200, resolved
    status, minted = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": proposed["approval_id"]})
    assert status == 200, minted
    return str(minted["grant_id"])


def test_late_route_or_account_changes_cannot_consume_the_consent(journey) -> None:
    """API-level. One consent for the current account; the route is withdrawn, then the token is replaced:
    both turns refuse before any byte. Restoring the original pair lets that same consent pay its one call."""
    daemon, service, token = journey.daemon, journey.service, journey.token
    assert journey.sessions, "requires the original journey"
    grant_id = _consent_api(daemon, 500_000)
    before = _inference_requests(service)

    status, _ = daemon.call("POST", "/api/cloud/usepod/forget-route", {"model_id": NOVEL_MODEL})
    assert status == 200
    session_id, status, _answer = _chat_api(daemon, NOVEL_MODEL, "Suggest a name for a small community tool library.")
    assert "usepod_route_not_approved" in _rejections(daemon.events(session_id)) and _inference_requests(service) == before
    status, _ = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": NOVEL_MODEL})
    assert status == 200

    replacement = str(uuid.uuid4())
    journey.secrets.append(replacement)
    service.tokens[replacement] = 3_000_000
    status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": replacement, "base_url": service.origin})
    assert status == 200 and saved["credential_fingerprint"] != journey.state["fingerprint"], saved
    # The replacement account's balance is observed first, so what refuses is the consent's account binding
    # itself -- not the (also correct) refusal of an account whose liquidity was never read.
    status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
    assert status == 200 and refreshed["credential"]["state"] == "observed", refreshed
    liabilities_before = len(daemon.call("GET", "/api/money/liabilities")[1]["liabilities"])
    session_id, status, _answer = _chat_api(daemon, NOVEL_MODEL, "Suggest a name for a small community tool library.")
    events = daemon.events(session_id)
    _keep("journey_late_account_change.json", {"rejections": _rejections(events), "inference_requests_delta": _inference_requests(service) - before})
    assert "MONEY_AUTHORITY_" in json.dumps(events), _rejections(events)
    assert _inference_requests(service) == before
    assert len(daemon.call("GET", "/api/money/liabilities")[1]["liabilities"]) == liabilities_before, "a refused turn left a liability"
    # The refused turns left the consent untouched: no liability was ever recorded against it.
    status, against = daemon.call("GET", f"/api/money/liabilities?grant_id={grant_id}")
    assert status == 200 and against["liabilities"] == [], against

    status, restored = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": token, "base_url": service.origin})
    assert status == 200 and restored["credential_fingerprint"] == journey.state["fingerprint"], restored
    session_id, status, answer = _chat_api(daemon, NOVEL_MODEL, "Suggest a name for a small community tool library.")
    journey.sessions.append(session_id)
    assert _inference_requests(service) == before + 1
    _receipt, liability = _correlated_liability(journey, session_id, NOVEL_MODEL)
    assert liability["grant_id"] == grant_id, "the restored pair paid its one call under the consent minted for it"


def test_other_provider_setup_controls_still_work_beside_usepod(journey) -> None:
    """Browser-level. The one provider selector still offers the other cloud providers and the web-search
    providers beside UsePod, and an OpenRouter key saved for later lands in quarantine through the same intake
    without touching the UsePod pair, the active lane or its catalogue."""
    daemon = journey.daemon
    assert journey.sessions, "requires the journey"
    status, before = daemon.call("GET", "/api/cloud/usepod/discovery")
    later_key = "sk-or-v1-" + uuid.uuid4().hex + uuid.uuid4().hex
    journey.secrets.append(later_key)
    page = _page(journey, "/settings#keys")
    try:
        page.wait_for_selector("select[aria-label='Provider']", timeout=20000)
        page.wait_for_timeout(600)
        groups = page.eval_on_selector_all(
            "select[aria-label='Provider'] optgroup",
            "gs => gs.map(g => [g.label, Array.from(g.querySelectorAll('option')).map(o => o.value)])",
        )
        offered = {value for _label, values in groups for value in values}
        assert {"openrouter", "custom", "usepod"} <= offered, groups
        assert any(label == "Web search" and any("brave" in value for value in values) for label, values in groups), groups
        page.select_option("select[aria-label='Provider']", "openrouter")
        page.fill("input[aria-label='API key']", later_key)
        page.click("button.key-save-later")
        page.wait_for_function(
            "() => /Stored sealed and UNVERIFIED for later/.test((document.querySelector('.key-save-state') || {textContent:''}).textContent || '')",
            timeout=30000,
        )
        assert later_key not in page.content()
    finally:
        page.close()
    status, after = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert after["credential"] == before["credential"], (before["credential"], after["credential"])
    status, catalog = daemon.call("GET", "/api/cloud/models")
    assert status == 200 and catalog["provider"] == "usepod", catalog.get("provider")


def test_the_tokens_appear_on_no_served_surface_log_or_file(journey) -> None:
    """API-level plus pages: every door the pages read, both pages, the daemon log and every file of the home."""
    daemon = journey.daemon
    assert journey.sessions, "requires the journey"
    texts: dict[str, str] = {}
    paths = ["/api/settings/credentials", "/api/cloud/usepod/discovery", "/api/cloud/models?provider=usepod", "/api/cloud/model",
             "/api/money/liabilities", "/api/money/grants", "/api/cloud/usepod/spend-approval/pending"]
    paths += [f"/api/runtime/events?session={session}&limit=500" for session in journey.sessions]
    for path in paths:
        status, body = daemon.call("GET", path)
        texts[path] = body if isinstance(body, str) else json.dumps(body)
    for path in ("/settings#keys", "/settings#models", "/chat"):
        page = _page(journey, path)
        try:
            page.wait_for_timeout(800)
            texts[f"page:{path}"] = page.content()
        finally:
            page.close()
    texts["daemon.log"] = daemon.log_path.read_text("utf-8", "replace")
    leaks = [(name, secret[:8]) for name, text in texts.items() for secret in journey.secrets if secret in text]
    file_leaks = []
    for path in daemon.home.rglob("*"):
        if path.is_file() and path.stat().st_size < 64 * 1024 * 1024:
            data = path.read_bytes()
            file_leaks += [(str(path.relative_to(daemon.home)), secret[:8]) for secret in journey.secrets if secret.encode() in data]
    _keep("journey_leak_scan.json", {"surfaces": sorted(texts), "files_scanned": sum(1 for p in daemon.home.rglob("*") if p.is_file()), "leaks": leaks, "file_leaks": file_leaks})
    assert leaks == [] and file_leaks == [], (leaks, file_leaks)


def _evidence_dir() -> str:
    import os

    target = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR", "")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
    return target
