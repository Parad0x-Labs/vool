"""The /web0 page speaks the person's language and works with the keyboard.

Pins the four contract points of job web0-page-words-077-20261030:

* every sentence the page shows is a catalog key (web0.*), so de/ja renders carry those
  catalogs' own values -- not English leftovers and not a stale-stamped catalog;
* the standing status bar shows plain words only (B7): owner prefix, tx id and the payments
  endpoint URL render inside the closed "Details" fold, never in the standing row;
* the dispatch panel answers in one plain keyed sentence; the raw result / refusal detail
  goes to the console instead of being pasted into the panel;
* favourite chips are real <button>s (keyboard focus + Enter/Space by construction, visible
  focus ring) and long names get ellipsis / overflow-wrap CSS.

Chromium is not required: the script runs in the node DOM harness (same law as
tests/test_no_fake_empty_phone.py) and the static structure/CSS pins read the render.
"""
from __future__ import annotations

import re

from core.null_browser_page import render_null_browser_html
from tests.chat_page_js_harness import DOM, run_node, script_blocks

# ---- every string is keyed and the fallback equals the catalog ------------------------------

_ATTR_KEYS = ("data-i18n", "data-i18n-title", "data-i18n-placeholder", "data-i18n-aria-label")


def test_every_key_the_page_references_exists_and_the_inline_fallback_matches() -> None:
    import json
    from pathlib import Path

    from core.i18n.catalog import CATALOGS_DIR

    messages = json.loads((Path(CATALOGS_DIR) / "en.json").read_text(encoding="utf-8"))["messages"]
    html = render_null_browser_html()

    referenced: set[str] = set()
    for attr in _ATTR_KEYS:
        referenced.update(re.findall(attr + r'="([^"]+)"', html))
    referenced.update(re.findall(r'VOOLT\("([^"]+)"', html))
    assert referenced, "no keys found -- the extraction broke, not the page"
    unknown = sorted(k for k in referenced if k not in messages)
    assert not unknown, f"keys used by the page but missing from en.json: {unknown}"
    assert all(k.startswith("web0.") for k in referenced), sorted(referenced - {k for k in referenced if k.startswith("web0.")})

    # The English inside each element IS the catalog value (single source, no drift).
    for key, inline in re.findall(r'data-i18n="([^"]+)">\s*([^<]*?)\s*</', html):
        assert inline == messages[key], f"{key}: inline English {inline!r} != catalog {messages[key]!r}"
    for key, inline in re.findall(r'(data-i18n-(?:title|placeholder|aria-label))="([^"]+)"', html):
        assert key and inline in messages  # the attribute value itself is the key


def test_the_page_has_no_english_sentence_left_outside_the_catalog() -> None:
    # The visible text of every element that shows words is either keyed (data-i18n*) or an
    # address/example literal (web0.null, null://task/...). Long English prose surviving in
    # the markup means a new string landed without a key. Only the page's OWN static markup
    # is scanned: the palette CSS and the i18n bundle (all of English, by design) are not
    # this page's strings.
    from core.null_browser_page import _PAGE

    static = _PAGE[_PAGE.index("<title") : _PAGE.index("<style>")] + _PAGE[
        _PAGE.index("</style>") + len("</style>") : _PAGE.index("<script>")
    ]
    for element in re.findall(r">([A-Za-z][A-Za-z'’ ,.:;?!…-]{25,})<", static):
        assert any(
            token in element for token in ("null://", ".null", "Arweave", "Solana", "VOOL")
        ), f"unkeyed English sentence in the page: {element!r}"


# ---- de and ja really carry their own words ---------------------------------------------------

def test_de_and_ja_renders_carry_their_catalogs_own_values() -> None:
    import json
    from pathlib import Path

    from core.i18n.catalog import CATALOGS_DIR

    en = json.loads((Path(CATALOGS_DIR) / "en.json").read_text(encoding="utf-8"))["messages"]
    web0_en = {k: v for k, v in en.items() if k.startswith("web0.")}
    for locale in ("de", "ja"):
        html = render_null_browser_html(locale)
        assert f'<html lang="{locale}"' in html
        bundle = json.loads(re.search(r"var B = (\{.*?\});\n", html, re.DOTALL).group(1))
        theirs = {k: v for k, v in bundle["messages"].items() if k.startswith("web0.")}
        assert len(theirs) == len(web0_en), f"{locale}: bundle lost web0 keys"
        same = [k for k in web0_en if theirs[k] == web0_en[k]]
        # A stale catalog (source_catalog_sha256 not re-stamped) serves pure English and
        # fails here; a translated catalog keeps at most the genuinely-identical few.
        assert len(same) <= 6, f"{locale}: {len(same)} of {len(web0_en)} web0 keys came back English: {sorted(same)[:5]}"


