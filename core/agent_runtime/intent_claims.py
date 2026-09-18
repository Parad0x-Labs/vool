"""Non-executing would-claim probes: which fast-path families does a message match?

The front door dispatches fast-path families in a fixed priority order, and each family only
knows its own markers. That is exactly how "check Token hunter folder on this machine" got
answered with MACHINE SPECS: the folder family missed (verb gap) while the specs family matched
the " this machine " marker — nothing ever *compared* the two readings. These probes make the
competing readings visible: run them all (cheaply — pure regex/marker checks, no tool executes),
and when two or more families claim the same message, the dispatch KNOWS it is ambiguous instead
of silently letting priority order win. That signal feeds the decision log today and the
constrained intent arbiter (a real model tie-break) behind it.

Probes must stay side-effect free and conservative: claiming here does not answer anything, it
only describes what the family's own detector would do.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.execution.constants import (
    asks_about_running_processes,
    image_generation_intent,
    machine_diagnostics_intent,
    machine_folder_search_intent,
    machine_host_state_intent,
    machine_path_listing_intent,
)
from core.retrieval_constraints import analyze_retrieval_constraints

# Families a probe can claim for. Keys are stable identifiers used by the decision log, the
# gauntlet, and the arbiter menu — renaming one invalidates recorded history, so don't.
FAMILY_FIND_FOLDER = "find_folder"
FAMILY_DISK_USAGE = "disk_usage"
FAMILY_LIST_DIRECTORY = "list_directory"
FAMILY_READ_FILE = "read_file"
FAMILY_MACHINE_SPECS = "machine_specs"
FAMILY_FOLDER_OVERVIEW = "folder_overview"
FAMILY_IMAGE_GENERATION = "image_generation"
FAMILY_LIST_PROCESSES = "list_processes"
FAMILY_HOST_STATE = "host_state"


@dataclass(frozen=True)
class IntentClaim:
    family: str
    argument: str = ""


class ActionPolicy(str, Enum):
    """Turn-scoped execution policy derived from the user's actual request."""

    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"


