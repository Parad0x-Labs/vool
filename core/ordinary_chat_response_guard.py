"""Validate ordinary chat output before it can become conversational memory.

This is intentionally a narrow, structural detector.  It does not prescribe an
answer and it does not constrain explicit creative-media requests.  It only
rejects clearly image-prompt-shaped output from a normal text-chat turn.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

_CAMERA_SIGNALS = re.compile(
    r"\b(?:camera|lens|shot|framing|depth of field|aspect ratio|close-up|wide shot)\b",
    re.IGNORECASE,
)
_STYLE_SIGNALS = re.compile(
    r"\b(?:lighting|colour grade|color grade|cinematic|photorealistic|film grain|bokeh|8k|4k)\b",
    re.IGNORECASE,
)
_SCAFFOLD_SIGNALS = re.compile(
    r"(?:^|\n)\s*(?:subject|camera|lighting|negative prompt|style|composition|tags)\s*:",
    re.IGNORECASE,
)
_PROMPT_DETAIL_SIGNALS = re.compile(
    r"\b(?:35mm|50mm|85mm|film grain|negative prompt|tags?|aspect ratio|"
    r"depth of field|bokeh|color palette|colour palette|shot list)\b",
    re.IGNORECASE,
)
_LIST_LINE_RE = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+")
_EXPLANATORY_HEADING_LINE_RE = re.compile(
    r"(?m)^\s*(?:[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3})\s*:\s+"
)
_CONTRAST_TOPIC_RE = re.compile(
    r"(?:^|[.!?]\s+)(?P<topic>[A-Z][\w-]*(?:\s+[A-Za-z][\w-]*){0,3}),\s+"
    r"on the other hand\b",
)
_UNREQUESTED_COMPARISON_RE = re.compile(
    r"\b(?:just like|similar to|unlike)\s+(?:a|an|the)?\s*(?P<topic>[A-Za-z][\w-]*)\b",
    re.IGNORECASE,
)
_EXPLICIT_PRIOR_ANSWER_FOLLOW_UP_RE = re.compile(
    r"\b(?:what|which|how)\b[^.!?]{0,80}\b(?:"
    r"you\s+(?:just\s+)?(?:gave|mentioned|said|explained|described)"
    r"|that\s+(?:answer|explanation|reason|detail)"
    r"|(?:there|above|earlier|before))\b",
    re.IGNORECASE,
)
_SCOPED_MEMORY_RECALL_RE = re.compile(
    r"\b(?:"
    r"(?:what|which|where|when)\b[^.!?]{0,100}\b(?:"
    r"identifier|marker|code|label|date|port|number|phrase|value|fact|token"
    r")\b|"
    r"(?:repeat|recall|remember|remind\s+me|tell\s+me|what\s+did\s+i)\b"
    r"[^.!?]{0,100}\b(?:"
    r"identifier|marker|code|label|date|port|number|phrase|value|fact|token"
    r")\b|"
    r"\b(?:current|active|latest|exact)\s+(?:"
    r"identifier|marker|code|label|date|port|number|phrase|value|fact|token"
    r")\b"
    r")",
    re.IGNORECASE,
)
_MEMORY_RECALL_DENIAL_RE = re.compile(
    r"\b(?:old|previous|prior|superseded|outdated)\b|"
    r"\b(?:without|do\s+not|don't|never)\s+repeating?\b|"
    r"\b(?:not|never)\s+(?:repeat|reuse|use)\b",
    re.IGNORECASE,
)
_FORGET_COMMAND_RE = re.compile(
    r"^\s*(?:forget|erase)\s+(?P<target>.+?)\s*$",
    re.IGNORECASE,
)
_CONFIRMATION_LITERAL_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9_]*-[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b"
)
_TYPED_MEMORY_DECLARATION_RE = re.compile(
    r"\b(?:remember|store|save|note|the)\b"
    r"[^.!?\n]{0,80}?\b(?:identifier|code|marker|label|port|number|phrase|value)\b"
    r"(?:\s+for\b[^:,.!?]{0,40})?\s*(?:is|=|:|was|became|becomes|now)\s*"
    r"(?P<value>[A-Z0-9][A-Za-z0-9_-]*(?:\s+[A-Z0-9][A-Za-z0-9_-]*){0,5}?)"
    r"(?=\s*(?:[.!?]|$))",
    re.IGNORECASE,
)
_OPAQUE_MULTIWORD_VALUE_RE = re.compile(
    r"\b[A-Z]{2,}(?:\s+(?:\d{1,6}|[A-Z]{2,})){1,4}\b"
)
#: A single opaque error/status code token joined by underscores. The multiword pattern above
#: needs spaces and the confirmation pattern needs a hyphen+digit, so ERR_AWS_DENIED, ERR_NZMDFS
#: and SEC_DENIED_0x9F -- the exact literals that leaked across turns on the served surface --
#: matched NEITHER and were never even extracted. Deliberately conservative: a code-y prefix OR a
#: digit after an underscore, so a leaked status code is caught but an ordinary all-caps constant
#: (API_KEY, MAX_SIZE, HTTP_OK, NEW_YORK) is not -- those legitimately appear in code answers.
_OPAQUE_CODE_TOKEN_RE = re.compile(
    r"\b(?:"
    r"(?:ERR|ERROR|SEC|WARN|FATAL|STATUS|CODE|DENIED|FAIL|EXC)_[A-Za-z0-9_]{2,}"
    r"|[A-Z][A-Za-z0-9]*_[A-Za-z0-9]*\d[A-Za-z0-9_]*"
    r")\b"
)
_CONFIRMATION_REQUEST_RE = re.compile(
    r"\b(?:confirm|repeat|echo)\b",
    re.IGNORECASE,
)
_GENERIC_FOLLOW_UP_RE = re.compile(
    r"\b(?:let me know if you need|feel free to ask|if you have any other questions|"
    r"i can also help|would you like me to|don't hesitate to ask|"
    r"do you relate to|does that resonate(?: with you)?|"
    r"how can i (?:help|assist)(?: you)?(?: today)?|"
    r"do you have any (?:specific )?(?:topics?|questions?)(?: in mind)?|"
    r"what (?:would|do) you like to (?:discuss|do|work on|talk about|explore))\b",
    re.IGNORECASE,
)
_ASSISTANCE_REQUEST_RE = re.compile(
    r"\b(?:what|how)\b[^.!?]{0,50}\b(?:help|assist|support|can you do)\b"
    r"|\bwhat can you do\b",
    re.IGNORECASE,
)
#: An explanation question: any "why", or a "how" that does not ask for a quantity. "How many /
#: much / long / old / often / far" asks for an amount, and one word ("Twice.", "Forty.") answers it.
#: Each "how" is classified where it stands. The quantity exemption used to be anchored to the start
#: of the whole message, so any lead-in before the question ("Quick one, how many...", an instruction
#: line, a greeting) turned an amount question into an explanation question and a correct one-word
#: answer was withdrawn as too short (measured 2026-10-06: "Twice." to "How many times have I met up
#: with Alex?" after an instruction line ended as "I couldn't produce a normal chat response").
_OPEN_EXPLANATION_QUESTION_RE = re.compile(
    r"\bwhy\b|\bhow\b(?!\s+(?:many|much|long|old|often|far)\b)",
    re.IGNORECASE,
)
#: The sentence a one-word reply answers: the last one ending in "?", else the last sentence.
_QUESTION_SENTENCE_RE = re.compile(r"[^.!?]*\?")
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
_EXPLICIT_WORD_SHAPE_RE = re.compile(
    r"\b(?:one|two|three|\d+)\s*[- ]?words?\b",
    re.IGNORECASE,
)
_DETAIL_REQUEST_RE = re.compile(
    r"\b(?:detailed|in detail|step[- ]by[- ]step|steps|list|examples|thorough|"
    r"compare|pros and cons|explain fully|deep dive)\b",
    re.IGNORECASE,
)
#: A stated length or long-form structure is an explicit CONTRACT, and it outranks the brevity
#: default the same way every other stated output contract outranks a heuristic. Measured live
#: 2026-08-15 (MF-12): "write me a 500-word article: age, history, funny facts..." matched no
#: detail keyword, so the 64-word ceiling applied and the runtime shipped TWO sentences with no
#: disclosure. Multi-digit word counts only: "two words"/"one word" is the SHORT exact contract
#: (_EXPLICIT_WORD_SHAPE_RE) and keeps its own handling.
_STATED_LENGTH_REQUEST_RE = re.compile(
    r"\b\d{2,5}\s*[- ]?words?\b"
    r"|\b(?:\d+|two|three|four|five|several|multiple)\s+paragraphs?\b"
    r"|\b(?:an?\s+)?(?:article|essay|blog\s+post|write[- ]?up|long[- ]?form|full\s+(?:report|"
    r"breakdown|explanation|analysis|summary|guide)|comprehensive\b|in[- ]depth)"
    r"|\bat\s+least\s+\d{2,5}\s*(?:words?|characters?|sentences?)\b",
    re.IGNORECASE,
)


_SHORT_WORD_COUNT_RE = re.compile(
    r"\b(?:(?:exactly|only|in|within|at\s+most|maximum(?:\s+of)?|no\s+more\s+than|under)\s+"
    r"(?P<bounded>one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+words?"
    r"|(?P<shape>one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s*[- ]\s*word\s+"
    r"(?:answer|reply|response|summary)"
    r"|(?:answer|reply|respond|summari[sz]e)[ \t]+with[ \t]+"
    r"(?P<with_count>one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})[ \t]+words?)\b"
    r"(?![ \t]+(?:per|for[ \t]+each|for[ \t]+every)\b)",
    re.IGNORECASE,
)
_SHORT_RESPONSE_RE = re.compile(
    r"\b(?:briefly|concisely)\b"
    r"|\b(?:keep|make)\s+(?:(?:it|the\s+(?:answer|reply|response|summary))\s+)?"
    r"(?:brief|short|concise)\b"
    r"|\b(?:in|within|with|to|use|only)\s+(?:one|a\s+single)\s+sentence\b"
    r"(?![ \t]+(?:per|for[ \t]+each|for[ \t]+every)\b)",
    re.IGNORECASE,
)
#: The wider grammar of an explicit length request: the user asks for a short, brief or concise
#: ANSWER (a request verb or a "please"/"only" on the answer noun), asks the assistant to be brief,
#: asks for just the answer, or names a sentence/phrase/few-words shape for the reply. Read from
#: the grammar of the request, never from its topic: "Is the short notebook on the rack?" names no
#: answer noun and no request verb and stays outside it. Measured on a fresh paid probe: every
#: reader turn ended "Give a concise final answer.", none matched `_SHORT_RESPONSE_RE`, and the
#: shipped answers ran a median 42 words against 1-6 word gold answers.
# Common user misspellings are part of the grammar ("breif", "consise").
_BREVITY_ADJ = r"(?:short|brief|breif|concise|consise|terse|succinct|quick|one[- ]line)"
_ANSWER_NOUN = r"(?:final\s+)?(?:answer|reply|response|version|summary|explanation)"
_EXPLICIT_BREVITY_REQUEST_RE = re.compile(
    # "Give a concise final answer.", "I want a short reply", "provide a brief response"
    r"\b(?:give|send|provide|write|offer|return|want|need|prefer|like)\s+(?:me\s+|us\s+)?"
    r"(?:(?:a|an|the|your|just\s+a)\s+)?(?:very\s+|really\s+|nice\s+(?:and\s+)?)?"
    + _BREVITY_ADJ + r"\s+" + _ANSWER_NOUN + r"\b"
    # "Short answer please", "a concise answer, please", "brief answer only"
    r"|\b" + _BREVITY_ADJ + r"\s+" + _ANSWER_NOUN
    + r"\s*[,:]?\s*(?:please|pls|plz|only|is\s+(?:fine|enough|ok(?:ay)?))\b"
    # "Be brief.", "please be concise"
    r"|\bbe\s+(?:very\s+|really\s+)?(?:brief|breif|concise|consise|terse|succinct)\b"
    # "keep your answer short", "keep this tight", "make the reply succinct"
    r"|\b(?:keep|make)\s+(?:it|this|that|your\s+(?:answer|reply|response)"
    r"|the\s+(?:answer|reply|response|summary))\s+(?:very\s+|really\s+)?"
    r"(?:brief|short|concise|terse|succinct|tight)\b"
    r"|\b(?:succinctly|tersely)\b"
    # "Just the answer.", "just give me the final answer", "only the answer, please"
    r"|\b(?:just|only)\s+(?:give\s+(?:me\s+)?|tell\s+me\s+|need\s+|want\s+)?the\s+"
    r"(?:final\s+|short\s+|bare\s+)?answer\b"
    # "answer in a sentence", "reply in a few words", "tell me in brief"
    r"|\b(?:answer|reply|respond|explain|summari[sz]e|tell\s+me|describe|say)\b[^.!?\n]{0,40}?"
    r"\bin\s+(?:a\s+(?:single\s+)?(?:sentence|line|phrase)|a\s+few\s+words|brief)\b"
    r"(?![ \t]+(?:per|for[ \t]+each|for[ \t]+every)\b)"
    r"|\bin\s+as\s+few\s+words\s+as\s+possible\b"
    r"|\bno\s+(?:explanations?|elaboration|details)\s+(?:needed|necessary|required|please)\b",
    re.IGNORECASE,
)
#: "I don't want a short answer", "no need to be brief": a negated length request is not one.
_NEGATED_BREVITY_PREFIX_RE = re.compile(
    r"\b(?:not|don't|dont|do\s+not|never|no\s+need\s+to|rather\s+than|instead\s+of|without)"
    r"(?:\s+[\w']+){0,2}\s*$",
    re.IGNORECASE,
)


#: "Did Marta give a brief answer?", "should the clerk want a short reply": an auxiliary with a
#: third-party subject before the request verb reports someone else's answer, it asks for none.
#: "can you give me a short answer" keeps the request (the subject is the assistant).
_REPORTED_ANSWER_PREFIX_RE = re.compile(
    r"\b(?:did|does|do|didn't|doesn't|don't|will|would|should|could|can|has|have|had)\s+"
    r"(?!(?:you|u)\b)[\w']+(?:\s+[\w']+)?\s*$",
    re.IGNORECASE,
)
_REQUEST_VERB_START_RE = re.compile(
    r"(?:give|send|provide|write|offer|return|want|need|prefer|like)\b", re.IGNORECASE
)


def _explicit_brevity_request(text: str) -> bool:
    for match in _EXPLICIT_BREVITY_REQUEST_RE.finditer(text):
        prefix = text[max(0, match.start() - 40): match.start()]
        if _NEGATED_BREVITY_PREFIX_RE.search(prefix):
            continue
        if _REQUEST_VERB_START_RE.match(match.group(0)) and _REPORTED_ANSWER_PREFIX_RE.search(prefix):
            continue
        return True
    return False


_LENGTH_DIRECTIVE_RESIDUE_RE = re.compile(
    r"^(?:(?:please|pls|plz|just|only|and|so|ok(?:ay)?|thanks|thank\s+you|answer|reply|respond)"
    r"\b[\s,]*)*$",
    re.IGNORECASE,
)


def is_answer_length_directive(clause: str) -> bool:
    """Whether a clause ONLY states the answer's length ("Keep it short.", "Give a concise answer.").

    Such a clause shapes the other requests; it is not a request part of its own. Counted as a
    part, "Q1? Q2? Give a short answer." demanded three answers and rejected a complete two-part
    reply; failing the request-head test, "Q1? Q2? Keep it short." dropped the two-part contract
    altogether. A clause that carries its own request ("Explain the tides briefly") is not one.
    """
    text = " ".join(str(clause or "").split()).strip(" \t.!?;:,")
    if not text or explicit_short_output_max_words(text) is None:
        return False
    residue = text
    for pattern in (_SHORT_WORD_COUNT_RE, _SHORT_RESPONSE_RE, _EXPLICIT_BREVITY_REQUEST_RE):
        residue = pattern.sub(" ", residue)
    return bool(_LENGTH_DIRECTIVE_RESIDUE_RE.match(residue.strip(" \t.!?;:,")))


def explicit_short_output_max_words(user_text: str) -> int | None:
    """The explicit shorter delivery contract outranks a complete collection."""
    text = str(user_text or "")
    counts = []
    number_words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    }
    for match in _SHORT_WORD_COUNT_RE.finditer(text):
        value = str(match.group("bounded") or match.group("shape") or match.group("with_count")).lower()
        count = number_words.get(value) if value in number_words else int(value)
        if 0 < count <= _DEFAULT_ORDINARY_CHAT_MAX_WORDS:
            counts.append(count)
    if counts:
        return min(counts)
    return (
        _DEFAULT_ORDINARY_CHAT_MAX_WORDS
        if _SHORT_RESPONSE_RE.search(text) or _explicit_brevity_request(text)
        else None
    )


#: An open request for advice is a request KIND, read from its grammar and never from its topic:
#: the user asks the assistant to suggest / recommend / advise, asks for suggestions, tips,
#: ideas, pointers or advice as the object of a request, or asks what they should do. Such an
#: answer is only useful when it fits the person, and the 64-word ceiling measurably cut exactly
#: those details (holdout preference drafts: user-evidence words 82 -> 28 -> 20 from draft to
#: delivered answer). It gets a bounded detail budget instead of the unbounded detail contract.
# Common user spellings are part of the grammar ("sugestions", "recomend", "can u suggest").
_ADVICE_VERB = r"(?:sugg?est|rec+om+end|advise)"
_ADVICE_NOUN = r"(?:sugg?estions?|rec+om+endations?|recs|tips|advice|ideas|pointers)"
_OPEN_ADVICE_REQUEST_RE = re.compile(
    # "any suggestions?", "any helpful tips on ...", "got any podcast recommendations"
    r"\bany\s+(?:[a-z][\w'-]*\s+){0,2}?" + _ADVICE_NOUN + r"\b"
    # "can you suggest ...", "could you please recommend ...", "would you advise ..."
    r"|\b(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+|pls\s+)?" + _ADVICE_VERB + r"\b"
    # an imperative at the start of a clause: "Please recommend ...", "Suggest ..."
    r"|(?:^|[.!?;,]\s*)(?:please\s+|pls\s+|plz\s+)?(?:sugg?est|rec+om+end)\b"
    # the advice noun as the object of a request: "give me some tips", "I need some advice on"
    r"|\b(?:give|offer|share|send)\s+me\s+(?:some\s+|any\s+|a\s+few\s+|your\s+)?"
    r"(?:[a-z][\w'-]*\s+){0,2}?" + _ADVICE_NOUN + r"\b"
    r"|\b(?:i\s+(?:need|want|would\s+like)|i'd\s+like|looking\s+for|in\s+need\s+of)\s+"
    r"(?:some\s+|any\s+|a\s+few\s+|your\s+)?(?:[a-z][\w'-]*\s+){0,2}?" + _ADVICE_NOUN + r"\b"
    # a decision the user asks the assistant to weigh in on
    r"|\b(?:what|which)\s+(?:[a-z][\w'-]*\s+){0,3}?should\s+i\b"
    r"|(?:^|[.!?;]\s+)should\s+i\b"
    r"|\b(?:do\s+you\s+think|would\s+it\s+be|is\s+it)\s+(?:it\s+would\s+be\s+)?"
    r"(?:a\s+good\s+idea|worth\s+it)\b"
    r"|\bdo\s+you\s+think\s+i\s+should\b",
    re.IGNORECASE,
)
#: A cardinal object right after the request verb ("recommend 8 novels") is the user's own item
#: count; the advice item bound must not override it.
_STATED_ADVICE_COUNT_RE = re.compile(
    r"\b(?:sugg?est|rec+om+end|give\s+me|list|name)\s+(?:me\s+)?(?:about\s+|around\s+)?"
    r"(?:\d{1,3}|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)\b",
    re.IGNORECASE,
)
_ADVICE_MAX_WORDS = 140
_ADVICE_MAX_ITEMS = 5


def _is_open_advice_request(user_text: str) -> bool:
    text = " ".join(str(user_text or "").split())
    return bool(
        text
        and _OPEN_ADVICE_REQUEST_RE.search(text)
        and not _STATED_ADVICE_COUNT_RE.search(text)
    )


def _length_contract_lifts_ceiling(user_text: str) -> bool:
    from core.context_retrieval import query_requests_complete_collection
    from core.response_constraints import parse_clause_response_constraint, requested_output_item_count

    if explicit_short_output_max_words(user_text) is not None:
        return False
    constraint = parse_clause_response_constraint(str(user_text or ""))
    # A structured output has its own shape/length authority. Cutting it at an
    # ordinary-prose word boundary can destroy a row or a closing code fence.
    return bool(
        _DETAIL_REQUEST_RE.search(str(user_text or ""))
        or _STATED_LENGTH_REQUEST_RE.search(str(user_text or ""))
        or (constraint is not None and constraint.presentation_format)
        or query_requests_complete_collection(user_text)
        or requested_output_item_count(user_text) is not None
    )
# A multiple-choice option label: "(a)", "a)", "A." or "1)" at the start of the text or after
# whitespace. Each label opens one option, running to the next label.
_CHOICE_LABEL_RE = re.compile(r"(?:^|(?<=\s))(?:\(([A-Ha-h]|[1-8])\)|([A-Ha-h]|[1-8])[).])(?=\s)")
# An option whose own text says the asked information is absent from the source.
_NOT_MENTIONED_OPTION_RE = re.compile(
    r"^\W*(?:(?:it\s+is|it's|this\s+(?:is|was))\s+)?(?:"
    r"(?:not|never)\s+(?:mentioned|stated|specified|discussed|recorded|given|said|provided|indicated|known)"
    r"|no\s+(?:information|mention|record)"
    r"|(?:not\s+enough|insufficient)\s+information"
    r"|cannot\s+be\s+determined|can(?:'|\u2019|no)t\s+be\s+determined"
    r"|unknown\b)",
    re.IGNORECASE,
)


def offers_not_mentioned_option(user_text: str) -> bool:
    """True when the turn offers labelled answer options and one says the fact is not mentioned.

    Two or more options in label order are required, so prose that happens to contain "a)" or
    "not mentioned" is not a multiple-choice question.
    """
    text = str(user_text or "")
    labels = list(_CHOICE_LABEL_RE.finditer(text))
    if len(labels) < 2:
        return False

    def ordinal(match: re.Match[str]) -> int:
        label = (match.group(1) or match.group(2) or "").lower()
        return int(label) if label.isdigit() else ord(label) - ord("a") + 1

    ordinals = [ordinal(match) for match in labels]
    if ordinals[0] != 1 or ordinals[1] != 2:
        return False
    for index, match in enumerate(labels):
        if ordinals[index] != index + 1:
            break
        end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
        option = text[match.end():end].strip()
        if _NOT_MENTIONED_OPTION_RE.search(option) or _typed_not_mentioned_option(option):
            return True
    return False


# Typed forms of a not-mentioned option the pattern above misses: a contracted or run-together lead
# ("isnt mentioned", "its not stated"), a hedge ("not explicitly mentioned"), one typing slip in a
# long participle ("not mentiond", "not specifed"), "not in the records" or "nowhere in the chats",
# "no info", or "don't know". A short participle must be exact: "not started" is no slip of "not
# stated".
_OPTION_WORD_RE = re.compile(r"[^\W\d_][\w'’]*")
_OPTION_LEAD_WORDS = frozenset({"it", "its", "it's", "this", "that", "is", "was", "info", "information"})
_OPTION_NEGATIONS = frozenset({"not", "never", "nowhere", "isnt", "isn't", "wasnt", "wasn't"})
_NO_INFORMATION_NOUNS = frozenset({
    "info", "information", "mention", "record", "records", "data", "details", "evidence",
})
# What may follow "no info": "no information given", "no record of it", "no mention in the chats";
# anything else makes it a factual option ("no data plan").
_NO_INFORMATION_TAIL = frozenset({
    "given", "provided", "available", "found", "mentioned", "stated", "recorded", "about", "of",
    "on", "in", "from", "the", "it", "this", "that", "any", "at", "all", "here", "there",
    "records", "record", "conversation", "conversations", "chat", "chats", "notes", "messages",
    "text", "transcript", "transcripts",
})
_OPTION_HEDGES = frozenset({
    "been", "ever", "explicitly", "directly", "clearly", "really", "specifically", "actually",
    "anywhere", "even",
})
_NOT_MENTIONED_PARTICIPLES = frozenset({
    "mentioned", "stated", "specified", "discussed", "recorded", "given", "said", "provided",
    "indicated", "known",
})
_RECORD_PLACE_NOUNS = frozenset({
    "records", "record", "conversation", "conversations", "chat", "chats", "notes", "messages",
    "text", "transcript", "transcripts",
})


def _typed_not_mentioned_option(option: str) -> bool:
    words = [word.lower().replace("’", "'") for word in _OPTION_WORD_RE.findall(option)]
    index = 0
    while index < min(2, len(words)) and words[index] in _OPTION_LEAD_WORDS:
        index += 1
    if words[index:index + 1] == ["i"]:
        index += 1
    following = words[index + 1:index + 3]
    if words[index:index + 1] in (["don't"], ["dont"]) and following[:1] == ["know"]:
        return True
    if words[index:index + 1] == ["do"] and following == ["not", "know"]:
        return True
    if (
        words[index:index + 1] == ["no"]
        and following[:1]
        and following[0] in _NO_INFORMATION_NOUNS
        and all(word in _NO_INFORMATION_TAIL for word in words[index + 2:])
    ):
        return True
    if index >= len(words) or words[index] not in _OPTION_NEGATIONS:
        return False
    rest = [word for word in words[index + 1:index + 5] if word not in _OPTION_HEDGES]
    if not rest:
        return False
    head = rest[0]
    if head in _NOT_MENTIONED_PARTICIPLES or head.startswith("mention"):
        return True
    if len(head) >= 7 and any(
        len(participle) >= 8 and _within_one_edit(head, participle)
        for participle in _NOT_MENTIONED_PARTICIPLES
    ):
        return True
    return head in {"in", "from"} and any(word in _RECORD_PLACE_NOUNS for word in rest[1:3])


# Which turns ask the retrieved records something. Each sentence is read on its own, by its shape,
# never by its topic:
#   * a pleasantry or small talk asks nothing ("great, thanks for that!", "cheers, talk soon",
#     "hey, how are you?");
#   * a sentence that opens - after a vocative, "please", "now", "can you" - with a verb that makes,
#     does or shapes something ("write", "draft", "remind <someone>", "answer", "use") asks nothing,
#     whatever interrogative words follow ("write a poem about why the lake freezes"); "remind me
#     what ...", "remember when ..." and "recall ..." ask. A verb that is also a noun and is
#     followed by a preposition opens a fragment instead ("update on the repairs?", "plan for the
#     trip, when is it");
#   * a sentence that opens with a telling verb ("tell me", "explain", "describe", "summarize",
#     "list", "show", "give") asks only when it points at the records: a speaker the records label,
#     a record word ("said", "mentioned", "chat") or a date. "Explain how a lock works" does not;
#   * a statement that opens with its subject ("I went ...", "that's what I thought") asks only with
#     a question mark or an asking verb ("I wonder", "I forgot", "I can't remember");
#   * any other sentence - a question, a fragment, a clause ("according to ...") - asks when it has
#     a question mark, opens with an auxiliary ("did", "was"), or, unless it is an exclamation,
#     carries an interrogative word or names a person ("<name> swimming feelings"); a comment or
#     statement about someone ("<name> seems busy", "<name> is tall", "<name> fixed it") does not.
_TOKEN_RE = re.compile(r"[^\W_][\w'’]*")
_SENTENCE_WITH_END_RE = re.compile(r"[^.!?;\n]+[.!?;]*")
_INTERROGATIVE_WORDS = frozenset({
    "how", "hows", "what", "whats", "when", "where", "wheres", "which", "who", "whos", "whom",
    "whose", "why",
    # common typed slips of them
    "wat", "wht", "whta", "waht", "wut", "whn", "wehn", "wher", "whre", "wehre", "hw", "hwo",
    "wich", "whcih", "whihc",
})
_AUXILIARY_LEADS = frozenset({
    "am", "is", "are", "was", "were", "do", "does", "did", "has", "have", "had", "can", "could",
    "will", "would", "should", "shall", "may", "might", "must",
    "isn't", "aren't", "wasn't", "weren't", "don't", "doesn't", "didn't", "hasn't", "haven't",
    "hadn't", "can't", "couldn't", "won't", "wouldn't", "shouldn't",
    "isnt", "arent", "wasnt", "werent", "dont", "doesnt", "didnt", "hasnt", "havent", "hadnt",
    "cant", "couldnt", "wont", "wouldnt", "shouldnt",
})
# Words a sentence may open with before its working verb: discourse fillers and "please".
_LEAD_FILLERS = frozenset({
    "please", "pls", "plz", "kindly", "now", "then", "and", "also", "so", "ok", "okay", "just",
    "oh", "well", "hey", "hi", "hello", "btw", "anyway", "anyways", "um", "uh", "hmm", "right",
    "alright", "quick", "quickly", "first", "next", "lastly", "finally", "actually", "but", "yes",
    "yeah", "no", "sorry", "thanks", "cool", "great", "sure",
})
# What follows "remind me" when it sets a reminder rather than recalls something.
_REMINDER_SETTINGS = frozenset({
    "to", "at", "in", "on", "tomorrow", "tonight", "today", "later", "next", "this", "every",
    "before", "after", "again", "once", "daily", "weekly", "monthly", "soon",
})
# A request frame before the working verb: "can you", "could u please".
_REQUEST_FRAME_VERBS = frozenset({"can", "could", "would", "will", "wont", "won't"})
# Verbs whose sentence makes, does or shapes something; it asks the records nothing.
_CREATE_OR_ACT_VERBS = frozenset({
    "write", "draft", "compose", "create", "make", "generate", "build", "draw", "paint", "design",
    "sketch", "translate", "rewrite", "rephrase", "paraphrase", "edit", "proofread", "format",
    "reformat", "send", "email", "text", "message", "call", "phone", "ring", "book", "schedule",
    "add", "save", "store", "note", "log", "delete", "remove", "forget", "erase", "clear", "open",
    "close", "install", "fix", "rename", "convert", "buy", "pay", "order", "cancel", "upload",
    "download", "implement", "refactor", "deploy", "play", "pause", "stop", "start", "run",
    "launch", "set", "turn", "move", "copy", "paste", "print", "share", "post", "publish", "invite",
    "plan", "organize", "organise", "prepare", "cook", "bake", "imagine", "pretend", "invent",
    "brainstorm", "suggest", "recommend", "propose", "solve", "sort", "rank", "rate", "pick",
    "choose", "select", "improve", "shorten", "expand", "simplify", "fill", "complete",
    "continue", "finish", "teach", "quiz", "roleplay", "sing", "rhyme", "update", "change",
    "replace", "insert", "append", "merge", "split", "clean", "tidy", "ask",
    # Verbs that shape the answer itself: "Answer from ...", "Use only ...", "Be brief."
    "answer", "use", "keep", "respond", "reply", "base", "stick", "rely", "cite", "quote",
    "include", "avoid", "say", "try", "go", "come", "wait", "let", "be",
})
# Verbs that are nouns too: followed by a preposition they open a fragment ("call with her last
# week", "list of the boats", "update on the repairs"), not an instruction.
_NOUN_LIKE_VERBS = frozenset({
    "call", "message", "text", "note", "log", "order", "change", "plan", "post", "set", "sort",
    "draft", "design", "sketch", "update", "list", "review", "report", "quote",
})
_PREPOSITIONS = frozenset({
    "of", "for", "from", "with", "in", "on", "at", "about", "between", "during", "after",
    "before", "regarding",
})
# Verbs that ask to be told something; the sentence asks the records only when it points at them.
_TELLING_VERBS = frozenset({
    "explain", "describe", "summarize", "summarise", "sum", "recap", "list", "outline", "detail",
    "elaborate", "clarify", "define", "compare", "contrast", "show", "give", "get", "find",
    "search", "look", "check", "verify", "confirm", "identify", "name", "count", "calculate",
    "compute", "estimate", "guess", "figure", "work", "review", "help", "pull", "fetch",
    "retrieve", "read", "repeat", "point", "report", "state", "specify", "mention",
})
# A comment on someone opens with the name and one of these verbs ("<Name> seems busy"). An
# auxiliary is left out: a mistyped interrogative opens the same way ("wat does ...").
_COMMENT_VERBS = frozenset({
    "sounds", "sounded", "seems", "seemed", "looks", "looked", "feels", "felt", "loves", "loved",
    "likes", "liked", "hates", "hated", "knows", "knew", "thinks", "thought", "says", "said",
    "told", "wants", "wanted", "needs", "needed", "deserves", "rocks",
})
# A statement opens with its subject.
_SUBJECT_LEADS = frozenset({
    "i", "im", "ive", "id", "ill", "we", "weve", "you", "youre", "youve", "he", "she", "they",
    "theyre", "it", "its", "that", "thats", "this", "there", "theres", "these", "those",
})
# A copula early in a sentence that has no question in it makes the sentence a statement.
_COPULAS = frozenset({
    "is", "was", "are", "were", "isnt", "isn't", "wasnt", "wasn't", "arent", "aren't", "werent",
    "weren't",
})
# "that", "this", "these", "those" open a statement only as its subject: contracted ("that's") or
# before a copula ("this is"); before a noun they open a fragment ("that boat ...").
_DEMONSTRATIVE_LEADS = frozenset({"that", "this", "these", "those"})
_STATEMENT_COPULAS = frozenset({
    "is", "was", "are", "were", "isnt", "isn't", "wasnt", "wasn't", "seems", "sounds", "looks",
    "makes", "means", "will", "would", "could", "should", "can", "must", "might", "has", "had",
    "have",
})
# A statement that still asks: "I wonder ...", "I forgot ...", "I can't remember ...".
_ASKING_STATEMENT_RE = re.compile(
    r"\b(?:wonder(?:ing|ed)?|curious|forg[eo]t(?:ten)?|unsure|not\s+sure|no\s+idea|"
    r"(?:do\s+not|don['’]?t|can\s*not|can['’]?t|could\s+not|couldn['’]?t|did\s+not|didn['’]?t|"
    r"never)\s+(?:know|remember|recall)|"
    r"(?:want|wanted|need|needed|like|trying|try)\s+to\s+(?:know|find\s+out|remember|recall|"
    r"check|confirm|see))\b",
    re.IGNORECASE,
)
# A word that points a telling sentence at the records ("explain what she said in our chat").
_RECORD_WORDS = frozenset({
    "said", "says", "saying", "told", "mentioned", "mention", "mentions", "mentioning", "talked",
    "talking", "discussed", "chat", "chats", "chatted", "conversation", "conversations", "record",
    "records", "notes", "messages", "transcript", "imported", "earlier", "previously",
})
_DATE_REFERENCE_RE = re.compile(
    r"\b(?:january|february|march|april|june|july|august|september|october|november|december|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|yesterday|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+may|may\s+\d{1,2}|"
    r"(?:last|this|next)\s+(?:week|weekend|month|year|night|time)|"
    r"\d+\s+(?:days?|weeks?|months?|years?)\s+ago|(?:19|20)\d{2}|\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?)\b",
    re.IGNORECASE,
)
# Pleasantries and small talk: every word is one of these (one vocative allowed), and at least one
# is a core pleasantry, or the sentence is a small-talk phrase.
_PLEASANTRY_CORE = frozenset({
    "thanks", "thank", "thx", "ty", "tysm", "cheers", "ta", "hi", "hello", "hey", "heya", "hiya",
    "yo", "howdy", "ok", "okay", "k", "kk", "great", "cool", "nice", "perfect", "awesome",
    "brilliant", "lovely", "wonderful", "excellent", "amazing", "fantastic", "sweet", "neat", "bye",
    "goodbye", "cya", "noted", "understood", "gotcha", "sure", "yes", "yeah", "yep", "yup",
    "alright", "fine", "good", "appreciated", "appreciate", "welcome", "congrats",
    "congratulations", "sorry", "oops", "wow", "haha", "lol", "really", "seriously",
})
_PLEASANTRY_WORDS = _PLEASANTRY_CORE | frozenset({
    "you", "ya", "u", "so", "much", "very", "really", "a", "lot", "lots", "for", "that", "this",
    "the", "it", "all", "right", "no", "worries", "problem", "problems", "np", "got", "sounds",
    "sound", "makes", "sense", "see", "later", "soon", "talk", "ttyl", "take", "care", "have",
    "day", "weekend", "night", "morning", "evening", "afternoon", "today", "tonight", "tomorrow",
    "again", "help", "helps", "helped", "helpful", "info", "mate", "buddy", "pal", "man", "dude",
    "guys", "folks", "friend", "everyone", "everybody", "there", "love", "one", "will", "do",
    "done", "both", "too", "as", "always", "oh", "ah", "my", "your", "best", "indeed", "totally",
    "absolutely", "definitely", "of", "course", "now", "then", "just", "kind", "pleasure", "glad",
    "happy", "to", "hear", "hope", "well", "is", "was",
})
_SMALL_TALK_RE = re.compile(
    r"\b(?:how\s+(?:are|r)\s+(?:you|u|ya|things)(?:\s+doing)?|hows?\s+(?:is\s+)?it\s+going|"
    r"how\s+have\s+you\s+been|how\s+you\s+doing|"
    r"hows?\s+(?:is\s+|was\s+)?your\s+(?:day|week|weekend|morning|evening)|whats?\s+up|sup|"
    r"(?:will|can)\s+do)\b"
)
# Calendar words are capitalised without naming anyone.
_CALENDAR_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
})
# A dialogue speaker label inside a retrieved record: one to three capitalised words (any script)
# and a colon, opening a line or following an attribution colon ("- user said: <Name>: ..."), then
# a space.
_RECORD_SPEAKER_LABEL_RE = re.compile(
    r"(?m)(?:^|(?<=: ))([^\W\d_][\w'’-]{1,30}(?: [^\W\d_][\w'’-]{1,30}){0,2}):[ \t]"
)
# Role and heading labels that name no person.
_NON_PERSON_LABELS = frozenset({
    "user", "assistant", "system", "vool", "human", "ai", "bot", "note", "notes",
    "update", "correction", "summary", "question", "answer", "re", "subject", "warning",
})
# Words that open sentences without naming anyone; a capitalised word opening a sentence names a
# person only when it is none of these.
_FUNCTION_LEADS = frozenset({
    "a", "an", "the", "any", "some", "all", "both", "each", "every", "either", "neither", "many",
    "much", "most", "few", "several", "none", "other", "another", "such", "one", "in", "on", "at",
    "after", "before", "during", "since", "until", "till", "from", "to", "for", "with",
    "without", "about", "according", "between", "among", "over", "under", "by", "of", "around",
    "through", "across", "along", "besides", "except", "like", "unlike", "per", "via", "into",
    "onto", "upon", "within", "regarding", "re", "or", "nor", "yet", "because", "if", "unless",
    "although", "though", "while", "whereas", "whether", "than", "as", "plus", "not", "only",
    "even", "still", "again", "ever", "never", "always", "usually", "often", "sometimes", "maybe",
    "perhaps", "roughly", "exactly", "overall", "generally", "basically", "really", "yesterday",
    "today", "tonight", "tomorrow", "last", "earlier", "later", "previously", "recently",
    "lately", "once", "second", "third", "otherwise", "instead", "meanwhile", "here", "same",
    "based", "given", "considering", "assuming", "question", "wait", "excuse", "my", "our",
    "your", "his", "her", "their", "me", "us", "him", "them", "recall", "remind", "remember",
    "tell",
})
_KNOWN_LEAD_WORDS = (
    _INTERROGATIVE_WORDS | _AUXILIARY_LEADS | _LEAD_FILLERS | _CREATE_OR_ACT_VERBS
    | _TELLING_VERBS | _SUBJECT_LEADS | _PLEASANTRY_WORDS | _FUNCTION_LEADS
)
_NAME_SPLIT_RE = re.compile(r"[.!?:;\n]+")


def _norm_word(token: str) -> str:
    """A token as the reading rules compare it: lower case, possessive and contraction dropped."""
    word = str(token or "").lower().replace("’", "'")
    if word in _AUXILIARY_LEADS:
        return word
    if word.endswith("'s"):
        return word[:-2]
    return word.split("'", 1)[0] if "'" in word else word


def _is_capitalised(token: str) -> bool:
    """A name-shaped token in any script: a capital, then at least one lower-case letter."""
    return bool(token) and token[0].isupper() and any(ch.islower() for ch in token[1:])


def _within_one_edit(left: str, right: str) -> bool:
    """True when the words differ by at most one insertion, deletion, substitution or swap."""
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    index = 0
    while index < min(len(left), len(right)) and left[index] == right[index]:
        index += 1
    if len(left) == len(right):
        return left[index + 1:] == right[index + 1:] or (
            index + 1 < len(left)
            and left[index] == right[index + 1]
            and left[index + 1] == right[index]
            and left[index + 2:] == right[index + 2:]
        )
    if len(left) > len(right):
        return left[index + 1:] == right[index:]
    return left[index:] == right[index + 1:]


def _is_record_speaker(word: str, record_speakers: tuple[str, ...]) -> bool:
    """A typed word names a one-word speaker the records label: exactly, as a possessive typed
    without its apostrophe ("tamsins"), or with one typing slip in a name of six or more letters
    ("bertl", "tasmin"); a slip keeps the first letter and does not just add a final letter."""
    if len(word) < 2:
        return False
    for label in record_speakers or ():
        name = str(label or "").strip().lower()
        if len(name) < 2 or " " in name:
            continue
        if word == name or (word.endswith("s") and word[:-1] == name):
            return True
        if len(name) < 6:
            continue
        for typed in {word, word[:-1] if word.endswith("s") else word}:
            if (
                len(typed) >= 5
                and typed[0] == name[0]
                and not (len(typed) == len(name) + 1 and typed.startswith(name))
                and _within_one_edit(typed, name)
            ):
                return True
    return False


def _names_a_multi_word_speaker(text: str, record_speakers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    for label in record_speakers or ():
        name = str(label or "").strip().lower()
        if " " in name and re.search(rf"(?<![\w'’-]){re.escape(name)}s?(?![\w-])", lowered):
            return True
    return False


def _is_pleasantry(words: list[str], tokens: list[str], record_speakers: tuple[str, ...]) -> bool:
    joined = " ".join(words)
    small_talk = _SMALL_TALK_RE.search(joined)
    if small_talk:
        before = len(joined[: small_talk.start()].split())
        after = len(joined[small_talk.end():].split())
        indexes = [*range(before), *range(len(words) - after, len(words))]
    else:
        indexes = list(range(len(words)))
    has_core = bool(small_talk)
    vocative = False
    for index in indexes:
        word = words[index]
        if word in _PLEASANTRY_CORE:
            has_core = True
        elif word in _PLEASANTRY_WORDS:
            continue
        elif not vocative and (
            _is_capitalised(tokens[index]) or _is_record_speaker(word, record_speakers)
        ):
            vocative = True
        else:
            return False
    return has_core


def _points_at_records(text: str, words: list[str], record_speakers: tuple[str, ...]) -> bool:
    """A telling sentence points at the records: a recorded speaker, a record word or a date."""
    return (
        any(_is_record_speaker(word, record_speakers) for word in words)
        or _names_a_multi_word_speaker(text, record_speakers)
        or any(word in _RECORD_WORDS for word in words)
        or bool(_DATE_REFERENCE_RE.search(text))
    )


def _sentence_asks(sentence: str, record_speakers: tuple[str, ...]) -> bool:
    text = sentence.strip().strip(" \t\"'“”‘’()[]*-–—")
    matches = list(_TOKEN_RE.finditer(text))
    if not matches:
        return False
    tokens = [match.group(0) for match in matches]
    words = [_norm_word(token) for token in tokens]
    if _is_pleasantry(words, tokens, record_speakers):
        return False
    asked = "?" in text
    start = 0
    while start < len(words) and words[start] in _LEAD_FILLERS:
        start += 1
    # A vocative before a comma ("Bertil, write me a haiku").
    if (
        start < len(words) - 1
        and words[start] not in _KNOWN_LEAD_WORDS
        and text[matches[start].end():].lstrip().startswith(",")
    ):
        start += 1
        while start < len(words) and words[start] in _LEAD_FILLERS:
            start += 1
    if (
        start + 1 < len(words)
        and words[start] in _REQUEST_FRAME_VERBS
        and words[start + 1] in {"you", "u"}
    ):
        start += 2
        while start < len(words) and words[start] in _LEAD_FILLERS:
            start += 1
    lead = words[start:] or words[-1:]
    first = lead[0]
    second = lead[1] if len(lead) > 1 else ""
    third = lead[2] if len(lead) > 2 else ""
    if first == "recall":
        return True
    if first == "remind":
        # "remind me what ...", "remind me of ...", "remind me tamsin's days" recall; "remind me to
        # ...", "remind me at five", "remind <someone> ..." set a reminder.
        return second in {"me", "us"} and bool(third) and third not in _REMINDER_SETTINGS
    if first == "remember":
        return second in _INTERROGATIVE_WORDS or second in {"whether", "if"}
    if first == "tell" or (first == "let" and third == "know"):
        return second in {"me", "us"} and _points_at_records(text, words, record_speakers)
    opens_a_fragment = first in _NOUN_LIKE_VERBS and second in _PREPOSITIONS
    if first in _CREATE_OR_ACT_VERBS and not opens_a_fragment:
        return False
    if first in _TELLING_VERBS and not opens_a_fragment:
        return _points_at_records(text, words, record_speakers)
    statement = first in _SUBJECT_LEADS
    if first in _DEMONSTRATIVE_LEADS:
        # "that's what I thought", "this is it" state; "that boat she fixed, which colour" asks.
        statement = (
            tokens[start].lower().replace("’", "'") in {"that's", "thats", "this's"}
            or second in _STATEMENT_COPULAS
        )
    if statement:
        return asked or bool(_ASKING_STATEMENT_RE.search(text))
    if first in {"don't", "dont"} or (first == "do" and second == "not"):
        return asked  # "Don't answer yet." is an instruction, not a question
    if asked or first in _AUXILIARY_LEADS:
        return True
    if text.endswith("!"):
        return False
    if any(word in _INTERROGATIVE_WORDS for word in words):
        return True
    # A comment or statement about someone ("<name> seems busy", "<name> is so talented",
    # "<name>'s boat was green", "<name> fixed the boats"), not a fragment about them.
    if second in _COMMENT_VERBS or _is_past_tense(second) or any(
        word in _COPULAS for word in lead[1:3]
    ):
        return False
    return names_a_person(text, record_speakers)


def _is_past_tense(word: str) -> bool:
    """A regular past tense ("fixed", "started"); "speed" and "need" are not one."""
    return len(word) >= 5 and word.endswith("ed") and not word.endswith("eed")


def record_question_sentences(
    user_text: str, record_speakers: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """The sentences of a turn that ask the records something (see the reading rules above)."""
    speakers = tuple(record_speakers or ())
    return tuple(
        sentence.strip()
        for sentence in _SENTENCE_WITH_END_RE.findall(str(user_text or ""))
        if _sentence_asks(sentence, speakers)
    )


def asks_a_question(user_text: str, record_speakers: tuple[str, ...] = ()) -> bool:
    """True when the turn asks the records something; a pleasantry, a request to make or do
    something, a telling request that points nowhere in the records, or a plain statement does
    not."""
    return bool(record_question_sentences(user_text, record_speakers))


def retrieved_record_speakers(texts: Any) -> tuple[str, ...]:
    """Speaker names labelled inside retrieved records ("<Name>: ..."), in first-seen order."""
    names: list[str] = []
    for text in texts or ():
        for match in _RECORD_SPEAKER_LABEL_RE.finditer(str(text or "")):
            label = match.group(1).strip()
            if not all(part[0].isupper() for part in label.split()):
                continue
            if label.lower() not in _NON_PERSON_LABELS:
                names.append(label)
    return tuple(dict.fromkeys(names))


def names_a_person(user_text: str, record_speakers: tuple[str, ...] = ()) -> bool:
    """True when the turn names someone.

    A speaker the retrieved records label counts in any case and position, with one typing slip
    in a longer name (see `_is_record_speaker`). A capitalised name (any script) counts inside a
    sentence; opening a sentence it counts when it is not a word sentences open with ("What",
    "Tell", "According", "Swimming"). Calendar words and role labels never count.
    """
    text = str(user_text or "")
    speakers = tuple(record_speakers or ())
    for piece in _NAME_SPLIT_RE.split(text):
        for index, match in enumerate(_TOKEN_RE.finditer(piece)):
            token = match.group(0)
            word = _norm_word(token)
            if _is_record_speaker(word, speakers):
                return True
            if not _is_capitalised(token) or word in _CALENDAR_WORDS or word in _NON_PERSON_LABELS:
                continue
            if index == 0 and (
                word in _KNOWN_LEAD_WORDS
                or (len(word) >= 6 and word.endswith("ing"))
                or (len(word) >= 7 and word.endswith("ly") and not word.endswith("erly"))
            ):
                continue
            return True
    return _names_a_multi_word_speaker(text, speakers)


_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_SENTENCE_RE = re.compile(r".+?(?:[.!?]+(?=\s|$)|$)", re.DOTALL)
_DEFAULT_ORDINARY_CHAT_MAX_WORDS = 64


@dataclass(frozen=True)
class OrdinaryChatOutputCheck:
    """A redacted verdict suitable for runtime receipts."""

    allowed: bool
    reasons: tuple[str, ...] = ()
    signal_groups: tuple[str, ...] = ()
    completeness_status: str | None = None
    # Length verdicts the answer earned but that were not enforced, because it is read from memory
    # records the reader request carried (see `inspect_ordinary_chat_output`). On a free lane each
    # one is a rewrite over the whole evidence set that was not made; on any lane it is an answer
    # that was neither cut at the word ceiling nor replaced by the fallback.
    waived_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "signal_groups": list(self.signal_groups),
        }
        if self.completeness_status is not None:
            result["completeness_status"] = self.completeness_status
        # Recorded only when a waiver fired, so every other turn's receipt keeps its exact shape.
        if self.waived_reasons:
            result["waived_reasons"] = list(self.waived_reasons)
        return result


def ordinary_chat_output_policy(
    *,
    prompt_profile: str,
    output_mode: str,
    creative_medium: str | None = None,
    user_text: str = "",
    prior_turn_literal_hashes: tuple[str, ...] = (),
    forgotten_prior_turn_literal_hashes: tuple[str, ...] = (),
    memory_records_supplied: bool = False,
    record_speakers: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return server-derived policy metadata without retaining request text.

    ``memory_records_supplied`` says the reader request carries retrieved memory records; only
    then is an event-time question a memory question (see `relative_event_time_instruction`), and
    only then is the answer's length left as the reader wrote it (the policy's
    ``memory_records_supplied`` key; see `inspect_ordinary_chat_output`). ``record_speakers`` are
    the speaker names those records label; a question that names one, in any case or with one
    typing slip in a longer name, names a person (see `person_attribution_instruction`), and a
    telling request that names one ("tell me about ...") points at the records (see
    `record_question_sentences`).
    """
    # The prompt profile is downstream inference and can be wrong.  Only an
    # explicit image/video request is allowed to disable this ordinary-chat
    # guard, so a misclassified normal turn cannot leak a director prompt.
    ordinary = (
        str(output_mode or "").strip() in {"plain_text", "text"}
        and str(creative_medium or "").strip().lower() not in {"image", "video"}
    )
    from core.plain_task_routing import ordinary_plain_request_count, user_requires_numbered_answers
    from core.response_constraints import requested_output_item_count

    requested_items = requested_output_item_count(user_text)
    required_parts = ordinary_plain_request_count(user_text)
    detail_requested = _length_contract_lifts_ceiling(user_text)
    short_max_words = explicit_short_output_max_words(user_text)
    # An open advice request is a bounded detail request: room for the user's own details, still
    # capped. A stated short contract or a stated detail/count contract outranks it.
    advice_budget = (
        ordinary
        and not detail_requested
        and short_max_words is None
        and _is_open_advice_request(user_text)
    )
    not_mentioned_offered = bool(ordinary and offers_not_mentioned_option(user_text))
    # A question over retrieved records with no not-mentioned option to fall back on: the reader
    # reads linked records before saying they are silent, and leads with a fact the records hold
    # for someone else instead of a denial. An offered not-mentioned option keeps the premise rule.
    # Only the sentences that ask the records something count (see `record_question_sentences`):
    # a pleasantry, a request to make something, or the harness's own answer instructions do not.
    speakers = tuple(record_speakers or ())
    record_questions = (
        record_question_sentences(user_text, speakers)
        if ordinary and memory_records_supplied and not not_mentioned_offered
        else ()
    )
    open_record_question = bool(record_questions)
    return {
        "mode": "ordinary_chat" if ordinary else "not_applicable",
        "prompt_profile": str(prompt_profile or "unknown"),
        "detail_requested": "true" if detail_requested else "false",
        "requested_items": requested_items,
        "max_words": (
            str(
                short_max_words
                or (_ADVICE_MAX_WORDS if advice_budget else _DEFAULT_ORDINARY_CHAT_MAX_WORDS)
            )
            if ordinary and not detail_requested
            else ""
        ),
        "max_items": _ADVICE_MAX_ITEMS if advice_budget else None,
        # The user's explicit length request sets the answer shape as well as the word ceiling:
        # answer first, no preamble or restatement. See `short_answer_instruction`.
        "answer_shape": "short" if ordinary and short_max_words is not None else "",
        # Offered options include a "not mentioned" choice: the reader is told when a factual option
        # may be chosen. See `not_mentioned_option_instruction`.
        "not_mentioned_option_offered": not_mentioned_offered,
        # A record question that names a person: a fact the records hold only for someone else is
        # given first, with its owner, not denied. See `person_attribution_instruction`.
        "person_attribution": any(
            names_a_person(sentence, speakers) for sentence in record_questions
        ),
        # A record question: records stated with the matched event, or on a date the question
        # names, are read together before the reader says the records are silent. See
        # `linked_records_instruction`.
        "linked_records": open_record_question,
        # A memory question that asks when something happened: the reader is told to keep a
        # record's relative time anchored to the record's date. See
        # `relative_event_time_instruction`.
        "relative_event_time": bool(
            ordinary and memory_records_supplied and _asks_event_time(user_text)
        ),
        # The reader request carries retrieved memory records, so the answer is read from them:
        # a length verdict is recorded, not enforced, because its only repairs are a second reader
        # call over the whole evidence set or a cut at the word ceiling. See
        # `inspect_ordinary_chat_output`.
        "memory_records_supplied": bool(ordinary and memory_records_supplied),
        "prior_turn_literal_hashes": list(
            dict.fromkeys(
                str(value).strip().lower()
                for value in prior_turn_literal_hashes
                if str(value).strip()
            )
        ),
        "forgotten_prior_turn_literal_hashes": list(
            dict.fromkeys(
                str(value).strip().lower()
                for value in forgotten_prior_turn_literal_hashes
                if str(value).strip()
            )
        ),
        "scoped_memory_recall": _is_scoped_memory_recall_request(user_text),
        "independent_request_parts": required_parts if required_parts >= 2 else 0,
        "required_numbered_parts": required_parts if required_parts >= 2 and user_requires_numbered_answers(user_text) else 0,
    }


