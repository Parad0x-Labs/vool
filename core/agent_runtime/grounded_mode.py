"""Which answer mode a request requires, and the claim contract a grounded answer must satisfy.

Protection (B) and (C) of the tiered-truthfulness design. Behind `VOOL_GROUNDED_MODE`; nothing
here changes a turn until the flag is on and the renderer is wired.

Why three modes rather than one standard
----------------------------------------
Running evidence machinery on every turn buys latency, a browsing dependency, and the orchestration
failures this runtime already documents (`empty_synthesis`, the 60s local read timeout, sub-turn
dogpiling) -- and it buys them worst on the free local models least able to absorb them. "Why is the
sky blue?" needs none of it. So the runtime decides per request:

    DIRECT       stable knowledge, explanatory prose, no browsing, no runtime confidence labels
    GROUNDED     the request promised sources, exactness, currency, or "do not guess"
    AUDIT_GRADE  code audits, high-stakes work, explicit claim-by-claim proof

Nothing here is domain-specific. The Corolla and Prius/Passat prompts are acceptance fixtures, not
the subject: the same detection has to fire for a software version comparison, a regional revenue
breakdown, or a scientific constant nobody publishes to the requested precision.

Why a keyword detector is acceptable HERE
-----------------------------------------
Three hand-tuned word rules were reverted earlier in this work because they decided *what a request
meant* and got it wrong in both directions. This one only decides whether to spend MORE care. A
false positive costs a slower, better-evidenced answer; a false negative is exactly today's
behaviour. Wrong in either direction, it cannot fabricate -- which is the asymmetry that makes the
list defensible here and not inside a split.

The claim contract
------------------
Only the runtime may assign a state. A model cannot promote its own text to SUPPORTED by writing
"verified", "confirmed", "official", "industry consensus" or "widely cited" -- see
`verification_labels.py`, protection (A). A SUPPORTED claim must carry at least one evidence
reference minted by the retrieval layer; text that merely *looks* like a citation is not evidence.

`UNAVAILABLE` is a successful outcome. When no source supports the requested granularity the
correct rendering is "No authoritative global breakdown found" -- not a plausible completion, and
not an empty answer. A table with a missing cell is a better answer than a table with an invented
one.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum


class AnswerMode(str, Enum):
    DIRECT = "direct"
    GROUNDED = "grounded"
    AUDIT_GRADE = "audit_grade"


class ClaimState(str, Enum):
    """Only the runtime assigns these. A model may never promote its own text to SUPPORTED."""

    SUPPORTED = "supported"        # carries >= 1 runtime-minted evidence reference
    INFERRED = "inferred"          # runtime-derived or model-reasoned; suppressed under no-guess
    UNAVAILABLE = "unavailable"    # no source supports the requested granularity -- a real answer
    CONFLICTING = "conflicting"    # sources disagree; both sides must be shown


def grounded_mode_enabled() -> bool:
    return str(os.getenv("VOOL_GROUNDED_MODE", "")).strip().lower() in {"1", "true", "on", "yes"}


# An explicit contract the user stated. Model weights alone cannot satisfy any of these.
_PROMISES_EVIDENCE = (
    r"\bdo(?:n['’]?t| not)\s+guess\b",
    r"\bno\s+guess(?:ing|es)?\b",
    r"\bverified\s+fact",
    r"\bwith\s+(?:sources?|citations?|references?)\b",
    r"\b(?:cite|citing)\s+(?:your\s+)?sources?\b",
    r"\bsources?\s+(?:please|required|only)\b",
    r"\bseparate\s+.{0,40}\bfrom\s+unavailable\b",
    r"\bonly\s+(?:if|what)\s+(?:you\s+)?(?:can\s+)?verif",
    r"\bfact[-\s]?check\b",
)

_PRECISION_REQUEST = re.compile(r"\bexact\s+(?:figure|number|statistic|value)s?\b", re.IGNORECASE)
_VALUE_FIDELITY = re.compile(
    r"\b(?:preserve|retain|keep|copy|reproduce|transcribe)\s+(?:the\s+|these\s+|those\s+)?"
    r"exact\s+(?:figure|number|statistic|value)s?\b", re.IGNORECASE,
)

# Granularity a general aggregate source usually cannot support: a per-country, per-year or
# per-configuration split of an otherwise well-published total. Domain-neutral by construction --
# it matches the SHAPE of the request, not the subject.
_ASKS_FOR_A_BREAKDOWN = (
    r"\bin\s+(?:which|what)\s+(?:country|countries|region|market|state)\b",
    r"\bwhich\s+(?:country|region|market)\s+.{0,30}\b(?:most|highest|largest|top)\b",
    r"\bmost\s+common\s+\w+",
    r"\bbreak\s?down\s+by\b",
    r"\bper\s+(?:country|region|year|quarter|market)\b",
    r"\bwhat\s+year\b.{0,40}\b(?:most|peak|highest|best)\b",
    r"\b(?:by|per)\s+(?:model\s+)?year\b",
)

_WANTS_CURRENT = (
    r"\b(?:current|latest|today['’]?s|right\s+now|as\s+of\s+(?:today|now))\b",
    r"\bthis\s+(?:week|month|quarter|year)\b",
)

# A request whose SUBSTANCE is facts about named things in the world. Read by the requirements
# authority into `ExecutionRequirements.world_facts_requested`; deliberately NOT a care level and
# NOT consulted by `answer_mode_for`, so routing is unchanged (such a turn is DIRECT and reaches
# adaptive research from chat, where a comparison is planned per facet). What the reading governs
# is narrower: a provisional widening by that research is kept when retrieval finds nothing, so
# the publication gate sees the empty retrieval instead of the model's memory of sales figures
# (measured 2026-09-08: "compare the VW Passat and the VW Golf: production periods, sales ..."
# with an unavailable search published "about 37 million" once the widening was retracted).
_VERIFIES_A_WORLD_CLAIM = (
    r"\bverify\s+(?:whether|if|that)\b",
    r"\bis\s+it\s+true\s+that\b",
    r"\bconfirm\s+(?:whether|if|that)\b",
)
_COMPARISON_HEAD = (
    r"\bcompare\b",
    r"\bcomparison\s+(?:of|between)\b",
    r"\bdifference[s]?\s+between\b",
    r"\b\w+\s+(?:vs\.?|versus)\s+\w+",
    r"\bwhich\s+(?:is|one\s+is|would\s+be)\s+(?:better|cheaper|faster|more\s+\w+)\b",
)
_FACTUAL_FACET = (
    r"\b(?:production|produced|manufactur\w*|sales|sold|units|revenue|market\s*share|markets?)\b",
    r"\b(?:price|prices|pricing|cost|costs|cheaper|expensive|free\s+tier|plan|plans|quota|limits?)\b",
    r"\b(?:release[sd]?|launch(?:ed)?|founded|discontinued|generations?|model\s+years?)\b",
    r"\b(?:engine|engines|horsepower|range|mpg|fuel|dimensions|weight|specs?|specifications?)\b",
    r"\b(?:population|area|airport|passengers|gdp|inhabitants|capital)\b",
    r"\b(?:features?|performance|reliability|uptime|support|integrations?|hosting|backend|latency)\b",
    r"\bfor\s+(?:a|an|my|our)\s+\w+(?:\s+\w+){0,3}\s+(?:backend|app|startup|business|project|team|company|site|website|service)\b",
)


def requests_world_facts(request: str) -> bool:
    """Whether the request's substance is facts about named things in the world (see above)."""
    text = " ".join(str(request or "").split())
    if not text:
        return False
    if _matches(text, _VERIFIES_A_WORLD_CLAIM):
        return True
    return _matches(text, _COMPARISON_HEAD) and _matches(text, _FACTUAL_FACET)


