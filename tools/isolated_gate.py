#!/usr/bin/env python3
"""Run a recorded pytest selection under a synthetic home, and fingerprint every failure.

Three things this exists to make true, none of which the earlier shell gate did.

**Nothing consults or mutates operator state.** Every run gets one freshly created
directory used as ``HOME``, ``VOOL_HOME`` and ``TMPDIR``, removed afterwards, with the
removal recorded rather than assumed.

**Zero Keychain access, proven rather than asserted.** Two independent nets. A shim
directory is prepended to ``PATH`` carrying a ``security`` executable that logs the call
and exits non-zero, so any process shelling out to the macOS security tool is recorded and
fails loudly instead of prompting. And a ``sitecustomize`` on ``PYTHONPATH`` installs a
``sys.addaudithook`` that records every process spawn whose argv mentions security,
keychain or keyring, plus any import of ``keyring``. Both nets write to one log; a run is
only clean if that log is empty.

**Attribution by failure, not by name.** Same node id failing at base and at tip is not
the same failure — a test can fail for two different reasons. Results are written as
``{node id -> fingerprint}`` where the fingerprint is the exception type plus a normalised
first line of the message, taken from JUnit XML rather than scraped from console text.
``--attribute`` then compares two runs and reports three sets: INHERITED (same node, same
fingerprint), UNRESOLVED (same node, DIFFERENT fingerprint — a different defect wearing the
same name) and REGRESSION (fails at tip, passes or absent at base).

    python -m tools.isolated_gate --worktree <path> --selection <file> --label tip
    python -m tools.isolated_gate --attribute tip.json base.json --out attribution.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

PYTEST_BASE = ["-q", "-p", "no:randomly", "--no-header"]

#: Written into the shim dir and put first on PATH.
_SECURITY_SHIM = """#!/bin/sh
# Installed by tools/isolated_gate: records the call and refuses. A test run that needs
# the macOS security tool is a test run that would have prompted the operator.
printf '{"tool":"security","argv":"%s"}\\n' "$*" >> "$VOOL_KEYCHAIN_AUDIT_LOG"
exit 1
"""

_SITECUSTOMIZE = '''"""Installed by tools/isolated_gate. Records anything that smells of the Keychain."""
import os
import sys

_LOG = os.environ.get("VOOL_KEYCHAIN_AUDIT_LOG", "")
#: Substrings that mean an actual credential store is being touched. Deliberately NOT
#: the bare word "keychain": a test that greps its own source for "--use-mock-keychain"
#: is asking a question about text, not opening a keychain, and flagging it would make
#: the clean signal meaningless.
_PATH_MARKERS = ("/library/keychains", "login.keychain", "keychain-db")


def _record(kind, detail):
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write('{"kind": "%s", "detail": %r}\\n' % (kind, str(detail)[:400]))
    except Exception:
        pass


def _argv_of(args):
    for item in args:
        if isinstance(item, (list, tuple)) and item:
            return [str(x) for x in item]
    return [str(x) for x in args if isinstance(x, (str, bytes))]


def _hook(event, args):
    try:
        if event in ("subprocess.Popen", "os.exec", "os.posix_spawn", "os.system"):
            argv = _argv_of(args)
            if not argv:
                return
            exe = os.path.basename(argv[0]).lower()
            joined = " ".join(argv).lower()
            hit = exe in {"security", "codesign"} or any(m in joined for m in _PATH_MARKERS)
            if hit:
                _record(event, argv)
        elif event == "import" and args and str(args[0]).split(".")[0] == "keyring":
            _record("import", args[0])
    except Exception:
        pass


if _LOG:
    sys.addaudithook(_hook)
'''


#: Chromium-family executables to wrap, best first. A bundled build is preferred over
#: the operator's installed Chrome: it has no Keychain integration and no operator
#: profile to reach for in the first place.
_WRAPPABLE_GLOBS = (
    "chromium_headless_shell-*/chrome-mac*/headless_shell",
    "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
)

_CHROME_WRAPPER = """#!/bin/sh
# Installed by tools/isolated_gate. TEST INFRASTRUCTURE ONLY -- it does not modify any
# worktree, and in particular does not modify the historical base, which predates the
# production fix and would otherwise launch a browser with no credential isolation.
exec {binary} \\
  --password-store=basic \\
  --use-mock-keychain \\
  --no-first-run \\
  --no-default-browser-check \\
  "$@"
