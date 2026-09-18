# VOOL gauntlet

Deterministic, local-first acceptance checks for the VOOL runtime, grouped by the
capability they defend. The goal: a green build means the runtime survived a real
gauntlet — memory, tool loop, model routing, wallet/x402 safety, prompt-injection
containment — not that a cloud judge liked the output.

## Principles

1. **Deterministic first.** The default lane runs with **no live model at all**.
   The agent loop is driven through its real seams with scripted
   `ModelExecutionDecision`s (the same pattern as `tests/test_runtime_continuity.py`),
   and gates are exercised with adversarial inputs directly. Acceptance is exact:
   a file exists, a spend is blocked, a status string matches — never "the judge
   approved."
2. **Real interfaces only.** Every test drives a function/route that exists today.
   Where a spec item has no implementation, we **pin the gap as a canary** instead
   of testing fiction (see "Honest gap pins" below).
3. **Safety is zero-tolerance.** Tests marked `safety` (money / consent /
   destructive actions) must stay 100% green to ship.

## Markers (registered in `pyproject.toml`)

| marker | meaning |
| --- | --- |
| `gauntlet` | deterministic — no live model, runs in the default CI lane |
| `gauntlet_live` | needs a live local Ollama; opt in with `VOOL_ALLOW_LIVE_OLLAMA_TESTS=1` (or `VOOL_ALPHA_LIVE_SOAK=1`) |
| `safety` | zero-tolerance money/consent/destructive gate |

Run the deterministic gauntlet:

```
py -m pytest tests/gauntlet -q
```

Run only the zero-tolerance safety gate:

```
py -m pytest -m safety -q
```

The deterministic lane is **142 tests** (0 need a model). CI must exclude the live
lane explicitly — the marker alone does not auto-skip:

```
py -m pytest tests/gauntlet -m "not gauntlet_live" -q   # the CI lane
```

The **live lane** runs on-box against the canonical model in
`config/acceptance/local_ollama_bundle_profile.json`, feeding real `run_once`
output into the deterministic scorers. It skips in default CI and only
runs when the env flag is set on the GPU machine.

PowerShell (this box):

```powershell
$env:VOOL_ALLOW_LIVE_OLLAMA_TESTS=1
$env:VOOL_OLLAMA_NUM_GPU=0  # canonical qwen3:8b CPU fallback on the 8 GB runner
py -m pytest tests/gauntlet -m gauntlet_live -q
$env:VOOL_ALLOW_LIVE_OLLAMA_TESTS=$null
$env:VOOL_OLLAMA_NUM_GPU=$null
```

bash / Git Bash:

```
VOOL_ALLOW_LIVE_OLLAMA_TESTS=1 py -m pytest tests/gauntlet -m gauntlet_live -q
```

Shared live harness in `tests/gauntlet/_live.py` (`require_live_provider` fails
loudly if the pinned model is missing — a silent swap does not pass green).

> On this Windows box, pytest + ruff live in the **system** Python 3.12 — invoke as
> `py -m pytest` / `py -m ruff`, not bare `python`.

## Suites in this wave

| file | category | what it pins |
| --- | --- | --- |
| `test_wallet_safety_gaps.py` | 9 — x402/wallet | HTTP `/stopx402` freezes the on-disk policy without invoking the model; consent never prompted when blocked; two honest gap pins (below); `pay.x402` fails safe + clamps to the 1.0 USDC ceiling |
| `test_prompt_injection_containment.py` | 11 — injection | a model that has already been injected still cannot cross a gate: workspace-escape write, protected-path move, publish self-authorization, spend self-authorization, and brake commands buried in content all refuse |
| `test_tool_loop_gates.py` | 5 + 6 — tool/agent loop | the real loop executes a tool and synthesizes; a real file lands on disk end-to-end; missing/unknown intents never fake success; loop-entry gate truth table |
| `test_contract_map_golden.py` | 14 + 5 — regression golden | freezes the 39-tool `(side_effect_class, approval_requirement)` table so any tier downgrade fails CI |
| `test_router_matrix.py` | 7 — model router | `resolve_fallback_budget_seconds` matrix; the stop/freeze control path resolves without ever invoking the model; message-complexity routing boundary |
| `test_memory_and_needle.py` | 3 + 4 — memory / needle | needle ranked top-k among 60 distractors (via `node_search_hybrid` + `stable_text_embedding`); BM25 exact-token recall; position-independence; `store_turn`→`inject_retrieved` round-trip resurfaces a fact; irrelevant query injects nothing; injection stays within the 350-token budget; **L3 retrieval is char-exact for decimals/dotted names; memory survives restart; keyed by agent-id not model; forgotten memory stays gone** |
| `test_context_continuity.py` | 1 — plot-loss / continuity | the `dialogue_sessions` state machine that keeps VOOL on-mission: goal persists across neutral turns; latest instruction wins (topic-shift archival); commitments become unresolved follow-ups; assistant-failure archives the topic; continuity is injected into the next turn; history hydrates from the session log; `canonical_runtime_transcript` source-tags; `memory_lifecycle_snapshot` score-floors + utility-skip |
| `test_memory_compression_structure.py` | 2 — L2 compression | `compress_if_needed` fires only above threshold and reduces to one `<context_summary>` + exactly `keep_recent` byte-identical tail, system prefix preserved; `token_estimate` = chars//4; the deterministic fallback emits the four-section shape and preserves each first sentence |
| `test_tool_honesty_matrix.py` | 4 — tool honesty | `sell.quote` is read-only and states no payment; malformed/unknown intents fail honestly with no leaked internals; `respond.direct` is a sentinel not a fake execution; `_should_fallback_after_tool_failure` truth table |

