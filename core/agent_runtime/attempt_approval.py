# A9 pass-001: canonical home of this module moved OUT of the retired engine to
# core.attempt_approval (active code). This retired-path alias keeps legacy imports resolving
# to the one canonical implementation - it contains no logic of its own.
#
# R2 (AUD-20260829-003): this alias used to run `sys.modules[__name__] = _canonical`, replacing
# its own registry entry outright. Under concurrent first import of this name, CPython's import
# fast path can hand a waiting thread the module object it looked up BEFORE the swap happened --
# an object abandoned at the swap that never carried a single canonical name, raising
# `ImportError: cannot import name '...' from 'core.agent_runtime.attempt_approval'`. Sibling
# shim `core/agent_runtime/live_data_plan.py` documents the full mechanism and carries the same
# fix; see it for the complete rationale. Reproduced byte-exact for this module too (see
# tests/test_module_identity_race.py).
#
# The fix: copy the canonical module's own namespace onto THIS module in place, so the one
# object registered in sys.modules for this alias's entire lifetime ends up carrying every
# canonical name and is never abandoned mid-swap. `ops/check_module_identity.py` enforces
# tree-wide that no module under core/, adapters/, or apps/ may assign to
# `sys.modules[__name__]`, so a fifth shim of the old idiom cannot reappear.
import core.attempt_approval as _canonical

for _name in dir(_canonical):
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = getattr(_canonical, _name)
del _name
