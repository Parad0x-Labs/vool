"""Exercise a downloaded macOS DMG the way a user runs it, in a disposable HOME.

Mounts the DMG read-only, copies VOOL.app out, launches the app's own main executable (the
native host, which starts and owns the bundled runtime and Ollama), and drives the real chat
door: one plain reply, then remember, recall, forget and recall-after-forget on a fresh profile.
Then it stops the native host with SIGTERM and requires the owned API to go away.

Every step writes its raw reply under --scratch. A failed prerequisite (hardware, signature,
bundle identity, runtime start, model pull or certification) stops the run at once, because nothing
after it can run. Each chat check runs whatever an earlier check said, so one slow answer cannot hide
the others; the run prints a PASS/FAIL table of every step and still exits non-zero if any failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

SESSION = "intel-acceptance-ilona"
FACT = "Remember that my sister Ilona's favourite tree is the silver birch."
QUESTION = "What is Ilona's favourite tree?"
FORGET = "Forget what I told you about Ilona's favourite tree."
MARKER = "birch"
# Settings > Local models certifies with this per-call budget; the runtime multiplies it by
# core/local_compute_budget.CPU_ONLY_FACTOR on a machine with no GPU (every Intel runner).
CERTIFY_CALL_SECONDS = 150
CERTIFY_MAX_CALLS = 5
CPU_ONLY_FACTOR = 4.0


# The runtime's own wordings for a turn whose model gave no answer (core/agent_runtime/memory_runtime.py).
# A reply like that carries no fact either, so an absence check must never read it as a PASS.
MODEL_FAILURE_WORDS = (
    "usable reply",
    "usable answer",
    "usable model response",
    "failed before producing",
    "timed out before producing",
    "could not reach its local runtime",
)


class SmokeFailure(RuntimeError):
    pass


def failed_model_calls(value: object) -> int:
    """The largest failed_model_calls count anywhere in a chat answer's provenance."""
    if isinstance(value, dict):
        own = value.get("failed_model_calls")
        found = own if isinstance(own, int) else 0
        return max([found] + [failed_model_calls(item) for item in value.values()])
    if isinstance(value, list):
        return max([0] + [failed_model_calls(item) for item in value])
    return 0


