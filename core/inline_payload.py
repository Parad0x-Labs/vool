"""A turn that carries its own content is answered from that content, not the filesystem.

Measured live 2026-08-15 (MF-17, session dbac716c...): "Please summarize this text file for me:
'meeting.txt'. Contents: \"We discussed Q4 metrics...\"" -- the contents PASTED IN THE TURN -- was
claimed by the workspace runtime fast path, which dispatched workspace.read_file('meeting.txt')
and shipped the tool's not_found ("There is no file at `meeting.txt`...") as the final answer.
Same shape twice in the 10:52 batch, and its sibling: "Review this pull request comment: \"...\""
grabbed by the workspace-audit recognizer ("I can run the audit, but this chat is General...").

The rule: when the turn SUPPLIES a quoted, fenced, or labelled payload, the named file is a label
for that pasted text, not a filesystem target, and the analysis lanes must answer from the
supplied text. A turn that merely NAMES a file ("summarize meeting.txt") still reads the disk;
a turn that DESCRIBES expected contents without supplying them ("read notes.txt, it should
contain my keys") still reads the disk.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

#: A content label followed by an opening quote or fence: `Contents: "...`, `text: '...`,
#: `body: ```...`. The quote/fence is what separates SUPPLYING content from describing it.
_LABELLED_PAYLOAD_RE = re.compile(
    r"\b(?:contents?|text|body|transcript|message|document|comment|snippet|payload|excerpt)\s*"
    r"[:=]\s*[\"'“‘`]",
    re.IGNORECASE,
)

#: A fenced block anywhere in the turn is supplied content by construction.
_FENCED_PAYLOAD_RE = re.compile(r"(`{3,}|~{3,}).+?\1", re.DOTALL)

# A content label owns its unfenced body, including any executable sentences.
# A literal locator can instead be the object of an explicit observation request;
# that authority comes from the prefix, never from sentences inside supplied data.
_PLAIN_PAYLOAD_RE = re.compile(
    r"\b(?:this|(?:the\s+)?following|pasted|supplied)\b"
    r"(?:\s+(?P<content_kind>text|content|contents|file|document|message|transcript|comment|code)\b)?"
    r"[^:\n]*:\s*\n", re.IGNORECASE,
)

#: "here is/here's the text/content/file …:" followed by anything -- the conversational way of
#: pasting.
_HERE_IS_PAYLOAD_RE = re.compile(
    r"\bhere(?:\s+is|'s)\s+(?:the\s+|my\s+|a\s+)?"
    r"(?:text|content|contents|file|document|message|transcript|comment|code)\b[^.!?]{0,40}[:\n]",
    re.IGNORECASE,
)

#: An analysis ask that makes supplied content self-sufficient. Without one of these the payload
#: may be an instruction ("save this text to notes.txt: ...") and the machine lanes keep the turn.
_ANALYSIS_ASK_RE = re.compile(
    r"\b(?:summari[sz]e|review|analy[sz]e|translate|extract|rewrite|proofread|explain|"
    r"critique|classify|shorten|condense|paraphrase|evaluate|assess)\b",
    re.IGNORECASE,
)


# Chat normalization can remove line breaks; an unfenced paste still supplies code.
# Require both an explicit inline/pasted scope and source syntax, not merely a filename.
_PASTED_CODE_SCOPE_RE = re.compile(r"\b(?:pasted|inline|supplied|following)\s+(?:source\s+)?code\b", re.I)
_SOURCE_SYNTAX_RE = re.compile(
    r"\b(?:async\s+def|def|function)\s+[A-Za-z_]\w*\s*\([^)]*\)\s*[:{]"
    r"|\bclass\s+[A-Za-z_]\w*(?:\([^)]*\))?\s*[:{]"
    r"|\b(?:const|let|var)\s+[A-Za-z_]\w*\s*=", re.I,
)


def _plain_body_names_external_input(body: str, *, observation_request: bool) -> bool:
    """Only a request prefix can give a leading locator external-input ownership."""

    def locator(line: str) -> bool:
        value = line.strip().rstrip(".,;")
        quoted = value[:1] in {"'", '"', "`"} and value[-1:] == value[:1]
        if quoted:
            value = value[1:-1]
        elif any(char.isspace() for char in value):
            return False
        return (any(char.isalpha() for char in value)
                and bool(PurePosixPath(value).suffix or "/" in value or "\\" in value))

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return bool(lines) and locator(lines[0]) and (len(lines) == 1 or observation_request)


def turn_supplies_its_own_content(text: Any, *, source_context: dict | None = None) -> bool:
    """Whether this turn pastes the very content it asks about.

    Require an analysis ask and a supplied payload, or a non-mutating request introducing
    fenced or explicitly labelled material. The existing clause kinds distinguish reading shown material from an
    instruction to write it. Naming a file alone still requires a read; supplying text for
    a write still belongs to machine work.
    """

    if source_context is not None:
        from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

        request = source_context.get(TURN_REQUEST_KEY)
        if isinstance(request, TurnRequest):
            text = request.user_text
    turn = str(text or "")
    if not turn.strip():
        return False
    plain = re.search(r":\s*\n", turn)
    if plain and turn[plain.end():].strip():
        from core.turn_ir import ClauseKind, parse_turn_ir

        prefix = parse_turn_ir(turn[:plain.end()], response_shape_parser=None).clauses
        kinds = {clause.kind for clause in prefix}
        # Without a content noun, a transform still introduces data. A batch of
        # questions or actions retains its existing execution owner.
        label = _PLAIN_PAYLOAD_RE.search(turn[:plain.end()])
        supplied_prefix = bool(label and label.group("content_kind")) or (
            len(prefix) == 1 and prefix[0].kind == ClauseKind.TRANSFORM
        )
        external_input = _plain_body_names_external_input(
            turn[plain.end():],
            observation_request=len(prefix) == 1 and prefix[0].kind == ClauseKind.OBSERVE,
        )
        if (supplied_prefix and not external_input
                and kinds & {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.TRANSFORM}
                and ClauseKind.ACT not in kinds):
            return True
    analysis = bool(_ANALYSIS_ASK_RE.search(turn))
    fence = _FENCED_PAYLOAD_RE.search(turn)
    if not analysis and fence:
        from core.turn_ir import ClauseKind, parse_turn_ir

        kinds = {clause.kind for clause in parse_turn_ir(
            turn[:fence.start()], response_shape_parser=None,
        ).clauses}
        analysis = bool(kinds & {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.TRANSFORM}) and ClauseKind.ACT not in kinds
    if not analysis:
        return False
    return bool(
        _LABELLED_PAYLOAD_RE.search(turn)
        or _FENCED_PAYLOAD_RE.search(turn)
        or _HERE_IS_PAYLOAD_RE.search(turn)
        or (_PASTED_CODE_SCOPE_RE.search(turn) and _SOURCE_SYNTAX_RE.search(turn))
    )


__all__ = ["turn_supplies_its_own_content"]