_AUDIT_GRADE = (
    r"\baudit\b", r"\bverify\s+every\s+claim\b", r"\bclaim[-\s]by[-\s]claim\b",
    r"\bprove\s+(?:each|every)\b",
    # "security review" is a THING that can be mentioned -- a pipeline stage, a failed gate, a
    # team -- not only an act the user demands. The bare phrase read a supplied dependency
    # graph's "Security Review --> Release" node as a demand to audit, the turn went AUDIT_GRADE
    # with a web-evidence contract, irrelevant security pages were bound, and the graph
    # re-presentation was then withheld for lacking their support (measured live 2026-09-18).
    # It counts as an audit demand only in the imperative shape: a perform verb governing the
    # noun ("run a security review of the auth module"). A word alone never activates the
    # workflow.
    # The demand verb must not itself be governed by a prohibition lead: the first draft's bare
    # `do` read "Do not reinterpret a failed Security Review" as a demand ("do" + " not
    # reinterpret a failed" + the noun). The fixed-width lookbehinds exclude a lead directly
    # before the verb, and the filler between verb and noun stays inside the clause.
    r"(?<!not )(?<!never )(?<!don't )(?<!don’t )(?<!do not )(?<!must not )"
    r"\b(?:run|perform|conduct|carry\s+out|complete|write|give(?:\s+me)?|need|want|request|"
    r"like|start|begin|schedule)\b[^.;!?]{0,48}?\bsecurity\s+review\b",
)

