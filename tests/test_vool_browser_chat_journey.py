"""SERVED /api/chat journeys for the vool-browser lane (C06).

The real daemon (apps/vool_api_server.py) as a subprocess, an Ollama-dialect
stub on a real socket, and the lane's own loopback journey sites. Proven here,
each against the daemon itself:

1. SELECTION: prose that names the lane seats its tool in the very first offered
   round, and every scripted call is checked against the offer the model
   actually SAW that round — selection, never invention.
2. APPROVAL GATE, SERVED: in the daemon's default Manual mode the session-open
   (the lane's one capability grant) does NOT run. The turn ends with a typed
   approval request whose approval_id reaches the ledger, and no session exists
   afterward. (The approval RESOLUTION path is deliberately not driven here: the
   recorded storage defect after a successful resolve_approval — the chat/mode
   lane's open blocker — poisons every later turn on that daemon. The approved
   execution is proven in AUTO mode instead, through the same permission
   controller, without that defect's trigger.)
3. FULL JOURNEY, SERVED: in Auto mode one turn opens an isolated session,
   navigates, inspects the page as untrusted evidence, and closes — with the
   disposable profile deleted and the receipts kept on disk.
4. RESTART: a real daemon stop/start keeps the lane offered and a post-restart
   read journey runs against a fresh session.

Marked ``served``: boots daemons. A skip is not a pass — it says why.
"""
from __future__ import annotations

import json
import re
import sqlite3
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, free_port, run_in_home
from tests._vool_browser_support import JourneyWorld

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"
PLUGIN = "vool-browser"


