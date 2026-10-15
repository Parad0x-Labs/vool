"""The words Settings shows when a provider's edge blocks the key check, and for a plain refusal.

The shared verifier answers ``gateway_blocked`` when a provider's edge refuses this client
before the key is judged, and its ``unauthorized`` means "request not permitted; key validity
was not established". These tests pin the WORDS the user sees in the two doors that show them
(saving a key -> ``verifyOutcomeMessage``, the Test button -> ``keyTestMessage``):

- gateway_blocked: both doors say the provider blocked the check before looking at the key and
  that the key was not judged — never "rejected" or "recognised", in English and in German;
- unauthorized: neither door claims the key is valid ("recognised the key") nor that it was
  judged bad ("rejected the key") — the request was refused and validity stays unknown.

The JavaScript is the real served page's, executed under node with a catalog-backed T/tfmt
(the same contract VOOLT/VOOLFMT give the browser); the catalog and wiring assertions run even
where node is absent.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.i18n.catalog import catalog_for, clear_catalog_cache
from core.vool_settings_page import render_vool_settings_html

EN_OUTCOME_GATEWAY = ("{label} blocked this check before looking at the key{http}. "
                      "The key was not judged. Try again later or from another network. "
                      "Nothing was stored.")
EN_TEST_GATEWAY = "{label} blocked this check before looking at the key{http}. The key was not judged."
EN_OUTCOME_UNAUTHORIZED = ("{label} refused this request{http}. The key may lack permission for it, "
                           "or may not be valid. Nothing was stored.")
EN_TEST_UNAUTHORIZED = ("{label} refused this request{http}. The key may lack permission for it, "
                        "or may not be valid.")

#: The exact payload shapes the two doors really receive: the intake verify endpoint returns the
#: verifier's outcome fields under ``outcome``; the Test button's probe maps a verdict that is
#: neither verified nor key-judged onto ``state=failed`` with the status word in ``detail``.
SAVE_GATEWAY = {"outcome": "gateway_blocked", "http_status": 403, "detail": "",
                "provider_error_code": "", "retry_after_s": None, "redirect_origin": ""}
TEST_GATEWAY = {"state": "failed", "detail": "gateway_blocked", "http_status": 403}
SAVE_UNAUTHORIZED = {"outcome": "unauthorized", "http_status": 403, "detail": "",
                     "provider_error_code": "forbidden", "retry_after_s": None, "redirect_origin": ""}
TEST_UNAUTHORIZED = {"state": "failed", "detail": "unauthorized", "http_status": 403}


def teardown_function() -> None:
    clear_catalog_cache()


# ---- the English source says the new words, with the same placeholders as ever ------------------


def test_the_english_source_carries_the_new_gateway_and_refusal_words() -> None:
    en = catalog_for("en")
    assert en.text("keys.outcome.gateway_blocked") == EN_OUTCOME_GATEWAY
    assert en.text("keys.test.gateway_blocked") == EN_TEST_GATEWAY
    assert en.text("keys.outcome.unauthorized") == EN_OUTCOME_UNAUTHORIZED
    assert en.text("keys.test.unauthorized") == EN_TEST_UNAUTHORIZED
    for key in ("keys.outcome.gateway_blocked", "keys.test.gateway_blocked",
                "keys.outcome.unauthorized", "keys.test.unauthorized"):
        assert set(re.findall(r"\{(\w+)\}", en.text(key))) == {"label", "http"}, key


def test_both_doors_route_gateway_blocked_to_the_new_keys() -> None:
    html = render_vool_settings_html(build_commit="t")
    assert "case 'gateway_blocked': return tfmt('keys.outcome.gateway_blocked'" in html
    assert "if (reason === 'gateway_blocked') return tfmt('keys.test.gateway_blocked'" in html
    # the old unauthorized claims are gone from the served words
    assert "recognised the key" not in html
    assert "rejected the key{http}. Re-check" not in html


def test_the_german_words_ride_to_the_browser() -> None:
    de = catalog_for("de")
    html = render_vool_settings_html(build_commit="t", ui_locale="de")
    for key in ("keys.outcome.gateway_blocked", "keys.test.gateway_blocked",
                "keys.outcome.unauthorized", "keys.test.unauthorized"):
        shipped = json.dumps(de.text(key), ensure_ascii=False)[1:-1]
        assert shipped in html, f"{key}: the German text is not in the bootstrap bundle"


# ---- the real page JavaScript, executed with a catalog behind T/tfmt ----------------------------


def _page_functions() -> tuple[str, str]:
    html = render_vool_settings_html(build_commit="t")

    def extract(name: str) -> str:
        match = re.search(r"^ {2}function " + name + r"\(label, j\) \{.*?^ {2}\}", html,
                          re.DOTALL | re.MULTILINE)
        assert match, f"{name} not found in the served page"
        return match.group(0)

    return extract("verifyOutcomeMessage"), extract("keyTestMessage")


def _run_page_functions(locale: str) -> dict[str, str]:
    """Execute the two functions under node; T/tfmt resolve exactly like the browser's
    VOOLT/VOOLFMT pair (locale bundle first, English fallback, then {param} filling)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the settings page script")
    messages = {key: catalog_for(locale).text(key) for key in catalog_for(locale).keys}
    verify_fn, test_fn = _page_functions()
    program = (
        "const MESSAGES = " + json.dumps(messages, ensure_ascii=False) + ";\n"
        "function T(key, fallback) { return Object.prototype.hasOwnProperty.call(MESSAGES, key) ? MESSAGES[key] : fallback; }\n"
        "function tfmt(key, fallback, params) { return String(T(key, fallback)).replace(/\\{(\\w+)\\}/g, (m, name) => (params && params[name] != null) ? String(params[name]) : m); }\n"
        + verify_fn + "\n" + test_fn + "\n"
        "console.log(JSON.stringify({\n"
        "  save_gateway: verifyOutcomeMessage('DeepSeek', " + json.dumps(SAVE_GATEWAY) + "),\n"
        "  test_gateway: keyTestMessage('DeepSeek', " + json.dumps(TEST_GATEWAY) + "),\n"
        "  save_unauthorized: verifyOutcomeMessage('DeepSeek', " + json.dumps(SAVE_UNAUTHORIZED) + "),\n"
        "  test_unauthorized: keyTestMessage('DeepSeek', " + json.dumps(TEST_UNAUTHORIZED) + ")\n"
        "}));\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=90)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"settings page harness failed under node:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _assert_no_verdict_words(rendered: str, locale: str) -> None:
    """No door may dress an unjudged or unknown-validity answer as a key verdict."""
    lowered = rendered.lower()
    if locale == "en":
        assert "reject" not in lowered, rendered
        assert "recognis" not in lowered, rendered
    else:
        assert "erkannt" not in lowered, rendered
        assert "abgelehnt" not in lowered, rendered


def test_gateway_blocked_shows_the_new_english_and_german_words() -> None:
    for locale in ("en", "de"):
        rendered = _run_page_functions(locale)
        save = rendered["save_gateway"]
        test = rendered["test_gateway"]
        catalog = catalog_for(locale)
        assert save == catalog.format("keys.outcome.gateway_blocked", label="DeepSeek", http=" (HTTP 403)"), (locale, save)
        assert test == catalog.format("keys.test.gateway_blocked", label="DeepSeek", http=" (HTTP 403)"), (locale, test)
        assert "DeepSeek" in save and "DeepSeek" in test
        _assert_no_verdict_words(save, locale)
        _assert_no_verdict_words(test, locale)
        if locale == "en":
            assert "The key was not judged." in save and "The key was not judged." in test
            assert "Nothing was stored." in save
            assert "Nothing was stored." not in test


def test_unauthorized_claims_neither_validity_nor_rejection() -> None:
    for locale in ("en", "de"):
        rendered = _run_page_functions(locale)
        _assert_no_verdict_words(rendered["save_unauthorized"], locale)
        _assert_no_verdict_words(rendered["test_unauthorized"], locale)
        catalog = catalog_for(locale)
        assert rendered["save_unauthorized"] == catalog.format(
            "keys.outcome.unauthorized", label="DeepSeek", http=" (HTTP 403, forbidden)"), (locale, rendered)
        assert rendered["test_unauthorized"] == catalog.format(
            "keys.test.unauthorized", label="DeepSeek", http=" (HTTP 403)"), (locale, rendered)
