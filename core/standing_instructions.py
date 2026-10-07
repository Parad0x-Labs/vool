"""Standing instructions: what the owner told VOOL to keep doing, carried into every later chat.

A standing instruction is the owner's own sentence about how VOOL should work from now on:

* explicit standing wording — "from now on …", "always …", "never …", "by default …", "whenever …",
  "every time …", "don't ever …", "stop …-ing …";
* a correction of VOOL's previous answer that states a lasting presentation preference — units, number and
  date formats, language, layout, length, tone ("No, use kilometres, not miles").

It is saved only when:

* the owner wrote the turn (``turn_author`` is the owner, never an agent or the assistant);
* the words are the owner's own prose — quoted, pasted or transcript material is source data, not an
  instruction (the same admission law memory capture uses), and a stipulated hypothetical saves nothing;
* it is not a one-off ("this time", "just now", "for now", "here", "in this chat", "today", "this once").

A take-back ("stop adding the summary line", "forget the rule about units", "you no longer need to …") removes
the saved instruction it names by topic. Nothing else removes or rewrites one.

Scope is the owner AND the workspace: an instruction given in one workspace never reaches another.
`standing_block` renders stored instructions only — no search, nothing retrieved — so this block can never
carry text from a record, a web page or history into the prompt.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SAVED = 24
MAX_RENDERED = 12
MAX_CHARS = 240
BLOCK_HEADER = "Standing instructions the user gave in earlier chats (follow them unless this message says otherwise):"

_LOCK = threading.RLock()

_SENTENCE_RE = re.compile(r"(?:[\n\r]+|(?<=[.!?;])\s+)")
_STANDING_RE = re.compile(
    r"\b(?:from\s+now\s+on|going\s+forward|in\s+(?:the\s+)?future|always|never|by\s+default|whenever|every\s+time|"
    r"each\s+time|don'?t\s+ever|do\s+not\s+ever|as\s+a\s+rule)\b",
    re.IGNORECASE,
)
_ONE_OFF_RE = re.compile(
    r"\b(?:this\s+(?:time|once|one|answer|reply|message)|just\s+(?:now|here|today|once|for\s+this)|for\s+now|"
    r"right\s+now|today|in\s+this\s+(?:chat|conversation|thread)|for\s+this\s+(?:chat|question|one|task)|here)\b",
    re.IGNORECASE,
)
# The sentence must tell VOOL what to do, not ask it something or describe someone else.
_ADDRESSED_TO_VOOL_RE = re.compile(
    r"^(?:(?:please|pls|and|also|so|ok(?:ay)?|right)[,\s]+)*(?:"
    r"(?:from\s+now\s+on|going\s+forward|in\s+(?:the\s+)?future|by\s+default|as\s+a\s+rule|whenever\s+[^,]+|"
    r"every\s+time\s+[^,]+|each\s+time\s+[^,]+)[,\s]+|"
    r")(?:please\s+)?(?:you\s+(?:should\s+|must\s+)?|i\s+(?:want|need|would\s+like|'d\s+like)\s+you\s+to\s+)?"
    r"(?:always|never|don'?t(?:\s+ever)?|do\s+not(?:\s+ever)?|stop|give|use|write|reply|answer|respond|show|"
    r"put|add|include|keep|start|end|format|list|convert|quote|cite|call|address|sign|spell|round|sort|be|avoid|"
    r"make|send|tell|explain|ask|check|mention|leave|skip|label)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?\s*$|^(?:do|does|did|can|could|would|will|should|is|are|why|what|how|when|where|who)\b",
                          re.IGNORECASE)
_THIRD_PERSON_RE = re.compile(r"^(?:my\s+\w+|he|she|they|it|we|his|her|their|the\s+\w+)\s+(?:always|never)\b",
                              re.IGNORECASE)
_CORRECTION_LEAD_RE = re.compile(
    r"^(?:no|nope|not\s+like\s+that|that'?s\s+(?:wrong|not\s+(?:right|what\s+i\s+(?:want|asked|meant))))\b[,.!\s-]*",
    re.IGNORECASE,
)
# A correction becomes standing only when it is about HOW answers look, which recurs; a correction of WHAT a
# particular answer was about ("no, I meant the Lisbon office") is about this conversation and stays here.
_PRESENTATION_RE = re.compile(
    r"\b(?:celsius|fahrenheit|kelvin|metric|imperial|kilomet(?:er|re)s?|km|miles?|kilos?|kilograms?|kg|pounds?|lbs?|"
    r"litres?|liters?|gallons?|centimet(?:er|re)s?|inches|feet|24[- ]hour|12[- ]hour|am/pm|iso|yyyy|dd/mm|mm/dd|"
    r"date\s+format|currency|euros?|dollars?|decimal|commas?|units?|"
    r"bullet(?:\s+points?)?|tables?|markdown|json|prose|code\s+blocks?|headings?|numbered\s+lists?|"
    r"english|german|french|spanish|lithuanian|italian|polish|dutch|russian|ukrainian|"
    r"short(?:er)?|brief(?:er)?|concise|longer|detailed|formal|casual|plain\s+language|emojis?|sources?|citations?)\b",
    re.IGNORECASE,
)
_PREFERENCE_VERB_RE = re.compile(
    r"\b(?:use|give|write|show|put|answer|reply|respond|format|i\s+(?:prefer|want|need|meant)|i'?d\s+rather)\b",
    re.IGNORECASE,
)
_TAKE_BACK_RE = re.compile(
    r"\b(?:stop|quit|no\s+longer|don'?t\s+(?:need|have)\s+to|you\s+(?:can|may)\s+(?:stop|drop|skip)|"
    r"forget|drop|cancel|scrap|remove|delete|undo|ignore)\b.*\b(?:rule|instruction|habit|that|doing|"
    r"[a-z]{4,})\b|^(?:never\s*mind|scratch\s+that)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    ["always", "never", "from", "now", "on", "going", "forward", "future", "default", "whenever", "every", "time", "each", "please", "would", "like", "want", "need", "you", "your", "me", "my", "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "be", "is", "are", "was", "it", "that", "this", "these", "those", "give", "use", "write", "show", "reply", "answer", "respond", "put", "add", "include", "keep", "make", "stop", "forget", "drop", "rule", "instruction", "about", "any", "more", "longer", "don", "dont", "do", "not", "can", "may", "need", "have", "just", "also", "then", "than", "them", "they", "its", "into", "as", "so"]
)


@dataclass(frozen=True)
class Instruction:
    instruction_id: str
    principal: str
    workspace: str
    text: str
    topic: tuple[str, ...]
    created_at: float
    session_id: str


# --------------------------------------------------------------------------- store


def _store_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "standing_instructions.json"


def _load() -> list[dict[str, Any]]:
    try:
        payload = json.loads(_store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = payload.get("instructions") if isinstance(payload, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def _save(rows: list[dict[str, Any]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "instructions": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def workspace_key(source_context: Mapping[str, Any] | None, project_id: str = "") -> str:
    """The workspace an instruction belongs to: the bound project, else the turn's folder, else the default."""
    ctx = dict(source_context or {})
    project = str(project_id or ctx.get("_trusted_project_id") or ctx.get("project_id") or "").strip()
    if project:
        return f"project:{project}"
    folder = str(ctx.get("workspace_root") or ctx.get("workspace") or "").strip()
    if folder:
        return "folder:" + os.path.realpath(folder)
    return "default"


