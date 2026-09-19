from __future__ import annotations

import re

# ---------------------------------------------------------------------------------------------
# "Is this sentence ASKING THE RUNTIME for a fact, or is it merely ABOUT the topic?"
#
# The same defect has now been found seven times, each time inside a different handler: ordinary
# prose read as a destructive command, a discussion read as a build instruction, a greeting eating
# the question after it, a bare `name:` in a spec read as a rename, "so it stays useful over time"
# answered with the clock, "a shared drive" answered with disk space, "write a function that
# reports gpu usage" answered by running the process list. Every one of them was a TOPIC WORD
# claiming a whole turn, and every one was fixed privately inside the detector that happened to
# get caught.
#
# These helpers are that fix in shared form, so the next family inherits it instead of
# rediscovering it. They follow the rule set by `_greeting_is_the_whole_message`: do not enumerate
# forbidden topics -- ask what the sentence ASKS FOR, and what REMAINS once the matched phrase is
# taken out of it.
#
# A runtime fact read is answerable only when the message (a) is a request at all, (b) is not
# asking for text to be produced or a concept taught, (c) is not asking about the past, a cause,
# or a definition, and (d) names the topic INSIDE the clause that carries the ask -- so a topic
# sitting in some other clause ("my landlord says the storage space in the basement is included in
# the rent, is that normal?") no longer answers a question that was never asked.
# ---------------------------------------------------------------------------------------------

# A request to PRODUCE text or TEACH a subject is about the subject, not about this host. The verb
# alone is not enough ("check my disk space" would be lost), so it must govern a text/teaching
# object. This is the generalised form of `_AUTHORING_NOT_MEASURING_RE` below, which was added for
# exactly this shape when "write a function that reports gpu usage" ran the process list.
_PRODUCE_OR_TEACH_RE = re.compile(
    r"\b(?:writ(?:e|ing)|draft(?:ing)?|compos(?:e|ing)|rewrit(?:e|ing)|reword(?:ing)?|"
    r"edit(?:ing)?|proofread(?:ing)?|review(?:ing)?|critique|translat(?:e|ing)|"
    r"paraphras(?:e|ing)|explain(?:ing)?|describ(?:e|ing)|teach(?:ing)?|defin(?:e|ing)|"
    r"document(?:ing)?|outlin(?:e|ing)|sketch(?:ing)?|generat(?:e|ing)|blog|tweet|"
    r"summari[sz](?:e|ing)|recap|annotat(?:e|ing)|caption(?:ing)?)\b"
    r"[^.?!\n]{0,60}?"
    r"\b(?:paragraph|sentence|sentences|email|e-mail|letter|memo|doc|docs|document|documentation|"
    r"note|notes|message|post|article|essay|copy|caption|text|wording|grammar|spelling|draft|"
    r"reply|readme|guide|tutorial|explanation|explainer|summary|answer|story|script|scripts|"
    r"function|program|code|snippet|class|method|module|command|query|example|onboarding|"
    r"changelog|release notes|slide|slides|deck|policy|prose|blurb|headline|subject line|"
    # The OBJECT can be a piece of writing the user is handing over, not only one they want back:
    # "summarise this REVIEW of the new MacBook battery life" is a document to work on.
    r"review|reviews|ticket|thread|transcript|chapter|abstract|readme|spec|specs?heet)\b",
    re.IGNORECASE,
)

# General-knowledge framings: a cause, a definition, a comparison, or the past. A live measurement
# of this host answers none of them. Same reasoning as `_DISPLAY_GENERAL_RE` and
# `_LOCAL_FACT_ADVISORY_RE`, which already carve these out for their own families.
_GENERAL_KNOWLEDGE_RE = re.compile(
    r"\bdifference(?:s)?\s+between\b"
    r"|\bwhat(?:'s|s|\s+is|\s+are)\s+(?:a|an)\b"
    r"|\b(?:why|how\s+come)\s+(?:did|do|does|was|were|is|are)\b"
    # The two framings its own comment always promised: past attribution ("who made linux")
    # and the definition frame ("what RAM means", "what does GPU stand for"). Both reached the
    # hardware/measurement families as if they asked about THIS host (measured live
    # 2026-08-29); standing them down to the model lane is the safe direction of error for
    # every family that shares this gate.
    r"|\bwho\s+(?:made|created|invented|wrote|built|founded|discovered)\b"
    r"|\bwhat\b[^.?!\n]{0,30}?\b(?:means?|meaning|stands?\s+for)\b"
    r"|\bhistorical(?:ly)?\b|\bback\s+then\b|\bused\s+to\b|\bin\s+the\s+(?:19|20)\d{2}s?\b"
    r"|\bin\s+the\s+(?:early|late|mid)\s+(?:19|20)\d{2}s?\b"
    r"|\btypical(?:ly)?\b|\bin\s+general\b|\bgenerally\s+speaking\b",
    re.IGNORECASE,
)

# The clause that carries the ask: it ends the message with a question mark, opens with an
# interrogative, or opens with an imperative aimed at the runtime.
_INTERROGATIVE_OPENER_RE = re.compile(
    r"^(?:so|ok|okay|hey|hi|and|but|also|then|now|please|pls|quick(?:ly)?|just)?[\s,]*"
    r"(?:what|whats|what's|which|where|when|how|why|who|whose|is|are|am|do|does|did|"
    r"can|could|would|will|shall|should|have|has|had|any|got)\b",
    re.IGNORECASE,
)
_IMPERATIVE_ASK_RE = re.compile(
    r"^(?:so|ok|okay|hey|and|also|then|now|please|pls|just|quick(?:ly)?|could\s+you|can\s+you)?[\s,]*"
    r"(?:tell|show|give|list|check|report|find|look|see|read|display|print|get|fetch|scan|"
    r"inspect|measure|count|summari[sz]e|run|pull\s+up|bring\s+up)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[?!.;:,])\s+")

# Words that carry no subject of their own. What is left after the topic and these come out is the
# rest of the sentence -- and if there IS none, the topic was the whole message.
_REQUEST_FILLER_WORDS = frozenset(
    {
        "a", "an", "the", "my", "mine", "our", "ours", "this", "that", "these", "those",
        "please", "pls", "hey", "hi", "ok", "okay", "so", "and", "but", "also", "then", "now",
        "just", "quick", "quickly", "i", "me", "we", "us", "you", "your", "it", "its",
        "what", "whats", "what's", "hows", "how", "much", "many", "is", "are", "am", "do", "does",
        "did", "can", "could", "would", "will", "have", "has", "had", "got", "get", "give",
        "tell", "show", "check", "report", "see", "look", "list", "read", "display", "print",
        "left", "on", "of", "in", "at", "for", "to", "there", "here", "still", "right", "current",
        "currently", "total", "free", "used", "available", "status", "usage", "s", "u",
    }
)


# Words that only ever QUALIFY a measurement -- a ranking, a quantity, or the shape of the output.
# They add no subject, so "biggest files on my drive" and "top 5 folders" are still bare requests
# for the topic and nothing else. Kept separate from the filler list because these are specific to
# a measurement ask rather than to English scaffolding.
_MEASUREMENT_MODIFIER_WORDS = frozenset(
    {
        "biggest", "largest", "smallest", "big", "large", "small", "top", "bottom", "most",
        "least", "highest", "lowest", "full", "empty", "remaining", "leftover", "size", "sizes",
        "per", "each", "all", "every", "report", "overview", "summary", "breakdown", "numbers",
        "stats", "statistics", "details", "info", "information", "figures", "amount", "level",
        "levels", "up", "down", "out", "over", "about", "please", "thanks", "now", "again",
    }
)


def _topic_is_the_whole_message(lowered: str, topic_re: re.Pattern[str]) -> bool:
    """Nothing with a subject of its own survives the topic -- so the topic IS the request.

    The `_greeting_is_the_whole_message` test, reused. "disk space" and "free space?" are requests
    because there is nothing else in them to be about, and so is a bare noun phrase that only
    ranks or shapes the same topic ("biggest files on my drive", "space usage report please") --
    people ask for a measurement that way constantly and it carries no verb to detect.
    """
    remainder = topic_re.sub(" ", lowered)
    remainder = re.sub(r"[^\w\s]", " ", remainder)
    leftover = [
        word
        for word in remainder.split()
        if word not in _REQUEST_FILLER_WORDS
        and word not in _MEASUREMENT_MODIFIER_WORDS
        and not word.isdigit()
    ]
    return not leftover


def _ask_clause(lowered: str) -> str:
    """The clause(s) actually carrying a request, or "" when the message asks for nothing."""
    parts = [part.strip() for part in _CLAUSE_SPLIT_RE.split(lowered) if part.strip()]
    if not parts:
        return ""
    asks = [
        part
        for part in parts
        if part.rstrip().endswith("?")
        or _INTERROGATIVE_OPENER_RE.match(part)
        or _IMPERATIVE_ASK_RE.match(part)
    ]
    if asks:
        return " ".join(asks)
    return lowered if lowered.rstrip().endswith("?") else ""


# The user's OWN possessive landing directly on the topic: "my hard drive", "this machine's ram",
# "our disk". People report a state as often as they ask for one -- "my hard drive is nearly full"
# is a request for the numbers, not a remark -- so a possessive that GOVERNS the topic is an ask in
# its own right. It has to govern it: at most two words may sit between, which is what keeps "our
# warehouse is out of floor space" and "my landlord says the storage space in the basement" out.
_OWN_TOPIC_TEMPLATE = r"\b(?:my|our|this|these)\s+(?:\w+\s+){0,2}?(?:%s)\b"


def _host_owned_topic(lowered: str, topic_re: re.Pattern[str]) -> bool:
    inner = topic_re.pattern
    try:
        return re.search(_OWN_TOPIC_TEMPLATE % inner, lowered, re.IGNORECASE) is not None
    except re.error:
        return False


