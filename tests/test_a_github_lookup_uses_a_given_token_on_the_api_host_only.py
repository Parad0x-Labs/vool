"""A GitHub add-on lookup carries a given token to GitHub's API host and to no other host.

CI on 4b39b40d5 and b19b37bce (2026-11-24, shard 7): the real-GitHub add-on journey met "GitHub's
public download limit was reached" because the anonymous allowance is counted per network address and
a shared runner address had spent it. A token in VOOL_GITHUB_TOKEN lifts that; it must never reach
raw file hosts, plain http, another port or a look-alike host, and without one nothing changes.
"""
from __future__ import annotations

import random
import secrets

import pytest

import core.addon_discovery as discovery

_RNG = random.Random()


def _token() -> str:
    return _RNG.choice(("ghs_", "github_pat_", "")) + secrets.token_hex(_RNG.randint(12, 24))


@pytest.fixture()
def sent(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    calls: list[tuple[str, dict]] = []

    def fake_fetch(url, *, data=None, headers=None, limit=0):
        calls.append((url, dict(headers or {})))
        return b"{}", {}

    monkeypatch.setattr(discovery, "fetch_bytes", fake_fetch)
    return calls


def test_the_api_host_gets_the_token(monkeypatch, sent):
    token = _token()
    monkeypatch.setenv(discovery.GITHUB_TOKEN_ENV, "  " + token + "\n")
    path = _RNG.choice(("/repos/owner/repo", "/search/repositories?q=x", "/repos/a/b/git/trees/abc?recursive=1"))
    discovery._request("https://api.github.com" + path, 1024)
    assert sent[-1][1]["Authorization"] == "Bearer " + token


@pytest.mark.parametrize("url", [
    "https://raw.githubusercontent.com/owner/repo/abc/SKILL.md",
    "http://api.github.com/repos/owner/repo",
    "https://api.github.com:8443/repos/owner/repo",
    "https://api.github.com.example.test/repos/owner/repo",
    "https://github.com/owner/repo",
])
def test_no_other_host_gets_the_token(monkeypatch, sent, url):
    monkeypatch.setenv(discovery.GITHUB_TOKEN_ENV, _token())
    assert "Authorization" not in discovery._github_headers(url)


def test_without_a_token_the_lookup_is_anonymous(monkeypatch, sent):
    monkeypatch.delenv(discovery.GITHUB_TOKEN_ENV, raising=False)
    if _RNG.random() < 0.5:
        monkeypatch.setenv(discovery.GITHUB_TOKEN_ENV, "   ")
    discovery._request("https://api.github.com/repos/owner/repo", 1024)
    assert "Authorization" not in sent[-1][1]
    assert sent[-1][1]["Accept"] == "application/vnd.github+json"
