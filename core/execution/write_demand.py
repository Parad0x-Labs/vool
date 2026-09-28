"""THE one typed literal-versus-brief write-demand authority.

Defect (audit of 59aa5ee7, 2026-09-02): capability selection had no notion of literal-versus-
brief content. "create notes.txt containing hello" — a request whose content is already on the
page — was enabled by nothing (the only reader that recognized the shape demanded the literal
noun "file"), so the builder's profile branch classified it as a named-file build and handed a
LITERAL write to the model-driven project builder, which needs a local Ollama model and fails
wherever that lane is unreachable. Its project-shaped siblings ("... for my project") promoted
on a project noun ANYWHERE in the sentence and reached the multi-file scaffolder under an OPEN
scope. One defect, two faces: nobody computed a typed write demand, so nothing could compare
the claim against it.

This module is that computation, called once per turn. It turns text plus workspace into a
typed demand: targets, content CLASSIFIED as literal or brief, the asked mode
(create/overwrite/append), whether the result was asked to run, and a confinement verdict per
target. It composes readers that already exist rather than adding phrases: the planner's write
grammar (retired here from ``constants.py``), ``content_is_a_brief`` (the brief authority the
machine lane already answers to), ``named_build_files``/``_RUN_REQUEST_RE`` for scope signals.

Content classification is STRUCTURAL, in this order of authority:

* an explicit literal marker in the retired grammar (``with exactly this content:``,
  ``that says``) is literal — the user marked the text as text;
* a quoted span or a colon-delimited span after the target is literal — quoting and a colon
  are themselves literal markers, which is what they are FOR;
* any other captured run is literal only if ``content_is_a_brief`` REJECTS it. A brief
  ("... containing a two-line summary of what a linter does") is a description of the file,
  not the file: the demand stays, classified ``brief``, and belongs to the builder under its
  EXACT mutation scope — never to a verbatim write.

Consumers, and only two of them: a literal demand becomes ``workspace.write_file`` payloads
that cross ``decide_tool_call`` and the effect gates like any other write (zero model calls);
a brief demand leaves the builder owning the turn under EXACT scope. ``builder/support.py``
refuses ``model_build`` for literal content on both of its branches, and the project
promotion uses the object-phrase test (``mutation_scope._object_phrase_widens``) instead of
noun-anywhere — so the locative "in this project" stops promoting a one-file write into a
scaffold.
"""
from __future__ import annotations

import posixpath
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

_SPLIT_EXT_CLASS_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-"
)
# A dot whose following whitespace separates it from a known extension. The stem class of the
# grammar below includes the dot itself, so the previous `[class]+\.\s+ext` pattern backtracked
# through every dot of a long run at every scan position -- quadratic on runs of "a.a.a".
# The scanner's own `(?<=\.)\s+` lead re-tries every extension word per whitespace-run
# backtrack step, so `_iter_split_ext_occurrences` walks dots directly instead; the
# regex stays for the differential harness that pins the iterator to it.
_SPLIT_EXT_OCCURRENCE_RE = re.compile(
    r"(?<=\.)\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b"
)
_SPLIT_EXT_WORDS = ("json", "yaml", "toml", "tsx", "jsx", "txt", "yml", "md", "py", "js", "ts")
_SPLIT_EXT_WORD_CHAR_RE = re.compile(r"\w")


def _iter_split_ext_occurrences(raw: str):
    """``(start, ext_start, end)`` for every `_SPLIT_EXT_OCCURRENCE_RE` match, in order.

    A split extension's dot ends its character run (the whitespace after it ends
    the run), the extension word starts at the end of that whitespace run, and the
    longest word followed by a word boundary is the one the ordered alternation
    plus `\\b` selects. Each dot is visited once; a failed or consumed occurrence
    never rescans the text behind it.
    """
    n = len(raw)
    pos = 0
    while pos < n:
        dot = raw.find(".", pos)
        if dot < 0:
            return
        j = dot + 1
        while j < n and raw[j].isspace():
            j += 1
        if j > dot + 1:
            for word in _SPLIT_EXT_WORDS:
                end = j + len(word)
                if raw[j:end] == word and (end >= n or not _SPLIT_EXT_WORD_CHAR_RE.match(raw[end])):
                    yield dot + 1, j, end
                    pos = end
                    break
            else:
                pos = j
                continue
            continue
        pos = dot + 1


def _repair_split_extensions(raw: str) -> str:
    """Close the whitespace in ``notes. txt`` -- exactly the replacements the previous
    ``re.sub(r"[A-Za-z0-9_./-]+\\.\\s+(ext)\b", ...)`` made, in one linear pass.

    A split extension is always a dot at the END of its character run (the whitespace after it
    ends the run), so each occurrence is found directly instead of by backtracking. Two rules
    keep the deleted whitespace identical to the old substitution: the dot needs one stem
    character before it, and the engine only ever saw characters at or after the end of the
    previous replacement, so an occurrence whose stem begins inside an earlier replacement is
    left alone exactly as the scanner-skipping ``re.sub`` left it.
    """
    out: list[str] = []
    copy_from = 0
    resume = 0
    for m_start, ext_start, m_end in _iter_split_ext_occurrences(raw):
        dot = m_start - 1
        if dot < resume + 1:
            continue
        run_start = dot
        while run_start > 0 and raw[run_start - 1] in _SPLIT_EXT_CLASS_CHARS:
            run_start -= 1
        if run_start == dot:
            continue
        out.append(raw[copy_from:m_start])
        copy_from = ext_start
        resume = m_end
    out.append(raw[copy_from:])
    return "".join(out)


# ── The retired write grammar (moved verbatim from core/execution/constants.py) ─────────────
# ``constants.py`` re-exports these names; ``planner.py`` and the machine-tool audit test
# import them from there and keep working unchanged.

_WORKSPACE_FILE_RE = r"[A-Za-z0-9_./-]+\.[A-Za-z0-9_+-]+"
# A target may be quoted, in which case it may contain spaces: 'create "my notes.txt"
# containing hello'. The quoted branch is tried first; the bare branch cannot match spaces, so
# an unquoted sentence never captures a two-word target by accident.
_WORKSPACE_TARGET_RE = rf"[`\"'][^`\"']+?\.[A-Za-z0-9_+-]+[`\"']|{_WORKSPACE_FILE_RE}"

