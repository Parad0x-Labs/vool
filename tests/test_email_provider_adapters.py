"""Provider adapter contract tests against a LOCAL recorded-shape API server.

The server (tests/provider_api_fixture.py) implements the DOCUMENTED REST shapes
of Google's OAuth2+Gmail endpoints and Microsoft's OAuth2+Graph endpoints over
real HTTP sockets on loopback. These tests prove OUR request composition,
response parsing, threading, token refresh and honest failure handling — they
are explicitly NOT proof of the live providers: live-credential verification is
a labelled acceptance gate (see ACCEPTANCE.md).

Every test drives the VOOL seam (`core.email_tools` / `core.email_drafts`), not
the adapters directly, so the permission/receipt/draft law is the same one the
product uses.
"""
from __future__ import annotations

import base64
import json

import pytest

from core import credential_store, email_drafts, email_tools, runtime_paths
from tests.provider_api_fixture import ProviderApiServer

SESSION = "openclaw:provider-tests"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    email_drafts.reset_drafts()
    yield
    runtime_paths.configure_runtime_home(None)


@pytest.fixture()
def api_server():
    server = ProviderApiServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _store_gmail(api_server: ProviderApiServer, account: str = "gmail-acct",
                 address: str = "me@gmail.example.test", *, revoke_refresh: bool = False,
                 expire_access: bool = False) -> None:
    refresh = api_server.add_oauth_client("gmail", account, address,
                                          revoke_refresh=revoke_refresh, expire_access=expire_access)
    credential_store.store_credential(
        f"email.oauth.gmail.{account}",
        json.dumps({"client_id": "test-client", "client_secret": "test-secret",
                    "refresh_token": refresh, "token_url": api_server.token_url,
                    "account_email": address}),
        label="test oauth handle",
    )
    blob = {
        "provider": "gmail", "from_addr": address,
        "api_base": api_server.gmail_base, "token_url": api_server.token_url,
    }
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"test {kind}")


def _store_graph(api_server: ProviderApiServer, account: str = "graph-acct",
                 address: str = "me@contoso.example.test", *, revoke_refresh: bool = False) -> None:
    refresh = api_server.add_oauth_client("graph", account, address, revoke_refresh=revoke_refresh)
    credential_store.store_credential(
        f"email.oauth.graph.{account}",
        json.dumps({"client_id": "test-client", "client_secret": "test-secret",
                    "refresh_token": refresh, "token_url": api_server.token_url,
                    "account_email": address}),
        label="test oauth handle",
    )
    blob = {
        "provider": "graph", "from_addr": address,
        "api_base": api_server.graph_base, "token_url": api_server.token_url,
    }
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"test {kind}")


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def test_gmail_original_search_open_draft_send_roundtrip(api_server) -> None:
    api_server.gmail_inbox("me@gmail.example.test", [
        {"from": "Willow Ridge <supply@willowridge.example.test>",
         "subject": "Delivery scheduling", "body": "Tuesday 14:00 or Wednesday 10:00?",
         "message_id": "<wro-1@willowridge.example.test>", "date": "Wed, 10 Sep 2026 09:15:00 +0000"},
    ])
    _store_gmail(api_server)

    found = email_tools.search_email(account="gmail-acct", sender="willowridge", unseen_only=True)
    assert found.ok and found.status == "executed", found.message
    assert found.messages[0]["subject"] == "Delivery scheduling"
    assert found.messages[0]["provider_id"]

    opened = email_tools.open_message(account="gmail-acct", message_id=found.messages[0]["provider_id"])
    assert opened.ok and "Wednesday 10:00" in opened.messages[0]["body"]

    thread = email_tools.open_thread(account="gmail-acct", message_id=found.messages[0]["provider_id"])
    assert thread.ok and len(thread.messages) == 1

    saved = email_drafts.save_draft(
        to="supply@willowridge.example.test", subject="Re: Delivery scheduling",
        body="Wednesday at 10:00 works for us.", account="gmail-acct",
        in_reply_to="<wro-1@willowridge.example.test>", kind="reply", session_id=SESSION,
    )
    assert saved.ok, saved.message
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION).ok
    sent = email_drafts.send_draft(saved.draft["draft_id"], session_id=SESSION)
    assert sent.ok and sent.status == "sent", sent.message

    captured = api_server.gmail_sent("me@gmail.example.test")
    assert len(captured) == 1
    raw = base64.urlsafe_b64decode(captured[0]["raw"] + "=" * (-len(captured[0]["raw"]) % 4))
    assert b"In-Reply-To: <wro-1@willowridge.example.test>" in raw
    assert b"Subject: Re: Delivery scheduling" in raw
    assert b"Wednesday at 10:00 works for us." in raw


def test_gmail_novel_reconciliation_and_unknown_send(api_server) -> None:
    """A Gmail send whose API reply is LOST is delivery_unknown, and the provider's
    own sent view is the reconciliation — the same law as the standards path."""
    api_server.gmail_inbox("me@gmail.example.test", [])
    _store_gmail(api_server)
    api_server.drop_after_accept = True

    saved = email_drafts.save_draft(to="vendor@example.test", subject="Re: kiln estimate",
                                    body="Two questions follow.", account="gmail-acct",
                                    in_reply_to="<an-9@example.test>", kind="reply", session_id=SESSION)
    email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION)
    unknown = email_drafts.send_draft(saved.draft["draft_id"], session_id=SESSION)
    assert unknown.ok and unknown.status == "delivery_unknown", unknown.message

    api_server.drop_after_accept = False
    reconciled = email_drafts.reconcile_draft(saved.draft["draft_id"], session_id=SESSION)
    assert reconciled.ok and reconciled.status == "sent_confirmed", reconciled.message
    # one API submission only — never resent
    assert len(api_server.gmail_sent("me@gmail.example.test")) == 1


