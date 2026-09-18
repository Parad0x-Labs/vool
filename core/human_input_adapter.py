from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.context_understanding import WorkingInterpretation, expand_unfinished_for_self
from core.followup_subject_continuity import correction_is_a_bare_refinement
from core.input_normalizer import NormalizationResult, normalize_user_text
from core.structured_literal_input import looks_like_structured_literal_input
from storage.dialogue_memory import (
    archive_dialogue_topic,
    get_dialogue_session,
    recent_dialogue_turns,
    record_dialogue_turn,
    session_lexicon,
    update_dialogue_session,
    upsert_lexicon_term,
)

_AMBIGUOUS_REFERENCE_RE = re.compile(r"\b(it|they|them|this|that|this one|that one|other one)\b", re.IGNORECASE)
# Match ordinals generically rather than by a fixed vocabulary. An ordinal this pattern does not
# recognise never reaches the resolver, so it is answered with full confidence against the wrong
# subject; matching broadly lets an out-of-range or unresolvable ordinal be flagged honestly.
_ORDINAL_REFERENCE_RE = re.compile(
    r"\b(?:the\s+)?(?P<ordinal>"
    r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|last|next"
    r"|\d{1,3}(?:st|nd|rd|th)"
    r")\s+(?:one|file|folder|item|result|entry|option)\b",
    re.IGNORECASE,
)
_ORDINAL_WORD_POSITIONS = {
    "first": 0,
    "second": 1,
    "third": 2,
    "fourth": 3,
    "fifth": 4,
    "sixth": 5,
    "seventh": 6,
    "eighth": 7,
    "ninth": 8,
    "tenth": 9,
}
_ORDINAL_DIGIT_RE = re.compile(r"^(?P<digits>\d{1,3})(?:st|nd|rd|th)$", re.IGNORECASE)
#: A COLLECTIVE answer to an offered choice: it selects every option rather than one of them, so
#: the ordinal path above cannot resolve it. Measured live on c6eed761 -- the assistant offered
#: "1. VW Passat vs Camry  2. Renault Laguna vs Avensis  3. Something else", the user answered
#: "both", and because "both" carries no pronoun, no ordinal and no correction opening, the resolver
#: returned NO targets. The turn reached the model with no subject at all and it answered about the
#: USDC x402 spend lane instead.
#:
#: Anchored to the whole utterance, so it fires only when the selection IS the turn. "both of these
#: boxes are on the same rack" is a statement that happens to contain the word and must not resolve.
#: "neither" is deliberately absent: it REJECTS the options, and resurrecting them as targets is the
#: contamination `_NEGATED_REFERENCE_RE` exists to prevent.
_COLLECTIVE_SELECTION_RE = re.compile(
    r"^\s*(?:both|all|either|any)"
    r"(?:\s+(?:of\s+)?(?:them|these|those|the\s+(?:two|three|options|above)))?"
    r"\s*(?:please|pls|thanks|thx)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)

_ENUMERATED_LIST_LOOKBACK_TURNS = 6
_ENUMERATED_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)(?P<item>.+?)\s*$")
_WORD_RE = re.compile(r"[a-z0-9_'\-]+")
_FOLLOWUP_RE = re.compile(
    r"\b(what do you mean|how so|why that|go on|continue|expand|tell me more|ok do that|okay do that|yes do that|do that|"
    r"can you sharpen|can you continue|can you unpack|explain that|and then|what about that|"
    r"(?:what|which)\s+(?:part|point|detail|aspect)\s+of\s+(?:that|this)\s+(?:explanation|answer|response)|"
    r"(?:what|which)\b[^.!?]{0,80}\b(?:you\s+(?:just\s+)?(?:said|gave|mentioned|explained|described)|"
    r"(?:there|above|earlier|before))\b|"
    r"(?:make|put|say|give|turn|rewrite|condense|shorten|summarize)\s+(?:that|this|it))\b",
    re.IGNORECASE,
)
_SCOPED_TOOL_OR_HISTORY_FOLLOWUP_RE = re.compile(
    r"^\s*(?:"
    r"what\s+was\s+my\s+(?:previous|last)\s+question"
    r"|use\s+(?:the\s+)?(?:previous|last)\s+(?:result|answer|output)"
    r"|(?:now\s+)?(?:edit|open|revert)\s+(?:it|that|(?:the|that)\s+(?:file|change|result))"
    r"|run\s+(?:the\s+)?tests?"
    r"|try\s+again"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_FENCED_CLASSIFIER_LITERAL_RE = re.compile(
    r"```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~",
    re.DOTALL,
)
_INLINE_CLASSIFIER_LITERAL_RE = re.compile(r"`[^`\n]*`")
_DOUBLE_QUOTED_CLASSIFIER_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)
_SINGLE_QUOTED_CLASSIFIER_LITERAL_RE = re.compile(
    r"(?<![\w])'(?:\\.|[^'\\])*'",
    re.DOTALL,
)
_BLOCK_QUOTED_CLASSIFIER_LITERAL_RE = re.compile(r"(?m)^\s*>.*$")
_SELF_CONTAINED_PREFERENCE_RE = re.compile(
    r"(?:\bfor\s+this\s+chat\b[^.!?]{0,120}\b(?:i|we)\s+prefer\b"
    # A correction commonly puts the remember request in a second sentence.
    # Keep it self-contained so a trailing "that" cannot resolve to an old
    # topic or import unrelated prior answer/format history.
    r"|\b(?:i|we)\s+prefer\b[^\n]{0,240}?\b(?:remember|keep|save|store)\b)",
    re.IGNORECASE,
)
_COMMITMENT_RE = re.compile(
    r"^(?:i(?:['’]ll| will| can| am going to|['’]m going to)\b|let me\b|next i can\b)",
    re.IGNORECASE,
)

