"""First-run provider choice: the ONE provider onboarding state authority.

Owns ``data/first_run_state.json`` — the provider side of first run (Local Only /
Connect / Skip), per the provider onboarding contract's state machine (states and
persisted shape §4.1; laws L1 boot-never-blocks, L10 skip-everywhere, L9 failure
never corrupts). The First-Run Pact (``core/first_run_pact.py``) READS this state
and delegates the choice step to it; it never writes it directly — mutations ride
the registered ``onboarding.*`` commands only.

The state file carries provider-choice truth ONLY: schema, state, revision,
chosen provider id, and the terminal timestamps. No key material, no origins
beyond provider ids, no operator facts (those belong to the Operator Profile),
no boundary values (those belong to ``core/policy_engine``).

Connect-path states exist so the delegated intake flow has a real machine to
advance; at this slice the paste → origin-preview → verify → complete flow rides
the existing ``core/credential_intelligence`` package (zero network until the
operator explicitly verifies against the one pinned endpoint).
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from typing import Any

from core.runtime_paths import active_data_dir

SCHEMA_NAME = "vool.first_run_provider"
SCHEMA_VERSION = 1

FILENAME = "first_run_state.json"

# States (provider spec §4.1). Terminals: local_only_done | connected_done | skipped.
STATE_ABSENT = "absent"
STATE_CARD_VISIBLE = "card_visible"
STATE_LOCAL_ONLY_DONE = "local_only_done"
STATE_CONNECTING = "connecting"
STATE_PROVIDER_PICK = "provider_pick"
STATE_KEY_ENTRY = "key_entry"
STATE_ORIGIN_PREVIEW = "origin_preview"
STATE_VERIFYING = "verifying"
STATE_VERIFIED = "verified"
STATE_TEST_TURN = "test_turn"
STATE_CERT_OFFER = "cert_offer"
STATE_CONNECTED_DONE = "connected_done"
STATE_SKIPPED = "skipped"

TERMINAL_STATES = frozenset({STATE_LOCAL_ONLY_DONE, STATE_CONNECTED_DONE, STATE_SKIPPED})
PROVIDER_TERMINALS_PERMITTING_LOCAL = frozenset({STATE_LOCAL_ONLY_DONE, STATE_CONNECTED_DONE, STATE_SKIPPED})
NON_TERMINAL_STATES = frozenset(
    {
        STATE_ABSENT,
        STATE_CARD_VISIBLE,
        STATE_CONNECTING,
        STATE_PROVIDER_PICK,
        STATE_KEY_ENTRY,
        STATE_ORIGIN_PREVIEW,
        STATE_VERIFYING,
        STATE_VERIFIED,
        STATE_TEST_TURN,
        STATE_CERT_OFFER,
    }
)

# Persisted whitelist (L7-adjacent: states and provider ids only).
_VALUE_KEYS = frozenset({"chosen_provider_id", "skipped_at", "connected_at", "test_turn_message_id"})

_lock = threading.RLock()


class FirstRunError(RuntimeError):
    """Typed provider-first-run fault; ``code`` rides the registry fault path."""

    def __init__(self, code: str, detail: str = "", http_status: int = 409):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.http_status = http_status


def _path() -> Any:
    return active_data_dir() / FILENAME


def _lock_path() -> Any:
    return active_data_dir() / (FILENAME + ".lock")


def _read_raw() -> dict[str, Any] | None:
    try:
        raw = _path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load(*, reseed_on_corrupt: bool = True) -> dict[str, Any]:
    """Read the provider state file, honestly reporting absence; corrupt ⇒ rename + reseed."""
    data = _read_raw()
    if data is None:
        if _path().exists():
            # Unreadable or non-JSON: quarantine and reseed (S-3.1 discipline).
            stamp = time.strftime("%Y%m%d-%H%M%S")
            with contextlib.suppress(OSError):
                _path().rename(_path().with_name(f"{FILENAME}.corrupt-{stamp}"))
            return _fresh(state=STATE_ABSENT)
        return _fresh(state=STATE_ABSENT)
    version = data.get("version")
    state = str(data.get("state") or "")
    if version != SCHEMA_VERSION or state not in (TERMINAL_STATES | NON_TERMINAL_STATES):
        if reseed_on_corrupt:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            with contextlib.suppress(OSError):
                _path().rename(_path().with_name(f"{FILENAME}.corrupt-{stamp}"))
            return _fresh(state=STATE_ABSENT)
        raise FirstRunError("state_unreadable", "first-run provider state has an unknown schema")
    return data


def _fresh(state: str) -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "state": state,
        "revision": 0,
        "chosen_provider_id": "",
        "skipped_at": "",
        "connected_at": "",
        "test_turn_message_id": "",
    }


def _atomic_write(data: dict[str, Any]) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(_path().parent), prefix=".first_run_state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, _path())
        try:
            dir_fd = os.open(str(_path().parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


class _FileLock:
    """Cross-process publication lock — FAIL CLOSED, delegating to the ONE shared
    cross-platform implementation (fcntl on POSIX, msvcrt on Windows). Non-blocking
    acquisition; on contention the mutation refuses with the typed
    ``lock_unavailable`` fault."""

    def __init__(self):
        self._impl = None

    def __enter__(self):
        from core.cross_process_lock import LockUnavailable, PublicationLock

        try:
            self._impl = PublicationLock(_lock_path()).__enter__()
        except LockUnavailable as exc:
            raise FirstRunError(
                "lock_unavailable",
                "another window or process is publishing first-run provider state; retry in a moment",
                http_status=409,
            ) from exc
        return self

    def __exit__(self, *exc):
        if self._impl is not None:
            return bool(self._impl.__exit__(*exc))
        return False


def _idempotent_or_stale(current: dict[str, Any], expect_revision: int | None) -> dict[str, Any]:
    """A terminal-hit choice is idempotent ONLY for CAS-less callers. A caller carrying
    ``expect_revision`` demanded CAS semantics: succeeding without validating it would
    mask a stale carrier as success, so the typed fault is required instead."""
    if expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):
        raise FirstRunError("stale_revision", "the first-run provider state changed; refresh and retry")
    return current


def _mutate(expect_revision: int | None, mutate) -> dict[str, Any]:
    """The ONE publication path: re-read, CAS-check, mutate and publish under a single
    fail-closed lock acquisition — cross-process races resolve to exactly one winner."""
    with _lock:
        with _FileLock():
            current = load()
            if expect_revision is not None and int(expect_revision) != int(current.get("revision", 0)):
                raise FirstRunError("stale_revision", "the first-run provider state changed; refresh and retry")
            next_data = mutate(dict(current))
            next_data["revision"] = int(current.get("revision", 0)) + 1
            whitelist = {"version", "state", "revision"} | _VALUE_KEYS
            for key in list(next_data):
                if key not in whitelist:
                    next_data.pop(key)
            _atomic_write(next_data)
            return next_data


def snapshot() -> dict[str, Any]:
    """Read-only projection for GET surfaces; absence is reported honestly."""
    data = load()
    return {
        "version": data.get("version", SCHEMA_VERSION),
        "state": data.get("state", STATE_ABSENT),
        "revision": int(data.get("revision", 0)),
        "can_connect": data.get("state") in {STATE_CARD_VISIBLE, STATE_ABSENT},
        "chosen_provider_id": data.get("chosen_provider_id") or None,
        "skipped_at": data.get("skipped_at") or None,
        "connected_at": data.get("connected_at") or None,
    }


def is_absent() -> bool:
    return load().get("state") == STATE_ABSENT


def terminal_permits_local() -> bool:
    """True when the provider machine sits on a terminal that permits local operation."""
    return load().get("state") in PROVIDER_TERMINALS_PERMITTING_LOCAL


def seed_if_absent() -> dict[str, Any]:
    """Boot-time idempotent seed: absent stays absent until the operator acts (L1)."""
    data = load()
    if data.get("state") == STATE_ABSENT and not _path().exists():
        with _FileLock():
            _atomic_write(data)
    return data


def choose(choice: str, *, expect_revision: int | None = None) -> dict[str, Any]:
    """Operator choice at the card: local_only | connect | skip."""
    clean = str(choice or "").strip().lower()
    with _lock:
        current = load()

        def _apply(data: dict[str, Any]) -> dict[str, Any]:
            state = data.get("state", STATE_ABSENT)
            if clean == "local_only":
                if state not in NON_TERMINAL_STATES:
                    raise FirstRunError("invalid_transition", f"cannot choose local_only from {state}")
                data["state"] = STATE_LOCAL_ONLY_DONE
                return data
            if clean == "skip":
                if state in TERMINAL_STATES:
                    return data  # idempotent
                data["state"] = STATE_SKIPPED
                data["skipped_at"] = _utcnow()
                return data
            if clean == "connect":
                if state in TERMINAL_STATES:
                    raise FirstRunError("invalid_transition", f"cannot connect from terminal {state}")
                data["state"] = STATE_PROVIDER_PICK
                return data
            raise FirstRunError("invalid_choice", f"unknown first-run choice {clean!r}")

        if current.get("state") in TERMINAL_STATES and clean == "skip":
            return _idempotent_or_stale(current, expect_revision)
        if current.get("state") == STATE_LOCAL_ONLY_DONE and clean == "local_only":
            return _idempotent_or_stale(current, expect_revision)
        return _mutate(expect_revision, _apply)


def reset(*, expect_revision: int | None = None) -> dict[str, Any]:
    """Settings 'review setup': back to the card; keys/boundaries are never touched here."""
    return _mutate(expect_revision, lambda data: {**_fresh(STATE_CARD_VISIBLE)})


def advance(state: str, *, expect_revision: int | None = None, provider_id: str = "") -> dict[str, Any]:
    """Advance the delegated connect flow one operator-driven step."""
    order = {
        STATE_PROVIDER_PICK: STATE_KEY_ENTRY,
        STATE_KEY_ENTRY: STATE_ORIGIN_PREVIEW,
        STATE_ORIGIN_PREVIEW: STATE_VERIFYING,
        STATE_VERIFYING: STATE_VERIFIED,
        STATE_VERIFIED: STATE_TEST_TURN,
        STATE_TEST_TURN: STATE_CERT_OFFER,
        STATE_CERT_OFFER: STATE_CONNECTED_DONE,
    }
    if state not in order:
        raise FirstRunError("invalid_transition", f"cannot advance to {state!r}")

    def _apply(data: dict[str, Any]) -> dict[str, Any]:
        current = data.get("state")
        if current != state and not (current == STATE_CONNECTING and state == STATE_PROVIDER_PICK):
            raise FirstRunError("invalid_transition", f"provider flow is at {current!r}, not {state!r}")
        data["state"] = order[state]
        if state == STATE_PROVIDER_PICK and provider_id:
            data["chosen_provider_id"] = str(provider_id)
        if order[state] == STATE_CONNECTED_DONE:
            data["connected_at"] = _utcnow()
        return data

    return _mutate(expect_revision, _apply)


def pick_provider(provider_id: str, *, expect_revision: int | None = None) -> dict[str, Any]:
    clean = str(provider_id or "").strip()
    if not clean:
        raise FirstRunError("invalid_choice", "a provider id is required")
    return _mutate(
        expect_revision,
        lambda data: {**data, "state": STATE_KEY_ENTRY, "chosen_provider_id": clean},
    )


def note_test_turn(message_id: str, *, expect_revision: int | None = None) -> dict[str, Any]:
    return _mutate(
        expect_revision,
        lambda data: {**data, "test_turn_message_id": str(message_id or "")},
    )
