#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
RUNTIME_REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements-runtime.txt"
WHEELHOUSE_DIR="${PROJECT_ROOT}/vendor/wheelhouse"
PYTHON_BIN="${PYTHON_BIN:-}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

say() {
  printf '%s\n' "$*" >&2
}

python_supports_minimum() {
  local candidate="$1"
  "${candidate}" - <<PY >/dev/null 2>&1
import sys
sys.exit(0 if sys.version_info >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)
PY
}

resolve_python_bin() {
  local candidate=""
  for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_supports_minimum "${candidate}"; then
      command -v "${candidate}"
      return 0
    fi
  done

  if command -v uv >/dev/null 2>&1; then
    local uv_target=""
    for uv_target in 3.13 3.12 3.11 3.10; do
      candidate="$(uv python find "${uv_target}" 2>/dev/null || true)"
      if [[ -n "${candidate}" && -x "${candidate}" ]] && python_supports_minimum "${candidate}"; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    done

    say "No supported system Python found. Attempting uv-managed Python bootstrap..."
    for uv_target in 3.12 3.11 3.10; do
      if uv python install "${uv_target}" >/tmp/vool_uv_python_install.log 2>&1; then
        candidate="$(uv python find "${uv_target}" 2>/dev/null || true)"
        if [[ -n "${candidate}" && -x "${candidate}" ]] && python_supports_minimum "${candidate}"; then
          printf '%s\n' "${candidate}"
          return 0
        fi
      fi
    done
  fi

  return 1
}

runtime_python_ready() {
  local candidate="$1"
  [[ -x "${candidate}" ]] || return 1
  "${candidate}" - <<'PY' >/dev/null 2>&1
required = ("starlette", "uvicorn")
for name in required:
    __import__(name)
PY
}

dir_has_files() {
  local dir="$1"
  [[ -d "${dir}" ]] || return 1
  shopt -s nullglob dotglob
  local matches=("${dir}"/*)
  shopt -u nullglob dotglob
  [[ ${#matches[@]} -gt 0 ]]
}

create_or_update_venv() {
  if [[ -x "${VENV_DIR}/bin/python" ]] && ! python_supports_minimum "${VENV_DIR}/bin/python"; then
    say "Workspace virtualenv uses unsupported Python. Rebuilding..."
    rm -rf "${VENV_DIR}"
  fi
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    if [[ -z "${PYTHON_BIN}" ]]; then
      PYTHON_BIN="$(resolve_python_bin)"
    fi
    if [[ -z "${PYTHON_BIN}" ]]; then
      say "ERROR: No supported Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ interpreter was found for workspace bootstrap."
      exit 1
    fi
    say "Creating workspace virtualenv with ${PYTHON_BIN}..."
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  fi
}

ensure_pip() {
  if "${VENV_DIR}/bin/python" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  say "Workspace virtualenv is missing pip. Repairing with ensurepip..."
  "${VENV_DIR}/bin/python" -m ensurepip --upgrade >/tmp/vool_workspace_ensurepip.log 2>&1
}

install_runtime_dependencies() {
  say "Workspace runtime deps missing. Bootstrapping ${VENV_DIR}..."
  ensure_pip
  "${VENV_DIR}/bin/python" -m pip install --upgrade "pip<26" setuptools wheel >/tmp/vool_workspace_pip_bootstrap.log 2>&1
  if dir_has_files "${WHEELHOUSE_DIR}" && [[ -f "${RUNTIME_REQUIREMENTS_FILE}" ]]; then
    if ! "${VENV_DIR}/bin/python" -m pip install --no-index --find-links "${WHEELHOUSE_DIR}" -r "${RUNTIME_REQUIREMENTS_FILE}" >/tmp/vool_workspace_runtime_install.log 2>&1; then
      say "Bundled wheelhouse runtime install failed. Falling back to editable runtime install."
      "${VENV_DIR}/bin/python" -m pip install -e "${PROJECT_ROOT}[runtime,proof]" >/tmp/vool_workspace_runtime_install.log 2>&1
    else
      "${VENV_DIR}/bin/python" -m pip install --no-deps -e "${PROJECT_ROOT}" >>/tmp/vool_workspace_runtime_install.log 2>&1
    fi
  else
    "${VENV_DIR}/bin/python" -m pip install -e "${PROJECT_ROOT}[runtime,proof]" >/tmp/vool_workspace_runtime_install.log 2>&1
  fi
}

main() {
  if runtime_python_ready "${VENV_DIR}/bin/python"; then
    printf '%s\n' "${VENV_DIR}/bin/python"
    return 0
  fi

  create_or_update_venv
  install_runtime_dependencies

  if ! runtime_python_ready "${VENV_DIR}/bin/python"; then
    say "ERROR: Workspace runtime virtualenv is still incomplete after bootstrap."
    say "Check /tmp/vool_workspace_runtime_install.log for dependency failures."
    exit 1
  fi

  printf '%s\n' "${VENV_DIR}/bin/python"
}

main "$@"
