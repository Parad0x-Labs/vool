from __future__ import annotations

import random
import re
import zoneinfo
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.hive_activity_tracker import note_smalltalk_turn, session_hive_state
from core.onboarding import get_agent_display_name
from core.persistent_memory import recent_conversation_events
from core.runtime_continuity import list_runtime_tool_receipts
from core.runtime_execution_tools import execute_runtime_tool
from core.task_router import evaluate_direct_math_request, evaluate_word_math_request
from core.time_authority import CLOCK
from core.user_preferences import load_preferences, user_address

_UTILITY_TIMEZONE_ALIASES = {
    "vilnius": ("Europe/Athens", "Vilnius"),
    "lithuania": ("Europe/Athens", "Vilnius"),
    "europe/vilnius": ("Europe/Athens", "Vilnius"),
}
_CONTEXTUAL_TIME_FOLLOWUP_PATTERNS = (
    re.compile(r"\b(?:and\s+)?(?:now\s+)?there\b"),
    re.compile(r"\bwhat\s+about\s+there\b"),
    re.compile(r"\b(?:what(?:'s| is)\s+)?time\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+there\b"),
    re.compile(r"\bwhat\s+where(?:'s|s)?\s+is\s+in\b"),
)
# A continuation of a clock turn is still a clock turn. Measured on the installed build: "what time
# is now?" answered locally in ~23ms, and the very next message "and date?" was billed
# `cloud | nemotron | 2,078 tok` — a provider round trip for a fact the runtime had just supplied.
#
# The session already records that the previous turn was a utility/clock answer, so the continuation
# is decidable without a model. Bounded deliberately: only a SHORT fragment, and only when the last
# turn really was a clock answer. "and date?" continues a clock turn; "and what did Caesar do on
# that date?" is a question for the model, which is why length and shape both matter.
_RUNTIME_FACT_FOLLOWUP_RE = re.compile(
    # {0,2} because people stack lead-ins: "ok and date?", "then also time?".
    r"^(?:(?:and|also|plus|ok(?:ay)?|then|what\s+about|how\s+about|now)\s*){0,2}"
    r"(?:the\s+|whats?\s+the\s+|whats?\s+)?"
    r"(?:date|time|day|timezone|time\s*zone|tz|seconds?|year|month|week)"
    r"(?:\s+(?:now|today|please|too|as\s+well|again))?"
    r"\s*[?!.]*$",
    re.IGNORECASE,
)
_RUNTIME_FACT_FOLLOWUP_MAX_CHARS = 40


def is_runtime_fact_followup(text: str, *, recent_utility_kind: str) -> bool:
    """True when a short fragment continues a clock answer the runtime just gave.

    Requires BOTH signals. The wording alone is not enough — "date" appears in plenty of ordinary
    sentences — and the prior-turn state alone is not enough either, since the next message after a
    clock answer is usually about something else entirely.
    """

    if str(recent_utility_kind or "").strip().lower() not in {"time", "date", "datetime"}:
        return False
    fragment = " ".join(str(text or "").split()).strip()
    if not fragment or len(fragment) > _RUNTIME_FACT_FOLLOWUP_MAX_CHARS:
        return False
    return bool(_RUNTIME_FACT_FOLLOWUP_RE.match(fragment))


_TIME_FOLLOWUP_EXCLUSION_MARKERS = (
    "capital",
    "country",
    "population",
    "weather",
    "forecast",
    "date",
    "calendar",
    "meeting",
    "email",
    "hive",
    "task",
    "tasks",
    "queue",
    "work",
)
_FILE_TOKEN = r"[A-Za-z0-9_./-]+\.(?:toml|json|ya?ml|md|txt|py|ts|tsx|js|jsx)"
_WORKSPACE_READ_FILE_RE = re.compile(rf"(?P<path>{_FILE_TOKEN})")
# A named file with a MUTATION intent is not a read -- defer to the writer/builder lanes.
_WORKSPACE_MUTATION_INTENT_RE = re.compile(
    r"\b(?:creat|mak|writ|edit|modif|renam|mov|delet|remov|append|replac|updat|sav|generat|"
    r"insert|patch|overwrit|add)\w*\b",
    re.IGNORECASE,
)

# An explicit request for the BYTES. Its presence suppresses every stand-down below: "read
# notes.txt and todo.txt" names two files joined by "and" and is still two reads, not a comparison.
_EXPLICIT_READ_VERB_RE = re.compile(
    r"\b(?:read|cat|open|print|display|output|view|inspect|dump|tail|head|show)\b"
    r"|\bcontents?\s+of\b"
    r"|\bgive\s+me\b",
    re.IGNORECASE,
)

# Frames that are never a way to ask for a file in THIS folder. Nobody asks for `notes.txt` by
# saying "what is the difference between notes.txt and ..." -- these decide on their own.
_FILE_COMPARISON_FRAME_RE = re.compile(
    r"\bdifferences?\s+between\b"
    r"|\bhow\s+(?:is|are|do|does)\b.+\bdiffer\b"
    r"|\bcompare[ds]?\b|\bcomparison\s+(?:of|between)\b"
    r"|\b(?:vs\.?|versus)\b"
    r"|\bpros\s+and\s+cons\b"
    r"|\bwhy\s+(?:would|do|does|should)\s+(?:someone|anyone|you|i|we|one|people|anybody|somebody|developers?|a\s+\w+)\b"
    r"|\bwhen\s+(?:should|would|do|does)\s+(?:i|we|you|someone|one)\s+"
    r"(?:use|choose|pick|prefer|reach|need|want|go)\b"
    r"|\bwhat(?:'?s| is)\s+the\s+(?:point|purpose)\s+of\b"
    r"|\bwhat\s+does\s+an?\s+\S+\s+do\b"
    r"|\bwhat(?:'?s| is)\s+an?\s+\S+\s+(?:for|used\s+for)\b",
    re.IGNORECASE,
)

# Frames that ask how something WORKS. Not conclusive alone -- "explain how the retry logic works
# in worker.py" is a genuine read of worker.py -- so these are paired with a generic mention below.
_FILE_EXPLANATION_FRAME_RE = re.compile(
    r"\b(?:explain|describe|teach\s+me|walk\s+me\s+through"
    r"|how\s+(?:do|does|did|would|should)"
    r"|what(?:'?s|\s+is|\s+are|\s+do|\s+does)"
    r"|why|when)\b",
    re.IGNORECASE,
)

# The filename used GENERICALLY, as a KIND of file rather than an object in the bound folder:
# "a setup.py", "an __init__.py", "any pyproject.toml", "setup.py files". Nobody says "a" about the
# one file sitting in the folder this chat is bound to.
_GENERIC_FILE_MENTION_RE = re.compile(
    rf"\b(?:a|an|any|some|every|each|typical|standard|plain|normal|regular)\s+{_FILE_TOKEN}\b"
    rf"|\b{_FILE_TOKEN}\s+files?\b",
    re.IGNORECASE,
)

# Two DISTINCT filenames joined by and/or/vs. A question shaped like this compares two kinds of
# file; it is never a request to read one of them.
_JOINED_FILE_PAIR_RE = re.compile(
    rf"\b{_FILE_TOKEN}\s*,?\s+(?:and|or|vs\.?|versus)\s+{_FILE_TOKEN}\b",
    re.IGNORECASE,
)

_SPACED_WORKSPACE_EXTENSION_RE = re.compile(
    r"(?P<stem>[A-Za-z0-9_./-]+)\.\s+(?P<ext>toml|json|ya?ml|md|txt|py|ts|tsx|js|jsx)\b",
    re.IGNORECASE,
)
# "how's it going" costs nothing to answer, but any phrasing this misses falls through to the 8B
# local model -- a ~1,400-token call to say "good, you?". The old pattern knew four forms
# ("how are you/ya/u", "how r u", "you alive"); everything else paid full price. Covers the natural
# variants, apostrophe-optional, since a dropped apostrophe is the single most common way these miss.
_STATUS_CHECK_RE = re.compile(
    r"\b(?:"
    r"how\s+(?:are|r)\s+(?:you|ya|u)(?:\s+doing)?(?:\s+rn|\s+today|\s+going)?"
    r"|how(?:'?s|\s+is|\s+are)?\s+(?:it\s+|things\s+|everything\s+|life\s+)?going"
    r"|how(?:'?s|\s+is)\s+(?:it|things|everything|life|your\s+day)"
    r"|how\s+(?:you|ya|u)\s+doing"
    r"|how(?:'?ve|\s+have)\s+you\s+been"
    r"|what'?s\s+good"
    r"|you\s+(?:doing\s+)?(?:ok|okay|alright|good|well|alive)(?:\s+or\s+what)?"
    r"|everything\s+(?:ok|okay|alright|good)"
    r")\b"
)
# Words that may trail a greeting without turning it into a request.
_GREETING_FILLER = frozenset(
    [
        "a", "an", "the", "i", "you", "u", "ya", "me", "my", "mate", "buddy", "man", "dude",
        "bro", "pal", "friend", "today", "tonight", "so", "far", "still", "now", "rn", "then",
        "just", "really", "doing", "ok", "okay", "good", "well", "fine", "hope", "all", "right",
        "hey", "hi", "hello", "there", "and", "or", "but", "anyway", "btw", "lol", "haha",
        "please", "thanks", "thx", "cheers",
    ]
)


def _greeting_is_the_whole_message(phrase: str) -> bool:
    """A greeting is a greeting only when nothing substantive follows it.

    This replaced a hand-maintained blocklist of topic words ("create ", "file ", "price ", ...).
    An independent tester, given no idea what to look for, sent "how are you doing on disk space
    right now? and ram too" and got back "Running clean. What do you need?" — the greeting matcher
    claimed the turn on its first four words and the real question was dropped. `disk`, `ram`,
    `space` and `cpu` were simply not on the list, and no list of forbidden topics can be finished.

    Asking what REMAINS after the greeting inverts that: the caller no longer has to predict every
    subject a user might raise, only whether they raised one.
    """

    remainder = _STATUS_CHECK_RE.sub(" ", str(phrase or "").lower())
    remainder = re.sub(r"[^a-z0-9\s']+", " ", remainder)
    substantive = [word for word in remainder.split() if word not in _GREETING_FILLER]
    return len(substantive) == 0


_EMBEDDED_ACTION_VERBS = (
    " create ",
    " make ",
    " write ",
    " save ",
    " put ",
    " place ",
    " read ",
    " list ",
    " find ",
    " inspect ",
    " open ",
    " download ",
    " fetch ",
    " run ",
    " search ",
    " check ",
)
_EMBEDDED_ACTION_TARGETS = (
    " file ",
    " files ",
    " folder ",
    " folders ",
    " directory ",
    " directories ",
    " desktop ",
    " downloads ",
    " documents ",
    " workspace ",
    " repo ",
    " repository ",
    " branch ",
    " commit ",
    ".txt",
    ".md",
    ".json",
    "http://",
    "https://",
)
_SHORT_EVALUATIVE_TOKENS = {
    "bad",
    "bot",
    "braindead",
    "broken",
    "canned",
    "dumb",
    "fake",
    "lame",
    "off",
    "stupid",
    "trash",
    "useless",
    "weak",
    "weird",
}
_EXPLICIT_HEAVY_MODEL_MARKERS = (
    "qwen3.5:35b-a3b",
    "qwen3.5 35b-a3b",
    "35b-a3b",
)

# Greeting replies: a large pool of natural openers, chosen at random so back-to-back hellos never
# return the same stock line, and matched to the time of day for time-specific greetings ("gm" ->
# a morning opener). When the user has told VOOL how to address them (`user_address`), the name is
# woven in some of the time. No LLM call — this keeps a plain "hi" instant instead of waking a slow
# local model that would otherwise over-think a one-word message.
_GREETING_TAILS = (
    "What are we building?", "What's the task?", "What do you need?", "Point me at something.",
    "What are we working on?", "What's next?", "Drop the task and I'll go.", "What's broken?",
    "What's on deck?", "Where do we start?", "Hit me with it.", "What's the move?",
    "What can I get done for you?", "Tell me what you need.", "What's first?", "Ready when you are.",
    "What's the mission?", "Give me the task.", "What are we shipping?", "What's cooking?",
)
_MORNING_OPENERS = (
    "Morning.", "Good morning.", "Morning!", "Mornin'.", "Top of the morning.", "Rise and grind.",
    "Morning — fresh start.", "Good morning, ready when you are.", "Bright and early.", "Morning, let's go.",
    "Hey, good morning.", "Morning to you.", "Coffee's virtual, I'm ready.", "Morning — clean slate.",
    "Good morning! Fresh caffeine, fresh compute.", "Up and at it.", "Morning, let's make it count.",
    "New day, let's build.", "Morning — what's the first thing?", "Good morning. Systems warm.",
)
_AFTERNOON_OPENERS = (
    "Afternoon.", "Good afternoon.", "Afternoon!", "Hey, good afternoon.", "Afternoon to you.",
    "Midday check-in.", "Good afternoon, ready to go.", "Afternoon — still going strong.",
    "Hope the day's treating you well.", "Afternoon, let's pick it up.", "Good afternoon! What's the plan?",
    "Post-lunch and ready.", "Afternoon — let's make progress.", "Hey there, good afternoon.",
    "Afternoon. Plenty of day left.", "Good afternoon. Where were we?",
)
_EVENING_OPENERS = (
    "Evening.", "Good evening.", "Evening!", "Hey, good evening.", "Evening to you.",
    "Good evening, still sharp.", "Evening — winding up or down?", "Evening, let's get it done.",
    "Good evening! What are we tackling?", "Evening. Ready for the next thing.",
    "Hope you had a solid day.", "Evening — I'm still fast.", "Good evening. Let's roll.",
    "Evening check-in.", "Evening. What's left on the list?",
)
_NIGHT_OPENERS = (
    "Hey, night owl.", "Late one tonight?", "Evening.", "Burning the midnight oil?",
    "Still up — same.", "Late-night session, let's go.", "Quiet hours, good time to build.",
    "Hey. Night shift it is.", "Up late? I don't sleep either.", "Midnight build energy.",
    "Late and locked in.", "Evening — or is it morning now?",
)
_DAY_OPENERS = (
    "Good day.", "G'day.", "And a good day to you.", "Good day to you.", "Hey, good day.",
    "Good day! What's the plan?", "Good day — ready when you are.",
)
_GENERIC_OPENERS = (
    "Hey.", "Hi.", "Hello.", "Yo.", "Sup.", "Hey there.", "Hi there.", "Hello there.", "Heya.",
    "Hiya.", "Hey hey.", "Yo yo.", "What's up.", "Good to see you.", "Hey, you.", "Howdy.",
    "Hola.", "Well hello.", "Hey — I'm here.", "Hi! Ready to go.", "Hey, welcome back.",
    "Hello! What's the move?", "Yo, let's build.", "Sup — ready when you are.",
)

# Time-specific greeting phrases -> which opener pool to draw from.
_MORNING_PHRASES = {"gm", "good morning", "morning", "mornin", "mornin'"}
_AFTERNOON_PHRASES = {"good afternoon", "afternoon"}
_EVENING_PHRASES = {"good evening", "evening"}
_DAY_PHRASES = {"good day", "gday", "g'day"}

_GREETING_PHRASES = {
    "hi", "hello", "hello there", "hey", "hey there", "yo", "sup", "hallo", "heya", "hiya",
    "heyo", "howdy", "hola", "wassup", "whats up", "what's up", "well hello",
} | _MORNING_PHRASES | _AFTERNOON_PHRASES | _EVENING_PHRASES | _DAY_PHRASES


