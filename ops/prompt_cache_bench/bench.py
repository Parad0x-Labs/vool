"""Prompt-cache bench: the same multi-turn tasks through VOOL and through Claude Code's CLI.

Measures, per turn and per arm: wall time, prompt/cached/cache-write/output tokens, the money the
provider reported, and the answer (graded later, blind). Spend is metered against one hard cap
for the whole run root, checked before every billable call; a run stops cleanly at the cap.

Subcommands (run from the repo root, with the repo's own Python):

  preflight  Read OpenRouter's public model catalog: ids, prices, cache prices. No key, no spend.
  vool       Stage an exact commit, boot an isolated daemon pinned to one OpenRouter model, and
             drive the task set through POST /api/chat. Needs OPENROUTER_API_KEY in the
             environment, or --key-source vool-store (VOOL's own saved key, never printed).
  cli        Drive the same tasks through `claude -p --output-format json` (Claude Code), one
             resumed session per task, in a fresh copy of the fixture workspace.
  grade      Blind-grade every answer against its reference with one judge model on OpenRouter.
  report     Write REPORT.md and summary.json from what was measured. Unknown stays unknown.

Everything an arm produces lands under <run-root>/<label>/; results.jsonl rows carry the arm,
task, repeat, turn and attempt, so a resumed run never double-counts and never re-spends a
finished (task, repeat).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
TASKS_PATH = HERE / "tasks.json"
FIXTURE_WORKSPACE = HERE / "fixtures" / "files"
OPENROUTER = "https://openrouter.ai/api/v1"
TURN_TIMEOUT_S = 300.0
# Held before every billable turn until a real turn cost is known; then 2x the largest seen.
INITIAL_TURN_RESERVE_USD = 0.40

EXIT_OK = 0
EXIT_CAP = 4
EXIT_HARNESS = 2
EXIT_USAGE = 3
EXIT_INCOMPLETE = 5


class ArmAborted(RuntimeError):
    """The arm's first chat failed: every later chat would fail the same way, so none is sent."""


class HarnessError(RuntimeError):
    pass


def _close_chat(results_path: Path, task_id: str, repeat: int, attempt: str, failure: str, *, any_success: bool) -> bool:
    """Record how a chat ended. Only a chat whose every turn answered is ``chat_complete``.

    A failed chat is recorded as ``chat_failed`` so a resume runs it again and the report counts it.
    Returns whether the chat succeeded; raises :class:`ArmAborted` when nothing has succeeded yet.
    """
    if not failure:
        _append_jsonl(results_path, {"kind": "chat_complete", "task": task_id, "repeat": repeat, "attempt": attempt})
        return True
    _append_jsonl(results_path, {"kind": "chat_failed", "task": task_id, "repeat": repeat, "attempt": attempt, "reason": failure})
    if not any_success:
        raise ArmAborted(f"first chat failed ({task_id} r{repeat} {failure}); no further chats sent")
    return False


class CapReached(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# small io helpers


def _now() -> float:
    return time.time()


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with contextlib.suppress(OSError):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                with contextlib.suppress(ValueError):
                    rows.append(json.loads(line))
    return rows


def load_tasks(only: str = "") -> list[dict[str, Any]]:
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))["tasks"]
    if only:
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        unknown = wanted - {task["id"] for task in tasks}
        if unknown:
            raise HarnessError(f"unknown task ids: {sorted(unknown)}")
        tasks = [task for task in tasks if task["id"] in wanted]
    return tasks


# --------------------------------------------------------------------------------------------
# spend: one ledger per run root, checked before every billable call


class SpendLedger:
    """Money the providers reported, against one hard cap for the whole run root.

    ``billed_usd`` is money OpenRouter reported charging (VOOL turns and judge calls).
    ``notional_usd`` is what Claude Code reports for a turn billed to a Claude login: a list-price
    figure, not a charge on the OpenRouter key. Both count toward the cap unless the run says
    otherwise, so the cap can only stop a run early, never late.
    """

    def __init__(self, root: Path, cap_usd: float, *, count_notional: bool = True) -> None:
        self.path = root / "spend.json"
        self.cap_usd = float(cap_usd)
        self.count_notional = count_notional
        state = _read_json(self.path, {})
        self.billed_usd = float(state.get("billed_usd") or 0.0)
        self.notional_usd = float(state.get("notional_usd") or 0.0)
        self.unknown_cost_calls = int(state.get("unknown_cost_calls") or 0)
        self.max_turn_usd = float(state.get("max_turn_usd") or 0.0)
        if state.get("cap_usd") not in (None, self.cap_usd):
            # The cap is a property of the run root; a resumed run may lower it, never raise it.
            self.cap_usd = min(self.cap_usd, float(state["cap_usd"]))

    @property
    def counted_usd(self) -> float:
        return self.billed_usd + (self.notional_usd if self.count_notional else 0.0)

    def reserve(self) -> float:
        return max(INITIAL_TURN_RESERVE_USD, 2.0 * self.max_turn_usd)

    def check(self, what: str) -> None:
        if self.counted_usd + self.reserve() > self.cap_usd:
            raise CapReached(
                f"cap stop before {what}: counted ${self.counted_usd:.4f} + reserve ${self.reserve():.4f} "
                f"> cap ${self.cap_usd:.2f}"
            )

    def record(self, *, billed: float | None, notional: float | None, unknown: bool) -> None:
        if billed:
            self.billed_usd += float(billed)
        if notional:
            self.notional_usd += float(notional)
        if unknown:
            self.unknown_cost_calls += 1
        self.max_turn_usd = max(self.max_turn_usd, float(billed or 0.0) + float(notional or 0.0))
        self.save()

    def save(self) -> None:
        _write_json(
            self.path,
            {
                "cap_usd": self.cap_usd,
                "billed_usd": round(self.billed_usd, 8),
                "notional_usd": round(self.notional_usd, 8),
                "count_notional": self.count_notional,
                "unknown_cost_calls": self.unknown_cost_calls,
                "max_turn_usd": round(self.max_turn_usd, 8),
                "updated_at": _now(),
            },
        )


