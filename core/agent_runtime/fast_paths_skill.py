"""Asking for a skill creates one, deterministically, without asking a model to emit tool JSON.

The three skill tools work. Driven through the authorized execution boundary
(``execute_authorized_runtime_tool``, so every dispatch carries the same permission decision the
model tool loop takes) they draft, validate and install a real ``SKILL.md`` that the real loader
loads. Nothing routed chat to them.

Measured 2026-07-30. ``plan_tool_workflow`` returns ``handled=False`` for "create a skill called
qa-echo that takes a word and echoes it back twice", for "author a new skill named qa-greeter" and
for "validate the qa-echo skill" — there is no planner coverage and no fast path anywhere, so the
only route was a small local model choosing to emit ``skill.create`` as tool JSON. That is the exact
failure the fast paths exist to remove, and a blind drive of eight skill phrasings created zero
skills with three of them hanging at 300 s.

One phrasing was worse than uncovered. "new skill: qa-wordcount. counts words in a block of text. go
ahead and create it." routed, deterministically, to ``hive.create_topic`` — it posted a research
TOPIC titled with the request instead of authoring a skill. A wrong tool that answers confidently is
worse than no tool at all.

So the lane is deterministic and narrow: the message must NAME the skill, because a skill with no
name cannot be written and guessing one produces a file the operator did not ask for. What this lane
will not do is INSTALL on its own — ``create`` drafts into staging and says what the next step is,
and activation stays an explicit second request. Authoring is not authorisation to change behaviour,
which is the same line ``core/skill_tools.py`` draws.

Scope, drafting and sharing (2026-10-05). "We need a skill for this chat / for the whole project /
for all of VOOL that does X" names WHERE the skill lives (``core.skill_scopes``), and the skill is
drafted, installed and loaded at that scope only. The draft is written by the turn's own model
(the same seam the conductor's helper generations use: the pinned model when one is pinned, with
its per-turn paid-call cap; the unpaid lane otherwise) as a real SKILL.md: a name, a "Use when"
description, trigger words and step-by-step instructions. When no model can write it, the draft is
made from the operator's own words and the reply says so. A request with a clear purpose but no
name gets a name derived from that purpose, shown in the reply, so the operator can still refer to
it; a request with neither a name nor a purpose is still asked about, never guessed.

Activation stays a separate step: the reply shows the draft and "turn it on" installs exactly those
bytes (the install is bound to the draft's digest), through the same authorized boundary. Export
packages a skill (or a plugin of skills) the operator made as one zip; import stages that zip for a
scope; remove moves a skill out of the loader's tree without deleting it.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any

from core.agent_runtime import build_request_intent

# The word for the thing, so "make me a skill" is this lane and "make me a script" is the builder.
# "skil" is here because a QA drive typed it; a typo in the noun is not a different request.
_SKILL_WORD = r"(?:skills?|skil|skillset)"

# create / author / draft a skill. The verb list is the shared build vocabulary plus "author" and
# "draft", which are how people say it about a skill specifically and about nothing else here.
_CREATE_RE = re.compile(
    r"\b(?:creat(?:e|ing)|mak(?:e|ing)|writ(?:e|ing)|author(?:ing)?|draft(?:ing)?|add(?:ing)?|"
    r"generat(?:e|ing)|build(?:ing)?|new|need|want)\b[^.?!]{0,40}?\b" + _SKILL_WORD + r"\b",
    re.IGNORECASE,
)
# "new skill: qa-wordcount" — a heading, no verb at all. This is the phrasing that reached
# hive.create_topic, so it is spelled out rather than left to the verb pattern.
_HEADING_RE = re.compile(r"\bnew\s+" + _SKILL_WORD + r"\s*[:\-]\s*(?P<name>[\w][\w -]{0,60})", re.IGNORECASE)

_VALIDATE_RE = re.compile(r"\b(?:validat(?:e|ing)|check|verify|lint)\b", re.IGNORECASE)
_INSTALL_RE = re.compile(r"\b(?:install(?:ing)?|activat(?:e|ing)|enabl(?:e|ing))\b", re.IGNORECASE)
# Editing an EXISTING skill: the same authoring lane, but the overwrite is expected and the
# prior version is what the version store preserves. The skill noun is required, so "edit the
# file" and "update the doc" stay in their own lanes.
_EDIT_RE = re.compile(
    r"\b(?:edit|edit(?:ing)?|update|updating|change|changing|revise|revising|amend|amending|"
    r"fix|fixing|improve|improving|rewrite|rewriting)\b[^.?!]{0,40}?\b"
    + _SKILL_WORD
    + r"\b|\b"
    + _SKILL_WORD
    + r"\b[^.?!]{0,40}?\b(?:edit|update|change|revise|amend|fix|improve|rewrite)\b",
    re.IGNORECASE,
)
# Rolling an activated skill back to a recorded version. The skill noun is required so
# "revert my last change" and "roll back the edit in calc.py" stay in the workspace lane.
_ROLLBACK_RE = re.compile(
    r"\b(?:roll\s?back|rollback|revert|restore|undo)\b[^.?!]{0,60}?\b"
    + _SKILL_WORD
    + r"\b|\b"
    + _SKILL_WORD
    + r"\b[^.?!]{0,60}?\b(?:roll\s?back|rollback|revert|restore|undo)\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"\b(?:version|v)\s*[:\-]?\s*(?P<version>\d{1,3})\b|\bback\s+to\s+(?:version\s*|v\s*)?(?P<version2>\d{1,3})\b",
    re.IGNORECASE,
)
# "which skills are installed" asks for the same inventory as "list skills"; the head word is the
# question marker, so `which` sits beside `what` and no noun-led alternative is needed -- one was
# tried and it claimed the STATEMENT "the skill is installed correctly now" as a list request.
_LIST_RE = re.compile(r"\b(?:list|show|what|which)\b[^.?!]{0,30}\b" + _SKILL_WORD + r"\b", re.IGNORECASE)
# The listing is the answer only when the whole message asks for the inventory. Searched anywhere,
# _LIST_RE claimed ordinary questions that merely say "which ... skill": "which skill would you use
# to cook pasta?" and "which skills are in demand for data science jobs?" got the skill inventory.
_INVENTORY_RE = re.compile(
    r"(?:(?:ok(?:ay)?|hey|please|so)[,\s]+)*(?:(?:can|could)\s+you\s+)?"
    r"(?:list|show(?:\s+me)?|what|which)\s+(?:are\s+)?"
    r"(?:(?:all|the|my|your|our|installed|available|active|enabled|current|of)\s+)*"
    + _SKILL_WORD
    + r"(?:\s+(?:do|does|are|is)\s+(?:you|i|we|vool|there|installed|available|loaded|active|enabled)"
    r"(?:\s+(?:have|got|installed|available|loaded|know))?)?"
    r"(?:\s+(?:installed|available|loaded|here|now|right\s+now|on\s+this\s+runtime))?",
    re.IGNORECASE,
)

_PLUGIN_WORD = r"(?:plugins?|plug-ins?)"
# Sharing. The noun is required on both sides, so "export the csv" and "import pandas" stay away.
_EXPORT_RE = re.compile(
    r"\b(?:export|share|package|zip(?:\s+up)?)\b[^.?!]{0,50}?\b(?:" + _SKILL_WORD + "|" + _PLUGIN_WORD + r")\b"
    r"|\b(?:" + _SKILL_WORD + "|" + _PLUGIN_WORD + r")\b[^.?!]{0,50}?\b(?:export(?:ed)?|as\s+a\s+zip|to\s+a\s+zip)\b",
    re.IGNORECASE,
)
_IMPORT_RE = re.compile(
    r"\bimport(?:ing)?\b[^.?!]{0,50}?\b(?:" + _SKILL_WORD + "|" + _PLUGIN_WORD + r")\b"
    r"|\b(?:" + _SKILL_WORD + "|" + _PLUGIN_WORD + r")\b[^.?!]{0,50}?\bimport\b"
    r"|\b(?:install|add|load)\b[^.?!]{0,30}?\b(?:" + _SKILL_WORD + "|" + _PLUGIN_WORD + r")\b[^.?!]{0,30}?"
    r"\b(?:zip|attached|attachment|from\s+(?:this|the|my)\s+file)\b",
    re.IGNORECASE,
)
# Turning a skill off: the verb takes the skill as its OBJECT ("remove the tldr skill", "delete
# skill tldr"), so "a skill to delete old log files" stays a create. "disable" is deliberately
# absent: enable/disable is the skills settings store's verb (core.native_skill_library), and it
# applies to first-party skills this lane must not move.
_REMOVE_RE = re.compile(
    r"\b(?:remove|delete|uninstall|deactivate|turn\s+off|switch\s+off|get\s+rid\s+of)\s+"
    r"(?:(?:the|my|this|that|our)\s+)?(?:[`\"']?[\w-]+[`\"']?\s+)?" + _SKILL_WORD + r"\b",
    re.IGNORECASE,
)
# A plugin made of skills: "create a plugin called research-kit with the tldr and log-triage skills".
_PACK_RE = re.compile(
    r"\b(?:creat(?:e|ing)|mak(?:e|ing)|build(?:ing)?|bundl(?:e|ing)|pack(?:age)?|put|turn)\b[^.?!]{0,80}?\b"
    + _PLUGIN_WORD + r"\b",
    re.IGNORECASE,
)
# "turn it on" after a draft: the activation of THIS chat's pending draft, which needs no name.
# Only the activation itself, optionally with a scope phrase or a courtesy word: "save it to my
# notes" is about something else and must not reach back to a skill draft.
_ACTIVATE_PENDING_RE = re.compile(
    r"^\W*(?:(?:yes|yep|yeah|ok(?:ay)?|sure|great|perfect|good|looks\s+good)\b\W*)?(?:please\s+)?(?:"
    r"(?:turn|switch)\s+(?:it|this|that)\s+on"
    r"|(?:install|activate|enable|save|keep|use)\s+(?:it|this|that|the\s+(?:draft|skill|plugin)|this\s+(?:skill|plugin)|that\s+(?:skill|plugin))"
    r"|go\s+live|make\s+it\s+(?:live|active)"
    r")(?:\s+(?:please|now|then|thanks|thank\s+you))?"
    r"(?:\s+(?:for|in)\s+(?:this\s+chat|this\s+project|all\s+of\s+vool|everything|all\s+chats))?\W*$",
    re.IGNORECASE,
)


# The name, however it was introduced. Quoted or back-ticked wins, then "called/named X", then a
# bare hyphenated token next to the skill word.
_NAME_PATTERNS = (
    # "call it qa-temp" is how a name arrives when it comes in a later sentence than the noun, which
    # is exactly how one QA phrasing wrote it. Without the optional "it" the capture WAS "it".
    re.compile(r"\b(?:call(?:ed)?|nam(?:e|ed))\s+(?:it\s+)?[`\"']?(?P<name>[A-Za-z][\w-]{1,60})", re.IGNORECASE),
    re.compile(r"\b" + _SKILL_WORD + r"\s+[`\"'](?P<name>[^`\"']{2,60})[`\"']", re.IGNORECASE),
    re.compile(r"\b" + _SKILL_WORD + r"\s+(?P<name>[a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", re.IGNORECASE),
    re.compile(r"\b(?P<name>[a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+" + _SKILL_WORD + r"\b", re.IGNORECASE),
)

# Words that mean the sentence is ABOUT skills rather than an instruction to make one. The shared
# deliberation gate covers most of it; these are the skill-specific ones it has no reason to know.
_ABOUT_RE = re.compile(
    r"\b(?:what\s+is\s+a|what\s+are|how\s+do(?:es)?\s+(?:a\s+)?" + _SKILL_WORD + r"|"
    r"how\s+would\s+i|should\s+i|can\s+you\s+explain|explain|tell\s+me\s+about|"
    r"what.{0,12}difference)\b",
    re.IGNORECASE,
)


def _clean_name(raw: str) -> str:
    return " ".join(str(raw or "").replace("`", " ").split()).strip().strip("`\"'.,!?").strip()


def _skill_exists(name: str) -> bool:
    """Whether this named skill already exists as a draft or an activated skill."""
    from core.skill_tools import resolve_skill_markdown

    try:
        found = resolve_skill_markdown(str(name or ""))
    except Exception:
        return False
    return bool(str(found)) and found.is_file()


_NOT_NAMES = {"a", "an", "the", "it", "this", "that", "new", "my", "our", "for", "to", "with", "and", "of"}


def skill_name_in(text: str) -> str:
    """The skill this message names, or ``""``. Never guessed."""
    body = " ".join(str(text or "").split())
    heading = _HEADING_RE.search(body)
    if heading:
        name = _clean_name(heading.group("name"))
        if name:
            return name
    for pattern in _NAME_PATTERNS:
        match = pattern.search(body)
        if match:
            name = _clean_name(match.group("name"))
            # "a skill" and "the skill" are not names.
            if name and name.lower() not in _NOT_NAMES:
                return name
    return ""


_PLUGIN_NAME_PATTERNS = (
    re.compile(r"\b(?:call(?:ed)?|nam(?:e|ed))\s+(?:it\s+)?[`\"']?(?P<name>[A-Za-z][\w-]{1,60})", re.IGNORECASE),
    re.compile(r"\b" + _PLUGIN_WORD + r"\s+[`\"'](?P<name>[^`\"']{2,60})[`\"']", re.IGNORECASE),
    re.compile(r"\b" + _PLUGIN_WORD + r"\s+(?P<name>[a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", re.IGNORECASE),
    re.compile(r"\b(?P<name>[a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+" + _PLUGIN_WORD + r"\b", re.IGNORECASE),
)


def plugin_name_in(text: str) -> str:
    body = " ".join(str(text or "").split())
    for pattern in _PLUGIN_NAME_PATTERNS:
        match = pattern.search(body)
        if match:
            name = _clean_name(match.group("name"))
            if name and name.lower() not in _NOT_NAMES:
                return name
    return ""


def _pack_skill_names(text: str) -> list[str]:
    """The skills a "make a plugin ... with A, B and C" request lists, in order."""
    body = " ".join(str(text or "").split())
    match = re.search(r"\b(?:with|containing|from|of|holding|including)\b\s+(?P<list>.+)$", body, re.IGNORECASE)
    if not match:
        return []
    raw = re.sub(r"[.?!]+$", "", match.group("list"))
    raw = re.sub(r"\b(?:my|the|our|skills?|skil|and\s+the|both)\b", " ", raw, flags=re.IGNORECASE)
    parts = [part.strip(" `\"'") for part in re.split(r",|\band\b|&|\+", raw, flags=re.IGNORECASE)]
    return [part for part in (" ".join(p.split()) for p in parts) if part and part.lower() not in _NOT_NAMES]


# Words that carry no purpose: "a skill that does something" has nothing to draft from.
_VAGUE = frozenset(
    {
        "something", "stuff", "things", "thing", "anything", "whatever", "this", "that", "these",
        "those", "does", "do", "doing", "it", "work", "works", "useful", "cool", "nice", "good",
    }
)


def _purpose_of(text: str) -> str:
    """What the requested skill should do, in the operator's words, without scope words."""
    from core.skill_scopes import strip_scope_words

    body = strip_scope_words(" ".join(str(text or "").split()))
    match = re.search(
        r"\b(?:that|which|to|so\s+(?:it|that)|for|it\s+should|should|will|can)\b\s+(?P<rest>.+)$",
        body,
        re.IGNORECASE,
    )
    rest = (match.group("rest") if match else "").strip(" .,:;")
    rest = re.sub(r"^\s*(?:it\s+)?(?:should|will|can|must)\s+", "", rest, flags=re.IGNORECASE)
    rest = re.sub(r"\b(?:call|name)\s+it\s+[`\"']?[\w-]+[`\"']?", " ", rest, flags=re.IGNORECASE)
    rest = re.sub(r"\b(?:go\s+ahead\s+and\s+)?(?:create|make|build|draft|write)\s+it\b.*$", " ", rest, flags=re.IGNORECASE)
    return " ".join(rest.split()).strip(" .,:;")


