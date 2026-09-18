"""Contact identity comparison: same-name, UTS #39 confusables, unsafe characters, scripts and deleted-name tombstones.

What executes: the production identity owner (core.contacts.identity) over the committed UTS #39 skeleton table generated
from the system ICU (core/contacts/data/uts39_identity.json), and the production device-secret derivation for tombstone
hashes. Deterministic; no model, network or provider.
"""
from __future__ import annotations

import unicodedata

import pytest

from core.contacts import identity


def _same(a: str, b: str) -> str | None:
    return identity.compare(identity.identity_keys(a), identity.identity_keys(b))


@pytest.mark.parametrize("a,b", [
    ("TOM", "ＴＯＭ"),           # full-width to ASCII (NFKC): the same name
    ("TOM", "tom"),             # case: the same name
    ("Zoë Ng", "Zoë  Ng"),      # collapsed whitespace: the same name
    ("Straße", "STRASSE"),      # case folding expands ß: the same name
])
def test_same_name_is_recognised_across_normalisation(a: str, b: str) -> None:
    assert _same(a, b) == identity.MATCH_SAME


@pytest.mark.parametrize("a,b", [
    ("TOM", "T0M"),                        # the digit zero for the letter O
    ("TOM", "ТОМ"),         # Cyrillic ТОМ
    ("Bill", "BiII"),                      # capital I for lowercase l
    ("Alex", "Alex‍"),                # a trailing joiner is ignored, so it is not even different -> same
    ("paypal", "paypaI"),                  # a capital I tail
])
def test_confusables_are_recognised(a: str, b: str) -> None:
    assert _same(a, b) in (identity.MATCH_CONFUSABLE, identity.MATCH_SAME)


@pytest.mark.parametrize("a,b", [
    ("Priya Nair", "Alex Chen"),
    ("Tom", "Tomas"),
    ("Zoe", "Chloe"),
    ("李小龍", "王小明"),
])
def test_distinct_names_do_not_collide(a: str, b: str) -> None:
    assert _same(a, b) is None


def test_legitimate_international_names_are_not_confusables_of_each_other() -> None:
    # two different real names that share no skeleton
    assert _same("Ольга Петрова", "Игорь Смирнов") is None
    assert _same("María José", "Ana Sofía") is None
    # an accented name and its ASCII fold are only SIMILAR (a fuzzy warning), never the same identity
    from core.contacts import names

    assert _same("Zoë", "Zoe") is None
    assert names.fuzzy_score("Zoe", display_name="Zoë") >= identity.SIMILAR_THRESHOLD


@pytest.mark.parametrize("name,code", [
    ("TOM‮", "U+202E"),      # right-to-left override
    ("T​OM", "U+200B"),      # zero-width space
    ("A" + chr(0) + "B", "U+0000"),  # null
    ("Alex⁦Chen", "U+2066"),  # left-to-right isolate
])
def test_unsafe_characters_are_named_and_refused(name: str, code: str) -> None:
    problems = identity.unsafe_characters(name)
    assert problems and problems[0]["code_point"] == code
    with pytest.raises(identity.IdentityError) as refused:
        identity.require_safe_name(name)
    assert refused.value.reason == "unsafe_characters" and code in refused.value.message


@pytest.mark.parametrize("name", ["Zoë", "李小龍", "Ольга", "Zoe‍Ng", "José", "علي"])
def test_ordinary_names_including_joiners_and_non_latin_are_safe(name: str) -> None:
    identity.require_safe_name(name)  # does not raise


def test_mixed_script_is_flagged_but_restrictive_combinations_are_not() -> None:
    assert identity.mixed_script("AlexА")  # Latin + Cyrillic
    assert not identity.mixed_script("李小龍")  # Han alone
    assert not identity.mixed_script("東京タワー")  # Han + Katakana is a UTS #39 highly-restrictive combination
    assert identity.describe("AlexА")["scripts"] == ["Cyrl", "Latn"]


def test_describe_shows_the_exact_characters_that_tell_lookalikes_apart() -> None:
    tom = identity.describe("TOM")
    t0m = identity.describe("T0M")
    cyr = identity.describe("ТОМ")
    assert tom["visible_characters"] == "TOM" and "U+0030" in " ".join(t0m["code_points"])  # the zero
    assert cyr["scripts"] == ["Cyrl"] and any("CYRILLIC" in cp for cp in cyr["code_points"])
    assert tom["display"] == "TOM" and identity.describe("A​B")["visible_characters"] == "A⟨U+200B⟩B"


def test_the_policy_version_names_the_icu_and_unicode_versions() -> None:
    version = identity.policy_version()
    assert version.startswith("contacts-identity-1/uts39-icu") and "unicode" in version


def test_lookalike_of_matches_names_aliases_and_name_tokens() -> None:
    assert identity.lookalike_of("T0M", display_name="TOM") == identity.MATCH_CONFUSABLE
    assert identity.lookalike_of("T0M", display_name="Priya", aliases=["TOM"]) == identity.MATCH_CONFUSABLE
    assert identity.lookalike_of("A1ex", display_name="Alex Chen") == identity.MATCH_CONFUSABLE  # one word of the name
    assert identity.lookalike_of("Priya", display_name="Alex Chen") is None


def test_tombstone_hashes_recognise_a_deleted_name_without_keeping_it() -> None:
    hashes = identity.tombstone_hashes(["TOM", "Tommy"])
    assert hashes and all(len(h) == 64 for h in hashes)
    # the readable name is nowhere in the hashes
    assert "tom" not in "".join(hashes).lower()
    # a same or confusable name's own hashes intersect the stored set; an unrelated name's do not
    for lookalike in ("TOM", "T0M", "ТОМ"):
        keys = identity.identity_keys(lookalike)
        tagged = identity.tombstone_hashes_for_keys(keys)
        assert set(tagged["same"] + tagged["confusable"]) & set(hashes), lookalike
    unrelated = identity.tombstone_hashes_for_keys(identity.identity_keys("Priya Nair"))
    assert not (set(unrelated["same"] + unrelated["confusable"]) & set(hashes))


def test_the_skeleton_law_is_nfd_of_the_prototype_map() -> None:
    # skeleton(x) = NFD(map(NFD(x))): the ICU prototype map is applied, so a confusable letter is folded
    # ("m" and "rn" are confusable, so skeleton("tom") == "torn") and the result is in NFD (idempotent)
    for name in ("tom", "T0M", "Zoë", "ТОМ"):
        skel = identity.skeleton(name)
        assert skel == unicodedata.normalize("NFD", skel), name
    assert identity.skeleton("tom") == identity.skeleton("torn"), "the ICU map folds the m/rn confusable"
    # the digit zero has the letter O as its confusable skeleton, so T0M and TOM share a skeleton
    keys_tom, keys_t0m = identity.identity_keys("TOM"), identity.identity_keys("T0M")
    assert keys_tom.skeletons() & keys_t0m.skeletons()
