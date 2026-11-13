"""A Ledger as the hand that signs a Solana wallet: the USB framing, the Solana app's APDUs, and every way it says no."""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.vool_wallet import b58encode
from core.wallet import ledger
from tests.wallet._soft_ledger import SoftLedger

pytestmark = [pytest.mark.safety]


@pytest.fixture
def plugged():
    state: dict[str, list] = {"ledgers": []}
    ledger.set_transport_source_for_tests(lambda: list(state["ledgers"]))
    yield state
    ledger.set_transport_source_for_tests(None)


def test_framing_round_trips_long_answers():
    for size in (0, 1, 57, 58, 59, 120, 300, 1300):
        apdu = bytes(range(256)) * 6
        apdu = apdu[:size]
        packets = ledger.frame(apdu)
        assert all(len(p) == ledger.PACKET for p in packets)
        assert ledger.unframe(iter(packets).__next__) == apdu


def test_a_packet_out_of_order_is_refused():
    packets = ledger.frame(bytes(200))
    packets[1], packets[2] = packets[2], packets[1]
    with pytest.raises(ledger.LedgerProblem):
        ledger.unframe(iter(packets).__next__)


def test_the_address_comes_from_the_ledger_at_the_solana_path(plugged):
    soft = SoftLedger("A")
    plugged["ledgers"] = [soft]
    path = ledger.solana_path(0)
    assert ledger.path_text(path) == "m/44'/501'/0'/0'"
    assert ledger.public_key(path) == b58encode(soft.public_bytes(path))
    assert ledger.public_key(ledger.solana_path(1)) != ledger.public_key(path)
    assert soft.closed == 3  # each of the three asks closes the device after itself


def test_a_long_message_is_signed_in_chunks_and_the_signature_checks(plugged):
    soft = SoftLedger("A")
    plugged["ledgers"] = [soft]
    path = ledger.solana_path(0)
    address = ledger.public_key(path)
    message = bytes(range(256)) * 3  # 768 bytes: four APDUs
    signature = ledger.sign_solana_message(path, message, expected_public_key=address)
    assert soft.shown == [message]
    from core.vool_wallet import decode_solana_pubkey

    Ed25519PublicKey.from_public_bytes(decode_solana_pubkey(address)).verify(signature, message)


@pytest.mark.parametrize(("make", "reason"), [
    (lambda: [], "no_ledger"),
    (lambda: [SoftLedger("A"), SoftLedger("B")], "more_than_one_ledger"),
    (lambda: [SoftLedger("A", locked=True)], "ledger_locked"),
    (lambda: [SoftLedger("A", app_open=False)], "open_solana_app"),
    (lambda: [SoftLedger("A", approve=False)], "refused_on_ledger"),
    (lambda: [SoftLedger("A", approve=None)], "ledger_timeout"),
    (lambda: [SoftLedger("B")], "not_this_ledger"),
])
def test_every_no_from_the_ledger_is_a_plain_reason(plugged, make, reason):
    plugged["ledgers"] = [SoftLedger("A")]
    path = ledger.solana_path(0)
    address = ledger.public_key(path)
    plugged["ledgers"] = make()
    with pytest.raises(ledger.LedgerProblem) as caught:
        ledger.sign_solana_message(path, b"transfer", expected_public_key=address)
    assert caught.value.reason == reason


def test_a_message_too_long_for_a_solana_transaction_never_reaches_the_ledger(plugged):
    soft = SoftLedger("A")
    plugged["ledgers"] = [soft]
    path = ledger.solana_path(0)
    with pytest.raises(ledger.LedgerProblem):
        ledger.sign_solana_message(path, bytes(ledger.MAX_MESSAGE_BYTES + 1), expected_public_key=ledger.public_key(path))
    assert soft.shown == []


# --- a Ledger wallet in VOOL: the same quote, caps, approval and receipts; the Ledger signs -------------------------

