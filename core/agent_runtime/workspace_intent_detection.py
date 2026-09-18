"""Shared, fuzzy-tolerant intent detection for workspace-scoped natural language.

Three independent runtime gates each tried to recognize "the user wants me to look at this
workspace" from raw text with hand-rolled, literal-string regexes: the audit trigger
(`core/agent_runtime/workspace_audit.py`), the general tool-intent gate
(`core/execution/planner.py`), and the project-overview classifier
(`core/runtime_execution_tools.py`). All three failed the same way, for the same structural
reason: a literal-string vocabulary has zero tolerance for a synonym or a typo nobody enumerated
by hand, and enumerating the next one is not a fix -- it just moves the miss to the next word.
Measured live 2026-08-04 (see `VOOL-DELIVERY/RUNTIME_UPGRADE_LEDGER.md`, findings A/B/C):

  * 18 common human synonyms for "audit" ('look over', 'check', 'rate', 'roast', ...) and every
    single-character typo on all 10 already-recognized verbs (48/48 combinations) missed the audit
    gate entirely.
  * A keyword allowlist with no vocabulary for "files"/"here"/"list"/"ls" meant "what files are
    here?" never reached a tool at all -- it fell to plain, ungrounded chat, and in one case the
    model hallucinated a fake tool-call marker in its own prose.
  * "what is this project about" / "give me an overview" never matched the literal shapes the
    project-overview classifier required, so the turn was misclassified as external web research
    and refused with "Live research is not available on this runtime" -- workspace tools were
    never even considered.

This module is the single generalizable answer: fuzzy/edit-distance vocabulary matching (typos
tolerated by construction, not by exhaustive enumeration) plus a small set of composable
predicates. Call sites wire these in on top of whatever structural checks they already had
correct -- this augments, it does not replace, the scope/verdict co-occurrence logic that already
protects `workspace_audit.py`'s conceptual-question exclusions.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Iterable

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]*")

# ---------------------------------------------------------------------------------------------
# Vocabulary. Every entry is a CANONICAL word or phrase; typos on any of them are tolerated by
# the fuzzy matcher below, not by adding the misspelling here.
# ---------------------------------------------------------------------------------------------

# The original 10 audit verbs this codebase already recognized. Fuzzy/edit-distance typo
# tolerance (Finding A: 48/48 single-character typos on these 10 missed the gate) is applied to
# THIS set only.
_AUDIT_VERB_WORDS_CORE = frozenset(
    {
        "audit", "inspect", "analyze", "analyse", "review", "examine", "scrutinize",
        "scrutinise", "assess", "evaluate", "critique",
    }
)
# Additional human synonyms measured missing live (Finding A, 2026-08-04). Matched EXACTLY (plus
# ordinary -s/-ed/-ing inflection), not fuzzily: several of these are short, common English words
# ('rate', 'store'; 'score', 'store') that sit only one edit away from ordinary vocabulary used in
# unrelated requests ("create"/"store" a file) -- fuzzy-matching them produced exactly that
# collision live (`create` ~ `rate`, `store` ~ `score`, both at a 0.8 similarity ratio) and must
# not be reintroduced by widening the cutoff instead of scoping which words it applies to.
# "once-over" (a single hyphenated token, per `_WORD_RE`) is the idiomatic noun form of the same
# request ("give this repo a once-over") -- adversary-measured missing 2026-08-04. Exact-only for
# the same reason as the rest of this set: it is short enough as a bare token to risk an
# unrelated fuzzy collision if it were opened up to edit-distance matching.
_AUDIT_VERB_WORDS_EXACT_ONLY = frozenset({"check", "rate", "grade", "score", "roast", "once-over"})
# "scan this project for oversized files" (measured 2026-09-07): exact-only, like the other short verbs.
AUDIT_VERB_WORDS = _AUDIT_VERB_WORDS_CORE | _AUDIT_VERB_WORDS_EXACT_ONLY | frozenset({"scan"})
AUDIT_VERB_PHRASES = (
    "look over", "look at", "take a look at", "check out", "go through", "dig into",
    # Plain-words inspection asks measured missing 2026-09-07 ("look through my project and tell me
    # which files are huge monoliths worth splitting").
    "look through", "comb through", "go over", "check over", "run through",
    "check for bugs", "check for issues", "find bugs", "find issues",
    "whats wrong with", "what's wrong with", "give me your thoughts on",
    # Unhyphenated form of the same idiom ("once over this code please") -- adversary-measured
    # missing 2026-08-04, alongside "once-over" above.
    "once over",
)

# Finding B: words missing from the folder-listing keyword allowlist. `dir` is a canonical
# alias for `directory` (not a typo of it), so it is listed directly rather than left to the
# fuzzy matcher.
_LISTING_STRONG_WORDS = frozenset({"files", "ls", "contents", "inventory"})
_LISTING_WEAK_WORDS = frozenset(
    {"list", "show", "here", "folder", "directory", "dir", "tree", "content"}
)
# Adversarial regression (2026-08-04): `_fuzzy_word_hit`'s edit-distance tolerance, applied
# blanket across every weak word, matched "where"/"there" to "here" ('where' -> delete one letter
# -> 'here'; ratio 0.8) -- a collision between two different, common English words, not a typo of
# one of them. "describe a workspace where thinking feels easy", "is there a good workspace app
# for note taking" and similar ordinary sentences fired the tool-intent gate purely because
# "where"/"there" fuzzy-matched "here". `_LISTING_WEAK_WORDS` stays the full canonical vocabulary
# (checked for EXACT/inflected matches); typo-tolerant fuzzy matching is restricted to the subset
# below, which excludes the two short, collision-prone words.
_LISTING_WEAK_WORDS_FUZZY = frozenset({"list", "show", "folder", "directory", "tree", "content"})
# The listing STRONG words' typo neighborhoods are full of different, common English words at
# the same 0.8 cutoff -- "miles"/"piles"/"tiles"/"isles" vs "files", "continents"/"contends"/
# "contented" vs "contents", "inventors" vs "inventory" (measured live: "How many continents
# are there?" inside an eight-question quiz routed the whole turn to the tool-intent lane,
# which died as "I wasn't able to turn that into a completed action" while the model lane was
# down). The same collision class the weak-word split above was cut for: a different real word
# is not a typo. Strong words stay exact-only; a misspelling still reaches the listing lane
# through the weak branch when a weak word or demonstrative accompanies it ("list the contetns
# here"), and the exact forms are untouched.
_LISTING_STRONG_WORDS_FUZZY: frozenset[str] = frozenset()
_WORKSPACE_DEMONSTRATIVE_RE = re.compile(
    r"\bthis\s+(?:folder|directory|dir|repo|repository|workspace|project)\b|\bhere\b",
    re.IGNORECASE,
)

# Finding C: words missing from the project-overview classifier.
OVERVIEW_WORDS = frozenset({"overview", "purpose", "summarize", "summarise", "describe", "explain"})
# Self-referential: the phrase itself already names "this"/"it" as the subject, so matching the
# phrase ALONE (with no separate workspace noun anywhere in the message) is still specifically
# about the bound workspace, not about anything in the world named "this".
OVERVIEW_PHRASES = (
    "what does this do", "what is this for", "what's this for", "whats this for",
    "what is this about", "what's it about", "whats it about", "what is this project about",
    "what's this project about", "give me an overview", "explain what this",
)
# Casual idioms measured missing live (2026-08-04 QA pass): "yo what's up with this repo" and
# "what's the deal with this codebase" carry no OVERVIEW_WORDS hit and no demonstrative adjacent to
# about/for, so without these they never reached the tool lane at all. UNLIKE `OVERVIEW_PHRASES`
# above, these idioms are generic ("what's up with you today", "what's the deal with the traffic")
# and say nothing about a workspace by themselves -- they may only fire the gate together with an
# explicit demonstrative-qualified workspace noun ELSEWHERE in the message, never alone.
OVERVIEW_PHRASES_NEEDS_NOUN = ("what's up with", "whats up with", "the deal with")
_WORKSPACE_TARGET_RE = re.compile(
    r"\b(?:repo|repository|workspace|codebase|(?:this|the|my|our|current)\s+project)\b",
    re.IGNORECASE,
)
# Fuzzy/typo-tolerant counterpart to `_WORKSPACE_TARGET_RE` (Finding C generalization, 2026-08-04):
# a literal-word regex missed "waht is this projct abuot" outright, because "projct" never matches
# `\bproject\b`. Checked token-by-token via `_fuzzy_word_hit` alongside the exact regex, never
# instead of it. UNLIKE `_WORKSPACE_TARGET_RE`, every noun here requires an immediately preceding
# demonstrative/possessive (this/the/my/our/current) -- measured live: "describe a workspace where
# thinking feels easy" is a creative-writing prompt, not a question about the BOUND workspace, and
# a bare noun match (the shape `_WORKSPACE_TARGET_RE` already allows for repo/workspace/codebase)
# fired on it. Every one of the six real overview phrasings this exists for ("what is THIS project
# about", "explain THIS codebase", ...) already carries the demonstrative, so requiring it costs
# nothing real and removes the false-positive class.
_WORKSPACE_NOUN_WORDS = frozenset({"project", "repo", "repository", "workspace", "codebase"})
_DEMONSTRATIVE_WORDS = frozenset({"this", "the", "my", "our", "current"})
# 'for'/'about' are far too common to be fuzzy-matched as standalone overview words (they show up
# in unrelated sentences constantly), so they only count immediately adjacent to a DEMONSTRATIVE-
# qualified workspace noun -- checked as token adjacency below, tolerant of a typo on EITHER side
# ("abuot" as well as "projct"), which a fixed regex can never be.
_ABOUT_FOR_WORDS = frozenset({"about", "for"})


def _bound_workspace_noun_positions(tokens: list[str]) -> list[int]:
    """Index of every workspace-noun token immediately preceded by a demonstrative/possessive."""
    positions = []
    for index, token in enumerate(tokens):
        if index == 0:
            continue
        if tokens[index - 1] not in _DEMONSTRATIVE_WORDS:
            continue
        if token in _WORKSPACE_NOUN_WORDS or _fuzzy_word_hit(token, _WORKSPACE_NOUN_WORDS):
            positions.append(index)
    return positions

# Issue 1 fix (adversarial regression, 2026-08-04): a scope/target signal `contains_audit_verb`
# must co-occur with before it counts toward the fail-open OR in `plausibly_about_bound_workspace`.
# Mirrors `workspace_audit._AUDIT_SCOPE_RE` deliberately -- that regex is the already-correct
# pattern this module's own docstring says call sites should layer on top of, and every noun in it
# ("project"/"workspace"/"folder"/"dir(ectory)" included) is safe to reuse HERE specifically
# because this gate only decides whether to offer tools, not whether to run the real audit
# pipeline (`looks_like_code_audit_request` keeps its own, narrower demonstrative-scoped path for
# that reason -- see `test_bare_verb_plus_this_project_still_needs_more_evidence`). Confirmed live:
# none of the 10 measured-bad phrasings ("assess my resume for the marketing role", "critique my
# poem about the ocean", "analyze my dream from last night", "inspect this used car before I buy
# it", "evaluate my job offer, should I take it", "grade my exam answer please", "score this wine
# for me", "examine this rash on my arm, is it serious", "review this movie for me", "can you rate
# my essay about dogs") name code, a repo, a project, a workspace, a folder or a directory anywhere
# -- so requiring one of these nouns (or a real code-like file target) closes the class without
# touching "roast this workspace" or any of the existing audit-verb-synonym regression coverage,
# none of which relies on a bare verb with NOTHING pointing at the workspace at all.
_WORKSPACE_SCOPE_RE = re.compile(
    r"\b(?:code|codebase|implementation|sources?|source\s+code|repo(?:sitory)?|project|workspace|folder|dir(?:ectory)?)\b",
    re.IGNORECASE,
)
# A dotted token that names a real code/text file ("bugs.py", "deploy.log") is just as valid a
# scope signal as a bare noun -- "audit api/main.py" names no "code"/"project"/"repo" word at all
# but is unambiguously about the workspace. Deliberately a lightweight local check (not a call into
# `workspace_audit.audit_target_in`, which would import back into this module and deadlock -- see
# the module docstring for the existing lazy-import pattern this project already uses for the
# reverse direction).
_DOTTED_TOKEN_RE = re.compile(r"\b[\w][\w\-./]*\.[A-Za-z][A-Za-z0-9]{0,8}\b")


def _mentions_code_like_file(text: str) -> bool:
    return any(
        has_code_like_extension(match.group(0)) for match in _DOTTED_TOKEN_RE.finditer(str(text or ""))
    )


def _audit_verb_has_workspace_scope(text: str) -> bool:
    """Whether an audit-shaped verb in `text` is actually pointed at the workspace/code, rather
    than merely present in a sentence about something else entirely (a resume, a poem, a car, a
    rash). A bare verb alone is never sufficient -- see the block comment above."""
    return bool(_WORKSPACE_SCOPE_RE.search(str(text or ""))) or _mentions_code_like_file(text)


# Off-topic small talk that must never be dragged into the workspace tool lane just because a
# workspace happens to be bound. Deliberately short -- the real boundary is that ordinary
# conversation carries none of the vocabulary above, not that this list is exhaustive.
_CLEARLY_UNRELATED_MARKERS = (
    "weather", "joke", "how are you", "your name", "sing a song", "write a poem",
    "recipe for", "translate ", "who is ", "what year", "capital of", "how old are you",
)

# Reasonable code/text-file extensions. Deliberately excludes document/media/spreadsheet
# extensions (com, pdf, doc, docx, csv, xlsx, jpg, png, mp4, ...) so a dotted-looking token in
# ordinary prose ("rate this restaurant.com review") cannot be mistaken for an audit target
# (Finding A's 4 measured false positives).
# `log`/`env` were an undisclosed omission (adversary-measured 2026-08-04: "audit this deploy.log
# for suspicious entries", "review error.log please" both missed the gate entirely, silently, with
# no decision recorded either way). Decided explicitly here, not left as a gap:
#   * `env` is unambiguous -- a `.env` file is a runtime-config/secrets artifact in every codebase
#     that has one; nobody names a personal document `notes.env`. Treated like `py`/`json`: a bare
#     verb + target is already enough, no dual-use gate.
#   * `log` is a common real-world audit target (deploy logs, error logs, access logs) and is
#     listed here as unambiguous too, on the same reasoning: unlike `.md`/`.json`/`.yaml`, a `.log`
#     file is overwhelmingly a technical/operational artifact, not an ordinary personal-document
#     naming convention (a diary or journal is `.txt`/`.md`, essentially never `.log`).
#   * `conf`/`ini` were an undisclosed omission (adversary-measured 2026-08-04: "audit server.conf
#     for misconfigurations", "check config.ini for problems" both missed the gate entirely,
#     silently, with no decision recorded either way). Decided explicitly here: both are
#     overwhelmingly technical configuration-file extensions in real projects (`nginx.conf`,
#     `redis.conf`, `setup.cfg`-style `.ini` files) and essentially never an ordinary personal-
#     document naming convention -- nobody names a diary or a shopping list `notes.conf` or
#     `notes.ini`. Same reasoning as `log`/`env` above: unambiguous, so a bare verb + target is
#     already enough, no dual-use gate.
CODE_TEXT_EXTENSIONS = frozenset(
    {
        "py", "js", "ts", "jsx", "tsx", "go", "rs", "java", "c", "cpp", "h", "hpp", "rb", "php",
        "sh", "bash", "md", "json", "yaml", "yml", "toml", "sql", "html", "css", "swift", "kt",
        "log", "env", "conf", "ini", "txt",
    }
)
# Dual-use subset of the above: these extensions are just as often an ordinary personal document
# as a project file -- "my resume.md", "my playlist.json", "the theme.css". A 2026-08-04 QA pass
# measured 7 concrete false positives ('rate my playlist.json', 'review my notes.md', 'check my
# resume.md', 'grade my essay.md', 'score the recipe.yaml', 'check the theme.css', 'rate my
# portfolio.html') firing the full audit pipeline purely because SOME judgment verb plus one of
# these extensions is not, on its own, distinguishable from an ordinary document question.
# `looks_like_code_audit_request` requires extra evidence (the unambiguous word "audit" itself, or
# explicit bug/verdict language) before opening the gate on one of these -- never for the
# unambiguous source extensions above, where a bare verb + target is already enough.
#
# `txt` was an undisclosed omission alongside `conf`/`ini` (adversary-measured 2026-08-04: "audit
# requirements.txt", "audit requirements.txt for outdated packages" both missed the gate entirely).
# Unlike `.conf`/`.ini`, `.txt` is genuinely dual-use: `requirements.txt`/`CHANGELOG.txt`/
# `README.txt` are extremely common real project files, but a bare `notes.txt` or `todo.txt` is
# just as plausibly a personal document -- the extension alone does not tell them apart the way
# `.conf`/`.ini` do. So `txt` goes here, in `CODE_TEXT_EXTENSIONS` too (`has_code_like_extension`
# must still recognize it as a target at all) but gated the same as `.md`/`.json`: the explicit
# word "audit" or verdict language is required before the gate opens on it.
DUAL_USE_TEXT_EXTENSIONS = frozenset({"md", "json", "yaml", "yml", "toml", "html", "css", "txt"})

_FUZZY_CUTOFF = 0.8
_MIN_FUZZY_LEN = 4


def _tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(str(text or ""))]


def _candidate_stems(token: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Plausible base forms of an inflected verb: 'auditing' -> exact 'audit', etc.

    Returns `(exact_candidates, fuzzy_candidates)`. A fuzzy edit-distance cutoff tight enough to
    reject unrelated words ('cat' vs. 'check') is also too tight for 'auditing' vs. 'audit' (ratio
    0.77) or 'examining' vs. 'examine' (0.75) -- exactly the ordinary -ing/-ed/-s/-es inflections a
    real request uses constantly ("I'm auditing this", "already reviewed", "evaluates fine").
    Stripping a suffix and comparing again, rather than only fuzzy-matching the raw token, is what
    lets one canonical vocabulary entry cover its whole verb family without enumerating every
    inflected form by hand.

    Reconstructed forms that ADD a letter back ('evaluat' + 'e' -> 'evaluate', 'analys' + 'y' ->
    'analysy'... i.e. the -ing/-ied repairs) are exact-match only: fuzzy-comparing a reconstructed
    string that never appeared in the input risks matching an unrelated word by coincidence
    ('read' -> 'reade' -> fuzzy-close to 'grade'). Plain suffix-stripped stems (no letters added)
    are safe to fuzzy-match because they are still substrings of what the user actually typed.
    """
    exact = [token]
    fuzzy = [token]
    if token.endswith("ing") and len(token) > 5:
        stem = token[:-3]
        fuzzy.append(stem)
        exact.append(stem)
        exact.append(stem + "e")  # evaluat+ing -> evaluate
    if token.endswith("ied") and len(token) > 5:
        exact.append(token[:-3] + "y")
    if token.endswith("ed") and len(token) > 4:
        stem = token[:-2]
        fuzzy.append(stem)
        exact.append(stem)
        exact.append(token[:-1])  # critiqued -> critique
    if token.endswith("es") and len(token) > 4:
        stem = token[:-2]
        fuzzy.append(stem)
        exact.append(stem)
    if token.endswith("s") and len(token) > 3:
        stem = token[:-1]
        fuzzy.append(stem)
        exact.append(stem)
    return tuple(dict.fromkeys(exact)), tuple(dict.fromkeys(fuzzy))


