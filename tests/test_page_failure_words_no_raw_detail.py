"""No person-facing failure text on the two served pages is built from codes or raw detail.

When something fails, the person reads one keyed plain sentence; the code, the status number
and the server's own words go to the console. This is the job-level source scan over both
served pages (chat + Settings): none of the raw-detail compositions may appear on a line
that builds text a person sees, and the keyed sentences this job wired in must be present.

Lines that only carry the detail to console.warn/console.error are fine. Two Settings lines
are pinned verbatim by tests/test_vool_settings_page.py:130-131 and stay until the owner
okays that test edit; they are exempted by their exact pinned text and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PAGES = {
    "chat": REPO / "core" / "vool_chat_page.py",
    "settings": REPO / "core" / "vool_settings_page.py",
}

BANNED = (
    "'HTTP ' +",
    "' (HTTP '",
    "'HTTP ')",
    "(HTTP {status})",
    "'VOOL stored ' +",
    "d.error ||",
    "sj.error ||",
    "e.fromApp ?",
)

# The exact pinned compositions an existing test locks in place (see module docstring).
PINNED_OK = (
    # tests/test_vool_settings_page.py:131 pins 'VOOL stored ' + JSON.stringify(stored) + ' instead.'
    "'VOOL stored ' + JSON.stringify(stored) + ' instead.'",
)

CONSOLE_RE = re.compile(r"console\.(warn|error|info)\(")


def _offenders(path: Path) -> list[str]:
    offenders = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if CONSOLE_RE.search(line):
            continue
        if any(pinned and pinned in line for pinned in PINNED_OK):
            continue
        for pattern in BANNED:
            if pattern in line:
                offenders.append(f"{path.name}:{number}: {pattern} -> {line.strip()[:120]}")
                break
    return offenders


def test_no_raw_http_status_or_server_detail_is_composed_into_person_visible_text() -> None:
    problems: list[str] = []
    for path in PAGES.values():
        problems.extend(_offenders(path))
    assert not problems, "\n".join(problems)


def test_the_keyed_plain_sentences_this_job_wired_are_present() -> None:
    chat_src = PAGES["chat"].read_text(encoding="utf-8")
    settings_src = PAGES["settings"].read_text(encoding="utf-8")
    for key, fallback in (
        ("chat.still_unreachable", "Still can't reach VOOL. It will keep trying."),
        ("chat.session_export_failed", "The chat list could not be saved to a file."),
        ("chat.session_preview_failed", "The preview could not be loaded."),
        ("chat.session_import_failed", "The chats could not be brought back. Nothing was changed."),
        ("chat.model_switch_failed", "That model could not be picked. The old one stays."),
        ("chat.send_failed", "The message could not be sent."),
        ("attach.doc_gone_http", "This document is no longer available."),
    ):
        assert f"'{key}'" in chat_src and fallback in chat_src, f"chat page lost keyed sentence {key}"
    assert "'settings.store_mismatch'" in settings_src, "settings page lost the store-mismatch keyed sentence"