def asks_runtime_for_a_fact(
    text: str,
    topic_re: re.Pattern[str],
    *,
    also_anchored: bool = False,
    owned_topic_re: re.Pattern[str] | None = None,
) -> bool:
    """True when the message asks THIS runtime for a present fact about ``topic_re``.

    False for prose that merely contains the topic word, for a request to write or explain
    something about it, for a question about its history/definition, and for a topic that sits
    outside the clause carrying the ask.

    ``also_anchored`` lets a family add its own unambiguous this-host evidence (a named drive
    letter, say). It is consulted only AFTER the produce/teach and general-knowledge guards, so an
    anchor can widen what counts as an ask but can never turn an essay into a measurement.

    ``owned_topic_re`` opts the family into the possessive anchor, and is a SEPARATE pattern
    because it is only sound for nouns that can mean nothing but this machine. "my hard drive is
    nearly full" is a request for the numbers; "our deployment process uses a lot of memory" and
    "my hiring process" are not, because `process`, `program` and `app` name non-machine things
    constantly. Families whose topic is polysemous simply leave this off.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if _PRODUCE_OR_TEACH_RE.search(lowered):
        return False
    if _GENERAL_KNOWLEDGE_RE.search(lowered):
        return False
    if _topic_is_the_whole_message(lowered, topic_re):
        return True
    if also_anchored:
        return True
    if owned_topic_re is not None and _host_owned_topic(lowered, owned_topic_re):
        return True
    ask = _ask_clause(lowered)
    if not ask:
        return False
    return topic_re.search(ask) is not None


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# Two tiers, same shape as the disk STRONG-phrase/AMBIGUOUS-cue split further down this file: a
# STRONG marker is unambiguous on its own (a multi-word phrase, or a word/acronym with no everyday
# non-tool sense). An AMBIGUOUS marker is a single plain-English word that also has a common
# everyday sense with zero tool/lookup intent, so it only counts as a signal when a SECOND marker
# (ambiguous or strong, from either list) also appears in the same message -- one bare hit is not
# enough. Measured live: every ambiguous entry below matched an ordinary, tool-free sentence, e.g.
# "check this couch for comfort before purchasing" (via "check") and "examine my sleep schedule and
# suggest improvements" (via "schedule"), each of which used to open the tool-intent lane for chat
# that had nothing to do with this machine.
_LIVE_LOOKUP_STRONG_MARKERS = (
    "release notes",
    "status page",
    "search online",
    "check online",
    "look up",
    "show me",
    "on x",
    "on twitter",
    "on the web",
    "on web",
)
_LIVE_LOOKUP_AMBIGUOUS_MARKERS = (
    "latest",
    "current",
    "today",
    "recent",
    "version",
    "fetch",
    "pull",
    "open",
    "browse",
    "render",
    "google",
    "find",
    "check",
)
_LOCAL_TOOL_STRONG_MARKERS = (
    "clean temp",
    "mkdir",
    "desktop",
    "downloads",
    "documents",
    "directory",
    "folder",
    "cpu",
    "gpu",
    "vram",
)
_LOCAL_TOOL_AMBIGUOUS_MARKERS = (
    "process",
    "service",
    "disk",
    "space",
    "cleanup",
    "move",
    "archive",
    "calendar",
    "meeting",
    "schedule",
    "tool",
    "machine",
    "hardware",
    "specs",
    "cores",
    "ram",
    "memory",
    "chip",
)
# Read-only disk/drive questions that map to the machine.disk_usage tool. Two tiers: a STRONG
# phrase is unambiguous on its own; the ambiguous space-family CUES only count as a disk question
# when an actual storage-device NOUN co-occurs, so "free space in my calendar" or "how much storage
# does my cloud plan give me" fall through to the model instead of running the disk tool.
_DISK_STRONG_PHRASES = (
    "how many drives",
    "how many drive",
    "how many disks",
    "number of drives",
    "number of disks",
    "list drives",
    "list my drives",
    "my drives",
    "hard drive",
    "hard drives",
    "hard disk",
    "disk space",
    "drive space",
    "disk usage",
    "how much disk",
    "drives do i have",
    "drives does my",
    "space on my drive",
    "space on my disk",
    "space on this drive",
    "space on this disk",
)
_DISK_NOUNS = ("drive", "drives", "disk", "disks", "ssd", "hdd", "volume", "volumes", "partition", "partitions")
# The storage subject, for `asks_runtime_for_a_fact`: what has to appear inside the clause that
# carries the ask before a drive report can be the answer to it.
_DISK_TOPIC_RE = re.compile(
    r"\b(?:disk|disks|disc|drive|drives|ssd|hdd|volume|volumes|partition|partitions|"
    r"storage|space|capacity|room|gigabytes?|gb|terabytes?|tb)\b",
    re.IGNORECASE,
)
# The subset a possessive can anchor on. "space" and "room" are left out on purpose -- "my
# warehouse space" and "my spare room" are not this machine.
_DISK_OWNED_TOPIC_RE = re.compile(
    r"\b(?:disk|disks|drive|drives|ssd|hdd|volume|volumes|partition|partitions)\b",
    re.IGNORECASE,
)
_DISK_SPACE_CUES = (
    "free space",
    "space left",
    "space free",
    "storage space",
    "how much storage",
    "how much space",
    "space available",
    "available space",
    "total space",
    "space total",
    "total storage",
    "used space",
    "space used",
    "total capacity",
)
# Both tiers match on WORD BOUNDARIES, never as bare substrings. `"hard drive" in lowered` is true
# of "a s|hard drive|", and that is not a hypothetical: the normalizer rewrites "shared" -> "shard"
# (a mesh vocabulary word), so "What's a sensible file naming convention for a shared drive that has
# many versions of the same document?" reached this function as "...for a shard drive..." and was
# answered "Drive space: / 69.4 GB free of 460.4 GB" in 0.2s, reproduced 2/2 on the running daemon.
# The `shared` rewrite is fixed at its source in core/input_normalizer.py; the boundary is fixed here
# because a phrase table that matches inside other words will keep finding new ways to be wrong.
# Same reason for the cues: "space left" is a substring of "work|space left| over".
_DISK_STRONG_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(phrase) for phrase in _DISK_STRONG_PHRASES) + r")\b",
    re.IGNORECASE,
)
_DISK_SPACE_CUE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(cue) for cue in _DISK_SPACE_CUES) + r")\b",
    re.IGNORECASE,
)
_NOUNLESS_LOCAL_DISK_SPACE_RE = re.compile(
    # `whats` with no apostrophe, alongside `what's` and `what is`. People drop the apostrophe
    # constantly and every other question detector in this file already allows for it
    # (_LOCAL_FACT_QUESTION_RE and _HOST_STATE_GENERAL_RE both list `whats`); this one did not.
    # Measured live 2026-07-30 on 503249a: "whats my free space right now" got "I can only answer
    # that by inspecting your drives on your machine, and that tool didn't run", while "what's my
    # free space right now" answered from the disk in 0.2s. The fabrication backstop was right
    # that a drive read was needed -- the read simply had no route, so the user was refused an
    # answer the runtime holds, over one apostrophe.
    r"(?:\bwhat(?:'s|s|\s+is)\s+my\s+(?:total\s+)?(?:free|available)\s+space\b"
    r"|\bhow\s+much\s+(?:free\s+space|storage(?:\s+space)?|space)\s+"
    # The same first-person ask with the auxiliary in the other place -- "how much space i have
    # left", "how much space have i got left". Measured live 2026-07-31: "tell me how much space i
    # have left please" spent 60s in the model and returned nothing, because the rule required the
    # word "do" ("...space do i have"). This rule already exists to own the noun-less first-person
    # space question; these are that question, ordered the way people speak it.
    r"(?:do\s+i\s+have|is\s+left|is\s+free|is\s+available"
    r"|i\s+have\s+left|i've\s+got\s+left|ive\s+got\s+left|have\s+i\s+got\s+left"
    r"|i\s+have\s+got\s+left|i\s+got\s+left)\b)",
    re.IGNORECASE,
)
_NON_LOCAL_SPACE_CONTEXT_RE = re.compile(
    r"\b(?:calendar|schedule|cloud|icloud|subscription|account|plan|database|warehouse|phone|camera)\b",
    re.IGNORECASE,
)
# The phrase tables above enumerate WORDINGS ("free space", "disk space", "how much storage"). People
# do not ask in those wordings. Measured live 2026-07-31 on d51250a, 20 of 22 ordinary ways to ask
# this machine how full it is were refused or mis-answered: "am i running out of storage", "whats the
# storage situation looking like" and "do i have room for a 50gb download" each fell through to the
# model and came back "I couldn't get a usable model response"; "how many gb are left on this
# machine" was answered with the SPEC SHEET ("RAM: 24.0 GiB"), a wrong number for the question asked.
# The runtime holds the answer -- `df` was one call away in every case.
#
# So this matches the RELATION a capacity question expresses (a capacity PREDICATE applied to a
# storage TOPIC), not the strings it happens to be spelled with. Four relations cover the family:
# remaining, exhaustion, fullness, and fit. Every one still runs behind `asks_runtime_for_a_fact`
# (which is what rejects "our warehouse is running out of floor space", "how much room is left in
# the venue" and "am i running out of time") and behind _NON_LOCAL_SPACE_CONTEXT_RE, so widening the
# accepted PHRASING does not widen what counts as a question about this host.
#
# "space" and "room" are split off from the rest because they are the two words in the family with a
# thriving non-storage sense, and a bare remaining-relation built on them says nothing about this
# host: "how much room is left in the venue" is the same relation as "how much room is left on my
# ssd". So a relation resting on one of THOSE additionally needs a corroborating anchor -- a storage
# device, a data unit, or an explicit reference to this host -- which is the same two-tier rule
# _DISK_SPACE_CUES already follows. "storage", "gb", "tb" and friends carry the sense on their own.
_STORAGE_QTY = r"(?:space|room|storage|capacity|gb|tb|mb|gigabytes?|terabytes?|megabytes?)"
_STORAGE_AMBIGUOUS_QTY_RE = re.compile(r"\b(?:space|room|capacity)\b", re.IGNORECASE)
_STORAGE_HOST_ANCHOR_RE = re.compile(
    r"\b(?:disks?|drives?|ssd|hdd|volumes?|partitions?|storage"
    r"|gb|tb|mb|gigabytes?|terabytes?|megabytes?"
    r"|machine|computer|laptop|mac|macbook|imac|pc|harddrive)\b"
    r"|\b(?:do\s+i\s+have|have\s+i\s+got|i've\s+got|ive\s+got|on\s+here|on\s+this)\b"
    # First person about oneself ("am i nearly out of space") is this-host framing once the shared
    # gate has already ruled the sentence a live question about storage: the user asking whether
    # THEY are out of space is asking about the machine they are typing into. The venue, the fridge
    # and the meeting room are all asked about in the third person.
    r"|\b(?:am\s+i|i\s+am|i'm|im)\b",
    re.IGNORECASE,
)
_STORAGE_DEVICE = r"(?:disks?|drives?|ssd|hdd|volumes?|partitions?|storage)"
# The quantity being asked about is EITHER an amount word or the device itself: "am i running out of
# disk" names the device where "am i running out of storage" names the amount, and they are the same
# question. Measured live 2026-07-31: with the amount words alone, "am i running out of disk",
# "whats the state of my storage", "how much free room is on the drive", "have i got enough disk for
# a 20gb video" and "whats my remaining capacity on this mac" all still missed.
_STORAGE_SUBJECT = rf"(?:{_STORAGE_QTY[3:-1]}|{_STORAGE_DEVICE[3:-1]})"
_STORAGE_CAPACITY_ASK_RE = re.compile(
    # REMAINING -- "how many gb are left", "how much space have i got left", "whats left on my drive",
    # "how much is left on my ssd". The quantity and the remainder word are separated by arbitrary
    # filler ("do i have", "have i got"), so a bounded word gap carries it rather than a fixed phrase.
    rf"\b{_STORAGE_SUBJECT}\b(?:\s+\S+){{0,4}}?\s+(?:left|remaining|remain|available|spare)\b"
    rf"|\b(?:left|remaining|available|spare)\s+(?:on|in)\s+(?:my|this|the)\s+{_STORAGE_DEVICE}\b"
    # The modifier can equally lead the noun: "remaining capacity", "free room", "available storage".
    # "free" is admitted HERE, where it directly modifies a storage subject, and as a predicate below
    # ("how many gigs are free") -- but never as a bare word elsewhere in the sentence, which is what
    # keeps "how many gb does the free tier give you" out.
    rf"|\b(?:left|remaining|available|spare|free|unused)\s+{_STORAGE_SUBJECT}\b"
    rf"|\b{_STORAGE_SUBJECT}\b(?:\s+\S+){{0,3}}?\s+(?:is|are|'s)\s+free\b"
    # EXHAUSTION -- "am i running out of storage", "running low on space", "am i nearly out of space".
    rf"|\b(?:run(?:ning|s)?\s+(?:out\s+of|low\s+on)|low\s+on|(?:nearly|almost|about\s+to\s+be)\s+out\s+of)\s+"
    rf"(?:\S+\s+){{0,2}}?{_STORAGE_SUBJECT}\b"
    # FULLNESS -- "how full is my disk", "is my drive nearly full", "how much of my disk is used",
    # "percentage of disk used". The device noun is required: "how full is the venue" is not this.
    rf"|\bhow\s+full\s+(?:is|are)\s+(?:my|the|this)?\s*{_STORAGE_DEVICE}\b"
    rf"|\b(?:my|the|this)\s+{_STORAGE_DEVICE}\b(?:\s+\S+){{0,3}}?\s+(?:full|filling\s+up)\b"
    rf"|\bhow\s+much\s+of\s+(?:my|the|this)\s+{_STORAGE_DEVICE}\b(?:\s+\S+){{0,3}}?\s+used\b"
    rf"|\b(?:percentage|percent|%)\s+of\s+(?:my\s+|the\s+|this\s+)?{_STORAGE_DEVICE}\b(?:\s+\S+){{0,2}}?\s*used\b"
    # FIT -- "do i have room for a 50gb download", "have i got enough disk for a 20gb video". A
    # CONCRETE data size is required, which is what separates it from "room for another meeting".
    rf"|\b{_STORAGE_SUBJECT}\s+for\b(?:\s+\S+){{0,3}}?\s+\d+\s*(?:gb|tb|mb|gigs?|gigabytes?|terabytes?)\b"
    # STATE -- "whats the storage situation looking like", "whats the state of my storage".
    rf"|\b{_STORAGE_DEVICE}\s+(?:situation|status|health)\b"
    rf"|\b(?:state|status|situation|health)\s+of\s+(?:my|the|this)\s+{_STORAGE_DEVICE}\b"
    rf"|\b(?:my|the|this)\s+{_STORAGE_DEVICE}\b(?:\s+\S+){{0,2}}?\s+looking\s+like\b",
    re.IGNORECASE,
)
# Mutating/cleanup intents in ANY tense are reads-turned-writes — suppressed so they fall to the
# operator action lane, not answered as a read (matches stems, so "cleaning"/"removing" also count).
_MACHINE_DIAG_WRITE_RE = re.compile(
    r"\b(?:format(?:ting)?|wip(?:e|ing)|eras(?:e|ing)|delet(?:e|ing)|remov(?:e|ing)"
    r"|partition(?:ing)?|clean(?:ing|up)?|clear(?:ing)?|defrag\w*|free\s+up|freeing\s+up)\b"
)


# A disk question that names ONE specific drive ("free space on C:", "C drive") should report
# that drive only, not the all-drive total. Returns a drive root like "C:\\" or None (all drives).
_DISK_DRIVE_SCOPE_RE = re.compile(
    r"\b([a-z]):(?![a-z])|(?:\b(?:on|for|of|my|the|this)\s+)([a-z])\s+drive\b|\bdrive\s+([a-z])\b"
    # A bare trailing drive letter after "on" ("biggest file on G?", "free space on d") -- the
    # letter must be the last token so ordinary words ("on a file") never match.
    r"|(?:\bon\s+)([a-z])(?=[\s?!.]*$)",
    re.IGNORECASE,
)


def machine_disk_scope(text: str) -> str | None:
    lowered = " ".join(str(text or "").strip().lower().split())
    match = _DISK_DRIVE_SCOPE_RE.search(lowered)
    if not match:
        return None
    letter = next((group for group in match.groups() if group), "")
    if len(letter) != 1 or not letter.isalpha():
        return None
    return f"{letter.upper()}:\\"


# An anaphoric reference to the drive of the previous machine read ("the biggest file on
# THAT drive", "how much is left on it", "same disk", "in there"). Resolved against the
# last drive the fast path scoped to, so a follow-up doesn't silently default to C:.
_DRIVE_ANAPHORA_RE = re.compile(
    r"\b(?:that|this|the\s+same|same)\s+(?:drive|disk|volume)\b|\bin\s+there\b|\bon\s+it\b|\bthat\s+one\b",
    re.IGNORECASE,
)


def resolve_drive_scope(text: str, *, last_drive: str | None = None) -> str | None:
    """The drive a machine read should scope to: an explicit drive in the text wins; else an
    anaphoric reference ("that drive") resolves to ``last_drive`` from the prior read; else None
    (all drives / tool default). Keeps "biggest file on that drive" pinned to the drive the user
    was just talking about instead of falling back to the system drive."""
    explicit = machine_disk_scope(text)
    if explicit:
        return explicit
    lowered = " ".join(str(text or "").strip().lower().split())
    if last_drive and _DRIVE_ANAPHORA_RE.search(lowered):
        return last_drive
    return None


# A bare drive-letter follow-up to a prior disk read ("ok what about D?", "and D:", "how about
# the E drive"). Deliberately tight: the WHOLE message must reduce to filler + one drive token,
# so ordinary questions ("what about dinner?") never match.
_DRIVE_FOLLOWUP_FILLER_RE = re.compile(
    r"^(?:ok(?:ay)?|and|so|now|then|but|also|please|pls|what\s+about|how\s+about|"
    r"what\s+of|and\s+for|for|check|show\s+me|look\s+at|tell\s+me\s+about)\b[\s,]*",
    re.IGNORECASE,
)
_DRIVE_TOKEN_RE = re.compile(r"^(?:the\s+)?(?:drive\s+)?([a-z])(?::\\?|\s+drive)?\s*\??$", re.IGNORECASE)


def elliptical_drive_followup(text: str) -> str | None:
    """A drive root ("D:\\\\") when ``text`` is a bare drive-letter follow-up, else None. Only the
    caller's prior-read context makes this a disk question -- so the caller must gate it on there
    being a recent machine disk read before routing to the disk tool."""
    remainder = " ".join(str(text or "").strip().lower().split())
    if not remainder:
        return None
    prev = None
    while remainder and remainder != prev:
        prev = remainder
        remainder = _DRIVE_FOLLOWUP_FILLER_RE.sub("", remainder).strip()
    match = _DRIVE_TOKEN_RE.match(remainder)
    if not match:
        return None
    return f"{match.group(1).upper()}:\\"


_LARGEST_FILES_RE = re.compile(r"\bfiles?\b", re.IGNORECASE)
_LARGEST_FOLDERS_RE = re.compile(r"\b(?:folders?|director(?:y|ies))\b", re.IGNORECASE)


def largest_kind(text: str) -> str:
    """Whether a "biggest/largest" query asks about ``files``, ``folders``, or ``both`` (default).
    "biggest file on D:" must rank individual files, not folders."""
    lowered = " ".join(str(text or "").strip().lower().split())
    has_file = bool(_LARGEST_FILES_RE.search(lowered))
    has_folder = bool(_LARGEST_FOLDERS_RE.search(lowered))
    if has_file and not has_folder:
        return "files"
    if has_folder and not has_file:
        return "folders"
    return "both"


def machine_diagnostics_intent(text: str) -> str | None:
    """Map a read-only disk/drive question to the ``machine.disk_usage`` tool intent, else ``None``.

    A strong phrase ("how many drives", "disk space", ...) matches on its own; ambiguous space-family
    cues ("free space", "how much storage") only match when a real storage-device noun co-occurs, so
    metaphorical uses ("free space in my calendar") fall through to the model. A mutating/cleanup verb
    in any tense suppresses the match so "clean up disk space" is left to the operator action lane.
    (Windows event-log routing is intentionally not wired here yet: machine.event_log_errors works,
    but its output trips the model-text traceback sanitizer in agent_runtime/response.py; wiring it
    needs a trusted-tool-output bypass first.)
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered):
        return None
    # The phrase tables below say the message MENTIONS storage. They cannot say it ASKS about this
    # host's storage, and on their own they answered eight ordinary sentences out of twelve with a
    # drive report -- a warehouse out of floor space, a drummer's hard drive of samples, what a
    # 1990s hard drive held, a grammar fix on a sentence about a full server, and a request to
    # write onboarding copy about why disk space matters, each measured on the deployed build
    # 2026-07-30 in about half a second. Deciding on what the sentence asks for is the fix that
    # has already had to be made for the clock, the greeting, the builder and the process list.
    # A named drive ("on C:", "my D drive") is this-host evidence no metaphor produces, so it is
    # passed as the family's own anchor rather than re-derived inside the shared gate.
    if not asks_runtime_for_a_fact(
        text,
        _DISK_TOPIC_RE,
        also_anchored=bool(machine_disk_scope(text)),
        owned_topic_re=_DISK_OWNED_TOPIC_RE,
    ):
        return None
    if _NOUNLESS_LOCAL_DISK_SPACE_RE.search(lowered) and not _NON_LOCAL_SPACE_CONTEXT_RE.search(lowered):
        return "machine.disk_usage"
    if _STORAGE_CAPACITY_ASK_RE.search(lowered) and not _NON_LOCAL_SPACE_CONTEXT_RE.search(lowered):
        # A relation resting only on "space"/"room"/"capacity" needs corroboration that the subject is
        # a storage device on this host; otherwise the venue, not the SSD, is what is running out.
        if not _STORAGE_AMBIGUOUS_QTY_RE.search(lowered) or _STORAGE_HOST_ANCHOR_RE.search(lowered):
            return "machine.disk_usage"
    if _DISK_STRONG_RE.search(lowered):
        return "machine.disk_usage"
    if _DISK_SPACE_CUE_RE.search(lowered) and any(
        re.search(rf"\b{noun}\b", lowered) for noun in _DISK_NOUNS
    ):
        return "machine.disk_usage"
    # An explicit drive scope ("free space on C:", "how much space is left on D:") is itself an
    # unambiguous storage-device reference, so a space cue + a named drive routes to the disk tool
    # even without a separate disk noun. machine_disk_scope only matches a real drive (a letter with
    # a colon, or the word "drive"), so metaphors ("free space in my calendar") never match here.
    if _DISK_SPACE_CUE_RE.search(lowered) and machine_disk_scope(text):
        return "machine.disk_usage"
    return None


