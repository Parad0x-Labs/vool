"""Follow-up turns for the one-page site built from a person's plain words.

The build lane (``builder/site_words.py`` + ``app_builder``) writes a single ``index.html`` into
the chat's workspace. Its footer carries the line ``Made on this computer`` -- a human-readable
provenance marker AND the coordination point this module relies on: a follow-up turn ("can I see
it?", "add our opening hours ...", "put it online") does not need chat history or stored state to
find that page again, it needs a bounded walk of the workspace for an ``index.html`` carrying the
marker.

Every branch here fails closed and answers in plain words: an unreadable page, a missing
workspace or a wording this module does not recognise returns ``None`` so the ordinary chat lane
answers the turn. The publish family never performs a network action and never changes a file.
"""
from __future__ import annotations

import os
import re
from typing import Any

# The exact provenance line the built page's footer carries (see builder/site_words.py). Matched
# case-insensitively so a hand-edited footer still resolves.
SITE_PAGE_MARKER = "made on this computer"

# Bounded search: the build writes <folder>/index.html, so depth 3 covers the workspace root plus
# any reasonable nesting without walking a whole disk.
_MAX_SEARCH_DEPTH = 3
_MAX_PAGE_BYTES = 512 * 1024

# Directory names never worth searching for a person's page.
_SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    "site-packages",
    "dist",
    "build",
    "vendor",
}


def find_built_site_page(workspace_root: str) -> str | None:
    """The most recently modified built ``index.html`` under ``workspace_root``, else ``None``.

    Bounded (depth ~3), skips hidden directories and the usual heavy folders, and fails closed on
    anything odd: an unreadable root, an unreadable file or a decode error simply does not
    surface as a candidate.
    """
    root = str(workspace_root or "").strip()
    if not root or not os.path.isdir(root):
        return None
    root = os.path.abspath(root)
    best_path = ""
    best_key: tuple[float, str] = (-1.0, "")
    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=None, followlinks=False):
            relative = os.path.relpath(dirpath, root)
            depth = 0 if relative == "." else len(relative.split(os.sep))
            if depth >= _MAX_SEARCH_DEPTH:
                # No descent past the bound: the deepest page considered is
                # <root>/a/b/c/index.html (three folder levels down).
                dirnames[:] = []
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and name.lower() not in _SKIP_DIRS
            ]
            for name in filenames:
                if name.lower() != "index.html":
                    continue
                candidate = os.path.join(dirpath, name)
                try:
                    if not os.path.isfile(candidate):
                        continue
                    with open(candidate, "rb") as handle:
                        head = handle.read(_MAX_PAGE_BYTES)
                    if SITE_PAGE_MARKER not in head.decode("utf-8", "ignore").lower():
                        continue
                    mtime = os.path.getmtime(candidate)
                except OSError:
                    continue
                key = (mtime, candidate)
                if key > best_key:
                    best_key = key
                    best_path = candidate
    except OSError:
        return None
    return best_path or None


# --------------------------------------------------------------------------------------
# Wording families. Tight on purpose: this handler only claims turns that clearly point at the
# built page, everything else falls through to the ordinary chat lane.
# --------------------------------------------------------------------------------------

# The turn must name the thing it means: the page itself or the pronoun standing in for it.
_SITE_REF_RE = re.compile(r"\b(?:it|this|that|page|site|website|web ?page|homepage)\b")

_PUBLISH_RE = re.compile(
    r"\bpublish(?:ing|ed|es)?\b"
    r"|\bhost(?:ing|ed|s)?\b"
    r"|\bupload(?:ing|ed|s)?\b"
    r"|\bput\b[^.!?\n]{0,40}\bonline\b"
    r"|\bon the internet\b"
    r"|\bmake\b[^.!?\n]{0,40}\blive\b"
    r"|\bgo(?:ing)?\s+live\b"
)

_SEE_RE = re.compile(
    r"\bcan i see\b|\blet me see\b|\bshow me\b|\bshow it\b"
    r"|\bopen\b|\blook at\b|\bwhere(?:'s| is| do| can| did| does)\b|\bhow do i\b"
)
_SEE_ONLY_RE = re.compile(
    r"\bhow do i open\b|\bwhere do i edit\b|\bwhere can i edit\b|\bhow do i edit\b"
    r"|\bwhere(?:'s| is| was) (?:it|this|that|my (?:website|site|page)|the (?:page|site|website))\b"
    r"|\bwheres (?:it|my (?:website|site|page))\b"
)

_EDIT_VERB_RE = re.compile(r"\b(?:add|put|change|update|set|include)\b")
_HOURS_RE = re.compile(r"\b(?:opening\s+hours?|hours?)\b")
_PHONE_RE = re.compile(r"\b(?:phone|telephone)\b|\bphone\s+numbers?\b|\btelephone\s+numbers?\b")
_HOURS_VALUE_RE = re.compile(r"\bhours?\b")
_PHONE_VALUE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{6,17}\d)(?!\d)")

