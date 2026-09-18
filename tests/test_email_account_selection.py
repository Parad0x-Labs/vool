"""The account the operator chose is the account every email step uses.

Reproduced 2026-09-14 on revision 6 (0e3e19ec): `email_tools.resolve_default_account(kind)` accepted a
profile binding only when its credential prefix named the requested transport. The operator's default,
bound as `credential:email.smtp.work`, resolved to `work` for a send and to the literal `default` slot for
a read -- so "check my mail" read whatever mailbox the `default` slot happened to hold. The repair is one
account authority (core.email_accounts): a selection names ONE mailbox identity with the transports it
supports, explicit choices win, and anything ambiguous, missing, revoked or one-way is refused with the
configured choices, never redirected.

What executes: the production credential store (vault, in an isolated home), the Operator Profile store,
the account authority, and the Gmail / Microsoft Graph adapters over loopback against
tests/provider_api_fixture.py (SIMULATED providers; the request log records which mailbox each
credential authenticated). No live account, mailbox, model or network beyond loopback.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from core import credential_store, email_drafts, email_tools
from tests.operator_profile_rig import OWNER, profile_env_generator
from tests.provider_api_fixture import ProviderApiServer

WORK = "studio@clayworks.example.test"          # the work mailbox, Microsoft Graph
PERSONAL = "me@orchard-farm.example.test"       # the personal mailbox, Gmail
OTHER = "someone-else@example.test"             # a contrary mailbox behind the literal `default` slot
SESSION = "openclaw:account-selection"

KILN = {"from": {"emailAddress": {"name": "Northfield Kiln Repair", "address": "estimates@northfield-kiln.example.test"}},
        "subject": "Estimate 8442 for relining the studio kiln", "body": "Total: 1,240 EUR.",
        "message_id": "<est-8442@northfield-kiln.example.test>", "date": "2026-09-11T08:30:00Z"}
ORCHARD = {"from": "Willow Ridge Orchard Supply <supply@willowridge-orchard.example.test>", "subject": "Delivery scheduling",
           "body": "Tuesday at 14:00 or Wednesday at 10:00?", "message_id": "<wro-20260910@willowridge-orchard.example.test>",
           "date": "Wed, 10 Sep 2026 09:15:00 +0000", "thread_id": "th-orchard"}
DECOY = {"from": "Decoy Sender <decoy@example.test>", "subject": "Not your mail",
         "body": "This mailbox belongs to someone else.", "message_id": "<decoy-1@example.test>",
         "date": "Tue, 09 Sep 2026 16:00:00 +0000", "thread_id": "th-decoy"}


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    yield from profile_env_generator(tmp_path, monkeypatch)


@pytest.fixture()
def api():
    server = ProviderApiServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _store_provider(api: ProviderApiServer, provider: str, account: str, address: str, *,
                    kinds: tuple[str, ...] = ("imap", "smtp"), handle: bool = True) -> None:
    grant = api.add_oauth_client(provider, account, address)
    if handle:
        credential_store.store_credential(
            f"email.oauth.{provider}.{account}",
            json.dumps({"client_id": "c", "client_secret": "s", "refresh_token": grant,
                        "token_url": api.token_url, "account_email": address}),
            label=f"{account} oauth")
    blob = {"provider": provider, "from_addr": address, "token_url": api.token_url,
            "api_base": api.gmail_base if provider == "gmail" else api.graph_base}
    for kind in kinds:
        credential_store.store_credential(f"email.{kind}.{account}", json.dumps(blob), label=f"{account} {kind}")


def _store_password_slot(kind: str, account: str, address: str) -> None:
    """A standards (IMAP/SMTP) slot that would connect to a closed loopback port: fine for resolution and
    listing, which never connect, and a loud failure for anything that does."""
    credential_store.store_credential(
        f"email.{kind}.{account}",
        json.dumps({"host": "127.0.0.1", "port": 1, "username": address, "password": "fixture-pw",
                    "from_addr": address, "security": "plain"}),
        label=f"{account} {kind}")


def _bind_default(value: str, *, scope: str = "global", session_id: str = "") -> None:
    from core import operator_profile

    change = operator_profile.remember(OWNER, "default_account.email", value, scope=scope,
                                       scope_key=session_id if scope == "chat" else "", session_id=session_id,
                                       origin="explicit", actor="operator", replace=True)
    assert change.kind in {"saved", "updated"}, (change.kind, change.report)


def _principals_read(api: ProviderApiServer) -> list[str]:
    """Which mailbox each authenticated READ hit, in order (the provider's own view of the credential)."""
    return [str(entry.get("principal") or "") for entry in api.state.request_log
            if entry["method"] == "GET" and ("/messages" in entry["path"] or "/mailFolders" in entry["path"])]


def _two_accounts_and_a_contrary_default(api: ProviderApiServer) -> None:
    api.graph_inbox(WORK, [dict(KILN)])
    api.gmail_inbox(PERSONAL, [dict(ORCHARD)])
    api.gmail_inbox(OTHER, [dict(DECOY)])
    _store_provider(api, "graph", "work", WORK)
    _store_provider(api, "gmail", "personal", PERSONAL)
    _store_provider(api, "gmail", "default", OTHER)


# ---------------------------------------------------------------------------------------------
# ORIGINAL: the reproduced defect -- a send-bound default becomes the literal `default` for a read
# ---------------------------------------------------------------------------------------------


def test_original_the_chosen_work_account_is_read_not_the_literal_default_slot(profile_env, api) -> None:
    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")

    assert email_tools.resolve_default_account("smtp") == "work"
    assert email_tools.resolve_default_account("imap") == "work", "a read must use the chosen account too"

    result = email_tools.search_email(sender="kiln", limit=5)
    assert result.ok and [m["message_id"] for m in result.messages] == [KILN["message_id"]], (result.status, result.message)
    assert set(_principals_read(api)) == {WORK}, api.state.request_log


def test_novel_a_read_bound_default_is_used_for_the_send_too(profile_env, api) -> None:
    """The same defect from the other side: a default bound through its IMAP slot must send from that
    mailbox, not from the literal `default` slot."""
    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.imap.personal")

    assert email_tools.resolve_default_account("imap") == "personal"
    assert email_tools.resolve_default_account("smtp") == "personal"
    saved = email_drafts.save_draft(to="supply@willowridge-orchard.example.test", subject="Delivery",
                                    body="Wednesday works.", session_id=SESSION)
    assert saved.ok, saved.message
    assert saved.draft["account_resolved"] == "personal", saved.draft
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION).ok
    sent = email_drafts.send_draft(saved.draft["draft_id"], session_id=SESSION)
    assert sent.ok and sent.status == "sent", (sent.status, sent.message)
    assert api.sent_count("gmail", PERSONAL) == 1 and api.sent_count("gmail", OTHER) == 0


