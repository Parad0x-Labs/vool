"""A message sent while a task runs must not break, pause or duplicate that task.

Before this module a chat message that arrived while a turn was still working waited behind it:
the page queued it, the per-session turn gate (`core/session_turn_gate.py`) held it, and it ran as
a NEW turn only after the task had finished. A question waited for the whole task. A correction
("also add tests") never reached the work it was about; it started a second task afterwards. A
typed "stop" was queued like any other text and stopped nothing.

This module is the one authority for what a follow-up does while a task is live in its chat:

* ``stop`` -- an explicit stop/cancel ("stop", "cancel that", "never mind"). Ends the task.
* ``pause`` -- "pause", "hold on", "wait". The task is set aside; "continue" starts it again.
* ``switch`` -- "pause that and do X first", "let's shift focus to X, finish the other thing
  after". The task is set aside, X runs now, and the task is started again right after it.
* ``replace`` -- "forget that, do X instead". The task is stopped for good and X runs now.
* ``status`` -- "what are you doing?", "progress?", "status". Answered at once from the task's
  own progress, without a model call, so it never waits on a busy model.
* ``question`` -- any other question. Answered right away by a side answer that cannot act (no
  tools, no effects) and does not wait on the task's turn gate. The task keeps running.
* ``note`` -- an amendment ("also add tests", "use the blue one instead", "don't email John,
  just draft it"). Attached to the running task, which takes it in before its next action that
  changes anything: an action chosen before the note arrived is not run, the task decides again
  with the note in hand. An action already in flight when the note arrives cannot be recalled,
  and the reply to the user says so.
* ``passthrough`` -- a whole-message command another authority owns (the x402 brake, a full VOOL
  shutdown). It runs as an ordinary turn.

Nothing a follow-up attaches is lost. A note the task never reached before it ended (it finished
first, or it never took another step) is carried into the chat's persisted message queue and runs
next, in order, in the user's own words.

The state is in-process, like `core.live_turns`: a task lives exactly as long as its worker, and a
restart ends the worker. It is keyed by ``(session_id, turn_id)`` -- the client turn id the cancel
registry already uses -- so a note can never reach another chat's task, or a later task in the same
chat.

Classification is deterministic. It reads the message the way a person types it mid-task -- lower
case, chat shorthand ("wat r u doin", "nvm", "plz"), a slipped letter in a key word -- and it is
deliberately conservative about the routes that end work: a stop, a pause, a switch or a replace
needs the whole shape of one, and anything that is not clearly one of the kinds above is a note,
which is always delivered to the task (or run next) in the user's own words.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "FOLLOWUP_NOTES_CONTEXT_KEY",
    "FOLLOWUP_OF_REQUEST_KEY",
    "SIDE_ANSWER_CONTEXT_KEY",
    "SIDE_ANSWER_SNAPSHOT_KEY",
    "active_task",
    "begin_action",
    "begin_task",
    "classify_followup",
    "drain_notes_into_context",
    "end_action",
    "end_task",
    "handle_followup",
    "note_progress",
    "render_prompt_context",
    "reset_followup_state",
    "take_parked_request",
    "task_snapshot",
]

# The running task's delivered notes, cumulative, on its source context (prompt assembly reads it).
FOLLOWUP_NOTES_CONTEXT_KEY = "operator_followup_notes"
# Server-owned: the request id of the task a side answer was asked inside. Rides the side answer's
# transcript row so the reader can show the exchange where it happened.
FOLLOWUP_OF_REQUEST_KEY = "followup_of_request_id"
# Set by the server (never trusted from a caller) on a side answer to a question asked mid-task.
SIDE_ANSWER_CONTEXT_KEY = "followup_side_answer"
# The running task's request and progress, as the side answer's model sees them.
SIDE_ANSWER_SNAPSHOT_KEY = "followup_active_task_snapshot"

_MAX_PROGRESS = 12
_MAX_NOTES = 50
_MAX_NOTE_CHARS = 4000
_MAX_IDEMPOTENCY_KEYS = 200


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── classification ───────────────────────────────────────────────────────────────────────────────

KIND_STOP = "stop"
KIND_PAUSE = "pause"
KIND_SWITCH = "switch"
KIND_REPLACE = "replace"
KIND_STATUS = "status"
KIND_QUESTION = "question"
KIND_NOTE = "note"
KIND_ACK = "ack"
KIND_PASSTHROUGH = "passthrough"

# How people type mid-task. Applied to a lower-cased copy used ONLY for classification: the note
# the task receives, the message that runs next and the transcript keep the user's own words.
_SHORTHAND = {
    "u": "you", "ya": "you", "r": "are", "ur": "your", "wat": "what", "wut": "what", "wht": "what",
    "whats": "what is", "wats": "what is", "whts": "what is", "what's": "what is", "hows": "how is",
    "how's": "how is", "wheres": "where is", "where's": "where is", "doin": "doing", "doin'": "doing",
    "goin": "going", "goin'": "going", "workin": "working", "takin": "taking", "takin'": "taking", "plz": "please", "pls": "please",
    "nvm": "never mind", "nvmd": "never mind", "nevermind": "never mind", "nm": "never mind",
    "rn": "right now", "wyd": "what are you doing", "w8": "wait", "b4": "before", "1st": "first",
    "dont": "don't", "lets": "let's", "im": "i'm", "youre": "you are", "you're": "you are",
    "ok": "ok", "okay": "ok", "k": "ok", "kk": "ok", "sec": "second", "min": "minute",
    "thats": "that", "that's": "that", "its": "it", "it's": "it", "til": "until", "till": "until",
    "gonna": "going to", "wanna": "want to", "tho": "though", "abt": "about", "cuz": "because",
    "sotp": "stop", "stpo": "stop", "stopp": "stop", "stahp": "stop", "stp": "stop", "tsop": "stop",
    "wiat": "wait", "waitt": "wait", "frist": "first", "fisrt": "first", "firts": "first",
}
# Key words a slipped letter must not hide ("cancle", "staus", "progres", "insted", "pasue"). A
# typed word one edit away from one of these is read as it. Short words and real words that sit
# one edit away from a key word ("paste", "about", "forgot") are never touched.
_KEY_WORDS = (
    "cancel", "status", "progress", "instead", "forget", "working", "continue", "resume",
    "finish", "update", "pause", "abort", "focus", "switch", "doing", "later", "before", "after",
    "never", "scrap", "shift",
)
_NOT_A_TYPO = frozenset({
    "paste", "cause", "about", "forgot", "forged", "cancer", "statue", "states", "state", "stats",
    "layer", "latex", "alter", "aster", "lever", "sever", "nevers", "scrape", "scraps", "strap",
    "shirt", "short", "shaft", "sight", "swatch", "stitch", "witch", "focal", "locus", "being",
    "dying", "going", "doings", "updated", "updates", "finished", "resumed", "resumes", "paused",
    "pauses", "working", "worming", "wording", "worked", "instead", "befog", "afters", "rafter",
    "laser", "eater", "water", "hater", "cater", "dater", "abort", "aborts", "amort", "above",
    "abode", "adore", "resale", "result", "results", "presume", "finis", "finite", "finnish",
    "forger", "forges", "forgets", "cancels", "parcel", "chancel", "progress", "regress",
    "status", "stators", "update", "uprate", "update's", "doing", "dozing", "doping", "dosing",
    "later", "after", "before", "never", "scrap", "shift", "switch", "focus", "pause", "finish",
    "resume", "continue", "forget", "cancel", "mater", "rater", "nerve", "fever", "neve", "newer",
    "shit", "swift", "sift", "shifts", "drift", "voice", "focuses", "house", "passe", "parse",
    "pulse", "purse", "posse", "abbot", "aorta", "apart", "alert", "avert", "adopt", "adapt",
})


def _one_edit_apart(a: str, b: str) -> bool:
    """True when `a` becomes `b` by one insert, delete, substitution or adjacent swap."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _fix_word(word: str) -> str:
    mapped = _SHORTHAND.get(word)
    if mapped is not None:
        return mapped
    if len(word) < 5 or len(word) > 9 or word in _NOT_A_TYPO or not word.isalpha():
        return word
    for key in _KEY_WORDS:
        if _one_edit_apart(word, key):
            return key
    return word


