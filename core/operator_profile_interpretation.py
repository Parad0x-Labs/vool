"""Typed interpretation of what a turn says about the operator's profile.

This is the deterministic half of the interpretation authority for the Operator Profile. It reads
one user turn and emits **structured proposals** -- ``ProfileProposal(category, value, strength,
scope, action)`` -- that the ONE profile authority (:mod:`core.operator_profile`) then applies under
the memory law. It never persists, never answers, never grants anything.

It is a clause grammar over normalized tokens, not an ordered list of phrase regexes that each
fire a canned reply: every clause is normalized once, its persistence directive, scope hint and
category frame are read as separate typed signals, and the SAME three signals decide the strength
of every category. The model lane reaches the same authority through the registered
``profile.remember`` / ``profile.forget`` tool contracts (structured output), which
:func:`proposals_from_tool_arguments` turns into the same proposals -- there is exactly one
apply path.

Strength law (each row has a test):

* ``explicit``  -- the clause carries a persistence directive ("remember", "save", "from now on",
  "always", "by default", "going forward") or an explicit settings verb ("set my name to").
  The authority persists it immediately and reports the exact change.
* ``strong``    -- an imperative addressed to the assistant, or a first-person declaration of a
  stable preference ("call me X", "my name is X", "be concise", "reply in Lithuanian").
  The authority makes a CANDIDATE the operator confirms.
* ``weak``      -- a passing self-reference ("I'm Alex btw"), or a style instruction attached
  to a one-off task ("short answer please, what is ..."). Chat-local only.

Identity and account facts (signature, default accounts) are only ever proposed as ``explicit``;
without the directive the clause yields nothing, by construction rather than by a later filter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.operator_profile import CATEGORIES
from core.user_preferences import _clean_requested_name, _looks_like_real_name

__all__ = [
    "ProfileProposal",
    "interpret_profile_turn",
    "proposals_from_tool_arguments",
]

STRENGTHS = ("explicit", "strong", "weak")


@dataclass(frozen=True)
class ProfileProposal:
    category: str
    value: str
    strength: str
    scope: str = "global"
    action: str = "remember"  # remember | forget | undo | show
    clause: str = ""

    @property
    def explicit(self) -> bool:
        return self.strength == "explicit"


# --------------------------------------------------------------------------- normalization

_CLAUSE_SPLIT_RE = re.compile(r"(?:[\n\r]+|(?<=[.!?;])\s+|\s+(?:and|but)\s+(?=(?:also\s+)?(?:please\s+)?(?:remember|save|call|address|refer|my|i|be|keep|reply|answer|respond|talk|speak|use|sign|treat|from|always|forget|stop|don't|do not)\b))", re.IGNORECASE)
_LEADING_FILLER_RE = re.compile(r"^(?:(?:hey|hi|hello|yo|ok|okay|oh|btw|by the way|also|and|so|well|please|pls|plz|vool|vool)[,!\s]+)+", re.IGNORECASE)
_TRAILING_FILLER_RE = re.compile(r"(?:[,\s]+(?:please|pls|plz|thanks|thank you|thx|ok|okay|from now on|going forward|in future|in the future|always|by default|for good|permanently|if you can|if possible|btw))+\s*[.!?]*$", re.IGNORECASE)
_QUOTED_RE = re.compile(r"[\"“”'‘’]([^\"“”'‘’]{1,200})[\"“”'‘’]")

_DIRECTIVE_RE = re.compile(
    r"\b(?:remember|memori[sz]e|save|store|note(?:\s+down)?|keep\s+in\s+mind|from\s+now\s+on|going\s+forward|"
    r"in\s+(?:the\s+)?future|always|by\s+default|as\s+(?:a|the)\s+default|permanently|for\s+good|"
    r"set\s+my|update\s+my|change\s+my|make\s+my\s+default|don'?t\s+forget)\b",
    re.IGNORECASE,
)
_WORK_SCOPE_RE = re.compile(r"\b(?:for|at|in|during)\s+work\b|\bwork\s+(?:chats?|mode|stuff|context|emails?)\b|\bwhen\s+(?:i'?m\s+)?working\b|\bprofessionally\b", re.IGNORECASE)
_PERSONAL_SCOPE_RE = re.compile(r"\b(?:for|in)\s+personal\b|\bpersonal\s+(?:chats?|mode|stuff|context|emails?)\b|\boutside\s+(?:of\s+)?work\b|\bpersonally\b|\bprivately\b", re.IGNORECASE)
_CHAT_SCOPE_RE = re.compile(r"\b(?:in|for)\s+this\s+(?:chat|conversation|thread|session)\b|\bjust\s+(?:for\s+)?(?:now|here|today)\b|\bfor\s+now\b|\bthis\s+time\b|\bhere\b", re.IGNORECASE)
_ATTACHED_TASK_RE = re.compile(
    r"\?|\b(?:tell me|what(?:'s| is| are)|explain|summari[sz]e|compare|describe|give me|write|create|implement|"
    r"code|build|fix|refactor|how (?:do|does|much|many)|why|list|show me|calculate|convert|translate)\b",
    re.IGNORECASE,
)

_UNDO_RE = re.compile(r"^(?:undo|revert|roll\s*back)(?:\s+(?:that|this|it|the\s+last\s+(?:change|one|thing)|my\s+last\s+change))?$", re.IGNORECASE)
_SHOW_RE = re.compile(r"^(?:what\s+do\s+you\s+(?:remember|know)\s+about\s+me|show\s+(?:me\s+)?(?:my\s+)?profile|what(?:'s| is)\s+in\s+my\s+profile|what\s+have\s+you\s+(?:saved|remembered)\s+about\s+me)$", re.IGNORECASE)

# --------------------------------------------------------------------------- category frames

_NAME_IMPERATIVE_RE = re.compile(r"^(?:you\s+(?:can|may|should)\s+)?(?:call|address|refer\s+to)\s+me\s+(?:as\s+)?(?P<v>.+)$", re.IGNORECASE)
_NAME_DECLARATION_RE = re.compile(r"^(?:my\s+name(?:'s|\s+is)|i\s+go\s+by|i\s+prefer\s+to\s+be\s+called|(?:set|change|update)\s+my\s+name\s+to)\s+(?P<v>.+)$", re.IGNORECASE)
_NAME_WEAK_RE = re.compile(r"^(?i:i'?m|i\s+am|this\s+is|it'?s)\s+(?P<v>[A-Z][A-Za-z'\-]{1,30})$")
_NAME_FORGET_RE = re.compile(r"^(?:forget|clear|drop|delete)\s+my\s+name\b|^stop\s+calling\s+me\b|^don'?t\s+(?:call|address)\s+me\s+(?:that|by\s+(?:my|any)\s+name|anything)\b", re.IGNORECASE)
_FORGET_ALL_RE = re.compile(r"^(?:forget|clear|delete|erase|wipe)\s+(?:everything|all|what)\s+(?:you\s+)?(?:know|remember|have|saved|stored)?\s*(?:about\s+me)?(?:\s+everything)?$|^forget\s+(?:my\s+)?(?:whole\s+)?profile$", re.IGNORECASE)
_FORGET_CATEGORY_RE = re.compile(r"^(?:forget|clear|drop|delete|remove)\s+my\s+(?P<c>signature|email\s+signature|language|timezone|time\s*zone|locale|default\s+(?:email|inbox|mail)\s*(?:account)?|default\s+(?:social|x|twitter)\s*(?:account)?|(?:response|answer|reply)\s+(?:style|preference|preferences)|formatting(?:\s+preference)?)\b", re.IGNORECASE)

_SIGNATURE_RE = re.compile(
    r"^(?:(?:set|update|change|save|remember|use)\s+)?(?:my\s+)?(?:e-?mail\s+)?signature\s*(?:is|should\s+be|will\s+be|reads?|to|as)?\s*[:\-–—]?\s*(?P<v>.+)$"
    r"|^sign\s+(?:my\s+|all\s+(?:my\s+)?)?(?:e-?mails?|messages?)\s+(?:with|as)\s*[:\-–—]?\s*(?P<v3>.+)$",
    re.IGNORECASE,
)
_LANGUAGES = {
    "english", "lithuanian", "russian", "german", "french", "spanish", "italian", "portuguese", "polish", "dutch",
    "swedish", "norwegian", "danish", "finnish", "estonian", "latvian", "ukrainian", "czech", "slovak", "hungarian",
    "romanian", "greek", "turkish", "arabic", "hebrew", "hindi", "chinese", "mandarin", "japanese", "korean",
}
_LANGUAGE_RE = re.compile(r"^(?:(?:please\s+)?(?:reply|answer|respond|talk|speak|write|chat)(?:\s+to\s+me)?\s+(?:only\s+)?in|my\s+(?:preferred\s+)?language\s+is|use)\s+(?P<v>[A-Za-z]{3,20})(?:\s+(?:please|only|from\s+now\s+on|going\s+forward))*$", re.IGNORECASE)
_TIMEZONE_RE = re.compile(r"^(?:my\s+time\s*zone\s+is|i'?m\s+(?:in|on)\s+(?:the\s+)?|time\s*zone\s*[:=]?\s*|set\s+my\s+time\s*zone\s+to)\s*(?P<v>[A-Za-z_]+(?:/[A-Za-z_\-]+){1,2}|utc|gmt)(?:\s+time\s*zone)?$", re.IGNORECASE)
_STYLE_WORDS = {
    "concise": "concise", "brief": "concise", "short": "concise", "terse": "concise", "succinct": "concise",
    "detailed": "detailed", "thorough": "detailed", "long": "detailed", "comprehensive": "detailed",
    "formal": "formal", "casual": "casual", "direct": "direct", "blunt": "direct", "technical": "technical",
    "plain": "plain", "simple": "plain",
}
_STYLE_IMPERATIVE_RE = re.compile(r"^(?:be|stay|keep\s+(?:it|things|answers|replies|responses|your\s+(?:answers|replies|responses)))\s+(?:more\s+|very\s+|extra\s+)?(?P<v>[a-z]+)(?:\s+(?:and\s+)?(?:to\s+the\s+point|with\s+me))?$", re.IGNORECASE)
_STYLE_PREFERENCE_RE = re.compile(r"^(?:i\s+(?:prefer|like|want)|give\s+me|i'?d\s+like)\s+(?:only\s+)?(?P<v>[a-z]+)\s+(?:answers|replies|responses|explanations)$", re.IGNORECASE)
_STYLE_NOUN_RE = re.compile(r"^(?P<v>[a-z]+)\s+(?:answers|replies|responses)(?:\s+only)?$", re.IGNORECASE)
# "answer in short telegram style", "respond in a formal tone", "answer me in short telegram
# dev style with no fluff". This grammar was lost when the free-form style lane moved from
# user_preferences.style_notes to this authority: only the be/keep imperatives survived, so the
# answer/reply/respond-in-X-style family silently produced NO proposal and the preference
# vanished (measured: 4 of the 8 shipped style commands captured nothing).
_STYLE_IN_PHRASE_RE = re.compile(
    r"^(?:answer|reply|respond|talk(?:\s+to\s+me)?|speak|write)\s+(?:me\s+)?(?:in|with|using)\s+(?P<v>.+?)\s+(?:style|tone|voice|manner|register)\b.*$",
    re.IGNORECASE,
)
_FORMAT_BULLETS_RE = re.compile(r"^(?:use|give\s+me|answer\s+(?:in|with)|reply\s+(?:in|with)|i\s+(?:prefer|like|want))\s+bullet\s*points?$|^bullet\s*points?(?:\s+(?:please|only))?$", re.IGNORECASE)
_FORMAT_NO_BULLETS_RE = re.compile(r"^(?:no|avoid|skip|don'?t\s+use|never\s+use)\s+bullet\s*points?$|^(?:use\s+)?(?:plain\s+)?prose(?:\s+only)?$", re.IGNORECASE)
_FORMAT_MARKDOWN_RE = re.compile(r"^(?P<neg>no|avoid|don'?t\s+use|never\s+use|skip)?\s*(?:use\s+)?markdown$", re.IGNORECASE)
_ACCOUNT_EMAIL_RE = re.compile(
    r"^(?:use|send\s+from|make)\s+my\s+(?P<v>[a-z0-9_.\-]+)\s+(?:inbox|e-?mail|mailbox|mail\s+account|email\s+account)(?:\s+(?:as\s+)?(?:the\s+|my\s+)?default)?$"
    r"|^(?:my\s+)?default\s+(?:e-?mail|inbox|mail|mail\s+account|email\s+account)\s+(?:is|should\s+be|=|:)\s*(?P<v2>[a-z0-9_.\-]+)(?:\s+(?:inbox|account|email))?$",
    re.IGNORECASE,
)
_ACCOUNT_SOCIAL_RE = re.compile(
    r"^(?:post|tweet)\s+from\s+my\s+(?P<v>[a-z0-9_.\-]+)\s+(?:x\s+|twitter\s+|social\s+)?account(?:\s+(?:as\s+)?(?:the\s+|my\s+)?default)?$"
    r"|^(?:my\s+)?default\s+(?:x|twitter|social)\s+account\s+(?:is|should\s+be|=|:)\s*(?P<v2>[a-z0-9_.\-]+)$",
    re.IGNORECASE,
)
_CONTEXT_RE = re.compile(r"^(?:this\s+(?:chat|conversation|thread|one)\s+is\s+(?:a\s+)?(?:for\s+)?|treat\s+this\s+(?:chat|conversation|thread)\s+as\s+(?:a\s+)?|we'?re\s+(?:at|doing|in)\s+|this\s+is\s+(?:a\s+)?(?:for\s+)?)(?P<v>work|personal)(?:\s+(?:chat|conversation|thread|stuff|mode|now))?$", re.IGNORECASE)


def _style_value(raw: Any) -> str:
    """Resolve a style descriptor to the value the profile stores.

    A phrase made only of known style words normalizes to the typed value ("a formal" ->
    "formal", "be blunt" -> "direct"); a phrase carrying its own descriptor ("short telegram",
    "short telegram dev") keeps the operator's words -- collapsing it to "concise" would
    silently discard the register they asked for. Empty/overlong/garbled input yields "".
    """
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", str(raw or "").strip().lower()).strip()
    cleaned = re.sub(r"\s+(?:more|very|extra)$", "", cleaned).strip()
    if not cleaned:
        return ""
    tokens = [token for token in re.split(r"[\s,]+", cleaned) if token]
    if not tokens:
        return ""
    if len(tokens) == 1:
        # Single word: only a known style word is a style. "be happy" is a mood, not a
        # response-style preference -- same conservatism the imperative frame always had.
        return _STYLE_WORDS.get(tokens[0], "")
    if all(token in _STYLE_WORDS for token in tokens):
        return _STYLE_WORDS[tokens[0]]
    return cleaned[:200]


def _clauses(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for chunk in _CLAUSE_SPLIT_RE.split(raw):
        clause = " ".join(str(chunk or "").split()).strip()
        if clause:
            out.append(clause)
    return out


def _normalize_clause(clause: str) -> str:
    text = _LEADING_FILLER_RE.sub("", clause.strip())
    text = _TRAILING_FILLER_RE.sub("", text)
    return text.strip(" ,.!;:")


def _scope_hint(clause: str) -> str:
    if _WORK_SCOPE_RE.search(clause):
        return "work"
    if _PERSONAL_SCOPE_RE.search(clause):
        return "personal"
    if _CHAT_SCOPE_RE.search(clause):
        return "chat"
    return "global"


def _strip_scope_words(clause: str) -> str:
    text = _WORK_SCOPE_RE.sub("", clause)
    text = _PERSONAL_SCOPE_RE.sub("", text)
    text = _CHAT_SCOPE_RE.sub("", text)
    return " ".join(text.split()).strip(" ,.!;:")


def _strip_directive(clause: str) -> str:
    text = re.sub(r"^(?:please\s+)?(?:remember|memori[sz]e|save|store|note(?:\s+down)?|keep\s+in\s+mind|don'?t\s+forget)(?:\s+(?:that|this|to|,))?\s+", "", clause, flags=re.IGNORECASE)
    text = re.sub(r"^(?:from\s+now\s+on|going\s+forward|in\s+(?:the\s+)?future|always|by\s+default|permanently|for\s+good)[,\s]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[,\s]+(?:from\s+now\s+on|going\s+forward|in\s+(?:the\s+)?future|always|by\s+default|permanently|for\s+good)\s*$", "", text, flags=re.IGNORECASE)
    return text.strip(" ,.!;:")


def _clean_value(value: str) -> str:
    text = str(value or "").strip().strip("\"'“”‘’")
    text = _TRAILING_FILLER_RE.sub("", text)
    return text.strip(" ,.!;:\"'")


def _name_value(value: str) -> str:
    cleaned = _clean_requested_name(_clean_value(value))
    if not cleaned or not _looks_like_real_name(cleaned) or len(cleaned) > 60:
        return ""
    lowered = cleaned.lower()
    if lowered in {"tired", "busy", "here", "back", "done", "sure", "fine", "not", "sorry", "good", "ok", "okay", "ready", "home", "late", "lost", "confused", "bored", "hungry"}:
        return ""
    if lowered.startswith(("a ", "an ", "the ", "your ", "that ", "this ", "it ", "not ")):
        return ""
    if any(tok in lowered.split() for tok in ("later", "back", "when", "if", "after", "before", "tomorrow", "again")):
        return ""
    return cleaned


def _frame(clause: str, normalized: str, *, explicit: bool, attached_task: bool) -> ProfileProposal | None:
    """One clause -> at most one proposal. Order is by specificity, not by priority between
    competing readings: each frame requires its own head phrase, so at most one can match."""
    ctx = _CONTEXT_RE.match(_strip_directive(normalized) if explicit else normalized)
    if ctx:
        return ProfileProposal("context_mode", ctx.group("v").lower(), "explicit" if explicit else "strong", scope="chat", clause=clause)
    body = _strip_scope_words(_strip_directive(normalized)) if explicit else _strip_scope_words(normalized)
    scope = _scope_hint(normalized)
    strength = "explicit" if explicit else "strong"
    if scope == "chat" and not explicit:
        strength = "weak"

    if _UNDO_RE.match(body):
        return ProfileProposal("*", "", "explicit", action="undo", clause=clause)
    if _SHOW_RE.match(body):
        return ProfileProposal("*", "", "explicit", action="show", clause=clause)
    if _FORGET_ALL_RE.match(body):
        return ProfileProposal("*", "", "explicit", action="forget", clause=clause)
    if _NAME_FORGET_RE.match(body):
        return ProfileProposal("preferred_name", "", "explicit", action="forget", clause=clause)
    forget_cat = _FORGET_CATEGORY_RE.match(body)
    if forget_cat:
        label = " ".join(forget_cat.group("c").lower().split())
        category = {
            "signature": "email_signature", "email signature": "email_signature", "language": "language",
            "timezone": "timezone", "time zone": "timezone", "locale": "locale",
        }.get(label)
        if category is None:
            if label.startswith("default") and ("social" in label or label.endswith(("x", "twitter", "x account", "twitter account"))):
                category = "default_account.social"
            elif label.startswith("default"):
                category = "default_account.email"
            elif "style" in label or "preference" in label:
                category = "response_style"
            elif label.startswith("formatting"):
                category = "format_preference"
        if category:
            return ProfileProposal(category, "", "explicit", action="forget", clause=clause)

    # Identity / account facts: explicit only, by construction.
    sig = _SIGNATURE_RE.match(body)
    if sig:
        value = _clean_value(sig.group("v") or sig.group("v3") or "")
        # An identity fact the operator STATES ("my signature is: ...") is explicit -- nothing was
        # inferred. A signature buried in a task ("write an email ... my signature is X") is not
        # a profile statement and yields nothing.
        if value and not attached_task and not value.lower().startswith(("a ", "an ", "the ", "what", "how", "not ")):
            return ProfileProposal("email_signature", value, "explicit", scope=scope if scope != "chat" else "global", clause=clause)
        return None
    for regex, category in ((_ACCOUNT_EMAIL_RE, "default_account.email"), (_ACCOUNT_SOCIAL_RE, "default_account.social")):
        acc = regex.match(body)
        if acc:
            value = _clean_value(acc.group("v") or acc.group("v2") or "")
            is_default = explicit or bool(re.search(r"\bdefault\b", clause, re.IGNORECASE))
            if value and is_default and not attached_task:
                return ProfileProposal(category, value, "explicit", scope=scope if scope != "chat" else "global", clause=clause)
            return None

    name = _NAME_IMPERATIVE_RE.match(body) or _NAME_DECLARATION_RE.match(body)
    if name:
        value = _name_value(name.group("v"))
        if not value:
            return None
        if _NAME_DECLARATION_RE.match(body) and re.match(r"^(?:set|change|update)\s+my\s+name", body, re.IGNORECASE):
            strength = "explicit"
        return ProfileProposal("preferred_name", value, strength, scope=scope, clause=clause)
    weak_name = _NAME_WEAK_RE.match(normalized)
    if weak_name and not explicit:
        value = _name_value(weak_name.group("v"))
        if value:
            return ProfileProposal("preferred_name", value, "weak", scope="chat", clause=clause)

    lang = _LANGUAGE_RE.match(body)
    if lang and lang.group("v").lower() in _LANGUAGES:
        return ProfileProposal("language", lang.group("v").capitalize(), "weak" if attached_task and not explicit else strength, scope=scope, clause=clause)
    tz = _TIMEZONE_RE.match(body)
    if tz:
        return ProfileProposal("timezone", tz.group("v") if "/" in tz.group("v") else tz.group("v").upper(), "weak" if attached_task and not explicit else strength, scope=scope, clause=clause)

    style = (
        _STYLE_IMPERATIVE_RE.match(body)
        or _STYLE_PREFERENCE_RE.match(body)
        or _STYLE_NOUN_RE.match(body)
        or _STYLE_IN_PHRASE_RE.match(body)
    )
    if style:
        value = _style_value(style.group("v"))
        if value:
            style_strength = strength
            if attached_task and not explicit:
                style_strength = "weak"
            return ProfileProposal("response_style", value, style_strength, scope=scope if style_strength != "weak" else "chat", clause=clause)
    if _FORMAT_BULLETS_RE.match(body):
        return ProfileProposal("format_preference", "bullet points", "weak" if attached_task and not explicit else strength, scope=scope, clause=clause)
    if _FORMAT_NO_BULLETS_RE.match(body):
        return ProfileProposal("format_preference", "prose, no bullet points", "weak" if attached_task and not explicit else strength, scope=scope, clause=clause)
    md = _FORMAT_MARKDOWN_RE.match(body)
    if md:
        value = "no markdown" if md.group("neg") else "markdown"
        return ProfileProposal("format_preference", value, "weak" if attached_task and not explicit else strength, scope=scope, clause=clause)
    return None


def interpret_profile_turn(text: str) -> list[ProfileProposal]:
    """Every profile proposal one user turn carries, in clause order. Quoted spans are data the
    user is SHOWING, not a request, and are removed first; a stipulated hypothetical frame owns
    its own names and yields nothing."""
    raw = str(text or "")
    if not raw.strip():
        return []
    try:
        from core.hypothetical_frame import detect_hypothetical_frame

        if detect_hypothetical_frame(raw).supplies_premises:
            return []
    except Exception:
        pass
    without_quotes = _QUOTED_RE.sub(lambda m: m.group(0) if _looks_like_quoted_value(raw, m) else " ", raw)
    attached_task = bool(_ATTACHED_TASK_RE.search(without_quotes)) and not _DIRECTIVE_RE.search(without_quotes)
    proposals: list[ProfileProposal] = []
    seen: set[tuple[str, str, str]] = set()
    for clause in _clauses(without_quotes):
        normalized = _normalize_clause(clause)
        if not normalized:
            continue
        # Read the directive off the clause BEFORE trailing fillers are dropped: "by default" and
        # "from now on" are the directive, not filler, when they close the clause.
        explicit = bool(_DIRECTIVE_RE.search(_LEADING_FILLER_RE.sub("", clause)))
        proposal = _frame(clause, normalized, explicit=explicit, attached_task=attached_task)
        if proposal is None:
            continue
        key = (proposal.category, proposal.value.lower(), proposal.action)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(proposal)
    return proposals


def _looks_like_quoted_value(raw: str, match: re.Match) -> bool:
    """A quote that is the VALUE of a frame ("call me 'Saul'") stays; a quoted sentence the user
    is showing ("he said 'my name is Bob'") is data and goes."""
    before = raw[: match.start()].lower().rstrip()
    return before.endswith(("call me", "address me as", "refer to me as", "my name is", "signature is", "signature:", "signature to", "sign my emails with", "reply in", "answer in"))


def proposals_from_tool_arguments(intent: str, arguments: dict | None) -> list[ProfileProposal]:
    """The model lane: a ``profile.remember`` / ``profile.forget`` tool call (structured output)
    becomes the same typed proposal the deterministic lane emits, so both share one apply path."""
    args = dict(arguments or {})
    category = str(args.get("category") or "").strip()
    if intent == "profile.forget":
        if category and category != "*" and category not in CATEGORIES:
            return []
        return [ProfileProposal(category or "*", "", "explicit", action="forget", clause="tool")]
    if intent != "profile.remember" or category not in CATEGORIES:
        return []
    value = str(args.get("value") or "").strip()
    if not value:
        return []
    scope = str(args.get("scope") or "global").strip().lower()
    if scope not in {"global", "work", "personal", "project", "chat"}:
        scope = "global"
    strength = "explicit" if bool(args.get("explicit", True)) else "strong"
    return [ProfileProposal(category, value, strength, scope=scope, clause="tool")]
