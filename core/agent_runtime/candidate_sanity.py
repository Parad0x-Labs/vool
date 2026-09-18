"""Cheap, deterministic candidate-sanity checks — spent before a challenge/proof model call, not
instead of one, and never a SUBSTITUTE for one.

SCALPEL fixture repair, failure class F, 2026-08-06: several weak candidates in the authoritative
fixture shared one shape — a claim about EXCEPTION BEHAVIOR that a plain read of the cited source's
control flow already answers. "This silently produces wrong output" when the code has a bare
`except: pass`/`break`/`continue` swallowing the error at that exact cited line is a claim this
module's AST read can genuinely contradict (the code demonstrably does NOT swallow there — it
raises), so filtering it costs nothing real.

ARGUS repair D3, 2026-08-06: the original version of this module also filtered the OPPOSITE
direction — "this raises IndexError unexpectedly" whenever the cited code contained ANY explicit
`raise`, on the theory that an explicit raise is always "designed behavior, not an accidental
crash." That reasoning does not hold: an explicit raise can itself be the bug — a wrong triggering
condition, the wrong exception type, a message that leaks a raw secret, a contract violation, an
exception re-raised as the wrong thing further out. Whether a given raise is CORRECT requires
semantic judgment this module's AST walk cannot supply, so that direction is never filtered here —
"the filter is an optimization, not an adjudicator"; when uncertain, the candidate goes to
challenge, not into a filtered bucket a challenge call never sees.

General on purpose, per the operator's own instruction: no filename, no bug class, no per-fixture
exception list. `classify_exception_expectation` answers one question — "at this cited location,
does control flow raise, or does it swallow and continue?" — for any Python source, and callers
decide what that answer means for their specific claim.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExceptionExpectation:
    # "raises" — an explicit `raise` (bare re-raise or a new exception) is reachable from the cited
    #   range without being caught by a swallowing handler first.
    # "swallows" — the cited range sits inside (or at) an `except` clause whose body neither
    #   re-raises nor lets the exception propagate (pass/break/continue/return/bare log-and-drop).
    # "unknown" — no try/except structure touches the cited range, or the source did not parse; the
    #   claim is neither supported nor contradicted by this check alone.
    verdict: str
    reason: str


def classify_exception_expectation(
    source: str, line_start: int, line_end: int
) -> ExceptionExpectation:
    """What the code's own control flow does at the cited lines — raise, or swallow-and-continue.

    Deliberately narrow: this answers a structural question about ONE cited range, not "is this
    function correct". A "swallows" verdict does not itself prove a defect (many swallow-and-log
    patterns are intentional); it means a claim of "silently produces wrong output instead of
    raising" is at least STRUCTURALLY PLAUSIBLE and worth a real challenge call. A "raises" verdict
    means that exact claim is structurally contradicted before any model needs to look at it.
    """
    try:
        tree = ast.parse(source or "")
    except (SyntaxError, ValueError, TypeError):
        return ExceptionExpectation("unknown", "the source did not parse")

    start, end = int(line_start or 0), int(line_end or 0)
    if start <= 0:
        return ExceptionExpectation("unknown", "no citation range given")
    if end < start:
        end = start

    def _in_range(node: ast.AST) -> bool:
        lineno = int(getattr(node, "lineno", 0) or 0)
        return start <= lineno <= end

    # 1) A `raise` statement directly in the cited range is the strongest signal: the code means
    #    to raise here, whether or not it's inside a try/except elsewhere.
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and _in_range(node):
            return ExceptionExpectation(
                "raises", f"an explicit `raise` statement is at/within the cited range (line {node.lineno})"
            )

    # 2) Is the cited range inside an `except` handler's body? Collect EVERY enclosing handler whose
    #    body's line range contains the citation, then use the INNERMOST one (the smallest span) —
    #    ARGUS repair D3, 2026-08-06: `ast.walk` visits nodes breadth-first, not innermost-first, so
    #    a nested try/except where an OUTER handler's body also happens to span the cited lines could
    #    be returned instead of the actual, more specific INNER handler containing them. A citation
    #    inside an inner handler that swallows, wrapped by an outer handler that re-raises something
    #    else, was reported as "raises" from the outer handler alone — the exact shape that must
    #    survive to challenge, not be silently reported as structurally settled.
    candidates: list[tuple[int, ast.excepthandler]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not handler.body:
                continue
            handler_start = int(handler.lineno or 0)
            handler_end = max(
                (int(getattr(n, "end_lineno", getattr(n, "lineno", 0)) or 0) for n in handler.body),
                default=handler_start,
            )
            if handler_start <= start and end <= max(handler_end, handler_start):
                candidates.append((max(handler_end, handler_start) - handler_start, handler))
    if candidates:
        _span, handler = min(candidates, key=lambda pair: pair[0])
        swallows = True
        for stmt in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
            if isinstance(stmt, ast.Raise):
                swallows = False
                break
        if swallows:
            kind = "a bare `except:`" if handler.type is None else "an `except` clause"
            verbs = [type(s).__name__ for s in handler.body]
            tail = verbs[-1] if verbs else "?"
            return ExceptionExpectation(
                "swallows",
                f"the cited range sits inside {kind} whose body does not re-raise "
                f"(ends with a {tail}, line {handler.lineno})",
            )
        return ExceptionExpectation(
            "raises", f"the enclosing `except` clause re-raises (line {handler.lineno})"
        )

    return ExceptionExpectation("unknown", "no try/except structure touches the cited range")


_EXCEPTION_NOUN = r"(?:exceptions?|errors?|[A-Z]\w*(?:Error|Exception)\b)"
# ARGUS repair D3, 2026-08-06: the old pattern was a bare `raises?|throws?|crash(?:es)?` match
# ANYWHERE in the claim text — "throws AWAY bytes" (an ordinary data-loss idiom, nothing to do with
# a Python exception) matched "throws" and was misclassified as an exception claim. Required now:
# the raise/throw/crash verb co-occurs with an actual exception-shaped noun, or with "unexpectedly"
# immediately before it, or with "wrong/incorrect/unexpected" immediately after it — never the bare
# verb alone. "Remove claim-level keyword logic where words such as 'throws' or 'raises' alone
# classify the candidate."
_EXPECTS_RAISE_RE = re.compile(
    rf"\b(?:raises?|throws?|crash(?:es)?)\b[\w\s]{{0,20}}?\b{_EXCEPTION_NOUN}|"
    rf"\b{_EXCEPTION_NOUN}\b[\w\s]{{0,20}}?\b(?:raised|thrown|raises?|throws?)\b|"
    r"\bunexpectedly\s+(?:raises?|throws?|crash(?:es)?)\b|"
    r"\b(?:raises?|throws?)\s+(?:an?\s+)?(?:unexpected|wrong|incorrect)\b",
    re.IGNORECASE,
)
_EXPECTS_SILENT_RE = re.compile(
    r"\b(silent(?:ly)?|without\s+(?:an?\s+)?(?:error|exception|raising)|"
    r"instead\s+of\s+raising|swallow(?:s|ed|ing)?)\b",
    re.IGNORECASE,
)


def exception_claim_direction(failure_scenario: str) -> str:
    """What the CLAIM itself asserts about exception behavior at the cited location.

    "expects_raise" — the claim says the code raises/crashes/throws an actual exception (an
    unwanted-exception claim) — requires an exception-shaped noun or an "unexpectedly"/"wrong"
    qualifier nearby, never the bare verb alone (see `_EXPECTS_RAISE_RE`'s own note).
    "expects_silent" — the claim says the code silently continues/returns wrong data instead of
    raising (an unwanted-silence claim).
    "" — the claim's text does not make either assertion; this check has nothing to compare.
    """
    text = str(failure_scenario or "")
    if _EXPECTS_RAISE_RE.search(text):
        return "expects_raise"
    if _EXPECTS_SILENT_RE.search(text):
        return "expects_silent"
    return ""


@dataclass(frozen=True)
class ProducerConsumerAnalysis:
    """One structured producer/consumer relationship, for Activity/reporting — not a static-
    analysis engine. SCALPEL fixture repair, failure class G, 2026-08-06: the operator's own
    instruction is to compare, explicitly, what a producer represents against what a consumer
    assumes it represents, generalized beyond one file: indexes, caches, manifests, serialization
    metadata, checksums, summaries, capability declarations, fast-negative filters all share this
    shape. This type names the six questions that shape asks, so a finding using it says the same
    six things every time instead of prose that may or may not cover all of them.
    """

    producer: str
    represented_domain: str
    omitted_domain: str
    consumer: str
    consumer_assumption: str
    # Whether the consumer treats the producer's negative/absent result as PROOF of absence (never
    # falls back to checking the full data) or merely as a HINT it may still verify another way.
    absence_is_authoritative: bool
    affected_public_operation: str

    @property
    def is_a_genuine_mismatch(self) -> bool:
        """The shape that is actually a defect: the producer's domain is incomplete (something is
        omitted) AND the consumer treats its negative result as authoritative rather than advisory
        — an omission alone is not a bug (most indexes are deliberately partial); it becomes one
        only when a caller can no longer reach the truth through it."""
        return bool(self.omitted_domain.strip()) and self.absence_is_authoritative

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "producer": self.producer,
            "represented_domain": self.represented_domain,
            "omitted_domain": self.omitted_domain,
            "consumer": self.consumer,
            "consumer_assumption": self.consumer_assumption,
            "absence_is_authoritative": self.absence_is_authoritative,
            "affected_public_operation": self.affected_public_operation,
            "is_a_genuine_mismatch": self.is_a_genuine_mismatch,
        }


def structural_exception_mismatch(
    *, source: str, line_start: int, line_end: int, failure_scenario: str
) -> str:
    """"" when the claim is unsupported by, absent from, or merely UNCERTAIN under this check;
    otherwise a short, factual reason the claim is structurally contradicted by the code's own
    control flow at the cited lines.

    Used as a cheap PRE-FILTER, before a challenge model call is spent: a candidate this function
    contradicts is filtered deterministically (CANDIDATE_FILTERED), not sent to adversarial review.

    ARGUS repair D3, 2026-08-06: ONLY the "claims silence, code demonstrably raises there" direction
    is filtered — a claim that the code raises is NEVER filtered here, no matter how explicit and
    controlled the cited `raise` looks. Whether that raise is itself the bug (wrong condition, wrong
    type, a leaked secret in the message, a violated contract) is not a question this AST read can
    answer; the filter is an optimization on a genuinely narrow, unambiguous contradiction, not an
    adjudicator on whether an existing raise is correct. When `classify_exception_expectation`
    itself is uncertain (`"unknown"` — unparseable source, no citation, no enclosing try/except),
    that uncertainty also means: do not filter, send it to challenge.
    """
    direction = exception_claim_direction(failure_scenario)
    if direction != "expects_silent":
        return ""
    expectation = classify_exception_expectation(source, line_start, line_end)
    if expectation.verdict != "raises":
        return ""
    return (
        "the claim describes silent wrong output, but the cited code contains an explicit, "
        f"controlled `raise` at that exact location ({expectation.reason}) — it does not "
        "silently continue"
    )
