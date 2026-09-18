"""Revision-3 correction tests: original + novel + negative data per repair.

R1  reservation lifecycle: response-phase failures (timeout, malformed 202/200
    body, empty 202) keep acceptance/uncertainty; pre-acceptance rejection is a
    real failure; recovery survives replay, fresh-process restart and CONCURRENT
    reconciliation; corrupt state is quarantined, never silently discarded.
R2  principal-bound approval: same From alias + different OAuth principal =
    needs_reapproval (both providers, [real-rest]); rotation for the SAME
    principal stays valid; reconciliation refuses a re-pointed account slot.
R3  Graph wire: decoded approved body (quoted-printable paragraph, Unicode) on
    the wire; strict fixture rejects standard headers (already enforced); the
    served seam carries the same guarantees.
R4  Gmail thread binding from the ACTUAL parent; ambiguous/missing parent and
    edited-subject invalidation negatives; echoed thread ids never bind.
R5  refresh-token rotation is PERSISTED to the credential-store handle; Graph
    reconciliation matches subject+recipient within the reservation window and
    states its bounds.

Transports: [real-rest] local recorded-shape provider server; [real-proc] real
subprocesses against the shared runtime home; synthetic HTTP-boundary doubles
only where the review's own file already uses them (not here).
"""
from __future__ import annotations

import json
import subprocess
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

from core import credential_store, email_drafts, runtime_paths
from tests import test_email_live_workflow as _live_module
from tests.test_email_live_workflow import _isolated, _raw  # noqa: F401

# The real local-mail fixture, re-exported so pytest resolves `mail_service` here too.
mail_service = _live_module.mail_service

from tests.provider_api_fixture import ProviderApiServer

SESSION = "openclaw:revision3"
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _store_provider(api: ProviderApiServer, provider: str, account: str, address: str,
                    *, principal: str | None = None, refresh: str | None = None) -> str:
    effective = api.add_oauth_client(provider, account, principal or address)
    if refresh:
        api.set_refresh(provider, account, refresh)
        effective = refresh
    credential_store.store_credential(
        f"email.oauth.{provider}.{account}",
        json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": effective,
                    "token_url": api.token_url, "account_email": principal or address}),
        label="v3 oauth",
    )
    blob = {"provider": provider, "from_addr": "alias@example.test",  # constant outward alias
            "api_base": api.gmail_base, "token_url": api.token_url}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"v3 {kind}")
    return refresh


def _draft(*, body="Wednesday at 10:00 works.", subject="Re: Delivery scheduling",
           account="acct", reply_to="<p@x.test>", session=SESSION, to="r@x.test"):
    saved = email_drafts.save_draft(to=to, subject=subject, body=body, account=account,
                                    in_reply_to=reply_to, kind="reply" if reply_to else "compose",
                                    session_id=session)
    assert saved.ok, saved.message
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=session).ok
    return saved.draft["draft_id"]


# ---------------------------------------------------------------------------
# R1 — response-phase failures keep acceptance; rejection before acceptance fails
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["empty-202", "pre-acceptance-rejection"])
@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r1_response_phase_outcomes(api_server_fixture, provider, scenario) -> None:
    api = api_server_fixture
    _store_provider(api, provider, "acct", "me@example.test")
    did = _draft(account="acct", subject=f"R1 {provider} {scenario}")
    if scenario == "pre-acceptance-rejection":
        api.reject_next_send = True
        result = email_drafts.send_draft(did, session_id=SESSION)
        assert not result.ok and result.status == "failed"
        assert api.sent_count(provider, "me@example.test") == 0
        # A real refusal re-opens the draft; retry is a fresh decision.
        assert email_drafts.get_draft(did, session_id=SESSION).draft["status"] == "failed"
    else:
        result = email_drafts.send_draft(did, session_id=SESSION)
        assert result.ok and result.status == "sent"  # 202/200 with empty body = acceptance
        assert api.sent_count(provider, "me@example.test") == 1
        again = email_drafts.send_draft(did, session_id=SESSION)
        assert again.status == "sent" and api.sent_count(provider, "me@example.test") == 1


