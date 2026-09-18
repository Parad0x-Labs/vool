# VOOL Runtime Architecture Contract

**Status:** authoritative for work continuing from branch `fix/canonical-obligation-floor`.
**Purpose:** a strong implementation agent should be able to repair the remaining seams without
rediscovering this architecture or reintroducing the whack-a-mole failure mode. Everything here
was measured against the live repository; the commit series on this branch is the worked example.

---

## 1. The actual runtime shape (measured, not intended)

```
POST /api/chat  (core/web/api/service.py — strips reserved trust keys)
  -> core/web/api/runtime.py  _run_agent_locked
       |  4 pre-agent exits: web0 grounding / assistant-identity (arbitrated) /
       |  private-memory recall (arbitrated since this branch) / post-agent finalize
       v
  apps/vool_agent.py  run_once  — EIGHT claiming stages, in order:
       1. attempt-followup gate (deterministic, terminates)
       2. currency-value contract (terminates)
       3. closed semantic contracts -> early frontdoor (terminates)
       4. CONDUCTOR  plan_conductor_turn -> run_conductor_plan
          -> reduce_execution_report -> ConductorClaimGate (monotonic claim)
       5. live-data typed plan  (build_live_data_plan -> run_live_data_plan)
       6. generic planner  (core/agent_runtime/turn_planner.py)
       7. frontdoor  (core/agent_runtime/turn_frontdoor.py — ~39 maybe_handle_*
          claimants, ~73 terminating return sites)
       8. turn_dispatch task bundle -> _execute_grounded_turn (model lane)
```

Tool-call authority (adversarially re-measured 2026-08-19 at cb45583c): `decide_tool_call` is the
mode/approval decider ONLY on the model-intent seam — every model-emitted tool call converges on
`core/tool_intent_executor.py` (which also runs the FORBIDDEN gate and semantic preflight), and
the conductor's tool execution reaches that seam through `NodeContext.run_tool_intent`. Also
consumed by `live_data_plan.py` and `core/semantic/admission.py`. It is conditional on
`mode_policy_is_active` (false when the request carries no mode; fallback MANUAL).
The deterministic fast paths are gated at CLAIM time (`action_forbidden` on the frontdoor
claimants), NOT at call time: Plan/Review modes are invisible to them (no `OperatingMode`
reference in `turn_frontdoor.py`). `core/orchestration/executor.py` runs a SECOND permission
system (envelope capability ids) — do not confuse them. Conductor fetch operations
(weather/market/fx/place/research) do NOT use the tool seam: they fetch under `_turn_forbids`
plus `remote_fetch_policy.open_remote`, which is the only socket door.
Remote-fetch authority: `open_remote`; `core/execution/web_tools.py:41` additionally gates on the
process-global fallback switch (known, deliberately left).

## 2. Which state is authoritative

| Question | The one authority | Module |
|---|---|---|
| What did the user ask for (conductor turns) | `RequirementLedger` + `CanonicalObligations` | `core/conductor/requirements.py`, `core/conductor/obligations.py` |
| What a turn PROVED about itself (roles, polarity) | `SemanticTurnProof` | `core/conductor/semantic_proof.py` (`prove_turn`, formal grammar + bounded proposer) |
| Whether a deterministic lane may END a turn | `coverage_for` / `claim_may_preempt_turn` over ONE registry | `core/agent_runtime/answer_coverage.py` (`_PROBES` + machine families adapted from `intent_claims._PROBE_FAMILIES`) |
| Which machine/tool family owns a clause | `probe_claims` | `core/agent_runtime/intent_claims.py` |
| Whether text authorizes a market mention | `mention_is_market_authorized` | `core/semantic_claim_authority.py` |
| Alias -> canonical asset identity | curated tables + rank-bounded coin index, joined at | `core/agent_runtime/live_data_plan.py::_resolve_price_alias` |
| A live-data turn's terminal state | `reconcile_attempt_lifecycle` over persisted subtask rows, carried onto the result via `fulfillment_outcome_from_attempt_lifecycle` | `core/runtime_continuity.py`, `core/runtime_task_outcome.py` |
| A model's residency/heavy-eligibility size | `model_total_parameter_billions` (metadata-first); inference-cost questions use `model_parameter_billions` (active count) | `core/local_model_bundles.py` |
| A conductor turn's product verdict | `reduce_execution_report` -> `ProductDecision` (immutable, monotonic claim) | `core/conductor/product_decision.py` |
| Tool permission per call | `decide_tool_call` | `core/mode_permission_policy.py` |
| Whether private-memory recall claims a clause | `looks_like_private_memory_recall` | `core/memory_recall_intent.py` (single definition; `core/web/api/runtime.py` aliases it) |