def prior_turn_literal_hashes(
    messages: list[dict[str, Any]] | None,
    *,
    current_user_text: str,
) -> tuple[str, ...]:
    """Return redacted opaque identifiers from prior user turns only.

    The response guard needs to recognize an unrelated identifier without adding
    private prior-turn text to provider manifests or runtime receipts.
    """
    current_literals = {
        _literal_hash(value)
        for value in _guard_literal_values(str(current_user_text or ""))
    }
    hashes: list[str] = []
    for message in list(messages or []):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        for value in _guard_literal_values(str(message.get("content") or "")):
            digest = _literal_hash(value)
            if digest not in current_literals:
                hashes.append(digest)
    return tuple(dict.fromkeys(hashes))


def forgotten_prior_turn_literal_hashes(
    messages: list[dict[str, Any]] | None,
) -> tuple[str, ...]:
    """Hash literals named by prior forget commands in the current chat."""
    hashes: list[str] = []
    for message in list(messages or []):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        match = _FORGET_COMMAND_RE.match(str(message.get("content") or ""))
        if not match:
            continue
        target = re.sub(
            r"\s+from\s+(?:this|the\s+current)\s+chat\s*\??$",
            "",
            match.group("target").strip().strip(".!?"),
            flags=re.IGNORECASE,
        )
        for value in _guard_literal_values(target):
            hashes.append(_literal_hash(value))
    return tuple(dict.fromkeys(hashes))


