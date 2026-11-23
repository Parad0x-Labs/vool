"""A chat app (Telegram, Discord) reads the approval lines in the language the person wrote in.

Trust-features audit of 900c7bc19: "This needs your OK on your computer ...", "You said no on your computer ..." and
"That approval expired ..." were English literals beside a reply and receipts in the person's language, and an
approval the store no longer held (cleared by a restart) was told it had expired. The lines are catalog words now,
picked by the language the message itself shows, and a missing approval is said to be missing.
"""
from __future__ import annotations

import pytest

from core.i18n.catalog import catalog_for
from core.mobile_companion.bot_runtime import _locale_of, approval_ended_words, approval_words

MESSAGES = (
    ("lt", "Primink man šeštadienį 10 val. išnešti šiukšles į konteinerį prie namų"),
    ("ru", "Напомни мне в субботу в десять вынести мусор"),
    ("ja", "土曜日の十時にゴミを出すことを思い出させて"),
    ("en", "remind me on saturday at ten to take the bins out please"),
)


@pytest.mark.parametrize("locale,message", MESSAGES)
def test_the_approval_line_is_in_the_message_language(locale, message) -> None:
    assert _locale_of(message) == locale
    said = approval_words({"intent": "operator.schedule_reminder", "action_plain": "set a reminder"}, _locale_of(message))
    expected = catalog_for(locale).text("bot.approval_needed_what").replace("{what}", "set a reminder")
    assert said == expected
    if locale != "en":
        assert said != catalog_for("en").text("bot.approval_needed_what").replace("{what}", "set a reminder")


@pytest.mark.parametrize("state,key", [("denied", "bot.approval_denied"), ("expired", "bot.approval_expired"),
                                       ("", "bot.approval_unknown")])
def test_an_ended_approval_is_said_as_what_happened(state, key) -> None:
    assert approval_ended_words(state, "lt") == catalog_for("lt").text(key)
    assert approval_ended_words(state, "en") == catalog_for("en").text(key)


def test_no_language_shown_is_english() -> None:
    assert _locale_of("ok") == "en"
    assert approval_words({}, "en") == catalog_for("en").text("bot.approval_needed")
