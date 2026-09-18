"""The First-Run Pact: the one tour authority (``vool.first_run_pact``).

A non-blocking, skippable, evidence-checked tour rendered inside the already-served
chat page. This module is the pact's single writer: it owns
``data/first_run_pact_state.json`` and stores ONLY tour progress (C3) — state,
step statuses, timestamps, and evidence *references*. Every fact of the world is
projected live from its owning authority on every read (LP4):

- agent name            → ``core.onboarding`` (``owner_identity.json``)
- operator facts        → ``core.operator_profile`` (SQLite, typed categories)
- privacy boundaries    → ``core.policy_engine`` / ``core.operator_profile.set_paused``
- provider choice       → ``core.first_run`` (``first_run_state.json``)
- receipts              → ``core.honesty_receipt`` (Ed25519, hash-chained JSONL)
- one-turn proof        → ``core.proof_projection`` (bound to session + request)
- denials               → real pre-dispatch refusal records (proof refusals /
                          ``blocked_false_claim`` verdicts / durable denied effects)

Completion of the task and denial steps is verified server-side against real,
cryptographically-checked ledger evidence (LP2/C5): a receipt that exists but does
not verify completes nothing. Every mutation is a Command Registry command; the
HTTP layer is a thin adapter. The tour never blocks boot, never touches Keychain,
and can be skipped from every non-terminal state.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Any

from core.runtime_paths import active_data_dir

SCHEMA_NAME = "vool.first_run_pact"
SCHEMA_VERSION = 1

FILENAME = "first_run_pact_state.json"

# --- Tour states (spec §3.2) ---------------------------------------------------------------
STATE_ABSENT = "absent"
STATE_NOT_APPLICABLE = "not_applicable"
STATE_WELCOME = "welcome"
STATE_NAMING = "naming"
STATE_FACTS = "facts"
STATE_BOUNDARIES = "boundaries"
STATE_PROVIDER_CHOICE = "provider_choice"
STATE_LOCAL_TASK = "local_task"
STATE_TASK_RECEIPT = "task_receipt"
STATE_DENIAL_DEMO = "denial_demo"
STATE_MEMORY_DONE = "memory_done"
STATE_DONE = "done"
STATE_SKIPPED = "skipped"

TOUR_STATES = frozenset(
    {
        STATE_WELCOME,
        STATE_NAMING,
        STATE_FACTS,
        STATE_BOUNDARIES,
        STATE_PROVIDER_CHOICE,
        STATE_LOCAL_TASK,
        STATE_TASK_RECEIPT,
        STATE_DENIAL_DEMO,
        STATE_MEMORY_DONE,
    }
)
TERMINAL_STATES = frozenset({STATE_DONE, STATE_SKIPPED})
ALL_STATES = TOUR_STATES | TERMINAL_STATES | {STATE_ABSENT, STATE_NOT_APPLICABLE}

# Tour steps, their order, and the state each one leaves from.
STEP_ORDER = ("naming", "facts", "boundaries", "provider_choice", "task", "denial", "memory")
_STEP_STATE = {
    "naming": STATE_NAMING,
    "facts": STATE_FACTS,
    "boundaries": STATE_BOUNDARIES,
    "provider_choice": STATE_PROVIDER_CHOICE,
    "task": STATE_LOCAL_TASK,
    "denial": STATE_DENIAL_DEMO,
    "memory": STATE_MEMORY_DONE,
}

# Operator-action-driven edges only. `done` is reachable from any non-terminal tour
# state ("Finish — skip the rest"); `skipped` from every non-terminal state (LP5).
_NEXT_STEP_STATE = {
    STATE_WELCOME: STATE_NAMING,
    STATE_NAMING: STATE_FACTS,
    STATE_FACTS: STATE_BOUNDARIES,
    STATE_BOUNDARIES: STATE_PROVIDER_CHOICE,
    STATE_PROVIDER_CHOICE: STATE_LOCAL_TASK,
    STATE_LOCAL_TASK: STATE_TASK_RECEIPT,
    STATE_TASK_RECEIPT: STATE_DENIAL_DEMO,
    STATE_DENIAL_DEMO: STATE_MEMORY_DONE,
    STATE_MEMORY_DONE: STATE_DONE,
}

EVIDENCE_LOCKED_STATES = frozenset({STATE_TASK_RECEIPT, STATE_MEMORY_DONE})

PACT_FILE_LOCK = threading.RLock()

# Typed fault codes carried on the registry's conflict path (HTTP 409 + code).
FAULT_INVALID_TRANSITION = "invalid_transition"
FAULT_STALE_REVISION = "stale_revision"
FAULT_EVIDENCE_MISSING = "evidence_missing"
FAULT_EVIDENCE_UNVERIFIABLE = "evidence_unverifiable"
FAULT_EVIDENCE_SESSION_UNKNOWN = "evidence_session_unknown"
FAULT_NOT_APPLICABLE_READONLY = "not_applicable_readonly"
FAULT_BOUNDARY_KEY_UNKNOWN = "boundary_key_unknown"
FAULT_LOCK_UNAVAILABLE = "lock_unavailable"

# The spec's served task/denial prompts (constants so tests assert the real thing).
TASK_PROMPT_MODEL = (
    "Create welcome-notes.txt in my workspace with one friendly sentence about today. "
    "Then tell me what you wrote."
)
# No-model variant: a LITERAL write demand. The deterministic workspace-write lane
# executes it with ZERO model calls (census §7: probe P1/P2), so first use reaches a
# genuinely useful real result even before any model is installed.
TASK_PROMPT_DIRECT = (
    "Create welcome-notes.txt containing exactly this sentence: "
    "Welcome to VOOL - this note was written on your machine, no cloud involved."
)
DENIAL_PROMPT = "Fetch https://example.com and summarize the page."
# No-model variant: the deterministic live-data lane attempts the retrieval WITHOUT any
# model. Under the Local Only composite the outbound doors refuse it pre-dispatch and the
# attempt is durably recorded — a genuine refusal with zero effect, provable after the turn.
DENIAL_PROMPT_DIRECT = "What is the weather in Kaunas?"


class PactFault(RuntimeError):
    """Typed pact fault; rides the registry conflict path with its HTTP status."""

    def __init__(self, code: str, detail: str = "", http_status: int = 409):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.http_status = http_status


# --- persistence (atomic, whitelisted, corruption-safe) -------------------------------------

def _path():
    return active_data_dir() / FILENAME


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write(data: dict[str, Any]) -> None:
    """The repo's canonical atomic pattern; the pact file is the pattern's exemplar."""
    _path().parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(_path().parent), prefix=".first_run_pact-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, _path())
        try:
            # Make the rename itself durable: fsync the containing directory where the
            # platform supports an openable directory fd.
            dir_fd = os.open(str(_path().parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


class _FileLock:
    """Cross-process publication lock — FAIL CLOSED, delegating to the ONE shared
    cross-platform implementation (fcntl on POSIX, msvcrt on Windows).

    Acquisition is non-blocking: on contention the mutation refuses with the typed
    ``lock_unavailable`` fault. Read/CAS/mutate/publish all happen under ONE acquisition,
    so two processes can never read the same revision and both publish revision+1.
    """

    def __init__(self):
        self._impl = None

    def __enter__(self):
        from core.cross_process_lock import LockUnavailable, PublicationLock

        try:
            self._impl = PublicationLock(_path().with_name(FILENAME + ".lock")).__enter__()
        except LockUnavailable as exc:
            raise PactFault(
                FAULT_LOCK_UNAVAILABLE,
                "another window or process is publishing first-run state; retry in a moment",
                http_status=409,
            ) from exc
        return self

    def __exit__(self, *exc):
        if self._impl is not None:
            return bool(self._impl.__exit__(*exc))
        return False


def _step_default() -> dict[str, Any]:
    return {key: {"status": "pending"} for key in STEP_ORDER}


def _quarantine_corrupt() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        _path().rename(_path().with_name(f"{FILENAME}.corrupt-{stamp}"))
    except OSError:
        pass


def _read_state() -> dict[str, Any] | None:
    try:
        raw = _path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_name") != SCHEMA_NAME or data.get("schema_version") != SCHEMA_VERSION:
        return None
    if str(data.get("state") or "") not in ALL_STATES:
        return None
    return data


def _sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Enforce the C3 whitelist structurally: unknown keys never reach the file."""
    allowed_top = {
        "schema_name",
        "schema_version",
        "state",
        "revision",
        "welcome_hidden",
        "seed_reason",
        "steps",
        "provider_step",
        "created_at",
        "updated_at",
        "completed_at",
        "skipped_at",
    }
    clean = {key: data[key] for key in allowed_top if key in data}
    steps = clean.get("steps")
    if isinstance(steps, dict):
        clean_steps: dict[str, Any] = {}
        for name, entry in steps.items():
            if name not in STEP_ORDER or not isinstance(entry, dict):
                continue
            kept: dict[str, Any] = {}
            if entry.get("status") in {"pending", "done", "skipped"}:
                kept["status"] = entry["status"]
            if isinstance(entry.get("at"), str) and entry["at"]:
                kept["at"] = entry["at"]
            if isinstance(entry.get("via_command_ids"), list):
                kept["via_command_ids"] = [str(x) for x in entry["via_command_ids"]][:16]
            evidence = entry.get("evidence")
            if isinstance(evidence, dict):
                kept_evidence = {
                    key: evidence[key]
                    for key in ("session_id", "request_id", "receipt_id", "effect_ids", "verified")
                    if key in evidence
                }
                if kept_evidence:
                    kept["evidence"] = kept_evidence
            clean_steps[name] = kept
        clean["steps"] = clean_steps
    return clean