_PHRASE_HINTS = [
    ("knowledge shard", ("knowledge", "shard")),
    ("swarm memory", ("swarm", "memory")),
    ("meet and greet", ("meet", "greet")),
    ("presence lease", ("presence", "lease")),
    ("fetch route", ("fetch", "route")),
    ("replica count", ("replica", "count")),
    ("security hardening", ("security", "harden")),
    ("password leak", ("password", "leak")),
    ("telegram bot", ("telegram", "bot")),
    ("calendar workflow", ("calendar",)),
    ("email workflow", ("email",)),
    ("discord integration", ("discord",)),
    ("openclaw integration", ("openclaw",)),
]

_TOPIC_TERMS = {
    "agent",
    "bot",
    "credit",
    "credits",
    "calendar",
    "daemon",
    "discord",
    "email",
    "fetch",
    "freshness",
    "helper",
    "identity",
    "inbox",
    "knowledge",
    "lease",
    "liquefy",
    "lore",
    "memory",
    "meeting",
    "mesh",
    "node",
    "openclaw",
    "onboarding",
    "password",
    "persona",
    "presence",
    "protect",
    "replica",
    "replication",
    "route",
    "schedule",
    "security",
    "server",
    "setup",
    "shard",
    "standalone",
    "swarm",
    "telegram",
    "timeout",
    "transport",
}
_CONTINUITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "by",
    "do",
    "explain",
    "for",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "make",
    "me",
    "my",
    "of",
    "on",
    "or",
    "run",
    "that",
    "the",
    "this",
    "to",
    "use",
    "we",
    "what",
    "why",
    "write",
    "you",
}


@dataclass
class HumanInputInterpretation:
    raw_text: str
    normalized_text: str
    reconstructed_text: str
    intent_mode: str
    topic_hints: list[str]
    reference_targets: list[str]
    understanding_confidence: float
    quality_flags: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    # ``None`` keeps direct construction by older callers behaviorally
    # compatible; real ingress always stamps an explicit boolean.
    is_continuation: bool | None = None
    # Typed state changes are handled by the runtime as metadata rather than
    # being mixed with conversational context.  This prevents a preference
    # correction from receiving or echoing unrelated bootstrap material.
    state_mutation: str | None = None
    turn_id: str | None = None
    working_interpretation: WorkingInterpretation | None = None
    # The USER'S OWN WORDS, separate from ``normalized_text`` when this interpretation was
    # built from a COMPOSED prompt. ``observation_prompt`` (core/agent_runtime/chat_surface.py)
    # builds model inputs as user text + "Grounding observations ..." + a JSON dump of what the
    # runtime observed; routing legitimately wants to read that composed text, but demand
    # predicates ("did THIS TURN ask for a heavy model?") must read what the person said, not
    # numbers inside the runtime's own scaffolding. Empty for interpretations built before the
    # split; readers treat empty as "the interpretation text IS the demand text".
    user_demand_text: str = ""

    def as_context(self) -> dict[str, object]:
        ctx = {
            "topic_hints": list(self.topic_hints),
            "reference_targets": list(self.reference_targets),
            "understanding_confidence": float(self.understanding_confidence),
            "quality_flags": list(self.quality_flags),
            "intent_mode": self.intent_mode,
            "normalized_text": self.normalized_text,
            "reconstructed_text": self.reconstructed_text,
            "needs_clarification": bool(self.needs_clarification),
            "conversation_continuation": bool(self.is_continuation),
            "state_mutation": self.state_mutation,
            "turn_id": self.turn_id,
            "interpretation_summary": self.interpretation_summary,
            "model_prompt_hints": {
                "ambiguity": "high" if self.understanding_confidence < 0.45 else "medium" if self.understanding_confidence < 0.7 else "low",
                "topic_count": len(self.topic_hints),
                "reference_count": len(self.reference_targets),
            },
        }
        if self.working_interpretation and self.working_interpretation.grounding_note:
            ctx["short_input_grounding_note"] = self.working_interpretation.grounding_note
        return ctx

    @property
    def interpretation_summary(self) -> str:
        topic = ", ".join(self.reference_targets or self.topic_hints[:2]) or "the current request"
        return f"{self.intent_mode} about {topic}"


