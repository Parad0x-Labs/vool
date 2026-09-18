"""Local credential store for capability onboarding.

Secrets VOOL/VOOL needs to act on the user's behalf (the OpenRouter/BYOK cloud keys, SMTP
app-passwords, X API keys, ...) are stored at rest and never leave the machine. There are two
backends, chosen automatically:

* **macOS Keychain** (preferred, Apple lane) -- the secret VALUE is stored in the native OS secret
  store via the ``keyring`` library (Security.framework), protected by the login session / Secure
  Enclave, not by a file on disk. A non-secret sidecar (``credentials.meta.json``) tracks which
  names exist + their labels so ``list_credentials`` works (Keychain has no "list accounts" API).
* **AES-256-GCM vault file** (fallback) -- for environments without a usable keyring backend
  (headless Linux, CI, the dev venv). Each value is sealed under a key derived from the node
  signing key (``network.signer.derive_local_secret``); this is the historical scheme.

Per VOOL_MIGRATION.md §4 the Keychain path is the hardening: it removes the "stolen disk -> plaintext
seed -> vault key -> every secret" exposure, because the secret value no longer lives in a
seed-sealed file. On first Keychain-available run, existing vault secrets are migrated into the
Keychain (read-back verified) and their ciphertext is scrubbed from the vault.

The public API is unchanged across both backends: ``store_credential`` / ``get_credential`` /
``has_credential`` / ``list_credentials`` / ``delete_credential``. ``list_credentials`` and
``export_diagnostics`` never return a value -- only names, labels, and a masked last-4.

Windows Credential Manager / DPAPI is the Windows lane's job; the ``_load_keyring`` selector is the
seam where that backend plugs in. This module gates Keychain to macOS on purpose.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import sys
import time
from contextvars import ContextVar

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.runtime_paths import data_path

_STORE_LABEL = "vool-credential-store-v1"
_STORE_FILE = "credentials.enc.json"                # AES vault (fallback + pre-migration source)
_META_FILE = "credentials.meta.json"                # non-secret sidecar for the Keychain path
_MIGRATION_MARKER = "credentials.keychain_migrated"  # one-time "vault -> keychain done" marker
_KC_SERVICE = "nulla-credentials"                   # Keychain service; account = credential name
_STORE_MODE_ENV = "VOOL_CREDENTIAL_STORE"          # keychain | vault | auto (default)


class CredentialReadError(RuntimeError):
    """A credential read whose outcome is UNKNOWN — distinct from a read that SUCCEEDED and found
    nothing (``None``): the vault file exists but cannot be read or parsed, an entry exists but does
    not authenticate, the keyring call raised or timed out, or the process-wide Keychain breaker is
    armed. Reads raise it only inside :func:`strict_reads`; the vault writer raises it in every scope
    rather than rewrite a vault whose existing contents it could not read."""


#: Whether the current call stack asked for strict reads. The default stays lenient: every other
#: consumer's contract is "the value or None" and never raises.
_STRICT_READS: ContextVar[bool] = ContextVar("vool_credential_strict_reads", default=False)


@contextlib.contextmanager
def strict_reads():
    """Scope in which ``get_credential`` tells absence (``None``) from an unreadable or unknown
    backend state (:class:`CredentialReadError`). A transaction that DECIDES on what it reads —
    rollback, recovery, removal, adoption — reads inside this scope, so a failed read can never be
    taken for "no credential" (revision-5 review R4)."""
    token = _STRICT_READS.set(True)
    try:
        yield
    finally:
        _STRICT_READS.reset(token)


# --------------------------------------------------------------------------- backend selection

def _mode() -> str:
    value = str(os.environ.get(_STORE_MODE_ENV) or "").strip().lower()
    return value if value in {"keychain", "vault", "auto"} else "auto"


from core.bounded_keyring import bounded_keyring_call, keychain_blocked
from core.keychain_policy import keychain_write_granted, surface_fallback_notice


def _load_keyring():
    """Return the ``keyring`` module IF a real macOS Keychain backend is active, else None.

    Gated to macOS on purpose (Windows Credential Manager is the other lane). Rejects the
    ``fail``/``null``/``chainer`` backends, which would silently store nothing. Tests monkeypatch
    this to inject an in-memory fake keyring.
    """
    if sys.platform != "darwin":
        return None
    try:
        import keyring
    except Exception:
        return None
    try:
        backend = keyring.get_keyring()
        ident = f"{backend.__class__.__module__}.{backend.__class__.__name__}".lower()
    except Exception:
        return None
    if "fail" in ident or "null" in ident or "chainer" in ident:
        return None  # no real store behind it -> never trust it with a secret
    return keyring


def _keychain_previously_used() -> bool:
    """This runtime has used the Keychain before (migration marker or sidecar index present).
    Reads of ALREADY-SAVED credentials stay available without a fresh grant (the normal
    signed-app path); a scratch/isolated home has neither signal and never touches the Keychain."""
    try:
        if data_path(_MIGRATION_MARKER).exists():
            return True
    except Exception:
        pass
    return bool(_meta_load())


def _active_keyring(*, write: bool = False):
    """The keyring to use for this call (honours VOOL_CREDENTIAL_STORE), or None for the vault.

    Policy (2026-09-02 operator addendum):
    - ``write=True`` (store/migrate): only after the EXPLICIT operator grant
      (VOOL_KEYCHAIN_ALLOWED / Settings flag) — a fresh or scratch runtime must never
      initiate an interactive Keychain operation.
    - reads: granted runtimes, plus runtimes with a PRIOR keychain marker (existing saved
      credentials remain readable through the normal path).
    - every path: None once the process-wide circuit breaker armed (a timed-out call means a
      prompt is pending that this process cannot cancel — never retry it).
    Each denial with a real backend present surfaces the one-time fallback notice.
    """
    if keychain_blocked():
        return None
    mode = _mode()
    if mode == "vault":
        return None
    allowed = keychain_write_granted() if write else (keychain_write_granted() or _keychain_previously_used())
    if not allowed:
        if _load_keyring() is not None and mode == "keychain":
            surface_fallback_notice()
        return None
    backend = _load_keyring()
    if backend is None:
        surface_fallback_notice()
    return backend


def active_backend() -> str:
    """Which backend is live right now: 'keychain' or 'vault' (for diagnostics/tests)."""
    return "keychain" if _active_keyring() is not None else "vault"


def active_storage_class() -> str:
    """The TRUTHFUL storage class of the live credential backend (core.secret_storage vocabulary).

    The vault is AES-GCM sealed, but its sealing key sits in a 0600 file in the same user
    profile — so the honest class is account-file-permissions, never anything stronger."""
    from core.secret_storage import (
        STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS,
        STORAGE_CLASS_KEYCHAIN,
        assert_known_storage_class,
    )

    if _active_keyring() is not None:
        return assert_known_storage_class(STORAGE_CLASS_KEYCHAIN)
    return assert_known_storage_class(STORAGE_CLASS_ACCOUNT_FILE_PERMISSIONS)


# --------------------------------------------------------------------------- vault (AES) backend

def _key() -> bytes:
    # Derived per-label secret from the node signing key (never the raw seed). Deferred import
    # keeps this module importable in environments where the signer backend isn't loaded.
    from network.signer import derive_local_secret

    return derive_local_secret(_STORE_LABEL, length=32)


def _store_path():
    return data_path(_STORE_FILE)


def _aad(name: str) -> bytes:
    # Binds each ciphertext to its own credential name, so entries can't be swapped or relabeled.
    return f"vool-credential:{name}".encode()


def _chmod600(path) -> None:
    try:  # best-effort tighten perms on posix; Windows relies on the user profile ACL
        if os.name == "posix":
            path.chmod(0o600)
    except Exception:
        pass


def _load_raw(*, strict: bool | None = None) -> dict[str, dict[str, str]]:
    """The vault's entries. An ABSENT vault is empty. An existing vault that cannot be read or
    parsed, or is not a mapping, is empty only to lenient callers; a strict caller (``strict`` True,
    or None inside :func:`strict_reads`) gets :class:`CredentialReadError` — unreadable is not empty."""
    strict = _STRICT_READS.get() if strict is None else strict
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if strict:
            raise CredentialReadError(
                f"the credential vault exists but could not be read ({type(exc).__name__})"
            ) from exc
        return {}
    if isinstance(data, dict):
        return data
    if strict:
        raise CredentialReadError("the credential vault exists but is not a mapping")
    return {}


def _save_raw(raw: dict[str, dict[str, str]]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    _chmod600(path)


def _file_credential_fault(name: str) -> None:
    """File the typed fault for a vault entry that exists but cannot be decrypted.

    Best-effort and never raises: a diagnostics write must not change what the read
    returns. The credential NAME is the identity (it is not itself secret); the value
    and the failure's crypto detail never leave this function.
    """
    try:
        from core.faults.recorder import record_fault
        from core.faults.records import FaultRecord

        record_fault(
            FaultRecord.for_code(
                "credential_failure",
                authority="core.credential_store",
                dedupe=f"vault:{name}",
                context={
                    "credential_name": name,
                    "status": "undecryptable",
                    "reason": "vault entry failed authentication",
                },
            )
        )
    except Exception:
        pass


def _vault_decrypt(name: str, entry: dict) -> str | None:
    try:
        nonce = base64.b64decode(entry["nonce_b64"])
        ciphertext = base64.b64decode(entry["ct_b64"])
        return AESGCM(_key()).decrypt(nonce, ciphertext, _aad(str(name).strip())).decode("utf-8")
    except Exception as exc:
        # The entry EXISTS but its ciphertext does not authenticate: a read failure, not an
        # absent credential. Filed so the failure is observable instead of reading as "no key";
        # a strict read raises it instead of returning the same None as absence (review R4).
        _file_credential_fault(str(name).strip())
        if _STRICT_READS.get():
            raise CredentialReadError(f"the vault entry for {str(name).strip()} does not authenticate") from exc
        return None


def _vault_get(name: str) -> str | None:
    entry = _load_raw().get(str(name).strip())
    return _vault_decrypt(name, entry) if isinstance(entry, dict) else None


def _vault_store(name: str, value: str, label: str) -> None:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, str(value).encode("utf-8"), _aad(name))
    # Strict in every scope: rewriting the whole file from a vault that could not be read would
    # replace every credential it still holds with this one entry (review R4). Refuse instead.
    raw = _load_raw(strict=True)
    raw[name] = {
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ct_b64": base64.b64encode(ciphertext).decode("ascii"),
        "label": str(label or ""),
    }
    _save_raw(raw)


def _vault_drop(name: str) -> bool:
    raw = _load_raw()
    if name in raw:
        del raw[name]
        _save_raw(raw)
        return True
    return False


# --------------------------------------------------------------------------- keychain sidecar (non-secret)

def _meta_path():
    return data_path(_META_FILE)


def _meta_load() -> dict[str, dict[str, str]]:
    path = _meta_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _meta_save(meta: dict[str, dict[str, str]]) -> None:
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
    _chmod600(path)


def _meta_upsert(name: str, label: str) -> None:
    meta = _meta_load()
    current = meta.get(name) if isinstance(meta.get(name), dict) else {}
    meta[name] = {
        "label": str(label or current.get("label") or ""),
        "created": current.get("created") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _meta_save(meta)


def _meta_delete(name: str) -> bool:
    meta = _meta_load()
    if name in meta:
        del meta[name]
        _meta_save(meta)
        return True
    return False


# --------------------------------------------------------------------------- reinstall reconcile

# The Keychain has no "list accounts" API, so the sidecar index is what makes a stored credential
# VISIBLE. The index lives in the runtime data dir; the secret lives in the OS keychain. Wipe the
# runtime dir -- reinstall, "start fresh", a new machine profile -- and the two separate: the key is
# still there and still works, but has_credential()/list_credentials() both say it is missing, so the
# product tells the user no cloud key is configured while a working one sits in the keychain.
#
# Reproduced: after deleting the runtime home, get_credential("llm.cloud.openrouter") returned a
# 73-character value while list_credentials() returned [].
#
# VOOL's own credential slots are a small fixed set, so the index is rebuildable by probing for those
# exact names -- no enumeration API needed and nothing else in the keychain is touched.
_RECONCILE_NAMES: tuple[tuple[str, str], ...] = (
    ("llm.cloud.openrouter", "OpenRouter"),
    ("llm.cloud.cloudflare", "Cloudflare"),
    ("llm.cloud.generic", "Custom OpenAI-compatible"),
    ("llm.cloud.custom_base_url", "Custom base URL"),
)


def _search_reconcile_names() -> tuple[tuple[str, str], ...]:
    """Search-API slots to re-index, read from the provider table rather than duplicated here.

    `_RECONCILE_NAMES` above is a hand-maintained list, and a slot missing from it is invisible
    after a runtime-dir wipe even though the key is still sitting in the Keychain -- the user is
    told they have no key while macOS holds one. Deriving the search half from
    `core.search_providers` means adding a provider there cannot reintroduce that gap.
    """
    try:
        from core.search_providers import SEARCH_PROVIDERS

        return tuple((cfg.credential_slot, cfg.label) for cfg in SEARCH_PROVIDERS.values())
    except Exception:
        return ()


def _reconcile_index_with_keychain() -> None:
    """Re-index keychain credentials this runtime owns but has no sidecar entry for.

    Only ever ADDS index rows for names that really resolve in the keychain, so it cannot invent a
    credential; a name the user deleted stays deleted because its keychain entry is gone too.
    """
    keyring = _active_keyring()
    if keyring is None:
        return
    known = _meta_load()

    def _reconcile_all() -> None:
        for name, label in (*_RECONCILE_NAMES, *_search_reconcile_names()):
            if name in known:
                continue
            try:
                if keyring.get_password(_KC_SERVICE, name):
                    _meta_upsert(name, label)
            except Exception:
                continue  # a locked or unreadable keychain must never break startup

    # The whole pass is best-effort and bounded as ONE unit: a binary the Keychain has never
    # seen can block EVERY lookup on the GUI authorization dialog, and N names x a per-call
    # bound still exceeds a boot's readiness window. On timeout the shared process-wide
    # circuit breaker arms (bounded_keyring_call does that itself), so nothing in this
    # process retries the keyring; the sidecar index simply stays as loaded.
    try:
        bounded_keyring_call(_reconcile_all, what="reconcile index")
    except TimeoutError:
        print(
            "WARNING: credential index reconciliation skipped: keychain access did not complete "
            "in time (an authorization prompt is likely pending for this binary)",
            file=sys.stderr,
        )
    except Exception:
        pass  # best-effort by contract


# --------------------------------------------------------------------------- migration (vault -> keychain)

def _maybe_migrate() -> None:
    """One-time, safe move of existing vault secrets into the Keychain. Idempotent; loss-safe.

    For each vault entry: decrypt -> set_password -> READ-BACK VERIFY -> only then drop its
    ciphertext + record its (non-secret) metadata. An entry that can't be decrypted or whose
    Keychain write fails is left untouched in the vault, so no secret is ever lost; the marker is
    written only once the vault is fully drained.
    """
    keyring = _active_keyring(write=True)  # migration WRITES the keychain: grant required
    if keyring is None:
        return
    marker = data_path(_MIGRATION_MARKER)
    if marker.exists():
        return
    raw = _load_raw()
    if not raw:
        _write_marker(marker)
        return
    remaining = dict(raw)
    for name, entry in list(raw.items()):
        if not isinstance(entry, dict):
            remaining.pop(name, None)
            continue
        value = _vault_decrypt(name, entry)
        if value is None:
            continue  # undecryptable -> leave it, don't lose it
        try:
            bounded_keyring_call(lambda name=name, value=value: keyring.set_password(_KC_SERVICE, name, value), what=f"migrate {name}")
            readback = bounded_keyring_call(lambda name=name: keyring.get_password(_KC_SERVICE, name), what=f"migrate readback {name}")
        except Exception:
            readback = None
        if readback == value:  # verified in the Keychain -> safe to drop the ciphertext
            _meta_upsert(name, str(entry.get("label") or ""))
            remaining.pop(name, None)
    if remaining != raw:
        _save_raw(remaining)
    if not remaining:
        _write_marker(marker)


def _write_marker(marker) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
        _chmod600(marker)
    except Exception:
        pass


# --------------------------------------------------------------------------- public API (backend-agnostic)

def store_credential(name: str, value: str, *, label: str = "") -> None:
    """Store `value` under `name`. `label` is a non-secret human hint (e.g. 'OpenRouter key').

    When this is an explicit Keychain write (operator grant present, real backend active),
    the write goes through the ONE explicit secure-storage door: exactly one bounded call;
    a failure raises ``SecureStorageError`` typed with a recovery instruction — never a
    retry loop. Vault writes are local files and cannot prompt.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("credential name is required")
    from core.unattended_preflight import explicit_secure_storage

    keyring = _active_keyring(write=True)  # keychain WRITE: explicit operator grant required
    if keyring is not None:
        explicit_secure_storage(
            lambda: keyring.set_password(_KC_SERVICE, name, str(value)), what=f"set {name}"
        )
        _meta_upsert(name, label)
        _vault_drop(name)  # if a legacy ciphertext existed, the value now lives only in the Keychain
    else:
        _vault_store(name, value, label)


