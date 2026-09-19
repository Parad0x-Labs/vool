"""Live claim-sequence monitor for VOOL's front-door claim points.

TEST INFRASTRUCTURE ONLY. This module changes no product behaviour; it is a debugging lens over
the runtime, in the same spirit as `core.semantic.reach` -- but where `reach` requires a call site
to opt in with an explicit `semantic_reach.claimed(...)` call, this module observes probes that
were never wired into that system at all. `core.agent_runtime.turn_frontdoor.handle_turn_frontdoor`
(~1,438 lines, ~70 inline `x = probe(...); if x: return ...` claim points) contains ZERO
`semantic_reach` call sites, so every one of those ~70 gates currently reports as the single label
`GATE_FRONTDOOR`. Debugging a misroute among them means grepping the function by hand. This module
exists to answer, for a given prompt: which probes were consulted, in what order, which one
claimed, and what it returned -- without touching `turn_frontdoor.py` or any other production file.

THE CENTRAL TECHNICAL PROBLEM AND HOW IT WAS RESOLVED
-------------------------------------------------------
The probes are not separate functions the runtime dispatches through a table; they are ordinary
Python callables imported directly into whichever module calls them. Some of those imports are
function-local (`from X import Y` re-executed on every call); some are module-top-level (`Y` bound
once, at import time, into the CALLER's own namespace); some access the whole module object
(`import X; X.Y(...)`) rather than the bare symbol. These three patterns have different, precisely
knowable consequences for monkeypatching:

* **Function-local `from X import Y`, re-executed per call** -- patching `X.Y` (the DEFINING
  module's attribute) is seen immediately, because the import statement re-resolves `X.Y` fresh
  every time it runs.
* **Module-top-level `from X import Y`** -- the CALLER's module gets its own bound copy of the
  function object at import time. Patching `X.Y` afterwards does nothing to that copy; the caller's
  own module attribute must be patched directly.
* **`import X` (or `from pkg import X as alias`), then `X.Y(...)`** -- always observable by patching
  `X.Y`, regardless of when the `import X` statement ran, because `X` is a live, shared, mutable
  module object and the caller re-reads its `Y` attribute on every call.

Every one of the 16 required registry symbols was traced to its REAL production call site(s) --
not assumed from the task's suggested module path -- and empirically classified using this rule.
The method combined two techniques, per symbol, and both are real (see
`/private/tmp/.../scratchpad/verify_observability.py` for the standalone script this was worked out
in before being folded in here):

1. LIVE CALL -- patch the defining module's attribute with a call-logging wrapper that delegates to
   the original, then invoke the real (or nearest safe real) production caller and confirm the
   wrapper fired.
2. STRUCTURAL INSPECTION -- for callers that need heavier scaffolding to drive live (a running
   agent, a session, storage), `hasattr(caller_module, symbol)` before any patch is applied answers
   the question exactly: a persistent top-level attribute means a load-time copy (blind); its
   absence means the caller only ever imports the symbol inside a function body (observable, since
   that import statement re-executes on every call).

FINDINGS -- OBSERVABLE, BLIND, AND WHY (this is the deliverable's most important section)
-------------------------------------------------------------------------------------------
Fully OBSERVABLE by patching only the defining module (10 of 16):
    maybe_answer_currency_value, asks_for_dynamic_currency_value, static_currency_identity_admitted
    (all core.currency_value_contract) -- live-verified via VoolAgent._run_once_inner,
    core.agent_runtime.grounded_mode.answer_mode_for, and the intra-module call chain respectively.

    _recent_price_subject, url_read_request (intra-module calls from
    recover_price_lookup_query / maybe_handle_url_read -- live-verified).

    is_build_instruction, is_opted_out (core.agent_runtime.build_request_intent) -- every real
    caller found (fast_paths_builder, fast_paths_utility, builder_facade, fast_paths_skill,
    builder/*.py) does `from core.agent_runtime import build_request_intent` then
    `build_request_intent.is_build_instruction(...)` -- a whole-module import, so dotted access is
    always fresh. Live-verified via fast_paths_utility.looks_like_agentic_build_request.

    turn_may_hold_several_requests -- **the task listed this symbol's module as
    `core.conductor.planner`; that is wrong as a patch target.** It is DEFINED in
    `core.agent_runtime.turn_planner` and only ever referenced elsewhere via a function-local
    `from core.agent_runtime.turn_planner import turn_may_hold_several_requests` (both in
    `core.conductor.planner.plan_conductor_turn` and in `core.execution_requirements`).
    `core.conductor.planner` never has a persistent attribute of this name at all, so patching
    "core.conductor.planner.turn_may_hold_several_requests" as literally written would raise
    AttributeError, not silently do nothing. Registered here under its real defining module,
    live-verified via `core.conductor.planner.plan_conductor_turn`.

    build_live_data_plan (core.agent_runtime.live_data_plan) -- both real call sites
    (core/agent_runtime/agent.py since M1, two places) are function-local imports inside VoolAgent methods;
    confirmed structurally (`apps.vool_agent` has no top-level attribute of this name) since
    driving those methods live needs a running attempt/session/storage stack out of scope here.

    record_slice_answer (core.agent_runtime.answer_coverage) -- turn_frontdoor.py's three call
    sites are all inside `handle_turn_frontdoor`'s body (function-local); confirmed structurally.

PARTIAL -- observable from some real callers, blind from others, same symbol (2 of 16):
    parse_raw_output_contract, apply_raw_output_contract (core.raw_output_contract) --
    OBSERVABLE via core.agent_runtime.turn_frontdoor (both symbols' relevant call sites are
    function-local; live-verified for apply_raw_output_contract via
    fast_paths_utility._fixed_shape_violates_a_stated_output_contract), core.task_router,
    core.execution.planner, core.web.api.response_control, core.agent_runtime.agent (all function-local,
    confirmed by reading). BLIND via core.exact_output_seal, core.memory_first_router,
    core.stable_exact_semantics, core.stable_term_contract, and (apply_raw_output_contract only)
    core.agent_runtime.response -- all of these do a MODULE-TOP-LEVEL `from
    core.raw_output_contract import parse_raw_output_contract[, apply_raw_output_contract]`,
    confirmed structurally (each has its own persistent attribute of the name). A patch to the
    defining module silently misses whichever of these lanes actually produced a turn's final
    text. `also_patch` below lists every blind copy found so `monitor()` covers them too.

Genuinely BLIND on the defining module alone, but with ONE clean alternate target (2 of 16):
    intake_request_text (core.within_turn_retraction) -- core/agent_runtime/agent.py imports it at
    module top level (the only real consumer found); confirmed structurally. `also_patch`
    includes the agent module (core.agent_runtime.agent since M1).

    looks_like_grounded_price_lookup, looks_like_builder_request,
    looks_like_personalized_plan_request -- each is defined in its own small
    `fast_paths_*.py` module, but the ONLY real production path to it is
    `VoolAgent` (via `FastPathFacadeMixin`) -> `agent_fast_paths.<name>(...)`, where
    `agent_fast_paths` is `core.agent_runtime.fast_paths`, a module that captured its OWN copy of
    each name at ITS module-top-level import (or, for the price lookup, a bare
    `looks_like_grounded_price_lookup = agent_fast_live_info.looks_like_grounded_price_lookup`
    rebinding). Live-verified for all three: patching the defining module leaves the facade path
    unaffected; patching `core.agent_runtime.fast_paths.<name>` instead is seen immediately.
    `also_patch` lists `core.agent_runtime.fast_paths` for all three.

CAVEAT ON COMPLETENESS. Every `also_patch` list below was built from a full-repo grep of each
symbol at SHA 9c328889, read with enough surrounding context to classify the import style, and (for
every case involving `fast_paths.py` and the currency/price/build/raw-output/companion families)
cross-checked with a real monkeypatch-and-invoke run. It is not a claim that no OTHER re-exported
copy exists anywhere in ~740 shared files; a new one added later would silently reopen a blind spot
this module does not know about. That is the honest boundary of "empirically verified": it covers
what was found, not what could theoretically exist.

WHAT monitor() DOES AND DOES NOT CHANGE
------------------------------------------
Like `core.semantic.reach`, this observes and never decides: every wrapper calls straight through
to the original function and returns its real result unchanged. Installing/removing the monitor
must not be observable in the runtime's own behaviour, only in `ClaimMonitor.calls`.
"""
from __future__ import annotations

import importlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

# `core.memory_first_router` / `core.exact_output_seal` have a documented circular-import ordering
# hazard (`core.agent_runtime.turn_reasoning` imports back from `memory_first_router`) that only
# bites when one of them is the very first `core.*` module a fresh process touches -- verified while
# building this module (a bare `import core.memory_first_router` first thing raised ImportError;
# importing the package below first made it disappear). Real entrypoints never hit this because
# something earlier in their own import chain already establishes the package. A monitor script
# easily can, so it is done here, once, defensively, rather than left to whoever calls this module
# first.
import core.agent_runtime


@dataclass(frozen=True)
class ProbeSpec:
    """One registry entry: WHERE the callable is defined, and whether patching that location alone
    is actually seen by the real production call chain.

    `module` is the DEFINING module (where `def symbol(...)` lives) -- except
    `turn_may_hold_several_requests`, corrected to its true defining module; see the module
    docstring. `also_patch` lists every OTHER module empirically or structurally confirmed to hold
    its own independent, load-time-bound copy of the same symbol, reached by a real caller.
    `monitor()` installs a wrapper at `module` AND every path in `also_patch`.
    """

    symbol: str
    module: str
    cluster: str
    observability: str  # "observable" | "partial" | "blind_without_also_patch"
    evidence: str
    also_patch: tuple[str, ...] = ()


