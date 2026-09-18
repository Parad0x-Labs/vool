# Restart-first-turn P0 — evidence record (2026-09-02, worktree restart-first-turn-20260902)

## The bench case (source of the P0)

`build/served-reality-bench-20260902`, final run `sr-20260902-184211-e362a3e0` (commit 3f90873e,
`ops/served_reality/EVIDENCE.md`, findings JSON, raw run dir `/private/tmp/sr-final-evidence`):

> restart-continuity: post-restart first model turn failed once in five full runs
> (instability recorded, not averaged away)

The failing turn's own runtime events (wire-daemon.jsonl, session `openclaw:c4d7bee81059802ead14`):

```
15:45:00.591  Provider prewarm deferred; binding the API port first and warming in the background.
15:45:00.627  Atlas API server ready.            <- readiness published, prewarm still running
15:45:01.199  task_received    And now confirm you are back with: RESTART-SECOND-3302.
15:45:01.375  web_retrieval_started              <- the turn escalated to research
15:45:15.060  model_routing_started  Autopilot routed normalization_assist through the daily lane.
15:45:15.061  model_routing_failed   Autopilot blocked model execution before adapter invocation.
              rejection_reason = explicit_heavy_lane_unavailable
              ranked_candidates = [ollama-local:served-reality-stub, vllm-local:served-reality-stub]
15:45:15.090  task_completed  "I couldn't get a usable model response in this run..."
```

`model_call_id: null` — the turn never reached the (live, loopback-stub) model lane. The refusal
is the product's own honest stand-down (`core/agent_runtime/memory_runtime.py:129`).

## Reproduction at base 84bf8b6a

Harness: `ops/restart_repro` (`/tmp/vool-venv312/bin/python -m ops.restart_repro --cycles N`).
One cycle = fresh isolated home/workspace → boot (gate on /healthz) → memory turn → clean SIGTERM
restart → FIRST model-bound turn → second post-restart turn → full runtime-event attribution.
Model lanes point at a scripted loopback/LAN stub on this machine; external egress is contained
by the stub's CONNECT gate; `VOOL_CREDENTIAL_STORE=vault`; no shared daemon, no real user data.