def _literal_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").strip().casefold().encode("utf-8")).hexdigest()


def _guard_literal_values(text: str) -> tuple[str, ...]:
    values = list(_CONFIRMATION_LITERAL_RE.findall(str(text or "")))
    values.extend(
        match.group("value").strip()
        for match in _TYPED_MEMORY_DECLARATION_RE.finditer(str(text or ""))
        if match.group("value").strip()
    )
    values.extend(_OPAQUE_MULTIWORD_VALUE_RE.findall(str(text or "")))
    values.extend(_OPAQUE_CODE_TOKEN_RE.findall(str(text or "")))
    return tuple(dict.fromkeys(values))


def _is_scoped_memory_recall_request(text: str) -> bool:
    """Allow prior-turn literals only for an explicit, typed memory recall."""
    normalized = " ".join(str(text or "").split())
    return bool(
        _SCOPED_MEMORY_RECALL_RE.search(normalized)
        and not _MEMORY_RECALL_DENIAL_RE.search(normalized)
    )


def _max_words(policy: dict[str, Any]) -> int | None:
    try:
        value = int(str(policy.get("max_words") or ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _max_items(policy: dict[str, Any]) -> int | None:
    try:
        value = int(str(policy.get("max_items") or ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _prior_literal_contamination(
    text: str, policy: dict[str, Any], current_user_text: str
) -> OrdinaryChatOutputCheck | None:
    """A prior turn's opaque literal must not answer a later, unrelated turn -- in ANY mode.

    Measured on the served surface, 2026-08-15: an exact-literal turn ("Output exactly
    ERR_AWS_DENIED") contaminated a LATER array-logic puzzle, which answered "ERR_AWS_DENIED". The
    contamination rejection already existed but ran only for `ordinary_chat`; a turn classified
    `research`/`unknown` bypassed it, and the extractor could not even see a single ERR_ token.

    Two exceptions keep it from eating legitimate output: a literal the CURRENT turn itself states
    (its own contract), and an explicit scoped recall ("what did you say the code was?").
    """

    rendered = str(text or "").strip()
    if not rendered or "```" in rendered:
        return None
    normalized_user_text = " ".join(str(current_user_text or "").lower().split())
    explicit_memory_recall = (
        _is_scoped_memory_recall_request(normalized_user_text)
        if normalized_user_text
        else bool(policy.get("scoped_memory_recall"))
    )
    prior_hashes = {
        str(v).strip().lower()
        for v in list(policy.get("prior_turn_literal_hashes") or [])
        if str(v).strip()
    }
    forgotten_hashes = {
        str(v).strip().lower()
        for v in list(policy.get("forgotten_prior_turn_literal_hashes") or [])
        if str(v).strip()
    }
    # A literal the current turn ITSELF states is its own, never contamination -- use the full
    # extractor so a current exact-literal contract exempts its own code.
    current_hashes = {_literal_hash(v) for v in _guard_literal_values(current_user_text)}
    rendered_values = _guard_literal_values(rendered)
    for value in rendered_values:
        digest = _literal_hash(value)
        if digest in forgotten_hashes and digest not in current_hashes:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("forgotten_memory_literal",),
                signal_groups=("forgotten_memory",),
            )
    if prior_hashes and not explicit_memory_recall:
        for value in rendered_values:
            digest = _literal_hash(value)
            if digest in prior_hashes and digest not in current_hashes:
                return OrdinaryChatOutputCheck(
                    allowed=False,
                    reasons=("unrequested_prior_turn_literal",),
                    signal_groups=("prior_turn_identifier",),
                )
    return None


def _underanswer_question(normalized_user_text: str) -> str:
    """The question a one-word reply would be answering, without any lead-in before it.

    The last sentence ending in "?" (the question asked), else the last sentence; a "label:"
    lead-in inside it is dropped. Lead-in sentences (instructions, greetings, context) are not the
    question, so a "why" or "how" inside them does not make the question an explanation question.
    """
    text = str(normalized_user_text or "").strip()
    questions = [part.strip() for part in _QUESTION_SENTENCE_RE.findall(text) if part.strip(" ?")]
    if questions:
        clause = questions[-1]
    else:
        sentences = [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip(" .!?")]
        clause = sentences[-1] if sentences else text
    if ":" in clause:
        clause = clause.rsplit(":", 1)[-1].strip()
    return clause


def inspect_ordinary_chat_output(
    text: str,
    policy: dict[str, Any] | None,
    *,
    current_user_text: str = "",
) -> OrdinaryChatOutputCheck:
    """Reject strong visual-prompt evidence on an ordinary chat turn.

    Cross-turn literal contamination is checked in EVERY mode via `_prior_literal_contamination`;
    the structure/visual-prompt heuristics below stay ordinary-chat-specific.
    """
    if not isinstance(policy, dict):
        return OrdinaryChatOutputCheck(allowed=True)
    contamination = _prior_literal_contamination(str(text or ""), policy, current_user_text)
    if contamination is not None:
        return contamination
    if policy.get("mode") != "ordinary_chat":
        return OrdinaryChatOutputCheck(allowed=True)

    rendered = str(text or "").strip()
    if not rendered or "```" in rendered:
        return OrdinaryChatOutputCheck(allowed=True)
    normalized_user_text = " ".join(str(current_user_text or "").lower().split())
    numbered_parts = int(policy.get("required_numbered_parts") or 0)
    required_parts = int(policy.get("independent_request_parts") or numbered_parts)
    completeness_status = None
    if required_parts >= 2:
        from core.plain_task_routing import ordinary_indexed_answer_status, ordinary_multi_part_answer_status

        # The original request supplies the shared shape authority. The final display
        # backstop uses the same server-derived count when request text is not retained.
        if current_user_text:
            completeness_status = ordinary_multi_part_answer_status(current_user_text, rendered)
        else:
            completeness_status = ordinary_indexed_answer_status(
                required_parts, rendered, numbering_required=numbered_parts >= 2,
            )
        complete = completeness_status != "missing_requested_parts"
        from core.model_output_guard import delivers_a_withdrawal_notice

        if not complete and not delivers_a_withdrawal_notice(rendered):
            # A part the runtime itself withdrew ("...I'm not going to state them") is
            # declined, not omitted: the honesty markers are this runtime's own minted
            # verdicts (model_output_guard.WITHDRAWAL_NOTICE_STEMS), and demanding the
            # withdrawn part back would route an honest decline to the fallback.
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("missing_requested_parts",),
                signal_groups=("multi_part_completeness",),
                completeness_status=completeness_status,
            )
    explicit_prior_answer_follow_up = bool(
        _EXPLICIT_PRIOR_ANSWER_FOLLOW_UP_RE.search(normalized_user_text)
    )
    required_literals = (
        tuple(dict.fromkeys(_CONFIRMATION_LITERAL_RE.findall(current_user_text)))
        if _CONFIRMATION_REQUEST_RE.search(current_user_text)
        else ()
    )
    if any(literal not in rendered for literal in required_literals):
        return OrdinaryChatOutputCheck(
            allowed=False,
            reasons=("missing_confirmed_literal",),
            signal_groups=("required_literal",),
        )
    # Prior-turn-literal and forgotten-literal contamination are now handled universally at the
    # top of this function (`_prior_literal_contamination`), for every mode -- not only here.
    for match in _CONTRAST_TOPIC_RE.finditer(rendered):
        topic = " ".join(match.group("topic").lower().split())
        if topic and topic not in normalized_user_text:
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("unrequested_contrast_topic",),
                signal_groups=("contrast_topic",),
            )
    for match in _UNREQUESTED_COMPARISON_RE.finditer(rendered):
        topic = match.group("topic").lower()
        if (
            topic
            and topic not in normalized_user_text
            and not explicit_prior_answer_follow_up
        ):
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("unrequested_prior_reference",),
                signal_groups=("prior_turn_bridge",),
            )
    groups: list[str] = []
    if _CAMERA_SIGNALS.search(rendered):
        groups.append("camera_or_composition")
    if _STYLE_SIGNALS.search(rendered):
        groups.append("visual_style")
    if _SCAFFOLD_SIGNALS.search(rendered):
        groups.append("prompt_scaffold")

    # A scaffold plus one independent class is strong evidence. Without a
    # scaffold, require prompt-specific detail as well; ordinary explanations
    # can legitimately mention both cameras and lighting.
    has_scaffold = "prompt_scaffold" in groups
    has_prompt_detail = bool(_PROMPT_DETAIL_SIGNALS.search(rendered))
    if not (
        (has_scaffold and len(groups) >= 2)
        or (len(groups) >= 2 and has_prompt_detail)
    ):
        word_count = len(_WORD_RE.findall(rendered))
        list_count = len(_LIST_LINE_RE.findall(rendered))
        heading_count = len(_EXPLANATORY_HEADING_LINE_RE.findall(rendered))
        has_generic_tail = bool(_GENERIC_FOLLOW_UP_RE.search(rendered))
        detail_requested = str(policy.get("detail_requested", "false")) == "true"
        # A normal conversational question does not need an unsolicited
        # mini-article. A list with three or more items is strong enough
        # structural evidence once it is long, even when the model omits the
        # usual generic follow-up line. Explicit detail requests stay exempt.
        # A bounded detail request (an open advice request) may carry up to
        # `max_items` items; only a longer list is the unsolicited expansion.
        max_items = _max_items(policy)
        structure_limit = (max_items + 1) if max_items is not None else 3
        long_structure = list_count >= structure_limit or heading_count >= structure_limit
        unsolicited_list_overanswer = (
            not detail_requested
            and word_count >= 120
            and long_structure
        )
        boilerplate_overanswer = (
            not detail_requested
            and word_count >= 180
            and long_structure
            and has_generic_tail
        )
        if not policy.get("brevity_advisory") and (unsolicited_list_overanswer or boilerplate_overanswer):
            overanswer_signals = ["long_response"]
            if list_count >= 3:
                overanswer_signals.append("list_block")
            if heading_count >= 3:
                overanswer_signals.append("heading_block")
            if has_generic_tail:
                overanswer_signals.append("generic_follow_up")
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("boilerplate_overanswer",),
                signal_groups=tuple(overanswer_signals),
            )
        if (
            not policy.get("brevity_advisory")
            and not detail_requested
            and has_generic_tail
            and not _ASSISTANCE_REQUEST_RE.search(current_user_text)
        ):
            return OrdinaryChatOutputCheck(
                allowed=False,
                reasons=("unsolicited_generic_follow_up",),
                signal_groups=("generic_follow_up",),
            )
        max_words = _max_words(policy)
        question_for_underanswer = _underanswer_question(normalized_user_text)
        short_open_answer = (
            word_count <= 1
            and bool(_OPEN_EXPLANATION_QUESTION_RE.search(question_for_underanswer))
            and not bool(_EXPLICIT_WORD_SHAPE_RE.search(normalized_user_text))
        )
        # An answer read from the memory records the reader request carried is not sent back for
        # its length. A length verdict is repaired by a second reader call over the whole context
        # and evidence set (free lanes), else by a cut at the word ceiling ("too long") or the
        # safe fallback ("too short"). An external review measured, on a 94-question memory
        # check, six such second passes at 57,579 tokens (about 6% of the run), five of them on
        # preference questions, and one rewrite that moved an unrelated preference to the front
        # and changed the meaning; the cut can stop a list part-way. So a complete short answer
        # from the records ships as it is, and a long one keeps every item it names; the verdict
        # is recorded in `waived_reasons`. Only these two verdicts are waived: every semantic and
        # safety verdict above still rejects, and a "too short" draft that is not a complete
        # answer at all (a bare list marker) is still sent back.
        answers_from_records = bool(policy.get("memory_records_supplied"))
        waived: list[str] = []
        if short_open_answer:
            from core.incomplete_answer import inspect_answer_completeness

            if not answers_from_records or inspect_answer_completeness(rendered).incomplete:
                return OrdinaryChatOutputCheck(
                    allowed=False,
                    reasons=("ordinary_answer_too_short",),
                    signal_groups=("underanswer",),
                )
            waived.append("ordinary_answer_too_short")
        if not policy.get("brevity_advisory") and max_words is not None and word_count > max_words:
            if not answers_from_records:
                return OrdinaryChatOutputCheck(
                    allowed=False,
                    reasons=("ordinary_response_too_long",),
                    signal_groups=("response_budget",),
                )
            waived.append("ordinary_response_too_long")
        return OrdinaryChatOutputCheck(
            allowed=True,
            signal_groups=tuple(groups),
            completeness_status=completeness_status,
            waived_reasons=tuple(waived),
        )

    return OrdinaryChatOutputCheck(
        allowed=False,
        reasons=("image_prompt_shaped_output",),
        signal_groups=tuple(groups),
    )