def _purpose_words(purpose: str) -> list[str]:
    from core.plugin_skills import _STOPWORDS

    return [
        token
        for token in re.split(r"[^a-z0-9]+", purpose.lower())
        if len(token) > 2 and token not in _STOPWORDS and token not in _VAGUE
    ]


def _first_noun(body: str) -> str:
    """Which noun the request is about first: "skill" or "plugin" ("" when neither)."""
    skill = re.search(r"\b" + _SKILL_WORD + r"\b", body, re.IGNORECASE)
    plugin = re.search(r"\b" + _PLUGIN_WORD + r"\b", body, re.IGNORECASE)
    if skill and (not plugin or skill.start() < plugin.start()):
        return "skill"
    return "plugin" if plugin else ""


def skill_request(text: str) -> dict[str, Any] | None:
    """``{"action": ..., "name": str, ...}``, or None when this is not one.

    ``scope`` is present only when the message names one, so a request that names no scope reads
    exactly as it always has.
    """
    request = _skill_request_raw(text)
    if request is not None and not request.get("scope"):
        request.pop("scope", None)
    return request


def _skill_request_raw(text: str) -> dict[str, Any] | None:
    """``{"action": ..., "name": str, ...}``, or None when this is not one.

    Actions: create, edit, validate, install, rollback, list, export, import, remove, pack,
    activate (this chat's pending draft), needs_name. ``scope`` is set when the message names one
    (``chat`` / ``project`` / ``global``) and is ``""`` otherwise.

    Deliberately answers None for a QUESTION about skills. The restraint here is the same one the
    build lane keeps: discussing a capability is not asking for it.
    """
    from core.skill_scopes import scope_in

    body = " ".join(str(text or "").split())
    if not body:
        return None
    lowered = f" {body.lower()} "
    names_skill = bool(re.search(r"\b" + _SKILL_WORD + r"\b", lowered))
    names_plugin = bool(re.search(r"\b" + _PLUGIN_WORD + r"\b", lowered))
    if not names_skill and not names_plugin:
        # "turn it on" right after a draft names neither noun; it is resolved against this chat's
        # pending draft by the handler, and only when one exists.
        if _ACTIVATE_PENDING_RE.search(body) and len(body) <= 80:
            return {"action": "activate", "name": "", "scope": ""}
        return None
    if build_request_intent.is_opted_out(lowered):
        return None

    scope = scope_in(body)
    name = skill_name_in(body)

    # A question ABOUT skills is answered by whoever answers questions, not by this lane. Checked
    # BEFORE listing: "what is a skill and how does it work?" matched the list pattern on "what ...
    # skill" and was answered with an inventory of the operator's skills, which is not the question.
    if _ABOUT_RE.search(body):
        return None

    # Listing is a READ, so it is decided before the deliberation gate rather than after. That gate
    # reads "what skills do i have" as discussion -- correctly, for a gate whose job is to stop
    # WRITES -- and letting it veto here answered a direct question with nothing.
    if _INVENTORY_RE.fullmatch(body.strip(" .?!")) and not _CREATE_RE.search(body):
        return {"action": "list", "name": "", "scope": scope}

    # Everything below this line mutates, so discussion stops here. "how would i create a skill?"
    # and "lets discuss adding a skill" are both left alone, by the same gate the build lane uses.
    if build_request_intent.is_deliberation(lowered):
        return None

    # A create sentence may DESCRIBE exporting, importing or removing ("a skill to delete old log
    # files", "a skill that exports invoices as a zip"). Whichever verb comes first is the request.
    create_match = _HEADING_RE.search(body) or _CREATE_RE.search(body)

    def _first(match: Any) -> bool:
        return match is not None and (create_match is None or match.start() < create_match.start())

    first_noun = _first_noun(body)
    if _first(_EXPORT_RE.search(body)):
        if first_noun == "plugin":
            return {"action": "export", "kind": "plugin", "name": plugin_name_in(body), "scope": scope}
        return {"action": "export", "kind": "skill", "name": name, "scope": scope}
    if _first(_IMPORT_RE.search(body)):
        return {"action": "import", "name": "", "scope": scope}
    if first_noun == "plugin" and _PACK_RE.search(body):
        return {
            "action": "pack",
            "name": plugin_name_in(body),
            "skills": _pack_skill_names(body),
            "scope": scope,
        }
    if _first(_REMOVE_RE.search(body)):
        return {"action": "remove", "name": name, "scope": scope}
    if names_plugin and not names_skill:
        if _ACTIVATE_PENDING_RE.search(body) or _INSTALL_RE.search(body):
            return {"action": "activate", "name": plugin_name_in(body), "kind": "plugin", "scope": ""}
        return None

    if _VALIDATE_RE.search(body) and name:
        return {"action": "validate", "name": name, "scope": scope}
    if _ROLLBACK_RE.search(body):
        version_match = _VERSION_RE.search(body)
        return {
            "action": "rollback",
            "name": name,
            "version": int(version_match.group("version") or version_match.group("version2"))
            if version_match
            else 0,
            "scope": scope,
        }
    if _EDIT_RE.search(body) and name:
        # An edit of an existing skill rides the authoring lane with the overwrite the request
        # implies; the version store preserves what is being replaced.
        if _skill_exists(name) or _skill_exists_in_any_scope(name):
            return {"action": "edit", "name": name, "scope": scope}
        return {"action": "needs_name", "name": "", "scope": scope}
    if _ACTIVATE_PENDING_RE.search(body) and not name:
        return {"action": "activate", "name": "", "scope": scope}
    if _INSTALL_RE.search(body) and name:
        return {"action": "install", "name": name, "scope": scope}
    if _HEADING_RE.search(body) or _CREATE_RE.search(body):
        if not name:
            # A purpose is enough to draft from: the name is derived from it and SHOWN, so the
            # operator can refer to the skill. With neither, there is nothing to write.
            if len(_purpose_words(_purpose_of(body))) >= 2:
                return {"action": "create", "name": "", "scope": scope}
            return {"action": "needs_name", "name": "", "scope": scope}
        return {"action": "create", "name": name, "scope": scope}
    return None


