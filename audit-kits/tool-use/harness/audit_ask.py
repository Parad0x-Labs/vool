#!/usr/bin/env python3
"""Audit harness: send ordinary asks to a REAL VOOL daemon running a REAL local model, record everything.

Frozen for the glm-audit-20261027 jobs (SHA256SUMS covers it). Run it with `python3 -I`; it never imports the
repo. What it does:

1. Boots `apps/vool_api_server.py` from --repo on a free loopback port, with a scratch VOOL_HOME (<out>/home), a
   fake HOME (--fakehome, built by make_fakehome.py) so "my Downloads" means the fixture folder, the Mac guard first
   on PYTHONPATH, the keychain off, plugins empty and VOOL_DEBUG_PROMPT=1 (so the offered tools are logged).
2. Makes --model ready the way a person does in Settings > Local models ("Use this model"): register, then the tool
   check. The verdict is written to <out>/model_ready.json. A model that is not ready is a finding; the run goes on.
3. For each case in --cases (JSON lines, see COMMON.md section 4), sends each turn through POST /api/chat on the case's
   own chat (canonical session id), and appends one row per case to <out>/raw.jsonl: HTTP status, reply text, latency,
   the runtime events of that chat, the tools offered to the model (prompt debug rows), new routing rows, new Mac
   guard refusals, new error lines of the daemon log, and auto flags.
4. Resumable: case ids already in raw.jsonl are skipped. --deadline (UTC, e.g. 2026-10-28T01:30Z) stops before a new
   case starts once it has passed. Ctrl-C stops the daemon it started, nothing else.

It writes only inside --out. It stops only the process group it started.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TURN_TIMEOUT = float(os.environ.get("AUDIT_TURN_TIMEOUT", "240"))
REFUSAL_MARKERS = (
    "not certified", "no certification run", "is not certified", "not_certified", "cannot author",
)
ERROR_LINE = re.compile(r"Traceback|\bERROR\b|Exception:|HTTP 500|status=500")


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def session_for(case_id: str, run_tag: str) -> str:
    return "openclaw:" + hashlib.sha256(f"{run_tag}:{case_id}".encode()).hexdigest()[:20]


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def jsonl_rows(path: Path) -> list[dict]:
    rows = []
    for line in read_lines(path):
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


class Daemon:
    def __init__(self, repo: Path, python: str, home: Path, fakehome: Path, extra_env: dict[str, str]):
        self.repo, self.python, self.home, self.fakehome = repo, python, home, fakehome
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self.extra_env = extra_env

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "empty-plugins").mkdir(exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VOOL_", "NULLA_", "OPENAI", "ANTHROPIC"))}
        guard = str(HERE / "macguard")
        env.update({
            "HOME": str(self.fakehome),
            "VOOL_HOME": str(self.home),
            "VOOL_KEYCHAIN_ALLOWED": "0",
            "VOOL_LOCAL_MODELS_ENABLED": "1",
            "VOOL_DEBUG_PROMPT": "1",
            "VOOL_PLUGINS_DIR": str(self.home / "empty-plugins"),
            "VOOL_DAEMON_BIND_PORT": str(free_port()),
            "VOOL_DAEMON_HEALTH_PORT": "0",
            "PYTHONPATH": guard + os.pathsep + str(self.repo),
        })
        env.update(self.extra_env)
        log = open(self.home / "daemon.log", "ab")
        cmd = [self.python, str(self.repo / "apps" / "vool_api_server.py"), "--port", str(self.port),
               "--bind", "127.0.0.1"]
        if platform.system() == "Darwin":
            cmd = ["nice", "-n", "10", *cmd]
        self.proc = subprocess.Popen(cmd, cwd=str(self.repo), env=env, stdout=log, stderr=log,
                                     start_new_session=True)
        (self.home.parent / "daemon.pid").write_text(f"{self.proc.pid}\n")
        deadline = time.time() + 180
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"daemon exited early (code {self.proc.returncode}); see {self.home}/daemon.log")
            try:
                with urllib.request.urlopen(f"{self.base}/api/skills", timeout=3) as response:
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(1.0)
        raise SystemExit(f"daemon never became ready; see {self.home}/daemon.log")

    def stop(self) -> None:
        if self.proc is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(self.proc.pid), sig)
            except (ProcessLookupError, PermissionError):
                break
            for _ in range(50):
                if self.proc.poll() is not None:
                    break
                time.sleep(0.2)
            if self.proc.poll() is not None:
                break
        self.proc = None

    def request(self, method: str, path: str, payload: dict | None = None, timeout: float = 60.0) -> tuple[int, dict | str]:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except Exception as exc:  # timeout, refused
            return 0, f"{type(exc).__name__}: {exc}"
        try:
            return status, json.loads(raw)
        except Exception:
            return status, parse_stream(raw) if "\n" in raw.strip() else raw


def parse_stream(raw: str) -> dict:
    """NDJSON reply (stream or event lines): join the content pieces, keep the event lines."""
    parts, events = [], []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if "vool_event" in row:
            events.append(row)
            continue
        text = reply_text(row)
        if text:
            parts.append(text)
    return {"message": {"content": "".join(parts)}, "_stream_events": events}


def reply_text(body) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""
    msg = body.get("message")
    if isinstance(msg, dict) and msg.get("content"):
        return str(msg["content"])
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        for key in ("message", "delta"):
            part = first.get(key) or {}
            if isinstance(part, dict) and part.get("content"):
                return str(part["content"])
    for key in ("response", "reply", "content", "text", "error", "detail"):
        if body.get(key):
            return str(body[key])
    return ""


def ready_model(daemon: Daemon, model: str, out: Path) -> dict:
    record: dict = {"model": model, "at": utcnow()}
    status, listing = daemon.request("GET", "/api/models/local", timeout=30)
    record["listing_status"] = status
    record["listing"] = listing
    entry = None
    if isinstance(listing, dict):
        entry = next((m for m in listing.get("models") or [] if m.get("model_name") == model), None)
    if entry is None:
        record["readiness"] = "model_not_installed_in_ollama"
    else:
        if not entry.get("registered"):
            record["register"] = daemon.request("POST", "/api/models/local/register", {"model_name": model})
        started = time.time()
        record["check"] = daemon.request(
            "POST", "/api/model-tool-certification/run",
            {"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 150}, timeout=400)
        record["check_seconds"] = round(time.time() - started, 1)
        body = record["check"][1]
        record["readiness"] = str(body.get("readiness") or body.get("state") or "unknown") if isinstance(body, dict) else "unknown"
    (out / "model_ready.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    return record


def auto_flags(row: dict, case: dict) -> list[str]:
    flags = []
    for turn in row["turns"]:
        if turn["http_status"] != 200:
            flags.append(f"http_{turn['http_status']}")
        text = (turn["reply"] or "").strip()
        if not text:
            flags.append("empty_reply")
        low = text.lower()
        if any(marker in low for marker in REFUSAL_MARKERS):
            flags.append("not_certified_refusal")
        if turn["seconds"] > 120:
            flags.append("slow_over_120s")
    if row["macguard_new"]:
        flags.append("macguard_refused")
    if row["daemon_errors_new"]:
        flags.append("daemon_error_lines")
    expect = case.get("expect") or {}
    wanted = set(expect.get("tool_any") or [])
    if wanted:
        seen = set(row.get("tools_executed") or [])
        if not (wanted & seen):
            flags.append("expected_tool_not_executed")
    return sorted(set(flags))


def runtime_events(daemon: Daemon, session: str) -> list[dict]:
    status, body = daemon.request("GET", f"/api/runtime/events?session={session}", timeout=30)
    if status == 200 and isinstance(body, dict):
        return list(body.get("events") or [])
    return [{"_events_unavailable": status, "_body": str(body)[:300]}]


def answered_by(events: list[dict], routing: dict) -> dict:
    """Who answered: a word-pattern fast path (family) or a model (provider/model), from the turn's own records."""
    fast = [row.get("family") for row in routing.get("routing_decisions.jsonl", []) if row.get("handled")]
    lanes = [{"backend": e.get("backend"), "provider": e.get("provider_id"), "model": e.get("model_id"),
              "fallback_reason": e.get("fallback_reason")}
             for e in events if e.get("event_type") == "model_lane_proof"]
    failed = [str(e.get("message") or "")[:200] for e in events
              if str(e.get("event_type") or "").endswith(("_failed", "_declined"))]
    return {"fast_path_families": fast, "model_lanes": lanes, "failures": failed}


