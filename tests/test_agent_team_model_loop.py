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
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from core.agent_team.coordinator import TeamCoordinator
from core.agent_team.model_agent import HttpChatRunner
from core.agent_team.report import RESULT_TOKEN_CAP
from core.prompt_assembly_report import estimate_tokens
from tests._blackbox_served_rig import SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

REPO = Path(__file__).resolve().parents[1]
ALPHA, BETA, USER = "agent-alpha:2b", "agent-beta:3b", "user-chat:4b"
TEAM_LIMITS = {"max_usd": 1.0, "max_tokens": 200_000, "max_calls": 10, "wall_clock_seconds": 300}
AGENT_LIMITS = {"max_usd": 0.5, "max_tokens": 50_000, "max_calls": 3, "wall_clock_seconds": 240}
SLOW = 2.0


class _Timeline:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.spans: list[dict[str, Any]] = []

    def reply(self, body: dict[str, Any]) -> str | None:
        model = str(body.get("model") or "")
        messages = body.get("messages") or []
        prompt = " ".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
        start = time.monotonic()
        if "in a VOOL team" in prompt:
            name = prompt.split('the agent "', 1)[1].split('"', 1)[0]
            time.sleep(SLOW)  # long enough that serial dispatch would be visible
            text = (f"Checked it. {name} found the config loader reads the file twice.\n"
                    'RESULT: {"status": "done", "summary": "' + name + ' found a double read in the config loader.", "changed": []}')
            if "Changelog" in name:
                text = " ".join(f"entry{i}" for i in range(2500)) + '\nRESULT: {"status": "done", "summary": "' + \
                    " ".join(f"entry{i}" for i in range(2500)) + '", "changed": []}'
        else:
            text = f"Plain chat answer from {model}."
        with self.lock:
            self.spans.append({"model": model, "prompt": prompt[-300:], "start": start, "end": time.monotonic(),
                               "agent": "in a VOOL team" in prompt})
        return text


def _seed(home: Path, provider: ScriptedProvider) -> None:
    run_in_home(home, SEED_MANIFEST.format(root=REPO, base_url=provider.base_url, registered=[ALPHA, BETA, USER]))


def _chat_model(daemon: ServedDaemon, timeline: _Timeline, session: str, model: str = "") -> str:
    before = len(timeline.spans)
    daemon.chat("What does the config loader do?", session_id=session, model=model)
    with timeline.lock:
        mine = [s for s in timeline.spans[before:] if not s["agent"]]
    return mine[-1]["model"] if mine else ""


def test_l1_parallel_agents_on_their_own_models_while_the_chat_keeps_its_own(tmp_path):
    timeline = _Timeline()
    home = tmp_path / "home"
    with ScriptedProvider({ALPHA: "", BETA: "", USER: ""}, reply_fn=timeline.reply) as provider:
        _seed(home, provider)
        with ServedDaemon(home) as daemon:
            # What an ordinary chat picks today, with no team anywhere.
            auto_before = _chat_model(daemon, timeline, "openclaw:" + "1" * 20)
            pinned_before = _chat_model(daemon, timeline, "openclaw:" + "2" * 20, model=USER)
            assert pinned_before == USER

            team = TeamCoordinator(tmp_path / "team", workspace=tmp_path, team_limits=TEAM_LIMITS,
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
                     "mode": "read", "model": BETA, "limits": AGENT_LIMITS},
                    {"key": "tiny", "objective": "Dependency audit", "importance": "low", "kind": "model",
                     "mode": "read", "model": BETA,
                     "limits": {**AGENT_LIMITS, "max_tokens": 500}},
                ]
                started = time.monotonic()
                team.start(plan)
                # The user keeps chatting while the agents run.
                during: dict[str, str] = {}

                def user_turns() -> None:
                    during["auto"] = _chat_model(daemon, timeline, "openclaw:" + "1" * 20)
                    during["pinned"] = _chat_model(daemon, timeline, "openclaw:" + "2" * 20, model=USER)

                chatter = threading.Thread(target=user_turns)
                chatter.start()
                assert team.run_until_ended(600), team.status()
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
                for title in ("Config loader review", "Auth flow review", "CLI flags review"):
                    assert rows[title]["state"] == "done", rows[title]
                    assert "double read" in rows[title]["result"]["relay"]
                    assert rows[title]["result"]["session_id"].startswith("openclaw:")
                # T1 on a model agent: relayed result capped, full text on disk
                log = rows["Changelog summary"]
                assert log["state"] == "done" and estimate_tokens(log["result"]["relay"]) <= RESULT_TOKEN_CAP
                assert (team.team_dir / log["result"]["full_result"]).read_text().count("entry") >= 2500
                # C1 at the served door: refused before the call, so the provider never saw it
                tiny = rows["Dependency audit"]
                assert tiny["state"] == "partial" and "tokens" in tiny["result"]["relay"]
                assert not any("Dependency audit" in s["prompt"] for s in agent_spans)
                assert tiny["spend"]["calls"] == 0
                spend = team.budget.snapshot()
                assert spend["calls"] == 4 and spend["reserved_tokens"] == 0
                json.dumps(team.status())
                print(json.dumps({"elapsed_s": round(elapsed, 2), "agent_turns": len(agent_spans),
                                  "max_overlap": overlapping, "models": by_name, "team_spend": spend}))
            finally:
                team.stop()
                team.close()
