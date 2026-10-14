"""Where a user-made skill lives: this chat, this project, or all of VOOL.

"We need a skill for this chat / for the whole project / for all of VOOL that does X" names a
SCOPE, and the scope decides two things: where the skill's files are kept, and which turns may
load it. A chat skill must never reach another chat, and a project skill must never reach a chat
bound to a different project.

The chat and project KEYS are not decided here. They come from the one scope model the user rules
own (``core.user_rules.scope_keys_for``): the chat key is the turn's session id, and the project
key is the project that chat is bound to, server-side. Rules and skills therefore always agree on
which chat and which project a turn belongs to.

Global skills keep living where they always have (the installed plugins tree that
``core.skill_tools.plugins_root`` resolves). Chat and project skills live in the runtime's data
directory, one folder per chat or project, with the same ``SKILL.md`` + ``versions/`` layout, so the
loader, the version store and the exporter treat every scope the same way.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

SCOPES: tuple[str, ...] = ("chat", "project", "global")

#: How each scope is said back to the operator.
SCOPE_LABELS: dict[str, str] = {
    "chat": "this chat",
    "project": "this project",
    "global": "all of VOOL",
}

#: The data-directory folder every chat and project skill lives under.
USER_SKILLS_DIRNAME = "user-skills"
ACTIVE_DIRNAME = "skills"
STAGED_DIRNAME = "staged"
REMOVED_DIRNAME = "removed"

# Scope words. Chat first: "for this chat" is the narrowest statement and must win over a
# "project" that appears elsewhere in the same sentence. Plural "all projects" / "all chats" is
# everywhere; singular "all project" / "the whole project" is this project (how the request is
# actually typed: "for this chat or for all project or as per whole vool").
#
# The patterns are phrases about WHERE the skill applies, never a bare noun: "a skill that posts
# to the chat" or "summarises global news" says what the skill does, not where it lives, so "the"
# is accepted only after "for" and a bare "global" only next to the skill or "it".
_CHAT_RE = re.compile(
    r"\b(?:for|in|inside|within|from)\s+(?:this|current|our|my)\s+(?:chat|conversation|thread)\b"
    r"|\bfor\s+the\s+(?:current\s+)?(?:chat|conversation)\b"
    r"|\b(?:this|current)\s+chat\s+only\b|\bchat[- ](?:only|scoped|level|wide)\b",
    re.IGNORECASE,
)
_GLOBAL_RE = re.compile(
    r"\b(?:all|whole|entire)\s+(?:of\s+)?vool\b|\bvool[- ]wide\b|\bper\s+whole\s+vool\b"
    r"|\b(?:for|across|in)\s+(?:all|every)\s+(?:chats|projects|conversations)\b"
    r"|\b(?:for|in)\s+every\s+(?:chat|project|conversation)\b"
    r"|\bglobal(?:ly)?\s+skills?\b|\b(?:make|keep|save|install|put|add)\s+it\s+global(?:ly)?\b"
    r"|\b(?:use|apply|available|work)s?\s+(?:it\s+)?everywhere\b|\bsystem[- ]wide\b",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(
    r"\b(?:for|in|across|inside|within|from)\s+(?:this|current|our|my)\s+project\b"
    r"|\bfor\s+the\s+(?:current\s+|whole\s+|entire\s+)?project\b"
    r"|\b(?:all|whole|entire)\s+(?:the\s+|this\s+)?project\b|\bproject[- ](?:wide|scoped|level|only)\b",
    re.IGNORECASE,
)

_SAFE_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z")


def scope_in(text: str) -> str:
    """The scope a request names (``chat`` / ``project`` / ``global``), or ``""`` when it names none."""
    body = " ".join(str(text or "").split())
    if not body:
        return ""
    if _CHAT_RE.search(body):
        return "chat"
    if _GLOBAL_RE.search(body):
        return "global"
    if _PROJECT_RE.search(body):
        return "project"
    return ""


def strip_scope_words(text: str) -> str:
    """The request with its scope phrase removed, so it does not leak into a name or description."""
    out = str(text or "")
    for pattern in (_CHAT_RE, _GLOBAL_RE, _PROJECT_RE):
        out = pattern.sub(" ", out)
    return " ".join(out.split())


def scope_keys(
    source_context: dict[str, Any] | None = None,
    *,
    chat_id: str = "",
    project_id: str = "",
) -> dict[str, str]:
    """``{"chat": <chat key>, "project": <project key>}`` for this turn; either may be ``""``.

    The user-rules scope model answers this (``core.user_rules.scope_keys_for``): explicit
    arguments win; otherwise the chat key is the turn's session id and the project is the chat's
    server-owned binding. A client-supplied workspace string is never an input: it could name
    another project's folder. Skills keep no second opinion about either key.
    """
    from core.user_rules import scope_keys_for

    keys = scope_keys_for(source_context, chat_id=chat_id, project_id=project_id) or {}
    return {"chat": str(keys.get("chat") or ""), "project": str(keys.get("project") or "")}


def _safe_dir(key: str) -> str:
    """A filesystem-safe folder name for a chat or project key. Never a path the key can steer."""
    raw = str(key or "").strip()
    if _SAFE_KEY_RE.fullmatch(raw) and ".." not in raw:
        return raw
    return "k_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def scope_root(scope: str, keys: dict[str, str] | None) -> Path | None:
    """The folder that holds one chat's or one project's skills, or None.

    None for ``global`` (that scope lives in the plugins tree) and for a chat/project scope whose
    key is unknown: a chat skill with no chat to belong to has nowhere to go.
    """
    key = str((keys or {}).get(scope) or "").strip()
    if scope not in ("chat", "project") or not key:
        return None
    from core.runtime_paths import data_path

    return data_path(USER_SKILLS_DIRNAME, scope, _safe_dir(key))


def scope_unavailable_reason(scope: str, keys: dict[str, str] | None) -> str:
    """Why a chat/project scope cannot be used on this turn, or ``""`` when it can."""
    if scope == "chat" and not str((keys or {}).get("chat") or "").strip():
        return "this turn carries no chat id, so there is no chat to keep the skill in"
    if scope == "project" and not str((keys or {}).get("project") or "").strip():
        return "this chat is not bound to a project, so there is no project to keep the skill in"
    return ""


__all__ = [
    "ACTIVE_DIRNAME",
    "REMOVED_DIRNAME",
    "SCOPES",
    "SCOPE_LABELS",
    "STAGED_DIRNAME",
    "USER_SKILLS_DIRNAME",
    "scope_in",
    "scope_keys",
    "scope_root",
    "scope_unavailable_reason",
    "strip_scope_words",
]
