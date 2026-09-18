"""How much of the workspace a build request actually authorized this runtime to change.

A QA drive asked, in an otherwise empty project::

    Create a file called notes.txt in this project containing the line: hello from qa.

and watched the runtime create ``generated/file-called-notes-txt-b92eec/``, write ``notes.txt``,
``app.py``, ``README.md`` and ``test_app.py`` into it, run ``python -m unittest``, rewrite
``test_app.py`` and run it again. ``notes.txt`` was correct. Everything else was invented.

Nothing in that trace was a bug in any single step. The request named one file; the model-driven
build lane in ``builder/app_builder.py`` then asked a model to *list the files for this project* --
a prompt that instructs it to include a test module and a README -- and executed whatever came
back. The lane had no idea it had been handed an exact target, because nobody ever computed one.
Planner creativity became mutation authority, which is the boundary this module exists to hold:

    **A request that names its files authorizes those files. Nothing else, and no commands.**

The scope is resolved from the request BEFORE planning, so the file list is the operator's and the
model only ever supplies content. It is deliberately narrow: it fires only where
``looks_like_named_file_build_request`` already fires, and it steps aside the moment the request
asks for something broader than the files it names -- a project, an app, a service, "and tests".
Those still reach the full build/test/fix lane untouched, because a scaffold that was asked for is
not a scope violation.

Two signals decide "broader", and both are read off the sentence rather than guessed:

* **A multi-file deliverable as the OBJECT of the build verb.** "create a small python project with
  app.py, README.md and tests" builds a project. "create a file called notes.txt in this project"
  builds a file -- the same word, in a locative phrase, naming where rather than what. The object
  phrase ends at the first preposition or subordinator, which is what separates the two. Filenames
  are stripped out of that phrase first: ``\\bapp\\b`` matches inside ``app.py``, and this repository
  has already paid once for substring collisions in build vocabulary (see ``build_request_intent``).
* **Unnamed extras asked for explicitly** -- "and tests", "with a readme". The operator asked for
  files they did not name, so the model gets to choose them.

Vocabulary is reused, never re-implemented. ``build_request_intent`` exists because five copies of
"is this a build?" disagreed 31 times out of 116; a sixth copy of the verb list here would be the
same defect wearing a different name.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from core.agent_runtime import build_request_intent
from core.agent_runtime.builder.named_file_build import (
    looks_like_named_file_build_request,
    named_build_files,
    named_file_build_root,
)

EXACT = "exact"
OPEN = "open"

# Deliverables that are more than one file by definition. A "script", a "module", a "test" and a
# "tool" are all things a single named file can be, so they are deliberately absent: "build me a
# small python script called reverse.py" names its one file and means it, and treating `script` as
# a widening noun would hand that request back to the scaffolder.
_MULTI_FILE_NOUNS = (
    "project", "projects",
    "app", "apps", "application", "applications",
    "api", "apis",
    "service", "services",
    "server", "servers",
    "site", "sites", "website", "websites",
    "bot", "bots",
    "cli",
    "dashboard", "dashboards",
    "package", "packages",
    "repo", "repos", "repository", "repositories",
    "codebase",
    "monorepo",
    "scaffold", "scaffolding",
    "boilerplate",
    "starter",
    "suite",
    "webhook", "webhooks",
    "plugin", "plugins",
    "skill", "skills",
)
_MULTI_FILE_NOUN_RE = re.compile(r"\b(?:" + "|".join(_MULTI_FILE_NOUNS) + r")\b")

# Where the object of the build verb ends. "create a file called notes.txt IN this project" and
# "create a small python project WITH app.py" both carry the word "project"; only the second one is
# building one, and the preposition is the whole difference.
#
# The boundaries exclude hyphens as well as word characters. A plain `\bto\b` matches inside
# "to-do", so "create a small python command-line to-do app in a new folder" ended its object phrase
# at "to" -- and a request for a whole To-Do app read as an exact single-file write.
_OBJECT_END_RE = re.compile(
    r"(?<![\w-])(?:in|inside|into|within|under|at|on|onto|to|from|for|of|with|without|containing|"
    r"contains|that|which|whose|so|because|using|via)(?![\w-])"
)

# Anything shaped like a path with an extension. Removed from the object phrase before the noun
# test, because `\bapp\b` matches inside `app.py` and `\bcli\b` inside `cli.py`.
_PATHY_RE = re.compile(r"\S*\.[A-Za-z0-9]{1,10}(?![\w])\S*")

# Files the operator asked for but did not name. Asking for "tests" without naming one hands the
# choice to the model, which is a widening the operator performed themselves.
_UNNAMED_EXTRAS_RE = re.compile(
    r"\b(?:and|with|plus|including|include|also)\s+"
    r"(?:a\s+|an\s+|the\s+|some\s+|basic\s+|simple\s+|short\s+|unit\s+|proper\s+)*"
    r"(?:tests?|test\s+suite|readme|documentation|docs|examples?|scaffold(?:ing)?)\b"
)

# The operator asking for the result to be executed. Without this, an exact named-file request
# authorizes writes and nothing else -- "create notes.txt" is not consent to run a shell command.
_RUN_REQUEST_RE = re.compile(
    r"\brun\s+(?:it|them|those|the\s+tests?|the\s+suite|the\s+code|the\s+script|the\s+file)\b"
    r"|\b(?:and|then)\s+run\b"
    r"|\brun\s+them\s+to\b"
    r"|\b(?:verify|test)\s+it\b"
    r"|\bmake\s+sure\s+(?:it|they)\b"
    r"|\bcheck\s+(?:it|they)\s+(?:passes|pass|works?)\b"
)


def normalise_path(candidate: str) -> str:
    """A workspace-relative path in one shape, so two spellings of one target compare equal.

    A leading ``/`` is NOT stripped: turning ``/tmp/evil.py`` into ``tmp/evil.py`` would quietly
    convert an escape into a legal-looking relative target. It stays absolute here and
    ``MutationScope.authorizes`` refuses it as one.
    """
    text = str(candidate or "").strip().strip("`\"'").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.rstrip("/")
    return posixpath.normpath(text) if text else ""


@dataclass(frozen=True)
class MutationScope:
    """The mutation authority a single request carries.

    ``OPEN`` is this runtime's historical behaviour and authorizes whatever the lane decides to do.
    ``EXACT`` names its targets, and every consumer must refuse anything outside them.
    """

    kind: str = OPEN
    paths: tuple[str, ...] = ()
    root_dir: str = ""
    allow_commands: bool = True
    reason: str = ""

    @property
    def is_exact(self) -> bool:
        return self.kind == EXACT

    def authorizes(self, path: str) -> bool:
        """Whether ``path`` is a file this request authorized writing.

        Two conditions, and the confinement one is checked FIRST. Under an EXACT scope the build
        lane skips ``_confine_relative_path`` -- flattening a target against one build directory is
        what relocates the second of two named folders -- so this method is the confinement gate
        that replaces it. A ``..`` escape, an absolute path or a drive-letter path is refused
        whatever the target list says, because a scope is a plain dataclass any caller can build and
        a boundary that trusts its own inputs is not a boundary.
        """
        if not self.is_exact:
            return True
        normalised = normalise_path(path)
        if not normalised or normalised == "..":
            return False
        if normalised.startswith(("../", "/", "~")) or re.match(r"^[A-Za-z]:[\\/]", normalised):
            return False
        return normalised in {normalise_path(target) for target in self.paths}

    def authorizes_directory(self, path: str) -> bool:
        """Whether ``path`` is a directory this request authorized creating.

        The workspace root always qualifies (it exists already). Anything else must be an ancestor
        of an authorized file -- which is how ``create src/foo.py`` may make ``src/`` while
        ``create notes.txt`` may not make ``generated/file-called-notes-txt-b92eec/``.
        """
        if not self.is_exact:
            return True
        normalised = normalise_path(path)
        if not normalised or normalised == ".":
            return True
        prefix = normalised + "/"
        return any(normalise_path(target).startswith(prefix) for target in self.paths)

    def as_dict(self) -> dict[str, object]:
        """The scope as it goes into the turn's change ledger -- targets, root, command authority."""
        return {
            "kind": self.kind,
            "authorized_paths": list(self.paths),
            "root_dir": self.root_dir,
            "allow_commands": bool(self.allow_commands),
            "reason": self.reason,
        }


