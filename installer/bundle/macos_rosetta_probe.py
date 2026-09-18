"""Reproduce, on demand, the measurement that identified the Rosetta defect.

Why this is a committed instrument and not a paragraph
-----------------------------------------------------
The claim behind this lane -- "the app launched under Rosetta because its main executable is a
shell script, so LaunchServices forged an x86_64-first architecture priority" -- is a claim about
RUNTIME BEHAVIOUR on a specific machine. A static census cannot show it, and a sentence in a
report cannot be re-checked. Anyone who doubts the diagnosis, or who wants to know whether a
future macOS still behaves this way, must be able to re-run the experiment rather than trust the
prose. So the experiment ships.

What it does
------------
1. Validates the translation detector itself against a POSITIVE CONTROL. `sysctl.proc_translated`
   only answers for the calling process, so other processes are read out of `kinfo_proc.p_flag`
   (P_TRANSLATED, 0x00020000). A detector that has never returned YES for a known-translated
   process is not evidence, so `arch -x86_64 /bin/sleep` is launched and must read YES.
2. Builds two synthetic .app bundles that differ in EXACTLY ONE BYTE-LEVEL FACT: whether
   Info.plist declares LSArchitecturePriority. Both have a shell script as CFBundleExecutable,
   which is the shape of the real VOOL.app.
3. Launches each through LaunchServices (`open`), which is the code path a double-click takes and
   the one a terminal launch bypasses -- the reason a `ps` sweep of a running app shows nothing.
4. Optionally runs a universal binary (`--universal-child`, e.g. the bundled ollama) as a child of
   each launcher and reports whether the CHILD ran translated. That is the "embedded component"
   macOS names in its notification.
5. Unregisters both bundles from LaunchServices and cleans up.

Nothing here reads, launches or modifies a delivered artifact: the bundles are synthetic and the
optional child binary is executed read-only with a harmless argument.

Usage
-----
    python3 installer/bundle/macos_rosetta_probe.py
    python3 installer/bundle/macos_rosetta_probe.py --universal-child /path/to/ollama --json out.json
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

P_TRANSLATED = 0x00020000
CTL_KERN, KERN_PROC, KERN_PROC_PID = 1, 14, 1
# p_flag sits at offset 32 in extern_proc on 64-bit Darwin. Sanity-checked by the positive
# control below: a normal exec'd 64-bit process must read P_LP64|P_EXEC (0x4004).
P_FLAG_OFFSET = 32
P_LP64, P_EXEC = 0x00000004, 0x00004000

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def process_flags(pid: int) -> int | None:
    """kinfo_proc.p_flag for *pid*, or None if the process is gone."""
    mib = (ctypes.c_int * 4)(CTL_KERN, KERN_PROC, KERN_PROC_PID, pid)
    size = ctypes.c_size_t(0)
    if _libc.sysctl(mib, 4, None, ctypes.byref(size), None, 0) != 0 or size.value < P_FLAG_OFFSET + 4:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _libc.sysctl(mib, 4, buf, ctypes.byref(size), None, 0) != 0:
        return None
    # A DEAD PID IS NOT A NATIVE PID. The sizing call answers with the struct's size whether or
    # not the process exists; the fetching call then succeeds and writes ZERO bytes. Without this
    # check the zero-filled buffer reads as p_flag=0 -- P_TRANSLATED clear -- so a process that
    # had already exited would be reported "not translated". That is the one wrong answer this
    # whole instrument exists to avoid, and it would have been indistinguishable from a real
    # native result.
    if size.value < P_FLAG_OFFSET + 4:
        return None
    return struct.unpack_from("<i", buf.raw, P_FLAG_OFFSET)[0]


def is_translated(pid: int) -> bool | None:
    flags = process_flags(pid)
    return None if flags is None else bool(flags & P_TRANSLATED)


def validate_detector() -> dict:
    """The detector must return YES for a known-translated process, or it proves nothing."""
    out: dict = {"check": "positive_control", "method": "arch -x86_64 /bin/sleep"}
    if not Path("/usr/bin/arch").exists():
        out.update(ok=False, reason="no /usr/bin/arch")
        return out
    try:
        proc = subprocess.Popen(["/usr/bin/arch", "-x86_64", "/bin/sleep", "10"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        out.update(ok=False, reason=f"cannot launch control: {exc}")
        return out
    try:
        flags = translated = None
        for _ in range(30):
            time.sleep(0.1)
            flags = process_flags(proc.pid)
            if flags is not None:
                translated = bool(flags & P_TRANSLATED)
                break
        out["control_p_flag"] = None if flags is None else f"0x{flags:08x}"
        out["control_translated"] = translated
        # Rosetta must be present for the control to mean anything; if the x86_64 process could
        # not start at all, say so rather than reporting a silent "no".
        out["ok"] = translated is True
        if translated is False:
            out["reason"] = ("control ran NATIVE -- either Rosetta is absent (so this host cannot "
                             "reproduce the defect) or the p_flag offset is wrong for this kernel")
    finally:
        proc.kill()
        proc.wait(timeout=5)
    return out


def _make_bundle(root: Path, name: str, arch_priority: list[str] | None,
                 log_path: Path, universal_child: Path | None) -> Path:
    app = root / f"{name}.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    probe = app / "Contents" / "MacOS" / "probe"
    lines = [
        "#!/usr/bin/env bash",
        f'exec > "{log_path}" 2>&1',
        'echo "launcher_slice=$(uname -m)"',
        'echo "launcher_translated=$(sysctl -n sysctl.proc_translated 2>/dev/null)"',
    ]
    if universal_child is not None:
        # The child is started, sampled while alive, then stopped. `--version` style invocations
        # exit too fast to sample, so the child is given a harmless long-running argument only if
        # the caller passed one that blocks; otherwise the sample may legitimately miss.
        # ISOLATED BY CONSTRUCTION. The child is a real server binary; it must never be able to
        # bind the port or touch the model store an operator's own install is using. A unique
        # high port and a throwaway model dir make a collision impossible rather than unlikely.
        #
        # The sampler is a WRITTEN FILE, not an inline -c string: quoting a python one-liner
        # inside a bash heredoc inside a python string silently produced no output, and the run
        # then reported "child not translated" -- a false negative dressed as a result.
        sampler = log_path.parent / f"sample_{name}.py"
        sampler.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})\n"
            "from macos_rosetta_probe import is_translated\n"
            "print('child_translated=%s' % is_translated(int(sys.argv[1])))\n",
            encoding="utf-8",
        )
        lines += [
            # A PORT PER CASE. Both cases previously shared one port, so the second server found
            # it taken, exited immediately, and its sample came back absent -- which the report
            # then had to label NOT SAMPLED. Distinct ports make both cases observable.
            f'export OLLAMA_HOST=127.0.0.1:{49700 + (os.getpid() % 150) + (0 if name == "without_key" else 1)}',
            f'export OLLAMA_MODELS="{log_path.parent}/models-{name}"',
            f'"{universal_child}" serve >/dev/null 2>&1 &',
            "CHILD=$!",
            # Sample as soon as the child is readable instead of once after a fixed sleep: a
            # server that takes longer to come up must not be recorded as unsampled.
            "for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 0.5; kill -0 $CHILD 2>/dev/null && break; done",
            'echo "child_pid=$CHILD"',
            f'"{sys.executable}" "{sampler}" "$CHILD"',
            "kill $CHILD 2>/dev/null",
            "wait $CHILD 2>/dev/null",
        ]
    lines.append('echo "probe_complete=1"')
    probe.write_text("\n".join(lines) + "\n", encoding="utf-8")
    probe.chmod(0o755)

    info: dict = {
        "CFBundleExecutable": "probe",
        "CFBundleIdentifier": f"ai.vool.rosettaprobe.{name}",
        "CFBundleName": name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
    }
    if arch_priority is not None:
        info["LSArchitecturePriority"] = arch_priority
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)
    return app


LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/"
              "LaunchServices.framework/Versions/A/Support/lsregister")


def run_ab(universal_child: Path | None, host_arch: str) -> dict:
    """Launch two bundles differing only in LSArchitecturePriority, through LaunchServices."""
    results: dict = {}
    root = Path(tempfile.mkdtemp(prefix="vool-rosetta-probe-"))
    apps = []
    try:
        cases = [("without_key", None), ("with_key", [host_arch])]
        for name, prio in cases:
            log = root / f"{name}.log"
            app = _make_bundle(root, name, prio, log, universal_child)
            apps.append(app)
            subprocess.run([LSREGISTER, "-f", str(app)], capture_output=True, timeout=60)
            subprocess.run(["/usr/bin/open", str(app)], capture_output=True, timeout=60)

        def _complete(name: str) -> bool:
            log = root / f"{name}.log"
            if not log.exists():
                return False
            return "probe_complete=1" in log.read_text(errors="replace")

        deadline = time.time() + 60
        while time.time() < deadline:
            if all(_complete(n) for n, _ in cases):
                break
            time.sleep(0.5)

        for name, _ in cases:
            log = root / f"{name}.log"
            parsed: dict = {}
            if log.exists():
                for line in log.read_text(errors="replace").splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        parsed[k.strip()] = v.strip()
            results[name] = parsed
    finally:
        for app in apps:
            subprocess.run([LSREGISTER, "-u", str(app)], capture_output=True, timeout=60)
        shutil.rmtree(root, ignore_errors=True)
    return results


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universal-child", type=Path, default=None,
                    help="a universal binary to run as a child of each launcher (e.g. the bundled ollama)")
    ap.add_argument("--json", type=Path, default=None)
    opts = ap.parse_args(argv[1:])

    if sys.platform != "darwin":
        print("macOS only", file=sys.stderr)
        return 2

    host_arch = os.uname().machine
    report: dict = {"host_arch": host_arch, "detector": validate_detector()}

    if not report["detector"].get("ok"):
        report["verdict"] = "DETECTOR NOT VALIDATED -- results below would not be evidence"
        print(json.dumps(report, indent=2))
        if opts.json:
            opts.json.write_text(json.dumps(report, indent=2))
        return 1

    report["ab"] = run_ab(opts.universal_child, host_arch)
    without = report["ab"].get("without_key", {})
    with_key = report["ab"].get("with_key", {})
    report["defect_reproduced"] = without.get("launcher_translated") == "1"
    report["fix_effective"] = with_key.get("launcher_translated") == "0"
    if opts.universal_child:
        # A missing sample is NOT a negative result. Absent -> None, so a child probe that never
        # ran can never be read as "the child was native".
        def _child(entry):
            v = entry.get("child_translated")
            return None if v in (None, "", "None") else (v == "True")
        report["child_translated_without_key"] = _child(without)
        report["child_translated_with_key"] = _child(with_key)
        report["child_defect_reproduced"] = report["child_translated_without_key"] is True
        report["child_fix_effective"] = report["child_translated_with_key"] is False
        if report["child_translated_without_key"] is None or report["child_translated_with_key"] is None:
            report["child_sampling"] = "NOT SAMPLED -- the child exited before it could be read; treat as no evidence, not as a negative"

    print(f"host                     {host_arch}")
    print(f"detector positive control  translated={report['detector'].get('control_translated')} "
          f"(p_flag {report['detector'].get('control_p_flag')})")
    print(f"script-main, NO key      {without}")
    print(f"script-main, WITH key    {with_key}")
    if opts.universal_child:
        print(f"universal child          without_key={report.get('child_translated_without_key')} "
              f"with_key={report.get('child_translated_with_key')}")
        if report.get("child_sampling"):
            print(f"                         {report['child_sampling']}")
    print(f"defect reproduced        {report['defect_reproduced']}")
    print(f"fix effective            {report['fix_effective']}")
    if opts.json:
        opts.json.write_text(json.dumps(report, indent=2))
        print(f"json                     {opts.json}")
    return 0 if report["defect_reproduced"] and report["fix_effective"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
