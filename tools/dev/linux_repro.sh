#!/usr/bin/env bash
# D3: local Linux repro for the code-task sandbox failure, in minutes, without a
# 2-hour CI run. One command pulls ubuntu:24.04, installs python3.12 + bubblewrap
# + the repo's dev deps, mounts THIS worktree read-only at /src (the host tree is
# never written by the container), and runs the real probe plus one targeted
# code-task test file inside.
#
# STATUS: EXPERIMENTAL -- never executed locally on this Mac. The Docker CLI is
# installed but the daemon (colima) is stopped, another worker's VM
# (null-rh-sandbox) is running, and free disk was 3.7GiB when this was written;
# starting the default VM profile under those conditions risks a full disk that
# would kill active work. The REAL Linux evidence is the dispatched workflow:
# .github/workflows/debug-linux.yml (see tools/dev/LINUX_SANDBOX_FINDINGS.md).
#
# What this repro CAN and CANNOT reproduce, honestly:
#   * GitHub's ubuntu runners fail bwrap because Ubuntu 24.04's AppArmor sets
#     kernel.apparmor_restrict_unprivileged_userns=1 (measured, CI run
#     35537608791: "bwrap: setting up uid map: Permission denied").
#   * A plain Docker container instead applies Docker's own seccomp profile to
#     the userns syscalls -- a DIFFERENT mechanism that usually produces the
#     same class of failure string. If the probe unexpectedly passes inside the
#     container, the kernel now allows unprivileged userns there; use
#     --privileged as the positive control and read the probe's own output.
#
# Usage:
#   tools/dev/linux_repro.sh              # reproduce + run the targeted test
#   tools/dev/linux_repro.sh --privileged # positive control: bwrap should work
#   tools/dev/linux_repro.sh --clean      # remove THIS script's image+containers only
set -euo pipefail

IMAGE="vool-repro:ubuntu-24.04"
LABEL="vool-repro"
WORKTREE="$(cd "$(dirname "$0")/../.." && pwd)"
TEST_TARGET="tests/test_code_task_repair_units.py"

die() { echo "linux_repro: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker CLI not found"
docker version >/dev/null 2>&1 || die "docker daemon not reachable (start it, or rely on the debug-linux workflow)"
[ -f "$WORKTREE/sandbox/job_runner.py" ] || die "no repo at $WORKTREE (run from a checkout)"

if [ "${1:-}" = "--clean" ]; then
  echo "-- removing containers labeled $LABEL (this script's only)"
  # shellcheck disable=SC2046
  docker rm -f $(docker ps -aq --filter "label=$LABEL") 2>/dev/null || true
  echo "-- removing image $IMAGE"
  docker rmi -f "$IMAGE" 2>/dev/null || true
  echo "clean done"
  exit 0
fi

PRIVILEGED=""
if [ "${1:-}" = "--privileged" ]; then
  PRIVILEGED="--privileged"
  shift
fi
[ $# -eq 0 ] || die "unknown argument(s): $* (expected --clean or --privileged)"

FREE_KB=$(df -k / | awk 'NR==2 {print $4}')
if [ "$FREE_KB" -lt 5242880 ]; then
  die "less than 5GiB free on / -- the container layers plus pip deps would risk filling the disk (Rule 12)"
fi

echo "== building $IMAGE (ubuntu:24.04 + python3.12 + bubblewrap) =="
docker build --tag "$IMAGE" - <<'DOCKERFILE'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv bubblewrap ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
DOCKERFILE

echo "== running probe + targeted test against $WORKTREE (mounted read-only at /src) =="
# The worktree is mounted READ-ONLY: the container physically cannot write the
# host tree. pip install -e needs a writable source tree, so the repo is copied
# to /work inside the container first (same bytes, disposable).
docker run --rm --label "$LABEL" $PRIVILEGED \
  --mount type=bind,src="$WORKTREE",dst=/src,readonly \
  --env TEST_TARGET="$TEST_TARGET" \
  "$IMAGE" bash -euxc '
    cp -a /src /work
    python3.12 -m venv /opt/venv
    /opt/venv/bin/pip install --upgrade pip
    /opt/venv/bin/pip install -e "/work[dev,companion]"
    echo "===== PROBE ====="
    /opt/venv/bin/python /work/tools/dev/probe_sandbox_linux.py || echo "PROBE EXITED NONZERO (expected on a restricted host)"
    echo "===== TARGETED TEST ====="
    cd /work && /opt/venv/bin/python -m pytest "$TEST_TARGET" --tb=long -p no:cacheprovider -q || echo "PYTEST EXITED NONZERO"
  '

echo "== done. cleanup with: tools/dev/linux_repro.sh --clean =="
