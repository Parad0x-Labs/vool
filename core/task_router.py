from __future__ import annotations

import ast
import hashlib
import operator
import os
import platform
import re
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from core import policy_engine
from core.canonical_project_knowledge import has_canonical_project_entity
from core.learning import load_procedure_shards, rank_reusable_procedures
from core.learning_integration import _inert_guidance_line
from core.orchestration import TaskEnvelopeV1, build_task_envelope
from core.persistent_memory import session_memory_policy
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)
from core.task_state_machine import transition
from core.trace_id import ensure_trace
from storage.db import get_connection

_PATH_PATTERNS = [
    re.compile(r"[A-Za-z]:\\[^\s]+"),         # Windows paths
    re.compile(r"~/(?:[^\s)]+)"),             # Home-relative Unix paths
    re.compile(r"/(?:Users|home|etc|var|tmp|opt|srv|private|mnt)/[^\s)]+"),  # Sensitive absolute Unix paths
]

#: What `redact_text` leaves where a filesystem path was. Named because two separate rules now
#: read it back as "this turn named a place on this machine".
_REDACTED_PATH_TOKEN = "<path>"

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
_TOKEN_RE = re.compile(r"\b[A-Fa-f0-9]{24,}\b|\b[a-zA-Z0-9_\-]{32,}\b")

_BUSINESS_CHAT_MARKERS = (
    "business",
    "pricing",
    "position",
    "positioning",
    "go to market",
    "gtm",
    "sales",
    "marketing",
    "revenue",
    "customer acquisition",
    "brand strategy",
    "market strategy",
)
_FOOD_CHAT_MARKERS = (
    "food",
    "meal",
    "meals",
    "recipe",
    "recipes",
    "diet",
    "nutrition",
    "calories",
    "protein",
    "carbs",
    "fat loss",
    "what should i eat",
)
_RELATIONSHIP_CHAT_MARKERS = (
    "relationship",
    "relationships",
    "partner",
    "girlfriend",
    "boyfriend",
    "wife",
    "husband",
    "dating",
    "breakup",
    "intimacy",
    "sex life",
    "argument",
)
_CREATIVE_CHAT_MARKERS = (
    "brainstorm",
    "creative",
    "campaign idea",
    "campaign ideas",
    "name ideas",
    "tagline",
    "slogan",
    "story idea",
    "concept ideas",
    "creative direction",
)
_GENERAL_ADVISORY_MARKERS = (
    "should i",
    "what should i do",
    "help me decide",
    "need advice",
    "advice on",
    "how do i handle",
)
_HIVE_MARKERS = ("hive", "hive mind", "brain hive", "public hive")
_SEMANTIC_HIVE_FALLBACK_MARKERS = ("task", "tasks", "work", "queue", "open", "available", "online", "anything")
_SEMANTIC_HIVE_PATTERNS = (
    re.compile(r"\b(?:check|show|list|see)\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+in\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bwhat(?:'s| is)\s+on\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\banything\s+on\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\b"),
    re.compile(r"\bshow\s+(?:the\s+)?(?:hive|hive mind|brain hive|public hive)\s+(?:work|tasks?|queue)\b"),
    re.compile(r"\bwhat\s+(?:online\s+)?tasks?\s+(?:do\s+)?we\s+have\b"),
)
_HARD_ENTITY_LOOKUP_QUESTION_PATTERNS = (
    re.compile(r"\bwho(?:'s|\s+is)\b"),
    re.compile(r"\bis\s+(?:he|she|they)\s+(?:the\s+)?(?:owner|founder|ceo|cto|creator|co-founder)\b"),
    re.compile(r"\bwho\s+(?:is|are|was)\s+(?:behind|running|leading)\b"),
    re.compile(r"\bwhat\s+(?:is|are)\s+(?:his|her|their)\s+(?:role|position|title)\b"),
    re.compile(r"\bi\s+see\s+(?:him|her|them)\s+mentioned\b"),
    re.compile(r"\bwho\s+(?:runs?|owns?|founded|created|built|leads?)\b"),
    re.compile(r"\bfind\s+(?:me\s+)?who\b"),
)
_SOFT_ENTITY_LOOKUP_QUESTION_PATTERNS = (
    re.compile(r"\btell\s+me\s+about\b"),
    re.compile(r"\bwhat\s+do\s+you\s+know\s+about\b"),
)
_EXPLICIT_LOOKUP_MARKERS = (
    "find",
    "look up",
    "lookup",
    "search",
    "google",
    "check",
)
_WEB_SOCIAL_LOOKUP_MARKERS = (
    "on x",
    "x.com",
    "on twitter",
    "twitter",
    "on the web",
    "on web",
    "google",
    "online",
    "web",
)

_CHAT_TRUTH_SOURCE_SURFACES = {"openclaw", "api", "channel"}
_CHAT_TRUTH_SOURCE_PLATFORMS = {"openclaw", "api"}


def _contains_phrase_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    return any(re.search(rf"\b{re.escape(marker)}\b", lowered) for marker in markers)
_DIRECT_MATH_EXPRESSION_RE = re.compile(r"^[\d\s\.\+\-\*\/%\(\)]+$")
_DIRECT_MATH_QUESTION_RE = re.compile(
    r"^(?:what'?s|what\s+is|whats|calculate|compute|how\s+much\s+is)\s*(?P<expression>.+?)\s*[?]?$",
    re.IGNORECASE,
)
_SAFE_ARITHMETIC_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_WORD_MATH_MARKERS = (
    " total ",
    " sum ",
    " show the steps",
    " step by step",
    " twice ",
    " minus ",
    " plus ",
    " times ",
    " multiplied by ",
    " divided by ",
    " in total",
)
_WORD_MATH_CONTEXT_MARKERS = (
    " minute",
    " minutes",
    " hour",
    " hours",
    " task a",
    " task b",
    " task c",
    " takes ",
)
_LIVE_RECENCY_MARKERS = (
    " right now",
    " current ",
    " latest ",
    " today ",
    " just now",
    " just happened",
    " minute ago",
    " minutes ago",
    " hour ago",
    " hours ago",
)
_LIVE_FACT_DOMAIN_MARKERS = (
    " btc",
    " bitcoin",
    " eur/usd",
    " eurusd",
    # The bare currency codes the pair markers above already imply. "Explain the recent USD move
    # today." is the same kind of question as "eur/usd today" and was reaching neither the live lane
    # nor `research`: the pair markers only match when the user writes the pair.
    #
    # These carry a TRAILING space as well as a leading one, unlike the prefix-matched markers
    # around them. The entries here are whole words with common longer neighbours -- " eur" alone
    # matches "europe" and "european", " stock" matches "stockholm" -- and a currency marker that
    # fires on a place name would send "what happened in europe today" to the paid lane on the
    # strength of three letters. The text is padded at both ends, so a word at either end still
    # matches. `" dollar"` keeps prefix matching on purpose: it needs to cover "dollars".
    " usd ",
    " eur ",
    " gbp ",
    " dollar",
    " euro ",
    " euros ",
    " weather",
    " headline",
    " headlines",
    " news",
    " markets",
    " market",
    " price",
    # Windowed change-attribution admits follow-ups like "eth 24h change" to the
    # live lane the way " price" does. Bare " change" is deliberately absent:
    # "climate change ..." is not a fresh market fact, and the windowed forms
    # are what real change asks look like (measured live 2026-08-29: "24 change
    # on eth" reached no lane at all).
    "24h change",
    "24 change",
    "24hr change",
    "% change",
    "day change",
    "daily change",
    "weekly change",
    " change today",
    " change now",
    " stock ",
    " stocks ",
    " exchange rate",
)
_CURRENT_CHAT_RECALL_RE = re.compile(
    r"^\s*(?:what|which)\s+(?:is|are)\s+(?:my|our|the)\s+(?:current\s+)?"
    r"(?:preference|focus|goal|decision|choice|plan)\b"
    r"|^\s*what\s+do\s+i\s+(?:prefer|want)\s+(?:now|currently)\b",
    re.IGNORECASE,
)
_ORDINARY_CONCEPTUAL_QUESTION_RE = re.compile(
    r"^\s*(?:why|how)\b"
    r"|^\s*what\s+is\s+(?:one|a|an|some|meaning)\b"
    r"|^\s*tell\s+me\s+about\s+(?:why|how|what|whether)\b",
    re.IGNORECASE,
)
# A turn whose whole content is a pointer at the last answer: "what is that?", "what's this?".
# There is no topic in it to look up, so the lookup markers below must not claim it.
_BARE_DEICTIC_QUESTION_RE = re.compile(
    r"\s*(?:so|but|ok|okay|wait|huh)?[,\s]*"
    r"(?:what|why)(?:'s|s| is| are| was| were)?\s+"
    r"(?:that|this|it|those|these)\s*[?!.]*",
    re.IGNORECASE,
)
# The rejection half of a correction: the user saying the answer was not the one they wanted.
_CORRECTION_REJECTION_RE = re.compile(
    r"\bnot\s+what\s+(?:i|we)\s+(?:have\s+|had\s+)?(?:asked|wanted|requested|meant|said)"
    r"|\b(?:that|this|it)(?:'s|s|\s+is|\s+was)?\s+not\s+(?:what|right|correct|it|useful|helpful)"
    r"|\b(?:i|we)\s+(?:didn'?t|did\s+not|never)\s+ask"
    r"|\b(?:you|u)\s+(?:mis(?:understood|read)|got\s+(?:it|that)\s+wrong|ignored)"
    r"|\b(?:wrong|incorrect|irrelevant|nonsense|gibberish)\b"
    r"|\bnot\s+(?:what|the)\s+(?:i|answer|thing)\b",
    re.IGNORECASE,
)
# The half that says what is being rejected. Without it, "wrong" alone is just a word.
_PRIOR_TURN_REFERENCE_RE = re.compile(
    r"\b(?:that|this|it|these|those|above|you|your\s+(?:answer|response|reply|output))\b",
    re.IGNORECASE,
)
# Where the opening sentence of a turn ends. Only a hard sentence break counts -- a comma continues
# the same thought ("no, that's wrong") and must not split it.
_CORRECTION_OPENING_RE = re.compile(r"[.!?]+\s+")
# A correction that does not OPEN with its rejection is terse. Past this, the turn is carrying its
# own content and belongs to whatever lane that content implies.
_CORRECTION_MAX_WORDS = 16
# Naming any of these makes the turn a technical request that keeps its own lane, however
# unhappily it is phrased -- "why is this code wrong?" is debugging, not a correction.
_CORRECTION_TECHNICAL_EXCLUSIONS = (
    "code",
    "script",
    "function",
    "traceback",
    "exception",
    "stack trace",
    "test",
    "command",
    "config",
    "log",
    "file",
    "repo",
)
_NON_WEB_LOOKUP_EXCLUSIONS = (
    "file",
    "files",
    "folder",
    "workspace",
    "repo",
    "repository",
    "command",
    "terminal",
    "shell",
    "calendar",
    "email",
)
_PLAIN_TEXT_CHAT_TASK_CLASSES = {
    "chat_conversation",
    "chat_research",
    "general_advisory",
    "business_advisory",
    "food_nutrition",
    "relationship_advisory",
    "creative_ideation",
}
_AI_FIRST_CHAT_DOMAIN_TASK_CLASSES = {
    "unknown",
    "research",
    "chat_conversation",
    "chat_research",
    "general_advisory",
    "business_advisory",
    "food_nutrition",
    "relationship_advisory",
    "creative_ideation",
    "debugging",
    "dependency_resolution",
    "config",
    "system_design",
    "file_inspection",
    "shell_guidance",
    "workspace_audit",
}


def looks_like_semantic_hive_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if any(
        marker in lowered
        for marker in (
            "create task",
            "create topic",
            "new task",
            "new topic",
            "claim task",
            "submit result",
            "post progress",
            "status",
            "done",
            "finished",
        )
    ):
        return False
    if any(pattern.search(lowered) for pattern in _SEMANTIC_HIVE_PATTERNS):
        return True
    return bool(
        _contains_phrase_marker(lowered, _HIVE_MARKERS)
        and _contains_phrase_marker(lowered, _SEMANTIC_HIVE_FALLBACK_MARKERS)
    )


def _is_mailbox_request(text: str) -> bool:
    """A request about mail the user received is a request to their OWN mailbox, never a public
    lookup, whatever its sender phrase looks like. Decided by the one recognizer that owns the
    question (core.tool_demand_signals.mailbox_retrieval_intents), not by a word list: measured served
    2026-09-14, "Now check my personal inbox for the orchard supplier." carried none of the exclusion
    words above, read as an entity lookup, was classified research and reached no email tool."""
    try:
        from core.tool_demand_signals import mailbox_retrieval_intents

        return bool(mailbox_retrieval_intents(text))
    except Exception:
        return False


def looks_like_public_entity_lookup_request(text: str) -> bool:
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.stipulated_frame import stipulated_frame_forbids_retrieval

    if stipulated_frame_forbids_retrieval(text):
        return False
    constraints = analyze_retrieval_constraints(text)
    lowered = " ".join(constraints.eligible_text.strip().lower().split())
    if not lowered or constraints.forbids_candidate(lowered) or looks_like_semantic_hive_request(lowered):
        return False
    if any(marker in lowered for marker in _NON_WEB_LOOKUP_EXCLUSIONS) or _is_mailbox_request(lowered):
        return False
    if any(pattern.search(lowered) for pattern in _HARD_ENTITY_LOOKUP_QUESTION_PATTERNS):
        return True
    soft_entity_prompt = any(pattern.search(lowered) for pattern in _SOFT_ENTITY_LOOKUP_QUESTION_PATTERNS)
    has_lookup_marker = any(marker in lowered for marker in _EXPLICIT_LOOKUP_MARKERS)
    has_web_or_social_cue = any(marker in lowered for marker in _WEB_SOCIAL_LOOKUP_MARKERS)
    has_public_context = any(marker in lowered for marker in (
        "solana", "founder", "ceo", "cto", "person", "guy", "girl", "crypto",
        "ethereum", "bitcoin", "helius", "blockchain", "defi", "nft",
        "company", "startup", "project", "protocol", "influencer",
        "community", "developer", "engineer", "owner", "co-founder",
    ))
    if soft_entity_prompt:
        return bool(has_web_or_social_cue or has_public_context)
    return bool(has_lookup_marker and (has_web_or_social_cue or has_public_context))


def looks_like_explicit_lookup_request(text: str) -> bool:
    from core.retrieval_constraints import analyze_retrieval_constraints
    from core.stipulated_frame import stipulated_frame_forbids_retrieval

    if stipulated_frame_forbids_retrieval(text):
        return False
    from core.within_turn_retraction import turn_retracts_an_instruction

    # A bare within-turn retraction ("search the web for X. WAIT. Cancel the search.") is invisible
    # to analyze_retrieval_constraints (has_prohibition=False), so without this an explicitly
    # cancelled lookup still read as an active retrieval demand and forced current-information /
    # grounding on a turn the user had stopped. The retraction authority is precise in both
    # directions; it simply had no consumer on this seam.
    if turn_retracts_an_instruction(text):
        return False
    constraints = analyze_retrieval_constraints(text)
    lowered = " ".join(constraints.eligible_text.strip().lower().split())
    if not lowered or constraints.forbids_candidate(lowered) or looks_like_semantic_hive_request(lowered):
        return False
    if any(marker in lowered for marker in _NON_WEB_LOOKUP_EXCLUSIONS) or _is_mailbox_request(lowered):
        return False
    if looks_like_public_entity_lookup_request(lowered):
        return True
    # "browse" is matched as a WORD on purpose: a substring match also fires on
    # "browser", which sent every vool-browser turn (C06) to the live-info fast
    # path instead of the tool loop -- the tool named in the user's own words
    # became unreachable by a substring accident (measured served, 2026-09-04).
    return bool(
        re.search(r"\b(?:look\s+up|lookup|search\s+online|check\s+online|browse)\b", lowered)
        or (
            re.search(r"\b(?:fetch|read|summarize|summarise)\b", lowered)
            and re.search(r"\b(?:website|webpage|https?)\b|\b(?:[a-z0-9-]+\.)+[a-z]{2,63}\b", lowered)
            and not re.search(r"\bhow\s+(?:to|do|can|should|would)\b|\b(?:explain|teach|show)\s+(?:me\s+)?how\b", lowered)
        )
        # "find [me] [the] official <thing>" is an explicit retrieval demand for an external,
        # published spec or record. Without a web marker it fell through to DIRECT/stable_knowledge
        # (measured: "Find the official weight of the Apple Watch Ultra 2." acquired no obligation
        # to look), so the model supplied the figure from its weights and the unsourced-claim guard
        # never inspected it.
        or re.search(r"\bfind\s+(?:me\s+)?(?:the\s+)?official\b", lowered)
        or re.search(
            r"\b(?:search|check)\s+(?:the\s+)?(?:latest|newest|current|recent)\b",
            lowered,
        )
        or (
            any(marker in lowered for marker in ("find", "check", "search", "google"))
            and any(marker in lowered for marker in _WEB_SOCIAL_LOOKUP_MARKERS)
        )
    )