def remove_unrequested_prior_turn_literals(
    text: str,
    policy: dict[str, Any] | None,
    *,
    current_user_text: str = "",
) -> str:
    """Remove only complete sentences that disclose an unrequested prior identifier."""
    if not isinstance(policy, dict):
        return str(text or "").strip()
    prior_literal_hashes = {
        str(value).strip().lower()
        for value in list(policy.get("prior_turn_literal_hashes") or [])
        if str(value).strip()
    }
    if not prior_literal_hashes:
        return str(text or "").strip()
    current_literal_hashes = {
        _literal_hash(value)
        for value in _guard_literal_values(current_user_text)
    }
    kept: list[str] = []
    for sentence in _SENTENCE_RE.findall(str(text or "").strip()):
        sentence_hashes = {
            _literal_hash(value)
            for value in _guard_literal_values(sentence)
        }
        if sentence_hashes & (prior_literal_hashes - current_literal_hashes):
            continue
        if sentence.strip():
            kept.append(sentence)
    return "".join(kept).strip()


def remove_unsolicited_generic_follow_up(text: str) -> str:
    """Remove only generic assistance-offer sentences from an otherwise valid reply."""
    return "".join(
        sentence
        for sentence in _SENTENCE_RE.findall(str(text or "").strip())
        if sentence.strip() and not _GENERIC_FOLLOW_UP_RE.search(sentence)
    ).strip()