#: The actions whose whole message is ONE authoring request: every clause is the skill's spec.
SKILL_AUTHORING_ACTIONS = frozenset({"create", "edit", "pack", "import", "export", "remove"})


def is_skill_authoring_request(text: str) -> bool:
    """Whether this message asks VOOL to author, share or remove a skill (one request, not several).

    The turn planner, the mixed-demand splitter and the current-information classifier treat a
    software-authoring request as one artifact whose clauses are its spec; a skill request is the
    same shape, and each of them carved or re-classed it (measured live 2026-10-05).
    """
    try:
        return (skill_request(text) or {}).get("action") in SKILL_AUTHORING_ACTIONS
    except Exception:
        return False


def _skill_exists_in_any_scope(name: str) -> bool:
    """An edit may name a chat or project skill; the bare-name resolver only sees global ones.

    Answered from the request alone (no turn context here), so this checks whether ANY chat or
    project folder holds the slug. The handler resolves the real one against the turn's keys.
    """
    try:
        from core.runtime_paths import data_path
        from core.skill_scopes import ACTIVE_DIRNAME, USER_SKILLS_DIRNAME
        from core.skill_tools import _slug

        slug = _slug(name)
        root = data_path(USER_SKILLS_DIRNAME)
        return bool(slug) and any(root.glob(f"*/*/{ACTIVE_DIRNAME}/{slug}/SKILL.md"))
    except Exception:
        return False