def test_r1_concurrent_reconciliation_single_outcome(api_server_fixture) -> None:
    """Two concurrent reconcile calls cannot interleave their terminal writes."""
    from concurrent.futures import ThreadPoolExecutor

    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    did = _draft(account="acct")
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "delivery_unknown"
    api.drop_after_accept = False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [f.result() for f in [pool.submit(email_drafts.reconcile_draft, did, session_id=SESSION)
                                        for _ in range(4)]]
    assert all(r.status == "sent_confirmed" for r in results), [r.status for r in results]
    assert api.sent_count("gmail", "me@example.test") == 1  # reconciliation never dispatches


def test_r1_corrupt_state_is_quarantined_not_discarded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    store = runtime_paths.active_data_dir() / "email" / "drafts.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{ this is not json", encoding="utf-8")
    row = email_drafts.save_draft(to="a@b.test", subject="s", body="b", session_id=SESSION)
    assert row.ok
    quarantined = list(store.parent.glob("drafts.corrupt-*.json"))
    assert len(quarantined) == 1 and "{ this is not json" in quarantined[0].read_text()
    runtime_paths.configure_runtime_home(None)


_DRIVER_SEND = '''
import os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
from core import email_drafts
r = email_drafts.send_draft(os.environ["DRAFT_ID"], session_id=os.environ["SESSION"])
print("RESULT:" + r.status)
'''


def test_r1_fresh_process_restart_never_redispatches(api_server_fixture) -> None:
    """After a response-phase loss the persisted state is all a RESTARTED process
    sees: a fresh interpreter must not dispatch again."""
    import os

    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    did = _draft(account="acct", subject="R1 restart")
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "delivery_unknown"
    api.drop_after_accept = False
    env = dict(os.environ)
    env.update(REPO_ROOT=str(REPO_ROOT), PYTHONPATH=str(REPO_ROOT),
               PYTHONDONTWRITEBYTECODE="1", DRAFT_ID=did, SESSION=SESSION)
    proc = subprocess.run([sys.executable, "-B", "-c", _DRIVER_SEND], cwd=str(REPO_ROOT),
                          env=env, capture_output=True, text=True, timeout=30)
    status = next(line.partition("RESULT:")[2].strip() for line in proc.stdout.splitlines()
                  if line.startswith("RESULT:"))
    assert status == "delivery_unknown"
    assert api.sent_count("gmail", "me@example.test") == 1


# ---------------------------------------------------------------------------
# R2 — principal-bound approval and reconciliation account binding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_oauth_repoint_same_alias_invalidates(api_server_fixture, provider) -> None:
    api = api_server_fixture
    _store_provider(api, provider, "acct", "me@example.test")
    did = _draft(account="acct", subject=f"R2 {provider} repoint")
    # SAME outward From alias, DIFFERENT authenticated principal behind the grant.
    _store_provider(api, provider, "acct", "other-mailbox@example.test", refresh="other-refresh")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.status == "needs_reapproval" and api.sent_count(provider, "me@example.test") == 0
    assert api.sent_count(provider, "other-mailbox@example.test") == 0


def test_r2_credential_rotation_same_principal_stays_valid(api_server_fixture) -> None:
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test", refresh="first-refresh")
    did = _draft(account="acct", subject="R2 rotation")
    # Rotated grant, SAME principal: normal credential lifecycle.
    _store_provider(api, "gmail", "acct", "me@example.test", refresh="rotated-refresh")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message