## 3. Obligation-creating modules (closed set)

Chat-turn obligations/work items may be created ONLY by:

- `core/conductor/planner.py::build_plan_from_clauses` (conductor plans, incl. requirement-projected nodes)
- `core/agent_runtime/live_data_plan.py::build_live_data_plan` (typed live-data subtasks)
- `core/agent_runtime/turn_planner.py` (generic multi-request plans)

Fourth minter, OUTSIDE the chat-obligation sense but live in production: `core/task_decomposer.py`
mints mesh/order-book work items, reachable from a chat turn via
`core/agent_runtime/turn_reasoning.py:~552` (gated by `should_decompose` + budget) and from the
daemon. It does not feed chat finality; do not extend it to.

Canonicalization of identities happens ONLY at:

- `_resolve_price_alias` (market: alias -> canonical id; static tables + coin index)
- `core/semantic_claim_authority.py` (market mention authorization)
- `core/conductor/registry.py::realize_subject` (conductor subjects)

## 4. Finality owners

- Conductor turns: `ProductDecision` via `ConductorClaimGate` — monotonic; post-claim faults become
  `CLAIMED_INTEGRITY_FAILURE`, never a silent fall-through. Persistence reads the decision verbatim
  (`terminal_fulfillment_outcome` reads `conductor_product_decision` first).
- Live-data turns: `finalize_runtime_attempt` / `reconcile_attempt_lifecycle` over persisted
  subtask rows — the ATTEMPT STORE is canonical (PARTIAL_SUCCESS persists per subtask), and as of
  2026-08-19 both live-data arms carry that verdict onto the result as an explicit
  `fulfillment_outcome`, which `terminal_fulfillment_outcome` reads BEFORE the prose fallback.
  Prose no longer upgrades a partial live-data turn to FULFILLED.
- Fast-path turns: `_fast_path_result` + `enforce_final_action_honesty` guard.
- A lane may terminate a turn only when `coverage_for(text, family)` is `whole_turn` (or the turn
  has a single clause). Slice answers travel via `record_slice_answer` and are composed later.
  KNOWN UNGATED EXITS (adversarial census, 2026-08-19): the web0/null-registration pre-agent exit
  (`core/web/api/runtime.py`, first of the four); the live-data lane in `apps/vool_agent.py`
  (no coverage check — a non-live sibling like "…and write a poem about it" is dropped whenever the
  model-dependent conductor declines); and roughly two-thirds of the ~29 frontdoor exits
  (date_time, credit_status, image_generation — which also absorbs sibling text into the prompt —
  pdf_read and url_read are now `action_forbidden`-gated at least). The frontdoor's gated exits
  are: workspace_audit, currency, receipt_location, live_info (coverage) plus the
  `mixed_turn`-gated group.

## 5. Invariants that must hold before a turn terminates

1. **Obligation identity.** One source mention -> one semantic obligation. Canonicalization may
   change representation, never cardinality. (Repaired: `_residual_mentions` — residual/unsupported
   extraction runs over mentions, and spans already owned by canonical resolutions are consumed,
   never re-minted or fused.)
2. **Obligation conservation.** Finality reports over every planned obligation; an unaccounted
   obligation blocks a success verdict.
3. **Plan fidelity before execution success.** The plan is the unit of truth; attempt success is
   necessary, not sufficient.
4. **One terminal verdict.** The same obligation cannot be both fulfilled and unresolved under a
   raw alias. (The refused-clause-vs-projected-node duplication was repaired by letting a proven
   typed frame outrank the clause-kind classifier in `core/conductor/planner.py::_resolve_clause`.)