def get_credential(name: str) -> str | None:
    """Return the stored value, or None if absent / locked / undecryptable.

    Inside :func:`strict_reads` only a read that SUCCEEDED returns None; the unknown outcomes — an
    armed Keychain breaker, a keyring call that raised or timed out, an unreadable vault, an entry
    that does not authenticate — raise :class:`CredentialReadError` instead."""
    name = str(name or "").strip()
    if not name:
        return None
    _maybe_migrate()
    strict = _STRICT_READS.get()
    if strict and keychain_blocked() and _mode() != "vault":
        # The breaker short-circuits every keyring access in this process, so the Keychain half of
        # this read cannot happen: whatever the vault says, the state is unknown.
        raise CredentialReadError(f"credential {name} could not be read: the Keychain breaker is armed in this process")
    keyring = _active_keyring()
    if keyring is not None:
        try:
            value = bounded_keyring_call(lambda: keyring.get_password(_KC_SERVICE, name), what=f"get {name}")
        except Exception as exc:
            if strict:
                raise CredentialReadError(
                    f"credential {name} could not be read from the Keychain ({type(exc).__name__})"
                ) from exc
            value = None
        if value is not None:
            return value
        return _vault_get(name)  # not yet migrated / mixed state -> fall back to the vault
    return _vault_get(name)


