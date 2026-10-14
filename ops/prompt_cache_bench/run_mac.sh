#!/usr/bin/env bash
# Prompt-cache bench on the operator's Mac, one paste per phase. Usage, from a fresh clone:
#   PY=<python with VOOL's deps> RUN_ROOT=<scratch folder> bash ops/prompt_cache_bench/run_mac.sh <phase>
# Phases: baseline | after | grade | after_grade | all, or check (the checks only, no arm), or
# dryrun (every arm end to end on a stand-in provider and a stand-in Claude Code: free, reads no
# key, calls no model; it also says where the real run will look for the key, without reading it).
# Every phase first checks every outside piece (python, this clone, the commits, disk, the
# installed VOOL profile's saved OpenRouter key, Claude Code's sign-in, a VOOL boot) and stops in
# plain words before any arm if one is missing. Then each arm runs nice'd in a throwaway VOOL
# profile and HOME under RUN_ROOT, removed afterwards (evidence stays). A failed arm never stops
# the next; the cap stops every later paid arm. REPORT.md and spend.json are always written.
# The key is read in memory through VOOL's own credential store and is never printed or written.
# Optional: CAP_USD (20), REPEATS (2), ARMS=<label,...>, KEY_HOME=<VOOL profile>, BASE_SHA.
set -u
PHASE="${1:-}"
case "$PHASE" in
  baseline|after|grade|after_grade|all) EXTRA="";;
  check) PHASE=baseline; EXTRA="--check-only";;
  dryrun) PHASE=all; EXTRA="--dryrun";;
  *) echo "usage: run_mac.sh baseline|after|grade|after_grade|all|check|dryrun"; exit 3;;
esac
if [ -z "${PY:-}" ]; then echo "set PY to a python with the VOOL dependencies"; exit 3; fi
if [ -z "${RUN_ROOT:-}" ]; then echo "set RUN_ROOT to a scratch folder outside this clone"; exit 3; fi
case "$RUN_ROOT" in /*) ;; *) echo "RUN_ROOT must be an absolute path"; exit 3;; esac
cd "$(dirname "$0")/../.." || exit 3
mkdir -p "$RUN_ROOT" || exit 3
ARGS=(run --phase "$PHASE" --run-root "$RUN_ROOT" --cap-usd "${CAP_USD:-20}" --key-source vool-store
      --repeats "${REPEATS:-2}" --base-sha "${BASE_SHA:-06ff4dd5}")
if [ -n "${ARMS:-}" ]; then ARGS+=(--arms "$ARMS"); fi
if [ -n "${KEY_HOME:-}" ]; then ARGS+=(--key-home "$KEY_HOME"); fi
if [ -n "${CLAUDE_BIN:-}" ]; then ARGS+=(--claude-bin "$CLAUDE_BIN"); fi
if [ -n "$EXTRA" ]; then ARGS+=("$EXTRA"); fi
echo "bench $(git rev-parse HEAD) phase ${1} into $RUN_ROOT"
exec nice -n 10 "$PY" -m ops.prompt_cache_bench.bench "${ARGS[@]}"