def executed(events: list[dict], *, ran_only: bool = False) -> list[str]:
    """Tool names seen this turn. ran_only: only what actually ran (receipts, tool_executed/tool_result),
    not a selection or a proposed call."""
    names = []
    for event in events:
        if event.get("event_type") == "semantic_resolution_receipt":
            execution = (event.get("receipt") or {}).get("execution") or {}
            for item in list(execution.get("executed_tools") or []) + list(execution.get("executed_operations") or []):
                names.append(str(item.get("intent") or item.get("name") or item) if isinstance(item, dict) else str(item))
            continue
        kind = str(event.get("event_type") or "")
        if kind in (("tool_executed", "tool_result") if ran_only else
                    ("tool_executed", "tool_result", "tool_selected", "tool_call", "tool_started")):
            name = str(event.get("intent") or event.get("tool") or event.get("tool_name") or "")
            if name:
                names.append(name)
    return names


PHONE: dict | None = None  # set by --surface phone: {"node": path, "repo": path}


def phone_turns(daemon: Daemon, case: dict, home: Path) -> tuple[str, list[dict]]:
    """Send the case through the real phone protocol code (phone_ask.mjs); return (canonical chat id, turns)."""
    case_file = home.parent / "phone-case.json"
    case_file.write_text(json.dumps(case))
    try:
        done = subprocess.run(
            [PHONE["node"], str(HERE / "phone_ask.mjs"), PHONE["repo"], daemon.base, str(case_file),
             str(home.parent / "phone-creds.json")],
            capture_output=True, text=True, timeout=TURN_TIMEOUT * max(1, len(case["turns"])) + 60)
        result = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
        if not result:
            result = {"turns": [], "error": (done.stderr or "no output")[-2000:]}
    except Exception as exc:
        result = {"turns": [], "error": f"{type(exc).__name__}: {exc}"}
    turns = list(result.get("turns") or [])
    if result.get("error"):
        turns.append({"words": "(phone driver)", "http_status": 0, "seconds": 0, "reply": "",
                      "body": {"driver_error": result["error"]}})
    return str(result.get("canonical_chat_id") or ""), turns