def _fuzzy_word_hit(
    token: str,
    vocabulary: frozenset[str],
    *,
    fuzzy_vocabulary: frozenset[str] | None = None,
) -> bool:
    """Exact/inflection match against the full `vocabulary`; edit-distance match against the
    (usually smaller) `fuzzy_vocabulary` -- typo tolerance is opt-in per word, not automatic for
    every vocabulary entry, because some short/common words collide with ordinary English at this
    cutoff (see `_AUDIT_VERB_WORDS_EXACT_ONLY`)."""
    exact_candidates, fuzzy_candidates = _candidate_stems(token.lower())
    if any(candidate in vocabulary for candidate in exact_candidates):
        return True
    fuzzy_pool = tuple(fuzzy_vocabulary if fuzzy_vocabulary is not None else vocabulary)
    if not fuzzy_pool:
        return False
    return any(
        len(candidate) >= _MIN_FUZZY_LEN
        and difflib.get_close_matches(candidate, fuzzy_pool, n=1, cutoff=_FUZZY_CUTOFF)
        for candidate in fuzzy_candidates
    )


_PHRASE_WORD_TYPO_CUTOFF = 0.75


def _word_pair_is_typo(candidate_word: str, phrase_word: str, *, cutoff: float = _PHRASE_WORD_TYPO_CUTOFF) -> bool:
    """Whether two tokens are plausibly one typo apart -- not two different real words.

    Gated on BOTH sides being long enough to fuzz safely: a short function word ('do', 'is',
    'for') collides with an unrelated short word at any reasonable ratio cutoff ('dog' vs. 'do' is
    already 0.8) and must match exactly instead of fuzzily.
    """
    if candidate_word == phrase_word:
        return True
    if len(candidate_word) < _MIN_FUZZY_LEN or len(phrase_word) < _MIN_FUZZY_LEN:
        return False
    return difflib.SequenceMatcher(None, candidate_word, phrase_word).ratio() >= cutoff