#: Operator words a typo may land near. Correction is attempted ONLY between two numbers.
_OPERATOR_WORDS: tuple[str, ...] = ("times", "plus", "minus", "over", "divided", "multiplied")

#: "divided BY 4" and "multiplied BY 4" put a preposition between the operator word and the
#: second number, so the window has to allow it or those two never get corrected.
_NUMBER_WORD_NUMBER_RE = re.compile(r"(?<=\d)(\s+)([a-z]{3,12})(\s+(?:by\s+)?)(?=[\d(])")


def _within_one_edit(word: str, target: str) -> bool:
    """True when `word` is at most ONE insertion, deletion, substitution or transposition from
    `target`. Written out rather than pulled from a library: the whole point is a bound tight
    enough that "list" never becomes "plus"."""

    if word == target:
        return True
    if abs(len(word) - len(target)) > 1:
        return False
    if len(word) == len(target):
        diff = [i for i, (a, b) in enumerate(zip(word, target, strict=True)) if a != b]
        if len(diff) == 1:
            return True
        # A transposition of two adjacent characters ("tiems" -> "times").
        if len(diff) == 2 and diff[1] == diff[0] + 1:
            i, j = diff
            return word[i] == target[j] and word[j] == target[i]
        return False
    shorter, longer = (word, target) if len(word) < len(target) else (target, word)
    return any(longer[:index] + longer[index + 1 :] == shorter for index in range(len(longer)))


#: An explicit signal that the turn IS a calculation: an equals sign, or a calculation verb. The
#: typo repair below runs only when one is present, because edit distance alone is far too loose
#: on short words -- measured, "3 plums 4" became 7, "5 limes 6" became 30 and "2 mines 1" became
#: 1, since "plums"/"limes"/"mines" are each ONE edit from an operator word. Rewriting prose into a
#: sum is a worse defect than the mistyped calculation this repair exists for, so the repair is
#: bought only where the user has already said, unambiguously, that they want a number.
_CALCULATION_SIGNAL_RE = re.compile(
    r"=|\b(?:calc|calculate|compute|work\s+out|figure\s+out|solve|evaluate|what\s+is|whats|what's)\b",
    re.IGNORECASE,
)


def _repair_mistyped_operator_words(expression: str, *, original: str) -> str:
    """Correct an operator word that a typo put between two numbers, and nothing else.

    Two guards, and BOTH are load-bearing: the word must sit between two numbers, and the TURN
    must carry an explicit calculation signal. Either alone is too permissive.

    The signal is read from `original` rather than `expression`, because by this point the calc
    verb and the trailing "= ?" have already been stripped off as filler -- checking the stripped
    form found no signal in any real case and silently disabled the whole repair.
    """

    if not _CALCULATION_SIGNAL_RE.search(original):
        return expression

    def _repair(match: re.Match[str]) -> str:
        word = match.group(2)
        if word in _OPERATOR_WORDS:
            return match.group(0)
        for target in _OPERATOR_WORDS:
            if _within_one_edit(word, target):
                return f"{match.group(1)}{target}{match.group(3)}"
        return match.group(0)

    return _NUMBER_WORD_NUMBER_RE.sub(_repair, expression)


def _direct_math_expression(text: str) -> str:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized or len(normalized) > 120:
        return ""
    # A leading conversational opener ("hi,", "hey", "ok", "so", "please") must not block the
    # calculator, so "hi, what is the 100 x 50" still reaches it (was falling to the slow model).
    normalized = re.sub(r"^(?:hi|hey|hello|yo|ok|okay|so|well|um|uh|please|pls|plz)[\s,]+", "", normalized).strip()
    # A leading COORDINATING CONNECTIVE is filler of the same class. Measured 2026-09-02: a
    # mixed turn split into demand units serves each unit as its own request, and the unit
    # keeps the sentence's connective — "and what is 2 plus 2?" — which blocked the
    # recognizer and sent exact arithmetic to the model lane. Strip it like any other opener.
    normalized = re.sub(r"^(?:(?:and|then|also|next)[\s,]+)+", "", normalized).strip()
    # A leading CALCULATION VERB must not block the calculator either. Measured live 2026-08-15:
    # "calc 80*81 for me" missed this recognizer, fell through to a local model, and came back
    # "648" -- a dropped digit presented as the answer. "what is 80*81" was deterministic and
    # correct the whole time. Arithmetic is the last thing that should reach a stochastic lane.
    normalized = re.sub(
        r"^(?:calc|calculate|compute|work\s+out|figure\s+out|solve|eval|evaluate)\s+", "", normalized
    ).strip()
    # A leading REQUEST opener is filler too: "just tell me 31+51 asap" is the same arithmetic as
    # "31+51". Measured live 2026-08-15 (hostile seed 31337) -- that turn missed the calculator,
    # reached a model, and came back as a truncation notice with no number in it at all.
    normalized = re.sub(
        r"^(?:just\s+)?(?:tell|give|show)\s+me\s+(?:the\s+(?:answer|result|value)\s+(?:to|for|of)\s+)?",
        "",
        normalized,
    ).strip()
    # ...and a trailing urgency word is filler at the other end.
    normalized = re.sub(r"[\s,]*\b(?:asap|now|quick(?:ly)?|fast|already)\b[\s!?.]*$", "", normalized).strip()
    question = _DIRECT_MATH_QUESTION_RE.fullmatch(normalized)
    expression = str(question.group("expression") if question else normalized).strip()
    # Trailing filler, chained: "for me thx", "please!", "= ?". The `+` matters -- a single pass
    # left "for me" behind on "calc 80*81 for me thx" and the whole expression failed to parse.
    expression = re.sub(
        r"(?:[\s,]*(?:for\s+me|please|pls|plz|thanks|thank\s+you|thx|ty|mate|man)\s*)+[?!.]*$",
        "",
        expression,
    ).strip()
    # A trailing "= ?" / "=" is how people ASK for the result ("80 times 81 = ?"), not part of the
    # expression; leaving it in makes the expression unparseable and hands plain arithmetic to a
    # model.
    expression = re.sub(r"\s*=\s*[?]?\s*$", "", expression).strip()
    # Leading article/filler before the numeric expression ("the 100 x 50" -> "100 x 50",
    # "the value of 7*8" -> "7*8"). The trailing "+" chains stacked fillers in one pass.
    expression = re.sub(r"^(?:the\s+|a\s+|an\s+|value\s+of\s+|result\s+of\s+|answer\s+to\s+)+", "", expression).strip()
    # A MISTYPED operator word still sits between two numbers, and that context is what makes
    # correcting it safe. Measured live 2026-08-15 (hostile seed 8675): "34 tiems 95 = ?" missed
    # this recognizer, reached a model, and came back 2995 -- the answer is 3230. Nobody proofreads
    # a calculation. Only a word BETWEEN TWO NUMBERS is considered, and only when it is a single
    # edit away from an operator word, so ordinary prose can never be rewritten into arithmetic.
    expression = _repair_mistyped_operator_words(expression, original=text)
    expression = re.sub(r"\bmultiplied\s+by\b", "*", expression)
    expression = re.sub(r"\bdivided\s+by\b", "/", expression)
    expression = re.sub(r"\btimes\b", "*", expression)
    expression = re.sub(r"\bplus\b", "+", expression)
    expression = re.sub(r"\bminus\b", "-", expression)
    # "x" / "×" between digits is multiplication ("100x50", "100 x 50", "100×50"). The digit
    # look-around keeps it unambiguous, so a variable-name "x" elsewhere is never touched.
    expression = re.sub(r"(?<=\d)\s*[x×]\s*(?=\d)", "*", expression)
    if not _DIRECT_MATH_EXPRESSION_RE.fullmatch(expression):
        return ""
    if not any(marker in expression for marker in ("+", "-", "*", "/", "%")):
        return ""
    if len(re.findall(r"\d+(?:\.\d+)?", expression)) < 2:
        return ""
    return expression


def looks_like_direct_math_request(text: str) -> bool:
    expression = _direct_math_expression(text)
    if not expression:
        return False
    try:
        parsed = ast.parse(expression, mode="eval")
    except Exception:
        return False
    return all(
        not isinstance(node, ast.Call | ast.Name | ast.Attribute | ast.Subscript)
        for node in ast.walk(parsed)
    )


#: Named operations that are exactly computable and that people write in words rather than
#: symbols. Measured live 2026-08-15: "the square root of 144" reached a cloud model and came back
#: "echo fourtytwo" -- wrong by thirty, and misspelled. An arithmetic question must never be
#: settled by a stochastic lane when the runtime can compute it exactly.
_SQUARE_ROOT_RE = re.compile(
    r"(?:\bsquare\s+root\s+of\s+|\bsqrt\s*\(?\s*|\u221a\s*)(?P<n>\d+(?:\.\d+)?)\s*\)?",
    re.IGNORECASE,
)
_POWER_RE = re.compile(r"\b(?P<n>\d+(?:\.\d+)?)\s+(?P<word>squared|cubed)\b", re.IGNORECASE)
_PERCENT_OF_RE = re.compile(
    r"\b(?P<pct>\d+(?:\.\d+)?)\s*(?:%|percent|per\s*cent)\s+of\s+(?P<n>\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)


def _format_number(value: float) -> str:
    """Exact integers stay integers; everything else is rounded rather than shown to 15 places."""

    if value == int(value):
        return str(int(value))
    return f"{round(value, 6):g}"


def evaluate_named_math_request(text: str) -> str | None:
    """A named arithmetic operation stated in words, computed exactly.

    Deliberately narrow: three shapes that are unambiguous and exactly computable. The guard is
    that a NUMBER must be present -- "the square root of the problem" names no operand and is left
    alone, which keeps a metaphor from being answered with a decimal.
    """

    body = " ".join(str(text or "").split())
    if not body:
        return None

    percent = _PERCENT_OF_RE.search(body)
    if percent is not None:
        pct = float(percent.group("pct"))
        base = float(percent.group("n"))
        return f"{_format_number(pct)}% of {_format_number(base)} = {_format_number(pct * base / 100)}."

    power = _POWER_RE.search(body)
    if power is not None:
        base = float(power.group("n"))
        exponent = 2 if power.group("word").lower() == "squared" else 3
        return f"{_format_number(base)}^{exponent} = {_format_number(base**exponent)}."

    root = _SQUARE_ROOT_RE.search(body)
    if root is not None:
        value = float(root.group("n"))
        if value < 0:
            return None
        return f"The square root of {_format_number(value)} is {_format_number(value**0.5)}."
    return None


#: ---- Deterministic sequence-operation lane -----------------------------------------------------
#: Measured live 2026-08-15, twice, on two different local models: "Array A starts with [1,2,3].
#: I append 4. Then I remove 2. Then I multiply the first element by 10. Then I reverse. Output
#: exactly the final array in [1,2,3] format, no spaces." came back EMPTY -- the reply "did not
#: contain the requested output format". The turn is EXACTLY computable; VOOL handed it to a
#: stochastic lane anyway. The evaluator below parses a literal starting list plus an ordered,
#: CLOSED verb set and executes it exactly. The invariant is all-or-nothing: any step outside the
#: closed set makes the WHOLE turn fall through to the model (None), because a half-executed
#: sequence presented as exact is worse than no fast path at all. Judgment steps ("sort these by
#: relevance") and open-ended list requests ("give me a list of 5 fruits") never enter this lane:
#: the former fails the closed verb grammar, the latter never states a literal starting list.

_SEQUENCE_NUMBER = r"-?\d+(?:\.\d+)?"

#: The declaration must be ABOUT a sequence: a subject word before the literal list keeps
#: "my PIN is [1,2,3,4]"-style sentences (and anything else that merely contains a bracketed
#: literal) out of the lane.
_SEQUENCE_SUBJECT_RE = re.compile(
    r"\b(?:list|array|sequence|seq|vector|stack|queue|numbers|values|elements)\b",
    re.IGNORECASE,
)
_SEQUENCE_LIST_DECLARATION_RE = re.compile(
    rf"(?:\bstarts?\s+(?:with|as|at)\b|\bbegins?\s+(?:with|as)\b|\bis\s+initially\b|\binitially\s+(?:is|contains)\b"
    rf"|\bcontains\b|\bis\b|=)\s*"
    rf"(?P<list>\[\s*{_SEQUENCE_NUMBER}(?:\s*,\s*{_SEQUENCE_NUMBER})*\s*\])",
    re.IGNORECASE,
)

#: Step separators. Commas join steps here because the only comma-bearing literal (the starting
#: list) has already been consumed before splitting.
_SEQUENCE_SPLIT_RE = re.compile(
    r"[.;!?\n]|,|\band\s+then\b|\bthen\b|\bnext\b|\bafter\s+that\b|\bfinally\b|\blastly\b|\band\b",
    re.IGNORECASE,
)
_SEQUENCE_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:i|we|you|please|now|also|ok|okay|so|just)\b[\s,]*)+",
    re.IGNORECASE,
)
#: Trailing urgency/politeness never changes an op. This repo has measured "thx"/"asap" disabling
#: a recognizer before (see _direct_math_expression); the same strip is applied per segment here.
_SEQUENCE_TRAILING_FILLER_RE = re.compile(
    r"(?:[\s,]*\b(?:asap|please|pls|plz|thx|thanks|thank\s+you|ty|now|quick(?:ly)?|fast|"
    r"mate|man|already)\b)+[\s!?.]*$",
    re.IGNORECASE,
)

#: A segment that asks for the OUTPUT rather than mutating the list ("Output exactly the final
#: array in [1,2,3] format, no spaces", "what is the list now?"). Skipped, not executed -- but only
#: when it is visibly about the result; "give me a haiku" is neither an op nor an output ask, and
#: the all-or-nothing rule sends the turn to the model.
_SEQUENCE_OUTPUT_DIRECTIVE_RE = re.compile(
    r"^(?:output|print|return|respond|answer|reply|show|give|display|format|render|state|write|tell)\b",
    re.IGNORECASE,
)
_SEQUENCE_QUESTION_RE = re.compile(r"^(?:what|whats|what's|how)\b", re.IGNORECASE)
_SEQUENCE_OUTPUT_TOPIC_RE = re.compile(
    r"\b(?:list|array|sequence|result|final|output|value|values|elements|contents?|format|"
    r"bracket(?:s|ed)?|spaces?|json|answer|now|it|look)\b|\[",
    re.IGNORECASE,
)

