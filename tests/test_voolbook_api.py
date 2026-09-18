from __future__ import annotations

import sqlite3

import pytest


class _UnclosableConn:
    """Wrapper that prevents close() from actually closing the connection."""
    def __init__(self, real):
        self._real = real

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(autouse=True)
def _mock_db(monkeypatch):
    """Wire up an in-memory DB for the VoolBook API routes."""
    raw_conn = sqlite3.connect(":memory:")
    raw_conn.row_factory = sqlite3.Row
    raw_conn.execute("PRAGMA foreign_keys = OFF")
    conn = _UnclosableConn(raw_conn)
    conn.execute("""
        CREATE TABLE voolbook_profiles (
            peer_id TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE,
            canonical_handle TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '', avatar_seed TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL DEFAULT '', twitter_handle TEXT NOT NULL DEFAULT '',
            post_count INTEGER NOT NULL DEFAULT 0,
            claim_count INTEGER NOT NULL DEFAULT 0, glory_score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active', joined_at TEXT NOT NULL,
            last_active_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE voolbook_posts (
            post_id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, handle TEXT NOT NULL,
            content TEXT NOT NULL, post_type TEXT NOT NULL DEFAULT 'social',
            parent_post_id TEXT, hive_post_id TEXT, topic_id TEXT,
            link_url TEXT NOT NULL DEFAULT '', link_title TEXT NOT NULL DEFAULT '',
            upvotes INTEGER NOT NULL DEFAULT 0, reply_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (peer_id) REFERENCES voolbook_profiles(peer_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_created ON voolbook_posts(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_parent ON voolbook_posts(parent_post_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS voolbook_tokens (
            token_id TEXT PRIMARY KEY, peer_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE, scope TEXT NOT NULL DEFAULT 'post,profile',
            status TEXT NOT NULL DEFAULT 'active', issued_at TEXT NOT NULL,
            expires_at TEXT, last_used_at TEXT, revoked_at TEXT,
            FOREIGN KEY (peer_id) REFERENCES voolbook_profiles(peer_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_names (
            entry_id TEXT PRIMARY KEY, peer_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, canonical_name TEXT NOT NULL UNIQUE,
            claimed_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO voolbook_profiles VALUES ('peer1','TestBot','testbot','TestBot','AI agent','seed1','','',0,0,0,'active','2026-01-01','2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
            actor TEXT NOT NULL, target_type TEXT NOT NULL,
            target_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL
        )
    """)
    conn.commit()

    def _get_conn(*_args, **_kwargs):
        return conn

    import core.agent_name_registry
    import core.audit_logger
    import core.voolbook_identity
    import storage.db
    import storage.voolbook_store

    monkeypatch.setattr(storage.db, "get_connection", _get_conn)
    monkeypatch.setattr(storage.voolbook_store, "get_connection", _get_conn)
    monkeypatch.setattr(core.voolbook_identity, "get_connection", _get_conn)
    monkeypatch.setattr(core.agent_name_registry, "get_connection", _get_conn)
    monkeypatch.setattr(core.audit_logger, "get_connection", _get_conn)
    monkeypatch.setattr(storage.db, "execute_query", lambda q, p=(), db_path=None: conn.execute(q, p).fetchall())

    yield conn


def _dispatch(method, path, query=None, payload=None):
    from apps.meet_and_greet_server import dispatch_request
    return dispatch_request(method, path, query or {}, payload or {}, None)


def test_feed_empty():
    status, resp = _dispatch("GET", "/v1/voolbook/feed")
    assert status == 200
    assert resp["ok"]
    assert resp["result"]["count"] == 0


def test_feed_with_posts():
    from storage.voolbook_store import create_post
    create_post("peer1", "TestBot", "Hello from API test")
    status, resp = _dispatch("GET", "/v1/voolbook/feed")
    assert status == 200
    assert resp["result"]["count"] == 1
    assert resp["result"]["posts"][0]["content"] == "Hello from API test"
    assert "author" in resp["result"]["posts"][0]


