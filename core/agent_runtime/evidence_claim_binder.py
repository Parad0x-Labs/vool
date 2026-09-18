"""Bind a reply's runtime-observable claims to the runtime's own trace, and repair the ones that contradict it.

The model may be wrong. The durable execution trace must not be rewritten by prose afterwards.

The production failure: Activity recorded an attempted fetch of
`https://github.com/VOOL-ai/openclaw-skills`; the reply said "I never tried GitHub." Every existing
guard in `action_honesty_validator` looks for a POSITIVE claim missing a receipt, so a DENIAL of
something the trace recorded was not merely allowed — it was invisible. A second reply asserted a
provider-attested model when no provider attestation existed anywhere in the runtime.

**Deliberately narrow.** Only claims the runtime can settle from its own trace are touched:

* an absolute first-person denial of a tool action against a source the trace tracks;
* a claim that a PROVIDER attested which model served the turn.

Everything else is left exactly as the model wrote it. This lane does not fact-check findings, does
not police opinions, does not rewrite style, and has no opinion on whether anything outside the
runtime is true. Mutation vocabulary (created / deleted / sent funds) is owned by
`action_honesty_validator`'s existing claim regexes and is not re-litigated here — two layers
fighting over one sentence is a worse outcome than either one alone.

**Fails open at every boundary.** No turn id, no subject, no evidence store — the reply passes
through untouched. It repairs one sentence at a time and never discards the rest of an answer,
because the cost of a false positive is a correct answer withheld, which is worse than the
fabrication it would have prevented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.runtime_evidence import FILE_SUFFIXES, SCOPE_REMOTE, TurnEvidence, host_of

VERDICT_CONTRADICTED = "contradicted"  # the trace says the opposite
VERDICT_UNPROVABLE = "unprovable"  # no evidence either way, and no complete scope to prove absence
VERDICT_SUPPORTED = "supported"  # the trace agrees; leave the sentence alone

SUBJECT_HOST = "host"
SUBJECT_REMOTE = "remote"
SUBJECT_PATH = "path"
SUBJECT_READ = "read"
SUBJECT_ATTESTATION = "provider_attestation"

# A sentence boundary that keeps the delimiter, so a repaired reply reads like prose rather than a
# list of fragments.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# --- Denial shape ------------------------------------------------------------------------------
# A denial is a NEGATION governing an ACCESS VERB. Both halves are required and they must sit in the
# same sentence: "I checked the cache, so no fetch was needed" is not a denial that a fetch happened,
# and a whole-response search would read it as one.
_NEGATION = (
    r"never|not|n't|no\b|none|nothing|neither|nor|"
    r"did\s+not|didn'?t|do\s+not|don'?t|does\s+not|doesn'?t|"
    r"have\s+not|haven'?t|has\s+not|hasn'?t|had\s+not|hadn'?t|"
    r"was\s+not|wasn'?t|were\s+not|weren'?t|at\s+no\s+point|without"
)
_ACCESS_VERB = (
    r"tr(?:y|ied|ying)|attempt(?:ed|ing)?|call(?:ed|ing)?|contact(?:ed|ing)?|"
    r"fetch(?:ed|ing)?|request(?:ed|ing)?|quer(?:y|ied|ying)|hit|access(?:ed|ing)?|"
    r"browse(?:d)?|browsing|visit(?:ed|ing)?|open(?:ed|ing)?|read|load(?:ed|ing)?|"
    r"download(?:ed|ing)?|reach(?:ed|ing)?|us(?:e|ed|ing)|look(?:ed)?\s+(?:at|up)|"
    r"connect(?:ed|ing)?|pull(?:ed|ing)?|retriev(?:e|ed|ing)|scrape(?:d)?|"
    r"search(?:ed|ing)?|go(?:ne)?\s+(?:to|online)|went\s+(?:to|online)"
)
# A denial written as an event that did not occur: "no GitHub call occurred", "no fetch was made".
_EVENT_NOUN = (
    r"call|fetch|request|lookup|look-?up|query|connection|attempt|access|download|"
    r"visit|hit|read|network\s+call|web\s+call|http\s+call|api\s+call"
)
_EVENT_DENIAL_RE = re.compile(
    rf"\b(?:no|zero|not\s+a\s+single)\s+(?:\w+\s+){{0,3}}?(?:{_EVENT_NOUN})s?\b"
    rf"[^.\n]{{0,40}}?\b(?:occurred|happened|was\s+made|were\s+made|took\s+place|was\s+sent|"
    rf"went\s+out|was\s+issued|was\s+performed|ran)\b",
    re.IGNORECASE,
)
# The denial must be about what THIS AGENT did. Requiring the agent as the subject ahead of the
# negation is what separates "I did not use the web" from "The build does not use GitHub Actions" —
# measured as a false positive on the first run, where the second sentence was rewritten into a
# confession of a fetch that had nothing to do with it. Up to two filler words are allowed
# ("I certainly never tried") but not a whole clause, which is where another subject would hide.
_SELF_SUBJECT = r"i|we|vool|vool|the\s+runtime|the\s+assistant|the\s+agent|the\s+model"
_VERB_DENIAL_RE = re.compile(
    rf"\b(?:{_SELF_SUBJECT})\b\s+(?:\w+\s+){{0,2}}?(?:{_NEGATION})\b[^.\n]{{0,40}}?\b(?:{_ACCESS_VERB})\b",
    re.IGNORECASE,
)
# The impersonal passive: "No external sources were accessed in this conversation."
#
# Produced verbatim by qwen3:8b on the live acceptance drive, one turn after a `web.fetch` to
# github.com had been attempted and failed — the production defect reproducing in a shape neither
# regex above could see. It has no first-person subject, so the self-report gate rejects it, and
# "sources ... were accessed" is neither an access-verb clause nor an event-noun clause.
#
# The quantifier is what keeps this from swallowing ordinary prose: it requires an explicit
# no/none/zero attached to a RUNTIME-ACTION noun. "The build does not use GitHub Actions" has no
# such quantifier and stays untouched. File nouns are deliberately absent — "no files were deleted"
# is `action_honesty_validator`'s existing vocabulary and two layers rewriting one sentence is
# worse than either alone.
_PASSIVE_ACTION_NOUN = (
    r"external\s+sources?|sources?|sites?|pages?|urls?|endpoints?|servers?|hosts?|"
    r"web\s+pages?|network\s+calls?|http\s+calls?|api\s+calls?|web\s+requests?|"
    r"calls?|fetches|requests?|lookups?|queries|connections?|downloads?"
)
_PASSIVE_ACCESS_PARTICIPLE = (
    r"accessed|contacted|used|fetched|read|opened|reached|queried|called|hit|visited|"
    r"touched|downloaded|retrieved|consulted|requested|made|issued|performed|occurred|happened"
)
_PASSIVE_DENIAL_RE = re.compile(
    rf"\b(?:no|none\s+of\s+the|zero|not\s+a\s+single)\s+(?:\w+\s+){{0,2}}?(?:{_PASSIVE_ACTION_NOUN})\b"
    rf"[^.\n]{{0,30}}?\b(?:was|were|has\s+been|have\s+been|had\s+been)?\s*"
    rf"(?:{_PASSIVE_ACCESS_PARTICIPLE})\b",
    re.IGNORECASE,
)
# Active voice with the quantifier on the OBJECT: "VOOL made no network calls to GitHub."
# The agent is the subject and the negation sits after the verb, so neither the verb form (which
# wants negation-then-verb) nor the passive form (which wants a participle) sees it. Still gated on
# the agent as subject and on an explicit no/zero attached to a runtime-action noun, so "the build
# makes no network calls" and "there is no cache" stay untouched.
_ACTIVE_QUANTIFIED_DENIAL_RE = re.compile(
    rf"\b(?:{_SELF_SUBJECT})\b\s+(?:\w+\s+){{0,2}}?"
    rf"(?:made|make|makes|issued|issue|issues|sent|send|sends|performed|performs|"
    rf"initiated|initiates|placed|places|opened|opens|did)\s+"
    rf"(?:no|zero|not\s+a\s+single)\s+(?:\w+\s+){{0,2}}?(?:{_PASSIVE_ACTION_NOUN})\b",
    re.IGNORECASE,
)
# Fronted negation: "At no point did I contact github.com." / "Never did we fetch that page."
# The verb form requires the agent BEFORE the negation; here the order is inverted, which is
# ordinary English and was simply unreachable.
_FRONTED_NEGATION_DENIAL_RE = re.compile(
    rf"\b(?:at\s+no\s+point|never|not\s+once|at\s+no\s+time)\b\s+"
    rf"(?:did|do|does|have|has|had|was|were|will)?\s*"
    rf"\b(?:{_SELF_SUBJECT})\b[^.\n]{{0,30}}?\b(?:{_ACCESS_VERB})\b",
    re.IGNORECASE,
)

# Framing that makes a sentence somebody else's action, a hypothetical, an offer, or a rule rather
# than a first-person report about this turn. Checked on the claim's OWN sentence.
_NOT_A_SELF_REPORT_RE = re.compile(
    r"\byou\s+(?:did|didn'?t|never|have|haven'?t|should|could|can|may|might|need|want|must)\b|"
    r"\b(?:if|unless|whenever|when|should)\s+(?:you|we|i|it|they|a|an|the)\b|"
    r"\b(?:would|could|should|will|won'?t|can'?t|cannot|shall|may|might)\s+(?:not\s+)?"
    r"(?:have\s+)?(?:try|attempt|call|fetch|access|read|open|use|reach|browse|visit|download)\b|"
    r"\b(?:i\s+)?(?:can'?t|cannot|am\s+not\s+able\s+to|do\s+not\s+have\s+(?:the\s+)?"
    r"(?:ability|permission|access))\b|"
    r"\bin\s+general\b|\bby\s+default\b|\bnormally\b|\btypically\b",
    re.IGNORECASE,
)

# --- Subjects ----------------------------------------------------------------------------------
_REMOTE_SUBJECT_RE = re.compile(
    r"\b(?:the\s+)?(?:web|internet|network|online|web\s+search|external\s+(?:source|site|service|resource)s?|"
    r"outside\s+(?:source|world)s?|remote\s+(?:source|server|host)s?|any(?:thing)?\s+external)\b",
    re.IGNORECASE,
)
# A named external source: a URL, a bare domain, or a bare brand token that the trace also names.
_URL_OR_DOMAIN_RE = re.compile(r"\b((?:https?://)?[a-z0-9][\w-]*(?:\.[a-z0-9][\w-]*)+(?:/[^\s`'\"),;]*)?)", re.IGNORECASE)
_BRAND_TOKEN_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9-]{2,30})\b")
# A file the sentence names. Same extension gate the inspection guard uses: an unknown extension is
# ambiguity, and dotted code expressions (`blob.startswith`) are everywhere in a report.
_FILENAME_RE = re.compile(r"[`'\"]?((?:[\w\-.]+/)*[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,5})[`'\"]?")
# A generic denial of having read something, with no filename: "I never tried to read the file."
_GENERIC_READ_RE = re.compile(
    r"\b(?:read|open(?:ed)?|access(?:ed)?|look(?:ed)?\s+at)\b\s+(?:the|that|any|this|it|your)\s*"
    r"(?:file|files|document|source|contents?|path)?\b",
    re.IGNORECASE,
)
# Words that read as brands in a denial but name no external source. Without this, "I never tried
# anything unusual" resolves "unusual" to a host and the sentence gets judged against the trace.
_BRAND_STOPWORDS = frozenset(
    {
        "the", "and", "any", "anything", "all", "that", "this", "with", "from", "for", "was",
        "were", "not", "never", "did", "does", "have", "has", "had", "you", "your", "our", "its",
        "them", "they", "there", "here", "what", "when", "where", "which", "while", "would",
        "could", "should", "call", "called", "calls", "fetch", "fetched", "read", "reading",
        "tried", "trying", "attempt", "attempted", "using", "used", "access", "accessed",
        "request", "requested", "query", "queried", "visit", "visited", "open", "opened",
        "file", "files", "tool", "tools", "command", "commands", "page", "pages", "site",
        "sites", "source", "sources", "server", "servers", "turn", "session", "runtime",
        "model", "provider", "anywhere", "nothing", "none", "only", "just", "actually",
        "external", "remote", "online", "internet", "network", "web",
    }
)

# --- Provider attestation ------------------------------------------------------------------------
# A claim that the PROVIDER vouches for which model served the turn. The distinction against "the
# runtime selected X" is the entire point: one is a statement about a source the runtime does not
# have, the other is a statement about its own routing, which it does have.
_ATTESTATION_RE = re.compile(
    r"\bprovider[-\s]?attested\b|"
    r"\b(?:the\s+)?provider\s+(?:says|said|reports?|reported|confirms?|confirmed|attests?|attested|"
    r"verified|verifies|states?|stated|tells?\s+me|told\s+me)\b|"
    r"\battested\s+by\s+(?:the\s+)?(?:provider|upstream|vendor|api)\b|"
    r"\b(?:confirmed|verified|attested)\s+by\s+(?:the\s+)?(?:provider|upstream|vendor)\b|"
    r"\baccording\s+to\s+(?:the\s+)?provider\b",
    re.IGNORECASE,
)
# The runtime's own, legitimate wording. The repair emits this, so the binder must recognise it and
# not judge its own output an unsourced claim on a second pass.
_ATTESTATION_DISCLAIMED_RE = re.compile(
    r"\b(?:no|not\s+have|without|lack(?:s|ing)?|do\s+not\s+have|don'?t\s+have|cannot\s+(?:confirm|verify))\b"
    r"[^.\n]{0,60}?\b(?:independent\s+)?(?:provider\s+)?attestation\b|"
    r"\bnot\s+independently\s+attested\b",
    re.IGNORECASE,
)


# A question that is explicitly about the whole conversation rather than the turn in front of it.
# INVARIANT 6 cuts both ways: binding last turn's evidence to a question about this turn is wrong,
# and refusing to look past this turn when the user asked about the session is equally wrong.
_WHOLE_CONVERSATION_RE = re.compile(
    r"\b(?:this|the|our|entire|whole|full)\s+(?:conversation|session|chat|thread|history)\b|"
    r"\b(?:at\s+any\s+point|ever|so\s+far|up\s+to\s+now|until\s+now|previously|earlier\s+today|"
    r"in\s+any\s+(?:turn|message)|across\s+(?:all\s+)?turns)\b",
    re.IGNORECASE,
)
# ...and the opposite: wording that pins the question to the turn in front of the user. Explicit
# turn scope wins, because "did you call GitHub on this turn" is a narrower question than the
# session words that may also appear around it.
_THIS_TURN_RE = re.compile(
    r"\b(?:this|the\s+current|that\s+last|the\s+last|just\s+now)\s+(?:turn|request|message|reply|answer|response)\b|"
    r"\bjust\s+(?:now|then)\b|\bright\s+now\b",
    re.IGNORECASE,
)


def question_covers_whole_conversation(user_input: str) -> bool:
    """Whether the user asked about the session rather than this turn. Defaults to the turn."""

    text = str(user_input or "")
    if not text.strip() or _THIS_TURN_RE.search(text):
        return False
    return bool(_WHOLE_CONVERSATION_RE.search(text))


@dataclass(frozen=True)
class ClaimFinding:
    kind: str
    subject: str
    verdict: str
    sentence: str
    replacement: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "verdict": self.verdict,
            "detail": self.detail,
            "original": self.sentence[:240],
            "replacement": self.replacement,
        }


@dataclass(frozen=True)
class BindingOutcome:
    checked: bool = False
    response: str = ""
    findings: tuple[ClaimFinding, ...] = ()

    @property
    def applied(self) -> bool:
        return any(f.verdict in {VERDICT_CONTRADICTED, VERDICT_UNPROVABLE} for f in self.findings)

    @property
    def contradicted(self) -> bool:
        return any(f.verdict == VERDICT_CONTRADICTED for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "applied": self.applied,
            "contradicted": self.contradicted,
            "findings": [f.as_dict() for f in self.findings],
        }


def _sentences(text: str) -> list[str]:
    return [part for part in _SENTENCE_SPLIT_RE.split(str(text or "")) if part.strip()]


def _denial_subjects(sentence: str, evidence: TurnEvidence) -> list[tuple[str, str]]:
    """What a denial is denying, as (kind, subject) pairs. Empty means "not a checkable claim".

    A brand token is only accepted as an external source when the trace itself names that host.
    Without that gate every capitalised word in a denial would become a source to check, and the
    binder would spend its time judging sentences about nothing.
    """

    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, subject: str) -> None:
        key = (kind, subject.lower())
        if subject and key not in seen:
            seen.add(key)
            found.append((kind, subject))

    for match in _URL_OR_DOMAIN_RE.finditer(sentence):
        label = host_of(match.group(1))
        if label:
            add(SUBJECT_HOST, label)

    known_hosts = {a.host for a in evidence.conversation_attempts if a.host}
    for match in _BRAND_TOKEN_RE.finditer(sentence):
        token = match.group(1).lower()
        if token in _BRAND_STOPWORDS or token not in known_hosts:
            continue
        add(SUBJECT_HOST, token)

    for match in _FILENAME_RE.finditer(sentence):
        candidate = match.group(1)
        stem, _, extension = candidate.rpartition(".")
        if stem and extension.lower() in FILE_SUFFIXES:
            add(SUBJECT_PATH, candidate)

    if _REMOTE_SUBJECT_RE.search(sentence):
        add(SUBJECT_REMOTE, "the web")
    if not found and _GENERIC_READ_RE.search(sentence):
        add(SUBJECT_READ, "a file")
    return found


def _matching_attempts(kind: str, subject: str, evidence: TurnEvidence, *, whole_conversation: bool):
    if kind == SUBJECT_HOST:
        return evidence.attempts_against_host(subject, whole_conversation=whole_conversation)
    if kind == SUBJECT_REMOTE:
        return evidence.remote_attempts(whole_conversation=whole_conversation)
    if kind == SUBJECT_PATH:
        return evidence.attempts_against_target(subject, whole_conversation=whole_conversation)
    if kind == SUBJECT_READ:
        return tuple(
            a
            for a in evidence.attempts_in_scope(whole_conversation=whole_conversation)
            if any(word in a.intent.lower() for word in ("read", "open", "cat", "view", "file"))
        )
    return ()


def _scope_for(kind: str) -> str:
    # Only the remote account is complete enough today to prove a negative; see
    # `runtime_evidence._remote_account_is_complete`. A file-read account has no equivalent
    # exhaustive counter, so its negatives stay unprovable rather than being waved through.
    return SCOPE_REMOTE if kind in {SUBJECT_HOST, SUBJECT_REMOTE} else ""


def _subject_label(kind: str, subject: str) -> str:
    if kind == SUBJECT_REMOTE:
        return "a web call"
    if kind == SUBJECT_HOST:
        return f"a {subject} call"
    if kind == SUBJECT_PATH:
        return f"a read of {subject}"
    return "a file read"


def _chronology(matching: tuple[Any, ...], evidence: TurnEvidence) -> str:
    """Name what happened in the order it happened, keeping retry generations apart.

    INVARIANT 5 and Regression 5. A first attempt that tried GitHub and failed, followed by a
    second that stayed local, are two facts about one turn; flattening them produces a sentence
    true of neither. The span is taken from the WHOLE turn, not only from the attempts that matched
    the denial — otherwise the repair reports the failed fetch with nothing to place it against and
    the reader cannot tell that the turn went on to succeed by another route.

    With one generation there is no earlier/later to report, and claiming one would be a fabricated
    ordering; the wording degrades to plain chronology instead.
    """

    generations = evidence.generations(evidence.attempts)
    if len(generations) < 2:
        return _verb_phrase(sorted(matching, key=lambda a: a.sequence), tried=True)

    parts: list[str] = []
    for position, generation in enumerate(generations):
        when = "an earlier attempt" if position < len(generations) - 1 else "the latest attempt"
        in_generation = sorted(
            (a for a in matching if a.generation == generation), key=lambda a: a.sequence
        )
        if in_generation:
            parts.append(f"{when} (generation {generation}) {_verb_phrase(in_generation, tried=True)}")
            continue
        # This generation did not touch the denied subject. Saying so — with the intents it DID
        # run — is what makes the repair a chronology rather than an accusation.
        others = sorted(
            (a for a in evidence.attempts if a.generation == generation), key=lambda a: a.sequence
        )
        if others:
            parts.append(f"{when} (generation {generation}) {_verb_phrase(others, tried=False)}")
    return "; ".join(parts)


def _verb_phrase(attempts: list[Any], *, tried: bool) -> str:
    verb = "tried" if tried else "ran"
    described = [a.describe() if tried else (a.intent or a.target or "an unnamed tool") for a in attempts]
    if not described:
        return f"{verb} nothing recorded"
    if len(described) == 1:
        return f"{verb} {described[0]}"
    return f"{verb} " + ", ".join(described[:-1]) + f", and {described[-1]}"


def _where_the_evidence_lives(attempts: tuple[Any, ...], evidence: TurnEvidence) -> str:
    """Name the turn the evidence actually sits in.

    A repair that answers a conversation-scoped denial with "the durable execution record for this
    turn" asserts something the evidence does not say: the attempt may be two turns back, and the
    current turn's record may be empty. Getting this wrong replaces one false claim about the trace
    with another, which is the failure mode this whole layer exists to stop.
    """

    current = [a for a in attempts if a.turn_id and a.turn_id == evidence.turn_id]
    earlier = [a for a in attempts if a.turn_id and a.turn_id != evidence.turn_id]
    unplaced = [a for a in attempts if not a.turn_id]
    if earlier and not current:
        return "an earlier turn in this conversation contains that attempt"
    if earlier and current:
        return "this turn and an earlier turn in this conversation both contain attempts"
    if current:
        return "the attempt is in the durable execution record for this turn"
    if unplaced:
        # Recorded, but the runtime cannot place it in a turn. Saying which turn would be a guess.
        return "the attempt is in this conversation's durable execution record"
    return "the attempt is in this conversation's durable execution record"


def _repair_denial(
    kind: str,
    subject: str,
    verdict: str,
    attempts: tuple[Any, ...],
    evidence: TurnEvidence,
) -> tuple[str, str]:
    if verdict == VERDICT_CONTRADICTED:
        detail = f"{len(attempts)} recorded attempt(s) contradict the denial"
        return (
            f"Activity shows {_chronology(attempts, evidence)}. I cannot claim otherwise — "
            f"{_where_the_evidence_lives(attempts, evidence)}.",
            detail,
        )
    return (
        f"I cannot verify from runtime evidence whether {_subject_label(kind, subject)} occurred "
        f"on this turn.",
        "no matching evidence and no complete action scope to prove the negative",
    )


def _check_denial(sentence: str, evidence: TurnEvidence, *, whole_conversation: bool) -> ClaimFinding | None:
    if not (
        _VERB_DENIAL_RE.search(sentence)
        or _EVENT_DENIAL_RE.search(sentence)
        or _PASSIVE_DENIAL_RE.search(sentence)
        or _ACTIVE_QUANTIFIED_DENIAL_RE.search(sentence)
        or _FRONTED_NEGATION_DENIAL_RE.search(sentence)
    ):
        return None
    if _NOT_A_SELF_REPORT_RE.search(sentence):
        return None  # a capability statement, a rule, an offer, or something the user did
    subjects = _denial_subjects(sentence, evidence)
    if not subjects:
        return None
    for kind, subject in subjects:
        attempts = _matching_attempts(kind, subject, evidence, whole_conversation=whole_conversation)
        if attempts:
            replacement, detail = _repair_denial(kind, subject, VERDICT_CONTRADICTED, attempts, evidence)
            return ClaimFinding(
                kind="tool_action_denial",
                subject=f"{kind}:{subject}",
                verdict=VERDICT_CONTRADICTED,
                sentence=sentence,
                replacement=replacement,
                detail=detail,
            )
    # Nothing in the trace matched. Whether that PROVES the denial depends entirely on whether the
    # runtime held a complete account — INVARIANT 2. Absence of a receipt is not absence of an act.
    kind, subject = subjects[0]
    scope = _scope_for(kind)
    if scope and evidence.can_prove_absence(scope, whole_conversation=whole_conversation):
        return ClaimFinding(
            kind="tool_action_denial",
            subject=f"{kind}:{subject}",
            verdict=VERDICT_SUPPORTED,
            sentence=sentence,
            replacement=sentence,
            detail="complete action scope for this turn proves the negative",
        )
    # A claim about the whole conversation that the turn's own account CAN settle. Measured on the
    # live drive: the turn was provably web-free, the model said "no external sources were accessed
    # in this conversation", and the repair answered only "I cannot verify" — true, but it discarded
    # a fact the runtime actually held. Report the provable half rather than nothing; a narrower
    # true statement beats a broader empty one.
    if whole_conversation and scope and evidence.can_prove_absence(scope, whole_conversation=False):
        return ClaimFinding(
            kind="tool_action_denial",
            subject=f"{kind}:{subject}",
            verdict=VERDICT_UNPROVABLE,
            sentence=sentence,
            replacement=(
                f"On this turn there was no {_subject_label(kind, subject).removeprefix('a ')}, and the "
                f"runtime's account of this turn is complete enough to say so. I cannot establish "
                f"that for the earlier turns of this conversation from runtime evidence."
            ),
            detail="turn-scoped absence proven; conversation-scoped absence not provable",
        )
    replacement, detail = _repair_denial(kind, subject, VERDICT_UNPROVABLE, (), evidence)
    return ClaimFinding(
        kind="tool_action_denial",
        subject=f"{kind}:{subject}",
        verdict=VERDICT_UNPROVABLE,
        sentence=sentence,
        replacement=replacement,
        detail=detail,
    )


def _check_attestation(sentence: str, evidence: TurnEvidence) -> ClaimFinding | None:
    if not _ATTESTATION_RE.search(sentence):
        return None
    if _ATTESTATION_DISCLAIMED_RE.search(sentence):
        return None  # the reply is already saying it has no attestation, which is the truth
    provenance = evidence.provenance
    if provenance.has_provider_attestation:
        return ClaimFinding(
            kind="model_provenance",
            subject=SUBJECT_ATTESTATION,
            verdict=VERDICT_SUPPORTED,
            sentence=sentence,
            replacement=sentence,
            detail=f"attested by {provenance.attestation_source}",
        )
    selected = provenance.runtime_selected_model or provenance.requested_model
    named = f"`{selected}`" if selected else "the model it routed to"
    return ClaimFinding(
        kind="model_provenance",
        subject=SUBJECT_ATTESTATION,
        verdict=VERDICT_CONTRADICTED,
        sentence=sentence,
        replacement=(
            f"The runtime selected {named}, but I do not have independent provider attestation "
            f"for which model actually served this turn."
        ),
        detail="no provider-attestation source is carried by the runtime",
    )


def bind_response_to_evidence(
    response: str,
    evidence: TurnEvidence,
    *,
    whole_conversation: bool = False,
) -> BindingOutcome:
    """Repair the sentences that contradict the trace; leave every other sentence alone.

    Sentence-level rather than whole-response, and that is the smallest architecture consistent
    with the existing answer binder: a reply that contains one false denial and four correct
    paragraphs should lose the denial, not the paragraphs.

    ``whole_conversation`` widens the window from this turn to the session, for a question that is
    explicitly about the conversation. It is never the default: binding last turn's GitHub call to
    a question about this turn is the same class of error as denying it.
    """

    text = str(response or "")
    if not text.strip():
        return BindingOutcome(checked=False, response=text)
    # A typed refusal is the RUNTIME's own statement of what it declined and why -- deterministic
    # wording, often quoting the provider's own typed error. Judging it as a model's attestation
    # claim rewrites the actionable reason the operator was refused: measured 2026-09-17, a pinned
    # build's "provider said: {Reasoning is mandatory...}" quote matched the attestation pattern
    # and the whole first line became a non-sequitur about model attestation. Runtime refusals are
    # already trace-backed truth; there is nothing here to repair.
    try:
        from core.grounding_publication import is_typed_refusal_text

        if is_typed_refusal_text(text):
            return BindingOutcome(checked=False, response=text)
    except Exception:
        pass
    # No identified turn means no window to judge against. Every record would be unattributed and
    # every denial "unprovable", which would rewrite honest answers wholesale on any lane that does
    # not carry a turn id. Refusing to judge is the correct behaviour, not a gap.
    if not evidence.turn_id:
        return BindingOutcome(checked=False, response=text)

    findings: list[ClaimFinding] = []
    rebuilt = text
    for sentence in _sentences(text):
        stripped = sentence.strip()
        # The claim's own wording is the most direct statement of what it is claiming ABOUT, so it
        # outranks the question's scope. "No external sources were accessed in this conversation"
        # is a claim about the session even if the question was not; "I did not use the web on this
        # turn" is a claim about the turn even if the question was about the session.
        window = whole_conversation
        if _THIS_TURN_RE.search(stripped):
            window = False
        elif _WHOLE_CONVERSATION_RE.search(stripped):
            window = True
        finding = _check_denial(stripped, evidence, whole_conversation=window) or _check_attestation(
            stripped, evidence
        )
        if finding is None:
            continue
        findings.append(finding)
        if finding.verdict == VERDICT_SUPPORTED:
            continue
        rebuilt = rebuilt.replace(stripped, finding.replacement, 1)
    return BindingOutcome(checked=True, response=rebuilt, findings=tuple(findings))


__all__ = [
    "SUBJECT_ATTESTATION",
    "SUBJECT_HOST",
    "SUBJECT_PATH",
    "SUBJECT_READ",
    "SUBJECT_REMOTE",
    "VERDICT_CONTRADICTED",
    "VERDICT_SUPPORTED",
    "VERDICT_UNPROVABLE",
    "BindingOutcome",
    "ClaimFinding",
    "bind_response_to_evidence",
    "question_covers_whole_conversation",
]