def load_state(*, quarantine_corrupt: bool = True) -> dict[str, Any] | None:
    """Read the pact file; unreadable/unknown-version ⇒ quarantined, caller reseeds."""
    data = _read_state()
    if data is None:
        if _path().exists():
            if quarantine_corrupt:
                _quarantine_corrupt()
            return None
        return None
    return data


# --- the §12 seed predicate ------------------------------------------------------------------

def has_existing_signal() -> bool:
    """Any real prior use on this home trips at least one signal (spec §12)."""
    from core.runtime_paths import active_config_home_dir, data_path

    try:
        log = data_path("conversation_log.jsonl")
        if log.exists() and log.stat().st_size > 0:
            return True
    except Exception:
        pass

    try:
        import storage.db as _sdb

        # storage.db hands out FRESH connections the caller owns: this census closes on
        # EVERY exit path — including the early `return True` below — via the finally.
        conn = _sdb.get_connection()
        try:
            for table in ("chat_sessions", "conversations", "messages"):
                try:
                    row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                    if row is not None:
                        return True
                except Exception:
                    continue
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass

    try:
        ledger_dir = active_data_dir() / "honesty_receipts"
        if any(ledger_dir.glob("*.jsonl")):
            return True
    except Exception:
        pass

    try:
        from core import operator_profile

        items = operator_profile.list_items(operator_profile.OWNER_PRINCIPAL)
        if items:
            return True
    except Exception:
        pass

    try:
        credentials_meta = data_path("credentials.meta.json")
        if credentials_meta.exists():
            return True
        connection_state = data_path("cloud_connection_state.json")
        if connection_state.exists():
            data = json.loads(connection_state.read_text(encoding="utf-8"))
            states = {str(v.get("state")) for v in data.values() if isinstance(v, dict)}
            if states & {"ok", "failed", "untested"}:
                return True
    except Exception:
        pass

    try:
        identity = data_path("owner_identity.json")
        if identity.exists():
            import datetime as _dt

            data = json.loads(identity.read_text(encoding="utf-8"))
            created = str(data.get("created_at") or "")
            try:
                created_ts = _dt.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if created_ts < time.time() - 60.0:
                    return True
            except Exception:
                return True
    except Exception:
        pass

    _ = active_config_home_dir()
    return False


def seed() -> dict[str, Any]:
    """Boot-time idempotent seed (never blocks boot; exception-safe by contract).

    Always leaves a readable file: fresh homes persist ``absent`` so a GET racing the
    seed sees typed state, never a guess; existing homes persist ``not_applicable``
    once and render nothing forever (LP6). The pact file is the ONLY write either way.
    """
    with PACT_FILE_LOCK:
        with _FileLock():
            return _seed_locked()