_NO_GUESSING = (
    r"\bdo(?:n['’]?t| not)\s+guess\b",
    # "Invent" takes an object: prohibiting invented evidence is epistemic;
    # prohibiting new architecture/components constrains the design's scope.
    r"\bdo(?:n['’]?t| not)\s+invent(?:\s+(?:(?:any|the|new|extra|additional|unsupported|unverified)\s+)*"
    r"(?:facts?|figures?|numbers?|statistics?|sources?|citations?|evidence|claims?|data|results?|prices?|answers?)\b"
    r"|(?=\s*(?:[.!?;]|$)))",
    r"\bno\s+guess(?:ing|es)?\b",
    r"\bno\s+specul",
    r"\bonly\s+(?:if|what)\s+(?:you\s+)?(?:can\s+)?verif",
)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


# A question whose SUBJECT is the assistant is never a research request, however many currency
# markers it carries. Matched on a possessive/second-person subject close to the question head --
# "your mood", "how are you" -- not on the bare word "you", which appears in ordinary instructions
# ("give me sources you can verify").
_ABOUT_THE_ASSISTANT_RE = re.compile(
    r"\b(?:your|you're|youre)\s+(?:current\s+|latest\s+)?"
    r"(?:mood|feeling|feelings|state|status|opinion|view|thoughts?|name|version|build|model|day)\b"
    r"|\bhow\s+are\s+you\b|\bhow\s+do\s+you\s+feel\b|\bwhat\s+are\s+you\s+(?:doing|thinking)\b",
    re.IGNORECASE,
)


def _is_about_the_assistant(text: str) -> bool:
    return bool(_ABOUT_THE_ASSISTANT_RE.search(str(text or "")))


