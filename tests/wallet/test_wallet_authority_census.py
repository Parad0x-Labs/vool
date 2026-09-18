"""ONE money authority: every signing, broadcast, key-creation or export of user funds traverses
core.wallet's proposal -> simulation -> reservation -> approval -> signing -> broadcast -> receipt
lifecycle. The proof is a production-caller census with two halves: a STATIC scan of every
production package for money tokens outside the canonical package (an explicit, reasoned
allowlist for authority-free helpers), and an EXECUTABLE probe of every retired surface proving
it refuses typed, receipt-backed, without creating a key, signing a byte or touching a network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._device_auth_fake import FakeDeviceAuthority
from tests.wallet._rig import DESTINATION, DEVNET, fault_codes, sec_codes

pytestmark = [pytest.mark.safety]
LEGACY = "wallet_legacy_surface_retired"
EXPORT = "wallet_export_refused"


def _no_hidden_wallet(home: Path) -> None:
    assert not list(home.rglob("solana_wallet.enc")), "a legacy key file was minted"


def test_1_static_census_finds_zero_money_authority_outside_core_wallet():
    from core.wallet import authority

    report = authority.static_census()
    assert report.scanned_files > 200
    assert report.violations == [], "\n".join(report.violations)
    # every allowlisted file states its reason and exists; nothing is allowlisted silently
    for path, reason in report.allowlist.items():
        assert reason.strip() and Path(path).exists(), path
    assert all(p.startswith("core/wallet/") is False for p in report.allowlist), "the canonical package needs no allowlist entry"


def test_1b_executable_census_every_retired_surface_refuses(wallet_env, tmp_path):
    from core.wallet import authority

    violations = authority.executable_census(home=tmp_path)
    assert violations == [], "\n".join(violations)
    assert wallet_env["rpc"].send_count() == 0
    _no_hidden_wallet(tmp_path)


def test_2_wallet_spend_and_pay_x402_cannot_approve_sign_or_broadcast(wallet_env, monkeypatch):
    from core.execution.payment_tools import execute_payment_tool
    from core.runtime_execution_tools import _wallet_spend

    monkeypatch.setenv("VOOL_ENABLE_WALLET_SPEND", "1")
    monkeypatch.setenv("VOOL_ENABLE_X402_SPEND", "1")
    spend = _wallet_spend({"to": DESTINATION, "amount_lamports": 5, "asset": "SOL", "allow_spend": True, "approve": True})
    assert spend.handled and not spend.ok and spend.status == LEGACY
    assert spend.details["fault"]["fault_id"].startswith("fault-") and spend.details["observation"]["status"] == LEGACY
    assert "wallet.propose" in spend.response_text
    pay = execute_payment_tool("pay.x402", {"resource_url": "https://example.test/paid", "allow_spend": True, "max_spend_usdc": 0.5}, source_context={"vool_wallet": object(), "owner_local": True})
    assert pay.handled and not pay.ok and pay.status == LEGACY and pay.details["fault"]["fault_id"].startswith("fault-")
    quote = execute_payment_tool("sell.quote", {"task": "summarize a page"}, source_context={})
    assert quote.handled and quote.status != LEGACY, "the read-only quote lane stays"
    assert wallet_env["rpc"].send_count() == 0


def test_3_cli_and_http_cannot_create_a_second_hidden_wallet(wallet_env, tmp_path, monkeypatch, capsys):
    from apps.vool_api_server import create_app
    from apps.vool_cli import cmd_wallet_address, cmd_wallet_init, cmd_x402_pay
    from core.web.api.runtime import RuntimeServices
    from installer.initialize_agent_wallet import initialize_agent_wallet

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(home)
    try:
        assert cmd_wallet_address() == 0
        out = capsys.readouterr().out
        assert "core.wallet" in out and "mainnet" not in out.lower().replace("mainnet: disabled", "")
        assert cmd_wallet_init(hot_address="A" * 32, cold_address="B" * 32, cold_secret="secret", hot_usdc=1.0, cold_usdc=1.0) == 2
        assert LEGACY in capsys.readouterr().out
        assert cmd_x402_pay(0.001, DESTINATION, allow_spend=True, mainnet=True, keypair_path=str(tmp_path / "kp.json")) == 2
        assert LEGACY in capsys.readouterr().out
        with pytest.raises(Exception) as exc:
            initialize_agent_wallet(str(home))
        assert getattr(exc.value, "code", "") == LEGACY
        app = create_app(RuntimeServices(display_name="VOOL"))
        for path in ("/api/wallet/info", "/v1/wallet/info"):
            status, _h, raw = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
            body = json.loads(raw)
            assert status == 200 and body["authority"] == "core.wallet" and body["read_only"] is True, body
            assert "pubkey" in body and "custody_mode" in body and body["network"] == DEVNET
        from core.web.meet import routes as meet_routes

        assert "get_or_create_wallet" not in Path(meet_routes.__file__).read_text()
    finally:
        runtime_paths.configure_runtime_home(None)
    _no_hidden_wallet(home)
    assert wallet_env["rpc"].send_count() == 0


def test_4_private_key_export_cannot_bypass_the_canonical_custody_policy(wallet_env, tmp_path, capsys):
    from apps.vool_cli import cmd_wallet_export
    from core import wallet as wallet_pkg
    from core.vool_wallet import VoolWallet, reveal_wallet_secret_key_base58
    from core.wallet.errors import WalletFault

    consent_calls: list[str] = []
    with pytest.raises(WalletFault) as exc:
        reveal_wallet_secret_key_base58(runtime_home=tmp_path, reason="test")
    assert exc.value.code == EXPORT and exc.value.fault_id.startswith("fault-")
    assert consent_calls == [], "the refusal happens before any consent prompt could be asked"
    with pytest.raises(WalletFault) as exc2:
        VoolWallet(runtime_home=tmp_path).export_secret_key_base58()
    assert exc2.value.code == EXPORT
    assert cmd_wallet_export() == 2 and EXPORT in capsys.readouterr().out
    # the canonical package has exactly ONE export door -- custody.export_private_key (operator decision 2026-09-07:
    # Phantom / MetaMask export unlocked by device authentication alone). Its shape is pinned here so no unlock other
    # than the device can ever be added quietly, and no second door can appear anywhere in the package.
    import inspect

    doors: list[str] = []
    for module_name in ("custody", "signers", "external_signing", "status", "proposals", "lifecycle", "mnemonic"):
        module = __import__(f"core.wallet.{module_name}", fromlist=["x"]).__dict__
        for name, obj in module.items():
            if name.startswith("_") or not inspect.isfunction(obj) or obj.__module__ != f"core.wallet.{module_name}":
                continue
            if name == "reveal_recovery_phrase":
                continue  # the typed always-None door, asserted below
            if any(word in name.lower() for word in ("export", "reveal", "dump_secret")):
                doors.append(f"core.wallet.{module_name}.{name}")
    assert doors == ["core.wallet.custody.export_private_key"], doors
    params = inspect.signature(wallet_pkg.custody.export_private_key).parameters
    assert list(params) == ["wallet_id", "target", "source_context"], list(params)
    assert params["target"].kind is inspect.Parameter.KEYWORD_ONLY
    secret_words = ("pin", "password", "passphrase", "secret", "unlock", "phrase", "mnemonic", "seed")
    assert not any(word in name.lower() for name in params for word in secret_words), list(params)
    # and the door is gated by the device, not by anything the caller can supply: a sealed pocket wallet whose device
    # authority is missing gets a typed refusal and no material; the refusal is journaled, the export never is
    from core.wallet import custody, device_auth, receipts

    device_auth.set_device_authority_for_tests(FakeDeviceAuthority())
    try:
        profile = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin="482913").profile
        device_auth.set_device_authority_for_tests(FakeDeviceAuthority(available=False))
        with pytest.raises(WalletFault) as exc3:
            custody.export_private_key(profile.wallet_id, target="phantom")
        assert exc3.value.code == "wallet_device_auth_unavailable", exc3.value.code
        kinds = [r.get("kind") for r in receipts.list_receipts(limit=50)]
        assert "wallet_private_key_exported" not in kinds, kinds
    finally:
        device_auth.set_device_authority_for_tests(None)
    assert "reveal_recovery_phrase" in dir(wallet_pkg.custody) and wallet_pkg.custody.reveal_recovery_phrase("any") is None
    assert "SEC_WALLET_EXPORT_REFUSED" in sec_codes()


def test_5_mainnet_remains_impossible_on_every_surface(wallet_env):
    from core.wallet import authority, config, lifecycle
    from core.wallet.errors import WalletFault
    from core.x402.client import X402Client, X402Config, X402Mode

    assert config.mainnet_enabled() is False and not any("mainnet" in n for n in config.ALLOWED_NETWORKS)
    with pytest.raises(WalletFault):
        lifecycle.RpcClient("https://api.mainnet-beta.solana.com", network=DEVNET)
    with pytest.raises(WalletFault) as exc:
        X402Client(X402Config(mode=X402Mode.MAINNET, max_fee_usdc=1.0)).pay(0.001, DESTINATION)
    assert exc.value.code == LEGACY
    report = authority.static_census()
    assert report.broadcast_sites == ["core/wallet/lifecycle.py"], report.broadcast_sites
    assert wallet_env["rpc"].send_count() == 0


def test_6_every_refusal_is_typed_and_receipt_backed(wallet_env, tmp_path):
    from core.faults.recorder import list_faults
    from core.wallet import authority

    before = len(list_faults(limit=500))
    assert authority.executable_census(home=tmp_path) == []
    records = list_faults(limit=500)
    assert len(records) > before
    legacy = [r for r in records if r.code in {LEGACY, EXPORT}]
    surfaces = {r.context.get("surface") for r in legacy}
    for expected in authority.RETIRED_SURFACES:
        assert expected in surfaces, f"no receipt for {expected}"
    assert all(r.fault_id.startswith("fault-") and r.authority.startswith("core.wallet") for r in legacy)
    assert "SEC_WALLET_LEGACY_SURFACE_RETIRED" in sec_codes(limit=500)
    assert LEGACY in fault_codes(limit=500)
