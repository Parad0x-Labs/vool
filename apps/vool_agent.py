
"""Thin CLI/composition facade for the VOOL agent (MILESTONE 1).

The agent runtime AND its CLI entry `main()` live in `core/agent_runtime/agent.py`;
this file is the process entrypoint only (and the pyproject script target). REMOVAL
CONDITION: once every caller -- production imports AND test patch sites -- targets the
core module, the re-export block is deleted and only the `__main__` hook remains.
"""
from __future__ import annotations

import os as _bootstrap_os
import sys as _bootstrap_sys

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)

from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
from core.agent_runtime.agent import *  # noqa: F401,F403  (public surface)
from core.agent_runtime.agent import (  # noqa: F401  (underscore names tests drive)
    _arm_demand_set_for_entrance,
    _certify_entrance_turn,
    _r3_open_turn_execution,
    _record_demand_consumption,
    _release_entrance_demand_set,
    _seal_semantic_result,
    main,
)

if __name__ == "__main__":
    raise SystemExit(main())
