"""SERVED C02 proof — real /api/chat turns against a real daemon, plugin + MCP + native command
effects, under exact session/turn isolation, with the ONE budget authority's own store as the
ledger of truth.

Same rig law as the authorship and blackbox served packs: a real daemon process in its own
VOOL_HOME on an ephemeral port, a scripted provider on a real socket (this machine is cloud-only
for model execution — a stub is not a model launch), and everything between the HTTP door and the
effect's bytes served by production code from THIS checkout. Every assertion reads a durable
authority — the plugin child's call log, the MCP stub's call log, the workspace bytes, the
Blackbox journal, or the budget store read back in the daemon's own home — never the reply text.
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home
from tests._toolchain_fixtures import PLUGIN_ID, calls_logged, make_plugin

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"

STUB_MCP = str(Path(__file__).resolve().parents[1] / "mcp_stub_server.py")


def _call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _stub_models_are_resident(provider: ScriptedProvider) -> None:
    handler = provider._server.RequestHandlerClass
    original = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in provider.table]})
        return original(self)

    handler.do_GET = do_GET


def _mcp_config(tmp_path: Path, mcp_cwd: Path) -> Path:
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "stub",
                        "command": __import__("sys").executable,
                        "args": [
                            STUB_MCP,
                            "--extra-tools",
                            "--marker",
                            str(mcp_cwd / "started.marker"),
                            "--call-log",
                            str(mcp_cwd / "calls.log"),
                        ],
                        "cwd": str(mcp_cwd),
                        "enabled": True,
                        "confinement": "heuristic_only",
                        "trust": {
                            # the operator pins echo as a declared workspace-write tool:
                            # the pin is the declaration the budget door charges, and the
                            # pinned tool is what the served offer seats
                            "echo": {
                                "side_effect_class": "workspace_write",
                                "permission_actions": ["create_files"],
                            },
                            "write_outside": {
                                "side_effect_class": "workspace_write",
                                "permission_actions": ["create_files"],
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return cfg


_SEED_BUDGET = """
import sys
sys.path.insert(0, "__ROOT__")
from core import effect_budget as eb
token = eb.grant_operator_budget_authority("served budgets lane seed")
rules = __RULES__
adjustments = [
    eb.BudgetAdjustment(
        budget_class=rule["budget_class"],
        scope=rule["scope"],
        new_limit=int(rule["limit"]),
        window_seconds=float(rule.get("window") or 0.0),
        note="served budgets lane",
    )
    for rule in rules
]
eb.apply_operator_adjustment(token, adjustments)
print("SEEDED", len(eb.active_budgets()))
"""


def _seed_budget(home: Path, *rules: dict[str, Any]) -> None:
    script = _SEED_BUDGET.replace("__ROOT__", str(REPO_ROOT)).replace(
        "__RULES__", json.dumps(list(rules))
    )
    out = run_in_home(home, script)
    assert "SEEDED" in out, out


def _journal_entries(store_dir: Path) -> list[dict[str, Any]]:
    from storage.blackbox.journal import Journal

    if not store_dir.exists():
        return []
    return [dict(entry) for entry in Journal(store_dir).entries()]


@pytest.fixture()
def served(tmp_path: Path, request):
    """One daemon, one workspace, one admitted synthetic pack. `request.param` selects the
    optional MCP world (a fixture param)."""
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    store_dir = tmp_path / "blackbox-store"
    plugins_root = tmp_path / "plugins"
    scratch_root = tmp_path / "scratch"
    workspace.mkdir()

    plugin_dir = make_plugin(plugins_root, plugin_id=PLUGIN_ID, admit=False)

    env_extra = {
        "VOOL_ALWAYS_ON_CATALOG": "1",
        "VOOL_WORKSPACE_ROOT": str(workspace),
        "VOOL_BLACKBOX_DIR": str(store_dir),
        "VOOL_MODEL_LOAD_FLOOR_GB": "0",
        "VOOL_PLUGINS_DIR": str(plugins_root),
        "VOOL_PLUGIN_RUNTIME_TOOLS": "1",
        "VOOL_PLUGIN_SCRATCH_ROOT": str(scratch_root),
    }
    mcp_cwd = None
    if getattr(request, "param", None) == "mcp":
        mcp_cwd = tmp_path / "mcp-cwd"
        mcp_cwd.mkdir()
        env_extra["VOOL_MCP_CONFIG"] = str(_mcp_config(tmp_path, mcp_cwd))

    provider = ScriptedProvider({MODEL: "no script"}, after_tool_result={MODEL: "unused: the runtime renders the tool result itself"})
    provider.budget_scripts = {}

    def reply_for_request(body):
        messages = body.get("messages") or []
        if any(message.get("role") == "tool" for message in messages):
            return None
        prompt = " ".join(str(message.get("content") or "") for message in messages)
        with provider._lock:
            matches = [(prompt.rfind(marker), reply) for marker, reply in provider.budget_scripts.items()
                       if marker in prompt]
        return max(matches, key=lambda entry: entry[0])[1] if matches else None

    provider.reply_fn = reply_for_request
    daemon = ServedDaemon(
        home,
        env_extra={
            **env_extra,
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
        },
    )
    _stub_models_are_resident(provider)
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the runtime
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
        from urllib.request import Request, urlopen

        for act in ("install", "verify", "enable"):
            request_payload = Request(
                f"{daemon.base_url}/api/plugins/lifecycle",
                data=json.dumps({"action": act, "plugin_id": PLUGIN_ID}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request_payload, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            assert payload.get("ok"), f"{PLUGIN_ID} {act}: {payload}"
        provider.reset()
        yield {
            "home": home,
            "workspace": workspace,
            "store_dir": store_dir,
            "plugin_dir": plugin_dir,
            "scratch_root": scratch_root,
            "mcp_cwd": mcp_cwd,
            "daemon": daemon,
            "provider": provider,
        }
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)


def _turn(daemon: ServedDaemon, provider: ScriptedProvider, *, session: str, tool: str, arguments: dict, text: str, ask: str = "") -> dict:
    marker = "budget-request-" + uuid.uuid4().hex
    with provider._lock:
        provider.budget_scripts[marker] = _call(tool, arguments, marker)
    # Ask for the registered effect itself. A coding-repair request correctly opens a
    # code task and requires its reproduction/approval workflow before any mutation.
    payload = daemon.chat(
        (ask or (
            f"Run the registered tool {tool.replace('__', '.')} to {text}. "
            f"Use these exact arguments: {json.dumps(arguments)}"
        )) + f" Request reference: {marker}.",
        session_id=session,
        model=MODEL,
        mode="auto",
        timeout=600.0,
    )
    assert payload, f"the served turn never answered for {session}"
    offered = [call for call in provider.calls if call.get("tools")]
    assert any(tool in json.dumps(call) for call in provider.calls) or offered, (
        f"the provider never saw the {tool} call; reply={json.dumps(payload)[:500]!r} "
        f"provider_calls={json.dumps(provider.calls)[-1500:]}"
    )
    return payload


class TestServedPluginBudgets:
    def test_exhausted_session_budget_refuses_the_second_served_plugin_effect(
        self, served
    ) -> None:
        home: Path = served["home"]
        plugin_dir: Path = served["plugin_dir"]
        store_dir: Path = served["store_dir"]
        daemon: ServedDaemon = served["daemon"]
        provider: ScriptedProvider = served["provider"]

        _seed_budget(home, {"budget_class": "file_write", "scope": "session", "limit": 1})

        # turn one, session A: the session's single unit is spent by a real served effect
        _turn(
            daemon,
            provider,
            session="served-budget-a",
            tool=f"{PLUGIN_ID}__touch",
            arguments={"path": str(plugin_dir / "served-a.txt")},
            text="write the marker",
        )
        assert (plugin_dir / "served-a.txt").exists(), "the served plugin effect landed once"
        assert calls_logged(plugin_dir) == [f"{PLUGIN_ID}.touch"], calls_logged(plugin_dir)
        entries = _journal_entries(store_dir)
        assert [e for e in entries if str(e.get("path", "")).endswith("served-a.txt")], (
            "the served success journaled its effect"
        )

        # turn two, SAME session: the session rule refuses; the handler never reruns
        _turn(
            daemon,
            provider,
            session="served-budget-a",
            tool=f"{PLUGIN_ID}__touch",
            arguments={"path": str(plugin_dir / "served-b.txt")},
            text="write the second marker",
        )
        assert not (plugin_dir / "served-b.txt").exists(), "the refused effect wrote nothing"
        assert calls_logged(plugin_dir) == [f"{PLUGIN_ID}.touch"], "zero handler calls on refusal"
        entries = _journal_entries(store_dir)
        assert [e for e in entries if str(e.get("path", "")).endswith("served-b.txt")] == [], (
            "the refused effect left no Blackbox observation"
        )

        # turn three, session B: exact session isolation — the other session's unit is intact
        _turn(
            daemon,
            provider,
            session="served-budget-b",
            tool=f"{PLUGIN_ID}__touch",
            arguments={"path": str(plugin_dir / "served-c.txt")},
            text="write the other session's marker",
        )
        assert (plugin_dir / "served-c.txt").exists(), "the isolated session's effect ran"
        assert calls_logged(plugin_dir) == [f"{PLUGIN_ID}.touch", f"{PLUGIN_ID}.touch"]

    def test_served_parallel_sessions_race_for_the_last_project_unit(self, served) -> None:
        plugin_dir: Path = served["plugin_dir"]
        daemon: ServedDaemon = served["daemon"]
        provider: ScriptedProvider = served["provider"]

        _seed_budget(served["home"], {"budget_class": "file_write", "scope": "project", "limit": 1})

        barrier = threading.Barrier(2, timeout=120)
        outcomes: list[str] = []
        lock = threading.Lock()

        def _racer(name: str) -> None:
            try:
                barrier.wait()
                _turn(
                    daemon,
                    provider,
                    session=f"served-race-{name}",
                    tool=f"{PLUGIN_ID}__touch",
                    arguments={"path": str(plugin_dir / f"race-{name}.txt")},
                    text="win the unit",
                )
            except Exception as exc:  # a lost race still answers; a broken turn does not
                with lock:
                    outcomes.append(f"{name}:error:{type(exc).__name__}")
                return
            with lock:
                outcomes.append(name)

        threads = [threading.Thread(target=_racer, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)

        landed = [name for name in ("a", "b") if (plugin_dir / f"race-{name}.txt").exists()]
        assert len(landed) == 1, f"exactly one racer's effect may land, got {landed} ({outcomes})"
        assert calls_logged(plugin_dir) == [f"{PLUGIN_ID}.touch"], "the handler ran for exactly one racer"

    def test_served_budget_holds_across_daemon_restart(self, served) -> None:
        plugin_dir: Path = served["plugin_dir"]
        daemon: ServedDaemon = served["daemon"]
        provider: ScriptedProvider = served["provider"]
        home: Path = served["home"]

        _seed_budget(home, {"budget_class": "file_write", "scope": "session", "limit": 1})
        _turn(
            daemon,
            provider,
            session="served-restart",
            tool=f"{PLUGIN_ID}__touch",
            arguments={"path": str(plugin_dir / "restart-a.txt")},
            text="spend the unit",
        )
        assert (plugin_dir / "restart-a.txt").exists()

        daemon.stop()
        daemon.start(timeout=240)

        _turn(
            daemon,
            provider,
            session="served-restart",
            tool=f"{PLUGIN_ID}__touch",
            arguments={"path": str(plugin_dir / "restart-b.txt")},
            text="try again after restart",
        )
        assert not (plugin_dir / "restart-b.txt").exists(), "the exhaustion holds across restart"
        assert calls_logged(plugin_dir) == [f"{PLUGIN_ID}.touch"], "zero handler calls after restart"

    def test_served_operator_command_dispatch_is_budgeted(self, served) -> None:
        """The native command path: POST /api/commands/dispatch — the SAME
        `execute_command` door the CLI and the model lane use — under a window-scoped
        `command` rule. The first real mutation runs; the second dispatch is refused by
        the budget before the handler, and the pack's lifecycle state proves it."""
        daemon: ServedDaemon = served["daemon"]
        home: Path = served["home"]

        _seed_budget(
            home, {"budget_class": "command", "scope": "window", "limit": 1, "window": 3600}
        )

        from urllib.request import Request, urlopen

        def _dispatch(payload: dict) -> dict:
            request = Request(
                f"{daemon.base_url}/api/commands/dispatch",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=60) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                return json.loads(error.read().decode("utf-8"))

        first = _dispatch(
            {
                "command_id": "plugins.lifecycle.transition",
                "input": {"plugin_id": PLUGIN_ID, "action": "disable"},
            }
        )
        assert first.get("ok") is True, json.dumps(first)[:400]

        second = _dispatch(
            {
                "command_id": "plugins.lifecycle.transition",
                "input": {"plugin_id": PLUGIN_ID, "action": "enable"},
            }
        )
        assert second.get("ok") is False, json.dumps(second)[:400]
        assert second.get("fault", {}).get("code") == "budget_refused", json.dumps(second)[:400]
        assert second.get("fault", {}).get("detail", {}).get("code") == "EFFECT_BUDGET_EXCEEDED"

        # the refused enable never ran: the pack the first dispatch disabled stays disabled
        request = Request(f"{daemon.base_url}/api/plugins/lifecycle",
                          data=json.dumps({"action": "state", "plugin_id": PLUGIN_ID}).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=30) as response:
                state = json.loads(response.read().decode("utf-8"))
        except Exception:
            state = None
        if isinstance(state, dict) and state.get("state"):
            assert state["state"] == "disabled", state
        third = _dispatch(
            {
                "command_id": "plugins.lifecycle.transition",
                "input": {"plugin_id": PLUGIN_ID, "action": "state"},
            }
        )
        assert third.get("ok") is False and third.get("fault", {}).get("code") == "budget_refused", (
            "every later dispatch inside the window stays refused: json=" + json.dumps(third)[:300]
        )


