"""Plain-words housekeeping: "which chat did we talk about X in? pin it", "rename this chat to Y and pin it".

The operator asks VOOL to find, pin, rename, archive or move its OWN chats and projects in plain
words. Before this lane those sentences reached a model, which has no tool for them and could
answer "done" for a chat it never renamed. This lane reads the sentence deterministically, runs
each action through ``core.chat_housekeeping`` (which reads every write back), and answers with
what actually happened, failures included.

What this lane claims, and what it never claims:

- A clause is housekeeping only when it names a chat/conversation/project, refers back to one
  ("pin it" after a search, "pin 2"), or asks WHERE something was said ("which chat did we talk
  about X in", "find where we discussed X", "I don't remember which chat"). "Find the file we
  discussed" or "rename the variable to x" are never claimed.
- A message is claimed only if EVERY clause in it is housekeeping. "Rename this chat to Budget and
  explain inflation" is left whole to the composite planners.
- Ambiguity is asked about, never guessed: several matching chats, "pin it" with nothing to point
  at, an unknown project name.
- Only the owner's local session may search across chats or change them. Elsewhere the lane says
  so instead of letting a model pretend.
- Delete is not offered here: it cannot be undone, so it stays a click in the sidebar.
- Each completed action is attested to the demand ledger for the demand it served; an asked,
  refused, forbidden or failed action never is.

What it does NOT make true: the reply and the writes need no model call from this lane, but the
TURN is not model-free. The shared planner is admitted before the front door this lane sits in, so
a compound message ("rename this chat to X and pin it") can be split by a planner model request
first, and background extraction may run after the reply. Never report a housekeeping turn as
having made zero model or provider requests.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.typo_fold import near_miss

LANE_ID = "chat_housekeeping"
COVERAGE = "housekeeping"

# --------------------------------------------------------------------------------------------
# Typo folding with an offset map (titles are cut from the ORIGINAL text, never the folded one)
# --------------------------------------------------------------------------------------------

#: Words this lane keys on. Long heads accept one inserted/dropped letter or a transposition;
#: short heads (< 6 letters) accept only a transposition, so "chart" never becomes "chat".
_FOLD_VOCAB = (
    "chat", "chats", "conversation", "conversations", "convo", "convos", "thread", "threads",
    "project", "projects", "rename", "retitle", "archive", "unarchive", "restore", "remember",
    "recall", "mention", "mentioned", "mentioning", "talking", "talked", "discussed", "discussing",
    "search", "find", "where", "which", "pinned", "unpin", "previously", "something",
)
_TOKEN_RE = re.compile(r"[A-Za-z]+")


def _fold_token(token: str) -> str:
    lowered = token.lower()
    if len(lowered) < 4 or lowered in _FOLD_VOCAB:
        return lowered
    hits = []
    for head in _FOLD_VOCAB:
        if len(head) < 6 and len(head) != len(lowered):
            continue
        if near_miss(lowered, head):
            hits.append(head)
    return hits[0] if len(hits) == 1 else lowered


def fold_with_offsets(text: str) -> tuple[str, list[int]]:
    """(folded lowercase text, offsets) where ``offsets[i]`` is the original index of folded i.

    ``offsets`` has len(folded) + 1 entries so an end position maps too.
    """
    out: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(text):
        gap = text[cursor:match.start()]
        for index, ch in enumerate(gap):
            out.append(ch.lower())
            offsets.append(cursor + index)
        folded = _fold_token(match.group(0))
        span = match.end() - match.start()
        for index, ch in enumerate(folded):
            out.append(ch)
            offsets.append(match.start() + min(index, span - 1))
        cursor = match.end()
    for index, ch in enumerate(text[cursor:]):
        out.append(ch.lower())
        offsets.append(cursor + index)
    offsets.append(len(text))
    return "".join(out), offsets


# --------------------------------------------------------------------------------------------
# Grammar
# --------------------------------------------------------------------------------------------

_CHAT_N = r"(?:chat|conversation|convo|thread|session|discussion)"
_THIS_CHAT = rf"(?:(?:this|the\s+current|current|the|our|my\s+current)\s+{_CHAT_N})"
_ORDINALS = {
    "first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1,
}
_HIT_REF = (
    r"(?:(?:the\s+)?(?:first|second|third|fourth|fifth|top|last|1st|2nd|3rd|4th|5th)"
    r"(?:\s+(?:one|chat|result|hit|match|conversation))?"
    r"|(?:number|no\.?|#)\s*[1-9]|[1-9])"
)
_PRONOUN = (
    rf"(?:it|that(?:\s+(?:one|{_CHAT_N}))?|this\s+one|them|both(?:\s+of\s+them)?|those(?:\s+{_CHAT_N}s)?)"
)
_TARGET = rf"(?:{_THIS_CHAT}|{_HIT_REF}|{_PRONOUN})"
_TAIL = r"(?:\s+(?:please|pls|plz|for\s+me|too|as\s+well|now|thanks|thank\s+you|thx))*\s*[.!?]*\s*$"
#: An action verb must OPEN its clause ("vool, can you rename this chat to X"), so a sentence that
#: merely mentions renaming or pinning ("how do I rename the chat to X?") is never an action.
_LEAD = (
    r"^(?:(?:hey|hi|ok|okay|so|now|vool|please|pls|plz|and|also|then|just|yes|yeah|"
    r"can\s+you|could\s+you|would\s+you|will\s+you|can\s+u|could\s+u|"
    r"i\s+want\s+you\s+to|i'?d\s+like\s+you\s+to|go\s+ahead\s+and|let'?s)\b[\s,]*)*"
)

_PIN_MESSAGE_RE = re.compile(
    _LEAD + r"\b(?P<verb>unpin|pin)\s+(?:(?:your|the|that|this|my)\s+)?(?:(?:last|previous|latest)\s+)?"
    r"(?P<what>answer|reply|response|message)" + _TAIL
)
_PIN_RE = re.compile(
    _LEAD + rf"\b(?P<verb>unpin|pin|star|unstar)\s+(?P<target>{_TARGET})"
    r"(?:\s+(?:to|at|on)\s+(?:the\s+)?top(?:\s+of\s+(?:the\s+|my\s+)?(?:list|sidebar|chats))?)?" + _TAIL
)
_PIN_TOP_RE = re.compile(
    _LEAD + rf"\b(?:keep|put|move|stick)\s+(?P<target>{_THIS_CHAT}|it|that(?:\s+(?:one|{_CHAT_N}))?)\s+"
    r"(?:at|on|to)\s+(?:the\s+)?top\b.*$"
)
_ARCHIVE_RE = re.compile(_LEAD + rf"\b(?P<verb>archive|unarchive|restore)\s+(?P<target>{_TARGET})" + _TAIL)
_ARCHIVE_MOVE_RE = re.compile(
    _LEAD + rf"\b(?:move|put|send)\s+(?P<target>{_THIS_CHAT}|it|that(?:\s+one)?)\s+(?:to|into|in)\s+(?:the\s+)?archives?" + _TAIL
)
_RENAME_PROJECT_RES = (
    re.compile(_LEAD + r"\b(?:rename|retitle)\s+(?P<this>this|the\s+current|current|our|my\s+current)\s+project\s+(?:to|as|into)\s+(?P<new>\S.*)$"),
    re.compile(_LEAD + r"\b(?:rename|retitle)\s+(?:the\s+|my\s+)?project\s+(?P<old>\S.*?)\s+(?:to|as|into)\s+(?P<new>\S.*)$"),
    re.compile(_LEAD + r"\b(?:rename|retitle)\s+(?:the\s+|my\s+)?(?P<old>\S.*?)\s+project\s+(?:to|as|into)\s+(?P<new>\S.*)$"),
)
_RENAME_CHAT_RES = (
    re.compile(_LEAD + rf"\b(?:rename|retitle|re-name|re-title)\s+(?P<target>{_TARGET}|{_CHAT_N})\s+(?:to|as|into)\s*:?\s+(?P<title>\S.*)$"),
    re.compile(_LEAD + rf"\b(?:rename|retitle)\s+(?P<target>{_TARGET}|{_CHAT_N})\s*:\s*(?P<title>\S.*)$"),
    re.compile(_LEAD + rf"\bcall\s+(?P<target>{_THIS_CHAT})\s+(?:as\s+)?(?P<title>\S.*)$"),
    re.compile(_LEAD + rf"\b(?:name|title)\s+(?P<target>{_THIS_CHAT}|it|this\s+one)\s+(?:as\s+|to\s+)?(?P<title>\S.*)$"),
    re.compile(
        _LEAD + rf"\b(?:change|set|update|make)\s+(?:the\s+)?(?:{_CHAT_N}(?:'s|s)?\s+)?(?:title|name)\s+"
        rf"(?:(?:of|for)\s+(?P<target>{_THIS_CHAT}|it|this\s+one)\s+)?(?:to|as|into)\s+(?P<title>\S.*)$"
    ),
)
_MOVE_RES = (
    re.compile(
        _LEAD + rf"\b(?:move|take|remove)\s+(?P<target>{_THIS_CHAT}|it|that(?:\s+one)?)\s+out\s+of\s+"
        r"(?:the\s+|this\s+|its\s+|that\s+)?project" + _TAIL
    ),
    re.compile(
        _LEAD + rf"\b(?:move|put|file|add|assign|attach)\s+(?P<target>{_TARGET})\s+(?:to|into|in|under|onto)\s+"
        r"(?:the\s+|my\s+)?project\s+(?P<project>\S.*?)" + _TAIL
    ),
    re.compile(
        _LEAD + rf"\b(?:move|put|file|add|assign|attach)\s+(?P<target>{_TARGET})\s+(?:to|into|in|under|onto)\s+"
        r"(?:the\s+|my\s+)?(?P<project>\S.*?)\s+project" + _TAIL
    ),
    re.compile(
        _LEAD + rf"\b(?:move|put|file|assign)\s+(?P<target>{_THIS_CHAT})\s+(?:to|into|in|under|onto)\s+"
        r"(?:the\s+|my\s+)?(?P<project>\S.*?)" + _TAIL
    ),
)
_LIST_PINNED_RE = re.compile(
    _LEAD + rf"\b(?:(?:show|list|see)\s+(?:me\s+)?(?:all\s+)?(?:my\s+|the\s+)?pinned\s+{_CHAT_N}s?"
    rf"|(?:which|what)\s+{_CHAT_N}s?\s+(?:are|did\s+i|have\s+i)\s+(?:i\s+)?pinned"
    rf"|what(?:'s|\s+is|\s+are)\s+(?:my\s+)?pinned(?:\s+{_CHAT_N}s?)?)\b"
)

_S_LOC = re.compile(rf"\b(?:where|which|wich|whcih|whihc|what|in\s+which|in\s+what)\s+(?:{_CHAT_N}|project)s?\b")
#: "which project am I working in", "what project is this?": where the operator IS now, which the
#: workspace identity answer owns. A location ask is a search only about what was said or done.
_S_HERE_NOW = re.compile(
    r"\b(?:am\s+i|are\s+(?:we|you|u))\s+(?:currently\s+|now\s+|still\s+)?"
    r"(?:in|on|at|inside|working|using|bound|scoped|set)\b"
    r"|\bis\s+(?:this|that)\s*[?.!]*$"
)
_S_WHERE = re.compile(
    r"\bwhere\s+(?:did\s+|do\s+|have\s+|had\s+)?(?:we|i|you|u)\s+(?:were\s+|was\s+|have\s+|had\s+)?"
    r"(?:talk|talked|talking|discuss|discussed|discussing|mention|mentioned|mentioning|chat|chatted|"
    r"spoke|speak|went\s+over|cover|covered|work|worked|working|figure|figured|plan|planned|write|wrote|"
    r"decide|decided|look|looked|sort|sorted|ask(?:ed)?(?:\s+you)?\s+about)\b"
)
#: A retrieval ask: find it, dig it up, I can't remember / forgot where, take me back to it.
_S_FIND = re.compile(
    r"\b(?:find|search|locate|look\s+(?:\w+\s+)?(?:up|for|through)|dig\s+(?:\w+\s+)?up|"
    r"track\s+(?:\w+\s+)?down|pull\s+(?:\w+\s+)?up|bring\s+(?:\w+\s+)?up|hunt\s+(?:\w+\s+)?down|"
    r"(?:can'?t|cannot|couldn'?t|don'?t|dont|cant)\s+(?:remember|recall|find|know)\s+(?:where|which|wich|when)|"
    r"forg[eo]t\s+(?:where|which|wich|in\s+which)|no\s+idea\s+(?:where|which)|"
    r"take\s+me\s+(?:back\s+)?to|get\s+(?:me\s+)?back\s+to)\b"
)
#: A reference to an earlier conversation: "we talked about", "you told me", "somewhere",
#: "an old chat", "something you did for me".
_S_PAST = re.compile(
    r"\b(?:we|i|you|u)\s+(?:were\s+|was\s+|had\s+|have\s+|did\s+|once\s+|already\s+|previously\s+)?"
    r"(?:talk|talked|talking|discuss|discussed|discussing|mention|mentioned|mentioning|chat|chatted|chatting|"
    r"spoke|speak|said|told|went\s+over|covered|brainstormed|planned|wrote|worked\s+on|figured\s+out|"
    r"looked\s+(?:at|into)|researched|asked(?:\s+you)?\s+about)\b"
    r"|\b(?:you|u)\s+(?:did|made|wrote|built|created|fixed|helped|done)\b.*\b(?:for|with)\s+(?:me|us)\b"
    rf"|\b(?:somewhere|some\s+{_CHAT_N}|another\s+{_CHAT_N}|(?:an?\s+|the\s+|my\s+)?(?:old|older|earlier|previous|past|other)\s+{_CHAT_N}s?"
    rf"|one\s+of\s+(?:my|our|the)\s+{_CHAT_N}s)\b"
)
#: The same phrasing as _S_PAST, cut piece by piece: "you did <topic> for me" keeps its topic.
_S_PAST_CUT = re.compile(
    _S_PAST.pattern.replace(
        r"|\b(?:you|u)\s+(?:did|made|wrote|built|created|fixed|helped|done)\b.*\b(?:for|with)\s+(?:me|us)\b",
        r"|\b(?:you|u)\s+(?:did|made|wrote|built|created|fixed|helped|done)\b|\b(?:for|with)\s+(?:me|us)\b",
    )
)
assert _S_PAST_CUT.pattern != _S_PAST.pattern
_S_FORGOT = re.compile(
    rf"\b(?:don'?t|do\s+not|dont|cant|can'?t|cannot|couldn'?t)\s+(?:remember|recall|find)\s+"
    rf"(?:where|which|in\s+which|what\s+(?:{_CHAT_N}|project))\b"
)
_S_ALL = re.compile(
    rf"\b(?:search|look\s+through|go\s+through|scan|grep)\s+(?:all\s+(?:of\s+)?)?(?:my|our|the)?\s*"
    rf"(?:{_CHAT_N}s|history|chat\s+history|past\s+{_CHAT_N}s|old\s+{_CHAT_N}s)\b"
)
_CHAT_NOUN = re.compile(rf"\b{_CHAT_N}s?\b")
#: With no chat noun, a "find ... we discussed" sentence about these is a file/web/code ask.
_NOT_CHATS = re.compile(
    r"\b(?:file|files|folder|folders|directory|repo|repository|codebase|code|function|class|"
    r"web|online|internet|google|website|email|emails|inbox|document|pdf)\b"
)

_BOUNDARY = re.compile(
    r"(?:\s*[,;]\s*|\s+(?:and|then|&|plus)\s+|\s*[.!?]+\s+)"
    r"(?:(?:also|then|please|pls|and|can\s+you|could\s+you|can\s+u|would\s+you)\s+)*"
    r"(?=(?:unpin|pin|star|unstar|archive|unarchive|restore|rename|retitle|move|put|file|keep|show|list|"
    r"call|name|title|change|set|take|remove|add|assign)\b)"
)
_TITLE_QUOTED = re.compile(r"^\s*[\"“'‘«](?P<q>[^\"”'’»]{1,200})[\"”'’»]")
_TITLE_TAIL = re.compile(r"(?:\s+(?:please|pls|plz|thanks|thank\s+you|thx))+\s*[.!?]*\s*$|[\s.!?,;:]+$", re.IGNORECASE)

#: A name that runs on into another request ("rename this chat to Budget and explain inflation")
#: is two demands, not a long title. The whole message is then left to the composite planners,
#: which hand the rename unit back to this lane on its own.
_TITLE_RUNS_ON = re.compile(
    r"(?:\s+(?:and|then)\s+|\s*[,;]\s*(?:and\s+|then\s+)?)(?:also\s+|please\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:explain|tell|write|give|show\s+me|what|how|why|who|when|summari[sz]e|translate|draft|"
    r"calculate|compute|help|answer|check|fix|build|create|make\s+me|remind)\b",
    re.IGNORECASE,
)

_MUTATING = frozenset({"pin_chat", "unpin_chat", "rename_chat", "archive_chat", "restore_chat", "move_chat", "rename_project", "pin_message", "unpin_message"})


@dataclass(frozen=True)
class Target:
    kind: str            # "current" | "pronoun" | "plural" | "index"
    index: int = 0       # 1-based, or -1 for "last"


@dataclass(frozen=True)
class Action:
    kind: str            # search | list_pinned | pin_chat | unpin_chat | rename_chat | archive_chat |
    #                      restore_chat | move_chat | rename_project | pin_message | unpin_message
    target: Target | None = None
    value: str = ""      # title, project reference, query or new project name
    old: str = ""        # rename_project: the project as named ("" with this_project)
    this_project: bool = False


@dataclass(frozen=True)
class Plan:
    actions: tuple[Action, ...]

    @property
    def mutating(self) -> bool:
        return any(a.kind in _MUTATING for a in self.actions)


def _target_from(text: str) -> Target:
    t = " ".join(str(text or "").split())
    if re.fullmatch(_THIS_CHAT, t) or re.fullmatch(_CHAT_N, t):
        return Target("current")
    if re.fullmatch(r"both(?:\s+of\s+them)?", t):
        return Target("plural", 2)
    if re.fullmatch(r"(?:them|those(?:\s+\w+)?)", t):
        return Target("plural")
    number = re.search(r"[1-9]", t)
    if number and re.fullmatch(r"(?:(?:number|no\.?|#)\s*)?[1-9]", t):
        return Target("index", int(number.group(0)))
    for word, value in _ORDINALS.items():
        if re.search(rf"\b{word}\b", t):
            return Target("index", value)
    return Target("pronoun")


def _cut(original: str, offsets: list[int], start: int, end: int) -> str:
    return original[offsets[start]:offsets[end]]


def _clean_title(raw: str) -> str:
    quoted = _TITLE_QUOTED.match(raw)
    if quoted:
        return " ".join(quoted.group("q").split()).strip()
    text = _TITLE_TAIL.sub("", raw.strip())
    text = text.strip().strip("\"“”'‘’«»").strip()
    return " ".join(text.split())


def _parse_action_clause(original: str) -> Action | None:
    """One clause as a NON-search action, or None."""
    folded, offsets = fold_with_offsets(original)
    f = folded.strip()
    lead = len(folded) - len(folded.lstrip())

    m = _PIN_MESSAGE_RE.match(f)
    if m:
        return Action("pin_message" if m.group("verb") == "pin" else "unpin_message")
    m = _LIST_PINNED_RE.match(f)
    if m:
        return Action("list_pinned")
    for pattern in _RENAME_PROJECT_RES:
        m = pattern.match(f)
        if m:
            new = _clean_title(_cut(original, offsets, lead + m.start("new"), lead + m.end("new")))
            if "this" in m.groupdict() and m.group("this"):
                return Action("rename_project", value=new, this_project=True)
            old = _clean_title(_cut(original, offsets, lead + m.start("old"), lead + m.end("old")))
            if re.search(rf"\b{_CHAT_N}\b", _fold_simple(old)):
                continue   # "rename the chat project ..." is not a project name
            return Action("rename_project", value=new, old=old)
    for pattern in _RENAME_CHAT_RES:
        m = pattern.match(f)
        if not m:
            continue
        target_text = m.groupdict().get("target") or ""
        if pattern is _RENAME_CHAT_RES[4] and not target_text and not _CHAT_NOUN.match(f):
            continue   # "change the name to X" alone could be anything
        title = _clean_title(_cut(original, offsets, lead + m.start("title"), lead + m.end("title")))
        return Action("rename_chat", _target_from(target_text) if target_text else Target("current"), value=title)
    m = _PIN_RE.match(f) or _PIN_TOP_RE.match(f)
    if m:
        verb = m.groupdict().get("verb") or "pin"
        kind = "unpin_chat" if verb in ("unpin", "unstar") else "pin_chat"
        return Action(kind, _target_from(m.group("target")))
    m = _ARCHIVE_RE.match(f)
    if m:
        kind = "archive_chat" if m.group("verb") == "archive" else "restore_chat"
        return Action(kind, _target_from(m.group("target")))
    m = _ARCHIVE_MOVE_RE.match(f)
    if m:
        return Action("archive_chat", _target_from(m.group("target")))
    for index, pattern in enumerate(_MOVE_RES):
        m = pattern.match(f)
        if not m:
            continue
        if index == 0:
            return Action("move_chat", _target_from(m.group("target")), value="general")
        project = _clean_title(_cut(original, offsets, lead + m.start("project"), lead + m.end("project")))
        if not project:
            continue
        return Action("move_chat", _target_from(m.group("target")), value=project)
    return None


def _fold_simple(text: str) -> str:
    return fold_with_offsets(text)[0]


def looks_like_search(clause: str) -> bool:
    """Whether a clause asks WHERE something was said or done across the operator's chats."""
    f = _fold_simple(clause)
    if _S_LOC.search(f) and _S_HERE_NOW.search(f) and not _S_PAST.search(f):
        return False
    if _S_LOC.search(f) or _S_WHERE.search(f) or _S_FORGOT.search(f) or _S_ALL.search(f):
        return True
    if _S_FIND.search(f) and _S_PAST.search(f):
        # "find the file we discussed" is a file ask; with a chat named it is ours again.
        return bool(_CHAT_NOUN.search(f)) or not _NOT_CHATS.search(f)
    # "can you find the conversation about tax returns", "take me back to the chat ..."
    retrieval = _S_FIND.search(f)
    return bool(retrieval and re.search(rf"\b{_CHAT_N}s?\b", f[retrieval.start():]))


