"""Cheap, raw-bound semantic admission for deterministic turn routes.

This module deliberately knows no domain facts.  It records discourse frames and a very small
set of structural defects, then requires the route that wants to answer deterministically to
provide typed proof for every entity/relation/type dependency it owns.

It never calls a model, provider, retrieval backend, tool, or ontology.  A miss is not a chat
verdict: admission simply declines and the ordinary conductor/model path remains responsible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.hypothetical_frame import detect_hypothetical_frame
from core.semantic.canonical_text import CanonicalText, EntitySpan
from core.turn_ir import ClauseKind, TurnIR, classify_clause_kind, parse_turn_ir

RAW_USER_TEXT_REPRESENTATION = "user_text.raw.v1"


class SemanticFrame(str, Enum):
    """Where the turn says its semantic authority comes from."""

    REAL = "real"
    STIPULATED = "stipulated"
    FICTIONAL = "fictional"
    QUOTED = "quoted"
    UNKNOWN = "unknown"


class SemanticRequirement(str, Enum):
    ENTITY_IDENTITY = "entity_identity"
    RELATION_IDENTITY = "relation_identity"
    TYPE_MEMBERSHIP = "type_membership"


class Establishment(str, Enum):
    """Where a route obtained one semantic binding.

    No inferred/guessed member exists.  If a lane cannot name one of these authorities, the
    requirement remains unestablished and deterministic admission declines.
    """

    EXPLICIT_TEXT = "explicit_text"
    USER_STIPULATION = "user_stipulation"
    AUTHORITATIVE_REGISTRY = "authoritative_registry"
    RUNTIME_STATE = "runtime_state"
    VERIFIED_PRIOR_RECEIPT = "verified_prior_receipt"
    UNESTABLISHED = "unestablished"
    AMBIGUOUS = "ambiguous"


class StructuralIssueCode(str, Enum):
    UNTERMINATED_QUOTE = "unterminated_quote"
    CONFLICTING_LITERAL_ASSIGNMENT = "conflicting_literal_assignment"
    CONFLICTING_RESPONSE_CONSTRAINT = "conflicting_response_constraint"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"


class AdmissionReason(str, Enum):
    ADMITTED = "admitted"
    ROUTE_ID_REQUIRED = "route_id_required"
    FRAME_NOT_ALLOWED = "frame_not_allowed"
    STRUCTURAL_ISSUE = "structural_issue"
    PROOF_ABSENT = "proof_absent"
    PROOF_UNESTABLISHED = "proof_unestablished"
    PROOF_WRONG_TEXT = "proof_wrong_text"
    PROOF_OUTSIDE_SCOPE = "proof_outside_scope"
    EFFECT_FRAME_FORBIDDEN = "effect_frame_forbidden"


@dataclass(frozen=True)
class FrameScope:
    frame: SemanticFrame
    scope: EntitySpan
    marker_spans: tuple[EntitySpan, ...] = ()


@dataclass(frozen=True)
class StructuralIssue:
    code: StructuralIssueCode
    scope: EntitySpan
    related_spans: tuple[EntitySpan, ...] = ()
    blocks: frozenset[SemanticRequirement] = frozenset()


@dataclass(frozen=True)
class SemanticProof:
    requirement: SemanticRequirement
    establishment: Establishment
    subject_span: EntitySpan
    source_id: str = ""
    relation: str = ""
    object_value: str = ""


@dataclass(frozen=True)
class DeterministicRouteCandidate:
    route_id: str
    allowed_frames: frozenset[SemanticFrame]
    required: frozenset[SemanticRequirement]
    proofs: tuple[SemanticProof, ...] = ()
    scope: EntitySpan | None = None
    effectful: bool = False
    resolved_issues: frozenset[StructuralIssueCode] = frozenset()


@dataclass(frozen=True)
class DeterministicAdmission:
    admitted: bool
    deterministic_route_safe: bool
    reasons: tuple[AdmissionReason, ...]


@dataclass(frozen=True)
class SemanticPreflight:
    """One immutable inspection of the exact authoritative user text."""

    raw: CanonicalText
    turn_ir: TurnIR
    frames: tuple[FrameScope, ...]
    dominant_frame: SemanticFrame
    issues: tuple[StructuralIssue, ...]
    unresolved_requirements: frozenset[SemanticRequirement]
    deterministic_route_safe: bool
    effect_routes_safe: bool

    @property
    def full_span(self) -> EntitySpan:
        return self.raw.span(0, self.raw.length, kind="semantic_turn")


_QUOTE_PAIRS = {'"': '"', "“": "”", "`": "`", "«": "»"}
_FICTION_MARKER = re.compile(
    r"fiction|fictitious|hypothetical|counterfactual|make[- ]believe|imaginary|"
    r"alternate (?:universe|reality|timeline|history)|pretend|story|game",
    re.IGNORECASE,
)
_REAL_FRAME = re.compile(
    r"\b(?:actually|current(?:ly)?|today(?:'s)?|live|present[- ]day|real[- ]world|"
    r"in reality|runtime|on this machine|right now)\b",
    re.IGNORECASE,
)
_FRAME_EXIT = re.compile(
    r"\b(?:back to (?:reality|the real world)|(?:end|drop|leave|exit) "
    r"(?:the |this )?(?:scenario|hypothetical|fiction|roleplay|timeline))\b",
    re.IGNORECASE,
)
_QUOTED_DISCOURSE = re.compile(
    r"\b(?:calls?|called|says?|said|claims?|claimed|labels?|labelled|means?|meant|"
    r"phrase|metaphor|figurative|quote|quoted|example|test case|worksheet|fable|"
    r"interpret|explain)\b",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    r"(?<![\w])(?P<lhs>[A-Za-z][A-Za-z0-9_. -]{0,48}?)\s*=\s*"
    r"(?P<rhs>[+-]?(?:\d+(?:\.\d+)?|\.\d+)|\"[^\"\n]{0,120}\"|"
    r"“[^”\n]{0,120}”|'[^'\n]{0,120}'|[A-Za-z][A-Za-z0-9_.-]{0,48})",
)
_EXACT_WORDS = re.compile(r"\bexactly\s+(?P<count>\d{1,4})\s+words?\b", re.IGNORECASE)


def _quote_spans(raw: CanonicalText) -> tuple[tuple[EntitySpan, ...], EntitySpan | None]:
    """Balanced quote contents and, separately, one unterminated quote opener."""

    text = raw.text
    spans: list[EntitySpan] = []
    opener_index: int | None = None
    closer = ""
    content_start = 0
    for index, char in enumerate(text):
        if opener_index is not None:
            if char == closer:
                spans.append(raw.span(content_start, index, kind="quoted_content"))
                opener_index = None
                closer = ""
            continue
        if char in _QUOTE_PAIRS:
            opener_index = index
            closer = _QUOTE_PAIRS[char]
            content_start = index + 1
    unterminated = (
        raw.span(opener_index, raw.length, kind="unterminated_quote")
        if opener_index is not None
        else None
    )
    return tuple(spans), unterminated


def _find_marker_spans(raw: CanonicalText, markers: tuple[str, ...]) -> tuple[EntitySpan, ...]:
    spans: list[EntitySpan] = []
    lowered = raw.text.casefold()
    cursor = 0
    for marker in markers:
        needle = str(marker or "").casefold().strip()
        if not needle:
            continue
        index = lowered.find(needle, cursor)
        if index < 0:
            index = lowered.find(needle)
        if index < 0:
            continue
        spans.append(raw.span(index, index + len(needle), kind="frame_marker"))
        cursor = index + len(needle)
    return tuple(spans)


def _quoted_content_is_the_subject(text: str, quote_spans: tuple[EntitySpan, ...]) -> bool:
    if not quote_spans:
        return False
    first = quote_spans[0]
    last = quote_spans[-1]
    outside = (text[: max(0, first.start - 1)] + " " + text[min(len(text), last.end + 1) :]).strip()
    # A fully quoted question remains the question itself rather than becoming an example.
    if not outside:
        return False
    if re.search(r"\b(?:search|browse|look up|find online|google)\b", outside, re.IGNORECASE):
        return False
    return bool(_QUOTED_DISCOURSE.search(outside))


def _frame_scopes(raw: CanonicalText) -> tuple[tuple[FrameScope, ...], SemanticFrame, bool]:
    text = raw.text
    quote_spans, _unterminated = _quote_spans(raw)
    detected = detect_hypothetical_frame(text)
    marker_spans = _find_marker_spans(raw, detected.markers)
    frames: list[FrameScope] = []

    premise_frame: SemanticFrame | None = None
    if detected.supplies_premises:
        premise_frame = (
            SemanticFrame.FICTIONAL
            if any(_FICTION_MARKER.search(marker) for marker in detected.markers)
            else SemanticFrame.STIPULATED
        )
        exit_match = _FRAME_EXIT.search(text)
        premise_end = exit_match.start() if exit_match is not None else raw.length
        frames.append(
            FrameScope(
                premise_frame,
                raw.span(0, premise_end, kind="semantic_frame"),
                tuple(span for span in marker_spans if span.start < premise_end),
            )
        )
        if exit_match is not None:
            exit_span = raw.span(exit_match.start(), raw.length, kind="semantic_frame")
            frames.append(
                FrameScope(
                    SemanticFrame.REAL,
                    exit_span,
                    (raw.span(exit_match.start(), exit_match.end(), kind="frame_marker"),),
                )
            )

    quoted_subject = _quoted_content_is_the_subject(text, quote_spans)
    if quoted_subject:
        frames.extend(FrameScope(SemanticFrame.QUOTED, span, (span,)) for span in quote_spans)

    # Real-frame markers inside a quoted example are not authority for the containing request.
    masked = list(text)
    for span in quote_spans:
        for index in range(span.start, span.end):
            masked[index] = " "
    real_matches = tuple(_REAL_FRAME.finditer("".join(masked)))
    if premise_frame is None and real_matches and not quoted_subject:
        marker = real_matches[0]
        frames.append(
            FrameScope(
                SemanticFrame.REAL,
                raw.span(0, raw.length, kind="semantic_frame"),
                (raw.span(marker.start(), marker.end(), kind="frame_marker"),),
            )
        )

    distinct = {frame.frame for frame in frames if frame.frame is not SemanticFrame.QUOTED}
    if len(distinct) > 1:
        dominant = SemanticFrame.UNKNOWN
    elif premise_frame is not None:
        dominant = premise_frame
    elif quoted_subject:
        dominant = SemanticFrame.QUOTED
    elif distinct:
        dominant = next(iter(distinct))
    else:
        dominant = SemanticFrame.UNKNOWN

    # A framed premise is not effect authority. An explicit frame exit creates a separate REAL
    # scope; that later scope may contain a genuine action and therefore must not be globally muted.
    quoted_action_outside_quote = False
    if dominant is SemanticFrame.QUOTED:
        for clause in parse_turn_ir(text, response_shape_parser=None).clauses:
            if clause.kind not in {ClauseKind.ACT, ClauseKind.CREATE, ClauseKind.OBSERVE}:
                continue
            if any(
                clause.request_start >= span.start and clause.request_end <= span.end
                for span in quote_spans
            ):
                continue
            quoted_action_outside_quote = True
            break
        if not quoted_action_outside_quote and quote_spans:
            quoted_action_outside_quote = classify_clause_kind(
                text[min(len(text), quote_spans[-1].end + 1) :]
            ) in {ClauseKind.ACT, ClauseKind.CREATE, ClauseKind.OBSERVE}
    effect_routes_safe = dominant not in {
        SemanticFrame.STIPULATED,
        SemanticFrame.FICTIONAL,
        SemanticFrame.QUOTED,
    } or quoted_action_outside_quote
    return tuple(frames), dominant, effect_routes_safe


def _structural_issues(
    raw: CanonicalText,
    *,
    ambiguous_reference: bool,
) -> tuple[StructuralIssue, ...]:
    issues: list[StructuralIssue] = []
    _quotes, unterminated = _quote_spans(raw)
    if unterminated is not None:
        issues.append(
            StructuralIssue(
                StructuralIssueCode.UNTERMINATED_QUOTE,
                unterminated,
                (unterminated,),
                frozenset(SemanticRequirement),
            )
        )

    assignments: dict[str, tuple[str, EntitySpan]] = {}
    for match in _ASSIGNMENT.finditer(raw.text):
        lhs = " ".join(match.group("lhs").casefold().split())
        rhs = " ".join(match.group("rhs").casefold().split())
        current = raw.span(match.start(), match.end(), kind="literal_assignment")
        prior = assignments.get(lhs)
        if prior is not None and prior[0] != rhs:
            issues.append(
                StructuralIssue(
                    StructuralIssueCode.CONFLICTING_LITERAL_ASSIGNMENT,
                    raw.span(prior[1].start, current.end, kind="structural_conflict"),
                    (prior[1], current),
                    frozenset(SemanticRequirement),
                )
            )
        else:
            assignments[lhs] = (rhs, current)

    word_constraints = tuple(_EXACT_WORDS.finditer(raw.text))
    counts = {match.group("count") for match in word_constraints}
    if len(counts) > 1:
        related = tuple(
            raw.span(match.start(), match.end(), kind="response_constraint")
            for match in word_constraints
        )
        issues.append(
            StructuralIssue(
                StructuralIssueCode.CONFLICTING_RESPONSE_CONSTRAINT,
                raw.span(related[0].start, related[-1].end, kind="structural_conflict"),
                related,
                frozenset(),
            )
        )

    if ambiguous_reference:
        full = raw.span(0, raw.length, kind="ambiguous_reference")
        issues.append(
            StructuralIssue(
                StructuralIssueCode.AMBIGUOUS_REFERENCE,
                full,
                (full,),
                frozenset(SemanticRequirement),
            )
        )
    return tuple(issues)


def semantic_preflight(
    raw_text: str,
    *,
    ambiguous_reference: bool = False,
) -> SemanticPreflight:
    """Inspect exact turn text without acquiring semantic or execution authority."""

    raw = CanonicalText.of(raw_text, representation=RAW_USER_TEXT_REPRESENTATION)
    turn_ir = parse_turn_ir(raw.text)
    frames, dominant, effect_routes_safe = _frame_scopes(raw)
    issues = _structural_issues(raw, ambiguous_reference=ambiguous_reference)
    unresolved = frozenset(SemanticRequirement)
    # There is no route yet, hence no entity/relation/type proof.  Route-specific admission below
    # is the only operation that can turn this false into a true deterministic decision.
    return SemanticPreflight(
        raw=raw,
        turn_ir=turn_ir,
        frames=frames,
        dominant_frame=dominant,
        issues=issues,
        unresolved_requirements=unresolved,
        deterministic_route_safe=False,
        effect_routes_safe=effect_routes_safe,
    )


def _spans_overlap(left: EntitySpan, right: EntitySpan) -> bool:
    return left.binds_to_same_text(right) and left.start < right.end and right.start < left.end


def admit_deterministic_candidate(
    preflight: SemanticPreflight,
    candidate: DeterministicRouteCandidate,
) -> DeterministicAdmission:
    """Admit a deterministic route only when every semantic dependency has typed proof."""

    reasons: list[AdmissionReason] = []
    scope = candidate.scope or preflight.full_span
    if not candidate.route_id.strip():
        reasons.append(AdmissionReason.ROUTE_ID_REQUIRED)
    try:
        scope.resolve(preflight.raw)
    except Exception:
        reasons.append(AdmissionReason.PROOF_WRONG_TEXT)

    if preflight.dominant_frame not in candidate.allowed_frames:
        reasons.append(AdmissionReason.FRAME_NOT_ALLOWED)
    if candidate.effectful and not preflight.effect_routes_safe:
        reasons.append(AdmissionReason.EFFECT_FRAME_FORBIDDEN)

    for issue in preflight.issues:
        if issue.code in candidate.resolved_issues:
            continue
        if _spans_overlap(scope, issue.scope) and (not issue.blocks or issue.blocks & candidate.required):
            reasons.append(AdmissionReason.STRUCTURAL_ISSUE)
            break

    established: set[SemanticRequirement] = set()
    for proof in candidate.proofs:
        if proof.establishment in {Establishment.UNESTABLISHED, Establishment.AMBIGUOUS}:
            reasons.append(AdmissionReason.PROOF_UNESTABLISHED)
            continue
        try:
            proof.subject_span.resolve(preflight.raw)
        except Exception:
            reasons.append(AdmissionReason.PROOF_WRONG_TEXT)
            continue
        if proof.subject_span.start < scope.start or proof.subject_span.end > scope.end:
            reasons.append(AdmissionReason.PROOF_OUTSIDE_SCOPE)
            continue
        established.add(proof.requirement)
    if candidate.required - established:
        reasons.append(AdmissionReason.PROOF_ABSENT)

    unique_reasons = tuple(dict.fromkeys(reasons))
    if unique_reasons:
        return DeterministicAdmission(False, False, unique_reasons)
    return DeterministicAdmission(True, True, (AdmissionReason.ADMITTED,))


def authoritative_whole_turn_candidate(
    preflight: SemanticPreflight,
    *,
    route_id: str,
    source_id: str,
    allowed_frames: frozenset[SemanticFrame],
    resolved_issues: frozenset[StructuralIssueCode] = frozenset(),
) -> DeterministicRouteCandidate:
    """Build proof for a reviewed local registry that owns the whole semantic answer.

    This helper carries no facts. The caller is responsible for invoking its domain resolver first;
    a non-answer must never manufacture this candidate merely to make admission green.
    """

    scope = preflight.full_span
    proofs = tuple(
        SemanticProof(
            requirement=requirement,
            establishment=Establishment.AUTHORITATIVE_REGISTRY,
            subject_span=scope,
            source_id=source_id,
        )
        for requirement in SemanticRequirement
    )
    return DeterministicRouteCandidate(
        route_id=route_id,
        allowed_frames=allowed_frames,
        required=frozenset(SemanticRequirement),
        proofs=proofs,
        scope=scope,
        resolved_issues=resolved_issues,
    )


def explicit_text_whole_turn_candidate(
    preflight: SemanticPreflight,
    *,
    route_id: str,
    source_id: str,
    allowed_frames: frozenset[SemanticFrame],
    effectful: bool = False,
) -> DeterministicRouteCandidate:
    """Candidate for a typed recognizer whose exact raw-text probe accepted the request.

    The caller must run that recognizer first.  This helper is intentionally unable to decide that
    a path, machine, operation or entity is present; it only records the proof after the route's
    existing typed probe has established all three bindings against this raw turn.
    """

    scope = preflight.full_span
    return DeterministicRouteCandidate(
        route_id=route_id,
        allowed_frames=allowed_frames,
        required=frozenset(SemanticRequirement),
        proofs=tuple(
            SemanticProof(
                requirement=requirement,
                establishment=Establishment.EXPLICIT_TEXT,
                subject_span=scope,
                source_id=source_id,
            )
            for requirement in SemanticRequirement
        ),
        scope=scope,
        effectful=effectful,
    )


__all__ = [
    "RAW_USER_TEXT_REPRESENTATION",
    "AdmissionReason",
    "DeterministicAdmission",
    "DeterministicRouteCandidate",
    "Establishment",
    "FrameScope",
    "SemanticFrame",
    "SemanticPreflight",
    "SemanticProof",
    "SemanticRequirement",
    "StructuralIssue",
    "StructuralIssueCode",
    "admit_deterministic_candidate",
    "authoritative_whole_turn_candidate",
    "explicit_text_whole_turn_candidate",
    "semantic_preflight",
]
