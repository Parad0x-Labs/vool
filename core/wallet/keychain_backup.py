"""A wallet's backup key, sealed with a password the person chooses and kept in the computer's own secure store.

Writing a key down is the step new people skip. This keeps the same backup key VOOL would show once, but encrypted,
in the macOS Keychain (the Secret Service keyring on Linux), so VOOL can bring the wallet back after a forgotten PIN or
a reinstall. Writing it down stays offered beside it.

How a stolen copy stays closed:

* The key is sealed with AES-256-GCM under a key stretched from the person's backup password with scrypt
  (N=2^17, r=8, p=1: about 128 MB and a noticeable fraction of a second per guess), with a random salt and nonce per
  backup. The network and address are bound in as associated data, so a sealed copy cannot be passed off as another
  wallet's.
* The password is never stored, logged or sent anywhere; it must be at least :data:`MIN_PASSWORD` characters, not
  only digits, and not the wallet's PIN. No machine secret is mixed in on purpose: a copy that travels with the
  person's Keychain to a new Mac still opens with their password, and with nothing else.
* The secure store itself is locked by the operating system account on top of that.
* Opening it here goes through the wallet's own recovery throttle, so this computer is not a fast guessing oracle.

Saving needs the wallet's own PIN or password (the same proof as showing its backup). The money rule is untouched:
restoring only re-seals the same key under a new PIN; it signs and sends nothing.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import time
from typing import Any

from core.wallet import chains, custody
from core.wallet.security import wallet_fault
from core.wallet.store import connection, utcnow

AUTHORITY = "core.wallet.keychain_backup"
SERVICE = "VOOL wallet backup"
INDEX_ACCOUNT = "index"
VERSION = 1
MIN_PASSWORD = 12
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
_AAD_LABEL = "vool-wallet-keychain-backup-v1"


def _fault(reason: str, *, code: str = "wallet_setup_state_invalid", source_context: dict[str, Any] | None = None, **context: Any) -> Exception:
    return wallet_fault(code, authority=AUTHORITY, context={"reason": reason, **context}, source_context=source_context)


# --- the secure store --------------------------------------------------------------------------------------

def _backend():
    try:
        import keyring  # type: ignore
    except Exception:
        return None
    try:
        current = keyring.get_keyring()
    except Exception:
        return None
    if float(getattr(current, "priority", 0) or 0) <= 0 or type(current).__module__.startswith("keyring.backends.fail"):
        return None
    return current


def available() -> bool:
    """Whether this computer has a secure store VOOL can write to (Keychain, Secret Service)."""
    from core.bounded_keyring import keychain_blocked

    return _backend() is not None and not keychain_blocked()


def _call(what: str, fn):
    from core.bounded_keyring import bounded_keyring_call, keychain_blocked

    if _backend() is None or keychain_blocked():
        raise _fault("secure_store_unavailable")
    try:
        return bounded_keyring_call(fn, what=what)
    except Exception:
        raise _fault("secure_store_unavailable") from None


def _account(network: str, address: str) -> str:
    return f"{chains.resolve_network(network).network}|{address}"


def _read_index() -> list[dict[str, str]]:
    backend = _backend()
    raw = _call("wallet backup index read", lambda: backend.get_password(SERVICE, INDEX_ACCOUNT)) or "[]"
    try:
        listed = json.loads(raw)
    except Exception:
        return []
    return [entry for entry in listed if isinstance(entry, dict) and entry.get("account")] if isinstance(listed, list) else []


def _write_index(entries: list[dict[str, str]]) -> None:
    backend = _backend()
    payload = json.dumps(entries, sort_keys=True)
    _call("wallet backup index write", lambda: backend.set_password(SERVICE, INDEX_ACCOUNT, payload))


# --- sealing ------------------------------------------------------------------------------------------------

def _stretch(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password.encode("utf-8"))


def _aad(network: str, address: str) -> bytes:
    return f"{_AAD_LABEL}|{network}|{address}".encode()


def seal(backup_value: str, *, password: str, network: str, address: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    key = _stretch(password, salt)
    try:
        sealed = AESGCM(key).encrypt(nonce, backup_value.encode("utf-8"), _aad(network, address))
    finally:
        del key
    b64 = lambda data: base64.b64encode(data).decode("ascii")  # noqa: E731
    return json.dumps({"v": VERSION, "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": b64(salt), "nonce": b64(nonce),
                       "sealed": b64(sealed), "network": network, "address": address}, sort_keys=True)


def unseal(envelope: str, *, password: str, network: str, address: str) -> str | None:
    """The backup key, or None when the password is wrong or the copy was changed or belongs to another wallet."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    try:
        data = json.loads(envelope)
        if int(data["v"]) != VERSION or data["kdf"] != "scrypt" or (int(data["n"]), int(data["r"]), int(data["p"])) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return None
        salt, nonce, sealed = (base64.b64decode(data[name]) for name in ("salt", "nonce", "sealed"))
    except Exception:
        return None
    key = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(password.encode("utf-8"))
    try:
        return AESGCM(key).decrypt(nonce, sealed, _aad(network, address)).decode("utf-8")
    except (InvalidTag, ValueError):
        return None
    finally:
        del key