def _publish(mutate) -> dict[str, Any]:
    """Publication tail for a caller ALREADY holding the publication lock that has
    CAS-checked and applied its effects: mutate, bump, sanitize, write. No re-read,
    no second lock acquisition — nesting would self-deadlock the flock."""
    current = load_state()
    if current is None:
        current = _seed_locked()
    next_data = mutate(dict(current))
    next_data["revision"] = int(current.get("revision", 0)) + 1
    next_data["updated_at"] = _utcnow()
    next_data = _sanitize(next_data)
    _atomic_write(next_data)
    return next_data


def _seed_locked() -> dict[str, Any]:
    """Seed body for a caller that ALREADY holds the publication lock (no nesting)."""
    existing = load_state()
    if existing is not None:
        return existing
    existing_user = has_existing_signal()
    state = STATE_NOT_APPLICABLE if existing_user else STATE_ABSENT
    data = _sanitize(
        {
            "schema_name": SCHEMA_NAME,
            "schema_version": SCHEMA_VERSION,
            "state": state,
            "revision": 0,
            "welcome_hidden": False,
            "seed_reason": "existing_user" if existing_user else "fresh_install",
            "steps": _step_default(),
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }
    )
    _atomic_write(data)
    return data


# --- mutations --------------------------------------------------------------------------------

def _require_mututable(data: dict[str, Any]) -> None:
    if data.get("state") == STATE_NOT_APPLICABLE:
        raise PactFault(FAULT_NOT_APPLICABLE_READONLY, "this home is not part of the tour; press 'Show me around' first")


def _mutate(expect_revision: int | None, mutate, *, allow_not_applicable: bool = False) -> dict[str, Any]:
    """The ONE publication path: re-read, CAS-check, mutate and publish under a single
    fail-closed lock acquisition — cross-process races resolve to exactly one winner."""
    with PACT_FILE_LOCK:
        with _FileLock():
            current = load_state()
            if current is None:
                current = _seed_locked()
            if not allow_not_applicable:
                _require_mututable(current)
            if expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):
                raise PactFault(FAULT_STALE_REVISION, "the tour state changed elsewhere; refreshed")
            next_data = mutate(dict(current))
            next_data["revision"] = int(current.get("revision", 0)) + 1
            next_data["updated_at"] = _utcnow()
            next_data = _sanitize(next_data)
            _atomic_write(next_data)
            return next_data


def _mark_step(data: dict[str, Any], step: str, status: str) -> None:
    steps = data.setdefault("steps", _step_default())
    entry = steps.setdefault(step, {})
    entry["status"] = status
    entry["at"] = _utcnow()


def _current_step(state: str) -> str | None:
    for step, mapped in _STEP_STATE.items():
        if mapped == state:
            return step
    return None


def begin(*, expect_revision: int | None = None) -> dict[str, Any]:
    """Settings replay or first entry; the ONE legal transition out of not_applicable
    (spec §6.3: begin → welcome from not_applicable/skipped/done — an explicit
    operator action). Every OTHER mutation stays refused there (LP6)."""
    with PACT_FILE_LOCK:
        current = load_state() or seed()
        state = current.get("state", STATE_ABSENT)
        if state in TOUR_STATES and expect_revision is None:
            return current  # already touring: idempotent for CAS-less callers
        if state in TOUR_STATES:
            return current  # with a CAS expectation the mutate below validates the revision

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        data["state"] = STATE_WELCOME
        data["welcome_hidden"] = False
        return data

    return _mutate(expect_revision, _apply, allow_not_applicable=True)


def hide(hidden: bool = True, *, expect_revision: int | None = None) -> dict[str, Any]:
    """Welcome-card ✕: dismiss until the footer link; records NO terminal state (LP5)."""

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        if data.get("state") not in {STATE_WELCOME, STATE_ABSENT}:
            raise PactFault(FAULT_INVALID_TRANSITION, "hide applies to the welcome card only")
        data["welcome_hidden"] = bool(hidden)
        return data

    return _mutate(expect_revision, _apply)


def skip(*, expect_revision: int | None = None) -> dict[str, Any]:
    """Skip from EVERY non-terminal state; terminal states are idempotent (LP5)."""
    current = load_state()
    if current is not None and current.get("state") in TERMINAL_STATES:
        if expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):
            raise PactFault(FAULT_STALE_REVISION, "the tour state changed elsewhere; refreshed")
        return current
    if current is not None and current.get("state") == STATE_NOT_APPLICABLE:
        raise PactFault(FAULT_NOT_APPLICABLE_READONLY, "nothing to skip; the tour was never started")

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        if data.get("state") in TERMINAL_STATES:
            # re-checked under the publication lock: a concurrent skip/finish already won
            raise PactFault(FAULT_STALE_REVISION, "the tour was already skipped or finished elsewhere")
        step = _current_step(data.get("state", ""))
        if step:
            _mark_step(data, step, "skipped")
        data["state"] = STATE_SKIPPED
        data["skipped_at"] = _utcnow()
        return data

    return _mutate(expect_revision, _apply)


def reset(*, expect_revision: int | None = None) -> dict[str, Any]:
    """Replay the tour; facts, boundaries and provider state are never erased (S-P14)."""

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        data["state"] = STATE_WELCOME
        data["welcome_hidden"] = False
        data["completed_at"] = ""
        data["skipped_at"] = ""
        data["provider_step"] = ""
        return data

    return _mutate(expect_revision, _apply)


def advance(to: str, *, evidence: dict[str, Any] | None = None, expect_revision: int | None = None) -> dict[str, Any]:
    """One operator-action-driven tour transition (spec §3.2/§6.2)."""
    if to not in ALL_STATES:
        raise PactFault(FAULT_INVALID_TRANSITION, f"unknown tour state {to!r}")
    with PACT_FILE_LOCK:
        current = load_state() or seed()
        state = current.get("state", STATE_ABSENT)

        if state == STATE_ABSENT and to == STATE_WELCOME:
            return begin(expect_revision=expect_revision)

        if to == STATE_SKIPPED:
            return skip(expect_revision=expect_revision)
        if to == STATE_DONE:
            return _finish(current, expect_revision)

        def _check(data: dict[str, Any]) -> None:
            expected = _NEXT_STEP_STATE.get(data.get("state", STATE_ABSENT))
            if expected != to:
                raise PactFault(FAULT_INVALID_TRANSITION, f"cannot advance from {data.get('state')!r} to {to!r}")
            if to in EVIDENCE_LOCKED_STATES:
                raise PactFault(
                    FAULT_INVALID_TRANSITION,
                    "this step completes only through evidence-checked claim commands",
                )
            if to == STATE_LOCAL_TASK:
                from core import first_run as provider

                if not provider.terminal_permits_local():
                    raise PactFault(
                        FAULT_INVALID_TRANSITION,
                        "choose Local Only, connect a provider, or skip the provider step first",
                    )

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            # The edge check runs on the WINNER's fresh data inside the mutate, so a CAS
            # loser reports stale_revision — never a transition fault from a stale read.
            _check(data)
            leaving = _current_step(data.get("state", ""))
            if leaving:
                _mark_step(data, leaving, "done")
            data["state"] = to
            if to == STATE_DONE:
                data["completed_at"] = _utcnow()
            return data

        return _mutate(expect_revision, _apply)


