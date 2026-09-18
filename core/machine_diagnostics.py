"""Read-only host diagnostics for VOOL's assistant tools.

Three capabilities a local personal assistant needs and VOOL lacked: per-drive disk space, recent
Windows Event Log errors, and the heaviest running processes. Everything here is READ-ONLY, bounded
(row/size/time caps), windowless (CREATE_NO_WINDOW so no console pops in the taskbar), free of shell
injection (argument lists, never shell=True), and best-effort (returns partial/empty data rather than
raising). Subprocess and filesystem calls are injectable so the logic is unit-testable without a
specific host.
"""
from __future__ import annotations

import os
import shutil
import string
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

_GiB = 1024 ** 3
_MiB = 1024 ** 2
# How long a CPU ranking samples for. Long enough that the percentages mean something, short
# enough that the answer still lands inside a fast-path turn.
_CPU_SAMPLE_SECONDS = 0.3
# Suppress the console window subprocess would otherwise pop (Windows only; 0 is a no-op elsewhere).
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_SUBPROCESS_TIMEOUT = 12

Runner = Callable[..., Any]


def _run(cmd: list[str], runner: Runner) -> Any:
    return runner(
        cmd,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        creationflags=_NO_WINDOW,
    )


def _enumerate_drives() -> list[str]:
    """Local drive roots to report. Windows: existing letter roots; POSIX: '/' plus /home if distinct."""
    if sys.platform == "win32":
        return [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]
    roots = ["/"]
    if os.path.isdir("/home") and os.path.ismount("/home"):
        roots.append("/home")
    return roots


def disk_usage(*, drives: list[str] | None = None, usage_fn: Callable[[str], Any] = shutil.disk_usage) -> list[dict[str, Any]]:
    """Per-drive total/free/used in GiB plus percent used. Never raises; skips unreadable drives."""
    targets = drives if drives is not None else _enumerate_drives()
    rows: list[dict[str, Any]] = []
    for drive in targets:
        try:
            usage = usage_fn(drive)
            total, used, free = int(usage.total), int(usage.used), int(usage.free)
        except Exception:
            continue
        rows.append(
            {
                "mount": drive,
                "total_gb": round(total / _GiB, 1),
                "used_gb": round(used / _GiB, 1),
                "free_gb": round(free / _GiB, 1),
                "percent_used": round(100.0 * used / total, 1) if total else 0.0,
            }
        )
    return rows


