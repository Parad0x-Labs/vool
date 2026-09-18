"""The trusted spend-approval user flow, served and browser-driven.

No grant exists → the operator proposes spending from Settings (the SERVER derives account,
asset, models and routes; the operator sets only the ceilings) → the PENDING approval lives in
the SAME trusted store the tool gate uses → the operator ALLOWS it with the VISIBLE Allow
control (the same trusted resolution door the chat approval buttons use) → a full page RELOAD
between Allow and confirm (the consent survives in the state model, not the page) → Settings
confirms → ONE grant is minted through the real operator authority → ONE synthetic local
provider call runs, settles truthfully BOUNDED, and the money survives a restart → the spent
single-payment consent refuses the next call until the operator consents again → a denied
proposal mints nothing → a minted grant revokes through the existing money route. The denial
and control tests resolve approvals through the same door over HTTP and are labelled mixed
browser/API proof.

Browser-level evidence against the served /settings page (NOT native-window acceptance). All
funds are labelled synthetic; the provider is the SYNTHETIC strict local service.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.served_browser import launch_chromium
from tests.usepod.strict_usepod_service import Listing, StrictUsePodService
from tests.usepod.test_usepod_money_law_served import CENTRAL, LANE_ID, MODEL, MoneyLawDaemon, _session
from tests.usepod.test_usepod_served_flow import MARKET, MARKET_ID


def _chat(daemon, text: str, session_id: str):
    payload = {"messages": [{"role": "user", "content": text}], "stream": False, "session_id": session_id, "model": LANE_ID, "mode": "auto"}
    return daemon.call("POST", "/api/chat", payload, timeout=300.0)


def _answer_text(answer) -> str:
    if isinstance(answer, dict):
        message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
        return str(message.get("content") or "")
    return str(answer)


#: The operator's own session for approval resolutions (the chat approval buttons send the
#: chat they are approving from; a Settings-originated approval has no chat, so the operator's
#: local session identity is the honest scope).
OPERATOR_SESSION = "openclaw:settings-operator"


def _resolve_approval(daemon, approval_id: str, decision: str):
    """The OPERATOR's resolution, through the same door the chat approval buttons use."""
    return daemon.call("POST", "/api/mode", {"op": "resolve_approval", "approval_id": approval_id, "decision": decision, "session_id": OPERATOR_SESSION}, timeout=60.0)