# Local folder-search requests ("find my dropbox folder", "where is the Website V3
# folder on this pc") map to the read-only machine.find_folder tool. The name is
# whatever sits between the search verb and the folder/directory noun. Write-stem
# guard reuses the machine-diagnostics rule so "delete the dropbox folder" falls to
# the operator action lane instead of running a read.
_FOLDER_SEARCH_RE = re.compile(
    r"(?:find|locate|look\s+for|search\s+for|where(?:'s|\s+is|\s+are)|check|inspect|audit|look\s+at|go\s+through)"
    r"(?:\s+(?:me|my|mine|our|the|a|an))*"
    r"\s+(?P<name>[\w][\w .'&()\-]{0,60}?)"
    r"\s+(?:folder|folders|directory|dir)\b",
    re.IGNORECASE,
)
_FOLDER_NAMED_RE = re.compile(
    r"(?:folder|directory|dir)\s+(?:named|called)\s+[\"']?(?P<name>[\w][\w .'&()\-]{0,60})[\"']?",
    re.IGNORECASE,
)
# The named-form ("folder named X") only counts as a SEARCH when a search verb is
# present -- "create a folder named demo" is a workspace-write request, not a lookup.
# check/inspect/audit are read verbs too: "check the Token hunter folder" is a lookup,
# not a spec question -- without them it fell through to machine.inspect_specs via
# the " this machine " marker (a real reported mis-route).
_FOLDER_SEARCH_VERB_RE = re.compile(r"\b(?:find|locate|search|look\s+for|where|check|inspect|audit|look\s+at|go\s+through)\b", re.IGNORECASE)
# Asking WHERE something is, or WHETHER it exists, without using a search verb at all. Measured live
# 2026-07-31 against folders that exist on this Desktop: "i lost my orchid folder, where did it go"
# and "hey where'd my maple folder end up" each spent about a minute in the model and came back "I
# couldn't map that cleanly to a real action"; "do i have a folder called scraper anywhere" reached
# no family; "which directory holds my invoices" was answered with the workspace root while
# ~/Desktop/my-invoices-folder sat on the disk. All four are folder searches.
_FOLDER_LOCATION_ASK_RE = re.compile(
    r"\bwhere\b|\bwhere(?:'s|s|'d|d)\b|\bwhereabouts\b"
    r"|\b(?:do|did)\s+i\s+have\b|\bhave\s+i\s+got\b|\bis\s+there\b|\bare\s+there\b"
    r"|\bi\s+(?:lost|misplaced|can'?t\s+find|cannot\s+find)\b"
    r"|\bwhich\s+(?:folder|directory|dir)\b",
    re.IGNORECASE,
)
# "my orchid folder" / "the maple folder" -- the name and its noun, with no verb in front of them.
# Only consulted when a location ask is present, so ordinary prose mentioning a folder is untouched.
_FOLDER_NAME_BEFORE_NOUN_RE = re.compile(
    r"\b(?:my|our|the|a|an|that|your)\s+(?P<name>[\w][\w .'&()\-]{0,60}?)\s+(?:folder|folders|directory|dir)\b",
    re.IGNORECASE,
)
# The name on the OTHER side of the noun: "which directory holds my invoices".
_FOLDER_NAME_AFTER_NOUN_RE = re.compile(
    r"\b(?:which|what)\s+(?:folder|directory|dir)\s+"
    r"(?:holds|has|contains|keeps|stores|is)\s+(?:me|my|mine|our|the|a|an)?\s*"
    r"(?P<name>[\w][\w .'&()\-]{0,60}?)\s*[?.!]*$",
    re.IGNORECASE,
)
# A bare name with no folder noun at all, pinned to this host by an explicit machine reference:
# "locate polybets on this machine". Without that marker "find polybets" is not necessarily a
# question about the disk, so the marker is required rather than assumed.
_FOLDER_BARE_NAME_ON_HOST_RE = re.compile(
    r"\b(?:find|locate|search\s+for|look\s+for)\s+(?:me\s+|my\s+|the\s+|a\s+|an\s+)?"
    r"(?P<name>[\w][\w .'&()\-]{0,60}?)\s+"
    r"on\s+(?:this|my)\s+(?:machine|mac|computer|laptop|pc|system|box|disk|drive|desktop)\b",
    re.IGNORECASE,
)
# Locative adverbs that trail a folder NAME but are not part of it ("a folder called scraper
# anywhere"). Stripped after extraction so the disk search looks for "scraper", not "scraper
# anywhere", which cannot match a real directory.
_FOLDER_NAME_TRAILING_LOCATIVE_RE = re.compile(
    r"\s+(?:anywhere|somewhere|any\s?where|some\s?where|at\s+all|on\s+(?:this|my)\s+\w+"
    r"|on\s+here|around\s+here|please|pls)\s*$",
    re.IGNORECASE,
)
# Creation/scaffolding stems mean a write flow owns the request even if a search verb
# also appears ("create the folder ... then find it" stays a write plan).
_FOLDER_CREATE_RE = re.compile(r"\b(?:creat\w*|mak(?:e|ing)|mkdir|new folder|scaffold\w*|bootstrap\w*|generat\w*|set\s+up)\b", re.IGNORECASE)
# Words that mean the user is talking about a remote/app folder, not this machine.
_FOLDER_SEARCH_REMOTE_MARKERS = ("online", "on the web", "in gmail", "in google drive", "in the cloud", "on github")
_FOLDER_NAME_STOPWORDS = frozenset({"a", "an", "the", "me", "my", "our", "this", "that", "your"})
_FOLDER_NAME_FILLER_WORDS = frozenset({"damn", "fucking", "fuckin", "fuckign", "fcking"})

# Normalize only high-confidence misspellings of "this" immediately before a scope noun.  A
# literal folder named "thsi" remains searchable through an explicit "folder named thsi" ask.
_CURRENT_SCOPE_TYPO_RE = re.compile(
    r"\b(?:thsi|tihs|ths)\s+(?=(?:work(?:ing|space)?\s+|current\s+)?"
    r"(?:folder|dir(?:ectory)?|project|repo(?:sitory)?|workspace|code\s?base)\b)",
    re.IGNORECASE,
)


def normalize_current_scope_reference(text: str) -> str:
    """Normalize unambiguous deictic typos used to reference the bound workspace."""

    compact = " ".join(str(text or "").strip().lower().split())
    return _CURRENT_SCOPE_TYPO_RE.sub("this ", compact)

# Demonstratives that mean "the folder/scope we are ALREADY in" -- NOT a folder name to search for.
# When present, the machine named-folder lanes stand down so the project-aware folder-overview path
# resolves the BOUND project root, instead of substring-matching a word like "local" onto an
# unrelated directory (the split-brain scope bug: "analyse local folder we are in" wrongly matched
# "local" -> vool-local-product). Requires a folder-noun so it never fires on unrelated prose.
_CURRENT_SCOPE_RE = re.compile(
    # "(this/current/local/our) [work/working/workspace/current] folder|project|repo|workspace"
    r"\b(?:this|current|local|present|existing|our)\s+"
    r"(?:work(?:ing|space)?\s+|current\s+)?"
    r"(?:folder|dir(?:ectory)?|project|repo(?:sitory)?|workspace|code\s?base)\b"
    # "[the] work/working/workspace/current folder|project|repo" -- "work folder" IS the current scope,
    # not a folder literally named "work" (which substring-matched w2l_work live).
    r"|\b(?:the\s+)?(?:work(?:ing|space)?|current)\s+(?:folder|dir(?:ectory)?|project|repo(?:sitory)?|workspace)\b"
    # "the folder/project we are in"
    r"|\b(?:folder|dir(?:ectory)?|project|repo(?:sitory)?|workspace|code\s?base)\s+"
    r"(?:we(?:'re|\s+are)?|i(?:'m|\s+am)?)\s+(?:in|inside|working\s+(?:in|on))\b",
    re.IGNORECASE,
)


def refers_to_current_scope(text: str) -> bool:
    """True when the ask targets the folder/project we are ALREADY in (a demonstrative), so the
    named-folder machine lanes should defer to the bound project root."""
    normalized = normalize_current_scope_reference(text)
    if _CURRENT_SCOPE_RE.search(normalized):
        return True
    return bool(
        re.search(
            r"\b(?:folder|dir(?:ectory)?|workspace)\s+we\s+(?:are\s+)?(?:started|start(?:ed|ing)?)\s+"
            r"(?:this\s+)?project\s+(?:in|on|from)\b",
            normalized,
            re.IGNORECASE,
        )
    )


