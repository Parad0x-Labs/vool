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


def as_vool_browse(monkeypatch, *names: str) -> None:
    """Treat the named test skills as VOOL's own pinned Browse skills (core.skill_origin).

    Only those may seat the social list (core.tool_offer_assembly). The real origin check needs a
    signed local-check receipt for a skill pinned in this app's Browse list; it is proved on its own
    in tests/x_social/test_skill_origin_policy.py, so these tests name the skill instead.
    """
    from core import skill_origin

    real = skill_origin.origin_of
    wanted = set(names)

    def origin_of(skill):
        if str(getattr(skill, "name", "") or "") in wanted:
            return skill_origin.ORIGIN_VOOL_BROWSE
        return real(skill)

    monkeypatch.setattr(skill_origin, "origin_of", origin_of)


def served_as_vool_browse(*names: str) -> None:
    """``as_vool_browse`` for a served daemon: called from the daemon's own test bootstrap.

    A skill installed from a file is only a narrowing skill under the trust boundary; the served
    X flow is about VOOL's own pinned Browse social-manager, whose real origin proof (signed local
    check of a pinned Browse copy) is covered in tests/x_social/test_skill_origin_policy.py.
    """
    from core import skill_origin

    real = skill_origin.origin_of
    wanted = set(names)

    def origin_of(skill):
        if str(getattr(skill, "name", "") or "") in wanted:
            return skill_origin.ORIGIN_VOOL_BROWSE
        return real(skill)

    skill_origin.origin_of = origin_of
