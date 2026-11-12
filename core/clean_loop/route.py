"""Should this turn take the clean local path? One pure decision, in a fixed order.

1. The flag `VOOL_CLEAN_LOCAL_LOOP` is on (default ON; 0/false/no/off turns it off), and the turn is not one
   of the memory lane's commands (a forget, a "remember that", the memory list), which are never the loop's,
   not even as a short follow-up (`core.persistent_memory.memory_command_owns_turn`).
2. The turn is not RED: no pay/send/delete/post/cancel-style verb and no money words. VOOL has no
   text-level risky-action classifier to reuse (its consent gates sit at the effect, which this loop
   never reaches), so this is a small verb floor. RED turns keep the normal ceremony. Nor is it a
   write (create, move, rename, save...), unless the changes flag is on (then the change is shown as
   a card first). A question ("when do i have to pay the plumber by", also after a joining word: "and
   how much am i paying for it") asks for facts, so its verb counts as neither, but only when every clause is a question.
   Money words are RED in any form.
3. Nothing else owns the turn: not a planned sub-turn, a resumed checkpoint, a browser workflow, a
   project-bound chat, a private chat (its words never reach this loop's brain) or a chat with an
   active goal workflow, and it is the owner's own local turn.
4. It is about the owner's own files or computer (or a short follow-up to a clean-loop turn, or it
   shares a distinctive word with the name of one of the owner's files, or, checked last, it is a
   question about something the owner's documents hold: `tools.document_matches`), and not a
   live/online question, which the online lanes own. No skill keeps it: the person names no skill
   VOOL has ("use the project archaeology skill") and the ask picks no skill the person added (made
   by them, or added from Browse, the Skill Codex, a file or an MCP server). Those turns keep the
   normal lane, the only one that reads skills; VOOL's built-in skills picked by words alone do not.
5. A brain answers: a ready local model, or with `VOOL_CLEAN_LOOP_CLOUD` on (default ON) the owner's own cloud model
   (resolved last: it is the only costly check).
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

FLAG_ENV = "VOOL_CLEAN_LOCAL_LOOP"
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

_RED_RE = re.compile(
    r"\b(?:pay|paying|send|sending|transfer|wire|remit|settle|top\s*up|withdraw|cash\s+(?:out|him|her|them)|lend|reimburse|refund|charge|deposit|"
    r"unlock|lock|freeze|block|close\s+(?:it|the|my|this|that)|buy|purchase|sell|trade|swap|donate|tip\s+(?:him|her|them|the)|"
    r"order\s+(?:me|a|an|some)|book\s+(?:me|a|an)|reserve|subscribe|unsubscribe|cancel|"
    r"delete|deleting|remove|removing|erase|wipe|trash|shred|purge|clear\s+out|clean\s+out|clean\s+up|"
    r"empty\s+(?:the\s+)?(?:trash|bin)|get\s+rid|uninstall|install|kill|shut\s*down|reboot|restart\s+(?:it|my|the)|"
    r"format\s+(?:the|my|this|that)\s+(?:drive|disk|usb|computer|laptop|mac|pc)|"
    r"post\s+(?:it|this|that|to|on|a|an|my)|publish|tweet|share|upload|email\s+(?:it|this|that|him|her|them|to)|"
    r"message\s+(?:him|her|them)|sign\s+(?:it|this|that|the|up|in|me)|submit|approve|authori[sz]e)\b",
    # (An amount alone is not RED: "find the €129 invoice" asks about a file. The verbs above still
    # catch "send €50 to anna"; Fable consult 2026-10-28.)
    re.I,
)
# Destructive verbs stay RED in a question too: "is it ok to delete everything in Downloads" is an
# ask to delete in polite form (review 2026-10-28, N4). Only the everyday verbs ("when do i pay the
# plumber", "when did we move the audit") are exempt in a question.
_DESTRUCTIVE_RE = re.compile(
    r"\b(?:delete|deleting|remove|removing|erase|wipe|trash|shred|purge|clear\s+out|clean\s+out|"
    r"empty\s+(?:the\s+)?(?:trash|bin)|get\s+rid|uninstall|kill|shut\s*down|reboot|format)\b",
    re.I,
)
_MONEY_RE = re.compile(
    r"\b(?:wallet|crypto|bitcoin|btc|eth|solana|usdc|(?:credit|debit|visa)\s*-?\s*card|bank\s*-?\s*account|iban|cvv|cvc|"
    r"card\s+number|security\s+code|sort\s+code|routing\s+number|pin\s+(?:number|code)|card\s+pin|passwords?|mastercard|amex|paypal|venmo|zelle)\b", re.I
)
# A question asks for facts: "when do i have to pay the plumber by", "when did we move the audit to".
# The verb inside it is the subject of the question, not an order (Mac hold-out 2026-10-28: both were
# sent to the old pipeline and answered wrong). The loop only reads; a change still needs its card,
# the owner's yes and VOOL's own approval. Money words stay RED in any form.
_QUESTION_RE = re.compile(
    r"^\s*(?:when|what(?!\s+if\b)|what'?s|whats|which|where|who|whose|why|how\s+(?:much|many|long|big|old|often|far)|"
    r"did|was|were|am|is|are|have|has)\b",
    re.I,
)
_WRITE_RE = re.compile(
    r"\b(?:move|moving|rename|renaming|copy|copying|duplicate|create|creating|write|save|saving|put|stick|"
    r"make\s+(?:me\s+)?(?:a|an|new)|set\s+up|organi[sz]e|tidy|sort\s+out|edit|append|add\s+(?:a|an|it|this|that|to))\b",
    re.I,
)
# "where did i put my cv", "what did i write", "it didnt save": the owner recalling, not asking for a change.
_RECALL_RE = re.compile(
    r"\b(?:did\s+(?:i|you)|i|i'?ve|i\s+had|didn'?t|did\s+not|wasn'?t)\s+(?:just\s+|already\s+)?"
    r"(?:move|rename|copy|create|write|save|put|stick|make|edit)\w*",
    re.I,
)
_ONLINE_RE = re.compile(
    r"\b(?:weather|forecast|news|headlines|stock|share\s+price|exchange\s+rate|price\s+of|search\s+(?:the\s+)?(?:web|internet|online)|"
    r"google|online|website|https?://|www\.)\b",
    re.I,
)
_TOPIC_RE = re.compile(
    r"\b(?:files?|folders?|pdfs?|csv|docs?|documents?|downloads?|desktop|photos?|pictures?|pics?|images?|screenshots?|"
    r"videos?|movies?|music|songs?|to\s*do|list|invoices?|receipts?|bills?|owe|cv|resume|tax|bank|statement|boarding\s+pass|"
    r"itinerar(?:y|ies)|meetings?|reports?|insurance|lease|rent|"
    r"contracts?|payslips?|warrant(?:y|ies)|appointments?|recipes?|disk|drive|storage|space|ram|memory|cpu|processor|specs?|"
    r"screen|monitors?|resolution|process(?:es)?|running|background|services?|logs?|errors?|slow|sluggish|junk|temp|"
    r"privacy|private|left\s+(?:this|my)\s+(?:computer|mac|laptop|machine)|sent\s+to\s+the\s+cloud)\b",
    re.I,
)
# "minutes" is not a topic: "meetings?" already covers meeting minutes, and a bare "minutes" admitted
# "15 minutes a day" (skills thread, 2026-11-12). Notes are not a topic either, see is_notes_ask.
# The person's notes ("check my notes", "find my note about the plumber") belong to the notes tools,
# which this loop does not have: it answered from files and the claim gate then said "I haven't
# looked through your notes". A notes FILE ("my notes.md", "the notes on my desktop") stays a file.
_NOTES_RE = re.compile(
    r"\b(?:my|our)\s+notes?\b|\bnotes?\s+app\b|\bapple\s+notes\b|"
    r"\b(?:a|any|the)\s+notes?\s+(?:about|on|for|that|i|where|with|mentioning)\b|"
    r"\b(?:in|through|search|check|find|look\s+in)\s+(?:my\s+|the\s+)?notes\b(?!\s*\.)",
    re.I,
)
_NOTES_FILE_RE = re.compile(
    r"\bnotes?\s*\.\w|\bnotes?\s+(?:file|folder|doc|document|pdf|txt)s?\b|"
    r"\b(?:files?|folders?|desktop|downloads|documents\s+folder|pdfs?)\b|\.(?:md|txt|pdf|docx?|rtf)\b",
    re.I,
)
# Tickets, flights, bookings and reservations count only when they are the owner's: "my flight",
# "the tickets i bought", "what seat am i in on the flight". Without that they are prices and
# timetables for the web ("how much is a ticket for the louvre these days", "cheapest flight from
# krakow to rome"), which this loop answered from the owner's files (red3 wordings, 2026-11-12).
_OWNED_TRAVEL_RE = re.compile(
    r"\b(?:my|our)\s+(?:[\w'-]+\s+){0,2}(?:tickets?|flights?|bookings?|reservations?|boarding)\b|"
    r"\b(?:tickets?|flights?|bookings?|reservations?)\s+(?:i|we|i've|we've)\b|"
    r"\b(?:am|do|did|have|are|was|were)\s+(?:i|we)\b[^.?!]{0,40}\b(?:tickets?|flights?|bookings?|reservations?)\b",
    re.I,
)
# The machine itself counts only when it is the owner's: "my laptop", "this mac", not "mac or windows".
_MACHINE_RE = re.compile(r"\b(?:my|this|the)\s+(?:own\s+)?(?:laptop|computer|mac(?:book)?|pc|machine)\b", re.I)
# Ownership counts only with a file action of the owner's own ("did i move the audit", "i saved the
# invoice"). A bare possessive ("my cat is called Biscuit", default-on delivery 2026-11-01) is
# ordinary chat, not an ask about the owner's files or computer: claiming it kept the turn out of
# the old pipeline, which then never wrote its routing provenance (turn_routing_events.jsonl) —
# the no-trace contract's ordinary-chat control broke. The same holds for "my speed" style general
# asks (the document-admission hold-out's reported false admission).
_OWNED_RE = re.compile(
    r"\b(?:did\s+(?:i|we)|i\s+(?:wrote|saved|downloaded|took|put|keep|named|made|have|had|got|need|owe))\b",
    re.I,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_ASK_STOP = frozenset(
    {
        "the", "and", "for", "with", "what", "whats", "when", "where", "which", "who", "whose", "why", "how", "much",
        "many", "long", "does", "did", "was", "were", "are", "have", "has", "had", "this", "that", "these", "those",
        "there", "here", "from", "into", "about", "your", "you", "can", "could", "would", "should", "will", "get",
        "goes", "going", "need", "tell", "show", "find", "file", "files", "folder", "new", "old", "last", "first",
        "next", "some", "any", "all", "one", "two", "best", "better", "good", "make", "made", "know", "like",
        "want", "please", "thing", "things", "stuff", "today", "week", "time", "copy", "final", "untitled", "img",
        "image", "screenshot", "document", "notes", "note", "draft",
    }
)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def ask_words(text: str) -> set[str]:
    """The distinctive words of an ask (four letters or more, or a word with a digit like "q3")."""
    words = set()
    for word in _WORD_RE.findall(str(text or "").lower()):
        if word in _ASK_STOP or word.isdigit():
            continue
        if len(word) >= 4 or (len(word) >= 2 and any(ch.isdigit() for ch in word)):
            words.add(_stem(word))
    return words


def names_owner_file(text: str, file_words: Callable[[], set[str]] | None) -> bool:
    """True when the ask shares a distinctive word with the name of one of the owner's own files
    ("lisbon" and "Lisbon boarding pass.pdf", "balcony" no, "flat" and "flat viewing notes.md")."""
    if file_words is None:
        return False
    words = ask_words(text)
    if not words:
        return False
    try:
        names = file_words()
    except Exception:
        return False
    return bool(words & names)
_SELF_RE = re.compile(r"\b(?:my\s+name|call\s+me|who\s+am\s+i|about\s+me)\b|^\s*(?:please\s+)?(?:remember|forget)\b", re.I)
_APPROVAL_RE = re.compile(r"^\s*(?:yes|yep|yeah|ok|okay|sure|go\s+ahead|do\s+it|approve[d]?|confirm(?:ed)?|no|nope|stop)\b", re.I)

# A reply to a change card is a whole short answer, nothing more: "ok, but not the pdfs", "yes?" or
# "yes and also..." are new asks, never an approval (independent review 2026-10-28).
_YES_WORDS = frozenset({
    "yes", "yes please", "yep", "yeah", "yup", "ok", "okay", "sure", "go ahead", "do it", "please do",
    "ok do it", "okay do it", "yes do it", "yes go ahead", "ok go ahead", "sounds good",
})
_NO_WORDS = frozenset({
    "no", "nope", "no thanks", "no thank you", "stop", "don't", "dont", "leave it", "never mind",
    "nevermind", "forget it", "no leave it",
})
_UNDO_RE = re.compile(
    r"(?:please )?(?:undo(?: (?:that|it|this|everything|all of it))?|put (?:them|it|everything|all of it) back|"
    r"revert(?: (?:that|it))?|take (?:that|it) back|change it back)(?: please)?",
)


def _bare(text: str) -> str:
    """Lower case, commas dropped, spaces collapsed, trailing full stops and "!" off. A "?" stays,
    so a question never reads as an answer."""
    return " ".join(str(text or "").lower().replace(",", " ").split()).rstrip(".! ")


_FOLLOW_UP_WORDS = 14
_FOLLOW_UP_TTL_S = 900.0
_RECENT: OrderedDict[str, float] = OrderedDict()
_RECENT_LOCK = threading.Lock()


@dataclass(frozen=True)
class RouteDecision:
    admit: bool
    reason: str
    brain: Any = None
    #: Admitted only because the owner's documents hold its words (no topic, ownership or file-name
    #: word). Such a turn may be one VOOL's memory answers; finding nothing, it falls through.
    by_documents: bool = False


CHANGES_ENV = "VOOL_CLEAN_LOOP_CHANGES"


def changes_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Write asks (new folder, move, rename, new file) come here only with their own flag: the change
    is rehearsed, shown as a card and undoable (core.clean_loop.changes). Default OFF."""
    return str((env if env is not None else os.environ).get(CHANGES_ENV) or "").strip().lower() in _TRUTHY


