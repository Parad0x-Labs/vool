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
# The sentence must tell VOOL what to do, not ask it something or describe someone else. This used to be a list of
# ~40 verbs the main clause had to start with, so "whenever I paste a stack trace, point out the failing line" was
# dropped ("point" was not listed). English has a small closed set of words that cannot open an imperative --
# pronouns, determiners, auxiliaries, question words, prepositions -- so the test is the inverse: after the standing
# lead, the condition clause and the politeness words, the main clause must not open with one of those, and must
# not read as a statement about something ("Tables always break", "Being concise matters").
_LEAD_FILLER_RE = re.compile(r"^(?:(?:please|pls|kindly|and|also|so|ok(?:ay)?|right|then)[,\s]+)+", re.IGNORECASE)
_STANDING_LEAD_RE = re.compile(
    r"^(?:from\s+now\s+on|going\s+forward|in\s+(?:the\s+)?future|by\s+default|as\s+a\s+rule)\b[,\s]*", re.IGNORECASE)
_CONDITION_LEAD_RE = re.compile(r"^(?:whenever|every\s+time|each\s+time|any\s+time|before|after|when|once)\b[^,]{1,120},\s*",
                                re.IGNORECASE)
_SUBJECT_YOU_RE = re.compile(
    r"^(?:you\s+(?:should|must|need\s+to|have\s+to|can|could|may|will|ought\s+to)\s+|"
    r"i\s+(?:want|need|would\s+like|'d\s+like|would\s+prefer|'d\s+prefer)\s+you\s+to\s+)", re.IGNORECASE)
_MODE_ADVERB_RE = re.compile(r"^(?:always|never|just|also|don'?t(?:\s+ever)?|do\s+not(?:\s+ever)?)\s+", re.IGNORECASE)
_PREFERENCE_RE = re.compile(r"^i\s+(?:prefer|like|want|need|'d\s+(?:prefer|like|rather)|would\s+(?:prefer|like|rather))\b",
                            re.IGNORECASE)
_NOT_AN_IMPERATIVE = frozenset([
    "i", "me", "my", "mine", "we", "us", "our", "ours", "he", "him", "his", "she", "her", "hers", "it", "its",
    "they", "them", "their", "theirs", "you", "your", "yours", "one", "the", "a", "an", "this", "that", "these",
    "those", "some", "any", "each", "every", "all", "no", "none", "both", "either", "neither", "another", "other",
    "such", "is", "are", "was", "were", "be", "been", "am", "will", "would", "can", "could", "should", "shall",
    "may", "might", "must", "do", "does", "did", "has", "have", "had", "what", "why", "how", "when", "where", "who",
    "whom", "whose", "which", "whether", "if", "though", "although", "because", "since", "unless", "while", "in",
    "on", "at", "for", "with", "about", "of", "to", "from", "by", "into", "onto", "over", "under", "after", "before",
    "during", "between", "through", "without", "there", "here", "not", "and", "or", "but", "nor", "yet", "so", "as",
    "than", "then", "also", "only", "even", "still", "too", "very",
])


def _main_clause(sentence: str) -> str:
    """The sentence with its standing lead, condition clause, politeness and "you should" removed."""
    s = " ".join(str(sentence or "").split())
    s = _LEAD_FILLER_RE.sub("", s)
    s = _STANDING_LEAD_RE.sub("", s)
    s = _LEAD_FILLER_RE.sub("", s)
    s = _CONDITION_LEAD_RE.sub("", s)
    s = _LEAD_FILLER_RE.sub("", s)
    if _PREFERENCE_RE.match(s):
        return s
    s = _SUBJECT_YOU_RE.sub("", s)
    s = _MODE_ADVERB_RE.sub("", s)
    return _LEAD_FILLER_RE.sub("", s)


def _has_lead(sentence: str) -> bool:
    """Whether the sentence opens with a standing lead or a condition ("from now on", "whenever …,")."""
    s = _LEAD_FILLER_RE.sub("", " ".join(str(sentence or "").split()))
    return bool(_STANDING_LEAD_RE.match(s) or _CONDITION_LEAD_RE.match(s))


