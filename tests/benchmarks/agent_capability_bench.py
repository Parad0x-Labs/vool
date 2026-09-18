#!/usr/bin/env python3
"""
VOOL Agent Capability Benchmark
Compares: VOOL (tool-use agent with real file reads + iteration)
      vs: Single-shot Ollama (full context, no tools, one shot)

Tasks are designed so the correct fix requires:
  1. Reading the actual files (not guessing)
  2. Running tests to verify (and iterate on failures)
  3. Finding all affected files (multi-file tasks)

Usage:
  python -m tests.benchmarks.agent_capability_bench
  python -m tests.benchmarks.agent_capability_bench --vool-only
  python -m tests.benchmarks.agent_capability_bench --task multi_bug_stats
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

BENCH_DIR = Path("/tmp/vool_agent_bench")
MAX_STEPS = 10  # max agent iterations per task

# ── providers ──────────────────────────────────────────────────────────────────

VOOL_PROVIDER = {
    "label": "VOOL agent (native-8b + tools + iteration)",
    "url": "http://127.0.0.1:8090/v1/chat/completions",
    "model": "qwen3:8b-gguf",
    "api": "openai",
}

VOOL_FALLBACK = {
    "label": "VOOL agent (ollama-14b + tools + iteration, fallback)",
    "url": "http://127.0.0.1:11434/api/chat",
    "model": "qwen3:14b",
    "api": "ollama",
}

OLLAMA_PROVIDER = {
    "label": "Raw Ollama 14B (single-shot, full context, no tools)",
    "url": "http://127.0.0.1:11434/api/chat",
    "model": "qwen3:14b",
    "api": "ollama",
}

# ── task definitions ───────────────────────────────────────────────────────────
#
# Each task has real bugs planted in fixture files.
# Without reading the files and running tests, you cannot reliably fix them.

TASKS = [
    {
        "id": "binary_search",
        "label": "Off-by-one in binary search",
        "description": "Fix the bug in search.py so all tests pass.",
        "files_to_fix": ["search.py"],
        "fixtures": {
            "search.py": textwrap.dedent("""\
                def binary_search(nums, target):
                    lo, hi = 0, len(nums)
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        if nums[mid] == target:
                            return mid
                        elif nums[mid] < target:
                            lo = mid + 1
                        else:
                            hi = mid - 1
                    return -1
                """),
            "test_search.py": textwrap.dedent("""\
                from search import binary_search

                def test_found_first():
                    assert binary_search([1, 3, 5, 7, 9], 1) == 0

                def test_found_last():
                    assert binary_search([1, 3, 5, 7, 9], 9) == 4

                def test_found_middle():
                    assert binary_search([1, 3, 5, 7, 9], 5) == 2

                def test_not_found_between():
                    assert binary_search([1, 3, 5, 7, 9], 4) == -1

                def test_not_found_beyond():
                    # off-by-one bug causes IndexError when target > max element
                    assert binary_search([1, 3, 5, 7, 9], 10) == -1

                def test_single_element():
                    assert binary_search([42], 42) == 0
                """),
        },
    },
    {
        "id": "config_key",
        "label": "Config key mismatch (2 files)",
        "description": (
            "get_max_retries() raises a KeyError at runtime. "
            "Fix it so all tests pass. The config lives in config.json."
        ),
        "files_to_fix": ["config.py"],
        "fixtures": {
            "config.json": json.dumps({"max_retries": 3, "timeout": 30}, indent=2),
            "config.py": textwrap.dedent("""\
                import json
                from pathlib import Path

                def get_max_retries():
                    cfg = Path(__file__).parent / "config.json"
                    with open(cfg) as f:
                        data = json.load(f)
                    return data["max_retry"]
                """),
            "test_config.py": textwrap.dedent("""\
                from config import get_max_retries

                def test_type():
                    assert isinstance(get_max_retries(), int)

                def test_value():
                    assert get_max_retries() == 3
                """),
        },
    },
    {
        "id": "multi_bug_stats",
        "label": "Two bugs in stats.py (iteration required)",
        "description": (
            "The statistics module has multiple bugs. "
            "Run the tests, read the failures, fix all of them."
        ),
        "files_to_fix": ["stats.py"],
        "fixtures": {
            "stats.py": textwrap.dedent("""\
                def median(nums):
                    s = sorted(nums)
                    n = len(s)
                    if n % 2 == 0:
                        # off-by-one: should be s[n//2 - 1] and s[n//2]
                        return (s[n//2] + s[n//2 + 1]) / 2
                    return s[n//2]

                def trimmed_mean(nums, pct=0.1):
                    s = sorted(nums)
                    k = int(len(s) * pct)
                    # off-by-one in slice end: should be len(s) - k (not + 1)
                    trimmed = s[k : len(s) - k + 1]
                    if not trimmed:
                        return 0.0
                    return sum(trimmed) / len(trimmed)
                """),
            "test_stats.py": textwrap.dedent("""\
                from stats import median, trimmed_mean

                def test_median_odd():
                    assert median([3, 1, 5]) == 3

                def test_median_even():
                    assert median([1, 2, 3, 4]) == 2.5

                def test_median_even_larger():
                    assert median([10, 20, 30, 40]) == 25.0

                def test_trimmed_mean():
                    # 10 elements, trim 1 from each end => [2..9] => mean 5.5
                    assert trimmed_mean(list(range(1, 11)), pct=0.1) == 5.5

                def test_trimmed_mean_no_trim():
                    assert trimmed_mean([1, 2, 3], pct=0.0) == 2.0
                """),
        },
    },
    {
        "id": "cross_file_rename",
        "label": "Renamed function, 2 callers not updated",
        "description": (
            "utils.py renamed calc_total to calculate_total but two callers "
            "still import the old name. Fix all broken imports so tests pass."
        ),
        "files_to_fix": ["order.py", "invoice.py"],
        "fixtures": {
            "utils.py": textwrap.dedent("""\
                def calculate_total(items):
                    return sum(item["price"] * item["qty"] for item in items)

                def apply_discount(total, pct):
                    return total * (1 - pct / 100)
                """),
            "order.py": textwrap.dedent("""\
                from utils import calc_total, apply_discount

                def process_order(items, discount_pct=0):
                    total = calc_total(items)
                    return apply_discount(total, discount_pct)
                """),
            "invoice.py": textwrap.dedent("""\
                from utils import calc_total

                def generate_invoice(items):
                    return {"total": calc_total(items), "items": items}
                """),
            "test_all.py": textwrap.dedent("""\
                from order import process_order
                from invoice import generate_invoice

                ITEMS = [{"price": 10, "qty": 2}, {"price": 5, "qty": 4}]

                def test_order_no_discount():
                    assert process_order(ITEMS) == 40.0

                def test_order_discount():
                    assert process_order(ITEMS, discount_pct=10) == 36.0

                def test_invoice():
                    inv = generate_invoice(ITEMS)
                    assert inv["total"] == 40.0
                    assert inv["items"] == ITEMS
                """),
        },
    },
    {
        "id": "log_parser",
        "label": "Two bugs in log parser (strptime + None filter)",
        "description": "The log parser crashes and filters incorrectly. Fix log_parser.py so all tests pass.",
        "files_to_fix": ["log_parser.py"],
        "fixtures": {
            "log_parser.py": textwrap.dedent("""\
                import re
                from datetime import datetime

                LOG_RE = re.compile(r'(\\d{4}-\\d{2}-\\d{2}) (\\d{2}:\\d{2}:\\d{2}) (\\w+): (.+)')

                def parse_line(line):
                    m = LOG_RE.match(line)
                    if not m:
                        return None
                    d, t, level, msg = m.groups()
                    # wrong strptime format: day-month-year, should be year-month-day
                    ts = datetime.strptime(f"{d} {t}", "%d-%m-%Y %H:%M:%S")
                    return {"ts": ts, "level": level, "msg": msg}

                def filter_level(lines, level):
                    parsed = [parse_line(l) for l in lines]
                    # crashes if parse_line returns None (missing None guard)
                    return [p for p in parsed if p["level"] == level]
                """),
            "test_log_parser.py": textwrap.dedent("""\
                from log_parser import parse_line, filter_level

                LINES = [
                    "2024-01-15 14:23:05 ERROR: timeout",
                    "2024-01-15 14:23:10 INFO: retrying",
                    "2024-01-15 14:23:15 ERROR: still failing",
                ]

                def test_parse_level():
                    r = parse_line(LINES[0])
                    assert r is not None
                    assert r["level"] == "ERROR"

                def test_parse_timestamp_year():
                    r = parse_line(LINES[0])
                    assert r["ts"].year == 2024

                def test_parse_timestamp_month():
                    r = parse_line(LINES[0])
                    assert r["ts"].month == 1

                def test_parse_timestamp_day():
                    r = parse_line(LINES[0])
                    assert r["ts"].day == 15

                def test_filter_errors():
                    errors = filter_level(LINES, "ERROR")
                    assert len(errors) == 2

                def test_filter_info():
                    info = filter_level(LINES, "INFO")
                    assert len(info) == 1
                """),
        },
    },
]

# ── LLM calls ─────────────────────────────────────────────────────────────────


def call_openai(url: str, model: str, messages: list[dict], timeout: int = 240) -> str:
    """OpenAI-compatible endpoint — used for native llama-server (already has --reasoning off)."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": 0.1,
        "max_tokens": 2048,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data["choices"][0]["message"]["content"]


def call_ollama_chat(url: str, model: str, messages: list[dict], timeout: int = 120) -> str:
    """Ollama native API — supports think:false reliably."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 2048},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data["message"]["content"]


def call_llm(provider: dict, messages: list[dict], timeout: int = 240) -> str:
    """Route to the right call function based on provider API type."""
    if provider["api"] == "openai":
        return call_openai(provider["url"], provider["model"], messages, timeout=timeout)
    else:
        return call_ollama_chat(provider["url"], provider["model"], messages, timeout=timeout)


# ── agent system prompt ────────────────────────────────────────────────────────

SYSTEM_AGENT = """\
You are an autonomous coding agent fixing bugs in a Python project.
Tools — output EXACTLY one JSON object per turn (no extra text):

  {"tool":"list_files"}
  {"tool":"read_file","path":"filename.py"}
  {"tool":"write_file","path":"filename.py","content":"...full corrected file content..."}
  {"tool":"run_tests"}

Workflow:
1. list_files to see the project structure
2. read_file on relevant source files AND test files
3. write_file with the corrected code
4. run_tests to verify — if tests still fail, read the output and fix again
5. When all tests pass, output exactly: DONE

Output ONLY a JSON tool call or the word DONE. Never output prose."""

# ── tool executor ──────────────────────────────────────────────────────────────


def exec_tool(workspace: Path, call: dict) -> str:
    tool = call.get("tool", "")
    if tool == "list_files":
        files = sorted(
            str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file()
        )
        return "\n".join(files) or "(empty)"

    elif tool == "read_file":
        target = workspace / call.get("path", "")
        if not target.exists():
            return f"File not found: {call.get('path')}"
        return target.read_text()

    elif tool == "write_file":
        target = workspace / call.get("path", "")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(call.get("content", ""))
        return f"Written: {call.get('path')}"

    elif tool == "run_tests":
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-v", "--tb=short", "-q", "--no-header"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:3000] if len(out) > 3000 else out

    return f"Unknown tool: {tool}"


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_tool_call(text: str) -> dict | None:
    clean = _strip_think(text)
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", clean).strip()

    # Extract JSON object with proper brace counting — handles nested {} in Python code
    start = clean.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    end = -1

    for i, ch in enumerate(clean[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    if end == -1:
        return None

    json_str = clean[start : end + 1]

    def _fix_escapes(s: str) -> str:
        """Fix invalid JSON escape sequences (e.g. \\d, \\w from Python regex)."""
        valid = set('"\\\\/bfnrtu')
        out: list[str] = []
        i = 0
        in_str = False
        esc = False
        while i < len(s):
            ch = s[i]
            if esc:
                esc = False
                if in_str and ch not in valid:
                    out.append("\\")  # add extra backslash to make \\d valid JSON
                out.append(ch)
                i += 1
                continue
            if ch == "\\" and in_str:
                esc = True
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                in_str = not in_str
            out.append(ch)
            i += 1
        return "".join(out)

    for attempt in (json_str, _fix_escapes(json_str)):
        try:
            obj = json.loads(attempt)
            if "tool" in obj:
                return obj
        except json.JSONDecodeError:
            pass
    return None


# ── VOOL agent loop ───────────────────────────────────────────────────────────


def run_vool_agent(provider: dict, task: dict, workspace: Path) -> dict:
    # /nothink prefix disables Qwen3 thinking mode — agent loop needs speed, not long CoT
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_AGENT},
        {"role": "user", "content": f"/nothink\n\n{task['description']}"},
    ]
    steps = tool_calls = 0
    t0 = time.time()
    files_read: list[str] = []
    test_runs = 0

    for _ in range(MAX_STEPS):
        steps += 1
        try:
            raw = call_llm(provider, messages, timeout=240)
        except Exception as e:
            return {
                "passed": False, "steps": steps, "tool_calls": tool_calls,
                "elapsed": time.time() - t0, "error": str(e),
            }

        content = _strip_think(raw)
        messages.append({"role": "assistant", "content": raw})

        if re.search(r"\bDONE\b", content):
            break

        call = _parse_tool_call(content)
        if not call:
            # Model output prose without a tool call — treat as done
            break

        tool_calls += 1
        if call.get("tool") == "read_file":
            files_read.append(call.get("path", ""))
        if call.get("tool") == "run_tests":
            test_runs += 1

        result = exec_tool(workspace, call)
        messages.append({"role": "user", "content": f"[{call['tool']}]\n{result}"})

    # Final authoritative test run
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "--tb=no"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
    )
    passed = r.returncode == 0
    return {
        "passed": passed,
        "steps": steps,
        "tool_calls": tool_calls,
        "test_runs": test_runs,
        "files_read": files_read,
        "elapsed": time.time() - t0,
    }


# ── Ollama single-shot runner ──────────────────────────────────────────────────


def run_ollama_single_shot(task: dict, workspace: Path) -> dict:
    # Build context with ALL file contents — maximally generous to single-shot baseline
    file_context = ""
    for fname, content in task["fixtures"].items():
        file_context += f"\n\n### {fname}\n```python\n{content}\n```"

    files_to_fix = ", ".join(task["files_to_fix"])
    multi = len(task["files_to_fix"]) > 1

    if multi:
        output_instructions = (
            "Output each fixed file as a separate code block with the filename as "
            "a Python comment on the first line:\n"
            "```python\n# order.py\n[corrected code]\n```\n\n"
            "```python\n# invoice.py\n[corrected code]\n```"
        )
    else:
        output_instructions = (
            f"Output ONLY the corrected {files_to_fix} in a single code block. "
            f"No explanations."
        )

    prompt = (
        f"Here are all the project files:{file_context}\n\n"
        f"Task: {task['description']}\n\n"
        f"{output_instructions}"
    )

    t0 = time.time()
    try:
        raw = call_ollama_chat(
            OLLAMA_PROVIDER["url"],
            OLLAMA_PROVIDER["model"],
            [{"role": "user", "content": prompt}],
            timeout=120,
        )
    except Exception as e:
        return {"passed": False, "steps": 1, "elapsed": time.time() - t0, "error": str(e)}

    # Extract code blocks and write files
    written = 0
    for fname in task["files_to_fix"]:
        # Look for a code block preceded by the filename as a comment or heading
        patterns = [
            # ```python\n# fname.py\n...```
            rf"```(?:python)?\s*\n\s*#\s*{re.escape(fname)}\s*\n(.*?)```",
            # ### fname.py\n```python\n...```
            rf"###?\s+{re.escape(fname)}\s*\n```(?:python)?\s*\n?(.*?)```",
            # just the first code block (fallback for single-file tasks)
        ]
        for pat in patterns:
            m = re.search(pat, raw, re.DOTALL | re.IGNORECASE)
            if m:
                code = m.group(1).strip()
                if code:
                    (workspace / fname).write_text(code + "\n")
                    written += 1
                    break

    # Last-resort fallback: take the longest code block and apply to first file
    if not written:
        all_blocks = re.findall(r"```(?:python)?\n?(.*?)```", raw, re.DOTALL)
        if all_blocks:
            code = max(all_blocks, key=len).strip()
            # Strip leading comment line if it's a filename
            lines = code.splitlines()
            if lines and re.match(r"#\s*\S+\.py", lines[0]):
                code = "\n".join(lines[1:]).strip()
            if code:
                (workspace / task["files_to_fix"][0]).write_text(code + "\n")

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header", "--tb=no"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30,
    )
    passed = r.returncode == 0
    return {"passed": passed, "steps": 1, "tool_calls": 0, "elapsed": time.time() - t0}


# ── workspace setup ────────────────────────────────────────────────────────────


def setup_workspace(task: dict) -> Path:
    ws = BENCH_DIR / task["id"]
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    for fname, content in task["fixtures"].items():
        (ws / fname).write_text(content)
    return ws


# ── provider health check ──────────────────────────────────────────────────────


def check_openai_provider(url: str, timeout: int = 3) -> bool:
    probe = url.replace("/chat/completions", "/models")
    try:
        with urllib.request.urlopen(probe, timeout=timeout):
            return True
    except Exception:
        return False


def check_ollama(timeout: int = 3) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout):
            return True
    except Exception:
        return False


# ── report ─────────────────────────────────────────────────────────────────────

W = 76


def print_report(vool_label: str, vool_results: dict, ollama_results: dict) -> None:
    print()
    print("=" * W)
    print("VOOL AGENT CAPABILITY BENCHMARK".center(W))
    print("Tool-use agent vs single-shot LLM on real engineering tasks".center(W))
    print("=" * W)

    col_n = 28
    col_o = 24

    print(f"\n  {'TASK':<30}  {vool_label[:col_n]:<{col_n}}  {'Ollama 14B (1-shot)':<{col_o}}")
    print(f"  {'-'*30}  {'-'*col_n}  {'-'*col_o}")

    vool_total = ollama_total = 0
    for task in TASKS:
        tid = task["id"]
        nr = vool_results.get(tid, {})
        or_ = ollama_results.get(tid, {})

        if nr.get("passed"):
            vool_total += 1
            n_str = f"✓  {nr['steps']}steps/{nr.get('test_runs', '?')}runs  {nr['elapsed']:.1f}s"
        elif nr.get("error"):
            n_str = "✗  error"
        elif nr:
            n_str = f"✗  {nr.get('steps', '?')} steps"
        else:
            n_str = "(skipped)"

        if or_.get("passed"):
            ollama_total += 1
            o_str = f"✓  {or_['elapsed']:.1f}s"
        elif or_.get("error"):
            o_str = "✗  error"
        elif or_:
            o_str = "✗"
        else:
            o_str = "(skipped)"

        print(f"  {task['label']:<30}  {n_str:<{col_n}}  {o_str}")

    n5 = len(TASKS)
    print(f"  {'-'*30}  {'-'*col_n}  {'-'*col_o}")
    vool_pct = vool_total * 100 // n5 if n5 else 0
    ollama_pct = ollama_total * 100 // n5 if n5 else 0
    print(f"  {'SCORE':<30}  {vool_total}/{n5}  ({vool_pct}%){'':<17}  {ollama_total}/{n5}  ({ollama_pct}%)")

    print()
    if vool_total > ollama_total:
        diff = vool_total - ollama_total
        print(f"  VOOL wins {diff} task(s) that single-shot Ollama cannot complete.")
        print("  Real file reads + test-driven iteration = tasks impossible without tool use.")
    elif vool_total == ollama_total:
        print(f"  Tied: {vool_total}/{n5}. Consider adding harder multi-iteration tasks.")
    else:
        print("  Ollama 1-shot outperformed the agent. Tasks may be solvable from context alone.")

    print("=" * W)


# ── main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="VOOL agent capability benchmark")
    ap.add_argument("--vool-only", action="store_true", help="Skip Ollama single-shot")
    ap.add_argument("--ollama-only", action="store_true", help="Skip VOOL agent")
    ap.add_argument("--task", help="Run a single task by id")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    tasks = [t for t in TASKS if not args.task or t["id"] == args.task]
    if not tasks:
        print(f"Unknown task id: {args.task}")
        print("Available:", ", ".join(t["id"] for t in TASKS))
        sys.exit(1)

    # Resolve VOOL provider
    vool_provider = VOOL_PROVIDER
    run_vool = not args.ollama_only
    run_ollama = not args.vool_only

    if run_vool:
        if check_openai_provider(VOOL_PROVIDER["url"]):
            print(f"VOOL provider: {VOOL_PROVIDER['url']} (native-8b, no thinking)")
        elif check_ollama():
            vool_provider = VOOL_FALLBACK
            print(f"VOOL provider: {VOOL_FALLBACK['url']} ({VOOL_FALLBACK['model']}, think:false)")
        else:
            print("WARNING: no VOOL provider reachable — skipping agent runs")
            run_vool = False

    if run_ollama:
        if check_ollama():
            print(f"Ollama provider: {OLLAMA_PROVIDER['url']}")
        else:
            print("WARNING: Ollama not reachable — skipping single-shot runs")
            run_ollama = False

    if not run_vool and not run_ollama:
        print("No providers available. Start native servers or Ollama.")
        sys.exit(1)

    print(f"\nRunning {len(tasks)} task(s) ...\n")

    vool_results: dict = {}
    ollama_results: dict = {}

    for task in tasks:
        print(f"── {task['label']} ──")

        if run_vool:
            ws = setup_workspace(task)
            print("  VOOL agent ... ", end="", flush=True)
            r = run_vool_agent(vool_provider, task, ws)
            vool_results[task["id"]] = r
            if r.get("error"):
                print(f"ERROR: {r['error'][:60]}")
            else:
                verdict = "PASS" if r["passed"] else "FAIL"
                print(
                    f"{verdict}  "
                    f"steps={r['steps']}  "
                    f"tool_calls={r['tool_calls']}  "
                    f"test_runs={r.get('test_runs', '?')}  "
                    f"{r['elapsed']:.1f}s"
                )
                if args.verbose and r.get("files_read"):
                    print(f"    files read: {r['files_read']}")

        if run_ollama:
            ws = setup_workspace(task)
            print("  Ollama 1-shot .. ", end="", flush=True)
            r = run_ollama_single_shot(task, ws)
            ollama_results[task["id"]] = r
            if r.get("error"):
                print(f"ERROR: {r['error'][:60]}")
            else:
                verdict = "PASS" if r["passed"] else "FAIL"
                print(f"{verdict}  {r['elapsed']:.1f}s")

        print()

    vool_label = (vool_provider.get("label", "VOOL agent")[:28])
    print_report(vool_label, vool_results, ollama_results)

    out_path = Path("/tmp/vool_agent_capability_results.json")
    out_path.write_text(
        json.dumps({"vool": vool_results, "ollama": ollama_results}, indent=2)
    )
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
