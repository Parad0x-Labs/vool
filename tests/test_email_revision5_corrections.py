"""Revision-5 correction tests: original + novel + negative controls per repair.

I1  AUTHENTICATED DISPATCH IDENTITY. Every provider request that carries an
    approved effect (the send, the parent lookups it depends on, reconciliation
    reads) runs under a bearer token whose principal the provider's OWN profile
    confirmed. An unreadable or identity-less profile fails closed; a token
    minted after verification is verified again before it carries anything;
    same-principal rotation stays valid; reconciliation reads only the reserved
    verified mailbox. The review's six synthetic-boundary probes are the
    original cases; these add real sockets, new accounts, new failure modes and
    the legitimate controls.

R2  BOUNDED 401 RECOVERY. A spoken 401 means the request was not processed, so
    it earns exactly ONE genuine refresh (a new token, verified again when the
    request is bound); a second 401 is needs_reauthorization, a refused refresh
    is needs_reauthorization, and a refresh that switches mailbox never carries
    the POST. Uncertain or accepted dispatches are never retried or refreshed.
    Transport refusals are typed (provider_refused with its status), which also
    makes a missing message `not_found` instead of a failed read.

G1  GRAPH REPLY SEMANTICS. A reply is ONE documented sendMail in MIME form
    (base64, text/plain) carrying the exact approved From, To, Subject, body,
    Message-ID and the In-Reply-To/References parent binding. A new message
    stays an exact JSON sendMail. Native /reply is not used for reviewed
    content because Graph addresses it itself (documented; modelled by the
    fixture). A reply MIME missing or contradicting its parent headers is
    refused before any request; nothing falls back to a new compose.

S1  DAMAGED-STORE RECOVERY FAILS CLOSED. A damaged draft store is replaced only
    after its bytes are durably quarantined (a verified rename of the original,
    with the replacement prepared and synced first). A refused quarantine, a
    failed replacement (rolled back) or a persistent filesystem fault leaves the
    original bytes in place and every operation returns store_recovery_required.
    An interrupted recovery never looks like an empty store. Recovered
    unresolved effects (reservations, unknown deliveries, salvaged Message-IDs)
    put ordinary sends on hold until an operator acknowledges the exact
    quarantine file. Two processes recovering at once quarantine once.

Transports: [real-rest] the local strict provider server
(tests/provider_api_fixture.py) over loopback sockets. The production draft
store, MIME builder, adapters and OAuth client all run; the fixture's request
log is the wire-level evidence. No live account, token or model is used.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email import policy as mail_policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from pathlib import Path

import pytest

from core import credential_store, email_drafts, email_tools, runtime_paths
from core.email_providers.base import ProviderAccountError, _OAuthClient
from tests.provider_api_fixture import ProviderApiServer

SESSION = "openclaw:revision5"


@pytest.fixture()
def api():
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _store_handle(api: ProviderApiServer, provider: str, account: str, grant: str, *, metadata: str) -> None:
    handle = {"client_id": "c", "client_secret": "s", "refresh_token": grant, "token_url": api.token_url}
    if metadata:
        handle["account_email"] = metadata
    credential_store.store_credential(f"email.oauth.{provider}.{account}", json.dumps(handle), label="r5 oauth")


def _connect(api: ProviderApiServer, provider: str, account: str, principal: str, *,
             metadata: str | None = None, expire_access: bool = False) -> str:
    """Register the account on the provider and store its handle + account blob,
    the way account setup does. Returns the grant (refresh token)."""
    grant = api.add_oauth_client(provider, account, principal, expire_access=expire_access)
    _store_handle(api, provider, account, grant, metadata=principal if metadata is None else metadata)
    blob = {"provider": provider, "from_addr": principal,
            "api_base": api.gmail_base, "token_url": api.token_url}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"r5 {kind}")
    return grant


def _switch_grant(api: ProviderApiServer, provider: str, account: str, grant: str, principal: str, *,
                  metadata: str) -> None:
    """The account slot's grant now authenticates `principal`; the stored
    metadata is left as `metadata` (stale when it names the old mailbox)."""
    api.set_refresh(provider, account, grant)
    api.serve_principal_for(provider, account, grant, principal)
    _store_handle(api, provider, account, grant, metadata=metadata)


def _approved_draft(account: str, *, to, subject: str, body: str, reply_to: str = "",
                    references: list[str] | None = None, session: str = SESSION) -> str:
    saved = email_drafts.save_draft(
        to=to, subject=subject, body=body, account=account, in_reply_to=reply_to,
        references=references if references is not None else ([reply_to] if reply_to else []),
        kind="reply" if reply_to else "compose", session_id=session)
    assert saved.ok, saved.message
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=session).ok
    return saved.draft["draft_id"]


def _profile_path(provider: str) -> str:
    return "/gmail/v1/users/me/profile" if provider == "gmail" else "/v1.0/me"


def _dispatches(api: ProviderApiServer, provider: str, since: int = 0) -> list[dict]:
    """Every send-shaped POST the provider RECEIVED (accepted or not)."""
    def is_send(path: str) -> bool:
        if provider == "gmail":
            return path.endswith("/messages/send")
        return path.endswith("/sendMail") or path.endswith("/reply")
    return [e for e in api.state.request_log[since:] if e["method"] == "POST" and is_send(e["path"])]


def _assert_requests_carried_verified_tokens(api: ProviderApiServer, provider: str, principal: str,
                                             since: int = 0) -> set[str]:
    """Wire law: every non-profile request carried a bearer token that a prior
    profile read (same token, 200, identity served) confirmed as `principal`."""
    verified: set[str] = set()
    for entry in api.state.request_log[since:]:
        if entry["path"] == _profile_path(provider):
            if entry["status"] == 200 and not entry["identity_withheld"] and entry["principal"] == principal:
                verified.add(entry["token"])
            continue
        assert entry["token"] in verified, ("request carried an unverified token", entry,
                                            api.state.request_log[since:])
    return verified


# ---------------------------------------------------------------------------
# I1 — authenticated dispatch identity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["gmail", "graph"])
@pytest.mark.parametrize("mode", ["forbidden", "missing", "drop"])
def test_i1_inconclusive_profile_under_switched_grant_refuses_and_keeps_the_approval(api, provider, mode) -> None:
    """NOVEL accounts and a new failure mode (drop) for the review's class, over a
    real socket: the slot's grant is switched behind unchanged metadata and the
    new credential's profile cannot confirm anyone. Nothing is dispatched, the
    reservation is released, and the untouched approval sends exactly once after
    the original grant is back and readable — metadata never stood in."""
    principal = "ops@lagoon.example.test"
    original = _connect(api, provider, "ledger", principal)
    did = _approved_draft("ledger", to="harbour@quay.example.test", subject="Berth 7 manifest",
                          body="Fourteen pallets, crane slot at 06:40.")
    _switch_grant(api, provider, "ledger", "switched-ledger-grant", "intruder@elsewhere.example.test",
                  metadata=principal)
    api.fail_profile(provider, "ledger", mode)

    refused = email_drafts.send_draft(did, session_id=SESSION)
    assert not refused.ok and refused.status == "identity_unverified", (refused.status, refused.message)
    assert _dispatches(api, provider) == []
    assert api.sent_count(provider, principal) == 0
    assert api.sent_count(provider, "intruder@elsewhere.example.test") == 0
    row = email_drafts.get_draft(did, session_id=SESSION).draft
    assert row["status"] == "approved" and not row["sent_message_id"], row

    api.fail_profile(provider, "ledger", None)
    api.set_refresh(provider, "ledger", original)
    _store_handle(api, provider, "ledger", original, metadata=principal)
    marker = len(api.state.request_log)
    sent = email_drafts.send_draft(did, session_id=SESSION)
    assert sent.ok and sent.status == "sent", sent.message
    assert api.sent_count(provider, principal) == 1
    assert api.sent_count(provider, "intruder@elsewhere.example.test") == 0
    _assert_requests_carried_verified_tokens(api, provider, principal, since=marker)
    assert sent.details["receipt"]["verified_principal"] == principal


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_i1_token_minted_after_verification_is_verified_again_before_use(api, provider) -> None:
    """The credential changes between the identity check and the dispatch: every
    access token lives one second, so each request mints a fresh one, and the
    schedule makes the grant authenticate another mailbox from the second mint.
    The first token is verified; the next token must be verified AGAIN before it
    carries the parent lookup or the POST, and its conclusive mismatch refuses."""
    principal = "desk@orchard.example.test"
    _connect(api, provider, "desk", principal, expire_access=True)
    api.schedule_principals(provider, "desk", [principal, "switched@elsewhere.example.test"])
    parent = "<crates-2026-09-12@farm.example.test>"
    did = _approved_draft("desk", to="grower@farm.example.test", subject="Re: Crate count",
                          body="Twelve crates on Friday.", reply_to=parent)
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not result.ok and result.status == "needs_reapproval", (result.status, result.message)
    assert _dispatches(api, provider) == []
    profile_reads = [e for e in api.state.request_log if e["path"] == _profile_path(provider)]
    assert [e["principal"] for e in profile_reads] == [principal, "switched@elsewhere.example.test"]
    assert len({e["token"] for e in profile_reads}) == 2
    # No request other than the profile read itself ever carried the switched token.
    switched = {e["token"] for e in profile_reads if e["principal"] != principal}
    assert not [e for e in api.state.request_log
                if e["token"] in switched and e["path"] != _profile_path(provider)]
    assert email_drafts.get_draft(did, session_id=SESSION).draft["status"] == "approved"


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_i1_every_fresh_token_for_the_same_principal_is_verified_and_sends_once(api, provider) -> None:
    """CONTROL for the case above: the same short-lived tokens, all for the SAME
    mailbox. Each distinct token is verified before it carries a request, and the
    reply is dispatched exactly once."""
    principal = "desk@granary.example.test"
    _connect(api, provider, "granary", principal, expire_access=True)
    did = _approved_draft("granary", to="miller@mill.example.test", subject="Re: Flour order",
                          body="Forty sacks, ground fine.", reply_to="<flour-88@mill.example.test>")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    assert len(_dispatches(api, provider)) == 1 and api.sent_count(provider, principal) == 1
    verified = _assert_requests_carried_verified_tokens(api, provider, principal)
    assert len(verified) >= 2  # more than one credential event, each verified
    assert result.details["receipt"]["principal_verifications"] == len(verified)


def test_i1_same_principal_rotation_with_upn_only_profile_sends_once(api) -> None:
    """LEGITIMATE LIFECYCLE: the provider rotates the refresh token on use and the
    Graph profile names the user only by userPrincipalName (mail is null). The
    principal is unchanged, so this is not a repoint: one send, rotation persisted."""
    principal = "planner@tenant.example.test"
    grant = _connect(api, "graph", "planner", principal)
    api.rotate_refresh_for("graph", "planner", grant, "planner-rotated-1")
    api.graph_profile_without_mail("planner")
    did = _approved_draft("planner", to="vendor@supply.example.test", subject="PO 5531 schedule",
                          body="Deliver on the 21st, dock B.")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", result.message
    assert api.sent_count("graph", principal) == 1
    handle = json.loads(credential_store.get_credential("email.oauth.graph.planner"))
    assert handle["refresh_token"] == "planner-rotated-1"
    assert result.details["receipt"]["verified_principal"] == principal
    _assert_requests_carried_verified_tokens(api, "graph", principal)


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_i1_reconciliation_reads_only_the_reserved_verified_mailbox(api, provider) -> None:
    """An unresolved send is reconciled ONLY through a credential verified as the
    reserved principal: a switched grant (stale metadata) or an unreadable profile
    blocks reconciliation before any sent-view read, and the state stays unresolved.
    With the reserved mailbox verified again, reconciliation proceeds."""
    principal = "books@atelier.example.test"
    original = _connect(api, provider, "books", principal)
    did = _approved_draft("books", to="printer@press.example.test", subject="Proof run 3",
                          body="Approve 400 copies on matte stock.")
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=SESSION).status == "delivery_unknown"
    api.drop_after_accept = False

    _switch_grant(api, provider, "books", "switched-books-grant", "other@atelier.example.test",
                  metadata=principal)
    marker = len(api.state.request_log)
    switched = email_drafts.reconcile_draft(did, session_id=SESSION)
    assert switched.status == "reconciliation_blocked", (switched.status, switched.message)
    assert all(e["path"] == _profile_path(provider) for e in api.state.request_log[marker:])
    assert email_drafts.get_draft(did, session_id=SESSION).draft["status"] == "delivery_unknown"

    api.set_refresh(provider, "books", original)
    _store_handle(api, provider, "books", original, metadata=principal)
    api.fail_profile(provider, "books", "forbidden")
    marker = len(api.state.request_log)
    unverified = email_drafts.reconcile_draft(did, session_id=SESSION)
    assert unverified.status == "reconciliation_blocked", (unverified.status, unverified.message)
    assert all(e["path"] == _profile_path(provider) for e in api.state.request_log[marker:])

    api.fail_profile(provider, "books", None)
    marker = len(api.state.request_log)
    final = email_drafts.reconcile_draft(did, session_id=SESSION)
    if provider == "gmail":
        assert final.status == "sent_confirmed", final.message
    else:
        assert final.status == "delivery_unknown" and "candidate" in final.message.lower(), final.message
    _assert_requests_carried_verified_tokens(api, provider, principal, since=marker)
    assert api.sent_count(provider, principal) == 1


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_i1_direct_send_is_bound_to_the_configured_mailbox(api, provider) -> None:
    """A send outside the draft flow has no approval to bind, so it binds to the
    mailbox the account slot is configured for: a switched grant is refused
    before any POST, and the correctly bound grant sends once."""
    principal = "front@desk.example.test"
    original = _connect(api, provider, "front", principal)
    _switch_grant(api, provider, "front", "switched-front-grant", "elsewhere@desk.example.test",
                  metadata=principal)
    refused = email_tools.send_email(to="guest@visit.example.test", subject="Key pickup",
                                     body="Reception is open until 20:00.", account="front")
    assert not refused.ok and refused.status == "needs_reapproval", (refused.status, refused.message)
    assert _dispatches(api, provider) == []

    api.set_refresh(provider, "front", original)
    _store_handle(api, provider, "front", original, metadata=principal)
    sent = email_tools.send_email(to="guest@visit.example.test", subject="Key pickup",
                                  body="Reception is open until 20:00.", account="front")
    assert sent.ok and sent.status == "executed", sent.message
    assert api.sent_count(provider, principal) == 1


def test_i1_oauth_account_without_a_bound_principal_fails_closed(api) -> None:
    """An OAuth account whose handle names no principal has nothing an approval can
    be bound to: the send fails closed before any request carries the message."""
    grant = api.add_oauth_client("gmail", "bare", "bare@mail.example.test")
    _store_handle(api, "gmail", "bare", grant, metadata="")
    blob = {"provider": "gmail", "from_addr": "bare@mail.example.test",
            "api_base": api.gmail_base, "token_url": api.token_url}
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.bare", json.dumps(blob), label=f"r5 {kind}")
    did = _approved_draft("bare", to="someone@else.example.test", subject="Unbound", body="Should not leave.")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not result.ok and result.status == "identity_unverified", (result.status, result.message)
    assert _dispatches(api, "gmail") == []
    assert email_drafts.get_draft(did, session_id=SESSION).draft["status"] == "approved"


# ---------------------------------------------------------------------------
# R2 — bounded 401 recovery that cannot bypass the verified principal
# ---------------------------------------------------------------------------

_ACCEPTED_STATUS = {"gmail": 200, "graph": 202}


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_token_revoked_before_the_post_gets_one_refresh_and_sends_once(api, provider) -> None:
    """NOVEL through real sockets: the send's token is revoked server-side between
    verification and dispatch. The provider refuses the POST (401, not processed);
    the client performs ONE genuine refresh, verifies the new token's principal and
    dispatches once with it."""
    principal = "clerk@registry.example.test"
    _connect(api, provider, "registry", principal)
    did = _approved_draft("registry", to="archive@records.example.test", subject="Deed 7781 filing",
                          body="Filed under folio 12.")
    api.revoke_token_at_next(provider, "registry", method="POST")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    posts = _dispatches(api, provider)
    assert [p["status"] for p in posts] == [401, _ACCEPTED_STATUS[provider]]
    assert posts[0]["token"] != posts[1]["token"]
    assert len(api.mints(provider, "registry")) == 2
    assert api.sent_count(provider, principal) == 1
    verified = _assert_requests_carried_verified_tokens(api, provider, principal)
    assert posts[1]["token"] in verified
    assert result.details["receipt"]["principal_verifications"] == 2


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_second_401_is_reauthorization_with_no_third_attempt(api, provider) -> None:
    """The account's access is revoked while its refresh grant still mints tokens:
    the refreshed POST is refused too. That is needs_reauthorization after exactly
    two refused attempts; nothing was processed, and the same approval sends once
    after access is restored."""
    principal = "clerk@annex.example.test"
    _connect(api, provider, "annex", principal)
    did = _approved_draft("annex", to="archive@records.example.test", subject="Annex plan B",
                          body="Plan B approved for the annex.")
    api.refuse_every_token(provider, "annex", method="POST")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not result.ok and result.status == "failed", (result.status, result.message)
    assert result.details["receipt"]["outcome"] == "needs_reauthorization", result.details["receipt"]
    assert [p["status"] for p in _dispatches(api, provider)] == [401, 401]
    assert len(api.mints(provider, "annex")) == 2
    assert api.sent_count(provider, principal) == 0

    api.state.revocation_rules.clear()
    retry = email_drafts.send_draft(did, session_id=SESSION)
    assert retry.ok and retry.status == "sent", (retry.status, retry.message)
    assert api.sent_count(provider, principal) == 1


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_refresh_refused_after_401_is_reauthorization(api, provider) -> None:
    """The 401's one refresh is itself refused (invalid_grant): needs_reauthorization,
    one refused POST, no further attempt."""
    principal = "desk@quarry.example.test"
    _connect(api, provider, "quarry", principal)
    did = _approved_draft("quarry", to="haulier@road.example.test", subject="Load 44 window",
                          body="Load 44 leaves at dawn.")
    api.revoke_token_at_next(provider, "quarry", method="POST")
    api.refuse_refresh_after(provider, "quarry", mints=1)
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not result.ok, (result.status, result.message)
    assert result.details["receipt"]["outcome"] == "needs_reauthorization", result.details["receipt"]
    assert [p["status"] for p in _dispatches(api, provider)] == [401]
    assert [e["outcome"] for e in api.state.token_log if e["account"] == (provider, "quarry")] == [
        "minted", "refused"]
    assert api.sent_count(provider, principal) == 0


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_refresh_after_401_that_switches_the_mailbox_never_carries_the_post(api, provider) -> None:
    """The refresh the 401 earns mints a token for ANOTHER mailbox. The new credential
    is verified before it carries anything, so the POST is never sent with it."""
    principal = "desk@foundry.example.test"
    _connect(api, provider, "foundry", principal)
    api.schedule_principals(provider, "foundry", [principal, "stranger@elsewhere.example.test"])
    did = _approved_draft("foundry", to="caster@mould.example.test", subject="Pour schedule",
                          body="Pour at 07:00 Thursday.")
    api.revoke_token_at_next(provider, "foundry", method="POST")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert not result.ok and result.status == "needs_reapproval", (result.status, result.message)
    posts = _dispatches(api, provider)
    assert [p["status"] for p in posts] == [401] and posts[0]["principal"] == principal
    minted = api.mints(provider, "foundry")
    assert [m["principal"] for m in minted] == [principal, "stranger@elsewhere.example.test"]
    assert not [e for e in api.state.request_log
                if e["token"] == minted[1]["token"] and e["path"] != _profile_path(provider)]
    assert email_drafts.get_draft(did, session_id=SESSION).draft["status"] == "approved"


def test_r2_same_principal_rotation_during_401_recovery_sends_once_and_persists(api) -> None:
    """LEGITIMATE LIFECYCLE inside recovery: the provider rotates the refresh token, the
    401's refresh presents the rotated grant, and the SAME mailbox is verified again."""
    principal = "desk@cannery.example.test"
    grant = _connect(api, "gmail", "cannery", principal)
    api.rotate_refresh_for("gmail", "cannery", grant, "cannery-rotated-1")
    did = _approved_draft("cannery", to="buyer@market.example.test", subject="Tins for Friday",
                          body="Six hundred tins, labelled.")
    api.revoke_token_at_next("gmail", "cannery", method="POST")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    minted = api.mints("gmail", "cannery")
    assert [m["grant"] for m in minted] == [grant, "cannery-rotated-1"]
    assert {m["principal"] for m in minted} == {principal}
    handle = json.loads(credential_store.get_credential("email.oauth.gmail.cannery"))
    assert handle["refresh_token"] == "cannery-rotated-1"
    assert api.sent_count("gmail", principal) == 1


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_uncertain_post_is_never_retried_or_refreshed(api, provider) -> None:
    """CONTROL: a dispatch whose outcome is uncertain earns no refresh and no retry."""
    principal = "desk@ferry.example.test"
    _connect(api, provider, "ferry", principal)
    did = _approved_draft("ferry", to="purser@deck.example.test", subject="Crossing 19:10",
                          body="Two cars, one van.")
    api.drop_after_accept = True
    first = email_drafts.send_draft(did, session_id=SESSION)
    api.drop_after_accept = False
    assert first.status == "delivery_unknown", (first.status, first.message)
    assert len(_dispatches(api, provider)) == 1 and len(api.mints(provider, "ferry")) == 1
    again = email_drafts.send_draft(did, session_id=SESSION)
    assert again.status == "delivery_unknown" and len(_dispatches(api, provider)) == 1


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_refreshed_post_with_uncertain_outcome_stops_at_two_attempts(api, provider) -> None:
    """401, one refresh, then the refreshed POST is accepted but its reply is lost:
    delivery_unknown after exactly two attempts; a replay dispatches nothing."""
    principal = "desk@pier.example.test"
    _connect(api, provider, "pier", principal)
    did = _approved_draft("pier", to="harbourmaster@pier.example.test", subject="Mooring 3",
                          body="Mooring 3 until Sunday.")
    api.revoke_token_at_next(provider, "pier", method="POST")
    api.drop_after_accept = True
    first = email_drafts.send_draft(did, session_id=SESSION)
    api.drop_after_accept = False
    assert first.status == "delivery_unknown", (first.status, first.message)
    assert [p["status"] for p in _dispatches(api, provider)] == [401, None]
    assert api.sent_count(provider, principal) == 1
    replay = email_drafts.send_draft(did, session_id=SESSION)
    assert replay.status == "delivery_unknown" and len(_dispatches(api, provider)) == 2


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_read_refused_401_recovers_once_and_a_second_401_is_reauthorization(api, provider) -> None:
    """Reads get the same bounded recovery: one refresh recovers a revoked token; when
    every token is refused the result is needs_reauthorization, never an empty mailbox."""
    principal = "reader@library.example.test"
    _connect(api, provider, "library", principal)
    if provider == "gmail":
        api.gmail_inbox(principal, [{"from": "curator@museum.example.test", "subject": "Loan 88",
                                     "body": "Loan 88 returns Monday.",
                                     "message_id": "<loan-88@museum.example.test>",
                                     "date": "Fri, 12 Sep 2026 09:00:00 +0000"}])
    else:
        api.graph_inbox(principal, [{"from": {"emailAddress": {"address": "curator@museum.example.test"}},
                                     "subject": "Loan 88", "body": "Loan 88 returns Monday.",
                                     "message_id": "<loan-88@museum.example.test>",
                                     "date": "2026-09-12T09:00:00Z"}])
    api.revoke_token_at_next(provider, "library", method="GET", path_suffix="/messages")
    found = email_tools.search_email(account="library", sender="museum")
    assert found.ok and found.status == "executed", (found.status, found.message)
    assert [m["subject"] for m in found.messages] == ["Loan 88"]
    assert len(api.mints(provider, "library")) == 2

    api.refuse_every_token(provider, "library", method="GET", path_suffix="/messages")
    refused = email_tools.search_email(account="library", sender="museum")
    assert not refused.ok and refused.status == "needs_reauthorization", (refused.status, refused.message)
    assert refused.messages == []


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_profile_read_refused_401_recovers_through_one_verified_refresh(api, provider) -> None:
    """The identity read itself meets a revoked token: one refresh, the new token is
    verified, and the send carries only that verified token."""
    principal = "desk@observatory.example.test"
    _connect(api, provider, "observatory", principal)
    did = _approved_draft("observatory", to="night@dome.example.test", subject="Dome slot",
                          body="Dome slot 23:00.")
    api.revoke_token_at_next(provider, "observatory", method="GET", path_suffix=_profile_path(provider))
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    profile_reads = [e for e in api.state.request_log if e["path"] == _profile_path(provider)]
    assert [e["status"] for e in profile_reads] == [401, 200]
    assert len(api.mints(provider, "observatory")) == 2
    _assert_requests_carried_verified_tokens(api, provider, principal)


