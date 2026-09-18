"""Deterministic fast path for "read this PDF".

`pdf.extract_text` and `pdf.ocr` were correct from the day they landed and almost unreachable from
chat. Measured 2026-07-30 on the live daemon at e3f59af: of the natural phrasings a user actually
types at a named PDF, none reached either tool. Two separate seams did it, and both are structural
rather than a wording accident:

* the machine-read lane stands down the moment the user types out an explicit path under a home
  folder (`_names_an_explicit_path_below_a_home_folder`), on the reasoning that "the general machine
  and workspace tools handle the real target" — and for a PDF there is no such general tool, so the
  stand-down led nowhere;
* `machine.read_file` itself answers a `.pdf` with `binary_file`, "does not look like readable
  text", with no handoff to the two tools that exist precisely to read one.

So the only remaining route was the tool-intent loop asking a model to emit `{"intent":
"pdf.extract_text", ...}` as JSON. That is the same thing the fast paths exist because small local
models cannot do reliably: every miss cost ~61s and came back as "I couldn't map that cleanly to a
real action", which is also what a missing file, a disguised file and a scanned page all said.

This module makes the route deterministic. A message that names a `.pdf` gets the file read by the
real tool, and each distinct failure keeps its own words: a file that is not there does not read
like a file that is not a PDF, and neither reads like a scanned page.

Two further properties this buys, both deliberate:

* a named local path can no longer reach the live-info lane, which sits below this in the front
  door — one measured turn spent 236s web-searching Wikipedia for a path on this disk;
* the PDF's contents never enter a model prompt on this route. The bytes are read, recognised and
  rendered locally, so a document read this way does not leave the machine even while the turn's
  model lane is a cloud provider.

Scanned pages chain on their own: `extract_text` reporting `no_text_layer` is not an answer a user
can use, it is the tool naming its own successor, so the fast path runs `pdf.ocr` and says it did.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PDF_TOKEN_RE = re.compile(r"\.pdf\b", re.IGNORECASE)
_QUOTED_PDF_RE = re.compile(r"""[`'"]\s*([^`'"\r\n]*?\.pdf)\s*[`'"]""", re.IGNORECASE)

# Words that can sit immediately before a filename and are never part of it. The suffix walk below
# generates "at my summer notes.pdf" as well as "my summer notes.pdf"; these trim the first back to
# the second without needing the file to exist.
_LEADING_NOISE = {
    "at", "in", "on", "of", "from", "for", "the", "a", "an", "my", "this", "that",
    "inside", "within", "into", "file", "pdf", "document", "doc", "called", "named",
    "is", "it", "and", "to", "read", "open", "check", "see", "get", "have",
}

# A message that MAKES a PDF is not a message that reads one. Kept narrow on purpose: only verbs
# that unambiguously produce or transform a document, so "extract the text from x.pdf" (which
# contains "extract") still reads.
_CREATE_MARKERS = (
    " create a pdf", " create the pdf", " make a pdf", " make me a pdf", " generate a pdf",
    " write a pdf", " build a pdf", " export as pdf", " export to pdf", " save as pdf",
    " save it as pdf", " convert to pdf", " turn it into a pdf", " turn this into a pdf",
    " print to pdf", " merge the pdf", " combine the pdf", " split the pdf",
)

_OCR_MARKERS = (
    " ocr", " ocr ", " scan", " scanned", " recognise the text", " recognize the text",
    " image-only", " image only", " optical character",
)

_MAX_NAME_WORDS = 8
# A bare filename with no directory is looked for under the same roots the machine lane allows.
# Bounded so a deep tree cannot turn a chat turn into a disk crawl.
_SEARCH_MAX_DEPTH = 4
_SEARCH_MAX_ENTRIES = 60_000
# Directories that hold thousands of files a user never means by a bare filename. Skipping them is
# what keeps the budget available for the folders they do mean.
_SEARCH_SKIP_DIRS = {
    "node_modules", ".venv", "venv", "site-packages", ".git", "library", "__pycache__",
    ".cache", "build", "dist", ".tox", "target",
}


def _padded(text: str) -> str:
    return f" {' '.join(str(text or '').split()).lower()} "