def search_query_from(clause: str) -> str:
    """The content words of a search clause: request phrasing and its typos removed."""
    from core.chat_housekeeping import STOPWORDS, tokenize

    long_stopwords = tuple(w for w in STOPWORDS if len(w) >= 5)
    # Cut the request phrasing out first ("take me back to", "where did I ask about", "we talked
    # about", "dig it up"), so its words can never become search terms.
    folded = _fold_simple(clause)
    for pattern in (_S_FIND, _S_PAST_CUT, _S_WHERE, _S_LOC, _S_FORGOT, _S_ALL):
        folded = pattern.sub(" ", folded)
    kept: list[str] = []
    for token in tokenize(folded):
        if token in STOPWORDS:
            continue
        if len(token) >= 4 and any(near_miss(token, word) for word in long_stopwords):
            continue   # "remeber", "somethign", "mentionin"
        if _fold_token(token) in _FOLD_VOCAB:
            continue   # "caht" -> chat, a request word
        kept.append(token)
    return " ".join(kept)


def split_clauses(text: str) -> list[str]:
    """Split at connectors that open another housekeeping action; keep everything else whole.

    A piece after a connector stays separate only if it parses as an action on its own;
    otherwise it is glued back, connector included ("rename this chat to Pin, Archive & Restore
    tips" keeps its title).
    """
    original = str(text or "")
    folded, offsets = fold_with_offsets(original)
    spans: list[tuple[int, int]] = []   # original-text spans
    cursor = 0
    for match in _BOUNDARY.finditer(folded):
        spans.append((offsets[cursor], offsets[match.start()]))
        cursor = match.end()
    spans.append((offsets[cursor], offsets[len(folded)]))
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        piece = original[start:end].strip()
        if not piece:
            continue
        if merged and _parse_action_clause(piece) is None:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [original[start:end].strip() for start, end in merged if original[start:end].strip()]


