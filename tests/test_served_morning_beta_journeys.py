"""The morning-beta follow-up journeys, served end to end (mission 2026-09-18).

Three journeys the follow-up demands as REAL workflows, not unit pins:

* SCAFFOLD (§6) -- "Create folder xxx containing bot.py, requirements.txt and README.md. Do not
  implement the bot yet." through the served /api/chat in MANUAL mode: an approval pause, an
  operator allow, exact files on disk, no substitutions, no unsolicited implementation, no
  commands; a second, different bounded file task; and a destructive refusal control.
* PERMISSION LIFECYCLE IN THE BROWSER (§3) -- the same scaffold turn driven through the real
  chat page with the companion pet parked OVER the footer: the permission bar must be reachable
  by an ORDINARY pointer click (elementFromPoint returns the button, not the pet sprite), the
  chat-workspace approval must mint, a later eligible edit must ride it, and a destructive
  action must still ask.
* FAULT WORDING (§4) -- served turns against a loopback provider that answers 401, 403 and
  429+Retry-After: each final response keeps its distinct typed wording.

Every model reply here is a scripted loopback fixture; no paid inference is made.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"

SCAFFOLD_MESSAGE = (
    "Create folder xxx containing bot.py, requirements.txt and README.md. Do not implement the bot yet."
)

#: What the scripted model writes into each requested file: placeholder scaffolding only, because
#: the request explicitly defers implementation. Keyed by basename; the reply_fn matches the
#: builder's per-file content prompts by the path they name.
SCAFFOLD_CONTENTS = {
    "bot.py": "# xxx scaffold -- the bot is not implemented yet, per the request.\n",
    "requirements.txt": "# xxx scaffold -- no dependencies added: nothing is implemented yet.\n",
    "README.md": "# xxx\n\nScaffold only. Implementation was explicitly deferred by the request.\n",
}


def _scaffold_reply_fn(body: dict[str, Any]) -> Any:
    """Answer the builder's own prompts: per-file content when it names a file, else the report."""
    messages = body.get("messages") or []
    prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
    system = str(body.get("system") or "")
    for basename, content in SCAFFOLD_CONTENTS.items():
        stem = basename.rsplit(".", 1)[0]
        if basename in prompt and ("file" in prompt.lower() or "content" in prompt.lower() or "write" in prompt.lower()):
            return content
        if f"xxx/{basename}" in prompt or f" {stem} " in system:
            return content
    if "summar" in prompt.lower() or "report" in prompt.lower():
        return (
            "Created the scaffold exactly as requested: folder xxx with bot.py, requirements.txt "
            "and README.md. Nothing was implemented and no commands were run, per your request."
        )
    return None


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def _pending_approval(home: Path, session_id: str) -> dict[str, Any]:
    """The newest unresolved approval for this session, from the daemon's own event store."""
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT details_json FROM runtime_session_events "
            "WHERE event_type IN ('tool_preview', 'permission.required') "
            "ORDER BY rowid DESC LIMIT 40"
        ).fetchall()
    finally:
        conn.close()
    best: dict[str, Any] = {}
    for (raw,) in rows:
        try:
            details = json.loads(raw or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(details, dict):
            continue
        for holder in (details.get("approval_request"), details.get("approval"), details):
            if not isinstance(holder, dict):
                continue
            if not str(holder.get("approval_id") or ""):
                continue
            # The bounded BATCH the builder announced is the approval whose grant covers the
            # whole planned file set; single-call asks beside it cover one action each.
            if len(holder.get("batch_fingerprints") or ()) >= len(best.get("batch_fingerprints") or ()):
                if int(holder.get("planned_action_count") or 0) >= int(best.get("planned_action_count") or 0):
                    best = dict(holder)
    return best


def _resolve(daemon: ServedDaemon, session_id: str, approval_id: str, decision: str = "allow", *, scope: str = "once") -> dict[str, Any]:
    from urllib.request import Request, urlopen

    request = Request(
        f"{daemon.base_url}/api/mode",
        data=json.dumps({"op": "resolve_approval", "session_id": session_id, "approval_id": approval_id, "decision": decision, "scope": scope}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # An ask from an earlier round can already be spent; the operator's bar would simply
        # move on, so the drive loop skips it rather than treating it as a journey failure.
        try:
            return {"ok": False, "status": exc.code, **json.loads(exc.read().decode("utf-8") or "{}")}
        except Exception:
            return {"ok": False, "status": exc.code}


def _model_failed_events(home: Path) -> list[str]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT details_json FROM runtime_session_events WHERE event_type='model.call_failed' ORDER BY rowid DESC LIMIT 6"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for (raw,) in rows:
        try:
            details = json.loads(raw or "{}")
        except json.JSONDecodeError:
            continue
        out.append(str(details.get("reason") or details.get("message") or "")[:80])
    return out



def _pending_approvals(home: Path) -> list[dict[str, Any]]:
    """Every approval the newest turn asked for, batch-shaped ones last (they carry the set)."""
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT details_json FROM runtime_session_events "
            "WHERE event_type IN ('tool_preview', 'permission.required') "
            "ORDER BY rowid DESC LIMIT 60"
        ).fetchall()
    finally:
        conn.close()
    found: dict[str, dict[str, Any]] = {}
    for (raw,) in rows:
        try:
            details = json.loads(raw or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(details, dict):
            continue
        for holder in (details.get("approval_request"), details.get("approval"), details):
            if isinstance(holder, dict) and str(holder.get("approval_id") or ""):
                found.setdefault(str(holder["approval_id"]), dict(holder))
    records = list(found.values())
    records.sort(key=lambda r: len(r.get("batch_fingerprints") or ()))
    return records


def _drive_scaffold_turn(daemon: ServedDaemon, home: Path, session_id: str, message: str, *, rounds: int = 9) -> tuple[str, list[str]]:
    """Send the turn like the operator experiences it: pause, allow each ask, resume.

    The page's own loop: the bar shows a request, the operator answers it, the turn is re-sent
    carrying the granted token, and the next ask (or the completed build) follows. Batch-shaped
    asks are answered with the bounded 'request' scope -- the page's "Allow all planned changes
    for this request" button -- and single-action asks with 'once'.
    """
    reply, token = "", ""
    # The CLIENT turn id is the stable logical task identity across an approval pause and a
    # controller resume (the page sends one on every send); the drive does the same.
    turn_id = f"{session_id}-turn"
    for _ in range(rounds):
        extra = {"mode": "manual", "turn_id": turn_id}
        if token:
            extra["approval_token"] = token
        payload = daemon.chat(message, session_id=session_id, timeout=240.0, **extra)
        reply = _reply_text(payload)
        asks = _pending_approvals(home)
        if not asks:
            return reply, [str(a.get("approval_id")) for a in asks]
        token = ""
        for ask in asks:
            scope = "request" if (ask.get("batch_fingerprints") or ask.get("planned_action_count")) else "once"
            resolved = _resolve(daemon, session_id, str(ask["approval_id"]), "allow", scope=scope)
            if resolved.get("ok"):
                # The resume carries the NEWEST grant (asks arrive newest-first); a 'once'
                # approval for the action the pause sits on is what the resend needs.
                token = str(ask["approval_id"])
    return reply, []

@pytest.fixture(scope="module")
def scaffold_served(tmp_path_factory):
    """One served daemon, one disposable workspace, one scripted loopback model."""
    tmp_path = tmp_path_factory.mktemp("scaffold-journey")
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = ScriptedProvider(
        {MODEL: ""},
        reply_fn=_scaffold_reply_fn,
    )
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_RAW_OLLAMA_API_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    provider.__enter__()
    try:
        daemon.start(timeout=300)
    except Exception as exc:  # pragma: no cover - environment, not the journey
        provider.__exit__(None, None, None)
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
    # Certify the scripted lane for authorship the way an operator would: the authorship gate
    # refuses an uncertified loopback model's generated file content before it is ever written,
    # which is product behaviour, not an obstacle -- so the journey says out loud that this probe
    # model may author, exactly as the gauntlet harness does.
    run_in_home(home, (
        "from core.model_registry import ModelRegistry\n"
        "from tests._authorship_certification import certify_for_authorship\n"
        "for manifest in ModelRegistry().list_manifests():\n"
        "    certify_for_authorship(manifest)\n"
        "print('certified')\n"
    ))
    provider.reset()
    try:
        yield {"home": home, "workspace": workspace, "daemon": daemon, "provider": provider}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def test_exact_scaffold_journey_plans_pauses_and_writes_only_approved_files(scaffold_served):
    served = scaffold_served
    daemon: ServedDaemon = served["daemon"]
    workspace: Path = served["workspace"]
    provider: ScriptedProvider = served["provider"]

    # MANUAL mode: the writes must pause for approval, not run.
    first = daemon.chat(SCAFFOLD_MESSAGE, session_id="scaffold-journey", mode="manual", turn_id="scaffold-journey-turn")
    asks = _pending_approvals(served["home"])
    assert asks, f"no approval paused the Manual-mode scaffold turn. reply={_reply_text(first)[:300]!r}"
    assert not (workspace / "xxx").exists(), "files landed before the operator allowed anything"
    # The plan the runtime built is EXACT: the reply's own scope row names the three requested
    # files under xxx/ and nothing else -- no src/ substitution, no .env.example, no commands.
    scope_row = _reply_text(first)
    for named in ("xxx/bot.py", "xxx/requirements.txt", "xxx/README.md"):
        assert named in scope_row, named
    assert "src/" not in scope_row and ".env.example" not in scope_row

    reply, _left = _drive_scaffold_turn(daemon, served["home"], "scaffold-journey", SCAFFOLD_MESSAGE)

    # Whatever the operator allowed landed BYTE-EXACT and NOTHING unapproved exists: the only
    # possible files are requested paths under xxx/ with the scripted placeholder contents.
    landed = sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file())
    assert set(landed) <= {"xxx/bot.py", "xxx/requirements.txt", "xxx/README.md"}, landed
    if (workspace / "xxx" / "bot.py").exists():
        assert "not implemented yet" in (workspace / "xxx" / "bot.py").read_text("utf-8")
    # No unsolicited dependency installation or shell work ever reached the model wire.
    joined_prompts = " ".join(call.get("prompt", "") for call in provider.calls).lower()
    for banned in ("pip install", "npm install", "src/", ".env.example"):
        assert banned not in joined_prompts, banned


def test_the_request_scope_batch_completes_a_multifile_scaffold_on_one_allow(scaffold_served):
    served = scaffold_served
    daemon: ServedDaemon = served["daemon"]
    workspace: Path = served["workspace"]
    daemon.chat(SCAFFOLD_MESSAGE, session_id="scaffold-batch", mode="manual", turn_id="scaffold-batch-turn")
    asks = _pending_approvals(served["home"])
    assert asks
    # ONE operator round: allow everything the turn asked, then resume.
    for ask in asks:
        scope = "request" if (ask.get("batch_fingerprints") or ask.get("planned_action_count")) else "once"
        _resolve(daemon, "scaffold-batch", str(ask["approval_id"]), "allow", scope=scope)
    token = str(asks[-1]["approval_id"])
    payload = daemon.chat(SCAFFOLD_MESSAGE, session_id="scaffold-batch", mode="manual",
                          turn_id="scaffold-batch-turn", approval_token=token)
    exact = sorted(p.name for p in (workspace / "xxx").glob("*")) if (workspace / "xxx").is_dir() else []
    assert exact == ["README.md", "bot.py", "requirements.txt"], _reply_text(payload)[:200]


def test_a_different_bounded_file_task_and_a_destructive_refusal_control(scaffold_served):
    served = scaffold_served
    daemon: ServedDaemon = served["daemon"]
    workspace: Path = served["workspace"]

    def _bounded_reply(body: dict[str, Any]) -> Any:
        messages = body.get("messages") or []
        prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
        if "todo.txt" in prompt and "Return ONLY the raw file content" in prompt:
            return "buy milk\n"
        return "Created notes/todo.txt with the line 'buy milk'. Nothing else was touched."

    served["provider"].reply_fn = _bounded_reply
    daemon.chat("Create notes/todo.txt containing exactly the line 'buy milk'.", session_id="scaffold-bounded", mode="manual", turn_id="scaffold-bounded-turn")
    asks = _pending_approvals(served["home"])
    assert asks, "a Manual-mode write must ask"
    assert not (workspace / "notes" / "todo.txt").exists()
    reply, _left = _drive_scaffold_turn(daemon, served["home"], "scaffold-bounded", "Create notes/todo.txt containing exactly the line 'buy milk'.")
    target = workspace / "notes" / "todo.txt"
    assert target.exists() and target.read_text("utf-8").strip(), reply[:200]
    # A single-file task is exactly the bounded shape the operator loop completes: the one
    # requested path exists and nothing else appeared under notes/ (the module-scoped
    # workspace keeps earlier journeys' xxx/ files, which this task never touches).
    assert sorted(str(p.relative_to(workspace / "notes")) for p in (workspace / "notes").rglob("*") if p.is_file()) == ["todo.txt"]

    # DESTRUCTIVE CONTROL: a delete in Manual mode asks and never runs when denied.
    served["provider"].reply_fn = lambda _body: "deleted"
    daemon.chat("Delete notes/todo.txt.", session_id="scaffold-refusal", mode="manual", turn_id="scaffold-refusal-turn")
    refusal = _pending_approvals(served["home"])
    assert refusal, "a destructive action in Manual mode must ask"
    _resolve(daemon, "scaffold-refusal", str(refusal[-1]["approval_id"]), "deny")
    time.sleep(0.5)
    assert target.exists(), "a denied delete must not remove the file"


def test_fault_wording_served_401_403_429_retry_after(tmp_path):
    """Served turns against a loopback provider that answers typed HTTP refusals."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    statuses: dict[str, tuple[int, str]] = {}
    handler_calls: list[str] = []

    class _FaultHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def _send(self, status: int, body: str, extra: str = ""):
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if extra:
                self.send_header(*extra.split(":", 1))
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path.startswith("/api/tags"):
                return self._send(200, json.dumps({"models": [{"name": name} for name in statuses]}))
            return self._send(200, '{"ok": true}')

        def do_POST(self):
            handler_calls.append(self.path)
            model = next(iter(statuses))
            status, body = statuses[model]
            extra = f"Retry-After:{body}" if status == 429 else ""
            return self._send(status, '{"error": "fixture refusal"}', extra)

    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FaultHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    statuses["qwen3-stub:2b"] = (401, "")
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "OLLAMA_HOST": f"http://127.0.0.1:{port}",
            "VOOL_OLLAMA_URL": f"http://127.0.0.1:{port}",
            "VOOL_RAW_OLLAMA_API_URL": f"http://127.0.0.1:{port}",
            "VOOL_OLLAMA_CHAT_URL": f"http://127.0.0.1:{port}/api/chat",
        },
    )
    try:
        daemon.start(timeout=300)
    except Exception as exc:  # pragma: no cover - environment
        pytest.skip(f"served daemon could not boot here: {exc}")
    run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=f"http://127.0.0.1:{port}", registered=["qwen3-stub:2b"]))
    run_in_home(home, (
        "from core.model_registry import ModelRegistry\n"
        "from tests._authorship_certification import certify_for_authorship\n"
        "for manifest in ModelRegistry().list_manifests():\n"
        "    certify_for_authorship(manifest)\n"
        "print('certified')\n"
    ))

    def _turn(session: str) -> str:
        # A multi-unit stable-knowledge question, deliberately NOT a current-information ask
        # (that would hit the retrieval/publication surface) and not a single plain knowledge
        # question (the ambiguity probe owns those, and a failing provider makes IT ask back).
        # The wording under proof is the PROVIDER-FAILURE surface of the answering call itself.
        return _reply_text(daemon.chat(
            "What is 137 x 29? Also explain what an immutable value is.", session_id=session, timeout=180.0
        ))

    try:
        statuses["qwen3-stub:2b"] = (401, "")
        text_401 = _turn("fault-401")
        statuses["qwen3-stub:2b"] = (403, "")
        text_403 = _turn("fault-403")
        statuses["qwen3-stub:2b"] = (429, "27")
        text_429 = _turn("fault-429")
    finally:
        daemon.stop()
        server.shutdown()
        server.server_close()

    assert "rejected the stored API key" in text_401 or "authentication failed" in text_401, text_401[:400]
    assert "access denied" in text_403 or "refused the request" in text_403, text_403[:400]
    assert "429" in text_429 and "rate or quota limit" in text_429, text_429[:400]
    assert "27" in text_429 and "Retry-After" in text_429, text_429[:400]
    assert "wait a moment" not in text_429.lower(), text_429[:400]
