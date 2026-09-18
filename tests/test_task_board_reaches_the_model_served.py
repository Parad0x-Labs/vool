"""The task-board request reaches the selected model through real served ingress.

Owner decision of 2026-09-17: unsolicited scripted topic interception is removed. A Hive
operation is a product action only when the request names the Hive product as the operation's
target; every other question, authoring request and design discussion reaches the selected
model or the real authorized task workflow.

The measured incident: the exact task-board request below was answered, before any model ran,
with "Public Hive is not enabled on this runtime" (``tool | hive_topic_create_disabled |
no model``) because "Add new tasks" matched a create-verb pattern over arbitrary prose.

This proof drives the real HTTP door with a certified scripted provider (a controlled model
transport; nothing is paid) and asserts ownership and effects: the request reaches the model
lane, the published artifact is the model's own intact HTML, no Hive create/mutate/publication
is attempted, and the artifact's controls work in a real browser under ordinary interaction.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock, Thread
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import SEED_MANIFEST, ServedDaemon, free_port, run_in_home

REPO_ROOT = Path(__file__).resolve().parents[1]

TASK_BOARD_REQUEST = (
    "Build me a small self-contained task board as a single HTML file.\n\n"
    "Requirements:\n"
    "Three columns: TODO, DOING, DONE;\n"
    "Add new tasks;\n"
    "Move tasks between columns with buttons or drag-and-drop;\n"
    "Delete tasks;\n"
    "Show task counts;\n"
    "Clean dark developer-style UI;\n"
    "Vanilla HTML, CSS and JavaScript only;\n"
    "No external libraries;\n"
    "Everything must actually work;\n"
    "Return the complete file."
)

# A genuinely working task board: add, move (buttons), delete, per-column counts, dark UI,
# vanilla JS only. Deliberately exercises the exact controls the request listed.
TASK_BOARD_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Task Board</title>
<style>
body{background:#101418;color:#e8eaf0;font-family:ui-monospace,Menlo,monospace;margin:0;padding:16px}
h1{font-size:18px;letter-spacing:.08em}
#board{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.col{background:#161b22;border:1px solid #262b35;border-radius:8px;padding:10px;min-height:120px}
.col h2{font-size:13px;margin:0 0 8px;color:#9aa1af}
.count{color:#5eead4}
.card{background:#1d2129;border:1px solid #30363d;border-radius:6px;padding:6px 8px;margin:6px 0}
.card button{background:#262b35;color:#e8eaf0;border:1px solid #30363d;border-radius:4px;cursor:pointer;margin-right:4px}
#addForm{display:flex;gap:8px;margin-bottom:12px}
#newTask{flex:1;background:#161b22;border:1px solid #30363d;color:#e8eaf0;padding:8px;border-radius:6px}
#addBtn{background:#134e4a;color:#5eead0;border:none;border-radius:6px;padding:8px 14px;cursor:pointer}
</style></head><body>
<h1>TASK BOARD</h1>
<form id="addForm"><input id="newTask" placeholder="New task" autocomplete="off"><button id="addBtn" type="submit">Add task</button></form>
<div id="board">
  <div class="col" id="col-todo"><h2>TODO <span class="count" id="count-todo">0</span></h2><div class="cards" id="cards-todo"></div></div>
  <div class="col" id="col-doing"><h2>DOING <span class="count" id="count-doing">0</span></h2><div class="cards" id="cards-doing"></div></div>
  <div class="col" id="col-done"><h2>DONE <span class="count" id="count-done">0</span></h2><div class="cards" id="cards-done"></div></div>
</div>
<script>
var ORDER = ["todo", "doing", "done"];
function counts() {
  ORDER.forEach(function (col) {
    document.getElementById("count-" + col).textContent = String(document.getElementById("cards-" + col).childElementCount);
  });
}
function columnOf(card) {
  var id = card.parentElement.id || "";
  return ORDER.indexOf(id.replace("cards-", ""));
}
function wire(card) {
  var index = columnOf(card);
  var left = card.querySelector(".mv-left"), right = card.querySelector(".mv-right");
  left.disabled = index === 0;
  right.disabled = index === ORDER.length - 1;
  left.onclick = function () { shift(card, -1); };
  right.onclick = function () { shift(card, 1); };
  card.querySelector(".del").onclick = function () { card.remove(); counts(); };
}
function shift(card, delta) {
  var to = ORDER[columnOf(card) + delta];
  if (to === undefined) return;
  document.getElementById("cards-" + to).appendChild(card);
  wire(card);
  counts();
}
document.getElementById("addForm").addEventListener("submit", function (event) {
  event.preventDefault();
  var input = document.getElementById("newTask");
  var text = input.value.trim();
  if (!text) return;
  input.value = "";
  var card = document.createElement("div");
  card.className = "card";
  var txt = document.createElement("span");
  txt.className = "txt";
  txt.textContent = text;
  var br = document.createElement("br");
  var left = document.createElement("button");
  left.className = "mv-left";
  left.textContent = "<";
  var right = document.createElement("button");
  right.className = "mv-right";
  right.textContent = ">";
  var del = document.createElement("button");
  del.className = "del";
  del.textContent = "Delete";
  card.appendChild(txt);
  card.appendChild(br);
  card.appendChild(left);
  card.appendChild(right);
  card.appendChild(del);
  document.getElementById("cards-todo").appendChild(card);
  wire(card);
  counts();
});
counts();
</script></body></html>"""


