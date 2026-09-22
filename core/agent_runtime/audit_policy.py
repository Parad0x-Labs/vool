"""What an audit turn is permitted to do, as state — computed once, carried through every step.

The incident: the operator asked for a read-only audit, and the runtime wrote a generated test file
into the audited workspace and executed it. Nothing malfunctioned. Permission simply was not a
thing the code held. It was prose in a prompt, and `_prove_finding` ran because the code path
reached it. `AuditExecutionPolicy` makes permission a value instead.

The SCALPEL incident (2026-08-06): a second, narrower failure of the same kind. The old policy had
exactly one axis for "may write and run a proof" (`workspace_writes and command_execution`), and the
regex that read "read-only" out of the operator's own words fired on "safe read-only commands:
**allowed**" — a grant — identically to "read-only audit, no writes" — a restriction. The regex
could not tell them apart because it never looked at what came after the word it matched. Worse:
even a fixed regex could not have produced a correct policy for that exact request, because
"repository modifications: forbidden" + "temporary isolated inputs outside repository: allowed" is
not representable by one write/execute pair — it needs "no repository writes" and "yes to execution
and to writes outside the repository" to be independently true at once.

So there are five axes, matched independently and combined without inference from one to another:

* ``repository_write``          — may create or modify files INSIDE the audited repository
* ``repository_test_creation``  — may create a NEW test file INSIDE the audited repository
* ``read_only_commands``        — may invoke a command at all, provided the command itself does not
                                   mutate anything (existing tests, greps, a generated reproduction)
* ``temporary_external_files``  — may write files OUTSIDE the audited repository, in a
                                   runtime-controlled temporary location
* ``isolated_reproduction``     — may run a full reproduction using only external temp files and
                                   read-only commands, touching nothing inside the repository

Plus two carried over unchanged from the first incident:

* ``network_research``   — may reach the network for adaptive research
* ``model_substitution`` — may answer with a model other than the pinned one (never granted by
                            parsing; only ever true if a caller constructs a policy directly)

The default for an audit is all axes DENIED. An unauthorized turn returns a labelled
`candidate_unproven` rather than take the liberty. Authorization is widened only by an explicit
later request, and only along the axes that request actually names.
"""
from __future__ import annotations

import os
import posixpath
import re
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

# ---------------------------------------------------------------------------------------------
# Clause-scoped, polarity-aware parsing.
#
# The first incident's fix added one regex per concept ("read-only", "prove it", "local only") and
# matched each against the WHOLE message. That is exactly what broke on "safe read-only commands:
# allowed" — the regex found its keyword and had no way to notice the word "allowed" sitting right
# next to it. This does not add a sixth regex to the same flat list; it changes HOW a match is
# turned into a verdict:
#
#   1. Split the input into clauses (sentence/line/semicolon boundaries).
#   2. For each axis, find where its SUBJECT is mentioned inside a clause.
#   3. Look at the text immediately before the subject (a prefix negation: "do not", "without",
#      "never") and immediately after it (a suffix verdict: ": allowed", "is forbidden") to decide
#      whether THIS mention is a grant or a restriction — never a whole-message keyword search.
#   4. If two clauses disagree about the same axis, that is a genuine conflict: the axis resolves to
#      DENIED (the narrower reading) and the conflict is recorded, not silently overwritten by
#      whichever pattern happened to run last.
#
# This generalizes to any similarly-shaped request, not only this one fixture's exact wording.
# ---------------------------------------------------------------------------------------------

_CLAUSE_SPLIT_RE = re.compile(r"[.;\n]+|(?<=[a-z])\s+(?=and\s+(?:do|don))", re.IGNORECASE)

_GRANT_SUFFIX_RE = re.compile(
    r"^\s*[:\-]?\s*(?:is\s+|are\s+)?(allowed|permitted|granted|authorized|authorised|ok|okay|"
    r"fine|yes|true|may\s+proceed)\b",
    re.IGNORECASE,
)
_DENY_SUFFIX_RE = re.compile(
    r"^\s*[:\-]?\s*(?:is\s+|are\s+)?(forbidden|denied|prohibited|disallowed|not\s+allowed|"
    r"not\s+permitted|no|false|never)\b",
    re.IGNORECASE,
)
# A negation cue in the words directly BEFORE the axis's own subject phrase: "do not run commands",
# "without writing", "never modify the repository". Matched against the text preceding the subject
# match, and only within roughly one clause of lookback (the caller passes just the prefix text).
_DENY_PREFIX_RE = re.compile(
    r"\b(?:do\s+not|don'?t|without|never|must\s+not|shall\s+not|no)\s+[\w\s]{0,12}$",
    re.IGNORECASE,
)

