"""The person's wallets by name and role: one Main per network that a new wallet never takes, burners that pay once.

Each test drives the real stores (wallet profiles, roles, spend ledger) through the same functions the Settings doors
and the payment path call, and reads the facts back from those stores.
"""
from __future__ import annotations

import uuid

import pytest

from core.wallet.errors import WalletFault

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
BASE_MAINNET = "eip155:8453"
PIN = "482913"
OTHER_PIN = "905172"
STRANGER = "Vote111111111111111111111111111111111111111"


@pytest.fixture
def home(monkeypatch, tmp_path):
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


def _create(network: str = SOLANA_MAINNET, *, kind: str = "", label: str = "", pin: str = PIN) -> dict:
    from core.wallet import pilot_custody

    made = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=pin, credential_confirmation=pin,
                                             creation_key=f"create-{uuid.uuid4().hex}", label=label, kind=kind)
    revealed = pilot_custody.reveal_pilot_backup(made["wallet_id"], credential=pin)
    pilot_custody.acknowledge_pilot_backup(made["wallet_id"], ack_token=revealed["ack_token"])
    return made


def _entry(wallet_id: str) -> dict:
    from core.wallet import custody

    return next(e for e in custody.list_wallets() if e["wallet_id"] == wallet_id)


def _address(wallet_id: str) -> str:
    return _entry(wallet_id)["public_key"]


def _hold(wallet_id: str, destination: str, *, amount: int = 1_000, proposal: str = ""):
    from core.wallet import limits

    return limits.reserve_spend(wallet_id=wallet_id, asset="SOL", amount_minor=amount, destination=destination,
                         proposal_id=proposal or f"p-{uuid.uuid4().hex[:12]}", chain=SOLANA_MAINNET)


def test_the_first_wallet_is_main_and_a_wallet_added_later_never_takes_its_place(home):
    from core.wallet import custody, wallet_roles

    first = _create()
    assert _entry(first["wallet_id"])["kind"] == "main" and _entry(first["wallet_id"])["label"] == "Main"
    second = _create(label="Groceries")
    third = _create(BASE_MAINNET) if _evm_ok() else None
    # before roles existed the NEWEST wallet was the default: now the first stays Main and default
    assert custody.default_wallet().wallet_id == first["wallet_id"]
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == first["wallet_id"]
    assert _entry(second["wallet_id"])["kind"] == "everyday" and _entry(second["wallet_id"])["label"] == "Groceries"
    if third is not None:
        assert wallet_roles.main_wallet_id(BASE_MAINNET) == third["wallet_id"]  # its network's first wallet
        assert custody.default_wallet().wallet_id == first["wallet_id"]


def _evm_ok() -> bool:
    from core.wallet import custody

    return custody.evm_pocket_custody_available()


def test_an_upgraded_home_keeps_the_wallet_it_used_as_default(home):
    """A home from before roles: two wallets on one network, no role rows. The newest was the default; adding a
    third must not move it."""
    from core.wallet import custody, wallet_roles
    from core.wallet.store import connection

    older = _create()
    newer = _create()
    with connection() as conn:  # undo what this build wrote, so the home looks like one from before it
        conn.execute("DELETE FROM wallet_roles")
        conn.execute("DELETE FROM wallet_main")
        conn.execute("UPDATE wallet_profiles SET is_default = 0")
    assert custody.default_wallet().wallet_id == newer["wallet_id"]  # the old rule: the newest
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == newer["wallet_id"]  # a read writes nothing and agrees
    _create(label="Later")
    assert custody.default_wallet().wallet_id == newer["wallet_id"]
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == newer["wallet_id"]
    assert _entry(older["wallet_id"])["kind"] == "everyday"


def test_make_main_needs_that_wallets_own_credential_and_moves_the_default_with_it(home):
    from core.wallet import custody, wallet_roles

    first = _create()
    second = _create(label="Travel", pin=OTHER_PIN)
    for wrong in ("", PIN):  # the first wallet's PIN does not prove the second
        with pytest.raises(WalletFault):
            wallet_roles.make_main(second["wallet_id"], credential=wrong)
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == first["wallet_id"]
    moved = wallet_roles.make_main(second["wallet_id"], credential=OTHER_PIN)
    assert moved["previous"] == first["wallet_id"]
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == second["wallet_id"]
    assert custody.default_wallet().wallet_id == second["wallet_id"]
    assert _entry(first["wallet_id"])["kind"] == "everyday" and _entry(second["wallet_id"])["is_main"] is True


def test_rename_keeps_one_clean_line_and_refuses_an_empty_name(home):
    from core.wallet import wallet_roles

    made = _create()
    assert wallet_roles.rename(made["wallet_id"], "  My\nspending\twallet  ")["label"] == "My spending wallet"
    assert _entry(made["wallet_id"])["label"] == "My spending wallet"
    assert len(wallet_roles.rename(made["wallet_id"], "x" * 90)["label"]) == wallet_roles.NAME_MAX
    with pytest.raises(WalletFault) as refused:
        wallet_roles.rename(made["wallet_id"], " \n ")
    assert refused.value.context["reason"] == "name_required"
    with pytest.raises(WalletFault):
        wallet_roles.rename("wallet-does-not-exist", "Name")