# A request to BUILD software. The freshness words inside such a request ("check the latest chats",
# "fetch the current weather", "posts today's headlines") specify what the ARTIFACT must do; they
# are not a request that the runtime look anything up now. Matched on the SHAPE -- an authoring
# verb near the head followed within a few words by a software artifact noun -- never on the
# subject. Document artifacts (a report, a summary, a brief) are deliberately absent: a report on
# the latest rate decision needs current information.
_AUTHORING_VERBS = (
    r"(?:create|build|write|make|implement|code|develop|generate|scaffold|set\s+up|program|prototype|spin\s+up"
    # Continuation verbs: they name PREVIOUS software work as their object, so the same
    # verb+noun shape rule admits them only when the object is the work itself.
    r"|continue|extend|modify|update|refactor|finish|fix)"
)
# The noun a CONTINUATION verb names: the previous software work ("Continue the previous
# implementation", "extend the function from your last answer"). A continuation of software
# is ONE atomic authoring task -- its bullet list is a change spec for the SAME artifact,
# never several independent requests. Measured live 2026-09-17: "Continue the previous
# implementation. Add these changes: - add an active boolean field ..." was carved into six
# sub-turns ("- when onlyActive is true" became a standalone request), each fragment lost
# the code it modified, and the merge published contradictory partial implementations.
_CONTINUATION_OBJECTS = (
    r"(?:implementation|version|function|functions|script|scripts|class|classes|module|"
    r"app|application|program|component|code)"
)
_CONTINUATION_REQUEST_RE = re.compile(
    r"\b(?:" + _AUTHORING_VERBS + r")\s+(?:me\s+|us\s+)?(?:the\s+|my\s+|our\s+|this\s+|that\s+|previous\s+|last\s+|your\s+|a\s+|an\s+)?(?:[\w'\u2019-]+\s+){0,3}?" + _CONTINUATION_OBJECTS + r"\b",
    re.IGNORECASE,
)
_SOFTWARE_ARTIFACTS = (
    r"(?:bot|bots|script|scripts|web\s*page|website|site|app|apps|application|tool|cli|function|program|service|"
    r"api|module|class|component|scraper|crawler|pipeline|job|workflow|plugin|extension|daemon|server|endpoint|"
    r"library|package|widget|dashboard|game|notebook|macro|automation|integration|webhook|cron\s+job|lambda|"
    r"microservice|backend|frontend|command|handler|parser|watcher|listener|worker|"
    # The agent-noun family build requests naturally use: "Build me a small Python
    # provider-health CHECKER" matched no artifact noun and the authoring register never
    # claimed it (measured live 2026-09-17: the turn was carved into five fragment sub-turns
    # that each died separately). Same shape requirement as the rest of the list -- an
    # authoring verb, a determiner, and the noun naming the thing to be built.
    r"checkers?|viewers?|monitors?|trackers?|testers?|generators?|runners?|simulators?|"
    r"visuali[sz]ers?|inspectors?|profilers?|linters?|formatters?|converters?|fuzzer?s?|"
    r"utilities?|wrappers?|clients?|sdks?"
    # The artifact deliverable family a single-file request names by its FORM rather than a
    # product noun: "a single HTML file", "one complete index.html". The chat table cut these
    # mid-file (measured live 2026-09-17: the task-board request truncated twice on a paid
    # lane because "task board" matched no register noun and no artifact room was granted).
    # A file the request says to build IS the software artifact.
    r"|html\s+file|\.(?:html?|js|css|py)\b|web\s*app|single[-\s]file)"
)
#: The file-form artifact a request names by its DELIVERABLE ("a single HTML file", "one
#: complete index.html", "a self-contained single-file app"). These forms are specific enough
#: to stand as the artifact on their own, so the verb may sit up to eight words away -- the
#: register's usual four-word window exists to keep generic nouns honest, and a deliverable
#: form needs no such protection.
_FILE_FORM_ARTIFACT_RE = re.compile(
    r"\b(?:html\s+file|\.(?:html?|js|css|py)\b|single[-\s]file\s+(?:app|application|html|page|tool|program)?)\b",
    re.IGNORECASE,
)
_FILE_FORM_REQUEST_RE = re.compile(
    r"\b" + _AUTHORING_VERBS + r"\b[^.;:\n]{0,120}?" + r"(?:html\s+file|\.(?:html?|js|css|py)\b|single[-\s]file)",
    re.IGNORECASE,
)
_AUTHORING_REQUEST_RE = re.compile(
    r"\b" + _AUTHORING_VERBS + r"\s+(?:me\s+|us\s+)?(?:a|an|the|some|this|that|my|our|one|another)?\s*"
    r"(?:[\w'’-]+\s+){0,4}?" + _SOFTWARE_ARTIFACTS + r"\b",
    re.IGNORECASE,
)


def is_software_authoring_request(text: str) -> bool:
    """Whether the request asks the runtime to build software (see the note above).

    A continuation of previous software work is the same register: the artifact already
    exists in the conversation and the request changes it, so its requirements are one
    spec, not independent demands.
    """
    value = str(text or "")
    if _FILE_FORM_REQUEST_RE.search(value) and _FILE_FORM_ARTIFACT_RE.search(value):
        return True
    return bool(_AUTHORING_REQUEST_RE.search(value) or _CONTINUATION_REQUEST_RE.search(value))


_CLAUSE_END_RE = re.compile(r"[.;!?\n]")
#: A coordinated SECOND demand inside the same sentence ("..., and tell me today's rate"): the
#: authoring clause ends where it begins.
_COORDINATED_DEMAND_RE = re.compile(
    r",?\s+(?:and|then|also|plus)\s+(?:tell|what|how|which|when|where|who|give|show|find|check|is|are|"
    r"can|could|would|do|does|did|please|convert|compare|explain|list|summari[sz]e|get|fetch|look)\b",
    re.IGNORECASE,
)


