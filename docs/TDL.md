# TDL — MASTER BUILD ORDER (the canonical VOOL queue)

Supersedes the previous technical-debt ledger (2026-03→07) that lived in this file —
that history is in git (`git log --follow docs/TDL.md`); its still-open items are
carried forward in §CARRY-FWD below, and its shipped/closed sections remain true
history. This file is now the ONE general TDL: deduped, importance-sorted,
phase-gated. The parked initiative TDL under
`workspace/TDL - GENERAL CROSS-CAPABILITY AUTOMATION (GLM. 5.3)/` is a child of
this queue (see PHASE 1, item A), not a rival.

**State anchors (verified 2026-08-30, repo HEAD `0b4c2eb4`, branch
`build/vool-cl-p10-20260829`):** `32ff8a6d` (Foundation+A7 freeze), `1efb8b29`
(A8 pass-001) and `b7f4475d` (A8 pass-002) are all ancestors of HEAD.
Re-verify before relying on any SHA claim.

## THE CHAIN (pin this on the wall)

```
A8 targeted proof → A8 hard freeze → A9 → (A10-ARCH seams) → A11 minimum UX
→ A13 served gauntlet → A14 hardening → RELEASE
```

Everything else runs parallel or after. The point of the order is to stop getting
distracted by shiny features halfway through stabilizing the core. At the end of
PHASE 0 the sentence we want to be able to say is: **"This exact build of VOOL is
boringly dependable."**

## HOW AGENTS USE THIS FILE (read before claiming ANY work)

1. `AGENT_HANDOVER.md` is the front door; THIS file is the queue. Read both before
   starting. Claim work in phase order — never jump the queue for something fun.
2. Check the ☠️ DON'T-DO LIST at the bottom before designing anything. It outranks
   every item above it.
2b. **Check `workspace/NOT-IMPLEMENTED-ARCHIVE/REGISTRY.md` before building
   anything** — the 2026-08-30 machine-wide archaeology found ~98 unwired modules
   in this checkout plus whole unmerged subsystems on the farm (repoops,
   routing_authority_v2, paged-memory Law 5, …). Revive-before-rebuild is law;
   `REGRESSION-LEADS.md` there maps the "worked 4 days ago, broken now" suspects.
3. Gate 0 before touching code: confirm repo path / branch / HEAD / clean status
   (the tree you are in is the tree you think it is).
4. An item leaves this file only with test evidence — never "module exists" and
   never self-certified builder claims where independent review is practical.
5. Close items by editing THIS file (mark ✅ with an evidence pointer), not by
   starting a parallel list somewhere else. One queue, one owner.
6. Skills/Plugins product surface is LOWEST priority (PHASE 3) by operator rule —
   EXCEPT the architecture seams it needs, which are pulled early (PHASE 1 item D)
   so nothing later becomes a second authority.

### Active final-integration ledger — 2026-09-03

Canonical writer: `build/vool-final-integration-20260903`, based on the clean
checkpoint `79f05f14`. Security/effect foundations, Command Registry, the unified
native code assistant, native/plugin/MCP skills, KAS/RepoOps and PB01 learning are
already in this branch.

- ✅ Long-input documents + the official VOOL working mark/layout are integrated.
  Exact code/table/Unicode/CRLF bytes, bounded model delivery, retry/restart,
  erasure, chip↔marker identity, cloud-text routing and the real browser palette
  remain green. The command census was regenerated for the document-list route:
  250 surfaces, zero `LEGACY_UNMIGRATED`.
- ✅ Confined artifact readers ACCEPTED (2026-09-03, run 2+3): the mixed
  video+host-display served RED closed at the routing admission gates
  (`7eedc19c` — one shared attachment-aware display authority; sabotages
  S1/S2 bite), reader dependencies declared with installer projections
  (`8320d6e9`), served M2 12/12 with the formerly strict-xfail "on screen" pin
  green, and the cumulative union green with one attributed inherited gap
  (`tests/test_composer_model_anchor.py`, 8 tests, fails identically at clean
  ancestors `1b3917f5` and `79f05f14` — now CLOSED by the chat-surface owner,
  see the composer-anchor entry below).
  Evidence: `validation-logs/final-integration-20260903/CUMULATIVE_UNION_R.md`.
- ✅ MEDIA/AUDIO/FORMAT product closure LANDED (2026-09-04, branch
  `build/media-reader-product-20260904`, base `5a46e35f`, local-only): the six
  document formats (XLSX/XLS/PPTX/RTF/ODT/EPUB) on the product line with
  `xlrd==2.0.1` projected into every packaging surface and a two-decoder
  packaging census; image OCR wired to served turns (staged once, secret-scanned,
  labeled text to every model, pixels still vision-gated); the surviving "on
  screen" routing collision closed (the disk-PDF lane claimed turns naming a
  bound attachment; stand-down reads `bound_attachment_names`, sabotages bite);
  bounded on-device audio transcription + composer dictation with typed
  absence everywhere (`audio_decoder_unavailable` at the door, dictation 503
  typed, picker never offers what cannot be read). Fast union 282 green,
  served drives 48 green (all families + corrupt/password/oversize/cancel/
  secret refusals), sabotage 9/9. Real speech transcription is stub-proven
  only — Speech access is not granted on the drive machine, so the served
  audio proof is the typed-unavailable path. Evidence:
  `validation-logs/media-reader-product-20260904/CHECKPOINT.md`.
- ✅ Composer model-anchor red CLOSED (2026-09-03, lane
  `composer-anchor-repair-20260903`, branch `fix/composer-anchor-repair-20260903`,
  base `97a4d2ea`): all 8 inherited failures re-produced at base (8F/7P) and
  classified — every one a STALE TEST CONTRACT, not a product defect. The
  selector moved to the header routing pill on 2026-08-28 (`9be2bb21`, the
  ux-pass1 design authority); the eight geometry tests kept measuring the
  header pill against composer geometry. Proven on the real page in Chromium
  over the full 42-case matrix: pill inside the header, zero header-sibling
  overlap, zero horizontal scroll, menu unclipped, everywhere. The family was
  corrected to name the current contracts (old→new ID mapping in its
  docstring) plus four new behavioral seam laws against captured requests.
  The seam itself needed ONE real repair: a pin restored from storage read as
  its raw provider id after reload although the friendly-name catalog had
  landed — `renderCloudModels` now feeds `CLOUD_MODEL_LABELS`. Sabotage checks
  bite (propagation break and repair revert each fail a named test). Cumulative
  lane union 262/262 green with zero console errors; four OTHER failures in the
  wider sweep (3 message-timestamps IDs, 1 API-server snapshot ID) fail
  identically at clean base `97a4d2ea` — inherited elsewhere, untouched.
  Evidence: `validation-logs/composer-anchor-repair-20260903/COMPOSER_ANCHOR_REPAIR_EVIDENCE.md`.
- ✅ Session portability INTEGRATED (2026-09-03): all 11 source commits from
  `f9d5259e` cherry-picked inspected; package byte-identical; suite 68/68;
  the census regression it caused was reconciled at the projection authority
  (`f98cce72`, 261 items, zero LEGACY_UNMIGRATED). Evidence:
  `validation-logs/final-integration-20260903/PORTABILITY_PROFILE_CHECKPOINTS.md`.
- ✅ Operator Profile semantic remainder CLOSED by inclusion (2026-09-03):
  every `ffa38448` law verified present (dedicated modules byte-identical to
  `a50350f8`); profile family 70/70; no code change.
- ✅ Liquefy cold-log projection INTEGRATED (2026-09-03): `ce445222` family
  landed with the encrypted CAS v2 preserved at the blackbox seam (additive
  post-durable sink; journal stays authority); liquefy 55/55 + blackbox 76/76;
  zstd remains the byte-exact default per the measured decision record.
- ✅ Checkpoint D — DB skill INTEGRATED (2026-09-03): `build/db-skill-20260903`
  @ `9271d6b0` landed (pack + native skill + owned packs) WITHOUT replaying its
  shared-core edits; integration closures: lifecycle install/verify/enable in
  fixtures and served rig, plugin-aware `skill.validate` through the canonical
  registry, demand matcher undoing the normalizer's shorthand rename inside
  dotted intents. DB packs 74/74, source served pack 3/3, served chat journey
  2/2; sabotage matrix red-by-name with byte-exact restores.