def display_info(*, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Connected-display resolution + refresh rate from Windows CIM. Physical screen size is
    reported only when EDID provides it (else marked unverified). Read-only; never raises; a
    display question must be answered from THIS, not from CPU/GPU specs.
    """
    if sys.platform != "win32":
        return {"supported": False, "platform": sys.platform, "displays": [], "physical": {"verified": False}}

    displays: list[dict[str, Any]] = []
    try:
        proc = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | Where-Object { $_.CurrentHorizontalResolution } | "
                "ForEach-Object { '{0}|{1}|{2}|{3}' -f "
                "$_.Name,$_.CurrentHorizontalResolution,$_.CurrentVerticalResolution,$_.CurrentRefreshRate }",
            ],
            runner,
        )
        for line in str(getattr(proc, "stdout", "") or "").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
                continue
            displays.append(
                {
                    "name": parts[0] or "Display",
                    "width": int(parts[1]),
                    "height": int(parts[2]),
                    "refresh_hz": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None,
                }
            )
    except Exception:
        pass

    physical: dict[str, Any] = {"diagonal_in": None, "verified": False}
    try:
        proc = _run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorBasicDisplayParams | "
                "ForEach-Object { '{0}|{1}' -f $_.MaxHorizontalImageSize,$_.MaxVerticalImageSize }",
            ],
            runner,
        )
        for line in str(getattr(proc, "stdout", "") or "").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                w_cm, h_cm = int(parts[0]), int(parts[1])
                if w_cm and h_cm:
                    diag_cm = (w_cm ** 2 + h_cm ** 2) ** 0.5
                    physical = {"diagonal_in": round(diag_cm / 2.54, 1), "width_cm": w_cm, "height_cm": h_cm, "verified": True}
                    break
    except Exception:
        pass

    return {"supported": True, "platform": "win32", "displays": displays, "physical": physical}


# Top-level names that are OS/app-managed and must never be casually recommended for deletion.
_SYSTEM_MANAGED = frozenset({
    "windows", "windows.old", "winsxs", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "config.msi", "perflogs", "msocache",
    "pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootmgr", "$windows.~bt", "$windows.~ws",
})
_APP_CACHE_MANAGED = frozenset({
    "appdata", "node_modules", ".cache", ".gradle", ".nuget", ".m2", "temp", "tmp",
    "__pycache__", "packages", "cache",
})
_USER_DATA = frozenset({
    "users", "documents", "downloads", "desktop", "pictures", "videos", "music", "onedrive",
    "dropbox", "google drive",
})


def classify_delete_safety(name: str) -> str:
    """Coarse delete-safety class for a top-level entry name. Never advises deleting
    system/OS-managed data; user data is flagged for review."""
    lowered = str(name or "").strip().lower()
    if lowered in _SYSTEM_MANAGED:
        return "system-managed (do not delete)"
    if lowered in _APP_CACHE_MANAGED:
        return "app-managed / cache (safe to clear via the app, not by hand)"
    if lowered in _USER_DATA:
        return "your files (review before deleting)"
    return "review before deleting"


def largest_entries(
    root: str,
    *,
    top: int = 8,
    time_budget_s: float = 20.0,
    scandir_fn: Callable[[str], Any] = os.scandir,
    walk_fn: Callable[[str], Any] = os.walk,
    getsize_fn: Callable[[str], int] = os.path.getsize,
    monotonic_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Measured top-level files+folders under ``root`` by size, largest first. Read-only,
    bounded by a wall-clock budget; a folder whose walk did not finish is returned with the
    size measured SO FAR and ``complete=False`` (honest partial, never a fabricated total).
    Each entry carries a delete-safety class. Never raises.
    """
    import time as _time

    now = monotonic_fn or _time.monotonic
    deadline = now() + max(2.0, float(time_budget_s))
    entries: list[dict[str, Any]] = []
    try:
        children = list(scandir_fn(root))
    except OSError:
        return {"root": root, "entries": [], "error": "unreadable", "complete": False}

    for child in children:
        if now() > deadline:
            break
        try:
            is_dir = child.is_dir(follow_symlinks=False)
        except OSError:
            continue
        name = child.name
        size = 0
        complete = True
        if is_dir:
            try:
                for dirpath, dirnames, filenames in walk_fn(child.path):
                    if now() > deadline:
                        complete = False
                        break
                    # Do not descend into reparse points / junctions (avoid loops + double counting).
                    dirnames[:] = [d for d in dirnames if not _is_reparse_point(os.path.join(dirpath, d))]
                    for filename in filenames:
                        try:
                            size += int(getsize_fn(os.path.join(dirpath, filename)))
                        except OSError:
                            continue
            except OSError:
                complete = False
        else:
            try:
                size = int(getsize_fn(child.path))
            except OSError:
                continue
        entries.append({
            "name": name,
            "path": child.path,
            "size_bytes": size,
            "size_gb": round(size / _GiB, 2),
            "kind": "folder" if is_dir else "file",
            "complete": complete,
            "delete_safety": classify_delete_safety(name),
        })

    entries.sort(key=lambda item: item["size_bytes"], reverse=True)
    all_complete = bool(entries) and all(item["complete"] for item in entries) and now() <= deadline
    return {"root": root, "entries": entries[: max(1, int(top))], "complete": all_complete, "scanned": len(entries)}


def largest_files(
    root: str,
    *,
    top: int = 8,
    time_budget_s: float = 20.0,
    walk_fn: Callable[[str], Any] = os.walk,
    getsize_fn: Callable[[str], int] = os.path.getsize,
    monotonic_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """The largest individual FILES under ``root`` (recursive), largest first. This differs
    from :func:`largest_entries`, which ranks top-level folders by aggregate size -- "the
    biggest file on D:" wants a single deeply-nested file, not a folder listing. Read-only,
    bounded by a wall-clock budget; ``complete=False`` when the budget cut the walk short
    (the ranking is then a lower bound, never fabricated). Never raises.

    ``top`` was hard-capped at 25 regardless of what a caller asked for. A caller that filters
    the ranked list afterward (`core.runtime_execution_tools._machine_find_largest` excludes
    results living in a project other than the one a chat is bound to) could see every one of
    those 25 slots consumed by entries it was always going to filter out, under-returning even
    when plenty of legitimate results existed further down the real ranking. The walk below
    covers the same ground and is bounded by `time_budget_s` either way -- `top` only sizes the
    result heap, a few hundred bytes of bookkeeping, not how much filesystem gets read -- so
    raising the ceiling costs nothing the walk wasn't already going to spend. 500 is a fixed,
    bounded ceiling, not an unbounded one.
    """
    import heapq
    import time as _time

    now = monotonic_fn or _time.monotonic
    deadline = now() + max(2.0, float(time_budget_s))
    capped = max(1, min(int(top), 500))
    heap: list[tuple[int, str]] = []  # min-heap of (size_bytes, path), holds the top-N seen
    complete = True
    try:
        for dirpath, dirnames, filenames in walk_fn(root):
            if now() > deadline:
                complete = False
                break
            # Do not descend into reparse points / junctions (avoid loops + double counting).
            dirnames[:] = [d for d in dirnames if not _is_reparse_point(os.path.join(dirpath, d))]
            for filename in filenames:
                fpath = os.path.join(dirpath, filename)
                try:
                    size = int(getsize_fn(fpath))
                except OSError:
                    continue
                if len(heap) < capped:
                    heapq.heappush(heap, (size, fpath))
                elif size > heap[0][0]:
                    heapq.heapreplace(heap, (size, fpath))
    except OSError:
        complete = False
    if not heap:
        # Empty because the tree genuinely has no files (complete) vs the budget cut the walk
        # before any file was read (partial) -- do not label a budget cut as "unreadable".
        return {"root": root, "entries": [], "error": "unreadable" if complete else None, "complete": complete, "scanned": 0}
    ranked = sorted(heap, key=lambda item: item[0], reverse=True)
    entries = [
        {
            "name": os.path.basename(path) or path,
            "path": path,
            "size_bytes": size,
            "size_gb": round(size / _GiB, 2),
            "kind": "file",
            "complete": True,
            "delete_safety": classify_delete_safety(os.path.basename(path) or path),
        }
        for size, path in ranked
    ]
    return {"root": root, "entries": entries, "complete": complete, "scanned": len(entries)}


def _is_reparse_point(path: str) -> bool:
    try:
        return bool(os.path.islink(path)) or bool(os.lstat(path).st_reparse_tag)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False


def event_log_errors(
    *,
    limit: int = 25,
    logs: tuple[str, ...] = ("System", "Application"),
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """Recent Critical/Error entries from the Windows Event Log (read-only ``wevtutil qe``).

    Non-Windows returns ``supported=False`` rather than guessing. Bounded to ``limit`` newest entries
    per log; output text is size-capped so a huge log can't flood context.
    """
    if sys.platform != "win32":
        return {"platform": sys.platform, "supported": False, "reason": "Windows Event Log only", "logs": []}
    capped = max(1, min(int(limit), 100))
    out: list[dict[str, Any]] = []
    for log_name in logs:
        # Level 1 = Critical, Level 2 = Error. /rd:true = newest first. Text format, bounded count.
        cmd = ["wevtutil", "qe", log_name, "/q:*[System[(Level=1 or Level=2)]]", f"/c:{capped}", "/rd:true", "/f:text"]
        try:
            result = _run(cmd, runner)
            text = (getattr(result, "stdout", "") or "").strip()
            out.append({"log": log_name, "text": text[:6000], "truncated": len(text) > 6000})
        except Exception as exc:
            out.append({"log": log_name, "error": str(exc)})
    return {"platform": "win32", "supported": True, "logs": out}


def _parse_tasklist_csv(text: str, limit: int) -> list[dict[str, Any]]:
    import csv
    import io

    rows: list[dict[str, Any]] = []
    for record in csv.reader(io.StringIO(text)):
        # tasklist /fo csv /nh columns: Image Name, PID, Session Name, Session#, Mem Usage
        if len(record) < 5:
            continue
        mem_kb = "".join(ch for ch in record[4] if ch.isdigit())
        rows.append(
            {
                "name": record[0],
                "pid": int(record[1]) if record[1].isdigit() else None,
                "rss_mb": round(int(mem_kb) / 1024, 1) if mem_kb else 0.0,
            }
        )
    rows.sort(key=lambda item: item["rss_mb"], reverse=True)
    return rows[:limit]


def _sort_processes(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Order rows by the resource the caller asked about, memory when they did not say."""
    key = "cpu_percent" if str(sort or "").strip().lower() == "cpu" else "rss_mb"
    rows.sort(key=lambda item: float(item.get(key) or 0.0), reverse=True)
    return rows


def top_processes(*, limit: int = 12, sort: str = "memory", runner: Runner = subprocess.run) -> dict[str, Any]:
    """Heaviest processes by resident memory (``sort="cpu"`` ranks by CPU instead).

    Both numbers are read for every row regardless of the sort, because "what is eating my CPU"
    and "what is eating my RAM" are the same read with a different ranking -- and answering the
    CPU question with a memory ranking is the same class of wrong answer as answering it with a
    static spec sheet. Uses psutil when available, else tasklist/ps. Read-only.
    """
    capped = max(1, min(int(limit), 40))
    try:
        import psutil

        procs: list[dict[str, Any]] = []
        # cpu_percent() is measured since the previous call on that process object, and the first
        # call on a fresh object always returns 0.0. Prime every process, wait one short interval,
        # then read -- otherwise a CPU ranking is a list of zeros in arbitrary order.
        wants_cpu = str(sort or "").strip().lower() == "cpu"
        primed = []
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                if wants_cpu:
                    proc.cpu_percent(None)
                primed.append(proc)
            except Exception:
                continue
        if wants_cpu:
            time.sleep(_CPU_SAMPLE_SECONDS)
        cpu_count = psutil.cpu_count() or 1
        for proc in primed:
            try:
                info = proc.info
                rss = getattr(info.get("memory_info"), "rss", 0) or 0
                row = {"pid": info.get("pid"), "name": info.get("name") or "", "rss_mb": round(rss / _MiB, 1)}
                if wants_cpu:
                    # psutil reports a process's CPU as a share of ONE core (so 800% on 8 cores).
                    # Normalizing to whole-machine percent is what a user means by "eating my CPU".
                    # The key is omitted entirely on a memory ranking: without the sampling interval
                    # above every reading would be 0.0, and a row saying "0.0% CPU" is a fabricated
                    # measurement, not a missing one.
                    row["cpu_percent"] = round((proc.cpu_percent(None) or 0.0) / cpu_count, 1)
                procs.append(row)
            except Exception:
                continue
        return {"source": "psutil", "sort": sort, "processes": _sort_processes(procs, sort)[:capped]}
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            result = _run(["tasklist", "/fo", "csv", "/nh"], runner)
            rows = _parse_tasklist_csv(getattr(result, "stdout", "") or "", 40)
            return {"source": "tasklist", "sort": sort, "processes": _sort_processes(rows, sort)[:capped]}
        except Exception as exc:
            return {"source": "tasklist", "error": str(exc), "processes": []}
    try:
        result = _run(["ps", "-Ao", "pid=,%cpu=,rss=,comm="], runner)
        rows = []
        for line in (getattr(result, "stdout", "") or "").splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4 or not parts[0].isdigit():
                continue
            try:
                rows.append({
                    "pid": int(parts[0]),
                    "name": parts[3].strip(),
                    "rss_mb": round(int(parts[2]) / 1024, 1),
                    "cpu_percent": float(parts[1]),
                })
            except ValueError:
                continue
        return {"source": "ps", "sort": sort, "processes": _sort_processes(rows, sort)[:capped]}
    except Exception as exc:
        return {"source": "ps", "error": str(exc), "processes": []}


def _darwin_hardware_model(runner: Runner) -> str:
    """The Apple hardware identifier ("iMac24,1", "MacBookPro18,3"), or '' when unreadable."""
    try:
        result = _run(["sysctl", "-n", "hw.model"], runner)
        return str(getattr(result, "stdout", "") or "").strip()
    except Exception:
        return ""


def _windows_chassis(runner: Runner) -> str:
    """Portable-vs-desktop from the Win32 chassis type, or '' when unreadable.

    Chassis types 8-11 and 14 are the portable family (portable, laptop, notebook, hand-held,
    sub-notebook); everything else reported here is a fixed machine.
    """
    try:
        result = _run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_SystemEnclosure).ChassisTypes -join ','"],
            runner,
        )
        codes = {part.strip() for part in str(getattr(result, "stdout", "") or "").split(",")}
        if codes & {"8", "9", "10", "11", "14"}:
            return "laptop"
        return "desktop" if codes - {""} else ""
    except Exception:
        return ""


def host_state(*, runner: Runner = subprocess.run, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Live host state the static spec sheet does not hold: uptime, battery, and chassis.

    These change minute to minute, so they are a separate read from ``machine.inspect_specs``.
    Answering "what is this machine's uptime" or "what is the battery percentage" from a spec
    sheet is how a canned block ends up standing in for a measurement. Never raises; every field
    is independently best-effort, and an unreadable field is reported as unknown rather than
    guessed.
    """
    state: dict[str, Any] = {
        "uptime_seconds": None,
        "boot_time": None,
        "battery": {"present": None, "percent": None, "plugged_in": None},
        "chassis": "",
        "hardware_model": "",
    }
    try:
        import psutil

        boot = float(psutil.boot_time())
        state["boot_time"] = boot
        state["uptime_seconds"] = max(0.0, float(clock()) - boot)
    except Exception:
        pass
    try:
        import psutil

        battery = psutil.sensors_battery()
        if battery is None:
            # No battery sensor is itself an answer on a desktop -- and the only correct reply to
            # "what's the battery percentage" on an iMac.
            state["battery"] = {"present": False, "percent": None, "plugged_in": None}
        else:
            state["battery"] = {
                "present": True,
                "percent": round(float(battery.percent), 1),
                "plugged_in": bool(battery.power_plugged),
            }
    except Exception:
        pass
    if sys.platform == "darwin":
        model = _darwin_hardware_model(runner)
        state["hardware_model"] = model
        lowered = model.lower()
        if lowered.startswith(("macbook",)):
            state["chassis"] = "laptop"
        elif lowered.startswith(("imac", "macmini", "macpro", "macstudio")):
            state["chassis"] = "desktop"
    elif sys.platform == "win32":
        state["chassis"] = _windows_chassis(runner)
    if not state["chassis"]:
        # A battery sensor is the cross-platform fallback: portable machines have one, fixed
        # machines do not. Only consulted when the platform-specific read gave nothing.
        present = state["battery"].get("present")
        if present is True:
            state["chassis"] = "laptop"
        elif present is False:
            state["chassis"] = "desktop"
    return state