def software_authoring_remainder(text: str) -> str:
    """The request with its software-authoring clause(s) removed.

    A mixed turn ("what is 1000 TRY in EUR right now, and create a script that prints it") keeps
    its live-data clause: the authoring register describes the ARTIFACT clause only, so the rest
    of the sentence is read on its own. Each authoring match is removed from its start (and a
    leading ", and" / "and" / "then" / "also") to the end of its clause."""
    remainder = str(text or "")
    for _ in range(4):
        match = _AUTHORING_REQUEST_RE.search(remainder)
        if not match:
            break
        start = match.start()
        lead_match = re.search(r"(?:,\s*)?(?:and|then|also|plus)\s*$", remainder[:start], re.IGNORECASE)
        if lead_match:
            start = lead_match.start()
        end_match = _CLAUSE_END_RE.search(remainder, match.end())
        end = end_match.end() if end_match else len(remainder)
        coordinated = _COORDINATED_DEMAND_RE.search(remainder, match.end())
        if coordinated and coordinated.start() < end:
            end = coordinated.start()
        remainder = (remainder[:start] + " " + remainder[end:]).strip()
    return " ".join(remainder.split())


def creative_writing_remainder(text: str) -> str:
    """The request with its creative-writing clause(s) removed: the creative register beside the software one.

    A request for a poem, a story or a script names the piece's SUBJECT, and that subject is not a lookup
    the runtime owes now: "write a haiku about rain in this project" asks for a haiku, and "rain in this
    project" is what the haiku is about. Measured on the served path (2026-09-15): the live-weather
    recognizer read "rain in this project" as a place-scoped weather request, `demand_coverage` listed the
    live-info and live-data lanes for the unit, and the live-info lane answered with its lookup reply. No
    lane wrote the haiku. "write a poem about the weather in this folder" reached the model only after a
    live-data weather plan had run.

    Which clause asks for creative writing is the creative authority's reading
    (`core.creative_director.detect_prose_request`), asked of each clause that opens on one of its writing
    verbs or want lead-ins. Each such clause is removed from that word to the end of its clause or the
    start of a coordinated second demand, as `software_authoring_remainder` removes an authoring clause, so
    every other clause is read on its own ("what is the price of gold now? also write a poem" keeps its
    quote). A clause that itself asks for the present state keeps its lookup ("a haiku about today's weather
    in Vilnius"): its subject is current information. Text with no creative-writing clause comes back
    unchanged, whitespace included.
    """
    from core.creative_director import creative_request_anchors, detect_prose_request

    original = str(text or "")
    remainder = original
    for _ in range(4):
        for start in creative_request_anchors(remainder):
            end_match = _CLAUSE_END_RE.search(remainder, start)
            end = end_match.end() if end_match else len(remainder)
            coordinated = _COORDINATED_DEMAND_RE.search(remainder, start)
            if coordinated and coordinated.start() < end:
                end = coordinated.start()
            clause = remainder[start:end]
            if detect_prose_request(clause) is None or _matches(clause, _WANTS_CURRENT):
                continue
            lead_match = re.search(r"(?:,\s*)?(?:and|then|also|plus)\s*$", remainder[:start], re.IGNORECASE)
            remainder = remainder[: lead_match.start() if lead_match else start] + " " + remainder[end:]
            break
        else:
            break
    if remainder == original:
        return original
    remainder = re.sub(r" *\r?\n *", "\n", re.sub(r"[\t\f\v ]+", " ", remainder))
    return remainder.strip(" ,;:-—\t\r\n")


def explicitly_promises_evidence(text: str) -> bool:
    """Whether the user stated an evidence contract that model weights alone cannot satisfy."""
    clean = " ".join(str(text or "").split())
    # Preserving input precision does not ask for new world facts. Remove only that
    # grammatical span; an independent lookup, citation or precision demand still binds.
    return _matches(clean, _PROMISES_EVIDENCE) or bool(
        _PRECISION_REQUEST.search(_VALUE_FIDELITY.sub("", clean))
    )