def _fuzzy_phrase_hit(normalized_text: str, phrase: str, *, max_word_typos: int = 1) -> bool:
    """Whether some contiguous word-window of `normalized_text` plausibly says `phrase`.

    Compared WORD BY WORD against the phrase (tolerating at most `max_word_typos` mismatched
    word(s)), not by scoring the whole window as one string. A whole-string ratio conflates "one
    typo'd word inside an otherwise exact phrase" with "a different phrase that happens to share a
    lot of characters": measured live 2026-08-04, "code for bugs" scored 0.81 against the
    vocabulary phrase "check for bugs" (well past the old 0.8 cutoff) even though "code" and
    "check" are different words, not a typo of each other -- firing the full audit pipeline on "I
    saw the code for bugs.py yesterday". The same whole-string collision let "what does this DOG
    do" match "what does this DO" (ratio 0.97) and attempt a tool call on ordinary chat. Comparing
    per word, gated by `_word_pair_is_typo`, rejects both: "code"/"check" fails the typo check
    outright (ratio 0.44), and "dog"/"do" is refused on length alone before any ratio is computed,
    because "do" is too short to fuzz safely. "check for bugz" still matches "check for bugs" --
    the one mismatched word ("bugz"/"bugs") is long enough to fuzz and clears the cutoff.
    """
    phrase_words = phrase.split()
    window = len(phrase_words)
    if window <= 1:
        return False
    words = normalized_text.split()
    for start in range(0, max(0, len(words) - window + 1)):
        candidate_words = words[start : start + window]
        typos = 0
        matched = True
        for candidate_word, phrase_word in zip(candidate_words, phrase_words, strict=False):
            if candidate_word == phrase_word:
                continue
            if not _word_pair_is_typo(candidate_word, phrase_word):
                matched = False
                break
            typos += 1
            if typos > max_word_typos:
                matched = False
                break
        if matched:
            return True
    return False


