"""Put details back in displayed answers, never in executable tool arguments."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from core.disclosure.replacer import PreparedText


def _usd_report_complete(usage: Any, metadata: Any) -> bool | None:
    # The canonical prepaid receipt has its own currency/evidence contract.
    # This observer only qualifies USD reports; it never prices or settles a call.
    if not isinstance(usage, dict) or not isinstance(metadata, (dict, type(None))):
        return False
    receipt = (metadata or {}).get('receipt') or {}
    if not isinstance(receipt, dict):
        return False
    if receipt.get('schema') == 'vool.usepod.receipt.v1':
        return None
    from core.model_compare.measurements import reported_bill
    return reported_bill(usage) is not None


def restore_response(request: Any, response: Any) -> Any:
    prepared = request.metadata.get("_disclosure_restore")
    if (isinstance(prepared, PreparedText) and not getattr(response, "tool_calls", ())
            and getattr(request, "output_mode", "plain_text") == "plain_text"):
        response = replace(response, output_text=prepared.restore(response.output_text))
    from core.disclosure.wire import note_response
    timing = request.metadata.get("_disclosure_timing")
    if timing is not None:
        timing.finish()  # Buffered responses do not claim a first-token measurement.
    note_response(request, response.output_text, usable=bool(response.output_text) and not getattr(response, "tool_calls", ())
                  and getattr(request, "output_mode", "plain_text") == "plain_text",
                  usd_report_complete=_usd_report_complete(getattr(response, 'usage', None), getattr(response, 'provider_metadata', None)))
    return response


def restore_stream(request: Any, chunks: Any) -> Any:
    tail = ""
    collected = []
    completed = False
    tool_requested = False
    usage = None
    metadata = None
    for chunk in chunks:
        from core.model_compare.tool_observation import requested_tool
        tool_requested |= requested_tool(chunk.raw_event) or (chunk.provider_metadata or {}).get("vool_tool_request_observed") is True
        timing = request.metadata.get("_disclosure_timing")
        if chunk.delta_text:
            collected.append(chunk.delta_text)
            if timing is not None:
                timing.token()
        completed |= chunk.done
        if chunk.usage is not None:
            usage = chunk.usage
        if chunk.provider_metadata:
            metadata = chunk.provider_metadata
        prepared = request.metadata.get("_disclosure_restore")
        if not isinstance(prepared, PreparedText) or getattr(request, "output_mode", "plain_text") != "plain_text":
            yield chunk
            continue
        text = tail + chunk.delta_text
        tail = ""
        if not chunk.done:
            start = text.rfind("[")
            if start >= 0 and any(r.placeholder.startswith(text[start:]) for r in prepared.replacements):
                tail = text[start:]
                text = text[:start]
        yield replace(chunk, delta_text=prepared.restore(text))
    if tail:
        # A truncated unknown token stays literal; never invent the rest.
        from adapters.base_adapter import ModelStreamChunk

        yield ModelStreamChunk(delta_text=tail, done=True)

    timing = request.metadata.get("_disclosure_timing")
    if timing is not None:
        timing.finish()
    from core.disclosure.wire import note_response
    prepared = request.metadata.get("_disclosure_restore")
    output = "".join(collected)
    if isinstance(prepared, PreparedText):
        output = prepared.restore(output)
    note_response(request, output, usable=completed and not tool_requested and bool(output) and getattr(request, "output_mode", "plain_text") == "plain_text",
                  usd_report_complete=_usd_report_complete(usage, metadata))