def _clean_text(text: str) -> str:
    lowered = " ".join(str(text or "").lower().replace("`", " ").split()).strip()
    # Plain apostrophes so "where's" and "wheres" both match the same patterns.
    return lowered.replace("’", "'")


def maybe_handle_site_followup(
    agent: Any,
    text: str,
    source_context: dict[str, object] | None,
    *,
    session_id: str = "",
) -> dict[str, Any] | None:
    """Answer a follow-up turn about the built page, or return ``None`` to claim nothing.

    Returns ``{"result": <fast-path result>}`` -- the shape the front door lanes return -- with a
    plain-words reply. Only acts when the workspace in ``source_context`` holds a built page; a
    chat that never built one gets ``None`` and answers normally.
    """
    clean = _clean_text(text)
    if not clean:
        return None
    context = dict(source_context or {})
    workspace = str(context.get("workspace") or context.get("workspace_root") or "").strip()
    page = find_built_site_page(workspace) if workspace else None
    if not page:
        return None

    def result(reply: str, reason: str) -> dict[str, Any]:
        return {
            "result": agent._fast_path_result(
                session_id=str(session_id or context.get("session_id") or ""),
                user_input=clean,
                response=reply,
                confidence=0.9,
                source_context=context,
                reason=reason,
            )
        }

    if _is_publish_request(clean):
        return result(
            "The page was made on this computer and nothing has been put online. "
            "Putting a page on the internet is not something VOOL does.",
            "built_site_publish_refused",
        )

    if _is_edit_request(clean):
        return _handle_edit(result, page, clean)

    if _looks_like_see_request(clean):
        folder = os.path.basename(os.path.dirname(os.path.abspath(page))) or "workspace"
        reply = (
            f"Your page is on this computer, in the {folder} folder, in a file called index.html. "
            "Open index.html in your browser to see it."
        )
        if re.search(r"\bedit\b", clean):
            reply += " If you want to change the words yourself, index.html is the file to change."
        return result(reply, "built_site_location")

    return None


def _is_publish_request(clean: str) -> bool:
    # A publish-shaped ask only counts when it points at the thing that was built ("put it
    # online", "publish the page"); "can you host a party for me" claims nothing.
    return bool(_PUBLISH_RE.search(clean) and _SITE_REF_RE.search(clean))


def _looks_like_see_request(clean: str) -> bool:
    if not _SITE_REF_RE.search(clean):
        return False
    if _SEE_ONLY_RE.search(clean):
        return True
    return bool(_SEE_RE.search(clean) and not _EDIT_VERB_RE.search(clean))


def _is_edit_request(clean: str) -> bool:
    if not _EDIT_VERB_RE.search(clean):
        return False
    if not (_HOURS_RE.search(clean) or _PHONE_RE.search(clean)):
        return False
    # The details must belong to the person's page ("add our hours", "change the hours", "put
    # our phone number on it"), or the turn must point at the page itself.
    if _SITE_REF_RE.search(clean):
        return True
    return bool(re.search(r"\b(?:our|my|the)\s+(?:opening\s+)?(?:hours?|phone|telephone)", clean))


# --------------------------------------------------------------------------------------
# Editing the page in place
# --------------------------------------------------------------------------------------

