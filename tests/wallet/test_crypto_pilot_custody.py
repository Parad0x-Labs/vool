"""Crypto Pilot, stage 3: pilot custody and the setup lifecycle.

Every assertion reads an environment fact (profile rows, the durable throttle table, the journal files, the
status payload, the key-leak detector over every surface), never prose.
"""
from __future__ import annotations

import json
import pathlib
import threading
import uuid

import pytest

from core.wallet.errors import WalletFault
from tests.wallet._rig import key_leaked

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_MAINNET = "eip155:8453"
PIN = "482913"
KAT_K1_ADDRESS = "0x7E5F4552091A69125d5DfCb7b8C2659029395Bdf"


@pytest.fixture
def pilot_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    yield tmp_path
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


def _rows() -> list[dict]:
    from core.wallet.store import connection

    with connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(wallet_profiles)").fetchall()]
        return [dict(zip(cols, row, strict=True)) for row in conn.execute("SELECT * FROM wallet_profiles").fetchall()]


def _create(network: str = SOLANA_MAINNET, *, method: str = "pin", credential: str = PIN, confirmation: str | None = None, key: str | None = None):
    from core.wallet import pilot_custody

    return pilot_custody.create_pilot_wallet(
        network=network, method=method, credential=credential,
        credential_confirmation=credential if confirmation is None else confirmation,
        creation_key=key or f"create-{uuid.uuid4().hex}",
    )


# --- creation gates: refused before any row, material, Keychain write or journal value -------------------

@pytest.mark.parametrize("credential, confirmation, code", [
    (PIN, "482914", "wallet_credential_mismatch"),
    ("12345", "12345", "wallet_pin_invalid"),            # below 6 digits
    ("1234567890123", "1234567890123", "wallet_pin_invalid"),  # above 12 digits
    ("４８２９１３", "４８２９１３", "wallet_pin_invalid"),  # full-width digits: isdigit() but not ASCII
])
def test_pilot_creation_refuses_a_weak_or_unconfirmed_pin_before_any_row(pilot_home, credential, confirmation, code):
    with pytest.raises(WalletFault) as refused:
        _create(credential=credential, confirmation=confirmation)
    assert refused.value.code == code
    assert _rows() == []


def test_pilot_creation_refuses_an_all_digit_password_and_the_inactive_environment(pilot_home):
    from core.wallet import environment

    with pytest.raises(WalletFault) as weak:
        _create(method="password", credential="1234567890")
    assert weak.value.code == "wallet_password_invalid"
    with pytest.raises(WalletFault) as inactive:
        _create(network=SOLANA_DEVNET)  # fresh install: Mainnet is active
    assert inactive.value.code == "wallet_environment_inactive"
    environment.set_active_environment("testnet")
    with pytest.raises(WalletFault) as inactive_mainnet:
        _create(network=SOLANA_MAINNET)
    assert inactive_mainnet.value.code == "wallet_environment_inactive"
    assert _rows() == []


def test_pilot_creation_refuses_ephemeral_key_storage(pilot_home, monkeypatch):
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "ephemeral")
    with pytest.raises(WalletFault) as refused:
        _create()
    assert refused.value.code == "wallet_storage_class_refused"
    assert _rows() == []


# --- reserve first, idempotent, never a second wallet -----------------------------------------------------

def test_the_reserve_row_exists_before_material_and_a_lost_response_never_makes_a_second_wallet(pilot_home, monkeypatch):
    from core.wallet import pilot_custody

    seen_rows_at_generation: list[list[dict]] = []

    def failing_generation(*_args, **_kwargs):
        seen_rows_at_generation.append(_rows())
        raise RuntimeError("injected crash after the reserve row")

    key = "create-lost-response"
    original = pilot_custody._generate_material
    monkeypatch.setattr(pilot_custody, "_generate_material", failing_generation)
    with pytest.raises(Exception):
        _create(key=key)
    assert len(seen_rows_at_generation) == 1 and [r["setup_state"] for r in seen_rows_at_generation[0]] == ["generating"]
    reserved = _rows()
    assert len(reserved) == 1 and reserved[0]["sealed_blob"] == "" and reserved[0]["setup_state"] == "generating"
    monkeypatch.setattr(pilot_custody, "_generate_material", original)
    again = _create(key=key)
    assert again["wallet_id"] == reserved[0]["wallet_id"]
    assert len(_rows()) == 1


def test_the_same_creation_key_returns_the_same_ready_to_reveal_wallet(pilot_home):
    first = _create(key="create-twice")
    second = _create(key="create-twice")
    assert first["wallet_id"] == second["wallet_id"] and second["setup_state"] == "awaiting_backup"
    assert len(_rows()) == 1
    assert "backup_value" not in first and "backup_value" not in second