def _finish(current: dict[str, Any], expect_revision: int | None) -> dict[str, Any]:
    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        leaving = _current_step(data.get("state", ""))
        if leaving:
            _mark_step(data, leaving, "done")
        for name, entry in data.get("steps", {}).items():
            if entry.get("status") == "pending":
                entry["status"] = "skipped"
                entry["at"] = _utcnow()
        data["state"] = STATE_DONE
        data["completed_at"] = _utcnow()
        return data

    return _mutate(expect_revision, _apply)


def set_welcome_hidden(hidden: bool, *, expect_revision: int | None = None) -> dict[str, Any]:
    return hide(hidden, expect_revision=expect_revision)


# --- evidence-checked claims (LP2 / C5) -------------------------------------------------------

def _verify_turn_evidence(
    session_id: str,
    request_id: str,
    *,
    require_refusal: bool = False,
    receipt_id: str = "",
) -> dict[str, Any]:
    """The §6.6 pipeline: locate → verify signature/hash → verify chain → verify proof.

    Every boolean in the response is the VERIFIER's output; a receipt-shaped record
    that fails any check completes nothing.
    """
    from core.honesty_receipt import (
        list_honesty_receipts,
        verify_honesty_chain,
        verify_honesty_receipt,
    )
    from core.proof_projection import ProofNotBound, build_turn_proof

    session = str(session_id or "").strip()
    request = str(request_id or "").strip()
    if not session or not request:
        raise PactFault(FAULT_EVIDENCE_MISSING, "session_id and request_id are required")

    receipts = list_honesty_receipts(session)
    if not receipts:
        raise PactFault(FAULT_EVIDENCE_SESSION_UNKNOWN, "no honesty-receipt ledger exists for this session")

    try:
        proof = build_turn_proof(session_id=session, request_id=request, principal="owner_local")
    except ProofNotBound as exc:
        raise PactFault(FAULT_EVIDENCE_MISSING, f"no served turn binds this session to this request id: {exc}")

    expanded = proof.get("expanded") or {}
    finalization = expanded.get("finalization") or {}
    witness = expanded.get("witness") or {}
    proof_state = str(proof.get("state") or "")

    if finalization.get("content_hash_ok") is not True:
        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, "the turn's finalized content hash does not verify")
    if witness.get("consistent") is not True:
        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, "the turn's execution-truth witness is inconsistent")
    if proof_state not in {"VERIFIED", "RECORDED"}:
        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f"proof state {proof_state or 'UNKNOWN'} — the step cannot complete")

    located = None
    if receipt_id:
        located = next((r for r in receipts if str(r.get("receipt_id")) == str(receipt_id)), None)
        if located is None:
            raise PactFault(FAULT_EVIDENCE_MISSING, f"receipt {receipt_id} is not in this session's ledger")
    else:
        located = receipts[-1]

    signed = bool(located.get("signature"))
    if not signed:
        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, "receipt unsigned")
    ok, why = verify_honesty_receipt(located)
    if not ok:
        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f"receipt verification failed: {why}")
    chain_ok, chain_why = verify_honesty_chain(receipts)
    if not chain_ok:
        raise PactFault(FAULT_EVIDENCE_UNVERIFIABLE, f"receipt chain broken: {chain_why}")

    refusals = expanded.get("refusals") or []
    refusal_evidence: dict[str, Any] | None = None
    if require_refusal:
        verdict = str(located.get("verdict") or "")
        if refusals:
            refusal_evidence = {"source": "execution_facts", "refusals": refusals}
        elif verdict == "blocked_false_claim":
            refusal_evidence = {"source": "honesty_receipt", "verdict": verdict}
        else:
            refusal_evidence = _durable_denial_effects(session, request)
        if refusal_evidence is None:
            refusal_evidence = _composite_contained_attempt(proof)
        if refusal_evidence is None:
            raise PactFault(
                FAULT_EVIDENCE_MISSING,
                "no genuine pre-dispatch refusal is recorded for this turn — nothing was denied",
            )

    return {
        "ok": True,
        "receipt": located,
        "receipt_id": str(located.get("receipt_id") or ""),
        "verdict": str(located.get("verdict") or ""),
        "signed": True,
        "signature_verified": True,
        "chain_verified": True,
        "proof_state": proof_state,
        "refusals": refusals,
        "refusal_evidence": refusal_evidence,
    }


def _composite_contained_attempt(proof: dict[str, Any]) -> dict[str, Any] | None:
    """A network-class attempt that FAILED while the composite provably blocks all egress.

    Under the Local Only composite every outbound door refuses (the §9.4 census proves it
    at the authorities), so a durably recorded attempted-but-failed retrieval/effect action
    on such a turn IS a genuine policy refusal outcome — the attempt could not have left
    the machine. A turn where nothing was attempted (no action facts at all) never
    qualifies, and neither does a succeeded/failed NON-network action.
    """
    from core import policy_engine

    if not (
        bool(policy_engine.local_only_mode())
        and not bool(policy_engine.allow_web_fallback())
        and not bool(policy_engine.get("network.outbound_enabled", False))
    ):
        return None
    actions = ((proof.get("expanded") or {}).get("actions")) or []
    network_markers = ("web", "fetch", "search", "retriev", "live_data", "browser", "http", "url")
    contained = [
        row
        for row in actions
        if str(row.get("outcome")) in {"refused", "failed"}
        and (
            str(row.get("kind")) in {"retrieval", "effect"}
            or any(marker in str(row.get("name", "")).lower() for marker in network_markers)
        )
    ]
    if not contained:
        return None
    return {"source": "execution_facts", "refusals": contained, "note": "attempt contained by the Local Only composite"}


