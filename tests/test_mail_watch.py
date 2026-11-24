"""New-mail alerts: what a connected mailbox does to the bell, the chime and the top mail icon.

Mail is served by real loopback servers: tests/local_mail_service.py speaks IMAP over a real socket with real
\\Seen flags, and tests/provider_api_fixture.py answers Gmail and Graph in their documented REST shapes. The
watcher under test is the production MailWatchPoller and the served /api routes; nothing is mocked.
"""
from __future__ import annotations

import email.utils
import json
import time
from email.message import EmailMessage

import pytest

from tests.local_mail_service import LocalMailService
from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home

ME = "me@watch.test"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    return prepare_home(tmp_path, monkeypatch)


@pytest.fixture
def mail():
    service = LocalMailService(smtp_port=0, imap_port=0)
    service.start()
    service.store.add_user(ME, "pw")
    yield service
    service.stop()


def _raw(sender: str, subject: str, mid: str, *, date: str | None = None, body: str = "BODY-TEXT-NEVER-READ") -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = ME
    m["Subject"] = subject
    m["Message-ID"] = mid
    m["Date"] = date or email.utils.formatdate(time.time())
    m.set_content(body)
    return m.as_bytes()


def _connect_imap(mail: LocalMailService, account: str = "default") -> None:
    from core import credential_store

    blob = {"host": "127.0.0.1", "port": mail.imap_port, "security": "plain", "username": ME, "password": "pw"}
    credential_store.store_credential(f"email.imap.{account}", json.dumps(blob), label="test imap")


def _poller(**kwargs):
    from core.mail_watch import MailWatchPoller

    return MailWatchPoller(**kwargs)


def _due_now() -> None:
    """Bring every account's next check to now, as the clock passing the poll period would."""
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("UPDATE mail_watch_accounts SET next_check_at = '1970-01-01T00:00:00+00:00'")
        conn.commit()
    finally:
        conn.close()


def _mail_items() -> list[dict]:
    status, payload = api_get("/api/notifications")
    assert status == 200, payload
    return [item for item in payload["items"] if item["source_kind"] == "new_mail"]


def _table_exists() -> bool:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'mail_watch_accounts'").fetchone() is not None
    finally:
        conn.close()


# ---- nothing without a mailbox -------------------------------------------------------------------------------


def test_a_fresh_install_reads_nothing_writes_nothing_and_shows_no_icon(home):
    calls = []
    outcome = _poller(read_fn=lambda **kw: calls.append(kw)).sweep_once()
    assert outcome == {"checked": 0, "alerts": 0, "blocked": 0, "errors": 0}
    assert calls == [] and not _table_exists()
    status, view = api_get("/api/mail/alerts")
    assert status == 200 and view["connected"] is False and view["unread"] is None
    assert view["enabled"] is True  # on by default, waiting for a mailbox


# ---- the IMAP path: baseline, one batch, dedupe across restart, read-only -------------------------------------


def test_new_mail_rings_once_per_batch_and_never_again_after_restart(home, mail):
    mail.store.deliver(ME, _raw("Old Friend <old@x.test>", "Already waiting", "<old-1@x.test>"))
    mail.store.deliver(ME, _raw("Old Friend <old@x.test>", "Also waiting", "<old-2@x.test>"))
    _connect_imap(mail)

    first = _poller().sweep_once()
    assert first["checked"] == 1 and first["alerts"] == 0  # the baseline never rings
    assert _mail_items() == []
    assert api_get("/api/mail/alerts")[1]["unread"] == 2

    mail.store.deliver(ME, _raw("Rasa Petraitis <rasa@x.test>", "Garden invoice", "<new-1@x.test>"))
    mail.store.deliver(ME, _raw("Tom <tom@x.test>", "Ferry times", "<new-2@x.test>"))
    _due_now()
    second = _poller().sweep_once()
    assert second["alerts"] == 1
    items = _mail_items()
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "2 new emails"
    assert item["payload"]["count"] == 2 and item["payload"]["section"] == "updates"
    shown = {row["from"]: row["subject"] for row in item["payload"]["messages"]}
    assert shown == {"Rasa Petraitis": "Garden invoice", "Tom": "Ferry times"}
    assert "BODY-TEXT-NEVER-READ" not in json.dumps(item)
    assert api_get("/api/mail/alerts")[1]["unread"] == 4
    # Read-only: the watcher never set \Seen on anything.
    assert mail.store.seen_flags(ME) == [False, False, False, False]

    # A restart (a new poller) and a second pass over the same mail add nothing.
    for _ in range(2):
        _due_now()
        assert _poller().sweep_once()["alerts"] == 0
    # The same message delivered twice (same Message-ID) is still the same mail.
    mail.store.deliver(ME, _raw("Tom <tom@x.test>", "Ferry times", "<new-2@x.test>"))
    _due_now()
    assert _poller().sweep_once()["alerts"] == 0
    assert len(_mail_items()) == 1