def parse_housekeeping(text: str) -> Plan | None:
    """The whole message as housekeeping actions, or None when any part is something else."""
    raw = " ".join(str(text or "").split())
    if not raw or len(raw) > 2000:
        return None
    actions: list[Action] = []
    for clause in split_clauses(raw):
        action = _parse_action_clause(clause)
        if action is not None and action.kind in ("rename_chat", "rename_project"):
            quoted = re.search(r"(?:\bto|\bas|\binto|:)\s*:?\s*[\"“'‘«]", clause)
            if not quoted and _TITLE_RUNS_ON.search(action.value):
                return None
        if action is not None:
            # A rename whose title swallowed a search is still a rename; a clause that is a whole
            # action AND a search ("find the chat ... and rename it") was split above already.
            actions.append(action)
            continue
        if looks_like_search(clause):
            actions.append(Action("search", value=search_query_from(clause)))
            continue
        return None
    if not actions:
        return None
    # A plan of only pronoun-target actions with nothing to point at is still ours (we ask).
    return Plan(tuple(actions))


# --------------------------------------------------------------------------------------------
# Demand coverage hooks (core.agent_runtime.demand_ownership)
# --------------------------------------------------------------------------------------------


def covers_unit(unit_text: str) -> bool:
    """Per-unit probe: this fragment alone reads as housekeeping.

    A fragment whose every action points at "it" or "them" names no chat of its own: in "create a
    folder on the desktop. Name it X" the pronoun is the folder. Such a unit is ours only when the
    whole message is housekeeping, which `binds_message` decides."""
    try:
        plan = parse_housekeeping(unit_text)
    except Exception:
        return False
    if plan is None:
        return False
    return not all(
        action.target is not None and action.target.kind in ("pronoun", "plural") for action in plan.actions
    )


