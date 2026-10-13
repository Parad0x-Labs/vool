"""The served chat page must not set user-visible English without the catalog lookup.

Job post071-k (2026-10-14): 150 strings in the chat page script were written straight
into textContent/title/placeholder/aria-label/alert/confirm/toast calls, so every
non-English UI locale still showed them in English. Each is now wrapped in
pageT/pageTF; the English survives only as the inline fallback argument.

This test pins the RAW forms: each pattern below is the exact pre-fix assignment
(``x.textContent = 'No past chats yet.'``). A pattern reappearing means a string
regressed to raw English. The strings beyond the job's 150-string file-order cap
(model-catalog retry buttons and everything after them in file order) are recorded
in the job's inventory (out/inventory.tsv) and deliberately not asserted here.
"""

from __future__ import annotations

import re

from core.vool_chat_page import render_vool_chat_html

# (what the user sees, regex matching only the RAW pre-fix code form)
RAW_ENGLISH_PATTERNS: tuple[tuple[str, str], ...] = (
    # search counters
    ("transcript search hint", r"textContent = 'type ' \+ SEARCH_MIN_CHARS"),
    ("transcript search no-matches", r"textContent = 'no matches'"),
    ("activity search min chars", r"textContent = SEARCH_MIN_CHARS \+ '\+ chars'"),
    ("activity search none", r"textContent = 'none'"),
    # pin panel author label
    ("pin panel author", r"\? 'VOOL' : 'You'"),
    # copy feedback
    ("copied feedback", r"const prev = btn\.textContent; btn\.textContent = 'Copied'"),
    ("copy failed feedback", r"textContent = 'Copy failed'"),
    # text prompt dialog
    ("prompt dialog cancel", r"data-cancel>Cancel</button>'"),
    ("prompt dialog ok", r"class=\"set-btn\">OK</button>"),
    ("prompt dialog default message", r"String\(message \|\| 'Enter a value'\)"),
    # project flows
    ("project folder prompt", r"requestTextInput\('Project folder"),
    ("project name prompt", r"requestTextInput\('Project name:'"),
    ("project add failed", r"alert\('Could not add project: '"),
    ("project add failed short", r"alert\('Could not add project\.'\)"),
    ("project delete confirm", r"confirm\('Delete project \"" ),
    ("session item new-chat title", r"\|\| '\(new chat\)'"),
    ("chat emoji title", r"semo\.title = 'Set chat emoji or colour'"),
    ("move to project title", r"move\.title = 'Move to project'"),
    ("rename title", r"ren\.title = 'Rename'"),
    ("archive titles", r"archived \? 'Unarchive' : 'Archive'"),
    ("delete chat title", r"del\.title = 'Delete chat \(removes it and its memory\)'"),
    ("delete chat confirm", r"confirm\('Delete \"' \+ \(title \|\| 'this chat'\)"),
    ("project folder missing", r"'Folder not found: '"),
    ("reveal folder title", r"prev\.title = 'Reveal folder in Finder'"),
    ("rename project title", r"pren\.title = 'Rename project'"),
    ("delete project title", r"pdel\.title = 'Delete project \(forget its memory"),
    ("new chat in project title", r"pnew\.title = 'New chat in this project'"),
    ("project emoji title", r"pemo\.title = 'Set project emoji or colour'"),
    ("clear appearance", r"clr\.textContent = 'Clear emoji & colour'"),
    # sidebar sessions
    ("no past chats", r"none\.textContent = 'No past chats yet\.'"),
    ("general project name", r"n\.textContent = 'General'"),
    ("archived header", r"textContent = 'Archived \(' \+ archived\.length"),
    ("rename placeholder comparison", r"titleEl\.textContent === '\(new chat\)'"),
    # answer actions
    ("review flagged banner", r"code0\.textContent = '\\u26a0 review flagged"),
    ("report button", r"(?m)^\s{2}report\.textContent = 'Report';$"),
    ("report button title", r"report\.title = 'Report a wrong answer"),
    ("report down title", r"down\.title = 'Bad answer"),
    ("pdf download title", r"button\.title = 'Download this answer as PDF'"),
    ("pdf saved states", r"\? 'Cancelled' : 'Saved'"),
    ("pdf downloaded", r"textContent = 'Downloaded'"),
    ("pdf failed", r"refusal\.error \|\| 'PDF export failed'"),
    ("run record title", r"head\.title = 'Run record"),
    # image context menu
    ("image menu open preview", r"add\('Open in Preview'"),
    ("image menu show finder", r"add\('Show in Finder'"),
    ("image menu copy", r"add\('Copy image'"),
    ("image menu copy path", r"add\('Copy path'"),
    # run card status
    ("activity stage map", r"Searching: 'Searching'"),
    ("activity working fallback", r"HUMAN_ACTIVITY_STATES\[stage\] \|\| 'Working'"),
    ("run paused price title", r"run\.pricePaused \? 'Paused — waiting for your price'"),
    ("run working title", r": 'VOOL is working'"),
    ("run elapsed suffix", r"</b> elapsed'"),
    ("run model identity title", r"\|\| 'Recorded model identity'"),
    ("run cost used", r"\(usd \+ ' used'\)"),
    ("run verifying title", r"run\.verifying \? 'Answer ready"),
    ("run sr waiting", r"run\.permission \? 'Waiting for approval\. '"),
    ("run sr done", r"\|\| 'Done';"),
    # activity panel scope
    ("panel scope chat", r"panelScope === 'chat'\) return 'this chat'"),
    ("panel scope project", r"return 'project ' \+ currentProjectName"),
    ("panel empty all", r"return 'No ' \+ kind \+ ' recorded anywhere"),
    ("panel empty scoped", r"return 'No ' \+ kind \+ ' for ' \+ scopeWhere"),
    ("panel chat fallback", r"\|\| 'Chat';"),
    # recovery / receipts / panel copy
    ("recovery timeline toast", r"toast\('Showing the saved task timeline"),
    ("recovery cancelled toast", r"toast\('Interrupted task cancelled\.'\)"),
    ("recovery cancel failed", r"toast\('Could not cancel recovered task: '"),
    ("panel copy label", r"'Copy ' \+ \(panelTab \|\| 'Activity'\)"),
    ("receipt no hash toast", r"toast\('That receipt carries no hash to copy\.'\)"),
    # queue chips
    ("queue item labels", r"running \? 'Running: '"),
    ("queue price wait label", r"'Waiting for price · '"),
    ("queue queued label", r"\('Queued ' \+ pos \+ ': '\)"),
    ("queue cancel title", r"x\.title = 'Cancel this queued message'"),
    ("queue more hidden", r"'+' \+ hiddenCount \+ ' queued'"),
    ("queue show all title", r"more\.title = 'Show all queued messages'"),
    ("queue show less", r"less\.textContent = 'Show less'"),
    ("queue collapse title", r"less\.title = 'Collapse the queue'"),
    # paid-model confirm / usepod
    ("paid model confirm", r"window\.confirm\('Use the PAID model \"" ),
    ("usepod nothing sent suffix", r"toast\(blocker \+ ' Nothing was sent\.'\)"),
    # send-gate toasts
    ("send wait uploading toast", r"toast\('Wait for the attachments"),
    ("send failed attachment toast", r"toast\('Remove or retry the failed attachment"),
    ("send say-what toast", r"toast\('Add a message saying what to do"),
    ("send price check toast", r"toast\('Price check unavailable\." ),
    ("send price queue toast", r"toast\('Could not save this task to the price queue"),
    ("send price ready toast", r"toast\(target\.ready \? 'Price is available"),
    ("send task saved toast", r": 'Task saved\. Waiting for your price"),
    # attach hint
    ("attach hint line", r"return 'Up to ' \+ L\.max_files_per_turn"),
    ("attach families list", r"const families = 'text, code, PNG, JPEG, GIF, WebP'"),
    ("attach title prefix", r"attachBtnEl\.title = 'Attach files or photos"),
    # attachment chips
    ("attach name fallbacks", r"item\.document \? 'pasted text' : 'attachment'"),
    ("attach uploading", r"meta\.textContent = 'uploading…'"),
    ("attach use as message", r"asMessage\.textContent = 'Use as message'"),
    ("attach upload failed", r"\|\| 'Upload failed\.'"),
    ("attach retry labels", r"retry\.textContent = 'Retry'; retry\.title = 'Upload this file again'"),
    ("attach remove title", r"x\.title = 'Remove this attachment'"),
    ("attach remove aria", r"setAttribute\('aria-label', 'Remove ' \+"),
    ("attach preview toggle", r"toggle\.textContent = 'Preview'; toggle\.title = 'Show the beginning"),
    ("attach more chars", r"' more characters; the whole document was kept"),
    ("attach no stored copy", r"pre\.textContent = 'This document has no stored copy yet\.'"),
    ("attach doc erased", r"pre\.textContent = 'This document was erased"),
    ("attach doc gone http", r"'This document is no longer available \(HTTP '"),
    ("attach preview failed", r"pre\.textContent = 'The preview could not be loaded"),
    ("attach hide toggle", r"open \? 'Hide' : 'Preview'"),
    ("attach limit no paste", r"toast\('Up to ' \+ ATTACH_LIMITS\.max_files_per_turn"),
    ("attach limit not added", r"attachments per message — '"),
    ("attach doc load failed", r"toast\('Could not load the document"),
    ("attach moved toast", r"toast\('Moved into your message"),
    ("attach read failed", r"toast\(f\.name \+ ' could not be read as text\.'\)"),
    # dictation
    ("dictation idle title const", r"DICTATION_TITLE_IDLE = 'Dictate into this message"),
    ("dictation recording title const", r"DICTATION_TITLE_RECORDING = 'Recording — click to stop"),
    ("dictation ptt title const", r"DICTATION_TITLE_PTT = 'Hold to talk"),
    ("dictation notice hide toast", r"toast\('Notice hidden for now"),
    ("dictation ptt recording aria", r"'Recording — release to transcribe' : DICTATION_TITLE_PTT"),
    ("dictation transcribing title", r"on \? 'Transcribing on this machine"),
    # mode / provider / model picker
    ("mode change failed toast", r"toast\('Mode was not changed: '"),
    ("provider label", r"label\.textContent = 'Provider'"),
    ("provider select aria", r"setAttribute\('aria-label', 'Model provider'\)"),
    ("paid reason texts", r"\? 'The server classified this model as PAID\.'"),
    ("cost unknown reason", r"\? 'The server could not verify what this model costs\.'"),
    ("price rose reason", r"\? 'The catalog now lists this model ABOVE"),
    ("indeterminate reason", r": \"This model's published pricing is indeterminate"),
    ("pin confirm suffix", r"\+ ' Pin \"' \+ id"),
    ("auto fallback failed title", r"selectEl\.title = 'Could not change Auto fallback\.'"),
    ("model checking state", r"kind\.textContent = 'Checking model and price"),
    ("sort label", r"lab\.textContent = 'Sort'"),
    ("auto fallback label", r"lab\.textContent = 'Auto fallback'"),
    ("best verified free option", r"best\.textContent = 'Best verified free'"),
    ("auto hint", r"hint\.textContent = 'used only by VOOL Auto; never paid'"),
    ("model catalog load error", r"'Could not load the model catalog: ' \+ fetchError"),
)


