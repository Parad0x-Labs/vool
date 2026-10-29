"""Model replies that say an action was done, or a live source was read, when no tool ran.

Measured 2026-10-29 (preview 0.7.7 re-audit, qwen3:8b on CPU, no mail account, no calendar):
the turn offered no tool or the model used none, and the reply still said "No new emails
received today. - Checked email inbox", "your accountant thing is scheduled for the 3rd at 11",
"I'll nudge you in 20 minutes", "Nothing suspicious is running ... based on the current system
scan", "I'll pass your request to your other machine". Nothing was read, saved or sent.

`gate_execution_claims` (final_answer_authorship) already withholds a described TOOL RUN
("Tool: Web Search / query: ..."); these replies name no tool, they simply state the outcome.
This gate reads the same evidence (who wrote the bytes, what the turn's ledgers hold) and adds
the plain-sentence forms:

- a first-person action said as done or about to happen ("I'll add", "let me adjust", "I've set"),
- a passive completion about the user's things ("your ... is scheduled", "reminder set"),
- a report of a read ("Checked ...", "based on the current system scan", "No new emails ...",
  "Next Tuesday afternoon is completely free", "You don't have any videos on here yet"),

each only when the same sentence names one of the live sources below. Advice, questions, offers
and sentences that already say what could not be done ("you could check your inbox", "I can't
see your calendar", "want me to add a reminder?") never count.

When a claim stands and the turn holds no matching tool result, the reply is replaced by a plain
line per source saying VOOL has not done or checked it and what the real step is. A missing
connection is said first ("No email account is connected yet ..."), because that is the actual
reason nothing could be read.
"""
from __future__ import annotations

import re
from typing import Any, Callable

# ---- live sources ------------------------------------------------------------------------------
# (source, words in the sentence that name it, tool intents whose run backs a claim about it)
_SOURCES: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = (
    (
        "mail",
        re.compile(r"\b(?:e-?mails?|inbox(?:es)?|mailbox|unread mail|mail)\b", re.I),
        re.compile(r"mail|inbox", re.I),
    ),
    (
        "calendar",
        re.compile(
            r"\b(?:calendar|agenda|appointments?|meetings?|events?|scheduled|rescheduled|booked)\b",
            re.I,
        ),
        re.compile(r"calendar|agenda|availability|schedul|event", re.I),
    ),
    (
        "reminders",
        re.compile(
            r"\b(?:reminders?|nudge|alarm|timer|remind you (?:in|at|on|to|tomorrow|tonight|later|next))\b", re.I
        ),
        re.compile(r"remind|alarm|timer|schedul", re.I),
    ),
    (
        "notes",
        re.compile(r"\b(?:notes? app|apple notes|your notes|a note|the note|note (?:saved|added|created))\b", re.I),
        re.compile(r"note", re.I),
    ),
    (
        "system",
        re.compile(
            r"\b(?:system scan|scan(?:ned)? (?:of )?(?:your|the|this) (?:system|computer|machine)|processes"
            r"|running in the background|background (?:apps|tasks|processes))\b",
            re.I,
        ),
        re.compile(r"process|service|system|machine\.", re.I),
    ),
    (
        "device",
        re.compile(r"\b(?:other|second|another) (?:machine|computer|device|laptop|mac|pc|vool)\b", re.I),
        re.compile(r"mesh|device|peer|remote|delegat|handoff", re.I),
    ),
    (
        "social",
        re.compile(r"\b(?:twitter|tweets?|retweets?|x\.com|on x)\b", re.I),
        re.compile(r"^x\.|twitter|social|web\.", re.I),
    ),
    (
        "files",
        re.compile(
            r"\b(?:videos?|photos?|pictures?|files?|folders?|documents folder|downloads folder|desktop)"
            r"\b(?:[^.?!\n]{0,30}\b(?:on here|on (?:this|your) (?:computer|mac|pc|machine)|in your))",
            re.I,
        ),
        re.compile(r"file|fs\.|machine\.|workspace\.|media|photo|search", re.I),
    ),
)