def machine_folder_search_intent(text: str) -> tuple[str, str] | None:
    """Map a local folder-search request to ``("machine.find_folder", <name>)``, else ``None``.

    Matches "find/locate/where is ... <name> folder" and "folder named <name>". A
    mutating verb (delete/move/clean...) or an explicitly remote target ("in google
    drive") suppresses the match. The extracted name must contain a real word.
    """
    lowered = normalize_current_scope_reference(text)
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered) or _FOLDER_CREATE_RE.search(lowered):
        return None
    if any(marker in lowered for marker in _FOLDER_SEARCH_REMOTE_MARKERS):
        return None
    if refers_to_current_scope(lowered):
        return None  # "this/current/local folder" = the bound scope; the project-aware path handles it
    # A folder search is signalled EITHER by a search verb ("find the X folder") or by asking where
    # something is / whether it exists ("i lost my X folder, where did it go"). The verb-only rule
    # left the second form with no route at all.
    asks_location = bool(_FOLDER_LOCATION_ASK_RE.search(lowered))
    if not _FOLDER_SEARCH_VERB_RE.search(lowered) and not asks_location:
        return None
    match = _FOLDER_NAMED_RE.search(lowered) or _FOLDER_SEARCH_RE.search(lowered)
    if match is None and asks_location:
        # The name can sit either side of the folder noun, and need not follow a verb.
        match = _FOLDER_NAME_AFTER_NOUN_RE.search(lowered) or _FOLDER_NAME_BEFORE_NOUN_RE.search(lowered)
    if match is None:
        # No folder noun anywhere: the only thing marking this as a directory search is the
        # this-host phrase. That is weak evidence, so the name must also LOOK like a directory --
        # one unbroken token ("polybets", "token-hunter", "web0_internal"). Without that,
        # "find me a good restaurant on this machine" is a folder search for "good restaurant".
        bare = _FOLDER_BARE_NAME_ON_HOST_RE.search(lowered)
        if bare is not None and not str(bare.group("name") or "").strip().split()[1:]:
            match = bare
    if not match:
        return None
    name = " ".join(str(match.group("name") or "").split()).strip(" .'\"-")
    name = _FOLDER_NAME_TRAILING_LOCATIVE_RE.sub("", name).strip(" .'\"-")
    name_words = [word for word in name.split() if word not in _FOLDER_NAME_STOPWORDS]
    if not name_words:
        return None
    clean_name = " ".join(name_words)
    if all(word in _FOLDER_NAME_FILLER_WORDS for word in clean_name.split()):
        return None
    if len(clean_name) < 2:
        return None
    return ("machine.find_folder", clean_name)


# An ANALYSIS verb wants the folder's CONTENTS ("audit/analyze/review/what's in my X folder"),
# not just its location. find_folder only LISTS paths, so those asks used to stop at a path list
# and never read anything -- the model then talked about an audit it never ran. This routes them to
# a grounded overview of the real directory instead.
_FOLDER_ANALYSIS_VERB_RE = re.compile(
    r"\b(?:audit|analy[sz]e|analy[sz]is|review|examine|inspect|"
    r"go\s+through|look\s+through|look\s+inside|dig\s+(?:in)?to|dig\s+through|break\s+down|"
    r"overview|understand|what(?:'s|s|\s+is)\s+(?:in|inside))\b",
    re.IGNORECASE,
)
_FOLDER_AUDIT_RE = re.compile(
    r"(?:audit|analy[sz]e|review|examine|inspect|check|go\s+through|"
    r"look\s+(?:through|inside|at)|dig\s+(?:in)?to|break\s+down|overview\s+of|understand|"
    r"what(?:'s|s|\s+is)\s+(?:in|inside))"
    r"(?:\s+(?:me|my|mine|our|the|a|an))*"
    r"\s+(?P<name>[\w][\w .'&()\-]{0,60}?)"
    r"\s+(?:folder|folders|directory|dir|project)\b",
    re.IGNORECASE,
)


def machine_folder_audit_intent(text: str) -> str | None:
    """Return the folder NAME to read+overview when the ask is to analyse a named local folder.

    Fires when an analysis verb (audit/analyse/review/go through/what's in ...) targets a named
    folder. Suppressed for create/remote asks. Returns the name only; the caller resolves it to a
    real path and, if found, answers with a grounded folder overview -- otherwise it falls through
    to the plain find_folder locator.
    """
    lowered = normalize_current_scope_reference(text)
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered) or _FOLDER_CREATE_RE.search(lowered):
        return None
    if any(marker in lowered for marker in _FOLDER_SEARCH_REMOTE_MARKERS):
        return None
    if refers_to_current_scope(lowered):
        return None  # "this/current/local folder" = the bound scope; the project-aware path handles it
    if not _FOLDER_ANALYSIS_VERB_RE.search(lowered):
        return None
    match = _FOLDER_AUDIT_RE.search(lowered) or _FOLDER_NAMED_RE.search(lowered)
    if not match:
        return None
    name = " ".join(str(match.group("name") or "").split()).strip(" .'\"-")
    name_words = [word for word in name.split() if word not in _FOLDER_NAME_STOPWORDS]
    if not name_words:
        return None
    clean_name = " ".join(name_words)
    if all(word in _FOLDER_NAME_FILLER_WORDS for word in clean_name.split()):
        return None
    return clean_name if len(clean_name) >= 2 else None


def resolve_named_folder_under_home(name: str) -> str | None:
    """Resolve a folder NAME to a real path under the user's common roots, else ``None``.

    Separator-insensitive ("token hunter" == "token-hunter"), checks Desktop first (people say "on
    desktop"), then Documents/Downloads/home/Shared. Direct children only -- fast, no deep scan; a
    miss falls through to the broad find_folder search. Read-only.
    """
    import os
    from pathlib import Path

    needle = re.sub(r"[\s_\-]+", "", str(name or "").strip().lower())
    if len(needle) < 2:
        return None
    # Honour explicit profile variables first so Windows-like environments can be exercised
    # consistently on every host. ``expanduser`` remains the final fallback for normal runtime
    # resolution when neither variable is present.
    home = Path(
        os.environ.get("HOME")
        or os.environ.get("USERPROFILE")
        or os.path.expanduser("~")
    )
    roots = [home / "Desktop", home / "Documents", home / "Downloads", home, Path("/Users/Shared")]
    best: str | None = None
    best_rank = 1_000_000
    for rank, root in enumerate(roots):
        try:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                try:
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                except OSError:
                    continue
                norm = re.sub(r"[\s_\-]+", "", child.name.lower())
                # EXACT (separator-insensitive) match ONLY. A raw substring match let a short common
                # word autoselect an unrelated folder ("work" -> w2l_work). A non-exact needle now
                # returns None and falls through to the broad find_folder locator, which LISTS the
                # candidates instead of silently picking one.
                if norm == needle:
                    if rank < best_rank:  # Desktop beats deeper roots
                        best_rank, best = rank, str(child)
        except OSError:
            continue
    return best


# A local/machine FACT question must be answered by a real tool, never by model prose. This
# classifier flags such a question so the runtime can refuse to fabricate when no tool ran
# (honest blocker instead of a guessed drive size / folder list). It intentionally requires a
# this-machine framing so general-knowledge questions ("what is a hard drive?") are NOT caught.
#: A drive letter is a single letter NAMING a drive ("C drive"). English has exactly two
#: one-letter words, and both are false letters here: "a drive" is the indefinite article
#: (a common noun, the determiner this module's own rules already respect) and "I drive" is the
#: pronoun plus the verb ("can I drive from Rome to Paris in one day?" was read as a question
#: about this machine's drives -- measured 2026-08-19, the Fable E1 case). The negative lookahead
#: is closed-class English, not drive vocabulary.
_DRIVE_LETTER_RE = r"(?!\ba\s)(?!\bi\s)\b[a-z]\s+drive\b"
_LOCAL_FACT_THIS_MACHINE_RE = re.compile(
    r"(?:\bmy\b|\bmine\b|this pc|this machine|this computer|this laptop|this host|"
    rf"\bdo i have\b|\bdoes my\b|\bon this\b|\bon\s+[a-z]:|\b[a-z]:\\|{_DRIVE_LETTER_RE}|\bmy drive\b)",
    re.IGNORECASE,
)
_LOCAL_FACT_QUESTION_RE = re.compile(
    r"(?:\?\s*$|^\s*(?:what|whats|what's|hows|how|where|which|is|are|does|do|tell me|show me|list|check|find|give me)\b)",
    re.IGNORECASE,
)
_LOCAL_FACT_LARGEST_RE = re.compile(
    # "biggest/largest/top … files/folders", and the reverse order too ("folders and files … top 5"),
    # plus "space hogs / space eaters".
    # "heaviest / hugest / fattest / bulkiest file" is the same question in plainer words
    # (measured 2026-09-07: "find the heaviest file on my desktop" reached no tool at all).
    r"\b(?:biggest|largest|heaviest|hugest|fattest|bulkiest|top)\b[\w\s,'-]*\b(?:file|files|folder|folders)\b"
    r"|\b(?:file|files|folder|folders)\b[\w\s,'-]*\b(?:biggest|largest|heaviest|hugest|fattest|bulkiest|top\s+\d+)\b"
    r"|\bspace\s+(?:hogs?|eaters?|guzzlers?)\b"
    # "what's eating up my disk space" / "what is hogging my storage" asks for the biggest
    # consumers, not the free-space total it was answered with (measured 2026-09-07).
    r"|\b(?:eating|hogging|filling|chewing)\s+(?:up\s+)?(?:all\s+)?(?:of\s+)?(?:my\s+|the\s+)?"
    r"(?:(?:disk\s+|drive\s+|hard\s+drive\s+|storage\s+|ssd\s+)?(?:space|storage)|disk|drive|ssd|hard\s+drive)\b",
    re.IGNORECASE,
)
# Advisory / how-to / definitional framings are NOT fact lookups about this machine, so they
# must not be gated as tool-required (a "how should I organise my folders?" is a chat answer).
_LOCAL_FACT_ADVISORY_RE = re.compile(
    r"\b(?:should i|how do i|how can i|how to|how should|what should|help me|recommend|"
    r"advice|explain|what is a|what's a|difference between)\b",
    re.IGNORECASE,
)
_LOCAL_FACT_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("the display", re.compile(r"\b(?:screen|resolution|display|displays|monitor|monitors)\b", re.IGNORECASE)),
    ("your drives", re.compile(r"\b(?:drive|drives|disk|disks|free\s+space|storage|volume|volumes|partition|partitions)\b", re.IGNORECASE)),
    ("a local folder or file search", re.compile(r"\b(?:folder|folders|directory|directories)\b", re.IGNORECASE)),
    ("running processes", re.compile(r"\b(?:processes|running programs|task manager)\b", re.IGNORECASE)),
    ("this machine's hardware", re.compile(r"\b(?:cpu|gpu|ram|cores|vram|specs?)\b", re.IGNORECASE)),
)
_LOCAL_FACT_MOST_SPACE_RE = re.compile(
    # "taking up the most space", but also "takes the most space", "uses / eats / hogs / consumes /
    # fills the most (disk) space" — the verb may be bare (no "up") and any tense.
    r"\b(?:tak(?:e|es|ing)|us(?:e|es|ing)|eat(?:s|ing)?|hogg?(?:s|ing)?|"
    r"consum(?:e|es|ing)|gobbl(?:e|es|ing)|fill(?:s|ing)?)\s+"
    r"(?:up\s+)?(?:the\s+)?most\s+(?:disk\s+|drive\s+|storage\s+)?space\b",
    re.IGNORECASE,
)
# The subject of a "most space" question can be files/folders, not just a named drive.
_SPACE_SUBJECT_RE = re.compile(r"\b(?:file|files|folder|folders|directory|directories)\b", re.IGNORECASE)


def _is_local_most_space_question(text: str, lowered: str) -> bool:
    if not _LOCAL_FACT_MOST_SPACE_RE.search(lowered):
        return False
    return bool(
        machine_disk_scope(text)
        or _LOCAL_FACT_THIS_MACHINE_RE.search(lowered)
        or any(re.search(rf"\b{noun}\b", lowered) for noun in _DISK_NOUNS)
        or _SPACE_SUBJECT_RE.search(lowered)
    )


_DISPLAY_RE = re.compile(r"\b(?:screen|resolution|display|displays|monitor|monitors)\b", re.IGNORECASE)
# General-knowledge display questions ("resolution of a 4k monitor") are NOT about this host.
_DISPLAY_GENERAL_RE = re.compile(r"\b(?:of a|of an|difference between|what is a|what's a|whats a|typical|standard)\b", re.IGNORECASE)


def bound_chat_attachment_present(source_context: dict | None) -> bool:
    """True when the turn carries ingress-bound chat-attachment material.

    The one attachment-authority check every bound-material routing decision reads:
    the door stamped an attachment turn AND ingress bound at least one typed
    ``chat_attachment`` evidence row to it. Filenames typed in the request text are
    not binding -- provenance is context, never a routing warrant.
    """
    context = source_context or {}
    return bool(context.get("attachment_turn_id")) and any(
        isinstance(item, dict)
        and item.get("origin") == "chat_attachment"
        and item.get("attachment_id")
        for item in context.get("external_evidence") or []
    )


def bound_attachment_names(source_context: dict | None) -> frozenset[str]:
    """Display names of the attachments ingress bound to this turn, case-folded.

    The name accessor beside ``bound_chat_attachment_present``: a routing lane that
    resolves a FILE BY NAME (the disk PDF lane) stands down when the name it resolved
    is the name of material already bound to the turn. Only ingress-bound rows count;
    a client-supplied evidence item is not an attachment.
    """
    names: set[str] = set()
    for item in (source_context or {}).get("external_evidence") or []:
        if (
            isinstance(item, dict)
            and item.get("origin") == "chat_attachment"
            and item.get("attachment_id")
        ):
            name = str(item.get("name") or "").strip().casefold()
            if name:
                names.add(name)
    return frozenset(names)