def _keep(name: str, payload: object) -> None:
    keep = os.environ.get("USEPOD_SERVED_ARTIFACT_DIR")
    if keep:
        Path(keep).mkdir(parents=True, exist_ok=True)
        (Path(keep) / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("usepod-spend-approval")
    token = str(uuid.uuid4())
    service = StrictUsePodService(
        tokens={token: 80_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    ).start()
    daemon = MoneyLawDaemon(root / "home")
    manager = browser = None
    try:
        daemon.start()
        status, saved = daemon.call("POST", "/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
        assert status == 200, saved
        status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
        assert status == 200 and refreshed["credential"]["state"] == "observed", refreshed
        daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
        status, pinned = daemon.call("POST", "/api/cloud/model", {"model": MODEL, "provider": "usepod", "confirm_paid": True})
        assert status == 200 and pinned.get("ok") is True, pinned
        status, approved = daemon.call("POST", "/api/cloud/usepod/approve-route", {"model_id": MODEL})
        assert status == 200, approved
        manager, browser = launch_chromium()
        yield SimpleNamespace(service=service, daemon=daemon, token=token, fingerprint=saved["credential_fingerprint"], browser=browser)
    finally:
        if browser is not None:
            browser.close()
        if manager is not None:
            manager.stop()
        daemon.stop()
        service.stop()


def _open_panel(served):
    page = served.browser.new_page()
    page.goto(f"{served.daemon.base_url}/settings#models", wait_until="networkidle")
    page.wait_for_selector(".usepod-spend-approval", timeout=20000)
    page.wait_for_timeout(600)
    return page


def test_the_full_user_flow_no_grant_to_trusted_consent_to_one_settled_call(served) -> None:
    daemon, service = served.daemon, served.service
    page = _open_panel(served)
    try:
        text = page.inner_text("body")
        # No grant exists: the panel says so, and the approval form is offered.
        assert "NO active spend grant" in text
        assert page.query_selector("input.usepod-spend-percall") is not None

        # A paid turn before consent refuses with the money law's code, nothing sent.
        before = len(service.requests_to("/proxy/{token}/v1/chat/completions"))
        session_id = _session(f"pre-consent-{uuid.uuid4()}")
        status, answer = _chat(daemon, "Write a short thank-you note to a neighbor who watered my plants.", session_id)
        assert status == 200 and "MONEY_AUTHORITY_INVALID" in json.dumps(daemon.events(session_id))
        assert len(service.requests_to("/proxy/{token}/v1/chat/completions")) == before

        # The operator proposes: ceilings only; the facts come back server-derived.
        page.fill("input.usepod-spend-percall", "0.5")
        page.fill("input.usepod-spend-total", "5")
        page.fill("input.usepod-spend-hours", "2")
        page.click("button.usepod-spend-propose")
        page.wait_for_selector("button.usepod-spend-allow", timeout=20000)
        pending_text = page.inner_text(".usepod-spend-approval")
        assert "ONE call" in pending_text and "0.5 USDC" in pending_text
        assert served.fingerprint in pending_text and MODEL in pending_text
        status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
        approval_id = view["spend_approval"]["approval_id"]

        # Confirm BEFORE the operator allows: refused, nothing minted.
        status, early = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": approval_id})
        assert status == 403 and "awaiting" in str(early.get("error", "")), early

        # The OPERATOR allows it with the VISIBLE Allow control, through the same trusted
        # resolution door the chat approval buttons use.
        page.click("button.usepod-spend-allow")
        page.wait_for_selector("button.usepod-spend-confirm", timeout=20000)
        assert "You allowed this" in page.inner_text(".usepod-spend-approval")

        # A full page RELOAD between Allow and confirm: the approved-unminted consent must
        # survive it (the state model, not the page, carries the continuation).
        page.reload(wait_until="networkidle")
        page.wait_for_selector("button.usepod-spend-confirm", timeout=20000)
        assert "You allowed this" in page.inner_text(".usepod-spend-approval")
        assert served.fingerprint in page.inner_text(".usepod-spend-approval")

        # Settings confirms; exactly one grant is minted.
        page.click("button.usepod-spend-confirm")
        page.wait_for_function(
            "() => /Enabled:/.test((document.querySelector('.usepod-note') || {textContent:''}).textContent || '')",
            timeout=20000,
        )
        status, grants = daemon.call("GET", "/api/money/grants?active=1")
        assert status == 200 and len(grants["grants"]) == 1, grants
        grant = grants["grants"][0]
        assert grant["spec"]["per_operation_max_atomic"] == "500000"
        assert grant["spec"]["provider_account"] == served.fingerprint
        assert grant["spec"]["approval_ref"] == approval_id

        # ONE paid call runs and settles TRUTHFULLY BOUNDED.
        session_id = _session(f"consented-{uuid.uuid4()}")
        status, answer = _chat(daemon, "Write a short note congratulating a colleague on finishing a marathon.", session_id)
        assert status == 200 and "synthetic reply" in _answer_text(answer), answer
        status, projection = daemon.call("GET", "/api/money/projection?provider_id=usepod")
        buckets = projection["projection"]["inference_expense"]
        assert sum(int(b.get("settled_bounded") or 0) for b in buckets.values()) > 0, buckets
        assert sum(int(b.get("settled_exact") or 0) for b in buckets.values()) == 0, buckets
        _keep("spend_approval_flow.json", {"grant": grant, "projection": projection})

        # The spent single-payment consent refuses the NEXT call: repeat spending needs repeat consent.
        after = len(service.requests_to("/proxy/{token}/v1/chat/completions"))
        session2 = _session(f"spent-{uuid.uuid4()}")
        status, answer = _chat(daemon, "Write a two-line limerick about a cat.", session2)
        assert len(service.requests_to("/proxy/{token}/v1/chat/completions")) == after
        assert "MONEY_AUTHORITY_" in json.dumps(daemon.events(session2))
    finally:
        page.close()


def test_the_grant_and_settlement_survive_restart_and_revoke(served) -> None:
    daemon = served.daemon
    status, before = daemon.call("GET", "/api/money/projection?provider_id=usepod")
    assert status == 200
    daemon.kill()
    daemon.restart()
    status, after = daemon.call("GET", "/api/money/projection?provider_id=usepod")
    assert after["projection"]["liability_states"] == before["projection"]["liability_states"]
    status, grants = daemon.call("GET", "/api/money/grants?active=1")
    grant_id = grants["grants"][0]["grant_id"]
    # Revoke through the existing owner route; the lane refuses afterwards.
    status, revoked = daemon.call("POST", "/api/money/grants/revoke", {"grant_id": grant_id, "reason": "user revoked consent"})
    assert status == 200, revoked
    served_before = len(served.service.requests_to("/proxy/{token}/v1/chat/completions"))
    session_id = _session(f"revoked-{uuid.uuid4()}")
    _chat(daemon, "Write a short farewell note to a coworker.", session_id)
    assert len(served.service.requests_to("/proxy/{token}/v1/chat/completions")) == served_before
    assert "MONEY_AUTHORITY_" in json.dumps(daemon.events(session_id))
    _keep("spend_approval_revoke.json", {"revoked": revoked})


def test_denial_mints_nothing_and_replay_cannot_double_mint(served) -> None:
    daemon = served.daemon
    # A proposal the operator DENIES.
    status, proposal = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 300000, "max_total_atomic": 900000})
    assert status == 200, proposal
    status, _resolved = _resolve_approval(daemon, proposal["approval_id"], "deny")
    assert status == 200
    status, denied = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": proposal["approval_id"]})
    assert status == 403 and "denied" in str(denied.get("error", "")), denied
    # An allowed proposal mints exactly once; replay is idempotent on the SAME grant.
    status, proposal = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 200000, "max_total_atomic": 400000})
    assert status == 200, proposal
    _resolve_approval(daemon, proposal["approval_id"], "allow")
    status, first = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": proposal["approval_id"]})
    assert status == 200 and first.get("ok") is True and first.get("idempotent") is False, first
    status, replay = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": proposal["approval_id"]})
    assert status == 200 and replay.get("grant_id") == first["grant_id"] and replay.get("idempotent") is True, replay
    status, grants = daemon.call("GET", "/api/money/grants?active=1")
    minted = [g for g in grants["grants"] if g["spec"].get("approval_ref") == proposal["approval_id"]]
    assert len(minted) == 1, minted
    # An unknown approval id cannot mint.
    status, unknown = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": "does-not-exist"})
    assert status == 403, unknown
    # Cleanup: revoke the extra grant so later files start clean.
    daemon.call("POST", "/api/money/grants/revoke", {"grant_id": first["grant_id"], "reason": "test cleanup"})
    _keep("spend_approval_controls.json", {"denied": denied, "first": first, "replay": replay})