5. **Partial claimants never finalize.** Every whole-turn termination site is gated on the shared
   coverage arbitration — including, as of this branch, the private-memory pre-agent exit.

## 6. Remaining seams (repair independently, in any order)

| Seam | Earliest boundary | Repair type |
|---|---|---|
| Water-temperature veto granularity | `_live_data_classification` (`core/execution_requirements.py:129`): whole-message boolean vetoes weather when a water clause coexists. Both the classifier and `live_data_plan`'s weather enumeration must read air-weather-eligible clauses, one shared helper in `core/measurement_medium.py` | ownership consolidation |
| `turn_slices` vs `turn_ir` splitter split + FX multi-target ("and EUR" drops) | `core/agent_runtime/answer_coverage.py::_split_spans` (sentence grammar) vs `core/turn_ir.py` (request grammar) — measured 29/337 corpus divergence, DIFFERENT semantics; do not unify blindly. FX multi-target: `core/currency_intent.py::fx_conversion_intent` keeps one target | parser replacement (needs its own measured run) |
| Currency whole-turn reparse leak | REPAIRED 2026-08-19: a whole-turn-currency multi-clause turn now answers EVERY owned slice deterministically; the per-slice arm is the fallback when any clause cannot be answered (`turn_frontdoor.py:1122`+) | done |
| Live-data turn-trace finality vs attempt store | REPAIRED 2026-08-19: both live-data arms set an explicit `fulfillment_outcome` from the finalized attempt lifecycle (`fulfillment_outcome_from_attempt_lifecycle`), which the outcome authority reads before any prose fallback | done |
| Local-fact backstop lexical authority ("can I drive from Rome to Paris") | REPAIRED 2026-08-19: `\b[a-z] drive\b` matched the pronoun-verb "i drive"; the drive-letter arm now excludes the two one-letter English words (closed-class, not domain vocabulary) | done |
| Epistemic authority (stable vs needs-retrieval) | MEASURED 2026-08-19: three lexical authorities (`requirements_for`, `_live_data_classification`, `live_info_mode`) can ALL miss an explicit freshness ask ("what is diesel selling for in Vilnius now?" → DIRECT, tools-less). No bounded lexical repair exists — the miss is open-vocabulary. Target: a per-obligation epistemic state (stable / retrieval-required / retrieval-optional / unavailable-under-mode / subject-unresolved) proposed through the EXISTING bounded proposer seam (`core/conductor/semantic_proof.py`) and owned by the runtime; lexical tables demoted to fast-path proposals that may only accelerate proven closed cases | missing capability (next run, use the proposer seam — no new framework) |
| Model identity/size for residency vs inference cost | REPAIRED 2026-08-19: `model_total_parameter_billions` (metadata-first, MoE-product, largest-token fallback) now owns residency/heavy-lane eligibility in both router gates; `model_parameter_billions` (active count) keeps inference-cost lanes. A pinned MoE name yields one number per question — the flag and the gate agree (`memory_first_router.py`, `core/local_model_bundles.py`). The frontdoor's literal heavy-marker block list (`fast_paths_utility.py`) and the user-text size scan in `_explicit_heavy_requested` are pinned product behavior (prose "use the 70b model" is a tested contract), NOT repaired — the remaining surface is that prose, not the parsers | done |
| Live-data lane ungated + sibling loss | `apps/vool_agent.py::_maybe_answer_live_data_turn` — no coverage/mixed check; sibling clauses outside registered families are dropped when the conductor declines. Also `_named_live_data_entity_count` (~:1110) re-extracts entities with independent recognizers to pick single vs multipart arms | ownership consolidation (needs registry coverage for the sibling families first) |
| Ungated pre-agent web0 exit | `core/web/api/runtime.py` first `finalize_turn_trace` (web0/null grounding) has no coverage gate — fires on mixed input (measured) | ownership consolidation (small) |
| Image-generation prompt contamination | `turn_frontdoor.py:~1546`: ungated exit; sibling clause text is absorbed into the image prompt (measured) | ownership consolidation (small) |
| Live-data turn-trace finality vs attempt store | `terminal_fulfillment_outcome` default-FULFILLED arm; the lane never sets explicit `fulfillment_outcome` from the attempt lifecycle | accounting change (small) |
| Weather bare-space list fusion ("kaunas riga") | `tools/web/web_research.py::_split_weather_candidates` — needs a place-membership authority (gazetteer/geocoder); do NOT split without one ("New York" is the adversarial case) | missing capability |
| Open-world entity grounding (Toyota Touareg / iPhone Galaxy S5) | model/research lane has NO unresolved-subject gate; `UNRESOLVED_SUBJECT` / `Establishment.AMBIGUOUS` exist as types (`core/conductor/realization.py`, `core/semantic/preflight.py`) with no consumer on that path. Cheapest honest version: the model lane declares the subjects it will answer; the runtime compares against the requested subject spans; mismatch -> typed refusal. Requires a way to ESTABLISH requested subjects — currently missing | missing capability (candidate: declare-back contract; no dictionaries) |
| Plan-vs-attempt observability | truth already canonical (`runtime_attempts.lifecycle_state` + `runtime_attempt_subtasks`); Activity/Agents counts attempts, not obligations. Move the finality READ to plan-land; no new store | accounting change |
| Provider malformed-response retry | `MALFORMED_PROVIDER_RESPONSE` classified `retryable=false` though immediate retry succeeds — misclassification seam in the provider adapter/router | adapter hardening |
| Brave BYOK acceptance | provider-settings write path (separate product bug; do not conflate with research architecture) | adapter hardening |