def machine_display_intent(
    text: str, *, source_context: dict | None = None, whole_turn: bool = False
) -> str | None:
    """Map a this-host display question ("what is my screen resolution?") to
    ``machine.display_inspect``, else None. General-knowledge display questions and
    mutations are excluded so only real host inspection is routed here. A turn carrying
    ingress-bound reference material is not a this-host question by default: "screen"
    can belong to that material. Leave that ambiguity to the attachment-aware model
    rather than silently inspecting this machine. An explicitly host-owned question
    remains actionable. A whole-turn answer must additionally cover the entire request;
    mixed host/material requests leave the host step to the workflow planner. Both
    callers use this authority; the evidence is already read and bound by ingress.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered):
        return None
    if not _DISPLAY_RE.search(lowered):
        return None
    if _DISPLAY_GENERAL_RE.search(lowered):
        return None
    if not _LOCAL_FACT_QUESTION_RE.search(lowered):
        return None
    if bound_chat_attachment_present(source_context):
        if not _host_owned_topic(lowered, _DISPLAY_RE):
            return None
        if whole_turn and not _topic_is_the_whole_message(lowered, _DISPLAY_RE):
            return None
    return "machine.display_inspect"


# ---------------------------------------------------------------------------------------------
# Live host state vs. the static spec sheet
#
# `machine.inspect_specs` answers exactly one question: what hardware IS this. "What is eating my
# CPU", "what's the uptime", "what's the battery at" and "is this a laptop" are questions about
# what the machine is DOING or how it is BUILT right now, and a block that happens to print the
# words CPU and RAM does not answer any of them. Each gets the tool that measures it, decided by
# what the sentence asks for rather than by which topic word it happens to contain.
# ---------------------------------------------------------------------------------------------

# Phrasings the operator action lane already owns end to end (parse -> gate -> audit log). Listed
# once, here, because two lanes matching the same words from two private lists is how a working
# answer silently changes hands.
OPERATOR_OWNED_PROCESS_PHRASES = (
    "what processes",
    "top processes",
    "process offenders",
    "startup offenders",
    "memory hogs",
    "cpu hogs",
)
OPERATOR_OWNED_SERVICE_PHRASES = (
    "what services",
    "running services",
    "service offenders",
    "startup services",
    "startup items",
    "launch agents",
)
OPERATOR_OWNED_MACHINE_PHRASES = OPERATOR_OWNED_PROCESS_PHRASES + OPERATOR_OWNED_SERVICE_PHRASES

# The resource is the OBJECT of the consumption, never merely a word in the sentence: the verb has
# to come first ("eating my CPU"), or the resource has to be paired with a load noun ("CPU usage").
# Order matters — "what GPU am I using?" names the resource first and is a hardware question, so it
# must not be read as a live-load one.
_RESOURCE_PRESSURE_RE = re.compile(
    r"\b(?:eat(?:s|ing)?|us(?:e|es|ing)|hogg?(?:s|ing)?|chew(?:s|ing)?|consum(?:e|es|ing)|"
    r"burn(?:s|ing)?|max(?:es|ing)?\s+out|pegg?(?:s|ing)?|thrash(?:es|ing)?|spik(?:e|es|ing))\b"
    r"[^.?!\n]{0,30}?\b(?:cpu|gpu|ram|memory|processor)\b"
    r"|\b(?:cpu|gpu|ram|memory|processor)\s+(?:usage|utili[sz]ation|load|pressure|hog|hogs)\b"
    r"|\bmost\s+(?:cpu|gpu|ram|memory)\b",
    re.IGNORECASE,
)
# "which apps are running", "what programs are open" — the process list, asked without the word
# "process". The operator-owned phrasings above are subtracted before this is consulted.
_RUNNING_PROCESSES_RE = re.compile(
    r"\b(?:running|active|open)\s+(?:processes|programs|apps|applications)\b"
    # "open TO" is the availability sense, not the running one. "What programs are open to graduates
    # without a technical degree" is listed in the comment above as a sentence this lane once
    # answered with a process table; it still was, because `open` matched here and the shared gate
    # reads the sentence as a genuine question. A degree programme being open to applicants is not a
    # program being open on this machine, and the preposition is what says so.
    r"|\b(?:processes|programs|apps|applications)\b[^.?!\n]{0,20}?\b(?:running|active)\b"
    r"|\b(?:processes|programs|apps|applications)\b[^.?!\n]{0,20}?\bopen\b(?!\s+to\b)",
    re.IGNORECASE,
)
# The same question with the noun dropped: "show me what's running". Both patterns above require one
# of processes/programs/apps/applications to be present, and people leave it out constantly.
# Measured live 2026-07-31: "show me what's running" reached no family, spent 40s in the model, and
# came back as a shell snippet the user was expected to run themselves -- ```ps -ef | grep -v grep```
# -- while the process table was 0.1s away. The model had understood the question perfectly; only
# the route was missing.
#
# "running" with no noun is idiomatic English for a dozen other things, so the ask must END there
# (optionally after a this-host or right-now tail). That is what keeps "what's running late",
# "what's running through your mind", "what's running in docker" and "what's running on port 8080"
# out: each continues into an object that is not this machine.
_BARE_RUNNING_PROCESSES_RE = re.compile(
    r"\b(?:what(?:'s|s|\s+is)|everything(?:\s+that(?:'s|s)?)?|anything(?:\s+that(?:'s|s)?)?"
    r"|everything\s+thats|anything\s+thats|all(?:\s+that(?:'s|s)?)?)"
    r"\s+(?:all\s+|currently\s+|actually\s+|even\s+|is\s+)?running\b"
    r"(?:\s+(?:right\s+now|now|currently|at\s+the\s+moment|at\s+present"
    r"|on\s+(?:this|my)\s+(?:machine|mac|computer|laptop|pc|system|box|host)"
    r"|on\s+here|here))?"
    r"\s*[?.!]*$",
    re.IGNORECASE,
)
# A RANKING of processes, asked with a superlative instead of a load verb: "top 5 processes by cpu",
# "show me the heaviest apps by ram". Measured live 2026-07-31: "top 5 processes by cpu please" spent
# 75s in the model and returned nothing (the operator lane matches the literal substring "top
# processes", which a count in the middle breaks), and "show me the heaviest apps by ram" was
# answered with the hardware SPEC SHEET -- "RAM: 24.0 GiB" -- because no process family claimed it and
# the word "ram" was enough for the specs family.
#
# A superlative over "apps" is not on its own a question about this host ("the biggest apps on the app
# store"), so this form additionally requires a resource word, which is what the ranking is BY.
_PROCESS_RANKING_RE = re.compile(
    r"\b(?:top|heaviest|biggest|largest|hungriest|greediest|worst|busiest|"
    r"most\s+demanding|most\s+active|most\s+intensive)\s+"
    r"(?:\d+\s+)?(?:\w+\s+){0,1}?"
    r"(?:process|processes|program|programs|app|apps|application|applications)\b",
    re.IGNORECASE,
)
_PROCESS_RESOURCE_WORD_RE = re.compile(r"\b(?:cpu|gpu|ram|memory|processor)\b", re.IGNORECASE)
# A process MISBEHAVING is the same question as a process consuming: "any processes going crazy right
# now" names no resource at all, and spent 60s in the model returning nothing.
_PROCESS_MISBEHAVIOUR_RE = re.compile(
    r"\b(?:process|processes|program|programs|app|apps|application|applications)\b[^.?!\n]{0,25}?"
    r"\b(?:going\s+crazy|going\s+nuts|going\s+haywire|misbehaving|acting\s+up|playing\s+up|"
    r"runaway|run\s+away|spiking|stuck|hung|hanging|out\s+of\s+control|pegged|thrashing)\b",
    re.IGNORECASE,
)
# A storage framing belongs to the disk tools ("what's eating my disk space" is a disk question,
# not a process one), so this lane stands down whenever the sentence names storage.
_STORAGE_NOUN_RE = re.compile(
    r"\b(?:disk|disks|drive|drives|storage|space|volume|volumes|partition|partitions)\b",
    re.IGNORECASE,
)
# What a process ranking has to be ABOUT before it can answer the sentence. See the gate in
# machine_live_load_intent for why this family gets no possessive anchor.
_PROCESS_TOPIC_RE = re.compile(
    r"\b(?:process|processes|program|programs|app|apps|application|applications|task|tasks|"
    r"cpu|gpu|ram|memory|processor|usage|load|running|open|active)\b",
    re.IGNORECASE,
)


# A request to produce or teach, not to measure. "write a function that reports gpu usage" and
# "explain how cpu cache works" are about the subject, not about this host's current state.
_AUTHORING_NOT_MEASURING_RE = re.compile(
    r"\b(?:write|draft|generate|create|make|build|code|implement|show\s+me\s+how|"
    r"explain|describe|teach|document|sketch)\b[^.?!\n]{0,40}?"
    r"\b(?:function|script|program|code|snippet|class|method|module|command|query|"
    r"example|tutorial|guide|works?|working)\b",
    re.IGNORECASE,
)


def machine_live_load_intent(text: str) -> str | None:
    """Map a "what is consuming this machine right now" question to ``machine.list_processes``.

    Answers the CPU/RAM-pressure and what-is-running questions with a measurement of what is
    actually running. Storage pressure, operator-owned phrasings and mutations are left to the
    lanes that already own them.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered):
        return None
    if any(phrase in lowered for phrase in OPERATOR_OWNED_MACHINE_PHRASES):
        return None
    if _STORAGE_NOUN_RE.search(lowered):
        return None
    # Asking for something to be WRITTEN or EXPLAINED about resource usage is not asking to measure
    # it. Caught when two independently-authored fixes were merged: "write a function that reports
    # gpu usage" matched on the word "usage" and was answered by running machine.list_processes on
    # the user's box. A coding request answered with a process table is the same over-claiming shape
    # this file has now been narrowed for several times - a topic word deciding a turn.
    if _AUTHORING_NOT_MEASURING_RE.search(lowered):
        return None
    # `process`, `program` and `app` are among the most overloaded nouns in English, and the two
    # patterns below only test that one of them sits near a load word. Measured live 2026-07-30:
    # "Our deployment process uses a lot of memory on the CI box, not on my laptop", "Write a blog
    # post about which apps are running in the background on modern phones" and "My manager asked
    # what programs are open to graduates without a technical degree" were each answered, in about
    # 0.2s, with a ranking of the processes on the operator's own Mac.
    #
    # No possessive anchor here, deliberately: "our deployment process" and "my hiring process"
    # are possessives that own the word without owning anything on this host.
    # A superlative ranking of processes BY a resource ("top 5 processes by cpu please") is this-host
    # evidence in the same way a named drive letter is for the disk family: nothing but a live machine
    # is described that way. It is passed as the family's own anchor rather than loosening the shared
    # gate, which reads it as an imperative with no question word and declines it. `also_anchored` is
    # consulted only after the produce/teach and general-knowledge guards, so "write a script that
    # lists the top processes by cpu" is still an authoring request, not a measurement.
    ranked_by_resource = bool(
        _PROCESS_RANKING_RE.search(lowered) and _PROCESS_RESOURCE_WORD_RE.search(lowered)
    )
    if not asks_runtime_for_a_fact(text, _PROCESS_TOPIC_RE, also_anchored=ranked_by_resource):
        return None
    if (
        _RESOURCE_PRESSURE_RE.search(lowered)
        or _RUNNING_PROCESSES_RE.search(lowered)
        or _BARE_RUNNING_PROCESSES_RE.search(lowered)
        or (_PROCESS_RANKING_RE.search(lowered) and _PROCESS_RESOURCE_WORD_RE.search(lowered))
        or _PROCESS_MISBEHAVIOUR_RE.search(lowered)
    ):
        return "machine.list_processes"
    return None


def asks_about_running_processes(text: str) -> bool:
    """True when the sentence is ABOUT running processes, whichever lane ends up answering it.

    ``machine_live_load_intent`` deliberately declines the phrasings the operator lane owns, so it
    cannot double as "is this a process question". The routing probes need that broader reading: a
    message no family claims is treated as a near-miss and handed to the arbiter, and paying a
    model call to rediscover a route priority order already gets right is a real cost -- measured
    on the live daemon at 0.2s -> 6.1s for "list the top processes by cpu usage".
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if machine_live_load_intent(text) is not None:
        return True
    return any(phrase in lowered for phrase in OPERATOR_OWNED_PROCESS_PHRASES)


_CPU_SORT_RE = re.compile(r"\b(?:cpu|gpu|processor)\b", re.IGNORECASE)


def machine_process_sort(text: str) -> str:
    """``"cpu"`` when the question is about processor load, ``"memory"`` otherwise.

    A CPU question answered with a memory ranking is still the wrong answer, just a subtler one.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    return "cpu" if _CPU_SORT_RE.search(lowered) else "memory"


