"""Live seat dispatch: one seat turn = one REAL agent turn through the app's own door.

Each seat runs as a normal `/api/chat` turn in its own chat session, in PLAN mode —
the controller-owned permission matrix allows reads/listing/safe commands and DENIES
writes at dispatch depth, so a reviewer that tries to "helpfully fix" what it reviews
is stopped by trusted application state, not by prompt text. The seat's session is a
real, clickable chat: the investigation IS the audit trail.

Spend law: the council NEVER self-authorizes payment. A cloud seat model is pinned
without `confirm_paid`; if the server gates it as paid, the pin is retried with the
confirmation ONLY when the operator's acceptance ledger already holds that exact model
(re-affirming a recorded ceiling). A model the operator never accepted — or one whose
price rose above the accepted ceiling — fails the seat typed instead of spending.

v1 dispatches seats serially because the composer pin is global runtime state; the
round BARRIER in the orchestrator is semantic and survives a future parallel dispatch
unchanged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from core.council import containment
from core.council.containment import seat_session_id_for
from core.council.orchestrator import Seat

#: Wall-clock bound for one seat turn. Investigations run real tool loops over a real
#: workspace; this is a read-timeout (wall clock), never an output ceiling.
SEAT_TURN_TIMEOUT_SECONDS = 15 * 60

#: The header every council-owned request presents to pass the model-pin fence
#: (``core/council/pin_lock.py``): the seat turns, the per-seat pin, and the final
#: restoration. It carries a capability minted by the server when the run took the pin —
#: never the run id, never a seat session id: both are things a caller can learn, and
#: neither is authority.
DISPATCH_CAPABILITY_HEADER = "X-Vool-Council-Dispatch"


class SeatDispatchError(RuntimeError):
    """Typed seat-turn failure the orchestrator converts into a failed report.

    ``attempt_outcome`` names the :class:`core.council.attempts.AttemptOutcome` this
    fault IS, so the orchestrator classifies it without importing dispatch (which would
    close an import cycle) and without guessing from the message text. Unset means "let
    the orchestrator decide" — a plain transport fault.
    """

    attempt_outcome: str = ""


class SeatEmptyReportError(SeatDispatchError):
    """The turn completed and produced no text. An answer-shaped hole, not a crash."""

    attempt_outcome = "EMPTY"


class SeatTurnTimeoutError(SeatDispatchError):
    """The seat turn hit its wall-clock read timeout. Needs more clock, not a new provider."""

    attempt_outcome = "TIMED_OUT"


class SeatCancelledError(SeatDispatchError):
    """The run was fenced — before this turn was dispatched, or while it was in flight.

    Distinct from a transport fault on purpose: a cancelled turn must never be retried, and
    must never be recorded as a seat that failed. The operator stopped it.
    """

    attempt_outcome = "CANCELLED"


def _post_json(
    base_url: str,
    path: str,
    body: dict[str, Any],
    timeout: float = 30.0,
    capability: str = "",
) -> tuple[int, dict[str, Any]]:
    headers = {"Content-Type": "application/json"}
    if capability:
        headers[DISPATCH_CAPABILITY_HEADER] = capability
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry the typed body
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        return exc.code, payload


def _model_is_cloud(model: str) -> bool:
    return "/" in str(model or "")


def _acceptance_recorded(model: str) -> bool:
    try:
        from core.model_price_acceptance import list_acceptances

        wanted = str(model or "").strip().lower()
        return any(
            str(row.get("model") or "").strip().lower() == wanted
            for row in list_acceptances()
        )
    except Exception:
        return False  # fail toward NOT spending


def ensure_seat_model_pinned(base_url: str, model: str, capability: str = "") -> None:
    """Pin a cloud seat model within the operator's existing authorization only.

    ``capability`` is the run's own pass through the model-pin fence — the SAME secret the
    seat turns present, never a second one. Without it the council's own write is refused
    by its own fence, which is the correct failure: loudly, not a hole.
    """
    status, payload = _post_json(base_url, "/api/cloud/model", {"model": model}, capability=capability)
    if status == 200 and payload.get("ok"):
        return
    code = str(payload.get("code") or "")
    if status == 409 and code == "paid_model_confirm_required" and _acceptance_recorded(model):
        status, payload = _post_json(
            base_url, "/api/cloud/model", {"model": model, "confirm_paid": True},
            capability=capability,
        )
        if status == 200 and payload.get("ok"):
            return
        raise SeatDispatchError(
            f"model_pin_failed: {model}: {payload.get('error') or status}"
        )
    if status == 409 and code == "paid_model_confirm_required":
        raise SeatDispatchError(
            f"model_not_accepted: {model} is paid and the operator has never accepted its "
            "price — accept it once in the model selector, then reconvene. The council does "
            "not authorize spend."
        )
    if status == 409:
        raise SeatDispatchError(
            f"model_gated: {model}: {payload.get('error') or code or status} — operator "
            "decision required; the council does not override price gates."
        )
    raise SeatDispatchError(f"model_pin_failed: {model}: {payload.get('error') or status}")


def read_current_pin(base_url: str) -> str:
    try:
        request = urllib.request.Request(base_url + "/api/cloud/status")
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
        return str(payload.get("model") or "")
    except Exception:
        return ""


def restore_pin(base_url: str, model: str, capability: str = "") -> None:
    """Hand the operator's pin back. Runs while the council still OWNS the fence, so it
    carries the run's capability like every other council write — the release comes after."""
    if not str(model or "").strip():
        return
    # Restoring what was already pinned re-affirms at most an already-accepted ceiling.
    status, payload = _post_json(base_url, "/api/cloud/model", {"model": model}, capability=capability)
    if status == 409 and _acceptance_recorded(model):
        _post_json(
            base_url, "/api/cloud/model", {"model": model, "confirm_paid": True},
            capability=capability,
        )