def _describe(text: str, name: str) -> str:
    """A one-line description of when the skill applies, taken from the request itself.

    `create_skill` refuses an empty description on purpose -- the match corpus is name plus
    description, so a skill without one can never rank. This is the operator's own wording, not an
    invented one.
    """
    from core.skill_scopes import strip_scope_words

    body = strip_scope_words(" ".join(str(text or "").split()))
    tail = re.split(r"\b(?:that|which|to|for|it should|and it)\b", body, maxsplit=1)
    detail = tail[1].strip(" .,:;") if len(tail) > 1 else ""
    detail = re.sub(r"^\s*(?:it\s+)?(?:should|will|can)\s+", "", detail, flags=re.IGNORECASE).strip()
    if detail:
        return f"Use when the user asks to {detail}."
    return f"Use when the user asks for {name}."


# --------------------------------------------------------------------------------------------
# Drafting: the turn's own model writes the SKILL.md; the operator's words are the fallback.
# --------------------------------------------------------------------------------------------

_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "description", "triggers", "instructions"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "triggers": {"type": "array", "items": {"type": "string"}},
        "instructions": {"type": "string"},
    },
}

_DRAFT_SYSTEM_PROMPT = (
    "You write skills for VOOL, a personal AI assistant. A skill is a short instruction file that "
    "VOOL loads into its context whenever a request matches it, so it does that kind of task the "
    "way the user wants.\n"
    "Return ONLY one JSON object with exactly these keys:\n"
    '- "name": 1-4 lowercase words joined by hyphens that say what the skill does (e.g. "release-notes").\n'
    '- "description": one sentence that starts with "Use when the user" and says exactly when it applies.\n'
    '- "triggers": 4-10 short words or phrases a user would actually type when they need it '
    "(include singular and plural forms and common synonyms).\n"
    '- "instructions": Markdown instructions, written to the assistant in second person ("You ..."): '
    "the steps to follow, the exact output format, and what to do when the input is missing or "
    "unclear. Keep to what the user asked for. Do not invent tools, files, URLs, accounts, "
    "credentials or facts. At most 350 words.\n"
    "No preamble, no code fences, no commentary."
)

_DRAFT_TTL_SECONDS = 3600.0
_PENDING_TTL_SECONDS = 2 * 3600.0
_STATE_LOCK = threading.Lock()
# (chat key, request digest) -> the drafted fields, so a turn resumed after an approval prompt
# re-dispatches the SAME arguments the operator approved instead of asking the model again.
_DRAFTS: dict[tuple[str, str], dict[str, Any]] = {}
# chat key -> the draft (or staged import/plugin) "turn it on" activates in that chat.
_PENDING: dict[str, dict[str, Any]] = {}


def _request_digest(text: str, scope: str) -> str:
    return hashlib.sha256(f"{scope}\n{' '.join(str(text or '').split())}".encode()).hexdigest()


def _remember_pending(chat: str, entry: dict[str, Any]) -> None:
    if not chat:
        return
    with _STATE_LOCK:
        _PENDING[chat] = {**entry, "at": time.monotonic()}


def _pending_for(chat: str) -> dict[str, Any] | None:
    if not chat:
        return None
    with _STATE_LOCK:
        entry = _PENDING.get(chat)
        if entry and time.monotonic() - float(entry.get("at") or 0.0) > _PENDING_TTL_SECONDS:
            _PENDING.pop(chat, None)
            entry = None
    return dict(entry) if entry else None


def _clear_pending(chat: str) -> None:
    with _STATE_LOCK:
        _PENDING.pop(chat, None)


def _derived_name(purpose: str) -> str:
    """A short hyphenated name from the purpose's first content words ("summarise-pasted-articles")."""
    words = _purpose_words(purpose)[:3]
    return "-".join(words)


def _parse_draft(raw: str) -> dict[str, Any] | None:
    """The model's JSON, checked field by field. None when any field is unusable."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        text = match.group(0) if match else ""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    from core.skill_tools import _slug

    name = _slug(str(data.get("name") or ""))[:60].strip("-")
    description = " ".join(str(data.get("description") or "").split())[:400]
    instructions = str(data.get("instructions") or "").strip()[:7000]
    triggers_raw = data.get("triggers")
    triggers = [
        " ".join(str(item).split())[:40]
        for item in (triggers_raw if isinstance(triggers_raw, list) else [])
        if str(item).strip()
    ][:12]
    if not name or not description or len(instructions) < 40:
        return None
    return {"name": name, "description": description, "triggers": triggers, "instructions": instructions}


def _draft_with_model(
    agent: Any,
    source_context: dict[str, Any] | None,
    *,
    request_text: str,
    scope_label: str,
    name: str,
    current_skill: str = "",
) -> tuple[dict[str, Any] | None, str]:
    """(draft, author label) from the turn's model, or (None, why) when no model wrote one."""
    if getattr(agent, "memory_router", None) is None:
        return None, "no model router on this turn"
    try:
        from core.agent_runtime.turn_planner_hook import build_conductor_ask_model, build_pinned_paid_turn_scope

        paid_scope = build_pinned_paid_turn_scope(agent, source_context)
        ask = build_conductor_ask_model(
            agent,
            source_context,
            paid_scope=paid_scope,
            reasoning_mode="disabled",
            default_json_schema=_DRAFT_SCHEMA,
        )
    except Exception as exc:
        return None, f"no drafting model ({type(exc).__name__})"
    lines = [f"The user asked for this skill: {request_text.strip()}", f"It is for {scope_label}."]
    if name:
        lines.append(f'Use exactly this name: "{name}".')
    if current_skill:
        lines.append(
            "This is the skill as it is now. Revise it as the user asked and return the whole "
            "revised skill:\n" + current_skill[:6000]
        )
    try:
        raw = ask(_DRAFT_SYSTEM_PROMPT, "\n".join(lines))
    except Exception as exc:
        return None, f"the drafting model failed ({type(exc).__name__})"
    draft = _parse_draft(raw)
    if draft is None:
        return None, "the drafting model returned no usable skill" if str(raw or "").strip() else "no drafting model answered"
    if name:
        from core.skill_tools import _slug

        draft["name"] = _slug(name) or draft["name"]
    author = ""
    log = list((source_context or {}).get("conductor_generation_authorship") or []) if isinstance(source_context, dict) else []
    for row in reversed(log):
        if isinstance(row, dict) and row.get("eligible", True) is not False and row.get("provider_id"):
            author = str(row.get("provider_id"))
            break
    return draft, author or "the turn's model"


def _draft_from_words(request_text: str, name: str) -> dict[str, Any] | None:
    """A skill made of the operator's own words: what they asked for, verbatim, as the instruction."""
    from core.skill_scopes import strip_scope_words
    from core.skill_tools import _slug

    purpose = _purpose_of(request_text)
    final_name = _slug(name) or _slug(_derived_name(purpose))
    if not final_name:
        return None
    detail = purpose or strip_scope_words(request_text)
    triggers = list(dict.fromkeys(_purpose_words(purpose)))[:8]
    body = (
        f"# {final_name}\n\n"
        "The user made this skill and described what it should do in these words:\n\n"
        f"> {strip_scope_words(request_text).strip()}\n\n"
        f"When a request matches, do exactly that: {detail}."
    )
    return {
        "name": final_name,
        "description": _describe(request_text, final_name),
        "triggers": triggers,
        "instructions": body,
    }