**Model router (category 7)** is deliberately *not* given a new file: `rank_providers`
self-heal, `model_execution_profile`, lane selection, and the verifier-before-done
execution are already well-covered by `tests/test_model_selection_local_heal.py`,
`tests/test_model_execution_layer.py`, and `tests/test_local_inference_autopilot.py`,
plus `test_router_matrix.py` here. Duplicating them would be padding.

## Honest gap pins

Some tests assert **current, imperfect behaviour on purpose**, so a future fix
turns a red canary instead of shipping silently. Each is labelled `_gap_pin` or
`_regression_pin` and names the follow-up. When the fix lands, the test flips and
must be updated in the same change:

- **Policy-file deletion reverts to permissive defaults** (clears an active
  freeze). The open "policy-exists anchor" hardening item will make a missing file
  fail closed. — `test_wallet_safety_gaps.py`
- **The USDC x402 lane does not consult `SpendPolicy.frozen`.** Only the SOL
  `.null`-registration lane is gated; the brake docstring's "every x402 spend"
  overclaims. — `test_wallet_safety_gaps.py`
- **The loop-entry gate over-triggers** on some benign conceptual prompts (a
  2026-07-04 eval finding). — `test_tool_loop_gates.py`
- **The dialogue goal snapshot corrupts exact values** — the input normalizer
  splits decimals (`0.037` → `0. 037`) and dotted names (`alice.null` → `alice.
  null`), so exact caps/domains must be recalled from the char-exact L3 memory
  layer, not `current_user_goal`. — `test_context_continuity.py`

## Missing capabilities — flagged, not tested as if they exist

The acceptance spec asked for behaviours that **have no implementation**. Rather
than write tests-against-fiction, these are recorded here with the concrete impl
requirement. Build first, then test:

- **Router uncertainty / schema-failure escalation does not exist.** A contract
  failure only stamps `validation_state='contract_failed'` (with `used_model=True`)
  — the router never re-ranks/retries/escalates to a larger model. *Impl:* add a
  re-route step in `_execute_provider_task` on `validation.ok==False` (or
  `confidence<threshold`). Until then, ~4 router escalation tests are blocked.
- **Entropy-driven escalation is inert.** `_ENTROPY_ESCALATION_THRESHOLD=0.35` is
  emitted into the plan dict and read by no decision logic. *Impl:* capture model
  logprobs/entropy at generation time and branch on it.
- **No typed cap/deadline slot** in `dialogue_sessions`. A planted "$50 cap / due
  Friday" survives only as free text in `current_user_goal` (which the normalizer
  corrupts) or as an L3 memory fact — there is no structural field to assert on.
  *Impl:* add columns/extractors for structural cap/deadline retention.
- **No JSON-mode / style response contract.** `apply_exact_response_control`
  (verbatim echo, ≤240-char single line) is the only output-mode control. *Impl:*
  add a structured-output contract before JSON-discipline acceptance can be tested.
- **Structural note:** the router *cannot* lose session state on a model switch —
  it holds no per-session model binding. "Switch preserves state" is a
  memory/chat-surface acceptance test keyed by `session_id`, not a router test.

## Corrections to the original gauntlet spec

The spec was mapped against the real code before writing anything. Items that do
**not** exist in this repo were dropped so we don't test fiction:

- There is **no** `plot-loss` API — continuity lives in
  `storage/dialogue_memory.py` (`dialogue_sessions`: `current_user_goal`,
  `commitments`, `unresolved_followups`) plus session summaries. Future memory
  suites target those.
- Tool names are namespaced (`workspace.write_file`, `machine.read_file`,
  `machine.inspect_specs`), not flat (`write_file`, `machine_specs`).
- Real autopilot lanes are `tiny / daily / deep / cloud / human` — not
  `fast / smart` (chat shorthand).
- **No** escalation/retry on tool-schema failure, and **no** entropy-based
  escalation, are wired — `validation_state` is recorded but not acted on.
- **No** prompt-injection *sanitizer* module exists, and none is required for the
  property that matters: injection tests target **policy-gate containment**, not a
  sanitizer.
- **No** codebase-RAG/vector index over repo files — "repo knowledge" is the
  deterministic `core/web0_project_grounding.py` Q&A plus docs.
- Real Ollama `num_ctx` stays at **4096** unless a verified A-E hardware bucket is persisted;
  `VOOL_ADAPTIVE_CONTEXT=0` explicitly opts out of adaptive sizing.
  in-window needle sweeps must respect that. Retrieval-based needle recall is
  deterministic via the hash-BoW embedding fallback and does not need a live model.

## Next waves (mapped, not yet written)

Ordered by value, all deterministic unless noted:

1. Context continuity (`dialogue_sessions` goal/commitment pipeline via
   `append_conversation_event`, cross-session summary pickup, offline L2
   compression structure with the summarizer monkeypatched).
2. Web0 + grounding (HTTP-joined `.null` register flow; grounding-vs-stale-memory
   precedence).
3. Installer seams — **after** reconciling the uncommitted install overlay noted in
   the 2026-07-04 eval (VRAM-sized model pick, driver-compat detector contract).
4. Env-gated **live** pack (`gauntlet_live`): pins the canonical acceptance
   profile, reuses the deterministic scorers, runs on-box against live
   Ollama — never in the default CI lane.

Done: wallet safety, injection containment, tool loop, contract golden, router,
memory + needle (this and the previous wave).