def _clean_candidate(raw: str) -> str:
    value = str(raw or "").strip()
    value = value.strip("`'\"“”‘’<>(){}[],;:!?")
    value = value.strip()
    # A PATH begins at the first word carrying a separator, and nothing before that word can belong
    # to it. Measured 2026-07-30: "show me ~/Downloads/fixtures/budget_2024.pdf" produced the
    # candidate "show me ~/Downloads/fixtures/budget_2024.pdf", which is relative, which resolves
    # against the process cwd, which is outside the allowed roots — so a missing file under
    # ~/Downloads was refused as "outside those folders". A noise-word list cannot fix that: the
    # words in front of a path are whatever the user felt like typing.
    words = value.split(" ")
    for index, word in enumerate(words):
        if any(mark in word for mark in ("/", "\\", "~")):
            value = " ".join(words[index:])
            break
    else:
        # No separator anywhere: a bare filename, where a leading article or preposition is the
        # only thing that can be stuck to the front.
        while True:
            parts = value.split(" ", 1)
            if len(parts) == 2 and parts[0].lower().strip(".,:;") in _LEADING_NOISE:
                value = parts[1].strip()
                continue
            break
    return value


def _candidate_names(text: str) -> list[str]:
    """Every plausible spelling of the file the user named, most specific first.

    Filenames with spaces are the reason this is a walk rather than one regex: "I have a PDF at
    ~/Downloads/notes/my summer notes.pdf - what does it say?" has no token boundary that separates
    the name from the sentence, so the suffixes are generated and the disk decides which is real.
    """
    raw = str(text or "")
    out: list[str] = []
    for match in _QUOTED_PDF_RE.finditer(raw):
        cleaned = _clean_candidate(match.group(1))
        if cleaned:
            out.append(cleaned)
    for match in _PDF_TOKEN_RE.finditer(raw):
        head = raw[: match.end()]
        words = head.split()
        if not words:
            continue
        for count in range(min(_MAX_NAME_WORDS, len(words)), 0, -1):
            cleaned = _clean_candidate(" ".join(words[len(words) - count :]))
            if cleaned and cleaned.lower().endswith(".pdf") and cleaned not in out:
                out.append(cleaned)
    return out


def _looks_like_a_name(candidate: str) -> bool:
    stem = Path(candidate).stem.strip()
    return bool(stem) and stem.lower() not in {"", "a", "an", "the", "my", "this", "that"}


def _search_roots() -> tuple[Path, ...]:
    from core.runtime_execution_tools import _safe_machine_roots

    return _safe_machine_roots()


def _expand(candidate: str) -> Path:
    from core.runtime_execution_tools import _expand_machine_user

    return _expand_machine_user(candidate)


def _find_by_basename(name: str) -> Path | None:
    """A bare `report.pdf` with no directory, looked for under the allowed roots only.

    Breadth-first across ALL roots at once, not root-by-root depth-first. Depth-first spent the
    whole entry budget inside one checkout on ~/Desktop and returned None for a file sitting two
    levels down in ~/Downloads — the shallow, obvious answer lost to a deep, irrelevant tree.
    """
    target = name.strip().lower()
    if not target or "/" in target or "\\" in target:
        return None
    seen = 0
    frontier = [root for root in _search_roots() if root.is_dir()]
    for _ in range(_SEARCH_MAX_DEPTH + 1):
        if not frontier:
            return None
        next_frontier: list[Path] = []
        for directory in frontier:
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for entry in entries:
                seen += 1
                if seen > _SEARCH_MAX_ENTRIES:
                    return None
                try:
                    if entry.name.lower() == target and entry.is_file():
                        return entry
                    if (
                        not entry.name.startswith(".")
                        and entry.name.lower() not in _SEARCH_SKIP_DIRS
                        and entry.is_dir()
                    ):
                        next_frontier.append(entry)
                except OSError:
                    continue
        frontier = next_frontier
    return None


def _resolve_named_pdf(text: str) -> dict[str, Any] | None:
    """The file the user meant: an existing path when there is one, else what they wrote.

    `found` distinguishes "here it is" from "you named this and it is not there", so `not_found`
    keeps the user's own spelling instead of a guess.
    """
    candidates = _candidate_names(text)
    if not candidates:
        return None
    for candidate in candidates:
        if not _looks_like_a_name(candidate):
            continue
        expanded = _expand(candidate)
        try:
            if expanded.is_file():
                return {"path": expanded, "named": candidate, "found": True}
        except OSError:
            pass
        if not expanded.is_absolute():
            for root in _search_roots():
                probe = root / candidate
                try:
                    if probe.is_file():
                        return {"path": probe, "named": candidate, "found": True}
                except OSError:
                    continue
            located = _find_by_basename(candidate)
            if located is not None:
                return {"path": located, "named": candidate, "found": True}
    # Nothing on disk. Report the most specific thing they wrote: a path if they typed one.
    named = ""
    for candidate in candidates:
        if not _looks_like_a_name(candidate):
            continue
        if any(mark in candidate for mark in ("/", "\\", "~")):
            named = candidate
            break
        if not named:
            named = candidate
    if not named:
        return None
    return {"path": _expand(named), "named": named, "found": False}