def flag_on(env: Mapping[str, str] | None = None) -> bool:
    # Default ON (0.8): unset or empty runs the clean loop; 0/false/no/off turns it off.
    return str((env if env is not None else os.environ).get(FLAG_ENV) or "").strip().lower() not in _FALSY


# A follow-up often opens with a joining word: "and how much am i paying for it a year", "so when is it
# due", "ok what about the other one" (R4-CL review 2026-10-31, R38). The joining word is not the ask.
_LEAD_IN_RE = re.compile(r"^\s*(?:(?:and|so|ok|okay|but|also|oh)\b[\s,.!-]*)+", re.I)
# A question excuses its verb only when the whole turn is questions: "when is the rent due, then wire it"
# and "what is my balance? pay it" carry an order after the question (money review 2026-10-31).
_CLAUSE_SPLIT_RE = re.compile(r"[?.!;,:&+/(\n\u2026]+|\s-\s|\s+(?:and\s+)?then\b|\s+(?:and|so|'n|n')\s+", re.I)
# "is it ok to transfer my savings" asks permission for an order: polite, still RED.
_PERMISSION_RE = re.compile(
    r"\b(?:(?:is\s+it|would\s+it\s+be|it'?s)\s+(?:ok|okay|fine|alright|all\s+right)\s+(?:to|if)|"
    r"(?:are|were)\s+you\s+able\s+to|(?:can|could|would|will)\s+you)\b",
    re.I,
)
# In a question an order verb is the question's subject only right after its own subject: "when do i
# have to pay the plumber", "how much am i paying". "what about paying anna" and "what is due next pay
# it" are orders (money review 2026-10-31).
_SUBJECT_BEFORE_RE = re.compile(r"\b(?:i|we|you|he|she|they|it)(?:\s+[\w']+){0,3}\s+$", re.I)


