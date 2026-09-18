"""RepoOpsRuntime -- the one execution authority behind VOOL's repository operations.

A model, skill or plugin PROPOSES ``repo.*`` calls. Only this runtime executes them, and every
local effect it runs goes back through the same production door every other tool crosses
(``core.runtime_execution_tools.execute_runtime_tool``), while every remote observation goes
through the KAS forge boundary and its VOOL-built transport. So the existing authorities apply
unchanged and are not re-implemented here: the tool registry and its contracts, the
mode/permission matrix, the effect gateway and its receipts, workspace confinement, the Blackbox
flight recorder, the fault catalog, and A6 effect reconciliation.

What this module ADDS:

* **the vertical as an enforced stage machine** -- inspect -> bind -> retrieve -> diagnose ->
  repair -> test -> review -> authorize -> push -> verify -> seal. A repair before a diagnosis,
  a push before an authorization, or a "verified" before a push are refused by the runtime
  rather than by the model's good manners;
* **exact SHA binding** -- the remote head, local head, PR base and PR head are frozen once and
  every later step is checked against them. A repository that moved underneath the session
  refuses; a CI result is read for a SHA, never for a branch name;
* **authorization binding** -- a push executes only against a live operator authorization whose
  plan hash matches the CURRENT plan byte-for-byte. A plan that changed after authorization
  invalidates it;
* **UNKNOWN is not FAILED** -- a push whose reply never arrived reserves a durable unresolved
  effect and blocks an identical retry until it reconciles, mechanically, against the remote;
* **evidence** -- the sealed receipt is assembled ONLY from journaled, executed steps. Caller-
  supplied results are in no contract schema and are refused at the door as
  ``invalid_arguments``, so prose cannot become repository truth.

State is journaled as JSON under ``VOOL_REPOOPS_DIR`` (default: the Blackbox data root), one
file per session, rewritten atomically after every change, so a session survives a restart and
``repo.receipt`` answers from the journal rather than from memory of intending to run something.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

AUTHORITY = "repo_ops_runtime"

STAGES: tuple[str, ...] = (
    "inspect",
    "bind",
    "retrieve",
    "diagnose",
    "repair",
    "test",
    "review",
    "authorize",
    "push",
    "verify",
    "seal",
)
STAGE_CANCELLED = "cancelled"
STAGE_SEALED = "sealed"

#: The tool intents a repair step may use. Anything else is refused: a repository repair that
#: needs to send an email is not a repository repair.
REPAIR_READ_INTENTS = frozenset(
    {
        "workspace.read_file",
        "workspace.list_files",
        "workspace.list_tree",
        "workspace.search_text",
        "workspace.git_status",
        "workspace.git_diff",
        "workspace.git_summary",
        "workspace.identity",
    }
)
REPAIR_COMMAND_INTENTS = frozenset(
    {"workspace.run_tests", "workspace.run_lint", "workspace.run_formatter", "sandbox.run_command"}
)
REPAIR_MUTATION_INTENTS = frozenset(
    {"workspace.write_file", "workspace.replace_in_file", "workspace.apply_unified_diff", "workspace.ensure_directory"}
)
TEST_INTENTS = frozenset({"workspace.run_tests"})

#: How long an operator's Authorize Push stays live. Short on purpose: an authorization is
#: consent for the plan that was shown, and a repository moves.
AUTHORIZATION_TTL_SECONDS = 900

#: The source_context key the served operator surface stamps. It is in
#: `core.request_trust.RESERVED_TRUST_KEYS`, so an inbound HTTP body carrying it is stripped
#: before it reaches any runtime: a model cannot author its own authorization.
AUTHORIZATION_CONTEXT_KEY = "repo_push_authorization"

#: The parallel operator gesture for FORGE ACTIONS — creating a draft pull request, updating a
#: reviewed PR text, or posting a comment. Reserved in `core.request_trust.RESERVED_TRUST_KEYS`
#: for the same reason as the push key: consent to put content on a forge is the operator's act,
#: never a claim a turn can make about itself.
FORGE_ACTION_CONTEXT_KEY = "repo_forge_action_authorization"

_SHA_RE_LEN = 40


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def session_dir() -> Path:
    override = str(os.environ.get("VOOL_REPOOPS_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    from core.blackbox.store import store_root

    return store_root() / "repo_sessions"


#: How long a journal write waits for another writer of the SAME session journal to finish.
#: Journal writes take milliseconds; this bounds lock contention (wall clock), after which the
#: write fails closed as "not durable" -- it never proceeds unlocked.
_JOURNAL_LOCK_WAIT_SECONDS = 5.0


def _read_journal_bytes(path: Path) -> tuple[str, bytes]:
    """The journal file's exact bytes: ("ok", bytes), ("missing", b"") or ("unreadable", b"")."""
    try:
        return "ok", path.read_bytes()
    except FileNotFoundError:
        return "missing", b""
    except OSError:
        return "unreadable", b""


def _journal_revision(raw: bytes) -> int:
    """The revision recorded in journal bytes. A journal written before revisions existed is 0."""
    payload = json.loads(raw.decode("utf-8"))
    try:
        return max(0, int(dict(payload).get("journal_revision") or 0))
    except (TypeError, ValueError):
        return 0


@contextmanager
def _journal_lock(lock_path: Path):
    """The cross-process write lock of ONE session journal.

    `core.cross_process_lock.PublicationLock` is the repository's one fail-closed cross-process
    lock (fcntl on POSIX, msvcrt on Windows) and is non-blocking by design; contention between
    writers of the same journal is waited out for a bounded moment and then raises
    `LockUnavailable` -- a journal write never proceeds unlocked."""
    from core.cross_process_lock import LockUnavailable, PublicationLock

    deadline = time.monotonic() + _JOURNAL_LOCK_WAIT_SECONDS
    while True:
        lock = PublicationLock(lock_path)
        try:
            lock.__enter__()
        except LockUnavailable:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.005)
            continue
        try:
            yield
        finally:
            lock.__exit__(None, None, None)
        return


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass
class StepRecord:
    step_id: str
    intent: str
    arguments: dict[str, Any]
    stage_at: str
    started_at: str
    completed_at: str = ""
    executed: bool = False
    ok: bool = False
    status: str = "pending"
    reason: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    fault_id: str = ""


@dataclass
class RepoBinding:
    """The exact SHAs a session operates on. Frozen once; never re-derived silently."""

    binding_id: str = ""
    bound_at: str = ""
    root: str = ""
    remote: str = "origin"
    remote_url: str = ""
    provider: str = ""
    namespace: str = ""
    pull_request: str = ""
    #: A credential BINDING id, never a secret. The transport resolves it inside VOOL.
    auth_binding: str = ""
    branch: str = ""
    local_head: str = ""
    remote_head: str = ""
    base_sha: str = ""
    head_sha: str = ""
    dirty: bool = False
    dirty_paths: list[str] = field(default_factory=list)
    #: The local HEAD as this session last left it. A HEAD that differs from this without the
    #: session having moved it means somebody else moved the repository.
    working_head: str = ""

    @property
    def bound(self) -> bool:
        return bool(self.binding_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepoSession:
    session_key: str
    objective: str
    root: str
    session_id: str
    turn_id: str
    turn_key: str
    created_at: str
    updated_at: str = ""
    stage: str = "inspect"
    cancelled_reason: str = ""
    binding: RepoBinding = field(default_factory=RepoBinding)
    inspection: dict[str, Any] = field(default_factory=dict)
    pull_request: dict[str, Any] = field(default_factory=dict)
    #: Issues this session has READ, keyed by number. Read-only remote truth; the bodies are
    #: untrusted data and confer no authority anywhere in this plane.
    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)
    ci: dict[str, Any] = field(default_factory=dict)
    diagnosis: dict[str, Any] = field(default_factory=dict)
    steps: dict[str, StepRecord] = field(default_factory=dict)
    step_order: list[str] = field(default_factory=list)
    tests: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    push_plan: dict[str, Any] = field(default_factory=dict)
    authorization: dict[str, Any] = field(default_factory=dict)
    push_outcome: dict[str, Any] = field(default_factory=dict)
    remote_verification: dict[str, Any] = field(default_factory=dict)
    #: The CURRENT forge-action plan (draft PR create / PR text update / comment) and the
    #: operator authorization bound to its action hash. Same law as the push plan: a new plan
    #: invalidates an older authorization, because consent was for the exact text shown.
    forge_plan: dict[str, Any] = field(default_factory=dict)
    forge_authorization: dict[str, Any] = field(default_factory=dict)
    #: Every forge action this session has PLANNED, keyed by action hash. The record is the
    #: durable exact result: once "applied", an identical request replays the recorded outcome
    #: instead of putting a second PR or comment on the forge; once "unknown", the identical
    #: action is blocked until its outcome is reconciled — never retried blind.
    forge_actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    fault_ids: list[str] = field(default_factory=list)
    gate0: dict[str, Any] = field(default_factory=dict)
    seal: str = ""
    #: The journal's monotonic revision. Every durable write is CONDITIONAL on the journal on
    #: disk still being the revision (and the exact bytes) this copy was read at or last wrote:
    #: a copy another runtime instance or process has advanced since is stale and may not
    #: overwrite it. Journals written before revisions existed read as revision 0.
    journal_revision: int = 0

    def plan(self) -> list[dict[str, Any]]:
        index = STAGES.index(self.stage) if self.stage in STAGES else len(STAGES)
        return [
            {"stage": name, "state": "done" if i < index else ("current" if i == index else "pending")}
            for i, name in enumerate(STAGES)
        ]

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = {sid: asdict(step) for sid, step in self.steps.items()}
        payload["binding"] = self.binding.to_dict()
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> RepoSession:
        raw_steps = dict(payload.pop("steps", {}) or {})
        raw_binding = dict(payload.pop("binding", {}) or {})
        known = {f for f in cls.__dataclass_fields__}
        session = cls(**{k: v for k, v in payload.items() if k in known})
        session.steps = {sid: StepRecord(**row) for sid, row in raw_steps.items()}
        session.binding = RepoBinding(**{k: v for k, v in raw_binding.items() if k in RepoBinding.__dataclass_fields__})
        return session


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------


