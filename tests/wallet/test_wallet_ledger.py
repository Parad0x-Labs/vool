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
    """Add (or add again) the Ledger account. The Ledger is asked to sign the proof transfer once; that press is
    checked here and then cleared, so a test counts only the presses that come after."""
    import uuid

    from core.wallet import pilot_custody

    ledgers = [soft for soft in (ledger._transport_source() if ledger._transport_source else []) if isinstance(soft, SoftLedger)]
    before = [len(soft.shown) for soft in ledgers]
    made = pilot_custody.add_ledger_wallet(network=SOLANA_DEVNET, account=account, method="pin", credential=pin, credential_confirmation=pin,
                                           creation_key=f"l-{uuid.uuid4().hex}", label="My Ledger")
    for soft, count in zip(ledgers, before):
        assert len(soft.shown) == count + 1, "adding a Ledger asks it to sign the proof transfer once"
        soft.shown.clear()
    return made


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


# --- review fixes: the Ledger proves the key before any PIN is set; one deadline inside the claim ----------------

class AddressOnly(SoftLedger):
    """Not a Ledger: a USB gadget with Ledger's vendor id that answers the address question with a PUBLIC address it
    read off the page or the chain, and can sign nothing."""

    def __init__(self, address: str, *, signs_with: SoftLedger | None = None):
        super().__init__("gadget")
        from core.vool_wallet import decode_solana_pubkey

        self.raw = decode_solana_pubkey(address)
        self.signs_with = signs_with  # a gadget that signs with ITS OWN key, not the address's

    def _answer(self, apdu: bytes):
        if apdu[1] == ledger.INS_GET_PUBKEY:
            return self.raw, ledger.SW_OK
        if self.signs_with is not None:
            return self.signs_with._answer(apdu)
        return b"", 0x6D00


@pytest.mark.parametrize("signs", [False, True])
def test_a_gadget_that_only_knows_the_address_never_sets_a_pin_on_a_ledger_wallet(on_ledger, signs, monkeypatch):
    from core.wallet import custody, pilot_custody

    made = _add()
    resealed: list[str] = []
    real_commit = pilot_custody._commit_envelope
    monkeypatch.setattr(pilot_custody, "_commit_envelope", lambda row, **kw: resealed.append(kw.get("event")) or real_commit(row, **kw))
    for _ in range(pilot_custody.WALLET_LOCK_AFTER):
        _refusal(lambda: pilot_custody.prove_credential(made["wallet_id"], WRONG_PIN))
    assert _refusal(lambda: pilot_custody.prove_credential(made["wallet_id"], PIN))[0] == "wallet_unlock_throttled"
    gadget = AddressOnly(made["address"], signs_with=SoftLedger("other") if signs else None)
    on_ledger["ledgers"] = [gadget]
    code, reason = _refusal(lambda: _add(pin="111222"))
    assert code == "wallet_signing_unavailable" and reason == ("ledger_not_this_ledger" if signs else "ledger_open_solana_app")
    # nothing changed: the gadget's PIN proves nothing, the wrong-PIN lock still stands, the owner is still the owner
    assert _refusal(lambda: pilot_custody.prove_credential(made["wallet_id"], "111222"))[0] == "wallet_unlock_throttled"
    assert _refusal(lambda: custody.prove_owner(made["wallet_id"], credential="111222", purpose="raise a cap"))[0] in {"wallet_unlock_throttled", "wallet_pin_invalid"}
    assert resealed == [], "the seal was never touched"


def test_a_gadget_never_adds_a_ledger_wallet_for_someone_elses_address(on_ledger):
    from core.wallet import pilot_custody
    from core.wallet.store import connection

    someone = SoftLedger("someone else")
    address = b58encode(someone.public_bytes(ledger.solana_path(0)))
    on_ledger["ledgers"] = [AddressOnly(address)]
    assert _refusal(lambda: _add())[1] == "ledger_open_solana_app"
    on_ledger["ledgers"] = [AddressOnly(address, signs_with=SoftLedger("gadget key"))]
    assert _refusal(lambda: _add())[1] == "ledger_not_this_ledger"
    with connection() as conn:
        assert conn.execute("SELECT count(*) FROM wallet_ledgers").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM wallet_profiles").fetchone()[0] == 0
    assert pilot_custody.ledger_of("anything") is None


def test_the_proof_is_a_zero_sol_transfer_to_itself_that_can_never_land(on_ledger):
    from solders.message import Message
    from solders.pubkey import Pubkey

    soft = on_ledger["ledgers"][0]
    address = b58encode(soft.public_bytes(ledger.solana_path(0)))
    first, second = ledger.proof_message(address), ledger.proof_message(address)
    assert first != second, "a fresh random blockhash every time: an old proof never answers a new add"
    parsed = Message.from_bytes(first)
    assert [str(key) for key in parsed.account_keys] == [address, str(Pubkey.from_string("11111111111111111111111111111111"))]
    assert len(parsed.instructions) == 1 and bytes(parsed.instructions[0].data) == bytes([2, 0, 0, 0]) + (0).to_bytes(8, "little"), "transfer of 0 lamports"
    ledger.prove_key(ledger.solana_path(0), address)
    assert soft.shown and Message.from_bytes(soft.shown[-1]).account_keys[0] == parsed.account_keys[0]
    soft.approve = False
    with pytest.raises(ledger.LedgerProblem) as refused:
        ledger.prove_key(ledger.solana_path(0), address)
    assert refused.value.reason == "refused_on_ledger"


