"""Copy-truth pins for the served chat page (TDL B5 inherited gate).

The First-Run Pact's trust story dies if the surrounding page lies, so the three
inherited claims are pinned here mechanically:

1. the empty-state fun-facts never claim an on-device image generation that does not exist;
2. the receipts panel states the chain truth (valid over the retained window, not "complete history");
3. the settings budget line stays honest (a guide, nothing enforces it yet).
"""
from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import script

BANNED_PAGE_CLAIMS = (
    "Generates images on-device",
    "nothing uploaded",
)


def _html() -> str:
    return render_vool_chat_html()


def test_the_empty_state_facts_never_claim_a_capability_the_build_does_not_ship():
    for banned in BANNED_PAGE_CLAIMS:
        assert banned not in _html(), (
            f"the empty-state fun-facts claim '{banned}' — the build does not generate "
            "images on-device; the page may not advertise what it cannot do"
        )


def test_the_receipts_panel_states_the_chain_truth_over_the_retained_window_never_completeness():
    assert "This does not prove the history is complete" in _html(), (
        "the receipts panel must carry the retained-window honesty line"
    )
    assert "This does not prove the answer is correct" in _html()
    for overclaim in ("evidence history is intact", "complete history is proven", "chain is complete"):
        assert overclaim not in _html(), f"receipts panel overclaim regressed: {overclaim}"


def test_the_settings_budget_line_stays_a_guide_and_admits_nothing_enforces_it():
    assert "Daily cloud token budget" in _html()
    assert "nothing enforces it yet" in _html(), (
        "the settings budget line must keep admitting the budget is not enforced"
    )


def test_the_pact_owned_copy_never_enters_the_page_as_a_hidden_second_source():
    # The pact card's copy lives in ONE Python dict (single copy-managed surface);
    # this page-level pin only asserts the page script never hardcodes the pact's
    # strong egress sentence outside the tiered renderer.
    strong = "Nothing leaves this Mac while this is on"
    assert script().count(strong) <= 1, (
        "the strong egress sentence must appear only in the tier renderer, never hardcoded elsewhere"
    )