class _WireReply:
    status = 200

    def __init__(self, data: bytes) -> None:
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self.data


def test_r2_concurrent_401s_on_one_client_share_a_single_refresh(monkeypatch) -> None:
    """[synthetic-boundary] Two requests on one client see the same cached token refused
    at the same time: exactly one genuine refresh happens and both complete with it."""
    client = _OAuthClient(provider="graph", token_url="https://oauth.fixture.test/token", client_id="c",
                          client_secret="s", refresh_token="grant", scopes="scope")
    client._access_token = "stale-access"
    client._expires_at = time.time() + 3600
    barrier = threading.Barrier(2, timeout=5)
    guard = threading.Lock()
    refreshes: list[int] = []
    bearers: list[str] = []

    def wire(req, **kwargs):
        if req.full_url.endswith("/token"):
            with guard:
                refreshes.append(1)
            time.sleep(0.05)
            return _WireReply(b'{"access_token":"fresh-access","expires_in":3600}')
        bearer = req.get_header("Authorization")
        with guard:
            bearers.append(bearer)
        if bearer == "Bearer stale-access":
            barrier.wait()
            raise urllib.error.HTTPError(req.full_url, 401, "revoked", {}, io.BytesIO(b"{}"))
        return _WireReply(b'{"value": []}')

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    results: list[dict] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            results.append(client.api_read("GET", "https://graph.fixture.test/v1.0/me/messages"))
        except BaseException as exc:  # recorded for the assertion below
            errors.append(exc)

    workers = [threading.Thread(target=read) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(15)
    assert not errors, errors
    assert len(results) == 2 and len(refreshes) == 1, (results, refreshes, bearers)
    assert sorted(bearers) == ["Bearer fresh-access", "Bearer fresh-access",
                               "Bearer stale-access", "Bearer stale-access"]


def test_r2_spoken_refusal_on_a_read_is_typed(monkeypatch) -> None:
    """[synthetic-boundary] A 503 is the provider's spoken refusal: a typed
    provider_refused with its status, never a raw transport exception."""
    client = _OAuthClient(provider="graph", token_url="https://oauth.fixture.test/token", client_id="c",
                          client_secret="s", refresh_token="grant", scopes="scope")
    client._access_token = "live-access"
    client._expires_at = time.time() + 3600

    def wire(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {},
                                     io.BytesIO(b'{"error":{"code":"ServiceUnavailable"}}'))

    monkeypatch.setattr(urllib.request, "urlopen", wire)
    with pytest.raises(ProviderAccountError) as refused:
        client.api_read("GET", "https://graph.fixture.test/v1.0/me/messages")
    assert refused.value.kind == "provider_refused" and refused.value.status == 503


@pytest.mark.parametrize("provider", ["gmail", "graph"])
def test_r2_opening_a_message_the_mailbox_does_not_have_is_not_found(api, provider) -> None:
    """The provider's 404 for an unknown id is `not_found` (the documented branch),
    while an existing message still opens: never a generic failed read."""
    principal = "reader@stacks.example.test"
    _connect(api, provider, "stacks", principal)
    if provider == "gmail":
        api.gmail_inbox(principal, [{"from": "binder@press.example.test", "subject": "Spine proof",
                                     "body": "Spine proof attached.", "message_id": "<spine@press.example.test>",
                                     "date": "Fri, 12 Sep 2026 10:00:00 +0000"}])
        existing = api.state.gmail_mailboxes[principal][0]["id"]
    else:
        api.graph_inbox(principal, [{"from": {"emailAddress": {"address": "binder@press.example.test"}},
                                     "subject": "Spine proof", "body": "Spine proof attached.",
                                     "message_id": "<spine@press.example.test>", "date": "2026-09-12T10:00:00Z"}])
        existing = api.state.graph_mailboxes[principal][0]["id"]
    opened = email_tools.open_message(account="stacks", message_id=existing)
    assert opened.ok and opened.messages[0]["subject"] == "Spine proof", (opened.status, opened.message)
    missing = email_tools.open_message(account="stacks", message_id="no-such-message-7")
    assert not missing.ok and missing.status == "not_found", (missing.status, missing.message)


# ---------------------------------------------------------------------------
# G1 — Graph replies carry the exact approved message AND its parent binding
# ---------------------------------------------------------------------------

def _graph_parent(api: ProviderApiServer, principal: str, *, message_id: str, subject: str, sender: str,
                  reply_to: list[str] | None = None) -> str:
    entry = {"from": {"emailAddress": {"address": sender}}, "subject": subject, "body": "Parent body.",
             "message_id": message_id, "date": "2026-09-12T08:00:00Z"}
    if reply_to:
        entry["reply_to"] = [{"emailAddress": {"address": address}} for address in reply_to]
    api.graph_inbox(principal, [entry])
    return api.state.graph_mailboxes[principal][0]["id"]


def _wire_mime(record: dict):
    """The MIME message exactly as the provider received it (strictly decoded)."""
    return BytesParser(policy=mail_policy.default).parsebytes(record["raw_mime"])


def _raw_token(api: ProviderApiServer, grant: str) -> str:
    request = urllib.request.Request(
        api.token_url, data=f"grant_type=refresh_token&refresh_token={grant}&client_id=c".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())["access_token"]


def _raw_post(api: ProviderApiServer, path: str, token: str, body: bytes, content_type: str) -> tuple[int, dict]:
    request = urllib.request.Request(f"{api.graph_base}{path}", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            text = response.read().decode("utf-8")
            return response.status, (json.loads(text) if text.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def _assert_exact_reply(wire, approved: dict, receipt: dict, principal: str, parent: str) -> None:
    assert [m.addr_spec for m in wire["From"].addresses] == [principal]
    assert [address for _name, address in getaddresses([str(wire["To"])])] == approved["to"]
    assert str(wire["Subject"]) == approved["subject"]
    assert wire.get_body(preferencelist=("plain",)).get_content().rstrip("\n") == approved["body"]
    assert str(wire["In-Reply-To"]) == parent
    assert str(wire["References"]).split() == approved["references"]
    assert str(wire["Message-ID"]) == receipt["message_id"] == receipt["reserved_message_id"]


def test_g1_graph_reply_is_one_mime_send_carrying_the_exact_message_and_parent(api) -> None:
    """ORIGINAL class, real socket: the approved reply leaves as ONE base64 MIME sendMail
    whose decoded headers and body equal the approved canonical draft, with the parent
    binding; the receipt is accepted-for-processing, never inbox delivery."""
    principal = "desk@kiln.example.test"
    _connect(api, "graph", "kiln", principal)
    parent = "<estimate-8442@repair.example.test>"
    parent_id = _graph_parent(api, principal, message_id=parent, subject="Kiln repair estimate 8442",
                              sender="estimator@repair.example.test")
    did = _approved_draft("kiln", to="estimator@repair.example.test", subject="Kiln repair estimate 8442",
                          body="Two questions: does it include re-testing, and what is the lead time?",
                          reply_to=parent)
    approved = email_drafts.get_draft(did, session_id=SESSION).draft
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    assert [(p["path"], p["content_type"], p["status"]) for p in _dispatches(api, "graph")] == [
        ("/v1.0/me/sendMail", "text/plain", 202)]
    sent = api.graph_sent(principal)
    assert len(sent) == 1 and sent[0]["representation"] == "mime"
    receipt = result.details["receipt"]
    _assert_exact_reply(_wire_mime(sent[0]), approved, receipt, principal, parent)
    assert approved["subject"] == "Re: Kiln repair estimate 8442"
    assert receipt["acceptance"] == "accepted_for_processing"
    assert receipt["representation"] == "mime" and receipt["parent_resolved"] is True
    assert receipt["parent_provider_id"] == parent_id
    assert api.graph_replies_sent(principal) == []


def test_g1_novel_multi_recipient_unicode_reply_with_a_reference_chain_is_exact(api) -> None:
    """NOVEL data: two recipients, a Lithuanian/currency subject, a body mixing Lithuanian,
    Japanese and a long wrapped line, and a three-message References chain."""
    principal = "biuras@dirbtuves.example.test"
    _connect(api, "graph", "dirbtuves", principal)
    chain = ["<a-1@tiekejas.example.test>", "<a-2@tiekejas.example.test>", "<a-3@tiekejas.example.test>"]
    _graph_parent(api, principal, message_id=chain[-1], subject="Pasiūlymas — 1 230 €",
                  sender="tiekejas@tiekejas.example.test")
    body = ("Labas,\n\nAčiū už pasiūlymą: 1 230 € su PVM.\n会議は火曜日 14時に確認します。\n\n"
            + "Pristatymo sąlygos: " + "paletės, " * 20)
    did = _approved_draft("dirbtuves", to=["tiekejas@tiekejas.example.test", "sandelis@tiekejas.example.test"],
                          subject="Pasiūlymas — 1 230 €", body=body, reply_to=chain[-1], references=chain)
    approved = email_drafts.get_draft(did, session_id=SESSION).draft
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    sent = api.graph_sent(principal)
    assert len(sent) == 1 and sent[0]["representation"] == "mime"
    _assert_exact_reply(_wire_mime(sent[0]), approved, result.details["receipt"], principal, chain[-1])
    assert approved["to"] == ["tiekejas@tiekejas.example.test", "sandelis@tiekejas.example.test"]
    assert chain[0] in approved["references"] and approved["references"][-1] == chain[-1]


def test_g1_reply_whose_parent_is_not_in_the_mailbox_still_carries_the_rfc_binding(api) -> None:
    """The parent is not found in the verified mailbox: the reply still leaves as the
    exact MIME reply with its In-Reply-To/References binding (never a new compose), and
    the receipt says the provider-side parent was not resolved."""
    principal = "desk@cooperage.example.test"
    _connect(api, "graph", "cooperage", principal)
    parent = "<barrel-order-17@vintner.example.test>"
    did = _approved_draft("cooperage", to="vintner@vintner.example.test", subject="Barrel order 17",
                          body="Twelve barrels, French oak.", reply_to=parent)
    approved = email_drafts.get_draft(did, session_id=SESSION).draft
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    receipt = result.details["receipt"]
    _assert_exact_reply(_wire_mime(api.graph_sent(principal)[0]), approved, receipt, principal, parent)
    assert receipt["representation"] == "mime"
    assert receipt["parent_resolved"] is False and receipt["parent_provider_id"] == ""


@pytest.mark.parametrize("shape", ["missing-parent-headers", "different-parent"])
def test_g1_reply_mime_that_does_not_carry_its_approved_parent_is_refused_before_any_request(api, shape) -> None:
    """Structural guard at the adapter: a reply whose MIME lacks, or contradicts, the
    approved parent binding is refused before a single request (no profile read, no
    lookup, no POST) — never sent as a new compose."""
    principal = "desk@tannery.example.test"
    _connect(api, "graph", "tannery", principal)
    creds = json.loads(credential_store.get_credential("email.smtp.tannery"))
    adapter = email_tools._provider_adapter("tannery", creds)
    message = EmailMessage()
    message["From"] = principal
    message["To"] = "hides@leather.example.test"
    message["Subject"] = "Re: Hide count"
    message["Message-ID"] = "<r5-guard@tannery.example.test>"
    if shape == "different-parent":
        message["In-Reply-To"] = "<some-other-message@leather.example.test>"
        message["References"] = "<some-other-message@leather.example.test>"
    message.set_content("Forty hides.")
    result = adapter.send(raw_mime=message.as_bytes(), reply_to_rfc="<hides-9@leather.example.test>",
                          expected_identity=email_tools.configured_identity("tannery", creds))
    assert not result.ok and result.status == "invalid_message", (result.status, result.message)
    assert api.state.request_log == []


def test_g1_new_message_without_a_parent_stays_an_exact_json_send(api) -> None:
    """CONTROL: a new message (no parent) keeps the exact JSON sendMail representation."""
    principal = "desk@frames.example.test"
    _connect(api, "graph", "frames", principal)
    did = _approved_draft("frames", to=["framer@frame.example.test", "gilder@gold.example.test"],
                          subject="Frame sizes 40×60", body="Two frames, 40×60 cm, walnut.")
    result = email_drafts.send_draft(did, session_id=SESSION)
    assert result.ok and result.status == "sent", (result.status, result.message)
    assert [(p["path"], p["content_type"]) for p in _dispatches(api, "graph")] == [
        ("/v1.0/me/sendMail", "application/json")]
    sent = api.graph_sent(principal)[0]
    assert sent["representation"] == "json"
    assert [r["emailAddress"]["address"] for r in sent["message"]["toRecipients"]] == [
        "framer@frame.example.test", "gilder@gold.example.test"]
    assert sent["message"]["subject"] == "Frame sizes 40×60"
    assert sent["message"]["body"]["content"].rstrip("\n") == "Two frames, 40×60 cm, walnut."
    assert result.details["receipt"]["representation"] == "json"


def test_g1_fixture_enforces_the_documented_sendmail_contract(api) -> None:
    """[fixture-contract] The local provider refuses what Graph documents it refuses:
    invalid base64 MIME, a MIME message with no recipient, an unsupported media type."""
    principal = "desk@probe.example.test"
    grant = _connect(api, "graph", "probe", principal)
    token = _raw_token(api, grant)
    status, body = _raw_post(api, "/v1.0/me/sendMail", token, b"@@ not base64 @@", "text/plain")
    assert status == 400 and body["error"]["code"] == "ErrorMimeContentInvalidBase64String"
    no_recipient = EmailMessage()
    no_recipient["From"] = principal
    no_recipient["Subject"] = "Nobody"
    no_recipient.set_content("No recipient at all.")
    status, body = _raw_post(api, "/v1.0/me/sendMail", token, base64.b64encode(no_recipient.as_bytes()),
                             "text/plain")
    assert status == 400 and body["error"]["code"] == "ErrorInvalidRecipients"
    status, _body = _raw_post(api, "/v1.0/me/sendMail", token, b"<xml/>", "application/xml")
    assert status == 415
    assert api.graph_sent(principal) == []


def test_g1_fixture_models_documented_native_reply_addressing(api) -> None:
    """[fixture-contract] Why reviewed replies do not use /reply: Graph addresses a native
    reply itself. JSON toRecipients are ADDED to the parent's replyTo (else its sender),
    comment and message.body together are a 400, and a MIME reply goes to the parent's
    sender whatever its To says."""
    principal = "desk@wharf.example.test"
    grant = _connect(api, "graph", "wharf", principal)
    parent_id = _graph_parent(api, principal, message_id="<berth-2@port.example.test>", subject="Berth 2",
                              sender="agent@port.example.test", reply_to=["ops@port.example.test"])
    token = _raw_token(api, grant)
    reply_path = f"/v1.0/me/messages/{parent_id}/reply"
    status, _body = _raw_post(api, reply_path, token, json.dumps({
        "message": {"toRecipients": [{"emailAddress": {"address": "approved@ship.example.test"}}]},
        "comment": "Only the approved recipient should get this."}).encode(), "application/json")
    assert status == 202
    assert api.graph_replies_sent(principal)[-1]["recipients"] == ["ops@port.example.test",
                                                                   "approved@ship.example.test"]
    status, body = _raw_post(api, reply_path, token, json.dumps({
        "message": {"body": {"contentType": "text", "content": "body"}}, "comment": "and a comment"}).encode(),
        "application/json")
    assert status == 400 and body["error"]["code"] == "ErrorInvalidRequest"
    mime = EmailMessage()
    mime["From"] = principal
    mime["To"] = "approved@ship.example.test"
    mime["Subject"] = "Re: Berth 2"
    mime.set_content("Approved text.")
    status, _body = _raw_post(api, reply_path, token, base64.b64encode(mime.as_bytes()), "text/plain")
    assert status == 202
    assert api.graph_replies_sent(principal)[-1]["recipients"] == ["agent@port.example.test"]


def test_g1_mime_reply_with_a_lost_acceptance_is_unknown_and_reconciles_only_to_candidates(api) -> None:
    """A MIME reply accepted but whose response is lost is delivery_unknown after ONE
    POST; reconciliation reads the verified sent view and reports candidate evidence,
    never a confirmation, and dispatches nothing."""
    principal = "desk@saltworks.example.test"
    _connect(api, "graph", "saltworks", principal)
    did = _approved_draft("saltworks", to="buyer@brine.example.test", subject="Salt lot 5",
                          body="Lot 5 is ready for collection.", reply_to="<lot-5@brine.example.test>")
    api.drop_after_accept = True
    first = email_drafts.send_draft(did, session_id=SESSION)
    api.drop_after_accept = False
    assert first.status == "delivery_unknown", (first.status, first.message)
    sent = api.graph_sent(principal)
    assert len(_dispatches(api, "graph")) == 1 and len(sent) == 1 and sent[0]["representation"] == "mime"
    reconciled = email_drafts.reconcile_draft(did, session_id=SESSION)
    assert reconciled.status == "delivery_unknown", (reconciled.status, reconciled.message)
    assert "candidate" in reconciled.message.lower()
    assert len(_dispatches(api, "graph")) == 1


# ---------------------------------------------------------------------------
# S1 — damaged-store recovery fails closed and keeps unresolved effects visible
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]

# A store torn mid-write while it held an unresolved send (parse-invalid).
_TORN_UNRESOLVED = (b'{"version": 1, "drafts": {"ed-lost": {"draft_id": "ed-lost", "status": "delivery_unknown", '
                    b'"sent_message_id": "<lost-7@quarry.example.test>", "to": ["buyer@quarry.example.test"]')
# Valid JSON in the wrong shape that still names a reservation (schema-invalid).
_MISSHAPEN_UNRESOLVED = (b'{"version": 1, "drafts": {"ed-9": ["not", "a", "row"]}, "archived": '
                         b'{"status": "sending", "sent_message_id": "<berth-3@harbour.example.test>"}}')


def _write_store(raw: bytes) -> Path:
    path = email_drafts._drafts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _quarantines(path: Path) -> list[Path]:
    return sorted(path.parent.glob("drafts.corrupt-*.json"))


def _unsendable_compose(subject: str) -> str:
    """An approved draft on the unconfigured default account: if a send is NOT held it
    reaches the dispatch seam and stops there as needs_credentials (no network)."""
    return _approved_draft("default", to="buyer@quarry.example.test", subject=subject, body="Recovery probe.")


_PROCESS_DRIVER = """
import json, os, sys, time
sys.path.insert(0, os.environ["REPO_ROOT"])
from core import email_drafts
start = float(os.environ.get("START_AT") or 0)
while start and time.time() < start:
    time.sleep(0.005)
result = email_drafts.get_draft(session_id=os.environ["SESSION"], latest=True)
print("RESULT:" + json.dumps({"status": result.status, "store_recovery": result.details.get("store_recovery")}))
"""


def _driver_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(REPO_ROOT=str(REPO_ROOT), PYTHONPATH=str(REPO_ROOT), PYTHONDONTWRITEBYTECODE="1",
               SESSION=SESSION, **extra)
    return env


def _driver_result(stdout: str) -> dict:
    return json.loads(next(line.partition("RESULT:")[2] for line in stdout.splitlines() if line.startswith("RESULT:")))


@pytest.mark.parametrize("damaged", [_TORN_UNRESOLVED, _MISSHAPEN_UNRESOLVED], ids=["parse-invalid", "schema-invalid"])
def test_s1_refused_quarantine_keeps_the_original_and_every_operation_fails_closed(monkeypatch, damaged) -> None:
    """NOVEL path for the review's class: the quarantine rename is refused while the damaged
    store is reached through the public operations (not only `_load`). No operation writes,
    the bytes stay, no half-made quarantine or temp file is left, and each operation reports
    the structured recovery state instead of pretending the store is empty."""
    path = _write_store(damaged)
    real_rename = type(path).rename

    def refuse(self, target):
        if self == path:
            raise PermissionError("quarantine rename refused")
        return real_rename(self, target)

    monkeypatch.setattr(type(path), "rename", refuse)
    saved = email_drafts.save_draft(to="new@quarry.example.test", subject="After damage", body="b",
                                    account="default", session_id=SESSION)
    assert not saved.ok and saved.status == "store_recovery_required", (saved.status, saved.message)
    recovery = saved.details["store_recovery"]
    assert recovery["sha256"] == hashlib.sha256(damaged).hexdigest() and recovery["quarantine"] == ""
    assert recovery["unresolved_markers"]
    outcomes = [email_drafts.get_draft(session_id=SESSION, latest=True),
                email_drafts.approve_draft("ed-lost", session_id=SESSION),
                email_drafts.send_draft("ed-lost", session_id=SESSION),
                email_drafts.reconcile_draft("ed-lost", session_id=SESSION)]
    assert [o.status for o in outcomes] == ["store_recovery_required"] * 4
    assert path.read_bytes() == damaged
    assert _quarantines(path) == [] and not list(path.parent.glob("*.tmp"))


def test_s1_persistent_filesystem_refusal_survives_a_restart_and_recovery_follows_the_fix(tmp_path) -> None:
    """[real-fs][real-proc] A real, persistent fault: the store directory refuses new entries.
    A fresh process cannot quarantine and reports store_recovery_required with the bytes
    untouched. Once the directory is writable again, the next process recovers durably."""
    path = _write_store(_TORN_UNRESOLVED)
    (path.parent / "drafts.lock").touch()
    os.chmod(path.parent, 0o555)
    try:
        refused = subprocess.run([sys.executable, "-B", "-c", _PROCESS_DRIVER], cwd=str(REPO_ROOT),
                                 env=_driver_env(), capture_output=True, text=True, timeout=60)
        assert _driver_result(refused.stdout)["status"] == "store_recovery_required", refused.stdout + refused.stderr
        assert path.read_bytes() == _TORN_UNRESOLVED and _quarantines(path) == []
    finally:
        os.chmod(path.parent, 0o755)
    recovered = subprocess.run([sys.executable, "-B", "-c", _PROCESS_DRIVER], cwd=str(REPO_ROOT),
                               env=_driver_env(), capture_output=True, text=True, timeout=60)
    report = _driver_result(recovered.stdout)
    assert report["status"] == "not_found", recovered.stdout + recovered.stderr
    quarantined = _quarantines(path)
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == _TORN_UNRESOLVED
    record = json.loads(path.read_text(encoding="utf-8"))["recovery"]
    assert record["quarantine"] == quarantined[0].name and record["send_hold"] is True
    assert report["store_recovery"]["quarantine"] == quarantined[0].name


def test_s1_replacement_failure_after_the_quarantine_rename_rolls_the_evidence_back(monkeypatch) -> None:
    """The quarantine rename succeeds but installing the replacement store fails: the
    quarantined bytes are renamed back, so the original sits where it was and no orphan
    quarantine remains."""
    path = _write_store(_MISSHAPEN_UNRESOLVED)
    real_replace = type(path).replace

    def refuse(self, target):
        if Path(target) == path:
            raise OSError("replacement refused")
        return real_replace(self, target)

    monkeypatch.setattr(type(path), "replace", refuse)
    result = email_drafts.get_draft(session_id=SESSION, latest=True)
    assert result.status == "store_recovery_required", (result.status, result.message)
    assert path.read_bytes() == _MISSHAPEN_UNRESOLVED
    assert _quarantines(path) == [] and not list(path.parent.glob("*.tmp"))


def test_s1_interrupted_recovery_holds_sends_until_the_operator_acknowledges_the_quarantine(monkeypatch) -> None:
    """Both the replacement AND its rollback fail: the evidence exists only as a quarantine
    file and there is no store. That must never read as an empty store: the next load
    persists a send hold naming the quarantine, ordinary sends are held, and only an
    explicit, confirmed acknowledgement of that exact file lifts the hold."""
    path = _write_store(_TORN_UNRESOLVED)
    cls = type(path)
    real_replace, real_rename = cls.replace, cls.rename

    def refuse_replace(self, target):
        if Path(target) == path:
            raise OSError("replacement refused")
        return real_replace(self, target)

    def refuse_rollback(self, target):
        if Path(target) == path:
            raise OSError("rollback refused")
        return real_rename(self, target)

    monkeypatch.setattr(cls, "replace", refuse_replace)
    monkeypatch.setattr(cls, "rename", refuse_rollback)
    interrupted = email_drafts.get_draft(session_id=SESSION, latest=True)
    assert interrupted.status == "store_recovery_required", (interrupted.status, interrupted.message)
    orphans = _quarantines(path)
    assert not path.exists() and len(orphans) == 1 and orphans[0].read_bytes() == _TORN_UNRESOLVED

    monkeypatch.setattr(cls, "replace", real_replace)
    monkeypatch.setattr(cls, "rename", real_rename)
    listing = email_drafts.get_draft(session_id=SESSION, latest=True)
    assert listing.status == "not_found"
    record = json.loads(path.read_text(encoding="utf-8"))["recovery"]
    assert record["send_hold"] is True and record["quarantine"] == orphans[0].name
    assert "delivery_unknown" in record["unresolved_markers"]
    assert "<lost-7@quarry.example.test>" in record["message_ids_in_damaged_store"]

    did = _unsendable_compose("Held while effects are unresolved")
    held = email_drafts.send_draft(did, session_id=SESSION)
    assert not held.ok and held.status == "store_recovery_hold", (held.status, held.message)
    assert "<lost-7@quarry.example.test>" in held.message
    assert held.details["store_recovery"]["quarantine"] == orphans[0].name

    assert email_drafts.acknowledge_store_recovery(orphans[0].name).status == "confirmation_required"
    assert email_drafts.acknowledge_store_recovery("drafts.corrupt-0.json", confirm=True).status == "quarantine_mismatch"
    assert email_drafts.send_draft(did, session_id=SESSION).status == "store_recovery_hold"
    acknowledged = email_drafts.acknowledge_store_recovery(orphans[0].name, confirm=True)
    assert acknowledged.ok and acknowledged.status == "acknowledged", (acknowledged.status, acknowledged.message)
    released = email_drafts.send_draft(did, session_id=SESSION)
    assert released.status == "needs_credentials", (released.status, released.message)
    assert orphans[0].read_bytes() == _TORN_UNRESOLVED


@pytest.mark.parametrize("damaged", [_TORN_UNRESOLVED, _MISSHAPEN_UNRESOLVED], ids=["parse-invalid", "schema-invalid"])
def test_s1_durable_recovery_preserves_the_bytes_and_holds_ordinary_sends(damaged) -> None:
    """The happy recovery path with unresolved effects: the original bytes are in exactly
    one quarantine file (hash-verified in the record), the salvaged reservation identities
    are listed, saving and approving still work, and sends are held."""
    path = _write_store(damaged)
    did = _unsendable_compose("Draft written during recovery")
    quarantined = _quarantines(path)
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == damaged
    record = json.loads(path.read_text(encoding="utf-8"))["recovery"]
    assert record["sha256"] == hashlib.sha256(damaged).hexdigest() and record["bytes"] == len(damaged)
    assert record["quarantine"] == quarantined[0].name and record["send_hold"] is True
    assert record["unresolved_markers"] and record["message_ids_in_damaged_store"]
    held = email_drafts.send_draft(did, session_id=SESSION)
    assert held.status == "store_recovery_hold", (held.status, held.message)
    assert email_drafts.get_draft(did, session_id=SESSION).details["store_recovery"]["send_hold"] is True


def test_s1_two_processes_recovering_the_same_damaged_store_quarantine_it_once(tmp_path) -> None:
    """[real-proc] Concurrent recovery: two fresh processes load the damaged store at the same
    instant. The cross-process lock serializes them: one quarantine, one recovery record, and
    both processes see the recovered store."""
    path = _write_store(_MISSHAPEN_UNRESOLVED)
    env = _driver_env(START_AT=str(time.time() + 1.5))
    workers = [subprocess.Popen([sys.executable, "-B", "-c", _PROCESS_DRIVER], cwd=str(REPO_ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [worker.communicate(timeout=90) for worker in workers]
    reports = [_driver_result(out) for out, _err in outputs]
    assert [r["status"] for r in reports] == ["not_found", "not_found"], outputs
    quarantined = _quarantines(path)
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == _MISSHAPEN_UNRESOLVED
    record = json.loads(path.read_text(encoding="utf-8"))["recovery"]
    assert record["quarantine"] == quarantined[0].name
    assert {r["store_recovery"]["quarantine"] for r in reports} == {quarantined[0].name}


def test_s1_save_never_replaces_damaged_bytes_it_did_not_quarantine() -> None:
    """Defence in depth at the writer: bytes that are damaged when a write is about to replace
    them are refused, not overwritten."""
    path = _write_store(b'{"drafts": ')
    with pytest.raises(email_drafts.DraftStoreRecoveryRequiredError):
        email_drafts._save({"version": 1, "drafts": {}})
    assert path.read_bytes() == b'{"drafts": '


def test_s1_recovery_without_unresolved_effects_does_not_hold_sends() -> None:
    """CONTROL: a damaged store that named no reservation is quarantined and recorded, but
    ordinary sends are not held."""
    path = _write_store(b'{"version": 1, "drafts": {"ed-1": 7}}')
    did = _unsendable_compose("No hold expected")
    record = json.loads(path.read_text(encoding="utf-8"))["recovery"]
    assert record["send_hold"] is False and record["unresolved_markers"] == []
    assert len(_quarantines(path)) == 1
    sent = email_drafts.send_draft(did, session_id=SESSION)
    assert sent.status == "needs_credentials", (sent.status, sent.message)


def test_s1_the_tool_result_carries_the_structured_recovery_state() -> None:
    """[component] The email.draft tool result a turn renders carries the hold status and the
    structured recovery record (quarantine name, markers, salvaged Message-IDs), not only prose."""
    from core import runtime_execution_tools

    path = _write_store(_TORN_UNRESOLVED)
    did = _unsendable_compose("Tool-layer recovery probe")
    context = {"runtime_session_id": SESSION, "session_id": SESSION}
    result = runtime_execution_tools._email_draft_tool("email.draft.send", {"draft_id": did}, context)
    assert not result.ok and result.status == "store_recovery_hold", (result.status, result.response_text)
    recovery = result.details["store_recovery"]
    assert recovery["send_hold"] is True and recovery["quarantine"] == _quarantines(path)[0].name
    assert "<lost-7@quarry.example.test>" in recovery["message_ids_in_damaged_store"]
    assert "<lost-7@quarry.example.test>" in result.response_text


# ---------------------------------------------------------------------------
# C1 — the email tool evidence a model receives is enough to act on
# ---------------------------------------------------------------------------
#
# Found while driving the served journey (evidence/served-discovery-turn1-*.json): after
# email.read executed against the provider, the ONLY thing the model received was
# "1 message(s) matched (Gmail)." No sender, subject or message id. A real model therefore
# cannot open the right thread, ground a reply, or review an exact draft; the v4 component
# journeys hid it by scripting ids the model never saw. These tests drive the public runtime
# tool door and check the model-visible observation and its rendered prompt block.

@pytest.fixture
def email_policy():
    from core import policy_engine

    previous = policy_engine._POLICY_CACHE
    base = dict(policy_engine.load())
    base["email"] = {**(base.get("email") or {}), "read_enabled": True, "send_enabled": True}
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous


def _model_view(execution, intent: str) -> tuple[dict, str]:
    """(observation payload, rendered prompt block) exactly as the tool loop builds them."""
    from core.agent_runtime.response_policy_tool_history import tool_history_observation_payload
    from core.prompt_normalizer import _runtime_tool_observation_message

    payload = tool_history_observation_payload(execution=execution, tool_name=intent)
    message = _runtime_tool_observation_message({"runtime_tool_observations": [payload]})
    assert message is not None
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return payload, str(content if content is not None else message)


def _harvest_inbox(api: ProviderApiServer, principal: str, count: int = 2) -> list[dict]:
    entries = [{"from": f"Cooperative {n} <grower{n}@cooperative.example.test>",
                "subject": f"Harvest window {n}", "body": f"Pick-up slot {n}: Thursday {8 + n}:00.",
                "message_id": f"<harvest-{n}@cooperative.example.test>",
                "date": f"Fri, 11 Sep 2026 0{n % 10}:00:00 +0000"} for n in range(1, count + 1)]
    api.gmail_inbox(principal, entries)
    return api.state.gmail_mailboxes[principal]


def test_c1_read_observation_carries_the_messages_a_model_must_act_on(api, email_policy) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "desk@harvest.example.test"
    _connect(api, "gmail", "harvest", principal)
    models = _harvest_inbox(api, principal)
    result = execute_runtime_tool("email.read", {"account": "harvest", "sender": "cooperative"})
    assert result is not None and result.ok, (result.status if result else None, result.response_text if result else None)
    payload, block = _model_view(result, "email.read")
    seen = {m["provider_id"]: m for m in payload["messages"]}
    assert set(seen) == {m["id"] for m in models}
    for model in models:
        entry = seen[model["id"]]
        headers = {h["name"]: h["value"] for h in model["payload"]["headers"]}
        assert entry["subject"] == headers["Subject"] and entry["message_id"] == headers["Message-ID"]
        assert entry["from"] == headers["From"] and entry["thread_id"] == model["threadId"]
        assert model["id"] in block and headers["Subject"] in block


def test_c1_open_observation_carries_the_body_and_threading_headers(api, email_policy) -> None:
    """NOVEL provider: a Graph message opened through the door delivers its body and headers."""
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "desk@kilnworks.example.test"
    _connect(api, "graph", "kilnworks", principal)
    _graph_parent(api, principal, message_id="<estimate-8442@repair.example.test>",
                  subject="Kiln repair estimate 8442", sender="estimator@repair.example.test")
    model = api.state.graph_mailboxes[principal][0]
    result = execute_runtime_tool("email.open", {"account": "kilnworks", "message_id": model["id"]})
    assert result is not None and result.ok, (result.status, result.response_text)
    payload, block = _model_view(result, "email.open")
    opened = payload["messages"][0]
    assert opened["provider_id"] == model["id"] and opened["body"] == "Parent body."
    assert opened["message_id"] == "<estimate-8442@repair.example.test>"
    assert opened["subject"] == "Kiln repair estimate 8442" and "Parent body." in block


def test_c1_draft_observations_carry_the_exact_reviewable_message(api, email_policy) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "desk@orchardlane.example.test"
    _connect(api, "gmail", "orchardlane", principal)
    context = {"runtime_session_id": SESSION, "session_id": SESSION}
    saved = execute_runtime_tool("email.draft.save", {
        "account": "orchardlane", "to": "supply@willowridge.example.test", "subject": "Delivery scheduling",
        "body": "Wednesday at 10:00 works for our delivery.", "in_reply_to": "<wro-1@willowridge.example.test>",
        "kind": "reply"}, source_context=context)
    assert saved is not None and saved.ok, (saved.status, saved.response_text)
    payload, block = _model_view(saved, "email.draft.save")
    draft = payload["draft"]
    assert draft["version"] == 1 and draft["status"] == "draft" and draft["approved"] is False
    assert draft["to"] == ["supply@willowridge.example.test"] and draft["subject"] == "Re: Delivery scheduling"
    assert draft["body"] == "Wednesday at 10:00 works for our delivery." and draft["account"] == "orchardlane"
    assert draft["in_reply_to"] == "<wro-1@willowridge.example.test>" and draft["draft_id"] in block

    edited = execute_runtime_tool("email.draft.save", {
        "draft_id": draft["draft_id"], "account": "orchardlane", "to": "supply@willowridge.example.test",
        "subject": "Delivery scheduling", "body": "Wednesday 10:00 confirmed.",
        "in_reply_to": "<wro-1@willowridge.example.test>", "kind": "reply"}, source_context=context)
    edited_payload, edited_block = _model_view(edited, "email.draft.save")
    assert edited_payload["draft"]["version"] == 2 and edited_payload["draft"]["body"] == "Wednesday 10:00 confirmed."
    assert "Wednesday 10:00 confirmed." in edited_block
    got = execute_runtime_tool("email.draft.get", {"draft_id": draft["draft_id"]}, source_context=context)
    assert _model_view(got, "email.draft.get")[0]["draft"]["body"] == "Wednesday 10:00 confirmed."


def test_c1_send_observation_carries_the_receipt(api, email_policy) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "desk@saltpan.example.test"
    _connect(api, "gmail", "saltpan", principal)
    did = _approved_draft("saltpan", to="buyer@brine.example.test", subject="Lot 9 ready", body="Lot 9 is packed.")
    context = {"runtime_session_id": SESSION, "session_id": SESSION}
    sent = execute_runtime_tool("email.draft.send", {"draft_id": did}, source_context=context)
    assert sent is not None and sent.ok and sent.status == "sent", (sent.status, sent.response_text)
    payload, block = _model_view(sent, "email.draft.send")
    receipt = payload["receipt"]
    assert receipt["outcome"] == "executed" and receipt["to"] == ["buyer@brine.example.test"]
    assert receipt["account"] == "saltpan" and receipt["verified_principal"] == principal
    assert receipt["message_id"] and receipt["message_id"] in block
    assert api.sent_count("gmail", principal) == 1


def test_c1_evidence_is_bounded_and_says_what_it_leaves_out(api, email_policy) -> None:
    """A large match set is capped with an explicit omitted count, and a long body is clipped with
    an explicit notice: bounded context, never a silent cut."""
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "desk@bulkfarm.example.test"
    _connect(api, "gmail", "bulkfarm", principal)
    _harvest_inbox(api, principal, count=26)
    result = execute_runtime_tool("email.read", {"account": "bulkfarm", "sender": "cooperative", "limit": 26})
    payload, _block = _model_view(result, "email.read")
    assert result.ok and len(result.details["messages"]) == 26
    assert len(payload["messages"]) == 20 and payload["messages_omitted"] == 6

    long_body = "Delivery terms. " * 600
    api.gmail_inbox(principal, [{"from": "terms@cooperative.example.test", "subject": "Contract terms",
                                 "body": long_body, "message_id": "<terms-1@cooperative.example.test>",
                                 "date": "Fri, 11 Sep 2026 10:00:00 +0000"}])
    model = api.state.gmail_mailboxes[principal][0]
    opened = execute_runtime_tool("email.open", {"account": "bulkfarm", "message_id": model["id"]})
    body = _model_view(opened, "email.open")[0]["messages"][0]["body"]
    assert len(body) < len(long_body) and "not shown" in body
    assert body.startswith("Delivery terms.")


def test_c1_a_failed_read_delivers_no_invented_messages(api, email_policy) -> None:
    """NEGATIVE: an unreadable mailbox result reaches the model as a failure with no messages."""
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "desk@deadmail.example.test"
    _connect(api, "gmail", "deadmail", principal)
    api.refuse_every_token("gmail", "deadmail", method="GET", path_suffix="/messages")
    result = execute_runtime_tool("email.read", {"account": "deadmail", "sender": "anyone"})
    payload, _block = _model_view(result, "email.read")
    assert not result.ok and payload["status"] == "needs_reauthorization"
    assert "messages" not in payload


# ---------------------------------------------------------------------------
# C2 — same-session follow-ups keep the email tools their durable work needs
# ---------------------------------------------------------------------------
#
# Found driving the served conversation under SHIPPED routing
# (evidence/served-discovery-shipped-routing-20260914-133440.json): turn 1 "Check my email from the
# orchard supplier on my Google account." executed email.read against the provider; turn 2 "Open
# that delivery thread and show me what they wrote." classified `unknown`, was kept in the
# tools-less chat lane (output_mode plain_text) and was answered with no tool. Nothing on a
# follow-up's path -- the chat-lane keep, the planner's tool admission or the tool offer --
# consulted durable evidence that the session was in the middle of email work, so every follow-up
# that names no mailbox ("add that ...", "read me the final version", "approve it and send it")
# depended on keyword luck. These tests drive those three seams with the session's REAL durable
# state: the execution ledger written through the runtime event choke point, and drafts written
# through the public tool door or the draft API.

_EMAIL_REVIEW_SEATS = {"email.draft.get", "email.draft.save", "email.draft.approve", "email.draft.send"}


@pytest.fixture
def chat_agent():
    from core.agent_runtime.agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def _session(tag: str) -> str:
    return f"openclaw:c2-{tag}-{uuid.uuid4().hex[:10]}"


def _chat_context(session: str) -> dict:
    return {"surface": "openclaw", "platform": "openclaw", "runtime_session_id": session,
            "session_id": session, "turn_id": f"turn-{uuid.uuid4().hex[:12]}"}


def _ledger_tool(session: str, intent: str, *, ok: bool = True) -> None:
    """Record one tool execution the way the tool loop does: a runtime event through the choke
    point that writes the authoritative execution ledger."""
    from core.execution_truth import facts_for_turn
    from core.runtime_task_events import emit_runtime_event

    context = _chat_context(session)
    emit_runtime_event(context, event_type="tool_executed" if ok else "tool_failed",
                       message=f"{intent} {'executed' if ok else 'failed'}",
                       details={"tool_name": intent, "status": "executed" if ok else "failed"})
    assert any(fact.name == intent for fact in facts_for_turn(context["turn_id"], session_id=session)), \
        "the ledger did not record the execution; the test would prove nothing"


def _kept_in_chat_lane(agent, text: str, context: dict) -> bool:
    from types import SimpleNamespace

    from core.agent_runtime import runtime_checkpoint_lane_policy

    return runtime_checkpoint_lane_policy.should_keep_ai_first_chat_lane(
        agent, user_input=text, classification={"task_class": "unknown"},
        interpretation=SimpleNamespace(as_context=lambda: {}, topic_hints=[]),
        source_context=dict(context), checkpoint_state={})


def _admitted(text: str, context: dict) -> bool:
    from core.execution.planner import should_attempt_tool_intent

    return bool(should_attempt_tool_intent(text, task_class="unknown", source_context=dict(context)))


def _seated(text: str, context: dict) -> set[str]:
    from core.tool_offer_assembly import assemble_tool_offer

    return set(assemble_tool_offer(user_text=text, task_class="unknown", source_context=dict(context)).intents)


def test_c2_the_served_follow_up_after_an_email_read_leaves_the_chat_lane_with_email_seats(
        chat_agent, email_policy) -> None:
    """ORIGINAL: the exact served turn-2 wording, after turn 1's email.read is in the ledger."""
    session = _session("orchard")
    follow_up = "Open that delivery thread and show me what they wrote."
    turn2 = _chat_context(session)
    # Control: with no email work in the session the ordinary lane decision is unchanged.
    assert _kept_in_chat_lane(chat_agent, follow_up, turn2) is True
    baseline = _seated(follow_up, turn2)

    _ledger_tool(session, "email.read")
    assert _kept_in_chat_lane(chat_agent, follow_up, turn2) is False
    assert _admitted(follow_up, turn2) is True
    seats = _seated(follow_up, turn2)
    assert {"email.open", "email.read", "email.draft.save", "email.draft.get"} <= seats, (baseline, seats)


def test_c2_novel_graph_draft_stages_decide_the_email_seats_through_send_and_after(
        api, chat_agent, email_policy, monkeypatch) -> None:
    """NOVEL: a Graph compose draft driven through the public tool door. Each durable stage
    (drafting, approved, dispatched) admits the follow-up and seats the tools that stage needs;
    a finished send stops admitting once its recent window closes."""
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "studio@kilnworks.example.test"
    _connect(api, "graph", "kilnworks", principal)
    session = _session("kiln")
    ctx = _chat_context(session)
    add_line = "Add that the kiln is at the north studio."
    # Control: the wording alone reaches no tool and no review seat.
    assert _admitted(add_line, ctx) is False and _kept_in_chat_lane(chat_agent, add_line, ctx) is True
    assert not _EMAIL_REVIEW_SEATS & _seated(add_line, ctx)

    saved = execute_runtime_tool("email.draft.save", {
        "account": "kilnworks", "to": "estimator@repair.example.test", "subject": "Kiln repair estimate 8442",
        "body": "We accept the estimate if the work is done before Friday."}, source_context=ctx)
    assert saved is not None and saved.ok, (saved.status, saved.response_text)
    did = saved.details["draft"]["draft_id"]
    assert _kept_in_chat_lane(chat_agent, add_line, ctx) is False
    assert _admitted(add_line, ctx) is True
    assert _seated(add_line, ctx) >= _EMAIL_REVIEW_SEATS

    approved = execute_runtime_tool("email.draft.approve", {"draft_id": did}, source_context=ctx)
    assert approved is not None and approved.ok, (approved.status, approved.response_text)
    send_now = "Looks good. Approve it and send it."
    assert _admitted(send_now, ctx) is True
    assert {"email.draft.send", "email.draft.get"} <= _seated(send_now, ctx)

    sent = execute_runtime_tool("email.draft.send", {"draft_id": did}, source_context=ctx)
    assert sent is not None and sent.ok and sent.status == "sent", (sent.status, sent.response_text)
    went_out = "Did that email actually go out?"
    assert _kept_in_chat_lane(chat_agent, went_out, ctx) is False and _admitted(went_out, ctx) is True
    assert "email.draft.reconcile" in _seated(went_out, ctx)

    from core import email_work_state

    later = time.time() + email_work_state.RECENT_EMAIL_WORK_SECONDS + 60
    monkeypatch.setattr(email_work_state, "_now", lambda: later)
    assert _admitted(went_out, ctx) is False and _kept_in_chat_lane(chat_agent, went_out, ctx) is True


def test_c2_email_work_admits_only_its_own_session_and_principal(chat_agent, email_policy) -> None:
    """NEGATIVE: another session's ledger or drafts, another principal's draft under the same
    session id, and a session whose recent tools were not email tools admit nothing."""
    line = "Add that the kiln is at the north studio."
    worked = _session("worked")
    _ledger_tool(worked, "email.open")
    assert email_drafts.save_draft(to="estimator@repair.example.test", subject="Kiln estimate",
                                   body="Accepted.", account="kilnworks", session_id=worked).ok
    assert _admitted(line, _chat_context(worked)) is True  # control: the work exists

    stranger = _chat_context(_session("stranger"))
    assert _admitted(line, stranger) is False and _kept_in_chat_lane(chat_agent, line, stranger) is True
    assert not _EMAIL_REVIEW_SEATS & _seated(line, stranger)

    shared = _session("shared")
    assert email_drafts.save_draft(to="estimator@repair.example.test", subject="Kiln estimate", body="Accepted.",
                                   account="kilnworks", session_id=shared, principal="channel:telegram:4242").ok
    assert _admitted(line, _chat_context(shared)) is False

    busy = _session("busy")
    _ledger_tool(busy, "workspace.list_tree")
    assert _admitted(line, _chat_context(busy)) is False


def test_c2_email_work_expires_with_its_own_windows(chat_agent, email_policy, monkeypatch) -> None:
    """NEGATIVE: a recent email tool admits follow-ups for the recent window only; an unfinished
    draft keeps its session's work open for the open-work window, and then stops admitting."""
    session = _session("windows")
    ctx = _chat_context(session)
    line = "Read me the final version before it goes."
    _ledger_tool(session, "email.read")
    assert _admitted(line, ctx) is True

    from core import email_work_state

    start = time.time()
    monkeypatch.setattr(email_work_state, "_now", lambda: start + email_work_state.RECENT_EMAIL_WORK_SECONDS + 60)
    assert _admitted(line, ctx) is False and _kept_in_chat_lane(chat_agent, line, ctx) is True

    assert email_drafts.save_draft(to="buyer@brine.example.test", subject="Lot 9 ready", body="Lot 9 is packed.",
                                   account="saltpan", session_id=session).ok
    assert _admitted(line, ctx) is True and _seated(line, ctx) >= _EMAIL_REVIEW_SEATS
    monkeypatch.setattr(email_work_state, "_now", lambda: start + email_work_state.OPEN_EMAIL_WORK_SECONDS + 60)
    assert _admitted(line, ctx) is False and not _EMAIL_REVIEW_SEATS & _seated(line, ctx)


def test_c2_user_stated_constraints_still_outrank_email_work(chat_agent, email_policy) -> None:
    """NEGATIVE: a tool prohibition, a stipulated hypothetical and a surface that cannot run tools
    keep their decisions while email work is open in the session."""
    session = _session("constraints")
    ctx = _chat_context(session)
    _ledger_tool(session, "email.read")
    assert email_drafts.save_draft(to="supply@willowridge.example.test", subject="Delivery scheduling",
                                   body="Wednesday at 10:00 works.", account="orchardlane", session_id=session).ok
    assert _admitted("Add that the kiln is at the north studio.", ctx) is True  # control: work is open

    no_tools = "Don't use any tools, just tell me in one line what we were doing."
    assert _admitted(no_tools, ctx) is False and _kept_in_chat_lane(chat_agent, no_tools, ctx) is True
    hypothetical = "Imagine you had no access to my mailbox. What would you say to the supplier?"
    assert _admitted(hypothetical, ctx) is False and _kept_in_chat_lane(chat_agent, hypothetical, ctx) is True
    no_tool_surface = {**ctx, "surface": "webhook", "platform": "webhook"}
    assert _admitted("Add that the kiln is at the north studio.", no_tool_surface) is False


def test_c2_an_unresolved_send_stays_reconcilable_beside_a_newer_draft(api, email_policy, monkeypatch) -> None:
    """An uncertain send (delivery_unknown) keeps reconciliation seated for its open-work window,
    even after the recent-tool window, while a newer draft in the same session is being edited."""
    principal = "books@loom.example.test"
    _connect(api, "gmail", "loom", principal)
    session = _session("loom")
    did = _approved_draft("loom", to="printer@press.example.test", subject="Proof run 4",
                          body="Approve 300 copies on linen stock.", session=session)
    api.drop_after_accept = True
    assert email_drafts.send_draft(did, session_id=session).status == "delivery_unknown"
    api.drop_after_accept = False
    assert email_drafts.save_draft(to="printer@press.example.test", subject="Proof run 5",
                                   body="Hold run 5 until run 4 is confirmed.", account="loom", session_id=session).ok
    question = "Where do things stand with the print shop?"
    wanted = {"email.draft.reconcile", "email.draft.save", "email.draft.get"}
    assert wanted <= _seated(question, _chat_context(session))

    from core import email_work_state

    start = time.time()
    monkeypatch.setattr(email_work_state, "_now", lambda: start + email_work_state.RECENT_EMAIL_WORK_SECONDS + 60)
    assert wanted <= _seated(question, _chat_context(session))


# ---------------------------------------------------------------------------
# C3 — the email tools a model is offered are the ones it can run, with the arguments they accept
# ---------------------------------------------------------------------------
#
# Found driving the served conversation (evidence/served-conversation-original-gmail-discovery1-*.json,
# edit turn): the model re-saved the draft it had just been shown, copying `"to": [...]` from the
# draft evidence, and the provider-response parser refused the call -- "native tool argument to has
# type array, expected ['string']" -- although email.draft.save's contract declares
# `"to": "string|list[string]"` and the handler accepts both. The native schema translator read only
# the hint's leading word, so every declared type union reached models as its first member.
# Found in the routing-affected comparison (evidence/30-c2-routing-affected.log against
# 32-attribution-base-df49f096.log, bisected in 34-bisect-navigator-*.log): since 492bed6d the draft
# tools were offered as runnable under a policy that disables email, so
# tests/test_p0_tool_offer_authority.py::test_navigator_keeps_disabled_tools_as_unavailable_metadata
# is red at every email revision and green at the base.


def _offered_definition(intent: str):
    from core.cloud_tool_call_contract import build_cloud_tool_definitions
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contract = runtime_tool_contract_map()[intent]
    return build_cloud_tool_definitions([{"intent": intent, "description": contract.description,
                                          "arguments": dict(contract.input_schema)}])[0]


def _native_call(definition, **arguments) -> list[dict]:
    return [{"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
             "function": {"name": definition.name, "arguments": json.dumps(arguments)}}]


def test_c3_the_served_edit_call_parses_and_a_wrong_type_is_still_refused(email_policy) -> None:
    """ORIGINAL: the exact edit call the served model made parses, a single recipient still
    parses, and a recipient of the wrong type is still refused before anything runs."""
    from core.cloud_tool_call_contract import parse_native_tool_calls

    definition = _offered_definition("email.draft.save")
    # anyOf is the union form the strict tool-schema subset documents (nested, never at the root); a
    # bare type list is documented only as the nullable form (Azure OpenAI structured outputs,
    # "JSON Schema support and limitations", updated 2026-08-24).
    assert definition.parameters["properties"]["to"]["anyOf"] == [
        {"type": "string"}, {"type": "array", "items": {"type": "string"}}]
    served_edit = {"draft_id": "ed-8f5a2e7d70c5", "kind": "reply", "to": ["supply@willowridge-orchard.example.test"],
                   "subject": "Re: Delivery scheduling", "body": "Wednesday 10:00 is confirmed. Please use the north gate.",
                   "in_reply_to": "<wro-20260910@willowridge-orchard.example.test>",
                   "references": ["<wro-20260910@willowridge-orchard.example.test>"]}
    parsed = parse_native_tool_calls(_native_call(definition, **served_edit), definitions=(definition,))
    assert parsed[0].intent == "email.draft.save" and parsed[0].arguments["to"] == served_edit["to"]
    single = {**served_edit, "to": "supply@willowridge-orchard.example.test"}
    assert parse_native_tool_calls(_native_call(definition, **single), definitions=(definition,))[0].arguments["to"] == single["to"]
    with pytest.raises(ValueError, match="native tool argument to has type integer"):
        parse_native_tool_calls(_native_call(definition, **{**served_edit, "to": 42}), definitions=(definition,))

    # Semantic admission reads the same contract prose and must accept the same call.
    from core.runtime_tool_contracts import runtime_tool_contract_map
    from core.semantic.executable_schema import schema_from_contract, validate_arguments

    admission = schema_from_contract("email.draft.save", runtime_tool_contract_map()["email.draft.save"].input_schema)
    assert not [problem for problem in validate_arguments(admission, served_edit) if "'to'" in problem]
    assert not [problem for problem in validate_arguments(admission, single) if "'to'" in problem]
    assert [problem for problem in validate_arguments(admission, {**served_edit, "to": 42}) if "'to'" in problem]


def test_c3_novel_type_unions_translate_and_value_lists_keep_their_single_type() -> None:
    """NOVEL hints: an object-or-string union, a list-first union and an optional union translate
    to their member types; NEGATIVE: a value list ("normal|dark-null") is not a type union, and
    plain hints keep exactly the schema they had."""
    from core.cloud_tool_call_contract import build_cloud_tool_definitions

    definition = build_cloud_tool_definitions([{"intent": "c3.probe", "arguments": {
        "body": "dict|str (optional)", "recipients": "list[string]|string",
        "privacy_path": "normal|dark-null optional", "count": "integer", "note": "string optional",
        "paths": "list[string]"}}])[0]
    props = definition.parameters["properties"]
    assert props["body"]["anyOf"] == [{"type": "object", "additionalProperties": True}, {"type": "string"}, {"type": "null"}]
    assert props["recipients"]["anyOf"] == [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]
    assert "type" not in props["body"] and "type" not in props["recipients"]
    assert props["privacy_path"]["type"] == ["string", "null"] and "items" not in props["privacy_path"]
    assert props["count"]["type"] == "integer" and props["note"]["type"] == ["string", "null"]
    assert props["paths"]["type"] == "array" and props["paths"]["items"] == {"type": "string"}

    from core.cloud_tool_call_contract import parse_native_tool_calls

    for body in ({"lot": "9"}, "plain text", None):
        parse_native_tool_calls(_native_call(definition, recipients=["buyer@brine.example.test"], count=1,
                                             paths=["a"], body=body), definitions=(definition,))
    with pytest.raises(ValueError, match="native tool argument body has type integer"):
        parse_native_tool_calls(_native_call(definition, recipients="buyer@brine.example.test", count=1,
                                             paths=[], body=7), definitions=(definition,))


def test_c3_a_policy_that_disables_email_runs_admits_and_seats_no_email_tool(chat_agent) -> None:
    """NEGATIVE: under a policy that disables email no email intent is runnable -- the draft tools
    exist to be sent and share the send switch -- so durable email work (a recent email tool and an
    open draft) neither admits the turn, releases the chat lane nor seats an email tool."""
    from core.runtime_tool_contracts import runtime_tool_contract_map

    runnable = sorted(intent for intent, contract in runtime_tool_contract_map().items()
                      if intent.startswith("email.") and contract.supported)
    assert runnable == []
    session = _session("disabled")
    ctx = _chat_context(session)
    _ledger_tool(session, "email.read")
    assert email_drafts.save_draft(to="supply@willowridge.example.test", subject="Delivery scheduling",
                                   body="Wednesday at 10:00 works.", account="orchard", session_id=session).ok
    line = "Add that the kiln is at the north studio."
    assert _admitted(line, ctx) is False and _kept_in_chat_lane(chat_agent, line, ctx) is True
    assert not {intent for intent in _seated(line, ctx) if intent.startswith("email.")}


def test_c3_novel_a_read_only_email_policy_seats_reading_tools_and_no_draft_tool(chat_agent) -> None:
    """NOVEL policy split: reading enabled, sending disabled. The session's email work seats the
    reading tools; the draft tools, which cannot lead to a send here, are neither runnable nor seated."""
    from core import policy_engine
    from core.runtime_tool_contracts import runtime_tool_contract_map

    previous = policy_engine._POLICY_CACHE
    policy = dict(policy_engine.load())
    policy["email"] = {**(policy.get("email") or {}), "read_enabled": True, "send_enabled": False}
    policy_engine._POLICY_CACHE = policy
    try:
        contracts = runtime_tool_contract_map()
        assert contracts["email.read"].supported and contracts["email.open"].supported
        assert not any(contracts[intent].supported for intent in _EMAIL_REVIEW_SEATS)
        session = _session("readonly")
        ctx = _chat_context(session)
        _ledger_tool(session, "email.read")
        follow_up = "Open that delivery thread and show me what they wrote."
        seats = {intent for intent in _seated(follow_up, ctx) if intent.startswith("email.")}
        assert {"email.open", "email.read"} <= seats and not seats & _EMAIL_REVIEW_SEATS, seats
        assert _admitted("Add that the kiln is at the north studio.", ctx) is True
    finally:
        policy_engine._POLICY_CACHE = previous


# ---------------------------------------------------------------------------
# C4 — a mailbox folder in an email conversation is not a disk search
# ---------------------------------------------------------------------------
#
# Found driving the served conversation (evidence/served-conversation-original-gmail-discovery1-*.json,
# reconcile turn) and diagnosed with a thread dump (evidence/served-hang-repro-unresolved-approval-*.json):
# after the reply had been sent, "Did it actually go out? Check the sent folder." was claimed before
# any model by the direct machine-read fast path, which called machine.find_folder {"name": "sent"}
# and walked this machine's drives while the user waited on their email. The discovery turn timed
# out at 300 s; the repro daemon was still inside _machine_find_folder after 60 s.


class _FolderFastPathAgent:
    def _emit_runtime_event(self, source_context, *, event_type, message, **details):
        return None

    def _fast_path_result(self, **kwargs):
        return {"response": kwargs.get("response", ""), "reason": kwargs.get("reason", "")}


def _recording_machine_tools(monkeypatch) -> list:
    """Machine tools answer from a recorder: the claim DECISION is under test, and no disk is walked."""
    import types

    import core.agent_runtime.fast_paths_machine as fast_paths

    calls: list = []

    def execute(intent, args=None, *, source_context=None):
        calls.append((intent, dict(args or {})))
        return types.SimpleNamespace(ok=True, status="executed", response_text=f"{intent} ran",
                                     details={"observation": {}})

    monkeypatch.setattr(fast_paths, "execute_authorized_runtime_tool", execute)
    return calls


def _machine_claim(text: str, context: dict):
    import core.agent_runtime.fast_paths_machine as fast_paths

    return fast_paths.maybe_handle_direct_machine_read_request(
        _FolderFastPathAgent(), text, session_id=context["session_id"], source_surface="openclaw",
        source_context=dict(context))


def test_c4_the_served_sent_folder_follow_up_is_not_claimed_as_a_disk_search(monkeypatch, email_policy) -> None:
    """ORIGINAL: the exact served wording, in a session whose send ran, is left to the model instead
    of a deterministic disk walk; with no email work the same words keep their folder-search claim."""
    calls = _recording_machine_tools(monkeypatch)
    follow_up = "Did it actually go out? Check the sent folder."
    assert _machine_claim(follow_up, _chat_context(_session("quiet"))) is not None  # control
    assert calls == [("machine.find_folder", {"name": "sent"})]

    calls.clear()
    session = _session("sentfolder")
    _ledger_tool(session, "email.draft.send")
    assert _machine_claim(follow_up, _chat_context(session)) is None
    assert calls == []


def test_c4_novel_a_mailbox_folder_question_is_not_answered_from_a_disk_folder_but_typed_paths_are(
        monkeypatch, tmp_path, email_policy) -> None:
    """NOVEL wording through the other claim (folder audit): "What's in my sent folder?" in an email
    conversation is left to the model even though a disk folder of that name exists. NEGATIVE: a
    typed path is unambiguous and is still answered deterministically in the same conversation."""
    import core.agent_runtime.fast_paths_machine as fast_paths

    calls = _recording_machine_tools(monkeypatch)
    disk_folder = tmp_path / "Sent"
    disk_folder.mkdir()
    (disk_folder / "README.md").write_text("Scanned delivery notes.\n", encoding="utf-8")
    monkeypatch.setattr(fast_paths, "resolve_named_folder_under_home",
                        lambda name: str(disk_folder) if name == "sent" else None)
    question = "What's in my sent folder?"
    claimed = _machine_claim(question, _chat_context(_session("quiet-audit")))
    assert claimed is not None and claimed["reason"] == "folder_audit_fast_path"  # control

    session = _session("audit")
    _ledger_tool(session, "email.read")
    context = _chat_context(session)
    assert _machine_claim(question, context) is None

    listing = _machine_claim("What is inside /etc?", context)
    assert listing is not None and calls and calls[-1][0] == "machine.list_directory"


# The second deterministic claimant (evidence/served-hang-repro-after-allowed-send-20260914-150447.json):
# with the front door declining, the tool loop's workflow planner still planned machine.find_folder as
# the turn's first step, before the model's first round, and the thread dump put the daemon in
# _machine_find_folder again. One owner decides for both.


def _planned_intent(text: str, context: dict) -> str:
    from core.execution.planner import plan_tool_workflow

    decision = plan_tool_workflow(user_text=text, task_class="unknown", executed_steps=[], source_context=dict(context))
    return str((decision.next_payload or {}).get("intent") or "") if decision.handled else ""


def test_c4_the_workflow_planner_does_not_plan_a_disk_search_for_the_served_follow_up(email_policy) -> None:
    """ORIGINAL wording at the workflow planner: in a session whose send ran, no disk search is
    planned; with no email work the same words keep their planned folder search (control)."""
    follow_up = "Did it actually go out? Check the sent folder."
    assert _planned_intent(follow_up, _chat_context(_session("quiet-plan"))) == "machine.find_folder"  # control
    session = _session("plan-sent")
    _ledger_tool(session, "email.draft.send")
    assert _planned_intent(follow_up, _chat_context(session)) != "machine.find_folder"


def test_c4_novel_the_workflow_planner_leaves_a_mailbox_folder_location_ask_to_the_model(email_policy) -> None:
    """NOVEL wording (a location ask) in a session with an unfinished draft; NEGATIVE: a path-anchored
    listing keeps its planned machine.list_directory in the same session, and with no email work the
    ask is planned."""
    ask = "Where is my Drafts folder?"
    assert _planned_intent(ask, _chat_context(_session("quiet-where"))) == "machine.find_folder"  # control
    session = _session("plan-draft")
    assert email_drafts.save_draft(to="estimates@northfield-kiln.example.test", subject="Estimate 8442",
                                   body="We accept the estimate.", account="kilnworks", session_id=session).ok
    context = _chat_context(session)
    assert _planned_intent(ask, context) != "machine.find_folder"
    # The planner never plans "What is inside /etc?" (the front-door fast path owns that listing, see
    # the C4 test above; evidence/probe-planner-folder-claims-*.txt shows no plan at the base too).
    assert _planned_intent("List the files in ~/Downloads", context) == "machine.list_directory"


# ---------------------------------------------------------------------------
# G2 — typed search filters reach each provider in its own documented syntax
# ---------------------------------------------------------------------------
#
# Found driving the novel served conversation (evidence/served-journeys-53/served-journey-novel-graph-*.json):
# the recorded-shape fixture answered every Graph $search with the whole mailbox, so "the latest email from
# the kiln repair shop" came back beside a guild newsletter and the conversation replied to the newsletter.
# With the fixture honouring the documented message query forms (tests/provider_api_fixture.py), the
# adapter's own requests were not those forms: search_email composed every typed filter in Gmail syntax for
# both providers -- after:, before:, is:unread and in:inbox reached Graph, and a single term reached it
# without the documented double quotes -- while seen_only and the folder reached no provider at all.
# Documented Graph forms: $search="<KQL>" over from/subject/body/received
# (learn.microsoft.com/en-us/graph/search-query-parameter and the KQL reference it links), $filter for
# isRead and receivedDateTime, never both together on messages (Microsoft Q&A 1401458), and the Inbox
# folder's own collection when there is no filter (learn.microsoft.com/en-us/graph/api/user-list-messages).


def _listing_queries(api: ProviderApiServer, since: int) -> list[dict]:
    """Every message-collection listing the provider received after `since`: its path and decoded query."""
    rows = []
    for entry in api.state.request_log[since:]:
        if entry["method"] == "GET" and entry["path"].rstrip("/").endswith("/messages"):
            query = urllib.parse.parse_qs(entry.get("query") or "", keep_blank_values=True)
            rows.append({"path": entry["path"], **{name: values[0] for name, values in query.items()}})
    return rows


def _studio_inbox(api: ProviderApiServer, principal: str) -> dict[str, str]:
    kiln = {"emailAddress": {"name": "Northfield Kiln Repair", "address": "estimates@northfield-kiln.example.test"}}
    api.graph_inbox(principal, [
        {"from": kiln, "subject": "Estimate 8442 for relining the studio kiln", "body": "Total: 1,240 EUR.",
         "message_id": "<est-8442@northfield-kiln.example.test>", "date": "2026-09-11T08:30:00Z"},
        {"from": {"emailAddress": {"name": "Clay Guild", "address": "news@clayguild.example.test"}},
         "subject": "September glaze workshop", "body": "Join the September glaze workshop.",
         "message_id": "<news-0910@clayguild.example.test>", "date": "2026-09-10T18:00:00Z"},
        {"from": kiln, "subject": "Kiln shelf delivery", "body": "Your shelves ship on Friday.", "is_read": True,
         "message_id": "<ship-77@northfield-kiln.example.test>", "date": "2026-09-02T10:00:00Z"},
    ])
    return {model["subject"]: model["id"] for model in api.state.graph_mailboxes[principal]}


def test_g2_the_served_graph_sender_search_is_the_documented_search_and_finds_only_that_sender(api) -> None:
    """ORIGINAL: the novel conversation's read (sender "kiln") reaches Graph as $search="from:kiln" and
    returns the kiln shop's two messages, newest first, without the guild newsletter."""
    principal = "studio@clayworks.example.test"
    _connect(api, "graph", "studio", principal)
    _studio_inbox(api, principal)
    mark = len(api.state.request_log)
    found = email_tools.search_email(account="studio", sender="kiln")
    assert found.ok, (found.status, found.message)
    assert [m["subject"] for m in found.messages] == ["Estimate 8442 for relining the studio kiln",
                                                     "Kiln shelf delivery"]
    (listing,) = _listing_queries(api, mark)
    assert listing["path"] == "/v1.0/me/messages" and listing["$search"] == '"from:kiln"', listing
    assert "$filter" not in listing and "$orderby" not in listing


def test_g2_novel_graph_date_windows_read_state_and_the_plain_inbox_use_their_documented_forms(api) -> None:
    """NOVEL filters on Graph: sender or subject words (with a date window) stay one KQL $search; read
    state (with a date) is a $filter and no $search; no filter at all lists the Inbox folder, newest
    first, bounded by the limit."""
    principal = "studio@clayworks.example.test"
    _connect(api, "graph", "studio", principal)
    _studio_inbox(api, principal)
    estimate, newsletter, shelves = ("Estimate 8442 for relining the studio kiln", "September glaze workshop",
                                     "Kiln shelf delivery")
    cases = (
        ({"sender": "Northfield Kiln", "since": "2026-09-05", "before": "2026-09-12"}, [estimate],
         {"path": "/v1.0/me/messages",
          "$search": '"from:northfield from:kiln received>=2026-09-05 received<2026-09-12"'}),
        ({"subject": "Glaze workshop"}, [newsletter],
         {"path": "/v1.0/me/messages", "$search": '"subject:glaze subject:workshop"'}),
        ({"unseen_only": True, "since": "2026-09-01"}, [estimate, newsletter],
         {"path": "/v1.0/me/messages", "$filter": "receivedDateTime ge 2026-09-01T00:00:00Z and isRead eq false"}),
        ({"seen_only": True}, [shelves], {"path": "/v1.0/me/messages", "$filter": "isRead eq true"}),
        ({"limit": 2}, [estimate, newsletter],
         {"path": "/v1.0/me/mailFolders/inbox/messages", "$orderby": "receivedDateTime desc", "$top": "2"}),
    )
    for filters, subjects, expected in cases:
        mark = len(api.state.request_log)
        result = email_tools.search_email(account="studio", **filters)
        assert result.ok, (filters, result.status, result.message)
        assert [m["subject"] for m in result.messages] == subjects, filters
        (listing,) = _listing_queries(api, mark)
        assert {name: listing.get(name) for name in expected} == expected, (filters, listing)
        assert not ("$search" in listing and "$filter" in listing), listing


def test_g2_a_filter_a_provider_cannot_express_is_refused_before_any_listing(api) -> None:
    """NEGATIVE: what a provider's search cannot express is a typed refusal with no listing request --
    never dropped, never sent in another provider's syntax. Graph: sender or subject words with a read
    state ($search cannot carry read state and cannot be combined with $filter) and a sender with no
    searchable word. Both providers: a date that is not a date, contradictory read states, and a folder
    their mailbox-wide search cannot be limited to."""
    _connect(api, "graph", "studio", "studio@clayworks.example.test")
    _connect(api, "gmail", "orchard", "desk@orchard-farm.example.test")
    mark = len(api.state.request_log)
    refusals = [
        ("studio", {"sender": "kiln", "unseen_only": True}, "unsupported_filter"),
        ("studio", {"subject": "estimate", "seen_only": True}, "unsupported_filter"),
        ("studio", {"sender": "(( ))"}, "invalid_arguments"),
    ]
    for account in ("studio", "orchard"):
        refusals += [
            (account, {"since": "last week"}, "invalid_arguments"),
            (account, {"sender": "kiln", "before": "2026-13-40"}, "invalid_arguments"),
            (account, {"unseen_only": True, "seen_only": True}, "invalid_arguments"),
            (account, {"folder": "Sent", "sender": "kiln"}, "unsupported_folder"),
        ]
    for account, filters, status in refusals:
        result = email_tools.search_email(account=account, **filters)
        assert (result.ok, result.status, result.messages) == (False, status, []), (account, filters, result.message)
        assert "nothing was searched" in result.message.lower(), result.message
    assert _listing_queries(api, mark) == []


def test_g2_gmail_receives_its_own_operators_and_seen_only_now_reaches_it(api) -> None:
    """ORIGINAL provider control (Gmail): every typed filter stays a Gmail operator (from:, subject:,
    after:/before:, is:unread, in:inbox with no filter), and seen_only -- which reached no provider
    before -- reaches Gmail as is:read."""
    _connect(api, "gmail", "orchard", "desk@orchard-farm.example.test")
    for filters, query in (
        ({"sender": "Willow Ridge", "since": "2026-09-01", "before": "2026-09-12", "unseen_only": True},
         'from:"Willow Ridge" after:2026/09/01 before:2026/09/12 is:unread'),
        ({"subject": "delivery", "seen_only": True}, "subject:delivery is:read"),
        ({}, "in:inbox"),
    ):
        mark = len(api.state.request_log)
        result = email_tools.search_email(account="orchard", **filters)
        assert result.ok, (filters, result.status, result.message)
        (listing,) = _listing_queries(api, mark)
        assert (listing["path"], listing["q"]) == ("/gmail/v1/users/me/messages", query), filters


# ---------------------------------------------------------------------------
# C1b — a Graph message names its sender the way a Gmail message does
# ---------------------------------------------------------------------------
#
# Found driving the novel served conversation (evidence/served-journeys-53/served-journey-novel-graph-*.json,
# find turn): the Graph summary carried only the sender's address ("estimates@northfield-kiln.example.test")
# where a Gmail summary carries its From header ("Willow Ridge Orchard Supply <supply@...>"). Asked about
# "the kiln repair shop", a model received less evidence from Graph than from Gmail for the same message.


def test_c1b_graph_messages_name_their_sender_as_gmail_does(api, email_policy) -> None:
    """ORIGINAL (the served novel inbox): the Graph read the model receives and the opened message both
    carry "Name <address>"; the same message on Gmail carries the same From text (parity control)."""
    from core.runtime_execution_tools import execute_runtime_tool

    principal = "studio@clayworks.example.test"
    _connect(api, "graph", "studio", principal)
    ids = _studio_inbox(api, principal)
    expected = "Northfield Kiln Repair <estimates@northfield-kiln.example.test>"
    result = execute_runtime_tool("email.read", {"account": "studio", "sender": "kiln"})
    assert result is not None and result.ok, (result.status if result else None, result.response_text if result else None)
    payload, block = _model_view(result, "email.read")
    assert [m["from"] for m in payload["messages"]] == [expected, expected] and expected in block
    opened = email_tools.open_message(account="studio", message_id=ids["Estimate 8442 for relining the studio kiln"])
    assert opened.ok and opened.messages[0]["from"] == expected, (opened.status, opened.messages)

    gmail = "desk@clayworks-mail.example.test"
    _connect(api, "gmail", "studio-gmail", gmail)
    api.gmail_inbox(gmail, [{"from": expected, "subject": "Estimate 8442 for relining the studio kiln",
                             "body": "Total: 1,240 EUR.", "message_id": "<est-8442@northfield-kiln.example.test>",
                             "date": "Fri, 11 Sep 2026 08:30:00 +0000"}])
    mirrored = email_tools.search_email(account="studio-gmail", sender="kiln")
    assert [m["from"] for m in mirrored.messages] == [expected]


def test_c1b_novel_names_are_quoted_where_needed_and_nameless_senders_stay_addresses(api) -> None:
    """NOVEL: a display name holding a comma is quoted as RFC 5322 requires and its address stays
    recoverable; NEGATIVE: a sender Graph gives no name, or names by its own address, stays the bare
    address."""
    principal = "studio@clayworks.example.test"
    _connect(api, "graph", "studio", principal)
    api.graph_inbox(principal, [
        {"from": {"emailAddress": {"name": "Doe, Jane", "address": "jane@glazes.example.test"}},
         "subject": "Glaze order", "body": "Order confirmed.", "message_id": "<g1@glazes.example.test>",
         "date": "2026-09-09T09:00:00Z"},
        {"from": {"emailAddress": {"address": "noname@glazes.example.test"}}, "subject": "Glaze invoice",
         "body": "Invoice attached.", "message_id": "<g2@glazes.example.test>", "date": "2026-09-08T09:00:00Z"},
        {"from": {"emailAddress": {"name": "echo@glazes.example.test", "address": "echo@glazes.example.test"}},
         "subject": "Glaze receipt", "body": "Receipt attached.", "message_id": "<g3@glazes.example.test>",
         "date": "2026-09-07T09:00:00Z"},
    ])
    found = email_tools.search_email(account="studio", sender="glazes")
    assert found.ok, (found.status, found.message)
    assert [m["from"] for m in found.messages] == ['"Doe, Jane" <jane@glazes.example.test>',
                                                  "noname@glazes.example.test", "echo@glazes.example.test"]
    assert [parseaddr(m["from"]) for m in found.messages] == [
        ("Doe, Jane", "jane@glazes.example.test"), ("", "noname@glazes.example.test"),
        ("", "echo@glazes.example.test")]


# ---------------------------------------------------------------------------
# C5 — inferred web research leaves an email conversation's follow-ups to the model
# ---------------------------------------------------------------------------
#
# Found driving the novel served conversation (evidence/served-journeys-53/served-journey-novel-graph-*.json,
# reconcile turn): after the send, "Confirm it's in the Sent folder." was planned onto web.search as the
# turn's first step (" confirm " reads as research to the workflow planner), the search failed, and the
# turn ended without the model being asked. The planner claims "latest", "current" and "compare"
# follow-ups the same way, with or without email work
# (evidence/probe-planner-research-claims-candidate-a20d1cfd.txt).


def test_c5_the_served_confirmation_follow_up_is_not_planned_onto_the_web(email_policy) -> None:
    """ORIGINAL: the exact served wording, in a session whose send ran, is left to the model; with no
    email work the same words keep their planned research search (control)."""
    follow_up = "Confirm it's in the Sent folder."
    assert _planned_intent(follow_up, _chat_context(_session("quiet-confirm"))) == "web.search"  # control
    session = _session("confirm-sent")
    _ledger_tool(session, "email.draft.send")
    assert _planned_intent(follow_up, _chat_context(session)) == ""


def test_c5_novel_research_words_in_an_email_conversation_but_explicit_web_lookups_keep_their_plan(
        email_policy) -> None:
    """NOVEL wordings ("latest", "compare") in a session with an unfinished draft are left to the model;
    NEGATIVE: explicit web lookups in the same session keep their planned web.search, through the
    planner's research branch and through its entity-lookup branch."""
    session = _session("research-draft")
    assert email_drafts.save_draft(to="estimates@northfield-kiln.example.test", subject="Re: Estimate 8442",
                                   body="We accept the estimate.", account="kilnworks", session_id=session).ok
    context = _chat_context(session)
    for index, ask in enumerate(("Is this the latest estimate from the kiln shop?",
                                 "Compare the two estimates they sent me.")):
        assert _planned_intent(ask, _chat_context(_session(f"quiet-research-{index}"))) == "web.search"  # control
        assert _planned_intent(ask, context) == "", ask
    # evidence/probe-explicit-lookup-planning-candidate-3582c2a0.txt: the first wording reaches the research
    # branch that C5 guards, the second is planned by the entity-lookup branch before it.
    assert _planned_intent("Browse the web for the latest kiln safety guidance.", context) == "web.search"
    assert _planned_intent("Search the web for Northfield Kiln Repair reviews.", context) == "web.search"
