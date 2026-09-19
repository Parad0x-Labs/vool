"""The served-reality rig: one staged app, one isolated home, one stub, one truth.

Composes everything a case needs and enforces the bench's own laws:

* the product under test is ALWAYS the staged exact-SHA app (never a tree);
* the home/workspace are per-run throwaways;
* every HTTP exchange (bench→daemon, provider stub→daemon) is retained as
  exact wire bytes;
* each case gets a fresh workspace and its own chat namespace so cases
  cannot contaminate each other;
* teardown is total (stub down, daemon tree dead, artifacts flushed) and
  runs even when cases raise.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ops.served_reality.appstage import StagedApp, clone_staging, stage_app
from ops.served_reality.classify import BenchError, ProductAssertionError
from ops.served_reality.daemon import DaemonHandle, launch_daemon, read_pending_approvals
from ops.served_reality.provider_stub import ProviderStub, StubPlan
from ops.served_reality.schema import (
    FAILURE_CLASS_VOOL,
    VERDICT_FAIL,
    VERDICT_PASS,
    CaseResult,
    TurnIdentity,
    WireRef,
)
from ops.served_reality.wire import HttpExchange, WireClient, WireLog

TURN_TIMEOUT_S = 90.0


def discover_python() -> str:
    """A python that can run the product (needs uvicorn at minimum)."""
    import os
    import sys

    candidates = []
    env_pin = os.environ.get("VOOL_SR_PYTHON")
    if env_pin:
        candidates.append(env_pin)
    candidates.append(sys.executable)
    candidates.append("/tmp/vool-venv312/bin/python")
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        import subprocess

        try:
            probe = subprocess.run(
                [candidate, "-c", "import uvicorn"],
                capture_output=True,
                timeout=30,
            )
            if probe.returncode == 0:
                return candidate
        except Exception:
            continue
    raise BenchError("no python with uvicorn found; set VOOL_SR_PYTHON")


@dataclass
class TurnRecord:
    """One served turn, parsed for identity extraction."""

    exchange: HttpExchange
    status: int | None
    payload: dict[str, Any]
    content: str
    session_id: str
    commit: dict[str, Any]
    events: list[dict[str, Any]]
    receipts: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.content)


class CaseContext:
    """The API a corpus case drives. One instance per case."""

    def __init__(self, rig: BenchRig, result: CaseResult) -> None:
        self.rig = rig
        self.result = result
        self.identities: list[TurnIdentity] = []
        self._workspaces: list[Path] = []

    # -- stub control ----------------------------------------------------
    def set_stub_rules(self, plan: StubPlan) -> None:
        plan.apply(self.rig.stub)

    def stub_answers(self, *contents: str) -> None:
        from ops.served_reality.provider_stub import StubRule

        StubPlan().rules.extend(StubRule(content=c) for c in contents).apply(self.rig.stub)

    # -- workspace -------------------------------------------------------
    def workspace(self) -> str:
        path = self.rig.run_dir / "workspaces" / self.result.case_id
        path.mkdir(parents=True, exist_ok=True)
        self._workspaces.append(path)
        return str(path)

    # -- product surfaces --------------------------------------------------
    def chat(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        turn_id: str | None = None,
        workspace: str | None = None,
        timeout_s: float | None = None,
        extra_body: dict[str, Any] | None = None,
        collect_identity: bool = True,
    ) -> TurnRecord:
        chat_ns = chat_id or f"sr-{self.result.case_id}"
        if turn_id is None:
            turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        exchange = self.rig.client.post_chat(
            text,
            chat_id=chat_ns,
            turn_id=turn_id,
            workspace=workspace or self.workspace(),
            extra_body=extra_body,
            timeout_s=timeout_s if timeout_s is not None else TURN_TIMEOUT_S,
        )
        if exchange.error:
            raise ProductAssertionError(
                f"chat transport failed: {exchange.error}",
                detail=f"case={self.result.case_id} chat_id={chat_ns}",
            )
        payload = exchange.response_json()
        if not isinstance(payload, dict):
            raise ProductAssertionError(
                "chat response is not a JSON object",
                detail=exchange.response_text()[:300],
            )
        message = payload.get("message")
        content = str((message or {}).get("content") or "") if isinstance(message, dict) else ""
        session_id = str(payload.get("vool_session_id") or "")
        commit = dict(payload.get("vool_response_commit") or {})
        events = self.rig.client.events(session_id) if session_id else []
        receipts = self.rig.client.receipts(session_id) if session_id else {}
        record = TurnRecord(
            exchange=exchange,
            status=exchange.status,
            payload=payload,
            content=content,
            session_id=session_id,
            commit=commit,
            events=events,
            receipts=receipts,
        )
        if collect_identity:
            identity = extract_identity(record, client_turn_id=turn_id)
            self.identities.append(identity)
            self.result.identities.append(identity)
        return record

    def cancel(self, session_id: str, turn_id: str) -> HttpExchange:
        return self.rig.client.post(
            "/api/chat/cancel", {"session_id": session_id, "turn_id": turn_id}, timeout_s=15.0
        )

    def resolve_approval(self, session_id: str, approval_id: str, decision: str) -> HttpExchange:
        return self.rig.client.post(
            "/api/mode",
            {"op": "resolve_approval", "session_id": session_id, "approval_id": approval_id, "decision": decision},
            timeout_s=15.0,
        )

    def pending_approvals(self) -> list[dict[str, Any]]:
        return read_pending_approvals(self.rig.home)

    def memory_entries(self) -> list[dict[str, Any]]:
        exchange = self.rig.client.get("/api/memory/entries?limit=200", timeout_s=10.0)
        if exchange.error or exchange.status != 200:
            raise ProductAssertionError(
                f"memory entries read failed: {exchange.error or exchange.status}"
            )
        payload = exchange.response_json() or {}
        return list(payload.get("entries") or [])

    def memory_forget(self, record_id: str) -> HttpExchange:
        return self.rig.client.post("/api/memory/forget", {"record_id": record_id}, timeout_s=15.0)

    def council_convene(self, body: dict[str, Any], timeout_s: float = 60.0) -> HttpExchange:
        return self.rig.client.post("/api/council/convene", body, timeout_s=timeout_s)

    def council_status(self, run_id: str, timeout_s: float = 15.0) -> HttpExchange:
        return self.rig.client.get(f"/api/council/runs?run_id={run_id}", timeout_s=timeout_s)

    def restart_daemon(self) -> None:
        """Stop and relaunch the SAME staged app against the SAME home.

        For the restart-continuity case: the product's persisted state (chat
        log, receipts, sessions) must survive its own daemon dying.
        """
        self.rig.restart_daemon()

    # -- assertions --------------------------------------------------------
    def record(self, name: str, ok: bool, *, expected: Any = None, observed: Any = None, note: str = "") -> bool:
        self.result.assertion(name, ok, expected=expected, observed=observed, note=note)
        return ok

    def require(
        self,
        name: str,
        ok: bool,
        *,
        expected: Any = None,
        observed: Any = None,
        note: str = "",
        error: type[ProductAssertionError] | None = None,
    ) -> None:
        self.result.assertion(name, ok, expected=expected, observed=observed, note=note)
        if not ok:
            raise (error or ProductAssertionError)(
                f"assertion failed: {name}",
                detail=f"expected={expected!r} observed={observed!r} note={note}",
            )

    def require_eq(self, name: str, expected: Any, observed: Any, *, note: str = "") -> None:
        self.require(name, expected == observed, expected=expected, observed=observed, note=note)

    def require_in(self, name: str, needle: str, haystack: str, *, note: str = "") -> None:
        ok = bool(needle) and needle in (haystack or "")
        self.require(name, ok, expected=f"contains {needle!r}", observed=(haystack or "")[:300], note=note)

    def require_not_in(self, name: str, needle: str, haystack: str, *, note: str = "") -> None:
        ok = needle not in (haystack or "")
        self.require(name, ok, expected=f"does not contain {needle!r}", observed=(haystack or "")[:300], note=note)


def _event_field(event: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in event and event[name] not in (None, ""):
            return event[name]
    return None


def extract_identity(record: TurnRecord, *, client_turn_id: str | None = None) -> TurnIdentity:
    """Pull the full identity set out of one served turn's own records."""
    identity = TurnIdentity()
    commit = record.commit or {}
    identity.request_id = str(commit.get("request_id") or "") or None
    identity.finalization_id = str(commit.get("finalization_id") or "") or None
    identity.turn_id = str(commit.get("turn_id") or "") or None
    identity.client_turn_id = client_turn_id
    identity.session_id = record.session_id or None
    identity.content_hash = str(commit.get("content_hash") or "") or None

    tool_names: set[str] = set()
    effect_ids: list[str] = []
    for event in record.events:
        etype = str(event.get("event_type") or "")
        if etype == "runtime_attempt_created" and identity.attempt_id is None:
            identity.attempt_id = str(_event_field(event, "attempt_id", "attempt") or "") or None
            msg = str(event.get("message") or "")
            if "attempt-" in msg and identity.attempt_id is None:
                token = msg.split("attempt-")[1].split()[0]
                identity.attempt_id = f"attempt-{token.rstrip(' .,()')}"
        if etype in {"model_lane_selected", "model_lane_started"} and identity.model_lane is None:
            identity.model_lane = str(_event_field(event, "model_lane", "lane") or "") or None
            msg = str(event.get("message") or "")
            if identity.model_lane in (None, "") and msg.startswith("Selected "):
                identity.model_lane = msg.split("Selected ", 1)[1].split(" for")[0].strip()
        if etype == "model_usage" and identity.model_call_id is None:
            identity.model_call_id = str(_event_field(event, "model_call_id", "call_id") or "") or None
        if etype.startswith("tool_") or etype.endswith("_tool") or "_tool_" in etype:
            name = str(_event_field(event, "tool_name", "tool") or "") or etype
            tool_names.add(name)
        for key in ("effect_id", "logical_effect_id"):
            value = event.get(key)
            if value:
                effect_ids.append(str(value))
        if etype == "turn.trace_completed":
            identity.turn_id = identity.turn_id or str(_event_field(event, "chat_id", "turn_id") or "") or None
    identity.tool_names = sorted(tool_names)
    identity.effect_ids = effect_ids

    receipt_rows = list((record.receipts or {}).get("receipts") or [])
    identity.receipt_ids = [str(row.get("receipt_id") or "") for row in receipt_rows if row.get("receipt_id")]
    for row in receipt_rows:
        for tool in row.get("executed_tools") or []:
            tool_names.add(str(tool))
    identity.tool_names = sorted(tool_names)
    evidence_refs: list[str] = []
    web0 = record.payload.get("web0_receipt")
    if isinstance(web0, dict) and web0.get("receipt_id"):
        evidence_refs.append(f"web0:{web0['receipt_id']}")
    if identity.content_hash:
        evidence_refs.append(f"commit:{identity.content_hash}")
    for row in receipt_rows:
        if row.get("content_hash"):
            evidence_refs.append(f"receipt:{row['content_hash']}")
    identity.evidence_refs = evidence_refs
    return identity