#: A bracketed literal in the STEP text is a format example ("in [1,2,3] format"), never data --
#: the data literal was consumed by the declaration match. Its commas would otherwise be split as
#: step separators and shred the directive that carries it.
_SEQUENCE_FORMAT_EXAMPLE_RE = re.compile(r"\[[^\]]{0,80}\]")

#: A comma-split fragment of a format directive ("no spaces", "nothing else"). Pure qualifiers,
#: never operations; anything with more content than these fails the closed grammar as before.
_SEQUENCE_FORMAT_QUALIFIER_RE = re.compile(
    r"^(?:no\s+spaces?|no\s+markdown|no\s+prose|no\s+extra\s+text|nothing\s+else|"
    r"only\s+the\s+(?:list|array|numbers|values|result)|exactly|verbatim|raw|"
    r"one\s+line|single\s+line|on\s+one\s+line|in\s+brackets|as\s+is)$",
    re.IGNORECASE,
)

# Output clauses have a closed vocabulary too: a mention of "list" must not
# absorb a second request, and a format tail must not discard unknown work.
_SEQUENCE_PRESENTATION_WORDS = frozenset(
    ["output", "print", "return", "respond", "answer", "reply", "show", "give", "display", "format", "render", "state", "write", "tell", "what", "whats", "what's", "how", "is", "are", "does", "do", "me", "the", "a", "an", "my", "your", "of", "with", "in", "as", "at", "to", "but", "exactly", "only", "final", "resulting", "list", "array", "sequence", "seq", "vector", "stack", "queue", "numbers", "values", "value", "elements", "contents", "content", "result", "it", "now", "look", "looks", "like", "brackets", "bracket", "bracketed", "commas", "comma", "spaces", "space", "no", "absolutely", "punctuation", "end", "markdown", "explanation", "prose", "extra", "text", "nothing", "else", "verbatim", "raw", "one", "single", "line", "on", "without", "e", "g", "example", "json"]
)

#: Compact rendering is used only when the turn ASKS for it: "no spaces", an example like
#: "[1,2,3] format" / "[1,2,3]-style", or "in brackets".
_SEQUENCE_COMPACT_RE = re.compile(
    r"\bno\s+spaces\b|\]\s*[-–]?\s*(?:style|format)|\bin\s+brackets\b|\bbracketed\b",
    re.IGNORECASE,
)

_SEQ_SCALE_RE = re.compile(
    rf"(?P<op>multiply|divide)\s+(?:the\s+)?(?P<which>first|last|every|each|all)\s+elements?\s+"
    rf"by\s+(?:the\s+)?(?:number\s+|value\s+)?(?P<n>{_SEQUENCE_NUMBER})",
    re.IGNORECASE,
)
_SEQ_PREPEND_RE = re.compile(
    rf"prepend\s+(?:the\s+)?(?:number\s+|value\s+)?(?P<n>{_SEQUENCE_NUMBER})"
    rf"(?:\s+to\s+(?:it|the\s+(?:front|start|beginning)(?:\s+of\s+the\s+(?:list|array))?))?",
    re.IGNORECASE,
)
_SEQ_ADD_FRONT_RE = re.compile(
    rf"(?:add|insert|put)\s+(?:the\s+)?(?:number\s+|value\s+)?(?P<n>{_SEQUENCE_NUMBER})\s+"
    rf"(?:to|at)\s+the\s+(?:front|start|beginning)(?:\s+of\s+the\s+(?:list|array))?",
    re.IGNORECASE,
)
_SEQ_APPEND_RE = re.compile(
    rf"(?:append|push|add)\s+(?:the\s+)?(?:number\s+|value\s+)?(?P<n>{_SEQUENCE_NUMBER})"
    rf"(?:\s+to\s+(?:it|the\s+(?:end|back|list|array)(?:\s+of\s+the\s+(?:list|array))?))?",
    re.IGNORECASE,
)
_SEQ_REMOVE_RE = re.compile(
    rf"(?:remove|delete)\s+(?:the\s+)?(?:number\s+|value\s+)?(?P<n>{_SEQUENCE_NUMBER})"
    rf"(?:\s+from\s+(?:it|the\s+(?:list|array)))?",
    re.IGNORECASE,
)
_SEQ_REVERSE_RE = re.compile(
    # One optional modifier word before the noun: the ENTIRE array, the WHOLE list. Measured on
    # the operator's real turn -- "reverse the entire array" fell out of the closed set and the
    # whole turn went to a model that answered [8,7] for [8,7,5].
    r"reverse\s*(?:it|the\s+(?:\w+\s+)?(?:list|array|order|elements|sequence))?",
    re.IGNORECASE,
)
_SEQ_SORT_RE = re.compile(
    r"sort\s*(?:it|the\s+(?:list|array))?\s*"
    r"(?:(?:in\s+)?(?P<dir>asc(?:ending)?|desc(?:ending)?)(?:\s+order)?)?",
    re.IGNORECASE,
)


def _apply_sequence_step(segment: str, values: list[float]) -> list[float] | None:
    """Execute ONE recognized step, or return None for anything outside the closed set.

    Every pattern is a FULLMATCH on the cleaned segment: leftover words ("sort by relevance",
    "add 4 to every element") mean the step is not the closed-set op it resembles, and the whole
    turn belongs to the model.
    """

    scale = _SEQ_SCALE_RE.fullmatch(segment)
    if scale is not None:
        factor = float(scale.group("n"))
        op = scale.group("op").lower()
        if op == "divide" and factor == 0:
            return None
        which = scale.group("which").lower()
        out = list(values)
        if not out:
            return None

        def _scaled(value: float) -> float:
            return value / factor if op == "divide" else value * factor

        if which == "first":
            out[0] = _scaled(out[0])
        elif which == "last":
            out[-1] = _scaled(out[-1])
        else:
            out = [_scaled(value) for value in out]
        return out
    prepend = _SEQ_PREPEND_RE.fullmatch(segment) or _SEQ_ADD_FRONT_RE.fullmatch(segment)
    if prepend is not None:
        return [float(prepend.group("n")), *values]
    append = _SEQ_APPEND_RE.fullmatch(segment)
    if append is not None:
        return [*values, float(append.group("n"))]
    remove = _SEQ_REMOVE_RE.fullmatch(segment)
    if remove is not None:
        target = float(remove.group("n"))
        out = list(values)
        try:
            out.remove(target)
        except ValueError:
            # Removing a value that is not present has no exact semantics ("error" vs "no-op" is
            # a judgment call), so the turn is the model's.
            return None
        return out
    if _SEQ_REVERSE_RE.fullmatch(segment) is not None:
        return list(reversed(values))
    sort = _SEQ_SORT_RE.fullmatch(segment)
    if sort is not None:
        direction = (sort.group("dir") or "asc").lower()
        return sorted(values, reverse=direction.startswith("desc"))
    return None


def evaluate_sequence_ops_request(text: str) -> str | None:
    """A literal starting list plus an ordered closed verb set, executed exactly.

    Returns the final list -- compact ("[10,3,4]") when the turn asked for a bracket/no-spaces
    form, a sentence otherwise -- or None the moment anything falls outside the closed grammar.
    """

    body = " ".join(str(text or "").split())
    if not body or len(body) > 2000:
        return None
    declaration = _SEQUENCE_LIST_DECLARATION_RE.search(body)
    if declaration is None:
        return None
    subject_match = _SEQUENCE_SUBJECT_RE.search(body[: declaration.start("list")])
    if not subject_match:
        return None
    prefix = body[:subject_match.start()]
    if not re.fullmatch(r"(?:(?:ok|okay|so|please|my|the|this)\b[\s,]*)*", prefix, re.IGNORECASE):
        return None
    # Only an optional sequence name may sit between its noun and declaration.
    if not re.fullmatch(r"\s*(?:[A-Za-z]\w{0,15})?\s*", body[subject_match.end():declaration.start()]):
        return None
    # The declared subject's own tokens ("Array Q" -> array, q) count as on-topic below: a
    # question naming the subject ("What is Q now?") is about the list by definition, and the
    # generic topic vocabulary cannot enumerate every name a user gives a list. Single letters
    # only count when uppercase in the original (Q the variable, not q the word).
    # Harvest from the whole span between the subject noun and the list literal -- the NAME sits
    # there ("Array Q starts at [9, 8]" -> Q), outside the subject regex's own match. Grammar
    # words from the declaration are excluded so "starts"/"with" never count as the subject.
    subject_tokens = {
        token.lower()
        for token in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*",
            body[subject_match.start() : declaration.start("list")],
        )
        if (len(token) > 1 or token.isupper())
        and token.lower() not in {"starts", "start", "begins", "begin", "with", "as", "at",
                                  "is", "initially", "contains", "the", "my", "a", "an"}
    }
    values = [float(token) for token in re.findall(_SEQUENCE_NUMBER, declaration.group("list"))]
    if not values or len(values) > 256:
        return None
    executed = 0
    in_format_tail = False
    steps_text = _SEQUENCE_FORMAT_EXAMPLE_RE.sub("[example]", body[declaration.end() :])
    for raw_segment in _SEQUENCE_SPLIT_RE.split(steps_text):
        segment = _SEQUENCE_LEADING_FILLER_RE.sub("", (raw_segment or "").strip(" ,.;:!?")).strip()
        segment = _SEQUENCE_TRAILING_FILLER_RE.sub("", segment).strip()
        if not segment:
            continue
        if _SEQUENCE_FORMAT_QUALIFIER_RE.match(segment):
            continue
        if _SEQUENCE_OUTPUT_DIRECTIVE_RE.match(segment) or _SEQUENCE_QUESTION_RE.match(segment):
            segment_words = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", segment)}
            if ((_SEQUENCE_OUTPUT_TOPIC_RE.search(segment) or (subject_tokens & segment_words))
                    and segment_words <= (_SEQUENCE_PRESENTATION_WORDS | subject_tokens)):
                in_format_tail = True
                continue
            return None
        applied = _apply_sequence_step(segment, values)
        if applied is None:
            words = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", segment)}
            if in_format_tail and words and words <= _SEQUENCE_PRESENTATION_WORDS:
                continue
            return None
        values = applied
        executed += 1
        if executed > 64:
            return None
    if executed == 0:
        return None
    rendered = [_format_number(value) for value in values]
    if _SEQUENCE_COMPACT_RE.search(body):
        return "[" + ",".join(rendered) + "]"
    return "Final list: [" + ", ".join(rendered) + "]."


# ---------------------------------------------------------------------------------------------
# Deterministic string-transform lane (E-class). Measured live 2026-08-15, repeatedly, across
# models and runs: "String X is 'VOOL'... replace 'O' with '0'. Reverse... " died in the format
# seal; "String P is 'MESH'. String Q is 'NET'..." answered T3N3HSM (correct T3NHS3M);
# "Reverse the string `/usr/local/bin/node`" died in the empty-format notice; two ORCHESTRATOR
# transforms garbled. Exactly-computable text transforms on QUOTED literals belong to no model.
# Same doctrine as the sequence lane: a closed op set, fullmatch per step, all-or-nothing decline.
# ---------------------------------------------------------------------------------------------