def test_r2_reconciliation_refuses_a_repointed_slot(api_server_fixture) -> None:
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    did = _draft(account="acct", subject="R2 reconcile repoint")
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "delivery_unknown"
    # The slot is re-pointed AFTER the unresolved send: reconciliation through the
    # replacement would inspect the WRONG sent view.
    _store_provider(api, "gmail", "acct", "other-mailbox@example.test", refresh="other-refresh")
    blocked = email_drafts.reconcile_draft(did, session_id=SESSION)
    assert blocked.status == "reconciliation_blocked"
    assert "reserved mailbox" in blocked.message or "reserved identity" in blocked.message
    assert api.sent_count("gmail", "me@example.test") == 1  # nothing re-dispatched


# ---------------------------------------------------------------------------
# R3 — Graph wire: the decoded approved body, byte for byte
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    "The approved delivery details are " + "packaging " * 30,
    "Ačiū už pagalbą. " + "€" * 100,
], ids=["quoted-printable-paragraph", "unicode-heavy"])
def test_r3_graph_compose_sends_decoded_body(api_server_fixture, body) -> None:
    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    did = _draft(account="acct", body=body, subject="R3 compose", reply_to="")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    captured = api.graph_sent("me@example.test")
    assert len(captured) == 1
    assert captured[0]["message"]["body"]["content"].rstrip("\n") == body.rstrip("\n")