- ✅ Checkpoint E — chat export/copy INTEGRATED (2026-09-03):
  `build/chat-export-copy-20260903` @ `9ffbc7d3` reconciled by three-way merge
  (shared page/service kept, export surface added); export/copy 37/37 on the
  composition; real-browser served proofs re-run 31/0 and 18/0; sabotage
  matrix red-by-name (allocation bound, privacy gate) with byte-exact
  restores; census snapshot refreshed (+1: `http:GET:/api/chat/export`).
- ⛔ OPEN (integration blockers for chat mutation/resume, storage/chat-mode
  lane): a successful `resolve_approval` poisons later `/api/chat` turns on
  the daemon and post-restart chat turns alike with `sqlite3.OperationalError:
  disk I/O error` at `storage/db.py`; local-loop `capability.expand_family`
  reseat re-uses the initial catalog. Recorded with repros in
  `validation-logs/final-integration-20260903/DB_CHECKPOINT_D.md`.
- ✅ CLOSED (2026-09-04, `fix/sqlite-chat-lifecycle-p0-20260904`): the storage half of the
  blocker above is root-fixed — `storage/db.py` serves the default store PER USE (no
  thread-local pool). Measured chain: helper processes' last-close checkpoint+DELETE pulled
  live WAL generations out from under the daemon's idle pooled connections (shm fcntl state
  erodes under multithreaded churn), committed bytes landed in an unlinked WAL, and every
  fresh open failed process-wide with `disk I/O error` until restart. Served acceptance
  (approval 200 → same/unrelated/new-session turns + a real second daemon generation, all
  answering) ×3 stable; storage/migrations 126, DB packs 79 (checkpoint-D journey
  post-restart chat legs now green), approval 154+1 skip, chat 446, Blackbox 113, effect
  budgets 99; sabotage by exact base revert re-arms the defect by name. Evidence:
  `validation-logs/sqlite-chat-lifecycle-p0-20260904/STORAGE_CHAT_LIFECYCLE_REPAIR.md`.
  Residual uncertainty: multi-process behavior measured on macOS arm64/APFS + bundled
  SQLite 3.50.4 only.
- ⛔ OPEN (separate repair lane): the 8 composer-model-anchor failures,
  recorded by exact ID in `D_CUMULATIVE_UNION.md`.
- ⛔ No `.app` compilation, identifier migration, wallet/mobile landing or public
  repository publication until this checkpoint and its cumulative gates close.
- ✅ Native skill PRODUCT FAMILY INTEGRATED (2026-09-04, `build/native-skill-product-family-20260904`
  off `5a46e35f`): C05 conversational skill lifecycle (immutable version store + `skill.rollback`
  behind the install gate; approval-joined activation proven pending → resolve → exact replay;
  legacy installed skills now disable through the ONE store), C08 six expert lenses (bounded,
  typed, version-recorded; injection-proof), C19 explicit presentation formats through the
  existing response-constraint authority (explicit wins, prose default, never invent values,
  unsupported falls back to text/table; copy fidelity under node). Closed two inherited
  base-reds: the vool-database contract law and the stale 73-entry gauntlet golden
  (reconciled to the live 112-contract table). Served /api/chat proofs in
  `tests/test_skill_family_served_turns.py`; evidence:
  `validation-logs/native-skill-product-family-20260904/EVIDENCE.md`. Open inherited reds
  recorded there (pypdf guard, tiny-turn token ceilings, composer-model-anchor).


---

## 🔴 PHASE 0 (P0) — RELEASE-CRITICAL. ENTER NOW. EXIT = "boringly dependable".

**A. Finish A8 privacy convergence — targeted reproof, then the real freeze**
- Finish A8 at the pass-002 candidate `b7f4475d` (builder returned YES; what is
  due is the targeted independent reproof — 3–5 surgical workers, NOT another
  giant archaeology swarm; the failure surface is known).
- Mechanically re-run every known A8 killer: served leaks, resurrection/races,
  derivative erasure, replay identity, duplicate request IDs, digest oracles,
  fail-closed behavior, Foundation+A7 regression.
- Only after targeted proof passes: the REAL hard freeze — bundle + SHA256 +
  bundle verify + Finder open. Until then: NOT FROZEN.
- Do NOT reopen Foundation+A7 (frozen at `32ff8a6d`) unless someone produces an
  actual deterministic counterexample. Not for aesthetic cleanup. Ever.
- Residual A8 detail (folded from the old backlog): universal serve-time privacy
  gate (check A8 at read/serve, not just write); kill resurrection paths (sync,
  stale writers, checkpoints, old epochs, mirrors, derivatives); principal-scoped
  replay; complete erasure graph (.bak, Liquefy, facts/memory nodes, feedback
  snapshots, runtime events, checkpoints, legacy archives, task results/hive
  posts); strict digest privacy (no unsalted hash-dictionary oracle surviving
  erasure); request-ID ambiguity protection (duplicate IDs must not let an older
  AVAILABLE payload survive while newest is erased); production resume for
  incomplete erasure sweeps after restart/crash.

**B. A9 routing / context / retry convergence**
- ONE authoritative scoped routing plan per request: locality, permitted
  providers/models, cost ceiling, fallback, retry, explicit pin state. Privacy and
  locality are BINDING inputs to routing, not a secret-scanner guess; remove
  automatic "PUBLIC" assumptions for owner/local chat.
- Kill global/sticky routing contamination — one chat/project's routing decision
  must not silently poison another. Unify the two routing eligibility systems so
  ranker and broker cannot disagree about what may run.
- Prove local → cloud → local in the SAME real chat with context preserved and
  unrelated context isolated (release-proof gap to this day).
- Fix referential follow-ups structurally: "why did that fail?", "retry that exact
  request", "check the original message" — persist typed execution/failure
  outcomes (SubtaskOutcome + failure causality + execution identity) instead of
  reconstructing from assistant prose. Retry resolves the referenced execution,
  never a fresh search of the retry sentence. Do not overwrite
  current_user_goal with every latest sentence; stop hallucinated assistant
  content contaminating unresolved-followup state.
- Temporary-rule lifecycle: arm → consume → decrement → expire → cancel → survive
  restart; standalone constraints ("no more than five words" as its own turn)
  bind correctly; order-pollution tests (earlier messages/failed turns/retries
  must not consume or revive the wrong constraint).
- Current-turn payload/identifier preservation: exact identifiers, URLs, literal
  user text, "what did I say?" remain owned by the user payload, never replaced
  by model inference.
- Typed quantity/unit/referent cleanup (5G ≠ grams, 1125 sec, 1h30m) — typed
  parsing, NOT another regex pile.
- Terminal/refusal persistence reproof: refused/failed/no-answer must not become
  semantic content after retry, recovery or restart.
- Relevance-gate VOOL self-knowledge/bootstrap context (the wallet/x402 safety
  block must not sit at priority ~1 in every unrelated weather turn).
- Continuation-detection cleanup (stop misclassifying unrelated messages as
  continuations and vice versa).
- Real model-sufficiency telemetry: correctness × task family × tool validity ×
  TTFT × latency × warm/cold × cost — so Auto learns sufficiency instead of
  pretending benchmark rank = intelligence.

**C. Test-trust as a release blocker**
- Repair remaining false-green vectors: piped exit codes, `|| true`,
  continue-on-error, missing baselines exiting 0, unexpected skips, order
  pollution, mocked served boundaries.
- Unexpected-skip policy: a required live test silently SKIPPED fails the release
  gate unless explicitly allowed.
- Increase REAL served-product testing — a green internal/kernel suite is not
  enough; historically the suite could be green while the shipped app lost
  request content.
- Action-truth mutation tests: sabotage the effect path, prove the detector fails
  for the exact expected reason.

**D. A13 Served Reality Bench Wave 002**
- Reuse the nasty historical corpus against the ACTUAL served product: strict
  output, multipart, no-tools/web, identifiers, exact reads, temporary
  constraints, refusal, context switching — across model/provider/local/cloud,
  separating model failure from VOOL failure. (Wave-001 runner exists; Wave 002
  was never run. Historical kernel numbers are reference data only.)
- Mutation-sensitive proof on release-critical invariants: GREEN → break the real
  guard → RED for the correct reason → byte-identical restore → GREEN, plus prove
  the mutated path actually executed.
- Crash/race tests: cancellation, retry, stale writers, recovery, concurrent
  execution. Sequential happy-path tests do not close races.