#: Quoted/backticked literals are masked to \x00N\x00 placeholders BEFORE splitting, so a literal
#: containing dots, commas or spaces ('/usr/local/bin/node', "Hello World") cannot be shredded by
#: the sentence splitter and can never be misread as an op word.
_STR_LITERAL_RE = re.compile(
    "[‘“'\"`]([^‘’“”'\"`]{1,512})[’”'\"`]"
)
_STR_PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")
_STR_DECL_RE = re.compile(
    r"^(?:set\s+)?(?:string|variable|var|word|text|value|letter)\s+(?P<name>[A-Za-z]\w{0,15})\s*"
    r"(?:is|=|to:?|:)\s*\x00(?P<idx>\d+)\x00$",
    re.IGNORECASE,
)
#: The noun carrying the literal is often qualified ("the ordinary English word `WON`", "the
#: title 'AVATAR'"). Measured live 2026-08-17 on a 141-prompt drive: those two shapes declined
#: here and the model then reversed both strings wrong. The adjective run is BOUNDED (three
#: words, from a closed qualifier set) and the noun set stays closed, so the segment still has to
#: be nothing but a declaration -- "the word I sent you earlier" has no literal and still fails.
_STR_ANON_DECL_RE = re.compile(
    r"^(?:take\s+|start\s+with\s+|given\s+|use\s+|consider\s+)?the\s+"
    r"(?:(?:exact|ordinary|plain|simple|literal|english|actual|whole|entire|single|common|"
    r"following|given|raw|bare|original|uppercase|lowercase)\s+){0,3}"
    r"(?:word|string|text|phrase|title|name|term|token|label|letters)\s+"
    r"(?:is\s+)?\x00(?P<idx>\d+)\x00$",
    re.IGNORECASE,
)
_STR_CONCAT_RE = re.compile(
    r"^concatenate|^combine|^join", re.IGNORECASE
)
_STR_CONCAT_FULL_RE = re.compile(
    r"^(?:concatenate|combine|join)\s+"
    r"(?:them(?:\s+together)?|(?:variable\s+|string\s+)?(?P<a>[A-Za-z]\w{0,15})\s+(?:and|with)\s+"
    r"(?:variable\s+|string\s+)?(?P<b>[A-Za-z]\w{0,15}))"
    r"(?P<space>\s+with\s+a\s+space(?:\s+in\s+between)?)?"
    r"(?:\s+(?:with\s+)?no\s+space(?:s)?(?:\s+in\s+between)?)?"
    r"(?:\s+(?:to|into|making|giving|forming)\s+\x00(?P<stated>\d+)\x00)?$",
    re.IGNORECASE,
)
_STR_REPLACE_RE = re.compile(
    r"^replace\s+(?:every\s+|all\s+|each\s+)?(?:the\s+letter\s+|occurrences\s+of\s+)?"
    r"(?:\x00(?P<oldp>\d+)\x00|(?P<old>\w))(?:'?s)?\s+with\s+"
    r"(?:\x00(?P<newp>\d+)\x00|(?P<new>\w+))$",
    re.IGNORECASE,
)
_STR_REMOVE_RE = re.compile(
    r"^(?:remove|delete|strip)\s+(?:every\s+|all\s+|each\s+)?"
    r"(?:(?:the\s+letter\s+)?(?:\x00(?P<remp>\d+)\x00|(?P<rem>\w))(?:'?s)?|(?P<spaces>spaces?)|(?P<vowels>vowels?))$",
    re.IGNORECASE,
)
_STR_REVERSE_RE = re.compile(
    r"^reverse\s*(?:it|them|everything|"
    r"the\s+(?:\w+\s+)?(?:string|word|text|thing|result|order|letters?|characters?|chars?|"
    r"spelling)|"
    r"the\s+(?:string|word|text|title|name|phrase|term|label|token|letters)\s+"
    r"\x00(?P<idx>\d+)\x00)?"
    r"(?:\s*,?\s*(?:completely|entirely|fully|totally|exactly))?"
    r"(?:\s*,?\s*(?:backwards?|in\s+reverse))?$",
    re.IGNORECASE,
)
_STR_CASE_RE = re.compile(
    r"^(?:(?:make|convert|turn|put)\s+(?:it|them|the\s+(?:string|word|text|result))?\s*"
    r"(?:to|into|in)?\s*|in\s+all\s+|all\s+)?"
    r"(?P<case>upper\s*case|lower\s*case|caps|capitals)(?:\s+it)?$",
    re.IGNORECASE,
)
_STR_BASE64_RE = re.compile(
    r"^(?:base\s*64[-\s]?encode\s*(?:it|the\s+(?:string|word|text|result))?|"
    r"encode\s+(?:it|the\s+(?:exact\s+)?(?:string|word|text|result)(?:\s+\x00(?P<idx>\d+)\x00)?)?\s*"
    r"(?:into|in|as|to)\s+base\s*64)$",
    re.IGNORECASE,
)
_STR_SLICE_RE = re.compile(
    r"^(?:take|keep|use)\s+(?:only\s+)?the\s+(?P<which>first|last)\s+(?P<n>\d{1,3})\s+"
    r"(?:characters?|chars?|letters?)(?:\s+of\s+(?:it|the\s+(?:string|word|text|result)))?$",
    re.IGNORECASE,
)
#: HOW-constraints and reassurances are not steps: they restrict tools/format, never the value.
#: Serving the turn deterministically satisfies every one of them by construction.
_STR_CONSTRAINT_RE = re.compile(
    r"^(?:do\s+not\b|don'?t\b|dont\b|never\b|no\s+(?:tools?|web|internet|python|bash|code|"
    r"markdown|explanations?|prose|extra\s+text|conversational\s+filler)\b|without\s+using\b|"
    r"strict(?:\s+rule)?\b|note\b|remember\b|important\b|i\s*'?a?m\s+just\s+asking\b|"
    r"this\s+is\s+(?:just\s+)?a\s+string\b|wait\b)",
    re.IGNORECASE,
)
#: The same HOW-constraints stated as FRAMING rather than as a prohibition: "Using only the
#: currently active model", "LOCAL EXECUTION ONLY". Measured live 2026-08-17: five of five
#: reversals on a 141-prompt drive declined here and the model answered every one of them wrong.
#: This one is matched with fullmatch, not match, and the segment must END on the means noun --
#: that is the whole safety argument. "Using only your internal knowledge tell me the capital of
#: France" carries a real second task past the noun, fails the fullmatch, and still declines the
#: turn. A prefix match would have swallowed the task and answered half the turn.
_STR_FRAMING_CONSTRAINT_RE = re.compile(
    r"(?:using|use|with|via|through|from)\s+(?:only\s+)?(?:the\s+|your\s+|this\s+|its\s+)?"
    r"(?:\w+\s+){0,3}"
    r"(?:model|models|llm|weights|knowledge|memory|reasoning|context|runtime|engine|assistant|"
    r"tokenizer|vocabulary)(?:\s+only)?"
    r"|(?:local|offline|on[-\s]?device|in[-\s]?process|internal|deterministic|air[-\s]?gapped|"
    r"sandboxed|single[-\s]?model|one[-\s]?model)(?:\s+\w+){0,2}\s+only"
    r"|(?:local|offline|on[-\s]?device)\s+(?:execution|inference|processing|mode|compute)"
    r"(?:\s+only)?"
    r"|no\s+(?:external|outbound|network|remote|cloud|third[-\s]?party)(?:\s+\w+){0,2}",
    re.IGNORECASE,
)
#: A RETRACTION withdraws what was said BEFORE it in the same turn ("Search the internet for X.
#: HOLD ON. Abort the web search. ... reverse the letters"). Measured live 2026-08-17: that turn
#: declined and the model returned a wrong reversal. Retracted segments are dropped unread;
#: everything AFTER the last retraction still has to satisfy the closed grammar in full.
#:
#: The object vocabulary is what keeps this safe, and it is deliberately tight: matched with
#: fullmatch, and only over things a turn can withdraw (a search, a query, an instruction, a
#: step). "Cancel my subscription" and "Cancel the meeting with Bob" do NOT match -- they are
#: real work, and a turn carrying them must still decline rather than be answered halfway.
_STR_RETRACTION_RE = re.compile(
    r"(?:hold\s+(?:on|up)|wait|wait\s+a\s+(?:sec(?:ond)?|moment|minute)|"
    r"one\s+(?:sec(?:ond)?|moment|minute)|stop|scratch\s+that|strike\s+that|belay\s+that|"
    r"never\s*mind|nvm|correction|(?:on\s+)?second\s+thought(?:s)?|actually\s+no|no\s+wait|"
    r"(?:actually\s+)?(?:abort|cancel|forget|disregard|ignore|drop|skip|scratch|undo)"
    r"(?:\s+\w+){0,3}\s+"
    r"(?:search(?:es)?|lookup(?:s)?|quer(?:y|ies)|request(?:s)?|instruction(?:s)?|task(?:s)?|"
    r"step(?:s)?|question(?:s)?|prompt(?:s)?|part|above|that|this|it)"
    r"|(?:abort|cancel|forget|disregard|ignore|scratch|undo)\s+(?:that|this|it|the\s+above))",
    re.IGNORECASE,
)
#: A comma or sentence split leaves the conjunction on the front of the next segment ("and output
#: ONLY that reversed string"), which no recognizer in the closed set starts with. Stripped for
#: the same reason the sequence lane strips "i"/"please": it is grammar, never an operation.
_STR_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:and|then|also|but|finally|lastly|next|first|second|third|after\s+that)\b[\s,]*)+",
    re.IGNORECASE,
)
_STR_OUTPUT_TOPIC_RE = re.compile(
    r"\b(?:string|word|text|result|final|output|answer|value|it|state)\b|\x00\d+\x00",
    re.IGNORECASE,
)
#: An output ASK is presentation and is skipped -- so it must be nothing BUT presentation. The
#: topic search above is a substring test and lets a real second task through on one shared noun:
#: "write it to /tmp/out.txt" and "tell me whether that is a real word" both start with an output
#: verb and both mention a topic word, and both were measured being swallowed here once the "and"
#: separator started handing this branch its own segment. Fullmatched against a closed
#: vocabulary, both fail and the whole turn declines to a lane that can serve them.
_STR_OUTPUT_ASK_RE = re.compile(
    r"^(?:output|print|return|respond|reply|show|give|display|state|write|tell|say)"
    r"(?:\s+(?:with|me|us|back))*"
    r"(?:\s+(?:only|just|exactly|simply|purely|precisely))*"
    r"(?:\s+(?:the|that|this|its|your))?"
    r"(?:\s+(?:final|resulting|reversed|new|full|entire|complete|last|transformed|modified|"
    r"updated|combined|concatenated|encoded|joined|whole))*"
    r"(?:\s+(?:string|word|text|result|value|answer|output|thing|it))?"
    r"(?:\s+(?:only|verbatim|exactly|as\s+is|in\s+full|and\s+nothing\s+else))*$",
    re.IGNORECASE,
)
_STR_OUTPUT_QUESTION_RE = re.compile(
    r"^(?:what|what'?s|whats|how)(?:\s+(?:is|are|does|do|did))?"
    r"(?:\s+(?:the|that|this|it|its|your))?"
    r"(?:\s+(?:final|resulting|reversed|new|full|entire|complete|last|transformed))*"
    r"(?:\s+(?:string|word|text|result|value|answer|output|thing))?"
    r"(?:\s+(?:now|then|look\s+like|at\s+the\s+end))?$",
    re.IGNORECASE,
)
#: Once an output ask has been seen, unrecognized comma fragments of the format directive are
#: skipped. A fragment that OPENS with a real action verb is not format -- it is work this lane
#: cannot do ("... output only the result. Then delete the file config.yaml"), and swallowing it
#: would answer half the turn with full confidence.
_STR_UNSERVED_ACTION_RE = re.compile(
    r"^(?:reverse|replace|remove|delete|strip|encode|decode|concatenate|combine|join|translate|"
    r"uppercase|lowercase|capitali[sz]e|trim|slice|split|sort|shuffle|append|prepend|insert|"
    r"send|email|post|save|upload|publish|run|execute|install|deploy|fetch|download|search|"
    r"call|open|close|create|make|add|set|move|copy|rename|summari[sz]e|explain|draft)\b",
    re.IGNORECASE,
)
_STR_BARE_OUTPUT_RE = re.compile(
    r"\b(?:only|exactly|just)\s+the\b|\bnothing\s+else\b|\boutput\s+only\b|\bonly\s+output\b",
    re.IGNORECASE,
)
#: Commas are step separators here for the same reason as the sequence lane: every
#: comma-bearing literal was masked to a placeholder before splitting.
#: "and" is a step separator ONLY in front of a verb the closed grammar already knows ("... `WON`
#: and reverse it"): a bare "and" split would shred "concatenate A and B", whose two operands are
#: names, not verbs. The lookahead deliberately also lists verbs this lane cannot serve
#: (send/email/tell) so a real second task lands as its own segment and declines cleanly instead
#: of hiding inside a longer one.
_STR_SPLIT_RE = re.compile(
    r"(?<=[.!?;])\s+|\n|,|\s+(?:and\s+)?then\s+|\s+(?:pls|please|plz|kindly)\s+"
    r"|\s+and\s+(?=(?:reverse|replace|remove|delete|strip|make|convert|turn|put|encode|"
    r"base\s*64|take|keep|use|slice|trim|output|print|return|respond|answer|reply|show|give|"
    r"display|format|render|state|write|tell|send|email|also|finally|lastly|next)\b)",
    re.IGNORECASE,
)


def evaluate_string_transform_request(text: str) -> str | None:
    """Quoted literals plus an ordered closed transform set, executed exactly.

    Returns the final string -- bare when the turn asked for only it, labelled otherwise -- or
    None the moment anything falls outside the closed grammar: an op not in the set, a reference
    to an undeclared name, a stated intermediate that contradicts the computation, or a judgment
    edit ("make it catchier"), which is a model decision by definition.
    """

    body = " ".join(str(text or "").split())
    if not body or len(body) > 2000:
        return None
    literals: list[str] = []

    def _mask(match: re.Match[str]) -> str:
        literals.append(match.group(1))
        return f"\x00{len(literals) - 1}\x00"

    masked = _STR_LITERAL_RE.sub(_mask, body)
    if not literals:
        return None

    named: dict[str, str] = {}
    order: list[str] = []
    working: str | None = None
    executed = 0
    in_output_tail = False
    wants_bare = bool(_STR_BARE_OUTPUT_RE.search(masked))

    def _lit(index_text: str | None) -> str | None:
        if index_text is None:
            return None
        position = int(index_text)
        return literals[position] if 0 <= position < len(literals) else None

    stated: list[str] = []
    for raw_segment in _STR_SPLIT_RE.split(masked):
        segment = _SEQUENCE_LEADING_FILLER_RE.sub("", (raw_segment or "").strip(" ,.;:!?")).strip()
        segment = _STR_LEADING_FILLER_RE.sub("", segment).strip()
        segment = _SEQUENCE_LEADING_FILLER_RE.sub("", segment).strip()
        segment = _SEQUENCE_TRAILING_FILLER_RE.sub("", segment).strip()
        if segment:
            stated.append(segment)

    # A retraction withdraws everything before it, so those segments are never read -- and the
    # retraction marker itself sits in the withdrawn prefix. Everything after the LAST one still
    # has to satisfy the closed grammar; a turn whose every segment is withdrawn executes nothing
    # and declines below, which is the right answer for "reverse `X`. Actually, never mind."
    last_retraction = -1
    for index, segment in enumerate(stated):
        if _STR_RETRACTION_RE.fullmatch(segment):
            last_retraction = index

    for segment in stated[last_retraction + 1 :]:
        if (
            _STR_CONSTRAINT_RE.match(segment)
            or _STR_FRAMING_CONSTRAINT_RE.fullmatch(segment)
            or _SEQUENCE_FORMAT_QUALIFIER_RE.match(segment)
        ):
            continue
        declaration = _STR_DECL_RE.match(segment) or _STR_ANON_DECL_RE.match(segment)
        if declaration:
            value = _lit(declaration.group("idx"))
            if value is None:
                return None
            name = (declaration.groupdict().get("name") or f"_anon{len(order)}").upper()
            named[name] = value
            order.append(name)
            working = value
            continue
        if _SEQUENCE_OUTPUT_DIRECTIVE_RE.match(segment) or _SEQUENCE_QUESTION_RE.match(segment):
            if _STR_OUTPUT_TOPIC_RE.search(segment) and (
                _STR_OUTPUT_ASK_RE.fullmatch(segment)
                or _STR_OUTPUT_QUESTION_RE.fullmatch(segment)
            ):
                in_output_tail = True
                continue
            return None
        concat = _STR_CONCAT_FULL_RE.match(segment)
        if concat:
            if concat.group("a") and concat.group("b"):
                first = named.get(concat.group("a").upper())
                second = named.get(concat.group("b").upper())
                if first is None or second is None:
                    return None
                parts = [first, second]
            else:
                if len(order) < 2:
                    return None
                parts = [named[key] for key in order]
            joiner = " " if concat.group("space") else ""
            candidate = joiner.join(parts)
            stated = _lit(concat.group("stated"))
            if stated is not None and stated != candidate:
                # The user's stated intermediate contradicts the computation: not this lane's turn.
                return None
            working = candidate
            executed += 1
            continue
        if _STR_CONCAT_RE.match(segment):
            # A concatenate step outside the closed shape (unknown names, extra prose) poisons
            # the whole turn rather than half-matching.
            if in_output_tail:
                continue
            return None
        applied: str | None = None
        replace = _STR_REPLACE_RE.match(segment)
        reverse = _STR_REVERSE_RE.match(segment)
        remove = _STR_REMOVE_RE.match(segment)
        case = _STR_CASE_RE.match(segment)
        b64 = _STR_BASE64_RE.match(segment)
        slice_ = _STR_SLICE_RE.match(segment)
        if reverse and reverse.groupdict().get("idx"):
            inline = _lit(reverse.group("idx"))
            if inline is None:
                return None
            working = inline
        if b64 and b64.groupdict().get("idx"):
            inline = _lit(b64.group("idx"))
            if inline is None:
                return None
            working = inline
        if working is None and (replace or reverse or remove or case or b64 or slice_):
            return None
        if replace:
            old = _lit(replace.group("oldp")) or replace.group("old")
            new = _lit(replace.group("newp"))
            if new is None:
                new = replace.group("new") or ""
            if not old:
                return None
            applied = working.replace(old, new).replace(old.lower(), new).replace(old.upper(), new)
        elif remove:
            if remove.group("spaces"):
                applied = working.replace(" ", "")
            elif remove.group("vowels"):
                applied = "".join(ch for ch in working if ch.lower() not in "aeiou")
            else:
                target = _lit(remove.group("remp")) or remove.group("rem")
                if not target:
                    return None
                applied = working.replace(target, "").replace(target.lower(), "").replace(
                    target.upper(), ""
                )
        elif reverse:
            applied = working[::-1]
        elif case:
            applied = (
                working.upper()
                if "upper" in case.group("case").lower() or "cap" in case.group("case").lower()
                else working.lower()
            )
        elif b64:
            import base64 as _base64

            applied = _base64.b64encode(working.encode("utf-8")).decode("ascii")
        elif slice_:
            count = int(slice_.group("n"))
            applied = working[:count] if slice_.group("which").lower() == "first" else working[-count:]
        else:
            if in_output_tail and not _STR_UNSERVED_ACTION_RE.match(segment):
                continue
            return None
        working = applied
        executed += 1
        if executed > 64:
            return None
    if executed == 0 or working is None:
        return None
    if wants_bare or in_output_tail:
        return working
    return f"Final string: {working}"


def evaluate_direct_math_request(text: str) -> str | None:
    expression = _direct_math_expression(text)
    if not expression or not looks_like_direct_math_request(expression):
        # A named operation ("square root of 144", "12 squared", "5% of 200") carries no operator
        # symbol, and a sequence-op turn ("list is [1,2,3], append 4, reverse") is longer than the
        # expression path's cap, so the expression path above never sees either. Falling through
        # here means every existing caller inherits both without a wiring change. Sequence ops run
        # first: they match only a literal-list declaration, while the named-math patterns search
        # anywhere in the text and would otherwise answer a stray "5% of 200" inside a sequence
        # turn they cannot execute.
        # String transforms first: they require a QUOTED literal declaration, the most specific
        # shape here, and their literals may contain digits that would confuse the looser lanes.
        return (
            evaluate_string_transform_request(text)
            or evaluate_sequence_ops_request(text)
            or evaluate_named_math_request(text)
        )

    try:
        parsed = ast.parse(expression, mode="eval")
    except Exception:
        return None

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Num):  # pragma: no cover
            return float(node.n)
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_ARITHMETIC_OPERATORS:
            return _SAFE_ARITHMETIC_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_ARITHMETIC_OPERATORS:
            return _SAFE_ARITHMETIC_OPERATORS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported arithmetic node")

    try:
        value = _eval(parsed)
    except Exception:
        return None

    rendered = str(int(value)) if isinstance(value, float) and value.is_integer() else f"{value:.12g}"
    return f"{expression} = {rendered}."