def test_the_api_server_passes_the_resolved_page_locale() -> None:
    # The serving door must hand the page the person's UI locale (?ui_locale= > cookie > en),
    # or no catalog ever reaches a real browser.
    from pathlib import Path

    import core.web.api.service as service_mod

    source = Path(service_mod.__file__).read_text(encoding="utf-8")
    assert "render_null_browser_html(locale=resolve_page_locale(query, headers))" in source


# ---- the standing status bar stays plain; the ids live behind Details ------------------------

def test_owner_tx_and_endpoint_sit_inside_the_details_fold() -> None:
    html = render_null_browser_html()
    details_open = html.index('<details id="s-details">')
    fold_close = html.index("</details>", details_open)
    for raw_id in ('id="s-owner"', 'id="s-tx"', 'id="s-x402-wrap"', 'id="s-x402"'):
        assert html.index(raw_id) > details_open, f"{raw_id} renders outside the Details fold"
        assert html.index(raw_id) < fold_close, f"{raw_id} renders after the Details fold"
    # The standing row carries only plain words: the site name and the payments marker.
    standing = html[html.index('class="line"'):details_open]
    assert 'id="s-name"' in standing and 'id="s-pays"' in standing
    assert 'id="s-owner"' not in standing and 'id="s-x402"' not in standing


_NULL_SCRIPT = script_blocks(render_null_browser_html())[0]

_PREAMBLE = """
const __json = (status, payload) => ({ ok: status >= 200 && status < 300, status: status,
  headers: { get: () => 'application/json' }, json: async () => payload });
"""

_OK_ANSWER = (
    "globalThis.fetch = async () => __json(200, { ok: true, name: 'web0', owner: 'o'.repeat(32),"
    " arweave_txid: 't'.repeat(43), gateway_url: 'https://ar.example/web0', has_content: true,"
    " x402_endpoint: 'https://pay.example/x402' });"
)


def _null(fetch_stub: str, body: str, setup: str = "") -> dict:
    return run_node(DOM + setup + _PREAMBLE + fetch_stub + _NULL_SCRIPT + body)


def test_a_loaded_site_writes_plain_words_into_the_standing_row() -> None:
    result = _null(
        _OK_ANSWER,
        """
await new Promise(setImmediate);
const plain = (id) => String(document.getElementById(id).textContent || '');
out({
  name: plain('s-name'), paysShown: document.getElementById('s-pays').style.display === '',
  owner: plain('s-owner'), tx: plain('s-tx'), endpoint: plain('s-x402'),
  x402Shown: document.getElementById('s-x402-wrap').style.display === '',
  statusShown: document.getElementById('status').style.display === 'flex',
});
""",
        setup="document.getElementById('addr').value = 'web0.null';",
    )
    assert result["errors"] == []
    # Standing row: the name only, no url, no long identifier.
    assert result["name"] == "web0.null"
    assert result["paysShown"] is True
    assert "http" not in result["name"]
    # The ledger values still exist (display only -- nothing about what is recorded changed),
    # but they were written to the elements the Details fold owns.
    assert result["owner"] == "oooooooo" + "…"
    assert result["tx"] == "tttttttttt" + "…"
    assert result["endpoint"] == "https://pay.example/x402"
    assert result["x402Shown"] is True
    assert result["statusShown"] is True


