"""One bounded, priced and receipted door for documented X calls.

Provider billing is not inferred from an HTTP success. Paid calls reserve their ceiling in
the existing spend ledger and remain billing-ambiguous until real reconciliation evidence.
This module never pays, tops up, creates wallet authority or retries a mutation.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit

from core import usage_quota, x_capabilities, x_private_calls
from core.runtime_paths import active_data_dir
from core.x_endpoint_catalog import InvalidXRequest, match, needs_approval, operation, prepare

_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar('x_request_context', default=None)
_QUOTA = 'x.api'
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_STREAM_RECORDS = 100
_SECRET_FIELDS = frozenset({'token', 'access_token', 'access_token_secret', 'refresh_token',
                            'bearer_token', 'api_key', 'api_secret', 'client_secret', 'password',
                            'conversation_token', 'media_hash_key', 'media_hash_keys'})


def _session(context: dict[str, Any]) -> str:
    return str(context.get('runtime_session_id') or context.get('chat_id') or context.get('session_id') or '')


@contextmanager
def chat_scope(context: dict[str, Any] | None):
    token = _CONTEXT.set(dict(context or {}))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def current_context() -> dict[str, Any] | None:
    return _CONTEXT.get()


def legacy_descriptor(method: str, path: str, query=None, body=None) -> dict[str, Any]:
    """Bind the existing SDK's fixed, validated routes to the same admission identity."""
    name = match(method, path)
    entry = operation(name)
    if entry['deferred']:
        raise InvalidXRequest("This X feature needs an extra X permission that VOOL's sign-in does not ask for. Nothing was sent.")
    return {'operation_id': name, 'method': method, 'path': path, 'query': dict(query or {}),
            'body': body, 'stream': False, 'content_type': 'application/json'}


