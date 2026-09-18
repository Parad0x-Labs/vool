# Served-reality bench — final evidence record (2026-09-02)

Branch `build/served-reality-bench-20260902`, tip `340e038c`, base exactly
`a6c8e3c4` (9 local commits, author `sls_0x <sls_0x@local>`, never pushed).
This lane built the bench only; no product defect was repaired here.

## The final run (the bench's own proof)

`/tmp/vool-venv312/bin/python -m ops.served_reality.runner --sha a6c8e3c4 --mutations --run-dir /tmp/sr-final-evidence`

- run id `sr-20260902-184211-e362a3e0`, staged product SHA
  `a6c8e3c4eaa70f30ccd77a9a750560286da34f2c` (full), staged from a CLEAN tree,
  archive sha256 `80ac895a…4236`, app census UNCHANGED after the run
  (immutability held; the legacy `.vool_local/` state root is carved out and
  reported), provider mode deterministic, 0 network mutations.
- **29 cases (23 corpus + 6 mutation controls): 3 VOOL failures, 0 unexpected
  skips; all 6 mutations DETECTED.** A truthful red run — the bench's success
  condition is truth, not green.
- Full machine-readable proof: `evidence/final-run-20260902-{manifest,results,findings}.*`
  (verbatim copies of the run's artifacts; the live run dir also retains
  exact wire bytes both directions).

## Product findings at a6c8e3c4 (VOOL-class, unrepaired by design of this lane)

1. **strict-literal-output** — "Reply with exactly this and nothing else:
   VERBATIM-PROOF-2291" is served by the deterministic strict-literal fast
   path as `this and nothing else: VERBATIM-PROOF-2291` — the span
   extraction swallows the instruction clause, so the strict literal
   contract (bytes, and only the bytes) is violated. Deterministic: 0 model
   calls; reproduced in every run.
2. **mixed-demands** — "what time is it in Tokyo? convert 100 USD to EUR.
   finish with a two-word joke": the product's own demand ledger mints ≥2
   demands, the time unit is served, and under a failed retrieval (the FX
   fetch, deterministically blocked by the bench's contained-network proxy)
   the remaining units VANISH from the answer — no figure, no typed
   "couldn't retrieve", nothing. Minted-but-dropped units: the multi-slot
   drop defect class, made deterministic by containment.
3. **restart-continuity** (final run; PASSED in four earlier runs) — after a
   daemon restart the first model-bound turn failed with the product's own
   honest refusal ("couldn't get a usable model response… not going to
   recycle cached text") even though the model lane was a live loopback
   stub. Post-restart first-turn model availability is unstable; history,
   receipts and the receipt chain all survived the restart (those
   assertions held).

Live environment truths discovered while building the bench (documented in
code comments, no product change made): a served daemon auto-registers the
operator's keychain OpenRouter key as a paid lane unless
`VOOL_CREDENTIAL_STORE=vault`; the mesh daemon's fixed UDP port hard-kills
concurrent instances; the durable conversation log persists only "memorable"
turns (≈1 row per 24 ordinary turns measured); runtime event rows flush
late/inconsistently on short-lived daemons.

## Mutation controls — 6/6 DETECTED

| control | injected defect | how | verdict |
| --- | --- | --- | --- |
| m1-dropped-request-unit | responder serves 1 of 3 demanded units | stub script | DETECTED (mixed-demands FAILED) |
| m2-bypassed-permission | `decide_tool_call` patched to ALLOW-all | throwaway app clone, anchored patch | DETECTED (denied-write caught the silent write) |
| m3-fabricated-tool-success | every receipt claims a tool that never ran | throwaway app clone | DETECTED (receipt↔event correspondence failed) |
| m4-wrong-turn-evidence | beta's conversation row rebound onto alpha's session | home tamper, API-visibility proven | DETECTED (history isolation failed) |
| m5-corrupted-cas-accepted | CAS chunk corrupted in place | home tamper | DETECTED (bench verifier flagged it) |
| m6-false-receipt-verification | receipt ledger rewritten with false hashes | home tamper | DETECTED (chain verification failed) |

Patch anchors are verified exactly-once; a stale anchor fails the control
loudly (this happened twice during construction — the clean-tree gate and the
anchor gate each refused honestly rather than passing silently).

## Self-tests

- `python -m ops.served_reality.selftest` → **9/9** (schema fidelity, wire
  byte retention + credential redaction, stub determinism, failure
  classification truth table, skip-is-failure semantics, mutation anchors,
  CAS verifier both directions, artifact persistence).
- `python -m pytest tests/test_served_reality_bench.py -q` → **11 passed**
  (the same checks + corpus/mutation contracts).
- The corpus went RED-first against the untouched base wherever it now fails:
  both standing product findings reproduced at base before any bench-side
  adjustment existed (first full run, `/tmp/sr-full1`).

## Honest limitations

- **Reclaimed-residue gap (recorded per operator instruction):** early runs'
  read-only staging (`/tmp/sr-smoke1/app`) could not be removed by plain
  `rm -rf` (permission denied on the read-only tree). The bench now reclaims
  its own staging via owner-chmod in `stage_app`; the leftover was
  subsequently reclaimed the same way. No foreign-owned file was touched.
- **Determinism scope:** model lanes are fully stubbed and external HTTP is
  proxied to a recorded 502 (the honest "offline machine" shape). The FX
  live-data leg therefore always exercises the retrieval-FAILURE path; the
  grounded-success path of that leg is only observable with `--live`.
- **restart-continuity flake:** passed in 4 of 5 full runs; the final run's
  failure is recorded as product instability, not averaged away.
- **Conversation/event persistence lag** at this SHA bounds what m4 and the
  erasure case can observe on short-lived daemons; both record their
  observations instead of guessing.

## Running the bench against a candidate SHA

```bash
# full proof run (corpus + mutation controls), from this worktree root:
/tmp/vool-venv312/bin/python -m ops.served_reality.runner --sha <candidate-sha> --mutations

# quick subset:
/tmp/vool-venv312/bin/python -m ops.served_reality.runner --sha <candidate-sha> --cases ordinary-chat,corrupted-cas

# instrument self-checks (no daemon):
/tmp/vool-venv312/bin/python -m ops.served_reality.selftest
```

Exit codes: 0 = all required cases pass and every mutation detected;
1 = truthful findings; 2 = harness error (staging/boot — nothing was proven);
3 = usage. Artifacts land in the run dir (`manifest.json`, `results.jsonl`,
exact wire bytes, daemon logs, staged app + isolated home).
