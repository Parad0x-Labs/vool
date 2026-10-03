"""At-most-once audible delivery, shared by native and browser channels.

Claims are durable before playback. A crash or autoplay rejection may lose a cue;
retrying an uncertain effect could repeat it. The inbox always remains available.
"""
from __future__ import annotations

import json
from datetime import timedelta

from core.operator import notification_center as centre


def enabled_now(*, preferences=None):
    from core.operator.native_notifications import _user_zone
    preferences = centre.load_preferences() if preferences is None else preferences
    now = centre._parse(centre._utcnow())
    return bool(preferences["sound"] and not centre.in_quiet_hours(preferences, local_now=now.astimezone(_user_zone())))


def claim(conn, row, *, now_iso, channel, preferences):
    """Caller holds BEGIN IMMEDIATE; one unique delivery row owns both channels."""
    payload = json.loads(row["payload_json"] or "{}")
    if row["source_kind"] != "background_run" or payload.get("event_type") not in {"task_completed", "task_pending_approval"}:
        return False
    from core.operator.notification_hub import supersede_closed_action
    if supersede_closed_action(conn, row, now_iso=now_iso):
        return False
    from core.operator.native_notifications import _user_zone
    current = centre._load_preferences_conn(conn)
    preferences = {**current, 'sound': preferences['sound'] and current['sound']}
    now = centre._parse(now_iso)
    created = centre._parse(row["created_at"])
    allowed = bool(payload.get("audio_eligible") and preferences["sound"] and
                   not centre.in_quiet_hours(preferences, local_now=now.astimezone(_user_zone())) and
                   created and now - timedelta(seconds=30) <= created <= now and
                   not row["read_at"] and not row["dismissed_at"] and not row["superseded_at"])
    result = conn.execute(
        """INSERT OR IGNORE INTO notification_deliveries
        (delivery_id, notification_id, channel, state, detail, external_id, history_json, created_at, updated_at)
        VALUES (?, ?, 'audio', ?, ?, '', '[]', ?, ?)""",
        (row["notification_id"] + ':audio', row["notification_id"], 'submitted' if allowed else 'suppressed',
         channel + ': claim before playback; not proof of audibility' if allowed else 'muted, quiet, closed or expired', now_iso, now_iso))
    return allowed and result.rowcount == 1


def claim_browser(after):
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        return {"ok": False, "reason": "invalid_cursor"}
    from core.operator.notification_hub import sync_pending_actions
    sync_pending_actions()
    now_iso = centre._utcnow()
    conn = centre._default_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        preferences = centre._load_preferences_conn(conn)
        # Native opt-in owns sound even if OS denied/suppressed it. Never bypass Focus
        # or permission denial by falling back to web audio.
        rows = conn.execute("SELECT * FROM notification_items WHERE seq > ? AND source_kind = 'background_run' ORDER BY seq ASC LIMIT 200", (after,)).fetchall()
        cues = []
        for row in rows:
            if json.loads(row['payload_json']).get('audio_owner') == 'native':
                continue
            effective = {**preferences, 'sound': preferences['sound'] and not preferences['native_notifications']}
            if claim(conn, row, now_iso=now_iso, channel='browser', preferences=effective):
                cues.append(row['notification_id'])
        conn.commit()
        return {'ok': True, 'notification_ids': cues}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