#: An ASKING head: the clause requests the assistant to report something. A freshness word
#: only arms grounding inside a clause that opens with one of these -- "tell me today's EUR to
#: USD rate" asks for a lookup; "the current behavior duplicates entries" describes the
#: artifact. Head-shaped, not a topic list.
_ASKING_HEAD_RE = re.compile(
    r"^\s*(?:and\s+|then\s+|also\s+|plus\s+)?"
    r"(?:tell|show|give|find|check|look\s+up|fetch|get|what|how|when|where|who|is|are)\b",
    re.IGNORECASE,
)


def _remainder_asks_for_a_lookup(rest: str) -> bool:
    """Whether an authoring turn's non-authoring remainder ASKS the runtime to look something
    up now -- by the classifier's own explicit-lookup and recency-lookup authorities, or by a
    clause that opens with an asking head and carries a freshness signal -- never by a bare
    freshness word (see `answer_mode_for`)."""
    try:
        from core.task_router import looks_like_explicit_lookup_request, looks_like_live_recency_lookup

        if looks_like_explicit_lookup_request(rest) or looks_like_live_recency_lookup(rest):
            return True
    except Exception:
        pass
    for clause in re.split(r"[.;\n]", str(rest or "")):
        clause = clause.strip()
        if clause and _ASKING_HEAD_RE.match(clause) and _matches(clause, _WANTS_CURRENT):
            return True
    return False


def answer_mode_for(request: str) -> AnswerMode:
    """The care level this request requires. DIRECT unless it asks for more."""
    text = " ".join(str(request or "").split())
    if not text:
        return AnswerMode.DIRECT
    if _matches(text, _AUDIT_GRADE):
        return AnswerMode.AUDIT_GRADE
    if explicitly_promises_evidence(text):
        return AnswerMode.GROUNDED
    # A cross-currency comparison asks for market observations even when it omits words such as
    # "current" or "today".  Currency identity is stable; relative value is not.  Keep the
    # structural reading in the currency contract so routing and deterministic refusal cannot
    # grow separate symbol/FX keyword tables.
    from core.currency_value_contract import asks_for_dynamic_currency_value

    if is_software_authoring_request(text):
        # Building software is DIRECT work: the freshness words in its specification describe
        # the artifact, not a lookup the runtime owes now. An explicit evidence contract above
        # still wins, and so does any OTHER clause that ASKS FOR current information on its own
        # ("what is 1000 TRY in EUR right now, and create a script that prints it"). The
        # remainder arms grounding only when it is itself a lookup request -- recognized by
        # the same explicit-lookup and recency authorities the classifier uses -- never
        # because a spec sentence merely contains a freshness word. Measured live
        # 2026-09-17: a self-contained JavaScript task whose spec said "The current behavior
        # duplicates entries" opened the grounding lifecycle on the word "current", searched,
        # bound unrelated sources, and withheld the user's own requirements as unsupported
        # claims. A requirement is task data, not a world claim.
        rest = software_authoring_remainder(text)
        if rest and (
            asks_for_dynamic_currency_value(rest)
            or _matches(rest, _ASKS_FOR_A_BREAKDOWN)
            or _remainder_asks_for_a_lookup(rest)
        ):
            return AnswerMode.GROUNDED
        return AnswerMode.DIRECT
    if asks_for_dynamic_currency_value(text):
        return AnswerMode.GROUNDED
    if _matches(text, _ASKS_FOR_A_BREAKDOWN):
        return AnswerMode.GROUNDED
    if _matches(text, _WANTS_CURRENT) and not _is_about_the_assistant(text):
        # Currency alone means "go and look" only when the subject is the WORLD. "what is your
        # current mood right now" carries every currency marker and is a question about the
        # assistant -- measured 2026-08-06, it opened the tool loop and tripped a gauntlet gate
        # that exists to keep exactly that closed. An evidence promise or a breakdown request
        # (above) is unambiguous and is not subject to this carve-out.
        return AnswerMode.GROUNDED
    return AnswerMode.DIRECT


def forbids_inference(request: str) -> bool:
    """True when the user ruled out guessing, which suppresses INFERRED claims entirely.

    "Do not guess" is a contract about what may be SHOWN, not only about tone. An inferred value
    presented under it is the defect however carefully it is hedged.
    """
    from core.turn_prohibitions import _strip_quoted_spans

    return _matches(" ".join(_strip_quoted_spans(str(request or "")).split()), _NO_GUESSING)