def binds_message(text: str, units: Any) -> frozenset[str]:
    """Whole-message binder: when the WHOLE message is housekeeping, every request unit is ours
    ("... I don't remember which chat. can you find it and pin it?" mints "can you find it" as a
    unit that means nothing alone)."""
    try:
        if parse_housekeeping(str(text or "")) is None:
            return frozenset()
    except Exception:
        return frozenset()
    return frozenset(
        str(getattr(unit, "unit_id", "") or "")
        for unit in (units or ())
        if getattr(unit, "kind", "request") == "request" and getattr(unit, "unit_id", "")
    )


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------

_ERROR_TEXT = {
    "chat_not_found": "I can't find that chat any more",
    "chat_deleted": "that chat was deleted",
    "invalid_session_id": "that isn't a valid chat",
    "empty_title": "the new name was empty",
    "empty_project_name": "the project name was empty",
    "project_not_found": "there's no project with that name",
    "ambiguous_project": "more than one project matches that name",
    "project_folder_missing": "the project's folder no longer exists, so its record can't be rewritten",
    "write_not_confirmed": "the change didn't stick when I read it back",
    "message_not_servable": "that message is withheld or erased",
    "pin_refused": "the pin store refused it",
    "no_message": "there's no answer of mine in this chat yet",
}