def runtime_session_id(*, device: str, persona_id: str) -> str:
    safe_device = re.sub(r"[^a-zA-Z0-9_\-]+", "-", device).strip("-").lower() or "local"
    safe_persona = re.sub(r"[^a-zA-Z0-9_\-]+", "-", persona_id).strip("-").lower() or "default"
    return f"{safe_device}:{safe_persona}"


def learn_user_shorthand(term: str, canonical: str, *, session_id: str | None = None) -> None:
    upsert_lexicon_term(term, canonical, scope=session_id or "global", source="manual")


def _extract_topic_hints(text: str) -> list[str]:
    lower = text.lower()
    hints: list[str] = []
    for phrase, required in _PHRASE_HINTS:
        if all(token in lower for token in required):
            hints.append(phrase)
    for token in _WORD_RE.findall(lower):
        if token in _TOPIC_TERMS and token not in hints:
            hints.append(token)
        if len(hints) >= 8:
            break
    return hints[:8]


def _is_self_contained_preference_declaration(text: str) -> bool:
    """Recognize a preference declaration that owns its trailing pronoun."""
    return bool(_SELF_CONTAINED_PREFERENCE_RE.search(str(text or "")))


def _infer_intent_mode(text: str) -> str:
    lower = text.lower().strip()
    if lower.endswith("?") or re.match(r"^(what|why|how|can|should|will|would|is|are)\b", lower):
        return "question"
    if any(marker in lower for marker in ["please", "need", "want", "make", "create", "fix", "harden", "check"]):
        return "request"
    return "statement"


def _continuity_tokens(text: str) -> set[str]:
    return {
        token
        for token in _WORD_RE.findall(str(text or "").lower())
        if len(token) > 2 and token not in _CONTINUITY_STOPWORDS
    }


def continuation_classifier_text(text: str) -> str:
    """Return classifier-only prose with quoted/code/JSON literals neutralized.

    This is intentionally conservative rather than a parser for arbitrary programming languages.
    It never changes the text stored or sent to the model.  An unmatched fence discards the tail
    from classification so evidence cannot accidentally become broad-history authority.
    """

    classified = _FENCED_CLASSIFIER_LITERAL_RE.sub(" ", str(text or ""))
    for fence in ("```", "~~~"):
        unmatched = classified.find(fence)
        if unmatched >= 0:
            classified = classified[:unmatched]
    classified = _BLOCK_QUOTED_CLASSIFIER_LITERAL_RE.sub(" ", classified)
    classified = _INLINE_CLASSIFIER_LITERAL_RE.sub(" ", classified)
    classified = _DOUBLE_QUOTED_CLASSIFIER_LITERAL_RE.sub(" ", classified)
    classified = _SINGLE_QUOTED_CLASSIFIER_LITERAL_RE.sub(" ", classified)
    return " ".join(classified.split()).strip()


def _has_continuity_overlap(left: str, right: str) -> bool:
    left_tokens = _continuity_tokens(left)
    right_tokens = _continuity_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens & right_tokens)


