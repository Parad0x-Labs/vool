"""Widget failure paths on Settings: one keyed plain sentence; codes and detail go to the console.

The failure-words-2 sweep of core/vool_settings_page.py replaced every remaining 'HTTP ' + status /
' (HTTP n)' / 'VOOL stored <raw>' composition on the widget paths (the ~24 sites FW1 listed as
still broken, plus the two 'VOOL stored ' mismatch displays). What a person reads on a row,
toast, status line or card is a keyed sentence -- the FW1 settings.* keys reused wherever the
meaning fits, and the one new contract key settings.store_mismatch for stored-value mismatches;
the status, code and raw stored value ride console.warn. This file pins that state in the served
page source, so a new failure path cannot quietly compose dev text into person-visible words
again.
"""

from __future__ import annotations

from core.vool_settings_page import render_vool_settings_html
from tests.chat_page_js_harness import run_node
from tests.test_no_fake_empty_settings import DOM, _routes_js, settings_scripts

SETTINGS_HTML = render_vool_settings_html()
SETTINGS_SRC = "\n".join(settings_scripts())

# The ONE composition the page still builds from 'VOOL stored ': pinned verbatim by
# tests/test_vool_settings_page.py:132 ("'VOOL stored ' + JSON.stringify(stored) +
# ' instead.'"), so it stays byte-identical until sls okays that test edit (FW1 REPORT Needs).
# Its raw stored value already rides the console.warn on the same line.
PINNED_STORED = "'VOOL stored ' + JSON.stringify(stored) + ' instead.'"

# The keyed sentences this sweep wired, with their exact English fallbacks (FW1's catalog
# values; settings.store_mismatch is the FW2 contract text from CLAIMS.md).
KEYED_CALLS = (
    r"""T('settings.row_not_saved', "Couldn\u2019t save that just now. Try again.")""",
    r"""T('settings.row_unavailable', "Couldn\u2019t load this right now. Try again.")""",
    r"""T('settings.not_responding', "VOOL isn\u2019t responding right now.")""",
    "T('settings.store_mismatch', 'What Settings stored does not match what was saved. Try again.')",
    "T('settings.retry', 'Retry')",
)


def test_no_http_code_composition_survives_on_any_widget_path() -> None:
    """No person-facing string is built from an HTTP status: the compositions are gone."""
    for line in SETTINGS_SRC.splitlines():
        for banned in (
            "'HTTP ' +",
            "' (HTTP '",
            "(HTTP '",
            ", HTTP '",
            "HTTP refused",
        ):
            assert banned not in line, (
                f"the settings page still composes {banned!r} into person-visible text: "
                f"{line.strip()[:100]!r}"
            )


def test_the_only_vool_stored_composition_is_the_one_pinned_line() -> None:
    for line in SETTINGS_SRC.splitlines():
        if "'VOOL stored '" not in line:
            continue
        assert line.strip() in (
            "else { console.warn('settings stored a different value for ' + row.id + ':', stored); "
            "setState(row.id, 'stale', 'VOOL stored ' + JSON.stringify(stored) + ' instead.'); }",
        ), (
            "a 'VOOL stored ' composition outside the one line pinned by "
            f"tests/test_vool_settings_page.py:132: {line.strip()[:120]!r}"
        )
        assert "console.warn" in line, "the pinned mismatch line must still warn its raw value"
    assert PINNED_STORED in SETTINGS_SRC, "the pinned line itself must stay byte-identical"


def test_widget_failure_paths_read_the_keyed_plain_sentences() -> None:
    for call in KEYED_CALLS:
        assert call in SETTINGS_SRC, f"{call!r} is not wired on the settings page"
    # The store-mismatch sentence is the FW2 contract text, exactly.
    assert (
        "T('settings.store_mismatch', 'What Settings stored does not match what was saved. Try again.')"
        in SETTINGS_SRC
    ), "settings.store_mismatch must carry its exact contract fallback"
    assert (
        "What Settings stored does not match what was saved. Try again." in SETTINGS_HTML
    ), "the store-mismatch sentence does not reach the served page"


