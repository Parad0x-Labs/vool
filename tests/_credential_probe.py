"""C15 fake credential/browser binaries — PATH-injected spies that record argv.

Every fake writes one JSON line per invocation (argv, pid, ppid, ancestry) to the
log named by ``C15_FAKE_BIN_LOG`` in the child's environment, then behaves:

* ``security``      — the macOS credential CLI. Records, then FAILS (exit 4,
  "unexpected invocation") unless ``fails=False``. A product path that quietly
  depends on it can therefore never pass unnoticed: either the recorded argv
  shows up (tests assert zero credential calls) or the call errors loudly.
* ``keyring``       — the keyring CLI entry point. Records, then fails like
  ``security``.
* Chrome-family names (``fake-browser``, ``Google Chrome``, ``Chromium``,
  ``chrome-headless-shell``, ``Google Chrome for Testing``) — record argv (so
  tests can assert the isolated ``--user-data-dir`` and which binary was
  selected) and print a tiny DOM, driving ``chrome_render`` end to end with no
  real browser.

Each recorded line also embeds the calling process's ancestry (as observed by
the fake via ``ps``), so every synthetic attempt carries its provenance.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

FAKE_SECURITY = "security"
FAKE_KEYRING = "keyring"
FAKE_BROWSER = "fake-browser"

DOM = "<html><head><title>c15-fake</title></head><body>ok</body></html>"

_RECORDER = """#!{python}
import json, os, subprocess, sys

LOG = os.environ.get("C15_FAKE_BIN_LOG", "")
NAME = {name!r}
FAILS = {fails!r}


def _ancestry():
    chain = []
    ppid = os.getppid()
    for _ in range(8):
        try:
            out = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(ppid)],
                                 capture_output=True, text=True, timeout=2)
            argv = (out.stdout or "").strip()
        except Exception:
            argv = ""
        chain.append({{"pid": ppid, "argv": argv}})
        if ppid <= 1:
            break
        try:
            out = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(ppid)],
                                 capture_output=True, text=True, timeout=2)
            ppid = int((out.stdout or "1").strip() or 1)
        except Exception:
            break
    return chain


if LOG:
    record = {{
        "binary": NAME,
        "argv": sys.argv,
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "ancestry": _ancestry(),
    }}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\\n")

if FAILS:
    sys.stderr.write("c15-fake: unexpected %s invocation; unattended paths must never call it\\n" % NAME)
    sys.exit(4)

sys.stdout.write({stdout!r})
sys.exit(0)
"""


def fake_bin_dir(
    root: Path,
    *,
    python: str,
    log: Path,
    failing: tuple[str, ...] = (FAKE_SECURITY, FAKE_KEYRING),
    extra_names: tuple[str, ...] = (
        FAKE_BROWSER,
        "Google Chrome",
        "Chromium",
        "chrome-headless-shell",
        "Google Chrome for Testing",
    ),
) -> Path:
    """Create the fake-binary directory. Put it FIRST on PATH in the child env."""
    bindir = root / "fake-bin"
    bindir.mkdir(parents=True, exist_ok=True)

    def _write(name: str, *, fails: bool, stdout: str) -> None:
        script = _RECORDER.format(python=python, name=name, fails=fails, stdout=stdout)
        target = bindir / name
        target.write_text(script, encoding="utf-8")
        target.chmod(0o755)

    _write(FAKE_SECURITY, fails=FAKE_SECURITY in failing, stdout="")
    _write(FAKE_KEYRING, fails=FAKE_KEYRING in failing, stdout="")
    for name in extra_names:
        _write(name, fails=False, stdout=DOM)
    return bindir


def read_log(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    out = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def credential_calls(log: Path) -> list[dict[str, Any]]:
    """Every recorded invocation of a credential CLI (must be EMPTY in unattended runs)."""
    return [e for e in read_log(log) if e.get("binary") in {FAKE_SECURITY, FAKE_KEYRING}]


def browser_calls(log: Path) -> list[dict[str, Any]]:
    return [e for e in read_log(log) if e.get("binary") not in {FAKE_SECURITY, FAKE_KEYRING}]


def env_for(bin_dir: Path, log: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '/usr/bin:/bin')}"
    env["C15_FAKE_BIN_LOG"] = str(log)
    return env


def run_driver(
    python: str,
    driver_source: str,
    driver_path: Path,
    env: dict[str, str],
    *,
    timeout: float = 240,
) -> subprocess.CompletedProcess[str]:
    driver_path.parent.mkdir(parents=True, exist_ok=True)
    driver_path.write_text(driver_source, encoding="utf-8")
    return subprocess.run(
        [python, str(driver_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