def test_an_old_unread_message_surfacing_later_does_not_ring(home, mail):
    # 25 unread at connect: only the newest 20 fit the baseline window.
    for index in range(25):
        mail.store.deliver(ME, _raw("Shop <shop@x.test>", f"Offer {index}", f"<offer-{index}@x.test>",
                                    date="Mon, 6 Jan 2025 09:00:00 +0000"))
    _connect_imap(mail)
    _poller().sweep_once()
    # The person reads the newest 20 elsewhere; the 5 older ones become the newest unread.
    for entry in mail.store.folder(ME)[5:]:
        entry.flags.add("\\Seen")
    _due_now()
    assert _poller().sweep_once()["alerts"] == 0
    assert _mail_items() == []
    assert api_get("/api/mail/alerts")[1]["unread"] == 5


def test_details_off_stores_and_shows_only_the_count(home, mail):
    _connect_imap(mail)
    _poller().sweep_once()
    assert api_post("/api/notifications/preferences", {"preferences": {"mail_alert_details": False}})[1]["ok"]
    mail.store.deliver(ME, _raw("Dr Vaitkus <clinic@x.test>", "Your test results", "<private-1@x.test>"))
    _due_now()
    assert _poller().sweep_once()["alerts"] == 1
    item = _mail_items()[0]
    assert item["title"] == "1 new email" and item["body"] == "" and item["payload"]["messages"] == []
    assert "Vaitkus" not in json.dumps(item) and "results" not in json.dumps(item)


def test_switch_off_stops_reads_and_hides_the_icon_at_once(home, mail):
    _connect_imap(mail)
    _poller().sweep_once()
    assert api_post("/api/notifications/preferences", {"preferences": {"check_new_mail": False}})[1]["ok"]
    calls = []
    _due_now()
    assert _poller(read_fn=lambda **kw: calls.append(kw)).sweep_once()["checked"] == 0
    assert calls == []
    view = api_get("/api/mail/alerts")[1]
    assert view["enabled"] is False  # the page hides the icon on this
    assert api_post("/api/notifications/preferences", {"preferences": {"check_new_mail": "no"}})[0] == 400


def test_a_refused_sign_in_is_a_reconnect_state_with_a_long_wait(home, mail):
    from core import credential_store
    from core.mail_watch import AUTH_BACKOFF_S

    blob = {"host": "127.0.0.1", "port": mail.imap_port, "security": "plain", "username": ME, "password": "wrong"}
    credential_store.store_credential("email.imap.default", json.dumps(blob), label="test imap")
    outcome = _poller().sweep_once()
    assert outcome["errors"] == 1 and _mail_items() == []
    view = api_get("/api/mail/alerts")[1]
    assert view["state"] == "needs_reconnect" and view["unread"] is None
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT next_check_at FROM mail_watch_accounts").fetchone()
    finally:
        conn.close()
    from core.mail_watch import _epoch

    assert _epoch(row["next_check_at"]) - time.time() > AUTH_BACKOFF_S - 60
    # Not retried before then: a second sweep contacts nothing.
    calls = []
    assert _poller(read_fn=lambda **kw: calls.append(kw)).sweep_once()["errors"] == 0 and calls == []