def _chat_key(session_id: str, source_context: dict[str, Any] | None) -> str:
    context = dict(source_context or {})
    return str(session_id or context.get("session_id") or context.get("runtime_session_id") or "").strip()


def _scope_label(scope: str) -> str:
    from core.skill_scopes import SCOPE_LABELS

    return SCOPE_LABELS.get(scope or "global", "all of VOOL")


def _preview(draft: dict[str, Any], *, limit: int = 1400) -> str:
    instructions = str(draft.get("instructions") or "").strip()
    if len(instructions) > limit:
        instructions = instructions[:limit].rstrip() + "\n…"
    triggers = ", ".join(str(t) for t in draft.get("triggers") or [])
    lines = [f"**When it applies:** {draft.get('description')}"]
    if triggers:
        lines.append(f"**Trigger words:** {triggers}")
    lines.append("**What it tells me to do:**")
    lines.append(instructions)
    return "\n".join(lines)


def _zip_attachments(session_id: str, source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The zip files attached to THIS turn of THIS chat (names and ids only, never contents)."""
    context = dict(source_context or {})
    session = str(context.get("runtime_session_id") or context.get("session_id") or session_id or "").strip()
    turn = str(context.get("attachment_turn_id") or "").strip()
    if not session or not turn:
        return []
    try:
        from core.chat_attachments import receipt_for_turn

        rows = receipt_for_turn(session_id=session, turn_id=turn)
    except Exception:
        return []
    return [
        row for row in rows
        if str(row.get("name") or "").lower().endswith(".zip")
        or "zip" in str(row.get("media_type") or "").lower()
    ]


_PATH_RE = re.compile(r"(?P<path>(?:~|/)[^\s`\"']+\.zip)\b", re.IGNORECASE)


def maybe_handle_skill_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Run the skill tool this message asks for. None when it asks for none."""
    request = skill_request(user_input)
    chat = _chat_key(session_id, source_context)
    if request is None:
        # A pending draft is activated by the NEXT thing said about it; once the conversation has
        # moved on, a later "turn it on" must not reach back to it.
        _clear_pending(chat)
        return None

    from core.authorized_tool_execution import (
        REFUSAL_STATUSES,
        execute_authorized_runtime_tool,
    )

    action = str(request.get("action") or "")
    name = str(request.get("name") or "")
    scope = str(request.get("scope") or "")

    def plain(body: str, *, reason: str, execution: Any = None) -> dict[str, Any]:
        details_in = dict(getattr(execution, "details", {}) or {}) if execution is not None else {}
        pending_approval = dict(details_in.get("approval_request") or {})
        extra: dict[str, Any] = {}
        if pending_approval:
            # The chat page paints its approval bar from this event, and ignores events that reach
            # a run after its `task_completed` -- which `_fast_path_result` emits. So it goes out
            # FIRST, exactly as the machine lane does (fast_paths_machine.py, measured 2026-09-06:
            # emitted after, the approval never painted and a Manual-mode request read as a refusal).
            emit = getattr(agent, "_emit_runtime_event", None)
            if callable(emit):
                emit(
                    source_context,
                    event_type="task_pending_approval",
                    message=str(getattr(execution, "response_text", "") or "").strip(),
                    tool_name=str(pending_approval.get("intent") or details_in.get("intent") or ""),
                    approval_request=pending_approval,
                )
            extra["checkpoint_status"] = "pending_approval"
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=body,
            confidence=0.9,
            source_context=source_context,
            reason=reason,
            **extra,
        )
        if execution is not None:
            # A refusal from the authorized execution boundary carries the typed decision (and
            # the approval request when the mode wants one); put it on the envelope so the turn
            # says WHICH authority decided, the same way the model route does.
            details = dict(getattr(execution, "details", {}) or {})
            result["details"] = details
            status = str(getattr(execution, "status", "") or "")
            if status in REFUSAL_STATUSES:
                result["status"] = status
                result["success"] = False
            approval_request = dict(details.get("approval_request") or {})
            if approval_request:
                result["approval_request"] = approval_request
                result["task_outcome"] = "pending_approval"
        return result

    def run(intent: str, arguments: dict[str, Any]) -> Any:
        return execute_authorized_runtime_tool(intent, arguments, source_context=source_context)

    def refused(execution: Any) -> bool:
        return str(getattr(execution, "status", "") or "") in REFUSAL_STATUSES

    if action == "activate":
        pending = _pending_for(chat) or _pending_from_history(source_context)
        if pending is None and not name:
            # Nothing drafted in this chat to turn on: this is not a skill turn after all.
            return None
        if pending is not None and not name:
            _remember_pending(chat, pending)
            if pending.get("kind") != "plugin" and _staged_exists(pending, source_context, session_id) and not _staged_matches_shown(
                pending, source_context, session_id
            ):
                return plain(
                    f"The saved draft `{pending.get('slug')}` is not the one I showed you here -- it was "
                    "replaced since -- so I did not turn it on. Ask me to show it, or create it again.",
                    reason="skill_activate_draft_changed",
                )
            if pending.get("kind") != "plugin" and not _staged_exists(pending, source_context, session_id):
                # The offer is in the conversation but its draft never landed (its save was not
                # approved, or it was refused). Say so here: left to a model, this turn was
                # answered "the skill is now turned on" with nothing saved (measured live).
                return plain(
                    f"The draft `{pending.get('slug')}` was never saved -- its save was not approved -- so "
                    "there is nothing to turn on yet. Ask me to create it again and approve the save.",
                    reason="skill_activate_nothing_saved",
                )

    if action == "needs_name":
        return plain(
            "I can make that skill, but I need to know what it should do -- and, if you like, a name "
            "for it, which is how you turn it on, export or change it afterwards. For example: "
            "`create a skill for this chat that turns pasted meeting notes into action items`, or "
            "`create a skill called release-notes that summarises CHANGELOG.md by version`.",
            reason="skill_needs_name",
        )

    if action == "list":
        # The workspace is the operator's own project -- for "what skills do we have", the files in
        # THAT folder are most of the answer, so it rides along for the scan.
        result = run("skill.list", {"workspace": str((source_context or {}).get("workspace") or "")})
        if result is None:
            return plain(
                "I can draft, validate and install skills, but I have no tool for listing them on "
                "this build. Name one and I can validate it.",
                reason="skill_list_unsupported",
            )
        return plain(
            str(getattr(result, "response_text", "") or ""), reason="skill_list", execution=result
        )

    # Authorization for the actions that touch the filesystem. `list` and `validate` are reads
    # and stay available on purpose: a turn that rules out taking an action still deserves an answer
    # about what already exists, and withdrawing the read as well would be a denial applied wider
    # than the sentence that justified it.
    #
    # `skill.create` stages under `~/Desktop/Vool-skills-plugins/staged-skills`; `skill.install`
    # copies into the ACTIVATED plugins tree, which changes what the runtime will load -- the very
    # separation `core/skill_tools.py:28-30` exists to keep ("create is not an authorisation to
    # change behaviour"). Measured 2026-08-17: BOTH ran to completion with `action_policy=FORBIDDEN`,
    # because this lane is called at `turn_frontdoor.py:1284`, twenty-one lines above the
    # `if action_forbidden:` mute that its four mutation-capable siblings all sit behind
    # (`:1359` web0_builder, `:1504` machine_download, `:1516` machine_write, `:1539` image).
    # The call site cannot move -- `c147c027` put it there so a skill request is not swallowed by the
    # app builder -- so the check comes to the lane instead, and binds to the OPERATION rather than
    # to the turn.
    if action in {"create", "install", "edit", "rollback", "export", "import", "remove", "pack", "activate"}:
        from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context

        if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
            return plain(
                f"This turn rules out taking an action, so I did not run `skill.{action}` -- it "
                f"writes to disk. I can still list or validate skills, or do this once the "
                f"restriction is lifted.",
                reason=f"skill_{action}_action_forbidden",
            )

    if action == "rollback":
        if not name:
            return plain(
                "I can roll a skill back to a version recorded in its history -- tell me which "
                "skill and which version, e.g. `roll back the qa-echo skill to version 1`.",
                reason="skill_rollback_needs_name",
            )
        from core.skill_tools import skill_history

        keys = _turn_keys(source_context, session_id)
        history = skill_history(name, keys)
        if history.get("status") != "ok":
            return plain(
                str(history.get("reason") or f"I have no recorded versions for {name}."),
                reason="skill_rollback_no_history",
            )
        if not request.get("version"):
            recorded = ", ".join(
                str(entry.get("version")) for entry in history.get("versions") or []
            )
            return plain(
                f"`{history.get('name')}` has these recorded versions: {recorded}. "
                f"Which one should I restore?",
                reason="skill_rollback_needs_version",
            )
        result = run("skill.rollback", {"skill": name, "version": int(request.get("version") or 0)})
        if result is None:
            return plain(
                "This build has no `skill.rollback` tool, so I did not pretend to run it.",
                reason="skill_rollback_unsupported",
            )
        return plain(
            str(getattr(result, "response_text", "") or ""),
            reason="skill_rollback",
            execution=result,
        )

    if action in {"create", "edit"}:
        return _create_or_edit(
            agent, user_input, request, session_id=session_id, source_context=source_context,
            chat=chat, plain=plain, run=run, refused=refused,
        )

    if action == "validate":
        result = run("skill.validate", {"path": name})
        if result is None:
            return plain(f"This build has no `skill.{action}` tool, so I did not pretend to run it.", reason=f"skill_{action}_unsupported")
        return plain(str(getattr(result, "response_text", "") or ""), reason="skill_validate", execution=result)

    if action == "activate":
        pending = _pending_for(chat) or {}
        kind = str(request.get("kind") or pending.get("kind") or "skill")
        if kind == "plugin":
            target = name or str(pending.get("name") or "")
            arguments = {"kind": "plugin", "path": target, "overwrite": True}
            if pending.get("kind") == "plugin" and (not name or name == pending.get("name")):
                # Bound to the pack the operator was shown.
                arguments["sha256"] = str(pending.get("sha256") or "")
            result = run("skill.install", arguments)
        else:
            arguments = {
                "path": str(pending.get("slug") or name),
                "scope": str(pending.get("scope") or "global"),
                "sha256": str(pending.get("sha256") or ""),
                "overwrite": True,
            }
            result = run("skill.install", arguments)
        if result is None:
            return plain("This build has no `skill.install` tool, so I did not pretend to run it.", reason="skill_install_unsupported")
        if str(getattr(result, "status", "") or "") == "ok":
            _clear_pending(chat)
        return plain(str(getattr(result, "response_text", "") or ""), reason="skill_activate", execution=result)

    if action == "install":
        # Installing over an existing activation is the EDIT flow's second half: a staged draft
        # of the same skill is what the operator just revised, so the overwrite the request
        # implies is passed through (the prior version is preserved by the version store).
        from core.skill_tools import resolve_skill_markdown, staging_root

        keys = _turn_keys(source_context, session_id)
        resolved = resolve_skill_markdown(name, keys)
        draft_scope = _scope_of_path(resolved, keys)
        staged_draft = resolved.is_file() and (
            staging_root() in resolved.resolve().parents or draft_scope in ("chat", "project")
        )
        arguments: dict[str, Any] = {"path": name, "overwrite": bool(staged_draft)}
        target_scope = scope or draft_scope
        if target_scope and target_scope != "global":
            arguments["scope"] = target_scope
        result = run("skill.install", arguments)
        if result is None:
            return plain(f"This build has no `skill.{action}` tool, so I did not pretend to run it.", reason=f"skill_{action}_unsupported")
        if str(getattr(result, "status", "") or "") == "ok":
            _clear_pending(chat)
        return plain(str(getattr(result, "response_text", "") or ""), reason=f"skill_{action}", execution=result)

    if action == "export":
        kind = str(request.get("kind") or "skill")
        target = name or str((_pending_for(chat) or {}).get("slug") or "")
        if not target:
            return plain(
                f"Which {kind} should I export? For example: `export the release-notes {kind}`.",
                reason="skill_export_needs_name",
            )
        arguments = {"name": target, "kind": kind}
        if scope:
            arguments["scope"] = scope
        result = run("skill.export", arguments)
        if result is None:
            return plain("This build has no `skill.export` tool, so I did not pretend to run it.", reason="skill_export_unsupported")
        text = str(getattr(result, "response_text", "") or "")
        if str(getattr(result, "status", "") or "") == "ok":
            text += (
                "\n\nTo use it elsewhere, attach that zip in a VOOL chat and say "
                + (
                    "`import this plugin` (a plugin is for all of VOOL)."
                    if kind == "plugin"
                    else "`import this skill for this chat` (or `for this project` / `for all of VOOL`)."
                )
            )
        return plain(text, reason="skill_export", execution=result)

    if action == "import":
        from core import skill_package

        zips = _zip_attachments(session_id, source_context)
        holder = _attachment_session(session_id, source_context)
        for row in zips:
            # Held now, while the bytes exist: if this import waits for an approval, the approved
            # turn arrives without the attachment.
            _hold_attachment(holder, row)
        path_match = _PATH_RE.search(user_input or "")
        sources: list[dict[str, Any]] = [{"attachment_id": str(row.get("id") or ""), "label": str(row.get("name") or "")} for row in zips]
        if not sources and not path_match and holder:
            sources = [
                {"attachment_id": attachment_id, "label": label}
                for attachment_id, label in skill_package.held_for_session(holder)
            ]
        if not sources and path_match:
            sources = [{"path": path_match.group("path"), "label": path_match.group("path")}]
        if not sources:
            return plain(
                "Attach the skill zip to your message (or give its full path) and say `import this "
                "skill` -- add `for this chat`, `for this project` or `for all of VOOL` to choose "
                "where it goes.",
                reason="skill_import_needs_file",
            )
        replies: list[str] = []
        last: Any = None
        for source in sources[:4]:
            label = source.get("label") or "the zip"
            # Checked BEFORE asking to write: a package VOOL will refuse anyway (runnable code, an
            # unsafe archive, a tampered digest) must not first cost the operator an approval.
            precheck = _precheck_package(holder, source)
            if precheck:
                replies.append(f"I did not import `{label}`: {precheck}")
                if source.get("attachment_id"):
                    _record_import_receipt(session_id, source_context, source, "refused")
                    skill_package.release_package(holder, str(source["attachment_id"]))
                continue
            arguments = {key: value for key, value in source.items() if key != "label"}
            arguments["scope"] = scope or "global"
            result = run("skill.import", arguments)
            last = result
            if result is None:
                return plain("This build has no `skill.import` tool, so I did not pretend to run it.", reason="skill_import_unsupported")
            if refused(result):
                return plain(str(getattr(result, "response_text", "") or ""), reason="skill_import", execution=result)
            details = dict(getattr(result, "details", {}) or {})
            status = str(getattr(result, "status", "") or "")
            label = source.get("label") or "the zip"
            if status in ("ok", "invalid", "refused"):
                _record_import_receipt(session_id, source_context, source, status)
                if source.get("attachment_id"):
                    skill_package.release_package(holder, str(source["attachment_id"]))
            if status == "ok":
                if details.get("kind") == "plugin":
                    _remember_pending(chat, {"kind": "plugin", "name": details.get("name"), "sha256": details.get("sha256")})
                    replies.append(
                        f"`{label}` is plugin `{details.get('name')}` with "
                        + ", ".join(f"`{s}`" for s in details.get("skills") or [])
                        + ". It is staged, not active. Say **turn it on** to activate it for all of VOOL."
                    )
                else:
                    _remember_pending(
                        chat,
                        {"kind": "skill", "slug": details.get("slug"), "scope": details.get("scope"), "sha256": details.get("sha256")},
                    )
                    preview = _preview(
                        {"description": details.get("description"), "instructions": _body_of(str(details.get("preview_markdown") or ""))}
                    )
                    replies.append(
                        f"`{label}` holds the skill `{details.get('name')}`, staged for "
                        f"{_scope_label(str(details.get('scope') or 'global'))}.\n\n{preview}\n\n"
                        "It is not on yet. Say **turn it on** to start using it."
                    )
            else:
                problems = "; ".join(str(p) for p in details.get("problems") or []) or str(getattr(result, "response_text", "") or "")
                replies.append(f"I did not import `{label}`: {problems}")
        return plain("\n\n".join(replies), reason="skill_import", execution=last)

    if action == "remove":
        if not name:
            return plain(
                "Which skill should I turn off? For example: `remove the release-notes skill from this chat`.",
                reason="skill_remove_needs_name",
            )
        arguments = {"name": name}
        if scope:
            arguments["scope"] = scope
        result = run("skill.remove", arguments)
        if result is None:
            return plain("This build has no `skill.remove` tool, so I did not pretend to run it.", reason="skill_remove_unsupported")
        return plain(str(getattr(result, "response_text", "") or ""), reason="skill_remove", execution=result)

    if action == "pack":
        skills = list(request.get("skills") or [])
        if not name or not skills:
            return plain(
                "I can bundle skills you made into a plugin. Tell me its name and which skills go in, "
                "e.g. `create a plugin called research-kit with the tldr and log-triage skills`.",
                reason="skill_pack_needs_details",
            )
        result = run(
            "skill.pack",
            {"name": name, "description": _purpose_of(user_input) or f"Skills bundled as {name}.", "skills": skills},
        )
        if result is None:
            return plain("This build has no `skill.pack` tool, so I did not pretend to run it.", reason="skill_pack_unsupported")
        details = dict(getattr(result, "details", {}) or {})
        text = str(getattr(result, "response_text", "") or "")
        if str(getattr(result, "status", "") or "") == "ok":
            _remember_pending(chat, {"kind": "plugin", "name": details.get("name"), "sha256": details.get("sha256")})
            text += " Say **turn it on** to activate it for all of VOOL, or `export the " + str(details.get("name")) + " plugin` after that to share it."
        return plain(text, reason="skill_pack", execution=result)

    return None