- ✅ **M2 PLANNER-CHAIN REPAIR landed 2026-08-31 (`f12f869e`, arch-truth-r1 worktree):**
  the chain from the entry below was right and four operational truths under it were not.
  A **resumed turn wrote two user dialogue rows** — the re-interpretation of its restored
  request went through the persisting intake, so one external turn filed a second thing
  the user never said; `adapt_user_input` grows `record_user_turn`, and the resume now
  updates typed continuity state and carries the canonical turn id without minting a row.
  The **planner admitted a request as servable and the runtime then refused it**:
  `planned_subturn` short-circuited the live-data lane, so a planned sub-request answered
  "Live web lookup is disabled on this runtime" for a lookup the same message gets served
  when it arrives whole — that flag blocks recursive PLANNING, never an execution lane.
  **Two distinct tasks of one turn could not run at once**: `run_plan` runs an independent
  wave concurrently, but exclusivity was keyed on the chain root, so the second child's
  claim was refused and its lane answered "could not be retrieved". `runtime_attempts`
  gains a migration-backed `execution_slot` — `task:<index>:<sha256(request)[:16]>` for a
  planned task, `''` for the chain's ordinary rows — and the key becomes (chain,
  container-bit, slot): distinct tasks run concurrently, the same task still cannot execute
  twice. And a **follow-up recalled one child of a multi-child turn**; recall now reads
  every answer-bearing child of the chain, each contributing the generation it actually
  ran, while acting on an attempt (retry) stays bound to that attempt.
  Six pins in `tests/test_planner_chain_execution.py`, all six RED at the parent; four
  sabotages each red for their own pin, byte-identical restores; cumulative pack 794/16
  against the parent's 788/16 on the identical pack.
  **Two of the pins had to be corrected before they proved anything** — the concurrency pin
  first held both children at attempt CREATION (a fresh row is `RECEIVED`, which the index
  does not cover) and then counted rows (a refused claim leaves its row behind); both were
  found by watching the slot-removal sabotage leave the test green.
  **NOT claimed:** the production planner still runs its wave at `max_workers=1` — a
  measured local-model saturation ceiling recorded at that call site, not something this
  changed; what changed is that the database no longer forbids the concurrency, proven by
  driving `run_plan` at `max_workers=2`. A message whose parts are live-data is still
  claimed whole by the live-data lane before the planner is offered it, so the
  planner-with-live-data-children path is reached in production only when that lane
  declines the parent; the tests construct that condition at one named seam.
- ✅ **M2 TURN-CHAIN REPAIR landed 2026-08-31 (`9b644f3f`, arch-truth-r1 worktree):**
  closes what the entry below could only measure. One external turn owned TWO unchained
  attempt rows under TWO turn ids — the turn door's bookkeeping attempt (ingress turn id)
  and the answering lane's (a second uuid minted downstream inside `record_dialogue_turn`).
  The door closes its row last, so "the latest attempt in this session" returned the
  bookkeeping row, which carries no subtasks: a referential follow-up one turn later
  answered "No entities were recorded for that request" on the HTTP surface, while
  in-process turns hid the same defect by filing the door row under an EMPTY session.
  Repaired on three axes. **One turn id:** `record_dialogue_turn` takes the turn's
  canonical id instead of minting its own, the intake supplies the request's, and the
  ingress mints in that id space — so the dialogue row, the request and every attempt of
  the turn agree. **One chain:** `runtime_attempts` gains a migration-backed `attempt_role`
  (turn_root / answer / planner_task / retry, never backfilled); the door row is the chain
  ROOT, the answering lane joins it BY ROOT (not by parent — a parent means retry in that
  table), planner sub-tasks and retries hang off the same root, and the door row is filed
  under the request's session on every surface. **Structural resolution:** the three
  session-scoped attempt reads rank by role before recency, so a bookkeeping root cannot
  stand in for the row that answered and reordering `updated_at` cannot move the selection;
  untyped legacy rows rank with the answer-bearing ones, so an existing database resolves
  as before. Two invariants had to be repaired to keep holding: `ux_one_live_attempt` was
  keyed on `execution_id` alone, so a turn's own container row refused its lane's claim
  (the key now separates container from executors, and every row satisfying the old index
  satisfies the new one); and `_chain_retry_rearm_eligible` read attempts through the
  ledger connection instead of the continuity one, refusing every explicit retry of a
  terminal chain wherever the stores are configured apart. 9 pins in
  `tests/test_turn_attempt_chain.py`, all 8 behavioural ones RED at the parent; three
  sabotages (root link, newest-row selection, second dialogue id) each red for the named
  cause with byte-identical restores. Cumulative pack 713/16 against the parent's 703/17 on
  the identical pack — same failures except one the parent fails and this passes.
  **NOT fixed by that repair, measured** (both closed by the planner-chain repair above): a
  planned sub-turn could not reach the live-data lane, so an end-to-end planned turn
  recorded no sub-task attempt at all; and a resumed turn still wrote a second dialogue row
  for its re-interpretation.
- ✅ **M2 TURN-IDENTITY REPAIR landed 2026-08-31 (`54c078a5`, arch-truth-r1 worktree):**
  the repair below fixed the INNER runtime's ordering and left the layer above it
  inconsistent. `_r3_open_turn_execution` — which mints the turn's attempt row, claims
  it RUNNING, opens the L0 fence and mints the RSS obligation set — ran BEFORE the
  canonical request existed and derived request, turn and session identity itself. On
  every door-served turn its trigger turn id was a fresh `turn-<uuid>` unrelated to the
  request, whose own `turn_id` was EMPTY there: the HTTP door never stamps
  `_canonical_user_turn_id` (the runtime writes it later, from the persisted dialogue
  turn). And the planner sub-turn lane called `from_ingress` a second time, so one
  external user turn minted TWO "canonical" requests. Repaired: the request is minted
  at the top of `run_once` ahead of execution identity, the turn-id mint moved there
  with it (taking the caller-claimable `_canonical_user_turn_id` read off the door
  path), the execution seam takes the request as a required typed parameter and reads
  its request id / turn id / text off it — no `current_request_id()` re-read, no mint,
  one added fail-closed refusal for an unbound turn id — and the planner sub-turn mints
  nothing, running under the parent object with its task text as input (`PlannedTask`,
  the existing task contract). 7 pins in `tests/test_turn_identity_boundary.py`, 5 RED
  at the parent commit; sabotage A (seam re-derives) and B (planner remints) each red
  for the named cause, byte-identical restores green; cumulative pack 431/4 against the
  parent's 424/4 — same four pre-existing `execute_attempt_retry` failures — and a
  second sweep whose failure set is byte-identical to the parent's.
  **NOT fixed by that repair, and measured rather than assumed** (closed by the
  turn-chain repair above): the attempt ROW's session binding stayed the ingress override. Filing it under the request's session makes that row
  visible to `latest_runtime_attempt`, and since the turn door closes its attempt LAST
  it becomes the session's newest row — the next turn's referential follow-up then
  binds to the bookkeeping row and answers "No entities were recorded for that
  request". Driving two turns with a session override on BOTH, which is what
  `core/web/api/runtime.py` passes on every HTTP turn, reproduces that at the PARENT
  commit: it is a live HTTP-surface defect today, hidden from the suite only because an
  in-process turn passes no override. The root cause is that one turn owns two attempt
  rows with no chain between them; that belongs to the milestone that owns the attempt
  chain, and the measurement is pinned as the last test in the new file.
- ✅ **M2 TRUTH REPAIR landed 2026-08-31 (`c37a301f`, arch-truth-r1 worktree):** the
  slice-1 claim "TurnRequest at the intake" was timing-false — `run_once` constructed
  the typed request only AFTER `_run_once_inner` returned, so the whole legacy runtime
  routed, called models/tools, and built the answer before any contract existed, and
  production code wrote `TURN_REQUEST_KEY` that nothing consumed. Repaired: ONE
  construction point, server-derived, BEFORE the inner runtime begins (identities read
  from the same authorities — A0 request context, door-stamped/interior turn id,
  override/door-stamp/interior-mint session; the mint is pure, so no second authority);
  the same immutable object is handed to `_run_once_inner` as a REQUIRED typed
  parameter (isinstance-guarded — mechanically threaded, not a dict lookup) and is
  CONSUMED there (the turn's session now comes from the request; sabotage: reverting
  that line fails the consumption pin). The post-hoc construction is deleted; the
  planner sub-turn lane mints its own request through the same `from_ingress`; the
  checkpoint persistence boundary strips the live-turn key so no stringified contract
  lands in stored JSON. Timing/identity pins: request exists at inner ENTRY, explicit
  argument is the identical object, byte-exact text, server-derived trust, ids bound,
  forged inbound `turn_request` cannot survive ingress (RED pre-implementation: the
  forged string rode the turn into the inner runtime). Cumulative pack 102/102
  (turn_contract + answer_integrity + composition boundary); sabotages S1 (wiring
  removed) and S2 (consumption removed) both red for the named cause, byte-identical
  restores green. NOT claimed: TurnState remains post-turn; lane mediation,
  EffectReceipt, TurnPolicy, and effect ownership are untouched.
