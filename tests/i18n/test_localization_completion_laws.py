"""The localization-completion laws: fragments and technical labels cannot ship English-only,
and plural categories cannot be flattened.

Guards in this file answer one concrete question each:

- CLDR plural selection: the formatter (and the page's mirrored JS) picks the branch by the
  rendering locale's cardinal category — Polish ``few``/``many``, Arabic's six, Hebrew's
  ``two`` — never by a flattened ``n === 1`` test; server and browser format identically;
- a plural translation that drops a category its locale distinguishes is REJECTED at load
  time (silent flattening is how the defect shipped the first time);
- a translated word that precedes ``{n}`` inside a branch is text, not a branch header
  (the Lithuanian ``pakeistas {n} failas`` regression);
- every string the three settings fragments and the activity technical view render resolves
  through a catalog key, so a new label cannot bypass the catalog — and the fragments really
  render localized text through a real bundle, not only carry keys;
- the copied failure report exports CANONICAL field ids, so pasted evidence is
  locale-independent while the rendered labels localize.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.calendar_settings_fragment import _JS as CAL_JS
from core.i18n.catalog import (
    CATALOGS_DIR,
    MessageCatalog,
    _branches,
    _extract_compounds,
    _validate_locale_entry,
    catalog_for,
    clear_catalog_cache,
    format_message,
)
from core.i18n.plurals import integer_categories, plural_category
from core.notification_settings_fragment import _JS as NOTIF_JS
from core.settings_extras_fragment import _EXTRAS_JS

REPO = Path(__file__).resolve().parents[2]
CHAT_PAGE = (REPO / "core/vool_chat_page.py").read_text(encoding="utf-8")

PLURAL_KEYS = (
    "activity.operations",
    "activity.rollup.actions",
    "activity.rollup.changes",
    "activity.rollup.commands",
    "activity.rollup.edits",
    "activity.rollup.reads",
    "activity.rollup.searches",
    "activity.rollup.tests",
    "activity.rollup.tools",
    "activity.worklog",
)


def _en_messages() -> dict[str, str]:
    return json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]


def _node() -> str:
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


def _run_js(program: str) -> dict:
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([_node(), path], capture_output=True, text=True, timeout=90)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr[:800]
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---- CLDR plural selection ---------------------------------------------------------------------


def test_plural_category_matches_cldr_cardinals_for_integer_counts() -> None:
    probes = {
        # (locale, count) -> category — transcribed from CLDR plurals.xml cardinals.
        ("pl", 1): "one", ("pl", 2): "few", ("pl", 4): "few", ("pl", 5): "many",
        ("pl", 12): "many", ("pl", 22): "few", ("pl", 0): "many", ("pl", 100): "many",
        ("ru", 1): "one", ("ru", 3): "few", ("ru", 11): "many", ("ru", 21): "one", ("ru", 0): "many",
        ("uk", 2): "few", ("uk", 14): "many", ("uk", 23): "few",
        ("ar", 0): "zero", ("ar", 1): "one", ("ar", 2): "two", ("ar", 3): "few",
        ("ar", 10): "few", ("ar", 11): "many", ("ar", 99): "many", ("ar", 100): "other",
        ("ar", 101): "other", ("ar", 103): "few", ("ar", 111): "many",
        ("he", 0): "other", ("he", 1): "one", ("he", 2): "two", ("he", 3): "other", ("he", 7): "other",
        ("lt", 1): "one", ("lt", 3): "few", ("lt", 10): "other", ("lt", 11): "other",
        ("lt", 21): "one", ("lt", 0): "other",
        ("es", 1): "one", ("es", 0): "other", ("es", 1000000): "many",
        ("fr", 0): "one", ("fr", 1): "one", ("fr", 2): "other", ("fr", 2000000): "many",
        ("pt", 0): "one", ("pt", 1000000): "many",
        ("hi", 0): "one", ("hi", 1): "one", ("hi", 2): "other",
        ("vi", 0): "one", ("vi", 1): "one", ("vi", 2): "other",
        ("de", 1): "one", ("de", 2): "other", ("en", 0): "other", ("tr", 1): "one",
        ("ja", 1): "other", ("ko", 1): "other", ("zh-Hans", 1): "other",
    }
    wrong = [
        f"{loc} n={n} -> {plural_category(loc, n)} (want {want})"
        for (loc, n), want in probes.items()
        if plural_category(loc, n) != want
    ]
    assert not wrong, "; ".join(wrong)


def test_format_message_selects_branches_by_locale_category() -> None:
    template = "{n, plural, one {{n} plik} few {{n} pliki} many {{n} plików} other {{n} pliku}}"
    assert format_message(template, {"n": 1}, locale="pl") == "1 plik"
    assert format_message(template, {"n": 2}, locale="pl") == "2 pliki"
    assert format_message(template, {"n": 5}, locale="pl") == "5 plików"
    assert format_message(template, {"n": 22}, locale="pl") == "22 pliki"
    # English keeps its historical behavior (the default locale is a compatibility seam).
    en_tpl = "{n, plural, one {{n} file} other {{n} files}}"
    assert format_message(en_tpl, {"n": 1}) == "1 file"
    assert format_message(en_tpl, {"n": 2}) == "2 files"


def test_the_catalog_renders_repaired_plurals_in_richer_locales() -> None:
    clear_catalog_cache()
    checks = [
        ("pl", "activity.rollup.actions", 2, "2 działania"),
        ("pl", "activity.rollup.actions", 5, "5 działań"),
        ("pl", "activity.rollup.actions", 1, "1 działanie"),
        ("ru", "activity.rollup.changes", 3, "изменено 3 файла"),
        ("ru", "activity.rollup.changes", 5, "изменено 5 файлов"),
        ("ar", "activity.rollup.changes", 0, "لم يغيّر أي ملف"),
        ("ar", "activity.rollup.changes", 2, "غيّر ملفين"),
        ("ar", "activity.rollup.changes", 3, "غيّر 3 ملفات"),
        ("he", "activity.rollup.changes", 2, "שינה שני קבצים"),
        ("lt", "activity.rollup.actions", 3, "3 veiksmai"),
        ("lt", "activity.rollup.actions", 10, "10 veiksmų"),
    ]
    wrong = [
        f"{loc} {key} n={n}: got {catalog_for(loc).format(key, n=n)!r}, wanted {want!r}"
        for loc, key, n, want in checks
        if catalog_for(loc).format(key, n=n) != want
    ]
    assert not wrong, "; ".join(wrong)


def test_every_plural_translation_carries_its_locales_full_category_set() -> None:
    """A locale's plural translations must cover every category an integer count reaches —
    the loader rejects flattened sets, and completeness is asserted so a rejection cannot
    hide behind an English fallback."""
    clear_catalog_cache()
    problems: list[str] = []
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        tag = path.stem
        if tag == "en":
            continue
        catalog = MessageCatalog(tag)
        reachable = integer_categories(tag)
        for key in PLURAL_KEYS:
            text = catalog._messages.get(key)
            if text is None or key not in catalog.locale_keys:
                problems.append(f"{tag}: {key} missing/rejected")
                continue
            for _name, kind, body, _s, _e in _extract_compounds(text):
                if kind != "plural":
                    continue
                missing = reachable - set(_branches(body, kind))
                if missing:
                    problems.append(f"{tag}: {key} drops {sorted(missing)}")
    assert not problems, "; ".join(problems)


def test_a_flattened_plural_translation_is_rejected_at_load_time() -> None:
    source = "{n, plural, one {{n} file} other {{n} files}}"
    flattened_pl = "{n, plural, one {{n} plik} other {{n} plików}}"  # no 'few'/'many'
    reason = _validate_locale_entry("k", flattened_pl, source, locale="pl")
    assert reason and "few" in reason and "many" in reason, reason
    # A full-category Polish translation is accepted.
    full_pl = "{n, plural, one {{n} plik} few {{n} pliki} many {{n} plików} other {{n} pliku}}"
    assert _validate_locale_entry("k", full_pl, source, locale="pl") is None
    # An unknown category name (a typo, or a word mistaken for a header) is rejected.
    typo = "{n, plural, one {{n} file} onw {{n} files} other {{n} files}}"
    assert _validate_locale_entry("k", typo, source, locale="en") is not None


def test_a_translated_word_before_the_count_is_branch_text_not_a_category() -> None:
    """Lithuanian participles precede {n} ("pakeistas {n} failas"); the parser must read the
    branches by category name, not mint a branch named "pakeistas"."""
    text = "{n, plural, one {pakeistas {n} failas} few {pakeisti {n} failai} other {pakeista {n} failų}}"
    [(name, kind, body, _s, _e)] = _extract_compounds(text)
    assert name == "n" and kind == "plural"
    branches = _branches(body, kind)
    assert set(branches) == {"one", "few", "other"}
    assert branches["one"] == "pakeistas {n} failas"
    # And the formatter renders it without losing the participle.
    assert format_message(text, {"n": 1}, locale="lt") == "pakeistas 1 failas"
    assert format_message(text, {"n": 3}, locale="lt") == "pakeisti 3 failai"


def test_the_page_js_formatter_mirrors_the_server_plural_selection() -> None:
    """Every shipped locale × probe count, formatted by the page's own VOOLFMT under node,
    byte-matches core.i18n.catalog.format_message — the server/browser parity law."""
    from core.i18n.page_bundle import i18n_bootstrap_js

    clear_catalog_cache()
    locales = sorted(p.stem for p in CATALOGS_DIR.glob("*.json"))
    counts = [0, 1, 2, 3, 5, 11, 22, 100, 1000000]
    template = "{n, plural, zero {ZERO} one {ONE} two {TWO} few {FEW} many {MANY} other {OTHER}}"
    program = (
        "globalThis.window = globalThis;\n"
        "globalThis.document = { readyState: 'loading', addEventListener() {},"
        " querySelectorAll() { return []; }, getElementById() { return null; } };\n"
        + i18n_bootstrap_js("en")
        + "\nconst out = {};\n"
        + json.dumps(locales)
        + ".forEach(function (loc) {\n"
        + "  out[loc] = {};\n"
        + json.dumps([str(c) for c in counts])
        + ".forEach(function (n) {\n"
        + f"    out[loc][n] = VOOLFMT({json.dumps(template)}, {{ n: parseInt(n, 10) }}, loc);\n"
        + "  });\n"
        + "});\nconsole.log(JSON.stringify(out));\n"
    )
    rendered = _run_js(program)
    mismatches = []
    for loc in locales:
        for n in counts:
            want = format_message(template, {"n": n}, locale=loc)
            got = rendered[loc][str(n)]
            if got != want:
                mismatches.append(f"{loc} n={n}: js={got!r} py={want!r}")
    assert not mismatches, "; ".join(mismatches[:6])


# ---- the fragments and the technical view cannot ship English-only ------------------------------


def test_every_fragment_catalog_reference_resolves() -> None:
    """Every key the three fragments look up through their VOOLT helpers exists in the
    English source — a missing key would render the fallback English silently everywhere."""
    messages = _en_messages()
    referenced: set[str] = set()
    for source in (NOTIF_JS, CAL_JS, _EXTRAS_JS):
        referenced.update(
            re.findall(r"[NCX]T?F?\(\s*'((?:vns|vcs|vx|notif)\.[a-z0-9_.]+)'", source)
        )
    assert len(referenced) > 80, f"parser rot: only {len(referenced)} fragment keys found"
    missing = sorted(referenced - set(messages))
    assert not missing, f"fragment keys without catalog entries: {missing}"


def test_every_activity_technical_field_has_its_label_key() -> None:
    messages = _en_messages()
    table = re.search(r"const ACTIVITY_DETAIL_FIELDS = \[(.*?)\n\];", CHAT_PAGE, re.DOTALL)
    assert table, "ACTIVITY_DETAIL_FIELDS not found in the chat page"
    fields = re.findall(r"\['([a-z0-9_]+)', '[^']*'\]", table.group(1))
    assert len(fields) > 40, f"ACTIVITY_DETAIL_FIELDS parse found {len(fields)} entries — parser rot"
    missing = sorted(f"activity.field.{f}" for f in fields if f"activity.field.{f}" not in messages)
    assert not missing, f"technical-view fields without label keys: {missing}"
    for table_name in ("EMPTY_REPLY_LABELS", "VERIFY_LABELS", "USEPOD_DETAIL_LABELS"):
        block = re.search(rf"const {table_name} = \{{(.*?)\n\}};", CHAT_PAGE, re.DOTALL)
        assert block, f"{table_name} not found"
        ids = re.findall(r"'([a-z0-9_.]+)':", block.group(1))
        assert ids, f"{table_name} parse rot"
        gone = [i for i in ids if f"activity.field.{i}" not in messages]
        assert not gone, f"{table_name} ids without keys: {gone}"
    for extra in (
        "activity.field.not_reported",
        "activity.field.not_reported_by_provider",
        "activity.review_prices",
        "activity.review_prices_note",
    ):
        assert extra in messages, extra


# A DOM complete enough to run the fragments' mount paths: elements store what they are
# given, fetch is stubbed per surface, and the interval self-clears (body.contains → false).
_DOM_STUB = """
function elem(tag) {
  return { tag: tag, children: [], attrs: {}, _text: '', _html: '', className: '',
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; }, addEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; this._html = ''; },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); } };
}
globalThis.document = { createElement: elem, body: elem('body'), getElementById() { return null; },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  readyState: 'complete' };
globalThis.document.body.contains = function () { return false; };
globalThis.window = globalThis;
"""


def test_the_fragments_render_localized_through_the_real_bundle() -> None:
    """Proof the wiring RESOLVES through VOOLT (not only that keys exist): each fragment's
    renderer runs under node with the real Lithuanian bundle published as VOOLT and must
    produce Lithuanian text where the catalog carries it."""
    clear_catalog_cache()
    catalog = catalog_for("lt")
    bundle = {"messages": dict(catalog._messages), "english": {}, "tag": "lt"}
    stub = (
        "const B = " + json.dumps(bundle, ensure_ascii=False) + ";\n"
        "globalThis.VOOLT = function (key) { var t = B.messages[key];"
        " return (t === undefined || t === '') ? key : t; };\n"
        "globalThis.VOOLFMT = function (t, p) {"
        " return String(t).replace('{auth}', (p && p.auth) || '')"
        ".replace('{name}', (p && p.name) || '')"
        ".replace('{binding}', (p && p.binding) || '')"
        ".replace('{when}', (p && p.when) || '')"
        ".replace('{minutes}', (p && p.minutes) || '')"
        ".replace('{reason}', (p && p.reason) || ''); };\n"
    )
    fetch_stub = """