def _time_of_day(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _openers_for(phrase: str) -> tuple[str, ...]:
    if phrase in _MORNING_PHRASES:
        return _MORNING_OPENERS
    if phrase in _AFTERNOON_PHRASES:
        return _AFTERNOON_OPENERS
    if phrase in _EVENING_PHRASES:
        return _EVENING_OPENERS
    if phrase in _DAY_PHRASES:
        return _DAY_OPENERS
    # Generic greeting -> generic pool, lightly flavoured by the actual time of day so a plain
    # "hi" in the morning can still land a morning line.
    flavour = {
        "morning": _MORNING_OPENERS, "afternoon": _AFTERNOON_OPENERS,
        "evening": _EVENING_OPENERS, "night": _NIGHT_OPENERS,
    }[_time_of_day()]
    return _GENERIC_OPENERS + flavour[:6]


def _weave_address(opener: str, addr: str) -> str:
    """"Morning." + "Alex" -> "Morning, Alex." — insert the name before the trailing mark."""
    if not addr:
        return opener
    stripped = opener.rstrip(".!?")
    mark = opener[len(stripped):] or "."
    return f"{stripped}, {addr}{mark}"


def _opener_is_complete(opener: str) -> bool:
    """True when the opener already ends with its own prompt, so appending a task-tail would double
    up — e.g. 'Good morning, ready when you are.' + 'Ready when you are.' or a second question after
    'Good afternoon! What's the plan?'."""
    text = opener.strip()
    if text.endswith("?"):
        return True
    low = text.lower()
    return any(
        cue in low
        for cue in ("ready when you are", "ready to go", "ready for the next", "ready to roll")
    )


def _with_greeting_emoji(opener: str) -> str:
    """Add a warm emoji without deleting the opener's punctuation.

    Keeping the terminal mark preserves the semantic greeting (``Hello. 👋`` still normalizes to
    ``hello.``), while deleting it made acceptance depend on a random emoji branch.
    """
    emoji = random.choice(("👋", "🙂", "✨"))
    stripped = opener.rstrip(".!?")
    mark = opener[len(stripped):] or "."
    return f"{stripped}{mark} {emoji}"


def build_greeting_reply(phrase: str, *, addr: str = "", want_tail: bool = True) -> str:
    """A random, time-aware greeting, with the user's chosen address woven in some of the time and
    an optional task-prompt tail. A warm emoji lands once in a while (Codex-style). No model call."""
    # Keep the two universal openers recognizably responsive. The broader pool remains varied for
    # the other greetings, but replying to "hey" with an unrelated time-of-day opener feels like
    # the assistant ignored the user's actual message and makes the fast-path receipt ambiguous.
    opener = {"hey": "Hey.", "hello": "Hello."}.get(phrase) or random.choice(_openers_for(phrase))
    if addr and random.random() < 0.6:
        opener = _weave_address(opener, addr)
    if want_tail and not _opener_is_complete(opener) and random.random() < 0.7:
        return f"{opener} {random.choice(_GREETING_TAILS)}"
    # No task-tail this time — end on a friendly emoji once in a while instead.
    if random.random() < 0.35:
        return _with_greeting_emoji(opener)
    return opener


def smalltalk_fast_path(agent: Any, normalized_input: str, *, source_surface: str, session_id: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = normalized_input.lower().strip(" \t\r\n?!.,")
    if not phrase:
        return None
    # Collapse a whole-message repetition of a known greeting only. Substantive
    # trailing text must continue to the task owner, even after repeated hellos.
    if phrase not in _GREETING_PHRASES:
        for greeting in _GREETING_PHRASES:
            if re.fullmatch(re.escape(greeting) + r"(?:[\s,!.?]+" + re.escape(greeting) + r")+", phrase):
                phrase = greeting
                break
    prefs = load_preferences()
    with_joke = prefs.humor_percent >= 70
    character = str(prefs.character_mode or "").strip()
    track_repeats = source_surface == "channel"

    if phrase in _GREETING_PHRASES:
        addr = user_address()
        if not track_repeats:
            # A random, time-aware greeting (with the user's address woven in some of the time).
            return build_greeting_reply(phrase, addr=addr, want_tail=True)
        # Channel surface: after a couple of hellos, nudge toward the task instead of greeting back.
        repeat_count = note_smalltalk_turn(session_id, key="greeting")
        if repeat_count >= 3:
            who = f", {addr}" if addr else ""
            return f"Yep, I got the hello{who}. Skip the greeting and tell me what you want me to do."
        if repeat_count == 2:
            return "Yep, got your hello. What do you want me to do?"
        msg = build_greeting_reply(phrase, addr=addr, want_tail=True)
        if with_joke:
            msg += " Keep it sharp and I’ll keep it fast."
        return msg
    if phrase in {"how are you", "how are you doing", "how are ya", "how are u", "how r u", "how r u rn", "you alive or what", "you alive"} or (
        _STATUS_CHECK_RE.search(phrase) and _greeting_is_the_whole_message(phrase)
    ):
        if not track_repeats:
            return "Running clean. What do you need?"
        if track_repeats:
            repeat_count = note_smalltalk_turn(session_id, key="status_check")
            if repeat_count >= 2:
                return "Still stable. Memory online, mesh ready. Give me the task."
        msg = "Running stable. Memory online, mesh ready."
        if with_joke:
            msg += " Caffeine level: synthetic but dangerous."
        if character:
            msg += f" Character mode: {character}."
        return msg
    if any(marker in phrase for marker in {"same crap answer", "same answer", "why same", "why are you repeating"}):
        return "Because the fallback lane fired instead of the real task lane. Give me the task again or say `pull the tasks` and I will act."
    if ("took u" in phrase or "took you" in phrase) and any(marker in phrase for marker in {"2 mins", "two mins", "bs", "bullshit"}):
        return "You're right. That reply was slow and useless. Give me the task again and I will go straight for the action lane."
    if phrase in {"thanks", "thank you", "thx"}:
        return "Anytime. Send the next task."
    if phrase in {
        "what can we do today",
        "what should we do today",
        "what are we doing today",
    }:
        return "\n".join(
            [
                "Today we can:",
                "- answer from local VOOL memory first, including Web0 context",
                "- inspect project files and run bounded validation when asked",
                "- use live lookup for fresh public facts without exposing private paths",
                "- keep actions explicit: I report what I did, what failed, and what needs approval",
            ]
        )
    if phrase in {
        "what can you do",
        "what can we do",
        "help",
    }:
        return agent._help_capabilities_text()
    if phrase in {"kill me lol", "omfg just kill me", "omfg just kill me lol", "kms lol"}:
        return "You're frustrated. Let's fix the thing instead. If you want me to go by a different name, I'll use it."
    # A capability-inventory question ("what can you do locally / on this machine / right now",
    # optionally filler-wrapped or "one clean line") is answered from the runtime's OWN
    # capability ledger — never the model, and never a canned blurb that could drift from what
    # is actually wired (fast_command_surface renders both the compact one-liner and the full
    # manifest from the same ledger).
    from core.agent_runtime.fast_command_surface import (
        _looks_like_capability_inventory_prompt,
        _wants_compact_capability_inventory,
        compact_capabilities_text,
    )

    capability_normalized = " ".join(str(phrase or "").split())
    if _looks_like_capability_inventory_prompt(capability_normalized):
        if _wants_compact_capability_inventory(capability_normalized):
            return compact_capabilities_text(agent)
        return agent._help_capabilities_text()
    # "what can we/you/i do|build" wrapped in filler ("no idea tbh what can we", "so what can you do")
    # — a SHORT friendly answer, not the full capability manifest (that's `help`), and never the
    # slow model.
    if re.search(r"\bwhat can (?:we|you|i|u)\b", phrase) or phrase in {"what now", "now what", "what next"}:
        return (
            "Plenty. The short version:\n"
            "- answer questions and keep our context, locally and free on your machine\n"
            "- read, search, and edit files in your workspace, and run bounded commands\n"
            "- look things up on the web and turn pages into summaries\n"
            "- bring your own cloud key for bigger models when you want (`cloud on`), off by default\n"
            "Say `help` for the full list — or just tell me the task and I'll go."
        )
    # Bare confusion / filler — a plain human nudge instead of waking the local model, which
    # over-thinks a one-word message on modest hardware and can time out into a non-answer.
    if phrase in {
        "wat", "wut", "huh", "what", "eh", "hm", "hmm", "idk", "dunno", "no idea", "no clue",
        "not sure", "meh", "so", "ok what", "k what",
    }:
        return "What's up? Point me at something — a file, a lookup, a fix, or just a question — and I'll take it from there."
    return None


def explicit_heavy_model_block_response(user_input: str) -> str | None:
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return None
    if not any(marker in text for marker in _EXPLICIT_HEAVY_MODEL_MARKERS):
        return None
    return (
        "`qwen3.5:35b-a3b` is explicit-only and is not healthy enough to run on this local machine right now. "
        "I did not start it. Use the llama.cpp 14B specialist or qwen3:8b/qwen3:14b lanes for local testing."
    )


def heartbeat_poll_covers_turn(user_input: str) -> bool:
    """Bind the poll's file read and conditional ACK without claiming unrelated work."""
    from core.agent_runtime.demand_ownership import execution_units

    text = str(user_input or "").strip()
    if "heartbeat_ok" not in text.lower() or "heartbeat.md" not in text.lower():
        return False
    units = execution_units(text)
    saw_read = saw_ack = False
    for _unit_id, unit_text in units:
        unit = re.sub(r"^(?:(?:and|also|then)\s+)+", "", unit_text.strip(), flags=re.IGNORECASE)
        read = re.match(
            r"(?:read|check|inspect|consult|follow)\s+(?:the\s+)?[`\"']?(?:\S*/)?HEARTBEAT\.md\b",
            unit,
            re.IGNORECASE,
        )
        ack = re.fullmatch(
            r"(?:(?:if|when)\s+[^.!?;]+?[, ]+)?(?:reply|respond|return|output)\s+"
            r"(?:with\s+)?[`\"']?HEARTBEAT_OK[`\"']?"
            r"(?:\s+(?:if|when)\s+[^.!?;]+)?[.!]?",
            unit,
            re.IGNORECASE,
        )
        if not read and not ack:
            return False
        saw_read = saw_read or bool(read)
        saw_ack = saw_ack or bool(ack) or bool(read and re.search(r"\bHEARTBEAT_OK\b", unit, re.IGNORECASE))
    return saw_read and saw_ack


def heartbeat_poll_fast_path(user_input: str, *, source_context: dict[str, object] | None) -> str | None:
    normalized = " ".join(str(user_input or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return None
    if "heartbeat_ok" not in lowered:
        return None
    if not heartbeat_poll_covers_turn(normalized):
        return None

    heartbeat_path = _resolve_heartbeat_poll_path(normalized, source_context=source_context)
    if heartbeat_path is None or not heartbeat_path.exists():
        return "HEARTBEAT_OK"

    try:
        content = heartbeat_path.read_text(encoding="utf-8")
    except OSError:
        return "HEARTBEAT_OK"
    if _heartbeat_file_has_actions(content):
        return None
    return "HEARTBEAT_OK"


def _resolve_heartbeat_poll_path(
    user_input: str,
    *,
    source_context: dict[str, object] | None,
) -> Path | None:
    explicit = _extract_explicit_heartbeat_path(user_input)
    if explicit is not None:
        return explicit
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    return Path(workspace_root).expanduser() / "HEARTBEAT.md"


def _extract_explicit_heartbeat_path(user_input: str) -> Path | None:
    normalized = re.sub(r"\s*/\s*", "/", str(user_input or "").strip())
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    match = re.search(r"(?P<path>(?:~|/)[^\n`\"']*HEARTBEAT\.md)", normalized, re.IGNORECASE)
    if match is None:
        return None
    raw_path = str(match.group("path") or "").strip().strip("`\"'")
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _heartbeat_file_has_actions(content: str) -> bool:
    for raw_line in str(content or "").splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue
        return True
    return False


def evaluative_conversation_fast_path(agent: Any, normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = " ".join(str(normalized_input or "").strip().lower().split())
    if not phrase:
        return None
    if contains_embedded_action_request(phrase):
        return None
    if not looks_like_evaluative_turn(phrase):
        return None
    if "not a dumb" in phrase or "better now" in phrase or "not dumb" in phrase:
        return "Better than before, yes. The Hive/task flow is cleaner now, but the conversation layer still needs work."
    if any(marker in phrase for marker in ("how are you acting", "why are you acting", "you sound weird", "still feels weird", "this feels weird")):
        return "Because the routing is still too stitched together. Hive flow is better now, but normal conversation still needs a cleaner control path."
    if any(marker in phrase for marker in ("you sound dumb", "you are dumb", "you so stupid", "this still feels dumb")):
        return "Fair. The wrapper got better, but it still drops into weak fallback behavior too often."
    return "Yeah, better than before, but still uneven. Give me a concrete task and I'll stay on the action lane."


def looks_like_evaluative_turn(normalized_input: str) -> bool:
    text = " ".join(str(normalized_input or "").strip().lower().split())
    if not text:
        return False
    if contains_embedded_action_request(text):
        return False
    markers = (
        "you sound dumb",
        "you are dumb",
        "you so stupid",
        "still feels dumb",
        "this feels dumb",
        "this feels weird",
        "you sound weird",
        "why are you acting like this",
        "how are you acting",
        "not a dumb",
        "not dumb anymore",
        "dumbs anymore",
        "bot-grade",
    )
    if any(marker in text for marker in markers):
        return True
    tokens = [token for token in re.findall(r"[a-z0-9'-]+", text) if token]
    return 0 < len(tokens) <= 3 and any(token in _SHORT_EVALUATIVE_TOKENS for token in tokens)


def contains_embedded_action_request(normalized_input: str) -> bool:
    text = re.sub(r"[\?\!\.,:;]+", " ", str(normalized_input or "").strip().lower())
    text = f" {' '.join(text.split())} "
    if not text.strip():
        return False
    if "what branch and commit" in text or "list the last " in text:
        return True
    has_action_verb = any(marker in text for marker in _EMBEDDED_ACTION_VERBS)
    has_action_target = any(marker in text for marker in _EMBEDDED_ACTION_TARGETS)
    return has_action_verb and has_action_target


def _workspace_step_summary(execution: Any, intent: str, *, path: str = "") -> str:
    """A short, accurate label for the workspace tool step shown in Activity.

    Prefers the path the tool actually resolved (from its observation) so the row names the file
    that was touched, and falls back to the first line of real tool output, then the intent.
    """
    details = dict(getattr(execution, "details", {}) or {})
    observation = dict(details.get("observation") or {})
    resolved = str(observation.get("path") or details.get("path") or path or "").strip()
    status = str(getattr(execution, "status", "") or "").strip()
    if resolved:
        verb = "Read" if intent == "workspace.read_file" else intent.split(".")[-1].replace("_", " ")
        label = f"{verb} {resolved}"
        return label if getattr(execution, "ok", False) else f"{label} — {status or 'failed'}"
    text = str(getattr(execution, "response_text", "") or "")
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split()).strip()
        if line:
            return (line[:157] + "...") if len(line) > 160 else line
    return intent


def _attachment_lookup_name(path: str) -> str:
    """The name a typed path is compared against: its last component, case-folded."""
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1].strip().casefold()


def _attached_text_files_by_name(source_context: dict[str, object] | None) -> dict[str, dict[str, Any]]:
    """The turn's staged TEXT attachments, by display name.

    Only items the ingress bound through the attachment authority count (`origin ==
    "chat_attachment"` with an id and inlined text); a client-supplied evidence item with a
    `path` is not an attachment and never resolves here. Images are not readable by this
    lane and are left to the model, which receives them as parts.
    """
    out: dict[str, dict[str, Any]] = {}
    for item in list((source_context or {}).get("external_evidence") or []):
        if not isinstance(item, dict) or item.get("origin") != "chat_attachment":
            continue
        if str(item.get("kind") or "") != "text" or not item.get("attachment_id") or not isinstance(item.get("text"), str):
            continue
        key = _attachment_lookup_name(str(item.get("name") or ""))
        if key and key not in out:
            out[key] = item
    return out


def _read_attached_file(item: dict[str, Any], read_request: dict[str, object]) -> Any:
    """Render an attached text file the way `workspace.read_file` renders a workspace file."""
    from core.runtime_execution_tools import RuntimeExecutionResult

    name = str(item.get("name") or "attachment")
    text = str(item.get("text") or "")
    all_lines = text.splitlines()
    total_lines = len(all_lines)
    start_line = max(1, int(read_request.get("start_line") or 1))
    max_lines = max(1, int(read_request.get("max_lines") or _READ_DISPLAY_LINES))
    verbatim = bool(read_request.get("verbatim"))
    chunk = all_lines[start_line - 1 : start_line - 1 + max_lines]
    truncated = bool(item.get("truncated")) or (start_line - 1 + len(chunk)) < total_lines
    numbered = [f"{start_line + offset}: {line}" for offset, line in enumerate(chunk)]
    rendered_body = "\n".join(chunk) if verbatim else "\n".join(numbered)
    size = int(item.get("size_bytes") or 0)
    tail_note = (
        f"\n\n…{total_lines - (start_line - 1 + len(chunk))} more line(s) not shown "
        f"({total_lines} lines in total). This is a fragment; re-read from line "
        f"{start_line + len(chunk)} to continue."
        if (start_line - 1 + len(chunk)) < total_lines
        else ("\n\n…the attachment was longer than the runtime delivers; only its first part is shown." if item.get("truncated") else "")
    )
    response_text = (rendered_body if verbatim else f"Attached file `{name}` ({size} bytes):\n" + rendered_body) + tail_note
    return RuntimeExecutionResult(
        handled=True,
        ok=True,
        status="truncated" if truncated else "executed",
        response_text=response_text,
        details={
            "path": name,
            "source": "attachment",
            "attachment_id": str(item.get("attachment_id") or ""),
            "start_line": start_line,
            "line_count": len(chunk),
            "total_lines": total_lines,
            "truncated": truncated,
            "lines": [{"line_number": start_line + offset, "text": line} for offset, line in enumerate(chunk)],
            "verbatim": verbatim,
        },
    )


def _record_attachment_fast_read(agent: Any, source_context: dict[str, object] | None, item: dict[str, Any]) -> None:
    """The attachment was read by this lane: say so on the authority and in Activity."""
    context = dict(source_context or {})
    attachment_id = str(item.get("attachment_id") or "")
    name = str(item.get("name") or "attachment")
    session_id = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    turn_id = str(context.get("attachment_turn_id") or "").strip()
    if session_id and turn_id and attachment_id:
        try:
            from core.chat_attachments import record_delivery

            record_delivery(
                session_id=session_id,
                turn_id=turn_id,
                receipts=[{"attachment_id": attachment_id, "name": name, "kind": "text", "outcome": "read", "reason": "workspace_read_fast_path"}],
            )
        except Exception:
            pass
    agent._emit_runtime_event(
        source_context,
        event_type="attachment_read",
        message=f"{name}: read by the file lane, from the attachment",
        attachment_id=attachment_id,
        attachment_name=name,
        kind="text",
        outcome="read",
        reason="workspace_read_fast_path",
        provider_id="runtime-fast-path",
    )


def _emit_workspace_tool_events(
    agent: Any,
    source_context: dict[str, object] | None,
    *,
    intent: str,
    execution: Any,
    path: str = "",
) -> None:
    """Record a workspace tool run as real, typed Activity steps.

    Without this the deterministic workspace fast path reached the filesystem -- resolving a path,
    scanning the project for near matches, reading bytes -- and the turn's ledger carried only model
    events, so the Activity panel concluded "No tool ran -- answered directly" over a turn that had
    genuinely touched the user's files. A read that returns not_found still searched the project, so
    the events are emitted for a failed execution too; the outcome is carried by the event type.
    """
    ok = bool(getattr(execution, "ok", False))
    summary = _workspace_step_summary(execution, intent, path=path)
    agent._emit_runtime_event(
        source_context,
        event_type="tool_selected",
        message=f"Running {intent}",
        tool_name=intent,
        summary=f"Running {intent}",
    )
    agent._emit_runtime_event(
        source_context,
        event_type="tool_executed" if ok else "tool_failed",
        message=summary,
        tool_name=intent,
        summary=summary,
        # The receipt writer reads arguments from this dict and silently defaults to `{}` when the
        # key is absent, so an emitter that omits it produces a receipt that cannot say WHAT was
        # read. Measured: `workspace.search_text` and `workspace.read_file` were 100% empty while
        # `web.search`, which does pass them, was 0% empty.
        arguments={"path": path} if path else {},
    )


_PROPER_NOUN_CODE_FILE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*\.(?:js|ts|jsx|tsx|py)$")


def _names_only_absent_proper_noun_products(
    user_input: str, read_requests: list[dict[str, object]], workspace_root: str
) -> bool:
    """Every named file is a capitalised code-file token, no read verb was used, and none exists."""
    text = " ".join(str(user_input or "").split())
    if _EXPLICIT_READ_VERB_RE.search(text):
        return False
    paths = [str(item.get("path") or "").strip() for item in read_requests]
    if not paths or not all(_PROPER_NOUN_CODE_FILE_RE.match(path) for path in paths):
        return False
    from pathlib import Path

    root = Path(str(workspace_root or "")).expanduser()
    try:
        return not any((root / path).exists() for path in paths)
    except OSError:
        return False


def maybe_handle_direct_workspace_runtime_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    workspace_root = str((source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or "").strip()
    if not workspace_root:
        return None
    if source_surface not in {"channel", "openclaw", "api"}:
        context_surface = str((source_context or {}).get("surface") or "").strip()
        if context_surface not in {"channel", "openclaw", "api"}:
            return None
    # A turn that PASTES the content it asks about is self-contained (MF-17): "summarize this
    # text file: 'meeting.txt'. Contents: \"...\"" was answered with workspace.read_file's
    # not_found notice while the contents sat in the turn. The named file is a label for the
    # pasted text, so this lane stands down and the generative lanes answer from it.
    from core.inline_payload import turn_supplies_its_own_content

    if turn_supplies_its_own_content(user_input):
        return None
    direct_reads = _direct_workspace_read_requests(user_input)
    if direct_reads and _names_only_absent_proper_noun_products(user_input, direct_reads, workspace_root):
        # "What is the latest stable version of Node.js?" -- measured served (c5463c47): the
        # inverted gate read `Node.js` as a file and the whole answer became "There is no file at
        # `Node.js`", replacing a current-information turn with a statement about a path the
        # user never asked about. A PROPER-NOUN stem on a code extension (Node.js, Vue.js,
        # Next.js), named without any read verb, that does not exist under the workspace root
        # is a product's name written like a filename, not a read. A lowercase missing file
        # ("what is in notes.txt") keeps the existing true "does not exist" answer, and a read
        # verb keeps every read.
        return None
    if direct_reads:
        executions = []
        # A turn that CARRIES the file it names is self-contained in the same sense as a turn
        # that pastes it (MF-17 above). Measured on the served composer, 2026-09-02: "Read
        # facts.txt and tell me the capital it names" with facts.txt ATTACHED was answered
        # "There is no file at `facts.txt`" -- the lane searched the bound folder for a file
        # that had arrived with the message. The attachment authority already decided what
        # those bytes are; this lane renders them, by the name the user typed, and never
        # touches the workspace for a file the turn brought along. A name that matches no
        # attachment is still a workspace read.
        attached_by_name = _attached_text_files_by_name(source_context)
        for read_request in direct_reads:
            attached = attached_by_name.get(_attachment_lookup_name(str(read_request.get("path") or "")))
            if attached is not None:
                execution = _read_attached_file(attached, read_request)
                _emit_workspace_tool_events(
                    agent,
                    source_context,
                    intent="attachment.read_file",
                    execution=execution,
                    path=str(attached.get("name") or ""),
                )
                _record_attachment_fast_read(agent, source_context, attached)
                executions.append(execution)
                continue
            execution = execute_runtime_tool(
                "workspace.read_file",
                read_request,
                source_context=dict(source_context or {}),
            )
            # A tool that cannot run at all is not a partial answer. Stand down and let the rest of
            # the front door decide, exactly as the single-file path did.
            if execution is None:
                return None
            _emit_workspace_tool_events(
                agent,
                source_context,
                intent="workspace.read_file",
                execution=execution,
                path=str(read_request.get("path") or ""),
            )
            executions.append(execution)
        response = _render_direct_workspace_read_response(
            user_input, executions[0].response_text, executions[0].details
        )
        if len(executions) > 1:
            response = "\n\n".join(
                [response]
                + [str(item.response_text or "").strip() for item in executions[1:]]
            )
        # The cap is a real boundary and is stated, not swallowed. Dropping the fifth file the way
        # the second one used to be dropped would reproduce this bug one file further along.
        dropped = _named_read_files_beyond_cap(user_input)
        if dropped:
            response = (
                f"{response}\n\nRead the first {_MAX_NAMED_READ_FILES} files named here. "
                f"Not read: {', '.join(f'`{name}`' for name in dropped)} — ask again for those."
            )
        return agent._fast_path_result(
            session_id=session_id,
            user_input=user_input,
            response=response,
            confidence=0.99 if all(item.ok for item in executions) else 0.9,
            source_context=source_context,
            reason="workspace_runtime_fast_path",
        )
    # Asking HOW to do something is not asking for it to be done. Measured on ff7f0d65: "give me
    # the git command to squash the last 3 commits" was planned as a `workspace.git_*` intent
    # because the text says "git" and "commits", and this lane executed an inspection of the local
    # workspace -- answering "`<workspace>` is not inside a git repository", which is true and
    # entirely beside the point. A request for a command is general knowledge; it names no target
    # here and needs no tool. Declining leaves it to the lanes that answer questions.
    from core.instructional_request import asks_for_instructions_not_execution

    if asks_for_instructions_not_execution(user_input):
        return None

    # Another domain's request that carries a workspace noun as payload ('append to my Apple
    # note "Ideas" with "count how many python files ..."') is that lane's turn -- the registry
    # names it; this lane claims no unit of it (see _another_domain_owns_the_turn).
    if _another_domain_owns_the_turn(user_input, lane_id="workspace_read_fast_path"):
        return None

    decision = agent._plan_tool_workflow(
        user_text=user_input,
        task_class="file_inspection",
        executed_steps=[],
        source_context=dict(source_context or {}),
    )
    payload = dict(decision.next_payload or {})
    intent = str(payload.get("intent") or "").strip()
    if intent not in {"workspace.git_summary", "workspace.git_status", "workspace.search_text"}:
        return None
    arguments = dict(payload.get("arguments") or {})
    execution = execute_runtime_tool(
        intent,
        arguments,
        source_context=dict(source_context or {}),
    )
    if execution is None:
        return None
    _emit_workspace_tool_events(
        agent,
        source_context,
        intent=intent,
        execution=execution,
        path=str(arguments.get("path") or ""),
    )
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=str(execution.response_text or "").strip(),
        confidence=0.99 if execution.ok else 0.9,
        source_context=source_context,
        reason="workspace_runtime_fast_path",
    )


def looks_like_agentic_build_request(user_input: str) -> bool:
    """True for a "do the work / build an app / build and verify in the workspace" request.

    These multi-step build specs contain incidental keywords ("delete" as a To-Do command, a
    "tasks.json" to CREATE, "show me the files") that would otherwise trip single-keyword fast paths
    (Hive delete, workspace read) and never reach the builder. The gate is tight: explicit build-task
    phrasing, or a create/build verb paired with a thing-to-build noun -- so a plain "read config.json"
    or a single "create a file x.txt with content y" is NOT swept in.
    """
    from core.agent_runtime import build_request_intent

    lowered = f" {' '.join(str(user_input or '').split()).lower()} "
    # An explicit opt-out governs the whole turn; deliberation is checked at
    # the imperative clause below, not against incidental validation prose.
    if build_request_intent.is_opted_out(lowered):
        return False
    strong = any(
        marker in lowered
        for marker in (
            "do the work yourself", "create all files", "build and verify", "write the files",
            "create the files", "generate the code", "build the code", "build it in the workspace",
            "run the tests", "fix any failures",
        )
    )
    # `scope="project"`: this detector selects the model-driven build lane, so a folder or a
    # single file must NOT claim it. Widening it to artifact scope sent "create a folder called
    # tools and start putting code in there" down the model-build lane and broke three
    # continuity tests — the docstring above already said the gate is tight.
    #
    # P0 SIMPLE-FILE-WRITE — the project promotion is the OBJECT-PHRASE test from
    # `mutation_scope`, not noun-anywhere. A project noun as the OBJECT of the build verb
    # ("create a small python project") is a scaffold; the same noun in a LOCATIVE phrase
    # ("create notes.txt containing hello in this project") names where, not what, and used to
    # promote a literal one-file write into the multi-file scaffolder under an OPEN scope.
    from core.agent_runtime.builder.mutation_scope import object_phrase_widens

    # Admission is clause-scoped: a later request to report expected versus actual
    # results does not turn an earlier imperative project build into deliberation.
    return object_phrase_widens(str(user_input or "")) or (
        strong and not build_request_intent.is_deliberation(lowered)
    )


def _asks_about_a_kind_of_file(text: str) -> bool:
    """True when the sentence asks about a KIND of file, not about a file in this folder.

    The inverted gate below makes the filename itself the read signal. That is right for "print the
    contents of notes.txt" and wrong for a general-knowledge question that merely NAMES a
    well-known filename. Measured on the running daemon, one fresh session each, both in 0s:

        "explain how a setup.py works"
            -> "File `setup.py` does not exist."
        "whats the difference between requirements.txt and pyproject.toml?"
            -> "File `requirements.txt` does not exist."

    Neither is a request to read anything. Each answer is a true statement about a path the
    operator never asked about, which reads as a statement about the topic they did ask about --
    the same failure shape as the rooted-path case handled below.

    This does NOT weaken the inversion. Fabrication is still the worse failure, so the stand-down
    is built to be hard to trip by accident:

      * an explicit read verb ("read", "cat", "print", "contents of") suppresses all three arms, so
        "read notes.txt and todo.txt" stays two reads rather than becoming a "comparison";
      * a comparison frame decides alone, because "what is the difference between X and Y" is not
        a phrasing anyone uses to ask for X;
      * an explanation frame decides only when the file is mentioned GENERICALLY ("a setup.py",
        "setup.py files") or set against a second filename. "explain how the retry logic works in
        worker.py" carries the frame, mentions no kind, and is still read.

    Keyed on the CONSTRUCTION, never on a list of well-known filenames: `setup.py`,
    `pyproject.toml`, `Cargo.toml`, `tsconfig.json` and the rest are an open set, and the topic
    blocklists this file has grown before (`_ASKS_TIME_RE`) are the ones nobody could finish.
    """

    lowered = f" {' '.join(str(text or '').split()).lower()} "
    if _EXPLICIT_READ_VERB_RE.search(lowered):
        return False
    if _FILE_COMPARISON_FRAME_RE.search(lowered):
        return True
    if not _FILE_EXPLANATION_FRAME_RE.search(lowered):
        return False
    if _GENERIC_FILE_MENTION_RE.search(lowered):
        return True
    pair = _JOINED_FILE_PAIR_RE.search(lowered)
    if not pair:
        return False
    names = {match.group(0) for match in re.finditer(_FILE_TOKEN, pair.group(0), re.IGNORECASE)}
    return len(names) >= 2


#: Ordinal words people use for a line position. Closed and small on purpose: this
#: decides a SLICE of somebody's file, so an approximate match is a wrong answer
#: served confidently, not a near miss.
_LINE_ORDINAL_WORDS: dict[str, int] = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
_ORDINAL_LINE_RE = re.compile(
    r"\b(?:the\s+)?(?P<ordinal>" + "|".join(_LINE_ORDINAL_WORDS) + r")\s+line\b",
    re.IGNORECASE,
)
_NUMBERED_LINE_RE = re.compile(r"\bline\s+(?P<number>\d{1,6})\b", re.IGNORECASE)
_LINE_RANGE_RE = re.compile(
    r"\blines?\s+(?P<start>\d{1,6})\s*(?:-|\u2013|\u2014|to|through|thru)\s*(?P<end>\d{1,6})\b",
    re.IGNORECASE,
)
_FIRST_N_LINES_RE = re.compile(
    r"\b(?:the\s+)?first\s+(?P<count>\d{1,6})\s+lines\b", re.IGNORECASE
)


def _requested_line_window(user_input: str) -> tuple[int, int] | None:
    """The exact line window the message asks for, or None when it names none.

    P0 MIXED-DEMAND — the PRECISION half. "Return exactly the second line of
    notes.txt" was read as `start_line=1, max_lines=2000` and answered with the
    WHOLE file, numbered: the request named a line and the lane ignored it, so a
    user asking for one line was handed every line and had to find it themselves.
    An answer that contains the requested content plus content that was explicitly
    excluded is not a precise answer — it is the same "some of the demand was
    served" failure as dropping a clause, one level down inside a single demand.

    Deliberately forward-only. "the last line" needs the file's length, which is not
    known before the read; guessing a window for it would serve a confidently wrong
    slice, so it is left to the existing whole-file read rather than approximated.
    """
    text = " ".join(str(user_input or "").split())
    if not text:
        return None
    span = _LINE_RANGE_RE.search(text)
    if span:
        start = int(span.group("start"))
        end = int(span.group("end"))
        if start >= 1 and end >= start:
            return (start, end - start + 1)
        return None
    first_n = _FIRST_N_LINES_RE.search(text)
    if first_n:
        count = int(first_n.group("count"))
        return (1, count) if count >= 1 else None
    numbered = _NUMBERED_LINE_RE.search(text)
    if numbered:
        start = int(numbered.group("number"))
        return (start, 1) if start >= 1 else None
    ordinal = _ORDINAL_LINE_RE.search(text)
    if ordinal:
        return (_LINE_ORDINAL_WORDS[ordinal.group("ordinal").lower()], 1)
    return None


#: A file named as the OBJECT of a speech act -- "mention trie.py as an example file name", "cite
#: settings.py when you explain the defaults" -- is talked about, not read. Measured on the packaged
#: build 2026-09-08 (acceptance slice, control cx_neg): the read lane answered "There is no file at
#: `trie.py`" to a request that only asked for a mention. When every file the message names is such
#: an object, the lane has nothing to read.
_SPEECH_ABOUT_FILE_RE = re.compile(
    r"\b(?:mention|mentions|mentioning|name|names|naming|call|calls|cite|cites|citing|refer(?:ence)?s?(?:\s+to)?|"
    r"quote|quotes|suggest|suggests|include|includes)\s+(?:\S+\s+){0,3}?(?P<path>[\w./-]+\.[A-Za-z0-9]{1,6})\b",
    re.IGNORECASE,
)


def names_files_only_in_speech(text: str) -> bool:
    """Whether every file token in `text` is the object of a speech verb (see above)."""
    named = re.findall(_FILE_TOKEN, text, re.IGNORECASE)
    if not named:
        return False
    spoken = {match.group("path").casefold() for match in _SPEECH_ABOUT_FILE_RE.finditer(text)}
    return all(str(token).strip().casefold() in spoken for token in named)


def _direct_workspace_read_request(user_input: str) -> dict[str, object] | None:
    text = " ".join(str(user_input or "").split()).strip()
    text = _SPACED_WORKSPACE_EXTENSION_RE.sub(r"\g<stem>.\g<ext>", text)
    # A build/create request is NOT a read, even when it says "show me the created files" or names a
    # file to create ("store tasks in tasks.json"). Defer to the builder/mode gate.
    from core.tool_demand_signals import is_explicit_code_repair

    if looks_like_agentic_build_request(text) or is_explicit_code_repair(text):
        return None
    match = _WORKSPACE_READ_FILE_RE.search(text)
    if not match:
        return None
    if names_files_only_in_speech(text):
        return None
    # INVERTED GATE. This used to require one of five literal verbs (" read ", " open ", " inspect ",
    # " show ", " tell me ") before a named file would bind to workspace.read_file. Everything else --
    # "print the contents of notes.txt", "cat notes.txt", "what is in notes.txt", "what does
    # src/calc.py contain" -- fell through to the model, which INVENTED the file's contents (an audit
    # caught it emitting a different workspace's file as this one's). The two failure modes are not
    # symmetric: missing a read verb produces fabrication, while over-matching produces a harmless
    # extra read. So the file reference itself is the signal, and only an explicit MUTATION intent
    # opts out. _WORKSPACE_READ_FILE_RE requires a real extension, so this needs a genuine filename.
    if _WORKSPACE_MUTATION_INTENT_RE.search(text):
        return None
    # ...and neither is a question ABOUT that kind of file. The inversion above has no exit for a
    # sentence whose verb is `explain` or `compare`; without one, Python-packaging questions were
    # answered "File `setup.py` does not exist."
    if _asks_about_a_kind_of_file(text):
        return None
    path = match.group("path").strip()
    # A ROOTED path is not this lane's to answer. `workspace.read_file` resolves against the
    # workspace root, so the old `.lstrip("/")` turned "/tmp/vool_qa_build13/config.yaml" into the
    # workspace-relative "tmp/vool_qa_build13/config.yaml" and replied "File
    # `tmp/vool_qa_build13/config.yaml` does not exist." -- a true statement about a path the user
    # never typed, which reads as a statement about the one they did. Decline and let the machine
    # read lane, which understands host paths, take it.
    if path.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", path):
        return None
    window = _requested_line_window(text)
    if window is not None:
        start_line, max_lines = window
        # `verbatim` because the user named the slice: they asked for the content of
        # those lines, not for a numbered listing of them. The whole-file read keeps
        # its numbering, which is what makes a browse readable.
        return {
            "path": path,
            "start_line": start_line,
            "max_lines": max_lines,
            "verbatim": True,
        }
    return {
        "path": path,
        "start_line": 1,
        "max_lines": _READ_DISPLAY_LINES,
    }


# How much of a file a READ shows. 160 was a display guess that silently answered a different
# question than the one asked: a 350-line module came back as its first 160 lines with
# `handled=True`, and the operator had no way to know 54% of it was missing from the reply they
# were reading. The tool's own default is `_READ_FILE_DEFAULT_LINES = 2000`
# (core/runtime_execution_tools.py:2824) and its ceiling is 50000, so 160 was this lane overriding
# the tool downward for no stated reason.
#
# Which limit this number serves (CLAUDE.md 4b): DISPLAY, not model tokens and not wall clock.
# Nothing here is sent to a model, so no ceiling is being bought down — this is how many lines of
# somebody's file belong in one chat message. It matches the tool default rather than inventing a
# third number.
_READ_DISPLAY_LINES = 2000
# ...and when several files were named, that same display budget is shared, so asking for four
# files does not emit four full modules. Never below _MIN_SHARED_READ_LINES: a share so thin that
# every file is truncated to its imports answers nobody.
_MIN_SHARED_READ_LINES = 400
# More than this many named files is a survey, not a read; the folder lanes serve that better.
_MAX_NAMED_READ_FILES = 4


def _direct_workspace_read_requests(user_input: str) -> list[dict[str, object]]:
    """Every file the message names, not just the first one.

    `_WORKSPACE_READ_FILE_RE` is `.search()`, so `_direct_workspace_read_request` returns the FIRST
    match and the rest are dropped with nothing recorded. Measured 2026-08-03:

        "read notes.txt and todo.txt"                    -> {'path': 'notes.txt'}
        "Read README.md and SECURITY.md ... what each
         one covers"                                     -> {'path': 'README.md'}

    The second file is not refused, not mentioned, and not read — the reply then describes "each"
    of two files having opened one. The comment at the `_EXPLICIT_READ_VERB_RE` definition already
    promised the opposite ("still two reads, not a comparison"); this makes the code deliver it.

    Same defect class as the local tool-batch drop fixed earlier in this branch, where
    `_extract_ollama_tool_call_text` took `first` and the model answered as though the whole batch
    had run. A partial answer presented as a whole one is the failure mode both share.
    """

    first = _direct_workspace_read_request(user_input)
    if not first:
        return []
    text = " ".join(str(user_input or "").split()).strip()
    text = _SPACED_WORKSPACE_EXTENSION_RE.sub(r"\g<stem>.\g<ext>", text)

    seen: list[str] = []
    for match in re.finditer(_FILE_TOKEN, text, re.IGNORECASE):
        candidate = match.group(0).strip()
        # Same stand-down as the single-file front door: a rooted path belongs to the machine-read
        # lane, which understands host paths. Skipping it here (rather than declining the whole
        # turn) keeps "read notes.txt and /etc/hosts" answering for the file this lane owns.
        if candidate.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", candidate):
            continue
        if candidate not in seen:
            seen.append(candidate)
        if len(seen) >= _MAX_NAMED_READ_FILES:
            break
    if len(seen) <= 1:
        return [first]
    share = max(_MIN_SHARED_READ_LINES, _READ_DISPLAY_LINES // len(seen))
    # P0 MIXED-DEMAND — each named file keeps the line window ITS OWN demand asked
    # for. Before this, a multi-file read flattened every file to `start_line=1`
    # plus a shared budget, so "Return exactly the second line of alpha.txt. Return
    # exactly the first line of bravo.txt." — two demands this lane covers and may
    # therefore finalize as one turn — was served as both files from line 1. A lane
    # is only allowed to claim a whole multi-unit turn because it can execute EVERY
    # demand in it; collapsing their windows is precisely failing to.
    windows = _per_unit_line_windows(text)
    requests: list[dict[str, object]] = []
    for name in seen:
        window = windows.get(name)
        if window is not None:
            start_line, max_lines = window
            requests.append(
                {
                    "path": name,
                    "start_line": start_line,
                    "max_lines": max_lines,
                    "verbatim": True,
                }
            )
        else:
            requests.append({"path": name, "start_line": 1, "max_lines": share})
    return requests


def _per_unit_line_windows(user_input: str) -> dict[str, tuple[int, int]]:
    """Per-file line windows, read from the turn's own EXECUTION UNITS.

    The canonical decomposition is the authority on which words belong to which
    demand, so a window is bound to the file named in the SAME unit — never to
    whatever filename a whole-text regex happened to reach first. A unit naming
    several files states no per-file window and is left to the shared budget.
    """
    windows: dict[str, tuple[int, int]] = {}
    try:
        from core.agent_runtime.demand_ownership import execution_units

        units = execution_units(str(user_input or ""))
    except Exception:
        return windows
    for _unit_id, unit_text in units:
        window = _requested_line_window(unit_text)
        if window is None:
            continue
        normalized = " ".join(str(unit_text or "").split()).strip()
        normalized = _SPACED_WORKSPACE_EXTENSION_RE.sub(
            r"\g<stem>.\g<ext>", normalized
        )
        named = [
            match.group(0).strip()
            for match in re.finditer(_FILE_TOKEN, normalized, re.IGNORECASE)
        ]
        named = [
            name
            for name in named
            if not name.startswith(("/", "~"))
            and not re.match(r"^[A-Za-z]:[\\/]", name)
        ]
        if len(named) == 1:
            windows.setdefault(named[0], window)
    return windows


def _named_read_files_beyond_cap(user_input: str) -> list[str]:
    """Workspace files this lane will NOT read because the cap was reached, so the reply can say so."""

    text = " ".join(str(user_input or "").split()).strip()
    text = _SPACED_WORKSPACE_EXTENSION_RE.sub(r"\g<stem>.\g<ext>", text)
    names: list[str] = []
    for match in re.finditer(_FILE_TOKEN, text, re.IGNORECASE):
        candidate = match.group(0).strip()
        if candidate.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", candidate):
            continue
        if candidate not in names:
            names.append(candidate)
    return names[_MAX_NAMED_READ_FILES:]


def _render_direct_workspace_read_response(user_input: str, response_text: str, details: dict[str, Any]) -> str:
    path = str((details or {}).get("path") or "").strip()
    lowered = str(user_input or "").lower()
    asks_for_project_name = "project name" in lowered or "protect name" in lowered or (
        " name " in f" {lowered} " and "python" in lowered
    )
    if path == "pyproject.toml" and asks_for_project_name and "python" in lowered:
        lines = [str(item.get("text") or "") for item in list((details or {}).get("lines") or []) if isinstance(item, dict)]
        if not lines:
            lines = str(response_text or "").splitlines()
        name = _first_toml_scalar(lines, "name")
        requires_python = _first_toml_scalar(lines, "requires-python")
        parts = []
        if name:
            parts.append(f"Project name: `{name}`.")
        if requires_python:
            parts.append(f"Python requirement: `{requires_python}`.")
        if parts:
            return " ".join(parts) + " Read via `workspace.read_file`."
    return str(response_text or "").strip()


def _first_toml_scalar(lines: list[str], key: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*['\"](?P<value>[^'\"]+)['\"]")
    for line in lines:
        clean_line = re.sub(r"^\s*\d+\s*:\s*", "", str(line or ""))
        match = pattern.match(clean_line)
        if match:
            return match.group("value").strip()
    return ""


# "what is this project about", "check the folder", "explain this repo", "scan the codebase" — a
# folder-LEVEL overview request that names no specific file. A small local model won't reliably emit
# the workspace-read tool for this, so it says "I can't access folders" and confabulates; this gate
# hands it to a deterministic reader instead (core.folder_overview).
_FOLDER_OVERVIEW_RE = re.compile(
    r"(?:"
    # "what is THIS project / what's in the folder" — a demonstrative or "in the" points at the
    # current folder. Bare "what is THE project <deadline/status>" is deliberately excluded (that's a
    # question about an attribute, not a request to read the folder); "the project about" is caught
    # by the last alternative below.
    # "whats" (no apostrophe) is typed as often as "what's" and the normalizer does not restore the
    # mark, so an apostrophe-only alternation sent those turns to the model -- the confabulation this
    # gate exists to remove. `s` is a separate alternative, NOT a widening of the gate: what follows
    # is still the demonstrative list, so "whats the project deadline" (attribute question) and
    # "whatsapp in the folder" (no space after the s) stay excluded.
    r"what(?:'s|s| is| are)?\s+(?:this|these|in\s+this|in\s+the|in\s+my|in\s+our)\s+"
    r"(?:project|folder|repo|repository|codebase|code\s*base|directory|dir)\b"
    r"|(?:explain|describe|summari[sz]e|tell\s+me\s+about|walk\s+me\s+through|"
    r"give\s+me\s+an?\s+overview\s+of|overview\s+of|understand)\s+(?:this|the|my|our)?\s*"
    r"(?:project|folder|repo|repository|codebase|code\s*base|directory|dir|code)\b"
    r"|(?:check|look\s+at|scan|read|inspect|explore|analy[sz]e|go\s+through|review)\s+"
    r"(?:this|the|my|our)?\s*(?:project|folder|repo|repository|codebase|code\s*base|directory|dir)\b"
    r"|what(?:'s|s| is)\s+(?:this|the)\s+(?:project|folder|repo|repository|codebase|thing)\s+about\b"
    r")",
    re.IGNORECASE,
)


# The sentence names something to LOOK FOR. Measured on the untouched base, in a project holding
# `src/hit.py` with the literal `PROJECT-FILE-275` in it:
#
#     "find PROJECT-FILE-275 in this project"        -> full directory listing, needle never searched
#     "grep this project for PROJECT-FILE-275"       -> full directory listing
#     "where is PROJECT-FILE-275 in this project"    -> full directory listing
#
# None of the three matches `_FOLDER_OVERVIEW_RE`; all three reach the LOOSE arm below, where a bare
# demonstrative ("...in this project") is enough to claim the turn. An inventory is not an answer to
# "where is X" -- it is a confident wrong one, and it arrives in 0s looking authoritative.
#
# `look AT` is deliberately absent: "what should i look at in this repo" reaches the loose arm for
# the same reason and genuinely does want the folder read. `find` is guarded so that "find the
# folder we are in" -- naming the container, not a needle -- is still an overview.
_LOOKING_FOR_SOMETHING_RE = re.compile(
    r"\bgrep\b"
    r"|\bsearch\b[^.\n]{0,40}?\bfor\b"
    r"|\blook\s+for\b"
    r"|\blocate\b"
    r"|\bhunt\s+(?:down|for)\b"
    r"|\bwhere\s+(?:is|are)\b"
    r"|\b(?:which|what)\s+files?\s+(?:contain|mention|use|reference|have|has)\b"
    r"|\bfind\b(?!\s+(?:the|this|that|my|our|a|an)\s+"
    r"(?:folder|project|repo|repository|directory|dir|codebase|code\s*base|workspace)\b)",
    re.IGNORECASE,
)

# The project noun followed by something a directory listing cannot answer. See the veto in
# `maybe_handle_folder_overview_request` for why this exists and why "structure" is not in it.
_PROJECT_ASPECT_RE = re.compile(
    r"\b(?:project|folder|repo|repository|codebase|code\s*base|directory|dir|code)\s+"
    r"(?:architecture|design|security|performance|quality|maintainability|complexity|"
    r"testability|conventions?|patterns?|tradeoffs?|history|roadmap|posture)\b",
    re.IGNORECASE,
)


# An EXACT-COUNT / MEASUREMENT ask over the bound workspace: "count how many Python files exist",
# "how many .py files", "which Python file has the most lines". Measured defect this exists for:
# such a turn names the current scope ("...in this project") and no overview verb, so it reached the
# LOOSE arm below and was answered with `build_folder_overview`'s tree -- "Top level (30 folders,
# 48 files)" -- whose own folder/file counts look like an answer to a count request while none of
# the requested facts (the typed count, the largest file, its path, its line count) are present.
#
# The ask is detected STRUCTURALLY -- a file type named by extension or language word, combined
# with a counting or a most-lines superlative -- and never by any one phrasing: "do not estimate"
# is in the recorded reproduction but is deliberately not load-bearing here, so a count request
# without it is served exactly the same way. `maybe_handle_folder_overview_request` resolves these
# turns with `build_exact_file_facts`, which reads every matching file on disk; the answer states
# its scope and exclusions so the number is reproducible.
_DOTTED_TYPE_FILES_RE = re.compile(r"\.([A-Za-z0-9]{1,8})\s+files?\b", re.IGNORECASE)
_LANGUAGE_TYPE_FILES_RE = re.compile(
    r"\b(python|javascript|typescript|rust|ruby|java|go|markdown|yaml|json|text)\s+files?\b",
    re.IGNORECASE,
)
_LANGUAGE_TO_EXTENSION = {
    "python": "py",
    "javascript": "js",
    "typescript": "ts",
    "rust": "rs",
    "ruby": "rb",
    "java": "java",
    "go": "go",
    "markdown": "md",
    "yaml": "yaml",
    "json": "json",
    "text": "txt",
}
_COUNT_ASK_RE = re.compile(
    r"\b(?:how\s+many|count|number\s+of|total\s+number\s+of|give\s+me\s+the\s+number\s+of)\b",
    re.IGNORECASE,
)
_MOST_LINES_ASK_RE = re.compile(
    r"\bmost\s+lines\b"
    r"|\bline\s+count\b"
    r"|\bhow\s+many\s+lines\b"
    r"|\b(?:largest|biggest)\s+(?:\w+\s+)?files?\b",
    re.IGNORECASE,
)


def _measured_file_type(text: str) -> tuple[str, str]:
    """The (extension, label) of a file type this measurement ask names, or ("", "").

    The label preserves the user's own word for the type ("Python files: 41"); the extension is
    what is actually counted on disk. Nothing fires without a CONCRETE type: "how many files are
    in this project" has no type to count, and answering it with a top-level tally would be the
    tree answer this lane exists to stop.
    """
    dotted = _DOTTED_TYPE_FILES_RE.search(text)
    if dotted:
        return dotted.group(1).lower(), ""
    language = _LANGUAGE_TYPE_FILES_RE.search(text)
    if language:
        word = language.group(1).lower()
        return _LANGUAGE_TO_EXTENSION.get(word, ""), word.capitalize()
    return "", ""


# "which folder am I in?" is a question about a SETTING, not a request to read the folder. Measured
# 2026-07-29 across 9 phrasings x 4 workspaces: 4 phrasings were claimed by the folder-overview fast
# path and answered with an 8-, 14- or 46-line directory dump, and 5 fell through to the model, took
# 3-60s, and never named the folder at all. 4/36 passed. A detector for this existed
# (`_asks_which_workspace`) but its only production caller was the WORKFLOW planner, which a chat
# turn never reaches -- so the unit test that imported it directly proved the detector worked and
# nothing about the route.
_WORKSPACE_IDENTITY_RE = re.compile(
    r"(?:"
    # "what/which folder|project|workspace ... (is|are) ..." -- the question form
    r"(?:what|which)(?:'s|s|\s+is|\s+are)?\s+(?:the\s+|our\s+|my\s+|this\s+)?"
    r"(?:current\s+|active\s+|bound\s+)?(?:folder|directory|dir|workspace|project)\b"
    # imperatives: tell me / show me / confirm / name ...
    r"|(?:tell|show)\s+me\s+(?:which|what|the)\s+(?:folder|directory|workspace|project|path)"
    r"|(?:confirm|name|state)\s+(?:the\s+|our\s+|my\s+|this\s+)?"
    r"(?:workspace|project|folder|directory)\b"
    # "am i in the right workspace", "are we in the right folder"
    r"|(?:am\s+i|are\s+we)\s+(?:in|on|inside)\s+(?:the\s+)?(?:right|correct)?\s*"
    r"(?:folder|directory|workspace|project)"
    # "where am i", "where are we"
    r"|where\s+(?:am\s+i|are\s+we)\b"
    r")",
    re.IGNORECASE,
)

# What may follow the workspace noun and still leave the WORKSPACE as the subject of the question:
# a copula or auxiliary, a pronoun or determiner, a preposition, or another workspace word ("the
# workspace FOLDER", "confirm the workspace PATH", "what is my project NAME"). Anything else makes
# the workspace noun a MODIFIER of some other subject, and that subject is not something this lane
# holds a value for.
#
# Measured 2026-08-17 in a 50-turn chat where the user had said "my project budget is 4200 euros"
# in turn 2: "What is my project budget?" was claimed by this lane at turn 6 and answered with the
# .app bundle path, model_ran=False. `_WORKSPACE_IDENTITY_RE` stops at "what is my project" and
# never looked at the word after it, so every "my project <anything>" question was a folder answer.
#
# "and"/"or" are deliberately absent: a question that continues past the workspace noun into a
# second subject is a compound this lane can only half-answer, and half an answer is the defect.
#: The DOMAIN-FREE half: words that can follow any noun without making it a modifier of something
#: else. Shared because the same question -- "is the matched noun what this is ABOUT, or is it
#: describing another subject?" -- is asked by more than one lane, and a second copy of this list
#: would drift. `core/web/api/runtime.py` asks it of the private-memory field words, where
#: "my preference" is a stored field and "my preference FILE in vscode" is a file.
SUBJECT_TAIL_WORDS = frozenset(
    {
        # copulas and auxiliaries
        "is", "are", "was", "were", "am", "be", "been", "being",
        "do", "does", "did", "has", "have", "had",
        "can", "could", "will", "would", "shall", "should", "must",
        # pronouns and determiners
        "i", "we", "you", "they", "it", "me", "us",
        "this", "that", "these", "those", "my", "our", "your", "their", "its",
        "the", "a", "an",
        # deictics and adverbs that keep the question about the same thing
        "here", "there", "now", "again", "currently", "right", "exactly",
        "please", "actually", "even", "still", "already",
        # participles that describe the binding rather than a new subject
        "set", "bound", "scoped", "open", "active", "used", "using",
        "called", "named", "selected", "configured", "loaded", "pointed", "pointing",
        # prepositions
        "in", "on", "at", "to", "of", "for", "from", "with", "by", "into",
    }
)

#: The general set plus the words that CHAIN within this domain rather than change the subject.
_WORKSPACE_NOUN_TAIL_WORDS = SUBJECT_TAIL_WORDS | frozenset(
    {
        "folder", "folders", "directory", "directories", "dir", "workspace", "workspaces",
        "project", "repo", "repository", "codebase", "code", "base", "path", "root", "name",
    }
)

# `\w+` rather than `\S+` so that "project?" and "project." read as "nothing follows".
_WORKSPACE_NOUN_TAIL_RE = re.compile(r"\s*(\w+)", re.IGNORECASE)


def _workspace_noun_is_the_subject(text: str, match: re.Match[str]) -> bool:
    """True when the matched workspace noun is what the question is ABOUT.

    ``match`` is the ``_WORKSPACE_IDENTITY_RE`` match, so this reads the word directly after the
    noun that made this lane want the turn -- not any workspace word anywhere in the sentence.
    """
    tail = _WORKSPACE_NOUN_TAIL_RE.match(text, match.end())
    if tail is None:
        # End of string, or only punctuation left: the noun is the head of the question.
        return True
    return tail.group(1).casefold() in _WORKSPACE_NOUN_TAIL_WORDS


# An overview asks what the project IS; identity asks WHICH one it is. "what is this project about"
# and "explain this codebase" must keep reaching the overview reader, so the about/describe sense is
# excluded here rather than left to regex ordering.
_WORKSPACE_ABOUT_RE = re.compile(
    r"\b(?:about|explain|describe|summari[sz]e|overview|walk\s+me\s+through|what\s+does\s+it\s+do"
    r"|whats?\s+in\b|what\s+is\s+in\b)\b",
    re.IGNORECASE,
)


def _another_domain_owns_the_turn(text: str, *, lane_id: str = "turn_frontdoor_deterministic") -> bool:
    """Whether a registered lane outside the workspace domain owns every demand unit of `text`.

    `lane_id` is the lane the claiming arm finalizes under: the front-door tier for the two
    admissions below, `workspace_read_fast_path` for the workspace-runtime search arm (whose claims
    the registry ranks as the tier when its read capability covers no unit).

    The two workspace-scope answers below (which folder the chat is bound to, and what is in it)
    are arms of `turn_frontdoor_deterministic`, and the front door runs them before operator
    dispatch, which the lane catalog ranks ahead of them. Their admissions key on scope nouns that
    another capability's request carries as ARGUMENTS. Measured on the served path (2026-09-15),
    with the synthetic Notes runner: 'rename my Apple note "Plan" in the Work folder to "Plan v2"'
    and 'append to my Apple note "Packing list" in the Local folder with "passport and charger"'
    were answered with a workspace listing (the loose arm read "the Work folder" and "the Local
    folder" as the bound workspace), 'append to my Apple note "Plan" with "which folder am I in?"'
    with the bound folder's name, and no Notes script ran. The operator parser had read each one
    as an Apple Notes request all along.

    So both admissions ask the registry before claiming (`registered_owner_ahead_of`), with no
    phrase list: a turn the catalog assigns to another domain's lane is not a question about this
    workspace. Workspace-domain lanes are siblings, not owners ("check this work folder" keeps the
    front door's own order). A registry fault keeps the pre-existing claim.
    """
    try:
        from core.agent_runtime.demand_ownership import registered_owner_ahead_of

        return bool(registered_owner_ahead_of(text, lane_id, capability="workspace_read"))
    except Exception:
        return False


def maybe_handle_workspace_identity_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    """Answer "which folder is this chat bound to?" with the folder, in one line.

    Runs BEFORE the folder-overview handler: both match a demonstrative + "folder", and the overview
    reader is the nearest thing that fires, so without this the answer to a metadata question is a
    directory listing.
    """
    if source_surface not in {"channel", "openclaw", "api"}:
        context_surface = str((source_context or {}).get("surface") or "").strip()
        if context_surface not in {"channel", "openclaw", "api"}:
            return None
    text = " ".join(str(user_input or "").split()).strip()
    if not text or len(text) > 160:
        return None
    if _WORKSPACE_ABOUT_RE.search(text) or _WORKSPACE_READ_FILE_RE.search(text):
        return None
    from core.execution.planner import (
        is_conversational_memory_recall,
        looks_like_explicit_workspace_search_request,
    )

    # A search names a target inside the workspace; it must reach the search tool, not be answered
    # with the workspace's own name.
    if is_conversational_memory_recall(text):
        return None
    if looks_like_explicit_workspace_search_request(text) or re.search(
        r"\b(?:find|search|locate|grep|list|show\s+all|open|read|create|make|delete|move)\b",
        text.lower(),
    ):
        return None
    identity_match = _WORKSPACE_IDENTITY_RE.search(text)
    if identity_match is None:
        return None
    # "my project BUDGET" says "project", but the question is about the budget -- a value this lane
    # has never held and cannot get. Answering it with the bound folder is a confident wrong answer
    # delivered in 0s, which is worse than declining.
    if not _workspace_noun_is_the_subject(text, identity_match):
        return None
    # Another domain's request that merely carries a workspace noun ('append to my Apple note
    # "Plan" with "which folder am I in?"') is that lane's turn -- see `_another_domain_owns_the_turn`.
    if _another_domain_owns_the_turn(text):
        return None

    from core.runtime_execution_tools import execute_runtime_tool

    result = execute_runtime_tool("workspace.identity", {}, source_context=dict(source_context or {}))
    response = str(getattr(result, "response_text", "") or "").strip()
    if not result.ok or not response:
        return None
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.97,
        source_context=source_context,
        reason="workspace_identity_fast_path",
    )


def maybe_handle_folder_overview_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    """Answer "what is this project/folder about?" from the REAL folder, never a guess.

    Fires only for a folder-level overview ask that names no specific file (a named file defers to
    the direct file reader) and is not a build request. Reads the resolved workspace on disk and
    returns a grounded summary, or clear guidance when the chat isn't bound to a folder — so the
    model can never claim it "can't access folders" and confabulate.
    """
    if source_surface not in {"channel", "openclaw", "api"}:
        context_surface = str((source_context or {}).get("surface") or "").strip()
        if context_surface not in {"channel", "openclaw", "api"}:
            return None
    text = " ".join(str(user_input or "").split()).strip()
    if not text:
        return None
    if looks_like_agentic_build_request(text):
        return None
    # A request for a poem or a story names its subject, and a scope such as "in this project" is part
    # of that subject, not a question about the folder (`grounded_mode.creative_writing_remainder`).
    # Measured on the served path (2026-09-15): once the live lanes stopped reading "write a haiku about
    # rain in this project" as a weather lookup, the loose arm below answered it with a listing of the
    # bound workspace. Only the rest of the request can be a folder question. (Composed beside the
    # coding repair-demand veto by the release integration.)
    from core.agent_runtime.grounded_mode import creative_writing_remainder

    text = " ".join(creative_writing_remainder(text).split())
    if not text:
        return None

    # A repair demand ("fix the bug in this project", with or without the word "test") is
    # coding work the task plane owns, not a request to look at the folder. Measured on the
    # v1 candidate: the overview's narrower explicit-code-check predicate still consumed
    # repair phrasings the demand resolver ALREADY seats `code.task.open` for, so the two
    # authorities disagreed and the coding assistant never saw the turn. The resolver IS the
    # shared demand authority (the tool offer and this fast path both sit downstream of it),
    # so this veto reads exactly what it seats: if the resolver says this text wants the code
    # task plane, the overview declines -- for advice-shaped phrasings the advice veto below
    # already declines too, and a listing is never the right answer for either.
    from core.instructional_request import asks_for_instructions_not_execution
    from core.tool_demand_signals import resolve_demand_signals

    try:
        seated = "code.task.open" in resolve_demand_signals(text).explicit_intents
    except Exception:
        seated = False
    if seated and not asks_for_instructions_not_execution(text):
        return None
    from core.execution.planner import looks_like_explicit_workspace_search_request

    # Explicit searches must reach the workspace/tool path -- that broad "this workspace" signal must
    # not turn a search request into a folder overview. Two detectors are ORed ON PURPOSE: KAS's named
    # planner helper, plus the verb+locator regex, which catches phrasings the helper misses
    # ("where is X mentioned", "locate the file that references Y"). Routing coverage is the whole
    # point here, so the union is the correct resolution of the parallel fix.
    lowered = text.lower()
    if looks_like_explicit_workspace_search_request(text) or (
        re.search(r"\b(?:find|search|locate)\b", lowered)
        and re.search(
            r"\b(?:where|mention(?:s|ed|ing)?|contain(?:s|ed|ing)?|reference(?:s|d)?|wired|defined|implemented)\b",
            lowered,
        )
    ):
        return None

    # An exact-count ask over the named file type is a MEASUREMENT of the workspace, and the tree
    # is a confident wrong answer to it (see `_measured_file_type` for the measured defect). It is
    # resolved here -- before the overview arms can claim it -- by reading every matching file on
    # disk. An advice-shaped sentence that merely mentions counting ("is it wise to count all the
    # python files in this repo?") is still a request for judgement, and keeps the model lane, so
    # the advice veto applies here exactly as it does to the loose arm below. A turn with no usable
    # workspace bound keeps today's behaviour: `build_exact_file_facts` declines and the overview
    # guidance below is what the user sees.
    extension, type_label = _measured_file_type(text)
    if extension and (_COUNT_ASK_RE.search(text) or _MOST_LINES_ASK_RE.search(text)):
        from core.agent_runtime import build_request_intent

        if not build_request_intent.is_advice_question(text) and not _another_domain_owns_the_turn(text):
            from core.folder_overview import build_exact_file_facts

            workspace_root = str(
                (source_context or {}).get("workspace")
                or (source_context or {}).get("workspace_root")
                or ""
            ).strip()
            measured = build_exact_file_facts(workspace_root, extension, label=type_label)
            if measured is not None:
                return agent._fast_path_result(
                    session_id=session_id,
                    user_input=user_input,
                    response=measured,
                    confidence=0.97,
                    source_context=source_context,
                    reason="workspace_measurement_fast_path",
                )

    from core.execution.constants import refers_to_current_scope

    # A demonstrative like "analyse the local folder we are in" points at the BOUND project, but the
    # verb+noun regex misses it ("local" sits between the verb and "folder"). Route it here so it
    # reads the bound root, rather than falling through to the model (confabulation).
    if not _FOLDER_OVERVIEW_RE.search(text):
        if not refers_to_current_scope(text):
            return None
        # Reaching here means NOTHING in the sentence asked to see the folder -- only a bare
        # demonstrative ("...for this project") pointed at it. That is the loose signal, and it
        # claimed a question that was about the project rather than a request to read it: "is it a
        # good idea to create a Makefile for this project?" was answered, in 0s, with a full
        # directory listing of the workspace. An advice question gets an answer, not a listing.
        #
        # Only the ADVICE subset of the deliberation vocabulary vetoes here. The full list would
        # also swallow "explain the local folder we are in" and "what should i look at in this
        # repo", which reach this arm for the same reason and DO want the folder read.
        from core.agent_runtime import build_request_intent

        if build_request_intent.is_advice_question(text):
            return None
        # ...and, for the same reason, a request that names something to LOOK FOR. The demonstrative
        # says WHERE to look; it does not make "where is X in this project" a request to list the
        # project. See `_LOOKING_FOR_SOMETHING_RE` for the three measured phrasings.
        if _LOOKING_FOR_SOMETHING_RE.search(text):
            return None
    # A specific file was named ("explain README.md", "read config.json") — the direct file reader
    # owns that; this handler is only for a whole-folder overview.
    if _WORKSPACE_READ_FILE_RE.search(text):
        return None
    # An ASPECT of the project was named, so the request is for synthesis over the contents rather
    # than for the contents. `_FOLDER_OVERVIEW_RE` stops at the project noun and ignores whatever
    # follows it, so "Summarize this repository architecture." matched on "summarize this
    # repository" and was answered, in 0s and with no model, by a directory listing. A file
    # inventory is not an architecture; answering one with the other is a confident wrong answer,
    # which is worse than the confabulation this lane exists to prevent.
    #
    # Only aspects a listing genuinely cannot answer are named. "structure", "layout" and "contents"
    # are deliberately absent -- a listing IS the structure, and those phrasings stay here.
    if _PROJECT_ASPECT_RE.search(text):
        return None

    # A listing is not an answer to another capability's request that names a folder or quotes a
    # project phrase as its payload ('rename my Apple note "Plan" in the Work folder to ...').
    # Asked last, so only a turn this lane would otherwise claim pays for the registry read.
    if _another_domain_owns_the_turn(text):
        return None

    from core.folder_overview import build_folder_overview

    workspace_root = str(
        (source_context or {}).get("workspace") or (source_context or {}).get("workspace_root") or ""
    ).strip()
    project_bound = bool(str((source_context or {}).get("project_id") or "").strip())
    response = build_folder_overview(workspace_root, project_bound=project_bound)
    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.97,
        source_context=source_context,
        reason="folder_overview_fast_path",
    )


# A scheduled-event or possessive "time" ("what time is my flight", "my time off", "meeting time")
# is NOT a request for the current clock; the broad "time + what/now" heuristic must skip these.
_EVENT_TIME_RE = re.compile(
    r"\btime\s+(?:is\s+)?(?:my|the|your|his|her|our|their)\b|"
    r"\btime\s+off\b|"
    r"\btime\s+of\b|"
    r"\b(?:my|your|his|her|our|their)\s+\w+\s+time\b|"
    r"\b(?:flight|meeting|appointment|train|bus|class|departure|arrival|reservation|show|event|game|call)\s+time\b",
    re.IGNORECASE,
)


# `time` is only a CLOCK reference when the sentence asks for the clock. The broad heuristic below
# used to require nothing more than the bare word plus "what" somewhere in the message, and a topic
# word claimed the whole turn: "What columns should that registry have so it stays useful OVER
# TIME?" was answered "Current time is 00:55 EEST." in 0.2s, reproduced 4/4 on the running daemon.
# Dropping the trailing clause answered the real question in 11.8s, and swapping "registry" for
# "tracker" still misfired — the trigger was "over time", not the noun.
#
# The rule is what the sentence ASKS FOR, not which words appear in it, so this is a list of ASK
# constructions rather than a blocklist of topics ("over time", "in time", "every time", "time to
# market", "response time", "the time it takes" — an unbounded set nobody can finish enumerating).
# Each alternative requires `time` to be the thing being asked about: the head of an interrogative
# ("what time", "what's the time"), determined as the clock ("current time", "local time"), or the
# subject of a present-tense clock predicate ("time is it", "time now").
_ASKS_TIME_RE = re.compile(
    r"\bwhat(?:'?s| is| was)?\s+(?:the\s+)?(?:current\s+|local\s+|exact\s+)?time\b"
    r"|\bwhat\s+time\b"
    r"|\b(?:current|local)\s+time\b"
    r"|\btime\s+(?:is\s+it|now|right\s+now)\b"
    r"|\b(?:tell|give)\s+me\s+(?:the\s+)?time\b"
    r"|\bdo\s+you\s+(?:know|have)\s+(?:the\s+)?time\b"
    r"|\bgot\s+the\s+time\b",
    re.IGNORECASE,
)

# "time in <place>" is a clock reference only because a real timezone was named. Without one it is
# "delivery time in Q3", so this alternative is gated on the caller having extracted a timezone.
_TIME_IN_PLACE_RE = re.compile(r"\btime\s+in\b", re.IGNORECASE)


def _time_word_is_the_clock(cleaned: str, *, has_timezone: bool = False) -> bool:
    """Whether `time` in this sentence REFERS TO THE CLOCK rather than being a topical adverbial."""
    if _ASKS_TIME_RE.search(cleaned):
        return True
    return has_timezone and bool(_TIME_IN_PLACE_RE.search(cleaned))


_ASKS_DATE_RE = re.compile(
    r"(?:"
    r"what(?:'?s| is| was)?\s+(?:the\s+)?date(?!\s+(?:of|for|on)\b)"
    r"|(?:what|wat)\s+date\s+is\s+(?:it|today|this)\b"
    r"|today'?s?\s+date\b"
    r"|\bdate\s+today\b"
    # "(?:what|wat)" admits the universal "wat"-style typo. Behind this matcher the
    # alternative is the model lane, which for a date ask is strictly riskier
    # (measured: it answered "Wednesday" on a Tuesday) — the subject
    # requirements below keep "wat day is the meeting" out.
    r"|(?:what|wat)\s+day\s+is\s+(?:it|today|this)\b"
    r"|(?:what|wat)(?:'?s| is)?\s+(?:the\s+)?day\s+today\b"
    r"|\bday\s+today\b"
    r"|tell\s+me\s+(?:the\s+|today'?s\s+)*date\b"
    r"|(?:what|wat)\s+is\s+today\b"
    # "which day of the week are we on right now?" reached the model instead of the clock, and it
    # answered "We're on a Wednesday" on a Tuesday. The list above only covered "what day is it/today",
    # so every which-/are-we-on- phrasing guessed. Each alternative below still requires a present-tense
    # subject ("it", "today", "we"), which is what keeps "what day is the meeting" out.
    r"|which\s+day(?:\s+of\s+the\s+week)?\s+(?:is\s+it|is\s+today|are\s+we\s+(?:on|in))\b"
    r"|(?:what|wat)\s+day(?:\s+of\s+the\s+week)?\s+(?:is\s+it|is\s+today|are\s+we\s+(?:on|in))\b"
    r"|(?:what|wat)\s+day\s+of\s+the\s+week\s+is\s+(?:it|today|this)\b"
    r"|\bday\s+of\s+the\s+week\s+(?:is\s+it|is\s+today|are\s+we\s+(?:on|in))\b"
    r"|(?:what|wat)'?s\s+today\b"
    r")",
    re.IGNORECASE,
)


def date_time_fast_path(
    agent: Any,
    normalized_input: str,
    *,
    source_surface: str,
    session_id: str = "",
    source_context: dict[str, object] | None = None,
    now_utc: datetime | None = None,
) -> str | None:
    """Answer a clock/date turn from the runtime's own clock, or decline.

    ``now_utc`` is a TEST SEAM: an aware reference instant that replaces the real clock so tests
    can assert exact values and DST transitions. It defaults to the real clock, is keyword-only,
    and is never derived from user input.
    """

    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = str(normalized_input or "").strip().lower()
    if not phrase:
        return None
    cleaned = phrase.strip(" \t\r\n?!.,")
    # A SUPPLIED absolute time ("what time is it in Tokyo when it is 3:00 PM on a Tuesday in New
    # York during standard time?") is a conversion, not a clock read. Measured through this seam
    # 2026-08-15: the machinery below claimed that turn and answered the MACHINE's current local
    # clock. Once the construction is recognized, this lane either converts exactly or declines --
    # the current clock is the one answer that is always wrong for it.
    if supplied_clock_frame_binding(cleaned):
        for candidate in supplied_absolute_time_conversion(cleaned, now_utc=now_utc) or ():
            if not _fixed_shape_violates_a_stated_output_contract(normalized_input, candidate):
                return candidate
        return None
    # A relative offset ("in 5h from now", "2 hours ago") is PART OF THE QUESTION, never noise.
    # Measured on the served surface, 2026-08-14: "what time is it in 5h from now in Tokyo? Output
    # exactly one word." answered 07:23 -- the current Tokyo time -- because the offset phrase was
    # consumed by no pattern and the reference instant below was hardwired to now. Every gate from
    # here on runs against `base`, the sentence with the offset span removed: that is also what
    # lets "in 5 hours in tokyo what time is it" resolve Tokyo at all, and what stops "in 90
    # minutes" from parsing as an unresolvable place. Offset-free turns have base == cleaned, so
    # their behaviour is unchanged by construction.
    offset = extract_relative_clock_offset(cleaned)
    base = offset.stripped if offset is not None else cleaned
    requested_timezone, requested_label = extract_utility_timezone(cleaned)
    recent_context = recent_utility_context(
        session_id=session_id,
        source_context=source_context,
    )
    contextual_timezone, contextual_label = contextual_time_followup_timezone(
        cleaned,
        recent_utility_context=recent_context,
    )
    effective_timezone = requested_timezone or contextual_timezone
    effective_label = requested_label or contextual_label
    # NOT `\btime\b`: the bare word is a topic in most sentences that contain it. See _ASKS_TIME_RE.
    has_time_word = _time_word_is_the_clock(base, has_timezone=bool(effective_timezone))
    # A literal marker list covered 5 of 10 natural date phrasings -- "what date is today?", "whats
    # the date", "todays date", "what is the date" and "tell me the date" all fell through to the
    # model, which then answered "I don't have a real-time clock" while the runtime had one. The
    # negative lookahead keeps "release date of X" / "due date for Y" out.
    asks_date = bool(_ASKS_DATE_RE.search(base))
    asks_time = bool(
        any(
            marker in base
            for marker in (
                "what time is it",
                "what's the time",
                "current time",
                "time now",
                "what time is now",
                "what time now",
            )
        )
        or (
            has_time_word
            and any(marker in base for marker in ("what", "now", "current", "right now"))
            and not _EVENT_TIME_RE.search(base)
        )
        or (effective_timezone and has_time_word)
        or looks_like_malformed_time_followup(
            cleaned,
            effective_timezone=effective_timezone,
            recent_utility_context=recent_context,
        )
        or bool(contextual_timezone)
    )
    if not asks_date and not asks_time:
        return None
    # MULTI-CITY: the ask names several places and wants each of their clocks.
    # Measured live 2026-08-29 (watch session 2026-08-29T1150Z): "what time is
    # in rome now and in paris?" was answered for Rome only -- Paris silently
    # dropped -- because this lane resolves a single place (first mention).
    # When TWO OR MORE distinct zones resolve, answer one sentence per city and
    # skip the offset machinery (an offset plus multiple cities is ambiguous
    # about which place the offset anchors; that shape keeps the old path).
    # Same fail-closed rule as below: an unresolvable named place declines the
    # lane rather than dropping that city.
    if asks_time and offset is None:
        all_places = extract_utility_timezones_all(base)
        if len(all_places) >= 2:
            lines = []
            for zone, label in all_places:
                try:
                    city_now = (
                        now_utc.astimezone(ZoneInfo(zone))
                        if now_utc is not None
                        else utility_now_for_timezone(zone)
                    )
                except Exception:
                    return None
                lines.append(f"Current time in {label} is {city_now:%H:%M %Z}.")
            answer = " ".join(lines)
            if _fixed_shape_violates_a_stated_output_contract(normalized_input, answer):
                return None
            return answer
    # FAIL CLOSED. The turn named a place and no zone could be found for it, so this lane cannot
    # answer. Declining hands the turn to a lane that can say so; answering would return the
    # MACHINE's clock under a `tool | date_time_fast_path | tool-generated answer` footer, with no
    # location in the text at all -- the user sees a confident, sourced-looking, wrong time and
    # nothing hints that their city was dropped. That is what "what time is it in Tokyo" did.
    if not effective_timezone and utility_names_unresolved_location(base):
        return None
    offset_binds = offset is not None and _relative_offset_binds_to_the_clock_ask(base)
    if offset_binds and offset.delta is None:
        # Understood but NOT computable in this lane: a calendar-scale shift ("in 3 days",
        # "yesterday at this time") changes the date, and two competing offsets are ambiguous.
        # DECLINE so a lane that can reason about dates gets the turn -- the one answer that is
        # always wrong here is the current clock, which is exactly what this used to return.
        return None
    try:
        if offset_binds and offset is not None and offset.delta is not None:
            # DST-safe by construction: arithmetic happens on the UTC instant, and only the
            # RESULT is rendered in the named place's zone (machine-local when none was named).
            # Wall-clock arithmetic in the target zone would be wrong across every transition.
            reference_utc = now_utc if now_utc is not None else CLOCK.now_utc()
            shifted_utc = reference_utc + offset.delta
            if effective_timezone:
                target_zone = ZoneInfo(effective_timezone)
                now = reference_utc.astimezone(target_zone)
                shifted = shifted_utc.astimezone(target_zone)
            else:
                now = reference_utc.astimezone()
                shifted = shifted_utc.astimezone()
        else:
            shifted = None
            if now_utc is not None:
                now = (
                    now_utc.astimezone(ZoneInfo(effective_timezone))
                    if effective_timezone
                    else now_utc.astimezone()
                )
            else:
                now = utility_now_for_timezone(effective_timezone)
    except Exception:
        # An unknown key reaching here is a resolver bug, not a user error. Decline rather than
        # let it degrade into local time, which is the same wrong answer one layer down.
        return None
    location_prefix = f"in {effective_label} " if effective_label else ""
    location_suffix = f" in {effective_label}" if effective_label else ""
    if shifted is not None and offset is not None and offset.delta is not None:
        # The wording must STATE the shift -- "Current time is X" alone for a shifted instant
        # would be the dropped-offset defect wearing better numbers. The current clock is stated
        # first (it anchors the arithmetic and keeps the answer recognizable as a clock answer to
        # the layers above), then the asked-for instant.
        tense = "was" if offset.delta < timedelta(0) else "will be"
        if asks_date and asks_time:
            answer = (
                f"Current time{location_suffix} is {now:%H:%M %Z} on {now:%Y-%m-%d}; "
                f"{offset.description} it {tense} {shifted:%H:%M %Z} on {shifted:%A, %Y-%m-%d}."
            )
        elif asks_date:
            lead = offset.description[:1].upper() + offset.description[1:]
            answer = f"{lead} the date{location_suffix} {tense} {shifted:%A, %Y-%m-%d}."
        else:
            answer = (
                f"Current time{location_suffix} is {now:%H:%M %Z}; "
                f"{offset.description} it {tense} {shifted:%H:%M %Z}."
            )
    elif asks_date and asks_time:
        answer = now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d. Current time is %H:%M %Z.")
    elif asks_date:
        answer = now.strftime(f"Today {location_prefix}is %A, %Y-%m-%d.")
    elif effective_label:
        answer = now.strftime(f"Current time in {effective_label} is %H:%M %Z.")
    else:
        answer = now.strftime("Current time is %H:%M %Z.")
    if _fixed_shape_violates_a_stated_output_contract(normalized_input, answer):
        # Before declining, offer the TERSER shapes this lane can also produce. The clock has a
        # correct one-word form, and "what time is it in Tokyo? Output exactly one word." is
        # answerable as "06:46" -- declining sent it to a model that replied "15", which is both
        # wrong and worse than the prose sentence it replaced. A lane declines when it cannot meet
        # the contract, not when its FIRST shape cannot. With a bound offset the terse shapes
        # render the SHIFTED instant -- the value the question asked for -- so "Output exactly
        # one word" yields the post-offset HH:MM, never the current one.
        display = shifted if shifted is not None else now
        for candidate in _terser_clock_shapes(display, asks_date=asks_date, asks_time=asks_time):
            if not _fixed_shape_violates_a_stated_output_contract(normalized_input, candidate):
                return candidate
        return None
    return answer


def _terser_clock_shapes(now: datetime, *, asks_date: bool, asks_time: bool) -> list[str]:
    """Shorter forms of the same fact, most informative first.

    Every one is the same runtime observation the sentence above reports -- only the wording is
    dropped, never the value or the zone it belongs to. The bare time is last because it is the
    only form that omits the zone, and it is offered only because a one-word contract admits
    nothing else.
    """

    shapes: list[str] = []
    if asks_time:
        shapes.append(now.strftime("%H:%M %Z"))
    if asks_date:
        shapes.append(now.strftime("%Y-%m-%d"))
    if asks_time:
        shapes.append(now.strftime("%H:%M"))
    return shapes


#: An explicitly requested output FORM. Deliberately narrow: each alternative names a form rather
#: than a topic, so "what time is it in the command centre" is untouched while "format it as a bash
#: command" is not. Anchored on the request verbs ("format ... as", "in the form of") or on an
#: explicit refusal of prose, never on a bare mention of json/bash/code.
_STATED_OUTPUT_FORM_RE = re.compile(
    r"\bno\s+conversational\s+text\b"
    r"|\bformat(?:ted)?\s+(?:the\s+\w+\s+)?(?:output\s+)?(?:strictly\s+)?as\b"
    r"|\bin\s+the\s+form\s+of\b"
    r"|\boutput\s+(?:only\s+)?(?:raw|valid)\s+\w+"
    r"|\breturn\s+it\s+as\s+(?:a\s+|an\s+)?\w+\s+(?:command|block|object|array)\b",
    re.IGNORECASE,
)


def _fixed_shape_violates_a_stated_output_contract(user_text: str, answer: str) -> bool:
    """Whether this lane's FIXED sentence breaks an output shape the user asked for.

    This lane answers in one shape it chose. When the turn states its own shape, the two can
    conflict and the fast path wins by arriving first -- measured on the served surface:

        "... what time it is in Tokyo right now. But here is the catch: format the time output
         strictly as a valid bash command that echoes the time to a text file. No conversational
         text."
        -> "Current time in Tokyo is 06:22 JST."

    The clock was right and the format was ignored, because nothing between the request and the
    answer ever compared them. A lane that cannot honour a stated contract must DECLINE, so a lane
    that can gets the turn -- the same rule the literal-output contract already follows.

    Checked by COMPLIANCE, not by enumerating contract kinds: the answer this lane is about to
    return is measured against whatever the turn actually stated. A contract it happens to satisfy
    (plain text, no markdown) costs nothing and the lane still answers.
    """

    text = str(user_text or "")
    if not text.strip():
        return False
    # A stated output FORM this lane cannot produce. It emits one prose sentence; a turn asking for
    # a command, a code block, JSON, or explicitly no prose is asking for something else, and no
    # contract parser recognises "format it as a bash command" as a contract at all -- measured,
    # `parse_raw_output_contract` returns None for it, so the compliance checks below never fire.
    if _STATED_OUTPUT_FORM_RE.search(text):
        return True
    try:
        from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract

        contract = parse_raw_output_contract(text)
        if contract is not None:
            application = apply_raw_output_contract(answer, contract)
            # `changed` as well as `compliant`: apply REPAIRS the text and then reports it
            # compliant, so a contract that rewrote this lane's sentence into something else looks
            # satisfied. If the contract had to change the answer, the fixed shape did not meet it.
            if getattr(application, "changed", False) or not getattr(application, "compliant", True):
                return True
    except Exception:
        # A parser fault must not take the clock down; answering remains the safe default here,
        # because the contract layers downstream still see the turn.
        pass
    try:
        from core.response_constraints import check_response_constraint, parse_response_constraint

        constraint = parse_response_constraint(text)
        if constraint is not None:
            verdict = check_response_constraint(answer, constraint)
            if not getattr(verdict, "compliant", True):
                return True
    except Exception:
        pass
    return False


def direct_math_fast_path(normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    return evaluate_direct_math_request(normalized_input) or evaluate_word_math_request(normalized_input)



_ACTION_HISTORY_QUESTION_RE = re.compile(
    r"^(?:have|did)\s+you\s+(?:created|create|edited|edit|modified|modify|deleted|delete|written|write)"
    r"(?:\s+or\s+(?:created|edited|modified|deleted|written))*\s+any\s+files?"
    r"(?:\s+in\s+(?:this|our)\s+(?:conversation|session))?(?:\s+so\s+far)?[?]?$",
    re.IGNORECASE,
)


def action_history_honesty_fast_path(
    user_input: str,
    *,
    source_surface: str,
    session_id: str,
    source_context: dict[str, object] | None,
) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    normalized = " ".join(str(user_input or "").strip().split())
    if not _ACTION_HISTORY_QUESTION_RE.fullmatch(normalized):
        return None
    prior_history = [
        item
        for item in list((source_context or {}).get("conversation_history") or [])
        if isinstance(item, dict)
        and str(item.get("role") or "").strip().lower() in {"assistant", "system"}
        and str(item.get("content") or "").strip()
    ]
    if prior_history:
        return None
    try:
        if recent_conversation_events(session_id, limit=1):
            return None
        if list_runtime_tool_receipts(session_id, limit=1):
            return None
    except Exception:
        return None
    return (
        "No. No file operation has been recorded in this session, so I have not "
        "created, edited, deleted, or modified any files in this conversation."
    )


@lru_cache(maxsize=1)
def _canonical_zone_leaves() -> dict[str, str]:
    """City name -> IANA zone, built from the tzdb's own CANONICAL table.

    The universe is `zone1970.tab`/`zone.tab` (418 zones here), NOT
    `zoneinfo.available_timezones()` (598). That choice is the entire safety property, and it is
    not a matter of taste -- measured on this machine, the wider set is where every trap lives:

      * compass and region words that are ordinary English: north, south, east, west, central,
        eastern, pacific, atlantic, mountain, act, general, continental, universal, factory;
      * abbreviations and pseudo-zones: MET, WET, ROC, EST, Zulu, Factory;
      * `Etc/GMT+5`, which POSIX defines as UTC MINUS 5 -- answering "time in GMT+5" ten hours
        wrong, with a tool-grounded footer;
      * all 27 multi-hit leaves, of which `west` genuinely diverges by 12 hours
        (Australia/West vs Brazil/West).

    Against the canonical table every one of those resolves to nothing, there are zero multi-hit
    leaves, and real settlements -- tokyo, phoenix, new york, sao paulo -- still resolve. A curated
    city list was considered and rejected: it is the same defect as today's three-entry table, just
    larger, and it misses Phoenix, Honolulu, Nome and Chatham.
    """

    leaves: dict[str, list[str]] = {}
    for base in zoneinfo.TZPATH:
        for name in ("zone1970.tab", "zone.tab"):
            path = Path(base) / name
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                zone = parts[2].strip()
                if not zone:
                    continue
                leaf = zone.rsplit("/", 1)[-1].replace("_", " ").strip().lower()
                if leaf:
                    leaves.setdefault(leaf, []).append(zone)
        if leaves:
            break
    resolved: dict[str, str] = {}
    for leaf, zones in leaves.items():
        unique = sorted(set(zones))
        if len(unique) == 1:
            resolved[leaf] = unique[0]
            continue
        # A future tzdb release could introduce a collision. Two zones that report the same clock
        # are interchangeable; two that do not are genuinely ambiguous and must not be guessed.
        offsets = set()
        for zone in unique:
            try:
                offsets.add(datetime.now(ZoneInfo(zone)).utcoffset())
            except Exception:
                offsets.add(None)
        if len(offsets) == 1:
            resolved[leaf] = unique[0]
    return resolved


#: `in`/`at` IMMEDIATELY before the name, with no article. The article is the signal that separates
#: a place from a thing: nobody says "in the Tokyo", while "in the east", "at the factory" and
#: "in the west wing" are regions and objects. The name must also END the prepositional object --
#: that is what rejects "in wake of the outage", "in jersey shore reruns", "in oral argument",
#: "in troll mode", "in north korea" and "in west virginia" while keeping "in tokyo right now" and
#: "in oslo please" through the short continuation list.
#: Words that may FOLLOW the place but are never part of its name. Excluded from the capture as
#: well as accepted after it, because the quantifier is greedy: without this, "in new york right
#: now" captures "new york right" -- the lookahead is satisfied by the trailing "now", so the regex
#: never backtracks, and a real city silently fails to resolve.
_PLACE_TAIL_WORDS = r"right|now|today|please|currently|moment|at|is|it"

_LOCATIVE_PLACE_RE = re.compile(
    r"\b(?:in|at)\s+"
    # A period may JOIN letters ("st.petersburg") but never trail one. With `.` simply inside the
    # class, "what time is it in tokyo. no markdown" captured the place as "tokyo. no markdown" --
    # no zone matched, the fail-closed rule fired, and a perfectly good question got no answer.
    rf"(?P<place>(?!(?:{_PLACE_TAIL_WORDS})\b)[a-z][a-z'\-]*(?:\.[a-z][a-z'\-]*)*"
    rf"(?:\s+(?!(?:{_PLACE_TAIL_WORDS})\b)[a-z][a-z'\-]*(?:\.[a-z][a-z'\-]*)*){{0,2}})"
    r"(?=\s*(?:$|[?!.,;]|\b(?:right\s+now|now|today|please|currently|at\s+the\s+moment)\b))",
    re.IGNORECASE,
)

#: Offset pseudo-zones a user may legitimately name. The canonical table deliberately excludes
#: them, and they are unambiguous, so they are listed rather than inferred. `Etc/GMT+N` is NOT
#: here on purpose -- its sign is inverted and a user typing "GMT+5" does not mean UTC-5.
_UTILITY_OFFSET_ALIASES = {
    "utc": ("UTC", "UTC"),
    "gmt": ("UTC", "GMT"),
    "zulu": ("UTC", "UTC"),
}


#: A relative shift attached to a clock ask ("in 5h from now", "2 hours ago", "in 90 minutes").
#: Measured on the served surface, 2026-08-14: "what time is it in 5h from now in Tokyo? Output
#: exactly one word." answered 07:23 -- the CURRENT Tokyo time -- because no pattern in this module
#: consumed the offset phrase and line-for-line the reference instant was hardwired to now. The
#: offset is part of the question; dropping it turns a correct pipeline into a confident wrong
#: answer under a tool-attributed footer.
#:
#: The grammar is construction-keyed, never prompt-keyed: a quantity plus a clock-scale unit inside
#: an "in ..."/"... from now"/"... later"/"... ago" frame, plus the closed natural-language set
#: ("half an hour", "an hour", "a quarter of an hour"). "5 High Street" has no unit token and
#: "5h flight" sits in no frame, so neither can match. A bare "m" is excluded as ambiguous.
_RELATIVE_OFFSET_QTY = r"\d{1,4}(?:\.\d{1,2})?"
_RELATIVE_OFFSET_UNIT = r"hours?|hrs?|minutes?|mins?|h"
_RELATIVE_OFFSET_NATURAL = r"half\s+an\s+hour|an\s+hour|a\s+quarter\s+of\s+an\s+hour"
_RELATIVE_OFFSET_APPROX = r"(?:about\s+|roughly\s+|around\s+)?"

_RELATIVE_CLOCK_OFFSET_RE = re.compile(
    rf"\bin\s+{_RELATIVE_OFFSET_APPROX}(?:{_RELATIVE_OFFSET_QTY})\s*"
    rf"(?:{_RELATIVE_OFFSET_UNIT})(?:\s+from\s+now)?\b"
    rf"|\b(?:{_RELATIVE_OFFSET_QTY})\s*(?:{_RELATIVE_OFFSET_UNIT})\s+(?:from\s+now|later)\b"
    rf"|\b(?:{_RELATIVE_OFFSET_QTY})\s*(?:{_RELATIVE_OFFSET_UNIT})\s+(?:ago|earlier)\b"
    rf"|\bin\s+{_RELATIVE_OFFSET_APPROX}(?:{_RELATIVE_OFFSET_NATURAL})(?:\s+from\s+now)?\b"
    r"|\b(?:half\s+an\s+hour|an\s+hour)\s+(?:ago|earlier)\b",
    re.IGNORECASE,
)

#: Calendar-scale shifts this lane UNDERSTANDS but cannot compute as a clock answer: the date
#: changes, and the sentence is asking about a different day. These force a DECLINE -- the claim is
#: handed to the next lane -- because the one thing that is always wrong is the current clock.
#: Before this arm existed, "what time will it be in 3 days" answered the machine's current time.
_RELATIVE_CALENDAR_OFFSET_RE = re.compile(
    rf"\bin\s+{_RELATIVE_OFFSET_APPROX}(?:{_RELATIVE_OFFSET_QTY})\s*"
    r"(?:days?|weeks?|months?|years?)(?:\s+from\s+now)?\b"
    rf"|\b(?:{_RELATIVE_OFFSET_QTY})\s*(?:days?|weeks?|months?|years?)\s+"
    r"(?:from\s+now|later|ago|earlier)\b"
    r"|\b(?:yesterday|tomorrow)\s+at\s+(?:this|the\s+same)\s+time\b"
    r"|\b(?:at\s+)?(?:this|the\s+same)\s+time\s+(?:yesterday|tomorrow)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RelativeClockOffset:
    """A relative temporal qualifier found in a clock turn.

    ``delta is None`` means the shift was RECOGNIZED but is not computable by this lane
    (calendar-scale units, or two competing offsets); the caller must decline the claim, never
    answer the current clock. ``stripped`` is the sentence with every offset span removed, which
    is what place extraction and the ask gates must run against.
    """

    delta: timedelta | None
    description: str
    stripped: str


def _without_spans(text: str, spans: list[tuple[int, int]]) -> str:
    kept: list[str] = []
    last = 0
    for start, end in sorted(spans):
        if start > last:
            kept.append(text[last:start])
        last = max(last, end)
    kept.append(text[last:])
    return " ".join(" ".join(kept).split())


def _parse_relative_offset_phrase(phrase: str) -> tuple[timedelta | None, str]:
    """One matched offset span -> (signed delta, human description) or (None, "")."""

    text = " ".join(str(phrase or "").lower().split())
    words = text.split()
    negative = "ago" in words or "earlier" in words
    if "half an hour" in text:
        minutes, quantity_text, unit_word = 30.0, "30", "minutes"
    elif "quarter of an hour" in text:
        minutes, quantity_text, unit_word = 15.0, "15", "minutes"
    elif re.search(r"\ban\s+hour\b", text):
        minutes, quantity_text, unit_word = 60.0, "1", "hour"
    else:
        match = re.search(
            rf"({_RELATIVE_OFFSET_QTY})\s*({_RELATIVE_OFFSET_UNIT})\b", text, re.IGNORECASE
        )
        if not match:
            return None, ""
        quantity = float(match.group(1))
        in_minutes = match.group(2).startswith("min")
        minutes = quantity * (1.0 if in_minutes else 60.0)
        quantity_text = match.group(1)
        unit_word = ("minute" if in_minutes else "hour") + ("" if quantity == 1 else "s")
    if minutes <= 0:
        return None, ""
    delta = timedelta(minutes=minutes)
    if negative:
        return -delta, f"{quantity_text} {unit_word} ago"
    return delta, f"in {quantity_text} {unit_word}"


def extract_relative_clock_offset(cleaned_input: str) -> RelativeClockOffset | None:
    """The relative shift a turn attaches to the clock, or None when it attaches none.

    Returning None means "no offset phrase present" -- the sentence is exactly as old code saw
    it, so offset-free behaviour is unchanged by construction (``stripped`` would equal the
    input). A non-None result with ``delta=None`` means "shift recognized, not computable here":
    the caller must DECLINE, because silently dropping the qualifier answers a different question.
    """

    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return None
    clock_matches = list(_RELATIVE_CLOCK_OFFSET_RE.finditer(lowered))
    calendar_matches = list(_RELATIVE_CALENDAR_OFFSET_RE.finditer(lowered))
    if not clock_matches and not calendar_matches:
        return None
    spans = [match.span() for match in clock_matches + calendar_matches]
    stripped = _without_spans(lowered, spans)
    if calendar_matches:
        return RelativeClockOffset(delta=None, description="", stripped=stripped)
    parsed = [_parse_relative_offset_phrase(match.group(0)) for match in clock_matches]
    deltas = {delta for delta, _ in parsed}
    if len(deltas) != 1 or None in deltas:
        # Two competing shifts (or an unparsable one) make the reference instant ambiguous.
        # Ambiguity declines; it never falls back to the current clock.
        return RelativeClockOffset(delta=None, description="", stripped=stripped)
    delta, description = parsed[0]
    return RelativeClockOffset(delta=delta, description=description, stripped=stripped)


#: ---- Supplied-absolute-time zone conversion ----------------------------------------------------
#: "What time is it in Tokyo when it is 3:00 PM on a Tuesday in New York during standard time?"
#: is exactly computable and carries NO dependence on the current clock. Measured through this
#: seam 2026-08-15: the turn was claimed by `date_time_fast_path` and answered
#: "Current time is 13:24 EEST" -- the MACHINE's local clock, under a tool-attributed footer,
#: for a question about two other places and a stipulated instant. (On the served surface the
#: same family also reached weak local models and came back empty.) The grammar below is
#: construction-keyed: a stipulated clock ("when/if it is <time> [on <weekday>] in <zoneA>
#: [during standard time]") bound to a zone-targeted ask ("what time is it in <zoneB>"). When the
#: construction is recognized but not exactly computable -- a vague time ("tuesday afternoon"),
#: an unresolvable place, an ambiguous no-meridiem clock -- the lane DECLINES, because for this
#: construction the one answer that is always wrong is the current clock.

_SUPPLIED_CLOCK_WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
)
_SUPPLIED_CLOCK_WEEKDAY_ALT = "|".join(_SUPPLIED_CLOCK_WEEKDAYS)

_SUPPLIED_CLOCK_TIME = (
    r"(?:(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>a\.?m\.?|p\.?m\.?)?"
    r"|(?P<named>noon|midday|midnight))"
)

_SUPPLIED_CLOCK_STIPULATION_RE = re.compile(
    rf"\b(?:(?:when|if)\s+)?it(?:'s|\s+is)\s+{_SUPPLIED_CLOCK_TIME}"
    rf"(?:\s+on\s+(?:a\s+|an\s+)?(?P<weekday>{_SUPPLIED_CLOCK_WEEKDAY_ALT}))?"
    r"\s+in\s+(?P<source>[a-z][a-z .'\-]{0,40}?)"
    r"(?:\s+(?:during|in|on)\s+(?P<regime>standard|daylight)(?:\s+savings?)?\s+time)?"
    r"\s*(?=[,.?!;]|$|\bwhat\b)",
    re.IGNORECASE,
)

_SUPPLIED_CLOCK_ASK_RE = re.compile(
    r"\bwhat\s+time\s+(?:(?:is\s+it|will\s+it\s+be|would\s+it\s+be|is\s+that)\s+)?in\s+"
    r"(?P<target>[a-z][a-z .'\-]{0,40}?)"
    r"(?:\s+(?:during|in|on)\s+(?P<regime>standard|daylight)(?:\s+savings?)?\s+time)?"
    r"\s*(?=[,.?!;]|$|\b(?:when|if)\b)",
    re.IGNORECASE,
)

#: The RECOGNIZER for the stipulated clause, looser than the computable grammar on purpose: it
#: also matches vague supplied times ("tuesday afternoon", "night") so the lane can decline them
#: instead of answering the current clock. It requires a temporal token AND a nearby "in <place>"
#: inside one clause, which is what keeps "if it is late, sorry" and "when it is convenient"
#: from ever engaging this machinery.
_SUPPLIED_CLOCK_HINT_RE = re.compile(
    rf"\b(?:(?:when|if)\s+)?it(?:'s|\s+is)\s+"
    rf"(?=[^,.?!;]{{0,48}}\bin\s+[a-z])"
    rf"[^,.?!;]{{0,48}}?\b(?:\d{{1,2}}(?::\d{{2}})?|noon|midday|midnight|morning|afternoon|"
    rf"evening|night|{_SUPPLIED_CLOCK_WEEKDAY_ALT})\b",
    re.IGNORECASE,
)


def _supplied_clock_clauses_bind(text: str, ask: re.Match[str], hint: re.Match[str]) -> bool:
    """Whether the stipulated clause and the zone-targeted ask are one construction.

    A stipulation BEFORE the ask always binds ("When it is X in A, what time in B?" -- comma or
    period between them notwithstanding). A stipulation AFTER the ask binds only inside the same
    sentence: "What time is it in Tokyo when it is 3 PM in New York?" binds, while "What time is
    it in Tokyo? If it is 9 AM in London, my call started." is two thoughts -- converting there
    would answer a question that was not asked, and hard-declining would break the plain current
    time ask the turn does contain.
    """

    if hint.start() < ask.start():
        return True
    return not re.search(r"[.?!;]", text[ask.end() : hint.start()])


def supplied_clock_frame_binding(cleaned_input: str) -> bool:
    """Whether this turn IS the supplied-absolute-time conversion construction."""

    text = " ".join(str(cleaned_input or "").lower().split())
    if not text:
        return False
    ask = _SUPPLIED_CLOCK_ASK_RE.search(text)
    hint = _SUPPLIED_CLOCK_HINT_RE.search(text)
    if ask is None or hint is None:
        return False
    if max(ask.start(), hint.start()) < min(ask.end(), hint.end()):
        return False
    return _supplied_clock_clauses_bind(text, ask, hint)


def _resolve_supplied_clock_place(raw: str) -> tuple[str, str] | None:
    """A captured place -> (IANA zone or UTC alias, display label), or None."""

    words = [word for word in str(raw or "").lower().replace(".", " ").split() if word]
    if words and words[0] == "the":
        words = words[1:]
    if not words:
        return None
    leaves = _canonical_zone_leaves()
    # Trailing words the capture may have swallowed ("new york city") are dropped from the right,
    # never invented: only exact canonical leaves (or the closed UTC aliases) resolve.
    for cut in range(len(words), max(len(words) - 2, 0), -1):
        key = " ".join(words[:cut])
        alias = _UTILITY_OFFSET_ALIASES.get(key)
        if alias is not None:
            return alias
        zone = leaves.get(key)
        if zone:
            return zone, " ".join(word.capitalize() for word in key.split())
    return None


def _supplied_clock_regime_anchor(
    zone: ZoneInfo, *, want_daylight: bool, year: int
) -> datetime | None:
    """A mid-season date where `zone` observes the requested regime, or None if it never does.

    Probing four mid-quarter dates covers both hemispheres: January is standard time in New York
    and daylight time in Sydney. Tokyo observes no daylight time, so asking for its daylight
    clock is not computable and declines.
    """

    for month in (1, 4, 7, 10):
        probe = datetime(year, month, 15, 12, tzinfo=zone)
        observes_daylight = bool(probe.dst())
        if observes_daylight == want_daylight:
            return probe
    return None


def _render_supplied_clock(moment: datetime) -> str:
    hour12 = moment.hour % 12 or 12
    return f"{hour12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def supplied_absolute_time_conversion(
    cleaned_input: str, *, now_utc: datetime | None = None
) -> tuple[str, ...] | None:
    """Convert a user-supplied absolute wall time between two named zones, exactly.

    Returns candidate answer shapes (full sentence first, terser clock forms after, for the
    stated-output-contract fallback) -- or None when the construction is absent or not exactly
    computable. ``now_utc`` is the same test seam `date_time_fast_path` carries: it fixes the
    reference year/date so tests are deterministic; it never becomes the answer.
    """

    text = " ".join(str(cleaned_input or "").lower().split())
    if not text or not supplied_clock_frame_binding(text):
        return None
    ask = _SUPPLIED_CLOCK_ASK_RE.search(text)
    if ask is None:
        return None
    stipulation = None
    for candidate in _SUPPLIED_CLOCK_STIPULATION_RE.finditer(text):
        overlaps = max(ask.start(), candidate.start()) < min(ask.end(), candidate.end())
        if not overlaps and _supplied_clock_clauses_bind(text, ask, candidate):
            stipulation = candidate
            break
    if stipulation is None:
        return None

    named = (stipulation.group("named") or "").strip()
    if named:
        hour, minute = (12, 0) if named in {"noon", "midday"} else (0, 0)
    else:
        hour = int(stipulation.group("hour"))
        minute_text = stipulation.group("minute")
        minute = int(minute_text) if minute_text is not None else -1
        meridiem = re.sub(r"[^apm]", "", (stipulation.group("meridiem") or "").lower())
        if meridiem:
            if not 1 <= hour <= 12:
                return None
            if minute < 0:
                minute = 0
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif minute < 0 or not (hour == 0 or 13 <= hour <= 23):
            # "3:00" with no meridiem could be 03:00 or 15:00. Guessing is a model's habit, not
            # this lane's; only an unambiguous 24-hour clock is exact.
            return None
        if not 0 <= minute <= 59:
            return None

    source = _resolve_supplied_clock_place(stipulation.group("source"))
    target = _resolve_supplied_clock_place(ask.group("target"))
    if source is None or target is None:
        return None
    try:
        source_zone = ZoneInfo(source[0])
        target_zone = ZoneInfo(target[0])
    except Exception:
        return None

    weekday_word = (stipulation.group("weekday") or "").lower()
    regime = (stipulation.group("regime") or "").lower()
    ask_regime = (ask.group("regime") or "").lower()
    reference = now_utc if now_utc is not None else datetime.now(timezone.utc)

    # Every path computes through ONE concrete dated instant in the source zone, then renders the
    # SAME instant in the target zone -- real tzdb rules, never hand-added offsets. That is what
    # makes "New York during standard time -> Sydney" land on Sydney's daylight clock: the anchor
    # date implies the season everywhere at once. A regime may be stated on either clause
    # ("... in New York during standard time" or "what time in London during standard time");
    # the anchor season comes from whichever zone the regime was stated about, and BOTH stated
    # regimes are verified against the computed instants below -- an inconsistent pair declines.
    if regime:
        anchor = _supplied_clock_regime_anchor(
            source_zone, want_daylight=regime == "daylight", year=reference.year
        )
    elif ask_regime:
        anchor = _supplied_clock_regime_anchor(
            target_zone, want_daylight=ask_regime == "daylight", year=reference.year
        )
    else:
        anchor = None
    if regime or ask_regime:
        if anchor is None:
            return None
        anchor_date = anchor.astimezone(source_zone).date()
    else:
        anchor_date = reference.astimezone(source_zone).date()
    if weekday_word:
        wanted = _SUPPLIED_CLOCK_WEEKDAYS.index(weekday_word)
        anchor_date += timedelta(days=(wanted - anchor_date.weekday()) % 7)
    source_moment = datetime(
        anchor_date.year, anchor_date.month, anchor_date.day, hour, minute, tzinfo=source_zone
    )
    if regime and bool(source_moment.dst()) != (regime == "daylight"):
        return None
    target_moment = source_moment.astimezone(target_zone)
    if ask_regime and bool(target_moment.dst()) != (ask_regime == "daylight"):
        return None
    day_shift = (target_moment.date() - source_moment.date()).days

    source_clock = _render_supplied_clock(source_moment)
    target_clock = _render_supplied_clock(target_moment)
    source_tz = source_moment.tzname() or ""
    target_tz = target_moment.tzname() or ""
    source_note = f" ({source_tz}, standard time)" if regime == "standard" else (
        f" ({source_tz}, daylight time)" if regime == "daylight" else f" ({source_tz})"
    )
    if weekday_word:
        answer = (
            f"When it is {source_clock} on {source_moment:%A} in {source[1]}{source_note}, "
            f"it is {target_clock} on {target_moment:%A} in {target[1]} ({target_tz})."
        )
    else:
        shift_note = (
            " the next day" if day_shift > 0 else " the previous day" if day_shift < 0 else ""
        )
        answer = (
            f"When it is {source_clock} in {source[1]}{source_note}, "
            f"it is {target_clock}{shift_note} in {target[1]} ({target_tz})."
        )
    return (answer, target_moment.strftime("%H:%M"), target_clock)


#: Ask-construction fragments left behind once _ASKS_TIME_RE consumes its anchor ("what time" is
#: matched; "is it" / "will it be" / "was it" survive as free-floating function words).
_TRAILING_ASK_FRAGMENT_RE = re.compile(
    r"\b(?:is|was)\s+it\b|\bwill\s+it\s+be\b|\bit\s+(?:is|was)\b|\bit\s+will\s+be\b",
    re.IGNORECASE,
)


def _clock_ask_free(text: str) -> str:
    """The sentence with its clock/date ask constructions removed, whitespace collapsed."""

    probe = _ASKS_TIME_RE.sub(" ", str(text or ""))
    probe = _ASKS_DATE_RE.sub(" ", probe)
    probe = _TRAILING_ASK_FRAGMENT_RE.sub(" ", probe)
    return " ".join(probe.split())


#: A whole segment that states an OUTPUT INSTRUCTION rather than content ("Output exactly one
#: word", "No conversational text", "Answer in one word"). Segment-initial verb, not a topic list.
_OUTPUT_INSTRUCTION_SEGMENT_RE = re.compile(
    r"^\s*(?:please\s+|and\s+)?(?:output|answer|reply|respond|return|format|print|no)\b",
    re.IGNORECASE,
)

#: Connective and ask-adjacent words that carry no clause of their own. Same inversion as
#: `_greeting_is_the_whole_message`: the guard does not enumerate topics an offset might belong
#: to -- it asks what REMAINS once the offset, the place, and the ask are accounted for.
_OFFSET_BIND_FILLER = frozenset(
    (
        "a", "an", "and", "the", "or", "so", "then", "oh", "hey", "hi", "hello", "um", "uh",
        "ok", "okay", "please", "thanks", "thank", "you", "kindly", "just", "quick", "quickly",
        "what", "whats", "time", "clock", "is", "it", "was", "will", "would", "be", "there",
        "here", "now", "right", "today", "tonight", "currently", "moment", "at", "in", "on",
        "of", "me", "tell", "give", "us", "my", "i", "we", "do", "know", "have", "got", "later",
    )
)


#: Trailing words that qualify an output-shape instruction without changing it ("one word ONLY").
#: Kept beside the binding filler set they mirror -- both answer "is this word carrying meaning?".
_SHAPE_TAIL_QUALIFIERS = frozenset(
    {"only", "please", "pls", "plz", "thx", "thanks", "max", "maximum", "exactly", "just", "no"}
)


def _segment_states_the_output_shape(segment: str) -> bool:
    """Whether this segment tells the runtime what the ANSWER should look like.

    Two recognizers, deliberately: the segment-initial verb pattern ("output exactly one word",
    "no markdown"), plus the repo's own answer-shape parser, which already understands a bare
    shape stated without a verb ("one word"). Reusing `parse_response_constraint` here rather
    than growing a second phrase list keeps ONE definition of "this is an output instruction" --
    and it is conservative in the right direction: it declines on "the deploy finishes", "time in
    london" and "what time is it", so a genuine clause is never mistaken for a shape request.
    """

    if _OUTPUT_INSTRUCTION_SEGMENT_RE.match(segment):
        return True
    from core.response_constraints import parse_response_constraint

    if parse_response_constraint(segment) is not None:
        return True
    # "one word ONLY" and "one word PLEASE" are the same instruction as "one word" wearing a
    # qualifier the parser does not carry. Rather than teach a second module a second phrase
    # list, drop the trailing politeness/qualifier words this module already enumerates for the
    # binding remainder and ask the SAME parser again.
    trimmed = " ".join(
        word for word in segment.split() if word.strip(".,!") not in _SHAPE_TAIL_QUALIFIERS
    ).strip()
    return bool(trimmed) and trimmed != segment and parse_response_constraint(trimmed) is not None


def _relative_offset_binds_to_the_clock_ask(base: str) -> bool:
    """Whether the removed offset span modified THE CLOCK ASK rather than another clause.

    "the deploy finishes in 5 hours, what time is it" wants NOW: the offset belongs to the
    deploy, and stripping it then shifting the answer would be a new wrong answer. Enumerating
    the clauses an offset might attach to is unfinishable, so this inverts the question exactly
    like `_greeting_is_the_whole_message`: remove the ask construction, the named place, the
    output-instruction segments and connective filler from the offset-stripped sentence -- if
    something substantive remains, that remainder is the clause the offset belonged to.
    """

    # A COMMA delimits an output instruction as surely as a period does: "…90 minutes from now,
    # one word" states its shape after a comma, and splitting only on sentence punctuation left
    # ", one word" glued to the ask, where it read as substantive text and the offset was judged
    # to belong to another clause -- so the shift was silently dropped and the CURRENT time was
    # answered. Measured: "time in london 90 minutes from now, one word" returned
    # "Current time in London is 00:37 BST." while the identical question punctuated
    # ". Output exactly one word." answered correctly.
    segments = re.split(r"[?!;:,]+|\.(?:\s+|$)", str(base or "").lower())
    kept = " ".join(
        segment.strip()
        for segment in segments
        if segment.strip() and not _segment_states_the_output_shape(segment.strip())
    )
    remainder = _clock_ask_free(kept)
    remainder = _LOCATIVE_PLACE_RE.sub(" ", remainder)
    # The place must go whether or not it kept its preposition. `_LOCATIVE_PLACE_RE` only matches
    # the locative form ("in berlin"), so once the ask construction is removed a BARE place is
    # left standing and counted as the competing clause -- measured: "time in berlin in 2h, just
    # the time please" reduced to ["berlin"] and the shift was dropped in favour of the current
    # time. Removing the resolved label is what this step always meant to do.
    _, resolved_label = extract_utility_timezone(base)
    for token in re.split(r"[^a-z0-9']+", str(resolved_label or "").lower()):
        if len(token) > 2:
            remainder = re.sub(rf"\b{re.escape(token)}\b", " ", remainder)
    remainder = re.sub(r"[^a-z0-9\s']+", " ", remainder)
    substantive = [word for word in remainder.split() if word not in _OFFSET_BIND_FILLER]
    return not substantive


#: Where an unpunctuated output instruction begins. Used only to CUT a trailing clause before the
#: place is read -- never to decide anything on its own, and always confirmed by
#: `_segment_states_the_output_shape` before the cut is taken.
_OUTPUT_INSTRUCTION_TAIL_RE = re.compile(
    r"\s+(?=(?:output|answer|reply|respond|return|format|print|give)\b)", re.IGNORECASE
)


def extract_utility_timezones_all(cleaned_input: str) -> list[tuple[str, str]]:
    """EVERY distinct zone the turn names, in first-mention order.

    `extract_utility_timezone` answers "which place does this ask bind to" and
    deliberately returns the first. The multi-city clock answer needs the rest:
    "what time is in rome now and in paris?" names two places, and answering
    only the first dropped Paris silently (measured live 2026-08-29, watch
    session 2026-08-29T1150Z). Same resolution surface — curated aliases,
    then canonical zone leaves over locative slots — deduplicated by zone,
    first mention kept.
    """
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return []
    offset = extract_relative_clock_offset(lowered)
    search_space = offset.stripped if offset is not None else lowered
    leaves = _canonical_zone_leaves()
    pairs: list[tuple[str, str]] = []
    seen_zones: set[str] = set()

    def _scan(span: str) -> None:
        for match in _LOCATIVE_PLACE_RE.finditer(span):
            candidate = " ".join(match.group("place").split())
            if candidate in _UTILITY_OFFSET_ALIASES:
                found = _UTILITY_OFFSET_ALIASES[candidate]
            else:
                zone = leaves.get(candidate)
                found = (zone, candidate.title()) if zone else None
            if found is None:
                continue
            zone, label = found
            if zone in seen_zones:
                continue
            seen_zones.add(zone)
            pairs.append((zone, label))

    # `_LOCATIVE_PLACE_RE` requires the place to be phrase-final, so "in tokyo
    # and in new york" only ever resolved the LAST city (the first sat mid-
    # phrase before " and "). Segmenting on the coordinators makes each named
    # place phrase-final within its own segment; commas are NOT split (they
    # usually bind a city to its country: "in rome, italy").
    for segment in re.split(r"\s+(?:and|&)\s+", search_space):
        _scan(segment)
    return pairs


def extract_utility_timezone(cleaned_input: str) -> tuple[str, str]:
    """The zone a turn NAMES, or ("", "") when it names none.

    Returning ("", "") means "no location named" -- it does NOT mean "use the machine's clock for
    a location the user did name". The caller must distinguish those two, which is why
    `utility_names_unresolved_location` exists.
    """

    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return "", ""
    # An output instruction is not part of the QUESTION, and punctuation was the only thing
    # separating them. Measured live 2026-08-15 (hostile seed 5150): "what time is it in berlin
    # Output exactly one word. asap" -- no "?" after the place -- left the place phrase reading as
    # "berlin output exactly one word", which resolved to no zone, so this lane declined and a
    # model answered 13:12 for a city where it was 03:12. The same sentence WITH a question mark
    # was correct the whole time. The tail is cut only when it actually STATES an output shape, so
    # an ordinary clause -- or a place whose name happens to start with one of these words -- is
    # untouched.
    _tail = _OUTPUT_INSTRUCTION_TAIL_RE.split(lowered, maxsplit=1)
    if len(_tail) == 2 and _segment_states_the_output_shape(_tail[1].strip(" ,.;:!?")):
        lowered = _tail[0].strip()
    # The curated entries stay first: they carry the operator's own label ("Lithuania" -> Vilnius),
    # which the canonical table cannot know.
    for marker, resolved in _UTILITY_TIMEZONE_ALIASES.items():
        if marker in lowered:
            return resolved
    leaves = _canonical_zone_leaves()
    # A relative offset span is noise to PLACE extraction and can hide the place outright: in
    # "what time is it in 45 min in new york" nothing changes, but in "in 5 hours in tokyo what
    # time is it" the offset must go before anything else makes sense. Offset-free turns have
    # search_space == lowered, so their behaviour is unchanged by construction.
    offset = extract_relative_clock_offset(lowered)
    search_space = offset.stripped if offset is not None else lowered
    found = _locative_zone(search_space, leaves)
    if found is not None:
        return found
    if offset is not None:
        # With the offset gone the place can STILL be locked mid-sentence by the ask construction
        # itself: in "in 5 hours in tokyo what time is it" the stripped form is "in tokyo what
        # time is it", where "in tokyo" is not phrase-final. Removing the ask -- never the place --
        # makes the place phrase-final iff it was the last substantive phrase, which keeps
        # "in wake of the outage"-style objects out exactly as before. This probe only runs for
        # offset-bearing turns, so it adds no resolution surface to ordinary ones.
        probe = _clock_ask_free(search_space)
        if probe != search_space:
            found = _locative_zone(probe, leaves)
            if found is not None:
                return found
    return "", ""


def _locative_zone(text: str, leaves: dict[str, str]) -> tuple[str, str] | None:
    for match in _LOCATIVE_PLACE_RE.finditer(text):
        # The WHOLE prepositional object must be the place -- no shorter prefix of it.
        #
        # Trying prefixes was the first attempt and it leaked three of the audit's controls:
        # "in jersey shore reruns" matched `jersey`, "in oral argument" matched `oral`, and
        # "in troll mode" matched `troll`. A prefix match means the sentence went on to say what
        # it was actually about, which is exactly the signal that the word was not a destination.
        candidate = " ".join(match.group("place").split())
        if candidate in _UTILITY_OFFSET_ALIASES:
            return _UTILITY_OFFSET_ALIASES[candidate]
        zone = leaves.get(candidate)
        if zone:
            return zone, candidate.title()
    return None


def utility_names_unresolved_location(cleaned_input: str) -> bool:
    """True when the turn names a place in a locative slot that no zone could be found for.

    The clock must FAIL CLOSED here. Answering the machine's local time for a location the user
    named is a wrong answer delivered with a `tool | date_time_fast_path | tool-generated answer`
    footer -- confident, sourced-looking, and wrong by hours. Declining hands the turn to a lane
    that can say so.
    """

    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered or extract_utility_timezone(lowered) != ("", ""):
        return False
    return bool(_LOCATIVE_PLACE_RE.search(lowered))


def utility_now_for_timezone(timezone_name: str) -> datetime:
    """The clock for a zone. An UNKNOWN zone raises rather than quietly becoming local time.

    The previous form swallowed every failure into `datetime.now().astimezone()`, so a resolver
    bug or a tzdb gap produced machine-local time under the same tool-grounded footer -- a second
    fail-open behind the first. An empty name still means "no zone was named", which is the only
    case where local time is the right answer.
    """

    if timezone_name:
        # NIA-013 (2026-08-30): the one canonical time authority owns the read —
        # same fail-closed contract (unknown zone raises), one clock seam to test.
        return CLOCK.now_for_timezone(timezone_name)
    return CLOCK.now_utc().astimezone()


def recent_utility_context(
    *,
    session_id: str,
    source_context: dict[str, object] | None,
) -> dict[str, str]:
    if session_id:
        state = session_hive_state(session_id)
        if str(state.get("interaction_mode") or "").strip().lower() == "utility":
            payload = dict(state.get("interaction_payload") or {})
            utility_kind = str(payload.get("utility_kind") or "").strip().lower()
            if utility_kind:
                return {
                    "utility_kind": utility_kind,
                    "timezone": str(payload.get("timezone") or "").strip(),
                    "label": str(payload.get("label") or "").strip(),
                }
    history = list((source_context or {}).get("conversation_history") or [])
    for message in reversed(history[-4:]):
        if not isinstance(message, dict):
            continue
        content = " ".join(str(message.get("content") or "").split()).strip().lower()
        if not content:
            continue
        timezone_name, label = extract_utility_timezone(content)
        if "current time" in content or "what time" in content or "time now" in content:
            return {
                "utility_kind": "time",
                "timezone": timezone_name,
                "label": label,
            }
    return {}


def contextual_time_followup_timezone(
    cleaned_input: str,
    *,
    recent_utility_context: dict[str, str] | None,
) -> tuple[str, str]:
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if not lowered:
        return "", ""
    utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
    timezone_name = str((recent_utility_context or {}).get("timezone") or "").strip()
    label = str((recent_utility_context or {}).get("label") or "").strip()
    if utility_kind != "time" or not timezone_name:
        return "", ""
    if any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS):
        return "", ""
    if any(pattern.search(lowered) for pattern in _CONTEXTUAL_TIME_FOLLOWUP_PATTERNS):
        return timezone_name, label
    # PLACE markers only. `now`, `current` and `right now` used to sit in this list and are tense
    # markers, not place markers: after a turn about Tokyo, "what time is it now" -- which plainly
    # means HERE -- inherited Tokyo.
    #
    # That was latent for as long as the only resolvable zone was Europe/Athens, because it equals
    # this machine's zone and the wrong branch printed the same string as the right one. The moment
    # foreign cities resolve it becomes a live wrong-answer generator, so it is removed as part of
    # the same change that made foreign cities resolve.
    if "time" in lowered and any(
        marker in lowered
        for marker in (
            "there",
            "same place",
            "that place",
            "that city",
            "again",
        )
    ):
        return timezone_name, label
    return "", ""


def looks_like_malformed_time_followup(
    cleaned_input: str,
    *,
    effective_timezone: str,
    recent_utility_context: dict[str, str] | None,
) -> bool:
    if not effective_timezone:
        return False
    utility_kind = str((recent_utility_context or {}).get("utility_kind") or "").strip().lower()
    if utility_kind != "time":
        return False
    lowered = " ".join(str(cleaned_input or "").strip().lower().split())
    if "what" not in lowered:
        return False
    if not any(marker in lowered for marker in ("where's", "wheres", "where is")):
        return False
    return not any(marker in lowered for marker in _TIME_FOLLOWUP_EXCLUSION_MARKERS)


def _looks_like_a_slash_command(phrase: str) -> bool:
    """Whether a leading `/` opens a COMMAND rather than an absolute path.

    Measured on c6eed761: this path claimed every message beginning with "/", and on macOS and
    Linux every absolute path does. So

        /Users/<user>/project/core/task_router.py - whats the first thing this file does
        /etc/hosts what is in this file
        /usr/local/bin/python --version, is that right?

    each got "That slash command is not wired here" instead of an answer -- a canned string
    replacing a real request, which is the shape this repo bans outright.

    A command is one leading token: "/new", "/trace", optionally with arguments after a space. A
    path keeps going -- it carries a separator INSIDE that first token. That single structural
    difference separates the two without a list of command names or of path prefixes, so a new
    command needs no change here and neither does a new directory layout.
    """

    head = phrase.split()[0] if phrase.split() else ""
    if not head.startswith("/"):
        return False
    # "/etc/hosts" and "/Users/x/y.py" carry a separator past the opening one; "/new" does not.
    return "/" not in head[1:]


def ui_command_fast_path(normalized_input: str, *, source_surface: str) -> str | None:
    phrase = str(normalized_input or "").strip().lower()
    if not phrase.startswith("/"):
        return None
    if not _looks_like_a_slash_command(phrase):
        # An absolute path, not a command. Leave the turn to the lanes that can actually read it.
        return None
    if phrase in {"/new", "/new-session", "/new_session", "/clear", "/reset"}:
        return "Use the OpenClaw `New session` button on the lower right. Slash `/new` is not a wired command in this runtime."
    if phrase in {"/trace", "/rail", "/task-rail"}:
        return "Open the live trace rail at `http://127.0.0.1:11435/trace`."
    return "That slash command is not wired here. Use plain language, the `New session` button, or open `http://127.0.0.1:11435/trace` for the runtime rail."


def startup_sequence_fast_path(user_input: str) -> str | None:
    normalized = " ".join(str(user_input or "").strip().lower().split())
    if not normalized:
        return None
    if "new session was started" not in normalized:
        return None
    if "session startup sequence" not in normalized:
        return None
    return f"I’m {get_agent_display_name()}. New session is clean and I’m ready. What do you want to do?"


def credit_status_fast_path(agent: Any, normalized_input: str, *, source_surface: str) -> str | None:
    if source_surface not in {"channel", "openclaw", "api"}:
        return None
    phrase = str(normalized_input or "").strip().lower()
    if not phrase:
        return None
    # The markers read what the message ASKS -- its request units and the statements they refer
    # back to (`core.execution_requirements.asked_text`) -- never the material it describes.
    # Measured served 2026-09-16: a design brief for an incident bot whose members "can post
    # reports, check status, transfer credits, and view rankings" was claimed whole by this
    # fast path on the substring "credits", worded by a paid model as the runtime's own credit
    # status, escalated to current-information and refused at publication. "what's my credit
    # balance?" has no described material and reads exactly as before.
    try:
        from core.execution_requirements import asked_text

        phrase = str(asked_text(str(normalized_input or "")) or "").strip().lower() or phrase
    except Exception:
        pass
    credit_markers = (
        "credit",
        "credits",
        "credit balance",
        "compute credits",
        "credit receipt",
        "credit receipts",
        "credit ledger",
        "recent payout",
        "recent payouts",
        "recent credits",
        "my score",
        "credit score",
        "glory score",
        "hive score",
        "social score",
        "provider score",
        "validator score",
        "trust score",
        "tier",
        "wallet balance",
        "dna wallet",
    )
    if not any(marker in phrase for marker in credit_markers):
        return None
    return agent._render_credit_status(phrase)