def _padded(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip().lower()
    normalized = re.sub(r"[\?\!\.,:;]+", " ", normalized)
    return f" {' '.join(normalized.split())} "


def _claim_find_folder(text: str) -> IntentClaim | None:
    got = machine_folder_search_intent(text)
    return IntentClaim(FAMILY_FIND_FOLDER, got[1]) if got else None


def _claim_disk_usage(text: str) -> IntentClaim | None:
    return IntentClaim(FAMILY_DISK_USAGE) if machine_diagnostics_intent(text) == "machine.disk_usage" else None


def _claim_list_directory(text: str) -> IntentClaim | None:
    # A path the user typed out plus "what is inside it" is a listing and nothing else, so it is
    # claimed here rather than left to look like a near-miss the arbiter has to guess at.
    explicit = machine_path_listing_intent(text)
    if explicit:
        return IntentClaim(FAMILY_LIST_DIRECTORY, explicit)
    # Mirrors the directory-listing combo in looks_like_supported_machine_read_request: a known
    # user directory + a listing verb phrase.
    padded = _padded(text)
    has_directory = any(f" {name} " in padded for name in ("desktop", "downloads", "documents", "docs"))
    listing_phrases = (
        " list ", " show ", " what are ", " what's on ", " what is on ",
        " contents of ", " what do we have on ", " tell me what ", " can you see ",
    )
    has_listing = any(phrase in padded for phrase in listing_phrases) or any(
        phrase in padded for phrase in (" folders and files ", " files and folders ", " folder and file ")
    )
    return IntentClaim(FAMILY_LIST_DIRECTORY) if has_directory and has_listing else None


def _claim_read_file(text: str) -> IntentClaim | None:
    try:
        from core.agent_runtime.fast_paths_machine import _extract_machine_file_read_target

        target = _extract_machine_file_read_target(text)
    except Exception:
        target = None
    if not target:
        return None
    return IntentClaim(FAMILY_READ_FILE, str(target.get("filename") or target.get("path") or ""))


def _claim_machine_specs(text: str) -> IntentClaim | None:
    # The gate's own rule, not a copy of its marker list: a probe that claimed on topic words
    # alone would report "machine_specs" for the live-state questions the specs lane now declines,
    # and the ambiguity signal would be describing a dispatch that no longer happens.
    try:
        from core.agent_runtime.fast_paths_machine import looks_like_machine_specs_question
    except Exception:
        return None
    return IntentClaim(FAMILY_MACHINE_SPECS) if looks_like_machine_specs_question(text) else None


def _claim_list_processes(text: str) -> IntentClaim | None:
    # The broader reading, not the routing one: the operator lane owns some of these phrasings and
    # answers them well, but the family that READS them is still the process list. Claiming here is
    # what keeps "list the top processes by cpu usage" out of the near-miss branch and off the
    # arbiter, which is a 6s model call to reach the lane it was already going to reach.
    return IntentClaim(FAMILY_LIST_PROCESSES) if asks_about_running_processes(text) else None


def _claim_host_state(text: str) -> IntentClaim | None:
    return IntentClaim(FAMILY_HOST_STATE) if machine_host_state_intent(text) is not None else None


def _claim_folder_overview(text: str) -> IntentClaim | None:
    try:
        from core.agent_runtime.fast_paths_utility import _FOLDER_OVERVIEW_RE

        return IntentClaim(FAMILY_FOLDER_OVERVIEW) if _FOLDER_OVERVIEW_RE.search(str(text or "")) else None
    except Exception:
        return None


def _claim_image_generation(text: str) -> IntentClaim | None:
    prompt = image_generation_intent(text)
    return IntentClaim(FAMILY_IMAGE_GENERATION, str(prompt or "")) if prompt else None


#: The registry as (family, probe) pairs, so a consumer that needs the family each probe claims
#: does not have to restate this list beside it. `core.agent_runtime.answer_coverage` reads these
#: pairs to let the whole-turn arbitration SEE machine/tool clauses; keeping the pairs here is what
#: stops the two registries from drifting apart again.
_PROBE_FAMILIES = (
    (FAMILY_FIND_FOLDER, _claim_find_folder),
    (FAMILY_DISK_USAGE, _claim_disk_usage),
    (FAMILY_LIST_DIRECTORY, _claim_list_directory),
    (FAMILY_READ_FILE, _claim_read_file),
    (FAMILY_MACHINE_SPECS, _claim_machine_specs),
    (FAMILY_LIST_PROCESSES, _claim_list_processes),
    (FAMILY_HOST_STATE, _claim_host_state),
    (FAMILY_FOLDER_OVERVIEW, _claim_folder_overview),
    (FAMILY_IMAGE_GENERATION, _claim_image_generation),
)

_PROBES = tuple(probe for _, probe in _PROBE_FAMILIES)


def probe_claims(text: str) -> list[IntentClaim]:
    """Every family that would claim this message, probe order (NOT dispatch priority)."""
    claims: list[IntentClaim] = []
    for probe in _PROBES:
        try:
            claim = probe(text)
        except Exception:
            claim = None
        if claim is not None:
            claims.append(claim)
    return claims


def is_ambiguous(claims: list[IntentClaim]) -> bool:
    """Two or more DISTINCT families reading the same message = the priority order is guessing."""
    return len({claim.family for claim in claims}) >= 2


# Tool-ish nouns: a message carrying one of these but claiming NO family is a near-miss — the
# user probably wanted a tool but phrased it in a way no detector knows (typo'd verb, novel
# phrasing). Those are arbiter candidates, never silent model-lane fallthroughs.
_TOOLISH_NOUN_RE = re.compile(
    r"\b(?:folder|folders|directory|directories|file|files|disk|drive|storage|ram|memory|cpu|gpu|specs?)\b",
    re.IGNORECASE,
)


# A concrete path the user typed. Absolute, home-relative, or explicit ./ — a bare word is not
# a path, so ordinary prose cannot trip this.
_EXPLICIT_PATH_RE = re.compile(r"(?:^|[\s`'\"(])((?:~|\.{1,2})?/[\w\-./]{2,})")
_NON_MACHINE_FILE_TYPE_RE = re.compile(
    r"\b(?:file|document|image|audio|video)\s+(?:format|type|extension|standard)s?\b",
    re.IGNORECASE,
)
_SEMANTIC_FILE_TYPE_QUESTION_RE = re.compile(
    r"(?:^|[.!?]\s*)(?:which|what|are|is|does|do)\b"
    r"|\b(?:defined\s+by|member(?:s|ship)?\s+of|belongs?\s+to|supports?)\b",
    re.IGNORECASE,
)
_NO_ACTION_RE = re.compile(
    r"\b(?:take|perform|run|do)\s+no\s+actions?\b"
    r"|\bdo\s+not\s+(?:take|perform|run|do)\s+(?:any\s+)?actions?\b"
    r"|\bwithout\s+(?:taking|performing|running|doing)\s+(?:any\s+)?actions?\b",
    re.IGNORECASE,
)
_SPECIFIC_NO_ACTION_RE = re.compile(
    r"\bdo\s+not\s+(?:inspect|open|read|list|search|check|run|write|create|edit|change|delete|remove|rename|move)\b",
    re.IGNORECASE,
)
_CONVERSATIONAL_OPENING_RE = re.compile(
    r"^\s*(?:"
    r"why\b"
    r"|what\s+do\s+you\s+think\b"
    r"|how\s+do\s+you\s+feel\b"
    r"|what(?:'s|\s+is)\s+a\s+sensible\b"
    r"|explain\s+(?:this|that|why|conceptually)\b"
    r")",
    re.IGNORECASE,
)


def near_miss(text: str, claims: list[IntentClaim]) -> bool:
    """No family claimed, but the message points at something a tool could act on.

    Two signals, and the second is the one that was missing. A tool-ish NOUN
    ("folder", "file", "disk") catches most phrasings. A concrete PATH catches the rest: a user
    who types `~/Desktop/notes` is naming a place regardless of which noun they wrapped it in.

    Measured 2026-07-28. Across eight phrasings of one request, the six that reached the
    deterministic arbiter answered correctly in about a second and never called a model.
    "give me a rundown of what lives in <path>" contains no noun from the list, so this returned
    False, the arbiter was never consulted, and the turn fell through to the model lane and died
    on a 60-second provider timeout. Asked directly, the arbiter routes that exact sentence to
    `list_directory` in 0.79s — the gate simply never let it see the sentence.
    """

    if claims:
        return False
    text = str(text or "")
    # A user can explicitly request a conceptual answer.  Do not send that prose to a tool
    # arbiter merely because it names a filesystem-shaped noun: any resulting action claim would
    # be dishonest, even when the tool is later declined safely.
    if _NO_ACTION_RE.search(text) or _CONVERSATIONAL_OPENING_RE.search(text):
        return False
    # A request for a poem or a story names its subject, and a folder or a file inside that subject is
    # not something a tool could act on (`grounded_mode.creative_writing_remainder`). Measured on the
    # served path (2026-09-15): "i'd like a sonnet about the wind in this folder" and "write a poem about
    # the weather in this folder" were near-misses on "folder", and a `find_folder` pick ran
    # `machine.find_folder` in place of the poem. Only the rest of the request can be a near-miss.
    from core.agent_runtime.grounded_mode import creative_writing_remainder

    text = creative_writing_remainder(text)
    if not text.strip():
        return False
    semantic_type_spans = tuple(match.span() for match in _NON_MACHINE_FILE_TYPE_RE.finditer(text))
    toolish = next(
        (
            match
            for match in _TOOLISH_NOUN_RE.finditer(text)
            if not any(start <= match.start() and match.end() <= end for start, end in semantic_type_spans)
        ),
        None,
    )
    if toolish is not None:
        return True
    return _EXPLICIT_PATH_RE.search(text) is not None


def semantic_file_type_request(text: str) -> bool:
    """Whether file-like words name a data category, not a local machine object.

    This is deliberately narrow and positive: at least one explicit format/type/extension/standard
    phrase, no concrete path, and no typed machine-family claim. It lets the always-on catalog stay
    open for genuinely novel tool requests while preventing ordinary standards or compatibility
    questions from being converted into filesystem work.
    """

    raw = str(text or "")
    return bool(
        _NON_MACHINE_FILE_TYPE_RE.search(raw)
        and _SEMANTIC_FILE_TYPE_QUESTION_RE.search(raw)
        and _EXPLICIT_PATH_RE.search(raw) is None
        and not probe_claims(raw)
    )


# Verbs whose negation genuinely disables the turn's write lane. `do not write` and `do not delete`
# take the action surface with them; `do not run` and `do not read` do not, and conflating the two
# is what turned "Do not run tests" into a turn with no build in it at all.
_MUTATING_NEGATED_VERBS = frozenset({"write", "create", "edit", "change", "delete", "remove", "rename", "move"})

# The object of a negated verb, when the operator named ONE operation rather than banning the lane.
# "do not run TESTS" constrains the test run. "do not list, open, or change anything" constrains
# everything, and nothing here matches it -- the tail after "list" is a comma, not a noun.
_SCOPED_NEGATION_OBJECT_RE = re.compile(
    r"^\s+(?:the\s+|a\s+|an\s+|any\s+|its\s+|these\s+|those\s+|my\s+|our\s+)*"
    r"(?:tests?|test\s+(?:suite|file|files|module|modules)|suite|unit\s+tests?|"
    r"it|them|code|script|app|file|command|commands|benchmarks?|linters?|lint)"
    r"(?![\w-])",
    re.IGNORECASE,
)

# "do not run / don't execute / without running / no tests / skip the tests" -- the operator saying
# do not EXECUTE anything, which is a real constraint that has to reach the tool boundary even
# though it leaves the rest of the turn alone.
_NO_RUN_RE = re.compile(
    r"\b(?:do\s+not|do\s+nt|don'?t|dont|never)\s+(?:run|execute|invoke|launch)\b"
    r"|\bwithout\s+(?:running|executing)\b"
    r"|\bno\s+tests?\b"
    r"|\bskip\s+(?:the\s+)?tests?\b",
    re.IGNORECASE,
)

# Server-derived, never caller-supplied: the turn's "do not execute anything" constraint as it
# travels in `source_context` to the tool boundary.
NO_COMMANDS_CONTEXT_KEY = "no_command_execution"


@dataclass(frozen=True)
class TurnActionConstraints:
    """What a turn's own words permit: the lane policy, plus the narrower bans inside it."""

    policy: ActionPolicy = ActionPolicy.ALLOWED
    forbid_commands: bool = False


def _negation_is_scoped(value: str, match: re.Match[str]) -> bool:
    """True when this ``do not <verb>`` names ONE operation inside a turn that asks for another.

    Both halves are required, and each rules out a case the other lets through.

    The verb must not be a mutating one: "create X but do not write files" really does withdraw the
    write lane, whatever else the sentence says.

    The turn must carry a real build instruction the negation does not cover. Without that clause,
    "Do not run a search. Conceptually, why are indexes useful?" -- a question with no action in it
    at all -- would come out ALLOWED, and the whole no-action policy with it.
    """
    verb = str(match.group(0) or "").rsplit(None, 1)[-1].lower()
    if verb in _MUTATING_NEGATED_VERBS:
        return False
    if not _SCOPED_NEGATION_OBJECT_RE.match(value[match.end() :]):
        return False
    from core.agent_runtime import build_request_intent

    return bool(build_request_intent.imperative_build_sentences(value))


def action_policy_for_text(text: str) -> ActionPolicy:
    """Return the turn policy from clear, user-authored no-action language only."""
    return turn_action_constraints(text).policy


def turn_action_constraints(text: str) -> TurnActionConstraints:
    """The full reading of a turn's no-action language: what it forbids, and how much of it.

    A live QA drive asked "Create a small Python project called StormWatch with a README and one
    app.py file. Do not run tests." and got three copies of "I will keep this turn conversational
    and will not propose or run an action", no README and no app.py. The final sentence constrains
    ONE operation; it was read as a ban on the turn, and it took the build with it.

    So the answer is two values, not one. `forbid_commands` carries the constraint the operator
    actually stated down to the tool boundary, which is the only way "do not run tests" can be
    honoured without the build being cancelled to honour it.
    """
    value = str(text or "")
    forbid_commands = bool(_NO_RUN_RE.search(value))
    if _NO_ACTION_RE.search(value) or analyze_retrieval_constraints(value).forbids_all_tools:
        return TurnActionConstraints(policy=ActionPolicy.FORBIDDEN, forbid_commands=True)
    for match in _SPECIFIC_NO_ACTION_RE.finditer(value):
        # A bounded create request commonly ends with "do not create anything else".
        # That constrains an already-authorized action; it is not a request to disable
        # the action lane for the entire turn.
        tail = value[match.end() :]
        if re.match(r"\s+(?:anything|any\s+\w+)\s+else\b", tail, re.IGNORECASE):
            continue
        if _negation_is_scoped(value, match):
            continue
        return TurnActionConstraints(policy=ActionPolicy.FORBIDDEN, forbid_commands=True)
    return TurnActionConstraints(policy=ActionPolicy.ALLOWED, forbid_commands=forbid_commands)


def action_policy_from_context(source_context: dict[str, object] | None) -> ActionPolicy:
    """Read the server-derived policy defensively at execution boundaries."""
    value = str((source_context or {}).get("action_policy") or "").strip().lower()
    return ActionPolicy.FORBIDDEN if value == ActionPolicy.FORBIDDEN.value else ActionPolicy.ALLOWED


def commands_forbidden_in_context(source_context: dict[str, object] | None) -> bool:
    """Whether this turn's own words ruled out executing anything.

    Read at the tool boundary for the same reason the policy is: the constraint is decided once
    from the operator's sentence, and every lane that can reach a command has to honour it without
    each one re-deriving what "do not run tests" meant.
    """
    context = source_context or {}
    if action_policy_from_context(context) is ActionPolicy.FORBIDDEN:
        return True
    return bool(context.get(NO_COMMANDS_CONTEXT_KEY))


__all__ = [
    "FAMILY_DISK_USAGE",
    "FAMILY_FIND_FOLDER",
    "FAMILY_FOLDER_OVERVIEW",
    "FAMILY_HOST_STATE",
    "FAMILY_IMAGE_GENERATION",
    "FAMILY_LIST_DIRECTORY",
    "FAMILY_LIST_PROCESSES",
    "FAMILY_MACHINE_SPECS",
    "FAMILY_READ_FILE",
    "NO_COMMANDS_CONTEXT_KEY",
    "ActionPolicy",
    "IntentClaim",
    "TurnActionConstraints",
    "action_policy_for_text",
    "action_policy_from_context",
    "commands_forbidden_in_context",
    "is_ambiguous",
    "near_miss",
    "probe_claims",
    "turn_action_constraints",
]