- ✅ **M5 slice 3 landed 2026-09-01 (`9cd5761e`+`31130588`) — then SAFE-STOPPED per
  operator amendment:** the command class receipts at the sandbox seam (all eight outcomes;
  simulate_only is a mechanically-distinct SIMULATED), and the frozen scoped TurnPolicy is the
  shared consult's typed input. Cumulative 126/23,222, zero deterministic candidate-only. M5
  REMAINS OPEN (remote-API/public-write/financial/provider/helper classes; the ad-hoc
  compositions; the mandatory bypass removal). No further slices started — awaiting operator
  review.
- ✅ **M5 slice 2 landed 2026-09-01 (`63ac7195`+`2f44cb4a`):** the filesystem
  effect class receipts at the machine-effect seam — both outcomes typed onto the
  turn's ONE receipt channel (shared with the fetch door; one authority across
  effect classes, pinned). Cumulative 127/23,218, zero deterministic candidate-only.
  Remaining M5: command/remote-API/public-write/financial/provider/helper classes;
  the scoped TurnPolicy object; the frontdoor's ad-hoc compositions.
- ✅ **M5 slice 1 landed 2026-09-01 (`a1a38fc7`+`4e81f31c`):** the one permission/effect
  gateway chosen by survey (decide_tool_call) with the network-fetch class normalized at
  the one outbound HTTP door — typed EffectReceipts for BOTH outcomes (denial is a
  recorded fact, not just an exception), the door consults the same gateway as the
  model-tool path (non-disagreement pinned), legacy veto preserved and receipted.
  Cumulative 130/23,212, zero deterministic candidate-only. Remaining M5: the other
  effect classes, the scoped TurnPolicy object, the frontdoor's ad-hoc compositions.
- ✅ **M4 slice 4 landed 2026-08-31 (`c3be8186`) — consult coverage complete:** the
  conductor consults the kernel before executing its plan, the frontdoor serve consults
  mediate() (rank 0, the structural law), and the AST-pinned structural test proves the
  serve guards guard. Every serving route and decline typed. Cumulative 130/23,207 with
  zero deterministic candidate-only (2 known turn_manager flakes + 2 install-profile
  env-draws, all with green-rerun evidence). M4's mediation architecture is complete;
  the cascade's deletion is now a coverage-provable step.
- ✅ **M4 slice 3 landed 2026-08-31 (`7789803e`+`aea2fff9`):** consult-before-serve
  on the live-data lane — the lane asks the kernel before EXECUTING its plan; a refused
  serve is a typed decline and the earlier lane's service stands. Also fixed the
  frontdoor's slice-local unit-id collision (claims now resolve against the turn's
  canonical set). Cumulative 126/23,208, ZERO slice-only. Remaining M4: the same
  consult on the conductor + frontdoor serves, typed declines on pre-agent exits,
  then cascade deletion.
- ✅ **M4 slice 2 landed 2026-08-31 (`21ac2b17`):** claim MEDIATION on the
  decide_claims seam — lanes consult the pure `mediate()` (registry rank only,
  arrival order irrelevant) at the one recorder every lane uses; a mediated-away
  claim lands as the typed refusal naming the superseder, so exactly one claim
  survives per unit at record time. Cumulative 126/23,205 with ZERO slice-only
  failures. Remaining M4: lanes consult mediation before SERVING (not just
  recording); decline reasons on every pre-agent exit; cascade deletion as
  mediation proves out.
- ✅ **M4 slice 1 landed 2026-08-31 (`2f2f97a0`+`b71a06b8`):** the one ordered lane
  registry + the kernel's claim decision — one pure decision per canonical obligation
  (claimed with owner / model_required for the fallback escalation / unclaimed as the
  named anomaly), computed at the spine from the lanes' own recorded proposals and
  carried on TurnState.claim_ledger. Contests resolve by registry order. Cumulative
  128/23,199, zero deterministic candidate-only. Remaining M4: claim mediation moves
  onto this seam; decline-reason recording everywhere; the cascade's implicit order
  deleted once mediated.
- ✅ **M3 slice 4 landed 2026-08-31 (`df279329`+`c272f3ef`) — M3 CLOSES:** the
  admission seam records the FALLBACK claim (no lane claimed → the serving route
  claims the whole set, typed and conserved; non-answer routes claim nothing), and
  the mint is enriched (exact spans test-pinned, prohibitions preserved not
  vanished, freshness from the stated recency phrase, output_constraint slot).
  Every route that can serve a turn now emits a typed proposal. Cumulative
  128/23,195 with zero deterministic candidate-only (two known turn_manager
  flakes). M4's lane registry builds directly on these proposals.
- ✅ **M3 slice 3 landed 2026-08-31 (`9736795c`+`f03364e6`):** the conductor lane
  consumes the canonical set — GEOMETRIC span-overlap binding (node clause_span x unit
  start/end), unbound units named, typed no-plan refusal, route literal displaced.
  Cumulative 127/23,190 with ZERO slice-only failures. M3 consumption now covers
  live-data, frontdoor, and conductor; remaining: model routing + mint enrichment.
- ✅ **M3 slice 2 landed 2026-08-31 (`1644d85b`):** the frontdoor live-info fast path
  consumes the canonical set — typed claim/decline at the flow seam (a disabled
  preflight is a typed DECISION, never a silent pre-answer exit), siblings named,
  route literals displaced to the recorded proposal. Cumulative 129/23,184, zero
  deterministic candidate-only (one known turn_manager flake). Remaining M3:
  conductor + model-routing consumption; mint enrichment.
- ✅ **M3 slice 1 landed 2026-08-31 (`311ddbd8`+`8c884f38`):** the live-data lane
  CONSUMES the canonical obligation set handed in at both call sites — unbound demands
  are NAMED on the plan and the proposal (never silently absent), the conservation law
  (claimed + unclaimed covers every unit) is test-enforced, and the claim/demand census
  reads zero divergence; conservation sabotage proven. Cumulative 129/23,181, zero
  deterministic candidate-only (one draw-only turn_manager flake, the file's fourth).
  Remaining M3: conductor + frontdoor + model routing consumption; spans/polarity/
  freshness on the mint.
- ✅ **M2 COMPLETE 2026-08-31 (`1450903b`→`e11f1e47` + final docs) — the contract
  quadruple is live end to end:** the typed TurnResult at the finalization seam, with the
  commit envelope's status/content/hash PROJECTED from it (the projection-tamper sabotage
  proves the commit reads the typed object). Obligation buckets follow the ledger's own
  census; undeclared slots stay undeclared. Remaining M2 work: migrating the remaining
  lanes to LaneProposal and displacing the residual scattered policy reads — then M3.
  (CORRECTION 2026-08-31, arch-truth-r1: "live end to end" was overstated for the
  REQUEST half — slice 1's TurnRequest was constructed post-hoc, after the inner call.
  The M2 TRUTH REPAIR entry above closes that gap; TurnState is still post-turn.)
- ✅ **M2 slice 3 landed 2026-08-31 (`c5a96525`+`224cffb0`):** the typed LaneProposal,
  live-data as the first producing lane — its lane-id/confidence literals displaced to
  the proposal's constants, both decline paths record typed refusals, and the
  no-answer-bytes law is structural (field-set pin). 5 new tests (19), two sabotages,
  cumulative 127/23,175 with zero deterministic candidate-only (the one slice-only
  failure is the third distinct turn_manager flake). Remaining M2: TurnResult (slice 4).
- ✅ **M2 slice 2 landed 2026-08-31 (`9643f562`):** the typed TurnState at the same
  intake seam — the execution-identity read DISPLACED through it (reference, not copy;
  copy-sabotage red), ledger binding recorded ids-only, unwritten fields empty by
  construction; 6 new tests (14 total), two sabotages, cumulative 127/23,170 with zero
  deterministic candidate-only (the one slice-only failure is a turn_manager flake that
  fails at base too). Next: LaneProposal + TurnResult, then displace the remaining
  scattered policy reads.
- ✅ **M2 slice 1 landed 2026-08-31 (`7dc485c3`+`37ca5236`):** the typed TurnRequest
  lives at the run_once intake every surface funnels through — server-derived identity
  and trust (forged principals never read), verbatim text, reserved-key protected,
  behavior byte-identical; 8-test family + sabotage; HTTP live-proof at the exact SHA.
  (CORRECTION 2026-08-31, arch-truth-r1: "at the intake" was timing-false — the
  construction ran AFTER `_run_once_inner` returned, so it was a post-hoc audit
  decoration. The M2 TRUTH REPAIR entry above moved it before execution and made it
  load-bearing.)
  Next M2 slices: TurnState / LaneProposal / TurnResult, then displacing the scattered
  source_context policy reads.
