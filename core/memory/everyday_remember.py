"""Remember asks in everyday words, the list one opens, and updates to a saved fact.

Measured on the v0.8 head-to-head (f4f048f7, a cloud model and a local one, fresh profile, every task a new chat):

    "hey, few things to remember for me pls. my sister Ilse has her birthday on 14 february, ..."
    "work stuff to keep in mind: our team standup moved to 9:40 every weekday"
    "small update: the cabin dates changed, its now 13-16 march, same booking code"

were each answered "Got it -- noted" and nothing was saved: the memory lane only took a message that STARTS with
remember / note / store / keep / retain / save, and the model never called its memory tool. The facts said on the
following turns of the same list, and the updates said in a later chat, were lost the same way.

This module only reads text and the chat's earlier turns. What is saved, where, and under which law is still the
memory lane's (``core.persistent_memory``): the same admission, scope rule, secret refusal and forget fence as an
explicit "Remember that ...".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: An everyday lead that asks VOOL to remember what follows. "Remember to ..." and "don't forget to ..." are
#: reminders (another lane), so the forms here never take "to" after them.
_LEAD_RE = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|so|oh|also|and|btw|right)\b[\s,!.]*)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|pls\s+|plz\s+|please\s+)?"
    r"(?P<lead>"
    r"(?:a\s+)?(?:few|some|couple(?:\s+of)?|two|three|four|five|\d+)\s+(?:more\s+|quick\s+)?"
    r"(?P<plural1>things|bits|facts|notes|details)\s+(?:for\s+you\s+)?to\s+(?:remember|keep\s+in\s+mind|note(?:\s+down)?)"
    r"|(?:[a-z][\w'-]*\s+){0,3}(?P<plural2>stuff|things|info|details|notes)\s+(?:for\s+you\s+)?to\s+"
    r"(?:remember|keep\s+in\s+mind|note(?:\s+down)?)"
    r"|keep\s+(?:this\s+|that\s+)?in\s+mind(?:\s+that)?"
    r"|remember\s+(?:this\s+)?for\s+me(?:\s+that)?"
    r"|(?:make|take)\s+a\s+note(?:\s+of\s+this)?(?:\s+that)?"
    r"|note\s+(?:this\s+)?down(?:\s+that)?"
    r"|for\s+future\s+reference"
    r"|don'?t\s+forget\s+that"
    r")"
    # "keep in mind for later that ...": the filler may stand between the lead and its "that" too (Mac gate 15 on
    # 06a3f89fc kept "for later that ..." as the fact).
    r"(?:\s+for\s+(?:me|later)(?:\s+that\b)?)?(?:\s*,?\s*(?:pls|please|plz|thanks|thx))?"
    r"(?:\s*[:.,;!\-]+\s*|\s+|$)(?P<fact>.*)$",
    re.IGNORECASE | re.DOTALL,
)

#: A request or command, not something the person is telling VOOL about their life.
_REQUEST_HEAD_RE = re.compile(
    r"^\s*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:write|make|create|build|draft|show|tell|give|find|search|look|check|open|send|read|summari[sz]e|translate|"
    r"explain|list|help|fix|run|install|buy|order|play|set|add|remove|delete|forget|erase|turn|switch|start|stop|"
    r"cancel|remind|compare|calculate|convert|generate|draw|plan|book|schedule|email|call|text|message|pay|swap|"
    r"remember|note|save|store|keep|retain)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|im|i've|ive|i'd|my|mine|me|we|we're|we've|our|ours|us)\b", re.IGNORECASE)
_CONTINUATION_HEAD_RE = re.compile(r"^(?:(?:oh|also|and|plus|btw|then|one more(?: thing)?)\b[\s,:.-]*)+", re.IGNORECASE)

#: What makes a statement an update to something already saved.
_UPDATE_RE = re.compile(
    r"\b(?:update[ds]?|changed|change of plans?|moved|went back|is now|are now|it'?s now|its now|now (?:on|at|in|is)|"
    r"rescheduled|postponed|brought forward|no longer|not anymore|anymore|instead)\b",
    re.IGNORECASE,
)
_UPDATE_LEAD_RE = re.compile(
    r"^(?:(?:oh|also|and|btw|ok|okay|so)\b[\s,]*)*(?:(?:a\s+)?(?:small|quick|little|tiny)\s+)?(?:update|change|correction)"
    r"(?:\s+(?:on|to)\s+that)?\s*[:,.\-\u2013\u2014]\s*",   # the dash people type as - or as an en/em dash
    re.IGNORECASE,
)
#: Words an update shares with almost any saved fact; they never decide which fact it updates.
_GENERIC = frozenset(
    [
        "update", "updates", "updated", "small", "quick", "little", "tiny", "change", "changed", "changes",
        "moved", "move", "went", "back", "now", "new", "same", "old", "its", "it's", "is", "are", "was", "were",
        "the", "a", "an", "and", "also", "oh", "to", "on", "at", "in", "of", "for", "my", "our", "we", "i",
        "me", "us", "this", "that", "these", "those", "date", "dates", "time", "times", "day", "days", "week",
        "weeks", "instead", "anymore", "longer", "no", "not"
    ]
)
_MONTHS = frozenset(
    [
        "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
        "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
        "dec", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "weekday",
        "weekdays"
    ]
)

#: How many earlier turns of one chat a list stays open across.
LIST_TURNS = 8


@dataclass(frozen=True)
class EverydayLead:
    """A recognised everyday remember ask: the fact it carries ("" for a lead alone), whether it opens a list, and
    what the list is for when the person named it ("for my kayak trip")."""

    fact: str
    opens_list: bool
    subject: str = ""
    #: The subject is written after the item, in brackets, not in front of it (a lead a judge read: `judged_lead`).
    subject_last: bool = False

    def under_subject(self, item: str) -> str:
        """An item of this list as it is saved: with what the list is for."""
        if not self.subject:
            return item
        return f"{item.rstrip('. ')} ({self.subject})" if self.subject_last else f"{self.subject}: {item}"


_ASK_HEAD = (
    r"^(?:(?:hey|hi|hello|ok|okay|so|oh|also|and|btw|right)\b[\s,!.]*)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|pls\s+|plz\s+|please\s+)?"
)
#: A list with a named subject: "Remember these for my kayak trip: a dry bag and a bilge sponge". What is said now
#: and on the list's later turns is kept under that subject, so an item said alone ("and a spare paddle leash") is
#: still findable by what it is for.
_SUBJECT_LIST_RE = re.compile(
    _ASK_HEAD
    + r"(?:remember|note(?:\s+down)?|keep(?:\s+in\s+mind)?|save|store)\s+"
    r"(?:these|those|the\s+following|this\s+list)(?:\s+(?:things|items|bits))?\s+"
    r"(?P<subject>(?:for|about)\s+[^:?!.]{2,60}?)\s*:\s*(?P<items>.*)$",
    re.IGNORECASE | re.DOTALL,
)
#: An ask to keep a fact, in any verb: "<do something> that <a fact about me>" ("jot down that my godmother's
#: birthday is 27 August"). The ask is read from its shape, an imperative in front of "that" and a personal fact
#: behind it, not from a list of keep verbs. What can stand in front of "that" without asking anything is excluded
#: by `_NOT_AN_ASK`: a subject, a linking word, a judgement, or a verb of thinking, saying or checking.
_THAT_FACT_RE = re.compile(
    _ASK_HEAD + r"(?P<ask>[a-z]+(?:\s+[a-z]+){0,5}?)\s+that\s+(?P<fact>(?:my|our|i|i'm|i’m|i've|i’ve|we|we're|we've)\b.+)$",
    re.IGNORECASE | re.DOTALL,
)
_NOT_AN_ASK = frozenset(
    [
        "i", "it", "he", "she", "they", "we", "you", "this", "that", "there", "someone", "everyone", "people",
        "is", "was", "are", "were", "do", "does", "did", "has", "have", "had", "seems", "now", "so", "given",
        "seeing", "considering", "provided", "except", "not", "sorry", "glad", "sure", "afraid", "happy", "sad",
        "funny", "shame", "pity", "true", "false", "good", "great", "bad", "nice", "weird", "odd", "strange",
        "lucky", "think", "thinks", "thought", "believe", "guess", "bet", "hope", "wish", "doubt", "mean",
        "know", "knew", "see", "saw", "notice", "deny", "agree", "admit", "understand", "realise", "realize",
        "confirm", "verify", "ensure", "check", "prove", "mention", "mentioned", "say", "said", "says", "tell",
        "told", "heard", "read", "pretend", "imagine", "suppose", "assume", "if", "when", "because", "since",
        "while", "although", "though", "but", "and", "or", "the", "a", "an", "my", "our"
    ]
)


#: A short framing in front of a personal fact, set off by a colon or a dash: "hey for later: my turtle is called
#: noodle", "one more thing to remember: my nephew's goldfish goes by Flapjack", "note to self - my locker is 214".
#: The framing is read by its shape (a few plain words, none of them the person, no number and no question), not
#: from a list of keep phrases. Gate v3 on fe98e849d (2026-11-22): "hey for later: my turtle is called noodle" was
#: kept by nothing, the local model answered as if it had been kept, and the turtle's name was "not saved" from
#: another chat and after a restart.
_FRAMED_FACT_RE = re.compile(
    r"^(?P<frame>[a-z][a-z' ]{0,60}?)\s*(?::|\s[-\u2013\u2014]|[\u2013\u2014])\s*(?P<fact>\S.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _framed_fact(clean: str) -> str:
    """The personal fact behind a short framing and a colon or dash, or "" when the sentence is not one."""
    match = _FRAMED_FACT_RE.match(clean)
    if match is None:
        return ""
    frame, fact = match.group("frame").strip(), match.group("fact").strip()
    if not 1 <= len(frame.split()) <= 6 or _FIRST_PERSON_RE.search(frame) or is_question(fact):
        return ""
    if _REQUEST_HEAD_RE.match(fact) or not _personal_fact_clause(fact):
        return ""
    try:
        from core.private_chat import looks_like_privacy_request

        if looks_like_privacy_request(frame):
            # "bitte nicht speichern: my cat is scared of thunder" asks NOT to keep it: a frame that asks not to save or
            # record, in any language the privacy reader reads, is never a frame for keeping (served on 261a03892 and
            # later: answered "Locked in. I'll remember that." and saved the fact).
            return ""
    except Exception:
        return ""
    if update_shaped(f"{frame}: {fact}") or update_shaped(fact):
        # "update: my cello is kept at the music school now", "correction: my locker is 215": the frame itself, or the
        # fact, says a saved fact changes; the update reading owns it (and writes nothing when nothing is saved).
        return ""
    try:
        from core.stipulated_frame import has_stipulated_frame

        if has_stipulated_frame(clean):
            return ""
    except Exception:
        return ""
    try:
        from core.operator.parser import parse_operator_action_intent

        if parse_operator_action_intent(clean) is not None:
            return ""  # a reminder, a note or another action said with a colon is that lane's
    except Exception:
        return ""
    return fact


def _ask_that_fact(clean: str) -> str:
    """The personal fact of "<imperative> that <fact>", or "" when the sentence is not such an ask."""
    match = _THAT_FACT_RE.match(clean)
    if match is None or is_question(clean):
        return ""
    ask = match.group("ask").casefold().split()
    if ask[0] in _NOT_AN_ASK or _REQUEST_HEAD_RE.match(match.group("ask")):
        return ""  # a request another lane owns, or the explicit "remember that ..." with its own path
    try:
        from core.stipulated_frame import has_stipulated_frame

        if has_stipulated_frame(clean):
            return ""
    except Exception:
        return ""
    try:
        from core.operator.parser import parse_operator_action_intent

        if parse_operator_action_intent(clean) is not None:
            return ""  # "jot down that ..." saves a note: the notes lane's own object
    except Exception:
        return ""
    fact = match.group("fact").strip()
    return fact if _personal_fact_clause(fact) else ""


#: "<a short lead>: <a text>". The colon is followed by a space, so a clock time ("9:40") is not a lead.
_LED_RE = re.compile(r"^(?P<lead>[^:?!\n]{3,90}?)\s*:\s+(?P<body>\S.*)$", re.DOTALL)
#: What a lead says the text is for, when it names something of the person's: "for the flock book", "on my jobs
#: list". Kept with the fact, as a named list's subject is ("for my kayak trip: a dry bag").
_LEAD_SUBJECT_RE = re.compile(
    r"\b(?:for|on|onto|in|into|to|about)\s+(?P<subject>(?:my|our|the)\s+[\w'’-]+(?:\s+[\w'’-]+){0,3}?)"
    r"(?:\s+(?:for\s+me|please|pls|plz))?\s*(?=,|$)",
    re.IGNORECASE,
)
#: A lead that announces more than one thing ("two things for ...", "a few bits about ..."): a count, not a verb.
_SEVERAL_RE = re.compile(r"\b(?:few|some|several|couple|two|three|four|five|six|seven|eight|nine|ten|[2-9]|\d{2})\b",
                         re.IGNORECASE)


def led_statement(text: str) -> tuple[str, str] | None:
    """(lead, body) when the message is one short lead, a colon, and a text behind it; else None.

    Measured on the real app (94ce6169 and e24e867c, an independent question set): "stash this for me: the spare
    key is under the blue watering can", "add to my list: replace the greenhouse glass in spring" and "Two things
    for the flock book: shearing day is 19 june" wrote no row, and the model answered "Got it" or "added to your
    list". The words of such a lead are open-ended, and the same shape carries every other task ("put this into
    German: ..."), so this only reads the shape. Whether the lead asks for a keep is judged where it is used
    (`core.persistent_memory`), by a model and not by a list of verbs."""
    whole = str(text or "").strip()
    if not whole or len(whole) > 600 or "\n" in whole:
        return None
    match = _LED_RE.match(" ".join(whole.split()))
    if match is None:
        return None
    lead = match.group("lead").strip()
    body = match.group("body").strip()
    if not 1 <= len(lead.split()) <= 12 or not 2 <= len(body.split()) <= 60 or "?" in body:
        return None
    return lead, body


#: What a lead points at the text behind it with: "stash THIS for me", "keep THESE", "hang on to IT".
_LEAD_POINTER_RE = re.compile(r"\b(?:this|these|that|those|it|them)\b", re.IGNORECASE)


#: A lead that opens on its pointer as the OBJECT of the capture verb before it ("keep THIS tucked away for later:
#: ..."); a pointer followed by a verb of its own is the subject of a statement ("this is the gate code: 4471").
_POINTER_OBJECT_LEAD_RE = re.compile(
    r"^(?:this|these|that|those|it|them)\b(?!\s+(?:is|are|was|were|am|has|have|had|does|do|did|will|would|shall|"
    r"should|can|could|may|might|must|means|meant|goes|went|says|said)\b)(?!['’]s\b)",
    re.IGNORECASE,
)


def pointed_capture_fact(capture_object: str) -> str | None:
    """The fact of an explicit capture whose object is a pointer lead and the text behind it, else None.

    "Keep this in the back of your head for later: the boat club meets on Thursday" names its fact behind the
    colon; the pointer and whatever the lead adds to it ("tucked away", "for later", "in your head") are the
    ask, not the fact. Measured on the installed app (Mac gate 15 on 06a3f89fc): the whole object was saved, so
    the row began "this in the back of your head for later: ...". What the lead says the text is for stays with
    the fact, as any judged lead's does ("Remember this for my allotment: ..." keeps "(my allotment)")."""
    led = led_statement(capture_object)
    if led is None:
        return None
    lead, body = led
    if not _POINTER_OBJECT_LEAD_RE.match(lead):
        return None
    return judged_lead(lead, body).fact or None


