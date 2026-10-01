"""Model Radar in the REAL chat shell — fragment presence, page hooks, served page.

String-level structural assertions on the production page (the repo's own UI-fragment
test pattern): the chip exists in the top-right cluster, the card carries every
contract field, the actions route through the page's authorities (never a bypass),
and the served /chat document actually contains the surface.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def page_html() -> str:
    from core.vool_chat_page import render_vool_chat_html

    return render_vool_chat_html(build_commit="radar-test")


@pytest.fixture(scope="module")
def fragment() -> str:
    from core.model_radar_fragment import render_model_radar_fragment

    return render_model_radar_fragment()


def test_fragment_is_registered_and_mounted(page_html, fragment) -> None:
    assert "discoverModelsBtn" in page_html and "VoolModelRadar" in page_html, "the radar must ship in the real chat shell"
    assert fragment in page_html, "rendered via _page_fragments, not duplicated"


def test_discovery_is_in_model_selector_without_second_header_badge(fragment) -> None:
    assert "document.getElementById('discoverModelsBtn')" in fragment
    assert "mrChip" not in fragment and "mrBadge" not in fragment
    assert "insertBefore(chip, anchor)" not in fragment


def test_card_carries_the_full_contract(fragment) -> None:
    for needle in (
        "input_usd_per_m", "output_usd_per_m", "cached_input_usd_per_m",  # per-component prices
        "price no longer published",                                       # honest unknown
        "ends ",                                                           # expiry display
        "ctx", "tools ", "images ",                                        # context + capabilities
        "data terms not stated by the provider",                           # privacy honesty
        "evidence ", "source",                                             # evidence timestamp + URL
        "f.why",                                                           # why it qualified
        "Try once", "Set as default", "Hide offer",                           # actions
        "Providers to watch", "Lane", "tool support", "image support",     # preferences
        "as it happens", "at most daily per model", "at most weekly per model",
    ):
        assert needle in fragment, f"card/prefs contract missing: {needle!r}"
    # Per-component reductions render separately; a blended average is never computed.
    assert "mr-pct" in fragment and "reductions" in fragment
    assert "average" not in fragment.lower().replace("never averaged", "")


def test_actions_never_bypass_the_existing_gates(fragment) -> None:
    # No direct model mutation from the fragment: pinning goes through the page's own
    # A11-gated switch (pageActions), and the only fragment POSTs are radar-state ones.
    assert "/api/cloud/model" not in fragment, "the fragment must not switch models itself"
    for path in ("/api/model-radar/dismiss", "/api/model-radar/try-once", "/api/model-radar/preferences", "/api/model-radar/viewed"):
        assert path in fragment
    assert "p.pinCloudModel" in fragment and "p.tryModelOnce" in fragment
    assert "No auto-switching, ever" in fragment


def test_page_try_once_hooks_exist_and_are_bounded(page_html) -> None:
    assert page_html.count("let tryOnceGrant = null;") == 1
    assert page_html.count("function armTryOnce(") == 1
    # The trial is checked FIRST in effectiveModel and consumed by exactly one send.
    assert "const trial = activeTryOnce(chatId, msgText);" in page_html
    assert "if (trialServing) consumeTryOnce(chatId);" in page_html
    # A grant never crosses chats, never rides a trivial turn, and reports sticky.
    assert "a grant never crosses chats" in page_html
    # The trial arm is part of the model-selection verdict. (The pin drifted when the
    # expression gained the queuedModel arm — a pre-existing base failure, repaired here to
    # keep the original meaning: the try-once grant reads as 'sticky'.)
    assert "trialServing ? 'sticky'" in page_html
    assert page_html.count("const modelSelection = ") == 1
    # Page actions exposed for the fragment.
    assert "tryModelOnce: (model, label) => armTryOnce(displayedChat, model, label)" in page_html
    assert "pinCloudModel: (id, label) => switchCloudModel(" in page_html


def test_served_chat_document_contains_the_radar() -> None:
    """The production GET /chat route serves the page with the surface mounted."""
    import json

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    res = dispatch_get(
        path="/chat", query={}, runtime=RuntimeServices(display_name="N"),
        model_name="vool", client_host="127.0.0.1",
    )
    assert res.status == 200
    body = res.body.decode("utf-8")
    assert "discoverModelsBtn" in body and "VoolModelRadar" in body and "Discover models" in body
    # sanity: the response is the real page, not an error document
    assert json.dumps("VOOL") .strip('"') in body


def test_discovery_is_not_bound_to_static_model_pin_handler() -> None:
    from tests.chat_page_js_harness import DOM, run_node, script

    drive = r"""
const discover = document.getElementById('discoverModelsBtn');
const auto = document.createElement('button'); auto.setAttribute('data-model','vool');
document.querySelectorAll = function(selector){
  if(selector.startsWith('#modelPop .pop-item')) return selector.includes('[data-model]') ? [auto] : [auto,discover];
  return [];
};
initComposerControls();
modelValue = 'vool-local-only';
if(discover.__on && discover.__on.click) discover.__on.click({stopPropagation(){}});
out({discoveryHasPinHandler:!!(discover.__on && discover.__on.click),model:modelValue,autoHasPinHandler:!!auto.__on.click});
"""
    control = run_node(DOM + script().replace(".pop-item[data-model]:not(.cloud-dyn)", ".pop-item:not(.cloud-dyn)") + drive)
    assert not control["errors"], control
    assert control["discoveryHasPinHandler"] and control["model"] != "vool-local-only"
    result = run_node(DOM + script() + drive)
    assert not result["errors"], result
    assert result["autoHasPinHandler"]
    assert not result["discoveryHasPinHandler"] and result["model"] == "vool-local-only"
