"""Original-column measurements from the exact recorded provider invocation."""
from __future__ import annotations

import json
import sqlite3
from contextlib import suppress


def measurements(source) -> dict:
    from core import usage_meter
    from core.turn_model_call_ledger import provider_call_metrics

    call = provider_call_metrics(source.provider_call_id)
    timing = source.timing.public() if source.timing is not None else {"first_token_ms": None, "total_ms": None}
    ident = source.provider_call_id or "disclosure-" + source.preview_id
    state = call["state"] if call else "unrecorded"
    result = {**timing, "call_id": ident, "state": state, "prompt_tokens": None, "output_tokens": None,
              "billed_usd": None, "billed_amount": None, "billed_currency": None}
    if call:
        result.update(call)
        if source.usd_report_complete is False and call.get('billed_currency') in {None, 'USD'}:
            # The canonical record can label a partial BYOK platform fee exact.
            # Refuse that display claim when the observed USD report was incomplete;
            # accounting and its unknown liabilities remain with their existing owner.
            result['billed_usd'] = None
            result['billed_amount'] = result['billed_currency'] = None
    if not source.timing_recorded and timing["total_ms"] is not None and state != "attempted":
        try:
            # A second view of this immutable physical call is idempotent.
            with suppress(sqlite3.IntegrityError):
                usage_meter.record_call_timing(model_call_id=ident, usage_entry_id=None,
                    provider_id=source.provider, model_id=str(json.loads(source.body).get("model") or ""),
                    state=state, **timing)
            stored = usage_meter.measured_call(ident)
            if stored is not None:
                result.update(first_token_ms=stored["first_token_ms"], total_ms=stored["total_ms"])
                source.timing_recorded = True
            else:
                result["accounting_error"] = "Timing could not be saved to the local ledger."
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            result["accounting_error"] = "Timing could not be saved to the local ledger."
    return result
