"""Project authoritative model news and runtime events into the existing durable inbox.

Discovery remains a catalogue; inbox actions never change models, recommendation
preferences, approvals or schedules. Only exact matching economic changes coalesce.
"""
from __future__ import annotations

import hashlib
import json
import threading

from core.operator import notification_center as centre

_LOCK = threading.RLock()
_BASELINE = "model_news_inbox_v1"
# Canonical runtime reasons for greetings, transport polling and UI controls.
# These are terminal transport receipts, not completed requested work.
_NON_WORK_COMPLETIONS = frozenset({
    "smalltalk_fast_path", "empty_turn_fast_path", "help_fast_path",
    "startup_sequence_fast_path", "heartbeat_poll_fast_path", "ui_command_fast_path",
    "user_preference_command", "bare_secret_intercept", "cloud_key_command",
    "image_key_command", "cloud_model_command", "cloud_models_command",
    "cloud_escalation_command", "spend_brakes_command", "hive_cleanup_noop",
    "image_generate_cancelled", "hive_topic_create_cancelled",
})


def _key(provider, model, kind, before, after, offer="permanent", expiry=""):
    def prices(row):
        return [round(float(row[k]), 4) if row.get(k) is not None else None
                for k in ("input_usd_per_m", "output_usd_per_m")]
    values = [str(provider or "openrouter").lower(), str(model).lower(), kind,
              [] if kind == "free" else prices(before), prices(after), offer, expiry]
    # A cache-only drop cannot be conflated with a different input/output offer.
    if kind == "drop" and prices(before) == prices(after):
        values += [before.get("cache_usd_per_m"), after.get("cache_usd_per_m")]
    return "model-news:" + hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def finding_key(f):
    return _key(f.provider_id, f.model_id, "free" if f.kind == "new_free" else "drop",
                f.before, f.after, f.offer_kind, f.expires_at)


def market_key(ev):
    kind = ev.get("type")
    after = ev.get("prices") if kind == "new_free_model" else ev.get("after") or {}
    before = ev.get("before") or {}
    def normalized(row):
        return {"input_usd_per_m": row.get("input_usd_per_m", row.get("prompt_usd_per_m")),
                "output_usd_per_m": row.get("output_usd_per_m", row.get("completion_usd_per_m"))}
    return _key(ev.get("provider_id"), ev.get("model"),
                "free" if kind in {"new_free_model", "paid_to_free"} else "drop" if kind == "price_decreased" else kind,
                normalized(before), normalized(after or {}))


def _by_key(key):
    conn = centre._default_connection()
    try:
        row = conn.execute("SELECT notification_id FROM notification_items WHERE dedupe_key = ?", (key,)).fetchone()
        return centre.load_item(row[0]) if row else None
    finally:
        conn.close()


def sync_model_news():
    from core.model_market_feed import read_events
    from storage import model_radar as radar

    sync_pending_actions()
    with _LOCK:
        conn = centre._default_connection()
        try:
            initialized = conn.execute("SELECT 1 FROM notification_preferences WHERE pref_key = ?", (_BASELINE,)).fetchone()
        finally:
            conn.close()
        # Findings first: a matched catalogue event retains the richer evidence card.
        unread = {f.fingerprint for f in radar.unread_findings(limit=200)}
        hidden_keys = set()
        for f in radar.list_findings(limit=200, include_dismissed=True):
            key = finding_key(f)
            if radar.is_dismissed(f.fingerprint):
                hidden_keys.add(key)
                existing = _by_key(key)
                if existing and not existing["dismissed_at"]:
                    centre.apply_action(existing["notification_id"], action="dismiss")
                continue
            item = centre.record_item(dedupe_key=key, source_kind="model_offer",
                                     title=("Observed free: " if f.kind == "new_free" else "Price cut: ") + (f.display_name or f.model_id),
                                     body=f.why, payload={"section": "offers", "finding": f.to_dict()})
            # An earlier market poll may have recorded this same change. Enrich its
            # immutable source snapshot without resetting read or dismissal state.
            if item["source_kind"] == "model_market":
                conn = centre._default_connection()
                try:
                    payload = {**item["payload"], "section": "offers", "finding": f.to_dict()}
                    conn.execute("UPDATE notification_items SET source_kind = 'model_offer', payload_json = ? WHERE notification_id = ?",
                                 (json.dumps(payload, sort_keys=True), item["notification_id"]))
                    conn.commit()
                finally:
                    conn.close()
            if item["created"] and f.fingerprint not in unread:
                centre.apply_action(item["notification_id"], action="read")
        events = read_events(after=0)
        # Prefer the fully identified free event over its companion paid-to-free row.
        events.sort(key=lambda ev: ev.get("type") != "new_free_model")
        for ev in events:
            key = market_key(ev)
            if key in hidden_keys:
                continue
            warning = ev.get("type") in {"price_increased", "free_to_paid", "model_delisted"}
            offer = False  # only qualified findings populate Model offers; other catalogue news is Updates
            item = centre.record_item(dedupe_key=key, source_kind="model_market",
                                     title=str(ev.get("display_name") or ev.get("model") or "Model change"),
                                     body=str(ev.get("type") or "catalogue change").replace("_", " "),
                                     payload={"section": "needs" if warning else "offers" if offer else "updates", "market_event": ev})
            if not initialized and item["created"]:
                conn = centre._default_connection()
                try:
                    conn.execute("UPDATE notification_items SET read_at = ?, last_action = 'baseline' WHERE notification_id = ? AND read_at IS NULL",
                                 (centre._utcnow(), item["notification_id"]))
                    conn.commit()
                finally:
                    conn.close()
        conn = centre._default_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO notification_preferences (pref_key, value_json, updated_at) VALUES (?, 'true', ?)",
                         (_BASELINE, centre._utcnow()))
            conn.commit()
        finally:
            conn.close()


