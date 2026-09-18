from __future__ import annotations

from typing import Any


def inspect_processes(
    *,
    os_name: str,
    subprocess_run: Any,
    csv_module: Any,
) -> list[dict[str, Any]]:
    if os_name == "nt":
        completed = subprocess_run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows: list[dict[str, Any]] = []
        for record in csv_module.reader(completed.stdout.splitlines()):
            if len(record) < 2:
                continue
            rows.append({"pid": record[1], "name": record[0], "cpu_percent": 0.0, "mem_percent": 0.0})
        return rows[:8]

    completed = subprocess_run(
        ["ps", "-Ao", "pid=,%cpu=,%mem=,comm="],
        capture_output=True,
        text=True,
        timeout=4,
        check=False,
    )
    if completed.returncode != 0:
        return []
    rows = []
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            rows.append(
                {
                    "pid": parts[0],
                    "cpu_percent": float(parts[1]),
                    "mem_percent": float(parts[2]),
                    "name": parts[3],
                }
            )
        except ValueError:
            continue
    rows.sort(key=lambda row: (row["cpu_percent"] + row["mem_percent"], row["cpu_percent"]), reverse=True)
    return rows[:8]


def inspect_services(
    *,
    os_name: str,
    subprocess_run: Any,
    which_fn: Any,
) -> list[dict[str, Any]]:
    if os_name == "nt":
        completed = subprocess_run(
            ["sc", "query", "type=", "service", "state=", "all"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows: list[dict[str, Any]] = []
        current: dict[str, str] | None = None
        for line in completed.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("SERVICE_NAME:"):
                if current:
                    rows.append(current)
                current = {"name": stripped.split(":", 1)[1].strip(), "state": "unknown", "detail": ""}
                continue
            if current is None:
                continue
            if stripped.startswith("STATE"):
                current["state"] = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith("DISPLAY_NAME:"):
                current["detail"] = stripped.split(":", 1)[1].strip()
        if current:
            rows.append(current)
        return rows[:12]

    if which_fn("systemctl"):
        completed = subprocess_run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if completed.returncode == 0:
            rows = []
            for line in completed.stdout.splitlines():
                parts = line.split(None, 4)
                if len(parts) < 5:
                    continue
                rows.append(
                    {
                        "name": parts[0],
                        "state": parts[2],
                        "detail": parts[4].strip(),
                    }
                )
            rows.sort(key=lambda row: (row["state"] != "running", row["name"]))
            return rows[:12]

    if which_fn("launchctl"):
        completed = subprocess_run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if completed.returncode != 0:
            return []
        rows = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid, status, label = parts
            state = "running" if pid != "-" else "loaded"
            rows.append({"name": label.strip(), "state": state, "detail": f"pid={pid} status={status}"})
        return rows[:12]

    return []
