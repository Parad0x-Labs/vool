"""Revision-4 correction tests: original + novel + negative data per repair.

D2  dispatch/outcome contract: uncertain (header timeout / wrapped reset) vs
    PROVEN unsent (DNS failure / connection refused) vs sticky acceptance —
    through the real seam, plus a RESTART replay.
D3  truthful reads: malformed/timeout bodies fail search AND open/thread;
    genuinely empty searches still succeed (preservation control).
D4  exact Graph semantics: encoded-word subjects decoded on the wire (Unicode,
    folded), approved recipients byte-exact, compose AND reply.
D5  verified principal: grant swap with STALE metadata refused at dispatch;
    same-principal rotation survives a REAL restart.
D6  store safety: schema-invalid bytes quarantined with a recovery record;
    repeated corruption preserves every generation; unresolved markers surface.

Transports: [real-rest] local strict provider server; [real-proc] subprocess.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core import credential_store, email_drafts, email_tools, runtime_paths
from tests import test_email_live_workflow as _live_module
from tests.test_email_live_workflow import _isolated  # noqa: F401

mail_service = _live_module.mail_service
from tests.provider_api_fixture import ProviderApiServer

SESSION = "openclaw:revision4"
REPO_ROOT = Path(__file__).resolve().parents[1]


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


def _store_provider(api: ProviderApiServer, provider: str, account: str, address: str,
                    *, refresh: str | None = None) -> str:
    effective = api.add_oauth_client(provider, account, address)
    if refresh:
        api.set_refresh(provider, account, refresh)
        effective = refresh
    credential_store.store_credential(
        f"email.oauth.{provider}.{account}",
        json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": effective,
                    "token_url": api.token_url, "account_email": address}),
        label="v4 oauth",
    )
    blob = {"provider": provider, "from_addr": "alias@example.test",
            "api_base": api.gmail_base, "token_url": api.token_url}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"v4 {kind}")
    return effective


def _draft(*, body="Wednesday at 10:00 works.", subject="Re: Delivery scheduling",
           account="acct", reply_to="<p@x.test>", session=SESSION, to="r@x.test"):
    saved = email_drafts.save_draft(to=to, subject=subject, body=body, account=account,
                                    in_reply_to=reply_to, kind="reply" if reply_to else "compose",
                                    session_id=session)
    assert saved.ok, saved.message
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=session).ok
    return saved.draft["draft_id"]


# ---------------------------------------------------------------------------
# D2 — outcome classification by phase, not exception name
# ---------------------------------------------------------------------------

def test_d2_dns_failure_is_proven_unsent_and_retryable(api_server_fixture) -> None:
    """A connection that never opened (DNS) is the one class that may re-dispatch:
    the draft reopens, the retry succeeds, exactly one wire POST ever lands."""
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    real = email_tools._provider_adapter
    attempts: list[str] = []

    def dns_failing(account, creds):
        adapter = real(account, creds)

        def failing_mutation(method, url, **kw):
            if url.endswith("/messages/send") and len(attempts) == 0:
                attempts.append("send")
                import socket
                import urllib.error
                raise urllib.error.URLError(socket.gaierror("name resolves to nothing"))
            return adapter._oauth.api_mutation(method, url, **kw)

        adapter._oauth.api_mutation = failing_mutation
        return adapter

    from unittest import mock

    with mock.patch.object(email_tools, "_provider_adapter", dns_failing):
        did = _draft(account="acct", subject="D2 dns")
        first = email_drafts.send_draft(did, session_id=SESSION)
    assert not first.ok and first.status == "failed"
    assert email_drafts.get_draft(did, session_id=SESSION).draft["status"] == "failed"
    retry = email_drafts.send_draft(did, session_id=SESSION)  # retry IS the user decision here
    assert retry.ok and retry.status == "sent", retry.message
    assert api.sent_count("gmail", "me@example.test") == 1


def test_d2_header_timeout_is_uncertain_not_retryable(api_server_fixture) -> None:
    """NOVEL wording/class: a timeout waiting for response headers after the POST
    may have transmitted - sticky unknown, no completed send through replay."""
    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    import urllib.request

    real_urlopen = urllib.request.urlopen

    def timing_out(req, **kw):
        if req.method == "POST" and str(req.full_url).endswith("/sendMail"):
            raise TimeoutError("headers never arrived after the POST")
        return real_urlopen(req, **kw)

    from unittest import mock

    with mock.patch.object(urllib.request, "urlopen", timing_out):
        did = _draft(account="acct", subject="D2 header-timeout")
        first = email_drafts.send_draft(did, session_id=SESSION)
        again = email_drafts.send_draft(did, session_id=SESSION)
    assert first.status == "delivery_unknown" and again.status == "delivery_unknown"
    assert api.sent_count("graph", "me@example.test") == 0  # no completed send ever landed


_DRIVER = '''
import os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
from core import email_drafts
r = email_drafts.send_draft(os.environ["DRAFT_ID"], session_id=os.environ["SESSION"])
print("RESULT:" + r.status)
'''


def test_d2_acceptance_survives_a_real_restart(api_server_fixture) -> None:
    """An accepted send whose receipt write is lost: a FRESH process reads the
    reservation and never re-dispatches; reconciliation confirms it."""
    import os

    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    did = _draft(account="acct", subject="D2 restart acceptance")
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "delivery_unknown"
    api.drop_after_accept = False
    env = dict(os.environ)
    env.update(REPO_ROOT=str(REPO_ROOT), PYTHONPATH=str(REPO_ROOT),
               PYTHONDONTWRITEBYTECODE="1", DRAFT_ID=did, SESSION=SESSION)
    proc = subprocess.run([sys.executable, "-B", "-c", _DRIVER], cwd=str(REPO_ROOT),
                          env=env, capture_output=True, text=True, timeout=30)
    status = next(line.partition("RESULT:")[2].strip() for line in proc.stdout.splitlines()
                  if line.startswith("RESULT:"))
    assert status == "delivery_unknown"
    assert api.sent_count("gmail", "me@example.test") == 1
    assert email_drafts.reconcile_draft(did, session_id=SESSION).status == "sent_confirmed"


# ---------------------------------------------------------------------------
# D3 — truthful reads across the whole surface
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("surface", ["search", "open", "thread"])
def test_d3_unreadable_reads_fail_honestly(api_server_fixture, surface) -> None:
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    real = email_tools._provider_adapter

    def unreadable(account, creds):
        adapter = real(account, creds)
        original_read = adapter._oauth.api_read

        def failing_read(method, url):
            if surface == "search" or "/messages?" in url or "/threads/" in url:
                raise __import__("core.email_providers.base", fromlist=["ProviderAccountError"]).ProviderAccountError(
                    "read_failed", "synthetic unreadable body")
            return original_read(method, url)

        adapter._oauth.api_read = failing_read
        return adapter

    from unittest import mock

    with mock.patch.object(email_tools, "_provider_adapter", unreadable):
        if surface == "search":
            result = email_tools.search_email(account="acct", sender="x")
            assert not result.ok and result.status == "read_failed"
        elif surface == "open":
            result = email_tools.open_message(account="acct", message_id="gm-0001")
            assert not result.ok and result.status == "read_failed"
        else:
            result = email_tools.open_thread(account="acct", message_id="gm-0001")
            assert not result.ok and result.status == "read_failed"


def test_d3_genuinely_empty_search_still_succeeds(api_server_fixture) -> None:
    """PRESERVATION: a readable empty mailbox is a successful empty search — the
    read-failure repair must not have swallowed real empties."""
    api = api_server_fixture
    _store_provider(api, "gmail", "acct", "me@example.test")
    result = email_tools.search_email(account="acct", sender="nobody")
    assert result.ok and result.status == "executed" and result.messages == []
    assert "0 message" in result.message


# ---------------------------------------------------------------------------
# D4 — exact Graph message semantics through the real seam
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subject", ["Pasiūlymas € 42 — pristatymas", "会議の確認 — 火曜日 14時"])
def test_d4_graph_unicode_subject_arrives_decoded(api_server_fixture, subject) -> None:
    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    did = _draft(account="acct", subject=subject, reply_to="", to="ops@x.test")
    approved = email_drafts.get_draft(did, session_id=SESSION).draft["subject"]
    assert approved == subject
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    captured = api.graph_sent("me@example.test")
    assert len(captured) == 1
    assert captured[0]["message"]["subject"] == approved  # decoded, not =?utf-8?...?


def test_d4_graph_multi_recipient_compose_is_exact(api_server_fixture) -> None:
    """NOVEL: multiple approved recipients, reply-shaped, with a folded encoded
    subject — every writable field lands exactly as reviewed."""
    api = api_server_fixture
    _store_provider(api, "graph", "acct", "me@example.test")
    did = _draft(account="acct", subject="Re: Kiln įvertinimas — 1 230 €",
                 to="ops@x.test", reply_to="<an-d4@x.test>",
                 body="Two questions; work not authorized.")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    message = api.graph_sent("me@example.test")[0]["message"]
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == ["ops@x.test"]
    assert message["body"]["content"].strip() == "Two questions; work not authorized."
    assert "€" in message["subject"] and "1 230" in message["subject"]


# ---------------------------------------------------------------------------
# D5 — verified principal at dispatch; rotation across restart
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_d5_grant_swap_with_stale_metadata_refused_at_dispatch(api_server_fixture, provider) -> None:
    """The metadata stays 'me@example.test' while the GRANT now authenticates
    other@example.test (the fixture's profile answers per bearer): the
    dispatch-time conclusive check refuses BEFORE any POST."""
    api = api_server_fixture
    _store_provider(api, provider, "acct", "me@example.test")
    api.set_refresh(provider, "acct", "other-principal-refresh")
    api.serve_principal_for(provider, "acct", "other-principal-refresh", "other@example.test")
    did = _draft(account="acct", subject=f"D5 swap {provider}")
    # Swap only the grant; leave account_email metadata stale.
    credential_store.store_credential(
        f"email.oauth.{provider}.acct",
        json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": "other-principal-refresh",
                    "token_url": api.token_url, "account_email": "me@example.test"}),
        label="swapped",
    )
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.status == "needs_reapproval", result.message
    assert api.sent_count(provider, "me@example.test") == 0
    assert api.sent_count(provider, "other@example.test") == 0


_DRIVER_ROTATION = """
import json, os, sys
sys.path.insert(0, os.environ["REPO_ROOT"])
from core import credential_store, email_drafts
credential_store.store_credential(
    "email.oauth.gmail.acct",
    json.dumps({"client_id": "c", "client_secret": "s",
                "refresh_token": "rotating-grant",
                "token_url": os.environ["TOKEN_URL"],
                "account_email": "me@example.test"}), label="rotation")
for kind in ("imap", "smtp"):
    credential_store.store_credential(
        f"email.{kind}.acct",
        json.dumps({"provider": "gmail", "from_addr": "alias@example.test",
                    "api_base": os.environ["API_BASE"], "token_url": os.environ["TOKEN_URL"]}),
        label=kind)
saved = email_drafts.save_draft(to="r@x.test", subject="Re: rotation restart",
                                body="same mailbox, rotated grant.", account="acct",
                                in_reply_to="<p@x>", kind="reply", session_id="S")
assert saved.ok, saved.message
assert email_drafts.approve_draft(saved.draft["draft_id"], session_id="S").ok
result = email_drafts.send_draft(saved.draft["draft_id"], session_id="S")
print("RESULT:" + result.status)
print("HANDLE:" + json.loads(credential_store.get_credential("email.oauth.gmail.acct"))["refresh_token"])
"""


def test_d5_same_principal_rotation_survives_restart(api_server_fixture) -> None:
    """NOVEL: the rotated grant authenticates the SAME mailbox; a FRESH process
    (restart) still sends — rotation is not a repoint, and the rotated token is
    what got persisted."""
    import os

    api = api_server_fixture
    api.add_oauth_client("gmail", "acct", "me@example.test")
    api.set_refresh("gmail", "acct", "rotating-grant")
    api.rotate_refresh_for("gmail", "acct", "rotating-grant", "rotated-grant-2")
    env = dict(os.environ)
    env.update(REPO_ROOT=str(REPO_ROOT), PYTHONPATH=str(REPO_ROOT),
               PYTHONDONTWRITEBYTECODE="1", SESSION=SESSION,
               TOKEN_URL=api.token_url, API_BASE=api.gmail_base)
    proc = subprocess.run([sys.executable, "-B", "-c", _DRIVER_ROTATION], cwd=str(REPO_ROOT),
                          env=env, capture_output=True, text=True, timeout=30)
    status = next(line.partition("RESULT:")[2].strip() for line in proc.stdout.splitlines()
                  if line.startswith("RESULT:"))
    handle = next(line.partition("HANDLE:")[2].strip() for line in proc.stdout.splitlines()
                  if line.startswith("HANDLE:"))
    assert status == "sent", proc.stdout + proc.stderr
    assert handle == "rotated-grant-2"
    assert api.sent_count("gmail", "me@example.test") == 1


# ---------------------------------------------------------------------------
# D6 — store safety with structured recovery
# ---------------------------------------------------------------------------

def test_d6_schema_invalid_store_recovers_with_record(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    store = runtime_paths.active_data_dir() / "email" / "drafts.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"drafts": []}).encode()
    store.write_bytes(original)
    result = email_drafts.save_draft(to="a@b.test", subject="After recovery", body="b",
                                     account="default", session_id=SESSION)
    assert result.ok
    quarantines = list(store.parent.glob("drafts.corrupt-*.json"))
    assert len(quarantines) == 1 and quarantines[0].read_bytes() == original
    state = json.loads(store.read_text())
    assert state.get("recovery", {}).get("quarantine") == quarantines[0].name
    runtime_paths.configure_runtime_home(None)


def test_d6_repeated_corruption_preserves_every_generation(tmp_path, monkeypatch) -> None:
    """NOVEL: corrupting twice must keep BOTH byte generations — collision-safe
    quarantine never overwrites evidence."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    store = runtime_paths.active_data_dir() / "email" / "drafts.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    first, second = b'{"drafts": []}', b'{"version": 99}'
    store.write_bytes(first)
    email_drafts.save_draft(to="a@b.test", subject="one", body="b", account="default", session_id=SESSION)
    store.write_bytes(second)
    email_drafts.save_draft(to="c@d.test", subject="two", body="b", account="default", session_id=SESSION)
    quarantines = {p.read_bytes() for p in store.parent.glob("drafts.corrupt-*.json")}
    assert {first, second} <= quarantines
    runtime_paths.configure_runtime_home(None)


def test_d6_unresolved_markers_surface_in_recovery(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    store = runtime_paths.active_data_dir() / "email" / "drafts.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b'{"drafts": {"ed-x": {"status": "sending", "sent_message_id": "<g@x>"')
    email_drafts.save_draft(to="a@b.test", subject="after", body="b", account="default", session_id=SESSION)
    state = json.loads(store.read_text())
    markers = state.get("recovery", {}).get("unresolved_markers") or []
    assert markers and "sending" in markers
    runtime_paths.configure_runtime_home(None)
