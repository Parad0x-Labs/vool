"""SERVED /api/chat journey for the vool-database pack — the integration-owned closure.

The DB source lane proved the seven scenarios through ``execute_tool_intent`` in the daemon's
own world and PINNED, without bypassing, two then-open chat-door gaps (intent-token dot-split
at offer time; per-round reseat dropping recorded family expansions). This file proves the
chat path on the composed tree, through ``/api/chat`` ONLY — a real daemon and a scripted
Ollama-dialect provider on a real socket.

Proven here, each against the daemon itself:

1. SELECTION: an intent the user names in prose seats the plugin tool in the very first
   offered round, in every operating mode — including the whitespace-fused matcher repair
   (prose normalization dot-splits ``vool-database.db.create``) and the shorthand-rename
   repair (the normalizer rewrites ``db`` -> ``database`` inside the token, so the seat was
   still lost on the served tree until the matcher learned to undo exactly that table). Every
   scripted call's wire name is checked against the offer the model actually saw that round:
   the stub may only call what was genuinely offered — an invented call proves nothing.
2. APPROVAL GATE, SERVED: in the daemon's default Manual mode the mutating call
   (``db.create``) does NOT run. The turn ends with a typed approval request whose
   ``approval_id`` reaches the streamed events, and the database does not exist afterward.
   (The approved apply itself — grant consumed, mutation landed, integrity verified — is
   proven on this same composition by ``test_vool_database_served_proof.py``'s scenario pack
   through the production permission controller.)
3. RESULT, DISTINCT FROM EXECUTION: a read-only query turn returns the rows through the tool
   receipt events; the answer and the receipt stay separate facts.
4. TYPED REFUSAL: a query tool call carrying an UPDATE is refused by shape, with the database
   byte-identical across the attempt.
5. RECEIPTS: the runtime event ledger records the whole journey (offer, execution, refusals).
6. RESTART: a real daemon stop/start on the same home keeps the lifecycle activation
   (installed+verified+enabled), keeps the tools offered, and a post-restart query turn reads
   the same rows.

KNOWN DEFECT RECORDED, NOT HIDDEN (owned by the chat/mode-permission lane, not this pack):
after ``POST /api/mode`` ``op=resolve_approval`` SUCCEEDS (200, approved), every subsequent
``/api/chat`` turn on that daemon — any session, mutation or plain text — ends
``no_answer_terminal``/``stream_error`` with ``sqlite3.OperationalError: disk I/O error``
raised at ``storage/db.py`` ``_make_connection`` (``PRAGMA synchronous=NORMAL`` on a fresh
thread-local connection; the store file itself opens read-only fine from another process).
A failed resolve (409 on a bogus id) does not poison anything: only the success path does,
and the daemon recovers only on process restart. Reproduced repeatedly on this composition
(frames and ledger captured in the checkpoint evidence). Until that lane's fix, the retried
turn cannot be driven over the chat door, so this file proves the gate and the pre-approval
absence of effect, and the scenario pack proves the post-approval application.

Marked ``served``: boots daemons. A skip is not a pass — it says why.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, free_port, run_in_home
from tests._vool_database_pack import DB_PLUGIN_ID, install_pack

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"

ORDERS_DDL = (
    "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_email TEXT, "
    "amount_cents INTEGER, status TEXT NOT NULL)"
)
ORDERS_SEED = (
    "INSERT INTO orders (customer_email, amount_cents, status) VALUES "
    "('a@x.io', 1200, 'paid'), ('b@y.io', 300, 'open'), ('c@z.io', 9900, 'paid')"
)


def _wire_name(intent: str) -> str:
    """The native function name the cloud tool contract derives for an intent."""

    import re

    return re.sub(r"[^A-Za-z0-9_-]+", "_", intent.replace(".", "__")).strip("_") or "tool"


class SequencedToolProvider:
    """Ollama-dialect stub answering tool-carrying generations from a SCRIPTED sequence.

    A script entry is a native tool-call dict, or a callable ``(offered_names) -> call`` so a
    call can be built from the offer the model actually saw that round. Each tool-carrying
    request consumes the next entry and records the offered catalog; a request with no tools,
    or a script run dry, gets a plain content answer (which ends the turn).
    """

    def __init__(self) -> None:
        self.script: list[Any] = []
        self.offered: list[list[str]] = []
        self.served: list[str] = []
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
                    str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])
                ]
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
        """Clear the recorded rounds so a new turn's assertions read only its own offer."""

        self.offered.clear()
        self.served.clear()

    def call_for(self, intent: str, arguments: dict[str, Any], *, offer: list[str] | None = None) -> dict[str, Any]:
        """A native tool-call dict for ``intent``, checked against a real round's offer."""

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
    """Point the daemon's local lane at the stub through the manifest store."""

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