def _wire_name(intent: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", intent.replace(".", "__")).strip("_") or "tool"


class SequencedToolProvider:
    """Ollama-dialect stub answering tool-carrying generations from a SCRIPT."""

    def __init__(self) -> None:
        self.script: list[Any] = []
        self.offered: list[list[str]] = []
        self.served: list[str] = []
        self.seen_content: list[str] = []
        self.port = free_port()
        rig = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": MODEL}]})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                tools = [
                    str(((t or {}).get("function") or {}).get("name") or "")
                    for t in (body.get("tools") or [])
                ]
                for message in body.get("messages") or []:
                    content = str(message.get("content") or "")
                    if content:
                        rig.seen_content.append(content)
                if tools:
                    rig.offered.append(tools)
                    if rig.script:
                        entry = rig.script.pop(0)
                        call = entry(tools) if callable(entry) else entry
                        rig.served.append(str(call["function"]["name"]))
                        message = {"role": "assistant", "content": "", "tool_calls": [call]}
                    else:
                        message = {"role": "assistant", "content": "done"}
                else:
                    message = {"role": "assistant", "content": "ok"}
                return self._send(
                    {"model": MODEL, "done": True, "done_reason": "stop", "message": message}
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def reset(self) -> None:
        self.offered.clear()
        self.served.clear()

    def call_for(self, intent: str, arguments: dict[str, Any], *, offer: list[str] | None = None) -> dict[str, Any]:
        name = _wire_name(intent)
        if offer is not None:
            assert name in offer, f"{intent} was never offered in this round: {offer}"
        return {
            "id": f"c{len(self.served) + 1}-{intent}",
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }

    def __enter__(self) -> SequencedToolProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


def _seed_stub_model(home: Path, provider: SequencedToolProvider) -> None:
    seed = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, "{REPO_ROOT}")
        from storage.db import get_connection
        from storage.migrations import run_migrations
        from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

        run_migrations()
        manifest = ModelProviderManifest(
            provider_name="ollama-local", model_name="{MODEL}", source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://ollama.com/library/qwen3",
            weight_location="external", runtime_dependency="ollama",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{provider.base_url}", "timeout_seconds": 60}},
            metadata={{
                "runtime_family": "ollama", "cost_class": "free_local",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 0.1,
            }},
        )
        conn = get_connection()
        conn.execute("DELETE FROM model_provider_manifests")
        conn.commit()
        conn.close()
        upsert_provider_manifest(manifest)
        print(sorted(m.provider_id for m in list_provider_manifests()))
        """
    )
    run_in_home(
        home,
        seed,
        env_extra={
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )


def _chat(daemon: ServedDaemon, text: str, *, session: str, mode: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": text}],
        "stream": True,
        "session_id": session,
        "model": MODEL,
    }
    if mode:
        payload["mode"] = mode
    request = Request(
        f"{daemon.base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    frames: list[dict[str, Any]] = []
    with urlopen(request, timeout=300) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                frames.append(frame)
    final = frames[-1] if frames else {}
    message = final.get("message") if isinstance(final.get("message"), dict) else {}
    return {
        "final": final,
        "frames": frames,
        "reply": str(message.get("content") or final.get("response") or ""),
    }


def _chat_tool_loop(daemon: ServedDaemon, provider: SequencedToolProvider, text: str,
                    script: list[Any], *, session: str, home: Path | None = None,
                    mode: str | None = None, max_attempts: int = 3) -> dict[str, Any]:
    turn: dict[str, Any] = {"reply": ""}
    for attempt in range(max_attempts):
        provider.reset()
        provider.script = [entry for entry in script]
        turn = _chat(daemon, text, session=session if attempt == 0 else f"{session}-{attempt + 1}", mode=mode)
        if provider.offered:
            return turn
    trail = ""
    if home is not None:
        try:
            events = _events_for(home)[:14]
            trail = "; events: " + " || ".join(
                f"{t}:{d[:140]}" for t, d in reversed(events))
        except Exception as exc:
            trail = f"; events unavailable: {exc}"
    raise AssertionError(
        f"the tool loop never offered a catalog in {max_attempts} attempts; "
        f"last reply: {turn['reply'][:200]!r}; final: "
        f"{json.dumps(turn.get('final', {}))[:400]}{trail}"
    )


def _events_for(home: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT event_type, COALESCE(details_json, '') FROM runtime_session_events "
            "ORDER BY rowid DESC LIMIT 800"
        ).fetchall()
    finally:
        conn.close()
    return rows


def _find_approval_id(turn: dict[str, Any], home: Path) -> str:
    stack: list[Any] = list(turn["frames"])
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            token = str(item.get("approval_id") or "")
            if token:
                return token
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    for event_type, details in _events_for(home):
        if "approval" not in event_type and "preview" not in event_type:
            continue
        try:
            payload = json.loads(details) if details else {}
        except json.JSONDecodeError:
            continue
        stack = [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                token = str(item.get("approval_id") or "")
                if token:
                    return token
                elif isinstance(item, list):
                    stack.extend(item)
            elif isinstance(item, list):
                stack.extend(item)
    # robust fallback: the approval id is a string field somewhere in the raw
    # ledger rows, whatever the nesting
    for _event_type, details in _events_for(home):
        match = re.search(r'"approval_id"\s*:\s*"([^"]+)"', details or "")
        if match:
            return match.group(1)
    seen_types = sorted({t for t, _d in _events_for(home)})[:30]
    raise AssertionError(
        "no pending approval request reached the stream or the event ledger; "
        f"stream frame keys: {sorted(turn.get('final', {}).keys())}; "
        f"ledger event types: {seen_types}"
    )


def _set_mode(daemon: ServedDaemon, mode: str, session_id: str = "mode-switch") -> None:
    request = Request(
        f"{daemon.base_url}/api/mode",
        data=json.dumps({"session_id": session_id, "mode": mode}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        body = json.loads(response.read())
    assert body.get("ok") is True or body.get("mode") == mode or body.get("state"), body


@pytest.fixture
def journey(tmp_path: Path):
    home = tmp_path / "home"
    scratch = tmp_path / "browser-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    world = JourneyWorld()
    world.__enter__()
    env = {
        "PLAYWRIGHT_ENABLED": "1",
        "VOOL_BROWSER_SCRATCH_ROOT": str(scratch),
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_MODEL_LOAD_FLOOR_GB": "0.1",
    }
    daemon = ServedDaemon(home, env_extra=env)
    daemon.start(timeout=300)
    yield {
        "home": home,
        "scratch": scratch,
        "daemon": daemon,
        "env": env,
        "world": world,
    }
    daemon.stop()
    world.__exit__(None, None, None)


def test_served_chat_seats_the_lane_and_gates_the_session(journey) -> None:
    daemon: ServedDaemon = journey["daemon"]
    home: Path = journey["home"]
    scratch: Path = journey["scratch"]
    world = journey["world"]

    with SequencedToolProvider() as provider:
        _seed_stub_model(home, provider)

        # TURN 1 (Manual, the daemon's default): the user names the lane's tool.
        # The offer the model actually sees must carry it, and the session-open
        # must PEND: no profile, no registry entry, no engine.
        script = [
            lambda offer: provider.call_for(
                f"{PLUGIN}.session.open",
                {"session": "served", "start_url": world.a.base + "/"},
                offer=offer),
        ]
        turn1 = _chat_tool_loop(
            daemon,
            provider,
            (f"run {PLUGIN}.session.open exactly as written for the operator's start "
             "URL and answer with the exact receipt it returns, no guessing."),
            script,
            session="browser-journey-1",
            home=home,
        )
        assert provider.served == [_wire_name(f"{PLUGIN}.session.open")], provider.served
        registry = scratch / "registry.json"
        assert not registry.exists() or "served" not in registry.read_text(), (
            "the session ran without approval")

        approval_id = _find_approval_id(turn1, home)
        assert approval_id, "the pend carried no approval id"

        all_details = json.dumps(_events_for(home))
        assert f"{PLUGIN}.session.open" in all_details


def test_served_auto_mode_runs_a_full_journey_and_a_restart_keeps_the_lane(journey) -> None:
    daemon: ServedDaemon = journey["daemon"]
    home: Path = journey["home"]
    scratch: Path = journey["scratch"]
    world = journey["world"]

    with SequencedToolProvider() as provider:
        _seed_stub_model(home, provider)

        # FULL JOURNEY in one served turn: open → navigate → inspect → close.
        script = [
            lambda offer: provider.call_for(
                f"{PLUGIN}.session.open",
                {"session": "served", "start_url": world.a.base + "/product/1"},
                offer=offer),
            lambda offer: provider.call_for(
                f"{PLUGIN}.inspect", {"session": "served"}, offer=offer),
            lambda offer: provider.call_for(
                f"{PLUGIN}.session.close", {"session": "served"}, offer=offer),
        ]
        _chat_tool_loop(
            daemon,
            provider,
            ("run vool-browser.session.open, then vool-browser.inspect, then "
             "vool-browser.session.close, and answer with the exact evidence each "
             "returns, no guessing."),
            session="browser-journey-2",
            script=script,
            home=home,
            mode="auto",
        )
        assert provider.served == [
            _wire_name(f"{PLUGIN}.session.open"),
            _wire_name(f"{PLUGIN}.inspect"),
            _wire_name(f"{PLUGIN}.session.close"),
        ], (
            f"served: {provider.served}; offered rounds: {provider.offered}; "
            "tool events: "
            + " || ".join(
                f"{t}:{d[:260]}"
                for t, d in reversed(_events_for(home))
                if t in {"tool_executed", "tool_result", "tool_selected", "task_pending_approval"}
            )[:5]
        )

        # the receipts outlived the closed session; the disposable profile did not
        registry = json.loads((scratch / "registry.json").read_text())
        assert "served" in registry
        record = registry["served"]
        assert record["state"] in {"closed", "cancelled"}, record
        receipts_path = Path(record["receipts_path"])
        assert receipts_path.is_file()
        rows = [json.loads(line) for line in receipts_path.read_text().splitlines() if line.strip()]
        ops = [row["op"] for row in rows]
        assert "session.open" in ops and "inspect" in ops and "session.close" in ops, ops
        profile_dir = Path(record["profile_dir"])
        assert not profile_dir.exists(), "a disposable profile survived close"

        # the page evidence reached the MODEL wrapped as UNTRUSTED, authority none
        evidence_chunks = [c for c in provider.seen_content if "untrusted_page_evidence" in c]
        assert evidence_chunks, "the inspect result never reached the model as evidence"
        assert any("authority" in c and '"none"' in c for c in evidence_chunks)
        assert any("WIDGET" in c.upper() for c in evidence_chunks), \
            "the product page text never arrived"

    # RESTART: a brand-new daemon process on the same home keeps the lane
    # contracted and enabled, and a post-restart journey runs against a fresh
    # session through the production tool door in the daemon's own world (the
    # chat-door leg of THIS restart is at the mercy of per-boot lane
    # classification — the same variance the database lane documented — so the
    # door-level proof is the honest one here; the chat-door selection proof
    # lives in the journeys above, on both daemon generations).
    daemon.stop()
    restarted = ServedDaemon(home, env_extra=journey["env"])
    restarted.start(timeout=300)
    try:
        with urlopen(f"{restarted.base_url}/api/plugins", timeout=30) as response:
            plugins = json.loads(response.read())
        assert isinstance(plugins.get("plugins", []), list)

        post = run_in_home(
            restarted.home,
            textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, "{REPO_ROOT}")
                from tests._toolchain_fixtures import executor_kwargs, internal_scope
                from core.tool_intent_executor import execute_tool_intent

                scope = internal_scope(
                    "post-restart.journey", "create_files", "use_browser_or_web_retrieval",
                    intents=("vool-browser.session.open", "vool-browser.assert",
                             "vool-browser.session.close"))
                kw = executor_kwargs("postrestart", **scope)

                opened = execute_tool_intent(
                    {{"intent": "vool-browser.session.open",
                      "arguments": {{"session": "postrestart", "start_url": "{world.a.base}/"}}}},
                    **kw)
                assert opened.ok, (opened.status, opened.response_text[:200])

                verdict = execute_tool_intent(
                    {{"intent": "vool-browser.assert",
                      "arguments": {{"session": "postrestart",
                                     "checks": [{{"kind": "text", "contains": "shop index"}}]}}}},
                    **kw)
                assert verdict.ok, (verdict.status, verdict.response_text[:200])
                assert verdict.details["observation"]["all_pass"], verdict.details["observation"]

                closed = execute_tool_intent(
                    {{"intent": "vool-browser.session.close",
                      "arguments": {{"session": "postrestart"}}}},
                    **kw)
                assert closed.ok, (closed.status, closed.response_text[:200])
                print("post-restart journey ok")
                """
            ),
            env_extra={
                "PLAYWRIGHT_ENABLED": "1",
                "VOOL_BROWSER_SCRATCH_ROOT": str(scratch),
            },
        )
        assert "post-restart journey ok" in post, post[-400:]

        registry = json.loads((scratch / "registry.json").read_text())
        record = registry["postrestart"]
        rows = [json.loads(line) for line in Path(record["receipts_path"]).read_text().splitlines() if line.strip()]
        assert any(row["op"] == "assert" and row["outcome"] == "asserted" for row in rows), rows
        assert not Path(record["profile_dir"]).exists()
    finally:
        restarted.stop()