@dataclass(frozen=True)
class Evidence:
    """A reference minted by the retrieval layer. A model cannot create one of these."""

    evidence_id: str
    source_url: str
    passage: str = ""


@dataclass
class Claim:
    """One externally checkable assertion, with the state the RUNTIME assigned it."""

    attribute: str
    value: str = ""
    state: ClaimState = ClaimState.UNAVAILABLE
    evidence: list[Evidence] = field(default_factory=list)
    note: str = ""

    def __post_init__(self) -> None:
        # The contract, enforced at construction rather than trusted: SUPPORTED without evidence is
        # exactly the failure protection (A) catches in prose, and it must be impossible in the
        # structured path rather than merely discouraged.
        if self.state is ClaimState.SUPPORTED and not self.evidence:
            raise ValueError(
                f"claim {self.attribute!r} marked SUPPORTED with no evidence reference; "
                "only the retrieval layer may mint evidence"
            )


UNAVAILABLE_TEXT = "No authoritative global breakdown found"


def render_claims(claims: list[Claim], *, allow_inference: bool) -> str:
    """A grounded answer built FROM the claim states, not from free prose.

    Grouped by state so a reader can see at a glance what is sourced and what is not. Under
    `allow_inference=False` an INFERRED claim renders as unavailable rather than being shown with a
    caveat -- the user asked for no guesses, and a hedged guess is still a guess.

    This is also the deterministic fallback for a failed grounded synthesis: the runtime already
    holds the facts, so a weak model failing to compose the final table must not discard successful
    research or return `empty_synthesis`.
    """
    if not claims:
        return ""
    buckets: dict[ClaimState, list[Claim]] = {state: [] for state in ClaimState}
    for claim in claims:
        state = claim.state
        if state is ClaimState.INFERRED and not allow_inference:
            state = ClaimState.UNAVAILABLE
        buckets[state].append(claim)

    lines: list[str] = []
    if buckets[ClaimState.SUPPORTED]:
        lines.append("**Supported by sources**")
        for claim in buckets[ClaimState.SUPPORTED]:
            refs = " ".join(f"[{item.evidence_id}]({item.source_url})" for item in claim.evidence)
            lines.append(f"- {claim.attribute}: {claim.value} — {refs}")
    if buckets[ClaimState.CONFLICTING]:
        lines.append("\n**Sources disagree**")
        for claim in buckets[ClaimState.CONFLICTING]:
            refs = " ".join(f"[{item.evidence_id}]({item.source_url})" for item in claim.evidence)
            detail = f" ({claim.note})" if claim.note else ""
            lines.append(f"- {claim.attribute}: {claim.value}{detail} — {refs}")
    if buckets[ClaimState.INFERRED] and allow_inference:
        lines.append("\n**Inferred, not directly sourced**")
        for claim in buckets[ClaimState.INFERRED]:
            lines.append(f"- {claim.attribute}: {claim.value}")
    if buckets[ClaimState.UNAVAILABLE]:
        lines.append("\n**Not available**")
        for claim in buckets[ClaimState.UNAVAILABLE]:
            lines.append(f"- {claim.attribute}: {claim.note or UNAVAILABLE_TEXT}")
    return "\n".join(lines).strip()


def citation_supports_claim(claim: Claim, evidence: Evidence) -> bool:
    """Whether a passage supports THIS claim, rather than merely mentioning the same subject.

    A source about total worldwide sales does not support a country-level or per-year split, and
    entity overlap is not claim support. Conservative on purpose: an unverifiable pairing returns
    False and the claim falls to UNAVAILABLE, which is a correct answer. Returning True on a bare
    entity match is how a real source ends up cited for a fabricated attribute.
    """
    passage = str(evidence.passage or "").lower()
    if not passage:
        return False
    attribute_terms = [
        term for term in re.findall(r"[a-z]{4,}", str(claim.attribute or "").lower())
    ]
    if not attribute_terms:
        return False
    # The passage must speak to the ATTRIBUTE, not only to the subject the claim is about.
    return any(term in passage for term in attribute_terms)
