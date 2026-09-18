"""Canonical x402 client after the money-authority retirement (core.wallet is the ONE authority).

What used to be pinned here -- a partially-signed v0 transaction built from a payer keypair and
POSTed to the PayAI facilitator /verify then /settle -- is a retired surface now. Those tests
became refusal tests: every live door on ``core.x402.client`` (``pay`` in DEVNET / MAINNET,
``pay_requirements``, ``_live_pay``, ``_solana_pay``, ``_resolve_signer``, ``_load_payer_keypair``,
``build_solana_x402_payment``, ``wallet_signer``) raises a typed, receipt-backed
:class:`~core.wallet.errors.WalletFault` and -- proven with detonators on every socket door, on
the blockhash helper and on the signer -- no signature, no blockhash fetch, no facilitator call
and no keypair read happens.

What remains and is pinned as behaviour: the STUB receipt (no funds, no sockets), the x402
``paymentRequirements`` wire format (``_build_payment_requirements`` with the sponsored fee payer
from GET /supported through the one outbound door), the read-only blockhash helper, receipt
hashing and the config selectors.
"""
from __future__ import annotations

import json

import pytest

from core.remote_fetch_policy import RemoteFetchRefusedError, remote_fetch_policy_scope
from core.wallet.authority import LEGACY_RETIRED
from core.wallet.errors import WalletFault
from core.x402.client import (
    PAYAI_FACILITATOR,
    PAYAI_SOLANA_FEEPAYER,
    X402Client,
    X402Config,
    X402Mode,
    X402PaymentError,
    X402Receipt,
    _get_latest_blockhash,
    build_solana_x402_payment,
    wallet_signer,
)

# Fixed, valid base58 keys: the system program, devnet USDC, and the advertised PayAI fee payer.
_PAY_TO = "11111111111111111111111111111111"
_ASSET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
_FEE = "2wKupLR9q6wXYppw8Gr2NvWxKBUqm4PPJKkQfoxHDBg4"