def run_case(daemon: Daemon, case: dict, run_tag: str, home: Path) -> dict:
    session = session_for(case["id"], run_tag)
    prompt_log = home / "logs" / "prompt_debug.jsonl"
    routing_logs = [home / "data" / "routing_decisions.jsonl", home / "data" / "turn_routing_events.jsonl"]
    guard_log = home / "macguard.log"
    daemon_log = home / "daemon.log"
    marks = {
        "prompt": len(read_lines(prompt_log)),
        "guard": len(read_lines(guard_log)),
        "daemon": len(read_lines(daemon_log)),
        **{str(p): len(read_lines(p)) for p in routing_logs},
    }
    turns = []
    history: list[dict] = []
    if PHONE is not None:
        session, turns = phone_turns(daemon, case, home)
    for words in ([] if PHONE is not None else case["turns"]):
        history.append({"role": "user", "content": words})
        payload = {"messages": list(history), "session_id": session, "stream": False}
        if case.get("mode"):
            payload["mode"] = case["mode"]
        started = time.time()
        status, body = daemon.request("POST", "/api/chat", payload, timeout=TURN_TIMEOUT)
        seconds = round(time.time() - started, 1)
        # status 0 = the harness's own HTTP timeout or refusal, not a VOOL reply: keep it out of the reply text.
        harness_error = str(body) if status == 0 else ""
        text = "" if status == 0 else reply_text(body)
        turns.append({"words": words, "http_status": status, "seconds": seconds, "reply": text,
                      "harness_error": harness_error,
                      "body": body if isinstance(body, dict) else str(body)[:4000]})
        history.append({"role": "assistant", "content": text})
    events = runtime_events(daemon, session)
    prompt_rows = []
    for line in read_lines(prompt_log)[marks["prompt"]:]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        tools = (row.get("payload") or {}).get("tools") or row.get("tools") or row.get("offered_tools") or []
        names = [t.get("function", {}).get("name") or t.get("name") for t in tools if isinstance(t, dict)]
        prompt_rows.append({"model": row.get("model") or (row.get("payload") or {}).get("model"), "offered": [n for n in names if n],
                            "keys": sorted(row.keys())[:30]})
    routing = {Path(p).name: [json.loads(line) for line in read_lines(Path(p))[marks[p]:] if line.strip().startswith("{")]
               for p in map(str, routing_logs)}
    daemon_new = read_lines(daemon_log)[marks["daemon"]:]
    row = {
        "id": case["id"],
        "job": case.get("job", ""),
        "surface": "phone" if PHONE is not None else "pc",
        "session": session,
        "at": utcnow(),
        "turns": turns,
        "tools_executed": executed(events),
        "tools_ran": executed(events, ran_only=True),
        "answered_by": answered_by(events, routing),
        "events": events[-80:],
        "offered_per_round": prompt_rows,
        "routing_new": routing,
        "macguard_new": read_lines(guard_log)[marks["guard"]:],
        "daemon_errors_new": [line for line in daemon_new if ERROR_LINE.search(line)][-40:],
        "expect": case.get("expect") or {},
    }
    row["auto_flags"] = auto_flags(row, case)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--python", required=True, help="the venv python that runs the daemon")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--cases", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--fakehome", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--deadline", default="", help="UTC like 2026-10-28T01:30Z: no new case starts after it")
    ap.add_argument("--surface", choices=("pc", "phone"), default="pc",
                    help="phone: send every turn through the real phone protocol code (needs node 22.6+)")
    ap.add_argument("--node", default="node")
    ap.add_argument("--skip-model-check", action="store_true", help="reuse <out>/model_ready.json from an earlier start")
    args = ap.parse_args()

    global PHONE
    if args.surface == "phone":
        PHONE = {"node": args.node, "repo": str(args.repo.resolve())}
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    home = out / "home"
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    raw_path = out / "raw.jsonl"
    done = {row.get("id") for row in jsonl_rows(raw_path)}
    deadline = None
    if args.deadline:
        deadline = dt.datetime.strptime(args.deadline, "%Y-%m-%dT%H:%MZ").replace(tzinfo=dt.timezone.utc).timestamp()
    head = subprocess.run(["git", "-C", str(args.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(args.repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    run_tag = out.name
    meta = {"started": utcnow(), "repo": str(args.repo), "head": head, "dirty": bool(dirty.strip()),
            "platform": platform.platform(), "model": args.model, "surface": args.surface, "cases_file": str(args.cases),
            "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(), "turn_timeout_s": TURN_TIMEOUT,
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with open(out / "run.json", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(meta) + "\n")

    daemon = Daemon(args.repo.resolve(), args.python, home, args.fakehome.resolve(), {})
    try:
        daemon.start()
        if args.skip_model_check and (out / "model_ready.json").is_file():
            ready = json.loads((out / "model_ready.json").read_text())
            # The daemon restarted: the check result lives in VOOL_HOME, so it is still there.
        else:
            ready = ready_model(daemon, args.model, out)
        print(f"model {args.model}: {ready.get('readiness')}", flush=True)
        for case in cases:
            if case["id"] in done:
                continue
            if deadline and time.time() > deadline:
                print("deadline passed: stopping before", case["id"], flush=True)
                break
            row = run_case(daemon, case, run_tag, home)
            with open(raw_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str) + "\n")
            print(f"{case['id']}: {row['turns'][-1]['http_status']} {row['turns'][-1]['seconds']}s "
                  f"tools={row['tools_executed'][:4]} flags={row['auto_flags']}", flush=True)
    except KeyboardInterrupt:
        print("interrupted", flush=True)
    finally:
        daemon.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
