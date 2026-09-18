"""Product follow-up, Phase B: a forgotten PIN or password is recovered with the backup VOOL issued, or with an
enrolled device secret, without destroying the wallet, its history or its unresolved payments.

IN-PROCESS against the custody authority (a loopback scripted Solana node where a transfer is involved). The security
decision under test: only the wallet's own key material (the one-time backup, decoded and derived twice) or an enrolled,
freshly released device secret proves control; a wrong, foreign, malformed or tampered backup writes nothing and is
counted on the recovery throttle only; no countdown, label, timer or support path resets anything. Recovery re-seals
the SAME key under the new credential in one fenced write: the old credential fails afterwards, a stale handle cannot
commit, open previews are withdrawn, unresolved transfers stay, and signing opened under an older credential
generation is revoked before the transmit site.
"""
from __future__ import annotations

import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.wallet import custody, device_auth, pilot_custody, proposals, quotes, transfers
from core.wallet.errors import WalletFault
from core.wallet.store import connection
from tests.wallet._device_auth_fake import FakeDeviceAuthority
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
BASE_SEPOLIA = "eip155:84532"
PIN, NEW_PIN, WRONG_PIN = "482913", "775310", "111111"
PASSWORD, NEW_PASSWORD = "correct horse battery", "another strong phrase"


@pytest.fixture
def wallet_home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment

    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    environment.set_active_environment("testnet")
    yield tmp_path
    chains.invalidate_chain_identity()
    store_module.reset_default_store()


@pytest.fixture
def fake_device():
    authority = FakeDeviceAuthority()
    device_auth.set_device_authority_for_tests(authority)
    try:
        yield authority
    finally:
        device_auth.set_device_authority_for_tests(None)


def _sol_key() -> str:
    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _create(network: str, *, method: str = "pin", credential: str = PIN, label: str = "") -> tuple[dict, str]:
    """A ready pilot wallet and the backup VOOL showed once (the disposable fixture key of this test)."""
    view = pilot_custody.create_pilot_wallet(network=network, method=method, credential=credential, credential_confirmation=credential, creation_key=f"rec-{uuid.uuid4().hex}", label=label)
    revealed = pilot_custody.reveal_pilot_backup(view["wallet_id"], credential=credential)
    pilot_custody.acknowledge_pilot_backup(view["wallet_id"], ack_token=revealed["ack_token"])
    return pilot_custody.setup_view(view["wallet_id"]), revealed["backup_value"]


def _blobs(wallet_id: str) -> tuple[str, str, int]:
    with connection() as conn:
        row = conn.execute("SELECT sealed_blob, device_sealed_blob, credential_generation FROM wallet_profiles WHERE wallet_id = ?", (wallet_id,)).fetchone()
    return str(row[0]), str(row[1]), int(row[2])


def _attempts() -> dict[str, tuple[int, float]]:
    with connection() as conn:
        return {str(r[0]): (int(r[1]), float(r[2])) for r in conn.execute("SELECT scope, failures, locked_until FROM wallet_unlock_attempts").fetchall()}


# --- the mandatory backup path -------------------------------------------------------------------------------------