def test_an_online_mailbox_waits_for_the_outbound_switch(home, monkeypatch):
    from core import credential_store, policy_engine

    blob = {"host": "imap.mail.invalid", "port": 993, "username": ME, "password": "pw"}
    credential_store.store_credential("email.imap.default", json.dumps(blob), label="test imap")
    calls = []
    outcome = _poller(read_fn=lambda **kw: calls.append(kw)).sweep_once()
    assert outcome["blocked"] == 1 and calls == []
    assert api_get("/api/mail/alerts")[1]["state"] == "online_access_off"
    # With the switch on the same mailbox is contacted (here it cannot resolve: unreachable, still no crash).
    monkeypatch.setattr(policy_engine, "automatic_outbound_enabled", lambda: True)
    _due_now()
    _poller().sweep_once()
    assert api_get("/api/mail/alerts")[1]["state"] == "unreachable"


def test_removing_the_mailbox_removes_its_count(home, mail):
    from core import credential_store

    _connect_imap(mail)
    _poller().sweep_once()
    assert api_get("/api/mail/alerts")[1]["unread"] == 0
    credential_store.delete_credential("email.imap.default")
    _poller().sweep_once()
    view = api_get("/api/mail/alerts")[1]
    assert view["connected"] is False and view["unread"] is None and view["accounts"] == []


def test_a_slot_set_up_again_for_another_mailbox_starts_a_new_baseline(home, mail):
    _connect_imap(mail)
    _poller().sweep_once()
    other = LocalMailService(smtp_port=0, imap_port=0)
    other.start()
    try:
        other.store.add_user(ME, "pw")
        for index in range(3):
            other.store.deliver(ME, _raw("Someone <s@x.test>", f"Waiting {index}", f"<other-{index}@x.test>"))
        _connect_imap(other)  # the same slot name now names another mailbox, before any due time
        outcome = _poller().sweep_once()
        assert outcome["checked"] == 1 and outcome["alerts"] == 0  # its unread mail is a baseline, not new
        assert api_get("/api/mail/alerts")[1]["unread"] == 3
    finally:
        other.stop()


def test_an_answer_count_is_read_fresh_from_the_mailbox_never_from_storage(home, mail):
    from core.mail_watch import unread_now

    mail.store.deliver(ME, _raw("A <a@x.test>", "One", "<k-1@x.test>"))
    _connect_imap(mail)
    _poller().sweep_once()
    assert api_get("/api/mail/alerts")[1]["unread"] == 1
    # Mail arrives after the watcher's last check: the answer path still sees it, because it asks the server.
    mail.store.deliver(ME, _raw("B <b@x.test>", "Two", "<k-2@x.test>"))
    answer = unread_now()
    assert answer["unread"] == 2 and answer["accounts"][0]["ok"] is True
    assert api_get("/api/mail/alerts")[1]["unread"] == 2  # the fresh read also refreshed the icon
    assert mail.store.seen_flags(ME) == [False, False]
    # A mailbox that cannot be read now is unknown, never the older number.
    mail.stop()
    assert unread_now()["unread"] is None


# ---- the chime: one per batch, through the notification centre's own audio law -------------------------------


def _audio(after: int) -> list[str]:
    status, result = api_post("/api/notifications/audio", {"after": after})
    assert status == 200, result
    return result["notification_ids"]


def test_one_batch_is_one_chime_and_mute_or_quiet_hours_silence_it(home, mail):
    _connect_imap(mail)
    _poller().sweep_once()
    cursor = api_get("/api/notifications")[1]["cursor"]
    mail.store.deliver(ME, _raw("A <a@x.test>", "One", "<c-1@x.test>"))
    mail.store.deliver(ME, _raw("B <b@x.test>", "Two", "<c-2@x.test>"))
    _due_now()
    _poller().sweep_once()
    assert len(_audio(cursor)) == 1
    assert _audio(cursor) == []  # claimed once, never twice

    cursor = api_get("/api/notifications")[1]["cursor"]
    assert api_post("/api/notifications/preferences", {"preferences": {"sound": False}})[1]["ok"]
    mail.store.deliver(ME, _raw("C <c@x.test>", "Three", "<c-3@x.test>"))
    _due_now()
    assert _poller().sweep_once()["alerts"] == 1
    assert _audio(cursor) == []

    cursor = api_get("/api/notifications")[1]["cursor"]
    assert api_post("/api/notifications/preferences", {"preferences": {
        "sound": True, "quiet_hours": {"enabled": True, "start": "00:00", "end": "23:59"}}})[1]["ok"]
    mail.store.deliver(ME, _raw("D <d@x.test>", "Four", "<c-4@x.test>"))
    _due_now()
    assert _poller().sweep_once()["alerts"] == 1
    assert _audio(cursor) == []