# Each axis's SUBJECT pattern — what counts as "this clause is talking about this axis" — kept
# independent per axis so a clause about one axis cannot leak into another's verdict.
_AXIS_SUBJECTS: dict[str, re.Pattern[str]] = {
    "repository_write": re.compile(
        r"\brepository\s+(?:modif\w*|writ\w*|edit\w*|chang\w*)|"
        r"\b(?:modify|modifying|edit|editing|change|changing|write|writing|touch|touching)\s+"
        r"(?:the\s+)?(?:repository|repo|workspace|source|production)(?:\s+(?:files?|code))?\b|"
        r"\brepository\s+writes?\b",
        re.IGNORECASE,
    ),
    "repository_test_creation": re.compile(
        # ARGUS repair D2, 2026-08-06: the old pattern allowed at most ONE modifier word between
        # the verb and "test(s)" (`(?:a\s+|new\s+)?`), so "create A NEW test file" — two stacked
        # modifiers plus a trailing "file" — matched NOTHING. A clause the operator meant as an
        # explicit denial was then invisible to `mentioned`, and the bare-"prove it" fallback
        # silently granted the axis it had just been told to deny. Widened to: any determiner/
        # adjective run before test(s) (bounded, not unlimited, so it still cannot leak into an
        # unrelated later sentence), an optional trailing "file(s)", and a bare "repository tests"
        # noun phrase with no verb at all ("No repository tests.").
        # The verb list also carries `prove`/`reproduc` — "prove the bug with a failing test" IS a
        # test-based proof request in this domain's own convention (every pre-repair test fixture
        # phrases it this way), not a bare unqualified "prove it"; only a clause with NO "test" word
        # in it at all stays governed by the safer default below.
        r"\brepository\s+test\s+creation\b|"
        r"\b(?:creat|writ|add|generat|prove|proves|proved|proving|reproduc|demonstrat)\w*\b"
        r"[\w\s'-]{0,45}?\btests?\b(?:\s+files?\b)?(?!\s+(?:outside|external))|"
        r"\btest\s+creation\b|"
        r"\brepository\s+tests?\b(?!\s+(?:outside|external))",
        re.IGNORECASE,
    ),
    "read_only_commands": re.compile(
        r"\bread[\s\-]only\s+commands?\b|\bsafe\s+commands?\b|\bcommands?\s+(?:execution|allowed)\b|"
        r"\brun(?:ning)?\b[\w\s]{0,15}?\bcommands?\b|\bexecut\w*\b[\w\s]{0,15}?\bcommands?\b",
        re.IGNORECASE,
    ),
    "temporary_external_files": re.compile(
        r"\btemporary\s+(?:isolated\s+)?(?:external\s+)?(?:test\s+)?(?:inputs?|files?|artifacts?)"
        r"(?:\s+outside(?:\s+(?:the\s+)?repository)?)?\b|"
        r"\b(?:isolated|external)\s+(?:inputs?|files?|artifacts?)\s+outside\b|"
        r"\b(?:inputs?|files?)\s+outside\s+(?:the\s+)?repository\b",
        re.IGNORECASE,
    ),
    "isolated_reproduction": re.compile(
        r"\b(?:isolated|non[\s\-]mutating)\s+reproduction\b|\bisolated\s+repro\w*\b|"
        r"\breproduc\w*\b|\bdemonstrate\b|\bverify\s+(?:it|the\s+bug)\b|\bprove\b|\bproof\b",
        re.IGNORECASE,
    ),
}

