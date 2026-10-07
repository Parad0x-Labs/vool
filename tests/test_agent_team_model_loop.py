"""Agent teams on a real served VOOL: model-loop agents in parallel, each on its own model, while
the user's own chat keeps answering on the model it picks today.

One isolated daemon (``apps.vool_api_server``, its own VOOL_HOME and port) with a scripted
Ollama-dialect provider behind it, the same rig the other served tests use. Every agent turn is a
real ``/api/chat`` turn through the app's own door, in its own chat session, with the model on
the request. The provider records which model answered which prompt and when, so parallelism and
model identity are read from the provider side, not from anything the team reports.

Acceptance: L1 (parallel agents on two models while the chat answers on its own), C1 at the served
door (a token limit refuses the reservation, so the provider never sees that agent), T1 on a model
agent, and "an ordinary chat turn picks exactly what it picks today".
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

from core.agent_team.coordinator import TeamCoordinator
from core.agent_team.model_agent import HttpChatRunner
from core.agent_team.report import RESULT_TOKEN_CAP
from core.prompt_assembly_report import estimate_tokens
from tests._blackbox_served_rig import ServedDaemon, run_in_home
from tests._served_sufficiency_provider import SEED, BodyAwareScriptedProvider

REPO = Path(__file__).resolve().parents[1]
ALPHA, BETA, USER = "agent-alpha:2b", "agent-beta:3b", "user-chat:4b"
TEAM_LIMITS = {"max_usd": 1.0, "max_tokens": 200_000, "max_calls": 10, "wall_clock_seconds": 900}
AGENT_LIMITS = {"max_usd": 0.5, "max_tokens": 50_000, "max_calls": 3, "wall_clock_seconds": 240}
SLOW = 2.0


class _Timeline:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.spans: list[dict[str, Any]] = []

    def reply(self, body: dict[str, Any]) -> Any:
        model = str(body.get("model") or "")
        messages = body.get("messages") or []
        prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
        tools = [str(((t or {}).get("function") or {}).get("name") or "") for t in (body.get("tools") or [])
                 if isinstance(t, dict)]
        has_tool_result = any(str(m.get("role") or "") == "tool" for m in messages if isinstance(m, dict))
        start = time.monotonic()
        read_tool = next((t for t in tools if t.endswith("read_file")), "")
        if "in a VOOL team" in prompt and read_tool and not has_tool_result and "Dependency audit" not in prompt:
            # A reviewer reads the file it reviews: a real tool call through VOOL's own tool loop.
            with self.lock:
                self.spans.append({"model": model, "prompt": prompt[-300:], "start": start,
                                   "end": time.monotonic(), "agent": True, "tool_call": True})
            return {"content": "", "tool_calls": [{"id": f"read-{len(self.spans)}", "type": "function",
                                                   "function": {"name": read_tool, "arguments": {"path": "config_loader.py"}}}]}
        if "in a VOOL team" in prompt:
            name = prompt.split('the agent "', 1)[1].split('"', 1)[0]
            time.sleep(SLOW)  # long enough that serial dispatch would be visible
            text = ('RESULT: {"status": "done", "summary": "' + name + ' found a double read in the config loader.", '
                    '"changed": []}\n' + f"Checked it. {name} found the config loader reads the file twice.")
            if "Release notes" in name:
                text = "I fixed everything and saved the notes."  # no RESULT line: not a report
            if "Changelog" in name:
                # A verbose model that ignores "<= 3 sentences": a long, NON-repetitive summary on the
                # RESULT line (VOOL collapses degenerate repetition, so a repeated sentence would be
                # rewritten by the runtime, not by the team). The relay must still be <= 400 tokens.
                verbs = ["opens", "reads", "parses", "caches", "validates", "returns", "logs", "closes"]
                nouns = ["settings file", "profile block", "env overrides", "default table", "schema map",
                         "plugin list", "path aliases", "secret refs", "locale pack", "theme record"]
                verbose = " ".join(
                    f"Step {i + 1}: load() {verbs[i % len(verbs)]} the {nouns[(i * 3) % len(nouns)]} "
                    f"at line {i % 4 + 1} before branch {chr(65 + i % 26)}{i // 26}."
                    for i in range(120)
                )
                text = 'RESULT: {"status": "done", "summary": "' + verbose + '", "changed": []}\nDetail follows.'
        else:
            text = f"Plain chat answer from {model}."
        with self.lock:
            self.spans.append({"model": model, "prompt": prompt[-300:], "start": start, "end": time.monotonic(),
                               "agent": "in a VOOL team" in prompt})
        return text


class _TeamProvider(BodyAwareScriptedProvider):
    """The certification-capable stub: the sealed probe choreography is answered by the rig's own law,
    every other request by the timeline above."""

    def __init__(self, timeline: _Timeline) -> None:
        super().__init__({ALPHA: "", BETA: "", USER: ""})
        self.timeline = timeline

    def reply(self, model: str, body: dict[str, Any]) -> Any:
        if "vool_probe" in json.dumps(body):
            return super().reply(model, body)
        return self.timeline.reply(body)


def _seed(home: Path, provider: BodyAwareScriptedProvider) -> None:
    run_in_home(home, SEED.format(root=REPO, base_url=provider.base_url, models=[(ALPHA, {}), (BETA, {}), (USER, {})]))


def _certify(daemon: ServedDaemon, model: str) -> dict[str, Any]:
    request = Request(
        f"{daemon.base_url}/api/model-tool-certification/run",
        data=json.dumps({"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 120}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=400) as response:
        return json.loads(response.read().decode("utf-8"))


def _answered_by(home: Path, session: str, after_rowid: int) -> tuple[str, int]:
    """The model the DAEMON recorded for this session's turn (its own model.call_completed event),
    not a guess from the provider's timeline: concurrent agent turns make timing attribution racy."""
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT rowid, details_json FROM runtime_session_events WHERE session_id = ? AND rowid > ? "
            "AND event_type = 'model.call_completed' ORDER BY rowid", (session, after_rowid),
        ).fetchall()
        top = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM runtime_session_events").fetchone()[0]
    finally:
        conn.close()
    models = [str(json.loads(d or "{}").get("model_id") or "") for _r, d in rows]
    models = [m for m in models if m]
    return (models[-1] if models else ""), int(top)