# --- backup formats verified independently ---------------------------------------------------------------

def test_the_solana_backup_is_the_base58_64_byte_keypair_of_the_stored_address(pilot_home):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import pilot_custody

    created = _create()
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    raw = b58decode(revealed["backup_value"])
    assert len(raw) == 64 and b58encode(raw[32:]) == created["address"] == revealed["address"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    assert Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes_raw() == raw[32:]
    assert revealed["backup_format"] == "solana_keypair_base58" and revealed["shown_once"] is True


def test_the_evm_backup_is_32_byte_hex_whose_independent_address_is_the_stored_one(pilot_home):
    from eth_hash.auto import keccak
    from eth_keys import keys

    from core.wallet import pilot_custody

    created = _create(network=BASE_MAINNET)
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    value = revealed["backup_value"]
    assert value.startswith("0x") and len(value) == 66
    public = keys.PrivateKey(bytes.fromhex(value[2:])).public_key.to_bytes()
    independent = "0x" + keccak(public)[-20:].hex()
    assert independent.lower() == created["address"].lower()
    assert pilot_custody.evm_address_for_key((1).to_bytes(32, "big")) == KAT_K1_ADDRESS


# --- one reveal, credential-bound, durable throttle ---------------------------------------------------------

def test_reveal_needs_the_credential_and_happens_exactly_once_even_under_concurrency(pilot_home):
    from core.wallet import pilot_custody

    created = _create()
    with pytest.raises(WalletFault) as missing:
        pilot_custody.reveal_pilot_backup(created["wallet_id"], credential="")
    assert missing.value.code == "wallet_pin_invalid"
    outcomes: list[str] = []
    barrier = threading.Barrier(4)

    def reveal():
        barrier.wait()
        try:
            pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
            outcomes.append("revealed")
        except WalletFault as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=reveal) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert outcomes.count("revealed") == 1 and outcomes.count("wallet_backup_unavailable") == 3, outcomes
    with pytest.raises(WalletFault) as again:
        pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    assert again.value.code == "wallet_backup_unavailable"


def test_a_pilot_wallet_stores_no_offline_verifier(pilot_home):
    from core.wallet import custody

    created = _create()
    row = next(r for r in _rows() if r["wallet_id"] == created["wallet_id"])
    assert row["pin_hash"] == "" and row["pin_salt"] == "" and row["seal_policy"] == "pilot-v3"
    assert custody.verify_secret(created["wallet_id"], PIN) is False


def test_failed_unlocks_throttle_durably_across_a_process_restart(pilot_home):
    """The throttle lives in the store, not in memory: a fresh process on the same database is still locked.
    The test database path is configured in-process by the conftest, so the child is given it explicitly."""
    import os
    import subprocess
    import sys

    from core.wallet import pilot_custody
    from storage.db import active_default_db_path

    created = _create()
    for _ in range(5):
        with pytest.raises(WalletFault) as wrong:
            pilot_custody.reveal_pilot_backup(created["wallet_id"], credential="000000")
        assert wrong.value.code == "wallet_pin_invalid"
    child = (
        "import sys\n"
        "from storage.db import configure_default_db_path, active_default_db_path\n"
        "from core.runtime_continuity import configure_runtime_continuity_db_path\n"
        "configure_default_db_path(sys.argv[1]); configure_runtime_continuity_db_path(active_default_db_path())\n"
        "from core.wallet import pilot_custody\n"
        "from core.wallet.errors import WalletFault\n"
        "try:\n"
        "    pilot_custody.reveal_pilot_backup(sys.argv[2], credential=sys.argv[3])\n"
        "    print('REVEALED')\n"
        "except WalletFault as exc:\n"
        "    print('FAULT', exc.code, exc.context.get('retry_after_seconds', ''))\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", child, str(active_default_db_path()), created["wallet_id"], PIN],
        capture_output=True, text=True, timeout=120, env=dict(os.environ), cwd=str(pilot_home),
    )
    assert result.returncode == 0, result.stderr[-2000:]
    line = result.stdout.strip().splitlines()[-1]
    assert line.startswith("FAULT wallet_unlock_throttled") and int(line.split()[-1]) > 0, result.stdout
    assert pilot_custody.setup_view(created["wallet_id"])["setup_state"] == "awaiting_backup"


# --- acknowledge, cancel, resume -----------------------------------------------------------------------------