class _Detonator:
    """Stands in for a door that must never be reached; any call is a failed test."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError(f"{self.label} was reached: {args[:1]} {sorted(kwargs)}")


class _NeverSigns:
    """A payer / wallet whose every key operation detonates: the retired doors refuse before touching it."""

    def __init__(self) -> None:
        self.touched = 0

    def pubkey(self):
        self.touched += 1
        raise AssertionError("pubkey() was read")

    def sign_message(self, *_args):
        self.touched += 1
        raise AssertionError("sign_message() was called")

    def sign(self, *_args):
        self.touched += 1
        raise AssertionError("sign() was called")

    sign_transaction = sign


class _Resp:
    """HTTPResponse-shaped, as read through open_remote_url."""

    def __init__(self, payload, status=200):
        self._payload, self.status = payload, status

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture
def sealed(monkeypatch):
    """Every outbound door and the blockhash helper detonate; the teardown proves none was reached."""
    doors = {
        "urlopen": _Detonator("urllib.request.urlopen"),
        "open_remote": _Detonator("remote_fetch_policy.open_remote"),
        "open_remote_url": _Detonator("remote_fetch_policy.open_remote_url"),
        "blockhash": _Detonator("x402.client._get_latest_blockhash"),
    }
    monkeypatch.setattr("urllib.request.urlopen", doors["urlopen"])
    monkeypatch.setattr("core.remote_fetch_policy.open_remote", doors["open_remote"])
    monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", doors["open_remote_url"])
    monkeypatch.setattr("core.x402.client._get_latest_blockhash", doors["blockhash"])
    yield doors
    assert {name: door.calls for name, door in doors.items()} == {name: 0 for name in doors}


def _requirements() -> dict:
    return {
        "scheme": "exact", "network": "solana-devnet",
        "maxAmountRequired": "1000", "resource": "https://vool.local/x402/t",
        "description": "t", "mimeType": "application/json",
        "payTo": _PAY_TO, "maxTimeoutSeconds": 120, "asset": _ASSET,
        "extra": {"feePayer": _FEE},
    }


def _assert_refusal(exc: WalletFault, surface: str) -> None:
    """Typed code, a fault receipt on file, and the matching security observation."""
    from core.faults.recorder import fault_by_id
    from core.security_events.catalog import SEC_WALLET_LEGACY_SURFACE_RETIRED
    from core.security_events.store import list_security_events

    assert exc.code == LEGACY_RETIRED
    assert exc.fault_id.startswith("fault-"), exc.to_dict()
    assert exc.user_message
    assert exc.context["surface"] == surface
    assert not isinstance(exc, X402PaymentError)
    record = fault_by_id(exc.fault_id)
    assert record is not None and record.code == LEGACY_RETIRED
    assert record.context.get("surface") == surface
    observations = [e for e in list_security_events(limit=50) if e.fault_id == exc.fault_id]
    assert observations and observations[0].sec_code == SEC_WALLET_LEGACY_SURFACE_RETIRED


# ── retired live doors: typed refusal, nothing signed, nothing fetched ─────────────────────


class TestRetiredLiveDoors:
    @pytest.mark.parametrize("mode", [X402Mode.DEVNET, X402Mode.MAINNET])
    def test_live_pay_refuses_before_any_keypair_read_or_facilitator_call(self, sealed, tmp_path, mode):
        # A surviving loader would raise FileNotFoundError on this path, not the typed refusal.
        never_read = tmp_path / "never-read.json"
        cfg = X402Config(mode=mode, keypair_path=str(never_read), asset_mint=_ASSET)
        with pytest.raises(WalletFault) as info:
            X402Client(cfg).pay(0.001, _PAY_TO, "sess-live")
        _assert_refusal(info.value, "x402.client.pay")
        assert not never_read.exists()

    def test_devnet_without_keypair_path_is_the_same_typed_refusal(self, sealed):
        # Previously X402PaymentError("keypair_path is required"); a missing key is no longer the reason.
        cfg = X402Config(mode=X402Mode.DEVNET, keypair_path=None)
        with pytest.raises(WalletFault) as info:
            X402Client(cfg).pay(0.001, _PAY_TO, "sess-4")
        _assert_refusal(info.value, "x402.client.pay")

    def test_injected_signer_is_never_consulted(self, sealed):
        signer = _NeverSigns()
        cfg = X402Config(mode=X402Mode.DEVNET, asset_mint=_ASSET)
        with pytest.raises(WalletFault) as info:
            X402Client(cfg, signer=signer).pay(0.001, _PAY_TO, "sess-signer")
        _assert_refusal(info.value, "x402.client.pay")
        assert signer.touched == 0

    def test_amount_guards_still_fire_ahead_of_the_refusal(self, sealed):
        client = X402Client(X402Config(mode=X402Mode.DEVNET, max_fee_usdc=0.01))
        with pytest.raises(ValueError, match="max_fee_usdc"):
            client.pay(0.05, _PAY_TO, "sess-big")
        with pytest.raises(ValueError, match="must be > 0"):
            client.pay(0.0, _PAY_TO, "sess-zero")

    @pytest.mark.parametrize("mode", [X402Mode.DEVNET, X402Mode.MAINNET])
    def test_pay_requirements_refuses(self, sealed, mode):
        signer = _NeverSigns()
        cfg = X402Config(mode=mode, asset_mint=_ASSET)
        with pytest.raises(WalletFault) as info:
            X402Client(cfg, signer=signer).pay_requirements(_requirements(), "sess-req")
        _assert_refusal(info.value, "x402.client.pay_requirements")
        assert signer.touched == 0

    def test_internal_live_helpers_refuse(self, sealed):
        signer = _NeverSigns()
        client = X402Client(X402Config(mode=X402Mode.DEVNET, asset_mint=_ASSET), signer=signer)
        for call, surface in (
            (lambda: client._live_pay(0.001, _PAY_TO, "s"), "x402.client.pay"),
            (lambda: client._solana_pay(0.001, _PAY_TO, "s"), "x402.client.pay"),
            (client._resolve_signer, "x402.client._load_payer_keypair"),
        ):
            with pytest.raises(WalletFault) as info:
                call()
            _assert_refusal(info.value, surface)
        assert signer.touched == 0

    def test_keypair_loader_refuses_even_when_a_real_keypair_file_exists(self, sealed, tmp_path):
        Keypair = pytest.importorskip("solders.keypair").Keypair
        kp = Keypair.from_seed(bytes(range(32)))
        path = tmp_path / "payer.json"
        path.write_text(json.dumps(list(bytes(kp))))
        before = path.read_bytes()

        with pytest.raises(WalletFault) as info:
            X402Client(X402Config(mode=X402Mode.DEVNET, keypair_path=str(path)))._load_payer_keypair()
        _assert_refusal(info.value, "x402.client._load_payer_keypair")
        assert path.read_bytes() == before

        with pytest.raises(WalletFault) as missing:
            X402Client(X402Config(mode=X402Mode.DEVNET, keypair_path=None))._load_payer_keypair()
        _assert_refusal(missing.value, "x402.client._load_payer_keypair")

    def test_build_solana_x402_payment_refuses_before_blockhash_or_signature(self, sealed):
        payer = _NeverSigns()
        with pytest.raises(WalletFault) as info:
            build_solana_x402_payment(payer, _requirements(), "https://rpc.example", decimals=6, compute_unit_limit=50_000)
        _assert_refusal(info.value, "x402.client.build_solana_x402_payment")
        assert payer.touched == 0
        assert sealed["blockhash"].calls == 0

    def test_wallet_signer_refuses_and_never_touches_the_wallet(self, sealed):
        wallet = _NeverSigns()
        with pytest.raises(WalletFault) as info:
            wallet_signer(wallet)
        _assert_refusal(info.value, "x402.client.wallet_signer")
        assert wallet.touched == 0


# ── the stub lane stays: a deterministic receipt, no funds, no sockets ─────────────────────


class TestStubLane:
    def test_stub_pay_returns_a_receipt_without_funds_or_sockets(self, sealed):
        receipt = X402Client(X402Config(mode=X402Mode.STUB)).pay(0.001, _PAY_TO, "sess-stub")
        assert receipt.mode == "stub"
        assert receipt.payment_tx.startswith("stub-tx-")
        assert receipt.facilitator_sig.startswith("stub-fac-sig-")
        assert receipt.session_id == "sess-stub" and receipt.amount_usdc == 0.001
        assert len(receipt.receipt_hash) == 64


# ── what remains of the wire format: requirements, fee payer, blockhash ────────────────────


class TestRemainingWireFormat:
    def test_payment_requirements_wire_format(self, monkeypatch):
        seen: list[str] = []

        def fake_open_remote_url(url, *, data=None, headers=None, method="", timeout=0.0, context=None):
            seen.append(url)
            return _Resp({"kinds": [
                {"scheme": "exact", "network": "solana", "extra": {"feePayer": "WRONG-NETWORK"}},
                {"scheme": "exact", "network": "solana-devnet", "extra": {"feePayer": _FEE}},
            ]})

        monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", fake_open_remote_url)
        client = X402Client(X402Config(mode=X402Mode.DEVNET, asset_mint=_ASSET, memo="vool-x402"))

        req = client._build_payment_requirements(0.001, _PAY_TO, "sess-wire")

        assert req == {
            "scheme": "exact",
            "network": "solana-devnet",
            "maxAmountRequired": "1000",  # atomic units, rounded not truncated
            "resource": "https://vool.local/x402/sess-wire",
            "description": "vool x402 settlement sess-wire",
            "mimeType": "application/json",
            "payTo": _PAY_TO,
            "maxTimeoutSeconds": 120,
            "asset": _ASSET,
            "extra": {"feePayer": _FEE, "memo": "vool-x402"},
        }
        assert seen == [f"{PAYAI_FACILITATOR}/supported"]
        # the fee payer is cached per client: a second build fetches nothing
        client._build_payment_requirements(0.002, _PAY_TO, "sess-wire-2")
        assert len(seen) == 1

    def test_fee_payer_falls_back_to_the_advertised_constant_on_a_transient_failure(self, monkeypatch):
        def broken(*_args, **_kwargs):
            raise OSError("/supported unreachable")

        monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", broken)
        assert X402Client(X402Config(mode=X402Mode.MAINNET))._facilitator_fee_payer() == PAYAI_SOLANA_FEEPAYER

    def test_fee_payer_veto_fails_closed_before_any_socket(self, monkeypatch):
        detonator = _Detonator("urllib.request.urlopen")
        monkeypatch.setattr("urllib.request.urlopen", detonator)
        with remote_fetch_policy_scope({"allow_remote_fetch": False}), pytest.raises(RemoteFetchRefusedError):
            X402Client(X402Config(mode=X402Mode.DEVNET))._facilitator_fee_payer()
        assert detonator.calls == 0

    def test_latest_blockhash_reads_through_the_door(self, monkeypatch):
        seen: dict = {}

        def fake_open_remote_url(url, *, data=None, headers=None, method="", timeout=0.0, context=None):
            seen.update(url=url, body=json.loads(data), method=method, timeout=timeout)
            return _Resp({"jsonrpc": "2.0", "id": 1, "result": {"value": {"blockhash": "BLOCKHASH-1"}}})

        monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", fake_open_remote_url)
        assert _get_latest_blockhash("https://rpc.example") == "BLOCKHASH-1"
        assert seen["url"] == "https://rpc.example"
        assert seen["method"] == "POST"
        assert seen["body"]["method"] == "getLatestBlockhash"
        assert seen["body"]["params"] == [{"commitment": "finalized"}]

    def test_latest_blockhash_surfaces_an_http_error(self, monkeypatch):
        monkeypatch.setattr("core.remote_fetch_policy.open_remote_url", lambda *a, **k: _Resp({}, status=503))
        with pytest.raises(RuntimeError, match="HTTP 503"):
            _get_latest_blockhash("https://rpc.example")


# ── receipt hashing + config selectors ─────────────────────────────────────────────────────


class TestReceiptAndConfig:
    def test_receipt_hash_is_canonical_and_ignores_the_facilitator_signature(self):
        base = dict(session_id="sess-h", payment_tx="SIG", amount_usdc=0.005, recipient_wallet=_PAY_TO, timestamp=1_700_000_000.123, mode="devnet")
        one = X402Receipt(facilitator_sig="a", **base)
        two = X402Receipt(facilitator_sig="b", **base)
        assert one.receipt_hash == two.receipt_hash and len(one.receipt_hash) == 64
        assert X402Receipt(facilitator_sig="a", **{**base, "amount_usdc": 0.006}).receipt_hash != one.receipt_hash
        assert one.to_dict()["receipt_hash"] == one.receipt_hash

    def test_network_name(self):
        assert X402Config(mode=X402Mode.DEVNET).network_name == "solana-devnet"
        assert X402Config(mode=X402Mode.MAINNET).network_name == "solana"

    def test_single_facilitator_host(self):
        assert X402Config(mode=X402Mode.DEVNET).effective_facilitator == PAYAI_FACILITATOR
        assert X402Config(mode=X402Mode.MAINNET).effective_facilitator == PAYAI_FACILITATOR

    def test_asset_override_else_usdc(self):
        from core.x402.client import USDC_MINT_DEVNET

        assert X402Config(mode=X402Mode.DEVNET).effective_asset == USDC_MINT_DEVNET
        assert X402Config(mode=X402Mode.DEVNET, asset_mint="MINT").effective_asset == "MINT"
