"""Model-loop agents: one real VOOL chat turn per agent, on the agent's own model.

Each agent runs in its own chat session through the app's own ``/api/chat`` door, exactly like a
council seat (``core/council/dispatch.py``), with three differences:

* **Its own model, without touching anyone else's.** The model travels on the request
  (``model`` + ``model_selection: sticky``). A cloud model is first made resolvable for THAT
  session through ``POST /api/cloud/model`` with ``session_id`` — the per-chat selection rail,
  which registers an explicit-only lane and never moves the machine's global pin. Ordinary chat
  turns keep resolving exactly as before: an explicit-only lane is excluded from automatic
  ranking unless a request names it.
* **Spend is reserved before the call.** The most the turn can cost (prompt estimate + output
  ceiling, at the operator's accepted price) is reserved against the agent and team limits; a
  refused reservation means the call is never made. The provider's reported usage settles it.
  An unreported usage books the full reservation — a lower bound is never treated as zero.
* **Never self-authorizes payment.** A paid model the operator never accepted is refused.

Read agents run in PLAN mode: the controller's permission matrix denies writes at that depth.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from core.prompt_assembly_report import estimate_tokens

DEFAULT_OUTPUT_CEILING = 2048
#: Fixed allowance for the system prompt and tool schemas VOOL adds around the brief.
PROMPT_OVERHEAD_TOKENS = 6000
TURN_TIMEOUT_SECONDS = 15 * 60


class ModelAgentRefused(RuntimeError):
    """The agent could not start its turn (model not accepted, gate missing, budget)."""


def agent_session_id(team_id: str, agent_id: str) -> str:
    material = f"agent-team:{team_id}:{agent_id}"
    return "openclaw:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _is_cloud(model: str) -> bool:
    return "/" in str(model or "")


def _split(model: str) -> tuple[str, str]:
    provider, sep, rest = str(model).partition(":")
    if sep and "/" in rest and "/" not in provider:
        return provider, rest
    return "openrouter", str(model)


def usd_estimate(model: str, prompt_tokens: int, output_tokens: int) -> float:
    """Most the call can cost at the operator's ACCEPTED price. Local models cost 0.

    A cloud model with no accepted price is refused: the team does not authorize spend."""
    if not _is_cloud(model):
        return 0.0
    from core.model_price_acceptance import acceptance_for

    provider, model_id = _split(model)
    accepted = acceptance_for(provider, model_id)
    if accepted is None:
        if str(model).endswith(":free"):
            return 0.0
        raise ModelAgentRefused(
            f"{model} is not a model the operator has accepted a price for; accept it once in the "
            "model selector. The team does not authorize spend."
        )
    prompt_rate = float(accepted.get("prompt_usd_per_m") or 0.0) / 1_000_000
    output_rate = float(accepted.get("completion_usd_per_m") or 0.0) / 1_000_000
    return prompt_tokens * prompt_rate + output_tokens * output_rate


@dataclass
class TurnResult:
    text: str
    receipts: int
    prompt_tokens: int
    output_tokens: int
    usd: float | None
    usage_complete: bool
    model_actual: str | None
    session_id: str


def _post(base_url: str, path: str, body: dict[str, Any], timeout: float = 30.0) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        base_url + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {}


def select_session_model(base_url: str, session_id: str, model: str) -> None:
    """Make ``model`` resolvable for this one session (cloud models only). Never the global pin."""
    if not _is_cloud(model):
        return
    status, payload = _post(base_url, "/api/cloud/model", {"model": model, "session_id": session_id})
    if status == 200 and payload.get("ok"):
        return
    code = str(payload.get("code") or "")
    if status == 409 and code == "paid_model_confirm_required":
        from core.council.dispatch import _acceptance_recorded

        if _acceptance_recorded(_split(model)[1]):
            status, payload = _post(base_url, "/api/cloud/model",
                                    {"model": model, "session_id": session_id, "confirm_paid": True})
            if status == 200 and payload.get("ok"):
                return
    raise ModelAgentRefused(f"could not select {model} for the agent's chat: {payload.get('error') or code or status}")


class HttpChatRunner:
    """Runs one agent turn through ``/api/chat`` on ``base_url`` and reads the event stream."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def run(self, *, session_id: str, model: str, prompt: str, mode: str, turn_id: str,
            cancel: threading.Event, on_response: Callable[[Any], None], workspace: str = "") -> TurnResult:
        from core.council.dispatch import _EVIDENCE_RANK, merged_usage, model_identity_from_event

        select_session_model(self.base_url, session_id, model)
        body = {
            "model": model, "model_selection": "sticky",
            "messages": [{"role": "user", "content": prompt}],
            "stream": True, "stream_task_events": True,
            "session_id": session_id, "turn_id": turn_id,
            "mode": "plan" if mode == "read" else "", "autonomy": "",
            # An agent's turn is not the owner speaking: the server keeps it out of the owner's profile
            # and preferences (otherwise an agent's report format was saved as the owner's preference,
            # measured on the live comparison, 2026-10-07).
            "turn_author": "agent",
        }
        if workspace:
            # The agent works in the TEAM's folder. Without this the turn runs as a general chat with no
            # project binding, and VOOL (rightly) refuses to guess a folder to read (measured on the live
            # comparison, 2026-10-07: every agent answered "this chat is not bound to a project folder").
            body["workspace"] = workspace
        request = urllib.request.Request(
            self.base_url + "/api/chat", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        text_parts: list[str] = []
        receipts = 0
        cost_blocks: list[dict[str, Any]] = []
        model_actual: str | None = None
        evidence = "unknown"
        try:
            response_cm = urllib.request.urlopen(request, timeout=TURN_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            # The server refused the turn before it ran (for example a council owns the global model pin).
            # That is a refusal with a reason the operator can act on, not an opaque transport fault.
            try:
                refusal = json.loads(exc.read().decode("utf-8") or "{}")
            except (ValueError, OSError):
                refusal = {}
            code = str(refusal.get("error") or refusal.get("code") or exc.code)
            if code == "council_model_pin_active":
                raise ModelAgentRefused(
                    "a council holds VOOL's model pin, so the agent's turn was refused before any model ran; "
                    "start the agent again when the council finishes") from None
            raise ModelAgentRefused(f"the chat turn was refused ({exc.code} {code})") from None
        on_response(response_cm)
        with response_cm as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ModelAgentRefused(f"chat turn answered {status}")
            for raw in response:
                if cancel.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    decoded = json.loads(line)
                except ValueError:
                    continue
                if "vool_event" in decoded:
                    event = decoded.get("vool_event") or {}
                    kind = str(event.get("type") or event.get("event_type") or "")
                    if kind in {"tool.completed", "tool.succeeded"}:
                        receipts += 1
                    if kind == "cloud.cost_updated" and isinstance(event.get("cost"), dict):
                        cost_blocks.append(event["cost"])
                    block = event.get("model")
                    if isinstance(block, dict):
                        candidate, strength = model_identity_from_event(block)
                        if _EVIDENCE_RANK[strength] >= _EVIDENCE_RANK[evidence]:
                            evidence = strength
                            model_actual = candidate or model_actual
                    continue
                if "error" in decoded and not decoded.get("message"):
                    raise ModelAgentRefused(str(decoded.get("error")))
                chunk = (decoded.get("message") or {}).get("content") or ""
                if chunk:
                    text_parts.append(chunk)
                if decoded.get("done"):
                    break
        usage = merged_usage(cost_blocks)
        return TurnResult(
            text="".join(text_parts).strip(), receipts=receipts,
            prompt_tokens=int(usage["prompt_tokens"]), output_tokens=int(usage["output_tokens"]),
            usd=usage["usd_actual"], usage_complete=bool(usage["complete"]),
            model_actual=model_actual, session_id=session_id,
        )


def build_prompt(*, display_name: str, objective: str, claims: tuple[str, ...], mode: str,
                 constraints: str, extra: str = "", decision: str = "") -> str:
    """The agent's brief as ONE sentence.

    VOOL's turn planner splits a message into separate demands at sentence, line and paragraph breaks. A brief
    written as paragraphs became five asks, each answered or refused on its own, and the file the agent was meant
    to read was never read (measured on the live comparison, 2026-10-07). So the brief is one sentence: the task,
    then the report format and the scope as clauses of the same request. Read agents run in PLAN mode, where the
    server itself denies writes, pushes and spends; write agents also carry the never-do rules and the parent's
    own constraints, as a clause.
    """
    task = " ".join((extra.strip() or objective).split()).rstrip(". ")
    clauses = [task]
    if decision:
        clauses.append(f"given that the user decided {decision.strip().rstrip('.')}")
    clauses.append(
        # FIRST line of the reply, not last: VOOL trims long replies to the turn's output budget, and a report
        # line at the end is the first thing a trim removes (measured on the served run, 2026-10-06).
        "beginning your reply with one line `RESULT: {\"status\": \"done|partial|needs_decision|failed\", "
        "\"summary\": \"at most 3 sentences\", \"changed\": [paths], \"question\": \"only if needs_decision\"}`"
    )
    if mode == "write":
        rules = "; ".join(line.strip().lstrip("-").strip().removeprefix("Never: ").rstrip(".")
                          for line in constraints.splitlines() if line.strip())
        clauses.append("changing files only inside " + ", ".join(f"{c}/" for c in claims)
                       + (f" and never doing any of these: {rules.replace('. ', '; ')}" if rules else ""))
    else:
        clauses.append("changing no file")
    clauses.append(f"as the agent \"{display_name}\" in a VOOL team")
    return ", ".join(clauses) + "."


def parse_result_line(text: str) -> dict[str, Any]:
    """The agent's typed report: the `RESULT:` line, or a bare JSON object line carrying `status`.

    VOOL's reply shaping can drop the `RESULT:` label and keep the object (measured on the served run,
    2026-10-07: replies arrived as `{"status": "done", ...}` on their first line), so the object is
    accepted with or without its label. Anything else is not a report.
    """
    for line in str(text or "").splitlines():
        # A bulleted object is NOT a report: VOOL lists the statements it withheld as unsupported as
        # bullets ("Withheld from this answer: … - {...}"), and a withheld report must stay withheld.
        stripped = line.strip()
        if stripped.upper().startswith("RESULT:"):
            stripped = stripped.split(":", 1)[1].strip()
        elif not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("status"), str):
            return payload
    return {}


def reservation_for(model: str, prompt: str, output_ceiling: int) -> tuple[int, float]:
    prompt_tokens = estimate_tokens(prompt) + PROMPT_OVERHEAD_TOKENS
    tokens = prompt_tokens + int(output_ceiling)
    return tokens, usd_estimate(model, prompt_tokens, int(output_ceiling))


__all__ = [
    "DEFAULT_OUTPUT_CEILING",
    "HttpChatRunner",
    "ModelAgentRefused",
    "TurnResult",
    "agent_session_id",
    "build_prompt",
    "parse_result_line",
    "reservation_for",
    "select_session_model",
    "usd_estimate",
]