def evaluate_word_math_request(text: str) -> str | None:
    normalized = " ".join(str(text or "").strip().split())
    if not looks_like_word_math_request(normalized):
        return None

    task_triplet = re.search(
        r"task a takes (?P<a>\d+(?:\.\d+)?) [^.?!]*?task b takes twice task a minus (?P<delta>\d+(?:\.\d+)?) [^.?!]*?task c takes (?P<c>\d+(?:\.\d+)?)",
        normalized,
        re.IGNORECASE,
    )
    if task_triplet is None:
        return None

    a = float(task_triplet.group("a"))
    delta = float(task_triplet.group("delta"))
    c = float(task_triplet.group("c"))
    b = (2 * a) - delta
    total = a + b + c

    def _render(value: float) -> str:
        return str(int(value)) if value.is_integer() else f"{value:.12g}"

    return (
        f"Task A = {_render(a)}. "
        f"Task B = 2 * {_render(a)} - {_render(delta)} = {_render(b)}. "
        f"Task C = {_render(c)}. "
        f"Total = {_render(a)} + {_render(b)} + {_render(c)} = {_render(total)}."
    )


def looks_like_word_math_request(text: str) -> bool:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or looks_like_direct_math_request(lowered):
        return False
    if len(re.findall(r"\d+(?:\.\d+)?", lowered)) < 2:
        return False
    if not any(marker in lowered for marker in _WORD_MATH_MARKERS):
        return False
    return any(marker in lowered for marker in _WORD_MATH_CONTEXT_MARKERS)


def looks_like_live_recency_lookup(text: str) -> bool:
    """Whether the turn asks for a fact that is only true right now.

    Both marker tables are space-padded (`" latest "`, `" today "`, `" price"`) so a marker cannot
    match inside a longer word. That padding only works against text that is itself padded and has
    its sentence punctuation separated, and this function tested it against neither -- so a marker
    at the START or END of the sentence never matched. Measured on the untouched base:

        "Latest BTC price."           -> " latest " absent (start of string, and no space before ".")
        "Current weather in Vilnius." -> " current " absent (start of string)
        "Explain the recent USD move today."  -> " today " absent (welded to the full stop)

    All three are exactly the live questions this predicate exists to find, and all three read
    False -- while "what is the latest BTC price right now", the same question with the markers
    sitting mid-sentence, read True. The verdict depended on where in the sentence the user put the
    word. This is the same defect class as the `" git status "` test in `core/execution/planner.py`,
    and it is normalized the same way.
    """
    from core.retrieval_constraints import analyze_retrieval_constraints

    constraints = analyze_retrieval_constraints(text)
    if not constraints.eligible_text or constraints.forbids_candidate(constraints.eligible_text):
        return False
    cleaned = re.sub(r"[.,;:!?]+", " ", constraints.eligible_text.strip().lower())
    if not cleaned.strip():
        return False
    lowered = f" {' '.join(cleaned.split())} "
    has_recency = any(marker in lowered for marker in _LIVE_RECENCY_MARKERS)
    has_domain = any(marker in lowered for marker in _LIVE_FACT_DOMAIN_MARKERS) or "what happened" in lowered
    return has_recency and has_domain


def looks_like_current_chat_recall(text: str) -> bool:
    """Return whether the user is recalling a current-chat fact, not live research."""
    return bool(_CURRENT_CHAT_RECALL_RE.search(" ".join(str(text or "").split())))


def looks_like_ordinary_conceptual_question(text: str) -> bool:
    """Keep everyday conceptual questions in conversation without swallowing topic lookups."""
    return bool(_ORDINARY_CONCEPTUAL_QUESTION_RE.search(" ".join(str(text or "").split())))


def looks_like_assistant_identity_question(text: str) -> bool:
    """Whether the turn asks what THIS assistant or app is called.

    Measured on the untouched base 6a73d526, this is the row that failed:

        "what is your name?"   research  summarization  queen  paid=True   daily  60.0

    Nothing in `classify` recognised the shape, so the turn fell all the way to
    `_bare_lookup_marker_claims_this_turn` and was claimed on the bare substring "what is" -- the
    same substring branch that already had to be pulled off local file reads and conversational
    corrections. `research` maps to `provider_role=queen` with `allow_paid_fallback=True`, so live
    the turn tried qwen3:14b and then a cloud arm, and took nearly three minutes to say "VOOL".

    The contraction was no better for being different: "what's your name?" misses the substring,
    lands on `unknown`, and `unknown` is what sends `classify` to `_classify_via_model` -- a model
    call spent deciding how to route a question whose answer is a field in a local identity file.

    The subject decision itself is not re-implemented here. It belongs to
    :mod:`core.user_identity_authority`, which is the one place that separates the assistant's name
    from the user's saved name, so "tell me your name" and "say my name" cannot drift apart between
    the router and the answer path.
    """
    from core.user_identity_authority import classify_identity_question

    return classify_identity_question(text).asks_assistant_identity


def looks_like_hypothetical_frame_turn(text: str) -> bool:
    """Whether the turn supplies its own premises, or forbids a lookup, and so must not buy `research`.

    Measured on the base 03b04b39, the live prompt --

        "Assume today is January 1st, 2035. The US President is a golden retriever named Buster …
        Based ONLY on these new facts … explain why your internal web search tools would fail to
        verify this transaction."

    -- was claimed by `looks_like_explicit_lookup_request` on the substring pair "search" + "web",
    taken from the clause asking the runtime to explain why searching would NOT work. `research`
    maps to `provider_role=queen` with `allow_paid_fallback=True`, and
    `core.execution.planner.should_attempt_tool_intent` returns True for `research` and
    `chat_research` unconditionally -- so a turn that stipulated its own facts, and said so, was
    handed a web-search catalog to go and check them against the real world.

    The retrieval decision is delegated to :func:`core.stipulated_frame.stipulated_frame_forbids_retrieval`.
    That contract keeps refusals and frame-owned targets local while allowing a separate fresh
    lookup about independent real-world data. Keeping the decision there prevents this router from
    disagreeing with preflight, execution requirements, or the tool planner.
    """
    from core.hypothetical_frame import detect_hypothetical_frame

    frame = detect_hypothetical_frame(text)
    if not frame.supplies_premises:
        return frame.search_refused

    # Retrieval scope has one owner. Do not independently re-interpret "latest", "current" or a
    # lookup verb here: the stipulated-frame contract already decides whether those words target
    # the supplied world or an independent real-world fact.
    from core.stipulated_frame import stipulated_frame_forbids_retrieval

    return stipulated_frame_forbids_retrieval(text)


_BARE_LOOKUP_MARKERS = ("find", "look up", "research", "search", "what is", "tell me about")

# A conversational opener carries no request. Stripped before the frame below is matched, because
# "ok what is usd?" and "what is usd?" are the same turn and were not treated as one: the anchored
# `^what is (a|an|one|some)` in `_ORDINARY_CONCEPTUAL_QUESTION_RE` cannot see past a leading "ok".
_DEFINITIONAL_OPENER_RE = re.compile(
    r"^\s*(?:ok(?:ay)?|so|well|right|hey|hi|hello|yo|and|but|also|just|quick(?:ly)?|btw|"
    r"sorry|please|now)\b[\s,.:;!-]*",
    re.IGNORECASE,
)
# "what is X" / "what's X" / "what are X" / "what does X mean" / "what does X stand for".
_DEFINITIONAL_FRAME_RE = re.compile(
    r"^what\s+do(?:es)?\s+(?P<term>.+?)\s+(?:mean|stand\s+for)\s*[?!.]*$"
    r"|^(?:what(?:'s|s|\s+is|\s+are))\s+(?P<subject>.+?)\s*[?!.]*$",
    re.IGNORECASE,
)
#: A term is a short noun phrase. Past this the turn is carrying its own content and belongs to
#: whatever lane that content implies -- the same reasoning as `_CORRECTION_MAX_WORDS`.
_DEFINITIONAL_MAX_TERM_WORDS = 5
#: A definitional frame over one of these is asking for a live VALUE, not for what a word means:
#: "what is BTC" is a definition, "what is the BTC price" is a lookup. This is the line that keeps
#: the demotion below from swallowing the live questions `looks_like_live_recency_lookup` owns.
_DEFINITIONAL_LIVE_VALUE_NOUNS = (
    "price", "prices", "weather", "forecast", "news", "headline", "headlines",
    "market", "markets", "rate", "rates", "score", "scores", "cost", "value",
)
#: A second clause means the frame is only the sentence's opening, not the whole request.
_DEFINITIONAL_CLAUSE_MARKERS = (" and ", " then ", " also ", " because ", " so that ", ";")
#: A genitive turns the frame from "what does this word mean" into "what is this ATTRIBUTE of that
#: ENTITY" -- a fact to be looked up, not a definition to be given. "what is the capital of France"
#: is the shape, and `tests/test_v050_trivial_tasks_stay_trivial.py` already pins it to `research`.
_DEFINITIONAL_GENITIVE_MARKERS = (" of ", "'s ")
#: A term is a NOUN PHRASE. Any of these inside it means the sentence is doing something else, so
#: they are rejected wherever they appear rather than only in first position:
#:
#: * a PREPOSITION makes the frame relational, not definitional -- "what is wrong WITH this config"
#:   is a diagnostic question and "what is IN package.json" is a file read. Both were claimed by an
#:   earlier form of this predicate that only checked the first word, and "what is wrong with this
#:   config" is a real turn in `tests/test_a_turn_with_no_request_is_answered_not_crashed.py`;
#: * a DEICTIC points at something in this conversation or on this machine -- "what is this project"
#:   belongs to the folder lanes and "what is that" to the correction branch above;
#: * a WH-WORD or copula means a clause follows, so the frame is only the sentence's opening.
_DEFINITIONAL_TERM_STOPWORDS = frozenset(
    {
        # deictic / possessive
        "this", "that", "these", "those", "it", "my", "our", "your", "his", "her", "their",
        # prepositional
        "in", "on", "at", "inside", "within", "under", "from", "with", "without", "for",
        "about", "between", "against", "into", "onto", "over", "per", "via", "by", "to",
        # clause openers
        "when", "why", "how", "who", "which", "whether", "if", "are", "is", "was", "were",
        "do", "does", "did", "should", "would", "could", "can",
    }
)
#: A filename is not a term to define. `_PATH_PATTERNS` only recognises a path with a separator, so
#: a bare "package.json" reaches here looking like a one-word noun phrase.
_DEFINITIONAL_FILENAME_RE = re.compile(r"^[\w.\-]+\.[A-Za-z0-9_+-]{1,8}$")


def looks_like_bare_definitional_question(text: str) -> bool:
    """Whether the whole turn is "what is <term>?" and nothing more.

    Measured on the untouched base 6204d352, at the three decisions that actually buy weight:

        "ok what is usd?"    research  summarization  queen  paid=True   daily  60.0
        "what is usd?"       research  summarization  queen  paid=True   daily  60.0
        "hey what is json?"  config    action_plan    auto   paid=False  deep   None

    The first two are claimed by `_bare_lookup_marker_claims_this_turn` on the bare substring
    "what is", which maps to `provider_role=queen` and `allow_paid_fallback=True` -- a heavier
    candidate ranking and a paid arm, bought for a three-word definition. The third is worse: the
    keyword branch above matches the substring "json" and returns `config`, whose `action_plan`
    output mode resolves to the **deep** lane with an **unbounded** fallback budget, which is the
    mechanism behind a multi-minute turn. Live, this turn tried qwen3:14b, then cloud, then qwen3:8b.

    The verdict for one short question therefore depended on which domain vocabulary its noun
    happened to belong to. A definition is the lightest semantic turn there is; it needs a model,
    but not a heavy one and not a paid arm.

    Deliberately NOT claimed, so the demotion cannot swallow a real lookup:

    * anything `looks_like_live_recency_lookup` owns -- "what is the latest BTC price right now";
    * a frame over a live VALUE noun -- "what is the BTC price" asks what it costs, not what it is;
    * an explicit lookup verb, a public-entity ("who is ...") shape, a named path or a workspace
      noun -- those keep the lanes they already had;
    * anything carrying a second clause, or a term longer than a short noun phrase.
    """
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return False
    lowered = raw.lower()
    if any(marker in f" {lowered} " for marker in _DEFINITIONAL_CLAUSE_MARKERS):
        return False
    if looks_like_live_recency_lookup(lowered):
        return False
    if looks_like_explicit_lookup_request(lowered) or looks_like_public_entity_lookup_request(lowered):
        return False
    if any(marker in lowered for marker in _NON_WEB_LOOKUP_EXCLUSIONS):
        return False
    if _REDACTED_PATH_TOKEN in lowered or any(p.search(lowered) for p in _PATH_PATTERNS):
        return False

    # Strip at most one opener: "ok what is usd?" is one turn, "ok so well ..." is someone stalling
    # and its shape is no longer a bare definition.
    stripped = _DEFINITIONAL_OPENER_RE.sub("", lowered, count=1).strip()
    match = _DEFINITIONAL_FRAME_RE.match(stripped)
    if not match:
        return False
    term = (match.group("term") or match.group("subject") or "").strip()
    if not term:
        return False
    words = term.split()
    if not words or len(words) > _DEFINITIONAL_MAX_TERM_WORDS:
        return False
    if any(word in _DEFINITIONAL_TERM_STOPWORDS for word in words):
        return False
    if any(noun in words for noun in _DEFINITIONAL_LIVE_VALUE_NOUNS):
        return False
    if any(_DEFINITIONAL_FILENAME_RE.match(word) for word in words):
        return False
    if any(marker in f" {term} " for marker in _DEFINITIONAL_GENITIVE_MARKERS):
        return False
    # A term is a noun phrase, so it must carry at least one word that is not a bare article.
    return any(word not in {"a", "an", "the", "one", "some", "any"} for word in words)


def _bare_lookup_marker_claims_this_turn(text: str) -> bool:
    """The weakest lookup rule in `classify`, held to the same exclusions as its siblings.

    This branch is a bare substring test, and `research` is an expensive verdict: it maps to
    task_kind `summarization`, `provider_role=queen` and `allow_paid_fallback=True`, so a turn it
    claims buys the heavy lane and a paid arm. Measured against QA-050-024's own trivial classes,
    two of them were being claimed here:

    * "read ~/Desktop/notes.txt and tell me what is in it" -- a two-line LOCAL file read (QA-050-011)
      routed to WEB research on the substring "what is";
    * ordinary questions naming a file, folder, repo or terminal, which `_NON_WEB_LOOKUP_EXCLUSIONS`
      already keeps away from `looks_like_explicit_lookup_request` and
      `looks_like_public_entity_lookup_request`.

    Nothing new is invented here: the exclusion set is the module's own, and this branch simply
    stops being the one lookup rule that ignores it. A turn naming a concrete local path is added
    for the same reason -- a place on this machine is not a thing to search the web for. Neither
    check touches what the turn can DO; a demoted turn lands on the ordinary conversational profile
    and the file lanes serve it as before.
    """
    from core.retrieval_constraints import analyze_retrieval_constraints

    constraints = analyze_retrieval_constraints(text)
    text = constraints.eligible_text.lower()
    if not text or constraints.forbids_candidate(text):
        return False
    if not any(marker in text for marker in _BARE_LOOKUP_MARKERS):
        return False
    if any(marker in text for marker in _NON_WEB_LOOKUP_EXCLUSIONS) or _is_mailbox_request(text):
        return False
    # `classify` redacts before it matches, so a path has already become the literal `<path>` by the
    # time this runs -- checking `_PATH_PATTERNS` here finds nothing and the raw text is not in
    # scope. The placeholder is the surviving evidence that a path was named, so that is what is
    # checked; the raw patterns stay for any caller that passes unredacted text.
    if _REDACTED_PATH_TOKEN in text:
        return False
    return not any(pattern.search(text) for pattern in _PATH_PATTERNS)


