"""Refuse a write this build cannot reach, instead of quietly performing it somewhere else.

Found by a blind QA drive on 2026-07-29. Asked to ``create /tmp/vool_qa_build6/notes.md with a
bulleted summary ...``, the runtime wrote ``~/Documents/notes.md`` and answered "Created file
``~/Documents/notes.md``." The write really happened, a real tool receipt backed it, and every
honesty guard passed: the reply was true about what it did and silent about that not being what was
asked. Silent relocation is worse than refusal -- the user is told a file exists, it does exist, and
it is nowhere they will look for it.

So the rule here is narrow and absolute: when the request roots its own path -- absolute POSIX,
``~``-relative, or a Windows drive path -- the answer is either that path or an explanation naming
the roots we can reach. Never a different path presented as though it were the one asked for.

The gate is deliberately conservative about what counts as a write. A rooted path alone is not
enough (``take a look inside /Users/me/Desktop/ledger-demo`` is a read), and a write verb anywhere
in the sentence is not enough either (``read /etc/hosts and make a summary`` writes nothing). The
verb must come BEFORE the path and close to it, which is how a person names a destination.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path

# A path the USER rooted. The lookbehind is what keeps prose out: the slash in "and/or", "24/7",
# "TCP/IP" and "he/she" is preceded by a word character and never starts a path. It also has to keep
# out a slash that is only ROOTED-LOOKING because something else sits right before it: "." is the
# real case found on 2026-08-07 -- "./foo.txt" and "../sibling.txt" both have their "/" preceded by
# a bare period, which the old class didn't exclude, so the match started AT that slash and reported
# the relative path as if it were the absolute path "/foo.txt" or "/sibling.txt". A workspace-
# relative path is never what this gate is for; excluding "." keeps it out the same way "-" already
# keeps "well-/badly-formed" out.
_ROOTED_PATH_RE = re.compile(
    r"(?<![\w`'\"/~\\.-])(?P<path>(?:~(?=[/\\])|[A-Za-z]:[\\/]|/)[^\s`'\"<>|]*)"
)
# ANVIL round 2, 2026-08-07: the first D16 fix truncated the scan at the first guessed content
# marker. That is directional and single-shot -- it missed every shebang that had no marker word
# in front of it ("create ./b.sh\n#!/bin/sh", "make ./a.sh executable, content #!/usr/bin/env
# python3", "create ./d.sh - #!/bin/zsh"), and it discarded any REAL destination that happened to
# be named AFTER the first marker found anywhere in the turn ("I have a folder containing: old
# logs. Now create /tmp/x" truncated before "/tmp/x" ever ran). Replaced below with per-candidate
# classification: each rooted match is judged by its OWN local context, not by where the first
# marker in the whole turn happened to sit.
#
# A rooted-looking string on a code line, in a code fence, or inside a marker's content SPAN is
# PAYLOAD, not a destination -- regardless of where else in the turn a genuine destination is
# named. See `_is_payload_span`.
_CODE_FENCE_RE = re.compile("```")
# ANVIL round 3, 2026-08-07 (R1): a clause boundary (below, now removed) ended a marker's reach at
# the next newline or sentence -- correct for "containing: see /etc/motd for the banner text" on
# one line, wrong for dictated MULTI-LINE content ("...whose contents contains:\n\nprefix=/opt/app
# \nPREFIX=/usr/local\n..."), where line 2 onward is not shebang-adjacent and was mined as a
# destination the moment it crossed a newline. A content marker's span now runs from the marker to
# the first DESTINATION-PREPOSITION-governed candidate after it (see `_DESTINATION_PREPOSITION_RE`)
# -- that is what a real second destination looks like ("...contents: hello at /tmp/foo.txt",
# "...\n\nNow also save a backup at /tmp/backup.ini") -- or to the end of the text if no such
# candidate follows, which is what genuinely-multi-line dictated content needs. See
# `_content_marker_spans`. Vocabulary expanded (colon-optional "containing", "that mentions",
# "contents are", "add a line") for the natural unmarked forms ANVIL attacked directly, without
# growing into an open-ended command/verb/path blacklist -- these are still a small, closed set of
# content-introducing phrases, not a list of forbidden paths or programs.
_CONTENT_MARKER_RE = re.compile(
    r"\bwith\s+(?:the\s+|these\s+|this\s+|exact(?:ly)?\s+)?(?:contents?|text|code|lines?|body)\s*:"
    r"|\bcontaining\s*:?"
    r"|\bcontents?\s+(?:is|are|contains)\b"
    r"|\bthat\s+(?:says|reads|mentions)\s*:?"
    r"|\bsaying\s*:"
    r"|\bwrite\s+this\s*:"
    r"|\bcontent\s*:"
    r"|\badd(?:ing)?\s+a\s+lines?\b",
    re.IGNORECASE,
)
# The structural signal that a real destination follows dictated content, closing that content's
# span right before it: "at", "to", "into", "onto", "under", "inside (of)" immediately ahead of a
# candidate is how a person names a PLACE, not more content. A closed class of English function
# words, not a growing blacklist.
_DESTINATION_PREPOSITION_RE = re.compile(
    r"\b(?:at|to|into|onto|under|inside(?:\s+of)?)\s*\Z", re.IGNORECASE
)
# The other way a content span ends: a genuinely NEW sentence after the marker, governed by its
# OWN write-intent verb ("I have a folder containing: old logs. Now create /tmp/x" -- "Now
# create" is a fresh instruction, not more of the "old logs" content). Requires an actual sentence
# break in between, not just any later verb-shaped word -- a note whose dictated prose happens to
# contain "add" or "create" mid-sentence is not thereby a fresh instruction.
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]\s")
# F7, ANVIL round 2: "create a config from https://example.com/x" mined "//example.com/x" as a
# rooted destination -- the colon before the double-slash wasn't excluded by the lookbehind above
# (excluding it there would also break the legitimate, if rare, "label:/path" phrasing that has no
# double slash). A URL authority section is recognized structurally instead: a candidate starting
# with "//" immediately after a URI scheme is a network resource, never a filesystem path.
_URL_SCHEME_BEFORE_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\Z")
# A destination-naming verb. "need/want a file at ..." earns its place: it is how the QA drive
# phrased build 13 ("I need a file at /tmp/vool_qa_build13/config.yaml with three sample keys"),
# and with no verb in it that request was read as a REQUEST TO READ the file.
_WRITE_INTENT_RE = re.compile(
    r"\b(?:creat(?:e|ing)|mak(?:e|ing)|writ(?:e|ing)|sav(?:e|ing)|put(?:ting)?|plac(?:e|ing)|"
    r"add(?:ing)?|generat(?:e|ing)|scaffold(?:ing)?|bootstrap(?:ping)?|mkdir|touch|drop|append|"
    r"stor(?:e|ing)|set\s+up|setting\s+up|"
    r"need(?:ed)?\s+(?:a|an|the)\s+\w*\s*file|want(?:ed)?\s+(?:a|an|the)\s+\w*\s*file)\b",
    re.IGNORECASE,
)
# How far a destination verb may sit ahead of the path it governs. "put it at /tmp/x.sh" is 10
# characters; "...and then I finally decided to read /etc/hosts" keeps `make` from a later clause
# out of range.
_WRITE_INTENT_WINDOW = 60
# A question about what the USER should do, not an instruction to us: "should I create
# /etc/nginx/nginx.conf myself or use the package default?" First person only -- "can you go ahead
# and set up /tmp/..." and "Could you please create a file ... in /tmp/..." are both real requests
# and must still be answered about their own path.
_USER_OWN_ACTION_RE = re.compile(
    r"\b(?:should|shall|can|could|do|did|would|must|ought)\s+i\b"
    r"|\bi\s+(?:should|could|would|ought\s+to)\s+"
    r"(?:creat|writ|mak|add|put|sav|plac|generat|scaffold)",
    re.IGNORECASE,
)

_SAFE_MACHINE_ROOT_NAMES = ("Desktop", "Downloads", "Documents")


def _clean_path(candidate: str) -> str:
    return str(candidate or "").strip().rstrip(".,;:!?)").strip()


def _is_url_authority(text: str, start: int) -> bool:
    """Whether the match at `start` is a URL's `//host/...` authority section: a network
    resource, not a filesystem path, no matter what governs it."""
    return text[start : start + 2] == "//" and bool(_URL_SCHEME_BEFORE_RE.search(text[:start]))


def requested_rooted_paths(text: str) -> list[str]:
    """Every path in the text that the user rooted, in the order they appear. A URL's authority
    section (`https://host/...`) is never included -- it names a network resource, not a
    filesystem destination."""
    body = str(text or "")
    found: list[str] = []
    for match in _ROOTED_PATH_RE.finditer(body):
        if _is_url_authority(body, match.start()):
            continue
        candidate = _clean_path(match.group("path"))
        if len(candidate) > 1 and candidate not in found:
            found.append(candidate)
    return found


def allowed_write_roots(source_context: dict[str, object] | None) -> list[Path]:
    """Directories this build may write into: the active workspace and the safe machine roots."""
    roots: list[Path] = []
    workspace = str(
        (source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or ""
    ).strip()
    if not workspace:
        try:
            from core.runtime_paths import active_workspace_dir

            workspace = str(active_workspace_dir())
        except Exception:
            workspace = ""
    if workspace:
        with contextlib.suppress(Exception):
            roots.append(Path(workspace).expanduser().resolve())
    home = Path.home()
    for name in _SAFE_MACHINE_ROOT_NAMES:
        try:
            roots.append((home / name).resolve())
        except Exception:
            continue
    return roots


def _is_reachable(candidate: str, roots: list[Path]) -> bool:
    try:
        resolved = Path(candidate).expanduser().resolve()
    except Exception:
        return True  # unparseable: not our call to make, let the normal lanes handle it
    return any(resolved == root or root in resolved.parents for root in roots)


def _write_intent_precedes(text: str, start: int) -> bool:
    """Whether a destination verb sits just before position `start`, rather than anywhere in the
    turn. Position-based (not a substring re-search) so a candidate that appears more than once,
    once as payload and once as a real destination, is judged on its OWN occurrence."""
    window = text[max(0, start - _WRITE_INTENT_WINDOW) : start]
    return bool(_WRITE_INTENT_RE.search(window) and not _USER_OWN_ACTION_RE.search(window))


def _fenced_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) character spans of every COMPLETE ``` ... ``` fence in `text`. An unpaired
    trailing ``` opens no span -- nothing after it is fenced by an opener that never closed."""
    marks = [match.start() for match in _CODE_FENCE_RE.finditer(text)]
    return [(marks[i], marks[i + 1]) for i in range(0, len(marks) - 1, 2)]


