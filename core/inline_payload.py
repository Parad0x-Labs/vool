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
from typing import Any

#: A content label followed by an opening quote or fence: `Contents: "...`, `text: '...`,
#: `body: ```...`. The quote/fence is what separates SUPPLYING content from describing it.
_LABELLED_PAYLOAD_RE = re.compile(
    r"\b(?:contents?|text|body|transcript|message|document|comment|snippet|payload|excerpt)\s*"
    r"[:=]\s*[\"'“‘`]",
    re.IGNORECASE,
)

#: A fenced block anywhere in the turn is supplied content by construction.
_FENCED_PAYLOAD_RE = re.compile(r"```.+?```", re.DOTALL)

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


def turn_supplies_its_own_content(text: Any) -> bool:
    """Whether this turn pastes the very content it asks about.

    True only when BOTH are present: an analysis ask (summarize/review/translate/...) and a
    supplied payload (a labelled quote, a fenced block, or a "here is the text:" paste). Either
    alone is not enough: "summarize meeting.txt" names a file to read, and "save this text: ..."
    supplies content for a WRITE, which is real machine work.
    """

    turn = str(text or "")
    if not turn.strip():
        return False
    if not _ANALYSIS_ASK_RE.search(turn):
        return False
    return bool(
        _LABELLED_PAYLOAD_RE.search(turn)
        or _FENCED_PAYLOAD_RE.search(turn)
        or _HERE_IS_PAYLOAD_RE.search(turn)
        or (_PASTED_CODE_SCOPE_RE.search(turn) and _SOURCE_SYNTAX_RE.search(turn))
    )


__all__ = ["turn_supplies_its_own_content"]