def _when(ts: str) -> str:
    try:
        moment = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return ""
    now = datetime.now(moment.tzinfo)
    label = f"{moment.day} {moment.strftime('%b')}"
    return label if moment.year == now.year else f"{label} {moment.year}"


def _display_title(title: str) -> str:
    """A sidebar title as a reply names it: an auto title cut at 80 characters gets an ellipsis."""
    clean = " ".join(str(title or "").split())
    if not clean:
        return "New chat"
    if len(clean) >= 79:
        return clean.rstrip(" ,.;:!?-") + "…"
    return clean.rstrip(" ,;:-") or clean


def _quote_adds(snippet: str, title: str) -> bool:
    """Whether quoting the snippet tells the reader more than the title already did."""
    s = " ".join(str(snippet or "").strip("…").split()).lower()
    t = " ".join(str(title or "").split()).lower().rstrip(" ,.;:!?-")
    return bool(s) and not (t and (s.startswith(t[:60]) or t.startswith(s[:60])))


def _chat_label(title: str, *, project_name: str = "", archived: bool = False, current: bool = False) -> str:
    bits = [f"**{_display_title(title)}**"]
    extras = []
    if current:
        extras.append("this chat")
    if project_name:
        extras.append(f"project {project_name}")
    if archived:
        extras.append("archived")
    if extras:
        bits.append("(" + ", ".join(extras) + ")")
    return " ".join(bits)


@dataclass
class _Run:
    session_id: str
    lines: list[str] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    any_mutation: bool = False
    any_failure: bool = False
    referent: list[dict[str, Any]] | None = None     # chats the next pronoun points at
    referent_ambiguous: bool = False
    search_hits: list[dict[str, Any]] | None = None  # this turn's search, for "pin 2" next time
    asked_choice: bool = False                        # the reply already asks which one
    #: Positions in the plan whose action this turn actually completed: a search that ran, the
    #: pinned list, or a write every target of which was read back. Asked, refused, forbidden or
    #: failed actions are never here. This is what the lane attests to the demand ledger.
    completed: list[int] = field(default_factory=list)


def _resolve(run: _Run, target: Target | None, *, recalled: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], str]:
    """Chats a target names, or ([], question) when it can't be resolved without guessing."""
    current = [{"session_id": run.session_id, "current": True}]
    if target is None or target.kind == "current":
        return current, ""
    pool = run.search_hits if run.search_hits is not None else recalled
    if target.kind == "index":
        if not pool:
            return [], "I don't have a list of chats to pick from yet. Ask me to find the chat first."
        index = len(pool) if target.index == -1 else target.index
        if index < 1 or index > len(pool):
            return [], f"There's no number {target.index} in the list; it had {len(pool)}."
        return [pool[index - 1]], ""
    if target.kind == "plural":
        if not pool:
            return [], "I don't have any chats from a search to act on yet."
        if target.index == 2 and len(pool) != 2:
            return [], f"The list has {len(pool)} chats, not two. Say which ones, for example \"pin 1 and pin 3\"."
        return list(pool), ""
    # pronoun
    if run.referent is not None:
        if run.referent_ambiguous:
            return [], "Several chats match, so I haven't changed anything yet. Say \"pin 1\" (or another number)."
        return list(run.referent), ""
    if recalled:
        if len(recalled) == 1:
            return [recalled[0]], ""
        return [], "That could be any of the chats I listed. Say \"pin 1\" (or another number)."
    return [], ""