def looks_like_conversational_correction(text: str) -> bool:
    """Whether the turn is the user rejecting the answer they just got.

    Measured on the v0.5.0 smoke run (QA-050-018/019): "what is that? this is not what i have
    asked" was classified `research` -- because the bare substring "what is" is one of this
    module's lookup markers -- which maps to task_kind `summarization`, provider_role `queen` and
    `allow_paid_fallback`. The turn then spent a heavyweight escalation chain (a 14B local model, a
    pinned cloud model, an 8B local model) on a sentence whose whole content is "you got it wrong".
    A correction is the lightest turn there is: it needs the conversation, not a research lane.

    Three structural shapes. The first needs BOTH halves -- a rejection AND something for the
    rejection to point at -- so an ordinary sentence containing "wrong" is not a correction:

    * a rejection aimed at the previous exchange ("that is not what i asked", "no, that's wrong");
    * a bare deictic follow-up whose entire content is a pointer ("what is that?"), which is a
      question about the last answer and carries no topic to research;
    * a turn whose FIRST sentence is such a rejection, however long the rest runs. "No, that is
      wrong. Lumen is a photo editor, not a note-taking app. Fix the description." is eighteen words
      and every one of them after the first four is the user restating what they wanted. Measured
      on the v0.5.0 smoke run (QA-050-025), that is the turn the VoolBook fast path stood ready to
      swallow whole as a display name, so the length bound below must not be what decides it.

    Bounded and excluded on purpose. A correction that does NOT open with the rejection is terse, so
    a long turn is something else, and a turn naming a technical object ("why is this code wrong?")
    is a debugging request that must keep its own lane -- both would otherwise be pulled into
    conversation by the same words.
    """
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if _BARE_DEICTIC_QUESTION_RE.fullmatch(lowered):
        return True
    # Word boundaries, not substrings. Written as `marker in lowered` first, which read "script" out
    # of "de-script-ion" and "log" out of "dialogue" -- the same containment defect this module
    # already paid for with `x` inside "tax" and `del` inside "delta".
    if _contains_phrase_marker(lowered, _CORRECTION_TECHNICAL_EXCLUSIONS):
        return False
    if "/" in lowered:
        return False
    opening = _CORRECTION_OPENING_RE.split(lowered, maxsplit=1)[0]
    if opening != lowered and _is_rejection_of_the_last_answer(opening):
        return True
    if len(lowered.split()) > _CORRECTION_MAX_WORDS:
        return False
    return _is_rejection_of_the_last_answer(lowered)


def _is_rejection_of_the_last_answer(text: str) -> bool:
    """Both halves of a rejection: the refusal, and the thing being refused."""
    return bool(
        _CORRECTION_REJECTION_RE.search(text)
        and _PRIOR_TURN_REFERENCE_RE.search(text)
    )


def looks_like_canonical_project_question(text: str) -> bool:
    """Keep project questions on local canonical grounding unless live lookup is explicit."""
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered or not has_canonical_project_entity(lowered):
        return False
    if any(marker in lowered for marker in _WEB_SOCIAL_LOOKUP_MARKERS):
        return False
    question = re.sub(
        r"^(?:in|using|with)\s+(?:exactly\s+)?(?:one|two|three|\d+)\s+"
        r"(?:concise\s+)?(?:sentence|sentences|word|words)\s*[:,;-]?\s*",
        "",
        lowered,
    )
    return bool(
        re.search(
            r"^(?:what|who|why|how|where|which)\b|^tell\s+me\s+about\b|^explain\b|^describe\b",
            question,
        )
    )


@dataclass
class TaskRecord:
    task_id: str
    session_id: str
    task_class: str
    task_summary: str
    redacted_input_hash: str
    environment_os: str
    environment_shell: str
    environment_runtime: str
    environment_version_hint: str
    plan_mode: str
    share_scope: str
    confidence: float
    outcome: str
    harmful_flag: bool
    created_at: str
    updated_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_name() -> str:
    return "python"


def _runtime_version_hint() -> str:
    version = platform.python_version()
    major_minor = ".".join(version.split(".")[:2])
    return f"python-{major_minor}"


def _current_shell() -> str:
    if platform.system().lower() == "windows":
        return os.environ.get("COMSPEC", "cmd").split("\\")[-1].lower()
    return os.environ.get("SHELL", "sh").split("/")[-1].lower()


