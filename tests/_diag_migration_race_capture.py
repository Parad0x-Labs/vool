"""TEMPORARY DIAGNOSTIC CAPTURE HARNESS (instrumentation only, no production edit).

Purpose: capture the exact failing operation, SQLite error code/name, and full
traceback for the writer-side "OperationalError: database schema has changed"
observed on Linux CI (runs 35600921603 / 35614200187 / 35615031134) on
tests/test_run_migrations_cross_process.py::test_concurrent_first_use_migrations_lose_no_committed_row_and_poison_no_process.

Relationship to the original test: the writer below is the ORIGINAL `_WRITER` from
that test with ONLY diagnostic additions — phase labels around its existing
operations and enriched exception capture. The operations are unchanged and in the
same order: storage.db.get_connection() (the real production open path, not a
reconstruction), CREATE TABLE IF NOT EXISTS, INSERT, commit, close; errors are
recorded and the loop continues exactly as before; `committed` increments only
after a fully successful close. No extra queries were added to the hot path, no
exceptions are suppressed, nothing is retried, and the concurrency pattern (8
independent migrator processes + 1 writer behind a file barrier, 4.0 s window,
4 rounds) is identical to the original test.

Modes:
  (default)   run the 4-round reproduction; verdict pass/fail under the original
              test's assertions (migrator success, writer errors == 0, committed
              row equality through an independent connection, integrity ok, no
              snapshot residue).
  --smoke     one tiny real-write round (2 migrators, 1.0 s window) and proof that
              rows are durable through an independent connection after the writer
              exits.
  --self-test deliberately inject ONE exception at a named phase; validates that
              the capture records the correct phase, sqlite error code/name, and
              full traceback, and that the verdict for it is FAIL. This validates
              the instrumentation only — not the product defect.

Run: venv-python tests/_diag_migration_race_capture.py [--smoke | --self-test]
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Identical to the original test's _MIGRATOR.
_MIGRATOR = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from storage.migrations import run_migrations
go = Path(sys.argv[3])
Path(sys.argv[3] + ".ready-" + sys.argv[4]).write_text("ready")
while not go.exists():
    time.sleep(0.001)
try:
    run_migrations(sys.argv[2])
    print(json.dumps({"migrated": True}))
except Exception as exc:
    print(json.dumps({"migrated": False, "error": type(exc).__name__, "detail": str(exc)[:200]}))
"""

# The original test's _WRITER with only phase labels and enriched exception capture.
_WRITER_CAPTURE = """
import json, sys, time, traceback
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sqlite3
from storage.db import get_connection
go = Path(sys.argv[3])
Path(sys.argv[3] + ".ready-writer").write_text("ready")
while not go.exists():
    time.sleep(0.001)

VERSIONS = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version}
IDENTITY = sys.argv[5]
FAULT_PHASE = sys.argv[6] if len(sys.argv) > 6 else ""
faulted = False

class _InjectedDiagnostic(sqlite3.OperationalError):
    pass

def _diag(exc, phase):
    return {
        "phase": phase,
        "exc_type": type(exc).__name__,
        "exc_str": str(exc)[:200],
        "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None),
        "sqlite_errorname": getattr(exc, "sqlite_errorname", None),
        "traceback": traceback.format_exc(),
        "versions": VERSIONS,
        "identity": IDENTITY,
    }

committed, errors, error_details = 0, [], []
deadline = time.monotonic() + float(sys.argv[4])
while time.monotonic() < deadline:
    conn = None
    try:
        phase = "get_connection"
        conn = get_connection(sys.argv[2])
        phase = "create_table"
        conn.execute("CREATE TABLE IF NOT EXISTS committed_probe (v INTEGER NOT NULL)")
        phase = "insert"
        if FAULT_PHASE and FAULT_PHASE == phase and not faulted:
            faulted = True
            injected = _InjectedDiagnostic("database schema has changed (injected diagnostic)")
            injected.sqlite_errorcode = 17
            injected.sqlite_errorname = "SQLITE_SCHEMA"
            raise injected
        conn.execute("INSERT INTO committed_probe (v) VALUES (?)", (committed,))
        phase = "commit"
        conn.commit()
        phase = "close"
        conn.close()
        conn = None
        committed += 1
    except Exception as exc:
        errors.append(type(exc).__name__ + ": " + str(exc)[:80])
        if len(error_details) < 3:
            error_details.append(_diag(exc, phase))
        if conn is not None:
            try:
                conn.close()
            except Exception as close_exc:
                errors.append(type(close_exc).__name__ + ": " + str(close_exc)[:80])
        conn = None
    time.sleep(0.003)
print(json.dumps({"committed": committed, "errors": len(errors),
                  "error_kinds": sorted(set(errors))[:5], "error_details": error_details,
                  "versions": VERSIONS, "identity": IDENTITY}))
"""