def _addressed_to_vool(sentence: str) -> bool:
    s = _main_clause(sentence)
    if _PREFERENCE_RE.match(s):
        return True
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", s)
    if not words:
        return False
    first = words[0].lower()
    if first in _NOT_AN_IMPERATIVE or first.endswith("ing"):
        return False
    # "<Something> always/never/is …": a statement about that something, not an order to VOOL.
    return not (len(words) > 1 and words[1].lower() in {"always", "never", "usually", "often", "is", "are", "was",
                                                         "were", "has", "have", "had", "does", "do", "did", "will",
                                                         "would", "can"})


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
    r"[a-z]{4,})\b|^(?:never\s*mind|scratch\s+that)\b|"
    # Said the way people say it when they are tired of something: these take back only an instruction that shares
    # the sentence's topic words, so "the weather got old fast" removes nothing.
    r"\b(?:got|gets|getting|gotten|grown|growing)\s+(?:old|stale|tiresome|annoying|boring|repetitive)\b|"
    r"\bno\s+more\b|\benough\s+(?:with|of)\b|\b(?:i'?m|i\s+am)\s+(?:tired|sick)\s+of\b|"
    r"^(?:please\s+)?(?:lose|ditch|kill|cut(?:\s+out)?)\s+the\b|\bplease\s+stop\b|\bstop\s+(?:it|that|this)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    ["always", "never", "from", "now", "on", "going", "forward", "future", "default", "whenever", "every", "time", "each", "please", "would", "like", "want", "need", "you", "your", "me", "my", "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by", "be", "is", "are", "was", "it", "that", "this", "these", "those", "give", "use", "write", "show", "reply", "answer", "respond", "put", "add", "include", "keep", "make", "stop", "forget", "drop", "rule", "instruction", "about", "any", "more", "longer", "don", "dont", "do", "not", "can", "may", "need", "have", "just", "also", "then", "than", "them", "they", "its", "into", "as", "so",
     "got", "gets", "getting", "gotten", "grown", "growing", "old", "stale", "tiresome", "annoying", "boring",
     "repetitive", "enough", "tired", "sick", "lose", "ditch", "kill", "cut", "out", "those", "these", "please",
     "being", "called", "calling", "i'm"]
)


# A setting that holds ONE value at a time. A new instruction on one of these replaces the old one on the same
# setting ("keep it short" after "give me long answers"); every other instruction stays. A list of separate yes/no
# habits (tables, bullets, emojis, a closing line) is one setting per habit, so "never use tables" and "use bullet
# points" never displace each other.
_FACETS: tuple[tuple[str, re.Pattern[str]], ...] = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in (
    ("length", r"\b(?:short(?:er)?|brief(?:er)?|concise|long(?:er)?|lengthy|detailed|verbose|terse|length|"
               r"(?:one|two|three|four|five|six|\d+)(?:\s+or\s+\w+)?\s+(?:sentences|paragraphs|lines|words))\b"),
    ("language", r"\b(?:english|german|french|spanish|lithuanian|italian|polish|dutch|russian|ukrainian|portuguese|"
                 r"swedish|norwegian|danish|finnish|latvian|estonian|japanese|chinese|korean|language)\b"),
    ("temperature_unit", r"\b(?:celsius|fahrenheit|kelvin|temperatures?)\b"),
    ("distance_unit", r"\b(?:kilomet(?:er|re)s?|km|miles?|met(?:er|re)s|feet|yards|distances?)\b"),
    ("weight_unit", r"\b(?:kilo(?:gram)?s?|kg|pounds|lbs?|ounces|grams|weights?)\b"),
    ("unit_system", r"\b(?:metric|imperial)\b"),
    ("date_format", r"\b(?:dates?|iso|dd/mm(?:/yyyy)?|mm/dd(?:/yyyy)?|yyyy-mm-dd)\b"),
    ("time_format", r"\b(?:24[- ]hour|12[- ]hour|am/pm)\b"),
    ("tone", r"\b(?:tone|formal|informal|casual|friendly|polite|blunt|stiff|warm|playful|professional|chatty)\b"),
    ("emoji", r"\b(?:emojis?|emoticons?)\b"),
    ("tables", r"\btables?\b"),
    ("bullets", r"\bbullet(?:s|\s+points?)?\b"),
    ("headings", r"\bheadings?\b"),
    ("closing", r"\b(?:end|finish|close|sign\s+off)\s+(?:\w+\s+){0,3}with\b"),
))


