"""The matrix: intents x phrasings x workspaces x lanes, scored on real outcomes.

Every fix before this was driven against one folder, with one phrasing the fixer chose, on one
model, and called done. Three times that produced a green suite over a broken product. This module
exists so "it works" has to survive being asked eight different ways, in three different workspaces,
on four different lanes — and so a fix that helps one cell and breaks another is visible rather than
averaged into a pass.

Scoring is on OUTCOMES, never on strings in the reply:
  * was the named file actually opened (a read receipt for that path)
  * did the turn produce a non-empty answer
  * did the expected tool run
  * is a completion claim backed by a record

Two kinds of cell, because they cannot be measured the same way:

  ROUTING cells run with model="vool" (Auto). Pinning a model sets `requested_model`, which trips
  `explicit_model_owns_semantic_turn` — so a pinned turn is a poor place to read routing from.
  Learned the hard way: an early drive pinned qwen3:8b and some fast paths vanished, which read as
  "the product is broken" when it was the harness.

  CORRECTED 2026-08-14, because the earlier wording here said pinning "disables every front-door
  fast path" and that is not true. `explicit_model_owns_semantic_turn` is consulted at exactly two
  live call sites (`turn_frontdoor` semantic arbitration and `builder/pinned_generation`; a third in
  `builder_facade` is commented out). Date/time, smalltalk, direct math, live-info, credit-status,
  machine reads and the whole `fast_command_surface` ladder ALL still fire on a pinned turn —
  measured: a pinned `vool-local-only` drive answered through
  `route=deterministic:live_data_typed_plan`.

  This matters in the direction opposite to the original warning. Believing the old sentence makes a
  fast path that DID fire on a pinned turn read as impossible, so a real runtime defect gets written
  off as a harness artifact. Read the turn's actual `route` rather than assuming from the pin.

  LANE cells pin a provider on purpose, to ask whether THAT lane can carry a turn end to end.

The stub lane needs no daemon and always runs. Live lanes are opt-in: VOOL_LIVE_LANES=1.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BASE = os.environ.get("VOOL_BASE_URL", "http://127.0.0.1:11435")
LIVE = os.environ.get("VOOL_LIVE_LANES", "").strip().lower() in {"1", "true", "yes", "on"}

# Resolved from $HOME rather than written out, so no build-machine username is committed and the
# matrix runs unchanged on another box. Override with VOOL_OWNER_WORKSPACE to point it elsewhere.
OWNER_WORKSPACE = Path(
    os.environ.get("VOOL_OWNER_WORKSPACE") or (Path.home() / "Desktop" / "VOOL WEBSITE")
)
REPO_WORKSPACE = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------------------
# Phrasings — eight or more per intent, and never only the one the fix was written against
# --------------------------------------------------------------------------------------

WORKSPACE_IDENTITY = (
    # verbatim from the owner's transcript, typos intact
    "what folder is in use for this workspace?",
    "do you see what folder our workspace is set on?",
    # imperatives — a question-mark-only detector misses all of these
    "tell me which folder this chat is bound to",
    "show me the workspace folder",
    "confirm the workspace path",
    # no apostrophe, the way people actually type
    "whats the current workspace",
    # indirect
    "am i in the right workspace?",
    "which project am i working in",
    "name the folder you are scoped to",
)

NAMED_FILE_AUDIT = (
    "great, i need you to run audit for this - app-landing/index.html tell me if u can spot any "
    "security issues and such?",
    "audit app-landing/index.html",
    "check app-landing/index.html for security problems",
    "can you review app-landing/index.html",
    "look at app-landing/index.html and tell me whats wrong with it",
    "security review of app-landing/index.html please",
    "go through app-landing/index.html and flag anything dangerous",
    "is app-landing/index.html safe?",
)

FOLDER_OVERVIEW = (
    "what is this project about",
    "explain this codebase",
    "give me an overview of this folder",
    "whats in this repo",
    "describe the project",
    "walk me through this directory",
    "summarise what this code does",
    "check this folder and tell me what it is",
)

GREETING = ("Hey", "hi", "hello there", "yo", "good morning", "hey!", "sup", "hi again")


# --------------------------------------------------------------------------------------
# A scored cell
# --------------------------------------------------------------------------------------


@dataclass
class Cell:
    intent: str
    phrasing: str
    workspace: str
    lane: str
    passed: bool = False
    detail: str = ""
    seconds: float = 0.0
    answer: str = ""
    tools: tuple[str, ...] = ()


@dataclass
class Scoreboard:
    cells: list[Cell] = field(default_factory=list)

    def add(self, cell: Cell) -> None:
        self.cells.append(cell)

    def failures(self) -> list[Cell]:
        return [cell for cell in self.cells if not cell.passed]

    def render(self) -> str:
        """intent x lane, so a fix that trades one cell for another cannot hide in an average."""

        intents = sorted({cell.intent for cell in self.cells})
        lanes = sorted({cell.lane for cell in self.cells})
        width = max((len(i) for i in intents), default=10) + 2
        rows = ["".ljust(width) + "".join(lane[:14].ljust(16) for lane in lanes)]
        for intent in intents:
            row = intent.ljust(width)
            for lane in lanes:
                sel = [c for c in self.cells if c.intent == intent and c.lane == lane]
                if not sel:
                    row += "-".ljust(16)
                    continue
                ok = sum(1 for c in sel if c.passed)
                row += f"{ok}/{len(sel)}".ljust(16)
            rows.append(row)
        lines = ["", "\n".join(rows), ""]
        for cell in self.failures():
            lines.append(
                f"  FAIL [{cell.lane}] {cell.intent}: {cell.detail}\n"
                f"        said: {cell.phrasing[:88]}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Driving a real turn
# --------------------------------------------------------------------------------------


def _post(path: str, body: dict, timeout: float = 900.0) -> dict:
    request = urllib.request.Request(
        f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "{}")


def daemon_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/healthz", timeout=3):
            return True
    except Exception:
        return False


def bind_session(session_id: str, project_id: str) -> None:
    _post("/api/chat/session", {"session_id": session_id, "project_id": project_id}, timeout=30)


def ensure_project(name: str, root: str) -> str:
    """Register a workspace, tolerating one that already exists."""

    try:
        payload = _post("/api/projects", {"name": name, "root": root}, timeout=30)
        return str((payload.get("project") or payload).get("id") or "")
    except urllib.error.HTTPError:
        with urllib.request.urlopen(f"{BASE}/api/projects", timeout=10) as response:
            for project in json.loads(response.read().decode()).get("projects") or []:
                if str(project.get("root") or "") == root:
                    return str(project.get("id") or "")
    return ""


def drive(session_id: str, turn_id: str, text: str, *, model: str = "vool") -> tuple[str, tuple[str, ...], float]:
    """One turn. Returns (answer, executed tool intents, seconds).

    Streaming on purpose: the blank-bubble failure lived in the streaming surface, so a buffered
    call would prove nothing about it.
    """

    body = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "stream": True,
        "stream_task_events": True,
        "session_id": session_id,
        "turn_id": turn_id,
        "mode": "daily",
    }
    request = urllib.request.Request(
        f"{BASE}/api/chat", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.time()
    chunks: list[str] = []
    tools: list[str] = []
    with urllib.request.urlopen(request, timeout=900) as response:
        for line in response:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            piece = ((event.get("message") or {}).get("content")) or ""
            if piece:
                chunks.append(piece)
            node = event.get("vool_event") or {}
            if node.get("tool"):
                tools.append(str(node["tool"]))
    return "".join(chunks), tuple(tools), time.time() - started


def turn_events(session_id: str) -> list[dict]:
    """The ledger, which keeps the fields the stream projection drops (reason, prompt_budget...)."""

    url = f"{BASE}/api/runtime/events?session={urllib.parse.quote(session_id)}&limit=400"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.loads(response.read().decode()).get("events") or []
    except Exception:
        return []