def _phrase_present(phrase: str, normalized: str) -> bool:
    """Word-boundary-safe containment of `phrase` inside the space-joined token string
    `normalized`.

    A raw `phrase in normalized` substring check is not word-boundary aware: measured live
    2026-08-04, the vocabulary phrase "what does this do" was found INSIDE "what does this dog
    want for dinner" purely because "dog" starts with the three characters "do" followed by the
    normal word-separating space check missing (`...this do` + `g want...` matches the phrase
    string as characters, ending right before the "g"). Wrapping both sides with boundary spaces
    makes "do" require a following space, which "dog" never has.
    """
    return f" {phrase} " in f" {normalized} "


def fuzzy_vocabulary_hit(
    text: str,
    *,
    words: frozenset[str] = frozenset(),
    phrases: Iterable[str] = (),
    fuzzy_words: frozenset[str] | None = None,
) -> bool:
    """True when `text` plausibly contains one of `words`/`phrases`, typos tolerated by
    construction -- not because the exact misspelling was enumerated. `fuzzy_words`, when given,
    restricts EDIT-DISTANCE matching to that subset of `words` (see `_fuzzy_word_hit`)."""
    tokens = _tokens(text)
    if words and any(
        _fuzzy_word_hit(token, words, fuzzy_vocabulary=fuzzy_words) for token in tokens
    ):
        return True
    if phrases:
        normalized = " ".join(tokens)
        for phrase in phrases:
            if _phrase_present(phrase, normalized):
                return True
            if _fuzzy_phrase_hit(normalized, phrase):
                return True
    return False