def _unique_strings(values: list[str], *, limit: int = 4, max_chars: int = 180) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        clean = " ".join(str(item or "").split()).strip().rstrip(".!?")
        if not clean:
            continue
        lowered = clean.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(clean[:max_chars])
        if len(normalized) >= limit:
            break
    return normalized


def _sentence_fragments(text: str) -> list[str]:
    return [fragment.strip() for fragment in re.split(r"(?:\n+|(?<=[.!?])\s+)", str(text or "")) if fragment.strip()]


def _extract_assistant_commitments(text: str) -> list[str]:
    commitments: list[str] = []
    for fragment in _sentence_fragments(text):
        if not _COMMITMENT_RE.search(fragment):
            continue
        commitment = _COMMITMENT_RE.sub("", fragment, count=1).strip(" :-")
        if len(commitment) < 6:
            continue
        commitments.append(commitment)
    return _unique_strings(commitments, limit=4)


def _latest_assistant_commitments(recent_turns: list[dict[str, object]]) -> list[str]:
    for turn in recent_turns:
        if str(turn.get("speaker_role") or "").strip().lower() != "assistant":
            continue
        commitments = _extract_assistant_commitments(str(turn.get("reconstructed_input") or turn.get("raw_input") or ""))
        if commitments:
            return commitments
    return []


def _is_followup_continuation(text: str, *, reference_targets: list[str]) -> bool:
    lower = str(text or "").strip().lower()
    if reference_targets:
        return True
    if _FOLLOWUP_RE.search(lower):
        return True
    if _SCOPED_TOOL_OR_HISTORY_FOLLOWUP_RE.search(lower):
        return True
    return lower in {"yes", "yeah", "yep", "ok", "okay", "do that", "go on", "continue"}


def _infer_stance_and_emotional_tone(text: str, *, intent_mode: str) -> tuple[str | None, str | None]:
    lower = str(text or "").strip().lower()
    stance: str | None = None
    emotional_tone: str | None = None

    if any(marker in lower for marker in ["fucking", "stuck", "annoyed", "frustrated", "wtf", "hate", "broken", "tired of"]):
        emotional_tone = "frustrated"
    elif any(marker in lower for marker in ["urgent", "asap", "right now", "now", "immediately"]):
        emotional_tone = "urgent"
    elif intent_mode == "question":
        emotional_tone = "curious"

    if any(marker in lower for marker in ["not convinced", "skeptical", "doubt", "are you sure", "really", "actually"]):
        stance = "skeptical"
    elif any(marker in lower for marker in ["need", "must", "want", "help me", "decide", "deciding", "fix", "make", "build"]):
        stance = "goal_driven"
    elif intent_mode == "question":
        stance = "exploratory"

    return stance, emotional_tone


def _derive_continuity_state(
    *,
    normalized_text: str,
    classifier_text: str,
    classifier_topics: list[str],
    classifier_reference_targets: list[str],
    current_topics: list[str],
    reference_targets: list[str],
    intent_mode: str,
    session_state: dict[str, object],
    recent_turns: list[dict[str, object]],
) -> dict[str, object]:
    session_topics = [str(item) for item in list(session_state.get("topic_hints") or []) if str(item or "").strip()]
    last_subject = str(session_state.get("last_subject") or "").strip()
    existing_goal = str(session_state.get("current_user_goal") or "").strip()
    existing_commitments = [str(item) for item in list(session_state.get("assistant_commitments") or []) if str(item or "").strip()]
    existing_unresolved = [str(item) for item in list(session_state.get("unresolved_followups") or []) if str(item or "").strip()]
    existing_stance = str(session_state.get("user_stance") or "").strip() or None
    existing_emotional_tone = str(session_state.get("emotional_tone") or "").strip() or None

    followup_like = _is_followup_continuation(
        classifier_text,
        reference_targets=classifier_reference_targets,
    )
    topical_overlap = bool(
        {
            item.lower()
            for item in classifier_topics + classifier_reference_targets
        }
        & {item.lower() for item in session_topics}
    )
    lexical_overlap = any(
        _has_continuity_overlap(classifier_text, candidate)
        for candidate in [existing_goal, last_subject, *existing_commitments, *existing_unresolved]
        if candidate
    )
    same_thread = followup_like or topical_overlap or lexical_overlap

    next_subject = (current_topics or reference_targets or ([last_subject] if same_thread and last_subject else []) or [None])[0]
    if same_thread:
        merged_topics = list(dict.fromkeys(current_topics + reference_targets + session_topics))[:8]
    else:
        merged_topics = list(dict.fromkeys(current_topics + reference_targets))[:8]

    if same_thread and (followup_like or not _continuity_tokens(classifier_text)) and existing_goal:
        current_user_goal = existing_goal
    else:
        current_user_goal = " ".join(str(normalized_text or "").split()).strip()[:240] or existing_goal or None

    latest_commitments = _latest_assistant_commitments(recent_turns)
    if same_thread:
        assistant_commitments = _unique_strings(latest_commitments or existing_commitments)
        unresolved_followups = _unique_strings(assistant_commitments or existing_unresolved)
    else:
        assistant_commitments = []
        unresolved_followups = []

    current_stance, current_emotional_tone = _infer_stance_and_emotional_tone(
        normalized_text,
        intent_mode=intent_mode,
    )
    if same_thread:
        user_stance = current_stance or existing_stance
        emotional_tone = current_emotional_tone or existing_emotional_tone
    else:
        user_stance = current_stance
        emotional_tone = current_emotional_tone

    return {
        "same_thread": same_thread,
        "next_subject": str(next_subject) if next_subject else None,
        "merged_topics": merged_topics,
        "current_user_goal": current_user_goal,
        "assistant_commitments": assistant_commitments,
        "unresolved_followups": unresolved_followups,
        "user_stance": user_stance,
        "emotional_tone": emotional_tone,
    }


