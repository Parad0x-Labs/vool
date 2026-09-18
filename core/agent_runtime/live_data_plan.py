# A9 pass-001: canonical home of this module moved OUT of the retired engine to
# core.live_data_plan (active code). This retired-path alias keeps legacy imports resolving
# to the one canonical implementation - it contains no logic of its own.
#
# R2 (AUD-20260829-003): this alias used to run `sys.modules[__name__] = _canonical`, replacing
# its own registry entry outright. Under concurrent first import of this name -- the conductor's
# ThreadPoolExecutor can run two weather nodes that both do
# `from core.agent_runtime.live_data_plan import _weather_subtask` for the first time in the same
# process (core/conductor/operations.py, core/conductor/scheduler.py:260) -- CPython's import
# fast path can hand a waiting thread the module object it looked up BEFORE the swap happened.
# That object was abandoned at the swap and never carried a single canonical name, so the waiting
# thread's `IMPORT_FROM` raises
# `ImportError: cannot import name '_weather_subtask' from 'core.agent_runtime.live_data_plan'`.
# This is a mechanism, not a maybe: reproduced byte-exact (see tests/test_module_identity_race.py).
#
# The fix is to never abandon the registered module object: copy the canonical module's own
# namespace onto THIS module in place, so the one object registered in sys.modules for this
# alias's entire lifetime ends up carrying every canonical name, including private ones existing
# call sites reach for directly (`_weather_subtask`, `_market_subtask`, `_resolve_price_alias`).
# Any thread that waits on this module's import lock and then reads an attribute off the object
# it already holds gets the fully populated module -- never an abandoned one. `import *` semantics
# are preserved too: Python's star-import already skips leading-underscore names by inspecting the
# module's own __dict__, so copying them here changes nothing about `from ... import *` behaviour.
#
# `ops/check_module_identity.py` enforces tree-wide that no module under core/, adapters/, or
# apps/ may assign to `sys.modules[__name__]`, so a fifth shim of the old idiom cannot reappear.
import core.live_data_plan as _canonical

for _name in dir(_canonical):
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = getattr(_canonical, _name)
del _name