def open_scope(reason: str = "") -> MutationScope:
    """The unbounded scope: the lane decides, exactly as it did before this module existed."""
    return MutationScope(kind=OPEN, allow_commands=True, reason=reason)


def _object_phrase_widens(sentence: str) -> bool:
    """True when a multi-file deliverable is the OBJECT of a build verb in this sentence."""
    # `_BUILD_VERB_RE` is imported rather than copied on purpose: `build_request_intent` documents
    # what five divergent copies of this vocabulary cost, and a sixth would repeat it.
    for match in build_request_intent._BUILD_VERB_RE.finditer(sentence):
        tail = sentence[match.end() :]
        end = _OBJECT_END_RE.search(tail)
        phrase = tail[: end.start()] if end else tail
        if _MULTI_FILE_NOUN_RE.search(_PATHY_RE.sub(" ", phrase)):
            return True
    return False


def request_widens_beyond_named_files(text: str) -> bool:
    """True when the request asks for more than the files it names.

    Read off the imperative build sentences only, so a question or a report sitting beside the
    instruction cannot widen it -- "create notes.txt; what would a full project look like?" is one
    instruction and one question, and the question authorizes nothing.
    """
    for sentence in build_request_intent.imperative_build_sentences(text):
        if _object_phrase_widens(sentence) or _UNNAMED_EXTRAS_RE.search(sentence):
            return True
    return False


