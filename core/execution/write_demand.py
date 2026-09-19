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
from dataclasses import dataclass, field
from pathlib import Path

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
    raw = re.sub(
        r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>py|js|ts|tsx|jsx|txt|md|json|yaml|yml|toml)\b",
        r"\g<stem>.\g<ext>",
        raw,
    )

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
            sentence for sentence in re.split(r"(?<=[.!?])\s+", raw) if _WRITING_SENTENCE_RE.search(sentence)
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

    in_workspace = _IN_WORKSPACE_CREATE_FILE_RE.search(raw)
    if in_workspace is not None:
        add(str(in_workspace.group("path") or "").strip(), _content_of(in_workspace), marked_literal=True)
        if items:
            return _finish(items, refused, directory="", mode=MODE_CREATE, raw=raw)

    file_in_folder = _FILE_IN_FOLDER_SAYING_RE.search(raw)
    if file_in_folder is not None:
        from core.execution.planner import _clean_workspace_directory_path

        directory = _clean_workspace_directory_path(
            str(file_in_folder.group("directory") or "").strip(),
            workspace_root=workspace_root,
        )
        resolved, reason = confine_target(
            str(file_in_folder.group("path") or "").strip(),
            base_dir=directory,
            workspace_root=workspace_root,
        )
        if reason:
            refused.append((str(file_in_folder.group("path") or "").strip(), reason))
        else:
            content = _content_of(file_in_folder)
            if resolved and content:
                items.append(WriteItem(path=resolved, content=content, action="write", content_kind=LITERAL))
        if items:
            return _finish(items, refused, directory=directory, mode=MODE_CREATE, raw=raw)

    overwrite = _OVERWRITE_FILE_RE.search(raw)
    if overwrite is not None:
        add(str(overwrite.group("path") or "").strip(), _content_of(overwrite), marked_literal=True)
        if items:
            return _finish(items, refused, directory=base_dir, mode=MODE_OVERWRITE, raw=raw)

    seen_paths: set[str] = set()
    for pattern in (
        _CREATE_NAMED_FILE_WITH_CONTENT_RE,
        _INLINE_CREATE_FILE_RE,
        _PLAIN_CREATE_FILE_WITH_CONTENT_RE,
    ):
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