def _archive_topic_shift_if_needed(
    *,
    session_id: str,
    previous_session_state: dict[str, object],
    continuity: dict[str, object],
    closing_user_input: str,
) -> None:
    if bool(continuity.get("same_thread")):
        return
    last_subject = str(previous_session_state.get("last_subject") or "").strip() or None
    topic_hints = [str(item).strip() for item in list(previous_session_state.get("topic_hints") or []) if str(item).strip()]
    current_user_goal = str(previous_session_state.get("current_user_goal") or "").strip() or None
    assistant_commitments = [str(item).strip() for item in list(previous_session_state.get("assistant_commitments") or []) if str(item).strip()]
    unresolved_followups = [str(item).strip() for item in list(previous_session_state.get("unresolved_followups") or []) if str(item).strip()]
    if not any([last_subject, topic_hints, current_user_goal, assistant_commitments, unresolved_followups]):
        return
    archive_dialogue_topic(
        session_id,
        last_subject=last_subject,
        topic_hints=topic_hints,
        current_user_goal=current_user_goal,
        assistant_commitments=assistant_commitments,
        unresolved_followups=unresolved_followups,
        closure_status="unresolved" if (assistant_commitments or unresolved_followups) else "resolved",
        closure_reason="topic_shift",
        closing_user_input=closing_user_input,
    )


# A NEGATED reference ("not for this", "not that one") explicitly rejects the prior
# subject rather than referring to it, so it must never resurrect the session's last
# subject. This is the exact cross-turn contamination shape ("i'm asking for screen,
# NOT for this" inherited a stale "telegram" subject). Positive anaphora is unaffected.
_NEGATED_REFERENCE_RE = re.compile(
    r"\b(?:not|no|n't)\b(?:\s+\w+){0,2}\s+(?:this|that|it|them|those|these|one|ones)\b",
    re.IGNORECASE,
)


def _latest_enumerated_items(context_turns: list[dict[str, object]]) -> list[str]:
    # Bounded to roughly the pronoun path's lookback. Searching further back resolves an ordinal
    # against a list the user has long since moved on from, which answers confidently about the
    # wrong item -- worse than declining, because nothing signals the mismatch.
    for turn in context_turns[:_ENUMERATED_LIST_LOOKBACK_TURNS]:
        if str(turn.get("speaker_role") or "").strip().lower() != "assistant":
            continue
        text = str(turn.get("raw_input") or turn.get("reconstructed_input") or "")
        items: list[str] = []
        for line in text.splitlines():
            match = _ENUMERATED_ITEM_RE.match(line)
            if not match:
                continue
            item = " ".join(match.group("item").split()).strip()
            if item:
                items.append(item[:240])
        if items:
            return items
    return []


