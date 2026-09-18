#!/usr/bin/env bash
set -euo pipefail

OWNER="${VOOL_GITHUB_OWNER:-Parad0x-Labs}"
REPO="${VOOL_GITHUB_REPO:-vool-local}"
REF="${VOOL_GITHUB_REF:-main}"
# NULLA -> VOOL compatibility: reuse a pre-rename install directory instead of creating a second one.
__vool_pick_install_dir() {
  if [ -n "${VOOL_INSTALL_DIR:-}" ]; then
    printf '%s' "$VOOL_INSTALL_DIR"
  elif [ -d "$HOME/nulla-local" ] && [ ! -d "$HOME/vool-local" ]; then
    printf '%s' "$HOME/nulla-local"
  else
    printf '%s' "$HOME/vool-local"
  fi
}
INSTALL_DIR="$(__vool_pick_install_dir)"
ARCHIVE_URL="${VOOL_ARCHIVE_URL:-https://github.com/${OWNER}/${REPO}/archive/refs/heads/${REF}.tar.gz}"
ARCHIVE_SHA256="${VOOL_ARCHIVE_SHA256:-}"
SOURCE_COMMIT="${VOOL_BUILD_COMMIT:-}"
SOURCE_DIRTY_STATE="${VOOL_BUILD_DIRTY_STATE:-}"
TMP_DIR=""
AUTO_START=1
INSTALL_PROFILE="${VOOL_INSTALL_PROFILE:-}"
BUILD_COMMIT=""


say() {
  printf '%s\n' "$*"
}


cleanup() {
  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}


usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --ref <git-ref>          Git branch or tag to fetch (default: ${REF})
  --dir <install-dir>      Target install folder (default: ${INSTALL_DIR})
  --archive-url <url>      Override the source archive URL
  --sha256 <hex>           Verify the downloaded archive against a SHA-256 digest
  --source-commit <sha>    Override the source commit recorded in build metadata
  --source-dirty <bool>    Override the source dirty-state recorded in build metadata
  --install-profile <id>   auto-recommended | local-only (alias: ollama-only) | local-max (alias: ollama-max)
  --no-start               Install but do not launch VOOL
  --help, -h               Show this help

Environment overrides:
  VOOL_GITHUB_OWNER
  VOOL_GITHUB_REPO
  VOOL_GITHUB_REF
  VOOL_INSTALL_DIR
  VOOL_ARCHIVE_URL
  VOOL_ARCHIVE_SHA256
  VOOL_BUILD_COMMIT
  VOOL_BUILD_DIRTY_STATE
  VOOL_INSTALL_PROFILE
EOF
}


json_escape() {
  local value="${1//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  printf '%s' "${value}"
}


json_bool_or_null() {
  local normalized
  normalized="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  case "${normalized}" in
    1|true|yes|on)
      printf 'true'
      ;;
    0|false|no|off)
      printf 'false'
      ;;
    *)
      printf 'null'
      ;;
  esac
}


parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --ref requires a value."; exit 2; }
        REF="$1"
        ARCHIVE_URL="https://github.com/${OWNER}/${REPO}/archive/refs/heads/${REF}.tar.gz"
        ;;
      --dir)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --dir requires a value."; exit 2; }
        INSTALL_DIR="$1"
        ;;
      --archive-url)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --archive-url requires a value."; exit 2; }
        ARCHIVE_URL="$1"
        ;;
      --sha256)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --sha256 requires a value."; exit 2; }
        ARCHIVE_SHA256="$1"
        ;;
      --source-commit)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --source-commit requires a value."; exit 2; }
        SOURCE_COMMIT="$1"
        ;;
      --source-dirty)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --source-dirty requires a value."; exit 2; }
        SOURCE_DIRTY_STATE="$1"
        ;;
      --install-profile)
        shift
        [[ $# -gt 0 ]] || { say "ERROR: --install-profile requires a value."; exit 2; }
        INSTALL_PROFILE="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
        ;;
      --no-start)
        AUTO_START=0
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


compute_sha256() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
    return 0
  fi
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{print $1}'
    return 0
  fi
  if command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 -r "${path}" | awk '{print $1}'
    return 0
  fi
  say "ERROR: Cannot verify archive checksum because sha256sum, shasum, and openssl are unavailable."
  exit 1
}


verify_archive_checksum() {
  local archive_path="$1"
  local expected
  expected="$(printf '%s' "${ARCHIVE_SHA256}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  if [[ -z "${expected}" ]]; then
    say "WARNING: Downloaded archive is not checksum-verified. Set --sha256 or VOOL_ARCHIVE_SHA256 to verify it."
    return 0
  fi
  local actual
  actual="$(compute_sha256 "${archive_path}")"
  if [[ "${actual}" != "${expected}" ]]; then
    say "ERROR: Archive checksum mismatch."
    say "Expected: ${expected}"
    say "Actual:   ${actual}"
    exit 1
  fi
  say "Archive checksum verified."
}


require_command() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    say "ERROR: Required command not found: ${cmd}"
    exit 1
  fi
}