def viewed_offers(fingerprints):
    from storage import model_radar as radar
    sync_model_news()
    wanted = set(fingerprints)
    count = 0
    for f in radar.list_findings(limit=200):
        if f.fingerprint in wanted:
            item = _by_key(finding_key(f))
            if item and not item["read_at"]:
                centre.apply_action(item["notification_id"], action="read")
                count += 1
    return count


def migrate_market_state(read_seq, dismissed):
    """Import only existing, server-authored events from the old browser inbox."""
    from core.model_market_feed import read_events
    if isinstance(read_seq, bool) or not isinstance(read_seq, int) or read_seq < 0:
        return {"ok": False, "reason": "invalid_read_sequence"}
    if not isinstance(dismissed, list) or len(dismissed) > 200 or any(type(x) is not int or x < 1 for x in dismissed):
        return {"ok": False, "reason": "invalid_dismissals"}
    with _LOCK:
        sync_model_news()
        states = []
        for ev in read_events(after=0):
            seq = int(ev.get("seq") or 0)
            item = _by_key(market_key(ev))
            if item:
                action = "dismiss" if seq in dismissed else "read" if seq <= read_seq else "unread"
                states.append((item["notification_id"], action))
        return centre.import_model_read_state(states)


def record_runtime_event(session_id, event):
    kind = event.get("event_type")
    if kind not in {"task_completed", "task_failed", "task_cancelled", "task_pending_approval"}:
        return
    if kind == "task_completed" and event.get("status") in _NON_WORK_COMPLETIONS:
        return
    turn = event.get("client_turn_id") or event.get("turn_key")
    if not turn:
        return  # no stable identity: never guess which turn ended
    status = {"task_completed": "completed", "task_failed": "failed", "task_cancelled": "stopped", "task_pending_approval": "needs approval"}[kind]
    identity = ""
    if kind == "task_pending_approval":
        identity = hashlib.sha256(json.dumps(event.get("approval_request") or {}, sort_keys=True).encode()).hexdigest()
    # Terminal truth closes earlier action cards for this exact turn only.
    if kind in {"task_completed", "task_failed", "task_cancelled"}:
        conn = centre._default_connection()
        try:
            rows = conn.execute("SELECT notification_id, payload_json FROM notification_items WHERE session_id = ? AND source_kind = 'background_run' AND superseded_at IS NULL", (session_id,)).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                if payload.get("turn_id") == turn and payload.get("event_type") == "task_pending_approval":
                    conn.execute("UPDATE notification_items SET superseded_at = ? WHERE notification_id = ?", (centre._utcnow(), row["notification_id"]))
            conn.commit()
        finally:
            conn.close()
    from core.operator.notification_audio import enabled_now
    approval_id = str((event.get('approval_request') or {}).get('approval_id') or '')
    from core.mode_permission_policy import approval_is_pending
    approval_pending = bool(approval_id) and approval_is_pending(approval_id)
    preferences = centre.load_preferences()
    eligible = (kind in {"task_completed", "task_pending_approval"}
                and (kind != "task_pending_approval" or approval_pending)
                and enabled_now(preferences=preferences))
    centre.record_item(dedupe_key=f"runtime:{session_id}:{turn}:{kind}:{identity}", source_kind="background_run",
                       title="Chat " + status, body=str(event.get("message") or ""), session_id=session_id,
                       payload={"section": "needs" if kind in {"task_failed", "task_pending_approval"} else "updates",
                                "turn_id": turn, "status": status, "event_type": kind, "audio_eligible": eligible,
                                "audio_owner": "native" if preferences["native_notifications"] else "browser",
                                "approval_id": approval_id})


def supersede_closed_action(conn, row, *, now_iso):
    """Recheck canonical consent at delivery, preserving the original action identity."""
    payload = json.loads(row['payload_json'] or '{}')
    if payload.get('event_type') != 'task_pending_approval':
        return False
    from core.mode_permission_policy import approval_is_pending
    token = payload.get('approval_id')
    if token and approval_is_pending(token):
        return False
    conn.execute('UPDATE notification_items SET superseded_at = ? WHERE notification_id = ?',
                 (now_iso, row['notification_id']))
    return True


def sync_pending_actions():
    """Resolved, denied and expired approvals are no longer Needs-you items."""
    conn = centre._default_connection()
    try:
        rows = conn.execute("SELECT notification_id, payload_json FROM notification_items WHERE source_kind = 'background_run' AND superseded_at IS NULL").fetchall()
        for row in rows:
            supersede_closed_action(conn, row, now_iso=centre._utcnow())
        conn.commit()
    finally:
        conn.close()