# ---------------------------------------------------------------------------------------------
# One authority: what a selection is
# ---------------------------------------------------------------------------------------------


def test_a_selection_names_one_mailbox_identity_with_both_transports(profile_env, api) -> None:
    from core.email_accounts import select_account

    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")
    for kind in ("imap", "smtp"):
        selection = select_account(kind)
        assert selection.ok and selection.account == "work", selection
        assert selection.provider == "graph" and selection.identity.startswith(WORK), selection
        assert selection.read and selection.send
        assert selection.source == "global"


def test_the_listing_names_identities_capabilities_and_the_default_without_secrets(profile_env, api) -> None:
    from core.email_accounts import list_accounts

    _two_accounts_and_a_contrary_default(api)
    _store_password_slot("smtp", "notify", "notify@example.test")     # a send-only standards slot
    _bind_default("credential:email.smtp.work")

    rows = {row["account"]: row for row in list_accounts()}
    assert set(rows) == {"work", "personal", "default", "notify"}, sorted(rows)
    assert rows["work"]["provider"] == "graph" and rows["work"]["address"] == WORK
    assert rows["work"]["read"] and rows["work"]["send"] and rows["work"]["is_default"]
    assert rows["personal"]["provider"] == "gmail" and not rows["personal"]["is_default"]
    assert rows["notify"]["send"] and not rows["notify"]["read"] and rows["notify"]["state"] == "send_only"
    dumped = json.dumps(rows)
    for secret in ("fixture-pw", "rt-graph-work", "rt-gmail-personal", "client_secret", "refresh_token", "password"):
        assert secret not in dumped, secret


# ---------------------------------------------------------------------------------------------
# Explicit choices win; nothing is redirected
# ---------------------------------------------------------------------------------------------


def test_an_explicit_account_argument_wins_over_the_default_binding(profile_env, api) -> None:
    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")
    result = email_tools.search_email(account="personal", sender="orchard", limit=5)
    assert result.ok and [m["message_id"] for m in result.messages] == [ORCHARD["message_id"]], (result.status, result.message)
    assert set(_principals_read(api)) == {PERSONAL}


def test_an_unknown_explicit_account_is_refused_under_its_own_name_with_the_configured_choices(profile_env, api) -> None:
    """An explicit choice is honoured as named: a name with no credentials is refused for THAT name (the
    setup guidance), with the accounts that could read -- never redirected to the default or to another
    configured mailbox."""
    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")
    result = email_tools.search_email(account="ghost", sender="kiln", limit=5)
    assert not result.ok and result.status == "needs_credentials", (result.status, result.message)
    assert "'ghost'" in result.message and "work" in result.message and "personal" in result.message, result.message
    assert _principals_read(api) == [], "nothing may be read on the way to a refusal"


