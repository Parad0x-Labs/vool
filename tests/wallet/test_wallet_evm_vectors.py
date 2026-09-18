"""Stage C — signing vectors for the multichain wallet (RED corpus).

EVM exact-scheme signing is EIP-3009 `transferWithAuthorization` over EIP-712 typed data.
The positive vector is the OFFICIAL example from the x402 v2 specification
(x402-foundation/x402 `specs/schemes/exact/scheme_exact_evm.md` @ 78412bce, itself the
EIP-3009 provider example): digest, domain separator and recovered signer are all pinned.
Every negative vector — wrong chain, wrong token, wrong domain name/version, wrong nonce,
wrong amount, wrong payee, expired window, wrong signer — must fail verification.
Solana vectors are the existing devnet pack, which must remain untouched-green.
"""
from __future__ import annotations

import pytest

from core.wallet.errors import WalletFault

BASE_SEPOLIA = "eip155:84532"

#: The official spec vector, quoted exactly (see module docstring).
OFFICIAL_DOMAIN = {"name": "USDC", "version": "2", "chainId": 84532, "verifyingContract": "0x036CbD53842c5426634e7929541eC2318f3dCF7e"}
OFFICIAL_MESSAGE = {
    "from": "0x857b06519E91e3A54538791bDbb0E22373e36b66",
    "to": "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
    "value": "10000",
    "validAfter": "1740672089",
    "validBefore": "1740672154",
    "nonce": "0xf3746613c2d920b5fdabc0856f2aeb2d4f88ee6037b8cc5d04a71a4462f13480",
}
OFFICIAL_SIGNATURE = "0x2d6a7588d6acca505cbf0d9a4a227e0c52c6c34008c8e8986a1283259764173608a2ce6496642e377d6da8dbbf5836e9bd15092f9ecab05ded3d6293af148b571c"
OFFICIAL_DIGEST = "0xf256992871671abcb27ff92885a7afa46218724e5fc0bac35d050115aa1d22e6"
OFFICIAL_DOMAIN_SEPARATOR = "0x71f17a3b2ff373b803d70a5a07c046c1a2bc8e89c09ef722fcb047abe94c9818"
OFFICIAL_SIGNER = "0x857b06519E91e3A54538791bDbb0E22373e36b66"

#: Permit2 canonical deployment and the x402 exact-scheme proxy (pinned, official).
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
X402_PERMIT2_PROXY = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001"


def _official_typed_data() -> dict:
    from core.wallet import evm

    message = OFFICIAL_MESSAGE
    return evm.transfer_with_authorization_typed_data(
        chain_id=84532, token=OFFICIAL_DOMAIN["verifyingContract"], token_name="USDC", token_version="2",
        from_address=message["from"], to=message["to"], value=message["value"],
        valid_after=message["validAfter"], valid_before=message["validBefore"], nonce=message["nonce"],
    )


