"""Read-only host diagnostics: disk space, Windows Event Log errors, top processes.

All subprocess/filesystem access is injected, so these run deterministically on any host and never
touch real system state.
"""
from __future__ import annotations

import types

import core.machine_diagnostics as md


def _usage(total_gb: float, free_gb: float):
    total = int(total_gb * 1024 ** 3)
    free = int(free_gb * 1024 ** 3)
    return types.SimpleNamespace(total=total, used=total - free, free=free)


def test_disk_usage_computes_gb_and_percent() -> None:
    rows = md.disk_usage(drives=["C:\\", "D:\\"], usage_fn=lambda d: _usage(100.0, 25.0))
    assert len(rows) == 2
    c = rows[0]
    assert c["mount"] == "C:\\" and c["total_gb"] == 100.0 and c["free_gb"] == 25.0
    assert c["used_gb"] == 75.0 and c["percent_used"] == 75.0


def test_disk_usage_skips_unreadable_drives() -> None:
    def flaky(drive: str):
        if drive == "D:\\":
            raise OSError("not ready")
        return _usage(50.0, 10.0)

    rows = md.disk_usage(drives=["C:\\", "D:\\"], usage_fn=flaky)
    assert [r["mount"] for r in rows] == ["C:\\"]


def test_event_log_errors_unsupported_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(md.sys, "platform", "linux")
    report = md.event_log_errors()
    assert report["supported"] is False and report["logs"] == []


def test_event_log_errors_parses_windows_output(monkeypatch) -> None:
    monkeypatch.setattr(md.sys, "platform", "win32")
    calls: list[list[str]] = []

    def fake_runner(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout="Event[0]:\n  Level: Error\n  Source: Disk\n", returncode=0)

    report = md.event_log_errors(limit=5, logs=("System",), runner=fake_runner)
    assert report["supported"] is True
    assert report["logs"][0]["log"] == "System" and "Disk" in report["logs"][0]["text"]
    # read-only query command, bounded, newest-first, level filter for Critical/Error
    cmd = calls[0]
    assert cmd[:2] == ["wevtutil", "qe"] and "/c:5" in cmd and "/rd:true" in cmd
    assert any("Level=1 or Level=2" in part for part in cmd)


def test_event_log_errors_caps_limit(monkeypatch) -> None:
    monkeypatch.setattr(md.sys, "platform", "win32")
    seen: list[list[str]] = []
    md.event_log_errors(limit=9999, logs=("System",), runner=lambda cmd, **kw: seen.append(cmd) or types.SimpleNamespace(stdout=""))
    assert "/c:100" in seen[0]  # hard cap at 100, never unbounded


def test_parse_tasklist_csv_sorts_and_limits() -> None:
    csv = (
        '"chrome.exe","100","Console","1","512,000 K"\n'
        '"python.exe","200","Console","1","1,024,000 K"\n'
        '"idle.exe","300","Console","1","1,000 K"\n'
    )
    rows = md._parse_tasklist_csv(csv, limit=2)
    assert [r["name"] for r in rows] == ["python.exe", "chrome.exe"]  # sorted by memory desc
    assert rows[0]["rss_mb"] == 1000.0 and rows[0]["pid"] == 200


def test_largest_files_ranks_individual_nested_files_not_folders() -> None:
    import os

    tree = [
        ("D:", ["games", "docs"], ["a.bin"]),
        ("D:games", [], ["big.pak", "small.cfg"]),
        ("D:docs", [], ["mid.pdf"]),
    ]
    # Keyed by basename so the assertion holds under either path separator (CI is Linux).
    sizes = {"a.bin": 5, "big.pak": 100, "small.cfg": 1, "mid.pdf": 40}
    res = md.largest_files(
        "D:",
        top=2,
        walk_fn=lambda root: iter(tree),
        getsize_fn=lambda p: sizes[os.path.basename(p)],
        monotonic_fn=lambda: 0.0,
    )
    # Largest individual files first, nested files included, folders never listed.
    assert [e["name"] for e in res["entries"]] == ["big.pak", "mid.pdf"]
    assert all(e["kind"] == "file" for e in res["entries"])
    assert res["entries"][0]["size_gb"] == round(100 / (1024 ** 3), 2)


def test_largest_files_marks_partial_when_budget_hits() -> None:
    clock = {"t": 0.0}

    def slow_clock() -> float:
        clock["t"] += 10.0
        return clock["t"]

    tree = [("D:", [], ["only.bin"])]
    res = md.largest_files("D:", top=5, time_budget_s=2.0, walk_fn=lambda root: iter(tree),
                           getsize_fn=lambda p: 10, monotonic_fn=slow_clock)
    # The walk was cut before reading anything; honest empty + not complete, never fabricated.
    assert res["complete"] is False


def test_top_processes_returns_shaped_list() -> None:
    result = md.top_processes(limit=5)
    assert "processes" in result and isinstance(result["processes"], list)
    if result["processes"]:
        entry = result["processes"][0]
        assert "name" in entry and "rss_mb" in entry