def _facets(text: str) -> frozenset[str]:
    return frozenset(name for name, pattern in _FACETS if pattern.search(str(text or "")))


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
        settings = _facets(clean)
        for other in rows:
            if (settings and other.get("principal") == principal and other.get("workspace") == workspace
                    and other.get("status") == "active" and settings & _facets(str(other.get("text", "")))):
                other["status"] = "superseded"
                other["superseded_by"] = row["id"]
                other["superseded_at"] = row["created_at"]
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
    if _STANDING_RE.search(s) and _addressed_to_vool(s):
        return True
    lead = _CORRECTION_LEAD_RE.match(s)
    if lead and lead.end() > 0:
        rest = s[lead.end():]
        return bool(_PRESENTATION_RE.search(rest) and _PREFERENCE_VERB_RE.search(rest))
    return False


# A rule said plainly ("Use metric units.", "Before you delete anything, ask me.") carries no "always" or "from now
# on". It is saved only when the WHOLE turn is instructions, optionally with a reason about the owner: beside a task
# or a question the same words are about that task ("Convert 3 cups of flour to grams. Use metric units.").
_STYLE_RE = re.compile(
    r"\b(?:answers?|replies|reply|responses?|pleasantries|small\s+talk|filler|preambles?|disclaimers?|caveats?|"
    r"apolog\w*|jargon|to\s+the\s+point|wording|spelling|british|american|oxford\s+comma)\b", re.IGNORECASE)
# The words of a task, not a rule: an order about one specific thing ("the attached report", "this recipe", "it").
_SPECIFIC_OBJECT_RE = re.compile(
    r"\b(?:it|this|that|these|those|attached|following|above|below)\b|"
    r"\b(?:the|my)\s+(?:\w+\s+){0,2}(?:report|file|text|email|document|draft|article|code|page|message|data|"
    r"recipe|essay|letter|post|paper|notes?|spreadsheet|pdf|slides?|screenshot)\b", re.IGNORECASE)
_WORKFLOW_RE = re.compile(
    r"^(?:(?:please|and|also|so)[,\s]+)*(?:before|after|once)\s+[^,]{1,120},|"
    r"\b(?:before|after)\s+(?:you\s+\w+|\w+ing)\b", re.IGNORECASE)
# A quantity of something ("3 cups of flour", "12 km") is data to work on, so the sentence is a task; a count of
# sentences, lines or words is how long an answer should be.
_TASK_QUANTITY_RE = re.compile(
    r"\b\d[\d.,/]*\s*(?!(?:sentences?|lines?|words?|paragraphs?|bullets?|points?|items?|characters?|hours?)\b)[a-z]+",
    re.IGNORECASE)
_CONTEXT_RE = re.compile(r"^(?:i|i'm|i've|i'd|my|we|we're|our)\b", re.IGNORECASE)


# An implicit correction of the previous answer. A complaint about how it came across ("that was way too formal",
# "too stiff") is a lasting preference when it names a setting of how answers look and is not a one-off ("this
# time", "but it's fine here", "for a birthday card"); a content fix ("that was wrong, it's Canberra") names no
# setting and is about that answer only.
_COMPLAINT_RE = re.compile(
    r"^(?:(?:ugh|hmm|meh|wow|well|no|nope|ok(?:ay)?)[,.!\s]+)*(?:"
    r"(?:(?:that|this|it)(?:\s+(?:one|answer|reply|response))?|your\s+(?:answers?|replies|reply|responses?|tone|writing)|you)"
    r"\s+(?:was|were|is|are|sound(?:s|ed)?|seem(?:s|ed)?|came\s+across|reads?|felt|feels|looked|looks)\b|"
    r"(?:(?:way|much|far)\s+)?too\s+\w+)", re.IGNORECASE)