_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?|[,.;:!?]")


def _plain_words(text: str) -> list[tuple[str, int, int]]:
    """The message as the patterns below read it, word by word, each with the span of the typed
    text it came from: lower case, shorthand spelled out, slipped key words restored, punctuation
    kept only as single ``,`` separators. Linear in the text."""
    lowered = str(text or "").lower().replace("’", "'").replace("‘", "'")
    lowered = lowered.replace("—", ",").replace("–", ",").replace(" - ", " , ")
    out: list[tuple[str, int, int]] = []
    for match in _TOKEN_RE.finditer(lowered[:_MAX_NOTE_CHARS]):
        token = match.group(0)
        if token in ",.;:!?":
            if out and out[-1][0] != ",":
                out.append((",", match.start(), match.end()))
            continue
        out.extend((word, match.start(), match.end()) for word in _fix_word(token).split())
    while out and out[-1][0] == ",":
        out.pop()
    while out and out[0][0] == ",":
        out.pop(0)
    return out


def _plain(text: str) -> str:
    return " ".join(word for word, _, _ in _plain_words(text))


def _typed_span(text: str, plain: str, start: int, end: int) -> str:
    """The typed words behind ``plain[start:end]`` (a whole-word slice of `_plain(text)`)."""
    words = _plain_words(text)
    first = len(plain[:start].split())
    count = len(plain[start:end].split())
    if count <= 0 or first >= len(words):
        return ""
    last = min(first + count, len(words)) - 1
    return str(text)[words[first][1] : words[last][2]]