def test_original_solana_backup_restores_access_without_touching_the_wallet(wallet_home) -> None:
    wallet, backup = _create(SOLANA_DEVNET, label="forgotten pin")
    blob_before, _device, gen_before = _blobs(wallet["wallet_id"])
    assert gen_before == 1 and wallet["credential_generation"] == 1
    # the PIN is forgotten; a wrong one is refused as before
    with pytest.raises(WalletFault) as refused:
        pilot_custody.prove_credential(wallet["wallet_id"], WRONG_PIN)
    assert refused.value.code == "wallet_pin_invalid"
    view = pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert view["recovered"] is True and view["wallet_id"] == wallet["wallet_id"] and view["address"] == wallet["address"]
    assert view["credential_generation"] == 2 and view["setup_state"] == "ready" and view["method"] == "pin"
    blob_after, device_after, gen_after = _blobs(wallet["wallet_id"])
    assert blob_after != blob_before and device_after == "" and gen_after == 2
    # the new credential works, the old one does not; the seal is a fresh envelope over the SAME key (same address)
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)
    with pytest.raises(WalletFault) as old:
        pilot_custody.prove_credential(wallet["wallet_id"], PIN)
    assert old.value.code == "wallet_pin_invalid"
    with pilot_custody.signing_session(wallet["wallet_id"], NEW_PIN) as signer:
        assert signer.public_key == wallet["address"] and signer.generation == 2
        signer.sign_svm(b"the same key signs after recovery")
    # "after restart": nothing lives in memory -- a fresh read of the record opens with the new credential
    assert pilot_custody.credential_generation(wallet["wallet_id"]) == 2
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)
    # the backup value never reaches a fault or the journal; the exact-value scrubber knows it
    from core.secret_redaction import redact_secrets

    assert backup not in redact_secrets(f"note {backup} note")


def test_novel_evm_password_wallet_recovers_with_its_hex_backup(wallet_home) -> None:
    wallet, backup = _create(BASE_SEPOLIA, method="password", credential=PASSWORD, label="forgotten password")
    assert backup.startswith("0x") and len(backup) == 66
    view = pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert view["address"] == wallet["address"] and view["method"] == "pin" and view["credential_generation"] == 2
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)
    with pytest.raises(WalletFault) as old:
        pilot_custody.prove_credential(wallet["wallet_id"], PASSWORD)
    assert old.value.code == "wallet_pin_invalid"  # the wallet is a PIN wallet now: the old password is not a PIN
    # a checksum-cased hex backup is the same key
    view2 = pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup.upper().replace("0X", "0x"), method="password", credential=NEW_PASSWORD, credential_confirmation=NEW_PASSWORD)
    assert view2["credential_generation"] == 3 and view2["method"] == "password"
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PASSWORD)


@pytest.mark.parametrize(
    ("network", "make_bad", "reason"),
    [
        (SOLANA_DEVNET, lambda good, other: other, "backup_for_another_wallet"),
        (SOLANA_DEVNET, lambda good, other: good[:-3] + ("111" if good[-3:] != "111" else "222"), "backup_keypair_halves_disagree"),
        (SOLANA_DEVNET, lambda good, other: "0x" + "ab" * 32, "backup_wrong_format_expected_solana_keypair_base58"),
        (SOLANA_DEVNET, lambda good, other: "not a key at all", "backup_wrong_format"),
        (BASE_SEPOLIA, lambda good, other: other, "backup_for_another_wallet"),
        (BASE_SEPOLIA, lambda good, other: good[:-2] + ("00" if good[-2:] != "00" else "11"), "backup_for_another_wallet"),
        (BASE_SEPOLIA, lambda good, other: "0x" + "ff" * 32, "backup_key_outside_curve_order"),
        (BASE_SEPOLIA, lambda good, other: good[:-4], "backup_malformed_expected_32_byte_hex"),
        (SOLANA_DEVNET, lambda good, other: "", "backup_missing"),
    ],
)
def test_wrong_foreign_malformed_or_tampered_backups_write_nothing(wallet_home, network: str, make_bad, reason: str) -> None:
    wallet, good = _create(network)
    _other, other = _create(network)
    blob_before, _d, gen_before = _blobs(wallet["wallet_id"])
    with pytest.raises(WalletFault) as refused:
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=make_bad(good, other), method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert refused.value.code == "wallet_recovery_refused" and refused.value.context["reason"].startswith(reason.split("_expected")[0]), refused.value.context
    assert good not in json.dumps(refused.value.context) and other not in json.dumps(refused.value.context)
    assert _blobs(wallet["wallet_id"]) == (blob_before, _d, gen_before)
    pilot_custody.prove_credential(wallet["wallet_id"], PIN)  # the original credential still opens the untouched seal
    with pytest.raises(WalletFault):
        pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)


