"""Hand a report to GitHub through the user's own browser: no token, no socket from VOOL.

The person opens a prefilled "new issue" page in their browser, signs in there if they
need to, reads the report one more time and presses GitHub's own Submit. VOOL itself makes
no request: it only builds the link from the exact approved bytes. When the encoded report
would make the link too long for GitHub, the link carries the title only and the report
goes to the clipboard for the person to paste -- the same approved bytes either way.

The project's own report repository uses issue forms (``wrong_answer.yml`` /
``bug_report.yml``) whose single ``report`` field takes the whole report; any other
destination gets a plain ``body=`` prefill.
"""
from __future__ import annotations

from urllib.parse import quote

from core.bug_report.destination import BUILTIN_DESTINATION, is_valid_destination

#: GitHub serves new-issue links up to roughly 8 KB; stay well under it.
MAX_URL_CHARS = 7500
FORM_FIELD = "report"
PASTE_NOTE = "The report is on your clipboard. Paste it here, read it once more, then press Submit."


def _form_for(kind: str) -> str:
    return "wrong_answer.yml" if kind == "wrong_answer" else "bug_report.yml"


def new_issue_link(*, destination: str, title: str, body: str, kind: str) -> dict:
    """{"url", "complete"}: ``complete`` is False when the body did not fit and must be pasted."""
    if not is_valid_destination(destination):
        raise ValueError(f"not a valid owner/name repository: {destination!r}")
    base = f"https://github.com/{destination}/issues/new"
    uses_form = destination == BUILTIN_DESTINATION
    field = FORM_FIELD if uses_form else "body"
    prefix = f"{base}?template={_form_for(kind)}&" if uses_form else f"{base}?"
    head = f"{prefix}title={quote(title, safe='')}"
    full = f"{head}&{field}={quote(body, safe='')}"
    if len(full) <= MAX_URL_CHARS:
        return {"url": full, "complete": True}
    return {"url": f"{head}&{field}={quote(PASTE_NOTE, safe='')}", "complete": False}


def search_link(*, destination: str, fingerprint: str) -> str:
    """Existing reports carrying this fingerprint (the marker line every report ends with)."""
    if not is_valid_destination(destination):
        raise ValueError(f"not a valid owner/name repository: {destination!r}")
    query = quote(f'"bug-report-fingerprint: {fingerprint}"', safe="")
    return f"https://github.com/{destination}/issues?q={query}"


__all__ = ["MAX_URL_CHARS", "PASTE_NOTE", "new_issue_link", "search_link"]
