"""Settings rows under failure: one plain keyed sentence, never HTTP codes or server detail.

The rendered behaviour is driven through the node harness (the same DOM shape the no-fake-empty
suite boots): every URL answering 503 with a server-detail body must paint rows that carry no
"HTTP", no status digits and no raw server detail -- only the keyed plain sentence. The page
source is pinned too, so a new failure path cannot quietly compose dev text into a row again.
"""

from __future__ import annotations

from core.vool_settings_page import render_vool_settings_html
from tests.chat_page_js_harness import run_node, script_blocks
from tests.test_no_fake_empty_settings import DOM, _routes_js, settings_scripts

SETTINGS_HTML = render_vool_settings_html()
SETTINGS_SRC = "\n".join(settings_scripts())


def test_the_keys_group_is_named_your_keys() -> None:
    assert '"API Keys"' not in SETTINGS_HTML, "the keys group still carries the old 'API Keys' title"
    assert "Your keys" in SETTINGS_HTML, "the keys group lost its 'Your keys' title"


def test_no_row_composes_http_status_or_raw_server_detail_into_person_visible_text() -> None:
    # The TASK-scoped row compositions. Wider 'HTTP ' constructions on other widget paths
    # exist on BASE and stay (next-job list in REPORT); the driven test below proves the
    # failed-load rows a person actually sees carry no code and no raw detail.
    for banned in (
        "Unavailable — ",
        "refused with HTTP",
    ):
        assert banned not in SETTINGS_SRC, f"the settings page still composes {banned!r} into a row"
    # NOTE: "'Not saved — ' + why" and "'VOOL stored ' + JSON.stringify(stored) + ' instead.'"
    # are pinned verbatim by tests/test_vool_settings_page.py:130-131 and stay until sls okays
    # the test edit (see REPORT Needs); the pinned branch now renders the keyed plain sentence
    # through `why`, so no raw detail rides it.


def test_export_import_preview_say_plain_keyed_sentences_not_e_message() -> None:
    for phrase in ("Export failed: ", "Preview failed: ", "Import failed: "):
        assert phrase not in SETTINGS_SRC, f"{phrase!r} still reaches the person"
    # e.message may ride to the console only, never into say()/textContent -- except the one
    # tfmt call pinned verbatim by tests/test_setup_checklist_wiring.py:67 (Needs entry).
    pinned = "tfmt('setup.checklist.not_saved', 'Not saved — {error}'"
    for line in SETTINGS_SRC.splitlines():
        if pinned in line:
            continue
        if "e.message" in line:
            assert "console.warn" in line or "console.error" in line, (
                f"e.message is used outside a console line: {line.strip()[:100]!r}"
            )


def test_row_state_words_come_from_the_catalog_not_literals() -> None:
    from tests.test_failure_words_catalog_law import _messages

    en = _messages("en")
    for key in (
        "settings.row_loading", "settings.row_saving", "settings.row_saved", "settings.row_cleared",
        "settings.retry", "settings.row_unavailable", "settings.not_responding", "settings.row_not_saved",
        "settings.export_failed", "settings.preview_failed", "settings.import_failed",
    ):
        # settings.row_saved_differently is deliberately absent: its wiring site
        # ("'VOOL stored ' + JSON.stringify(stored) + ' instead.'") is pinned by
        # tests/test_vool_settings_page.py:131 and TASK.md forbids changing pinned words.
        assert key in SETTINGS_SRC, f"{key} is not read from the catalog on the settings page"
        assert en[key] in SETTINGS_HTML, f"the English fallback for {key} is not present on the page"


def test_a_failed_load_paints_no_http_and_no_server_detail_in_any_row() -> None:
    """Driven: every URL 503s with a server-detail body; the rendered rows must stay plain."""
    program = (
        DOM
        # the keys pane paints its provider-guide results through replaceChildren,
        # which the minimal DOM shape above does not define
        + "El.prototype.replaceChildren = function (...kids) { this.children = [...kids]; this.__text = ''; };\n"
        + "El.prototype.insertBefore = function (n, ref) { const i = ref ? this.children.indexOf(ref) : -1; if (i >= 0) this.children.splice(i, 0, n); else this.children.push(n); n.parentNode = this; return n; };\n"
        + "document.createElementNS = (ns, t) => new El(t);\n"
        + _routes_js({})
        + "globalThis.__defaultRoute = {'status': 503, 'body': {'ok': false, 'error': 'store unavailable', 'detail': 'HTTP 503 from upstream'}};\n"
        + "\n".join(settings_scripts())
        + """
await globalThis.__wait(200);
await window.__voolSettings.go('keys');   // the keys group reads its sources on open
await globalThis.__wait(400);
const paneBody = document.querySelector('#paneBody');
const text = paneBody.textContent;
out({ paneText: text, errors: [] });
"""
    )
    result = run_node(program)
    assert result["errors"] == [], result["errors"]
    text = result["paneText"]
    assert text, "the driven keys pane painted nothing; the test proved nothing"
    assert "HTTP" not in text, f"an HTTP code reached a rendered row: {text[:400]!r}"
    assert "503" not in text, f"a status number reached a rendered row: {text[:400]!r}"
    assert "store unavailable" not in text, f"raw server detail reached a rendered row: {text[:400]!r}"
    assert "Unavailable" not in text, f"the old 'Unavailable' row composition survived: {text[:400]!r}"