# The patterns read `_plain` text: single spaces, `,` as the only separator, so every space is
# matched by exactly one literal and nothing here can go quadratic on a long run of anything.
_LEAD = r"(?:(?:ok|hey|so|and|but|actually|sorry|oh|um|vool|please)(?: ,)? )*"
_TAIL = r"(?: (?:please|now|right now|for now|for a moment|for a second|for a minute|thanks|thank you|vool))*"
# What "it" is when the user means the running task.
_TASK = (
    r"(?:it|that|this|the task|this task|that task|the job|the run|the other (?:thing|one|task|stuff)"
    r"|your (?:current|active|other|old|previous) (?:task|job|work|thing)"
    r"|the (?:current|active|old|previous|first) (?:task|job|work|thing|one)"
    r"|what you are doing|what you were doing|what you are working on|working|all that|all of that|everything)"
)
# Whole-message only. "stop using pandas" is an amendment, "should I stop it?" is a question, and
# "stop everything" / "stop vool" belong to the shutdown brake -- none of them is this stop.
_STOP_RE = re.compile(
    rf"^{_LEAD}(?:(?:/ ?)?(?:stop|cancel|abort|halt|quit|kill|drop|scrap)(?: {_TASK})?|enough|that is enough"
    rf"|never mind(?: {_TASK})?|forget (?:it|that|about it|about that)|leave it|do not bother|don't bother)"
    rf"{_TAIL}$"
)
_PAUSE_RE = re.compile(
    rf"^{_LEAD}(?:(?:pause|park|shelve|hold)(?: {_TASK})?|hold on|hold up|hang on|wait|wait up"
    rf"|(?:just )?(?:a|one) (?:second|moment|minute)|give me a (?:second|moment|minute)"
    rf"|put {_TASK} on hold|set {_TASK} aside|hold that thought)"
    rf"(?: (?:a|one) (?:second|moment|minute|bit))?{_TAIL}$"
)
# "Set the task aside" said as the opening of a longer message: what follows is the new work.
_SWITCH_HEAD_RE = re.compile(
    rf"^{_LEAD}(?:"
    rf"hold that thought|(?:pause|park|shelve|hold|leave)(?: {_TASK})?(?: (?:for now|for later|for a moment|aside|on hold))?"
    rf"|put {_TASK} (?:on hold|aside)|set {_TASK} aside|hold on|hang on|wait"
    rf"|let's (?:shift|switch|change|move) (?:focus|gears|topic|to something else)"
    rf"|(?:shift|switch|change) (?:focus|gears|topic)"
    rf"|(?:do|finish|continue|resume) {_TASK} (?:later|after|afterwards|at the end|last)"
    rf"|before (?:you )?(?:continue|finish|go on|carry on|do that|that)(?: {_TASK})?"
    rf"|(?:but )?first(?: of all)?|real quick|quickly|quick thing|one thing first"
    rf")(?: ,| and| then| and then| but| to| on)* (?P<rest>.+)$"
)
# The same thing said at the end: "check my mail first, then go back to it".
_SWITCH_TAIL_RE = re.compile(
    rf"^(?P<rest>.+?)(?: ,| and| then| and then| but)* (?:"
    rf"(?:then |and |after that |afterwards )?(?:come back to|go back to|get back to|return to|resume|continue|continue with"
    rf"|carry on with|finish|pick up|pick back up) {_TASK}(?: (?:later|after|afterwards|after that|at the end|when you are done))?"
    rf"|(?:do|finish|leave|keep|continue|resume) {_TASK} (?:for )?(?:later|after|afterwards|after that|at the end|last|for the end)"
    rf"|{_TASK} can wait|{_TASK} (?:later|after|afterwards|at the end|last)"
    rf"){_TAIL}$"
)
# "Do this first": a message that says the new thing goes first, anywhere in it.
_SWITCH_FIRST_RE = re.compile(
    rf"\b(?:do (?:this|that) first|this (?:comes |goes )?first|first do|before (?:you )?(?:continue|finish|go on)"
    rf"|then (?:continue|resume|go back|come back|get back|carry on|finish)"
    rf"|{_TASK} (?:at the end|afterwards|after this|after that|can wait)|shift focus|switch focus|change focus"
    rf"|focus on this|now focus on)\b"
)
# The task is thrown away and something else is asked for in the same breath.
_REPLACE_HEAD_RE = re.compile(
    rf"^{_LEAD}(?:"
    rf"(?:stop|cancel|abort|scrap|drop|forget|kill|ditch|skip)(?: about)? {_TASK}"
    rf"|never mind(?: {_TASK})?|forget it|scratch that|change of plans?|new plan|different plan"
    rf"|instead of {_TASK}|stop"
    rf")(?: ,| and| then| and then| but| instead| just| now| please)+ (?P<rest>.+)$"
)
_STATUS_RE = re.compile(
    rf"^{_LEAD}(?:"
    rf"status|progress|update|eta|any (?:progress|update|updates|news|luck)|status update|progress update"
    rf"|(?:what is|what's) (?:the )?(?:status|progress|eta|update|holdup|hold up)"
    rf"|what (?:are|r) you (?:doing|working on|up to|busy with)|what you doing|what is (?:happening|going on|taking so long)"
    rf"|what is it doing|what are you (?:currently|still) (?:doing|working on)"
    rf"|how is it going|how is it coming(?: along)?|how are (?:things|we doing|you doing)|how far(?: along)?(?: are you| is it)?"
    rf"|how (?:much )?long(?:er)?(?: will (?:it|this|that) take| left| more| until (?:it is|you are) done)?"
    rf"|how much (?:longer|more|is left|time(?: left)?)"
    rf"|where are you(?: at| up to| with (?:it|that|this))?|where are we(?: at)?"
    rf"|(?:(?:are|r) )?you (?:still )?(?:working|there|alive|on it|busy|done|finished|stuck|ok)(?: yet| on (?:it|that|this))?"
    rf"|(?:is|was) (?:it|that|this) (?:still )?(?:working|done|finished|stuck|running|going|ready)(?: yet)?"
    rf"|still (?:working|there|going|running|on it)|(?:done|finished|ready) yet|you (?:done|there|still there)"
    rf"|did you (?:finish|get stuck|stop|freeze)|have you (?:finished|started)|anything yet"
    rf")(?: (?:now|so far|on (?:it|that|this)|with (?:it|that|this)|at the moment|right now|currently|yet))*{_TAIL}$"
)
_QUESTION_LEAD_RE = re.compile(
    r"^(?:(?:hey|so|and|btw|ok|quick question|question) (?:, )?)*"
    r"(?:what|why|how|when|where|who|whom|which|whose|is|are|am|was|were"
    r"|(?:do|have) (?:you|i|we|they)|does|did|has|had|will|would|should|shall|could|can|may|might"
    r"|isn't|aren't|doesn't|didn't|won't|wouldn't|shouldn't|can't|couldn't)\b(?! not\b)"
)
# A request phrased as a question is work for the task, not a question for a side answer:
# "can you also add tests?", "could you use the staging bucket instead?".
_REQUEST_AS_QUESTION_RE = re.compile(
    r"^(?:please )?(?:can|could|would|will) you (?:please )?(?:also )?"
    r"(?!tell\b|explain\b|say\b|remind\b|confirm\b|clarify\b|check whether\b)[a-z]"
)
_ADDITIVE_RE = re.compile(r"\b(?:also|too|as well|additionally|in addition)\b")
# Said to nobody in particular: it asks for nothing and changes nothing.
_ACK_RE = re.compile(
    r"^(?:ok|yes|yep|yeah|yup|sure|cool|nice|great|good|perfect|thanks|thank you|thx|ty|got it|alright|fine"
    r"|sounds good|looks good|go on|keep going|carry on|continue|no rush|take your time|np|no problem|lol|haha)"
    r"(?: ,)?(?: (?:ok|thanks|thank you|vool|please|cool|great|good|nice))*$"
)
# Too little to be a task of its own: "first", "pause and", "forget that please".
_EMPTY_REST_RE = re.compile(rf"^(?:{_TASK}|please|now|ok|thanks|,| )*$")


