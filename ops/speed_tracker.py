"""Per-release local-speed tracker: model-free, in-repo, red on regression.

Drives a fixed turn set through the REAL daemon (apps/vool_api_server.py) against the in-repo
recording engine (ops/local_speed_engine.py, no model), scores the run from the raw logs the
same way an engine sees it (prompt bytes rendered from the messages; serial calls before the
first answer text; uncached bytes past the longest common prefix with the previous call on the
same warm runner; wall time; models touched; runner reloads), stores one JSON per release under
docs/benchmarks/speed/, and FAILS when any turn type is more than 10% heavier or slower than the
last stored release (or reloads grew).

With --engine-url <url> (opt-in, never the default) the set is driven against a REAL local
engine the person runs, and the per-turn final frames' own prompt_eval / eval counts and
durations are recorded as tokens-per-second alongside the model-free numbers. Nothing outbound:
loopback only.

Usage:
    python ops/speed_tracker.py [--store] [--engine-url URL] [--out DIR] [--keep-dir DIR]
Exit codes: 0 green (or --store recorded), 1 regression (red), 2 infrastructure failure.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ops"))

#: The fixed, model-free turn set. Synthetic texts only; nothing here is derived from any
#: evaluation set. Types mirror the shapes whose speed this repo tracks.
TURN_SET: tuple[dict, ...] = (
    {"type": "greet", "text": "hello there"},
    {"type": "quick", "text": "Which planet has the most moons?"},
    {"type": "quick", "text": "How many centimetres are in a metre?"},
    {"type": "followup", "text": "and the second most?"},
    {"type": "tool", "text": "List the files in my workspace."},
    {"type": "long", "text": (
        "Summarise in two bullets:\n\nThe library committee met on Tuesday to review the "
        "community room booking policy. The room has been double-booked four times this year, "
        "twice by the chess club and twice by the evening language classes. The librarian "
        "proposed an online calendar with a two-hour minimum slot. The chess club asked for a "
        "standing weekly slot, and the language classes offered to move to the smaller reading "
        "room on Thursdays. The committee agreed to trial the online calendar for three months "
        "and review the standing-slot request after the trial."
    )},
    {"type": "heavy", "text": (
        "Write a short Python function that counts the words in a sentence, with one example."
    )},
    {"type": "followup", "text": "now make it ignore punctuation"},
)

INSTALLED_MODELS = "qwen3:8b,qwen3:4b,nomic-embed-text:latest"
SETTLE_SECONDS = 2.0
CHAT_PATHS = ("/api/chat", "/api/generate", "/v1/chat/completions")
TOLERANCE = 1.10


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _rendered_prompt(body: dict) -> bytes:
    """The prompt as an engine renders it: messages in order, tool list with the first system."""
    parts: list[str] = []
    tools = body.get("tools")
    tools_text = json.dumps(tools, ensure_ascii=False) if tools else ""
    placed = False
    for message in body.get("messages") or []:
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        parts.append(f"<{message.get('role')}>\n{content}")
        if message.get("tool_calls"):
            parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
        if message.get("role") == "system" and tools_text and not placed:
            parts.append(tools_text)
            placed = True
    if tools_text and not placed:
        parts.insert(0, tools_text)
    return "\n".join(parts).encode("utf-8")


def _is_prewarm(body: dict) -> bool:
    messages = body.get("messages") or []
    options = body.get("options") or {}
    return (
        options.get("num_predict") == 1
        and len(messages) <= 1
        and not str((messages or [{}])[0].get("content") or "").strip()
    )


def score_run(run_dir: Path) -> dict:
    """Per-turn-type medians from the two raw logs alone."""
    engine = [json.loads(line) for line in (run_dir / "engine.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    turns = [json.loads(line) for line in (run_dir / "turns.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    turn_rows = [row for row in turns if row.get("kind") == "turn"]
    calls = [row for row in engine
             if row.get("method") == "POST" and str(row.get("path", "")).startswith(CHAT_PATHS)]
    last_prompt: dict[str, bytes] = {}
    enriched = []
    for call in sorted(calls, key=lambda row: row["t"]):
        body = call.get("body") or {}
        rendered = _rendered_prompt(body)
        prompt = len(rendered)
        key = json.dumps(call.get("runner_key"))
        previous = last_prompt.get(key)
        shared = 0
        if call.get("residency") == "warm" and previous is not None:
            for left, right in zip(previous, rendered, strict=False):
                if left != right:
                    break
                shared += 1
        last_prompt[key] = rendered
        enriched.append({**call, "_prompt": prompt, "_uncached": prompt - shared,
                         "_prewarm": _is_prewarm(body), "_probe": bool(call.get("probe"))})
    per_turn = []
    for index, turn in enumerate(turn_rows):
        end = turn_rows[index + 1]["t_sent"] if index + 1 < len(turn_rows) else float("inf")
        mine = [call for call in enriched
                if turn["t_sent"] <= call["t"] < end and not call["_probe"]]
        work = [call for call in mine if not call["_prewarm"]]
        first = turn.get("t_first_text") or turn.get("t_done")
        pre = [call for call in work if call["t"] < first]
        per_turn.append({
            "type": turn["type"],
            "prompt_bytes": sum(call["_prompt"] for call in work),
            "serial_calls": len(pre),
            "uncached_bytes": sum(call["_uncached"] for call in pre),
            "vool_s": round(max(0.0, first - turn["t_sent"]), 3),
            "models": sorted({str((call.get("body") or {}).get("model")) for call in work}),
            "reloads": len([call for call in work if call.get("residency") == "reload"]),
            "real_engine": turn.get("real_engine"),
        })
    by_type: dict[str, dict] = {}
    for kind in sorted({row["type"] for row in per_turn}):
        rows = [row for row in per_turn if row["type"] == kind]
        entry: dict = {"n": len(rows)}
        for field in ("prompt_bytes", "serial_calls", "uncached_bytes", "vool_s"):
            values = [float(row[field]) for row in rows]
            entry[field + "_median"] = round(statistics.median(values), 3)
        entry["reloads"] = sum(int(row["reloads"]) for row in rows)
        entry["models"] = sorted({m for row in rows for m in row["models"]})
        reals = [row["real_engine"] for row in rows if row.get("real_engine")]
        if reals:
            entry["real_engine_tokens_per_s_median"] = round(statistics.median(
                [real["tokens_per_second"] for real in reals]), 3)
        by_type[kind] = entry
    return {"turns": by_type,
            "reloads_total": sum(row["reloads"] for row in per_turn),
            "per_turn": per_turn}


def drive(base_url: str, run_dir: Path, engine_url: str | None) -> None:
    """Send the fixed set as real streamed /api/chat turns, logging wall-clock marks."""
    with open(run_dir / "turns.jsonl", "a", encoding="utf-8") as out:
        for index, turn in enumerate(TURN_SET):
            out.write(json.dumps(_drive_turn(base_url, index, turn, engine_url), ensure_ascii=False) + "\n")
            out.flush()
            time.sleep(SETTLE_SECONDS)


def _drive_turn(base_url: str, index: int, turn: dict, engine_url: str | None) -> dict:
    """One streamed turn: the record of when it was sent, first answered and done."""
    body = json.dumps({"model": "vool", "stream": True, "session_id": "speed-tracker",
                       "messages": [{"role": "user", "content": turn["text"]}]}).encode()
    request = urllib.request.Request(base_url + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
    record = {"kind": "turn", "index": index, "type": turn["type"], "t_sent": time.time()}
    answer = ""
    first = None
    final_frame: dict = {}
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            for line in response:
                line = line.strip()
                if not line:
                    continue
                now = time.time()
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(frame, dict):
                    continue
                piece = ((frame.get("message") or {}).get("content")
                         if isinstance(frame.get("message"), dict) else None)
                if piece:
                    if first is None:
                        first = now
                    answer += piece
                if frame.get("done"):
                    final_frame = frame
    except Exception as exc:  # a failed turn is data
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["t_first_text"] = first
    record["t_done"] = time.time()
    record["answer_head"] = answer[:80]
    if engine_url and final_frame.get("eval_count"):
        eval_ns = float(final_frame.get("eval_duration") or 0)
        record["real_engine"] = {
            "prompt_eval_count": final_frame.get("prompt_eval_count"),
            "eval_count": final_frame.get("eval_count"),
            "prompt_eval_duration_ms": round(float(final_frame.get("prompt_eval_duration") or 0) / 1e6, 3),
            "eval_duration_ms": round(eval_ns / 1e6, 3),
            "tokens_per_second": round(float(final_frame["eval_count"]) / (eval_ns / 1e9), 3)
            if eval_ns > 0 else None,
        }
    return record


def _wait_healthy(port: int, timeout_s: float = 180.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    return False


def _certify(port: int, run_dir: Path) -> None:
    for model in ("qwen3:8b", "qwen3:4b"):
        body = json.dumps({"provider_name": "ollama-local", "model_name": model}).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/model-tool-certification/run", data=body,
            headers={"Content-Type": "application/json"})
        with contextlib.suppress(Exception), urllib.request.urlopen(request, timeout=300) as response:
            response.read()
    (run_dir / "certified.marker").write_text("ok", encoding="utf-8")


def run_once(run_dir: Path, engine_url: str | None) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    daemon_port = _free_port()
    engine_port = _free_port()
    with open(run_dir / "engine.out", "w", encoding="utf-8") as engine_out, \
            open(run_dir / "daemon.out", "w", encoding="utf-8") as daemon_out:
        return _run_once(run_dir, engine_url, daemon_port, engine_port, engine_out, daemon_out)


def _run_once(run_dir: Path, engine_url: str | None, daemon_port: int, engine_port: int,
              engine_out, daemon_out) -> int:
    engine_proc = None
    if engine_url is None:
        engine_proc = subprocess.Popen(
            [sys.executable, "-I", str(REPO_ROOT / "ops" / "local_speed_engine.py"),
             str(engine_port), str(run_dir / "engine.jsonl")],
            stdout=engine_out, stderr=subprocess.STDOUT)
        target_engine = f"http://127.0.0.1:{engine_port}"
        time.sleep(1.0)
    else:
        target_engine = engine_url
        (run_dir / "engine.jsonl").write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "VOOL_HOME": str(run_dir / "home"),
        "VOOL_WORKSPACE_ROOT": str(run_dir / "ws"),
        "OLLAMA_HOST": target_engine,
        "VOOL_INSTALLED_OLLAMA_MODELS": INSTALLED_MODELS,
        # The daemon must import core/ from THIS repo whatever the caller's cwd or an
        # installed checkout says (same fence the frozen harness enforces).
        "PYTHONPATH": str(REPO_ROOT),
    })
    env.pop("VOOL_RAW_OLLAMA_API_URL", None)
    env.pop("VOOL_OLLAMA_URL", None)
    (run_dir / "home").mkdir(parents=True, exist_ok=True)
    (run_dir / "ws").mkdir(parents=True, exist_ok=True)
    daemon = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "apps" / "vool_api_server.py"), "--port", str(daemon_port)],
        env=env, cwd=str(REPO_ROOT),
        stdout=daemon_out, stderr=subprocess.STDOUT)
    try:
        if not _wait_healthy(daemon_port):
            print("speed tracker: daemon never became healthy", file=sys.stderr)
            return 2
        _certify(daemon_port, run_dir)
        time.sleep(5.0)
        drive(f"http://127.0.0.1:{daemon_port}", run_dir, engine_url)
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except subprocess.TimeoutExpired:
            daemon.kill()
        if engine_proc is not None:
            engine_proc.terminate()
            engine_proc.wait(timeout=10)
    return 0


def last_stored(bench_dir: Path) -> Path | None:
    if not bench_dir.is_dir():
        return None
    files = sorted(path for path in bench_dir.glob("speed-*.json") if path.is_file())
    return files[-1] if files else None


def compare(previous: dict, current: dict) -> list[str]:
    """Red lines: any turn type >10% heavier/slower, or reloads grown. Missing types are reported."""
    findings: list[str] = []
    old = previous.get("turns") or {}
    new = current.get("turns") or {}
    for kind in sorted(set(old) | set(new)):
        if kind not in old:
            findings.append(f"{kind}: new turn type (no baseline)")
            continue
        if kind not in new:
            findings.append(f"{kind}: vanished from the run (no measurement)")
            continue
        for field in ("prompt_bytes_median", "serial_calls_median", "uncached_bytes_median", "vool_s_median"):
            before = float(old[kind].get(field) or 0)
            after = float(new[kind].get(field) or 0)
            if before <= 0:
                continue
            if after > before * TOLERANCE:
                findings.append(
                    f"{kind}.{field}: {after} vs baseline {before} (+{round(100 * (after / before - 1))}%)")
        if int(new[kind].get("reloads") or 0) > int(old[kind].get("reloads") or 0):
            findings.append(f"{kind}.reloads: {new[kind]['reloads']} vs baseline {old[kind]['reloads']}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", action="store_true",
                        help="write this run as the new baseline release JSON")
    parser.add_argument("--engine-url", default=None,
                        help="opt-in: drive against a REAL local engine at this URL (loopback), "
                             "recording its own prompt_eval/eval timings; never the default")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "benchmarks" / "speed"),
                        help="directory holding the stored per-release JSONs")
    parser.add_argument("--keep-dir", default=None,
                        help="directory for the run's raw logs (default: a temp dir, removed)")
    args = parser.parse_args()

    if args.engine_url and not str(args.engine_url).startswith(("http://127.0.0.1", "http://localhost")):
        print("speed tracker: --engine-url must be loopback", file=sys.stderr)
        return 2
    import tempfile
    if args.keep_dir:
        run_dir = Path(args.keep_dir)
    else:
        run_dir = Path(tempfile.mkdtemp(prefix="vool-speed-"))
    code = run_once(run_dir, args.engine_url)
    if code:
        return code
    current = score_run(run_dir)
    try:
        import subprocess as _sp
        commit = _sp.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = ""
    current["commit"] = commit
    current["recorded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    current["engine"] = args.engine_url or "in-repo model-free"
    bench_dir = Path(args.out)
    previous_path = last_stored(bench_dir)
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path else None
    print(json.dumps(current["turns"], indent=1))
    if args.store or previous is None:
        bench_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
        target = bench_dir / f"speed-{stamp}.json"
        target.write_text(json.dumps(current, indent=1), encoding="utf-8")
        print(f"speed tracker: stored {target}")
        return 0
    findings = compare(previous, current)
    if findings:
        print("speed tracker: RED — regressions against", previous_path)
        for line in findings:
            print("  -", line)
        return 1
    print("speed tracker: green against", previous_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
