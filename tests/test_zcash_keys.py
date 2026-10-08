"""The Zcash watch lane takes a viewing key and nothing that can spend: refusals store nothing and echo nothing.

A key is accepted only after it decodes (bech32m, ZIP 316 F4Jumble, padding, items), and it is kept by VOOL's
credential store, read back before a save is reported."""
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
# Real-shaped keys whose bech32 body happens to contain "tprv"/"xprv": still viewing keys.
UFVK_MAIN_WITH_TPRV = (
    "uview14p8y5yc4t7wny4lypvp4p5870fq59ftgq09f706frgccuchethu7dr8w9jwmah6we505hdg4rqjtwtprvasz224ucqqa0565hdfy4fxcfw8237nhxh"
    "04jtcmn69h8k5vqnu8vdgeendg0ykyuf7rd7egrvl7me5ezh6pvj8x0ayqhekzxlk9fg0n2chlcdfk4wkp487rctxrda34zuchwh6c5f2h4486ct6zyl6mue"
    "qxp3wn8nep7a84ajegtty9q2yaf0fujy2s4shayk5kakrgc948khx7zxm2smdjf4xem8cny72lhckpm79m0gtlr60czfxkkhzmygth6s7yqea6dlzze392ld"
    "v2x0z2h060nh384akxlp9nkr4t8tdl6j90l40ql4nnevdp4tgf7h7kcnfaewvrru5qm7vuzvzgwyxgactgse47qq57uhh4uecqdp4e2dd0xm30lgw4au6er"
    "t3l486rzfgpjgma3nd0q68qkyvxsfn4"
)
UFVK_TEST_WITH_XPRV = (
    "uviewtest1ud2q9l7epkhhk7f2t2eeg70k9e7zu5wkqcug4wpvk0xxquy07vsfqzna779t9x36rqk6592c4jrew43znlykm86wwptwpeheafhxr9l32cdc"
    "x6cv9jfdvxjcx3l43wp3chfc2z7e3psd0rzk0w4cqhvcdxncc72d9eydyxfka79x3jau7hye0skgwyauwkax9d8msr2cujk5s6pr3natwww26qyh02rat6u"
    "6jhw3t44eyfw845d5wmctvvvh8xhw9k9qwv97ku4qxa83t5ygy987m758xp4td9e3fpa9tfa6crarn0nd75k6y9qfs9fkujw72mmuxx2ah8qjnewz0rx3agl"
    "7vk4yfpmgrysyns3lap4eks6mx4neucs47adn5lykgqspvenh9hgl0rl7yx4g5tc5z3sxvy3y5vawen0gmrxnn6aaw9evgf7z32eplcdkxprvprata0h570"
    "h3k0cl2d5au8cv5asdz67vzs9asad35qhn9936"
)
#: A second testnet UFVK (another throwaway seed), for "a different key".
UFVK_TEST_OTHER = (
    "uviewtest1p8r4uyu9y9kk2fmymcn95an9ykxyhx5n7zp9twd5p5r9lyyjn7t6qu8awm78j53ckc0c4hasraqz0qa5wu8vmwefapz25chnep860dag45fvk"
    "uec9h2r6vkfnvewt0ue7h7yugj8frhmacvr8ehmfvyfds8ffghsnxcq5fxc40ercl9wccvz3gnyt4px9dyyl0en4yx42kszl269llcsuxgd0rflpsaed325k"
    "w2wqetyqkkhtdrk9q5a520nvqdnhrjl4ya5yysd3yu46vg8wmhydshplxjdn32f6xvf25cs2ez9ja33uf7er8q0xhrdyvcks3d7h6sm7v755ej5xm988xg5wa"
    "ag4l20rxchfm6njsmnzg5xhqd6vr69tgjmrevlfhquwnxcrts8nn2l0yvu7cc5pg9wqk9c785schcyw22ka2vudv0t7t39dukhkk7vt0xyttd4qvwurwh9cd0"
    "yrtqvqtyfzjwa2yz3yx20f0kg2g0rjx0r"
)
SPENDING_KEY = "secret-extended-key-main1qd3k8v0ezqqqqpq9h0kxw6tdlu0vrv0xyzexampleexampleexampleexampleexampleexample"


def isolate_credential_vault(monkeypatch, root) -> None:
    """Point the credential vault at a per-test folder. The vault key is derived per test (the suite gives every
    test its own node key) while the runtime home is shared, so an entry another test wrote would not decrypt."""
    import core.credential_store as store

    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "_store_path", lambda: root / "credentials.enc.json")
    monkeypatch.setattr(store, "_meta_path", lambda: root / "credentials.meta.json")


