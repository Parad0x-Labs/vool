"""A wallet never asks a human to approve bytes whose consequences it cannot explain.

`core.wallet.meaning` turns the exact bytes or typed data a user is about to sign into one plain sentence and a
tier, derived from decoding, never from a model:
* GREEN  -- "Exactly X goes to Y, once. No continuing permission."
* AMBER  -- "Up to X / until date Y / limited to scope Z."
* RED    -- standing authority, ownership or upgrade authority, blanket access, or anything that cannot be
            completely decoded. Red names the address and the capability, adds "This is standing authority, not a
            one-time payment", and its acknowledgement repeats the actual capability rather than a generic risk line.
"""
from __future__ import annotations

import base64

from core.wallet import meaning

SPENDER = "0x1a2b3c4d5e6f70819293a4b5c6d7e8f90a1b2c9f"
TOKEN = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
OWNER = "0x00112233445566778899aabbccddeeff00112233"
UINT256_MAX = str(2**256 - 1)


def _typed(primary: str, message: dict, *, name: str = "USDC", chain_id: int = 84532, types: dict | None = None) -> dict:
    return {"primaryType": primary, "domain": {"name": name, "version": "2", "chainId": chain_id, "verifyingContract": TOKEN},
            "types": types or {}, "message": message}


# --- EVM typed data -------------------------------------------------------------------------------------

def test_eip3009_exact_transfer_is_green_once_no_continuing_permission():
    m = meaning.describe_evm_typed_data(_typed("TransferWithAuthorization", {"from": OWNER, "to": SPENDER, "value": "1000000", "validAfter": "0", "validBefore": "1788700000", "nonce": "0x" + "11" * 32}), decimals=6)
    assert m.tier == meaning.TIER_GREEN and m.decoded
    assert m.headline.startswith("Exactly 1 USDC goes to 0x1a2b…2c9f, once.")
    assert "No continuing permission" in m.headline
    assert m.ack_text == ""


def test_unlimited_permit_is_red_with_the_address_and_the_capability():
    m = meaning.describe_evm_typed_data(_typed("Permit", {"owner": OWNER, "spender": SPENDER, "value": UINT256_MAX, "nonce": "0", "deadline": "1788700000"}), decimals=6)
    assert m.tier == meaning.TIER_RED
    assert m.headline == "Approving this gives 0x1a2b…2c9f permission to transfer every USDC token in this wallet, from your wallet, until the permission is revoked."
    assert "This is standing authority, not a one-time payment." in m.lines
    assert m.ack_text == "I understand this gives 0x1a2b…2c9f ongoing permission to spend my USDC."


def test_bounded_permit_is_amber_up_to_amount_until_deadline():
    m = meaning.describe_evm_typed_data(_typed("Permit", {"owner": OWNER, "spender": SPENDER, "value": "25000000", "nonce": "0", "deadline": "1788700000"}), decimals=6)
    assert m.tier == meaning.TIER_AMBER
    assert m.headline.startswith("Approving this lets 0x1a2b…2c9f take up to 25 USDC from your wallet until ")
    assert "standing authority within a limit" in " ".join(m.lines)


def test_dai_style_permit_allowed_true_is_red():
    m = meaning.describe_evm_typed_data(_typed("Permit", {"holder": OWNER, "spender": SPENDER, "nonce": "0", "expiry": "1788700000", "allowed": True}, name="Dai Stablecoin"), decimals=18)
    assert m.tier == meaning.TIER_RED and "every Dai Stablecoin token" in m.headline


def test_unknown_primary_type_is_red_cannot_be_completely_explained():
    m = meaning.describe_evm_typed_data(_typed("Migrate", {"user": OWNER, "target": SPENDER, "data": "0x1234"}), decimals=6)
    assert m.tier == meaning.TIER_RED and not m.decoded
    assert m.headline.startswith("This request cannot be completely explained")
    assert m.ack_text == "I understand this wallet cannot tell me what this does, and I am signing it anyway."


# --- EVM calldata ---------------------------------------------------------------------------------------

def _word(value: int) -> str:
    return value.to_bytes(32, "big").hex()