_UPTIME_RE = re.compile(
    r"\buptime\b"
    # "how long has X been up" only counts when X is this host — "how long has the build been
    # running" is a question about a build, and the subject is the only thing that says so.
    r"|\bhow\s+long\s+(?:has|have|had|is|'s)\s+"
    r"(?:this\s+(?:machine|computer|pc|mac|laptop|desktop|host|system|box)|it|"
    r"the\s+(?:machine|computer|pc|mac|host|system|box))\b"
    r"[^.?!\n]{0,30}?\b(?:up|on|running|awake|booted|been)\b"
    r"|\b(?:since|last)\s+(?:the\s+)?(?:reboot|restart|boot|boot-?up)\b"
    r"|\bwhen\s+(?:did|was)\b[^.?!\n]{0,30}?\b(?:reboot|restart|rebooted|restarted|booted|last\s+boot)\b",
    re.IGNORECASE,
)
_BATTERY_RE = re.compile(
    r"\bbatter(?:y|ies)\b|\bcharge\s+level\b|\bplugged\s+in\b|\bon\s+ac\b|\bpower\s+source\b",
    re.IGNORECASE,
)
# "is this a laptop or a desktop" — the chassis, asked as a question about THIS machine. Narrow on
# purpose: the bare words "laptop" and "desktop" appear in plenty of sentences that ask nothing.
_CHASSIS_QUESTION_RE = re.compile(
    r"\bis\s+(?:this|it)\s+(?:a\s+|an\s+)?(?:laptop|desktop|notebook|macbook|imac|mac\s?mini|mac\s?studio)\b"
    r"|\b(?:laptop|notebook)\s+or\s+(?:a\s+)?(?:desktop|imac|tower)\b"
    r"|\bdesktop\s+or\s+(?:a\s+)?(?:laptop|notebook)\b"
    r"|\bam\s+i\s+(?:on|using)\s+(?:a\s+)?(?:laptop|desktop|notebook)\b",
    re.IGNORECASE,
)
# General-knowledge framings ("how long does a MacBook battery last?") are not a read of this host.
# The indefinite article is the tell: "does a macbook" asks about macbooks, "does my macbook" asks
# about this one.
_HOST_STATE_GENERAL_RE = re.compile(
    r"\b(?:of a|of an|difference between|what is a|what's a|whats a|typical|standard|"
    r"how do i|how to|should i|explain|do(?:es)? an?)\b",
    re.IGNORECASE,
)
# What a live host read has to be ABOUT, and the narrower subset a possessive may anchor on.
_HOST_STATE_TOPIC_RE = re.compile(
    r"\b(?:uptime|battery|batteries|charge|charging|charged|plugged|power|boot|booted|reboot|"
    r"rebooted|restart|restarted|laptop|notebook|desktop|macbook|imac|machine|computer|pc|"
    r"host|system)\b",
    re.IGNORECASE,
)
_HOST_STATE_OWNED_TOPIC_RE = re.compile(
    r"\b(?:uptime|battery|laptop|notebook|macbook|imac|machine|computer|pc|host)\b",
    re.IGNORECASE,
)


# A question about this host is short and direct. The longest genuine phrasing measured is
# "how long has this machine been up for" (8 words); the brief that wrongly reached this lane
# was 49.
_MAX_HOST_STATE_QUESTION_WORDS = 20


