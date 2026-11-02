"""The 0.7.2 approval, money and consent surfaces stay VOOL's own inside every skin.

Contributor: sls_0x. The 0.7.2 code added surfaces the Toybox rescue base never had:
the private-chat markers, the X action approvals, the in-app confirm dialog and the
Web lookups switch. Each one either sits inside the trusted islands (so no themed rule
reaches it and VOOL's standard tokens are restored on it) or, like the update chip,
keeps its meaning colours through the pinned rules that follow the themed ones. The
price gate and the wallet sheets were islands already; that is proven here too.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

# Surfaces 0.7.2 added after the rescue base. Each selector is an island root: the
# element itself and everything inside it keep VOOL's standard presentation.
NEW_072_ISLANDS = (
    ".msg.is-private",  # a turn that is not being saved, user or assistant side
    ".private-divider",  # the quiet line where saving starts again
    ".private-note",  # the centred note about the private stretch
    ".private-banner",  # the composer banner with the end-private button
    ".x-action-card",  # a pending X action with its Approve and send / Decline buttons
    "#confirmPromptDialog",  # the in-app yes/no confirm used before destructive acts
    '[data-row="web_lookups"]',  # the Web lookups consent switch and its row
)
# The activity rows that carry a paid call's money receipt (x402 amounts, wallets, network and
# DNA service fees): receipts are trusted money surfaces.
RECEIPT_ISLANDS = (".xp-receipt",)
# Islands the rescue base already had; the 0.7.2 payment confirmations (price gate,
# wallet channels and review sheets) live inside these.
ALREADY_ISLANDS = ("#vgOverlay", "#vwSec", "#vwCx", ".vw-sheet", "#vwHomeOverlay")

BUILT_IN_IDS = ["voxel-workshop", "forest-relic", "neon-courier", "sakura-skies", "xeno-abyss"]
EDGE_PALETTES = (
    dict(background="#000000", panel="#0a0a0a", field="#000000", ink="#ffffff", muted="#c8c8c8",
         accent="#ffffff", accentInk="#000000", border="#ffffff"),
    dict(background="#ffffff", panel="#fefefe", field="#ffffff", ink="#000000", muted="#6f6f6f",
         accent="#7000ff", accentInk="#ffffff", border="#4d4d4d"),
)


def _manifest(identifier):
    from core.toybox import EXAMPLES

    return deepcopy(next(e for e in EXAMPLES if e["id"] == identifier))


def _variants():
    """The five reviewed skins plus two extreme palettes on the newest contract."""
    out = [(i, _manifest(i)) for i in BUILT_IN_IDS]
    light = _manifest("voxel-workshop")
    light.update(renderer_contract="1.2")
    light["skin"].update(frame="capsule", ornament="sakura", palette=EDGE_PALETTES[1])
    out.append(("edge-light", light))
    dark = _manifest("xeno-abyss")
    dark["skin"].update(frame="chamfer", ornament="xeno", palette=EDGE_PALETTES[0])
    out.append(("edge-dark", dark))
    return out


def _css(manifest):
    from core.toybox import validate
    from core.toybox_fragment import skin_css

    return skin_css(validate(json.dumps(manifest))[0])


def _rules(css):
    """(subject, body) pairs for every top-level rule, @media blocks flattened."""
    rules, i = [], 0
    while i < len(css):
        start = css.find("{", i)
        if start < 0:
            break
        subject = css[i:start].strip()
        depth, j = 1, start + 1
        while j < len(css) and depth:
            depth += (css[j] == "{" and 1) or (css[j] == "}" and -1) or 0
            j += 1
        body = css[start + 1 : j - 1]
        if subject.startswith("@media"):
            inner, k = _rules(body)
            rules.extend(inner)
            i = j
            continue
        rules.append((subject, body))
        i = j
    return rules, i


def test_every_new_072_surface_is_a_trusted_island():
    from core.toybox_fragment import TRUSTED

    for surface in NEW_072_ISLANDS:
        assert surface in TRUSTED, surface


def test_the_072_payment_surfaces_were_already_islands():
    from core.toybox_fragment import TRUSTED

    for surface in ALREADY_ISLANDS:
        assert surface in TRUSTED, surface


def test_receipt_rows_are_trusted_islands():
    from core.toybox_fragment import TRUSTED

    for surface in RECEIPT_ISLANDS:
        assert surface in TRUSTED.split(","), surface


def _reset_subjects():
    from core.toybox_fragment import ISLAND_RESET

    return {subject for subject, _ in _rules(ISLAND_RESET)[0]}


@pytest.mark.parametrize("name", [name for name, _ in _variants()])
def test_no_themed_rule_can_reach_a_new_072_surface(name):
    from core.toybox_fragment import PAGE_TOKENS_SELECTOR

    css = _css(dict(_variants())[name])
    rules, _ = _rules(css)
    resets = _reset_subjects()
    for subject, _body in rules:
        if subject == PAGE_TOKENS_SELECTOR or subject in resets:
            continue
        # what the rule can match is the part before its guard; a new surface never appears there
        reachable = subject.split(":not(")[0]
        for surface in NEW_072_ISLANDS + RECEIPT_ISLANDS:
            assert surface not in reachable, subject


def _vool_tokens(theme):
    """VOOL's own page tokens for one Appearance theme, read from the palette the pages ship."""
    import re

    from core.ui_palette import DARK_PALETTE_CSS, LIGHT_ONLY_PALETTE_CSS

    def block(css):
        body = css[css.index("{") + 1 : css.index("}")]
        return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", re.sub(r"/\*.*?\*/", "", body, flags=re.S)))

    tokens = block(DARK_PALETTE_CSS)
    if theme == "light":
        tokens.update(block(LIGHT_ONLY_PALETTE_CSS))
    return tokens