def test_erc20_transfer_calldata_is_green():
    data = "0xa9059cbb" + _word(int(SPENDER, 16)) + _word(2_500_000)
    m = meaning.describe_evm_calldata(data, to=TOKEN, symbol="USDC", decimals=6)
    assert m.tier == meaning.TIER_GREEN and m.headline.startswith("Exactly 2.5 USDC goes to 0x1a2b…2c9f, once.")


def test_unlimited_approve_calldata_is_red():
    data = "0x095ea7b3" + _word(int(SPENDER, 16)) + _word(2**256 - 1)
    m = meaning.describe_evm_calldata(data, to=TOKEN, symbol="USDC", decimals=6)
    assert m.tier == meaning.TIER_RED and "every USDC token" in m.headline and m.ack_text.startswith("I understand this gives 0x1a2b…2c9f ongoing permission")


def test_set_approval_for_all_is_red_and_names_the_operator():
    data = "0xa22cb465" + _word(int(SPENDER, 16)) + _word(1)
    m = meaning.describe_evm_calldata(data, to=TOKEN, symbol="CoolCats", decimals=0)
    assert m.tier == meaning.TIER_RED
    assert "0x1a2b…2c9f" in m.headline and "every CoolCats token you own, now and in the future" in m.headline
    assert m.ack_text == "I understand this gives 0x1a2b…2c9f ongoing permission to move every CoolCats token I own."


def test_transfer_ownership_and_upgrade_are_red():
    own = meaning.describe_evm_calldata("0xf2fde38b" + _word(int(SPENDER, 16)), to=TOKEN, symbol="", decimals=0)
    up = meaning.describe_evm_calldata("0x3659cfe6" + _word(int(SPENDER, 16)), to=TOKEN, symbol="", decimals=0)
    assert own.tier == meaning.TIER_RED and "hands control" in own.headline and own.ack_text.startswith("I understand this hands control")
    assert up.tier == meaning.TIER_RED and "replace this contract's code" in up.headline


def test_unknown_selector_is_red_not_a_guess():
    m = meaning.describe_evm_calldata("0xdeadbeef" + _word(1), to=TOKEN, symbol="", decimals=0)
    assert m.tier == meaning.TIER_RED and not m.decoded and "cannot be completely explained" in m.headline


def test_native_value_transfer_with_empty_data_is_green():
    m = meaning.describe_evm_calldata("0x", to=SPENDER, symbol="ETH", decimals=18, value_wei=10**16)
    assert m.tier == meaning.TIER_GREEN and m.headline.startswith("Exactly 0.01 ETH goes to 0x1a2b…2c9f, once.")


# --- Solana messages (real compiled bytes) ---------------------------------------------------------------

def _solana_transfer_message(lamports: int = 1_200_000) -> tuple[bytes, str, str]:
    from solders.hash import Hash
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.system_program import TransferParams, transfer

    payer, dest = Keypair(), Keypair()
    ix = transfer(TransferParams(from_pubkey=payer.pubkey(), to_pubkey=dest.pubkey(), lamports=lamports))
    msg = Message.new_with_blockhash([ix], payer.pubkey(), Hash.default())
    return bytes(msg), str(payer.pubkey()), str(dest.pubkey())


def test_solana_system_transfer_is_green_and_names_the_destination():
    raw, _payer, dest = _solana_transfer_message()
    m = meaning.describe_solana_message(raw, network="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")
    assert m.tier == meaning.TIER_GREEN and m.decoded
    assert m.headline == f"Exactly 0.0012 SOL goes to {meaning.short_address(dest)}, once. No continuing permission."


def test_spl_set_authority_is_red_hands_control():
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.pubkey import Pubkey

    owner, account, new_auth = Keypair(), Keypair(), Keypair()
    # SPL Token SetAuthority: [6, authority_type u8 (2 = AccountOwner), option(1), new_authority 32 bytes]
    data = bytes([6, 2, 1]) + bytes(new_auth.pubkey())
    ix = Instruction(Pubkey.from_string(meaning.SPL_TOKEN_PROGRAM), data, [AccountMeta(account.pubkey(), False, True), AccountMeta(owner.pubkey(), True, False)])
    raw = bytes(Message.new_with_blockhash([ix], owner.pubkey(), Hash.default()))
    m = meaning.describe_solana_message(raw, network="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")
    assert m.tier == meaning.TIER_RED
    assert "hands control" in m.headline and meaning.short_address(str(new_auth.pubkey())) in m.headline
    assert m.ack_text.startswith("I understand this hands control")


