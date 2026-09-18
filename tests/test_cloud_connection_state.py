"""Connection state: evidence-only verdicts, key material never persisted, probes rate-limited.

The contract behind the chat-header indicator: gray without a key, yellow until THIS key has
passed a live probe, green only on a real 200 from the provider's auth-gated endpoint, red on
401/unreachable — and a key change degrades a stale green to yellow instead of lying.
"""
from __future__ import annotations

import json
import types

import pytest

from core import cloud_connection_state as ccs

FAKE_KEY = "sk-or-v1-" + "a" * 56
OTHER_KEY = "sk-or-v1-" + "b" * 56


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("VOOL_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(ccs, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(ccs, "_resolve_active_provider", lambda: "openrouter")
    keys: dict[str, str] = {}
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: keys.get(name))
    ccs.reset_probe_rate_limit_for_tests()
    return keys


def _fake_requests(monkeypatch, *, status=None, error=None):
    calls: list[dict] = []

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append({"url": url, "headers": dict(headers or {})})
        if error is not None:
            raise error
        # HTTPResponse-shaped: the probe reads .status and the body through the outbound door. A
        # 2xx must carry OpenRouter's documented /key answer; an empty 200 is not a verified key.
        body = json.dumps({"data": {"label": "test", "limit_remaining": None}}).encode("utf-8") if status == 200 else b""
        return types.SimpleNamespace(status=status, read=lambda *_a: body)

    monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", fake_get)
    return calls


def test_no_key_is_gray(env):
    assert ccs.connection_status()["state"] == ccs.STATE_NO_KEY


def test_key_present_but_never_probed_is_untested(env):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    status = ccs.connection_status()
    assert status["state"] == ccs.STATE_UNTESTED
    assert status["checked_at"] is None


def test_probe_200_goes_green_and_hits_the_auth_gated_endpoint(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    calls = _fake_requests(monkeypatch, status=200)
    result = ccs.run_auth_probe(now=1000.0)
    assert result["state"] == ccs.STATE_OK and result["http_status"] == 200
    assert calls[0]["url"].endswith("/key"), "must probe /key — /models is public and proves nothing"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    # Cached verdict now serves without another network call.
    assert ccs.connection_status(now=1001.0)["state"] == ccs.STATE_OK


def test_probe_401_goes_red_unauthorized(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    _fake_requests(monkeypatch, status=401)
    result = ccs.run_auth_probe(now=1000.0)
    assert result["state"] == ccs.STATE_FAILED and result["detail"] == "unauthorized"


def test_probe_network_error_goes_red_unreachable(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    _fake_requests(monkeypatch, error=ConnectionError("down"))
    result = ccs.run_auth_probe(now=1000.0)
    # The probe runs the ONE verification policy, so its refusal words are the verifier's
    # ("network_unavailable"), not the probe's old private spelling ("unreachable").
    assert result["state"] == ccs.STATE_FAILED and result["detail"] == "network_unavailable"


def test_key_change_degrades_a_green_to_untested(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    _fake_requests(monkeypatch, status=200)
    assert ccs.run_auth_probe(now=1000.0)["state"] == ccs.STATE_OK
    env[ccs._CREDENTIAL_NAME] = OTHER_KEY  # rotated key: the old green is not evidence
    assert ccs.connection_status(now=1001.0)["state"] == ccs.STATE_UNTESTED


def test_state_file_never_contains_the_key(env, monkeypatch, tmp_path):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    _fake_requests(monkeypatch, status=200)
    ccs.run_auth_probe(now=1000.0)
    raw = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert FAKE_KEY not in raw
    assert FAKE_KEY[-8:] not in raw, "not even a fragment of the key may be persisted"


def test_probe_rate_limit_serves_cache_between_live_calls(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    calls = _fake_requests(monkeypatch, status=200)
    ccs.run_auth_probe(now=1000.0)
    ccs.run_auth_probe(now=1002.0)  # 2s later: inside the 5s window -> cached, no new call
    assert len(calls) == 1
    ccs.run_auth_probe(now=1010.0)  # past the window -> live again
    assert len(calls) == 2


def test_status_probe_flag_only_reprobes_when_stale(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = FAKE_KEY
    calls = _fake_requests(monkeypatch, status=200)
    ccs.run_auth_probe(now=1000.0)
    ccs.connection_status(probe=True, now=1030.0)  # fresh -> cache, no live call
    assert len(calls) == 1
    ccs.connection_status(probe=True, now=1000.0 + 301.0)  # stale -> live probe
    assert len(calls) == 2


def test_env_key_wins_over_the_store(env, monkeypatch):
    env[ccs._CREDENTIAL_NAME] = OTHER_KEY
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    calls = _fake_requests(monkeypatch, status=200)
    ccs.run_auth_probe(now=1000.0)
    assert calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"


def test_concurrent_writes_never_promote_corrupt_json(env):
    # The routes are served on a threadpool; concurrent _write_cached calls must each use a
    # unique temp (mkstemp) so os.replace only ever promotes a complete file — never a truncated
    # one two writers were racing on the same PID-named temp.
    import json as _json
    import threading

    path = ccs._state_path()
    errors: list[str] = []

    def writer(n: int) -> None:
        for i in range(60):
            ccs._write_cached({"state": "ok", "n": n, "i": i, "pad": "x" * 200})
            try:
                _json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, PermissionError):
                # Windows can briefly deny a read while another writer atomically replaces the
                # destination. That is an availability race, not a promoted partial JSON file;
                # every successful read and the final file must still parse below.
                pass
            except ValueError as exc:  # a corrupt/truncated promoted file
                errors.append(str(exc))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"promoted a corrupt state file: {errors[:2]}"
    _json.loads(path.read_text(encoding="utf-8"))  # final file is valid
