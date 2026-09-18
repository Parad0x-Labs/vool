# live_drive — driving the shipped app and grading what comes back

These are the instruments behind the 2026-08-18 findings. They are here rather than in a scratch
directory because rule 0.9 exists: a measurement script that is not in git did not happen — it
cannot be re-run, cited, or inherited, and the next agent rebuilds it from scratch.

Everything here drives the **app's own runtime** (`127.0.0.1:11435`), not a scratch daemon. What the
shipped app does is the only thing that counts.

## The pieces

| file | what it is |
|---|---|
| `overnight.py` | the driver: runs a prompt set against the app, writing `FINDINGS.md` after **every turn** so an interrupted run still leaves everything it learned |
| `orchestrator.sh` | the automation: one job on the daemon at a time (mkdir-lock), and a landed commit **jumps the queue** with a resync + regression |
| `resync.sh` | copies the worktree into the `.app` bundle and restarts it — the runtime is frozen at the SHA it booted with, so a commit does **not** reach the app on its own |
| `compare_runs.py` | turn-by-turn diff of two runs, separating an **upstream outage** from a code regression (this subsumes an earlier `compare_lanes.py`, which split failures into VOOL-defect / model-ceiling / provider-failed but depended on a set-specific oracle) |
| `grade_noise.py` | differential: did typing noise break a prompt that works when typed cleanly? |
| `sets/` | the prompt corpora |

## Configuration

Relocatable; three environment variables, no hardcoded paths:

```bash
export VOOL_APP=~/Applications/VOOL-Test.app     # required: the bundle to drive
export VOOL_WORKTREE=/path/to/worktree            # defaults to two levels up
export VOOL_PY=/path/to/python                    # defaults to python3
```

## The two things these exist to stop

**1. A regression proves nothing broke. It does not prove the fix works.** Measured 2026-08-18: of
the 123 regression prompts, only ~7 touched the three code changes in the run they were added for —
4 SSRF-download, 2 unit-as-place, 1 blocked-live-info. `sets/set_fixes.json` is the other half: one
lane per landed fix, driven end to end, each with its must-keep control beside it so a fix that
over-corrects is caught in the same run.

**2. A grader that flatters the new run is worse than no grader, because it is believed.** The first
`compare_runs.py` carried a short list of failure phrases and called anything else "served". It was
wrong three times out of three on 2026-08-18 — it reported an answer of raw Python source, a
`TimeoutError` on every city, and a wrong-city refusal as *improvements* — and it also missed real
failures, reporting two turns that lost live data as "unchanged". The current version keeps a
failure vocabulary of what was actually observed and separates a transport outage from a code
regression, because "we broke the weather lane" and "wttr.in was down" are different sentences.

## Reading a result

`run-<name>/FINDINGS.md` is written incrementally; `run-<name>/<set>.json` is the machine-readable
record with route, timing and the answer per turn. Compare two runs with:

```bash
python compare_runs.py run-regr-<old-sha>-<time> run-regr-<new-sha>-<time>
```

Turns whose failure names a timeout or a dead provider are reported separately and are **not**
evidence about the commit under test.
