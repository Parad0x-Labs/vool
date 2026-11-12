"""Round two of the chat page's plain-words law (failure-words-2).

Pins the leftovers the first job listed under "Still broken", on the served page source:

* the session transfer pane's export/preview/import refusals render one keyed sentence --
  the server's own ``d.error``/``sj.error`` words are not shown;
* the model-select failure (toast and row hint) is one keyed sentence, raw detail not shown;
* the bug-report failure's FIRST sentence is keyed; technical detail stays in the disclosure;
* the export dialog never shows the app's raw refusal (``e.fromApp`` / server ``j.message``);
* the gone-document notice carries no HTTP status;
* a failed send paints one keyed sentence on the transcript row (``run.error`` stays a
  machine field, never the person's text);
* the offline retry toast reads through the catalog.

Every English fallback here is the CLAIMS contract value, so the page and the catalogs
(agent C) can be held to the same words.

On the v0.8 line the raw detail goes nowhere: the chat page writes nothing to the browser console
(Requirement 9, tests/test_bug_report_chat_ui.py), so the train's console.warn lines are gone and
these tests pin that each failure site writes none. A dictation refusal shows the server's typed
words (reason, then remedy) as v0.8 pins (tests/test_dictation_ui_states.py); only a refusal
without them reads the keyed sentence, and none reads a status code.
"""

from __future__ import annotations

import re

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import script, slice_source

HTML = render_vool_chat_html()
SCRIPT = script()

# The CLAIMS contract English for this file's keys (chat.*), verbatim. The contract's
# seventh key chat.result_failed found no site and was pruned from the catalogs.
CONTRACT = {
    "chat.still_unreachable": "Still can't reach VOOL. It will keep trying.",
    "chat.session_export_failed": "The chat list could not be saved to a file.",
    "chat.session_preview_failed": "The preview could not be loaded.",
    "chat.session_import_failed": "The chats could not be brought back. Nothing was changed.",
    "chat.model_switch_failed": "That model could not be picked. The old one stays.",
    "chat.send_failed": "The message could not be sent.",
}


def _pageT_call(key: str) -> str:
    """The ``pageT(key, '<contract English>')`` call exactly as the source must carry it."""
    return "pageT('%s', '%s'" % (key, CONTRACT[key])


def _args_of(source: str, opener: str):
    """Yield every paren-accurate argument of each ``opener`` call in ``source``."""
    for match in re.finditer(re.escape(opener), source):
        depth, i = 0, match.start()
        while i < len(source):
            if source[i] == "(":
                depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield source[match.start() : i]


def _fn(name: str, end_marker: str) -> str:
    return slice_source(SCRIPT, name, end_marker)


_CONSOLE_CALL = re.compile(r"console\.(?:log|debug|info|warn|error|trace|table|dir|assert|count|group|profile|time)\(")


def _writes_to_console(source: str) -> bool:
    """Whether ``source`` calls the browser console, which the v0.8 chat page never does."""
    return bool(_CONSOLE_CALL.search(source))


def test_the_session_transfer_refusals_are_keyed_sentences() -> None:
    """d.error/sj.error from the bundle API never reach the pane; one keyed line does."""
    for key in ("chat.session_export_failed", "chat.session_preview_failed", "chat.session_import_failed"):
        assert _pageT_call(key) in SCRIPT, f"{key} is not read through pageT with the contract English"
    # the old raw refusals are gone as person-visible text
    for gone in (
        "sbStatus(sbExportStatus, d.error || 'Export refused.'",
        "sbStatus(sbImportStatus, sj.error || 'Could not stage the file.'",
        "sbStatus(sbImportStatus, d.error || 'Preview refused.'",
        "sbStatus(sbImportStatus, d.error || 'Import refused",
    ):
        assert gone not in SCRIPT, f"the raw server refusal still renders: {gone!r}"
    # and the refusal detail is not moved to the browser console either
    for warn in ("chat bundle export refused", "chat bundle upload refused",
                 "chat bundle preview refused", "chat bundle import refused"):
        assert warn not in SCRIPT, f"the refusal detail went to the console: {warn!r}"


def test_no_server_error_field_reaches_an_sbstatus_line() -> None:
    """Every sbStatus argument must be keyed/plain words, like the toast law in round one."""
    for argument in _args_of(SCRIPT, "sbStatus("):
        for raw in ("d.error", "sj.error", "e.message", "j.error", "r.status", "'HTTP '"):
            assert raw not in argument, f"raw {raw} still reaches the pane at: {argument[:120]!r}"


def test_the_model_select_failure_is_one_keyed_sentence() -> None:
    body = _fn("async function performCloudModelSwitch", "async function switchAutoFreeModel")
    assert _pageT_call("chat.model_switch_failed") in body, "the model-select failure is not keyed"
    assert not _writes_to_console(body), "the raw switch detail went to the console"
    assert "e.message || 'Could not select this model. Try again.'" not in SCRIPT, (
        "the bare exception text still feeds the model-select toast/hint"
    )
    # the row hint and its dataset carrier hold the keyed sentence (the hint renders both
    # here and from dataset.selectionError in the 'Not pinned.' sibling), never raw detail;
    # the old refusal words survive only inside the throw the catch above replaces
    assert "rowEl.dataset.selectionError = message;" in body
    assert "const message = pageT('chat.model_switch_failed'" in body