@pytest.mark.parametrize("name", [name for name, _ in _variants()])
def test_the_island_reset_restores_vool_tokens_on_new_surfaces(name):
    """The islands get the pages' own palette back, for the theme the person chose in
    Settings > Appearance: dark by default, light for Light and for "Same as this computer"
    on a light system. Not a separate, older palette."""
    from core.toybox_fragment import TRUSTED

    css = _css(dict(_variants())[name])
    rules = dict(_rules(css)[0])
    island = ":is(" + TRUSTED + ")"
    for subject, theme in (
        (TRUSTED, "dark"),
        (':root[data-theme="light"] ' + island, "light"),
        (':root[data-theme="system"] ' + island, "light"),
    ):
        reset = rules.get(subject)
        assert reset is not None, subject
        vool = _vool_tokens(theme)
        for token in ("bg", "chat", "panel", "field", "user", "ink", "muted", "accent", "accent2", "border"):
            assert f"--{token}:{vool[token]};" in reset, (theme, token)
        # a token VOOL's palette leaves undefined is undefined again, so fallbacks apply as without a skin
        assert ("--bg2:initial" in reset) == ("bg2" not in vool), (theme, reset)
    assert '@media (prefers-color-scheme: light){:root[data-theme="system"] ' + island in css
    backing = ":where(" + island + ":not(" + island + " *)){background-color:var(--panel);color:var(--ink)}"
    assert backing in css
    resets = "".join(body for subject, body in _rules(css)[0] if subject in _reset_subjects())
    for stale in ("#5eead4", "#16191f", "#e8eaf0"):  # the palette VOOL used before its warm redesign
        assert stale not in resets, stale


@pytest.mark.parametrize("name", [name for name, _ in _variants()])
def test_the_update_chip_keeps_its_standard_state_colours(name):
    """The chip takes the themed button frame, but its ready/ok/warn cues are restated
    by the pinned rules, which follow every themed rule in the sheet."""
    css = _css(dict(_variants())[name])
    rules, _ = _rules(css)
    themed_chip = [i for i, (s, _) in enumerate(rules) if "#updateChip" in s and ".upd-" not in s]
    pinned = [i for i, (s, _) in enumerate(rules) if "#updateChip.upd-" in s]
    assert themed_chip and pinned and min(pinned) > max(themed_chip)
    pinned_bodies = " ".join(body for s, body in rules if "#updateChip.upd-" in s)
    assert "color:var(--ok)" in pinned_bodies  # upd-ok stays the standard ok colour
    # upd-warn stays the pages' own warn colour: 0.7.2 moved it from a fixed red to the calm --bad
    assert "border-color:var(--bad);color:var(--bad)" in pinned_bodies and "#f87171" not in pinned_bodies