def test_acknowledge_cancel_and_resume_never_reveal_twice_or_delete_a_wallet(pilot_home):
    from core.wallet import pilot_custody

    created = _create()
    with pytest.raises(WalletFault) as early:
        pilot_custody.acknowledge_pilot_backup(created["wallet_id"], credential=PIN)
    assert early.value.code == "wallet_setup_state_invalid"
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    pilot_custody.cancel_pilot_setup(created["wallet_id"], credential=PIN)
    assert pilot_custody.setup_view(created["wallet_id"])["setup_state"] == "cancelled"
    assert len(_rows()) == 1
    resumed = pilot_custody.resume_pilot_setup(created["wallet_id"], credential=PIN)
    assert resumed["setup_state"] == "backup_revealed"
    with pytest.raises(WalletFault) as twice:
        pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    assert twice.value.code == "wallet_backup_unavailable"
    with pytest.raises(WalletFault) as wrong_token:
        pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token="not-the-token")
    assert wrong_token.value.code == "wallet_setup_state_invalid"
    done = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    assert done["setup_state"] == "ready"


# --- legacy boundaries -------------------------------------------------------------------------------------

def test_legacy_export_refuses_a_pilot_wallet(pilot_home):
    from core.wallet import custody

    created = _create()
    with pytest.raises(WalletFault) as refused:
        custody.export_private_key(created["wallet_id"], target="phantom")
    assert refused.value.code == "wallet_export_unavailable"
    assert refused.value.context.get("reason") == "pilot_backup_is_setup_only"


# --- the key never leaves custody -------------------------------------------------------------------------

def test_the_backup_never_reaches_status_receipts_journal_projection_or_the_database(pilot_home):
    """Read every store through its own decoding path: the blackbox blobs are encrypted and the liquefy
    projection is compressed, so a byte scan of their files would prove nothing."""
    from core.blackbox import store as blackbox_store
    from core.liquefy import hooks as liquefy_hooks
    from core.wallet import pilot_custody, receipts, status
    from storage.db import active_default_db_path

    created = _create()
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    secret = revealed["backup_value"]
    projection = liquefy_hooks.get_default_store()
    surfaces = {
        "status": json.dumps(status.wallet_status(), default=str),
        "receipts": json.dumps(receipts.list_receipts(limit=100), default=str),
        "blackbox_entries": json.dumps(blackbox_store.default_store().entries(), default=str),
        "liquefy_hot": json.dumps(projection.hot_entries() if projection is not None else [], default=str),
        "setup_view": json.dumps(pilot_custody.setup_view(created["wallet_id"]), default=str),
        "wallet_database": pathlib.Path(active_default_db_path()).read_bytes().decode("latin-1"),
    }
    for name, text in surfaces.items():
        assert not key_leaked(secret, text), name
    from core.secret_redaction import redact_secrets

    assert secret not in redact_secrets(f"the backup was {secret}")


# --- store registration (the suite wipes every wallet table between tests) ---------------------------------

def test_the_unlock_attempts_table_is_a_wallet_store_table_the_suite_wipes():
    from core.wallet import store
    from tests.conftest import RUNTIME_TABLES

    assert "wallet_unlock_attempts" in store.TABLES
    assert "wallet_unlock_attempts" in RUNTIME_TABLES


def test_a_revealed_evm_backup_is_scrubbed_from_free_text_by_its_exact_value(pilot_home):
    """0x + 64 hex matches no canonical masker rule, so only the exact-value registration at reveal hides it."""
    from core.secret_redaction import contains_secret, redact_secrets
    from core.wallet import pilot_custody

    created = _create(network=BASE_MAINNET)
    secret = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)["backup_value"]
    line = f"the saved backup is {secret} for later"
    assert secret not in redact_secrets(line)
    assert contains_secret(line) is True


def test_a_pilot_wallet_is_refused_typed_at_the_legacy_signing_doors(pilot_home, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import approval, lifecycle, pilot_custody, proposals
    from tests.wallet._rig import ScriptedRpc

    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    with ScriptedRpc() as devnet_node:
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", devnet_node.url)
        created = _create(network=SOLANA_DEVNET)
        revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
        pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
        destination = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
        proposal = proposals.propose_transaction(wallet_id=created["wallet_id"], destination=destination, amount_minor=1000, asset="SOL", origin=proposals.ORIGIN_USER)
        engine = lifecycle.default_lifecycle()
        assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        with pytest.raises(WalletFault) as refused:
            engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
        # Solana Devnet's transfer lane is complete: the legacy door still refuses, and names the approval sheet
        assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_network_disabled", "pilot_transfer_needs_quote_approval")
        assert proposals.approval_refusals(proposal.proposal_id) == 0
        assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
        assert "sendTransaction" not in [call.get("method") for call in devnet_node.calls]
