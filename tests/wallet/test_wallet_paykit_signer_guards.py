"""The pay-kit lane's signer and settlement guards, which need no pay-kit: they run in every install and in CI.

The signature guard (``verify_payment_message``) stands between pay-kit and this wallet's key: every rule it holds has
a case here that breaks exactly that rule, for SOL and for a registered token (USDC). The guarded signer signs once
and never before the guard passed. Settlement counts only the transaction it was asked about, and only on that
transaction's complete execution result. The lanes that drive these guards through pay-kit itself stay in the
optional modules beside this one.
"""
from __future__ import annotations

import base64

import pytest

from tests.wallet._rig import DESTINATION, OTHER_DESTINATION

pytestmark = [pytest.mark.safety]


# --- building messages ---------------------------------------------------------------------------------------------

def _key() -> str:
    from solders.keypair import Keypair

    return str(Keypair().pubkey())


def _usdc():
    from core.wallet import chains

    return chains.asset_for(chains.SOLANA_DEVNET, "USDC")


def _compile(instructions, *, fee_payer, lookups=()) -> bytes:
    from solders.hash import Hash
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey

    return bytes(to_bytes_versioned(MessageV0.try_compile(Pubkey.from_string(fee_payer), instructions, list(lookups), Hash.default())))


def _ix(program, data, accounts=()):
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    return Instruction(Pubkey.from_string(program), bytes(data), [AccountMeta(Pubkey.from_string(k), signer, writable) for k, signer, writable in accounts])


def _sol(source, destination, lamports):
    from core.wallet import paykit_x402

    return _ix(paykit_x402.SYSTEM_PROGRAM, (2).to_bytes(4, "little") + int(lamports).to_bytes(8, "little"), [(source, True, True), (destination, False, True)])


def _token(owner, pay_to, mint, amount, decimals, *, kind=12, source="", destination=""):
    from core.wallet import paykit_x402

    source = source or paykit_x402._ata(owner, mint, paykit_x402.TOKEN_PROGRAM)
    destination = destination or paykit_x402._ata(pay_to, mint, paykit_x402.TOKEN_PROGRAM)
    data = bytes([kind]) + int(amount).to_bytes(8, "little") + bytes([decimals])
    return _ix(paykit_x402.TOKEN_PROGRAM, data, [(source, False, True), (mint, False, False), (destination, False, True), (owner, True, False)])


def _memo(*accounts, signer=False):
    from core.wallet import paykit_x402

    return _ix(paykit_x402.MEMO_PROGRAM, b"nonce", [(a, signer, False) for a in accounts])


def _budget(kind, value, size, *accounts):
    from core.wallet import paykit_x402

    return _ix(paykit_x402.COMPUTE_BUDGET_PROGRAM, bytes([kind]) + int(value).to_bytes(size - 1, "little"), [(a, False, False) for a in accounts])


def _refused(message, **terms) -> str:
    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    with pytest.raises(WalletFault) as exc:
        paykit_x402.verify_payment_message(message, **terms)
    assert exc.value.code == "wallet_signature_invalid"
    return str(exc.value.context["reason"])


# --- the signature guard, rule by rule -----------------------------------------------------------------------------

def _sol_case(name, payer, fee_payer):
    other = _key()
    transfer = _sol(payer, OTHER_DESTINATION, 1500)
    cases = {
        "a lookup table": ([transfer, _memo()], "paykit_message_uses_lookup_tables"),
        "a third signer": ([transfer, _memo(other, signer=True)], "paykit_unexpected_signers"),
        "a memo naming an account": ([transfer, _memo(other)], "paykit_memo_names_accounts"),
        "another program": ([transfer, _ix(other, b"\x01")], "paykit_unexpected_program"),
        "two memos": ([transfer, _memo(), _memo()], "paykit_instruction_count_differs"),
        "three compute-budget settings": ([_budget(2, 20_000, 5), _budget(3, 1, 9), _budget(3, 1, 9), transfer], "paykit_instruction_count_differs"),
        "a compute-budget setting of the wrong size": ([_budget(2, 20_000, 6), transfer], "paykit_unexpected_compute_budget_instruction"),
        "a compute-budget setting naming an account": ([_budget(2, 20_000, 5, other), transfer], "paykit_unexpected_compute_budget_instruction"),
    }
    instructions, reason = cases[name]
    lookups = ()
    if name == "a lookup table":
        from solders.address_lookup_table_account import AddressLookupTableAccount
        from solders.pubkey import Pubkey

        lookups = (AddressLookupTableAccount(key=Pubkey.from_string(_key()), addresses=[Pubkey.from_string(OTHER_DESTINATION)]),)
    return _compile(instructions, fee_payer=fee_payer, lookups=lookups), reason