_EXCESS_RE = re.compile(r"\b(?:too|overly|full\s+of|like\s+a)\b", re.IGNORECASE)
_ONE_OFF_COMPLAINT_RE = re.compile(
    r"\bbut\b.*\b(?:fine|ok|okay|good|alright)\b|\bfor\s+(?:a|an|the|this|that|my|your)\s+\w+", re.IGNORECASE)
# A lasting fact about the owner given as the reason to redo the answer ("I'm vegetarian, redo it").
_LASTING_FACT_RE = re.compile(
    r"^(?:(?:i'?m|i\s+am|we'?re|we\s+are)\s+(?:an?\s+)?(?:vegetarian|vegan|pescatarian|diabetic|coeliac|celiac|teetotal|"
    r"(?:lactose|gluten|[a-z]+)[- ]intolerant|allergic\s+to\s+[a-z][a-z\s-]{1,40})|"
    r"(?:i|we)\s+(?:don'?t|do\s+not|can'?t|cannot|never)\s+(?:eat|drink)\s+[a-z][a-z\s-]{1,40}|"
    r"(?:i|we)\s+keep\s+(?:kosher|halal)(?:\s+at\s+home)?|"
    r"(?:i'?m|i\s+am|we'?re|we\s+are)\s+on\s+an?\s+[a-z-]+\s+diet)$", re.IGNORECASE)
_REDO_RE = re.compile(
    r"\b(?:redo|re-do|try\s+again|do\s+it\s+again|rewrite|re-write|regenerate|start\s+over|swap|replace|"
    r"change\s+(?:it|that|the))\b", re.IGNORECASE)
_CLAUSE_RE = re.compile(r"\s*(?:[,;]|\s+so\s+|\s+and\s+)\s*", re.IGNORECASE)


def _complaint(sentence: str) -> bool:
    s = sentence.strip()
    return bool(_COMPLAINT_RE.match(s) and _EXCESS_RE.search(s) and (_facets(s) or _PRESENTATION_RE.search(s))
                and not _ONE_OFF_RE.search(s) and not _ONE_OFF_COMPLAINT_RE.search(s))


def _lasting_facts(sentences: list[str]) -> list[str]:
    """The owner's lasting facts in a turn that asks for a redo; nothing when there is no redo."""
    if not any(_REDO_RE.search(sentence) for sentence in sentences):
        return []
    facts: list[str] = []
    for sentence in sentences:
        for clause in _CLAUSE_RE.split(sentence):
            clause = clause.strip().rstrip(".!?").strip()
            if clause and _LASTING_FACT_RE.match(clause):
                facts.append(clause + ".")
    return facts


def _plain_kind(sentence: str) -> str:
    """"workflow" / "plain" for a rule said without trigger words, "context" for a reason about the owner, else ""."""
    s = sentence.strip()
    if not s or _QUESTION_RE.search(s) or _ONE_OFF_RE.search(s) or _THIRD_PERSON_RE.search(s):
        return ""
    if _addressed_to_vool(s) and not _SPECIFIC_OBJECT_RE.search(s) and not _TASK_QUANTITY_RE.search(s):
        if _WORKFLOW_RE.search(s):
            return "workflow"
        if _facets(s) or _PRESENTATION_RE.search(s) or _STYLE_RE.search(s):
            return "plain"
    if _CONTEXT_RE.match(s) and not _addressed_to_vool(s):
        return "context"
    return ""