def test_spl_unlimited_approve_is_red_bounded_is_amber_and_unknown_program_is_red():
    from solders.hash import Hash
    from solders.instruction import AccountMeta, Instruction
    from solders.keypair import Keypair
    from solders.message import Message
    from solders.pubkey import Pubkey

    owner, account, delegate = Keypair(), Keypair(), Keypair()
    metas = [AccountMeta(account.pubkey(), False, True), AccountMeta(delegate.pubkey(), False, False), AccountMeta(owner.pubkey(), True, False)]
    unlimited = Instruction(Pubkey.from_string(meaning.SPL_TOKEN_PROGRAM), bytes([4]) + (2**64 - 1).to_bytes(8, "little"), metas)
    bounded = Instruction(Pubkey.from_string(meaning.SPL_TOKEN_PROGRAM), bytes([4]) + (5_000_000).to_bytes(8, "little"), metas)
    unknown = Instruction(Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"), b"\x01\x02", [])
    net = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
    m1 = meaning.describe_solana_message(bytes(Message.new_with_blockhash([unlimited], owner.pubkey(), Hash.default())), network=net)
    m2 = meaning.describe_solana_message(bytes(Message.new_with_blockhash([bounded], owner.pubkey(), Hash.default())), network=net)
    m3 = meaning.describe_solana_message(bytes(Message.new_with_blockhash([unknown], owner.pubkey(), Hash.default())), network=net)
    assert m1.tier == meaning.TIER_RED and meaning.short_address(str(delegate.pubkey())) in m1.headline and "until the permission is revoked" in m1.headline
    assert m2.tier == meaning.TIER_AMBER and "take up to 5000000 minor units" in m2.headline
    assert m3.tier == meaning.TIER_RED and not m3.decoded and "MemoSq4g…fcHr" in m3.headline


def test_garbage_bytes_are_red_not_a_guess():
    m = meaning.describe_solana_message(b"not a message", network="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1")
    assert m.tier == meaning.TIER_RED and not m.decoded


# --- proposals and signing requests ---------------------------------------------------------------------

def test_a_proposal_reads_as_an_exact_transfer_and_its_x402_binding_as_a_payment():
    class P:  # the fields describe_proposal reads
        proposal_id = "pay-1"
        network = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
        asset = "SOL"
        amount_minor = 1_200_000
        destination = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
        origin = "user"
        memo = ""
    plain = meaning.describe_proposal(P)
    assert plain.tier == meaning.TIER_GREEN and plain.headline == "Exactly 0.0012 SOL goes to 9xQeWvG8…VFin, once. No continuing permission."
    paid = meaning.describe_proposal(P, binding={"url": "https://api.example.org/paid/report", "pay_to": P.destination, "amount_minor": 1_200_000, "asset": "SOL"})
    assert paid.tier == meaning.TIER_GREEN and "for api.example.org" in paid.headline and any("Payment for" in line for line in paid.lines)


def test_signing_bytes_that_do_not_match_the_proposal_are_red():
    raw, _payer, _dest = _solana_transfer_message(lamports=999_999_999)
    class P:
        proposal_id = "pay-2"
        network = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
        asset = "SOL"
        amount_minor = 1_200_000
        destination = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
        origin = "user"
        memo = ""
    m = meaning.describe_signing_request({"family": "svm", "message_b64": base64.b64encode(raw).decode(), "typed_data_b64": ""}, P)
    assert m.tier == meaning.TIER_RED and "do not match" in m.headline
    raw2, _payer2, dest2 = _solana_transfer_message(lamports=1_200_000)
    P.destination = dest2
    ok = meaning.describe_signing_request({"family": "svm", "message_b64": base64.b64encode(raw2).decode(), "typed_data_b64": ""}, P)
    assert ok.tier == meaning.TIER_GREEN and ok.decoded


def test_to_dict_is_json_safe_and_carries_the_principle():
    m = meaning.describe_evm_calldata("0xdeadbeef", to=TOKEN, symbol="", decimals=0)
    d = m.to_dict()
    assert set(d) >= {"tier", "headline", "lines", "ack_text", "decoded", "kind"}
    assert d["tier"] == "red" and d["ack_text"]