def contains_audit_verb(text: str) -> bool:
    """A request to judge/critique code, tolerant of the 18 measured missing synonyms and any
    single-typo variant of any of the 10 originally-recognized verbs (Finding A)."""
    return fuzzy_vocabulary_hit(
        text,
        words=AUDIT_VERB_WORDS,
        phrases=AUDIT_VERB_PHRASES,
        fuzzy_words=_AUDIT_VERB_WORDS_CORE,
    )


def contains_listing_intent(text: str) -> bool:
    """A request to see what files/folders exist (Finding B).

    Strong words ('files', 'ls', 'contents', 'inventory') are unambiguous on their own. Weak
    words ('list', 'show', 'here', 'folder', ...) are common in ordinary conversation, so they
    only count alongside a demonstrative pointing at the current location -- "list files here",
    "what's in this folder" -- not "put it on my list" or "show me a joke".

    Deliberately checked against `_WORKSPACE_DEMONSTRATIVE_RE` only, never the bare-noun
    `_WORKSPACE_TARGET_RE` (2026-08-04 QA regression): "is there a good workspace app for note
    taking" and "where should I put my workspace desk" name "workspace" with no demonstrative at
    all, and would fire on the bare noun alone paired with an ordinary word like "show".
    """
    tokens = _tokens(text)
    if any(
        _fuzzy_word_hit(
            token, _LISTING_STRONG_WORDS, fuzzy_vocabulary=_LISTING_STRONG_WORDS_FUZZY
        )
        for token in tokens
    ):
        return True
    return bool(
        any(
            _fuzzy_word_hit(token, _LISTING_WEAK_WORDS, fuzzy_vocabulary=_LISTING_WEAK_WORDS_FUZZY)
            for token in tokens
        )
        and _WORKSPACE_DEMONSTRATIVE_RE.search(text)
    )


