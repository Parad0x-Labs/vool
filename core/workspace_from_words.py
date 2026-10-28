"""Bind a chat to the person's own project folder, named in words.

The coding chat historically worked in the empty default workspace because a chat was only
ever bound to a project through the project endpoints: a person saying "my garden planner
project in my Projects folder" never bound anything, so every workspace tool hit the default
folder and approval cards targeted invented paths. This module resolves the folder the
person's words name, inside a bounded set of their own locations, and binds the chat through
the project endpoints' own path and authority — it adds no looser route.

Laws carried from the job's task sheet and its first amendment:

- the turn must ASK to work in a folder: a work verb (open, work on, look at, fix, run,
  build, edit, test, review, change, add to, or a coding verb) AND a folder pointer (a
  quoted name, a container-named path-like mention, or a name followed by a pointer noun
  such as project/app/repo/folder/code/codebase/stuff/files). Ordinary chat and casual
  mentions of a name never bind and never ask anything — they resolve to "no_ask", silent;
- matching is on folder names; case, dash, underscore, dot and space are equal separators,
  and camelCase splits too ("GardenPlanner" == "garden planner");
- "project", "app", "thing", "folder", "stuff", "files", "codebase", "code" are generic
  nouns and never part of the name ("work on the tax 2025 stuff in documents" names the
  folder "Tax 2025");
- the search set is bounded: the home folder's direct children plus the children of
  Projects, Code, Developer, Documents and Desktop — two levels deep at most, never a disk
  walk, never a system or secret location, never outside the person's home;
- the home's standard folders (Downloads, Music, Pictures, Movies, Public, Library,
  Applications, Desktop, Documents) and the five containers themselves are never bound, and
  neither is a folder whose name or any path part is a secret word (passwords, keys, ssh,
  wallet, seed, ...): those resolve to "refused" with one plain sentence — VOOL will not
  work in that folder — never a path, never a raw refusal reason;
- an exact name wins over a longer near-name (a trailing version-like word); the notice
  may say so in plain words, no ask needed;
- exactly one match binds; several matches ask which one; none say so plainly — but
  "several" and "none" speak only on a work-in-a-folder ask;
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
    "repo", "repos", "repository", "repositories", "stuff", "files", "codebase",
    "codebases", "code",
)
_GENERIC_NOUN_RE = re.compile(
    r"\s*\b(?:" + "|".join(re.escape(word) for word in _GENERIC_NOUNS) + r")\b\s*",
    re.IGNORECASE,
)
# A name followed by one of these nouns is a folder pointer ("my garden planner project",
# "the tax 2025 stuff", "fix my wallet code"). The nouns themselves never name the folder.
_POINTER_NOUNS = frozenset(_GENERIC_NOUNS)

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

# The home's own standard folders, plus the five containers themselves, are never bound.
# These are name comparisons (lower-cased) against a folder that IS the home's direct
# child, or a phrase whose whole name is exactly one of these words.
_STANDARD_HOME_NAMES = frozenset(
    {
        "downloads", "music", "pictures", "movies", "public", "library",
        "applications", "desktop", "documents",
    }
)
_CONTAINER_NAMES = frozenset(alias for alias in _CONTAINER_ALIASES if alias not in {"dev", "docs", "document"})
# Folder names (and any path part, token by token) that hold private material: never
# searched into, never bound, refused in plain words. No general secret-folder-name list
# exists elsewhere in the code base to reuse (the wallet-path list in the cloud privacy
# policy covers only wallet-shaped paths), so this one small list lives here.
_SECRET_NAME_WORDS = frozenset(
    {
        "passwords", "password", "keys", "keychain", "ssh", "gnupg", "gpg", "wallet",
        "recovery", "credentials", "secrets", "2fa", "seed",
    }
)

# A turn asks to work on something when one of these verb shapes appears. Inflections are
# folded in; irregular and loose verbs ("check", "show", "play", "clean") are deliberately
# absent — a casual mention must never look like a work ask.
_WORK_ASK_RES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bopen(?:s|ed|ing)?\b",
        r"\blook(?:s|ed|ing)?\s+(?:at|through|over|into)\b",
        r"\bgo(?:es|ne|ing)?\s+through\b",
        r"\bwork(?:s|ed|ing)?\s+(?:on|in|with)\b",
        r"\bfix(?:es|ed|ing)?\b",
        r"\brun(?:s|ning|ned)?\b",
        r"\b(?:build(?:s|ing)?|built)\b",
        r"\bedit(?:s|ed|ing)?\b",
        r"\btest(?:s|ed|ing)?\b",
        r"\breview(?:s|ed|ing)?\b",
        r"\bchang(?:e|es|ed|ing)\b",
        r"\badd(?:s|ed|ing)?\s+(?:\S+\s+){0,6}?to\b",
        r"\bwrit(?:e|es|ote|ten|ing)\b",
        r"\bcod(?:e|es|ed|ing)\b",
        r"\bdebug(?:s|ged|ging)?\b",
        r"\brefactor(?:s|ed|ing)?\b",
        r"\bcompil(?:e|es|ed|ing)\b",
        r"\bdeploy(?:s|ed|ing)?\b",
        r"\bimplement(?:s|ed|ing)?\b",
        r"\bcommit(?:s|ted|ting)?\b",
        r"\bupdat(?:e|es|ed|ing)\b",
    )
)

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
# "in my Projects folder", "on my Desktop", "under Documents": narrows the search set.
_LOCATIVE_CONTAINER_RE = re.compile(
    r"\b(?:in|inside|under|on)\s+(?:(?:my|our|the|this|that)\s+)?"
    r"(?P<container>projects|code|developer|dev|documents?|docs|desktop)\b"
    r"(?:\s+(?:folder|directory))?",
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
# Determiners that may sit right before a folder name ("my garden planner project").
_DETERMINERS = {"my", "our", "the", "this", "that", "these", "those", "a", "an"}
# A container word preceded by one of these is a location ("in my Code folder"), not the name.
_LOCATIVE_PREPOSITIONS = {"in", "inside", "under", "on"}
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NAME_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w'’.-]*")
_WORDS_MAX = 2000
_MAX_LISTED = 3
_MAX_NAME_TOKENS_BEFORE_NOUN = 4


@dataclass(frozen=True)
class FolderWordsResolution:
    """What the person's words resolved to.

    status: "found" (exactly one folder), "several" (two or more, listed), "none" (a name
        was extracted but no folder matched), "refused" (the folder is one VOOL will not
        work in — a home standard folder/container, or a secret name), "no_ask" (the turn
        does not ask to work in a folder, or names none).
    message carries the plain-words line for "several", "none" and "refused"; empty otherwise.
    note carries the optional plain-words aside for "found" (an exact name won over a
    longer near-name); empty otherwise.
    """

    status: str
    path: str = ""
    name: str = ""
    matches: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    message: str = ""
    note: str = ""


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


def _phrase_relation(phrase: str, folder: str) -> str:
    """How a spoken phrase stands to a folder name: "exact", "near" or "".

    Generic nouns are not part of the name on EITHER side ("recipe" names "recipe-app",
    "garden planner" names a folder literally called "garden planner project"). Equal token
    runs (and joined-without-separator spellings, "GardenPlanner") are exact. A longer
    folder name whose extra tokens are at most two version-like words ("garden planner"
    vs "garden planner 2") is near — the exact name wins over it. A PARTIAL name ("garden"
    for "garden planner") deliberately does not match at all: a shorter phrase standing for
    a longer folder name is how an idea ask binds the wrong project.
    """
    phrase_tokens = [token for token in _name_tokens(phrase) if token not in _GENERIC_TOKENS]
    folder_tokens = [token for token in _name_tokens(folder) if token not in _GENERIC_TOKENS]
    if not phrase_tokens or not folder_tokens:
        return ""
    if phrase_tokens == folder_tokens:
        return "exact"
    if "".join(phrase_tokens) == "".join(folder_tokens):
        return "exact"
    extra = folder_tokens[len(phrase_tokens):]
    if (
        len(folder_tokens) > len(phrase_tokens)
        and folder_tokens[: len(phrase_tokens)] == phrase_tokens
        and 0 < len(extra) <= 2
        and all(_is_version_token(token) for token in extra)
    ):
        return "near"
    return ""


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


def _noun_pointer_phrases(text: str) -> list[str]:
    """Names that sit immediately before a pointer noun: "my garden planner project".

    Walks each pointer noun ("project", "app", "folder", "code", "stuff", ...) backwards
    over plain words, stopping at a determiner, a structural word, or a container word used
    as a location ("in my Code folder"). The noun is a pointer, never part of the name.
    """
    raw_tokens = text.split()
    phrases: list[str] = []
    for index, token in enumerate(raw_tokens):
        word = token.lower().strip(".,!?;:\"'`()")
        if word not in _POINTER_NOUNS:
            continue
        name_tokens: list[str] = []
        cursor = index - 1
        while cursor >= 0 and len(name_tokens) < _MAX_NAME_TOKENS_BEFORE_NOUN:
            candidate = raw_tokens[cursor].lower().strip(".,!?;:\"'`()")
            if candidate in _DETERMINERS:
                break
            if candidate in _PHRASE_CUT_WORDS:
                break
            if candidate in _CONTAINER_ALIASES:
                # A container right before the noun is the NAME only when nothing locative
                # sits in front of it ("open my documents folder" names it; "the recipe
                # app in my Code folder" uses it as a place).
                previous = (
                    raw_tokens[cursor - 1].lower().strip(".,!?;:\"'`()")
                    if cursor >= 1
                    else ""
                )
                if previous in _LOCATIVE_PREPOSITIONS or (
                    previous in _DETERMINERS
                    and cursor >= 2
                    and raw_tokens[cursor - 2].lower().strip(".,!?;:\"'`()")
                    in _LOCATIVE_PREPOSITIONS
                ):
                    break
            if not _NAME_TOKEN_RE.fullmatch(candidate):
                break
            name_tokens.insert(0, raw_tokens[cursor].strip(".,!?;:\"'`()"))
            cursor -= 1
        if not name_tokens:
            continue
        # The raw collected name rides along too: a lone container word ("open my
        # projects folder") is a generic noun that _variants would strip away, but said
        # alone it names the container itself and must reach the refusal check.
        raw_phrase = " ".join(name_tokens)
        for variant in [raw_phrase, *_variants(raw_phrase)]:
            if variant and variant not in phrases:
                phrases.append(variant)
    return phrases


def _tokens_hit_secret_word(tokens: list[str]) -> bool:
    if any(token in _SECRET_NAME_WORDS for token in tokens):
        return True
    return any(
        tokens[position] == "backup" and tokens[position + 1] == "codes"
        for position in range(len(tokens) - 1)
    )


def _phrase_refusal_kind(phrase: str) -> str:
    """Why the person's own words name a folder VOOL will not work in, or "".

    A phrase whose whole name is one standard-folder word ("open my downloads folder"), or
    one container word used as the thing itself ("open my projects folder"), or any phrase
    carrying a secret word ("fix my wallet code"), is refused before any search. Raw name
    tokens are used on purpose: "projects" is a generic noun, but said alone it names the
    container itself.
    """
    tokens = _name_tokens(phrase)
    if not tokens:
        return ""
    if len(tokens) == 1:
        if tokens[0] in _SECRET_NAME_WORDS:
            return "secret"
        if tokens[0] in _CONTAINER_NAMES:
            return "container"
        if tokens[0] in _STANDARD_HOME_NAMES:
            return "standard"
        return ""
    if _tokens_hit_secret_word(tokens):
        return "secret"
    return ""


def _folder_refusal_kind(home_real: Path, folder_path: Path, folder_name: str) -> str:
    """Why a real folder may never be bound, or "": its name or any path part."""
    try:
        relative = folder_path.relative_to(home_real)
    except ValueError:
        return ""
    parts = list(relative.parts)
    if len(parts) == 1 and parts[0].lower() in _STANDARD_HOME_NAMES:
        return "standard"
    if len(parts) == 1 and parts[0].lower() in _CONTAINER_NAMES:
        return "container"
    for part in parts:
        if _tokens_hit_secret_word(_name_tokens(part)):
            return "secret"
    return ""


def _refusal_notice(kind: str, display: str) -> str:
    """One plain sentence, never a path, never a raw refusal reason."""
    if kind == "secret":
        return (
            "I won't work in that folder; it may hold private information such as "
            "passwords or keys, so I will leave it alone."
        )
    shown = str(display or "").strip() or "that folder"
    if kind == "container":
        return (
            f"I won't work in your {shown} folder itself; point me at one folder "
            "inside it."
        )
    return (
        f"I won't work in your {shown} folder; it is one of your computer's own "
        "folders, not one of your projects."
    )


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
    """Resolve the person's words to one of their own folders, or say why not.

    Ordinary chat and casual mentions resolve to "no_ask" and never speak: a resolution
    only happens when the turn asks to work in a folder — a work verb plus a folder
    pointer — and "several"/"none" speak only on such a turn.
    """
    text = " ".join(str(words or "").split())[:_WORDS_MAX]
    home_real = _real_home(home)
    if not text or not home_real.is_dir():
        return FolderWordsResolution(status="no_ask")
    if not any(pattern.search(text) for pattern in _WORK_ASK_RES):
        return FolderWordsResolution(status="no_ask")

    container: str | None = None
    container_match = _PATH_LIKE_RE.search(text) or _LOCATIVE_CONTAINER_RE.search(text)
    if container_match is not None:
        container = _CONTAINER_ALIASES.get(str(container_match.group("container") or "").lower(), container)

    # Folder pointers, in priority order: a quoted name, a path-like container mention,
    # then a name standing before a pointer noun. The first tier whose phrases match real
    # folders decides; a lower tier is only reached when every higher tier matched nothing.
    tiers: list[list[str]] = [[], [], []]
    for match in _QUOTED_RE.finditer(text):
        tiers[0].extend(_variants(match.group("name")))
    for match in _PATH_LIKE_RE.finditer(text):
        tiers[1].extend(_variants(match.group("name")))
    tiers[2].extend(_noun_pointer_phrases(text))
    for tier in tiers:
        for phrase in tier:
            kind = _phrase_refusal_kind(phrase)
            if kind:
                return FolderWordsResolution(
                    status="refused", message=_refusal_notice(kind, phrase)
                )
    if not any(tier for tier in tiers):
        return FolderWordsResolution(status="no_ask")

    folders = _candidate_folders(home_real, container)
    for tier in tiers:
        exact: dict[str, tuple[Path, str]] = {}
        near: dict[str, tuple[Path, str]] = {}
        for phrase in tier:
            for folder_path, folder_name in folders:
                relation = _phrase_relation(phrase, folder_name)
                if relation == "exact":
                    exact.setdefault(str(folder_path), (folder_path, folder_name))
                elif relation == "near":
                    near.setdefault(str(folder_path), (folder_path, folder_name))
        matched = {**near, **exact}
        if not matched:
            continue
        allowed: dict[str, tuple[Path, str]] = {}
        refused: dict[str, tuple[Path, str]] = {}
        for key, entry in matched.items():
            kind = _folder_refusal_kind(home_real, entry[0], entry[1])
            if kind:
                refused[key] = entry
            else:
                allowed[key] = entry
        if refused and not allowed:
            path, name = next(iter(refused.values()))
            kind = _folder_refusal_kind(home_real, path, name)
            return FolderWordsResolution(
                status="refused", message=_refusal_notice(kind, name)
            )
        if not allowed:
            continue
        exact_allowed = {key: value for key, value in exact.items() if key in allowed}
        near_allowed = {key: value for key, value in near.items() if key in allowed}
        if len(exact_allowed) == 1:
            path, name = next(iter(exact_allowed.values()))
            note = ""
            if near_allowed:
                listed = sorted(near_allowed.values(), key=lambda item: item[1].lower())[:_MAX_LISTED]
                names = ", ".join(f'"{listed_name}"' for _p, listed_name in listed)
                note = (
                    f"There is also a similar folder called {names}; this one matches "
                    "the name exactly."
                )
            return FolderWordsResolution(
                status="found", path=str(path), name=name, note=note
            )
        winners = exact_allowed or near_allowed
        if len(winners) == 1:
            path, name = next(iter(winners.values()))
            return FolderWordsResolution(status="found", path=str(path), name=name)
        listed = sorted(winners.values(), key=lambda item: item[1].lower())[:_MAX_LISTED]
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

    Refusal notices are one plain sentence with no path in them, whatever the underlying
    registry said. Binding goes through the project endpoints' own path: project_store
    .create_project (idempotent, its guards refuse home/system/secret roots),
    set_chat_namespace_project, grant_context_import(scope="project", source_id=
    "project:<id>"), set_session_meta(project_id=<id>). Never auto-approves any write.
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
    if resolution.status in {"several", "none", "refused"}:
        return {"status": resolution.status, "bound": False, "notice": resolution.message}
    if resolution.status != "found" or not resolution.path:
        return {"status": "no_ask", "bound": False, "notice": ""}

    from core import project_store

    try:
        ok, project = project_store.create_project(resolution.name, resolution.path)
    except Exception:
        return {
            "status": "refused",
            "bound": False,
            "notice": "I found the folder but couldn't set it up as your project, so nothing was changed.",
        }
    if not ok:
        return {
            "status": "refused",
            "bound": False,
            "notice": "I couldn't start working in that folder, so nothing was changed.",
        }
    project_id = str(project.get("id") or "").strip()
    root = str(project.get("root") or resolution.path)
    if not project_id:
        return {
            "status": "refused",
            "bound": False,
            "notice": "I couldn't set that folder up as your project, so nothing was changed.",
        }

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
            "notice": "I found the folder but couldn't switch this chat to it, so nothing was changed.",
        }
    notice = f"I'm working in your {resolution.name} folder now."
    if resolution.note:
        notice = f"{notice} {resolution.note}"
    return {
        "status": "found",
        "bound": True,
        "project_id": project_id,
        "root": root,
        "notice": notice,
    }
