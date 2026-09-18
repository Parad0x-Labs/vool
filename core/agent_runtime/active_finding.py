"""The active finding: what a follow-up is allowed to be *about*.

A turn that names a concrete defect creates a referent. The next turn may point at it with a
pronoun — "prove it", "fix it", "show me" — and carry no other subject at all. Before this module
the referent lived nowhere: the sentence was re-planned from scratch, and a plan built from
"prove it" alone is free to invent its own target. Live, it did: an audit reported a decompression
defect, the operator said "prove the bug you identified", and the runtime generated tests for
empty-input behaviour nobody had mentioned.

So a finding becomes state, with four properties that each close one way the referent used to slip:

* **It is addressed** — `finding_id`, and a scope of `project_id` + `chat_id` + `request_id`, so
  "the finding" is a specific row rather than whatever the next planner reconstructs.
* **It is scoped** — the store is keyed by project AND chat. A finding made in one project is not
  reachable from another, even in a session that carries the same id. Isolation is a lookup miss,
  not a filter someone must remember to apply.
* **It carries its own claim** — `claimed_failure_mechanism` and `expected_failure_behavior` are
  what a continuation must stay bound to (see `continuity_gate`), separate from the title, which
  is prose and drifts.
* **It knows what it is worth** — `confidence` separates a hypothesis from a confirmed defect, and
  `verification_status` records what execution has actually settled. A finding that failed
  reproduction is withdrawn here, not quietly reworded.

This is INTERNAL AGENT STATE. It is never rendered as JSON to an operator; `render_finding_report`
is the only sanctioned way it becomes words.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Bounded: a long-lived daemon must not accumulate a row for every chat it has ever seen.
_MAX_FINDINGS = 128
_FINDING_TTL_SECONDS = 60 * 60 * 6

_LOCK = threading.Lock()
_FINDINGS: dict[str, ActiveFinding] = {}

# --- confidence -----------------------------------------------------------------------------
HYPOTHESIS = "hypothesis"
SUPPORTED = "supported"
CONFIRMED = "confirmed"

# --- verification status ----------------------------------------------------------------------
# What execution has settled about this claim. Finer-grained than `finding_status` below, because
# the operator-facing distinction between "nothing was run" and "a run errored" is real.
UNVERIFIED = "unverified"
CANDIDATE_UNPROVEN = "candidate_unproven"
PROVEN = "proven"
REFUTED = "refuted"
WITHDRAWN = "withdrawn"
# The audit ran and produced nothing that survived checking. This is NOT the absence of a record:
# "there is no finding" is a conclusion the next turn must be able to read, and it is different
# from "no audit has happened in this scope", which is the absence of a record.
NO_FINDING = "no_finding"

# --- finding status ---------------------------------------------------------------------------
# The four states a follow-up gate has to tell apart, and the only vocabulary it reasons in. They
# are derived from `verification_status` rather than stored beside it, so the two can never
# disagree — a second stored field is a second thing to forget to update.
CANDIDATE = "candidate"
VERIFIED = "verified"
REJECTED = "rejected"
# NO_FINDING is shared with the verification vocabulary above: at this coarser grain it means
# exactly the same thing, and a second spelling would only invite a mismatched comparison.

_FINDING_STATUS_BY_VERIFICATION = {
    PROVEN: VERIFIED,
    UNVERIFIED: CANDIDATE,
    CANDIDATE_UNPROVEN: CANDIDATE,
    REFUTED: REJECTED,
    WITHDRAWN: REJECTED,
    NO_FINDING: NO_FINDING,
}

# --- follow-up actions ----------------------------------------------------------------------
REPRODUCE = "reproduce"
FIX = "fix"
EXPLAIN = "explain"
CONTINUE = "continue"
# Attack the previous conclusion, or ask why it was reached. Deliberately NOT a finding-requiring
# action: an audit that ended with nothing can still be challenged, and the answer to "challenge
# your audit" when nothing survived is the audit lane resuming its own recorded state — not a
# refusal, and certainly not a fresh audit of a file the operator never named.
CHALLENGE = "challenge"
NONE = "none"

# A continuation points BACK at a finding without naming it. The object must be a genuine
# back-reference: a bare `that` is often an ordinary conjunction ("prove that you can write
# Python"), and binding on the verb alone would drag unrelated turns into the finding's lane —
# the mirror image of the drift being fixed. So a pronoun counts only where a conjunction cannot
# stand (end of clause, or before a trailing modifier), and everything else must name the finding.
# The determiner does NOT have to sit against the noun. It used to, and the zero-confirmed guard was
# unreachable because of it: with a recorded `no_finding` row in scope, "prove it." blocked
# correctly and "prove the strongest remaining finding" sailed straight past into generic routing,
# where the model chose a demo-video tool and a GitHub URL built from the project name (measured
# 2026-08-07). Up to three modifiers are allowed between them, which covers the natural forms
# ("the strongest remaining finding", "that first issue") without letting the noun drift out of the
# phrase it belongs to. `candidate` joins the list because the audit reports its candidates by that
# name, so "why did you reject candidate 2?" points at something this vocabulary should recognise.
# `one` is deliberately kept ADJACENT to the determiner while every other noun may be modified.
# It is the one member of this list that is not a noun about defects at all — it is a pro-form, and
# with modifiers allowed it swallows ordinary English: "demonstrate that the API is faster than the
# old one" became a continuation, which is precisely the over-binding this vocabulary is supposed
# to prevent. "that one" still binds; "the old one" does not.
_FINDING_NOUN = (
    r"(?:the|that|this|your)\s+"
    r"(?:(?:\w+\s+){0,3}?(?:bug|finding|issue|defect|problem|report|candidate)|one\b)"
)
_TRAILING_PRONOUN = (
    r"(?:it|that|this)\s*(?:[.,;!?]|$|\bbefore\b|\bfirst\b|\bnow\b|\bplease\b|\bagain\b|\bfor\s+me\b)"
)
_ATTRIBUTION = r"\byou\s+(?:identified|found|reported|named|flagged|described|claimed)\b"

_REPRODUCE_VERB = r"(?:prove|reproduce|demonstrate|verify|confirm|test|show)"
_FIX_VERB = r"(?:fix|patch|repair|resolve|correct)"

_REPRODUCE_RE = re.compile(
    rf"\b{_REPRODUCE_VERB}\b.{{0,60}}?(?:{_FINDING_NOUN}|{_ATTRIBUTION}|\b{_TRAILING_PRONOUN})",
    re.IGNORECASE | re.DOTALL,
)
_SHORT_REPRODUCE_RE = re.compile(
    rf"^\s*(?:please\s+|now\s+|ok(?:ay)?[,\s]+|go\s+)*{_REPRODUCE_VERB}\s+(?:{_FINDING_NOUN}|{_TRAILING_PRONOUN})",
    re.IGNORECASE,
)
_FIX_RE = re.compile(
    rf"\b{_FIX_VERB}\b\s*(?:{_FINDING_NOUN}|{_TRAILING_PRONOUN})",
    re.IGNORECASE,
)
# Naming the referent in one sentence and acting on it in the next. The production turn was "Now
# pick the strongest remaining finding. Prove it as far as the read-only audit policy allows." —
# the object sits in sentence one and sentence two carries only a mid-clause "it", which no
# trailing-pronoun shape can catch ("it as far as" is not the end of anything). Selecting from a
# recorded set is itself a back-reference, so the selection is what binds and the verb that follows
# says what to do with it.
_SELECT_FINDING_RE = re.compile(
    rf"\b(?:pick|select|choose|take|start\s+with)\b[^.!?]{{0,40}}?{_FINDING_NOUN}",
    re.IGNORECASE,
)
_ANY_REPRODUCE_VERB_RE = re.compile(rf"\b{_REPRODUCE_VERB}\b", re.IGNORECASE)
# "Show me" plus either nothing (the object is the last thing discussed), a demonstrative that the
# code is broken, or the failure itself by name. The noun list is deliberately closed to words that
# can only mean the finding: "show me the code" and "show me the files" are ordinary requests and
# must stay out of the finding's lane.
_SHOW_ME_RE = re.compile(
    r"^\s*(?:please\s+|now\s+|ok(?:ay)?[,\s]+)*show\s+me\s*(?:[.!?]|$"
    r"|(?:that|this|it|the\s+\w+)\s*(?:is|'s)?\s*(?:actually\s+)?"
    r"(?:broken|breaks?|breaking|failing|fails?|wrong|real)\b"
    r"|(?:the\s+|that\s+|this\s+)?(?:failure|bug|defect|breakage|crash)\b)",
    re.IGNORECASE,
)
# "continue", "keep going", "carry on" — and the same request with the open work named.
#
# Two failures bracket this, and the shape has to miss both. Anchored to the whole string (the
# original), a bare "continue" bound and "Continue from the three files we already identified." did
# not, so the continuation fell to generic routing with no record of the three files. Opened up to
# "verb + any connective + anything" (ARGUS R1), the opposite broke: "Continue the deployment to
# staging", "Resume the download" and "Carry on with lunch" all re-armed the audit lane whenever a
# capsule happened to exist.
#
# The verb opening the sentence is necessary and nowhere near sufficient. What makes a continuation
# a continuation is its OBJECT pointing back at work already done, so the object is what is tested:
# absent, a back-reference, or an investigation noun. Anything else is a fresh subject the verb
# happens to precede, and it does not bind on shape alone — see `continuation_object`, which hands
# those to the state-aware check instead of guessing.
_CONTINUE_VERB = r"(?:continue|carry\s+on|keep\s+going|go\s+on|proceed|resume|pick\s+up)"
_CONTINUE_OPENER_RE = re.compile(
    rf"^\s*(?:please\s+|now\s+|ok(?:ay)?[,\s]+)*{_CONTINUE_VERB}\b(?P<object>.*)$",
    re.IGNORECASE | re.DOTALL,
)
# The object points BACK. Either deictically ("that", "the same", "where we left off") or by naming
# the shared history outright ("we already identified", "you found").
_BACK_REFERENCE_RE = re.compile(
    r"\b(?:that|this|those|these|it|the\s+same|same|earlier|previous(?:ly)?|before|already|"
    r"so\s+far|where\s+(?:we|you)\s+left)\b"
    r"|\b(?:we|you)\s+(?:already\s+)?(?:identified|found|discussed|reported|named|flagged|"
    r"listed|were|had|started|left|began|opened)\b",
    re.IGNORECASE,
)
# Or the object names the work itself. Deliberately a closed list of nouns that can only mean this
# lane's output — never a generic noun like "plan", "task" or "work", each of which is exactly how
# an unrelated object gets described.
_INVESTIGATION_NOUN_RE = re.compile(
    r"\b(?:audits?|investigations?|reviews?|analys[ie]s|inspections?|traces?|"
    r"findings?|candidates?|defects?|bugs?|reports?|scans?|sweeps?|searches)\b",
    re.IGNORECASE,
)
# Words too generic to prove an object refers to the recorded subject. "Keep going on the provider
# health path" earns its binding from "provider"/"health" overlapping the recorded work — never
# from "path", which is a shape word that would match almost any investigation.
_GENERIC_OBJECT_WORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "into", "onto", "about", "our", "your", "its",
        "path", "paths", "plan", "plans", "work", "thing", "things", "stuff", "item", "items",
        "step", "steps", "part", "parts", "rest", "list", "next", "more", "please", "now",
        "then", "there", "here", "one", "ones", "same", "line", "lines", "side", "way",
    }
)
# Asking the previous conclusion to be attacked. "Challenge your own audit", "try to falsify the
# findings", "poke holes in that report" — every one of them is meaningless without a prior audit,
# and every one of them was classified as an ordinary new request. In production that re-opened a
# fresh untargeted audit which re-ran nomination from scratch against the wrong file.
_CHALLENGE_RE = re.compile(
    r"\b(?:challenge|falsify|refute|disprove|re-?examine|re-?assess|revisit|second-guess|"
    r"poke\s+holes\s+in)\b[^.!?]{0,60}?"
    r"\b(?:audit|findings?|report|conclusions?|results?|claims?|analysis|candidates?)\b",
    re.IGNORECASE,
)
# A question about a decision the previous pass made. "Why did you reject candidate 2?" points at
# audit state as precisely as a pronoun does, and carries no subject of its own.
_DECISION_QUERY_RE = re.compile(
    r"\bwhy\b[^.!?]{0,40}?\byou\b[^.!?]{0,30}?"
    r"\b(?:reject(?:ed)?|dismiss(?:ed)?|skip(?:ped)?|withdrew|withdrawn|drop(?:ped)?|"
    r"downgrade[d]?|discard(?:ed)?)\b",
    re.IGNORECASE,
)
_EXPLAIN_RE = re.compile(
    rf"\b(?:explain|walk\s+me\s+through|why\s+is)\b.{{0,40}}?(?:{_FINDING_NOUN}|\b{_TRAILING_PRONOUN})",
    re.IGNORECASE | re.DOTALL,
)

# The operator explicitly dropping the referent. This OUTRANKS every continuation phrasing above:
# "Ignore that one. Check for an empty-input bug instead." is a continuation sentence in form and a
# change of subject in fact, and honouring the form would pin the operator to a finding they just
# discarded. Binding must be sticky against drift, never against instruction.
_RETARGET_RE = re.compile(
    r"\b(?:ignore|forget|drop|skip|discard|disregard|never\s*mind|set\s+aside)\s+"
    r"(?:that|this|it|the\s+\w+|your\s+\w+)\b"
    r"|\b(?:different|another|other|a\s+new)\s+(?:bug|finding|issue|defect|problem|file|target)\b"
    r"|\b(?:instead|rather\s+than\s+that)\b"
    r"|\bnot\s+that\s+one\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FindingScope:
    """Where a finding lives. Two findings in different projects are never the same referent."""

    project_id: str = ""
    chat_id: str = ""
    request_id: str = ""

    @property
    def key(self) -> str:
        # The request id is deliberately NOT in the key: a follow-up is a different request and
        # must still reach the finding. Project and chat are the isolation boundary.
        return f"{self.project_id.strip()}\x1f{self.chat_id.strip()}"

    def same_binding(self, other: FindingScope) -> bool:
        return (
            self.project_id.strip() == other.project_id.strip()
            and self.chat_id.strip() == other.chat_id.strip()
        )


def scope_from_context(source_context: dict[str, Any] | None, *, session_id: str = "") -> FindingScope:
    """The scope this turn belongs to, from the identifiers the runtime already carries.

    `_trusted_project_id` is preferred over `project_id` for the same reason the tool-receipt
    recorder prefers it: the trusted value is stamped by the front door, the plain one can be
    echoed by a payload.
    """
    ctx = source_context if isinstance(source_context, dict) else {}
    project_id = str(ctx.get("_trusted_project_id") or ctx.get("project_id") or "").strip()
    chat_id = str(
        ctx.get("chat_id") or ctx.get("runtime_session_id") or ctx.get("session_id") or session_id or ""
    ).strip()
    request_id = str(ctx.get("request_id") or ctx.get("turn_id") or "").strip()
    return FindingScope(project_id=project_id, chat_id=chat_id, request_id=request_id)


@dataclass
class ActiveFinding:
    """One concrete claim a later turn may refer to. Internal state — never shown as JSON."""

    finding_id: str
    scope: FindingScope
    title: str = ""
    target_files: tuple[str, ...] = ()
    target_symbols_or_lines: tuple[str, ...] = ()
    claimed_failure_mechanism: str = ""
    expected_failure_behavior: str = ""
    evidence_sources: tuple[str, ...] = ()
    confidence: str = HYPOTHESIS
    verification_status: str = UNVERIFIED
    # The lane that owns this finding ("workspace_audit", …), so a continuation resumes the same
    # lane rather than being re-planned by whichever front door claims the sentence first.
    lane: str = ""
    origin_request: str = ""
    # Why this row stopped being referable, for the sentence a blocked continuation prints. Free
    # text, never parsed.
    closing_reason: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def finding_status(self) -> str:
        """CANDIDATE | VERIFIED | REJECTED | NO_FINDING — the state a follow-up gate reads."""
        return _FINDING_STATUS_BY_VERIFICATION.get(self.verification_status, CANDIDATE)

    @property
    def is_open(self) -> bool:
        """Whether this finding can still be the subject of a continuation."""
        return self.finding_status in {CANDIDATE, VERIFIED} and bool(self.finding_id)

    def as_dict(self) -> dict[str, Any]:
        """The internal view. Used by receipts and tests, never by the response layer."""
        return {
            "finding_id": self.finding_id,
            "finding_status": self.finding_status,
            "project_id": self.scope.project_id,
            "chat_id": self.scope.chat_id,
            "request_id": self.scope.request_id,
            "title": self.title,
            "target_files": list(self.target_files),
            "target_symbols_or_lines": list(self.target_symbols_or_lines),
            "claimed_failure_mechanism": self.claimed_failure_mechanism,
            "expected_failure_behavior": self.expected_failure_behavior,
            "evidence_sources": list(self.evidence_sources),
            "confidence": self.confidence,
            "verification_status": self.verification_status,
            "lane": self.lane,
        }


def finding_id_for(*, scope: FindingScope, title: str, target: str) -> str:
    """A stable id for the same claim about the same target in the same scope."""
    seed = f"{scope.key}\x1f{str(target or '').strip()}\x1f{str(title or '').strip().lower()}"
    return "af-" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16]


def _prune_locked() -> None:
    now = time.time()
    for key in [k for k, row in _FINDINGS.items() if now - row.updated_at > _FINDING_TTL_SECONDS]:
        _FINDINGS.pop(key, None)
    while len(_FINDINGS) > _MAX_FINDINGS:
        oldest = min(_FINDINGS, key=lambda k: _FINDINGS[k].updated_at)
        _FINDINGS.pop(oldest, None)


def record_active_finding(finding: ActiveFinding) -> ActiveFinding:
    """Make this the finding a continuation in its scope refers to.

    Most-recent-wins, deliberately: when a turn produces several candidates the operator was shown
    one conclusion, and "prove it" means that one.
    """
    finding.updated_at = time.time()
    with _LOCK:
        _FINDINGS[finding.scope.key] = finding
        _prune_locked()
    return finding


def active_finding_for(scope: FindingScope) -> ActiveFinding | None:
    """The finding this scope may refer to, or None.

    A miss is the isolation boundary doing its job. It is also the honest degraded state: a
    continuation with no referent must ask what the operator means, never guess a target.
    """
    with _LOCK:
        row = _FINDINGS.get(scope.key)
        if row is None:
            return None
        if time.time() - row.updated_at > _FINDING_TTL_SECONDS:
            _FINDINGS.pop(scope.key, None)
            return None
        # Belt as well as braces: the key already isolates, and a row whose own scope disagrees
        # with the caller's is a bug somewhere upstream, not something to serve.
        if not row.scope.same_binding(scope):
            return None
        return row


def record_no_finding(
    scope: FindingScope,
    *,
    target_files: tuple[str, ...] = (),
    lane: str = "",
    origin_request: str = "",
    reason: str = "",
) -> ActiveFinding:
    """Record that the search concluded with nothing. ``active_finding_id`` becomes null.

    Deliberately a ROW and not an erasure. "The previous audit found nothing" and "no audit has run
    in this scope" are different answers to the same follow-up, and only a stored conclusion can
    tell them apart. The row carries no id precisely because there is nothing to point at: a
    continuation resolves to ``None`` through the same accessor a refuted claim does, and the
    difference shows only in the sentence the operator reads.
    """
    return record_active_finding(
        ActiveFinding(
            finding_id="",
            scope=scope,
            title="",
            target_files=tuple(target_files),
            claimed_failure_mechanism="",
            expected_failure_behavior="",
            confidence=HYPOTHESIS,
            verification_status=NO_FINDING,
            lane=str(lane or ""),
            origin_request=str(origin_request or "")[:400],
            closing_reason=str(reason or "")[:400],
        )
    )


def active_finding_id_for(scope: FindingScope) -> str | None:
    """The id a downstream step must carry, or ``None`` when there is nothing to be about.

    One accessor, so no call site can decide for itself that a rejected or absent finding is close
    enough to referable. ``None`` here is the signal that stops the follow-up gate.
    """
    row = active_finding_for(scope)
    if row is None or not row.is_open:
        return None
    return row.finding_id or None


def update_verification_status(
    scope: FindingScope, *, status: str, confidence: str | None = None
) -> ActiveFinding | None:
    """Record what execution settled. A refuted finding stops being referable by that fact alone."""
    with _LOCK:
        row = _FINDINGS.get(scope.key)
        if row is None or not row.scope.same_binding(scope):
            return None
        row.verification_status = str(status or UNVERIFIED)
        if confidence is not None:
            row.confidence = str(confidence)
        row.updated_at = time.time()
        return row


def withdraw_active_finding(scope: FindingScope, *, reason: str = "") -> ActiveFinding | None:
    """Withdraw the finding rather than let a failed reproduction be reworded into a new claim."""
    row = update_verification_status(scope, status=WITHDRAWN, confidence=HYPOTHESIS)
    if row is not None and reason:
        row.claimed_failure_mechanism = row.claimed_failure_mechanism  # unchanged; reason is a log
    return row


def clear_active_findings() -> None:
    """Test seam and session reset. Never called on an ordinary turn."""
    with _LOCK:
        _FINDINGS.clear()


@dataclass(frozen=True)
class FollowUpIntent:
    """What a follow-up sentence is asking to do with the active finding."""

    action: str = NONE
    is_continuation: bool = False
    explicit_retarget: bool = False

    @property
    def binds_to_active_finding(self) -> bool:
        """A continuation binds unless the operator explicitly discarded the referent."""
        return self.is_continuation and not self.explicit_retarget


def continuation_object(text: str) -> str | None:
    """The object of a continue-verb that opens this sentence, or ``None``.

    ``""`` means the verb stands alone ("continue.") — a continuation with no object at all, which
    can only mean the open work. A non-empty string is an object that did NOT prove itself on shape,
    and is the input the state-aware check below needs.
    """
    match = _CONTINUE_OPENER_RE.match(" ".join(str(text or "").split()))
    if match is None:
        return None
    return match.group("object").strip(" .!?,;:")


def _continuation_binds_on_shape(text: str) -> bool:
    """Whether the sentence alone proves it continues prior work."""
    obj = continuation_object(text)
    if obj is None:
        return False
    if not obj:
        return True  # bare "continue" — no object to be wrong about
    return bool(_BACK_REFERENCE_RE.search(obj) or _INVESTIGATION_NOUN_RE.search(obj))


def _content_words(text: str) -> set[str]:
    words = {word for word in re.findall(r"[a-z][a-z0-9_]{2,}", str(text or "").lower())}
    return words - _GENERIC_OBJECT_WORDS


def continuation_refers_to_recorded_work(
    text: str, *, subject: str = "", paths: tuple[str, ...] = ()
) -> bool:
    """Whether a continue-verb's object names the work this scope actually recorded.

    The third way an object can point backwards, and the only one that needs state: "Keep going on
    the provider health path" carries no deictic and no investigation noun, and is a continuation
    purely because the recorded investigation WAS about provider health. "Keep going with the
    workout plan" is the identical shape and is not, because nothing recorded mentions a workout.

    Overlap on content words rather than similarity scoring: the question is whether the operator
    named something the recorded work is about, and a shared content word is exactly that evidence.
    Path segments count, so "keep going on liquefy_apache" reaches a target of that name.
    """
    obj = continuation_object(text)
    if not obj:
        return False
    spoken = _content_words(obj)
    if not spoken:
        return False
    recorded = _content_words(subject)
    for path in paths:
        recorded |= _content_words(re.sub(r"[/\\.\-]+", " ", str(path or "")))
    return bool(spoken & recorded)


def classify_follow_up(text: str) -> FollowUpIntent:
    """Read a sentence for a back-reference to the active finding.

    Nothing here inspects a filename, a language or a bug class — it reads English shape only, so
    it transfers to any lane that records a finding.
    """
    body = " ".join(str(text or "").split())
    if not body:
        return FollowUpIntent()
    retarget = bool(_RETARGET_RE.search(body))

    action = NONE
    # Challenge is checked first: "Challenge your own audit. Take every confirmed finding you just
    # reported and try to falsify it" also contains attribution language a reproduce shape can
    # claim, and answering it with a reproduction would be the wrong half of the request.
    if _CHALLENGE_RE.search(body) or _DECISION_QUERY_RE.search(body):
        action = CHALLENGE
    # Reproduction is checked before repair on purpose: "Prove the bug you identified before fixing
    # it" asks for proof and names repair only as the thing it precedes.
    elif (
        _SHORT_REPRODUCE_RE.search(body)
        or _REPRODUCE_RE.search(body)
        or _SHOW_ME_RE.search(body)
        or (_SELECT_FINDING_RE.search(body) and _ANY_REPRODUCE_VERB_RE.search(body))
    ):
        action = REPRODUCE
    elif _FIX_RE.search(body):
        action = FIX
    elif _EXPLAIN_RE.search(body):
        action = EXPLAIN
    elif _continuation_binds_on_shape(body):
        action = CONTINUE

    return FollowUpIntent(
        action=action,
        is_continuation=action != NONE,
        explicit_retarget=retarget,
    )


def resolve_referent(text: str, scope: FindingScope) -> tuple[ActiveFinding | None, FollowUpIntent]:
    """The finding this follow-up is about, plus why.

    Both halves are required. A continuation phrasing with no finding in scope is NOT a
    continuation — it is an ordinary request that happens to contain the word "prove", and routing
    it on the strength of a verb is the mirror image of the drift being fixed.
    """
    intent = classify_follow_up(text)
    if not intent.binds_to_active_finding:
        return None, intent
    finding = active_finding_for(scope)
    if finding is None or not finding.is_open:
        return None, intent
    return finding, intent


def render_finding_report(
    finding: ActiveFinding,
    *,
    evidence_lines: tuple[str, ...] = (),
    why_it_matters: str = "",
    cloud_context: str = "",
) -> str:
    """The operator-visible form of a finding. The ONLY sanctioned way this state becomes words.

    Deliberately a pure function of the record: no model call composes the headline, so the
    headline cannot disagree with `verification_status`. Affirmative wording lives in one branch.
    """
    location = ", ".join(finding.target_files) or "the inspected source"
    lines_label = ", ".join(finding.target_symbols_or_lines)
    if finding.verification_status == PROVEN:
        headline = "Highest-risk bug"
    elif finding.verification_status in {REFUTED, WITHDRAWN}:
        headline = "Withdrawn — the claim did not hold"
    else:
        headline = "Highest-risk candidate — not yet proven"

    lines = [f"**{headline}: {finding.title}**", "", f"File: `{location}`"]
    if lines_label:
        lines.append(f"Lines: {lines_label}")
    lines += ["", "**What is wrong**", finding.claimed_failure_mechanism or "Not stated."]
    lines += ["", "**Concrete failure**", finding.expected_failure_behavior or "Not stated."]
    if why_it_matters:
        lines += ["", "**Why it matters**", why_it_matters]
    if evidence_lines:
        lines += ["", "**Evidence**"] + [f"- {item}" for item in evidence_lines]
    if finding.verification_status != PROVEN:
        lines += [
            "",
            f"This is a {finding.confidence}, not a confirmed defect: nothing has been executed "
            "against it yet.",
        ]
    lines += ["", "**Cloud context**", cloud_context or "Exact cloud-input token breakdown unavailable."]
    return "\n".join(lines)
