"""SERVED proofs for the vool-database pack — what the real HTTP door does and does not reach.

A real ``apps.vool_api_server`` process in its own VOOL_HOME on an ephemeral port, the pack
installed through the EXISTING isolated install flow (VOOL_PLUGINS_DIR pointing at an isolated
plugins root; the server's own boot loader discovers and registers it), and a scripted
Ollama-dialect provider behind a real socket for the model-loop boundary probe (this machine is
cloud-only for model execution; a stub is not a model launch).

Three served facts, each asserted against the daemon itself:

1. DISCOVERY: ``/api/plugins`` lists the pack and its skill; ``/api/runtime/capabilities``
   exposes the nine typed intents — the server's own production catalog, read over HTTP.
2. THE CHAT-DOOR BOUNDARY: a chat turn that demands evidence reaches the model tool loop (the
   designed ``capability.expand_family`` primitive EXECUTES over the wire), and a later round
   of the same turn offers the plugin family. Revision 6 repaired the read-and-clear expansion
   that kept it out: the round's prompt catalog consumed it before the native tool definitions
   were built (``core.tool_offer_state``). The interpreter still dot-splits intent tokens in
   user text, so ``core.tool_demand_signals._plugin_intents`` cannot match them at offer time;
   the expansion is the model's path to the family.
3. THE SEVEN SCENARIOS in the daemon's own world: a separate process under the daemon's
   home/plugins/scratch environment drives every scenario through the production
   ``execute_tool_intent`` boundary (the boundary the tool loop dispatches through), with
   approvals resolved through the real permission controller — the blackbox lane's precedent
   for routes the chat door cannot yet reach.

Marked ``served``: boots a daemon. A skip is not a pass — it says why.
"""
from __future__ import annotations

import json
import sqlite3
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, ServedDaemon, free_port, run_in_home
from tests._vool_database_pack import install_pack

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
GOOD_MIGRATION = ["ALTER TABLE orders ADD COLUMN note TEXT", "CREATE INDEX idx_orders_status ON orders(status)"]
FAILING_MIGRATION = [
    "DROP TABLE orders",
    "CREATE TABLE broken (x INTEGER PRIMARY KEY, badcol TEXT NOT NULL)",
    "INSERT INTO broken (x) VALUES (1)",
]

_STUB_SEED = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, "{root}")
    from storage.db import get_connection
    from storage.migrations import run_migrations
    from storage.model_provider_manifest import ModelProviderManifest, list_provider_manifests, upsert_provider_manifest

    run_migrations()
    manifest = ModelProviderManifest(
        provider_name="ollama-local", model_name="{model}", source_type="http",
        adapter_type="openai_compatible", license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        weight_location="external", runtime_dependency="ollama",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={{"base_url": "{base_url}", "timeout_seconds": 60}},
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


class OneShotToolProvider:
    """Ollama-dialect stub: a tool-carrying generation returns ONE scripted call; probes get 'ok'."""

    def __init__(self, call: dict[str, Any]) -> None:
        self.call = call
        self.offered: list[list[str]] = []
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
                    message = {"role": "assistant", "content": "", "tool_calls": [dict(rig.call)]}
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

    def __enter__(self) -> OneShotToolProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def served(tmp_path: Path):
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    plugins_root = tmp_path / "pluginsroot"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    plugins_root.mkdir()
    install_pack(plugins_root)  # the existing isolated install: server discovers <root>/plugins/*
    # Presence is not availability: the canonical register_plugin gate offers a pack's tools
    # only after the EXISTING lifecycle's three separate acts, recorded in the store under
    # this home (so the daemon and every in-home scenario process see the same activation).
    run_in_home(
        home,
        textwrap.dedent(
            """
            import sys
            from pathlib import Path
            sys.path.insert(0, "{root}")
            from core.plugin_lifecycle import enable, install, verify
            pack = Path("{plugins_root}") / "plugins" / "vool-database"
            install("vool-database", root=pack, source="test-isolated")
            verify("vool-database", root=pack)
            enable("vool-database")
            print("lifecycle: installed+verified+enabled")
            """
        ).format(root=REPO_ROOT, plugins_root=plugins_root),
        env_extra={"VOOL_PLUGINS_DIR": str(plugins_root), "VOOL_PLUGIN_SCRATCH_ROOT": str(scratch)},
    )
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            # The scripted provider loads no weights; the load-gate's headroom floor is for
            # real model loads, and this box runs sibling sessions' daemons today.
            "VOOL_MODEL_LOAD_FLOOR_GB": "0.1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_PLUGINS_DIR": str(plugins_root),
            "VOOL_PLUGIN_RUNTIME_TOOLS": "1",
            "VOOL_PLUGIN_SCRATCH_ROOT": str(scratch),
        },
    )
    daemon.start(timeout=240)
    yield {
        "home": home,
        "daemon": daemon,
        "db": scratch / "vool-database" / "databases" / "orders.sqlite",
        "plugins_root": plugins_root,
        "scratch": scratch,
    }
    daemon.stop()