def _ordinal_item_index(
    ordinal: str,
    *,
    items: list[str],
    context_turns: list[dict[str, object]],
) -> int | None:
    fixed_positions = {**_ORDINAL_WORD_POSITIONS, "last": len(items) - 1}
    normalized = str(ordinal or "").strip().lower()
    if normalized in fixed_positions:
        return fixed_positions[normalized]
    digit_match = _ORDINAL_DIGIT_RE.match(normalized)
    if digit_match is not None:
        # Out-of-range indices are returned as-is so the caller flags them rather than clamping
        # to a neighbouring item and answering about the wrong one.
        return int(digit_match.group("digits")) - 1
    if normalized != "next":
        return None

    for turn in context_turns:
        if str(turn.get("speaker_role") or "").strip().lower() != "user":
            continue
        prior_match = _ORDINAL_REFERENCE_RE.search(str(turn.get("normalized_input") or turn.get("raw_input") or ""))
        if not prior_match:
            continue
        for target in list(turn.get("reference_targets") or []):
            normalized_target = str(target or "").strip()
            if normalized_target in items:
                return items.index(normalized_target) + 1
        prior_ordinal = prior_match.group("ordinal").lower()
        if prior_ordinal in fixed_positions:
            return fixed_positions[prior_ordinal] + 1
    return None


def _resolve_ordinal_target(
    match: re.Match[str],
    *,
    context_turns: list[dict[str, object]],
) -> list[str]:
    items = _latest_enumerated_items(context_turns)
    if not items:
        return []
    index = _ordinal_item_index(match.group("ordinal"), items=items, context_turns=context_turns)
    if index is None or index < 0 or index >= len(items):
        return []
    return [items[index]]


def _resolve_reference_targets(
    normalized_text: str,
    *,
    current_topics: list[str],
    session_state: dict[str, object],
    recent_turns: list[dict[str, object]],
    context_turns: list[dict[str, object]] | None = None,
) -> tuple[list[str], list[str]]:
    quality_flags: list[str] = []
    ordinal_match = _ORDINAL_REFERENCE_RE.search(normalized_text)
    # A bare correction is a reference to the prior turn even when it contains no pronoun.
    # "I mean construction" and "no i mean real castle" both point at what was just discussed, and
    # before this the resolver returned nothing for them because neither says "it" or "that" -- so
    # a correction arrived at the model carrying no subject at all.
    #
    # BARE is what widens the gate, not "is a correction". A correction that heads a new
    # instruction -- "Actually, help me write the deployment script instead." -- must keep
    # replacing the current goal rather than reading as a continuation of the old one, because
    # `_derive_continuity_state` treats any turn with reference targets as `followup_like` and
    # holds `existing_goal`. Measured: gating on the correction alone broke exactly that, in
    # tests/gauntlet/test_context_continuity.py and tests/pa_beta_gate/
    # test_context_continuity_matrix.py.
    #
    # Only the ENTRY gate widens: everything below is the existing precedence, so a correction that
    # names its own subject still wins outright over stale session state, and an explicit "not
    # this" still redirects away.
    opens_a_correction = correction_is_a_bare_refinement(normalized_text)
    # A collective answer to an offered choice selects EVERY option, so the ordinal branch cannot
    # resolve it and none of the three gates above sees it either. See `_COLLECTIVE_SELECTION_RE`
    # for the measured failure this admits.
    collective_selection = bool(_COLLECTIVE_SELECTION_RE.match(normalized_text))
    if (
        not _AMBIGUOUS_REFERENCE_RE.search(normalized_text)
        and ordinal_match is None
        and not opens_a_correction
        and not collective_selection
    ):
        return [], quality_flags

    if collective_selection:
        # Bind to the whole offered list, in the order it was offered. Declining when there is no
        # list is the point: a bare "both" with nothing to select from is genuinely ambiguous, and
        # flagging it is what lets the turn ask rather than invent a subject.
        offered = _latest_enumerated_items(list(context_turns or []))
        if offered:
            return offered[:3], quality_flags
        quality_flags.append("ambiguous_reference")
        return [], quality_flags

    if ordinal_match is not None:
        if _NEGATED_REFERENCE_RE.search(normalized_text):
            quality_flags.append("ambiguous_reference")
            return [], quality_flags
        ordinal_targets = _resolve_ordinal_target(
            ordinal_match,
            context_turns=list(context_turns or []),
        )
        if ordinal_targets:
            return ordinal_targets, quality_flags
        quality_flags.append("ambiguous_reference")
        return [], quality_flags

    # The current turn's OWN subject always wins over stale session state.
    if current_topics:
        return current_topics[:3], quality_flags

    # A negated reference redirects AWAY from the prior subject; never resurrect it.
    if _NEGATED_REFERENCE_RE.search(normalized_text):
        quality_flags.append("ambiguous_reference")
        return [], quality_flags

    targets: list[str] = []
    last_subject = str(session_state.get("last_subject") or "").strip()
    if last_subject:
        targets.append(last_subject)
    for turn in recent_turns:
        for hint in turn.get("topic_hints") or []:
            if hint not in targets:
                targets.append(str(hint))
            if len(targets) >= 3:
                break
        if len(targets) >= 3:
            break

    if not targets:
        quality_flags.append("ambiguous_reference")
    return targets[:3], quality_flags