@pytest.fixture(autouse=True)
def _own_credential_vault(tmp_path, monkeypatch):
    isolate_credential_vault(monkeypatch, tmp_path / "vault")


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
        ("uview1" + "q" * 120, "viewing_key_malformed"),  # the right shape, but no valid checksum
        (UFVK_MAIN[:-1] + ("q" if UFVK_MAIN[-1] != "q" else "p"), "viewing_key_malformed"),  # one character off
        ("zxviews1" + "q" * 120, "viewing_key_unsupported"),
        ("secret-extended-key-main1" + "q" * 120, "spending_key_refused"),
        ("secret-spending-key-main1" + "q" * 120, "spending_key_refused"),
        ("tprv8ZgxMBicQKsPd7Uf69XL1XwhmjHopUGep8GuEiJDZmbQz6o58LninorQAfcKZWARbtRtfnLcJ5MQ2AtHcQJCCRUcMRvmDUjyEmNUWwx8UbK", "spending_key_refused"),
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


def test_a_real_key_whose_body_contains_a_bip32_marker_is_still_a_viewing_key() -> None:
    assert "tprv" in UFVK_MAIN_WITH_TPRV and "xprv" in UFVK_TEST_WITH_XPRV
    assert parse_viewing_key(UFVK_MAIN_WITH_TPRV, expected_network="main").encoded == UFVK_MAIN_WITH_TPRV
    assert parse_viewing_key(UFVK_TEST_WITH_XPRV, expected_network="test").encoded == UFVK_TEST_WITH_XPRV


def test_zcash_key_shapes_are_masked_by_the_shared_redactor() -> None:
    from core.secret_redaction import contains_secret, redact_secrets

    parse_viewing_key(UFVK_TEST, expected_network="test")
    assert UFVK_TEST not in redact_secrets(f"my viewing key is {UFVK_TEST}")
    for shape in ("secret-extended-key-main1" + "q" * 80, "uview1" + "q" * 80, "uivktest1" + "q" * 80, "zxviews1" + "q" * 80,
                  "secret-spending-key-main1" + "q" * 80, "secret-extended-key-regtest1" + "q" * 80, "uviewregtest1" + "q" * 80,
                  "zviews1" + "q" * 80, "zviewtestsapling1" + "q" * 80, "zivks1" + "q" * 80, "zivktestsapling1" + "q" * 80):
        assert shape not in redact_secrets(f"key {shape} here") and contains_secret(shape), shape
        for glued in (f'{{"k": "key:\\n{shape}"}}', f"fvk_{shape}", f"x{shape.upper()}"):  # escaped newline in JSON, "_", a letter
            assert shape[-40:] not in redact_secrets(glued).lower(), glued[:40]
    address = "utest1rfz5kscxeqmr73uq0ujm4j60jc49fvcjatcy5p2485t5tpzzh3c5uxqvttqukr6yp0ryvfj7sar4um4skuqsdgt8nkprkr82y5865z5j"
    uri = f"pay to zcash:{address}?amount=0.05"
    assert redact_secrets(uri) == uri  # a payment address is not a key


def test_the_viewing_key_round_trips_through_the_credential_store() -> None:
    from core.credential_store import get_credential

    keys.store_viewing_key(parse_viewing_key(UFVK_TEST, expected_network="test"))
    assert get_credential(keys.credential_name("test")) == UFVK_TEST
    assert keys.load_viewing_key("test").encoded == UFVK_TEST
    assert keys.load_viewing_key("main") is None


def test_a_store_that_keeps_nothing_or_fails_refuses_and_is_never_read_as_no_key(monkeypatch) -> None:
    import core.credential_store as store

    key = parse_viewing_key(UFVK_TEST, expected_network="test")
    monkeypatch.setattr(store, "store_credential", lambda name, value, label="": None)  # a backend that drops writes
    with pytest.raises(ZcashKeyRefused) as dropped:
        keys.store_viewing_key(key)
    assert dropped.value.code == "keychain_unavailable"

    def denied(*args, **kwargs):
        raise RuntimeError(f"keychain denied for {args}")

    monkeypatch.setattr(store, "store_credential", denied)
    with pytest.raises(ZcashKeyRefused) as write:
        keys.store_viewing_key(key)
    assert write.value.code == "keychain_unavailable" and write.value.__cause__ is None and UFVK_TEST not in str(write.value)
    monkeypatch.setattr(store, "get_credential", denied)
    with pytest.raises(ZcashKeyRefused) as read:
        keys.load_viewing_key("test")
    assert read.value.code == "keychain_unavailable" and read.value.__cause__ is None


def test_without_the_operators_keychain_grant_the_key_goes_to_the_vault_not_the_keychain(monkeypatch) -> None:
    import keyring

    import core.credential_store as store

    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "auto")
    monkeypatch.setenv("VOOL_KEYCHAIN_ALLOWED", "0")
    writes: list[str] = []
    monkeypatch.setattr(keyring, "set_password", lambda service, user, value: writes.append(user))
    keys.store_viewing_key(parse_viewing_key(UFVK_TEST, expected_network="test"))
    assert writes == [] and store._active_keyring(write=True) is None
    assert keys.load_viewing_key("test").encoded == UFVK_TEST