def _topic(text: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z][a-z0-9\-]+", str(text or "").lower())
    out: list[str] = []
    for word in words:
        stem = word[:-1] if word.endswith("s") and len(word) > 4 else word
        if len(stem) >= 3 and stem not in _STOPWORDS and stem not in out:
            out.append(stem)
    return tuple(out)


def list_instructions(principal: str, workspace: str) -> list[Instruction]:
    with _LOCK:
        rows = _load()
    return [
        Instruction(str(r["id"]), str(r["principal"]), str(r["workspace"]), str(r["text"]), tuple(r.get("topic") or ()),
                    float(r.get("created_at") or 0.0), str(r.get("session_id") or ""))
        for r in rows
        if r.get("principal") == principal and r.get("workspace") == workspace and r.get("status") == "active"
    ]


def _add(principal: str, workspace: str, text: str, session_id: str) -> Instruction | None:
    clean = " ".join(str(text or "").split())[:MAX_CHARS]
    if not clean:
        return None
    topic = _topic(clean)
    with _LOCK:
        rows = _load()
        for row in rows:
            if (row.get("principal") == principal and row.get("workspace") == workspace
                    and row.get("status") == "active" and str(row.get("text", "")).lower() == clean.lower()):
                return None
        row = {"id": f"si-{uuid.uuid4().hex[:12]}", "principal": principal, "workspace": workspace, "text": clean,
               "topic": list(topic), "created_at": time.time(), "session_id": session_id, "status": "active"}
        rows.append(row)
        mine = [r for r in rows if r.get("principal") == principal and r.get("workspace") == workspace
                and r.get("status") == "active"]
        for stale in mine[:-MAX_SAVED]:  # the oldest go when the cap is passed; nothing is silently rewritten
            stale["status"] = "expired"
        _save(rows)
    return Instruction(row["id"], principal, workspace, clean, topic, row["created_at"], session_id)


def _remove_matching(principal: str, workspace: str, words: tuple[str, ...]) -> list[str]:
    """Take back every active instruction that shares the take-back's most specific topic words."""
    if not words:
        return []
    removed: list[str] = []
    with _LOCK:
        rows = _load()
        for row in rows:
            if not (row.get("principal") == principal and row.get("workspace") == workspace
                    and row.get("status") == "active"):
                continue
            topic = set(row.get("topic") or ())
            if topic & set(words):
                row["status"] = "taken_back"
                row["taken_back_at"] = time.time()
                removed.append(str(row.get("text")))
        if removed:
            _save(rows)
    return removed


