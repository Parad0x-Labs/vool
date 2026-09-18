"""A correction made three times should stop needing to be made a fourth.

THE GAP THIS CLOSES. VOOL's memory record is already richer than what the competition stores --
it carries `authority`, `confidence`, `status`, `expires_at`, `review_after`,
`superseded_record_id` and `last_confirmed_at`. What it has never had is a reason to treat a
REPEATED correction as stronger than a one-off remark. Repetition is the operator saying the same
thing again because the runtime did not act on it the first time, and nothing counted it.

WHAT MAKES THIS DIFFERENT FROM HERMES AND OPENCLAW (researched live 2026-08-15, not recalled):

  * OpenClaw appends to `MEMORY.md` and daily files, rarely consolidates, silently truncates at
    ~20K characters (openclaw#45415), and its own tracker calls memory management "in chaos"
    (openclaw#43747). Nothing there distinguishes a thing said once from a thing said weekly.

  * Hermes runs a background self-improvement review that "may quietly save a memory or update a
    skill". A background writer with no per-turn attribution is exactly the shape shown to enable
    SILENT MEMORY POLLUTION (arXiv 2603.23064), where the paper finds pre-compaction flushes,
    heartbeat prompts and ordinary access controls all insufficient, and asks instead for audit
    logging of every write.

So the primitive here is not storage, it is ATTRIBUTION:

    evidence is a set of DISTINCT TURN IDS, never a counter

A counter can be inflated by a background job re-saving the same conclusion -- which is how a
self-improvement loop manufactures its own confidence. A set of turn ids cannot: two saves of one
turn are one piece of evidence, and every piece can be replayed against the exchange that produced
it. An entry that cannot name the turn that taught it is invalid by construction, which closes the
pollution class structurally rather than by a heartbeat an attacker can simply outwait.

DELIBERATELY NOT HERE: applying a promoted instruction. This module measures, attributes and
PROPOSES. Changing the assistant's behaviour because a threshold was crossed is a consent decision
for the operator, not an engineering default -- see docs/MEMORY_SELF_LEARNING_DESIGN.md §7.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

__all__ = [
    "DEFAULT_EVIDENCE_BUDGET",
    "DEFAULT_STALE_AFTER_DAYS",
    "PROMOTION_THRESHOLD",
    "BudgetOutcome",
    "CorrectionEvidence",
    "accumulate_correction",
    "correction_directive",
    "enforce_evidence_budget",
    "promotion_proposals",
    "retirement_proposals",
]

#: Distinct turns that must carry the same directive before it is PROPOSED as an instruction.
#: Two is a coincidence and one is a remark; three is the operator having repeated themselves twice
#: because nothing changed. Deliberately a proposal at this point, never an application.
PROMOTION_THRESHOLD = 3

#: How many distinct directives are carried before the budget bites. A cap is not the interesting
#: part -- OpenClaw has one too, at ~20K characters. The interesting part is that theirs truncates
#: SILENTLY with no warning (openclaw#45415), so the operator discovers the loss by noticing the
#: agent forgot something. A bound that cannot be observed is indistinguishable from data loss.
DEFAULT_EVIDENCE_BUDGET = 200

#: How long a directive stands without being asked for again before retirement is PROPOSED.
#: Not a deletion: an instruction the operator still wants is re-confirmed the next time they ask
#: for it, and one they have moved on from stops costing context. This is the answer to bloat that
#: does not require anyone to garden a file by hand -- OpenClaw's own tracker records entries
#: accumulating with no consolidation (openclaw#43747).
DEFAULT_STALE_AFTER_DAYS = 90

#: The turn is telling the runtime how to BEHAVE, not stating a fact about the world. Each cue is a
#: correction of conduct -- a complaint that something was done wrong, or a standing rule about how
#: to do it next time. Anchored at a clause start so "I told him to stop" stays a description.
_CORRECTION_CUE = re.compile(
    r"(?:^|[.!?;]\s*|,\s*(?=(?:please|always|never|stop|dont|don't)\b))\s*"
    r"(?:"
    r"(?:i\s+(?:already\s+|just\s+)?(?:told|asked|said)\s+you)"
    r"|(?:i\s+keep\s+(?:telling|asking|saying))"
    r"|(?:how\s+many\s+times)"
    r"|(?:stop\s+(?:doing|using|adding|writing|putting|making))"
    r"|(?:(?:please\s+)?(?:always|never)\s+\w+)"
    r"|(?:don'?t\s+(?:ever\s+)?\w+)"
    r"|(?:no,?\s+(?:i\s+(?:said|meant|want)|use|do|make|write|format|give))"
    r"|(?:from\s+now\s+on)"
    r"|(?:in\s+future|in\s+the\s+future)"
    r"|(?:every\s+time\s+i\s+ask)"
    r"|(?:when\s+i\s+ask\s+for\s+\w+)"
    r")",
    re.IGNORECASE,
)

#: "give me the short version ALWAYS" states the same standing rule as "ALWAYS give me the short
#: version", and the clause-anchored cue above only ever saw the leading form -- measured, three of
#: seven natural phrasings were missed, so the repetition they were meant to prove never counted.
#: Anchored to the END of a clause, which is what keeps "the build always fails on tuesdays"
#: (always mid-sentence, followed by a verb) from reading as an instruction.
_TRAILING_RULE_CUE = re.compile(
    r"\b(?:always|never)\b\s*[,.!?]*\s*(?:please|pls|thanks|thx|ty|ok|okay)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)

#: Words that carry no discriminating power in a directive key. Kept small on purpose: an
#: over-eager stop list collapses distinct directives onto one key and merges unrelated evidence.
_KEY_NOISE = frozenset(
    {
        "a", "an", "the", "to", "of", "for", "me", "my", "you", "your", "i", "it", "that", "this",
        "and", "or", "but", "so", "please", "pls", "just", "always", "never", "dont", "don't",
        "stop", "no", "not", "them", "they", "when", "ask", "asked", "told", "said", "keep",
        "telling", "asking", "saying", "from", "now", "on", "in", "future", "every", "time",
        "times", "how", "many", "want", "use", "do", "make", "give", "be", "is", "are", "with",
        "ever", "doing", "using", "adding", "writing", "putting", "making", "meant",
    }
)


#: A directive that FORBIDS rather than requires. Polarity is the one word that can never be
#: noise: "always use metric units" and "never use metric units" name the same topic and mean
#: opposite things, and a matcher that strips both merges a reversal into agreement.
_NEGATIVE_POLARITY = re.compile(
    r"\b(?:never|stop|don'?t|do\s+not|avoid|quit|cease|no\s+more|without)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class CorrectionEvidence:
    """One directive and every distinct turn that has asked for it."""

    directive_key: str
    text: str
    evidence_turn_ids: tuple[str, ...] = field(default_factory=tuple)
    #: Set when a LATER turn reversed this directive. A superseded entry is kept, never deleted --
    #: "why does it think that?" has to stay answerable -- but it can no longer be promoted.
    superseded_by_turn_id: str = ""
    #: ISO-8601 UTC of the most recent turn that asked for this. Lifecycle needs a clock, and this
    #: is the only place one enters the module -- always injected, never read inside the logic.
    last_confirmed_at: str = ""

    @property
    def strength(self) -> int:
        """Distinct turns asking for this, which is the only honest measure of repetition."""

        return len(set(self.evidence_turn_ids))

    @property
    def ready_for_promotion(self) -> bool:
        return self.strength >= PROMOTION_THRESHOLD


def correction_directive(text: str) -> str | None:
    """The behavioural directive this turn states, or None when it states none.

    Returns a normalized KEY, not the sentence: "always give me the short version", "give me the
    short version always" and "short version, always" are one directive asked three ways, and
    counting them as three separate one-off remarks is exactly the failure this closes.
    """

    body = " ".join(str(text or "").split())
    if not body or not (_CORRECTION_CUE.search(body) or _TRAILING_RULE_CUE.search(body)):
        return None
    words = re.findall(r"[a-z0-9][a-z0-9'-]*", body.lower())
    meaningful = [word for word in words if word not in _KEY_NOISE and len(word) > 1]
    if len(meaningful) < 2:
        # A bare "stop it" or "no, don't" corrects something, but names nothing to remember. It is
        # a correction of THIS turn, not a standing rule, and inventing a key for it would merge
        # every unrelated complaint onto one entry.
        return None
    polarity = "no" if _NEGATIVE_POLARITY.search(body) else "yes"
    return f"{polarity}|" + ":".join(sorted(set(meaningful))[:6])


def accumulate_correction(
    existing: dict[str, CorrectionEvidence],
    *,
    text: str,
    turn_id: str,
    at: datetime | None = None,
) -> dict[str, CorrectionEvidence]:
    """Fold one turn into the evidence map, returning a NEW map.

    `turn_id` is required and must be non-empty: an entry that cannot name the turn that taught it
    is invalid by construction, which is the whole anti-pollution argument. Re-folding the same
    turn is a no-op rather than a second vote, so a background writer replaying its own conclusion
    can never manufacture strength.
    """

    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    clean_turn = str(turn_id or "").strip()
    if not clean_turn:
        raise ValueError(
            "turn_id is required to record correction evidence -- an unattributed memory write is "
            "exactly the silent-pollution shape this module exists to prevent"
        )
    key = correction_directive(text)
    if key is None:
        return dict(existing)

    updated = dict(existing)
    # Match by OVERLAP, not by key equality. "always give me the short version", "I told you
    # already, give me the short version always" and "short version, always please" are one
    # directive asked three ways -- and an exact-key match filed them as three separate one-off
    # remarks, which is precisely the failure this module exists to close. A stop list cannot fix
    # that: the next paraphrase always carries one word the list does not have.
    key = _merge_target(updated, key) or key
    updated = _supersede_reversed_directives(updated, key=key, turn_id=clean_turn)
    prior = updated.get(key)
    if prior is None:
        updated[key] = CorrectionEvidence(
            directive_key=key,
            text=" ".join(str(text or "").split()),
            evidence_turn_ids=(clean_turn,),
            last_confirmed_at=moment.isoformat(),
        )
        return updated
    if clean_turn in prior.evidence_turn_ids:
        return updated
    updated[key] = CorrectionEvidence(
        directive_key=key,
        text=prior.text,
        evidence_turn_ids=(*prior.evidence_turn_ids, clean_turn),
        superseded_by_turn_id=prior.superseded_by_turn_id,
        last_confirmed_at=moment.isoformat(),
    )
    return updated


def _merge_target(existing: dict[str, CorrectionEvidence], key: str) -> str | None:
    """An existing directive that means the same thing as `key`, or None.

    Sameness is set overlap: the two share at least two content words AND those shared words are
    most of the smaller directive. Requiring TWO shared words keeps "short version" apart from
    "short deadline"; requiring them to dominate the smaller set keeps a long directive from
    swallowing a short unrelated one that happens to share a word.
    """

    candidate_polarity, _, candidate_topic = key.partition("|")
    candidate = set(candidate_topic.split(":"))
    best: tuple[float, str] | None = None
    for other in existing:
        other_polarity, _, other_topic = other.partition("|")
        if other_polarity != candidate_polarity:
            # Same topic, opposite instruction -- a reversal, handled by supersession rather than
            # merged into agreement. Merging here promoted the rule the operator had just
            # withdrawn, carrying the strength of their objections to it. Measured.
            continue
        other_words = set(other_topic.split(":"))
        shared = candidate & other_words
        # No separate "at least two shared words" rule: sabotaging it changed nothing, because the
        # ratio below already rejects every case it would have -- a two-word key sharing one word
        # scores 0.5. A guard that provably does nothing is coupling with no benefit.
        if not shared:
            continue
        ratio = len(shared) / min(len(candidate), len(other_words))
        if ratio >= 0.6 and (best is None or ratio > best[0]):
            best = (ratio, other)
    return best[1] if best else None


def _supersede_reversed_directives(
    existing: dict[str, CorrectionEvidence], *, key: str, turn_id: str
) -> dict[str, CorrectionEvidence]:
    """Mark any opposite-polarity directive on the same topic as reversed BY this turn.

    Kept rather than deleted: the record of what was once asked for, and the turn that withdrew it,
    is what makes "why does it think that?" answerable. Deleting is how a store ends up remembering
    everything and understanding nothing.
    """

    polarity, _, topic = key.partition("|")
    opposite = "yes" if polarity == "no" else "no"
    topic_words = set(topic.split(":"))
    updated = dict(existing)
    for other_key, item in existing.items():
        other_polarity, _, other_topic = other_key.partition("|")
        if other_polarity != opposite or item.superseded_by_turn_id:
            continue
        other_words = set(other_topic.split(":"))
        shared = topic_words & other_words
        if not shared:
            continue
        if len(shared) / min(len(topic_words), len(other_words)) >= 0.6:
            updated[other_key] = CorrectionEvidence(
                directive_key=item.directive_key,
                text=item.text,
                evidence_turn_ids=item.evidence_turn_ids,
                superseded_by_turn_id=turn_id,
                last_confirmed_at=item.last_confirmed_at,
            )
    return updated


def promotion_proposals(evidence: dict[str, CorrectionEvidence]) -> list[CorrectionEvidence]:
    """Directives repeated across enough distinct turns to be worth proposing as instructions.

    A PROPOSAL. Nothing here changes behaviour; the operator decides whether a promoted directive
    is applied, and the returned evidence names the turns so that decision can be made from the
    record rather than from trust.
    """

    ready = [
        item
        for item in evidence.values()
        if item.ready_for_promotion and not item.superseded_by_turn_id
    ]
    return sorted(ready, key=lambda item: (-item.strength, item.directive_key))


@dataclass(frozen=True)
class BudgetOutcome:
    """What the budget kept, what it dropped, and why -- never a silent trim."""

    kept: dict[str, CorrectionEvidence]
    dropped: tuple[CorrectionEvidence, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def within_budget(self) -> bool:
        return not self.dropped


def enforce_evidence_budget(
    evidence: dict[str, CorrectionEvidence], *, budget: int = DEFAULT_EVIDENCE_BUDGET
) -> BudgetOutcome:
    """Bound the evidence map, and REPORT anything it costs.

    Eviction order is by how little the entry is carrying, so the strongest, most-repeated
    directives are the last things to go:

      1. superseded entries first -- already barred from promotion, kept only for readability;
      2. then the weakest (fewest distinct turns);
      3. ties broken by directive key so the outcome is deterministic and replayable.

    A caller that ignores the returned `dropped` has chosen to lose data, which is a different
    thing from not being told. That distinction is the entire point: OpenClaw truncates MEMORY.md
    at ~20K characters with no warning at all (openclaw#45415), so the loss is invisible until the
    agent has already forgotten something the operator relied on.
    """

    if budget < 0:
        raise ValueError("budget cannot be negative")
    if len(evidence) <= budget:
        return BudgetOutcome(kept=dict(evidence))

    def _evict_rank(item: CorrectionEvidence) -> tuple[int, int, str]:
        return (0 if item.superseded_by_turn_id else 1, item.strength, item.directive_key)

    ordered = sorted(evidence.values(), key=_evict_rank)
    overflow = len(evidence) - budget
    dropped = tuple(ordered[:overflow])
    dropped_keys = {item.directive_key for item in dropped}
    kept = {key: item for key, item in evidence.items() if item.directive_key not in dropped_keys}
    return BudgetOutcome(
        kept=kept,
        dropped=dropped,
        reason=(
            f"{len(dropped)} directive(s) dropped to stay within a budget of {budget}: "
            + ", ".join(
                f"{item.directive_key!r} (strength {item.strength}"
                + (", superseded" if item.superseded_by_turn_id else "")
                + ")"
                for item in dropped[:5]
            )
            + ("..." if len(dropped) > 5 else "")
        ),
    )


def retirement_proposals(
    evidence: dict[str, CorrectionEvidence],
    *,
    now: datetime,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> list[CorrectionEvidence]:
    """Live directives nobody has asked for in a long time, weakest and oldest first.

    A PROPOSAL, like promotion -- nothing is deleted here. The operator confirms a rule simply by
    asking for it again, which re-stamps `last_confirmed_at`, so a directive that still matters
    never goes stale in the first place and one they have moved on from stops costing context.

    `now` is required rather than defaulted: a retirement list that silently depends on the wall
    clock cannot be tested for the boundary that matters, and this is a decision about the
    operator's instructions, not a background convenience.

    Superseded entries are NOT proposed. They are already barred from promotion and are kept only
    so a withdrawn rule stays readable; disposing of them is the budget's job, and having two
    mechanisms competing to remove the same record is how a lifecycle turns into a race.
    """

    if stale_after_days < 0:
        raise ValueError("stale_after_days cannot be negative")
    cutoff = now.astimezone(timezone.utc) - timedelta(days=stale_after_days)
    stale: list[CorrectionEvidence] = []
    for item in evidence.values():
        if item.superseded_by_turn_id or not item.last_confirmed_at:
            continue
        try:
            confirmed = datetime.fromisoformat(item.last_confirmed_at)
        except ValueError:
            # An unreadable timestamp is not evidence of staleness. Proposing retirement on a
            # parse failure would quietly delete instructions because a field was malformed.
            continue
        if confirmed.tzinfo is None:
            confirmed = confirmed.replace(tzinfo=timezone.utc)
        if confirmed <= cutoff:
            stale.append(item)
    return sorted(stale, key=lambda item: (item.strength, item.last_confirmed_at, item.directive_key))
