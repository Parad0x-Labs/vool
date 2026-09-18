from __future__ import annotations

import pytest

from storage.voolbook_store import (
    count_posts,
    create_post,
    delete_post,
    get_post,
    list_feed,
    list_replies,
    list_user_posts,
    post_to_dict,
    search_posts,
)


@pytest.fixture(autouse=True)
def _ensure_profile(tmp_path, monkeypatch):
    """Create an in-memory DB with the voolbook schema and a test profile."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("""
        CREATE TABLE voolbook_profiles (
            peer_id TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE,
            canonical_handle TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
            bio TEXT NOT NULL DEFAULT '', avatar_seed TEXT NOT NULL DEFAULT '',
            profile_url TEXT NOT NULL DEFAULT '', post_count INTEGER NOT NULL DEFAULT 0,
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
    conn.execute(
        "INSERT INTO voolbook_profiles (peer_id, handle, canonical_handle, display_name, bio, status, joined_at, last_active_at, updated_at) "
        "VALUES ('peer1', 'TestAgent', 'testagent', 'TestAgent', 'test bio', 'active', '2026-01-01', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO voolbook_profiles (peer_id, handle, canonical_handle, display_name, bio, status, joined_at, last_active_at, updated_at) "
        "VALUES ('peer2', 'OtherAgent', 'otheragent', 'OtherAgent', '', 'active', '2026-01-01', '2026-01-01', '2026-01-01')"
    )
    conn.commit()
    monkeypatch.setattr("storage.voolbook_store.get_connection", lambda: conn)
    yield conn


def test_create_post():
    post = create_post("peer1", "TestAgent", "Hello VoolBook!")
    assert post.post_id
    assert post.handle == "TestAgent"
    assert post.content == "Hello VoolBook!"
    assert post.post_type == "social"
    assert post.origin_kind == "human"
    assert post.origin_channel == "voolbook_token"
    assert post.origin_peer_id == "peer1"
    assert post.status == "active"


def test_create_post_supports_explicit_ai_provenance():
    post = create_post(
        "peer1",
        "TestAgent",
        "Autonomous update",
        origin_kind="ai",
        origin_channel="runtime_fast_path",
        origin_peer_id="peer1",
    )

    assert post.origin_kind == "ai"
    assert post.origin_channel == "runtime_fast_path"
    assert post.origin_peer_id == "peer1"


def test_create_post_rejects_unknown_origin_kind():
    with pytest.raises(ValueError):
        create_post("peer1", "TestAgent", "Bad provenance", origin_kind="spoofed")


def test_get_post():
    post = create_post("peer1", "TestAgent", "Findable post")
    fetched = get_post(post.post_id)
    assert fetched is not None
    assert fetched.content == "Findable post"


def test_get_post_not_found():
    assert get_post("nonexistent") is None


def test_list_feed():
    create_post("peer1", "TestAgent", "Post A")
    create_post("peer2", "OtherAgent", "Post B")
    feed = list_feed(limit=10)
    assert len(feed) >= 2
    assert feed[0].created_at >= feed[1].created_at


def test_list_feed_excludes_replies():
    parent = create_post("peer1", "TestAgent", "Parent")
    create_post("peer2", "OtherAgent", "Reply", parent_post_id=parent.post_id)
    feed = list_feed(limit=100)
    for p in feed:
        assert not p.parent_post_id


def test_list_feed_pagination():
    for i in range(5):
        create_post("peer1", "TestAgent", f"Paginated {i}")
    list_feed(limit=100)
    first_page = list_feed(limit=3)
    assert len(first_page) == 3
    second_page = list_feed(limit=3, before=first_page[-1].created_at)
    assert len(second_page) == 2
    assert second_page[0].post_id != first_page[-1].post_id


def test_list_feed_hides_smoke_and_empty_public_junk():
    create_post("peer1", "TestAgent", "Legit shipping update")
    create_post(
        "peer1",
        "TestAgent",
        "[VOOL_SMOKE:G:cleanup:20260318T000000Z:abcd1234] Disposable cleanup artifact",
    )
    create_post("peer2", "OtherAgent", "   ")

    feed = list_feed(limit=10)

    assert [post.content for post in feed] == ["Legit shipping update"]


def test_list_user_posts():
    create_post("peer1", "TestAgent", "My post")
    create_post("peer2", "OtherAgent", "Their post")
    mine = list_user_posts("TestAgent", limit=10)
    assert all(p.handle == "TestAgent" for p in mine)


def test_list_user_posts_is_case_insensitive():
    create_post("peer1", "TestAgent", "My post")

    mine = list_user_posts("testagent", limit=10)

    assert [post.content for post in mine] == ["My post"]


def test_list_user_posts_hides_smoke_artifacts_on_public_profile():
    create_post("peer1", "TestAgent", "Public profile post")
    create_post("peer1", "TestAgent", "Disposable smoke cleanup artifact for feed hiding")
    mine = list_user_posts("TestAgent", limit=10)
    assert [post.content for post in mine] == ["Public profile post"]


def test_list_replies():
    parent = create_post("peer1", "TestAgent", "Thread starter")
    create_post("peer2", "OtherAgent", "Reply 1", parent_post_id=parent.post_id)
    create_post("peer1", "TestAgent", "Reply 2", parent_post_id=parent.post_id)
    replies = list_replies(parent.post_id, limit=10)
    assert len(replies) == 2
    assert replies[0].created_at <= replies[1].created_at


def test_list_replies_hides_smoke_cleanup_artifacts():
    parent = create_post("peer1", "TestAgent", "Thread starter")
    create_post("peer2", "OtherAgent", "Real reply", parent_post_id=parent.post_id)
    create_post(
        "peer1",
        "TestAgent",
        "[VOOL_SMOKE:G:reply:20260318T000000Z:abcd1234] Disposable cleanup artifact",
        parent_post_id=parent.post_id,
    )

    replies = list_replies(parent.post_id, limit=10)

    assert [post.content for post in replies] == ["Real reply"]


def test_reply_increments_parent_count():
    parent = create_post("peer1", "TestAgent", "Countable thread")
    create_post("peer2", "OtherAgent", "First reply", parent_post_id=parent.post_id)
    updated = get_post(parent.post_id)
    assert updated is not None
    assert updated.reply_count == 1


def test_delete_post():
    post = create_post("peer1", "TestAgent", "Delete me")
    assert delete_post(post.post_id, "peer1")
    assert get_post(post.post_id) is None


def test_delete_only_own_post():
    post = create_post("peer1", "TestAgent", "Not yours")
    assert not delete_post(post.post_id, "peer2")
    assert get_post(post.post_id) is not None


def test_post_to_dict():
    post = create_post("peer1", "TestAgent", "Dict test")
    d = post_to_dict(post)
    assert d["post_id"] == post.post_id
    assert d["content"] == "Dict test"
    assert d["handle"] == "TestAgent"
    assert d["origin_kind"] == "human"
    assert d["provenance"]["locked"] is True
    assert isinstance(d["upvotes"], int)


def test_count_posts():
    initial = count_posts()
    create_post("peer1", "TestAgent", "Counted")
    assert count_posts() == initial + 1


def test_create_post_with_link():
    post = create_post("peer1", "TestAgent", "Check this", link_url="https://example.com", link_title="Example")
    assert post.link_url == "https://example.com"
    assert post.link_title == "Example"


def test_create_research_post():
    post = create_post("peer1", "TestAgent", "Research findings", post_type="research", hive_post_id="hive123", topic_id="topic456")
    assert post.post_type == "research"
    assert post.hive_post_id == "hive123"
    assert post.topic_id == "topic456"


def test_search_posts_hides_smoke_cleanup_artifacts():
    create_post("peer1", "TestAgent", "Signal-rich release note")
    create_post(
        "peer2",
        "OtherAgent",
        "[VOOL_SMOKE:G:search:20260318T000000Z:abcd1234] Signal cleanup artifact",
    )

    results = search_posts("Signal", limit=10)

    assert [post.content for post in results] == ["Signal-rich release note"]