def seat_session_id(run_id: str, seat: Seat) -> str:
    """The CANONICAL chat id this seat's turn is persisted under.

    ``/api/chat`` passes a canonical id (``openclaw:`` + 20 lowercase hex) through
    verbatim and RE-HASHES anything else. The previous form — stripped run and seat text
    concatenated — was 18 mixed characters, so every seat's turn was persisted under
    ``sha256(that)[:20]`` while the report recorded the pre-hash string. The pointer on
    every SeatReport named a chat that did not exist, and the module docstring's promise
    that "the investigation IS the audit trail" was true of the chats and false of the
    only link to them.

    Deriving the id the same way the server would makes recorded and persisted the same
    value by construction, rather than by two places agreeing to compute alike.

    The derivation itself now lives in `core.council.containment`, which has to register a
    run's seat sessions BEFORE any of them speaks — the containment fence must exist before
    the first turn, not after it. Same reason as above, one layer down: recorded, persisted
    and FENCED are the same value by construction rather than by three places hashing alike.
    """
    return seat_session_id_for(run_id, seat.seat_id)


#: Identity strength, strongest last. `requested` is on the ladder so "the stream told us
#: what was asked for" is distinguishable from "the stream told us nothing" — but it never
#: becomes `model_actual`: what a request names is not proof of what answered.
_EVIDENCE_RANK = {"unknown": 0, "requested": 1, "selected": 2, "actual_adapter": 3}