def lead_may_ask(lead: str) -> bool:
    """Whether a lead has the shape of an ask about the text behind it: it points at the text ("stash this for
    me"), or names something of the person's the text is for ("for the flock book", "to my list").

    A bare label ("error", "idea", "heads up", "my problem") has neither and is never sent to the judge: measured
    on the judge's probe, a 4b local model read such labels as keep asks, and read every lead of this shape right.
    A keep ask with neither ("note to self: ...") is not read here; it is answered as before."""
    return bool(_LEAD_POINTER_RE.search(lead) or _LEAD_SUBJECT_RE.search(lead))


_ROW_SUBJECT_LAST_RE = re.compile(r"\((?P<subject>(?:my|our|the)\s+[^()]{2,60})\)\s*$", re.IGNORECASE)
_ROW_SUBJECT_FIRST_RE = re.compile(r"^(?:for|about)\s+(?P<subject>(?:my|our|the)\s+[^:]{2,60}):\s+\S", re.IGNORECASE)


def row_list_subject(row_text: str) -> str:
    """The list a saved row was kept under, as `EverydayLead.under_subject` wrote it: "shearing day is 19 june
    (the flock book)" and "for my kayak trip: a dry bag" give "the flock book" and "my kayak trip"; else ""."""
    clean = " ".join(str(row_text or "").split())
    found = _ROW_SUBJECT_LAST_RE.search(clean) or _ROW_SUBJECT_FIRST_RE.match(clean)
    return " ".join(found.group("subject").split()).lower() if found is not None else ""


