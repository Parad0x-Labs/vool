"""Build-time architecture / deployment-target gate for VOOL.app.

Run at the end of a bundle build. It refuses an artifact that would launch through Rosetta,
that carries a dependency without the target slice, or that advertises a minimum macOS it
cannot actually reach.

The four checks, and the concrete defect each one exists to catch (all four were live in the
0.5.0 macOS bundles at 45a22cf9):

  launch-contract   The bundle's main executable is a shell script. LaunchServices has no
                    Mach-O header to read, so it FORGES ``LSArchitecturePriority`` with
                    x86_64 first and the app launches translated -- which is what makes
                    macOS report an "Intel-based component". Verified by A/B on this host:
                    a script-main bundle with no key launched proc_translated=1; adding
                    ``LSArchitecturePriority=[arm64]`` launched proc_translated=0.
  slice             Every Mach-O in the bundle must carry the target slice. An arm64 CPython
                    cannot load an x86_64-only extension.
  deployment        No Mach-O may require a newer macOS than LSMinimumSystemVersion claims.
                    ``vool-devauth`` was compiled with no -target and inherited the build
                    host's macOS 26 while the plist advertised 12.0.
  symlink-escape    A symlink pointing outside the bundle makes the artifact depend on the
                    build machine's filesystem.

Exit 0 = pass, 1 = fail. Stdlib only: this runs inside the bundle build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from macos_arch_census import census


def _vt(v: str) -> tuple[int, ...]:
    parts = []
    for chunk in str(v).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


# arm64e is arm64 code with pointer authentication; an arm64 target accepts it.
_COMPATIBLE = {"arm64": {"arm64", "arm64e"}, "x86_64": {"x86_64", "x86_64h"}}


def check(app: Path, target_arch: str, min_os: str) -> tuple[list[str], dict]:
    """Return (failures, report). Empty failures == gate passes."""
    data = census(app)
    lc = data["launch_contract"]
    fails: list[str] = []
    accepted = _COMPATIBLE.get(target_arch, {target_arch})

    # 1. launch contract -- the Rosetta root cause
    prio = lc.get("lsarchitecture_priority")
    if not lc.get("main_executable_is_macho"):
        if not prio:
            fails.append(
                f"launch-contract: {lc.get('info_plist')}: CFBundleExecutable "
                f"'{lc.get('cfbundle_executable')}' is not a Mach-O binary and Info.plist declares no "
                f"LSArchitecturePriority. LaunchServices will forge (x86_64, arm64) and run this app "
                f"under Rosetta. Declare LSArchitecturePriority=[{target_arch}]."
            )
        elif list(prio) != [target_arch]:
            fails.append(
                f"launch-contract: {lc.get('info_plist')}: LSArchitecturePriority is {list(prio)}, "
                f"expected exactly ['{target_arch}'] for a {target_arch} artifact."
            )

    # 2. every Mach-O carries the target slice
    for m in data["macho"]:
        archs = {s["arch"] for s in m["slices"]}
        if not (archs & accepted):
            fails.append(
                f"slice: {m['path']}: has {sorted(archs)}, missing a {target_arch} slice."
            )

    # 3. deployment target vs the advertised floor
    declared = lc.get("lsminimum_system_version") or min_os
    if _vt(declared) != _vt(min_os):
        fails.append(
            f"deployment: {lc.get('info_plist')}: LSMinimumSystemVersion is {declared}, "
            f"expected {min_os} for this build."
        )
    floor = _vt(min_os)
    for m in data["macho"]:
        # A binary the product never loads must not set the advertised floor. Without this a
        # test-only extension built against a newer SDK fails the gate for a product that would
        # never import it. Unreachable binaries are still censused, just not floor-setting.
        if not m.get("runtime_reachable", True):
            continue
        for s in m["slices"]:
            if s["arch"] not in accepted or not s["minos"]:
                continue
            if _vt(s["minos"]) > floor:
                fails.append(
                    f"deployment: {m['path']} ({s['arch']}): requires macOS {s['minos']}, "
                    f"above the advertised minimum {min_os}."
                )

    # 3b. dependency closure -- a load path that resolves to nothing inside the bundle is a
    # dependency on the build machine, and it fails at the user's first launch, not here.
    for m in data["macho"]:
        for s in m["slices"]:
            for dep in s.get("unresolved", []):
                fails.append(
                    f"closure: {m['path']} ({s['arch']}): links {dep}, which resolves to no file "
                    f"inside the bundle."
                )

    # 4. symlinks escaping the bundle
    for s in data["symlinks_escaping_bundle"]:
        fails.append(f"symlink-escape: {s['path']} -> {s['target']} leaves the bundle.")

    report = {
        "app": str(app),
        "target_arch": target_arch,
        "min_os": min_os,
        "macho_count": data["macho_count"],
        "launch_contract": lc,
        "failures": fails,
        "passed": not fails,
    }
    return fails, report


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: macos_arch_gate.py <VOOL.app> [--arch arm64] [--min-os 12.0] [--json out]", file=sys.stderr)
        return 2
    app = Path(argv[1]).resolve()
    # NO SILENT DEFAULTS. A defaulted floor is how this gate first passed the wrong number: the
    # build called it with --min-macos while this parser read --min-os, so the check ran against
    # a default of 12.0 and reported the bundle's honest 14.0 as the mismatch. Both spellings are
    # accepted and a missing value is an error, never an assumption.
    arch = argv[argv.index("--arch") + 1] if "--arch" in argv else "arm64"
    min_os = None
    for flag in ("--min-os", "--min-macos"):
        if flag in argv:
            min_os = argv[argv.index(flag) + 1]
            break
    if min_os is None:
        print("macos_arch_gate: --min-os is required (no default floor is assumed)", file=sys.stderr)
        return 2

    fails, report = check(app, arch, min_os)
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(report, indent=2))

    print(f"architecture gate: {app}")
    print(f"  target {arch}, minimum macOS {min_os}, {report['macho_count']} Mach-O files")
    if fails:
        print(f"  FAIL ({len(fails)}):")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("  PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