# A GENERIC mutation ban with no stated object ("do not modify anything", "don't touch it",
# "without editing") reads as "the whole repository", by the same logic the first incident's fix
# used — but only when no MORE SPECIFIC axis word follows (a ban on "modifying tests" or "writing
# files outside the repository" is that axis's own signal, not this blanket one, so the negative
# lookahead defers to the per-axis patterns above whenever a specific object is actually named).
_BLANKET_NO_MODIFY_RE = re.compile(
    r"\b(?:do\s+not|don'?t|without|never)\s+(?:modify|modifying|change|changing|edit|editing|"
    r"writ(?:e|ing)|touch|touching)\b"
    r"(?!\s+(?:the\s+)?(?:repository|repo|workspace|source|production|tests?\b|files?\s+outside|"
    r"commands?))",
    re.IGNORECASE,
)

_LOCAL_ONLY_RE = re.compile(
    r"\blocal[\s\-]only\b|\bonly\s+(?:what(?:'s|\s+is)?\s+)?(?:on|from)\s+this\s+machine\b|"
    r"\b(?:do\s+not|don'?t|without)\s+(?:search|searching|use|using|browse|browsing)\s+"
    r"(?:the\s+)?(?:web|internet|online)\b|\boffline\b",
    re.IGNORECASE,
)


def _clause_verdict(clause: str, subject: re.Pattern[str], *, bare_mention_grants: bool = False) -> bool | None:
    """True/False/None (no mention) for one axis inside one clause, from LOCAL polarity only.

    The four permission axes (repository_write, repository_test_creation, read_only_commands,
    temporary_external_files) are naturally phrased as DECLARATIVE permission statements ("X:
    allowed", "do not do X") — a bare mention with neither cue carries no signal of its own (e.g.
    "reproduction" appearing mid-sentence for an unrelated reason).

    A proof REQUEST ("prove it", "reproduce the bug", "prove the single highest-risk bug with a
    failing test") is naturally IMPERATIVE, not declarative — the verb's presence in a directive
    sentence already IS the request; there is no "allowed" left to say. ``bare_mention_grants``
    lets that one axis treat an unqualified imperative mention as a grant, the same way the
    original incident's fix always treated a bare "prove it" as a request — while a genuine local
    deny cue (prefix or suffix) still overrides it either way.
    """
    match = subject.search(clause)
    if match is None:
        return None
    prefix = clause[: match.start()]
    suffix = clause[match.end() :]
    if _DENY_PREFIX_RE.search(prefix):
        return False
    if _GRANT_SUFFIX_RE.match(suffix):
        return True
    if _DENY_SUFFIX_RE.match(suffix):
        return False
    if bare_mention_grants:
        return True
    return None


def _axis_signal(
    body: str, clauses: list[str], axis: str, *, bare_mention_grants: bool = False
) -> tuple[bool | None, tuple[str, ...]]:
    """The resolved verdict for one axis across every clause, plus the raw conflicting clauses."""
    subject = _AXIS_SUBJECTS[axis]
    seen_true: list[str] = []
    seen_false: list[str] = []
    for clause in clauses:
        verdict = _clause_verdict(clause, subject, bare_mention_grants=bare_mention_grants)
        if verdict is True:
            seen_true.append(clause.strip())
        elif verdict is False:
            seen_false.append(clause.strip())
    if seen_true and seen_false:
        return None, tuple(seen_true + seen_false)  # conflict: caller resolves to deny
    if seen_true:
        return True, ()
    if seen_false:
        return False, ()
    return None, ()


