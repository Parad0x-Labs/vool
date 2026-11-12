"""Owner-local lending doors; neither relay data nor body fields grant ownership."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os

from core.drop import DropError
from core.drop import enabled as drop_enabled

from . import settings
from .terms import LendError, defaults, demand, parse_link
from .worker import available


def _guard(headers, client_host, *, raw=False):
    from core.web.api.service import _credential_page_refusal

    lowered = {str(k).lower():str(v) for k,v in (headers or {}).items()}
    if raw:
        if lowered.get('content-type', '').split(';', 1)[0] != 'application/octet-stream':
            return 415, {'ok':False, 'error':'CONTENT_TYPE'}
        lowered['content-type'] = 'application/json'
    why = _credential_page_refusal(lowered, client_host)
    if why:
        return (415 if why == 'content_type' else 403), {'ok':False, 'error':'OWNER_REQUIRED'}
    return None


def handle(body, *, headers, client_host):
    refused = _guard(headers, client_host)
    if refused:
        return refused
    if not isinstance(body, dict) or len(json.dumps(body).encode()) > 16384:
        return 400, {'ok': False, 'error': 'INVALID_INPUT'}
    fields = {'status': set(), 'settings': {'enabled'}, 'invite': {'fingerprint', 'confirmed', 'terms'},
              'preview': {'link'}, 'join': {'link', 'fingerprint', 'confirmed'},
              'poll': set(), 'list': set(), 'run': {'job'}, 'decline': {'job'}, 'revoke': {'invite'},
              'download': {'job'}}.get(body.get('action') if isinstance(body.get('action'), str) else '')
    if fields is None or set(body) != fields | {'action'}:
        return 400, {'ok': False, 'error': 'INVALID_INPUT'}
    try:
        action = body['action']
        if action == 'status':
            import time

            from .runtime import status

            return 200, {'ok': True, 'enabled': settings.enabled(), 'drop_enabled': drop_enabled(),
                         'delivery':status(),
                         'defaults': defaults(int(time.time()*1000)), 'reviewed': False,
                         'capabilities': {key: available(key) for key in
                                          ['contact_sheet', 'clean_pdf', 'summary', 'translation', 'recording']}}
        if os.name != 'posix':
            demand(action == 'settings' and body['enabled'] is False, 'CAPABILITY_UNAVAILABLE')
            return 200, {'ok': True, 'enabled': settings.save(False)}
        from core.drop.service import _read
        from core.web.api.drop_api import _home

        from .service import LendHome

        if action == 'settings':
            if body['enabled'] is False and settings.enabled() and drop_enabled():
                with _home() as drop, drop.scope(), LendHome(drop) as home:
                    for value in home.all('invites'):
                        if value['role'] == 'host' and value['state'] not in {'revoked', 'expired'}:
                            home.revoke(value['id'])
            elif body['enabled'] is False:
                LendHome.revoke_local_offers()
            value = settings.save(body['enabled'])
            from .runtime import wake

            wake()
            return 200, {'ok': True, 'enabled': value}
        settings.require()
        if action == 'preview':
            import time

            value = parse_link(body['link'], now=int(time.time()*1000))
            return 200, {'ok': True, 'terms': value['terms'], 'host': value['host'], 'digest': value['digest'],
                         'trusted_processor': True, 'confirmed_by_host': False}
        if action in {'run', 'poll'}:
            with _home() as drop, LendHome(drop) as home:
                if action == 'run':
                    result = {'job': home.run(body['job'])}
                else:
                    outcomes = home.poll()
                    with drop.scope():
                        home.sweep()
                        result = {'outcomes': outcomes, 'invites': home.all('invites'), 'jobs': home.all('jobs')}
                return 200, {'ok': True, **result}
        with _home() as drop, drop.scope(), LendHome(drop) as home:
            if action == 'invite':
                from .qr import image

                link = home.invite(body['fingerprint'], body['terms'], confirmed=body['confirmed'])
                result = {'link': link, 'qr': image(link)}
            elif action == 'join':
                result = {'invite': home.join(body['link'], body['fingerprint'], confirmed=body['confirmed'])}
            elif action == 'decline':
                home.decline(body['job'])
                result = {'state': 'declined'}
            elif action == 'revoke':
                home.revoke(body['invite'])
                result = {'state': 'revoked'}
            elif action == 'download':
                home.sweep()
                job = home.get('jobs', body['job'])
                home.live(job['invite'])
                demand(job['role'] == 'guest' and job['state'] == 'complete', 'RESULT_NOT_READY')
                data = _read(home.folder(job['id'])/'file.bin', 10*1024*1024)
                demand(hashlib.sha256(data).hexdigest() == job['receipt']['result_digest'], 'RESULT_CHANGED')
                result = {'data': base64.b64encode(data).decode(), 'name': job['result_name'], 'receipt': job['receipt']}
            else:
                home.sweep()
                result = {'invites': home.all('invites'), 'jobs': home.all('jobs')}
            return 200, {'ok': True, **result}
    except (LendError, DropError) as exc:
        return 409, {'ok': False, 'error': exc.code}
    except (OSError, ValueError, TypeError, KeyError):
        logging.getLogger(__name__).exception('Lending local operation unavailable')
        return 400, {'ok': False, 'error': 'LOCAL_INPUT_OR_STATE_FAILURE'}


def upload(data, *, headers, client_host):
    refused = _guard(headers, client_host, raw=True)
    if refused:
        return refused
    from urllib.parse import unquote

    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    try:
        settings.require()
        from core.web.api.drop_api import _home

        from .service import LendHome

        demand(lowered.get('x-lend-size') == str(len(data)), 'INPUT_CHANGED')
        demand(lowered.get('x-lend-sha256') == hashlib.sha256(data).hexdigest(), 'INPUT_CHANGED')
        with _home() as drop, drop.scope(), LendHome(drop) as home:
            key = home.submit(lowered.get('x-lend-invite'), data,
                              unquote(lowered.get('x-lend-name', '')),
                              job_id=lowered.get('x-lend-job'))
            return 200, {'ok': True, 'job': key}
    except (LendError, DropError) as exc:
        return 409, {'ok': False, 'error': exc.code}
    except (OSError, ValueError, TypeError, KeyError):
        logging.getLogger(__name__).exception('Lending upload unavailable')
        return 400, {'ok': False, 'error': 'LOCAL_INPUT_OR_STATE_FAILURE'}