def test_two_slots_with_one_name_but_different_mailboxes_are_refused_not_merged(profile_env, api) -> None:
    """`email.smtp.work` and `email.imap.work` share a name; only their identities say whether they are one
    mailbox. Here the IMAP slot names a different mailbox, so a read through the work default must refuse
    -- reading the other mailbox would be a silent cross-account fallback."""
    api.graph_inbox(WORK, [dict(KILN)])
    api.gmail_inbox(OTHER, [dict(DECOY)])
    _store_provider(api, "graph", "work", WORK, kinds=("smtp",))
    grant = api.add_oauth_client("gmail", "work", OTHER)
    credential_store.store_credential("email.oauth.gmail.work", json.dumps(
        {"client_id": "c", "client_secret": "s", "refresh_token": grant, "token_url": api.token_url,
         "account_email": OTHER}), label="stray")
    credential_store.store_credential("email.imap.work", json.dumps(
        {"provider": "gmail", "from_addr": OTHER, "token_url": api.token_url, "api_base": api.gmail_base}), label="stray")
    _bind_default("credential:email.smtp.work")

    result = email_tools.search_email(sender="kiln", limit=5)
    assert not result.ok and result.status == "account_mismatch", (result.status, result.message)
    assert _principals_read(api) == [], api.state.request_log


def test_no_selection_among_several_accounts_asks_which_one_instead_of_guessing(profile_env, api) -> None:
    api.graph_inbox(WORK, [dict(KILN)])
    api.gmail_inbox(PERSONAL, [dict(ORCHARD)])
    _store_provider(api, "graph", "work", WORK)
    _store_provider(api, "gmail", "personal", PERSONAL)

    result = email_tools.search_email(sender="kiln", limit=5)
    assert not result.ok and result.status == "account_required", (result.status, result.message)
    assert "work" in result.message and "personal" in result.message, result.message
    assert _principals_read(api) == []


def test_a_default_that_is_not_an_email_account_is_refused_not_guessed_at(profile_env, api) -> None:
    """The profile accepts any stored credential name as a binding; only an email slot names a mailbox. A
    binding to another credential family is refused with the choices -- its last name part is not an account."""
    _two_accounts_and_a_contrary_default(api)
    credential_store.store_credential("x.api.work", json.dumps({"token": "not-a-mailbox"}), label="social")
    _bind_default("credential:x.api.work")
    result = email_tools.search_email(sender="kiln", limit=5)
    assert not result.ok and result.status == "account_unavailable", (result.status, result.message)
    assert "x.api.work" in result.message and "work" in result.message, result.message
    assert _principals_read(api) == []


def test_legacy_default_slot_is_used_only_when_nothing_else_was_selected(profile_env, api) -> None:
    api.gmail_inbox(OTHER, [dict(DECOY)])
    _store_provider(api, "gmail", "default", OTHER)
    assert email_tools.resolve_default_account("imap") == "default"
    result = email_tools.search_email(sender="decoy", limit=5)
    assert result.ok and [m["message_id"] for m in result.messages] == [DECOY["message_id"]], (result.status, result.message)


def test_a_chat_scoped_selection_outranks_the_global_default(profile_env, api) -> None:
    from core.operator_profile_turn import bind_turn_scope, clear_turn_scope

    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")
    _bind_default("credential:email.smtp.personal", scope="chat", session_id=SESSION)
    assert email_tools.resolve_default_account("imap", principal=OWNER, session_id=SESSION) == "personal"
    assert email_tools.resolve_default_account("imap", principal=OWNER) == "work"
    bind_turn_scope(OWNER, SESSION)
    try:
        result = email_tools.search_email(sender="orchard", limit=5)
    finally:
        clear_turn_scope()
    assert result.ok and set(_principals_read(api)) == {PERSONAL}, (result.status, result.message, api.state.request_log)


def test_a_one_way_account_is_refused_for_the_transport_it_lacks(profile_env, api) -> None:
    """The default names a send-only slot: reading through it is refused as send-only with the accounts
    that can read, never redirected to another slot."""
    api.graph_inbox(WORK, [dict(KILN)])
    _store_provider(api, "graph", "work", WORK)
    _store_password_slot("smtp", "notify", "notify@example.test")
    _bind_default("credential:email.smtp.notify")
    result = email_tools.search_email(sender="kiln", limit=5)
    assert not result.ok and result.status == "account_send_only", (result.status, result.message)
    assert "work" in result.message, result.message
    assert _principals_read(api) == []


