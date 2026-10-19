"""A fresh install with no AI model: the reply says so in plain words, and the composer says so first.

Measured on the 0.7.2 train (153148eb), fresh install, no Ollama: "tell me a joke" came back as
"I can't publish this answer: no AI model is connected and ready yet, so there is nothing that can
write a reply. ..." -- an engine word ("publish") as the first thing a new user reads, no button,
and nothing near the composer warned before the send. These tests pin the repaired behaviour:

* the refusal for "nothing can write a reply" leads with plain words in the turn's app language,
  names the way forward, keeps the cause as its own last paragraph, and is still a fixed point of
  the authorship gate (finalization admits identical duplicates only);
* the chat page puts a real "Set up a model" button under that reply (any shipped language) and
  folds the cause behind the existing "details" toggle, without changing what Copy copies;
* before any send, the setup line becomes the no-model notice exactly when nothing this chat would
  use can answer -- and not when a local model runs, a cloud model is saved for Auto, or the chat
  names a cloud model; Local Only does not count a saved cloud model.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core import local_model_presence


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    from core import os_consent_gate, runtime_paths
    from core.runtime_continuity import configure_runtime_continuity_db_path, reset_runtime_continuity_state
    from storage.db import active_default_db_path, configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    home = tmp_path / "no-model-home"
    (home / "data").mkdir(parents=True)
    monkeypatch.setenv("VOOL_HOME", str(home))
    # An Ollama endpoint that refuses connections: the real presence probe finds nothing running.
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:9")
    runtime_paths.configure_runtime_home(home)
    configure_default_db_path(home / "data" / "vool_web0_v2.db")
    reset_default_connection()
    configure_runtime_continuity_db_path(active_default_db_path())
    run_migrations()
    os_consent_gate.set_consent_override_for_tests(lambda reason: False)
    local_model_presence.reset_cache()
    yield home
    local_model_presence.reset_cache()
    os_consent_gate.set_consent_override_for_tests(None)
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


def _register_local_model(base_url: str = "http://127.0.0.1:11434") -> str:
    from storage.model_provider_manifest import ModelProviderManifest, upsert_provider_manifest

    upsert_provider_manifest(
        ModelProviderManifest(
            provider_name="ollama-local",
            model_name="qwen3:4b",
            source_type="http",
            capabilities=["summarize"],
            runtime_config={"base_url": base_url},
        )
    )
    return "ollama-local:qwen3:4b"


def _blocked_decision(model: str):
    from core.final_answer_authorship import REASON_BLOCKED_BEFORE_GENERATION, AuthorshipDecision

    return AuthorshipDecision(
        eligible=False,
        author_role="final_answer",
        task_class="general",
        selected_model=model,
        reason=REASON_BLOCKED_BEFORE_GENERATION,
    )


class _FakeOllama:
    """A real loopback endpoint answering /api/tags with one chat model."""

    def __init__(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = (
                    json.dumps({"models": [{"name": "qwen3:4b", "details": {"families": ["qwen3"]}}]}).encode()
                    if self.path == "/api/tags" else b"{}"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --- the reply ------------------------------------------------------------------------------


def test_with_nothing_running_the_refusal_is_plain_words_with_the_cause_last(home) -> None:
    from core.final_answer_authorship import NO_MODEL_NOTICE_LEAD, UNCERTIFIED_AUTHOR_NOTICE_LEAD

    text = _blocked_decision(_register_local_model()).refusal_text()
    assert text.startswith(NO_MODEL_NOTICE_LEAD), text
    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in text
    paragraphs = text.split("\n\n")
    assert len(paragraphs) == 3, paragraphs
    assert "open Setup" in paragraphs[1]
    assert paragraphs[2].startswith("Details: no local model is running on this computer"), paragraphs[2]
    for engine_word in ("publish", "certif", "runtime", "`", "ollama-local", "qwen3"):
        assert engine_word not in text, (engine_word, text)


def test_with_a_local_model_running_the_certification_refusal_is_unchanged(home, monkeypatch) -> None:
    from core.final_answer_authorship import NO_MODEL_NOTICE_LEAD, UNCERTIFIED_AUTHOR_NOTICE_LEAD

    fake = _FakeOllama()
    try:
        monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", fake.url)
        local_model_presence.reset_cache()
        text = _blocked_decision(_register_local_model(fake.url)).refusal_text()
    finally:
        fake.close()
    assert text.startswith(UNCERTIFIED_AUTHOR_NOTICE_LEAD), text
    assert NO_MODEL_NOTICE_LEAD not in text
    assert "run the local model tool certification" in text


def test_the_refusal_speaks_the_turn_language_and_every_language_is_recognised(home) -> None:
    from core.final_answer_authorship import (
        NO_MODEL_NOTICE_LEAD,
        is_authorship_refusal_text,
        no_model_notice_leads,
        no_model_notice_text,
    )
    from core.i18n.catalog import catalog_for
    from core.i18n.locales import ui_catalog_tags
    from core.i18n.turn_locale import bind_turn_locale, reset_turn_locale

    model = _register_local_model()
    token = bind_turn_locale("de")
    try:
        german = _blocked_decision(model).refusal_text()
    finally:
        reset_turn_locale(token)
    german_lead = catalog_for("de").text("chat.no_model.reply_lead")
    assert german_lead != NO_MODEL_NOTICE_LEAD and german.startswith(german_lead), german

    leads = no_model_notice_leads()
    for tag in ui_catalog_tags():
        localized = no_model_notice_text(tag)
        assert localized.split("\n\n")[0] in leads, tag
        assert is_authorship_refusal_text(localized), tag
        assert len(localized.split("\n\n")) == 3, (tag, localized)


def test_the_gate_is_a_fixed_point_over_the_no_model_refusal(home) -> None:
    from core.final_answer_authorship import (
        NO_MODEL_NOTICE_LEAD,
        gate_authored_content,
        record_authorship_decision,
    )

    model = _register_local_model()
    ctx = {"turn_id": "turn-nomodel", "cancel_turn_id": "turn-nomodel", "session_id": "sess-nomodel"}
    record_authorship_decision(ctx, _blocked_decision(model), blocked_model=model, request_text="tell me a joke")
    first, payload = gate_authored_content("Why did the chicken cross the road?", turn_id="turn-nomodel")
    assert first.startswith(NO_MODEL_NOTICE_LEAD), first
    assert payload.get("publication") == "refused", payload
    second, payload = gate_authored_content(first, turn_id="turn-nomodel")
    assert second == first, "the gate is not a fixed point over its own no-model refusal"
    assert payload.get("publication") == "refused_idempotent", payload


def test_model_bytes_that_only_quote_the_lead_are_still_refused(home) -> None:
    """The lead is recognised at the START of the bytes only: a model answer that quotes it in the
    middle is model prose, and the uncertified author's prose still may not ship."""
    from core.final_answer_authorship import (
        NO_MODEL_NOTICE_LEAD,
        gate_authored_content,
        record_authorship_decision,
    )

    model = _register_local_model()
    ctx = {"turn_id": "turn-quote", "cancel_turn_id": "turn-quote", "session_id": "sess-quote"}
    record_authorship_decision(ctx, _blocked_decision(model), blocked_model=model, request_text="say it")
    quoted = f"Sure! Here it is: {NO_MODEL_NOTICE_LEAD} -- funny, right?"
    shipped, payload = gate_authored_content(quoted, turn_id="turn-quote")
    assert payload.get("publication") == "refused", payload
    assert shipped != quoted and shipped.startswith(NO_MODEL_NOTICE_LEAD)