def _passthrough_command(text: str) -> bool:
    """A whole-message command another authority owns: the x402 brake and the VOOL shutdown."""
    try:
        from core import vool_agent_brake as brake
    except Exception:  # pragma: no cover - the brake module ships with the runtime
        return False
    for name in ("_STOPALL_RE", "_STOP_RE", "_START_RE"):
        pattern = getattr(brake, name, None)
        if pattern is not None and pattern.match(text):
            return True
    return False


def _has_work(rest: str | None) -> bool:
    return bool(rest) and not _EMPTY_REST_RE.match(str(rest))


def classify_followup(text: str) -> str:
    """What a message sent while a task runs is. Deterministic; see the module docstring."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return KIND_NOTE
    if _passthrough_command(clean):
        return KIND_PASSTHROUGH
    plain = _plain(clean)
    if not plain:
        return KIND_NOTE
    asked = clean.rstrip(" .!").endswith("?")
    if _ACK_RE.match(plain) and not asked:
        return KIND_ACK
    if _STOP_RE.match(plain) and not asked:
        return KIND_STOP
    if _PAUSE_RE.match(plain) and not asked:
        return KIND_PAUSE
    if _STATUS_RE.match(plain):
        return KIND_STATUS
    request_shaped = bool(_REQUEST_AS_QUESTION_RE.match(plain)) or bool(_ADDITIVE_RE.search(plain))
    weak = _WEAK_SWITCH_HEAD_RE.match(plain)
    after_opener = plain[weak.end():].lstrip(" ,") if weak else plain
    question_shaped = asked or bool(_QUESTION_LEAD_RE.match(plain)) or bool(_QUESTION_LEAD_RE.match(after_opener))
    if question_shaped and not request_shaped and not _SWITCH_FIRST_RE.search(plain):
        return KIND_QUESTION
    replace = _REPLACE_HEAD_RE.match(plain)
    if replace and _has_work(replace.group("rest")) and not _keeps_the_task(replace.group("rest")):
        return KIND_REPLACE
    head = _SWITCH_HEAD_RE.match(plain)
    tail = _SWITCH_TAIL_RE.match(plain)
    rest = ""
    if head and _has_work(head.group("rest")) and _is_switch(plain, head):
        rest = head.group("rest")
    elif tail and _has_work(tail.group("rest")):
        rest = tail.group("rest")
    if rest:
        # "before you continue, what time is it in Tokyo": a question needs no setting aside, it
        # is answered beside the task.
        if _QUESTION_LEAD_RE.match(rest) and not _REQUEST_AS_QUESTION_RE.match(rest):
            return KIND_QUESTION
        return KIND_SWITCH
    return KIND_NOTE


_REST_TRIM_RE = re.compile(r"(?:[\s,;:.!]+(?:first|1st|instead|please|now|for now|right now|real quick|quickly))+[\s.!]*$", re.IGNORECASE)


_REST_LEAD_RE = re.compile(r"^(?:(?:first|1st|now|then|and|but|instead|just|new plan|the new plan is)[\s,;:]+)+", re.IGNORECASE)


def _trim_rest(typed: str) -> str:
    typed = _REST_TRIM_RE.sub("", typed.strip(" ,;:")).strip(" ,;:")
    return _REST_LEAD_RE.sub("", typed).strip(" ,;:")


def new_work_text(text: str) -> str:
    """For a switch or a replace: the part of the message that is the new work, in the words the
    user typed -- "pause that and check my mail first" is "check my mail". Running the whole
    sentence would hand the next turn an instruction about a task it never saw ("pause that",
    "first"). Falls back to the whole message whenever the part cannot be cut out cleanly."""
    clean = " ".join(str(text or "").split())
    plain = _plain(clean)
    for pattern in (_REPLACE_HEAD_RE, _SWITCH_HEAD_RE, _SWITCH_TAIL_RE):
        match = pattern.match(plain)
        if not match or not _has_work(match.group("rest")):
            continue
        typed = _trim_rest(_typed_span(clean, plain, match.start("rest"), match.end("rest")))
        # A second pass: "…, then come back to it" after a head that was already cut.
        tail = _SWITCH_TAIL_RE.match(_plain(typed)) if typed else None
        if tail and _has_work(tail.group("rest")):
            typed = _trim_rest(_typed_span(typed, _plain(typed), tail.start("rest"), tail.end("rest"))) or typed
        if len(typed.split()) >= 2:
            return typed
        break
    return clean


# After "stop that" / "never mind", words that show the task itself goes on in a changed form
# ("stop that and use the other file" is still an amendment only when it names the task's work;
# these openers are the clear cases).
_KEEPS_TASK_RE = re.compile(
    r"^(?:keep going|carry on|continue|go on|finish(?: it| that| up)?$|i changed my mind , (?:keep|continue|carry))\b"
)


def _keeps_the_task(rest: str) -> bool:
    return bool(_KEEPS_TASK_RE.match(str(rest or "")))


# Openers of `_SWITCH_HEAD_RE` that also begin ordinary amendments ("first sort them by date",
# "wait, use the other file", "quickly add a title"). They switch only when the message also says
# the task is to be come back to.
_WEAK_SWITCH_HEAD_RE = re.compile(
    rf"^{_LEAD}(?:hold on|hang on|wait|(?:but )?first(?: of all)?|real quick|quickly|quick thing|one thing first)\b"
)


def _is_switch(plain: str, head: re.Match[str]) -> bool:
    if not _WEAK_SWITCH_HEAD_RE.match(plain):
        return True
    return bool(_SWITCH_FIRST_RE.search(plain) or _SWITCH_TAIL_RE.match(plain))


# ── live task registry ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Note:
    note_id: str
    seq: int
    text: str
    created_at: str
    conflict: bool = False
    # pending -> delivered | carried_over
    status: str = "pending"
    delivered_at: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "seq": self.seq,
            "text": self.text,
            "conflict": self.conflict,
            "status": self.status,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
        }


@dataclass
class _Task:
    session_id: str
    turn_id: str
    request_text: str
    started_at: str
    started_seq: int
    notes: list[_Note] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    next_note_seq: int = 1
    # The transcript identity of the task's own turn, so a follow-up's record can be shown inside it.
    request_id: str = ""
    # The action that changes something and is running right now ("" between actions).
    action_in_flight: str = ""
    # Set when a follow-up ended the task itself: its undelivered notes are not run on their own.
    ended_by: str = ""


_LOCK = threading.Lock()
_TASKS: dict[tuple[str, str], _Task] = {}
# Per-session results of handled follow-ups, keyed by the client's idempotency key, so a resend or
# reconnect replay of one follow-up never attaches the same note twice.
_HANDLED: dict[str, dict[str, dict[str, Any]]] = {}
_START_SEQ = 0
# Per-session request of a task the user set aside with a bare "pause": "continue" starts it again.
_PARKED: dict[str, str] = {}
_RESUME_RE = re.compile(
    r"^(?:ok (?:, )?)?(?:please )?(?:continue|resume|go on|carry on|keep going|go ahead|unpause|pick (?:it )?(?:back )?up"
    r"|pick up where you left off|back to (?:it|that|the task)|continue (?:it|that|the task|with (?:it|that))"
    r"|resume (?:it|that|the task))(?: please| now)*$"
)


def _key(session_id: str, turn_id: str) -> tuple[str, str]:
    return (str(session_id or "").strip(), str(turn_id or "").strip())


def begin_task(session_id: str, turn_id: str, request_text: str, *, request_id: str = "") -> None:
    """Record a task that is now live in its chat. Called beside `live_turns.register_turn` for a
    real (non side-answer) turn. Idempotent for the same key."""
    global _START_SEQ
    key = _key(session_id, turn_id)
    if not key[0] or not key[1]:
        return
    with _LOCK:
        if key in _TASKS:
            return
        _START_SEQ += 1
        _TASKS[key] = _Task(
            session_id=key[0],
            turn_id=key[1],
            request_text=str(request_text or "")[:_MAX_NOTE_CHARS],
            started_at=_utcnow(),
            started_seq=_START_SEQ,
            request_id=str(request_id or "").strip(),
        )
        # A new task in the chat is the user moving on: an older "paused" one is no longer what
        # "continue" means -- unless this IS that task being started again.
        if _PARKED.get(key[0]) and _PARKED[key[0]].strip() != str(request_text or "").strip():
            _PARKED.pop(key[0], None)


def active_task(session_id: str) -> dict[str, Any] | None:
    """The live task of this chat (the most recently started one), or None."""
    clean = str(session_id or "").strip()
    if not clean:
        return None
    with _LOCK:
        task = _latest_task_locked(clean)
        return _task_view(task) if task is not None else None


def _latest_task_locked(session_id: str) -> _Task | None:
    candidates = [task for (sid, _), task in _TASKS.items() if sid == session_id]
    if not candidates:
        return None
    return max(candidates, key=lambda task: task.started_seq)


def _task_view(task: _Task) -> dict[str, Any]:
    return {
        "session_id": task.session_id,
        "turn_id": task.turn_id,
        "request_text": task.request_text,
        "started_at": task.started_at,
        "request_id": task.request_id,
        "action_in_flight": task.action_in_flight,
        "progress": list(task.progress),
        "notes": [note.public() for note in task.notes],
    }


def note_progress(session_id: str, turn_id: str, summary: str) -> None:
    """Keep the task's latest progress lines, so a side question about the task can be answered."""
    clean = " ".join(str(summary or "").split())[:240]
    if not clean:
        return
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return
        if task.progress and task.progress[-1] == clean:
            return
        task.progress.append(clean)
        del task.progress[:-_MAX_PROGRESS]


