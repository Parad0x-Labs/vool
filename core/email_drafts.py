"""Durable email draft + send-receipt state: the reviewable-reply authority.

WHY THIS EXISTS
---------------
`email.reply`'s preview was STATELESS: it echoed `confirm_arguments` and nothing
persisted, so a follow-up "make it shorter" had nothing to edit and "send that"
could only re-send whatever body a later call happened to carry — the reviewed
version and the sent version were never provably the same thing. Measured on base
df49f096 (v1 evidence/red-repro.json, step draft-review-state).

THE CONTRACT (v2 — hardened after independent review of v1)
------------------------------------------------------------
A draft is a row of reviewable state under the runtime home, owned by the session
(and principal) that created it:

  * BIND TO WHAT WILL BE SENT: `save_draft` resolves the CANONICAL final message
    once — recipients as sanitized, the subject with its Re: prefix applied, the
    body WITH the signature folded in, the exact In-Reply-To/References chain, and
    the CONCRETE account (the default resolved to a name, and that account's
    mailbox identity, i.e. its from-address — never its secret). The approval
    hash covers that canonical form, so a References edit, a subject change, a
    body edit, a recipient change, or re-pointing the account to a different
    mailbox each invalidates the approval. A password refresh on the SAME mailbox
    identity does not.
  * RESERVE BEFORE DISPATCH: sending persists the full reservation — status
    `sending` plus the pre-generated Message-ID — BEFORE any socket work, under
    an exclusive OS file lock (`fcntl.flock` on the store's lock file) that makes
    the check-and-set atomic across threads AND processes. A second caller (or a
    restarted process, or a request after a crashed receipt write) sees `sending`
    and never dispatches again: an unresolved in-flight effect is not retryable.
  * AN UNKNOWN DELIVERY IS STICKY: once an accepted-but-unacknowledged send is
    recorded (`delivery_unknown`), ordinary editing and re-approving cannot clear
    it — the review's measured double-send. Only `reconcile_draft` (which proves
    the outcome from the account's own Sent copy of OUR Message-ID) or an honest
    continued-unknown resolution moves the state, and nothing here ever resends.
  * The send executes the STORED canonical content verbatim: no live re-derivation
    of subject prefix, signature or threading can drift from what was approved.

Everything fails closed with a structured result; nothing raises into a turn.

STORE RECOVERY (revision 5)
---------------------------
Damaged store bytes are evidence of effects that may already have happened, so they
are never overwritten. `_load` quarantines them durably before anything replaces them:
the replacement is written and synced first, the original is renamed aside atomically,
the quarantined bytes are hash-verified, and only then is the replacement installed (a
failure after the rename renames the evidence back). When that cannot be done, every
operation fails closed with store_recovery_required and the structured state. A
recovered store whose damaged bytes recorded unresolved sends holds ordinary sends
(store_recovery_hold) until an operator acknowledges the exact quarantine file
(acknowledge_store_recovery). Quarantined evidence of unresolved sends with no store
beside it is an interrupted recovery: it holds sends too and never reads as empty.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core import runtime_paths

_DRAFTS_LOCK = threading.RLock()
_MAX_DRAFTS = 200
_LOCK_TIMEOUT_SECONDS = 10.0
# Unresolved send effects: a reservation exists for each of them.
_UNRESOLVED_STATES = frozenset({"sending", "delivery_unknown"})
_TERMINAL_STATES = frozenset({"sent", "sent_confirmed", "failed"})
# What damaged store bytes can still prove, read from the raw bytes (no parse needed).
_UNRESOLVED_MARKER = re.compile(rb'"status"\s*:\s*"(sending|delivery_unknown)"')
_SEND_HOLD_MARKER = re.compile(rb'"send_hold"\s*:\s*true')
_MESSAGE_ID_MARKER = re.compile(rb'"(?:sent_message_id|reserved_message_id|message_id)"\s*:\s*"(<[^"<>\s]{1,300}>)"')
#: The account slots damaged bytes name (a reservation's or a draft's account): where an unresolved
#: send could be reconciled, when the operator asks.
_ACCOUNT_MARKER = re.compile(rb'"(?:account_resolved|account)"\s*:\s*"([A-Za-z0-9_.@+\-]{1,100})"')
_QUARANTINE_GLOB = "drafts.corrupt-*.json"
# Per-thread depth of the cross-process store lock (re-entrant within a thread).
_LOCK_DEPTH = threading.local()


@dataclass
class DraftResult:
    ok: bool
    status: str
    message: str
    draft: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _drafts_path() -> Path:
    return runtime_paths.active_data_dir() / "email" / "drafts.json"


def _now() -> float:
    return time.time()


def _store_shape_ok(data: Any) -> bool:
    """The store contract: an object with a `drafts` map of row objects, and an
    optional integer version. Anything else is damaged state, not an empty store."""
    if not isinstance(data, dict) or not isinstance(data.get("drafts"), dict):
        return False
    if "version" in data and not isinstance(data.get("version"), int):
        return False
    return all(isinstance(row, dict) for row in data["drafts"].values())


class DraftStoreRecoveryRequiredError(OSError):
    """The draft store cannot be used safely until its damaged bytes are durably preserved.

    Carries the structured, operator-visible recovery state. Raising it means nothing was
    written over the damaged bytes."""

    def __init__(self, message: str, state: dict[str, Any]) -> None:
        super().__init__(message)
        self.state = state


def _parse_store(raw: bytes) -> tuple[dict[str, Any] | None, str]:
    """(store, "") for valid bytes; (None, reason) for damaged ones."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "unparseable store bytes"
    if not _store_shape_ok(data):
        return None, "schema-invalid store (drafts map missing or malformed)"
    return data, ""


def _evidence(raw: bytes) -> dict[str, Any]:
    """What damaged bytes still prove: their hash and size, the unresolved-send markers they
    contain, and every Message-ID they name (some of those may be already-confirmed sends)."""
    markers = {match.decode("ascii") for match in _UNRESOLVED_MARKER.findall(raw)}
    if _SEND_HOLD_MARKER.search(raw):
        markers.add("send_hold")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "unresolved_markers": sorted(markers),
        "message_ids_in_damaged_store": sorted({match.decode("utf-8", "replace")
                                                for match in _MESSAGE_ID_MARKER.findall(raw)}),
        "accounts_in_damaged_store": sorted({match.decode("utf-8", "replace")
                                             for match in _ACCOUNT_MARKER.findall(raw)}),
    }


def _quarantine_target(path: Path) -> Path:
    """A collision-safe quarantine name: never an existing generation of evidence."""
    stamp = int(_now() * 1000)
    candidate = path.with_name(f"drafts.corrupt-{stamp}.json")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"drafts.corrupt-{stamp}-{suffix}.json")
        suffix += 1
    return candidate