- ✅ **M0 + M1 of the architecture campaign landed 2026-08-30 night:** M0 — boundary
  map instrument committed; pinned 3.12.13 venv stood up (the authoritative gate RUNS;
  stops at 988 base-identical pre-existing lint findings = a lint-debt lane); base
  full-suite receipt 133/23,123; the -q wedge is a non-reproducing timing class (open).
  M1 — the composition boundary is one-way (core does not import apps, AST + string-edge
  scan enforced by tests/test_architecture_composition_boundary.py, sabotage-proven):
  VoolAgent/VoolDaemon/config dataclasses/url-grounding moved to core homes; apps are
  pure facades; HTTP+CLI equivalence live-proven at the exact SHA. Evidence: ISSUES
  register Pass 7; VOOL-DELIVERY architecture_convergence_campaign.
- ✅ **M3B exit gate CLOSED 2026-08-30 night (`6f974e92`, LOCAL-ONLY):** typo families
  pass end to end (extractor silent-drop fixed), TEST MATRIX completed (timeout/
  malformed/model-failure/cross-turn), root cause D closed (attempt status derives from
  the demand census — PARTIAL_SUCCESS live over unanswered demands), sabotages
  #1/#2/#5/#6 run with byte-identical restores; 1,695-test perimeter zero
  candidate-only; live-proven at the exact SHA. Evidence: ISSUES register §ANSWER-INTEGRITY
  Pass 5+6. First slice (`fcfad933`):
  the operator's three-incident live round (request-text echo; answered-comparison-
  reported-unanswered; clause-grain false absolution) fixed at root cause, proven on a
  fresh isolated daemon (healthz commit match), mutation-proofed S1/S2/S2b/S3 with
  byte-identical restores, 1,581-test attributed perimeter (zero candidate-only
  failures). Evidence: `ISSUES/OPEN - multi-intent-served-slot-loss - 2026-08-29/`
  §ANSWER-INTEGRITY; tests `test_answer_integrity_incidents.py`. Open follow-ups kept
  in that register: conductor/frontdoor receipt coverage, recognizer offsets, census-
  derived attempt status (M3B root-cause D).

**E. A14 release hardening (the gate)**
- [2026-09-01 LANDED, foundation half] Signed atomic self-update with automatic
  rollback — `core/updater/` + helper + CLI + release tools on
  `build/self-update-atomic-20260901` (base b3f5117f, local-only): fail-closed
  Ed25519 manifest/artifact verification, channels + no-downgrade + replay defense,
  resumable verified download, journaled atomic swap with crash recovery and
  automatic rollback, transactional user-data migrations, plain-language status,
  explicit-press gate; 160 tests incl. a real-HTTP sandbox e2e; sabotage matrix 8/8.
  Remaining for the gate: pin a real publisher key, wire the wrapper UI button,
  Developer-ID notarization drive, Windows/Linux installers (contract boundary only
  today). [AMENDED, same day: production wiring LANDED — boot/API/chat-chip/wrapper
  pipeline all live through core/updater/runtime, one authority; browser-driven
  sandbox journey DONE + ROLLED BACK with screenshots; see CURRENT_STATE
  signed_atomic_self_update_2026_09_01.amendment_2026_09_01.]
- Exact-SHA desktop build, independent review, fresh install/upgrade/restart/crash
  recovery, explicit offline mode, missing/invalid/exhausted API-key behavior,
  cancellation at every significant boundary, persistence, privacy deletion
  served proof, strict-output served tests after all presentation code is
  attached, refusal truth after recovery/restart, local/cloud switching in one
  real chat, final-byte invariance, activity+receipt truth vs the same execution,
  model-selector correctness + price-approval path, and neutral product-visible
  paths produced by the display sanitizer and clean demo fixtures. **Do not rename
  the operator's Mac, macOS account, short name, home directory, or hostname for
  this gate.** Host-identity surgery is unrelated release risk and is explicitly
  out of scope.
- Ship because the exact served product survived the attacks — never because
  "20,000 tests green."

**F. Public-demo application convergence — mandatory before recording**
- Finish every currently active build/amendment lane, then integrate only the
  proven commits into ONE candidate. The current browser surface, unified runtime,
  desktop wrapper, and feature branches are different realities until this gate
  proves otherwise.
- Build a **self-contained macOS VOOL.app from the exact integrated SHA**. The
  checkout-dependent wrapper in `Desktop/vool-checkout/VOOL.app` is not a distributable
  demo artifact and must not be used as evidence: it launches a different checkout.
- From a fresh install, mechanically prove app launch -> owned daemon -> `/healthz`
  exact commit/build id -> native window -> real chat. Then cumulatively drive the
  public-demo journey: mixed demands, live search, selected cloud model, tool use,
  Council, Activity/receipts, Root-Cause Doctrine, Model Radar, Safe Bug Reporter,
  local-model-disable policy, update status, restart and rollback.
- Run the first readiness audit before the VOOL -> VOOL identifier migration and
  freeze it as the behavioral baseline. Run the identical audit after migration;
  the second pass is the release verdict. No demo recording and no MIT publication
  between those two gates.
- Add file/photo attachment UX to the real chat before claiming attachment support.
  The API accepting attachment metadata is not an upload surface; desktop and mobile
  both currently lack a real file/camera picker in chat.
- **Mobile does not block the first desktop demo.** Public claims for this release
  are macOS/Windows/Linux only. A narrow responsive viewport screenshot is not a
  mobile app, PWA, pairing flow, or remote companion.

---

## 🟠 PHASE 1 (P1) — CORE PRODUCT POWER. ENTER after A9 lands (item A may start in parallel with late A9).

**A. General cross-capability automation (thin facade) — the parked initiative**
- Full analysis + design + slices live in
  `workspace/TDL - GENERAL CROSS-CAPABILITY AUTOMATION (GLM. 5.3)/` — start at its
  `00-START-HERE.md`. Decision on record: thin `core/automation/` facade; the only
  new truth is a standing-intent store + scheduler that fires normal turns through
  the existing A0 door. Operational laws (delivery, overlap, auto-pause, kill
  switch, idle gate, catch-up, recurrence vocabulary, budget, lifecycle,
  unattended approvals) are in its file 13.
- Phasing: slice 1 (intent store + run-now) can start as soon as A9's routing plan
  lands (it needs no routing changes itself); full scheduler AFTER A9; risk
  escalation AFTER preflight. Do NOT build a second orchestration authority.

**B. KAS ↔ VOOL integration enforcement (not just architecture docs)**
- KAS owns external channels/integrations/automation transport; VOOL stays the
  authority for permission, semantics, effects, finality, privacy. Remote
  GitHub/GitLab/Discord/Telegram/email become KAS adapters, never duplicated
  provider-specific VOOL logic.
- Typed KAS external-adapter registry; typed capability exposure plugin→A5
  capability graph (installed ≠ available ≠ permitted ≠ invoked).
- External-action evidence contracts: KAS reports what it observed externally
  (sent message, provider response, channel id, remote error) but never authors
  VOOL semantic/finality truth. Remote deletion that cannot be verified stays
  UNKNOWN — no "deleted everywhere" fantasy.
- KAS automation receipts bind external evidence back to VOOL execution/effect
  identity WITHOUT creating a duplicate receipt authority.

**C. Council as a governed verification engine (no automatic swarms)**
- C14 lane 2026-09-04 (`build/council-model-truth-20260904`, commit `a206216c`):
  measured seat spend is captured from the real seat-turn stream and aggregated
  in the scorecard and chat card with lower-bound honesty; receipt counting
  reads the real served wire shape (receipt-backed counterexample precedence
  fires live); council control statuses stay typed through the registry seam.
  Served proof + honest skips (cloud seats Keychain-gated, local author
  uncertified): `validation-logs/c14-council-model-truth-20260904/`.
  824 cumulative tests green; 5 sabotage proofs with named red tests.
- TASK_CONTRACT v1 enforced mechanically for every serious worker:
  BASE/EXACT_SHA, WHAT, WHERE, DONT_TOUCH, DEPS, AUTHORITY_OWNER, DONE_WHEN,
  VERIFY, COUNTEREXAMPLE, EVIDENCE_PATH. Builders get BASE/CANDIDATE SHA; proof
  workers get immutable EXACT_SHA.