def _is_inside_fence(start: int, fence_spans: list[tuple[int, int]]) -> bool:
    return any(fence_start <= start <= fence_end for fence_start, fence_end in fence_spans)


_SHEBANG_ADJACENT_RE = re.compile(r"#!\s{0,4}\Z")


def _is_shebang_adjacent(text: str, start: int) -> bool:
    """Whether `#!` sits directly before this candidate, POSIX-legal optional whitespace and all.
    A shebang is strong payload evidence regardless of what punctuation or words surround it on
    the line -- "#!/bin/sh" on its own line, "#! /bin/sh" (the POSIX-legal space form), "content
    #!/usr/bin/env python3" inline, and "- #!/bin/zsh" after a dash all qualify, because what
    comes immediately before the match is what a shebang always is, not where it sits in the
    sentence."""
    return bool(_SHEBANG_ADJACENT_RE.search(text[max(0, start - 6) : start]))


def _is_destination_preposition_governed(text: str, start: int) -> bool:
    window = text[max(0, start - 20) : start]
    return bool(_DESTINATION_PREPOSITION_RE.search(window))


def _content_marker_spans(text: str, candidate_starts: list[int]) -> list[tuple[int, int]]:
    """(start, end) spans of dictated CONTENT introduced by a marker ("with contents:",
    "containing", "add a line", ...).

    A span runs from the marker to the first candidate after it that is EITHER:
    - destination-preposition-governed ("...contents: hello AT /tmp/foo.txt") -- that candidate
      names a real PLACE, in the same clause as the content itself, or
    - past a genuine sentence break AND governed by its own write-intent verb ("...containing:
      old logs. Now CREATE /tmp/x") -- a fresh instruction, not more of the dictated content,

    or to the end of the text if neither follows. Multi-line dictated content (a script, a config
    file, a note) has no other natural terminator inside the turn; truncating at the next newline
    or sentence is what silently re-opened D16 for line 2 onward (ANVIL R1).
    """
    sentence_boundaries = [match.end() for match in _SENTENCE_BOUNDARY_RE.finditer(text)]
    spans: list[tuple[int, int]] = []
    for marker in _CONTENT_MARKER_RE.finditer(text):
        span_start = marker.end()
        span_end = len(text)
        for candidate_start in candidate_starts:
            if candidate_start < span_start:
                continue
            past_a_sentence_break = any(
                span_start <= boundary <= candidate_start for boundary in sentence_boundaries
            )
            if _is_destination_preposition_governed(text, candidate_start) or (
                past_a_sentence_break and _write_intent_precedes(text, candidate_start)
            ):
                span_end = candidate_start
                break
        spans.append((span_start, span_end))
    return spans


