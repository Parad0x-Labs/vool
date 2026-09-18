from __future__ import annotations

from core.agent_runtime.voolbook import classify_voolbook_intent


def _c(text: str) -> str | None:
    return classify_voolbook_intent(text.lower())


def test_word_ending_in_x_does_not_trigger_twitter_write() -> None:
    # Regression: the bare "x" alternative had no leading word boundary, so "sales tax is added"
    # matched (x in "tax" + following "is") and the fast path WROTE @added to the profile.
    for text in (
        "a $50 item is discounted 20%, then 8% sales tax is added to the discounted price",
        "the fax is on the desk",
        "the box is empty",
        "the max is 100",
    ):
        assert _c(text) != "twitter", text


def test_real_twitter_commands_still_classify() -> None:
    assert _c("set my twitter handle to vool") == "twitter"
    assert _c("update my x handle: parad0x") == "twitter"
    assert _c("my twitter is @sls") == "twitter"
    assert _c("my x is sls_0x") == "twitter"