- Deterministic Gate 0 BEFORE any model token: already done? wrong repo? wrong
  SHA? dirty tree? dependency blocked? authority blocked? provider dead? budget
  exceeded? → STOP before LLM.
- Typed AUTHORITY_OWNER enforcement — refuse tasks that cannot name who owns the
  truth being modified; no inventing pseudo-authorities.
- Permanent seats, replaceable models: FREE_FLASH_SCOUT / CHEAP_FLASH_WORKER /
  INDEPENDENT_FREE_COUNCIL / frontier escalation (CLAUDE/CODEX/FULL_GLM) as
  runtime roles. Model-family provenance graph + harness provenance separate from
  model provenance — five GLM harnesses are NOT five independent models; alias
  detection invalidates independence assumptions automatically.
- Escalation ladder in code: deterministic → free scout → cheap builder →
  mutation/hostile QA → independent different-family council → frontier ONLY for
  unresolved contradiction / release blocker / theorem. No silent paid fallback:
  free → free → cheap → PAUSE; premium requires explicit human escalation.
- Cost: hard ceiling per task/swarm known to the worker (€X / Y requests /
  free-only), pre-launch cost estimate, terminate instead of accidentally
  launching expensive armies. Provider/model integrity record per run (requested
  slug, actual route when observable, context limit, capabilities, free/paid,
  thinking effort = UNKNOWN unless mechanically exposed).
- Swarm shape law: known failure surface = 3–5 surgical workers; broad 10–20+
  swarms ONLY for unknown repo-wide search spaces; stop audit-of-audit swarms
  once deterministic reproducers are green. One authoritative builder per
  overlapping surface; read-only children ENFORCED at runtime for
  discovery/proof; evidence workspace separated from the code worktree.
- Persistent Council state outside chat sessions: task DAG, leases, evidence
  refs, worker status, candidate SHA, blockers — killing a model session never
  loses orchestration state. Council control-room UI: worker, task, SHA, model,
  cost, dependency, evidence, RUNNING/PASS/FAIL/UNKNOWN.
- State separation law: TASK_VERIFY_GREEN ≠ TASK_DAG_CLOSED ≠ INTEGRATION_GREEN ≠
  INDEPENDENT_PROOF_PASS ≠ VOOL_OBLIGATION_CLOSED ≠ A2_ADMITTED ≠ A7_FINALIZED.
  Counterexample supremacy: Council never votes a PASS over one deterministic
  repro. Mutation proof as a primitive, not a prompt. Human owns
  promotion/freeze/merge — always.
- Council Gate-0 / contract items here are prerequisites for dogfooding: one
  request → task contract → scouts → one builder → adversarial proof → candidate
  → human promotion ("VOOL safely building VOOL through VOOL" — the ultimate
  flex, scheduled LAST for a reason).

**D. A10-ARCH — skills/plugins ARCHITECTURE SEAMS (early on purpose; product surface stays PHASE 3)**
- Operator rule: skills/plugins product work is lowest priority — but the
  architecture must reserve the seams NOW so nothing later grows into a second
  authority. This item is those seams ONLY:
  - A5 capability graph is the ONE capability representation; plugin/skill/KAS
    exposure registers into it (installed ≠ available ≠ permitted ≠ invoked).
  - Permission law wired: installation NEVER equals permission (A1 stays the sole
    widening authority); skills can never grant permissions; plugins can never be
    authority. The parked `plugin_executor` wiring decision (currently registered
    but not executable) is made here deliberately, not by accident later.
  - Credential Intelligence vault seam: paste key → local classifier → likely
    provider shortlist → verify ONLY selected provider → secure vault → opaque
    CredentialBinding → capability graph → KAS adapter. Raw secret never reaches
    models/memory/logs/receipts.
  - Clean lineage decision for the parked experimental stack (toolbelt /
    skill-system / ox-innovation-lab composition line) — adopt, adapt, or reject
    ON RECORD (see parked automation TDL file 11), so PHASE 3 builds on decided
    ground.

**E. Planner / execution quality**
- Structured planner: objective, steps, required tools, stopping conditions,
  retry/tool/token/wall-clock budgets — not prompt prose. Planning is OPTIONAL
  ("hi" and 37+18 never take a ten-layer tour); tiered execution: deterministic
  reflex → single-model chat → heavy agentic only when justified.
- Controlled replanning (revise remaining plan on new observations without
  rebuilding the universe); normalized tool observations; unified failure
  taxonomy (timeout ≠ denied ≠ unavailable ≠ partial ≠ cancelled → different
  retry/recovery each).
- Fast-path admission discipline: a deterministic fast path claims the turn only
  when it covers the WHOLE obligation set.

**F. RepoOps vertical slice (coding-agent product)**
- Inspect PR → diff bound to exact SHA → CI logs/artifacts → repair → review →
  explicit Authorize Push → push → verify → receipt. Human owns push.
- Typed git actions instead of god-shell: cherry-pick, revert, merge/conflict
  states (durable first-class, not "nonzero, shrug"), tags, branch ops, CI
  rerun/cancel. Local-git mutation completeness + full PR lifecycle + CI-failure
  analysis feeding repair evidence. Force-push/branch-delete default-deny.
  Ambiguous remote mutation = reconcile first, never blind retry.

---

## 🟡 PHASE 2 (P2) — UX / COMPANION / PERFORMANCE. ENTER after the Phase-1 spine lands; "feels like VOOL".

1. A11 production UX convergence (bind experimental UI to real backend truth).
2. Honest model selector: Auto / pinned local / pinned cloud visually distinct;
   current provider/model/locality + fallback reason visible; routing receipt in
   UI ("why this model?", paid/free, whether Auto overrode).
3. Dynamic FREE/NEW model UX backed by the free-model watcher + verified provider
   state; FREE→PAID requires approval BEFORE dispatch; free ≠ auto-select.
4. Compact readable receipts/activity — five useful words first, full evidence on
   demand; collapsed activity rail ("COMPLETED · 3 actions · 14s").
5. ⌘K command palette (non-negotiable — 45 capabilities must not be 45 buttons).
6. Capabilities surface (installed/available/permitted/used) + tool health cards
   with evidence on click (GitHub connected, Docker down, permission UNKNOWN) —
   no fake green lights.
7. Companion profile as typed explicit identity (not model-memory folklore);
   precedence: current-turn explicit → project/chat → global → inferred →
   default; nothing self-renames.
8. Pixel dude driven ONLY by real typed state (idle/listening/working/searching/
   speaking/done/failed/refused/unknown); UNKNOWN gets its own visual state;
   friends only when real swarm children exist; rage on real failure, cheer only
   on typed PASS+DONE; no fake chain-of-thought animation.
9. Voice UX: provisional vs final transcript with on-device provenance; barge-in
   ≠ task cancellation; audio not retained by default.
10. Memory UX: EXPLICIT/INFERRED/PROJECT tags, one-click forget, consent; model
    text never becomes authority because memory stored it; "forget" propagates
    through A8 eventually.
11. Result UX for partial/failed/UNKNOWN ("answered 6/8", "tool unavailable",
    "format unsatisfied") — no generic mush. UNKNOWN as a consumer feature
    (dashed/neutral ≠ red/green).
12. Neutral display-path sanitizer (show ~/foo, keep exact internal identity,
    never authorize against sanitized text). RepoOps GUI rail with explicit
    Authorize Push.
13. Mobile layout / accessibility: keyboard path, reduced motion, no color-only
    status, captions for Companion state.
14. Performance ONLY after correctness: local keep-alive/prewarm (15-min
    experiment revisit), dead-provider preflight (fail fast instead of 10–38s),
    prompt-prefix measurement/partial reuse without reordering
    semantically-important prompts to win a cache benchmark; local→cloud→local
    residency measured.
15. Result/chat-noise cleanup carried from the old ledger: internal
    workflow/system chatter hidden from primary chat unless debug is on.

---

## 🟢 PHASE 3 (P3) — PRODUCT EXPANSION. ENTER after base VOOL is solid.

1. **A10-PRODUCT — skills/plugins convergence + catalog/marketplace LAST by
   operator rule** (architecture seams already reserved in PHASE 1-D): Skill =
   workflow/knowledge; Plugin = executable capability; Platform = authority.
   Lifecycle: discover → install → verify → update → revoke → uninstall →
   evidence. KAS owns packaging/distribution; VOOL owns permission to use.
