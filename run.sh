#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$(bash "${SCRIPT_DIR}/scripts/ensure_workspace_runtime.sh")"

if [[ ! -f "${VENV_PY}" ]]; then
  echo "ERROR: Virtual environment not found at ${SCRIPT_DIR}/.venv"
  echo "Run the installer first: bash Install_And_Run_VOOL.sh"
  exit 1
fi

export PYTHONPATH="${SCRIPT_DIR}"
export VOOL_HOME="${VOOL_HOME:-${HOME}/.vool_runtime}"

echo "Starting Vool Hive Mind..."
echo "API: http://127.0.0.1:11435"
exec "${VENV_PY}" -m apps.vool_api_server