@pytest.mark.parametrize("name", ["a lookup table", "a third signer", "a memo naming an account", "another program", "two memos",
                                  "three compute-budget settings", "a compute-budget setting of the wrong size",
                                  "a compute-budget setting naming an account"])
def test_the_guard_refuses_a_sol_payment_with(name):
    payer, fee_payer = _key(), _key()
    message, reason = _sol_case(name, payer, fee_payer)
    terms = {"payer": payer, "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    assert _refused(message, **terms) == reason


def test_the_guard_refuses_bytes_that_are_not_exactly_the_message():
    from core.wallet import paykit_x402

    payer, fee_payer = _key(), _key()
    terms = {"payer": payer, "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    good = _compile([_sol(payer, OTHER_DESTINATION, 1500), _memo()], fee_payer=fee_payer)
    assert paykit_x402.verify_payment_message(good, **terms)["amount_minor"] == 1500
    assert _refused(good + b"\x00", **terms) == "paykit_message_not_canonical"


@pytest.mark.parametrize("name", ["exact", "another payee's account", "another source account", "other decimals", "another amount",
                                  "a plain Transfer", "a System transfer beside it", "another program beside it"])
def test_the_guard_holds_a_token_payment_to_the_exact_transfer(name):
    from core.wallet import paykit_x402

    payer, fee_payer, other = _key(), _key(), _key()
    usdc = _usdc()
    mint, decimals = usdc.address, usdc.decimals
    exact = _token(payer, OTHER_DESTINATION, mint, 1500, decimals)
    cases = {
        "exact": ([_budget(2, 20_000, 5), _budget(3, 1, 9), exact, _memo()], None),
        "another payee's account": ([_token(payer, other, mint, 1500, decimals)], "paykit_transfer_parties_differ"),
        "another source account": ([_token(payer, OTHER_DESTINATION, mint, 1500, decimals, source=paykit_x402._ata(other, mint, paykit_x402.TOKEN_PROGRAM))], "paykit_transfer_parties_differ"),
        "other decimals": ([_token(payer, OTHER_DESTINATION, mint, 1500, 9)], "paykit_transfer_decimals_differ"),
        "another amount": ([_token(payer, OTHER_DESTINATION, mint, 1501, decimals)], "paykit_transfer_amount_differs"),
        "a plain Transfer": ([_token(payer, OTHER_DESTINATION, mint, 1500, decimals, kind=3)], "paykit_unexpected_token_instruction"),
        # with a mint approved, a System transfer out of this wallet is never "the transfer"
        "a System transfer beside it": ([exact, _sol(payer, other, 1)], "paykit_unexpected_program"),
        "another program beside it": ([exact, _ix(other, b"\x01")], "paykit_unexpected_program"),
    }
    instructions, reason = cases[name]
    message = _compile(instructions, fee_payer=fee_payer)
    terms = {"payer": payer, "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": mint, "decimals": decimals, "fee_payer": fee_payer}
    if reason is None:
        proof = paykit_x402.verify_payment_message(message, **terms)
        assert proof["mint"] == mint and proof["amount_minor"] == 1500 and proof["pay_to"] == OTHER_DESTINATION
    else:
        assert _refused(message, **terms) == reason


def test_the_signer_is_never_asked_before_the_guard_passed():
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    key = Keypair()
    asked: list[bytes] = []

    class _Spy:
        public_key = str(key.pubkey())

        def sign(self, message):
            asked.append(bytes(message))
            return bytes(key.sign_message(message))

    fee_payer = _key()
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    guarded = paykit_x402.GuardedSigner(_Spy(), terms=terms)
    with pytest.raises(WalletFault):
        guarded.sign(_compile([_sol(_Spy.public_key, OTHER_DESTINATION, 1501), _memo()], fee_payer=fee_payer))
    assert asked == [] and guarded.signature == b""


# --- settlement ------------------------------------------------------------------------------------------------------

def test_settlement_counts_only_the_transaction_it_was_asked_about():
    """An endpoint that answers the same successful transaction whatever id it is asked about proves only that id."""
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0, to_bytes_versioned
    from solders.transaction import VersionedTransaction

    from core.wallet import paykit_x402

    payer, fee_payer = Keypair(), Keypair()
    message = MessageV0.try_compile(fee_payer.pubkey(), [_sol(str(payer.pubkey()), OTHER_DESTINATION, 1500), _memo()], [], Hash.default())
    signed = bytes(to_bytes_versioned(message))
    landed = VersionedTransaction.populate(message, [fee_payer.sign_message(signed), payer.sign_message(signed)])
    wire = base64.b64encode(bytes(landed)).decode()

    class _SameAnswer:
        def _call(self, method, params):
            assert method == "getTransaction"
            return {"transaction": [wire, "base64"], "meta": {"err": None}}

    def verdict(tx_id):
        return paykit_x402.verify_settlement_on_chain(_SameAnswer(), tx_id, signed_message=signed, payer=str(payer.pubkey()), attempts=1)

    assert verdict(str(landed.signatures[0])) == "settled"
    assert verdict(str(Keypair().sign_message(b"another payment"))) == "unknown"


def _one_landed_payment():
    """A payer-signed SOL payment as the chain holds it: (wire bytes, signed message, payer, transaction id)."""
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0, to_bytes_versioned
    from solders.transaction import VersionedTransaction

    payer, fee_payer = Keypair(), Keypair()
    message = MessageV0.try_compile(fee_payer.pubkey(), [_sol(str(payer.pubkey()), OTHER_DESTINATION, 1500), _memo()], [], Hash.default())
    signed = bytes(to_bytes_versioned(message))
    landed = VersionedTransaction.populate(message, [fee_payer.sign_message(signed), payer.sign_message(signed)])
    return base64.b64encode(bytes(landed)).decode(), signed, str(payer.pubkey()), str(landed.signatures[0])


class _Chain:
    """The same getTransaction answer for every read, counted."""

    def __init__(self, answer):
        self.answer = answer
        self.reads = 0

    def _call(self, method, params):
        assert method == "getTransaction"
        self.reads += 1
        return self.answer


@pytest.mark.parametrize("meta", [None, "absent", {"fee": 5000}], ids=["meta-null", "meta-absent", "meta-without-err"])
def test_missing_execution_metadata_is_never_a_settlement(meta):
    """Matching bytes and signatures prove WHICH transaction came back, not that it ran: without an explicitly present
    ``err`` there is no verdict, and the answer is read again rather than taken as final."""
    from core.wallet import paykit_x402

    wire, signed, payer, tx_id = _one_landed_payment()
    answer = {"transaction": [wire, "base64"]}
    if meta != "absent":
        answer["meta"] = meta
    chain = _Chain(answer)
    assert paykit_x402.verify_settlement_on_chain(chain, tx_id, signed_message=signed, payer=payer, attempts=3, wait_seconds=0) == "unknown"
    assert chain.reads == 3


@pytest.mark.parametrize(("meta", "verdict"), [
    ({"err": None, "fee": 5000}, ("settled", 5000)),
    ({"err": {"InstructionError": [2, "Custom"]}, "fee": 5000}, ("failed", 5000)),
    ({"err": None}, ("settled", None)),
    ({"err": {"InstructionError": [2, "Custom"]}, "fee": True}, ("failed", None)),
    ({"err": None, "fee": -1}, ("settled", None)),
], ids=["landed", "failed", "landed-fee-unstated", "failed-fee-not-a-number", "landed-fee-negative"])
def test_a_complete_execution_result_decides_and_names_the_fee_the_chain_charged(meta, verdict):
    """A fee the chain does not state as a whole number of lamports is unknown (the caller keeps the reserved maximum
    counted), never zero or a guess."""
    from core.wallet import paykit_x402

    wire, signed, payer, tx_id = _one_landed_payment()
    chain = _Chain({"transaction": [wire, "base64"], "meta": meta})
    assert paykit_x402.chain_settlement(chain, tx_id, signed_message=signed, payer=payer, attempts=3, wait_seconds=0) == verdict
    assert chain.reads == 1


# --- the x402 signature guard and the guarded signer ---------------------------------------------------------------

def _message(instructions, *, fee_payer, payer):
    from solders.hash import Hash
    from solders.message import MessageV0, to_bytes_versioned
    from solders.pubkey import Pubkey

    message = MessageV0.try_compile(Pubkey.from_string(fee_payer), instructions, [], Hash.default())
    _ = payer
    return bytes(to_bytes_versioned(message))


def _system_transfer(src, dst, lamports):
    from solders.pubkey import Pubkey
    from solders.system_program import TransferParams, transfer

    return transfer(TransferParams(from_pubkey=Pubkey.from_string(src), to_pubkey=Pubkey.from_string(dst), lamports=lamports))


def test_the_signature_guard_accepts_only_the_exact_transfer():
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    payer, fee_payer = str(Keypair().pubkey()), str(Keypair().pubkey())
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    good = _message([_system_transfer(payer, OTHER_DESTINATION, 1500), _memo()], fee_payer=fee_payer, payer=payer)
    assert paykit_x402.verify_payment_message(good, payer=payer, **terms)["amount_minor"] == 1500
    cases = {
        "a second transfer": [_system_transfer(payer, OTHER_DESTINATION, 1500), _system_transfer(payer, DESTINATION, 1), _memo()],
        "another payee": [_system_transfer(payer, DESTINATION, 1500), _memo()],
        "another amount": [_system_transfer(payer, OTHER_DESTINATION, 1501), _memo()],
        "no transfer": [_memo()],
    }
    for name, instructions in cases.items():
        with pytest.raises(WalletFault):
            paykit_x402.verify_payment_message(_message(instructions, fee_payer=fee_payer, payer=payer), payer=payer, **terms)
        assert name
    with pytest.raises(WalletFault) as self_fee:
        paykit_x402.verify_payment_message(_message([_system_transfer(payer, OTHER_DESTINATION, 1500)], fee_payer=payer, payer=payer), payer=payer, **{**terms, "fee_payer": payer})
    assert self_fee.value.context["reason"] == "paykit_fee_payer_not_the_offer"
    with pytest.raises(WalletFault) as legacy:
        paykit_x402.verify_payment_message(good[1:], payer=payer, **terms)
    assert legacy.value.context["reason"] in {"paykit_message_not_v0", "paykit_message_unparseable"}


def test_the_guarded_signer_signs_once():
    from solders.keypair import Keypair

    from core.wallet import paykit_x402
    from core.wallet.errors import WalletFault

    key = Keypair()

    class _Signer:
        public_key = str(key.pubkey())

        def sign(self, message):
            return bytes(key.sign_message(message))

    fee_payer = str(Keypair().pubkey())
    terms = {"pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9, "fee_payer": fee_payer}
    guarded = paykit_x402.GuardedSigner(_Signer(), terms=terms)
    message = _message([_system_transfer(_Signer.public_key, OTHER_DESTINATION, 1500), _memo()], fee_payer=fee_payer, payer=_Signer.public_key)
    assert len(guarded.sign(message)) == 64
    with pytest.raises(WalletFault) as exc:
        guarded.sign(message)
    assert exc.value.context["reason"] == "paykit_second_signature_refused"
    assert not hasattr(guarded.keypair, "secret") and not hasattr(guarded.keypair, "sign_message"), "pay-kit only ever sees a public key"