# These are also everyday nouns ("how much was the deposit", "when is the refund due", "which block is my
# flat in"): in a question they are an order only where an order starts (money review 2026-10-31).
_NOUN_TOO = frozenset({"refund", "charge", "deposit", "block", "lock", "transfer"})
_ORDER_START_RE = re.compile(r"(?:^|[?.!;,:\n]|\b(?:you|please|to|and|then|just|now|so))\s*$", re.I)


def _verbs_are_the_questions_own(value: str, pattern: re.Pattern[str]) -> bool:
    for match in pattern.finditer(value):
        before = value[: match.start()]
        if match.group(0).lower() in _NOUN_TOO:
            if _ORDER_START_RE.search(before):
                return False
            continue
        if not _SUBJECT_BEFORE_RE.search(before):
            return False
    return True


def is_question(text: str) -> bool:
    value = _LEAD_IN_RE.sub("", str(text or ""), count=1)
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(value) if c and c.strip(" ,-")]
    return bool(clauses) and all(_QUESTION_RE.search(_LEAD_IN_RE.sub("", c, count=1)) for c in clauses)


# Sending something out of this computer is RED even asked as a question ("can you upload my cv to
# dropbox?", "is it ok to share these with my landlord"): the owner's things leave. Recalling it ("what
# did i send to anna") is a question about the past and stays a question.
_OUTWARD_RE = re.compile(r"\b(?:upload|send|share|post|publish|tweet|email|forward)\b", re.I)
_OUTWARD_RECALL_RE = re.compile(
    r"\b(?:did|have|had)\s+(?:i|we|you)\s+(?:ever\s+|already\s+|just\s+|last\s+)?"
    r"(?:upload|send|share|post|publish|tweet|email|forward)\b",
    re.I,
)