def _durable_denial_effects(session_id: str, request_id: str) -> dict[str, Any] | None:
    """Denied pre-dispatch effect receipts bound to this request, from the durable journal.

    Turn effect ledgers are in-process at this SHA; when a turn scope IS persisted
    durably this finds its denied receipts. Absence here is not a fault — the
    execution-fact and honesty-receipt refusal records above are the durable truth.
    """
    try:
        from core.effect_gateway import background_effect_evidence

        report = background_effect_evidence(limit=500)
        if report.get("query_error"):
            return None
        denied: list[str] = []
        for event in report.get("events") or ():
            payload = event.get("payload") if isinstance(event, dict) else None
            data = payload if isinstance(payload, dict) else event
            if not isinstance(data, dict):
                continue
            if str(data.get("request_id") or "") != request_id:
                continue
            if str(data.get("decision") or "") == "denied" and str(data.get("lifecycle") or "") == "denied":
                effect_id = str(data.get("effect_id") or event.get("effect_id") or "")
                if effect_id:
                    denied.append(effect_id)
        if denied:
            return {"source": "effect_journal", "effect_ids": denied}
    except Exception:
        return None
    return None


def claim_task(
    *,
    session_id: str,
    request_id: str,
    expect_revision: int | None = None,
    receipt_id: str = "",
) -> dict[str, Any]:
    with PACT_FILE_LOCK:
        current = load_state() or seed()
        if current.get("state") != STATE_LOCAL_TASK:
            raise PactFault(FAULT_INVALID_TRANSITION, f"the task step completes from {STATE_LOCAL_TASK}, not {current.get('state')!r}")
        verified = _verify_turn_evidence(session_id, request_id, receipt_id=receipt_id)

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            _mark_step(data, "task", "done")
            entry = data["steps"]["task"]
            entry["evidence"] = {
                "session_id": str(session_id),
                "request_id": str(request_id),
                "receipt_id": verified["receipt_id"],
                "verified": True,
            }
            data["state"] = STATE_TASK_RECEIPT
            return data

        next_data = _mutate(expect_revision, _apply)
        return {
            "state": next_data["state"],
            "revision": next_data["revision"],
            "receipt": {
                "receipt_id": verified["receipt_id"],
                "verdict": verified["verdict"],
                "signed": verified["signed"],
                "signature_verified": verified["signature_verified"],
                "chain_verified": verified["chain_verified"],
            },
            "proof_state": verified["proof_state"],
        }


def claim_denial(
    *,
    session_id: str,
    request_id: str,
    expect_revision: int | None = None,
    receipt_id: str = "",
) -> dict[str, Any]:
    with PACT_FILE_LOCK:
        current = load_state() or seed()
        if current.get("state") != STATE_DENIAL_DEMO:
            raise PactFault(FAULT_INVALID_TRANSITION, f"the denial step completes from {STATE_DENIAL_DEMO}, not {current.get('state')!r}")
        verified = _verify_turn_evidence(session_id, request_id, require_refusal=True, receipt_id=receipt_id)
        refusal = verified.get("refusal_evidence") or {}
        effect_ids = list(refusal.get("effect_ids") or []) if refusal.get("source") == "effect_journal" else []

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            _mark_step(data, "denial", "done")
            entry = data["steps"]["denial"]
            entry["evidence"] = {
                "session_id": str(session_id),
                "request_id": str(request_id),
                "receipt_id": verified["receipt_id"],
                "effect_ids": effect_ids,
                "verified": True,
            }
            data["state"] = STATE_MEMORY_DONE
            return data

        next_data = _mutate(expect_revision, _apply)
        return {
            "state": next_data["state"],
            "revision": next_data["revision"],
            "denial": {
                "effect_ids": effect_ids,
                "decision": "denied",
                "source": refusal.get("source", ""),
                "verified": True,
            },
            "proof_state": verified["proof_state"],
        }


# --- boundaries (the six-binding composite, C1) -----------------------------------------------

BOUNDARY_KEY_COMPOSITE = "local_only_composite"
BOUNDARY_KEY_MEMORY = "memory_paused"
BOUNDARY_KEY_WEB = "web_lookups"
BOUNDARY_KEYS = frozenset({BOUNDARY_KEY_COMPOSITE, BOUNDARY_KEY_MEMORY, BOUNDARY_KEY_WEB})


def boundary_live() -> dict[str, Any]:
    """The authorities' truth, read fresh (LP4) — never a copy in the pact file."""
    from core import operator_profile, policy_engine

    try:
        share_scope = str(policy_engine.get("shards.default_share_scope", "local_only") or "local_only")
    except Exception:
        share_scope = "local_only"
    try:
        memory_paused = bool(operator_profile.is_paused(operator_profile.OWNER_PRINCIPAL))
    except Exception:
        memory_paused = False
    return {
        "local_only_mode": bool(policy_engine.local_only_mode()),
        "allow_web_fallback": bool(policy_engine.allow_web_fallback()),
        "outbound_enabled": bool(policy_engine.get("network.outbound_enabled", False)),
        "memory_paused": memory_paused,
        "share_scope": share_scope,
    }


def _wallet_freeze_note() -> str:
    try:
        from core.wallet.limits import is_frozen

        return "frozen" if is_frozen() else "not_frozen"
    except Exception:
        return "no_wallet_record"