def test_proposal_refusals_name_their_reason(served) -> None:
    daemon = served.daemon
    status, bad = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 0, "max_total_atomic": 100})
    assert status == 400 and "positive" in str(bad.get("error", "")), bad
    status, bad = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 500, "max_total_atomic": 100})
    assert status == 400 and "cannot exceed" in str(bad.get("error", "")), bad
    status, bad = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 100, "max_total_atomic": 5000, "expiry_epoch": 1})
    assert status == 400 and "future" in str(bad.get("error", "")), bad
    status, bad = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 100, "max_total_atomic": 5000, "extra": 1})
    assert status == 400 and "unknown fields" in str(bad.get("error", "")), bad


def test_the_expired_consent_shows_and_recovers_after_refresh(served) -> None:
    """The two deadline dead-ends, visible and recoverable: an allowed-but-expired consent
    renders its EXPIRED state with the exact facts, survives a page RELOAD, and its only
    recovery is a FRESH approval -- pre-filled ceilings, but a NEW explicit Allow before
    anything can be enabled. The expiry itself matures in real time (a served daemon's clock is
    not patchable from outside); the proposal's window is 3 seconds."""
    import time as _time

    daemon = served.daemon
    # A short-lived consent the operator allows, whose economic window then elapses.
    status, proposal = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 400000, "max_total_atomic": 1200000, "expiry_epoch": _time.time() + 3})
    assert status == 200, proposal
    status, _resolved = _resolve_approval(daemon, proposal["approval_id"], "allow")  # mixed browser/API step, labelled
    assert status == 200
    _time.sleep(4.0)
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert view["spend_approval"]["state"] == "expired", view["spend_approval"]
    # Confirm on the dead consent refuses -- at the bridge's own gate (403) or at the money
    # law's spec validation (400 spend_grant_refused); nothing is enabled either way.
    status, dead = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": proposal["approval_id"]})
    assert status in {400, 403} and "refus" in json.dumps(dead).lower(), dead

    page = _open_panel(served)
    try:
        body_text = page.inner_text(".usepod-spend-approval")
        assert "expired" in body_text.lower()
        assert "0.4 USDC" in body_text and served.fingerprint in body_text
        again = page.query_selector("button.usepod-spend-propose-again")
        assert again is not None, "the expired state must offer the fresh-consent recovery action"
        assert page.query_selector("button.usepod-spend-confirm") is None, "no dead confirm action"

        # The expired state and its recovery survive a full reload.
        page.reload(wait_until="networkidle")
        page.wait_for_selector("button.usepod-spend-propose-again", timeout=20000)
        assert "expired" in page.inner_text(".usepod-spend-approval").lower()

        # Recovery: a FRESH approval (ceilings pre-filled, no authority carried over).
        page.click("button.usepod-spend-propose-again")
        page.wait_for_selector("button.usepod-spend-allow", timeout=20000)
        assert "Awaiting YOUR approval" in page.inner_text(".usepod-spend-approval")
        status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
        fresh_id = view["spend_approval"]["approval_id"]
        assert fresh_id != proposal["approval_id"], "recovery must be a NEW consent, not a renewal"

        # The new explicit operator decision: visible Allow, reload, confirm.
        page.click("button.usepod-spend-allow")
        page.wait_for_selector("button.usepod-spend-confirm", timeout=20000)
        page.reload(wait_until="networkidle")
        page.wait_for_selector("button.usepod-spend-confirm", timeout=20000)
        page.click("button.usepod-spend-confirm")
        page.wait_for_function(
            "() => /Enabled:/.test((document.querySelector('.usepod-note') || {textContent:''}).textContent || '')",
            timeout=20000,
        )
        status, grants = daemon.call("GET", "/api/money/grants?active=1")
        minted = [g for g in grants["grants"] if g["spec"].get("approval_ref") == fresh_id]
        assert len(minted) == 1 and minted[0]["spec"]["per_operation_max_atomic"] == "400000", minted
        # ONE bounded paid call, then the spent consent refuses the next.
        session_id = _session(f"recovered-{uuid.uuid4()}")
        status, answer = _chat(daemon, "Write a one-sentence note thanking a courier.", session_id)
        assert status == 200 and "synthetic reply" in _answer_text(answer), answer
        after = len(served.service.requests_to("/proxy/{token}/v1/chat/completions"))
        session2 = _session(f"recovered-spent-{uuid.uuid4()}")
        _chat(daemon, "Write a one-line note to a librarian.", session2)
        assert len(served.service.requests_to("/proxy/{token}/v1/chat/completions")) == after
        assert "MONEY_AUTHORITY_" in json.dumps(daemon.events(session2))
        # Cleanup for later files.
        daemon.call("POST", "/api/money/grants/revoke", {"grant_id": minted[0]["grant_id"], "reason": "test cleanup"})
    finally:
        page.close()
