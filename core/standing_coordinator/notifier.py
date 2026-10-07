"""Where the coordinator's words reach the user.

The default notifier files each report and question in the notification centre as a
``background_run`` item tied to the chat the team was started from, so it shows in that
chat and under the bell. Questions go to the "needs" section, reports to "updates".
These items are in-app only: the native bridge pushes only its own event types.
"""

from __future__ import annotations

from typing import Any, Protocol


class Notifier(Protocol):
    def notify(self, *, key: str, team_id: str, session_id: str, kind: str, title: str, body: str,
               section: str) -> dict[str, Any]:
        """Record one item, once per key. Returns ``{"ok": bool, ...}``."""


class NotificationCentreNotifier:
    def notify(self, *, key: str, team_id: str, session_id: str, kind: str, title: str, body: str,
               section: str) -> dict[str, Any]:
        from core.operator import notification_center as centre

        item = centre.record_item(
            dedupe_key=f"coordinator:{key}",
            source_kind="background_run",
            title=title,
            body=body,
            session_id=session_id,
            payload={
                "section": "needs" if section == "needs" else "updates",
                "event_type": f"coordinator_{kind}",
                "team_id": team_id,
                "status": kind,
            },
        )
        return {"ok": True, "notification_id": item.get("notification_id"), "created": item.get("created")}


class MemoryNotifier:
    """Keeps items in a list. For tests and for a coordinator with no UI attached."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def notify(self, *, key: str, team_id: str, session_id: str, kind: str, title: str, body: str,
               section: str) -> dict[str, Any]:
        self.items.append({"key": key, "team_id": team_id, "session_id": session_id, "kind": kind,
                           "title": title, "body": body, "section": section})
        return {"ok": True}


__all__ = ["MemoryNotifier", "NotificationCentreNotifier", "Notifier"]