def test_options_read_changes_nothing_and_states_what_is_offered(wallet_home) -> None:
    wallet, _backup = _create(SOLANA_DEVNET, label="reader")
    before = _blobs(wallet["wallet_id"])
    options = pilot_custody.recovery_options(wallet["wallet_id"])
    assert options["recoverable"] is True and options["address"] == wallet["address"] and options["backup"]["format"] == "solana_keypair_base58"
    assert options["device_recovery"] == {"available": False, "enrolled": False, "reason": "not_enrolled", "challenge": "", "note": options["device_recovery"]["note"]}
    assert "Phantom" in options["external_import"]["target"] and options["external_import"]["references"][0].startswith("https://help.phantom.com/")
    assert "cannot reset access" in options["no_reset"]["text"] and "no funds move" in options["no_reset"]["text"]
    assert _blobs(wallet["wallet_id"]) == before
    assert _attempts() == {} or all(v[0] == 0 for v in _attempts().values())


def test_new_credential_shape_and_confirmation_are_checked_before_any_attempt_is_counted(wallet_home) -> None:
    wallet, backup = _create(SOLANA_DEVNET)
    with pytest.raises(WalletFault) as mismatch:
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation="000000")
    assert mismatch.value.code == "wallet_credential_mismatch"
    with pytest.raises(WalletFault) as shape:
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential="12", credential_confirmation="12")
    assert shape.value.code == "wallet_pin_invalid"
    assert not any(scope.startswith("recovery:") for scope in _attempts())
    assert _blobs(wallet["wallet_id"])[2] == 1


# --- throttles: recovery has its own; a PIN cooldown does not block it; success clears the wallet's unlock count -------

def test_recovery_stays_reachable_during_a_pin_cooldown_and_clears_it_on_success(wallet_home) -> None:
    wallet, backup = _create(SOLANA_DEVNET)
    for _ in range(pilot_custody.WALLET_LOCK_AFTER):
        with pytest.raises(WalletFault):
            pilot_custody.prove_credential(wallet["wallet_id"], WRONG_PIN)
    with pytest.raises(WalletFault) as locked:
        pilot_custody.prove_credential(wallet["wallet_id"], PIN)
    assert locked.value.code == "wallet_unlock_throttled" and int(locked.value.context["retry_after_seconds"]) >= 1
    assert pilot_custody.recovery_options(wallet["wallet_id"])["unlock_retry_after_seconds"] >= 1
    view = pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert view["credential_generation"] == 2
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)  # the wallet's own lock cleared by the valid proof, nothing else
    assert _attempts()[f"wallet:{wallet['wallet_id']}"] == (0, 0.0)


def test_wrong_backups_count_on_the_recovery_throttle_only(wallet_home) -> None:
    wallet, _backup = _create(SOLANA_DEVNET)
    bystander, _b = _create(SOLANA_DEVNET)
    for _ in range(pilot_custody.WALLET_LOCK_AFTER):
        with pytest.raises(WalletFault) as refused:
            pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value="not a key", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
        assert refused.value.code == "wallet_recovery_refused"
    with pytest.raises(WalletFault) as throttled:
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value="not a key", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert throttled.value.code == "wallet_unlock_throttled" and throttled.value.context["reason"] == "too_many_failed_recovery_attempts"
    assert pilot_custody.recovery_options(wallet["wallet_id"])["backup"]["retry_after_seconds"] >= 1
    attempts = _attempts()
    assert attempts[f"recovery:{wallet['wallet_id']}"][0] == pilot_custody.WALLET_LOCK_AFTER and attempts[f"recovery:{wallet['wallet_id']}"][1] > 0  # the locked attempt is not counted
    assert f"wallet:{wallet['wallet_id']}" not in attempts or attempts[f"wallet:{wallet['wallet_id']}"] == (0, 0.0)
    pilot_custody.prove_credential(wallet["wallet_id"], PIN)  # the PIN path is untouched by recovery failures
    pilot_custody.prove_credential(bystander["wallet_id"], PIN)  # and so is another wallet
    assert f"recovery:{bystander['wallet_id']}" not in attempts