def _precheck_package(holder: str, source: dict[str, Any]) -> str:
    """The refusal reason for a package VOOL would refuse anyway, or "" (read-only)."""
    from core import skill_package

    raw: bytes | None = None
    if source.get("attachment_id"):
        held = skill_package.held_package(holder, str(source["attachment_id"]))
        raw = held[0] if held else None
    elif source.get("path"):
        try:
            from pathlib import Path

            path = Path(str(source["path"])).expanduser()
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= skill_package.MAX_PACKAGE_BYTES:
                raw = path.read_bytes()
        except OSError:
            raw = None
    if raw is None:
        return ""  # nothing to check here; the tool decides
    try:
        skill_package.read_package(raw)
    except skill_package.PackageRefused as refused:
        return refused.message
    return ""


_OFFER_RE = re.compile(
    r"(?:drafted|Revised) the skill `(?P<slug>[a-z0-9][a-z0-9-]*)` for (?P<scope>this chat|this project|all of VOOL)"
    r"|holds the skill `(?P<islug>[a-z0-9][a-z0-9-]*)`, staged for (?P<iscope>this chat|this project|all of VOOL)"
    r"|plugin `(?P<plugin>[a-z0-9][a-z0-9-]*)`[^\n]*(?:staged|Packed)",
)
_LABEL_SCOPES = {"this chat": "chat", "this project": "project", "all of VOOL": "global"}