from tests.wallet.test_crypto_pilot_dispatch import (
    PIN,
    SOLANA_DEVNET,
    WRONG_PIN,
    _approve,
    _hold,
    _pending,
    _quote,
    node,
    pilot,
)


@pytest.fixture
def on_ledger(pilot, plugged):
    plugged["ledgers"] = [SoftLedger("A")]
    yield plugged


def _add(account: int = 0, pin: str = PIN) -> dict:
    import uuid

    from core.wallet import pilot_custody

    return pilot_custody.add_ledger_wallet(network=SOLANA_DEVNET, account=account, method="pin", credential=pin, credential_confirmation=pin,
                                           creation_key=f"l-{uuid.uuid4().hex}", label="My Ledger")


def _refusal(call) -> tuple[str, str]:
    from core.wallet.errors import WalletFault

    with pytest.raises(WalletFault) as refused:
        call()
    return refused.value.code, str(refused.value.context.get("reason") or "")


def test_adding_a_ledger_keeps_only_its_address_and_path_and_it_can_be_main(on_ledger):
    from core.wallet import pilot_custody, wallet_roles
    from core.wallet.store import connection

    soft = on_ledger["ledgers"][0]
    made = _add()
    assert made["address"] == b58encode(soft.public_bytes(ledger.solana_path(0)))
    assert pilot_custody.ledger_of(made["wallet_id"])["path"] == "m/44'/501'/0'/0'"
    assert made["ledger"] == {"path": "m/44'/501'/0'/0'"} and made["setup_state"] == "ready"
    with connection() as conn:
        listed = wallet_roles.describe({"wallet_id": made["wallet_id"]}, conn=conn)
    assert listed["ledger"]["path"] == "m/44'/501'/0'/0'"
    wallet_roles.make_main(made["wallet_id"], credential=PIN)
    assert wallet_roles.main_wallet_id(SOLANA_DEVNET) == made["wallet_id"]


def test_a_send_from_a_ledger_wallet_is_pressed_on_the_ledger_and_sent_once(on_ledger):
    from core.wallet import proposals

    soft = on_ledger["ledgers"][0]
    made = _add()
    engine, proposal = _pending(made)
    quote = _quote(proposal.proposal_id)
    assert "approval_unavailable" not in quote, "a Ledger wallet is a VOOL wallet: the sheet approves it"
    result = _approve(engine, proposal.proposal_id, quote)
    assert result["transfer"]["state"] == "confirmed"
    assert len(soft.shown) == 1, "the person read and pressed exactly one transaction on the Ledger"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_CONFIRMED


def test_a_send_rejected_on_the_ledger_sends_nothing_and_releases_the_money(on_ledger, pilot):
    from core.wallet import proposals, quotes, transfers

    made = _add()
    on_ledger["ledgers"][0].approve = False
    engine, proposal = _pending(made)
    quote = _quote(proposal.proposal_id)
    assert _refusal(lambda: _approve(engine, proposal.proposal_id, quote)) == ("wallet_signing_unavailable", "ledger_refused_on_ledger")
    assert pilot.send_count() == 0
    assert transfers.get_transfer_by_id(proposal.proposal_id)["state"] == transfers.STATE_RELEASED
    assert _hold(proposal.proposal_id)[2] == "released", "the held amount is free again"
    # the same as any signing failure: this preview is used up and the payment is marked failed, never sent
    assert proposals.get_proposal(proposal.proposal_id).state == "failed" and quotes.get_quote(quote["quote_id"])["state"] == "consumed"


