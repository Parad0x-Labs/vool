"""A failed load never renders as a fake "nothing here" on the phone-pairing and web0 pages.

Page half of the no-fake-empty contract, same law as tests/test_restart_shows_loading.py pins for
the chat sidebar:

* /web0 (core/null_browser_page.py open_): a fetch that throws, a body that will not parse, or an
  error status the server did not answer "not found" for paints the reach-failure wording --
  never "the site may not exist yet" and never "has no pages yet". "has no pages yet" shows only
  on a real OK answer without content, and a real OK answer with content still loads the site.
* /api/mobile/pairing/page (core/web/api/mobile_companion_api.py): the page's one fetch ("Make a
  new code") says a failure in words and hands the button back while the countdown keeps running;
  a real OK answer opens the new pairing page.

Both pages are served server-side with no i18n helper, so the wording under test is the pages'
own English (reported to the lead; no catalog keys exist for these surfaces).
"""
from __future__ import annotations

import time

from core.null_browser_page import render_null_browser_html
from tests.chat_page_js_harness import DOM, run_node, script_blocks


# ---- /web0: the .null browser ---------------------------------------------------------------
# The page auto-opens web0.null while the script evaluates, so the fetch stub is installed BEFORE
# the page script runs (the harness default fetch would otherwise answer first).

_NULL_SCRIPT = script_blocks(render_null_browser_html())[0]

_NULL_PREAMBLE = """
const __json = (status, payload) => ({ ok: status >= 200 && status < 300, status: status,
  headers: { get: () => 'application/json' }, json: async () => payload });
const __ph = () => {
  const ph = document.getElementById('placeholder');
  return {
    h2: String(ph.querySelector('h2').textContent || ''),
    hint: String(ph.querySelector('.hint').textContent || ''),
    frameShown: document.getElementById('frame').style.display === 'block',
    statusShown: document.getElementById('status').style.display === 'flex',
  };
};
"""


def _null(fetch_stub: str, body: str) -> dict:
    setup = """
// The stub DOM does not parse HTML attributes; give the address bar the page's own default.
document.getElementById('addr').value = 'web0.null';
"""
    return run_node(DOM + setup + _NULL_PREAMBLE + fetch_stub + _NULL_SCRIPT + body)


def test_web0_error_status_is_a_reach_failure_not_a_missing_site() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(502, { ok: false, name: 'web0', error: 'resolver error' });",
        """
await new Promise(setImmediate);
const ph = __ph();
out({ h2: ph.h2, hint: ph.hint, statusShown: ph.statusShown,
      missing: ph.hint.includes('may not exist yet'), noPages: ph.h2.includes('has no pages yet') });
""",
    )
    assert result["errors"] == []
    assert result["h2"] == "Couldn't reach web0.null"
    assert "did not answer" in result["hint"]
    assert "press Go again" in result["hint"]
    assert result["missing"] is False
    assert result["noPages"] is False
    assert result["statusShown"] is False


def test_web0_fetch_throw_paints_the_reach_failure() -> None:
    result = _null(
        "globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };",
        """
await new Promise(setImmediate);
const ph = __ph();
out({ h2: ph.h2, hint: ph.hint,
      missing: ph.hint.includes('may not exist yet'), noPages: ph.h2.includes('has no pages yet') });
""",
    )
    assert result["errors"] == []
    assert result["h2"] == "Couldn't reach web0.null"
    assert "press Go again" in result["hint"]
    assert result["missing"] is False
    assert result["noPages"] is False


def test_web0_unreadable_body_is_a_reach_failure() -> None:
    result = _null(
        """
globalThis.fetch = async () => ({ ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => { throw new SyntaxError('Unexpected token <'); } });
""",
        """
await new Promise(setImmediate);
const ph = __ph();
out({ h2: ph.h2, hint: ph.hint,
      missing: ph.hint.includes('may not exist yet'), noPages: ph.h2.includes('has no pages yet') });
""",
    )
    assert result["errors"] == []
    assert result["h2"] == "Couldn't reach web0.null"
    assert result["missing"] is False
    assert result["noPages"] is False


def test_web0_not_found_answer_keeps_its_own_wording() -> None:
    # 404 is the server's own not-found answer; it keeps the name-checking wording.
    result = _null(
        "globalThis.fetch = async () => __json(404, { ok: false, name: 'web0', error: 'unregistered' });",
        """
await new Promise(setImmediate);
const ph = __ph();
out({ h2: ph.h2, hint: ph.hint, reach: ph.h2.includes(\"Couldn't reach\") });
""",
    )
    assert result["errors"] == []
    assert result["h2"] == "Couldn't open web0.null"
    assert "may not exist yet" in result["hint"]
    assert result["reach"] is False


