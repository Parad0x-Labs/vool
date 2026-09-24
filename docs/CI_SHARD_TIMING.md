# CI shard timing evidence and duration-aware planning

This document explains how VOOL's Linux CI shard matrix is measured, how those
measurements become a better-balanced shard plan, and what each number does and
does not mean. It is the operating manual for two instruments:

- `ops/pytest_timing.py` — the measurement wrapper the `tests` matrix runs.
- `ops/shard_plan.py` — the planner that turns measurements into assignments.

## The problem being measured

The Linux matrix partitions ~2,300 collected test files (≈36k tests) across 10
shards by **descending file size, round-robin** (`.github/workflows/ci.yml`,
"Resolve this shard's test files"). File size is a weak proxy for duration.
The green run [35815768194](https://github.com/Parad0x-Labs/vool/actions/runs/35815768194)
at `a0a160a` measured:

| shard minutes (10 Linux shards) | total raw runner-minutes |
| --- | --- |
| 24.60 – 87.45 (slowest shard ≈ 3.6× the fastest) | ≈ 524 |

Dependency installation is under a minute per job, so the imbalance is test
execution, not setup. The wall-clock bound of the whole matrix is the slowest
shard; that is what duration-aware planning attacks.

## What CI now records (and what it must never change)

Each `tests (N)` job runs the same pytest argv it always ran — same files, same
order, same flags — wrapped once:

```
python ops/pytest_timing.py \
  --output .verification-logs/shard-N-timing.json \
  -- -q --tb=short -p no:cacheprovider <shard-N files, resolver order>
```

The wrapper's contract (`tests/test_pytest_timing.py` pins each clause):

1. **The verdict is pytest's.** The wrapper's exit status IS the wrapped
   pytest's exit status. A red shard stays red.
2. **No behavioral change.** It never selects, skips, reorders, or retries
   tests; pytest's own collection decides the nodes; addopts and plugin
   autoload behave exactly as `python -m pytest` did.
3. **Evidence survives failure.** The manifest is written at session finish —
   including failure sessions — and the artifact step is `if: always()`, so a
   red shard uploads its evidence. If the manifest write itself fails, the
   wrapper turns red: missing evidence can never read as green.
4. **Incomplete runs are honest.** A snapshot with `"complete": false` is
   flushed every 512 test reports; a run killed mid-flight leaves how far it
   got, not silence.

The `verify` job uploads the canonical collection manifest
(`verification-logs-verify` artifact) produced by `ops/pytest_manifest.py`;
the shard artifacts (`verification-logs-shard-N`) carry each shard's file list
and timing manifest. Those two artifact families are the planner's inputs.

### What is in a timing manifest

`vool.pytest-timing.v1`, per shard run:

- per file: collection seconds plus every node's **setup/call/teardown**
  seconds (pytest's own phase durations), node counts, outcome counts;
- per run: `failed_nodes` (nodeid, phase, duration — the full traceback stays
  in the job log), session totals, wall seconds, git HEAD + dirty flag, and an
  environment **family** (python version, `sys.platform`, machine arch,
  GitHub-runner marker). No environment dump, no machine inventory, no
  secrets.

## Planning from evidence

```
python ops/shard_plan.py \
  --manifest pytest-manifest.json \
  --timing shard-0-timing.json --timing shard-1-timing.json ... \
  --shards 10 --platform linux \
  --output plan.json
```

The planner (contract tests in `tests/test_shard_plan.py`):

- takes the file set **only** from the canonical manifest — it invents no
  discovery of its own;
- routes macOS-only files by the workflow's own pinned list, parsed out of
  `ci.yml` (there is no second list to drift);
- uses the **median** measured per-file total across usable samples; files
  without usable measurements (new files, rejected non-finite records, files the
  evidence shows never started executing) get a deterministic fallback weight
  (median of measured files, or an explicit `--fallback-weight`), and every
  fallback file is named in the plan;
- refuses incomplete snapshots and evidence without platform identity; completed
  red runs remain usable measurements, with their failing verdict preserved. A
  completed process is still not automatic full coverage: files absent from the
  evidence (collection errors) or recorded with `started_nodes: 0` (interrupted
  or early-stopped sessions that still reached sessionfinish) count as
  unmeasured fallback, never as measurements;
- refuses to plan from corrupt, wrong-schema, missing, or wholly
  incompatible-platform evidence, and refuses a plan whose assignment is not
  exactly the manifest's Linux files, each once;
- assigns by deterministic LPT (longest file first onto the lightest shard,
  ties by name then shard index) — same inputs, same plan, every time;
- always emits the **dry-run comparison** against the current partition
  (replicated exactly: size-descending round-robin): per-shard and max
  estimated seconds, aggregate, coverage, completeness.

### What the numbers mean — and do not

All planner output is **estimates** derived from recorded runs, labelled as
such. Two distinctions the plan reports explicitly:

- **Wall time vs billed compute.** A shorter slowest shard is a wall-time
  improvement for the matrix. It is NOT automatically a reduction in total
  runner-minutes: the aggregate is the same files either way. Rebalancing
  pays off when the slowest shard gates delivery (it does today, at ≈87 min).
- **Measured vs estimated.** Numbers under `estimated:` are model outputs from
  past evidence; only a subsequent instrumented run measures the new plan.
  Coverage (`coverage.fraction`) says how much of the plan rests on real
  measurements rather than fallback weights.

Per-file durations exclude per-shard fixed cost (checkout, pip install,
sandbox provisioning — together ≈1 min/job), which is identical across
sharding strategies and therefore not attributable to any file.

## Activation (deliberate, currently pending)

Duration-aware planning is **implemented and tested but not active**: CI still
partitions by size round-robin. Activation is a separate reviewed change, and
the safe sequence is:

1. Let an instrumented run land its artifacts (this branch's CI does exactly
   this; artifacts live 90 days by default).
2. Run `ops/shard_plan.py` over those artifacts; review coverage, the
   comparison, and any fallback files in the PR.
3. If the estimated slowest-shard reduction holds with high coverage, switch
   the resolver to the duration-aware planner in a dedicated PR — keeping the
   macOS routing pin and the completeness guards the resolver already has, and
   keeping `tests/test_delivery_contracts.py` green.
4. The run that first executes a planned assignment is the measured proof;
   compare its slowest shard against the instrumented baseline, not against
   estimates.

Known limitation to respect during activation: test independence is assumed
per file. Re-grouping files can surface order-dependent failures that size
round-robin happened to hide — any such failure must be reproduced and
repaired on its own merits (or classified unresolved), never treated as a
scheduling success or hidden by moving the file.