def has_credential(name: str) -> bool:
    """True if a credential is stored under `name` (presence check, no value read)."""
    name = str(name or "").strip()
    if not name:
        return False
    _maybe_migrate()
    if _active_keyring() is not None:
        if name in _meta_load():
            return True
        # A wiped index must not report a live key as absent -- ask the keychain directly and
        # re-index on the way past, so the next caller sees it without another probe.
        _reconcile_index_with_keychain()
        if name in _meta_load():
            return True
    return name in _load_raw()


def list_credentials() -> list[dict[str, str]]:
    """Every stored credential's name + non-secret label. Never returns values."""
    _maybe_migrate()
    _reconcile_index_with_keychain()
    labels: dict[str, str] = {}
    if _active_keyring() is not None:
        for name, meta in _meta_load().items():
            if isinstance(meta, dict):
                labels[name] = str(meta.get("label") or "")
    for name, entry in _load_raw().items():  # any not-yet-migrated vault entries
        if isinstance(entry, dict):
            labels.setdefault(name, str(entry.get("label") or ""))
    return [{"name": name, "label": labels[name]} for name in sorted(labels)]


def delete_credential(name: str) -> bool:
    """Remove a credential from every backend; returns True if one was present anywhere."""
    name = str(name or "").strip()
    if not name:
        return False
    removed = False
    keyring = _active_keyring()
    if keyring is not None:
        # BOUNDED. This was a bare `keyring.delete_password(...)` with no timeout,
        # reached from an explicit operator action on two surfaces: the HTTP
        # credentials route and the chat command `cloud key forget`. macOS may show
        # an authorization dialog for a delete, and an unbounded wait wedges the
        # whole turn or the HTTP handler behind it -- measured: the call did not
        # return within 20s against a hanging backend.
        #
        # The amendment's law: an explicit operator deletion may request OS consent
        # ONCE, and every path must be bounded and typed. It goes through the same
        # one door `store_credential` uses, so the two halves of an explicit
        # credential action are bounded identically.
        from core.bounded_keyring import bounded_keyring_call
        from core.unattended_preflight import SecureStorageError, classify_secure_storage_failure

        try:
            bounded_keyring_call(
                lambda: keyring.delete_password(_KC_SERVICE, name), what=f"delete {name}"
            )
            removed = True
        except TimeoutError as exc:
            # A pending OS dialog is the one outcome that must NOT be swallowed: it
            # is neither "deleted" nor "absent", and reporting either would be a
            # false statement about the operator's own credential. Typed, and the
            # vault/meta drops below still run so it stops being usable from here.
            code, recovery = classify_secure_storage_failure(exc)
            raise SecureStorageError(
                code, f"secure storage 'delete {name}' did not complete: {exc}", recovery
            ) from exc
        except Exception:
            pass  # PasswordDeleteError when absent -> not an error for us
        if _meta_delete(name):
            removed = True
    if _vault_drop(name):
        removed = True
    return removed