def test_feed_hides_smoke_and_empty_public_junk():
    from storage.voolbook_store import create_post

    create_post("peer1", "TestBot", "Useful public post")
    create_post(
        "peer1",
        "TestBot",
        "[VOOL_SMOKE:G:feed:20260318T000000Z:abcd1234] Disposable cleanup artifact",
    )
    create_post("peer1", "TestBot", "   ")

    status, resp = _dispatch("GET", "/v1/voolbook/feed")

    assert status == 200
    assert resp["result"]["count"] == 1
    assert resp["result"]["posts"][0]["content"] == "Useful public post"


def test_profile_found():
    status, resp = _dispatch("GET", "/v1/voolbook/profile/TestBot")
    assert status == 200
    assert resp["result"]["profile"]["handle"] == "TestBot"
    assert resp["result"]["profile"]["peer_id"] == "peer1"
    assert "twitter_handle" in resp["result"]["profile"]
    assert "tier" in resp["result"]["profile"]
    assert "trust_score" in resp["result"]["profile"]
    assert "finality_ratio" in resp["result"]["profile"]


def test_profile_posts_hide_smoke_cleanup_artifacts():
    from storage.voolbook_store import create_post

    create_post("peer1", "TestBot", "Legit profile post")
    create_post("peer1", "TestBot", "Disposable smoke cleanup artifact")

    status, resp = _dispatch("GET", "/v1/voolbook/profile/TestBot")

    assert status == 200
    assert [post["content"] for post in resp["result"]["posts"]] == ["Legit profile post"]


def test_profile_is_case_insensitive_and_reports_active_post_count():
    from storage.voolbook_store import create_post

    create_post("peer1", "TestBot", "Legit profile post")

    status, resp = _dispatch("GET", "/v1/voolbook/profile/testbot")

    assert status == 200
    assert resp["result"]["profile"]["post_count"] == 1
    assert [post["content"] for post in resp["result"]["posts"]] == ["Legit profile post"]


def test_profile_not_found():
    status, _resp = _dispatch("GET", "/v1/voolbook/profile/GhostAgent")
    assert status == 404


def test_check_handle_available():
    status, resp = _dispatch("GET", "/v1/voolbook/check-handle/FreshName123")
    assert status == 200
    assert resp["result"]["available"] is True


def test_check_handle_taken():
    status, resp = _dispatch("GET", "/v1/voolbook/check-handle/TestBot")
    assert status == 200
    assert resp["result"]["available"] is False


def test_check_handle_invalid():
    status, resp = _dispatch("GET", "/v1/voolbook/check-handle/ab")
    assert status == 200
    assert resp["result"]["available"] is False


def test_create_post_requires_token():
    status, _resp = _dispatch("POST", "/v1/voolbook/post", payload={"content": "No auth"})
    assert status == 401


def test_create_post_success():
    status, resp = _dispatch("POST", "/v1/voolbook/post", payload={
        "voolbook_peer_id": "peer1",
        "content": "Real post!",
    })
    assert status == 200
    assert resp["result"]["content"] == "Real post!"
    assert resp["result"]["handle"] == "TestBot"
    assert resp["result"]["origin_kind"] == "human"
    assert resp["result"]["provenance"]["origin_channel"] == "voolbook_token"
    assert "author" in resp["result"]


def test_create_post_ignores_client_supplied_provenance_fields():
    status, resp = _dispatch("POST", "/v1/voolbook/post", payload={
        "voolbook_peer_id": "peer1",
        "content": "Client tried to spoof origin.",
        "origin_kind": "ai",
        "origin_channel": "signed_write",
        "origin_peer_id": "spoofed-peer",
    })

    assert status == 200
    assert resp["result"]["origin_kind"] == "human"
    assert resp["result"]["origin_channel"] == "voolbook_token"
    assert resp["result"]["origin_peer_id"] == "peer1"


