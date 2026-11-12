"""Logged-in X session research boundary — fake tab authority, never a real
account (Checkpoint 3)."""
from __future__ import annotations

import pytest

from core.social_content import x_session_research as xs

NOW = 5_000_000.0


class FakeTabAuthority:
    """Synthetic authenticated-tab fixture. Serves ONLY the granted tab's
    posts; has no cookie/storage/credential surface by construction."""

    def __init__(self, tabs: dict[str, list[xs.VisiblePost]]):
        self._tabs = tabs
        self.pages_requested: list[tuple[str, int]] = []

    def visible_posts(self, grant: xs.XTabGrant, *, page: int) -> list[xs.VisiblePost]:
        self.pages_requested.append((grant.tab_handle, page))
        posts = self._tabs.get(grant.tab_handle, [])
        # page 1 serves everything once; further pages are empty (bounded feed)
        return posts if page == 1 else []


def _grant(surface="following", handle="tab-1", **kw):
    params = dict(
        grant_id="g1", tab_handle=handle, surface=surface, issued_by="operator",
        read_only=True, issued_at=NOW - 10, expires_at=NOW + 600,
    )
    params.update(kw)
    return xs.XTabGrant(**params)


def _post(text, author="acct", **kw):
    params = dict(author=author, text=text, surface="following",
                  engagement={"likes": "1.2K"}, collected_at=NOW)
    params.update(kw)
    return xs.VisiblePost(**params)


# ---------------------------------------------------------------- grant truth


def test_explicit_selected_tab_authorization_succeeds_read_only():
    auth = FakeTabAuthority({"tab-1": [_post("VOOL receipts angle chatter")]})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    assert len(obs.posts) == 1
    assert obs.source_class == "personal_feed"
    assert obs.stopped_reason == "no further posts"


@pytest.mark.parametrize("bad_grant,why", [
    (_grant(issued_by="model"), "model-issued"),
    (_grant(read_only=False), "not read only"),
    (_grant(domain="evil.example"), "wrong domain"),
    (_grant(expires_at=NOW - 1), "expired"),
])
def test_bad_grants_refuse(bad_grant, why):
    auth = FakeTabAuthority({"tab-1": [_post("x")]})
    with pytest.raises(ValueError):
        xs.observe_session(auth, bad_grant, now=NOW)


def test_absent_tab_returns_typed_unavailable_in_production():
    with pytest.raises(xs.AuthenticatedBrowserSessionUnavailable) as exc:
        xs.observe_session(xs.ProductionXTabAuthority(), _grant(), now=NOW)
    assert exc.value.code == xs.UNAVAILABLE_CODE
    assert "authenticated_browser_session_unavailable" in str(exc.value)
    assert exc.value.alternatives  # safe alternatives offered


def test_unrelated_tab_is_invisible():
    auth = FakeTabAuthority({"tab-other": [_post("secret unrelated tab content")]})
    obs = xs.observe_session(auth, _grant(handle="tab-1"), now=NOW)
    assert obs.posts == []


def test_authority_has_no_cookie_or_storage_surface():
    auth = FakeTabAuthority({})
    for forbidden in ("read_cookies", "local_storage", "read_storage", "credentials",
                      "saved_passwords", "developer_token"):
        assert not hasattr(auth, forbidden)
        assert not hasattr(xs.ProductionXTabAuthority(), forbidden)


def test_mutation_intents_absent_and_refused():
    assert not any(
        m in i for i in xs.READ_ONLY_INTENTS
        for m in ("like", "repost", "follow", "bookmark", "settings", "reply")
    )
    with pytest.raises(ValueError):
        xs.assert_intent_set_is_read_only(
            ("x_session.like_post", "x_session.observe_visible_posts")
        )


# ---------------------------------------------------------------- labelling


def test_personal_feed_never_becomes_platform_trend():
    auth = FakeTabAuthority({"tab-1": [_post("niche topic dominates my feed")]})
    obs = xs.observe_session(auth, _grant(surface="for_you"), now=NOW)
    brief = xs.trend_brief(obs, campaign_terms=("niche",), now=NOW)
    assert brief["source_class"] == "personal_feed"
    assert any("not a platform trend" in c for c in brief["what_cannot_be_inferred"])


def test_explore_is_platform_trending_and_search_is_search():
    assert xs.surface_source_class("explore") == "platform_trending"
    assert xs.surface_source_class("search") == "x_search"
    assert xs.surface_source_class("account") == "named_account"


