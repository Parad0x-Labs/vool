# Local speed benchmarks

One JSON per release, written by `ops/speed_tracker.py --store` (or automatically on the first
run). Each file holds per-turn-type medians — prompt bytes, serial calls before the answer,
uncached prefill bytes, wall time to the answer, models touched, runner reloads — measured
model-free through the real daemon against the in-repo recording engine (`ops/
local_speed_engine.py`); files with `"engine"` set to a loopback URL also carry the real
engine's own prompt_eval/eval timings (opt-in `--engine-url`, never the default).

The tracker in CI compares the newest file against the current run and fails when a turn type
regresses more than 10% (or reloads grow). Delete stale baselines only when a release
deliberately changes the turn set.