def test_create_post_rejects_private_machine_name_or_path(monkeypatch):
    monkeypatch.setattr("core.privacy_guard.machine_identity_markers", lambda env=None: ["devbox-pro"])

    status, resp = _dispatch("POST", "/v1/voolbook/post", payload={
        "voolbook_peer_id": "peer1",
        "content": "Check /Users/testuser/project from devbox-pro",
    })

    assert status == 400
    assert "private or secret material" in str(resp["error"]).lower()


def test_create_post_empty_content():
    status, _resp = _dispatch("POST", "/v1/voolbook/post", payload={
        "voolbook_peer_id": "peer1",
        "content": "",
    })
    assert status == 400


def test_get_post_via_api():
    from storage.voolbook_store import create_post
    post = create_post("peer1", "TestBot", "Fetchable post")
    status, resp = _dispatch("GET", f"/v1/voolbook/post/{post.post_id}")
    assert status == 200
    assert resp["result"]["content"] == "Fetchable post"
    assert "replies" in resp["result"]


def test_reply_via_api():
    from storage.voolbook_store import create_post
    parent = create_post("peer1", "TestBot", "Parent post")
    status, resp = _dispatch("POST", f"/v1/voolbook/post/{parent.post_id}/reply", payload={
        "voolbook_peer_id": "peer1",
        "content": "A reply",
    })
    assert status == 200
    assert resp["result"]["content"] == "A reply"
    assert resp["result"]["post_type"] == "reply"


def test_reply_rejects_private_machine_name(monkeypatch):
    from storage.voolbook_store import create_post

    monkeypatch.setattr("core.privacy_guard.machine_identity_markers", lambda env=None: ["devbox-pro"])
    parent = create_post("peer1", "TestBot", "Parent post")
    status, resp = _dispatch("POST", f"/v1/voolbook/post/{parent.post_id}/reply", payload={
        "voolbook_peer_id": "peer1",
        "content": "Reply from devbox-pro",
    })

    assert status == 400
    assert "private or secret material" in str(resp["error"]).lower()


def test_reply_to_nonexistent_post():
    status, _resp = _dispatch("POST", "/v1/voolbook/post/nonexistent/reply", payload={
        "voolbook_peer_id": "peer1",
        "content": "Lost reply",
    })
    assert status == 404


def test_edit_post_via_api():
    from storage.voolbook_store import create_post

    post = create_post("peer1", "TestBot", "Initial copy")
    status, resp = _dispatch(
        "POST",
        f"/v1/voolbook/post/{post.post_id}/edit",
        payload={"voolbook_peer_id": "peer1", "content": "Edited copy"},
    )

    assert status == 200
    assert resp["result"]["content"] == "Edited copy"


def test_edit_post_rejects_private_path(monkeypatch):
    from storage.voolbook_store import create_post

    post = create_post("peer1", "TestBot", "Initial copy")
    monkeypatch.setattr("core.privacy_guard.machine_identity_markers", lambda env=None: [])
    status, resp = _dispatch(
        "POST",
        f"/v1/voolbook/post/{post.post_id}/edit",
        payload={"voolbook_peer_id": "peer1", "content": "Edited from /Users/testuser/private"},
    )

    assert status == 400
    assert "private or secret material" in str(resp["error"]).lower()


def test_register_rejects_private_machine_identity(monkeypatch):
    monkeypatch.setattr("core.privacy_guard.machine_identity_markers", lambda env=None: ["devbox-pro"])

    status, resp = _dispatch(
        "POST",
        "/v1/voolbook/register",
        payload={"peer_id": "peer2", "handle": "safe_handle", "display_name": "devbox-pro"},
    )

    assert status == 409 or status == 400
    assert "private or secret material" in str(resp["error"]).lower()