def export_diagnostics() -> dict[str, object]:
    """A redacted snapshot for support/export: names, labels, backend, masked last-4 — NO values.

    Satisfies VOOL_MIGRATION.md §4 "export-diagnostics-with-secrets-redacted": every field is
    non-secret by construction, and the whole payload is run through ``redact_secrets`` as a final
    belt-and-suspenders pass so an accidental secret can never ride along.
    """
    backend = active_backend()
    meta = _meta_load()
    creds: list[dict[str, str]] = []
    for item in list_credentials():
        name = item["name"]
        value = get_credential(name)
        suffix = f"…{value[-4:]}" if value and len(value) >= 4 else ""
        creds.append({
            "name": name,
            "label": item["label"],
            "backend": backend,
            "masked_suffix": suffix,
            "created": str((meta.get(name) or {}).get("created") or ""),
        })
    payload = {
        "backend": backend,
        "storage_class": active_storage_class(),
        "count": len(creds),
        "credentials": creds,
    }
    try:
        from core.secret_redaction import redact_secrets

        return json.loads(redact_secrets(json.dumps(payload)))
    except Exception:
        return payload


__all__ = [
    "CredentialReadError",
    "active_backend",
    "delete_credential",
    "export_diagnostics",
    "get_credential",
    "has_credential",
    "list_credentials",
    "store_credential",
    "strict_reads",
]