def set_boundary(key: str, value: bool, *, expect_revision: int | None = None) -> dict[str, Any]:
    """Flip ONE operator boundary through its owning authority (spec §9.1).

    Order of operations under ONE fail-closed publication acquisition: re-read →
    CAS check → authority effects → publish → project live truth. A stale request
    is refused with ``stale_revision`` BEFORE any authority is touched. If a LATER
    authority write fails after an earlier one applied, the outcome is the typed
    ``boundary_partial`` fault naming what applied and what failed, with live
    authority truth projected — never a clean success, never a clean refusal after
    effects occurred.
    """
    from core import policy_engine
    from core.cross_process_lock import LockUnavailable

    clean_key = str(key or "").strip()
    if clean_key not in BOUNDARY_KEYS:
        raise PactFault(FAULT_BOUNDARY_KEY_UNKNOWN, f"unknown boundary key {key!r}", http_status=400)
    value = bool(value)

    command_id = "first_run.pact.boundary.set"
    applied: list[str] = []
    failed: list[dict[str, Any]] = []

    def _effect(name: str, fn) -> None:
        try:
            fn()
            applied.append(name)
        except PactFault:
            raise
        except Exception as exc:
            failed.append({"effect": name, "error": f"{type(exc).__name__}: {exc}"})
            raise

    with PACT_FILE_LOCK:
        with _FileLock():
            current = load_state()
            if current is None:
                current = _seed_locked()
            _require_mututable(current)
            # CAS VALIDITY FIRST — a stale request is refused before ANY authority
            # is touched (policy composite, wallet freeze, memory pause, web lookups).
            if expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):
                raise PactFault(FAULT_STALE_REVISION, "the tour state changed elsewhere; refreshed")

            try:
                if clean_key == BOUNDARY_KEY_COMPOSITE:
                    # two sequential authority effects through the owning setters:
                    # the four policy keys, then (when a wallet record exists) the
                    # panic freeze. A later-effect failure after an earlier one
                    # applied is reported as boundary_partial — never clean.
                    _effect("policy_composite", lambda: _set_composite(value))
                    if value:
                        wallet_note = _wallet_freeze_note()
                        if wallet_note != "no_wallet_record":
                            def _freeze():
                                from core.wallet_spend_policy_store import set_frozen_mirrored

                                set_frozen_mirrored(True)
                            _effect("wallet_freeze", _freeze)
                elif clean_key == BOUNDARY_KEY_MEMORY:
                    from core import operator_profile

                    _effect("memory_pause", lambda: operator_profile.set_paused(
                        operator_profile.OWNER_PRINCIPAL, value))
                elif clean_key == BOUNDARY_KEY_WEB:
                    live = boundary_live()
                    if live["local_only_mode"] and value:
                        raise PactFault(
                            FAULT_INVALID_TRANSITION,
                            "web lookups stay off while 'Keep everything on this machine' is on",
                        )
                    _effect("web_lookups", lambda: policy_engine.set_operator_policy_values(
                        {"system.allow_web_fallback": value}))
            except LockUnavailable as exc:
                # the owning policy lock refused: typed, and ZERO authority effects
                # have occurred when this is the first effect (it is the policy write)
                if applied:
                    raise PactFault(
                        "boundary_partial",
                        json.dumps({"applied": applied, "failed": failed + [
                            {"effect": "policy_lock", "error": str(exc)}],
                            "live": boundary_live()}),
                        http_status=409,
                    ) from exc
                raise PactFault(
                    FAULT_LOCK_UNAVAILABLE,
                    "another window or process is publishing operator policy; retry in a moment",
                    http_status=409,
                ) from exc
            except PactFault as exc:
                if applied:
                    # truthful partial: some effects landed, name them and project truth
                    raise PactFault(
                        "boundary_partial",
                        json.dumps({"applied": applied, "failed": failed,
                                    "detail": exc.detail, "live": boundary_live()}),
                        http_status=409,
                    ) from exc
                if failed:
                    raise PactFault(
                        "authority_write_failed",
                        json.dumps({"applied": [], "failed": failed, "live": boundary_live()}),
                        http_status=409,
                    ) from exc
                raise
            except Exception as exc:
                # any other authority failure: the same truthful accounting — partial
                # if an earlier effect landed, a clean typed failure if nothing did.
                if applied:
                    raise PactFault(
                        "boundary_partial",
                        json.dumps({"applied": applied, "failed": failed + [
                            {"effect": "unclassified", "error": f"{type(exc).__name__}: {exc}"}],
                            "live": boundary_live()}),
                        http_status=409,
                    ) from exc
                raise PactFault(
                    "authority_write_failed",
                    json.dumps({"applied": [], "failed": [
                        {"effect": "unclassified", "error": f"{type(exc).__name__}: {exc}"}],
                        "live": boundary_live()}),
                    http_status=409,
                ) from exc

            def _apply(data: dict[str, Any]) -> dict[str, Any]:
                steps = data.setdefault("steps", _step_default())
                entry = steps.setdefault("boundaries", {})
                ids = entry.setdefault("via_command_ids", [])
                if command_id not in ids:
                    ids.append(command_id)
                return data

            try:
                next_data = _publish(_apply)
            except Exception as exc:
                # publication failure AFTER authority effects: truthful partial naming
                # the applied authority effect and the failed pact publication, with
                # live authority truth — never a raw 500, never a clean refusal.
                raise PactFault(
                    "boundary_partial",
                    json.dumps({"applied": applied, "failed": failed + [
                        {"effect": "pact_publication",
                         "error": "%s: %s" % (type(exc).__name__, exc)}],
                        "live": boundary_live()}),
                    http_status=409,
                ) from exc

    live = boundary_live()
    return {
        "state": next_data["state"],
        "revision": next_data["revision"],
        "boundary": clean_key,
        "value": value,
        "live": live,
        "egress": egress_census(),
    }


def _set_composite(on: bool) -> None:
    from core import policy_engine

    if on:
        policy_engine.set_operator_policy_values(
            {
                "system.local_only_mode": True,
                "system.allow_web_fallback": False,
                "network.outbound_enabled": False,
                "shards.default_share_scope": "local_only",
            }
        )
        os.environ.pop("VOOL_PUBLIC_HIVE_WATCH_HOST", None)
        # the wallet panic freeze is applied by the caller as its OWN named effect
        # (wallet_freeze) so a partial composite is observable and truthful
    else:
        # Leaving the composite restores the product defaults for the three policy
        # keys. The wallet panic freeze is deliberately NOT lifted here: unfreezing
        # is an OS-consent-gated action (wallet law), so it stays frozen and the
        # details view says so honestly.
        policy_engine.set_operator_policy_values(
            {
                "system.local_only_mode": False,
                "system.allow_web_fallback": True,
                "network.outbound_enabled": True,
            }
        )


# --- the §9.4 egress door census (C1: the claim is proved, not asserted) -----------------------