# --- device recovery: only a real enrolled secret, freshly released, bound to this wallet and generation --------------

def test_no_enrolled_recovery_cannot_masquerade_as_device_recovery(wallet_home, fake_device) -> None:
    wallet, _backup = _create(SOLANA_DEVNET)  # a PIN wallet: no device-protected copy exists
    assert fake_device.available() and pilot_custody.recovery_options(wallet["wallet_id"])["device_recovery"]["available"] is False
    with pytest.raises(WalletFault) as refused:
        pilot_custody.recover_with_device(wallet["wallet_id"], challenge="anything", method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert refused.value.code == "wallet_device_auth_unavailable" and refused.value.context["reason"] == "device_recovery_not_enrolled"
    assert fake_device.prompts == [] and _blobs(wallet["wallet_id"])[2] == 1
    pilot_custody.prove_credential(wallet["wallet_id"], PIN)


def test_device_recovery_denial_stale_challenge_and_success(wallet_home, fake_device) -> None:
    wallet, _backup = _create(SOLANA_DEVNET, method="device", credential="")
    assert _blobs(wallet["wallet_id"])[1] and wallet["method"] == "device"
    prompts_before = len(fake_device.prompts)  # the one-time reveal already prompted once
    options = pilot_custody.recovery_options(wallet["wallet_id"])
    assert options["device_recovery"]["available"] is True and options["device_recovery"]["challenge"] == f"recover:{wallet['wallet_id']}:1"
    # a denied or cancelled prompt writes nothing and counts once on the recovery throttle
    fake_device.deny_next = True
    with pytest.raises(WalletFault) as denied:
        pilot_custody.recover_with_device(wallet["wallet_id"], challenge=options["device_recovery"]["challenge"], method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert denied.value.code == "wallet_device_auth_denied" and _blobs(wallet["wallet_id"])[2] == 1
    assert len(fake_device.prompts) == prompts_before + 1 and "generation 1" in fake_device.prompts[-1] and _attempts()[f"recovery:{wallet['wallet_id']}"][0] == 1
    # a fresh release of the wallet-bound secret re-seals the same key -- here under a NEW device enrolment, so the
    # wallet stays device-protected and the handle from before the reset can be shown to be stale
    view = pilot_custody.recover_with_device(wallet["wallet_id"], challenge=options["device_recovery"]["challenge"], method="device")
    assert view["credential_generation"] == 2 and view["method"] == "device" and len(fake_device.prompts) == prompts_before + 2
    assert _blobs(wallet["wallet_id"])[1] and pilot_custody.recovery_options(wallet["wallet_id"])["device_recovery"]["challenge"] == f"recover:{wallet['wallet_id']}:2"
    with pytest.raises(WalletFault) as stale:
        pilot_custody.recover_with_device(wallet["wallet_id"], challenge=options["device_recovery"]["challenge"], method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert stale.value.code == "wallet_recovery_refused" and stale.value.context["reason"] == "stale_or_foreign_recovery_challenge"
    assert len(fake_device.prompts) == prompts_before + 2  # refused before any prompt
    # the current handle, a new PIN: the device copy is gone afterwards and the device secret is forgotten
    current = pilot_custody.recovery_options(wallet["wallet_id"])["device_recovery"]["challenge"]
    view = pilot_custody.recover_with_device(wallet["wallet_id"], challenge=current, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert view["credential_generation"] == 3 and view["method"] == "pin"
    sealed, device_blob, gen = _blobs(wallet["wallet_id"])
    assert sealed and device_blob == "" and gen == 3 and wallet["wallet_id"] not in fake_device.secrets
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)
    assert pilot_custody.recovery_options(wallet["wallet_id"])["device_recovery"] ["available"] is False  # not enrolled any more


# --- fencing: interrupted commit, competing resets, old authorizations, unresolved transfers -------------------------

def test_an_interrupted_commit_keeps_the_previous_envelope_usable(wallet_home, monkeypatch) -> None:
    wallet, backup = _create(SOLANA_DEVNET)
    before = _blobs(wallet["wallet_id"])

    def explode(conn):
        raise RuntimeError("disk gone")

    original = pilot_custody.limits._begin_immediate
    monkeypatch.setattr(pilot_custody.limits, "_begin_immediate", explode)
    with pytest.raises(RuntimeError):
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    monkeypatch.setattr(pilot_custody.limits, "_begin_immediate", original)
    assert _blobs(wallet["wallet_id"]) == before
    pilot_custody.prove_credential(wallet["wallet_id"], PIN)
    with pytest.raises(WalletFault):
        pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)
    # the same proof retried lands cleanly afterwards
    assert pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)["credential_generation"] == 2


def test_two_competing_resets_only_one_commits_and_the_loser_never_regains_control(wallet_home, monkeypatch) -> None:
    wallet, backup = _create(SOLANA_DEVNET)
    stale_row = pilot_custody._require_recoverable_row(wallet["wallet_id"], source_context=None)
    first = pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
    assert first["credential_generation"] == 2
    # the second reset read the wallet before the first committed: its fenced write finds another generation
    original = pilot_custody._require_recoverable_row
    monkeypatch.setattr(pilot_custody, "_require_recoverable_row", lambda wallet_id, *, source_context: dict(stale_row))
    with pytest.raises(WalletFault) as lost:
        pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential="909090", credential_confirmation="909090")
    assert lost.value.code == "wallet_setup_state_invalid" and lost.value.context["reason"] == "recovery_conflict_generation_changed"
    monkeypatch.setattr(pilot_custody, "_require_recoverable_row", original)
    assert _blobs(wallet["wallet_id"])[2] == 2
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)
    for old in (PIN, "909090"):
        with pytest.raises(WalletFault):
            pilot_custody.prove_credential(wallet["wallet_id"], old)