def object_phrase_widens(text: str) -> bool:
    """True when a multi-file deliverable is the OBJECT of a build verb in this request.

    The public form of ``_object_phrase_widens`` for detectors that select a lane: a project
    noun in the OBJECT phrase ("create a small python project") widens, while the same noun in
    a LOCATIVE phrase ("create notes.txt containing hello in this project") does not — it names
    where, not what. This replaces noun-anywhere promotion, which handed literal one-file
    writes to the multi-file scaffolder under an OPEN scope.
    """
    for sentence in build_request_intent.imperative_build_sentences(text):
        if _object_phrase_widens(sentence):
            return True
    return False


def scope_root(text: str, *, workspace_root: str = "") -> str:
    """The folder the named files belong in: the one carried by a named path, else the one stated.

    Both halves are load-bearing. "create <ws>/qa2/greet.py ..." states its folder inside the path,
    and "build me a small python script in <ws>/qa3 called reverse.py" states it separately -- the
    second shape is exactly the one that once built in `<ws>/reverse.py/`. The path-carried folder
    is preferred because ``extract_requested_builder_root`` reads "called foo" as a folder name and
    would answer `foo` for "create src/foo.py with a function called foo".
    """
    from_path = named_file_build_root(text, workspace_root=workspace_root)
    if from_path:
        return from_path
    from core.agent_runtime.fast_paths_builder import extract_requested_builder_root

    try:
        stated = extract_requested_builder_root(text, workspace_root=workspace_root)
    except TypeError:  # a caller-supplied stub with the older single-argument signature
        stated = extract_requested_builder_root(text)
    return normalise_path(stated)


def authorized_targets(text: str, *, workspace_root: str = "") -> tuple[str, ...]:
    """The workspace-relative files this request named, resolved against the folder it named.

    A named path that carries its own folder is taken verbatim. A bare filename adopts the folder
    the request stated, which is what "and a test_fizz.py next to it" means -- and the reason it is
    resolved HERE rather than by flattening later is that flattening against one target directory
    silently relocates the second file when a request names two different folders.
    """
    root = scope_root(text, workspace_root=workspace_root)
    resolved: list[str] = []
    for path in named_build_files(text, workspace_root=workspace_root):
        candidate = path if "/" in path else (f"{root}/{path}" if root else path)
        candidate = normalise_path(candidate)
        if candidate and candidate not in resolved:
            resolved.append(candidate)
    return tuple(resolved)


def resolve_mutation_scope(text: str, *, workspace_root: str = "") -> MutationScope:
    """The mutation authority ``text`` carries, decided before anything is planned or executed.

    Biased toward OPEN, which is the behaviour every existing build lane already has: a request only
    becomes EXACT when it names concrete files, is already recognised as a named-file build, and
    asks for nothing wider than those files. Getting this wrong in the OPEN direction costs the
    operator an invented file they can delete; getting it wrong in the EXACT direction would refuse
    a scaffold they asked for, which is a capability regression.
    """
    body = str(text or "")
    # A request that BOUNDS its own file set -- "only these", "exactly these", "one app.py file",
    # "no tests" -- authorizes that set even when it also says "project". The noun names the
    # container; the enumeration names the contents, and reading the noun alone is what sent
    # "Create a small Python project called StormWatch with a README and one app.py file" to a
    # model with the question "list the files for this project" and got `test_app.py` back. An
    # UNBOUNDED project request is untouched and still reaches the scaffolder below.
    from core.agent_runtime.builder import small_project_plan as small_project_plan_module

    plan = small_project_plan_module.resolve_small_project_plan(body, workspace_root=workspace_root)
    if plan is not None:
        return MutationScope(
            kind=EXACT,
            paths=plan.files,
            root_dir=plan.root,
            allow_commands=plan.allow_commands,
            reason=plan.reason,
        )
    if not looks_like_named_file_build_request(body, workspace_root=workspace_root):
        return open_scope(reason="not a named-file build request")
    if request_widens_beyond_named_files(body):
        return open_scope(reason="the request asks for a whole deliverable, not only the files it names")
    targets = authorized_targets(body, workspace_root=workspace_root)
    if not targets:
        return open_scope(reason="no concrete file target survived resolution")
    return MutationScope(
        kind=EXACT,
        paths=targets,
        root_dir=scope_root(body, workspace_root=workspace_root),
        allow_commands=bool(_RUN_REQUEST_RE.search(" ".join(body.lower().split()))),
        reason="the request names its files and asks for nothing wider",
    )


__all__ = [
    "EXACT",
    "OPEN",
    "MutationScope",
    "authorized_targets",
    "normalise_path",
    "object_phrase_widens",
    "open_scope",
    "request_widens_beyond_named_files",
    "resolve_mutation_scope",
    "scope_root",
]