def _hit_title(hit: dict[str, Any]) -> str:
    from core.chat_housekeeping import describe_chat

    info = describe_chat(str(hit.get("session_id") or "")) or {}
    return str(info.get("title") or hit.get("title") or "New chat")


def _do_search(run: _Run, action: Action, *, wants_pin_after: bool) -> bool:
    """Run one search and answer it. True when the search ran (found, several, or none)."""
    from core.chat_housekeeping import search_chats

    if not action.value.strip():
        run.lines.append("What was it about? Give me a word or two from that conversation and I'll look.")
        run.referent, run.referent_ambiguous, run.asked_choice = [], True, True
        run.search_hits = []
        return False
    result = search_chats(action.value, current_session_id=run.session_id)
    hits = [hit.to_dict() for hit in result.hits]
    run.details.append({"action": "search", "query": action.value, "result": {k: v for k, v in result.to_dict().items() if k != "hits"}, "hit_ids": [h["session_id"] for h in hits]})
    run.search_hits = hits
    terms = action.value.strip()
    if not result.ok:
        run.lines.append("What was it about? Give me a word or two from that conversation and I'll look.")
        run.referent, run.referent_ambiguous, run.asked_choice = [], True, True
        return False
    if not hits:
        chats = f"{result.chats_searched} chat" + ("" if result.chats_searched == 1 else "s")
        projects = f"{result.projects_searched} project" + ("" if result.projects_searched == 1 else "s")
        line = f"I couldn't find “{terms}” in any of your {chats}"
        line += f" or {projects}." if result.projects_searched else "."
        if result.log_trimmed and result.oldest_turn_ts:
            line += f" The oldest message I can still read is from {_when(result.oldest_turn_ts)}; older ones were trimmed."
        if wants_pin_after:
            line += " Nothing was pinned."
        run.lines.append(line)
        run.referent, run.referent_ambiguous, run.asked_choice = [], True, True
        return True
    if result.unique:
        hit = hits[0]
        line = "Found it: " + _chat_label(hit["title"], project_name=hit["project_name"], archived=hit["archived"], current=hit["is_current_chat"])
        when = _when(hit["snippet_ts"] or hit["updated_at"])
        if when:
            line += f", {when}"
        line += "."
        if hit["snippet"] and hit["snippet_role"] != "title" and _quote_adds(hit["snippet"], hit["title"]):
            who = {"user": "You", "assistant": "VOOL", "pinned_message": "Pinned"}.get(hit["snippet_role"], "")
            line += f"\n> {who + ': ' if who else ''}{hit['snippet']}"
        run.lines.append(line)
        run.referent, run.referent_ambiguous = [hit], False
        return True
    run.lines.append(f"I found {len(hits)} chats that mention “{terms}”:")
    for number, hit in enumerate(hits, start=1):
        when = _when(hit["snippet_ts"] or hit["updated_at"])
        entry = f"{number}. " + _chat_label(hit["title"], project_name=hit["project_name"], archived=hit["archived"], current=hit["is_current_chat"])
        if when:
            entry += f", {when}"
        if hit["snippet"] and hit["snippet_role"] != "title" and _quote_adds(hit["snippet"], hit["title"]):
            entry += f": “{hit['snippet']}”"
        run.lines.append(entry)
    if wants_pin_after:
        run.lines.append("Several chats match, so I haven't pinned anything yet. Say \"pin 1\" (or another number).")
    else:
        run.lines.append("Say \"pin 1\" (or another number) to pin one.")
    run.referent, run.referent_ambiguous = hits, True
    run.asked_choice = True
    return True


def _verb(kind: str) -> str:
    return {
        "pin_chat": "pin", "unpin_chat": "unpin", "rename_chat": "rename", "archive_chat": "archive",
        "restore_chat": "restore", "move_chat": "move", "rename_project": "rename the project",
        "pin_message": "pin", "unpin_message": "unpin",
    }.get(kind, kind)


def _fail(run: _Run, kind: str, error: str, *, what: str = "") -> None:
    reason = _ERROR_TEXT.get(error, error.replace("_", " "))
    run.lines.append(f"I couldn't {_verb(kind)}{(' ' + what) if what else ''}: {reason}.")
    run.any_failure = True


def _do_chat_action(run: _Run, action: Action, chats: list[dict[str, Any]]) -> None:
    from core import chat_housekeeping as hk

    for chat in chats:
        sid = str(chat.get("session_id") or "")
        is_current = sid == run.session_id
        label_title = "this chat" if is_current else f"**{_display_title(_hit_title(chat))}**"
        if action.kind in ("pin_chat", "unpin_chat"):
            result = hk.set_chat_pinned(sid, action.kind == "pin_chat", allow_new_chat=is_current)
            if result.ok:
                pinned = action.kind == "pin_chat"
                if not result.changed:
                    run.lines.append(f"{label_title[0].upper() + label_title[1:]} was already {'pinned' if pinned else 'not pinned'}.")
                else:
                    run.lines.append(("Pinned " if pinned else "Unpinned ") + label_title + (" to the top of your chats." if pinned else "."))
                # A chat found by search: pin the matching message inside it too.
                text_hash = str(chat.get("snippet_text_hash") or "")
                if pinned and text_hash and chat.get("snippet_role") in ("user", "assistant"):
                    message = hk.find_message(sid, text_hash)
                    if message is not None:
                        pin = hk.pin_message(sid, message["role"], message["text"], message["ts"])
                        run.details.append({"action": "pin_message", **pin.to_dict()})
                        if pin.ok:
                            run.lines.append("I also pinned the matching message inside it (📌 in that chat).")
            else:
                _fail(run, action.kind, result.error, what=label_title)
        elif action.kind == "rename_chat":
            result = hk.rename_chat(sid, action.value, allow_new_chat=is_current)
            if result.ok:
                before = str(result.before.get("title") or "")
                if is_current:
                    run.lines.append(f"Renamed this chat to **{result.after['title']}**.")
                else:
                    run.lines.append(f"Renamed **{_display_title(before) if before else 'that chat'}** to **{result.after['title']}**.")
            else:
                _fail(run, action.kind, result.error, what=label_title)
        elif action.kind in ("archive_chat", "restore_chat"):
            result = hk.set_chat_archived(sid, action.kind == "archive_chat")
            if result.ok:
                word = "Archived" if action.kind == "archive_chat" else "Restored"
                run.lines.append(f"{word} {label_title}." + (" It's under Archived in the sidebar." if action.kind == "archive_chat" else ""))
            else:
                _fail(run, action.kind, result.error, what=label_title)
        elif action.kind == "move_chat":
            result = hk.move_chat_to_project(sid, action.value, allow_new_chat=is_current)
            if result.ok:
                name = str(result.after.get("project_name") or "General")
                run.lines.append(f"Moved {label_title} to {'General' if not result.project_id else 'project **' + name + '**'}.")
            else:
                candidates = [c for c in result.after.get("candidates", []) if c]
                _fail(run, action.kind, result.error, what=label_title)
                if candidates:
                    run.lines.append("Projects that match: " + ", ".join(f"**{c}**" for c in candidates) + ".")
                elif result.error == "project_not_found":
                    names = [str(p.get("name") or "") for p in _project_list()][:8]
                    if names:
                        run.lines.append("Your projects: " + ", ".join(f"**{n}**" for n in names) + ".")
        else:
            continue
        run.any_mutation = True
        run.details.append({"action": action.kind, **result.to_dict()})