def test_reset_withdraws_open_previews_keeps_the_unresolved_transfer_and_sends_once_after_a_fresh_review(wallet_home, monkeypatch) -> None:
    from core.wallet import approval, chains, lifecycle

    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as node:
        node.real_signature = True
        node.status_keyed = True
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_DEVNET: node.url}))
        chains.invalidate_chain_identity()
        wallet, backup = _create(SOLANA_DEVNET)
        proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=400_000, asset="SOL", origin=proposals.ORIGIN_USER, network=SOLANA_DEVNET)
        engine = lifecycle.default_lifecycle()
        engine.prepare(proposal.proposal_id)
        old_quote = quotes.mint_quote(proposal.proposal_id)
        assert quotes.open_quote_for(proposal.proposal_id)["quote_id"] == old_quote["quote_id"]
        view = pilot_custody.recover_with_backup(wallet["wallet_id"], backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
        assert view["previews_withdrawn"] == 1 and quotes.open_quote_for(proposal.proposal_id) is None
        # the old approval authorization is dead: the quote it named is superseded, the old PIN is no credential
        with pytest.raises(WalletFault) as gone:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=old_quote["quote_id"], quote_digest=old_quote["digest"], approver=approval.PinApprover(PIN))
        assert gone.value.code == "wallet_quote_expired" and node.send_count() == 0
        assert proposals.get_proposal(proposal.proposal_id).state == "pending_approval"  # unresolved, retained, not re-sent
        # a fresh review with the new credential sends exactly once
        fresh = quotes.mint_quote(proposal.proposal_id)
        with pytest.raises(WalletFault) as wrong:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=fresh["quote_id"], quote_digest=fresh["digest"], approver=approval.PinApprover(PIN))
        assert wrong.value.code == "wallet_pin_invalid" and node.send_count() == 0
        fresh = quotes.mint_quote(proposal.proposal_id)
        result = engine.approve_pilot_transfer(proposal.proposal_id, quote_id=fresh["quote_id"], quote_digest=fresh["digest"], approver=approval.PinApprover(NEW_PIN))
        assert result["transfer"]["state"] == "confirmed" and node.send_count() == 1


