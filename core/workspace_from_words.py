"""Bind a chat to the person's own project folder, named in words.

The coding chat historically worked in the empty default workspace because a chat was only
ever bound to a project through the project endpoints: a person saying "my garden planner
project in my Projects folder" never bound anything, so every workspace tool hit the default
folder and approval cards targeted invented paths. This module resolves the folder the
person's words name, inside a bounded set of their own locations, and binds the chat through
the project endpoints' own path and authority — it adds no looser route.

Laws carried from the job's task sheet and its amendments:

- a statement is not an ask. The work verb must open its own clause, or sit right after an
  asking opener (please / can you / could you / would you / will you / let's / help me /
  I want you to); any subject standing before the verb ("I'm building a garden project",
  "my kid is building a lego project"), or a past tense ("I wrote some code"), makes the
  turn a statement: no_ask, silent. The same sentence shape governs note asks elsewhere;
- the verb must work on the folder. A pointer counts only as a licensed verb's own object
  ("open X", "fix X", "add a docs folder to X") or after a working preposition — work
  on/in/with, look at/through/into, go through, or a plain in/inside location. A pointer
  sitting inside a make-something complement ("write a poem about my novel project", "run
  the numbers on my budget stuff") is licensed by nothing: no_ask, silent;
- an ask whose object is a determiner plus a name with no pointer noun ("work on the garden
  planner", "open my garden planner, not the old one", "open the website") binds only on an
  EXACT match to a real folder — exact still wins over a longer near-name with the same
  plain aside — and with no exact match it stays silent: no "none" notice without a pointer
  noun. "not the <x> one" never names a folder;
- "a"/"an" before a name names a thing to make, never an existing folder ("make certain
  there's a tests folder in recipe box"): such a pointer is dropped, and the folder named
  after a following "in" is the one that binds;
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
- exactly one match binds; several matches ask which one; none say so plainly — but
  "several" and "none" speak only on a licensed pointer;
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
# absent — a casual mention must never look like a work ask. "make sure/certain" carries an
# ensure ask ("make certain there's a tests folder in recipe box"), while a bare "make"
# stays out: "make dinner" is not a work ask.
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
        r"\bmake(?:s|ing)?\s+(?:sure|certain)\b",
    )
)

# ----------------------------------------------------------------------------------------------
# The ask's shape: where the verb sits in its clause (a statement is not an ask)
# ----------------------------------------------------------------------------------------------

# A clause runs between these separators; the verb's place is judged inside its own clause.
_CLAUSE_SEPARATORS_RE = re.compile(r"[.!?,;:\n]+")
# Words that may open a clause before the verb or an asking opener and still leave the
# turn an ask ("hey, open the recipe app", "ok please review my notes").
_ASK_FILLER_WORDS = frozenset(
    {
        "hey", "hi", "hello", "ok", "okay", "yes", "yeah", "yep", "yup", "sure",
        "please", "just", "kindly", "so", "well", "now", "then", "also", "and", "but",
    }
)
# An ask's opener: the verb may sit right after one of these. Token tuples, matched on
# clause-initial (post-filler) position.
_ASK_OPENER_TOKENS = tuple(
    tuple(tokens)
    for tokens in (
        ("can", "you"), ("could", "you"), ("would", "you"), ("will", "you"),
        ("let's",), ("lets",), ("let", "us"),
        ("help", "me"), ("help", "us"),
        ("i", "want", "you", "to"), ("i", "need", "you", "to"),
        ("i'd", "like", "you", "to"), ("i'd", "love", "you", "to"),
    )
)
# Words allowed between an opener and the verb ("could you please just open ...").
_AFTER_OPENER_WORDS = frozenset(
    {"please", "just", "now", "also", "then", "kindly", "to", "quickly", "go", "ahead", "and"}
)
# Past forms are statements, wherever they sit ("I wrote some code yesterday").
_PAST_IRREGULAR = frozenset({"wrote", "built", "ran"})


def _is_past_form(form: str) -> bool:
    return form in _PAST_IRREGULAR or (form.endswith("ed") and len(form) > 3)


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

# ----------------------------------------------------------------------------------------------
# The verb must work on the folder: what licenses a pointer phrase
# ----------------------------------------------------------------------------------------------

# A pointer is the verb's own object when only these may stand between the verb's end and
# the phrase's start (or the determiner in front of it).
_OBJECT_GAP_RE = re.compile(
    r"^\s*(?:(?:my|our|the|this|that|these|those|his|her|their|your|please|just|now|also|then)\s+)*$",
    re.IGNORECASE,
)
# A pointer after one of these working prepositions is licensed anywhere in the clause.
_PREP_HEAD_RES = tuple(
    re.compile(pattern + r"\s*$", re.IGNORECASE)
    for pattern in (
        r"\b(?:work|works|working)\s+(?:on|in|with)",
        r"\b(?:look|looks|looking)\s+(?:at|through|into|over)",
        r"\b(?:go|goes|going)\s+through",
        r"\b(?:in|inside)",
    )
)
# The same shapes as extraction sites for a plain (determiner-plus-name) object.
_PREP_SITE_RE = re.compile(
    r"\b(?:work|works|working)\s+(?:on|in|with)\s+"
    r"|\b(?:look|looks|looking)\s+(?:at|through|into|over)\s+"
    r"|\b(?:go|goes|going)\s+through\s+"
    r"|\b(?:in|inside)\s+",
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
# A plain object's determiner: an article ("a"/"an") names a thing to make, never a folder.
_PLAIN_DETERMINERS = {"my", "our", "the", "this", "that", "these", "those", "his", "her", "their", "your"}
_PLAIN_POLITENESS = {"please", "just", "now", "also", "then"}
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


# ----------------------------------------------------------------------------------------------
# Clause tokens, the ask's shape, and pointer licensing
# ----------------------------------------------------------------------------------------------


def _segment_words(segment: str) -> list[tuple[str, int, int, str]]:
    """(raw, start, end, cleaned-lower) for every whitespace token of the clause."""
    out: list[tuple[str, int, int, str]] = []
    for match in re.finditer(r"\S+", segment):
        raw = match.group(0)
        cleaned = raw.lower().strip(".,!?;:\"`()")
        cleaned = cleaned.replace("’", "'").strip("'")
        out.append((raw, match.start(), match.end(), cleaned))
    return out


def _ask_shaped(left_words: list[str], gerund: bool) -> bool:
    """Whether the words before the verb leave the clause an ask.

    Fillers may open; then one asking opener may appear; then only politeness may bridge to
    the verb. Anything else standing before the verb — a subject like "I", "my kid", "she" —
    makes the clause a statement. A bare gerund ("working on the tax stuff was slow") is a
    statement too: it needs an opener.
    """
    index, count = 0, len(left_words)
    while index < count and left_words[index] in _ASK_FILLER_WORDS:
        index += 1
    opener = False
    for tokens in _ASK_OPENER_TOKENS:
        length = len(tokens)
        if left_words[index:index + length] == list(tokens):
            opener = True
            index += length
            break
    while index < count and left_words[index] in _AFTER_OPENER_WORDS:
        index += 1
    if index != count:
        return False
    if gerund and not opener:
        return False
    return True


def _licensed_work_verbs(segment: str) -> list[tuple[int, int]]:
    """The (start, end) spans of work verbs this clause actually asks for.

    A past form is a statement wherever it sits. A verb mid-clause must be preceded only by
    fillers, an asking opener, or politeness.
    """
    words = _segment_words(segment)
    starts = [word[1] for word in words]
    licensed: list[tuple[int, int]] = []
    for pattern in _WORK_ASK_RES:
        for match in pattern.finditer(segment):
            form = match.group(0).split()[0].lower()
            if _is_past_form(form):
                continue
            position = 0
            while position < len(starts) and starts[position] <= match.start():
                position += 1
            position -= 1
            if position < 0:
                continue
            left_words = [word[3] for word in words[:position]]
            if _ask_shaped(left_words, form.endswith("ing")):
                span = (match.start(), match.end())
                if span not in licensed:
                    licensed.append(span)
    return licensed


def _phrase_is_licensed(
    segment: str,
    licensed_verb_spans: list[tuple[int, int]],
    effective_start: int,
) -> bool:
    """Whether a pointer phrase is the object of a licensed verb or follows a working
    preposition: only then does the verb work ON the folder the phrase names. A working
    preposition inherits its ask from a licensed verb in the same clause — a statement
    ("she is working on her thesis project") licenses nothing."""
    if licensed_verb_spans:
        head = segment[:effective_start]
        if any(pattern.search(head) for pattern in _PREP_HEAD_RES):
            return True
        for verb_start, verb_end in licensed_verb_spans:
            if verb_end <= effective_start and _OBJECT_GAP_RE.match(
                segment[verb_end:effective_start]
            ):
                return True
    return False


def _noun_pointer_phrases(segment: str) -> list[tuple[str, int]]:
    """(phrase, effective start) for names standing immediately before a pointer noun.

    Walks each pointer noun ("project", "app", "folder", "code", "stuff", ...) backwards
    over plain words, stopping at a determiner, a structural word, or a container word used
    as a location ("in my Code folder"). The noun is a pointer, never part of the name. A
    name whose determiner is an article ("a tests folder") names a thing to make and is
    dropped, never an existing folder.
    """
    words = _segment_words(segment)
    phrases: list[tuple[str, int]] = []
    for index, (_raw, _start, _end, word) in enumerate(words):
        if word not in _POINTER_NOUNS:
            continue
        name_tokens: list[str] = []
        effective_start = -1
        cursor = index - 1
        while cursor >= 0 and len(name_tokens) < _MAX_NAME_TOKENS_BEFORE_NOUN:
            candidate = words[cursor][3]
            if candidate in _DETERMINERS:
                if candidate in {"a", "an"}:
                    name_tokens = []
                else:
                    effective_start = words[cursor][1]
                break
            if candidate in _PHRASE_CUT_WORDS:
                break
            if candidate in _CONTAINER_ALIASES:
                # A container right before the noun is the NAME only when nothing locative
                # sits in front of it ("open my documents folder" names it; "the recipe
                # app in my Code folder" uses it as a place).
                previous = words[cursor - 1][3] if cursor >= 1 else ""
                if previous in _LOCATIVE_PREPOSITIONS or (
                    previous in _DETERMINERS
                    and cursor >= 2
                    and words[cursor - 2][3] in _LOCATIVE_PREPOSITIONS
                ):
                    break
            if not _NAME_TOKEN_RE.fullmatch(candidate):
                break
            name_tokens.insert(0, words[cursor][0].strip(".,!?;:\"'`()"))
            effective_start = words[cursor][1]
            cursor -= 1
        if not name_tokens or effective_start < 0:
            continue
        phrases.append((" ".join(name_tokens), effective_start))
    return phrases


def _plain_name_phrases(
    segment: str, licensed_verb_spans: list[tuple[int, int]]
) -> list[tuple[str, int]]:
    """(phrase, effective start) for an ask's plain object: a determiner plus a name with
    no pointer noun ("open the website", "in recipe box").

    Sites sit right after a licensed verb, after a working preposition (work on/in/with,
    look at/through/into, go through, in/inside). An article in front of the name drops the
    site: it names a thing to make. A name run that meets or ends at a pointer noun belongs
    to the pointer-noun tier instead.
    """
    words = _segment_words(segment)
    if not words:
        return []
    starts = [word[1] for word in words]

    def token_at(char_position: int) -> int:
        index = 0
        while index < len(starts) and starts[index] <= char_position:
            index += 1
        return index - 1

    site_indices: set[int] = set()
    for _verb_start, verb_end in licensed_verb_spans:
        index = token_at(verb_end) + 1
        if 0 <= index < len(words):
            site_indices.add(index)
    for match in _PREP_SITE_RE.finditer(segment):
        index = token_at(match.end())
        if 0 <= index < len(words):
            site_indices.add(index)

    phrases: list[tuple[str, int]] = []
    for site in sorted(site_indices):
        index = site
        while index < len(words) and words[index][3] in _PLAIN_POLITENESS:
            index += 1
        effective_start = -1
        if index < len(words) and words[index][3] in _PLAIN_DETERMINERS:
            effective_start = words[index][1]
            index += 1
        elif index < len(words) and words[index][3] in {"a", "an"}:
            continue
        name_tokens: list[str] = []
        while index < len(words) and len(name_tokens) < _MAX_NAME_TOKENS_BEFORE_NOUN:
            candidate = words[index][3]
            if candidate in _POINTER_NOUNS:
                name_tokens = []
                break
            if (
                not candidate
                or candidate in _PHRASE_CUT_WORDS
                or candidate in _CONTAINER_ALIASES
                or not _NAME_TOKEN_RE.fullmatch(candidate)
            ):
                break
            name_tokens.append(words[index][0].strip(".,!?;:\"'`()"))
            if effective_start < 0:
                effective_start = words[index][1]
            index += 1
        if not name_tokens:
            continue
        if name_tokens[0].lower().strip(".,!?;:\"'`()") in _STOP_NAME_WORDS:
            continue
        if index < len(words) and words[index][3] in _POINTER_NOUNS:
            # "... the garden planner project": the pointer-noun tier owns this object.
            continue
        phrases.append((" ".join(name_tokens), effective_start))
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


class _PointerTiers:
    """Folder-pointer phrases per tier, each licensed by the clause's own shape.

    Tier 0: a quoted name. Tier 1: a container path-like mention ("Projects/garden
    planner"). Tier 2: a name standing before a pointer noun ("garden planner project").
    Tier 3: a plain determiner-plus-name object ("the website", "in recipe box") — this
    tier binds only on an exact folder name and never earns a "none" notice. Where a
    licensed phrase of tiers 0-2 already names the clause's object, the plain tier stays
    out: the "in <x>" of such a clause is a location of that object, not a second name.
    """

    __slots__ = ("tiers", "any_pointer_phrase", "any_ask_verb")

    def __init__(self) -> None:
        self.tiers: list[list[tuple[str, list[str]]]] = [[], [], [], []]
        # whether any licensed phrase of tiers 0-2 was found (they may speak a "none")
        self.any_pointer_phrase = False
        # whether any clause asked with a licensed work verb at all
        self.any_ask_verb = False

    def add(self, tier: int, phrase: str, variants: list[str]) -> None:
        self.tiers[tier].append((phrase, variants))
        if tier < 3:
            self.any_pointer_phrase = True


def _collect_pointer_tiers(text: str) -> _PointerTiers:
    result = _PointerTiers()
    for segment in _CLAUSE_SEPARATORS_RE.split(text):
        if not segment.strip():
            continue
        licensed_verbs = _licensed_work_verbs(segment)
        if licensed_verbs:
            result.any_ask_verb = True
        # Pointer phrases, in priority order within the clause: a quoted name, a
        # path-like container mention, a name standing before a pointer noun, then a
        # plain determiner-plus-name object. A phrase counts only where a licensed verb
        # works on it.
        quoted: list[tuple[int, str]] = []
        for match in _QUOTED_RE.finditer(segment):
            quoted.append((match.start(), match.group("name")))
        path_like: list[tuple[int, str]] = []
        for match in _PATH_LIKE_RE.finditer(segment):
            path_like.append((match.start(), match.group("name")))
        noun_pointers = _noun_pointer_phrases(segment)
        named_by_upper_tier = False
        for start, name in quoted:
            if _phrase_is_licensed(segment, licensed_verbs, start):
                result.add(0, name, _variants(name))
                named_by_upper_tier = True
        for start, name in path_like:
            if _phrase_is_licensed(segment, licensed_verbs, start):
                result.add(1, name, _variants(name))
                named_by_upper_tier = True
        for phrase, start in noun_pointers:
            if _phrase_is_licensed(segment, licensed_verbs, start):
                result.add(2, phrase, [phrase, *_variants(phrase)])
                named_by_upper_tier = True
        if named_by_upper_tier:
            continue
        for phrase, start in _plain_name_phrases(segment, licensed_verbs):
            if _phrase_is_licensed(segment, licensed_verbs, start):
                result.add(3, phrase, [phrase])
    return result


def resolve_folder_from_words(words: str, home: str | Path) -> FolderWordsResolution:
    """Resolve the person's words to one of their own folders, or say why not.

    Ordinary chat, casual mentions and statements resolve to "no_ask" and never speak: a
    resolution only happens when an asking-shaped clause works on a folder pointer. A
    plain object without a pointer noun binds only on an exact folder name and stays
    silent otherwise.
    """
    text = " ".join(str(words or "").split())[:_WORDS_MAX]
    home_real = _real_home(home)
    if not text or not home_real.is_dir():
        return FolderWordsResolution(status="no_ask")
    if not any(pattern.search(text) for pattern in _WORK_ASK_RES):
        return FolderWordsResolution(status="no_ask")

    pointers = _collect_pointer_tiers(text)
    if not pointers.any_ask_verb:
        # A work-verb shape appeared, but no clause asked with it: a statement.
        return FolderWordsResolution(status="no_ask")

    container: str | None = None
    container_match = _PATH_LIKE_RE.search(text) or _LOCATIVE_CONTAINER_RE.search(text)
    if container_match is not None:
        container = _CONTAINER_ALIASES.get(str(container_match.group("container") or "").lower(), container)

    # Refusals speak before any search, in tier order; an unlicensed phrase never
    # refuses anything (a make-verb complement is not an ask about that folder).
    for tier in pointers.tiers:
        for phrase, _variants_ in tier:
            kind = _phrase_refusal_kind(phrase)
            if kind:
                return FolderWordsResolution(
                    status="refused", message=_refusal_notice(kind, phrase)
                )
    if not pointers.any_pointer_phrase and not pointers.tiers[3]:
        return FolderWordsResolution(status="no_ask")

    folders = _candidate_folders(home_real, container)
    for tier_index, tier in enumerate(pointers.tiers):
        exact_only = tier_index == 3
        exact: dict[str, tuple[Path, str]] = {}
        near: dict[str, tuple[Path, str]] = {}
        for phrase, variants in tier:
            for folder_path, folder_name in folders:
                relation = _phrase_relation(phrase, folder_name)
                if not relation:
                    for variant in variants:
                        relation = _phrase_relation(variant, folder_name)
                        if relation:
                            break
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
        winners = exact_allowed or ({} if exact_only else near_allowed)
        if len(winners) == 1:
            path, name = next(iter(winners.values()))
            return FolderWordsResolution(status="found", path=str(path), name=name)
        if winners:
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
    if pointers.any_pointer_phrase:
        return FolderWordsResolution(
            status="none",
            message=(
                "I couldn't find a folder with that name in the places I may look: "
                "your home folder and your Projects, Code, Developer, Documents and Desktop folders."
            ),
        )
    return FolderWordsResolution(status="no_ask")


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