def request(origin: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(
        origin + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as reply:
        if reply.status != 200:
            raise SmokeFailure(f"{path} answered HTTP {reply.status}")
        return json.load(reply)


def reply_text(answer: dict) -> str:
    commit = answer.get("vool_response_commit") or {}
    text = str(commit.get("canonical_content") or "") or str((answer.get("message") or {}).get("content") or "")
    if not text.strip():
        raise SmokeFailure(f"chat answered with no text: {json.dumps(answer)[:600]}")
    return text


class Run:
    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.steps: list[dict] = []
        self.last_answer: dict = {}

    def record(self, name: str, ok: bool, detail: object, fatal: bool = True) -> None:
        self.steps.append({"step": name, "ok": ok, "detail": detail})
        (self.scratch / "steps.json").write_text(json.dumps(self.steps, indent=2) + "\n")
        print(
            f"{'PASS' if ok else 'FAIL'} {name}: {detail if isinstance(detail, str) else json.dumps(detail)[:400]}",
            flush=True,
        )
        if not ok and fatal:
            raise SmokeFailure(f"{name}: {detail}")

    def check(self, name: str, step) -> None:
        """Run one chat check and record it without stopping the run; an error in it is its FAIL."""
        try:
            ok, detail = step()
        except (SmokeFailure, OSError, ValueError, urllib.error.URLError) as error:
            ok, detail = False, f"{type(error).__name__}: {error}"
        self.record(name, ok, detail, fatal=False)

    def summary(self) -> list[str]:
        """Print every step as a PASS/FAIL table; the names of the failed ones."""
        width = max(len(step["step"]) for step in self.steps)
        print("----- smoke steps -----", flush=True)
        for step in self.steps:
            print(f"{'PASS' if step['ok'] else 'FAIL'}  {step['step'].ljust(width)}", flush=True)
        return [step["step"] for step in self.steps if not step["ok"]]

    def chat(self, origin: str, name: str, message: str, model: str, timeout: float, session: str = SESSION) -> str:
        body = {
            "messages": [{"role": "user", "content": message}],
            "session_id": session,
            "stream": False,
            "model": model,
            "model_selection": "pin",
        }
        start = time.monotonic()
        answer = request(origin, "/api/chat", body, timeout=timeout)
        self.last_answer = answer
        seconds = round(time.monotonic() - start, 1)
        (self.scratch / f"chat-{name}.json").write_text(
            json.dumps({"request": body, "seconds": seconds, "answer": answer}, indent=2) + "\n"
        )
        text = reply_text(answer)
        print(f"  {name} ({seconds}s): {text[:300]!r}", flush=True)
        return text


PIN_SESSION, PRIVATE_SESSION = "release-pin-check", "release-private-check"
SECRET = "Quillmere"


def files_holding(root: Path, needle: str) -> list[str]:
    raw = needle.encode()
    hits = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink() and raw in p.read_bytes():
                hits.append(str(p.relative_to(root)))
        except OSError as error:
            raise SmokeFailure(f"cannot inspect smoke profile: {p.relative_to(root)}") from error
    return hits



def release_privacy_checks(run: Run, origin: str, home: Path, model: str, timeout: float) -> None:
    """Exercise PIN redaction, private chat and forgetting through the served chat API."""
    def chat(session, name, text, model, timeout):
        return run.chat(origin, name, text, model, timeout, session=session)

    db = home / "Library/Application Support/VOOL/runtime/data/vool_web0_v2.db"

    def bench_rows():
        import shutil
        import sqlite3
        import tempfile
        # Read a private copy: a read-only open of a live WAL database cannot create its -shm.
        with tempfile.TemporaryDirectory(dir=run.scratch) as tmp:
            for suffix in ("", "-wal", "-shm"):
                src = Path(str(db) + suffix)
                if src.exists():
                    shutil.copyfile(src, Path(tmp) / ("copy.db" + suffix))
            con = sqlite3.connect(str(Path(tmp) / "copy.db"))
            try:
                return [r[0] for r in con.execute("select raw_result_json from local_inference_benchmarks order by rowid")]
            finally:
                con.close()

    def string_leaves(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in string_leaves(v)]
        if isinstance(value, list):
            return [s for v in value for s in string_leaves(v)]
        return []

    def snapshot_db(label):
        import shutil
        out = run.scratch / label
        out.mkdir(exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copyfile(src, out / ("vool_web0_v2.db" + suffix))

    def pin():
        said = chat(PIN_SESSION, "pin", "My card PIN is 4821, please remember it.", model, timeout)
        mem = request(origin, f"/api/memory/entries?session={PIN_SESSION}")
        (run.scratch / "memory-after-pin.json").write_text(json.dumps(mem, indent=2) + "\n")
        # Neither the digits nor an empty "PIN is [redacted]" shell may be remembered.
        stored = "4821" in json.dumps(mem) or bool((mem or {}).get("entries"))
        extra_answer = "locked in" in said.lower() or "remember \"it\"" in said.lower() or "remember “it”" in said.lower()
        return ("4821" not in said and not stored and not extra_answer), {
            "reply": said[:300], "pin_in_memory": stored, "second_answer": extra_answer}

    def private():
        before = len(bench_rows())
        on = chat(PRIVATE_SESSION, "private-on", "private chat", model, timeout)
        chat(PRIVATE_SESSION, "private-fact", f"My cousin's boat is called {SECRET}. Remember that.", model, timeout)
        snapshot_db("wal-snap-1-after-private-fact")
        during = len(bench_rows())
        off = chat(PRIVATE_SESSION, "private-off", "turn recording back on", model, timeout)
        snapshot_db("wal-snap-2-after-recording-on")
        hist = request(origin, f"/api/chat/history?session={PRIVATE_SESSION}")
        (run.scratch / "history-private.json").write_text(json.dumps(hist, indent=2) + "\n")
        on_disk = files_holding(home, SECRET)
        ok = SECRET not in json.dumps(hist) and not on_disk and during == before
        return ok, {"on": on[:200], "off": off[:200], "in_history": SECRET in json.dumps(hist), "files": on_disk[:10],
                    "benchmark_rows_before_private_turn": before, "after_private_fact": during}

    def bench_content_free():
        rows = bench_rows()
        (run.scratch / "benchmark-rows.json").write_text(json.dumps(rows, indent=2) + "\n")
        strings = sorted({s for r in rows for s in string_leaves(json.loads(r) if r else {})})
        return not strings, {"rows": len(rows), "string_values": strings[:20]}

    def guarded(fn):
        def run_it():
            try:
                return fn()
            except Exception as error:  # a broken check is its own FAIL, never a crash of the smoke
                return False, f"{type(error).__name__}: {error}"
        return run_it

    def forget_digit():
        # Forgetting a word that also appears in today's date must not block every other memory.
        s = "release-forget-digit-check"
        chat(s, "digit-remember-teal", "Remember that my favourite colour is teal.", model, timeout)
        chat(s, "digit-remember-locker", "Remember that my locker is 14.", model, timeout)
        forgot = chat(s, "digit-forget", os.environ.get("FORGET_DIGIT_PHRASE", "Forget 14."), model, timeout)
        mem = request(origin, f"/api/memory/entries?session={s}")
        (run.scratch / "memory-after-forget-digit.json").write_text(json.dumps(mem, indent=2) + "\n")
        dump = json.dumps(mem).lower()
        teal = chat(s, "digit-recall-teal", "What is my favourite colour?", model, timeout)
        locker = chat(s, "digit-recall-locker", "What is my locker number?", model, timeout)
        forget_ok = "could not" not in forgot.lower() and "retry" not in forgot.lower()
        locker_files = files_holding(home, "locker is 14")
        ok = (forget_ok and "teal" in dump and "locker" not in dump and "teal" in teal.lower() and "14" not in locker
              and not locker_files)
        return ok, {"forget": forgot[:200], "locker_files": locker_files[:10], "teal_in_memory": "teal" in dump, "locker_in_memory": "locker" in dump,
                    "recall_teal": teal[:200], "recall_locker": locker[:200]}

    pin, private, bench_content_free = guarded(pin), guarded(private), guarded(bench_content_free)
    forget_digit = guarded(forget_digit)
    run.check("forget '14' keeps unrelated memories and drops the locker", forget_digit)

    def birch_gone():
        files = files_holding(home, "silver birch")
        return not files, {"files_holding_silver_birch": files[:10]}

    run.check("forgotten 'silver birch' is gone from every file under HOME", guarded(birch_gone))
    run.check("PIN not echoed and not stored", pin)
    run.check("private chat leaves no transcript, memory or file", private)
    run.check("speed records hold only numbers", bench_content_free)



def host_identity() -> dict:
    def sysctl(key: str) -> str:
        done = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True)
        return done.stdout.strip() if done.returncode == 0 else ""

    return {
        "machine": platform.machine(),
        "cpu": sysctl("machdep.cpu.brand_string"),
        "arm64_capable": sysctl("hw.optional.arm64") or "0",
        "translated": sysctl("sysctl.proc_translated") or "0",
        "macos": platform.mac_ver()[0],
    }


def stage_app(dmg: Path, scratch: Path) -> Path:
    attached = subprocess.run(
        ["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-plist", str(dmg)],
        check=True, capture_output=True,
    )
    mounts = [Path(entry["mount-point"]) for entry in plistlib.loads(attached.stdout)["system-entities"]
              if entry.get("mount-point")]
    if len(mounts) != 1:
        for mounted in mounts:
            subprocess.run(["hdiutil", "detach", str(mounted)], check=True)
        raise SmokeFailure(f"expected one mounted filesystem, found {len(mounts)}")
    mount = mounts[0]
    try:
        apps = sorted(mount.glob("*.app"))
        if len(apps) != 1:
            raise SmokeFailure(f"expected one .app in the DMG, found {[a.name for a in apps]}")
        target = scratch / "Applications" / apps[0].name
        target.parent.mkdir()
        # ditto keeps the signature, extended attributes and symlinks exactly as shipped.
        subprocess.run(["ditto", str(apps[0]), str(target)], check=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], check=True)
    return target


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def smoke(dmg: Path, scratch: Path, expected: str, arch: str, model: str, chat_timeout: float,
          pet_verdict: Path | None = None) -> None:
    scratch.mkdir(parents=True, exist_ok=False)
    run = Run(scratch)
    host = host_identity()
    native = (
        host["machine"] == arch and host["translated"] == "0" and (arch != "x86_64" or host["arm64_capable"] == "0")
    )
    run.record("real hardware", native, host)

    app = stage_app(dmg, scratch)
    verify = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], capture_output=True, text=True)
    run.record("code signature intact after copy", verify.returncode == 0, verify.stderr.strip() or "valid")
    plist = app / "Contents/Info.plist"
    declared = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", "Print :LSArchitecturePriority:0", str(plist)], capture_output=True, text=True
    ).stdout.strip()
    sha = subprocess.run(
        ["defaults", "read", str(plist), "NULLASourceSHA"], capture_output=True, text=True
    ).stdout.strip()
    run.record("bundle identity", declared == arch and sha == expected, {"arch": declared, "commit": sha})
    embedded = app / "Contents/Resources/python/bin/python3"
    lipo = subprocess.run(["lipo", "-archs", str(embedded.resolve())], capture_output=True, text=True).stdout.split()
    run.record("embedded interpreter slice", arch in lipo, lipo)

    home = scratch / "home with spaces"
    home.mkdir()
    port = free_port()
    origin = f"http://127.0.0.1:{port}"
    support = home / "Library/Application Support/VOOL"
    # A fresh HOME and a stripped environment: no runner credentials, provider keys, VOOL_* or
    # OLLAMA_* settings can stand in for what the bundle itself provides.
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": "en_US.UTF-8",
        "TMPDIR": str(scratch / "tmp"),
        "USER": os.environ.get("USER", "runner"),
        "VOOL_NATIVE_API_URL": origin,
    }
    (scratch / "tmp").mkdir()
    process = subprocess.Popen(
        [str(app / "Contents/MacOS/VOOL")], cwd=home, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 300
        while True:
            if process.poll() is not None:
                raise SmokeFailure(f"the app exited before its runtime was ready: {process.returncode}; see app.log")
            try:
                health = request(origin, "/healthz")
                break
            except (OSError, ValueError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    raise SmokeFailure("runtime readiness timed out after 300s; see app.log") from None
                time.sleep(1)
        runtime = health.get("runtime") or {}
        run.record(
            "native host started its owned runtime",
            health.get("ok") is True and runtime.get("commit_full") == expected,
            {k: runtime.get(k) for k in ("commit_full", "release_version", "build_id", "pid")},
        )
        version = request(origin, "/api/runtime/version")
        run.record(
            "runtime version endpoint",
            version.get("commit_full") == expected,
            {k: version.get(k) for k in ("commit_full", "build_id")},
        )
        pact = request(origin, "/api/onboarding/pact")
        (scratch / "first-run-pact.json").write_text(json.dumps(pact, indent=2) + "\n")
        shot = scratch / "screen-after-launch.png"
        subprocess.run(["screencapture", "-x", str(shot)], check=False)

        ollama = app / "Contents/Resources/ollama/ollama"
        o_archs = subprocess.run(["lipo", "-archs", str(ollama)], capture_output=True, text=True).stdout.split()
        run.record("bundled ollama slice", arch in o_archs, o_archs)
        pull_env = {**env, "OLLAMA_HOST": "127.0.0.1:11434"}
        with (scratch / "ollama-pull.log").open("w") as log:
            pulled = subprocess.run(
                [str(ollama), "pull", model], env=pull_env, stdout=log, stderr=subprocess.STDOUT, timeout=1800
            )
        run.record(f"bundled ollama pulled {model}", pulled.returncode == 0, f"exit {pulled.returncode}")
        registered = request(origin, "/api/models/local/register", {"model_name": model}, timeout=120)
        (scratch / "model-register.json").write_text(json.dumps(registered, indent=2) + "\n")
        # A registered local model may not write final answers until it passes the sealed tool
        # probe; Settings > Local models runs it with this request and this 150 s per-call budget.
        # The wait covers the most the runtime itself may take: up to five probe calls (parallel
        # call + one repair, continuation, recovery + one retry), each allowed the requested
        # budget times the CPU-only factor on a machine with no GPU, plus a minute of slack. It
        # is a ceiling, never a wait: the request returns as soon as the probe does.
        start = time.monotonic()
        certified = request(
            origin,
            "/api/model-tool-certification/run",
            {"provider_name": "ollama-local", "model_name": model, "timeout_seconds": CERTIFY_CALL_SECONDS},
            timeout=CERTIFY_MAX_CALLS * CERTIFY_CALL_SECONDS * CPU_ONLY_FACTOR + 60,
        )
        seconds = round(time.monotonic() - start, 1)
        (scratch / "model-certification.json").write_text(json.dumps(certified, indent=2) + "\n")
        stages = {name: (stage or {}).get("state") for name, stage in (certified.get("stages") or {}).items()}
        run.record(
            f"{model} certified as an author ({seconds}s)",
            certified.get("state") == "verified",
            {"state": certified.get("state"), "stages": stages},
        )

        def plain():
            text = run.chat(origin, "plain", "Calculate 23 * 19", model, chat_timeout)
            return "437" in text, text[:300]

        def remember():
            said = run.chat(origin, "remember", FACT, model, chat_timeout)
            entries = request(origin, f"/api/memory/entries?session={SESSION}")
            (scratch / "memory-after-remember.json").write_text(json.dumps(entries, indent=2) + "\n")
            return MARKER in json.dumps(entries).lower(), said[:300]

        def recall():
            recalled = run.chat(origin, "recall", QUESTION, model, chat_timeout)
            return MARKER in recalled.lower(), recalled[:300]

        def forget():
            forgot = run.chat(origin, "forget", FORGET, model, chat_timeout)
            entries = request(origin, f"/api/memory/entries?session={SESSION}")
            (scratch / "memory-after-forget.json").write_text(json.dumps(entries, indent=2) + "\n")
            return MARKER not in json.dumps(entries).lower(), forgot[:300]

        def recall_after_forget():
            # A pass needs a real answer that lacks the fact: a model failure also lacks it.
            after = run.chat(origin, "recall-after-forget", QUESTION, model, chat_timeout)
            failed = failed_model_calls(run.last_answer)
            no_answer = failed > 0 or any(words in after.lower() for words in MODEL_FAILURE_WORDS)
            detail = after[:300] if not no_answer else f"no model answer (failed calls {failed}): {after[:300]}"
            return MARKER not in after.lower() and not no_answer, detail

        run.check("plain reply", plain)
        run.check("remember stored the fact", remember)
        run.check("recall answers from memory", recall)
        run.check("forget removed the entry", forget)
        run.check("recall after forget does not return the fact", recall_after_forget)
        release_privacy_checks(run, origin, home, model, chat_timeout)
        try:
            verdict = json.loads(pet_verdict.read_text()) if pet_verdict is not None else {}
        except (OSError, ValueError) as error:
            verdict = {"error": str(error)}
        run.record("pet: one pet, preview, drag to desktop (manual native check)",
                   verdict.get("ok") is True, verdict or "manual verdict required", fatal=False)
    finally:
        early_exit = process.poll()
        if early_exit is not None:
            print(f"the native host had already exited with {early_exit} before teardown", flush=True)
        else:
            process.send_signal(signal.SIGTERM)  # Only the native host started above.
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        # Every log the app wrote under its support folder, kept as evidence and its tail printed,
        # so a failure is readable from the job log itself.
        logs = scratch / "logs"
        logs.mkdir(exist_ok=True)
        for source in sorted(support.rglob("*.log")) if support.exists() else []:
            target = logs / str(source.relative_to(support)).replace("/", "__")
            shutil.copy2(source, target)
            lines = source.read_text(errors="replace").splitlines()
            # The API log is mostly per-request access lines; the decisions live between them.
            if source.name == "vool_api.log":
                lines = [line for line in lines if '"http_request' not in line or "/api/chat" in line]
            tail = lines[-200:]
            print(f"----- {source.relative_to(support)} (last {len(tail)} lines) -----", flush=True)
            print("\n".join(tail), flush=True)
        # The failed chat's own reply carries the lane, provenance and refusal reasons.
        for reply in sorted(scratch.glob("chat-*.json")):
            print(f"----- {reply.name} (first 6000 chars) -----", flush=True)
            print(reply.read_text(errors="replace")[:6000], flush=True)
    try:
        request(origin, "/healthz", timeout=5)
    except (OSError, ValueError, urllib.error.URLError):
        pass
    else:
        run.record(
            "owned runtime stopped with the app", False, "the API still answers after the native host exited", fatal=False
        )
    run.record("app quit on SIGTERM", process.returncode is not None, f"exit {process.returncode}", fatal=False)
    failed = run.summary()
    if failed:
        raise SmokeFailure(f"{len(failed)} of {len(run.steps)} steps failed: {', '.join(failed)}")
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    print(f"PASS: {dmg.name} sha256 {digest} on {host['cpu']} macOS {host['macos']}, commit {expected}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dmg", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--chat-timeout", type=float, default=900)
    parser.add_argument("--pet-verdict", type=Path, help="JSON verdict from the manual native pet check")
    args = parser.parse_args()
    smoke(args.dmg.resolve(), args.scratch.resolve(), args.expected_commit, args.arch, args.model,
          args.chat_timeout, args.pet_verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