def _chat_model(daemon: ServedDaemon, home: Path, session: str, model: str = "") -> str:
    _, marker = _answered_by(home, session, 1 << 62)
    daemon.chat("What does the config loader do?", session_id=session, model=model, timeout=600.0)
    # The turn's runtime events are committed at the turn's own boundary, which can land just after
    # the HTTP reply; wait for the daemon's record rather than reading too early.
    deadline = time.monotonic() + 60
    answered, _ = _answered_by(home, session, marker)
    while not answered and time.monotonic() < deadline:
        time.sleep(0.5)
        answered, _ = _answered_by(home, session, marker)
    return answered


def test_l1_parallel_agents_on_their_own_models_while_the_chat_keeps_its_own(tmp_path):
    timeline = _Timeline()
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "config_loader.py").write_text(
        "def load(path):\n    text = open(path).read()\n    again = open(path).read()  # read twice\n    return text\n"
    )
    with _TeamProvider(timeline) as provider:
        _seed(home, provider)
        env = {"VOOL_WORKSPACE_ROOT": str(workspace), "VOOL_MODEL_LOAD_FLOOR_GB": "0",
               "OLLAMA_HOST": provider.base_url, "VOOL_OLLAMA_URL": provider.base_url,
               "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat"}
        with ServedDaemon(home, env_extra=env) as daemon:
            for model in (ALPHA, BETA, USER):
                certified = _certify(daemon, model)
                assert certified.get("state") == "verified", f"{model}: {certified}"
            # What an ordinary chat picks today, with no team anywhere. The first turn on a fresh
            # daemon can be served by a lane with no model call (measured: run 8), so the comparison
            # is taken after one warm-up turn, the same way the turn during the team is.
            _chat_model(daemon, home, "openclaw:" + "0" * 20)
            auto_before = _chat_model(daemon, home, "openclaw:" + "1" * 20)
            pinned_before = _chat_model(daemon, home, "openclaw:" + "2" * 20, model=USER)
            assert pinned_before == USER

            team = TeamCoordinator(tmp_path / "team", workspace=workspace, team_limits=TEAM_LIMITS,
                                   tick_seconds=0.25, model_runner=HttpChatRunner(daemon.base_url))
            try:
                plan = [
                    {"key": "cfg", "objective": "Config loader review", "importance": "high", "kind": "model",
                     "mode": "read", "model": ALPHA, "limits": AGENT_LIMITS},
                    {"key": "auth", "objective": "Auth flow review", "kind": "model", "mode": "read",
                     "model": ALPHA, "limits": AGENT_LIMITS},
                    {"key": "cli", "objective": "CLI flags review", "kind": "model", "mode": "read",
                     "model": BETA, "limits": AGENT_LIMITS},
                    {"key": "log", "objective": "Changelog summary", "importance": "low", "kind": "model",
                     "mode": "read", "model": BETA,
                     "limits": {**AGENT_LIMITS, "wall_clock_seconds": 900}},
                    {"key": "notes", "objective": "Release notes check", "importance": "low", "kind": "model",
                     "mode": "read", "model": ALPHA, "limits": AGENT_LIMITS},
                    {"key": "tiny", "objective": "Dependency audit", "importance": "low", "kind": "model",
                     "mode": "read", "model": BETA,
                     "limits": {**AGENT_LIMITS, "max_tokens": 500}},
                ]
                started = time.monotonic()
                team.start(plan)
                # The user keeps chatting while the agents run.
                during: dict[str, str] = {}

                def user_turns() -> None:
                    # Fresh chats, so each compared turn is a chat's FIRST turn, exactly like the baseline:
                    # VOOL may route a repeated question in the same chat differently (measured: a chat's
                    # first ask answered without a model call, its second on the model), which is history,
                    # not the team.
                    during["auto"] = _chat_model(daemon, home, "openclaw:" + "3" * 20)
                    during["pinned"] = _chat_model(daemon, home, "openclaw:" + "4" * 20, model=USER)

                chatter = threading.Thread(target=user_turns)
                chatter.start()
                ended = team.run_until_ended(900)
                (tmp_path / "team-status.json").write_text(json.dumps(team.status(), indent=1, default=str))
                assert ended, (tmp_path / "team-status.json").read_text()
                chatter.join(timeout=600)
                elapsed = time.monotonic() - started

                rows = {r["title"]: r for r in team.registry.agents()}
                # each agent answered on ITS model, read from the provider side
                with timeline.lock:
                    agent_spans = [s for s in timeline.spans if s["agent"]]
                by_name: dict[str, int] = {}
                for span in agent_spans:
                    by_name.setdefault(span["model"], 0)
                    by_name[span["model"]] += 1
                assert by_name.get(ALPHA, 0) >= 2 and by_name.get(BETA, 0) >= 2, timeline.spans
                # parallel: four turns of >= SLOW s each overlapped in time
                overlapping = max(
                    sum(1 for t in agent_spans if t["start"] < s["end"] and t["end"] > s["start"]) for s in agent_spans
                )
                assert overlapping >= 2, agent_spans
                # the ordinary chat picked exactly what it picked before the team existed
                assert during == {"auto": auto_before, "pinned": USER}
                # and the daemon's own records agree: each agent's session answered on its model
                for title, want in (("Config loader review", ALPHA), ("Auth flow review", ALPHA),
                                    ("CLI flags review", BETA)):
                    assert _answered_by(home, rows[title]["result"]["session_id"], 0)[0] == want, title
                for title in ("Config loader review", "Auth flow review", "CLI flags review"):
                    assert rows[title]["state"] == "done", rows[title]
                    assert "double read" in rows[title]["result"]["relay"], rows[title]["result"]
                    assert rows[title]["result"]["session_id"].startswith("openclaw:")
                # a turn that ends without the agent's RESULT line is never reported as done
                notes = rows["Release notes check"]
                assert notes["state"] == "unverified" and "without a RESULT line" in notes["result"]["relay"]
                # T1 on a model agent: relayed result capped, full text on disk
                # Whatever VOOL's own answer gates did to the reply (measured: they withhold statements
                # the read file does not support, RESULT line included, which makes the agent
                # unverified, never done), the relay stays within the cap and the full turn is on disk.
                log = rows["Changelog summary"]
                assert log["state"] in ("done", "unverified")
                assert estimate_tokens(log["result"]["relay"]) <= RESULT_TOKEN_CAP
                outcome = json.loads((team.team_dir / "agents" / log["agent_id"] / "model_outcome.json").read_text())
                assert (team.team_dir / log["result"]["full_result"]).read_text() == outcome["text"]
                # C1 at the served door: refused before the call, so the provider never saw it
                tiny = rows["Dependency audit"]
                assert tiny["state"] == "partial" and "tokens" in tiny["result"]["relay"]
                assert not any("Dependency audit" in s["prompt"] for s in agent_spans)
                assert tiny["spend"]["calls"] == 0
                spend = team.budget.snapshot()
                assert spend["calls"] == 5 and spend["reserved_tokens"] == 0
                json.dumps(team.status())
                print(json.dumps({"elapsed_s": round(elapsed, 2), "agent_turns": len(agent_spans),
                                  "max_overlap": overlapping, "models": by_name, "team_spend": spend}))
            finally:
                team.stop()
                team.close()