def numbered_parts_instruction(required_parts: int) -> str:
    """Render only the server-derived explicit user numbering contract."""

    count = int(required_parts or 0)
    if count < 2:
        return ""
    return (
        f" Answer all {count} requested parts and number them 1 through {count}; "
        "do not omit or merge a part."
    )


def short_answer_instruction(policy: dict[str, Any] | None) -> str:
    """The answer shape a user's explicit length request asks for, or "" without one.

    A word ceiling alone left the shape to the model: answers met the 64-word budget while opening
    with "Based on the records, ..." and restating the question around a one-phrase fact. The
    shape is stated before the first call and on the one rewrite. Completeness and notices are
    kept explicitly: brevity shortens each item or part, it never drops one, and it never removes
    the sentence that says the evidence does not support an answer or a needed safety notice.
    """
    if str(dict(policy or {}).get("answer_shape") or "") != "short":
        return ""
    return (
        " The user asked for a short answer: give the answer first, in as few words as the fact "
        "needs (a phrase or one short sentence). No preamble such as \"Based on the records\", "
        "no restating the question, no narration of where the answer came from, and no hedging "
        "boilerplate. When the records name more than one thing that answers the question "
        "(several items, people, places, reasons or activities), name every one of them, tersely, "
        "even if the question does not ask for a list; a multi-part "
        "request still answers every part, tersely. A multiple-choice answer leads with the "
        "chosen option. When the same person's records from different dates give different "
        "values for the same thing, the latest-dated one is the current value unless the question "
        "asks about an earlier time: give it first, with its date. "
        "If the evidence does not support an answer, say so in one short "
        "sentence; but when the user asks for suggestions, tips or recommendations, give them, "
        "tailored to the preferences, plans, purchases and resources the records state, naming "
        "those details, since the records need not contain the suggestion itself. Keep any "
        "safety notice the answer needs."
    )