def _page_rule(page, selector):
    """The declarations of the page's own rule for one selector, spaces removed."""
    start = page.index(selector + " {") + len(selector) + 2
    return page[start : page.index("}", start)].replace(" ", "").rstrip(";")


def test_pinned_meaning_colours_are_the_072_pages_own():
    """The pins restate what the pages draw without a skin. 0.7.2 changed three of them: the
    update warning uses --bad, the LOCAL lane uses the warm accent, and the run-status dots
    moved from the old cloud pill to the model button and its connections list."""
    from core.toybox_fragment import PINNED
    from core.vool_chat_page import render_vool_chat_html

    chat = render_vool_chat_html(build_commit="t", ui_locale="en")
    vool = _vool_tokens("dark")
    pins = {selector: body for selectors, body in PINNED for selector in selectors}
    assert pins["#updateChip.upd-warn"] == _page_rule(chat, "#updateChip.upd-warn")
    local = _page_rule(chat, ".model-lane.local").replace("var(--accent)", vool["accent"])
    assert pins[".model-lane.local"] == local.replace("var(--accent-soft)", f"rgba({vool['accent-rgb']},.12)")
    neutral = _page_rule(chat, "#modelBtn .dot, #modelConnections .dot")
    assert "background:var(--muted)" in neutral.replace(",#", ", #")
    for selector in ("#modelBtn .dot", "#modelConnections .dot"):
        assert pins[selector] == "background:" + vool["muted"]
    for state, token in (("ok", "--ok"), ("bad", "--bad"), ("warn", "--warn")):
        assert pins[f"#modelBtn .dot.{state}"] == f"background:var({token})"
        assert f"#modelBtn .dot.{state}, #modelConnections .dot.{state} {{ background:var({token}); }}" in chat
    # no pin names the cloud pill 0.7.2 removed
    assert 'id="cloudPill"' not in chat and not any("#cloudPill" in selector for selector in pins)


def test_the_served_pages_really_carry_the_new_surfaces():
    from core.vool_chat_page import render_vool_chat_html
    from core.vool_settings_page import render_vool_settings_html

    chat = render_vool_chat_html(build_commit="t", ui_locale="en")
    assert 'id="privateBanner"' in chat and "private-divider" in chat and "private-note" in chat
    assert "confirm-prompt" in chat and "#vgOverlay{" in chat
    settings = render_vool_settings_html(build_commit="t", ui_locale="en")
    assert "x-action-card" in settings and "x-action-approve" in settings
    assert '"id": "web_lookups"' in settings or '"id":"web_lookups"' in settings


def test_the_wallet_payment_channel_rows_cannot_be_themed():
    """The payment channel consent rows (.vw-cx-channel with their checkboxes) render
    inside #vwCx, an island: every themed rule excludes island descendants."""
    from core.toybox_fragment import GUARD

    assert "#vwCx" in GUARD and "*)" in GUARD


# A paid call's UsePod receipt as the ledger carries it: x402 payment, chain-confirmed outflow,
# network fee and the DNA service fee. Synthetic values only.
_RECEIPT = {
    "schema": "vool.usepod.receipt.v1", "transport_mode": "x402", "operation_id": "op-1",
    "x402": {"pay_to": "Treasury111", "payer_wallet": "Payer222", "network": "solana-mainnet", "amount_atomic": 1200,
             "payment_signature": "5sig", "transaction_state": "confirmed",
             "chain_confirmation": {"state": "recorded", "wallet_outflow_atomic": 1201, "network_fee_atomic": 5000}},
    "cost": {"unit": "usdc_microunit", "exact_atomic": 1200},
    "inference": {"state": "completed"},
    "dna_fee": {"state": "accrued_owed", "fee_exact": "0.0000012", "asset": "USDC", "fee_exact_atomic": 1,
                "rate_bps": 10, "basis_atomic": 1200, "treasury_owner": "owner"},
}


