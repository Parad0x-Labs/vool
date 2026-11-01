"""Real cosmetic authority controls, with distinct palettes and concurrent Apply."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    from storage import db

    # The shared suite pins one DB and clears known tables; this new feature
    # uses a per-case profile instead, through the real runtime path authority.
    monkeypatch.setattr(db, "_DEFAULT_DB_PATH_OVERRIDE", None)
    return prepare_home(tmp_path, monkeypatch)


def raw(index=0):
    from core.toybox import EXAMPLES
    from core.toybox_registry_v2 import manifests

    return json.dumps(EXAMPLES[index])


def test_distinct_examples_and_unseen_light_palette():
    from core.toybox import EXAMPLES, validate

    assert validate(raw())[2] != validate(raw(1))[2]
    light = deepcopy(EXAMPLES[1])
    light["skin"]["palette"].update(
        background="#ffffff",
        panel="#fafafa",
        field="#f4f4f4",
        ink="#101010",
        muted="#404040",
        accent="#174817",
        accentInk="#ffffff",
        border="#555555",
    )
    assert validate(json.dumps(light))[0]["skin"]["palette"]["background"] == "#ffffff"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.update(schema_version=True),
        lambda m: m.update(schema_version=1.0),
        lambda m: m.update(id="voxel-workshop\n"),
        lambda m: m.update(assets=["https://remote/a.png"]),
        lambda m: m["skin"].update(css="body{display:none}"),
        lambda m: m["skin"].update(corners=True),
        lambda m: m["skin"].update(ornament="javascript:alert(1)"),
        lambda m: m["skin"]["palette"].update(accent="#a8dc68\n"),
        lambda m: m["skin"]["palette"].update(ink="#111a13"),
    ],
)
def test_active_content_types_and_unreadable_colours_refuse(mutate):
    from core.toybox import EXAMPLES, SkinError, validate

    manifest = deepcopy(EXAMPLES[0])
    mutate(manifest)
    with pytest.raises(SkinError):
        validate(json.dumps(manifest))


def test_duplicate_fields_refuse():
    from core.toybox import SkinError, validate

    with pytest.raises(SkinError):
        validate(raw().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))


def test_preview_apply_restart_restore_and_prefs_preserved(home):
    from core.toybox import state
    from core.user_preferences import load_preferences

    before = load_preferences()
    initial = api_get("/api/toybox")[1]
    preview = api_post("/api/toybox/preview", {"manifest": raw()})
    assert preview[0] == 200 and preview[1]["digest"]
    assert state()["revision"] == initial["revision"] and state()["digest"] == ""
    result = api_post("/api/toybox/apply", {"manifest": raw(), "revision": 0, "confirmed": True})
    assert result[0] == 200 and result[1]["selected"]["id"] == "voxel-workshop"
    # state opens a fresh database connection; the selected bytes survive independently.
    assert state()["selected"] == result[1]["selected"]
    assert load_preferences() == before
    next_result = api_post("/api/toybox/apply", {"manifest": raw(1), "revision": 1, "confirmed": True})
    assert next_result[0] == 200 and state()["selected"]["id"] == "forest-relic"
    assert api_post("/api/toybox/restore", {"revision": 2, "confirmed": True})[0] == 200
    assert state()["selected"] is None and state()["digest"] == ""


def test_stale_apply_and_unknown_fields_preserve_selection(home):
    from core.toybox import state

    assert api_post("/api/toybox/apply", {"manifest": raw(), "revision": 0, "confirmed": True})[0] == 200
    assert api_post("/api/toybox/apply", {"manifest": raw(1), "revision": 0, "confirmed": True})[0] == 409
    assert (
        api_post("/api/toybox/apply", {"manifest": raw(1), "revision": 1, "confirmed": True, "autonomy": "bypass"})[0]
        == 400
    )
    assert state()["selected"]["id"] == "voxel-workshop" and state()["revision"] == 1
    assert api_post("/api/toybox/apply", {"manifest": raw(1), "revision": 1, "confirmed": False})[0] == 400
    assert api_get("/api/toybox", host="remote.test")[0] == 403
    assert (
        api_post("/api/toybox/apply", {"manifest": raw(1), "revision": 1, "confirmed": True}, host="remote.test")[0]
        == 403
    )


def test_concurrent_windows_only_one_revision_wins(home):
    def apply(i):
        return api_post("/api/toybox/apply", {"manifest": raw(i), "revision": 0, "confirmed": True})[0]

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(apply, (0, 1)))
    assert sorted(results) == [200, 409]
    assert api_get("/api/toybox")[1]["revision"] == 1


def test_same_identity_different_bytes_do_not_overwrite_and_remove_active_restores(home):
    from core.toybox import EXAMPLES, state

    original = deepcopy(EXAMPLES[0])
    revised = deepcopy(original)
    revised["skin"]["corners"] = 3
    a = api_post("/api/toybox/apply", {"manifest": json.dumps(original), "revision": 0, "confirmed": True})[1]
    b = api_post("/api/toybox/apply", {"manifest": json.dumps(revised), "revision": 1, "confirmed": True})[1]
    assert a["digest"] != b["digest"]
    assert any(c["digest"] == a["digest"] for c in state()["choices"])
    assert api_post("/api/toybox/remove", {"digest": b["digest"], "revision": 2, "confirmed": True})[0] == 200
    assert state()["selected"] is None


def test_tampered_skin_falls_back_visibly_without_deleting_evidence(home):
    from core.toybox import state
    from storage.db import get_connection

    api_post("/api/toybox/apply", {"manifest": raw(), "revision": 0, "confirmed": True})
    with get_connection() as conn:
        conn.execute("UPDATE toybox_skin_objects SET manifest=?", ("{}",))
        conn.commit()
    result = state()
    assert result["warning"] and result["selected"] is None and result["digest"]


def test_distinct_runtime_profiles_keep_separate_skin_selections(home, monkeypatch):
    from core.toybox import state

    api_post("/api/toybox/apply", {"manifest": raw(), "revision": 0, "confirmed": True})
    previous = str(home / "home")
    monkeypatch.setenv("VOOL_HOME", str(home / "other-home"))
    assert state()["digest"] == "" and state()["revision"] == 0
    monkeypatch.setenv("VOOL_HOME", previous)
    assert state()["selected"]["id"] == "voxel-workshop"


def test_saved_skin_is_rendered_by_both_real_pages(home):
    from html.parser import HTMLParser

    from core.toybox import state
    from core.vool_chat_page import render_vool_chat_html
    from core.vool_settings_page import render_vool_settings_html

    class Styles(HTMLParser):
        def __init__(self):
            super().__init__()
            self.active = False
            self.value = ""

        def handle_starttag(self, tag, attrs):
            if tag == "style" and dict(attrs).get("id") == "vool-skin-style":
                self.active = True

        def handle_endtag(self, tag):
            if tag == "style":
                self.active = False

        def handle_data(self, data):
            if self.active:
                self.value += data

    assert api_post("/api/toybox/apply", {"manifest": raw(1), "revision": 0, "confirmed": True})[0] == 200
    for render in (render_vool_chat_html, render_vool_settings_html):
        parser = Styles()
        parser.feed(render())
        assert parser.value == state()["css"] and "--bg:#0f1613" in parser.value and "--tb-art-crest" in parser.value
    assert api_post("/api/toybox/restore", {"revision": 1, "confirmed": True})[0] == 200
    for render in (render_vool_chat_html, render_vool_settings_html):
        parser = Styles()
        parser.feed(render())
        assert parser.value == ""


# ---- renderer contract 1.1 and the application-owned materials ----------------------------

# The exact Voxel Workshop 1.0.0 bytes the previous build shipped and applied (renderer 1.0).
PREVIOUS_VOXEL = {
    "schema_version": 1,
    "renderer_contract": "1.0",
    "kind": "skin",
    "id": "voxel-workshop",
    "version": "1.0.0",
    "name": "Voxel Workshop",
    "author": "sls_0x",
    "description": "Grass-green highlights, stone panels and chunky crafted edges. Original art.",
    "license": "CC0-1.0",
    "assets": [],
    "skin": {
        "palette": {
            "background": "#111a13",
            "panel": "#1d2920",
            "field": "#27372b",
            "ink": "#f4f5df",
            "muted": "#bac9b8",
            "accent": "#a8dc68",
            "accentInk": "#16210c",
            "border": "#8aab7d",
        },
        "frame": "pixel",
        "density": "comfortable",
        "corners": 2,
        "borderWidth": 2,
        "gap": 12,
        "typography": "mono-heading",
        "ornament": "voxel",
    },
}

# Palettes the shipped skins do not use: light, mid-grey, and dark with text at the floor.
EDGE_PALETTES = [
    dict(background="#ffffff", panel="#fafafa", field="#f4f4f4", ink="#101010", muted="#404040",
         accent="#174817", accentInk="#ffffff", border="#555555"),
    dict(background="#f3ecd9", panel="#efe6cf", field="#e8dcc0", ink="#2a2214", muted="#5b4d33",
         accent="#7a4f12", accentInk="#fff8e8", border="#7d6a45"),
    dict(background="#1b1b1b", panel="#222222", field="#2a2a2a", ink="#9a9a9a", muted="#959595",
         accent="#d0d0d0", accentInk="#111111", border="#777777"),
]


def test_version_one_renderer_compatibility():
    from core.toybox import SkinError, validate
    from core.toybox_fragment import skin_css

    old, _, _ = validate(json.dumps(PREVIOUS_VOXEL))
    assert old["renderer_contract"] == "1.0" and "--tb-art-stone" in skin_css(old)
    serif_on_old = deepcopy(PREVIOUS_VOXEL)
    serif_on_old["skin"]["typography"] = "serif-heading"
    with pytest.raises(SkinError):
        validate(json.dumps(serif_on_old))
    serif_on_old["renderer_contract"] = "1.1"
    assert validate(json.dumps(serif_on_old))[0]["skin"]["typography"] == "serif-heading"
    for contract in ("1.3", "2.1", 1.1, None):
        bad = deepcopy(serif_on_old)
        bad["renderer_contract"] = contract
        with pytest.raises(SkinError):
            validate(json.dumps(bad))


def _blend(surface, tint, opacity):
    from core.toybox_materials import _rgb

    channels = [x + (y - x) * opacity for x, y in zip(_rgb(surface), _rgb(tint), strict=True)]
    return "#" + "".join(f"{round(c):02x}" for c in channels)


@pytest.mark.parametrize("palette", [*EDGE_PALETTES, None])
@pytest.mark.parametrize("ornament", ["neon", "sakura", "xeno"])
def test_illustrated_ornaments_keep_text_readable_over_any_art(palette, ornament):
    """Glass plates and veils are opaque enough that even a pure white or black backdrop
    behind them leaves text at 4.5:1; every other surface an ornament puts under text too."""
    from core.toybox import contrast, validate
    from core.toybox_fragment import _tokens
    from core.toybox_ornaments import ORNAMENTS

    manifest = _built_in({"neon": "neon-courier", "sakura": "sakura-skies", "xeno": "xeno-abyss"}[ornament])
    if palette:
        manifest["skin"]["palette"] = palette
    skin = validate(json.dumps(manifest))[0]["skin"]
    p = skin["palette"]
    tokens = _tokens(skin)
    for black_or_white in ("#000000", "#ffffff"):
        alpha = float(tokens["tb-glass"].rsplit(",", 1)[1].rstrip(")"))
        seen = _blend(black_or_white, p["panel"], alpha)
        for ink in (p["ink"], p["muted"]):
            worst_case = contrast(seen, ink)
            if (black_or_white == "#ffffff") == (contrast(p["background"], "#000000") < contrast(p["ink"], "#000000")):
                assert worst_case >= 4.5, (ornament, black_or_white, seen, ink)
    surfaces = [*ORNAMENTS[ornament]["under_text"](p, tokens), tokens["tb-hover"], tokens["tb-panel-hover"]]
    for surface in surfaces:
        for ink in (p["ink"], p["muted"]):
            assert contrast(surface, ink) >= 4.5, (ornament, surface, ink)


@pytest.mark.parametrize("palette", EDGE_PALETTES)
@pytest.mark.parametrize("ornament", ["voxel", "leaf"])
def test_materials_under_text_never_drop_below_the_contrast_floor(palette, ornament):
    import re

    from core import toybox_materials as art
    from core.toybox import contrast, validate
    from core.toybox_fragment import _tokens

    manifest = deepcopy(PREVIOUS_VOXEL)
    manifest["skin"]["palette"] = palette
    manifest["skin"]["ornament"] = ornament
    skin = validate(json.dumps(manifest))[0]["skin"]
    inks = (palette["ink"], palette["muted"])
    surfaces = []
    tokens = _tokens(skin)
    surfaces += [tokens["tb-hover"], tokens["tb-panel-hover"]]
    if ornament == "voxel":
        surfaces += [tokens["tb-dirt"], tokens["tb-wood"]]
        tiles = [(art.stone_tile(palette), palette["panel"]), (art.plank_tile(palette, tokens["tb-wood"]), tokens["tb-wood"])]
        pattern = r"fill='(#[0-9a-f]{6})' fill-opacity='([0-9.]+)'"
    else:
        tiles = [(art.contour_tile(palette), palette["panel"])]
        pattern = r"stroke='(#[0-9a-f]{6})' stroke-opacity='([0-9.]+)'"
    for markup, base in tiles:
        found = re.findall(pattern, markup)
        assert found
        surfaces += [_blend(base, tint, float(opacity)) for tint, opacity in found]
    for surface in surfaces:
        for ink in inks:
            assert contrast(surface, ink) >= 4.5, (surface, ink)


def _rule_selectors(css):
    import re

    selectors = [selector.strip() for selector in re.findall(r"([^{}]+)\{[^{}]*\}", css)]
    # keyframe steps (0%, 49.9%, from, to) are not element selectors
    return [s for s in selectors if not re.fullmatch(r"(?:[0-9.]+%|from|to)(?:,(?:[0-9.]+%|from|to))*", s)]


BUILT_IN_IDS = ["voxel-workshop", "forest-relic", "neon-courier", "sakura-skies", "xeno-abyss"]


def _built_in(identifier):
    from core.toybox import EXAMPLES

    return deepcopy(next(e for e in EXAMPLES if e["id"] == identifier))


@pytest.mark.parametrize("variant", [*BUILT_IN_IDS, "light-flat", "pixel-leaf", "chamfer-sakura-light", "capsule-xeno-dark"])
def test_every_skin_rule_excludes_the_trusted_islands(variant):
    from core.toybox import validate
    from core.toybox_fragment import GUARD, ISLAND_RESET, PAGE_TOKENS_SELECTOR, TRUSTED, skin_css

    manifest = _built_in(variant if variant in BUILT_IN_IDS else "voxel-workshop")
    if variant == "light-flat":
        manifest["skin"].update(frame="flat", ornament="none", palette=EDGE_PALETTES[0])
    if variant == "pixel-leaf":
        manifest["skin"].update(frame="pixel", ornament="leaf")
    if variant == "chamfer-sakura-light":
        manifest.update(renderer_contract="1.2")
        manifest["skin"].update(frame="chamfer", ornament="sakura", palette=EDGE_PALETTES[1])
    if variant == "capsule-xeno-dark":
        manifest.update(renderer_contract="1.2")
        manifest["skin"].update(frame="capsule", ornament="xeno")
    selectors = _rule_selectors(skin_css(validate(json.dumps(manifest))[0]))
    # the only unguarded rules: page tokens, and the resets that address the islands themselves
    # (VOOL's dark, light and same-as-this-computer tokens, then the backing)
    resets = _rule_selectors(ISLAND_RESET)
    themed = [s for s in selectors if s != PAGE_TOKENS_SELECTOR and s not in resets]
    assert len(themed) > 20 and TRUSTED in selectors and len(resets) == 4
    for selector in themed:
        subject = selector.split("::")[0]
        assert subject.endswith(GUARD), selector


def test_artwork_is_static_application_svg_and_preview_denies_network():
    import re
    from urllib.parse import unquote

    from core.toybox import EXAMPLES, validate
    from core.toybox_fragment import PREVIEW_CSP, preview_html, skin_css
    from core.toybox_materials import SVG_NS, thumbnail

    assert PREVIEW_CSP == "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    for example in EXAMPLES:
        manifest = validate(json.dumps(example))[0]
        css = skin_css(manifest)
        urls = re.findall(r'url\("([^"]*)"\)', css)
        assert urls and all(u.startswith("data:image/svg+xml,") for u in urls)
        assert "url(" not in re.sub(r'url\("data:image/svg\+xml,[^"]*"\)', "", css).replace("var(", "")
        documents = [unquote(u.removeprefix("data:image/svg+xml,")) for u in urls]
        documents.append(unquote(thumbnail(manifest).removeprefix("data:image/svg+xml,")))
        for document in documents:
            inner = document.replace(SVG_NS, "")
            assert document.startswith("<svg xmlns='" + SVG_NS + "'")
            assert not re.search(r"href|<script|<text|<image|<foreignObject|<use|\bon[a-z]+=|://|javascript:", inner, re.I)
            defined = {"#" + i for i in re.findall(r"\bid='([A-Za-z0-9_-]+)'", inner)}
            assert set(re.findall(r"url\(([^)]*)\)", inner)) <= defined
        page = preview_html(manifest)
        assert PREVIEW_CSP in page and "<script" not in page.lower()


def test_state_offers_thumbnails_and_upgraded_profile_keeps_its_saved_copy(home):
    from core.toybox import state
    from core.toybox_registry_v2 import manifests

    first = state()
    from core.toybox import EXAMPLES

    thumbs = [c["thumbnail"] for c in first["choices"]]
    assert len(set(thumbs)) == len(EXAMPLES) + len(manifests()) and all(t.startswith("data:image/svg+xml,") for t in thumbs)
    # A profile that applied the previous build's Voxel Workshop keeps that exact selection.
    applied = api_post("/api/toybox/apply", {"manifest": json.dumps(PREVIOUS_VOXEL), "revision": 0, "confirmed": True})
    assert applied[0] == 200
    after = state()
    assert after["selected"]["version"] == "1.0.0" and "--tb-art-stone" in after["css"]
    versions = sorted((c["manifest"]["version"], c["builtin"]) for c in after["choices"] if c["manifest"]["id"] == "voxel-workshop")
    assert versions == [("1.0.0", False), ("2.0.0", True)]


@pytest.mark.parametrize("variant", ["voxel", "forest", "light-flat"])
def test_status_controls_keep_their_meaning_colours_in_every_skin(variant):
    from core.toybox import EXAMPLES, validate
    from core.toybox_fragment import GUARD, ISLAND_RESET, PINNED, TRUSTED, skin_css

    manifest = deepcopy(EXAMPLES[1] if variant == "forest" else EXAMPLES[0])
    if variant == "light-flat":
        manifest["skin"].update(frame="flat", ornament="none", palette=EDGE_PALETTES[0])
    css = skin_css(validate(json.dumps(manifest))[0])
    # The lane badges and the bypass warning are restated after every themed rule, so no
    # frame, ornament or effect can recolour them; approvals and receipts stay excluded.

    def subject(s):
        return s[0] if len(s) == 1 else ":is(" + ",".join(s) + ")"

    rules = [subject(s) + suffix + GUARD + "{" + body + "}\n" for s, body in PINNED for suffix in ("", ":hover")]
    tail = css[css.index(rules[0]) :]
    for rule in rules:
        tail = tail.replace(rule, "", 1)
    # after the first pinned rule only the remaining pinned rules and the trusted reset follow:
    # VOOL's own tokens on the islands (for the chosen theme), then a zero-specificity backing
    # their own rules override
    assert tail == ISLAND_RESET and tail.startswith(TRUSTED + "{")
    island = ":is(" + TRUSTED + ")"
    assert ":where(" + island + ":not(" + island + " *)){background-color:var(--panel);color:var(--ink)}" in tail
    assert "#modelCtrl" not in TRUSTED and "#permBar" in TRUSTED and "#bypassBanner" in TRUSTED
    assert all(sel in TRUSTED for sel in (".chat-provenance", ".proof-chip", ".price-safety", '[role="alertdialog"]'))


# The reviewed skins' exact output. Updated when the trusted-island reset moved its backing
# colours into a zero-specificity :where() rule for top-level islands only (islands' own colours
# now win, nested islands inherit), when the neutral status-dot colour was pinned, and when the
# in-answer report and activity buttons joined the themed buttons, and when the island list grew
# by the 0.7.2 consent surfaces and the payment receipt rows while the island reset moved to VOOL's
# own palette for each Appearance theme, and when the pinned status colours followed the 0.7.2
# pages and the skin palette began to apply over the Appearance theme; each time the changed rules
# were proven to be the only bytes that differ in any skin's CSS.
GOLDEN_CSS = {
    "voxel-workshop": "d6f1945b4e34f2f1a7e4f7db3e9acfab52dc536469200f2b2a30495f93f1b217",
    "forest-relic": "ce4859126c149b4d0fe75291b378e5c8f734164550b45f191d072dcb4a5925ba",
}


def test_owner_reviewed_skins_render_byte_identical():
    import hashlib

    from core.toybox import validate
    from core.toybox_fragment import skin_css


    def digest(manifest):
        return hashlib.sha256(skin_css(validate(json.dumps(manifest))[0]).encode()).hexdigest()

    for identifier, expected in GOLDEN_CSS.items():
        assert digest(_built_in(identifier)) == expected, identifier
    assert digest(PREVIOUS_VOXEL) == "b8d30aa0185ba83e64d80087afa51c5331fda8ea53c301b5b815ab13adb93ff2"
    light = _built_in("voxel-workshop")
    light["skin"]["palette"] = EDGE_PALETTES[0]
    assert digest(light) == "22bf5248b2578d7bea5fbb12d6c3c363ccaabfaf600e0d9b2dca8bd3acac7674"


@pytest.mark.parametrize("field,value", [("frame", "toon"), ("frame", "capsule"), ("frame", "chamfer"), ("ornament", "neon"),
                                          ("ornament", "sakura"), ("ornament", "xeno"), ("typography", "rounded-heading")])
def test_contract_one_two_values_are_refused_by_older_contracts(field, value):
    from core.toybox import SkinError, validate

    manifest = deepcopy(PREVIOUS_VOXEL)
    manifest["skin"][field] = value
    for contract in ("1.0", "1.1"):
        manifest["renderer_contract"] = contract
        with pytest.raises(SkinError):
            validate(json.dumps(manifest))
    manifest["renderer_contract"] = "1.2"
    assert validate(json.dumps(manifest))[0]["skin"][field] == value


def _motion_block(css):
    head = "@media (prefers-reduced-motion: no-preference){"
    start = css.find(head)
    if start < 0:
        return "", css
    depth, i = 1, start + len(head)
    while depth:
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        i += 1
    return css[start:i], css[:start] + css[i:]


@pytest.mark.parametrize("identifier", BUILT_IN_IDS)
def test_motion_only_runs_when_the_system_allows_it(identifier):
    from core.toybox import validate
    from core.toybox_fragment import skin_css

    css = skin_css(validate(json.dumps(_built_in(identifier)))[0])
    motion, rest = _motion_block(css)
    assert "@keyframes" in motion and "animation:" in motion
    assert "@keyframes" not in rest and "animation:" not in rest and "transition:" not in rest


@pytest.mark.parametrize("identifier", BUILT_IN_IDS)
def test_accent_text_and_focus_stay_readable(identifier):
    import re

    from core.toybox import contrast, validate
    from core.toybox_fragment import skin_css

    manifest = validate(json.dumps(_built_in(identifier)))[0]
    palette, css = manifest["skin"]["palette"], skin_css(manifest)
    surfaces = [palette[s] for s in ("background", "panel", "field")]
    text = re.search(r"--tb-accent-text:(#[0-9a-f]{6})", css)
    colour = text.group(1) if text else palette["accent"]
    assert min(contrast(colour, s) for s in surfaces) >= 4.5
    focus = re.search(r"--tb-focus:(#[0-9a-f]{6})", css)
    assert min(contrast(focus.group(1) if focus else palette["accent"], s) for s in surfaces) >= 3
    if text:
        assert "{color:var(--tb-accent-text)}" in css
