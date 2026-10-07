"""The binders read packet facts from the same retrieval that produced the evidence the reader saw.

Packet facts reach the binders through the process's last-retrieval telemetry, which any later retrieval overwrites:
a second assembly pass of the same turn, a recall for another question, or another chat's turn. Bound against the
admitted capsule of THIS turn, such facts would mix two memory revisions, or two chats. A packet is used only when it
was compiled for this chat and its rendered text is the packet inside the evidence this turn admitted.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def packet_facts_for(evidence_text: Any, chat_id: str) -> list[Mapping[str, Any]]:
    """This turn's packet facts, or [] when the last compiled packet is not the one in ``evidence_text``."""

    try:
        from core.context_retrieval import get_last_retrieval_telemetry

        telemetry = dict(get_last_retrieval_telemetry() or {})
    except Exception:
        return []
    facts = list(telemetry.get("evidence_packet_facts") or [])
    snapshot = telemetry.get("evidence_packet_snapshot")
    if not facts or not isinstance(snapshot, Mapping):
        return []
    packet_text = str(snapshot.get("packet_text") or "").strip()
    if str(snapshot.get("chat_id") or "") != str(chat_id or "") or not packet_text:
        return []
    if packet_text not in str(evidence_text or ""):
        return []
    return facts


__all__ = ["packet_facts_for"]
