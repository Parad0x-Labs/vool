"""Receipts group — the first production reader of turn finalization truth.

Binds to the existing ``a7_finalizations`` store (storage.db) and the honesty
receipt chain (core.honesty_receipt). Nothing here writes; these commands close
the audit defect "zero production readers of TurnResult.effect_receipts" for
the finalization ledger.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from core.command_registry.spec import (
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
)


@dataclass(frozen=True)
class ListInput:
    limit: int = 20


@dataclass(frozen=True)
class ShowInput:
    finalization_id: str


@dataclass(frozen=True)
class VerifyInput:
    session_id: str = ""


def _query(sql: str, params: tuple = ()) -> list[dict]:
    from storage.db import get_connection
    from storage.migrations import run_migrations

    def _rows(conn):
        cur = conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    # get_connection(None) returns a thread-local REUSABLE pooled connection — never close it.
    conn = get_connection()
    try:
        return _rows(conn)
    except Exception:
        # a fresh store may not have migrated yet; migrate once and retry
        run_migrations()
        return _rows(conn)


def _handle_receipts_list(inp, ctx):
    limit = max(1, min(int(inp.limit or 20), 200))
    rows = _query(
        "SELECT finalization_id, turn_id, request_id, semantic_result_id, status, "
        "delivery_status, availability, created_at FROM a7_finalizations "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    return HandlerOk(
        data={"count": len(rows), "finalizations": rows},
        summary=f"{len(rows)} finalization receipts (newest first)",
        receipts=tuple({"kind": "finalization", "id": r["finalization_id"]} for r in rows[:5]),
    )


def _handle_receipts_show(inp, ctx):
    rows = _query(
        "SELECT * FROM a7_finalizations WHERE finalization_id = ? LIMIT 1",
        (inp.finalization_id,),
    )
    if not rows:
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"No finalization with id {inp.finalization_id!r}",
            detail={"finalization_id": inp.finalization_id},
        )
    row = dict(rows[0])
    # canonical_content can be large; the operator asked for the receipt, not the payload
    row.pop("canonical_content", None)
    return HandlerOk(data=row, summary=f"Finalization {inp.finalization_id}")


def _handle_receipts_verify(inp, ctx):
    from core.honesty_receipt import list_honesty_receipts, verify_honesty_chain

    session_id = (inp.session_id or "").strip()
    if session_id:
        receipts = list_honesty_receipts(session_id)
        source = f"session:{session_id}"
    else:
        from core.honesty_receipt import _latest_ledger_path

        latest = _latest_ledger_path()
        if latest is None:
            return HandlerOk(
                data={"chain_ok": True, "receipt_count": 0, "source": "none"},
                summary="No honesty ledger exists yet — nothing to verify",
            )
        receipts = json.loads(latest.read_text() or "[]")
        source = str(latest)
    ok, reason = verify_honesty_chain(receipts)
    if not ok:
        return HandlerFault(
            fault_code="fault_validation",
            summary=f"Honesty chain verification failed: {reason}",
            detail={"reason": reason, "source": source},
        )
    from core.honesty_receipt import CHAIN_PROVEN_CLAIM, CHAIN_UNPROVEN_CLAIM

    return HandlerOk(
        data={
            "chain_ok": True,
            "receipt_count": len(receipts),
            "source": source,
            # The envelope carries the boundary, not just the verdict: a caller that
            # renders `chain_ok` alone would otherwise report completeness the
            # verifier never established.
            "proven": CHAIN_PROVEN_CLAIM,
            "not_proven": CHAIN_UNPROVEN_CLAIM,
            "completeness_proven": False,
        },
        summary=(
            f"{len(receipts)} receipts present are consistent — each valid and correctly "
            "linked; completeness is not proven"
        ),
    )


def register(reg) -> None:
    reg.add_group(
        GroupSpec(
            group_id="receipts",
            description="turn finalization receipts and the honesty chain — the truth of what was done",
        )
    )
    reg.add(
        CommandSpec(
            command_id="receipts.list",
            group="receipts",
            description="List recent turn finalization receipts",
            aliases=("receipts",),
            input_schema=ListInput,
            effects="read_only",
            capabilities=frozenset({"finalization_ledger.read"}),
            handler=Handler("core.command_registry.groups.receipts:_handle_receipts_list"),
            fault_bindings=(FaultBinding(when="store_missing", fault_code="fault_validation", remediation=("vool faults list",)),),
            exit_codes=(0, 2, 42),
            next_actions=(
                NextAction(command_id="receipts.verify", label="Verify the honesty chain"),
                NextAction(command_id="receipts.show", label="Inspect one receipt"),
            ),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="receipts.show",
            group="receipts",
            description="Show one finalization receipt by id",
            input_schema=ShowInput,
            effects="read_only",
            capabilities=frozenset({"finalization_ledger.read"}),
            handler=Handler("core.command_registry.groups.receipts:_handle_receipts_show"),
            exit_codes=(0, 2, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="receipts.verify",
            group="receipts",
            description="Verify the honesty receipt chain for a session (latest if omitted)",
            input_schema=VerifyInput,
            effects="read_only",
            capabilities=frozenset({"honesty_chain.verify"}),
            handler=Handler("core.command_registry.groups.receipts:_handle_receipts_verify"),
            fault_bindings=(FaultBinding(when="chain_corrupt", fault_code="fault_validation", remediation=("vool faults list",)),),
            exit_codes=(0, 2, 42),
        )
    )
