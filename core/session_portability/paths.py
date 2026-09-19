"""Home scoping: run a block against an explicit VOOL home, then restore the process state.

The storage layer resolves paths at call time (`active_default_db_path()`, `active_data_dir()`)
and invalidates its pooled connection when the configured path changes, so a scoped switch is the
supported way for one process to touch a second home — the importer's whole job — without
cross-contaminating either home.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from core.runtime_paths import active_vool_home, configure_runtime_home
from storage.db import active_default_db_path, configure_default_db_path, resolve_runtime_db_filename

_SCOPE_LOCK = threading.RLock()


@contextmanager
def scoped_home(home: str | Path | None, *, env: dict[str, str] | None = None):
    """Bind the runtime home for the block. `home=None` keeps the process's current home.

    Restores the previous configuration even when the block raises. A module-level lock
    serializes scoped switches: two threads reconfiguring global storage state concurrently
    would interleave each other's homes.
    """
    if home is None:
        yield
        return
    target = Path(home).expanduser()
    with _SCOPE_LOCK:
        previous_home = active_vool_home(env=env)
        previous_db = active_default_db_path()
        configure_runtime_home(target)
        configure_default_db_path(resolve_runtime_db_filename((target / "data").resolve()))
        try:
            yield target
        finally:
            configure_runtime_home(Path(previous_home))
            configure_default_db_path(previous_db)
