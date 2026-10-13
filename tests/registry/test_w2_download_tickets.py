import pytest

from core.registry.artifact import inspect
from core.registry.codec import RegistryError
from registry_service.store import Store
from tests.registry.audit_helpers import package, publish
from tests.registry.helpers import NOW


def test_w2_anonymous_ticket_backlog_cannot_block_new_download(tmp_path):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    publish(store, raw, tmp_path / "trust.sqlite")
    sha = inspect(raw)["sha256"]
    with store.connection() as db:
        db.executemany("INSERT INTO download_tickets(id,sha256,expires) VALUES (?,?,?)",
                       [("old-live-ticket-" + str(i), sha, NOW + 900) for i in range(10000)])
    ticket = store.ticket(sha, now=NOW)
    assert store.stats("alice/echo")["download_requests"] == 0
    assert store.download(sha, ticket, now=NOW) == raw
    assert store.download(sha, ticket, now=NOW) == raw
    assert store.stats("alice/echo")["download_requests"] == 1


def test_w2_issuance_is_stateless_and_signed_tickets_bind_bytes_and_expiry(tmp_path):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    publish(store, raw, tmp_path / "trust.sqlite")
    sha = inspect(raw)["sha256"]
    tickets = [store.ticket(sha, now=NOW) for _ in range(8)]
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM download_tickets").fetchone()[0] == 0
    reopened = Store(store.path)
    assert reopened.download(sha, tickets[0], now=NOW) == raw
    for checksum, token, stamp in [("0" * 64, tickets[0], NOW), (sha, tickets[0] + "x", NOW),
                                    (sha, tickets[0], NOW + 900)]:
        with pytest.raises(RegistryError):
            reopened.download(checksum, token, now=stamp)
