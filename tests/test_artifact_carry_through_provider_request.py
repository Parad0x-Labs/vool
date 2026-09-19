"""A referenced artifact must ride the ACTUAL final provider request -- served proof.

The bounded artifact-carry repairs (2026-09-15 demo segment) are pinned at the history-authority
level (selection by conversation identity, the scan window, the fenced-body predicate, the carry
ceiling). What this file adds is the through-the-wire half of the contract: a continuation turn's
outgoing HTTP request to the answering model carries the referenced artifact's bytes -- not merely
a builder that assembled them and dropped them -- including across a MODEL change on the same
session, where the artifact's distinctive edit exists only in the earlier turn and cannot be
rebuilt from the continuation prompt alone. A refusal that quotes a code fence is the negative
control: the quoted span must not ride as the artifact.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from tests._blackbox_served_rig import SEED_MANIFEST, ServedDaemon, free_port, run_in_home

    _SERVED_AVAILABLE = True
except Exception:  # pragma: no cover
    _SERVED_AVAILABLE = False

#: The distinctive prior edit: exists ONLY in the T1 artifact, never in any prompt.
DISTINCTIVE_EDIT = "function escapeHtmlLantern(nodeId7f3a)"
QUOTED_IN_REFUSAL = "quotedFenceNotAnArtifact" + ("q" * 600)

ARTIFACT = (
    "```html\n<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Kanban Lantern Board</title></head>\n<body>\n<div id=\"board\">"
    "<section class=\"col todo\"></section><section class=\"col doing\"></section>"
    "<section class=\"col done\"></section></div>\n<script>\n"
    f"{DISTINCTIVE_EDIT} {{ /* bind the lantern column */ }}\n"
    "function refresh() { document.querySelectorAll('.col').forEach(c => c.dispatchEvent(new Event('refresh'))); }\n"
    "</script>\n</body></html>\n```\n"
)

BUILD_ASK = (
    "Write me a single-file HTML kanban board called Kanban Lantern Board. "
    "One file, inline CSS and JS, three columns: todo, doing, done."
)
CONTINUE_ASK = (
    "Continue the board file you just made and add a tags column to each card. "
    "Keep everything else exactly as it is."
)
REFUSAL_QUOTE_ASK = "Show me the ledger tool code you keep mentioning, quoted exactly."
CONTINUE_AFTER_REFUSAL = "Continue the previous file from where it was cut off."


class _ScriptedProvider:
    """Ollama-dialect stub that records every request body and answers by the current ask."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                with rig._lock:
                    rig.calls.append(body)
                messages = [m for m in (body.get("messages") or []) if isinstance(m, dict)]
                system = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
                joined = " ".join(str(m.get("content") or "") for m in messages)
                tool_names = [
                    str(((t or {}).get("function") or {}).get("name") or "")
                    for t in (body.get("tools") or [])
                    if isinstance(t, dict)
                ]
                has_tool_result = any(m.get("role") == "tool" for m in messages)
                if "vool_probe_add" in tool_names:
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "p1", "type": "function", "function": {"name": "vool_probe_add", "arguments": {"left": 19, "right": 23}}},
                            {"id": "p2", "type": "function", "function": {"name": "vool_probe_lookup_nonce", "arguments": {"key": "alpha"}}},
                        ],
                    }
                    return self._send({"model": "stub", "done": True, "done_reason": "stop", "message": message})
                if "vool_probe_echo" in tool_names:
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "pe", "type": "function", "function": {"name": "vool_probe_echo", "arguments": {"token": "recovered" if "recovered" in joined else "repair-me"}}}],
                    }
                    return self._send({"model": "stub", "done": True, "done_reason": "stop", "message": message})
                import re as _re

                if not tool_names and has_tool_result:
                    nonces = sorted(set(_re.findall(r"\b[0-9a-f]{12}\b", joined)))
                    reply = f"The sum is 42 and the sealed nonce is {nonces[0]}." if nonces else "The sum is 42."
                    return self._send(
                        {"model": "stub", "done": True, "done_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}
                    )
                reply = "Sure -- what would you like to know?"
                if system.startswith("You split a user's message"):
                    user_text = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
                    reply = json.dumps([{"request": user_text, "depends_on": []}])
                elif "Kanban Lantern Board" in joined and "tags column" not in joined:
                    reply = ARTIFACT
                elif REFUSAL_QUOTE_ASK[:40] in joined:
                    reply = (
                        "I cannot share that file from here. For reference, the span looks like "
                        f"this:\n```js\n{QUOTED_IN_REFUSAL}\n```\nPaste your own file and I will "
                        "continue it."
                    )
                elif "tags column" in joined or CONTINUE_AFTER_REFUSAL[:30] in joined:
                    reply = "Continued the file: added the requested column and kept all prior markup."
                self._send(
                    {
                        "model": str(body.get("model") or "stub"),
                        "done": True,
                        "done_reason": "stop",
                        "message": {"role": "assistant", "content": reply},
                        "prompt_eval_count": 300,
                        "eval_count": 40,
                    }
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _ScriptedProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


def _answer(frame: dict[str, Any]) -> str:
    message = frame.get("message")
    text = str((message or {}).get("content") or "") if isinstance(message, dict) else ""
    if not text.strip():
        commit = frame.get("vool_response_commit")
        if isinstance(commit, dict):
            text = str(commit.get("canonical_content") or "")
    return text


@pytest.mark.served
@pytest.mark.skipif(not _SERVED_AVAILABLE, reason="served rig unavailable")
def test_the_referenced_artifact_rides_the_final_provider_request_across_a_model_change(tmp_path) -> None:
    home = tmp_path / "home"
    with _ScriptedProvider() as provider:
        seed = SEED_MANIFEST.format(
            root=REPO_ROOT, base_url=provider.base_url, registered=["stub-chat:2b", "stub-chat:7b"]
        )
        run_in_home(home, seed)
        with ServedDaemon(home) as daemon:
            # The tool-probe's sealed nonce is single-use per home, so the model that will
            # need the SECOND certification of this daemon certifies FIRST.
            for model in ("stub-chat:7b", "stub-chat:2b"):
                request = Request(
                    f"{daemon.base_url}/api/model-tool-certification/run",
                    data=json.dumps(
                        {"provider_name": "ollama-local", "model_name": model, "timeout_seconds": 60}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=180) as response:
                    state = json.loads(response.read().decode("utf-8")).get("state")
                assert state == "verified", (model, state)

            session = "carry-session"

            # T1 -- the build turn, on the FIRST model. The artifact exists only after this.
            f1 = daemon.chat_stream(BUILD_ASK, session_id=session, model="stub-chat:2b")
            answer1 = _answer(f1)
            assert "Kanban Lantern Board" in answer1 or "escapeHtmlLantern" in answer1, answer1[:300]

            # T2 -- the continuation, on the SECOND model (a model change on the same session).
            # The prompt names no rebuildable detail: the distinctive edit can only arrive by
            # carrying the T1 artifact.
            before = len(provider.calls)
            f2 = daemon.chat_stream(CONTINUE_ASK, session_id=session, model="stub-chat:7b")
            answer2 = _answer(f2)
            assert answer2.strip(), answer2[:200]

            answering_calls = [
                call
                for call in provider.calls[before:]
                if not str(
                    next(
                        (m.get("content") for m in reversed(call.get("messages") or []) if m.get("role") == "system"),
                        "",
                    )
                ).startswith("You split")
            ]
            assert answering_calls, "the continuation turn never reached an answering model call"
            carried = [
                call
                for call in answering_calls
                if DISTINCTIVE_EDIT in " ".join(str(m.get("content") or "") for m in (call.get("messages") or []))
            ]
            assert carried, "the referenced artifact's distinctive edit never rode the outgoing request"
            # The same request carries the LIVE continuation ask as its current user turn.
            last = carried[-1]
            user_rows = [str(m.get("content") or "") for m in (last.get("messages") or []) if str(m.get("role")) == "user"]
            assert any("tags column" in row for row in user_rows), user_rows[:2]


@pytest.mark.served
@pytest.mark.skipif(not _SERVED_AVAILABLE, reason="served rig unavailable")
def test_a_refusal_quoting_a_fence_does_not_become_the_carried_artifact(tmp_path) -> None:
    home = tmp_path / "home"
    with _ScriptedProvider() as provider:
        seed = SEED_MANIFEST.format(
            root=REPO_ROOT, base_url=provider.base_url, registered=["stub-chat:2b"]
        )
        run_in_home(home, seed)
        with ServedDaemon(home) as daemon:
            request = Request(
                f"{daemon.base_url}/api/model-tool-certification/run",
                data=json.dumps(
                    {"provider_name": "ollama-local", "model_name": "stub-chat:2b", "timeout_seconds": 60}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=180) as response:
                assert json.loads(response.read().decode("utf-8")).get("state") == "verified"

            session = "refusal-carry-session"
            daemon.chat_stream(REFUSAL_QUOTE_ASK, session_id=session, model="stub-chat:2b")

            before = len(provider.calls)
            daemon.chat_stream(CONTINUE_AFTER_REFUSAL, session_id=session, model="stub-chat:2b")

            # The refusal's quoted span must not ride as THE CARRIED ARTIFACT: ordinary recent
            # history may still contain the refusal exchange itself, but the carry's own
            # provenance header must never introduce the quoted code as the earlier output.
            carry_header = "carried whole because this"
            for call in provider.calls[before:]:
                for message in call.get("messages") or []:
                    content = str(message.get("content") or "")
                    if carry_header in content:
                        assert QUOTED_IN_REFUSAL[:400] not in content, "the refusal's quoted fence rode as the carried artifact"
