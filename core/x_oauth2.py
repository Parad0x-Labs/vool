"""X bookmark sign-in: loopback PKCE and the existing sealed credential store."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode, urlsplit

SLOT = 'x.oauth2.default'
AUTHORIZE_URL = 'https://x.com/i/oauth2/authorize'
API_ORIGIN = 'https://api.x.com'
TOKEN_URL = API_ORIGIN + '/2/oauth2/token'
SCOPES = frozenset({'tweet.read', 'users.read', 'bookmark.read', 'bookmark.write', 'offline.access'})
REQUIRED_SCOPES = SCOPES - {'offline.access'}
_lock = threading.RLock()
_flows: dict[str, dict[str, Any]] = {}
_epochs: dict[str, int] = {}


class OAuthFailure(Exception):
    """``uncertain``: the request reached X and its answer was lost or unusable, so X may have
    served (and billed) it. Such an outcome is never treated as if nothing happened."""

    def __init__(self, state: str, *, uncertain: bool = False):
        super().__init__(state)
        self.state = state
        self.uncertain = uncertain


def _profile() -> str:
    from core.runtime_paths import active_data_dir
    return str(active_data_dir())


def connected() -> bool:
    from core.credential_store import credential_is_indexed
    try:
        return credential_is_indexed(SLOT)
    except Exception:
        return False


def _load() -> dict[str, Any]:
    from core.credential_store import get_credential, strict_reads
    try:
        with strict_reads():
            raw = get_credential(SLOT)
        data = json.loads(raw) if raw else {}
    except Exception:
        raise OAuthFailure('credentials_unavailable') from None
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    from core.credential_store import store_credential
    try:
        store_credential(SLOT, json.dumps(data, sort_keys=True), label='X')
    except Exception:
        raise OAuthFailure('credentials_unavailable') from None


def account() -> dict[str, str]:
    data = _load()
    who = data.get('account')
    if not isinstance(who, dict) or not re.fullmatch(r'[0-9]{1,25}', str(who.get('id') or '')):
        raise OAuthFailure('needs_sign_in')
    if not REQUIRED_SCOPES.issubset(str(data.get('scope') or '').split()):
        raise OAuthFailure('needs_sign_in')
    return {'id': str(who['id']), 'username': str(who.get('username') or '')}


def begin(client_id: Any, origin: str) -> dict[str, Any]:
    client_id = str(client_id or '')
    if not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,512}', client_id):
        raise OAuthFailure('invalid_client')
    try:
        parsed = urlsplit(origin)
        allowed = parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}
        allowed = allowed and parsed.port is not None and not parsed.username and not parsed.password
        allowed = allowed and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment
    except ValueError:
        allowed = False
    if not allowed:
        raise OAuthFailure('invalid_loopback_origin')
    profile = _profile()
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    redirect_uri = origin.rstrip('/') + '/api/x/oauth/callback'
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    with _lock:
        _epochs[profile] = _epochs.get(profile, 0) + 1
        for key, old in list(_flows.items()):
            if old['profile'] == profile or time.monotonic() - old['started'] > 300:
                _flows.pop(key)
        if len(_flows) >= 32:
            raise OAuthFailure('busy')
        _flows[state] = {'client_id': client_id, 'verifier': verifier, 'redirect_uri': redirect_uri,
                         'started': time.monotonic(), 'profile': profile, 'epoch': _epochs[profile]}
    query = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri,
             'scope': ' '.join(sorted(SCOPES)), 'state': state, 'code_challenge': challenge,
             'code_challenge_method': 'S256'}
    return {'ok': True, 'authorize_url': AUTHORIZE_URL + '?' + urlencode(query)}


def _http(method: str, url: str, *, fields: dict[str, Any] | None = None,
          token: str = '', body: dict[str, Any] | None = None) -> dict[str, Any]:
    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import open_remote_url
    from core.secret_redaction import register_exact_secret

    headers = {'Accept': 'application/json'}
    data = None
    if fields is not None:
        data = urlencode(fields).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        for name in ('code', 'code_verifier', 'refresh_token'):
            if fields.get(name):
                register_exact_secret(str(fields[name]))
    if token:
        register_exact_secret(token)
        headers['Authorization'] = 'Bearer ' + token
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    import socket
    import urllib.error

    from core.remote_fetch_policy import RemoteFetchRefusedError

    try:
        with named_background_effect_scope('x.oauth2'):
            with open_remote_url(url, method=method, data=data, headers=headers, timeout=20,
                                 no_proxy=True, provider_id='x.api', keyed_or_keyless='keyed',
                                 credential_headers=('Authorization',), redirect_policy='refuse') as response:
                status = int(getattr(response, 'status', 0) or 0)
                raw = response.read(4 * 1024 * 1024 + 1)
    except RemoteFetchRefusedError:
        raise OAuthFailure('request_failed') from None  # refused locally: nothing left VOOL
    except urllib.error.HTTPError as error:
        # A definite refusal from X carries no result; anything else may have been served.
        refused = 400 <= int(error.code or 0) < 500 and error.code != 408
        raise OAuthFailure('request_failed', uncertain=not refused) from None
    except Exception as error:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        never_connected = isinstance(reason, (ConnectionRefusedError, socket.gaierror))
        raise OAuthFailure('request_failed', uncertain=not never_connected) from None
    try:
        if not 200 <= status < 300 or len(raw) > 4 * 1024 * 1024:
            raise ValueError('unexpected answer')
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('errors') or result.get('error'):
            raise ValueError('unexpected answer')
    except ValueError:
        # X answered, but not with something usable (cut off, malformed or an error body).
        raise OAuthFailure('request_failed', uncertain=True) from None
    return result


def _tokens(reply: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    previous = previous or {}
    token = reply.get('access_token')
    scope = reply.get('scope', previous.get('scope', ''))
    expires = reply.get('expires_in')
    if (not isinstance(token, str) or not token or len(token) > 8192
            or str(reply.get('token_type') or '').lower() != 'bearer'
            or not isinstance(scope, str) or not REQUIRED_SCOPES.issubset(scope.split())
            or isinstance(expires, bool) or not isinstance(expires, (int, float))
            or not math.isfinite(expires) or not 0 < expires <= 86400):
        raise OAuthFailure('invalid_token_response')
    refresh = reply.get('refresh_token', previous.get('refresh_token', ''))
    if not isinstance(refresh, str) or len(refresh) > 8192:
        raise OAuthFailure('invalid_token_response')
    return {'access_token': token, 'refresh_token': refresh, 'scope': scope,
            'expires_at': time.time() + expires, 'client_id': previous.get('client_id', '')}


def finish(*, state: str, code: str, denied: bool = False) -> None:
    profile = _profile()
    with _lock:
        flow = _flows.pop(str(state or ''), None)
    if (not flow or flow['profile'] != profile or time.monotonic() - flow['started'] > 300
            or denied or not code or len(code) > 8192):
        raise OAuthFailure('invalid_callback')
    reply = _http('POST', TOKEN_URL, fields={'grant_type': 'authorization_code', 'code': code,
        'redirect_uri': flow['redirect_uri'], 'client_id': flow['client_id'], 'code_verifier': flow['verifier']})
    saved = _tokens(reply, {'client_id': flow['client_id']})
    from core import x_usage
    refusal = x_usage.reserve_reads(1)
    if refusal:
        raise OAuthFailure('usage_limit_reached')
    try:
        x_usage.record_request('/2/users/me')
        try:
            who = _http('GET', API_ORIGIN + '/2/users/me', token=saved['access_token']).get('data')
        except OAuthFailure as failure:
            if failure.uncertain:
                # X may have served this read: it keeps counting against the monthly limit.
                x_usage.record_read(users=1)
            raise
        # X answered with a result: the read happened, whatever account it describes.
        x_usage.record_read(users=1)
        if (not isinstance(who, dict) or not re.fullmatch(r'[0-9]{1,25}', str(who.get('id') or ''))
                or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', str(who.get('username') or ''))):
            raise OAuthFailure('invalid_account')
    finally:
        x_usage.release_reads(1)
    saved['account'] = {'id': str(who['id']), 'username': str(who.get('username') or '')[:15]}
    with _lock:
        if _epochs.get(profile) != flow['epoch']:
            raise OAuthFailure('invalid_callback')
        _save(saved)


def access_token(expected_account: str) -> str:
    with _lock:
        saved = _load()
        if account()['id'] != expected_account:
            raise OAuthFailure('account_changed')
        expires = saved.get('expires_at')
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires):
            raise OAuthFailure('needs_sign_in')
        if expires <= time.time() + 15:
            refresh = saved.get('refresh_token')
            client_id = saved.get('client_id')
            if (not isinstance(refresh, str) or not 1 <= len(refresh) <= 8192
                    or not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{1,512}', client_id)):
                raise OAuthFailure('needs_sign_in')
            reply = _http('POST', TOKEN_URL, fields={'grant_type': 'refresh_token',
                'refresh_token': refresh, 'client_id': client_id})
            refreshed = _tokens(reply, saved)
            refreshed['account'] = saved['account']
            _save(refreshed)
            saved = refreshed
        token = saved.get('access_token')
        if not isinstance(token, str) or not token:
            raise OAuthFailure('needs_sign_in')
        return token


def disconnect() -> None:
    from core.credential_store import delete_credential
    with _lock:
        profile = _profile()
        _epochs[profile] = _epochs.get(profile, 0) + 1
        for state, flow in list(_flows.items()):
            if flow['profile'] == profile:
                _flows.pop(state)
        try:
            delete_credential(SLOT)
        except Exception:
            raise OAuthFailure('credentials_unavailable') from None


def handle(body: dict[str, Any], origin: str) -> tuple[int, dict[str, Any]]:
    try:
        action = body.get('action')
        if action == 'status':
            return 200, {'ok': True, 'connected': connected()}
        if action == 'begin':
            return 200, begin(body.get('client_id'), origin)
        if action == 'disconnect':
            disconnect()
            return 200, {'ok': True, 'connected': False}
        return 400, {'ok': False, 'state': 'invalid_action'}
    except OAuthFailure as error:
        return 400, {'ok': False, 'state': error.state}