def is_red(text: str) -> bool:
    value = str(text or "")
    if _MONEY_RE.search(value) or _DESTRUCTIVE_RE.search(value):
        return True
    if _OUTWARD_RE.search(_OUTWARD_RECALL_RE.sub(" ", value)):
        return True
    if not _RED_RE.search(value):
        return False
    return bool(_PERMISSION_RE.search(value)) or not is_question(value) or not _verbs_are_the_questions_own(value, _RED_RE)


def is_write(text: str) -> bool:
    value = str(text or "")
    if not _WRITE_RE.search(_RECALL_RE.sub(" ", value)):
        return False
    return bool(_PERMISSION_RE.search(value)) or not is_question(value)


# An order to run something ("Run the tests and show me the errors", "run pytest and show me the logs",
# "Run the build in the background") belongs to the command lane: this loop only reads, so it would
# answer about errors and logs while the command never ran (wire-core review 2026-11-12, V-048). The
# machine-health words in such an order ("errors", "logs", "background") must not pull it in. "run" is
# an order only where an order starts; "the last run", "my passport runs out" and "which apps run at
# startup" are not orders.
_RUN_RE = re.compile(
    r"\b(?:re-?run|run|execute|launch|compile)\b(?!\s+(?:out|low|short|late|hot|slow|into|over|across)\b)",
    re.I,
)


