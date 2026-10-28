"""Live check of the Claude API lane, the way a person sets it up: one real key, a throwaway profile.

What it proves, against the real API and a real daemon on a temporary VOOL_HOME:

1. Settings > Models > Save key (the intake flow the page runs: begin, classify, verify, complete)
   stores the key and makes Claude the active provider.
2. A chat with each current model picked in the chat's model picker (Haiku 5.5, Sonnet 5.5,
   Opus 5.5; the paid confirmation answered once, as the person does) is answered by Claude.
3. A skill draft on Claude is written by the model (structured output), not from the person's words.
4. A key that only lives in ``ANTHROPIC_API_KEY`` (nothing saved) is enough.
5. Failure paths say what is wrong: a refused key is not stored, an unknown model and a bad key at
   chat time fail plainly instead of being answered by some other model, and a call that runs past
   its deadline ends as a timeout.
6. The adapter's own wire forms against the real API: a stream, a structured answer, a tool call.

The key is read from ``VOOL_LIVE_ANTHROPIC_KEY`` (never an argument, never printed). Spend is capped:
the run stops before a step once the profile's recorded spend plus the direct calls reach ``--cap``.
Every output is scanned for the key before the run ends.

    VOOL_LIVE_ANTHROPIC_KEY=... python ops/claude_api_live_check.py --repo . --out /path/to/run --cap 3
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
BAD_KEY = "sk-ant-api03-" + "x" * 86 + "AA"


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def profile_spend_usd(home: Path) -> float:
    total = 0.0
    for db in list(home.rglob("*.db")) + list(home.rglob("*.sqlite")) + list(home.rglob("*.sqlite3")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            try:
                row = con.execute("SELECT COALESCE(SUM(actual_usd),0) FROM model_spend_reservations").fetchone()
                total += float(row[0] or 0)
            finally:
                con.close()
        except Exception:
            continue
    return round(total, 6)


def reply_text(body) -> str:
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""
    message = body.get("message")
    if isinstance(message, dict) and message.get("content"):
        return str(message["content"])
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        for key in ("message", "delta"):
            part = (choices[0] or {}).get(key) or {}
            if isinstance(part, dict) and part.get("content"):
                return str(part["content"])
    for key in ("response", "reply", "text", "error", "detail"):
        if body.get(key):
            return str(body[key])
    return json.dumps(body)[:2000]


class Daemon:
    def __init__(self, repo: Path, python: str, root: Path, env_key: str = "") -> None:
        self.repo, self.python, self.root, self.env_key = repo, python, root, env_key
        self.home, self.fakehome = root / "home", root / "fakehome"
        self.port, self.proc = free_port(), None

    def start(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.fakehome.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("VOOL_", "NULLA_", "OPENAI", "ANTHROPIC", "OPENROUTER"))}
        env.update(
            {
                "HOME": str(self.fakehome),
                "VOOL_HOME": str(self.home),
                "VOOL_KEYCHAIN_ALLOWED": "0",
                "VOOL_DEBUG_PROMPT": "1",
                "VOOL_DAEMON_BIND_PORT": str(free_port()),
                "VOOL_DAEMON_HEALTH_PORT": "0",
                "PYTHONPATH": str(self.repo),
            }
        )
        if self.env_key:
            env["ANTHROPIC_API_KEY"] = self.env_key
        log = open(self.root / "daemon.log", "ab")  # noqa: SIM115 - the daemon writes to it until stop()
        cmd = [self.python, str(self.repo / "apps" / "vool_api_server.py"), "--port", str(self.port), "--bind", "127.0.0.1"]
        if sys.platform == "darwin":
            cmd = ["nice", "-n", "10", *cmd]
        self.proc = subprocess.Popen(cmd, cwd=str(self.repo), env=env, stdout=log, stderr=log, start_new_session=True)
        deadline = time.time() + 240
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"daemon exited early ({self.proc.returncode}); see {self.root}/daemon.log")
            status, _ = self.req("GET", "/api/skills", timeout=3)
            if status == 200:
                return
            time.sleep(1)
        raise SystemExit("daemon never became ready")

    def stop(self) -> None:
        if not self.proc:
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

    def req(self, method: str, path: str, payload=None, timeout: float = 60.0):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers={"content-type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                raw, status = resp.read().decode("utf-8", "replace"), resp.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read().decode("utf-8", "replace"), exc.code
        except Exception as exc:
            return 0, f"{type(exc).__name__}: {exc}"
        try:
            return status, json.loads(raw)
        except Exception:
            return status, raw

    def save_key(self, value: str) -> dict:
        """What Settings > Models > Save key does, step by step."""
        steps: dict = {}
        status, begun = self.req("POST", "/api/intake/begin", {})
        steps["begin"] = status
        sid = begun.get("session_id") if isinstance(begun, dict) else None
        status, classified = self.req("POST", "/api/intake/classify", {"session_id": sid, "value": value})
        steps["classify"] = [status, (classified or {}).get("suggestion") if isinstance(classified, dict) else classified]
        status, verified = self.req("POST", "/api/intake/verify", {"session_id": sid, "provider_id": "anthropic"}, timeout=60)
        steps["verify"] = [status, verified]
        if isinstance(verified, dict) and verified.get("outcome") == "verified":
            status, done = self.req("POST", "/api/intake/complete", {"session_id": sid})
            steps["complete"] = [status, done]
        return steps

    @staticmethod
    def session_id(label: str) -> str:
        """A chat id in the page's own shape (``openclaw:`` + 20 hex), stable per label."""
        import hashlib

        return "openclaw:" + hashlib.sha256(label.encode()).hexdigest()[:20]

    def pick_model(self, session: str, model: str) -> dict:
        """What the chat's model picker does: pin, and confirm once when the server says it is paid."""
        body = {"model": model, "session_id": session, "provider": "anthropic", "selection_revision": int(time.time() * 1000)}
        status, answer = self.req("POST", "/api/cloud/model", body)
        first = [status, (answer or {}).get("code") if isinstance(answer, dict) else answer]
        if status == 409 and isinstance(answer, dict) and answer.get("code") in {
            "paid_model_confirm_required", "MODEL_COST_UNKNOWN", "PAID_STATUS_UNKNOWN", "price_above_accepted"
        }:
            status, answer = self.req("POST", "/api/cloud/model", {**body, "confirm_paid": True})
        selected = ""
        if isinstance(answer, dict) and answer.get("ok"):
            provider = answer.get("provider") or ""
            selected = f"{provider}:{answer.get('model') or model}" if provider and provider != "openrouter" else str(answer.get("model") or model)
        return {"first": first, "final": [status, answer], "selected": selected}

    def chat(self, label: str, words: str, model: str | None) -> dict:
        session = self.session_id(f"{self.root.name}:{label}")
        body: dict = {"messages": [{"role": "user", "content": words}], "session_id": session, "stream": False}
        picked: dict = {}
        if model:
            picked = self.pick_model(session, model)
            body.update({"model": picked["selected"] or f"anthropic:{model}", "model_selection": "sticky"})
        t0 = time.time()
        status, reply = self.req("POST", "/api/chat", body, timeout=300)
        return {"pick": picked, "http": status, "seconds": round(time.time() - t0, 1), "reply": reply_text(reply)[:3000]}

    def models_called(self, since_line: int) -> list[str]:
        log = self.home / "logs" / "prompt_debug.jsonl"
        try:
            rows = log.read_text(encoding="utf-8", errors="replace").splitlines()[since_line:]
        except OSError:
            return []
        models = []
        for row in rows:
            try:
                item = json.loads(row)
            except ValueError:
                continue
            models.append(f"{item.get('provider_id') or item.get('lane') or '?'}|{item.get('model') or '?'}")
        return models

    def prompt_log_lines(self) -> int:
        try:
            return len((self.home / "logs" / "prompt_debug.jsonl").read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            return 0


class Run:
    def __init__(self, out: Path, cap: float) -> None:
        self.out, self.cap = out, cap
        self.rows: list[dict] = []
        self.direct_usd = 0.0
        self.daemons: list[Daemon] = []

    def spent(self) -> float:
        return round(self.direct_usd + sum(profile_spend_usd(d.home) for d in self.daemons), 6)

    def record(self, name: str, passed: bool, **facts) -> None:
        row = {"check": name, "pass": bool(passed), "at": utcnow(), "spend_usd_so_far": self.spent(), **facts}
        self.rows.append(row)
        with open(self.out / "results.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
        print(f"{'PASS' if passed else 'FAIL'} {name} (${row['spend_usd_so_far']})", flush=True)

    def room(self, name: str) -> bool:
        if self.spent() >= self.cap:
            self.record(name, False, skipped=f"spend cap ${self.cap} reached before this step")
            return False
        return True


def _claude_lane(model: str, key: str, **config):
    from adapters.openai_compatible_adapter import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter(
        SimpleNamespace(
            provider_name="anthropic-byok",
            provider_id=f"anthropic-byok:{model}",
            model_name=model,
            metadata={"runtime_family": "openai-compatible", "cost_class": "paid_cloud"},
            runtime_config={
                "base_url": "https://api.anthropic.com/v1",
                "api_path": "/messages",
                "protocol": "anthropic_messages",
                "supports_json_mode": True,  # as the registered lane (core.runtime_provider_defaults)
                "supports_json_schema": True,
                "timeout_seconds": 60.0,
                "headers": {"anthropic-version": "2023-06-01"},
                **config,
            },
        )
    )
    adapter._dispatch_credential = lambda _slot: key  # type: ignore[method-assign]
    return adapter


def _usd(model: str, usage: dict) -> float:
    from core.model_pricing import estimate_call_usd

    return float(
        estimate_call_usd(
            provider_id=f"anthropic-byok:{model}",
            model_id=model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        ).usd
    )


def direct_checks(run: Run, key: str) -> None:
    from adapters.base_adapter import ModelRequest
    from core.cloud_tool_call_contract import build_cloud_tool_definitions
    from core.model_output_contracts import json_schema_for_mode
    from core.provider_execution_boundary import invoke_provider_execution_boundary

    model = "claude-haiku-5-5"
    if run.room("direct.stream"):
        adapter = _claude_lane(model, key)
        request = ModelRequest(task_kind="chat", prompt="x", messages=[{"role": "user", "content": "Count from 1 to 5, separated by spaces."}], max_output_tokens=200)
        try:
            chunks = list(invoke_provider_execution_boundary(adapter, "stream_text_task", request))
            text = "".join(c.delta_text for c in chunks)
            usage = dict(chunks[-1].usage or {})
            run.direct_usd += _usd(model, usage)
            run.record("direct.stream", "1 2 3 4 5" in text and chunks[-1].done, text=text, finish=chunks[-1].finish_reason, usage=usage)
        except Exception as exc:
            run.record("direct.stream", False, error=f"{type(exc).__name__}: {exc}"[:800])
    if run.room("direct.structured"):
        adapter = _claude_lane(model, key)
        request = ModelRequest(
            task_kind="summarize",
            prompt="x",
            messages=[{"role": "user", "content": "Summarise: VOOL runs models locally and in the cloud."}],
            output_mode="summary_block",
            contract={"json_schema": json_schema_for_mode("summary_block")},
            max_output_tokens=300,
        )
        try:
            result = invoke_provider_execution_boundary(adapter, "run_structured_task", request)
            run.direct_usd += _usd(model, result.usage)
            parsed = json.loads(result.output_text)
            run.record("direct.structured", isinstance(parsed, dict) and bool(parsed.get("summary")), answer=parsed, usage=result.usage)
        except Exception as exc:
            run.record("direct.structured", False, error=f"{type(exc).__name__}: {exc}"[:800])
    if run.room("direct.json_object"):
        adapter = _claude_lane(model, key)
        request = ModelRequest(
            task_kind="extract",
            prompt="x",
            messages=[{"role": "user", "content": 'Give a JSON object with keys "city" and "country" for Paris.'}],
            output_mode="json_object",
            max_output_tokens=200,
        )
        try:
            result = invoke_provider_execution_boundary(adapter, "invoke", request)
            run.direct_usd += _usd(model, result.usage)
            parsed = json.loads(result.output_text)
            run.record("direct.json_object", parsed.get("country", "").lower() == "france", answer=parsed, usage=result.usage)
        except Exception as exc:
            run.record("direct.json_object", False, error=f"{type(exc).__name__}: {exc}"[:800])
    if run.room("direct.tool_call"):
        tools = build_cloud_tool_definitions(
            [{"intent": "weather.lookup", "description": "Look up the weather for a city.", "arguments": {"city": "string"}}]
        )
        for tool_model in ("claude-haiku-5-5", "claude-sonnet-5-5"):
            adapter = _claude_lane(tool_model, key)
            request = ModelRequest(
                task_kind="tool_intent",
                prompt="x",
                messages=[{"role": "user", "content": "What is the weather in Oslo?"}],
                output_mode="tool_intent",
                tools=tools,
            )
            try:
                result = invoke_provider_execution_boundary(adapter, "invoke", request)
                run.direct_usd += _usd(tool_model, result.usage)
                calls = [{"name": c.name, "arguments": c.arguments} for c in result.tool_calls]
                ok = bool(calls) and str(calls[0]["arguments"].get("city", "")).lower() == "oslo"
                run.record(f"direct.tool_call.{tool_model}", ok, calls=calls, usage=result.usage)
            except Exception as exc:
                run.record(f"direct.tool_call.{tool_model}", False, error=f"{type(exc).__name__}: {exc}"[:800])
    if run.room("direct.timeout"):
        adapter = _claude_lane("claude-opus-5-5", key, timeout_seconds=0.5)
        request = ModelRequest(
            task_kind="chat",
            prompt="x",
            messages=[{"role": "user", "content": "Write a 600-word story about a lighthouse."}],
            max_output_tokens=1500,
        )
        t0 = time.time()
        try:
            result = invoke_provider_execution_boundary(adapter, "run_text_task", request)
            run.direct_usd += _usd("claude-opus-5-5", result.usage)
            run.record("direct.timeout", False, unexpected="answered inside 0.5 s", seconds=round(time.time() - t0, 2))
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            run.direct_usd += 0.04  # the request may have been billed before it was dropped: counted at a bound
            run.record("direct.timeout", "timeout" in text.lower() or "timed out" in text.lower(), error=text[:600], seconds=round(time.time() - t0, 2))
    if run.room("direct.unknown_model"):
        adapter = _claude_lane("claude-nope-9-9", key)
        try:
            invoke_provider_execution_boundary(adapter, "run_text_task", ModelRequest(task_kind="chat", prompt="x", messages=[{"role": "user", "content": "hi"}], max_output_tokens=50))
            run.record("direct.unknown_model", False, unexpected="answered")
        except Exception as exc:
            text = str(exc)
            run.record("direct.unknown_model", "does not know that model" in text, error=text[:600])
    if run.room("direct.bad_key"):
        adapter = _claude_lane(model, BAD_KEY)
        try:
            invoke_provider_execution_boundary(adapter, "run_text_task", ModelRequest(task_kind="chat", prompt="x", messages=[{"role": "user", "content": "hi"}], max_output_tokens=50))
            run.record("direct.bad_key", False, unexpected="answered")
        except Exception as exc:
            text = str(exc)
            run.record("direct.bad_key", "refused the API key" in text and BAD_KEY not in text, error=text[:600])


def daemon_checks(run: Run, repo: Path, python: str, key: str) -> None:
    # A. The Settings flow: nothing in the environment, the key saved the way the page saves it.
    settings = Daemon(repo, python, run.out / "settings-flow")
    run.daemons.append(settings)
    settings.start()
    try:
        refused = settings.save_key(BAD_KEY)
        _status, creds = settings.req("GET", "/api/settings/credentials")
        stored_bad = "llm.cloud.anthropic" in json.dumps(creds) and '"connected": true' in json.dumps(creds).lower()
        run.record("settings.bad_key_not_stored", "complete" not in refused and not stored_bad, steps=refused)
        saved = settings.save_key(key)
        _status, cloud = settings.req("GET", "/api/cloud/status")
        provider = (cloud or {}).get("provider") if isinstance(cloud, dict) else None
        run.record(
            "settings.save_key_activates_claude",
            "complete" in saved and saved["complete"][0] == 200 and str(provider) == "anthropic",
            steps={k: v for k, v in saved.items() if k != "verify"},
            verify_outcome=(saved.get("verify") or [None, {}])[1].get("outcome") if isinstance((saved.get("verify") or [None, {}])[1], dict) else saved.get("verify"),
            cloud_status=cloud,
        )
        # Questions only a model answers (arithmetic and "reply with the word X" can be answered, or
        # refused, by VOOL's own fast paths without any model call).
        asks = {
            "claude-haiku-5-5": ("Write one short sentence about the sea that uses the word lighthouse.", "lighthouse"),
            "claude-sonnet-5-5": ("In one sentence, which planet in our solar system is the largest?", "jupiter"),
            "claude-opus-5-5": ("In one sentence, what gas do plants take in to make food?", "carbon dioxide"),
        }
        for model, (words, expect) in asks.items():
            if not run.room(f"chat.{model}"):
                continue
            mark = settings.prompt_log_lines()
            turn = settings.chat(f"live-{model}", words, model)
            called = settings.models_called(mark)
            run.record(
                f"chat.{model}",
                turn["http"] == 200 and expect in turn["reply"].lower() and any(model in c for c in called),
                turn=turn,
                model_calls=called,
            )
        if run.room("skill.draft_on_claude"):
            mark = settings.prompt_log_lines()
            turn = settings.chat(
                "live-skill",
                "Make a skill that turns my meeting notes into a short list of action items with owners.",
                "claude-haiku-5-5",
            )
            called = settings.models_called(mark)
            reply = turn["reply"]
            run.record(
                "skill.draft_on_claude",
                "from your own words" not in reply and ("anthropic" in reply.lower() or "claude" in reply.lower()) and called and all("claude" in c for c in called),
                turn=turn,
                model_calls=called,
            )
        if run.room("chat.unknown_model_fails_plainly"):
            mark = settings.prompt_log_lines()
            turn = settings.chat("live-unknown", "Write one short sentence about autumn leaves.", "claude-nope-9-9")
            called = settings.models_called(mark)
            local = [c for c in called if "claude" not in c]
            run.record(
                "chat.unknown_model_fails_plainly",
                not local and "know that model" in turn["reply"],
                turn=turn,
                model_calls=called,
                note="a pinned model that does not exist must not be answered by another model",
            )
        if run.room("chat.auto_after_key"):
            mark = settings.prompt_log_lines()
            turn = settings.chat("live-auto", "In one sentence, what is the boiling point of water at sea level in Celsius?", None)
            run.record("chat.auto_after_key", turn["http"] == 200 and "100" in turn["reply"], turn=turn, model_calls=settings.models_called(mark),
                       note="informational: with a local model installed, Auto may answer locally by design")
    finally:
        settings.stop()

    # B. Only ANTHROPIC_API_KEY in the environment, nothing saved.
    if run.room("env_key.chat"):
        envonly = Daemon(repo, python, run.out / "env-key", env_key=key)
        run.daemons.append(envonly)
        envonly.start()
        try:
            _status, cloud = envonly.req("GET", "/api/cloud/status")
            mark = envonly.prompt_log_lines()
            turn = envonly.chat("live-env", "In one sentence, what colour is a ripe banana?", "claude-haiku-5-5")
            called = envonly.models_called(mark)
            run.record("env_key.chat", turn["http"] == 200 and "yellow" in turn["reply"].lower() and any("claude" in c for c in called),
                       cloud_status=cloud, turn=turn, model_calls=called)
        finally:
            envonly.stop()

    # C. A bad key in the environment: the chat must say the key was refused, not answer locally.
    if run.room("env_bad_key.chat"):
        badenv = Daemon(repo, python, run.out / "env-bad-key", env_key=BAD_KEY)
        run.daemons.append(badenv)
        badenv.start()
        try:
            mark = badenv.prompt_log_lines()
            turn = badenv.chat("live-badenv", "In one sentence, what colour is a ripe banana?", "claude-haiku-5-5")
            called = badenv.models_called(mark)
            local = [c for c in called if "claude" not in c]
            run.record("env_bad_key.chat", not local and "yellow" not in turn["reply"].lower() and "key" in turn["reply"].lower(),
                       turn=turn, model_calls=called)
        finally:
            badenv.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--cap", type=float, default=3.0, help="USD; no new step starts once spend reaches it")
    parser.add_argument("--only", default="", help="direct,daemon (default both)")
    args = parser.parse_args()
    # Absolute: the daemons run with the repository as their working directory.
    args.out = args.out.resolve()
    args.repo = args.repo.resolve()
    key = str(os.environ.pop("VOOL_LIVE_ANTHROPIC_KEY", "") or "").strip()
    if not key.startswith("sk-ant-"):
        print("VOOL_LIVE_ANTHROPIC_KEY is not set to an Anthropic key", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.repo.resolve()))
    os.environ.setdefault("VOOL_HOME", str(args.out / "direct-home"))
    os.environ["VOOL_KEYCHAIN_ALLOWED"] = "0"
    run = Run(args.out, args.cap)
    head = subprocess.run(["git", "-C", str(args.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(args.repo), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    (args.out / "source.json").write_text(json.dumps({"head": head, "dirty": dirty, "started": utcnow(), "cap_usd": args.cap}, indent=1))
    groups = {g for g in args.only.split(",") if g} or {"direct", "daemon"}
    try:
        if "direct" in groups:
            direct_checks(run, key)
        if "daemon" in groups:
            daemon_checks(run, args.repo.resolve(), args.python, key)
    finally:
        for daemon in run.daemons:
            daemon.stop()
        leaks = []
        for path in args.out.rglob("*"):
            if path.is_file() and path.stat().st_size < 50_000_000:
                try:
                    if key in path.read_text(errors="ignore"):
                        leaks.append(str(path))
                except OSError:
                    pass
        passed = sum(1 for r in run.rows if r["pass"])
        summary = {"passed": passed, "total": len(run.rows), "spend_usd": run.spent(), "direct_usd_estimate": round(run.direct_usd, 6),
                   "key_found_in": leaks, "finished": utcnow()}
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary), flush=True)
    return 0 if passed == len(run.rows) and not leaks else 1


if __name__ == "__main__":
    raise SystemExit(main())