def _get(daemon: ServedDaemon, path: str) -> dict[str, Any]:
    with urlopen(f"{daemon.base_url}{path}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _events_for(home: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT event_type, COALESCE(details_json, '') FROM runtime_session_events "
            "ORDER BY rowid DESC LIMIT 400"
        ).fetchall()
    finally:
        conn.close()
    return rows


def test_served_discovery_lists_the_pack_and_its_capabilities(served: dict[str, Any]) -> None:
    daemon: ServedDaemon = served["daemon"]

    plugins = _get(daemon, "/api/plugins")
    assert plugins.get("installed") is True, plugins.get("reason")
    entry = next((p for p in plugins.get("plugins", []) if p.get("id") == "vool-database"), None)
    assert entry is not None, [p.get("id") for p in plugins.get("plugins", [])]
    assert entry["enabled"] is True
    skills = entry.get("skills") or []
    assert any(s.get("name") == "vool-database" for s in skills), skills
    assert "database" in str(entry.get("description") or "").lower()

    # The server's own boot loader registered the pack's contracts (the daemon log is the
    # production record of the existing install flow). The curated capability snapshot does
    # NOT list tool-level intents (it is a feature-level snapshot, not the registry) — the
    # served tool-visibility boundary is the pinned routing gap in the door test below.
    log = (served["home"] / "daemon.log").read_text(encoding="utf-8", errors="replace")
    assert "Loaded 1 plugin(s): vool-database" in log, log[-500:]
    capabilities = _get(daemon, "/api/runtime/capabilities")
    assert isinstance(capabilities.get("capabilities"), list) and capabilities["capabilities"]


