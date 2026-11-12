"""Explicit owner configuration for documented X features; no network or key material."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import unicodedata
from copy import deepcopy
from typing import Any

from core.runtime_paths import active_config_home_dir
from core.x_endpoint_catalog import InvalidXRequest, operation, source_digest

_FILE = 'x_capabilities.json'
_LIMITS = ('per_call_microusd', 'per_task_microusd', 'daily_microusd', 'monthly_microusd')
MAX_MICROUSD = 1_000_000_000_000


class XCapabilityRefused(ValueError):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


def defaults() -> dict[str, Any]:
    return {'version': 1, 'enabled': False, 'access_label': '', 'operations': {},
            'limits': dict.fromkeys(_LIMITS, 0)}


def _amount(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_MICROUSD:
        raise XCapabilityRefused('invalid_configuration', 'Use a non-negative whole number of millionths of a dollar.')
    return value


def validate_policy(value: Any) -> dict[str, Any]:
    expected = set(defaults())
    if not isinstance(value, dict) or set(value) != expected or type(value.get('enabled')) is not bool:
        raise XCapabilityRefused('invalid_configuration', 'The X feature configuration is incomplete.')
    if value.get('version') != 1 or isinstance(value.get('version'), bool):
        raise XCapabilityRefused('invalid_configuration', 'This X feature configuration version is not supported.')
    label = value.get('access_label')
    if not isinstance(label, str) or len(label) > 80 or any(ord(ch) < 32 or unicodedata.category(ch) == 'Cf' for ch in label):
        raise XCapabilityRefused('invalid_configuration', 'Use a short name for your X access profile.')
    allowed = value.get('operations')
    limits = value.get('limits')
    if not isinstance(allowed, dict) or not isinstance(limits, dict) or set(limits) != set(_LIMITS):
        raise XCapabilityRefused('invalid_configuration', 'Choose X features and their spending limits.')
    cleaned_limits = {key: _amount(limits[key]) for key in _LIMITS}
    cleaned = {}
    for name, amount in allowed.items():
        try:
            entry = operation(name)
        except InvalidXRequest as exc:
            raise XCapabilityRefused('invalid_configuration', str(exc)) from None
        if entry['deferred']:
            raise XCapabilityRefused('needs_x_permission', "This X feature needs an extra X permission that VOOL's sign-in does not ask for. It cannot be turned on yet.")
        amount = _amount(amount)
        if amount > cleaned_limits['per_call_microusd']:
            raise XCapabilityRefused('invalid_configuration', 'A feature reservation exceeds the per-call limit.')
        cleaned[name] = amount
    if value['enabled'] and (not label.strip() or not cleaned):
        raise XCapabilityRefused('invalid_configuration', 'Name your X access profile and choose at least one feature.')
    if any(cleaned.values()) and any(cleaned_limits[key] <= 0 for key in _LIMITS):
        raise XCapabilityRefused('invalid_configuration', 'Paid X features need positive per-call, per-chat, daily and monthly limits.')
    return {'version': 1, 'enabled': value['enabled'], 'access_label': label.strip(),
            'operations': dict(sorted(cleaned.items())), 'limits': cleaned_limits}


def _path():
    return active_config_home_dir() / _FILE


def load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return defaults()
    try:
        if path.stat().st_size > 128 * 1024 or path.is_symlink():
            raise ValueError('invalid policy file')
        return validate_policy(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError):
        raise XCapabilityRefused('configuration_unreadable', 'The saved X feature configuration cannot be read. Nothing was sent.') from None


def fingerprint(policy: dict[str, Any]) -> str:
    bound = {'configuration': policy, 'catalog_source': source_digest()}
    return hashlib.sha256(json.dumps(bound, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def permitted(operation_id: str, *, expected_fingerprint: str = '') -> tuple[dict[str, Any], int]:
    policy = load()
    if not policy['enabled']:
        raise XCapabilityRefused('disabled', 'X calls are off. Choose your X features and limits in Settings first.')
    if expected_fingerprint and fingerprint(policy) != expected_fingerprint:
        raise XCapabilityRefused('configuration_changed', 'Your X features or limits changed. Review this request again before sending it.')
    if operation_id not in policy['operations']:
        raise XCapabilityRefused('not_in_access_profile', 'This X feature is not included in your saved access profile. Nothing was sent.')
    return policy, policy['operations'][operation_id]


def feature_label(operation_id: str, locale: str = 'en') -> str:
    """A documented X operation's name in the owner's language (English catalog summary otherwise)."""
    from core.i18n.catalog import catalog_for

    entry = operation(operation_id)
    key = 'xop.' + operation_id
    try:
        return catalog_for(locale).format(key) if key in catalog_for('en').keys else entry['summary']
    except Exception:
        return entry['summary']


def ui_message(key: str, *, locale: str = 'en', **params: Any) -> str:
    from core.i18n.catalog import catalog_for

    if key not in catalog_for('en').keys:
        raise XCapabilityRefused('message_unavailable', 'The X approval message is unavailable. Nothing was authorized.')
    return catalog_for(locale).format(key, **params)


def save(value: Any, *, expected_fingerprint: str = '', ui_locale: str = 'en') -> dict[str, Any]:
    """An owner Settings act. Configuration never grants wallet authority by itself."""
    candidate = validate_policy(value)
    previous = load()
    previous_fingerprint = fingerprint(previous)
    if expected_fingerprint and expected_fingerprint != previous_fingerprint:
        raise XCapabilityRefused('configuration_changed', 'Another X settings change was saved. Reload before editing again.')
    # Disabling is always available. Any other change with enabled calls needs a device decision;
    # loopback alone is insufficient because another local program can issue a loopback POST.
    if candidate['enabled'] and candidate != previous:
        from core.effect_budget_money import format_atomic
        from core.os_consent_gate import require_os_user_consent

        limits = candidate['limits']
        reason = ui_message('xfull.device_prompt', locale=ui_locale, count=len(candidate['operations']),
                            profile=candidate['access_label'], call=format_atomic(limits['per_call_microusd'], 6),
                            chat=format_atomic(limits['per_task_microusd'], 6),
                            day=format_atomic(limits['daily_microusd'], 6), month=format_atomic(limits['monthly_microusd'], 6))
        try:
            accepted = require_os_user_consent(reason)
        except Exception:
            accepted = False
        if not accepted:
            raise XCapabilityRefused('approval_refused', 'The device approval was declined or unavailable. Your X settings did not change.')
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from core.cross_process_lock import LockUnavailable, PublicationLock

    tmp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with PublicationLock(path.with_suffix('.lock')):
            if fingerprint(load()) != previous_fingerprint:
                raise XCapabilityRefused('configuration_changed', 'Your X settings changed during approval. Reload before trying again.')
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(candidate, output, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmp, path)
    except LockUnavailable:
        raise XCapabilityRefused('configuration_busy', 'Another X settings save is in progress. Reload and try again.') from None
    finally:
        tmp.unlink(missing_ok=True)
    return deepcopy(candidate)


def overview() -> dict[str, Any]:
    from core.x_endpoint_catalog import operations

    policy = load()
    catalog = operations()
    return {'ok': True, 'configuration': policy, 'configuration_fingerprint': fingerprint(policy),
            'features': [{key: row[key] for key in ('id', 'summary', 'method', 'path', 'deferred')}
                         for row in catalog],
            'message': 'X calls stay off until you save the features your access permits and explicit cost reservations.'}


def handle(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(body, dict):
            return 400, {'ok': False, 'state': 'invalid_request'}
        if body.get('action') == 'overview' and set(body) <= {'action', 'ui_locale'}:
            return 200, overview()
        if (body.get('action') == 'save' and set(body) <= {'action', 'configuration', 'expected_fingerprint', 'ui_locale'}
                and isinstance(body.get('expected_fingerprint'), str) and len(body['expected_fingerprint']) == 64):
            saved = save(body.get('configuration'), expected_fingerprint=body['expected_fingerprint'],
                         ui_locale=str(body.get('ui_locale') or 'en'))
            return 200, {'ok': True, 'configuration': saved, 'message': 'X features and limits saved.'}
        return 400, {'ok': False, 'state': 'invalid_request', 'message': 'Unknown X configuration step.'}
    except XCapabilityRefused as exc:
        return 400, {'ok': False, 'state': exc.status, 'message': str(exc)}
    except OSError:
        return 503, {'ok': False, 'state': 'storage_unavailable', 'message': 'Your X settings could not be saved.'}
