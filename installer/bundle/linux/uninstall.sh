#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"${ROOT}/python/bin/python3" -B -I "${ROOT}/install_bundle.py" uninstall