def is_run_order(text: str) -> bool:
    value = str(text or "")
    for match in _RUN_RE.finditer(value):
        before = _LEAD_IN_RE.sub("", value[: match.start()], count=1)
        if _ORDER_START_RE.search(before) or _PERMISSION_RE.search(before):
            return True
    return False


def change_reply(text: str) -> str:
    """"yes", "no" or "undo" for a bare reply to a change card; "" for anything else."""
    value = _bare(text)
    if _UNDO_RE.fullmatch(value):
        return "undo"
    if value in _NO_WORDS:
        return "no"
    if value in _YES_WORDS:
        return "yes"
    return ""


# "how much of my stuff went to online AI today" asks about this computer, not the web: the privacy
# meter answers it locally (Mac demo 2026-10-28: it was sent to the web lanes and refused).
_PRIVACY_ASK_RE = re.compile(
    r"\b(?:privacy|private|online\s+(?:ai|models?)|cloud\s+(?:ai|models?)|"
    r"(?:went|go|gone|sent|send|left|leave|leaves|shared?)\s+(?:to\s+)?(?:the\s+)?(?:online|cloud|internet|openai|anthropic|chatgpt|claude))\b",
    re.I,
)


# "search online for the privacy policy of my bank" asks for the web, not about what left this computer.
_NOT_PRIVACY_RE = re.compile(
    r"\b(?:search|look\s+up|google|browse|polic(?:y|ies)|website|news|headlines?|latest|fined?|lawsuit|"
    r"https?://|www\.)\b", re.I
)


