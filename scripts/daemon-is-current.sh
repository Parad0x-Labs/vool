#!/usr/bin/env bash
# Is the running daemon actually serving the code on main?
#
# Four times on 2026-08-05/06 the operator tested the app, hit a bug that had already been fixed,
# and reasonably concluded the fix was broken -- because the daemon was still serving an older
# commit. A daemon does not pick up commits until it is restarted, and its version string cannot be
# trusted on its own: restarting from a dirty tree stamps the LAST COMMIT while running the working
# tree, so a daemon that genuinely had a fix reported a SHA that predated it, and one that lacked a
# fix looked plausible.
#
# So this compares behaviour-relevant FILES, not just the SHA: if any runtime file changed between
# the daemon's build and HEAD, it is stale regardless of what the version says.
#
#   scripts/daemon-is-current.sh            check, exit 1 if stale
#   scripts/daemon-is-current.sh --restart  check, and restart onto main if stale
set -uo pipefail
cd "$(dirname "$0")/.."
PORT="${VOOL_OPENCLAW_API_PORT:-11435}"

pid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
if [ -z "$pid" ]; then
  echo "daemon: NOT RUNNING on port $PORT"; [ "${1:-}" = "--restart" ] || exit 1
fi

head_sha="$(git rev-parse --short HEAD)"
origin_sha="$(git rev-parse --short origin/main 2>/dev/null || echo '-')"
# ASK THE RUNNING DAEMON, do not scrape logs. The first version of this script globbed
# /tmp/vool_*.log and took the newest match, which reported a build from a daemon that had already
# been replaced -- it called a current daemon stale on its very first run. The process itself is the
# only authority on what code it loaded.
build="$(curl -s --max-time 20 -X POST "http://127.0.0.1:$PORT/api/chat" \
         -H 'Content-Type: application/json' \
         -d '{"model":"vool","stream":false,"messages":[{"role":"user","content":"version"}]}' \
         2>/dev/null | grep -o 'Commit: .[0-9a-f]*' | grep -o '[0-9a-f]\{8,\}' | head -1 | cut -c1-8)"

echo "main        : $head_sha"
echo "origin/main : $origin_sha"
echo "daemon      : ${build:-unknown} (pid ${pid:-none})"

# WHICH INSTALL is serving the port, not just which commit it reports.
# On 2026-08-06 the port was held by ~/Applications/vool -- a SEPARATE clone tracking origin/main,
# 131 commits behind this repo -- after the repo daemon exited and freed the port. Every fix made
# here was absent from the app being tested, and a commit-only comparison cannot explain why: it
# reports "stale" for a daemon that no `git pull` in THIS directory will ever update.
repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
daemon_cmd="$(ps -o command= -p "${pid:-0}" 2>/dev/null)"
daemon_home=""
case "$daemon_cmd" in
  */*) daemon_home="$(printf '%s' "$daemon_cmd" | awk '{print $1}' | sed 's|/.venv/bin/python.*||; s|/bin/python.*||')" ;;
esac
foreign=0
if [ -n "$daemon_home" ] && [ -n "$repo_root" ] && [ "$daemon_home" != "$repo_root" ]; then
  foreign=1
  echo "  SERVING INSTALL: $daemon_home"
  echo "  THIS REPO      : $repo_root"
  echo "  WARNING: the daemon on this port runs from a DIFFERENT checkout."
  echo "           Committing or pulling here will NOT change what the app runs."
fi

stale=0
if [ -z "$build" ]; then
  echo "  VERDICT: build unknown -- treat as stale"; stale=1
else
  changed="$(git diff --name-only "$build"..HEAD -- core/ apps/ tools/ adapters/ storage/ 2>/dev/null)"
  # Committed drift is only half the question. The daemon loads the WORKING TREE, so a file edited
  # after it started makes it stale even when HEAD has not moved -- and comparing commits alone
  # cannot see that. On 2026-08-06 this printed CURRENT while the running daemon predated an
  # uncommitted fix to core/bootstrap_context.py, and --restart therefore did nothing: exactly the
  # false "the app has your fix" this script exists to prevent.
  if [ -n "${pid:-}" ]; then
    started="$(ps -o lstart= -p "$pid" 2>/dev/null)"
    if [ -n "$started" ]; then
      newer="$(find core apps tools adapters storage -name '*.py' -newermt "$started" 2>/dev/null)"
      changed="$(printf '%s\n%s' "$changed" "$newer" | grep -v '^$' | sort -u)"
    fi
  fi
  if [ -n "$changed" ]; then
    echo "  VERDICT: STALE -- runtime files changed since the daemon started:"
    echo "$changed" | sed 's/^/    /'
    stale=1
  else
    echo "  VERDICT: CURRENT -- no runtime file differs from main, and none edited since it started"
  fi
fi
[ "$head_sha" != "$origin_sha" ] && echo "  NOTE: main is not pushed to origin"

if [ "$foreign" = "1" ] && [ "${1:-}" = "--restart" ]; then
  echo "  REFUSING --restart: the port is served by $daemon_home, not this repo."
  echo "  Update that install (fetch + checkout there), or stop it before restarting from here."
  echo "  Restarting from this tree would swap the user's app for a repo process and hide the gap."
  exit 2
fi

if [ "$stale" = "1" ] && [ "${1:-}" = "--restart" ]; then
  echo "  restarting onto $head_sha ..."
  [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null
  while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; do sleep 1; done
  VOOL_OPENCLAW_API_PORT="$PORT" VOOL_HOME="${VOOL_HOME:-$HOME/.vool_runtime}" \
  VOOL_WORKSPACE_ROOT="${VOOL_WORKSPACE_ROOT:-$HOME/.vool_runtime/workspace}" \
  VOOL_CONTEXT_CAPSULE_V2=1 VOOL_ADAPTIVE_CONTEXT=1 VOOL_REGISTER_INSTALLED_OLLAMA_MODELS=1 \
  VOOL_PUBLIC_HIVE_SSH_KEY_PATH= VOOL_PUBLIC_HIVE_WATCH_HOST= \
  nohup .venv/bin/python -m apps.vool_api_server > "/tmp/vool_$head_sha.log" 2>&1 &
  until curl -sf --max-time 2 "http://127.0.0.1:$PORT/api/tags" >/dev/null 2>&1; do sleep 1; done
  grep -o 'Runtime build: [^|]*' "/tmp/vool_$head_sha.log" | head -1
  exit 0
fi
exit "$stale"
