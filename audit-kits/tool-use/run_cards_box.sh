#!/bin/bash
# Tool-use measurement, one shard per cloud box: TOOL CARDS copy of run_box.sh.
#   ASKS_PASS=<passphrase> bash audit-kits/tool-use/run_cards_box.sh <label> <git-ref> <shard_index> <shard_count>
# Same as run_box.sh plus: pulls nomic-embed-text (the embedder VOOL provisions for memory on local-model
# installs, which tool cards read meaning through) and writes a counts-only card calibration
# (tools/tool_card_calibrate.py) on the dev asks and on this shard before the run.
# label:   a run name, e.g. base or fix
# git-ref: the VOOL branch or commit under test, fetched from GitHub (e.g. skills/delivery-077-20261029)
# Writes only under /tmp/tooluse-box and audit-kits/tool-use/runs/. Never pushes, never touches a real home,
# never moves money. The ask file is encrypted so builders can't read it; the passphrase comes with the run prompt.
set -euo pipefail
LABEL=$1; REF=$2; IDX=$3; COUNT=$4
: "${ASKS_PASS:?set ASKS_PASS to the passphrase from the run prompt}"
KIT=$(cd "$(dirname "$0")" && pwd)
W=/tmp/tooluse-box
REPO=$W/vool
MODEL=qwen3:8b
mkdir -p "$W"
log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

# 1. code under test
[ -d "$REPO/.git" ] || git clone -q --filter=blob:none https://github.com/Parad0x-Labs/vool "$REPO"
git -C "$REPO" fetch -q origin "$REF"
WT="$W/wt-$LABEL"
[ -d "$WT" ] || git -C "$REPO" worktree add -q --detach "$WT" FETCH_HEAD
log "head $(git -C "$WT" rev-parse --short HEAD) ($REF)"

# 2. python env, CPU-only torch so it fits a small disk
if [ ! -x "$W/venv/bin/python" ]; then
  python3 -m venv "$W/venv"
  "$W/venv/bin/pip" install -q --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch > "$W/pip.log" 2>&1
  "$W/venv/bin/pip" install -q --no-cache-dir -r "$WT/requirements-runtime.txt" zstandard >> "$W/pip.log" 2>&1
fi

# 3. local model (CPU build of Ollama, GPU libraries skipped)
if [ ! -x "$W/ollama/bin/ollama" ]; then
  mkdir -p "$W/ollama"
  curl -sSL -m 900 -o "$W/ollama.tar.zst" https://ollama.com/download/ollama-linux-amd64.tar.zst
  "$W/venv/bin/python" -I - "$W/ollama.tar.zst" "$W/ollama" <<'E'
import sys, tarfile, zstandard
src, dst = sys.argv[1:]
with open(src, "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as r, tarfile.open(fileobj=r, mode="r|") as t:
    for m in t:
        if not any(k in m.name.lower() for k in ("cuda", "rocm", "vulkan", "mlx")):
            t.extract(m, dst, filter="data")
E
  rm -f "$W/ollama.tar.zst"
fi
export OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$W/ollama/models"
if ! curl -sf "http://$OLLAMA_HOST/api/version" >/dev/null; then
  nohup "$W/ollama/bin/ollama" serve > "$W/ollama-serve.log" 2>&1 &
  sleep 5
fi
"$W/ollama/bin/ollama" pull "$MODEL" >/dev/null 2>&1
"$W/ollama/bin/ollama" pull nomic-embed-text >/dev/null 2>&1
log "models: $("$W/ollama/bin/ollama" list | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"

# 4. this shard's cases (every COUNT-th line from IDX) and a fresh fake home per label
ASKS="$W/asks-v1.jsonl"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$KIT/asks-v1.jsonl.enc" -out "$ASKS" -pass env:ASKS_PASS
(cd "$W" && sha256sum -c "$KIT/asks-v1.sha256")
SHARD_FILE="$W/cases-$LABEL-$IDX.jsonl"
awk -v i="$IDX" -v n="$COUNT" '(NR-1) % n == i' "$ASKS" > "$SHARD_FILE"
[ -d "$W/fakehome-$LABEL" ] || cp -r "$KIT/fakehome" "$W/fakehome-$LABEL"

# 4b. tool-card calibration: counts per ask kind only, no ask text (tools/tool_card_calibrate.py)
DEST="$KIT/runs/$LABEL/s$IDX"; mkdir -p "$DEST" "$W/calib-home"
for SET in "dev:$KIT/dev-asks.jsonl" "shard:$SHARD_FILE"; do
  ( cd "$WT" && HOME="$W/calib-home" VOOL_HOME="$W/calib-home/.vool" VOOL_LOCAL_MODELS_ENABLED=1 VOOL_KEYCHAIN_ALLOWED=0 \
    "$W/venv/bin/python" -m tools.tool_card_calibrate "${SET#*:}" --out "$DEST/calibration-${SET%%:*}.json" ) >> "$W/calib.log" 2>&1 \
    || log "calibration ${SET%%:*} failed (see $W/calib.log)"
done
log "calibration: $(cat "$DEST"/calibration-*.json 2>/dev/null | tr -d ' \n' | head -c 400)"

# 5. run
OUT="$W/run-$LABEL-$IDX"; mkdir -p "$OUT"
DEADLINE=${DEADLINE:-$(date -u -d '+170 min' +%Y-%m-%dT%H:%MZ)}
log "run $LABEL shard $IDX/$COUNT ($(wc -l < "$SHARD_FILE") cases) until $DEADLINE"
AUDIT_TURN_TIMEOUT=600 "$W/venv/bin/python" -I "$KIT/harness/audit_ask.py" --repo "$WT" --python "$W/venv/bin/python" \
  --model "$MODEL" --cases "$SHARD_FILE" --fakehome "$W/fakehome-$LABEL" --out "$OUT" --deadline "$DEADLINE" \
  > "$OUT/stdout.log" 2>&1 || log "harness exit $?"
log "done $LABEL shard $IDX: $(wc -l < "$OUT/raw.jsonl") rows"

# 6. results: numbers in the clear, raw rows (which quote the asks) encrypted
"$W/venv/bin/python" -I "$KIT/score.py" "$SHARD_FILE" "$OUT/raw.jsonl" > "$DEST/score.json"
tar -C "$OUT" -czf - raw.jsonl stdout.log model_ready.json 2>/dev/null \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -out "$DEST/raw.tgz.enc" -pass env:ASKS_PASS
log "results in $DEST"