@dataclass(frozen=True)
class AuditExecutionPolicy:
    """One normalized permission state for an audit turn — five independent axes."""

    repository_write: bool = False
    repository_test_creation: bool = False
    read_only_commands: bool = False
    temporary_external_files: bool = False
    isolated_reproduction: bool = False
    network_research: bool = False
    model_substitution: bool = False
    # Axis names where this turn's own request text contained a genuine grant/deny contradiction.
    # Each such axis was resolved to DENIED (the narrower reading), never silently to the broader one.
    conflicts: tuple[str, ...] = ()
    reason: str = "audit default: read-only, local-only, pinned model"

    @property
    def in_repo_proof_authorized(self) -> bool:
        """The original proof path: write a NEW test file inside the repo and run it there.

        Gated on ``repository_test_creation`` alone, not also ``repository_write``: the two are
        deliberately independent axes, and "do not modify production code... use a temporary test
        file" is exactly the shape that grants test creation while denying broad repository writes
        — the original incident's ``proof_artifacts_only`` scope already confined every in-repo
        proof write to a fresh ``generated/`` file, never to production content, so requiring the
        broader axis too would refuse a request that named its narrower permission correctly.
        """
        return self.repository_test_creation and self.read_only_commands

    @property
    def isolated_proof_authorized(self) -> bool:
        """The path this repair adds: write and run entirely outside the repository."""
        return (
            self.isolated_reproduction
            and self.temporary_external_files
            and self.read_only_commands
            and not self.repository_write
        )

    @property
    def proof_authorized(self) -> bool:
        """Some real proof attempt is possible this turn, by either path."""
        return self.in_repo_proof_authorized or self.isolated_proof_authorized

    @property
    def proof_write_scope(self) -> str:
        """Where a proof artifact may land: the two real answers, or 'none'."""
        if self.isolated_proof_authorized:
            return "external_temp_root"
        if self.in_repo_proof_authorized:
            return "repository_generated"
        return "none"

    # Retained for the existing tool-boundary check (`audit_tool_permission_denial` below reads
    # this directly; nothing outside this module reads the attribute), spelled the way the original
    # incident's fix named it, so a `generated/`-scoped in-repo write keeps exactly its old contract.
    @property
    def write_scope(self) -> str:
        if self.proof_write_scope == "repository_generated":
            return "proof_artifacts_only"
        return "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_write": self.repository_write,
            "repository_test_creation": self.repository_test_creation,
            "read_only_commands": self.read_only_commands,
            "temporary_external_files": self.temporary_external_files,
            "isolated_reproduction": self.isolated_reproduction,
            "network_research": self.network_research,
            "model_substitution": self.model_substitution,
            "conflicts": list(self.conflicts),
            "proof_authorized": self.proof_authorized,
            "proof_write_scope": self.proof_write_scope,
            "write_scope": self.write_scope,
            "reason": self.reason,
        }

    def remaining_proof_sentence(self) -> str:
        """What the operator must say for the proof step to be allowed to run."""
        if self.conflicts:
            return (
                "What proof remains: this turn's own request gave contradictory instructions for "
                f"{', '.join(sorted(self.conflicts))}, so the narrower (denied) reading was used and "
                "nothing was written or run. State the permission for that axis on its own, without "
                "a conflicting clause in the same message, to authorize it."
            )
        return (
            "What proof remains: a failing test written outside the audited source and executed "
            "against it. This turn was not authorized to write or run anything, so nothing was "
            'written and no command ran. Reply "prove it" — or explicitly allow read-only commands '
            'plus temporary external files for an isolated reproduction — to authorize that.'
        )


READ_ONLY_AUDIT = AuditExecutionPolicy()


def repository_test_creation_fallback_blocked(
    *, explicit_isolated: bool, resolved: dict[str, bool], mentioned: dict[str, bool]
) -> bool:
    """Whether the bare-"prove it" fallback must NOT grant `repository_test_creation` this turn.

    ARGUS repair D2/D6, 2026-08-06. Two independent reasons block it:

    * an alternative, non-repository proof path is already "on offer" this turn — either the
      operator asked for it directly (``explicit_isolated``) or ``temporary_external_files`` was
      granted outright. Falling back to an in-repo write on top of that would be an unrequested
      WIDENING, not a narrowing, of what was actually asked for (the exact D6 incident text:
      "Temporary files outside the repository are allowed... Prove it.").
    * the message denies ``repository_write`` explicitly, even without ever mentioning tests
      ("...without touching the repository") — a signal to stay out of the repository entirely,
      which the fallback must respect rather than reading silence on the test-creation axis
      specifically as room to write.

    A bare "Prove the bug you identified before fixing it." follow-up, with neither condition true,
    is NOT blocked — that is the original incident's own preserved contract: a proof request with no
    alternative on offer and no repository-wide denial authorizes the conventional in-repo test.
    """
    isolated_alternative_offered = explicit_isolated or resolved.get("temporary_external_files", False)
    repository_explicitly_off_limits = mentioned["repository_write"] and not resolved.get(
        "repository_write", False
    )
    return isolated_alternative_offered or repository_explicitly_off_limits