REGISTRY: tuple[ProbeSpec, ...] = (
    ProbeSpec(
        symbol="maybe_answer_currency_value",
        module="core.currency_value_contract",
        cluster="currency",
        observability="observable",
        evidence=(
            "Live-verified via VoolAgent._run_once_inner: the real call site "
            "(core/agent_runtime/agent.py since M1, inside the method body) is a function-local "
            "`from core.currency_value_contract import maybe_answer_currency_value`, re-executed "
            "every turn."
        ),
    ),
    ProbeSpec(
        symbol="asks_for_dynamic_currency_value",
        module="core.currency_value_contract",
        cluster="currency",
        observability="observable",
        evidence=(
            "Live-verified via core.agent_runtime.grounded_mode.answer_mode_for (function-local "
            "import) and via intra-module calls inside currency_value_contract.py itself."
        ),
    ),
    ProbeSpec(
        symbol="static_currency_identity_admitted",
        module="core.currency_value_contract",
        cluster="currency",
        observability="observable",
        evidence=(
            "Live-verified via maybe_answer_currency_value -> _identity_reply, both defined in "
            "the same module, so the call resolves through the module's own (patchable) global "
            "namespace regardless of caller."
        ),
    ),
    ProbeSpec(
        symbol="looks_like_grounded_price_lookup",
        module="core.agent_runtime.fast_live_info_price",
        cluster="live_price",
        observability="blind_without_also_patch",
        evidence=(
            "Live-verified BLIND on the defining module: the real path is "
            "FastPathFacadeMixin._looks_like_grounded_price_lookup -> "
            "agent_fast_paths.looks_like_grounded_price_lookup, and "
            "core/agent_runtime/fast_paths.py:70 does "
            "`looks_like_grounded_price_lookup = agent_fast_live_info.looks_like_grounded_price_lookup` "
            "at module top level -- its own bound copy, made once at import. Live-verified "
            "OBSERVABLE when core.agent_runtime.fast_paths is patched instead."
        ),
        also_patch=("core.agent_runtime.fast_paths",),
    ),
    ProbeSpec(
        symbol="_recent_price_subject",
        module="core.agent_runtime.fast_live_info_price",
        cluster="live_price",
        observability="observable",
        evidence=(
            "Live-verified via recover_price_lookup_query, defined in the same module "
            "(intra-module call, always sees a patch to the module's own attribute)."
        ),
    ),
    ProbeSpec(
        symbol="url_read_request",
        module="core.agent_runtime.fast_paths_web",
        cluster="web_read",
        observability="observable",
        evidence=(
            "Live-verified via maybe_handle_url_read, defined in the same module (intra-module "
            "call)."
        ),
    ),
    ProbeSpec(
        symbol="looks_like_builder_request",
        module="core.agent_runtime.fast_paths_builder",
        cluster="builder",
        observability="blind_without_also_patch",
        evidence=(
            "Live-verified BLIND on the defining module for the same reason as "
            "looks_like_grounded_price_lookup: the real path is "
            "FastPathFacadeMixin._looks_like_builder_request -> "
            "agent_fast_paths.looks_like_builder_request, and fast_paths.py imports the symbol at "
            "module top level. Live-verified OBSERVABLE when core.agent_runtime.fast_paths is "
            "patched instead."
        ),
        also_patch=("core.agent_runtime.fast_paths",),
    ),
    ProbeSpec(
        symbol="is_build_instruction",
        module="core.agent_runtime.build_request_intent",
        cluster="builder",
        observability="observable",
        evidence=(
            "Live-verified via fast_paths_utility.looks_like_agentic_build_request. Every real "
            "caller found (fast_paths_builder, fast_paths_utility, builder_facade, "
            "fast_paths_skill, builder/named_file_build.py, builder/small_project_plan.py) does "
            "`from core.agent_runtime import build_request_intent` (the whole module) then "
            "`build_request_intent.is_build_instruction(...)` -- dotted access on a live module "
            "object is always fresh, independent of when the module import ran."
        ),
    ),
    ProbeSpec(
        symbol="is_opted_out",
        module="core.agent_runtime.build_request_intent",
        cluster="builder",
        observability="observable",
        evidence="Same whole-module dotted-access pattern as is_build_instruction; live-verified.",
    ),
    ProbeSpec(
        symbol="looks_like_personalized_plan_request",
        module="core.agent_runtime.fast_paths_companion",
        cluster="companion",
        observability="blind_without_also_patch",
        evidence=(
            "Live-verified BLIND on the defining module: real path is "
            "FastPathFacadeMixin._looks_like_personalized_plan_request -> "
            "agent_fast_paths.looks_like_personalized_plan_request, and fast_paths.py imports the "
            "symbol at module top level. Live-verified OBSERVABLE when "
            "core.agent_runtime.fast_paths is patched instead."
        ),
        also_patch=("core.agent_runtime.fast_paths",),
    ),
    ProbeSpec(
        symbol="turn_may_hold_several_requests",
        module="core.agent_runtime.turn_planner",
        cluster="planner",
        observability="observable",
        evidence=(
            "MODULE CORRECTED from the task's `core.conductor.planner`: that is where it is "
            "CONSUMED (function-local import inside plan_conductor_turn), not where it is defined. "
            "core.conductor.planner never has a persistent attribute of this name, so patching it "
            "as literally specified would raise AttributeError. The true defining module, "
            "core.agent_runtime.turn_planner, is live-verified observable via "
            "core.conductor.planner.plan_conductor_turn (function-local import) and via "
            "core.execution_requirements (also function-local, with its own no-op fallback if the "
            "import itself fails -- read, not exercised)."
        ),
    ),
    ProbeSpec(
        symbol="build_live_data_plan",
        module="core.agent_runtime.live_data_plan",
        cluster="live_data",
        observability="observable",
        evidence=(
            "Structurally confirmed: apps.vool_agent (the only real consumer, two call sites) "
            "has no top-level attribute of this name, so both call sites are the function-local "
            "`from core.agent_runtime.live_data_plan import build_live_data_plan` visible by "
            "reading the source. Not live-driven end-to-end here: both call sites also touch "
            "`self._create_live_data_runtime_attempt` / storage, out of scope for this monitor's "
            "own verification without a running attempt/session stack."
        ),
    ),
    ProbeSpec(
        symbol="parse_raw_output_contract",
        module="core.raw_output_contract",
        cluster="raw_output",
        observability="partial",
        evidence=(
            "OBSERVABLE via core.agent_runtime.turn_frontdoor (two function-local imports, "
            "structurally confirmed), core.task_router, core.execution.planner, "
            "core.web.api.response_control, core.agent_runtime.fast_paths_utility, "
            "apps.vool_agent (all function-local, confirmed by reading). BLIND via "
            "core.exact_output_seal, core.memory_first_router, core.stable_exact_semantics, "
            "core.stable_term_contract -- all four have their own module-top-level "
            "`from core.raw_output_contract import parse_raw_output_contract`, structurally "
            "confirmed (each has a persistent top-level attribute of this name)."
        ),
        also_patch=(
            "core.exact_output_seal",
            "core.memory_first_router",
            "core.stable_exact_semantics",
            "core.stable_term_contract",
        ),
    ),
    ProbeSpec(
        symbol="apply_raw_output_contract",
        module="core.raw_output_contract",
        cluster="raw_output",
        observability="partial",
        evidence=(
            "Live-verified OBSERVABLE via "
            "fast_paths_utility._fixed_shape_violates_a_stated_output_contract (function-local "
            "import). BLIND via core.exact_output_seal, core.memory_first_router, "
            "core.agent_runtime.response -- all three have their own module-top-level "
            "`from core.raw_output_contract import apply_raw_output_contract`, structurally "
            "confirmed."
        ),
        also_patch=(
            "core.exact_output_seal",
            "core.memory_first_router",
            "core.agent_runtime.response",
        ),
    ),
    ProbeSpec(
        symbol="intake_request_text",
        module="core.within_turn_retraction",
        cluster="retraction",
        observability="blind_without_also_patch",
        evidence=(
            "Structurally confirmed BLIND: apps/vool_agent.py:125 is the ONLY real consumer "
            "found, and it is a module-top-level `from core.within_turn_retraction import "
            "intake_request_text` (confirmed: apps.vool_agent has its own persistent top-level "
            "attribute of this name, identical object to the defining module's right now -- but a "
            "patch to the defining module afterwards would not change apps.vool_agent's already-"
            "bound copy)."
        ),
        also_patch=("core.agent_runtime.agent",),
    ),
    ProbeSpec(
        symbol="record_slice_answer",
        module="core.agent_runtime.answer_coverage",
        cluster="answer_coverage",
        observability="observable",
        evidence=(
            "Structurally confirmed: turn_frontdoor.py's three call sites are all inside "
            "handle_turn_frontdoor's body (function-local import); turn_frontdoor has no "
            "top-level attribute of this name. NOTE: unlike the other 15, this is a RECORDER, not "
            "a claim gate -- it always returns a ClaimCoverage object (never None/False), so "
            "`returned_truthy` is always True here. Its presence in a trace means 'this lane's "
            "answer was kept for its own clauses', not 'this probe claimed the turn'."
        ),
    ),
    # ------------------------------------------------------------------ added 2026-08-17
    # Every entry below was classified by the SAME method as the original 16: full-repo grep of the
    # symbol, read each real production call site to classify its import style, then confirm --
    # `hasattr(caller_module, symbol)` BEFORE any patch for the structural half, and a
    # patch-the-defining-module-then-drive-the-real-caller run for the live half. See
    # `tests/claim_dispatch_tracer.py` for what consumes them.
    ProbeSpec(
        symbol="currency_fast_path",
        module="core.agent_runtime.fast_paths_currency",
        cluster="currency",
        observability="observable",
        evidence=(
            "THE PROVEN OMISSION. On set-6 `L9.3` ('...Count the number of characters in "
            "`EUR/USD`...') all three registered core.currency_value_contract probes decline "
            "(maybe_answer_currency_value -> None, asks_for_dynamic_currency_value -> False, "
            "static_currency_identity_admitted -> False) yet BOTH model lanes answered with "
            "currency content at model_ran=False, route=currency_rate_lookup_fast_path. Executed "
            "here: currency_fast_path(L9.3) returns kind='rate_lookup' whose `response` is "
            "byte-for-byte the r8 local-lane answer, and turn_frontdoor.py:1134 builds the route "
            "label as f\"currency_{reply['kind']}_fast_path\" -- so this symbol, not any "
            "currency_value_contract symbol, is the L9.3 claimant.\n"
            "CALLERS (all three found by grep, all FUNCTION-LOCAL `from "
            "core.agent_runtime.fast_paths_currency import currency_fast_path`): "
            "turn_frontdoor.py:79 (in closed_semantic_contract_covers_turn, called :132), "
            "turn_frontdoor.py:185 (in _currency_reply, called :249 -- the one the front door "
            "actually dispatches through), answer_coverage.py:190 (in _claims_currency, called "
            ":192). Structurally confirmed: neither core.agent_runtime.turn_frontdoor nor "
            "core.agent_runtime.answer_coverage has a top-level attribute of this name. "
            "LIVE-VERIFIED: with a wrapper installed on the DEFINING module only, driving "
            "turn_frontdoor._currency_reply, turn_frontdoor.closed_semantic_contract_covers_turn "
            "and answer_coverage._claims_currency all fired it. also_patch is therefore empty by "
            "measurement, not by assumption."
        ),
    ),
    ProbeSpec(
        symbol="analyze_retrieval_constraints",
        module="core.retrieval_constraints",
        cluster="authority",
        observability="partial",
        evidence=(
            "The AUTHORITY, not a claimant: it produces the turn's negative retrieval contract "
            "(has_prohibition / forbids_all_tools / forbids_external_retrieval / "
            "prohibited_toolsets) that claimants are supposed to consult before claiming. "
            "Registered so a claim can be split by whether an authority was consulted inside its "
            "dynamic extent and what that authority said.\n"
            "OBSERVABLE via function-local imports in core.agent_runtime.turn_frontdoor:190 (inside "
            "_currency_reply), core.agent_runtime.fast_paths_web:85 (inside url_read_request), "
            "core.task_router (x4), core.execution.planner (x2), core.execution_requirements (x2) "
            "-- all structurally confirmed (none has a top-level attribute of this name). "
            "BLIND via four MODULE-TOP-LEVEL importers, each structurally confirmed to hold its own "
            "bound copy: core.agent_runtime.fast_live_info_price:8, "
            "core.agent_runtime.fast_live_info_mode_classifier:6, "
            "core.agent_runtime.fast_live_info_runtime_preflight:6, "
            "core.conductor.fresh_data_operations:35. All four are in also_patch.\n"
            "LIVE-VERIFIED through turn_frontdoor._currency_reply on set-6 L9.3, and the result "
            "CORRECTS an earlier reading of mine that was taken from a `domains` attribute this "
            "type does not have (it returned an empty tuple for every input, which looked like a "
            "classifier miss and was really an attribute typo). Read from the real fields, the "
            "authority is COMPLETELY CORRECT on that turn:\n"
            "    has_prohibition           = True\n"
            "    negative_clauses          = ('Do not perform market lookup or currency conversion',)\n"
            "    prohibited_toolsets       = frozenset({'market_prices'})\n"
            "    forbids('market_prices')  = True\n"
            "    forbids_all_tools         = False   forbids_external_retrieval = False\n"
            "The defect is in the CONSUMER, not the authority. `turn_frontdoor._currency_reply` "
            "calls this at :196 and then gates only on `not constraints.forbids_all_tools and not "
            "constraints.forbids_external_retrieval` (:204-206). It never asks "
            "`constraints.forbids('market_prices')`, so a correctly extracted and correctly "
            "classified prohibition is computed and dropped -- and the turn goes on to fetch a live "
            "FX rate (`grounded=live_rate`) on a prompt that said not to."
        ),
        also_patch=(
            "core.agent_runtime.fast_live_info_price",
            "core.agent_runtime.fast_live_info_mode_classifier",
            "core.agent_runtime.fast_live_info_runtime_preflight",
            "core.conductor.fresh_data_operations",
        ),
    ),
    ProbeSpec(
        symbol="live_request_after_retraction",
        module="core.within_turn_retraction",
        cluster="retraction",
        observability="observable",
        evidence=(
            "CORRECTION TO A PLAUSIBLE CHOICE. `turn_retracts_an_instruction` looks like the "
            "retraction claimant and is what the calibration test asserts on, but a full-repo grep "
            "finds it ONLY in its own module's `__all__` and its own def -- it has no production "
            "caller at all, so wrapping it would observe nothing on a served turn. The symbol the "
            "runtime actually runs is `live_request_after_retraction`, called intra-module from "
            "`intake_request_text` (within_turn_retraction.py:161), which apps/vool_agent.py:125 "
            "imports at module top level. An intra-module call resolves through the defining "
            "module's own global namespace, so patching the defining module is always seen -- "
            "unlike `intake_request_text` itself, which needs its `apps.vool_agent` copy patched."
        ),
    ),
    ProbeSpec(
        symbol="coverage_for",
        module="core.agent_runtime.answer_coverage",
        cluster="answer_coverage",
        observability="observable",
        evidence=(
            "The gate ABOVE the currency claim: turn_frontdoor.py:1117 computes "
            "coverage_for(effective_input, FAMILY_CURRENCY) and only consults the currency lane "
            "when `covers_whole_turn`. Registered because a currency turn that is NOT claimed is "
            "usually decided here rather than in currency_fast_path, and the trace has to be able "
            "to tell those apart. All turn_frontdoor call sites (:76, :930 imports; :127, :946, "
            ":1117, :1209, :1733 calls) are inside function bodies; structurally confirmed that "
            "turn_frontdoor has no top-level attribute of this name."
        ),
    ),
    ProbeSpec(
        symbol="slice_binding_is_unsafe",
        module="core.agent_runtime.answer_coverage",
        cluster="answer_coverage",
        observability="observable",
        evidence=(
            "The poison check paired with coverage_for at turn_frontdoor.py:1118-1120 and :1142. "
            "Same function-local import pattern, same structural confirmation."
        ),
    ),
    ProbeSpec(
        symbol="fast_path_result",
        module="core.agent_runtime.fast_command_surface",
        cluster="dispatch_emitter",
        observability="observable",
        evidence=(
            "NOT a claim gate -- the front door's single DISPATCH EMITTER, and the reason "
            "won_dispatch can be observed rather than inferred for every fast-path lane. Every "
            "`return {'result': agent._fast_path_result(..., reason=...)}` in turn_frontdoor.py "
            "(~50 of them, including the currency claim at :1134) funnels through "
            "apps/vool_agent.py:2439 `agent_fast_command_surface.fast_path_result(self, **kwargs)`, "
            "and that `reason` is what core/response_provenance.py:_route_label turns into the "
            "`route` a caller reads off the HTTP response. apps.vool_agent binds the whole MODULE "
            "(`from core.agent_runtime import fast_command_surface as agent_fast_command_surface`) "
            "and reads `.fast_path_result` off it per call, so dotted access is always fresh; "
            "structurally confirmed that apps.vool_agent has no top-level attribute of this name."
        ),
    ),
    ProbeSpec(
        symbol="maybe_handle_live_info_fast_path",
        module="core.agent_runtime.fast_live_info_runtime_flow",
        cluster="live_info",
        observability="blind_without_also_patch",
        evidence=(
            "The claimant behind route=live_info_fast_path. Re-exported THREE times at module top "
            "level, each structurally confirmed to hold its own load-time-bound copy:\n"
            "  core/agent_runtime/fast_live_info_runtime.py:3  (from .fast_live_info_runtime_flow)\n"
            "  core/agent_runtime/fast_live_info_router.py:10  (from ...fast_live_info_runtime)\n"
            "  core/agent_runtime/fast_paths.py                (the one the SERVED path uses)\n"
            "THE THIRD ONE WAS MISSED ON THE FIRST PASS, and the set-6 attribution run caught it "
            "empirically rather than by review: six turns served `route=live_info_fast_path` while "
            "this symbol recorded ZERO calls, which is only possible if the binding that ran was a "
            "copy the monitor had not patched. The served path is "
            "turn_frontdoor.py:1738 -> fast_path_facade.py:260 "
            "`_maybe_handle_live_info_fast_path` -> `agent_fast_paths.maybe_handle_live_info_fast_path`, "
            "and `agent_fast_paths` is `core.agent_runtime.fast_paths`. Verified by patch-and-invoke: "
            "replacing the DEFINING module's attribute leaves "
            "`core.agent_runtime.fast_paths.maybe_handle_live_info_fast_path` pointing at the old "
            "object, so that lane was invisible. All three are in also_patch now.\n"
            "This is the concrete form of the module docstring's completeness caveat: an also_patch "
            "list built from grep + review can still miss a re-export, and the only thing that finds "
            "it is a served turn whose route has no recorded call."
        ),
        also_patch=(
            "core.agent_runtime.fast_live_info_runtime",
            "core.agent_runtime.fast_live_info_router",
            "core.agent_runtime.fast_paths",
        ),
    ),
    ProbeSpec(
        symbol="plan_conductor_turn",
        module="core.conductor.planner",
        cluster="planner",
        observability="blind_without_also_patch",
        evidence=(
            "The claimant behind route=conductor_multi_intent_plan (the single largest route in "
            "set 6). Structurally confirmed BLIND on the defining module: core/conductor/__init__.py:49 "
            "does a MODULE-TOP-LEVEL `from core.conductor.planner import ConductorPlan, "
            "plan_conductor_turn` (core.conductor has its own persistent attribute of this name), "
            "and the only real consumer, apps/vool_agent.py:1952, imports `from core.conductor "
            "import ... plan_conductor_turn` -- i.e. it re-resolves the PACKAGE's copy, not the "
            "defining module's. also_patch=('core.conductor',)."
        ),
        also_patch=("core.conductor",),
    ),
    ProbeSpec(
        symbol="enforce_url_grounding",
        module="core.agent_runtime.action_honesty_validator",
        cluster="honesty",
        observability="observable",
        evidence=(
            "The claimant behind route=unfetched_url_claim_blocked: it is the code that writes "
            "`output['route_reason'] = 'unfetched_url_claim_blocked'` "
            "(action_honesty_validator.py:970), which is why wrapping it observes that route being "
            "stamped rather than inferring it. Its only real caller, core/web/api/runtime.py:1507, "
            "is a function-local `from core.agent_runtime.action_honesty_validator import "
            "enforce_final_action_honesty, enforce_url_grounding`; structurally confirmed that "
            "core.web.api.runtime has no top-level attribute of this name."
        ),
    ),
    ProbeSpec(
        symbol="enforce_final_action_honesty",
        module="core.agent_runtime.action_honesty_validator",
        cluster="honesty",
        observability="partial",
        evidence=(
            "The claimant behind route=forbidden_term_blocked (:583) and "
            "route=false_action_claim_blocked. OBSERVABLE via core/web/api/runtime.py:1507 "
            "(function-local, structurally confirmed). BLIND via core/web/api/service.py:12, a "
            "module-top-level import that holds its own bound copy -- structurally confirmed, in "
            "also_patch."
        ),
        also_patch=("core.web.api.service",),
    ),
    ProbeSpec(
        symbol="dispatch_post",
        module="core.web.api.service",
        cluster="turn_boundary",
        observability="blind_without_also_patch",
        evidence=(
            "NOT a claimant -- the HTTP turn BOUNDARY, registered so an in-daemon trace can be "
            "segmented per turn by the request's own `turn_id` instead of by timestamp gaps. Its "
            "`body` carries turn_id/session_id/model and its ApiResponse carries the final answer "
            "and provenance, so wrapping it puts the authoritative 'what did this turn actually "
            "answer, on what route' in the SAME log as the claim sequence. Structurally confirmed "
            "BLIND on the defining module: apps/vool_api_server.py:81 imports it at module top "
            "level (apps.vool_api_server has its own persistent attribute of this name)."
        ),
        also_patch=("apps.vool_api_server",),
    ),
)