@pytest.mark.parametrize(("swap", "reason"), [
    (lambda soft: [], "ledger_no_ledger"),
    (lambda soft: [SoftLedger("B")], "ledger_not_this_ledger"),
    (lambda soft: [SoftLedger("A", app_open=False)], "ledger_open_solana_app"),
    (lambda soft: [SoftLedger("A", approve=None)], "ledger_ledger_timeout"),
])
def test_without_this_ledger_ready_nothing_is_sent(on_ledger, pilot, swap, reason):
    made = _add()
    on_ledger["ledgers"] = swap(on_ledger["ledgers"][0])
    engine, proposal = _pending(made)
    quote = _quote(proposal.proposal_id)
    assert _refusal(lambda: _approve(engine, proposal.proposal_id, quote)) == ("wallet_signing_unavailable", reason)
    assert pilot.send_count() == 0


def test_the_pin_still_guards_a_ledger_wallet_and_is_counted(on_ledger, pilot):
    soft = on_ledger["ledgers"][0]
    made = _add()
    engine, proposal = _pending(made)
    quote = _quote(proposal.proposal_id)
    code, _reason = _refusal(lambda: _approve(engine, proposal.proposal_id, quote, pin=WRONG_PIN))
    assert code == "wallet_pin_invalid" and soft.shown == [] and pilot.send_count() == 0


def test_a_ledger_wallet_has_no_backup_in_vool_and_takes_no_security_key(on_ledger, monkeypatch, tmp_path):
    from core.wallet import keychain_backup, pilot_custody

    made = _add()
    wid = made["wallet_id"]
    assert _refusal(lambda: keychain_backup.save(wid, credential=PIN, password="river lamp orbit 42", confirmation="river lamp orbit 42"))[1] == \
        "ledger_wallet_backup_is_on_the_ledger"
    assert _refusal(lambda: pilot_custody.reveal_pilot_backup(wid, credential=PIN))[0] == "wallet_backup_unavailable"
    assert _refusal(lambda: pilot_custody.recover_with_backup(wid, backup_value="x" * 70, method="pin", credential="551177",
                                                             credential_confirmation="551177"))[1] == "ledger_wallet_backup_is_on_the_ledger"
    assert _refusal(lambda: pilot_custody.add_security_key(wid, credential=PIN))[1] == "ledger_wallet_needs_no_security_key"


def test_the_same_ledger_account_added_again_sets_a_new_pin_on_the_same_wallet(on_ledger):
    from core.wallet import pilot_custody, wallet_roles

    made = _add()
    again = _add(pin="551177")
    assert again["wallet_id"] == made["wallet_id"]
    with pilot_custody.signing_session(made["wallet_id"], "551177") as signer:
        assert len(signer.sign_svm(b"after a forgotten PIN")) == 64
    assert _refusal(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN))[0] == "wallet_pin_invalid"
    wallet_roles.remove_wallet(made["wallet_id"], credential="551177", backup_confirmed=True)
    back = _add(pin="482913")
    assert back["wallet_id"] == made["wallet_id"] and back["setup_state"] == "ready"


def test_a_vool_held_wallet_is_never_turned_into_a_ledger_wallet(on_ledger, monkeypatch):
    from core.wallet import pilot_custody

    made = _add(account=1)
    from core.wallet.store import connection

    with connection() as conn:
        conn.execute("DELETE FROM wallet_ledgers WHERE wallet_id = ?", (made["wallet_id"],))  # as if VOOL held this key
    assert _refusal(lambda: _add(account=1))[1] == "ledger_address_already_a_vool_wallet"
    assert pilot_custody.ledger_of(made["wallet_id"]) is None


def test_only_solana_and_only_a_pin_or_password(on_ledger):
    import uuid

    from core.wallet import pilot_custody

    assert _refusal(lambda: pilot_custody.add_ledger_wallet(network=SOLANA_DEVNET, method="device", creation_key=f"l-{uuid.uuid4().hex}"))[1] == \
        "ledger_needs_pin_or_password"
    assert _refusal(lambda: _add(account=-1))[1] == "ledger_account_invalid"
    on_ledger["ledgers"] = [SoftLedger("A", locked=True)]
    assert _refusal(lambda: _add())[1] == "ledger_ledger_locked"