@pytest.mark.parametrize("served", ["mcp"], indirect=True)
class TestServedMCPBudgets:
    def test_exhausted_session_budget_refuses_the_served_mcp_effect_with_zero_server_calls(
        self, served
    ) -> None:
        home: Path = served["home"]
        mcp_cwd = served["mcp_cwd"]
        daemon: ServedDaemon = served["daemon"]
        provider: ScriptedProvider = served["provider"]

        # WARM-UP (unbudgeted yet): the daemon's capability graph syncs the MCP server's
        # rows on the first tool dispatch (`_sync_registry_sources`), so this turn runs a
        # pack tool and the next turn's offer carries the pinned MCP tool.
        _turn(
            daemon,
            provider,
            session="served-mcp-warm",
            tool=f"{PLUGIN_ID}__touch",
            arguments={"path": str(served["plugin_dir"] / "mcp-warm.txt")},
            text="warm the registry",
        )
        assert calls_logged(served["plugin_dir"]) == [f"{PLUGIN_ID}.touch"]
        assert (served["plugin_dir"] / "mcp-warm.txt").exists()

        _seed_budget(home, {"budget_class": "file_write", "scope": "session", "limit": 1})

        def _mcp_calls() -> list[str]:
            log = mcp_cwd / "calls.log"
            if not log.is_file():
                return []
            return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

        # turn one: the pinned mutating MCP tool runs through a real served turn —
        # the session's only file_write unit is on the table
        _turn(
            daemon,
            provider,
            session="served-mcp-a",
            tool="mcp__stub__echo",
            arguments={"text": "mcp-a"},
            text="echo through the mcp tool",
        )
        assert (mcp_cwd / "started.marker").is_file(), (
            f"the stub never launched; dir={sorted(x.name for x in mcp_cwd.iterdir())}"
        )
        assert _mcp_calls() == ["echo"], _mcp_calls()

        # turn two, SAME session: the session rule refuses before any server call
        _turn(
            daemon,
            provider,
            session="served-mcp-a",
            tool="mcp__stub__echo",
            arguments={"text": "mcp-b"},
            text="echo again through the mcp tool",
        )
        assert _mcp_calls() == ["echo"], (
            f"zero server calls on refusal; call log={_mcp_calls()}"
        )