def _mentions_workspace_noun(tokens: list[str]) -> bool:
    """A demonstrative-qualified workspace noun -- "this project", "my codebase" -- never a bare
    one. A bare "workspace"/"repo" is common in ordinary and creative sentences that have nothing
    to do with the bound workspace ("describe a workspace where thinking feels easy")."""
    return bool(_bound_workspace_noun_positions(tokens))


def _workspace_noun_adjacent_to_about_or_for(tokens: list[str]) -> bool:
    """Token-adjacency version of "repo FOR" / "project ABOUT", tolerant of a typo on either
    side -- a fixed regex matches neither half of "waht is this projct abuot". Only counts for a
    demonstrative-qualified noun, for the same reason `_mentions_workspace_noun` requires one.

    The "for"/"about" neighbor must sit in the LAST two tokens of the message (2026-08-04 QA
    regression): "project FOR" and "workspace ABOUT" are ordinary English that keeps going --
    "my project FOR art class is due tomorrow" -- and "for"/"about" immediately follows the noun
    there exactly as it does in "what's this project for". What tells them apart is not the
    adjacency, which both have, but whether anything meaningful follows: an overview QUESTION ends
    at "for"/"about" (plus at most one trailing word like "exactly"/"please"); a description of
    something ELSE keeps naming what the project is for.
    """
    tail_start = max(0, len(tokens) - 2)
    for index in _bound_workspace_noun_positions(tokens):
        neighbors = [
            (position, tokens[position])
            for position in (index - 1, index + 1)
            if 0 <= position < len(tokens)
        ]
        for position, neighbor in neighbors:
            if position < tail_start:
                continue
            if neighbor in _ABOUT_FOR_WORDS or _fuzzy_word_hit(neighbor, _ABOUT_FOR_WORDS):
                return True
    return False