def is_privacy_ask(text: str) -> bool:
    value = str(text or "")
    return bool(_PRIVACY_ASK_RE.search(value)) and not _NOT_PRIVACY_RE.search(value)


def is_online_ask(text: str) -> bool:
    value = str(text or "")
    if is_privacy_ask(value):
        return False
    if _ONLINE_RE.search(value):
        return True
    # A web address with no scheme ("go to example.net and give me the gist") is a page to read,
    # which the web lane owns: with the loop on it was answered by the loop's model instead, and
    # with Web lookups off that skipped the plain "Web lookups are off" answer.
    try:
        from core.agent_runtime.fast_paths_web import url_read_request

        return url_read_request(value) is not None
    except Exception:
        return False


def is_notes_ask(text: str) -> bool:
    """An ask about the person's notes, for the notes tools; a notes file stays a file ask."""
    value = str(text or "")
    return bool(_NOTES_RE.search(value)) and not _NOTES_FILE_RE.search(value)


def asks_about_own_files_or_computer(text: str) -> bool:
    value = str(text or "")
    if _SELF_RE.search(value):
        return False
    return bool(
        _TOPIC_RE.search(value) or _OWNED_TRAVEL_RE.search(value) or _MACHINE_RE.search(value) or _OWNED_RE.search(value)
    )


# A planning or drafting ask that a typed content skill claims belongs to that skill's lane: this
# loop's brain answers from the owner's files and carries no skill guidance. Measured on the real
# chat page 2026-11-12: "Plan a launch campaign for our privacy tool across X and LinkedIn" was
# taken as a privacy-meter ask, and the next short ask ("content calendar for bluesky n mastodon")
# as its follow-up, so the social content manager never reached the model. An ask that names the
# owner's files ("the content calendar file in my Documents") still comes here.
_SKILL_OWNED_FAMILIES = frozenset({"social_content", "editorial"})
# Only an explicit file or folder reference keeps such an ask here. The broad topic words this
# loop admits on ("privacy", "list", "reports", "notes" alone) are a product's subject in a content
# ask ("a campaign for our privacy tool"), not a request to read the owner's files.
_NAMES_FILES_RE = re.compile(
    r"\b(?:my|our|the|this|that|these|those)\s+(?:[\w-]+\s+){0,2}"
    r"(?:files?|folders?|pdfs?|docs?|documents?|downloads|desktop|spreadsheets?|csv|notes?\s+(?:file|folder|app))\b"
    r"|\b[\w-]+\.(?:pdf|docx?|txt|md|csv|xlsx?|pages|numbers|key)\b",
    re.I,
)


def skill_owns_turn(text: str) -> bool:
    value = str(text or "")
    try:
        from core.tool_demand_signals import resolve_demand_signals

        families = set(resolve_demand_signals(value).required_families)
    except Exception:
        return False
    if not families & _SKILL_OWNED_FAMILIES:
        return False
    return not (_NAMES_FILES_RE.search(value) or _MACHINE_RE.search(value))