# --------------------------------------------------------------------------------------------
# OpenRouter: catalog, key usage, judge calls


def _http_json(url: str, *, key: str = "", body: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    request.add_header("Content-Type", "application/json")
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def openrouter_catalog() -> list[dict[str, Any]]:
    return list(_http_json(f"{OPENROUTER}/models").get("data") or [])


def key_usage_usd(key: str) -> float | None:
    """The key's lifetime usage counter on OpenRouter, a cross-check only (other use moves it)."""
    try:
        data = _http_json(f"{OPENROUTER}/key", key=key, timeout=30.0).get("data") or {}
        value = data.get("usage")
        return float(value) if isinstance(value, (int, float)) else None
    except Exception:
        return None


def resolve_key(source: str) -> str:
    """The OpenRouter key, held in memory only. Never printed, logged or written."""
    if source == "env":
        key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not key:
            raise HarnessError("OPENROUTER_API_KEY is not set (or use --key-source vool-store)")
        return key
    if source == "vool-store":
        # VOOL's own saved key, read through VOOL's own credential store from the operator's
        # normal profile. A VOOL_HOME pointing elsewhere would read a different profile.
        if os.environ.get("VOOL_HOME"):
            raise HarnessError("--key-source vool-store needs VOOL_HOME unset (it reads your normal VOOL profile)")
        sys.path.insert(0, str(REPO))
        from core import credential_store

        key = str(credential_store.get_credential("llm.cloud.openrouter") or "").strip()
        if not key:
            raise HarnessError("VOOL has no saved OpenRouter key")
        return key
    raise HarnessError(f"unknown key source {source!r}")


def cmd_preflight(args: argparse.Namespace) -> int:
    catalog = openrouter_catalog()
    wanted = [item.strip() for item in args.models.split(",") if item.strip()]
    by_id = {row.get("id"): row for row in catalog}
    out: dict[str, Any] = {"read_at": _now(), "models": {}}
    for model in wanted:
        row = by_id.get(model)
        if row is None:
            out["models"][model] = {"present": False}
            continue
        pricing = row.get("pricing") or {}
        out["models"][model] = {
            "present": True,
            "context_length": row.get("context_length"),
            "per_m": {k: round(float(v) * 1e6, 4) for k, v in pricing.items() if _is_number_text(v)},
        }
    if args.search:
        needles = [item.strip().lower() for item in args.search.split(",") if item.strip()]
        out["search"] = sorted(
            row.get("id") for row in catalog if any(n in str(row.get("id") or "").lower() for n in needles)
        )
    print(json.dumps(out, indent=1, sort_keys=True))
    return EXIT_OK


def _is_number_text(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------------------------
# result rows


def _done_keys(rows: list[dict[str, Any]]) -> set[tuple[str, int]]:
    """(task, repeat) pairs whose every turn finished in one attempt."""
    finished: set[tuple[str, int]] = set()
    for row in rows:
        if row.get("kind") == "chat_complete":
            finished.add((row["task"], int(row["repeat"])))
    return finished


def _spend_line(ledger: SpendLedger, results_path: Path) -> str:
    arm = sum(float(r.get("usd") or 0.0) for r in _read_jsonl(results_path) if r.get("kind") == "turn")
    return (
        f"arm ${arm:.4f} | run billed ${ledger.billed_usd:.4f} + notional ${ledger.notional_usd:.4f} "
        f"| hard stop at ${ledger.cap_usd:.2f}"
    )


def _remove_scratch(attempt_dir: Path, root: Path) -> list[str]:
    """Delete the attempt's throwaway product copy, profile and workspaces; keep the evidence.

    Only these named children of an attempt directory inside the run root are removed.
    """
    attempt_dir = attempt_dir.resolve()
    if root.resolve() not in attempt_dir.parents:
        raise HarnessError(f"refusing to clean {attempt_dir}: not inside the run root")
    removed = []
    for name in ("app", "home", "user-home", "ws", "workspace-root"):
        target = attempt_dir / name
        if target.exists():
            for path in [target, *target.rglob("*")]:
                with contextlib.suppress(OSError):
                    path.chmod(0o700 if path.is_dir() else 0o600)
            shutil.rmtree(target)
            removed.append(name)
    return removed


def _fresh_workspace(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(FIXTURE_WORKSPACE, dest)
    return dest


# --------------------------------------------------------------------------------------------
# VOOL arm


def _write_home_policy(app_dir: Path, home: Path, *, per_call: float, per_task: float, daily: float) -> None:
    """Copy the staged app's policy into the isolated home with the paid-call caps for the run.

    A home policy file REPLACES the app's, so it starts from the app's own bytes.
    """
    import yaml

    source = app_dir / "config" / "default_policy.yaml"
    policy = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    block = dict(policy.get("model_orchestration") or {})
    block.update(
        {
            "paid_call_per_call_max_usd": round(per_call, 4),
            "paid_call_per_task_max_usd": round(per_task, 4),
            "paid_call_daily_max_usd": round(daily, 4),
            "paid_call_monthly_max_usd": round(daily, 4),
        }
    )
    policy["model_orchestration"] = block
    target = home / "config" / "default_policy.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")


def _turn_usage(events: list[dict[str, Any]], seen: int, model: str) -> dict[str, Any]:
    """Sum the model_usage events this turn emitted (events after index ``seen``)."""
    calls = [e for e in events[seen:] if str(e.get("event_type") or e.get("type") or "") == "model_usage"]
    out: dict[str, Any] = {
        "calls": len(calls),
        "prompt_tokens": 0,
        "output_tokens": 0,
        "usd": 0.0,
        "usd_unknown_calls": 0,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "request_seconds": None,
        "served_models": [],
    }
    for event in calls:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        out["prompt_tokens"] += int(data.get("prompt_tokens") or 0)
        out["output_tokens"] += int(data.get("output_tokens") or 0)
        usd = data.get("usd_actual")
        if isinstance(usd, (int, float)):
            out["usd"] += float(usd)
        else:
            out["usd_unknown_calls"] += 1
        out["served_models"].append(f"{data.get('provider_id')}|{data.get('model_id')}")
    if calls:
        last = calls[-1]
        payload = last.get("payload") if isinstance(last.get("payload"), dict) else last
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        details = [d for d in list(data.get("turn_usage_details") or []) if isinstance(d, dict)]
        if details:
            cached = [d.get("cached_input_tokens") for d in details]
            written = [d.get("cache_write_input_tokens") for d in details]
            seconds = [d.get("request_seconds") for d in details]
            if all(isinstance(v, int) for v in cached):
                out["cached_tokens"] = sum(cached)
            if all(isinstance(v, int) for v in written):
                out["cache_write_tokens"] = sum(written)
            if all(isinstance(v, (int, float)) for v in seconds):
                out["request_seconds"] = round(sum(seconds), 3)
    out["off_model_calls"] = sum(1 for m in out["served_models"] if not m.endswith("|" + model))
    out["usd"] = round(out["usd"], 8)
    return out


def _events_after(client: Any, session_id: str, after_seq: int) -> list[dict[str, Any]]:
    """Every runtime event of the session after ``after_seq``, paged."""
    out: list[dict[str, Any]] = []
    cursor = after_seq
    for _ in range(50):
        exchange = client.get(f"/api/runtime/events?session={session_id}&after={cursor}&limit=500", timeout_s=30.0)
        payload = exchange.response_json() if not exchange.error else None
        batch = list((payload or {}).get("events") or []) if isinstance(payload, dict) else []
        if not batch:
            break
        out.extend(batch)
        cursor = max(int(e.get("seq") or 0) for e in batch)
        if len(batch) < 500:
            break
    return out


def cmd_vool(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO))
    from ops.served_reality.appstage import stage_app
    from ops.served_reality.bench import discover_python
    from ops.served_reality.daemon import launch_daemon
    from ops.served_reality.wire import WireClient, WireLog

    root = Path(args.run_root).resolve()
    arm_dir = root / args.label
    arm_dir.mkdir(parents=True, exist_ok=True)
    ledger = SpendLedger(root, args.cap_usd, count_notional=not args.cap_excludes_notional)
    results_path = arm_dir / "results.jsonl"
    done = _done_keys(_read_jsonl(results_path))
    tasks = load_tasks(args.tasks)
    todo = [(t, r) for t in tasks for r in range(args.repeats) if (t["id"], r) not in done]
    if not todo:
        print(json.dumps({"label": args.label, "status": "nothing_to_do"}))
        return EXIT_OK
    ledger.check(f"{args.label} boot")
    key = resolve_key(args.key_source)

    attempt_dir = arm_dir / f"attempt-{int(_now())}"
    attempt_dir.mkdir(parents=True)
    staged = stage_app(REPO, args.sha, attempt_dir)
    home = attempt_dir / "home"
    (attempt_dir / "user-home").mkdir(parents=True, exist_ok=True)
    remaining = max(0.01, ledger.cap_usd - ledger.counted_usd)
    _write_home_policy(staged.path, home, per_call=args.per_call_usd, per_task=args.per_task_usd, daily=remaining)
    env = {
        "OPENROUTER_API_KEY": key,
        "OPENROUTER_MODEL": args.model,
        "VOOL_LOCAL_MODELS_ENABLED": "0",
        "VOOL_INTENT_ARBITER": "0",
        "VOOL_DEBUG_PROMPT": "1",
        # A throwaway HOME too, so nothing in the operator's own home is read or written.
        "HOME": str(attempt_dir / "user-home"),
        "VOOL_PROMPT_CACHE_BREAKPOINTS": "1" if args.cache != "off" else "0",
    }
    if args.cache != "off":
        env["VOOL_PROMPT_CACHE_PLACEMENT"] = args.cache
    wire_log = WireLog(attempt_dir, "daemon")
    usage_before = key_usage_usd(key)
    manifest = {
        "label": args.label,
        "arm": "vool",
        "model": args.model,
        "cache": args.cache,
        "mode": args.mode,
        "sha": staged.sha_full,
        "archive_sha256": staged.archive_sha256,
        "repeats": args.repeats,
        "tasks": [t["id"] for t in tasks],
        "started_at": _now(),
        "key_usage_before_usd": usage_before,
        "caps": {"per_call": args.per_call_usd, "per_task": args.per_task_usd, "daily": remaining},
    }
    _write_json(attempt_dir / "manifest.json", manifest)
    daemon = launch_daemon(
        app_dir=staged.path,
        home=home,
        workspace=attempt_dir / "workspace-root",
        python_bin=args.python or discover_python(),
        run_dir=attempt_dir,
        wire_log=wire_log,
        expected_sha=staged.sha,
        extra_env=env,
        boot_timeout_s=240.0,
    )
    client = WireClient(f"http://127.0.0.1:{daemon.port}", wire_log, default_timeout_s=TURN_TIMEOUT_S)
    status = "complete"
    exit_code = EXIT_OK
    any_success, failed_chats = bool(done), 0
    try:
        for task, repeat in todo:
            chat_id = f"pcb-{args.label}-{task['id']}-r{repeat}-{uuid.uuid4().hex[:6]}"
            workspace = _fresh_workspace(attempt_dir / "ws" / chat_id)
            session_id = ""
            last_seq = 0
            failure = ""
            for index, turn in enumerate(task["turns"]):
                ledger.check(f"{args.label} {task['id']} r{repeat} t{index}")
                turn_id = f"t{index}-{uuid.uuid4().hex[:8]}"
                started = time.monotonic()
                exchange = client.post_chat(
                    turn["text"],
                    chat_id=chat_id,
                    turn_id=turn_id,
                    workspace=str(workspace),
                    extra_body={
                        "model": f"openrouter-byok:{args.model}",
                        "model_selection": "pin",
                        "mode": args.mode,
                        "stream": False,
                    },
                    timeout_s=TURN_TIMEOUT_S,
                )
                wall = round(time.monotonic() - started, 3)
                payload = exchange.response_json() if not exchange.error else None
                payload = payload if isinstance(payload, dict) else {}
                message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                answer = str(message.get("content") or "")
                session_id = str(payload.get("vool_session_id") or session_id)
                events = _events_after(client, session_id, last_seq) if session_id else []
                usage = _turn_usage(events, 0, args.model)
                last_seq = max([last_seq] + [int(e.get("seq") or 0) for e in events])
                ledger.record(billed=usage["usd"], notional=None, unknown=bool(usage["usd_unknown_calls"]))
                row = {
                    "kind": "turn",
                    "arm": "vool",
                    "label": args.label,
                    "model": args.model,
                    "cache": args.cache,
                    "sha": staged.sha_full,
                    "attempt": attempt_dir.name,
                    "task": task["id"],
                    "repeat": repeat,
                    "turn": index,
                    "chat_id": chat_id,
                    "http_status": exchange.status,
                    "transport_error": exchange.error or None,
                    "wall_seconds": wall,
                    "answer": answer,
                    **usage,
                }
                _append_jsonl(results_path, row)
                print(
                    f"{args.label} {task['id']} r{repeat} t{index}: {wall}s "
                    f"in={usage['prompt_tokens']} cached={usage['cached_tokens']} "
                    f"write={usage['cache_write_tokens']} out={usage['output_tokens']} "
                    f"${usage['usd']:.5f} calls={usage['calls']} status={exchange.status} | {_spend_line(ledger, results_path)}",
                    flush=True,
                )
                if exchange.error or exchange.status != 200 or not answer:
                    reason = exchange.error or (f"http {exchange.status}" if exchange.status != 200 else "empty answer")
                    failure = f"turn {index}: {str(reason)[:300]}"
                    _append_jsonl(results_path, {"kind": "turn_failed", "task": task["id"], "repeat": repeat, "turn": index, "attempt": attempt_dir.name, "reason": failure})
                    break  # later turns build on this one; the chat is re-run whole on resume
            ok = _close_chat(results_path, task["id"], repeat, attempt_dir.name, failure, any_success=any_success)
            any_success, failed_chats = any_success or ok, failed_chats + (not ok)
        if failed_chats:
            status, exit_code = f"incomplete: {failed_chats} chat(s) failed", EXIT_INCOMPLETE
    except CapReached as stop:
        status, exit_code = f"cap_stop: {stop}", EXIT_CAP
        print(status, flush=True)
    except ArmAborted as stop:
        status, exit_code = f"aborted: {stop}", EXIT_HARNESS
        print(status, file=sys.stderr, flush=True)
    except Exception as exc:
        # A crash is never recorded as "complete": the manifest says what broke and the exit is non-zero.
        status, exit_code = f"crashed: {type(exc).__name__}: {exc}", EXIT_HARNESS
        print(status, file=sys.stderr, flush=True)
    finally:
        with contextlib.suppress(Exception):
            usage_report = client.get("/api/runtime/usage?range=today&per_model=1", timeout_s=20.0).response_json()
            _write_json(attempt_dir / "vool_usage_summary.json", usage_report)
        daemon.terminate()
        wire_log.close()
        manifest.update(
            {
                "finished_at": _now(),
                "status": status,
                "key_usage_after_usd": key_usage_usd(key),
                "ledger": _read_json(root / "spend.json", {}),
            }
        )
        prompt_log = home / "logs" / "prompt_debug.jsonl"
        if prompt_log.exists():
            shutil.copy2(prompt_log, attempt_dir / "prompt_debug.jsonl")
        _write_json(attempt_dir / "manifest.json", manifest)
        _scrub_key_from_logs(attempt_dir, key)
        if args.clean_scratch:
            manifest["scratch_removed"] = _remove_scratch(attempt_dir, root)
            _write_json(attempt_dir / "manifest.json", manifest)
    return exit_code


def _scrub_key_from_logs(directory: Path, key: str) -> None:
    """Belt and braces: no file the run leaves behind may carry the key's bytes."""
    if not key:
        return
    needle = key.encode("utf-8")
    for path in directory.rglob("*"):
        if "app" in path.relative_to(directory).parts[:1]:
            continue  # the staged product: read-only, written before any key existed
        if path.is_file() and path.stat().st_size < 256 * 1024 * 1024 and not path.is_symlink():
            with contextlib.suppress(OSError):
                data = path.read_bytes()
                if needle in data:
                    path.write_bytes(data.replace(needle, b"[REDACTED-KEY]"))


# --------------------------------------------------------------------------------------------
# Claude Code CLI arm


def cmd_cli(args: argparse.Namespace) -> int:
    root = Path(args.run_root).resolve()
    arm_dir = root / args.label
    arm_dir.mkdir(parents=True, exist_ok=True)
    ledger = SpendLedger(root, args.cap_usd, count_notional=not args.cap_excludes_notional)
    results_path = arm_dir / "results.jsonl"
    done = _done_keys(_read_jsonl(results_path))
    tasks = load_tasks(args.tasks)
    claude = shutil.which(args.claude_bin)
    if not claude:
        raise HarnessError(f"Claude Code CLI not found: {args.claude_bin}")
    version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    attempt = f"attempt-{int(_now())}"
    env = dict(os.environ)
    if args.route == "openrouter":
        key = resolve_key(args.key_source)
        env.update({"ANTHROPIC_BASE_URL": "https://openrouter.ai/api", "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_API_KEY": ""})
    _write_json(
        arm_dir / f"{attempt}-manifest.json",
        {"label": args.label, "arm": "cli", "model": args.cli_model, "route": args.route, "claude_version": version, "started_at": _now()},
    )
    status, exit_code = "complete", EXIT_OK
    any_success, failed_chats = bool(done), 0
    try:
        for task in tasks:
            for repeat in range(args.repeats):
                if (task["id"], repeat) in done:
                    continue
                workspace = _fresh_workspace(arm_dir / "ws" / f"{task['id']}-r{repeat}-{uuid.uuid4().hex[:6]}")
                session_id = ""
                failure = ""
                for index, turn in enumerate(task["turns"]):
                    ledger.check(f"{args.label} {task['id']} r{repeat} t{index}")
                    command = [
                        claude, "-p", turn["text"], "--output-format", "json", "--model", args.cli_model,
                        "--allowedTools", "Read", "Glob", "Grep",
                        # No MCP servers from the operator's settings: the arm uses the CLI's own tools.
                        "--strict-mcp-config", "--mcp-config", '{"mcpServers": {}}',
                    ]
                    if session_id:
                        command += ["--resume", session_id]
                    started = time.monotonic()
                    proc = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, timeout=TURN_TIMEOUT_S)
                    wall = round(time.monotonic() - started, 3)
                    data: dict[str, Any] = {}
                    with contextlib.suppress(ValueError):
                        parsed = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
                        data = parsed if isinstance(parsed, dict) else {}
                    session_id = str(data.get("session_id") or session_id)
                    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                    cost = data.get("total_cost_usd")
                    cost = float(cost) if isinstance(cost, (int, float)) else None
                    billed = cost if args.route == "openrouter" else None
                    notional = cost if args.route == "login" else None
                    ledger.record(billed=billed, notional=notional, unknown=cost is None)
                    input_tokens = int(usage.get("input_tokens") or 0)
                    cached = usage.get("cache_read_input_tokens")
                    written = usage.get("cache_creation_input_tokens")
                    row = {
                        "kind": "turn",
                        "arm": "cli",
                        "label": args.label,
                        "model": args.cli_model,
                        "route": args.route,
                        "claude_version": version,
                        "attempt": attempt,
                        "task": task["id"],
                        "repeat": repeat,
                        "turn": index,
                        "exit_code": proc.returncode,
                        "is_error": bool(data.get("is_error")),
                        "wall_seconds": wall,
                        "duration_api_ms": data.get("duration_api_ms"),
                        "num_turns": data.get("num_turns"),
                        "answer": str(data.get("result") or ""),
                        # Anthropic's input_tokens excludes cache reads and writes; the comparable
                        # "prompt tokens" figure is the sum of all three.
                        "prompt_tokens": input_tokens + int(cached or 0) + int(written or 0),
                        "cached_tokens": int(cached) if isinstance(cached, int) else None,
                        "cache_write_tokens": int(written) if isinstance(written, int) else None,
                        "output_tokens": int(usage.get("output_tokens") or 0),
                        "usd": round(cost, 8) if cost is not None else None,
                        "usd_kind": "billed_openrouter" if args.route == "openrouter" else "notional_login",
                        "model_usage": data.get("modelUsage"),
                        "stderr_tail": proc.stderr[-600:] if proc.returncode else "",
                    }
                    _append_jsonl(results_path, row)
                    print(
                        f"{args.label} {task['id']} r{repeat} t{index}: {wall}s in={row['prompt_tokens']} "
                        f"cached={row['cached_tokens']} write={row['cache_write_tokens']} out={row['output_tokens']} "
                        f"${cost if cost is not None else float('nan'):.5f} exit={proc.returncode} | {_spend_line(ledger, results_path)}",
                        flush=True,
                    )
                    if proc.returncode or row["is_error"] or not row["answer"].strip():
                        reason = row["answer"].strip() or row["stderr_tail"].strip() or (f"exit {proc.returncode}" if proc.returncode else "empty answer")
                        failure = f"turn {index}: {reason[:300]}"
                        _append_jsonl(results_path, {"kind": "turn_failed", "task": task["id"], "repeat": repeat, "turn": index, "attempt": attempt, "reason": failure})
                        break  # later turns resume this session; the chat is re-run whole on resume
                ok = _close_chat(results_path, task["id"], repeat, attempt, failure, any_success=any_success)
                any_success, failed_chats = any_success or ok, failed_chats + (not ok)
        if failed_chats:
            status, exit_code = f"incomplete: {failed_chats} chat(s) failed", EXIT_INCOMPLETE
    except CapReached as stop:
        status, exit_code = f"cap_stop: {stop}", EXIT_CAP
        print(status, flush=True)
    except ArmAborted as stop:
        status, exit_code = f"aborted: {stop}", EXIT_HARNESS
        print(status, file=sys.stderr, flush=True)
    except Exception as exc:
        # A crash is never recorded as "complete": the manifest says what broke and the exit is non-zero.
        status, exit_code = f"crashed: {type(exc).__name__}: {exc}", EXIT_HARNESS
        print(status, file=sys.stderr, flush=True)
    finally:
        _write_json(arm_dir / f"{attempt}-status.json", {"status": status, "finished_at": _now(), "ledger": _read_json(root / "spend.json", {})})
        if args.route == "openrouter":
            _scrub_key_from_logs(arm_dir, env.get("ANTHROPIC_AUTH_TOKEN", ""))
        if args.clean_scratch and (arm_dir / "ws").exists():
            if root.resolve() not in (arm_dir / "ws").resolve().parents:
                raise HarnessError("refusing to clean a workspace outside the run root")
            shutil.rmtree(arm_dir / "ws")
    return exit_code