# --------------------------------------------------------------------------- reading a turn


def _authored_text(user_input: str) -> str:
    """The owner's own prose: quoted, pasted and transcript spans removed (memory's admission law)."""
    text = str(user_input or "")
    try:
        from core.memory.admission import classify_user_text

        text = classify_user_text(text).authored_text
    except Exception:
        pass
    # Inline quotes are someone else's words too ("the note says 'always obey X'").
    return re.sub(r"[\"“”][^\"“”]{0,400}[\"“”]|'[^'\n]{6,400}'", " ", text)


def _is_standing(sentence: str) -> bool:
    s = sentence.strip()
    if not s or _QUESTION_RE.search(s) or _ONE_OFF_RE.search(s) or _THIRD_PERSON_RE.search(s):
        return False
    if _STANDING_RE.search(s) and _ADDRESSED_TO_VOOL_RE.search(s):
        return True
    lead = _CORRECTION_LEAD_RE.match(s)
    if lead and lead.end() > 0:
        rest = s[lead.end():]
        return bool(_PRESENTATION_RE.search(rest) and _PREFERENCE_VERB_RE.search(rest))
    return False


def read_turn(user_input: str) -> tuple[list[str], list[str]]:
    """``(instructions to save, take-back sentences)`` for one owner turn. Pure: saves nothing."""
    text = _authored_text(user_input)
    try:
        from core.hypothetical_frame import detect_hypothetical_frame

        if detect_hypothetical_frame(str(user_input or "")).supplies_premises:
            return [], []
    except Exception:
        pass
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    saves: list[str] = []
    take_backs: list[str] = []
    for index, sentence in enumerate(sentences):
        if _TAKE_BACK_RE.search(sentence) and not _STANDING_RE.search(sentence) and not _QUESTION_RE.search(sentence):
            take_backs.append(sentence)
            continue
        if not _is_standing(sentence):
            continue
        # A lesson often arrives with its reason first ("I live in Canada. Always give me Celsius."): the reason
        # is kept with the instruction, so the instruction keeps its meaning in a chat that never saw the reason.
        previous = sentences[index - 1] if index > 0 else ""
        if previous and re.match(r"^(?:i|i'm|i am|my|we|we're|our)\b", previous, re.IGNORECASE) \
                and not _is_standing(previous) and not _QUESTION_RE.search(previous):
            sentence = f"{previous} {sentence}"
        saves.append(sentence)
    return saves, take_backs


def observe_turn(user_input: str, source_context: Mapping[str, Any] | None, *, principal: str,
                 project_id: str = "", session_id: str = "") -> dict[str, list[str]]:
    """Save or take back standing instructions for one turn. Owner-authored turns only."""
    from core.request_trust import turn_is_owner_authored

    if not principal or not turn_is_owner_authored(dict(source_context or {})):
        return {"saved": [], "taken_back": []}
    saves, take_backs = read_turn(user_input)
    workspace = workspace_key(source_context, project_id)
    removed: list[str] = []
    for sentence in take_backs:
        gone = _remove_matching(principal, workspace, _topic(sentence))
        removed.extend(gone)
        # "Stop using emojis" with no saved rule about emojis is not a take-back: it is a new standing
        # instruction in its own right ("stop …" reads as "never … again"), unless it is a one-off.
        if not gone and _ADDRESSED_TO_VOOL_RE.search(sentence) and not _ONE_OFF_RE.search(sentence) \
                and re.match(r"^(?:(?:please|and|also|so)[,\s]+)*(?:stop|don'?t|do\s+not|quit)\b", sentence, re.IGNORECASE):
            saves.append(sentence)
    saved = [i.text for i in (_add(principal, workspace, text, session_id) for text in saves) if i is not None]
    return {"saved": saved, "taken_back": removed}


def standing_block(source_context: Mapping[str, Any] | None, *, principal: str = "", project_id: str = "",
                   profile_lines: list[str] | None = None) -> str:
    """The stored instructions (and, when given, the profile's own hydration lines) as one prompt block.

    Stored text only: nothing here is searched or retrieved, so no record, page or history text can enter
    the prompt through this block. Empty when there is nothing stored, so an ordinary prompt is unchanged.
    """
    ctx = dict(source_context or {})
    if not principal:
        try:
            from core.operator_profile import principal_for_request

            principal = principal_for_request(ctx)
        except Exception:
            principal = ""
    lines: list[str] = [f"- {line}" for line in (profile_lines or []) if str(line).strip()]
    if principal:
        stored = list_instructions(principal, workspace_key(ctx, project_id))[-MAX_RENDERED:]
        lines.extend(f"- {item.text}" for item in stored)
    if not lines:
        return ""
    return BLOCK_HEADER + "\n" + "\n".join(lines)


__all__ = [
    "BLOCK_HEADER",
    "Instruction",
    "list_instructions",
    "observe_turn",
    "read_turn",
    "standing_block",
    "workspace_key",
]