CLUSTERS: tuple[str, ...] = tuple(sorted({spec.cluster for spec in REGISTRY}))

#: Common parameter names probes use for "the text this call is about", checked in this priority
#: order before falling back to the first positional argument. Needed because e.g.
#: `record_slice_answer(source_context, *, text=...)` carries the meaningful text in a keyword,
#: not in `args[0]`.
_PROMPT_KWARG_NAMES: tuple[str, ...] = (
    "text", "user_text", "user_input", "raw_user_input", "query", "prompt", "lowered",
)


def _extract_prompt(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    for name in _PROMPT_KWARG_NAMES:
        if name in kwargs and kwargs[name] is not None:
            return str(kwargs[name])
    if args:
        return str(args[0])
    if kwargs:
        return str(next(iter(kwargs.values())))
    return ""


@dataclass(frozen=True)
class ClaimRecord:
    """`(symbol, module, called_with_prompt_prefix, returned_truthy, repr_of_return_head)`, plus
    enough extra context (`cluster`, `patched_module` when it differs from the registry's
    canonical `module`, `timestamp`) to make an ordered trace actually readable."""

    symbol: str
    module: str
    prompt_prefix: str
    returned_truthy: bool
    return_repr: str
    cluster: str = ""
    patched_module: str = ""
    timestamp: float = field(default_factory=time.monotonic)


class ClaimMonitor:
    """Installed wrappers + the ordered call log they fill in. Read `.calls` any time; it grows
    live while installed."""

    def __init__(self, registry: tuple[ProbeSpec, ...] = REGISTRY) -> None:
        self.registry = registry
        self.calls: list[ClaimRecord] = []
        self._restores: list[Callable[[], None]] = []
        #: Modules/symbols the registry named but that could not be imported or found -- reported,
        #: never silently skipped. Each entry is (module_path, symbol, reason).
        self.install_failures: list[tuple[str, str, str]] = []

    def _wrap_one(self, module_path: str, symbol: str, spec: ProbeSpec) -> bool:
        try:
            mod = importlib.import_module(module_path)
        except Exception as exc:
            self.install_failures.append((module_path, symbol, f"import failed: {exc!r}"))
            return False
        original = getattr(mod, symbol, None)
        if original is None or not callable(original):
            self.install_failures.append(
                (module_path, symbol, f"no callable attribute {symbol!r} on {module_path}")
            )
            return False

        calls = self.calls

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            calls.append(
                ClaimRecord(
                    symbol=symbol,
                    module=spec.module,
                    prompt_prefix=_extract_prompt(args, kwargs)[:80],
                    returned_truthy=bool(result),
                    return_repr=repr(result)[:200],
                    cluster=spec.cluster,
                    patched_module=module_path,
                )
            )
            return result

        setattr(mod, symbol, wrapper)
        self._restores.append(lambda m=mod, s=symbol, o=original: setattr(m, s, o))
        return True

    def install(self) -> None:
        for spec in self.registry:
            self._wrap_one(spec.module, spec.symbol, spec)
            for extra_module in spec.also_patch:
                self._wrap_one(extra_module, spec.symbol, spec)

    def uninstall(self) -> None:
        for restore in reversed(self._restores):
            restore()
        self._restores.clear()

    def claimed(self) -> list[ClaimRecord]:
        """Every call that returned truthy, in the order it happened."""
        return [c for c in self.calls if c.returned_truthy]


@contextmanager
def monitor(registry: tuple[ProbeSpec, ...] = REGISTRY) -> Iterator[ClaimMonitor]:
    """Install logging wrappers around every REGISTRY probe (and its known `also_patch` copies)
    for the duration of the `with` block; `.calls` is the ordered trace.

    Wrappers always delegate to the original and return its real result unchanged -- installing
    this must not be observable in what the runtime actually does, only in `ClaimMonitor.calls`.
    Safe to nest/reuse: each call installs its own wrappers and restores the exact prior state on
    exit, including on an exception raised inside the block.
    """
    m = ClaimMonitor(registry)
    m.install()
    try:
        yield m
    finally:
        m.uninstall()


def _call(module_path: str, symbol: str, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(importlib.import_module(module_path), symbol)
    return fn(*args, **kwargs)


def _probe_apply_raw_output_contract(prompt: str) -> Any:
    mod = importlib.import_module("core.raw_output_contract")
    contract = mod.parse_raw_output_contract(prompt)
    if contract is None:
        return None
    return mod.apply_raw_output_contract(prompt, contract)


#: Per-symbol adapter: how to call this probe directly, over a bare prompt string, without a live
#: agent/server/session. Every one of the 16 required symbols turned out to be a pure-enough
#: function to call this way with synthesized arguments -- none NEEDED a live agent for a
#: meaningful (if not production-faithful) verdict. What this does NOT reproduce: the outer
#: gating some of these run inside in production (e.g. `looks_like_builder_request` itself checks
#: `is_deliberation`/`is_opted_out` first; `attribute()` still calls it as a whole, so that gating
#: is exercised -- but the REAL claim sequence's ORDER, and lanes that only fire after an earlier
#: lane has already run and mutated shared state, are a `monitor()`-around-real-code question, not
#: an `attribute()` one).
#:
#: NOTE (2026-08-17): the registry grew past the original 16 and NOT every entry has an adapter --
#: see the comment at the end of this dict for which are deliberately absent and why. `attribute()`
#: skips a spec with no adapter rather than faking one. That is also why `attribute()` alone was
#: never enough: it can only ask "does this predicate say yes", and the entries with no adapter are
#: exactly the ones (the dispatch emitter, the turn boundary, the honesty validators) that say who
#: actually WON the turn. `tests/claim_dispatch_tracer.py` is the surface for that question.
_DIRECT_CALL_ADAPTERS: dict[str, Callable[[str], Any]] = {
    "maybe_answer_currency_value": lambda p: _call(
        "core.currency_value_contract", "maybe_answer_currency_value", p, source_context=None
    ),
    "asks_for_dynamic_currency_value": lambda p: _call(
        "core.currency_value_contract", "asks_for_dynamic_currency_value", p
    ),
    "static_currency_identity_admitted": lambda p: _call(
        "core.currency_value_contract", "static_currency_identity_admitted", p
    ),
    "looks_like_grounded_price_lookup": lambda p: _call(
        "core.agent_runtime.fast_live_info_price", "looks_like_grounded_price_lookup", p
    ),
    "_recent_price_subject": lambda p: _call(
        "core.agent_runtime.fast_live_info_price",
        "_recent_price_subject",
        {"conversation_history": [{"role": "user", "content": p}]},
    ),
    "url_read_request": lambda p: _call(
        "core.agent_runtime.fast_paths_web", "url_read_request", p
    ),
    "looks_like_builder_request": lambda p: _call(
        "core.agent_runtime.fast_paths_builder", "looks_like_builder_request", p.lower()
    ),
    "is_build_instruction": lambda p: _call(
        "core.agent_runtime.build_request_intent", "is_build_instruction", p
    ),
    "is_opted_out": lambda p: _call(
        "core.agent_runtime.build_request_intent", "is_opted_out", p
    ),
    "looks_like_personalized_plan_request": lambda p: _call(
        "core.agent_runtime.fast_paths_companion", "looks_like_personalized_plan_request", p.lower()
    ),
    "turn_may_hold_several_requests": lambda p: _call(
        "core.agent_runtime.turn_planner", "turn_may_hold_several_requests", p
    ),
    "build_live_data_plan": lambda p: _call(
        "core.agent_runtime.live_data_plan",
        "build_live_data_plan",
        p,
        plan_id="monitor-probe",
        attempt_id="monitor-probe",
        source_context=None,
    ),
    "parse_raw_output_contract": lambda p: _call(
        "core.raw_output_contract", "parse_raw_output_contract", p
    ),
    "apply_raw_output_contract": _probe_apply_raw_output_contract,
    "intake_request_text": lambda p: _call(
        "core.within_turn_retraction", "intake_request_text", p
    ),
    "record_slice_answer": lambda p: _call(
        "core.agent_runtime.answer_coverage",
        "record_slice_answer",
        None,
        text=p,
        family="claim_sequence_monitor_probe",
        response="",
        reason="direct_call_probe",
    ),
    # Added with the 2026-08-17 registry entries. Only the three that are pure functions of a bare
    # prompt get an adapter; the rest (coverage_for needs a family, fast_path_result needs a live
    # agent + session, plan_conductor_turn needs a plan context, the honesty validators need a
    # result dict + fetch-attempt count, dispatch_post needs a runtime) are deliberately absent, so
    # `attribute()` skips them and only `monitor()`/`trace()` around real code observes them.
    "currency_fast_path": lambda p: _call(
        "core.agent_runtime.fast_paths_currency", "currency_fast_path", p
    ),
    "analyze_retrieval_constraints": lambda p: _call(
        "core.retrieval_constraints", "analyze_retrieval_constraints", p
    ),
    "live_request_after_retraction": lambda p: _call(
        "core.within_turn_retraction", "live_request_after_retraction", p
    ),
}


def attribute(prompt: str) -> list[ClaimRecord]:
    """Direct, agent-free triage over `prompt`: call every registry probe's own defining-module
    callable in REGISTRY order and record what happened. NOT a simulation of the real turn's claim
    sequence -- it is always calling through the defining module (never through a blind copy), and
    it skips whatever the real front door's own earlier gates would have done first. It answers a
    narrower, useful question instead: "of these 16 pure predicates, which say yes to this text at
    all?" -- with the real per-probe input contract respected (source_context shape, keyword names,
    etc.), not a fake signature. All 16 have an adapter; none is skipped here, since none required
    live agent/session state to produce a real (if not production-faithful) verdict from its own
    defining module.
    """
    with monitor() as m:
        for spec in REGISTRY:
            adapter = _DIRECT_CALL_ADAPTERS.get(spec.symbol)
            if adapter is None:
                continue
            try:
                adapter(prompt)
            except Exception as exc:
                m.calls.append(
                    ClaimRecord(
                        symbol=spec.symbol,
                        module=spec.module,
                        prompt_prefix=prompt[:80],
                        returned_truthy=False,
                        return_repr=f"<adapter raised {type(exc).__name__}: {exc}>",
                        cluster=spec.cluster,
                        patched_module=spec.module,
                    )
                )
    return m.calls


def report(prompt: str) -> str:
    """Human-readable ordered trace for `prompt` -- the actual debugger surface this module exists
    to provide. Prints and returns the same text."""

    calls = attribute(prompt)
    lines = [
        f"claim_sequence_monitor.report() for prompt: {prompt!r}",
        "=" * 78,
    ]
    if not calls:
        lines.append("(no registry probe produced a call for this prompt)")
    else:
        for i, c in enumerate(calls, 1):
            verdict = "CLAIMED " if c.returned_truthy else "declined"
            lines.append(
                f"[{i:2d}] {verdict}  {c.symbol:38s} ({c.cluster:12s}) -> {c.return_repr}"
            )
    claimed = [c for c in calls if c.returned_truthy]
    lines.append("-" * 78)
    if claimed:
        names = ", ".join(f"{c.symbol} ({c.cluster})" for c in claimed)
        lines.append(f"Direct-predicate claim(s): {names}")
    else:
        lines.append("No registry probe claims this prompt as a direct predicate.")
    lines.append("")
    lines.append("Observability caveats (patching the defining module alone is NOT enough for"
                  " these; monitor() already patches the also_patch targets shown):")
    for spec in REGISTRY:
        if spec.observability != "observable":
            extra = f" also_patch={spec.also_patch}" if spec.also_patch else ""
            lines.append(f"  - {spec.symbol} [{spec.observability}]{extra}")
    text = "\n".join(lines)
    print(text)
    return text
