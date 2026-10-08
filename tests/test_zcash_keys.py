"""The Zcash watch lane takes a viewing key and nothing that can spend: refusals store nothing and echo nothing."""
from __future__ import annotations

import pytest

from core.zcash import keys
from core.zcash.keys import ZcashKeyRefused, parse_viewing_key

# Synthetic UFVKs derived from random throwaway seeds (zcash_keys 0.16, ZIP 316 encoding); no funds, no owner.
UFVK_TEST = (
    "uviewtest1gg6as5repkt55m2yqayusheq2vz7d2l8mrl3fpdq0q2kydcq7esghzy00ufm9hfeeffns9a35zqxmrphre4uwp2pc59537hfse22pv3g4zd8qs"
    "3cq5fppwwv3avq6u0vyn5u09jeguv7l9jlzs5tk8ue60s7j3v2dca76zg7pm5xduewaqfak8hg7w4728kvpu2ksycg4hld6dfxxz5pu4fumymgad4j8lcpf"
    "q25fpx99v8qw7nhq3rq8msg836jm5f9a5m7tcq9nk22f5qhu4rp8h5ktk9x6w9828k7ure02ld02rskmpse8yhw5ynpkm3d4uf4d6djddxlrh5wc5hupra8z0"
    "f9qg4c985ep6fe6dk99w9ndmsp5gnjm43uulgpxjq3y69eg2h3j4c3007fnmlh8p0tn2tzyezyxc6f0gt9z8qk2700cuet2f57fypulmgnh8vnsvn5m6gc8h"
    "gar2ylcv7e4vz777pcnsxnkw69vul5qxm3"
)
UFVK_MAIN = (
    "uview13qecdf3dyf6g7ayh9e3pap9gl3g0hsg3a2vnsvy9txucawg4uhx6zgj2mfcxdtcark8kknlrjaqavnljkw7pm5adgmq2y3cctcuptqpjgch7h0up8eq"
    "nmg5k0kk8hgt4fkukntvhqa2g0a6awu8jzd3mnjjk5mac3akzr49lyxypceaz92a3qxgsdlxfs6cnlkkj2rtq0j3epgsv7rk9l0snkfwhg4glezh2hpf5dxejs"
    "k9pstjgg60xccywpar00fh7hetd6pa79juawvylj88astev3wc0yeq9ha7vm3q9lgqrp5h22t54y7azau2yv66f6h2ecmkl0t6ru3x0fqrvlwjehkcvt9x88xg"
    "367ywtkkw4rk3v02tuugcv5y4vyhz46ujj9nnqy0cnyxfeyrwwpvd06qvzrfuzaqd8k2utqc4dedc6d9xnujq0qsu873jn6zh6v09zmymd88u975kulmqcw0fs"
    "xwdtyk6nrlatmt99gyrx6ts"
)
SEED_PHRASE = "abandon ability able about above absent absorb abstract absurd abuse access accident"
SPENDING_KEY = "secret-extended-key-main1qd3k8v0ezqqqqpq9h0kxw6tdlu0vrv0xyzexampleexampleexampleexampleexampleexample"


class MemoryKeyring:
    """An in-process keyring backend so no test can reach the OS keychain."""

    def __init__(self, fail: bool = False) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.fail = fail

    def set_password(self, service: str, user: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("keychain denied")
        self.items[(service, user)] = value

    def get_password(self, service: str, user: str) -> str | None:
        if self.fail:
            raise RuntimeError("keychain denied")
        return self.items.get((service, user))


@pytest.fixture
def memory_keyring(monkeypatch):
    import keyring

    backend = MemoryKeyring()
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    return backend


def test_viewing_keys_are_accepted_on_their_own_network_only() -> None:
    key = parse_viewing_key(UFVK_TEST, expected_network="test")
    assert (key.network, key.kind) == ("test", "unified")
    assert UFVK_TEST not in repr(key)
    assert parse_viewing_key(UFVK_MAIN.upper(), expected_network="main").encoded == UFVK_MAIN
    with pytest.raises(ZcashKeyRefused) as wrong:
        parse_viewing_key(UFVK_TEST, expected_network="main")
    assert wrong.value.code == "viewing_key_wrong_network"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (SEED_PHRASE, "seed_phrase_refused"),
        (SEED_PHRASE + " " + SEED_PHRASE, "seed_phrase_refused"),
        (SPENDING_KEY, "spending_key_refused"),
        ("xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvvNKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi", "spending_key_refused"),
        ("a" * 64, "spending_key_refused"),
        ("uivktest1" + "q" * 80, "incoming_viewing_key_unsupported"),
        ("uview1" + "Q" * 40 + "q" * 40, "viewing_key_malformed"),
        ("uview1" + "b" * 80, "viewing_key_malformed"),  # 'b' is outside the bech32 alphabet
        ("uview1short", "viewing_key_malformed"),
        ("", "viewing_key_missing"),
    ],
)
def test_anything_but_a_viewing_key_is_refused_without_echo(raw: str, code: str) -> None:
    with pytest.raises(ZcashKeyRefused) as refused:
        parse_viewing_key(raw, expected_network="main")
    assert refused.value.code == code
    if raw:
        assert raw not in str(refused.value)


def test_the_viewing_key_round_trips_through_the_keychain(memory_keyring) -> None:
    keys.store_viewing_key(parse_viewing_key(UFVK_TEST, expected_network="test"))
    assert memory_keyring.items == {(keys.KEYRING_SERVICE, "ufvk-test"): UFVK_TEST}
    assert keys.load_viewing_key("test").encoded == UFVK_TEST
    assert keys.load_viewing_key("main") is None


def test_a_failing_keychain_refuses_and_is_never_read_as_no_key(monkeypatch) -> None:
    import keyring

    backend = MemoryKeyring(fail=True)
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    with pytest.raises(ZcashKeyRefused) as write:
        keys.store_viewing_key(parse_viewing_key(UFVK_TEST, expected_network="test"))
    assert write.value.code == "keychain_unavailable"
    with pytest.raises(ZcashKeyRefused) as read:
        keys.load_viewing_key("test")
    assert read.value.code == "keychain_unavailable"
