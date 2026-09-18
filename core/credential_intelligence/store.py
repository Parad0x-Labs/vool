"""The credential-intelligence store: verified persistence, opaque bindings, explicit
lifecycle, and honest reconciliation.

Built ON ``core.credential_store`` (the existing Keychain/vault authority — bounded calls,
operator write grant, process-wide breaker all stay theirs). This layer adds the
credential-intelligence laws:

* **Verified-only persistence.** ``save_verified`` refuses any outcome that is not
  ``verified`` — an invalid/exhausted/unauthorized/rate-limited/network-unavailable key
  persists NOTHING.
* **One transaction authority.** Every mutating operation on the binding index — verified
  save/replace, quarantine save/promote/delete, delete/revoke, reconcile — holds the ONE
  cross-process state lock (``_state_lock``) across its ENTIRE sequence: journal append,
  backend writes, index write, journal close. Atomic ``os.replace`` remains the crash
  atomicity mechanism per file; the lock is the concurrency mechanism. What the storage
  contract HONESTLY guarantees: while the lock is held no other participant mutates index or
  journal, and each operation re-reads state under the lock before deciding. What it cannot
  guarantee: the credential BACKEND (Keychain/vault) offers no compare-and-swap, so a bounded
  write that times out may still complete LATE, after our lock is released. That residual is
  owned by recovery, not by another pre-write check: the pending intent carries a unique
  ``operation_id`` plus the content digest, and reconcile adopts a late landing value only
  when the NEWEST pending intent for that slot still names that digest — otherwise it is a
  typed conflict for the operator. Quarantine rows additionally carry their operation's
  identity: the unique ``quarantine_operation_id`` minted with the operation's journal intent,
  plus a monotonic ``quarantine_epoch`` that orders operations. A delete/recreate of the SAME
  secret+endpoint (an ABA a content hash cannot distinguish) is a different operation, and stale
  snapshots/intents compare the operation id — which, unlike the epoch counter, cannot be
  re-issued when the counter's authority is lost or reset after a deletion.
* **Intent journal.** Every write/delete is journaled BEFORE it touches the Keychain. A
  bounded Keychain write that times out (a pending authorization dialog) leaves a pending
  intent, never a binding — and the daemon thread completing the write LATE cannot create a
  SHADOW (a keychain value no index row accounts for) or a DUPLICATE (the same value live in
  two backends): ``reconcile`` — after restart or on demand — adopts the value as exactly one
  ``unverified`` row (the read-back proof never happened, so it is honestly unverified),
  drops the vault copy when both backends hold it, and reports a typed CONFLICT rather than
  guessing when the value is not ours.
* **Explicit lifecycle.** ``replace`` refuses to create (it replaces an existing binding or
  raises); ``delete`` proves removal or keeps the binding and says so; ``revoke`` removes the
  secret and keeps an honest tombstone; nothing is silently inferred.
* **Honest storage.** A corrupt index raises ``MalformedBindingIndexError``; structurally bad rows
  are reported by ``reconcile``, never silently kept or dropped; a restart reloads bindings
  with statuses intact.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.credential_intelligence import binding as binding_module
from core.credential_intelligence.binding import (
    CredentialBinding,
    binding_from_row,
    binding_id_for,
    load_index,
    save_index,
)
from core.credential_intelligence.provider_registry import ProviderDescriptor, ProviderRegistry

JOURNAL_FILE = "credential_intake_journal.json"

PHASE_WRITE_PENDING = "write_pending"
PHASE_DELETE_PENDING = "delete_pending"
PHASE_DONE = "done"
PHASE_CONFLICT = "conflict"

#: Quarantine slots carry this prefix. No execution consumer resolves a slot by any other name
#: than ``descriptor.credential_slot`` (and the search table's own slots), so a value stored
#: here is structurally unreachable by routing, probing, search or completion — the isolation
#: is the slot name, not a flag somebody could forget to check.
QUARANTINE_PREFIX = "quarantine."


def _endpoint_slot_for(descriptor: ProviderDescriptor) -> str:
    """The slot a provider's endpoint half commits into when its key and endpoint are ONE pair. A
    descriptor names its own (``endpoint_slot``: the custom base URL, UsePod's origin); a user endpoint
    that names none pairs with the custom base-URL slot; any other key has a fixed destination ("")."""
    named = str(getattr(descriptor, "endpoint_slot", "") or "")
    if named:
        return named
    if getattr(descriptor, "user_endpoint", False):
        from core.cloud_providers import CUSTOM_BASE_URL_SLOT

        return CUSTOM_BASE_URL_SLOT
    return ""


def _endpoint_slot_label(slot: str, provider_label: str) -> str:
    """The backend label for an endpoint half (metadata, never a secret)."""
    from core.cloud_providers import CUSTOM_BASE_URL_SLOT

    return "Custom base URL" if slot == CUSTOM_BASE_URL_SLOT else f"{provider_label} endpoint"


def quarantine_slot_for(descriptor: ProviderDescriptor) -> str:
    return f"{QUARANTINE_PREFIX}{descriptor.credential_slot}"


@dataclass(frozen=True)
class QuarantineSnapshot:
    """ONE immutable, atomically-read view of a quarantined key: the secret together with the
    provider, the endpoint it was saved for, and the operation identity (unique operation id +
    content generation + monotonic epoch). Retry verifies EXACTLY this snapshot's destination, and promotion
    compares THIS snapshot against current state at the commit boundary — so the secret can
    never be combined with a later paste's endpoint, and a stale result can never overwrite a
    newer paste, deletion or verified replacement."""
    provider_id: str
    secret: str
    endpoint: str
    generation: str
    epoch: int
    key_digest: str
    #: The operation's unique identity (revision-5 review R1). Empty only for a row written before
    #: operation ids existed; such a legacy handle matches its own legacy row and nothing newer.
    operation_id: str = ""


_EPOCH_FILE = "quarantine_epochs.json"


def _epoch_rows_locked() -> dict[str, int]:
    """The highest epoch each provider's EXISTING quarantined row names (0 when none). Used to
    tell a legitimately absent authority (fresh install) from a damaged one (rows were issued
    epochs the file no longer remembers). Callers must hold the transaction."""
    rows: dict[str, int] = {}
    try:
        index = load_index()
    except Exception:
        return rows  # a corrupt index is refused by its own law (MalformedBindingIndexError); it must not silently mint epoch 1 either, and the file checks below still guard allocation
    for pid, row in index.items():
        if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
            continue
        try:
            epoch = int(row.get("quarantine_epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0
        if epoch > 0:
            rows[str(pid)] = max(rows.get(str(pid), 0), epoch)
    return rows


def _next_quarantine_epoch_locked(provider_id: str) -> int:
    """Allocate the NEXT operation epoch for a provider from a durable high-water mark kept in
    its own file, OUTSIDE the deletable binding row. The epoch ORDERS operations; it is not their
    identity. A counter is only as durable as its authority: once a completed delete has removed
    the last row, a missing or reset mark is indistinguishable from a first initialization and the
    counter restarts (revision-5 review R1: a stale snapshot then matched the same-value re-paste
    and deleted it). Identity is therefore the unique ``quarantine_operation_id`` minted with each
    operation, which no loss of this file can re-issue.

    DAMAGED authority never becomes a fresh counter (review F2): a file that exists but cannot
    be parsed, is not a mapping of non-negative integers, or is BEHIND epochs its own rows
    already carry, is established state that lost bytes — allocating from a reset would recycle
    deleted identities, so ``EpochAuthorityError`` refuses instead. A legitimately ABSENT file
    with no epoch-bearing rows is a true first initialization and starts at 1. Callers must
    hold the transaction (the file shares the binding-index lock domain)."""
    from core.runtime_paths import active_data_dir

    path = active_data_dir() / _EPOCH_FILE
    rows_high = _epoch_rows_locked()
    if path.exists():
        try:
            marks = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EpochAuthorityError(
                f"the quarantine epoch authority {_EPOCH_FILE} is unreadable ({exc}); refusing to "
                "allocate a new operation identity — restore the file, or remove it only after "
                "confirming no live quarantine operation, then retry"
            ) from exc
        if not isinstance(marks, dict):
            raise EpochAuthorityError(
                f"the quarantine epoch authority {_EPOCH_FILE} is not a mapping; refusing to "
                "allocate a new operation identity — restore the file, or remove it only after "
                "confirming no live quarantine operation, then retry"
            )
        for key, value in marks.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EpochAuthorityError(
                    f"the quarantine epoch authority {_EPOCH_FILE} carries a malformed mark for "
                    f"{key!r}; refusing to allocate a new operation identity — restore the file, or "
                    "remove it only after confirming no live quarantine operation, then retry"
                )
        for pid, row_epoch in rows_high.items():
            if int(marks.get(pid) or 0) < row_epoch:
                raise EpochAuthorityError(
                    f"the quarantine epoch authority {_EPOCH_FILE} is behind the epochs its own rows "
                    f"already carry ({pid}: file says {int(marks.get(pid) or 0)}, a live row says "
                    f"{row_epoch}); refusing to allocate an identity that may recycle theirs"
                )
    else:
        if rows_high:
            raise EpochAuthorityError(
                f"quarantine rows carry epochs but {_EPOCH_FILE} is absent — the allocation "
                "authority was deleted out from under established state; refusing to allocate a new "
                "operation identity that may recycle theirs"
            )
        marks = {}
    epoch = int(marks.get(provider_id) or 0) + 1
    marks[provider_id] = epoch
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(marks, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())  # operation identity must survive a crash that already renamed it
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
    return epoch


def quarantine_generation_for(secret: str, endpoint: str) -> str:
    """The durable binding between a quarantined secret and the destination it was saved
    with: one way, no key material recoverable, survives restart in the row."""
    return hashlib.sha256(f"quarantine:v1:{secret}\0{str(endpoint or '').strip().rstrip('/')}".encode()).hexdigest()


try:  # the typed failure the one bounded secure-storage door raises
    from core.unattended_preflight import SecureStorageError as _SecureStorageError
except Exception:  # pragma: no cover - keeps this module importable standalone
    class _SecureStorageError(RuntimeError):  # type: ignore[no-redef]
        pass


class IntakeRefusedError(RuntimeError):
    """A typed refusal at the persistence boundary (non-verified outcome, bad replace, or a
    forged/mismatched outcome). Nothing was stored."""

    def __init__(self, message: str, *, status: str = "refused"):
        super().__init__(message)
        self.status = status


class StoreWriteTimeoutError(RuntimeError):
    """The bounded Keychain write did not complete. A pending intent is journaled; nothing is
    claimed stored. Reconciliation after the prompt resolves (or a restart) adopts or
    conflicts — never a shadow, never a duplicate."""


class StorageUnavailableError(RuntimeError):
    """The store backend could not honor the operation (locked Keychain, breaker armed).
    State is retained and reported, never silently dropped."""


class StorageConflictError(RuntimeError):
    """Two sources of truth disagree (read-back mismatch, foreign value in a pending slot).
    Surfaced for the operator; the store never silently picks a winner."""


class StorageReadError(StorageUnavailableError):
    """A backend read needed for a transaction DECISION failed. The transaction is left
    UNRESOLVED with its pending intent intact (recovery owns it); absence is never guessed
    from an unreadable state."""


class EpochAuthorityError(StorageUnavailableError):  # type: ignore[misc,valid-type]
    """The durable quarantine-epoch authority exists but cannot be trusted (unreadable, wrong
    schema, or behind the rows it issued). Allocation REFUSES rather than guessing: a fresh
    counter over damaged state reuses a deleted operation's identity, and a stale snapshot or
    journal intent could then legitimately delete or promote a NEW paste (review F2). Recovery
    is the operator's: inspect ``data/quarantine_epochs.json``, restore it or remove it after
    confirming no live quarantine operation, then retry."""


@dataclass(frozen=True)
class DeleteResult:
    removed: bool
    note: str = ""


@dataclass
class ReconcileReport:
    adopted: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    malformed_rows: list[str] = field(default_factory=list)
    deduped: list[str] = field(default_factory=list)
    deletions_completed: list[str] = field(default_factory=list)
    #: Providers whose pending operation or row could not be decided because a read failed (an
    #: unreadable or unknown backend state). Nothing was closed, adopted or degraded for them; the
    #: next reconcile over readable storage decides (revision-5 review R4).
    unresolved: list[str] = field(default_factory=list)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CredentialStore:
    """One instance per runtime surface. All state lives in files under the active data dir
    (binding index + intent journal) and in the credential backend itself."""

    #: re-exported so tests and reconcile share ONE service-name truth with the backend
    _KC_SERVICE = None  # bound in __init__ from the credential module

    def __init__(self, registry: ProviderRegistry, *, credential_module=None):
        self._registry = registry
        self._cm = credential_module
        if self._cm is None:
            from core import credential_store as cm

            self._cm = cm
        self._KC_SERVICE = getattr(self._cm, "_KC_SERVICE", "nulla-credentials")

    # ------------------------------------------------------------------ paths + journal

    def _index_path(self) -> Path:
        return binding_module.index_path()

    def _journal_path(self) -> Path:
        from core.runtime_paths import active_data_dir

        return active_data_dir() / JOURNAL_FILE

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

    def _journal_rows(self) -> list[dict]:
        path = self._journal_path()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise binding_module.MalformedBindingIndexError("credential intent journal is unreadable")
        if not isinstance(raw, list):
            raise binding_module.MalformedBindingIndexError("credential intent journal is not a list")
        return [row for row in raw if isinstance(row, dict)]

    def _journal_append(self, row: dict) -> None:
        rows = [r for r in self._journal_rows() if r.get("phase") not in (PHASE_DONE,)]
        rows.append(row)
        self._journal_write(rows)

    def _journal_write(self, rows: list[dict]) -> None:
        path = self._journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(rows, sort_keys=True))
                handle.flush()
                os.fsync(handle.fileno())  # recovery evidence: a decided state must not lose its journal to a crash
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)

    def _journal_close(self, slot: str, phase: str) -> None:
        """Mark every non-terminal row for `slot` with `phase` (done/conflict)."""
        rows = self._journal_rows()
        for row in rows:
            if row.get("slot") == slot and row.get("phase") in (PHASE_WRITE_PENDING, PHASE_DELETE_PENDING):
                row["phase"] = phase
        self._journal_write([r for r in rows if r.get("phase") != PHASE_DONE])

    def _pending_intents(self) -> dict[str, dict]:
        """Latest non-terminal journal row per slot."""
        latest: dict[str, dict] = {}
        for row in self._journal_rows():
            if row.get("phase") in (PHASE_WRITE_PENDING, PHASE_DELETE_PENDING):
                latest[str(row.get("slot") or "")] = row
        return latest

    # ------------------------------------------------------------------ reads

    def bindings(self) -> list[CredentialBinding]:
        rows = load_index()  # raises MalformedBindingIndexError on a corrupt file, by law
        found = [b for b in (binding_from_row(row) for row in rows.values()) if b is not None]
        return sorted(found, key=lambda b: b.provider_id)

    def find(self, provider_id: str) -> CredentialBinding | None:
        row = load_index().get(str(provider_id or "").strip().lower())
        return binding_from_row(row) if row else None

    # ------------------------------------------------------------------ verified persistence

    # ------------------------------------------------------------------ the one transaction

    @contextlib.contextmanager
    def _transaction(self):
        """Hold the ONE cross-process authority lock over a whole mutating sequence: journal,
        backend and index together. Internal ``*_locked`` bodies assume it is held; the public
        entry points below are the only lock takers, so a nested call path can never re-enter
        (the flock is not same-thread re-entrant) — verify-before-commit flows snapshot under
        the lock, release it for network work, then re-take it at commit."""
        from core.credential_intelligence._state_lock import state_lock

        with state_lock(binding_module.index_path()):
            yield

    def save_verified(self, descriptor: ProviderDescriptor, secret: str, outcome, *,
                      endpoint: str = "") -> CredentialBinding:
        """Persist a VERIFIED key for `descriptor`'s slot and return its opaque binding.
        This is also the upsert behind ``replace``: one provider slot, one binding row.

        For a user endpoint (the custom OpenAI-compatible provider) ``endpoint`` is the base
        URL this verification belongs to: it is committed INSIDE the same transaction as the
        key and the index row, so the live pair can never be key A with endpoint B (review
        R3: the intake door used to write the endpoint before this lock, and two interleaved
        verified completions both reported success over a mixed pair)."""
        with self._transaction():
            return self._save_verified_locked(descriptor, secret, outcome, endpoint=endpoint)

    def _save_verified_locked(self, descriptor: ProviderDescriptor, secret: str, outcome, *,
                              endpoint: str = "") -> CredentialBinding:
        if getattr(outcome, "status", "") != "verified":
            raise IntakeRefusedError(
                f"refusing to persist a key whose verification is {getattr(outcome, 'status', 'unknown')!r}",
                status=str(getattr(outcome, "status", "")),
            )
        return self._save_pair_locked(descriptor, str(secret), outcome, endpoint=endpoint,
                                      binding_status=binding_module.STATUS_VERIFIED)

    def save_operator_pair(self, descriptor: ProviderDescriptor, secret: str, *,
                           endpoint: str) -> CredentialBinding:
        """An EXPLICIT operator save of a live custom pair WITHOUT a provider-verified outcome
        (the legacy Settings credentials door). It commits through the SAME transaction as a
        verified save — one journal intent, endpoint and key together, decided read-back, a
        coherent binding row — so a crash, a lost reply or a late completion this door causes
        can never leave a mixed pair. The row is honest about what happened: ``unverified``,
        operator-asserted, never provider-accepted."""
        with self._transaction():
            return self._save_pair_locked(descriptor, str(secret), None, endpoint=endpoint,
                                          binding_status=binding_module.STATUS_UNVERIFIED)

    def _save_pair_locked(self, descriptor: ProviderDescriptor, secret: str, outcome, *,
                          endpoint: str, binding_status: str) -> CredentialBinding:
        import uuid

        slot = descriptor.credential_slot
        digest = self._digest(str(secret))
        # The slot the endpoint half commits into. A user endpoint pairs with the custom base-URL
        # slot unless its descriptor names its own; a provider whose key and documented endpoint
        # are one pair (UsePod's origin) names its slot and the endpoint used when none is given.
        endpoint_slot = _endpoint_slot_for(descriptor)
        requested = str(endpoint or "").strip().rstrip("/")
        destination = requested or (str(descriptor.default_endpoint or "").strip().rstrip("/") if endpoint_slot else "")
        paired = bool(endpoint_slot and destination)
        if descriptor.user_endpoint and not requested:
            # A user-endpoint provider's key is only ever committed WITH the endpoint it was
            # verified against; a key-only write would leave the live pair decided by whatever
            # endpoint happens to be stored (review F1's mixed pair, from the other side).
            raise IntakeRefusedError(
                f"a save for {descriptor.provider_id} must name the endpoint this key belongs to; "
                "refusing to commit a key with no destination",
                status="missing_endpoint",
            )
        if endpoint_slot and not destination:
            raise IntakeRefusedError(
                f"a save for {descriptor.provider_id} must name the endpoint this key belongs to; "
                "refusing to commit a key with no destination",
                status="missing_endpoint",
            )
        # The durable binding/operation record (review F1). The intent is appended BEFORE any
        # backend write and now carries everything recovery needs to COMPLETE or UNDO the
        # transaction after a lost reply, a late completion or a restart: the destination, the
        # prior committed pair, and the verified outcome's own facts. It stays pending until
        # the transaction's state is DECIDED — never closed on ambiguity.
        intent = {
            "slot": slot, "provider_id": descriptor.provider_id, "digest": digest,
            "phase": PHASE_WRITE_PENDING, "kind": "save", "ts": _utcnow(),
            "operation_id": uuid.uuid4().hex,
            "binding_status": binding_status,
            "account": str(getattr(outcome, "account", "") or "") if outcome is not None else "",
            "checked_at": str(getattr(outcome, "checked_at", "") or "") if outcome is not None else "",
            "label": descriptor.label,
        }
        if paired:
            prior_base = self._strict_get(endpoint_slot)
            prior_key = self._strict_get(slot)
            intent["endpoint"] = destination
            intent["base_slot"] = endpoint_slot
            intent["prior_digest"] = self._digest(prior_key) if prior_key is not None else ""
            intent["prior_endpoint"] = (prior_base or "").strip().rstrip("/")
        self._journal_append(intent)
        # SecureStorageError as well as TimeoutError: `store_credential` was moved
        # onto the one bounded door in f4eca962, which converts a timeout into
        # SecureStorageError (a RuntimeError). This handler still named only
        # TimeoutError, so the conversion sailed past it, StoreWriteTimeoutError
        # became unreachable and the pending-intent journal path never ran -- two
        # tests have been red since. `delete_credential` now goes through the same
        # bounded door, so its sibling handler below needs the same pair.
        failure: BaseException | None = None
        try:
            if paired:
                self._cm.store_credential(endpoint_slot, destination, label=_endpoint_slot_label(endpoint_slot, descriptor.label))
                self._cm.store_credential(slot, str(secret), label=descriptor.label)
            else:
                self._cm.store_credential(slot, str(secret), label=descriptor.label)
        except BaseException as exc:  # the decision procedure below owns surfacing this
            failure = exc
        if paired:
            # The endpoint+key transaction DECIDES its own state by strict read-back inside the
            # still-held lock — on the failed path AND the clean one (review F1: a write that
            # lands and then raises left endpoint restored but the replacement key live, and
            # the failure path then reported "the prior pair was restored"). The states are
            # explicit: known-committed, rolled back, unresolved, or conflicting. The pending
            # intent is recovery ownership; it is never closed while the state is ambiguous.
            return self._resolve_pair_transaction_locked(
                descriptor, slot, str(secret), destination, intent, failure,
            )
        if failure is not None:
            if isinstance(failure, (TimeoutError, _SecureStorageError)):
                raise StoreWriteTimeoutError(
                    f"keychain write for {descriptor.provider_id} did not complete; a pending intent "
                    "is journaled and reconcile will resolve it after restart"
                ) from failure
            raise failure
        readback = self._strict_get(slot)  # unreadable -> StorageReadError; the intent stays pending
        if readback != str(secret):
            # A read-back that disagrees with a clean write return is a live disagreement the
            # operator must see; the intent stays PENDING so reconcile keeps reporting it
            # (closing it as conflict here would orphan the recovery evidence).
            raise StorageConflictError(f"read-back mismatch for {descriptor.provider_id}; nothing indexed")
        return self._commit_binding_row_locked(descriptor, slot, str(secret), "", intent)

    def _resolve_pair_transaction_locked(self, descriptor: ProviderDescriptor, slot: str,
                                         secret: str, destination: str, intent: dict,
                                         failure: BaseException | None) -> CredentialBinding:
        """Decide a user-endpoint pair transaction by strict read-back under the still-held
        transaction, then report a state the observed bytes actually support.

        The laws (review F1):

        * both writes read back as written → KNOWN COMMITTED. On the clean path the binding
          row commits and the caller gets its binding. On a lost reply the same read-back is
          the proof the pair landed — but a caller is never told "stored" on a missing
          receipt: the pending intent (which carries the endpoint and the verified outcome)
          stays for reconcile to complete, and the pair read gate stays closed until it does.
        * the key did not land → the destination half is ROLLED BACK to the prior endpoint
          inside this transaction, and the restoration is VERIFIED by read-back; a failed or
          unverifiable restoration is reported as unresolved, never as "restored".
        * whatever else is observed (a foreign value, an unreadable backend) is UNRESOLVED or
          CONFLICTING: the pending intent keeps recovery ownership and the surfaced error
          names exactly what was observed.

        The bounded backend may still complete a lost write LATE, after this transaction ends.
        No state this method can leave behind makes that disclosure-eligible: until the intent
        is decided, ``resolved_custom_pair`` refuses on the pending intent; after reconcile
        decides it, the row's recorded digest and endpoint are compared against the live bytes
        on every read, so a late landing surfaces as a typed refusal instead of a request."""
        base_slot = str(intent.get("base_slot") or "") or _endpoint_slot_for(descriptor)
        prior_endpoint = str(intent.get("prior_endpoint") or "")
        prior_digest = str(intent.get("prior_digest") or "")
        try:
            base_now = self._strict_get(base_slot)
            key_now = self._strict_get(slot)
        except StorageReadError as exc:
            raise StorageConflictError(
                f"the verified write for {descriptor.provider_id} did not complete and its stored "
                "state could not be re-read; nothing was restored and nothing was sent anywhere — "
                "the pending intent is journaled and reconcile (restart or on demand) decides it"
            ) from exc
        base_landed = (base_now or "").strip().rstrip("/") == destination
        key_landed = key_now == secret
        key_is_prior = key_now is not None and bool(prior_digest) and self._digest(key_now) == prior_digest
        key_absent_as_prior = key_now is None and not prior_digest

        if key_landed and base_landed:
            if failure is None:
                return self._commit_binding_row_locked(descriptor, slot, secret, destination, intent)
            raise StoreWriteTimeoutError(
                f"the verified {descriptor.provider_id} write landed but its storage reply was "
                f"lost; the committed pair for {destination} is journaled as a pending intent and "
                "reconcile (restart or on demand) completes the binding — nothing was sent anywhere"
            ) from failure
        if failure is None:
            # Clean write returns that do not read back as written: a live disagreement.
            raise StorageConflictError(
                f"read-back mismatch for {descriptor.provider_id}: the committed pair at "
                f"{destination} did not read back; nothing indexed, the pending intent keeps "
                "recovery ownership"
            )

        # A write failed. Undo the destination half if it landed without its key, so the live
        # pair can never be (new endpoint, prior key) — and VERIFY the restoration by
        # read-back instead of assuming it (review F1: a swallowed restoration failure was
        # reported as "the prior pair was restored").
        restored = True
        restore_note = "the prior pair was restored and verified by read-back"
        if base_landed and not key_landed:
            restored = self._restore_endpoint_locked(base_slot, prior_endpoint)
            if not restored:
                restore_note = (
                    "restoration of the prior endpoint FAILED or could not be verified; the live "
                    "state was left untouched"
                )
        if key_is_prior or key_absent_as_prior:
            # The observable key is the PRIOR one (or none, when there was none): this
            # transaction's key half did not land. Its fate may still be pending (the bounded
            # door can complete it late), so the intent STAYS PENDING — the pair read gate
            # refuses until reconcile decides, and reconcile finishes the rollback (including
            # a restoration that failed here) or completes the pair if the key lands late.
            if isinstance(failure, (TimeoutError, _SecureStorageError)):
                raise StoreWriteTimeoutError(
                    f"the verified write for {descriptor.provider_id} did not complete; "
                    f"{restore_note}; a pending intent is journaled and reconcile (restart or on "
                    "demand) decides whether the replacement completes late"
                ) from failure
            raise StorageConflictError(
                f"the verified write for {descriptor.provider_id} failed ({type(failure).__name__}); "
                f"{restore_note}; a pending intent is journaled and reconcile decides it"
            ) from failure
        # A FOREIGN value sits in a slot this transaction names: not ours to overwrite and not
        # ours to report as restored. Recovery ownership stays with the pending intent; every
        # reconcile re-surfaces this until the operator resolves it.
        raise StorageConflictError(
            f"the verified write for {descriptor.provider_id} failed ({type(failure).__name__}) and "
            f"the stored state does not match either the replacement or the prior pair "
            f"({restore_note}); a pending intent is journaled and reconcile surfaces this conflict"
        ) from failure

    def _restore_endpoint_locked(self, base_slot: str, prior_endpoint: str) -> bool:
        """Restore the endpoint slot to its prior value (or delete it when there was none) and
        VERIFY the restoration by read-back. False means the restoration failed or could not
        be proven — the caller must report that honestly, never as restored."""
        try:
            if prior_endpoint:
                self._cm.store_credential(base_slot, prior_endpoint, label="restored prior value")
            else:
                self._cm.delete_credential(base_slot)
            back = self._strict_get(base_slot)
        except BaseException:
            return False
        return (back or "").strip().rstrip("/") == prior_endpoint

    def _commit_binding_row_locked(self, descriptor: ProviderDescriptor, slot: str, secret: str,
                                   endpoint: str, intent: dict) -> CredentialBinding:
        """Write the binding's durable record — the index row — and close the intent DONE.
        The row carries the committed endpoint for user-endpoint providers, which is what the
        pair read gate compares the live bytes against on every read."""
        digest = self._digest(secret)
        rows = load_index()
        current = rows.get(descriptor.provider_id) if isinstance(rows.get(descriptor.provider_id), dict) else {}
        # A verified commit SUPERSEDES any quarantined paste for this provider: the row it
        # replaces can no longer be read as quarantined, so a leftover quarantine-slot value
        # would be an unreachable shadow claiming the key is still pending. Dropped here, in
        # the SAME transaction -- not left for a reconcile the operator may never run.
        with contextlib.suppress(Exception):
            self._cm.delete_credential(quarantine_slot_for(descriptor))
        row = {
            "binding_id": binding_id_for(descriptor.provider_id),
            "provider_id": descriptor.provider_id,
            "provider_label": descriptor.label,
            "account": str(intent.get("account") or ""),
            "capability_family": descriptor.capability_family,
            "status": str(intent.get("binding_status") or binding_module.STATUS_VERIFIED),
            "last_verified_at": str(intent.get("checked_at") or ""),
            "created_at": str(current.get("created_at") or _utcnow()),
            "key_digest": digest,
            "slot": slot,
            "storage_backend": str(getattr(self._cm, "active_backend", lambda: "vault")()),
        }
        if endpoint:
            row["endpoint"] = endpoint
        rows[descriptor.provider_id] = row
        self._commit_index_locked(rows)  # committed AND verified before the intent closes (R3)
        self._journal_close(slot, PHASE_DONE)
        return binding_from_row(row)  # type: ignore[return-value]

    def replace(self, descriptor: ProviderDescriptor, secret: str, outcome) -> CredentialBinding:
        """EXPLICIT replace: the binding must already exist. Creating by accident is the
        refusal case — a re-paste that should have been a fresh save is an operator error."""
        with self._transaction():
            if self.find(descriptor.provider_id) is None:
                raise IntakeRefusedError(
                    f"no existing binding for {descriptor.provider_id} to replace", status="no_binding",
                )
            return self._save_verified_locked(descriptor, secret, outcome)

    # ------------------------------------------------------------------ save-for-later quarantine

    def save_quarantined(self, descriptor: ProviderDescriptor, secret: str, outcome=None,
                         *, endpoint: str = "") -> CredentialBinding:
        """Public entry: see ``_save_quarantined_locked``. The whole sequence — verified-state
        check included — runs inside the ONE transaction, so a verified save that commits
        concurrently is either fully before (this refuses) or fully after (this wins) it."""
        with self._transaction():
            return self._save_quarantined_locked(descriptor, secret, outcome, endpoint=endpoint)

    def _save_quarantined_locked(self, descriptor: ProviderDescriptor, secret: str, outcome=None,
                                 *, endpoint: str = "") -> CredentialBinding:
        """Store an UNVERIFIED key by explicit operator choice: encrypted, in a quarantine slot
        no execution consumer reads, with an honest row that says exactly that. The row binds
        the secret to ITS OWN destination (``quarantine_endpoint``; the LIVE custom base URL is
        never touched by this path), to the content ``quarantine_generation`` (one-way digest
        of secret+endpoint) and to a fresh ``quarantine_epoch`` — a monotonic operation
        identity, so a delete/recreate of the SAME value is a different operation and a stale
        snapshot or intent cannot act on it (an ABA a content hash cannot distinguish).

        Refused when a VERIFIED binding already exists for the provider — a working key is
        never displaced by an unverified paste, and nothing is modified on refusal."""
        import uuid

        existing = self.find(descriptor.provider_id)
        if existing is not None and existing.status == binding_module.STATUS_VERIFIED:
            raise IntakeRefusedError(
                f"a verified key for {descriptor.provider_id} is already bound; replace it with a "
                "verified one instead of quarantining an unverified paste",
                status="verified_binding_exists",
            )
        slot = quarantine_slot_for(descriptor)
        destination = str(endpoint or "").strip().rstrip("/")
        generation = quarantine_generation_for(str(secret), destination)
        epoch = _next_quarantine_epoch_locked(descriptor.provider_id)
        # The operation's identity: minted once, journaled with the intent AND recorded on the row,
        # so a snapshot, a promotion and a pending delete all name THIS operation — never a later
        # same-value paste that happens to receive the same epoch (revision-5 review R1).
        operation_id = uuid.uuid4().hex
        self._journal_append({
            "slot": slot, "provider_id": descriptor.provider_id, "digest": self._digest(str(secret)),
            "phase": PHASE_WRITE_PENDING, "kind": "quarantine", "ts": _utcnow(),
            "endpoint": destination, "generation": generation, "epoch": epoch,
            "operation_id": operation_id,
        })
        try:
            self._cm.store_credential(slot, str(secret), label=f"{descriptor.label} (unverified)")
        except (TimeoutError, _SecureStorageError) as exc:
            raise StoreWriteTimeoutError(
                f"quarantine write for {descriptor.provider_id} did not complete; a pending intent "
                "is journaled and reconcile will resolve it after restart"
            ) from exc
        # Read-back decides the write. An unreadable slot decides nothing: StorageReadError leaves the
        # pending intent for recovery instead of closing it on a state nobody observed (R4).
        if self._strict_get(slot) != str(secret):
            self._journal_close(slot, PHASE_CONFLICT)
            raise StorageConflictError(f"quarantine read-back mismatch for {descriptor.provider_id}")
        rows = load_index()
        current = rows.get(descriptor.provider_id) if isinstance(rows.get(descriptor.provider_id), dict) else {}
        rows[descriptor.provider_id] = {
            "binding_id": binding_id_for(descriptor.provider_id),
            "provider_id": descriptor.provider_id,
            "provider_label": descriptor.label,
            "account": "",
            "capability_family": descriptor.capability_family,
            "status": binding_module.STATUS_QUARANTINED,
            "last_verified_at": None,
            "created_at": str(current.get("created_at") or _utcnow()),
            "key_digest": self._digest(str(secret)),
            "slot": slot,
            "storage_backend": str(getattr(self._cm, "active_backend", lambda: "vault")()),
            "last_outcome": str(getattr(outcome, "status", "") or ""),
            "quarantine_endpoint": destination,
            "quarantine_generation": generation,
            "quarantine_epoch": epoch,
            "quarantine_operation_id": operation_id,
        }
        self._commit_index_locked(rows)
        self._journal_close(slot, PHASE_DONE)
        return binding_from_row(rows[descriptor.provider_id])  # type: ignore[return-value]

    def quarantine_snapshot(self, provider_id: str) -> QuarantineSnapshot | None:
        """ONE atomic view of a quarantined key: secret, provider, saved endpoint and operation
        identity (generation + epoch), read together under the transaction and
        COHERENCE-CHECKED before anything is handed out: the sealed secret's digest must equal
        the row's ``key_digest`` AND recomputing secret+endpoint must reproduce the row's
        ``quarantine_generation``. A backend write whose index commit failed leaves exactly the
        mismatch this catches (review R1): the new secret beside the OLD row. An incoherent
        state raises ``StorageConflictError`` with named recovery -- the pending journal intent
        still describes the landed write, and reconcile resolves it -- and NO secret leaves the
        store, so no keyed request can be built against a destination the secret does not
        belong to. Retry-before-reconcile is therefore safe, not merely recovered-after."""
        with self._transaction():
            row = load_index().get(str(provider_id or "").strip().lower())
            if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
                return None
            slot = str(row.get("slot") or "")
            if not slot.startswith(QUARANTINE_PREFIX):
                return None
            value = self._strict_get(slot)  # unreadable is StorageReadError, never "not quarantined"
            if value is None:
                return None
            endpoint = str(row.get("quarantine_endpoint") or "")
            digest = self._digest(str(value))
            generation = str(row.get("quarantine_generation") or "")
            if digest != str(row.get("key_digest") or "") or (
                generation and generation != quarantine_generation_for(str(value), endpoint)
            ):
                raise StorageConflictError(
                    f"the quarantined state for {provider_id} is incoherent: the sealed key does not "
                    "match the recorded operation (a write whose index commit failed). Nothing was "
                    "sent anywhere; reconcile (restart or on demand) resolves the pending intent"
                )
            return QuarantineSnapshot(
                provider_id=str(row.get("provider_id") or provider_id),
                secret=str(value),
                endpoint=endpoint,
                generation=generation,
                epoch=int(row.get("quarantine_epoch") or 0),
                key_digest=digest,
                operation_id=str(row.get("quarantine_operation_id") or ""),
            )

    def quarantined_secret(self, provider_id: str) -> tuple[str, str]:
        """(secret, generation) convenience read, coherence-gated exactly like
        ``quarantine_snapshot``: an incoherent state (failed index commit beside a landed
        backend write) hands out NO secret — every reader, not only the retry flow."""
        row = load_index().get(str(provider_id or "").strip().lower())
        if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
            return "", ""
        slot = str(row.get("slot") or "")
        if not slot.startswith(QUARANTINE_PREFIX):
            return "", ""
        value = self._strict_get(slot)
        if value is None:
            return "", ""
        endpoint = str(row.get("quarantine_endpoint") or "")
        generation = str(row.get("quarantine_generation") or "")
        if self._digest(str(value)) != str(row.get("key_digest") or "") or (
            generation and generation != quarantine_generation_for(str(value), endpoint)
        ):
            return "", ""
        return str(value), generation

    def quarantined_endpoint(self, provider_id: str) -> str:
        """The base URL the CURRENT quarantined row names. The retry flow does not use this:
        it verifies the snapshot's endpoint, captured atomically with the secret."""
        row = load_index().get(str(provider_id or "").strip().lower())
        if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
            return ""
        return str(row.get("quarantine_endpoint") or "")

    @staticmethod
    def _snapshot_matches(snapshot: QuarantineSnapshot, row: dict) -> bool:
        """The commit-boundary comparison: the row must still be the exact quarantined
        operation the snapshot read — operation id AND content generation AND epoch AND
        destination AND the secret's digest. Any newer paste, deletion or verified replacement
        fails this, including a same-value re-paste whose epoch was re-issued (review R1)."""
        if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
            return False
        return (
            str(row.get("quarantine_operation_id") or "") == snapshot.operation_id
            and str(row.get("quarantine_generation") or "") == snapshot.generation
            and int(row.get("quarantine_epoch") or 0) == snapshot.epoch
            and str(row.get("quarantine_endpoint") or "").rstrip("/") == snapshot.endpoint.rstrip("/")
            and str(row.get("key_digest") or "") == snapshot.key_digest
        )

    def promote_quarantined(self, descriptor: ProviderDescriptor, secret: str, outcome, *,
                            snapshot: QuarantineSnapshot | None = None,
                            generation: str = "", endpoint: str = "") -> CredentialBinding:
        """Public entry: see ``_promote_locked``."""
        with self._transaction():
            return self._promote_locked(descriptor, secret, outcome, snapshot=snapshot,
                                        generation=generation, endpoint=endpoint)

    def _promote_locked(self, descriptor: ProviderDescriptor, secret: str, outcome, *,
                        snapshot: QuarantineSnapshot | None = None,
                        generation: str = "", endpoint: str = "") -> CredentialBinding:
        """Move a quarantined key into its real slot AFTER a verified outcome, dropping the
        quarantine copy: exactly one live source, bound and available. The COMMIT BOUNDARY is
        the transaction: the current row must still match the snapshot the caller verified
        under (content generation + epoch + endpoint + digest) — a paste, deletion or verified
        replacement that landed since makes this decline WITHOUT touching the newer state. A
        user endpoint's base URL is installed here, at promotion, in the same transaction as
        its key: the active endpoint is never repointed independently of its verified key."""
        if getattr(outcome, "status", "") != "verified":
            raise IntakeRefusedError(
                f"promotion requires a verified outcome, not {getattr(outcome, 'status', 'unknown')!r}",
                status=str(getattr(outcome, "status", "")),
            )
        row = load_index().get(descriptor.provider_id)
        qslot = quarantine_slot_for(descriptor)
        if snapshot is not None:
            if not self._snapshot_matches(snapshot, row if isinstance(row, dict) else {}):
                raise IntakeRefusedError(
                    f"the quarantined key for {descriptor.provider_id} changed while its verification "
                    "was in flight; nothing was promoted",
                    status="quarantine_changed",
                )
            destination = snapshot.endpoint
        else:
            if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
                raise IntakeRefusedError(
                    f"the quarantined key for {descriptor.provider_id} is no longer there",
                    status="quarantine_changed",
                )
            if generation and str(row.get("quarantine_generation") or "") and generation != str(row.get("quarantine_generation") or ""):
                raise IntakeRefusedError(
                    f"the quarantined key for {descriptor.provider_id} changed while its verification "
                    "was in flight; nothing was promoted",
                    status="quarantine_changed",
                )
            destination = str(row.get("quarantine_endpoint") or "")
        # Content check inside the same transaction: the slot holds exactly the verified value.
        seated = self._strict_get(qslot)
        if seated is None or self._digest(str(seated)) != str((row or {}).get("key_digest") or ""):
            raise IntakeRefusedError(
                f"the quarantined key for {descriptor.provider_id} changed while its promotion ran; nothing was promoted",
                status="quarantine_changed",
            )
        binding = self._save_verified_locked(descriptor, secret, outcome, endpoint=destination)
        with contextlib.suppress(Exception):
            self._cm.delete_credential(qslot)
        # the promoted row must not keep quarantine fields: this key is active, not pending
        rows = load_index()
        active = rows.get(descriptor.provider_id)
        if isinstance(active, dict):
            for field in ("quarantine_endpoint", "quarantine_generation", "quarantine_epoch",
                          "quarantine_operation_id", "last_outcome"):
                active.pop(field, None)
            save_index(rows)
        return binding

    def delete_quarantined(self, provider_id: str, *, generation: str = "",
                           snapshot: QuarantineSnapshot | None = None) -> DeleteResult:
        """Public entry: see ``_delete_locked``."""
        with self._transaction():
            return self._delete_locked(provider_id, generation=generation, snapshot=snapshot)

    def _delete_locked(self, provider_id: str, *, generation: str = "",
                       snapshot: QuarantineSnapshot | None = None) -> DeleteResult:
        """Remove a provider's quarantined key and its row. The ENTIRE sequence — snapshot
        comparison, content check, backend deletion, index write — is one transaction: a newer
        paste that re-uses the slot survives a stale delete untouched (the review's F2: the
        destructive call previously raced outside the lock). A verified binding is NOT touched
        by this door — delete/revoke owns that."""
        import uuid

        pid = str(provider_id or "").strip().lower()
        row = load_index().get(pid)
        if not isinstance(row, dict) or row.get("status") != binding_module.STATUS_QUARANTINED:
            return DeleteResult(False, "no quarantined key for this provider")
        slot = str(row.get("slot") or "")
        if not slot.startswith(QUARANTINE_PREFIX):
            return DeleteResult(False, "no quarantined key for this provider")
        if snapshot is not None and not self._snapshot_matches(snapshot, row):
            raise IntakeRefusedError(
                f"the quarantined key for {pid} changed while its deletion was in flight; nothing was deleted",
                status="quarantine_changed",
            )
        if generation and str(row.get("quarantine_generation") or "") and generation != str(row.get("quarantine_generation") or ""):
            raise IntakeRefusedError(
                f"the quarantined key for {pid} changed while its deletion was in flight; nothing was deleted",
                status="quarantine_changed",
            )
        seated_digest = str(row.get("key_digest") or "")
        # Content check inside the SAME transaction that deletes, and BEFORE anything is journaled:
        # nothing can replace the slot's value between this read and the destructive call, and an
        # unreadable slot (StorageReadError) decides nothing and leaves nothing behind (R4).
        seated = self._strict_get(slot)
        if seated is None or (seated_digest and self._digest(str(seated)) != seated_digest):
            raise IntakeRefusedError(
                f"the quarantined key for {pid} changed while its deletion ran; nothing was deleted",
                status="quarantine_changed",
            )
        self._journal_append({
            "slot": slot, "provider_id": pid, "digest": "",
            "phase": PHASE_DELETE_PENDING, "kind": "quarantine_delete", "ts": _utcnow(),
            "generation": str(row.get("quarantine_generation") or ""),
            "epoch": int(row.get("quarantine_epoch") or 0),
            "quarantine_operation_id": str(row.get("quarantine_operation_id") or ""),
            "operation_id": uuid.uuid4().hex,
        })
        try:
            self._cm.delete_credential(slot)
        except (TimeoutError, _SecureStorageError) as exc:
            raise StorageUnavailableError(
                f"quarantine delete for {pid} did not complete; the row is retained for reconcile"
            ) from exc
        # Removal is DECIDED by a strict read-back, never assumed from a call that returned: a delete
        # the backend swallowed, or a slot that cannot be re-read, keeps the row and the pending
        # intent for recovery (R4).
        try:
            remaining = self._strict_get(slot)
        except StorageReadError as exc:
            raise StorageUnavailableError(
                f"the quarantine delete for {pid} could not be verified (its slot is unreadable); the row "
                "is retained for reconcile"
            ) from exc
        if remaining is not None:
            raise StorageUnavailableError(
                f"the quarantined key for {pid} is still present after delete; the row is retained for reconcile"
            )
        rows = load_index()
        current = rows.get(pid)
        if isinstance(current, dict) and current.get("status") == binding_module.STATUS_QUARANTINED:
            rows.pop(pid, None)
        self._commit_index_locked(rows)
        self._journal_close(slot, PHASE_DONE)
        return DeleteResult(True, "quarantined key deleted")

    # ------------------------------------------------------------------ removal

    def delete(self, provider_id: str) -> DeleteResult:
        descriptor = self._registry.get(provider_id)
        if descriptor is None:
            return DeleteResult(False, "unknown provider")
        with self._transaction():
            return self._remove(descriptor, keep_tombstone=False)

    def revoke(self, provider_id: str) -> CredentialBinding | None:
        """Withdraw the capability and remove the secret, keeping an honest tombstone so the
        product can show 'revoked' instead of pretending the credential never existed."""
        descriptor = self._registry.get(provider_id)
        if descriptor is None:
            return None
        with self._transaction():
            self._remove(descriptor, keep_tombstone=True)
            return self.find(descriptor.provider_id)

    def _remove(self, descriptor: ProviderDescriptor, *, keep_tombstone: bool) -> DeleteResult:
        from core.bounded_keyring import keychain_blocked

        slot = descriptor.credential_slot
        pid = descriptor.provider_id
        self._journal_append({
            "slot": slot, "provider_id": pid, "digest": "",
            "phase": PHASE_DELETE_PENDING, "kind": "revoke" if keep_tombstone else "delete",
            "ts": _utcnow(),
        })
        try:
            self._cm.delete_credential(slot)
        except (TimeoutError, _SecureStorageError) as exc:
            raise StorageUnavailableError(
                f"keychain delete for {pid} did not complete; the binding is retained for reconcile"
            ) from exc
        if keychain_blocked():
            # The breaker means the Keychain is unreadable in this process: absence cannot be
            # proven, so removal is NOT claimed. The pending intent re-runs after restart.
            raise StorageUnavailableError(
                f"cannot prove removal of {pid} while the Keychain is blocked; binding retained"
            )
        try:
            remaining = self._strict_get(slot)
        except StorageReadError as exc:
            # Removal cannot be proven from a slot that cannot be read (R4): not claimed.
            raise StorageUnavailableError(
                f"cannot prove removal of {pid}: its slot could not be re-read; binding retained"
            ) from exc
        if remaining is not None:
            raise StorageUnavailableError(f"credential for {pid} still present after delete; binding retained")

        rows = load_index()
        if keep_tombstone:
            row = rows.get(pid) if isinstance(rows.get(pid), dict) else {}
            rows[pid] = {
                "binding_id": binding_id_for(pid),
                "provider_id": pid,
                "provider_label": descriptor.label,
                "account": str((row or {}).get("account") or ""),
                "capability_family": descriptor.capability_family,
                "status": binding_module.STATUS_REVOKED,
                "last_verified_at": (row or {}).get("last_verified_at"),
                "created_at": str((row or {}).get("created_at") or _utcnow()),
                "key_digest": "",
                "slot": slot,
                "storage_backend": str((row or {}).get("storage_backend") or ""),
            }
        else:
            rows.pop(pid, None)
        self._commit_index_locked(rows)
        self._journal_close(slot, PHASE_DONE)
        return DeleteResult(True, "revoked" if keep_tombstone else "deleted")

    # ------------------------------------------------------------------ reconcile + invariants

    def reconcile(self) -> ReconcileReport:
        """Bring index, journal and backend back to ONE truth. Safe to run at boot, on
        demand, or after a timed-out write; every action it takes is reported."""
        with self._transaction():
            return self._reconcile_locked()

    def _reconcile_locked(self) -> ReconcileReport:
        report = ReconcileReport()
        rows = load_index()

        # structurally bad rows are reported and dropped — never silently kept or guessed at
        for pid, row in list(rows.items()):
            if not binding_module.row_is_well_formed(row):
                report.malformed_rows.append(pid)
                rows.pop(pid)

        pending = self._pending_intents()
        conflicted_slots: set[str] = set()
        # Intents DECIDED below close only after the reconciled index is committed and verified.
        decided: list[str] = []
        for slot, intent in list(pending.items()):
            pid = str(intent.get("provider_id") or "")
            kind = str(intent.get("kind") or "save")
            # The intent's slots are read STRICTLY and once, before any branch mutates anything. An
            # unreadable slot decides nothing: the intent stays pending, no row changes, and the
            # provider is reported unresolved (revision-5 review R4). Presence is judged by VALUE
            # for every kind, deletes included — never by the backend's sidecar index.
            try:
                observed = self._strict_get(slot)
                observed_endpoint = (
                    self._strict_get(str(intent.get("base_slot") or ""))
                    if kind == "save" and intent.get("base_slot") else None
                )
            except StorageReadError:
                self._note_unresolved(report, pid)
                continue
            if kind in ("delete", "revoke"):
                if observed is None:
                    decided.append(slot)
                    report.deletions_completed.append(pid)
                continue  # still present: the intent stays pending and honestly visible
            if kind == "quarantine_delete":
                # A quarantined key's removal completing late (or after a restart): done only
                # when the slot is actually empty, and the row is removed only while it is still
                # the quarantined operation the intent names (its operation id when the intent
                # carries one, else generation AND epoch — a delete/recreate of the same value is
                # a different operation). A newer paste that re-used the slot keeps its row and
                # this surfaces as a conflict.
                row = rows.get(pid)
                named_operation = str(intent.get("quarantine_operation_id") or "")
                same_operation = isinstance(row, dict) and row.get("status") == binding_module.STATUS_QUARANTINED and (
                    str(row.get("quarantine_generation") or "") == str(intent.get("generation") or "")
                    and (str(intent.get("epoch") or "") == ""
                         or int(row.get("quarantine_epoch") or 0) == int(intent.get("epoch") or 0))
                    and (not named_operation
                         or str(row.get("quarantine_operation_id") or "") == named_operation)
                )
                if observed is None:
                    if same_operation:
                        rows.pop(pid, None)
                    decided.append(slot)
                    report.deletions_completed.append(pid)
                else:
                    if not same_operation:
                        # the slot now holds a NEWER paste than the delete intended: not ours to remove
                        report.conflicts.append(pid)
                        conflicted_slots.add(slot)
                continue
            if kind == "save" and intent.get("base_slot"):
                # A user-endpoint pair transaction left pending by a lost reply, a failed
                # restoration or a restart (review F1). Recovery is DECIDIVE, both ways: the
                # replacement key landing (late or not) completes the committed pair and its
                # binding row; an observable prior key finishes the rollback (restoring the
                # prior endpoint if the destination half landed alone); anything else is a
                # foreign value this reconcile only surfaces — the intent keeps recovery
                # ownership and is re-reported on every run until the operator resolves it.
                base_slot = str(intent.get("base_slot") or "")
                endpoint = str(intent.get("endpoint") or "")
                prior_endpoint = str(intent.get("prior_endpoint") or "")
                prior_digest = str(intent.get("prior_digest") or "")
                pair_value = observed
                pair_base = observed_endpoint
                if pair_value is not None and self._digest(pair_value) == str(intent.get("digest") or ""):
                    # the replacement landed: complete the committed pair — the completing endpoint
                    # write is VERIFIED by strict read-back before anything is decided (R3)
                    if (pair_base or "").strip().rstrip("/") != endpoint:
                        try:
                            self._cm.store_credential(
                                base_slot, endpoint, label=_endpoint_slot_label(base_slot, str(intent.get("label") or pid)))
                        except Exception:
                            report.conflicts.append(pid)
                            conflicted_slots.add(slot)
                            continue
                        verdict = self._verify_slot_locked(base_slot, endpoint)
                        if verdict == "unreadable":
                            self._note_unresolved(report, pid)
                            continue
                        if verdict == "mismatch":
                            report.conflicts.append(pid)
                            conflicted_slots.add(slot)
                            continue
                    descriptor = self._registry.get(pid)
                    if descriptor is not None:
                        current_pair_row = rows.get(pid) if isinstance(rows.get(pid), dict) else {}
                        rows[pid] = {
                            "binding_id": binding_id_for(pid),
                            "provider_id": pid,
                            "provider_label": str(intent.get("label") or descriptor.label),
                            "account": str(intent.get("account") or ""),
                            "capability_family": descriptor.capability_family,
                            "status": str(intent.get("binding_status") or binding_module.STATUS_VERIFIED),
                            "last_verified_at": str(intent.get("checked_at") or ""),
                            "created_at": str(current_pair_row.get("created_at") or _utcnow()),
                            "key_digest": self._digest(pair_value),
                            "slot": slot,
                            "storage_backend": str(getattr(self._cm, "active_backend", lambda: "vault")()),
                            "endpoint": endpoint,
                        }
                        with contextlib.suppress(Exception):
                            self._cm.delete_credential(quarantine_slot_for(descriptor))
                        report.adopted.append(pid)
                    decided.append(slot)
                elif (pair_value is None and not prior_digest) or (
                    pair_value is not None and prior_digest and self._digest(pair_value) == prior_digest
                ):
                    # the prior key (or no key, when there was none) is what is observable:
                    # finish the rollback — but only over a base this transaction put there
                    observed_base = (pair_base or "").strip().rstrip("/")
                    if observed_base == endpoint and prior_endpoint != endpoint:
                        try:
                            if prior_endpoint:
                                self._cm.store_credential(base_slot, prior_endpoint, label="restored prior value")
                            else:
                                self._cm.delete_credential(base_slot)
                        except Exception:
                            report.conflicts.append(pid)
                            conflicted_slots.add(slot)
                            continue
                        # a restoring write that returned is not a restoration until it reads back (R3)
                        verdict = self._verify_slot_locked(base_slot, prior_endpoint)
                        if verdict == "unreadable":
                            self._note_unresolved(report, pid)
                            continue
                        if verdict == "mismatch":
                            report.conflicts.append(pid)
                            conflicted_slots.add(slot)
                            continue
                    elif observed_base not in (endpoint, prior_endpoint):
                        # a base neither the replacement nor the prior pair names: not ours
                        # to overwrite — surface it and keep the intent pending
                        report.conflicts.append(pid)
                        conflicted_slots.add(slot)
                        continue
                    decided.append(slot)
                else:
                    # foreign value in the slot our intent names: not ours to adopt or remove
                    report.conflicts.append(pid)
                    conflicted_slots.add(slot)
                continue
            value = observed
            if value is None:
                # the write never landed anywhere — close the intent, adopt nothing
                decided.append(slot)
                continue
            if kind == "quarantine":
                # A quarantined write completing late (a bounded door timeout, then a restart):
                # the value lands as exactly one honest quarantined row bound to ITS OWN
                # destination — never as a generic unverified row, never near a real slot.
                if self._digest(value) == str(intent.get("digest") or ""):
                    existing_row = rows.get(pid)
                    if not (isinstance(existing_row, dict) and existing_row.get("status") == binding_module.STATUS_VERIFIED):
                        rows[pid] = {
                            "binding_id": binding_id_for(pid),
                            "provider_id": pid,
                            "provider_label": str(intent.get("label") or ""),
                            "account": "",
                            "capability_family": "",
                            "status": binding_module.STATUS_QUARANTINED,
                            "last_verified_at": None,
                            "created_at": _utcnow(),
                            "key_digest": self._digest(value),
                            "slot": slot,
                            "storage_backend": str(getattr(self._cm, "active_backend", lambda: "vault")()),
                            "last_outcome": "",
                            "quarantine_endpoint": str(intent.get("endpoint") or ""),
                            "quarantine_generation": str(intent.get("generation") or quarantine_generation_for(value, str(intent.get("endpoint") or ""))),
                            "quarantine_epoch": int(intent.get("epoch") or 0),
                            "quarantine_operation_id": str(intent.get("operation_id") or ""),
                        }
                        report.adopted.append(pid)
                    else:
                        # a verified save won while this quarantined write was pending: the
                        # quarantine value is a shadow of an active binding — remove it
                        with contextlib.suppress(Exception):
                            self._cm.delete_credential(slot)
                        report.deduped.append(pid)
                    decided.append(slot)
                    continue
            if self._digest(value) == str(intent.get("digest") or ""):
                if pid and pid not in rows:
                    rows[pid] = {
                        "binding_id": binding_id_for(pid),
                        "provider_id": pid,
                        "provider_label": str(intent.get("label") or ""),
                        "account": "",
                        "capability_family": "",
                        "status": binding_module.STATUS_UNVERIFIED,
                        "last_verified_at": None,
                        "created_at": _utcnow(),
                        "key_digest": self._digest(value),
                        "slot": slot,
                        "storage_backend": str(getattr(self._cm, "active_backend", lambda: "vault")()),
                    }
                    report.adopted.append(pid)
                if self._dedupe_vault_copy(slot, value):
                    report.deduped.append(pid)
                decided.append(slot)
            else:
                # A foreign value sits in the slot our intent names. NOT ours to delete and
                # NOT ours to adopt (the adoption pass below skips conflicted slots): a typed
                # conflict, surfaced every reconcile until the operator resolves it.
                report.conflicts.append(pid)
                conflicted_slots.add(slot)

        # A promotion whose quarantine-copy delete failed (or a restart landed between the two
        # writes) leaves a value in the quarantine slot while the row is already active: that
        # is a shadow of a live binding, not a recoverable quarantine — drop it, never letting
        # the product claim the key "remains quarantined" after it became active.
        for pid, row in rows.items():
            if row.get("status") != binding_module.STATUS_VERIFIED:
                continue
            descriptor = self._registry.get(pid)
            if descriptor is None:
                continue
            qslot = quarantine_slot_for(descriptor)
            if not qslot.startswith(QUARANTINE_PREFIX):
                continue
            try:
                shadow = self._strict_get(qslot)
            except StorageReadError:
                self._note_unresolved(report, pid)
                continue
            if shadow is not None:
                with contextlib.suppress(Exception):
                    self._cm.delete_credential(qslot)
                report.deduped.append(pid)

        # rows whose secret is gone degrade honestly to `missing` (tombstones excepted).
        # Presence is judged by VALUE (get_credential), not has_credential: the backend's
        # sidecar index only knows the legacy fixed slot names, so a custom binding slot
        # would look "absent" there while its key sits in the Keychain.
        for pid, row in rows.items():
            if row.get("status") == binding_module.STATUS_REVOKED:
                continue
            slot = str(row.get("slot") or "")
            if not slot:
                continue
            try:
                present = self._strict_get(slot) is not None
            except StorageReadError:
                # unknown is not missing: the row keeps its status until a read can decide (R4)
                self._note_unresolved(report, pid)
                continue
            if not present:
                if row.get("status") != binding_module.STATUS_MISSING:
                    row["status"] = binding_module.STATUS_MISSING
                    report.missing.append(pid)

        # registry slots holding a value with no row (e.g. a legacy Settings save) are
        # adopted as UNVERIFIED — capability evidence can only come from a completed
        # verification, and none happened on this path.
        for descriptor in self._registry.providers():
            pid = descriptor.provider_id
            if pid in rows:
                continue
            slot = descriptor.credential_slot
            if slot in conflicted_slots:
                continue  # an unresolved conflict is the operator's decision, not an adoption
            try:
                value = self._strict_get(slot)
            except StorageReadError:
                self._note_unresolved(report, pid)
                continue
            if value:
                rows[pid] = {
                    "binding_id": binding_id_for(pid),
                    "provider_id": pid,
                    "provider_label": descriptor.label,
                    "account": "",
                    "capability_family": descriptor.capability_family,
                    "status": binding_module.STATUS_UNVERIFIED,
                    "last_verified_at": None,
                    "created_at": _utcnow(),
                    "key_digest": self._digest(value),
                    "slot": slot,
                    "storage_backend": str(getattr(self._cm, "active_backend", lambda: "vault")()),
                }
                report.adopted.append(pid)

        # Commit ORDER is the recovery contract (revision-5 review R3): the reconciled index is
        # committed and verified FIRST and only then are the decided intents closed. A commit that
        # fails or does not read back raises with every intent still pending, so the next reconcile —
        # after a second interruption, or at the next restart — still owns the recovery. A failure
        # while closing leaves decided intents pending; re-deciding them is idempotent.
        self._commit_index_locked(rows)
        for decided_slot in decided:
            self._journal_close(decided_slot, PHASE_DONE)
        return report

    def _commit_index_locked(self, rows: dict[str, dict]) -> None:
        """Commit the binding index and VERIFY it by read-back, before the caller closes any journal
        intent. The intent is the recovery record for the state its index row describes; closing it
        before that row was committed lost the record whenever the commit failed (revision-5 review
        R3). A commit that raises propagates with every intent still pending; one that returns but
        does not read back as written raises ``StorageConflictError`` the same way."""
        save_index(rows)
        if load_index() != rows:
            raise StorageConflictError(
                "the credential binding index did not read back as written; every pending operation "
                "keeps its recovery record and reconcile (restart or on demand) decides it"
            )

    def _verify_slot_locked(self, slot: str, expected: str) -> str:
        """Strict read-back of a write this store just made: ``verified`` when the slot holds
        ``expected`` (an empty expectation means absent; endpoints compare without a trailing slash),
        ``unreadable`` when the read fails, ``mismatch`` when the write returned without landing."""
        try:
            value = self._strict_get(slot)
        except StorageReadError:
            return "unreadable"
        if (value or "").strip().rstrip("/") == str(expected or "").strip().rstrip("/"):
            return "verified"
        return "mismatch"

    @staticmethod
    def _note_unresolved(report: ReconcileReport, pid: str) -> None:
        if pid and pid not in report.unresolved:
            report.unresolved.append(pid)

    def _dedupe_vault_copy(self, slot: str, value: str) -> bool:
        """When the Keychain holds the value AND a legacy vault ciphertext holds the same
        value, drop the vault copy: exactly one live source. Mismatched values are a
        conflict for the operator, not something this method decides."""
        try:
            if getattr(self._cm, "_active_keyring", lambda: None)() is None:
                return False
            if self._cm._vault_get(slot) != value:
                return False
            return bool(self._cm._vault_drop(slot))
        except Exception:
            return False

    def binding_invariants(self) -> list[str]:
        """Machine-checkable shadow/duplicate detection — what ``reconcile`` exists to keep
        empty. Used by tests and audits to prove the invariant, not just the repair."""
        violations: list[str] = []
        rows = load_index()
        for slot, intent in self._pending_intents().items():
            pid = str(intent.get("provider_id") or slot)
            try:
                value = self._strict_get(slot)
            except StorageReadError:
                violations.append(f"unknown: {slot} could not be read (the pending intent for {pid} is undecidable)")
                continue
            if value is None:
                continue
            if pid not in rows:
                if self._digest(value) == str(intent.get("digest") or ""):
                    violations.append(f"shadow: value stored at {slot} with no binding row (pending write unreconciled)")
                else:
                    violations.append(f"conflict: foreign value at {slot} vs pending intent for {pid}")
        for row in rows.values():
            slot = str(row.get("slot") or "")
            if not slot:
                continue
            try:
                if getattr(self._cm, "_active_keyring", lambda: None)() is None:
                    continue
                keychain_value = self._strict_get(slot)
                if keychain_value is None:
                    continue
                if self._cm._vault_get(slot) == keychain_value:
                    violations.append(f"duplicate: {slot} live in both keychain and vault")
            except Exception:
                continue
        return violations

    def _strict_get(self, slot: str) -> str | None:
        """The ONE read every store decision uses. ``None`` means a read that SUCCEEDED and found
        nothing; an unreadable or unknown backend state raises ``StorageReadError`` and is never
        collapsed to absence (revision-5 review R4: the previous helper caught every backend
        exception and returned None, so a failed read could close a pending operation, degrade a
        live binding to missing or pass for a completed rollback). The backend tells the two apart
        inside its strict-read scope — an unreadable vault, an entry that does not authenticate, a
        keyring call that raised or timed out, the armed Keychain breaker. For a credential module
        without that scope every exception it raises is still unknown, and the process-wide breaker
        stays the failure signal for a None. Callers leave the operation undecided for recovery."""
        from core.bounded_keyring import keychain_blocked

        scope = getattr(self._cm, "strict_reads", None)
        try:
            with scope() if callable(scope) else contextlib.nullcontext():
                value = self._cm.get_credential(slot)
        except Exception as exc:
            raise StorageReadError(
                f"credential slot {slot} could not be read ({type(exc).__name__}); the state is "
                "undecided, not absent"
            ) from exc
        if value is None and keychain_blocked():
            raise StorageReadError(
                f"credential slot {slot} could not be read (keychain blocked); the state is "
                "undecided, not absent"
            )
        return value


def recover_pending_operations(registry: ProviderRegistry | None = None) -> ReconcileReport | None:
    """Decide the credential operations a failed or interrupted Save left pending — at restart,
    before any consumer registers or reads a binding (revision-5 review R3). Reconcile runs only when
    the durable journal names pending work (None otherwise), so an ordinary boot performs no backend
    reads. A reconcile that cannot finish raises with every intent still pending."""
    from core.credential_intelligence.provider_registry import default_registry

    store = CredentialStore(registry or default_registry())
    if not store._pending_intents():
        return None
    return store.reconcile()


__all__ = [
    "QUARANTINE_PREFIX",
    "CredentialStore",
    "DeleteResult",
    "EpochAuthorityError",
    "IntakeRefusedError",
    "ReconcileReport",
    "StorageConflictError",
    "StorageReadError",
    "StorageUnavailableError",
    "StoreWriteTimeoutError",
    "quarantine_generation_for",
    "quarantine_slot_for",
    "recover_pending_operations",
]
