"""Every wallet refusal is filed once, typed, in the runtime's fault plane, then raised."""
from __future__ import annotations

import uuid
from typing import Any

from core.faults.recorder import identity_from_context, record_fault
from core.faults.records import FaultRecord
from core.wallet.errors import WalletFault
from core.wallet.redaction import redact_wallet_record


def wallet_fault(code: str, *, authority: str, context: dict[str, Any] | None = None, source_context: dict[str, Any] | None = None, dedupe: str = "") -> WalletFault:
    """Record a typed fault (security observation included when the catalog says so) and return the exception to raise."""
    clean_context = redact_wallet_record(dict(context or {}))
    turn_key, session_id = identity_from_context(source_context)
    fault_id = ""
    message = ""
    try:
        record = FaultRecord.for_code(
            code,
            authority=authority,
            turn_key=turn_key,
            session_id=session_id,
            dedupe=dedupe or f"wallet-{uuid.uuid4().hex}",
            context={k: str(v) for k, v in clean_context.items()},
        )
        message = record.user_message
        fault_id = record_fault(record)
    except Exception:
        fault_id = ""
    return WalletFault(code, context=clean_context, fault_id=fault_id, message=message)