def test_refusal_branches_throw_the_keyed_sentence_and_warn_the_detail() -> None:
    """The sweep's refusal branches keep their diagnosis: the branch warns the status/code on
    the same line it throws the keyed sentence (FW1's getJSON catch-and-rethrow and the updates
    check predate the sweep and warn at their callers, so only branches that name a response
    are counted). No throw anywhere carries an HTTP or stored-value composition."""
    warned_refusals = 0
    for line in SETTINGS_SRC.splitlines():
        if "throw new Error(" not in line:
            continue
        for banned in ("HTTP", "'VOOL stored '"):
            assert banned not in line, f"a throw still composes {banned!r}: {line.strip()[:100]!r}"
        if (
            "T('settings." in line
            and "console.warn" in line
            and ("!r.ok" in line or "!ok" in line or "ok === false" in line)
        ):
            warned_refusals += 1
    assert warned_refusals >= 10, (
        f"expected the sweep's warn-then-throw refusal branches, found only {warned_refusals}"
    )


def test_backup_dialog_and_x_refusals_put_the_keyed_sentence_first() -> None:
    """Round 2 of the sweep: the backup dialog's export/staging/preview/import says, the
    security key-check line and the X-setup/X-usage notes no longer lead with the server's
    message or a status; the keyed sentence is what renders and the detail rides console.warn."""
    for gone in (
        "say(d.error || 'Export refused.'",
        "say(sj.error || 'Could not stage the file.'",
        "say(d.error || 'Preview refused.'",
        "say(d.error || 'Import refused",
        "tfmt('keys.check_failed', 'Check failed — {message}'",
        "res.j.message || (res.ok ? '' : T('xsetup.failed'",
        "out.j.message || (out.ok ? '' : lost)",
        "out.j.message || T('xact.reset_failed'",
        "out.j.message || T('xact.limits_failed'",
    ):
        assert gone not in SETTINGS_SRC, f"the raw-detail-first display is back: {gone!r}"
    for wired in (
        r"""say(T('settings.export_failed', "Couldn\u2019t export this chat. Try again.")""",
        r"""say(T('settings.preview_failed', "Couldn\u2019t preview this file. Try again.")""",
        r"""say(T('settings.import_failed', "Couldn\u2019t import this chat. Try again.")""",
        "T('settings.row_unavailable', \"Couldn\\u2019t load this right now. Try again.\")) : keyTestMessage(providerLabel, j)",
        "note.textContent = res.ok ? String((res.j && res.j.message) || '') : T('xsetup.failed', "
        "'X setup failed. Nothing was saved.')",
        "await draw(out.ok ? String((out.j && out.j.message) || '') : lost)",
        "await draw(out.ok ? '' : T('xact.reset_failed', 'The count was not reset. Try again.'))",
        "await draw(out.ok ? T('xact.limits_saved', 'Limits saved.') : T('xact.limits_failed', "
        "'Your limits were not saved. Try again.'))",
    ):
        assert wired in SETTINGS_SRC, f"the keyed-first inversion is missing: {wired!r}"
    # every round-2 refusal branch warns its server detail next to the keyed display
    for label in (
        "chat export refused:", "bundle staging refused:", "bundle preview refused:",
        "bundle import refused:", "security key check refused:", "x setup bearer save refused:",
        "x setup key check refused:", "x setup key save refused:", "x action refused:",
        "x usage reset refused:", "x limits save refused:",
    ):
        hits = [ln for ln in SETTINGS_SRC.splitlines() if ("'" + label) in ln and "console.warn" in ln]
        assert hits, f"the {label!r} branch lost its console.warn of the raw detail"