def derive_execution_policy(
    text: str,
    source_context: dict[str, Any] | None = None,
    *,
    prior: AuditExecutionPolicy | None = None,
) -> AuditExecutionPolicy:
    """The policy for this turn, from the operator's own words plus the turn's prior policy.

    ``prior`` is the policy the audit was opened under (from the session capsule). It carries a
    DENIED axis forward (an audit opened "do not modify anything" stays denied on the follow-up)
    but never carries a GRANTED axis forward on its own — each turn's own text must re-state what it
    authorizes for that axis, so a later, narrower message can still restrict what an earlier one
    opened.
    """
    body = " ".join(str(text or "").split())
    prior_policy = prior if prior is not None else READ_ONLY_AUDIT
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(body) if c.strip()] or [body]

    from core.agent_runtime.active_finding import REPRODUCE, classify_follow_up

    local_only_stated = bool(_LOCAL_ONLY_RE.search(body))
    network_research = prior_policy.network_research and not local_only_stated

    blanket_denied = bool(_BLANKET_NO_MODIFY_RE.search(body))

    conflicts: list[str] = []
    resolved: dict[str, bool] = {}
    mentioned: dict[str, bool] = {}
    for axis in ("repository_write", "repository_test_creation", "read_only_commands", "temporary_external_files"):
        # ARGUS repair D2, 2026-08-06: `repository_test_creation` alone gets `bare_mention_grants` —
        # an explicit, undecorated description of creating a test ("Create the smallest deterministic
        # regression test...") is itself the request, the same way `isolated_reproduction` already
        # treats a bare "prove"/"reproduce" verb as one. `repository_write` deliberately does NOT get
        # this: the broadest, most dangerous axis requires an explicit positive grant ("...: allowed")
        # and is never inferred from a bare mention — see the safer-default note below.
        verdict, conflicting = _axis_signal(
            body, clauses, axis, bare_mention_grants=(axis == "repository_test_creation")
        )
        axis_blanket = blanket_denied and axis in ("repository_write", "repository_test_creation")
        mentioned[axis] = bool(conflicting) or verdict is not None or axis_blanket
        if conflicting:
            conflicts.append(axis)
            resolved[axis] = False
        elif verdict is not None:
            resolved[axis] = verdict
        elif axis_blanket:
            resolved[axis] = False
        else:
            resolved[axis] = getattr(prior_policy, axis) if axis in ("repository_write", "repository_test_creation") else False

    # `isolated_reproduction`'s subject pattern also matches bare "prove"/"reproduce"/"demonstrate" —
    # a general proof REQUEST, not necessarily an isolated one. What follows from that one request is
    # decided per AXIS, by what else is true this turn — never by a second regex for this exact
    # phrasing, and never as one all-or-nothing bundle:
    #   - explicit isolated/external/non-mutating language grants the isolated axis directly;
    #   - a bare proof request, when the SAME message also denies repository writes but allows
    #     read-only commands and temporary external files, is a request for proof BY THE ONLY MEANS
    #     that does not violate the rest of the same message — so it grants the isolated axis too;
    #   - a bare proof request grants `read_only_commands` on its own UNLESS that axis was already
    #     explicitly resolved by the message itself.
    #
    # ARGUS repair D2, 2026-08-06 — safer default: `repository_write` is NEVER inferred from a bare
    # proof request — it requires an explicit positive grant (an ": allowed"-shaped suffix) — full
    # stop, no fallback of any kind. `repository_test_creation` keeps a NARROWER fallback than
    # before: a proof request grants it only when the axis was not otherwise mentioned THIS turn
    # (denied or granted) AND no alternative isolated/external proof mechanism is already explicitly
    # available. That second condition is what the old code was missing — "Temporary files outside
    # the repository are allowed. Prove it." never mentions repository_test_creation at all, but it
    # DOES explicitly grant the isolated alternative, so falling back to an in-repo write on top of
    # that is exactly the unwarranted broadening ARGUS flagged. Where no alternative is on offer — a
    # bare "Prove the bug you identified before fixing it." follow-up with nothing else granted —
    # the fallback still applies: this is the original incident's own contract (a bare proof request
    # after a read-only audit turn authorizes the conventional in-repo test), preserved for the one
    # case where it does not smuggle in a redundant, wider grant. `read_only_commands` keeps its own
    # unconditional fallback: running a read-only command mutates nothing, so inferring it from a
    # proof request carries none of the risk the other two axes carry.
    proof_requested_verdict, proof_conflict = _axis_signal(
        body, clauses, "isolated_reproduction", bare_mention_grants=True
    )
    proof_requested = bool(proof_requested_verdict) or (
        classify_follow_up(body).action == REPRODUCE
    )
    if proof_conflict:
        conflicts.append("isolated_reproduction")

    explicit_isolated = bool(
        # ARGUS repair D2, 2026-08-06: "isolated EXTERNAL reproduction" — the ordinary variant ARGUS
        # named — did not match with "reproduction" required directly after "isolated"; an optional
        # "external " infix covers it without loosening the match to something ambiguous.
        re.search(
            r"\b(?:isolated|non[\s\-]mutating)\s+(?:external\s+)?(?:reproduction|repro\w*)\b",
            body,
            re.IGNORECASE,
        )
    )
    if proof_requested and not mentioned["read_only_commands"]:
        resolved["read_only_commands"] = True
    if proof_requested and not mentioned["repository_test_creation"] and not repository_test_creation_fallback_blocked(
        explicit_isolated=explicit_isolated, resolved=resolved, mentioned=mentioned
    ):
        resolved["repository_test_creation"] = True

    # ARGUS repair D6, 2026-08-06: this used to also require `repo_write_denied_explicitly` — the
    # message had to separately SAY "repository writes forbidden" before an isolated proof could be
    # selected, even though `repository_write` is False by default and, after the D2 repair above,
    # is never silently inferred to True. Requiring operators to restate a denial that already holds
    # by default was the exact D6 defect ("Temporary files outside the repository are allowed. Safe
    # read-only commands are allowed. Prove it." was refused for lack of a redundant denial clause).
    # The real gate is simply: repository_write did not end up granted THIS turn.
    isolated_reproduction = explicit_isolated or (
        proof_requested
        and not resolved.get("repository_write", False)
        and resolved.get("read_only_commands", False)
        and resolved.get("temporary_external_files", False)
    )

    conflicts_t = tuple(sorted(set(conflicts)))
    if conflicts_t:
        reason = "the request's own wording conflicted on " + ", ".join(conflicts_t) + "; the narrower reading was used"
    elif resolved.get("repository_write") or isolated_reproduction:
        reason = "the request explicitly authorized proof, along the axes it actually named"
    elif any(resolved.values()):
        reason = "the request explicitly authorized some, but not all, execution axes"
    else:
        reason = prior_policy.reason if prior is not None else READ_ONLY_AUDIT.reason

    return AuditExecutionPolicy(
        repository_write=bool(resolved.get("repository_write", False)),
        repository_test_creation=bool(resolved.get("repository_test_creation", False)),
        read_only_commands=bool(resolved.get("read_only_commands", False)),
        temporary_external_files=bool(resolved.get("temporary_external_files", False)),
        isolated_reproduction=bool(isolated_reproduction),
        network_research=network_research,
        model_substitution=False,
        conflicts=conflicts_t,
        reason=reason,
    )


