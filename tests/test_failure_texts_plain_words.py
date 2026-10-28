"""Person-facing failure texts carry no paths, ids, model names or engineering words.

Every row here pins a text rewritten in repair/error-plain-words-20261028: the English
catalog value must state what failed in plain words, keep the placeholders a person
needs ({label}) and drop the ones that leak engineering detail ({status}, {http},
{error}); every other locale must keep the same placeholder set so the loader still
accepts it; and the served pages must carry the new inline fallbacks, never the old
wording. Banned in the person-facing text: "/", "`", "HTTP ", "127.0.0.1", model ids,
exception class names.
"""
from __future__ import annotations

import re

import pytest

from core.i18n.catalog import CATALOGS_DIR, catalog_for, clear_catalog_cache, placeholders_of
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html

BANNED = ("/", "`", "HTTP ", "127.0.0.1")

#: key -> params that stay in the person-facing text (rendered to prove plainness).
PLAIN_CATALOG_ROWS = {
    "attach.doc_gone_http": {},
    "attach.upload_failed_http": {},
    "chat.toast_project_failed": {},
    "fault.timeout.action": {},
    "fault.tool_unavailable.message": {},
    "keyguide.still_loading": {},
    "keys.outcome.gateway_blocked": {"label": "DeepSeek"},
    "mode.activate_failed": {"label": "Local Only"},
    "msg.copy_failed": {},
    "msg.pdf_failed": {},
    "radar.save_failed": {},
    "receipts.unavailable": {},
    "run.connection_error": {},
    "search_err.unreachable": {},
    "session.project_add_failed": {},
    "settings.widget.unavailable": {},
    "setup.checklist.not_saved": {},
    "update.failed": {},
    "update.unavailable": {},
}

#: column labels and event labels on the activity surfaces.
PLAIN_LABEL_ROWS = (
    "activity.ev.task_failed",
    "activity.field.error",
    "activity.field.error_class",
    "activity.field.error_kind",
    "activity.field.failure_class",
    "activity.field.failure_reason",
)

#: keys whose placeholder set changed: every locale must match English or the loader
#: rejects the entry and the locale silently falls back.
PLACEHOLDER_SYNCED_KEYS = (
    "attach.doc_gone_http",
    "attach.upload_failed_http",
    "keys.outcome.gateway_blocked",
    "mode.activate_failed",
    "radar.save_failed",
    "session.project_add_failed",
    "settings.widget.unavailable",
    "setup.checklist.not_saved",
)


def teardown_function() -> None:
    clear_catalog_cache()


def assert_plain(text: str) -> None:
    for banned in BANNED:
        assert banned not in text, f"{banned!r} leaked into: {text!r}"
    assert not re.search(r"\b\d{3}\b", text), f"status code leaked into: {text!r}"
    assert not re.search(r"\w+\.\w+[-\w]*:\d+\w*", text), f"model id leaked into: {text!r}"


@pytest.mark.parametrize("key, params", sorted(PLAIN_CATALOG_ROWS.items()))
def test_catalog_failure_text_is_plain_words(key: str, params: dict) -> None:
    text = catalog_for("en").format(key, **params)
    assert_plain(text)
    lowered = text.lower()
    assert any(
        word in lowered
        for word in ("failed", "could not", "couldn", "not available", "unavailable",
                     "no longer", "did not load", "not saved", "try again", "blocked",
                     "slow")
    ), f"{key} no longer states what went wrong: {text!r}"


def test_gateway_blocked_keeps_the_service_name_but_drops_the_code() -> None:
    text = catalog_for("en").format("keys.outcome.gateway_blocked", label="DeepSeek")
    assert "DeepSeek" in text
    assert "403" not in text and "{http}" not in text
    assert "The key was not judged." in text


def test_upload_and_doc_gone_texts_carry_no_status_code() -> None:
    en = catalog_for("en")
    assert "{status}" not in en.text("attach.upload_failed_http")
    assert "{status}" not in en.text("attach.doc_gone_http")
    assert "site" in en.text("attach.doc_gone_http")


@pytest.mark.parametrize("key", PLAIN_LABEL_ROWS)
def test_activity_labels_are_plain_words(key: str) -> None:
    text = catalog_for("en").text(key)
    assert_plain(text)
    assert text.strip(), key


@pytest.mark.parametrize("key", PLACEHOLDER_SYNCED_KEYS)
def test_every_locale_keeps_the_same_placeholders_as_english(key: str) -> None:
    en_placeholders = placeholders_of(catalog_for("en").text(key))
    assert "status" not in en_placeholders and "http" not in en_placeholders \
        and "error" not in en_placeholders, key
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        if path.stem == "en":
            continue
        catalog = catalog_for(path.stem)
        locale_text = catalog._messages.get(key)
        assert locale_text is not None, f"{path.stem} lost {key}"
        assert placeholders_of(locale_text) == en_placeholders, (path.stem, key, locale_text)
        assert not catalog.diagnostic.rejected_keys.get(key), (path.stem, key)


