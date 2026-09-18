#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_HOME="${VOOL_HOME:-$HOME/.vool_runtime}"
INSTALL_ROOT="${VOOL_INSTALL_ROOT:-$HOME/vool-local}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw-default}"
OPENCLAW_AGENT_DIR="${VOOL_OPENCLAW_AGENT_DIR:-$HOME/.openclaw/agents/main/agent/vool}"
LAUNCH_AGENT_PATH="${VOOL_LAUNCH_AGENT_PATH:-$HOME/Library/LaunchAgents/ai.vool.runtime.plist}"
OLLAMA_MODELS_DIR="${OLLAMA_MODELS:-$HOME/.ollama/models}"
VOOL_LLAMACPP_MODELS_DIR="${VOOL_LLAMACPP_MODELS_DIR:-$HOME/.vool_runtime/models/llamacpp}"
AUTO_YES=0


say() {
  printf '%s\n' "$*"
}


usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --yes, -y                 Remove the local VOOL install without prompting
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


stop_runtime_processes() {
  pkill -f "apps.vool_api_server" >/dev/null 2>&1 || true
  pkill -f "llama_cpp.server" >/dev/null 2>&1 || true
  pkill -f "openclaw gateway run" >/dev/null 2>&1 || true
  pkill -f "ollama launch openclaw" >/dev/null 2>&1 || true
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
    "${RUNTIME_HOME}"
    "${OPENCLAW_HOME}"
    "${OPENCLAW_AGENT_DIR}"
    "${LAUNCH_AGENT_PATH}"
    "${OLLAMA_MODELS_DIR}"
    "${VOOL_LLAMACPP_MODELS_DIR}"
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
  stop_runtime_processes
  bootout_launch_agent
  remove_targets
  say "Local VOOL install removed."
}


main "$@"