def test_refusal_catches_and_not_compositions_read_one_keyed_sentence() -> None:
    """Round 3: the e.message catch family and the 'Not …' server-word compositions render
    exactly one keyed sentence; the raw text rides console.warn. e.message itself survives only
    on console lines plus the setup-checklist tfmt pinned by tests/test_setup_checklist_wiring.py:67."""
    for gone in (
        "tfmt('keys.test_failed'",
        "tfmt('keys.not_removed'",
        "tfmt('keys.not_stored'",
        "tfmt('keys.quarantine_unavailable'",
        "tfmt('keys.quarantine_retry_failed'",
        "tfmt('keys.not_deleted'",
        "'Not registered — '",
        "'Certification did not run — '",
        "'Not pinned — ' +",
        "'Refresh failed — '",
        "'Discovery state unavailable — '",
        "'Not changed — ' +",
        "'Not cleared — ' +",
        "'Could not export — '",
        "'Not saved — ' + e.message",
        "'Not switched — '",
        "'Origin not changed — '",
        "'Not enabled — '",
        "'Not proposed — '",
        "'Not withdrawn — '",
        "'Not approved — ' +",
        "'Not chosen — ' +",
        "'Budget not registered — '",
        "'Model not pinned — '",
        "'Limits not saved — '",
        "'Route store error — '",
        "'Model list unavailable — '",
        "'Sent, but VOOL reports '",
        "data.store_message || T(",
        "usage.message || T('xact.unavailable'",
        "report(res, res.ok ? okText",
        "throw new Error(result.detail",
    ):
        assert gone not in SETTINGS_SRC, f"the raw-detail display is back: {gone!r}"
    # the stored-mismatch contract sentence covers the two general stored-differs displays; the third
    # (the default email account) reads its own keyed sentence, pinned in test_email_rows_plain_words
    assert SETTINGS_SRC.count("T('settings.store_mismatch'") >= 2, (
        "the store-mismatch keyed sentence lost a wiring site"
    )
    assert "T('emailadd.default_not_made'" in SETTINGS_SRC
    # the profile run() inversion: keyed sentence on refusal, authority report only on success
    assert (
        "say(res.ok ? (plainOk ? okText : report(res, okText)) : T('settings.row_not_saved'"
        in SETTINGS_SRC
    ), "the profile action refusal no longer reads the keyed sentence"
    # e.message only on console lines, plus the pinned setup-checklist tfmt
    pinned = "tfmt('setup.checklist.not_saved', 'Not saved — {error}'"
    for line in SETTINGS_SRC.splitlines():
        if "e.message" not in line:
            continue
        assert "console.warn" in line or "console.error" in line or pinned in line, (
            f"e.message is used outside a console line: {line.strip()[:100]!r}"
        )


def test_the_page_still_boots_after_the_plain_words_sweep() -> None:
    """The rewrite stayed inside the real scripts: they still parse and boot end to end."""
    program = (
        DOM
        + "El.prototype.replaceChildren = function (...kids) { this.children = [...kids]; this.__text = ''; };\n"
        + "El.prototype.insertBefore = function (n, ref) { const i = ref ? this.children.indexOf(ref) : -1; if (i >= 0) this.children.splice(i, 0, n); else this.children.push(n); n.parentNode = this; return n; };\n"
        + "document.createElementNS = (ns, t) => new El(t);\n"
        + _routes_js({})
        + "globalThis.__defaultRoute = {'status': 200, 'body': {'ok': true, 'state': 'ready', 'sessions': []}};\n"
        + "\n".join(settings_scripts())
        + """
await globalThis.__wait(200);
await window.__voolSettings.go('keys');
await globalThis.__wait(400);
const paneBody = document.querySelector('#paneBody');
out({ paneText: (paneBody && paneBody.textContent || '').slice(0, 400), errors: [] });
"""
    )
    result = run_node(program)
    assert result["errors"] == [], result["errors"]
    assert result["paneText"], "the driven keys pane painted nothing; the boot proved nothing"