def narrowed_to_read_only(policy: AuditExecutionPolicy) -> AuditExecutionPolicy:
    """The same policy with every mutating axis removed — used when a gate refuses mid-turn."""
    return replace(
        policy,
        repository_write=False,
        repository_test_creation=False,
        temporary_external_files=False,
        isolated_reproduction=False,
    )


def audit_writes_permitted(source_context: dict[str, Any] | None) -> bool:
    """Whether the policy stamped on this turn's context permits ANY workspace writes.

    Read by the tool layer so a policy denial cannot be bypassed by a code path that forgot to ask.
    A turn with no audit policy stamped is not an audit turn, and keeps its existing behaviour.
    """
    blob = (source_context or {}).get("audit_execution_policy")
    if not isinstance(blob, dict):
        return True
    return bool(blob.get("repository_write")) or bool(blob.get("temporary_external_files"))


# ---------------------------------------------------------------------------------------------
# ARGUS repair D1, 2026-08-06: the isolated-external proof root, made runtime-owned instead of a
# scope NAME on the stamped policy.
#
# The defect: `proof_write_scope == "external_temp_root"` on `audit_execution_policy` unconditionally
# allowed every `workspace_write` call for the rest of the turn, because that policy dict is stamped
# on `source_context` ONCE and carried everywhere — the enforcement check never looked at what
# workspace or path the CURRENT call actually targeted. A call that reached the tool boundary with
# the ORIGINAL (audited-repository) `source_context` instead of the isolated override — a future
# bug, a model-issued tool call the stepped driver did not route through `_run_tool`, anything — was
# allowed to write into the audited repository, despite `repository_write=False`, because the string
# "external_temp_root" alone was treated as proof.
#
# The fix binds each isolated proof attempt to a runtime-owned, in-memory record that only
# `_prove_finding` can create (via `register_isolated_proof_root`, immediately after `tempfile.
# mkdtemp()`) and that a caller cannot forge by writing to `source_context` — `source_context` is
# never model-populated (see `runtime_execution_tools.py`'s own comment: the model-reachable
# `arguments` dict has its underscore-prefixed keys stripped before dispatch for exactly this
# reason), but even so this binding does not trust a bare key's PRESENCE — the run id must resolve
# a real, still-open entry in this process's own registry, and every field of that entry (proof
# root, audited repository root) is re-derived and re-canonicalized at enforcement time, never taken
# from the caller's copy.
_PROOF_ROOT_REGISTRY_LOCK = threading.Lock()
_PROOF_ROOT_REGISTRY: dict[str, IsolatedProofBinding] = {}