## 7. Behaviors that MUST NOT be reintroduced

Enforcement level is honest, measured 2026-08-19: (A) = impossible/enforced by code,
(B) = guarded by a test that fails when violated, (C) = convention only — you CAN violate it
without any test failing; the review burden is on you.

- (B) **A second claimant registry.** The arbitration reads `answer_coverage._PROBES` (with the
  machine families adapted from `intent_claims._PROBE_FAMILIES`). A new deterministic family is
  registered THERE — never as a private detector a lane consults alone. Guarded by the pinning
  tests' registry assertions; NOT mechanically prevented from a third registry existing.
- (C) **A claimant that terminates without coverage arbitration.** Convention only — ~two-thirds
  of frontdoor exits and the live-data lane are ungated today (see section 4). New exits MUST be
  gated; do not treat the existing ungated ones as precedent.
- (C) **Reparsing raw text for item identity.** Convention only; the currency whole-turn branch
  violates it today (section 6). New consumers must consume the canonical identity.
- (C) **A boolean used as whole-turn authority for a per-clause fact.**
- (A, conductor only) **`except Exception: return None` on an owning lane's claim path** — the
  `ConductorClaimGate` makes post-claim un-claiming a raised `ClaimViolationError`. Everywhere
  else this rule is (C).
- (B) **Model-proposed names outranking runtime-proven claims.** `outranks_planner_naming` and the
  proven-frame override in `_resolve_clause` have sabotage tests
  (`tests/test_a_machine_clause_binds_the_machine_capability.py`,
  `tests/test_a_proven_frame_outranks_the_kind_classifier.py`).
- (C) **Sabotage-unaware tests.** Convention; every seam test on this branch carries one.

## 8. How to work a seam (the method this branch used)

1. Reproduce at the boundary with an in-process measurement (no model needed for plan/extraction
   stages; `build_live_data_plan` / `build_plan_from_clauses` accept injected seams).
2. State MEASURED FACT / earliest wrong decision / stronger-ignored-state before editing.
3. Falsify against the frozen corpus (`ops/semantic_phase0_frozen_corpus.json` + the claim census):
   the repair may only ever move verdicts fail-closed; count flips.
4. Repair the ownership boundary; do not add a framework beside the existing one.
5. Regression family: human wording + paraphrases + sloppy variants + negative controls +
   adversarial near-miss + a sabotage test.
6. Compare the affected selection against the same selection at baseline in a detached worktree —
   this repo has order-dependent flaky tests (~40 in the wide selection) that pre-exist; never
   attribute them to your change without the baseline comparison.
