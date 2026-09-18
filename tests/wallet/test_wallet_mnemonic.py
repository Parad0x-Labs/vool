"""Standard BIP39 + the canonical Solana (Phantom-compatible) derivation, proven against PUBLIC
deterministic vectors: the BIP39 English wordlist hash, Trezor's reference vectors for
entropy -> mnemonic -> seed, SLIP-0010's ed25519 vectors, and agreement between the maintained
solders derivation and an independent SLIP-0010 walk on the m/44'/501'/0'/0' path.

No live mnemonic is ever written here: every phrase below is a published test vector.
"""
from __future__ import annotations

import hashlib

import pytest

pytestmark = [pytest.mark.safety]

BIP39_ENGLISH_SHA256 = "2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda"
VECTOR_1_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
VECTOR_1_SEED_TREZOR = "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e53495531f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04"
TREZOR_ENTROPY_VECTORS = {
    "00000000000000000000000000000000": VECTOR_1_MNEMONIC,
    "7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f7f": "legal winner thank year wave sausage worth useful legal winner thank yellow",
    "80808080808080808080808080808080": "letter advice cage absurd amount doctor acoustic avoid letter advice cage above",
    "ffffffffffffffffffffffffffffffff": "zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo zoo wrong",
}
SLIP10_ED25519_VECTOR_1 = {
    (): "2b4be7f19ee27bbf30c667b642d5f4aa69fd169872f8fc3059c08ebae2eb19e7",
    (0,): "68e0fe46dfb67e368c75379acec591dad19df3cde26e63b93a8e704f1dade7a3",
    (0, 1): "b1d0bad404bf35da785a64ca1ac54b2617211d2777696fbffaf208f746ae84f2",
}


def test_the_wordlist_is_the_canonical_bip39_english_list():
    from core.wallet import mnemonic

    assert hashlib.sha256(mnemonic.WORDLIST_PATH.read_bytes()).hexdigest() == BIP39_ENGLISH_SHA256
    assert len(mnemonic.WORDS) == 2048 and mnemonic.WORDS[0] == "abandon" and mnemonic.WORDS[-1] == "zoo"


def test_entropy_to_mnemonic_matches_trezor_reference_vectors():
    from core.wallet import mnemonic

    for entropy_hex, phrase in TREZOR_ENTROPY_VECTORS.items():
        assert mnemonic.entropy_to_mnemonic(bytes.fromhex(entropy_hex)) == phrase
        assert mnemonic.validate_mnemonic(phrase)
    assert not mnemonic.validate_mnemonic("abandon " * 11 + "abandon")  # bad checksum
    assert not mnemonic.validate_mnemonic("notaword " * 12)


def test_mnemonic_to_seed_matches_trezor_and_solders():
    from solders.keypair import Keypair

    from core.wallet import mnemonic

    seed = mnemonic.mnemonic_to_seed(VECTOR_1_MNEMONIC, passphrase="TREZOR")
    assert seed.hex() == VECTOR_1_SEED_TREZOR
    # the maintained implementation agrees on the legacy (solana-keygen) key from the same phrase
    assert Keypair.from_seed(seed[:32]).pubkey() == Keypair.from_seed_phrase_and_passphrase(VECTOR_1_MNEMONIC, "TREZOR").pubkey()


def test_phantom_path_derivation_is_solders_and_agrees_with_an_independent_slip10_walk():
    from solders.keypair import Keypair

    from core.wallet import mnemonic

    assert mnemonic.SOLANA_DERIVATION_PATH == "m/44'/501'/0'/0'"
    seed = mnemonic.mnemonic_to_seed(VECTOR_1_MNEMONIC)
    derived = mnemonic.derive_solana_keypair(seed)
    independent = Keypair.from_seed(mnemonic.slip10_ed25519_private_key(seed, (44, 501, 0, 0)))
    assert str(derived.pubkey()) == str(independent.pubkey()) == "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"
    assert str(mnemonic.derive_solana_keypair(seed, account=1).pubkey()) == "Hh8QwFUA6MtVu1qAoq12ucvFHNwCcVTV7hpWjeY1Hztb"


def test_slip10_walk_reproduces_the_published_ed25519_vectors():
    from core.wallet import mnemonic

    seed = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    for path, expected in SLIP10_ED25519_VECTOR_1.items():
        assert mnemonic.slip10_ed25519_private_key(seed, path).hex() == expected, path


def test_generated_mnemonics_are_twelve_valid_words_and_restore_to_the_same_address():
    from core.wallet import mnemonic

    seen = set()
    for _ in range(5):
        phrase = mnemonic.generate_mnemonic()
        words = phrase.split()
        assert len(words) == 12 and all(w in mnemonic.INDEX for w in words)
        assert mnemonic.validate_mnemonic(phrase)
        address = mnemonic.address_for_mnemonic(phrase)
        assert mnemonic.address_for_mnemonic(" ".join(words)) == address  # restore -> identical address
        assert mnemonic.address_for_mnemonic(phrase, passphrase="x") != address
        seen.add(phrase)
    assert len(seen) == 5
    assert len(mnemonic.generate_mnemonic(strength_bits=256).split()) == 24


def test_derivation_path_is_a_constant_never_caller_supplied():
    import inspect

    from core.wallet import mnemonic

    params = inspect.signature(mnemonic.derive_solana_keypair).parameters
    assert "path" not in params and "dpath" not in params and "derivation_path" not in params