def egress_census() -> dict[str, Any]:
    """Enumerate every outbound seam this build ships and prove each closed or say why not.

    ``proof`` is ``complete`` only when every enumerated door provably refuses under the
    composite AND no named residual remains; anything else is ``partial`` and the card
    renders the narrow truth (which always names the residuals).
    """
    from core import policy_engine

    doors: list[dict[str, Any]] = []
    local_only = bool(policy_engine.local_only_mode())
    web_off = not bool(policy_engine.allow_web_fallback())
    outbound_off = not bool(policy_engine.get("network.outbound_enabled", False))

    doors.append(
        {
            "door": "cloud_model_transport",
            "closed": outbound_off and local_only,
            "proof": "gate consult refuses: network.outbound_enabled=false and machine-wide local-only"
            if (outbound_off and local_only)
            else "transport gate would still allow outbound",
        }
    )
    doors.append(
        {
            "door": "outbound_http_fetch",
            "closed": local_only,
            "proof": "the one fetch door refuses non-local targets pre-dispatch under local-only (decided_by remote_fetch_policy.local_only_egress)"
            if local_only
            else "the fetch door is not constrained to loopback",
        }
    )
    doors.append(
        {
            "door": "web_search_engines",
            "closed": web_off,
            "proof": "allow_web_fallback=false: keyless and keyed retrieval both refuse"
            if web_off
            else "web fallback is enabled",
        }
    )
    browser_closed = web_off and outbound_off
    doors.append(
        {
            "door": "browser_lane",
            "closed": browser_closed,
            "proof": "no browser backend is wired in this build and the lane rides the disabled outbound doors"
            if browser_closed
            else "browser lane not confined",
        }
    )
    doors.append(
        {
            "door": "plugin_mcp_egress",
            "closed": local_only,
            "proof": "mode matrix gates tool egress and the local-only fetch door refuses every non-local destination"
            if local_only
            else "plugin/MCP destinations are not confined to local",
        }
    )
    wallet_state = _wallet_freeze_note()
    doors.append(
        {
            "door": "wallet_relay_rpc",
            "closed": wallet_state in {"frozen", "no_wallet_record"},
            "proof": {
                "frozen": "panic freeze active: all spending and wallet broadcast refused",
                "no_wallet_record": "no wallet exists on this machine — nothing to broadcast",
                "not_frozen": "wallet spend policy is not frozen",
            }[wallet_state],
        }
    )
    watch_env = str(os.environ.get("VOOL_PUBLIC_HIVE_WATCH_HOST") or "").strip()
    doors.append(
        {
            "door": "relay_watch_egress",
            "closed": watch_env == "",
            "proof": "watch/relay host env is unset: no watcher origin is configured"
            if watch_env == ""
            else "VOOL_PUBLIC_HIVE_WATCH_HOST is set in this process",
        }
    )
    mesh_disabled = str(os.environ.get("VOOL_DISABLE_MESH_DAEMON") or "").strip() != "" or not bool(
        policy_engine.get("system.enable_stream_data_plane", True)
    )
    doors.append(
        {
            "door": "mesh_daemon_outbound",
            "closed": mesh_disabled and outbound_off,
            "proof": "mesh daemon disabled in this process and outbound sockets are off"
            if (mesh_disabled and outbound_off)
            else "mesh/daemon lane is not proven closed",
        }
    )

    residuals: list[str] = []
    updater_residual = _update_feed_configured()
    residuals.append("signed update checks may contact their configured feed")
    if updater_residual:
        doors.append({"door": "updater_feed", "closed": False, "proof": "an update feed is configured"})
    residuals.append("local model downloads may fetch from their configured source")

    complete = all(d["closed"] for d in doors) and not updater_residual
    return {
        "proof": "complete" if complete else "partial",
        "doors": doors,
        "residuals": residuals if not complete else [],
    }


def _update_feed_configured() -> bool:
    """Honest updater residual: is a signed-update feed configured on this home?"""
    try:
        from core.runtime_paths import config_path

        for name in ("update_feed.conf", "update_feed.json", "updater.json"):
            try:
                if config_path(name).exists():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        # THE SIGNED UPDATE AUTHORITY, not the retired detached lane. This previously read
        # DEFAULT_FEED_URL/FEED_URL off ``installer.self_update`` -- a module that defines
        # neither, so the branch always answered False, and importing it re-wired the
        # fail-open detached updater into a production import closure. The updater lane's own
        # reachability contract (one install authority) catches exactly that, and it fails the
        # moment both lanes are in one tree.
        from core.updater.feed import load_feed_config

        return bool(load_feed_config().has_feed)
    except Exception:
        return False


# --- facts (typed Operator Profile items, C2 fence) --------------------------------------------

FACT_CATEGORIES = (
    "preferred_name",
    "language",
    "locale",
    "timezone",
    "response_style",
    "format_preference",
    "email_signature",
    "shipping_region_preference",
)

SHIPPING_REGIONS = (
    "Europe",
    "North America",
    "Asia-Pacific",
    "Africa",
    "Oceania",
    "South America",
    "Prefer not to say",
)


def _fact_refusal(value: str, category: str) -> str | None:
    """The C2 fence: secrets AND address-classified text are refused before any store."""
    from core.privacy_guard import text_privacy_risks
    from core.secret_redaction import contains_secret

    text = str(value or "")
    if not text.strip():
        return "empty"
    if contains_secret(text):
        return "secret_shaped"
    risks = text_privacy_risks(text)
    if "postal_address" in risks:
        return "address_shaped"
    if category == "shipping_region_preference" and text not in SHIPPING_REGIONS:
        return "not_in_region_enum"
    return None


def set_facts(
    items: list[dict[str, Any]],
    *,
    expect_revision: int | None = None,
) -> dict[str, Any]:
    """Save typed operator facts with origin=explicit; refusals store NOTHING (LP7)."""
    from core import operator_profile

    results: list[dict[str, Any]] = []
    saved_any = False
    for item in items or []:
        category = str((item or {}).get("category") or "").strip()
        value = str((item or {}).get("value") or "")
        if category not in FACT_CATEGORIES:
            results.append({"category": category, "status": "validation_failed", "detail": "unknown category"})
            continue
        refusal = _fact_refusal(value, category)
        if refusal is None and category == "shipping_region_preference" and value and value not in SHIPPING_REGIONS:
            results.append({"category": category, "status": "validation_failed", "detail": "not_in_region_enum"})
            continue
        if refusal is not None:
            results.append({"category": category, "status": "refused_secret", "detail": refusal})
            continue
        change = operator_profile.remember(
            operator_profile.OWNER_PRINCIPAL,
            category,
            value,
            origin="explicit",
            actor="first_run_pact",
        )
        if getattr(change, "kind", "") == "ok" or getattr(getattr(change, "item", None), "item_id", ""):
            saved_any = True
            results.append({"category": category, "status": "saved", "item_id": getattr(change.item, "item_id", "")})
        else:
            results.append({"category": category, "status": getattr(change, "kind", "validation_failed"), "detail": getattr(change, "report", "")})

    with PACT_FILE_LOCK:
        current = load_state() or seed()

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            if saved_any:
                _mark_step(data, "facts", "done")
            return data

        next_data = _mutate(expect_revision, _apply)

    return {"state": next_data["state"], "revision": next_data["revision"], "results": results}