def test_chat_page_serves_the_new_fallbacks_not_the_old_ones() -> None:
    html = render_vool_chat_html(build_commit="t")
    for fragment in (
        "'This document is no longer available on the site.'",
        "'The upload failed. Try again in a moment.'",
        "'Could not turn on {label}. It was not changed. Try again.'",
        "'Receipts are not available right now. Try again in a moment.'",
        "'Update failed. Try again later.'",
        "'Updates are not available right now. Try again later.'",
        "'Connection problem. Check the internet, then try again.'",
        "'Copy failed. Try again.'",
        "'PDF export failed. Try again.'",
        "'VOOL could not reach the search service; the internet may be off or the network may be blocking it. Check the connection and try again.'",
        "'Could not put this chat in the project, so it is not isolated there. Try again.'",
    ):
        assert fragment in html, fragment
    for gone in (
        "'This document is no longer available (HTTP {status}).'",
        "'Upload failed (HTTP {status}).'",
        "Could not activate {label}: {error}",
        "Could not reach the provider from this machine",
        "it is NOT isolated",
        "'Connection error'",
        "'Receipts unavailable.'",
        "'Updates unavailable'",
    ):
        assert gone not in html, gone


def test_settings_page_serves_the_new_fallbacks_not_the_old_ones() -> None:
    html = render_vool_settings_html(build_commit="t")
    for fragment in (
        "T('settings.widget.unavailable', 'This section could not load. Try again.'",
        "T('setup.checklist.not_saved', 'Not saved. Try again.'",
        "'{label} blocked this check before it reached the key service.",
    ):
        assert fragment in html, fragment
    for gone in (
        "Unavailable — {error}",
        "Not saved — {error}",
        "before looking at the key{http}. The key was not judged. Try again later",
    ):
        assert gone not in html, gone


# ---- the worst-30 rows: driven through the functions that build them -----------------------------

from core.agent_runtime.fast_paths_pdf import _failure_text  # noqa: E402
from core.agent_runtime.fast_paths_utility import ui_command_fast_path  # noqa: E402
from core.agent_runtime.provider_failure import (  # noqa: E402
    EMPTY_CHOICES,
    EMPTY_CONTENT,
    MISSING_USAGE,
    OUTPUT_BUDGET_EXHAUSTED,
    PROVIDER_ERROR,
    ProviderFailure,
    REASONING_ONLY,
    UNREACHABLE,
    UNUSABLE_ARTIFACT,
)

#: R010-R017 plus the missing-usage sibling: a raw detail and a model id go in, only
#: plain words may come out, whatever the transport actually said.
PROVIDER_FAILURE_KINDS = (
    UNREACHABLE,
    PROVIDER_ERROR,
    EMPTY_CHOICES,
    EMPTY_CONTENT,
    REASONING_ONLY,
    OUTPUT_BUDGET_EXHAUSTED,
    MISSING_USAGE,
    UNUSABLE_ARTIFACT,
)

NASTY_DETAIL = "qwen3.5:35b-a3b via http://127.0.0.1:11434 [Errno 61] ConnectionRefusedError"


@pytest.mark.parametrize("kind", PROVIDER_FAILURE_KINDS)
def test_provider_failure_sentence_stays_plain_whatever_the_detail(kind: str) -> None:
    sentence = ProviderFailure(kind, detail=NASTY_DETAIL).operator_sentence(
        model_label="qwen3.5:35b-a3b", phase="draft",
    )
    assert_plain(sentence)
    assert "qwen" not in sentence.lower() and "11434" not in sentence
    assert "AI service" in sentence


def test_provider_failure_sentences_still_differ_per_kind() -> None:
    sentences = {
        ProviderFailure(kind, detail="x").operator_sentence() for kind in PROVIDER_FAILURE_KINDS
    }
    assert len(sentences) == len(PROVIDER_FAILURE_KINDS)


@pytest.mark.parametrize(
    "status, reason",
    (
        ("unreadable", ""),
        ("missing_dependency", "No module named 'PyObjC'"),
        ("timeout", "PDF read exceeded 20s"),
        ("invalid_arguments", "path is required"),
    ),
)
def test_pdf_failure_text_is_plain_words(status: str, reason: str) -> None:
    text = _failure_text(status, reason=reason, shown="holiday.pdf")
    assert_plain(text)
    assert "holiday.pdf" in text or "PDF" in text


def test_unknown_slash_commands_answer_in_plain_words() -> None:
    for command in ("/new", "/trace"):
        guidance = ui_command_fast_path(command, source_surface="api")
        assert guidance, command
        assert_plain(guidance)
        assert "127.0.0.1" not in guidance and "http" not in guidance.lower()
    # a path pasted with a leading slash is still not claimed as a command
    assert not ui_command_fast_path("/etc/hosts what is in this file", source_surface="api")