def test_the_bug_report_failure_first_sentence_is_keyed() -> None:
    body = _fn("function brRenderFailed(", "if (bugReportOverlay)")
    # the first sentence reads through the catalog; the caller's raw message is not shown
    assert "pageT('chat.try_again', 'Couldn’t do that just now. Try again.')" in body
    assert "failure.message" not in body, "brRenderFailed still reads the submission's raw failure message"
    assert not _writes_to_console(body)
    # the technical diagnostic stays in the expandable disclosure, not the first sentence
    assert "techBits.length" in body and "br-tech" in body


def test_the_export_dialog_never_shows_the_apps_raw_refusal() -> None:
    body = _fn("async function runExport()", "btn.addEventListener('click', openExport)")
    assert "? e.message" not in body, "the app's raw refusal message still renders on export"
    assert "msg = j.message" not in body, "the server's raw export message still renders"
    assert not _writes_to_console(body), "the export refusal went to the console"
    assert "pageT('chat.try_again', 'Couldn’t do that just now. Try again.')" in body
    # e.fromApp survives only as a condition/flag, never as the shown text
    for occurrence in [m.start() for m in re.finditer("e.fromApp", body)]:
        line_start = body.rfind("\n", 0, occurrence) + 1
        line = body[line_start : body.find("\n", occurrence)]
        assert "textContent" not in line or "pageT(" in line, f"e.fromApp rides a display line: {line!r}"


def test_the_gone_document_notice_carries_no_http_status() -> None:
    assert "pageT('attach.doc_gone_http', 'This document is no longer available.')" in SCRIPT
    for gone in (
        "pageTF('attach.doc_gone_http'",
        "This document is no longer available (HTTP {status})",
    ):
        assert gone not in SCRIPT, f"the gone-document notice still composes status text: {gone!r}"
    assert "attachment preview unavailable" not in SCRIPT, "the preview status went to the console"


def test_the_failed_send_paints_one_keyed_sentence_on_the_transcript_row() -> None:
    assert _pageT_call("chat.send_failed") in SCRIPT, "the send failure is not keyed"
    # the raw splice into the transcript row is gone
    for gone in (
        "pageT('run.error_prefix', 'Error: ') + run.error",
        "run.textEl.textContent = pageT('run.error_prefix', 'Error: ') + run.error",
    ):
        assert gone not in SCRIPT, f"raw exception text still reaches the transcript row: {gone!r}"
    # run.error stays a machine field (cross-tab/queue state reads it) -- kept, never rendered
    assert "run.error = (e && e.message ? e.message : String(e));" in SCRIPT
    assert "chat send failed" not in SCRIPT, "the raw exception went to the console"


def test_the_offline_retry_toast_is_keyed() -> None:
    assert "toast(pageT('chat.still_unreachable', \"Still can't reach VOOL. It will keep trying.\"))" in SCRIPT
    assert 'toast("Still can\'t reach VOOL. It will keep trying.")' not in SCRIPT


def test_the_failed_upload_chip_is_one_keyed_sentence() -> None:
    """The upload chip renders item.error; it must be keyed words, never data.message/status."""
    assert "item.error = pageT('attach.upload_failed_http', 'The file could not be uploaded.')" in SCRIPT
    body = _fn("async function uploadAttachment", "function removeAttachment")
    for gone in (
        "pageTF('attach.upload_failed_http'",
        "item.error = (data && data.message)",
        "Upload failed (HTTP {status})",
    ):
        assert gone not in body, f"the upload chip still composes raw failure text: {gone!r}"
    assert not _writes_to_console(body), "the upload refusal went to the console"


def test_the_dictation_failure_carries_no_status_code() -> None:
    assert "dictT('failed_http', 'Dictation did not work. Try again.')" in SCRIPT
    body = _fn("recorder.onstop = async () =>", "dictationRecorder = recorder;")
    for gone in ("Dictation failed (HTTP ", "res.status"):
        assert gone not in body, f"the dictation error still shows a status code: {gone!r}"
    # the server's typed words (reason, then remedy) or the keyed sentence, nothing else
    assert "setDictationError(typed || dictT('failed_http', 'Dictation did not work. Try again.'))" in body
    assert not _writes_to_console(body)


def test_the_key_test_rejection_reads_the_catalog_sentence() -> None:
    """The in-chat OpenRouter test refuses with the FW1 cloud.err_401 sentence, no HTTP code."""
    assert (
        "pageT('cloud.err_401', 'That key was refused. It may be wrong, expired, or withdrawn. "
        "Copy it fresh from the service’s own page and save it here again.'"
    ) in SCRIPT
    assert "OpenRouter rejected the key (HTTP " not in SCRIPT
    assert "cloud key test refused" not in SCRIPT, "the key test status went to the console"