def test_engagement_numbers_are_observations_with_time():
    auth = FakeTabAuthority({"tab-1": [_post("hello")]})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    post = obs.posts[0]
    assert post["engagement"] == {"likes": "1.2K"}  # as shown, not normalized
    assert post["collected_at"] == NOW


def test_reposts_and_near_identical_posts_deduplicated():
    posts = [
        _post("same text exactly", author="a"),
        _post("same  text   exactly", author="b"),   # near-identical
        _post("same text exactly", author="a", reposter="c"),
        _post("different text", author="d"),
    ]
    auth = FakeTabAuthority({"tab-1": posts})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    assert len(obs.posts) == 2
    assert obs.duplicates_removed == 2


def test_quoted_and_repost_authors_distinguished():
    posts = [_post("quote body", author="quoter", quoted_author="original")]
    auth = FakeTabAuthority({"tab-1": posts})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    assert obs.posts[0]["quoted_author"] == "original"
    assert obs.posts[0]["author"] == "quoter"


def test_cross_domain_links_are_observations_only():
    post = _post("see https://news.example/story and https://x.com/status/9")
    auth = FakeTabAuthority({"tab-1": [post]})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    assert obs.cross_domain_links == ["https://news.example/story"]


# ---------------------------------------------------------------- bounds


def test_post_bound_stops_scrolling():
    posts = [_post(f"post {i}") for i in range(100)]
    auth = FakeTabAuthority({"tab-1": posts})
    grant = _grant(bounds=xs.SessionBounds(max_posts=5, max_pages=3))
    obs = xs.observe_session(auth, grant, now=NOW)
    assert len(obs.posts) == 5 and obs.stopped_reason == "post bound"


def test_page_bound_limits_navigation():
    auth = FakeTabAuthority({"tab-1": [_post("p")]})
    grant = _grant(bounds=xs.SessionBounds(max_pages=2))
    xs.observe_session(auth, grant, now=NOW)
    assert [p for _, p in auth.pages_requested] == [1, 2]


def test_character_bound_stops_capture():
    posts = [_post(f"post {i} " + "x" * 4900) for i in range(20)]
    auth = FakeTabAuthority({"tab-1": posts})
    grant = _grant(bounds=xs.SessionBounds(max_chars=10_000, max_posts=100))
    obs = xs.observe_session(auth, grant, now=NOW)
    assert obs.stopped_reason == "character bound"
    assert obs.chars_captured <= 10_000


# ---------------------------------------------------------------- trend brief


def test_trend_brief_shape_with_relevant_angles():
    auth = FakeTabAuthority({"tab-1": [_post("VOOL receipts keep receipts")]})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    brief = xs.trend_brief(obs, campaign_terms=("vool",), now=NOW)
    for key in ("what_is_visible", "why_it_may_matter", "source_class", "sample_limit",
                "what_cannot_be_inferred", "corroboration", "expiry",
                "candidate_angles"):
        assert key in brief
    assert brief["candidate_angles"]


def test_no_relevant_angle_yields_no_post():
    auth = FakeTabAuthority({"tab-1": [_post("completely unrelated cooking content")]})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    brief = xs.trend_brief(obs, campaign_terms=("vool",), now=NOW)
    assert brief["decision"] == "NO_POST"


def test_saved_references_contain_no_credentials_and_persist_only_when_saved(clean_store=None):
    from storage.db import get_connection

    conn = get_connection()
    conn.executescript("DROP TABLE IF EXISTS social_content_records;")
    from storage import social_content_store as store

    auth = FakeTabAuthority({"tab-1": [_post("session text stays local unless saved")]})
    obs = xs.observe_session(auth, _grant(), now=NOW)
    # nothing persisted by observation alone
    assert store.list_trend_observations("p1") == []
    # an explicitly saved reference holds minimum necessary fields, never creds
    saved = store.add_trend_observation(
        project_id="p1", source_class=obs.source_class,
        observed_text=obs.posts[0]["text"], session_scope=obs.grant_id,
    )
    blob = str(saved)
    for forbidden in ("cookie", "password", "token", "session_key"):
        assert forbidden not in blob.lower()
    # restart: the saved reference survives, the raw session does not
    assert store.list_trend_observations("p1")[0]["observation_id"] == saved["observation_id"]
    assert not any("full_feed" in str(o) for o in store.list_trend_observations("p1"))