def test_a_send_asks_the_ledger_inside_one_deadline_shorter_than_the_claim(on_ledger, pilot):
    from core.wallet import settlement

    soft = on_ledger["ledgers"][0]
    made = _add()
    soft.timeouts.clear()
    engine, proposal = _pending(made)
    quote = _quote(proposal.proposal_id)
    assert _approve(engine, proposal.proposal_id, quote)["transfer"]["state"] == "confirmed"
    assert ledger.SIGN_BUDGET_SECONDS + ledger.LOCK_WAIT_SECONDS < settlement.SIGNER_BUDGET_SECONDS
    address_check, *press = soft.timeouts
    assert 0 < address_check <= ledger.ADDRESS_CHECK_SECONDS
    assert press and all(0 < left <= ledger.SIGN_BUDGET_SECONDS for left in press)


def test_a_ledger_busy_with_another_approval_says_so_quickly_and_the_status_never_waits(on_ledger, monkeypatch):
    import threading
    import time

    monkeypatch.setattr(ledger, "LOCK_WAIT_SECONDS", 0.2)
    soft = on_ledger["ledgers"][0]
    address = b58encode(soft.public_bytes(ledger.solana_path(0)))
    held, done = threading.Event(), threading.Event()

    def press_pending() -> None:
        with ledger._LOCK:
            held.set()
            done.wait(5)

    worker = threading.Thread(target=press_pending)
    worker.start()
    try:
        held.wait(5)
        started = time.monotonic()
        assert ledger.plugged_in() is True and time.monotonic() - started < 0.1, "the status never waits behind a press"
        with pytest.raises(ledger.LedgerProblem) as busy:
            ledger.sign_solana_message(ledger.solana_path(0), b"pay", expected_public_key=address)
        assert busy.value.reason == "ledger_busy" and time.monotonic() - started < 2
        assert soft.shown == []
    finally:
        done.set()
        worker.join()


def test_a_deadline_already_spent_never_reaches_the_ledger(on_ledger):
    import time

    soft = on_ledger["ledgers"][0]
    address = b58encode(soft.public_bytes(ledger.solana_path(0)))
    with pytest.raises(ledger.LedgerProblem) as late:
        ledger.sign_solana_message(ledger.solana_path(0), b"pay", expected_public_key=address, deadline=time.monotonic() - 1)
    assert late.value.reason in {"ledger_busy", "ledger_timeout"} and soft.shown == []


def test_a_late_answer_left_in_the_usb_buffer_is_thrown_away_not_read_as_the_next_answer(on_ledger):
    soft = on_ledger["ledgers"][0]
    path = ledger.solana_path(0)
    address = b58encode(soft.public_bytes(path))
    soft.stale = [(b"\x07" * 64, ledger.SW_OK)]  # a press that came after VOOL stopped waiting
    assert ledger.public_key(path) == address and soft.drained == 1
    soft.stale = [(b"\x07" * 64, ledger.SW_OK)]
    assert len(ledger.sign_solana_message(path, b"next payment", expected_public_key=address)) == 64


def test_a_press_after_the_claim_ended_says_it_took_too_long_and_frees_the_money(on_ledger, pilot, monkeypatch):
    from core.wallet import proposals, settlement, transfers

    made = _add()
    engine, proposal = _pending(made)
    quote = _quote(proposal.proposal_id)
    soft = on_ledger["ledgers"][0]
    real_answer = soft._answer

    def slow_press(apdu: bytes):
        if apdu[1] == ledger.INS_SIGN_MESSAGE:
            # while the person reads the Ledger, the claim's lease runs out and the status poll's recovery frees it
            row = transfers.get_transfer_by_id(proposal.proposal_id)
            settlement.release_claimed(proposal.proposal_id, reason="lease_expired_during_press")
            assert row["state"] == transfers.STATE_CLAIMED
        return real_answer(apdu)

    monkeypatch.setattr(soft, "_answer", slow_press)
    assert _refusal(lambda: _approve(engine, proposal.proposal_id, quote)) == ("wallet_signing_unavailable", "ledger_pressed_too_late")
    assert pilot.send_count() == 0 and len(soft.shown) == 1
    assert transfers.get_transfer_by_id(proposal.proposal_id)["state"] == transfers.STATE_RELEASED
    assert _hold(proposal.proposal_id)[2] == "released", "the held amount is free again"
    assert proposals.get_proposal(proposal.proposal_id).state != proposals.STATE_CONFIRMED


def test_the_ledger_words_cover_every_reason_and_say_what_to_do_after_a_timeout():
    from core.web.api import wallet_api

    for reason in ("no_ledger", "more_than_one_ledger", "ledger_locked", "open_solana_app", "refused_on_ledger", "ledger_timeout", "ledger_busy",
                   "not_this_ledger", "ledger_error"):
        assert f"ledger_{reason}" in wallet_api._LEDGER_WORDS, reason
    assert "reject it there" in wallet_api._LEDGER_WORDS["ledger_ledger_timeout"]
    late = wallet_api._LEDGER_WORDS["ledger_pressed_too_late"]
    assert "nothing was sent" in late and "free again" in late and "already" not in late