@dataclass(frozen=True)
class IsolatedProofBinding:
    """One isolated proof attempt's write root, canonicalized once at creation.

    Exists only in this process's memory from `register_isolated_proof_root` to
    `release_isolated_proof_root` — a stale or forged run id resolves to nothing, which the
    enforcement check below treats as an outright denial, not a fallback to trusting the caller.
    """

    run_id: str
    proof_root: str  # os.path.realpath'd external temp directory — never inside the audited repo
    audited_repository_root: str  # os.path.realpath'd audited root, "" when not resolvable
    created_at: float


def register_isolated_proof_root(proof_root: str, audited_repository_root: str) -> str:
    """Called ONLY by `_prove_finding`, once, right after it creates the temp root.

    Returns the run id the caller must stamp onto the ISOLATED `source_context` copy it passes to
    every tool call for this proof attempt (never onto the original `source_context`).
    """
    run_id = uuid.uuid4().hex
    real_root = os.path.realpath(str(proof_root or ""))
    real_audited = os.path.realpath(str(audited_repository_root or "")) if audited_repository_root else ""
    with _PROOF_ROOT_REGISTRY_LOCK:
        _PROOF_ROOT_REGISTRY[run_id] = IsolatedProofBinding(
            run_id=run_id,
            proof_root=real_root,
            audited_repository_root=real_audited,
            created_at=time.monotonic(),
        )
    return run_id


def release_isolated_proof_root(run_id: str) -> None:
    """Called ONLY by `_prove_finding`'s own cleanup, after `shutil.rmtree` on the temp root.

    Removed entirely, not merely flagged: any call arriving after this point (a straggler, a retry
    holding a stale run id) finds nothing in the registry and is denied as stale — see
    `_external_temp_root_denial` below.
    """
    with _PROOF_ROOT_REGISTRY_LOCK:
        _PROOF_ROOT_REGISTRY.pop(str(run_id or ""), None)


def _isolated_proof_binding(run_id: str) -> IsolatedProofBinding | None:
    with _PROOF_ROOT_REGISTRY_LOCK:
        return _PROOF_ROOT_REGISTRY.get(str(run_id or ""))


def isolated_proof_root_is_registered(run_id: str) -> bool:
    """Whether `run_id` still resolves to a live binding — for tests and diagnostics."""
    return _isolated_proof_binding(run_id) is not None