def _is_content_marker_governed(start: int, content_spans: list[tuple[int, int]]) -> bool:
    return any(span_start <= start < span_end for span_start, span_end in content_spans)


def _is_payload_span(
    text: str,
    start: int,
    *,
    fence_spans: list[tuple[int, int]],
    content_spans: list[tuple[int, int]],
) -> bool:
    """Whether the rooted candidate at `start` is dictated CONTENT rather than an addressed
    destination. Judged from the candidate's own local context (its line, its fence, its content
    span) -- never by truncating the scan at some earlier point in the turn, so a real destination
    named later is never silently discarded just because payload appeared first.
    """
    return (
        _is_shebang_adjacent(text, start)
        or _is_inside_fence(start, fence_spans)
        or _is_content_marker_governed(start, content_spans)
    )


def unreachable_write_target(text: str, *, source_context: dict[str, object] | None) -> str:
    """The rooted path this turn asks us to write and cannot reach, or ``""``.

    Returns the FIRST such path, because that is the one the reply has to be about. Every rooted
    candidate is classified on its own local context (`_is_payload_span`) rather than the scan
    being cut off at the first guessed content marker -- a shebang or code fence anywhere in the
    turn marks only ITS OWN candidate as payload, never the ones around it.
    """
    body = str(text or "")
    if not body.strip():
        return ""
    roots = allowed_write_roots(source_context)
    fence_spans = _fenced_spans(body)
    candidate_matches = [
        match for match in _ROOTED_PATH_RE.finditer(body) if not _is_url_authority(body, match.start())
    ]
    content_spans = _content_marker_spans(body, [match.start() for match in candidate_matches])
    for match in candidate_matches:
        start = match.start()
        candidate = _clean_path(match.group("path"))
        if len(candidate) <= 1:
            continue
        if _is_payload_span(body, start, fence_spans=fence_spans, content_spans=content_spans):
            continue
        if _is_reachable(candidate, roots):
            continue
        if _write_intent_precedes(body, start):
            return candidate
    return ""


def unreachable_write_response(path: str, *, source_context: dict[str, object] | None) -> str:
    """The honest answer: name the path we were given, the roots we have, and the write we skipped."""
    roots = allowed_write_roots(source_context)
    workspace = str(roots[0]) if roots else ""
    where = f"the workspace (`{workspace}`)" if workspace else "the active workspace"
    return (
        f"I can't write to `{path}` -- that path is outside every folder this build can write to, "
        f"and I'm not going to put the file somewhere else and report it as done. I can write under "
        f"{where}, or under `~/Desktop`, `~/Downloads` and `~/Documents`. Give me a path under one "
        f"of those and I'll create it there. Nothing was written on this turn."
    )


__all__ = [
    "allowed_write_roots",
    "requested_rooted_paths",
    "unreachable_write_response",
    "unreachable_write_target",
]