def _score_understanding(
    normalized: NormalizationResult,
    *,
    current_topics: list[str],
    reference_targets: list[str],
    reference_flags: list[str],
) -> float:
    score = 0.58
    if current_topics:
        score += 0.14
    if reference_targets:
        score += 0.10
    if normalized.replacements:
        score += 0.04
    if "fragmented" in normalized.quality_flags:
        score -= 0.08
    if "short_input" in normalized.quality_flags:
        score -= 0.06
    if "typo_heavy" in normalized.quality_flags:
        score -= 0.05
    if "shorthand_heavy" in normalized.quality_flags:
        score -= 0.03
    if "ambiguous_reference" in reference_flags:
        score -= 0.18
    return max(0.20, min(0.95, score))


def adapt_user_input(
    user_input: str,
    *,
    session_id: str,
    persist: bool = True,
    turn_id: str = "",
    record_user_turn: bool = True,
    user_demand_text: str = "",
) -> HumanInputInterpretation:
    """Interpret one utterance; by default, RECORD it as the session's newest turn.

    `persist=False` derives the same interpretation and writes nothing. It exists because two call
    sites need an interpretation of a composed PROMPT -- the user's text plus grounding observations
    plus a JSON dump of what the runtime found -- to size context and route the model call, and this
    function is not a pure function of its argument. It records a dialogue turn with its argument as
    `raw_input`, derives `current_user_goal` from it, and captures active-mission slots from it.

    Measured on this branch before the flag existed, three real turns through /api/chat:

        U: which local model should i run, qwen3 8b or llama 3.1 8b
        A: <an offer of three numbered options>
        U: yes ok my bad, do compare those models

    left `current_user_goal` reading

        'yes ok my bad, do compare those models Grounding observations for this turn. Use them as
         evidence, not as a template:{"actions_taken":["initial_search","stop_answer"],
         "channel": "adaptive_research", ...'

    which `bootstrap_context` then injects into later turns as "Current user goal: ...". The
    conversation's record of what the person wants was the runtime's own scaffolding.

    The split is deliberately at PERSISTENCE and not at the text: routing and context selection may
    legitimately want an interpretation that reflects the evidence, and taking that away would be a
    behaviour change dressed up as a bug fix. What may never happen is a prompt writing the
    conversation's memory of what the user said.
    """
    session = get_dialogue_session(session_id)
    lexicon = session_lexicon(session_id)
    normalized = normalize_user_text(user_input, session_lexicon=lexicon)
    turns = recent_dialogue_turns(session_id, limit=3)
    continuity_turns = recent_dialogue_turns(session_id, limit=8, speaker_roles=("user", "assistant"))
    structured_literal = looks_like_structured_literal_input(normalized.normalized_text)
    self_contained_preference = _is_self_contained_preference_declaration(
        normalized.normalized_text
    )
    if structured_literal:
        current_topics = []
        reference_targets = []
        reference_flags = []
    elif self_contained_preference:
        current_topics = ["preference"]
        reference_targets = []
        reference_flags = []
    else:
        current_topics = _extract_topic_hints(normalized.normalized_text)
        reference_targets, reference_flags = _resolve_reference_targets(
            normalized.normalized_text,
            current_topics=current_topics,
            session_state=session,
            recent_turns=turns,
            context_turns=continuity_turns,
        )
    classifier_text = (
        ""
        if structured_literal
        else continuation_classifier_text(normalized.normalized_text)
    )
    if not classifier_text or self_contained_preference:
        classifier_topics: list[str] = []
        classifier_reference_targets: list[str] = []
    else:
        classifier_topics = _extract_topic_hints(classifier_text)
        classifier_reference_targets, _classifier_reference_flags = _resolve_reference_targets(
            classifier_text,
            current_topics=classifier_topics,
            session_state=session,
            recent_turns=turns,
            context_turns=continuity_turns,
        )
    intent_mode = _infer_intent_mode(normalized.normalized_text)
    quality_flags = list(dict.fromkeys(list(normalized.quality_flags) + reference_flags))
    confidence = _score_understanding(
        normalized,
        current_topics=current_topics,
        reference_targets=reference_targets,
        reference_flags=reference_flags,
    )
    needs_clarification = confidence < 0.45 or "ambiguous_reference" in reference_flags

    working = expand_unfinished_for_self(
        user_input,
        normalized_text=normalized.normalized_text,
        topic_hints=current_topics,
        reference_targets=reference_targets,
        recent_turns=turns,
        quality_flags=quality_flags,
    )
    # Reference resolution stays typed.  It must never be appended to the user text because
    # downstream routing, retrieval, and providers cannot distinguish that generated suffix from
    # something the user actually said.  `reference_targets` remains available in `as_context()`
    # and bootstrap context for the current turn.
    reconstructed = normalized.normalized_text

    # ARCH-TRUTH-R1c: `turn_id` is the turn's canonical identity, minted once at the
    # run_once ingress and carried on its TurnRequest. Passing it through means the
    # persisted dialogue turn IS that turn rather than a second identity minted here.
    # ARCH-TRUTH-R1d: `record_user_turn=False` interprets and updates the session's typed
    # continuity state WITHOUT filing a second thing the user said. One external turn is
    # one user dialogue row; a mid-turn re-reading (the resume/retry re-interpretation of
    # a restored request) is internal work, and the id it carries forward is the turn's
    # canonical one rather than a new row's.
    turn_id = (
        record_dialogue_turn(
            session_id,
            turn_id=turn_id,
            raw_input=user_input,
            normalized_input=normalized.normalized_text,
            # ``reconstructed`` is internal routing assistance.  It can carry resolved
            # references, so it must never be persisted as if the user authored it.
            reconstructed_input=normalized.normalized_text,
            topic_hints=current_topics,
            reference_targets=reference_targets,
            understanding_confidence=confidence,
            quality_flags=quality_flags,
        )
        if persist and record_user_turn
        else str(turn_id or "")
    )
    continuity = _derive_continuity_state(
        normalized_text=normalized.normalized_text,
        classifier_text=classifier_text,
        classifier_topics=classifier_topics,
        classifier_reference_targets=classifier_reference_targets,
        current_topics=current_topics,
        reference_targets=reference_targets,
        intent_mode=intent_mode,
        session_state=session,
        recent_turns=continuity_turns,
    )
    if persist:
        _archive_topic_shift_if_needed(
            session_id=session_id,
            previous_session_state=session,
            continuity=continuity,
            closing_user_input=normalized.normalized_text,
        )
        update_dialogue_session(
            session_id,
            last_subject=str(continuity.get("next_subject") or "") or None,
            topic_hints=list(continuity.get("merged_topics") or []),
            last_intent_mode=intent_mode,
            current_user_goal=str(continuity.get("current_user_goal") or "") or None,
            assistant_commitments=list(continuity.get("assistant_commitments") or []),
            unresolved_followups=list(continuity.get("unresolved_followups") or []),
            user_stance=str(continuity.get("user_stance") or "") or None,
            emotional_tone=str(continuity.get("emotional_tone") or "") or None,
        )

        # Capture typed latest-wins mission slots from the RAW text (exact values), so
        # mission constraints survive distraction turns instead of living only in the
        # free-text goal. Fail-safe: never break a turn on capture.
        try:
            from core.active_mission import capture_active_mission_slots

            capture_active_mission_slots(session_id, user_input, turn_id=turn_id)
        except Exception:
            pass

    return HumanInputInterpretation(
        raw_text=user_input,
        normalized_text=normalized.normalized_text,
        reconstructed_text=reconstructed,
        intent_mode=intent_mode,
        topic_hints=current_topics,
        reference_targets=reference_targets,
        understanding_confidence=confidence,
        quality_flags=quality_flags,
        needs_clarification=needs_clarification,
        is_continuation=bool(continuity.get("same_thread")),
        state_mutation="preference" if self_contained_preference else None,
        turn_id=turn_id,
        working_interpretation=working,
        user_demand_text=str(user_demand_text or "").strip() or str(user_input or ""),
    )