def note_clean_turn(session_id: str) -> None:
    """Remember that this session's last turn ran here, so a short follow-up stays on the same path."""
    if not session_id:
        return
    with _RECENT_LOCK:
        _RECENT[session_id] = time.monotonic()
        _RECENT.move_to_end(session_id)
        while len(_RECENT) > 256:
            _RECENT.popitem(last=False)


def _is_follow_up(text: str, session_id: str) -> bool:
    value = str(text or "")
    if not session_id or len(value.split()) > _FOLLOW_UP_WORDS or _APPROVAL_RE.search(value):
        return False
    with _RECENT_LOCK:
        seen = _RECENT.get(session_id)
    return seen is not None and time.monotonic() - seen < _FOLLOW_UP_TTL_S


def _carries_attachments(source_context: Mapping[str, Any]) -> bool:
    """A turn that carries files (the chat's attachments, a channel's media) belongs to the lanes
    that read them: this loop's brain sees only the words and its own tools, so "what token is in
    the attached notes?" was answered from the owner's disk with the file itself never read. When
    the evidence cannot be counted, a turn that names any is treated as carrying it."""
    if str(source_context.get("attachment_turn_id") or "").strip():
        return True
    evidence = source_context.get("external_evidence")
    try:
        from core.agent_runtime.request_authority import bounded_evidence

        return bool(bounded_evidence(evidence).recognized)
    except Exception:
        return bool(evidence)


def _owner_context_blocks(source_context: Mapping[str, Any], session_id: str) -> str:
    if source_context.get("planned_subturn"):
        return "planned_subturn"
    if source_context.get("turn_routing_retry") or source_context.get("runtime_checkpoint_resumed"):
        return "resumed_turn"
    if str(source_context.get("_trusted_project_id") or "").strip():
        return "project_owns_turn"
    if _carries_attachments(source_context):
        return "attachment_turn"
    try:
        from core.private_chat import turn_is_private

        # A private turn belongs to the private-chat machinery (core.private_chat): its words must
        # not reach this loop's brain — with VOOL_CLEAN_LOOP_CLOUD on (default) that can be the
        # owner's cloud model — and its pin/rename/move asks must keep the switch's own refusal
        # (no-trace contract, default-on delivery 2026-11-01: "pin this chat" in a private chat was
        # claimed here as a follow-up and answered by the brain).
        if turn_is_private(source_context, session_id):
            return "private_turn"
    except Exception:
        # The switch could not be read: treat the turn as private, as core.private_chat.record_chat_ref does.
        return "private_turn"
    try:
        from core.goal_workflow import load_active_goal_for_session

        if session_id and load_active_goal_for_session(session_id):
            return "workflow_owns_turn"
    except Exception:
        pass
    try:
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(dict(source_context)):
            return "not_owner_local"
    except Exception:
        return "not_owner_local"
    return ""


def _database_work_owns(text: str, source_context: Mapping[str, Any]) -> bool:
    """A data question about the person's own databases, or a follow-up inside a database chat."""
    try:
        from core.database_work_state import active_database_work_intents
        from core.tool_demand_signals import asks_for_database_work

        if asks_for_database_work(text):
            return True
        # Inside a database chat a bare follow-up stays with the database tools, but a turn that
        # plainly asks about the owner's files or computer is still this loop's.
        return bool(active_database_work_intents(dict(source_context))) and not asks_about_own_files_or_computer(text)
    except Exception:
        return False


def _memory_lane_owns(text: str) -> bool:
    """A forget, a "remember that", the memory list: the memory lane's commands. Taken here as a follow-up of
    a files turn, a forget was answered by the loop's model and nothing was forgotten (served, 2026-11-12)."""
    try:
        from core.persistent_memory import memory_command_owns_turn
    except Exception:
        # The memory lane cannot be asked: the turn keeps the old pipeline, which owns these commands.
        return True
    return memory_command_owns_turn(text)