def merged_usage(cost_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """The turn's measured spend, summed over the provider calls the stream reported.

    One seat turn with a tool loop makes several provider calls; each arrives as a typed
    ``cloud.cost_updated`` event carrying what the PROVIDER reported. The sum is the
    measurement; ``complete`` says whether every call reported tokens. A turn whose calls
    went unreported yields zeros-with-``complete: false`` — a stated lower bound, never a
    fabricated measurement — and ``usd_actual`` stays None until some provider names a
    number, because estimating a price here would be a spend claim this module cannot back.
    """
    usage: dict[str, Any] = {
        "cost_class": "",
        "prompt_tokens": 0,
        "output_tokens": 0,
        "usd_actual": None,
        "complete": bool(cost_blocks),
    }
    for block in cost_blocks:
        prompt, output = block.get("prompt_tokens"), block.get("output_tokens")
        if isinstance(prompt, int) and isinstance(output, int):
            usage["prompt_tokens"] += prompt
            usage["output_tokens"] += output
        else:
            usage["complete"] = False
        cost_class = str(block.get("cost_class") or "").strip()
        if cost_class:
            usage["cost_class"] = cost_class
        usd = block.get("usd_actual")
        if isinstance(usd, (int, float)) and usd >= 0:
            usage["usd_actual"] = (usage["usd_actual"] or 0.0) + float(usd)
    return usage


def _qualified(provider: str, model: str) -> str:
    provider, model = str(provider or "").strip(), str(model or "").strip()
    if not model:
        return ""
    return f"{provider}/{model}" if provider and "/" not in model else model


def model_identity_from_event(block: dict[str, Any]) -> tuple[str | None, str]:
    """``(model_actual, evidence)`` from one streamed event's model block.

    The authority ladder is the one `core/task_event_model.py` already documents and is
    deliberately ONE-WAY: adapter execution proof outranks router selection, and neither
    may be manufactured from the weaker fields below it. A block that carries only
    request-strength identity yields ``(None, "requested")`` — the label records how far
    the evidence got, and `model_actual` stays empty rather than echoing the ask back as
    though the runtime had confirmed it.
    """
    for provider_key, model_key, strength in (
        ("actual_adapter_provider_id", "actual_adapter_model_id", "actual_adapter"),
        ("selected_provider_id", "selected_model_id", "selected"),
    ):
        qualified = _qualified(block.get(provider_key), block.get(model_key))
        if qualified:
            return qualified, strength
    requested = str(block.get("requested_model_id") or block.get("requested_model") or "").strip()
    return (None, "requested") if requested else (None, "unknown")


def live_seat_turn_factory(base_url: str, dispatch_capability: str = ""):
    """Builds the orchestrator's SeatTurnFn bound to this server's own address.

    ``dispatch_capability`` is the run's own pass through the model-pin fence. Empty means
    the seat turns will be refused like any other chat turn while the pin is held — which
    is the correct failure, loudly, rather than a fence with a hole in it.
    """

    def live_seat_turn(seat: Seat, prompt: str, round_no: int, run_id: str) -> dict[str, Any]:
        # BEFORE the pin and before the turn. A model pin is a request and a seat turn on a
        # cloud model is money, so a run the operator has stopped must reach neither. The
        # orchestrator's own stop flag is read between ATTEMPTS and cannot cover a stop that
        # lands between "we decided to dispatch" and "we dispatched".
        if containment.run_is_fenced(run_id):
            raise SeatCancelledError(
                f"council run {run_id} was stopped; no further seat turn was dispatched"
            )
        model_field = "vool"
        selection = "auto"
        seat_model = str(seat.model or "").strip()
        if seat_model and seat_model.lower() not in {"auto", "vool"}:
            if _model_is_cloud(seat_model):
                ensure_seat_model_pinned(base_url, seat_model, dispatch_capability)
            model_field = seat_model
            selection = "sticky"
        body = {
            "model": model_field,
            "model_selection": selection,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_task_events": True,
            "session_id": seat_session_id(run_id, seat),
            "turn_id": f"{run_id}-r{round_no}-{seat.seat_id}",
            "mode": "plan",
            "autonomy": "",
        }
        seat_headers = {"Content-Type": "application/json"}
        if dispatch_capability:
            seat_headers[DISPATCH_CAPABILITY_HEADER] = dispatch_capability
        request = urllib.request.Request(
            base_url + "/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers=seat_headers,
            method="POST",
        )
        # Registered BEFORE the connection opens, so a stop racing the very first byte still
        # finds a handle to cancel. `holder` exists because the response does not yet — the
        # closer has to be able to close whatever this turn ends up holding.
        holder: dict[str, Any] = {"response": None}

        def _close_stream() -> None:
            response = holder.get("response")
            if response is not None:
                response.close()

        inflight_token = containment.register_inflight(
            run_id,
            seat_id=seat.seat_id,
            session_id=body["session_id"],
            turn_id=body["turn_id"],
            closer=_close_stream,
        )
        try:
            return _read_seat_turn(request, body, seat_model, holder, run_id)
        except SeatDispatchError:
            raise
        except Exception as exc:
            # The stream died. If the run was fenced, THIS is what cancelling it looks like
            # from in here — `_cancel_inflight` closed the response underneath the reader —
            # and it is a cancellation, not a transport fault to retry.
            if containment.run_is_fenced(run_id):
                raise SeatCancelledError(
                    f"council run {run_id} was stopped while this seat turn was in flight"
                ) from exc
            raise
        finally:
            containment.clear_inflight(run_id, inflight_token)

    def _read_seat_turn(request, body, seat_model, holder, run_id):
        text_parts: list[str] = []
        receipt_count = 0
        cost_blocks: list[dict[str, Any]] = []
        # Provenance starts at "we asked for X and know nothing else". It is only ever
        # RAISED by evidence arriving on the wire — never seeded from the request.
        model_actual: str | None = None
        model_evidence = "unknown"
        try:
            response_cm = urllib.request.urlopen(request, timeout=SEAT_TURN_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise SeatTurnTimeoutError(
                f"seat turn exceeded {SEAT_TURN_TIMEOUT_SECONDS}s: {exc}"
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise SeatTurnTimeoutError(
                    f"seat turn exceeded {SEAT_TURN_TIMEOUT_SECONDS}s: {exc.reason}"
                ) from exc
            raise
        holder["response"] = response_cm
        with response_cm as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    decoded = json.loads(line)
                except ValueError:
                    continue
                if "vool_event" in decoded:
                    event = decoded.get("vool_event") or {}
                    # The served stream carries events through `build_task_event`, which
                    # renames the raw `event_type` to `type` — reading the raw key here
                    # counted zero receipts on every real turn and left a receipt-backed
                    # counterexample unable to fire live. `event_type` is accepted only as
                    # a legacy alias, never as the primary reading.
                    event_type = str(event.get("type") or event.get("event_type") or "")
                    if event_type in {"tool.completed", "tool.succeeded"}:
                        receipt_count += 1
                    if event_type == "cloud.cost_updated":
                        block = event.get("cost")
                        if isinstance(block, dict):
                            cost_blocks.append(block)
                    block = event.get("model")
                    if isinstance(block, dict):
                        candidate, strength = model_identity_from_event(block)
                        # Monotonic: a later weaker event cannot demote identity a
                        # stronger one already established. A model switch mid-turn
                        # arrives as another actual_adapter block and wins on recency
                        # only because it ties on strength.
                        if _EVIDENCE_RANK[strength] >= _EVIDENCE_RANK[model_evidence]:
                            model_evidence = strength
                            if candidate:
                                model_actual = candidate
                    continue
                chunk = (decoded.get("message") or {}).get("content") or ""
                if chunk:
                    text_parts.append(chunk)
                if decoded.get("done"):
                    break
        text = "".join(text_parts).strip()
        if not text:
            raise SeatEmptyReportError("seat turn produced no text")
        return {
            "text": text,
            "receipt_count": receipt_count,
            "session_id": body["session_id"],
            # What the provider reported spending on this turn, summed over its calls.
            # Measured on the wire; `complete: false` names a lower bound.
            "usage": merged_usage(cost_blocks),
            # What the operator ASKED for, and separately what the runtime says actually
            # answered. Two fields because they are two facts, and the second is empty
            # when nothing proved it — never a copy of the first.
            "model_requested": seat_model,
            "model_actual": model_actual,
            "model_evidence": model_evidence,
        }

    return live_seat_turn