def test_a_burner_is_never_main_never_the_default_and_never_picked_without_being_named(home):
    from core.wallet import custody, transfers, wallet_roles

    burner = _create(kind="burner")  # the first wallet on its network, still not Main
    assert _entry(burner["wallet_id"])["kind"] == "burner" and _entry(burner["wallet_id"])["label"] == "Burner"
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == ""
    assert custody.default_wallet() is None
    with pytest.raises(WalletFault):
        wallet_roles.make_main(burner["wallet_id"], credential=PIN)
    main = _create()
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == main["wallet_id"]
    assert custody.default_wallet().wallet_id == main["wallet_id"]
    resolved = transfers.resolve_request(destination=STRANGER, amount="0.001", asset="SOL")
    assert resolved["wallet_id"] == main["wallet_id"]
    named = transfers.resolve_request(destination=STRANGER, amount="0.001", asset="SOL", wallet_id=burner["wallet_id"])
    assert named["wallet_id"] == burner["wallet_id"]


def test_several_wallets_on_one_network_are_settled_by_its_main(home):
    from core.wallet import transfers, wallet_roles

    main = _create()
    other = _create(label="Other", pin=OTHER_PIN)
    assert transfers.resolve_request(destination=STRANGER, amount="0.001", asset="SOL")["wallet_id"] == main["wallet_id"]
    wallet_roles.make_main(other["wallet_id"], credential=OTHER_PIN)
    assert transfers.resolve_request(destination=STRANGER, amount="0.001", asset="SOL")["wallet_id"] == other["wallet_id"]


def test_a_burner_is_a_normal_wallet_after_it_paid(home):
    """Owner 2026-11-12: no lock. A burner that paid still pays anyone; the listing only says it was used."""
    from core.wallet import limits

    _create()
    burner = _create(kind="burner")
    assert _entry(burner["wallet_id"])["burner_used"] is False
    _hold(burner["wallet_id"], STRANGER)
    assert _entry(burner["wallet_id"])["burner_used"] is True
    assert limits.check_limits(burner["wallet_id"], "SOL", 1_000, STRANGER, chain=SOLANA_MAINNET).ok


def test_an_unknown_kind_is_refused_before_anything_is_created(home):
    from core.wallet import custody

    with pytest.raises(WalletFault) as refused:
        _create(kind="savings")
    assert refused.value.context["reason"] == "unknown_wallet_kind"
    assert custody.list_wallets() == []


def test_deleting_a_wallet_needs_its_pin_and_a_confirmed_backup_and_main_moves_on(home):
    from core.wallet import custody, wallet_roles
    from core.wallet.store import connection

    main = _create()
    spare = _create(label="Spare", pin=OTHER_PIN)
    with pytest.raises(WalletFault) as refused:
        wallet_roles.remove_wallet(main["wallet_id"], credential=PIN, backup_confirmed=False)
    assert refused.value.context["reason"] == "backup_not_confirmed"
    with pytest.raises(WalletFault):
        wallet_roles.remove_wallet(main["wallet_id"], credential=OTHER_PIN, backup_confirmed=True)
    assert {e["wallet_id"] for e in custody.list_wallets()} == {main["wallet_id"], spare["wallet_id"]}
    wallet_roles.remove_wallet(main["wallet_id"], credential=PIN, backup_confirmed=True)
    assert [e["wallet_id"] for e in custody.list_wallets()] == [spare["wallet_id"]]
    assert wallet_roles.main_wallet_id(SOLANA_MAINNET) == spare["wallet_id"]
    assert custody.default_wallet().wallet_id == spare["wallet_id"]
    with connection() as conn:  # the key is gone from this computer; the row stays for the payment history
        sealed, state = conn.execute("SELECT sealed_blob, setup_state FROM wallet_profiles WHERE wallet_id = ?", (main["wallet_id"],)).fetchone()
    assert sealed == "" and state == "removed"
    with pytest.raises(WalletFault):  # a deleted wallet cannot be renamed, made main or deleted again
        wallet_roles.rename(main["wallet_id"], "Back")


def test_a_wallet_with_a_payment_waiting_is_not_deleted(home, monkeypatch):
    from core.wallet import proposals, wallet_roles

    made = _create()
    waiting = type("P", (), {"wallet_id": made["wallet_id"]})()
    monkeypatch.setattr(proposals, "list_proposals", lambda **kw: [waiting] if kw.get("state") == proposals.STATE_PENDING_APPROVAL else [])
    with pytest.raises(WalletFault) as refused:
        wallet_roles.remove_wallet(made["wallet_id"], credential=PIN, backup_confirmed=True)
    assert refused.value.context["reason"] == "payment_waiting"
