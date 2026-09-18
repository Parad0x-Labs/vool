"""`vool wallet-address` (read-only adapter over core.wallet) and `vool wallet-export` (retired).

The export command used to reveal a Phantom-format private key behind an OS-consent prompt. There
is no export door any more: the command refuses typed and receipt-backed BEFORE any consent prompt
could be shown, prints no key material, and returns 2. The address command never mints a key: it
reports the canonical wallet's public key when one is registered and says so when none is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.vool_cli import cmd_wallet_address, cmd_wallet_export
from core.vool_wallet import WALLET_FILENAME, b58encode

# base58 (Bitcoin alphabet) run of at least 80 chars ~ a 64-byte Solana secret key.
_B58_KEY = re.compile(r"[1-9A-HJ-NP-Za-km-z]{80,}")
EXPORT = "wallet_export_refused"


@pytest.fixture(autouse=True)
def _iso_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    from core import runtime_paths
    from core.os_consent_gate import set_consent_override_for_tests

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    set_consent_override_for_tests(None)
    yield tmp_path
    set_consent_override_for_tests(None)
    assert not list(Path(tmp_path).rglob(WALLET_FILENAME)), "a CLI command minted a legacy key file"


def _fresh_pubkey() -> str:
    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _export_receipts() -> list:
    from core.faults.recorder import list_faults

    return [f for f in list_faults(limit=200) if f.code == EXPORT]


# --- wallet-address: read-only ---------------------------------------------------------------------

def test_wallet_address_when_core_wallet_is_off_says_so_and_mints_nothing(capsys) -> None:
    assert cmd_wallet_address() == 0
    out = capsys.readouterr().out
    assert "switched off" in out.lower() and "VOOL_WALLET_ENABLED" in out
    assert re.search(r"[1-9A-HJ-NP-Za-km-z]{30,}", out) is None


def test_wallet_address_enabled_but_unregistered_reports_no_wallet(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    assert cmd_wallet_address() == 0
    out = capsys.readouterr().out
    assert "no wallet registered" in out.lower()
    assert re.search(r"[1-9A-HJ-NP-Za-km-z]{30,}", out) is None
    from core.wallet import custody

    assert custody.default_wallet() is None, "a read must not register a wallet as a side effect"


def test_wallet_address_prints_the_registered_core_wallet(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    from core.wallet import custody

    pubkey = _fresh_pubkey()
    custody.create_watch_only_wallet(pubkey, label="ops")
    assert cmd_wallet_address() == 0
    out = capsys.readouterr().out
    assert "core.wallet address" in out and pubkey in out
    assert "watch_only" in out and "solana-devnet" in out
    assert "mainnet" not in out.lower()


def test_wallet_address_json_shapes(monkeypatch, capsys) -> None:
    assert cmd_wallet_address(json_mode=True) == 0
    off = json.loads(capsys.readouterr().out)
    assert off == {"authority": "core.wallet", "address": "", "network": "solana-devnet", "custody_mode": "none", "enabled": False}

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    from core.wallet import custody

    pubkey = _fresh_pubkey()
    custody.register_external_signer_wallet(pubkey)
    assert cmd_wallet_address(json_mode=True) == 0
    on = json.loads(capsys.readouterr().out)
    assert on["address"] == pubkey and on["custody_mode"] == "external_signer" and on["enabled"] is True


# --- wallet-export: retired -------------------------------------------------------------------------

def test_wallet_export_refuses_before_the_consent_prompt(capsys) -> None:
    from core.os_consent_gate import set_consent_override_for_tests

    prompted: list[str] = []

    def _approving_prompt(reason: str) -> bool:
        prompted.append(reason)
        return True  # even a user who WOULD approve gets nothing: there is no door behind the prompt

    set_consent_override_for_tests(_approving_prompt)
    assert cmd_wallet_export() == 2
    out = capsys.readouterr().out
    assert EXPORT in out
    assert _B58_KEY.search(out) is None
    assert "phantom" not in out.lower() and "import private key" not in out.lower()
    assert prompted == [], "the refusal must precede any OS consent prompt"
    receipts = _export_receipts()
    assert receipts and receipts[0].fault_id.startswith("fault-")


def test_wallet_export_with_a_registered_pocket_capable_wallet_still_refuses(monkeypatch, capsys) -> None:
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    from core.wallet import custody

    custody.create_watch_only_wallet(_fresh_pubkey())
    assert cmd_wallet_export() == 2
    assert _B58_KEY.search(capsys.readouterr().out) is None


def test_wallet_export_json_is_a_typed_refusal(capsys) -> None:
    assert cmd_wallet_export(json_mode=True) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["error"] == EXPORT
    assert payload["fault_id"].startswith("fault-") and payload["message"].strip()
    assert _B58_KEY.search(json.dumps(payload)) is None


def test_wallet_export_consent_unavailable_changes_nothing(capsys) -> None:
    from core.os_consent_gate import set_consent_override_for_tests

    def _raise(_reason):
        raise RuntimeError("no consent mechanism")

    set_consent_override_for_tests(_raise)
    assert cmd_wallet_export() == 2  # same typed refusal whether or not a consent mechanism exists
    assert _B58_KEY.search(capsys.readouterr().out) is None