def read_turn(user_input: str) -> tuple[list[str], list[str]]:
    """``(instructions to save, take-back sentences)`` for one owner turn. Pure: saves nothing."""
    text = _authored_text(user_input)
    try:
        from core.hypothetical_frame import detect_hypothetical_frame

        frame = detect_hypothetical_frame(str(user_input or ""))
        if frame.supplies_premises:
            # "Whenever we talk about money, assume euros" orders VOOL to assume: its marker is the verb of an
            # instruction. Any other stipulation ("suppose I told you to …") is a premise and saves nothing.
            orders = {(_main_clause(part).split() or [""])[0].lower().strip(",.")
                      for part in _SENTENCE_RE.split(text)
                      if _addressed_to_vool(part) and _has_lead(part)}
            if not frame.markers or not {str(m).lower() for m in frame.markers} <= orders:
                return [], []
    except Exception:
        pass
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    saves: list[str] = []
    take_backs: list[str] = []
    consumed: set[int] = set()
    for index, sentence in enumerate(sentences):
        # A take-back verb inside a condition ("before you delete anything, ask me") is not a take-back.
        if _TAKE_BACK_RE.search(_main_clause(sentence)) and not _QUESTION_RE.search(sentence):
            marker = _STANDING_RE.search(sentence)
            if marker is None:
                take_backs.append(sentence)
                continue
            # "Drop the X, and from now on Y": the clause before the standing words takes X back, and the rest is
            # the new instruction. Only the instruction half is saved, so a later take-back of X cannot remove it.
            before = sentence[:marker.start()]
            after = sentence[marker.start():].strip()
            if _TAKE_BACK_RE.search(before) and _is_standing(after):
                take_backs.append(before.strip(" ,;-"))
                saves.append(after[:1].upper() + after[1:])
                continue
        if not _is_standing(sentence) and _complaint(sentence):
            following = sentences[index + 1] if index + 1 < len(sentences) else ""
            if following and _addressed_to_vool(following) and not _QUESTION_RE.search(following) \
                    and not _ONE_OFF_RE.search(following) and not _SPECIFIC_OBJECT_RE.search(following):
                consumed.add(index + 1)
                sentence = f"{sentence} {following}"
            saves.append(sentence)
            continue
        if index in consumed or not _is_standing(sentence):
            continue
        # A lesson often arrives with its reason first ("I live in Canada. Always give me Celsius."): the reason
        # is kept with the instruction, so the instruction keeps its meaning in a chat that never saw the reason.
        previous = sentences[index - 1] if index > 0 else ""
        if previous and re.match(r"^(?:i|i'm|i am|my|we|we're|our)\b", previous, re.IGNORECASE) \
                and not _is_standing(previous) and not _QUESTION_RE.search(previous):
            sentence = f"{previous} {sentence}"
        saves.append(sentence)
    saves.extend(fact for fact in _lasting_facts(sentences) if fact not in saves)
    if not saves and not take_backs:
        kinds = [_plain_kind(sentence) for sentence in sentences]
        if all(kinds) and any(kind in ("plain", "workflow") for kind in kinds):
            for index, (sentence, kind) in enumerate(zip(sentences, kinds, strict=True)):
                if kind == "context":
                    continue
                previous = sentences[index - 1] if index > 0 and kinds[index - 1] == "context" else ""
                saves.append(f"{previous} {sentence}" if previous else sentence)
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
        # "No more emojis in your replies" with nothing saved about emojis is a new "never" too -- but only when it
        # is about how answers look; "no more coffee for me after six" is about the owner's evening, not VOOL.
        stop_order = _addressed_to_vool(sentence) and re.match(
            r"^(?:(?:please|and|also|so)[,\s]+)*(?:stop|don'?t|do\s+not|quit)\b", sentence, re.IGNORECASE)
        no_more_order = re.match(r"^(?:(?:please|and|also|so)[,\s]+)*no\s+more\b", sentence, re.IGNORECASE) \
            and (_facets(sentence) or _PRESENTATION_RE.search(sentence))
        if not gone and not _ONE_OFF_RE.search(sentence) and (stop_order or no_more_order):
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
