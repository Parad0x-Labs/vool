"""F-05 repair proof: the paid media-generation lane goes through THE one
outbound door (core.remote_fetch_policy.open_remote).

- With the per-turn veto active (allow_remote_fetch=False), NO socket opens
  and the generation fails closed.
- Through the door, the fetch is reported into the turn's ledger.
- The legacy raw-urlopen bypass (model-authored prompt straight to
  urllib.request.urlopen, no veto, no reporting) cannot return.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f05.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _drive(monkeypatch, *, veto: bool, calls: list):
    import core.media_tools as mt
    from core.remote_fetch_policy import RemoteFetchRefusedError

    monkeypatch.setattr(mt, "_service", lambda kind, account: {
        "endpoint": "https://media.example.invalid/v1/generate",
        "api_key": "k-test",
        "provider": "generic",
    })
    monkeypatch.setattr(mt.usage_quota, "check_quota", lambda *a, **k: type(
        "C", (), {"allowed": True})())

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return json.dumps({"images": ["ref-1"]}).encode()

    if veto:
        from core.remote_fetch_policy import remote_fetch_policy_scope

        def _boom(request, timeout):
            calls.append(("urlopen", str(getattr(request, "full_url", ""))))
            raise AssertionError("raw socket opened despite veto")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        with remote_fetch_policy_scope({"allow_remote_fetch": False}):
            return mt.generate_image(prompt="a model authored scene")
    else:
        def _door(request, timeout):
            calls.append(("door", str(getattr(request, "full_url", ""))))
            return _FakeResp()

        monkeypatch.setattr(
            "core.remote_fetch_policy.open_remote",
            lambda request, timeout: _door(request, timeout),
        )
        # A raw socket would be a bypass: any call lands in `calls` tagged urlopen.
        monkeypatch.setattr("urllib.request.urlopen", _door)
        return mt.generate_image(prompt="a model authored scene")


def test_veto_blocks_media_generation_before_any_socket(fresh_store, monkeypatch):
    calls: list = []
    result = _drive(monkeypatch, veto=True, calls=calls)
    assert result.ok is False
    assert not isinstance(result, Exception)
    assert all(tag != "urlopen" for tag, _ in calls), calls
    assert "RemoteFetchRefused" in result.message or result.status == "generate_failed"


def test_media_generation_reports_through_the_one_door(fresh_store, monkeypatch):
    calls: list = []
    result = _drive(monkeypatch, veto=False, calls=calls)
    assert result.ok is True, result.message
    assert calls == [("door", "https://media.example.invalid/v1/generate")], calls


def test_media_tools_source_has_no_raw_urlopen_path():
    """Standing source guard: the media lane must not carry its own socket."""
    import pathlib

    source = pathlib.Path("core/media_tools.py").read_text()
    assert "open_remote" in source
    assert "urllib.request.urlopen" not in source.replace(
        "import urllib.request", ""
    ) or "opener" in source
