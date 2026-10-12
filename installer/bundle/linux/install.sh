#!/usr/bin/env bash
set -euo pipefail
# Stable integration hook: the Linux updater calls this after its own download
# verification and extraction. This installer owns no update/feed policy.
vool_install_linux_bundle() {
  local package_root="$1"
  "${package_root}/python/bin/python3" -B -I "${package_root}/install_bundle.py" install --package "${package_root}"
}
vool_install_linux_bundle "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