def test_a_reset_during_signing_revokes_the_bytes_before_the_transmit_site(wallet_home, monkeypatch) -> None:
    from core.wallet import approval, chains, lifecycle

    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as node:
        node.real_signature = True
        node.status_keyed = True
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_DEVNET: node.url}))
        chains.invalidate_chain_identity()
        wallet, backup = _create(SOLANA_DEVNET)
        proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=400_000, asset="SOL", origin=proposals.ORIGIN_USER, network=SOLANA_DEVNET)
        engine = lifecycle.default_lifecycle()
        engine.prepare(proposal.proposal_id)
        quote = quotes.mint_quote(proposal.proposal_id)
        real_session = pilot_custody.signing_session

        class _RotatingSigner:
            """The key opened under generation 1; the recovery commits inside the signing step, after the claim."""

            def __init__(self, signer, wallet_id):
                self.signer, self.wallet_id = signer, wallet_id
                self.public_key, self.family, self.generation = signer.public_key, signer.family, signer.generation

            def sign_svm(self, message):
                pilot_custody.recover_with_backup(self.wallet_id, backup_value=backup, method="pin", credential=NEW_PIN, credential_confirmation=NEW_PIN)
                return self.signer.sign_svm(message)

            def close(self):
                self.signer.close()

        class _Session:
            def __init__(self, wallet_id, credential, *, source_context=None):
                self.inner = real_session(wallet_id, credential, source_context=source_context)
                self.wallet_id = wallet_id

            def __enter__(self):
                return _RotatingSigner(self.inner.__enter__(), self.wallet_id)

            def __exit__(self, *exc):
                return self.inner.__exit__(*exc)

        monkeypatch.setattr(pilot_custody, "signing_session", _Session)
        with pytest.raises(WalletFault) as revoked:
            engine.approve_pilot_transfer(proposal.proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
        assert revoked.value.code == "wallet_signing_unavailable" and revoked.value.context["reason"] == "credential_rotated_before_send"
        assert node.send_count() == 0
        row = transfers.get_transfer_by_id(proposal.proposal_id)
        assert row is not None and row["state"] == transfers.STATE_SIGNED_REVOKED  # retained, reconcilable, never sent
        monkeypatch.setattr(pilot_custody, "signing_session", real_session)
        pilot_custody.prove_credential(wallet["wallet_id"], NEW_PIN)


# --- the remembered-credential change is a separate authenticated path ---------------------------------------------

def test_changing_a_remembered_pin_needs_the_current_one_and_bumps_the_generation(wallet_home) -> None:
    wallet, _backup = _create(SOLANA_DEVNET)
    with pytest.raises(WalletFault) as wrong:
        custody.change_approval_method(wallet["wallet_id"], new_method="password", new_secret=NEW_PASSWORD, current_secret=WRONG_PIN)
    assert wrong.value.code == "wallet_pin_invalid" and _attempts()[f"wallet:{wallet['wallet_id']}"][0] == 1 and _blobs(wallet["wallet_id"])[2] == 1
    profile = custody.change_approval_method(wallet["wallet_id"], new_method="password", new_secret=NEW_PASSWORD, current_secret=PIN)
    assert profile.approval_method == "password" and _blobs(wallet["wallet_id"])[2] == 2
    pilot_custody.prove_credential(wallet["wallet_id"], NEW_PASSWORD)
    with pytest.raises(WalletFault):
        pilot_custody.prove_credential(wallet["wallet_id"], PIN)
