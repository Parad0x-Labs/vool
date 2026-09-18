#!/usr/bin/env bash
# Verify VOOL - see the signed honesty-receipt proof and check it offline.
# No accounts, no server. Best run after Install_VOOL (uses the local .venv if present).
set -e
SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_ROOT"
export PYTHONPATH="$SCRIPT_ROOT"

if [ -x "$SCRIPT_ROOT/.venv/bin/python" ]; then
  PYEXE="$SCRIPT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYEXE="python3"
elif command -v python >/dev/null 2>&1; then
  PYEXE="python"
else
  echo "Could not find Python 3. Install it from https://python.org or run Install_VOOL first."
  exit 1
fi

echo "Running the VOOL signed honesty-receipt demo..."
echo "(cryptographic proof of what the agent did - verify it yourself, offline, no server)"
echo
"$PYEXE" -m core.honesty_receipt demo
echo
echo "To verify your OWN agent's last real session:  $PYEXE -m core.honesty_receipt verify-last"