# Where a rebuilt detail may live inside B's render, in preference order: the smallest existing
# element whose text names the detail, a heading that names it, the footer, before </body>, end.
# The footer itself is never a REPLACE candidate: it carries the "Made on this computer" marker
# this module (and the person) rely on, and replacing it would erase the provenance line. Inline
# fragments (span) are not candidates either: the builder renders a detail row as
# <p><span class="label">Hours</span> 9 to 5</p>, and replacing the LABEL would leave the old
# value sitting after it -- the row element (p) is the unit that gets rewritten.
_TEXT_ELEMENT_RE = re.compile(
    r"<(?P<tag>p|li|dd|dt|td|th|div|section|article|aside)\b[^>]*>"
    r"(?P<inner>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_ELEMENT_RE = re.compile(
    r"<(?P<tag>h[1-6])\b[^>]*>(?P<inner>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_FOOTER_OPEN_RE = re.compile(r"<footer\b[^>]*>", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)


def _extract_followup_details(clean: str) -> tuple[str, str]:
    """(hours, phone) the person just gave, from the follow-up sentence alone.

    A local reading of the sentence: everything after the word "hours" is the hours phrase (cut
    where a second detail starts), and the first long digit run is a phone number. Used alongside
    ``site_words.extract_site_words`` -- whichever actually finds a detail wins.
    """
    hours = ""
    # The LAST mention carries the value: "change the hours to 9 till 5" puts it after "hours".
    hours_matches = list(_HOURS_VALUE_RE.finditer(clean))
    if hours_matches:
        tail = clean[hours_matches[-1].end():].strip(" ,:;-")
        tail = re.sub(r"^(?:are|is|to|as|at|please)\s+", "", tail)
        # Stop where a second detail ("... and our phone number ...") begins.
        tail = re.split(r",|\s+and\s+(?:our|my|the)\s+|\s+(?:and\s+)?(?:our|my|the\s+)?(?:phone|telephone)\b", tail)[0]
        hours = tail.strip(" ,:;-")
    phone_match = _PHONE_VALUE_RE.search(clean)
    phone = phone_match.group(0).strip(" .-()") if phone_match else ""
    # Match the builder's own rule: fewer than seven digits is a time or a date, not a phone.
    if sum(char.isdigit() for char in phone) < 7:
        phone = ""
    return hours, phone


def _detail_from_words(words: Any, names: tuple[str, ...]) -> str:
    """Read a detail out of ``site_words.extract_site_words``'s result, whatever shape it takes.

    The builder returns a small record whose fields are ``hours`` / ``phone``; a dict with the same
    keys is read just as willingly so a change of shape there cannot break this lane.
    """
    for name in names:
        if isinstance(words, dict):
            value = str(words.get(name) or "").strip()
        else:
            value = str(getattr(words, name, "") or "").strip()
        if value:
            return value
    return ""


def _upsert_detail(html: str, *, anchor: str, text: str) -> str:
    """Place ``text`` in the page at the detail's existing spot, or in the nearest plain one."""
    # 1. Replace the smallest existing element whose text names the detail.
    best = None
    for match in _TEXT_ELEMENT_RE.finditer(html):
        inner_text = re.sub(r"<[^>]+>", " ", match.group("inner")).lower()
        if anchor in inner_text:
            if best is None or (match.end() - match.start()) < (best.end() - best.start()):
                best = match
    if best is not None:
        tag = best.group("tag")
        return html[: best.start()] + f"<{tag}>{text}</{tag}>" + html[best.end():]
    # 2. A heading names the detail but holds no value: put the line right under it.
    for match in _HEADING_ELEMENT_RE.finditer(html):
        inner_text = re.sub(r"<[^>]+>", " ", match.group("inner")).lower()
        if anchor in inner_text:
            return html[: match.end()] + f"\n<p>{text}</p>" + html[match.end():]
    # 3. Failing that: above the footer, else before </body>, else at the end.
    block = f"<p>{text}</p>"
    footer = _FOOTER_OPEN_RE.search(html)
    if footer is not None:
        return html[: footer.start()] + block + "\n" + html[footer.start():]
    body_close = _BODY_CLOSE_RE.search(html)
    if body_close is not None:
        return html[: body_close.start()] + block + "\n" + html[body_close.start():]
    return html + "\n" + block


def _apply_page_edit(page_path: str, clean: str) -> tuple[str, dict[str, str]]:
    """Edit the page file in place. Returns (status, details): status is "edited", "missing_value"
    or "failed"; details carries the hours/phone values that were applied."""
    # The extraction of the person's words belongs to the builder's own reader; without it this
    # lane claims nothing and the chat answers normally.
    try:
        from core.agent_runtime.builder import site_words
    except ImportError:
        return "failed", {}
    try:
        words = site_words.extract_site_words(clean)
    except Exception:
        words = {}
    local_hours, local_phone = _extract_followup_details(clean)
    hours = _detail_from_words(
        words, ("hours", "opening_hours", "opening_hours_text", "hours_text")
    ) or local_hours
    phone = _detail_from_words(
        words, ("phone", "phone_number", "phone_number_text", "telephone")
    ) or local_phone
    if not hours and not phone:
        return "missing_value", {}

    try:
        with open(page_path, encoding="utf-8") as handle:
            html = handle.read()
    except OSError:
        return "failed", {}
    updated = html
    if hours:
        updated = _upsert_detail(updated, anchor="hours", text=f"Opening hours: {hours}")
    if phone:
        updated = _upsert_detail(updated, anchor="phone", text=f"Phone: {phone}")
    try:
        if updated != html:
            with open(page_path, "w", encoding="utf-8") as handle:
                handle.write(updated)
    except OSError:
        return "failed", {}
    applied = {key: value for key, value in (("hours", hours), ("phone", phone)) if value}
    return "edited", applied


def _handle_edit(result: Any, page: str, clean: str) -> dict[str, Any] | None:
    status, applied = _apply_page_edit(page, clean)
    if status == "missing_value":
        return result(
            "Tell me the opening hours or the phone number and I will put them on the page.",
            "built_site_edit_needs_detail",
        )
    if status == "failed" or not applied:
        return result(
            "I could not change the page, so it still shows what it showed before.",
            "built_site_edit_failed",
        )
    parts = []
    if applied.get("hours"):
        parts.append(f"Your page now shows your opening hours: {applied['hours']}.")
    if applied.get("phone"):
        parts.append(f"Your page now shows your phone number: {applied['phone']}.")
    return result(" ".join(parts), "built_site_edited")
