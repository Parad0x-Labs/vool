"""Answer "where did that file go?" from the receipt of the write, never from a search.

THE LIVE DEFECT
---------------
Measured in chat. The user asked for a one-sentence description of their app written into
``README.md``; the Files table reported it written at the workspace root. The next three turns:

    "where was this file storred?"
        -> VOOL searched ``/`` for a FOLDER whose name matched "file storred", reported that no
           such folder existed, and never mentioned README.md.
    "u did created readme.md where did u created it?"
        -> "the root of your workspace", with no machine path anywhere in the answer.
    "I see, but since we are in General chat now - how can i find it in my machine?"
        -> a vague answer, escalated to cloud, still no absolute root.

ROOT CAUSE
----------
Driving the real front door with fourteen phrasings of this family found two causes. Most of them
reached the END of the door claimed by nothing, so the turn was handed to the model with no receipt
attached; the model then did the only thing available to it -- guessed a tool -- and
``machine.find_folder`` on the user's own words is what that guess looks like. The remaining four
were claimed by the workspace-identity lane, which answers with the name of the bound folder and
never mentions the file: the second measured reply, exactly. The runtime already HELD the answer
either way -- the write left a mutation record carrying the absolute workspace root, the relative
path and the action.

So the fix is a lookup, not a heuristic. This module is the seam:

  * `latest_file_action_receipt` reads the four stores a file mutation actually lands in, newest
    and most-specific first, and returns what they recorded -- never what the question said.
  * `location_followup_kind` decides whether a message is asking where THAT file went. The subject
    it accepts is drawn from the receipt at call time (the filename, its stem), so nothing here is
    keyed to a phrase or an expected answer.
  * `render_*` turn the receipt into the reply.

WHY THE DETECTOR IS SHAPED THIS WAY
-----------------------------------
Three gates, in order, and the last is the one doing the real work:

  1. A question about which folder this CHAT is bound to belongs to the workspace-identity lane
     ("where are we?" is otherwise indistinguishable from an elliptical location follow-up), and a
     message that still contains WORK belongs to whatever lane does that work ("create a skill
     called qa-echo. where do i find it?" must create the skill).
  2. A location ASK marker must be present.
  3. The clause carrying that marker must name NO subject other than this file. That is a
     vocabulary check, not a blocklist: "where is Python installed?" and "where are app settings
     stored?" are refused because ``python``/``app``/``settings`` are subjects this family has no
     business answering about, and any other foreign subject is refused the same way without being
     enumerated in advance.

Gate 3 is also why there is no separate veto for search DIRECTIVES. An explicit search names what
to search for ("folders named storage", "grep the repo for TODO", "search the project for README.md
content"), and every one of those subjects is foreign by construction -- ``storage``, ``grep``,
``named``, ``contents``. A dedicated regex for them was written first and then removed: probing it
against every control in the suite showed it could not change a single verdict, and a guard that
cannot fire is a guard nobody can test.

Typos are absorbed by bounded fuzzy matching against a small set of words this family is built
from ("storred" -> "stored"), not by listing misspellings -- the live defect's own question was
misspelled, and the next one will be misspelled differently.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

# Tool intents that leave a FILE somewhere a user can go and look at. `workspace.ensure_directory`
# is deliberately absent: a directory is not the thing these questions ask about.
_FILE_MUTATION_INTENTS = frozenset(
    {
        "workspace.write_file",
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
        "workspace.run_formatter",
        "machine.write_file",
        "machine.download_file",
        "operator.move_path",
    }
)

_PATH_ARGUMENT_KEYS = ("path", "file_path", "target", "target_path", "destination", "dest", "to")


@dataclass(frozen=True)
class FileActionReceipt:
    """What a previous turn's file mutation actually recorded.

    ``workspace_root`` and ``absolute_path`` are empty when no store knew them -- which is a real
    state (a chat with no bound workspace), not a defect, and the renderer says so rather than
    inventing a root.
    """

    file_name: str
    relative_path: str
    workspace_root: str = ""
    absolute_path: str = ""
    action: str = ""
    intent: str = ""
    source: str = ""

    @property
    def has_absolute_path(self) -> bool:
        return bool(self.absolute_path)

    def subject_tokens(self) -> tuple[str, ...]:
        """The words that count as naming THIS file: its name, its stem, and its path INSIDE the
        workspace.

        Drawn from the receipt, so "where is exact_one_file_6a73.txt?" is recognised for the same
        reason "where is README.md?" is, and neither is spelled anywhere in this file.

        The absolute path's own segments are deliberately excluded. Folding them in makes every
        directory on the way to the file -- ``users``, ``projects``, the project's own name -- a
        subject this lane will answer about, so a receipt at
        ``/Users/qa/projects/thunder/README.md`` would have it claim "where is the thunder folder?"
        and reply with the README's path. That is a folder search and belongs to the folder lane.
        """
        sources = [self.file_name]
        if not _is_absolute(self.relative_path):
            sources.append(self.relative_path)
        out: list[str] = []
        for value in sources:
            text = str(value or "").strip().lower()
            if not text:
                continue
            for segment in re.split(r"[\\/]+", text):
                segment = segment.strip()
                if not segment:
                    continue
                out.append(segment)
                stem = segment.rsplit(".", 1)[0].strip()
                if stem and stem != segment:
                    out.append(stem)
        return tuple(dict.fromkeys(item for item in out if len(item) >= 2))


# ------------------------------------------------------------------------------ receipt lookup


def latest_file_action_receipt(
    session_id: str,
    *,
    workspace_root: str = "",
) -> FileActionReceipt | None:
    """The most recent file mutation this session recorded, or ``None`` when it recorded none.

    Four stores are consulted because a file mutation lands in four, and only the first of them
    carries an ABSOLUTE root:

      1. the workspace mutation ledger  -- relative path + absolute workspace root + action;
      2. durable runtime tool receipts  -- mutating intents, replay-keyed, path in the arguments;
      3. in-process execution records   -- every executed tool, with the tool's own resolved target;
      4. durable runtime session events -- the Activity action record.

    Each is read defensively: a store that is unconfigured, empty or unreadable contributes nothing
    and never raises into the turn. ``workspace_root`` is the turn's own bound root and is used
    only to complete a receipt that had no root of its own -- it never overrides one that did.
    """
    for lookup in (
        _from_mutation_ledger,
        _from_tool_receipts,
        _from_execution_records,
        _from_session_events,
    ):
        try:
            receipt = lookup(session_id)
        except Exception:
            receipt = None
        if receipt is not None:
            return _completed(receipt, fallback_root=workspace_root)
    return None


def _completed(receipt: FileActionReceipt, *, fallback_root: str) -> FileActionReceipt:
    if receipt.absolute_path:
        return receipt
    root = str(receipt.workspace_root or fallback_root or "").strip()
    relative = str(receipt.relative_path or receipt.file_name or "").strip()
    if not root or not relative:
        return FileActionReceipt(
            file_name=receipt.file_name,
            relative_path=relative,
            workspace_root=root,
            absolute_path="",
            action=receipt.action,
            intent=receipt.intent,
            source=receipt.source,
        )
    return FileActionReceipt(
        file_name=receipt.file_name,
        relative_path=relative,
        workspace_root=root,
        absolute_path=_join_path(root, relative),
        action=receipt.action,
        intent=receipt.intent,
        source=receipt.source,
    )


def _join_path(root: str, relative: str) -> str:
    """Join a recorded root and relative path in the ROOT's own path flavour.

    The Windows lane records ``C:/work`` and the Apple/Linux lane ``/Users/...``; joining with
    `os.path` would render one of them in the other's separator on the wrong host, and this string
    is shown to a user who is about to paste it.
    """
    clean_root = str(root or "").rstrip("/\\")
    clean_relative = str(relative or "").lstrip("/\\")
    if not clean_relative:
        return clean_root
    if re.match(r"^[A-Za-z]:[\\/]", clean_root) or "\\" in clean_root:
        return str(PureWindowsPath(clean_root) / PureWindowsPath(clean_relative))
    return str(PurePosixPath(clean_root) / PurePosixPath(clean_relative))


def _is_absolute(path: str) -> bool:
    text = str(path or "").strip()
    return bool(text) and (text.startswith(("/", "~")) or bool(re.match(r"^[A-Za-z]:[\\/]", text)))


def _file_name_of(path: str) -> str:
    text = str(path or "").strip().rstrip("/\\")
    if not text:
        return ""
    return re.split(r"[\\/]+", text)[-1]


def _receipt_from_path(
    path: str,
    *,
    intent: str,
    action: str,
    source: str,
    workspace_root: str = "",
) -> FileActionReceipt | None:
    clean = str(path or "").strip()
    if not clean or clean.endswith(("/", "\\")):
        return None
    name = _file_name_of(clean)
    if not name or ("." not in name and len(name) < 2):
        return None
    if _is_absolute(clean):
        return FileActionReceipt(
            file_name=name,
            relative_path=clean,
            workspace_root=workspace_root,
            absolute_path=clean,
            action=action,
            intent=intent,
            source=source,
        )
    return FileActionReceipt(
        file_name=name,
        relative_path=clean,
        workspace_root=workspace_root,
        action=action,
        intent=intent,
        source=source,
    )


def _from_mutation_ledger(session_id: str) -> FileActionReceipt | None:
    from core.execution.artifacts import latest_workspace_mutation_for_session

    record = latest_workspace_mutation_for_session(session_id)
    if not record:
        return None
    root = str(record.get("workspace_root") or "").strip()
    intent = str(record.get("intent") or "").strip()
    for change in reversed(list(record.get("changes") or [])):
        if not isinstance(change, dict):
            continue
        if change.get("existed_after") is False:
            continue
        receipt = _receipt_from_path(
            str(change.get("path") or ""),
            intent=intent,
            action=str(change.get("action") or "").strip(),
            source="workspace_mutation_ledger",
            workspace_root=root,
        )
        if receipt is not None:
            return receipt
    return None


def _from_tool_receipts(session_id: str) -> FileActionReceipt | None:
    from core.runtime_continuity import list_runtime_tool_receipts

    for row in list_runtime_tool_receipts(session_id, limit=64):
        intent = str((row or {}).get("tool_name") or "").strip()
        if intent not in _FILE_MUTATION_INTENTS:
            continue
        arguments = dict((row or {}).get("arguments") or {})
        execution = dict((row or {}).get("execution") or {})
        details = dict(execution.get("details") or {})
        candidates = [details.get("path"), details.get("resolved_target")]
        candidates.extend(arguments.get(key) for key in _PATH_ARGUMENT_KEYS)
        for candidate in candidates:
            receipt = _receipt_from_path(
                str(candidate or ""),
                intent=intent,
                action=str(details.get("action") or "").strip(),
                source="runtime_tool_receipt",
            )
            if receipt is not None:
                return receipt
    return None


def _from_execution_records(session_id: str) -> FileActionReceipt | None:
    from core.execution_records import records_for

    for record in reversed(records_for(session_id)):
        intent = str(getattr(record, "intent", "") or "").strip()
        if intent not in _FILE_MUTATION_INTENTS:
            continue
        if not getattr(record, "ok", True):
            continue
        candidates = [getattr(record, "resolved_target", "")]
        arguments = dict(getattr(record, "arguments", {}) or {})
        candidates.extend(arguments.get(key) for key in _PATH_ARGUMENT_KEYS)
        for candidate in candidates:
            receipt = _receipt_from_path(
                str(candidate or ""),
                intent=intent,
                action="",
                source="execution_record",
            )
            if receipt is not None:
                return receipt
    return None


def _from_session_events(session_id: str) -> FileActionReceipt | None:
    from core.runtime_continuity import list_recent_runtime_session_events

    for event in reversed(list_recent_runtime_session_events(session_id, limit=120)):
        row = dict(event or {})
        action_record = dict(row.get("action_record") or {})
        intent = str(row.get("tool_name") or action_record.get("tool_name") or "").strip()
        if intent not in _FILE_MUTATION_INTENTS:
            continue
        if str(row.get("status") or "").strip().lower() in {"failed", "pending_approval"}:
            continue
        parameters = dict(action_record.get("parameters") or {})
        candidates = [action_record.get("target"), row.get("path"), row.get("target_path")]
        candidates.extend(parameters.get(key) for key in _PATH_ARGUMENT_KEYS)
        for candidate in candidates:
            receipt = _receipt_from_path(
                str(candidate or ""),
                intent=intent,
                action="",
                source="runtime_session_event",
            )
            if receipt is not None:
                return receipt
    return None


# ---------------------------------------------------------------------------------- detection

_MAX_MESSAGE_CHARS = 240

# Clause boundaries. The live turns wrap the real question in preamble ("I see, but since we are in
# General chat now - how can i find it in my machine?"), and the preamble's nouns are not the
# question's subject. The ask marker's OWN clause is the one gate 3 inspects.
#
# A sentence-ending period must be followed by whitespace to count. A bare `\.` would cut
# `README.md` in half, and the filename is the one token in this whole family that must survive
# intact.
_CLAUSE_SPLIT_RE = re.compile(r"[,;:?!\n]+|\.\s+|\s+[-\u2013\u2014]+\s+")

# Gate 1. Which folder this CHAT is bound to. `maybe_handle_workspace_identity_request` owns those
# and runs earlier in the front door, but this lane must refuse them on its own account: "where are
# we?" is three familiar words and an ask marker, which is exactly the shape of an elliptical
# location follow-up, and without this it would be answered with the last written file's path.
_WORKSPACE_IDENTITY_RE = re.compile(
    r"\bwhere\s+(?:am\s+i|are\s+we)\b"
    r"|\b(?:which|what)\s+(?:folder|directory|dir|workspace|project)\s+(?:am\s+i|are\s+we|is\s+this)\b",
    re.IGNORECASE,
)

# Gate 1b. The message still has WORK in it. This lane sits above the skill, hive and builder
# lanes, so "create a skill called qa-echo. where do i find it?" would otherwise be answered with
# the path of some earlier file and the skill would never be created.
#
# Only the imperative reading counts, and that is why the verb has to open its clause: "where was
# this file saved?" and "where did you write it?" are the family and contain the same verbs in the
# past tense. `find`/`locate`/`open`/`reveal`/`show`/`tell` are absent by design -- they are how
# this family ASKS, not work it is being given.
_PENDING_WORK_RE = re.compile(
    r"^(?:(?:please|pls|plz|now|then|also|and|ok|okay|can\s+you|could\s+you|would\s+you|"
    r"i\s+need\s+you\s+to|i\s+want\s+you\s+to)\s+)*"
    r"(?:creat\w*|mak\w*|writ\w*|build\w*|add|adds|adding|generat\w*|scaffold\w*|bootstrap\w*|"
    r"install\w*|delet\w*|remov\w*|renam\w*|mov\w*|run|runs|running|edit\w*|updat\w*|append\w*|"
    r"set\s+up|save|store)\b",
    re.IGNORECASE,
)

_WHERE_RE = re.compile(r"\b(?:where|wher|whre|wheer|whereabouts)\b", re.IGNORECASE)

# Gate 2. Every way this family asks for a location.
_LOCATION_ASK_RE = re.compile(
    r"\b(?:where|wher|whre|wheer|whereabouts)\b"
    r"|\b(?:what|which)\s+(?:folder|directory|dir|path|location|place)\b"
    r"|\b(?:show|give|tell|send)\s+me\s+(?:the\s+)?(?:full\s+|exact\s+|absolute\s+)?path\b"
    r"|\b(?:full|exact|absolute|complete|real)\s+path\b"
    r"|\bpath\s+(?:to|of|for|pls|plz|please)\b"
    r"|\bhow\s+(?:can|do|would|could|should)\s+(?:i|we)\s+(?:find|locate|open|get|reach|see)\b"
    r"|\bhow\s+to\s+(?:find|locate|open|reach)\b"
    r"|\b(?:finder|explorer)\s+(?:location|path|window)\b"
    r"|\b(?:open|reveal|show)\s+(?:its\s+|the\s+|that\s+)?(?:location|path|folder|directory)\b"
    r"|\bin\s+(?:finder|explorer)\b"
    r"|^\s*path\s*(?:pls|plz|please)?\s*$",
    re.IGNORECASE,
)

# The reveal half of the family ("open location?", "reveal path?", "find it in Finder").
_REVEAL_ASK_RE = re.compile(
    r"\b(?:open|reveal)\b|\b(?:finder|explorer)\b",
    re.IGNORECASE,
)

# Gate 3a, anchor form 1: the message points at the thing the previous turn produced.
_DEICTIC_FILE_RE = re.compile(
    r"\b(?:this|that|the|its|it'?s|your|ur)\s+(?:new\s+|last\s+|first\s+|same\s+)?"
    r"(?:files?|docs?|documents?|readme|one|thing)\b"
    r"|\bfiles?\b"
    r"|\b(?:find|open|locate|created?|wrote|written|stored?|storred|saved?|put|placed?|made)\s+it\b"
    r"|\bit\s+(?:in|at|on|into|inside|under)\b"
    r"|\bit\s*$",
    re.IGNORECASE,
)

# Gate 3a, anchor form 2: the message credits the action to ME.
_AGENT_ACTION_RE = re.compile(
    r"\b(?:you|u|ya)\s+(?:just\s+|did\s+|already\s+|had\s+)*"
    r"(?:creat\w*|wrote|writ\w*|made|make|sav\w*|put|generat\w*|add\w*|stor\w*|storr\w*|plac\w*|drop\w*)\b"
    r"|\bjust\s+(?:creat\w*|wrote|writ\w*|made|sav\w*|generat\w*|stor\w*|storr\w*)\b",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"[a-z0-9_.\-']+")

# Gate 3's vocabulary: the words this family is built out of. A clause made only of these (plus the
# receipt's own subject tokens) names no competing subject. Anything else -- `python`, `settings`,
# a project name, a person -- is a subject this lane must not answer about, and is refused without
# ever being listed.
_VOCABULARY_GROUPS = (
    # Function words, pronouns, determiners.
    "a an the this that these those it its it's there here one thing same",
    "i me my mine we our us you u ur your ya",
    "is are was were be been being do does did done doesn't didn't don't",
    "can could would should will shall may might must",
    "what which where wher whre wheer whereabouts who how why when whats what's wheres where's",
    "and but or so then now again also just still yet ok okay yeah yep yes no nope",
    "please pls plz thanks thx pretty exactly exact actual actually really precise precisely",
    "to of for from in on at by with under over near about as if",
    "s d ll re ve t m",
    # Asking for a location.
    "tell show give send say know see get got find locate open reveal reach put point",
    # The thing asked about, and the words for where things live.
    "file files doc docs document documents readme readmes name names filename filenames",
    "path paths filepath fullpath location locations place places folder folders",
    "directory directories dir dirs root roots workspace workspaces project projects repo repos",
    # Putting a file somewhere, in every tense this family uses.
    "store stored storred stord storing stores save saved saves saving",
    "write writes wrote written writing create created creates creating creation",
    "make made makes making generate generated generates put puts placed places",
    "add added adds drop dropped output outputs land landed end ended up",
    "go goes going gone went into onto inside within live lives living sit sits sitting",
    "reside resides exist exists sat",
    # This host.
    "machine machines mac macs macbook computer computers laptop laptops pc system systems",
    "disk disks drive drives desktop finder explorer os",
)
_FAMILY_VOCABULARY = frozenset(word for group in _VOCABULARY_GROUPS for word in group.split())

# Fuzzy matching is bounded to the words this family actually turns on, so a foreign subject can
# never be absorbed by looking vaguely like an unrelated vocabulary entry. "storred" -> "stored"
# passes; "general" -> "generate" is not offered the chance.
_TYPO_TOLERANT = (
    "stored",
    "storing",
    "saved",
    "created",
    "creating",
    "wrote",
    "written",
    "where",
    "whereabouts",
    "file",
    "files",
    "folder",
    "directory",
    "path",
    "location",
    "machine",
    "finder",
    "workspace",
    "readme",
    "exactly",
    "please",
)
_TYPO_MIN_LENGTH = 5
_TYPO_CUTOFF = 0.86


def _token_is_familiar(token: str, subject_tokens: tuple[str, ...]) -> bool:
    if token in _FAMILY_VOCABULARY or token in subject_tokens:
        return True
    # A pure number ("v2", "3") carries no competing subject on its own.
    if token.strip(".-_'").isdigit():
        return True
    if len(token) < _TYPO_MIN_LENGTH:
        return False
    if difflib.get_close_matches(token, _TYPO_TOLERANT, n=1, cutoff=_TYPO_CUTOFF):
        return True
    return bool(difflib.get_close_matches(token, subject_tokens, n=1, cutoff=_TYPO_CUTOFF))


def _clauses(text: str) -> list[str]:
    return [part.strip() for part in _CLAUSE_SPLIT_RE.split(text) if part.strip()]


def _ask_clause(text: str) -> str | None:
    """The LAST clause carrying a location-ask marker, which is the one that states the question."""
    for clause in reversed(_clauses(text)):
        if _LOCATION_ASK_RE.search(clause):
            return clause
    return None


def looks_like_location_ask(user_input: str) -> bool:
    """Gates 1 and 2 alone -- cheap, receipt-free, and safe to run on every turn.

    The full decision needs the receipt, and reading the receipt stores costs a database round
    trip. This lane sits high in the front door (above the workspace-identity lane, which was
    measured answering "where was this file stored?" with the name of the bound folder and never
    the file), so the great majority of turns reaching it are not this family at all. They are
    refused here, before anything is read.
    """
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text or len(text) > _MAX_MESSAGE_CHARS:
        return False
    if _WORKSPACE_IDENTITY_RE.search(text):
        return False
    if any(_PENDING_WORK_RE.match(clause) for clause in _clauses(text)):
        return False
    return _ask_clause(text) is not None


def location_followup_kind(
    user_input: str,
    *,
    receipt: FileActionReceipt | None,
) -> str | None:
    """``"reveal"`` / ``"locate"`` when this asks where the last written file went, else ``None``.

    ``receipt`` supplies the subject this family is allowed to be about. Passing ``None`` still
    answers the deictic forms ("where did you create it?") -- those unambiguously refer to a
    previous action of mine, and the caller owes the user an explanation that no such action was
    recorded rather than silence. A message that names a FILE instead is not claimed without a
    receipt to match it against, because without one there is nothing to distinguish it from an
    ordinary question about a file on disk.
    """
    # Gates 1, 1b and 2 live in `looks_like_location_ask` and are called, not restated: the front
    # door runs them separately as the cheap pre-check, and two copies of the same three gates
    # would drift the moment one of them gained a case.
    if not looks_like_location_ask(user_input):
        return None
    text = " ".join(str(user_input or "").strip().lower().split())
    clause = _ask_clause(text)
    if clause is None:
        return None

    subject_tokens = receipt.subject_tokens() if receipt is not None else ()
    tokens = _WORD_RE.findall(clause)
    if not tokens:
        return None
    if any(not _token_is_familiar(token, subject_tokens) for token in tokens):
        return None

    names_receipt_file = bool(subject_tokens) and any(token in subject_tokens for token in tokens)
    points_at_my_action = bool(
        _DEICTIC_FILE_RE.search(clause) or _AGENT_ACTION_RE.search(text)
    )
    # An elliptical ask -- "path pls", "finder location?", "where on mac?" -- names nothing at all.
    # It is a continuation by construction, and gate 3 has already proved it names no other
    # subject, so the only thing it can be continuing is the last action.
    elliptical = len(tokens) <= 4 and bool(
        _WHERE_RE.search(clause) or re.search(r"\b(?:path|location|folder|directory)\b", clause)
    )
    if not (names_receipt_file or points_at_my_action or elliptical):
        return None
    return "reveal" if _REVEAL_ASK_RE.search(clause) else "locate"


# ----------------------------------------------------------------------------------- rendering

_REVEAL_HINT = (
    "To open its folder, use the Files panel — it lists this file with a reveal action. "
    "I do not open windows on your desktop from chat."
)


def render_location_answer(receipt: FileActionReceipt, *, kind: str = "locate") -> str:
    """The reply, built only from what the receipt recorded."""
    verb = {"created": "was created", "updated": "was updated"}.get(
        str(receipt.action or "").strip().lower(), "was written"
    )
    if receipt.has_absolute_path:
        lines = [
            f"{receipt.file_name} {verb} at:",
            "",
            receipt.absolute_path,
            "",
            _provenance_line(receipt),
        ]
        if kind == "reveal":
            lines.extend(["", _REVEAL_HINT])
        return "\n".join(lines)
    return (
        f"I wrote it relative to the active workspace root as {receipt.relative_path}, "
        "but this chat does not expose the absolute root. "
        f"The relative path comes from {_receipt_name(receipt)}; I did not search your disk for it."
    )


def _provenance_line(receipt: FileActionReceipt) -> str:
    return f"That path comes from {_receipt_name(receipt)} — I did not search your disk for it."


def _receipt_name(receipt: FileActionReceipt) -> str:
    intent = str(receipt.intent or "").strip()
    return f"this session's `{intent}` receipt" if intent else "this session's action receipt"


def render_missing_receipt_answer() -> str:
    """What to say when the question is unambiguous and the receipt is not there.

    Not a search, and not a guess. The user is told which fact is missing and asked for the one
    thing that would resolve it.
    """
    return (
        "I have no file-write receipt in this session, so I cannot say where a file went "
        "without guessing — and I will not guess by searching your disk. "
        "Which file do you mean, or which turn wrote it?"
    )


__all__ = [
    "FileActionReceipt",
    "latest_file_action_receipt",
    "location_followup_kind",
    "looks_like_location_ask",
    "render_location_answer",
    "render_missing_receipt_answer",
]