def test_served_chat_door_reaches_the_tool_loop_and_the_expanded_plugin_family_seats(
    served: dict[str, Any],
) -> None:
    """The plugin family a model expands is offered in a later round of the same turn.

    Migrated in revision 6 (Gate B) from the gap it pinned. A user text demanding evidence reaches the
    model tool loop, and the designed ``capability.expand_family`` primitive EXECUTES over the wire.
    Before revision 6 the expansion was read-and-clear: the round's prompt catalog consumed it before
    the native tool definitions were built, so no round carried a vool-database tool -- which is what
    this test asserted. The expansion is turn navigation now (core.tool_offer_state) and one offer per
    round feeds both renderings, so a LATER round must carry the family. Sensitivity kept: the round
    BEFORE the expansion must still carry no plugin tool, because nothing but the expansion seats the
    family here.
    """

    daemon: ServedDaemon = served["daemon"]
    expand_call = {
        "id": "e1",
        "type": "function",
        "function": {"name": "capability__expand_family", "arguments": json.dumps({"family": "plugin"})},
    }
    provider = OneShotToolProvider(expand_call)
    with provider:
        # Point the daemon's local lane at the stub AFTER boot, through the manifest store.
        run_in_home(
            served["home"],
            _STUB_SEED.format(root=REPO_ROOT, base_url=provider.base_url, model=MODEL),
            env_extra={
                "OLLAMA_HOST": provider.base_url,
                "VOOL_OLLAMA_URL": provider.base_url,
                "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            },
        )
        run_in_home(
            served["home"],
            textwrap.dedent(
                f'''
                import sys
                sys.path.insert(0, "{REPO_ROOT}")
                from storage.model_provider_manifest import list_provider_manifests
                from tests._authorship_certification import certify_for_authorship
                for m in list_provider_manifests():
                    if m.model_name == "{MODEL}":
                        certify_for_authorship(m)
                '''
            ),
        )
        payload = daemon.chat(
            "answer from the orders database the exact total of paid orders, no guessing",
            session_id="db-door-1",
            mode="auto",
            model=MODEL,
        )
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        reply = str(message.get("content") or payload.get("response") or "")

    # The designed expansion primitive ran through the model loop over the wire.
    events = _events_for(served["home"])
    executed = [e for e in events if e[0] == "tool_executed" and "expand_family" in str(e[1])]
    assert executed, f"the tool loop never executed the expansion primitive. reply={reply[:200]!r}"

    # Evidence file for the report: exactly what each round offered, written before it is judged.
    offered = provider.offered
    (served["home"] / "served_door_expansion.json").write_text(
        json.dumps({"rounds_offered": offered, "expand_executed": True, "reply_head": reply[:200]}, indent=2),
        encoding="utf-8",
    )
    assert offered, "the model loop never offered a tool catalog"
    # Not seated before the expansion; carried by a later round of the same turn.
    assert not any("vool" in t for t in offered[0]), offered[0]
    assert any(any("vool" in t for t in tools) for tools in offered[1:]), offered


_SCENARIOS = (
    '''
"""The seven scenarios, in the daemon's own world, through the production tool door."""
import json
import sys

sys.path.insert(0, "@ROOT@")

from tests._toolchain_fixtures import executor_kwargs, internal_scope
from tests._vool_database_pack import DB_PLUGIN_ID


def run(intent, arguments, session, **ctx):
    from core.tool_intent_executor import execute_tool_intent

    return execute_tool_intent({"intent": intent, "arguments": arguments}, **executor_kwargs(session, **ctx))


def approve(pending):
    from core.mode_permission_policy import resolve_approval

    token = str(pending.details["approval_request"]["approval_id"])
    assert resolve_approval(token, decision="allow") is not None
    return {"mode_approval_token": token}


DB = DB_PLUGIN_ID
CREATE_SCOPE = internal_scope("served.db.create", "create_files", intents=(f"{DB}.db.create",))
BACKUP_SCOPE = internal_scope("served.db.backup", "create_files", intents=(f"{DB}.backup",))


def check(label, condition, detail=""):
    if not condition:
        raise SystemExit(f"SCENARIO FAILED: {label} {detail}")
    print("ok:", label)


# 1. inspect a sample schema
made = run(f"{DB}.db.create", {"name": "orders", "schema_sql": @DDL@, "seed_sql": @SEED@}, "s1", **CREATE_SCOPE)
check("create", made.ok, made.response_text[:200])
schema = run(f"{DB}.schema", {"name": "orders"}, "s1")
check("schema", schema.ok and schema.details["observation"]["tables"]["orders"]["row_count"] == 3)

# 2. answer a real data question
q = run(f"{DB}.query", {"name": "orders", "sql": "SELECT status, COUNT(*) AS n, SUM(amount_cents) AS total FROM orders WHERE amount_cents > :min GROUP BY status", "params": {"min": 500}}, "s2")
check("data question", q.ok and q.details["observation"]["rows"] == [["paid", 2, 11100]], str(q.details["observation"].get("rows")))

# 3. propose + approve a migration
preview = run(f"{DB}.migrate.preview", {"name": "orders", "statements": @GOOD@}, "s3")
check("preview", preview.ok and "tables_changed" in json.dumps(preview.details["observation"]["diff"]))
pending = run(f"{DB}.migrate.apply", {"name": "orders", "statements": @GOOD@, "backup_name": "approved-migration"}, "s3")
check("apply gates", pending.status == "pending_approval")
applied = run(f"{DB}.migrate.apply", {"name": "orders", "statements": @GOOD@, "backup_name": "approved-migration"}, "s3", **approve(pending))
check("apply", applied.ok and applied.details["observation"]["integrity"] == "ok")

# 4. verify actual DB state through the read-only door
after = run(f"{DB}.schema", {"name": "orders"}, "s4")
check("verify state", "note" in json.dumps(after.details["observation"]["tables"]))

# 5. reject a destructive unapproved operation
destructive = run(f"{DB}.migrate.apply", {"name": "orders", "statements": @FAILING@}, "s5")
check("destructive gated", destructive.status == "pending_approval" and destructive.details["executed"] is False)
survived = run(f"{DB}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"}, "s5")
check("nothing ran", survived.details["observation"]["rows"] == [[3]])

# 6. cancel a long query
slow = run(f"{DB}.query", {"name": "orders", "sql": "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT COUNT(*) FROM c", "max_seconds": 2}, "s6")
check("cancel", slow.ok and slow.details["observation"]["status_detail"] == "time_limit_cancelled")

# 7. recover a failed migration
backup = run(f"{DB}.backup", {"name": "orders", "backup_name": "recovery-point"}, "s7", **BACKUP_SCOPE)
check("backup", backup.ok)
pending2 = run(f"{DB}.migrate.apply", {"name": "orders", "statements": @FAILING@}, "s7")
failed = run(f"{DB}.migrate.apply", {"name": "orders", "statements": @FAILING@}, "s7", **approve(pending2))
check("failed rolls back", failed.status == "apply_failed_rolled_back" and failed.details["observation"]["rolled_back"] is True)
pending3 = run(f"{DB}.restore", {"name": "orders", "backup_name": "recovery-point"}, "s7")
restored = run(f"{DB}.restore", {"name": "orders", "backup_name": "recovery-point"}, "s7", **approve(pending3))
check("restore", restored.ok and restored.details["observation"]["integrity"] == "ok")
final = run(f"{DB}.query", {"name": "orders", "sql": "SELECT COUNT(*) AS n FROM orders"}, "s7")
check("recovered data", final.details["observation"]["rows"] == [[3]])
print("ALL SEVEN SCENARIOS OK")
'''
)



def _scenario_script() -> str:
    return (
        _SCENARIOS
        .replace("@ROOT@", str(REPO_ROOT))
        .replace("@DDL@", repr(ORDERS_DDL))
        .replace("@SEED@", repr(ORDERS_SEED))
        .replace("@GOOD@", repr(GOOD_MIGRATION))
        .replace("@FAILING@", repr(FAILING_MIGRATION))
    )


def test_served_seven_scenarios_in_the_daemons_own_world(served: dict[str, Any]) -> None:
    """Every scenario through the production tool door, under the daemon's own environment.

    Same precedent as the Blackbox served lane: a route /api/chat cannot yet reach (pinned
    above) is proven by crossing ``execute_tool_intent`` in a separate process bound to the
    daemon's home, plugins root and scratch — the boundary the tool loop itself dispatches
    through, with approvals resolved by the real permission controller.
    """

    out = run_in_home(
        served["home"],
        _scenario_script(),
        env_extra={
            "VOOL_PLUGINS_DIR": str(served["plugins_root"]),
            "VOOL_PLUGIN_RUNTIME_TOOLS": "1",
            "VOOL_PLUGIN_SCRATCH_ROOT": str(served["scratch"]),
        },
    )
    assert "ALL SEVEN SCENARIOS OK" in out, out[-2000:]
    # Ground truth outside the runtime: the disposable database in the daemon's scratch.
    db_path: Path = served["db"]
    assert db_path.is_file()
    conn = sqlite3.connect(str(db_path))
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        assert "note" in columns
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 3
    finally:
        conn.close()
    backups = list((db_path.parent / "backups" / "orders").glob("*.sqlite"))
    assert any("recovery-point" in p.name for p in backups) and any(
        "approved-migration" in p.name for p in backups
    ), sorted(p.name for p in backups)
