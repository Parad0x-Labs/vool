# R2 pass 3 (AUD-20260829-003). Four legacy import paths under this package
# (`core.agent_runtime.live_data_plan`, `.attempt_approval`, `.attempt_followup`,
# `.attempt_retry`) are pure aliases for canonical modules that moved to `core.<name>`. Passes 1-2
# made the alias FILES re-export shims (copy the canonical namespace onto themselves in place) to
# close a concurrent-first-import race -- but that made the alias and canonical module OBJECTS two
# distinct objects, which silently broke every `mock.patch("core.agent_runtime.<alias>.<name>",
# ...)` site in the tree: the patch landed on the alias's own copy of the name, an object the
# runtime never reads through, so the mock's effect vanished. That is worse than the patch merely
# failing -- a sabotage proof routed through such a patch would go GREEN whether or not the defect
# it exists to catch was reintroduced (BLUE-3, AUD-20260829-003 pass 3).
#
# The fix restores identity by resolving the race a level UP instead of inside the alias file.
# Importing a submodule of a package always waits for the package's own __init__ to finish first --
# CPython serializes on the PACKAGE name's import lock before a thread ever looks up a child name
# -- so registering the alias names here is a genuinely single-threaded boot seam. By the time any
# thread's `from core.agent_runtime.live_data_plan import X` reaches the point of looking up
# `sys.modules['core.agent_runtime.live_data_plan']`, THIS module (`core/agent_runtime/__init__.py`)
# has already finished running end to end, on every code path: either this thread ran it, or it
# waited for the thread that did. Either way the lookup that follows is a FRESH dict read of a
# name this file already finished setting, never a cached reference taken mid-assignment -- which
# is the exact difference from the original hazard. That bug was a module reassigning ITS OWN
# registered identity (`sys.modules[__name__] = ...`) while ITS OWN import was already in flight,
# so a second thread could already be waiting on the OLD object for that SAME name. Here, no
# thread ever waits on the alias name itself while it is mid-transition: nothing is registered
# under the alias name until the assignment below runs, and nothing can observe the alias name
# before this file (which every path to it must already have finished) has set it.
#
# The alias files themselves keep the pass-1/2 re-export-shim body as a defensive fallback -- it
# never executes on the normal path, since the registration below always wins the race, but if
# some exotic path ever manages to import the alias name without this package's __init__ having
# run first (e.g. a forced `importlib.reload` of just the alias module), that fallback still
# cannot reproduce the original race either: proven separately by
# `tests/test_module_identity_race.py`.
#
# `ops/check_module_identity.py` still enforces that no module may assign to
# `sys.modules[__name__]` (its OWN identity) -- unchanged, and unaffected by this: the assignments
# below target a CHILD name from the PARENT's own single-threaded init, never a module's own name
# from within its own body. See that file's docstring for the precise reasoning on why the rule
# does not need loosening to allow this, and does not need tightening to forbid it either.
import sys as _sys

import core.attempt_approval as attempt_approval
import core.attempt_followup as attempt_followup
import core.attempt_retry as attempt_retry
import core.live_data_plan as live_data_plan

from . import (
    builder,
    checkpoints,
    fast_live_info,
    fast_live_info_mode_classifier,
    fast_live_info_mode_failure,
    fast_live_info_mode_markers,
    fast_live_info_mode_policy,
    fast_live_info_mode_query,
    fast_live_info_mode_recency,
    fast_live_info_mode_rules,
    fast_live_info_runtime,
    fast_live_info_runtime_flow,
    fast_live_info_runtime_results,
    fast_live_info_runtime_search,
    fast_live_info_runtime_truth,
    fast_paths,
    hive_followups,
    hive_runtime,
    hive_topics,
    memory_runtime,
    orchestrator,
    presence,
    response,
    turn_dispatch,
    turn_frontdoor,
    turn_reasoning,
    voolbook,
)

_sys.modules[f"{__name__}.live_data_plan"] = live_data_plan
_sys.modules[f"{__name__}.attempt_approval"] = attempt_approval
_sys.modules[f"{__name__}.attempt_followup"] = attempt_followup
_sys.modules[f"{__name__}.attempt_retry"] = attempt_retry

del _sys

__all__ = [
    "attempt_approval",
    "attempt_followup",
    "attempt_retry",
    "builder",
    "checkpoints",
    "fast_live_info",
    "fast_live_info_mode_classifier",
    "fast_live_info_mode_failure",
    "fast_live_info_mode_markers",
    "fast_live_info_mode_policy",
    "fast_live_info_mode_query",
    "fast_live_info_mode_recency",
    "fast_live_info_mode_rules",
    "fast_live_info_runtime",
    "fast_live_info_runtime_flow",
    "fast_live_info_runtime_results",
    "fast_live_info_runtime_search",
    "fast_live_info_runtime_truth",
    "fast_paths",
    "hive_followups",
    "hive_runtime",
    "hive_topics",
    "live_data_plan",
    "memory_runtime",
    "orchestrator",
    "presence",
    "response",
    "turn_dispatch",
    "turn_frontdoor",
    "turn_reasoning",
    "voolbook",
]
