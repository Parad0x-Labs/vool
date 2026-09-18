"""P0 POLICY CONSERVATION — the frozen constraint set of a turn, and the child
policy intersection over it.

THE MEASURED DEFECT (base 389beea5, socket sentinel)
----------------------------------------------------
    "No web. Tell me the current ETH price."

The prohibition is read from the WHOLE turn text at the whole-turn boundary,
and every whole-turn lane declines the fetch. But the message mints two demand
units and the demand-owned executor serves the price unit as a CHILD sub-turn
whose text is the clean slice — the prohibition words sit in the other slice,
the child re-parses its own text, finds nothing, and fetches CoinGecko (12
outbound socket attempts measured at base). Delegation widened authority.

THE CONTRACT
------------
* The parent's prohibitions are frozen ONCE, at ingress, onto the canonical
  `TurnRequest` (`prohibitions` field): typed families + retrieval toolsets +
  reason codes + the user's own negative clauses + a digest. Children run
  under the SAME immutable request object, so inheritance is by construction —
  identity (request/turn/session) and reason codes are preserved, never
  re-derived from a slice.
* Child policy is the INTERSECTION — parent authority ∩ child capability. The
  helpers here never GRANT anything: `conserve_retrieval_constraints` can only
  add prohibitions to a text-derived reading (a child may add its own
  constraints, never remove the parent's), and `child_demand_policy` refuses a
  child demand whose capability needs a family the parent froze.
* Quoted text is not an instruction: the mint strips quoted spans before
  recognition, so `'The sign said "no web"'` freezes nothing.

AUTHORITIES THIS MODULE READS, NEVER RE-IMPLEMENTS
--------------------------------------------------
The retrieval-prohibition recognizers are `core.retrieval_constraints`
(`analyze_retrieval_constraints` — the same vocabulary every whole-turn lane
consults). The retrieval families for toolset-grain decisions are
`retrieval_domains_in` from the same authority. No city, ticker, token or
provider name lives here.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

#: The prohibition families a turn can freeze. Web/tools recognition belongs to
#: `core.retrieval_constraints`; write/spend/local_model are recognized here
#: because no other module owns those prohibitions today.
FAMILY_WEB = "web"
FAMILY_TOOLS = "tools"
FAMILY_WRITE = "write"
FAMILY_SPEND = "spend"
FAMILY_LOCAL_MODEL = "local_model"

#: The reason-code marker a CHILD refusal carries beside the parent's own
#: constraint reason codes, so a receipt can always say both WHAT was frozen
#: and WHERE the freeze came from.
REASON_CONSERVED = "parent_constraint_conserved"

#: Quoted spans are somebody's words, not instructions to this runtime. The
#: straight-single-quote form is boundary-guarded on both ends so contraction
#: apostrophes (`don't`, `it's`) can never open or close a span.
_QUOTED_SPAN_RES = (
    re.compile(r'"[^"\n]{1,200}"'),
    re.compile(r"“[^”\n]{1,200}”"),
    re.compile(r"`[^`\n]{1,200}`"),
    re.compile(r"(?<![\w'])'[^'\n]{1,200}'(?!\w)"),
)

# The three families no other module recognizes. Deliberately narrow: each
# pattern names the prohibition itself, not a topic.
_WRITE_PROHIBITION_RE = re.compile(
    r"\b(?:no|without)\s+(?:any\s+|file\s+|disk\s+|filesystem\s+)?writes?\b"
    r"|\bdo(?:n['’]?t|not)\s+write\s+(?:to\s+)?(?:any\s+|the\s+)?"
    r"(?:files?|disk|filesystem)\b",
    re.IGNORECASE,
)
_SPEND_PROHIBITION_RE = re.compile(
    r"\b(?:no|without)\s+(?:any\s+)?"
    r"(?:spending|paid\s+(?:services?|apis?|models?|tools?|requests?|lookups?))\b"
    r"|\bdo(?:n['’]?t|not)\s+(?:spend|pay\s+for)\b",
    re.IGNORECASE,
)
_LOCAL_MODEL_PROHIBITION_RE = re.compile(
    r"\bno\s+local\s+models?\b"
    r"|\bdo(?:n['’]?t|not)\s+use\s+(?:the\s+|any\s+)?local\s+models?\b",
    re.IGNORECASE,
)

#: What one registry capability CONSUMES when it executes, in frozen-family
#: terms. A capability absent from this table declares that it consumes none
#: of them (local work) and is never refused here — the table is the only
#: place a capability's authority needs are stated, and the registry's
#: capability names are the keys.
CAPABILITY_FAMILY_NEEDS: dict[str, frozenset[str]] = {
    "live_data": frozenset({FAMILY_WEB, FAMILY_TOOLS}),
    "currency": frozenset({FAMILY_WEB, FAMILY_TOOLS}),
    "workspace_write": frozenset({FAMILY_WRITE}),
    "paid_cloud": frozenset({FAMILY_SPEND}),
    "model_reasoning": frozenset({FAMILY_LOCAL_MODEL}),
}


def _constraint_digest(
    families: frozenset[str],
    toolsets: frozenset[str],
    reason_codes: tuple[str, ...],
) -> str:
    """The conservation identity: WHAT is frozen, not how it was worded.

    Two wordings of the same constraint ("No web." / "Don't search the web.")
    carry the same digest, so parallel children of either parent observe — and
    can be pinned on — one authority, not one phrasing.
    """
    payload = "|".join(
        (
            ",".join(sorted(families)),
            ",".join(sorted(toolsets)),
            ",".join(reason_codes),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TurnProhibitions:
    """The parent's frozen constraints — typed, immutable, minted at ingress.

    `families` are the coarse mission families (web/tools/write/spend/
    local_model); `prohibited_toolsets` keep the retrieval authority's finer
    grain (market_prices / weather / news / web_fetch), so a parent may freeze
    one live family while leaving its siblings free.
    """

    families: frozenset[str] = field(default=frozenset())
    prohibited_toolsets: frozenset[str] = field(default=frozenset())
    reason_codes: tuple[str, ...] = ()
    clauses: tuple[str, ...] = ()
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            object.__setattr__(
                self,
                "digest",
                _constraint_digest(self.families, self.prohibited_toolsets, self.reason_codes),
            )

    @property
    def empty(self) -> bool:
        return not (self.families or self.prohibited_toolsets)

    def prohibits_family(self, family: str) -> bool:
        """Whether the parent froze `family` (web, tools, write, ...)."""
        return str(family or "") in self.families

    def restricts_toolset(self, toolset: str) -> bool:
        """Whether a retrieval toolset is frozen, at family OR toolset grain.

        A frozen web/tools family forbids every retrieval toolset — the same
        reading `RetrievalConstraints.forbids` gives at the whole-turn
        boundary; a toolset-level freeze forbids only its own family.
        """
        if self.prohibits_family(FAMILY_WEB) or self.prohibits_family(FAMILY_TOOLS):
            return True
        return str(toolset or "") in self.prohibited_toolsets


#: The canonical empty set: a turn that froze nothing. One object, so an empty
#: parent and an absent request read identically downstream.
NO_PROHIBITIONS = TurnProhibitions()


def _strip_quoted_spans(text: str) -> str:
    """Replace quoted spans with spaces (span geometry preserved for parsers)."""
    value = str(text or "")
    for pattern in _QUOTED_SPAN_RES:
        value = pattern.sub(lambda match: " " * (match.end() - match.start()), value)
    return value


def prohibitions_from_text(text: str) -> TurnProhibitions:
    """Freeze a turn's prohibitions from its WHOLE request text.

    The retrieval reading is `analyze_retrieval_constraints` on the
    quote-stripped text — the same recognizers every whole-turn lane uses, so
    the frozen set can never disagree with the boundary vocabulary. Reason
    codes reuse the prohibited-requirements vocabulary plus one code per
    frozen toolset, so a child refusal can name exactly what was frozen.
    """
    from core.retrieval_constraints import analyze_retrieval_constraints

    stripped = _strip_quoted_spans(text)
    constraints = analyze_retrieval_constraints(stripped)

    families: set[str] = set()
    reasons: list[str] = []
    if constraints.forbids_all_tools:
        families.add(FAMILY_TOOLS)
        families.add(FAMILY_WEB)
        reasons.append("explicit_tool_prohibition")
    elif constraints.forbids_external_retrieval:
        families.add(FAMILY_WEB)
        reasons.append("explicit_retrieval_prohibition")

    toolsets = frozenset(constraints.prohibited_toolsets)
    if toolsets:
        reasons.extend(f"{toolset}_prohibited" for toolset in sorted(toolsets))
        # The whole-turn vocabulary names the live-data flavour of a toolset
        # freeze; keep it so a conserved refusal reads like its parent's.
        if toolsets & {"market_prices", "weather", "news", "web_fetch"}:
            reasons.append("live_data_toolset_prohibited")

    if _WRITE_PROHIBITION_RE.search(stripped):
        families.add(FAMILY_WRITE)
        reasons.append("explicit_write_prohibition")
    if _SPEND_PROHIBITION_RE.search(stripped):
        families.add(FAMILY_SPEND)
        reasons.append("explicit_spend_prohibition")
    if _LOCAL_MODEL_PROHIBITION_RE.search(stripped):
        families.add(FAMILY_LOCAL_MODEL)
        reasons.append("explicit_local_model_prohibition")

    return TurnProhibitions(
        families=frozenset(families),
        prohibited_toolsets=toolsets,
        reason_codes=tuple(dict.fromkeys(reasons)),
        clauses=tuple(constraints.negative_clauses),
    )


def conserved_request_prohibitions(source_context: dict[str, Any] | None) -> TurnProhibitions:
    """The frozen constraints of the CANONICAL request a context carries.

    Children run on shallow copies of the parent context, so the reserved
    `turn_request` key is the propagation channel: whatever request object the
    ingress minted is the authority every lane below reads. Absent key, absent
    field or a non-request value reads as the canonical empty set — this
    helper never guesses a prohibition, and never grants one either.
    """
    from core.turn_contract import TURN_REQUEST_KEY

    request = dict(source_context or {}).get(TURN_REQUEST_KEY)
    prohibitions = getattr(request, "prohibitions", None)
    return prohibitions if isinstance(prohibitions, TurnProhibitions) else NO_PROHIBITIONS


def conserve_retrieval_constraints(
    constraints: Any, source_context: dict[str, Any] | None
) -> Any:
    """A text-derived retrieval reading, widened by the parent's frozen set.

    THE INTERSECTION LAW, stated in its union form: a child's own text may add
    prohibitions, and the parent's frozen set is unioned in afterwards, so the
    result is everywhere AT LEAST as restrictive as either input. Delegation
    can narrow authority; it can never widen it. On the parent's own turn the
    union is idempotent (the freeze was minted from that very text).
    """
    parent = conserved_request_prohibitions(source_context)
    if parent.empty:
        return constraints

    from core.retrieval_constraints import RetrievalConstraints

    return RetrievalConstraints(
        eligible_text=constraints.eligible_text,
        has_prohibition=bool(constraints.has_prohibition)
        or bool(parent.families)
        or bool(parent.prohibited_toolsets),
        forbids_all_tools=bool(constraints.forbids_all_tools)
        or parent.prohibits_family(FAMILY_TOOLS),
        forbids_external_retrieval=bool(constraints.forbids_external_retrieval)
        or parent.prohibits_family(FAMILY_WEB),
        prohibited_toolsets=frozenset(constraints.prohibited_toolsets)
        | frozenset(parent.prohibited_toolsets),
        negative_clauses=tuple(constraints.negative_clauses) + tuple(parent.clauses),
    )


@dataclass(frozen=True)
class ChildPolicyDecision:
    """The intersection outcome for one child demand: allowed, or refused with
    the parent's reason codes (plus the conservation marker) and a refusal
    line the merged answer can carry verbatim."""

    allowed: bool
    reason_codes: tuple[str, ...] = ()
    refusal_text: str = ""

    @property
    def refused(self) -> bool:
        return not self.allowed


_FAMILY_REFUSAL_TEXT = {
    FAMILY_WEB: "requires external retrieval, which this turn explicitly forbids",
    FAMILY_TOOLS: "requires tools this turn explicitly forbids",
    FAMILY_WRITE: "requires a file write, which this turn explicitly forbids",
    FAMILY_SPEND: "requires a paid service, which this turn explicitly forbids",
    FAMILY_LOCAL_MODEL: "requires a local model, which this turn explicitly forbids",
}

_TOOLSET_REFUSAL_TEXT = "requires a live lookup this turn explicitly forbids"


def _refusal(parent: TurnProhibitions, text: str) -> ChildPolicyDecision:
    return ChildPolicyDecision(
        allowed=False,
        reason_codes=(*tuple(parent.reason_codes), REASON_CONSERVED),
        refusal_text=text,
    )


def child_demand_policy(
    parent: TurnProhibitions,
    capability: str,
    unit_text: str,
) -> ChildPolicyDecision:
    """Parent authority ∩ child capability, for one demand unit.

    `capability` is the registry's own name for the lane that would serve the
    unit. The question is only ever "does serving it consume a family (or, at
    live-data grain, a retrieval toolset) the parent froze?" — the child's own
    request text grants nothing. Capabilities that declare no frozen-family
    needs (local reads, arithmetic) are always allowed; an unknown capability
    declares nothing and is not refused here.
    """
    if parent.empty:
        return ChildPolicyDecision(allowed=True)

    needs = CAPABILITY_FAMILY_NEEDS.get(str(capability or ""), frozenset())
    for family in sorted(needs & parent.families):
        return _refusal(parent, _FAMILY_REFUSAL_TEXT[family])

    if str(capability or "") == "live_data" and parent.prohibited_toolsets:
        # Toolset grain, mirroring `_live_data_classification`: a parent froze
        # market prices (say) — a unit whose OWN live families are all frozen
        # is refused, a unit naming an unfrozen family still runs.
        from core.retrieval_constraints import retrieval_domains_in

        domains = retrieval_domains_in(str(unit_text or ""))
        if domains and domains <= parent.prohibited_toolsets:
            return _refusal(parent, _TOOLSET_REFUSAL_TEXT)

    return ChildPolicyDecision(allowed=True)


__all__ = [
    "CAPABILITY_FAMILY_NEEDS",
    "FAMILY_LOCAL_MODEL",
    "FAMILY_SPEND",
    "FAMILY_TOOLS",
    "FAMILY_WEB",
    "FAMILY_WRITE",
    "NO_PROHIBITIONS",
    "REASON_CONSERVED",
    "ChildPolicyDecision",
    "TurnProhibitions",
    "child_demand_policy",
    "conserve_retrieval_constraints",
    "conserved_request_prohibitions",
    "prohibitions_from_text",
]
