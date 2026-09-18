"""A request to create a NAMED source file is a build this runtime can do.

Found by a QA drive on 2026-07-30, driving the live daemon. Four clear build instructions produced
four different failures, and two of them ended at the same dead end::

    make a file at <workspace>/qa1/fizz.py with a fizzbuzz function, and a test_fizz.py next to it. go.
    Could you please create <workspace>/qa2/greet.py containing a function greet(name) ... ?

Both answered "I do not have a real bounded builder path for that request on this runtime." That
answer was false. The model-driven build lane (``builder/app_builder.py``) writes exactly this kind
of file, generates its tests and runs them -- the same drive watched it do so for a third phrasing.

The dead end was a scope mismatch, not a missing capability. ``controller_profile`` sends a turn to
the model-build lane only when ``is_build_instruction(scope="project")`` holds -- "build me a CLI",
a whole deliverable. A request that names its files instead of its product ("create greet.py") is an
ARTIFACT-scope instruction, and artifact scope is owned by the literal-write lane in
``core/execution/planner.py``. But that lane writes only content the user supplied verbatim; it
declines a BRIEF ("containing a function greet(name) that returns a greeting string") precisely so
it never writes the request into the file. So the brief-content build belonged to neither lane, and
the capability-gap report was what fell out.

This module is the missing predicate: artifact-scope build instruction AND at least one concrete
source filename. It is strictly NARROWER than the artifact gate it builds on -- it adds a
requirement, it removes none -- so the discuss-vs-build restraint is inherited whole. "should we
write a fizzbuzz?" is still a question, because ``is_build_instruction`` still says so.

The second job here is the destination. The same drive asked for a script "in <workspace>/qa3" and
got one in ``<workspace>/reverse.py/`` -- ``extract_requested_builder_root`` matched "called
reverse.py" as a FOLDER name, and dropped the rooted directory the user actually named because
rooted paths have no workspace-relative meaning. Inside the active workspace they do: that path IS
reachable, so the honest answer is to rebase it, not to discard it and build somewhere else. Silent
relocation is the failure this project keeps removing.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

from core.agent_runtime import build_request_intent

# Extensions that make a token a source file rather than prose. Deliberately a closed list: a bare
# `\w+\.\w+` also matches "app.py"-shaped things like "v1.2", "Node.js" and "e.g".
#
# Owned by the shared intent module, because it needs the same list: a named file is what overrides
# the "this verb is aimed at prose" veto there, and two copies of one vocabulary drifting apart is
# the defect `build_request_intent` was written to remove.
_SOURCE_EXTENSIONS = build_request_intent.SOURCE_EXTENSIONS
_EXT_ALT = "|".join(_SOURCE_EXTENSIONS)

# A file the user NAMED. Two shapes, because a rooted destination and a bare filename are written
# differently: `/Users/me/ws/qa1/fizz.py` and `fizz.py` both name one file.
_NAMED_FILE_RE = re.compile(
    r"(?<![\w`'\"])(?P<path>(?:~|[A-Za-z]:[\\/]|/)?[\w][\w./\\-]*\.(?:" + _EXT_ALT + r"))\b",
    re.IGNORECASE,
)


def is_source_filename(candidate: str) -> bool:
    """True when this path ends in a source-file extension -- i.e. it names a FILE, not a folder."""
    suffix = posixpath.splitext(str(candidate or "").replace("\\", "/"))[1].lstrip(".").lower()
    return bool(suffix) and suffix in _SOURCE_EXTENSIONS


_has_source_extension = is_source_filename


def workspace_relative_path(candidate: str, *, workspace_root: str) -> str:
    """``candidate`` expressed relative to the workspace, or ``""`` if it is not inside it.

    A rooted path is only meaningless to a workspace-relative caller when it points OUTSIDE the
    workspace. ``write_root_honesty`` refuses those by name. This answers the other half.
    """
    raw = str(candidate or "").strip().strip("`\"'")
    root = str(workspace_root or "").strip()
    if not raw or not root:
        return ""
    try:
        resolved_root = Path(root).expanduser().resolve()
        resolved = Path(raw).expanduser().resolve()
    except Exception:
        return ""
    if resolved == resolved_root:
        return ""
    if resolved_root not in resolved.parents:
        return ""
    return resolved.relative_to(resolved_root).as_posix()


def named_build_files(text: str, *, workspace_root: str = "") -> list[str]:
    """Workspace-relative paths of the source files this request names, in the order given.

    A rooted path inside the workspace is rebased. A rooted path outside it is dropped -- this lane
    must never answer about a destination it cannot reach, and ``write_root_honesty`` already owns
    saying so.
    """
    found: list[str] = []
    for match in _NAMED_FILE_RE.finditer(str(text or "")):
        candidate = str(match.group("path") or "").strip().rstrip(".,;:!?)")
        if not candidate or not _has_source_extension(candidate):
            continue
        if candidate.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", candidate):
            rebased = workspace_relative_path(candidate, workspace_root=workspace_root)
            if not rebased:
                continue
            candidate = rebased
        candidate = candidate.replace("\\", "/").lstrip("./")
        if not candidate or ".." in candidate.split("/"):
            continue
        if candidate not in found:
            found.append(candidate)
    return found


def named_file_build_root(text: str, *, workspace_root: str = "") -> str:
    """The workspace-relative FOLDER the named files belong in, or ``""`` for the workspace root.

    Taken from the first named file, because that is the destination the user stated. When they gave
    a bare filename with no folder there is nothing to honour, and the caller keeps its own default.
    """
    for path in named_build_files(text, workspace_root=workspace_root):
        parent = posixpath.dirname(path).strip("/")
        if parent:
            return parent
    return ""


# Two ways of asking that the shared imperative gate does not see, both found by driving EIGHT FRESH
# phrasings after the first fix and watching two of them answer "I couldn't map that cleanly to a
# real action":
#
#   i need <ws>/g5/wordcount.py that counts words in a block of text
#   knock up <ws>/g7/initials.py that turns a full name into initials, ta
#
# Neither carries a build verb in imperative position. "need"/"want" is a statement, and "knock up"
# is not in anyone's verb list. `write_root_honesty` already made the same call for the same reason
# -- its comment records that "I need a file at /tmp/..." with no verb was read as a request to READ
# the file -- so this is the established reading, kept LOCAL to this lane rather than widened into
# the shared gate, because here it is bounded by a named source file.
_COLLOQUIAL_BUILD_RE = re.compile(
    r"\b(?:knock\s+(?:up|together)|whip\s+up|throw\s+together|put\s+together|cook\s+up|bang\s+out|"
    r"spin\s+up|set\s+me\s+up\s+with)\b",
    re.IGNORECASE,
)
# "i need X" is a request for X. "i need to know what X does" is a question, and the difference is
# the verb that follows -- so a mental verb after need/want disqualifies it.
_NEED_RE = re.compile(
    r"\b(?:i\s+)?(?:need|want|would\s+like)\b(?!\s+to\s+"
    r"(?:know|see|understand|read|check|find|learn|hear|ask|decide|remember))",
    re.IGNORECASE,
)
# A question stays a question however it is phrased. Checked against the whole message, because
# "do i need a setup.py?" is a question whose need-word would otherwise carry it.
_QUESTION_LEAD_RE = re.compile(
    r"^\s*(?:do|does|did|should|shall|can|could|would|will|is|are|was|were|why|how|what|which|who|when)\b",
    re.IGNORECASE,
)


def _asks_in_another_way(text: str) -> bool:
    """A build asked for without an imperative verb. Narrow, and never a question."""
    body = " ".join(str(text or "").split())
    if not body or _QUESTION_LEAD_RE.match(body) or body.rstrip().endswith("?"):
        return False
    return bool(_COLLOQUIAL_BUILD_RE.search(body) or _NEED_RE.search(body))


def looks_like_named_file_build_request(text: str, *, workspace_root: str = "") -> bool:
    """True for "create <named source file> that does X" -- a build, stated as files not as a product.

    Narrower than ``is_build_instruction(scope="artifact")`` by construction: that gate must hold AND
    the request must name a real source file. Deliberation and opt-out are decided inside the shared
    gate, so "lets talk about whether to write fizz.py" and "just plan fizz.py, do not write" are
    both excluded here for the same reason they are excluded everywhere else.
    """
    body = " ".join(str(text or "").split())
    if not body:
        return False
    # The instruction test is the shared one. Only the NOUN differs: where the shared gate wants a
    # word like "file" or "script", this wants an actual filename -- the most concrete artifact noun
    # there is, and the reason "create qa5/slug.py with a slugify function" was not recognised as a
    # build at all.
    # Deliberation and opt-out are decided once, for BOTH routes in, so the second route cannot
    # become a way around the restraint the first one honours.
    lowered = f" {body.lower()} "
    if build_request_intent.is_opted_out(lowered) or build_request_intent.is_deliberation(lowered):
        return False
    if not build_request_intent.imperative_build_sentences(body) and not _asks_in_another_way(body):
        return False
    return bool(named_build_files(body, workspace_root=workspace_root))


__all__ = [
    "is_source_filename",
    "looks_like_named_file_build_request",
    "named_build_files",
    "named_file_build_root",
    "workspace_relative_path",
]