def _page_script(html: str) -> str:
    """The page's own house script: first <script> (with the i18n bootstrap prepended
    into it) through its closing tag — the appended window.Vool* fragment scripts are
    separate files and not this test's subject."""
    start = html.index("<script>")
    end = html.index("</script>", start)
    return html[start:end]


def test_chat_page_script_sets_no_raw_english_from_the_inventory() -> None:
    script = _page_script(render_vool_chat_html(build_commit="t"))
    offenders = [
        what
        for what, pattern in RAW_ENGLISH_PATTERNS
        if re.search(pattern, script)
    ]
    assert not offenders, (
        "chat page script sets these strings as raw English again "
        f"(must go through pageT/pageTF): {offenders}"
    )


def test_fixed_strings_resolve_in_a_non_english_bundle() -> None:
    """Spot-check that the new keys really ride the ja bundle, not the fallback."""
    from core.i18n.page_bundle import i18n_bootstrap_js

    bundle = i18n_bootstrap_js("ja")
    start = bundle.find("var B = ") + len("var B = ")
    depth = 0
    for i in range(start, len(bundle)):
        if bundle[i] == "{":
            depth += 1
        elif bundle[i] == "}":
            depth -= 1
            if depth == 0:
                break
    import json

    messages = json.loads(bundle[start : i + 1])["messages"]
    for key, expected_fragment in (
        ("session.no_past_chats", "まだ"),
        ("msg.copied", "コピー"),
        ("attach.uploading", "アップロード中"),
        ("queue.show_less", "折りたたむ"),
        ("model.checking", "確認中"),
    ):
        assert expected_fragment in messages.get(key, ""), (
            f"ja bundle lost the translation for {key}: {messages.get(key)!r}"
        )
