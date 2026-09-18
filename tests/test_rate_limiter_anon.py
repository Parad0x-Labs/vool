from __future__ import annotations

import pytest

from network import quarantine, rate_limiter


@pytest.fixture(autouse=True)
def _small_limit(monkeypatch):
    """Pin a small, deterministic limit and keep quarantine inert for the test."""

    def fake_get(path: str, default=None):
        if path == "network.max_requests_per_minute_per_peer":
            return 3
        if path == "network.max_failed_messages_before_quarantine":
            return 1000  # never escalate within the test
        return default

    monkeypatch.setattr(rate_limiter.policy_engine, "get", fake_get)
    monkeypatch.setattr(quarantine, "is_peer_quarantined", lambda peer_id: False)
    monkeypatch.setattr(quarantine, "quarantine_peer", lambda peer_id, reason: None)
    yield


def test_anon_nullifier_is_deterministic_and_hides_token():
    token = "client-token-abc"
    n1 = rate_limiter.anon_nullifier(token)
    n2 = rate_limiter.anon_nullifier(token)
    assert n1 == n2  # stable within a process -> the window can throttle it
    assert n1.startswith("anon:")
    assert token not in n1  # raw token is not recoverable from the nullifier
    assert rate_limiter.anon_nullifier("other-token") != n1


def test_anon_path_throttles_per_nullifier():
    token = "anon-client-1"
    rate_limiter.reset_anonymous(token)
    # limit is 3 -> first three allowed, fourth throttled
    assert rate_limiter.allow(token, allow_anon=True) is True
    assert rate_limiter.allow(token, allow_anon=True) is True
    assert rate_limiter.allow(token, allow_anon=True) is True
    assert rate_limiter.allow(token, allow_anon=True) is False
    rate_limiter.reset_anonymous(token)


def test_allow_anonymous_wrapper_matches_flag():
    token = "anon-client-2"
    rate_limiter.reset_anonymous(token)
    assert rate_limiter.allow_anonymous(token) is True
    assert rate_limiter.allow_anonymous(token) is True
    assert rate_limiter.allow_anonymous(token) is True
    assert rate_limiter.allow_anonymous(token) is False
    rate_limiter.reset_anonymous(token)


def test_distinct_tokens_have_independent_windows():
    a, b = "anon-a", "anon-b"
    rate_limiter.reset_anonymous(a)
    rate_limiter.reset_anonymous(b)
    # exhaust a
    for _ in range(3):
        assert rate_limiter.allow(a, allow_anon=True) is True
    assert rate_limiter.allow(a, allow_anon=True) is False
    # b is unaffected
    assert rate_limiter.allow(b, allow_anon=True) is True
    rate_limiter.reset_anonymous(a)
    rate_limiter.reset_anonymous(b)


def test_default_peer_path_unchanged():
    peer = "peer-default-1"
    rate_limiter.reset_peer(peer)
    assert rate_limiter.allow(peer) is True
    assert rate_limiter.allow(peer) is True
    assert rate_limiter.allow(peer) is True
    assert rate_limiter.allow(peer) is False
    rate_limiter.reset_peer(peer)


def test_anon_key_bypasses_peer_quarantine(monkeypatch):
    # An anon nullifier must never be fed to the identity-keyed quarantine path.
    quarantined: list[str] = []
    monkeypatch.setattr(
        quarantine, "quarantine_peer", lambda peer_id, reason: quarantined.append(peer_id)
    )

    def fake_get(path: str, default=None):
        if path == "network.max_requests_per_minute_per_peer":
            return 1
        if path == "network.max_failed_messages_before_quarantine":
            return 1
        return default

    monkeypatch.setattr(rate_limiter.policy_engine, "get", fake_get)

    token = "anon-no-quarantine"
    rate_limiter.reset_anonymous(token)
    assert rate_limiter.allow(token, allow_anon=True) is True
    # breach repeatedly; anon path must not quarantine despite low strike limit
    for _ in range(5):
        rate_limiter.allow(token, allow_anon=True)
    assert quarantined == []
    rate_limiter.reset_anonymous(token)
