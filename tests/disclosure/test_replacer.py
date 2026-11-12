from __future__ import annotations

import pytest

from core.disclosure.replacer import replace_texts, replace_using_contacts


def test_all_supported_details_are_removed_and_restored_together():
    values = (
        "Mira Example", "mira@example.invalid", "+44 7700 900123",
        "GB82 WEST 1234 5698 7654 32", "4111 1111 1111 1111", "42 Example Street",
    )
    text = "Contact " + "; ".join(values) + "."
    prepared = replace_texts([text], contact_names=[values[0]])
    assert all(value not in prepared.texts[0] for value in values)
    assert {item.placeholder.split()[0] for item in prepared.replacements} == {
        "[Person", "[Email", "[Phone", "[Bank", "[Card", "[Address",
    }
    assert prepared.restore(prepared.texts[0]) == text
    assert "Best effort" in prepared.warnings[0]
    assert "Mira Example" not in repr(prepared)


def test_sections_share_mapping_and_literal_placeholders_are_not_overwritten():
    sections = ["Literal [Person 1]; Mira Example", "Mira Example and [Email 1] mira@example.invalid"]
    prepared = replace_texts(sections, contact_names=["Mira Example"])
    assert prepared.texts == (
        "Literal [Person 1]; [Person 2]", "[Person 2] and [Email 1] [Email 2]",
    )
    assert tuple(prepared.restore(s) for s in prepared.texts) == tuple(sections)
    assert prepared.restore("Unknown [Person 99]") == "Unknown [Person 99]"


def test_contact_names_use_word_boundaries_unicode_and_longest_match():
    text = "Živilė Example and Živilė; miniAnn and Ann. ANN."
    prepared = replace_texts([text], contact_names=["Živilė", "Živilė Example", "Ann"])
    assert "miniAnn" in prepared.texts[0]
    assert "Živilė" not in prepared.texts[0]
    assert len(prepared.replacements) == 4
    assert prepared.restore(prepared.texts[0]) == text


def test_numbers_inside_addresses_and_email_are_not_split():
    text = "123 Long Example Road; user1234567@example.invalid; 2026-10-20; 3.14159"
    prepared = replace_texts([text], contact_names=["Long Example", "user1234567"])
    assert prepared.texts == ("[Address 1]; [Email 1]; 2026-10-20; 3.14159",)
    assert prepared.restore(prepared.texts[0]) == text


def test_unrecognized_formats_stay_visible_with_review_warning():
    text = "Flat blue, Example estate; mira (at) example (dot) invalid; 1234567890123456"
    prepared = replace_texts([text])
    assert prepared.texts == (text,)
    assert prepared.warnings == (
        "Best effort: check the preview. Unrecognized details may still be present.",
    )


@pytest.mark.parametrize("text", [
    "Email mira@example.invalid.", "Write to (mira@example.invalid).",
    "mira+notes@example.invalid, please.", "mira@example.invalid!",
])
def test_email_sentence_punctuation_does_not_leave_the_address(text):
    prepared = replace_texts([text])
    assert "@" not in prepared.texts[0]
    assert prepared.restore(prepared.texts[0]) == text


@pytest.mark.parametrize("account", [
    "gb82 west 1234 5698 7654 32", "DE89370400440532013000", "DE89 3704 0044 0532 0130 00",
])
def test_account_checksum_handles_case_grouping_and_following_prose(account):
    text = account + " NEXT item"
    prepared = replace_texts([text])
    assert prepared.texts == ("[Bank account 1] NEXT item",)
    assert prepared.restore(prepared.texts[0]) == text


def test_unknown_account_checksum_is_left_intact_for_review():
    text = "GB00 WEST 1234 5698 7654 32"
    prepared = replace_texts([text])
    assert prepared.texts == (text,)
    assert prepared.warnings


def test_street_first_address_and_original_punctuation():
    text = "Example Street 42B, then 8 Other Lane."
    prepared = replace_texts([text])
    assert prepared.texts == ("[Address 1], then [Address 2].",)
    assert prepared.restore(prepared.texts[0]) == text


def test_restoration_is_one_pass_and_does_not_interpret_replacement_values():
    prepared = replace_texts(["[Person 1] Example and Mira"], contact_names=["[Person 1] Example", "Mira"])
    # The original literal token is reserved even when it appears within a name.
    assert prepared.restore(prepared.texts[0]) == "[Person 1] Example and Mira"
    assert prepared.restore("[Person 1]") == "[Person 1]"


def test_mapping_is_scoped_to_one_preparation():
    first = replace_texts(["Mira"], contact_names=["Mira"])
    second = replace_texts(["Taro"], contact_names=["Taro"])
    assert first.restore("[Person 1]") == "Mira"
    assert second.restore("[Person 1]") == "Taro"
    assert replace_texts([]).replacements == ()


@pytest.mark.parametrize("bad", ["a single string", [None], [b"bytes"], [12], iter(["a"]), None])
def test_invalid_sections_are_refused(bad):
    with pytest.raises(TypeError):
        replace_texts(bad)


@pytest.mark.parametrize("bad", ["a name", [None]])
def test_invalid_contact_list_is_refused(bad):
    with pytest.raises(TypeError):
        replace_texts(["Mira"], contact_names=bad)
    with pytest.raises(TypeError):
        replace_texts(["Mira"], service_names=bad)


def test_active_local_contacts_supply_names_and_aliases(tmp_path, monkeypatch):
    # Real contacts store, private home; no fixture replaces the contacts API.
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core.contacts import store

    store.create_contact(display_name="Mira Example", aliases=["Miri"], actor=store.ACTOR_OWNER)
    store.create_contact(display_name="Example Company", kind="service", actor=store.ACTOR_OWNER)
    prepared = replace_using_contacts(["Mira Example (Miri) at Example Company"])
    assert prepared.texts == ("[Person 1] ([Person 2]) at [Service 1]",)
    assert prepared.restore(prepared.texts[0]) == "Mira Example (Miri) at Example Company"