def test_a_site_without_payments_hides_the_marker_and_the_fold_row() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(200, { ok: true, name: 'web0', owner: 'o'.repeat(32),"
        " arweave_txid: '', has_content: false, gateway_url: '' });",
        """
await new Promise(setImmediate);
out({ paysHidden: document.getElementById('s-pays').style.display === 'none',
      tx: String(document.getElementById('s-tx').textContent || '') });
""",
        "document.getElementById('addr').value = 'web0.null';",
    )
    assert result["errors"] == []
    assert result["paysHidden"] is True
    assert result["tx"] == "(none)"


# ---- the dispatch panel speaks one plain sentence ---------------------------------------------

def test_dispatch_answers_in_one_keyed_sentence_and_pastes_no_raw_payload() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(200, { ok: true, result: 'RAW-SECRET-ANSWER 0xdeadbeef' });",
        """
document.getElementById('uri-input').value = 'null://task/hello';
const said = []; const ci = console.info, cw = console.warn;
console.info = (...a) => said.push(['info', a.join(' ')]); console.warn = (...a) => said.push(['warn', a.join(' ')]);
await dispatch();
console.info = ci; console.warn = cw;
out({ text: String(document.getElementById('dispatch-out').textContent || ''), said });
""",
    )
    assert result["errors"] == []
    assert result["text"] == "The task ran."
    assert "RAW-SECRET-ANSWER" not in result["text"] and "0xdeadbeef" not in result["text"]
    # The raw detail went to the console, not the panel.
    assert any("RAW-SECRET-ANSWER" in line for _, line in result["said"]), result["said"]


def test_dispatch_refusal_is_plain_and_the_reason_stays_in_the_console() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(500, { ok: false, message: 'raw refusal detail http://internal' });",
        """
document.getElementById('uri-input').value = 'null://task/hello';
const said = []; const cw = console.warn; console.warn = (...a) => said.push(a.join(' '));
await dispatch(); console.warn = cw;
out({ text: String(document.getElementById('dispatch-out').textContent || ''), said });
""",
    )
    assert result["errors"] == []
    assert result["text"] == "That didn't work. Try again."
    assert "raw refusal" not in result["text"] and "http://internal" not in result["text"]
    assert any("raw refusal detail" in line for line in result["said"]), result["said"]


# ---- chips are real buttons; long names get CSS -----------------------------------------------

def test_favourite_chips_are_real_buttons_with_a_remove_label() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(404, { ok: false, name: 'web0', error: 'unregistered' });",
        """
await new Promise(setImmediate);
const bar = document.getElementById('favbar');
const wrap = bar.children[bar.children.length - 1];
const chip = wrap.children[0], x = wrap.children[1];
// Fire the wired handler the way a real Enter/Space on the button would (the page wires
// onclick; the harness __click only fires addEventListener listeners).
chip.onclick && chip.onclick({ preventDefault() {}, stopPropagation() {} });
out({ wrapCls: String(wrap.className), chipTag: chip.tagName, chipCls: String(chip.className),
      chipType: String(chip.type || ''), chipText: String(chip.textContent || ''),
      xTag: x.tagName, xCls: String(x.className), xType: String(x.type || ''),
      xLabel: String(x.getAttribute('aria-label') || ''),
      addrAfter: document.getElementById('addr').value });
""",
        # Seed favourites BEFORE the page script runs: renderFavs() executes at script
        # evaluation, not after the test body.
        "localStorage.setItem('web0.favorites.v1', JSON.stringify(['web0', 'averyverylongsitenameexample']));",
    )
    assert result["errors"] == []
    assert result["wrapCls"] == "chipwrap"
    # Real buttons: keyboard focus, Enter and Space come with the platform button.
    assert result["chipTag"] == "BUTTON" and result["chipCls"] == "chip"
    assert result["chipType"] == "button"
    assert result["chipText"].endswith(".null")
    assert result["xTag"] == "BUTTON" and result["xCls"] == "x" and result["xType"] == "button"
    assert "averyverylongsitenameexample.null" in result["xLabel"]
    # Clicking through the button navigates (the onclick path the keyboard shares).
    assert result["addrAfter"] == "averyverylongsitenameexample.null"


def test_long_names_get_ellipsis_and_wrap_css_and_chips_have_a_focus_ring() -> None:
    css = render_null_browser_html()
    chip_rule = re.search(r"\.chip\{[^}]*\}", css).group(0)
    assert "max-width" in chip_rule and "text-overflow:ellipsis" in chip_rule and "white-space:nowrap" in chip_rule
    h2_rule = re.search(r"#placeholder h2\{[^}]*\}", css).group(0)
    assert "max-width" in h2_rule and "overflow-wrap:anywhere" in h2_rule
    assert ".chip:focus-visible" in css and ".chipwrap .x:focus-visible" in css
