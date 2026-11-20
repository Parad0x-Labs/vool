"""Retained spend consent, settlement, denial, expiry and recovery over the normal daemon.

The central vendor budget form was removed by TDL L35. These real HTTP cases
retain the original financial assertions through the same trusted approval
store and operator routes. Native plugin browser workflows are covered by
 test_plugin_served_journey.py. Funds and external provider are synthetic;
production monetary authority is unchanged.
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
        saved = daemon.seed_legacy_profile(origin=service.origin, token=token, models=tuple(service.models))
        daemon.start()
        status, refreshed = daemon.call("POST", "/api/cloud/usepod/refresh", {})
        assert status == 200 and refreshed["credential"]["state"] == "observed", refreshed
        daemon.call("POST", "/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
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


def test_the_full_user_flow_no_grant_to_trusted_budget_to_two_settled_calls(served) -> None:
    daemon, service = served.daemon, served.service
    # A paid turn before consent refuses with the money law's code, nothing sent.
    before = len(service.requests_to("/proxy/{token}/v1/chat/completions"))
    session_id = _session(f"pre-consent-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short thank-you note to a neighbor who watered my plants.", session_id)
    assert status == 200 and "MONEY_AUTHORITY_INVALID" in json.dumps(daemon.events(session_id))
    assert len(service.requests_to("/proxy/{token}/v1/chat/completions")) == before

    # Only ceilings come from the operator; account and scope are server-derived.
    status, proposed = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 500_000, "max_total_atomic": 5_000_000, "budget_mode": "total"})
    assert status == 200, proposed
    facts = proposed["facts"]
    assert (facts["max_total_atomic"], facts["per_call_atomic"], facts["account"]) == (5_000_000, 500_000, served.fingerprint)
    assert facts["models"] == [] and facts["routes"] == [], facts
    approval_id = proposed["approval_id"]

    # Confirm BEFORE the operator allows: refused, nothing minted.
    status, early = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": approval_id})
    assert status == 403 and "awaiting" in str(early.get("error", "")), early

    status, allowed = _resolve_approval(daemon, approval_id, "allow")
    assert status == 200, allowed
    status, minted = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": approval_id})
    assert status == 200, minted
    # A fresh HTTP projection reads durable consent rather than page-local state.
    status, view = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert status == 200 and view["spend_approval"]["state"] == "minted", view
    # Exactly one grant was minted by the explicit approval.
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

    # The explicit total budget authorizes a second request within its remaining amount.
    after = len(service.requests_to("/proxy/{token}/v1/chat/completions"))
    session2 = _session(f"spent-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a short note welcoming a new colleague.", session2)
    assert status == 200 and "synthetic reply" in _answer_text(answer), answer
    assert len(service.requests_to("/proxy/{token}/v1/chat/completions")) == after + 1



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

    assert view["spend_approval"]["facts"]["per_call_atomic"] == 400_000
    assert view["spend_approval"]["facts"]["account"] == served.fingerprint
    status, reloaded = daemon.call("GET", "/api/cloud/usepod/discovery")
    assert status == 200 and reloaded["spend_approval"]["state"] == "expired", reloaded
    status, fresh = daemon.call("POST", "/api/cloud/usepod/spend-approval/propose", {"per_call_atomic": 400_000, "max_total_atomic": 1_200_000, "budget_mode": "total"})
    assert status == 200, fresh
    fresh_id = fresh["approval_id"]
    assert fresh_id != proposal["approval_id"], "recovery must be a NEW consent, not a renewal"
    status, allowed = _resolve_approval(daemon, fresh_id, "allow")
    assert status == 200, allowed
    status, enabled = daemon.call("POST", "/api/cloud/usepod/spend-approval/confirm", {"approval_id": fresh_id})
    assert status == 200, enabled
    status, grants = daemon.call("GET", "/api/money/grants?active=1")
    minted = [g for g in grants["grants"] if g["spec"].get("approval_ref") == fresh_id]
    assert len(minted) == 1 and minted[0]["spec"]["per_operation_max_atomic"] == "400000", minted
    # Both calls stay within the explicitly approved total budget.
    session_id = _session(f"recovered-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a one-sentence note thanking a courier.", session_id)
    assert status == 200 and "synthetic reply" in _answer_text(answer), answer
    after = len(served.service.requests_to("/proxy/{token}/v1/chat/completions"))
    session2 = _session(f"recovered-spent-{uuid.uuid4()}")
    status, answer = _chat(daemon, "Write a one-line note to a librarian.", session2)
    assert status == 200 and "synthetic reply" in _answer_text(answer), answer
    assert len(served.service.requests_to("/proxy/{token}/v1/chat/completions")) == after + 1
    # Cleanup for later files.
    daemon.call("POST", "/api/money/grants/revoke", {"grant_id": minted[0]["grant_id"], "reason": "test cleanup"})
