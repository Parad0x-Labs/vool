from __future__ import annotations

import json
import os
import time
from pathlib import Path


def resource(kind: str, value: int, state: str) -> None:
    target = os.environ.get('SOCIAL_RESOURCE_LOG')
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as out:
            out.write(json.dumps({'at': time.time(), 'pid': os.getpid(), 'kind': kind, 'value': value, 'state': state}) + '\n')
    status_target = os.environ.get('SOCIAL_STATUS_PATH')
    if status_target and os.name == 'posix':
        import fcntl

        status = Path(status_target)
        with status.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            data = json.loads(status.read_text())
            field = 'processes' if kind == 'process' else 'ports'
            data[field] = [row for row in data.get(field, [])
                           if not (row.get('fixture') and row.get('value') == value
                                   and row.get('parent_pid') == os.getpid())]
            if state == 'started':
                data[field].append({'fixture': True, 'kind': kind, 'value': value,
                                    'pid': value if kind == 'process' else os.getpid(),
                                    'parent_pid': os.getpid(), 'started': time.time()})
            tmp = status.with_suffix('.tmp-' + str(os.getpid()))
            tmp.write_text(json.dumps(data, indent=2) + '\n')
            os.replace(tmp, status)