@contextmanager
def _connection():
    path = active_data_dir() / 'x_call_receipts.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only, like the other private X files: created 0600 and kept so.
    os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
    os.chmod(path, 0o600)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('''CREATE TABLE IF NOT EXISTS x_calls (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, session_id TEXT NOT NULL,
        request_digest TEXT NOT NULL, ceiling_microusd INTEGER NOT NULL,
        reservation_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
        attempted INTEGER NOT NULL DEFAULT 0, http_status INTEGER,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _write(receipt: dict[str, Any]) -> None:
    with _connection() as connection:
        connection.execute('''INSERT INTO x_calls
            (id,operation_id,session_id,request_digest,ceiling_microusd,reservation_id,state,
             attempted,http_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET reservation_id=excluded.reservation_id,
            state=excluded.state,attempted=excluded.attempted,http_status=excluded.http_status,
            updated_at=excluded.updated_at''', tuple(receipt[key] for key in (
                'id', 'operation_id', 'session_id', 'request_digest', 'ceiling_microusd',
                'reservation_id', 'state', 'attempted', 'http_status', 'created_at', 'updated_at')))


def receipts(context: dict[str, Any], *, limit: int = 20) -> list[dict[str, Any]]:
    session = _session(context)
    if not session:
        return []
    with _connection() as connection:
        return [dict(row) for row in connection.execute(
            'SELECT * FROM x_calls WHERE session_id=? ORDER BY created_at DESC LIMIT ?',
            (session, max(1, min(100, int(limit)))))]


def _resolve_references(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict) and set(value) == {'credential_ref'}:
        ref = value['credential_ref']
        if not isinstance(ref, str) or not ref.startswith('x.response.'):
            raise InvalidXRequest('Only credentials returned by this chat’s X requests can be reused.')
        parts = ref.split('.')
        if len(parts) != 4 or not all(part.isalnum() for part in parts[2:]):
            raise InvalidXRequest('Invalid X result credential reference.')
        with _connection() as connection:
            row = connection.execute('SELECT session_id FROM x_calls WHERE id=?', (parts[2],)).fetchone()
        if not row or not _session(context) or row['session_id'] != _session(context):
            raise InvalidXRequest('This X result credential belongs to another chat.')
        from core.credential_store import get_credential

        raw = get_credential(ref)
        if not raw:
            raise InvalidXRequest('The saved X result credential is unavailable.')
        return json.loads(raw)
    if isinstance(value, dict):
        return {key: _resolve_references(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_references(item, context) for item in value]
    return value


def prepared_arguments(arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict) or set(arguments) - {'operation_id', 'path', 'query', 'body'}:
        raise InvalidXRequest('Use an operation name and its documented path, query and body fields.')
    name = arguments.get('operation_id')
    if not isinstance(name, str):
        raise InvalidXRequest('Choose an X operation.')
    x_capabilities.permitted(name)
    resolved = _resolve_references(arguments, context)
    return prepare(name, path=resolved.get('path'), query=resolved.get('query'), body=resolved.get('body'))


def _secret_reference(value: Any, receipt: dict[str, Any], path: str) -> dict[str, Any]:
    from core.credential_store import store_credential

    slot = 'x.response.' + receipt['id'] + '.' + hashlib.sha256(path.encode()).hexdigest()[:16]
    try:
        store_credential(slot, json.dumps(value), label='X result credential')
        return {'credential_ref': slot}
    except Exception:
        return {'credential_unavailable': True}


def _url_credentials(value: str) -> dict[str, str]:
    try:
        return {key: item for key, item in parse_qsl(urlsplit(value).query)
                if key.lower() in _SECRET_FIELDS | {'signature', 'sig', 'auth_token'} and item}
    except ValueError:
        return {}


def _register_result_secrets(value: Any) -> None:
    from core.secret_redaction import register_exact_secret

    def register(item):
        if isinstance(item, str):
            register_exact_secret(item)
        elif isinstance(item, list):
            for child in item:
                register(child)

    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in _SECRET_FIELDS:
                register(item)
            if key.lower().endswith('_url') and isinstance(item, str):
                for secret in _url_credentials(item).values():
                    register(secret)
            _register_result_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _register_result_secrets(item)


def _safe_result(value: Any, receipt: dict[str, Any], path: str = '') -> Any:
    from core.secret_redaction import redact_secrets

    if not path:
        # Register before visiting any sibling: a URL can precede its separate token field.
        _register_result_secrets(value)
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if key.lower() in _SECRET_FIELDS and item not in (None, ''):
                clean[key] = _secret_reference(item, receipt, path + key)
            elif key.lower().endswith('_url') and isinstance(item, str) and (secrets := _url_credentials(item)):
                clean[key] = {'url': redact_secrets(item, personal=False),
                              'query_credentials': {name: _secret_reference(secret, receipt, path + key + '.' + name)
                                                    for name, secret in secrets.items()}}
            else:
                clean[key] = _safe_result(item, receipt, path + key + '.')
        return clean
    if isinstance(value, list):
        return [_safe_result(item, receipt, path + str(i) + '.') for i, item in enumerate(value)]
    return redact_secrets(value, personal=False) if isinstance(value, str) else value


def safe_preview(prepared: dict[str, Any]) -> str:
    """The approval shows exact request fields, masking credential fields rather than storing them."""
    from core.secret_redaction import redact_secrets

    def hide(value):
        if isinstance(value, dict):
            return {key: ('[credential sha256:' + hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16] + ']')
                    if key.lower() in _SECRET_FIELDS else
                    ({'encoded_bytes': len(item), 'sha256': hashlib.sha256(item.encode()).hexdigest()}
                     if key == 'base64' and isinstance(item, str) else hide(item))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [hide(item) for item in value]
        return value
    text = json.dumps(hide(prepared), ensure_ascii=False, sort_keys=True, indent=2)
    text = ''.join(json.dumps(char, ensure_ascii=True)[1:-1] if unicodedata.category(char) == 'Cf' else char for char in text)
    return redact_secrets(text, personal=False)


def _never_connected(error: BaseException) -> bool:
    reason = error.reason if isinstance(error, URLError) else error
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


def perform(prepared: dict[str, Any], callback: Callable[[], Any], *,
            context: dict[str, Any], owner_local: bool | None = None,
            expected_policy: str = '', before_dispatch: Callable[[], None] | None = None) -> tuple[Any, dict[str, Any]]:
    """Admit once, call once, and retain a truthful financial outcome on every exit."""
    from core import model_spend_ledger
    from core.x_api import XReadError

    policy, ceiling = x_capabilities.permitted(prepared['operation_id'], expected_fingerprint=expected_policy)
    try:
        # Keyed, so a receipt can match equal requests but cannot be used to test guesses
        # about private request text.
        request_digest = x_private_calls.digest(prepared)
    except Exception:
        raise XReadError('storage_unavailable', 'VOOL could not open its private X records. Nothing was sent.') from None
    now = time.time()
    call_id = uuid.uuid4().hex
    receipt = {'id': call_id, 'operation_id': prepared['operation_id'], 'session_id': _session(context),
               'request_digest': request_digest,
               'ceiling_microusd': ceiling, 'reservation_id': '', 'state': 'preparing', 'attempted': 0,
               'http_status': None, 'created_at': now, 'updated_at': now}
    _write(receipt)
    quota_day = ''
    reserved = False
    dispatching = False
    try:
        if ceiling:
            from core.effect_budget_money import format_atomic
            from core.request_trust import request_is_owner_local
            from core.spend_authorization import require_spend_authorized

            local = request_is_owner_local(context) if owner_local is None else owner_local
            allowed, _reason = require_spend_authorized(
                asset='USD', amount=ceiling, recipient='x.api', owner_local=local,
                reason=x_capabilities.ui_message('xfull.spend_prompt', locale=str(context.get('ui_locale') or 'en'),
                                                 amount=format_atomic(ceiling, 6),
                                                 feature=x_capabilities.feature_label(prepared['operation_id'], str(context.get('ui_locale') or 'en'))))
            if not allowed:
                raise XReadError('spend_approval_required', 'The wallet did not authorize this X cost reservation. Nothing was sent.')
            limits = policy['limits']
            reservation = model_spend_ledger.reserve_spend(
                model_call_id=call_id,
                task_id=str(receipt['session_id'] or context.get('task_id') or call_id),
                subtask_id=prepared['operation_id'], model_id='x.api', maximum_usd=ceiling / 1_000_000,
                limits=model_spend_ledger.SpendLimits(
                    per_call_usd=limits['per_call_microusd'] / 1_000_000,
                    per_task_usd=limits['per_task_microusd'] / 1_000_000,
                    daily_usd=limits['daily_microusd'] / 1_000_000,
                    monthly_usd=limits['monthly_microusd'] / 1_000_000))
            reserved = True
            receipt['reservation_id'] = reservation.reservation_id
        if not usage_quota.store_readable():
            raise XReadError('quota_unreadable', 'The X daily call count cannot be read. Nothing was sent.')
        quota = usage_quota.consume_quota(_QUOTA, tier=usage_quota.active_tier())
        if not quota.allowed:
            raise XReadError('quota_exhausted', 'The daily X call limit is exhausted. Nothing was sent.')
        quota_day = quota.day
        # Recheck after wallet approval: disable, access removal or a changed price cancels admission.
        x_capabilities.permitted(prepared['operation_id'], expected_fingerprint=x_capabilities.fingerprint(policy))
        if before_dispatch is not None:
            before_dispatch()
        receipt.update(state='dispatching', attempted=1, updated_at=time.time())
        _write(receipt)
        dispatching = True
        outcome = callback()
        receipt.update(state='answered', updated_at=time.time())
        if reserved:
            model_spend_ledger.settle_spend(call_id, actual_usd=None, billing_ambiguous=True)
        _write(receipt)
        return outcome, receipt
    except BaseException as exc:
        from core.remote_fetch_policy import RemoteFetchRefusedError

        status = str(getattr(exc, 'status', ''))
        unsent = (not dispatching or _never_connected(exc) or
                  isinstance(exc, RemoteFetchRefusedError) or
                  status in {'needs_credentials', 'needs_account', 'usage_limit_reached', 'local_only',
                             'daily_limit_reached', 'quota_unavailable', 'refused'})
        receipt.update(state='unsent' if unsent else 'unknown', attempted=int(dispatching and not unsent), updated_at=time.time(),
                       http_status=getattr(exc, 'http_status', None))
        if reserved:
            if unsent:
                model_spend_ledger.release_spend(call_id, reason='proven_unsent')
            else:
                model_spend_ledger.settle_spend(call_id, actual_usd=None, billing_ambiguous=True)
        if unsent and quota_day:
            usage_quota.release_quota(_QUOTA, today=quota_day)
        _write(receipt)
        exc.x_receipt = dict(receipt)
        raise


def credential_mode(entry: dict[str, Any], saved: dict[str, Any]) -> str:
    """Which saved X credential a documented operation uses: the app Bearer Token, the four
    account keys (OAuth 1.0a), or the OAuth 2.0 sign-in, in that order of preference and only
    among the ones the operation accepts. Refuses before anything is sent."""
    from core import x_api
    from core.x_setup import FIELDS, XSetupError, oauth1_keys

    auth = entry['auth']
    if not auth:
        return ''
    if 'BearerToken' in auth and isinstance(saved.get('bearer_token'), str) and saved['bearer_token']:
        return 'bearer'
    if 'UserToken' in auth and all(saved.get(name) for name in FIELDS):
        try:
            oauth1_keys({name: saved[name] for name in FIELDS})
        except XSetupError:
            raise x_api.XReadError('needs_credentials', 'The saved X account keys are incomplete or invalid. Nothing was sent.') from None
        return 'oauth1'
    if 'OAuth2UserToken' in auth:
        from core import x_oauth2

        try:
            x_oauth2.account()
            return 'oauth2'
        except x_oauth2.OAuthFailure:
            pass
    raise x_api.XReadError('needs_credentials', 'Connect the X credentials this feature requires. Nothing was sent.')


def _oauth2_token() -> str:
    """A current OAuth 2.0 access token for the signed-in account, refreshed if needed. Called
    before admission, so a failed refresh is a refusal and never an uncertain X call."""
    from core import x_api, x_oauth2

    try:
        return x_oauth2.access_token(x_oauth2.account()['id'])
    except x_oauth2.OAuthFailure:
        raise x_api.XReadError('needs_credentials', 'Sign in to X again in Settings. Nothing was sent.') from None


def _send(prepared: dict[str, Any], saved: dict[str, Any], *, mode: str = '', oauth2_token: str = '') -> Any:
    from core import x_api, x_usage
    from core.remote_fetch_policy import open_remote_url
    from core.secret_redaction import register_exact_secret

    entry = operation(prepared['operation_id'])
    _register_result_secrets(prepared)
    url = x_api.API_ORIGIN + prepared['path']
    if prepared['query']:
        url += '?' + urlencode(prepared['query'])
    if mode == 'oauth2':
        if not oauth2_token:
            raise x_api.XReadError('needs_credentials', 'Sign in to X again in Settings. Nothing was sent.')
        authorization, secrets_in_use = 'Bearer ' + oauth2_token, (oauth2_token,)
    else:
        user_context = mode != 'bearer'
        authorization, secrets_in_use = (x_api._auth(saved, prepared['method'], url, user_context=user_context)
                                          if entry['auth'] else ('', ()))
    for secret in secrets_in_use:
        register_exact_secret(secret)
    headers = {'Accept': 'application/json', 'Content-Type': prepared['content_type']}
    if authorization:
        headers['Authorization'] = authorization
    body = prepared['body']
    if prepared['content_type'] == 'application/octet-stream' and body is not None:
        try:
            data = base64.b64decode(body['base64'], validate=True)
        except (ValueError, TypeError):
            raise InvalidXRequest('Invalid base64 upload.') from None
    else:
        data = json.dumps(body).encode() if body is not None else None
    read = prepared['method'] == 'GET'
    action_unsent = False
    read_settled = False  # the read's items were counted, or X provably served none
    post_day = ''
    if read and prepared['stream']:
        # Each streamed record is one answer of its own; the stream stops at MAX_STREAM_RECORDS.
        per_record = x_api._worst_case({k: v for k, v in prepared['query'].items() if k != 'max_results'})
        worst = MAX_STREAM_RECORDS * max(1, per_record)
    else:
        worst = max(1, x_api._worst_case(prepared['query'])) if read else 0
    refusal = x_usage.reserve_reads(worst) if read else x_usage.reserve_action()
    if refusal:
        raise x_api.XReadError('usage_limit_reached', refusal)
    try:
        if not read and prepared['operation_id'] == 'createPosts':
            if not usage_quota.store_readable():
                action_unsent = True
                raise x_api.XReadError('daily_limit_reached', 'The daily post count cannot be read. Nothing was sent.')
            day = usage_quota.consume_quota('x.post', tier=usage_quota.active_tier())
            if not day.allowed:
                action_unsent = True
                raise x_api.XReadError('daily_limit_reached', 'The daily post limit is exhausted. Nothing was sent.')
            post_day = day.day
        if read:
            x_usage.record_request(prepared['path'])
        try:
            with open_remote_url(url, method=prepared['method'], data=data, headers=headers,
                                 timeout=x_api.REQUEST_TIMEOUT_S, no_proxy=True, provider_id='x.api',
                                 keyed_or_keyless='keyed' if authorization else 'keyless',
                                 credential_headers=('Authorization',) if authorization else None,
                                 redirect_policy='refuse') as response:
                status = getattr(response, 'status', 200)
                if prepared['stream']:
                    started, records, size = time.monotonic(), [], 0
                    while len(records) < MAX_STREAM_RECORDS and time.monotonic() - started < x_api.REQUEST_TIMEOUT_S:
                        line = response.readline(MAX_RESPONSE_BYTES - size + 1)
                        if not line:
                            break
                        size += len(line)
                        if size > MAX_RESPONSE_BYTES:
                            raise x_api.XReadError('response_too_large', 'The X stream exceeded the response limit.')
                        if line.strip():
                            records.append(json.loads(line))
                    payload = {'records': records, 'bounded_stream': True}
                else:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise x_api.XReadError('response_too_large', 'X returned more than the response limit.')
                    content_type = str(response.headers.get('Content-Type', 'application/json'))
                    if raw and 'json' not in content_type.lower():
                        payload = {'content_type': content_type, 'base64': base64.b64encode(raw).decode('ascii')}
                    else:
                        try:
                            payload = json.loads(raw) if raw else {}
                        except ValueError:
                            raise x_api.XReadError('malformed_response', 'X did not return a valid response.') from None
        except HTTPError as exc:
            action_unsent = 400 <= exc.code < 500 and exc.code != 408
            read_settled = action_unsent  # a definite refusal carries no items
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
            try:
                error = json.loads(raw) if len(raw) <= MAX_RESPONSE_BYTES else {}
            except ValueError:
                error = {}
            raise x_api._http_failure(exc.code, error) from None
        if not 200 <= status < 300:
            read_settled = 400 <= status < 500 and status != 408
            raise x_api._http_failure(status, payload if isinstance(payload, dict) else {})
        if isinstance(payload, dict) and payload.get('errors') and not payload.get('data') and not payload.get('meta'):
            read_settled = True  # X answered with an error and no items
            raise x_api.XReadError('provider_error', 'X returned an error instead of a result.', http_status=status)
        if read:
            sources = payload['records'] if prepared['stream'] else [payload]
            sources = [source for source in sources if isinstance(source, dict)]
            x_usage.record_read(posts=sum(x_api._count_posts(source) for source in sources),
                                users=sum(x_api._count_users(source) for source in sources),
                                other=sum(x_api._count_other(source) for source in sources))
            read_settled = True
        return payload
    except BaseException as exc:
        from core.remote_fetch_policy import CredentialRedirectRefusedError, RemoteFetchRefusedError

        action_unsent = action_unsent or _never_connected(exc) or isinstance(
            exc, (RemoteFetchRefusedError, usage_quota.QuotaUnavailable))
        # A redirect is X's whole answer to a read: it carries no items.
        read_settled = read_settled or action_unsent or isinstance(exc, CredentialRedirectRefusedError)
        raise
    finally:
        if read:
            try:
                if not read_settled:
                    # The read went out and its answer was lost, cut off or unusable: X may have
                    # served everything it held, so the held amount counts.
                    x_usage.record_uncertain_read(worst)
            finally:
                x_usage.release_reads(worst)
        elif action_unsent:
            # A definite provider rejection has no successful action, while the request quota
            # and any uncertain billed cost remain accounted for by the outer admission.
            x_usage.unreserve_action()
            if post_day:
                usage_quota.release_quota('x.post', today=post_day)


def execute(prepared: dict[str, Any], context: dict[str, Any], *,
            settings_approved: bool = False, expected_policy: str = '', expected_account: str = ''):
    from core import x_actions, x_api
    from core.effect_gateway import named_background_effect_scope
    from core.secret_redaction import redact_secrets

    try:
        entry = operation(prepared['operation_id'])
    except InvalidXRequest as exc:
        return x_api.XResult(False, 'invalid_request', str(exc))
    if needs_approval(entry) and not settings_approved:
        return x_api.XResult(False, 'approval_required', 'This X action must be proposed and approved in Settings.')
    try:
        x_capabilities.permitted(prepared['operation_id'], expected_fingerprint=expected_policy)
        saved = x_api._saved() if entry['auth'] else {}
        mode = credential_mode(entry, saved)
        oauth2_token = _oauth2_token() if mode == 'oauth2' else ''
        account_fingerprint = expected_account or (x_actions._api_account_fingerprint(saved) if entry['auth'] else '')

        def unchanged_account():
            if account_fingerprint and x_actions._api_account_fingerprint(x_api._saved()) != account_fingerprint:
                raise x_api.XReadError('account_changed', 'The X credentials changed during approval. Nothing was sent.')

        if settings_approved:
            with named_background_effect_scope('x.approved_api'):
                result, receipt = perform(prepared, lambda: _send(prepared, saved, mode=mode, oauth2_token=oauth2_token),
                                          context=context, owner_local=True, expected_policy=expected_policy,
                                          before_dispatch=unchanged_account)
        else:
            result, receipt = perform(prepared, lambda: _send(prepared, saved, mode=mode, oauth2_token=oauth2_token),
                                      context=context, expected_policy=expected_policy, before_dispatch=unchanged_account)
        partial = isinstance(result, dict) and bool(result.get('errors'))
        return x_api.XResult(not partial, 'partial_result' if partial else 'executed',
                             'X returned a partial result. Do not repeat a mutation; review the returned data and receipt.' if partial else
                             'X answered. Any paid reservation stays held until billing is reconciled.',
                             {'result': _safe_result(result, receipt), 'receipt': receipt})
    except (InvalidXRequest, x_capabilities.XCapabilityRefused, x_api.XReadError) as exc:
        return x_api.XResult(False, getattr(exc, 'status', 'invalid_request'), redact_secrets(str(exc), personal=False),
                             {'receipt': getattr(exc, 'x_receipt', None)})
    except PermissionError as exc:
        return x_api.XResult(False, 'spend_cap_exhausted', 'The X cost reservation exceeds an existing spending cap. Nothing was sent.',
                             {'receipt': getattr(exc, 'x_receipt', None)})
    except Exception as exc:
        return x_api.XResult(False, 'outcome_unknown', 'The X request did not finish. It will not be repeated automatically; check its receipt.',
                             {'receipt': getattr(exc, 'x_receipt', None)})
