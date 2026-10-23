"""Canonical PC inbox notices, addressed only to the other paired phones."""

from __future__ import annotations

import secrets


def record(service, actor_id, kind):
    from core.operator import notification_center as center

    audience = [
        row["device_id"]
        for row in service.store.devices().values()
        if row.get("device_id") != actor_id
        and row.get("status") == "active"
        and row.get("kind", "phone") == "phone"
        and row.get("sealed_protocol")
    ]
    center.record_item(
        dedupe_key="phone-security:" + secrets.token_hex(16),
        source_kind="background_run",
        title="A phone was paired." if kind == "device_paired" else "A phone was unpaired.",
        payload={"event_type": "desktop_inbox", "security_notice": kind, "audience_device_ids": audience},
    )