def pdf_read_request(text: str) -> dict[str, Any] | None:
    """`{"path", "named", "found", "wants_ocr"}` when the message names a PDF to read, else None."""
    raw = str(text or "").strip()
    if not raw or not _PDF_TOKEN_RE.search(raw):
        return None
    padded = _padded(raw)
    if any(marker in padded for marker in _CREATE_MARKERS):
        return None
    resolved = _resolve_named_pdf(raw)
    if resolved is None:
        return None
    # The words the USER chose decide whether to reach for OCR, so the filename is removed before
    # they are read. Measured live: "scanned_invoice.pdf in my downloads fixtures folder - what
    # invoice number does it show?" matched " scan" inside its own filename and went straight to
    # OCR. It happened to be a scan, so the answer was right and the bug invisible; a text-layer
    # PDF called scan_of_contract.pdf would have been read by approximate recognition when exact
    # text was sitting there, and slowly.
    without_name = _padded(raw.replace(str(resolved.get("named") or ""), " "))
    resolved["wants_ocr"] = any(marker in without_name for marker in _OCR_MARKERS)
    return resolved


def _outside_allowed_roots(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    return not any(resolved == root or root in resolved.parents for root in _search_roots())


def _allowed_roots_label() -> str:
    from core.runtime_execution_tools import _home_relative_label

    return ", ".join(_home_relative_label(root) for root in _search_roots())


def _display(path: Path, named: str) -> str:
    from core.runtime_execution_tools import _home_relative_label

    try:
        return _home_relative_label(path)
    except Exception:
        return named or str(path)


def _failure_text(status: str, *, reason: str, shown: str) -> str:
    """Each status keeps its own words. This is the whole point of the module.

    Before this, a missing file, a .txt wearing a .pdf name, a scanned page and a router bail all
    reached the user as one sentence, so the one question a user can actually act on — is this my
    typo, my file, or your bug? — had no answer in the reply.
    """
    if status == "not_found":
        return (
            f"There is no file at `{shown}`. Check the name and the folder — I looked for exactly "
            f"that path and nothing is there."
        )
    if status == "unreadable":
        return (
            f"`{shown}` is not a readable PDF. The name ends in .pdf but the contents are not PDF "
            f"data, so PDFKit cannot open it — it is most likely a text or image file that was "
            f"renamed."
        )
    if status == "missing_dependency":
        return (
            f"I cannot read PDFs on this machine: {reason}. PDF reading needs the macOS system "
            f"Python at /usr/bin/python3 and its PyObjC bindings."
        )
    if status == "timeout":
        return f"Reading `{shown}` took too long and I stopped it: {reason}."
    if status == "invalid_arguments":
        return f"I could not work out which PDF you meant: {reason}."
    return f"I could not read `{shown}`: {reason or status}."


def _render_ok(execution: Any, *, shown: str, lead: str) -> str:
    details = dict(getattr(execution, "details", {}) or {})
    text = str(details.get("text") or "").strip()
    pages_read = int(details.get("pages_read") or 0)
    pages_total = int(details.get("pages_total") or 0)
    characters = int(details.get("characters") or 0)
    header = (
        f"{lead} `{shown}` — {pages_read} of {pages_total} page(s), {characters} character(s)."
    )
    if details.get("truncated"):
        header += " (Truncated; the file is longer than one reply.)"
    return f"{header}\n\n{text}".rstrip()


def maybe_handle_pdf_read(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Read the PDF the message names. None when it names none, so dispatch continues as today."""
    request = pdf_read_request(user_input)
    if request is None:
        return None

    # A bound attachment owns its own name. This lane resolves names against THIS disk and
    # claims the turn when the search misses; a PDF bound to the turn by ingress is material
    # the attachment pipeline already holds, so "the attached scan.pdf ... on screen on page 2"
    # must fall through to that pipeline instead of being answered "could not find ... anywhere
    # in ~". Same authority the display arbitration reads; a name that matches NOTHING bound
    # leaves this lane exactly as it was.
    from core.execution.constants import bound_attachment_names

    bound_names = bound_attachment_names(source_context)
    if bound_names:
        named_token = str(request.get("named") or "").replace("\\", "/").rsplit("/", 1)[-1].strip().casefold()
        # The name extractor can hand back a sentence fragment ("screen on page 2 of the
        # attached scan.pdf"), so the bound name is matched as the trailing filename of the
        # token, not compared for equality.
        if any(named_token == name or named_token.endswith((" " + name, "/" + name)) for name in bound_names):
            return None

    from core.agent_runtime.fast_paths_machine import _machine_tool_fast_path_result
    from core.authorized_tool_execution import (
        REFUSAL_STATUSES,
        execute_authorized_runtime_tool,
    )

    path: Path = request["path"]
    named = str(request.get("named") or "")
    shown = _display(path, named)

    def plain(body: str, *, reason: str) -> dict[str, Any]:
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=body,
            confidence=0.9,
            source_context=source_context,
            reason=reason,
        )

    # The root rule is the machine lane's, stated in the machine lane's words, so a PDF outside the
    # allowed folders is refused the same way any other file there is — and refused BEFORE the tool
    # runs, rather than read and then explained.
    #
    # Only a DIRECTORY the user typed can be outside the roots. A bare `budget.pdf` that the root
    # search did not turn up resolves relative to the process's cwd, which is outside the roots by
    # accident, and telling someone their filename "is outside those folders" when they named no
    # folder at all sends them to fix the wrong thing. Caught by sabotaging the bare-name search.
    user_named_a_directory = any(mark in named for mark in ("/", "\\", "~"))
    if user_named_a_directory and _outside_allowed_roots(path):
        return plain(
            f"I can only read files inside: {_allowed_roots_label()}. `{named or shown}` is outside "
            f"those folders, so I did not open it.",
            reason="pdf_read_outside_roots",
        )

    if not request.get("found"):
        if user_named_a_directory:
            return plain(
                _failure_text("not_found", reason="", shown=named or shown),
                reason="pdf_read_not_found",
            )
        return plain(
            f"I could not find a file called `{named}` anywhere in {_allowed_roots_label()}. "
            f"Give me the folder it is in and I will read it.",
            reason="pdf_read_not_found_by_name",
        )

    def run(intent: str) -> Any:
        return execute_authorized_runtime_tool(
            intent, {"path": str(path)}, source_context=dict(source_context or {})
        )

    intent = "pdf.ocr" if request.get("wants_ocr") else "pdf.extract_text"
    execution = run(intent)
    if execution is None:
        return None
    status = str(getattr(execution, "status", "") or "")

    # A scanned page is the one case where the tool's own answer names the next tool. Chaining it
    # here is what turns "no_text_layer" from a dead end into the text the user asked for.
    if status == "no_text_layer":
        ocr_execution = run("pdf.ocr")
        ocr_status = str(getattr(ocr_execution, "status", "") or "") if ocr_execution is not None else ""
        if ocr_execution is not None and getattr(ocr_execution, "ok", False):
            body = _render_ok(
                ocr_execution,
                shown=shown,
                lead=(
                    "That PDF carries no text layer, so it is a scan — I ran on-device OCR on it "
                    "instead and recognised"
                ),
            )
            return _machine_tool_fast_path_result(
                agent,
                user_input=user_input,
                session_id=session_id,
                source_context=source_context,
                intent="pdf.ocr",
                execution=ocr_execution,
                reason="pdf_read_fast_path_ocr",
                response_text=body,
            )
        ocr_reason = str(dict(getattr(ocr_execution, "details", {}) or {}).get("reason") or "")
        return plain(
            f"`{shown}` carries no text layer, so it is a scanned PDF. I ran on-device OCR on it as "
            f"well and that did not recover text either"
            + (f": {ocr_reason}." if ocr_reason else f" ({ocr_status or 'no result'})."),
            reason="pdf_read_scanned_ocr_failed",
        )

    if getattr(execution, "ok", False):
        lead = "Recognised text in" if intent == "pdf.ocr" else "Read"
        return _machine_tool_fast_path_result(
            agent,
            user_input=user_input,
            session_id=session_id,
            source_context=source_context,
            intent=intent,
            execution=execution,
            reason="pdf_read_fast_path",
            response_text=_render_ok(execution, shown=shown, lead=lead),
        )

    reason = str(dict(getattr(execution, "details", {}) or {}).get("reason") or "")
    if status in REFUSAL_STATUSES:
        # The authority refused (or wants approval for) this read: its own words are the answer,
        # and the envelope carries the typed decision, not a fabricated tool failure.
        return _machine_tool_fast_path_result(
            agent,
            user_input=user_input,
            session_id=session_id,
            source_context=source_context,
            intent=intent,
            execution=execution,
            reason="pdf_read_fast_path_refused",
        )
    return _machine_tool_fast_path_result(
        agent,
        user_input=user_input,
        session_id=session_id,
        source_context=source_context,
        intent=intent,
        execution=execution,
        reason="pdf_read_fast_path_failed",
        response_text=_failure_text(status, reason=reason, shown=shown),
    )


__all__ = ["maybe_handle_pdf_read", "pdf_read_request"]
