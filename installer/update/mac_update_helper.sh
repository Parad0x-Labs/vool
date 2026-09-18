#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# mac_update_helper.sh — the EXTERNAL macOS swap helper for VOOL self-update.
#
# The app cannot restart itself after exiting, so the updater hands the final
# wait→swap→relaunch sequence to this detached script:
#
#   mac_update_helper.sh --app /path/VOOL.app \
#                        --staged /path/.vool-stage-<txid>/VOOL.app \
#                        --txid <txid> \
#                        [--wait-pid <pid>] [--wait-seconds 30] \
#                        [--relaunch-cmd "<argv>"] \
#                        --result /path/result.json
#
# Behaviour:
#   1. waits until --wait-pid exits (poll; a missing pid counts as exited);
#   2. moves the current app aside:  VOOL.app → .VOOL.app.prior-<txid>
#      (same directory ⇒ rename-atomic; the prior is NEVER deleted here);
#   3. moves the staged bundle into place — on failure undoes step 2 immediately;
#   4. writes a JSON result ({"ok": true/false, "detail": ...}) to --result;
#   5. optionally relaunches via --relaunch-cmd (a full argv, run detached).
#
# Exit code 0 iff the swap succeeded. This script NEVER deletes a prior bundle
# and NEVER touches anything outside --app's parent directory.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

APP=""
STAGED=""
TXID=""
WAIT_PID=""
WAIT_SECONDS="30"
RELAUNCH_CMD=""
RESULT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="$2"; shift 2 ;;
    --staged) STAGED="$2"; shift 2 ;;
    --txid) TXID="$2"; shift 2 ;;
    --wait-pid) WAIT_PID="$2"; shift 2 ;;
    --wait-seconds) WAIT_SECONDS="$2"; shift 2 ;;
    --relaunch-cmd) RELAUNCH_CMD="$2"; shift 2 ;;
    --result) RESULT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*" >&2; }

write_result() {
  # $1 = ok ("true"/"false"), $2 = detail
  if [[ -n "${RESULT}" ]]; then
    mkdir -p "$(dirname "${RESULT}")"
    printf '{"ok": %s, "detail": "%s", "txid": "%s", "finished_at": %s}\n' \
      "$1" "$(printf '%s' "$2" | sed 's/"/\\"/g')" "${TXID}" "$(date +%s)" > "${RESULT}"
  fi
}

[[ -n "${APP}" && -n "${STAGED}" && -n "${TXID}" ]] || { say "usage: --app --staged --txid are required"; exit 2; }
[[ -d "${STAGED}" ]] || { say "staged bundle missing: ${STAGED}"; write_result false "staged bundle missing"; exit 1; }
[[ -d "${APP}" ]] || { say "app missing: ${APP}"; write_result false "app missing"; exit 1; }

# 1) wait for the app to exit. A pid is "gone" when ps no longer lists it OR it is a
#    zombie (state Z): the parent may not have reaped it yet, but it is not running.
if [[ -n "${WAIT_PID}" ]]; then
  deadline=$(( $(date +%s) + WAIT_SECONDS ))
  while true; do
    state="$(ps -p "${WAIT_PID}" -o stat= 2>/dev/null | tr -d ' ' || true)"
    if [[ -z "${state}" || "${state}" == Z* ]]; then
      break
    fi
    if [[ $(date +%s) -ge ${deadline} ]]; then
      say "app (pid ${WAIT_PID}) did not exit within ${WAIT_SECONDS}s"
      write_result false "app did not exit in time"
      exit 1
    fi
    sleep 0.2
  done
fi

PRIOR="$(dirname "${APP}")/.$(basename "${APP}").prior-${TXID}"
if [[ -e "${PRIOR}" ]]; then
  say "prior already exists: ${PRIOR}"
  write_result false "prior already exists for this transaction"
  exit 1
fi

# 2) move the current app aside (atomic rename, same directory)
if ! mv "${APP}" "${PRIOR}"; then
  say "could not move the current app aside"
  write_result false "could not move the current app aside"
  exit 1
fi

# 3) move the staged bundle into place; undo on failure
if ! mv "${STAGED}" "${APP}"; then
  say "could not move the staged bundle into place — restoring the previous app"
  mv "${PRIOR}" "${APP}" || say "WARNING: restore ALSO failed; previous app is at ${PRIOR}"
  write_result false "staged bundle could not be moved into place"
  exit 1
fi

write_result true "swapped; previous version kept at ${PRIOR}"

# 4) optional relaunch (detached)
if [[ -n "${RELAUNCH_CMD}" ]]; then
  # shellcheck disable=SC2086
  if ! (eval "exec ${RELAUNCH_CMD}" >/dev/null 2>&1 &) ; then
    say "swap succeeded but relaunch command failed: ${RELAUNCH_CMD}"
    exit 0  # the swap itself succeeded; relaunch is the app supervisor's fallback
  fi
fi

exit 0