# --------------------------------------------------------------------------------------------
# blind grading


JUDGE_PROMPT = """You grade one answer from a chat assistant. You do not know which assistant wrote it.

Question (turn {turn} of a conversation):
{question}

Reference answer (the facts that matter):
{reference}

Assistant's answer:
{answer}

Score it: 2 = correct and complete on the reference facts, 1 = partly correct or missing a needed fact, 0 = wrong, refuses, or does not answer. Judge facts, not style or length.
Reply with JSON only: {{"score": 0|1|2, "reason": "<one short sentence>"}}"""


def _final_rows(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every turn row from attempts whose (task, repeat) completed, one per (label, task, repeat, turn)."""
    out: list[tuple[Path, dict[str, Any]]] = []
    for results in sorted(root.glob("*/results.jsonl")):
        rows = _read_jsonl(results)
        complete_attempt = {(r["task"], int(r["repeat"])): r["attempt"] for r in rows if r.get("kind") == "chat_complete"}
        for row in rows:
            if row.get("kind") != "turn":
                continue
            if complete_attempt.get((row["task"], int(row["repeat"]))) == row.get("attempt"):
                out.append((results, row))
    return out


def cmd_grade(args: argparse.Namespace) -> int:
    root = Path(args.run_root).resolve()
    ledger = SpendLedger(root, args.cap_usd, count_notional=not args.cap_excludes_notional)
    key = resolve_key(args.key_source)
    tasks = {t["id"]: t for t in load_tasks()}
    grades_path = root / "grades.jsonl"
    graded = {(g["label"], g["task"], g["repeat"], g["turn"]) for g in _read_jsonl(grades_path)}
    pending = [row for _, row in _final_rows(root) if (row["label"], row["task"], row["repeat"], row["turn"]) not in graded]
    random.Random(7).shuffle(pending)  # the judge never sees answers grouped by arm
    status, exit_code = "complete", EXIT_OK
    try:
        for row in pending:
            turn = tasks[row["task"]]["turns"][int(row["turn"])]
            expect = [str(item) for item in turn.get("expect") or []]
            answer = str(row.get("answer") or "")
            keyword_hits = sum(1 for item in expect if item.lower() in answer.lower())
            ledger.check(f"grade {row['label']} {row['task']} r{row['repeat']} t{row['turn']}")
            prompt = JUDGE_PROMPT.format(
                turn=int(row["turn"]) + 1,
                question=turn["text"][:1500],
                reference=turn["reference"],
                answer=answer[:6000] or "(no answer)",
            )
            body = {
                "model": args.judge_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 200,
                "usage": {"include": True},
            }
            score: int | None = None
            reason = ""
            cost: float | None = None
            try:
                data = _http_json(f"{OPENROUTER}/chat/completions", key=key, body=body, timeout=120.0)
                usage = data.get("usage") or {}
                cost = float(usage["cost"]) if isinstance(usage.get("cost"), (int, float)) else None
                text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                start, end = text.find("{"), text.rfind("}")
                verdict = json.loads(text[start : end + 1]) if start >= 0 and end > start else {}
                if verdict.get("score") in (0, 1, 2):
                    score, reason = int(verdict["score"]), str(verdict.get("reason") or "")[:300]
                else:
                    reason = "judge reply unparseable"
            except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as exc:
                reason = f"judge call failed: {type(exc).__name__}"
            ledger.record(billed=cost, notional=None, unknown=cost is None)
            _append_jsonl(
                grades_path,
                {
                    "label": row["label"], "task": row["task"], "repeat": row["repeat"], "turn": row["turn"],
                    "judge_model": args.judge_model, "score": score, "reason": reason,
                    "keyword_expected": len(expect), "keyword_hits": keyword_hits, "judge_usd": cost,
                },
            )
    except CapReached as stop:
        status, exit_code = f"cap_stop: {stop}", EXIT_CAP
    print(json.dumps({"status": status, "graded_now": len(pending), "ledger": _read_json(root / "spend.json", {})}))
    return exit_code


# --------------------------------------------------------------------------------------------
# report


def _sum(values: list[Any]) -> float | None:
    """Sum when every value is known; None (unknown) as soon as one is missing."""
    if not values or any(not isinstance(v, (int, float)) for v in values):
        return None
    return float(sum(values))


def summarize(root: Path) -> dict[str, Any]:
    grades = {(g["label"], g["task"], g["repeat"], g["turn"]): g for g in _read_jsonl(root / "grades.jsonl")}
    by_label: dict[str, list[dict[str, Any]]] = {}
    for _, row in _final_rows(root):
        by_label.setdefault(row["label"], []).append(row)
    summary: dict[str, Any] = {"labels": {}, "ledger": _read_json(root / "spend.json", {})}
    for label, rows in sorted(by_label.items()):
        chats = sorted({(r["task"], r["repeat"]) for r in rows})
        scores = [grades.get((r["label"], r["task"], r["repeat"], r["turn"]), {}).get("score") for r in rows]
        known_scores = [s for s in scores if isinstance(s, int)]
        prompt = _sum([r.get("prompt_tokens") for r in rows])
        cached = _sum([r.get("cached_tokens") for r in rows])
        output = _sum([r.get("output_tokens") for r in rows])
        usd = _sum([r.get("usd") for r in rows])
        wall = _sum([r.get("wall_seconds") for r in rows])
        failed = sum(1 for r in rows if not str(r.get("answer") or "").strip() or r.get("transport_error") or r.get("is_error"))
        summary["labels"][label] = {
            "arm": rows[0].get("arm"),
            "model": rows[0].get("model"),
            "cache": rows[0].get("cache", "native"),
            "route": rows[0].get("route", "openrouter"),
            "usd_kind": rows[0].get("usd_kind", "billed_openrouter"),
            "chats": len(chats),
            "turns": len(rows),
            "failed_turns": failed,
            "wall_seconds_total": wall,
            "wall_seconds_median_turn": sorted(r["wall_seconds"] for r in rows)[len(rows) // 2] if rows else None,
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "cache_write_tokens": _sum([r.get("cache_write_tokens") for r in rows]),
            "cached_share": round(cached / prompt, 4) if cached is not None and prompt else None,
            "output_tokens": output,
            "output_tokens_per_second": round(output / wall, 2) if output is not None and wall else None,
            "usd_total": round(usd, 6) if usd is not None else None,
            "usd_unknown_turns": sum(1 for r in rows if not isinstance(r.get("usd"), (int, float)) or r.get("usd_unknown_calls")),
            "usd_per_turn": round(usd / len(rows), 6) if usd is not None and rows else None,
            "calls": _sum([r.get("calls") for r in rows]) if rows[0].get("arm") == "vool" else None,
            "off_model_calls": _sum([r.get("off_model_calls") for r in rows]) if rows[0].get("arm") == "vool" else None,
            "quality_points": sum(known_scores),
            "quality_max": 2 * len(known_scores),
            "quality_ungraded": len(scores) - len(known_scores),
        }
    # What the table leaves out: chats that failed or never finished still spent money and time.
    summary["not_counted"] = {}
    for results in sorted(root.glob("*/results.jsonl")):
        rows = _read_jsonl(results)
        counted = {(r["task"], int(r["repeat"]), r["attempt"]) for r in rows if r.get("kind") == "chat_complete"}
        dropped = [r for r in rows if r.get("kind") == "turn" and (r["task"], int(r["repeat"]), r.get("attempt")) not in counted]
        failed = [r for r in rows if r.get("kind") == "chat_failed"]
        if dropped or failed:
            summary["not_counted"][results.parent.name] = {
                "turns": len(dropped),
                "failed_chats": len(failed),
                "usd": _sum([r.get("usd") for r in dropped]),
                "reasons": sorted({str(r.get("reason") or "") for r in failed})[:5],
            }
    return summary


def cmd_report(args: argparse.Namespace) -> int:
    root = Path(args.run_root).resolve()
    if args.cap_usd is not None and not (root / "spend.json").exists():
        SpendLedger(root, args.cap_usd).save()  # a run that never paid still says so
    summary = summarize(root)
    _write_json(root / "summary.json", summary)
    lines = [
        "# Prompt-cache bench report",
        "",
        f"Run root: `{root.name}`. Money counted against the cap: billed ${summary['ledger'].get('billed_usd', 0):.4f}, "
        f"notional (Claude login, list price) ${summary['ledger'].get('notional_usd', 0):.4f}, "
        f"cap ${summary['ledger'].get('cap_usd', 0):.2f}.",
        "",
        "| Label | Arm | Model | Cache | Turns (failed) | Wall s total | Median turn s | Prompt tok | Cached tok | Cached share | Cache-write tok | Output tok | Out tok/s | USD total | USD/turn | Quality |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    def fmt(value: Any) -> str:
        return "unknown" if value is None else (f"{value:,.4f}" if isinstance(value, float) and value < 10 else f"{value:,}")

    for label, row in summary["labels"].items():
        quality = f"{row['quality_points']}/{row['quality_max']}" + (f" ({row['quality_ungraded']} ungraded)" if row["quality_ungraded"] else "")
        lines.append(
            f"| {label} | {row['arm']} | {row['model']} | {row['cache']} | {row['turns']} ({row['failed_turns']}) | "
            f"{fmt(row['wall_seconds_total'])} | {fmt(row['wall_seconds_median_turn'])} | {fmt(row['prompt_tokens'])} | "
            f"{fmt(row['cached_tokens'])} | {fmt(row['cached_share'])} | {fmt(row['cache_write_tokens'])} | "
            f"{fmt(row['output_tokens'])} | {fmt(row['output_tokens_per_second'])} | {fmt(row['usd_total'])} "
            f"({row['usd_kind']}) | {fmt(row['usd_per_turn'])} | {quality} |"
        )
    if summary["not_counted"]:
        lines += ["", "Not counted above (failed or unfinished chats; their spend is in the totals):", ""]
        for label, row in summary["not_counted"].items():
            reasons = "; ".join(row["reasons"]) or "unfinished"
            lines.append(f"- {label}: {row['turns']} turns, {row['failed_chats']} failed chats, USD {fmt(row['usd'])}. {reasons}")
    lines += [
        "",
        "Notes: USD for VOOL rows is what OpenRouter reported per call (usage.cost). USD for a Claude CLI row on a",
        "login is Claude Code's own list-price figure, not a charge. Unknown means a provider reported nothing.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return EXIT_OK


# --------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ops.prompt_cache_bench.bench")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-root", required=True, help="directory outside the repo for all results")
        p.add_argument("--cap-usd", type=float, required=True, help="hard cap for everything under the run root")
        p.add_argument("--cap-excludes-notional", action="store_true", help="do not count Claude-login list-price figures")
        p.add_argument("--key-source", choices=("env", "vool-store"), default="env")
        p.add_argument("--tasks", default="", help="comma list of task ids (default all)")
        p.add_argument("--repeats", type=int, default=1)

    pre = sub.add_parser("preflight")
    pre.add_argument("--models", required=True)
    pre.add_argument("--search", default="")
    pre.set_defaults(func=cmd_preflight)

    vool = sub.add_parser("vool")
    common(vool)
    vool.add_argument("--label", required=True)
    vool.add_argument("--sha", required=True)
    vool.add_argument("--model", required=True, help="OpenRouter model id, e.g. anthropic/claude-sonnet-4.5")
    vool.add_argument("--cache", choices=("off", "system", "system_history"), default="off")
    vool.add_argument("--per-call-usd", type=float, default=0.60)
    vool.add_argument("--per-task-usd", type=float, default=2.00)
    vool.add_argument("--python", default="")
    vool.add_argument("--clean-scratch", action="store_true", help="remove the staged app, profile and workspaces after the run")
    vool.add_argument("--mode", default="auto", help="VOOL operating mode for every turn (isolated workspace)")
    vool.set_defaults(func=cmd_vool)

    cli = sub.add_parser("cli")
    common(cli)
    cli.add_argument("--label", required=True)
    cli.add_argument("--cli-model", required=True, help="Claude Code model name, e.g. claude-sonnet-4-5")
    cli.add_argument("--route", choices=("login", "openrouter"), default="login")
    cli.add_argument("--claude-bin", default="claude")
    cli.add_argument("--clean-scratch", action="store_true", help="remove the task workspaces after the run")
    cli.set_defaults(func=cmd_cli)

    grade = sub.add_parser("grade")
    common(grade)
    grade.add_argument("--judge-model", default="openai/gpt-4.1-mini")
    grade.set_defaults(func=cmd_grade)

    report = sub.add_parser("report")
    report.add_argument("--run-root", required=True)
    report.add_argument("--cap-usd", type=float, default=None)
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HarnessError as exc:
        print(f"harness error: {exc}", file=sys.stderr)
        return EXIT_HARNESS


if __name__ == "__main__":
    raise SystemExit(main())