2. A12 Marketing Radar / Chaos CMO: project understanding → live culture scan →
   WHY → anti-slop filter → 3 ideas + visual → human approval → KAS publish →
   receipt → metrics → hypothesis update. NO-POST is a legitimate output.
   Community reply, quiet-day sentinel, launch narrator, transparency gatekeeper
   (SHIPPED/TESTED/EXPERIMENTAL/PLANNED/UNKNOWN before public), doodle director,
   marketing hypothesis memory with evidence decay.
3. "Hey VOOL, make a demo video": run real tests → record actual UI → zoom
   significant events → subtitle/explain → polished output. Receipt-first
   marketing content; screenshot-as-content.
4. **Mobile Companion V1 / Device Link productionization — after the desktop
   demo.** Heavy execution remains on the desktop; the phone is a scoped control,
   inspection, attachment and approval surface, never a second runtime authority.
   Build in this order: one desktop bridge authority → explicit enablement →
   pinned TLS → one-time QR pairing → signed/scoped/revocable grants → companion
   chat/status/Activity/approvals/cancel → file/photo upload → notifications →
   safe reconnect/revocation. Prove replay refusal, expiry, concurrent-device
   isolation, wrong-host pairing, lost-phone revocation and zero authority
   elevation. Never expose the existing loopback daemon by merely changing
   `127.0.0.1` to `0.0.0.0`. Start with an installable responsive companion/PWA
   only if it consumes this same bridge contract; decide on native iOS/Android
   shells after that path is proven. The existing `core/device_link` grant
   protocol/CLI is useful foundation but remains unwired: no bridge server, TLS,
   QR, handlers or phone frontend today.
- Device Mesh / Tether preserved evidence (PASS004 stays parked evidence):
   desktop is SOLE machine authority; phone is authenticated
   eyes/ears/input/output/approval — never a second router/permission/semantic/
   receipt authority. **Where the parked code lives (verified 2026-08-30 — NONE
   of it is in this checkout):** `~/vool/worktrees/swarm-tether-control/
   experiments/tether_pass001/` (capability-grant protocol + demo server:
   desktop Ed25519 keypair sole authority, phone = grant-id + HMAC request
   surface, envelopes VIEW_ONLY→FILES→FILES_TERMINAL→DEV_MACHINE→
   FULL_REMOTE_CONTROL, high-risk verbs via os_consent_gate);
   `~/vool/worktrees/device-mesh-goblin-20260826/experiments/device_mesh_pass001/`
   (tetherctl: devices/tier/revoke/mint-token→pair URL; companions can never
   elevate or revoke themselves); `~/vool/worktrees/swarm-r2-tether-converge/
   experiments/tether_converged/` (convergence pass); later probes archived at
   `~/vool/archive/at-risk-recovery-20260829/device_mesh_pass001/`
   (pass004_probe.py). NAMING HAZARD: "tether" in THIS repo already means an
   optional `tether-remote` OpenAI-compatible MODEL provider
   (`core/runtime_provider_defaults.py`) — the device lane needs a distinct
   name on revival. Real iOS build + physical device testing; real
   Windows/Linux bridge proofs; screenshot/remote-view slice (macOS TCC);
   camera/mic/location/clipboard/files with typed provenance + revocable grants;
   tether permission-tier UI; one-tap revoke killing leases; secret-once UX; QR
   pairing; device identity page; lost-phone mode; handoff without second
   semantic execution; approval-on-phone for dangerous desktop effects
   (approval ≠ execution); cross-device clipboard + file transfer as product
   surfaces.
5. x402 / wallets / payments — INTENTIONALLY DEFERRED until after base release
   (recon-only posture): chain-neutral PaymentIntent; opaque signer handles;
   approval binds exact intent (chain/asset/amount-max/recipient/purpose/expiry);
   atomic spend reservation before external execution; UNKNOWN reconciles before
   retry; spend ceilings wired to A6 not adapters; facilitator fees count toward
   budget; receipts bind request+response+external effect with A7 final-byte
   truth; Solana then Base/EVM adapters; v1↔v2 shim if reusing dna-x402; the
   USD-denominated daily/weekly cap track (signed-file v2 envelope migration)
   carried from the previous ledger.

---

## 🔵 PARALLEL RESEARCH — important, NEVER blocks base VOOL (Dark VOOL / Degen Lab)

Standing state (PASS007 landed: Cline-shadow verdict "LCF SURVIVES AS DESIGN
COROLLARY" — Maurer owns the lower-bound machinery; D2′ too narrow for common
nonlinear/boolean witness distributions; O6/O7 open):

1. Real B6 remains THE milestone: one public Solana devnet tx verifying the wrap
   under the settlement VK, binding NIFS/EC public IO, writing the nullifier,
   failing replay with DoubleSpend, then a second valid session with different
   pk_payer, with explorer evidence. Local validator + historical B2 tx don't count.
2. Replace the toy wrap binding with real settlement VK + real public IO (the
   third unbound public); wire NIFS + native ec_check INTO the settlement tx;
   redeploy the B6 program to public devnet; run the happy path, the replay
   attack (observe DoubleSpend), and the second-valid-session control; capture
   full explorer/RPC evidence (program, signatures, slots, CU, nullifier
   transition).
3. Do NOT resurrect the ~18M-constraint EC-in-circuit design (dead). Viable:
   native on-chain EC + a small field-only wrap — then finish the privacy problem
   that creates (Grumpkin-side wrapping / encrypted-openings directions).
4. Mova-style folding spike; batch-compression design; BPF Poseidon
   public-devnet CU measurement; resolve the 8-vs-9 test-count discrepancy.
