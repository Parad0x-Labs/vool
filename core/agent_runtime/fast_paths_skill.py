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
"""
from __future__ import annotations

import re
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
            if name and name.lower() not in {"a", "an", "the", "it", "this", "that", "new", "my"}:
                return name
    return ""


def skill_request(text: str) -> dict[str, Any] | None:
    """``{"action": create|edit|validate|install|rollback|list, "name": str}``, or None when this
    is not one.

    Deliberately answers None for a QUESTION about skills. The restraint here is the same one the
    build lane keeps: discussing a capability is not asking for it.
    """
    body = " ".join(str(text or "").split())
    if not body:
        return None
    lowered = f" {body.lower()} "
    if not re.search(r"\b" + _SKILL_WORD + r"\b", lowered):
        return None
    if build_request_intent.is_opted_out(lowered):
        return None

    name = skill_name_in(body)

    # A question ABOUT skills is answered by whoever answers questions, not by this lane. Checked
    # BEFORE listing: "what is a skill and how does it work?" matched the list pattern on "what ...
    # skill" and was answered with an inventory of the operator's skills, which is not the question.
    if _ABOUT_RE.search(body):
        return None

    # Listing is a READ, so it is decided before the deliberation gate rather than after. That gate
    # reads "what skills do i have" as discussion -- correctly, for a gate whose job is to stop
    # WRITES -- and letting it veto here answered a direct question with nothing.
    if _LIST_RE.search(body) and not _CREATE_RE.search(body):
        return {"action": "list", "name": ""}

    # Everything below this line mutates, so discussion stops here. "how would i create a skill?"
    # and "lets discuss adding a skill" are both left alone, by the same gate the build lane uses.
    if build_request_intent.is_deliberation(lowered):
        return None

    if _VALIDATE_RE.search(body) and name:
        return {"action": "validate", "name": name}
    if _ROLLBACK_RE.search(body):
        version_match = _VERSION_RE.search(body)
        return {
            "action": "rollback",
            "name": name,
            "version": int(version_match.group("version") or version_match.group("version2"))
            if version_match
            else 0,
        }
    if _EDIT_RE.search(body) and name:
        # An edit of an existing skill rides the authoring lane with the overwrite the request
        # implies; the version store preserves what is being replaced.
        if _skill_exists(name):
            return {"action": "edit", "name": name}
        return {"action": "needs_name", "name": ""}
    if _INSTALL_RE.search(body) and name:
        return {"action": "install", "name": name}
    if _HEADING_RE.search(body) or _CREATE_RE.search(body):
        # No name, no skill. A drafted file the operator cannot refer to is worse than a question.
        if not name:
            return {"action": "needs_name", "name": ""}
        return {"action": "create", "name": name}
    return None


def _describe(text: str, name: str) -> str:
    """A one-line description of when the skill applies, taken from the request itself.

    `create_skill` refuses an empty description on purpose -- the match corpus is name plus
    description, so a skill without one can never rank. This is the operator's own wording, not an
    invented one.
    """
    body = " ".join(str(text or "").split())
    tail = re.split(r"\b(?:that|which|to|for|it should|and it)\b", body, maxsplit=1)
    detail = tail[1].strip(" .,:;") if len(tail) > 1 else ""
    detail = re.sub(r"^\s*(?:it\s+)?(?:should|will|can)\s+", "", detail, flags=re.IGNORECASE).strip()
    if detail:
        return f"Use when the user asks to {detail}."
    return f"Use when the user asks for {name}."


def maybe_handle_skill_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Run the skill tool this message asks for. None when it asks for none."""
    request = skill_request(user_input)
    if request is None:
        return None

    from core.authorized_tool_execution import (
        REFUSAL_STATUSES,
        execute_authorized_runtime_tool,
    )

    action = str(request.get("action") or "")
    name = str(request.get("name") or "")

    def plain(body: str, *, reason: str, execution: Any = None) -> dict[str, Any]:
        result = agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=body,
            confidence=0.9,
            source_context=source_context,
            reason=reason,
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

    if action == "needs_name":
        return plain(
            "I can author a skill, but I need a name for it -- the name is how you validate, "
            "install and refer to it afterwards. Tell me what to call it and what it should do, "
            "e.g. `create a skill called release-notes that summarises CHANGELOG.md by version`.",
            reason="skill_needs_name",
        )

    if action == "list":
        # The workspace is the operator's own project -- for "what skills do we have", the files in
        # THAT folder are most of the answer, so it rides along for the scan.
        result = execute_authorized_runtime_tool(
            "skill.list",
            {"workspace": str((source_context or {}).get("workspace") or "")},
            source_context=source_context,
        )
        if result is None:
            return plain(
                "I can draft, validate and install skills, but I have no tool for listing them on "
                "this build. Name one and I can validate it.",
                reason="skill_list_unsupported",
            )
        return plain(
            str(getattr(result, "response_text", "") or ""), reason="skill_list", execution=result
        )

    # Authorization for the two actions that touch the filesystem. `list` and `validate` are reads
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
    if action in {"create", "install", "edit", "rollback"}:
        from core.agent_runtime.intent_claims import ActionPolicy, action_policy_from_context

        if action_policy_from_context(source_context) is ActionPolicy.FORBIDDEN:
            return plain(
                f"This turn rules out taking an action, so I did not run `skill.{action}` -- it "
                f"writes to disk. I can still list or validate skills, or author this one once the "
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

        history = skill_history(name)
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
        result = execute_authorized_runtime_tool(
            "skill.rollback",
            {"skill": name, "version": int(request.get("version") or 0)},
            source_context=source_context,
        )
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

    if action == "create":
        result = execute_authorized_runtime_tool(
            "skill.create",
            {
                "name": name,
                "description": _describe(user_input, name),
                "body": str(user_input or "").strip(),
            },
            source_context=source_context,
        )
    elif action == "edit":
        result = execute_authorized_runtime_tool(
            "skill.create",
            {
                "name": name,
                "description": _describe(user_input, name),
                "body": str(user_input or "").strip(),
                "overwrite": True,
            },
            source_context=source_context,
        )
    elif action == "validate":
        result = execute_authorized_runtime_tool(
            "skill.validate", {"path": name}, source_context=source_context
        )
    else:
        # Installing over an existing activation is the EDIT flow's second half: a staged draft
        # of the same skill is what the operator just revised, so the overwrite the request
        # implies is passed through (the prior version is preserved by the version store).
        from core.skill_tools import resolve_skill_markdown, staging_root

        resolved = resolve_skill_markdown(name)
        staged_draft = resolved.is_file() and staging_root() in resolved.resolve().parents
        result = execute_authorized_runtime_tool(
            "skill.install",
            {"path": name, "overwrite": bool(staged_draft)},
            source_context=source_context,
        )

    if result is None:
        return plain(
            f"This build has no `skill.{action}` tool, so I did not pretend to run it.",
            reason=f"skill_{action}_unsupported",
        )
    return plain(
        str(getattr(result, "response_text", "") or ""), reason=f"skill_{action}", execution=result
    )


__all__ = ["maybe_handle_skill_request", "skill_name_in", "skill_request"]
