"""Front-door lane for "where did that file go?" -- answered from the receipt, with no tool at all.

The sibling of `action_history_honesty_fast_path`: both answer a question ABOUT the runtime's own
prior actions, and both must do it from recorded state rather than by acting again. The difference
is what they hold down. That one refuses to claim an action nobody took; this one refuses to go
LOOKING for an action it already has a receipt for.

It sits ABOVE that one in `handle_turn_frontdoor` -- immediately before the workspace-identity
lane -- which puts it ahead of every lane that could answer this question with something other than
the file's path:

  * `_maybe_handle_workspace_identity_request`, which names the bound FOLDER and not the file;
  * the intent arbiter, which spends a model call to pick between families;
  * `_maybe_handle_direct_machine_read_request`, which owns `machine.find_folder`;
  * the live-info lane and every cloud escalation below it.

None of those is wrong about its own family. Two were simply the only things reachable once the
front door had nothing for this one, and the measured results were a search of `/` for a folder
named after the user's typo, and "the root of your workspace" with no machine path in it.

Because it sits that high, the receipt-free half of the decision (`looks_like_location_ask`) runs
first and the receipt stores are read only for a turn that survives it.
"""
from __future__ import annotations

from typing import Any

from core.action_receipt_location import (
    latest_file_action_receipt,
    location_followup_kind,
    looks_like_location_ask,
    render_location_answer,
    render_missing_receipt_answer,
)

_SURFACES = {"channel", "openclaw", "api"}


def maybe_handle_action_receipt_location_request(
    agent: Any,
    user_input: str,
    *,
    session_id: str,
    source_surface: str,
    source_context: dict[str, object] | None,
) -> dict[str, Any] | None:
    """Answer from the last file-mutation receipt, or decline so the normal pipeline runs.

    The receipt is looked up BEFORE the message is fully classified, because the receipt is what
    decides which subjects the message is allowed to be about -- "where is exact_one_file_6a73.txt?"
    is this family only when that is the file a previous turn wrote.
    """
    if source_surface not in _SURFACES:
        context_surface = str((source_context or {}).get("surface") or "").strip()
        if context_surface not in _SURFACES:
            return None
    if not looks_like_location_ask(user_input):
        return None

    context = dict(source_context or {})
    workspace_root = str(context.get("workspace") or context.get("workspace_root") or "").strip()

    try:
        receipt = latest_file_action_receipt(session_id, workspace_root=workspace_root)
    except Exception:
        receipt = None

    kind = location_followup_kind(user_input, receipt=receipt)
    if kind is None:
        return None

    if receipt is None:
        response = render_missing_receipt_answer()
        details: dict[str, Any] = {"receipt_found": False}
    else:
        response = render_location_answer(receipt, kind=kind)
        details = {
            "receipt_found": True,
            "receipt_source": receipt.source,
            "receipt_intent": receipt.intent,
            "file_name": receipt.file_name,
            "relative_path": receipt.relative_path,
            "workspace_root": receipt.workspace_root,
            "absolute_path": receipt.absolute_path,
        }

    return agent._fast_path_result(
        session_id=session_id,
        user_input=user_input,
        response=response,
        confidence=0.98,
        source_context=source_context,
        reason="action_receipt_location",
        classification_details=details,
    )


__all__ = ["maybe_handle_action_receipt_location_request"]
