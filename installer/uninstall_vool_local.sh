#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_HOME="${VOOL_HOME:-$HOME/.vool_runtime}"
if [[ -z "${VOOL_HOME:-}" && -n "${NULLA_HOME:-}" ]]; then
  RUNTIME_HOME="${NULLA_HOME}"
fi
INSTALL_ROOT="${VOOL_INSTALL_ROOT:-$HOME/vool-local}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw-default}"
OPENCLAW_AGENT_DIR="${VOOL_OPENCLAW_AGENT_DIR:-$HOME/.openclaw/agents/main/agent/vool}"
LAUNCH_AGENT_PATH="${VOOL_LAUNCH_AGENT_PATH:-$HOME/Library/LaunchAgents/ai.vool.runtime.plist}"
OLLAMA_MODELS_DIR="${OLLAMA_MODELS:-$HOME/.ollama/models}"
VOOL_LLAMACPP_MODELS_DIR="${VOOL_LLAMACPP_MODELS_DIR:-${RUNTIME_HOME}/models/llamacpp}"
AUTO_YES=0
REMOVE_DATA=0


say() {
  printf '%s\n' "$*"
}


usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --yes, -y                 Remove the local VOOL install without prompting
  --remove-data             Also delete your saved chats and settings (asks once
                            unless --yes is given)
  --runtime-home <path>     Override runtime home (default: ${RUNTIME_HOME})
  --install-root <path>     Override install root (default: ${INSTALL_ROOT})
  --openclaw-home <path>    Override isolated OpenClaw home (default: ${OPENCLAW_HOME})
  --openclaw-agent <path>   Override OpenClaw agent dir (default: ${OPENCLAW_AGENT_DIR})
  --help, -h                Show this help
EOF
}


parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes|-y)
        AUTO_YES=1
        ;;
      --remove-data)
        REMOVE_DATA=1
        ;;
      --runtime-home)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --runtime-home requires a value."; exit 2; }
        RUNTIME_HOME="$1"
        ;;
      --install-root)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --install-root requires a value."; exit 2; }
        INSTALL_ROOT="$1"
        ;;
      --openclaw-home)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --openclaw-home requires a value."; exit 2; }
        OPENCLAW_HOME="$1"
        ;;
      --openclaw-agent)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --openclaw-agent requires a value."; exit 2; }
        OPENCLAW_AGENT_DIR="$1"
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        say "ERROR: Unknown option: $1"
        usage
        exit 2
        ;;
    esac
    shift
  done
}


prompt_yn() {
  local label="$1"
  local default_value="$2"
  local value=""
  read -r -p "${label} [${default_value}]: " value || true
  value="$(printf '%s' "${value:-$default_value}" | tr '[:upper:]' '[:lower:]')"
  case "${value}" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}


trash_or_remove() {
  local target="$1"
  [[ -e "${target}" || -L "${target}" ]] || return 0
  if command -v trash >/dev/null 2>&1; then
    trash "${target}" >/dev/null 2>&1 || rm -rf "${target}"
    return 0
  fi
  if command -v gio >/dev/null 2>&1; then
    gio trash "${target}" >/dev/null 2>&1 || rm -rf "${target}"
    return 0
  fi
  rm -rf "${target}"
}


remove_empty_tree() {
  local target="$1"
  [[ -d "${target}" ]] || return 0
  find "${target}" -depth -type d -empty -exec rmdir {} \; >/dev/null 2>&1 || true
  [[ -d "${target}" ]] && rmdir "${target}" >/dev/null 2>&1 || true
}