# --- the page -------------------------------------------------------------------------------


def test_the_page_carries_the_lead_of_every_shipped_language() -> None:
    from core.final_answer_authorship import LEGACY_NO_MODEL_NOTICE_LEAD, no_model_notice_leads
    from core.vool_chat_page import render_vool_chat_html

    html = render_vool_chat_html(ui_locale="ar")
    assert "/*__VOOL_NO_MODEL_LEADS__*/" not in html
    marker = "var NO_MODEL_LEADS = "
    start = html.index(marker) + len(marker)
    embedded = json.loads(html[start:html.index(";\n", start)])
    assert embedded == [*no_model_notice_leads(), LEGACY_NO_MODEL_NOTICE_LEAD]
    assert len(embedded) >= 17


_STATE_NO_MODEL = {"local_model_running": False, "local_model_reason": "no_local_endpoint_answered",
                   "model": "", "decision": "", "candidates": [], "needs_choice": False}


def _setup_state(cloud_default, *, show_chat_line=True):
    return {"ok": True, "steps": [], "total": 4, "done_count": 0, "skipped": [], "complete": False,
            "dismissed": False, "show_entry": True, "show_chat_line": show_chat_line, "first_undone": "thinking",
            "autonomy_mode": "", "cloud_default": cloud_default}