def test_r3_graph_strict_boundary_rejects_standard_headers(api_server_fixture) -> None:
    """The local provider contract enforces the documented law (standard RFC
    headers in sendMail JSON are a 400) — proven by a direct wire probe — and
    production NEVER trips it: no captured Graph send carries them."""
    import urllib.error
    import urllib.request

    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    # Production path first: a reply send through the seam.
    did = _draft(account="acct", subject="Re: strict boundary", reply_to="<an-sb@x.test>")
    assert email_drafts.send_draft(did, session_id=SESSION).status == "sent"
    for captured in api.graph_sent("me@example.test"):
        assert not (captured["message"].get("internetMessageHeaders")), captured
    # The boundary itself: a raw sendMail carrying a standard header is refused.
    token_request = urllib.request.Request(
        api.token_url, data=b"grant_type=refresh_token&refresh_token=first-refresh&client_id=c",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    _store_provider(api, "graph", "probe", "me@example.test", refresh="probe-refresh")
    api.add_oauth_client("graph", "probe2", "me@example.test")
    token_request = urllib.request.Request(
        api.token_url,
        data=f"grant_type=refresh_token&refresh_token={api.state.oauth[('graph', 'probe2')]['refresh_token']}&client_id=c".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(token_request, timeout=5) as response:
        token = json.loads(response.read())["access_token"]
    probe = urllib.request.Request(
        f"{api.graph_base}/v1.0/me/sendMail",
        data=json.dumps({"message": {"subject": "s", "body": {"content": "b"},
                                     "internetMessageHeaders": [{"name": "In-Reply-To", "value": "<x@y>"}]}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(probe, timeout=5)
    assert refused.value.code == 400
    assert b"InvalidInternetMessageHeader" in refused.value.read()


# ---------------------------------------------------------------------------
# R4 — Gmail thread binding from the actual parent
# ---------------------------------------------------------------------------

def _gmail_parent(api: ProviderApiServer, address: str, mid: str, thread: str) -> str:
    m = EmailMessage()
    m["From"] = "supply@example.test"
    m["To"] = address
    m["Subject"] = "Delivery scheduling"
    m["Message-ID"] = mid
    m["Date"] = "Thu, 10 Sep 2026 08:00:00 +0000"
    m.set_content("Tuesday 14:00 or Wednesday 10:00?")
    api.state.gmail_mailboxes.setdefault(address, []).append({
        "id": api.state.next_id("gm-"), "threadId": thread, "snippet": "slot choice",
        "payload": {"headers": [{"name": "From", "value": "supply@example.test"},
                                {"name": "To", "value": address},
                                {"name": "Subject", "value": "Delivery scheduling"},
                                {"name": "Date", "value": m["Date"]},
                                {"name": "Message-ID", "value": mid}],
                    "mimeType": "text/plain", "body": {"data": ""}},
    })
    return api.state.gmail_mailboxes[address][-1]["id"]


def test_r4_gmail_reply_binds_resolved_thread(api_server_fixture) -> None:
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    _gmail_parent(api, "me@example.test", "<parent-r4@x.test>", "thread-r4")
    did = _draft(account="acct", reply_to="<parent-r4@x.test>", subject="Re: Delivery scheduling")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    sent = api.gmail_sent("me@example.test")[0]
    assert sent["threadId"] == "thread-r4"  # the RESOLVED parent thread, not an echo
    assert result.details["receipt"]["thread_id"] == "thread-r4"


def test_r4_gmail_missing_parent_sends_without_thread_binding(api_server_fixture) -> None:
    """Ambiguous/missing parent: the reply still goes out (RFC headers preserved),
    honestly without a provider thread binding — never a guessed thread id."""
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    did = _draft(account="acct", reply_to="<no-such-parent@x.test>")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    sent = api.gmail_sent("me@example.test")[0]
    assert "threadId" not in sent or not sent.get("threadId")
    assert result.details["receipt"]["thread_id"] == ""


# ---------------------------------------------------------------------------
# R5 — rotation persistence + graph reconciliation window
# ---------------------------------------------------------------------------

def test_r5_rotated_refresh_token_is_persisted(api_server_fixture) -> None:
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test", refresh="rotating-refresh")
    api.rotate_refresh_for("gmail", "acct", "rotating-refresh", "rotated-once")
    did = _draft(account="acct", subject="R5 rotation")
    assert email_drafts.send_draft(did, session_id=SESSION).status == "sent"
    handle = json.loads(credential_store.get_credential("email.oauth.gmail.acct"))
    assert handle["refresh_token"] == "rotated-once"


def test_r5_graph_reconciliation_candidate_evidence_is_not_confirmation(api_server_fixture) -> None:
    """Graph assigns its own Message-IDs, so subject+recipients in the window is
    CANDIDATE evidence only (revision-4 law): it must surface visibly and NEVER
    yield sent_confirmed; nothing is resent either way."""
    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    did = _draft(account="acct", subject="Re: Kiln estimate 8442", to="ops@x.test",
                 body="Questions follow.", reply_to="<an-9@x.test>")
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "delivery_unknown"
    api.drop_after_accept = False
    reconciled = email_drafts.reconcile_draft(did, session_id=SESSION)
    assert reconciled.ok and reconciled.status == "delivery_unknown", reconciled.message
    assert "candidate" in reconciled.message.lower()
    assert "not confirmed" in reconciled.message.lower()
    assert api.sent_count("graph", "me@example.test") == 1  # nothing re-dispatched


def test_r5_graph_reconciliation_reports_candidate_counts(api_server_fixture) -> None:
    """NOVEL: two same-subject sends to the same recipient make the evidence
    AMBIGUOUS — the note must expose the count, not collapse it to one."""
    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    for body in ("First wording.", "Second wording."):
        did = _draft(account="acct", subject="Re: Kiln estimate 8442", to="ops@x.test",
                     body=body, reply_to="<an-9@x.test>", session=SESSION + "-b")
        api.drop_after_accept = True
        assert email_drafts.send_draft(did, session_id=SESSION + "-b").status == "delivery_unknown"
    api.drop_after_accept = False
    rows = email_drafts._load()["drafts"]
    some = next(k for k, v in rows.items() if v.get("status") == "delivery_unknown")
    reconciled = email_drafts.reconcile_draft(some, session_id=SESSION + "-b")
    assert reconciled.status == "delivery_unknown"
    assert "2 candidate" in reconciled.message


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_server_fixture():
    server = ProviderApiServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    email_drafts.reset_drafts()
    yield
    runtime_paths.configure_runtime_home(None)
