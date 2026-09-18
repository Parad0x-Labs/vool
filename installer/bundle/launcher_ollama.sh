#!/bin/bash
# Sourced by the macOS launcher (and testable on its own). ONE resolution of the local Ollama endpoint, in the
# order the runtime uses (core/ollama_endpoint.py): VOOL_RAW_OLLAMA_API_URL, then OLLAMA_HOST, then
# VOOL_OLLAMA_URL, then the default. A launch that points the endpoint elsewhere -- an isolated test profile at a
# dead port, a remote box -- neither probes nor starts a local server. Found 2026-09-07: the launcher probed the
# literal default before the runtime even booted, so every "isolated" launch still touched the operator's Ollama.

vool_ollama_base() {
  local base="${VOOL_RAW_OLLAMA_API_URL:-${OLLAMA_HOST:-${VOOL_OLLAMA_URL:-}}}"
  base="${base:-http://127.0.0.1:11434}"
  case "${base}" in
    http://*|https://*) ;;
    *) base="http://${base}" ;;
  esac
  printf '%s' "${base%/}"
}

vool_ollama_is_default_local() {
  case "$(vool_ollama_base)" in
    http://127.0.0.1:11434|http://localhost:11434) return 0 ;;
    *) return 1 ;;
  esac
}

# $1 = bundled ollama binary, $2 = its log file. Starts the bundled server ONLY for the default local endpoint,
# and only when nothing answers there. Any other endpoint is left alone: no probe, no server.
vool_ensure_bundled_ollama() {
  local bin="$1" log="$2" base
  base="$(vool_ollama_base)"
  if ! vool_ollama_is_default_local; then
    echo "ollama endpoint is ${base}; not probing it and not starting a local server"
    return 0
  fi
  if curl -fsS --max-time 2 "${base}/api/tags" >/dev/null 2>&1; then
    return 0
  fi
  if [[ -x "${bin}" ]]; then
    echo "starting bundled ollama"
    nohup "${bin}" serve >>"${log}" 2>&1 &
    for _ in $(seq 1 30); do
      curl -fsS --max-time 2 "${base}/api/tags" >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  return 0
}