@pytest.fixture(scope="module")
def page_rig():
    from core.vool_chat_page import render_vool_chat_html
    from tests import served_browser

    html = render_vool_chat_html()
    state = {"setup": _setup_state(dict(_STATE_NO_MODEL))}

    def route(r):
        req = r.request
        if req.resource_type == "document":
            r.fulfill(status=200, content_type="text/html", body=html)
        elif "/api/setup/state" in req.url:
            r.fulfill(status=200, content_type="application/json", body=json.dumps(state["setup"]))
        else:
            r.fulfill(status=200, content_type="application/json", body="{}")

    manager, browser = served_browser.launch_chromium()
    page = browser.new_page(viewport={"width": 1280, "height": 820})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", route)
    page.goto("http://127.0.0.1:9/chat")
    page.wait_for_function("() => document.getElementById('setupLine').dataset.loaded === '1'")
    # Instrument the window opener only, so the click is observed without leaving the page.
    page.evaluate("() => { window.__opened = []; window.open = (u) => { window.__opened.push(String(u)); return null; }; }")
    yield page, state, errors
    browser.close()
    manager.stop()


_NOTICE = """() => { const l = document.getElementById('setupLine');
  const vis = (id) => { const e = document.getElementById(id); return !!e && e.getClientRects().length > 0; };
  return { shown: !l.hidden && l.getClientRects().length > 0, noModel: l.classList.contains('no-model-line'),
           notice: vis('noModelLineText'), button: vis('noModelLineOpen'), plain: vis('setupLineText'),
           x: vis('setupLineHide') }; }"""


def _reload_state(page, state, cloud_default, *, show_chat_line=True, model="vool"):
    state["setup"] = _setup_state(cloud_default, show_chat_line=show_chat_line)
    page.evaluate("(m) => setModelValue(m)", model)
    page.evaluate("() => setupLineLoad()")
    page.wait_for_timeout(300)
    return page.evaluate(_NOTICE)


def test_the_notice_shows_before_the_first_send_and_opens_the_model_step(page_rig) -> None:
    page, state, errors = page_rig
    n = _reload_state(page, state, dict(_STATE_NO_MODEL))
    assert n == {"shown": True, "noModel": True, "notice": True, "button": True, "plain": False, "x": False}, n
    page.click("#noModelLineOpen")
    assert page.evaluate("() => window.__opened.pop()") == "/setup#step=thinking"
    assert not errors, errors


def test_the_notice_shows_even_when_the_setup_reminder_was_dismissed(page_rig) -> None:
    page, state, _ = page_rig
    n = _reload_state(page, state, dict(_STATE_NO_MODEL), show_chat_line=False)
    assert n["shown"] and n["noModel"], n