def contains_overview_intent(text: str) -> bool:
    """A request to understand what a project/repo/codebase is or does (Finding C).

    'about'/'for'/'explain' are common English words on their own, so overview words only count
    when the message also names the workspace as their subject -- "what is this project about"
    triggers, "tell me about your day" does not. Both halves are typo-tolerant (Finding C
    generalization): "waht is this projct abuot" triggers exactly like the correctly spelled form.

    `OVERVIEW_PHRASES` (e.g. "what does this do") are self-referential -- the phrase itself already
    names "this"/"it" as the subject, so matching alone is enough. `OVERVIEW_PHRASES_NEEDS_NOUN`
    (casual idioms like "what's up with") are generic and say nothing about a workspace by
    themselves -- "what's up with you today" must NOT trigger just because the idiom is present, so
    that set only counts alongside a real demonstrative-qualified workspace noun elsewhere in the
    message (2026-08-04 QA regression class).
    """
    tokens = _tokens(text)
    if _workspace_noun_adjacent_to_about_or_for(tokens):
        return True
    all_phrases = OVERVIEW_PHRASES + OVERVIEW_PHRASES_NEEDS_NOUN
    if not fuzzy_vocabulary_hit(text, words=OVERVIEW_WORDS, phrases=all_phrases):
        return False
    if _mentions_workspace_noun(tokens):
        return True
    normalized = " ".join(tokens)
    return any(_phrase_present(phrase, normalized) for phrase in OVERVIEW_PHRASES)


