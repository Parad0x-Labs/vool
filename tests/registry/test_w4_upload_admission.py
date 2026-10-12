import asyncio
from types import SimpleNamespace

import pytest

from core.registry.artifact import inspect
from core.registry.codec import RegistryError, canonical
from registry_service.api import PREFIX, create_app
from registry_service.auth import Auth
from registry_service.store import Store
from tests.registry.audit_helpers import package, trusted
from tests.registry.helpers import NOW, TARGET


async def post(app, token, csrf, raw, *, route="submissions", received=None, release=None):
    messages, reads = [], []
    async def receive():
        if reads:
            return {"type": "http.disconnect"}
        reads.append(True)
        if received:
            received.set()
        if release:
            await release.wait()
        return {"type": "http.request", "body": raw, "more_body": False}
    async def send(message):
        messages.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
             "method": "POST", "scheme": "https", "path": PREFIX + "/" + route, "raw_path": (PREFIX + "/" + route).encode(),
             "query_string": b"", "root_path": "", "server": ("registry.test", 443), "client": ("127.0.0.1", 1000),
             "headers": [(b"host", b"registry.test"), (b"origin", b"https://registry.test"),
                         (b"content-type", b"application/json" if route == "drafts" else b"application/vnd.vool.registry+json"),
                         (b"cookie", ("vool_registry=" + token).encode()), (b"x-vool-csrf", csrf.encode())]}
    await app(scope, receive, send)
    return next(message["status"] for message in messages if message["type"] == "http.response.start"), len(reads)


@pytest.mark.parametrize("blocked", ["quota", "namespace"])
@pytest.mark.parametrize("route", ["submissions", "drafts"])
def test_w4_refused_upload_does_not_read_request_body(tmp_path, blocked, route):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    account = store.identity("github", "upload-owner", now=NOW)
    if blocked == "quota":
        store.profile(account, {"namespace": "alice", "bio": "", "handles": {}})
        for _ in range(10):
            store.upload(account, raw, now=NOW)
    token, csrf = store.session(account, now=NOW)
    with trusted(raw, tmp_path / "trust.sqlite") as (catalog, *_):
        app = create_app(store, Auth(store, "https://registry.test", []), catalog, TARGET, clock=lambda: NOW)
        status, reads = asyncio.run(post(app, token, csrf, raw, route=route))
    assert status == 400 and reads == 0


@pytest.mark.parametrize("route", ["upload", "draft"])
def test_w4_store_quota_precedes_artifact_parser(tmp_path, route):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    account = store.identity("github", "store-upload-owner", now=NOW)
    store.profile(account, {"namespace": "alice", "bio": "", "handles": {}})
    for _ in range(10):
        store.upload(account, raw, now=NOW)
    with pytest.raises(RegistryError, match="quota"):
        getattr(store, route)(account, b"not valid JSON" if route == "upload" else {}, now=NOW)


def draft_bytes(raw):
    artifact = inspect(raw)
    return canonical({"metadata": {k: v for k, v in artifact["metadata"].items() if k != "source"},
                      "skill_markdown": artifact["files"]["skills/echo/SKILL.md"].decode(),
                      "license_text": artifact["files"]["LICENSE"].decode()})


@pytest.mark.parametrize("route", ["submissions", "drafts"])
def test_w4_concurrent_uploads_refuse_before_fifth_body(tmp_path, route):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    sessions, bodies = [], []
    for i in range(5):
        account = store.identity("github", "upload-contender-" + str(i), now=NOW)
        store.profile(account, {"namespace": "author" + str(i), "bio": "", "handles": {}})
        sessions.append(store.session(account, now=NOW))
        source = tmp_path / ("author" + str(i))
        source.mkdir()
        content, _, _ = package(source, publisher="author" + str(i))
        bodies.append(draft_bytes(content) if route == "drafts" else content)
    with trusted(raw, tmp_path / "trust.sqlite") as (catalog, *_):
        app = create_app(store, Auth(store, "https://registry.test", []), catalog, TARGET, clock=lambda: NOW)
        async def exercise():
            release = asyncio.Event()
            started = [asyncio.Event() for _ in range(4)]
            tasks = [asyncio.create_task(post(app, *sessions[i], bodies[i], route=route, received=started[i], release=release)) for i in range(4)]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 5)
                status, reads = await post(app, *sessions[4], bodies[4], route=route)
                assert status == 400 and reads == 0
            finally:
                release.set()
                results = await asyncio.gather(*tasks)
                assert results == [(201, 1)] * 4
            assert await post(app, *sessions[4], bodies[4], route=route) == (201, 1)
            with store.connection() as db:
                assert db.execute("SELECT count(*) FROM upload_leases").fetchone()[0] == 0
        asyncio.run(exercise())


def test_w4_same_account_cannot_hold_two_bodies_and_release_restores_capacity(tmp_path):
    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    owner = store.identity("github", "same-uploader", now=NOW)
    store.profile(owner, {"namespace": "alice", "bio": "", "handles": {}})
    session = store.session(owner, now=NOW)
    with trusted(raw, tmp_path / "trust.sqlite") as (catalog, *_):
        app = create_app(store, Auth(store, "https://registry.test", []), catalog, TARGET, clock=lambda: NOW)
        async def exercise():
            entered, release = asyncio.Event(), asyncio.Event()
            first = asyncio.create_task(post(app, *session, raw, received=entered, release=release))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                assert await post(app, *session, raw) == (400, 0)
            finally:
                release.set()
                assert await first == (201, 1)
            assert await post(app, *session, raw) == (201, 1)
        asyncio.run(exercise())


def test_w4_slow_processing_cannot_commit_after_lease_expiry(tmp_path, monkeypatch):
    from registry_service import store as store_module

    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    owner = store.identity("github", "slow-uploader", now=NOW)
    store.profile(owner, {"namespace": "alice", "bio": "", "handles": {}})
    ticks = iter((0, 301))
    monkeypatch.setattr(store_module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    with pytest.raises(RegistryError, match="expired"):
        store.upload(owner, raw, now=NOW)
    assert store.submissions(owner) == []
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM upload_leases").fetchone()[0] == 0


def test_w4_body_timeout_releases_slot_before_next_upload(tmp_path, monkeypatch):
    from registry_service import api as service_api

    raw, _, _ = package(tmp_path)
    store = Store(tmp_path / "accounts.sqlite")
    owner = store.identity("github", "stalled-uploader", now=NOW)
    store.profile(owner, {"namespace": "alice", "bio": "", "handles": {}})
    session = store.session(owner, now=NOW)
    monkeypatch.setattr(service_api, "UPLOAD_BODY_TIMEOUT", 0.02)
    with trusted(raw, tmp_path / "trust.sqlite") as (catalog, *_):
        app = create_app(store, Auth(store, "https://registry.test", []), catalog, TARGET, clock=lambda: NOW)
        async def exercise():
            assert await post(app, *session, raw, release=asyncio.Event()) == (400, 1)
            assert await post(app, *session, raw) == (201, 1)
        asyncio.run(exercise())