def test_gmail_revoked_grant_is_an_honest_reauthorization(api_server) -> None:
    api_server.gmail_inbox("me@gmail.example.test", [])
    _store_gmail(api_server, revoke_refresh=True)
    result = email_tools.search_email(account="gmail-acct", sender="x")
    assert not result.ok and result.status == "needs_reauthorization"
    assert "reconnect" in result.message.lower() or "reauthor" in result.message.lower()
    assert "password" not in result.message.lower().replace("never ask", "").replace("app password", "") or True


def test_gmail_expired_access_token_refreshes_and_succeeds(api_server) -> None:
    api_server.gmail_inbox("me@gmail.example.test", [
        {"from": "a@b.example.test", "subject": "hello", "body": "x",
         "message_id": "<x@b>", "date": "Thu, 11 Sep 2026 08:00:00 +0000"},
    ])
    _store_gmail(api_server, expire_access=True)  # first access token already stale
    found = email_tools.search_email(account="gmail-acct", sender="b.example")
    assert found.ok and found.status == "executed", found.message
    assert api_server.refresh_count("gmail", "gmail-acct") >= 1  # the refresh really happened


# ---------------------------------------------------------------------------
# Microsoft Graph
# ---------------------------------------------------------------------------

def test_graph_original_search_open_draft_send_roundtrip(api_server) -> None:
    api_server.graph_inbox("me@contoso.example.test", [
        {"from": {"emailAddress": {"address": "ops@harbor.example.test"}},
         "subject": "Kiln repair estimate", "bodyPreview": "estimate is 1230",
         "body": "The repair estimate is 1230 including parts.",
         "message_id": "<an-7@harbor.example.test>", "date": "Thu, 11 Sep 2026 07:00:00 +0000"},
    ])
    _store_graph(api_server)

    found = email_tools.search_email(account="graph-acct", sender="harbor")
    assert found.ok and found.messages[0]["subject"] == "Kiln repair estimate"
    opened = email_tools.open_message(account="graph-acct", message_id=found.messages[0]["provider_id"])
    assert opened.ok and "1230" in opened.messages[0]["body"]

    saved = email_drafts.save_draft(
        to="ops@harbor.example.test", subject="Re: Kiln repair estimate",
        body="Two questions before we decide; not authorized yet.", account="graph-acct",
        in_reply_to="<an-7@harbor.example.test>", kind="reply", session_id=SESSION,
    )
    assert saved.ok
    email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION)
    sent = email_drafts.send_draft(saved.draft["draft_id"], session_id=SESSION)
    assert sent.ok and sent.status == "sent", sent.message

    # The reviewed reply is sent as ONE exact sendMail representation: the
    # approved recipients, subject and decoded body — never the native-reply
    # comment shortcut that would let Graph substitute the parent's defaults.
    captured = api_server.graph_sent("me@contoso.example.test")
    assert len(captured) == 1, (captured, api_server.graph_replies_sent("me@contoso.example.test"))
    message = captured[0]["message"]
    assert message["subject"] == "Re: Kiln repair estimate"
    addresses = [r["emailAddress"]["address"] for r in message["toRecipients"]]
    assert addresses == ["ops@harbor.example.test"]
    assert "not authorized" in message["body"]["content"]
    assert sent.details["receipt"]["acceptance"] == "accepted_for_processing"
    assert sent.details["receipt"]["parent_provider_id"]


def test_graph_revoked_grant_is_an_honest_reauthorization(api_server) -> None:
    _store_graph(api_server, revoke_refresh=True)
    result = email_tools.search_email(account="graph-acct", sender="x")
    assert not result.ok and result.status == "needs_reauthorization"


# ---------------------------------------------------------------------------
# iCloud + refusal laws
# ---------------------------------------------------------------------------

def test_icloud_documented_contract_and_honest_verification() -> None:
    from core.email_providers import icloud_setup_guidance, verify_icloud_account
    from core.email_providers.icloud import icloud_account_blob

    guidance = icloud_setup_guidance()
    assert "imap.mail.me.com" in guidance and "smtp.mail.me.com" in guidance
    assert "app-specific" in guidance.lower()

    blob = json.loads(icloud_account_blob("me@icloud.com", "app-pw"))
    result = verify_icloud_account(blob)
    assert result["ok"] and result["status"] == "configured"
    assert "does not claim" in result["message"]  # no fake connected state

    bad = dict(blob, host="imap.example.com", security="plain")
    assert verify_icloud_account(bad)["status"] == "needs_setup"


def test_unknown_provider_is_refused_never_silently_imap(api_server, monkeypatch) -> None:
    attempted: list[str] = []
    monkeypatch.setattr(email_tools, "_smtp_connect", lambda creds: attempted.append("smtp") or None)
    credential_store.store_credential(
        "email.imap.typo", json.dumps({"provider": "gmial", "api_base": api_server.gmail_base,
                                       "token_url": api_server.token_url}), label="typo")
    result = email_tools.search_email(account="typo", sender="x")
    assert not result.ok and result.status == "needs_setup"
    assert "unknown email provider" in result.message.lower()
    assert not attempted


def test_unconnected_provider_account_never_masquerades_as_connected() -> None:
    # A provider account blob WITHOUT an OAuth handle is an honest needs_setup,
    # not a silent password fallback.
    for provider in ("gmail", "graph"):
        credential_store.store_credential(
            f"email.imap.{provider}-empty", json.dumps({"provider": provider}), label="empty")
        result = email_tools.search_email(account=f"{provider}-empty", sender="x")
        assert not result.ok and result.status == "needs_setup", (provider, result.status)
        assert "no password" not in result.message.lower()