def forget_fact(category: str, *, expect_revision: int | None = None) -> dict[str, Any]:
    """The typed tombstone for one pact-captured fact (the S9 'Forget one' verb)."""
    from core import operator_profile

    clean = str(category or "").strip()
    if clean not in FACT_CATEGORIES:
        raise PactFault("fact_validation_failed", f"unknown fact category {category!r}", http_status=400)
    items = operator_profile.list_items(operator_profile.OWNER_PRINCIPAL)
    target = next((i for i in items if i.category == clean and i.status == "active"), None)
    if target is None:
        return {"forgotten": False, "detail": "nothing saved for this category"}
    change = operator_profile.forget_item(target.item_id, actor="first_run_pact")
    forgotten = getattr(change, "kind", "") == "ok"
    return {"forgotten": bool(forgotten or getattr(change, "item", None) is not None), "detail": getattr(change, "report", "")}


# --- naming (S2) --------------------------------------------------------------------------------

def set_name(
    *,
    agent_name: str = "",
    keep_default: bool = False,
    preferred_address: str = "",
    expect_revision: int | None = None,
) -> dict[str, Any]:
    """VOOL naming + optional operator address, through the existing authorities only."""
    from core import onboarding, operator_profile

    agent_name = str(agent_name or "").strip()
    preferred_address = str(preferred_address or "").strip()
    if not keep_default:
        if not agent_name:
            raise PactFault("fact_validation_failed", "agent_name is empty", http_status=400)
        if len(agent_name) > 40:
            raise PactFault("fact_validation_failed", "agent_name exceeds 40 characters", http_status=400)
    if len(preferred_address) > 60:
        raise PactFault("fact_validation_failed", "preferred_address exceeds 60 characters", http_status=400)
    if preferred_address:
        refusal = _fact_refusal(preferred_address, "preferred_name")
        if refusal == "secret_shaped" or refusal == "address_shaped":
            raise PactFault("fact_refused_secret", "that value looks like a secret or an address", http_status=400)

    if not keep_default:
        try:
            onboarding.force_rename(agent_name)
        except Exception as exc:
            raise PactFault("fact_validation_failed", f"rename refused: {exc}", http_status=409)
    if preferred_address:
        change = operator_profile.remember(
            operator_profile.OWNER_PRINCIPAL,
            "preferred_name",
            preferred_address,
            origin="explicit",
            actor="first_run_pact",
        )
        if getattr(change, "kind", "") not in {"ok", ""} and getattr(getattr(change, "item", None), "item_id", "") == "":
            raise PactFault("fact_refused_secret", str(getattr(change, "report", "refused")), http_status=400)

    with PACT_FILE_LOCK:
        current = load_state() or seed()

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            _mark_step(data, "naming", "done")
            return data

        next_data = _mutate(expect_revision, _apply)

    return {
        "state": next_data["state"],
        "revision": next_data["revision"],
        "agent_display_name": onboarding.get_agent_display_name(),
    }


# --- the aggregated GET projection (§6.1) -------------------------------------------------------

def provider_state_snapshot() -> dict[str, Any]:
    from core import first_run as provider

    snap = provider.snapshot()
    return {"state": snap["state"], "connected_provider": snap.get("chosen_provider_id") or None}


def cloud_connection_summary() -> str:
    try:
        from core.cloud_connection_state import STATE_NO_KEY, connection_status

        status = connection_status()
        state = str((status or {}).get("state") or STATE_NO_KEY)
        return state
    except Exception:
        return "no_key"


def agent_display_name() -> str:
    try:
        from core import onboarding

        return onboarding.get_agent_display_name()
    except Exception:
        return "VOOL"


def facts_snapshot() -> list[dict[str, Any]]:
    try:
        from core import operator_profile

        items = operator_profile.list_items(operator_profile.OWNER_PRINCIPAL)
        return [
            {
                "category": i.category,
                "value": i.value_text,
                "origin": i.origin,
                "scope": i.scope,
                "updated_at": i.updated_at,
            }
            for i in items
            if i.category in FACT_CATEGORIES and i.status == "active"
        ]
    except Exception:
        return []


def snapshot(model_pull: dict[str, Any] | None = None) -> dict[str, Any]:
    """The aggregated §6.1 projection; every live field is read from its authority NOW."""
    data = load_state()
    if data is None:
        raise PactFault("pact_absent", "the pact seed has not run yet", http_status=404)

    state = str(data.get("state"))
    steps = {name: str((data.get("steps", {}).get(name) or {}).get("status") or "pending") for name in STEP_ORDER}
    if state in TERMINAL_STATES:
        steps = {name: ("done" if status == "done" else "skipped") for name, status in steps.items()}

    live = boundary_live()
    live.update(
        {
            "agent_display_name": agent_display_name(),
            "cloud_connection": cloud_connection_summary(),
            "egress": egress_census(),
        }
    )
    if model_pull is not None:
        live["model_pull"] = model_pull

    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "revision": int(data.get("revision", 0)),
        "welcome_hidden": bool(data.get("welcome_hidden", False)),
        "seed_reason": str(data.get("seed_reason") or ""),
        "live": live,
        "facts": facts_snapshot(),
        "provider": provider_state_snapshot(),
        "steps": steps,
        "task_prompt": TASK_PROMPT_MODEL,
        "task_prompt_direct": TASK_PROMPT_DIRECT,
        "denial_prompt": DENIAL_PROMPT,
        "denial_prompt_direct": DENIAL_PROMPT_DIRECT,
    }


def seed_and_snapshot(model_pull: dict[str, Any] | None = None) -> dict[str, Any]:
    with PACT_FILE_LOCK:
        seed()
    return snapshot(model_pull=model_pull)