def redact_text(text: str) -> str:
    value = text.strip()
    value = _URL_RE.sub("<url>", value)
    value = _EMAIL_RE.sub("<email>", value)
    value = _TOKEN_RE.sub("<token>", value)

    for pattern in _PATH_PATTERNS:
        value = pattern.sub(_REDACTED_PATH_TOKEN, value)

    # compress whitespace
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def create_task_record(user_input: str, *, session_id: str | None = None) -> TaskRecord:
    redacted = redact_text(user_input)
    now = _utcnow()
    task_id = str(uuid.uuid4())
    task_summary = redacted[:240] if redacted else "empty_input"
    input_hash = hashlib.sha256(redacted.encode("utf-8")).hexdigest()
    policy = session_memory_policy(session_id)

    task = TaskRecord(
        task_id=task_id,
        session_id=str(session_id or ""),
        task_class="pending",
        task_summary=task_summary,
        redacted_input_hash=input_hash,
        environment_os=platform.system().lower(),
        environment_shell=_current_shell(),
        environment_runtime=_runtime_name(),
        environment_version_hint=_runtime_version_hint(),
        plan_mode=str(policy_engine.get("execution.default_mode", "advice_only")),
        share_scope=str(policy.get("share_scope") or "local_only"),
        confidence=0.0,
        outcome="pending",
        harmful_flag=False,
        created_at=now,
        updated_at=now,
    )

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO local_tasks (
                task_id, session_id, task_class, task_summary, redacted_input_hash,
                environment_os, environment_shell, environment_runtime, environment_version_hint,
                plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.session_id,
                task.task_class,
                task.task_summary,
                task.redacted_input_hash,
                task.environment_os,
                task.environment_shell,
                task.environment_runtime,
                task.environment_version_hint,
                task.plan_mode,
                task.share_scope,
                task.confidence,
                task.outcome,
                1 if task.harmful_flag else 0,
                task.created_at,
                task.updated_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    ensure_trace(task.task_id, trace_id=task.task_id)
    transition(
        entity_type="local_task",
        entity_id=task.task_id,
        to_state="created",
        details={"task_class": task.task_class, "plan_mode": task.plan_mode},
        trace_id=task.task_id,
    )
    return task


def load_task_record(task_id: str) -> TaskRecord | None:
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT task_id, session_id, task_class, task_summary, redacted_input_hash,
                   environment_os, environment_shell, environment_runtime, environment_version_hint,
                   plan_mode, share_scope, confidence, outcome, harmful_flag, created_at, updated_at
            FROM local_tasks
            WHERE task_id = ?
            LIMIT 1
            """,
            (clean_task_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = dict(row)
    data["harmful_flag"] = bool(data.get("harmful_flag"))
    return TaskRecord(**data)


# Risk markers, matched as COMMANDS rather than as substrings of English.
#
# The previous form was a plain `marker in text` containment test, and six of its eleven markers
# fired on ordinary prose. Measured against the real classifier: "website displays most of data in
# PDF format so agent needs to read it", "can you export the report in CSV format for me", "i am
# advising a startup on this", "look up the registry office address", "we exfiltrate nothing, that
# is the point" and "sudo is not installed on this box" ALL classified `risky_system_action`.
#
# That is not a cosmetic misclassification. `destructive_command` is in the hard_block set
# (core/agent_runtime/runtime_gate_policy.py), so the turn is refused outright, decomposition is
# skipped, confidence is clamped to 0.30, and the class falls out of the plain-text chat set — a
# question about a PDF became a blocked destructive action.
#
# Two shapes are distinguished:
#   COMMAND_TOKENS   only dangerous as the command being run. `format c:` is; "PDF format so" is
#                    not. Required to sit at a command position — start of the text or of a line, or
#                    after a shell separator (; && || |) — which is where a command actually appears.
#   ALWAYS_TOKENS    dangerous wherever they appear, but still matched on WORD BOUNDARIES so `cron`
#                    stops matching inside `cronbach` and `registry` inside a sentence about a
#                    registry office is judged by the word, not the letters.
#
# `system32`, `launchctl`, `systemctl` and `powershell -enc` are near-unambiguous and stay as word
# or literal matches. `startup` and `registry` are ordinary English nouns and are demoted to a
# command position: `launchctl load` and `reg add` still catch the real cases.
_RISK_COMMAND_TOKENS: tuple[tuple[str, str], ...] = (
    ("rm -rf", "destructive_command"),
    ("rm ", "destructive_command"),
    ("format", "destructive_command"),
    ("del ", "destructive_command"),
    ("mkfs", "destructive_command"),
    ("diskutil", "destructive_command"),
    ("sudo", "privileged_action"),
    ("reg add", "privileged_action"),
    ("reg delete", "privileged_action"),
    ("launchctl", "persistence_attempt"),
    ("systemctl", "persistence_attempt"),
    ("crontab", "persistence_attempt"),
    ("schtasks", "persistence_attempt"),
)
# Stems, not whole words: a leading boundary anchors the word, and the tail is left open so
# `exfiltrated` and `exfiltrating` are caught. Anchoring both ends missed both inflections — a false
# negative introduced while fixing the false positives, and caught only by testing the inflections.
_RISK_ALWAYS_TOKENS: tuple[tuple[str, str], ...] = (
    ("powershell -enc", "shell_injection_risk"),
    ("system32", "privileged_action"),
    ("exfiltrat", "exfiltration_hint"),
)
# A command sits at the start of the input, at the start of a line, or after a shell separator.
_COMMAND_POSITION = r"(?:^|\n|[;&|]\s*|`|\$\s*)\s*"
# A token followed by a copula, a negation or a question is being TALKED ABOUT, not invoked.
# "sudo is not installed on this box" begins with the word and is plainly not a command; the same
# holds for "we exfiltrate nothing". Every flag here hard-blocks the turn outright
# (core/agent_runtime/runtime_gate_policy.py), so a sentence discussing a dangerous thing must not
# be treated as doing it.
_TALKED_ABOUT_NOT_INVOKED = (
    r"(?!\s*(?:is|are|was|were|isn't|aren't|wasn't|means?|meant|does|did|can|could|should|would|"
    r"will|won't|nothing|none|no\b|never|only|just|command|flag|option|access|privileges?|rights?)\b)"
)
# A few command tokens are destructive only because of WHAT they are pointed at, and command
# position cannot separate the two readings because both put the word first.
#
# Measured on the served surface: "format the answer with tables and grpahs pls" classified
# `risky_system_action` with flag `destructive_command`. That flag is in the hard_block set
# (core/agent_runtime/runtime_gate_policy.py), so the turn was refused outright with
# `allowed_actions=[]` — a request about layout was answered as an attempt to erase a volume. The
# same session asked for tables five times and was silently refused every time.
#
# So `format` additionally requires a device or volume among the arguments that follow it. The
# shapes below are the ones that do not occur in English: a bare drive letter with a colon, a /dev
# node, or a kernel device name carrying a digit. `disk` alone is deliberately NOT one of them —
# "disk usage" is ordinary prose.
_DEVICE_ARGUMENT = re.compile(
    r"/dev/\S+"
    r"|\b[a-z]:(?=[\\/\s]|$)"
    r"|\bdisk\d+\w*"
    r"|\bsd[a-z]\d*\b"
    r"|\bnvme\d+\w*"
    r"|\bhd[a-z]\d*\b"
    r"|\bmmcblk\d+\w*"
)
# Plain-English destructive phrasing ("format my external drive") stays out of this predicate on
# purpose: it is classified exactly as "delete all my files" already is, and consent for that lives
# downstream. This table is only about a token being run as a command.
_REQUIRES_DEVICE_ARGUMENT: frozenset[str] = frozenset({"format"})
# How far after the token its arguments can be. Bounded because the device must belong to THIS
# command: without a bound, "format the answer as a table" followed anywhere later by a device would
# re-arm it.
#
# A line bound was tried first and is unreachable — `redact_text` collapses newlines to spaces
# before this predicate ever sees the text, so a whole message is one line here. A token window is
# the bound that actually holds. Six covers the longest real invocations (`format /q /fs:ntfs
# /v:data c:` is four), while the prose case that caused this fix reaches its device at token nine.
_ARGUMENT_TOKEN_WINDOW = 6


def _risk_flags_for(text: str) -> list[str]:
    """Risk flags for an input, judged by command shape rather than substring containment."""

    found: list[str] = []
    for token, flag in _RISK_COMMAND_TOKENS:
        # The boundary is decided from the STRIPPED token. Deciding it from the raw one made `del `
        # and `rm ` lose their trailing space to `.strip()` and their boundary to the space, so they
        # matched a bare `del` inside "delta between the two branches" — a hard block on a question
        # about git.
        word = token.strip()
        boundary = r"\b" if word[-1].isalnum() else ""
        pattern = _COMMAND_POSITION + re.escape(word) + boundary + _TALKED_ABOUT_NOT_INVOKED
        if flag in found:
            continue
        for match in re.finditer(pattern, text):
            if word in _REQUIRES_DEVICE_ARGUMENT:
                # The argument list runs to the next shell separator, and no further than the
                # window: past that, a device belongs to some other clause, not to this command.
                tail = re.split(r"[;&|]", text[match.end() :], maxsplit=1)[0]
                arguments = " ".join(tail.split()[:_ARGUMENT_TOKEN_WINDOW])
                if not _DEVICE_ARGUMENT.search(arguments):
                    continue
            found.append(flag)
            break
    for token, flag in _RISK_ALWAYS_TOKENS:
        # `\w*\b`, not `\w*`: the star must be forced to consume the whole word before the
        # talked-about guard applies. Without the trailing boundary it backtracks to the bare stem
        # and the guard is evaluated against "e nothing" instead of " nothing", so
        # "we exfiltrate nothing" reads as an invocation.
        base = (r"\b" + re.escape(token) + r"\w*\b") if token.isalnum() else re.escape(token)
        if re.search(base + _TALKED_ABOUT_NOT_INVOKED, text) and flag not in found:
            found.append(flag)
    return found


def _authoring_request_not_action(routing_input: str) -> bool:
    """True when the turn asks for the TEXT of an artifact rather than an action (MF-13)."""
    try:
        from core.instructional_request import asks_for_instructions_not_execution

        return asks_for_instructions_not_execution(routing_input)
    except Exception:
        return False


def _software_authoring_shape(text: str) -> bool:
    """Whether the turn asks the runtime to author or continue software (shared register)."""
    try:
        from core.agent_runtime.grounded_mode import is_software_authoring_request

        return is_software_authoring_request(text)
    except Exception:
        return False


def classify(user_input: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    # A typed chat JSON envelope has two independent authorities: ``task``/``intent`` says what
    # the user wants done, while ``format``/``constraints`` govern only the visible answer.  The
    # old substring classifier read the whole serialization, so "NO JSON" relabelled an ordinary
    # explanation as config work.  Route from the admitted task; arbitrary JSON and control-looking
    # siblings such as ``command`` remain ordinary user content.
    from core.raw_output_contract import parse_structured_user_directive

    structured_directive = parse_structured_user_directive(user_input)
    routing_input = (
        structured_directive.task_text if structured_directive is not None else user_input
    )
    from core.retrieval_constraints import analyze_retrieval_constraints

    raw_text = redact_text(routing_input).lower()
    # A prohibited operation is not positive task authority. Keep the complete
    # request for risk detection; classify only the existing parser's eligible work.
    # Authority-sensitive lookup checks below need raw_text: stripping their
    # prohibitions first would silently re-authorize the remaining lookup clause.
    text = analyze_retrieval_constraints(raw_text).eligible_text
    context = context or {}
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    reference_targets = {str(item).lower() for item in context.get("reference_targets") or []}
    understanding_confidence = float(context.get("understanding_confidence") or 0.0)
    quality_flags = {str(item).lower() for item in context.get("quality_flags") or []}
    source_context = dict(context.get("source_context") or {})
    has_workspace_audit_evidence = bool(source_context.get("workspace_audit_evidence_collected"))

    risk_flags: list[str] = []
    task_class = "unknown"
    confidence_hint = 0.35
    # C18 FINAL CORRECTION: a pasted command or test artifact is code CONTEXT, never action
    # AUTHORITY. "Explain this command: python -m pytest ..." and "Do not run this" carry the
    # same anchor as a genuine repair demand; routing debugging on the anchor alone made the
    # operator's own artifacts authorize execution. The anchor is no longer debugging or
    # code.task.open authority anywhere; bounded repo repair keeps its typed
    # mutation+verification meaning, and untranslated demands without typed action authority
    # fail closed (multilingual code-task execution stays PARTIAL for the C12 owner).
    bounded_repo_repair = looks_like_bounded_repo_repair_request(text)
    # A typed exact-literal output request is presentation, not configuration. JSON-looking user
    # directives necessarily contain the token "json" in constraints such as "No JSON wrapper";
    # letting that noun reach the substring branch below relabels a one-character answer as a
    # config action plan. The raw-output parser is the existing authority for whether the payload
    # is a real user directive and whether its literal is fully bound.
    exact_literal_output = False
    if str(user_input or "").lstrip().startswith("{"):
        try:
            from core.raw_output_contract import parse_raw_output_contract

            exact_literal_output = bool(
                (contract := parse_raw_output_contract(user_input)) is not None
                and contract.exact_text is not None
            )
        except Exception:
            exact_literal_output = False

    for flag in _risk_flags_for(raw_text):
        risk_flags.append(flag)

    if risk_flags:
        task_class = "risky_system_action"
        confidence_hint = 0.90
    elif has_workspace_audit_evidence:
        task_class = "workspace_audit"
        confidence_hint = 0.96
    elif bounded_repo_repair:
        task_class = "debugging"
        confidence_hint = 0.84
    elif exact_literal_output:
        task_class = "chat_conversation"
        confidence_hint = 1.0
    elif looks_like_assistant_identity_question(text):
        # First of the light-turn predicates, ahead of every keyword branch below: the words this
        # family is made of are the ones those branches match on -- "what is" (bare lookup),
        # "bot"/"agent"/"app" (system_design, integration), "setting" (config). The answer is a
        # local value, so the only thing a heavier lane can add to it is latency.
        task_class = "chat_conversation"
        confidence_hint = 0.93
    elif looks_like_direct_math_request(text):
        task_class = "chat_conversation"
        confidence_hint = 0.94
    elif looks_like_word_math_request(text):
        task_class = "chat_conversation"
        confidence_hint = 0.86
    elif looks_like_current_chat_recall(text):
        task_class = "chat_conversation"
        confidence_hint = 0.90
    elif looks_like_conversational_correction(text):
        # Ahead of every keyword branch below on purpose: a correction is made of the same words
        # they match on ("what is", "wrong"), and the lane it needs is the lightest one there is.
        task_class = "chat_conversation"
        confidence_hint = 0.88
    elif looks_like_bare_definitional_question(text):
        # Here for the same reason as the correction above, and it has to be here rather than beside
        # its sibling `looks_like_ordinary_conceptual_question` further down: the keyword branches in
        # between claim these turns first on a bare substring. "hey what is json?" was `config` --
        # and `config` is an `action_plan`, which resolves to the DEEP lane with an unbounded
        # fallback budget -- because the three-letter word "json" appears in it.
        task_class = "chat_conversation"
        confidence_hint = 0.82
    elif any(k in text for k in ["harden", "protect", "password", "passwords", "secret", "secrets", "leak", "leaks", "credential"]) or {"security", "security hardening", "password leak", "protect"} & topic_hints:
        task_class = "security_hardening"
        confidence_hint = 0.80
    elif any(k in text for k in ["swarm", "mesh", "replica", "replication", "shard", "presence", "knowledge"]) or {"knowledge shard", "swarm memory", "replica", "replication", "presence", "knowledge"} & topic_hints:
        task_class = "system_design"
        confidence_hint = 0.74
    elif any(k in text for k in ["traceback", "stack trace", "exception", "error", "bug", "fails", "broken"]):
        task_class = "debugging"
        confidence_hint = 0.75
    elif any(k in text for k in ["npm", "pip", "cargo", "brew", "dependency", "install", "module not found"]):
        task_class = "dependency_resolution"
        confidence_hint = 0.78
    elif any(k in text for k in ["config", "yaml", "json", ".env", "setting", "configure"]):
        task_class = "config"
        confidence_hint = 0.70
    elif looks_like_semantic_hive_request(text):
        task_class = "integration_orchestration"
        confidence_hint = 0.84
    elif (
        any(
            marker in text
            for marker in [
                "build",
                "design",
                "architecture",
                "plan",
                "best practice",
                "best practices",
                "framework",
                "stack",
                "github",
                "repo",
                "repos",
                "docs",
                "documentation",
            ]
        )
        and any(
            marker in text
            for marker in [
                "telegram",
                "discord",
                "bot",
                "api",
                "integration",
                "webhook",
                "agent",
            ]
        )
    ):
        task_class = "system_design"
        confidence_hint = 0.80
    elif (
        not looks_like_hypothetical_frame_turn(raw_text)
        and not looks_like_canonical_project_question(text)
        and (looks_like_live_recency_lookup(raw_text) or looks_like_explicit_lookup_request(raw_text))
    ):
        # Explicit fresh retrieval outranks topical vocabulary below. A weak setup such as
        # "Suppose I migrate soon" does not turn "look up the latest release notes online" into
        # advisory chat, and a noun such as "pricing" must not erase the requested lookup either.
        task_class = "research"
        confidence_hint = 0.78
    elif _software_authoring_shape(text):
        # A request to create, modify or continue SOURCE CODE is coding work, whatever English
        # words its specification contains. Placed ahead of the topical keyword families
        # because their lists read isolated words: measured live 2026-09-17, a standalone
        # "Create one JavaScript function called prepareTasks..." fell through to `unknown`,
        # and a continuation whose spec mentioned "active"/"relationship"-shaped words was
        # classified `relationship_advisory` -- sending a coding turn down an advisory lane.
        # The register is the SHARED software-authoring authority (same one the decomposition
        # guards use), so the class and the split decision cannot disagree about what coding is.
        task_class = "chat_conversation"
        confidence_hint = 0.80
    elif _contains_any(text, _BUSINESS_CHAT_MARKERS) or {"business", "pricing", "marketing", "sales"} & topic_hints:
        task_class = "business_advisory"
        confidence_hint = 0.62
    elif _contains_any(text, _FOOD_CHAT_MARKERS) or {"food", "nutrition", "meal", "diet"} & topic_hints:
        task_class = "food_nutrition"
        confidence_hint = 0.62
    elif _contains_any(text, _RELATIONSHIP_CHAT_MARKERS) or {"relationship", "dating", "intimacy"} & topic_hints:
        task_class = "relationship_advisory"
        confidence_hint = 0.62
    elif _contains_any(text, _CREATIVE_CHAT_MARKERS) or {"creative", "brainstorm", "campaign"} & topic_hints:
        task_class = "creative_ideation"
        confidence_hint = 0.62
    elif _contains_any(text, _GENERAL_ADVISORY_MARKERS):
        task_class = "general_advisory"
        confidence_hint = 0.58
    elif looks_like_hypothetical_frame_turn(raw_text):
        # Ahead of every lookup branch below, and of the "meeting"/"calendar" integration branch
        # further down that claimed the shorter paraphrases. A turn carrying its own premises has
        # nothing to look up: the facts are already in the message, and the answer is reasoning over
        # them. `chat_conversation` is the only class that buys neither the queen role, nor a paid
        # arm, nor the tool catalog `should_attempt_tool_intent` hands to `research`.
        #
        # It is deliberately BEHIND the substantive-work branches above (security, debugging,
        # dependency, config, system_design): "suppose the config is broken, how do I debug it?" is
        # real work with a rhetorical opener, and demoting it to chat would be this fix overreaching
        # in the same way the identity fast path did.
        task_class = "chat_conversation"
        confidence_hint = 0.84
    elif looks_like_live_recency_lookup(raw_text):
        task_class = "research"
        confidence_hint = 0.78
    elif looks_like_canonical_project_question(text):
        task_class = "chat_conversation"
        confidence_hint = 0.86
    elif looks_like_public_entity_lookup_request(raw_text) or looks_like_explicit_lookup_request(raw_text):
        task_class = "research"
        confidence_hint = 0.70
    elif looks_like_ordinary_conceptual_question(text):
        task_class = "chat_conversation"
        confidence_hint = 0.60
    elif _bare_lookup_marker_claims_this_turn(raw_text):
        task_class = "research"
        confidence_hint = 0.65
    elif _authoring_request_not_action(routing_input):
        # Asking for the TEXT of an artifact is authoring, not action (MF-13). Measured live
        # 2026-08-15: "write a bash one-liner that finds all `.log` files..." fell through to the
        # integration/shell branches below on the words "email"/"command", was routed
        # output_mode=tool_intent, and died at the approval gate with "I wasn't able to turn that
        # into a completed action"; "draft a polite email to David" was refused for lacking an
        # outbound channel nobody asked it to use. The deliverable is text, so the turn belongs to
        # the generative lanes; a turn that also says "and send it" is excluded by the
        # discriminator itself and still reaches the action branches.
        task_class = "chat_conversation"
        confidence_hint = 0.82
    elif any(
        k in text
        for k in [
            "calendar",
            "schedule",
            "meeting",
            "email",
            "inbox",
            "telegram",
            "discord",
            "openclaw",
            "integration",
            "webhook",
            "sync",
        ]
    ):
        task_class = "integration_orchestration"
        confidence_hint = 0.76
    elif any(k in text for k in ["read file", "inspect file", "open file", "check file"]):
        task_class = "file_inspection"
        confidence_hint = 0.68
    elif any(k in text for k in ["run ", "execute ", "command", "shell", "terminal"]):
        task_class = "shell_guidance"
        confidence_hint = 0.72

    if task_class == "unknown" and not _skip_model_classification_for_context(context):
        model_class = _classify_via_model(user_input)
        if model_class:
            task_class = model_class
            confidence_hint = 0.65

    if reference_targets:
        confidence_hint = min(0.92, confidence_hint + 0.04)
    if understanding_confidence:
        confidence_hint = min(confidence_hint, max(0.30, understanding_confidence))
    if "ambiguous_reference" in quality_flags:
        confidence_hint = min(confidence_hint, 0.42)

    return {
        "task_class": task_class,
        "risk_flags": sorted(set(risk_flags)),
        "confidence_hint": confidence_hint,
        "context_hints": list(context.keys()),
    }


def _skip_model_classification_for_context(context: dict[str, Any]) -> bool:
    if bool(context.get("skip_model_classification")) or bool(context.get("chat_surface")):
        return True
    source_context = context.get("source_context")
    source_context_dict = source_context if isinstance(source_context, dict) else {}
    source_surface = str(
        context.get("source_surface")
        or source_context_dict.get("surface")
        or ""
    ).strip().lower()
    source_platform = str(
        context.get("source_platform")
        or source_context_dict.get("platform")
        or ""
    ).strip().lower()
    return source_surface in _CHAT_TRUTH_SOURCE_SURFACES or source_platform in _CHAT_TRUTH_SOURCE_PLATFORMS


# The production task-class vocabulary lives in ONE shared module consumed by
# both classification (here) and capability discovery (capability_graph) —
# the same object, not a copy, so the two can never drift apart.
from core.task_class_vocabulary import VALID_TASK_CLASSES as _VALID_TASK_CLASSES


def _classify_via_model(user_input: str) -> str:
    """Ask the local LLM to classify user intent when regex fails."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""
    # The canonical LocalModelPolicy: disabled means no local classification call — return
    # the same empty verdict as an unreachable Ollama so the regex path stands on its own.
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return ""

    try:
        import requests as _req

        from core.hardware_tier import recommended_ollama_model
        from core.ollama_endpoint import ollama_base_url

        base_url = ollama_base_url()
        parsed = urlparse(base_url)
        host = str(parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        with socket.create_connection((host, port), timeout=0.25):
            pass

        class_list = ", ".join(sorted(_VALID_TASK_CLASSES))
        prompt = (
            f"Classify this user message into exactly one category.\n"
            f"Categories: {class_list}\n\n"
            f"User message: {user_input[:300]}\n\n"
            f"Reply with ONLY the category name, nothing else."
        )
        model = recommended_ollama_model()
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 20,
        }
        permit = seal_direct_provider_invocation(
            provider_id="ollama:task-classifier",
            model_id=model,
            operation="classification",
            payload=payload,
            request_id=(
                "task-classify-"
                + hashlib.sha256(
                    user_input[:300].encode("utf-8")
                ).hexdigest()
            ),
            max_output_tokens=20,
            header_names=("Content-Type",),
        )
        resp = _req.post(
            f"{base_url}/v1/chat/completions",
            json=permit.consume(),
            timeout=4,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip().lower()
        clean = raw.strip().strip("'\"`.").strip()
        if clean in _VALID_TASK_CLASSES:
            return clean
        for cls in _VALID_TASK_CLASSES:
            if cls in clean:
                return cls
    except Exception:
        pass
    return ""


def context_strategy(task_class: str, *, context: dict[str, Any] | None = None, user_input: str = "") -> dict[str, Any]:
    context = context or {}
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    lower = redact_text(user_input).lower()
    explicit_archive = any(
        marker in lower
        for marker in ("archive", "history", "older", "previous", "earlier", "receipt", "audit", "trace")
    )

    base = {
        "total_context_budget": 900,
        # Raised from 180 on 2026-08-01. Measured on an ordinary chat turn ("what files are in
        # this project?"): the bootstrap layer builds 14 items, 8 of them must_keep totalling 930
        # tokens. must_keep is a guarantee -- context_budgeter trims those items rather than
        # dropping them -- so at 180 the layer had to shred them to ~24 tokens each, and
        # bootstrap-conversation-safety got cut at the sentence break: it kept "Sensitive
        # conversation is allowed..." and lost "Do not confuse [it] with permission to take
        # action". A safety rule truncated into its own exception is worse than a missing one.
        # 380 is the first budget at which nothing safety-bearing is trimmed. It is also free:
        # every strategy row below already reserves total - relevant = 380 for bootstrap
        # (900-520, 680-300), so 200 tokens of the declared total were structurally unallocated.
        # No relevant_budget in any task class loses a token. Cost: +185 tokens of bootstrap per
        # turn (178 used before, 380 now), and the two persona essays -- bootstrap-self-knowledge
        # (586) and bootstrap-continuity (133) -- arrive capped at ~85 tokens instead of whole.
        "bootstrap_budget": 380,
        "relevant_budget": 520,
        "cold_budget": 0,
        # must_keep items are exempt from these gates (see core/context_budgeter.budget_layer), so
        # these now bound the OPTIONAL tail of each layer rather than its total size. Kept: the
        # bootstrap layer has 6 optional items and no reason to admit all of them.
        "max_bootstrap_items": 5,
        "max_relevant_items": 6,
        "max_cold_items": 2,
        "allow_swarm_metadata": False,
        "allow_swarm_fetch": False,
        "archive_dependent": False,
    }

    if task_class in {"shell_guidance", "file_inspection"}:
        base.update({"total_context_budget": 680, "relevant_budget": 300, "max_relevant_items": 4})
    elif task_class in {"system_design", "research", "integration_orchestration"}:
        base.update({"total_context_budget": 1100, "relevant_budget": 650, "max_relevant_items": 8, "allow_swarm_metadata": True})
    elif task_class == "security_hardening":
        base.update({"total_context_budget": 980, "relevant_budget": 560, "max_relevant_items": 6})
    elif task_class in {"dependency_resolution", "config", "debugging"}:
        base.update({"total_context_budget": 950, "relevant_budget": 560, "max_relevant_items": 7})

    if {"swarm", "mesh", "replication", "presence", "knowledge"} & topic_hints:
        base["allow_swarm_metadata"] = True

    if any(
        marker in lower
        for marker in (
            "from swarm",
            "from hive",
            "use swarm",
            "use hive",
            "consult swarm",
            "consult hive",
            "remote peers",
            "peer research",
            "swarm memory",
            "hive mind",
            "shared research",
        )
    ) or {"swarm memory", "knowledge shard", "hive mind", "public hive"} & topic_hints:
        base["allow_swarm_metadata"] = True
        base["allow_swarm_fetch"] = True

    if explicit_archive:
        base.update(
            {
                "cold_budget": max(120, int(base["cold_budget"])),
                "archive_dependent": True,
                "total_context_budget": max(int(base["total_context_budget"]), int(base["bootstrap_budget"]) + int(base["relevant_budget"]) + 120),
            }
        )

    return base


def curiosity_profile(task_class: str, *, context: dict[str, Any] | None = None, user_input: str = "") -> dict[str, Any]:
    context = context or {}
    lower = redact_text(user_input).lower()
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    interest_score = 0.30
    topic_kind = "general"

    if task_class in {"research", "system_design"}:
        interest_score += 0.22
        topic_kind = "technical"
    if any(token in lower for token in ("telegram", "discord", "bot", "api", "integration", "calendar", "email", "meeting", "schedule", "inbox")):
        interest_score += 0.18
        topic_kind = "integration"
    elif any(token in lower for token in ("design", "ux", "ui", "mobile app", "web app", "layout")):
        interest_score += 0.16
        topic_kind = "design"
    elif any(token in lower for token in ("news", "headline", "current events", "pulse", "today")):
        interest_score += 0.12
        topic_kind = "news"

    if {"telegram bot", "meet and greet", "swarm memory", "knowledge shard"} & topic_hints:
        interest_score += 0.10

    return {
        "interest_score": max(0.0, min(1.0, interest_score)),
        "topic_kind": topic_kind,
    }


def model_execution_profile(
    task_class: str,
    *,
    chat_surface: bool = False,
    planner_style_requested: bool = False,
) -> dict[str, Any]:
    mapping = {
        "dependency_resolution": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "debugging": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "config": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": False, "provider_role": "auto"},
        "security_hardening": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "system_design": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "integration_orchestration": {"task_kind": "action_plan", "output_mode": "action_plan", "allow_paid_fallback": True, "provider_role": "queen"},
        "research": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": True, "provider_role": "queen"},
        "file_inspection": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": False, "provider_role": "auto"},
        "shell_guidance": {"task_kind": "summarization", "output_mode": "summary_block", "allow_paid_fallback": False, "provider_role": "auto"},
        "workspace_audit": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": True, "provider_role": "queen"},
        "unknown": {"task_kind": "normalization_assist", "output_mode": "summary_block", "allow_paid_fallback": False, "provider_role": "auto"},
        "chat_conversation": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "chat_research": {"task_kind": "summarization", "output_mode": "plain_text", "allow_paid_fallback": True, "provider_role": "queen"},
        "general_advisory": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "business_advisory": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "food_nutrition": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "relationship_advisory": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
        "creative_ideation": {"task_kind": "normalization_assist", "output_mode": "plain_text", "allow_paid_fallback": False, "provider_role": "auto"},
    }
    normalized_task_class = str(task_class or "unknown").strip().lower() or "unknown"
    base_profile = dict(mapping.get(normalized_task_class, mapping["unknown"]))

    if chat_surface and normalized_task_class in _AI_FIRST_CHAT_DOMAIN_TASK_CLASSES:
        if planner_style_requested:
            return {
                "task_kind": "action_plan",
                "output_mode": "action_plan",
                "allow_paid_fallback": bool(base_profile.get("allow_paid_fallback", False)),
                "provider_role": str(base_profile.get("provider_role", "auto") or "auto"),
            }
        # The chat surface changes how an answer READS, never what the turn can DO.
        #
        # This branch used to force `normalization_assist` for every AI-first chat class, and
        # `research` is in that set. Measured 2026-08-05, session openclaw:20c336689d2a0ec3b35f:
        # an aviation question was classified `research`, routed here, and its whole 18-event trace
        # contains no search, no tool call and no fetch -- because `normalization_assist` declares
        # only {"format"} while `summarization` declares {"summarize"}. The turn accepted "use
        # authoritative sources and do not guess", retrieved nothing, and the model narrated
        # sources that were never fetched ("The GitHub link mentions the Boeing 737's dominance").
        # Driven directly, the search backend answers that question well -- six relevant hits,
        # zero GitHub -- so nothing was wrong with retrieval except that it never ran.
        #
        # The rule is derived from the capability table rather than from a list of exceptions: a
        # surface preference may not select a task kind whose capabilities are a strict subset of
        # the base kind's. `output_mode` still becomes plain_text, which is the whole point of the
        # override -- a conversational answer instead of a summary_block.
        from core.model_capabilities import TASK_KIND_TO_CAPABILITIES

        base_kind = str(base_profile.get("task_kind") or "normalization_assist")
        chat_kind = "normalization_assist"
        base_caps = set(TASK_KIND_TO_CAPABILITIES.get(base_kind, set()))
        chat_caps = set(TASK_KIND_TO_CAPABILITIES.get(chat_kind, set()))
        if base_caps - chat_caps:
            chat_kind = base_kind
        return {
            "task_kind": chat_kind,
            "output_mode": "plain_text",
            "allow_paid_fallback": bool(base_profile.get("allow_paid_fallback", False)),
            "provider_role": str(base_profile.get("provider_role", "auto") or "auto"),
        }

    return base_profile


def orchestration_role_for_task_class(task_class: str) -> str:
    normalized = str(task_class or "unknown").strip().lower() or "unknown"
    if normalized in {"system_design", "integration_orchestration"}:
        return "queen"
    if normalized in {"research", "chat_research"}:
        return "researcher"
    if normalized in {"debugging", "dependency_resolution", "config", "security_hardening"}:
        return "coder"
    if normalized in {"file_inspection", "shell_guidance"}:
        return "verifier"
    return "narrator"


def _marker_is_negated(lowered: str, marker: str) -> bool:
    """True when every occurrence of ``marker`` sits behind a negation in its own clause.

    C18 final correction: "do not change files" is a FORBIDDEN action, not a mutation demand —
    the operator's words withdraw the authority the marker would otherwise signal.
    """
    negations = (" do not ", " don't ", " dont ", " cannot ", " can't ", " cant ",
                 " never ", " without ", " stop ", " not ", " no ")
    start = 0
    found = False
    while True:
        idx = lowered.find(marker, start)
        if idx == -1:
            break
        found = True
        before = lowered[max(0, idx - 48):idx]
        if not any(b in before for b in (".", "?", "!", ";", "\n")):
            if any(token in f" {before} " for token in negations):
                return True
        start = idx + 1
    return not found


def looks_like_bounded_repo_repair_request(user_input: str) -> bool:
    lowered = re.sub(r"[^a-z0-9\-]+", " ", str(user_input or "").lower())
    lowered = f" {' '.join(lowered.split())} "
    has_mutation = any(
        marker in lowered and not _marker_is_negated(lowered, marker)
        for marker in (
            " replace ",
            " patch ",
            " apply patch ",
            " apply this patch ",
            " edit ",
            " change ",
            " fix ",
            " ```diff ",
            " ```patch ",
        )
    )
    if not has_mutation:
        return False
    return any(
        marker in lowered and not _marker_is_negated(lowered, marker)
        for marker in (
            " run tests ",
            " rerun tests ",
            " pytest ",
            " run lint ",
            " lint it ",
            " ruff check ",
            " check formatting ",
            " ruff format ",
        )
    )


def _looks_like_orchestrated_operator_request(user_input: str) -> bool:
    return looks_like_bounded_repo_repair_request(user_input)


def build_task_envelope_for_request(
    user_input: str,
    *,
    context: dict[str, Any] | None = None,
    task_id: str | None = None,
    parent_task_id: str = "",
    chat_surface: bool = False,
    planner_style_requested: bool = False,
) -> TaskEnvelopeV1:
    context = dict(context or {})
    if chat_surface:
        context["chat_surface"] = True
    classification = classify(user_input, context)
    bounded_repo_repair = looks_like_bounded_repo_repair_request(user_input)
    if bounded_repo_repair and not classification.get("risk_flags"):
        classification = {
            **classification,
            "task_class": "debugging",
        }
    routed_task_class = str(classification.get("task_class") or "unknown")
    if chat_surface:
        routed_task_class = chat_surface_execution_task_class(
            routed_task_class,
            user_input=user_input,
            context=context,
    )
    profile = model_execution_profile(
        routed_task_class,
        chat_surface=chat_surface,
        planner_style_requested=planner_style_requested,
    )
    role = orchestration_role_for_task_class(routed_task_class)
    if bounded_repo_repair and not classification.get("risk_flags"):
        role = "queen"
        profile = {
            **dict(profile),
            "provider_role": "queen",
        }
    if str(profile.get("task_kind") or "") == "action_plan" and role == "narrator":
        role = "queen"
    reused = _reused_procedure_inputs(task_class=routed_task_class, user_input=user_input)
    model_constraints = _routing_model_constraints(
        task_class=routed_task_class,
        role=role,
        profile=profile,
        context=context,
    )
    return build_task_envelope(
        task_id=task_id,
        parent_task_id=parent_task_id,
        role=role,
        goal=str(user_input or "").strip()[:600],
        inputs={
            "task_class": routed_task_class,
            "classification": classification,
            "task_kind": str(profile.get("task_kind") or ""),
            "output_mode": str(profile.get("output_mode") or ""),
            "routing_profile": dict(profile),
            "reused_procedure_ids": list(reused.get("reused_procedure_ids") or []),
            "reused_procedures": list(reused.get("reused_procedures") or []),
        },
        model_constraints=model_constraints,
        latency_budget=_latency_budget_for_request(
            task_class=routed_task_class,
            role=role,
        ),
        quality_target="high" if role in {"queen", "verifier", "researcher"} else "standard",
        required_receipts=("tool_receipt", "validation_result") if role in {"coder", "verifier"} else (),
        privacy_class=str(context.get("share_scope") or "local_only"),
    )


def _reused_procedure_inputs(*, task_class: str, user_input: str) -> dict[str, Any]:
    procedures = load_procedure_shards()
    if not procedures:
        return {}
    ranked = rank_reusable_procedures(
        task_class=str(task_class or "unknown").strip().lower() or "unknown",
        query_text=user_input,
        procedures=procedures,
        limit=3,
    )
    if not ranked:
        return {}
    return {
        "reused_procedure_ids": [shard.procedure_id for shard in ranked],
        "reused_procedures": [
            {
                "procedure_id": shard.procedure_id,
                "title": shard.title,
                "task_class": shard.task_class,
                "shareability": shard.shareability,
                "success_signal": shard.success_signal,
                "reuse_count": int(shard.reuse_count or 0),
                "verified_reuse_count": int(shard.verified_reuse_count or 0),
                # PB01 hook 1: the validated GUIDANCE rides with the counters — bounded by
                # core.learning_integration (procedures/steps/preconditions/chars), delivered to
                # the provider context at the router's system-prompt seam. Data, never policy:
                # these fields live in envelope INPUTS and the prompt block, never in
                # model_constraints or tool_permissions — and permission-language lines are
                # filtered here too (the pinned poison law: learned text is inert even as a
                # description).
                "steps": [
                    str(step)[:160]
                    for step in list(shard.steps)[:8]
                    if _inert_guidance_line(step)
                ][:6],
                "preconditions": [
                    str(pre)[:120]
                    for pre in list(shard.preconditions)[:6]
                    if _inert_guidance_line(pre)
                ][:4],
            }
            for shard in ranked
        ],
    }


def _routing_model_constraints(
    *,
    task_class: str,
    role: str,
    profile: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    privacy_class = str(context.get("share_scope") or "local_only").strip().lower() or "local_only"
    required_locality = "local" if privacy_class == "local_private" or role in {"coder", "verifier", "memory_clerk"} else ""
    preferred_locality = "local" if role in {"coder", "verifier", "memory_clerk"} else ""
    preferred_tool_support: list[str] = []
    if role == "researcher":
        preferred_tool_support.append("web_search")
    return {
        "routing_task_kind": str(profile.get("task_kind") or "").strip(),
        "routing_output_mode": str(profile.get("output_mode") or "").strip(),
        "allow_paid_fallback": bool(profile.get("allow_paid_fallback", False)),
        "preferred_provider_role": str(profile.get("provider_role") or "auto").strip() or "auto",
        "required_locality": required_locality,
        "preferred_locality": preferred_locality,
        "prefer_structured_output": str(profile.get("output_mode") or "plain_text") != "plain_text" or role in {"coder", "verifier", "queen"},
        "prefer_long_context": role in {"queen", "researcher"} or task_class in {"research", "chat_research", "system_design"},
        "prefer_code_complex": task_class in {"debugging", "dependency_resolution", "security_hardening", "integration_orchestration"},
        "preferred_tool_support": preferred_tool_support,
        "queue_pressure_strategy": "fail_closed" if required_locality else "degrade",
    }


def _latency_budget_for_request(*, task_class: str, role: str) -> str:
    if role in {"narrator", "verifier"}:
        return "low_latency"
    if role in {"queen", "researcher"} or task_class in {"research", "chat_research", "system_design", "integration_orchestration"}:
        return "deep"
    return "balanced"


def chat_surface_execution_task_class(
    task_class: str,
    *,
    user_input: str = "",
    context: dict[str, Any] | None = None,
) -> str:
    clean_task_class = str(task_class or "unknown").strip().lower() or "unknown"
    if clean_task_class in _PLAIN_TEXT_CHAT_TASK_CLASSES:
        return clean_task_class
    if clean_task_class == "unknown":
        return "chat_conversation"
    if clean_task_class == "research":
        return "chat_research"

    context = context or {}
    topic_hints = {str(item).lower() for item in context.get("topic_hints") or []}
    lower = redact_text(user_input).lower()
    if _contains_any(lower, _BUSINESS_CHAT_MARKERS) or {"business", "pricing", "marketing", "sales"} & topic_hints:
        return "business_advisory"
    if _contains_any(lower, _FOOD_CHAT_MARKERS) or {"food", "nutrition", "meal", "diet"} & topic_hints:
        return "food_nutrition"
    if _contains_any(lower, _RELATIONSHIP_CHAT_MARKERS) or {"relationship", "dating", "intimacy"} & topic_hints:
        return "relationship_advisory"
    if _contains_any(lower, _CREATIVE_CHAT_MARKERS) or {"creative", "brainstorm", "campaign"} & topic_hints:
        return "creative_ideation"
    if _contains_any(lower, _GENERAL_ADVISORY_MARKERS):
        return "general_advisory"
    return clean_task_class