5. LCF docs after PASS007: label = design corollary / NIFS-specific application,
   Maurer credited — never "novel theorem". Rewrite D2′ (quotient-ring/algebraic
   formulation covering bit-decomposed/boolean witnesses) then REPROVE — never
   just edit the statement until it sounds nicer. Correct the PASS005 devnet doc
   (64ed0e2e digest was right; PASS006's b132ffcf included loader metadata); fix
   the tx2 signature typo (rVfYvCGk → rBfYvCGk); fix artifact reproducibility
   (recorded snapshots currently recompute to a different hash than committed).
6. O6 shared-accumulator attack OPEN; O7 sumcheck/alternate-folding
   formalization OPEN (D1-for-e₂ block + commitment model unwritten).
7. One genuinely independent frontier review ONLY if going public academically
   (statement + proof + Maurer reduction + D2′ + O6/O7). No 20-reviewer orgy.
8. Keep public-claim separation: devnet tx = engineered demonstration;
   experiments = falsification; hand proof = candidate reasoning; prior-art
   search = novelty evidence. None alone proves theorem/novelty/firstness.
9. Useful secondary lanes: turn the 37 killed constructions + reasons into a
   research artifact; revisit one under deliberately stronger assumptions
   (witness co-location / MPC-threshold / FHE / changed arithmetization);
   threshold-MPC engineering spike (n ≥ 2t+1 flagged viable); private split
   verifier + state-bound transcript hardening as SYSTEMS lanes (novelty dead);
   rho_replay state-awareness + rho↔ledger binding; wrap skeleton → real circuit
   only where witness visibility truly requires; hostile malicious-prover
   campaign AFTER B6; crash/retry/idempotency around public settlement (send
   succeeds / client loses response, stale blockhash, nullifier already written);
   real CU economics (Groth16 verify + native EC + transcript replay + account
   writes); maybe the native split becomes a reusable Solana verification
   pattern. Re-evaluate witness privacy only after B6 is real.
10. Secret hygiene: programs/settlement/keypair.json out of future clean lineage;
    the ../keys/ sibling is COMPLETELY off-limits — never inspect/print those
    bytes into any transcript.
11. Decide public claims LAST (primitive vs design result vs systems
    architecture vs privacy rail). Token Hunter: chronological mining only from
    frozen datasets/holdouts, explicit base rates, zero leakage; forward
    proof-of-alpha, not chart-splaining; idea-killers before building; Goblin
    Maxi combined research stays research.

---

## 🌙 MOONSHOTS — cool as fuck, AFTER the house stops burning

1. VOOL Remote mobile app (chats/status/approvals/voice/files/sensors — not a
   duplicated desktop brain); full multi-device continuity (desktop → phone
   inspect/approve → desktop continues seamlessly).
2. KAS marketplace: browse → permissions → install → connect → use; builder SDK
   → test → package → publish.
3. Self-improving marketing hypotheses with bounded evidence-driven updates.
4. Automatic demo/media studio; agent-to-agent paid capability marketplace via
   x402 — only when services are actually worth buying.
5. Reusable Dark VOOL private settlement rail if B6 proves the engineering.
6. Council dogfoods VOOL development: one request → task contract → scouts →
   one builder → adversarial proof → candidate → human promotion. (The ultimate
   VOOL flex.)

---

### Native browser product lane — 2026-09-04 (C06 + C07)

Writer: `build/native-browser-product-20260904` (based on the clean checkpoint
`5a46e35f`). ONE writer (the browser lane owner); shared-core edits are additive
only (contracts list, demand signals, one word-boundary routing repair, blackbox
coverage declarations).

- ✅ C06 native product browser LANDED: `core/vool_browser/` + 15 contracted
  intents + native skill `skills/vool-browser` + demand seating for prose-named
  `vool-browser.*` intents. Isolated disposable scratch profiles (deleted on
  close), mock-keychain engine flags, per-origin deny-by-default permissions,
  pre-flighted redirect control (cross-origin redirect refuses without a grant),
  bounded screenshot/download/upload, per-session budgets, hash-chained receipts
  with tamper verification, blackbox coverage for every mutating intent,
  untrusted-evidence envelope on all page text. Gate: Manual pends the session
  act; Plan denies; financial C07 intents pend in every mode. Served /api/chat
  journeys green on a real daemon (round-1 seating checked against the real
  offer; Manual pend with approval_id; Auto full journey; restart).
  Routing repair (load-bearing, one line): "browse" was substring-matched in
  `looks_like_explicit_lookup_request`, hijacking every "browser" turn into the
  live-info fast path; now word-boundary matched.
- ✅ C07 shopping + wallet-checkout handoff LANDED on the same authority:
  exact offer extraction/comparison (sku/merchant/total/shipping/returns,
  mangled records refused), operator-confirmed profiles with card data refused
  structurally, checkout fills contact/address only, wallet handoff detects
  Apple Pay / Google Pay and requires a VISIBLE session (foreground auth; the
  lane never clicks pay), idempotent merchant-authoritative reconciliation
  (pending_merchant_confirmation until the merchant confirms; exact total;
  one terminal receipt per idempotency key; duplicate claims refused).
- Evidence: `validation-logs/vool-browser-20260904/EVIDENCE.md` (RED→GREEN,
  served journeys, sabotage S1–S7 red-by-name with byte-exact restores,
  cumulative unions, one attributed inherited weather-lookup gap at base).
  Lane tests: 47 passed (11 C07 + 33 C06 + 3 served journeys).

## ☠️ DON'T-DO LIST (anti-TDL — outranks everything above)

1. Do not reopen Foundation+A7 for aesthetic cleanup.
2. Do not freeze A8 because builder tests passed.
3. Do not use another 18-agent swarm when the failure surface is already known.
4. Do not let the builder certify its own work when independent review is practical.
5. Do not let Council consensus override one deterministic counterexample.
6. Do not auto-merge/promote/push. Human owns promotion.
7. Do not silently fall back from free to paid.
8. Do not claim thinking/MAX effort unless mechanically exposed.
9. Do not add another semantic/effect/finality/privacy authority because an
   implementation needs somewhere convenient to write state.
10. Do not fix structural bugs with another synonym dictionary/regex stack.
11. Do not let Skills grant permissions. Do not let Plugins become authority.
12. Do not let KAS duplicate VOOL semantic truth; do not let VOOL duplicate
    Telegram/Discord/email/etc. that belong behind KAS.
13. Do not expose raw API keys/private keys to models, logs, receipts, evidence.
14. Never inspect the Dark VOOL sibling ../keys/.
15. Do not blindly retry UNKNOWN external effects. Reconcile first.
16. Do not make crypto/x402 block the basic VOOL release.
17. Do not call Dark VOOL LCF a new proven theorem.
18. Do not ship because "20,000 tests green." Ship because the exact served
    product survived the attacks.
19. Do not rename the operator's Mac, computer/host name, macOS account, short
    username, or home directory for a demo. Remove personal identity from VOOL's
    rendered surfaces, fixtures, screenshots and logs at the product boundary;
    do not destabilize the development machine to manufacture neutral footage.

---

## §CARRY-FWD — open items carried from the previous ledger (mesh/infra, 2026-03→07)

Still open; none block the Phase-0 chain; schedule inside Phase 2/3 as infra
hardening (done-when criteria as originally written):

1. `P0` Multi-helper speculative reasoning still partial — N model-backed paths
   for the same capsule with evaluator scoring persisted as first-class review
   signals.
2. `P1` Observability baseline-only — end-to-end trace spans, alertable SLO
   metrics (latency, error rate, peer churn, stalled tasks).
3. `P1` UDP control-plane best-effort — critical control signals need explicit
   ACK/retry with bounded queueing.
4. `P1` Stream transport under churn — sustained multi-node stream/fragment
   recovery evidence under packet loss and helper churn.
5. `P1` Task-offer/market broadcast pressure — adaptive fanout caps, jittered
   rebroadcast suppression, backpressure metrics; chaos-stable at higher counts.
6. `P1` Consensus quality for subjective/creative reasoning — semantic/
   contract-aware comparison on by default; disputed paths trigger verification.
7. Closed-test guardrails stay in force while any of the above are open:
   closed trusted-testing posture, paid top-up disabled, payments/DEX labeled
   simulated, watcher/read surfaces separated from meet writes, signed writes +
   replay checks + identity-lifecycle enforcement everywhere.

## Review cadence

- Re-check this TDL before every claimed item and every cross-machine test cycle.
- Do not mark items complete without test evidence (never module presence).

## Source-convergence wave — frozen 2026-09-04

All 16 checkpoints of `OPUS_FINAL_SOURCE_CONVERGENCE_GOAL_20260904.md` (plus the two lanes the
convergence amendment added) are integrated on `build/vool-final-integration-20260903`, base
`5a46e35f`. The per-checkpoint records live beside each lane's evidence; `CURRENT_STATE.json`
carries the machine-readable summary under `source_convergence_wave_2026_09_04`.

- ✅ CLOSED: the 8 composer-model-anchor failures recorded as an inherited gap since checkpoint D
  are down to 1, and that one is a "boundary untested" guard whose premise no longer holds in
  this composition rather than a product break.
- ✅ CLOSED: the shipped `vool-database` typed contract, by the skill authority's own repair —
  the X-editorial lane's competing repair was reviewed and REJECTED because it would have
  stripped the skill's declared tool grants.
- ⛔ OPEN (C13-owned): `GET`/`POST /api/context/pages` have no owning registry command, so the
  operator-action census reports 2 `LEGACY_UNMIGRATED` and three tests fail. Not suppressed —
  no UI calls those routes, so classifying them as transport would be a false statement.
- ⛔ OPEN (skill-authority-owned): the composed native library cannot fit its own skills into
  `MAX_SKILLS=2` within `MAX_SKILL_CHARS=4000`. Two independent instances measured; raising the
  slot count fixes neither, because the character budget binds first.
- ⛔ OPEN (A8/privacy-owned): the PASS003 forced-interleave harness parks inside a write
  transaction that per-use connections made real. A lock wait, not a deadlock; the privacy law
  is stronger than at base, not weaker.
- ⛔ OPEN (wallet-owned): `core/wallet/lifecycle.py` imports `evm` unconditionally, so without
  `eth_abi` the Solana path, custody, ceilings and the mainnet-impossible guard raise
  `ImportError` rather than a typed refusal. App boot is unaffected.
- ⛔ OPEN (effect-budget-owned): `test_door_command_machine.py` writes into the operator's real
  `~/Desktop` and never cleans up.
- ⛔ NOT STARTED, and not claimed by this wave: C12 requirement-to-caller matrix, C13 consent/
  provenance UX, and the post-convergence closures C22
  (command-centre), C23 (human-readable trust UX) and C24 (historical served-regression
  gauntlet). The VOOL→VOOL rename, the updater and any packaging remain deliberately outside.
- ✅ C19 automatic presentation selection (2026-09-05, `build/c19-presentation-20260905`): the
  automatic half is implemented and served-proven per
  `~/vool/research/FREE_FLASH_C19_PRESENTATION_AUTHORITY_SPEC_20260904.md` — deterministic,
  prompt-blind election (`core/presentation_selection.py`), explicit requests always win,
  ambiguity defaults to prose, mermaid/chart/pie never auto-elected, the selection record ships
  in `display_metadata` re-stamped post-gate, and the one derived repair runs only on the
  free_local lane behind byte-survival acceptance. Served /api/chat proofs S1–S6, browser proofs
  B1–B8, the unit pack T1–T13 and the physical-mutation sabotage matrix are recorded in
  `validation-logs/c19-presentation-authority-20260905/EVIDENCE.md`.
