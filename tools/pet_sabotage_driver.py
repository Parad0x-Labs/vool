"""Prove each desktop-pet fix is load-bearing: revert it, watch a test that names the cause fail.

A green suite proves nothing about whether a test would notice the defect coming back. For every
repair this lane made, this driver re-introduces exactly that defect, runs the test written to catch
it, and requires it to FAIL. Then it restores the file byte-for-byte and checks the SHA-256.

Guards against the ways a mutation run quietly lies:

* the mutation must actually change the file (the replacement count is asserted, so a stale anchor
  is a hard error rather than a silent no-op that reports "survived");
* the named test must FAIL, not ERROR -- a collection error would go red for the wrong reason and
  read as a pass;
* a control test runs under the same mutation and must stay GREEN, so a mutation that simply breaks
  the module for everything is not counted as a specific catch;
* restoration is verified by hash, and the file is restored from a copy taken before the mutation
  rather than from git, so an aborted run cannot leave the mutation behind.

Usage:  PYTHONPATH=$PWD python tools/pet_sabotage_driver.py [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS = "tests/test_desktop_pet_repair.py"
CONTROL = f"{TESTS}::test_the_payload_allowlist_still_drops_everything_it_does_not_name"

# (label, file, find, replace, the test that must go red, why it is the defect)
MUTATIONS = [
    (
        "RC-1 invalid transparent background colour",
        "installer/bundle/vool_window.py",
        '"background_color": "#000000",',
        '"background_color": "#00000000",',
        f"{TESTS}::test_production_window_flags_survive_the_real_pywebview_validator",
        "eight hex digits are rejected by pywebview before any window exists",
    ),
    (
        "RC-4 on_top puts the pet above modal panels",
        "installer/bundle/vool_window.py",
        '"on_top": False,',
        '"on_top": True,',
        f"{TESTS}::test_pet_never_sits_above_a_modal_or_security_panel",
        "on_top maps to NSStatusWindowLevel (25) > NSModalPanelWindowLevel (8)",
    ),
    (
        "RC-4 window level raised to the status level",
        "installer/bundle/pet_native.py",
        "PET_WINDOW_LEVEL = 3 ",
        "PET_WINDOW_LEVEL = 25 ",
        f"{TESTS}::test_pet_never_sits_above_a_modal_or_security_panel",
        "the pet would obstruct dialogs the operator has to answer",
    ),
    (
        "RC-2a ground slab back on the transparent surface",
        "core/companion_world_fragment.py",
        "{ground:false}",
        "{ground:true}",
        f"{TESTS}::test_desktop_surface_suppresses_the_full_width_ground_bar",
        "a 132pt opaque bar spanning the whole canvas on a transparent window",
    ),
    (
        "RC-2a in-app artwork loses its ground bar",
        "core/companion_world_fragment.py",
        "if(opts.ground!==false)vcwR(g,0,42,48,6,'#20232b');const hero=",
        "const hero=",
        f"{TESTS}::test_the_in_app_artwork_keeps_its_ground_bar",
        "the repair must remove a backdrop, not the docked character's scene",
    ),
    (
        "RC-2b permanent caption card",
        "core/companion_world_fragment.py",
        "white-space:nowrap;opacity:0;pointer-events:none;transition:opacity .16s ease}",
        "white-space:nowrap;opacity:1;pointer-events:auto;transition:opacity .16s ease}",
        f"{TESTS}::test_desktop_document_paints_no_permanent_status_card",
        "an always-on dark status rectangle under the pet",
    ),
    (
        "RC-7 Electron-only drag CSS reinstated",
        "core/companion_world_fragment.py",
        "align-items:center;justify-content:center;cursor:grab}",
        "align-items:center;justify-content:center;cursor:grab;-webkit-app-region:drag}",
        f"{TESTS}::test_desktop_document_drops_the_electron_only_drag_css",
        "WKWebView does not implement it, so it never controlled dragging",
    ),
    (
        "RC-5 transparent margins intercept clicks again",
        "installer/bundle/pet_native.py",
        "    return [\n        (x, y, canvas, canvas),\n        (width - inset - control, height - inset - control, control, control),\n    ]",
        "    return [(0.0, 0.0, float(width), float(height))]",
        f"{TESTS}::test_transparent_corners_do_not_intercept_clicks",
        "the whole 176x176 window becomes an invisible click blocker again",
    ),
    (
        "RC-6 no on-screen clamp",
        "installer/bundle/pet_native.py",
        "    if not visible_frames:\n        return frame\n    fx, fy, fw, fh = frame",
        "    if visible_frames or not visible_frames:\n        return frame\n    fx, fy, fw, fh = frame",
        f"{TESTS}::test_a_pet_flung_past_the_top_edge_is_pulled_back_not_pushed_further",
        "a pet flung past an edge is never recovered",
    ),
    (
        "import moved above the sys.path line the launcher depends on",
        "installer/bundle/vool_window.py",
        "from pathlib import Path\n\n_API_ORIGIN",
        "from pathlib import Path\n\nfrom installer.bundle import pet_native\n\n_API_ORIGIN",
        f"{TESTS}::test_the_window_module_imports_the_way_the_app_launcher_runs_it",
        "the .app runs this file as a script; an import above the sys.path line never resolves",
    ),
    (
        "adopt samples the Cocoa registry once instead of waiting",
        "installer/bundle/pet_native.py",
        "    ADOPT_TIMEOUT_SECONDS = 5.0",
        "    ADOPT_TIMEOUT_SECONDS = 0.0",
        f"{TESTS}::test_adopting_the_native_window_waits_for_cocoa_to_register_it",
        "the window is registered asynchronously; a single sample finds nothing",
    ),
    (
        "bridge accepts an unbounded window coordinate",
        "core/companion_world_fragment.py",
        "if all(v == v and abs(v) <= 60000 for v in (x, y)):",
        "if all(v == v or True for v in (x, y)):",
        f"{TESTS}::test_the_payload_refuses_absurd_and_non_numeric_positions",
        "a page-supplied coordinate becomes a real window placement",
    ),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pytest(target: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:randomly", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-1500:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO_ROOT / "evidence" / "pet" / "sabotage"))
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    scratch = Path(tempfile.mkdtemp(prefix="pet-sabotage-"))
    try:
        for label, rel, find, replace, test, why in MUTATIONS:
            path = REPO_ROOT / rel
            before_hash = _sha256(path)
            backup = scratch / (rel.replace("/", "__"))
            shutil.copy2(path, backup)

            source = path.read_text()
            occurrences = source.count(find)
            entry = {
                "mutation": label,
                "file": rel,
                "defect": why,
                "test": test,
                "anchor_occurrences": occurrences,
            }
            if occurrences != 1:
                # A stale anchor is a hard failure. Reporting "survived" here would be the exact
                # no-op that makes a mutation report worthless.
                entry["verdict"] = "ANCHOR NOT UNIQUE - mutation never applied"
                results.append(entry)
                continue
            try:
                path.write_text(source.replace(find, replace))
                assert _sha256(path) != before_hash, "mutation did not change the file"

                code, output = _pytest(test)
                control_code, _control_output = _pytest(CONTROL)
                entry["named_test_exit"] = code
                entry["control_exit"] = control_code
                errored = "error" in output.lower() and " failed" not in output.lower()
                entry["bit"] = code != 0 and not errored
                entry["control_stayed_green"] = control_code == 0
                entry["verdict"] = (
                    "CAUGHT" if entry["bit"] and entry["control_stayed_green"] else "NOT CAUGHT"
                )
                entry["tail"] = output.strip().splitlines()[-3:] if output.strip() else []
            finally:
                shutil.copy2(backup, path)
                entry["restored_byte_for_byte"] = _sha256(path) == before_hash
            results.append(entry)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    report = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "mutations": len(MUTATIONS),
        "caught": sum(1 for r in results if r.get("verdict") == "CAUGHT"),
        "all_restored": all(r.get("restored_byte_for_byte") for r in results),
        "results": results,
    }
    (out_dir / "sabotage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for row in results:
        print(f"{row['verdict']:>12}  {row['mutation']}")
    print(f"\ncaught {report['caught']}/{report['mutations']} · all files restored: {report['all_restored']}")
    return 0 if report["caught"] == report["mutations"] and report["all_restored"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
