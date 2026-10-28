"""Bind a chat to the person's own project folder, named in words.

The coding chat historically worked in the empty default workspace because a chat was only
ever bound to a project through the project endpoints: a person saying "my garden planner
project in my Projects folder" never bound anything, so every workspace tool hit the default
folder and approval cards targeted invented paths. This module resolves the folder the
person's words name, inside a bounded set of their own locations, and binds the chat through
the project endpoints' own path and authority — it adds no looser route.

Laws carried from the job's task sheet:

- matching is on folder names; case, dash, underscore, dot and space are equal separators,
  and camelCase splits too ("GardenPlanner" == "garden planner");
- "project", "app", "thing", "folder" are generic nouns and never part of the name;
- the search set is bounded: the home folder's direct children plus the children of
  Projects, Code, Developer, Documents and Desktop — two levels deep at most, never a disk
  walk, never a system or secret location, never outside the person's home;
- exactly one match binds; several matches ask which one; none say so plainly;
- binding gives the chat its workspace and nothing else — writes keep their approval cards;
- only an owner-local turn binds; a remote surface never does.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GENERIC_NOUNS = (
    "project", "projects", "app", "apps", "thing", "things", "folder", "folders",
    "repo", "repos", "repository", "repositories",
)
_GENERIC_NOUN_RE = re.compile(
    r"\s*\b(?:" + "|".join(re.escape(word) for word in _GENERIC_NOUNS) + r")\b\s*",
    re.IGNORECASE,
)

# The five named containers plus the home folder itself make up the whole search set.
_CONTAINERS = ("Projects", "Code", "Developer", "Documents", "Desktop")
_CONTAINER_ALIASES = {
    "projects": "Projects",
    "code": "Code",
    "developer": "Developer",
    "dev": "Developer",
    "documents": "Documents",
    "document": "Documents",
    "docs": "Documents",
    "desktop": "Desktop",
}
# System trees that live INSIDE a person's home folder and are never a person's project.
_HOME_DENY_NAMES = {"library", "applications"}

# A phrase that can stand for a folder name: one or more name-like tokens, possibly joined
# by spaces. Raw captures are cut down to their leading name tokens afterwards (_cut_phrase).
_PHRASE = r"[A-Za-z0-9][\w'’.-]*(?:\s+[A-Za-z0-9][\w'’.-]*)*"
_QUOTED_RE = re.compile(r"[\"'`](?P<name>" + _PHRASE + r")[\"'`]")
# "Projects/garden planner" or "~/Code/recipe app": the container plus the name after the slash.
_PATH_LIKE_RE = re.compile(
    r"(?:~/)?"
    r"(?P<container>projects|code|developer|documents?|docs|desktop)\s*/\s*"
    r"(?P<name>" + _PHRASE + r")",
    re.IGNORECASE,
)
# "my garden planner project", "the recipe app": a possessive or demonstrative word, the name,
# an optional trailing generic noun. A bare article ("a docs folder") is deliberately NOT here:
# an article names a thing to be made, not the person's own existing project.
_POSSESSIVE_RE = re.compile(
    r"\b(?:my|our|the|this|that)\s+(?P<name>" + _PHRASE + r")",
    re.IGNORECASE,
)
# "in garden planner", "work on GardenPlanner", "inside my code": a locative naming where the
# work lives. A container word instead of a name narrows the search rather than naming a folder.
_LOCATIVE_RE = re.compile(
    r"\b(?:in|inside|under|on)\s+(?:(?:my|our|the|this|that)\s+)?"
    r"(?P<name>" + _PHRASE + r")",
    re.IGNORECASE,
)
_LOCATIVE_CONTAINER_RE = re.compile(
    r"\b(?:in|inside|under|on)\s+(?:(?:my|our|the|this|that)\s+)?"
    r"(?P<container>projects|code|developer|dev|documents?|docs|desktop)\b"
    r"(?:\s+(?:folder|directory))?",
    re.IGNORECASE,
)
# "a docs folder", "garden planner project": the name standing immediately before a generic
# noun, with an optional article. Lower priority than the framings above.
_ARTICLE_NOUN_RE = re.compile(
    r"\b(?:an?\s+|the\s+)?"
    r"(?P<name>" + _PHRASE + r"?)\s+(?:"
    r"|".join(re.escape(word) for word in _GENERIC_NOUNS) + r")\b",
    re.IGNORECASE,
)

# Words that end a captured phrase: prepositions, determiners, conjunctions and fillers name
# structure, not the folder. Generic nouns are NOT here — they ride along and are stripped
# from the phrase's tail, and container words end a phrase because they name a location.
_PHRASE_CUT_WORDS = {
    "in", "on", "under", "inside", "into", "and", "or", "but", "with", "for", "to", "of",
    "at", "about", "now", "please", "thanks", "thank", "later", "also", "then", "first",
    "my", "our", "the", "this", "that", "these", "those", "it", "its", "me", "us", "you",
    "there", "here", "sure", "certain", "yes", "no", "not", "up", "out", "down", "over",
}
_STOP_NAME_WORDS = {
    "", "a", "an", "the", "my", "our", "this", "that", "these", "those", "it", "its",
    "me", "us", "you", "here", "there", "now", "then", "some", "any", "one", "new",
    "old", "own", "sure", "certain", "please", "thanks", "yes", "no", "not", "and",
    "or", "but", "with", "for", "to", "of", "at", "on", "in", "into", "under", "about",
    "later", "first", "last", "next", "other", "another", "same", "different", "little",
    "small", "big", "short", "long", "quick", "scratch", "temp", "test", "idea", "ideas",
    "something", "anything", "everything", "nothing",
}
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORDS_MAX = 2000
_MAX_LISTED = 3


@dataclass(frozen=True)
class FolderWordsResolution:
    """What the person's words resolved to.

    status: "found" (exactly one folder), "several" (two or more, listed), "none" (a name
    was extracted but no folder matched), "no_ask" (the words name no folder at all).
    message carries the plain-words line for "several" and "none"; empty otherwise.
    """

    status: str
    path: str = ""
    name: str = ""
    matches: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    message: str = ""


def _name_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for run in re.findall(r"[0-9A-Za-z]+", str(text or "")):
        for piece in _CAMEL_SPLIT_RE.split(run):
            if piece:
                tokens.append(piece.lower())
    return tokens


def _is_version_token(token: str) -> bool:
    return bool(re.fullmatch(r"v?\d+", token))


_GENERIC_TOKENS = frozenset(_GENERIC_NOUNS)


def _phrase_names_folder(phrase: str, folder: str) -> bool:
    """True when the phrase's tokens name the folder's tokens.

    Generic nouns are not part of the name on EITHER side ("recipe" names "recipe-app",
    "garden planner" names a folder literally called "garden planner project"). Equal token
    runs are the base case; a trailing run of at most two version-like tokens ("garden
    planner" vs "garden planner 2" / "gardenplannerv2") still names the folder, while a
    PARTIAL name ("garden" for "garden planner") deliberately does not — a shorter phrase
    standing for a longer folder name is how an idea ask binds the wrong project.
    Joined-without-separators equality covers concatenated spellings ("GardenPlanner").
    """
    phrase_tokens = [token for token in _name_tokens(phrase) if token not in _GENERIC_TOKENS]
    folder_tokens = [token for token in _name_tokens(folder) if token not in _GENERIC_TOKENS]
    if not phrase_tokens or not folder_tokens:
        return False
    if phrase_tokens == folder_tokens:
        return True
    if "".join(phrase_tokens) == "".join(folder_tokens):
        return True
    extra = folder_tokens[len(phrase_tokens):]
    return (
        len(folder_tokens) > len(phrase_tokens)
        and folder_tokens[: len(phrase_tokens)] == phrase_tokens
        and 0 < len(extra) <= 2
        and all(_is_version_token(token) for token in extra)
    )


def _cut_phrase(raw: str) -> str:
    """The leading name tokens of a raw capture: cut at the first structural word."""
    tokens: list[str] = []
    for token in str(raw or "").split():
        lowered = token.lower().strip(".,!?;:")
        if lowered in _PHRASE_CUT_WORDS or lowered in _CONTAINER_ALIASES:
            break
        tokens.append(token.strip(".,!?;:"))
        if len(tokens) >= 6:
            break
    return " ".join(tokens)


def _clean_phrase(raw: str) -> str:
    phrase = _cut_phrase(raw)
    phrase = _GENERIC_NOUN_RE.sub(" ", phrase).strip(" -_.")
    phrase = " ".join(phrase.split())
    if not phrase:
        return ""
    first = phrase.split()[0].lower()
    if first in _STOP_NAME_WORDS:
        return ""
    return phrase


def _variants(phrase_raw: str) -> list[str]:
    """The cleaned phrase plus the cut phrase with its trailing generic noun kept, deduped.

    "recipe" (from "the recipe app") must still reach a folder literally named "recipe-app",
    so the merely-cut spelling rides along as a lower-priority variant.
    """
    cut = _cut_phrase(phrase_raw)
    cleaned = _clean_phrase(phrase_raw)
    out: list[str] = []
    for candidate in (cleaned, cut):
        if (
            candidate
            and candidate.lower() not in _STOP_NAME_WORDS
            and candidate not in out
        ):
            out.append(candidate)
    return out


def _real_home(home: str | Path) -> Path:
    return Path(str(home or "")).expanduser().resolve()


def _candidate_folders(home_real: Path, container: str | None) -> list[tuple[Path, str]]:
    """The bounded search set: home's direct children, or one container's children."""
    bases: list[Path]
    if container is None:
        bases = [home_real] + [home_real / name for name in _CONTAINERS]
    else:
        bases = [home_real / container]
    found: dict[str, tuple[Path, str]] = {}
    for base in bases:
        try:
            entries = sorted(os.scandir(base), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name.lower() in _HOME_DENY_NAMES:
                continue
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
                resolved = Path(entry.path).resolve()
            except OSError:
                continue
            if resolved == home_real or home_real not in resolved.parents:
                continue
            real_key = str(resolved)
            if real_key not in found:
                found[real_key] = (resolved, name)
    return list(found.values())


def resolve_folder_from_words(words: str, home: str | Path) -> FolderWordsResolution:
    """Resolve the person's words to one of their own folders, or say why not."""
    text = " ".join(str(words or "").split())[:_WORDS_MAX]
    home_real = _real_home(home)
    if not text or not home_real.is_dir():
        return FolderWordsResolution(status="no_ask")

    container: str | None = None
    container_match = _PATH_LIKE_RE.search(text) or _LOCATIVE_CONTAINER_RE.search(text)
    if container_match is not None:
        container = _CONTAINER_ALIASES.get(str(container_match.group("container") or "").lower(), container)

    # Candidate phrases, in priority order: quoted, path-like, possessive, locative, then
    # article-plus-generic-noun. The first tier whose phrases match real folders decides;
    # a lower tier is only reached when every higher tier matched nothing.
    tiers: list[list[str]] = [[] for _ in range(5)]
    for match in _QUOTED_RE.finditer(text):
        tiers[0].extend(_variants(match.group("name")))
    for match in _PATH_LIKE_RE.finditer(text):
        tiers[1].extend(_variants(match.group("name")))
    for match in _POSSESSIVE_RE.finditer(text):
        tiers[2].extend(_variants(match.group("name")))
    for match in _LOCATIVE_RE.finditer(text):
        candidate = str(match.group("name") or "")
        lowered = candidate.lower()
        if lowered in _CONTAINER_ALIASES or lowered in _STOP_NAME_WORDS:
            continue
        tiers[3].extend(_variants(candidate))
    for match in _ARTICLE_NOUN_RE.finditer(text):
        tiers[4].extend(_variants(match.group("name")))

    any_phrase = any(tier for tier in tiers)
    if not any_phrase:
        return FolderWordsResolution(status="no_ask")

    folders = _candidate_folders(home_real, container)
    for tier in tiers:
        matched: dict[str, tuple[Path, str]] = {}
        for phrase in tier:
            for folder_path, folder_name in folders:
                if _phrase_names_folder(phrase, folder_name):
                    matched.setdefault(str(folder_path), (folder_path, folder_name))
        if len(matched) == 1:
            path, name = next(iter(matched.values()))
            return FolderWordsResolution(status="found", path=str(path), name=name)
        if len(matched) > 1:
            listed = sorted(matched.values(), key=lambda item: item[1].lower())[:_MAX_LISTED]
            names = ", ".join(f'"{name}"' for _path, name in listed)
            return FolderWordsResolution(
                status="several",
                matches=tuple((str(path), name) for path, name in listed),
                message=(
                    "I found more than one folder like that: "
                    f"{names}. Which one should I work in?"
                ),
            )
    return FolderWordsResolution(
        status="none",
        message=(
            "I couldn't find a folder with that name in the places I may look: "
            "your home folder and your Projects, Code, Developer, Documents and Desktop folders."
        ),
    )


def apply_workspace_words_binding(
    session_id: str,
    user_text: str,
    *,
    home: str | Path,
    owner_local: bool,
    requested_workspace: str = "",
    bound_project_id: str = "",
) -> dict[str, Any]:
    """Bind the chat to the folder its words name, through the project endpoints' own path.

    Returns one of:
      {"status": "found",   "bound": True,  "project_id": ..., "root": ..., "notice": ...}
      {"status": "several", "bound": False, "notice": ...}
      {"status": "none",    "bound": False, "notice": ...}
      {"status": "refused", "bound": False, "notice": ...}   # a guard said no; nothing bound
      {"status": "no_ask",  "bound": False, "notice": ""}
      {"status": "skip",    "bound": False, "notice": ""}    # remote surface, explicit
      #   workspace, or a chat already bound to a project
    """
    clean_chat = str(session_id or "").strip()
    if (
        not owner_local
        or str(requested_workspace or "").strip()
        or str(bound_project_id or "").strip()
    ):
        return {"status": "skip", "bound": False, "notice": ""}

    resolution = resolve_folder_from_words(user_text, home)
    if resolution.status == "no_ask":
        return {"status": "no_ask", "bound": False, "notice": ""}
    if resolution.status in {"several", "none"}:
        return {"status": resolution.status, "bound": False, "notice": resolution.message}
    if resolution.status != "found" or not resolution.path:
        return {"status": "no_ask", "bound": False, "notice": ""}

    from core import project_store

    ok, project = project_store.create_project(resolution.name, resolution.path)
    if not ok:
        reason = str(project or "").strip() or "that folder is not one I may work in."
        return {
            "status": "refused",
            "bound": False,
            "notice": f"I couldn't start working in that folder: {reason}",
        }
    project_id = str(project.get("id") or "").strip()
    root = str(project.get("root") or resolution.path)
    if not project_id:
        return {"status": "refused", "bound": False, "notice": "that folder could not be set up as a project."}

    try:
        from core.context_namespace import (
            ensure_chat_namespace,
            grant_context_import,
            set_chat_namespace_project,
        )
        from core.persistent_memory import set_session_meta

        ensure_chat_namespace(clean_chat, project_id=project_id, grant_confirmed_profile=True)
        set_chat_namespace_project(clean_chat, project_id)
        grant_context_import(
            clean_chat,
            scope="project",
            source_id=f"project:{project_id}",
            source_project_id=project_id,
        )
        set_session_meta(clean_chat, project_id=project_id)
    except Exception:
        return {
            "status": "refused",
            "bound": False,
            "notice": "I found the folder but couldn't switch this chat to it. Nothing was changed.",
        }
    return {
        "status": "found",
        "bound": True,
        "project_id": project_id,
        "root": root,
        "notice": f"I'm working in your {resolution.name} folder now.",
    }