def task_snapshot(session_id: str) -> dict[str, Any] | None:
    """What a side answer may know about the running task: its request, progress and notes."""
    return active_task(session_id)


def end_task(
    session_id: str,
    turn_id: str,
    *,
    enqueue: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Release a task whose worker has finished, on every exit path.

    Every note the task never picked up (still pending, or still waiting for the user's yes) is
    carried into the chat's persisted message queue in order, so it runs next as the user's own
    message instead of being lost. Returns the carried notes. Idempotent.
    """
    with _LOCK:
        task = _TASKS.pop(_key(session_id, turn_id), None)
        if task is None:
            return []
        undelivered = [note for note in task.notes if note.status == "pending"]
        if task.ended_by in {KIND_STOP, KIND_REPLACE}:
            # The user threw the task away: a change to it has nothing left to change.
            for note in undelivered:
                note.status = "dropped"
            undelivered = []
        for note in undelivered:
            note.status = "carried_over"
        carried = [note.public() for note in undelivered]
    if not carried:
        return []
    enqueue_fn = enqueue or _default_enqueue
    for note in carried:
        try:
            enqueue_fn(
                session_id=task.session_id,
                payload={"text": note["text"], "followup_carried_from_turn": task.turn_id},
                idempotency_key=f"followup-{note['note_id']}",
            )
        except Exception:
            import logging

            logging.getLogger("vool.followup").exception(
                "could not carry an undelivered follow-up note into the chat queue (session=%s)",
                task.session_id,
            )
    return carried


def _default_enqueue(**kwargs: Any) -> Any:
    from core.runtime_continuity import enqueue_message

    return enqueue_message(**kwargs)


def drain_notes_into_context(source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Hand the running task the notes it has not seen yet, at a step boundary.

    Newly delivered notes are appended (in order) to ``source_context[FOLLOWUP_NOTES_CONTEXT_KEY]``,
    which prompt assembly renders on every later model call of the turn. Returns only the notes
    delivered by THIS call, so the caller can announce them once.
    """
    if not isinstance(source_context, dict) or source_context.get(SIDE_ANSWER_CONTEXT_KEY):
        return []
    session_id = str(source_context.get("runtime_session_id") or source_context.get("session_id") or "").strip()
    turn_id = str(source_context.get("cancel_turn_id") or "").strip()
    if not session_id or not turn_id:
        return []
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return []
        fresh: list[dict[str, Any]] = []
        now = _utcnow()
        for note in task.notes:
            if note.status == "pending":
                note.status = "delivered"
                note.delivered_at = now
                fresh.append(note.public())
    if fresh:
        existing = [dict(item) for item in list(source_context.get(FOLLOWUP_NOTES_CONTEXT_KEY) or []) if isinstance(item, dict)]
        source_context[FOLLOWUP_NOTES_CONTEXT_KEY] = existing + fresh
    return fresh


def begin_action(source_context: dict[str, Any] | None, action_name: str) -> list[dict[str, Any]]:
    """The task is about to run an action that changes something. Called by the tool loop for
    every call that is not declared read-only, immediately before it runs.

    One atomic decision under the lock: when a note is waiting, the notes are handed over exactly
    as `drain_notes_into_context` hands them and returned -- the caller must NOT run the action
    it had chosen, and decides again with the notes in hand. When none is waiting the action is
    marked in flight and ``[]`` is returned; a note that arrives from this moment on is told the
    action was already under way. `end_action` clears the mark.
    """
    if not isinstance(source_context, dict) or source_context.get(SIDE_ANSWER_CONTEXT_KEY):
        return []
    session_id = str(source_context.get("runtime_session_id") or source_context.get("session_id") or "").strip()
    turn_id = str(source_context.get("cancel_turn_id") or "").strip()
    if not session_id or not turn_id:
        return []
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return []
        fresh: list[dict[str, Any]] = []
        now = _utcnow()
        for note in task.notes:
            if note.status == "pending":
                note.status = "delivered"
                note.delivered_at = now
                fresh.append(note.public())
        if not fresh:
            task.action_in_flight = " ".join(str(action_name or "").split())[:80] or "action"
    if fresh:
        existing = [dict(item) for item in list(source_context.get(FOLLOWUP_NOTES_CONTEXT_KEY) or []) if isinstance(item, dict)]
        source_context[FOLLOWUP_NOTES_CONTEXT_KEY] = existing + fresh
    return fresh


def end_action(source_context: dict[str, Any] | None) -> None:
    """The action `begin_action` admitted has returned (on every exit path)."""
    if not isinstance(source_context, dict):
        return
    session_id = str(source_context.get("runtime_session_id") or source_context.get("session_id") or "").strip()
    turn_id = str(source_context.get("cancel_turn_id") or "").strip()
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is not None:
            task.action_in_flight = ""


def take_parked_request(session_id: str, text: str) -> str:
    """The request of the task this chat's user paused, when `text` asks to go on with it
    ("continue", "resume", "go on"); "" otherwise. Taking it clears it."""
    clean = str(session_id or "").strip()
    if not clean or not _RESUME_RE.match(_plain(text)):
        return ""
    with _LOCK:
        return _PARKED.pop(clean, "")


# ── the follow-up door ───────────────────────────────────────────────────────────────────────────


def _reply(route: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "route": route, "message": message, **extra}


def handle_followup(
    session_id: str,
    text: str,
    *,
    idempotency_key: str = "",
    cancel: Callable[[str, str], str] | None = None,
    enqueue: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Decide what one follow-up does to the chat's live task, and do the part that is not a turn.

    Routes:
      ``run_now``      no task is live: run the message as an ordinary turn.
      ``passthrough``  a command another authority owns: run it as an ordinary turn.
      ``answer``       a question: run a side answer (``followup_mode = side_answer``).
      ``status``       what the task is doing right now, answered here.
      ``ack``          "ok" / "thanks": nothing to do, the task carries on.
      ``note_added``   an amendment: the task takes it in before its next action.
      ``stopped``      the task was stopped.
      ``paused``       the task was set aside; "continue" starts it again.
      ``switched``     the task was set aside, this message runs now, the task runs again after.
      ``replaced``     the task was stopped and this message runs now instead.

    ``switched`` and ``replaced`` put the message (and, for ``switched``, the task's own request
    behind it) into the chat's persisted queue; the caller runs the queue as it does after any turn.
    """
    clean_session = str(session_id or "").strip()
    raw_text = str(text or "").strip()
    idem = str(idempotency_key or "").strip()[:120]
    if not clean_session or not raw_text:
        return {"ok": False, "error": "session_id and text are required"}
    if len(raw_text) > _MAX_NOTE_CHARS:
        return {"ok": False, "error": f"a follow-up is limited to {_MAX_NOTE_CHARS} characters"}
    if idem:
        with _LOCK:
            previous = _HANDLED.get(clean_session, {}).get(idem)
        if previous is not None:
            return {**previous, "replayed": True}
    result = _handle_followup_once(clean_session, raw_text, cancel=cancel, enqueue=enqueue, idem=idem)
    if idem and result.get("ok"):
        with _LOCK:
            seen = _HANDLED.setdefault(clean_session, {})
            seen[idem] = dict(result)
            while len(seen) > _MAX_IDEMPOTENCY_KEYS:
                seen.pop(next(iter(seen)))
    return result


def _preview(text: str, limit: int = 140) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _elapsed_words(started_at: str) -> str:
    try:
        seconds = int((datetime.now(timezone.utc) - datetime.fromisoformat(started_at)).total_seconds())
    except Exception:
        return ""
    if seconds < 5:
        return "just now"
    if seconds < 90:
        return f"{seconds} seconds ago"
    minutes = seconds // 60
    return f"{minutes} minutes ago" if minutes < 90 else f"{minutes // 60} hours ago"


def _status_message(view: dict[str, Any]) -> str:
    """What the task is doing, from its own record. No model call, no guess about time left."""
    lines = [f"Still working on: “{_preview(view.get('request_text') or '')}”"]
    started = _elapsed_words(str(view.get("started_at") or ""))
    if started:
        lines[0] += f" (started {started})."
    progress = [str(item) for item in list(view.get("progress") or []) if str(item).strip()]
    in_flight = str(view.get("action_in_flight") or "").strip()
    if in_flight:
        lines.append(f"Right now: running {_action_words(in_flight)}.")
    elif progress:
        lines.append(f"Right now: {_preview(progress[-1], 120)}.")
    else:
        lines.append("Right now: getting started.")
    notes = [n for n in list(view.get("notes") or []) if isinstance(n, dict)]
    waiting = [n for n in notes if n.get("status") == "pending"]
    if notes:
        taken = len(notes) - len(waiting)
        lines.append(
            f"Your changes: {taken} taken in"
            + (f", {len(waiting)} waiting for the next step." if waiting else ".")
        )
    lines.append("I can't tell how long is left. You can keep typing: I'll take changes in as I go.")
    return "\n\n".join(lines)


def _action_words(name: str) -> str:
    return " ".join(str(name or "").replace(".", " ").replace("_", " ").split()) or "an action"


def _handle_followup_once(
    session_id: str,
    text: str,
    *,
    cancel: Callable[[str, str], str] | None,
    enqueue: Callable[..., Any] | None,
    idem: str,
) -> dict[str, Any]:
    with _LOCK:
        task = _latest_task_locked(session_id)
        view = _task_view(task) if task else None
    if task is None or view is None:
        return _reply("run_now", "")
    turn_id = str(view["turn_id"])
    base = {"turn_id": turn_id, "task_request_id": str(view.get("request_id") or "")}
    kind = classify_followup(text)
    if kind == KIND_PASSTHROUGH:
        return _reply("passthrough", "", kind=kind, **base)
    if kind == KIND_QUESTION:
        return _reply("answer", "", kind=kind, **base)
    if kind == KIND_STATUS:
        return _reply("status", _status_message(view), kind=kind, **base)
    if kind == KIND_ACK:
        return _reply("ack", "Still on it.", kind=kind, **base)
    if kind == KIND_NOTE:
        return _add_note(session_id, turn_id, text, base=base)

    # The routes that end the running task. All four stop it the same way the Stop button does.
    cancel_fn = cancel or _default_cancel
    request_text = str(view.get("request_text") or "")
    if kind in {KIND_PAUSE, KIND_SWITCH, KIND_REPLACE, KIND_STOP}:
        with _LOCK:
            live = _TASKS.get(_key(session_id, turn_id))
            if live is not None:
                live.ended_by = kind
        state = cancel_fn(session_id, turn_id)
        if state not in {"cancelled", "already_cancelled"}:
            # The task ended on its own in the meantime: nothing to stop, the message is a turn.
            with _LOCK:
                live = _TASKS.get(_key(session_id, turn_id))
                if live is not None:
                    live.ended_by = ""
            return _reply("run_now", "", cancel_state=state, kind=kind, **base)
        in_flight = str(view.get("action_in_flight") or "")
        caveat = (
            f" One step ({_action_words(in_flight)}) was already under way when this arrived and may have finished."
            if in_flight
            else ""
        )
        if kind == KIND_STOP:
            with _LOCK:
                _PARKED.pop(session_id, None)
            return _reply("stopped", "Stopped." + caveat, cancel_state=state, kind=kind, **base)
        if kind == KIND_PAUSE:
            with _LOCK:
                _PARKED[session_id] = request_text
            return _reply(
                "paused",
                f"Paused: “{_preview(request_text, 100)}”. Say “continue” and I'll start it again."
                + caveat,
                cancel_state=state, kind=kind, **base,
            )
        enqueue_fn = enqueue or _default_enqueue
        key = idem or uuid.uuid4().hex[:16]
        try:
            enqueue_fn(
                session_id=session_id,
                payload={
                    "text": new_work_text(text), "typed_text": text,
                    "followup_kind": kind, "followup_after_turn": turn_id,
                },
                idempotency_key=f"followup-run-{key}",
            )
            if kind == KIND_SWITCH and request_text.strip():
                enqueue_fn(
                    session_id=session_id,
                    payload={"text": request_text, "followup_kind": "resume", "followup_resumes_turn": turn_id},
                    idempotency_key=f"followup-resume-{key}",
                )
        except Exception:
            import logging

            logging.getLogger("vool.followup").exception(
                "could not queue a follow-up after stopping the task (session=%s)", session_id
            )
            return {
                "ok": False,
                "error": "The running task was stopped, but your message could not be queued. Please send it again.",
                "task_stopped": True,
                **base,
            }
        if kind == KIND_SWITCH:
            return _reply(
                "switched",
                f"Set aside: “{_preview(request_text, 100)}”. Doing this first, then starting that again."
                + caveat,
                cancel_state=state, kind=kind, **base,
            )
        return _reply("replaced", "Stopped that. Doing this instead." + caveat, cancel_state=state, kind=kind, **base)
    return _add_note(session_id, turn_id, text, base=base)


def _add_note(session_id: str, turn_id: str, text: str, *, base: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return _reply("run_now", "")
        if len(task.notes) >= _MAX_NOTES:
            return {"ok": False, "error": f"this task already holds {_MAX_NOTES} notes"}
        note = _Note(
            note_id=f"note-{uuid.uuid4().hex[:16]}",
            seq=task.next_note_seq,
            text=text,
            created_at=_utcnow(),
        )
        task.next_note_seq += 1
        task.notes.append(note)
        public = note.public()
        in_flight = task.action_in_flight
    message = "Got it. I'll take that in before my next step; if I finish first, it runs right after."
    if in_flight:
        message = (
            f"Got it, but one step ({_action_words(in_flight)}) was already under way when this arrived, "
            "so that step may be done before your change counts. Everything after it follows your change."
        )
    return _reply("note_added", message, kind=KIND_NOTE, note=public, action_in_flight=in_flight, **base)


def _default_cancel(session_id: str, turn_id: str) -> str:
    from core.live_turns import request_cancel

    return request_cancel(session_id, turn_id)


# ── prompt context ───────────────────────────────────────────────────────────────────────────────


def render_prompt_context(source_context: dict[str, Any] | None) -> str:
    """The context block a model call sees about follow-ups, or "" when there is none.

    For the running task: the notes the user added while it worked. For a side answer: what the
    running task is and how far it got, and that this message must not start or redo it.
    """
    context = source_context or {}
    if context.get(SIDE_ANSWER_CONTEXT_KEY):
        snapshot = context.get(SIDE_ANSWER_SNAPSHOT_KEY)
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        lines = [
            "A task is still running in this chat. The user's message is a side question asked while "
            "it works. Answer it directly and briefly. Do not start, repeat or redo the running task, "
            "and do not claim it has finished: it continues on its own and reports its own result.",
        ]
        request = str(snapshot.get("request_text") or "").strip()
        if request:
            lines.append(f"The running task was asked: {request[:1200]}")
        progress = [str(item) for item in list(snapshot.get("progress") or []) if str(item).strip()]
        if progress:
            lines.append("Its progress so far (oldest first): " + " | ".join(progress[-_MAX_PROGRESS:]))
        else:
            lines.append("It has not reported a step yet.")
        notes = [
            dict(item) for item in list(snapshot.get("notes") or []) if isinstance(item, dict)
        ]
        if notes:
            lines.append(
                "Notes the user attached to it: "
                + " | ".join(f"{n.get('text', '')} ({n.get('status', '')})" for n in notes)
            )
        return "\n".join(lines)
    notes = [dict(item) for item in list(context.get(FOLLOWUP_NOTES_CONTEXT_KEY) or []) if isinstance(item, dict)]
    if not notes:
        return ""
    rendered = "\n".join(f"{index}. {str(note.get('text') or '').strip()}" for index, note in enumerate(notes, 1))
    return (
        "While you were working on this request, the user added these notes (oldest first). They are "
        "part of the request now and they win over the original wording: apply them to everything "
        "you have not done yet and reflect them in your answer. If a note says not to do something "
        "(send, pay, delete, post, book), do not do it. If a note contradicts work you already "
        "finished, do not undo that work on your own; say plainly what was already done and ask the "
        "user before undoing anything.\n"
        f"{rendered}"
    )


def reset_followup_state() -> None:
    """Tests only -- a process with live tasks must never call this."""
    global _START_SEQ
    with _LOCK:
        _TASKS.clear()
        _HANDLED.clear()
        _PARKED.clear()
        _START_SEQ = 0