def _project_list() -> list[dict[str, Any]]:
    from core import project_store

    return list(project_store.list_projects())


def _do_rename_project(run: _Run, action: Action) -> None:
    from core import chat_housekeeping as hk

    reference = action.old
    if action.this_project:
        info = hk.describe_chat(run.session_id) or {}
        reference = str(info.get("project_id") or "")
        if not reference:
            run.lines.append("This chat isn't in a project, so there's no project to rename.")
            run.any_failure = True
            return
    result = hk.rename_project(reference, action.value)
    run.any_mutation = True
    run.details.append({"action": "rename_project", **result.to_dict()})
    if result.ok:
        run.lines.append(f"Renamed project **{result.before['name']}** to **{result.after['name']}**.")
        return
    _fail(run, "rename_project", result.error, what=f"“{action.old}”" if action.old else "")
    candidates = [c for c in result.after.get("candidates", []) if c]
    if candidates:
        run.lines.append("Projects that match: " + ", ".join(f"**{c}**" for c in candidates) + ".")


def _do_message_pin(run: _Run, action: Action) -> None:
    from core import chat_housekeeping as hk
    from core import message_pins

    message = hk.last_assistant_message(run.session_id)
    if message is None:
        _fail(run, action.kind, "no_message", what="my last answer")
        return
    if action.kind == "pin_message":
        result = hk.pin_message(run.session_id, "assistant", message["text"], message["ts"])
        run.any_mutation = True
        run.details.append({"action": "pin_message", **result.to_dict()})
        if result.ok:
            run.lines.append("Pinned my last answer in this chat (📌 at the top opens your pins)." if result.changed else "My last answer was already pinned.")
        else:
            _fail(run, action.kind, result.error, what="my last answer")
        return
    clean = message["text"].strip()[:20000]
    pin = next((p for p in message_pins.list_pins(run.session_id) if p.get("role") == "assistant" and p.get("text") == clean), None)
    if pin is None:
        run.lines.append("My last answer wasn't pinned.")
        return
    removed = message_pins.unpin_message(run.session_id, str(pin.get("id") or ""))
    run.any_mutation = True
    run.details.append({"action": "unpin_message", "ok": bool(removed), "session_id": run.session_id})
    if removed:
        run.lines.append("Unpinned my last answer.")
    else:
        _fail(run, action.kind, "write_not_confirmed", what="my last answer")


def _do_list_pinned(run: _Run) -> None:
    from core import chat_housekeeping as hk

    pinned = hk.list_pinned_chats()
    if not pinned:
        run.lines.append("You have no pinned chats yet.")
        run.search_hits = []
        return
    hits = []
    run.lines.append(f"Your pinned chats ({len(pinned)}):")
    for number, item in enumerate(pinned, start=1):
        info = hk.describe_chat(item["session_id"]) or {}
        run.lines.append(f"{number}. " + _chat_label(str(info.get("title") or ""), project_name=str(info.get("project_name") or ""), current=item["session_id"] == run.session_id))
        hits.append({"session_id": item["session_id"], "title": str(info.get("title") or "")})
    run.search_hits = hits
    run.referent, run.referent_ambiguous = hits, len(hits) != 1


def _note_completed_write(run: _Run, position: int, kind: str, written_before: int) -> None:
    """A write completed when this action recorded at least one result and every one read back ok."""
    own = [item for item in run.details[written_before:] if item.get("action") == kind]
    if own and all(bool(item.get("ok")) for item in own):
        run.completed.append(position)


def _record_demand_receipts(message: str, run: _Run, *, plan_text: str) -> None:
    """Attest, per demand unit, the actions this turn completed -- on the same discharge channel
    the conductor and planned sub-turns use (`core.conductor.obligation_ledger`).

    "Rename this chat to X and pin it" is two demands. Both writes ran and were read back, but the
    lane attested neither, and the closure sweep found prose evidence for only one: the turn
    certified 1 satisfied / 1 indeterminate over two real effects. Each completed action's clause
    is located in the user's own text and bound to the demand unit(s) it spans through the one
    geometry (`demands_served_by_spans`). A clause that is not found exactly once binds nothing,
    and an asked, refused or failed action is never attested. Never raises.
    """
    try:
        from core.agent_runtime.answer_coverage import demand_units
        from core.conductor import obligation_ledger
        from core.conductor.planner import demands_served_by_spans

        active = obligation_ledger.active_set()
        if active is None or not run.completed:
            return
        text = str(message or "")
        units = demand_units(text)
        if not units:
            return
        # The plan's actions are its clauses, one each, in the text the plan was parsed from.
        clauses = split_clauses(" ".join(str(plan_text or "").split()))
        spans: list[tuple[int, int, str]] = []
        for position in run.completed:
            if position >= len(clauses):
                continue
            clause = clauses[position]
            first = text.find(clause)
            if first < 0 or text.find(clause, first + 1) >= 0:
                continue
            spans.append((first, first + len(clause), f"{LANE_ID}:{position}"))
        for unit_id, label in demands_served_by_spans(spans, units):
            obligation_ledger.record_slice_consumption(
                active[0], active[1], unit_id=unit_id, family=LANE_ID, evidence="slice_answer_record", excerpt=label,
            )
    except Exception:  # accounting never takes down an answered turn
        return


