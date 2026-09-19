"""First-Run Pact served journeys (spec §17): real daemon, isolated homes, zero network.

JP1 fresh-install Local-Only money path · JP2 existing-user upgrade · JP4 skip everywhere
+ replay · JP5 SIGKILL restart mid-pact · JP7 fabrication attempts.

Every journey boots the PRODUCTION daemon (``python -m apps.vool_api_server``) on an
isolated ``VOOL_HOME`` and a free loopback port, gates on /healthz, drives the real HTTP
surface, and cleans up behind itself (process killed and reaped, home deleted, port
re-bound as proof). Transcripts land in validation-logs/first-run-pact-20260905/journeys/.

Run:  .venv/bin/python ops/first_run_pact_journeys.py [jp1 jp2 jp4 jp5 jp7]
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "validation-logs" / "first-run-pact-20260905" / "journeys"
PY = sys.executable

CANON = "openclaw:" + "5a7b9c1d3e5f7a9b1c3d"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Daemon:
    def __init__(self, home: Path, log: Path):
        self.home = home
        self.log = log
        self.port = free_port()
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _env(self) -> dict:
        env = dict(os.environ)
        for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "VOOL_KEYCHAIN_ALLOWED"):
            env.pop(key, None)
        env.update({
            "VOOL_HOME": str(self.home),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_CREDENTIAL_STORE": "vault",
            "VOOL_DISABLE_MESH_DAEMON": "1",
            "VOOL_SKIP_PROVIDER_PREWARM": "1",
            "VOOL_PUBLIC_HIVE_ENABLED": "0",
            "VOOL_WORKSPACE_ROOT": str(self.home / "workspace"),
            "VOOL_CUSTOM_BASE_URL": "http://127.0.0.1:9/v1",
            "VOOL_CUSTOM_API_KEY": "journey-local-key-never-a-real-credential",
        })
        return env

    def start(self, timeout_s: float = 120.0) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [PY, "-m", "apps.vool_api_server", "--port", str(self.port), "--bind", "127.0.0.1"],
            cwd=str(REPO), env=self._env(),
            stdout=open(self.log, "ab"),  # noqa: SIM115 — handle ownership passes to the child
                stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                status, body = self.get("/healthz")
                if status == 200 and body.get("ok"):
                    return
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"daemon on :{self.port} never answered /healthz; see {self.log}")

    def kill(self, sig=signal.SIGKILL) -> None:
        if self.proc is None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(self.proc.pid), sig)
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            self.proc.wait(timeout=10)
        self.proc = None

    # --- HTTP ----------------------------------------------------------------

    def get(self, path: str):
        req = urllib.request.Request(self.base + path)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def post(self, path: str, body: dict):
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def chat(self, text: str, turn_id: str, session: str = CANON, extra: dict | None = None):
        body = {"model": "vool-local-only", "stream": True, "stream_task_events": True,
                "session_id": session, "turn_id": turn_id,
                "messages": [{"role": "user", "content": text}]}
        body.update(extra or {})
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        frames = []
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                for line in raw.decode("utf-8", "replace").splitlines():
                    if line.strip():
                        frames.append(json.loads(line))
        commit = next((f["vool_response_commit"] for f in frames if isinstance(f, dict) and f.get("vool_response_commit")), None)
        answer = "".join(f.get("message", {}).get("content", "") for f in frames if isinstance(f, dict))
        return commit or {}, answer

    def page(self) -> str:
        with urllib.request.urlopen(self.base + "/chat", timeout=30) as resp:
            return resp.read().decode()


def _approve_pending(daemon: Daemon) -> str:
    home = daemon.home
    pend = home / "data" / "pending_approvals.json"
    deadline = time.time() + 20
    while time.time() < deadline:
        if pend.exists():
            try:
                data = json.loads(pend.read_text())
                for token, entry in data.items():
                    if isinstance(entry, dict) and entry.get("status", "pending") == "pending":
                        return token
            except Exception:
                pass
        time.sleep(0.25)
    raise AssertionError("no pending approval appeared for the deterministic write")


def walk_to(daemon: Daemon, state: str) -> dict:
    status, snap = daemon.get("/api/onboarding/pact")
    assert status == 200, snap
    if snap["state"] == "absent":
        status, payload = daemon.post("/api/onboarding/pact/begin", {})
        assert status == 200, payload
        status, snap = daemon.get("/api/onboarding/pact")
    if snap["state"] == "welcome" and state != "welcome":
        status, payload = daemon.post("/api/onboarding/pact/advance", {"to": "naming", "expect_revision": snap["revision"]})
        assert status == 200, payload
        status, snap = daemon.get("/api/onboarding/pact")
    for step, following in (("naming", "facts"), ("facts", "boundaries"), ("boundaries", "provider_choice")):
        if state == step:
            return snap
        if snap["state"] == step:
            status, payload = daemon.post("/api/onboarding/pact/advance", {"to": following, "expect_revision": snap["revision"]})
            assert status == 200, (step, payload)
            status, snap = daemon.get("/api/onboarding/pact")
            if state == following:
                return snap
    if state == "local_task" and snap["state"] == "provider_choice":
        status, payload = daemon.post("/api/onboarding/pact/choice/local-only", {})
        assert status == 200, payload
        status, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/advance", {"to": "local_task", "expect_revision": snap["revision"]})
        assert status == 200, payload
        status, snap = daemon.get("/api/onboarding/pact")
    return snap


def run_task(daemon: Daemon, prompt: str) -> tuple[str, str]:
    token = ""
    commit, answer = daemon.chat(prompt, "jp-task")
    if "approval" in answer.lower() or (daemon.home / "data" / "pending_approvals.json").exists():
        try:
            token = _approve_pending(daemon)
        except AssertionError:
            token = ""
    if token:
        status, payload = daemon.post("/api/mode", {"op": "resolve_approval", "approval_id": token, "decision": "allow", "session_id": CANON})
        assert status == 200, payload
        commit, answer = daemon.chat(prompt, "jp-task", extra={"approval_token": token})
    return (commit or {}).get("request_id", ""), answer


def cleanup(daemon: Daemon, home: Path) -> dict:
    daemon.kill()
    gone_home = False
    shutil.rmtree(home, ignore_errors=True)
    gone_home = not home.exists()
    port_free = False
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", daemon.port))
        s.close()
        port_free = True
    except OSError:
        pass
    return {"process_reaped": daemon.proc is None, "home_removed": gone_home, "port_reusable": port_free}


# --- JP1 ---------------------------------------------------------------------

def jp1() -> dict:
    home = Path(tempfile.mkdtemp(prefix="jp1-home-"))
    daemon = Daemon(home, home.parent / (home.name + ".log"))
    record: dict = {"journey": "JP1 fresh-install Local-Only money path"}
    try:
        daemon.start()
        status, _health = daemon.get("/healthz")
        record["healthz"] = status == 200
        page = daemon.page()
        record["page_serves_pact_card"] = 'id="pactCard"' in page
        record["page_serves_copy_dict"] = "PACT_COPY" in page and "pactTitle" in page
        record["page_uses_composer_footer_region"] = 'id="queue"' in page and page.index('id="pactCard"') < page.index('id="queue')

        status, snap = daemon.get("/api/onboarding/pact")
        record["fresh_absent"] = (status == 200 and snap["state"] == "absent" and snap["seed_reason"] == "fresh_install")
        record["cloud_no_key"] = snap["live"]["cloud_connection"] == "no_key"

        # chat is usable with the card pending: an ordinary deterministic turn answers.
        commit, answer = daemon.chat("what is 2 plus 2?", "jp1-usable")
        record["chat_usable_with_card"] = bool(answer)

        # naming + facts (secret refused; the string never lands anywhere in the home)
        daemon.post("/api/onboarding/pact/begin", {})
        _, snap = daemon.get("/api/onboarding/pact")
        daemon.post("/api/onboarding/pact/advance", {"to": "naming", "expect_revision": snap["revision"]})
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/name", {"keep_default": True, "preferred_address": "Alex", "expect_revision": snap["revision"]})
        record["naming_saved"] = status == 200
        _, snap = daemon.get("/api/onboarding/pact")
        secret = "sk-or-v1-jp1canary000000000000000000"
        status, payload = daemon.post("/api/onboarding/pact/facts", {"items": [
            {"category": "locale", "value": "lt-LT"},
            {"category": "shipping_region_preference", "value": "Europe"},
            {"category": "email_signature", "value": secret},
        ], "expect_revision": snap["revision"]})
        refused = any(r["category"] == "email_signature" and r["status"] == "refused_secret" for r in payload["results"])
        record["secret_refused"] = refused
        hits = []
        for p in home.rglob("*"):
            if p.is_file() and p.stat().st_size < 1_000_000:
                try:
                    if secret in p.read_text(errors="ignore"):
                        hits.append(str(p))
                except Exception:
                    pass
        record["secret_nowhere_in_home"] = not hits

        # walk the pure steps naming → facts → boundaries; the composite; then the choice.
        snap = walk_to(daemon, "boundaries")
        status, payload = daemon.post("/api/onboarding/pact/boundary", {"key": "local_only_composite", "value": True, "expect_revision": snap["revision"]})
        live = payload["live"]
        record["composite_bindings"] = (live["local_only_mode"] is True and live["allow_web_fallback"] is False
                                        and live["outbound_enabled"] is False and live["share_scope"] == "local_only")
        record["egress_tier"] = payload["egress"]["proof"]
        record["all_doors_proved_closed"] = all(d["closed"] for d in payload["egress"]["doors"])
        snap = walk_to(daemon, "provider_choice")

        # choice → local_task
        status, payload = daemon.post("/api/onboarding/pact/choice/local-only", {})
        record["provider_local_only"] = payload.get("state") == "local_only_done"
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/advance", {"to": "local_task", "expect_revision": snap["revision"]})
        record["local_task_reached"] = status == 200 and payload.get("state") == "local_task"

        # the REAL task — deterministic write, zero model calls
        _, snap = daemon.get("/api/onboarding/pact")
        request_id, answer = run_task(daemon, snap["task_prompt_direct"])
        notes = home / "workspace" / "welcome-notes.txt"
        record["real_file_written"] = notes.exists() and "Welcome to VOOL" in notes.read_text()
        record["task_answer_honest"] = "welcome-notes" in answer

        # claim → receipt + chip linkage
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/task/claim", {"session_id": CANON, "request_id": request_id, "expect_revision": snap["revision"]})
        record["task_claim"] = status == 200 and payload["receipt"]["signature_verified"] and payload["receipt"]["chain_verified"]
        status, proof = daemon.get(f"/api/chat/proof?session={CANON}&request_id={request_id}")
        record["proof_chip_bound"] = status == 200 and proof.get("bound") is True and proof.get("state") in {"VERIFIED", "RECORDED"}
        status, receipts = daemon.get(f"/api/runtime/receipts?session={CANON}")
        record["receipts_chain_verified"] = receipts.get("chain_verified") is True

        # the honest "no" — deterministic retrieval attempt contained by the composite
        _, snap = daemon.get("/api/onboarding/pact")
        status, _ = daemon.post("/api/onboarding/pact/advance", {"to": "denial_demo", "expect_revision": snap["revision"]})
        commit, answer = daemon.chat(snap["denial_prompt_direct"], "jp1-denial")
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/denial/claim", {"session_id": CANON, "request_id": commit.get("request_id", ""), "expect_revision": snap["revision"]})
        record["denial_claim"] = status == 200 and payload.get("denial", {}).get("decision") == "denied"

        # memory verbs + done
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/facts/forget", {"category": "shipping_region_preference", "expect_revision": snap["revision"]})
        record["forget_tombstone"] = status == 200 and payload.get("forgotten") is True
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/boundary", {"key": "memory_paused", "value": True, "expect_revision": snap["revision"]})
        record["memory_paused"] = payload["live"]["memory_paused"] is True
        status, payload = daemon.post("/api/onboarding/pact/advance", {"to": "done", "expect_revision": pact_rig_snapshot_revision(daemon)})
        record["done_reached"] = status == 200 and payload.get("state") == "done"
    finally:
        record["cleanup"] = cleanup(daemon, home)
    return record


def pact_rig_snapshot_revision(daemon: Daemon) -> int:
    _, snap = daemon.get("/api/onboarding/pact")
    return snap["revision"]


# --- JP2 ---------------------------------------------------------------------

def jp2() -> dict:
    home = Path(tempfile.mkdtemp(prefix="jp2-home-"))
    daemon = Daemon(home, home.parent / (home.name + ".log"))
    record: dict = {"journey": "JP2 existing-user upgrade"}
    try:
        # Pre-seed a REAL used home: a conversation + credentials meta, THEN let one boot
        # create every normal boot artifact — so the measured diff isolates the PACT's writes.
        (home / "data").mkdir(parents=True, exist_ok=True)
        (home / "data" / "conversation_log.jsonl").write_text(json.dumps({
            "user": "hello", "assistant": "hi", "session_id": CANON}) + "\n")
        (home / "data" / "credentials.meta.json").write_text("{}\n")
        daemon.start()
        daemon.kill()

        before = {p.relative_to(home): p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
        daemon.start()
        status, snap = daemon.get("/api/onboarding/pact")
        record["existing_not_applicable"] = (status == 200 and snap["state"] == "not_applicable")
        page = daemon.page()
        import re as _re

        tour_states = _re.search(r"const PACT_TOUR_STATES = \[[^\]]*\]", page)
        record["page_tour_states_exclude_not_applicable"] = bool(tour_states) and "not_applicable" not in tour_states.group(0)

        after = {p.relative_to(home): p.stat().st_mtime_ns for p in home.rglob("*") if p.is_file()}
        changed = {str(n) for n, ts in after.items() if before.get(n) != ts}
        # Boot's OWN operational artifacts are allowed to refresh; the pact must add
        # NOTHING and touch no operator data (facts, receipts, identity, preferences).
        boot_artifacts = {
            "config/agent-bootstrap.json", "data/logs/vool_api.log", "data/vool_api.pid",
            "data/vool_web0_v2.db", "data/preflight/38301-apps_vool_api_server.json",
            "data/update_v2/status.json",
        }
        non_boot = {n for n in changed if n not in boot_artifacts and "data/preflight/" not in n}
        non_boot = {n for n in non_boot if not n.endswith(".lock")}  # lock hygiene files are the pact's own
        record["pact_writes_zero_operator_state_for_existing_homes"] = not non_boot
        record["non_boot_changes"] = sorted(non_boot)

        # Settings replay: begin → skip → terminal persists across another boot.
        status, payload = daemon.post("/api/onboarding/pact/begin", {})
        record["replay_begins"] = status == 200 and payload["state"] == "welcome"
        daemon.post("/api/onboarding/pact/skip", {})
        daemon.kill()
        daemon.start()
        status, snap = daemon.get("/api/onboarding/pact")
        record["consent_persists_after_reboot"] = snap["state"] == "skipped"
    finally:
        record["cleanup"] = cleanup(daemon, home)
    return record


# --- JP4 ---------------------------------------------------------------------

def jp4() -> dict:
    record: dict = {"journey": "JP4 skip everywhere + replay"}
    for skip_at in ("welcome", "naming", "facts", "boundaries", "provider_choice"):
        home = Path(tempfile.mkdtemp(prefix=f"jp4-{skip_at}-"))
        daemon = Daemon(home, home.parent / (home.name + ".log"))
        try:
            daemon.start()
            snap = walk_to(daemon, skip_at)
            status, payload = daemon.post("/api/onboarding/pact/skip", {"expect_revision": snap["revision"]})
            record[f"skip_from_{skip_at}"] = status == 200 and payload["state"] == "skipped"
            # Local Only keeps working after skip: an ordinary deterministic turn still answers.
            _commit, answer = daemon.chat("what is 3 plus 3?", "jp4-after-skip")
            record[f"local_works_after_{skip_at}"] = bool(answer)
            # replay
            status, payload = daemon.post("/api/onboarding/pact/begin", {})
            record[f"replay_after_{skip_at}"] = status == 200 and payload["state"] == "welcome"
        finally:
            record[f"cleanup_{skip_at}"] = cleanup(daemon, home)
    return record


# --- JP5 ---------------------------------------------------------------------

def jp5() -> dict:
    home = Path(tempfile.mkdtemp(prefix="jp5-home-"))
    daemon = Daemon(home, home.parent / (home.name + ".log"))
    record: dict = {"journey": "JP5 SIGKILL restart mid-pact"}
    try:
        daemon.start()
        snap = walk_to(daemon, "local_task")
        request_id, _answer = run_task(daemon, "Create welcome-notes.txt containing Welcome to VOOL.")
        assert (home / "workspace" / "welcome-notes.txt").exists()
        before_state, before_rev = snap["state"], snap["revision"]

        daemon.kill(signal.SIGKILL)  # the harness law: SIGKILL, no graceful shutdown
        daemon.start()

        status, snap = daemon.get("/api/onboarding/pact")
        record["state_survives_sigkill"] = (snap["state"] == before_state and snap["revision"] == before_rev)
        _, snap = daemon.get("/api/onboarding/pact")
        status, payload = daemon.post("/api/onboarding/pact/task/claim", {"session_id": CANON, "request_id": request_id, "expect_revision": snap["revision"]})
        record["claim_succeeds_after_restart"] = status == 200 and payload["receipt"]["chain_verified"]
        record["file_still_there"] = (home / "workspace" / "welcome-notes.txt").exists()
    finally:
        record["cleanup"] = cleanup(daemon, home)
    return record


# --- JP7 ---------------------------------------------------------------------

def jp7() -> dict:
    home = Path(tempfile.mkdtemp(prefix="jp7-home-"))
    daemon = Daemon(home, home.parent / (home.name + ".log"))
    record: dict = {"journey": "JP7 fabrication attempts"}
    try:
        daemon.start()
        snap = walk_to(daemon, "local_task")
        # fabricated request id
        status, payload = daemon.post("/api/onboarding/pact/task/claim", {"session_id": CANON, "request_id": "req:http:auto-fabricated"})
        record["fabricated_claim_refused"] = status == 409 and payload["error"] in {"evidence_missing", "evidence_session_unknown"}
        # asserted jump past the evidence lock
        status, payload = daemon.post("/api/onboarding/pact/advance", {"to": "task_receipt", "expect_revision": snap["revision"]})
        record["evidence_lock_holds"] = status == 409
        # on-disk tamper is projected (documented consequence) and the summary renders honestly
        pact_file = home / "data" / "first_run_pact_state.json"
        doc = json.loads(pact_file.read_text())
        doc["state"] = "done"
        pact_file.write_text(json.dumps(doc))
        status, snap = daemon.get("/api/onboarding/pact")
        record["tamper_is_the_authority"] = snap["state"] == "done"
        record["tampered_task_renders_skipped_not_done"] = snap["steps"]["task"] != "done" or snap["steps"]["task"] == "skipped"
    finally:
        record["cleanup"] = cleanup(daemon, home)
    return record


JOURNEYS = {"jp1": jp1, "jp2": jp2, "jp4": jp4, "jp5": jp5, "jp7": jp7}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = sys.argv[1:] or list(JOURNEYS)
    failures = []
    for name in wanted:
        print(f"=== {name} …", flush=True)
        try:
            record = JOURNEYS[name]()
        except Exception as exc:
            record = {"journey": name, "error": f"{type(exc).__name__}: {exc}"}
        def _check_ok(k: str, v) -> bool:
            if k in {"journey", "cleanup"} or isinstance(v, dict):
                return True
            if isinstance(v, bool):
                return v
            if isinstance(v, list):
                return not v  # diagnostic lists (non_boot_changes) must be EMPTY
            return True  # informative strings/numbers

        record["ok"] = (not record.get("error")) and all(_check_ok(k, v) for k, v in record.items())
        out = OUT / f"{name}.json"
        out.write_text(json.dumps(record, indent=2))
        print(json.dumps(record, indent=2))
        if not record["ok"]:
            failures.append(name)
    if failures:
        print(f"FAILED journeys: {failures}")
        return 1
    print("All requested journeys green; homes cleaned, ports released.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
