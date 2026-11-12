"""Home-local opt-in; Drop must also remain enabled."""
import json
import os
import secrets

from core.drop import require_enabled
from core.runtime_paths import active_config_home_dir

from .terms import demand


def enabled():
    try:
        value = json.loads((active_config_home_dir() / 'lend.json').read_text())
        return value == {'enabled': True}
    except (OSError, ValueError):
        return False


def require():
    require_enabled()
    demand(os.name == 'posix', 'CAPABILITY_UNAVAILABLE')
    demand(enabled(), 'LEND_DISABLED')


def save(value):
    demand(type(value) is bool)
    if value:
        require_enabled()
        demand(os.name == 'posix', 'CAPABILITY_UNAVAILABLE')
    path = active_config_home_dir() / 'lend.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump({'enabled': value}, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return value