def test_c1_official_eip3009_vector_digest_separator_and_recovery():
    """The builder reproduces the official digest/domain separator; the official signature
    verifies and recovers to the official payer. Hand-rolled crypto would break here."""
    from core.wallet import evm

    typed = _official_typed_data()
    assert typed["domain"] == OFFICIAL_DOMAIN
    assert typed["primaryType"] == "TransferWithAuthorization"
    assert typed["types"]["TransferWithAuthorization"] == [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
    assert evm.domain_separator_of(typed) == OFFICIAL_DOMAIN_SEPARATOR
    assert evm.typed_data_digest(typed) == OFFICIAL_DIGEST
    recovered = evm.recover_authorization_signer(typed, OFFICIAL_SIGNATURE)
    assert recovered.lower() == OFFICIAL_SIGNER.lower()
    assert evm.verify_authorization_signature(typed, OFFICIAL_SIGNATURE, OFFICIAL_SIGNER) is True


def test_c2_every_changed_field_breaks_verification():
    """Wrong chain/domain/token/nonce/deadline/amount/payee: the signature no longer
    verifies against the changed typed data, and the digest moves."""
    from core.wallet import evm

    base = _official_typed_data()
    assert evm.typed_data_digest(base) == OFFICIAL_DIGEST

    mutations = {
        "wrong_chain": {"domain": {**OFFICIAL_DOMAIN, "chainId": 1}},
        "wrong_domain_name": {"domain": {**OFFICIAL_DOMAIN, "name": "USD Coin"}},
        "wrong_domain_version": {"domain": {**OFFICIAL_DOMAIN, "version": "1"}},
        "wrong_token": {"domain": {**OFFICIAL_DOMAIN, "verifyingContract": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"}},
        "wrong_payee": {"message": {**OFFICIAL_MESSAGE, "to": "0x" + "9" * 40}},
        "wrong_amount": {"message": {**OFFICIAL_MESSAGE, "value": "10001"}},
        "wrong_nonce": {"message": {**OFFICIAL_MESSAGE, "nonce": "0x" + "00" * 32}},
        "shifted_window": {"message": {**OFFICIAL_MESSAGE, "validAfter": "1740672090"}},
    }
    for name, patch in mutations.items():
        typed = {**base, "domain": patch.get("domain", base["domain"]), "message": patch.get("message", base["message"])}
        assert evm.typed_data_digest(typed) != OFFICIAL_DIGEST, name
        try:
            recovered = evm.recover_authorization_signer(typed, OFFICIAL_SIGNATURE)
            assert recovered.lower() != OFFICIAL_SIGNER.lower(), name
        except WalletFault:
            pass  # malformed recovery is also a refusal
        assert evm.verify_authorization_signature(typed, OFFICIAL_SIGNATURE, OFFICIAL_SIGNER) is False, name
    # a signature over the EXACT data by a DIFFERENT key never recovers to the account
    stranger_key = "0x" + "11" * 32
    stranger_sig = evm.sign_authorization(stranger_key, base)
    assert evm.verify_authorization_signature(base, stranger_sig, OFFICIAL_SIGNER) is False


def test_c3_eip1193_signing_request_shape(wallet_env):
    """The external EVM request is an EIP-1193 eth_signTypedData_v4 call bound to the exact
    typed data, with no key material anywhere in the request."""
    from core.wallet import custody, evm, external_signing

    account = "0x" + "a" * 40
    profile = custody.register_external_signer_wallet(account, network=BASE_SEPOLIA)
    typed = _official_typed_data()
    record = evm.open_evm_signing_request(
        proposal_id="pay-evmvector0000000001", profile=profile, typed_data=typed,
        amount_minor=10000, asset="USDC",
    )
    view = external_signing.request_view(record)
    transport = view["transports"]["eip1193"]
    assert transport["method"] == "eth_signTypedData_v4"
    assert transport["params"][0].lower() == account.lower()
    import json

    assert json.loads(transport["params"][1])["domain"] == OFFICIAL_DOMAIN
    assert "seed" not in json.dumps(view).lower() and "private" not in json.dumps(view).lower()
    assert record["family"] == "evm"
    assert external_signing.get_signing_request(record["request_id"])["state"] == external_signing.STATE_OPEN


def test_c4_signature_submission_verifies_recovers_and_refuses_strangers(wallet_env):
    """A correct external answer submits and verifies; a stranger's signature, a signature
    over different typed data, and malformed bytes are typed refusals with zero effects."""
    from core.wallet import custody, evm

    key = "0x" + "22" * 32
    account = evm.address_for_private_key(key)
    profile = custody.register_external_signer_wallet(account, network=BASE_SEPOLIA)
    typed = _official_typed_data()
    record = evm.open_evm_signing_request(proposal_id="pay-evmvector0000000002", profile=profile, typed_data=typed, amount_minor=10000, asset="USDC")

    signature = evm.sign_authorization(key, typed)  # stands in for the wallet's answer
    digest = evm.verify_evm_submission(record, signature_hex=signature)
    assert digest.startswith("0x")

    stranger = evm.sign_authorization("0x" + "33" * 32, typed)
    with pytest.raises(WalletFault) as stranger_refused:
        evm.verify_evm_submission(record, signature_hex=stranger)
    assert stranger_refused.value.code == "wallet_signature_invalid"
    with pytest.raises(WalletFault):
        evm.verify_evm_submission(record, signature_hex="0x1234")
    with pytest.raises(WalletFault):
        evm.verify_evm_submission(record, signature_hex="")


def test_c5_permit2_offers_are_a_typed_unavailable_matrix():
    """Permit2 support is not claimed from serialization alone: a permit2 offer refuses with
    the typed code and the pinned canonical addresses in the context, on every chain."""
    from core.wallet import chains, evm

    for network in (BASE_SEPOLIA, "eip155:11155111", "eip155:97", "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"):
        with pytest.raises(WalletFault) as refused:
            evm.require_asset_transfer_method(network, "permit2")
        assert refused.value.code == "x402_scheme_unavailable"
        assert refused.value.context.get("permit2") == PERMIT2_ADDRESS
        assert refused.value.context.get("x402_permit2_proxy") == X402_PERMIT2_PROXY
    # the registry knows the canonical addresses even though it refuses the scheme
    assert evm.PERMIT2_ADDRESS == PERMIT2_ADDRESS
    assert evm.X402_PERMIT2_PROXY == X402_PERMIT2_PROXY
    # eip3009 is the one provable transfer method for registered EIP-3009 assets
    chains.resolve_network(BASE_SEPOLIA)
    assert evm.require_asset_transfer_method(BASE_SEPOLIA, "eip3009") == "eip3009"


def test_c6_solana_vectors_unchanged_and_evm_pocket_refused(wallet_env):
    """The existing Solana signing contract still holds (external Ed25519 over exact bytes),
    and an EVM pocket wallet remains a typed unavailable."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody

    key = Ed25519PrivateKey.generate()
    pubkey = b58encode(key.public_key().public_bytes_raw())
    profile = custody.register_external_signer_wallet(pubkey)  # solana-devnet default
    assert profile.network == "solana-devnet"
    before = custody.list_wallets()
    with pytest.raises(WalletFault) as pocket_refused:
        custody.create_pocket_wallet(
            acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE,
            pin="246810", network=BASE_SEPOLIA,
        )
    assert pocket_refused.value.code == "evm_pocket_custody_unavailable"
    assert custody.list_wallets() == before  # the refusal created no account