def judged_lead(lead: str, body: str) -> EverydayLead:
    """The everyday lead of a "<lead>: <body>" a judge read as a keep ask. The fact carries what the lead says it
    is for ("shearing day is 19 june (the flock book)"); a lead that announces several things opens a list.

    The subject goes behind the item. In front of it ("for the recipe binder: the scones need 12 minutes", then
    "for the recipe binder: the flapjacks keep for a week") two items of one list got one fact key, the key being
    read from a row's first words, and the second write marked both rows disputed: neither was listed again."""
    named = _LEAD_SUBJECT_RE.search(lead)
    subject = " ".join(named.group("subject").split()).lower() if named is not None else ""
    several = bool(_SEVERAL_RE.search(lead))
    titled = EverydayLead(fact="", opens_list=several, subject=subject, subject_last=True)
    return EverydayLead(fact=titled.under_subject(body), opens_list=several, subject=subject if several else "",
                        subject_last=True)


def everyday_lead(text: str) -> EverydayLead | None:
    clean = " ".join(str(text or "").split())
    listed = _SUBJECT_LIST_RE.match(clean)
    if listed is not None:
        subject = listed.group("subject").strip()
        items = listed.group("items").strip()
        return EverydayLead(fact=f"{subject}: {items}" if items else "", opens_list=True, subject=subject)
    match = _LEAD_RE.match(clean)
    if match is None:
        asked = _ask_that_fact(clean) or _framed_fact(clean)
        return EverydayLead(fact=asked, opens_list=False) if asked else None
    fact = match.group("fact").strip()
    if fact.lower().startswith("to "):
        if not match.group("lead").lower().startswith("keep"):
            return None  # "make a note to call mom" asks for a reminder, another lane
        # "keep in mind to buy milk" keeps "buy milk": the capture lane read "keep" as its verb and saved
        # "in mind to buy milk".
        fact = fact[3:].strip()
        if not fact:
            return None
    plural = bool(match.group("plural1") or match.group("plural2"))
    return EverydayLead(fact=fact, opens_list=plural or not fact)


def is_question(text: str) -> bool:
    """A question or an ask for an answer, also without its question mark ("remind me what my manager is called")."""
    from core.agent_runtime.cross_chat_recall import _is_statement

    clean = " ".join(str(text or "").split())
    return bool(clean) and not _is_statement(clean)


def personal_statement(text: str) -> bool:
    """Something the person tells about their own life: first person, not a question, not a request."""
    clean = _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split()))
    if not clean or is_question(clean) or _REQUEST_HEAD_RE.match(clean):
        return False
    return bool(_FIRST_PERSON_RE.search(clean))


#: A closing or a bare reaction inside a list ("ok that's all", "thanks!"): no fact.
_CLOSING_RE = re.compile(
    r"^(?:ok(?:ay)?|k|thanks?|thank\s+you|thx|ty|cool|great|perfect|nice|awesome|got\s+it|"
    r"that'?s\s+(?:it|all|everything)|nothing\s+else|done|no(?:pe)?|yes|yeah|yep|sure)\b[\s,.!]*"
    r"(?:(?:that'?s\s+(?:it|all|everything)|for\s+now|thanks?|thank\s+you|bye)[\s,.!]*)*$",
    re.IGNORECASE,
)


#: Taking back or correcting what was just said ("never mind, don't keep that one", "actually scratch the last one",
#: "the second one was wrong"): never a fact of the list (independent review of job 19, C2: these were saved and
#: answered "Locked in", with the retracted fact still kept).
_RETRACTION_RE = re.compile(
    r"\b(?:never\s*mind|nvm|scratch\s+(?:that|this|it|the|my)|strike\s+that|take\s+(?:that|it)\s+back|"
    r"(?:don'?t|do\s+not|no\s+need\s+to)\s+(?:keep|save|remember|store|note|bother)|forget\s+(?:that|it|this|the|what|about)|"
    r"ignore\s+(?:that|it|this|the|what|my)|(?:delete|remove|drop|cancel|undo)\s+(?:that|it|this|the\s+last)|"
    r"(?:was|is|'s|’s|were|are)\s+wrong|wrong\s+one|not\s+right|my\s+(?:bad|mistake)|i\s+(?:was|am)\s+wrong|"
    r"i\s+meant|correction|actually\s+no)\b",
    re.IGNORECASE,
)
#: Who a list fact can be about without "I" or "my": someone named or pointed at ("his daughter is called Marta").
_THIRD_PERSON_RE = re.compile(r"\b(?:he|he's|she|she's|his|her|hers|him|they|they're|their|theirs|them)\b", re.IGNORECASE)
#: Words a list's turns share without sharing a subject.
_LIST_FILLER = frozenset(
    ["things", "thing", "remember", "remind", "note", "notes", "list", "couple", "few", "some", "this", "that", "these",
     "those", "with", "from", "have", "has", "had", "about", "just", "also", "what", "when", "where", "there", "here",
     "been", "will", "would", "your", "yours", "for", "the", "and", "but", "then", "than", "very", "really", "today"]
)


def _list_subject_words(text: str) -> set[str]:
    return {w for w in topic_tokens(text) if len(w) >= 4 and w not in _LIST_FILLER}


