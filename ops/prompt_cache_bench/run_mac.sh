#!/usr/bin/env bash
# Prompt-cache bench on the operator's Mac. Usage:
#   PY=<python with VOOL's deps> RUN_ROOT=<scratch folder> bash ops/prompt_cache_bench/run_mac.sh baseline|after|grade
# Runs nice'd from this clone; every arm uses a throwaway VOOL profile and HOME under RUN_ROOT and
# removes it afterwards (evidence files stay). The OpenRouter key is VOOL's own saved key, read
# through VOOL's credential store in memory; it is never printed or written. Hard stop: CAP_USD.
set -euo pipefail
PHASE="${1:?baseline|after|grade}"
: "${PY:?set PY to a python that has VOOL's dependencies}"
: "${RUN_ROOT:?set RUN_ROOT to a scratch folder outside this clone}"
CAP_USD="${CAP_USD:-20}"
REPEATS="${REPEATS:-2}"
BASE_SHA="${BASE_SHA:-06ff4dd5}"
CLAUDE_OR="${CLAUDE_OR:-anthropic/claude-sonnet-4.5}"
CLAUDE_CLI="${CLAUDE_CLI:-claude-sonnet-4-5}"
GPT_OR="${GPT_OR:-openai/gpt-4.1-mini}"
HEAD_SHA="$(git rev-parse HEAD)"
mkdir -p "$RUN_ROOT"
case "$RUN_ROOT" in "$(pwd)"*) echo "RUN_ROOT must be outside the clone"; exit 3;; esac
B=(nice -n 10 "$PY" -m ops.prompt_cache_bench.bench)
COMMON=(--run-root "$RUN_ROOT" --cap-usd "$CAP_USD" --key-source vool-store --repeats "$REPEATS")

k3_id() {
  "${B[@]}" preflight --models "$CLAUDE_OR,$GPT_OR" --search kimi-k3 \
    | "$PY" -c 'import json,sys; s=json.load(sys.stdin).get("search") or []; print(sorted(s, key=len)[0] if s else "")'
}

case "$PHASE" in
  baseline)
    "${B[@]}" preflight --models "$CLAUDE_OR,$GPT_OR" --search kimi-k3 | tee "$RUN_ROOT/preflight.json"
    K3="$(k3_id)"; echo "K3 model on OpenRouter: ${K3:-none found}"
    "${B[@]}" cli "${COMMON[@]}" --label cli-claude --cli-model "$CLAUDE_CLI" --route login --clean-scratch
    "${B[@]}" vool "${COMMON[@]}" --label vool-base-claude --sha "$BASE_SHA" --model "$CLAUDE_OR" --cache off --clean-scratch
    "${B[@]}" vool "${COMMON[@]}" --label vool-base-gpt --sha "$BASE_SHA" --model "$GPT_OR" --cache off --clean-scratch
    if [ -n "$K3" ]; then
      "${B[@]}" vool "${COMMON[@]}" --label vool-base-k3 --sha "$BASE_SHA" --model "$K3" --cache off --clean-scratch
    fi
    ;;
  after)
    "${B[@]}" vool "${COMMON[@]}" --label vool-after-claude-system --sha "$HEAD_SHA" --model "$CLAUDE_OR" --cache system --clean-scratch
    "${B[@]}" vool "${COMMON[@]}" --label vool-after-claude-history --sha "$HEAD_SHA" --model "$CLAUDE_OR" --cache system_history --clean-scratch
    ;;
  grade)
    "${B[@]}" grade "${COMMON[@]}" --judge-model "${JUDGE:-openai/gpt-4.1-mini}"
    ;;
  *) echo "unknown phase $PHASE"; exit 3;;
esac
"${B[@]}" report --run-root "$RUN_ROOT"