prepare_install_dir() {
  mkdir -p "${INSTALL_DIR}"
  if [[ -f "${INSTALL_DIR}/Install_And_Run_VOOL.sh" || -f "${INSTALL_DIR}/installer/install_vool.sh" || -f "${INSTALL_DIR}/install_vool.sh" ]]; then
    say "Existing VOOL install detected at ${INSTALL_DIR}"
    return
  fi

  if find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 | read -r _; then
    say "ERROR: ${INSTALL_DIR} exists and is not an existing VOOL install."
    say "Choose an empty folder with --dir or remove the existing contents."
    exit 1
  fi
}


download_and_extract() {
  TMP_DIR="$(mktemp -d)"
  trap cleanup EXIT

  local archive_path="${TMP_DIR}/vool.tar.gz"
  say "Downloading VOOL from ${ARCHIVE_URL}"
  curl -fsSL "${ARCHIVE_URL}" -o "${archive_path}"
  verify_archive_checksum "${archive_path}"

  say "Extracting to ${INSTALL_DIR}"
  local -a tar_args=(-xzf "${archive_path}" -C "${INSTALL_DIR}")
  if archive_has_common_root "${archive_path}"; then
    tar_args+=(--strip-components=1)
  fi
  tar "${tar_args[@]}"
}


archive_has_common_root() {
  local archive_path="$1"
  local common_root=""
  local entry=""
  while IFS= read -r entry; do
    entry="${entry#./}"
    [[ -n "${entry}" ]] || continue
    [[ "${entry}" != "/" ]] || continue
    local first_component="${entry%%/*}"
    if [[ "${entry}" == "${first_component}" ]]; then
      return 1
    fi
    if [[ -z "${common_root}" ]]; then
      common_root="${first_component}"
      continue
    fi
    if [[ "${first_component}" != "${common_root}" ]]; then
      return 1
    fi
  done < <(tar -tzf "${archive_path}")
  [[ -n "${common_root}" ]]
}


resolve_archive_commit() {
  if [[ -n "${SOURCE_COMMIT}" ]]; then
    BUILD_COMMIT="${SOURCE_COMMIT}"
    return 0
  fi
  case "${ARCHIVE_URL}" in
    "https://github.com/${OWNER}/${REPO}/archive/refs/"*|"https://codeload.github.com/${OWNER}/${REPO}/tar.gz/"*)
      ;;
    *)
      return 0
      ;;
  esac
  local commit_payload
  commit_payload="$(curl -fsSL "https://api.github.com/repos/${OWNER}/${REPO}/commits/${REF}" 2>/dev/null || true)"
  BUILD_COMMIT="$(printf '%s' "${commit_payload}" | sed -n 's/^[[:space:]]*"sha":[[:space:]]*"\([0-9a-f]\{40\}\)".*/\1/p' | head -n 1)"
}


write_build_metadata() {
  local metadata_path="${INSTALL_DIR}/config/build-source.json"
  mkdir -p "$(dirname "${metadata_path}")"
  cat > "${metadata_path}" <<EOF
{
  "ref": "$(json_escape "${REF}")",
  "branch": "$(json_escape "${REF}")",
  "commit": "$(json_escape "${BUILD_COMMIT}")",
  "dirty_state": $(json_bool_or_null "${SOURCE_DIRTY_STATE}"),
  "source_kind": "archive",
  "source_url": "$(json_escape "${ARCHIVE_URL}")"
}
EOF
}


launch_installer() {
  local launcher="${INSTALL_DIR}/Install_And_Run_VOOL.sh"
  local guided="${INSTALL_DIR}/Install_VOOL.sh"
  local canonical="${INSTALL_DIR}/installer/install_vool.sh"
  local -a profile_args=()
  if [[ ! -f "${canonical}" ]]; then
    canonical="${INSTALL_DIR}/install_vool.sh"
  fi
  if [[ -n "${INSTALL_PROFILE}" ]]; then
    profile_args=(--install-profile "${INSTALL_PROFILE}")
  fi

  chmod +x "${launcher}" "${guided}" "${canonical}" 2>/dev/null || true

  exec_with_profile_args() {
    local target="$1"
    shift || true
    if [[ ${#profile_args[@]} -gt 0 ]]; then
      exec "${target}" "$@" "${profile_args[@]}"
    fi
    exec "${target}" "$@"
  }

  say "Running VOOL installer..."
  if [[ "${AUTO_START}" -eq 1 ]]; then
    if [[ -f "${launcher}" ]]; then
      exec_with_profile_args "${launcher}"
    fi
    if [[ -f "${canonical}" ]]; then
      exec_with_profile_args "${canonical}" --yes --start --openclaw default
    fi
  fi
  if [[ -f "${guided}" ]]; then
    exec_with_profile_args "${guided}" --yes --openclaw default
  fi
  if [[ -f "${canonical}" ]]; then
    exec_with_profile_args "${canonical}" --yes --openclaw default
  fi
  say "ERROR: Bootstrap download succeeded, but no usable installer entrypoint was found."
  exit 1
}


main() {
  parse_args "$@"
  require_command curl
  require_command tar
  require_command bash
  prepare_install_dir
  download_and_extract
  resolve_archive_commit
  write_build_metadata
  launch_installer
}


main "$@"
