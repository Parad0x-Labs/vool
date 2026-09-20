#!/usr/bin/env bash
# D4: post-push census loop -- run the real shard gate on a commit, then turn its
# output into a per-file failure cluster table, then CLEAN UP after itself.
#
# Why: after a push, the question is "what is still red, clustered by cause?".
# A full ad-hoc census costs ~40 manual minutes; this is the standing version.
#
# Hard rules baked in:
#   * --timeout-seconds 10800, NEVER the 1800s default (the default kills
#     legitimate long shards and reports them as failures they are not).
#   * Refuses to start while another pytest run is active (pgrep -f pytest):
#     prints and exits. It never queues silently and never competes with the
#     main worker's runs.
#   * Cleans its artifact root and the pytest tmp roots THIS run created
#     (marker-file scoped: another run's tmp dirs are never touched).
#
# Usage:
#   tools/dev/census_after_push.sh [--workers N] <commit-or-branch> [paths...]
#     --workers N   parallel shards (default 4)
#     paths...      optional scope; omit for the full canonical suite. For any
#                   real census pass an explicit small scope unless you mean it.
#
# Environment: PYTHON overrides the interpreter (default python3). Use the
# shared mission venv's absolute path on this machine.
#
# Exit code: 0 when the census ran and wrote its summary (failing TESTS are the
# census's data, not its failure); nonzero on operational failure.
set -euo pipefail

WORKERS=4
TIMEOUT_SECONDS=10800
PYTHON="${PYTHON:-python3}"

