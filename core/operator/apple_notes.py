"""Apple Notes through the genuine permission-aware native route (AppleScript bridge).

Apple ships NO public Notes API. The supported automation surface on macOS is the
Notes AppleScript dictionary via `osascript` — a real, permission-aware route: the OS
requires the calling app to hold Automation consent for Notes.app, and a denial is a
RECOVERABLE typed state (System Settings > Privacy & Security > Automation), never
bypassed and never re-prompted in a loop.

What this module owns: building the exact AppleScript, running it through the injected
runner (the real runner is `subprocess.run` of `/usr/bin/osascript`), and mapping the
OS's own failure answers to typed states. It never fabricates success: a note exists in
Apple Notes only when the bridge returned a real reference. Tests inject a labelled
method-shaped runner double for command/error-mapping proof; the native run (Notes.app
present + Automation consent granted, note visible in the real app) is the declared
verification gate.

Capability, stated exactly: CREATE only. There is no native read, search or update of
Apple Notes here, so nothing in this runtime can reconcile an ambiguous creation by looking
inside Notes. Delivery bookkeeping (one durable operation per explicit request, reservation
before the bridge is invoked) lives with the caller, ``core.operator.notes``.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

_PERMISSION_GUIDE = (
    "allow this app to control Notes in System Settings > Privacy & Security > "
    "Automation, then ask again"
)


def _applescript_quote(text: str) -> str:
    """Escape text for an AppleScript string literal (backslash and double-quote)."""
    return str(text or "").replace("\\", "\\\\").replace('"', '\\"')


def build_create_note_script(*, title: str, body: str, account: str = "", folder: str = "") -> str:
    """The AppleScript that creates one note in the real Notes app.

    Destination addressing is explicit and optional: a named account (e.g. "iCloud")
    and/or a named folder; defaults are the Notes app's own default account/folder,
    exactly as a user typing into the app gets.
    """
    safe_title = _applescript_quote(title)
    safe_body = _applescript_quote(body)
    location = ""
    if account and folder:
        location = f'folder "{_applescript_quote(folder)}" of account "{_applescript_quote(account)}"'
    elif account:
        location = f'account "{_applescript_quote(account)}"'
    elif folder:
        location = f'folder "{_applescript_quote(folder)}" of default account'
    at = f" at {location}" if location else ""
    return (
        'tell application "Notes"\n'
        f"make new note{at} with properties {{name:\"{safe_title}\", body:\"{safe_body}\"}}\n"
        "end tell"
    )


_OSASCRIPT = "/usr/bin/osascript"

#: The launch failures ``subprocess`` raises from exec, before a child process exists: the script
#: never ran, so no Apple event can have reached Notes. Any other exception escaping the runner may
#: come after launch (decoding the completion, a broken pipe, a read error) and proves nothing.
_LAUNCH_FAILURES = (FileNotFoundError, PermissionError, NotADirectoryError)


def _executable(path: str) -> bool:
    return os.access(path, os.X_OK)


def _classify(stderr: str, returncode: int) -> tuple[str, str]:
    """The bridge's completion -> (typed reason, user-facing detail); ``("", "")`` on success.

    A REFUSAL -- nothing reached Notes, so a new request may try once more -- needs affirmative
    evidence: macOS denied the Apple event (-1743), the Notes app or the addressed folder/account
    could not be resolved before any note existed, or osascript refused to compile the script.
    Everything else that is not success -- an Apple event timeout (-1712), a process killed by a
    signal, an unrecognised error after the script started -- leaves the creation UNKNOWN: the
    event may have been delivered, and the note created, before the failure was reported.
    """
    if returncode == 0:
        return "", ""
    text = str(stderr or "").replace("\u2019", "'")
    lowered = text.lower()
    if returncode < 0:
        return ("bridge_terminated",
                f"the Notes bridge was terminated by signal {-returncode} before it reported an outcome;"
                " the note may or may not have been created.")
    if "-1743" in text or "not authorized" in lowered:
        return "os_permission_denied", f"macOS refused Automation access to Notes; {_PERMISSION_GUIDE}. No note was created."
    if "-1712" in text or "appleevent timed out" in lowered or "apple event timed out" in lowered:
        return ("apple_event_timeout",
                "Notes did not answer the Apple event within its own timeout; the note may or may not have been created.")
    if "application can't be found" in lowered or "-1728" in text or "can't get" in lowered:
        if "can't get note" in lowered or "can’t get note" in lowered:
            # The app answered and the exact-named note does not exist: a typed lookup miss,
            # distinct from the app itself being unaddressable.
            return "note_not_found", "no note with that exact name was found in Notes; nothing was changed."
        return "notes_app_unavailable", "the Notes app could not be addressed on this machine; no note was created."
    if "-2741" in text or "-2740" in text or "syntax error" in lowered:
        return "notes_script_rejected", "osascript refused to compile the Notes script, so it never ran; no note was created."
    return ("bridge_outcome_unknown",
            f"the Notes bridge exited with status {returncode} ({text.strip()[:160] or 'no error text'}) after it started;"
            " whether the note was created is not established.")


def _unknown_after_launch(exc: BaseException) -> dict[str, Any]:
    return {"ok": False, "reason": "bridge_outcome_unknown",
            "detail": f"the Notes bridge failed after it started ({type(exc).__name__}); the note may or may not have been created."}


def create_apple_note(
    *,
    title: str,
    body: str,
    account: str = "",
    folder: str = "",
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Create a note in Apple Notes; the typed result says exactly what happened.

    The default runner executes the real `/usr/bin/osascript`. A successful creation
    returns ok with the bridge's own completion; every failure returns a typed reason
    and NEVER a success-shaped answer.
    """
    import subprocess

    run = runner or subprocess.run
    script = build_create_note_script(title=title, body=body, account=account, folder=folder)
    if runner is None and not _executable(_OSASCRIPT):
        return {"ok": False, "reason": "osascript_unavailable",
                "detail": "osascript is not present on this system, so nothing was sent to Notes; no note was created."}
    try:
        # BOUNDED on purpose: a machine without Automation consent makes macOS hold the
        # call on a dialog. A granted machine answers in well under this bound; an
        # ungranted one is cut off and reported as unknown -- a held consent dialog and a
        # lost reply look the same from here -- and the assistant never re-prompts in a loop.
        completed = run(
            [_OSASCRIPT, "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except _LAUNCH_FAILURES as exc:
        if getattr(exc, "filename", None) not in (None, _OSASCRIPT):
            return _unknown_after_launch(exc)
        return {"ok": False, "reason": "osascript_unavailable",
                "detail": f"the Notes bridge could not be started ({type(exc).__name__}), so nothing was sent to Notes; no note was created."}
    except subprocess.TimeoutExpired:
        # UNCERTAIN, not denied: the dispatch may have completed and lost its reply
        # (or macOS may be holding a consent dialog). A timeout proves NEITHER the
        # note's absence NOR a permission refusal — it must never be reported as one.
        return {"ok": False, "reason": "delivery_unknown", "detail": "the Notes automation did not answer within its bound; the creation may or may not have completed. It is not proven absent, and no permission decision is implied."}
    except Exception as exc:
        return _unknown_after_launch(exc)
    returncode = getattr(completed, "returncode", None)
    if not isinstance(returncode, int) or isinstance(returncode, bool):
        return {"ok": False, "reason": "bridge_outcome_unknown",
                "detail": "the Notes bridge reported no exit status; whether the note was created is not established."}
    reason, detail = _classify(str(getattr(completed, "stderr", "") or ""), returncode)
    if reason:
        return {"ok": False, "reason": reason, "detail": detail}
    reference = str(getattr(completed, "stdout", "") or "").strip()
    if not reference:
        return {"ok": False, "reason": "unverified", "detail": "the bridge completed without a note reference; the creation cannot be claimed either way."}
    return {"ok": True, "reason": "", "detail": "", "note_reference": reference, "destination": "apple_notes"}


#: The scope reader's OWN vocabulary. Only these words are typo-folded, through ``core.typo_fold`` (the one
#: budget), and only where the grammar expects them; a folder or account NAME is never folded.
_SCOPE_NOUNS = {"folder": "folder", "account": "account", "acct": "account"}
_SCOPE_PREPOSITIONS = frozenset({"in", "into", "inside", "within", "under", "from", "of", "on", "to"})
#: An article right after a preposition starts a new phrase: "... Apple Notes in THE Work folder".
_SCOPE_ARTICLES = frozenset({"the", "a", "an", "this", "that"})
#: A possessive can sit inside a name ("the On My Mac account"), so it never ends one.
_SCOPE_POSSESSIVES = frozenset({"my", "our", "your"})
_SCOPE_NAMING_WORDS = frozenset({"called", "named", "titled"})
# What each slot folds. "our" and "my" stay exact: "our" is one insertion from "four" and "hour".
_NOUN_FOLD = tuple(_SCOPE_NOUNS)
_PREPOSITION_FOLD = ("into", "inside", "within", "under", "from")
_ARTICLE_FOLD = ("the", "this", "that")
_POSSESSIVE_FOLD = ("your",)

_SCOPE_TOKEN_RE = re.compile(
    r'"(?P<dq>[^"\n]*)"'
    r"|\u201c(?P<cq>[^\u201d\n]*)\u201d"
    r"|(?<!\w)['\u2018](?P<sq>[^'\u2018\u2019\n]+)['\u2019](?!\w)"
    r"|(?P<word>\w(?:[\w&+\-]|['\u2019.](?=\w))*)"
    r"|(?P<mark>\S)"
)


def _scope_fold(word: str, vocabulary: tuple[str, ...], *, min_len: int) -> str:
    from core.typo_fold import fold_near_miss_tokens

    return fold_near_miss_tokens(word, vocabulary, min_len=min_len)


class _ScopeReader:
    """The folder and account a Notes request names, read from its own words.

    A scope phrase ends in a scope noun ("folder", "account", "acct"). Its name is written before the
    noun ("in the Work folder", "in my iCloud account's Work folder") or after it ("in the folder named
    Work", 'in the folder "Team Q3"'). The name before a noun runs back to the nearest phrase start: a
    preposition followed by an article, or a possessive scope noun. A run bounded instead by a quote,
    punctuation, "Apple Notes" or another scope noun starts at its leftmost preposition. So "the Work
    folder of the iCloud account" holds two phrases, "the On My Mac account" keeps its whole name, and
    "to Apple Notes in my Work folder" names the Work folder.

    Quoted text is data (a title, a payload, a new name) unless it is the name inside a scope phrase
    ('in the "Team Q3" folder'). A capitalised word is a name and is never folded into a function word.
    """

    def __init__(self, text: str) -> None:
        self.tokens: list[tuple[str, str]] = []
        #: spans[i]: where token i sits in the text; a quote's span includes its quote marks.
        self.spans: list[tuple[int, int]] = []
        #: The word tokens `read` took as folder or account names.
        self.named_words: set[int] = set()
        for match in _SCOPE_TOKEN_RE.finditer(text):
            for kind, group in (("quote", "dq"), ("quote", "cq"), ("quote", "sq"), ("word", "word"), ("mark", "mark")):
                value = match.group(group)
                if value is not None:
                    self.tokens.append((kind, value.strip() if kind == "quote" else value))
                    self.spans.append(match.span())
                    break
        count = len(self.tokens)
        self._spelled = [self._spell_noun(index) for index in range(count)]
        self._prepositions = [self._function_word(index, _SCOPE_PREPOSITIONS, _PREPOSITION_FOLD, 4) for index in range(count)]
        self._articles = [self._function_word(index, _SCOPE_ARTICLES, _ARTICLE_FOLD, 3) for index in range(count)]
        self._possessives = [self._function_word(index, _SCOPE_POSSESSIVES, _POSSESSIVE_FOLD, 3) for index in range(count)]
        self._apps = [self._app(index) for index in range(count)]
        # _reaches_noun[i]: plain words from token i onwards lead straight into a scope noun, so a noun just
        # before i is part of that phrase's name ("the Accounts folder", "the older notes folder").
        self._reaches_noun = [False] * (count + 1)
        for index in range(count - 1, -1, -1):
            if self._word(index) and not self._prepositions[index] and not self._naming(index):
                self._reaches_noun[index] = self._spelled[index] is not None or self._reaches_noun[index + 1]

    def _word(self, index: int) -> str:
        if 0 <= index < len(self.tokens) and self.tokens[index][0] == "word":
            return self.tokens[index][1]
        return ""

    def _quote(self, index: int) -> str:
        if 0 <= index < len(self.tokens) and self.tokens[index][0] == "quote":
            return self.tokens[index][1]
        return ""

    def _function_word(self, index: int, exact: frozenset[str], fold: tuple[str, ...], min_len: int) -> bool:
        word = self._word(index)
        if not word:
            return False
        if word.lower() in exact:
            return True
        if word[:1].isupper() and not word.isupper():
            return False
        return _scope_fold(word.lower(), fold, min_len=min_len) in exact

    def _spell_noun(self, index: int) -> tuple[str, bool, bool] | None:
        """(scope, possessive, exact) when token `index` spells a scope noun, as written or as its unique near miss."""
        word = self._word(index).lower()
        possessive = False
        for suffix in ("'s", "\u2019s"):
            if word.endswith(suffix) and len(word) > len(suffix):
                word, possessive = word[: -len(suffix)], True
                break
        if not word:
            return None
        if word in _SCOPE_NOUNS:
            return _SCOPE_NOUNS[word], possessive, True
        folded = _scope_fold(word, _NOUN_FOLD, min_len=4)
        return (_SCOPE_NOUNS[folded], possessive, False) if folded in _SCOPE_NOUNS else None

    def _app(self, index: int) -> bool:
        """'Apple Notes', 'Apple note', 'Notes app', 'Notes.app' name the app, never a folder."""
        word, before, after = (self._word(position).lower() for position in (index, index - 1, index + 1))
        return (word == "notes.app" or (word == "apple" and after in ("note", "notes"))
                or (word in ("note", "notes") and before == "apple")
                or (word == "notes" and after == "app") or (word == "app" and before == "notes"))

    def _naming(self, index: int) -> bool:
        return self._word(index).lower() in _SCOPE_NAMING_WORDS

    def _coordinating(self, index: int) -> bool:
        """Whether token `index` is a coordinating connector of the mint's own grain ("and",
        "also", "plus"), read case-insensitively: the boundary both grains cut at."""
        from core.agent_runtime.answer_coverage import _DEMAND_UNIT_SPLIT_CONNECTORS

        return self._word(index).lower() in _DEMAND_UNIT_SPLIT_CONNECTORS

    def _is(self, flags: list[bool], index: int) -> bool:
        return 0 <= index < len(flags) and flags[index]

    def _noun(self, index: int) -> tuple[str, bool, bool] | None:
        """A scope noun that heads its own phrase (see `_reaches_noun`)."""
        spelled = self._spelled[index] if 0 <= index < len(self.tokens) else None
        if spelled is None or self._apps[index]:
            return None
        if not spelled[1] and self._reaches_noun[index + 1]:
            return None
        return spelled

    def _joined(self, run: list[int]) -> str:
        return " ".join(self.tokens[index][1] for index in run)

    def _before(self, noun: int, *, exact_noun: bool) -> tuple[str, int | None, list[int]] | None:
        """The name written before scope noun `noun`: (name, the quote holding it or None, its word tokens)."""
        quoted = self._quote(noun - 1)
        if quoted:
            intro = noun - 2
            if (self._is(self._prepositions, intro) or self._is(self._articles, intro)
                    or self._is(self._possessives, intro) or (self._noun(intro) or ("", False, False))[1]):
                return quoted, noun - 1, []
            return None
        run: list[int] = []
        index = noun - 1
        boundary = "start"
        while index >= 0:
            if not self._word(index) or self._apps[index]:
                boundary = "edge"
                break
            spelled = self._noun(index)
            if spelled is not None:
                boundary = "possessive" if spelled[1] else "noun"
                break
            if self._articles[index] and self._is(self._prepositions, index - 1):
                boundary = "article"
                break
            run.insert(0, index)
            index -= 1
        if boundary in ("possessive", "article"):
            if run:
                return self._joined(run), None, run
            if boundary == "article" and self._word(index).lower() not in _SCOPE_ARTICLES:
                # "in theo folder": a folded article with nothing after it was the name all along.
                return self._word(index), None, [index]
            return None
        for position, member in enumerate(run):
            if self._prepositions[member]:
                rest = run[position + 1:]
                if rest and (self._articles[rest[0]] or self._possessives[rest[0]]):
                    rest = rest[1:]
                return (self._joined(rest), None, rest) if rest else None
        # No preposition: only a one-word name right after a quote, punctuation, "Apple Notes" or the start,
        # spelled with the exact noun ('... "Plan" work folder', "(Work folder)").
        if boundary == "noun" or not exact_noun:
            return None
        # A dropped preposition after a coordinating connector ("..., and the Work folder, open ..."):
        # the connector phrase-begins the scope exactly where the mint cuts it, so the name is what
        # follows it. A run the connector would empty is a name all along ("Plus account").
        if len(run) > 1 and self._coordinating(run[0]):
            run = run[1:]
        if run and (self._articles[run[0]] or self._possessives[run[0]]):
            run = run[1:]
        if len(run) == 1 and not self._naming(run[0]):
            return self._word(run[0]), None, run
        return None

    def _after(self, noun: int) -> tuple[str, int | None, list[int]] | None:
        """The name written after scope noun `noun`: "in the folder named Work", 'in the folder "Team Q3"'."""
        introduced = self._is(self._prepositions, noun - 1) or (
            (self._is(self._articles, noun - 1) or self._is(self._possessives, noun - 1))
            and self._is(self._prepositions, noun - 2)
        )
        if not introduced:
            return None
        index = noun + 1
        named = self._naming(index)
        if named:
            index += 1
        if self._quote(index):
            return self._quote(index), index, []
        run: list[int] = []
        while self._word(index) and not self._prepositions[index] and not self._apps[index] and not self._naming(index):
            run.append(index)
            index += 1
        # Without a naming word, only a capitalised name follows the noun: "from the folder please" names nothing.
        if not run or not (named or self._word(run[0])[:1].isupper()):
            return None
        return self._joined(run), None, run

    def read(self) -> tuple[str, str, set[int]]:
        """(folder, account, indexes of the quotes that hold a scope name). The first phrase of each scope wins."""
        folder = account = ""
        scope_quotes: set[int] = set()
        named: set[int] = set()
        for index in range(len(self.tokens)):
            noun = None if index in named else self._noun(index)
            if noun is None:
                continue
            scope, possessive, exact = noun
            reading = self._before(index, exact_noun=exact)
            if reading is None and not possessive:
                reading = self._after(index)
            if reading is None:
                continue
            name, quote, words = reading
            named.update(words)
            if quote is not None:
                scope_quotes.add(quote)
            if scope == "folder" and not folder:
                folder = name
            elif scope == "account" and not account:
                account = name
        self.named_words = named
        return folder, account, scope_quotes


def _data_spans(text: str, data: Any) -> list[tuple[int, int]]:
    """Where each value the caller already read as data sits in `text`: its last occurrence not taken by an earlier value."""
    spans: list[tuple[int, int]] = []
    for value in data or ():
        value = str(value or "").strip()
        at = text.rfind(value) if value else -1
        if at >= 0:
            spans.append((at, at + len(value)))
            text = text[:at] + " " * len(value) + text[at + len(value):]
    return spans


def _without_data(text: str, data: Any) -> str:
    """`text` with the last occurrence of each value the caller already read as data blanked out, offsets kept."""
    for start, end in _data_spans(text, data):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def parse_notes_destination(text: str, *, data: Any = ()) -> dict[str, str]:
    """Destination words the user actually wrote: named account/folder or defaults.

    'save a note to Apple Notes in my Work folder' -> folder=Work; 'in the Work folder of the iCloud
    account' -> folder=Work, account=iCloud; 'in teh work fodler' -> folder=work. Deterministic reading of
    explicit words (see `_ScopeReader`); nothing is guessed silently. `data` holds values the caller has
    already read as data from this request (an unquoted payload or new name); they are never read as scope.
    """
    raw = str(text or "")
    wants = bool(re.search(r"\bapple\s+notes\b|\bnotes\.app\b", raw, re.IGNORECASE))
    folder, account, _scope_quotes = _ScopeReader(_without_data(raw, data)).read()
    return {"wants_apple_notes": str(wants), "folder": folder, "account": account}


def quoted_note_values(text: str) -> list[str]:
    """The quoted values of a Notes request that are data -- title, payload, new name -- in order.

    A quote that names a folder or account ('in the "Work" folder') is scope, not data. Apostrophes inside
    words ("account's", "it's") never open or close a quote.
    """
    reader = _ScopeReader(str(text or ""))
    _folder, _account, scope_quotes = reader.read()
    return [value for index, (kind, value) in enumerate(reader.tokens)
            if kind == "quote" and value and index not in scope_quotes]


#: What `words_outside_values` writes over a value: neither a word character nor whitespace, so the words on either side of
#: a value never read as one phrase ('go "the" ahead' holds no "go ahead").
_VALUE_MASK = "\x00"


def words_outside_values(text: str, *, data: Any = ()) -> str:
    """The request's own words: `text` with every value it names written over with `_VALUE_MASK`, offsets kept.

    Masked: each value the caller already read as data (an unquoted title or payload, see `_without_data`), every quoted
    value -- a title, a payload, a new name, or a quoted folder or account name -- and every folder or account name the
    scope reader reads. A word inside a value belongs to that value; the user never said it to the runtime (see
    `core.operator.parser.request_own_words`).
    """
    raw = str(text or "")
    spans = _data_spans(raw, data)
    reader = _ScopeReader(_without_data(raw, data))
    reader.read()
    spans += [reader.spans[index] for index, (kind, _value) in enumerate(reader.tokens)
              if kind == "quote" or index in reader.named_words]
    chars = list(raw)
    for start, end in spans:
        chars[start:end] = _VALUE_MASK * (end - start)
    return "".join(chars)


def run_notes_bridge(script: str, *, runner: Any = None, purpose: str = "the Notes request") -> dict[str, Any]:
    """Execute one Notes AppleScript through the same typed bridge create_apple_note uses.

    Shared so every operation gets the identical permission/timeout/unknown classification.
    """
    import subprocess

    run = runner or subprocess.run
    if runner is None and not _executable(_OSASCRIPT):
        return {"ok": False, "reason": "osascript_unavailable",
                "detail": "osascript is not present on this system, so nothing was sent to Notes."}
    try:
        completed = run([_OSASCRIPT, "-e", script], capture_output=True, text=True, timeout=5)
    except _LAUNCH_FAILURES as exc:
        if getattr(exc, "filename", None) not in (None, _OSASCRIPT):
            return _unknown_after_launch(exc)
        return {"ok": False, "reason": "osascript_unavailable",
                "detail": f"the Notes bridge could not be started ({type(exc).__name__}), so nothing was sent to Notes."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "reason": "delivery_unknown",
                "detail": f"the Notes automation did not answer within its bound; {purpose} may or may not have completed."}
    except Exception as exc:
        return _unknown_after_launch(exc)
    returncode = getattr(completed, "returncode", None)
    if not isinstance(returncode, int) or isinstance(returncode, bool):
        return {"ok": False, "reason": "bridge_outcome_unknown",
                "detail": "the Notes bridge reported no exit status; the outcome is not established."}
    reason, detail = _classify(str(getattr(completed, "stderr", "") or ""), returncode)
    if reason:
        return {"ok": False, "reason": reason, "detail": detail}
    return {"ok": True, "reason": "", "detail": "", "output": str(getattr(completed, "stdout", "") or "")}


#: Field/record separators that can never appear in an AppleScript string concatenation, so a
#: structured listing survives commas, newlines and Unicode in note titles verbatim.
_UNIT_SEP = chr(31)
_RECORD_SEP = chr(30)


def build_list_notes_script() -> str:
    """One line per note: id, title, folder, account, unit-separated; records record-separated.

    Titles are data: a note called "Plans, Q4" must survive the listing as ONE title, so the
    listing is a structured record stream, not a comma-joined name list.
    """
    us, rs = _UNIT_SEP, _RECORD_SEP
    return (
        'tell application "Notes"\n'
        "set output to \"\"\n"
        "repeat with theNote in notes\n"
        "set output to output & (id of theNote) & \"" + us + "\" & (name of theNote) & \"" + us + "\" "
        "& (name of container of theNote) & \"" + us + "\" & (name of container of container of theNote) & \"" + rs + "\"\n"
        "end repeat\n"
        "return output\n"
        "end tell"
    )


def build_read_note_script(*, title: str = "", note_id: str = "") -> str:
    """Read by stable id when known (exact identity); a title read resolves through the listing."""
    if note_id:
        return (
            'tell application "Notes"\n'
            f"set theNote to first note whose id is \"{_applescript_quote(note_id)}\"\n"
            "return body of theNote\n"
            "end tell"
        )
    safe = _applescript_quote(title)
    return (
        'tell application "Notes"\n'
        f"set theNote to first note whose name is \"{safe}\"\n"
        "return body of theNote\n"
        "end tell"
    )


def build_note_name_script(*, note_id: str) -> str:
    """The CURRENT name of one note by stable id: what mutation revalidation compares against."""
    return (
        'tell application "Notes"\n'
        f"set theNote to first note whose id is \"{_applescript_quote(note_id)}\"\n"
        "return name of theNote\n"
        "end tell"
    )


def build_append_note_script(*, title: str = "", text: str = "", note_id: str = "") -> str:
    safe_text = _applescript_quote(text)
    where = f"whose id is \"{_applescript_quote(note_id)}\"" if note_id else f"whose name is \"{_applescript_quote(title)}\""
    return (
        'tell application "Notes"\n'
        f"set theNote to first note {where}\n"
        f"make new paragraph at end of paragraphs of body of theNote with data \"{safe_text}\"\n"
        "end tell"
    )


def build_rename_note_script(*, title: str = "", new_title: str = "", note_id: str = "") -> str:
    safe_new = _applescript_quote(new_title)
    where = f"whose id is \"{_applescript_quote(note_id)}\"" if note_id else f"whose name is \"{_applescript_quote(title)}\""
    return (
        'tell application "Notes"\n'
        f"set theNote to first note {where}\n"
        f"set name of theNote to \"{safe_new}\"\n"
        "end tell"
    )


def build_delete_note_script(*, title: str = "", note_id: str = "") -> str:
    where = f"whose id is \"{_applescript_quote(note_id)}\"" if note_id else f"whose name is \"{_applescript_quote(title)}\""
    return (
        'tell application "Notes"\n'
        f"set theNote to first note {where}\n"
        "delete theNote\n"
        "end tell"
    )


def _not_found_or(bridge: dict[str, Any]) -> dict[str, Any]:
    """First note whose name is X: the app answering 'no such note' is a typed miss."""
    if bridge.get("ok"):
        return bridge
    if bridge.get("reason") == "note_not_found":
        return {"ok": False, "reason": "not_found", "detail": str(bridge.get("detail") or "no note with that exact name was found.")}
    return bridge


def list_apple_notes(*, runner: Any = None) -> dict[str, Any]:
    """Every note as its own record (id, title, folder, account). Titles are verbatim data."""
    bridge = run_notes_bridge(build_list_notes_script(), runner=runner, purpose="the note listing")
    if not bridge.get("ok"):
        return bridge
    notes = []
    for record in bridge["output"].split(_RECORD_SEP):
        fields = record.split(_UNIT_SEP)
        if len(fields) != 4:
            continue
        note_id, title, folder, account = (field.strip("\r\n") for field in fields)
        if note_id:
            notes.append({"id": note_id, "title": title, "folder": folder, "account": account})
    return {**bridge, "notes": notes}


def resolve_note(*, title: str, account: str = "", folder: str, runner: Any = None) -> dict[str, Any]:
    """Resolve a title (optionally scoped to account/folder) to ONE stable note identity.

    A title is not an identity: the same title may exist in two folders or accounts. Zero
    matches is not found; more than one is an ambiguity naming every candidate, never a guess.
    """
    wanted = str(title or "").casefold()
    listing = list_apple_notes(runner=runner)
    if not listing.get("ok"):
        return listing
    matches = notes_in_scope([note for note in listing["notes"] if note["title"].casefold() == wanted],
                             folder=folder, account=account)
    if not matches:
        return {"ok": False, "reason": "not_found",
                "detail": f"no note titled \"{title}\" was found" + (f" in {folder}" if folder else "") + (f" of {account}" if account else "") + "."}
    if len(matches) > 1:
        where = ", ".join(f"\"{note['title']}\" in {note['folder']} ({note['account']}) [{note['id'][:24]}...]" for note in matches[:6])
        return {"ok": False, "reason": "ambiguous",
                "detail": f"{len(matches)} notes share that title; name the folder or use the id: {where}", "matches": matches}
    return {"ok": True, "note": matches[0]}


def notes_in_scope(notes: list[dict[str, Any]], *, folder: str = "", account: str = "") -> list[dict[str, Any]]:
    """The listed notes inside the named folder and/or account; names compare case-insensitively."""
    wanted_folder, wanted_account = str(folder or "").casefold(), str(account or "").casefold()
    return [note for note in notes
            if (not wanted_folder or str(note.get("folder") or "").casefold() == wanted_folder)
            and (not wanted_account or str(note.get("account") or "").casefold() == wanted_account)]


def read_apple_note(*, title: str = "", note_id: str = "", folder: str = "", account: str = "",
                    runner: Any = None) -> dict[str, Any]:
    """One note's body, by stable id or by title resolved inside the named folder/account (``note`` says which)."""
    note: dict[str, Any] = {}
    if note_id:
        bridge = _not_found_or(run_notes_bridge(build_read_note_script(note_id=note_id), runner=runner, purpose="the note read"))
    else:
        resolved = resolve_note(title=title, folder=folder, account=account, runner=runner)
        if not resolved.get("ok"):
            return resolved
        note = resolved["note"]
        bridge = _not_found_or(run_notes_bridge(build_read_note_script(note_id=note["id"]), runner=runner, purpose="the note read"))
    if not bridge.get("ok"):
        return bridge
    body = bridge["output"].strip()
    if not body:
        # A locked/protected note answers with an empty body rather than an error; the content
        # is protected and stays protected: the operation is refused, not fabricated.
        return {"ok": False, "reason": "note_locked_or_empty",
                "detail": "Notes returned no readable body for that note (it may be locked); its content is not exposed."}
    return {**bridge, "body": body, **({"note": note} if note else {})}


def revalidate_note(*, note_id: str, expected_title: str, runner: Any = None) -> dict[str, Any]:
    """The target is still the reviewed target: same stable id, same name as approved."""
    bridge = _not_found_or(run_notes_bridge(build_note_name_script(note_id=note_id), runner=runner, purpose="the revalidation"))
    if not bridge.get("ok"):
        return bridge
    current = bridge["output"].strip()
    if str(expected_title or "").strip() and current.casefold() != str(expected_title).casefold():
        return {"ok": False, "reason": "target_changed",
                "detail": f"that note is now called \"{current}\", not \"{expected_title}\": the reviewed target changed, so nothing was sent."}
    return {"ok": True, "name": current}


def append_apple_note(*, text: str, title: str = "", note_id: str = "", runner: Any = None) -> dict[str, Any]:
    if not str(text or "").strip():
        return {"ok": False, "reason": "invalid_request", "detail": "the text to append is empty"}
    return _not_found_or(run_notes_bridge(build_append_note_script(title=title, text=text, note_id=note_id), runner=runner, purpose="the append"))


def rename_apple_note(*, new_title: str, title: str = "", note_id: str = "", runner: Any = None) -> dict[str, Any]:
    if not str(new_title or "").strip():
        return {"ok": False, "reason": "invalid_request", "detail": "the new name is empty"}
    return _not_found_or(run_notes_bridge(build_rename_note_script(title=title, new_title=new_title, note_id=note_id), runner=runner, purpose="the rename"))


def delete_apple_note(*, title: str = "", note_id: str = "", runner: Any = None) -> dict[str, Any]:
    """Delete one note by stable id (a title is resolved first). Notes moves it to Recently
    Deleted, where macOS keeps it for its own retention period; VOOL cannot extend or shorten that."""
    return _not_found_or(run_notes_bridge(build_delete_note_script(title=title, note_id=note_id), runner=runner, purpose="the deletion"))


def new_note_title(text: str) -> str:
    """The new name a Notes rename asks for: its second quoted data value, or what follows a closing 'to'.

    A quoted folder or account name is scope, never the new name: 'in the "Work" folder to "Plan v2"'.
    """
    quoted = quoted_note_values(text)
    if len(quoted) > 1:
        return quoted[1]
    match = re.search(r"\bto\s+(?:\"([^\"]+)\"|([A-Za-z][\w \-]{1,60}))\s*$", str(text or ""))
    return (match.group(1) or match.group(2)).strip() if match else ""


__all__ = [
    "append_apple_note", "build_append_note_script", "build_create_note_script", "build_delete_note_script",
    "build_list_notes_script", "build_note_name_script", "build_read_note_script", "build_rename_note_script",
    "create_apple_note", "delete_apple_note", "list_apple_notes", "notes_in_scope", "parse_notes_destination",
    "quoted_note_values", "read_apple_note", "rename_apple_note", "resolve_note", "revalidate_note", "run_notes_bridge",
]
