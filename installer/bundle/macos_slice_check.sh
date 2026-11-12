#!/usr/bin/env bash
# Independent Mach-O slice check for a built VOOL.app, using the platform tools (`file`, `lipo`).
#
#   bash installer/bundle/macos_slice_check.sh <VOOL.app> <arm64|x86_64>
#
# Every regular file inside the bundle is classified by `file`; every Mach-O is then read by
# `lipo -archs`. The check fails when any Mach-O lacks the target slice -- for an arm64 app that is
# an x86_64-only file, which Apple Silicon can run only under Rosetta. Universal files pass because
# they carry the target slice. It also fails, never passes, when a tool is missing, when `lipo`
# cannot read a file `file` called Mach-O, when the two tools disagree about a file being Mach-O, or
# when the bundle holds no Mach-O at all.
#
# This is a second opinion beside macos_arch_gate.py (a pure-Python Mach-O parser): the gate runs
# inside the build, this runs on the finished artifact with Apple's own tools. LIPO and FILE name
# the tools (default `lipo` and `file`); llvm-lipo answers `-archs` the same way.
set -uo pipefail

app="${1:-}"
target="${2:-}"
LIPO="${LIPO:-lipo}"
FILE="${FILE:-file}"

fail() { echo "slice check: $*" >&2; exit 1; }

[[ -n "${app}" && -n "${target}" ]] || fail "usage: macos_slice_check.sh <VOOL.app> <arm64|x86_64>"
[[ -d "${app}" ]] || fail "not a directory: ${app}"
case "${target}" in
  arm64) accepted=" arm64 arm64e " ;;
  x86_64) accepted=" x86_64 x86_64h " ;;
  *) fail "unsupported target architecture: ${target} (expected arm64 or x86_64)" ;;
esac
command -v "${LIPO}" >/dev/null 2>&1 || fail "${LIPO} is not available; the check cannot run"
command -v "${FILE}" >/dev/null 2>&1 || fail "${FILE} is not available; the check cannot run"

# The first four bytes, as eight lowercase hex digits. Mach-O and fat headers in either byte order.
magic_is_macho() {
  local m
  m="$(LC_ALL=C od -An -tx1 -N4 -- "$1" 2>/dev/null | tr -d ' \n')"
  case "${m}" in
    feedface|cefaedfe|feedfacf|cffaedfe|cafebabe|bebafeca|cafebabf|bfbafeca) return 0 ;;
    *) return 1 ;;
  esac
}

total=0; universal=0; bad=0; disagree=0
while IFS= read -r -d '' f; do
  kind="$("${FILE}" -b -- "${f}" 2>/dev/null || true)"
  by_file=0; by_magic=0
  [[ "${kind}" == *Mach-O* ]] && by_file=1
  magic_is_macho "${f}" && by_magic=1
  if [[ "${by_file}" -ne "${by_magic}" ]]; then
    # 0xcafebabe is also the Java class-file magic; `file` tells those apart and lipo cannot read
    # them, so a class file is the one disagreement that is not a Mach-O.
    if [[ "${by_magic}" -eq 1 && "${kind}" == *"compiled Java class"* ]]; then
      continue
    fi
    echo "  UNDECIDED ${f#"${app}"/}: file says '${kind}', header magic says Mach-O=${by_magic}"
    disagree=$((disagree + 1))
    continue
  fi
  [[ "${by_file}" -eq 1 ]] || continue
  total=$((total + 1))
  if ! archs="$("${LIPO}" -archs "${f}" 2>/dev/null)" || [[ -z "${archs// /}" ]]; then
    echo "  UNREADABLE ${f#"${app}"/}: ${LIPO} -archs failed (${kind})"
    bad=$((bad + 1))
    continue
  fi
  set -- ${archs}
  [[ $# -gt 1 ]] && universal=$((universal + 1))
  ok=0
  for a in "$@"; do
    [[ "${accepted}" == *" ${a} "* ]] && ok=1
  done
  if [[ "${ok}" -ne 1 ]]; then
    echo "  MISSING-${target} ${f#"${app}"/}: slices [${archs}]"
    bad=$((bad + 1))
  fi
done < <(find "${app}" -type f -print0)

echo "slice check: ${app}"
echo "  target ${target}; Mach-O files ${total} (universal ${universal}); without the ${target} slice or unreadable ${bad}; undecided ${disagree}"
[[ "${total}" -gt 0 ]] || fail "no Mach-O file found in ${app}; a VOOL.app always carries some, so the check refuses to pass"
[[ "${disagree}" -eq 0 ]] || fail "${disagree} file(s) where file and the header magic disagree"
[[ "${bad}" -eq 0 ]] || fail "${bad} Mach-O file(s) cannot run natively as ${target}"
echo "  PASS"