def _asks_event_time(user_text: str) -> bool:
    from core.temporal_question_scope import asks_event_time

    return asks_event_time(user_text)


def relative_event_time_instruction(policy: dict[str, Any] | None) -> str:
    """The relative-time rule for a memory question that asks when something happened.

    Measured on archived dev answers: a record stated on one date that says the event happened
    "last weekend" or "last week" was answered with the record's own date, which dates the event
    to the day it was talked about. A relative time is kept, anchored to the date the record was
    stated; the record's date is the event date only when the record says the event happened that
    day; two records that bound the event give a range. Empty unless the policy marks the turn.
    """
    if not dict(policy or {}).get("relative_event_time"):
        return ""
    return (
        " This question asks when something happened. When a record describes the event with a "
        "relative time (yesterday, last night, last weekend, last week, two weeks ago, a few days "
        "ago, next month, recently), answer with that relative time anchored to the date the "
        "record was stated, for example \"the weekend before 12 June 2021\", \"the day before "
        "3 May 2021\" or \"the week before 9 September 2021\". Never give the record's stated "
        "date as the event date unless the record says the event happened that day. When two "
        "records bound the event (one before it, one after it), give the range between their "
        "dates."
    )


def not_mentioned_option_instruction(policy: dict[str, Any] | None) -> str:
    """The premise rule for a multiple-choice turn whose options include a not-mentioned choice.

    A record about one person or thing is not evidence about another: a factual option that the
    records attribute to someone else, or state about a different thing, is the trap the
    not-mentioned option exists for. Measured on archived dev answers with the earlier rule in the
    prompt: the reader still chose a factual option whose words appeared in a record that only
    asked about it, or chose it while noting the records attribute it to someone else. Those two
    shapes are named. Empty unless the turn offers such an option.
    """
    if not dict(policy or {}).get("not_mentioned_option_offered"):
        return ""
    return (
        " One offered option says the information is not mentioned. Choose a factual option only "
        "if a record states that fact about the exact person and the exact thing the question "
        "names (a speaker's words about themselves count). If the records attribute it to someone "
        "else, state it about a different thing, only ask about it, or do not state it, choose the "
        "not-mentioned option, even if the option's words appear in a record; never qualify a "
        "factual option as someone else's."
    )