def skill_keeps_turn(text: str, source_context: Mapping[str, Any] | None) -> str:
    """Why a skill keeps this turn in the normal lane, the only lane that reads skills, or "".

    ``named_skill_turn``: the person names a skill VOOL has, built-in or added ("Use the project
    archaeology skill for the file history"). ``added_skill_turn``: the ask picks, by its words, a
    skill the person added (made by them, or added from Browse, the Skill Codex, a file or an MCP
    server). This loop's brain never sees skills, so taking either turn would pass over the skill.
    A built-in skill picked by words alone does not count: the loop keeps those file asks. Checked
    after the cheap gates and before the brain; an error leaves the decision as it was.
    """
    context = dict(source_context or {})
    try:
        from core.tool_offer_assembly import named_skill_for

        if named_skill_for(text, source_context=context):
            return "named_skill_turn"
    except Exception:
        pass
    try:
        from core.tool_offer_assembly import added_skill_for

        if added_skill_for(text, source_context=context):
            return "added_skill_turn"
    except Exception:
        pass
    return ""


def decide(
    text: str,
    *,
    source_context: Mapping[str, Any] | None,
    session_id: str = "",
    resolve_brain: Callable[[], Any] | None = None,
    browser_turn: bool = False,
    env: Mapping[str, str] | None = None,
    file_words: Callable[[], set[str]] | None = None,
    document_match: Callable[[str], bool] | None = None,
) -> RouteDecision:
    if not flag_on(env):
        return RouteDecision(False, "flag_off")
    if _memory_lane_owns(text):
        return RouteDecision(False, "memory_command")
    if is_red(text):
        return RouteDecision(False, "red_turn")
    if is_write(text) and not changes_enabled(env):
        return RouteDecision(False, "write_turn")
    if is_run_order(text):
        return RouteDecision(False, "run_turn")
    if browser_turn:
        return RouteDecision(False, "browser_turn")
    blocked = _owner_context_blocks(dict(source_context or {}), session_id)
    if blocked:
        return RouteDecision(False, blocked)
    if is_online_ask(text):
        return RouteDecision(False, "online_ask")
    if is_notes_ask(text):
        return RouteDecision(False, "notes_ask")
    if _database_work_owns(text, dict(source_context or {})):
        # The person's own databases belong to the database tools, which this loop's brain does not
        # have: it answered "lacks sufficient context" to "has Tomas given everything back" and "is
        # Ines holding any books" (served run14), turns the database lane answers from real rows.
        return RouteDecision(False, "database_work")
    if skill_owns_turn(text):
        return RouteDecision(False, "skill_owns_turn")
    by_documents = False
    if not (
        asks_about_own_files_or_computer(text)
        or is_privacy_ask(text)
        or _is_follow_up(text, session_id)
        or names_owner_file(text, file_words)
    ):
        # Last, and only for the owner's own turn: words the owner's documents hold (bounded,
        # in-memory index; no model call).
        by_documents = document_match is not None and bool(document_match(text))
        if not by_documents:
            return RouteDecision(False, "not_about_own_files")
    kept = skill_keeps_turn(text, source_context)
    if kept:
        return RouteDecision(False, kept)
    brain = resolve_brain() if resolve_brain is not None else None
    if brain is None:
        return RouteDecision(False, "brain_not_local")
    return RouteDecision(True, "admitted", brain, by_documents=by_documents)


__all__ = [
    "CHANGES_ENV",
    "FLAG_ENV",
    "RouteDecision",
    "ask_words",
    "asks_about_own_files_or_computer",
    "change_reply",
    "changes_enabled",
    "decide",
    "flag_on",
    "is_notes_ask",
    "is_online_ask",
    "is_privacy_ask",
    "is_question",
    "is_red",
    "is_run_order",
    "is_write",
    "names_owner_file",
    "note_clean_turn",
    "skill_keeps_turn",
    "skill_owns_turn",
]
