"""An agent's own wallet: created only by the proven owner, opened only by this machine's credential, never an owner's."""
from __future__ import annotations

import json

import pytest

from core.wallet import agent_wallets, custody, pilot_custody
from core.wallet import allowances as alw
from core.wallet.security import WalletFault
from tests.wallet._rig import DESTINATION, DEVNET, wallet_env

PIN = "730461"
NOW = 1_800_000_000.0


@pytest.fixture
def owner(wallet_env):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _ready(agent: str = "researcher") -> dict:
    created = agent_wallets.create_agent_wallet(agent, network=DEVNET, credential=PIN)
    revealed = agent_wallets.reveal_backup(created["wallet_id"], credential=PIN)
    return agent_wallets.acknowledge_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _reason(excinfo) -> str:
    return str(excinfo.value.context.get("reason"))


def test_only_the_proven_owner_creates_an_agent_wallet(owner):
    for wrong in ("", "000000"):
        with pytest.raises(WalletFault):
            agent_wallets.create_agent_wallet("researcher", network=DEVNET, credential=wrong)
    assert agent_wallets.list_agent_wallets() == []
    created = agent_wallets.create_agent_wallet("researcher", network=DEVNET, credential=PIN)
    assert created["agent_id"] == "researcher" and created["ready"] is False and created["return_address"] == owner.public_key
    with pytest.raises(WalletFault) as excinfo:
        agent_wallets.create_agent_wallet("researcher", network=DEVNET, credential=PIN)
    assert _reason(excinfo) == "agent_wallet_exists"
    with pytest.raises(WalletFault) as excinfo:
        agent_wallets.create_agent_wallet("Not An Agent", network=DEVNET, credential=PIN)
    assert _reason(excinfo) == "unknown_agent"


def test_with_no_owner_wallet_there_is_nobody_to_prove_and_nothing_is_created(wallet_env):
    with pytest.raises(WalletFault) as excinfo:
        agent_wallets.create_agent_wallet("researcher", network=DEVNET, credential=PIN)
    assert _reason(excinfo) == "no_owner_wallet_to_prove"
    assert agent_wallets.list_agent_wallets() == []


def test_the_backup_is_shown_once_to_the_owner_and_the_wallet_cannot_sign_before_it_is_confirmed(owner):
    created = agent_wallets.create_agent_wallet("researcher", network=DEVNET, credential=PIN)
    with pytest.raises(WalletFault) as excinfo:
        agent_wallets.signing_credential(created["wallet_id"])
    assert _reason(excinfo) == "agent_wallet_not_ready"
    with pytest.raises(WalletFault):
        agent_wallets.reveal_backup(created["wallet_id"], credential="111111")
    revealed = agent_wallets.reveal_backup(created["wallet_id"], credential=PIN)
    with pytest.raises(WalletFault):
        agent_wallets.reveal_backup(created["wallet_id"], credential=PIN)
    with pytest.raises(WalletFault):
        agent_wallets.acknowledge_backup(created["wallet_id"], ack_token="")
    assert agent_wallets.acknowledge_backup(created["wallet_id"], ack_token=revealed["ack_token"])["ready"] is True


def test_the_machine_credential_opens_exactly_this_wallet_and_is_shown_nowhere(owner):
    first, second = _ready("researcher"), _ready("writer")
    credential = agent_wallets.signing_credential(first["wallet_id"])
    assert credential != agent_wallets.signing_credential(second["wallet_id"])
    with pilot_custody.signing_session(first["wallet_id"], credential) as signer:
        assert signer.public_key == first["address"]
    with pytest.raises(WalletFault):
        with pilot_custody.signing_session(second["wallet_id"], credential):
            pass
    from core.wallet.store import connection

    with connection() as conn:
        stored = json.dumps([list(row) for row in conn.execute("SELECT * FROM wallet_profiles").fetchall()], default=str)
    shown = json.dumps([agent_wallets.list_agent_wallets(), custody.list_wallets()], default=str)
    assert credential not in stored and credential not in shown
    with pytest.raises(WalletFault) as excinfo:
        agent_wallets.signing_credential(owner.wallet_id)
    assert _reason(excinfo) == "not_an_agent_wallet"


def test_an_agent_wallet_never_proves_the_owner(owner):
    wallet = _ready()
    machine = agent_wallets.signing_credential(wallet["wallet_id"])
    assert custody.owner_proof_wallet().wallet_id == owner.wallet_id
    with pytest.raises(WalletFault):
        custody.prove_owner(wallet["wallet_id"], credential=machine, purpose="raise a cap")
    with pytest.raises(WalletFault):
        custody.prove_owner(wallet["wallet_id"], credential="", purpose="raise a cap")
    custody.prove_owner(wallet["wallet_id"], credential=PIN, purpose="raise a cap")


def test_an_agent_wallets_credential_is_neither_changed_nor_recovered(owner):
    wallet = _ready()
    machine = agent_wallets.signing_credential(wallet["wallet_id"])
    with pytest.raises(WalletFault) as excinfo:
        pilot_custody.change_pilot_credential(wallet["wallet_id"], current_credential=machine, method="pin", credential="123123", credential_confirmation="123123")
    assert _reason(excinfo) == "agent_wallet_credential_is_fixed"
    with pytest.raises(WalletFault) as excinfo:
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value="x", method="pin", credential="123123", credential_confirmation="123123")
    assert _reason(excinfo) == "agent_wallet_credential_is_fixed"
    with pytest.raises(WalletFault) as excinfo:
        pilot_custody.recover_with_device(wallet["wallet_id"], challenge="x", method="pin", credential="123123", credential_confirmation="123123")
    assert _reason(excinfo) == "agent_wallet_credential_is_fixed"
    with pilot_custody.signing_session(wallet["wallet_id"], machine) as signer:
        assert signer.public_key == wallet["address"]


def _spec(wallet_id: str, agent: str = "researcher"):
    return alw.AllowanceSpec(agent_id=agent, wallet_id=wallet_id, network=DEVNET, asset="SOL", per_payment_minor=1_000, window_seconds=alw.DAY, window_minor=3_000,
                             total_minor=5_000, max_payments=10, min_interval_seconds=0, expires_at=NOW + alw.DAY, services=(alw.ServiceRule("https://api.example.test", 4_000, DESTINATION),))


def test_an_allowance_is_carried_only_by_that_agents_own_wallet(owner):
    wallet = _ready("researcher")
    for wallet_id, agent in ((owner.wallet_id, "researcher"), (wallet["wallet_id"], "writer")):
        with pytest.raises(WalletFault) as excinfo:
            alw.create_allowance(_spec(wallet_id, agent), credential=PIN, now=NOW)
        assert _reason(excinfo) == "allowance_needs_the_agents_own_wallet"
    machine = agent_wallets.signing_credential(wallet["wallet_id"])
    with pytest.raises(WalletFault):
        alw.create_allowance(_spec(wallet["wallet_id"]), credential=machine, now=NOW)  # the machine's credential is not the owner's
    assert alw.list_allowances() == []
    assert alw.create_allowance(_spec(wallet["wallet_id"]), credential=PIN, now=NOW)
