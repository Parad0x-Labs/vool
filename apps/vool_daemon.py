
"""Thin CLI facade for the VOOL mesh daemon (MILESTONE 1).

The daemon AND its CLI entry `main()` live in `core/agent_runtime/daemon.py`; this file
is the process entrypoint only. REMOVAL CONDITION: once every caller imports from the
core module, this file shrinks to the `__main__` hook alone.
"""
from __future__ import annotations

import os as _bootstrap_os
import sys as _bootstrap_sys

_repo_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__)))
if _repo_root not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, _repo_root)


from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)
import os as _research_os

_research_os.environ.setdefault("VOOL_RESEARCH_NETWORKING", "1")  # explicit research invocation

"""Experimental research. Not part of the standard VOOL production runtime.
Not shipped or enabled in official releases. Security assumptions, APIs and
architecture may change or be discarded. See research/README.md and
core/runtime_mode.py for the production/research boundary."""
from core.agent_runtime.daemon import VoolDaemon, main  # noqa: F401  (facade re-exports)
from core.daemon import DaemonConfig  # noqa: F401  (facade re-export)

if __name__ == "__main__":
    raise SystemExit(main())