def test_the_refused_turn_is_one_keyed_sentence() -> None:
    """A refused turn answers with keyed words; the typed reason rides run.error alone."""
    assert "pageT('run.refused_reason', 'VOOL did not run this turn.')" in SCRIPT
    assert "pageT('run.refused_status', 'VOOL did not run this turn.')" in SCRIPT
    body = _fn("// Any other refusal", "const reader = res.body.getReader()")
    for gone in (
        "VOOL did not run this turn: {reason}",
        "VOOL did not run this turn (HTTP {status})",
        ".replace('{reason}', reason)",
        ".replace('{status}', String(res.status))",
    ):
        assert gone not in body, f"the refused turn still interpolates raw detail: {gone!r}"
    # the machine field keeps the typed truth for cross-tab/queue state, never rendered
    assert "run.error = reason || String(res.status);" in body
    assert not _writes_to_console(body), "the refusal went to the console"


# ---- Round three: the job-level scan's raw-detail sites (throw carriers included) ----

# Code shapes that must no longer exist anywhere in the page script: each was a raw server
# detail or an 'HTTP ' + status composition riding a person-relevant Error or display line.
GONE_CODE_SHAPES = (
    "throw new Error(d.error || ('HTTP ' + r.status))",
    "new Error((data && data.error) || ('HTTP ' + r.status))",
    "throw new Error('HTTP ' + r.status)",
    "new Error(data.error || 'list unavailable')",
    "new Error(st.error || 'not found')",
    "(diag && diag.message) || data.error || data.detail || ('HTTP ' + res.status)",
    "result.message || result.error || 'PDF save failed'",
    "refusal.message || refusal.error || label('msg.pdf_failed'",
    "status.textContent = error.message || label('msg.pdf_failed'",
    "throw new Error('The server did not return a PDF')",
    "' (HTTP ' + balance.http_status",
    "(j.message || 'Dictation is unavailable",
    "{ error: err.message }",
    "selectEl.title = j.message",
    "throw new Error('Could not read runtime state (HTTP ' + response.status + ').')",
)


def test_the_runtime_state_read_carriers_are_plain() -> None:
    """The shared bounded-read helpers throw keyed words and write no status anywhere."""
    for warn in ("runtime state read refused", "runtime state post refused"):
        assert warn not in SCRIPT, f"the read status went to the console: {warn!r}"
    reads = SCRIPT.count("throw new Error(pageT('chat.try_again', 'Couldn’t do that just now. Try again.'))")
    assert reads >= 2, "the bounded-read carriers no longer throw keyed words"


def test_the_raw_detail_code_shapes_are_gone() -> None:
    for gone in GONE_CODE_SHAPES:
        assert gone not in SCRIPT, f"a raw-detail code shape survived: {gone!r}"


def test_the_pdf_export_status_is_one_keyed_sentence() -> None:
    body = _fn("msg-export-pdf", "actions.appendChild(button); host.appendChild(status);")
    assert "label('msg.pdf_failed', 'PDF export failed. Try again.')" in body
    assert not _writes_to_console(body), "the PDF export refusal went to the console"


def test_the_retired_usepod_balance_blocker_is_gone() -> None:
    # UsePod is out of core in 0.8 (owner decision, 2026-11-12): the chat page carries no UsePod
    # balance or spending check at all, so it can show no status or code from one.
    assert "function usepodBalanceBlocker(" not in SCRIPT
    assert "async function checkUsePodSpending" not in SCRIPT


def test_the_dictation_availability_note_is_keyed() -> None:
    body = _fn("async function probeDictationAvailability()", "// ---- Voice conversation")
    assert "dictT('unavailable', 'Dictation is not available on this machine. Type instead.')" in body
    # the probe says what the failed recording says: the typed reason and remedy when sent
    assert "String(j.message) + (j.remediation ? ' ' + j.remediation : '')" in body
    assert not _writes_to_console(body)


def test_the_mode_and_bypass_failures_are_keyed() -> None:
    body = _fn("async function postMode(", "async function setModeController(")
    assert "throw err;" in body and not _writes_to_console(body)
    bypass = _fn("async function confirmBypass()", "async function revokeBypass()")
    assert ": e.message" not in bypass, "the bypass dialog still paints the raw exception"
    assert "const detail = refusal ? pageT('bypass.readiness.' + String(refusal), plain) : plain;" in bypass
    assert not _writes_to_console(bypass)
    # the mode-change toast interpolates the keyed sentence, never err.message
    assert "{ error: pageT('chat.try_again', 'Couldn’t do that just now. Try again.') }" in SCRIPT
    assert "mode change failed" not in SCRIPT, "the mode change error went to the console"


def test_the_auto_fallback_tooltip_carries_no_server_message() -> None:
    body = _fn("async function switchAutoFreeModel(", "function makeCloudRow(")
    assert "selectEl.title = ''" in body
    assert not _writes_to_console(body), "the server's confirmation words went to the console"
