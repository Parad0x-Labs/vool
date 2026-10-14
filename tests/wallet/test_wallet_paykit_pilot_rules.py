"""The Crypto Pilot pay-kit rules that need no pay-kit: they run in every install and in CI.

The guard signs only over the blockhash the Pilot lane read and recorded with its claim. A transaction is this payment
only when it carries this wallet's own signature over its own bytes, whoever paid its fee. A payment whose fee the
resource's fee payer paid charged this wallet nothing. While the Pilot key is open, pay-kit's builder is answered only
from what was read before it opened. The drives that pay through pay-kit itself are in test_wallet_paykit_pilot.py.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from tests.wallet._rig import OTHER_DESTINATION
from tests.wallet.test_wallet_paykit_signer_guards import _memo, _refused, _sol

pytestmark = [pytest.mark.safety]


def _landed(*, blockhash=None, amount=1500):
    """A payer-signed SOL payment the resource's fee payer cosigned: (transaction, payer keypair, signed message)."""
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import MessageV0, to_bytes_versioned
    from solders.transaction import VersionedTransaction

    payer, fee_payer = Keypair(), Keypair()
    message = MessageV0.try_compile(fee_payer.pubkey(), [_sol(str(payer.pubkey()), OTHER_DESTINATION, amount), _memo()], [], blockhash or Hash.default())
    signed = bytes(to_bytes_versioned(message))
    return VersionedTransaction.populate(message, [fee_payer.sign_message(signed), payer.sign_message(signed)]), payer, signed


def _answer(transaction) -> dict:
    return {"transaction": [base64.b64encode(bytes(transaction)).decode(), "base64"], "meta": {"err": None, "fee": 5000}}


# --- the blockhash the lane read -----------------------------------------------------------------------------------

def test_the_guard_signs_only_over_the_blockhash_the_pilot_lane_read():
    from solders.hash import Hash

    from core.wallet import paykit_x402

    transaction, payer, signed = _landed()
    terms = {"payer": str(payer.pubkey()), "pay_to": OTHER_DESTINATION, "amount_minor": 1500, "mint": "", "decimals": 9,
             "fee_payer": str(transaction.message.account_keys[0])}
    proof = paykit_x402.verify_payment_message(signed, **terms, recent_blockhash=str(Hash.default()))
    assert proof["recent_blockhash"] == str(Hash.default())
    assert _refused(signed, **terms, recent_blockhash=str(Hash.new_unique())) == "paykit_blockhash_not_the_one_read"
    assert paykit_x402.verify_payment_message(signed, **terms)["amount_minor"] == 1500, "the pocket lane names no blockhash of its own"


# --- which transaction is this payment -----------------------------------------------------------------------------

def test_a_transaction_is_this_payment_only_when_it_carries_this_wallets_signature_over_its_own_bytes():
    from solders.keypair import Keypair
    from solders.transaction import VersionedTransaction

    from core.wallet import settlement

    transaction, payer, _signed = _landed()
    tx_id, own = str(transaction.signatures[0]), str(transaction.signatures[1])
    key = str(payer.pubkey())
    assert settlement._carries_payer_signature(_answer(transaction), payer=key, payer_signature=own, tx_id=tx_id)
    assert not settlement._carries_payer_signature(_answer(transaction), payer=key, payer_signature=own, tx_id=str(Keypair().sign_message(b"x"))), \
        "the answer must be the transaction it was asked about"
    earlier, _payer, _ = _landed()
    assert not settlement._carries_payer_signature(_answer(earlier), payer=key, payer_signature=own, tx_id=str(earlier.signatures[0])), \
        "another payment, even a real one, is not this one"
    other_bytes, _p, _s = _landed(amount=1499)
    forged = VersionedTransaction.populate(other_bytes.message, [other_bytes.signatures[0], transaction.signatures[1]])
    assert not settlement._carries_payer_signature(_answer(forged), payer=key, payer_signature=own, tx_id=str(forged.signatures[0])), \
        "this wallet's signature over other bytes does not verify"
    assert not settlement._carries_payer_signature(_answer(transaction), payer=str(Keypair().pubkey()), payer_signature=own, tx_id=tx_id)
    assert not settlement._carries_payer_signature({"transaction": ["not base64", "base64"]}, payer=key, payer_signature=own, tx_id=tx_id)


class _NoReads:
    def _call(self, method, params):
        raise AssertionError(f"no chain read is needed: {method}")


class _FeeAnswer:
    def __init__(self, fee):
        self.fee = fee
        self.reads = []

    def _call(self, method, params):
        self.reads.append(method)
        return {"meta": {"err": None, "fee": self.fee}}


def test_a_payment_whose_fee_the_site_paid_charged_this_wallet_nothing():
    from core.wallet import settlement

    transaction, _payer, _signed = _landed()
    tx_id, own = str(transaction.signatures[0]), str(transaction.signatures[1])
    assert settlement._charged_fee(_NoReads(), {"payer_signature": own}, tx_id) == 0
    chain = _FeeAnswer(5000)
    assert settlement._charged_fee(chain, {"payer_signature": own}, own) == 5000, "this wallet paid the fee of its own transaction"
    assert settlement._charged_fee(chain, {"payer_signature": ""}, tx_id) == 5000, "any other transfer reads what the chain charged"
    assert chain.reads == ["getTransaction", "getTransaction"]


# --- no read while the key is open ---------------------------------------------------------------------------------

def test_while_the_key_is_open_the_builder_is_answered_only_from_reads_made_before():
    from solders.hash import Hash
    from solders.keypair import Keypair

    from core.wallet import paykit_mpp
    from core.wallet.errors import WalletFault

    blockhash, mint, token_program = str(Hash.new_unique()), str(Keypair().pubkey()), "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    reader = paykit_mpp._ReadBeforeSigning(blockhash=blockhash, owners={mint: token_program})
    assert str(asyncio.run(reader.get_latest_blockhash()).value.blockhash) == blockhash
    assert str(asyncio.run(reader.get_account(mint)).value.owner) == token_program
    with pytest.raises(WalletFault) as refused:
        asyncio.run(reader.get_account(str(Keypair().pubkey())))
    assert (refused.value.code, refused.value.context["reason"]) == ("wallet_signing_unavailable", "paykit_read_while_key_open")
    missing = paykit_mpp._ReadBeforeSigning(blockhash=blockhash, owners={mint: ""})
    assert asyncio.run(missing.get_account(mint)).value is None, "an account that did not exist then does not exist now"