# ---- Gmail and Graph: documented counters, headers only -------------------------------------------------------


@pytest.fixture
def api():
    from tests.provider_api_fixture import ProviderApiServer

    server = ProviderApiServer()
    server.start()
    yield server
    server.stop()


def _connect_provider(api, provider: str, address: str) -> None:
    from core import credential_store

    grant = api.add_oauth_client(provider, "default", address)
    handle = {"client_id": "c", "client_secret": "s", "refresh_token": grant, "token_url": api.token_url,
              "account_email": address}
    blob = {"provider": provider, "from_addr": address, "token_url": api.token_url,
            "api_base": api.gmail_base if provider == "gmail" else api.graph_base}
    credential_store.store_credential(f"email.oauth.{provider}.default", json.dumps(handle), label="oauth")
    for kind in ("imap", "smtp"):
        credential_store.store_credential(f"email.{kind}.default", json.dumps(blob), label=kind)


def _entry(sender: str, subject: str, mid: str, **extra) -> dict:
    return {"from": sender, "subject": subject, "message_id": mid, "body": "BODY-TEXT-NEVER-READ",
            "date": email.utils.formatdate(time.time()), **extra}


def test_gmail_counts_unread_and_reads_only_metadata(home, api):
    address = "me@gmail.test"
    api.gmail_inbox(address, [_entry("Old <o@x.test>", "Waiting", "<g-old@x.test>"),
                              _entry("Read <r@x.test>", "Done", "<g-read@x.test>", unread=False)])
    _connect_provider(api, "gmail", address)
    _poller().sweep_once()
    assert api_get("/api/mail/alerts")[1]["unread"] == 1
    api.gmail_deliver(address, _entry("Ona <ona@x.test>", "Tickets", "<g-new@x.test>"))
    _due_now()
    assert _poller().sweep_once()["alerts"] == 1
    item = _mail_items()[0]
    assert item["title"] == "New email from Ona" and "BODY-TEXT" not in json.dumps(item)
    assert api_get("/api/mail/alerts")[1]["unread"] == 2
    reads = [entry for entry in api.state.request_log if "/messages/" in entry["path"]]
    assert reads and all("format=metadata" in entry["query"] for entry in reads)
    assert not any(entry["method"] != "GET" for entry in api.state.request_log)
    # A message already known by its id is not fetched again on the next check.
    before = len(reads)
    _due_now()
    _poller().sweep_once()
    assert len([e for e in api.state.request_log if "/messages/" in e["path"]]) == before


def test_graph_counts_unread_and_selects_header_fields_only(home, api):
    address = "me@outlook.test"
    api.graph_inbox(address, [_entry({"emailAddress": {"name": "Old", "address": "o@x.test"}}, "Waiting",
                                     "<m-old@x.test>")])
    _connect_provider(api, "graph", address)
    _poller().sweep_once()
    assert api_get("/api/mail/alerts")[1]["unread"] == 1
    api.graph_deliver(address, _entry({"emailAddress": {"name": "Jonas", "address": "j@x.test"}}, "Rent",
                                      "<m-new@x.test>"))
    _due_now()
    assert _poller().sweep_once()["alerts"] == 1
    item = _mail_items()[0]
    assert item["title"] == "New email from Jonas"
    listings = [e for e in api.state.request_log if e["path"].lower().endswith("/mailfolders/inbox/messages")]
    assert listings and all("%24select=id%2CinternetMessageId" in e["query"] or "$select=id,internetMessageId" in e["query"]
                            for e in listings)
    assert not any(entry["method"] != "GET" for entry in api.state.request_log)