class BenchRig:
    """Owns the staged app, stub, daemon and wire logs for one bench run."""

    def __init__(
        self,
        *,
        repo: Path,
        sha: str,
        run_dir: Path,
        python_bin: str | None = None,
        extra_daemon_env: dict[str, str] | None = None,
        app_dir_override: Path | None = None,
        keep_going_after_daemon_death: bool = True,
    ) -> None:
        self.repo = repo
        self.sha = sha
        self.run_dir = run_dir
        self.python_bin = python_bin or discover_python()
        self.extra_daemon_env = extra_daemon_env or {}
        self.app_dir_override = app_dir_override
        self.keep_going = keep_going_after_daemon_death
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.wire_log = WireLog(self.run_dir, "daemon")
        self.stub: ProviderStub | None = None
        self.daemon: DaemonHandle | None = None
        self.staged: StagedApp | None = None
        self.staged_for_manifest: dict[str, Any] = {}
        self.home = self.run_dir / "home"
        self.workspace_root = self.run_dir / "workspace-root"
        self.client: WireClient | None = None
        self._launch_count = 0

    # -- lifecycle ---------------------------------------------------------
    def setup(self) -> None:
        # A deterministic run requires a fresh home: persisted provider
        # manifests (e.g. a stale stub base_url from an earlier run) would
        # route model lanes at a dead port. The bench owns this home.
        import shutil as _shutil

        if self.home.exists():
            _shutil.rmtree(self.home)
        if self.app_dir_override is not None:
            # Mutation runs launch a pre-patched copy; the SHA claim is about
            # the pristine staging it was cloned from.

            self.staged_for_manifest = {"path": str(self.app_dir_override), "mutated": True}
            app_dir = self.app_dir_override
        else:
            self.staged = stage_app(self.repo, self.sha, self.run_dir)
            self.staged_for_manifest = {
                "path": str(self.staged.path),
                "sha": self.staged.sha,
                "sha_full": self.staged.sha_full,
                "branch": self.staged.branch,
                "archive_sha256": self.staged.archive_sha256,
                "file_count": self.staged.file_count,
            }
            app_dir = self.staged.path
        stub_wire_path = self.run_dir / "wire-stub.jsonl"
        self.stub = ProviderStub(model_id="served-reality-stub", wire_log_path=str(stub_wire_path))
        self.stub.start()
        bundle_manifest = self.home / "bundle_manifest.json"
        bundle_manifest.parent.mkdir(parents=True, exist_ok=True)
        bundle_manifest.write_text(json.dumps({"selected_model": "served-reality-stub"}), encoding="utf-8")
        # The deterministic "cloud" lane: a tether-remote manifest pointed at
        # the SAME stub (openai-compatible). It carries the paid_cloud tag in
        # the product's own metadata, so spend-consent gating is exercised
        # for real — while no paid network can ever be reached.
        assert self.stub.port is not None
        self.extra_daemon_env = {
            "TETHER_API_KEY": "sr-bench-stub-key-not-a-real-credential",
            "TETHER_BASE_URL": f"http://127.0.0.1:{self.stub.port}/v1",
            "TETHER_MODEL": "served-reality/cloud-stub",
            **self.extra_daemon_env,
        }
        self._launch_daemon(app_dir=app_dir, bundle_manifest=bundle_manifest)

    def _launch_daemon(self, *, app_dir: Path, bundle_manifest: Path, expected_sha: str | None = None) -> None:
        assert self.stub is not None
        self._launch_count += 1
        # The daemon writes its own logs under the home so a restart keeps
        # appending to the same evidence trail.
        self.daemon = launch_daemon(
            app_dir=app_dir,
            home=self.home,
            workspace=self.workspace_root,
            python_bin=self.python_bin,
            run_dir=self.run_dir,
            wire_log=self.wire_log,
            stub_port=self.stub.port,
            stub_model="served-reality-stub",
            bundle_manifest=bundle_manifest,
            expected_sha=expected_sha if expected_sha is not None else (self.staged.sha if self.staged else None),
            extra_env=self.extra_daemon_env,
            boot_timeout_s=180.0,
        )
        self.client = WireClient(
            f"http://127.0.0.1:{self.daemon.port}", self.wire_log, default_timeout_s=TURN_TIMEOUT_S
        )

    def restart_daemon(self, *, app_dir_override: Path | None = None) -> None:
        if self.daemon is None:
            raise BenchError("restart requested before any daemon launch")
        app_dir = app_dir_override or self.daemon.app_dir
        if self.daemon.alive:
            code = self.daemon.terminate()
            if code not in (0, None, -15, 15):
                # SIGTERM'd uvicorn may exit non-zero; record honestly but
                # continue — restart continuity is about state, not exit code.
                pass
        bundle_manifest = self.home / "bundle_manifest.json"
        if app_dir_override is not None:
            # Mutation runs boot a patched CLONE; the healthz SHA gate must
            # not refuse it (the mutation's provenance is the clone record).
            self._launch_daemon(app_dir=app_dir, bundle_manifest=bundle_manifest, expected_sha=None)
        else:
            self._launch_daemon(app_dir=app_dir, bundle_manifest=bundle_manifest)

    def teardown(self) -> None:
        if self.daemon is not None:
            try:
                self.daemon.terminate()
            finally:
                self.daemon = None
        if self.stub is not None:
            self.stub.stop()
            self.stub = None
        self.wire_log.close()

    # -- case driving -------------------------------------------------------
    def run_case(self, case: Case, provider_mode: str = "deterministic", product_sha: str = "") -> CaseResult:
        result = CaseResult(
            case_id=case.case_id,
            title=case.title,
            required=case.required,
            verdict=VERDICT_PASS,
            bench_sha=self.staged.sha if self.staged else (self.sha[:9] if self.sha else ""),
            product_sha=product_sha or (self.staged.sha if self.staged else ""),
            provider_mode=provider_mode,
            started_at=time.time(),
        )
        from ops.served_reality.classify import classify_exception

        ctx = CaseContext(self, result)
        stub_state = {}
        try:
            if self.daemon is not None and not self.daemon.alive:
                self.restart_daemon()
            if self.daemon is None:
                raise BenchError("daemon was never launched")
            if self.stub is not None:
                stub_state["calls_before"] = len(self.stub.chat_calls())
            case.run(ctx)
            result.verdict = VERDICT_PASS
        except Exception as exc:
            result.verdict = VERDICT_FAIL
            result.failure_class = classify_exception(exc)
            result.reason = str(exc)[:500]
            result.detail = str(getattr(exc, "detail", "") or "")[:2000]
        result.duration_s = time.time() - result.started_at
        if self.stub is not None:
            stub_state["calls_after"] = len(self.stub.chat_calls())
            result.assertion(
                "stub_model_consulted",
                stub_state["calls_after"] > stub_state.get("calls_before", 0)
                or case.accepts_no_model_call,
                expected=">0 stub model calls during case (unless case accepts none)",
                observed=stub_state["calls_after"] - stub_state.get("calls_before", 0),
            )
            if result.verdict == VERDICT_PASS and not case.accepts_no_model_call:
                if stub_state["calls_after"] <= stub_state.get("calls_before", 0):
                    # The turn never reached the model lane: PASS would be a
                    # lie about what was exercised.
                    result.verdict = VERDICT_FAIL
                    result.failure_class = FAILURE_CLASS_VOOL
                    result.reason = "no stub model call was made during the case"
        # wire refs for the case window
        exchanges = self.wire_log.exchanges()
        result.wire_refs = [
            WireRef(
                exchange_id=e.exchange_id,
                direction="daemon",
                method=e.method,
                path=e.path,
                status=e.status,
                artifacts_file=self.wire_log.jsonl_path.name,
                byte_range=[0, 0],
            )
            for e in exchanges[-40:]
        ]
        return result

    # -- home forensics (bench-owned home; mutations are labeled as such) ----
    def cas_chunk_root(self) -> Path:
        return self.home / "data" / "cas_chunks"

    def cas_verify_all(self) -> list[dict[str, Any]]:
        """Bench-side integrity check: every chunk file must hash to its name."""
        mismatches: list[dict[str, Any]] = []
        root = self.cas_chunk_root()
        if not root.is_dir():
            return mismatches
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != path.name:
                mismatches.append({"path": str(path.relative_to(root)), "named": path.name, "actual": digest})
        return mismatches

    def daemon_stderr_tail(self, limit: int = 4000) -> str:
        if self.daemon is None:
            return ""
        try:
            return self.daemon.stderr_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""


@dataclass
class Case:
    case_id: str
    title: str
    required: bool = True
    accepts_no_model_call: bool = False
    run: Callable[[CaseContext], None] = field(default=lambda ctx: None)


def mutation_app_clone(rig: BenchRig, name: str) -> Path:
    """A throwaway writable copy of the pristine staging for mutation runs."""
    if rig.staged is None:
        raise BenchError("mutation clone requires a pristine staging")
    target = rig.run_dir / f"app-mutated-{name}"
    return clone_staging(rig.staged, target)
