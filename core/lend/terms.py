"""Canonical, bounded job terms shared by the two local controllers."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from core.drop.session import b64, unb64

MAX_INPUT = 10 * 1024 * 1024
MAX_OUTPUT = 10 * 1024 * 1024
DAY = 86400000
CAPABILITIES = ('contact_sheet', 'clean_pdf', 'summary', 'translation', 'recording')
#: The jobs this version can run at all: on a computer with process confinement, and a recording only
#: with the local media tools as well. Summarising and translating with a local model are not among
#: them (there is no independently isolated model executor yet), so nothing may offer them as a job.
OFFERED = ('contact_sheet', 'clean_pdf', 'recording')


class LendError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def demand(condition, code='INVALID_INPUT'):
    if not condition:
        raise LendError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identity(value):
    demand(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value))
    return value


def sha(value):
    demand(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value))
    return value


def file_name(value):
    demand(isinstance(value, str) and 0 < len(value) <= 128)
    demand(not any(ord(c) < 32 or ord(c) == 127 or c in '/\\' for c in value))
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise LendError('INVALID_INPUT') from exc
    return value


def terms(value, *, now, fresh=False):
    demand(isinstance(value, dict))
    keys = {'capability', 'jobs', 'max_bytes', 'max_length', 'max_seconds', 'expires', 'auto', 'local_only',
            'trim_start', 'trim_length'}
    demand(set(value) == keys and value['capability'] in CAPABILITIES)
    for key, maximum in [('jobs', 10), ('max_bytes', MAX_INPUT), ('max_length', 250000), ('max_seconds', 120)]:
        demand(type(value[key]) is int and 1 <= value[key] <= maximum)
    demand(type(value['auto']) is bool and value['local_only'] is True)
    demand(type(value['trim_start']) is int and 0 <= value['trim_start'] <= 3600)
    demand(type(value['trim_length']) is int and 1 <= value['trim_length'] <= 300)
    if value['capability'] == 'contact_sheet':
        demand(value['max_length'] <= 100)
    if value['capability'] == 'recording':
        demand(value['max_length'] <= 3600)
    demand(type(value['expires']) is int and value['expires'] > now, 'EXPIRED')
    if fresh:
        demand(value['expires'] <= now + 7 * DAY, 'EXPIRY_LIMIT')
    return dict(value)


def defaults(now):
    # Convert tonight's local wall time with its own offset, rather than
    # freezing the morning's offset across a daylight-saving transition.
    today = datetime.fromtimestamp(now / 1000)
    tonight = today.replace(hour=23, minute=59, second=0, microsecond=0)
    if tonight <= today:
        tonight = today + timedelta(seconds=1)
    return {'capability': 'clean_pdf', 'jobs': 1, 'max_bytes': MAX_INPUT,
            'max_length': 50000, 'max_seconds': 30, 'expires': int(tonight.timestamp()*1000),
            'auto': False, 'local_only': True, 'trim_start': 0, 'trim_length': 30}


def parse_link(link, *, now):
    demand(isinstance(link, str) and link.startswith('vool-lend://invite/') and len(link) < 4096)
    try:
        value = json.loads(unb64(link.removeprefix('vool-lend://invite/'), 1, 3000))
        demand(set(value) == {'id', 'drop', 'host', 'guest', 'terms', 'digest', 'version'})
        demand(type(value['version']) is int and value['version'] == 1)
        identity(value['id'])
        unb64(value['host'], 32)
        unb64(value['guest'], 32)
        demand(isinstance(value['drop'], str) and re.fullmatch(r'vool-drop://invite/[0-9a-f]{32}', value['drop']))
        terms(value['terms'], now=now, fresh=True)
        demand(value['digest'] == digest(value['terms']), 'TERMS_CHANGED')
        return value
    except (ValueError, TypeError, KeyError) as exc:
        raise LendError('INVALID_INVITE') from exc


def make_link(value):
    return 'vool-lend://invite/' + b64(canonical(value).encode())