usage() { sed -n '2,30p' "$0" >&2; exit 2; }
while [ $# -gt 0 ] && [ "$1" = "--workers" ]; do
  WORKERS="$2"; shift 2
done
[ $# -ge 1 ] || usage
REF="$1"; shift
PATHS=("$@")

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

# --- Guard: never compete with a live pytest run (print and exit, no queue). ---
if pgrep -f pytest >/dev/null 2>&1; then
  echo "census: refusing to start, a pytest run is already active:" >&2
  pgrep -fl pytest >&2 || true
  echo "census: not queueing; re-run when the active run finishes." >&2
  exit 1
fi

# --- Resolve the ref and check out its exact commit (detached; branches untouched). ---
ORIG_HEAD="$(git rev-parse HEAD)"
TARGET="$(git rev-parse --verify --quiet "$REF^{commit}")" || {
  echo "census: cannot resolve ref: $REF" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || {
  echo "census: worktree is dirty; commit or stash first (never swept into a census)" >&2; exit 1; }
RESTORE=0
if [ "$ORIG_HEAD" != "$TARGET" ]; then
  git checkout --detach --quiet "$TARGET"
  RESTORE=1
fi
restore_head() {
  [ "$RESTORE" = 1 ] || return 0
  git checkout --detach --quiet "$ORIG_HEAD" 2>/dev/null || \
    git checkout --quiet "$(git branch --show-current)" 2>/dev/null || true
}
trap restore_head EXIT

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARTROOT="$REPO/.verification-logs/census-$STAMP"
SUMMARY="$REPO/census-summary.md"
MARKER="$(mktemp /tmp/.vool-census-marker-XXXXXX)"

echo "census: ref=$REF commit=$(git rev-parse --short HEAD) workers=$WORKERS timeout=${TIMEOUT_SECONDS}s"
echo "census: scope=${PATHS[*]:-<full canonical suite>}"
echo "census: artifact root $ARTROOT"
df -h / | tail -1

SHARD_RC=0
"$PYTHON" ops/pytest_shards.py \
  --workers "$WORKERS" \
  --timeout-seconds "$TIMEOUT_SECONDS" \
  --label "census-$STAMP" \
  --artifact-root "$ARTROOT" \
  ${PATHS[@]+"${PATHS[@]}"} || SHARD_RC=$?

echo "census: shard gate exit $SHARD_RC; building cluster table"

# --- Build census-summary.md from the shard execution manifests + logs. ---
"$PYTHON" - "$ARTROOT" "$SUMMARY" "$REF" "$(git rev-parse HEAD)" "$WORKERS" "$TIMEOUT_SECONDS" "$SHARD_RC" <<'PYEOF'
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

artroot, summary_path, ref, commit, workers, timeout, shard_rc = sys.argv[1:8]
artroot = Path(artroot)
run_roots = sorted(p for p in artroot.glob("vool-pytest-shards-*") if p.is_dir())
lines = []
lines.append("# Census summary")
lines.append("")
lines.append(f"- generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
lines.append(f"- ref: `{ref}`  commit: `{commit}`")
lines.append(f"- workers: {workers}  shard timeout: {timeout}s  shard gate exit: **{shard_rc}**")
scope_files = 0

totals = Counter()
per_file = defaultdict(Counter)
shard_rows = []
failed_nodes = []
for run_root in run_roots:
    logs = run_root / "logs"
    assignments = {}
    afile = run_root / "shard-assignments.json"
    if afile.is_file():
        payload = json.loads(afile.read_text(encoding="utf-8"))
        assignments = {a["index"]: a for a in payload.get("assignments", [])}
    for idx in sorted(assignments):
        manifest = logs / f"shard-{idx}-execution.json"
        log = logs / f"shard-{idx}.log"
        assigned = len(assignments[idx].get("targets", []))
        if not manifest.is_file():
            state = "KILLED by fail-fast (no execution manifest)"
            shard_rows.append((idx, assigned, state, 0, 0))
            continue
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            shard_rows.append((idx, assigned, "manifest unreadable", 0, 0))
            continue
        s = m.get("summary", {})
        f_count = int(s.get("failed", 0))
        p_count = int(s.get("passed", 0))
        state = "completed" if int(m.get("exitstatus", 1)) == 0 else f"completed, pytest exit {m['exitstatus']}"
        shard_rows.append((idx, assigned, state, f_count, p_count))
        for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "errors", "collected_total", "started_total", "executed_total"):
            totals[key] += int(s.get(key, 0))
        for path, counts in (s.get("files") or {}).items():
            per_file[path].update({k: int(v) for k, v in counts.items()})
            scope_files += 1
        for entry in m.get("failed", []):
            failed_nodes.append(str(entry.get("nodeid", "")))

# Error signatures from the short-test-summary FAILED lines in each shard log.
sig_counter = Counter()
sig_files = defaultdict(set)
for run_root in run_roots:
    for log in (run_root / "logs").glob("shard-*.log"):
        if not log.is_file():
            continue
        for raw in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.startswith("FAILED "):
                continue
            body = raw[len("FAILED "):]
            nodeid, _, err = body.partition(" - ")
            sig = err.strip().splitlines()[0] if err.strip() else "<no message>"
            if len(sig) > 140:
                sig = sig[:137] + "..."
            sig = sig.replace("|", "\\|")
            sig_counter[sig] += 1
            sig_files[sig].add(nodeid.split("::", 1)[0])

lines.append(f"- shards completed: {sum(1 for r in shard_rows if r[2].startswith('completed'))}/{len(shard_rows)}"
             f"  (a shard missing its manifest was killed by the gate's fail-fast: its files are NOT in this table)")
lines.append(f"- totals: collected={totals['collected_total']} executed={totals['executed_total']} "
             f"passed={totals['passed']} failed={totals['failed']} errors={totals['errors']} "
             f"skipped={totals['skipped']} xfailed={totals['xfailed']} xpassed={totals['xpassed']}")
lines.append("")
lines.append("## Per-file outcomes (completed shards only)")
lines.append("")
lines.append("| file | failed | passed | executed | skipped |")
lines.append("| --- | ---: | ---: | ---: | ---: |")
for path, counts in sorted(per_file.items(), key=lambda kv: (-kv[1].get("failed", 0), kv[0])):
    lines.append(
        f"| {path} | {counts.get('failed', 0)} | {counts.get('passed', 0)} | "
        f"{counts.get('executed_total', 0)} | {counts.get('skipped', 0)} |"
    )
lines.append("")
lines.append("## Failure clusters (signatures from shard log summaries)")
lines.append("")
if sig_counter:
    lines.append("| count | signature | files |")
    lines.append("| ---: | --- | --- |")
    for sig, count in sig_counter.most_common():
        files = sorted(sig_files[sig])
        preview = ", ".join(files[:3]) + (" ..." if len(files) > 3 else "")
        lines.append(f"| {count} | `{sig}` | {preview} |")
else:
    lines.append("(no FAILED summary lines found -- either nothing failed in the completed shards, "
                 "or failures produced no short-summary section)")
lines.append("")
lines.append("## Shard status")
lines.append("")
lines.append("| shard | files assigned | status | failed | passed |")
lines.append("| ---: | ---: | --- | ---: | ---: |")
for idx, assigned, state, f_count, p_count in shard_rows:
    lines.append(f"| {idx} | {assigned} | {state} | {f_count} | {p_count} |")
lines.append("")
Path(summary_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"census: wrote {summary_path} ({totals['failed']} failed nodes across completed shards)")
PYEOF

# --- Cleanup (Rule 12): artifact root goes, tmp roots created by THIS run go. ---
rm -rf "$ARTROOT"
find /tmp -maxdepth 2 -name 'pytest-*' -newer "$MARKER" -exec rm -rf {} + 2>/dev/null || true
rm -f "$MARKER"
echo "census: cleaned artifact root and this run's pytest tmp roots"
df -h / | tail -1

exit 0