def _run_writer_round(round_dir: Path, db: Path, barrier: Path, *, migrators: int, write_seconds: float, index: int, fault_phase: str = "", identity: str = "") -> dict:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(round_dir / "vool-home"),
        "VOOL_HOME": str(round_dir / "vool-home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-B", "-c", _MIGRATOR, str(REPO_ROOT), str(db), str(barrier), str(n)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        for n in range(migrators)
    ]
    writer = subprocess.Popen(
        [sys.executable, "-B", "-c", _WRITER_CAPTURE, str(REPO_ROOT), str(db), str(barrier),
         str(write_seconds), identity, fault_phase],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    # Readiness uses the barrier's own parent (the children write go.ready-* next to
    # it). Readiness is mandatory: a child that never arms fails the round instead of
    # releasing late after a timeout.
    deadline = time.monotonic() + 60.0
    armed = False
    while time.monotonic() < deadline:
        if len(list(barrier.parent.glob("go.ready-*"))) >= migrators + 1:
            armed = True
            break
        time.sleep(0.01)
    if not armed:
        for process in [*processes, writer]:
            process.kill()
        for process in [*processes, writer]:
            process.communicate()
        return {"migrators": [], "migrators_ok": False, "writer": {"no_result": True, "reason": "children never armed"},
                "writer_ok": False, "arming_failed": True}
    barrier.write_text("go")

    def reap(process, label):
        try:
            out, err = process.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
            return {"returncode": process.returncode, "timed_out": True, "stderr": err[-400:]}, False
        line = (out.strip().splitlines() or ["{}"])[-1]
        try:
            result = json.loads(line)
        except ValueError:
            result = {"no_result": True, "stderr": err[-400:]}
        result["returncode"] = process.returncode
        return result, process.returncode == 0

    migrated, ok_all = [], True
    for n, process in enumerate(processes):
        result, ok = reap(process, f"migrator-{n}")
        ok_all = ok_all and ok and result.get("migrated") is True
        migrated.append(result)
    written, writer_ok = reap(writer, "writer")
    return {"migrators": migrated, "migrators_ok": ok_all, "writer": written, "writer_ok": writer_ok}


def _independent_verify(db: Path) -> dict:
    """Independent connection AFTER every child exited: rows durable + integrity."""
    sys.path.insert(0, str(REPO_ROOT))
    from storage.db import get_connection

    conn = get_connection(db)
    try:
        table_present = (
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='committed_probe'").fetchone()
            is not None
        )
        present = int(conn.execute("SELECT COUNT(*) FROM committed_probe").fetchone()[0]) if table_present else 0
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()
    return {"present": present, "committed_table_present": table_present, "integrity": integrity}


def _round(root: Path, index: int, *, migrators: int = 8, write_seconds: float = 4.0, fault_phase: str = "", identity: str = "") -> dict:
    home = root / f"round-{index}"
    home.mkdir(parents=True)
    db = home / "store.db"
    barrier = home / "go"
    outcome = _run_writer_round(home, db, barrier, migrators=migrators, write_seconds=write_seconds, index=index, fault_phase=fault_phase, identity=identity)
    outcome.update(_independent_verify(db))
    outcome["snapshot_residue"] = sorted(path.name for path in home.glob("*pre-migration*"))
    return outcome


def _verdict(outcome: dict) -> list[str]:
    problems = []
    if not outcome["migrators_ok"]:
        problems.append("a migrator failed")
    if not outcome["writer_ok"]:
        problems.append("writer process failed")
    if outcome["writer"].get("errors") != 0:
        problems.append(f"writer recorded {outcome['writer'].get('errors')} errors")
    if outcome["writer"].get("committed", 0) <= 0:
        problems.append("writer committed nothing")
    if outcome["present"] != outcome["writer"].get("committed"):
        problems.append("committed rows were rewound")
    if outcome["integrity"] != "ok":
        problems.append("integrity not ok")
    if outcome["snapshot_residue"]:
        problems.append("snapshot residue present")
    return problems


def _source_identity() -> str:
    import subprocess

    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else f"git-unavailable({out.stderr[:80]})"


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "real"
    versions = {"python": sys.version.split()[0], "sqlite": __import__("sqlite3").sqlite_version}
    identity = _source_identity()
    with tempfile.TemporaryDirectory(prefix="migration-race-capture-") as tmp:
        root = Path(tmp)
        if mode == "--smoke":
            outcome = _round(root, 0, migrators=2, write_seconds=1.0, identity=identity)
            problems = _verdict(outcome)
            print(json.dumps({"mode": "smoke", "identity": identity, "verdict": "fail" if problems else "pass", "problems": problems,
                              "committed": outcome["writer"].get("committed"), "present": outcome["present"],
                              "integrity": outcome["integrity"]}, indent=2))
            return 1 if problems else 0
        if mode == "--self-test":
            outcome = _round(root, 0, migrators=2, write_seconds=1.5, fault_phase="insert", identity=identity)
            details = outcome["writer"].get("error_details") or []
            captured = details[0] if details else {}
            checks = {
                "phase_is_insert": captured.get("phase") == "insert",
                "code_17": captured.get("sqlite_errorcode") == 17,
                "name_SQLITE_SCHEMA": captured.get("sqlite_errorname") == "SQLITE_SCHEMA",
                "traceback_names_injection": "injected diagnostic" in (captured.get("traceback") or ""),
                "traceback_identifies_writer_line": 'File "<string>", line' in (captured.get("traceback") or ""),
                "versions_present": bool(captured.get("versions", {}).get("sqlite")),
                "identity_present": bool(captured.get("identity")),
                "verdict_failing": bool(_verdict(outcome)),
            }
            print(json.dumps({"mode": "self-test", "all_checks_pass": all(checks.values()), "checks": checks,
                              "capture": captured}, indent=2)[:4000])
            return 0 if all(checks.values()) else 1
        # real mode: the original test's exact 4-round shape
        aggregate, failed_rounds = [], []
        for index in range(4):
            outcome = _round(root, index, identity=identity)
            problems = _verdict(outcome)
            entry = {"round": index, "verdict": "fail" if problems else "pass", "problems": problems,
                     "committed": outcome["writer"].get("committed"), "errors": outcome["writer"].get("errors"),
                     "present": outcome["present"], "integrity": outcome["integrity"],
                     "migrators_ok": outcome["migrators_ok"]}
            if outcome["writer"].get("error_details"):
                entry["error_details"] = outcome["writer"]["error_details"]
            aggregate.append(entry)
            print(json.dumps(entry)[:600], flush=True)
            if problems:
                failed_rounds.append(index)
        final = {"mode": "real", "identity": identity, "rounds": aggregate,
                 "reproduced": bool(failed_rounds), "failed_rounds": failed_rounds,
                 "versions": versions}
        print(json.dumps(final)[:2000], flush=True)
        return 0  # a clean (non-reproducing) local run is a valid outcome, not a harness failure


if __name__ == "__main__":
    raise SystemExit(main())