# ---- claim forms -------------------------------------------------------------------------------
_ACT_VERBS = (
    r"add|put|set|schedul|book|creat|sav|jot|nudg|remind|ping|mov|adjust|chang|updat|reschedul"
    r"|cancel|delet|remov|pass|send|sent|forward|hand|check|scan|look|search|edit|replac|log|mark"
    r"|block|pencil|note"
)
_FIRST_PERSON_ACTION = re.compile(
    r"\b(?:i['’]ll|i will|i['’]ve|i have|i['’]m going to|i am going to|let me|i just"
    r"|i went ahead and|i['’]ve gone ahead and|i['’]m)\s+"
    r"(?:also\s+|now\s+|go ahead and\s+|quickly\s+|just\s+|make sure to\s+)?(?:" + _ACT_VERBS + r")\w*",
    re.I,
)
_PASSIVE_DONE = re.compile(
    r"\b(?:your|it|that|this|the)\b[^.?!\n]{0,60}?\b(?:is|are|has been|have been|was|were|['’]s|['’]re)\s+"
    r"(?:now\s+|all\s+)?(?:scheduled|rescheduled|booked|set(?: up)?|added|saved|noted|created|moved|updated"
    r"|cancel+ed|deleted|removed|sent|forwarded|passed(?: on| along)?|handed (?:off|over)|logged|blocked)\b",
    re.I,
)
_NOUN_DONE = re.compile(
    r"\b(?:reminder|note|event|appointment|alarm|timer|meeting)\s+(?:has been\s+|is\s+)?"
    r"(?:set|saved|added|created|scheduled|booked|moved|updated)\b",
    re.I,
)
_CHECKED = re.compile(
    r"(?:^|\bi(?:['’]ve| have)?\s+)(?:just\s+)?(?:checked|scanned|looked (?:through|at|in|over)|searched"
    r"|went through|reviewed)\b",
    re.I,
)
_BASED_ON_READ = re.compile(
    r"\bbased on (?:the|a|my|your) (?:current |latest |recent |quick |live )?"
    r"(?:system |inbox |calendar )?(?:scan|check|search|inbox|calendar|feed)\b",
    re.I,
)
_NONE_FOUND = re.compile(
    r"^(?:nope|no|nah)?[\s,.!—-]*(?:no|nothing|none|zero|there (?:are|is) no|there['’]s no"
    r"|you have no|you don['’]t have any|you haven['’]t (?:got|received) any)\b",
    re.I,
)
_REPORT_MARKER = re.compile(
    r"\b(?:found|received|new|unread|in your|on your|on here|running|blowing up|trending|scheduled"
    r"|today|tonight|yet|right now|at the moment|this (?:week|morning|afternoon|evening))\b",
    re.I,
)
_YOU_HAVE_COUNT = re.compile(
    r"\byou have (?:\d+|a few|some|several|one|two|three|four|five|a couple of)\s+(?:new\s+|unread\s+)?"
    r"(?:e-?mails?|messages?|meetings?|events?|appointments?|reminders?|notes?|videos?|photos?|files?)\b",
    re.I,
)
_TIME_WORD = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|tonight|weekend"
    r"|morning|afternoon|evening|day|week|schedule|calendar)"
)
_FREE_SLOT = re.compile(
    r"^(?:next |this |on |your )?" + _TIME_WORD + r"\b[^.?!\n]{0,40}\b(?:is|are|looks?)\s+"
    r"(?:completely |totally |entirely |wide |pretty |mostly |fully )?(?:free|clear|open|booked|busy)\b"
    r"|\byou(?:['’]re| are)\s+(?:completely |totally |entirely )?(?:free|clear|available|busy|booked)\s+"
    r"(?:on|next|this|tomorrow|today|tonight|all|that|then|at)\b",
    re.I,
)
# A sentence that hedges, asks, offers or advises is not a claim.
_NOT_A_CLAIM = re.compile(
    r"\b(?:can['’]?t|cannot|could not|couldn['’]t|unable|not able|don['’]t have (?:access|a way)"
    r"|no access|haven['’]t|have not|didn['’]t|did not|not connected|isn['’]t connected"
    r"|not set up|if you|once you|you can|you could|you might|you may|you should|would you like"
    r"|do you want|want me to|should i|shall i|i can (?:help|add|set|check)|i could|i would|i['’]d)\b",
    re.I,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_LEAD_FILLER = re.compile(r"^[\s>*_#`\-•]*(?:(?:got it|sure(?: thing)?|okay|ok|done|alright|all set|great)\b[\s,.!:;—–-]*)*", re.I)


def _claim_form(sentence: str) -> str:
    body = _LEAD_FILLER.sub("", sentence).strip()
    if not body or body.endswith("?") or _NOT_A_CLAIM.search(body):
        return ""
    if _FIRST_PERSON_ACTION.search(body):
        return "action"
    if _PASSIVE_DONE.search(body) or _NOUN_DONE.search(body):
        return "action"
    if _CHECKED.search(body) or _BASED_ON_READ.search(body) or _YOU_HAVE_COUNT.search(body):
        return "read"
    if _NONE_FOUND.search(body) and _REPORT_MARKER.search(body):
        return "read"
    if _FREE_SLOT.search(body):
        return "read"
    return ""


def _sources_of(sentence: str, form: str) -> list[str]:
    found = [name for name, words, _tools in _SOURCES if words.search(sentence)]
    if not found and form == "read" and _FREE_SLOT.search(_LEAD_FILLER.sub("", sentence).strip()):
        found = ["calendar"]
    return found


def action_claims(content: str) -> list[tuple[str, str, str]]:
    """`(sentence, form, source)` for every sentence of ``content`` that says an action on, or a
    read of, a live source happened. ``form`` is "action" or "read". Code blocks never count."""
    text = _FENCE.sub("\n", str(content or ""))
    claims: list[tuple[str, str, str]] = []
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = raw.strip()
        if not sentence:
            continue
        form = _claim_form(sentence)
        if not form:
            continue
        for source in _sources_of(sentence, form):
            claims.append((sentence, form, source))
    return claims


# ---- what is connected -------------------------------------------------------------------------


def _mail_connected() -> bool | None:
    try:
        from core.email_accounts import configured_accounts

        return bool(configured_accounts())
    except Exception:
        return None


def _calendar_connected() -> bool | None:
    try:
        from core.operator.calendar_provider import load_read_provider_config

        config = load_read_provider_config()
        return bool(config is not None and config.configured)
    except Exception:
        return None


def _x_connected() -> bool | None:
    try:
        from core.x_api import connected

        return bool(connected())
    except Exception:
        return None


def _line_for(source: str, connected: Callable[[str], bool | None]) -> str:
    if source == "mail":
        if connected("mail") is False:
            return (
                "No email account is connected yet, so I haven't checked any inbox and can't say "
                "what's in it. You can add one under Settings > Email accounts."
            )
        return "I haven't checked your email, so I can't say what's in your inbox. Ask me to check your email and I'll read it."
    if source == "calendar":
        if connected("calendar") is False:
            return (
                "No calendar is configured or chosen in Settings yet, so I haven't read or changed "
                "your schedule. You can connect one under Settings > Calendars."
            )
        return (
            "I haven't read or changed your calendar, so nothing was added or moved. Ask again with "
            "the day and time and I'll use the calendar."
        )
    if source == "reminders":
        return "No reminder was set. Ask me to remind you, with the time, and I'll set one."
    if source == "notes":
        return "No note was saved. Ask me to add it as a note and I'll save it."
    if source == "system":
        return (
            "I haven't scanned this computer or looked at what's running, so I can't say whether "
            "anything is wrong. Ask me to list the running processes and I'll check."
        )
    if source == "device":
        return "Nothing was sent to another machine; I haven't contacted any other device."
    if source == "social":
        if connected("x") is False:
            return (
                "I haven't looked at X (Twitter), and no X account is connected, so I can't say "
                "what's happening there. Ask me to search the web for it and I'll look."
            )
        return "I haven't looked at X (Twitter), so I can't say what's happening there. Ask me to look it up and I'll check."
    return "I haven't looked through your files, so I can't say what's there. Ask me to search your files and I'll look."


def _connected_reader() -> Callable[[str], bool | None]:
    cache: dict[str, bool | None] = {}
    readers = {"mail": _mail_connected, "calendar": _calendar_connected, "x": _x_connected}

    def read(name: str) -> bool | None:
        if name not in cache:
            cache[name] = readers[name]()
        return cache[name]

    return read


def no_action_reply(sources: list[str], *, connected: Callable[[str], bool | None] | None = None) -> str:
    """The plain reply that replaces a claimed action or read, one line per source."""
    read = connected or _connected_reader()
    lines = list(dict.fromkeys(_line_for(source, read) for source in dict.fromkeys(sources)))
    return "\n\n".join(lines)


# ---- evidence ----------------------------------------------------------------------------------


def _turn_tools(source_context: Any) -> list[str]:
    try:
        from core.turn_model_call_ledger import turn_call_accounting

        return [str(t) for t in (turn_call_accounting(source_context).get("tools") or [])]
    except Exception:
        return []


def _other_execution_evidence(turn_id: str) -> bool:
    """Retrieval, typed observations or effect receipts this turn: execution the tool-intent list
    may not name. Any of them stands the check down, as in `gate_execution_claims`."""
    try:
        from core.grounding_lifecycle import lifecycle_for_publication

        lifecycle = lifecycle_for_publication(turn_id=turn_id)
        if lifecycle is not None and (
            lifecycle.retrieved_source_count or lifecycle.typed_observations or lifecycle.bound_notes
        ):
            return True
    except Exception:
        pass
    try:
        from core.effect_gateway import effect_receipts

        if effect_receipts():
            return True
    except Exception:
        pass
    return False


def gate_action_claims(
    content: str,
    *,
    turn_id: str = "",
    source_context: Any = None,
    request_text: str = "",
    connected: Callable[[str], bool | None] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Replace a MODEL-AUTHORED reply that says it did or read something this turn did not.

    Stands down for runtime-composed bytes, a request that set an illustrative frame, other
    execution evidence (retrieval, observations, effects), and per source when a tool of that
    source's family ran this turn. Returns the bytes to publish and a record of the decision.
    """
    from core.final_answer_authorship import model_authored_turn

    text = str(content or "")
    authored, recorded_request = model_authored_turn(turn_id=turn_id, source_context=source_context)
    if not authored:
        return text, {"publication": "stood_down:not_model_authored"}
    claims = action_claims(text)
    if not claims:
        return text, {"publication": "stood_down:no_action_claims"}
    request = str(request_text or "") or recorded_request
    try:
        from core.stipulated_frame import has_stipulated_frame

        if request and has_stipulated_frame(request):
            return text, {"action_claims": sorted({c[2] for c in claims}), "publication": "illustrative_frame"}
    except Exception:
        pass
    if _other_execution_evidence(turn_id):
        return text, {"action_claims": sorted({c[2] for c in claims}), "publication": "execution_evidence_present"}
    tools = _turn_tools(source_context)
    unbacked: list[str] = []
    for _sentence, _form, source in claims:
        family = next(tool_re for name, _words, tool_re in _SOURCES if name == source)
        if any(family.search(tool) for tool in tools):
            continue
        if source not in unbacked:
            unbacked.append(source)
    if not unbacked:
        return text, {"action_claims": sorted({c[2] for c in claims}), "publication": "tool_result_present"}
    return no_action_reply(unbacked, connected=connected), {
        "action_claims": unbacked,
        "claimed_sentences": [c[0] for c in claims if c[2] in unbacked][:6],
        "publication": "action_claims_replaced",
    }


__all__ = ["action_claims", "gate_action_claims", "no_action_reply"]
