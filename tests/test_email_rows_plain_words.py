"""Settings > Email account rows, for a person who has never heard of "graph" or "imap".

Measured on e46e353 (findings pass, 2026-10-29): each account row showed the internal provider
id in a mono font ("graph", "imap"), the state words were hardcoded English with a raw-state
fallback ("needs reconnect: its grant is no longer stored", or the bare id for anything
unknown), the default-account messages were hardcoded, one of them printed raw JSON
("VOOL stored null as the default instead."), and the widget said an unkeyed "Loading…".

The rules this file guards, in two lanes:

- source lane (runs everywhere): the shipped settings page maps every provider id to the
  service name the person picked (from the preset table the product already carries), no row
  ever renders a raw provider id or a raw state id, the unknown state falls back to one plain
  keyed sentence, the default-account strings and the email widgets' "Loading…" are keyed, and
  the inline English fallbacks equal the catalog;
- catalog lane (runs everywhere): the new and changed keys exist in English and in all 16
  shipped locales, keep their placeholders, carry the current source stamp, and never say
  "grant", "local storage" or "provider" where a person reads;

plus one browser lane (skips cleanly where no chromium build is available): rows served from
a controlled snapshot show service names, never ids, and an unknown state shows the plain
sentence from the catalog, not the raw state.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

REPO = Path(__file__).resolve().parents[1]
CATALOGS = REPO / "core" / "i18n" / "catalogs"
LOCALES = ("ar", "de", "es", "fr", "he", "hi", "ja", "ko", "lt", "pl", "pt", "ru", "tr", "uk", "vi", "zh-Hans")

#: Keys this change added (the rows' service names, default-account messages, state words).
NEW_KEYS = (
    "emailadd.service.other",
    "emailadd.use_default",
    "emailadd.default_choosing",
    "emailadd.default_not_chosen",
    "emailadd.default_refused",
    "emailadd.default_set",
    "emailadd.default_not_made",
    "email.state.configured",
    "email.state.needs_reconnect",
    "email.state.send_only",
    "email.state.read_only",
    "email.state.mismatch",
    "email.state.unreadable",
    "email.state.unknown",
)
#: Keys this change reworded ("email service" instead of "provider", no "local storage").
CHANGED_KEYS = (
    "emailadd.app_password_hint",
    "emailadd.err.managed_elsewhere",
    "emailadd.err.auth_failed",
    "emailadd.err.store_failed",
    "emailadd.err.password_charset",
    "emailadd.err.smtp_auth_failed",
    "emailadd.servers_filled",
    "emailadd.other_help",
)
TOUCHED_KEYS = NEW_KEYS + CHANGED_KEYS


def _page() -> str:
    from core.vool_settings_page import render_vool_settings_html

    return render_vool_settings_html()


def _en() -> dict[str, str]:
    return json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))["messages"]


def _placeholders(value: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", value))


# ---- source lane: the service a person picked, never its internal id ---------------------------


def test_every_row_shows_the_service_name_from_the_preset_table_not_the_provider_id() -> None:
    page = _page()
    # The row's service cell resolves through one helper fed by the preset table the product
    # already carries -- not a second hardcoded list.
    assert "emailServiceName(a.provider)" in page
    assert "EMAIL_PROVIDERS.find(p => p.id === provider)" in page
    # The raw passthrough that used to print "graph" / "imap" in a mono font is gone.
    assert "el('span', 'subtle mono', a.provider)" not in page
    presets = re.findall(r"\{ id: '([a-z]+)', name: '([^']+)'", page)
    assert ("gmail", "Gmail") in presets and ("icloud", "iCloud Mail") in presets and ("yahoo", "Yahoo Mail") in presets
    # Microsoft's sign-in is not a preset, but its person-facing name is still a service name.
    assert "return 'Outlook';" in page
    # Anything else is a plain keyed name -- never its id.
    assert "T('emailadd.service.other', 'Other email')" in page


def test_an_unknown_state_falls_back_to_one_plain_keyed_sentence_never_the_raw_id() -> None:
    page = _page()
    assert "emailStateText(a.state)" in page
    # The raw-state fallback ("EMAIL_STATE_TEXT[a.state] || a.state") is gone for good.
    assert "EMAIL_STATE_TEXT[a.state] || a.state" not in page
    assert "EMAIL_STATE_TEXT[a.state] ||" not in page
    # Known states resolve through keyed text; anything unknown gets the one plain sentence.
    assert "EMAIL_STATE_TEXT[state] ? EMAIL_STATE_TEXT[state]() : T('email.state.unknown'" in page
    assert "'email.state.unknown', 'This account is in a state VOOL does not recognise.'" in page


def test_the_default_account_messages_are_keyed_and_print_no_raw_json() -> None:
    page = _page()
    assert "el('button', 'btn email-use-default', 'Use as default')" not in page
    assert "T('emailadd.use_default', 'Use as default')" in page
    for key in ("emailadd.default_choosing", "emailadd.default_not_chosen", "emailadd.default_set"):
        assert f"Tfmt('{key}'" in page or f"tfmt('{key}'" in page
    # A person's status line never carries JSON.stringify output.
    assert "JSON.stringify(back)" not in page
    assert "T('emailadd.default_not_made', 'VOOL could not make it the default email account.')" in page


def test_the_email_widgets_loading_note_is_keyed() -> None:
    page = _page()
    # Both email widgets (accounts and recovery) said an unkeyed "Loading…" straight to the body.
    assert "body.appendChild(el('div', 'empty', 'Loading…'))" not in page
    assert page.count("T('settings.widget.loading', 'Loading…')") >= 2


# ---- source lane: the inline English fallbacks are the catalog, not a second wording ------------


@pytest.mark.parametrize("key", TOUCHED_KEYS)
def test_the_page_fallback_english_equals_the_catalog(key: str) -> None:
    page = _page()
    en = _en()
    quoted = re.escape(en[key])
    patterns = (
        rf"T\('{re.escape(key)}', '{quoted}'\)",
        rf"T\('{re.escape(key)}', \"{quoted}\"\)",
        rf"[tT]fmt\('{re.escape(key)}', '{quoted}'",
        rf"[tT]fmt\('{re.escape(key)}', \"{quoted}\"",
    )
    assert any(re.search(p, page) for p in patterns), (
        f"the page's inline English for {key} drifted from en.json -- one wording, one source"
    )


# ---- catalog lane: translated, complete, current, plain ------------------------------------------


def test_every_touched_key_exists_in_english_and_all_locales_with_placeholders_intact() -> None:
    en = _en()
    problems: list[str] = []
    for tag in ("en", *LOCALES):
        messages = json.loads((CATALOGS / f"{tag}.json").read_text(encoding="utf-8"))["messages"]
        for key in TOUCHED_KEYS:
            if key not in messages:
                problems.append(f"{tag}: {key} missing")
                continue
            if _placeholders(messages[key]) != _placeholders(en[key]):
                problems.append(f"{tag}: {key} placeholders {_placeholders(messages[key])} != {_placeholders(en[key])}")
        if tag != "en":
            for key in TOUCHED_KEYS:  # copied English is not a completed translation
                if messages.get(key) == en[key]:
                    problems.append(f"{tag}: {key} is untranslated English")
    assert not problems, "; ".join(problems)


def test_every_locale_carries_the_current_source_stamp_and_english_carries_none() -> None:
    stamp = hashlib.sha256((CATALOGS / "en.json").read_bytes()).hexdigest()
    en_data = json.loads((CATALOGS / "en.json").read_text(encoding="utf-8"))
    assert "source_catalog_sha256" not in en_data, "the English source must not stamp itself"
    for tag in LOCALES:
        data = json.loads((CATALOGS / f"{tag}.json").read_text(encoding="utf-8"))
        assert data["source_catalog_sha256"] == stamp, f"{tag}: stale source stamp"


def test_the_email_words_a_person_reads_stay_plain() -> None:
    en = _en()
    assert "local storage" not in en["emailadd.err.store_failed"].lower()
    email_values = {k: v for k, v in en.items() if k.startswith("emailadd.") or k.startswith("email.")}
    for key, value in email_values.items():
        words = re.sub(r"\{\w+\}", "", value)  # placeholder tokens never reach a person's eyes
        assert "grant" not in words.lower(), f"{key} still says \"grant\""
        assert not re.search(r"\bprovider\b", words, re.IGNORECASE), (
            f"{key} still says \"provider\" where a person reads \"email service\""
        )


# ---- browser lane: what the served page actually renders (skips without a chromium build) -------


def _snapshot() -> dict:
    """A controlled account listing: every preset service, Microsoft's sign-in, a plain IMAP
    account, an unknown service, and one state the page does not know. The snapshot is served
    through the real GET /api/email/accounts route by standing in for the account authority's
    reader (core.email_accounts.accounts_snapshot) -- the handler itself is untouched."""
    def row(account: str, provider: str, address: str, state: str, *, read: bool = True, send: bool = True,
            is_default: bool = False) -> dict:
        return {"account": account, "provider": provider, "address": address, "read": read, "send": send,
                "state": state, "is_default": is_default,
                "binding_ref": "credential:email.imap." + account, "error": ""}
    accounts = [
        row("sam.example@gmail.com-0123456789", "gmail", "sam.example@gmail.com", "configured", is_default=True),
        row("pat@icloud.com-0123456789", "icloud", "pat@icloud.com", "send_only", read=False),
        row("mel@yahoo.com-0123456789", "yahoo", "mel@yahoo.com", "read_only", send=False),
        row("alex@outlook.com-0123456789", "graph", "alex@outlook.com", "needs_reconnect"),
        row("lee@mail.example.test-0123456789", "imap", "lee@mail.example.test", "half_migrated"),
        row("sasha@mail.example.test-1122334455", "qmail", "sasha@mail.example.test", "configured"),
    ]
    selection = {
        "imap": {"kind": "imap", "account": "sam.example@gmail.com-0123456789", "ok": True, "source": "global",
                 "address": "sam.example@gmail.com", "message": ""},
        "smtp": {"kind": "smtp", "account": "sam.example@gmail.com-0123456789", "ok": True, "source": "global",
                 "address": "sam.example@gmail.com", "message": ""},
    }
    return {"accounts": accounts,
            "default": {"account": "sam.example@gmail.com-0123456789",
                        "binding_ref": "credential:email.imap.sam.example@gmail.com-0123456789",
                        "source": "global", "item_id": "", "configured": True},
            "selection": selection, "legacy_default_configured": False}


@pytest.fixture()
def rows(monkeypatch):
    import core.email_accounts as email_accounts
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get
    from tests.served_browser import launch_chromium

    monkeypatch.setattr(email_accounts, "accounts_snapshot", lambda **_: _snapshot())

    runtime = RuntimeServices(display_name="email rows fixture")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            u = urlsplit(self.path)
            if u.path == "/settings":
                from core.vool_settings_page import render_vool_settings_html
                self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(render_vool_settings_html().encode()); return
            res = dispatch_get(path=u.path, query=parse_qs(u.query), runtime=runtime, model_name="fixture",
                               client_host="127.0.0.1")
            self.send_response(res.status); self.send_header("Content-Type", res.content_type)
            self.end_headers(); self.wfile.write(res.body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    errors: list[str] = []
    try:
        page = browser.new_page(viewport={"width": 1120, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{server.server_port}/settings#email")
        page.locator(".email-account").first.wait_for()
        yield page
        assert errors == []
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()


def test_every_preset_row_names_the_service_and_no_row_shows_a_raw_provider_id(rows) -> None:
    en = _en()
    expected = {
        "sam.example@gmail.com-0123456789": "Gmail",
        "pat@icloud.com-0123456789": "iCloud Mail",
        "mel@yahoo.com-0123456789": "Yahoo Mail",
        "alex@outlook.com-0123456789": "Outlook",
        "lee@mail.example.test-0123456789": en["emailadd.service.other"],
        "sasha@mail.example.test-1122334455": en["emailadd.service.other"],
    }
    names = rows.locator(".email-account-service").all_inner_texts()
    for account, service in expected.items():
        row = rows.locator(f".email-account[data-email-account='{account}']")
        assert row.locator(".email-account-service").inner_text() == service
    # The rule, not the examples: no service cell renders a raw provider id.
    raw_ids = {"gmail", "icloud", "yahoo", "graph", "imap", "qmail"}
    assert not [n for n in names if n in raw_ids], names
    # And the ids that could leak into the row text never do.
    for account, provider in (("alex@outlook.com-0123456789", "graph"),
                              ("lee@mail.example.test-0123456789", "imap"),
                              ("sasha@mail.example.test-1122334455", "qmail")):
        text = rows.locator(f".email-account[data-email-account='{account}']").inner_text()
        assert provider not in text, f"raw provider id {provider!r} rendered: {text!r}"


def test_an_unknown_state_shows_the_one_plain_sentence_not_the_raw_state(rows) -> None:
    en = _en()
    row = rows.locator(".email-account[data-email-account='lee@mail.example.test-0123456789']")
    state = row.locator(".email-account-state")
    assert state.inner_text() == en["email.state.unknown"]
    assert "half_migrated" not in row.inner_text()
    # The known reconnect state says the sign-in is no longer stored -- plainly, no "grant".
    graph_row = rows.locator(".email-account[data-email-account='alex@outlook.com-0123456789']")
    reconnect = graph_row.locator(".email-account-state").inner_text()
    assert reconnect == en["email.state.needs_reconnect"]
    assert "grant" not in reconnect.lower()
