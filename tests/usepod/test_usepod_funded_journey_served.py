"""Retained monetary journey through a normal daemon and actual vault, consent and ledger.

Central vendor Settings, composer preflight and price widgets were removed by TDL L35.
Their replacement Add-ons/browser workflows live in test_plugin_served_journey.py.
These API-level cases preserve the original observed prices, no-grant/refund refusals,
correlated receipts, novel model and late account-change financial controls. The
remaining provider setup and secret exposure checks still use the real browser.
The provider and funds are synthetic; no monetary authority is substituted.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.served_browser import launch_chromium
from tests.usepod._served_daemon import CENTRAL, PlainServedDaemon
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService, default_reply
from tests.usepod.test_usepod_served_flow import (
    INFERENCE_PATHS,
    MARKET,
    MARKET_ID,
    MODEL,
    _answer_text,
    _completed_receipts,
    _keep,
    _rejections,
    _session,
)
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
        daemon.seed_legacy_profile(origin=service.origin, token=token, models=tuple(service.models))
        daemon.start()
        status, policy = daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
        assert status == 200, policy
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






def _inference_requests(service) -> int:
    return sum(len(service.requests_to(path)) for path in INFERENCE_PATHS.values())














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
    status, probe = daemon.call("POST", "/api/cloud/usepod/balance", {"max_age_seconds": 0})
    assert status == 200 and probe["state"] == "reported", probe
    status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
    assert status == 200 and refreshed["credential"]["state"] == "observed", refreshed
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert status == 200 and view["credential"]["configured"] is True, view["credential"]
    journey.state["fingerprint"] = view["credential"]["fingerprint"]
    rows = {row["model_id"]: row for row in view["models"]}
    assert rows[MODEL]["listed_for_this_credential"] is True and rows[NOVEL_MODEL]["listed_for_this_credential"] is True
    assert rows[NOVEL_MODEL]["marketplace"]["input_microunits_per_million"] == NOVEL_MARKET[0]
    assert all(int(row["marketplace"]["input_microunits_per_million"]) > 0 for row in rows.values() if row.get("marketplace"))
    assert view["documented_facts"], "documented capability facts are named separately from observations"
    _keep("journey_discovery.json", {"marketplace": view["marketplace"], "models": view["models"], "capabilities": view["capabilities"]})
    assert token not in json.dumps(view) + json.dumps(probe)
    assert _inference_requests(service) == 0
    assert len(service.requests_to("/proxy/{token}/balance")) >= 2
    status, liabilities = daemon.call("GET", "/api/money/liabilities")
    assert status == 200 and liabilities["liabilities"] == [], liabilities


def test_the_original_model_answers_in_chat_with_a_correlated_receipt(journey) -> None:
    daemon, service = journey.daemon, journey.service
    assert journey.state.get("fingerprint"), "requires the credential saved by the first journey test"
    status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, approved
    before = _inference_requests(service)
    draft = "Write a short thank-you note to a neighbor who watered my plants."
    session_id, _status, _answer = _chat_api(daemon, MODEL, draft)
    assert "MONEY_AUTHORITY_INVALID" in json.dumps(daemon.events(session_id))
    assert _inference_requests(service) == before

    grant_id = _consent_api(daemon, 500_000)
    start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    session_id, status, answer = _chat_api(daemon, MODEL, "Write a short message inviting my team to a Friday retrospective.")
    assert status == 200, answer
    journey.sessions.append(session_id)
    arrived = [json.loads(request["body"]) for request in service.requests_to(INFERENCE_PATHS["openai"])[start:]]
    assert len(arrived) == 1, "exactly one answer request reached the provider"
    reply = default_reply(arrived[0], "openai").text
    assert reply in _answer_text(answer), (answer, reply)
    receipt, liability = _correlated_liability(journey, session_id, MODEL)
    assert (receipt["route"]["class"], receipt["route"]["provider_id"]) == ("marketplace", MARKET_ID)
    _keep("journey_original.json", {"answer": answer, "receipt": receipt, "liability": liability})

    after = _inference_requests(service)
    liabilities_after = len(daemon.call("GET", "/api/money/liabilities")[1]["liabilities"])
    status, revoked = daemon.call("POST", "/api/money/grants/revoke", {"grant_id": grant_id})
    assert status == 200, revoked
    session_id, _status, _answer = _chat_api(daemon, MODEL, "Write a two-line limerick about a cat.")
    assert "MONEY_AUTHORITY_REVOKED" in json.dumps(daemon.events(session_id))
    assert _inference_requests(service) == after
    assert len(daemon.call("GET", "/api/money/liabilities")[1]["liabilities"]) == liabilities_after, "a refused turn left a liability"


def test_a_novel_model_and_prompt_take_the_same_journey(journey) -> None:
    daemon, service = journey.daemon, journey.service
    assert journey.sessions, "requires the original journey"
    status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": NOVEL_MODEL})
    assert status == 200, approved
    _consent_api(daemon, 400_000)
    start = len(service.requests_to(INFERENCE_PATHS["openai"]))
    session_id, status, answer = _chat_api(daemon, NOVEL_MODEL, "Draft three friendly subject lines for a neighborhood garden-swap newsletter.")
    assert status == 200, answer
    journey.sessions.append(session_id)
    arrived = [json.loads(request["body"]) for request in service.requests_to(INFERENCE_PATHS["openai"])[start:]]
    assert len(arrived) == 1 and arrived[0]["model"] == NOVEL_MODEL, arrived
    assert default_reply(arrived[0], "openai").text in _answer_text(answer)
    receipt, liability = _correlated_liability(journey, session_id, NOVEL_MODEL)
    assert (receipt["route"]["class"], receipt["route"]["provider_id"]) == ("marketplace", NOVEL_MARKET_ID)
    _keep("journey_novel.json", {"answer": answer, "receipt": receipt, "liability": liability})


def _chat_api(daemon, model_id: str, text: str) -> tuple[str, int, object]:
    session_id = _session(f"late-{uuid.uuid4()}")
    payload = {"messages": [{"role": "user", "content": text}], "stream": False, "session_id": session_id, "model": f"usepod-byok:{model_id}", "mode": "auto"}
    status, answer = daemon.call("POST", "/api/chat", payload, timeout=300.0)
    return session_id, status, answer


def _consent_api(daemon, per_call: int) -> str:
    status, grants = daemon.call("GET", "/api/money/grants?active=1")
    assert status == 200, grants
    for grant in grants["grants"]:
        status, revoked = daemon.call("POST", "/api/money/grants/revoke", {"grant_id": grant["grant_id"]})
        assert status == 200, revoked
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
    saved = daemon.seed_legacy_profile(origin=service.origin, token=replacement, models=tuple(service.models))
    assert saved["credential_fingerprint"] != journey.state["fingerprint"], saved
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

    restored = daemon.seed_legacy_profile(origin=service.origin, token=token, models=tuple(service.models))
    assert restored["credential_fingerprint"] == journey.state["fingerprint"], restored
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
        assert {"openrouter", "custom"} <= offered, groups
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
    assert status == 200 and after["models"] == before["models"], (before, after)


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