def person_attribution_instruction(policy: dict[str, Any] | None) -> str:
    """The attribution rule for a record question that names a person and offers no fallback.

    Measured on archived dev answers: the records held the asked fact, said by or about a
    different person than the one the question named, and the reader opened with "the records
    don't state this" before quoting it. The fact is given first, then whom the records attribute
    it to, so the owner is stated and nothing is merged. Empty unless the policy marks the turn.
    """
    if not dict(policy or {}).get("person_attribution"):
        return ""
    return (
        " If the records state the asked fact only for someone other than the person the question "
        "names, give that fact first, then say whom the records attribute it to; do not open with "
        "a denial."
    )


def linked_records_instruction(policy: dict[str, Any] | None) -> str:
    """The linked-records rule for a question over retrieved records.

    Measured on archived dev answers: the reader said the records do not specify while a record
    stated on the same date as the matched event, or on the date the question names, held the
    answer. Records stated together are read together; an inference from them is marked. Empty
    unless the policy marks the turn.
    """
    if not dict(policy or {}).get("linked_records"):
        return ""
    return (
        " Before saying the records do not state it, combine the records stated on the same date "
        "as the record matching the asked event, or on a date the question names, marking an "
        "inference as inferred."
    )


def ordinary_chat_retry_instruction(policy: dict[str, Any] | None = None) -> str:
    """One neutral repair instruction; it contains no expected answer text.

    The rewrite must not read as "drop what the user told you earlier": the earlier-turn ban
    stripped the user's own details from advice answers (holdout preference drafts, user-evidence
    words 82 -> 28 -> 20). So the instruction asks to keep the user's own context, and it names
    earlier opaque codes only when this chat really holds one (a prior-turn literal hash). The
    hash check in `_prior_literal_contamination` stays the enforcement either way.
    """
    policy = dict(policy or {})
    budget = _max_words(policy)
    budget_instruction = (
        f" Keep the reply to at most {budget} words."
        if budget is not None
        else ""
    )
    parts_instruction = numbered_parts_instruction(
        int(policy.get("required_numbered_parts") or 0)
    )
    has_prior_literals = any(
        str(value).strip() for value in list(policy.get("prior_turn_literal_hashes") or [])
    )
    prior_literal_instruction = (
        "Do not repeat earlier opaque codes or identifiers unless the user asks for them. "
        if has_prior_literals
        else ""
    )
    max_items = _max_items(policy)
    structure_instruction = (
        f"Use at most {max_items} items and no unsolicited follow-up offer. "
        if max_items is not None
        else "Avoid generic lists and an unsolicited follow-up offer when the user did not ask for detail. "
    )
    return (
        "Answer only the user's current request as normal, directly relevant text. "
        "Keep the details from the user's own context that make this answer fit them. "
        "Do not add an analogy or comparison the user did not ask for. "
        + prior_literal_instruction
        + "Preserve every explicitly requested identifier exactly. "
        + structure_instruction
        + "Do not write an image, "
        "video, scene, camera, lighting, style, or prompt-description block unless the user "
        "explicitly asked for creative media prompting."
        + parts_instruction
        + short_answer_instruction(policy)
        + relative_event_time_instruction(policy)
        + not_mentioned_option_instruction(policy)
        + person_attribution_instruction(policy)
        + linked_records_instruction(policy)
        + budget_instruction
    )