def _pending_from_history(source_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """The draft the LAST assistant message offered to turn on, when the in-process record is gone.

    The record lives in memory, so a restart (or a turn served by another worker) loses it while the
    offer is still on the operator's screen. The offer's own words name the skill and its scope.
    Nothing is bound to a digest here: the operator approves the install itself.
    """
    context = dict(source_context or {})
    for key in ("conversation_history", "client_conversation_history"):
        history = context.get(key)
        if not isinstance(history, list):
            continue
        assistant = [
            str(message.get("content") or "")
            for message in history
            if isinstance(message, dict) and str(message.get("role") or "") == "assistant"
        ]
        # The offer may sit a few replies back: a model reply in between (measured live: one that
        # wrongly claimed the skill was already on) must not hide it. Four replies is the reach.
        for offset, text in enumerate(reversed(assistant[-4:])):
            match = _OFFER_RE.search(text) if "turn it on" in text else None
            if not match:
                continue
            later = list(reversed(assistant[-4:]))[:offset]
            if match.group("plugin"):
                name = match.group("plugin")
                if any(f"Plugin `{name}` is active" in reply for reply in later):
                    return None
                return {"kind": "plugin", "name": name}
            slug = match.group("slug") or match.group("islug")
            if any(f"Installed `{slug}`" in reply for reply in later):
                return None  # already turned on after that offer
            scope = _LABEL_SCOPES.get(match.group("scope") or match.group("iscope") or "", "global")
            shown = re.search(r"\*\*When it applies:\*\* (?P<d>[^\n]+)", text)
            return {
                "kind": "skill", "slug": slug, "scope": scope, "sha256": "",
                "shown_description": shown.group("d").strip() if shown else "",
            }
        return None
    return None


def _staged_matches_shown(pending: dict[str, Any], source_context: dict[str, Any] | None, session_id: str) -> bool:
    """Without a digest (the in-process record is gone), the draft on disk must at least be the one
    whose description the offer showed: another chat of the same project may have saved a different
    draft under the same name since (measured live)."""
    shown = str(pending.get("shown_description") or "").strip()
    if not shown or pending.get("sha256"):
        return True
    from core import skill_tools
    from core.plugin_skills import parse_skill

    slug = str(pending.get("slug") or "")
    scope = str(pending.get("scope") or "global")
    if scope == "global":
        path = skill_tools.staging_root() / slug / "SKILL.md"
    else:
        dirs = skill_tools._scoped_dirs(scope, _turn_keys(source_context, session_id))
        if not dirs:
            return False
        path = dirs[0] / slug / "SKILL.md"
    skill = parse_skill(path)
    return skill is not None and " ".join(skill.description.split()) == " ".join(shown.split())


def _staged_exists(pending: dict[str, Any], source_context: dict[str, Any] | None, session_id: str) -> bool:
    from core import skill_tools

    slug = str(pending.get("slug") or "")
    scope = str(pending.get("scope") or "global")
    if scope == "global":
        return (skill_tools.staging_root() / slug / "SKILL.md").is_file()
    dirs = skill_tools._scoped_dirs(scope, _turn_keys(source_context, session_id))
    return bool(dirs) and (dirs[0] / slug / "SKILL.md").is_file()


def _attachment_session(session_id: str, source_context: dict[str, Any] | None) -> str:
    context = dict(source_context or {})
    return str(context.get("runtime_session_id") or context.get("session_id") or session_id or "").strip()


def _hold_attachment(holder: str, row: dict[str, Any]) -> None:
    attachment_id = str(row.get("id") or "")
    if not holder or not attachment_id:
        return
    try:
        from core.chat_attachments import read_staged_bytes
        from core.skill_package import hold_package

        found = read_staged_bytes(session_id=holder, attachment_id=attachment_id)
        if found is not None:
            hold_package(holder, attachment_id, str(row.get("name") or ""), found[1])
    except Exception:
        return


def _record_import_receipt(session_id: str, source_context: dict[str, Any] | None, source: dict[str, Any], status: str) -> None:
    """Tell the attachment ledger this zip was read by the skill importer."""
    attachment_id = str(source.get("attachment_id") or "")
    context = dict(source_context or {})
    turn = str(context.get("attachment_turn_id") or "").strip()
    session = str(context.get("runtime_session_id") or context.get("session_id") or session_id or "").strip()
    if not attachment_id or not turn or not session:
        return
    try:
        from core.chat_attachments import record_delivery

        record_delivery(
            session_id=session,
            turn_id=turn,
            receipts=[{
                "attachment_id": attachment_id,
                "name": str(source.get("label") or ""),
                "kind": "artifact",
                "outcome": "read",
                "reason": f"skill_import_{status}",
            }],
        )
    except Exception:
        pass


def _body_of(markdown: str) -> str:
    from core.plugin_skills import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(str(markdown or ""))
    return (match.group(2) if match else str(markdown or "")).strip()


def _turn_keys(source_context: dict[str, Any] | None, session_id: str) -> dict[str, str] | None:
    """The chat/project keys the TOOLS will use for this turn (they read the turn context).

    The context's own chat identity is taken first, exactly as ``skill.*`` dispatch takes it, so the
    lane and the tools never name two different folders for one chat; the lane's ``session_id`` is
    only a fallback for a context that carries no chat at all.
    """
    try:
        from core.skill_scopes import scope_keys

        keys = scope_keys(source_context)
        if not keys.get("chat") and str(session_id or "").strip():
            keys = scope_keys(source_context, chat_id=str(session_id))
        return keys
    except Exception:
        return None


def _scope_of_path(path: Any, keys: dict[str, str] | None) -> str:
    """Which scope's folder a resolved SKILL.md sits in ("chat" / "project" / "global")."""
    from core.skill_scopes import scope_root

    try:
        resolved = path.resolve()
    except Exception:
        return "global"
    for scope in ("chat", "project"):
        root = scope_root(scope, keys)
        if root is not None and root.resolve() in resolved.parents:
            return scope
    return "global"


def _create_or_edit(
    agent: Any,
    user_input: str,
    request: dict[str, Any],
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    chat: str,
    plain: Any,
    run: Any,
    refused: Any,
) -> dict[str, Any]:
    action = str(request.get("action") or "")
    name = str(request.get("name") or "")
    scope = str(request.get("scope") or "")
    current = ""
    keys = _turn_keys(source_context, session_id)
    if action == "edit":
        from core.skill_tools import resolve_skill_markdown

        resolved = resolve_skill_markdown(name, keys)
        if resolved.is_file():
            scope = scope or _scope_of_path(resolved, keys)
            try:
                current = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                current = ""
    scope = scope or "global"
    if scope in ("chat", "project"):
        from core.skill_scopes import scope_unavailable_reason

        reason = scope_unavailable_reason(scope, keys)
        if reason:
            return plain(f"I can't keep a skill for {_scope_label(scope)}: {reason}.", reason="skill_scope_unavailable")

    digest_key = (chat, _request_digest(user_input, scope))
    with _STATE_LOCK:
        cached = _DRAFTS.get(digest_key)
        if cached and time.monotonic() - float(cached.get("at") or 0.0) > _DRAFT_TTL_SECONDS:
            _DRAFTS.pop(digest_key, None)
            cached = None
    if cached:
        draft, author = dict(cached["draft"]), str(cached["author"])
    else:
        draft, author = _draft_with_model(
            agent, source_context, request_text=user_input, scope_label=_scope_label(scope),
            name=name, current_skill=current,
        )
        if draft is None:
            words = _draft_from_words(user_input, name)
            if words is None:
                return plain(
                    "I couldn't work out a name or purpose for that skill. Tell me what it should do.",
                    reason="skill_needs_name",
                )
            draft, author = words, f"from your own words: {author}"
        with _STATE_LOCK:
            _DRAFTS[digest_key] = {"draft": dict(draft), "author": author, "at": time.monotonic()}

    arguments: dict[str, Any] = {
        "name": draft["name"],
        "description": draft["description"],
        "body": draft["instructions"],
    }
    if draft.get("triggers"):
        arguments["triggers"] = list(draft["triggers"])
    if scope != "global":
        arguments["scope"] = scope
    # A draft replaces an earlier draft of the same name: drafts are not active, and the arguments
    # must be identical when this same request is resumed after an approval prompt, or the
    # approval would not cover them.
    arguments["overwrite"] = True
    result = run("skill.create", arguments)
    if result is None:
        return plain(f"This build has no `skill.{action}` tool, so I did not pretend to run it.", reason=f"skill_{action}_unsupported")
    status = str(getattr(result, "status", "") or "")
    if status == "pending_approval":
        # The write waits for the operator; show them what they are approving.
        return plain(
            f"I drafted the skill `{draft['name']}` for {_scope_label(scope)} ({_written_by(author)}).\n\n"
            f"{_preview(draft)}\n\nApprove saving the draft and I'll keep it; it still won't be on "
            "until you say **turn it on**.",
            reason=f"skill_{action}",
            execution=result,
        )
    if refused(result) or status != "ok":
        return plain(str(getattr(result, "response_text", "") or ""), reason=f"skill_{action}", execution=result)
    details = dict(getattr(result, "details", {}) or {})
    _remember_pending(
        chat,
        {"kind": "skill", "slug": details.get("slug") or draft["name"], "scope": scope, "sha256": details.get("sha256")},
    )
    replaces = ""
    if _active_exists(draft["name"], scope, keys):
        replaces = f" It will replace the active `{draft['name']}` for {_scope_label(scope)}; that version is kept in its history."
    verb = "Revised" if action == "edit" else "Drafted"
    body = (
        f"{verb} the skill `{draft['name']}` for {_scope_label(scope)} ({_written_by(author)}).\n\n"
        f"{_preview(draft)}\n\n"
        f"It is not on yet.{replaces} Say **turn it on** to start using it, or tell me what to change."
    )
    return plain(body, reason=f"skill_{action}", execution=result)


def _written_by(author: str) -> str:
    """Who wrote the draft: "written by <model>", or "written from your own words: <why>"."""
    return f"written {author}" if author.startswith("from ") else f"written by {author}"


def _active_exists(slug: str, scope: str, keys: dict[str, str] | None) -> bool:
    from core import skill_tools

    if scope in ("chat", "project"):
        dirs = skill_tools._scoped_dirs(scope, keys)
        return bool(dirs) and (dirs[1] / slug / "SKILL.md").is_file()
    try:
        return any((skill_tools.plugins_root() / "plugins").glob(f"*/skills/{slug}/SKILL.md"))
    except OSError:
        return False


__all__ = [
    "SKILL_AUTHORING_ACTIONS",
    "is_skill_authoring_request",
    "maybe_handle_skill_request",
    "plugin_name_in",
    "skill_name_in",
    "skill_request",
]