def execute_plan(plan: Plan, *, session_id: str, mutations_allowed: bool = True) -> _Run:
    from core import chat_housekeeping as hk

    run = _Run(session_id=str(session_id or ""))
    recalled_entry = hk.recalled_results(run.session_id)
    recalled = list(recalled_entry.get("hits") or []) if recalled_entry else None
    for position, action in enumerate(plan.actions):
        written_before = len(run.details)
        if action.kind == "search":
            # Only a singular "pin it" waits on a unique hit; "pin both" / "pin 2" name their own.
            following = next((a for a in plan.actions[position + 1:] if a.kind == "pin_chat"), None)
            wants_pin = following is not None and following.target is not None and following.target.kind == "pronoun"
            if _do_search(run, action, wants_pin_after=wants_pin):
                run.completed.append(position)
            continue
        if action.kind == "list_pinned":
            _do_list_pinned(run)
            run.completed.append(position)
            continue
        if action.kind in _MUTATING and not mutations_allowed:
            run.lines.append(f"You asked me not to change anything, so I didn't {_verb(action.kind)} anything.")
            continue
        if action.kind in ("pin_message", "unpin_message"):
            _do_message_pin(run, action)
            _note_completed_write(run, position, action.kind, written_before)
            continue
        if action.kind == "rename_project":
            _do_rename_project(run, action)
            _note_completed_write(run, position, action.kind, written_before)
            continue
        chats, question = _resolve(run, action.target, recalled=recalled)
        if not chats:
            if question:
                if not run.asked_choice and question not in run.lines:
                    run.lines.append(question)
            elif action.kind == "pin_chat":
                run.lines.append("Pin what: this chat, or my last answer? Say \"pin this chat\" or \"pin your last answer\".")
            else:
                run.lines.append(f"Which chat should I {_verb(action.kind)}? Say \"this chat\", or ask me to find it first.")
            continue
        if action.kind == "archive_chat" and any(str(c.get("session_id") or "") == run.session_id for c in chats):
            # An archived chat is read-only: archiving the one we are talking in would refuse this
            # very reply's transcript row (measured live 2026-10-05: "chat namespace is archived").
            run.lines.append(
                "I can't archive the chat we're talking in, because an archived chat can't take this reply. "
                "Use the archive button on its row, or ask me from another chat, for example "
                "\"find the chat about ... and archive it\"."
            )
            run.any_failure = True
            continue
        if action.kind == "rename_chat" and len(chats) > 1:
            run.lines.append("I can only give one chat that name. Say which one, for example \"rename 1 to ...\".")
            continue
        _do_chat_action(run, action, chats)
        _note_completed_write(run, position, action.kind, written_before)
        # The chats just acted on are what "it" means next.
        run.referent, run.referent_ambiguous = chats, False
    if run.search_hits is not None:
        hk.remember_results(run.session_id, run.search_hits, query=" ".join(a.value for a in plan.actions if a.kind == "search"))
    return run


def maybe_handle_housekeeping_request(
    agent: Any,
    *,
    raw_user_input: str,
    effective_input: str,
    session_id: str,
    source_context: dict[str, object] | None,
    may_finalize: bool = True,
) -> dict[str, Any] | None:
    """The front door's housekeeping lane. Returns {"result": ...} when it owns the turn."""
    text = str(raw_user_input or effective_input or "")
    try:
        plan = parse_housekeeping(text)
    except Exception:
        plan = None
    if plan is None or not may_finalize:
        return None
    from core import chat_housekeeping as hk
    from core.agent_runtime.intent_claims import commands_forbidden_in_context
    from core.request_trust import request_is_owner_local

    if not request_is_owner_local(source_context):
        return {
            "result": agent._fast_path_result(
                session_id=session_id,
                user_input=effective_input,
                response="I can only search, pin or rename your chats from VOOL on this computer, not from here.",
                confidence=0.95,
                source_context=source_context,
                reason="chat_housekeeping_not_owner_local",
            )
        }
    try:
        run = execute_plan(plan, session_id=session_id, mutations_allowed=not commands_forbidden_in_context(source_context))
    except Exception as exc:  # a store failure is reported, never hidden behind a model answer
        run = _Run(session_id=str(session_id or ""))
        run.lines.append(f"I couldn't finish that: the chat store failed ({type(exc).__name__}). Nothing else was changed after the failure.")
        run.any_failure = True
        run.details.append({"action": "error", "error": f"{type(exc).__name__}: {exc}"[:300]})
    hk.record_handled_request(session_id, effective_input or text)
    _record_demand_receipts(str(effective_input or text), run, plan_text=text)
    response = "\n".join(line for line in run.lines if line).strip() or "I didn't find anything to change."
    response = response.replace("…**.", "…**").replace("….", "…")
    details = {"lane": LANE_ID, "actions": run.details}
    if run.any_mutation:
        return {
            "result": agent._action_fast_path_result(
                task_id=f"housekeeping-{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                user_input=effective_input,
                response=response,
                confidence=0.95,
                source_context=source_context,
                reason=LANE_ID,
                success=not run.any_failure,
                details=details,
                task_outcome="success" if not run.any_failure else "failed",
            )
        }
    return {
        "result": agent._fast_path_result(
            session_id=session_id,
            user_input=effective_input,
            response=response,
            confidence=0.95,
            source_context=source_context,
            reason=LANE_ID,
            runtime_event_details=details,
        )
    }


__all__ = [
    "COVERAGE",
    "LANE_ID",
    "Plan",
    "binds_message",
    "covers_unit",
    "execute_plan",
    "fold_with_offsets",
    "looks_like_search",
    "maybe_handle_housekeeping_request",
    "parse_housekeeping",
    "search_query_from",
    "split_clauses",
]