uninstall_python() {
  if [[ -x "${INSTALL_ROOT}/.venv/bin/python" ]]; then
    printf '%s\n' "${INSTALL_ROOT}/.venv/bin/python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}


stop_runtime_processes() {
  local helper="${SCRIPT_DIR}/uninstall_local_runtime.py"
  local python_bin
  if ! python_bin="$(uninstall_python)" || [[ ! -f "${helper}" ]]; then
    # Without the checker nothing can be proven about a recorded server, and an
    # uninstall must never remove files from under a process it could not check.
    if [[ -e "${RUNTIME_HOME}/data/vool_api.pid" ]]; then
      say "VOOL could not check whether it is still running, so nothing was removed."
      say "Close VOOL and run this uninstall again."
      exit 1
    fi
    return 0
  fi
  local output="" rc=0
  output="$("${python_bin}" "${helper}" stop \
    --runtime-home "${RUNTIME_HOME}" \
    --install-root "${INSTALL_ROOT}" \
    --llamacpp-models-dir "${VOOL_LLAMACPP_MODELS_DIR}" 2>&1)" || rc=$?
  if [[ "${rc}" -ne 0 ]]; then
    if [[ "${rc}" -eq 3 && -n "${output}" ]]; then
      say "${output}"
    else
      say "VOOL could not check whether it is still running, so nothing was removed."
      say "Close VOOL and run this uninstall again."
    fi
    exit 1
  fi
  if [[ -n "${output}" ]]; then
    say "${output}"
  fi
  return 0
}


bootout_launch_agent() {
  if [[ "$(uname -s)" == "Darwin" && -e "${LAUNCH_AGENT_PATH}" && -n "${UID:-}" ]]; then
    launchctl bootout "gui/${UID}" "${LAUNCH_AGENT_PATH}" >/dev/null 2>&1 || true
  fi
}


remove_targets() {
  local mac_desktop="${HOME}/Desktop"
  local linux_desktop="${HOME}/Desktop"
  local -a targets=(
    "${INSTALL_ROOT}"
    "${OPENCLAW_HOME}"
    "${OPENCLAW_AGENT_DIR}"
    "${LAUNCH_AGENT_PATH}"
    "${mac_desktop}/Start_VOOL.command"
    "${mac_desktop}/Talk_To_VOOL.command"
    "${mac_desktop}/OpenClaw_VOOL.command"
    "${linux_desktop}/OpenClaw_VOOL.desktop"
  )
  for target in "${targets[@]}"; do
    trash_or_remove "${target}"
  done
  remove_empty_tree "${INSTALL_ROOT}"
  remove_empty_tree "${RUNTIME_HOME}"
}


report_kept_data() {
  if [[ -d "${RUNTIME_HOME}" ]]; then
    say "Your chats and settings were kept, in: ${RUNTIME_HOME}"
    say "A fresh VOOL install will find them there again."
    say "To delete them too, delete that folder, or run this uninstall again with --remove-data."
  else
    say "No saved chats and settings were found; nothing was kept."
  fi
  if [[ -d "${OLLAMA_MODELS_DIR}" ]]; then
    say "Ollama keeps its own models in: ${OLLAMA_MODELS_DIR}"
    say "VOOL does not own that folder, so it was left alone."
  fi
}


remove_profile_data() {
  if [[ ! -e "${RUNTIME_HOME}" && ! -L "${RUNTIME_HOME}" ]]; then
    return 0
  fi
  if [[ -L "${RUNTIME_HOME}" || ! -d "${RUNTIME_HOME}" ]]; then
    say "Uninstall refused: ${RUNTIME_HOME} is not a plain VOOL data folder, so it was left alone."
    exit 1
  fi
  if [[ -z "${RUNTIME_HOME}" || "${RUNTIME_HOME}" == "/" || "$(cd "${RUNTIME_HOME}" && pwd)" == "$(cd "${HOME}" && pwd)" ]]; then
    say "Uninstall refused: ${RUNTIME_HOME} is your home folder, so it was left alone."
    exit 1
  fi
  if [[ "${AUTO_YES}" -eq 0 ]]; then
    if ! prompt_yn "Delete your saved chats and settings in ${RUNTIME_HOME} too?" "N"; then
      say "Kept your saved chats and settings."
      return 0
    fi
  fi
  trash_or_remove "${RUNTIME_HOME}"
  say "Deleted your saved chats and settings."
}


main() {
  parse_args "$@"
  say "VOOL local uninstall"
  say "Install root:   ${INSTALL_ROOT}"
  say "Runtime home:   ${RUNTIME_HOME}"
  say "OpenClaw home:  ${OPENCLAW_HOME}"
  say "OpenClaw agent: ${OPENCLAW_AGENT_DIR}"
  say "Ollama models:  ${OLLAMA_MODELS_DIR}"
  say "llama.cpp data: ${VOOL_LLAMACPP_MODELS_DIR}"
  if [[ "${AUTO_YES}" -eq 0 ]]; then
    if ! prompt_yn "Remove this local VOOL install and runtime state?" "N"; then
      say "Cancelled."
      exit 0
    fi
  fi
  # The agent goes first so its KeepAlive cannot restart what the stop step ends.
  bootout_launch_agent
  stop_runtime_processes
  remove_targets
  say "Local VOOL install removed."
  report_kept_data
  if [[ "${REMOVE_DATA}" -eq 1 ]]; then
    remove_profile_data
  fi
}


main "$@"