globalThis.fetch = function (url) {
  var prefs = { preferences: { native_notifications: true, sound: false, lock_screen: 'full',
    late_grace_minutes: 10, quiet_hours: { enabled: false, start: '22:00', end: '07:00' } } };
  var native = { ok: true, connected: true, authorization: 'authorized', requests: [], explanation: '' };
  var accounts = { ok: true, accounts: [], bindings: [],
    providers: { caldav: { label: 'CalDAV', connection: 'a password', events: 'events', links: 'links' } } };
  var payload = String(url).indexOf('native/status') !== -1 ? native
    : (String(url).indexOf('notifications/preferences') !== -1 ? prefs : accounts);
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } });
};
"""
    checks = {
        # fragment source → (program body, {field: expected Lithuanian substring})
        "notification": (
            NOTIF_JS,
            "var host = elem('div'); document.body.appendChild(host);"
            "VoolNotificationSettings.mountInto(host);"
            "await new Promise(function (r) { setTimeout(r, 30); });"
            "var mac = host.children[0].children[1];"
            "out.title = mac.children[0].textContent;"
            "out.toggle = mac.children[1].children[1].textContent;"
            "out.auth_line = mac.children[2].textContent;",
            {
                "title": "macOS pranešimai",
                "toggle": "Siųsti ir VOOL įspėjimus kaip macOS pranešimus",
                "auth_line": "macOS leidimas yra leidžiama",
            },
        ),
        "calendar": (
            CAL_JS,
            "var host = elem('div'); document.body.appendChild(host);"
            "VoolCalendarSettings.mountInto(host);"
            "await new Promise(function (r) { setTimeout(r, 30); });"
            "var root = host.children[0];"
            "out.note = root.children[0].textContent;"
            "out.add_title = root.children[3].children[0].textContent;",
            {
                "note": "VOOL skaito tik jūsų pasirinktus kalendorius",
                "add_title": "Pridėti kalendorių paskyrą",
            },
        ),
        "extras": (
            _EXTRAS_JS,
            "var host = elem('div'); document.body.appendChild(host);"
            "VoolSettingsExtras.mountInto(host, 'privacy');"
            "await new Promise(function (r) { setTimeout(r, 30); });"
            "out.privacy = host.children[0].innerHTML;",
            {"privacy": "Privatumas ir duomenys"},
        ),
    }
    problems: list[str] = []
    for name, (source, body, expects) in checks.items():
        program = (
            stub + _DOM_STUB + fetch_stub + source
            + "\nglobalThis.out = {};\n(async function () {\nconst out = globalThis.out;\n"
            + body
            + "\n})();\nawait new Promise(function (r) { setTimeout(r, 120); });\n"
            + "console.log(JSON.stringify(globalThis.out));\n"
        )
        rendered = _run_js(program)
        for field, want in expects.items():
            if want not in str(rendered.get(field, "")):
                problems.append(f"{name}.{field}: {rendered.get(field)!r} lacks {want!r}")
    assert not problems, "; ".join(problems)


def test_the_copied_failure_report_exports_canonical_field_ids() -> None:
    """The export names facts by canonical id (tool_name), never by the localized label, so a
    pasted report is comparable across app languages while the rendered panel localizes."""
    from tests.chat_page_js_harness import DOM, run_node, script

    result = run_node(
        DOM + "\n" + script() + "\n"
        "const report = turnFailureReport([{event_type: 'tool_failed', tool_name: 'search_web',"
        " reason: 'boom', error_kind: 'network'}], 'abc123');\n"
        "out({ report: report });\n"
    )
    report = result["report"]
    assert "tool_name: search_web" in report, report
    assert "error_kind: network" in report, report
    # The human label never leaks into the export.
    assert "tool: search_web" not in report
    assert "error: network" not in report