At base the first post-restart turn fails with TWO faces (both first-turn-only; the second
post-restart turn recovers — matching the bench's "later turns recover"):

* **Face A — blocked before the adapter** (the bench's exact failure): the turn escalates to
  research, autopilot selects no provider, `explicit_heavy_lane_unavailable` aborts routing
  before any adapter call. Zero stub calls.
* **Face B — answered then withheld**: routing selects `vllm-local`, the adapter calls the stub,
  the marker answer is minted (`task_completed: confirmed back: RESTART-SECOND-3302`), and the
  grounding publication gate then refuses to serve it ("it needed current information, and
  nothing in what this turn retrieved backs it").

Strict success = HTTP 200 + marker present + the bytes are not one of the runtime's refusal
leads + ≥1 stub model call (Face B turns contain the marker INSIDE the refusal, which the bench's
containment-only assertion would have counted as a pass — recorded here as the harness bug it is).

**Strict 10-cycle result at base: first post-restart model turn FAILED 10/10**
(4 × Face A, 6 × Face B). Under the strict reading the SECOND post-restart turn also failed
10/10 (Face B) — the bench's "later turns recover" property does not survive to 84bf8b6a in
this environment; the earlier loose-instrument runs that showed recovery were the
marker-inside-refusal false positive. The first-turn defect remains first-in-sequence: its Face A
(pre-adapter abort, the bench's exact failure) never occurs on later turns, while Face B's
publication refusal hits every turn of this shape once the grounding lifecycle opens.

## Controls (both run at base, 3 cycles each)

1. **3-second post-readiness delay before the first turn** (`--first-turn-delay 3.0`): failures
   persist. The defect is NOT a boot-initialization time window; waiting longer does not fix it.
   A readiness gate that only waits for provider prewarm completion would NOT repair this case.
2. **Stub bind address**: binding the OpenAI-compatible stub on loopback makes every lane
   "locally certifiable" under `core/final_answer_authorship.py`, and the authorship law then
   refuses ALL turns (first and later) in a fresh home — a policy refusal unrelated to restart.
   Binding on the machine's LAN address keeps lanes in the operator-attested (remote-shaped)
   class, matching a real tether deployment; only that environment reproduces the bench case.

## MILESTONE 2 — the first wrong decision, recorded (ops/restart_repro/evidence/trace-cold-cycle)

`ops/restart_repro/trace_main.py` wraps the product server with gated probes
(`VOOL_RESTART_TRACE=1`, harness-only). One traced cold served request proved the whole chain
with owning functions (`[RTRACE]` lines, values not guesses):

1. **External text** (`run_agent@core/web/api/runtime.py` ENTER): "And now confirm you are back
   with: RESTART-SECOND-3302." — the text-only authority reads it `current=False
   ['stable_knowledge']` at EVERY call (agent.py:2819, turn_reasoning.py:31, roamer facade,
   checkpoint lane policy). **No current-information demand exists.**
2. **Restored state**: `augment_history_from_session_log` returned rows only (1→3 rows) —
   history informed nothing downstream; the restored-state theory is disproved.
3. **First wrong decision**: `adaptive_research@core/curiosity_roamer.py:466` →
   `_adaptive_research_decision` → `{'enabled': true, 'reason': 'chat_escalation',
   'escalated_from_chat': true}` — bare **"confirm"** in `_VERIFY_MARKERS` (and independently
   in `_ADAPTIVE_RESEARCH_MARKERS`) read a conversational verb as an external-verification
   demand. The pre-restart turn ("Remember this code...") contains no marker and never
   escalated.
4. **Face B minted**: `require_current_information_for_retrieval@core/execution_requirements.py:433`
   widened the canonical requirement (`current_info_signal:lane:adaptive_research`, before=False
   → after=True); the grounding lifecycle opened; publication later refused the marker answer.
5. **Face A minted**: the escalation composed the interpreted prompt as user text + "Grounding
   observations for this turn. ..." (`observation_prompt@core/agent_runtime/chat_surface.py`);
   `_interpreted_user_text` returned that COMPOSED string, and
   `_explicit_heavy_requested@core/local_inference_autopilot.py:276` parsed numbers inside the
   runtime's own scaffolding as model-size markers: `size_b=12.0 → heavy=False` on one turn,
   `size_b=89.0 → heavy=True → selected=None → explicit_heavy_lane_unavailable` on the next.
   The nondeterministic A/B flip was which numbers the observations JSON happened to carry.

**One defect, two faces**: runtime-authored state (research scaffolding) mutated the turn's
DEMAND ownership — both the canonical current-information requirement and the heavy-model
predicate read demand that existed only in the runtime's own additions.

## Repair (commit 6bc9543a), each seam owning its decision

* `HumanInputInterpretation.user_demand_text` — the user's own words carried separately from
  `normalized_text` when the interpretation was built from a composed prompt
  (`chat_surface` passes the pure `user_input`); `build_local_inference_autopilot_plan(
  user_demand_text=...)` reads it for `_explicit_heavy_requested`/`_requested_heavy_marker`
  (supplied by `memory_first_router`).
* `curiosity_roamer` vocabularies — bare "confirm" no longer escalates; "confirm that"/"confirm
  whether" (and every other marker) keep genuine claim verification.

No sleeps, no retries, no phrase regexes on refusal paths, no fail-open publication; the two
refusal messages themselves were not touched.

## Post-repair proofs

* **10/10 isolated cold-restart cycles GREEN** under strict wire assertions
  (`evidence/green-fix/`): correct marker bytes served, stub called exactly once per turn,
  second turn green; turns dropped from ~35s (wasted retrieval) to ~5s.
* **Pins** (`tests/test_restart_first_turn_demand_ownership.py`, commit c444a188 + wire pins):
  5 RED at base on the measured defect texts, 16 green post-fix; genuine heavy and
  current-info demands keep their routing; the external turn's frozen requirement record
  cannot be mutated by a composed-prompt reader.
* **Sabotages, each named RED then restored byte-exactly** (`git checkout -- <file>`):
  S1 bare "confirm" back in `_VERIFY_MARKERS` → 2 benign-turn pins RED; S2 router stops
  supplying the demand text → the wire-integrity pin RED (the first S2 attempt exposed a pin
  gap — nothing guarded the plumb — fixed by adding `test_the_demand_text_wire_is_intact_at_both_ends`).
* **Typed unavailability**: with the model stub dead, the first post-restart turn answers in
  ~15s with the honest typed refusal (no crash, no hang, no fabricated answer).
* **Packs**: focused (9 files, 209 passed + **5 failures byte-identical to base
  84bf8b6a** — pre-existing, zero regressions); bounded cumulative (8 more files,
  107 passed, 1 xfailed).

## Remaining edge (recorded, unattributed)

Concurrent-first turns: in 2 of 3 `ops.restart_repro.verify` runs, ONE of two simultaneous
first post-restart turns returned HTTP 500 with an empty body (alpha once, beta once; the
other turn clean; no crossover; one stub call per served turn). The pass2 daemon log
(`evidence/proofs-pass2/proofs.json` + preserved `vool_api.log` timeline) shows the 500
served 350ms after a fresh server process booted, with a process shutdown/boot pair
mid-phase — the pattern points at harness/daemon lifecycle interleaving rather than the turn
engine, but that is unproven. One verify run was fully green (both turns clean, 0.89s).
This edge needs a dedicated session with per-request process attribution.

## M1 checkpoint attribution (superseded by the Milestone 2 trace above)

Established by measurement:

* The first post-restart turn of a session with pre-restart state escalates to research
  (`escalated_from_chat: true`, grounding lifecycle opens, web retrieval runs) where the second
  post-restart turn of the same shape is served normally. Both failure faces live downstream of
  that first-turn escalation.
* `explicit_heavy: true` on the failing plan is NOT explained by the user text, the compressed
  context capsule, an `effort=smarter` request, or a client-supplied `autopilot_allow_heavy_model`
  (all four falsified against the recorded plan/texts). The producing seam is unresolved.
* Readiness publication ordering is real but not the whole defect: the port/healthz declare
  ready while deferred prewarm runs (boot log ordering in every cycle), yet the 3s-delay control
  proves the first-turn failure survives long after any prewarm has settled.

Hypothesis (unproven, next step): the first post-restart turn executes while restored session
state (persisted conversation rows / delivery sweep of ATTEMPTED_UNKNOWN finalizations) has not
settled, and its classification consults that incomplete state — hence first-turn-only
escalation. The fix boundary remains the one the P0 names: one authority must declare the
runtime ready for the first real turn only when that turn can safely execute (providers
initialized AND restored session state settled); callers must not guess. A provider-only
readiness event is insufficient — measured above.

## Artifacts

* `evidence/red-base/` — 10 isolated cold-restart cycles at base (per-cycle `cycle.json` with
  full event timelines, stub wire log, daemon logs; `summary.json`).
* Earlier controls: `/tmp/repro-events` (4 cycles, full events), `/tmp/repro-lan`,
  `/tmp/repro-lan-delay3` (delay control), `/tmp/repro-smoke*`.

No product defect was repaired in this lane yet; no fix is claimed. Product code at this commit
is byte-identical to base 84bf8b6a.