def test_a_provider_account_whose_grant_is_gone_reports_reconnect_not_another_mailbox(profile_env, api) -> None:
    from core.email_accounts import list_accounts

    _two_accounts_and_a_contrary_default(api)
    credential_store.delete_credential("email.oauth.graph.work")
    _bind_default("credential:email.smtp.work")
    rows = {row["account"]: row for row in list_accounts()}
    assert rows["work"]["state"] == "needs_reconnect", rows["work"]
    result = email_tools.search_email(sender="kiln", limit=5)
    assert not result.ok and result.status == "needs_reconnect", (result.status, result.message)
    assert _principals_read(api) == []


def test_an_explicit_provider_account_whose_grant_is_gone_refuses_through_the_read_executor(profile_env, api) -> None:
    """The read EXECUTOR path (what a served turn runs): a named provider account whose grant was removed answers
    a typed refusal that names reconnecting, with no provider request and no other mailbox read. Measured served
    2026-09-14: the adapter's refusal came back as a send-shaped result and email.read failed on `.messages`."""
    from core.runtime_execution_tools import _email_read

    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")
    assert _email_read({"account": "personal", "sender": "orchard", "limit": 5}).ok
    credential_store.delete_credential("email.oauth.gmail.personal")
    result = _email_read({"account": "personal", "sender": "orchard", "limit": 5})
    assert not result.ok and result.status in {"needs_setup", "needs_reconnect"}, (result.status, result.response_text)
    assert "connect" in result.response_text.lower() and "personal" in result.response_text, result.response_text
    assert result.details["messages"] == []
    assert set(_principals_read(api)) == {PERSONAL}, "only the read that succeeded reached the provider"


# ---------------------------------------------------------------------------------------------
# Approved drafts stay bound to the mailbox they were approved for
# ---------------------------------------------------------------------------------------------


def test_a_changed_default_never_moves_an_approved_draft(profile_env, api) -> None:
    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")
    saved = email_drafts.save_draft(to="estimates@northfield-kiln.example.test", subject="Estimate 8442",
                                    body="We accept.", session_id=SESSION)
    assert saved.ok and saved.draft["account_resolved"] == "work", saved.draft
    assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION).ok
    _bind_default("credential:email.smtp.personal")
    assert email_tools.resolve_default_account("smtp") == "personal"
    sent = email_drafts.send_draft(saved.draft["draft_id"], session_id=SESSION)
    assert sent.ok and sent.status == "sent", (sent.status, sent.message)
    assert api.sent_count("graph", WORK) == 1 and api.sent_count("gmail", PERSONAL) == 0
    assert sent.details["receipt"]["verified_principal"] == WORK, sent.details


def test_a_same_principal_rotation_keeps_the_approval_and_a_repointed_slot_needs_reapproval(profile_env, api) -> None:
    _two_accounts_and_a_contrary_default(api)
    _bind_default("credential:email.smtp.work")

    def approved(subject: str) -> str:
        saved = email_drafts.save_draft(to="estimates@northfield-kiln.example.test", subject=subject,
                                        body="We accept.", session_id=SESSION)
        assert saved.ok and saved.draft["account_resolved"] == "work", saved
        assert email_drafts.approve_draft(saved.draft["draft_id"], session_id=SESSION).ok
        return saved.draft["draft_id"]

    first, second = approved("Estimate 8442"), approved("Estimate 8442, follow-up")
    # Same principal, rotated grant: the approval still names this mailbox, so the send goes.
    handle: dict[str, Any] = json.loads(credential_store.get_credential("email.oauth.graph.work") or "{}")
    api.set_refresh("graph", "work", "rt-graph-work-rotated")
    credential_store.store_credential("email.oauth.graph.work", json.dumps({**handle, "refresh_token": "rt-graph-work-rotated"}),
                                      label="rotated")
    sent = email_drafts.send_draft(first, session_id=SESSION)
    assert sent.ok and sent.status == "sent" and sent.details["receipt"]["verified_principal"] == WORK, (sent.status, sent.message)
    assert api.sent_count("graph", WORK) == 1
    # Re-pointed slot: the same name now authenticates another mailbox -- the second approval is stale.
    credential_store.store_credential("email.oauth.graph.work", json.dumps(
        {**handle, "account_email": OTHER, "refresh_token": api.add_oauth_client("graph", "work", OTHER)}), label="repointed")
    credential_store.store_credential("email.smtp.work", json.dumps(
        {"provider": "graph", "from_addr": OTHER, "token_url": api.token_url, "api_base": api.graph_base}), label="repointed")
    result = email_drafts.send_draft(second, session_id=SESSION)
    assert not result.ok and result.status == "needs_reapproval", (result.status, result.message)
    assert api.sent_count("graph", WORK) == 1 and api.sent_count("graph", OTHER) == 0