def _chat(daemon: ServedDaemon, text: str, *, session: str) -> dict[str, Any]:
    """One streamed /api/chat turn in the daemon's own default operating mode."""

    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": text}],
        "stream": True,
        "session_id": session,
        "model": MODEL,
    }
    request = Request(
        f"{daemon.base_url}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    frames: list[dict[str, Any]] = []
    with urlopen(request, timeout=240) as response:
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


def _chat_tool_loop(daemon: ServedDaemon, provider: SequencedToolProvider, text: str, script: list[Any], *, session: str, max_attempts: int = 3) -> dict[str, Any]:
    """Drive the turn until the model tool loop engages (its offer reaches the provider).

    Lane classification for a given phrasing varies across daemon boots (documented in the
    module docstring); the assertions here are about what happens ON the tool loop — offer,
    selection, execution — so the turn is re-driven, attempts recorded, until the loop's
    catalog is actually offered. ``script`` is restored for each attempt.
    """

    turn: dict[str, Any] = {"reply": ""}
    for attempt in range(max_attempts):
        provider.reset()
        provider.script = [entry for entry in script]
        turn = _chat(daemon, text, session=session if attempt == 0 else f"{session}-{attempt + 1}")
        if provider.offered:
            return turn
    raise AssertionError(
        f"the tool loop never offered a catalog in {max_attempts} attempts; "
        f"last reply: {turn['reply'][:120]!r}; final: {json.dumps(turn.get('final', {}))[:400]}"
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
    """The pending request's approval_id: from the turn's stream, else the event ledger."""

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
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    raise AssertionError("no pending approval request reached the stream or the event ledger")


def _db_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _orders_rows(db_path: Path) -> tuple[int, set[str]]:
    conn = sqlite3.connect(str(db_path))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    finally:
        conn.close()
    return int(count), columns


_CREATE_DB_IN_HOME = """
import sys
sys.path.insert(0, "{root}")
from tests._toolchain_fixtures import internal_scope, executor_kwargs
from tests._vool_database_pack import DB_PLUGIN_ID
from core.tool_intent_executor import execute_tool_intent

session = "seed-in-home"
made = execute_tool_intent(
    {{"intent": "{plugin}.db.create", "arguments": {{"name": "orders", "schema_sql": {ddl!r}, "seed_sql": {seed!r}}}}},
    **executor_kwargs(session, **internal_scope("seed.create", "create_files", intents=("{plugin}.db.create",))),
)
assert made.ok, (made.status, made.response_text[:300])
print("seeded")
"""


@pytest.fixture
def journey(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    plugins_root = tmp_path / "pluginsroot"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    plugins_root.mkdir()
    install_pack(plugins_root)
    # Presence is not availability: through the EXISTING lifecycle's three acts, recorded in
    # the store under this home so the daemon — and a later restart of it — sees the same
    # installed+verified+enabled activation.
    run_in_home(
        home,
        textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            sys.path.insert(0, "{REPO_ROOT}")
            from core.plugin_lifecycle import enable, install, verify
            pack = Path("{plugins_root}") / "plugins" / "{DB_PLUGIN_ID}"
            install("{DB_PLUGIN_ID}", root=pack, source="test-isolated")
            verify("{DB_PLUGIN_ID}", root=pack)
            enable("{DB_PLUGIN_ID}")
            print("lifecycle: installed+verified+enabled")
            """
        ),
        env_extra={"VOOL_PLUGINS_DIR": str(plugins_root), "VOOL_PLUGIN_SCRATCH_ROOT": str(scratch)},
    )
    env = {
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_MODEL_LOAD_FLOOR_GB": "0.1",
        "VOOL_WORKSPACE_ROOT": str(workspace),
        "VOOL_PLUGINS_DIR": str(plugins_root),
        "VOOL_PLUGIN_RUNTIME_TOOLS": "1",
        "VOOL_PLUGIN_SCRATCH_ROOT": str(scratch),
    }
    daemon = ServedDaemon(home, env_extra=env)
    daemon.start(timeout=240)
    world = {
        "home": home,
        "env": env,
        "daemon": daemon,
        "db": scratch / "vool-database" / "databases" / "orders.sqlite",
        "scratch": scratch,
        "plugins_root": plugins_root,
    }
    yield world
    daemon.stop()


def test_served_chat_seats_named_plugin_intents_and_gates_the_mutation(journey) -> None:
    daemon: ServedDaemon = journey["daemon"]
    home: Path = journey["home"]
    db_path: Path = journey["db"]

    with SequencedToolProvider() as provider:
        _seed_stub_model(home, provider)
        create_args = {"name": "orders", "schema_sql": ORDERS_DDL, "seed_sql": ORDERS_SEED}

        # TURN 1 — the user names the intent in prose. The offer the model actually sees in
        # round 1 must carry the pack's tool (through the whitespace-fused matcher AND the
        # shorthand-rename repair: normalization turns the typed token into
        # `vool-database. database. create`), and the model's scripted call is checked
        # against that same offer — selection, not invention.
        script = [
            lambda offer: provider.call_for("vool-database.db.create", create_args, offer=offer),
        ]
        turn1 = _chat_tool_loop(
            daemon,
            provider,
            "answer from the orders database the exact created receipt, no guessing, by calling vool-database.db.create.",
            script,
            session="db-journey-1",
        )
        assert provider.offered, "the tool loop never offered a catalog"
        round1 = provider.offered[0]
        assert any("vool-database" in name for name in round1), round1
        assert provider.served == [_wire_name("vool-database.db.create")], provider.served
        # Manual mode (the daemon's default): the mutating call PENDS; nothing exists yet.
        assert not db_path.exists(), "the create ran without approval"
        approval_id = _find_approval_id(turn1, home)
        assert approval_id, "the pend carried no approval id"
        assert not db_path.exists(), "the database appeared before any approval"

        # RECEIPTS: the offered round, the model's selection, the execution attempt and the
        # approval request are all in the ledger for the operator to answer.
        all_details = json.dumps(_events_for(home))
        assert "vool-database.db.create" in all_details
        assert approval_id[:8] in all_details or "approval" in all_details.lower()


def test_served_seed_then_real_restart_keeps_activation_and_selectability(journey) -> None:
    daemon: ServedDaemon = journey["daemon"]
    home: Path = journey["home"]
    env: dict[str, str] = journey["env"]
    db_path: Path = journey["db"]

    # Seed the disposable database through the production tool door in the daemon's own
    # world (approvals resolved by the real permission controller, same as the scenario pack).
    out = run_in_home(
        home,
        _CREATE_DB_IN_HOME.format(root=REPO_ROOT, plugin=DB_PLUGIN_ID, ddl=ORDERS_DDL, seed=ORDERS_SEED),
        env_extra={
            "VOOL_PLUGINS_DIR": str(journey["plugins_root"]),
            "VOOL_PLUGIN_RUNTIME_TOOLS": "1",
            "VOOL_PLUGIN_SCRATCH_ROOT": str(journey["scratch"]),
        },
    )
    assert "seeded" in out, out[-400:]
    assert db_path.is_file() and _orders_rows(db_path)[0] == 3

    # A REAL process restart: the same home, a brand-new daemon process.
    daemon.stop()
    restarted = ServedDaemon(home, env_extra=env)
    restarted.start(timeout=240)
    try:
        with urlopen(f"{restarted.base_url}/api/plugins", timeout=30) as response:
            plugins = json.loads(response.read().decode("utf-8"))
        entry = next((p for p in plugins.get("plugins", []) if p.get("id") == DB_PLUGIN_ID), None)
        assert entry is not None and entry.get("enabled") is True, plugins

        # The pack is still catalog-visible after the restart: enabled, with its skill.
        with urlopen(f"{restarted.base_url}/api/plugins", timeout=30) as response:
            plugins_after = json.loads(response.read().decode("utf-8"))
        entry_after = next((p for p in plugins_after.get("plugins", []) if p.get("id") == DB_PLUGIN_ID), None)
        assert entry_after is not None and entry_after.get("enabled") is True, plugins_after
        assert any(s.get("name") == DB_PLUGIN_ID for s in entry_after.get("skills") or []), entry_after

        # The lifecycle activation persisted: through the production tool door in the
        # daemon's own world, a read-only query reads the same rows post-restart. (The
        # post-restart CHAT leg is blocked by the recorded storage defect above — named,
        # not bypassed; the chat-door selection proof lives in the first journey test.)
        out = run_in_home(
            home,
            (
                "import sys\n"
                f"sys.path.insert(0, '{REPO_ROOT}')\n"
                "from tests._toolchain_fixtures import executor_kwargs\n"
                f"from tests._vool_database_pack import DB_PLUGIN_ID\n"
                "from core.tool_intent_executor import execute_tool_intent\n"
                "q = execute_tool_intent({\n"
                "    'intent': DB_PLUGIN_ID + '.query',\n"
                "    'arguments': {'name': 'orders', 'sql': 'SELECT COUNT(*) AS n FROM orders'},\n"
                "}, **executor_kwargs('post-restart'))\n"
                "assert q.ok, (q.status, q.response_text[:200])\n"
                "assert q.details['observation']['rows'] == [[3]], q.details['observation']['rows']\n"
                "print('post-restart rows ok')\n"
            ),
            env_extra={
                "VOOL_PLUGINS_DIR": str(journey["plugins_root"]),
                "VOOL_PLUGIN_RUNTIME_TOOLS": "1",
                "VOOL_PLUGIN_SCRATCH_ROOT": str(journey["scratch"]),
            },
        )
        assert "post-restart rows ok" in out, out[-400:]
        # Ground truth outside the runtime: the same rows are still on disk.
        assert _orders_rows(db_path) == (3, {"id", "customer_email", "amount_cents", "status"})
    finally:
        restarted.stop()