def _write_synced(target: Path, data: bytes) -> None:
    with open(target, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _encode(state: dict[str, Any]) -> bytes:
    return json.dumps(state, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")


def _recovery_failure(path: Path, reason: str, evidence: dict[str, Any], error: BaseException, *,
                      quarantine: str = "") -> DraftStoreRecoveryRequiredError:
    state = {"status": "recovery_required", "reason": reason, "store": path.name, "quarantine": quarantine,
             "send_hold": True, "error": f"{type(error).__name__}: {error}", **evidence}
    where = (f"its bytes are preserved in {quarantine}, but no usable store could be installed" if quarantine
             else "its bytes were left exactly where they are")
    return DraftStoreRecoveryRequiredError(
        f"The email draft store is damaged ({reason}) and could not be recovered safely "
        f"({type(error).__name__}); {where}. No draft operation can run until recovery succeeds, and "
        "any unresolved send recorded there stays unresolved.",
        state,
    )


def _recover(path: Path, raw: bytes, reason: str) -> dict[str, Any]:
    """Durably quarantine damaged store bytes, then install a recovery store (lock held).

    The order is what makes it fail closed: (1) the replacement store is written and synced
    beside the original; (2) the original is quarantined by an atomic rename; (3) the
    quarantined bytes are verified against the damaged bytes' hash and the rename is synced;
    (4) only then is the replacement installed. A failure before (2) leaves the original in
    place; a failure after (2) renames the evidence back, and if even that fails the
    quarantine stands alone (see _missing_store). Nothing writes over damaged bytes."""
    evidence = _evidence(raw)
    target = _quarantine_target(path)
    recovery = {
        "status": "unresolved_effects" if evidence["unresolved_markers"] else "recovered",
        "reason": reason,
        "quarantine": target.name,
        "send_hold": bool(evidence["unresolved_markers"]),
        **evidence,
    }
    state = {"version": 1, "drafts": {}, "recovered_at": _now(), "recovery": recovery}
    prepared = path.with_name("drafts.recovery.tmp")
    try:
        _write_synced(prepared, _encode(state))
    except OSError as exc:
        with contextlib.suppress(OSError):
            prepared.unlink()
        raise _recovery_failure(path, reason, evidence, exc) from exc
    try:
        path.rename(target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            prepared.unlink()
        raise _recovery_failure(path, reason, evidence, exc) from exc
    try:
        if hashlib.sha256(target.read_bytes()).hexdigest() != evidence["sha256"]:
            raise OSError("the quarantined bytes do not match the damaged store")
        _fsync_directory(path.parent)
        prepared.replace(path)
    except OSError as exc:
        restored = False
        if not path.exists():
            with contextlib.suppress(OSError):
                target.rename(path)
                restored = True
        with contextlib.suppress(OSError):
            prepared.unlink()
        raise _recovery_failure(path, reason, evidence, exc,
                                quarantine="" if restored else target.name) from exc
    with contextlib.suppress(OSError):
        _fsync_directory(path.parent)
    return state


def _missing_store(path: Path) -> dict[str, Any]:
    """No store file. Normally a fresh home; but quarantined evidence of unresolved sends with
    no store beside it is an interrupted recovery, and must not read as an empty store: a
    recovery store that holds sends is installed in its place."""
    if not path.parent.is_dir():
        return {"drafts": {}}
    held: list[tuple[Path, dict[str, Any]]] = []
    for quarantined in sorted(path.parent.glob(_QUARANTINE_GLOB)):
        try:
            evidence = _evidence(quarantined.read_bytes())
        except OSError:
            continue
        if evidence["unresolved_markers"]:
            held.append((quarantined, evidence))
    if not held:
        return {"drafts": {}}
    with _store_lock():
        if path.exists():
            return _load()
        newest, newest_evidence = held[-1]
        recovery = {
            "status": "unresolved_effects",
            "reason": "no store beside quarantined evidence of unresolved sends (interrupted recovery)",
            "quarantine": newest.name,
            "quarantines": [quarantined.name for quarantined, _evidence_row in held],
            "send_hold": True,
            "sha256": newest_evidence["sha256"],
            "bytes": newest_evidence["bytes"],
            "unresolved_markers": sorted({m for _q, row in held for m in row["unresolved_markers"]}),
            "message_ids_in_damaged_store": sorted({i for _q, row in held
                                                    for i in row["message_ids_in_damaged_store"]}),
            "accounts_in_damaged_store": sorted({a for _q, row in held
                                                 for a in row.get("accounts_in_damaged_store") or []}),
        }
        state = {"version": 1, "drafts": {}, "recovered_at": _now(), "recovery": recovery}
        _save(state)
        return state


def _load() -> dict[str, Any]:
    """The store, recovered first when its bytes are damaged (see _recover). Raises
    DraftStoreRecoveryRequiredError when damaged bytes cannot be durably preserved."""
    path = _drafts_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return _missing_store(path)
    except OSError as exc:
        raise DraftStoreRecoveryRequiredError(
            f"The email draft store could not be read ({type(exc).__name__}); it is not treated as "
            "empty, and no draft operation can run until it is readable.",
            {"status": "unreadable", "reason": "unreadable store", "store": path.name, "quarantine": "",
             "send_hold": True, "error": f"{type(exc).__name__}: {exc}"},
        ) from exc
    data, reason = _parse_store(raw)
    if data is not None:
        return data
    with _store_lock():
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return _missing_store(path)
        data, reason = _parse_store(raw)
        if data is not None:
            return data  # a concurrent recovery already replaced the damaged bytes
        return _recover(path, raw, reason)


def _save(state: dict[str, Any]) -> None:
    """A synced, crash-consistent write that never replaces damaged bytes it has not
    quarantined: bytes found damaged at write time refuse the write."""
    path = _drafts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        current = None
    if current is not None:
        _existing, reason = _parse_store(current)
        if reason:
            raise DraftStoreRecoveryRequiredError(
                f"The email draft store on disk is damaged ({reason}); this write was refused so those "
                "bytes are not overwritten. The next load quarantines them first.",
                {"status": "recovery_required", "reason": reason, "store": path.name, "quarantine": "",
                 "send_hold": True, "error": "damaged bytes at write time", **_evidence(current)},
            )
    tmp = path.with_suffix(".tmp")
    try:
        _write_synced(tmp, _encode(state))
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    with contextlib.suppress(OSError):
        _fsync_directory(path.parent)


def _recovery_required(exc: DraftStoreRecoveryRequiredError) -> DraftResult:
    return DraftResult(False, "store_recovery_required", str(exc), details={"store_recovery": dict(exc.state)})


def _recovery_details(state: dict[str, Any]) -> dict[str, Any]:
    """The recovery record, surfaced on results while it holds sends."""
    recovery = state.get("recovery") if isinstance(state, dict) else None
    if isinstance(recovery, dict) and recovery.get("send_hold"):
        return {"store_recovery": dict(recovery)}
    return {}


@contextlib.contextmanager
def _store_lock() -> Iterator[None]:
    """Exclusive cross-PROCESS lock around one read-modify-write cycle of the store.

    The atomic-rename write makes each _save crash-consistent; the flock makes the
    reservation check-and-set ('is this draft still approved? then mark sending')
    atomic between concurrent callers in any number of threads or processes — the
    at-most-once dispatch guarantee the review measured v1 lacking. A busy store
    times out (fail closed) rather than blocking a turn indefinitely. Re-entrant
    within one thread, so store recovery inside `_load` takes the same lock whether
    or not its caller already holds it; a lock that cannot be opened is typed."""
    if getattr(_LOCK_DEPTH, "value", 0):
        _LOCK_DEPTH.value += 1
        try:
            yield
        finally:
            _LOCK_DEPTH.value -= 1
        return
    path = _drafts_path()
    lock_path = path.with_suffix(".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+")  # noqa: SIM115 - closed by the with-block below
    except OSError as exc:
        raise DraftStoreRecoveryRequiredError(
            f"The email draft store's lock cannot be opened ({type(exc).__name__}); no draft operation "
            "can run until the store directory is usable.",
            {"status": "store_unavailable", "reason": "store lock unavailable", "store": path.name,
             "quarantine": "", "send_hold": True, "error": f"{type(exc).__name__}: {exc}"},
        ) from exc
    with handle:
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("email draft store is busy (lock timeout)")
                time.sleep(0.02)
        _LOCK_DEPTH.value = 1
        try:
            yield
        finally:
            _LOCK_DEPTH.value = 0
            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _prune(state: dict[str, Any]) -> None:
    drafts = state.get("drafts") or {}
    if len(drafts) <= _MAX_DRAFTS:
        return
    # UNRESOLVED reservations are retained WITHOUT LIMIT: they are the only durable
    # identity of a dispatched effect (reserved Message-ID) and reconciliation's
    # ground truth. The bounded history cap applies only to the rest. (Measured
    # defect: grouping unresolved with terminal let 200 newer completed drafts
    # evict the one unresolved reservation.)
    keep = {k: v for k, v in drafts.items() if str(v.get("status") or "") in _UNRESOLVED_STATES}
    remaining = sorted(
        ((k, v) for k, v in drafts.items() if k not in keep),
        key=lambda kv: -float(kv[1].get("updated_at") or 0),
    )
    keep.update(dict(remaining[: max(0, _MAX_DRAFTS - len(keep))]))
    state["drafts"] = keep


def _scope(session_id: str, principal: str | None) -> tuple[str, str]:
    from core.email_tools import _profile_scope

    who, bound_session, _project = _profile_scope(principal, session_id)
    return who, str(bound_session or session_id or "")


def _visible(draft: dict[str, Any], principal: str, session_id: str) -> bool:
    owner_principal = str(draft.get("principal") or "")
    if owner_principal and owner_principal != principal:
        return False
    # A draft created inside a session belongs to that session. The owner
    # principal may still see it from a session-less context (same human),
    # but a DIFFERENT session must not resolve, edit, approve or send it.
    owner_session = str(draft.get("session_id") or "")
    return not (owner_session and session_id and owner_session != session_id)


def _account_identity(account_name: str) -> str:
    """The CONCRETE mailbox identity behind an account slot, lowercased — LOCAL.

    For OAuth providers this is the outward From alias plus the principal named
    by the account's configuration, marked `oauth-meta:` because metadata is not
    proof of authentication. It is what an approval binds; the PROOF happens at
    dispatch and reconciliation inside the adapter, whose PrincipalBinding reads
    the provider's own profile with every credential that carries a bound
    request: an unconfirmable mailbox is refused (identity_unverified) and a
    different one is refused (needs_reapproval). Computing the local binding
    costs no network round-trip, so saving and approving add no provider requests."""
    from core import email_tools

    return email_tools.configured_identity(account_name, email_tools._load_account("smtp", account_name))


def _reserved_from_address(account_name: str) -> str:
    from core import email_tools

    creds = email_tools._load_account("smtp", account_name)
    if not isinstance(creds, dict):
        return ""
    return str(creds.get("from_addr") or creds.get("username") or "")


def _canonical_content(
    *,
    to: list[str],
    subject: str,
    body: str,
    account_name: str,
    in_reply_to: str,
    references: list[str],
    is_reply: bool,
    signature: str,
) -> dict[str, Any]:
    """The exact message form the wire will carry, resolved ONCE at save time.

    Hashing caller inputs and re-deriving at send is how an approval drifts from
    the sent message (v1: the Re: prefix, the signature and the default account
    were all re-resolved later). Everything downstream reads this canonical form;
    nothing re-derives it."""
    from core import email_tools

    recipients = [email_tools._header_safe(r) for r in to]
    subj = email_tools._header_safe(subject)
    if is_reply and subj and not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"
    final_body = str(body or "")
    if str(signature or "").strip():
        final_body = f"{final_body}\n\n{str(signature).strip()}"
    headers = email_tools.threading_headers(in_reply_to=in_reply_to, references=references) or {}
    return {
        "to": [r for r in recipients if r],
        "subject": subj,
        "body": final_body,
        "in_reply_to": str(headers.get("In-Reply-To") or email_tools._header_safe(in_reply_to) or ""),
        "references": str(headers.get("References") or ""),
        "account": str(account_name or ""),
        "account_identity": _account_identity(account_name),
    }


def _content_hash(canonical: dict[str, Any]) -> str:
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _public(draft: dict[str, Any]) -> dict[str, Any]:
    keys = ("draft_id", "kind", "account_resolved", "to", "subject", "body",
            "in_reply_to", "references", "version", "status", "created_at", "updated_at",
            "sent_message_id")
    out = {k: draft.get(k) for k in keys}
    approval = draft.get("approval") or None
    out["approved"] = bool(approval and approval.get("approved_hash") == draft.get("content_hash"))
    out["approval_stale"] = bool(approval and approval.get("approved_hash") != draft.get("content_hash"))
    return out


def reset_drafts() -> None:
    """Tests: clear the durable draft state under the active runtime home."""
    with _DRAFTS_LOCK:
        path = _drafts_path()
        try:
            path.unlink(missing_ok=True)
            path.with_suffix(".tmp").unlink(missing_ok=True)
        except OSError:
            pass


def save_draft(
    *,
    to: Any,
    subject: str,
    body: str,
    account: str = "",
    in_reply_to: str = "",
    references: list[str] | None = None,
    kind: str = "compose",
    draft_id: str = "",
    session_id: str = "",
    principal: str | None = None,
) -> DraftResult:
    """Create a draft, or edit an existing one (same owner session only).

    The row stores the CANONICAL final message (account resolved, subject prefix
    applied, signature folded, threading chain built) and its hash; an edit
    recomputes both, so any change — including References-only or an account
    re-point — visibly re-opens review. Editing is REFUSED while a send effect is
    unresolved (sending / delivery_unknown): the unresolved effect and its
    Message-ID must be preserved and reconciled, not rewritten."""
    from core import email_tools

    recipients = [to] if isinstance(to, str) else [str(r) for r in (to or [])]
    recipients = [r.strip() for r in recipients if str(r).strip()]
    if not recipients:
        return DraftResult(False, "invalid_recipient", "A draft needs at least one recipient address.")
    if not str(subject or "").strip() and kind == "compose":
        return DraftResult(False, "invalid_subject", "A new-email draft needs a subject.")
    who, session = _scope(session_id, principal)
    # The account authority selects the mailbox (an explicit account, else the operator's default
    # through the front-door turn scope); the result is PINNED here and identity-checked at send, so
    # default-account drift can never redirect an approved send. A selection that is refused refuses
    # the draft with the configured choices: a draft bound to no mailbox could only fail later.
    from core.email_accounts import select_account

    selection = select_account("smtp", account=str(account or ""))
    if not selection.ok:
        return DraftResult(False, selection.status, selection.message)
    account_name = selection.account
    try:
        signature = email_tools.email_signature_for(session_id=session)
    except Exception:
        signature = ""
    canonical = _canonical_content(
        to=recipients, subject=str(subject or ""), body=str(body or ""), account_name=account_name,
        in_reply_to=str(in_reply_to or ""), references=[str(r) for r in (references or []) if str(r).strip()],
        is_reply=bool(str(in_reply_to or "").strip()) or str(kind) == "reply", signature=signature,
    )
    content_hash = _content_hash(canonical)
    try:
        with _store_lock():
            state = _load()
            drafts = state.setdefault("drafts", {})
            now = _now()
            target_id = str(draft_id or "").strip()
            if target_id:
                existing = drafts.get(target_id)
                if existing is None or not _visible(existing, who, session):
                    return DraftResult(False, "not_found", f"No editable draft '{target_id}' in this session.")
                status = str(existing.get("status") or "")
                if status in _TERMINAL_STATES - {"failed"}:
                    return DraftResult(
                        False, "already_sent",
                        "This draft was already sent; start a new draft for further changes.",
                        draft=_public(existing),
                    )
                if status in _UNRESOLVED_STATES:
                    return DraftResult(
                        False, "reconciliation_required",
                        "This draft has an unresolved delivery (status "
                        f"'{status}', Message-ID {existing.get('sent_message_id') or 'unknown'}). "
                        "Editing cannot clear it: reconcile it first (email.draft.reconcile); the "
                        "uncertain send must not be duplicated by a reworded request.",
                        draft=_public(existing),
                    )
                existing.update({
                    "to": canonical["to"], "subject": canonical["subject"], "body": canonical["body"],
                    "account_resolved": canonical["account"],
                    "in_reply_to": canonical["in_reply_to"], "references": canonical["references"].split(),
                    "canonical": canonical, "content_hash": content_hash, "updated_at": now,
                    "version": int(existing.get("version") or 1) + 1,
                })
                if (existing.get("approval") or {}).get("approved_hash") != content_hash:
                    # Keep the stale binding on purpose: "you approved an earlier version"
                    # is review state the user should see, and send_draft still refuses
                    # on the hash mismatch. Status returns to draft — nothing is sendable.
                    existing["status"] = "draft"
                draft = existing
            else:
                new_id = f"ed-{uuid.uuid4().hex[:12]}"
                drafts[new_id] = {
                    "draft_id": new_id, "kind": str(kind or "compose"),
                    "account_resolved": canonical["account"],
                    "to": canonical["to"], "subject": canonical["subject"], "body": canonical["body"],
                    "in_reply_to": canonical["in_reply_to"], "references": canonical["references"].split(),
                    "canonical": canonical, "principal": who, "session_id": session,
                    "content_hash": content_hash, "version": 1, "approval": None,
                    "status": "draft", "created_at": now, "updated_at": now,
                }
                draft = drafts[new_id]
            _prune(state)
            _save(state)
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    except TimeoutError as exc:
        return DraftResult(False, "store_busy", f"Draft store is busy; try again. ({exc})")
    return DraftResult(
        True, "saved",
        f"Draft saved (version {draft['version']}); review before sending.",
        draft=_public(draft), details=_recovery_details(state),
    )


def get_draft(draft_id: str = "", *, session_id: str = "", principal: str | None = None,
              latest: bool = False) -> DraftResult:
    """One draft by id, or the session's most recent draft (`latest=True`)."""
    who, session = _scope(session_id, principal)
    try:
        state = _load()
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    recovery = _recovery_details(state)
    drafts = state.get("drafts") or {}
    if latest:
        candidates = [d for d in drafts.values() if _visible(d, who, session)]
        if not candidates:
            return DraftResult(False, "not_found", "No draft in this session yet.", details=dict(recovery))
        draft = max(candidates, key=lambda d: float(d.get("updated_at") or 0))
    else:
        draft = drafts.get(str(draft_id or "").strip())
        if draft is None or not _visible(draft, who, session):
            return DraftResult(False, "not_found", f"No draft '{draft_id}' in this session.",
                               details=dict(recovery))
    return DraftResult(True, "ok", "Draft retrieved.", draft=_public(draft),
                       details={"internal": draft, **recovery})


def session_drafts_snapshot(session_id: str = "", *, principal: str | None = None) -> list[dict[str, Any]]:
    """Read-only: the drafts THIS session owns, newest first, as draft_id / status / updated_at.

    For deciding whether a follow-up turn continues this session's email work
    (core.email_work_state) and nothing else. It takes no lock and never recovers, quarantines or
    rewrites the store: a missing, unreadable or damaged store reads as no drafts here, and the
    draft tools report its recovery state when they run. Session-less drafts are left out: every
    session of their owner can see them, so they cannot say which conversation is working.
    """
    who, session = _scope(session_id, principal)
    if not session:
        return []
    try:
        state, _reason = _parse_store(_drafts_path().read_bytes())
    except OSError:
        return []
    if state is None:
        return []
    rows: list[dict[str, Any]] = []
    for key, draft in (state.get("drafts") or {}).items():
        if not isinstance(draft, dict) or str(draft.get("session_id") or "") != session:
            continue
        if not _visible(draft, who, session):
            continue
        try:
            updated_at = float(draft.get("updated_at") or 0)
        except (TypeError, ValueError):
            continue
        rows.append({"draft_id": str(draft.get("draft_id") or key), "status": str(draft.get("status") or ""),
                     "updated_at": updated_at})
    return sorted(rows, key=lambda row: -row["updated_at"])


def approve_draft(
    draft_id: str = "", *, session_id: str = "", principal: str | None = None,
    expected_version: int | None = None, latest: bool = False,
) -> DraftResult:
    """Bind the user's explicit approval to the draft's CURRENT canonical content.

    REFUSED while a send effect is unresolved (sending / delivery_unknown): an
    ordinary approval is not evidence the uncertain message failed to send, and
    must not become authorization to duplicate it. Reconciliation is the only
    supported resolution path for those states."""
    who, session = _scope(session_id, principal)
    try:
        with _store_lock():
            state = _load()
            drafts = state.get("drafts") or {}
            if latest:
                candidates = [d for d in drafts.values() if _visible(d, who, session)]
                if not candidates:
                    return DraftResult(False, "not_found", "No draft in this session to approve.")
                draft = max(candidates, key=lambda d: float(d.get("updated_at") or 0))
            else:
                draft = drafts.get(str(draft_id or "").strip())
                if draft is None or not _visible(draft, who, session):
                    return DraftResult(False, "not_found", f"No draft '{draft_id}' in this session.")
            status = str(draft.get("status") or "")
            if status in {"sent", "sent_confirmed"}:
                return DraftResult(True, "already_sent", "This draft was already sent.", draft=_public(draft))
            if status in _UNRESOLVED_STATES:
                return DraftResult(
                    False, "reconciliation_required",
                    "This draft's earlier send is UNRESOLVED (status "
                    f"'{status}', Message-ID {draft.get('sent_message_id') or 'unknown'}). Approving it "
                    "again cannot clear that: the uncertain message may already be delivered, and a "
                    "second send would duplicate it. Reconcile the delivery first "
                    "(email.draft.reconcile); only a resolved outcome allows a fresh decision.",
                    draft=_public(draft),
                )
            if expected_version is not None and int(draft.get("version") or 0) != int(expected_version):
                return DraftResult(
                    False, "version_mismatch",
                    f"Draft changed since review (now version {draft.get('version')}, "
                    f"approval named version {expected_version}); review and approve again.",
                    draft=_public(draft),
                )
            draft["approval"] = {
                "approved_hash": draft.get("content_hash"),
                "approved_at": _now(),
                "approved_version": int(draft.get("version") or 0),
            }
            draft["status"] = "approved"
            draft["updated_at"] = _now()
            _save(state)
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    except TimeoutError as exc:
        return DraftResult(False, "store_busy", f"Draft store is busy; try again. ({exc})")
    return DraftResult(
        True, "approved", "Approval recorded for the exact reviewed content.", draft=_public(draft),
        details=_recovery_details(state),
    )


def _reserved_receipt(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome": str(draft.get("status") or ""),
        "message": "A send was dispatched and its final receipt is missing; delivery is UNKNOWN.",
        "message_id": str(draft.get("sent_message_id") or ""),
        "to": list(draft.get("to") or []),
        "subject": str(draft.get("subject") or ""),
        "account": str(draft.get("account_resolved") or "default"),
        "in_reply_to": str(draft.get("in_reply_to") or ""),
        "sent_at": float(draft.get("updated_at") or 0),
    }


def send_draft(
    draft_id: str = "", *, session_id: str = "", principal: str | None = None, latest: bool = False,
) -> DraftResult:
    """Send an APPROVED draft EXACTLY ONCE. The reservation is durable.

    Ordering, under the cross-process store lock: check approval → persist the
    reservation (status `sending` + the pre-generated Message-ID) → release the
    lock → execute the SMTP dispatch → persist the terminal receipt. Any caller
    that arrives after the reservation (overlap, restart, lost receipt write)
    sees `sending` and returns WITHOUT dispatching. Outcomes: sent |
    sent_confirmed | delivery_unknown (sticky, reconcile-only) | send_in_progress
    (reservation exists, outcome unrecorded — reconcile) | needs_reapproval |
    not_approved | not_found | send_failed (never reached the wire)."""
    from core import email_tools

    who, session = _scope(session_id, principal)
    try:
        with _store_lock():
            state = _load()
            drafts = state.get("drafts") or {}
            if latest:
                candidates = [d for d in drafts.values() if _visible(d, who, session)]
                if not candidates:
                    return DraftResult(False, "not_found", "No draft in this session to send.")
                draft = max(candidates, key=lambda d: float(d.get("updated_at") or 0))
            else:
                draft = drafts.get(str(draft_id or "").strip())
                if draft is None or not _visible(draft, who, session):
                    return DraftResult(False, "not_found", f"No draft '{draft_id}' in this session.")
            status = str(draft.get("status") or "")
            if status in {"sent", "sent_confirmed", "delivery_unknown", "sending"}:
                # Replay / overlap / restart / lost receipt: return the recorded state,
                # send nothing. 'sending' means the dispatch happened (or began) and the
                # receipt is missing — reconcile it; it is NEVER re-dispatched.
                receipt = draft.get("receipt") or (_reserved_receipt(draft) if status == "sending" else {})
                label = "send_in_progress" if status == "sending" else status
                return DraftResult(
                    True, label,
                    {
                        "sending": "A send of this draft was already dispatched and its final receipt is "
                                   "missing. It will NOT be sent again; reconcile the delivery "
                                   "(email.draft.reconcile).",
                        "delivery_unknown": "This draft's earlier send is UNRESOLVED; it is not resent. "
                                           "Reconcile the delivery (email.draft.reconcile).",
                    }.get(status, "This draft already has a send receipt; no second message was sent."),
                    draft=_public(draft), details={"receipt": receipt},
                )
            approval = draft.get("approval") or {}
            if not approval or approval.get("approved_hash") != draft.get("content_hash"):
                return DraftResult(
                    False, "needs_reapproval" if approval else "not_approved",
                    "The current draft content has not been approved for sending; "
                    "show it for review and get an explicit approval first.",
                    draft=_public(draft),
                )
            canonical = dict(draft.get("canonical") or {})
            account_name = str(canonical.get("account") or draft.get("account_resolved") or "default")
            if _account_identity(account_name) != str(canonical.get("account_identity") or ""):
                return DraftResult(
                    False, "needs_reapproval",
                    f"The account '{account_name}' no longer resolves to the mailbox this approval "
                    "reviewed; re-review and re-approve before sending.",
                    draft=_public(draft),
                )
            recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else {}
            if recovery.get("send_hold"):
                # The store was recovered from damaged bytes that recorded unresolved sends. Their
                # effect identities are not in this store, so a new send could duplicate one.
                named = ", ".join(recovery.get("message_ids_in_damaged_store") or []) or "none recoverable"
                return DraftResult(
                    False, "store_recovery_hold",
                    "Sending is on hold: the draft store was recovered from damaged bytes that recorded "
                    f"unresolved sends ({', '.join(recovery.get('unresolved_markers') or [])}). Message-IDs "
                    f"named there: {named}. The evidence is preserved in "
                    f"{recovery.get('quarantine') or 'the quarantine file'}. Check the account's sent mail "
                    "for those messages; an operator then acknowledges that quarantine file to release "
                    "sending. Nothing was sent.",
                    draft=_public(draft), details={"store_recovery": dict(recovery)},
                )
            # RESERVE, durably, BEFORE any network work: this row is what every other
            # caller/process/restart reads to know a dispatch already exists.
            message_id = email_tools._generate_message_id(_reserved_from_address(account_name))
            draft["status"] = "sending"
            draft["sent_message_id"] = message_id
            draft["reservation"] = {
                "message_id": message_id, "reserved_at": _now(),
                "approved_hash": draft.get("content_hash"),
                "account": account_name,
                "account_identity": _account_identity(account_name),
                # Graph assigns its own Message-ID; reconciliation may need these.
                "subject": str(canonical.get("subject") or ""),
                "recipient": str((canonical.get("to") or [""])[0] or ""),
            }
            draft["updated_at"] = _now()
            _save(state)
            draft_id_value = str(draft.get("draft_id") or "")
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    except TimeoutError as exc:
        return DraftResult(False, "store_busy", f"Draft store is busy; try again. ({exc})")

    # Outside the lock: network IO must not hold the store. The reservation above
    # is what makes this window safe — any competing caller already saw 'sending'.
    result = email_tools.send_email(
        to=canonical.get("to") or [], subject=str(canonical.get("subject") or ""),
        body=str(canonical.get("body") or ""), account=account_name,
        extra_headers={
            "In-Reply-To": str(canonical.get("in_reply_to") or ""),
            "References": str(canonical.get("references") or ""),
        } if str(canonical.get("in_reply_to") or "").strip() else None,
        signature="", message_id_override=message_id, prepend_subject_prefix=False,
        expected_identity=str(canonical.get("account_identity") or ""),
    )
    try:
        with _store_lock():
            state = _load()
            draft = (state.get("drafts") or {}).get(draft_id_value) or draft
            receipt = {
                "outcome": result.status,
                "message": result.message,
                "message_id": str(result.details.get("message_id") or message_id),
                "reserved_message_id": message_id,
                "to": list(canonical.get("to") or []),
                "subject": str(canonical.get("subject") or ""),
                "account": account_name,
                "account_identity": (draft.get("reservation") or {}).get("account_identity") or "",
                "in_reply_to": str(canonical.get("in_reply_to") or ""),
                "sent_at": _now(),
                "acceptance": str(result.details.get("acceptance") or ""),
                "provider": str(result.details.get("provider") or ""),
                "thread_id": str(result.details.get("thread_id") or ""),
                "parent_provider_id": str(result.details.get("parent_provider_id") or ""),
                # Which mailbox the provider confirmed for the credentials that carried
                # this send (empty for transports without that proof).
                "verified_principal": str(result.details.get("verified_principal") or ""),
                "principal_verifications": int(result.details.get("principal_verifications") or 0),
                # The wire form the provider received and, for replies, whether the
                # provider-side parent was found (None when the transport does not say).
                "representation": str(result.details.get("representation") or ""),
                "parent_resolved": result.details.get("parent_resolved"),
            }
            if result.status in {"needs_reapproval", "not_approved", "needs_credentials",
                                 "quota_exceeded", "identity_unverified"}:
                # Refused BEFORE the wire: nothing was dispatched, so the
                # reservation must not stand. The draft returns to its prior
                # review state and the reserved id is released.
                draft["status"] = "approved" if draft.get("approval") else "draft"
                draft.pop("reservation", None)
                draft["sent_message_id"] = ""
                draft["updated_at"] = _now()
                _save(state)
                return DraftResult(False, result.status, result.message, draft=_public(draft))
            draft["receipt"] = receipt
            draft["sent_message_id"] = receipt["message_id"]
            if result.ok and result.status == "executed":
                draft["status"] = "sent"
            elif result.status == "delivery_unknown":
                draft["status"] = "delivery_unknown"
            else:
                # A real failure re-opens the draft; the approval binding stays,
                # but the user decides whether to retry after seeing the failure.
                draft["status"] = "failed"
            draft["updated_at"] = _now()
            _save(state)
    except DraftStoreRecoveryRequiredError as exc:
        # The store became unusable between the reservation and the receipt. The
        # reservation's evidence is in those damaged bytes (a `sending` marker), so a
        # later recovery holds sends; this attempt is never repeated.
        return DraftResult(
            False, "store_recovery_required",
            f"{exc} The send attempt ended as {result.status}; its outcome could not be recorded and "
            "it will not be attempted again.",
            details={"store_recovery": dict(exc.state),
                     "unrecorded_outcome": {"status": result.status, "message_id": message_id}},
        )
    except Exception:
        # The terminal receipt could not be recorded (disk failure, crash). The
        # RESERVATION is already durable: the draft stays 'sending' and any later
        # call refuses to re-dispatch and points at reconciliation instead.
        raise
    ok = draft["status"] in {"sent", "delivery_unknown"}
    return DraftResult(ok, draft["status"], result.message, draft=_public(draft),
                       details={"receipt": receipt})


def reconcile_draft(
    draft_id: str = "", *, session_id: str = "", principal: str | None = None,
) -> DraftResult:
    """Resolve an UNRESOLVED send (`delivery_unknown` or `sending`) against the
    RESERVED account's own Sent view — never a changed default or replacement.

    The reserved Message-ID (or, for providers that assign their own, the reserved
    subject/recipient/window hints) proves the outcome. Absence keeps the state
    exactly as it is: a Sent view's absence is not proof of non-delivery, and
    nothing here may resend. Ordinary approval or editing never clears an
    unresolved effect — this lookup is the one supported resolution. The final
    write is serialized under the cross-process store lock so concurrent
    reconciliations cannot interleave."""
    who, session = _scope(session_id, principal)
    with _DRAFTS_LOCK:
        try:
            state = _load()
        except DraftStoreRecoveryRequiredError as exc:
            return _recovery_required(exc)
        draft = (state.get("drafts") or {}).get(str(draft_id or "").strip())
        if draft is None or not _visible(draft, who, session):
            return DraftResult(False, "not_found", f"No draft '{draft_id}' in this session.")
        status = str(draft.get("status") or "")
        if status not in _UNRESOLVED_STATES:
            return DraftResult(
                True, status,
                "Only an unresolved send (delivery_unknown / sending) needs reconciliation.",
                draft=_public(draft),
            )
        message_id = str(draft.get("sent_message_id")
                         or (draft.get("reservation") or {}).get("message_id")
                         or (draft.get("receipt") or {}).get("message_id") or "")
    if not message_id:
        return DraftResult(
            False, "cannot_reconcile",
            "No reserved Message-ID was recorded for this send; reconciliation needs one.",
            draft=_public(draft),
        )

    from core import email_tools

    # The RESERVED account — not whatever the default binding says today. If the
    # slot has since been re-pointed to a different mailbox, reconciliation
    # through the replacement would inspect the WRONG sent view: refuse with the
    # exact reserved identity so the operator can reconnect it.
    account = str((draft.get("reservation") or {}).get("account")
                  or draft.get("account_resolved") or "default")
    reserved_identity = str((draft.get("reservation") or {}).get("account_identity") or "")
    current_identity = _account_identity(account)
    if reserved_identity and current_identity != reserved_identity:
        return DraftResult(
            False, "reconciliation_blocked",
            f"The account slot '{account}' now resolves to a different mailbox than the one this "
            f"send was reserved against (reserved identity {reserved_identity!r}, now "
            f"{current_identity!r}). Reconciliation must run against the reserved mailbox; "
            "reconnect it or verify delivery with the provider directly. Nothing is resent.",
            draft=_public(draft),
        )
    reservation = dict(draft.get("reservation") or {})
    found = email_tools.message_in_folder(
        account=account, folder="Sent", message_id=message_id,
        subject=str(reservation.get("subject") or ""),
        recipient=str(reservation.get("recipient") or ""),
        recipients=list(draft.get("to") or []),
        since_epoch=float(reservation.get("reserved_at") or 0.0),
        # Evidence must come from the RESERVED mailbox, proven under the credential
        # that reads it — not from whatever the slot's grant authenticates today.
        expected_identity=reserved_identity or current_identity,
    )
    from core.email_accounts import ACCOUNT_REFUSALS
    from core.email_providers.base import BINDING_REFUSALS

    if found.status in BINDING_REFUSALS or found.status in ACCOUNT_REFUSALS:
        return DraftResult(
            False, "reconciliation_blocked",
            f"Reconciliation could not prove that the credential for account '{account}' still reaches "
            f"the reserved mailbox ({found.message}). No sent view was read, the send stays unresolved, "
            "and nothing is resent.",
            draft=_public(draft),
        )
    try:
        with _store_lock():
            state = _load()
            draft = (state.get("drafts") or {}).get(draft_id) or draft
            if found.ok and found.found:
                receipt = dict(draft.get("receipt") or {})
                receipt["outcome"] = "sent_confirmed"
                receipt["message_id"] = message_id
                receipt["reconciled_at"] = _now()
                receipt["reconciliation_evidence"] = found.message
                draft["receipt"] = receipt
                draft["status"] = "sent_confirmed"
                draft["updated_at"] = _now()
                _save(state)
                return DraftResult(
                    True, "sent_confirmed",
                    f"The account's own sent view confirms the send went through. ({found.message})",
                    draft=_public(draft), details={"receipt": receipt},
                )
            if found.ok and "candidate" in found.message:
                # Bounded candidate evidence (Graph): visible, never a confirmation.
                receipt = dict(draft.get("receipt") or {})
                receipt["reconciliation_evidence"] = found.message
                draft["receipt"] = receipt
                _save(state)
                return DraftResult(
                    True, status,
                    f"Reconciliation found only candidate evidence: {found.message}. "
                    "Delivery is NOT confirmed and nothing is resent.",
                    draft=_public(draft), details={"receipt": receipt},
                )
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    except TimeoutError as exc:
        return DraftResult(False, "store_busy", f"Draft store is busy; try again. ({exc})")
    if status == "delivery_unknown":
        return DraftResult(
            True, "delivery_unknown",
            f"The reserved send is not visible in the account's sent view; delivery remains "
            f"unknown. {found.message if found.ok else ''} "
            "Do not resend on this alone — check with the recipient or the provider's own logs.",
            draft=_public(draft),
        )
    return DraftResult(
        True, "send_in_progress",
        "The reservation is not visible in the account's sent view yet, so the send "
        "remains unresolved (dispatched or dispatching, receipt missing). It will NOT be "
        "re-dispatched; check again later or confirm with the provider.",
        draft=_public(draft),
    )


def recovery_generation(state: dict[str, Any]) -> str:
    """The identity of the CURRENT recovery: the quarantine file, the hash of the bytes it holds and
    the moment the recovery store was installed. A page that loaded one generation cannot acknowledge
    another (a later damage installs a new generation), and a quarantine name alone cannot be replayed
    against different evidence."""
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
    if not recovery:
        return ""
    material = "|".join((str(recovery.get("quarantine") or ""), str(recovery.get("sha256") or ""),
                         repr(state.get("recovered_at") or "")))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _acknowledge_effect(recovery: dict[str, Any]) -> str:
    named = list(recovery.get("message_ids_in_damaged_store") or [])
    unresolved = [m for m in named if str((recovery.get("checks") or {}).get(m, {}).get("status") or "") != "found"]
    effect = ("Acknowledging releases NEW approved sends from this draft store. It does not resend anything, "
              "and it does not resolve the sends the damaged bytes recorded")
    if unresolved:
        effect += f": {len(unresolved)} Message-ID(s) stay unresolved ({', '.join(unresolved)})"
    effect += ". The quarantined evidence is kept."
    return effect


def _recovery_summary(recovery: dict[str, Any]) -> str:
    named = list(recovery.get("message_ids_in_damaged_store") or [])
    accounts = list(recovery.get("accounts_in_damaged_store") or [])
    if recovery.get("send_hold"):
        text = (f"Sending is on hold. The email draft store was recovered from damaged bytes ({recovery.get('reason')}) "
                f"that recorded unresolved sends ({', '.join(recovery.get('unresolved_markers') or []) or 'unknown state'}). ")
        text += (f"Message-IDs named there: {', '.join(named)}. " if named else "No Message-ID could be recovered from them. ")
        text += (f"Account(s) named there: {', '.join(accounts)}. " if accounts else "")
        text += (f"The original bytes are preserved in {recovery.get('quarantine')} ({recovery.get('bytes')} bytes). "
                 "Nothing was sent or resent. Check the named messages in the account's sent mail, then acknowledge "
                 "to release new approved sends.")
        return text
    if str(recovery.get("status") or "") == "acknowledged":
        return (f"Sending was released by an operator acknowledgement; the recovery evidence "
                f"({recovery.get('quarantine')}) is kept.")
    return (f"The draft store was recovered from damaged bytes ({recovery.get('reason')}) that recorded no unresolved "
            f"send; sending was never held. The original bytes are preserved in {recovery.get('quarantine')}.")


def recovery_surface_view() -> dict[str, Any]:
    """The operator's view of the draft store's recovery state: plain language, the preserved evidence,
    the identifiers actually known, the checks recorded so far, the current generation and the exact
    effect an acknowledgement would have. Read-only; never the damaged bytes, never a credential."""
    try:
        state = _load()
    except DraftStoreRecoveryRequiredError as exc:
        recovery = dict(exc.state)
        return {"ok": False, "send_hold": True, "recovery": recovery, "generation": "",
                "summary": str(exc), "acknowledge_effect": "", "status": "store_recovery_required"}
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
    if not recovery:
        return {"ok": True, "send_hold": False, "recovery": None, "generation": "",
                "summary": "Sending is not held: the draft store has no recovery record.",
                "acknowledge_effect": "", "status": "no_recovery"}
    held = bool(recovery.get("send_hold"))
    return {"ok": True, "send_hold": held, "recovery": dict(recovery), "generation": recovery_generation(state),
            "summary": _recovery_summary(recovery), "acknowledge_effect": _acknowledge_effect(recovery) if held else "",
            "status": str(recovery.get("status") or "recovered")}


def check_recovery_message(message_id: str) -> DraftResult:
    """OPERATOR action: look one Message-ID the hold names up in the sent view of the account the damaged
    bytes named for it, and record the outcome. `found` resolves that identifier; `absent` is not proof
    of non-delivery and says so; `unverified` means the account could not be inspected. None of them
    releases the hold or resends anything. Not a model tool."""
    from core import email_tools

    wanted = str(message_id or "").strip()
    try:
        state = _load()
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
    if not recovery:
        return DraftResult(False, "no_recovery", "The draft store has no recovery record to check against.")
    if wanted not in list(recovery.get("message_ids_in_damaged_store") or []):
        return DraftResult(False, "unknown_message_id",
                           f"The recovery record names no Message-ID {wanted!r}; only the identifiers it names can be checked.",
                           details={"store_recovery": dict(recovery)})
    accounts = list(recovery.get("accounts_in_damaged_store") or [])
    check: dict[str, Any] = {"message_id": wanted, "account": "", "status": "unverified", "message": "",
                             "checked_at": _now()}
    if not accounts:
        check["message"] = ("The damaged bytes named no account for this send, so no sent view can be inspected; "
                            "the hold stays.")
    for account in accounts:
        found = email_tools.message_in_folder(account=account, folder="Sent", message_id=wanted)
        check["account"] = account
        if found.ok and found.found:
            check.update(status="found", message=f"Found in the sent view of account '{account}': {found.message}")
            break
        if found.ok:
            check.update(status="absent",
                         message=(f"Not in the sent view of account '{account}' ({found.message}). Absence there is not "
                                  "proof the message was unsent -- a provider view can lag -- so the hold stays."))
            continue
        check.update(status="unverified",
                     message=(f"The sent view of account '{account}' could not be inspected ({found.status}: "
                              f"{found.message}). Nothing is known about this send; the hold stays."))
    persisted = True
    try:
        with _store_lock():
            state = _load()
            current = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
            if current is not None:
                checks = dict(current.get("checks") or {})
                checks[wanted] = dict(check)
                current["checks"] = checks
                state["recovery"] = current
                _save(state)
                recovery = current
    except (DraftStoreRecoveryRequiredError, TimeoutError, OSError):
        persisted = False
    return DraftResult(True, check["status"], check["message"],
                       details={"check": check, "check_persisted": persisted, "store_recovery": dict(recovery)})


def acknowledge_store_recovery(quarantine: str, *, confirm: bool = False, generation: str = "",
                               via: str = "") -> DraftResult:
    """OPERATOR action: release the send hold a store recovery placed, once the unresolved
    sends its damaged bytes recorded have been checked outside VOOL. Requires the exact
    quarantine file name the hold names, the current recovery generation when one is given
    (an operator surface always gives it), and an explicit confirmation. Not a model tool.

    Released means PERSISTED: a write that fails leaves the hold in place and says so."""
    if not confirm:
        return DraftResult(
            False, "confirmation_required",
            "Acknowledging a store recovery releases held sends. Check the account's sent mail for the "
            "Message-IDs it names, then acknowledge with confirm=True.",
        )
    try:
        with _store_lock():
            state = _load()
            recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
            if not recovery or not recovery.get("send_hold"):
                return DraftResult(True, "no_hold", "No store recovery is holding sends.",
                                   details={"store_recovery": dict(recovery or {})})
            expected = str(recovery.get("quarantine") or "")
            if str(quarantine or "").strip() != expected:
                return DraftResult(
                    False, "quarantine_mismatch",
                    f"The hold names quarantine file {expected!r}; acknowledge that exact file.",
                    details={"store_recovery": dict(recovery)},
                )
            current_generation = recovery_generation(state)
            if str(generation or "").strip() and str(generation).strip() != current_generation:
                return DraftResult(
                    False, "generation_mismatch",
                    "The recovery this acknowledgement names is not the current one (the store was recovered "
                    "again since that page loaded). Reload, review the current hold, and acknowledge that.",
                    details={"store_recovery": dict(recovery), "generation": current_generation},
                )
            recovery.update({"send_hold": False, "status": "acknowledged", "acknowledged_at": _now(),
                             "acknowledged_generation": current_generation,
                             "acknowledged_via": str(via or "python")})
            state["recovery"] = recovery
            try:
                _save(state)
            except OSError as exc:
                # The store on disk still holds; reading it back is the only truth about release.
                held = True
                with contextlib.suppress(Exception):
                    held = bool((_load().get("recovery") or {}).get("send_hold"))
                if held:
                    return DraftResult(
                        False, "acknowledgement_not_persisted",
                        f"The acknowledgement could not be written ({type(exc).__name__}: {exc}); sending stays on "
                        "hold. Nothing was released.",
                        details={"store_recovery": {**recovery, "send_hold": True, "status": "unresolved_effects"}},
                    )
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    except TimeoutError as exc:
        return DraftResult(False, "store_busy", f"Draft store is busy; try again. ({exc})")
    return DraftResult(
        True, "acknowledged", f"Store recovery {expected} acknowledged; sending is released.",
        details={"store_recovery": dict(recovery)},
    )


def store_recovery_status() -> DraftResult:
    """The operator-visible recovery state of the draft store (read-only)."""
    try:
        state = _load()
    except DraftStoreRecoveryRequiredError as exc:
        return _recovery_required(exc)
    recovery = state.get("recovery") if isinstance(state.get("recovery"), dict) else None
    if not recovery:
        return DraftResult(True, "no_recovery", "The draft store has no recovery record.")
    return DraftResult(
        True, str(recovery.get("status") or "recovered"),
        "The draft store was recovered from damaged bytes; see the recovery record.",
        details={"store_recovery": dict(recovery), "generation": recovery_generation(state)},
    )


__all__ = [
    "DraftResult",
    "DraftStoreRecoveryRequiredError",
    "acknowledge_store_recovery",
    "approve_draft",
    "check_recovery_message",
    "get_draft",
    "reconcile_draft",
    "recovery_generation",
    "recovery_surface_view",
    "reset_drafts",
    "save_draft",
    "send_draft",
    "session_drafts_snapshot",
    "store_recovery_status",
]