def test_the_notice_stays_away_whenever_something_can_answer(page_rig) -> None:
    page, state, _ = page_rig
    running = dict(_STATE_NO_MODEL, local_model_running=True, local_model_reason="running")
    n = _reload_state(page, state, running)
    assert not n["noModel"] and n["shown"] and n["plain"], ("local model running", n)
    n = _reload_state(page, state, running, show_chat_line=False)
    assert not n["shown"], ("local model running, reminder dismissed", n)

    cloud = [{"id": "openai-byok:gpt-4.1-mini", "label": "gpt-4.1-mini", "provider": "openai-byok"}]
    saved = dict(_STATE_NO_MODEL, decision="use_cloud", model=cloud[0]["id"], candidates=cloud)
    n = _reload_state(page, state, saved)
    assert not n["noModel"], ("cloud model saved for Auto", n)

    key_only = dict(_STATE_NO_MODEL, candidates=cloud, needs_choice=True)
    n = _reload_state(page, state, key_only)
    assert n["noModel"], ("a key alone: Auto still has nothing chosen", n)
    n = _reload_state(page, state, key_only, model=cloud[0]["id"])
    assert not n["noModel"], ("this chat names a cloud model", n)

    # A saved cloud model whose key was removed is no longer a candidate: nothing can answer.
    stale = dict(_STATE_NO_MODEL, decision="use_cloud", model=cloud[0]["id"], candidates=[])
    n = _reload_state(page, state, stale)
    assert n["noModel"], ("saved model no longer registered", n)


def test_local_only_does_not_count_a_saved_cloud_model(page_rig) -> None:
    page, state, _ = page_rig
    cloud = [{"id": "openai-byok:gpt-4.1-mini", "label": "gpt-4.1-mini", "provider": "openai-byok"}]
    saved = dict(_STATE_NO_MODEL, decision="use_cloud", model=cloud[0]["id"], candidates=cloud)
    n = _reload_state(page, state, saved, model="vool-local-only")
    assert n["noModel"] and n["shown"], n


def test_an_unreadable_state_shows_no_notice(page_rig) -> None:
    page, state, _ = page_rig
    n = _reload_state(page, state, {"check_failed": "OSError", "local_model_running": None, "candidates": []})
    assert not n["noModel"], n


def test_the_reply_gets_a_real_button_and_folded_details_in_any_language(page_rig) -> None:
    from core.final_answer_authorship import no_model_notice_text

    page, _, errors = page_rig
    probe = """(text) => { const el = document.createElement('div'); document.body.appendChild(el);
      renderAssistantContent(el, text, {});
      const raw = el.querySelector('.no-model-detail .prov-raw');
      const out = { raw: el.dataset.raw, button: (el.querySelector('.no-model-setup') || {}).textContent || '',
        details: !!el.querySelector('.no-model-detail .prov-more'), detailHidden: raw ? raw.hidden : null,
        detailText: raw ? raw.textContent : '' };
      const b = el.querySelector('.no-model-setup'); if (b) b.click();
      out.opened = window.__opened.pop() || ''; el.remove(); return out; }"""
    for tag in ("en", "de", "ar"):
        text = no_model_notice_text(tag)
        out = page.evaluate(probe, text)
        assert out["raw"] == text, (tag, "Copy must stay the reply itself", out)
        assert out["button"] == "Set up a model", (tag, out)
        assert out["details"] and out["detailHidden"] is True, (tag, out)
        assert out["detailText"] == text.split("\n\n")[-1], (tag, out)
        assert out["opened"] == "/setup#step=thinking", (tag, out)
    # The 0.7.2-train wording, still in saved chats, gets the same button on reload.
    legacy = (
        "I can't publish this answer: no AI model is connected and ready yet, so there is nothing that can "
        "write a reply.\n\nTo connect one, open Setup and choose a model under \u201cWhere should it think?\u201d. "
        "If you already have a key for an online service, you can also pick a cloud model in the model "
        "selector at the top of the chat.\n\nDetails: no local model is running on this computer right now."
    )
    out = page.evaluate(probe, legacy)
    assert out["button"] == "Set up a model" and out["detailHidden"] is True, out
    assert out["raw"] == legacy, out
    ordinary = page.evaluate(probe, "Here is a joke about a cat.\n\nIt was purr-fect.\n\nThe end.")
    assert ordinary["button"] == "" and not ordinary["details"], ordinary
    quoted = page.evaluate(probe, "You said: I can't answer yet because no AI model is set up on this computer.")
    assert quoted["button"] == "", quoted
    assert not errors, errors