def machine_host_state_intent(text: str) -> str | None:
    """Map an uptime / battery / laptop-or-desktop question to ``machine.host_state``, else None.

    None of these live in the spec sheet: uptime and battery change minute to minute, and chassis
    is not one of the fields it prints. Each was previously answered either by the canned specs
    block or by the model guessing a number.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered):
        return None
    if _HOST_STATE_GENERAL_RE.search(lowered):
        return None
    # _HOST_STATE_GENERAL_RE already carves out the general-knowledge framings. It says nothing
    # about a request to WRITE something on the subject, and `_BATTERY_RE` is a bare word match.
    # Measured live 2026-07-30 on a71214e: "Write me a short LinkedIn post about which apps are
    # using battery on modern laptops." was answered "Current state of this host:" in 0.9s.
    if not asks_runtime_for_a_fact(text, _HOST_STATE_TOPIC_RE, owned_topic_re=_HOST_STATE_OWNED_TOPIC_RE):
        return None
    # A question about THIS host is short and direct: "is it on battery?", "how long has it been
    # up?". The carve-out above is a vocabulary rule and vocabulary keeps losing here -- the
    # LinkedIn case is recorded in the comment above, and on 2026-08-06 a 49-word research brief
    # slipped it entirely:
    #
    #   "What is the best-selling battery-electric car model of all time worldwide? Compare the
    #    Tesla Model 3 and Nissan Leaf ... Identify the most common battery-capacity configuration"
    #
    # `_HOST_STATE_GENERAL_RE` returned False on that (it fires on the longer phrasing but not this
    # one), `_BATTERY_RE` matched the word inside "battery-electric", and the answer was this Mac's
    # uptime and chassis. The same shape has now been fixed three times with words; length is the
    # discriminator that a phrase nobody anticipated cannot defeat. Generous at 20 -- the longest
    # genuine phrasing measured is "how long has this machine been up for" (8 words).
    if len(lowered.split()) > _MAX_HOST_STATE_QUESTION_WORDS:
        return None
    if _UPTIME_RE.search(lowered) or _BATTERY_RE.search(lowered) or _CHASSIS_QUESTION_RE.search(lowered):
        return "machine.host_state"
    return None


# A concrete filesystem path inside a sentence: absolute, home-rooted, or ./ prefixed. Trailing
# sentence punctuation is trimmed so "in /a/b?" yields "/a/b".
_EXPLICIT_PATH_RE = re.compile(r"""(?:~|\.{1,2})?/[^\s'"`,;]+""")
# A URL contains `//`, which the path pattern above reads as an absolute posix path: measured
# 2026-07-30, `explicit_path_in("fetch https://example.com ...")` returned `//example.com`, and the
# machine lane refused the "path" for being outside the home folders. Web addresses are removed
# before any path matching, so a sentence naming BOTH still finds its real local path. The input
# normalizer keeps URLs intact now, which is what makes this pattern reliable here.
_URL_SPAN_RE = re.compile(r"""\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s`"']+""")


def _without_urls(text: str) -> str:
    return _URL_SPAN_RE.sub(" ", str(text or ""))
# Phrasings that ask what is INSIDE a place, as opposed to where that place is. A request that
# says "what's in X" is answered by a listing; answering with X's location is a correct fact and
# the wrong reply.
_CONTENTS_QUESTION_RE = re.compile(
    r"\b(?:what(?:'|’)?s?\s+(?:is\s+)?(?:in|inside|within)|what\s+files|which\s+files|"
    r"contents?\s+of|inside|list|listing|peek\s+in|look\s+in|show\s+me\s+what|"
    r"name\s+every|tell\s+me\s+what(?:'|’)?s?)\b",
    re.IGNORECASE,
)


def _extend_path_over_spaces(text: str, candidate: str, end: int) -> str:
    """Grow ``candidate`` across the spaces that follow it while the longer path REALLY EXISTS.

    Folder names contain spaces and sentences do too, and nothing in the text says which is which:
    "the contents of ~/Downloads/Telegram Bot for me?" gives the same character sequence as a path
    followed by three ordinary words. The disk settles it -- and only the disk can.

    Measured on the deployed build 2026-07-30: that phrasing was cut at the space, and the answer
    was "Local directory `~/Downloads/Telegram` does not exist." about a folder that does exist.
    A confident negative about a real folder is the worst shape this can fail in.

    Only consulted when the plain match does NOT resolve, so a path that already works never pays
    for this and never changes.
    """
    from pathlib import Path

    def _resolves(value: str) -> bool:
        try:
            return Path(value).expanduser().exists()
        except (OSError, ValueError, RuntimeError):
            return False

    if _resolves(candidate):
        return candidate
    tail = str(text or "")[end:]
    grown = candidate
    # A path is a handful of words at most; the bound keeps a rambling sentence from being scanned
    # word by word into an absurd candidate.
    for word in tail.split(" ")[1:7]:
        word = word.strip()
        if not word:
            break
        grown = f"{grown} {word}".rstrip(".,;:!?)")
        if _resolves(grown):
            return grown
    return candidate


def explicit_path_in(text: str) -> str:
    """The first concrete path in the user's own words, or '' when they named no path."""
    cleaned = _without_urls(text)
    match = _EXPLICIT_PATH_RE.search(cleaned)
    if match is None:
        return ""
    candidate = match.group(0).rstrip(".,;:!?)")
    if len(candidate) <= 1:
        return ""
    return _extend_path_over_spaces(cleaned, candidate, match.end())


_QUOTED_PATH_VALUE_RE = re.compile(r"""(["'`])((?:~|\.{1,2})?/[^"'`\n]*|[A-Za-z]:\\[^"'`\n]*)\1""")
_WINDOWS_PATH_TOKEN_RE = re.compile(r"""\b[A-Za-z]:\\[^\s'"`,;]*""")
# `explicit_path_in`'s shape, anchored: a slash inside a word ("and/or", "Europe/Athens",
# "standup/meeting") joins words; it does not start a path.
_RECOGNITION_PATH_RE = re.compile(r"(?<!\w)" + _EXPLICIT_PATH_RE.pattern)


def text_without_paths(text: str) -> str:
    """The sentence with every URL and filesystem path it names replaced by the word ``path``.

    A path NAMES a place; its components are never words of the request. Measured on the calendar/
    notes build (revision-5 neighbour run): under a working directory named `.../02-calendar-notes/...`,
    'find disk bloat in "<that directory>"' parsed as a NOTES search and 'move "<dir>/report.txt" to
    "<dir>/archive"' as a CALENDAR move, because recognizers read `notes` and `calendar` out of the
    path. Recognition reads this text; extraction keeps reading the original, so a path argument is
    never altered by it.

    A quoted value that starts like a path is replaced whole -- the user's quotes are what delimit a
    path with spaces in it -- and the quote characters stay, so "propose \"...\"" still reads as a
    quoted proposal. The same path shapes as `explicit_path_in`, anchored so a slash between two words
    keeps both words, and without its disk probe: recognizing a request must not depend on what exists
    on this machine.
    """
    value = str(text or "")
    value = _QUOTED_PATH_VALUE_RE.sub(lambda match: f"{match.group(1)}path{match.group(1)}", value)
    value = _URL_SPAN_RE.sub(" path ", value)
    value = _WINDOWS_PATH_TOKEN_RE.sub(" path ", value)
    return _RECOGNITION_PATH_RE.sub(" path ", value)


def asks_about_contents(text: str) -> bool:
    """True when the sentence asks what is INSIDE a place rather than where the place is."""
    return _CONTENTS_QUESTION_RE.search(str(text or "")) is not None


# Stricter than `asks_about_contents`, and deliberately so. That helper repairs an arbiter pick
# that has already been made, where a loose read costs nothing. This one CLAIMS a turn outright,
# so it only fires when the listing is what the sentence asks for: "what files", "what's in",
# "contents of". A bare preposition does not count -- "take a look inside <path> and tell me what
# the billing helper does" wants the folder explained, and answering it with a list of filenames
# is the same class of miss as answering it with the whole Desktop.
_FILE_LISTING_ASK_RE = re.compile(
    r"\bwhat\s+files?\b|\bwhich\s+files?\b"
    r"|\bwhat(?:'|’)?s?\s+(?:is\s+)?(?:in|inside|within)\b"
    r"|\bcontents?\s+of\b"
    r"|\blist\s+(?:the\s+|all\s+(?:the\s+)?|every\s+)?(?:files?|contents?|entries|everything)\b"
    r"|\bshow\s+me\s+(?:the\s+)?(?:files?|contents?)\b"
    r"|\bname\s+every\s+file\b"
    # "anything in ~/Desktop/nonexistent-xyz?" and "show me what /private/var/db contains" are
    # contents questions about a path the user typed. Neither matched, so the read gate stood the
    # machine lane down and both were answered by a model -- 49s of cloud for "I wasn't able to
    # turn that into a completed action", and 64s for "I couldn't map that cleanly to a real
    # action". Both had one-line true answers waiting (the folder is not there; that path is
    # outside the readable lane). Measured on the deployed build 2026-07-30.
    r"|\b(?:anything|any\s+files?)\s+(?:in|inside|under)\b"
    r"|\bwhat\b[^.\n]{0,60}?\bcontains?\b"
    # "ls ~/Applications/vool" is a listing ask spelled the way people who live in a terminal
    # spell it, and it was reaching no lane at all -- measured on the deployed build 2026-07-30,
    # it came back as a two-word invented directory listing that matched neither that path nor its
    # parent. The lookahead is the whole guard: `ls` only counts when a path starts right after
    # it, so prose that merely contains the word cannot claim a turn.
    r"|\bls\s+(?=[~/])",
    re.IGNORECASE,
)


def machine_path_listing_intent(text: str) -> str | None:
    """The path to list when the user asks what files are inside a path they typed out, else None.

    "What files are inside ~/Desktop/my-budget-folder?" names its own answer. Left to keyword
    routing it was claimed by whichever family matched a word in it -- and the answer depended on
    which lane won that run. A typed path plus an explicit ask for its contents is the most
    specific thing in the sentence, so it decides.
    """
    if _MACHINE_DIAG_WRITE_RE.search(" ".join(str(text or "").strip().lower().split())):
        return None
    if _FILE_LISTING_ASK_RE.search(str(text or "")):
        candidate = explicit_path_in(text) or None
        # A FILE is read, not listed. Without this the strict route handed "show me whats in
        # ~/Desktop/vool-acceptance-probe/haiku.txt" to list_directory, which answered "Local
        # directory ... does not exist" about a file sitting right there. Measured on the deployed
        # build 2026-07-30, on two separate phrasings.
        return None if candidate and _is_existing_file(candidate) else candidate
    return _typed_directory_content_ask(text)


def _is_existing_file(candidate: str) -> bool:
    from pathlib import Path

    try:
        return Path(candidate).expanduser().is_file()
    except (OSError, ValueError, RuntimeError):
        return False


# The file half of the same principle the directory route uses: the phrasebook has an edge, the
# disk does not. Every one of "show me whats in <file>", "whats written in <file>", "print the
# contents of <file>", "i want to see <file>" and "cat <file>" missed the marker list on the
# deployed build 2026-07-30, and each returned either a lie about the file not existing or 60
# seconds of "I couldn't map that cleanly to a real action".
_TYPED_FILE_CONTENT_CUE_RE = re.compile(
    r"\b(?:read|reads|open|opens|cat|print|show|see|view|display|say|says|said|written|contents?|"
    r"inside|quote|text)\b"
    r"|\bwhat(?:'|’)?s?\s+(?:is\s+)?(?:in|inside|written)\b",
    re.IGNORECASE,
)
# Verbs asking for something to be DONE to the file rather than for the file itself.
_TYPED_FILE_OTHER_INTENT_RE = re.compile(
    r"\b(?:delete|remove|move|rename|copy|create|append|overwrite|save|download|upload|"
    r"run|execute|compile|install|summar(?:y|ise|ize)|translate|refactor|rewrite)\b",
    re.IGNORECASE,
)


def machine_file_read_path(text: str) -> str | None:
    """The path to READ when the user typed one that is a real file and asked for its contents."""

    raw = str(text or "")
    if not _TYPED_FILE_CONTENT_CUE_RE.search(raw):
        return None
    if _TYPED_FILE_OTHER_INTENT_RE.search(raw):
        return None
    candidate = explicit_path_in(raw)
    if not candidate or not _is_existing_file(candidate):
        return None
    return candidate


# Everything above this line is a surface form someone thought of. Five rounds of live phrasings
# kept landing just outside it -- "whatre the files under <path>", "run a dir on <path>", "just the
# folders in <path> please", "whats sat in <path> these days" -- and each miss cost 60-80 seconds
# and came back "I couldn't map that cleanly to a real action" for a folder holding three files.
# Adding four more literals would only move the edge.
#
# So this second route asks the disk instead of the phrasebook. If the user typed a path, that path
# is not an existing FILE (a file belongs to read_file), and the sentence asks about its contents
# without asking for something else to be done to them, then listing it is the only reading left.
_TYPED_DIR_CONTENT_CUE_RE = re.compile(
    r"\b(?:files?|folders?|dirs?|director(?:y|ies)|entr(?:y|ies)|contents?|items?|stuff|everything|"
    r"anything)\b"
    r"|\bwhat(?:'|’)?s?\s+(?:is\s+|are\s+|re\s+)?(?:in|inside|under|sat|sitting|stored|kept|there)\b"
    r"|\b(?:ls|dir)\b",
    re.IGNORECASE,
)
# Verbs that ask for something OTHER than a listing of the same folder. A listing is a poor answer
# to any of them, so the fallback declines and the normal lanes decide.
_TYPED_DIR_OTHER_INTENT_RE = re.compile(
    r"\b(?:summar(?:y|ise|ize|ising|izing)|explain|describe|review|audit|analy[sz]e|compare|"
    r"refactor|rewrite|fix|debug|test|build|deploy|install|read|open|cat|find|search|grep|locate|"
    r"look\s+for|delete|remove|move|rename|copy|create|make|write|save|append|edit|change|"
    r"what\s+does|how\s+does)\b",
    re.IGNORECASE,
)


def _typed_directory_content_ask(text: str) -> str | None:
    raw = str(text or "")
    if not _TYPED_DIR_CONTENT_CUE_RE.search(raw):
        return None
    if _TYPED_DIR_OTHER_INTENT_RE.search(raw):
        return None
    candidate = explicit_path_in(raw)
    if not candidate:
        return None
    from pathlib import Path

    try:
        if not Path(candidate).expanduser().is_dir():
            # A REAL directory is the whole warrant for this loose route. A file belongs to
            # read_file, and a path that is not there gives this route nothing to offer -- the
            # explicit-ask route above already claims those and answers "does not exist", which is
            # what keeps "anything in ~/Desktop/nonexistent-xyz?" working. Requiring the directory
            # to exist is also what keeps this route from claiming sentences the strict route
            # deliberately leaves alone, such as "give me an inventory of everything stored in
            # /Users/me/Documents/pollen-index".
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return candidate


def local_fact_capability_required(text: str) -> str | None:
    """Return a human capability label when the text is a this-machine FACT question that must
    be tool-backed, else None. Used as a fabrication backstop: a local-fact question that
    reaches the model with no tool result is refused, not answered with a guessed value.
    """
    lowered = _fold_largest_vocabulary(" ".join(str(text or "").strip().lower().split()))
    if not lowered:
        return None
    # Mutations belong to the operator action lane, not this read gate.
    if _MACHINE_DIAG_WRITE_RE.search(lowered):
        return None
    if _LOCAL_FACT_ADVISORY_RE.search(lowered):
        return None
    if not _LOCAL_FACT_QUESTION_RE.search(lowered):
        return None
    if _NON_LOCAL_SPACE_CONTEXT_RE.search(lowered):
        return None
    nounless_disk_query = bool(
        _NOUNLESS_LOCAL_DISK_SPACE_RE.search(lowered) and not _NON_LOCAL_SPACE_CONTEXT_RE.search(lowered)
    )
    local_most_space_query = _is_local_most_space_question(text, lowered)
    if not _LOCAL_FACT_THIS_MACHINE_RE.search(lowered) and not nounless_disk_query and not local_most_space_query:
        return None
    if _LOCAL_FACT_LARGEST_RE.search(lowered) or local_most_space_query:
        return "the largest files or folders"
    if nounless_disk_query:
        return "your drives"
    # `pattern.search` only says the topic word is somewhere in the message, and this backstop
    # REFUSES the turn when it fires with no tool result -- so a false positive here does not
    # merely mis-route, it tells the user their question cannot be answered without a tool that
    # has nothing to do with it. Measured on the deployed build 2026-07-30: "My landlord says the
    # storage space in the basement is included in the rent, is that normal in Lithuania?" came
    # back as "I can only answer that by inspecting your drives on your machine". `\bmy\b` in
    # _LOCAL_FACT_THIS_MACHINE_RE matched "my landlord", and "storage" matched the drive topic;
    # nothing checked that either belonged to the question being asked.
    for label, pattern in _LOCAL_FACT_TOPICS:
        if pattern.search(lowered) and asks_runtime_for_a_fact(text, pattern):
            return label
    return None


#: The words the largest-files recognizers key on. A token one closed typo away from exactly one
#: of them is read as that word before the patterns run (`core.typo_fold`): measured live
#: 2026-09-07, "what is the alrgest single file on my machine?" matched nothing and fell to a
#: model lane, which proposed a sandbox command instead of the runtime's own `machine.find_largest`.
_LARGEST_VOCABULARY = frozenset(
    {
        "biggest", "largest", "heaviest", "hugest", "fattest", "bulkiest", "smallest", "file", "files", "folder", "folders",
        "space", "hogs", "eaters", "guzzlers",
    }
)


def _fold_largest_vocabulary(lowered: str) -> str:
    from core.typo_fold import fold_near_miss_tokens

    return fold_near_miss_tokens(lowered, _LARGEST_VOCABULARY)


def machine_largest_intent(text: str) -> str | None:
    """Map a "biggest/largest files or folders" question to ``machine.find_largest``, else None.
    Mutations are excluded (they belong to the operator action / cleanup lane)."""
    lowered = _fold_largest_vocabulary(" ".join(str(text or "").strip().lower().split()))
    if not lowered:
        return None
    if _MACHINE_DIAG_WRITE_RE.search(lowered):
        return None
    if _LOCAL_FACT_LARGEST_RE.search(lowered) or _is_local_most_space_question(text, lowered):
        return "machine.find_largest"
    return None


_IMAGE_GEN_VERB_RE = re.compile(
    r"\b(?:generate|make|create|draw|paint|render|design|produce|give\s+me|whip\s+up|cook\s+up|imagine)\b",
    re.IGNORECASE,
)
_IMAGE_GEN_NOUN_RE = re.compile(
    r"\b(?:image|images|picture|pictures|pic|pics|photo|photos|drawing|drawings|illustration|"
    r"illustrations|artwork|render|renders|wallpaper|poster|sketch|logo|avatar|portrait|scene)\b",
    re.IGNORECASE,
)
# Pull the subject after the media noun ("...an image OF a goldfish" / "picture: a red car").
_IMAGE_GEN_PROMPT_RE = re.compile(
    r"\b(?:image|images|picture|pictures|pic|pics|photo|photos|drawing|drawings|illustration|"
    r"illustrations|artwork|render|renders|wallpaper|poster|sketch|logo|avatar|portrait|scene)\b"
    r"(?:\s+(?:of|showing|depicting|with|for|that\s+shows|that\s+depicts))?\s*[:,\-]?\s*(.+)",
    re.IGNORECASE,
)
# Advisory / how-to framings are chat, not a generation call ("how do I generate images?").
_IMAGE_GEN_ADVISORY_RE = re.compile(
    r"\b(?:how\s+(?:do|to|can|would|should)|what(?:'s| is)\s+the\s+best|which\s+(?:tool|model|api)|"
    r"recommend|tutorial|explain|guide|difference\s+between)\b",
    re.IGNORECASE,
)
_IMAGE_GEN_ACTION_RE = re.compile(
    r"\b(?:generate|make|create|draw|paint|render|design|produce|give\s+me|whip\s+up|cook\s+up|imagine)\b"
    r"[^.?!\n]{0,80}?"
    r"\b(?:image|images|picture|pictures|pic|pics|photo|photos|drawing|drawings|illustration|"
    r"illustrations|artwork|render|renders|wallpaper|poster|sketch|logo|avatar|portrait|scene)\b"
    r"(?!\s+generation\b)",
    re.IGNORECASE,
)
# A depictive verb asks for a picture with NO media noun at all: "draw me a cat", "sketch me a
# dragon", "paint a sunset". Requiring a media noun (the rule above) meant the most natural way to
# ask for an image was the one phrasing that never reached the image lane.
#
# The verb alone cannot be the trigger, because these same words carry a pile of idioms. The subject
# must be INDEFINITE ("a"/"an"/"some"), which is what separates "paint a sunset" from "paint the
# fence" -- a chore, not a render -- and an optional "me"/"us" marks the request as aimed here.
_IMAGE_GEN_DEPICT_RE = re.compile(
    r"\b(?:draw|sketch|paint|illustrate|doodle)\s+"
    r"(?:(?:me|us|for\s+me|for\s+us)\s+)?"
    r"(?:a|an|some)\s+"
    r"(.+)",
    re.IGNORECASE,
)
# Idioms that own a depictive verb without asking for a picture. Checked before any claim, because
# claiming one of these sends an ordinary sentence into the image renderer.
_IMAGE_GEN_FIGURATIVE_RE = re.compile(
    r"\bdraw(?:s|n|ing)?\s+(?:(?:your|my|our|their|his|her|its)\s+)?(?:own\s+)?"
    r"(?:a|an|the|some|any)?\s*"
    r"(?:conclusion|conclusions|inference|inferences|comparison|comparisons|parallel|parallels|"
    r"distinction|distinctions|analogy|analogies|attention|blank|breath|fire|criticism|praise|"
    r"crowd|crowds|line|lines|contrast|contrasts|lesson|lessons)\b"
    r"|\bdraw\s+(?:up|on|upon|out|down|from)\b"
    r"|\bpaint(?:s|ed|ing)?\s+(?:a|an|the)\s+(?:grim|bleak|rosy|clear|vivid|different|complicated|"
    r"mixed|damning|troubling|dire|pretty|stark|sobering|nuanced|compelling|worrying)\s+picture\b"
    r"|\bpaints\s+a\s+picture\b"
    r"|\bpaint\s+(?:the\s+town|by\s+numbers)\b"
    r"|\bsketch(?:es|ed|ing)?\s+out\b"
    r"|\blet\s+me\s+paint\b",
    re.IGNORECASE,
)
# "paint a picture of the market" carries a media noun AND a verb, and is still a figure of speech.
# What separates it from "paint a picture of a sunset" is the subject: an abstract head noun means
# the ask is for a summary, not a render.
_IMAGE_GEN_ABSTRACT_SUBJECT_RE = re.compile(
    r"^(?:(?:the|a|an|our|your|their|his|her|this|that)\s+)?"
    # Up to two modifiers before the head noun: "the HOUSING market", "the current job market".
    # Without this, "paint a picture of the housing market for me" was claimed as a render request
    # for "the housing market".
    r"(?:[\w-]+\s+){0,2}"
    r"(?:market|markets|economy|economics|situation|future|past|present|reality|landscape|"
    r"story|narrative|trend|trends|outlook|climate|industry|sector|company|business|team|"
    r"process|problem|problems|issue|issues|scenario|context|background|state|status|progress|"
    r"performance|risk|risks|impact|relationship|dynamic|dynamics|picture|"
    r"you|me|us|them|him|her|it)\b"
    r"(?:\s+(?:of|in|for|with|about|around)\b.*)?$",
    re.IGNORECASE,
)


def image_generation_intent(text: str) -> str | None:
    """Return the image PROMPT when the text asks to generate/draw an image, else None. A how-to or
    advisory question ("how do I generate images?") is chat, not a generation call."""
    raw = " ".join(str(text or "").split())
    if not raw:
        return None
    lowered = raw.lower()
    if _IMAGE_GEN_ADVISORY_RE.search(lowered):
        return None
    # "draw your own conclusions" / "the report paints a grim picture" are ordinary English.
    if _IMAGE_GEN_FIGURATIVE_RE.search(lowered):
        return None

    prompt = ""
    if _IMAGE_GEN_ACTION_RE.search(lowered):
        match = _IMAGE_GEN_PROMPT_RE.search(raw)
        prompt = (match.group(1).strip(" .!?,:;\"'") if match else "")
    if not prompt:
        # No media noun named -- "draw me a cat" is still a picture request.
        depicted = _IMAGE_GEN_DEPICT_RE.search(raw)
        if depicted:
            prompt = depicted.group(1).strip(" .!?,:;\"'")
    # A bare "make an image" with no subject is not actionable — let it fall through to chat.
    if len(prompt) < 2:
        return None
    # A media noun aimed at an abstract subject is a figure of speech, not a render.
    if _IMAGE_GEN_ABSTRACT_SUBJECT_RE.match(prompt):
        return None
    return prompt


_TOOL_INVENTORY_MARKERS = (
    "list tools",
    "list your tools",
    "show tools",
    "show me your tools",
    "what tools do you have",
    "what tools you have",
    "what tools are available",
    "what tools do you have available",
    "what toolings",
    "toolings you have",
    "toolings do you have",
    "what can you execute",
    "what actions can you take",
    "what tools do you need",
    "which tools do you need",
    "what would you use",
    "what are your tools",
    "what are your capabilities",
    "what are you capable of",
)
_SELF_TOOL_REQUEST_MARKERS = (
    "create your own tool",
    "create your own tools",
    "make your own tool",
    "make your own tools",
    "build your own tool",
    "build your own tools",
    "register a new tool",
    "register new tools",
)
_DIRECTORY_CREATE_MARKERS = (
    "create folder",
    "create a folder",
    "create new folder",
    "create a new folder",
    "create directory",
    "create a directory",
    "create new directory",
    "create a new directory",
    "make folder",
    "make a folder",
    "set up folder",
    "setup folder",
    "set up directory",
    "setup directory",
    "mkdir",
)
# A QUESTION about whether something was created is not a request to create it. Measured live
# 2026-09-18: "wait so did you created folder or not?" -- "creat" inside "created" satisfied the
# fuzzy create verb, "folder" satisfied the workspace-target noun, and `_FOLDER_PATH_RE` read
# "folder or" as a path named `or`. The turn was a yes/no question; the runtime created a
# directory named `or` and answered "I created `or`." A past-tense question form about creation
# is excluded from the directory-create planners entirely.
_DIRECTORY_CREATE_QUESTION_RE = re.compile(
    r"\b(?:did|do|does|have|has|was|were|is|are)\s+(?:you|u|it|we|they|he|she|vool|the\s+\w+){1,2}\s"
    r".{0,40}?\b(?:creat\w*|made|mak\w*|mkdir)"
    r"|\bdidn'?t\s+(?:you\s+)?creat\w*"
    r"|\b(?:created?|made)\s+.{0,40}?\bor\s+not\b",
    re.IGNORECASE,
)
_START_CODE_MARKERS = (
    "start coding",
    "start putting code",
    "put code",
    "putting code",
    "write the initial files",
    "initial files",
    "starter files",
    "bootstrap",
)
_NAMED_PATH_RE = re.compile(
    r"(?:named?|called|call)\s+(?:it\s+)?[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?",
    re.IGNORECASE,
)
_VERB_NAME_FOLDER_RE = re.compile(
    r"\b(?:create|make|crate|creat|mkdir)\s+(?:the\s+|a\s+|an\s+)?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)\s+(?:folder|directory|dir)\b",
    re.IGNORECASE,
)
_FOLDER_PATH_RE = re.compile(
    r"\b(?:folder|directory|dir|path)\s+(?:called|named)?\s*[`\"']?(?P<path>[A-Za-z0-9_./-]+)",
    re.IGNORECASE,
)
_CREATE_PATH_RE = re.compile(
    r"\b(?:create|make|setup|set up|bootstrap|mkdir)\s+(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)\b",
    re.IGNORECASE,
)
_INTO_PATH_RE = re.compile(
    r"\b(?:in|under|inside)\s+[`\"']?(?P<path>[A-Za-z0-9_./-]+(?:/[A-Za-z0-9_./-]+)*)[`\"']?",
    re.IGNORECASE,
)
# The write-demand grammar was RETIRED into core/execution/write_demand.py — the one typed
# literal-versus-brief write-demand authority (P0 simple-file-write, audit of 59aa5ee7).
# These re-exports keep every existing importer (planner.py, the machine-tool audit test)
# working unchanged; new code should import from the resolver, not from here.

from core.execution.write_demand import (  # noqa: F401  (deliberate re-exports)
    _APPEND_CONTENT_ONLY_RE,
    _APPEND_FILE_RE,
    _APPEND_TEXT_TO_FILE_RE,
    _CREATE_EXACT_FILES_RE,
    _CREATE_FILE_CONTENT_STOP,
    _CREATE_NAMED_FILE_WITH_CONTENT_RE,
    _FILE_IN_FOLDER_SAYING_RE,
    _FOLDER_FIRST_CREATE_FILE_RE,
    _IN_WORKSPACE_CREATE_FILE_RE,
    _INLINE_CREATE_FILE_RE,
    _OVERWRITE_FILE_RE,
    _PLAIN_CREATE_FILE_WITH_CONTENT_RE,
    _WORKSPACE_FILE_RE,
)

_CONTENT_BRIEF_QUANTIFIER = (
    r"(?:an?|the|some|two|three|four|five|six|seven|eight|nine|ten|\d+|"
    r"short|brief|quick|simple|small|tiny|basic|bulleted|bullet|numbered|sample|example|"
    r"one[-\s]line|two[-\s]line|three[-\s]line|multi[-\s]line|high[-\s]level|plain[-\s]english)"
)
_CONTENT_BRIEF_NOUN = (
    r"(?:summar(?:y|ies)|descriptions?|overviews?|explanations?|outlines?|rundowns?|breakdowns?|"
    r"write[-\s]?ups?|notes?|paragraphs?|bullets?|bullet\s+points?|sentences?|lines?|keys?|"
    r"entries|examples?|lists?|comparisons?|tradeoffs?|trade[-\s]offs?)"
)
_CONTENT_BRIEF_TOPIC = (
    r"(?:of|about|on|for|that|which|explaining|describing|covering|comparing|listing|in\s+it)"
)
_CONTENT_BRIEF_RES = (
    # A shape noun pointing at a topic: "summary of what a linter does", "keys in it".
    re.compile(
        rf"^(?:{_CONTENT_BRIEF_QUANTIFIER}\s+){{0,2}}{_CONTENT_BRIEF_NOUN}\s+{_CONTENT_BRIEF_TOPIC}\b",
        re.IGNORECASE,
    ),
    # A quantified shape noun and nothing else: "a two-line summary", "three sample keys". The
    # quantifier is REQUIRED here -- a bare "summary" is as likely to be the word the user wants in
    # the file, and the literal reading is the safer default when the phrase carries no brief.
    re.compile(
        rf"^(?:{_CONTENT_BRIEF_QUANTIFIER}\s+){{1,2}}{_CONTENT_BRIEF_NOUN}\s*$",
        re.IGNORECASE,
    ),
)


def content_is_a_brief(content: str) -> bool:
    """Whether captured "content" is a description of what to write rather than what to write."""
    text = " ".join(str(content or "").split()).strip().strip("`\"'")
    return bool(text) and any(pattern.match(text) for pattern in _CONTENT_BRIEF_RES)


_EXPLICIT_WORKSPACE_READ_RE = re.compile(
    r"\b(?:read|open|quote)\s+(?:the\s+file\s+)?[`\"']?(?P<path>[^`\"']+?\.[A-Za-z0-9_+-]+)[`\"']?"
    r"(?:\s+back)?\s+(?:exactly|verbatim)\b",
    re.IGNORECASE | re.DOTALL,
)
_EXACT_READBACK_RE = re.compile(
    r"\b(?:read(?:\s+(?:the\s+whole\s+file|the\s+file|it))?\s+back\s+exactly(?:\s+first)?|read(?:\s+it)?\s+exactly(?:\s+first)?|quote(?:\s+it)?\s+exactly)\b",
    re.IGNORECASE,
)
_PATH_STOP_WORDS = {
    "a",
    "an",
    "the",
    "for",
    "me",
    "my",
    "this",
    "that",
    "it",
    "on",
    "in",
    "folder",
    "directory",
    "dir",
    "path",
    "workspace",
    "repo",
    "repository",
    "there",
    "here",
    "code",
    "files",
    "machine",
    "computer",
    "desktop",
    "please",
    "pls",
}
_BUILDER_RESEARCH_MARKERS = (
    "build",
    "design",
    "architecture",
    "best practice",
    "best practices",
    "framework",
    "stack",
    "github",
    "repo",
    "repos",
    "docs",
    "documentation",
    "compare",
    "example",
    "examples",
)
_INTEGRATION_DOMAIN_MARKERS = (
    "telegram",
    "discord",
    "bot",
    "api",
    "integration",
    "webhook",
)
_HIVE_ACTION_PATTERNS = (
    "claim task",
    "claim this task",
    "claim topic",
    "take this task",
    "take this topic",
    "create topic",
    "create task",
    "create new task",
    "create hive mind task",
    "create hive task",
    "new task",
    "add task",
    "add to hive",
    "add to the hive",
    "open topic",
    "post progress",
    "update progress",
    "submit result",
    "submit findings",
    "submit verdict",
    "research packet",
    "research queue",
    "search artifacts",
    "research this topic",
)
_ENTITY_LOOKUP_DROP_TOKENS = frozenset(
    {
        "who",
        "is",
        "he",
        "she",
        "they",
        "them",
        "tell",
        "me",
        "about",
        "what",
        "do",
        "you",
        "know",
        "check",
        "find",
        "look",
        "up",
        "lookup",
        "search",
        "google",
        "in",
        "on",
        "the",
        "web",
        "pls",
        "please",
    }
)
_ENTITY_LOOKUP_KEEP_SHORT_TOKENS = frozenset({"x", "ai"})
_READ_ONLY_OPERATOR_INTENTS = {
    "operator.list_tools",
    "operator.inspect_processes",
    "operator.inspect_services",
    "operator.inspect_disk_usage",
    # Provider-backed calendar reads and note reads: no effect on this machine or any
    # provider state; they cross the network door like the web read lane.
    "operator.check_availability",
    "operator.list_calendars",
    "operator.inspect_calendar_event",
    "operator.show_agenda",
    "operator.search_calendar_event",
    "operator.find_notes",
    "operator.show_note",
}
_MUTATING_OPERATOR_INTENTS = {
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.schedule_calendar_event",
    # A note write mutates workspace content; provider proposals/updates stage an
    # outward-facing effect that always waits for the explicit approval turn.
    "operator.save_note",
    "operator.propose_calendar_event",
    "operator.update_calendar_event",
}
_WEB_TOOL_INTENTS = {
    "web.search",
    "web.fetch",
    "web.research",
    "browser.render",
}
_PAYMENT_TOOL_INTENTS = {
    "pay.x402",
    "sell.quote",
}
_HIVE_TOOL_INTENTS = {
    "hive.list_available",
    "hive.list_research_queue",
    "hive.export_research_packet",
    "hive.search_artifacts",
    "hive.research_topic",
    "hive.create_topic",
    "hive.claim_task",
    "hive.post_progress",
    "hive.submit_result",
    "voolbook.get_profile",
    "voolbook.update_profile",
}
_SUPPORTED_OPERATOR_TOOL_IDS = {
    "list_tools",
    "inspect_processes",
    "inspect_services",
    "inspect_disk_usage",
    "cleanup_temp_files",
    "move_path",
    "schedule_calendar_event",
}
_CAPABILITY_QUERY_PREFIXES = (
    "can you ",
    "could you ",
    "are you able to ",
    "do you have a way to ",
    "do you know how to ",
    "are you wired to ",
)
_IMPOSSIBLE_REQUEST_MARKERS = (
    "read my mind",
    "mind read",
    "teleport",
    "physically cook",
    "cook dinner",
    "taste this",
    "smell this",
    "touch this",
    "be physically there",
    "drive over",
    "hack a bank",
    "steal a password",
)
_PARTIAL_BUILD_MARKERS = (
    "full app",
    "entire app",
    "end to end app",
    "end-to-end app",
    "full product",
    "ship the whole app",
    "ios app",
    "android app",
    "mobile app",
)
_SWARM_DELEGATION_MARKERS = (
    "talk to other agents",
    "delegate to other agents",
    "delegate this to agents",
    "helper lane",
    "merge helper outputs",
    "swarm delegates",
    "other hive agents",
)
_EMAIL_SEND_MARKERS = (
    "send email",
    "send an email",
    "email this",
    "mail this",
    "reply by email",
)
_NEARBY_CAPABILITY_IDS = {
    "workspace.read": ["web.live_lookup"],
    "workspace.write": ["workspace.read", "sandbox.command"],
    "sandbox.command": ["workspace.read", "workspace.write"],
    "hive.write": ["hive.read"],
    "operator.discord_post": ["operator.telegram_send"],
    "operator.telegram_send": ["operator.discord_post"],
    "workspace.build_scaffold": ["workspace.write", "sandbox.command"],
}
_HIVE_CREATE_PREFIXES = (
    "create hive mind task",
    "create hive task",
    "create new task for research",
    "create new task for",
    "create new task",
    "create task for research",
    "create task for",
    "create task",
    "new task for research",
    "new task for",
    "new task",
    "add to the hive a new task",
    "add to hive a new task",
    "add to the hive",
    "add to hive",
    "add task",
    "create these tasks",
    "create them",
    "create these",
    "yes create",
    "yes create them",
    "do all and start working",
    "proceed with",
    "do it",
    "do all",
    "start working",
    "go ahead",
    "carry on",
)
_GENERIC_HIVE_TITLE_MARKERS = {
    "",
    "it",
    "them",
    "these",
    "this",
    "task",
    "tasks",
    "topic",
    "topics",
    "hive task",
    "hive tasks",
    "hive topic",
    "hive topics",
    "the task",
    "this task",
    "these tasks",
    "create task",
    "create tasks",
    "creating task",
    "creating tasks",
    "new task",
    "new tasks",
    "on hive",
    "on the hive",
    "on hive mind",
}