def isolated_proof_read_roots(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Read access to the audited source, bound to the live isolated proof only.

    The scratch project is writable; the original project must remain readable
    for imports without becoming writable or granting access to a sibling tree.
    Never accept an external root from caller-supplied context fields.
    """
    if _external_temp_root_denial(source_context, {}):
        return ()
    run_id = str((source_context or {}).get("_isolated_proof_run_id") or "").strip()
    binding = _isolated_proof_binding(run_id)
    if binding is None or not binding.audited_repository_root:
        return ()
    return (binding.audited_repository_root,)


def _external_temp_root_denial(
    source_context: dict[str, Any] | None, arguments: dict[str, Any] | None
) -> str:
    """The six required checks, in order — "" only when every one of them passes."""
    run_id = str((source_context or {}).get("_isolated_proof_run_id") or "").strip()
    if not run_id:
        return (
            "the audit execution policy claims an isolated external proof root, but this call "
            "carries no runtime proof-root binding"
        )
    binding = _isolated_proof_binding(run_id)
    if binding is None:
        return (
            "the isolated proof-root binding for this run is stale, already released, or unknown "
            "to this runtime"
        )
    proof_root = binding.proof_root
    if not proof_root or not os.path.isdir(proof_root):
        return "the bound isolated proof root no longer exists on disk"
    if binding.audited_repository_root and (
        proof_root == binding.audited_repository_root
        or proof_root.startswith(binding.audited_repository_root + os.sep)
    ):
        return "the bound proof root is inside the audited repository, which is never a valid isolated proof root"
    active_workspace = str((source_context or {}).get("workspace") or "").strip()
    if not active_workspace:
        return "this call has no active workspace, so it cannot be verified against the bound proof root"
    real_workspace = os.path.realpath(active_workspace)
    if real_workspace != proof_root:
        return "this call's active workspace does not match the runtime-bound isolated proof root"
    raw_path = str((arguments or {}).get("path") or "").replace("\\", "/").strip()
    candidate_target = raw_path if posixpath.isabs(raw_path) else posixpath.join(active_workspace, raw_path)
    real_target = os.path.realpath(candidate_target)
    if real_target != proof_root and not real_target.startswith(proof_root + os.sep):
        return "this call's target path escapes the runtime-bound isolated proof root"
    return ""


def audit_tool_permission_denial(
    intent: str,
    side_effect_class: str,
    source_context: dict[str, Any] | None,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Why an audit policy refuses this tool call, or ``""`` when it permits it.

    This is the enforcement boundary. Driver checks make the normal path cheap; this check makes a
    forgotten or future call site unable to bypass the operator's policy. A context without an
    audit policy is an ordinary turn and retains its existing behavior.

    An isolated-reproduction call runs with its OWN overridden ``source_context["workspace"]``
    (a fresh temporary directory — see ``stepped_audit._prove_finding``), carrying a runtime-issued
    ``_isolated_proof_run_id`` alongside it. The ``proof_write_scope == "external_temp_root"``
    branch below does not trust the scope name alone (ARGUS repair D1) — it re-derives and
    re-canonicalizes the bound proof root from this process's own registry and checks the CURRENT
    call's workspace and target path against it, every time. See ``_external_temp_root_denial``.
    """
    blob = (source_context or {}).get("audit_execution_policy")
    if not isinstance(blob, dict):
        return ""
    tool = str(intent or "").strip()
    effect = str(side_effect_class or "").strip()

    if tool.startswith("web.") and not bool(blob.get("network_research")):
        return "the audit execution policy denies network research"
    if effect == "read_only":
        return ""
    if effect == "workspace_write":
        scope = str(blob.get("proof_write_scope") or "none")
        if scope == "external_temp_root":
            return _external_temp_root_denial(source_context, arguments)
        if scope != "repository_generated":
            return "the audit execution policy denies workspace writes"
        raw_path = str((arguments or {}).get("path") or "").replace("\\", "/").strip()
        normalized = posixpath.normpath(raw_path).lstrip("/")
        if normalized == "generated" or normalized.startswith("generated/"):
            return ""
        return "the audit execution policy permits writes only under `generated/` proof artifacts"
    if effect in {"sandbox_command", "validation_command"}:
        if bool(blob.get("read_only_commands")):
            return ""
        return "the audit execution policy denies command execution"
    # The audit policy has explicit grants for its axes. Publishing, spending, orchestration,
    # runtime changes, builder state, and any new side-effect class have no grant and fail closed.
    return f"the audit execution policy does not authorize `{effect or 'unknown'}` side effects"