_CREATE_FILE_CONTENT_STOP = (
    r"(?="
    r"(?:\.\s*(?:Then|Now|Inside it|Do not)\b)"
    r"|(?:\s+and\s+(?:then|also|finally|next)\b)"
    r"|(?:\s+and\s+(?:create|write|make|save|add|append|run|execute|open|read|delete|remove|list|commit|test)\b)"
    r"|(?:\s+then\s+(?:create|write|make|save|add|append|run|execute|open|read|delete|remove|list|commit|test)\b)"
    r"|(?:\s*\n\s*(?:create|write|make|save|add|append|run|execute|open|read|delete|remove|list|commit|test|then|now|also|finally|next)\b)"
    r"|$"
    r")"
)
_CREATE_NAMED_FILE_WITH_CONTENT_RE = re.compile(
    rf"\bcreate\s+(?:a\s+)?file(?:\s+named)?\s+[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?(?:\s+in\s+[^\r\n]+?)?\s+with(?:\s+exactly)?(?:\s+(?:this|the))?\s+(?:line|content|code)(?:(?:\s*,?\s*[^:\n.]+?)\s*:|:\s*|\s+)(?P<content>.+?){_CREATE_FILE_CONTENT_STOP}",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_CREATE_FILE_RE = re.compile(
    rf"\bcreate\s+(?:a\s+file(?:\s+named)?\s+)?[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?\s+(?:with(?:\s+exactly)?(?:\s+(?:this|the))?(?:\s+(?:line|content|code))(?:(?:\s*,?\s*[^:\n.]+?)\s*:|:\s*|\s+)|that\s+says:)\s*(?P<content>.+?){_CREATE_FILE_CONTENT_STOP}",
    re.IGNORECASE | re.DOTALL,
)
# Plain natural phrasing the chat/API surface gets most often, e.g.
# "create a file test.txt with hello", "write a file notes.md saying hi",
# "make a file called out.txt containing done". Permissive content capture so a
# bare value after with/saying/containing still resolves to workspace.write_file.
#
# The noun "file" is OPTIONAL, and the target may be quoted. Measured 2026-09-02 on the served
# path: the requests people actually send drop the noun — "create notes.txt containing hello",
# "create notes.txt with hello", "create docs/notes.txt with nested" — and every one of them was
# claimed as a file request by the wide front-door detector and then produced NO typed write,
# because this pattern (the only producer of one) demanded the literal word "file" between the
# verb and the path. The target itself is the anchor — it must be a real file token (extension
# required), so "save the day with a smile" cannot match. Word order and optionality, not new
# phrases.
_PLAIN_CREATE_FILE_WITH_CONTENT_RE = re.compile(
    rf"\b(?:create|write|make|save|add)\s+(?:a\s+|the\s+|new\s+|this\s+)*(?:file\s+)?(?:(?:named|called)\s+)?"
    rf"(?P<path>{_WORKSPACE_TARGET_RE})"
    # A destination bridge between the target and the content marker — "in the workspace",
    # "in this project" — the same bridge the named pattern below always had; without it a
    # plain "create a file x.py in the workspace with print('hello')" resolved no write at
    # all and the turn fell to a workspace search. Bounded and punctuation-free so it can
    # only ever be a locative phrase.
    r"(?:\s+(?:in|inside|under|within|at)\s+[A-Za-z0-9_ ./-]{1,60}?)?"
    r"\s+(?:(?:with|containing)(?:\s+(?:the\s+|these\s+|this\s+|exact(?:ly)?\s+){0,3}(?:text|contents|content|lines|line|body))?|saying|that\s+says|holding)\s*:?\s*"
    rf"(?P<content>.+?){_CREATE_FILE_CONTENT_STOP}",
    re.IGNORECASE | re.DOTALL,
)
_FOLDER_FIRST_CREATE_FILE_RE = re.compile(
    rf"\b(?:pls\s+)?(?:make|create|setup|set up)\s+(?:a\s+)?folder\s+(?P<directory>[A-Za-z0-9_./-]+)"
    r"(?:\s+(?:here|in\s+this\s+workspace))?"
    r"\s+(?:and\s+)?(?:inside\s+it\s+)?(?:save|put|write|create)\s+[`\"']?(?P<path>"
    rf"{_WORKSPACE_TARGET_RE})[`\"']?(?:\s+inside)?\s+with(?:\s+exact(?:ly)?)?(?:\s+(?:this|the))?\s+text\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# The two lazy path+extension write grammars below ("inside this workspace create <path> with
# text: ...", "create file <path> in <dir> folder saying ...") are matched by the linear scanners
# `_in_workspace_create_file_match` / `_file_in_folder_saying_match`, not by nested regex
# quantifiers: a lazy `[^`"']+?\.[ext]+` path walks every dot of a quote-free run at every anchor
# occurrence, so repeated anchors with no successful tail anywhere rescan the remaining text once
# per anchor (measured: a 6000-anchor run exceeded a 3s subprocess deadline through
# resolve_write_demand). The scanners yield the same first match and the same groups; the legacy
# patterns are kept beside them as the documented language and the differential ground truth.
_IN_WORKSPACE_CREATE_FILE_RE = re.compile(
    r"\binside\s+this\s+workspace\s+create\s+[`\"']?(?P<path>[^`\"']+?\.[A-Za-z0-9_+-]+)[`\"']?"
    r"\s+with(?:\s+exact(?:ly)?)?(?:\s+(?:this|the))?\s+text\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_FILE_IN_FOLDER_SAYING_RE = re.compile(
    r"\bcreate\s+(?:a\s+)?file\s+[`\"']?(?P<path>[^`\"']+?\.[A-Za-z0-9_+-]+)[`\"']?"
    r"\s+in\s+(?:the\s+)?(?P<directory>[A-Za-z0-9 _./-]+?)\s+folder\s+saying\s+(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_IN_WORKSPACE_ANCHOR_RE = re.compile(
    r"\binside\s+this\s+workspace\s+create(?P<gap>\s+)", re.IGNORECASE
)
_FILE_IN_FOLDER_ANCHOR_RE = re.compile(r"\bcreate\s+(?:a\s+)?file(?P<gap>\s+)", re.IGNORECASE)
# The bounded "with … text:" tail that follows the extension run. The pad group records the
# trailing `\s*` so a candidate at end-of-input can give one whitespace character back to the
# required non-empty content, exactly as the regex engine backtracks.
_WITH_TEXT_TAIL_RE = re.compile(
    r"[`\"']?\s+with(?:\s+exact(?:ly)?)?(?:\s+(?:this|the))?\s+text\s*:(?P<pad>\s*)",
    re.IGNORECASE,
)
_IN_MARKER_RE = re.compile(
    r"[`\"']?\s+in(?P<gap>\s+)(?P<opt>the\s+)?", re.IGNORECASE
)
_FOLDER_SAYING_MARKER_RE = re.compile(r"\s+folder\s+saying(?P<pad>\s+)", re.IGNORECASE)
_DELIMITED_QUOTES = "`'\""


def _ws_run_end(raw: str, pos: int, end: int) -> int:
    while pos < end and raw[pos].isspace():
        pos += 1
    return pos


def _ws_run_start(raw: str, pos: int, floor: int) -> int:
    while pos > floor and raw[pos - 1].isspace():
        pos -= 1
    return pos


def _lit_at(raw: str, pos: int, word: str) -> bool:
    return raw[pos : pos + len(word)].casefold() == word


def _match_in_marker(raw: str, pos: int) -> tuple[tuple[int, int], tuple[int, int], int] | None:
    """The anchored match of `_IN_MARKER_RE` at ``pos``: (gap span, opt span, end).

    The pattern's parse is deterministic -- its `\\s+` runs are each followed by a
    literal, so a shorter run never lets a later element match -- which is what
    makes the hand scan exact. The quote is taken when present (the only other
    branch needs whitespace at the quote's position and fails immediately).
    """
    n = len(raw)
    p = pos
    if p < n and raw[p] in _DELIMITED_QUOTES:
        p += 1
    ws_end = _ws_run_end(raw, p, n)
    if ws_end == p or not _lit_at(raw, ws_end, "in"):
        return None
    gap_start = ws_end + 2
    gap_end = _ws_run_end(raw, gap_start, n)
    if gap_end == gap_start:
        return None
    opt = (-1, -1)
    end = gap_end
    if _lit_at(raw, end, "the"):
        opt_ws = _ws_run_end(raw, end + 3, n)
        if opt_ws > end + 3:
            opt = (end, opt_ws)
            end = opt_ws
    return (gap_start, gap_end), opt, end


def _iter_folder_saying_marks(raw: str) -> list[tuple[int, int, int]]:
    """``(start, end, pad_start)`` for every `_FOLDER_SAYING_MARKER_RE` match, in the
    overlapping order `_all_marker_matches` produces: one match per position of the
    whitespace run in front of each ``folder saying``.

    The `\\s+` leads and pads of the marker and tail patterns are the remaining
    polynomial shapes the scanner flags on uncontrolled turns; each of these parses
    is deterministic (every `\\s+` is followed by a literal), so the hand scans are
    exact and the regexes stay only for the differential harness.
    """
    n = len(raw)
    out: list[tuple[int, int, int]] = []
    pos = 0
    while True:
        f = raw.casefold().find("folder", pos)
        if f < 0:
            break
        pos = f + 1
        run = _ws_run_start(raw, f, 0)
        saying_at = _ws_run_end(raw, f + 6, n)
        if run == f or saying_at == f + 6 or not _lit_at(raw, saying_at, "saying"):
            continue
        word_end = f + 6
        saying_end = _ws_run_end(raw, word_end, n) + 6
        pad_start = saying_end
        pad_end = _ws_run_end(raw, pad_start, n)
        if pad_end == pad_start:
            continue  # the marker's pad is `\s+`: "folder saying" with no space after is no marker
        for start in range(run, f):
            out.append((start, pad_end, pad_start))
    return out


def _iter_with_text_tails(raw: str) -> list[tuple[int, int, int]]:
    """``(start, pad_start, end)`` for every `_WITH_TEXT_TAIL_RE` match, in the
    overlapping order `_all_marker_matches` produces: one match per admissible lead
    position (each whitespace-run position, plus the quote one character earlier).

    After ``with`` the optional ``exact(ly)`` and ``this|the`` groups and the
    mandatory ``text`` are all literal-anchored, so the greedy parse is the match;
    the pad records the `\\s*` after the colon and ``end`` its greedy end.
    """
    n = len(raw)
    out: list[tuple[int, int, int]] = []
    pos = 0
    folded = raw.casefold()
    while True:
        w = folded.find("with", pos)
        pos = w + 1
        if w < 0:
            break
        run = _ws_run_start(raw, w, 0)
        if run == w:
            continue
        p = _ws_run_end(raw, w + 4, n)
        if p == w + 4:
            continue  # the tail's first element after `with` is always whitespace
        if _lit_at(raw, p, "exact"):
            q = p + 5
            if _lit_at(raw, q, "ly"):
                q += 2
            if q < n and raw[q].isspace():
                p = _ws_run_end(raw, q, n)
        if _lit_at(raw, p, "this") or _lit_at(raw, p, "the"):
            q = p + (4 if _lit_at(raw, p, "this") else 3)
            if q < n and raw[q].isspace():
                p = _ws_run_end(raw, q, n)
        if not _lit_at(raw, p, "text"):
            continue
        p = _ws_run_end(raw, p + 4, n)
        if p >= n or raw[p] != ":":
            continue
        pad_start = p + 1
        end = _ws_run_end(raw, pad_start, n)
        leads = list(range(run, w))
        if run > 0 and raw[run - 1] in _DELIMITED_QUOTES:
            leads.append(run - 1)
        for start in sorted(leads):
            out.append((start, pad_start, end))
    return out


class DelimitedWriteMatch(NamedTuple):
    """One matched write grammar: the path (and directory) it names and the literal content."""

    path: str
    content: str
    directory: str = ""


def _all_marker_matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Every match of the bounded pattern, overlaps included.

    ``finditer`` alone is not enough: matches that begin inside another match (a second
    marker starting in a leading whitespace run) are exactly the ones a lazy path could
    still reach, so scanning restarts one character past each match start.
    """
    found: list[re.Match[str]] = []
    pos = 0
    while (match := pattern.search(text, pos)) is not None:
        found.append(match)
        pos = match.start() + 1
    return found


def _content_start_after(
    greedy_end: int, pad_start: int, text_end: int, *, pad_min: int = 0
) -> int | None:
    r"""Where `(?P<content>.+)$` starts after a marker that ended at ``greedy_end``.

    The content needs at least one character. A marker whose greedy trailing whitespace ran to
    the end of input gives one whitespace character back -- the engine's first backtracking
    step -- which only helps while the pad keeps its own minimum (``\s*`` keeps zero,
    ``\s+`` keeps one). A marker with nothing left to give cannot host content at all.
    """
    if greedy_end < text_end:
        return greedy_end
    if pad_start + pad_min < greedy_end:
        return greedy_end - 1
    return None


def _delimited_path_candidates(
    text: str,
    tail_positions: list[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    """``(dot, ext_end, content_start)`` for every dot+extension that a tail can follow.

    The extension is greedy, and the tail always begins with a quote or whitespace -- never an
    extension character -- so backtracking the extension can never land the tail inside the run:
    a dot succeeds exactly when the tail matches at the END of its extension run. That makes the
    candidate set computable once for the whole text instead of once per anchor.
    """
    candidates: list[tuple[int, int, int]] = []
    n = len(text)
    for tail_start, pad_start, tail_end in tail_positions:
        prev = tail_start - 1
        if prev < 0 or text[prev] not in _EXT_RUN_CHARS:
            continue
        run_start = prev
        while run_start > 0 and text[run_start - 1] in _EXT_RUN_CHARS:
            run_start -= 1
        if run_start == 0 or text[run_start - 1] != ".":
            continue
        content_start = _content_start_after(tail_end, pad_start, n)
        if content_start is None:
            continue
        candidates.append((run_start - 1, tail_start, content_start))
    candidates.sort()
    return candidates


#: The extension run `[A-Za-z0-9_+-]+` of both grammars, as one frozenset for membership scans.
_EXT_RUN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-"
)


def _quote_free_end(text: str, start: int, quotes: list[int]) -> int:
    i = bisect_left(quotes, start)
    return quotes[i] if i < len(quotes) else len(text)


def _anchor_path_starts(anchor: re.Match[str], raw: str) -> list[int]:
    """Path-start positions an anchor allows, in the engine's backtracking order.

    The anchor's trailing `\\s+` is greedy, but a path may begin inside the whitespace run:
    "create file \n.py" only matches with the newline inside the path, because a path needs
    one character before its dot. The optional quote after the anchor applies at the greedy
    end only -- inside the run the next character is whitespace.
    """
    gap = anchor.span("gap")
    starts = []
    if anchor.end() < len(raw) and raw[anchor.end()] in _DELIMITED_QUOTES:
        starts.append(anchor.end() + 1)
    starts.append(anchor.end())
    starts.extend(gap[0] + k for k in range(gap[1] - gap[0] - 1, 0, -1))
    return starts


def _in_workspace_create_file_match(text: str) -> DelimitedWriteMatch | None:
    """First `inside this workspace create <path> with text: <content>` -- the same match the
    legacy pattern above found, in bounded work per anchor instead of a lazy path walk."""
    raw = str(text or "")
    tails = _iter_with_text_tails(raw)
    candidates = _delimited_path_candidates(raw, tails)
    if not candidates:
        return None
    dots = [dot for dot, _e, _c in candidates]
    quotes = [match.start() for match in re.finditer(r"[`'\"]", raw)]
    for anchor in _IN_WORKSPACE_ANCHOR_RE.finditer(raw):
        for path_start in _anchor_path_starts(anchor, raw):
            run_end = _quote_free_end(raw, path_start, quotes)
            i = bisect_left(dots, path_start + 1)
            if i < len(dots) and dots[i] < run_end:
                _dot, ext_end, content_start = candidates[i]
                return DelimitedWriteMatch(
                    path=raw[path_start:ext_end], content=raw[content_start:]
                )
        # No dot in any run this anchor allows; the next anchor owns its own runs.
    return None


def _folder_directory_starts(raw: str, marker: tuple[tuple[int, int], tuple[int, int], int]) -> list[int]:
    """Directory-start positions an `in` marker allows, in the engine's backtracking order.

    The engine gives the optional `the` back first (the directory can absorb it), then shrinks
    the marker's own whitespace one character at a time, retrying the optional greedily at
    each width. Everything left of that whitespace is fixed by the preceding literal.
    """
    gap, opt, marker_end = marker
    starts: list[int] = [marker_end]
    if opt != (-1, -1):
        starts.append(opt[0])
    for k in range(gap[1] - gap[0] - 1, 0, -1):
        base = gap[0] + k
        probe = _THE_AFTER_IN_RE.match(raw, base)
        starts.append(probe.end() if probe is not None else base)
        starts.append(base)
    return starts


#: `the\s+` retried after the marker's whitespace shrinks.
_THE_AFTER_IN_RE = re.compile(r"the\s+", re.IGNORECASE)


def _file_in_folder_candidates(raw: str) -> list[tuple[int, int, int, str]]:
    """``(dot, ext_end, content_start, directory)`` for every continuation that can succeed.

    A dot's extension run, the `in` marker after it, and the first `folder saying` marker with
    an all-class directory before it are anchor-independent, so they are computed once; a
    directory character outside the class breaks every longer candidate at the same marker
    (the prefix property), which is why the first marker decides each variant.
    """
    n = len(raw)
    folder_marks = _iter_folder_saying_marks(raw)
    if not folder_marks:
        return []
    folder_starts = [start for start, _end, _pad in folder_marks]
    next_nonclass = _next_outside_class(raw, _FOLDER_DIR_CHARS)
    candidates: list[tuple[int, int, int, str]] = []
    for run in re.finditer(r"[A-Za-z0-9_+-]+", raw):
        start = run.start()
        if start == 0 or raw[start - 1] != ".":
            continue
        marker = _match_in_marker(raw, run.end())
        if marker is None:
            continue
        for dir_start in _folder_directory_starts(raw, marker):
            f = bisect_left(folder_starts, dir_start + 1)
            if f >= len(folder_starts):
                continue
            fstart = folder_starts[f]
            if next_nonclass[dir_start] < fstart:
                # a character outside the directory class breaks every longer candidate too
                continue
            directory = raw[dir_start:fstart]
            mark = folder_marks[f]
            content_start = _content_start_after(
                mark[1], mark[2], n, pad_min=1
            )
            if content_start is None:
                continue
            candidates.append((start - 1, run.end(), content_start, directory))
            break
    candidates.sort()
    return candidates


def _next_outside_class(text: str, chars: frozenset[str]) -> list[int]:
    """For each position, the first position at or after it outside ``chars``."""
    n = len(text)
    nxt = [n] * (n + 1)
    for i in range(n - 1, -1, -1):
        nxt[i] = i if text[i] not in chars else nxt[i + 1]
    return nxt


def _file_in_folder_saying_match(text: str) -> DelimitedWriteMatch | None:
    """First `create file <path> in <dir> folder saying <content>` -- the same match the legacy
    pattern above found, without the lazy path and directory walks."""
    raw = str(text or "")
    candidates = _file_in_folder_candidates(raw)
    if not candidates:
        return None
    dots = [dot for dot, _e, _c, _d in candidates]
    quotes = [match.start() for match in re.finditer(r"[`'\"]", raw)]
    for anchor in _FILE_IN_FOLDER_ANCHOR_RE.finditer(raw):
        for path_start in _anchor_path_starts(anchor, raw):
            run_end = _quote_free_end(raw, path_start, quotes)
            i = bisect_left(dots, path_start + 1)
            if i < len(dots) and dots[i] < run_end:
                _dot, ext_end, content_start, directory = candidates[i]
                return DelimitedWriteMatch(
                    path=raw[path_start:ext_end],
                    content=raw[content_start:],
                    directory=directory,
                )
    return None


#: The directory class `[A-Za-z0-9 _./-]` of the file-in-folder grammar.
_FOLDER_DIR_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _./-"
)
_APPEND_FILE_RE = re.compile(
    rf"\bappend(?:\s+a)?(?:\s+\w+)?\s+line\s+to\s+[`\"']?(?P<path>{_WORKSPACE_FILE_RE})[`\"']?\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_APPEND_CONTENT_ONLY_RE = re.compile(
    r"\b(?:append|add)\s+(?:(?:a|one)\s+more\s+|another\s+|a\s+second\s+|second\s+)?line(?:\s+exactly)?\s*:?\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# The other word order the same demand arrives in: the content BEFORE the path, no colon.
# "append the line goodnight to notes.txt", "append hello to notes.txt". At base only the
# path-first colon form ("append a line to notes.txt: goodnight") minted a typed append, so
# this order fell out of every extractor into the builder's "no bounded builder path" refusal.
# The optional noun between the article and the content is the CONTENT-TYPE word of the same
# retired grammar ("a line", "the line") — a closed set, and required to be followed by the
# anchor "to <file target>" at end of text, so free text is never eaten as filler.
_APPEND_TEXT_TO_FILE_RE = re.compile(
    rf"\bappend(?:\s+(?:a|an|the|one|another|second))?(?:\s+(?:more\s+)?line|\s+text|\s+sentence|\s+entry)?\s+(?P<content>.+?)\s+to\s+(?:the\s+|this\s+)?(?:file\s+)?(?P<path>{_WORKSPACE_TARGET_RE})[`\"']?\s*[.!]?$",
    re.IGNORECASE | re.DOTALL,
)
_OVERWRITE_FILE_RE = re.compile(
    rf"\boverwrite(?:\s+only)?\s+[`\"']?(?P<path>{_WORKSPACE_TARGET_RE})[`\"']?\s+with\s+(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_EXACT_FILES_RE = re.compile(
    rf"\bcreate\s+exactly\s+\w+\s+files:\s*(?P<paths>{_WORKSPACE_FILE_RE}(?:\s*,\s*{_WORKSPACE_FILE_RE})+)\.\s*put\s+(?P<contents>.+?)\s+respectively\b",
    re.IGNORECASE | re.DOTALL,
)
# STRUCTURAL literal arms — the shape itself marks the text as text.
# A colon right after the target: "create notes.txt: hello", "write out.log: first line".
_TARGET_COLON_CONTENT_RE = re.compile(
    rf"\b(?:create|write|make|save|add)\s+(?:a\s+|the\s+|new\s+|this\s+)*(?:file\s+)?(?:(?:named|called)\s+)?"
    rf"(?P<path>{_WORKSPACE_TARGET_RE})\s*:\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_PATH_TRAVERSAL_RE = re.compile(r"(?:^|/)\.\.(?:/|$)")
_ROOTED_PATH_RE = re.compile(r"^(?:/[^\s]*|~[^\s]*|[A-Za-z]:[\\/][^\s]*)$")

# Fences around captured content: "containing:\n```\nhello\n```". The fence is formatting
# around the payload, not payload; strip a matching wrapper, keep inner bytes verbatim.
_FENCED_WHOLE_RE = re.compile(
    r"^```[A-Za-z0-9_+-]*[ \t]*\r?\n(?P<body>.*)\r?\n?```\s*$",
    re.IGNORECASE | re.DOTALL,
)
# A BARE content capture that ENDS in a LOCATIVE prepositional phrase is boundary-ambiguous
# (more content, or the destination of the write) and is refused, never guessed. The closed
# class is the same locative set the destination bridge already accepts — signature and
# dative tails ("hello from qa", "the results for the team") are content and stay content.
# No noun or destination vocabulary; sentence structure only.
_TRAILING_PREPOSITION_PHRASE_RE = re.compile(
    r"\s(?:in|inside|into|within|under|underneath|beneath|at|on|onto|upon|"
    r"near|beside|between|among|around|behind|beyond|amid|along)\b"
    r"\s+\S[^,.;:!?]*$",
    re.IGNORECASE,
)

# A CODE-SPEC brief: a shape noun for generated code followed by a call signature or a behavior
# clause — "a function called foo", "a function greet(name) that returns a greeting string".
# This is a description of what the file SHOULD DO, not text to copy into it; the named-file
# build lane (which generates the code) owns it, exactly as `content_is_a_brief` already owns
# "a two-line summary of what a linter does". Composed AFTER that reader: same classification,
# same consumer (the builder), same consequence (the request text never lands in the file).
_CODE_SPEC_NOUNS = (
    r"(?:functions?|methods?|classes|module|modules|script|scripts|program|programs|routine|routines|handler|handlers)"
)
_CODE_SPEC_BRIEF_RE = re.compile(
    rf"^(?:an?\s+|\d+\s+)?(?:[a-z0-9_-]+\s+){{0,2}}{_CODE_SPEC_NOUNS}\s*$"
    rf"|^(?:an?\s+|\d+\s+)?(?:[a-z0-9_-]+\s+){{0,2}}{_CODE_SPEC_NOUNS}\b[^:;\n]{{0,120}}?"
    rf"(?:\(\s*[^)]*\)?|\b(?:that|which|called|named|taking|accepting|returning|returns|for)\b)",
    re.IGNORECASE,
)

LITERAL = "literal"
BRIEF = "brief"
MODE_CREATE = "create"
MODE_OVERWRITE = "overwrite"
MODE_APPEND = "append"


def verbatim_request_text(source_context: dict[str, object] | None = None, routing_text: str = "") -> str:
    """The request text exactly as the user typed it, from the canonical typed request.

    Input normalization collapses whitespace before routing, so a multiline literal file
    content ("create notes.txt containing hello<newline>today is tuesday") reached every
    router with the newline already destroyed and the file was written with the collapsed
    single line. The canonical TurnRequest minted at ingress keeps the user's own text
    verbatim; content-bearing resolution reads from it. Routing, classification and marker
    matching keep the normalized text — only the CONTENT a write will place on disk must be
    the user's bytes.

    A planned SUB-TURN is one demand unit under the parent's external turn: it runs on a
    copy of the parent context and therefore still carries the PARENT's TurnRequest, but the
    text it is serving is its own task text. Returning the parent's request there re-executed
    the parent's write inside every sibling unit (measured: the "what is 2 plus 2?" unit of a
    mixed turn answered with an approval prompt for the file the write unit had already
    written). So a sub-turn's verbatim request is its own routing text.
    """
    context = dict(source_context or {})
    if context.get("planned_subturn"):
        return str(routing_text or "")
    request = context.get("turn_request")
    text = str(getattr(request, "user_text", "") or "")
    if not text and isinstance(request, dict):
        text = str(request.get("user_text") or "")
    return text or str(routing_text or "")


@dataclass(frozen=True)
class WriteItem:
    """One file the demand asks to place bytes in."""

    path: str
    content: str
    action: str = "write"  # "write" | "append"
    content_kind: str = LITERAL


@dataclass(frozen=True)
class WriteDemand:
    """The typed write demand a turn carries, or the brief demand the builder owns."""

    mode: str = ""
    items: tuple[WriteItem, ...] = ()
    refused_targets: tuple[tuple[str, str], ...] = ()
    directory: str = ""
    run_requested: bool = False
    brief_summaries: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_literal(self) -> bool:
        """Every captured item carries text the user marked or that is not a brief."""
        return bool(self.items) and all(item.content_kind == LITERAL for item in self.items)

    @property
    def has_literal_writes(self) -> bool:
        return any(item.content_kind == LITERAL for item in self.items)

    def literal_items(self) -> tuple[WriteItem, ...]:
        return tuple(item for item in self.items if item.content_kind == LITERAL)

    def write_payloads(self) -> list[dict[str, object]]:
        """The `workspace.write_file`-shaped payloads for the literal items."""
        return [
            {"intent": "workspace.write_file", "arguments": {"path": item.path, "content": item.content}}
            for item in self.literal_items()
        ]


def _classify_content(content: str, *, marked_literal: bool) -> str:
    """LITERAL when the user marked the text as text or the run is not a brief; else BRIEF."""
    text = str(content or "").strip()
    if not text:
        return BRIEF
    if marked_literal:
        return LITERAL
    from core.execution.constants import content_is_a_brief

    if content_is_a_brief(text):
        return BRIEF
    if _CODE_SPEC_BRIEF_RE.match(text):
        return BRIEF
    return LITERAL


def _strip_content_fences(content: str) -> str:
    match = _FENCED_WHOLE_RE.match(str(content or "").strip())
    if match:
        return str(match.group("body") or "")
    return str(content or "")


def _split_ambiguous_content_tail(content: str) -> tuple[str, bool]:
    """Whether a BARE content capture ends in a prepositional phrase it cannot own.

    A capture that ends "... hello in this project" has two honest parses — the phrase is
    either more content or the destination of the write — and no structural reader can pick
    between them. This authority does not guess: the capture is reported AMBIGUOUS and the
    demand refuses. The user who means the bytes has the literal markers (a colon, a quote,
    "with exactly this content:", "that says") — a marked capture never reaches this check
    and stays verbatim to the end. Only closed-class prepositions are inspected; no noun or
    destination vocabulary, and no silent fallback: an inspection failure refuses too.
    """
    text = str(content or "").strip()
    if not text:
        return text, False
    tail = _TRAILING_PREPOSITION_PHRASE_RE.search(text)
    if tail is None:
        return text, False
    return text, True


def confine_target(
    candidate: str,
    *,
    base_dir: str = "",
    workspace_root: str = "",
) -> tuple[str, str]:
    """The workspace-relative form of a raw target, or the reason it is refused.

    Returns ``(path, "")`` when the target resolves inside the workspace and
    ``("", reason)`` when it does not: ``traversal`` for a parent-walk, ``outside_root``
    for a rooted path that is not inside the bound workspace. Order matters: the relative
    cleaner lstrips leading "./" characters, which would silently eat the "../" off
    "../escaped.txt" and turn an escape into an in-workspace relocation.
    """
    raw = str(candidate or "").strip().strip("`\"'")
    if not raw:
        return "", ""
    if ".." in raw.replace("\\", "/").split("/"):
        return "", "traversal"
    resolved = ""
    if raw and workspace_root:
        try:
            resolved_workspace_root = Path(workspace_root).expanduser().resolve()
            resolved_candidate = Path(raw).expanduser().resolve()
            if resolved_candidate == resolved_workspace_root or resolved_workspace_root in resolved_candidate.parents:
                resolved = resolved_candidate.relative_to(resolved_workspace_root).as_posix()
        except Exception:
            resolved = ""
    if not resolved:
        # A ROOTED path that did not resolve inside the workspace is not a relative
        # one. Stripping its leading "/" would relocate a destination the user named
        # outside the workspace; an outside-root path has no honest workspace-relative
        # form, so it is refused and the turn answers from the refusal lanes.
        if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", raw):
            return "", "outside_root"
        resolved = _clean_relative_path(raw)
    if not resolved:
        return "", "not_a_file_target"
    if base_dir and "/" not in resolved:
        resolved = f"{base_dir.rstrip('/')}/{resolved}"
    return resolved, ""


def _clean_relative_path(candidate: str) -> str:
    """The planner's relative-path cleaner, kept byte-compatible with its behaviour."""
    clean = str(candidate or "").strip().strip("`\"'").strip().rstrip(".,!?")
    if not clean:
        return ""
    from core.execution.planner import _PATH_STOP_WORDS

    if clean.lower() in _PATH_STOP_WORDS:
        return ""
    clean = clean.lstrip("/")
    clean = clean.lstrip("./")
    if not clean or clean.lower() in _PATH_STOP_WORDS:
        return ""
    if ".." in clean.split("/"):
        return ""
    if "." not in posixpath.normpath(clean).rpartition("/")[2]:
        return ""
    return clean


def _content_of(match: re.Match, name: str = "content") -> str:
    return str(match.group(name) or "").strip()


def _dedupe(items: list[WriteItem]) -> list[WriteItem]:
    seen: set[str] = set()
    ordered: list[WriteItem] = []
    for item in items:
        if not item.path or not item.content or item.path in seen:
            continue
        seen.add(item.path)
        ordered.append(item)
    return ordered


#: A sentence that performs or qualifies a write: a write verb, a file noun, or a content marker.
_WRITING_SENTENCE_RE = re.compile(
    r"\b(?:create|make|write|put|save|add|append|generate|touch|new)\b|\bfiles?\b|\bcontaining\b|\bwith\s+the\s+text\b"
    r"|[A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml|csv|html|css)\b",
    re.IGNORECASE,
)


def resolve_write_demand(text: str, *, workspace_root: str = "") -> WriteDemand | None:
    """The typed write demand ``text`` carries, or ``None`` when it names none.

    None is the honest answer for everything that is not a file-write demand — questions,
    project scaffolds, briefs about what a file SHOULD CONTAIN are classified, not written:
    a brief demand is returned with ``content_kind=brief`` items so the builder can own it
    under EXACT scope, and every path is confinement-resolved before it leaves this module.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    # Input normalization protects "notes.txt." from losing its extension to sentence
    # punctuation; the retired grammar expects the same repaired text the planner always saw.
    raw = _repair_split_extensions(raw)
    lowered = raw.casefold()

    from core.turn_ir import parse_turn_ir

    # Use the shared source boundaries before declaring file contents literal.
    # Otherwise a trailing question becomes protected content and disappears
    # from the same turn's remaining demands.
    source_clauses = parse_turn_ir(raw, response_shape_parser=None).clauses

    base_dir = ""
    try:
        from core.execution.planner import (
            _DIRECTORY_CREATE_MARKERS,
            _extract_workspace_bootstrap_path,
            _extract_workspace_parent_directory,
        )

        # Only a sentence that WRITES may name the destination folder. Read over the whole
        # message, "Create a.txt containing hi. Also, what is the weather in Rome right now?"
        # resolved a.txt under a directory named Rome (measured 2026-09-07).
        writing_sentences = " ".join(
            clause.request_text for clause in source_clauses
            if _WRITING_SENTENCE_RE.search(clause.request_text)
        ) or raw
        base_dir = _extract_workspace_parent_directory(writing_sentences, workspace_root=workspace_root)
        if not base_dir and any(marker in raw.lower() for marker in _DIRECTORY_CREATE_MARKERS):
            base_dir = _extract_workspace_bootstrap_path(raw)
    except Exception:
        base_dir = ""

    items: list[WriteItem] = []
    refused: list[tuple[str, str]] = []
    directory = ""
    mode = MODE_CREATE

    def add(
        raw_path: str,
        content: str,
        *,
        action: str = "write",
        marked_literal: bool = False,
        strip_fences: bool = False,
    ) -> None:
        content_text = _strip_content_fences(content) if strip_fences else str(content or "").strip()
        resolved, reason = confine_target(raw_path, base_dir=base_dir, workspace_root=workspace_root)
        if reason:
            if raw_path:
                refused.append((raw_path, reason))
            return
        if not resolved or not content_text:
            return
        items.append(
            WriteItem(
                path=resolved,
                content=content_text,
                action=action,
                content_kind=_classify_content(content_text, marked_literal=marked_literal),
            )
        )

    exact_multi = _CREATE_EXACT_FILES_RE.search(raw)
    if exact_multi is not None:
        paths = [item.strip() for item in str(exact_multi.group("paths") or "").split(",")]
        contents = [item.strip() for item in str(exact_multi.group("contents") or "").split(",")]
        for index, path in enumerate(paths):
            if index < len(contents):
                add(path, contents[index], marked_literal=True)
        if items:
            return _finish(items, refused, directory="", mode=MODE_CREATE, raw=raw)

    folder_first = _FOLDER_FIRST_CREATE_FILE_RE.search(raw)
    if folder_first is not None:
        from core.execution.planner import _clean_workspace_directory_path

        directory = _clean_workspace_directory_path(
            str(folder_first.group("directory") or "").strip(),
            workspace_root=workspace_root,
        )
        resolved, reason = confine_target(
            str(folder_first.group("path") or "").strip(),
            # The folder THIS sentence named is the destination — never the generic
            # bootstrap path, which for "make folder X here and put y.txt inside ..."
            # can resolve to a stray word and silently relocate the file.
            base_dir=directory,
            workspace_root=workspace_root,
        )
        if reason:
            refused.append((str(folder_first.group("path") or "").strip(), reason))
        else:
            content = _content_of(folder_first)
            if resolved and content:
                items.append(WriteItem(path=resolved, content=content, action="write", content_kind=LITERAL))
        if items:
            return _finish(items, refused, directory=directory, mode=MODE_CREATE, raw=raw)

    in_workspace = _in_workspace_create_file_match(raw)
    if in_workspace is not None:
        add(in_workspace.path.strip(), in_workspace.content.strip(), marked_literal=True)
        if items:
            return _finish(items, refused, directory="", mode=MODE_CREATE, raw=raw)

    file_in_folder = _file_in_folder_saying_match(raw)
    if file_in_folder is not None:
        from core.execution.planner import _clean_workspace_directory_path

        directory = _clean_workspace_directory_path(
            file_in_folder.directory.strip(),
            workspace_root=workspace_root,
        )
        resolved, reason = confine_target(
            file_in_folder.path.strip(),
            base_dir=directory,
            workspace_root=workspace_root,
        )
        if reason:
            refused.append((file_in_folder.path.strip(), reason))
        else:
            content = file_in_folder.content.strip()
            if resolved and content:
                items.append(WriteItem(path=resolved, content=content, action="write", content_kind=LITERAL))
        if items:
            return _finish(items, refused, directory=directory, mode=MODE_CREATE, raw=raw)

    overwrite = _OVERWRITE_FILE_RE.search(raw) if "overwrite" in lowered else None
    if overwrite is not None:
        add(str(overwrite.group("path") or "").strip(), _content_of(overwrite), marked_literal=True)
        if items:
            return _finish(items, refused, directory=base_dir, mode=MODE_OVERWRITE, raw=raw)

    seen_paths: set[str] = set()
    # Every grammar in this family needs a literal content marker (case-insensitive: with /
    # that says / saying / containing / holding) somewhere after its target. When none occurs,
    # no pattern in the family can match at any position and the searches are skipped --
    # without the skip, each of the text's anchors walked its unbounded `in`-bridge to the end
    # of the line hunting a marker that never existed (measured: a 32000-anchor single-line
    # payload spent minutes in this loop before returning nothing). Skipping a provably
    # matchless search changes no verdict.
    family_can_match = any(
        marker in lowered
        for marker in ("with", "that says", "saying", "containing", "holding")
    )
    for pattern in (
        _CREATE_NAMED_FILE_WITH_CONTENT_RE,
        _INLINE_CREATE_FILE_RE,
        _PLAIN_CREATE_FILE_WITH_CONTENT_RE,
    ) if family_can_match else ():
        for match in pattern.finditer(raw):
            path_match = str(match.group("path") or "").strip()
            content = _strip_content_fences(_content_of(match))
            marked_literal = pattern is not _PLAIN_CREATE_FILE_WITH_CONTENT_RE
            if not marked_literal:
                # "containing exactly: X" — the word "exactly" is the user marking the text
                # as text; strip the marker and treat the rest as verbatim.
                exact_marker = re.match(r"^exact(?:ly)?\s*:?\s*(.+)$", content, re.IGNORECASE | re.DOTALL)
                if exact_marker is not None:
                    marked_literal = True
                    content = str(exact_marker.group(1) or "").strip()
            if not marked_literal:
                owner = next((clause for clause in source_clauses
                              if clause.start <= match.start() < clause.end), None)
                if owner is not None and match.start("content") < owner.end < match.end("content"):
                    content = raw[match.start("content"):owner.end].rstrip()
                # A bare capture ending in a locative prepositional phrase has two honest
                # parses (more content, or the destination): refuse, never guess. A marked
                # capture never reaches this check.
                content, ambiguous_tail = _split_ambiguous_content_tail(content)
                if ambiguous_tail:
                    if path_match:
                        refused.append((path_match, "ambiguous_content"))
                    continue
                # A sentence-final period the unit splitter or the user's own sentence left on
                # the boundary is punctuation, not payload. One trailing dot, plain captures
                # only — a marked-literal capture stays byte-verbatim.
                if content.endswith("."):
                    content = content[:-1].rstrip()
            resolved, reason = confine_target(path_match, base_dir=base_dir, workspace_root=workspace_root)
            if reason:
                if path_match:
                    refused.append((path_match, reason))
                continue
            if not resolved or not content or resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            items.append(
                WriteItem(
                    path=resolved,
                    content=content,
                    action="write",
                    # The named and inline patterns require an explicit literal marker
                    # ("with exactly this content:", "that says:") — the user marked the
                    # text as text. The plain pattern's capture is permissive and is
                    # classified: a brief is a description of the file, not the file.
                    content_kind=_classify_content(content, marked_literal=marked_literal),
                )
            )

    # STRUCTURAL arms: the shape itself marks the content as literal.
    if not items:
        colon = _TARGET_COLON_CONTENT_RE.search(raw)
        if colon is not None:
            add(str(colon.group("path") or "").strip(), _content_of(colon), marked_literal=True)
        if not items:
            quoted = re.search(
                rf"\b(?:create|write|make|save|add)\s+(?:a\s+|the\s+|new\s+|this\s+)*(?:file\s+)?(?:(?:named|called)\s+)?(?P<path>{_WORKSPACE_TARGET_RE})\s+[`\"'](?P<content>[^`\"'\n]+)[`\"']",
                raw,
                re.IGNORECASE,
            )
            if quoted is not None:
                add(str(quoted.group("path") or "").strip(), _content_of(quoted), marked_literal=True)

    append_first = _APPEND_FILE_RE.search(raw)
    if append_first is not None:

        raw_path = str(append_first.group("path") or "").strip()
        if not raw_path:
            return None
        add(raw_path, _content_of(append_first), action=MODE_APPEND, marked_literal=True)
        if items:
            return _finish(items, refused, directory=base_dir, mode=MODE_APPEND, raw=raw)
    append_text = _APPEND_TEXT_TO_FILE_RE.search(raw)
    if append_text is not None:
        add(str(append_text.group("path") or "").strip(), _content_of(append_text), action=MODE_APPEND, marked_literal=True)
        if items:
            return _finish(items, refused, directory=base_dir, mode=MODE_APPEND, raw=raw)

    # The bootstrap directory flows with the demand exactly as the old planner contract
    # did (`writes, base_dir`), so a chain that names a folder first still plans the
    # directory bootstrap before its writes.
    return _finish(items, refused, directory=directory or base_dir, mode=mode, raw=raw)


def _finish(
    items: list[WriteItem],
    refused: list[tuple[str, str]],
    *,
    directory: str,
    mode: str,
    raw: str,
) -> WriteDemand | None:
    ordered = _dedupe(items)
    run_requested = False
    try:
        from core.agent_runtime.builder.mutation_scope import _RUN_REQUEST_RE

        run_requested = bool(_RUN_REQUEST_RE.search(" ".join(raw.lower().split())))
    except Exception:
        run_requested = False
    brief_summaries = tuple(item.content for item in ordered if item.content_kind == BRIEF)
    if not ordered and not refused:
        return None
    return WriteDemand(
        mode=mode,
        items=tuple(ordered),
        refused_targets=tuple(refused),
        directory=directory,
        run_requested=run_requested,
        brief_summaries=brief_summaries,
    )


__all__ = [
    "BRIEF",
    "LITERAL",
    "MODE_APPEND",
    "MODE_CREATE",
    "MODE_OVERWRITE",
    "WriteDemand",
    "WriteItem",
    "confine_target",
    "resolve_write_demand",
    "verbatim_request_text",
]
