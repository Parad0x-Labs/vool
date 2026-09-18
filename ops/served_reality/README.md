# Served Reality Bench (ops/served_reality)

A release-critical harness that proves the **assembled VOOL product** — the real
daemon, served over HTTP — rather than isolated modules. Every run:

1. stages **exactly one commit** (git archive → read-only app copy; the
   product's own packaged-install provenance `config/build-source.json` carries
   the SHA, and the boot gate refuses any daemon whose `/healthz` commit does
   not match the staged SHA),
2. verifies the source tree was clean (no modified product files),
3. boots the daemon with an **isolated throwaway home/workspace**, all model
   lanes pinned to a **deterministic stub** (OpenAI-compatible + Ollama
   protocol on loopback), all credentials forced to the isolated vault (never
   the operator's keychain), and **all external HTTP proxied to a recorded
   502** (network contained by default — no paid call, no network mutation,
   ever, unless `--live` explicitly opts in),
4. drives the permanent corpus through the real HTTP surface, asserting
   product truths and recording per-case results with full identity sets
   (request/turn/attempt/model/tool/effect/evidence), exact wire bytes
   (credential values redacted, never structure), and a four-way failure
   classification: **VOOL / PROVIDER / MODEL / HARNESS**,
5. verifies the staged app is byte-unchanged afterwards (the product's legacy
   in-tree state root `.vool_local/` is carved out and reported instead),
6. tears down the whole process tree, bounded, guaranteed.

A truthful red run is a SUCCESSFUL bench run. Assertions are never weakened to
green a run. Unexpectedly skipped required cases count as failures.

## Usage

```bash
# from the repo root, pinned python (3.12 venv):
/tmp/vool-venv312/bin/python -m ops.served_reality.runner

# prove one specific commit and a case subset:
/tmp/vool-venv312/bin/python -m ops.served_reality.runner --sha a6c8e3c4 \
    --cases ordinary-chat,corrupted-cas --run-dir /tmp/my-run

# include the six bench-sensitivity mutation controls:
/tmp/vool-venv312/bin/python -m ops.served_reality.runner --mutations

# explicitly enable a live provider (opt-in, needs the key in env):
OPENROUTER_API_KEY=... /tmp/vool-venv312/bin/python -m ops.served_reality.runner --live openrouter

# offline self-tests of the bench instruments (no daemon):
/tmp/vool-venv312/bin/python -m ops.served_reality.selftest

# list the corpus:
/tmp/vool-venv312/bin/python -m ops.served_reality.runner --list
```

Exit codes: `0` all required cases passed (and every requested mutation was
detected); `1` product/bench findings (this is a truthful result, not a bench
failure); `2` harness error (staging/boot — the run proved nothing); `3` usage.

Artifacts land in the run dir (`/tmp/served-reality-bench/<run_id>` unless
`--run-dir`): `manifest.json` (proof envelope), `results.jsonl` (one
machine-readable row per case), `wire-daemon.jsonl` / `wire-daemon.bytes`
(exact bench↔daemon wire), `wire-stub.jsonl` (exact provider-side wire,
including blocked external CONNECT attempts), `daemon.*.log`, the staged `app/`
and isolated `home/`.

## Layout

| Path | Owns |
| --- | --- |
| `runner.py` | CLI, case scheduling, honest exit codes, run manifest |
| `bench.py` | the rig (staging + stub + daemon + wire) and the case-context API |
| `appstage.py` | exact-SHA staging, clean-tree gate, immutability census |
| `daemon.py` | daemon env assembly (determinism + containment), launch, healthz SHA gate, process-tree teardown |
| `provider_stub.py` | deterministic dual-protocol model stub + grounded page + CONNECT containment gate + wire log |
| `wire.py` | byte-exact HTTP client with watchdog + credential redaction |
| `classify.py` | VOOL / PROVIDER / MODEL / HARNESS attribution |
| `schema.py` + `schemas/` | typed result/proof records and their JSON Schema mirrors |
| `corpus.py` | the 23 permanent cases |
| `mutations.py` | six sensitivity controls (stub / app-patch / home-tamper) |
| `selftest.py` | offline instrument self-tests |

## Permanent corpus (23 cases)

ordinary chat · strict literal output · mixed demands (unit coverage) · exact
identifiers/URLs · local→cloud→local lane consent · simultaneous chat isolation
· no-web rule · live search (grounded-or-honest) · unavailable/exhausted key ·
literal file write + manual approval · denied write · shell/test tool ·
attachment · Council · retry exact failure · cancellation · restart continuity
· privacy erasure · corrupted CAS · uncertified author · unsupported claim ·
refusal truth · evidence/receipt correspondence.

## Mutation controls (bench sensitivity)

Each control injects one defect and requires the bench to CATCH it:

| id | defect | injection | target case |
| --- | --- | --- | --- |
| m1-dropped-request-unit | responder drops a demanded unit | stub script | mixed-demands |
| m2-bypassed-permission | permission gate patched to ALLOW | throwaway app clone | denied-write |
| m3-fabricated-tool-success | receipts claim a tool that never ran | throwaway app clone | evidence-receipt-correspondence |
| m4-wrong-turn-evidence | another turn's event rebound to this session | home tamper | simultaneous-chat-isolation |
| m5-corrupted-cas-accepted | corrupted CAS chunk served as good | home tamper | corrupted-cas |
| m6-false-receipt-verification | tampered receipt ledger | home tamper | evidence-receipt-correspondence |

App-patch anchors are verified exactly-once against the staged content; a stale
anchor fails the control loudly instead of silently passing. The pristine
staging, the source worktree and product-runtime files are never touched.

## Laws the bench holds itself to

* **Exact SHA or nothing** — the daemon must report the staged commit and
  `dirty: false` or the run refuses to start.
* **Upstream failure is never green** — provider outages surface as typed
  findings; transport failures are exceptions; a dead daemon is a recorded
  product fact, never a skip.
* **A required case never skips** — unexpected skips (and any skip of a
  required case) are failures.
* **Determinism by default** — scripted providers, isolated vault, contained
  network; live providers only with explicit opt-in.
* **Bounded everything** — per-case wall clock, per-request watchdog, boot
  gate, guaranteed process-tree teardown in every path.
* **Wire truth** — exact bytes retained both directions; secrets redacted by
  value, never by structure.