"""


def _find_wrappable_browser() -> str | None:
    import glob as _glob

    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not cache or cache in {"0", "1"}:
        cache = os.path.expanduser(
            "~/Library/Caches/ms-playwright" if sys.platform == "darwin" else "~/.cache/ms-playwright"
        )
    for pattern in _WRAPPABLE_GLOBS:
        for hit in sorted(_glob.glob(os.path.join(cache, pattern)), reverse=True):
            if os.path.isfile(hit) and os.access(hit, os.X_OK):
                return hit
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def write_chrome_wrapper(shim_dir: pathlib.Path) -> tuple[str, str] | tuple[None, None]:
    """A Chrome that cannot reach the operator's Keychain, whatever the code under test does.

    The production fix (every automated launch carries the isolation flags) lives in the
    branch. The historical BASE predates it, so attributing a failure by running base code
    directly re-introduces exactly the defect the branch repairs -- measured, as repeated
    macOS Keychain dialogs. The base must not be edited to attribute against it, so the
    protection is injected from outside instead: a wrapper the base resolves through
    VOOL_BROWSER_BINARY, which prepends the flags before exec'ing the real binary.
    """
    binary = _find_wrappable_browser()
    if binary is None:
        return None, None
    shim_dir.mkdir(parents=True, exist_ok=True)
    wrapper = shim_dir / "chrome-safe"
    wrapper.write_text(_CHROME_WRAPPER.format(binary=json.dumps(binary)), encoding="utf-8")
    wrapper.chmod(0o755)
    return str(wrapper), binary


def _write_shims(shim_dir: pathlib.Path, audit_log: pathlib.Path) -> None:
    shim_dir.mkdir(parents=True, exist_ok=True)
    security = shim_dir / "security"
    security.write_text(_SECURITY_SHIM, encoding="utf-8")
    security.chmod(0o755)
    (shim_dir / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    audit_log.write_text("", encoding="utf-8")


_TYPE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure|Warning))\b")


def _fingerprint(kind: str, message: str) -> str:
    """Exception type plus a normalised first line: stable, but still discriminating.

    Numbers, hex ids, temp paths and addresses are masked, because a fingerprint that
    changes with a tmpdir name would call every rerun a different defect.
    """
    first = (message or "").strip().splitlines()
    head = first[0] if first else ""
    etype = (kind or "").strip() or (_TYPE_RE.match(head).group(1) if _TYPE_RE.match(head) else "")
    norm = head
    norm = re.sub(r"/(?:private/)?(?:tmp|var)/[^\s'\"]+", "<TMP>", norm)
    norm = re.sub(r"0x[0-9a-f]+", "<ADDR>", norm)
    # Only long digit runs are masked -- timestamps, pids, ports, ids. Small integers are
    # the DISCRIMINATING part of an assertion message ("1 != 2" is not "5 != 9"), and
    # masking them collapsed two different defects onto one fingerprint.
    norm = re.sub(r"\b\d{5,}\b", "<ID>", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return f"{etype}|{norm[:200]}"


def isolation_env(*, root: str, home: str, shim: str, audit_log: str, base_env: dict | None = None) -> dict:
    """The one definition of a safe test environment, shared by every runner here.

    Kept in one place because the C24 runner needs exactly the same guarantees the gate
    needs -- a synthetic home, a shimmed `security`, an audit hook and a wrapped browser --
    and it originally had none of them. Two runners with two ideas of "isolated" is how a
    release artifact ends up reporting isolated_home:false.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env.update(
        {
            "HOME": home,
            "VOOL_HOME": home,
            "TMPDIR": home,
            "XDG_CONFIG_HOME": home,
            "XDG_DATA_HOME": home,
            "XDG_CACHE_HOME": home,
            "PATH": f"{shim}{os.pathsep}{env.get('PATH', '')}",
            "PYTHONPATH": f"{shim}{os.pathsep}{root}",
            "VOOL_KEYCHAIN_AUDIT_LOG": audit_log,
        }
    )
    # Browser BINARIES are not operator state, and hiding them behind the synthetic home
    # is coverage loss, not isolation: it turned 21 chromium tests into "no chromium build
    # available" skips before this. The cache is pointed at explicitly and read-only; the
    # credential concern is handled by the wrapper and the audit hook, not by starving the
    # runner of a browser to launch.
    cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not cache or cache in {"0", "1"}:
        cache = os.path.expanduser(
            "~/Library/Caches/ms-playwright" if sys.platform == "darwin" else "~/.cache/ms-playwright"
        )
    if os.path.isdir(cache):
        env["PLAYWRIGHT_BROWSERS_PATH"] = cache
    return env