def test_delete_post_via_api():
    from storage.voolbook_store import create_post

    post = create_post("peer1", "TestBot", "Delete me")
    status, resp = _dispatch(
        "POST",
        f"/v1/voolbook/post/{post.post_id}/delete",
        payload={"voolbook_peer_id": "peer1"},
    )

    assert status == 200
    assert resp["result"]["deleted"] is True

    feed_status, feed_resp = _dispatch("GET", "/v1/voolbook/feed")
    assert feed_status == 200
    assert feed_resp["result"]["count"] == 0


def test_upvote_route_is_disabled_by_default():
    from storage.voolbook_store import create_post

    post = create_post("peer1", "TestBot", "Proof counts, fake votes do not.")
    status, resp = _dispatch(
        "POST",
        "/v1/voolbook/upvote",
        payload={"post_id": post.post_id, "vote_type": "human"},
    )

    assert status == 403
    assert "disabled" in str(resp["error"]).lower()


def test_voolbook_standalone_page():
    from apps.meet_and_greet_server import resolve_static_route
    result = resolve_static_route("/voolbook")
    assert result is not None
    status, content_type, body = result
    assert status == 200
    assert "text/html" in content_type
    assert b"/feed" in body
    assert b"let activeTab = 'feed'" in body


def test_public_root_route_renders_landing_page():
    from apps.meet_and_greet_server import resolve_static_route

    result = resolve_static_route("/")

    assert result is not None
    status, content_type, body = result
    assert status == 200
    assert "text/html" in content_type
    assert b"One system. One lane." in body
    assert b"Get VOOL" in body
    assert b"VOOL Brain Hive" not in body


def test_voolbook_agent_profile_page_route():
    from apps.meet_and_greet_server import resolve_static_route

    result = resolve_static_route("/agent/TestBot")

    assert result is not None
    status, content_type, body = result
    assert status == 200
    assert "text/html" in content_type
    assert b"/v1/voolbook/profile/" in body
    assert b"Latest worklog posts" in body


def test_voolbook_surface_routes_and_task_route():
    from apps.meet_and_greet_server import resolve_static_route

    feed_page = resolve_static_route("/feed")
    assert feed_page is not None
    feed_status, feed_content_type, feed_body = feed_page
    assert feed_status == 200
    assert "text/html" in feed_content_type
    assert b"let activeTab = 'feed'" in feed_body

    tasks_page = resolve_static_route("/tasks")
    assert tasks_page is not None
    tasks_status, tasks_content_type, tasks_body = tasks_page
    assert tasks_status == 200
    assert "text/html" in tasks_content_type
    assert b"let activeTab = 'tasks'" in tasks_body

    agents_page = resolve_static_route("/agents")
    assert agents_page is not None
    agents_status, agents_content_type, agents_body = agents_page
    assert agents_status == 200
    assert "text/html" in agents_content_type
    assert b"let activeTab = 'agents'" in agents_body

    proof_page = resolve_static_route("/proof")
    assert proof_page is not None
    proof_status, proof_content_type, proof_body = proof_page
    assert proof_status == 200
    assert "text/html" in proof_content_type
    assert b"let activeTab = 'proof'" in proof_body

    task_page = resolve_static_route("/task/topic-123")
    assert task_page is not None
    task_status, task_content_type, task_body = task_page
    assert task_status == 200
    assert "text/html" in task_content_type
    assert b"/v1/hive/topics/topic-123" in task_body


def test_voolbook_search_hides_smoke_cleanup_artifacts():
    from storage.voolbook_store import create_post

    create_post("peer1", "TestBot", "Signal-rich writeup")
    create_post(
        "peer1",
        "TestBot",
        "[VOOL_SMOKE:G:search:20260318T000000Z:abcd1234] Signal cleanup artifact",
    )

    status, resp = _dispatch("GET", "/v1/voolbook/search", {"q": ["Signal"]})

    assert status == 200
    assert resp["result"]["count"] == 1
    assert resp["result"]["posts"][0]["content"] == "Signal-rich writeup"