def has_code_like_extension(path: str) -> bool:
    stem = str(path or "").strip()
    if "." not in stem:
        return False
    extension = stem.rsplit(".", 1)[-1].strip().lower()
    return extension in CODE_TEXT_EXTENSIONS


def looks_clearly_unrelated_to_workspace(text: str) -> bool:
    lowered = f" {' '.join(str(text or '').lower().split())} "
    return any(marker in lowered for marker in _CLEARLY_UNRELATED_MARKERS)


def plausibly_about_bound_workspace(text: str) -> bool:
    """Fail-open signal for the tool-intent gate: with a workspace bound, is this message worth
    offering real tools for, rather than defaulting to a tools-less chat lane?

    Deliberately generous in one direction only: this is not "is this definitely a workspace
    question", it is "is there no clear reason to believe it is not one". A model handed tools it
    does not need simply answers in prose; a model never offered them cannot act however clearly
    it was asked (Findings B and C, 2026-08-04). Ordinary small talk is excluded explicitly so a
    bound workspace does not turn every chat message into an attempted tool call.

    `contains_audit_verb` alone is deliberately NOT sufficient (Issue 1 adversarial regression,
    2026-08-04): a bare judgment verb ("assess", "critique", "grade", "score", ...) appears
    constantly in ordinary requests that have nothing to do with a workspace -- "assess my resume",
    "grade my exam answer", "rate this wine". It only counts toward this OR when it co-occurs with
    an actual workspace-scope signal (`_audit_verb_has_workspace_scope`): a code/project/repo noun,
    or a named file with a recognized extension. `contains_listing_intent` and
    `contains_overview_intent` already carry their own scope requirements internally and need no
    such pairing here.
    """
    if looks_clearly_unrelated_to_workspace(text):
        return False
    return (
        (contains_audit_verb(text) and _audit_verb_has_workspace_scope(text))
        or contains_listing_intent(text)
        or contains_overview_intent(text)
    )
