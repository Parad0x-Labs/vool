"""A small project whose file set the operator ENUMERATED authorizes those files and nothing else.

A QA drive asked, verbatim::

    Create a small Python project called StormWatch with a README and one app.py file.
    Do not run tests.

and got a reply table naming ``StormWatch``, no ``app.py``, no ``README.md``, a planned
``test_app.py`` the request had ruled out, and the sentence "I will keep this turn conversational
and will not propose or run an action." three times over. Three separate readings of one sentence
were wrong at once:

1. **"Do not run tests" disabled the whole turn.** ``intent_claims.action_policy_for_text`` read any
   ``do not <verb>`` as a turn-wide ban, so a constraint on ONE operation ("running") took the
   build lane, the write lane and the approval card with it. That half is fixed in
   ``intent_claims``; this module owns the other half.
2. **The named file set never became mutation authority.** ``mutation_scope`` seals a request that
   names its files, but steps aside the moment the request also names a multi-file deliverable --
   "a small Python **project**" -- or asks for a file it did not name -- "with a **README**". Both
   fired here, so the seal was dropped, a model was asked to "list the files for this project" (a
   prompt that instructs it to include a test module), and ``test_app.py`` came back.
3. **"one app.py file" and "Do not run tests" were read as prose.** They are the operator bounding
   their own request, and nothing looked at them.

The rule this module adds, stated once:

    **A build request that BOUNDS its file set -- "only", "exactly", "one app.py file", "no
    tests" -- means the files it enumerates are the whole set, even when it also says "project".**

The noun then names the CONTAINER; the enumeration names the contents. Without a bound, nothing
changes: "Create a small Python project with app.py, README.md and tests" still reaches the full
scaffolder, because "and tests" hands the choice of files to the model and the operator never took
it back. That distinction is the entire safety margin here, and it is why an unbounded request is
untouched by this module.

A file set is complete two ways, and both are read off the sentence rather than off a keyword:

* **A bound the operator stated** -- "only", "exactly", "one app.py file", "no tests". A
  restricting word counts only where it is restricting THE FILE LIST: "with only a README and
  app.py" bounds the set, "with app.py, I only have ten minutes" bounds the afternoon, and a
  marker list keyed on the word alone cannot tell them apart.
* **A named container whose contents are listed right there** -- "create project called Storm
  Watch with readme and app.py". Bounded by grammar instead of vocabulary: the tail after the name
  has to be a file list and nothing else, which is what keeps a whole To-Do app that mentions
  `tasks.json` three sentences later out of it.

Three readings the sentence needs and the older lanes do not supply:

* **A README is a named file.** ``README.md`` is its name -- an operator who writes "with a README"
  named a file as concretely as one who typed it out. It stays an *unnamed extra* (and so still
  widens the scope) when the request did NOT bound its set, because "create notes.txt with hello,
  and a readme" really did leave the choice open.
* **The container's name.** "folder WeatherBug", "project CalcLite:", "project called Storm Watch"
  -- ``extract_requested_builder_root`` reads "called X" only, so four of five live phrasings built
  in a digest-named ``generated/`` directory the operator never asked for.
* **Dictated and clipped shapes.** "app dot py" is ``app.py`` said out loud, "readme + app only"
  is a file list with a plus sign, and a bare "app" in a Python request is ``app.py``.

Restraint is inherited, not re-implemented: deliberation, opt-out and the imperative test all come
from ``build_request_intent``, the module that exists because five copies of that vocabulary
disagreed 31 times out of 116. A question about a bounded project is still a question.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from core.agent_runtime import build_request_intent
from core.agent_runtime.builder.named_file_build import (
    _QUESTION_LEAD_RE,
    _asks_in_another_way,
    named_build_files,
    named_file_build_root,
)

_EXT_ALT = "|".join(build_request_intent.SOURCE_EXTENSIONS)

# ---------------------------------------------------------------------------
# Dictated / clipped shapes
# ---------------------------------------------------------------------------

# "app dot py" is a filename said out loud. Bounded to the known source extensions so ordinary
# prose ("version 3 dot 1", "dot product") cannot be turned into a path.
_SPOKEN_EXTENSION_RE = re.compile(
    r"\b([A-Za-z][\w-]*)\s+dot\s+(" + _EXT_ALT + r")\b", re.IGNORECASE
)
# "readme + app" is a file list. The plus reads as "and" and nothing else.
_PLUS_SEPARATOR_RE = re.compile(r"\s+\+\s+")


def normalise_request(text: str) -> str:
    """The request with dictated and clipped file shapes written out.

    Applied before every other read in this module, so "app dot py" and "app.py" are one request
    with one answer rather than two lanes with two behaviours. Markdown backticks come off here
    for the same reason: `` `bot.py` `` is how chat names a file, and the code span is
    presentation, not part of the path -- leaving it on made every backtick-wrapped filename
    invisible to the enumeration (measured live 2026-09-18: a request that listed `` `bot.py` ``,
    `` `requirements.txt` `` and `` `README.md` `` resolved no plan and fell to the application
    template).
    """
    body = " ".join(str(text or "").split()).replace("`", "")
    body = _SPOKEN_EXTENSION_RE.sub(lambda m: f"{m.group(1)}.{m.group(2).lower()}", body)
    return _PLUS_SEPARATOR_RE.sub(" and ", body)


# ---------------------------------------------------------------------------
# The bound the operator put on their own request
# ---------------------------------------------------------------------------

# A restricting word, which is only a bound when it is restricting THE FILE LIST. "only" is a
# common English intensifier -- "create a project called Foo with app.py, I only have ten minutes"
# restricts the operator's afternoon, not their file set -- so this word alone decides nothing and
# `_restriction_binds_the_file_list` checks what it sits next to.
#
# "just" is deliberately absent even from that. It is a politeness filler far more often than a
# restriction ("just create app.py and a test module"), and "just plan it" is already an opt-out
# elsewhere -- reading it as a bound would seal requests that never asked to be sealed.
_RESTRICTING_WORD_RE = re.compile(r"^(?:only|exactly|solely|purely)[,.;:!]?$", re.IGNORECASE)
# The bounds that carry their own scope: they name what is excluded, so they need nothing adjacent.
_SELF_CONTAINED_BOUND_RE = re.compile(
    r"\bnothing\s+else\b|\bno\s+(?:other|extra|additional|further)\s+files?\b", re.IGNORECASE
)
# What may sit between a restricting word and the file it restricts without breaking the link.
# Articles and list glue only: "with only a README" is one phrase, and "app.py, I only have ten
# minutes" is two -- the pronoun is the boundary, and a plain distance window cannot see it.
_RESTRICTION_FILLER_RE = re.compile(
    r"^(?:a|an|the|some|one|two|simple|basic|small|short|plain|python|py|and|plus|&)[,.;:]?$",
    re.IGNORECASE,
)
# How far a restricting word may sit from the file it restricts, filler aside.
_RESTRICTION_WINDOW = 2
# The operator ruling tests out. Covers the file ("no test file") and the run ("do not run tests"):
# either one says the model does not get to add a test module of its own choosing.
_NO_TESTS_RE = re.compile(
    r"\bno\s+tests?\b"
    r"|\bno\s+test\s+(?:files?|modules?|suite)\b"
    r"|\bwithout\s+(?:any\s+)?tests?\b"
    r"|\b(?:do\s+not|do\s+nt|don'?t|dont)\s+(?:run|write|add|create|include|generate|make)\s+"
    r"(?:any\s+|the\s+)?tests?\b"
    r"|\bskip\s+(?:the\s+)?tests?\b",
    re.IGNORECASE,
)
# "one app.py file", "a single main.py file" -- a counted deliverable is a bounded one.
_COUNTED_FILE_RE = re.compile(
    r"\b(?:one|a\s+single|1)\s+[\w./-]*\.(?:" + _EXT_ALT + r")\s+file\b", re.IGNORECASE
)

# Files the operator asked for but did NOT name, and whose name this runtime cannot derive. A
# README has one conventional name; "tests", "docs" and "examples" do not -- asking for those hands
# the file list back to the model, which is a widening the operator performed themselves.
#
# Deliberately the same shape as ``mutation_scope._UNNAMED_EXTRAS_RE`` minus `readme`: that module
# owns the unbounded case and must keep treating a bare readme as an extra.
_OPEN_ENDED_EXTRAS_RE = re.compile(
    r"\b(?:and|with|plus|including|include|also)\s+"
    r"(?:a\s+|an\s+|the\s+|some\s+|basic\s+|simple\s+|short\s+|unit\s+|proper\s+)*"
    r"(?:tests?|test\s+suite|documentation|docs|examples?|scaffold(?:ing)?)\b",
    re.IGNORECASE,
)


def _restriction_binds_the_file_list(body: str, *, workspace_root: str = "") -> bool:
    """True when a restricting word is sitting on the file list rather than somewhere else."""
    tokens = list(re.finditer(r"\S+", body))
    mentions = {start for start, _path in _file_mentions(body, workspace_root=workspace_root)}
    if not mentions:
        return False
    file_indices = {
        index
        for index, token in enumerate(tokens)
        if any(token.start() <= start < token.end() for start in mentions)
    }

    def binds(index: int) -> bool:
        for other in file_indices:
            gap = range(min(index, other) + 1, max(index, other))
            if len(gap) < _RESTRICTION_WINDOW and all(
                _RESTRICTION_FILLER_RE.match(tokens[between].group(0)) for between in gap
            ):
                return True
        return False

    return any(
        _RESTRICTING_WORD_RE.match(token.group(0)) and binds(index)
        for index, token in enumerate(tokens)
    )


def bounds_its_file_set(text: str, *, workspace_root: str = "") -> bool:
    """True when the request states that the files it lists are the whole list."""
    body = normalise_request(text)
    return bool(
        _SELF_CONTAINED_BOUND_RE.search(body)
        or _NO_TESTS_RE.search(body)
        or _COUNTED_FILE_RE.search(body)
        or _CREATE_COLON_LIST_RE.search(body)
        or _restriction_binds_the_file_list(body, workspace_root=workspace_root)
    )


def forbids_tests(text: str) -> bool:
    """True when the request rules tests out -- the file, the run, or both."""
    return bool(_NO_TESTS_RE.search(normalise_request(text)))


def asks_for_unnamed_extras(text: str) -> bool:
    """True when the request asks for files it neither named nor gave this runtime a name for."""
    body = normalise_request(text)
    for match in _OPEN_ENDED_EXTRAS_RE.finditer(body):
        # "...and app.py, no tests" is not a request for tests. A negation immediately before the
        # noun flips its meaning, and reading it as an ask is how "no tests" widened the scope.
        head = body[: match.start()]
        if re.search(r"\b(?:no|not|without|never)\s*$", head, re.IGNORECASE):
            continue
        if _NO_TESTS_RE.search(body) and re.search(r"tests?\b", match.group(0), re.IGNORECASE):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# The container the files go in
# ---------------------------------------------------------------------------

_CONTAINER_NOUNS = (
    "project", "folder", "directory", "dir", "package", "repo", "repository",
)
_CONTAINER_NOUN_RE = re.compile(r"\b(?:" + "|".join(_CONTAINER_NOUNS) + r")\b", re.IGNORECASE)
# "project called X", "folder named X", "app directory titled X".
_NAMED_CONTAINER_RE = re.compile(
    r"\b(?:" + "|".join(_CONTAINER_NOUNS) + r"|app|application)\s+(?:called|named|titled)\s+",
    re.IGNORECASE,
)
# "folder WeatherBug", "project CalcLite:" -- the name sits straight after the noun.
_BARE_CONTAINER_RE = re.compile(r"\b(?:" + "|".join(_CONTAINER_NOUNS) + r")\s+", re.IGNORECASE)

# Words that are never a project name, so reading one as a name is reading the sentence wrong.
# "folder, readme + app only" names no folder, and inventing `readme/` from it would be exactly the
# silent relocation this lane keeps removing.
_NAME_STOPWORDS = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "my", "our", "your", "its",
        "new", "small", "tiny", "mini", "simple", "basic", "little", "quick", "minimal", "sample",
        "python", "py", "js", "node", "rust", "go", "java", "typescript", "ts",
        "project", "projects", "folder", "folders", "directory", "directories", "dir",
        "package", "packages", "repo", "repos", "repository", "codebase",
        "app", "apps", "application", "applications", "script", "scripts", "tool", "tools",
        "file", "files", "code", "module", "modules", "source",
        "readme", "read", "me", "test", "tests", "doc", "docs", "documentation",
        "example", "examples", "licence", "license", "requirements",
        "with", "and", "plus", "or", "only", "exactly", "just", "no", "not", "without",
        "containing", "contains", "including", "include", "also",
        "in", "at", "on", "for", "of", "to", "from", "into", "under", "inside", "within",
        "called", "named", "titled", "one", "two", "three", "single", "here", "there",
        "please", "pls", "then", "now",
    }
)


def _read_container_name(rest: str) -> tuple[str, int]:
    """The project name at the start of ``rest``, and where it ends. ``("", 0)`` when there is none.

    A single token is taken as written -- "project stormwatch" names `stormwatch`. A run of
    Capitalized tokens is joined, because "called Storm Watch" is `StormWatch` dictated with a
    breath in the middle, and creating `Storm/` for it would drop half the name the operator gave.

    The end offset is returned because the caller has to read what FOLLOWS the name: a bare file
    list there is what separates "folder called tools with helper.py" from a whole app that happens
    to mention a storage file two sentences later.
    """
    words = [(m.end(), m.group(0).strip(",.;:!?)\"'`")) for m in re.finditer(r"\S+", rest)]
    words = [word for word in words if word[1]]
    if not words:
        return "", 0
    end, first = words[0]
    if first.lower() in _NAME_STOPWORDS or "." in first or "/" in first:
        return "", 0
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", first):
        return "", 0
    name = [first]
    if first[:1].isupper():
        for word_end, token in words[1:3]:
            if token.lower() in _NAME_STOPWORDS or not re.fullmatch(r"[A-Z][A-Za-z0-9_-]*", token):
                break
            name.append(token)
            end = word_end
    return ("".join(name) if len(name) > 1 else name[0]), end


def _named_container(text: str) -> tuple[str, str] | None:
    """``(name, rest_of_sentence_after_the_name)`` for the container this request named."""
    body = normalise_request(text)
    for pattern in (_NAMED_CONTAINER_RE, _BARE_CONTAINER_RE):
        for match in pattern.finditer(body):
            rest = body[match.end() :]
            name, end = _read_container_name(rest)
            if name:
                sentence_end = re.search(r"[.!?]\s+[A-Z]|[.!?]$", rest[end:])
                # The sentence's own ender, never the NEXT sentence's first letter: `.end()`
                # includes the capital the boundary alternative matched, which made
                # "Create folder xxx containing bot.py, requirements.txt and README.md. Do not
                # implement the bot yet." clip its tail to "...README.md. D" -- the tail grammar
                # then refused it and the request fell to the unbounded application template.
                tail = (
                    rest[end : end + sentence_end.start() + 1]
                    if sentence_end
                    else rest[end:]
                )
                return name, tail
    return None


def container_folder(text: str) -> str:
    """The folder the request named for its project, or ``""`` when it named none.

    ``""`` is a real answer and not a failure: "small python app folder, readme + app only" asks
    for a folder without naming one, and the workspace root is the only place this runtime may put
    those files without inventing a name for a directory the operator never chose.
    """
    found = _named_container(text)
    return found[0] if found else ""


# ---------------------------------------------------------------------------
# The files the request enumerates
# ---------------------------------------------------------------------------

# A deliverable the operator named by convention rather than by path: "a README" names README.md as
# concretely as typing it out. `app` and `main` need a Python-flavoured request behind them, because
# outside one they are ordinary English.
_CONVENTIONAL_STEMS: tuple[tuple[str, str, bool], ...] = (
    # (stem pattern, filename, requires a Python-flavoured request)
    (r"readme", "README.md", False),
    (r"app", "app.py", True),
    (r"main", "main.py", True),
)
# A stem immediately followed by a container noun is an adjective, not a file: "app folder" is
# where the files go, "readme" in "readme + app only" is one of them.
_STEM_IS_A_CONTAINER_RE = re.compile(
    r"^\s+(?:" + "|".join(_CONTAINER_NOUNS) + r"|module|package)\b", re.IGNORECASE
)
_PYTHONISH_RE = re.compile(r"\bpython\b|\bpy\b|\.py\b", re.IGNORECASE)

# One entry in a file list: an optional article or size adjective, a file, an optional "file" noun.
_FILE_ITEM = (
    r"(?:(?:a|an|the|one|1|two|single|simple|basic|short|small|minimal|tiny|plain|python|py)\s+)*"
    r"(?:[\w./-]+\.(?:" + _EXT_ALT + r")|readme|app|main)"
    r"(?:\s+(?:file|module|script))?"
)
# What must follow the container's name for the request to have ENUMERATED its contents: a file
# list and nothing else, to the end of that sentence.
#
# "nothing else" is the whole point. "with a Flask app.py, templates and static assets" names a
# file AND two things this runtime would have to invent, so it is not an enumeration and the
# scaffolder keeps the request. Same for "in a new folder called vool-todo-test." followed by
# three sentences of behaviour: an empty tail is not a file list either.
_FILE_LIST_TAIL_RE = re.compile(
    r"^\s*(?:with|containing|holding|of|comprising|[:,-])\s*"
    r"(?:" + _FILE_ITEM + r")"
    r"(?:\s*(?:,\s*and|,|and|plus|&)\s*(?:" + _FILE_ITEM + r"))*"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)

# A create verb introducing a COLON LIST of files: "Inside it, create: - bot.py -
# requirements.txt - README.md". The enumeration itself is the bound -- a colon-introduced list
# after "create" states what is being created, the same grammar `enumerates_a_named_container`
# reads for the inline tail. Two or more file items only: a single item after a colon can be an
# example ("example: app.py"), and one file is not a list the operator sealed. Measured live
# 2026-09-18: the request that listed exactly bot.py, requirements.txt and README.md under
# "create:" carried no marker word this module knew, fell through to the application template,
# and was built as src/bot.py + .env.example + an unsolicited compile.
_CREATE_COLON_LIST_RE = re.compile(
    r"\b(?:create|add|include|write|make|put)\s*:\s*"
    r"(?:-\s*)?(?:" + _FILE_ITEM + r")"
    r"(?:\s+(?:-\s*|,\s*(?:and\s+)?|and\s+|plus\s+|&\s*)?(?:" + _FILE_ITEM + r"))+",
    re.IGNORECASE,
)


def _conventional_matches(body: str) -> list[tuple[int, str]]:
    pythonish = bool(_PYTHONISH_RE.search(body))
    found: list[tuple[int, str]] = []
    for stem, filename, needs_python in _CONVENTIONAL_STEMS:
        if needs_python and not pythonish:
            continue
        # `(?!\.\w)` rather than `(?![\w.])`: the second one also rejects a stem at the end of a
        # SENTENCE, which is why "only app.py and README." resolved to app.py alone and the README
        # the operator asked for in the same breath was never planned.
        for match in re.finditer(r"\b" + stem + r"(?!\w)(?!\.\w)", body, re.IGNORECASE):
            if _STEM_IS_A_CONTAINER_RE.match(body[match.end() :]):
                continue
            found.append((match.start(), filename))
    return found


def _file_mentions(body: str, *, workspace_root: str = "") -> list[tuple[int, str]]:
    """``(character position, resolved filename)`` for every file this ALREADY-NORMALISED text names.

    One reading, shared by the bound check and the enumeration, so "what counts as naming a file"
    cannot mean two different things two lines apart.
    """
    found: list[tuple[int, str]] = []
    for match in re.finditer(r"\S+", body):
        # Reuse the named-file reader on the single token rather than re-deriving its rules: it
        # owns rebasing a workspace-rooted path and DROPPING one that points outside the workspace,
        # and a second copy of that judgement is a second place for it to drift.
        resolved = named_build_files(match.group(0), workspace_root=workspace_root)
        if resolved:
            found.append((match.start(), resolved[0]))
    found.extend(_conventional_matches(body))
    found.sort(key=lambda item: item[0])
    return found


def enumerated_files(text: str, *, workspace_root: str = "") -> list[str]:
    """Every file this request names, in the order it names them.

    Named paths and conventional stems are read in ONE pass ordered by position, because
    "with a README and one app.py file" lists the README first and a plan that reorders the
    operator's own sentence is a plan they have to re-read to check.
    """
    found = _file_mentions(normalise_request(text), workspace_root=workspace_root)
    ordered: list[str] = []
    seen: set[str] = set()
    for _position, path in found:
        key = posixpath.basename(path).lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


# ---------------------------------------------------------------------------
# The elliptical ask
# ---------------------------------------------------------------------------

# A finite verb makes the message a STATEMENT about a project rather than a request for one.
# "the project only has readme and app.py" describes what exists; "small python app folder,
# readme + app only" asks for one. Without this the two are the same bag of words.
_FINITE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|am|has|have|had|contains?|includes?|holds?|"
    r"looks?|seems?|uses?|used|works?|does|do|did|will|would|should|could|can|shall|may|might|"
    r"needs?|wants?|got|gets?)\b",
    re.IGNORECASE,
)
_MAX_ELLIPTICAL_WORDS = 14


def _asks_elliptically(body: str) -> bool:
    """A request written as a bare noun phrase: "small python app folder, readme + app only".

    Narrow on purpose, and every clause of it is load-bearing. No verb at all means no imperative
    for the shared gate to find, so the only thing separating a request from a remark ABOUT a
    project is the absence of a finite verb, a question mark, an interrogative opening -- and a
    length bound, because a long verbless fragment is far more likely to be a heading, a list or a
    pasted note than an instruction.
    """
    if not body or body.rstrip().endswith("?"):
        return False
    if len(body.split()) > _MAX_ELLIPTICAL_WORDS:
        return False
    if _QUESTION_LEAD_RE.match(body) or _FINITE_VERB_RE.search(body):
        return False
    return bool(_CONTAINER_NOUN_RE.search(body))


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SmallProjectPlan:
    """The complete, operator-authored plan for a bounded small-project build."""

    root: str
    files: tuple[str, ...]
    allow_commands: bool
    forbids_tests: bool
    reason: str = ""


def enumerates_a_named_container(text: str) -> bool:
    """True when the request names a container and lists its files right there.

    The second way a file set becomes complete, and the one that needs no marker word: "create
    project called Storm Watch with readme and app.py" says what the project IS. It is bounded by
    grammar rather than by vocabulary -- the tail after the name has to be a file list and nothing
    else -- which is what keeps a whole To-Do app that mentions `tasks.json` in a later sentence
    out of it.
    """
    found = _named_container(text)
    return bool(found and _FILE_LIST_TAIL_RE.match(found[1]))


def resolve_small_project_plan(text: str, *, workspace_root: str = "") -> SmallProjectPlan | None:
    """The bounded plan this request carries, or ``None`` when it carries none.

    ``None`` is the ordinary answer and means "nothing changes": every lane keeps the behaviour it
    had. A plan is returned only for a request that is an instruction, bounds its own file set,
    asks for no file this runtime cannot name, and resolves to at least one concrete target.
    """
    body = normalise_request(text)
    if not body:
        return None
    lowered = f" {body.lower()} "
    # The restraint is the shared one, whole. A bounded file set is not a way around it: "lets
    # discuss a project with only app.py and a readme" is still a discussion.
    if build_request_intent.is_opted_out(lowered) or build_request_intent.is_deliberation(lowered):
        return None
    if not (
        build_request_intent.imperative_build_sentences(body)
        or _asks_in_another_way(body)
        or _asks_elliptically(body)
    ):
        return None
    if not (
        bounds_its_file_set(body, workspace_root=workspace_root)
        or enumerates_a_named_container(body)
    ):
        return None
    if asks_for_unnamed_extras(body):
        return None
    names = enumerated_files(body, workspace_root=workspace_root)
    if not names:
        return None

    root = container_folder(body) or named_file_build_root(body, workspace_root=workspace_root)
    resolved: list[str] = []
    for name in names:
        candidate = name if "/" in name else (f"{root}/{name}" if root else name)
        candidate = posixpath.normpath(candidate)
        if candidate and candidate not in resolved:
            resolved.append(candidate)

    no_tests = forbids_tests(body)
    # Writing files is not consent to execute them, and this request said so out loud. The run has
    # to be asked for, and "no tests" withdraws the ask even when some other clause implies one.
    from core.agent_runtime.builder.mutation_scope import _RUN_REQUEST_RE

    run_requested = bool(_RUN_REQUEST_RE.search(" ".join(body.lower().split())))
    return SmallProjectPlan(
        root=root,
        files=tuple(resolved),
        allow_commands=run_requested and not no_tests,
        forbids_tests=no_tests,
        reason="the request enumerates the complete file set for a small project",
    )


__all__ = [
    "SmallProjectPlan",
    "asks_for_unnamed_extras",
    "bounds_its_file_set",
    "container_folder",
    "enumerated_files",
    "enumerates_a_named_container",
    "forbids_tests",
    "normalise_request",
    "resolve_small_project_plan",
]