def _parse_junit(path: pathlib.Path) -> dict:
    root = ET.parse(path).getroot()
    cases = root.iter("testcase")
    failures: dict[str, str] = {}
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for case in cases:
        totals["tests"] += 1
        file_attr = case.get("file") or ""
        name = case.get("name") or ""
        classname = case.get("classname") or ""
        cls = classname.split(".")[-1] if "." in classname else ""
        nodeid = f"{file_attr}::{cls}::{name}" if cls and cls != pathlib.Path(file_attr).stem else f"{file_attr}::{name}"
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
            if bad is not None:
                totals["errors"] += 1
        else:
            totals["failures"] += 1
        if case.find("skipped") is not None:
            totals["skipped"] += 1
        if bad is not None:
            failures[nodeid] = _fingerprint(bad.get("type") or "", bad.get("message") or "")
    return {"totals": totals, "failures": failures}


def run(*, worktree: str, selection: str, label: str, out_dir: str, timeout: int) -> dict:
    root = pathlib.Path(worktree).resolve()
    paths = [
        line.strip()
        for line in pathlib.Path(selection).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    present = [p for p in paths if (root / p.split("::")[0]).exists()]
    absent = [p for p in paths if p not in present]

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    home = pathlib.Path(tempfile.mkdtemp(prefix=f"vool-gate-{label}-"))
    shim = home / ".shims"
    audit = out / f"KEYCHAIN_AUDIT_{label}.log"
    junit = out / f"JUNIT_{label}.xml"
    console = out / f"GATE_{label.upper()}.out"
    _write_shims(shim, audit)

    real_home = pathlib.Path(os.path.expanduser("~"))
    keychain_dir = real_home / "Library" / "Keychains"
    before = keychain_dir.stat().st_mtime if keychain_dir.exists() else None

    wrapper, wrapped = write_chrome_wrapper(shim)

    # Built by the shared helper, not by a second copy here. This function used to carry
    # its own duplicate of the environment, so a correction made in `isolation_env`
    # (pointing PLAYWRIGHT_BROWSERS_PATH at the real browser cache) reached the C24 runner
    # and silently did not reach the gate -- which is the exact failure the helper's own
    # docstring warns about. Measured cost: 26 composer tests skipped as "no chromium build
    # available" in a gate artifact while the same file passed 26/26 when run with the
    # variable set by hand.
    env = isolation_env(root=str(root), home=str(home), shim=str(shim), audit_log=str(audit))
    if wrapper and not env.get("VOOL_BROWSER_BINARY"):
        # Applies to BOTH sides of an attribution. The tip carries the production fix; the
        # base does not, and running base code unwrapped is what raised real Keychain
        # dialogs. The wrapper is injected, never written into the worktree under test.
        env["VOOL_BROWSER_BINARY"] = wrapper

    started = time.monotonic()
    with console.open("w", encoding="utf-8") as fh:
        fh.write(f"# isolated gate: label={label}\n")
        fh.write(f"# worktree={root}\n")
        fh.write(f"# synthetic HOME/VOOL_HOME/TMPDIR={home}\n")
        fh.write(f"# selection={selection} ({len(present)} present, {len(absent)} absent)\n")
        for p in absent:
            fh.write(f"#   absent: {p}\n")
        fh.flush()
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *PYTEST_BASE, f"--junitxml={junit}", *present],
            cwd=str(root), env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout,
        )
        fh.write(f"EXIT={proc.returncode}\n")
    elapsed = round(time.monotonic() - started, 1)

    parsed = _parse_junit(junit) if junit.exists() else {"totals": {}, "failures": {}}
    audit_lines = [ln for ln in audit.read_text(encoding="utf-8").splitlines() if ln.strip()]
    after = keychain_dir.stat().st_mtime if keychain_dir.exists() else None

    shutil.rmtree(home, ignore_errors=True)
    report = {
        "label": label,
        "worktree": str(root),
        "sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                              capture_output=True, text=True).stdout.strip(),
        "tree_clean": not subprocess.run(["git", "status", "--porcelain"], cwd=str(root),
                                         capture_output=True, text=True).stdout.strip(),
        "selection": selection,
        "selected_present": len(present),
        "selected_absent": absent,
        "synthetic_home": str(home),
        "synthetic_home_removed": not home.exists(),
        "seconds": elapsed,
        "exit_code": proc.returncode,
        "totals": parsed["totals"],
        "failures": parsed["failures"],
        "keychain_audit_entries": audit_lines,
        "keychain_clean": not audit_lines,
        "chrome_wrapper": wrapper,
        "chrome_wrapped_binary": wrapped,
        # AMBIENT, not causal. ~/Library/Keychains is shared with everything the operator
        # is running, and a gate takes ~50 minutes of wall clock, so its mtime moving says
        # nothing about this run. Recorded because it is cheap, named so it cannot be read
        # as a violation. The CAUSAL evidence is `keychain_clean`: the PATH-shimmed
        # `security` tool and the audit hook, which observe this process tree only.
        "operator_keychain_mtime_before": before,
        "operator_keychain_mtime_after": after,
        "operator_keychain_mtime_moved_in_window": before != after,
        "keychain_access_by_this_run": bool(audit_lines),
    }
    (out / f"GATE_{label.upper()}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def attribute(tip_path: str, base_path: str) -> dict:
    tip = json.loads(pathlib.Path(tip_path).read_text(encoding="utf-8"))
    base = json.loads(pathlib.Path(base_path).read_text(encoding="utf-8"))
    tf, bf = tip["failures"], base["failures"]
    inherited, unresolved, regressions = {}, {}, {}
    for node, fp in sorted(tf.items()):
        if node not in bf:
            regressions[node] = {"tip": fp, "base": None}
        elif bf[node] == fp:
            inherited[node] = fp
        else:
            # Same name, different defect. Not inherited, and not silently counted as one.
            unresolved[node] = {"tip": fp, "base": bf[node]}
    return {
        "tip_sha": tip["sha"],
        "base_sha": base["sha"],
        "tip_failures": len(tf),
        "base_failures": len(bf),
        "inherited": inherited,
        "unresolved_different_mechanism": unresolved,
        "regressions": regressions,
        "closed_by_branch": sorted(set(bf) - set(tf)),
        "counts": {
            "inherited": len(inherited),
            "unresolved_different_mechanism": len(unresolved),
            "regressions": len(regressions),
            "closed_by_branch": len(set(bf) - set(tf)),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worktree")
    ap.add_argument("--selection")
    ap.add_argument("--label")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--timeout", type=int, default=14400)
    ap.add_argument("--attribute", nargs=2, metavar=("TIP_JSON", "BASE_JSON"))
    ap.add_argument(
        "--make-wrapper",
        default="",
        metavar="DIR",
        help="write the safe-Chrome wrapper into DIR and print its path, for ad-hoc runs "
        "that need the same protection the gate injects",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    if args.make_wrapper:
        wrapper, wrapped = write_chrome_wrapper(pathlib.Path(args.make_wrapper))
        if not wrapper:
            print("no chromium-family binary found to wrap", file=sys.stderr)
            return 1
        print(json.dumps({"wrapper": wrapper, "wraps": wrapped}))
        return 0

    if args.attribute:
        report = attribute(*args.attribute)
        text = json.dumps(report, indent=2) + "\n"
        if args.out:
            pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(json.dumps(report["counts"], indent=2))
        return 0

    report = run(worktree=args.worktree, selection=args.selection, label=args.label,
                 out_dir=args.out_dir, timeout=args.timeout)
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