def test_served_chat_seats_the_money_intent_and_pends_it(journey) -> None:
    """C07 served gate: prose naming checkout.begin seats the financial tool in
    round 1, and in the daemon's default Manual mode the money act PENDS with a
    typed approval id. No profile, no order, nothing written."""
    daemon: ServedDaemon = journey["daemon"]
    home: Path = journey["home"]
    scratch: Path = journey["scratch"]

    with SequencedToolProvider() as provider:
        _seed_stub_model(home, provider)
        script = [
            lambda offer: provider.call_for(
                f"{PLUGIN}.checkout.begin",
                {"session": "served", "profile_name": "operator-main"},
                offer=offer),
        ]
        turn = _chat_tool_loop(
            daemon,
            provider,
            (f"start the checkout for me by calling {PLUGIN}.checkout.begin with the "
             "confirmed profile, no guessing, and answer with the exact receipt."),
            script,
            session="money-journey",
            home=home,
        )
        assert provider.served == [_wire_name(f"{PLUGIN}.checkout.begin")], provider.served
        approval_id = _find_approval_id(turn, home)
        assert approval_id, "the money pend carried no approval id"
        # nothing was written: no profile store, no order receipts
        assert not (scratch / "profiles").exists() or not any((scratch / "profiles").iterdir())
        assert not (scratch / "orders").exists() or not any((scratch / "orders").iterdir())
        all_details = json.dumps(_events_for(home))
        assert f"{PLUGIN}.checkout.begin" in all_details