def test_the_activity_panel_marks_receipt_rows_as_islands():
    """The chat page's own row builders put the island class on every row and disclosure that
    carries a money receipt, and on nothing else: a call without one, or with an object that is
    not a UsePod receipt, stays an ordinary themed activity row."""
    from tests.chat_page_js_harness import DOM, run_node, script

    paid = {"seq": 7, "event_type": "model.call_completed", "model_id": "usepod:m", "lane": "cloud",
            "provider_receipt": _RECEIPT}
    plain = {"seq": 8, "event_type": "model.call_completed", "model_id": "local:m", "lane": "local"}
    forged = dict(paid, seq=9, provider_receipt=dict(_RECEIPT, schema="something.else"))
    result = run_node(DOM + script() + "\nconst paid=" + json.dumps(paid) + ", plain=" + json.dumps(plain)
                      + ", forged=" + json.dumps(forged) + r""";
const item = (e) => activityItemHtml({id: 'ai-' + e.seq, tool: '', status: 'completed', display: ledgerRow(e), endEvent: e}, {});
out({rows: [paid, plain, forged].map((e) => renderLedgerRows([e])), items: [paid, plain, forged].map(item)});
""")
    paid_row, plain_row, forged_row = result["rows"]
    paid_item, plain_item, forged_item = result["items"]
    assert 'class="xp-row ok xp-receipt"' in paid_row and "DNA service fee" in paid_row
    assert '<details class="xp-item ok xp-receipt"' in paid_item and "DNA service fee" in paid_item
    for row in (plain_row, forged_row, plain_item, forged_item):
        assert "xp-receipt" not in row, row


@pytest.mark.parametrize("name", [name for name, _ in _variants()])
def test_receipt_rows_keep_vool_tokens_in_every_skin(name):
    """No themed rule's subject can be a receipt row or anything inside it, and the row gets
    VOOL's own tokens back like every other island."""
    from core.toybox_fragment import GUARD, PAGE_TOKENS_SELECTOR

    css = _css(dict(_variants())[name])
    resets = _reset_subjects()
    themed = [subject for subject, _ in _rules(css)[0] if subject != PAGE_TOKENS_SELECTOR and subject not in resets]
    assert themed and all(GUARD in subject for subject in themed if not subject.startswith("@"))
    assert ".xp-receipt" in GUARD.split(":not(")[1]  # the island itself
    assert all(".xp-receipt" in subject for subject in resets)


@pytest.mark.parametrize("name", [name for name, _ in _variants()])
def test_a_skin_applies_whole_over_the_072_appearance_theme(name):
    """0.7.2 added Settings > Appearance, whose Light and same-as-this-computer looks are a
    :root[data-theme] token block. A skin's palette matches that block at equal specificity
    and is served after it, so a skin never lands half over the light palette (light page,
    dark skin frames, a light theme's white text on the skin's bright accent). The skin also
    says whether it is dark or light, and gives accent-filled controls its own button text."""
    from core.toybox_fragment import PAGE_TOKENS_SELECTOR
    from core.toybox_materials import is_dark

    manifest = dict(_variants())[name]
    css = _css(manifest)
    subject, body = _rules(css)[0][0]
    assert subject == PAGE_TOKENS_SELECTOR == ":root,:root[data-theme]"
    palette = manifest["skin"]["palette"]
    assert body.startswith("color-scheme:" + ("dark" if is_dark(palette) else "light") + ";")
    for token, field in (("accent-ink", "accentInk"), ("fg", "ink"), ("bg-elev", "panel"), ("bg", "background")):
        assert f"--{token}:{palette[field]};" in body, token


def test_the_pages_serve_the_skin_after_their_appearance_palette():
    """Equal specificity is decided by order: on both served pages the saved skin's style
    element follows the light and same-as-this-computer palette blocks."""
    from core.vool_chat_page import render_vool_chat_html
    from core.vool_settings_page import render_vool_settings_html

    for page in (render_vool_chat_html(build_commit="t", ui_locale="en", appearance=("light", 100)),
                 render_vool_settings_html(build_commit="t", ui_locale="en", appearance=("light", 100))):
        skin_at = page.index('<style id="vool-skin-style">')
        assert 'data-theme="light"' in page[: page.index(">", page.index("<html")) + 1]
        assert page.index(':root[data-theme="light"] {') < skin_at
        assert page.index(':root[data-theme="system"] {') < skin_at