#: The take-back part of the retractions alone (no correction words such as "wrong" or "i meant", which carry a new
#: value): "never mind, don't keep that one" holds no fact at all.
_TAKE_BACK_RE = re.compile(
    r"\b(?:never\s*mind|nvm|scratch\s+(?:that|this|it|the\s+last)|strike\s+that|take\s+(?:that|it)\s+back|"
    r"(?:don'?t|do\s+not|no\s+need\s+to)\s+(?:keep|save|remember|store|note)\s+(?:that|this|it|the\s+last)|"
    r"forget\s+(?:that|it|this)|ignore\s+(?:that|it|this)|(?:delete|remove|drop|cancel|undo)\s+(?:that|it|this|the\s+last))\b",
    re.IGNORECASE,
)


def takes_back(text: str) -> bool:
    """Whether the statement takes back what was said before ("never mind, don't keep that one")."""
    return bool(_TAKE_BACK_RE.search(" ".join(str(text or "").split())))


def retracts_or_corrects(text: str) -> bool:
    return bool(_RETRACTION_RE.search(" ".join(str(text or "").split())))


def list_statement(text: str, earlier_user_turns: list[str] | None = None) -> bool:
    """A fact said inside a list the person opened: a statement about the person, the list's people or its subject.

    First person is not needed there. Measured on the real local model (held-out set): after "ok a few things to
    remember for me. my brother Tobiah lives in Quillon", "his daughter is called Marta and she turns 7 on 3 august"
    and "oh and Tobiah is vegetarian" were answered "Got it ... Nothing was saved"; they name the people of the list.
    A question, a request, a closing, a retraction or a correction is no fact, and neither is chatter that is about
    nobody in the list ("the weather is awful today"). `earlier_user_turns` are the list's earlier turns: a statement
    sharing a subject word with them ("tobiah is vegetarian") belongs to the list."""
    clean = _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split()))
    if (len(clean.split()) < 3 or _CLOSING_RE.match(clean) or is_question(clean) or _REQUEST_HEAD_RE.match(clean)
            or _RETRACTION_RE.search(clean)):
        return False
    if _FIRST_PERSON_RE.search(clean) or _THIRD_PERSON_RE.search(clean):
        return True
    earlier = " ".join(str(t or "") for t in (earlier_user_turns or [])[:LIST_TURNS])
    return bool(_list_subject_words(clean) & _list_subject_words(earlier))


def strip_continuation(text: str) -> str:
    return _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split())).strip()


#: What a bare list item starts with: "a spare paddle leash", "two tent pegs", "my old stove".
_ITEM_HEAD_RE = re.compile(
    r"^(?:a|an|the|my|our|his|her|their|some|another|one\s+more|extra|spare|more|\d+|two|three|four|five|six)\b",
    re.IGNORECASE,
)


def list_item(text: str, earlier_user_turns: list[str] | None = None) -> bool:
    """A turn that adds to an open list: a list statement, or a bare item joined on with "and" / "also" / "plus"
    ("And a spare paddle leash."). Measured on the real app (94ce6169): the bare item was answered by the model and
    nothing was kept, because it names nobody and shares no word with the list's earlier turns."""
    if list_statement(text, earlier_user_turns):
        return True
    clean = " ".join(str(text or "").split())
    rest = strip_continuation(clean)
    if rest == clean or not 2 <= len(rest.split()) <= 12:
        return False
    return bool(
        _ITEM_HEAD_RE.match(rest) and not _CLOSING_RE.match(rest) and not is_question(rest)
        and not _REQUEST_HEAD_RE.match(rest) and not _RETRACTION_RE.search(rest)
    )


def open_list(earlier_user_turns: list[str], judged=None) -> EverydayLead | None:
    """The list-opening remember ask this chat's earlier turns, newest first, reach.

    The way back goes through the list's own items and updates, and through questions: a question asked in the middle
    of a list is answered and the list stays open (measured on the v0.8 line: "... / how long do you boil an egg? /
    oh and his school is Fernhill" lost the last fact, because the question closed the list). A closing ("that's
    all"), a take-back, or talk about something else still closes it, and so does LIST_TURNS.

    `judged(said)` is asked about an earlier turn none of the readers here knows: it returns the lead of a keep ask
    said in other words ("Two things for the flock book: ..."), or None."""
    window = list(earlier_user_turns[:LIST_TURNS])
    for index, said in enumerate(window):
        lead = everyday_lead(said)
        if lead is not None:
            return lead if lead.opens_list else None
        if list_item(said, window[index + 1:]) or update_shaped(said):
            continue
        clean = " ".join(str(said or "").split())
        if (clean and is_question(clean) and not _REQUEST_HEAD_RE.match(clean) and not _CLOSING_RE.match(clean)
                and not _RETRACTION_RE.search(clean)):
            continue  # a question; a request for something else ("can you tell me a joke") still closes the list
        lead = judged(said) if judged is not None else None
        return lead if lead is not None and lead.opens_list else None
    return None


def list_is_open(earlier_user_turns: list[str]) -> bool:
    """True when this chat's earlier turns, newest first, reach a list-opening remember ask (`open_list`)."""
    return open_list(earlier_user_turns) is not None


