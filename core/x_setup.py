"""Guided X (Twitter) connection: save and check existing X developer keys, read-only.

Two ways in, both from Settings:

* **Public reads** -- the app's Bearer Token (developer portal → your app → Keys and tokens).
  Checked with one public profile read before it is saved.
* **Your own account** -- the four keys an X app shows (Consumer Key and Secret, Access Token and
  Secret). Checked with one signed ``GET /2/users/me``; the account X names is shown first and
  saved only after the user confirms it.

Everything lands in the existing X slot ``x.api.default`` (the one ``core.x_tools`` reads) as one
JSON object, so the parts can be added one at a time without dropping the other. Nothing here posts,
sends a DM or changes anything on X: the only requests are the two reads above, both through the
one outbound door (Local Only and the per-turn veto apply). A key is stored only after X accepted
it; a rejection, rate limit, timeout or outage stores nothing. No key value is ever returned.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

SLOT = "x.api.default"
LABEL = "X (Twitter)"
API_ORIGIN = "https://api.x.com"
PUBLIC_PROBE_PATH = "/2/users/by/username/XDevelopers"
ACCOUNT_PROBE_PATH = "/2/users/me"
FIELDS = ("api_key", "api_secret", "access_token", "access_token_secret")
LABELS = ("Consumer Key", "Consumer Key Secret", "Access Token", "Access Token Secret")
PENDING_TTL_S = 600.0
MAX_PENDING = 8
REQUEST_TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 256 * 1024

_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
_lock = threading.RLock()
_pending: dict[str, dict[str, Any]] = {}


class XSetupError(Exception):
    """A refused or failed step, with a state the Settings page names and a plain message."""

    def __init__(self, state: str, message: str, *, http_status: int | None = None):
        super().__init__(message)
        self.state, self.message, self.http_status = state, message, http_status


@dataclass(frozen=True, repr=False)
class OAuth1Keys:
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str

    def __repr__(self) -> str:
        return "<X OAuth1 keys: sealed>"


def _clean_token(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise XSetupError("missing", f"Add your {label}. Nothing was sent or saved.")
    value = value.strip()
    if len(value) > 4096 or any(ch.isspace() or not 33 <= ord(ch) <= 126 for ch in value):
        raise XSetupError("invalid", f"Copy the complete {label} without spaces or extra lines. Nothing was sent or saved.")
    return value


def oauth1_keys(values: object) -> OAuth1Keys:
    if not isinstance(values, dict) or set(values) - set(FIELDS):
        raise XSetupError("invalid", "Add the four X keys using the labelled fields. Nothing was sent or saved.")
    return OAuth1Keys(**{name: _clean_token(values.get(name), label) for name, label in zip(FIELDS, LABELS, strict=True)})


def _encode(value: str) -> str:
    return quote(str(value), safe="~-._")


def oauth1_authorization(method: str, url: str, keys: OAuth1Keys, *, nonce: str | None = None,
                         timestamp: int | None = None) -> str:
    """The OAuth 1.0a HMAC-SHA1 ``Authorization`` header for one request (RFC 5849 §3.4)."""
    parts = urlsplit(url)
    params = {
        "oauth_consumer_key": keys.api_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time()) if timestamp is None else timestamp),
        "oauth_token": keys.access_token,
        "oauth_version": "1.0",
    }
    pairs = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    normalized = "&".join(f"{k}={v}" for k, v in sorted((_encode(k), _encode(v)) for k, v in pairs))
    base_url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", ""))
    base = "&".join(_encode(v) for v in (method.upper(), base_url, normalized))
    signing_key = f"{_encode(keys.api_secret)}&{_encode(keys.access_token_secret)}"
    params["oauth_signature"] = base64.b64encode(
        hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_encode(k)}="{_encode(v)}"' for k, v in sorted(params.items()))


def _get(path: str, authorization: str, *, secrets_in_use: tuple[str, ...]) -> dict[str, Any]:
    """One read-only GET to the pinned X API origin. Returns the parsed object or raises XSetupError."""
    import urllib.error

    from core.credential_intelligence.verification import is_timeout_error
    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import CredentialRedirectRefusedError, RemoteFetchRefusedError, open_remote_url
    from core.secret_redaction import register_exact_secret

    for value in secrets_in_use:
        register_exact_secret(value)  # scrubbed from every ledger, log and error text
    try:
        with named_background_effect_scope("x.connection_check"):
            response = open_remote_url(
                API_ORIGIN + path, headers={"Authorization": authorization, "Accept": "application/json"},
                method="GET", timeout=REQUEST_TIMEOUT_S, no_proxy=True, provider_id="x.api",
                keyed_or_keyless="keyed", credential_headers=("Authorization",),
            )
            status = int(getattr(response, "status", None) or getattr(response, "code", None) or 0)
            raw = response.read(MAX_RESPONSE_BYTES + 1) or b""
    except RemoteFetchRefusedError:
        raise XSetupError("refused", "VOOL is not allowed to go online right now (for example Local Only is on), so X was not asked. Nothing was saved.") from None
    except CredentialRedirectRefusedError:
        raise XSetupError("redirected", "X answered with a redirect to another site; VOOL did not send your keys there. Nothing was saved.") from None
    except urllib.error.HTTPError as exc:
        raise _http_failure(int(exc.code or 0)) from None
    except Exception as exc:
        if is_timeout_error(exc):
            raise XSetupError("timeout", "X did not answer in time. Try again. Nothing was saved.") from None
        raise XSetupError("unreachable", "Could not reach X from this machine. Nothing was saved.") from None
    if not 200 <= status < 300:
        raise _http_failure(status)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise XSetupError("malformed_response", "X answered with more than a profile read returns. Nothing was saved.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise XSetupError("malformed_response", "X answered with something that is not an API response. Nothing was saved.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise XSetupError("unexpected_schema", "X answered, but not with the profile VOOL asked for. Nothing was saved.")
    return payload["data"]


def _http_failure(status: int) -> XSetupError:
    if status == 401:
        return XSetupError("unauthorized", "X rejected these keys. Copy them again from your X app's Keys and tokens page. Nothing was saved.", http_status=status)
    if status == 403:
        return XSetupError("plan_refused", "X recognised the keys but refused this read: your X app's plan or permissions do not allow it. Nothing was saved.", http_status=status)
    if status == 402:
        return XSetupError("exhausted", "X says this app has no API credits left. Nothing was saved.", http_status=status)
    if status == 429:
        return XSetupError("rate_limited", "X is limiting requests for this app right now. Try again later. Nothing was saved.", http_status=status)
    if status >= 500 or status == 408:
        return XSetupError("provider_unavailable", f"X is having trouble right now (HTTP {status}). Try again later. Nothing was saved.", http_status=status)
    return XSetupError("unconfirmed", f"X gave an unexpected answer (HTTP {status}). Nothing was saved.", http_status=status)


def _account_of(data: dict[str, Any]) -> dict[str, str]:
    handle, uid = str(data.get("username") or ""), str(data.get("id") or "")
    if not _HANDLE_RE.fullmatch(handle) or not uid.isdigit():
        raise XSetupError("unexpected_schema", "X answered without a valid account name. Nothing was saved.")
    return {"username": handle, "id": uid}


def check_bearer(token: str) -> None:
    _get(PUBLIC_PROBE_PATH, f"Bearer {token}", secrets_in_use=(token,))


def check_account(keys: OAuth1Keys) -> dict[str, str]:
    auth = oauth1_authorization("GET", API_ORIGIN + ACCOUNT_PROBE_PATH, keys)
    return _account_of(_get(ACCOUNT_PROBE_PATH, auth, secrets_in_use=tuple(getattr(keys, f) for f in FIELDS)))


def _read_saved() -> dict[str, Any]:
    from core.credential_store import CredentialReadError, get_credential, strict_reads

    try:
        with strict_reads():
            raw = get_credential(SLOT)
    except CredentialReadError:
        raise XSetupError("failed", "The saved X keys could not be read from this machine's key store.") from None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    return data if isinstance(data, dict) else {"bearer_token": str(raw).strip()}  # a bare token, as x_tools accepts


def _write_saved(data: dict[str, Any]) -> None:
    from core.credential_store import store_credential

    try:
        store_credential(SLOT, json.dumps(data, sort_keys=True), label=LABEL)
    except Exception:
        raise XSetupError("failed", "X accepted your keys, but VOOL could not save them securely. Check local storage and try again.") from None


def _saved_keys(saved: dict[str, Any]) -> OAuth1Keys | None:
    if not all(isinstance(saved.get(name), str) and saved.get(name) for name in FIELDS):
        return None
    return OAuth1Keys(**{name: saved[name] for name in FIELDS})


def status() -> dict[str, Any]:
    saved = _read_saved()
    account = saved.get("account") if isinstance(saved.get("account"), dict) else {}
    handle = str(account.get("username") or "")
    return {"ok": True, "public_reads": bool(str(saved.get("bearer_token") or "").strip()),
            "account": handle if _HANDLE_RE.fullmatch(handle) and _saved_keys(saved) else ""}


def save_bearer(token: object) -> dict[str, Any]:
    token = _clean_token(token, "Bearer Token")
    check_bearer(token)
    saved = _read_saved()
    saved["bearer_token"] = token
    _write_saved(saved)
    return {"ok": True, "message": "X accepted the Bearer Token and it is saved. VOOL can read public X data with it; it cannot post."}


def begin_account(values: object) -> dict[str, Any]:
    keys = oauth1_keys(values)
    account = check_account(keys)
    session_id = secrets.token_urlsafe(32)
    with _lock:
        now = time.monotonic()
        for old in [sid for sid, entry in _pending.items() if entry["deadline"] <= now]:
            _pending.pop(old, None)
        if len(_pending) >= MAX_PENDING:
            raise XSetupError("busy", "Too many unfinished X setups. Cancel one or wait ten minutes. Nothing was saved.")
        _pending[session_id] = {"keys": keys, "account": account, "deadline": now + PENDING_TTL_S}
    return {"ok": True, "session_id": session_id, "account": account["username"],
            "message": f"X says these keys belong to @{account['username']}. Save to finish; nothing is saved yet."}


def finish_account(session_id: object) -> dict[str, Any]:
    with _lock:
        entry = _pending.pop(session_id, None) if isinstance(session_id, str) else None
    if not entry or entry["deadline"] <= time.monotonic():
        raise XSetupError("expired", "This key check expired. Check your keys again; nothing was saved.")
    saved = _read_saved()
    saved.update({name: getattr(entry["keys"], name) for name in FIELDS})
    saved["account"] = entry["account"]
    _write_saved(saved)
    return {"ok": True, "account": entry["account"]["username"],
            "message": f"Connected as @{entry['account']['username']}. VOOL only reads with these keys; it does not post."}


def cancel_account(session_id: object) -> dict[str, Any]:
    with _lock:
        if isinstance(session_id, str):
            _pending.pop(session_id, None)
    return {"ok": True}


def test_saved() -> dict[str, Any]:
    """The Settings Test button: the same read-only check(s) Save used, for each saved part."""
    saved = _read_saved()
    token = str(saved.get("bearer_token") or "").strip()
    keys = _saved_keys(saved)
    if not token and keys is None:
        return {"provider": "x", "label": LABEL, "state": "no_key", "detail": "no key stored", "http_status": None}
    checks: list[tuple[str, Any]] = []
    if token:
        checks.append(("public reads", lambda: check_bearer(token)))
    if keys is not None:
        checks.append(("account", lambda: check_account(keys)))
    for part, check in checks:
        try:
            check()
        except XSetupError as exc:
            return {"provider": "x", "label": LABEL, "part": part, "state": exc.state,
                    "detail": exc.state, "http_status": exc.http_status}
    return {"provider": "x", "label": LABEL, "state": "ok", "detail": "ok", "http_status": None}


def remove() -> dict[str, Any]:
    from core.credential_store import delete_credential

    with _lock:
        _pending.clear()
    delete_credential(SLOT)
    return {"ok": True, "message": "The saved X keys were removed from this machine. Nothing was sent to X."}


_ACTIONS = {
    "status": (frozenset(), lambda body: status()),
    "save_bearer": (frozenset({"bearer_token"}), lambda body: save_bearer(body.get("bearer_token"))),
    "keys_check": (frozenset({"credentials"}), lambda body: begin_account(body.get("credentials"))),
    "keys_save": (frozenset({"session_id"}), lambda body: finish_account(body.get("session_id"))),
    "keys_cancel": (frozenset({"session_id"}), lambda body: cancel_account(body.get("session_id"))),
    "test": (frozenset(), lambda body: test_saved()),
    "remove": (frozenset(), lambda body: remove()),
}


def handle(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One Settings action → (HTTP status, payload). Payloads never carry key material."""
    action = body.get("action") if isinstance(body, dict) else None
    if action not in _ACTIONS:
        return 400, {"ok": False, "state": "invalid", "message": "Unknown X setup step."}
    allowed, run = _ACTIONS[action]
    extra = set(body) - {"action"} - allowed
    if extra:
        return 400, {"ok": False, "state": "invalid", "message": f"Unexpected fields: {sorted(extra)}"}
    try:
        return 200, run(body)
    except XSetupError as exc:
        return 400, {"ok": False, "state": exc.state, "message": exc.message, "http_status": exc.http_status}


__all__ = ["FIELDS", "LABELS", "SLOT", "OAuth1Keys", "XSetupError", "handle", "oauth1_authorization", "oauth1_keys"]