def constrain_ordinary_chat_output(
    text: str,
    policy: dict[str, Any] | None,
) -> str:
    """Trim only a normal-chat overrun after its one model rewrite."""
    rendered = str(text or "").strip()
    max_words = _max_words(dict(policy or {}))
    words = list(_WORD_RE.finditer(rendered))
    if max_words is None or len(words) <= max_words:
        return rendered
    bounded = rendered[: words[max_words - 1].end()].rstrip(" \t\r\n,;:-")
    cut = _last_sentence_end(bounded)
    return bounded[:cut].rstrip() or bounded


def _last_sentence_end(text: str) -> int:
    """Offset just past the last real sentence terminator in `text`, or 0 if there is none.

    Two bugs lived in the previous form, and both reached users on the served surface. It collected
    sentences with `_SENTENCE_RE`, stripped each one, and rejoined them with `" "`::

        1.8L to 2.4L   ->   1. 8L to 2. 4L

    `_SENTENCE_RE` splits on any `.`, so the decimal point ended a "sentence"; stripping and
    rejoining then inserted a space that was never in the text. Measured live: "Here are the top 5
    best-selling cars ... 1. Toyota Corolla: ~186 mph (300 km/h), 1. 8L to 2. 4L", every engine size
    mangled. The rejoin also flattened newlines, so a numbered list arrived as one paragraph.

    Cutting the ORIGINAL text at an offset fixes both: nothing is re-assembled, so no space can be
    introduced and the list keeps its line breaks.

    A terminator is any run of `.!?` that is neither of the two things that merely look like one:

      * a decimal point -- digits on both sides (`1.8`);
      * a list marker -- digits at the start of a line (`4. Ford Focus`), which is what left an
        answer ending on a bare "3." after the word cap landed there.
    """

    last = 0
    for match in _TERMINATOR_RUN_RE.finditer(text):
        if _is_decimal_point(text, match) or _is_list_marker(text, match):
            continue
        last = match.end()
    return last


_TERMINATOR_RUN_RE = re.compile(r"[.!?]+")


def _is_decimal_point(text: str, match: re.Match[str]) -> bool:
    return (
        match.group(0) == "."
        and match.start() > 0
        and text[match.start() - 1].isdigit()
        and match.end() < len(text)
        and text[match.end()].isdigit()
    )


def _is_list_marker(text: str, match: re.Match[str]) -> bool:
    if match.group(0) != ".":
        return False
    index = match.start()
    if index == 0 or not text[index - 1].isdigit():
        return False
    while index > 0 and text[index - 1].isdigit():
        index -= 1
    while index > 0 and text[index - 1] in " \t":
        index -= 1
    return index == 0 or text[index - 1] == "\n"


def recover_bounded_ordinary_chat_output(
    text: str,
    policy: dict[str, Any] | None,
    *,
    current_user_text: str = "",
) -> str:
    """Remove a recognized unsolicited tail, never answer-bearing structure.

    A length/style check cannot establish that a prefix satisfies the request.
    Lists, headings and later sentences can contain the entire answer. If the
    remaining provider text still fails the guard, return no recovery and let
    the caller publish its typed failure, rather than certify a discarded task.
    """

    bounded = remove_unsolicited_generic_follow_up(str(text or "").strip())
    if len(_WORD_RE.findall(bounded)) < 3:
        return ""
    check = inspect_ordinary_chat_output(
        bounded,
        policy,
        current_user_text=current_user_text,
    )
    return bounded if check.allowed else ""


def ordinary_chat_safe_fallback() -> str:
    return "I couldn't produce a normal chat response for that request. Please try again."


def ordinary_chat_overanswer_failure(model_name: str = "") -> str:
    """Typed terminal message when neither same-provider rewrite nor local bounding is safe."""

    model = str(model_name or "").strip()
    subject = f"The pinned model `{model}`" if model else "The selected model"
    return (
        f"{subject} returned an overlong response that could not be safely condensed within the "
        "answer budget. No alternate model, paid call, or tool was used."
    )
