"""Explicit final-deliverable contracts for the current user turn.

The contract is deliberately opt-in. Ordinary answers are not style-scored here. A contract is
created only from direct output instructions, then travels as typed metadata to both the provider
boundary and the final UI boundary. The final application may remove presentation syntax, bind an
explicit literal, or reject a shape violation; it never invents semantic answer content.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

# A separator INSIDE a token is not a boundary between tokens: "03:12" is one word, and so is
# "12:30:45". `core.response_constraints` was taught this earlier (a one-word clock contract was
# failing there for the same reason); this module kept its own counter and kept splitting, so the
# same answer was one word by one module and two by the other. Measured live 2026-08-15 (hostile
# seed 5150): the clock lane declined "what time is it in berlin Output exactly one word. asap"
# because 03:12 failed a one-word contract it satisfied, and a model then answered 13:12 for a
# city where it was 03:12.
_WORD_RE = re.compile(r"\b[\w'-]+(?::\d+[\w'-]*)*\b", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)", re.MULTILINE)
_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_COUNT_TOKEN = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|\d{1,2}"
)
_SUPPORTED_BULLET_MARKERS = frozenset({"-", "*", "+", "•"})
_SUPPORTED_DELIMITERS = frozenset({","})

_RAW_TEXT_ONLY_RE = re.compile(
    r"\b(?:(?:raw\s+(?:text|output)|plain[-\s]+text)\s+only|"
    r"only\s+(?:raw\s+text|plain[-\s]+text))\b",
    re.IGNORECASE,
)
_NOTHING_EXCEPT_RE = re.compile(
    r"\b(?:output|return|reply\s+with|respond\s+with|write|print|give\s+me)\s+"
    r"nothing\s+(?:except|but|other\s+than)\b",
    re.IGNORECASE,
)
_NOTHING_ELSE_RE = re.compile(
    r"\b(?:and\s+)?noth(?:ing|in|ng)\s+else\b",
    re.IGNORECASE,
)
_FINAL_ONLY_RE = re.compile(
    r"\b(?:final\s+(?:answer|response)|answer|deliverable|result)\s+only\b|"
    r"\bonly\s+(?:the\s+)?(?:final\s+(?:answer|response)|answer|deliverable|result)\b",
    re.IGNORECASE,
)
_ONLY_OUTPUT_RE = re.compile(
    r"\b(?:only|just)\s+(?:say|output|return|print|reply(?:\s+with)?|respond(?:\s+with)?)\b|"
    r"\b(?:output|return|print)\s+only\b",
    re.IGNORECASE,
)
_PROHIBITION_BOUNDARY = r"(?:^|[,;.!]\s*|\b(?:and|but|or)\s+)"
_NO_MARKDOWN_RE = re.compile(
    _PROHIBITION_BOUNDARY
    + r"(?:no|without)\s+(?:any\s+)?markdown\b|"
    r"\bdo\s+not\s+(?:use|include|output|return)\s+(?:any\s+)?markdown\b",
    re.IGNORECASE,
)
_NO_JSON_RE = re.compile(
    _PROHIBITION_BOUNDARY
    + r"(?:no|without)\s+(?:any\s+)?json(?:\s+output)?\b|"
    r"\bdo\s+not\s+(?:use|include|output|return)\s+(?:any\s+)?json\b",
    re.IGNORECASE,
)
_NO_INTERNAL_THOUGHT_RE = re.compile(
    r"\b(?:no|without|do\s+not\s+(?:include|show|output|return|reveal))\b"
    r"[^.;\n]{0,96}\b(?:internal\s+thoughts?|thought\s+process|thinking|reasoning|analysis|"
    r"scratchpad|checklist|planning\s+notes?)\b",
    re.IGNORECASE,
)
_NO_TITLE_RE = re.compile(
    r"(?:^|[,;.]\s*|\bonly\s+|\b(?:and|or)\s+)(?:no|without)\s+(?:a\s+)?title\b|"
    r"\bdo\s+not\s+(?:include|add|write|show)\b[^.;\n]{0,64}\btitle\b",
    re.IGNORECASE,
)
_NO_PUNCTUATION_RE = re.compile(
    r"(?:^|[,;.]\s*|\b(?:and|or)\s+)(?:no|without)\s+(?:any\s+)?punctuation\b|"
    r"\bdo\s+not\s+(?:include|add|use)\s+(?:any\s+)?punctuation\b",
    re.IGNORECASE,
)
# "No punctuation AT THE END" is a trailing-scope constraint (drop the final period), not a blanket
# character ban. Measured live 2026-08-15: the deterministic sequence lane computed the exact
# requested "[4,3,10]" and this seam, reading the scoped phrase as blanket ``no_punctuation``,
# stripped the brackets the SAME directive demanded ("with brackets and commas"), judged the
# remainder non-compliant, and shipped the unsalvageable notice over a correct answer.
_NO_TRAILING_PUNCTUATION_RE = re.compile(
    r"(?:no|without)\s+(?:any\s+)?punctuation\s+(?:at\s+the\s+end\b|trailing\b)|"
    r"(?:no|without)\s+(?:any\s+)?trailing\s+punctuation\b|"
    r"\bdo\s+not\s+(?:include|add|use)\s+(?:any\s+)?punctuation\s+at\s+the\s+end\b",
    re.IGNORECASE,
)
# A directive that itself requests punctuation-bearing structure cannot also mean "no punctuation
# anywhere" -- the explicit structural ask wins and the ban narrows to trailing scope.
_PUNCTUATION_BEARING_FORMAT_RE = re.compile(
    r"\b(?:with|in)\s+brackets?\b|\bwith\s+(?:brackets?\s+and\s+)?commas?\b|"
    r"\bbrackets?\s+and\s+commas?\b|\bcomma[-\s]separated\b|"
    r"\bwith\s+braces\b|\bwith\s+parentheses\b",
    re.IGNORECASE,
)
_NO_EXTRA_RE = re.compile(
    r"\b(?:no|without|do\s+not\s+(?:include|add|write|show))\b"
    r"[^.;\n]{0,96}\b(?:intro(?:duction)?|introductory\s+text|preamble|explanation|notes?|"
    r"checklist|title|metadata)\b",
    re.IGNORECASE,
)
_EXPLICIT_JSON_RE = re.compile(
    r"\b(?:return|output|reply\s+with|respond\s+with|write|use|using|as|in)\s+"
    r"(?:the\s+result\s+)?(?:only\s+)?json\b|\bjson\s+only\b",
    re.IGNORECASE,
)
# The ONLY-scoped JSON shape is a binding output contract, not a soft preference. Measured
# live 2026-08-29: "return JSON only {…}; receipt must be separate metadata not appended
# text" set explicit_json (which only suppresses no_json) but never raw_only/no_markdown,
# so no contract engaged, the model's fenced draft plus an appended "Metadata:" block was
# served verbatim. Deliberately narrower than _EXPLICIT_JSON_RE: "as/in JSON" soft phrasing
# stays soft.
_JSON_ONLY_RE = re.compile(r"\bjson\s+only\b|\bonly\s+json\b", re.IGNORECASE)
_EXPLICIT_MARKDOWN_RE = re.compile(
    r"\b(?:return|output|reply\s+with|respond\s+with|write|use|using|as|in)\s+"
    r"(?:the\s+result\s+)?(?:only\s+)?markdown\b|\bmarkdown\s+only\b",
    re.IGNORECASE,
)
_WORD_COUNT_RE = re.compile(
    rf"\b(?:exactly\s+|just\s+|only\s+)?(?P<count>{_COUNT_TOKEN}|a\s+single|single)"
    r"(?:\s*[- ]\s*)words?\b",
    re.IGNORECASE,
)
_SENTENCE_COUNT_RE = re.compile(
    rf"\b(?:exactly\s+|just\s+|only\s+)?(?P<count>{_COUNT_TOKEN}|a\s+single|single)"
    r"(?:\s*[- ]\s*)sentences?\b",
    re.IGNORECASE,
)
_LINE_COUNT_RE = re.compile(
    rf"\b(?:exactly\s+|just\s+|only\s+)?(?P<count>{_COUNT_TOKEN})"
    r"(?:\s*[- ]\s*)lines?\b",
    re.IGNORECASE,
)
_BULLET_COUNT_RE = re.compile(
    rf"\b(?:exactly\s+)?(?P<count>{_COUNT_TOKEN})\s+"
    r"(?:plain[- ]text\s+)?(?:bullet(?:[- ]points?)?|bullets?)\b",
    re.IGNORECASE,
)
_BULLET_MARKER_RE = re.compile(
    r"\b(?:using|with|prefixed\s+with|prefix\s+each\s+with|begin\s+each\s+with|w/)\s*"
    r"(?:the\s+)?(?:marker\s*)?(?P<marker>[*+\-•])",
    re.IGNORECASE,
)
_COMMA_ONLY_RE = re.compile(
    r"\bcomma[-\s]+sep[ae]rated\s+(?:values?|vals?|items?|names?|tokens?|words?|entries)\b|"
    r"\b(?:values?|vals?|items?|names?|tokens?|words?|entries)\s+separated\s+by\s+commas\b",
    re.IGNORECASE,
)
_DELIMITED_LITERAL_RE = re.compile(
    r"\b(?:output|return|print|reply(?:\s+with)?|respond(?:\s+with)?|give(?:\s+me)?)\b"
    r"[^.;!?\n]{0,64}?\bcomma[-\s]+sep[ae]rated\s+"
    r"(?:list\s+of\s+)?(?:values?|vals?|items?|names?|tokens?|words?|entries)\b"
    r"\s*:?[ \t]*\((?P<literal>[^()\r\n]{3,200})\)",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"\b(?:actually|changed?\s+my\s+mind|correction|forget\s+(?:that|the\s+previous|json)|"
    r"instead|never\s+mind|no[,;:]?\s+(?:wait|rather)|rather|scratch\s+that|wait(?=\s*[!,:;-]))\b",
    re.IGNORECASE,
)
_TERMINAL_LITERAL_BINDING_RE = re.compile(
    r"(?:"
    r"(?:and\s+)?n[ou]th(?:ing|in|ng)\s+else"
    r"|(?:final\s+(?:answer|output|response)|output|result)\s+only"
    r"|only\s+(?:the\s+)?(?:list|values?|items?|names?|tokens?|words?|entries)"
    r"|(?:no|without)\s+(?:any\s+)?(?:json\s+)?(?:wrappers?|envelopes?)"
    r")",
    re.IGNORECASE,
)
_PRESENTATION_ONLY_TAIL_RE = re.compile(
    r"^[\s,;:.!?-]*(?:(?:and\s+)?(?:"
    r"n[ou]th(?:ing|in|ng)\s+else"
    r"|no\s+(?:json|markdown)(?:\s+(?:wrappers?|envelopes?|formatting))?"
    r"|without\s+(?:any\s+)?(?:json|markdown)(?:\s+(?:wrappers?|envelopes?|formatting))?"
    r"|(?:final\s+(?:answer|output|response)|output|result)\s+only"
    r"|only\s+(?:the\s+)?(?:list|values?|items?|names?|tokens?|words?|entries)"
    r")(?:\s+(?:allowed|please|pls|plz))?[\s,;:.!?-]*)*$",
    re.IGNORECASE,
)
#: `<verb> exactly the string X and nothing else` -- the phrasing the other four patterns all miss.
#:
#: Measured: NONE of nine natural phrasings bound a literal before this existed, including the one
#: reported. The reason is structural rather than a missing synonym -- every other pattern requires
#: the literal to sit IMMEDIATELY after the verb, so any qualifier between them ("exactly", "the
#: string", "the text") breaks the match, and three of the four are `$`-anchored so a trailing
#: instruction sentence breaks it again. The reported turn had both.
#:
#: `nothing else` is required rather than optional. It is the unambiguous signal that the user is
#: constraining the WHOLE reply, and requiring it is what keeps this from binding ordinary prose
#: like "explain exactly how the parser works". A false positive here is expensive: a bound literal
#: is returned verbatim before any model call, so mis-binding ships the user's own instruction text
#: back as the answer.
_QUALIFIED_LITERAL_RE = re.compile(
    # The verb must be an INSTRUCTION, not a noun. "the output was fine and nothing else broke" is
    # ordinary prose, and without this lookbehind it bound the literal "was fine" -- which would be
    # returned verbatim as the answer.
    r"(?<!\ba )(?<!\ban )(?<!\bthe )(?<!\bmy )(?<!\byour )(?<!\bits )(?<!\bthis )(?<!\bthat )"
    r"\b(?:say|output|print|return|reply|respond|answer)(?:\s+with)?\s+"
    r"(?:(?:exactly|only|just|precisely)\s+)*"
    r"(?:the\s+(?:exact\s+)?(?:string|text|word|token|literal|value|phrase)\s+)?"
    r"(?:(?:exactly|only|just|precisely)\s+)*"
    # A FORMAT name is not a literal. "Return JSON only and nothing else" asks for JSON-shaped
    # output; binding "JSON" would echo the word back and discard the payload. Measured against the
    # repo's own negative controls, which this broke on the first attempt.
    # The qualifier run above is greedy but may match ZERO times, so without naming the qualifiers
    # here too the literal simply absorbs them: "Return only Markdown and nothing else" bound the
    # literal "only Markdown". Measured.
    r"(?!(?:exactly|only|just|precisely)\b)"
    # A COUNT plus a shape noun is a shape contract, not a literal. "Reply with exactly three
    # bullets and nothing else" constrains the FORM of the answer; binding "three bullets" would
    # echo the instruction and discard the answer. Measured -- this stole three shape contracts.
    r"(?!(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\s+"
    r"(?:bullets?|words?|sentences?|lines?|items?|points?|paragraphs?|characters?|chars?)\b)"
    # A literal never begins with a preposition; "answer in exactly five words" bound
    # "in exactly five words".
    r"(?!(?:in|with|as|into|using|by|on|to|of)\b)"
    r"(?!(?:json|markdown|md|html|xml|yaml|yml|csv|tsv|text|plaintext|prose|code|"
    r"bullets?|bullet\s+points?|a\s+list|a\s+table|the\s+answer|the\s+result)\b)"
    r"(?P<quote>[\"\'])?(?P<literal>[A-Za-z0-9_][A-Za-z0-9_ -]{0,79}?)(?(quote)(?P=quote))"
    # `nothing else` must END the clause. "and nothing else broke" continues into a new predicate,
    # which means the phrase was describing something, not constraining the reply.
    # An INTENSIFIER may sit between "and" and "nothing": "and absolutely nothing else", "and
    # literally nothing else". Without naming them the non-greedy literal simply swallows them to
    # let the tail match -- measured on the served surface, "Output exactly ERR_NO_K8S_ACCESS and
    # absolutely nothing else" bound the literal "ERR_NO_K8S_ACCESS and absolutely" and shipped it.
    r"\s*[,.]?\s*(?:and\s+)?"
    r"(?:absolutely\s+|literally\s+|strictly\s+|positively\s+|categorically\s+|"
    r"really\s+|truly\s+|simply\s+|just\s+|only\s+)?"
    r"noth(?:ing|in|ng)\s+else"
    # The politeness that may follow is the FULL set people actually type, not three of them:
    # measured live 2026-08-15 (hostile seed 4242), "output exactly ERR_NZMDFS and nothing else
    # thx" bound no literal at all because "thx" was not listed, so the turn shipped a fenced
    # ```ERR_NZMDFS``` -- the contract said "nothing else" and got markdown.
    r"(?=\s*(?:$|[.!?,;]|\b(?:pls|plz|please|thx|thanks|thank\s+you|ty|cheers|mate)\b))",
    re.IGNORECASE,
)
# A quoted literal may span lines and carry any non-quote bytes (Unicode, code, URLs,
# punctuation): the QUOTES are the delimiter, so interior newlines and wide characters are
# payload, not noise. Serves byte-for-byte after the owning `.strip()` of the delimited span.
_QUOTED_LITERAL_RE = re.compile(
    r"\b(?:return|output|print|say|reply(?:\s+with)?)\s+(?:only\s+)?(?:exactly\s+)?"
    r"(?P<quote>[\"'])(?P<literal>[^\"']{1,240}?)(?P=quote)\s*(?:and\s+)?"
    r"noth(?:ing|in|ng)\s+else\b",
    re.IGNORECASE,
)
_SLOPPY_LITERAL_RE = re.compile(
    r"\b(?:only|just)\s+(?:say|output|print|return|reply(?:\s+with)?)\s+"
    r"(?P<literal>[A-Za-z0-9_][A-Za-z0-9_-]{0,79})"
    r"(?:\s+(?:pls|plz|please))?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_NOTHING_ELSE_LITERAL_RE = re.compile(
    r"\b(?:just\s+|only\s+)?(?:say|output|print|return|reply(?:\s+with)?)\s+"
    r"(?P<literal>[A-Za-z0-9_][A-Za-z0-9_-]{0,79})\s+(?:and\s+)?"
    r"noth(?:ing|in|ng)\s+else(?:\s+(?:pls|plz|please))?\s*[.!]?\s*$",
    re.IGNORECASE,
)
# The payload after the colon is the WHOLE rest of the line, byte-for-byte. The pre-cue
# (`exactly this ... and nothing else`) has already constrained the reply, so everything the
# user typed after the delimiter is payload, not instruction: punctuation, Unicode, code and
# URLs stay in, trailing sentence punctuation stays in (stripping it is payload mutation), and
# an empty rest refuses the binding rather than serving the cue text back (measured at
# 84bf8b6a: the sibling rewriter in ``web.api.response_control`` bound `this and nothing
# else: VERBATIM-PROOF-2291` -- the cue residue -- and overwrote the correct bytes at the
# final boundary). Only line boundaries end the payload: a multi-line colon payload is
# ambiguous against a following sentence and fails closed to the model.
_COLON_LITERAL_RE = re.compile(
    r"\b(?:say|output|print|return|reply(?:\s+with)?|respond(?:\s+with)?)\s+"
    r"exactly\s+(?:this|the\s+following)(?:\s+(?:word|text|token))?\s+"
    r"(?:and\s+)?noth(?:ing|in|ng)\s+else\s*:\s*"
    r"(?P<literal>[^\r\n]{1,240}?)[ \t]*$",
    re.IGNORECASE,
)
# The DIRECT colon form -- `verb exactly: PAYLOAD [and nothing else]` -- the family the
# legacy API-boundary rewriter bound alone (mixed-demand P0 pinned `reply with exactly:
# R5-CANARY and nothing else`). Typed ownership means the fast path serves it with zero model
# calls and the same bytes flow to the seal and finalization. One terminal punctuation mark
# after the payload (or after the `nothing else` cue) is message punctuation, matching the
# convention this family has always served under; the payload itself is otherwise verbatim.
_DIRECT_EXACTLY_COLON_LITERAL_RE = re.compile(
    r"\b(?:say|output|print|return|reply(?:\s+with)?|respond(?:\s+with)?)\s+"
    r"exactly\s*:\s*"
    r"(?P<literal>[^\r\n]{1,240}?)"
    r"(?:[ \t]+(?:and\s+)?noth(?:ing|in|ng)\s+else)?"
    r"[ \t]*[.!?,;]?[ \t]*$",
    re.IGNORECASE,
)
_STRUCTURED_SCALAR_LITERAL_RE = re.compile(
    r"^\s*(?:(?:please|just)\s+)*(?:say|output|print|return|reply(?:\s+with)?|"
    r"respond(?:\s+with)?)\s+"
    r"(?:only\s+)?(?:the\s+)?(?P<kind>word|number|digit|character|token)\s+"
    r"(?P<literal>[A-Za-z0-9_][A-Za-z0-9_-]{0,79})"
    r"(?:\s+(?:please|pls|plz))?\s*[.!]?\s*$",
    re.IGNORECASE,
)
_ONE_CHARACTER_RE = re.compile(
    r"\b(?:one|1|a\s+single|single)(?:\s*[- ]\s*)characters?\b",
    re.IGNORECASE,
)
_PAYLOAD_AUTHORITY_RE = re.compile(
    r"\b(?:process|follow|obey|apply|execute)\b[^.\n]{0,96}"
    r"\b(?:payload|instructions?|constraints?|format)\b[^.\n]{0,96}"
    r"\b(?:exactly|requested|written|specified|above)\b",
    re.IGNORECASE,
)
_REQUEST_OUTPUT_VERB_RE = re.compile(
    r"\b(?:answer|describe|explain|name|tell|write|compose|return|output|reply|respond|"
    r"summari[sz]e|give|gimme|list|say|print|provide|create|draft|make)\b",
    re.IGNORECASE,
)
_SHAPE_ONLY_RE = re.compile(
    r"\b(?:words?|sentences?|lines?|bullets?|bullet[- ]points?)\s+only\b",
    re.IGNORECASE,
)
# "just the number" / "number only" as an OUTPUT-SHAPE instruction — gated in the
# parser by output context (an output verb before it, arithmetic in the turn, or
# tail position) and by the "number of X" quantity-reference exclusion, so "only
# the number of votes matters" stays prose. See the parser comment at the fold-in
# site for the measured live failure.
_NUMBERS_ONLY_RE = re.compile(
    r"\b(?:answer\s+with\s+just\s+the|just\s+the|in)\s+numbers?\s+only\b"
    r"|\b(?:just|in)\s+numbers?\s+only\b"
    r"|\bjust\s+the\s+numbers?\b"
    r"|\bnumbers?\s+only\b",
    re.IGNORECASE,
)
_NUMBERS_ONLY_OF_RE = re.compile(
    r"\bnumbers?\s+only\b[^.!?\n]{0,16}\bof\b|\bof\b[^.!?\n]{0,16}\bnumbers?\s+only\b",
    re.IGNORECASE,
)
# "just the number" / "number only" binds the same byte-strict contract as
# "JSON only": the served answer must be the bare figure — no prose, no list,
# no units, no markdown. Measured live 2026-08-29 (watch session): "what is
# 2 + 2? answer with just the number" was served as a numbered list that also
# stated the date, while a sibling phrasing happened to comply by model luck.
# A bare-number contract removes the luck. False-positive surface matches the
# words-only shape beside it: output-shaping instructions, not prose.
_REQUEST_CLAUSE_RE = re.compile(
    r"^\s*(?:please\s+|pls\s+|just\s+|(?:can|could|would)\s+you\s+)?"
    r"(?:answer|calculate|compare|compose|compute|create|define|describe|draft|end|explain|"
    r"finish|give|gimme|identify|list|make|name|output|print|provide|reply|respond|return|"
    r"say|solve|summari[sz]e|tell|write|xplain)\b",
    re.IGNORECASE,
)
_INTERROGATIVE_REQUEST_RE = re.compile(
    r"\b(?:how|what|whats|when|where|which|who|whom|whose|why)\b",
    re.IGNORECASE,
)
_ARITHMETIC_REQUEST_RE = re.compile(r"\b\d+\s*(?:[x×*+/]|-(?!\s))\s*\d+\b", re.IGNORECASE)
_ENUMERATED_CLAUSE_RE = re.compile(
    r"(?:^|(?<=\s))(?P<label>(?:[A-Za-z]|\d{1,2})[.)-])\s*"
)
_WHOLE_FENCE_RE = re.compile(
    r"\A```[^\n]*\n(?P<body>[\s\S]*?)\n?```\s*\Z",
    re.IGNORECASE,
)
#: An EMBEDDED fence -- prose around a fenced block. `_WHOLE_FENCE_RE` deliberately matches only a
#: reply that is nothing but one fence, so "Here is the function:\n```js\n...\n```" reached the
#: violation check still wearing its markdown and was erased wholesale. The deliverable inside the
#: fence is recoverable without inventing a word; see `_repair_from_embedded_fences`.
_FENCE_BLOCK_RE = re.compile(r"```[^\n]*\n(?P<body>[\s\S]*?)\n?```")
#: Programming languages a turn can name as its deliverable. Longest alternatives first so
#: "javascript" never half-matches as "java".
_CODE_LANGUAGE = (
    r"(?:javascript|typescript|powershell|python|golang|csharp|kotlin|haskell|scala|"
    r"swift|rust|ruby|bash|zsh|shell|perl|java|lua|sql|php|cpp|go|js|ts)"
)
#: "Output ONLY raw javascript" / "python only" -- the deliverable is code, stated as a format.
_RAW_CODE_ONLY_RE = re.compile(
    rf"\b(?:only\s+)?raw\s+{_CODE_LANGUAGE}\b|\b{_CODE_LANGUAGE}\s+(?:code\s+)?only\b",
    re.IGNORECASE,
)
#: "write a 2-line javascript function" -- the deliverable is code, stated as a request. The verb
#: is required so "explain how a python function works" never marks the contract as code.
_CODE_DELIVERABLE_REQUEST_RE = re.compile(
    rf"\b(?:write|create|give|generate|draft|make|compose|produce|output|return|emit)\b"
    rf"[^.;\n]{{0,64}}?\b{_CODE_LANGUAGE}\s+"
    r"(?:function|script|program|snippet|class|module|one-?liner|code)\b",
    re.IGNORECASE,
)
#: Structural code signals, judged line-anchored or symbol-level on purpose: prose ABOUT code says
#: "function" and "return" mid-sentence but does not carry braces, semicolons, arrows, or a line
#: that OPENS with a definition keyword or comment marker. Used only to decide whether a
#: shape-violating draft under a code contract holds a salvageable deliverable; a fully compliant
#: reply is never run through this.
_CODE_SIGNAL_RE = re.compile(
    r"[{};]|=>|</\w|/>"
    r"|^\s*(?:function|def|class|import|from|const|let|var|public|private|#include|select)\b"
    r"|^\s*(?://|#|--)\s",
    re.IGNORECASE | re.MULTILINE,
)
#: A runtime-internal action-dict echo trailing a deliverable: `{action: write, content: ...}`.
#: Keyed on the LEADING KEY NAME, not on validity -- the measured echo was unquoted and unparseable
#: as JSON, which is exactly how it escaped the strict-JSON tool-span stripper. A leading key like
#: "name" stays untouched so a pasted package.json is never mistaken for scaffolding.
_ACTION_DICT_ECHO_LINE_RE = re.compile(
    r"^\s*\{\s*[\"']?(?:action|tool|tool_name|tool_call|command|function_call)[\"']?\s*:[^\n]*\}\s*$",
    re.IGNORECASE,
)
#: Violations that describe the SHAPE of a real deliverable (a count that missed), as opposed to
#: content-class violations (markdown, json, scaffolding, a bound literal) where the text itself is
#: not the deliverable. The split decides repair-versus-erase in `apply_raw_output_contract`.
_SHAPE_ONLY_VIOLATIONS = frozenset(
    {
        "exact_words",
        "exact_sentences",
        "exact_lines",
        "bullet_count",
        "bullet_marker",
        "delimiter_only",
        "trailing_punctuation",
    }
)
_LEADING_ANSWER_LABEL_RE = re.compile(
    r"\A\s*(?:#{1,6}\s*)?(?:final\s+answer|answer|output|result|haiku|poem|title)\s*:\s*",
    re.IGNORECASE,
)
_LEADING_NAMED_HEADING_RE = re.compile(
    r"\A\s*#{1,6}\s+(?:final\s+answer|answer|output|result|haiku|poem|title)\s*\n+",
    re.IGNORECASE,
)
_SCRATCHPAD_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:analysis|reasoning|internal\s+thoughts?|scratchpad|checklist|notes?|metadata)\s*:?\s*$",
    re.IGNORECASE,
)
_SCRATCHPAD_LINE_RE = re.compile(
    r"^\s*(?:(?:[-*+•]|\d+[.)]|\[[ xX]\])\s*)?"
    r"(?:identify|select|choose|verify|check|ensure|count|confirm|review|refine|"
    r"draft|write|compose|consider|decide|determine|analy[sz]e|validate|inspect|"
    r"make\s+sure)\b",
    re.IGNORECASE,
)
_INTERNAL_NARRATION_LINE_RE = re.compile(
    r"^\s*(?:(?:[-*+•]|\d+[.)])\s*)?(?:i|we)\s+(?:need|should|must|will)\s+to\b",
    re.IGNORECASE,
)
_ANY_LIST_MARKER_RE = re.compile(r"^\s*(?P<marker>[-*+•]|\d+[.)])\s+(?P<body>\S.*)$")
_JSON_DELIVERABLE_KEYS = frozenset(
    {
        "answer",
        "content",
        "final",
        "final_answer",
        "haiku",
        "output",
        "poem",
        "response",
        "summary",
        "text",
    }
)
_STRUCTURED_USER_DIRECTIVE_KEYS = (
    "intent",
    "task",
    "format",
    "constraints",
    "rule",
    "rules",
)


@dataclass(frozen=True)
class RawOutputContract:
    raw_only: bool = True
    no_markdown: bool = False
    no_internal_thought: bool = False
    no_json: bool = False
    no_punctuation: bool = False
    #: Only the very end of the answer may not carry sentence punctuation; structural characters
    #: inside the deliverable ("[4,3,10]") are content, not violations.
    no_trailing_punctuation: bool = False
    no_title: bool = False
    exact_text: str | None = None
    exact_words: int | None = None
    exact_sentences: int | None = None
    exact_lines: int | None = None
    bullet_count: int | None = None
    bullet_marker: str | None = None
    delimiter: str | None = None
    structured_labels: tuple[str, ...] = ()
    per_item_exact_words: int | None = None
    row_allowed_values: tuple[str, ...] = ()
    #: The turn asked for CODE as the deliverable ("Output ONLY raw javascript", "write a 2-line
    #: python function"). Never used to reject a compliant reply; it only decides whether a
    #: shape-violating draft still holds something worth shipping instead of an erased answer.
    code_deliverable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredUserDirective:
    """A narrow user-authored JSON request with separate task and output authority.

    The ``command`` key is deliberately absent.  Chat JSON is user content, not a second
    system-message channel; only the task/intent and presentation fields used by the shipped
    composer are admitted.
    """

    task_text: str
    output_directives: tuple[str, ...]
    constraints: tuple[str, ...]

    @property
    def output_directive_text(self) -> str:
        return ". ".join(self.output_directives)


@dataclass(frozen=True)
class RawOutputApplication:
    text: str
    changed: bool
    rejected: bool
    compliant: bool
    violations: tuple[str, ...]
    actions: tuple[str, ...]
    #: The fully repaired draft BEFORE any erase decision -- what the closest compliant text looks
    #: like even when a content-class violation forces `text` to "". Callers that must never ship a
    #: silent empty answer read this to decide what actually existed.
    repaired_text: str = ""


def _bounded_count(raw: Any, *, maximum: int = 200) -> int | None:
    clean = " ".join(str(raw or "").lower().split())
    if clean in {"a single", "single"}:
        return 1
    if clean in _COUNT_WORDS:
        return _COUNT_WORDS[clean]
    if not clean.isdigit():
        return None
    value = int(clean)
    return value if 1 <= value <= maximum else None


def _masked_quoted_text(text: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"|\'[^\'\n]*\'', " ", text)


def _flatten_payload_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _flatten_payload_strings(child)]
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in _flatten_payload_strings(child)]
    return []


def parse_structured_user_directive(user_text: str) -> StructuredUserDirective | None:
    """Parse only the typed JSON envelope the chat composer is allowed to author.

    Ordinary JSON data, pasted assistant/system payloads, and JSON with trailing prose remain
    ordinary user text.  Keeping this parser narrow lets routing inspect the actual task without
    granting control authority to arbitrary JSON keys.
    """

    raw = str(user_text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        payload, end = json.JSONDecoder().raw_decode(raw)
    except (TypeError, ValueError):
        return None
    if raw[end:].strip() or not isinstance(payload, dict):
        return None
    role = str(payload.get("role") or "user").strip().casefold()
    if role != "user":
        return None
    task_value = payload.get("intent", payload.get("task"))
    task_text = task_value.strip() if isinstance(task_value, str) else ""
    if not task_text or not any(
        key in payload for key in ("format", "constraints", "rule", "rules")
    ):
        return None
    output_directives = tuple(
        item.strip()
        for key in ("format", "constraints", "rule", "rules")
        for item in _flatten_payload_strings(payload.get(key))
        if item.strip()
    )
    if not output_directives:
        return None
    return StructuredUserDirective(
        task_text=task_text,
        output_directives=output_directives,
        constraints=tuple(
            item.strip()
            for key in ("constraints", "rule", "rules")
            for item in _flatten_payload_strings(payload.get(key))
            if item.strip()
        ),
    )


def _directive_surface(user_text: str) -> tuple[str, bool]:
    raw = str(user_text or "").strip()
    if not raw:
        return "", False
    if raw.startswith("{"):
        try:
            payload, end = json.JSONDecoder().raw_decode(raw)
        except (TypeError, ValueError):
            payload, end = None, 0
        suffix = raw[end:].strip() if end else ""
        if isinstance(payload, dict):
            if suffix and _PAYLOAD_AUTHORITY_RE.search(suffix):
                return " ".join([*_flatten_payload_strings(payload), suffix]), True
            # A JSON-looking user message is still user content. Accept only the narrow shape the
            # shipped composer and benchmark clients use, and deliberately ignore control-looking
            # siblings such as ``command``. A payload claiming to be system/assistant text never
            # gains output authority merely because it was pasted into chat.
            structured = parse_structured_user_directive(raw)
            if structured is not None:
                values = [
                    item
                    for key in _STRUCTURED_USER_DIRECTIVE_KEYS
                    for item in _flatten_payload_strings(payload.get(key))
                ]
                # Keep each structured field as a directive boundary. A whitespace-only join turns
                # ``constraints: [\"NO json\", \"NO punctuation\"]`` into prose where the second
                # prohibition is no longer at a clause boundary and therefore is intentionally not
                # recognized by the conservative regexes below.
                return ". ".join(values), True
    return _masked_quoted_text(raw), False


def _match_count(pattern: re.Pattern[str], text: str, *, maximum: int = 200) -> int | None:
    match = pattern.search(text)
    return _bounded_count(match.group("count"), maximum=maximum) if match else None


def _explicit_literal(
    raw_text: str,
    directive_text: str,
    exact_words: int | None,
    *,
    payload_authoritative: bool = False,
    original_text: str = "",
) -> str | None:
    # Delimiter-delimited payloads (quoted, colon-introduced) are extracted from the ORIGINAL
    # message surface, never from the whitespace-normalized cue surface: the payload's interior
    # bytes -- double spaces, tabs, newlines -- ARE the deliverable, and normalizing them first
    # is payload mutation the served answer can never undo. Cue-tail anchored patterns keep the
    # normalized surface, which is what makes them tolerant of arbitrary cue spacing.
    for pattern in (_QUOTED_LITERAL_RE, _COLON_LITERAL_RE, _DIRECT_EXACTLY_COLON_LITERAL_RE):
        match = pattern.search(original_text)
        if match:
            return str(match.group("literal") or "").strip()
    for pattern in (
        _QUALIFIED_LITERAL_RE,
        _NOTHING_ELSE_LITERAL_RE,
        _SLOPPY_LITERAL_RE,
    ):
        match = pattern.search(raw_text)
        if match:
            return str(match.group("literal") or "").strip()
    if payload_authoritative and re.search(
        r"\b(?:raw\s+text|pure\s+string|no\s+json|without\s+json)\b",
        directive_text,
        re.IGNORECASE,
    ):
        try:
            payload = json.loads(raw_text)
        except (TypeError, ValueError):
            payload = None
        intent = ""
        if isinstance(payload, dict):
            intent_value = payload.get("intent", payload.get("task"))
            intent = intent_value if isinstance(intent_value, str) else ""
        match = _STRUCTURED_SCALAR_LITERAL_RE.fullmatch(intent)
        if match:
            literal = str(match.group("literal") or "").strip()
            kind = str(match.group("kind") or "").casefold()
            if kind == "word" and not re.fullmatch(r"[A-Za-z][A-Za-z_-]{0,79}", literal):
                return None
            if kind in {"number", "digit"} and not literal.isdigit():
                return None
            if kind in {"digit", "character"} and len(literal) != 1:
                return None
            if _ONE_CHARACTER_RE.search(directive_text) and len(literal) != 1:
                return None
            return literal
    if exact_words != 1 or ":" not in raw_text:
        return None
    lead, candidate = raw_text.rsplit(":", 1)
    candidate = candidate.strip().rstrip(".")
    if (
        re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,79}", candidate)
        and re.search(r"\b(?:return|output|print|say|reply|respond)\b", lead, re.IGNORECASE)
        and _WORD_COUNT_RE.search(directive_text)
    ):
        return candidate
    return None


def _corrected_delimited_literal(directive_text: str) -> str | None:
    """Bind a fully supplied final comma-list, including an explicit correction.

    The parenthesized values are answer bytes only when the surrounding request makes them the
    final deliverable.  A prior output request requires correction language before the later one;
    prose, examples, open-ended lists, and any semantic instruction after the literal stay
    model-owned.
    """

    text = str(directive_text or "")
    if _NO_PUNCTUATION_RE.search(text):
        return None
    matches = list(_DELIMITED_LITERAL_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    prefix = text[: match.start()]
    tail = text[match.end() :]
    prior_output_matches = list(
        re.finditer(
            r"\b(?:output|return|print|reply|respond|give)\b",
            prefix,
            re.IGNORECASE,
        )
    )
    if prior_output_matches and not _CORRECTION_RE.search(
        prefix[prior_output_matches[-1].end() :]
    ):
        return None
    if re.search(r"\b(?:e\.g|example|for\s+example|such\s+as)\b", prefix[-96:], re.IGNORECASE):
        return None
    if not _TERMINAL_LITERAL_BINDING_RE.search(tail):
        return None
    if not _PRESENTATION_ONLY_TAIL_RE.fullmatch(tail):
        return None
    raw_items = [item.strip() for item in match.group("literal").split(",")]
    if not 2 <= len(raw_items) <= 20:
        return None
    if any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_' -]{0,39}", item)
        or item.casefold() in {"etc", "and so on", "others"}
        for item in raw_items
    ):
        return None
    return ", ".join(raw_items)


def _looks_like_request_clause(text: str) -> bool:
    clause = re.sub(
        r"^\s*(?:(?:also|and|next|plus|then)\b\s*[:,;-]?\s*)+",
        "",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    return bool(
        _REQUEST_CLAUSE_RE.match(clause)
        or _INTERROGATIVE_REQUEST_RE.search(clause)
        or _ARITHMETIC_REQUEST_RE.search(clause)
        or _WORD_COUNT_RE.search(clause)
        or _SENTENCE_COUNT_RE.search(clause)
        or _LINE_COUNT_RE.search(clause)
        or _BULLET_COUNT_RE.search(clause)
        or _COMMA_ONLY_RE.search(clause)
    )


def _has_several_request_clauses(text: str) -> bool:
    """Whether a shape directive belongs to one sibling among several requests.

    This is deliberately a local output-authority check, not task decomposition. Lettered and
    numbered requests need special handling because punctuation splitting leaves the first label
    attached to its preamble and misses interrogative siblings such as ``B) What is ...``. Quoted
    examples were already masked by ``_directive_surface`` before this function is called.
    """

    clean = str(text or "")
    # TurnIR is the runtime's structural source of truth for request boundaries.  The legacy
    # fallback below predates it and recognizes mostly knowledge/output verbs; it therefore missed
    # mixed effects such as "Send a text. Then write a 2-line poem." and promoted the poem's local
    # line count into a whole-response contract.  The UI validator then rejected the correct
    # three-line result (honest action status + two poem lines) to an empty string.
    try:
        from core.turn_ir import parse_turn_ir

        if len(parse_turn_ir(clean).clauses) > 1:
            return True
    except Exception:
        pass
    enumerators = list(_ENUMERATED_CLAUSE_RE.finditer(clean))
    if len(enumerators) >= 2:
        enumerated_clauses = [
            clean[match.end() : enumerators[index + 1].start()]
            if index + 1 < len(enumerators)
            else clean[match.end() :]
            for index, match in enumerate(enumerators)
        ]
        if sum(_looks_like_request_clause(clause) for clause in enumerated_clauses) > 1:
            return True

    clauses = [
        clause.strip()
        for clause in re.split(
            r"[.;?!\n]+|\b(?:also|next|plus|then)\b\s*[:,;-]?|"
            r",\s*(?:and\s+)?(?=(?:answer|calculate|compare|compose|compute|create|define|"
            r"describe|draft|end|explain|finish|give|gimme|identify|list|make|name|output|"
            r"print|provide|reply|respond|return|say|solve|summari[sz]e|tell|write|xplain)\b)",
            clean,
            flags=re.IGNORECASE,
        )
        if clause.strip()
    ]
    return sum(_looks_like_request_clause(clause) for clause in clauses) > 1


def parse_raw_output_contract(user_text: str) -> RawOutputContract | None:
    """Parse direct output-only instructions without activating on quoted documentation."""
    from core.structured_batch import parse_structured_batch

    structured_batch = parse_structured_batch(user_text)
    raw_text = " ".join(str(user_text or "").split())
    directive_text, payload_authoritative = _directive_surface(user_text)
    directive_text = " ".join(directive_text.split())
    if not directive_text:
        return None

    raw_text_only = bool(_RAW_TEXT_ONLY_RE.search(directive_text))
    explicit_json = bool(_EXPLICIT_JSON_RE.search(directive_text))
    explicit_markdown = bool(_EXPLICIT_MARKDOWN_RE.search(directive_text))
    # "Output ONLY raw bash" states the absence of a wrapper as plainly as "no markdown" does --
    # raw code in a ```fence is not raw. Measured live 2026-08-15 (hostile seed 4242): a turn
    # asking for "Output ONLY raw bash" was answered with a fenced block, because only the
    # literal words "no markdown" set this flag; the same request with "no markdown" appended was
    # unwrapped correctly. Guarded by `explicit_markdown` exactly like the raw-TEXT case beside
    # it, so "reply with raw markdown only" still keeps its markup.
    raw_code_only = bool(_RAW_CODE_ONLY_RE.search(directive_text))
    json_only = bool(_JSON_ONLY_RE.search(directive_text))
    # The number-only shape needs OUTPUT CONTEXT before it binds: the phrase also
    # occurs as a quantity reference ("only the number of votes matters"). Bound
    # only when an output verb precedes it, arithmetic shares the turn, or the
    # phrase sits in the tail instruction position; and never when it reads as
    # "the number of …".
    _num_match = _NUMBERS_ONLY_RE.search(directive_text)
    numbers_only = False
    if _num_match and not _NUMBERS_ONLY_OF_RE.search(directive_text):
        _after = directive_text[_num_match.end():].strip()
        _tail_shape = _after == "" or _after[:1] in {":", "-", "—"} or _after[:1] in ".!?"
        _continuation = bool(re.match(r"^[a-z]", _after))
        _verb_before = bool(
            _REQUEST_OUTPUT_VERB_RE.search(
                directive_text[max(0, _num_match.start() - 48): _num_match.start()]
            )
        )
        _arithmetic = bool(re.search(r"\d+\s*[+\-x×*/]\s*\d+", directive_text))
        numbers_only = _tail_shape or (
            (_verb_before or _arithmetic)
            and (not _continuation or _arithmetic)
            and len(_after) <= 48
        )
    no_markdown = bool(
        _NO_MARKDOWN_RE.search(directive_text)
        or (raw_text_only and not explicit_markdown)
        or (raw_code_only and not explicit_markdown)
        # "JSON only" states the absence of a wrapper as plainly as "no markdown" does --
        # the JSON payload inside a ```json fence is not what the user asked to receive.
        # Same argument and same explicit_markdown guard as the raw-text/raw-code cases above.
        or (json_only and not explicit_markdown)
        # A bare number served as a bullet list (measured live 2026-08-29: "answer
        # with just the number" came back as a numbered list that also stated the
        # date) is the same violation in a different shape.
        or (numbers_only and not explicit_markdown)
        or re.search(r"\bplain[- ]text\s+(?:bullet|list)", directive_text, re.IGNORECASE)
    )
    no_json = bool(
        _NO_JSON_RE.search(directive_text)
        or (raw_text_only and not explicit_json)
    )
    no_internal_thought = bool(_NO_INTERNAL_THOUGHT_RE.search(directive_text))
    no_title = bool(_NO_TITLE_RE.search(directive_text))
    no_trailing_punctuation = bool(_NO_TRAILING_PUNCTUATION_RE.search(directive_text))
    # Mask the scoped phrase before the blanket check so "No punctuation at the end" cannot double
    # as a blanket ban via its own prefix.
    no_punctuation = bool(
        _NO_PUNCTUATION_RE.search(_NO_TRAILING_PUNCTUATION_RE.sub(" ", directive_text))
    )
    if no_punctuation and _PUNCTUATION_BEARING_FORMAT_RE.search(directive_text):
        no_punctuation = False
        no_trailing_punctuation = True
    exact_words = _match_count(_WORD_COUNT_RE, directive_text)
    exact_sentences = _match_count(_SENTENCE_COUNT_RE, directive_text, maximum=20)
    exact_lines = _match_count(_LINE_COUNT_RE, directive_text, maximum=20)
    if payload_authoritative and exact_lines is None:
        try:
            structured_payload = json.loads(str(user_text or "").strip())
        except (TypeError, ValueError):
            structured_payload = None
        if isinstance(structured_payload, dict) and str(
            structured_payload.get("format") or ""
        ).strip().casefold() == "haiku":
            exact_lines = 3
    bullet_count = _match_count(_BULLET_COUNT_RE, directive_text, maximum=20)
    marker_match = _BULLET_MARKER_RE.search(directive_text)
    bullet_marker = marker_match.group("marker") if marker_match else None
    if bullet_marker and bullet_count is None:
        bullet_marker = None
    delimiter = "," if _COMMA_ONLY_RE.search(directive_text) else None
    exact_text = _explicit_literal(
        raw_text,
        directive_text,
        exact_words,
        payload_authoritative=payload_authoritative,
        original_text=str(user_text or "").strip(),
    )
    if exact_text is None:
        exact_text = _corrected_delimited_literal(directive_text)
    if exact_text is not None:
        exact_words = len(_WORD_RE.findall(exact_text)) or None
    if structured_batch is not None:
        # "single words" governs every answer row, not the combined response. Keeping it in the
        # whole-output word count is what collapsed a three-answer batch into one word.
        exact_words = None
    global_output_binding = bool(
        payload_authoritative
        or raw_text_only
        or _NOTHING_EXCEPT_RE.search(directive_text)
        or _NOTHING_ELSE_RE.search(directive_text)
        or _FINAL_ONLY_RE.search(directive_text)
        or _ONLY_OUTPUT_RE.search(directive_text)
        or _NO_EXTRA_RE.search(directive_text)
    )
    if not global_output_binding and _has_several_request_clauses(directive_text):
        # A shape attached to one request is not authority over its siblings. For example,
        # "Explain X. Calculate Y. Give a 7-word title." constrains only the title; treating it as
        # a seven-word final-response contract deletes the other two answers at the UI boundary.
        exact_words = None
        exact_sentences = None
        exact_lines = None
        bullet_count = None
        bullet_marker = None
        delimiter = None
    shape_directive = bool(
        payload_authoritative
        or _REQUEST_OUTPUT_VERB_RE.search(directive_text)
        or _SHAPE_ONLY_RE.search(directive_text)
        or (delimiter is not None and re.search(r"\bonly\b", directive_text, re.IGNORECASE))
    )

    raw_only = bool(
        raw_text_only
        or _NOTHING_EXCEPT_RE.search(directive_text)
        or _NOTHING_ELSE_RE.search(directive_text)
        or _FINAL_ONLY_RE.search(directive_text)
        or _ONLY_OUTPUT_RE.search(directive_text)
        or _NO_EXTRA_RE.search(directive_text)
        or json_only
        or numbers_only
        or no_markdown
        or no_internal_thought
        or no_json
        or no_title
        or no_punctuation
        or no_trailing_punctuation
        or exact_text is not None
        or (
            shape_directive
            and any(
                value is not None
                for value in (
                    exact_words,
                    exact_sentences,
                    exact_lines,
                    bullet_count,
                    delimiter,
                )
            )
        )
        or payload_authoritative
        or structured_batch is not None
    )
    if not raw_only:
        return None
    code_deliverable = bool(
        _RAW_CODE_ONLY_RE.search(directive_text)
        or _RAW_CODE_ONLY_RE.search(raw_text)
        or _CODE_DELIVERABLE_REQUEST_RE.search(raw_text)
    )
    return RawOutputContract(
        no_markdown=no_markdown,
        no_internal_thought=no_internal_thought,
        no_json=no_json,
        no_punctuation=no_punctuation,
        no_trailing_punctuation=no_trailing_punctuation,
        no_title=no_title,
        exact_text=exact_text,
        exact_words=exact_words,
        exact_sentences=exact_sentences,
        exact_lines=exact_lines,
        bullet_count=bullet_count,
        bullet_marker=bullet_marker,
        delimiter=delimiter,
        structured_labels=structured_batch.labels if structured_batch is not None else (),
        per_item_exact_words=(
            structured_batch.per_item_exact_words if structured_batch is not None else None
        ),
        row_allowed_values=(
            structured_batch.allowed_values if structured_batch is not None else ()
        ),
        code_deliverable=code_deliverable,
    )


def raw_output_contract_from_metadata(metadata: dict[str, Any] | None) -> RawOutputContract | None:
    payload = (metadata or {}).get("raw_output_contract")
    if not isinstance(payload, dict) or not bool(payload.get("raw_only")):
        return None
    exact_text_value = payload.get("exact_text")
    exact_text = str(exact_text_value).strip() if isinstance(exact_text_value, str) else None
    if exact_text and (len(exact_text) > 200 or "\n" in exact_text or "\r" in exact_text):
        exact_text = None
    marker_value = payload.get("bullet_marker")
    bullet_marker = str(marker_value) if isinstance(marker_value, str) else None
    delimiter_value = payload.get("delimiter")
    delimiter = str(delimiter_value) if isinstance(delimiter_value, str) else None
    labels_value = payload.get("structured_labels")
    structured_labels = tuple(
        str(value)
        for value in labels_value
        if isinstance(value, (str, int)) and re.fullmatch(r"[A-Za-z]|\d{1,3}", str(value))
    ) if isinstance(labels_value, (list, tuple)) else ()
    if not 2 <= len(structured_labels) <= 20 or len(
        {label.casefold() for label in structured_labels}
    ) != len(structured_labels):
        structured_labels = ()
    allowed_value = payload.get("row_allowed_values")
    row_allowed_values = tuple(
        str(value).casefold()
        for value in allowed_value
        if isinstance(value, str) and 1 <= len(value) <= 32 and "\n" not in value
    ) if isinstance(allowed_value, (list, tuple)) else ()
    return RawOutputContract(
        no_markdown=bool(payload.get("no_markdown")),
        no_internal_thought=bool(payload.get("no_internal_thought")),
        no_json=bool(payload.get("no_json")),
        no_punctuation=bool(payload.get("no_punctuation")),
        no_trailing_punctuation=bool(payload.get("no_trailing_punctuation")),
        no_title=bool(payload.get("no_title")),
        exact_text=exact_text,
        exact_words=_bounded_count(payload.get("exact_words")),
        exact_sentences=_bounded_count(payload.get("exact_sentences"), maximum=20),
        exact_lines=_bounded_count(payload.get("exact_lines"), maximum=20),
        bullet_count=_bounded_count(payload.get("bullet_count"), maximum=20),
        bullet_marker=bullet_marker,
        delimiter=delimiter,
        structured_labels=structured_labels,
        per_item_exact_words=(
            _bounded_count(payload.get("per_item_exact_words"), maximum=20)
            if structured_labels
            else None
        ),
        row_allowed_values=row_allowed_values if structured_labels else (),
        code_deliverable=bool(payload.get("code_deliverable")),
    )


def raw_output_contract_guidance(contract: RawOutputContract) -> str:
    requirements = [
        "Return only the requested deliverable",
        "with no answer label, explanation, analysis, reasoning, scratchpad, checklist, or follow-up",
    ]
    if contract.exact_text is not None:
        requirements.append(f"verbatim as {json.dumps(contract.exact_text, ensure_ascii=False)}")
    elif contract.exact_words is not None:
        requirements.append(f"using exactly {contract.exact_words} word(s)")
    if contract.exact_sentences is not None:
        requirements.append(f"using exactly {contract.exact_sentences} sentence(s)")
    if contract.exact_lines is not None:
        requirements.append(f"using exactly {contract.exact_lines} physical lines")
    if contract.bullet_count is not None:
        marker = contract.bullet_marker or "a consistent plain-text marker"
        requirements.append(f"as exactly {contract.bullet_count} bullets prefixed with {marker!r}")
    if contract.delimiter == ",":
        requirements.append("as one comma-separated line with no wrapper")
    if contract.no_markdown:
        requirements.append("without Markdown")
    if contract.no_json:
        requirements.append("without a JSON wrapper")
    if contract.no_punctuation:
        requirements.append("without punctuation")
    if contract.no_trailing_punctuation:
        requirements.append("without punctuation at the very end")
    if contract.no_title:
        requirements.append("without a title")
    if contract.structured_labels:
        from core.structured_batch import StructuredBatchContract, structured_batch_guidance

        requirements.append(
            structured_batch_guidance(
                StructuredBatchContract(
                    labels=contract.structured_labels,
                    items=(),
                    per_item_exact_words=contract.per_item_exact_words,
                    allowed_values=contract.row_allowed_values,
                )
            )
        )
    return "The current turn has an explicit final-output contract. " + "; ".join(requirements) + "."


def raw_output_retry_instruction(contract: RawOutputContract) -> str:
    """A bounded repair prompt for a draft that failed the final-output contract."""
    return (
        "Rewrite the answer to the original user request. Do not repeat these instructions or "
        "describe the format. "
        + raw_output_contract_guidance(contract)
    )


def _unwrap_json_deliverable(text: str) -> tuple[str, bool]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text, False
    if isinstance(payload, str):
        return payload.strip(), True
    if isinstance(payload, list) and payload and all(isinstance(item, str) for item in payload):
        return "\n".join(item.strip() for item in payload).strip(), True
    if isinstance(payload, dict) and len(payload) == 1:
        key, value = next(iter(payload.items()))
        if str(key).strip().lower() in _JSON_DELIVERABLE_KEYS and isinstance(value, str):
            return value.strip(), True
    return "", True


def _scratchpad_block(block: str) -> tuple[bool, int]:
    lines = [line for line in str(block or "").splitlines() if line.strip()]
    if not lines:
        return False, 0
    headed = bool(_SCRATCHPAD_HEADING_RE.match(lines[0]))
    content = lines[1:] if headed else lines
    if not content:
        return headed, 0
    matches = all(
        _SCRATCHPAD_LINE_RE.match(line) or _INTERNAL_NARRATION_LINE_RE.match(line)
        for line in content
    )
    return bool(matches and (headed or len(content) >= 2)), len(content)


def _strip_trailing_scratchpad(text: str) -> tuple[str, bool]:
    blocks = re.split(r"\n\s*\n", str(text or "").strip())
    if len(blocks) < 2:
        lines = str(text or "").splitlines()
        for index, line in enumerate(lines[1:], start=1):
            if _SCRATCHPAD_HEADING_RE.match(line):
                return "\n".join(lines[:index]).strip(), True
        return str(text or "").strip(), False
    cut = len(blocks)
    matched_lines = 0
    while cut > 1:
        matched, count = _scratchpad_block(blocks[cut - 1])
        if not matched:
            break
        matched_lines += count
        cut -= 1
    if cut == len(blocks) or matched_lines < 1:
        return str(text or "").strip(), False
    return "\n\n".join(blocks[:cut]).strip(), True


def _normalize_bullets(text: str, contract: RawOutputContract) -> tuple[str, bool]:
    if contract.bullet_count is None or not contract.bullet_marker:
        return text, False
    lines = str(text or "").splitlines()
    if len(lines) != contract.bullet_count or any(not line.strip() for line in lines):
        return text, False
    bodies: list[str] = []
    changed = False
    for line in lines:
        match = _ANY_LIST_MARKER_RE.match(line)
        if match:
            bodies.append(match.group("body").strip())
            changed = changed or match.group("marker") != contract.bullet_marker or not line.startswith(
                f"{contract.bullet_marker} "
            )
        else:
            bodies.append(line.strip())
            changed = True
    if not changed:
        return text, False
    return "\n".join(f"{contract.bullet_marker} {body}" for body in bodies), True


def _normalize_delimiter(text: str, contract: RawOutputContract) -> tuple[str, bool]:
    if contract.delimiter != ",":
        return text, False
    clean = str(text or "").strip()
    if "\n" in clean:
        parts = [
            _ANY_LIST_MARKER_RE.sub(r"\g<body>", line).strip()
            for line in clean.splitlines()
            if line.strip()
        ]
    elif ";" in clean and "," not in clean:
        parts = [part.strip() for part in clean.split(";") if part.strip()]
    else:
        return clean, False
    if len(parts) < 2 or any(not part for part in parts):
        return clean, False
    return ", ".join(parts), True


def _contains_markdown(text: str, *, allowed_bullet_marker: str | None) -> bool:
    clean = str(text or "")
    if "```" in clean or re.search(r"^\s{0,3}#{1,6}\s+\S", clean, re.MULTILINE):
        return True
    for line in clean.splitlines():
        marker = _ANY_LIST_MARKER_RE.match(line)
        if marker and marker.group("marker") != allowed_bullet_marker:
            return True
    return bool(
        re.search(r"(?:\*\*|__)(?=\S).+?(?:\*\*|__)", clean)
        or re.search(r"\[[^\]\n]+\]\([^\)\n]+\)", clean)
    )


def _sentence_count(text: str) -> int:
    return sum(1 for match in _SENTENCE_RE.finditer(str(text or "")) if match.group(0).strip())


def _contract_violations(text: str, contract: RawOutputContract) -> tuple[str, ...]:
    clean = str(text or "").strip()
    violations: list[str] = []
    if contract.delimiter is not None and contract.delimiter not in _SUPPORTED_DELIMITERS:
        violations.append("unsupported_delimiter")
    if contract.bullet_marker is not None and contract.bullet_marker not in _SUPPORTED_BULLET_MARKERS:
        violations.append("unsupported_bullet_marker")
    if contract.exact_text is not None and clean != contract.exact_text:
        violations.append("exact_text")
    if contract.exact_words is not None and len(_WORD_RE.findall(clean)) != contract.exact_words:
        violations.append("exact_words")
    if contract.exact_sentences is not None and _sentence_count(clean) != contract.exact_sentences:
        violations.append("exact_sentences")
    if contract.exact_lines is not None and len(clean.splitlines()) != contract.exact_lines:
        violations.append("exact_lines")
    if contract.bullet_count is not None:
        lines = clean.splitlines()
        if len(lines) != contract.bullet_count:
            violations.append("bullet_count")
        elif contract.bullet_marker and any(
            not line.startswith(f"{contract.bullet_marker} ") for line in lines
        ):
            violations.append("bullet_marker")
    if contract.delimiter == ",":
        values = [value.strip() for value in clean.split(",")]
        if "\n" in clean or len(values) < 2 or any(not value for value in values):
            violations.append("delimiter_only")
    if contract.no_punctuation and re.search(r"[^\w\s]", clean, re.UNICODE):
        violations.append("punctuation")
    if contract.no_trailing_punctuation and re.search(r"[.,;:!?]\s*$", clean):
        violations.append("trailing_punctuation")
    if contract.no_markdown and _contains_markdown(
        clean,
        allowed_bullet_marker=contract.bullet_marker,
    ):
        violations.append("markdown")
    if contract.no_json and clean and clean[:1] in "[{" and clean[-1:] in "]}":
        violations.append("json")
    scratchpad, _ = _scratchpad_block(clean)
    if contract.no_internal_thought and scratchpad:
        violations.append("internal_scaffold")
    if not clean and not violations:
        violations.append("empty_output")
    return tuple(violations)


def _strip_trailing_action_echo(text: str) -> tuple[str, bool]:
    """Remove trailing runtime action-dict echo lines, keeping the deliverable above them.

    Line-anchored and tail-only on purpose: a dict the user pasted mid-message is content, and a
    reply that is NOTHING but the dict is a tool invocation -- a different guard's verdict -- so
    stripping is skipped when nothing else would remain.
    """
    lines = str(text or "").splitlines()
    kept = list(lines)
    removed = False
    while kept and _ACTION_DICT_ECHO_LINE_RE.match(kept[-1]):
        kept.pop()
        removed = True
        while kept and not kept[-1].strip():
            kept.pop()
    remainder = "\n".join(kept).strip()
    if not removed or not remainder:
        return str(text or ""), False
    return remainder, True


def _repair_from_embedded_fences(
    original: str,
    clean: str,
    contract: RawOutputContract,
    *,
    prior_actions: tuple[str, ...],
) -> RawOutputApplication | None:
    """Recover the deliverable from a reply whose fenced block sits inside surrounding prose.

    Two candidates, both made only of text the model already wrote: the fenced bodies alone (the
    deliverable, prose dropped), and the reply with just the fence-marker lines removed (all prose
    kept). A code contract prefers the bodies; anything else prefers keeping every word. The first
    fully compliant candidate wins; failing that, the first candidate that still yields a
    non-empty shape-only-repaired text. None means nothing recoverable -- the caller's normal
    erase path stands.
    """
    bodies = [match.group("body").strip() for match in _FENCE_BLOCK_RE.finditer(clean)]
    bodies = [body for body in bodies if body]
    if not bodies:
        return None
    body_text = "\n".join(bodies).strip()
    defenced = "\n".join(
        line for line in clean.splitlines() if not line.strip().startswith("```")
    ).strip()
    candidates = [
        (body_text, "fenced_deliverable_extracted"),
        (defenced, "fence_markers_removed"),
    ]
    if not contract.code_deliverable:
        candidates.reverse()
    fallback: tuple[RawOutputApplication, str] | None = None
    chosen: tuple[RawOutputApplication, str] | None = None
    for candidate, action in candidates:
        if not candidate or "```" in candidate:
            continue
        applied = apply_raw_output_contract(candidate, contract)
        if applied.compliant and applied.text:
            chosen = (applied, action)
            break
        if fallback is None and applied.text:
            fallback = (applied, action)
    if chosen is None:
        chosen = fallback
    if chosen is None:
        return None
    applied, action = chosen
    return RawOutputApplication(
        text=applied.text,
        changed=applied.text != original,
        rejected=False,
        compliant=applied.compliant,
        violations=applied.violations,
        actions=(*prior_actions, action, *applied.actions),
        repaired_text=applied.repaired_text or applied.text,
    )


def apply_raw_output_contract(
    text: str,
    contract: RawOutputContract,
) -> RawOutputApplication:
    """Sanitize and validate exactly the text eligible to become the visible response."""
    original = str(text or "").strip()
    clean = original
    actions: list[str] = []

    if contract.no_markdown:
        fence = _WHOLE_FENCE_RE.match(clean)
        if fence:
            clean = fence.group("body").strip()
            actions.append("markdown_fence_removed")

    # A requested raw digit such as ``7`` is also valid JSON syntax, but it is not a JSON wrapper.
    # Once the typed contract binds that exact literal, accepting it as the requested text is the
    # only faithful reading. Quoted strings and real envelopes still pass through the JSON
    # unwrapping branch below.
    exact_raw_literal = contract.exact_text is not None and clean == contract.exact_text
    if contract.no_json and clean and not exact_raw_literal:
        clean, parsed = _unwrap_json_deliverable(clean)
        if parsed:
            actions.append("json_envelope_removed" if clean else "json_payload_rejected")

    if contract.exact_text is not None:
        if clean != contract.exact_text:
            clean = contract.exact_text
            actions.append("exact_literal_bound")
    else:
        stripped = _LEADING_ANSWER_LABEL_RE.sub("", clean, count=1)
        stripped = _LEADING_NAMED_HEADING_RE.sub("", stripped, count=1)
        if stripped != clean:
            clean = stripped.strip()
            actions.append("answer_label_removed")

        clean, scratchpad_removed = _strip_trailing_scratchpad(clean)
        if scratchpad_removed:
            actions.append("trailing_scratchpad_removed")

        # A runtime-internal action-dict echo (`{action: write, content: ...}`) trailing the
        # deliverable is scaffolding of the same class as a scratchpad: the user never asked for
        # it, and the measured leak rendered it as answer text because the strict-JSON tool-span
        # stripper cannot parse the unquoted form. Removed only from the tail, only when a real
        # deliverable remains.
        clean, echo_removed = _strip_trailing_action_echo(clean)
        if echo_removed:
            actions.append("action_dict_echo_removed")

        if contract.no_punctuation and clean:
            punctuation_stripped = re.sub(r"^[^\w]+|[^\w]+$", "", clean, flags=re.UNICODE)
            if punctuation_stripped != clean:
                clean = punctuation_stripped.strip()
                actions.append("boundary_punctuation_removed")

        if contract.no_trailing_punctuation and clean:
            # Sentence punctuation only. A closing bracket/paren/quote is part of the deliverable
            # ("[4,3,10]"), never the trailing period this scope exists to drop.
            trailing_stripped = re.sub(r"[\s.,;:!?]+$", "", clean)
            if trailing_stripped != clean:
                clean = trailing_stripped.strip()
                actions.append("trailing_punctuation_removed")

        clean, bullets_normalized = _normalize_bullets(clean, contract)
        if bullets_normalized:
            actions.append("bullet_markers_normalized")

        clean, delimiter_normalized = _normalize_delimiter(clean, contract)
        if delimiter_normalized:
            actions.append("delimiter_normalized")

    structured_violations: tuple[str, ...] = ()
    if contract.structured_labels:
        from core.structured_batch import (
            StructuredBatchContract,
            apply_structured_batch_contract,
        )

        structured = apply_structured_batch_contract(
            clean,
            StructuredBatchContract(
                labels=contract.structured_labels,
                items=(),
                per_item_exact_words=contract.per_item_exact_words,
                allowed_values=contract.row_allowed_values,
            ),
        )
        if structured.changed and structured.compliant:
            actions.append("structured_labels_normalized")
        clean = structured.text
        structured_violations = tuple(
            f"structured_{violation}" for violation in structured.violations
        )
    violations = (*_contract_violations(clean, contract), *structured_violations)

    # An embedded fence means the deliverable is INSIDE the markdown the contract forbids:
    # "Here is the function:\n```js\n...\n```" holds a complete, compliant answer that erasure
    # would destroy. Extraction never invents a word -- it selects text the model already wrote.
    if violations and contract.no_markdown and "```" in clean:
        recovered = _repair_from_embedded_fences(
            original,
            clean,
            contract,
            prior_actions=tuple(actions),
        )
        if recovered is not None:
            return recovered

    # REPAIR, never erase. A wrong count on a real deliverable ("3 lines instead of 2") is a
    # shape miss, and the repaired draft is the closest compliant text this seam can produce
    # without inventing content -- shipping it beats shipping nothing (measured: a 1,136-token
    # turn rendered as an empty answer). Content-class violations (markdown that cannot be
    # unwrapped, a scaffold/checklist, a JSON payload, a bound literal mismatch) still erase,
    # because there the text is not the deliverable at all; `repaired_text` keeps what existed so
    # the final UI seam can report the situation instead of a silent empty string.
    if (
        violations
        and clean
        and contract.code_deliverable
        and all(violation in _SHAPE_ONLY_VIOLATIONS for violation in violations)
        and not _CODE_SIGNAL_RE.search(clean)
    ):
        # The turn asked for code and this draft holds none: prose at the right-ish line count is
        # not a salvageable code deliverable.
        violations = (*violations, "code_deliverable_missing")
    shape_only_salvage = bool(
        clean
        and violations
        and all(violation in _SHAPE_ONLY_VIOLATIONS for violation in violations)
    )
    final_text = clean if (not violations or shape_only_salvage) else ""
    return RawOutputApplication(
        text=final_text,
        changed=final_text != original,
        rejected=bool(violations and original and not final_text),
        compliant=not violations,
        violations=violations,
        actions=tuple(actions),
        repaired_text=clean,
    )


__all__ = [
    "RawOutputApplication",
    "RawOutputContract",
    "StructuredUserDirective",
    "apply_raw_output_contract",
    "parse_raw_output_contract",
    "parse_structured_user_directive",
    "raw_output_contract_from_metadata",
    "raw_output_contract_guidance",
    "raw_output_retry_instruction",
]
