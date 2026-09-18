"""oc-l5 meta/pay door: fixed-endpoint outbound sites ride THE one outbound door.

Proves, per representative module (self_update_check GitHub metadata, the read-only
Solana RPC helper in vool_wallet that the x402 receipt verifier reads through,
fresh_data.fx providers), that:

(a) the live call flows through core.remote_fetch_policy.open_remote(_url) — the
    door function is captured, a fake HTTPResponse is returned, and the caller's
    parsed result comes out of that fake;
(b) with an active per-turn veto, the call fails with RemoteFetchRefusedError
    BEFORE any socket — urllib.request.urlopen is replaced by an AssertionError
    raiser, so any attempt to open one detonates.

The legacy signing wallet (``VoolWallet``) that used to be the RPC representative is a
RETIRED money surface: its key creation and load refuse typed and receipt-backed, mint
nothing on disk and open no socket -- pinned here alongside the helper that remains.
"""
from __future__ import annotations

import json

import pytest

from core import remote_fetch_policy
from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    remote_fetch_policy_scope,
)


class FakeHTTPResponse:
    """The HTTPResponse surface the rebound callers consume: status + read()."""

    def __init__(self, payload: dict | bytes, *, status: int = 200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, *_a):
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")


class SocketDetonator:
    """Stands in for urllib.request.urlopen; ANY call means a raw socket opened."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args, **kwargs):  # pragma: no cover - only on failure
        self.calls += 1
        raise AssertionError(f"raw socket opened outside the door: {args} {kwargs}")


# ── self_update_check: GitHub version metadata ──────────────────────────────


def test_self_update_release_fetch_rides_the_door(monkeypatch):
    from core import self_update_check

    seen: dict = {}

    def fake_open(request, *, timeout, context=None):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.headers)
        seen["timeout"] = timeout
        return FakeHTTPResponse({"tag_name": "v9.9.9", "draft": False, "prerelease": False})

    monkeypatch.setattr(remote_fetch_policy, "open_remote", fake_open)
    release = self_update_check.fetch_latest_release(timeout=3.25)

    assert release == {"tag_name": "v9.9.9", "draft": False, "prerelease": False}
    assert seen["url"] == (
        f"https://api.github.com/repos/{self_update_check.DEFAULT_OWNER}/"
        f"{self_update_check.DEFAULT_REPO}/releases/latest"
    )
    assert seen["timeout"] == 3.25  # preserved
    assert seen["headers"].get("Accept") == "application/vnd.github+json"


def test_self_update_veto_refuses_before_any_socket(monkeypatch):
    from core import self_update_check

    detonator = SocketDetonator()
    monkeypatch.setattr("urllib.request.urlopen", detonator)
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        # The module's documented error style: any fetch failure reads as None,
        # never as "up to date with a fetch that was never allowed to run".
        assert self_update_check.fetch_latest_release() is None
    assert detonator.calls == 0


def test_self_update_veto_is_typed_at_the_door(monkeypatch):
    import urllib.request

    detonator = SocketDetonator()
    monkeypatch.setattr("urllib.request.urlopen", detonator)
    req = urllib.request.Request("https://api.github.com/repos/x/y/releases/latest")
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        # Pin WHERE the refusal is raised: inside open_remote itself, before any
        # urlopen could ever be reached.
        with pytest.raises(RemoteFetchRefusedError):
            remote_fetch_policy.open_remote(req, timeout=1.0)
    assert detonator.calls == 0


# ── vool_wallet: the read-only Solana RPC helper (receipt verifier default) ─


_PUBKEY = "9M949AfyYCHp9hUk7crZZx3N6Y8sigyWBN6RM6tFq1q5"


def test_wallet_rpc_read_rides_the_door(monkeypatch):
    import core.vool_wallet as nw

    seen: dict = {}

    def fake_open(request, *, timeout, context=None):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["timeout"] = timeout
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return FakeHTTPResponse({"jsonrpc": "2.0", "id": 1, "result": {"value": 4_000_000_000}})

    monkeypatch.setattr(remote_fetch_policy, "open_remote", fake_open)
    assert nw._rpc_call("getBalance", [_PUBKEY], timeout=2.5) == {"value": 4_000_000_000}

    assert seen["url"] in nw._RPC_ENDPOINTS
    assert seen["method"] == "POST"
    assert seen["timeout"] == 2.5  # preserved
    assert seen["body"]["method"] == "getBalance"
    assert seen["body"]["params"] == [_PUBKEY]


def test_wallet_rpc_veto_refuses_before_any_socket(monkeypatch):
    import core.vool_wallet as nw

    detonator = SocketDetonator()
    monkeypatch.setattr("urllib.request.urlopen", detonator)
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        # Fail closed, LOUDLY: a vetoed on-chain read must not quietly read as None /
        # a zero balance, so the typed refusal propagates out of _rpc_call.
        with pytest.raises(RemoteFetchRefusedError):
            nw._rpc_call("getBalance", [_PUBKEY])
    assert detonator.calls == 0


def test_retired_wallet_creates_no_key_and_opens_no_socket(monkeypatch, tmp_path):
    from core.faults.recorder import fault_by_id
    from core.vool_wallet import VoolWallet
    from core.wallet.authority import LEGACY_RETIRED
    from core.wallet.errors import WalletFault

    detonator = SocketDetonator()
    monkeypatch.setattr("urllib.request.urlopen", detonator)
    monkeypatch.setattr(remote_fetch_policy, "open_remote", detonator)
    monkeypatch.setattr(remote_fetch_policy, "open_remote_url", detonator)

    wallet = VoolWallet(runtime_home=tmp_path, derivation_key=bytes(32))
    for call, surface in ((wallet.generate_and_save, "vool_wallet.generate_and_save"), (wallet.load, "vool_wallet.load")):
        with pytest.raises(WalletFault) as info:
            call()
        assert info.value.code == LEGACY_RETIRED
        assert info.value.context["surface"] == surface
        record = fault_by_id(info.value.fault_id)
        assert record is not None and record.code == LEGACY_RETIRED

    assert list(tmp_path.rglob("*")) == []  # no keys dir, no key file
    assert wallet.exists() is False
    with pytest.raises(RuntimeError):
        _ = wallet.pubkey  # nothing was loaded, so there is no key to read a balance for
    assert detonator.calls == 0


# ── core/fresh_data/fx.py: FX providers ──────────────────────────────────────


def test_fx_default_fetcher_rides_the_door(monkeypatch):
    from core.fresh_data.fx import _default_json_fetcher

    seen: dict = {}

    def fake_open_url(url, *, data=None, headers=None, method="", timeout=30.0, context=None):
        seen.update(url=url, data=data, headers=dict(headers or {}), method=method, timeout=timeout)
        return FakeHTTPResponse({"base": "USD", "rates": {"EUR": 0.91}})

    monkeypatch.setattr(remote_fetch_policy, "open_remote_url", fake_open_url)
    payload = _default_json_fetcher(
        "https://api.frankfurter.dev/v2/rate/USD/EUR", 4.5, {"Accept": "application/json"}
    )

    assert payload == {"base": "USD", "rates": {"EUR": 0.91}}
    assert seen["url"] == "https://api.frankfurter.dev/v2/rate/USD/EUR"
    assert seen["method"] == "GET"
    assert seen["data"] is None
    assert seen["timeout"] == 4.5  # preserved
    assert seen["headers"] == {"Accept": "application/json"}


def test_fx_default_fetcher_veto_refuses_before_any_socket(monkeypatch):
    from core.fresh_data.fx import _default_json_fetcher

    detonator = SocketDetonator()
    monkeypatch.setattr("urllib.request.urlopen", detonator)
    with remote_fetch_policy_scope({"allow_remote_fetch": False}):
        with pytest.raises(RemoteFetchRefusedError):
            _default_json_fetcher(
                "https://api.frankfurter.dev/v2/rate/USD/EUR", 4.5, {}
            )
    assert detonator.calls == 0