class RepoOpsRuntime:
    """One session, one enforced vertical. Nothing here touches a file, a process or a socket."""

    def __init__(self) -> None:
        self._sessions: dict[str, RepoSession] = {}
        #: sha256 of the exact journal bytes this instance last read into its cache or wrote,
        #: per session. A journal whose bytes differ was advanced by another writer.
        self._synced: dict[str, str] = {}
        self._lock = threading.RLock()
        self._path_locks: dict[tuple[str, str], threading.Lock] = {}

    # -- journal ---------------------------------------------------------------

    def _persist(self, session: RepoSession) -> bool:
        """Write the session journal DURABLY and CONDITIONALLY, and say whether that happened.

        Returns True only when the bytes reached the journal file (temp write + atomic rename
        both succeeded) AND, checked under the journal's cross-process lock, the file on disk
        was still exactly the journal this copy was read from or last wrote (same revision,
        same bytes). Returns False otherwise, for one of two reasons:

        * a STORAGE failure (disk full, rename denied, lock unavailable) -- the session stays
          live in memory and `repo.receipt` reports the persistence gap;
        * a STALE WRITER -- another runtime instance or process advanced the journal after this
          copy was read. The journal on disk is newer truth (a consumed authorization, an
          applied result), and a whole-file rewrite from a stale copy would silently erase it,
          so nothing is written; the next read of the session reloads the newer journal.

        Best-effort callers may ignore the return; callers at a DURABLE DISPATCH BOUNDARY must
        treat False as "not durable" and must not send an external mutation on top of state the
        journal does not hold. The temp file is removed on failure so a crashed writer leaves no
        stale .tmp that a later reader could mistake for truth."""
        with self._lock:
            session.updated_at = _utcnow()
            root = session_dir()
            target = root / f"{session.session_key}.json"
            tmp = root / f"{session.session_key}.json.tmp"
            try:
                root.mkdir(parents=True, exist_ok=True)
                with _journal_lock(root / f"{session.session_key}.json.lock"):
                    state, raw = _read_journal_bytes(target)
                    if state == "unreadable":
                        raise OSError("the session journal could not be read back for its revision check")
                    disk_revision = _journal_revision(raw) if state == "ok" else 0
                    disk_digest = hashlib.sha256(raw).hexdigest() if state == "ok" else ""
                    if (
                        disk_revision != session.journal_revision
                        or disk_digest != self._synced.get(session.session_key, "")
                    ):
                        return False
                    session.journal_revision = disk_revision + 1
                    payload = json.dumps(session.to_json(), indent=2, default=str)
                    try:
                        tmp.write_text(payload, encoding="utf-8")
                        os.replace(tmp, target)
                    except Exception:
                        session.journal_revision = disk_revision
                        raise
                    try:
                        written = hashlib.sha256(target.read_bytes()).hexdigest()
                    except OSError:
                        written = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                    self._synced[session.session_key] = written
                    self._sessions[session.session_key] = session
                    return True
            except Exception:
                # A journal that cannot be written must not silently pretend it was.
                session.inspection.setdefault("journal_warning", "the session journal could not be written to disk")
                with suppress(OSError):
                    tmp.unlink(missing_ok=True)
                return False

    def _journal_moved(self, session: RepoSession) -> bool:
        """Whether the journal on disk is no longer what this instance last read or wrote --
        a failed conditional write that lost to ANOTHER WRITER rather than to the disk."""
        state, raw = _read_journal_bytes(session_dir() / f"{session.session_key}.json")
        if state != "ok":
            return False
        return hashlib.sha256(raw).hexdigest() != self._synced.get(session.session_key, "")

    def _load(self, session_key: str) -> RepoSession | None:
        """The session -- from this instance's cache only while that copy IS the journal on disk.

        The journal is shared by every runtime instance and process serving the session (chat
        turns, dispatched steps, the owner-local console). A cached copy whose journal another
        writer has since advanced is stale and is replaced by the journal on disk: an old,
        unconsumed authorization held in a cache must never outlive its consumption on disk."""
        key = str(session_key or "").strip()
        if not key:
            return None
        with self._lock:
            live = self._sessions.get(key)
            state, raw = _read_journal_bytes(session_dir() / f"{key}.json")
            if state != "ok":
                # Nothing readable on disk: a cached copy stays usable, and its writes stay
                # conditional (they refuse to overwrite a journal they cannot account for).
                return live
            digest = hashlib.sha256(raw).hexdigest()
            if live is not None and digest == self._synced.get(key):
                return live
            try:
                session = RepoSession.from_json(json.loads(raw.decode("utf-8")))
            except Exception:
                return live
            self._sessions[key] = session
            self._synced[key] = digest
            return session

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._synced.clear()
            self._path_locks.clear()

    # -- identity / results ----------------------------------------------------

    def _session_context(self, session: RepoSession, source_context: dict[str, Any] | None) -> dict[str, Any]:
        context = dict(source_context or {})
        context["workspace"] = session.root
        context["workspace_root"] = session.root
        context["turn_id"] = session.turn_id
        context["session_id"] = session.session_id
        context["_blackbox_task_id"] = session.session_key
        return context

    def _file_fault(self, session: RepoSession, code: str, *, dedupe: str, context: dict[str, str]) -> str:
        try:
            from core.faults.recorder import record_fault
            from core.faults.records import FaultRecord

            record = FaultRecord.for_code(
                code,
                authority=AUTHORITY,
                turn_key=session.turn_key,
                session_id=session.session_id,
                dedupe=dedupe,
                context=dict(context),
            )
            fault_id = record_fault(record)
        except Exception:
            return ""
        if fault_id:
            session.fault_ids.append(fault_id)
        return fault_id

    @staticmethod
    def _result(session: RepoSession | None, *, ok: bool, status: str, text: str, **details: Any):
        from core.runtime_execution_tools import RuntimeExecutionResult

        payload: dict[str, Any] = {"executed": False}
        if session is not None:
            payload.update(
                {"repo_session_id": session.session_key, "stage": session.stage, "plan": session.plan()}
            )
        payload.update(details)
        return RuntimeExecutionResult(handled=True, ok=ok, status=status, response_text=text, details=payload)

    def _require(self, session_key: Any):
        session = self._load(str(session_key or "").strip())
        if session is None:
            return None, self._result(
                None,
                ok=False,
                status="unknown_session",
                text=f"No repo session `{session_key}` exists in the journal.",
            )
        return session, None

    def _require_fresh(self, session_key: Any):
        """``_require``, but the journal ON DISK is truth.

        The operator authorization surface serves sessions whose journal other processes may
        have advanced (the same session can be driven by chat turns, dispatched steps and the
        owner-local console across processes). An in-memory cached copy can therefore be
        OLDER than what the operator is looking at -- and a stale copy must never veto or
        forge the operator's act."""
        key = str(session_key or "").strip()
        state, raw = _read_journal_bytes(session_dir() / f"{key}.json")
        if state != "ok":
            return self._require(session_key)
        try:
            session = RepoSession.from_json(json.loads(raw.decode("utf-8")))
        except Exception:
            return self._require(session_key)
        with self._lock:
            self._sessions[key] = session
            self._synced[key] = hashlib.sha256(raw).hexdigest()
        return session, None

    def _record_known_unsent(
        self,
        session: RepoSession,
        action_hash: str,
        failure_kind: str,
        *,
        prior_authorization: dict[str, Any] | None = None,
    ) -> None:
        """Return a KNOWN-UNSENT action to a coherent, re-armable state after a storage failure
        that provably preceded any dispatch.

        ``prior_authorization`` is the consent as it stood before the write-ahead recorded it
        spent: the write never left the machine, so that consent is given back unchanged.

        The write never crossed the wire (the reservation was ours and pre-dispatch, and was
        released), so `failed_safe_to_retry` is a fact here, not a guess -- unlike an unproven
        post-acceptance outcome, this action may be re-authorized AS-IS once storage is
        healthy, without rewriting the user's content into a different action hash.

        The durable reservation is CANCELLED PRE-DISPATCH before this is called, which is the
        durable fact a restart reconciles from: even when this journal write also fails and
        the on-disk row stays `dispatching`, `_reconcile_dispatching_from_ledger` reads the
        ledger's cancelled-pre-dispatch row and restores the same re-armable truth."""
        with self._lock:
            row = session.forge_actions.get(action_hash) or {}
            row["status"] = "failed_safe_to_retry"
            row["failure_kind"] = failure_kind
            row["known_unsent_at"] = _utcnow()
            session.forge_actions[action_hash] = row
            if prior_authorization is not None:
                session.forge_authorization = dict(prior_authorization)
            with suppress(Exception):
                self._persist(session)  # best-effort; the ledger cancellation is the durable fact

    def _reconcile_dispatching_from_ledger(self, session: RepoSession, action_hash: str) -> str:
        """Reconcile a journal-stranded `dispatching` row against the DURABLE effect ledger.

        The session journal can strand a row in `dispatching` two ways: a marker/recovery
        outage after a successful write-ahead, or a post-dispatch result write that failed.
        The one ledger holds the fact both cases turn on, read here -- no second ledger:

        * every reservation for the effect was CANCELLED PRE-DISPATCH (or never claimed):
          the write provably never crossed the wire -> the action returns to its re-armable
          known-unsent state, SAME action hash ("unsent");
        * the effect has an APPLIED row: the write landed -> the record is reconciled to
          applied with the ledger as its provenance and the exact-result gap said plainly
          ("landed"); a later replay reports that truth instead of dispatching again;
        * DISPATCHED/UNKNOWN rows, or another executor's active claim: left exactly as they
          are -- an in-flight or unproven effect is never classified unsent;
        * a FAILED_SAFE_TO_RETRY row (the forge definitively refused, and the journal missed
          it): the action is `refused` with its consent spent on that real attempt.

        The logical effect id is CONTENT-derived, so other dispatches of identical content --
        another session's, an earlier authorized one -- share it. Only the row of the effect
        instance this journal recorded speaks for this dispatch; another dispatch's applied row
        must never make this one "landed". Journals from before instance ids were recorded fall
        back to every row of the effect.
        Returns "" when nothing applied. Reads only; the caller persists any change."""
        row = session.forge_actions.get(action_hash) or {}
        if str(row.get("status") or "") != "dispatching":
            return ""
        leid = str(row.get("logical_effect_id") or "").strip()
        if not leid:
            return ""
        instance = str(row.get("effect_instance_id") or "").strip()
        try:
            from core.runtime_continuity import logical_effect_row_states

            ledger_rows = logical_effect_row_states(leid)
        except Exception:
            return ""
        if instance:
            ledger_rows = [item for item in ledger_rows if item.get("effect_instance_id") == instance]
        if not ledger_rows:
            return ""
        states = {str(item.get("state") or "") for item in ledger_rows}
        if "applied" in states:
            session.forge_actions[action_hash] = {
                **row,
                "status": "applied",
                "reconciled_from": "effect_ledger",
                "result": {
                    "sent": True,
                    "reconciled_from": "effect_ledger",
                    "verified": False,
                    "logical_effect_id": leid,
                    "note": "the write landed; the exact number/URL was lost with the session-journal gap",
                },
            }
            return "landed"
        if "dispatched" in states or "unknown" in states or "prepared" in states:
            return ""
        if instance and states == {"failed_safe_to_retry"}:
            session.forge_actions[action_hash] = {**row, "status": "refused", "reconciled_from": "effect_ledger"}
            return "refused"
        # ONLY an explicit pre-dispatch cancellation (or supersession) proves unsent. A row
        # still `prepared` proves nothing -- it may be mid-claim by another executor, and a
        # lost claim leaves the WINNER's `dispatched` row -- so it stays untouched.
        if states and states <= {"expired_pre_dispatch", "superseded"}:
            session.forge_actions[action_hash] = {
                **row,
                "status": "failed_safe_to_retry",
                "failure_kind": "ledger_verified_unsent",
                "reconciled_from": "effect_ledger",
            }
            auth = dict(session.forge_authorization or {})
            if instance and str(auth.get("consumed_by") or "") == instance:
                # This dispatch's write-ahead recorded the consent as spent; it provably never
                # left the machine, so that consent is given back.
                session.forge_authorization = {
                    **{k: v for k, v in auth.items() if k not in {"consumed_at", "consumed_by"}},
                    "consumed": False,
                }
            return "unsent"
        return ""

    def _guard(self, session: RepoSession, *, needs: str = "", source_context: dict[str, Any] | None = None):
        """Every refusal that applies before ANY stage does its own work, in one place."""

        if session.stage == STAGE_CANCELLED:
            return self._result(
                session,
                ok=False,
                status="cancelled",
                text=f"The session was cancelled ({session.cancelled_reason}); nothing further executes.",
            )
        if _cancel_requested(source_context):
            self._cancel(session, "the turn was cancelled")
            return self._result(
                session, ok=False, status="cancelled", text="The turn was cancelled; nothing further executes."
            )
        if needs and not _stage_at_least(session.stage, needs):
            return self._result(
                session,
                ok=False,
                status="stage_violation",
                text=(
                    f"Cannot do this at stage `{session.stage}`: the vertical reaches it at `{needs}`. "
                    f"Plan: {' -> '.join(STAGES)}."
                ),
            )
        return None

    def _cancel(self, session: RepoSession, reason: str) -> None:
        with self._lock:
            if session.stage != STAGE_CANCELLED:
                session.stage = STAGE_CANCELLED
                session.cancelled_reason = str(reason or "cancelled")
                self._file_fault(session, "cancelled", dedupe=f"{session.session_key}:cancel", context={"reason": reason})
                self._persist(session)

    def _advance(self, session: RepoSession, to_stage: str) -> None:
        if session.stage in {STAGE_CANCELLED, STAGE_SEALED}:
            return
        if to_stage in STAGES and STAGES.index(to_stage) > STAGES.index(session.stage):
            session.stage = to_stage

    # -- runner ----------------------------------------------------------------

    def _runner(self, session: RepoSession):
        from core.repoops.gitops import GitRunner

        return GitRunner(root=Path(session.root))

    def _binding_holds(self, session: RepoSession) -> tuple[bool, str, str]:
        """Whether the repository is still where the binding says. Returns (ok, actual, expected)."""

        if not session.binding.bound:
            return True, "", ""
        expected = str(session.binding.working_head or session.binding.local_head or "")
        actual = self._runner(session).head()
        return (actual == expected), actual, expected

    # -- open ------------------------------------------------------------------

    def open(self, arguments: dict[str, Any], *, workspace_root: Path, source_context: dict[str, Any] | None):
        from core.repoops import task_law
        from core.repoops.forge import namespace_for_remote, provider_for_remote
        from core.repoops.gitops import GitRunner

        objective = str(arguments.get("objective") or "").strip()
        if not objective:
            return self._result(None, ok=False, status="invalid_arguments", text="`repo.session.open` needs a non-empty objective.")

        context = dict(source_context or {})
        raw_root = str(arguments.get("cwd") or "").strip()
        root = Path(workspace_root)
        if raw_root:
            from core.execution.workspace_tools import resolve_workspace_path

            try:
                root = Path(resolve_workspace_path(raw_root, workspace_root=Path(workspace_root)))
            except ValueError as exc:
                return self._result(None, ok=False, status="scope_violation", text=str(exc))
        runner = GitRunner(root=root)
        if not runner.is_repo():
            return self._result(
                None,
                ok=False,
                status="not_git_repo",
                text=f"`{root}` is not inside a git repository, so there is no repository to operate on.",
            )

        remote = str(arguments.get("remote") or "origin").strip() or "origin"
        remote_url = runner.remote_url(remote)
        provider = str(arguments.get("provider") or "").strip().lower() or provider_for_remote(remote_url)
        namespace = str(arguments.get("namespace") or "").strip() or namespace_for_remote(remote_url)
        head = runner.head()
        dirty = tuple(runner.dirty_paths())

        # GATE 0 -- deterministic, before a single model call. A refusal here costs zero spend.
        outcome = task_law.evaluate(
            repo_root=str(root),
            head_sha=head,
            dirty_paths=(),  # the owned scope is empty until a repair declares one; see repo.step
            objective=objective,
            authority_owner=str(context.get("authority_owner") or "operator"),
            writable_scope=(),
            mutating=False,
            builder_model=task_law.active_model_hint(context),
            reviewer_model=str(arguments.get("reviewer_model") or ""),
        )
        if not outcome.allowed:
            return self._result(
                None,
                ok=False,
                status="gate0_refused",
                text=(
                    f"Gate 0 refused this repository task before any model was asked anything: "
                    f"{outcome.reason} — {outcome.detail}"
                ),
                gate0=outcome.to_dict(),
                model_calls_spent=0,
            )

        session_key = f"rs-{uuid.uuid4().hex[:12]}"
        turn_id = f"repo-ops:{session_key}"
        session_id = (
            str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
            or f"repo-ops:{uuid.uuid4().hex[:8]}"
        )
        try:
            from core.faults.recorder import identity_from_context as fault_identity

            turn_key, _ = fault_identity({**context, "turn_id": turn_id, "session_id": session_id})
        except Exception:
            turn_key = turn_id
        session = RepoSession(
            session_key=session_key,
            objective=objective,
            root=str(root),
            session_id=session_id,
            turn_id=turn_id,
            turn_key=turn_key or turn_id,
            created_at=_utcnow(),
        )
        session.gate0 = {
            **outcome.to_dict(),
            # Carried so the WORKSPACE_WRITE re-evaluation names the same seats the read was
            # admitted under, instead of re-deriving them from a later turn's context.
            "builder_model": task_law.active_model_hint(context),
            "reviewer_model": str(arguments.get("reviewer_model") or ""),
        }
        session.binding = RepoBinding(
            root=str(root),
            remote=remote,
            remote_url=remote_url,
            provider=provider,
            namespace=namespace,
            pull_request=str(arguments.get("pull_request") or "").strip(),
            auth_binding=str(arguments.get("auth_binding") or "").strip(),
        )
        session.inspection = {"dirty_at_open": list(dirty)}
        with self._lock:
            self._sessions[session_key] = session
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"Opened repo session {session_key} on `{root}`: {objective}",
            gate0=outcome.to_dict(),
            provider=provider,
            namespace=namespace,
        )

    # -- inspect ---------------------------------------------------------------

    def inspect(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, source_context=source_context)
        if refusal:
            return refusal
        runner = self._runner(session)
        dirty_paths = runner.dirty_paths()
        pull_request: dict[str, Any] = {}
        pr_number = str(session.binding.pull_request or "").strip()
        if pr_number:
            adapter, forge_err = self._adapter(session, source_context)
            if forge_err is not None:
                return forge_err
            try:
                pr = adapter.describe_pull_request(pr_number)
            except Exception as exc:
                return self._forge_failure(session, exc, operation="describe_pull_request")
            pull_request = {
                "number": pr.number,
                "title": pr.title,
                "state": pr.state,
                "base_ref": pr.base_ref,
                "base_sha": pr.base_sha,
                "head_ref": pr.head_ref,
                "head_sha": pr.head_sha,
                "draft": pr.draft,
                "merge_state": pr.merge_state,
                "provider": pr.provider_id,
            }
        with self._lock:
            session.pull_request = pull_request
            session.inspection = {
                "root": session.root,
                "remote": session.binding.remote,
                "remote_url": session.binding.remote_url,
                "branch": runner.branch(),
                "head": runner.head(),
                "dirty": bool(dirty_paths),
                "dirty_paths": list(dirty_paths),
                "in_progress": runner.in_progress(),
                "provider": session.binding.provider,
                "namespace": session.binding.namespace,
            }
            self._advance(session, "bind")
            self._persist(session)
        return self._result(
            session, ok=True, status="ok", text=f"Inspected `{session.root}`.", **session.inspection,
            pull_request=pull_request,
        )

    # -- bind ------------------------------------------------------------------

    def bind(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="bind", source_context=source_context)
        if refusal:
            return refusal
        runner = self._runner(session)
        dirty_paths = runner.dirty_paths()
        allow_dirty = bool(arguments.get("allow_dirty") or False)
        if dirty_paths and not allow_dirty:
            fault = self._file_fault(
                session,
                "unsupported_claim",
                dedupe=f"{session.session_key}:dirty",
                context={"dirty_count": str(len(dirty_paths))},
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="dirty_worktree",
                text=(
                    f"The working tree has {len(dirty_paths)} uncommitted change(s), so no SHA here "
                    f"describes what is actually on disk. Commit, stash, or pass allow_dirty to bind "
                    f"anyway and carry the divergence in the receipt."
                ),
                dirty_paths=list(dirty_paths),
                fault_id=fault,
            )

        local_head = runner.head()
        if len(local_head) != _SHA_RE_LEN:
            return self._result(
                session, ok=False, status="unresolvable_head", text="Local HEAD does not resolve to a commit."
            )

        remote_head = ""
        base_sha = ""
        head_sha = ""
        pr_number = str(session.binding.pull_request or "").strip()
        if pr_number:
            adapter, forge_err = self._adapter(session, source_context)
            if forge_err is not None:
                return forge_err
            try:
                pr = adapter.describe_pull_request(pr_number)
                remote_head = adapter.resolve_ref(pr.head_ref) or pr.head_sha
            except Exception as exc:
                return self._forge_failure(session, exc, operation="bind")
            base_sha, head_sha = pr.base_sha, pr.head_sha
        else:
            base_sha = runner.merge_base(local_head, f"{session.binding.remote}/{runner.branch()}") or local_head
            head_sha = local_head
            remote_head = runner.resolve(f"{session.binding.remote}/{runner.branch()}")

        payload = {
            "root": session.root,
            "remote": session.binding.remote,
            "remote_url": session.binding.remote_url,
            "provider": session.binding.provider,
            "namespace": session.binding.namespace,
            "pull_request": pr_number,
            "branch": runner.branch(),
            "local_head": local_head,
            "remote_head": remote_head,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "dirty": bool(dirty_paths),
        }
        with self._lock:
            session.binding.binding_id = f"bind-{_sha(_canonical(payload))[:16]}"
            session.binding.bound_at = _utcnow()
            session.binding.branch = payload["branch"]
            session.binding.local_head = local_head
            session.binding.remote_head = remote_head
            session.binding.base_sha = base_sha
            session.binding.head_sha = head_sha
            session.binding.dirty = bool(dirty_paths)
            session.binding.dirty_paths = list(dirty_paths)
            session.binding.working_head = local_head
            self._advance(session, "retrieve")
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=(
                f"Bound exact SHAs: local {local_head[:12]}, remote {remote_head[:12] or 'none'}, "
                f"base {base_sha[:12] or 'none'}, head {head_sha[:12] or 'none'}."
            ),
            binding=session.binding.to_dict(),
            binding_id=session.binding.binding_id,
            remote_head=remote_head,
            local_head=local_head,
            base_sha=base_sha,
            head_sha=head_sha,
        )

    # -- forge helpers ---------------------------------------------------------

    def _adapter(self, session: RepoSession, source_context: dict[str, Any] | None):
        """The forge adapter for this session, or a typed refusal that names what is missing."""

        from core.repoops.forge import open_forge

        provider = str(session.binding.provider or "").strip()
        namespace = str(session.binding.namespace or "").strip()
        if not provider:
            return None, self._result(
                session,
                ok=False,
                status="capability_missing",
                text=(
                    "This repository's remote does not name a forge VOOL has an adapter for, so "
                    "there is no remote truth to read. Name a provider explicitly when opening the "
                    "session, or work locally."
                ),
                remote_url=session.binding.remote_url,
            )
        if not namespace:
            return None, self._result(
                session,
                ok=False,
                status="capability_missing",
                text="The repository namespace (owner/repo) could not be resolved from the remote URL.",
            )
        try:
            adapter = open_forge(
                provider=provider,
                namespace=namespace,
                auth_binding=session.binding.auth_binding,
                source_context=self._session_context(session, source_context),
            )
        except LookupError as exc:
            fault = self._file_fault(
                session, "tool_unavailable", dedupe=f"{session.session_key}:adapter", context={"provider": provider}
            )
            return None, self._result(
                session, ok=False, status="capability_missing", text=str(exc), fault_id=fault
            )
        except Exception as exc:
            return None, self._forge_failure(session, exc, operation="open_forge")
        return adapter, None

    def _forge_failure(self, session: RepoSession, exc: BaseException, *, operation: str):
        """Map a KAS transport outcome onto the truthful runtime status. UNKNOWN is not FAILED."""

        from core.kas.contract import (
            ForgeRateLimitedError,
            ForgeRefusedError,
            TransportDeniedError,
            TransportUnknownError,
        )

        if isinstance(exc, TransportUnknownError):
            fault = self._file_fault(
                session, "unknown", dedupe=f"{session.session_key}:{operation}", context={"operation": operation}
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="unknown_reconciliation_required",
                text=(
                    f"The `{operation}` request left the machine and its outcome could not be proven. "
                    f"It is not being reported as failed, and an identical retry stays blocked until "
                    f"the outcome is established."
                ),
                reason=exc.reason,
                fault_id=fault,
            )
        if isinstance(exc, ForgeRateLimitedError):
            # The forge answered and served nothing. This is a typed absence OF DATA, never an
            # empty result: no caller may read "zero rows" or "ref absent" out of it.
            fault = self._file_fault(
                session,
                "provider_unavailable",
                dedupe=f"{session.session_key}:{operation}",
                context={"operation": operation, "reason": "rate_limited"},
            )
            self._persist(session)
            wait = f" The forge suggested waiting {int(exc.retry_after)}s." if exc.retry_after else ""
            return self._result(
                session,
                ok=False,
                status="provider_rate_limited",
                text=(
                    f"The forge rate-limited `{operation}` and served no data, so nothing was read: "
                    f"this is not an empty result and not an absence.{wait}"
                ),
                reason="rate_limited",
                retry_after=exc.retry_after,
                fault_id=fault,
            )
        if isinstance(exc, ForgeRefusedError):
            if exc.reason == "not_found":
                return self._result(
                    session,
                    ok=False,
                    status="not_found",
                    text=f"The forge reports there is no such object for `{operation}` (HTTP 404).",
                    reason="not_found",
                )
            fault = self._file_fault(
                session,
                "provider_unavailable",
                dedupe=f"{session.session_key}:{operation}",
                context={"operation": operation, "reason": exc.reason},
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="provider_unavailable",
                text=(
                    (f"The forge returned an unusable response for `{operation}` "
                     f"({exc.reason}); no verified result is available.")
                    if 200 <= exc.status_code < 300 else
                    (f"The forge refused `{operation}` (HTTP {exc.status_code}) and served no data; "
                     f"this is not an empty result and not an absence.")
                ),
                reason=exc.reason,
                fault_id=fault,
            )
        if isinstance(exc, TransportDeniedError):
            code = "permission_denied" if exc.reason in {"egress_denied", "adapter_set_credential_header"} else "credential_failure"
            if exc.reason in {"transport_failed", "host_not_pinned", "unsupported_scheme", "malformed_url"}:
                code = "provider_unavailable"
            fault = self._file_fault(
                session, code, dedupe=f"{session.session_key}:{operation}", context={"reason": exc.reason}
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="provider_unavailable" if code == "provider_unavailable" else code,
                text=f"The forge request was refused: {exc.reason}.",
                reason=exc.reason,
                fault_id=fault,
            )
        fault = self._file_fault(
            session,
            "provider_unavailable",
            dedupe=f"{session.session_key}:{operation}",
            context={"operation": operation, "exception": type(exc).__name__},
        )
        self._persist(session)
        return self._result(
            session,
            ok=False,
            status="provider_unavailable",
            text=f"The forge could not be reached for `{operation}` ({type(exc).__name__}).",
            fault_id=fault,
        )

    def _require_binding(self, session: RepoSession):
        if not session.binding.bound:
            return self._result(
                session,
                ok=False,
                status="not_bound",
                text="Nothing is bound yet: run `repo.bind` so every later step names exact SHAs.",
            )
        held, actual, expected = self._binding_holds(session)
        if not held:
            fault = self._file_fault(
                session,
                "unsupported_claim",
                dedupe=f"{session.session_key}:moved",
                context={"expected": expected, "actual": actual},
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="binding_diverged",
                text=(
                    f"The repository moved underneath this session: HEAD is {actual[:12] or 'unresolvable'} "
                    f"but the session is bound to {expected[:12]}. Nothing further runs against a stale picture."
                ),
                expected_head=expected,
                actual_head=actual,
                fault_id=fault,
            )
        return None

    # -- retrieve --------------------------------------------------------------

    def diff(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        pr_number = str(session.binding.pull_request or "").strip()
        if not pr_number:
            return self._result(
                session,
                ok=False,
                status="no_pull_request",
                text="This session is not bound to a pull request, so there is no forge diff to retrieve.",
            )
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        try:
            text = adapter.pull_request_diff(pr_number)
        except Exception as exc:
            return self._forge_failure(session, exc, operation="pull_request_diff")
        paths = _diff_paths(text)
        with self._lock:
            session.diff = {"head_sha": session.binding.head_sha, "paths": paths, "bytes": len(text.encode("utf-8"))}
            self._advance(session, "diagnose")
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"Retrieved the diff for PR {pr_number} at {session.binding.head_sha[:12]}: {len(paths)} file(s).",
            diff=text[:200_000],
            paths=paths,
            head_sha=session.binding.head_sha,
        )

    def ci_jobs(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        sha = session.binding.head_sha or session.binding.local_head
        try:
            listing = adapter.ci_jobs(sha)
        except Exception as exc:
            return self._forge_failure(session, exc, operation="ci_jobs")
        rows = [
            {
                "job_id": j.job_id,
                "name": j.name,
                "status": j.status,
                "conclusion": j.conclusion,
                "head_sha": j.head_sha,
                "provider": j.provider_id,
            }
            for j in listing.rows
        ]
        failed = [r for r in rows if str(r["conclusion"]).lower() in {"failure", "failed", "timed_out", "canceled"}]
        truncated = bool(getattr(listing, "truncated", False))
        pages_read = int(getattr(listing, "pages_read", 1) or 1)
        with self._lock:
            session.ci = {
                "head_sha": sha,
                "jobs": rows,
                "failed": failed,
                "read_at": _utcnow(),
                "truncated": truncated,
                "pages_read": pages_read,
            }
            self._advance(session, "diagnose")
            self._persist(session)
        if truncated:
            # A listing the runtime did not finish reading is DATA, not a verdict: no caller
            # may read "0 failing among these rows" as "CI is green".
            return self._result(
                session,
                ok=False,
                status="listing_truncated",
                text=(
                    f"Read {len(rows)} CI job(s) across {pages_read} page(s) at {sha[:12]} before the "
                    f"pagination bound stopped the read. This is NOT the complete CI picture: "
                    f"{len(failed)} failing among what was read cannot speak for the rest."
                ),
                jobs=rows,
                failed=failed,
                job_names=[r["name"] for r in rows],
                head_sha=sha,
                truncated=True,
                pages_read=pages_read,
            )
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"{len(rows)} CI job(s) at {sha[:12]}; {len(failed)} failing.",
            jobs=rows,
            failed=failed,
            job_names=[r["name"] for r in rows],
            head_sha=sha,
            truncated=False,
            pages_read=pages_read,
        )

    def ci_log(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            return self._result(session, ok=False, status="invalid_arguments", text="`repo.ci.log` needs a job_id.")
        known = {str(r.get("job_id")) for r in list(session.ci.get("jobs") or [])}
        if known and job_id not in known:
            return self._result(
                session,
                ok=False,
                status="unknown_job",
                text=f"Job `{job_id}` is not among the jobs this session read at {session.binding.head_sha[:12]}.",
            )
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        try:
            log = adapter.ci_job_log(job_id)
        except Exception as exc:
            return self._forge_failure(session, exc, operation="ci_job_log")
        truncated = len(log) > 200_000
        return self._result(
            session, ok=True, status="ok", text=f"Retrieved the log for job {job_id}.",
            job_id=job_id, log=log[:200_000], truncated=truncated,
        )

    def ci_artifacts(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        sha = session.binding.head_sha or session.binding.local_head
        try:
            listing = adapter.ci_artifacts(sha)
        except Exception as exc:
            return self._forge_failure(session, exc, operation="ci_artifacts")
        rows = [
            {"artifact_id": a.artifact_id, "name": a.name, "size_bytes": a.size_bytes, "provider": a.provider_id}
            for a in listing.rows
        ]
        if bool(getattr(listing, "truncated", False)):
            return self._result(
                session,
                ok=False,
                status="listing_truncated",
                text=(
                    f"Read {len(rows)} artifact(s) at {sha[:12]} before the pagination bound stopped "
                    f"the read; this is not the complete artifact listing."
                ),
                artifacts=rows,
                head_sha=sha,
                truncated=True,
            )
        return self._result(
            session, ok=True, status="ok", text=f"{len(rows)} artifact(s) at {sha[:12]}.", artifacts=rows, head_sha=sha
        )

    # -- issues ----------------------------------------------------------------

    def issue(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.issue` -- read one issue of the bound repository's forge.

        The body, title and labels are UNTRUSTED DATA. They are journaled and returned
        verbatim, and they confer no authority anywhere: no text read off a forge can mint
        the push authorization that only the loopback operator surface mints.
        """

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        number = str(arguments.get("number") or "").strip()
        if not number:
            return self._result(session, ok=False, status="invalid_arguments", text="`repo.issue` needs an issue number.")
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        try:
            found = adapter.describe_issue(number)
        except Exception as exc:
            return self._forge_failure(session, exc, operation="describe_issue")
        row = {
            "number": found.number,
            "title": found.title,
            "state": found.state,
            "author": found.author,
            "labels": list(found.labels),
            "body": found.body,
            "comments_count": found.comments_count,
            "provider": found.provider_id,
        }
        with self._lock:
            session.issues[number] = row
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"Read issue #{found.number} ({found.state}): {found.title}",
            issue=row,
        )

    def issue_comments(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.issue.comments` -- the comments of one issue, with the listing-truth law."""

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        number = str(arguments.get("number") or "").strip()
        if not number:
            return self._result(
                session, ok=False, status="invalid_arguments", text="`repo.issue.comments` needs an issue number."
            )
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        try:
            listing = adapter.issue_comments(number)
        except Exception as exc:
            return self._forge_failure(session, exc, operation="issue_comments")
        rows = [
            {
                "comment_id": c.comment_id,
                "author": c.author,
                "body": c.body,
                "created_at": c.created_at,
                "provider": c.provider_id,
            }
            for c in listing.rows
        ]
        truncated = bool(getattr(listing, "truncated", False))
        if truncated:
            return self._result(
                session,
                ok=False,
                status="listing_truncated",
                text=(
                    f"Read {len(rows)} comment(s) of issue #{number} across "
                    f"{int(getattr(listing, 'pages_read', 1) or 1)} page(s) before the pagination bound "
                    f"stopped the read; this is not the complete comment thread."
                ),
                comments=rows,
                truncated=True,
            )
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"{len(rows)} comment(s) on issue #{number}.",
            comments=rows,
            comment_ids=[r["comment_id"] for r in rows],
            truncated=False,
        )

    def ci_control(self, intent: str, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.ci.rerun` / `repo.ci.cancel` -- the two CI operations that mutate the remote."""

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            return self._result(session, ok=False, status="invalid_arguments", text=f"`{intent}` needs a job_id.")
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        action = "rerun" if intent.endswith("rerun") else "cancel"
        try:
            requested = adapter.rerun_ci(job_id) if action == "rerun" else adapter.cancel_ci(job_id)
        except Exception as exc:
            return self._forge_failure(session, exc, operation=f"ci_{action}")
        return self._result(
            session,
            ok=bool(requested),
            status="ok" if requested else "refused_by_forge",
            text=(
                f"Asked the forge to {action} job {job_id}."
                if requested
                else f"The forge refused the {action} for job {job_id}."
            ),
            job_id=job_id,
            requested=bool(requested),
        )

    # -- forge actions: draft PR create / PR text update / comment ----------------------
    #
    # The one path by which VOOL puts CONTENT on a forge. Three laws hold throughout:
    #
    # 1. CONSENT IS THE OPERATOR'S. `repo.pr.request` builds the exact action text; only the
    #    server-stamped `repo_forge_action_authorization` gesture — minted by the owner-local
    #    operator surface, stripped from every inbound body by request_trust — can arm it.
    # 2. THE PICTURE IS FRESH. The plan freezes the head SHA the action is bound to, and the
    #    executor re-resolves it immediately before dispatch: a branch that moved after the
    #    preview is a different action than the one authorized, and is refused unsent.
    # 3. AN EFFECT IS JOURNALED, NEVER ASSUMED. Every dispatch reserves a logical effect
    #    (duplicate suppression across threads and restarts), and its outcome — applied,
    #    unknown, failed-safe-to-retry — is recorded with the forge's own answer: number, URL,
    #    exact text, head binding. An identical re-request replays that record; an unproven
    #    outcome blocks the identical action until reconciled. The returned URL alone is never
    #    read as completion: a read-back verifies what the forge now says.

    _FORGE_ACTIONS = ("create", "update", "comment")

    def pr_request(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.pr.request` -- build the exact draft-PR / PR-text / comment action and hash it.

        Nothing is sent. The plan names the repository, the head SHA the action is bound to, and
        the exact text; the operator authorizes THIS hash or nothing runs."""

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        action = str(arguments.get("action") or "").strip().lower()
        if action not in self._FORGE_ACTIONS:
            return self._result(
                session,
                ok=False,
                status="invalid_arguments",
                text=f"`action` must be one of {list(self._FORGE_ACTIONS)}, not `{action}`.",
            )
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        provider = str(session.binding.provider or "")
        namespace = str(session.binding.namespace or "")

        if action == "create":
            title = str(arguments.get("title") or "").strip()
            body = str(arguments.get("body") or "")
            task_id = str(arguments.get("task_id") or "").strip()
            if task_id:
                # The evidence path: the body comes from the task journal's prepared description,
                # never from prose about the task. An unprepared task is refused, not improvised.
                from core.code_assistant.task_runtime import code_task_runtime

                prepared = code_task_runtime().prepared_pr_description(task_id, source_context=source_context)
                if not prepared:
                    return self._result(
                        session,
                        ok=False,
                        status="insufficient_evidence",
                        text=(
                            f"Code task `{task_id}` has no prepared PR description. Run "
                            "`code.task.pr_description` first: a draft PR body is assembled only "
                            "from the task journal's verified evidence."
                        ),
                    )
                title = title or str(prepared.get("title") or "")
                body = body or str(prepared.get("body") or "")
            if not title:
                return self._result(
                    session, ok=False, status="invalid_arguments", text="A draft PR plan needs a title."
                )
            head_ref = str(arguments.get("head_ref") or session.binding.branch or "").strip()
            if not head_ref:
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text="A draft PR plan needs a head branch (`head_ref`, or a session with a branch).",
                )
            base_ref = str(arguments.get("base_ref") or "").strip()
            if not base_ref:
                try:
                    base_ref = adapter.describe_repository().default_branch
                except Exception as exc:
                    return self._forge_failure(session, exc, operation="forge.repository")
            if not base_ref:
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text="The base branch could not be resolved; name it explicitly with `base_ref`.",
                )
            try:
                head_sha = adapter.resolve_ref(head_ref)
            except Exception as exc:
                return self._forge_failure(session, exc, operation="forge.resolve_ref")
            if not head_sha:
                return self._result(
                    session,
                    ok=False,
                    status="head_ref_absent",
                    text=(
                        f"The head branch `{head_ref}` does not exist on the remote, so there is "
                        "nothing to open a draft PR from. Land the branch first (push), then request."
                    ),
                    head_ref=head_ref,
                )
            core = {
                "action": "create",
                "provider": provider,
                "namespace": namespace,
                "head_ref": head_ref,
                "head_sha": head_sha,
                "base_ref": base_ref,
                "title": title,
                "body": body,
                "draft": True,
            }
            extras: dict[str, Any] = {"task_id": task_id}
        elif action == "update":
            number = str(arguments.get("number") or session.binding.pull_request or "").strip()
            if not number:
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text="A PR update plan needs `number` (or a session bound to a pull request).",
                )
            try:
                current = adapter.describe_pull_request(number)
            except Exception as exc:
                return self._forge_failure(session, exc, operation="forge.pull_request")
            if current.state != "open":
                return self._result(
                    session,
                    ok=False,
                    status="not_open",
                    text=f"Pull request {number} is `{current.state}`, not open; only an open PR's text may be updated.",
                )
            title = str(arguments.get("title") or "").strip()
            body = str(arguments.get("body") or "")
            if not title and not body:
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text="A PR update plan needs at least one of `title` or `body`.",
                )
            core = {
                "action": "update",
                "provider": provider,
                "namespace": namespace,
                "number": number,
                "head_ref": current.head_ref,
                "head_sha": current.head_sha,
                "title": title,
                "body": body,
            }
            # The PRIOR text is journaled (not hashed into consent) so an unproven update can be
            # reconciled later: "forge now carries the new text" proves applied, "forge still
            # carries exactly the prior text" proves not-applied, anything else is divergence.
            extras = {"prior_title": current.title, "prior_body": current.body}
        else:
            number = str(arguments.get("number") or "").strip()
            if not number and session.binding.pull_request:
                number = str(session.binding.pull_request)
            if not number:
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text="A comment plan needs `number` (the issue or pull request to comment on).",
                )
            subject = str(arguments.get("subject") or "").strip().lower()
            if not subject:
                subject = "pull_request" if number == str(session.binding.pull_request or "") else "issue"
            if subject not in {"issue", "pull_request"}:
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text='`subject` must be "issue" or "pull_request".',
                )
            body = str(arguments.get("body") or "")
            if not body.strip():
                return self._result(
                    session, ok=False, status="invalid_arguments", text="A comment plan needs the exact `body` to post."
                )
            head_ref = head_sha = ""
            if subject == "pull_request":
                try:
                    current = adapter.describe_pull_request(number)
                except Exception as exc:
                    return self._forge_failure(session, exc, operation="forge.pull_request")
                if current.state != "open":
                    return self._result(
                        session,
                        ok=False,
                        status="not_open",
                        text=f"Pull request {number} is `{current.state}`, not open; it cannot be commented on through this path.",
                    )
                head_ref, head_sha = current.head_ref, current.head_sha
            core = {
                "action": "comment",
                "provider": provider,
                "namespace": namespace,
                "number": number,
                "subject": subject,
                "head_ref": head_ref,
                "head_sha": head_sha,
                "body": body,
            }
            extras = {}

        action_hash = _sha(_canonical(core))
        plan = {**core, **extras, "action_hash": action_hash, "built_at": _utcnow()}
        with self._lock:
            session.forge_plan = plan
            # Consent was for the text the operator saw; different text voids the old consent.
            if str(session.forge_authorization.get("action_hash") or "") != action_hash:
                session.forge_authorization = {}
            record = session.forge_actions.get(action_hash)
            if record is None:
                session.forge_actions[action_hash] = {
                    "action": action,
                    "status": "planned",
                    "action_hash": action_hash,
                    "created_at": _utcnow(),
                }
            self._persist(session)

        if action == "create":
            summary = (
                f"Draft PR plan: {namespace} {plan['head_ref']}@{plan['head_sha'][:12]} -> "
                f"{plan['base_ref']}, title {plan['title']!r}, body {len(plan['body'])} chars. "
                "Nothing has been sent."
            )
        elif action == "update":
            summary = (
                f"PR update plan: {namespace}#{plan['number']} at head {plan['head_sha'][:12]}, "
                + (f"new title {plan['title']!r}, " if plan["title"] else "")
                + f"new body {len(plan['body'])} chars. Nothing has been sent."
            )
        else:
            summary = (
                f"Comment plan: {namespace}#{plan['number']} ({plan['subject']}), "
                f"body {len(plan['body'])} chars. Nothing has been sent."
            )
        return self._result(
            session,
            ok=True,
            status="ok",
            text=summary,
            action=action,
            action_hash=action_hash,
            plan=plan,
            already_applied=bool(record and record.get("status") == "applied"),
            requires="an explicit operator authorization naming this exact action_hash",
        )

    def pr_authorize(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.pr.authorize` -- the operator's act, or a resolution of an unproven outcome."""

        session, err = self._require_fresh(arguments.get("repo_session_id"))
        if err:
            return err
        # No stage gate: the authorization is the OPERATOR's act, and its authority is the
        # server-stamped gesture naming the exact action hash -- not the session's position in
        # the vertical. (A cancelled session still refuses below.) This also keeps the served
        # owner-local route correct when the journal was advanced by another process serving
        # the same session: a stale in-memory stage must not veto the operator.
        refusal = self._guard(session, source_context=source_context)
        if refusal:
            return refusal
        stamped = dict((source_context or {}).get(FORGE_ACTION_CONTEXT_KEY) or {})
        if not stamped:
            return self._result(
                session,
                ok=False,
                status="operator_gesture_required",
                text=(
                    "Authorizing a forge action is the operator's act, not the turn's. This call "
                    "carries no server-stamped operator authorization, so it is refused. The "
                    "authorization is minted by the owner-local operator surface and cannot be "
                    "written by a request body."
                ),
            )

        resolution = str(stamped.get("resolve") or "").strip().lower()
        if resolution:
            # The operator INSPECTED the forge and states the unproven action did not land.
            # Only that direction is mintable: asserting "applied" without the forge's own
            # record would invent the exact result (number, URL) this journal exists to keep.
            target = str(stamped.get("action_hash") or arguments.get("action_hash") or "").strip()
            record = session.forge_actions.get(target)
            if record is None or record.get("status") != "unknown":
                return self._result(
                    session,
                    ok=False,
                    status="nothing_to_resolve",
                    text="There is no unproven forge action with that action_hash to resolve.",
                )
            if resolution != "failed_safe_to_retry":
                return self._result(
                    session,
                    ok=False,
                    status="invalid_arguments",
                    text=(
                        "The only operator resolution is `failed_safe_to_retry` (you inspected the "
                        "forge and the action did not land). An applied outcome must come from the "
                        "forge's own record, not from an assertion."
                    ),
                )
            with self._lock:
                record["status"] = "failed_safe_to_retry"
                record["resolved_at"] = _utcnow()
                record["resolved_by"] = str(stamped.get("operator") or "operator")
                auth = dict(session.forge_authorization or {})
                spender = str(auth.get("consumed_by") or "")
                if spender and spender == str(record.get("effect_instance_id") or ""):
                    # The unproven dispatch spent this consent in its write-ahead; the operator
                    # has now established it never landed, so that consent is given back --
                    # the same truth as a ledger-verified pre-dispatch cancellation.
                    session.forge_authorization = {
                        **{k: v for k, v in auth.items() if k not in {"consumed_at", "consumed_by"}},
                        "consumed": False,
                    }
                self._persist(session)
            # The continuity row that kept this action blocked is resolved under the operator's
            # name: the stamp came from the owner-local surface, and `user` -- not `model` -- is
            # an effect-truth source.
            if str(record.get("logical_effect_id") or ""):
                try:
                    from core.runtime_continuity import resolve_unresolved_effect

                    resolve_unresolved_effect(
                        logical_effect_id=str(record.get("logical_effect_id") or ""),
                        resolution="CONFIRMED_FAILED_SAFE_TO_RETRY",
                        source="user",
                        evidence="operator inspected the forge and states the action did not land",
                        resolved_by=str(stamped.get("operator") or "operator"),
                    )
                except Exception:
                    pass
            return self._result(
                session,
                ok=True,
                status="ok",
                text=f"Action {target[:16]} resolved as not applied; an identical retry may now be re-armed.",
                action_hash=target,
                resolved="failed_safe_to_retry",
            )

        plan_hash = str(arguments.get("action_hash") or "").strip()
        current = str(session.forge_plan.get("action_hash") or "")
        if not current:
            return self._result(
                session, ok=False, status="no_plan", text="There is no forge-action plan to authorize."
            )
        # A journal-stranded `dispatching` row is reconciled from the durable ledger first:
        # an effect whose every reservation was cancelled pre-dispatch is provably unsent and
        # ORDINARY re-authorization is lawful; a landed or in-flight effect keeps its block.
        self._reconcile_dispatching_from_ledger(session, current)
        record = session.forge_actions.get(current)
        if record is not None and record.get("status") == "applied":
            return self._result(
                session,
                ok=False,
                status="already_applied",
                text=(
                    "This exact action is already journaled as applied with its durable result; "
                    "authorizing it again would only invite a duplicate."
                ),
                action_hash=current,
            )
        if record is not None and str(record.get("status") or "") in {"unknown", "dispatching"}:
            # The identical action was already dispatched and its outcome is UNPROVEN -- either
            # the reply never came back, or the forge accepted the write but its answer could
            # not be decoded. A fresh ordinary authorization would silently arm a resend of a
            # write that may already have landed; only the explicit operator resolution of the
            # unproven outcome re-arms this exact action.
            return self._result(
                session,
                ok=False,
                status="resolution_required",
                text=(
                    "This exact action is dispatched with an UNPROVEN outcome (its reply was "
                    "lost or undecodable), so a new authorization is refused: the write may "
                    "already be on the forge. Resolve the unproven action first -- the operator "
                    "gesture carrying `resolve: failed_safe_to_retry` states you inspected the "
                    "forge and it did not land -- or plan a differently-worded action."
                ),
                action_hash=current,
                record_status=str(record.get("status") or ""),
            )
        if plan_hash != current:
            return self._result(
                session,
                ok=False,
                status="plan_mismatch",
                text=(
                    "The authorization names an action that is not the current plan. The plan "
                    "changed after the operator saw it; re-request and re-authorize."
                ),
                authorized_hash=plan_hash,
                current_hash=current,
            )
        if str(stamped.get("action_hash") or "") != current:
            return self._result(
                session,
                ok=False,
                status="plan_mismatch",
                text="The server-stamped operator authorization names a different action than the current plan.",
            )
        expires = (datetime.now(timezone.utc) + timedelta(seconds=AUTHORIZATION_TTL_SECONDS)).isoformat()
        with self._lock:
            session.forge_authorization = {
                "authorization_id": f"fa-{uuid.uuid4().hex}",
                "action_hash": current,
                "authorized_at": _utcnow(),
                "expires_at": expires,
                "operator": str(stamped.get("operator") or "operator"),
                "consumed": False,
            }
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"Forge action authorized ({session.forge_plan.get('action')}) for hash {current[:16]} until {expires}.",
            authorized=True,
            action_hash=current,
            expires_at=expires,
        )

    def _spend_forge_authorization(
        self, session: RepoSession, *, action_hash: str, leid: str, instance: str
    ) -> dict[str, Any]:
        """Record the dispatch AND spend its consent in ONE conditional journal write, decided
        from the journal as it is on disk NOW -- never from the calling snapshot.

        Another runtime instance or process serving this session may have applied the action,
        be dispatching it, or have spent this very authorization since the snapshot was read.
        `_load` replaces a stale copy with the journal on disk; the conditional write
        (`_persist`) refuses to record the spend on a journal that moved again in between.
        Verdicts: durable | applied | in_flight | unknown | not_authorized | plan_changed |
        consumed | expired | journal_unavailable | journal_contended. Returns the verdict, the
        session copy it was decided on, and the consent as it stood before the spend."""
        with self._lock:
            fresh = self._load(session.session_key)
            if fresh is None:
                return {"verdict": "journal_unavailable", "session": session,
                        "prior_authorization": dict(session.forge_authorization or {})}
            session = fresh
            row = dict(session.forge_actions.get(action_hash) or {})
            auth = dict(session.forge_authorization or {})
            spent = {"session": session, "prior_authorization": auth}
            status = str(row.get("status") or "")
            verdict = ""
            if status == "applied":
                verdict = "applied"
            elif status == "dispatching":
                verdict = "in_flight"
            elif status == "unknown":
                verdict = "unknown"
            elif not auth:
                verdict = "not_authorized"
            elif str(auth.get("action_hash") or "") != action_hash:
                verdict = "plan_changed"
            elif bool(auth.get("consumed")):
                verdict = "consumed"
            else:
                try:
                    if datetime.fromisoformat(str(auth.get("expires_at"))) <= datetime.now(timezone.utc):
                        verdict = "expired"
                except Exception:
                    verdict = "expired"
            if verdict:
                return {**spent, "verdict": verdict}
            session.forge_actions[action_hash] = {
                **row,
                "status": "dispatching",
                "logical_effect_id": leid,
                "effect_instance_id": instance,
                "authorization_id": str(auth.get("authorization_id") or ""),
            }
            session.forge_authorization = {**auth, "consumed": True, "consumed_at": _utcnow(), "consumed_by": instance}
            try:
                durable = bool(self._persist(session))
            except Exception:
                durable = False
            if durable:
                return {**spent, "verdict": "durable"}
            if row:
                session.forge_actions[action_hash] = row
            else:
                session.forge_actions.pop(action_hash, None)
            session.forge_authorization = auth
            return {**spent, "verdict": "journal_contended" if self._journal_moved(session) else "journal_unavailable"}

    def _pr_execute(self, intent: str, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        from core.kas.contract import TransportUnknownError

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="retrieve", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        action = intent.rpartition(".")[2]
        plan = dict(session.forge_plan or {})
        auth = dict(session.forge_authorization or {})
        if not plan:
            return self._result(
                session,
                ok=False,
                status="no_plan",
                text="There is no forge-action plan. Build one with `repo.pr.request` first.",
            )
        if str(plan.get("action") or "") != action:
            return self._result(
                session,
                ok=False,
                status="action_mismatch",
                text=(
                    f"The authorized plan is a `{plan.get('action')}` action; `{intent}` executes "
                    "only its own action. Re-request the plan you mean."
                ),
            )

        action_hash = str(plan.get("action_hash") or "")
        # A journal-stranded `dispatching` row is reconciled against the DURABLE effect
        # ledger before anything else: a pre-dispatch-cancelled effect is provably unsent
        # (re-armable), an applied one landed (replayed as such), and anything in flight or
        # unproven stays exactly that.
        reconciled = self._reconcile_dispatching_from_ledger(session, action_hash)
        if reconciled in {"unsent", "landed"}:
            try:
                with self._lock:
                    self._persist(session)
            except Exception:
                pass
        record = dict(session.forge_actions.get(action_hash) or {})
        if record.get("status") == "applied":
            # Durable exact result: the identical action replays its journaled outcome and never
            # touches the forge again. This is a READ of the journal, not an effect, so it needs
            # no live authorization -- asking "did this exact action run?" is not consent to run
            # another one. Delivery and verification stay SEPARATE facts: a write that LANDED but
            # failed its exact-result verification replays as the SAME failed verdict with its
            # recorded mismatches, never as success -- and never as safe-to-retry either, because
            # the record's delivery fact ("applied") keeps duplicate suppression armed.
            result = dict(record.get("result") or {})
            mismatches = list(result.get("mismatches") or [])
            if mismatches:
                return self._result(
                    session,
                    ok=False,
                    status="verification_mismatch",
                    text=(
                        "This exact action already landed on the forge and its recorded verification "
                        "STILL fails: " + "; ".join(mismatches) + ". The recorded result is replayed; "
                        "nothing was sent, and the landed write is not safe to resend."
                    ),
                    replayed=True,
                    action=action,
                    **result,
                )
            if result.get("reconciled_from") == "effect_ledger":
                return self._result(
                    session,
                    ok=True,
                    status="ok",
                    text=(
                        "This exact action already landed according to the durable effect "
                        "ledger; the exact number/URL was lost with a session-journal gap, so "
                        "it is reported as landed without those details. Nothing was sent."
                    ),
                    replayed=True,
                    action=action,
                    **result,
                )
            return self._result(
                session,
                ok=True,
                status="ok",
                text="This exact action is already applied; its recorded result is replayed, nothing was sent.",
                replayed=True,
                action=action,
                **result,
            )

        if not auth:
            return self._result(
                session,
                ok=False,
                status="not_authorized",
                text=(
                    "No live operator authorization holds for this forge action. Request the plan "
                    "and have the operator authorize its exact action_hash."
                ),
            )
        # The write-ahead spends consent BEFORE the write leaves. A dispatch whose outcome is
        # UNKNOWN therefore spent this very authorization -- and asking again must still reach
        # the reconciliation below, which only READS the forge and never resends. Consent spent
        # by anything else is spent.
        spent_by_unproven_dispatch = (
            str(record.get("status") or "") == "unknown"
            and bool(auth.get("consumed"))
            and bool(str(auth.get("consumed_by") or ""))
            and str(auth.get("consumed_by") or "") == str(record.get("effect_instance_id") or "")
        )
        if bool(auth.get("consumed")) and not spent_by_unproven_dispatch:
            return self._result(
                session,
                ok=False,
                status="authorization_consumed",
                text="That authorization was already used. One authorization is consent for one dispatch.",
            )
        if str(auth.get("action_hash") or "") != str(plan.get("action_hash") or ""):
            return self._result(
                session,
                ok=False,
                status="plan_mismatch",
                text="The authorization names a different plan than the current one; re-request and re-authorize.",
            )
        try:
            expired = datetime.fromisoformat(str(auth.get("expires_at"))) <= datetime.now(timezone.utc)
        except Exception:
            expired = True
        if expired:
            return self._result(
                session,
                ok=False,
                status="authorization_expired",
                text="The forge-action authorization has expired; re-authorize.",
            )

        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        provider = str(session.binding.provider or "")
        namespace = str(session.binding.namespace or "")

        if record.get("status") == "unknown":
            reconciled = self._reconcile_unknown(session, adapter, plan)
            if reconciled is not None:
                return reconciled
            record = dict(session.forge_actions.get(action_hash) or {})

        # -- the picture is re-read NOW: consent bound a head SHA, and a branch that moved
        # since the preview is a different action than the one the operator authorized.
        head_ref = str(plan.get("head_ref") or "")
        head_sha = str(plan.get("head_sha") or "")
        number = str(plan.get("number") or "")
        subject = str(plan.get("subject") or "")
        title = str(plan.get("title") or "")
        body = str(plan.get("body") or "")
        try:
            if action == "create":
                current_head = adapter.resolve_ref(head_ref)
                if current_head != head_sha:
                    return self._binding_refused(
                        session, head_ref=head_ref, authorized=head_sha, actual=current_head or "(absent)"
                    )
                existing = adapter.find_pull_request(head_ref=head_ref, base_ref=str(plan.get("base_ref") or ""))
                if existing is not None:
                    return self._result(
                        session,
                        ok=False,
                        status="pull_request_exists",
                        text=(
                            f"The forge already has an open pull request {head_ref} -> "
                            f"{plan.get('base_ref')} (#{existing.number}, {existing.url or 'no url'}). "
                            "Update or comment on that one; a duplicate is not created."
                        ),
                        existing_number=existing.number,
                        existing_url=existing.url,
                    )
            elif subject == "pull_request" or action == "update":
                current_pr = adapter.describe_pull_request(number)
                if str(current_pr.head_sha or "") != head_sha:
                    return self._binding_refused(
                        session, head_ref=current_pr.head_ref or number, authorized=head_sha, actual=current_pr.head_sha
                    )
                if action == "update":
                    already_title = not title or str(current_pr.title or "") == title
                    already_body = not body or str(current_pr.body or "") == body
                    if already_title and already_body and (title or body):
                        with self._lock:
                            session.forge_authorization["consumed"] = True
                            session.forge_actions[action_hash] = {
                                "action": action,
                                "status": "applied",
                                "action_hash": action_hash,
                                "executed_at": _utcnow(),
                                "result": {
                                    "sent": False,
                                    "verified": "already_current",
                                    "number": number,
                                    "head_sha": head_sha,
                                    "title": current_pr.title,
                                    "url": current_pr.url,
                                },
                            }
                            self._persist(session)
                        return self._result(
                            session,
                            ok=True,
                            status="ok",
                            text=f"Pull request {number} already carries exactly the planned text; nothing was sent.",
                            sent=False,
                            verified="already_current",
                            number=number,
                            head_sha=head_sha,
                            url=current_pr.url,
                        )
        except Exception as exc:
            return self._forge_failure(session, exc, operation=f"forge.pr_{action}_verify")

        # -- duplicate suppression across threads, processes and restarts.
        body_sha = _sha(body)
        if action == "create":
            effect_args = {
                "provider": provider,
                "namespace": namespace,
                "head_ref": head_ref,
                "base_ref": str(plan.get("base_ref") or ""),
                "title_sha": _sha(title),
                "body_sha": body_sha,
            }
            resource_identity = f"{provider}:{namespace}#pr-create:{head_ref}->{plan.get('base_ref')}"
            expected_evidence = {"head_sha": head_sha, "head_ref": head_ref, "base_ref": str(plan.get("base_ref") or "")}
            reconcilability = "reconcilable"
        elif action == "update":
            effect_args = {
                "provider": provider,
                "namespace": namespace,
                "number": number,
                "title_sha": _sha(title),
                "body_sha": body_sha,
            }
            resource_identity = f"{provider}:{namespace}#pr-update:{number}"
            expected_evidence = {"number": number, "head_sha": head_sha}
            reconcilability = "reconcilable"
        else:
            effect_args = {
                "provider": provider,
                "namespace": namespace,
                "number": number,
                "subject": subject,
                "body_sha": body_sha,
            }
            resource_identity = f"{provider}:{namespace}#comment:{subject}:{number}:body-{body_sha[:16]}"
            expected_evidence = {"comment_body_sha": body_sha}
            reconcilability = "unreconcilable"

        reservation: dict[str, Any] = {}
        try:
            from core.runtime_continuity import reserve_logical_effect

            reservation = reserve_logical_effect(
                intent=intent,
                arguments=effect_args,
                resource_identity=resource_identity,
                expected_evidence=expected_evidence,
                session_id=session.session_id,
                turn_id=session.turn_id,
                reconcilability=reconcilability,
            )
        except Exception as exc:
            # FAIL CLOSED. The durable effect ledger is what makes "an identical action cannot
            # double-apply" a fact rather than a hope: dispatching with no reservation means a
            # crash or a concurrent retry could put the write on the forge twice with nothing to
            # stop it. A journal that cannot be written to is a reason not to send, never a
            # detail to route around.
            fault = self._file_fault(
                session,
                "unknown",
                dedupe=f"{session.session_key}:forge_{action}:effect_journal_unavailable",
                context={"reason": "reserve_logical_effect_failed", "exception": type(exc).__name__},
            )
            return self._result(
                session,
                ok=False,
                status="effect_journal_unavailable",
                text=(
                    "The durable effect journal could not be written, so the write was NOT sent: "
                    "without a reservation the same authorized action could be applied twice. "
                    f"Journal error: {type(exc).__name__}."
                ),
                fault_id=fault,
            )
        if str(reservation.get("outcome") or "") == "blocked":
            return self._result(
                session,
                ok=False,
                status="duplicate_effect_blocked",
                text=(
                    "An identical forge action is already reserved and unresolved. Retrying it "
                    "could post it twice, so it stays blocked until its outcome is established."
                ),
                logical_effect_id=str(reservation.get("logical_effect_id") or ""),
            )
        leid = str(reservation.get("logical_effect_id") or "")
        instance = str(reservation.get("effect_instance_id") or "")

        # WRITE-AHEAD, and the dispatch claim too: an external mutation leaves the machine only
        # after BOTH durables hold. Three distinct failure shapes live here, and each gets its
        # own named state:
        #   * a STORAGE EXCEPTION (either journal) before anything is sent -- the reservation is
        #     OURS and unsent, so it is released and the action returns to a RE-ARMABLE
        #     known-unsent state: the same operator intent, the same action hash, retryable
        #     once storage is healthy, exactly one eventual mutation;
        #   * a LOST CLAIM (the ledger's compare-and-set returned False: another executor is
        #     dispatching this very effect right now) -- nothing is written, nothing is
        #     cancelled (the reservation belongs to the winner), and this executor may NOT
        #     assert the winner sent nothing, so the action stays pending, not re-armable;
        #   * success -- the write proceeds.
        # DURABLE WRITE-AHEAD: the journal write must actually SUCCEED -- a swallowed
        # disk-full or rename-denied error is indistinguishable from success to an exception
        # listener, so durability is the explicit return value here, not an exception.
        # The consent is SPENT in the same conditional write, and the decision is taken from the
        # journal AS IT IS ON DISK NOW, never from this call's snapshot: another runtime instance
        # or process serving the same session may have applied this action, be dispatching it,
        # or have spent this very authorization since the snapshot was read. A fresh
        # reservation after a terminal ledger row would otherwise double-send, and no
        # process-local lock can see another process.
        spent = self._spend_forge_authorization(session, action_hash=action_hash, leid=leid, instance=instance)
        session = spent["session"]
        verdict = str(spent["verdict"])
        if verdict != "durable":
            # Our own reservation never dispatched, so releasing it is safe; the ledger releases
            # PREPARED rows only, so another executor's claimed row is never touched.
            _cancel_reservation(leid, instance, reason=f"forge_write_ahead_{verdict}")
        if verdict == "applied":
            result = dict((session.forge_actions.get(action_hash) or {}).get("result") or {})
            mismatches = list(result.get("mismatches") or [])
            if mismatches:
                return self._result(
                    session,
                    ok=False,
                    status="verification_mismatch",
                    text=(
                        "This exact action already landed and its recorded verification STILL fails: "
                        + "; ".join(mismatches)
                        + ". Nothing was sent."
                    ),
                    replayed=True,
                    action=action,
                    **result,
                )
            return self._result(
                session,
                ok=True,
                status="ok",
                text="This exact action is already applied; its recorded result is replayed, nothing was sent.",
                replayed=True,
                action=action,
                **result,
            )
        if verdict == "in_flight":
            return self._result(
                session,
                ok=False,
                status="claim_lost_to_another_executor",
                text=(
                    "Another executor is dispatching this exact action right now, so this call "
                    "wrote nothing and cancelled nothing: the winner owns the outcome and will "
                    "journal it. Re-issuing this action will replay the journaled result once "
                    "the winner finishes."
                ),
            )
        if verdict == "unknown":
            return self._result(
                session,
                ok=False,
                status="unknown_reconciliation_required",
                text=(
                    "Another executor dispatched this exact action and its outcome is UNPROVEN, so "
                    "this call sent nothing: the write may already be on the forge, and its outcome "
                    "must be resolved before any new dispatch."
                ),
                action_hash=action_hash,
            )
        if verdict in {"consumed", "not_authorized", "plan_changed", "expired"}:
            status, text = {
                "consumed": (
                    "authorization_consumed",
                    "That authorization was already used -- the journal on disk records it spent by "
                    "another dispatch. One authorization is consent for one dispatch; nothing was sent.",
                ),
                "not_authorized": (
                    "not_authorized",
                    "No live operator authorization holds for this forge action any more. Nothing was sent.",
                ),
                "plan_changed": (
                    "plan_mismatch",
                    "The authorization in the journal now names a different plan than this action. "
                    "Nothing was sent; re-request and re-authorize.",
                ),
                "expired": ("authorization_expired", "The forge-action authorization has expired; re-authorize."),
            }[verdict]
            return self._result(session, ok=False, status=status, text=text)
        if verdict == "journal_contended":
            return self._result(
                session,
                ok=False,
                status="journal_contended",
                text=(
                    "Another runtime instance or process changed this session's journal while this "
                    "action was being prepared, so this call sent nothing and spent nothing. Re-issue "
                    "the action to act on the journal as it stands now."
                ),
            )
        if verdict == "journal_unavailable":
            self._record_known_unsent(
                session,
                action_hash,
                "session_journal_failure",
                prior_authorization=spent["prior_authorization"],
            )
            return self._result(
                session,
                ok=False,
                status="session_journal_unavailable",
                text=(
                    "The session journal could not be written, so the write was NOT sent: a "
                    "dispatch whose plan and authorization state cannot be durably recorded is "
                    "not a dispatch this runtime will make. Nothing crossed the wire, and the "
                    "action stays re-armable: re-authorize the SAME action once the journal is "
                    "healthy."
                ),
            )
        try:
            claimed = _claim_dispatch(leid, instance, claimed_by=AUTHORITY)
        except Exception as exc:
            # Storage failure while claiming -- KNOWN unsent (nothing has crossed the wire;
            # the reservation is ours and pre-dispatch, so releasing it is safe).
            _cancel_reservation(leid, instance, reason="dispatch_marker_unavailable")
            self._record_known_unsent(
                session,
                action_hash,
                "dispatch_marker_failure",
                prior_authorization=spent["prior_authorization"],
            )
            fault = self._file_fault(
                session,
                "unknown",
                dedupe=f"{session.session_key}:forge_{action}:claim_storage_failure",
                context={"reason": "mark_effect_dispatched_failed", "exception": type(exc).__name__},
            )
            return self._result(
                session,
                ok=False,
                status="effect_journal_unavailable",
                text=(
                    "The durable dispatch claim could not be written, so the write was NOT "
                    "sent: an unmarked dispatch looks like an expired lease to crash recovery "
                    "and could be replayed onto the forge. Nothing crossed the wire, and the "
                    "action stays re-armable: re-authorize the SAME action once the journal is "
                    f"healthy. Journal error: {type(exc).__name__}."
                ),
                fault_id=fault,
            )
        if not claimed:
            # Another executor won the compare-and-set and is dispatching THIS effect right
            # now. The reservation is theirs: releasing or cancelling it would unguard a live
            # mutation, and this executor cannot assert anything about what the winner sent.
            # The journaled state stays "dispatching" -- which is exactly what is happening.
            return self._result(
                session,
                ok=False,
                status="claim_lost_to_another_executor",
                text=(
                    "Another executor claimed this exact action's dispatch and is sending it "
                    "now, so this call wrote nothing and cancelled nothing: the winner owns "
                    "the outcome and will journal it. Re-issuing this action will replay the "
                    "journaled result once the winner finishes."
                ),
            )

        try:
            if action == "create":
                answer = adapter.create_pull_request(
                    title=title, body=body, head_ref=head_ref, base_ref=str(plan.get("base_ref") or ""), draft=True
                )
            elif action == "update":
                answer = adapter.update_pull_request(number, title=title, body=body)
            else:
                answer = adapter.post_comment(number, body, subject=subject)
        except Exception as exc:
            from core.kas.contract import ForgeAcceptedUnreadableError

            if isinstance(exc, TransportUnknownError):
                reason_code = "transport_unproven"
            elif isinstance(exc, ForgeAcceptedUnreadableError):
                # The forge ACCEPTED the write (2xx) but its reply could not be decoded into the
                # typed result. The mutation outcome is UNKNOWN exactly as a lost reply is: the
                # write may be on the forge with no number/URL to cite. Treating a decode error
                # as a definitive refusal would authorize a blind resend.
                reason_code = "accepted_reply_undecodable"
            else:
                reason_code = "definitive_refusal"
            if reason_code == "definitive_refusal":
                _classify_effect(leid, instance, "failed_safe_to_retry", reason=type(exc).__name__)
                with self._lock:
                    session.forge_authorization["consumed"] = True
                    row = session.forge_actions.get(action_hash) or {}
                    row["status"] = "refused"
                    session.forge_actions[action_hash] = row
                    self._persist(session)
            else:
                _classify_effect(leid, instance, "unknown", reason=reason_code)
                with self._lock:
                    row = session.forge_actions.get(action_hash) or {}
                    row["status"] = "unknown"
                    row["unknown_reason"] = reason_code
                    session.forge_actions[action_hash] = row
                    self._persist(session)
            if reason_code == "accepted_reply_undecodable":
                fault = self._file_fault(
                    session,
                    "unknown",
                    dedupe=f"{session.session_key}:forge_{action}:accepted_unreadable",
                    context={"operation": f"forge.pr_{action}", "reason": reason_code},
                )
                self._persist(session)
                return self._result(
                    session,
                    ok=False,
                    status="unknown_reconciliation_required",
                    text=(
                        "The forge ACCEPTED the write (HTTP success) but its reply could not be "
                        "decoded, so the exact result is unproven: the write may already be on "
                        "the forge with no number or URL to cite. This is not a failure, and the "
                        "identical action stays blocked -- a new ordinary authorization is "
                        "refused until the outcome is resolved."
                    ),
                    reason=reason_code,
                    fault_id=fault,
                )
            return self._forge_failure(session, exc, operation=f"forge.pr_{action}")

        # -- exact-result verification against the forge's own answer. The write HAS been
        # accepted at this point, so a LOCAL processing failure while building or checking the
        # typed result can never downgrade the outcome to "safe to resend": it is classified
        # UNKNOWN with the accepted write's receipt, exactly like an undecodable reply.
        try:
            return self._verify_and_record(
                session, adapter_result=answer, action=action, action_hash=action_hash,
                leid=leid, instance=instance, plan=plan, title=title, body=body, body_sha=body_sha,
                number=number, subject=subject, namespace=namespace, head_sha=head_sha,
                head_ref=head_ref,
            )
        except Exception as exc:
            _classify_effect(leid, instance, "unknown", reason="local_processing_after_accept")
            with self._lock:
                row = session.forge_actions.get(action_hash) or {}
                row["status"] = "unknown"
                row["unknown_reason"] = "local_processing_after_accept"
                session.forge_actions[action_hash] = row
                self._persist(session)
            fault = self._file_fault(
                session,
                "unknown",
                dedupe=f"{session.session_key}:forge_{action}:post_accept_processing",
                context={"operation": f"forge.pr_{action}", "exception": type(exc).__name__},
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="unknown_reconciliation_required",
                text=(
                    "The forge ACCEPTED the write, but processing its answer locally failed, so "
                    "the exact result is unproven: the write may already be on the forge with no "
                    "number or URL to cite. This is not a failure, and the identical action stays "
                    "blocked -- a new ordinary authorization is refused until the outcome is "
                    f"resolved. Processing error: {type(exc).__name__}."
                ),
                reason="post_accept_processing_failure",
                fault_id=fault,
            )

    def _verify_and_record(self, session, *, adapter_result, action, action_hash, leid, instance,
                           plan, title, body, body_sha, number, subject, namespace, head_sha,
                           head_ref):
        answer = adapter_result
        if action == "comment":
            mismatches: list[str] = []
            if str(answer.body or "") != body:
                mismatches.append("the stored comment text differs from what was authorized")
            if not str(answer.comment_id or ""):
                mismatches.append("the forge returned no comment id")
            if not str(answer.url or ""):
                mismatches.append("the forge returned no comment url")
            result = {
                "sent": True,
                "subject": subject,
                "number": number,
                "comment_id": str(answer.comment_id or ""),
                "url": str(answer.url or ""),
                "body_sha": body_sha,
                "verified": not mismatches,
            }
            text = (
                f"Comment posted on {namespace}#{number} ({subject}): {answer.url or answer.comment_id}."
                if not mismatches
                else "The comment was submitted but its verification failed: " + "; ".join(mismatches) + "."
            )
        else:
            mismatches = []
            if str(answer.number or "") != number and action == "update":
                mismatches.append("the forge answered with a different pull request number")
            if str(answer.head_sha or "") != head_sha:
                mismatches.append("the head SHA moved between authorization and the forge's answer")
            if action == "create":
                if str(answer.head_ref or "") != head_ref:
                    mismatches.append("the head branch differs from the plan")
                if str(answer.base_ref or "") != str(plan.get("base_ref") or ""):
                    mismatches.append("the base branch differs from the plan")
            if title and str(answer.title or "") != title:
                mismatches.append("the stored title differs from what was authorized")
            if body and str(answer.body or "") != body:
                mismatches.append("the stored body differs from what was authorized")
            if action == "create" and not answer.draft:
                mismatches.append("the forge did not record the pull request as a draft")
            if not str(answer.url or ""):
                mismatches.append("the forge returned no pull request url")
            result = {
                "sent": True,
                "number": str(answer.number or ""),
                "url": str(answer.url or ""),
                "title": str(answer.title or ""),
                "head_ref": str(answer.head_ref or ""),
                "base_ref": str(answer.base_ref or ""),
                "head_sha": str(answer.head_sha or ""),
                "draft": bool(answer.draft),
                "body_sha": body_sha,
                "verified": not mismatches,
            }
            text = (
                f"Draft pull request {namespace}#{answer.number} opened at head "
                f"{str(answer.head_sha or '')[:12]}: {answer.url}."
                if action == "create" and not mismatches
                else f"Pull request {namespace}#{answer.number} text updated: {answer.url}."
                if not mismatches
                else "The write was submitted but its verification failed: " + "; ".join(mismatches) + "."
            )

        # The write landed, so the effect is applied even when verification found a mismatch;
        # the mismatch is reported on its own axis and never downgrades a landed write to
        # "safe to retry". The mismatches live INSIDE the journaled result so a replay (of this
        # process or a restarted one) reproduces the failed verdict instead of success.
        result["mismatches"] = mismatches
        _classify_effect(
            leid,
            instance,
            "applied",
            reason="forge_verified" if not mismatches else "forge_answer_mismatch",
        )
        journal_persist_failed = False
        try:
            with self._lock:
                session.forge_authorization["consumed"] = True
                session.forge_actions[action_hash] = {
                    "action": action,
                    "status": "applied",
                    "action_hash": action_hash,
                    "executed_at": _utcnow(),
                    "logical_effect_id": leid,
                    "effect_instance_id": instance,
                    "result": result,
                }
                # The A6 row above is the DURABLE truth that a restart reconciles from; this
                # journal write may still fail (disk full, rename denied) without changing
                # what happened on the forge.
                journal_persist_failed = not self._persist(session)
        except Exception:
            # The write HAS landed; reporting it as unsent would be a lie, and the A6 row above
            # keeps duplicate suppression armed even without the session row. The missing
            # journal row costs replay, not safety -- said plainly in the result.
            journal_persist_failed = True
            from contextlib import suppress

            with suppress(Exception):
                self._file_fault(
                    session,
                    "unknown",
                    dedupe=f"{session.session_key}:forge_{action}:journal_persist_failed",
                    context={"action_hash": action_hash},
                )
        status = "ok" if not mismatches else "verification_mismatch"
        if journal_persist_failed:
            text += (
                " NOTE: the session journal could not be persisted after the write, so this "
                "result may not replay after a restart; the durable effect row still blocks any "
                "identical dispatch."
            )
        return self._result(
            session,
            ok=not mismatches,
            status=status,
            text=text,
            action=action,
            journal_persist_failed=journal_persist_failed,
            **result,
        )

    def _reconcile_unknown(self, session: RepoSession, adapter, plan: dict[str, Any]):
        """Compare the forge's CURRENT state against the AUTHORIZED state of an unproven action.

        What a current-state read can prove: the forge NOW carries (or does not carry) exactly
        the authorized state. What it cannot prove: that THIS request put it there. Positive
        reconciliation is reported in exactly those words. Every other observation -- a pull
        request with different text or draft state, or none at all after an accepted-but-
        unproven write -- leaves the outcome UNPROVEN: absence or difference in one response is
        not durable proof of non-delivery. The identical action stays blocked until the
        operator resolves it, and NOTHING here ever resends."""

        action = str(plan.get("action") or "")
        action_hash = str(plan.get("action_hash") or "")
        prior_row = dict(session.forge_actions.get(action_hash) or {})
        prior_leid = str(prior_row.get("logical_effect_id") or "")

        def _resolve_continuity(applied: bool, evidence: str) -> None:
            if not prior_leid:
                return
            try:
                from core.runtime_continuity import resolve_unresolved_effect

                resolve_unresolved_effect(
                    logical_effect_id=prior_leid,
                    resolution="CONFIRMED_APPLIED" if applied else "CONFIRMED_FAILED_SAFE_TO_RETRY",
                    source="mechanical",
                    evidence=evidence,
                    resolved_by=AUTHORITY,
                )
            except Exception:
                return

        def _keep_unknown(observation: dict[str, Any], text: str, **details):
            with self._lock:
                row = session.forge_actions.get(action_hash) or {}
                row["status"] = "unknown"
                row.setdefault("observations", []).append({**observation, "observed_at": _utcnow()})
                session.forge_actions[action_hash] = row
                self._persist(session)
            return self._result(session, ok=False, status="reconciliation_mismatch", text=text, **details)

        try:
            if action == "create":
                found = adapter.find_pull_request(
                    head_ref=str(plan.get("head_ref") or ""), base_ref=str(plan.get("base_ref") or "")
                )
                if found is None:
                    # Absence in ONE response is not durable proof the accepted create did not
                    # land: the reply was lost or undecodable precisely because something went
                    # wrong after the forge accepted it. The operator resolves or a differently-
                    # worded action is planned; this exact action never resends on this evidence.
                    return self._result(
                        session,
                        ok=False,
                        status="unknown_reconciliation_required",
                        text=(
                            "A completed lookup currently finds NO pull request for this head/base. "
                            "That is the forge's state NOW; it does not prove the earlier accepted "
                            "create did not land, and this runtime will not resend an accepted write "
                            "on the strength of one empty response. Resolve the action from the "
                            "operator surface (you inspected the forge and it did not land), or plan "
                            "a differently-worded action."
                        ),
                        observed_found_pull_request=False,
                    )
                authorized = {
                    "head_sha": str(plan.get("head_sha") or ""),
                    "head_ref": str(plan.get("head_ref") or ""),
                    "base_ref": str(plan.get("base_ref") or ""),
                    "title": str(plan.get("title") or ""),
                    "body": str(plan.get("body") or ""),
                    "draft": True,
                }
                current_state = {
                    "head_sha": str(found.head_sha or ""),
                    "head_ref": str(found.head_ref or ""),
                    "base_ref": str(found.base_ref or ""),
                    "title": str(found.title or ""),
                    "body": str(found.body or ""),
                    "draft": bool(found.draft),
                }
                mismatches = [
                    f"{field}: authorized {authorized[field]!r}, forge has {current_state[field]!r}"
                    for field in authorized
                    if authorized[field] != current_state[field]
                ]
                if mismatches:
                    # A PR for this head/base exists but is NOT the authorized state: the create
                    # may have landed and been altered, someone else's PR may occupy the pair, or
                    # the create never landed. No reading of one response decides it, so the
                    # outcome stays unproven and nothing is resent.
                    return _keep_unknown(
                        {"found_pull_request": current_state},
                        (
                            "An open pull request for this head/base EXISTS but differs from the "
                            "authorized action: " + "; ".join(mismatches) + ". Whether the earlier "
                            "accepted create landed, was altered, or never landed cannot be "
                            "established from this read; the identical action stays blocked and "
                            "nothing was resent."
                        ),
                        mismatches=mismatches,
                        found_state=current_state,
                    )
                result = {
                    "sent": True,
                    "reconciled": True,
                    "state_matches_authorized": True,
                    "number": str(found.number or ""),
                    "url": str(found.url or ""),
                    "title": str(found.title or ""),
                    "head_ref": str(found.head_ref or ""),
                    "base_ref": str(found.base_ref or ""),
                    "head_sha": str(found.head_sha or ""),
                    "draft": bool(found.draft),
                    "verified": True,
                    "mismatches": [],
                }
                with self._lock:
                    row = session.forge_actions.get(action_hash) or {}
                    row["status"] = "applied"
                    row["reconciled_at"] = _utcnow()
                    row["result"] = result
                    session.forge_actions[action_hash] = row
                    self._persist(session)
                _resolve_continuity(
                    True,
                    "forge currently carries an open pull request whose head, branches, title, "
                    "body and draft state equal the authorized action exactly",
                )
                return self._result(
                    session,
                    ok=True,
                    status="ok",
                    text=(
                        f"Reconciled from current state: the forge now carries open pull request "
                        f"#{found.number} for this head/base whose head SHA, branches, title, body "
                        f"and draft state are EXACTLY the authorized action's -- consistent with "
                        f"the earlier unproven create having landed ({found.url}). The lookup "
                        "proves the authorized state exists; it cannot prove this request "
                        "created it."
                    ),
                    action=action,
                    **result,
                )
            if action == "update":
                current = adapter.describe_pull_request(str(plan.get("number") or ""))
                new_body = str(plan.get("body") or "")
                new_title = str(plan.get("title") or "")
                matches_new = (not new_body or str(current.body or "") == new_body) and (
                    not new_title or str(current.title or "") == new_title
                )
                if (new_body or new_title) and matches_new:
                    result = {
                        "sent": True,
                        "reconciled": True,
                        "state_matches_authorized": True,
                        "number": str(current.number or ""),
                        "url": str(current.url or ""),
                        "title": str(current.title or ""),
                        "head_sha": str(current.head_sha or ""),
                        "verified": True,
                        "mismatches": [],
                    }
                    with self._lock:
                        row = session.forge_actions.get(action_hash) or {}
                        row["status"] = "applied"
                        row["reconciled_at"] = _utcnow()
                        row["result"] = result
                        session.forge_actions[action_hash] = row
                        self._persist(session)
                    _resolve_continuity(True, "pull request currently carries exactly the authorized text")
                    return self._result(
                        session,
                        ok=True,
                        status="ok",
                        text=(
                            f"Reconciled from current state: pull request {current.number} now "
                            f"carries exactly the authorized text -- consistent with the earlier "
                            f"unproven update having landed ({current.url or current.number}). "
                            "The read proves the authorized state exists; it cannot prove this "
                            "request wrote it."
                        ),
                        action=action,
                        **result,
                    )
                prior_body = str(plan.get("prior_body") or "")
                prior_title = str(plan.get("prior_title") or "")
                still_prior = (not new_body or str(current.body or "") == prior_body) and (
                    not new_title or str(current.title or "") == prior_title
                )
                current_state = {"title": str(current.title or ""), "body": str(current.body or "")}
                if still_prior:
                    return self._result(
                        session,
                        ok=False,
                        status="unknown_reconciliation_required",
                        text=(
                            "The pull request still carries exactly the PRIOR text. That is its "
                            "state NOW; a single unchanged read is not durable proof the accepted "
                            "update did not land, so the identical action stays blocked and "
                            "nothing was resent. Resolve it from the operator surface, or plan a "
                            "differently-worded update."
                        ),
                        observed_state=current_state,
                    )
                return _keep_unknown(
                    {"found_state": current_state},
                    (
                        "The pull request carries text that is neither the planned text nor the "
                        "prior text. Whether the accepted update landed and was overwritten, or "
                        "never landed, cannot be established from this read; the identical "
                        "action stays blocked and nothing was resent."
                    ),
                    found_state=current_state,
                )
            return self._result(
                session,
                ok=False,
                status="unknown_reconciliation_required",
                text=(
                    "The earlier comment's outcome is unproven: the reply never came back or "
                    "could not be decoded, and a posted comment cannot be told apart from an "
                    "identical pre-existing one by reading the thread. This exact comment stays "
                    "blocked; the operator may inspect the thread and resolve the action as "
                    "not-applied from the operator surface, or a differently-worded comment can "
                    "be planned."
                ),
            )
        except Exception as exc:
            return self._forge_failure(session, exc, operation="forge.pr_reconcile")

    def _binding_refused(self, session: RepoSession, *, head_ref: str, authorized: str, actual: str):
        return self._result(
            session,
            ok=False,
            status="binding_diverged",
            text=(
                f"The branch `{head_ref}` moved after the plan was authorized "
                f"(authorized {authorized[:12]}, remote now {str(actual)[:12]} or absent). The "
                "authorized action was bound to the old head; nothing was sent. Re-request the "
                "plan at the new head and re-authorize."
            ),
            authorized_head=authorized,
            actual_head=actual,
        )

    def pr_create(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.pr.create` -- open the authorized DRAFT pull request. Draft is not optional
        here: this runtime does not open ready-for-review pull requests, the operator promotes
        one on the forge after reviewing it there."""

        return self._pr_execute("repo.pr.create", arguments, source_context=source_context)

    def pr_update(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.pr.update` -- replace the authorized title/body of one open pull request."""

        return self._pr_execute("repo.pr.update", arguments, source_context=source_context)

    def pr_comment(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        """`repo.pr.comment` -- post the authorized exact comment text on a PR or issue."""

        return self._pr_execute("repo.pr.comment", arguments, source_context=source_context)

    # -- diagnose --------------------------------------------------------------

    def diagnose(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="diagnose", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        path = str(arguments.get("path") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if not path or not reason:
            return self._result(
                session, ok=False, status="invalid_arguments", text="A diagnosis needs both a path and a reason."
            )
        evidence_id = str(arguments.get("evidence_step_id") or "").strip()
        executed = [sid for sid in session.step_order if session.steps[sid].executed]
        has_ci = bool(session.ci.get("failed"))
        if evidence_id:
            record = session.steps.get(evidence_id)
            if record is None or not record.executed:
                return self._result(
                    session,
                    ok=False,
                    status="unsupported_claim",
                    text=(
                        f"Step `{evidence_id}` is not an executed step of this session, so it cannot "
                        f"be the evidence for a diagnosis."
                    ),
                )
        elif not executed and not has_ci:
            return self._result(
                session,
                ok=False,
                status="unsupported_claim",
                text=(
                    "Nothing has been executed and no failing CI job has been read, so there is no "
                    "observed failure for this diagnosis to own. Reproduce it first."
                ),
            )
        with self._lock:
            session.diagnosis = {
                "path": path,
                "line": int(arguments.get("line") or 0) or None,
                "reason": reason,
                "evidence_step_id": evidence_id,
                "ci_failures": [r.get("name") for r in list(session.ci.get("failed") or [])],
                "recorded_at": _utcnow(),
            }
            self._advance(session, "repair")
            self._persist(session)
        return self._result(
            session, ok=True, status="ok", text=f"Diagnosis recorded at `{path}`.", diagnosis=session.diagnosis
        )

    # -- step ------------------------------------------------------------------

    def _classify(self, intent: str) -> str:
        clean = str(intent or "").strip()
        if clean in REPAIR_MUTATION_INTENTS:
            return "mutation"
        if clean in REPAIR_COMMAND_INTENTS:
            return "command"
        if clean in REPAIR_READ_INTENTS:
            return "read"
        return "unknown"

    def _path_lock(self, session: RepoSession, path: str) -> threading.Lock:
        key = (session.root, str(path))
        with self._lock:
            lock = self._path_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._path_locks[key] = lock
            return lock

    def step(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, source_context=source_context)
        if refusal:
            return refusal
        intent = str(arguments.get("intent") or "").strip()
        kind = self._classify(intent)
        if kind == "unknown":
            return self._result(
                session,
                ok=False,
                status="unsupported_intent",
                text=(
                    f"`{intent}` is not a repository step. A repair reads, runs a command, or writes "
                    f"inside the workspace; anything else is a different tool's job."
                ),
            )
        inner = dict(arguments.get("arguments") or {})
        forged = sorted(k for k in inner if str(k).startswith("_"))
        if forged:
            return self._result(
                session,
                ok=False,
                status="invalid_arguments",
                text=f"Inner arguments may not carry runtime trust keys: {', '.join(forged)}.",
            )
        if kind in {"mutation", "command"}:
            gate = self._require_binding(session)
            if gate:
                return gate
        if kind == "mutation" and not session.diagnosis:
            return self._result(
                session,
                ok=False,
                status="stage_violation",
                text="A repair may not run before a diagnosis names what it is repairing.",
            )
        if kind == "mutation":
            # The session opened READ_ONLY. A repair is a different task with a different
            # permission, so Gate 0 evaluates AGAIN against a WORKSPACE_WRITE contract -- and
            # that contract needs the independent reviewer a read never needed. The refusal
            # lands before the mutation, not after it.
            write_gate = self._mutation_gate(session, source_context)
            if write_gate is not None:
                return write_gate

        step_id = str(arguments.get("step_id") or "").strip() or f"s-{uuid.uuid4().hex[:8]}"
        with self._lock:
            existing = session.steps.get(step_id)
            if existing is not None:
                if existing.status == "in_flight":
                    return self._result(
                        session,
                        ok=False,
                        status="in_flight",
                        text=f"Step `{step_id}` is still executing; wait for its result instead of re-issuing it.",
                        step_id=step_id,
                    )
                return self._step_result(session, existing, replayed=True)
            record = StepRecord(
                step_id=step_id,
                intent=intent,
                arguments=inner,
                stage_at=session.stage,
                started_at=_utcnow(),
                status="in_flight",
            )
            record.paths = _argument_paths(inner)
            session.steps[step_id] = record
            session.step_order.append(step_id)
            self._persist(session)

        locks = [self._path_lock(session, p) for p in sorted(set(record.paths))] if kind == "mutation" else []
        for lock in locks:
            lock.acquire()
        try:
            self._execute(session, record, kind, source_context)
        finally:
            for lock in reversed(locks):
                lock.release()
        return self._step_result(session, record)

    def _mutation_gate(self, session: RepoSession, source_context: dict[str, Any] | None):
        """Gate 0 again, for the WORKSPACE_WRITE half. Cached per session once it passes."""

        from core.repoops import task_law

        if bool(session.gate0.get("write_allowed")):
            return None
        context = dict(source_context or {})
        outcome = task_law.evaluate(
            repo_root=session.root,
            head_sha=session.binding.working_head or session.binding.local_head or self._runner(session).head(),
            dirty_paths=(),
            objective=session.objective,
            authority_owner=str(context.get("authority_owner") or "operator"),
            writable_scope=(".",),
            mutating=True,
            builder_model=task_law.active_model_hint(context) or str(session.gate0.get("builder_model") or ""),
            reviewer_model=str(session.gate0.get("reviewer_model") or context.get("reviewer_model") or ""),
            task_id=str(session.gate0.get("task_id") or ""),
        )
        with self._lock:
            session.gate0["write_gate"] = outcome.to_dict()
            session.gate0["write_allowed"] = bool(outcome.allowed)
            self._persist(session)
        if outcome.allowed:
            return None
        return self._result(
            session,
            ok=False,
            status="mutation_not_authorized",
            text=(
                f"Gate 0 refuses the repair before it runs: {outcome.reason} — {outcome.detail}"
            ),
            gate0=outcome.to_dict(),
        )

    def _step_result(self, session: RepoSession, step: StepRecord, *, replayed: bool = False):
        return self._result(
            session,
            ok=step.ok,
            status=step.status,
            text=step.reason or f"`{step.intent}` {step.status}",
            executed=False if replayed else step.executed,
            replayed=replayed,
            step_id=step.step_id,
            intent=step.intent,
            tool_result=step.result,
            receipts=step.receipts,
            paths=step.paths,
            reason=step.reason,
        )

    def _execute(self, session: RepoSession, step: StepRecord, kind: str, source_context: dict[str, Any] | None) -> None:
        from core.effect_gateway import current_effect_ledger
        from core.runtime_execution_tools import execute_runtime_tool

        ledger = current_effect_ledger()
        before = len(ledger.entries()) if ledger is not None else 0
        failure = ""
        try:
            outcome = execute_runtime_tool(
                step.intent, dict(step.arguments), source_context=self._session_context(session, source_context)
            )
        except Exception as exc:
            outcome = None
            failure = f"{type(exc).__name__}: {exc}"
        with self._lock:
            step.completed_at = _utcnow()
            if ledger is not None:
                step.receipts = [dict(e) for e in list(ledger.entries())[before:]]
            if outcome is None:
                step.executed, step.ok = False, False
                step.status = "tool_error" if failure else "unhandled"
                step.reason = failure or f"`{step.intent}` was not handled by the runtime door"
                step.fault_id = self._file_fault(
                    session,
                    "tool_unavailable",
                    dedupe=f"{session.session_key}:{step.step_id}",
                    context={"intent": step.intent, "status": step.status},
                )
            else:
                details = dict(getattr(outcome, "details", {}) or {})
                details.pop("observation", None)
                step.result = json.loads(json.dumps(details, default=str))
                # A command that ran to a return code EXECUTED, even when the command itself
                # failed: a red test during reproduction is the evidence this vertical collects.
                # Its success is carried as data and never folded into the step's own outcome.
                ran_command = kind == "command" and "returncode" in details
                step.executed = bool(outcome.ok) or ran_command
                step.ok = bool(outcome.ok) or ran_command
                step.status = str(outcome.status or ("ok" if outcome.ok else "error"))
                step.reason = "" if step.ok else str(outcome.response_text or "")
                if not step.ok and not step.fault_id:
                    step.fault_id = self._file_fault(
                        session,
                        "confinement_refusal"
                        if step.status in {"scope_violation", "blocked", "denied", "confinement_refusal"}
                        else "permission_denied"
                        if step.status in {"permission_denied", "blocked_by_mode", "pending_approval"}
                        else "unknown",
                        dedupe=f"{session.session_key}:{step.step_id}",
                        context={"intent": step.intent, "status": step.status},
                    )
            if step.executed and kind == "mutation":
                session.binding.working_head = self._runner(session).head()
            if step.executed and kind == "command" and session.stage == "retrieve":
                # A local reproduction is retrieval too: it is the evidence a diagnosis will own.
                self._advance(session, "diagnose")
            if step.executed and step.intent in TEST_INTENTS:
                payload = dict(step.result or {})
                session.tests = {
                    "step_id": step.step_id,
                    "intent": step.intent,
                    "returncode": payload.get("returncode"),
                    "success": bool(payload.get("returncode") in (0, "0")),
                    "at_head": session.binding.working_head or session.binding.local_head,
                    "ran_at": step.completed_at,
                }
                if session.tests["success"] and session.diagnosis:
                    self._advance(session, "review")
                elif session.diagnosis:
                    self._advance(session, "test")
            elif step.executed and kind == "mutation":
                self._advance(session, "test")
            self._persist(session)

    # -- typed git operations --------------------------------------------------

    def git(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        from core.repoops.gitops import DEFAULT_DENIED, OPERATIONS

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        operation = str(arguments.get("operation") or "").strip()
        if operation not in OPERATIONS:
            return self._result(
                session,
                ok=False,
                status="invalid_arguments",
                text=f"`{operation or 'none'}` is not a typed git operation; the set is {', '.join(OPERATIONS)}.",
            )
        if operation == "branch" and bool(arguments.get("delete") or False):
            if not _authorization_permits(session, "branch_delete", source_context):
                fault = self._file_fault(
                    session,
                    "permission_denied",
                    dedupe=f"{session.session_key}:branch_delete",
                    context={"operation": "branch_delete"},
                )
                return self._result(
                    session,
                    ok=False,
                    status="default_denied",
                    text=f"Branch deletion is denied by default: {DEFAULT_DENIED['branch_delete']}.",
                    operation="branch_delete",
                    fault_id=fault,
                )

        step_id = str(arguments.get("step_id") or "").strip() or f"g-{uuid.uuid4().hex[:8]}"
        with self._lock:
            existing = session.steps.get(step_id)
            if existing is not None:
                return self._step_result(session, existing, replayed=True)
            record = StepRecord(
                step_id=step_id,
                intent=f"repo.git.{operation}",
                arguments={k: v for k, v in arguments.items() if k != "repo_session_id"},
                stage_at=session.stage,
                started_at=_utcnow(),
                status="in_flight",
            )
            session.steps[step_id] = record
            session.step_order.append(step_id)
            self._persist(session)

        runner = self._runner(session)
        name = str(arguments.get("name") or "")
        commit = str(arguments.get("commit") or "")
        onto = str(arguments.get("onto") or "")
        message = str(arguments.get("message") or "")
        if operation == "branch":
            outcome = runner.create_branch(name, start_point=commit)
        elif operation == "commit":
            outcome = runner.commit(message=message)
        elif operation == "cherry_pick":
            outcome = runner.cherry_pick(commit)
        elif operation == "revert":
            outcome = runner.revert(commit)
        elif operation == "merge":
            outcome = runner.merge(onto or commit, message=message)
        elif operation == "tag":
            outcome = runner.tag(name, commit=commit, message=message)
        else:
            outcome = runner.restore()

        payload = outcome.to_dict()
        with self._lock:
            record.completed_at = _utcnow()
            record.result = payload
            record.executed = outcome.status in {"executed", "conflict"}
            record.ok = outcome.ok
            record.status = outcome.status
            record.reason = "" if outcome.ok else (outcome.detail or outcome.stderr[:400])
            if not outcome.ok:
                record.fault_id = self._file_fault(
                    session,
                    "unsupported_claim" if outcome.status == "conflict" else "unknown",
                    dedupe=f"{session.session_key}:{step_id}",
                    context={"operation": operation, "status": outcome.status},
                )
            if outcome.head:
                session.binding.working_head = outcome.head
            self._persist(session)
        return self._result(
            session,
            ok=outcome.ok,
            status=outcome.status,
            text=(
                f"`git {operation}` {outcome.status}."
                + (f" Conflicted: {', '.join(outcome.conflicted_paths)}." if outcome.conflicted_paths else "")
            ),
            step_id=step_id,
            # `ok` and `status` are carried by the result itself; spreading the git payload's own
            # copies would collide with them.
            **{k: v for k, v in payload.items() if k not in {"ok", "status"}},
        )

    # -- review ----------------------------------------------------------------

    def review(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="review", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        verdict = str(arguments.get("verdict") or "").strip().lower()
        if verdict not in {"approve", "reject"}:
            return self._result(session, ok=False, status="invalid_arguments", text="A review verdict is `approve` or `reject`.")
        if verdict == "approve" and session.tests.get("success") is not True:
            return self._result(
                session,
                ok=False,
                status="unsupported_claim",
                text=(
                    "An approval needs a green cumulative test run this session actually executed. "
                    f"The last recorded run is: {session.tests or 'none'}."
                ),
            )
        with self._lock:
            session.review = {
                "verdict": verdict,
                "notes": str(arguments.get("notes") or ""),
                "evidence": {"tests": dict(session.tests), "diagnosis": dict(session.diagnosis)},
                "recorded_at": _utcnow(),
            }
            if verdict == "approve":
                self._advance(session, "authorize")
            self._persist(session)
        return self._result(
            session, ok=True, status="ok", text=f"Review recorded: {verdict}.", verdict=verdict,
            evidence=session.review["evidence"],
        )

    # -- push ------------------------------------------------------------------

    def _plan(self, session: RepoSession, *, remote: str, ref: str, force: bool) -> dict[str, Any]:
        runner = self._runner(session)
        sha = runner.head()
        return {
            "remote": remote,
            "remote_url": session.binding.remote_url,
            "ref": ref,
            "sha": sha,
            "from_remote_sha": session.binding.remote_head,
            "force": bool(force),
            "argv": list(runner.push_argv(remote=remote, ref=ref, sha=sha)),
            "binding_id": session.binding.binding_id,
            "review_verdict": str(session.review.get("verdict") or ""),
            "tests_green": bool(session.tests.get("success")),
        }

    def push_request(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="authorize", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        remote = str(arguments.get("remote") or session.binding.remote or "origin").strip()
        ref = str(arguments.get("ref") or session.binding.branch or "").strip()
        if not ref:
            return self._result(session, ok=False, status="invalid_arguments", text="The push plan needs a ref.")
        plan = self._plan(session, remote=remote, ref=ref, force=bool(arguments.get("force") or False))
        plan_hash = _sha(_canonical(plan))
        with self._lock:
            session.push_plan = {**plan, "plan_hash": plan_hash, "built_at": _utcnow()}
            # A new plan invalidates any authorization held for an older one. Consent was for
            # the text the operator saw, and this is different text.
            if str(session.authorization.get("plan_hash") or "") != plan_hash:
                session.authorization = {}
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=(
                f"Push plan: {remote} {plan['sha'][:12]} -> refs/heads/{ref} "
                f"(from {plan['from_remote_sha'][:12] or 'nothing'}). Nothing has been sent."
            ),
            plan=session.push_plan,
            plan_hash=plan_hash,
            requires="an explicit operator Authorize Push naming this exact plan_hash",
        )

    def push_authorize(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="authorize", source_context=source_context)
        if refusal:
            return refusal
        stamped = dict((source_context or {}).get(AUTHORIZATION_CONTEXT_KEY) or {})
        if not stamped:
            return self._result(
                session,
                ok=False,
                status="operator_gesture_required",
                text=(
                    "Authorize Push is the operator's act, not the turn's. This call carries no "
                    "server-stamped operator authorization, so it is refused. The authorization is "
                    "stamped by the owner-local operator surface and cannot be written by a request body."
                ),
            )
        plan_hash = str(arguments.get("plan_hash") or "").strip()
        current = str(session.push_plan.get("plan_hash") or "")
        if not current:
            return self._result(session, ok=False, status="no_plan", text="There is no push plan to authorize.")
        if plan_hash != current:
            return self._result(
                session,
                ok=False,
                status="plan_mismatch",
                text=(
                    "The authorization names a plan that is not the current one. The repository or "
                    "the plan changed after the operator saw it; re-request and re-authorize."
                ),
                authorized_hash=plan_hash,
                current_hash=current,
            )
        if str(stamped.get("plan_hash") or "") != current:
            return self._result(
                session,
                ok=False,
                status="plan_mismatch",
                text="The server-stamped operator authorization names a different plan than the current one.",
            )
        expires = (datetime.now(timezone.utc) + timedelta(seconds=AUTHORIZATION_TTL_SECONDS)).isoformat()
        with self._lock:
            session.authorization = {
                "authorization_id": f"pa-{uuid.uuid4().hex}",
                "plan_hash": current,
                "authorized_at": _utcnow(),
                "expires_at": expires,
                "operator": str(stamped.get("operator") or "operator"),
                "force": bool(stamped.get("force") or False),
                "branch_delete": bool(stamped.get("branch_delete") or False),
                "consumed": False,
            }
            self._advance(session, "push")
            self._persist(session)
        return self._result(
            session,
            ok=True,
            status="ok",
            text=f"Push authorized for plan {current[:16]} until {expires}.",
            authorized=True,
            plan_hash=current,
            expires_at=expires,
        )

    def _spend_push_authorization(
        self,
        session: RepoSession,
        *,
        current: dict[str, Any],
        instance: str,
        recorded: dict[str, Any],
        advance: bool,
    ) -> dict[str, Any]:
        """Spend the push authorization and record the dispatch in ONE conditional journal write,
        decided from the journal as it is on disk NOW.

        This call's snapshot is not the authority: another runtime instance or process may have
        spent this very authorization since the snapshot was read. `_load` replaces a stale copy
        with the journal on disk, and the conditional write (`_persist`) refuses to record the
        spend on top of a journal that moved again in between -- so two executors cannot both
        record themselves as the one consuming the same consent. Returns the verdict (durable |
        consumed | not_authorized | plan_changed | expired | in_flight | journal_unavailable |
        journal_contended), the session copy it was decided on, and the pre-spend state."""
        with self._lock:
            fresh = self._load(session.session_key)
            if fresh is None:
                return {"verdict": "journal_unavailable", "session": session,
                        "prior_authorization": dict(session.authorization or {}),
                        "prior_outcome": dict(session.push_outcome or {})}
            session = fresh
            auth = dict(session.authorization or {})
            prior_outcome = dict(session.push_outcome or {})
            spent = {"session": session, "prior_authorization": auth, "prior_outcome": prior_outcome}
            verdict = ""
            if not auth:
                verdict = "not_authorized"
            elif bool(auth.get("consumed")):
                verdict = "consumed"
            elif str(auth.get("plan_hash") or "") != _sha(_canonical(current)):
                verdict = "plan_changed"
            elif str(prior_outcome.get("outcome") or "") == "dispatching":
                verdict = "in_flight"
            else:
                try:
                    if datetime.fromisoformat(str(auth.get("expires_at"))) <= datetime.now(timezone.utc):
                        verdict = "expired"
                except Exception:
                    verdict = "expired"
            if verdict:
                return {**spent, "verdict": verdict}
            prior_stage = session.stage
            session.authorization = {**auth, "consumed": True, "consumed_at": _utcnow(), "consumed_by": instance}
            session.push_outcome = dict(recorded)
            if advance:
                self._advance(session, "verify")
            try:
                durable = bool(self._persist(session))
            except Exception:
                durable = False
            if durable:
                return {**spent, "verdict": "durable"}
            session.authorization = auth
            session.push_outcome = prior_outcome
            session.stage = prior_stage
            return {**spent, "verdict": "journal_contended" if self._journal_moved(session) else "journal_unavailable"}

    def _push_spend_refusal(self, session: RepoSession, verdict: str):
        """The refusal, naming its cause, for a push whose consent could not be spent durably. Nothing ran."""
        refusals = {
            "consumed": (
                "authorization_consumed",
                "That authorization was already used -- the journal on disk records it spent. One "
                "authorization is consent for one push; nothing was sent.",
            ),
            "not_authorized": (
                "not_authorized",
                "No live Authorize Push holds for this session any more. Nothing was sent.",
            ),
            "plan_changed": (
                "plan_mismatch",
                "The authorization in the journal now names a different plan than this push. "
                "Nothing was sent; re-request and re-authorize the push you mean.",
            ),
            "expired": ("authorization_expired", "The push authorization has expired; re-authorize."),
            "in_flight": (
                "claim_lost_to_another_executor",
                "Another executor is dispatching this session's authorized push right now, so this "
                "call ran nothing and cancelled nothing: the winner owns the outcome and journals it.",
            ),
            "journal_unavailable": (
                "session_journal_unavailable",
                "The session journal could not durably record that this authorization is being "
                "spent, so the push was NOT run: a push whose consent and dispatch are not on disk "
                "is not a push this runtime will make. Nothing was sent, and the authorization is "
                "still unused once the journal is healthy.",
            ),
            "journal_contended": (
                "journal_contended",
                "Another runtime instance or process changed this session's journal while the push "
                "was being prepared, so this call sent nothing and spent nothing. Re-issue the push "
                "to act on the journal as it stands now.",
            ),
        }
        status, text = refusals.get(verdict, ("session_journal_unavailable", refusals["journal_unavailable"][1]))
        return self._result(session, ok=False, status=status, text=text)

    def _reconcile_push_from_ledger(self, session: RepoSession) -> str:
        """Reconcile a journal-stranded DISPATCHING push against its OWN row in the effect ledger.

        The push leid is content-derived (remote, ref, sha, url), so other dispatches of the same
        push -- another session's, an earlier one -- share it; only the row of the instance this
        journal recorded speaks for this dispatch:

        * APPLIED -- the push landed; the git output was lost with the journal gap, said plainly;
        * FAILED_SAFE_TO_RETRY -- git refused it; the consent stays spent on that real attempt;
        * UNKNOWN -- unproven; the session moves to verification against the remote;
        * EXPIRED_PRE_DISPATCH / SUPERSEDED -- it provably never ran: the consent this dispatch
          recorded as spent is given back (only if this dispatch is the one that spent it);
        * PREPARED / DISPATCHED -- an executor holds it right now: "in_flight", left untouched.

        Returns what was decided ("" for nothing). Reads the ledger only; the caller persists."""
        outcome = dict(session.push_outcome or {})
        if str(outcome.get("outcome") or "") != "dispatching":
            return ""
        leid = str(outcome.get("logical_effect_id") or "")
        instance = str(outcome.get("effect_instance_id") or "")
        if not leid or not instance:
            return ""
        try:
            from core.runtime_continuity import logical_effect_row_states

            rows = [row for row in logical_effect_row_states(leid) if row.get("effect_instance_id") == instance]
        except Exception:
            return ""
        if not rows:
            return ""
        state = str(rows[-1].get("state") or "")
        if state in {"prepared", "dispatched"}:
            return "in_flight"
        if state == "applied":
            session.push_outcome = {
                **outcome,
                "outcome": "applied",
                "pushed": True,
                "reconciled_from": "effect_ledger",
                "detail": "the push landed according to the durable effect ledger; its git output was lost with a session-journal gap",
            }
            self._advance(session, "verify")
            return "landed"
        if state == "failed_safe_to_retry":
            session.push_outcome = {**outcome, "outcome": "failed", "pushed": False, "reconciled_from": "effect_ledger"}
            self._advance(session, "verify")
            return "failed"
        if state == "unknown":
            session.push_outcome = {
                **outcome,
                "outcome": "unknown",
                "pushed": False,
                "reconciled_from": "effect_ledger",
                "detail": "outcome unproven; reconciliation required before any retry",
            }
            self._advance(session, "verify")
            return "unknown"
        if state in {"expired_pre_dispatch", "superseded"}:
            auth = dict(session.authorization or {})
            if str(auth.get("consumed_by") or "") == instance:
                session.authorization = {
                    **{k: v for k, v in auth.items() if k not in {"consumed_at", "consumed_by"}},
                    "consumed": False,
                }
            session.push_outcome = {}
            return "unsent"
        return ""

    def push(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        from core.repoops.gitops import DEFAULT_DENIED

        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        refusal = self._guard(session, needs="push", source_context=source_context) or self._require_binding(session)
        if refusal:
            return refusal
        # A push the journal recorded as DISPATCHING -- a crash or a journal gap can strand one --
        # is reconciled against its own ledger row before anything else: a landed, failed or
        # unproven push answers with that truth and never runs again; one another executor holds
        # right now is left alone; a provably-unsent one gets its consent back.
        stranded = self._reconcile_push_from_ledger(session)
        if stranded:
            with self._lock:
                self._persist(session)
        if stranded == "in_flight":
            return self._push_spend_refusal(session, "in_flight")
        if stranded in {"landed", "failed", "unknown"}:
            recorded = dict(session.push_outcome or {})
            return self._result(
                session,
                ok=stranded == "landed",
                status=str(recorded.get("outcome") or ""),
                text=(
                    f"This session's push was stranded by a journal gap and is reconciled from the "
                    f"durable effect ledger: {recorded.get('outcome')}. Nothing was run again."
                ),
                replayed=True,
                **recorded,
            )

        auth = dict(session.authorization or {})
        plan = dict(session.push_plan or {})
        if not auth or not plan:
            return self._result(
                session,
                ok=False,
                status="not_authorized",
                text="No live Authorize Push holds for this session. Request a plan and have the operator authorize it.",
            )
        if bool(auth.get("consumed")):
            return self._result(
                session,
                ok=False,
                status="authorization_consumed",
                text="That authorization was already used. One authorization is consent for one push.",
            )
        try:
            expired = datetime.fromisoformat(str(auth.get("expires_at"))) <= datetime.now(timezone.utc)
        except Exception:
            expired = True
        if expired:
            return self._result(
                session, ok=False, status="authorization_expired", text="The push authorization has expired; re-authorize."
            )
        # The plan is re-derived NOW: an authorization is consent for a state of the world, and
        # if the repository moved since, this is a different push than the one authorized.
        current = self._plan(session, remote=str(plan.get("remote")), ref=str(plan.get("ref")), force=bool(plan.get("force")))
        if _sha(_canonical(current)) != str(auth.get("plan_hash") or ""):
            fault = self._file_fault(
                session, "unsupported_claim", dedupe=f"{session.session_key}:plan_drift", context={"stage": "push"}
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="plan_diverged",
                text=(
                    "The push that would run now is not the push that was authorized: the repository "
                    "changed after consent was given. Nothing was sent."
                ),
                fault_id=fault,
            )
        force = bool(arguments.get("force") or plan.get("force") or False)
        if force and not bool(auth.get("force")):
            fault = self._file_fault(
                session, "permission_denied", dedupe=f"{session.session_key}:force_push", context={"operation": "force_push"}
            )
            self._persist(session)
            return self._result(
                session,
                ok=False,
                status="default_denied",
                text=f"Force push is denied by default: {DEFAULT_DENIED['force_push']}.",
                fault_id=fault,
            )

        simulate = arguments.get("simulate")
        if simulate is None:
            from core import policy_engine

            simulate = not bool(policy_engine.get("repo.real_push_enabled", False))
        simulate = bool(simulate)

        # DUPLICATE EXECUTION: one active reservation per logical effect, across threads and
        # processes and across a restart. A second identical push is blocked, not repeated.
        effect_args = {"remote": current["remote"], "ref": current["ref"], "sha": current["sha"], "url": current["remote_url"]}
        try:
            from core.runtime_continuity import compute_logical_effect_id, reserve_logical_effect

            expected_leid = compute_logical_effect_id(intent="repo.push", arguments=effect_args)
            reservation = reserve_logical_effect(
                intent="repo.push",
                arguments=effect_args,
                resource_identity=f"{current['remote_url']}#refs/heads/{current['ref']}",
                expected_evidence={"remote_sha": current["sha"], "ref": current["ref"]},
                session_id=session.session_id,
                turn_id=session.turn_id,
                reconcilability="reconcilable",
            )
        except Exception as exc:
            # FAIL CLOSED, exactly as the forge lane does. The durable reservation is what makes
            # "an identical push cannot run twice" a fact: a push with no reservation behind it
            # could be repeated by a crash-restart or a concurrent executor with nothing to stop
            # it. Nothing was sent, and the authorization is still unused.
            fault = self._file_fault(
                session,
                "unknown",
                dedupe=f"{session.session_key}:push:effect_journal_unavailable",
                context={"reason": "reserve_logical_effect_failed", "exception": type(exc).__name__},
            )
            return self._result(
                session,
                ok=False,
                status="effect_journal_unavailable",
                text=(
                    "The durable effect ledger could not reserve this push, so it was NOT run: "
                    "without a reservation the same authorized push could run twice. Nothing was "
                    "sent and the authorization is still unused; push again once the ledger is "
                    f"healthy. Ledger error: {type(exc).__name__}."
                ),
                fault_id=fault,
            )
        if str(reservation.get("outcome") or "") == "blocked":
            row = dict(reservation.get("row") or {})
            return self._result(
                session,
                ok=False,
                status="duplicate_effect_blocked",
                text=(
                    "An identical push is already reserved and unresolved. Retrying it could apply it "
                    "twice, so it stays blocked until its outcome is established."
                ),
                logical_effect_id=str(reservation.get("logical_effect_id") or ""),
                existing=row,
            )
        leid = str(reservation.get("logical_effect_id") or "")
        instance = str(reservation.get("effect_instance_id") or "")
        if str(reservation.get("outcome") or "") != "reserved" or not instance or leid != expected_leid:
            # An answer that is not a coherent, owned reservation of THIS push proves nothing about
            # duplicates, so nothing runs on it. A row the store did create for this call is
            # released.
            _cancel_reservation(leid, instance, reason="incoherent_reservation")
            fault = self._file_fault(
                session,
                "unknown",
                dedupe=f"{session.session_key}:push:effect_reservation_invalid",
                context={"reason": "incoherent_reservation", "outcome": str(reservation.get("outcome") or "")[:40]},
            )
            return self._result(
                session,
                ok=False,
                status="effect_reservation_invalid",
                text=(
                    "The durable effect ledger did not return a coherent reservation of this exact "
                    "push, so it was NOT run: an effect this runtime cannot prove it alone holds is "
                    "not one it performs. Nothing was sent, and the authorization is still unused."
                ),
                fault_id=fault,
            )

        identity = {
            "remote": current["remote"],
            "ref": current["ref"],
            "sha": current["sha"],
            "logical_effect_id": leid,
            "effect_instance_id": instance,
        }
        if simulate:
            recorded = {
                "pushed": False,
                "simulated": True,
                "outcome": "simulated",
                "argv": current["argv"],
                "detail": "the push was planned and authorized but not sent; the runtime is not armed for real remote mutation",
                **identity,
                "at": _utcnow(),
            }
        else:
            recorded = {"pushed": False, "simulated": False, "outcome": "dispatching", **identity, "at": _utcnow()}
        # DURABLE WRITE-AHEAD: the consent is spent, and the dispatch recorded, in the journal on
        # disk BEFORE anything can leave the machine -- the same contract the forge lane honors.
        spent = self._spend_push_authorization(
            session, current=current, instance=instance, recorded=recorded, advance=simulate
        )
        session = spent["session"]
        if spent["verdict"] != "durable":
            # Our own reservation never dispatched: releasing it is safe.
            _cancel_reservation(leid, instance, reason=f"push_write_ahead_{spent['verdict']}")
            return self._push_spend_refusal(session, spent["verdict"])
        if simulate:
            # Nothing was dispatched, so the reservation is released rather than classified.
            _cancel_reservation(leid, instance, reason="simulated")
            return self._result(
                session,
                ok=True,
                status="simulated",
                text=f"Push simulated: {current['remote']} {current['sha'][:12]} -> refs/heads/{current['ref']}.",
                **session.push_outcome,
            )

        # THE DURABLE DISPATCH CLAIM. Only the executor whose compare-and-set wins may run the push;
        # a storage failure while claiming and a lost claim are different facts with different
        # truths (the forge lane's `_claim_dispatch` law).
        try:
            claimed = _claim_dispatch(leid, instance, claimed_by=AUTHORITY)
        except Exception as exc:
            # KNOWN UNSENT: nothing has run, and the reservation is ours and pre-dispatch. It is
            # released, and the consent the write-ahead recorded as spent is given back.
            _cancel_reservation(leid, instance, reason="dispatch_marker_unavailable")
            with self._lock:
                session.authorization = dict(spent["prior_authorization"])
                session.push_outcome = dict(spent["prior_outcome"])
                with suppress(Exception):
                    self._persist(session)  # best-effort; the ledger cancellation is the durable fact
            fault = self._file_fault(
                session,
                "unknown",
                dedupe=f"{session.session_key}:push:claim_storage_failure",
                context={"reason": "mark_effect_dispatched_failed", "exception": type(exc).__name__},
            )
            return self._result(
                session,
                ok=False,
                status="effect_journal_unavailable",
                text=(
                    "The durable dispatch claim could not be written, so the push was NOT run: an "
                    "unmarked dispatch looks like an expired lease to crash recovery. Nothing was "
                    "sent, and the authorization is still unused once the ledger is healthy. "
                    f"Ledger error: {type(exc).__name__}."
                ),
                fault_id=fault,
            )
        if not claimed:
            # Another executor won the compare-and-set. The reservation and the journal's dispatch
            # record describe THEIR push: this call cancels nothing and reclassifies nothing.
            return self._result(
                session,
                ok=False,
                status="claim_lost_to_another_executor",
                text=(
                    "Another executor claimed this exact push's dispatch, so this call ran nothing and "
                    "cancelled nothing: the winner owns the outcome and journals it."
                ),
                logical_effect_id=leid,
            )

        runner = self._runner(session)
        code, out, errtext = runner.run(*current["argv"])
        if code == 0:
            outcome = {"pushed": True, "simulated": False, "outcome": "applied", "stdout": out[-2000:], "stderr": errtext[-2000:]}
            classified = _classify_effect(leid, instance, "applied", reason="push_exit_0")
        elif _push_outcome_unknown(errtext):
            # The push left the machine and the reply did not come back. Whether the remote
            # moved is not knowable from here; calling it failed would authorize a retry that
            # could double-apply. It stays UNKNOWN until `repo.verify_remote` proves otherwise.
            outcome = {
                "pushed": False,
                "simulated": False,
                "outcome": "unknown",
                "stderr": errtext[-2000:],
                "detail": "outcome unproven; reconciliation required before any retry",
            }
            classified = _classify_effect(leid, instance, "unknown", reason="transport_unproven", detail=errtext[:400])
            self._file_fault(
                session, "unknown", dedupe=f"{session.session_key}:push", context={"outcome": "unknown"}
            )
        else:
            outcome = {"pushed": False, "simulated": False, "outcome": "failed", "stderr": errtext[-2000:]}
            classified = _classify_effect(leid, instance, "failed_safe_to_retry", reason="push_nonzero", detail=errtext[:400])

        with self._lock:
            session.push_outcome = {**outcome, **identity, "ledger_classified": bool(classified), "at": _utcnow()}
            self._advance(session, "verify")
            try:
                journaled = bool(self._persist(session))
            except Exception:
                journaled = False
        text = f"Push {outcome['outcome']}: {current['remote']} {current['sha'][:12]} -> refs/heads/{current['ref']}."
        if not classified:
            text += (
                " NOTE: the durable effect ledger did not record this outcome, so an identical push "
                "is refused until `repo.verify_remote` reconciles it against the remote."
            )
        if not journaled:
            text += (
                " NOTE: the session journal could not record this outcome after the push; it still "
                "shows the dispatch, and the durable effect ledger is what a restart reconciles from."
            )
        return self._result(
            session,
            ok=outcome["outcome"] == "applied",
            status=outcome["outcome"],
            text=text,
            journal_persist_failed=not journaled,
            **session.push_outcome,
        )

    def verify_remote(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        # A dispatch stranded by a journal gap is first reconciled from its ledger row, so a
        # landed or unproven push reaches verification instead of staying stuck at `push`.
        if self._reconcile_push_from_ledger(session) in {"landed", "failed", "unknown", "unsent"}:
            with self._lock:
                self._persist(session)
        refusal = self._guard(session, needs="verify", source_context=source_context)
        if refusal:
            return refusal
        pushed = dict(session.push_outcome or {})
        if not pushed:
            return self._result(
                session, ok=False, status="nothing_to_verify", text="No push has been attempted in this session."
            )
        expected = str(pushed.get("sha") or "")
        if bool(pushed.get("simulated")):
            with self._lock:
                session.remote_verification = {
                    "verified": False,
                    "outcome": "not_applicable",
                    "expected_sha": expected,
                    "remote_sha": "",
                    "detail": "the push was simulated, so the remote was never asked to move and nothing is verified",
                }
                self._advance(session, "seal")
                self._persist(session)
            return self._result(
                session,
                ok=True,
                status="not_applicable",
                text="The push was simulated; there is no remote change to verify, and none is claimed.",
                **session.remote_verification,
            )
        adapter, forge_err = self._adapter(session, source_context)
        if forge_err is not None:
            return forge_err
        from core.kas.contract import ForgeRefusedError

        try:
            remote_sha = adapter.resolve_ref(str(pushed.get("ref") or ""))
        except ForgeRefusedError as exc:
            if exc.reason == "not_found":
                # The one truthful absence: the ref does not exist on the remote.
                remote_sha = ""
            else:
                return self._forge_failure(session, exc, operation="resolve_ref")
        except Exception as exc:
            return self._forge_failure(session, exc, operation="resolve_ref")
        verified = bool(remote_sha) and remote_sha == expected
        result_outcome = "verified" if verified else ("moved_elsewhere" if remote_sha else "absent")
        with self._lock:
            session.remote_verification = {
                "verified": verified,
                "outcome": result_outcome,
                "expected_sha": expected,
                "remote_sha": remote_sha,
                "checked_at": _utcnow(),
            }
            if str(pushed.get("outcome")) == "unknown" or pushed.get("ledger_classified") is False:
                # An unproven push, or one whose outcome the ledger never recorded (its row is
                # still active and would block every identical push): the remote's own answer
                # resolves it mechanically.
                _resolve_unknown(
                    str(pushed.get("logical_effect_id") or ""),
                    applied=verified,
                    evidence=f"remote ref resolves to {remote_sha or 'nothing'}; expected {expected}",
                )
                session.push_outcome["outcome"] = "applied" if verified else ("failed" if not remote_sha else "unknown")
            self._advance(session, "seal")
            self._persist(session)
        return self._result(
            session,
            ok=verified,
            status=result_outcome,
            text=(
                f"The remote ref resolves to {remote_sha[:12] or 'nothing'}; the push claimed {expected[:12]}."
            ),
            **session.remote_verification,
        )

    # -- receipt ---------------------------------------------------------------

    def receipt(self, arguments: dict[str, Any], *, source_context: dict[str, Any] | None):
        session, err = self._require(arguments.get("repo_session_id"))
        if err:
            return err
        steps = [session.steps[sid] for sid in session.step_order if sid in session.steps]
        executed = [s for s in steps if s.executed]
        refused = [s for s in steps if not s.executed and s.status not in {"in_flight", "pending"}]
        failed = [s for s in executed if not s.ok]
        files_changed = sorted({p for s in executed if s.intent in REPAIR_MUTATION_INTENTS for p in s.paths})
        commands = [
            {"step_id": s.step_id, "intent": s.intent, "returncode": (s.result or {}).get("returncode"), "ok": s.ok}
            for s in executed
            if s.intent in REPAIR_COMMAND_INTENTS
        ]
        receipts = [r for s in steps for r in s.receipts]
        faults: list[dict[str, Any]] = []
        try:
            from core.faults.recorder import faults_for_turn

            faults = [f.to_dict() for f in faults_for_turn(session.turn_key, session_id=session.session_id)]
        except Exception:
            faults = []

        unresolved: list[str] = []
        if not session.binding.bound:
            unresolved.append("the session never bound exact SHAs")
        if session.binding.dirty:
            unresolved.append("the working tree was dirty at bind time, so no SHA describes what was on disk")
        if not session.diagnosis:
            unresolved.append("no diagnosis was recorded")
        if not session.tests:
            unresolved.append("no test run was executed in this session")
        elif not session.tests.get("success"):
            unresolved.append("the last executed test run did not pass")
        if not session.review:
            unresolved.append("no review verdict was recorded")
        if session.push_outcome and str(session.push_outcome.get("outcome")) == "unknown":
            unresolved.append("the push outcome is unknown and has not reconciled")
        if session.push_outcome and str(session.push_outcome.get("outcome")) == "dispatching":
            unresolved.append("the push dispatch has no recorded outcome (in flight, or stranded by a journal gap)")
        if session.push_outcome and not session.remote_verification:
            unresolved.append("the push was never verified against the remote")
        forge_unknown = [
            row
            for row in (session.forge_actions or {}).values()
            if str(row.get("status") or "") == "unknown"
        ]
        for row in forge_unknown:
            unresolved.append(
                f"the {row.get('action')} forge action {str(row.get('action_hash') or '')[:16]} has an "
                "unproven outcome and has not reconciled"
            )

        if session.stage == STAGE_CANCELLED:
            verdict = "cancelled"
        elif session.remote_verification.get("verified"):
            verdict = "completed"
        elif str(session.push_outcome.get("outcome") or "") == "simulated":
            verdict = "simulated"
        elif str(session.push_outcome.get("outcome") or "") == "unknown":
            verdict = "unknown_pending_reconciliation"
        else:
            verdict = "unresolved"

        body = {
            "repo_session_id": session.session_key,
            "objective": session.objective,
            "verdict": verdict,
            "gate0": session.gate0,
            "binding": session.binding.to_dict(),
            "diff": session.diff,
            "ci": session.ci,
            "diagnosis": session.diagnosis,
            "files_changed": files_changed,
            "commands_run": commands,
            "tests": session.tests,
            "review": session.review,
            "push_plan": {k: v for k, v in session.push_plan.items()},
            "authorization": {k: v for k, v in session.authorization.items()},
            "push": session.push_outcome,
            "remote_verification": session.remote_verification,
            "forge_actions": {
                h: {
                    "action": row.get("action"),
                    "status": row.get("status"),
                    "result": row.get("result"),
                    "executed_at": row.get("executed_at") or row.get("created_at"),
                }
                for h, row in (session.forge_actions or {}).items()
            },
            "stages": session.plan(),
            "refused": [{"step_id": s.step_id, "intent": s.intent, "status": s.status, "reason": s.reason} for s in refused],
            "failed": [{"step_id": s.step_id, "intent": s.intent, "status": s.status, "reason": s.reason} for s in failed],
            "unresolved": unresolved,
        }
        seal = _sha(_canonical(body))
        with self._lock:
            session.seal = seal
            if verdict in {"completed", "simulated"} and session.stage == "seal":
                session.stage = STAGE_SEALED
            self._persist(session)
        return self._result(
            session,
            ok=verdict in {"completed", "simulated"},
            status=verdict,
            text=f"RepoOps receipt for {session.session_key}: {verdict}. Seal {seal[:16]}.",
            seal=seal,
            receipts=receipts,
            faults=faults,
            **body,
        )


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _stage_at_least(current: str, needed: str) -> bool:
    if current in {STAGE_CANCELLED, STAGE_SEALED}:
        return False
    if current not in STAGES or needed not in STAGES:
        return False
    return STAGES.index(current) >= STAGES.index(needed)


def _cancel_requested(source_context: dict[str, Any] | None) -> bool:
    """Whether the turn carrying this call has been cancelled.

    Reads the runtime's own cancellation marker rather than inventing a second one: the same
    `cancel_event` / `cancellation_token` the model adapters consult, plus the provider deadline.
    """

    context = dict(source_context or {})
    marker = context.get("cancel_event") or context.get("cancellation_token")
    try:
        if callable(marker):
            return bool(marker())
        is_set = getattr(marker, "is_set", None)
        if callable(is_set):
            return bool(is_set())
    except Exception:
        return False
    try:
        from core.provider_call_deadline import deadline_expired

        return bool(deadline_expired(context))
    except Exception:
        return False


def _authorization_permits(session: RepoSession, operation: str, source_context: dict[str, Any] | None) -> bool:
    """Whether a live, server-stamped operator authorization names this default-denied operation."""

    auth = dict(session.authorization or {})
    if bool(auth.get(operation)):
        return True
    stamped = dict((source_context or {}).get(AUTHORIZATION_CONTEXT_KEY) or {})
    return bool(stamped.get(operation))


def _diff_paths(text: str) -> list[str]:
    paths: list[str] = []
    for line in str(text or "").splitlines():
        if line.startswith("+++ b/"):
            paths.append(line[len("+++ b/") :].strip())
        elif line.startswith("diff --git a/"):
            tail = line[len("diff --git a/") :]
            _, _, right = tail.partition(" b/")
            if right.strip():
                paths.append(right.strip())
    return sorted({p for p in paths if p and p != "/dev/null"})


def _argument_paths(arguments: dict[str, Any]) -> list[str]:
    keys = ("path", "source", "destination", "target", "from_path", "to_path")
    found = [str(arguments.get(k)).strip() for k in keys if str(arguments.get(k) or "").strip()]
    return sorted(set(found))


_UNKNOWN_PUSH_MARKERS = (
    "timed out",
    "timeout",
    "connection reset",
    "broken pipe",
    "early eof",
    "the remote end hung up unexpectedly",
    "rpc failed",
    "unexpected disconnect",
)


def _push_outcome_unknown(stderr: str) -> bool:
    text = str(stderr or "").lower()
    return any(marker in text for marker in _UNKNOWN_PUSH_MARKERS)


def _claim_dispatch(logical_effect_id: str, instance_id: str, *, claimed_by: str) -> bool:
    """The durable dispatch CLAIM, with its two failure shapes kept apart.

    Returns the ledger's own compare-and-set verdict: ``False`` is the DOCUMENTED ordinary
    result for a claim another executor won -- no exception is involved and none should be.
    A storage failure RAISES, so a caller that must distinguish "lost the claim" from "could
    not ask" can. Only the successful claimant may mutate."""
    if not logical_effect_id or not instance_id:
        return False
    from core.runtime_continuity import mark_effect_dispatched

    return bool(
        mark_effect_dispatched(
            logical_effect_id=logical_effect_id, effect_instance_id=instance_id, claimed_by=claimed_by
        )
    )


#: The A6 store's closed outcome vocabulary. Anything else raises there, and a raise swallowed
#: here would leave a resolved effect looking active forever.
EFFECT_OUTCOMES = frozenset({"applied", "failed_safe_to_retry", "unknown"})


def _classify_effect(logical_effect_id: str, instance_id: str, outcome: str, *, reason: str = "", detail: str = "") -> bool:
    """Record a claimed dispatch's proven outcome in the ledger; True only when its row moved.

    A False return is a fact callers report (the row stays active and keeps blocking identical
    effects until reconciled), never a detail to swallow."""
    if not logical_effect_id or not instance_id:
        return False
    if outcome not in EFFECT_OUTCOMES:
        raise ValueError(f"`{outcome}` is not an A6 effect outcome; the set is {sorted(EFFECT_OUTCOMES)}")
    try:
        from core.runtime_continuity import classify_effect_outcome

        return bool(
            classify_effect_outcome(
                logical_effect_id=logical_effect_id,
                effect_instance_id=instance_id,
                outcome=outcome,
                reason=reason,
                detail=detail,
            )
        )
    except Exception:
        return False


def _cancel_reservation(logical_effect_id: str, instance_id: str, *, reason: str = "") -> bool:
    """Release a reservation whose effect never dispatched. Not an outcome -- an un-happening.

    Only a PREPARED row is released (the ledger's own predicate), so a reservation another
    executor has already claimed is never touched. True only when a row was released."""

    if not logical_effect_id or not instance_id:
        return False
    try:
        from core.runtime_continuity import cancel_effect_reservation

        return bool(
            cancel_effect_reservation(
                logical_effect_id=logical_effect_id, effect_instance_id=instance_id, reason=reason
            )
        )
    except Exception:
        return False


def _resolve_unknown(logical_effect_id: str, *, applied: bool, evidence: str) -> None:
    """Reconcile an UNKNOWN push MECHANICALLY, from what the remote now says. Never from prose."""

    if not logical_effect_id:
        return
    try:
        from core.runtime_continuity import resolve_unresolved_effect

        resolve_unresolved_effect(
            logical_effect_id=logical_effect_id,
            resolution="CONFIRMED_APPLIED" if applied else "CONFIRMED_FAILED_SAFE_TO_RETRY",
            source="mechanical",
            evidence=str(evidence),
            resolved_by=AUTHORITY,
        )
    except Exception:
        return


def _push_mechanical_resolver(row: dict[str, Any]):
    """Prove a push's outcome by asking the forge what the ref resolves to now.

    Registered with A6 so an unresolved push reconciles without this runtime being live: the
    evidence is the remote's own answer, and an unreachable remote stays honestly UNKNOWN.
    """

    from core.effect_reconciliation import EffectResolution, ResolutionOutcome
    from core.kas.contract import ForgeRefusedError
    from core.repoops.forge import open_forge, provider_for_remote

    expected = ""
    ref = ""
    try:
        expected = str(json.loads(str(row.get("expected_evidence_json") or "{}")).get("remote_sha") or "")
        ref = str(json.loads(str(row.get("expected_evidence_json") or "{}")).get("ref") or "")
    except Exception:
        expected, ref = "", ""
    identity = str(row.get("resource_identity") or "")
    url, _, _ = identity.partition("#")
    provider = provider_for_remote(url)
    if not (expected and ref and provider):
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence="the reservation does not carry a forge-resolvable ref and expected SHA",
        )
    from core.repoops.forge import namespace_for_remote

    try:
        adapter = open_forge(provider=provider, namespace=namespace_for_remote(url))
        actual = adapter.resolve_ref(ref)
    except ForgeRefusedError as exc:
        if exc.reason == "not_found":
            # The ref does not exist on the remote, so the push never landed -- the same
            # FAILED_SAFE_TO_RETRY a pre-typing empty answer carried.
            return EffectResolution(
                outcome=ResolutionOutcome.FAILED_SAFE_TO_RETRY,
                source="mechanical",
                evidence="the ref does not exist on the remote, so the push never landed",
            )
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence=f"the forge refused to answer ({exc.reason}); the outcome stays unknown",
        )
    except Exception as exc:
        return EffectResolution(
            outcome=ResolutionOutcome.STILL_UNKNOWN,
            source="mechanical",
            evidence=f"the forge could not be asked: {type(exc).__name__}",
        )
    if actual and actual == expected:
        return EffectResolution(
            outcome=ResolutionOutcome.APPLIED, source="mechanical", evidence=f"{ref} resolves to {actual}"
        )
    if not actual:
        return EffectResolution(
            outcome=ResolutionOutcome.FAILED_SAFE_TO_RETRY,
            source="mechanical",
            evidence=f"{ref} does not exist on the remote, so the push never landed",
        )
    return EffectResolution(
        outcome=ResolutionOutcome.STILL_UNKNOWN,
        source="mechanical",
        evidence=f"{ref} resolves to {actual}, which is neither the pushed SHA nor absent",
    )


def register_push_resolver() -> None:
    """Teach A6 how to prove a push. Idempotent; safe to call at import."""

    try:
        from core.effect_reconciliation import Reconcilability, register_effect_resolver

        register_effect_resolver("repo.push", _push_mechanical_resolver, reconcilability=Reconcilability.RECONCILABLE)
    except Exception:
        return


_RUNTIME = RepoOpsRuntime()


#: How long an open RepoOps session keeps seating its control-plane tools for the chat session
#: that owns it. Same shape as the code-task lane's active-task TTL: a session nobody has
#: touched for this long is not "open" for seating purposes, and a restarted daemon re-reads
#: the same journals and seats the same tools.
_ACTIVE_SESSION_TTL_SECONDS = 900

#: The control-plane tools an open session's CURRENT stage needs, bounded like the code-task
#: stage seats. An open session must not become a blank cheque: these are seats ON TOP of the
#: normal offer, and every forge WRITE among them still sits behind its operator authorization.
_STAGE_REPO_SEATS: dict[str, tuple[str, ...]] = {
    "inspect": ("repo.inspect", "repo.bind"),
    "bind": ("repo.bind", "repo.diff"),
    "retrieve": ("repo.diff", "repo.issue", "repo.ci.jobs", "repo.pr.request"),
    "diagnose": ("repo.ci.jobs", "repo.pr.request"),
    "repair": ("repo.pr.request", "repo.receipt"),
    "test": ("repo.pr.request",),
    "review": ("repo.pr.request", "repo.receipt"),
    "authorize": ("repo.pr.request", "repo.pr.create"),
    "push": ("repo.pr.create", "repo.pr.update", "repo.pr.comment"),
    "verify": ("repo.pr.update", "repo.pr.comment", "repo.verify_remote"),
    "seal": ("repo.receipt",),
}


def active_repo_session_intents(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Offered by repo capability: the control-plane tools this chat session's OPEN RepoOps
    session needs right now -- read from the session journal, never from the conversation, so
    a restarted daemon seats the same tools. Mirrors the code-task lane's
    ``active_task_control_intents``: without it, an open repository session loses its tools on
    the very next conversational turn, and a multi-step repository workflow cannot be driven
    through chat at all. Bounded to one session (the most recently updated) and five seats;
    a planned forge action seats its own executor beside the stage seats."""
    context = source_context if isinstance(source_context, dict) else {}
    session = str(context.get("session_id") or context.get("runtime_session_id") or "").strip()
    if not session:
        return ()
    root = session_dir()
    if not root.is_dir():
        return ()
    now = datetime.now(timezone.utc)
    best_payload: dict[str, Any] | None = None
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(payload.get("session_id") or "") != session:
            continue
        if str(payload.get("stage") or "") == STAGE_CANCELLED:
            continue
        try:
            updated = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        except ValueError:
            continue
        if (now - updated).total_seconds() > _ACTIVE_SESSION_TTL_SECONDS:
            continue
        if best_payload is None or str(payload.get("updated_at") or "") > str(best_payload.get("updated_at") or ""):
            best_payload = payload
    if best_payload is None:
        return ()
    stage = str(best_payload.get("stage") or "")
    seats = list(_STAGE_REPO_SEATS.get(stage, ())[:4])
    planned_action = str(dict(best_payload.get("forge_plan") or {}).get("action") or "").strip()
    if planned_action:
        # The planned action's executor sits FIRST among the stage seats: a plan the operator
        # is about to authorize must not be evicted by the stage's own list.
        seats.insert(0, f"repo.pr.{planned_action}")
    return tuple(dict.fromkeys(("repo.session.open", *seats)))[:5]


def repo_ops_runtime() -> RepoOpsRuntime:
    return _RUNTIME


def dispatch_repo_intent(
    intent: str, arguments: dict[str, Any], *, source_context: dict[str, Any] | None, workspace_root: Path
):
    """The dispatch seam ``core.runtime_execution_tools`` forwards every ``repo.*`` call to."""

    runtime = repo_ops_runtime()
    if intent == "repo.session.open":
        return runtime.open(arguments, workspace_root=workspace_root, source_context=source_context)
    handlers = {
        "repo.inspect": runtime.inspect,
        "repo.bind": runtime.bind,
        "repo.diff": runtime.diff,
        "repo.issue": runtime.issue,
        "repo.issue.comments": runtime.issue_comments,
        "repo.ci.jobs": runtime.ci_jobs,
        "repo.ci.log": runtime.ci_log,
        "repo.ci.artifacts": runtime.ci_artifacts,
        "repo.diagnose": runtime.diagnose,
        "repo.step": runtime.step,
        "repo.git": runtime.git,
        "repo.review": runtime.review,
        "repo.push.request": runtime.push_request,
        "repo.push.authorize": runtime.push_authorize,
        "repo.push": runtime.push,
        "repo.verify_remote": runtime.verify_remote,
        "repo.receipt": runtime.receipt,
        "repo.pr.request": runtime.pr_request,
        "repo.pr.authorize": runtime.pr_authorize,
        "repo.pr.create": runtime.pr_create,
        "repo.pr.update": runtime.pr_update,
        "repo.pr.comment": runtime.pr_comment,
    }
    if intent in {"repo.ci.rerun", "repo.ci.cancel"}:
        return runtime.ci_control(intent, arguments, source_context=source_context)
    handler = handlers.get(intent)
    if handler is None:
        return None
    return handler(arguments, source_context=source_context)


register_push_resolver()

__all__ = [
    "AUTHORIZATION_CONTEXT_KEY",
    "AUTHORIZATION_TTL_SECONDS",
    "FORGE_ACTION_CONTEXT_KEY",
    "STAGES",
    "RepoBinding",
    "RepoOpsRuntime",
    "RepoSession",
    "StepRecord",
    "dispatch_repo_intent",
    "register_push_resolver",
    "repo_ops_runtime",
    "session_dir",
]