def test_web0_no_pages_yet_shows_only_on_a_real_ok_answer() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(200, { ok: true, name: 'web0', owner: 'o'.repeat(32),"
        " arweave_txid: '', has_content: false, gateway_url: '' });",
        """
await new Promise(setImmediate);
const ph = __ph();
out({ h2: ph.h2, hint: ph.hint, reach: ph.h2.includes(\"Couldn't reach\") });
""",
    )
    assert result["errors"] == []
    assert result["h2"] == "web0.null has no pages yet"
    assert "have not published" in result["hint"]
    assert result["reach"] is False


def test_web0_a_real_ok_answer_with_content_still_loads_the_site() -> None:
    result = _null(
        "globalThis.fetch = async () => __json(200, { ok: true, name: 'web0', owner: 'o'.repeat(32),"
        " arweave_txid: 't'.repeat(43), gateway_url: 'https://ar.example/web0', has_content: true });",
        """
await new Promise(setImmediate);
const ph = __ph();
out({ frameShown: ph.frameShown, statusShown: ph.statusShown,
      name: String(document.getElementById('s-name').textContent || ''),
      placeholder: document.getElementById('placeholder').style.display === 'flex' });
""",
    )
    assert result["errors"] == []
    assert result["frameShown"] is True
    assert result["statusShown"] is True
    assert result["name"] == "web0.null"
    assert result["placeholder"] is False


# ---- /api/mobile/pairing/page: the desktop pairing surface ----------------------------------
# The real render path: the vendored QR renderer is inlined into the page's single script block.

def _pairing_html() -> str:
    from core.web.api.mobile_companion_api import _html_pairing_page

    payload = {
        "pid": "pair_test1234",
        "code": "123-456",
        "fp": "f" * 64,
        "short_fp": "AB CD EF",
        "exp": int(time.time()) + 600,
        "status": "waiting",
    }
    response = _html_pairing_page(payload)
    return (response.body or b"").decode("utf-8")


_PAIR_SCRIPT = script_blocks(_pairing_html())[0]

_PAIR_PREAMBLE = """
const __json = (status, payload) => ({ ok: status >= 200 && status < 300, status: status,
  headers: { get: () => 'application/json' }, json: async () => payload });
const __say = () => String(document.getElementById('againSay').textContent || '');
const __gone = () => document.getElementById('card').classList.contains('gone');
const __againEnabled = () => document.getElementById('again').disabled === false;
const __countdown = () => String(document.getElementById('exp').textContent || '');
"""


def _pair(body: str) -> dict:
    return run_node(DOM + _PAIR_SCRIPT + _PAIR_PREAMBLE + body)


def test_pairing_new_code_fetch_failure_is_said_in_words_and_the_button_comes_back() -> None:
    result = _pair(
        """
globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
document.getElementById('again').__click();
await new Promise(setImmediate);
out({ say: __say(), enabled: __againEnabled(), gone: __gone(), countdown: __countdown() });
""",
    )
    assert result["errors"] == []
    assert result["say"] == "VOOL isn't answering right now. Check that it is running, then try again."
    assert result["enabled"] is True
    assert result["gone"] is False
    assert result["countdown"].startswith("Expires in")


def test_pairing_new_code_non_ok_answer_keeps_the_button_and_says_why() -> None:
    result = _pair(
        """
globalThis.fetch = async () => __json(500, { ok: false, code: 'other' });
document.getElementById('again').__click();
await new Promise(setImmediate);
out({ say: __say(), enabled: __againEnabled(), gone: __gone(), countdown: __countdown() });
""",
    )
    assert result["errors"] == []
    assert result["say"] == "Couldn't make a new code. Please try again."
    assert result["enabled"] is True
    assert result["gone"] is False
    assert result["countdown"].startswith("Expires in")


def test_pairing_new_code_unreadable_body_is_said_in_words() -> None:
    result = _pair(
        """
globalThis.fetch = async () => ({ ok: true, status: 200,
  headers: { get: () => 'application/json' },
  json: async () => { throw new SyntaxError('Unexpected token <'); } });
document.getElementById('again').__click();
await new Promise(setImmediate);
out({ say: __say(), enabled: __againEnabled() });
""",
    )
    assert result["errors"] == []
    assert result["say"] == "VOOL isn't answering right now. Check that it is running, then try again."
    assert result["enabled"] is True


def test_pairing_new_code_ok_answer_opens_the_new_page() -> None:
    result = _pair(
        """
globalThis.fetch = async () => __json(200, { ok: true, pairing_page: '/api/mobile/pairing/page?pairing_id=pair_new' });
document.getElementById('again').__click();
await new Promise(setImmediate);
out({ href: String(window.location.href || ''), say: __say() });
""",
    )
    assert result["errors"] == []
    assert result["href"] == "/api/mobile/pairing/page?pairing_id=pair_new"
    assert result["say"] == ""


def test_pairing_page_serves_both_failure_wordings_and_the_real_end_state() -> None:
    # Python-side: the rendered page carries the failure wording for both directions of the one
    # fetch, and the real "expired" end state stays available to the countdown.
    html = _pairing_html()
    assert "VOOL isn't answering right now" in html
    assert "Couldn't make a new code" in html
    assert "This code expired." in html
