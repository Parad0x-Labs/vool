"""Legacy environment compatibility: NULLA_* -> VOOL_*.

Before the product rename, configuration was read from ``NULLA_<NAME>`` variables.
The canonical spelling is now ``VOOL_<NAME>``; user shells, launch agents and older
installer scripts that still export ``NULLA_<NAME>`` keep working through this shim.

Precedence: an explicit ``VOOL_<NAME>`` always wins. ``NULLA_<NAME>`` is copied into
``VOOL_<NAME>`` only when the canonical variable is unset or empty. ``VOOL_HOME`` /
``NULLA_HOME`` are additionally resolved directly by :mod:`core.runtime_paths`, so
home resolution works even in embedders that never call this function.

Call :func:`apply_legacy_nulla_env` once, as early as possible in a process entry
point (see ``apps/`` entrypoints, ``conftest.py`` and the installer launchers).
"""
from __future__ import annotations

import os

_LEGACY_PREFIX = "NULLA_"
_CANONICAL_PREFIX = "VOOL_"


def apply_legacy_nulla_env(environ: dict[str, str] | None = None) -> int:
    """Mirror legacy ``NULLA_*`` variables into canonical ``VOOL_*`` ones.

    Returns the number of variables mirrored. Idempotent: an already-set canonical
    variable is never overwritten.
    """
    env = os.environ if environ is None else environ
    mirrored = 0
    for key in list(env.keys()):
        if not key.startswith(_LEGACY_PREFIX):
            continue
        canonical = _CANONICAL_PREFIX + key[len(_LEGACY_PREFIX):]
        if not str(env.get(canonical) or "").strip():
            env[canonical] = env[key]
            mirrored += 1
    return mirrored
