"""Import an existing backed-up Solana wallet without replacing custody or signing."""
import json

import pytest
from solders.keypair import Keypair

from core.wallet import pilot_custody as pilot
from core.wallet.errors import WalletFault
from tests.wallet.test_crypto_pilot_custody import PIN, SOLANA_MAINNET, _rows, pilot_home


def adopt(key, **overrides):
    fields = dict(network=SOLANA_MAINNET, backup_value=str(key), expected_address=str(key.pubkey()),
                  method="pin", credential=PIN, credential_confirmation=PIN,
                  creation_key="import-one", backup_saved=True)
    fields.update(overrides)
    return pilot.import_solana_pilot_wallet(**fields)


def test_import_keeps_address_and_normal_signing_credential(pilot_home):
    key = Keypair()
    view = adopt(key)
    assert view["address"] == str(key.pubkey()) and view["setup_state"] == "ready"
    assert str(key) not in json.dumps(_rows())
    with pilot.signing_session(view["wallet_id"], PIN) as signer:
        assert signer.sign_svm(b"import proof") == bytes(key.sign_message(b"import proof"))
    with pytest.raises(WalletFault):
        pilot.prove_credential(view["wallet_id"], "192834")
    with pytest.raises(WalletFault):
        pilot.reveal_pilot_backup(view["wallet_id"], credential=PIN)


def test_import_replay_never_replaces_the_seal_or_adds_a_second_profile(pilot_home):
    key = Keypair()
    first = adopt(key)
    sealed = _rows()[0]["sealed_blob"]
    assert adopt(key)["wallet_id"] == first["wallet_id"]
    for overrides in ({"creation_key": "different"}, {"credential": "192834", "credential_confirmation": "192834"}):
        with pytest.raises(WalletFault):
            adopt(key, **overrides)
    with pytest.raises(WalletFault):
        adopt(Keypair())
    assert len(_rows()) == 1 and _rows()[0]["sealed_blob"] == sealed


@pytest.mark.parametrize("case", ["wrong_address", "corrupt_public", "missing_ack", "short_pin", "ephemeral"])
def test_import_refuses_before_any_profile(pilot_home, monkeypatch, case):
    from core.vool_wallet import b58encode
    key = Keypair()
    overrides = {}
    if case == "wrong_address":
        overrides["expected_address"] = str(Keypair().pubkey())
    elif case == "corrupt_public":
        overrides["backup_value"] = b58encode(bytes(key)[:32] + bytes(Keypair().pubkey()))
    elif case == "missing_ack":
        overrides["backup_saved"] = False
    elif case == "short_pin":
        overrides.update(credential="123", credential_confirmation="123")
    else:
        monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "ephemeral")
    with pytest.raises(WalletFault):
        adopt(key, **overrides)
    assert _rows() == []