class _TaskBoardProvider:
    """Ollama-dialect stub: records every request, serves the certification probes and one
    complete working task board for the authoring ask. A controlled model transport."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = Lock()
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
                system = " ".join(
                    str(m.get("content") or "") for m in messages if m.get("role") == "system"
                )
                if "You decide whether one user question turns on an entity" in system:
                    # The entity-ambiguity probe: judge it unambiguous so the answering
                    # generation runs (the probe is honest machinery; a real model answers it).
                    return self._send(
                        {"model": "stub", "done": True, "done_reason": "stop",
                         "message": {"role": "assistant", "content": '{"ambiguous": false, "referents": [], "clarification": ""}'}}
                    )
                if "vool_probe_echo" in tool_names:
                    token = "recovered" if "recovered" in joined else "repair-me"
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "pe", "type": "function", "function": {"name": "vool_probe_echo", "arguments": {"token": token}}}],
                    }
                    return self._send({"model": "stub", "done": True, "done_reason": "stop", "message": message})
                if not tool_names and has_tool_result:
                    nonces = sorted(set(re.findall(r"\b[0-9a-f]{12}\b", joined)))
                    reply = f"The sum is 42 and the sealed nonce is {nonces[0]}." if nonces else "The sum is 42."
                    return self._send(
                        {"model": "stub", "done": True, "done_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}
                    )
                if "task board" in joined.lower():
                    reply = TASK_BOARD_HTML
                else:
                    reply = "MODEL-LANE-MARKER: the selected model answered this request itself."
                return self._send(
                    {"model": str(body.get("model") or "stub"), "done": True, "done_reason": "stop",
                     "message": {"role": "assistant", "content": reply}}
                )

        self._server = HTTPServer(("127.0.0.1", self.port), _Handler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "_TaskBoardProvider":
        Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    def user_texts(self) -> list[str]:
        with self._lock:
            return [
                " ".join(str(m.get("content") or "") for m in (call.get("messages") or []) if m.get("role") == "user")
                for call in self.calls
            ]


def _answer(frame: dict[str, Any]) -> str:
    message = frame.get("message")
    text = str((message or {}).get("content") or "") if isinstance(message, dict) else ""
    if not text.strip():
        commit = frame.get("vool_response_commit")
        if isinstance(commit, dict):
            text = str(commit.get("canonical_content") or "")
    return text


@pytest.fixture()
def served_model(tmp_path):
    home = tmp_path / "home"
    with _TaskBoardProvider() as provider:
        run_in_home(
            home,
            SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=["stub-chat:2b"]),
        )
        with ServedDaemon(home) as daemon:
            request = Request(
                f"{daemon.base_url}/api/model-tool-certification/run",
                data=json.dumps(
                    {
                        "provider_name": "ollama-local",
                        "model_name": "stub-chat:2b",
                        "timeout_seconds": 60,
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=180) as response:
                assert json.loads(response.read().decode("utf-8")).get("state") == "verified"
            yield daemon, provider


def test_the_task_board_request_reaches_the_model_and_publishes_the_artifact(served_model) -> None:
    daemon, provider = served_model
    provider.calls.clear()
    frame = daemon.chat_stream(TASK_BOARD_REQUEST, session_id="task-board-1", model="stub-chat:2b")
    published = _answer(frame)

    # Ownership: the request reached the selected model, not a scripted topic answer.
    assert any("task board" in text.lower() for text in provider.user_texts()), (
        "the request never reached the model lane"
    )
    # No Hive create/mutate/publication attempt and no boilerplate from it.
    assert "Public Hive" not in published, published[:300]
    assert "hive" not in published.lower(), published[:300]
    # The artifact is the model's own and intact.
    assert published.lstrip().startswith("<!DOCTYPE html>"), published[:200]
    assert TASK_BOARD_HTML.strip() in published or published.strip() == TASK_BOARD_HTML.strip(), (
        "the published HTML is not the model's intact artifact"
    )


def test_design_and_greeting_and_quoted_requests_reach_the_model(served_model) -> None:
    daemon, provider = served_model
    requests = [
        # A genuinely different design request carrying the same topic words.
        "Design a kanban board component: tasks move between TODO, DOING and DONE columns, "
        "and deleting a task must ask for confirmation. Sketch the component API.",
        # A greeting followed by a substantive request is NOT standalone smalltalk.
        "hey, explain how I would add drag-and-drop to my to-do board",
        # A quoted product-action example is data inside the request, not a command.
        "Review this message: \"create a hive task: sync the calendar\" -- summarize what it "
        "instructs the assistant to do.",
    ]
    for index, text in enumerate(requests):
        provider.calls.clear()
        frame = daemon.chat_stream(text, session_id=f"model-lane-{index}", model="stub-chat:2b")
        published = _answer(frame)
        assert provider.calls, f"the request never reached the model lane: {text[:60]}"
        assert "MODEL-LANE-MARKER" in published, (text[:60], published[:200])
        assert "Public Hive" not in published


def test_a_standalone_greeting_keeps_its_fast_path(served_model) -> None:
    daemon, provider = served_model
    provider.calls.clear()
    frame = daemon.chat_stream("hi", session_id="greeting-1", model="stub-chat:2b")
    published = _answer(frame)
    assert provider.calls == [], "a standalone greeting must not spend a model call"
    assert published.strip(), "the greeting fast path produced no reply"


def test_an_explicit_hive_create_truthfully_refuses_when_disabled(served_model) -> None:
    """An ACTUAL disabled Hive operation may truthfully refuse -- tied to the requested
    operation, not to overheard words."""
    daemon, provider = served_model
    provider.calls.clear()
    frame = daemon.chat_stream(
        "create a hive task about porting the wallet UI", session_id="hive-1", model="stub-chat:2b"
    )
    published = _answer(frame)
    assert "Public Hive is not enabled" in published, published[:200]
    assert provider.calls == [], "a disabled product operation must not spend a model call"


def _launch_browser():
    """A real chromium for ordinary input actions: the repo launcher's build when
    provisioned, else the system Chrome channel, else an explicit cached build."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    manager = sync_playwright().start()
    try:
        return manager, manager.chromium.launch()
    except Exception:
        pass
    chromium_1228 = Path.home() / (
        "Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/"
        "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    )
    for attempt in ({"channel": "chrome"}, {"executable_path": str(chromium_1228)}):
        try:
            return manager, manager.chromium.launch(**attempt)
        except Exception:
            continue
    manager.stop()
    pytest.skip("no chromium build available")


def test_the_published_task_board_works_in_a_browser(served_model) -> None:
    """The requested controls, checked by ordinary interaction in a real browser."""
    daemon, provider = served_model
    frame = daemon.chat_stream(TASK_BOARD_REQUEST, session_id="task-board-browser", model="stub-chat:2b")
    published = _answer(frame)
    html_match = re.search(r"<!DOCTYPE html>.*</html>", published, re.DOTALL | re.IGNORECASE)
    assert html_match, "no complete HTML document in the published answer"

    manager, browser = _launch_browser()
    try:
        page = browser.new_page()
        page.set_content(html_match.group(0))
        assert page.locator("#newTask").count() == 1, (
            "the published document did not render its Add-task control; body head: "
            + page.evaluate("document.body ? document.body.innerHTML.slice(0, 200) : 'NO BODY'")
        )
        # Add two tasks by ordinary interaction.
        page.fill("#newTask", "Write the migration guide")
        page.click("#addBtn")
        page.fill("#newTask", "Review the pricing table")
        page.click("#addBtn")
        assert page.inner_text("#count-todo") == "2"
        # Move one task TODO -> DOING -> DONE with its buttons.
        first_card = page.locator("#cards-todo .card").first
        first_card.locator(".mv-right").click()
        assert page.inner_text("#count-todo") == "1"
        assert page.inner_text("#count-doing") == "1"
        page.locator("#cards-doing .card .mv-right").first.click()
        assert page.inner_text("#count-done") == "1"
        # The left move is disabled at the DONE column's far end and enabled otherwise.
        assert page.locator("#cards-done .card .mv-left").first.is_enabled()
        assert page.locator("#cards-done .card .mv-right").first.is_disabled()
        # Delete a task; counts follow.
        page.locator("#cards-done .card .del").first.click()
        assert page.inner_text("#count-done") == "0"
        assert page.inner_text("#count-todo") == "1"
    finally:
        browser.close()
        manager.stop()