def _check_password(password: str, confirmation: str, *, credential: str, source_context: dict[str, Any] | None) -> None:
    if password != confirmation:
        raise _fault("backup_password_mismatch", source_context=source_context)
    if len(password) < MIN_PASSWORD or password.isdigit():
        raise _fault("backup_password_weak", source_context=source_context, min_length=MIN_PASSWORD)
    if credential and password == credential:
        raise _fault("backup_password_is_the_pin", source_context=source_context)


# --- the person's doors ---------------------------------------------------------------------------------------

def _owner_pilot_row(wallet_id: str, *, source_context: dict[str, Any] | None) -> dict[str, Any]:
    from core.wallet import pilot_custody

    if custody.agent_of(wallet_id):
        raise _fault("agent_wallet_not_backed_up_here", source_context=source_context)
    row = pilot_custody._require_pilot_row(wallet_id, source_context=source_context)
    if str(row.get("setup_state") or "") not in ("ready", "backup_revealed", "awaiting_backup"):
        raise _fault("setup_not_finished", source_context=source_context)
    return row


def save(wallet_id: str, *, credential: str = "", password: str, confirmation: str, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seal this wallet's backup key with ``password`` and keep it in the secure store. Proven with the wallet's own
    PIN or password. Saving again replaces the earlier copy (a new password from then on)."""
    from core.secret_redaction import register_exact_secret
    from core.wallet import pilot_custody

    custody.require_enabled(source_context=source_context)
    row = _owner_pilot_row(wallet_id, source_context=source_context)
    _check_password(str(password or ""), str(confirmation or ""), credential=str(credential or ""), source_context=source_context)
    if not available():
        raise _fault("secure_store_unavailable", source_context=source_context)
    spec = chains.resolve_network(row["network"])
    secret, _generation = pilot_custody._verify(row, str(credential or ""), source_context=source_context)
    try:
        backup_value, backup_format = pilot_custody._backup_for(spec, secret, row["public_key"])
    finally:
        del secret
    register_exact_secret(backup_value)
    envelope = seal(backup_value, password=str(password), network=spec.network, address=row["public_key"])
    del backup_value
    account = _account(spec.network, row["public_key"])
    backend = _backend()
    _call("wallet backup write", lambda: backend.set_password(SERVICE, account, envelope))
    if unseal(_call("wallet backup readback", lambda: backend.get_password(SERVICE, account)) or "", password=str(password), network=spec.network,
              address=row["public_key"]) is None:
        raise _fault("secure_store_readback_failed", source_context=source_context)
    entries = [entry for entry in _read_index() if entry.get("account") != account]
    entries.append({"account": account, "network": spec.network, "address": row["public_key"], "label": str(row.get("label") or "")[:40],
                    "format": backup_format, "saved_at": utcnow()})
    _write_index(entries)
    with connection() as conn:
        conn.execute("INSERT INTO wallet_keychain_backups (wallet_id, network, address, saved_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(wallet_id) DO UPDATE SET saved_at = excluded.saved_at", (row["wallet_id"], spec.network, row["public_key"], utcnow()))
    from core.wallet import receipts

    receipts.journal_security_event("wallet_keychain_backup_saved", {"wallet_id": row["wallet_id"], "network": spec.network}, source_context=source_context)
    return {"wallet_id": row["wallet_id"], "saved": True, "store": store_name()}


def saved_for(wallet_id: str, *, conn: Any = None) -> bool:
    def read(c: Any) -> bool:
        return c.execute("SELECT 1 FROM wallet_keychain_backups WHERE wallet_id = ?", (str(wallet_id),)).fetchone() is not None

    if conn is not None:
        return read(conn)
    with connection() as c:
        return read(c)


def _envelope_for(network: str, address: str, *, source_context: dict[str, Any] | None) -> str:
    backend = _backend()
    account = _account(network, address)
    found = _call("wallet backup read", lambda: backend.get_password(SERVICE, account))
    if not found:
        raise _fault("no_backup_in_secure_store", source_context=source_context)
    return str(found)


def recover(wallet_id: str, *, password: str, method: str, credential: str = "", credential_confirmation: str = "",
            source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Forgot the PIN: open the Keychain copy with the backup password and seal the same key under a new PIN or
    password. A wrong backup password counts against the wallet's recovery throttle."""
    from core.wallet import pilot_custody

    row = _owner_pilot_row(wallet_id, source_context=source_context)
    spec = chains.resolve_network(row["network"])
    envelope = _envelope_for(spec.network, row["public_key"], source_context=source_context)
    scopes = pilot_custody._recovery_scopes(row["wallet_id"])
    pilot_custody._reserve_attempt(row["wallet_id"], source_context=source_context, scopes=scopes)
    backup_value = unseal(envelope, password=str(password or ""), network=spec.network, address=row["public_key"])
    if backup_value is None:
        pilot_custody._settle_attempt(row["wallet_id"], "failure", scopes=scopes)
        raise _fault("backup_password_wrong", code="wallet_recovery_refused", source_context=source_context)
    pilot_custody._settle_attempt(row["wallet_id"], "refund", scopes=scopes)
    try:
        return pilot_custody.recover_with_backup(row["wallet_id"], backup_value=backup_value, method=method, credential=credential,
                                                 credential_confirmation=credential_confirmation, source_context=source_context)
    finally:
        del backup_value


def restorable() -> list[dict[str, Any]]:
    """Backups in the secure store whose wallet is not on this computer now (deleted, or a new install)."""
    if not available():
        return []
    try:
        entries = _read_index()
    except Exception:
        return []
    present = {(chains.resolve_network(e["network"]).network, e["public_key"]) for e in custody.list_wallets() if e.get("public_key")}
    shown = []
    for entry in entries:
        try:
            key = (chains.resolve_network(entry["network"]).network, entry["address"])
        except Exception:
            continue
        if key not in present:
            shown.append({"network": key[0], "address": key[1], "label": entry.get("label", ""), "saved_at": entry.get("saved_at", ""),
                          "can_restore": chains.resolve_network(key[0]).is_svm})
    return shown


def restore(network: str, address: str, *, password: str, method: str, credential: str = "", credential_confirmation: str = "", label: str = "",
            source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bring a wallet back from its Keychain copy onto this computer (after deleting it, or on a new install). Solana
    wallets today; it is imported as an everyday wallet under a new PIN or password, and never takes Main."""

    custody.require_enabled(source_context=source_context)
    spec = chains.resolve_network(network)
    removed = _removed_row(spec.network, address)
    if not spec.is_svm and removed is None:
        raise _fault("restore_not_supported_on_this_network", source_context=source_context)
    envelope = _envelope_for(spec.network, address, source_context=source_context)
    view = restore_from_envelope(envelope, spec, address, removed=removed, password=password, method=method, credential=credential,
                                 credential_confirmation=credential_confirmation, label=label, scope_prefix="keychain",
                                 creation_key=f"keychain-restore:{address}", source_context=source_context)
    with connection() as conn:
        conn.execute("INSERT INTO wallet_keychain_backups (wallet_id, network, address, saved_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(wallet_id) DO UPDATE SET saved_at = excluded.saved_at", (view["wallet_id"], spec.network, address, utcnow()))
    return view


def restore_from_envelope(envelope: str, spec: chains.ChainIdentity, address: str, *, removed: dict[str, Any] | None, password: str, method: str,
                          credential: str, credential_confirmation: str, label: str, scope_prefix: str, creation_key: str,
                          source_context: dict[str, Any] | None) -> dict[str, Any]:
    """Open a sealed backup copy (Keychain or a USB stick) through the recovery throttle and bring its wallet back:
    the deleted row comes back with its history, or a Solana wallet is imported as an everyday wallet. Never Main."""
    from core.wallet import pilot_custody

    scope = f"{scope_prefix}:{spec.network}:{address}"
    scopes = ((scope, pilot_custody.WALLET_LOCK_AFTER), (pilot_custody.RECOVERY_APP_SCOPE, pilot_custody.APP_LOCK_AFTER))
    pilot_custody._reserve_attempt(scope, source_context=source_context, scopes=scopes)
    backup_value = unseal(envelope, password=str(password or ""), network=spec.network, address=address)
    if backup_value is None:
        pilot_custody._settle_attempt(scope, "failure", scopes=scopes)
        raise _fault("backup_password_wrong", code="wallet_recovery_refused", source_context=source_context)
    pilot_custody._settle_attempt(scope, "refund", scopes=scopes)
    if removed is not None:
        # the person deleted this wallet here earlier: the same row comes back, with its name and its history
        try:
            return _revive(removed, spec, backup_value, method=method, credential=credential, credential_confirmation=credential_confirmation,
                           source_context=source_context)
        finally:
            del backup_value
    if not spec.is_svm:
        del backup_value
        raise _fault("restore_not_supported_on_this_network", source_context=source_context)
    try:
        return pilot_custody.import_solana_pilot_wallet(network=spec.network, backup_value=backup_value, expected_address=address, method=method, credential=credential,
                                                        credential_confirmation=credential_confirmation, creation_key=creation_key,
                                                        backup_saved=True, label=label or "Restored", source_context=source_context)
    finally:
        del backup_value


def _removed_row(network: str, address: str) -> dict[str, Any] | None:
    from core.wallet import pilot_custody, wallet_roles

    with connection() as conn:
        rows = conn.execute("SELECT wallet_id, network FROM wallet_profiles WHERE public_key = ? AND setup_state = ? AND COALESCE(agent_id, '') = ''",
                            (str(address), wallet_roles.STATE_REMOVED)).fetchall()
    for wallet_id, row_network in rows:
        try:
            if chains.resolve_network(str(row_network)).network == network:
                return pilot_custody._load(str(wallet_id))
        except Exception:
            continue
    return None


def _revive(row: dict[str, Any], spec: chains.ChainIdentity, backup_value: str, *, method: str, credential: str, credential_confirmation: str,
            source_context: dict[str, Any] | None) -> dict[str, Any]:
    from core.wallet import limits, pilot_custody, wallet_roles

    chosen = pilot_custody._require_new_credential(method, credential, credential_confirmation, source_context=source_context)
    secret, decoded = pilot_custody._decode_backup(spec, backup_value, source_context=source_context)
    try:
        if not pilot_custody._same_address(spec, decoded, row["public_key"]):
            raise _fault("backup_for_another_wallet", code="wallet_recovery_refused", source_context=source_context)
        sealed_blob, device_blob = pilot_custody._staged_envelope(spec, row["wallet_id"], secret, row["public_key"], chosen, credential, source_context=source_context)
    finally:
        del secret
    view = pilot_custody._commit_envelope(row, sealed_blob=sealed_blob, device_blob=device_blob, method=chosen, expected_generation=pilot_custody._generation(row),
                                          event="wallet_restored_from_secure_store", source_context=source_context)
    with connection() as conn:
        limits._begin_immediate(conn)
        wallet_roles.freeze(conn)
    return view


def forget(wallet_id: str, *, source_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Remove this wallet's copy from the secure store. Loosens nothing, so it needs no credential."""
    with connection() as conn:
        found = conn.execute("SELECT network, address FROM wallet_keychain_backups WHERE wallet_id = ?", (str(wallet_id),)).fetchone()
    if found is None:
        raise _fault("no_backup_in_secure_store", source_context=source_context)
    account = _account(str(found[0]), str(found[1]))
    backend = _backend()
    with contextlib.suppress(Exception):  # already gone from the store: the record below is what the page shows
        _call("wallet backup delete", lambda: backend.delete_password(SERVICE, account))
    _write_index([entry for entry in _read_index() if entry.get("account") != account])
    with connection() as conn:
        conn.execute("DELETE FROM wallet_keychain_backups WHERE wallet_id = ?", (str(wallet_id),))
    return {"wallet_id": str(wallet_id), "forgotten": True}


def store_name() -> str:
    import sys

    return "Keychain" if sys.platform == "darwin" else "the system keyring"


def overview() -> dict[str, Any]:
    return {"available": available(), "store": store_name(), "min_password": MIN_PASSWORD, "restorable": restorable(), "checked_at": time.time()}


__all__ = ["MIN_PASSWORD", "SERVICE", "available", "forget", "overview", "recover", "restorable", "restore", "restore_from_envelope", "save", "saved_for", "seal",
           "unseal"]