#: A fact about the person's own life, by its shape: what is theirs and what it is ("my landlord's called ...",
#: "our rent is due on the 4th"), or what they are, have, or always do ("I'm allergic to ...", "I live in ...").
_POSSESSIVE_FACT_RE = re.compile(
    r"^(?:my|our)\s+(?P<subject>(?:[\w'’-]+\s+){0,5}?[\w-]+)"
    r"(?:\s+(?:is|are|was|were|will\s+be|has|have|lives?|works?|starts?|ends?|turns?)\b|(?:'s|’s)\s+(?!\w+\s+(?:is|are)\b))"
    r"\s*\S",
    re.IGNORECASE,
)
_SELF_FACT_RE = re.compile(
    r"^(?:i(?:'m|’m|\s+am)\s+(?:allergic|intolerant|from|born|(?:a|an)\s+(?!bit\b|little\b|lot\b|tad\b|few\b)|the\b|\d+)"
    r"|i\s+(?:live|work|study|teach|grew\s+up|was\s+born)\s+(?:in|at|on|for|as|near|with)\b"
    r"|i(?:\s+have|'ve\s+got|’ve\s+got|\s+own|\s+drive|\s+ride)\s+(?:a|an|two|three|four|five|\d+|my)\b"
    r"|i\s+(?:can'?t|can’t|cannot|don'?t|don’t|do\s+not|never|always|usually)\s+(?:eat|drink|have|take|use|wear|drive)\b"
    r"|i\s+(?:prefer|take\s+my)\b)",
    re.IGNORECASE,
)
#: What "my ... is ..." is about when the person is talking about the conversation or the task at hand, not their life.
_TASK_TALK = frozenset(
    [
        "question", "questions", "problem", "issue", "point", "guess", "bad", "mistake", "fault", "goal", "aim",
        "plan", "idea", "thought", "thoughts", "opinion", "understanding", "concern", "worry", "code", "script",
        "program", "function", "query", "error", "output", "screen", "prompt", "message", "request", "answer",
        "reply", "turn", "computer", "laptop", "browser", "app", "tab", "terminal", "internet", "wifi",
        "connection", "battery", "head", "day", "week", "morning", "evening", "night", "mood", "meeting",
        "call", "headache", "cold", "minute", "second", "favour", "favor", "feeling", "doubt", "bug", "ticket",
        "deadline", "lot"
    ]
)
#: Said about this moment, not about the person's life: "I have a call in ten minutes", "my train is late today".
_MOMENT_RE = re.compile(
    r"\b(?:today|tonight|right\s+now|at\s+the\s+moment|just\s+now|this\s+(?:morning|afternoon|evening|minute|second)|"
    r"in\s+(?:a|an|\d+|ten|five|two|a\s+few)\s+(?:minutes?|mins?|seconds?|hours?|sec)|currently|so\s+far)\b",
    re.IGNORECASE,
)
_ASIDE_TAIL_RE = re.compile(
    r"[\s,;]*\b(?:by\s+the\s+way|btw|fyi|just\s+so\s+you\s+know|for\s+what\s+it'?s\s+worth|in\s+case\s+it\s+matters)[\s.!]*$",
    re.IGNORECASE,
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


#: "<my thing> is <one value>" and nothing after it: "my scout hut door code is 5308", "our rent day is 4 March".
_ONLY_A_VALUE_RE = re.compile(r"\s(?:is|are)\s+(?:number\s+|no\.?\s*|#\s*)?(?P<value>\S+(?:\s+\S+)?)[.!]*$", re.IGNORECASE)


def _gives_only_a_value(clause: str) -> bool:
    found = _ONLY_A_VALUE_RE.search(clause)
    if found is None:
        return False
    value = found.group("value")
    return bool(typed_values(value)) and not _without_values(value).strip(" .,!")


#: "<something about me or mine>: <one value>": "the gym gave me a new table tennis rating this season: 1276".
_COLON_VALUE_RE = re.compile(r"^(?P<said>[^:?]{8,200}?)\s*:\s*(?P<value>\S+(?:\s+\S+)?)[.!]*$")


_OWN_OR_GIVEN_RE = re.compile(r"\b(?:my|our|mine|ours|me|us)\b", re.IGNORECASE)


def _gives_a_value_after_a_colon(clause: str) -> bool:
    """A statement in the first person that ends in a colon and one bare value (a number, a date, a clock time, a
    weekday): it gives something of the person's its value. Measured on the real app (an independent question set):
    "oh, the gym gave me a new table tennis rating this season: 1276." was answered "That's a solid rating!" and
    nothing was kept, so the later change and its undoing had no history to stand on."""
    clean = " ".join(str(clause or "").split())
    found = _COLON_VALUE_RE.match(clean)
    if found is None or not 4 <= len(clean.split()) <= 40:
        return False
    said, value = found.group("said"), found.group("value")
    # Something of the person's ("my ...") or given to them ("... gave me ..."); "I need the report by: 5 March"
    # is a need, not a thing of theirs with a value.
    if not _OWN_OR_GIVEN_RE.search(said) or _MOMENT_RE.search(clean) or _REQUEST_HEAD_RE.match(said):
        return False
    opening = {_word_form(w) for w in re.findall(r"[a-z][a-z-]+", said.casefold())}
    return bool(typed_values(value)) and not _without_values(value).strip(" .,!") and not (opening & _TASK_TALK)


def _personal_fact_clause(clause: str) -> bool:
    clean = " ".join(str(clause or "").split())
    if not 3 <= len(clean.split()) <= 40:
        return False
    owned = _POSSESSIVE_FACT_RE.match(clean)
    if owned is not None:
        subject = {_word_form(w) for w in re.findall(r"[a-z][a-z-]+", owned.group("subject").casefold())}
        # "my code is throwing an error" is talk about the task; "my scout hut door code is 5308" gives the thing
        # its value and nothing else, which is a fact whatever the thing is called. Measured on the real app: a
        # door code said in passing was never kept, because "code" is a task word.
        task_talk = bool(subject & _TASK_TALK) and not _gives_only_a_value(clean)
        return not task_talk and not _FILE_PLACE_RE.search(clean) and not _MOMENT_RE.search(clean)
    if not _SELF_FACT_RE.match(clean) or _MOMENT_RE.search(clean):
        return False
    opening = {_word_form(w) for w in re.findall(r"[a-z][a-z-]+", clean.casefold())[:7]}
    return not (opening & _TASK_TALK)


def stated_facts(text: str) -> list[str]:
    """The facts about their own life the person states in a message that asks for something else, or for nothing.

    Measured on the v0.8 line (job 22 review; the diagnosis probes of 2026-11-16): a fact said without a remember
    lead went to the model, which answered "Got it" or "I'll keep that safe", and no row was written. This reads the
    message one sentence at a time and returns the sentences that are personal facts by shape (`_personal_fact_clause`);
    a question, a request, a take-back, pasted material and a made-up frame hold none. What is saved, and under
    which law, is the memory lane's: this only reads."""
    whole = str(text or "")
    if not whole.strip() or len(whole) > 600 or "\n" in whole.strip():
        return []
    try:
        from core.stipulated_frame import has_stipulated_frame

        if has_stipulated_frame(whole):
            return []
    except Exception:
        return []
    facts: list[str] = []
    for sentence in _SENTENCE_END_RE.split(" ".join(whole.split())):
        clean = _ASIDE_TAIL_RE.sub("", strip_continuation(sentence)).strip()
        if not clean or "?" in clean or _REQUEST_HEAD_RE.match(clean) or _RETRACTION_RE.search(clean):
            continue
        if update_shaped(clean):
            # A change ("my desk moved to room 3B") is a value in flux. An update to a saved fact is the update
            # lane's; any other stays in the chat's own record, where the newest statement is the current one. A
            # row written here would outlive the next change said in passing and be answered as the saved value.
            continue
        if (_personal_fact_clause(clean) or _gives_a_value_after_a_colon(clean)) and not is_question(clean):
            facts.append(clean)
    return facts[:3]


#: Said about one past occasion or about something imagined: "I lost one stirrup leather last week", "lunch was hours
#: ago", "if we won the lottery we'd move to the coast". Used only by `unlisted_statements`: the judgement it feeds
#: read each of these as a lasting fact (the first on the real app, where it wrote a row; the other two in the fix
#: mission's probe), and a sentence anchored to one past occasion, or opened as a supposition, is not a standing fact
#: whatever a model says. The shapes `stated_facts` reads keep their own rule (`_MOMENT_RE`).
_ONE_OCCASION_RE = re.compile(
    r"\b(?:yesterday|last\s+(?:night|week|weekend|month|year|time|(?:mon|tues|wednes|thurs|fri|satur|sun)day)|"
    r"the\s+other\s+(?:day|night|week)|\w+\s+ago|earlier\s+(?:on|today)|just\s+(?:now|then)|"
    r"this\s+(?:week|weekend|month))\b"
    r"|^\s*(?:if|suppose|supposing|imagine|say|what\s+if)\b",
    re.IGNORECASE,
)


#: Words a sentence about something can start with: the closed class of pronouns and determiners, with their
#: contractions ("I'm", "it's"). Used only to tell a subject from a lead in `_opens_with_a_lead`.
_SUBJECT_STARTERS = frozenset(
    [
        "i", "we", "you", "he", "she", "they", "it", "this", "that", "these", "those", "there", "the", "a",
        "an", "my", "our", "your", "his", "her", "their", "its", "one", "some", "all", "every", "each", "no",
        "most", "many", "both"
    ]
)
_THAT_LEAD_RE = re.compile(r"^(?P<lead>[^,.;:?!]+?)\s+that\s+\S", re.IGNORECASE)


def _opens_with_a_lead(clean: str) -> bool:
    """Whether a sentence opens with a short lead about the text behind it: "<lead>: <text>" (`led_statement`) or
    "<lead> that <text>" where the lead has no subject of its own ("Scribble down that ...", "Stick it in your
    notes that ...").

    Such a sentence is said TO the assistant about its text: a keep ask, a note ask or another task. It belongs to
    the lanes that read leads; said in passing it is not. Measured in the fix mission (function level, 2026-11-17):
    "Jot down that my godmother's birthday is 27 August." reached the lasting-fact judgement and read yes, which
    with a model up writes a memory row holding the lead words beside the note the notes lane saves. The verbs of
    such leads are open-ended, so only the shape is read: at most five words in front of "that", the first of them
    not a pronoun or a determiner ("I think that ...", "the trouble is that ..." have a subject and are not leads).
    A noun subject ("Mum says that ...") reads as a lead too; that sentence is then simply not kept in passing."""
    if led_statement(clean) is not None:
        return True
    found = _THAT_LEAD_RE.match(clean)
    if found is None:
        return False
    words = found.group("lead").split()
    first = re.split(r"['’]", words[0].casefold())[0] if words else ""
    return 1 <= len(words) <= 5 and first not in _SUBJECT_STARTERS


def unlisted_statements(text: str) -> list[str]:
    """The sentences of a message that state something and are NOT personal facts by any shape `stated_facts` lists.

    Measured on the real app (an independent question set, the v0.8 line with the keep step): "lavender oil on the
    pillow eases my hay fever.", "a spoon of blackstrap molasses goes in my evening milk, nothing else." and "the
    coat with the torn lining goes to the tailor on mill street." were answered with a pleasant remark and no row
    was written; the later questions about them had nothing to find. No list of shapes covers how people mention
    such things, so whether one of these sentences is a lasting fact about the person is a judgement, made by the
    caller (`core.persistent_memory.keep_stated_facts`). This only reads: it returns the sentences that could be
    one, under the exclusions `stated_facts` already applies (a question, a request, a take-back, a change, pasted
    material, a made-up frame, a remark about this moment or about files) and two of its own (a sentence about one
    past occasion or a supposition; a sentence that opens with a lead), at most two of them."""
    whole = str(text or "")
    if not whole.strip() or len(whole) > 600 or "\n" in whole.strip():
        return []
    try:
        from core.stipulated_frame import has_stipulated_frame

        if has_stipulated_frame(whole):
            return []
    except Exception:
        return []
    listed = set(stated_facts(whole))
    found: list[str] = []
    for sentence in _SENTENCE_END_RE.split(" ".join(whole.split())):
        clean = _ASIDE_TAIL_RE.sub("", strip_continuation(sentence)).strip()
        if not clean or clean in listed or "?" in clean or is_question(clean):
            continue
        if _REQUEST_HEAD_RE.match(clean) or _RETRACTION_RE.search(clean) or update_shaped(clean):
            continue
        if not 4 <= len(clean.split()) <= 40 or _MOMENT_RE.search(clean) or _FILE_PLACE_RE.search(clean):
            continue
        if _ONE_OCCASION_RE.search(clean) or _opens_with_a_lead(clean):
            continue
        found.append(clean)
    return found[:2]


#: What a capture's object is when it only points back at something said before: "keep IT for me", "save THAT".
_POINTER_ONLY_RE = re.compile(
    r"^(?:it|that|this|them|those|these)\b(?:\s+(?:all|one|too|for\s+me|for\s+later|safe|somewhere(?:\s+safe)?|in\s+mind|"
    r"please|pls|plz|thanks|then|now|ok|okay))*[\s.!,]*$",
    re.IGNORECASE,
)


def points_back_only(fact: str) -> bool:
    """Whether what a capture names is only a pointer ("it for me", "that"): there is no fact in it to keep.

    Measured on the real app (94ce6169 and c3c3b8c2): "My phone unlock passcode is ..., keep it for me." refused the
    passcode and then saved the row "it for me." with "Locked in"."""
    return bool(_POINTER_ONLY_RE.match(" ".join(str(fact or "").split())))


def update_shaped(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean or is_question(clean) or _REQUEST_HEAD_RE.match(strip_continuation(clean)):
        return False
    return bool(_UPDATE_RE.search(clean) or _UPDATE_LEAD_RE.match(clean))


def update_body(text: str) -> str:
    return strip_continuation(_UPDATE_LEAD_RE.sub("", " ".join(str(text or "").split())))


def topic_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]+", str(text or "").casefold())
    return {w for w in words if len(w) > 2 and w not in _GENERIC and w not in _MONTHS}


#: A file, a folder or a place on the computer: "I moved the quarterly report pdf to the archive folder" is about where
#: a file is.
_FILE_PLACE_RE = re.compile(
    r"\b(?:files?|folders?|subfolders?|directory|directories|pdfs?|spreadsheets?|screenshots?|downloads|desktop|"
    r"documents\s+folder|icloud\s+drive|dropbox|google\s+drive|onedrive)\b"
    r"|\b[\w-]+\.(?:pdf|docx?|txt|md|csv|xlsx?|pages|numbers|key|png|jpe?g|zip)\b",
    re.IGNORECASE,
)


def _word_form(word: str) -> str:
    return re.sub(r"(?:'s|’s|s)$", "", word)


def _fact_holds(word: str, saved: str) -> bool:
    """Whether a saved fact holds a word of an update's subject, in any of its forms.

    The forms are the ones a question is matched by (core.chat_housekeeping), so the fact a question finds is the
    fact an update finds. Served on the first cut of step 3 (9f910ead): with "the chalet is now booked for 11
    september" saved, "the chalet booking changed again, it's on 13 september now" replaced nothing, because
    "booking" and "booked" were compared as written; a question about "our chalet booking" found the row."""
    if _word_form(word) in {_word_form(w) for w in topic_tokens(saved)}:
        return True
    try:
        from core.chat_housekeeping import _doc_stems, _term_in, query_terms

        stems = _doc_stems(saved)
        terms = query_terms(word)
        return bool(terms) and all(_term_in(term, stems) for term in terms)
    except Exception:
        return False


#: Who can stand in front of a change word without being what changed (the personal pronouns; a closed class).
_ACTOR_PRONOUNS = frozenset(["they", "them", "she", "her", "him", "you", "someone", "somebody", "people"])

#: "one" used for a thing already named ("the first one", "that one"), not as a count ("one stirrup leather").
_POINTS_BACK_RE = re.compile(
    r"\b(?:that|this|it|them|those|these)\b|\b(?:the|that|this|my|our|first|old|previous|original|other|same)\s+one\b",
    re.IGNORECASE,
)
_FIRST_VALUE_RE = re.compile(r"\b(?:first|original|very\s+first|earliest)\b", re.IGNORECASE)


#: What "my ..." names in a statement: "my old RATING is live again", "they recalculated my TABLE TENNIS RATING, ...".
_OWNED_PHRASE_RE = re.compile(
    r"\b(?:my|our)\s+(?P<phrase>[a-z][\w'’-]*(?:\s+[a-z][\w'’-]*){0,3}?)"
    r"(?=\s*[,.;:!?]|\s+(?:is|are|was|were|has|have|had|it'?s|it’s|now|again|back)\b|\s*$)",
    re.IGNORECASE,
)
#: Words of such a phrase that place a value in time and name nothing ("my OLD saddle rack").
_TIME_ADJECTIVES = frozenset(["old", "previous", "first", "original", "former", "usual", "new", "current", "last"])


#: A thing placed in time with "the": "the PREVIOUS desk", "the OLD rack". It refers to a thing already known.
_EARLIER_THING_RE = re.compile(
    r"\bthe\s+(?:old|previous|first|original|former|usual|last)\s+(?P<phrase>[a-z][\w'’-]*(?:\s+[a-z][\w'’-]*){0,3}?)"
    r"(?=\s*[,.;:!?]|\s+(?:is|are|was|were|has|have|had|it'?s|it’s|now|again|back)\b|\s*$)",
    re.IGNORECASE,
)


def owned_phrases(text: str) -> list[frozenset[str]]:
    """The things a statement calls the person's own, each as its words ("my old rating" -> {rating}), and the
    things it places in time ("the previous desk" -> {desk})."""
    found = []
    clean = " ".join(str(text or "").split())
    for match in [*_OWNED_PHRASE_RE.finditer(clean), *_EARLIER_THING_RE.finditer(clean)]:
        words = frozenset(
            _word_form(word) for word in re.findall(r"[a-z0-9][a-z0-9'’-]*", match.group("phrase").casefold())
            if word not in _TIME_ADJECTIVES and word not in _HEAD_DETERMINERS
        )
        if words:
            found.append(words)
    return found


def names_by_owned_phrase(text: str, fact: str) -> bool:
    """Whether a statement names a saved fact by a phrase of the person's own: every word of the phrase is in the
    fact ("my old rating" names "... a new table tennis rating this season: 1276"). The caller takes it only when
    exactly one fact is named."""
    return any(all(_fact_holds(word, fact) for word in phrase) for phrase in owned_phrases(text))


def may_revalue(text: str) -> bool:
    """Cheap test before the saved facts are read: a statement, one kind of typed value, and a thing of the
    person's own named in it ("they recalculated my table tennis rating, it's 1202 now")."""
    clean = " ".join(str(text or "").split())
    if not clean or len(clean) > 300 or is_question(clean) or _REQUEST_HEAD_RE.match(strip_continuation(clean)):
        return False
    return len(typed_values(clean)) == 1 and bool(owned_phrases(clean)) and not retracts_or_corrects(clean)


def revalued_entry(text: str, entries: list[dict]) -> dict | None:
    """The ONE saved entry this statement gives a new value for: it names the entry by a phrase of the person's own
    and gives a value of the kind the entry holds, with another value. None when no entry or more than one fits.

    `restates` needs both statements in the "<subject> is <value>" form; this reads a change told as a story."""
    clean = " ".join(str(text or "").split())
    if not may_revalue(clean):
        return None
    given = typed_values(clean)
    kind = next(iter(given))
    hits = []
    for row in entries:
        saved = " ".join(str(row.get("fact") or row.get("text") or "").split())
        held = typed_values(saved)
        if kind in held and given[kind].isdisjoint(held[kind]) and names_by_owned_phrase(clean, saved):
            hits.append(row)
    return hits[0] if len(hits) == 1 else None


def points_back(text: str) -> bool:
    """Whether a statement points at something said before instead of naming it ("scrap that", "the first one")."""
    return bool(_POINTS_BACK_RE.search(" ".join(str(text or "").split())))


def restore_cue(text: str) -> bool:
    """Whether a statement says, in the words the capsule marking already reads, that an earlier value is back
    ("I'm back on my old saddle rack", "I went back to the previous desk"). Other wordings are judged by a model
    where this is used (core.persistent_memory); neither decides WHICH value is back: the stored history does."""
    try:
        from core.updated_fact_marking import _REVERT_CUE_RE

        return bool(_REVERT_CUE_RE.search(" ".join(str(text or "").split())))
    except Exception:
        return False


def restore_wants_first(text: str) -> bool:
    """"the first one", "my original desk": the first value of the history, not the one before the current."""
    return bool(_FIRST_VALUE_RE.search(" ".join(str(text or "").split())))


def may_restore(text: str) -> bool:
    """Cheap test before the saved facts are read: a short message that gives no new value of its own. A question
    may ride behind the statement ("... my old rating is live again - what's my rating?")."""
    clean = " ".join(str(text or "").split())
    if not clean or len(clean) > 300 or "\n" in str(text or "").strip() or _REQUEST_HEAD_RE.match(strip_continuation(clean)):
        return False
    return not typed_values(clean) and len(clean.split()) >= 3


def names_subject(text: str, fact: str) -> bool:
    """Whether a statement names the subject of a saved fact: every word of the fact's subject ("my saddle rack")
    is in the statement. One shared word is not the subject ("door code" names neither the scout hut's nor the
    bike store's)."""
    head, _ = _head_and_rest(fact)
    return bool(head) and all(_fact_holds(word, text) for word in head)


def restored_body(previous: str, current: str) -> str:
    """What a restore writes back: the earlier row as it stood. Whatever else that row held was carried into the
    current row when it was changed, so the earlier row whole is the state before the change. (Measured on the
    first cut of this step: writing back only "the changed fact" and keeping the current row's other parts left
    "my saddle rack is number 58; they moved my saddle rack".)"""
    return " ".join(str(previous or "").split()).rstrip(". ")


def updated_entry(text: str, entries: list[dict]) -> dict | None:
    """The one saved entry an update statement replaces: the most shared topic words, newest on a tie.

    A statement about a file or folder replaces only a saved fact about a file or folder: with "the quarterly report
    is due on the 3rd" saved, "I moved the quarterly report pdf to the archive folder" replaced the due date (the clean
    loop hands everyday updates to this lane before it reads files)."""
    body = update_body(text)
    wanted = topic_tokens(body)
    if not wanted:
        return None
    about_files = bool(_FILE_PLACE_RE.search(text))
    # What the update is about: the words before its change word ("the STREET PARTY moved to saturday"). Every one of
    # them must be in the fact it replaces: with "my dentist is dr Okafor on Brill street" saved, the street party
    # replaced the dentist on the one word "street" (independent review of job 19, C6).
    # A statement may open with a change phrase of its own ("Change of plan: my pottery class login password is
    # now ..."): the subject stands before the first change word that has any words before it. Read from the
    # opening phrase the subject was empty, and the statement replaced "my pottery class is on Mondays" on the two
    # words they share (served base 94ce6169 and the first cut of step 3).
    subject: set[str] = set()
    said_from = 0
    for cue in _UPDATE_RE.finditer(body):
        # Who made the change is not what changed: in "they moved my saddle rack, it's number 73 now" the words
        # before the change word are a pronoun, and the thing stands behind it. Read as the subject, "they" was
        # required of the saved fact, no fact held it, and the update was dropped (measured on the real app).
        subject = topic_tokens(body[said_from: cue.start()]) - _ACTOR_PRONOUNS
        if subject:
            break
        said_from = cue.end()
    if not subject:
        # No change word names what changed ("update: my sailboat is called noodle now"): the thing it is about is
        # the first word it says about anything. Scored on shared words alone, it replaced "my turtle is called
        # Ziggy" on the one word "called" (V-128, function-level probe on fe98e849d and 261a03892).
        first = next((word for word in re.findall(r"\w+", body.lower()) if word in wanted), "")
        subject = {first} if first else set()
    best, best_score = None, 0
    for row in entries:
        saved = str(row.get("fact") or row.get("text") or "")
        if about_files and not _FILE_PLACE_RE.search(saved):
            continue
        if subject and not all(_fact_holds(word, saved) for word in subject):
            continue
        score = len(wanted & topic_tokens(saved))
        if score and score >= best_score:
            best, best_score = row, score
    return best


# --- one fact of a saved row, and the value it holds ----------------------------------------------------------
#
# Measured on the served base (94ce6169, a real local model): "Remember that my pottery class is on Wednesdays and
# the teacher is called Ottoline", then "Small change: my pottery class is on Saturdays now", was stored as "my
# pottery class is on Saturdays now (this replaces: my pottery class is on Wednesdays and the teacher is called
# Ottoline)": the whole row was replaced as a unit and its second fact lived on only inside the tail. An update
# speaks about one fact of a row; the others stay current.

#: Where a saved row holds more than one fact: a semicolon, a sentence end, a comma ("... for 4-7 june, booking code
#: BR-5527"), or an "and" whose right side opens a clause of its own ("... on Wednesdays and the teacher is called
#: Ottoline"; not "bread and milk"). A piece of one or two words is no fact of its own and stays with the one before.
_CLAUSE_SPLIT_RE = re.compile(
    r"\s*;\s*|(?<=[.!?])\s+|,\s+(?!and\b)"
    r"|,?\s+and\s+(?=(?:my|our|the|her|his|their|its|it|she|he|they|we|i)\b(?:\s+\S+){2,})",
    re.IGNORECASE,
)


def fact_clauses(text: str) -> list[str]:
    """The facts one saved row holds, in order."""
    clean = " ".join(str(text or "").split())
    parts: list[str] = []
    for piece in _CLAUSE_SPLIT_RE.split(clean):
        piece = piece.strip(" .;")
        if not piece:
            continue
        if parts and len(piece.split()) < 3:
            parts[-1] = f"{parts[-1]} {piece}"  # a fragment is not a fact of its own
        else:
            parts.append(piece)
    return parts


def _forms(text: str) -> set[str]:
    return {_word_form(word) for word in topic_tokens(text)}


def clauses_kept(saved: str, update: str) -> list[str]:
    """The facts of a saved row that an update does not speak about, in order: they stay current.

    Which fact the update speaks about is read in this order, and the first reading that names one decides:
    the fact it gives a new value for (`restates`); the facts holding a value of the kind the update gives, with
    another value ("the boat dates changed, its now 11-14 june, same booking code" replaces "... for 4-7 june" and
    leaves "booking code BR-5527"); the fact whose subject one of the update's own parts names ("my vet moved, THE
    SURGERY is on Orchard row now" replaces "her surgery is on Pell lane" and leaves "my vet is Dr Halloran"); the
    fact it shares the most topic words with (every fact that ties). A row of one fact, or an update that shares no
    word with any single fact of the row, is replaced whole."""
    parts = fact_clauses(saved)
    if len(parts) < 2:
        return []
    plain = strip_continuation(update_body(update))
    given = typed_values(plain)
    kind = next((name for name, _ in _VALUE_KINDS if name in given), "")
    named = {head for head in (_head_and_rest(piece)[0] for piece in _UPDATE_PART_RE.split(plain)) if head}
    for spoken in (
        [restates(plain, part) for part in parts],
        [bool(kind) and kind in typed_values(part) and given[kind].isdisjoint(typed_values(part)[kind]) for part in parts],
        [bool(_head_and_rest(part)[0] and _head_and_rest(part)[0] in named) for part in parts],
    ):
        if any(spoken):
            return [part for part, hit in zip(parts, spoken, strict=False) if not hit]
    wanted = _forms(update_body(update))
    scores = [len(wanted & _forms(part)) for part in parts]
    top = max(scores)
    if top <= 0:
        return []
    return [part for part, score in zip(parts, scores, strict=False) if score < top]


#: The kinds of value a plain statement gives, most specific first. A value's kind is a closed class (the seven
#: days, the twelve months, a clock time, a number), not a cue word: it says what sort of thing a fact holds.
_VALUE_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("weekday", re.compile(r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)days?\b", re.IGNORECASE)),
    ("date", re.compile(
        r"\b\d{1,2}(?:\s*[-–]\s*\d{1,2})?(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
        r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b",
        re.IGNORECASE,
    )),
    ("clock", re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b|\b(?:noon|midday|midnight)\b", re.IGNORECASE)),
    # A number standing alone: not the digits of a code ("BR-5527") or the second half of a range.
    ("number", re.compile(r"(?<![\w.\-–])\d+(?:[.,]\d+)?(?![\w])")),
)
_HEAD_DETERMINERS = frozenset(["my", "our", "the", "a", "an", "this", "that", "her", "his", "their", "its"])
#: The parts of one update statement: "my vet moved | the surgery is on Orchard row now".
_UPDATE_PART_RE = re.compile(r"\s*[,;]\s*|(?<=[.!?])\s+|\s+and\s+", re.IGNORECASE)
#: Where the subject of a plain statement ends: "my pottery class | is on Saturdays".
_HEAD_END_RE = re.compile(r"\s(?:is|are|was|were|will)\b|(?:'s|’s)\s", re.IGNORECASE)


def typed_values(text: str) -> dict[str, frozenset[str]]:
    """{kind: values} for the values a statement gives. A span read as one kind is not read again as another
    (the "3" of "3 July" is a date, not a number)."""
    rest = " ".join(str(text or "").split())
    found: dict[str, frozenset[str]] = {}
    for kind, pattern in _VALUE_KINDS:
        values = frozenset(re.sub(r"s$", "", " ".join(m.group(0).casefold().split())) for m in pattern.finditer(rest))
        if values:
            found[kind] = values
            rest = pattern.sub(" ", rest)
    return found


def _without_values(text: str) -> str:
    rest = str(text or "")
    for _, pattern in _VALUE_KINDS:
        rest = pattern.sub(" ", rest)
    return rest


def _head_and_rest(clause: str) -> tuple[frozenset[str], frozenset[str]]:
    clean = strip_continuation(" ".join(str(clause or "").split()))
    end = _HEAD_END_RE.search(clean)
    if end is None:
        return frozenset(), frozenset()
    # Every word of the subject counts ("my flight back" is not "my flight"); only the determiner is dropped.
    head = {
        _word_form(word)
        for word in re.findall(r"[a-z0-9][a-z0-9'’-]*", clean[: end.start()].casefold())
        if word not in _HEAD_DETERMINERS
    }
    return frozenset(head), frozenset(_forms(_without_values(clean[end.end():])))


def restates(statement: str, saved_fact: str) -> bool:
    """Whether a plain statement gives a new value for the thing a saved fact is about: the same subject ("my
    pottery class"), a value of the same kind (a weekday, a date, a clock time, a number), and a different value.

    "My pottery class is moving to Mondays" restates "my pottery class is on Saturdays now"; "my sister is a nurse"
    does not restate "my sister is 34", and "my flat is 80 square metres" does not restate "my flat is number 12"
    (both say something else about the subject). The later statement is then the current one. No change word is
    needed, and none decides."""
    head, said = _head_and_rest(statement)
    saved_head, saved_said = _head_and_rest(saved_fact)
    if not head or head != saved_head:
        return False
    if said and saved_said and said.isdisjoint(saved_said):
        return False
    new, old = typed_values(statement), typed_values(saved_fact)
    for kind, _ in _VALUE_KINDS:
        if kind in new or kind in old:
            return kind in new and kind in old and new[kind].isdisjoint(old[kind])
    return False


def may_restate(text: str) -> bool:
    """Cheap test before the saved facts are read: one plain statement that gives a typed value."""
    clean = " ".join(str(text or "").split())
    if not clean or len(clean) > 300 or is_question(clean) or _REQUEST_HEAD_RE.match(strip_continuation(clean)):
        return False
    if len(fact_clauses(clean)) != 1 or retracts_or_corrects(clean):
        return False
    return bool(_head_and_rest(clean)[0]) and bool(typed_values(clean))


def restated_entry(text: str, entries: list[dict]) -> dict | None:
    """The saved entry holding a fact this statement restates (newest on a tie), else None."""
    clean = " ".join(str(text or "").split())
    if not may_restate(clean):
        return None
    best = None
    for row in entries:
        saved = str(row.get("fact") or row.get("text") or "")
        if any(restates(clean, part) for part in fact_clauses(saved)):
            best = row
    return best


