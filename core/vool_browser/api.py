"""The runtime tool door for the browser lane.

`handle_intent(intent, arguments)` is what `core.runtime_execution_tools` calls.
It returns the runtime's standard result shape: ok / status / response_text /
details{observation}. Every executed operation appends a receipt to the
session's receipts file; page content never appears anywhere except wrapped in
the untrusted-evidence envelope.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.vool_browser import ops, shopping
from core.vool_browser.receipts import append_receipt, build_receipt, verify_receipts_file
from core.vool_browser.sessions import (
    OpFailed,
    OpTimeout,
    close_session,
    get_handle,
    open_session,
    record_receipt,
    registry_status,
)

TOOL_SURFACE = "vool_browser"

LANE_INTENTS: dict[str, dict[str, Any]] = {
    "vool-browser.session.open": {"op": "session.open", "write": True},
    "vool-browser.session.close": {"op": "session.close", "write": True},
    "vool-browser.session.status": {"op": "session.status", "write": False},
    "vool-browser.navigate": {"op": "navigate", "write": False},
    "vool-browser.inspect": {"op": "inspect", "write": False},
    "vool-browser.click": {"op": "click", "write": False},
    "vool-browser.type": {"op": "type", "write": False},
    "vool-browser.assert": {"op": "assert", "write": False},
    "vool-browser.screenshot": {"op": "screenshot", "write": True},
    "vool-browser.download": {"op": "download", "write": True},
    "vool-browser.upload.stage": {"op": "upload.stage", "write": True},
    "vool-browser.upload": {"op": "upload", "write": True},
    "vool-browser.permission.grant": {"op": "permission.grant", "write": False},
    "vool-browser.permission.list": {"op": "permission.list", "write": False},
    "vool-browser.cancel": {"op": "cancel", "write": True},
    # --- C07: shopping comparison and wallet-checkout handoff -----------------
    "vool-browser.offer.extract": {"op": "offer.extract", "write": False},
    "vool-browser.offer.compare": {"op": "offer.compare", "write": False},
    "vool-browser.profile.confirm": {"op": "profile.confirm", "write": True},
    "vool-browser.checkout.begin": {"op": "checkout.begin", "write": True},
    "vool-browser.checkout.handoff": {"op": "checkout.handoff", "write": True},
    "vool-browser.order.reconcile": {"op": "order.reconcile", "write": True},
}


class LaneResult:
    def __init__(self, *, ok: bool, status: str, response_text: str,
                 observation: dict[str, Any]) -> None:
        self.ok = ok
        self.status = status
        self.response_text = response_text
        self.observation = observation


def _result(*, ok: bool, status: str, response_text: str,
            observation: dict[str, Any] | None = None) -> LaneResult:
    return LaneResult(
        ok=ok, status=status,
        response_text=response_text,
        observation={"intent_observation": True, **(observation or {})})


def _require_session(arguments: dict[str, Any]) -> Any:
    from core.vool_browser.sessions import registry_status

    session = str(arguments.get("session") or "").strip()
    handle = get_handle(session)
    if handle is not None:
        if handle.state in {"closed", "closing"}:
            raise OpFailed("unknown_session", f"session {session!r} is closed")
        if handle.state == "cancelled":
            raise OpFailed("session_cancelled", f"session {session!r} was cancelled")
        return handle
    record = registry_status(session)
    if record is None:
        raise OpFailed(
            "unknown_session",
            f"no session named {session!r}; open one with vool-browser.session.open")
    if record.get("state") in {"stale", "recoverable", "cancelled"}:
        raise OpFailed(
            "session_unavailable",
            f"session {session!r} is {record.get('state')} after a restart and must be "
            "closed or reopened; receipts are kept at " + str(record.get("receipts_path") or ""))
    raise OpFailed("session_unavailable", f"session {session!r} is {record.get('state')}")


def handle_intent(intent: str, arguments: dict[str, Any]) -> LaneResult:
    spec = LANE_INTENTS.get(intent)
    if spec is None:
        return _result(ok=False, status="unsupported",
                       response_text=f"`{intent}` is not a vool-browser lane intent.")
    arguments = dict(arguments or {})
    op = spec["op"]

    # ---- lifecycle intents -------------------------------------------------
    if intent == "vool-browser.session.open":
        session = str(arguments.get("session") or "").strip()
        start_url = str(arguments.get("start_url") or "").strip()
        if start_url:
            reason = ops.check_url_navigable(start_url)
            if reason:
                return _result(ok=False, status="refused", response_text=f"start_url {reason}")
        try:
            handle = open_session(
                session=session, start_url=start_url,
                headless=bool(arguments.get("headless", True)),
                op_budget=int(arguments.get("op_budget") or 200),
                download_budget_bytes=int(
                    arguments.get("download_budget_bytes") or (100 * 1024 * 1024)))
        except OpFailed as exc:
            return _result(ok=False, status=exc.status, response_text=exc.message)
        except (TypeError, ValueError) as exc:
            return _result(ok=False, status="invalid_arguments", response_text=str(exc))
        # The start origin is the session's primary origin: everything else is
        # denied until the operator grants it. The engine DRIVES to the start
        # URL now, so a journey's first inspect sees the page the operator named;
        # a start page that refuses or hangs fails soft (typed note on the
        # receipt) — the session itself opened fine.
        start_note = ""
        if start_url:
            handle.touch(start_url)
            try:
                outcome = handle.submit(
                    lambda view: ops.op_navigate(handle, view, {"url": start_url}),
                    wait_seconds=30,
                )
                record_receipt(
                    handle, op="navigate", outcome="navigated",
                    origin=str(outcome.get("origin") or ""),
                    final_url=str(outcome.get("final_url") or start_url),
                    evidence={"start_url": start_url})
            except OpFailed as exc:
                # Fail-soft contract: a start page that refuses OR HANGS costs the
                # journey its start page, never the session. The two exception shapes
                # differ (OpFailed carries typed status/message; OpTimeout is bare),
                # so each is normalized here — reaching for .status on an OpTimeout
                # turned the fail-soft note itself into an AttributeError (CI shard 9,
                # run 35869013317: a 30s navigation timeout on a loaded runner).
                start_note = f"; start page not reached ({exc.status})"
                record_receipt(
                    handle, op="navigate", outcome=exc.status,
                    origin=handle.primary_origin, final_url=start_url,
                    reason=exc.message[:200])
            except OpTimeout as exc:
                start_note = "; start page not reached (timeout)"
                record_receipt(
                    handle, op="navigate", outcome="timeout",
                    origin=handle.primary_origin, final_url=start_url,
                    reason=str(exc)[:200])
        record = record_receipt(
            handle, op=op, outcome="opened", origin=handle.primary_origin,
            final_url=handle.current_url or start_url,
            evidence={"engine": handle.engine_binary, "headless": handle.headless,
                      "isolation": "disposable_scratch_profile+mock_keychain"})
        obs = {"receipt": record, "session": handle.describe()}
        return _result(ok=True, status="executed",
                       response_text=(f"session {session!r} open: disposable profile "
                                      f"{handle.profile_dir}, mock keychain, budget "
                                      f"{handle.op_budget} ops{start_note}"),
                       observation=obs)

    if intent == "vool-browser.session.close":
        session = str(arguments.get("session") or "").strip()
        handle = get_handle(session)
        receipts_path = str(handle.receipts_path) if handle else ""
        current_url = handle.current_url if handle else ""
        existed = close_session(session)
        if not existed:
            return _result(ok=False, status="unknown_session",
                           response_text=f"no session named {session!r}")
        if handle is not None:
            # The close is itself a receipted operation; the receipts file lives
            # OUTSIDE the deleted profile and survives the purge.
            record_receipt(handle, op="session.close", outcome="closed",
                           origin=handle.primary_origin, final_url=current_url,
                           evidence={"profile_deleted": True})
        return _result(ok=True, status="executed",
                       response_text=(f"session {session!r} closed; disposable profile deleted; "
                                      f"receipts kept at {receipts_path or 'the registry'}"),
                       observation={"session": session, "closed": True})

    if intent == "vool-browser.cancel":
        session = str(arguments.get("session") or "").strip()
        handle = get_handle(session)
        if handle is None:
            return _result(ok=False, status="unknown_session",
                           response_text=f"no live session named {session!r}")
        close_session(session, cancelled=True)
        record = {"op_id": f"cancel-{handle.session}", "op": "cancel", "session": session,
                  "ts": 0.0, "origin": "", "final_url": handle.current_url,
                  "outcome": "cancelled_by_operator_request"}
        return _result(ok=True, status="executed",
                       response_text=(f"session {session!r} cancelled: the engine is stopped "
                                      "and in-flight work returns typed cancelled/timeout"),
                       observation={"receipt": record, "session": handle.describe()})

    # ---- C07 pure operations (no engine thread, no session binding) ---------
    if intent == "vool-browser.offer.compare":
        try:
            outcome = shopping.compare_offers(None, None, arguments)
        except OpFailed as exc:
            return _result(ok=False, status=exc.status, response_text=exc.message)
        receipt = build_receipt(
            op=op, session="offer-compare", outcome="compared",
            evidence={"currency": outcome.get("currency"),
                      "offers": len(outcome.get("ranking") or [])})
        observation = dict(outcome)
        observation["receipt"] = receipt
        return _result(ok=True, status="executed",
                       response_text=(f"compared {len(outcome.get('ranking') or [])} offers "
                                      f"on exact totals ({outcome.get('currency')})"),
                       observation=observation)

    if intent == "vool-browser.profile.confirm":
        try:
            outcome = shopping.confirm_profile(arguments)
        except OpFailed as exc:
            return _result(ok=False, status=exc.status, response_text=exc.message)
        receipt = build_receipt(
            op=op, session="profile", outcome="profile_confirmed",
            final_url=str(outcome.get("profile_path") or ""),
            evidence={"profile_name": outcome.get("profile_name")})
        append_receipt(Path(str(outcome["profile_path"])).parent / "receipts.jsonl", receipt)
        observation = dict(outcome)
        observation["receipt"] = receipt
        return _result(ok=True, status="executed",
                       response_text=f"profile {outcome['profile_name']!r} confirmed for checkout",
                       observation=observation)

    # ---- session-bound operations -----------------------------------------
    if intent == "vool-browser.session.status":
        # Status never spends budget and answers even for a cancelled session —
        # but a CLOSED session is gone and must say so.
        session = str(arguments.get("session") or "").strip()
        handle = get_handle(session)
        if handle is not None and handle.state not in {"closed", "closing"}:
            return _result(ok=True, status="executed",
                           response_text=f"session {session!r}: {handle.state}",
                           observation={"session": handle.describe()})
        record = registry_status(session)
        if record is None:
            return _result(ok=False, status="unknown_session",
                           response_text=f"no session named {session!r}")
        return _result(ok=True, status="executed",
                       response_text=f"session {session!r}: {record.get('state')}",
                       observation={"session": record})

    try:
        handle = _require_session(arguments)
        handle.spend_op()
    except OpFailed as exc:
        return _result(ok=False, status=exc.status, response_text=exc.message)

    op_func = {
        "navigate": ops.op_navigate,
        "inspect": ops.op_inspect,
        "click": ops.op_click,
        "type": ops.op_type,
        "assert": ops.op_assert,
        "screenshot": ops.op_screenshot,
        "download": ops.op_download,
        "upload.stage": ops.op_upload_stage,
        "upload": ops.op_upload,
        "permission.grant": ops.op_permission_grant,
        "permission.list": ops.op_permission_list,
        "session.status": ops.op_session_status,
        # C07 (engine-bound ops)
        "offer.extract": shopping.extract_offer,
        "checkout.begin": shopping.begin_checkout,
        "checkout.handoff": shopping.checkout_handoff,
        "order.reconcile": shopping.reconcile_order,
    }.get(op)
    if op_func is None:
        return _result(ok=False, status="unsupported", response_text=f"op {op!r} unmapped")

    cancellable = intent == "vool-browser.navigate"
    try:
        outcome = handle.submit(
            lambda view: op_func(handle, view, arguments), cancellable=cancellable)
    except OpTimeout as exc:
        record = record_receipt(handle, op=op, outcome="timeout",
                                origin=handle.primary_origin, final_url=handle.current_url,
                                bounds={"timeout_seconds": arguments.get("timeout_seconds")})
        return _result(ok=False, status="timeout", response_text=str(exc),
                       observation={"receipt": record,
                                    "page": ops.Untrusted.wrap("", url=handle.current_url,
                                                               origin=handle.primary_origin)})
    except OpFailed as exc:
        if handle.cancelled or "ERR_ABORTED" in exc.message or "Target closed" in exc.message:
            status = "cancelled"
        else:
            status = exc.status
        record = record_receipt(handle, op=op, outcome=status,
                                origin=handle.primary_origin, final_url=handle.current_url,
                                **{"reason": exc.message[:300]})
        return _result(ok=False, status=status, response_text=exc.message,
                       observation={"receipt": record})

    evidence = {k: v for k, v in outcome.items()
                if k not in {"receipt_outcome", "origin", "final_url"}}
    receipt = record_receipt(
        handle, op=op,
        outcome=str(outcome.get("receipt_outcome") or "executed"),
        origin=str(outcome.get("origin") or handle.primary_origin),
        final_url=str(outcome.get("final_url") or handle.current_url),
        bounds=({k: outcome[k] for k in ("bytes", "max_bytes") if k in outcome} or None),
        **evidence)

    observation = {k: v for k, v in outcome.items() if k != "receipt_outcome"}
    observation["receipt"] = receipt
    return _result(ok=True, status="executed",
                   response_text=f"{op} ok on {receipt['final_url'] or receipt['origin']}",
                   observation=observation)


def verify_session_receipts(session: str) -> tuple[int, list[str]]:
    handle = get_handle(session)
    path = handle.receipts_path if handle else None
    if path is None:
        record = registry_status(session)
        path = record and record.get("receipts_path")
    if not path:
        return 0, ["no receipts"]
    return verify_receipts_file(path)


__all__ = ["LANE_INTENTS", "TOOL_SURFACE", "LaneResult", "handle_intent", "verify_session_receipts"]
