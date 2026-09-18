from __future__ import annotations

import argparse
import contextlib
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from storage.db import STORE_APPLICATION_ID, StoreVersionError, get_connection

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS persona_profiles (
    persona_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    spirit_anchor TEXT NOT NULL,
    tone TEXT NOT NULL,
    verbosity TEXT NOT NULL,
    risk_tolerance REAL NOT NULL CHECK (risk_tolerance >= 0 AND risk_tolerance <= 1),
    explanation_depth REAL NOT NULL CHECK (explanation_depth >= 0 AND explanation_depth <= 1),
    execution_style TEXT NOT NULL,
    strictness REAL NOT NULL CHECK (strictness >= 0 AND strictness <= 1),
    personality_locked INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS local_tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL DEFAULT '',
    task_class TEXT NOT NULL,
    task_summary TEXT NOT NULL,
    redacted_input_hash TEXT NOT NULL,
    environment_os TEXT,
    environment_shell TEXT,
    environment_runtime TEXT,
    environment_version_hint TEXT,
    plan_mode TEXT NOT NULL,
    share_scope TEXT NOT NULL DEFAULT 'local_only',
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    outcome TEXT NOT NULL,
    harmful_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_shards (
    shard_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    problem_class TEXT NOT NULL,
    problem_signature TEXT NOT NULL,
    summary TEXT NOT NULL,
    resolution_pattern_json TEXT NOT NULL,
    environment_tags_json TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_node_id TEXT,
    quality_score REAL NOT NULL CHECK (quality_score >= 0 AND quality_score <= 1),
    trust_score REAL NOT NULL CHECK (trust_score >= 0 AND trust_score <= 1),
    local_validation_count INTEGER NOT NULL DEFAULT 0,
    local_failure_count INTEGER NOT NULL DEFAULT 0,
    quarantine_status TEXT NOT NULL DEFAULT 'active',
    risk_flags_json TEXT NOT NULL,
    freshness_ts TEXT NOT NULL,
    expires_ts TEXT,
    signature TEXT,
    origin_task_id TEXT NOT NULL DEFAULT '',
    origin_session_id TEXT NOT NULL DEFAULT '',
    share_scope TEXT NOT NULL DEFAULT 'local_only',
    restricted_terms_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_learning_shards_problem_class
ON learning_shards(problem_class);

CREATE INDEX IF NOT EXISTS idx_learning_shards_problem_signature
ON learning_shards(problem_signature);

CREATE INDEX IF NOT EXISTS idx_learning_shards_trust
ON learning_shards(trust_score);

CREATE TABLE IF NOT EXISTS sniffed_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_peer_id TEXT NOT NULL,
    prompt_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    learning_value REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS dialogue_sessions (
    session_id TEXT PRIMARY KEY,
    last_subject TEXT,
    topic_hints_json TEXT NOT NULL DEFAULT '[]',
    last_intent_mode TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_memory_policies (
    session_id TEXT PRIMARY KEY,
    share_scope TEXT NOT NULL DEFAULT 'local_only',
    restricted_terms_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_hive_watch_state (
    session_id TEXT PRIMARY KEY,
    watched_topic_ids_json TEXT NOT NULL DEFAULT '[]',
    seen_post_ids_json TEXT NOT NULL DEFAULT '[]',
    pending_topic_ids_json TEXT NOT NULL DEFAULT '[]',
    seen_curiosity_topic_ids_json TEXT NOT NULL DEFAULT '[]',
    seen_curiosity_run_ids_json TEXT NOT NULL DEFAULT '[]',
    seen_agent_ids_json TEXT NOT NULL DEFAULT '[]',
    last_active_agents INTEGER NOT NULL DEFAULT 0,
    snooze_until TEXT NOT NULL DEFAULT '',
    last_prompted_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dialogue_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    raw_input TEXT NOT NULL,
    normalized_input TEXT NOT NULL,
    reconstructed_input TEXT NOT NULL,
    topic_hints_json TEXT NOT NULL DEFAULT '[]',
    reference_targets_json TEXT NOT NULL DEFAULT '[]',
    understanding_confidence REAL NOT NULL DEFAULT 0.0,
    quality_flags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dialogue_turns_session_created
ON dialogue_turns(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS adaptive_lexicon (
    term TEXT NOT NULL,
    canonical TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    source TEXT NOT NULL DEFAULT 'manual',
    confidence REAL NOT NULL DEFAULT 0.75,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (term, scope)
);

CREATE TABLE IF NOT EXISTS peers (
    peer_id TEXT PRIMARY KEY,
    display_alias TEXT,
    trust_score REAL NOT NULL CHECK (trust_score >= 0 AND trust_score <= 1),
    successful_shards INTEGER NOT NULL DEFAULT 0,
    failed_shards INTEGER NOT NULL DEFAULT 0,
    strike_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shard_feedback (
    feedback_id TEXT PRIMARY KEY,
    shard_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    peer_id TEXT,
    outcome TEXT NOT NULL,
    confidence_before REAL NOT NULL CHECK (confidence_before >= 0 AND confidence_before <= 1),
    confidence_after REAL NOT NULL CHECK (confidence_after >= 0 AND confidence_after <= 1),
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (shard_id) REFERENCES learning_shards(shard_id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES local_tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY (peer_id) REFERENCES peers(peer_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS web_notes (
    note_id TEXT PRIMARY KEY,
    query_hash TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_url_hash TEXT,
    summary TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    freshness_ts TEXT NOT NULL,
    used_in_task_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (used_in_task_id) REFERENCES local_tasks(task_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS curiosity_topics (
    topic_id TEXT PRIMARY KEY,
    session_id TEXT,
    originating_task_id TEXT,
    trace_id TEXT,
    topic TEXT NOT NULL,
    topic_kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority REAL NOT NULL DEFAULT 0.0,
    source_profiles_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT,
    candidate_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_curiosity_topics_status
ON curiosity_topics(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_curiosity_topics_session
ON curiosity_topics(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS operator_action_requests (
    action_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action_kind TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    executed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_operator_action_requests_session
ON operator_action_requests(session_id, action_kind, status, created_at DESC);

-- Durable local reminders and calendar alerts (the scheduling vertical's ONE schedule). Status machine:
-- scheduled -> dispatching -> delivered; scheduled -> cancelled; a dispatch interrupted
-- by a crash becomes delivery_uncertain and is reported, never re-fired silently; a calendar alert
-- that comes due after its event started becomes expired; a delivered row can be snoozed back to
-- scheduled one fire generation later. source_kind 'reminder' rows belong to a chat session;
-- 'calendar_alert' rows belong to an event projection (source_ref) and are keyed by schedule_key.
CREATE TABLE IF NOT EXISTS reminder_requests (
    reminder_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    note TEXT NOT NULL,
    due_at_utc TEXT NOT NULL,
    tz_name TEXT NOT NULL DEFAULT '',
    due_wall TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'scheduled',
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    delivered_at TEXT,
    last_error TEXT,
    delivery_receipt_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'reminder',
    source_ref TEXT NOT NULL DEFAULT '',
    schedule_key TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    snooze_count INTEGER NOT NULL DEFAULT 0,
    fire_generation INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_reminder_requests_due
ON reminder_requests(status, due_at_utc ASC);

CREATE INDEX IF NOT EXISTS idx_reminder_requests_session
ON reminder_requests(session_id, status, due_at_utc ASC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_requests_schedule_key
ON reminder_requests(schedule_key) WHERE schedule_key != '';

CREATE INDEX IF NOT EXISTS idx_reminder_requests_source
ON reminder_requests(source_kind, source_ref, status);

-- Calendar accounts (one connection to one calendar source; the credential is named by binding id,
-- never stored), the calendars chosen on each, and projections of the events the alert sync last read.
CREATE TABLE IF NOT EXISTS calendar_accounts (
    account_id TEXT PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    base_url TEXT NOT NULL DEFAULT '',
    auth_binding TEXT NOT NULL DEFAULT '',
    principal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'configured',
    status_detail TEXT NOT NULL DEFAULT '',
    status_at TEXT NOT NULL DEFAULT '',
    sync_enabled INTEGER NOT NULL DEFAULT 0,
    alerts_enabled INTEGER NOT NULL DEFAULT 0,
    default_lead_minutes_json TEXT NOT NULL DEFAULT '[15]',
    last_sync_started_at TEXT NOT NULL DEFAULT '',
    last_sync_ok_at TEXT NOT NULL DEFAULT '',
    next_sync_due_at TEXT NOT NULL DEFAULT '',
    sync_failures INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    disconnected_at TEXT
);

CREATE TABLE IF NOT EXISTS calendar_selections (
    account_id TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    can_write INTEGER,
    provider_default INTEGER NOT NULL DEFAULT 0,
    selected INTEGER NOT NULL DEFAULT 0,
    is_default_write INTEGER NOT NULL DEFAULT 0,
    present INTEGER NOT NULL DEFAULT 1,
    alert_lead_minutes_json TEXT,
    discovered_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, calendar_id)
);

CREATE TABLE IF NOT EXISTS calendar_event_projections (
    event_key TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    calendar_id TEXT NOT NULL,
    uid TEXT NOT NULL,
    occurrence_key TEXT NOT NULL DEFAULT '',
    series_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    start_utc TEXT NOT NULL DEFAULT '',
    end_utc TEXT NOT NULL DEFAULT '',
    all_day INTEGER NOT NULL DEFAULT 0,
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    tz_name TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    meeting_url TEXT NOT NULL DEFAULT '',
    web_url TEXT NOT NULL DEFAULT '',
    description_excerpt TEXT NOT NULL DEFAULT '',
    etag TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active',
    alert_lead_minutes_json TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calendar_projections_window
ON calendar_event_projections(account_id, calendar_id, state, start_utc);

-- The persistent notification centre the bell and the native notification bridge read, the delivery
-- evidence each channel recorded, and the user's notification preferences.
CREATE TABLE IF NOT EXISTS notification_items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_id TEXT NOT NULL UNIQUE,
    dedupe_key TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL,
    schedule_id TEXT NOT NULL DEFAULT '',
    event_key TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    read_at TEXT,
    dismissed_at TEXT,
    snoozed_until TEXT,
    superseded_at TEXT,
    last_action TEXT NOT NULL DEFAULT '',
    last_action_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_notification_items_schedule
ON notification_items(schedule_id);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    history_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_item
ON notification_deliveries(notification_id);

CREATE TABLE IF NOT EXISTS notification_preferences (
    pref_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- The macOS notification requests VOOL handed to the notification bridge, one row per request identifier VOOL chose,
-- with the states the bridge reported (core/operator/native_notifications.py).
CREATE TABLE IF NOT EXISTS native_notification_requests (
    identifier TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    notification_id TEXT NOT NULL DEFAULT '',
    schedule_id TEXT NOT NULL DEFAULT '',
    fire_generation INTEGER NOT NULL DEFAULT 0,
    deliver_at_utc TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    request_json TEXT NOT NULL DEFAULT '',
    hand_count INTEGER NOT NULL DEFAULT 0,
    handed_at TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT '',
    listed_at TEXT NOT NULL DEFAULT '',
    acknowledged_at TEXT NOT NULL DEFAULT '',
    last_action TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_native_requests_schedule ON native_notification_requests(schedule_id, fire_generation);
CREATE INDEX IF NOT EXISTS idx_native_requests_notification ON native_notification_requests(notification_id);

CREATE TABLE IF NOT EXISTS curiosity_runs (
    run_id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    task_id TEXT,
    trace_id TEXT,
    query_text TEXT NOT NULL,
    source_profile_ids_json TEXT NOT NULL DEFAULT '[]',
    snippets_json TEXT NOT NULL DEFAULT '[]',
    candidate_id TEXT,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (topic_id) REFERENCES curiosity_topics(topic_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_curiosity_runs_topic
ON curiosity_runs(topic_id, created_at DESC);

CREATE TABLE IF NOT EXISTS context_access_log (
    log_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    total_context_budget INTEGER NOT NULL,
    bootstrap_tokens_used INTEGER NOT NULL DEFAULT 0,
    relevant_tokens_used INTEGER NOT NULL DEFAULT 0,
    cold_tokens_used INTEGER NOT NULL DEFAULT 0,
    retrieval_confidence TEXT NOT NULL DEFAULT 'low',
    swarm_metadata_consulted INTEGER NOT NULL DEFAULT 0,
    cold_archive_opened INTEGER NOT NULL DEFAULT 0,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_evidence_log (
    entry_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_domain TEXT,
    media_kind TEXT NOT NULL,
    reference TEXT NOT NULL,
    credibility_score REAL NOT NULL DEFAULT 0.0,
    blocked INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_evidence_log_task
ON media_evidence_log(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hive_topics (
    topic_id TEXT PRIMARY KEY,
    created_by_agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    topic_tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'open',
    visibility TEXT NOT NULL DEFAULT 'agent_public',
    evidence_mode TEXT NOT NULL DEFAULT 'candidate_only',
    linked_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hive_topics_status
ON hive_topics(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS hive_posts (
    post_id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    author_agent_id TEXT NOT NULL,
    post_kind TEXT NOT NULL DEFAULT 'analysis',
    stance TEXT NOT NULL DEFAULT 'propose',
    body TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (topic_id) REFERENCES hive_topics(topic_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hive_posts_topic
ON hive_posts(topic_id, created_at ASC);

CREATE TABLE IF NOT EXISTS hive_topic_claims (
    claim_id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT,
    capability_tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (topic_id, agent_id),
    FOREIGN KEY (topic_id) REFERENCES hive_topics(topic_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hive_topic_claims_topic
ON hive_topic_claims(topic_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_hive_topic_claims_agent
ON hive_topic_claims(agent_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS hive_claim_links (
    claim_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    owner_label TEXT,
    visibility TEXT NOT NULL DEFAULT 'public',
    verified_state TEXT NOT NULL DEFAULT 'self_declared',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (agent_id, platform, handle)
);

CREATE INDEX IF NOT EXISTS idx_hive_claim_links_agent
ON hive_claim_links(agent_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS hive_write_grants (
    grant_id TEXT PRIMARY KEY,
    granted_by TEXT NOT NULL,
    granted_to TEXT NOT NULL,
    allowed_paths_json TEXT NOT NULL DEFAULT '[]',
    topic_id TEXT NOT NULL DEFAULT '',
    claim_id TEXT NOT NULL DEFAULT '',
    max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    max_body_bytes INTEGER NOT NULL DEFAULT 16384,
    review_required_by_default INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    issued_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_hive_write_grants_target
ON hive_write_grants(granted_to, status, expires_at);

CREATE TABLE IF NOT EXISTS hive_moderation_reviews (
    review_id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    note TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(object_type, object_id, reviewer_agent_id)
);

CREATE INDEX IF NOT EXISTS idx_hive_moderation_reviews_object
ON hive_moderation_reviews(object_type, object_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_hive_moderation_reviews_reviewer
ON hive_moderation_reviews(reviewer_agent_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_access_log_created
ON context_access_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_context_access_log_task
ON context_access_log(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS candidate_knowledge_lane (
    candidate_id TEXT PRIMARY KEY,
    task_hash TEXT NOT NULL,
    task_id TEXT,
    trace_id TEXT,
    task_class TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    output_mode TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    normalized_output TEXT NOT NULL,
    structured_output_json TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    trust_score REAL NOT NULL DEFAULT 0.0,
    validation_state TEXT NOT NULL DEFAULT 'candidate',
    promotion_state TEXT NOT NULL DEFAULT 'candidate',
    review_state TEXT NOT NULL DEFAULT 'unreviewed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    invalidated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_lane_task_hash
ON candidate_knowledge_lane(task_hash, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_candidate_lane_created
ON candidate_knowledge_lane(created_at DESC);

CREATE TABLE IF NOT EXISTS artifact_manifests (
    artifact_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    topic_id TEXT NOT NULL DEFAULT '',
    claim_id TEXT NOT NULL DEFAULT '',
    candidate_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    search_text TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    file_path TEXT NOT NULL,
    storage_backend TEXT NOT NULL DEFAULT 'local_archive',
    content_sha256 TEXT NOT NULL DEFAULT '',
    raw_bytes INTEGER NOT NULL DEFAULT 0,
    compressed_bytes INTEGER NOT NULL DEFAULT 0,
    compression_ratio REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifact_manifests_topic_created
ON artifact_manifests(topic_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_artifact_manifests_source_created
ON artifact_manifests(source_kind, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_artifact_manifests_session_created
ON artifact_manifests(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS capability_tokens (
    token_id TEXT PRIMARY KEY,
    capability_name TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    granted_to TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    used_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
ON audit_log(event_type);

CREATE TABLE IF NOT EXISTS nonce_cache (
    sender_peer_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (sender_peer_id, nonce)
);

CREATE TABLE IF NOT EXISTS agent_capabilities (
    peer_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    compute_class TEXT NOT NULL DEFAULT 'cpu_basic',
    supported_models_json TEXT NOT NULL DEFAULT '[]',
    capacity INTEGER NOT NULL DEFAULT 0,
    trust_score REAL NOT NULL DEFAULT 0.5,
    self_reported_trust REAL NOT NULL DEFAULT 0.5,
    assist_filters_json TEXT NOT NULL DEFAULT '{}',
    host_group_hint_hash TEXT,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_offers (
    task_id TEXT PRIMARY KEY,
    parent_peer_id TEXT NOT NULL,
    capsule_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    subtask_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    input_capsule_hash TEXT NOT NULL,
    required_capabilities_json TEXT NOT NULL,
    reward_hint_json TEXT NOT NULL DEFAULT '{}',
    max_helpers INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'normal',
    deadline_ts TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_offers_status
ON task_offers(status);

CREATE TABLE IF NOT EXISTS task_claims (
    claim_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    declared_capabilities_json TEXT NOT NULL,
    current_load INTEGER NOT NULL DEFAULT 0,
    host_group_hint_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    claimed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_offers(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_claims_task_id
ON task_claims(task_id);

CREATE TABLE IF NOT EXISTS task_assignments (
    assignment_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    parent_peer_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    assignment_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    capability_token_id TEXT,
    lease_expires_at TEXT,
    last_progress_state TEXT NOT NULL DEFAULT '',
    last_progress_note TEXT NOT NULL DEFAULT '',
    assigned_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    progress_updated_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (task_id) REFERENCES task_offers(task_id) ON DELETE CASCADE,
    FOREIGN KEY (claim_id) REFERENCES task_claims(claim_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_results (
    result_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    result_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    result_hash TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    abstract_steps_json TEXT NOT NULL DEFAULT '[]',
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'submitted',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_offers(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_progress_events (
    event_id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    progress_state TEXT NOT NULL,
    progress_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_offers(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_progress_events_task_created
ON task_progress_events(task_id, created_at DESC);

CREATE TABLE IF NOT EXISTS task_reviews (
    review_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    reviewer_peer_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    helpfulness_score REAL NOT NULL CHECK (helpfulness_score >= 0 AND helpfulness_score <= 1),
    quality_score REAL NOT NULL CHECK (quality_score >= 0 AND quality_score <= 1),
    harmful_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_offers(task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS contribution_ledger (
    entry_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    parent_peer_id TEXT NOT NULL,
    contribution_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    helpfulness_score REAL NOT NULL DEFAULT 0,
    points_awarded INTEGER NOT NULL DEFAULT 0,
    wnull_pending INTEGER NOT NULL DEFAULT 0,
    wnull_released INTEGER NOT NULL DEFAULT 0,
    compute_credits_pending REAL NOT NULL DEFAULT 0,
    compute_credits_released REAL NOT NULL DEFAULT 0,
    finality_state TEXT NOT NULL DEFAULT 'pending',
    finality_depth INTEGER NOT NULL DEFAULT 0,
    finality_target INTEGER NOT NULL DEFAULT 2,
    confirmed_at TEXT,
    finalized_at TEXT,
    parent_host_group_hint_hash TEXT,
    helper_host_group_hint_hash TEXT,
    slashed_flag INTEGER NOT NULL DEFAULT 0,
    fraud_window_end_ts TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contribution_ledger_helper
ON contribution_ledger(helper_peer_id);

CREATE INDEX IF NOT EXISTS idx_contribution_ledger_outcome
ON contribution_ledger(outcome);

CREATE TABLE IF NOT EXISTS contribution_proof_receipts (
    receipt_id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    helper_peer_id TEXT NOT NULL,
    parent_peer_id TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    finality_state TEXT NOT NULL DEFAULT '',
    finality_depth INTEGER NOT NULL DEFAULT 0,
    finality_target INTEGER NOT NULL DEFAULT 0,
    compute_credits REAL NOT NULL DEFAULT 0.0,
    points_awarded INTEGER NOT NULL DEFAULT 0,
    challenge_reason TEXT NOT NULL DEFAULT '',
    previous_receipt_id TEXT NOT NULL DEFAULT '',
    previous_receipt_hash TEXT NOT NULL DEFAULT '',
    receipt_hash TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (entry_id) REFERENCES contribution_ledger(entry_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_contribution_proof_receipts_entry_created
ON contribution_proof_receipts(entry_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contribution_proof_receipts_helper_created
ON contribution_proof_receipts(helper_peer_id, created_at DESC);

CREATE TABLE IF NOT EXISTS anti_abuse_signals (
    signal_id TEXT PRIMARY KEY,
    peer_id TEXT,
    related_peer_id TEXT,
    task_id TEXT,
    signal_type TEXT NOT NULL,
    severity REAL NOT NULL CHECK (severity >= 0 AND severity <= 1),
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_capsules (
    capsule_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    parent_peer_id TEXT NOT NULL,
    capsule_hash TEXT NOT NULL,
    capsule_json TEXT NOT NULL,
    parent_task_ref TEXT,
    verification_of_task_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES task_offers(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_capsules_task_id
ON task_capsules(task_id);

CREATE TABLE IF NOT EXISTS finalized_responses (
    parent_task_id TEXT PRIMARY KEY,
    raw_synthesized_text TEXT,
    rendered_persona_text TEXT,
    status_marker TEXT,
    confidence_score REAL,
    content_hash TEXT NOT NULL DEFAULT '',
    anchored_signature TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS peer_endpoints (
    peer_id TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'direct',   -- self, bootstrap, observed
    last_seen_at TEXT NOT NULL,
    last_verified_at TEXT NOT NULL DEFAULT '',
    verification_kind TEXT NOT NULL DEFAULT '',
    proof_count INTEGER NOT NULL DEFAULT 0,
    proof_message_id TEXT NOT NULL DEFAULT '',
    proof_message_type TEXT NOT NULL DEFAULT '',
    proof_hash TEXT NOT NULL DEFAULT '',
    proof_timestamp TEXT NOT NULL DEFAULT '',
    last_delivery_attempt_at TEXT NOT NULL DEFAULT '',
    last_delivery_success_at TEXT NOT NULL DEFAULT '',
    last_delivery_failure_at TEXT NOT NULL DEFAULT '',
    consecutive_delivery_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, host, port)
);

CREATE INDEX IF NOT EXISTS idx_peer_endpoints_peer_recent
ON peer_endpoints(peer_id, last_verified_at DESC, last_seen_at DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_peer_endpoints_recent
ON peer_endpoints(updated_at DESC);

CREATE TABLE IF NOT EXISTS peer_endpoint_observations (
    peer_id TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'observed',
    verification_kind TEXT NOT NULL DEFAULT 'protocol_signature',
    proof_message_id TEXT NOT NULL DEFAULT '',
    proof_message_type TEXT NOT NULL DEFAULT '',
    proof_hash TEXT NOT NULL DEFAULT '',
    proof_signature TEXT NOT NULL DEFAULT '',
    proof_timestamp TEXT NOT NULL DEFAULT '',
    first_verified_at TEXT NOT NULL,
    last_verified_at TEXT NOT NULL,
    proof_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, host, port, source)
);

CREATE INDEX IF NOT EXISTS idx_peer_endpoint_observations_peer
ON peer_endpoint_observations(peer_id, last_verified_at DESC);

CREATE INDEX IF NOT EXISTS idx_peer_endpoint_observations_recent
ON peer_endpoint_observations(last_verified_at DESC);

CREATE TABLE IF NOT EXISTS peer_endpoint_candidates (
    peer_id TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'dht',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_probe_attempt_at TEXT NOT NULL DEFAULT '',
    last_probe_delivery_ok INTEGER NOT NULL DEFAULT 0,
    consecutive_probe_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, host, port, source)
);

CREATE INDEX IF NOT EXISTS idx_peer_endpoint_candidates_seen
ON peer_endpoint_candidates(last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_peer_endpoint_candidates_peer
ON peer_endpoint_candidates(peer_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS scoreboard (
    entry_id          TEXT PRIMARY KEY,
    peer_id           TEXT NOT NULL,
    score_type        TEXT NOT NULL,
    delta             REAL NOT NULL,
    reason            TEXT,
    related_task_id   TEXT,
    related_peer_id   TEXT,
    season            INTEGER DEFAULT 1,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scoreboard_peer_id
ON scoreboard(peer_id);

CREATE INDEX IF NOT EXISTS idx_scoreboard_type_season
ON scoreboard(score_type, season);

CREATE TABLE IF NOT EXISTS compute_credit_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT NOT NULL,
    amount REAL NOT NULL,
    reason TEXT NOT NULL,
    receipt_id TEXT,
    settlement_mode TEXT NOT NULL DEFAULT 'simulated',
    receipt_hash TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    UNIQUE(receipt_id)
);

CREATE INDEX IF NOT EXISTS idx_compute_credit_ledger_peer
ON compute_credit_ledger(peer_id);

CREATE TABLE IF NOT EXISTS swarm_dispatch_budget_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT NOT NULL,
    day_bucket TEXT NOT NULL,
    amount REAL NOT NULL,
    dispatch_mode TEXT NOT NULL,
    reason TEXT NOT NULL,
    receipt_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(receipt_id)
);

CREATE INDEX IF NOT EXISTS idx_swarm_dispatch_budget_peer_day
ON swarm_dispatch_budget_events(peer_id, day_bucket, created_at DESC);

CREATE TABLE IF NOT EXISTS public_hive_write_quota_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT NOT NULL,
    day_bucket TEXT NOT NULL,
    route TEXT NOT NULL,
    amount REAL NOT NULL,
    trust_score REAL NOT NULL DEFAULT 0.0,
    trust_tier TEXT NOT NULL DEFAULT 'newcomer',
    request_nonce TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(request_nonce)
);

CREATE INDEX IF NOT EXISTS idx_public_hive_write_quota_peer_day
ON public_hive_write_quota_events(peer_id, day_bucket, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_public_hive_write_quota_route_day
ON public_hive_write_quota_events(route, day_bucket, created_at DESC);

CREATE TABLE IF NOT EXISTS meet_write_rate_limit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_key TEXT NOT NULL,
    window_seconds INTEGER NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at_epoch REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meet_write_rate_limit_bucket_window
ON meet_write_rate_limit_events(bucket_key, window_seconds, created_at_epoch DESC);

CREATE TABLE IF NOT EXISTS dna_wallet_profiles (
    profile_id TEXT PRIMARY KEY,
    hot_wallet_address TEXT,
    cold_wallet_address TEXT,
    hot_balance_usdc REAL NOT NULL DEFAULT 0,
    cold_balance_usdc REAL NOT NULL DEFAULT 0,
    hot_auto_spend_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dna_wallet_security (
    profile_id TEXT PRIMARY KEY,
    cold_secret_salt TEXT NOT NULL,
    cold_secret_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(profile_id) REFERENCES dna_wallet_profiles(profile_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dna_wallet_ledger (
    entry_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    asset_symbol TEXT NOT NULL,
    amount REAL NOT NULL,
    initiated_by TEXT NOT NULL,
    approval_mode TEXT NOT NULL,
    reference_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(profile_id) REFERENCES dna_wallet_profiles(profile_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dna_wallet_ledger_profile
ON dna_wallet_ledger(profile_id, created_at DESC);

CREATE TABLE IF NOT EXISTS model_provider_manifests (
    provider_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    adapter_type TEXT,
    license_name TEXT,
    license_url_or_reference TEXT,
    weight_location TEXT NOT NULL DEFAULT 'external',
    redistribution_allowed INTEGER,
    runtime_dependency TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    runtime_config_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider_name, model_name)
);

CREATE INDEX IF NOT EXISTS idx_model_provider_manifests_enabled
ON model_provider_manifests(enabled, provider_name, model_name);

CREATE TABLE IF NOT EXISTS cloud_model_catalog (
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider_id, model_id)
);

CREATE INDEX IF NOT EXISTS idx_cloud_model_catalog_expiry
ON cloud_model_catalog(provider_id, expires_at);

CREATE TABLE IF NOT EXISTS agent_names (
    entry_id        TEXT PRIMARY KEY,
    peer_id         TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    canonical_name  TEXT NOT NULL UNIQUE,
    claimed_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_names_canonical
ON agent_names(canonical_name);

CREATE TABLE IF NOT EXISTS runtime_sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 0,
    last_event_type TEXT NOT NULL DEFAULT '',
    last_message TEXT NOT NULL DEFAULT '',
    request_preview TEXT NOT NULL DEFAULT '',
    task_class TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    last_checkpoint_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_runtime_sessions_updated
ON runtime_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS runtime_session_events (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_runtime_session_events_session_created
ON runtime_session_events(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS runtime_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    task_class TEXT NOT NULL DEFAULT '',
    request_text TEXT NOT NULL,
    source_context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'running',
    step_count INTEGER NOT NULL DEFAULT 0,
    last_tool_name TEXT NOT NULL DEFAULT '',
    pending_intent_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    final_response TEXT NOT NULL DEFAULT '',
    final_response_hash TEXT NOT NULL DEFAULT '',
    failure_text TEXT NOT NULL DEFAULT '',
    outcome_json TEXT NOT NULL DEFAULT '{}',
    resume_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    resumed_from_checkpoint_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_runtime_checkpoints_session_updated
ON runtime_checkpoints(session_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_runtime_checkpoints_status
ON runtime_checkpoints(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS runtime_tool_receipts (
    receipt_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL DEFAULT '',
    arguments_json TEXT NOT NULL DEFAULT '{}',
    execution_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_tool_receipts_checkpoint
ON runtime_tool_receipts(checkpoint_id, updated_at DESC);

-- A6 effect reconciliation (assembly W001). `runtime_tool_receipts` above is
-- checkpoint-scoped replay evidence; these two tables hold the CROSS-TURN truth
-- state of one logical external effect: did an authorized effect actually happen?
-- UNKNOWN is a first-class durable state here and never collapses into FAILED.
-- One ACTIVE row per logical effect is enforced by the partial unique index below
-- (same proven pattern as idx_runtime_attempts_retry_idempotency).
CREATE TABLE IF NOT EXISTS runtime_unresolved_effects (
    logical_effect_id   TEXT NOT NULL,
    effect_instance_id  TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    resource_identity   TEXT NOT NULL DEFAULT '',
    expected_evidence_json TEXT NOT NULL DEFAULT '{}',
    state               TEXT NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    detail              TEXT NOT NULL DEFAULT '',
    claimed_by          TEXT,
    claimed_at          TEXT,
    dispatched_at       TEXT,
    attempt_id          TEXT NOT NULL DEFAULT '',
    turn_id             TEXT NOT NULL DEFAULT '',
    checkpoint_id       TEXT NOT NULL DEFAULT '',
    receipt_key         TEXT NOT NULL DEFAULT '',
    semantic_result_id  TEXT NOT NULL DEFAULT '',
    reconcilability     TEXT NOT NULL DEFAULT 'unknown',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (logical_effect_id, effect_instance_id)
);

-- At most ONE active (prepared/dispatched/unknown) row per logical effect. This
-- index is the cross-process concurrency primitive backing reserve/claim.
CREATE UNIQUE INDEX IF NOT EXISTS ux_unresolved_active_effect
ON runtime_unresolved_effects (logical_effect_id)
WHERE state IN ('prepared', 'dispatched', 'unknown');

-- Append-only resolution history. Resolutions are EVENTS (who/what/source/
-- evidence), never silent mutations of the effects row's past.
CREATE TABLE IF NOT EXISTS unresolved_effect_resolutions (
    resolution_id       TEXT PRIMARY KEY,
    logical_effect_id   TEXT NOT NULL,
    effect_instance_id  TEXT NOT NULL DEFAULT '',
    resolution          TEXT NOT NULL,
    source              TEXT NOT NULL,
    evidence            TEXT NOT NULL DEFAULT '',
    resolved_by         TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_resolutions_by_effect
ON unresolved_effect_resolutions(logical_effect_id, created_at ASC);

-- The authoritative record of what the runtime actually executed, one row per real execution.
-- `runtime_tool_receipts` above holds the ARGUMENTS AND RESULT of the paths that write one; this
-- holds the FACT THAT IT RAN for every path, which is a different question and the one every
-- consumer was previously answering from whichever store it happened to know about. Activity, the
-- signed honesty ledger, the tool/model counters and the turn trace all derive from these rows, so
-- they cannot report different realities. See core/execution_truth.py.
--
-- `fact_id` is a content hash rather than a random id: one execution must yield one fact even when
-- the event behind it is emitted twice, and an INSERT OR IGNORE on a deterministic key is what
-- makes the second write a no-op instead of a duplicate that inflates every derived count at once.
CREATE TABLE IF NOT EXISTS execution_facts (
    fact_id TEXT PRIMARY KEY,
    turn_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    ok INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_facts_turn
ON execution_facts(turn_key, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_execution_facts_session
ON execution_facts(session_id, created_at DESC);

-- The ONE versioned fault vocabulary, durably recorded. Every failure mapped at an owning
-- boundary becomes a row here; the schema rides on the row so a reader always knows which
-- catalog version it is reading. `fault_id` is a content hash (schema+code+authority+identity)
-- so re-recording one failure is a no-op, never a duplicate. See core/faults/.
CREATE TABLE IF NOT EXISTS fault_records (
    fault_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    retry TEXT NOT NULL DEFAULT '',
    lifecycle TEXT NOT NULL DEFAULT 'raised',
    user_message TEXT NOT NULL DEFAULT '',
    operator_action TEXT NOT NULL DEFAULT '',
    authority TEXT NOT NULL DEFAULT '',
    turn_key TEXT NOT NULL DEFAULT '',
    attempt_id TEXT NOT NULL DEFAULT '',
    effect_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    dedupe TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    context_json TEXT NOT NULL DEFAULT '{}',
    cause_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_fault_records_turn
ON fault_records(turn_key, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_fault_records_code
ON fault_records(code, created_at DESC);

-- The security-event plane: one OBSERVATION per fault the catalog declares
-- security-relevant, derived at the same instant the fault is recorded, with the fault id
-- carried as evidence. Wording law: these rows name mechanisms, never suspects. See
-- core/security_events/.
CREATE TABLE IF NOT EXISTS security_events (
    event_id TEXT PRIMARY KEY,
    sec_code TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'observed',
    source TEXT NOT NULL DEFAULT '',
    resource_class TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    fault_id TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    turn_key TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL DEFAULT '',
    acknowledged_at TEXT NOT NULL DEFAULT '',
    acknowledged_by TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT '',
    resolution_note TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_security_events_turn
ON security_events(turn_key, observed_at ASC);

CREATE INDEX IF NOT EXISTS idx_security_events_state
ON security_events(state, observed_at DESC);

-- A generic runtime execution attempt: one row per attempt at answering a request through a
-- typed, multi-step plan (LIVE_DATA today; workspace/tool-call/audit plans later -- nothing here
-- encodes market/weather-specific assumptions). A retry creates a NEW row linked via
-- parent_attempt_id/root_attempt_id rather than mutating the prior attempt, so the full retry
-- chain for one original request stays inspectable.
CREATE TABLE IF NOT EXISTS runtime_attempts (
    attempt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL DEFAULT '',

    origin_user_turn_id TEXT NOT NULL DEFAULT '',
    trigger_user_turn_id TEXT NOT NULL DEFAULT '',
    origin_conversation_event_id TEXT NOT NULL DEFAULT '',

    root_attempt_id TEXT NOT NULL DEFAULT '',
    parent_attempt_id TEXT NOT NULL DEFAULT '',
    execution_generation INTEGER NOT NULL DEFAULT 1,

    -- ARCH-TRUTH-R1c: what this row IS inside its turn's chain, machine-readable.
    -- 'turn_root' (the turn door's own execution attempt, the chain root), 'answer'
    -- (the lane that produced the turn's answer), 'planner_task' (one planned
    -- sub-request under the same turn), 'retry' (a new generation of the chain).
    -- Legacy rows written before this column keep '' and are treated as untyped:
    -- readers that rank by role fall back to their prior ordering for them, so an
    -- existing database is never re-labelled with a role nobody recorded.
    attempt_role TEXT NOT NULL DEFAULT '',

    -- ARCH-TRUTH-R1d: which SLOT of the chain this row executes. '' for the chain's
    -- ordinary rows (the turn root, the turn's single answer, a retry generation); a
    -- stable per-task key for one planned sub-request. Executing exclusivity is keyed on
    -- it, so two DISTINCT planner tasks of one turn may be live at once while the SAME
    -- task still cannot be executing twice.
    execution_slot TEXT NOT NULL DEFAULT '',

    original_request_snapshot TEXT NOT NULL DEFAULT '',
    original_request_hash TEXT NOT NULL DEFAULT '',
    original_request_bytes INTEGER NOT NULL DEFAULT 0,
    original_request_truncated INTEGER NOT NULL DEFAULT 0,

    answer_mode TEXT NOT NULL DEFAULT '',
    plan_id TEXT NOT NULL DEFAULT '',

    lifecycle_state TEXT NOT NULL DEFAULT 'RECEIVED',
    terminal_reason TEXT NOT NULL DEFAULT '',

    retryable INTEGER NOT NULL DEFAULT 0,
    retry_reason TEXT NOT NULL DEFAULT '',
    retry_from_stage TEXT NOT NULL DEFAULT '',
    refresh_required INTEGER NOT NULL DEFAULT 0,
    refresh_reason TEXT NOT NULL DEFAULT '',

    created_daemon_sha TEXT NOT NULL DEFAULT '',
    last_updated_daemon_sha TEXT NOT NULL DEFAULT '',
    process_instance_id TEXT NOT NULL DEFAULT '',

    -- Repair 2/3 (Mnemosyne review): a stable digest of (parent_attempt_id, trigger_user_turn_id,
    -- resolution_intent), set ONLY on retry-created rows. A real database uniqueness constraint,
    -- not a process-local lock, is what makes "two OS processes racing the same retry" produce one
    -- child row instead of two -- a process-local lock cannot see across process boundaries. The
    -- partial unique index below (WHERE != '') deliberately excludes first-generation attempts,
    -- which never set this column and would otherwise all collide on ''.
    retry_idempotency_key TEXT NOT NULL DEFAULT '',

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_runtime_attempts_session_updated
ON runtime_attempts(session_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_runtime_attempts_checkpoint
ON runtime_attempts(checkpoint_id);

CREATE INDEX IF NOT EXISTS idx_runtime_attempts_root
ON runtime_attempts(root_attempt_id, execution_generation DESC);

CREATE INDEX IF NOT EXISTS idx_runtime_attempts_state
ON runtime_attempts(lifecycle_state, updated_at DESC);

-- ARCH-TRUTH-R1c: answer-bearing resolution reads (session, role, recency).
CREATE INDEX IF NOT EXISTS idx_runtime_attempts_session_role
ON runtime_attempts(session_id, attempt_role, updated_at DESC);

-- Repair 1 (Mnemosyne final review, 2026-08-07): the partial unique index on
-- retry_idempotency_key is deliberately NOT created here. On a LEGACY database that already has
-- runtime_attempts (from before this column existed), "CREATE TABLE IF NOT EXISTS" above is a
-- no-op -- the column never gets added by it -- so an index statement placed here, inside this
-- executescript() batch, would immediately raise "OperationalError: no such column:
-- retry_idempotency_key" and abort the ENTIRE script, silently skipping every table/index defined
-- below this point AND every _add_column_if_missing() call in run_migrations() (which runs AFTER
-- this script, in the same try block, and would otherwise have added the missing column). The
-- index is instead created in run_migrations() itself, immediately after
-- _add_column_if_missing(conn, "runtime_attempts", "retry_idempotency_key", ...) guarantees the
-- column exists on every database, fresh or legacy -- see that ordering below.

-- One row per subtask per execution generation. Every subtask transition updates only ITS OWN
-- row (primary key includes subtask_id), so concurrent workers completing different subtasks
-- never contend for the same row and there is no read-modify-write of a shared blob to race on --
-- this is the structural fix for the lost-update hazard a single whole-JSON-blob design has.
CREATE TABLE IF NOT EXISTS runtime_attempt_subtasks (
    attempt_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL,
    execution_generation INTEGER NOT NULL DEFAULT 1,

    plan_id TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    entity_type TEXT NOT NULL DEFAULT '',
    entity_key TEXT NOT NULL DEFAULT '',
    arguments_json TEXT NOT NULL DEFAULT '{}',

    lifecycle_state TEXT NOT NULL DEFAULT 'PLANNED',
    result_summary_json TEXT NOT NULL DEFAULT '{}',
    failure_class TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    approval_state TEXT NOT NULL DEFAULT '',

    retryable INTEGER NOT NULL DEFAULT 0,
    retry_reason TEXT NOT NULL DEFAULT '',
    refresh_required INTEGER NOT NULL DEFAULT 0,
    refresh_reason TEXT NOT NULL DEFAULT '',

    -- Step 10: retry provenance. Set only on a subtask CARRIED FORWARD from a prior execution
    -- generation (carried_forward_from_attempt_id != '') or RERUN because the prior generation's
    -- result was transient (rerun_reason != '') -- never both on the same row.
    carried_forward_from_attempt_id TEXT NOT NULL DEFAULT '',
    carried_forward_from_generation INTEGER NOT NULL DEFAULT 0,
    rerun_reason TEXT NOT NULL DEFAULT '',
    previous_result_version INTEGER NOT NULL DEFAULT 0,

    queued_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    retrieved_at TEXT,

    result_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,

    PRIMARY KEY (attempt_id, subtask_id, execution_generation)
);

CREATE INDEX IF NOT EXISTS idx_runtime_attempt_subtasks_attempt
ON runtime_attempt_subtasks(attempt_id, execution_generation);

-- Durable approval decisions, valid only for the exact matching immutable action/plan digest --
-- the in-memory approval maps in core.mode_permission_policy are not authoritative after a
-- restart, and a changed plan/argument set must never silently inherit an old approval.
CREATE TABLE IF NOT EXISTS runtime_attempt_approvals (
    approval_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    subtask_id TEXT NOT NULL DEFAULT '',
    action_digest TEXT NOT NULL DEFAULT '',
    plan_digest TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL DEFAULT '',
    requested_scope TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    deciding_turn_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_runtime_attempt_approvals_attempt
ON runtime_attempt_approvals(attempt_id);

-- Step 9: one row per resolved (or explicitly unresolved) short follow-up -- "why did that fail?",
-- "retry", "which assets did I ask for?". Durable so a follow-up's routing decision is itself
-- inspectable/auditable, not just its side effect.
CREATE TABLE IF NOT EXISTS runtime_followup_resolutions (
    resolution_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    follow_up_turn_id TEXT NOT NULL DEFAULT '',
    resolved_attempt_id TEXT NOT NULL DEFAULT '',
    resolution_intent TEXT NOT NULL DEFAULT '',
    resolution_reason TEXT NOT NULL DEFAULT '',
    resolution_confidence REAL NOT NULL DEFAULT 0.0,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_followup_resolutions_session
ON runtime_followup_resolutions(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS message_queue (
    queue_item_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    lease_owner TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_message_queue_session_status
ON message_queue(session_id, status, seq);

CREATE UNIQUE INDEX IF NOT EXISTS idx_message_queue_idem
ON message_queue(session_id, idempotency_key)
WHERE idempotency_key != '';

-- Last grounded machine read per session, so an elliptical follow-up ("ok what about D?",
-- "the biggest file on that drive") re-runs the real tool scoped to the right drive even if the
-- server restarted between turns. Structured conversational reference (entity_type/value/source
-- turn) so it is not a bare global. Without persistence a restart re-opens the model-fabrication
-- path the in-process cache alone cannot survive.
CREATE TABLE IF NOT EXISTS machine_read_memory (
    session_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL DEFAULT 'drive',
    kind TEXT NOT NULL DEFAULT '',
    drive TEXT NOT NULL DEFAULT '',
    source_turn_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

-- The live-data OBLIGATION currently on the table for a session: which typed operation the
-- conversation opened, which entities have filled its slot so far, and the user's own phrasing of
-- it. A follow-up resolves against THIS, not against the prior message text.
--
-- Text is not enough, and the measured G1 failure is why. Turn 2 ("What about Tallinn?") carries no
-- weather word, so re-reading the transcript classifies it as an ordinary chat turn; by turn 3
-- ("Which one is warmer?") the nearest prior user message is that same unclassifiable turn, and a
-- text-only walk-back finds no live-data request at all. Recording what turn 2 RESOLVED TO -- not
-- what it said -- is what keeps the obligation reachable on turn 3.
--
-- `absorbed_json` holds the raw user texts this obligation has already answered, so walking back
-- can tell "part of the obligation" from "an unrelated request that interrupted it". That
-- distinction is the whole of the break-inheritance rule.
CREATE TABLE IF NOT EXISTS live_data_obligation_memory (
    session_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL DEFAULT '',
    slots_json TEXT NOT NULL DEFAULT '[]',
    request_text TEXT NOT NULL DEFAULT '',
    absorbed_json TEXT NOT NULL DEFAULT '[]',
    source_turn_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hive_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adaptation_corpora (
    corpus_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    source_config_json TEXT NOT NULL DEFAULT '{}',
    filters_json TEXT NOT NULL DEFAULT '{}',
    output_path TEXT NOT NULL DEFAULT '',
    example_count INTEGER NOT NULL DEFAULT 0,
    source_stats_json TEXT NOT NULL DEFAULT '{}',
    quality_score REAL NOT NULL DEFAULT 0.0,
    quality_details_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL DEFAULT '',
    last_scored_at TEXT,
    latest_build_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_adaptation_corpora_updated
ON adaptation_corpora(updated_at DESC);

CREATE TABLE IF NOT EXISTS adaptation_jobs (
    job_id TEXT PRIMARY KEY,
    corpus_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    base_model_ref TEXT NOT NULL,
    base_provider_name TEXT NOT NULL DEFAULT '',
    base_model_name TEXT NOT NULL DEFAULT '',
    adapter_provider_name TEXT NOT NULL DEFAULT '',
    adapter_model_name TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    device TEXT NOT NULL DEFAULT '',
    dependency_status_json TEXT NOT NULL DEFAULT '{}',
    training_config_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    registered_manifest_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    completed_at TEXT,
    promoted_at TEXT,
    rolled_back_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (corpus_id) REFERENCES adaptation_corpora(corpus_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_adaptation_jobs_status_updated
ON adaptation_jobs(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_adaptation_jobs_corpus_updated
ON adaptation_jobs(corpus_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS adaptation_job_events (
    job_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, seq),
    FOREIGN KEY (job_id) REFERENCES adaptation_jobs(job_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_adaptation_job_events_created
ON adaptation_job_events(job_id, created_at DESC);

CREATE TABLE IF NOT EXISTS adaptation_eval_runs (
    eval_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    corpus_id TEXT NOT NULL DEFAULT '',
    eval_kind TEXT NOT NULL DEFAULT '',
    split_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    sample_count INTEGER NOT NULL DEFAULT 0,
    baseline_provider_ref TEXT NOT NULL DEFAULT '',
    candidate_provider_ref TEXT NOT NULL DEFAULT '',
    baseline_score REAL NOT NULL DEFAULT 0.0,
    candidate_score REAL NOT NULL DEFAULT 0.0,
    score_delta REAL NOT NULL DEFAULT 0.0,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    decision TEXT NOT NULL DEFAULT '',
    error_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES adaptation_jobs(job_id) ON DELETE CASCADE,
    FOREIGN KEY (corpus_id) REFERENCES adaptation_corpora(corpus_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_adaptation_eval_runs_job_kind_updated
ON adaptation_eval_runs(job_id, eval_kind, updated_at DESC);

CREATE TABLE IF NOT EXISTS adaptation_loop_state (
    loop_name TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'idle',
    base_model_ref TEXT NOT NULL DEFAULT '',
    base_provider_name TEXT NOT NULL DEFAULT '',
    base_model_name TEXT NOT NULL DEFAULT '',
    active_job_id TEXT NOT NULL DEFAULT '',
    active_provider_name TEXT NOT NULL DEFAULT '',
    active_model_name TEXT NOT NULL DEFAULT '',
    previous_job_id TEXT NOT NULL DEFAULT '',
    previous_provider_name TEXT NOT NULL DEFAULT '',
    previous_model_name TEXT NOT NULL DEFAULT '',
    last_corpus_id TEXT NOT NULL DEFAULT '',
    last_corpus_hash TEXT NOT NULL DEFAULT '',
    last_example_count INTEGER NOT NULL DEFAULT 0,
    last_quality_score REAL NOT NULL DEFAULT 0.0,
    last_eval_id TEXT NOT NULL DEFAULT '',
    last_canary_eval_id TEXT NOT NULL DEFAULT '',
    last_tick_at TEXT,
    last_completed_tick_at TEXT,
    last_decision TEXT NOT NULL DEFAULT '',
    last_reason TEXT NOT NULL DEFAULT '',
    last_error_text TEXT NOT NULL DEFAULT '',
    last_metadata_publish_at TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS useful_outputs (
    useful_output_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    task_id TEXT NOT NULL DEFAULT '',
    topic_id TEXT NOT NULL DEFAULT '',
    claim_id TEXT NOT NULL DEFAULT '',
    result_id TEXT NOT NULL DEFAULT '',
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    instruction_text TEXT NOT NULL DEFAULT '',
    output_text TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    acceptance_state TEXT NOT NULL DEFAULT '',
    review_state TEXT NOT NULL DEFAULT '',
    archive_state TEXT NOT NULL DEFAULT 'transient',
    eligibility_state TEXT NOT NULL DEFAULT 'ineligible',
    durability_reasons_json TEXT NOT NULL DEFAULT '[]',
    eligibility_reasons_json TEXT NOT NULL DEFAULT '[]',
    quality_score REAL NOT NULL DEFAULT 0.0,
    source_created_at TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_useful_outputs_source_type_updated
ON useful_outputs(source_type, source_updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_useful_outputs_eligibility
ON useful_outputs(eligibility_state, archive_state, quality_score DESC);

CREATE TABLE IF NOT EXISTS hive_post_endorsements (
    endorsement_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    endorsement_kind TEXT NOT NULL DEFAULT 'endorse',
    note TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(post_id, agent_id),
    FOREIGN KEY (post_id) REFERENCES hive_posts(post_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hive_post_endorsements_post
ON hive_post_endorsements(post_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_hive_post_endorsements_agent
ON hive_post_endorsements(agent_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS hive_post_comments (
    comment_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    author_agent_id TEXT NOT NULL,
    body TEXT NOT NULL,
    moderation_state TEXT NOT NULL DEFAULT 'approved',
    moderation_score REAL NOT NULL DEFAULT 0.0,
    moderation_reasons_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES hive_posts(post_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hive_post_comments_post
ON hive_post_comments(post_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_hive_post_comments_author
ON hive_post_comments(author_agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hive_commons_promotion_candidates (
    candidate_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL UNIQUE,
    topic_id TEXT NOT NULL,
    requested_by_agent_id TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'draft',
    review_state TEXT NOT NULL DEFAULT 'pending',
    archive_state TEXT NOT NULL DEFAULT 'transient',
    requires_review INTEGER NOT NULL DEFAULT 1,
    promoted_topic_id TEXT,
    support_weight REAL NOT NULL DEFAULT 0.0,
    challenge_weight REAL NOT NULL DEFAULT 0.0,
    cite_weight REAL NOT NULL DEFAULT 0.0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    evidence_depth REAL NOT NULL DEFAULT 0.0,
    downstream_use_count INTEGER NOT NULL DEFAULT 0,
    training_signal_count INTEGER NOT NULL DEFAULT 0,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (post_id) REFERENCES hive_posts(post_id) ON DELETE CASCADE,
    FOREIGN KEY (topic_id) REFERENCES hive_topics(topic_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hive_commons_promotion_candidates_status
ON hive_commons_promotion_candidates(status, score DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS hive_commons_promotion_reviews (
    review_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    reviewer_agent_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    note TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, reviewer_agent_id),
    FOREIGN KEY (candidate_id) REFERENCES hive_commons_promotion_candidates(candidate_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_hive_commons_promotion_reviews_candidate
ON hive_commons_promotion_reviews(candidate_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS web0_workers (
    worker_id TEXT PRIMARY KEY,
    provider_ids_json TEXT NOT NULL DEFAULT '[]',
    top_tps REAL NOT NULL DEFAULT 0.0,
    top_tier TEXT NOT NULL DEFAULT 'drone',
    context_window INTEGER NOT NULL DEFAULT 32768,
    tools_json TEXT NOT NULL DEFAULT '[]',
    price_per_token_usdc REAL NOT NULL DEFAULT 0.000001,
    privacy_mode TEXT NOT NULL DEFAULT 'plain',
    announced_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web0_workers_expires_at
ON web0_workers(expires_at DESC);

-- Observe-only certification history for explicit local-model tool probes. This table is
-- deliberately disconnected from provider enablement/ranking: a diagnostic must never become a
-- silent routing or spend decision. Fingerprints make a model/backend/template change stale the
-- old evidence instead of accidentally inheriting it.
CREATE TABLE IF NOT EXISTS local_model_tool_certification_runs (
    run_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    adapter_type TEXT NOT NULL,
    state TEXT NOT NULL,
    successful INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    regression_confirmed INTEGER NOT NULL DEFAULT 0,
    fingerprint_json TEXT NOT NULL DEFAULT '{}',
    stages_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    latency_ms REAL NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    observe_only INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_local_model_tool_certification_fingerprint
ON local_model_tool_certification_runs(fingerprint, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_local_model_tool_certification_model
ON local_model_tool_certification_runs(provider_name, model_name, started_at DESC);

-- PA Contacts (core/contacts): the owner's saved people and services. Every endpoint is separately identified and
-- change-detectable (fingerprint + revision); sources and import runs record where imported entries came from;
-- suggestions read from untrusted content wait for the owner; contact_events journals every change. A deleted
-- contact leaves a tombstone without names or endpoints; receipts held by other owners (drafts, proposals) keep
-- their own snapshots and are never rewritten from here.
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'person',
    display_name TEXT NOT NULL DEFAULT '',
    name_key TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'local',
    state TEXT NOT NULL DEFAULT 'active',
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contacts_state_name
ON contacts(state, name_key);

CREATE TABLE IF NOT EXISTS contact_aliases (
    contact_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (contact_id, alias_key)
);

CREATE INDEX IF NOT EXISTS idx_contact_aliases_key
ON contact_aliases(alias_key);

CREATE TABLE IF NOT EXISTS contact_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL,
    canonical TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    provider_account TEXT NOT NULL DEFAULT '',
    chain_network TEXT NOT NULL DEFAULT '',
    chain_family TEXT NOT NULL DEFAULT '',
    chain_environment TEXT NOT NULL DEFAULT '',
    verification TEXT NOT NULL DEFAULT 'user_entered',
    source_id TEXT NOT NULL DEFAULT '',
    source_ref TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active',
    revision INTEGER NOT NULL DEFAULT 1,
    fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contact_endpoints_contact
ON contact_endpoints(contact_id, state, kind);

CREATE INDEX IF NOT EXISTS idx_contact_endpoints_identity
ON contact_endpoints(kind, canonical, state);

CREATE TABLE IF NOT EXISTS contact_sources (
    source_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    account_label TEXT NOT NULL DEFAULT '',
    account_identity TEXT NOT NULL DEFAULT '',
    auth_binding TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'connected',
    status_detail TEXT NOT NULL DEFAULT '',
    consented_at TEXT NOT NULL,
    last_read_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS contact_import_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    state TEXT NOT NULL,
    preview_json TEXT NOT NULL DEFAULT '{}',
    applied_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contact_import_runs_source
ON contact_import_runs(source_id, created_at DESC);

CREATE TABLE IF NOT EXISTS contact_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    contact_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    replaces_endpoint_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL,
    canonical TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT '',
    provider_account TEXT NOT NULL DEFAULT '',
    chain_network TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL,
    origin_ref TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contact_suggestions_state
ON contact_suggestions(state, created_at DESC);

CREATE TABLE IF NOT EXISTS contact_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    endpoint_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contact_events_contact
ON contact_events(contact_id, seq DESC);

-- Protected Contacts changes (core/contacts/authority.py, core/operator_credential): a change to a saved contact, or a new
-- name that is the same as or looks like a saved one, waits in contact_operations until the owner confirms it with the
-- operator credential. operator_credentials holds verifiers only (never a PIN or password); its attempt counters are rows,
-- so a restart does not reset them; contact_authorizations stores only the SHA-256 of a single-use authorization;
-- contact_identity_tombstones keeps keyed hashes of a deleted contact's name keys for a bounded period, never the name.
CREATE TABLE IF NOT EXISTS operator_credentials (
    credential_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    kdf TEXT NOT NULL,
    salt TEXT NOT NULL,
    verifier TEXT NOT NULL,
    recovery_kdf TEXT NOT NULL DEFAULT '',
    recovery_salt TEXT NOT NULL DEFAULT '',
    recovery_verifier TEXT NOT NULL DEFAULT '',
    generation INTEGER NOT NULL,
    scopes_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_credential_attempts (
    scope TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0,
    in_flight INTEGER NOT NULL DEFAULT 0,
    in_flight_since REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS operator_credential_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_operations (
    operation_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    source TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    requires_authentication INTEGER NOT NULL DEFAULT 1,
    reasons_json TEXT NOT NULL DEFAULT '[]',
    identity_digest TEXT NOT NULL DEFAULT '',
    policy_version TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    principal TEXT NOT NULL DEFAULT '',
    credential_generation INTEGER NOT NULL DEFAULT 0,
    state_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contact_operations_state
ON contact_operations(state, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contact_operations_digest
ON contact_operations(digest, state);

CREATE TABLE IF NOT EXISTS contact_authorizations (
    authorization_hash TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    principal TEXT NOT NULL,
    credential_generation INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'issued',
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_contact_authorizations_operation
ON contact_authorizations(operation_id, state);

CREATE TABLE IF NOT EXISTS contact_identity_tombstones (
    key_hash TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    retained_until TEXT NOT NULL,
    PRIMARY KEY (key_hash, contact_id)
);

CREATE INDEX IF NOT EXISTS idx_contact_identity_tombstones_retention
ON contact_identity_tombstones(retained_until);
"""

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

class StoreMigrationError(RuntimeError):
    """A migration step failed; the pre-update database snapshot was restored byte-exactly.

    ``code`` is the stable VOOL error code (``VOOL_E_MIGRATION_FAILED``)."""

    def __init__(self, message: str, *, code: str = "VOOL_E_MIGRATION_FAILED") -> None:
        super().__init__(message)
        self.code = code


def _snapshot_db_files(db_file: Path) -> list[tuple[Path, Path]]:
    """Copy the database (+ WAL/SHM sidecars) so a failed migration can restore the exact
    pre-update bytes. Returns the (snapshot, live) pairs; empty when there is nothing to guard."""
    import shutil

    if db_file is None or not db_file.exists():
        return []
    import os

    pairs: list[tuple[Path, Path]] = []
    # A per-invocation name makes the snapshot lifecycle private to the invocation that
    # created it: with one shared ".pre-migration" name, the finisher's discard unlinked
    # the file between another thread's copyfile and copystat and the concurrent first
    # turn died as an empty-body HTTP 500 (attributed 2026-09-02 on the restart lane,
    # reproduced and attributed again 2026-09-03 on concurrent /api/chat turns).
    tag = f".pre-migration-{os.getpid()}-{threading.get_ident()}"
    for suffix in ("", "-wal", "-shm"):
        live = Path(str(db_file) + suffix)
        if not live.exists():
            continue
        snap = Path(str(live) + tag)
        try:
            shutil.copy2(live, snap)
        except FileNotFoundError:
            # TOCTOU on the SIDECARS, widened by the per-use connection contract (2026-09-04):
            # every get_connection()/close() cycle can create and unlink -wal/-shm, so one can
            # vanish between the exists() above and this copy. A sidecar that is already gone
            # has nothing to roll back to -- the committed bytes live in the main db file, which
            # is snapshotted first -- so skip it rather than failing the whole migration. The
            # main db file disappearing is NOT benign and still raises.
            # copy2 is copyfile then copystat: a file that vanished after copyfile already left a
            # partial snapshot beside the store, and a snapshot outside `pairs` is never discarded
            # or restored by anyone. Remove it before skipping (or raising).
            with contextlib.suppress(OSError):
                snap.unlink()
            if suffix == "":
                raise
            continue
        pairs.append((snap, live))
    return pairs


def _restore_db_snapshot(pairs: list[tuple[Path, Path]]) -> None:
    import shutil

    for snap, live in pairs:
        shutil.copy2(snap, live)
    # sidecars of the FAILED attempt would shadow the restored bytes
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(pairs[0][1]) + suffix) if pairs else None
        if sidecar is not None and sidecar.exists():
            sidecar.unlink()


def _discard_db_snapshot(pairs: list[tuple[Path, Path]]) -> None:
    for snap, _live in pairs:
        with contextlib.suppress(OSError):
            snap.unlink()


# ------------------------------------------------------------------------------------------
# Store version ownership. The ledger is the ONE place a contract version is minted; its head
# is the version this authority stamps and the version run_migrations treats as "already
# current". storage.db.STORE_USER_VERSION (the refuse-open gate) must equal the head — asserted
# at import below — so an older stamp can never be mistaken for the current contract and skip
# the migrations that arrived after it. Versions below 3 predate the stamp (0/0 legacy).
# ------------------------------------------------------------------------------------------
STORE_VERSION_LEDGER: tuple[tuple[int, str], ...] = (
    (3, "K-03 store version gate: application_id + user_version stamped last; downgrade refuse-open"),
    (
        4,
        "M2 upgrade authority: LEGACY_SHAPE_STEPS ordered before SCHEMA_SQL, snapshot rollback, "
        "versioned no-op guard. Re-applies every object added after the version-3 stamp "
        "(obligation_sets, semantic_admissions, runtime_attempts.attempt_role/execution_slot, ...).",
    ),
    (5, "Scheduling vertical: add durable reminder_requests and its due/session indexes to existing version-four installs."),
    (
        6,
        "PA Contacts: adds contacts, contact_aliases, contact_endpoints, contact_sources, contact_import_runs, "
        "contact_suggestions and contact_events.",
    ),
    (
        7,
        "Protected Contacts changes: adds operator_credentials, operator_credential_attempts, operator_credential_events, "
        "contact_operations, contact_authorizations and contact_identity_tombstones.",
    ),
    (
        8,
        "Calendar alerts and notification centre (renumbered from the calendar lane's own v6 by the "
        "release integration -- this base's v6/v7 are the contacts and protected-change migrations): "
        "reminder_requests gains source_kind/source_ref/schedule_key/payload_json/snooze_count/"
        "fire_generation; adds calendar_accounts, calendar_selections, calendar_event_projections, "
        "notification_items, notification_deliveries, notification_preferences and "
        "native_notification_requests.",
    ),
)


class StoreVersionOwnershipError(RuntimeError):
    """The gate constant and the migration ledger disagree — refuse to load."""

    code = "VOOL_E_STORE_VERSION_OWNERSHIP"


def ledger_head_version() -> int:
    return int(STORE_VERSION_LEDGER[-1][0])


def assert_store_version_ownership() -> None:
    import storage.db as _store_db

    versions = [int(version) for version, _note in STORE_VERSION_LEDGER]
    if versions != sorted(set(versions)):
        raise StoreVersionOwnershipError(f"STORE_VERSION_LEDGER is not strictly increasing: {versions}")
    gate = int(_store_db.STORE_USER_VERSION)
    if versions[-1] != gate:
        raise StoreVersionOwnershipError(
            f"storage.db.STORE_USER_VERSION={gate} != STORE_VERSION_LEDGER head {versions[-1]}: "
            "a migration contract was minted on one side only"
        )


assert_store_version_ownership()


def _read_store_version(conn) -> int:
    try:
        row = conn.execute("PRAGMA user_version;").fetchone()
        return int(row[0]) if row is not None else 0
    except sqlite3.DatabaseError:
        return 0


_RUN_MIGRATIONS_LOCK = threading.Lock()


def run_migrations(db_path=None, *, force: bool = False) -> None:
    from storage.db import _resolve_db_path, active_default_db_path

    # Freeze the connection authority's concrete target once. The import-time default
    # is a sentinel, never a source-tree file to snapshot or restore.
    raw_path = _resolve_db_path(db_path) if db_path is not None else active_default_db_path()
    db_file = Path(str(raw_path))
    # One migration at a time per process: two concurrent first turns both used to
    # snapshot/migrate/discard side by side and raced each other's snapshot files.
    # And one at a time per STORE across processes: the in-process lock above is invisible to a
    # second interpreter, and two processes migrating one fresh store raced ALTERs; the loser
    # restored its pre-migration byte snapshot over the winner's committed work and unlinked the
    # WAL under every other connection (measured: committed rows rewound, SIGBUS, "file is not a
    # database" -- tests/test_run_migrations_cross_process.py).
    with _RUN_MIGRATIONS_LOCK:
        if not force and _store_is_current(db_file):
            return  # already at this binary's contract: no lock, no snapshot, nothing written
        with _cross_process_migration_lock(db_file):
            _run_migrations_locked(db_file, db_path=str(db_file), force=force)


# Wall clock, not a cost guess: how long one process's first use of a store waits while ANOTHER
# process migrates that same store. Past it the migration is refused before any snapshot or write,
# and the next use of the store tries again.
MIGRATION_LOCK_WAIT_SECONDS = 300.0
_MIGRATION_LOCK_FIRST_BACKOFF_SECONDS = 0.005
_MIGRATION_LOCK_MAX_BACKOFF_SECONDS = 0.25


@contextlib.contextmanager
def _cross_process_migration_lock(db_file: Path):
    """Exclusive per store, across processes, for the whole migration, through the ONE fail-closed
    lock authority (`core.cross_process_lock`: flock on POSIX, msvcrt on Windows). The authority
    never waits and never yields unlocked, so contention is retried with backoff up to
    MIGRATION_LOCK_WAIT_SECONDS and then refused as a typed StoreMigrationError. The lock file is its
    own file beside the store, never one of SQLite's locks, so releasing it cannot disturb a
    database connection."""
    import time

    from core.cross_process_lock import LockUnavailable, PublicationLock

    lock_path = str(db_file) + ".migration.lock"
    deadline = time.monotonic() + MIGRATION_LOCK_WAIT_SECONDS
    backoff = _MIGRATION_LOCK_FIRST_BACKOFF_SECONDS
    with contextlib.ExitStack() as held:
        while True:
            try:
                held.enter_context(PublicationLock(lock_path))
                break
            except LockUnavailable as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise StoreMigrationError(
                        "storage migration did not start: the store's migration lock stayed unavailable for "
                        f"{MIGRATION_LOCK_WAIT_SECONDS:g}s ({exc}); the store was not touched",
                        code="VOOL_E_MIGRATION_LOCK_UNAVAILABLE",
                    ) from exc
                time.sleep(min(backoff, remaining))
                backoff = min(backoff * 2, _MIGRATION_LOCK_MAX_BACKOFF_SECONDS)
        yield


def _store_is_current(db_file: Path) -> bool:
    """True only when the store is already stamped at this binary's contract. Read before any
    snapshot: the byte snapshot exists to undo a failed UPGRADE, and copying a current store's
    live bytes -- restorable over writes made after the copy -- guards nothing.

    The probe must not change a byte of the store: a pristine store that is NOT current is about
    to be snapshotted, and that snapshot is what a failed upgrade restores. So it is a raw
    read-only open that reads `user_version` and nothing else -- not `get_connection`, which
    converts the journal mode and would move the pre-update bytes before they are captured. Any
    doubt answers False, which is exactly the snapshot-first path this always took."""
    if db_file is None or not Path(db_file).exists():
        return False
    try:
        uri = Path(db_file).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    except Exception:
        return False
    try:
        row = conn.execute("PRAGMA user_version;").fetchone()
        return row is not None and int(row[0]) == ledger_head_version()
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _run_migrations_locked(db_file: Path, *, db_path=None, force: bool = False) -> None:
    if not force and _store_is_current(db_file):
        return  # already at this binary's contract: versioned no-op, nothing copied
    snapshot = _snapshot_db_files(db_file)
    conn = None
    try:
        conn = get_connection(db_path) if db_path is not None else get_connection()
        if not force and _read_store_version(conn) == ledger_head_version():
            return  # already at this binary's contract: versioned no-op
        _preflight_legacy_schema_sql_tables(conn)
        # M2 boot-order law: the legacy-shape steps own every column SCHEMA_SQL's DDL depends
        # on. They run BEFORE the schema script — never after it.
        run_legacy_shape_steps(conn)
        conn.executescript(SCHEMA_SQL)

        # Dynamic patches for existing tables:
        _add_column_if_missing(conn, "agent_capabilities", "host_group_hint_hash", "TEXT")
        _add_column_if_missing(conn, "agent_capabilities", "compute_class", "TEXT NOT NULL DEFAULT 'cpu_basic'")
        _add_column_if_missing(conn, "agent_capabilities", "supported_models_json", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "agent_capabilities", "self_reported_trust", "REAL NOT NULL DEFAULT 0.5")
        _add_column_if_missing(conn, "task_offers", "claimed_by", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "task_claims", "host_group_hint_hash", "TEXT")
        _add_column_if_missing(conn, "task_results", "result_hash", "TEXT")
        _add_column_if_missing(conn, "capability_tokens", "granted_to", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "capability_tokens", "task_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "capability_tokens", "signature", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "capability_tokens", "status", "TEXT NOT NULL DEFAULT 'active'")
        _add_column_if_missing(conn, "capability_tokens", "updated_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "capability_tokens", "used_at", "TEXT")
        _add_column_if_missing(conn, "capability_tokens", "revoked_at", "TEXT")
        _add_column_if_missing(conn, "contribution_ledger", "parent_host_group_hint_hash", "TEXT")
        _add_column_if_missing(conn, "contribution_ledger", "helper_host_group_hint_hash", "TEXT")
        _add_column_if_missing(conn, "contribution_ledger", "compute_credits_pending", "REAL NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "contribution_ledger", "compute_credits_released", "REAL NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "contribution_ledger", "finality_state", "TEXT NOT NULL DEFAULT 'pending'")
        _add_column_if_missing(conn, "contribution_ledger", "finality_depth", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "contribution_ledger", "finality_target", "INTEGER NOT NULL DEFAULT 2")
        _add_column_if_missing(conn, "contribution_ledger", "confirmed_at", "TEXT")
        _add_column_if_missing(conn, "contribution_ledger", "finalized_at", "TEXT")
        _add_column_if_missing(conn, "compute_credit_ledger", "receipt_hash", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "runtime_attempt_subtasks", "carried_forward_from_attempt_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "runtime_attempt_subtasks", "carried_forward_from_generation", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "runtime_attempt_subtasks", "rerun_reason", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "runtime_attempt_subtasks", "previous_result_version", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "runtime_attempts", "retry_idempotency_key", "TEXT NOT NULL DEFAULT ''")
        # ARCH-TRUTH-R1c: the attempt's role inside its turn's chain. Additive and never
        # backfilled — a row written before this column has no recorded role, and '' means
        # exactly that rather than a guess (MIGRATION_CONTRACT: no fabricated lineage).
        _add_column_if_missing(conn, "runtime_attempts", "attempt_role", "TEXT NOT NULL DEFAULT ''")
        # ARCH-TRUTH-R1d: the executing slot within a chain (see the CREATE TABLE comment).
        # Additive and never backfilled: an existing row executes the chain's ordinary slot,
        # which is exactly what '' means.
        _add_column_if_missing(conn, "runtime_attempts", "execution_slot", "TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_attempts_session_role "
            "ON runtime_attempts(session_id, attempt_role, updated_at DESC)"
        )
        _add_column_if_missing(conn, "runtime_checkpoints", "outcome_json", "TEXT NOT NULL DEFAULT '{}'")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_attempts_retry_idempotency "
            "ON runtime_attempts(retry_idempotency_key) WHERE retry_idempotency_key != ''"
        )
        # A7 W1 durable immutability: content identity columns so finalized truth
        # is hash-guarded at the engine level (CAS / immutable-first-insert).
        _add_column_if_missing(conn, "finalized_responses", "content_hash", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "runtime_checkpoints", "final_response_hash", "TEXT NOT NULL DEFAULT ''")
        # A7 W2 canonical finalization bindings: one immutable truth row per
        # admitted semantic result (partial unique index = the engine-enforced
        # different-content refusal).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS a7_finalizations (
                finalization_id TEXT PRIMARY KEY,
                semantic_result_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                canonical_content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'answer_present',
                delivery_status TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_a7_finalizations_sr "
            "ON a7_finalizations(semantic_result_id) WHERE semantic_result_id != ''"
        )
        # A8 PASS004 (NCE-F1/F2): canonical derivative-lineage registry. When a
        # governed payload transitions WITHHELD/ERASED, every surviving runtime
        # derivative (fragment text such as a streamed tail chunk sitting in
        # denormalized summary columns) is bound BY DIGEST to its governing
        # finalization here — so serve-time gates can answer "may these bytes be
        # disclosed?" by lineage identity, without re-deriving ownership from
        # fragment text or hashing guesses. Rows are additive and never mutated.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS a8_governed_derivatives (
                value_key       TEXT PRIMARY KEY,
                finalization_id TEXT NOT NULL,
                governed_hash   TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # F0-B (K-01/K-02): A0 invocation ledger + L0 execution fence.
        # Additive tables; legacy rows are never backfilled with repaired
        # identities (forward-only binding at accept/open time).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS invocation_requests (
                request_id      TEXT PRIMARY KEY,
                external_kind   TEXT NOT NULL,
                external_value  TEXT NOT NULL,
                principal       TEXT NOT NULL,
                session_binding TEXT NOT NULL DEFAULT '',
                privacy_local_only INTEGER NOT NULL DEFAULT 1,
                raw_digest      TEXT NOT NULL DEFAULT '',
                accepted_at     TEXT NOT NULL,
                state           TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_invocation_external "
            "ON invocation_requests(external_kind, external_value)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS executions (
                execution_id        TEXT PRIMARY KEY,
                request_id          TEXT NOT NULL REFERENCES invocation_requests(request_id),
                generation          INTEGER NOT NULL DEFAULT 0,
                runtime_epoch       TEXT NOT NULL DEFAULT '',
                max_generation      INTEGER NOT NULL DEFAULT 5,
                budget_remaining_calls INTEGER,
                budget_remaining_effects INTEGER,
                state               TEXT NOT NULL DEFAULT 'ACTIVE'
            )
            """
        )
        # ONE LIVE ATTEMPT: execution_id aliases the retry-chain root; the
        # partial index excludes legacy rows ('') so dirty data cannot abort
        # this staged step, and only new writers claim liveness. Predicate is
        # RUNNING-only: the legacy retry lane legally mints generation N+1
        # while the parent row is still RECEIVED/PLANNED (carried-forward
        # subtasks), so covering those states requires the attempt-lifecycle
        # promotion step first (recorded as an addendum in UNRESOLVED.md).
        _add_column_if_missing(conn, "runtime_attempts", "execution_id", "TEXT NOT NULL DEFAULT ''")
        conn.execute("DROP INDEX IF EXISTS ux_one_live_attempt")
        # ARCH-TRUTH-R1c: exclusivity now distinguishes a chain's CONTAINER row from its
        # EXECUTING rows. One external turn is one chain (`execution_id` still aliases the
        # chain root), and the turn door's own row -- role `turn_root` -- is live for the
        # whole turn by construction. Keyed on execution_id alone, that container made
        # every lane attempt of its own turn collide with it, so the answering lane was
        # refused its claim and the turn served "could not be retrieved".
        #
        # The invariant is NOT relaxed: the key gains one bit that separates the container
        # from the executors, so BOTH still hold -- at most one live executing attempt per
        # chain (what INV-2 was written for: two workers must never race one execution),
        # and at most one live root per chain. Every row that satisfied the old index
        # satisfies this one, so the rebuild cannot fail on existing data.
        #
        # ARCH-TRUTH-R1d adds the SLOT to the same key. A planned turn runs its independent
        # tasks as a concurrent wave (`run_plan`), and each task is its own unit of work on
        # the turn's chain -- keyed on the chain alone, the second task of a turn was
        # refused its claim by the database. Exclusivity is per (chain, container-bit,
        # slot): distinct tasks run at once, the SAME task still cannot execute twice, and
        # every row written before this column carries slot '' and is unaffected.
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_one_live_attempt
            ON runtime_attempts(
                execution_id,
                (CASE WHEN attempt_role = 'turn_root' THEN 1 ELSE 0 END),
                execution_slot
            )
            WHERE execution_id != ''
              AND lifecycle_state IN ('PLANNED','RUNNING')
            """
        )
        _add_column_if_missing(
            conn, "a7_finalizations", "delivery_status", "TEXT NOT NULL DEFAULT 'NOT_ATTEMPTED'"
        )
        # K-09 payload/finality separation (additive; lands while inline shape
        # has few external dependents). Plaintext is NEVER copied into
        # payload_ref by migration (MIGRATION_CONTRACT law 8).
        _add_column_if_missing(conn, "a7_finalizations", "payload_ref", "TEXT")
        _add_column_if_missing(
            conn, "a7_finalizations", "availability", "TEXT NOT NULL DEFAULT 'AVAILABLE'"
        )
        _add_column_if_missing(conn, "a7_finalizations", "request_id", "TEXT NOT NULL DEFAULT ''")
        # PASS003 (CE13): request identity must be UNIQUE across finalizations.
        # Newest-wins lookups over duplicates allowed attacker-substituted
        # content to be served as owner-committed truth. Canonical uniqueness
        # prevents invalid duplicates going forward; any legacy ambiguity that
        # blocks the index keeps the read path fail-closed (the replay lookup
        # refuses ambiguous request ids explicitly). '' request ids are
        # excluded — they carry no external identity claim.
        with contextlib.suppress(Exception):
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_a7_finalizations_request_id
                ON a7_finalizations(request_id)
                WHERE request_id != ''
                """
            )
        _add_column_if_missing(
            conn, "a7_finalizations", "terminal_reason", "TEXT NOT NULL DEFAULT ''"
        )
        # MIGRATION_CONTRACT law 7: legacy delivery marks import as
        # evidence-degraded, never as proven recipient-class truth. Rows whose
        # DELIVERED predates the evidence-class vocabulary are stamped
        # LEGACY_UNVERIFIED once; new reconciled marks carry RECONCILED.
        _add_column_if_missing(
            conn, "a7_finalizations", "delivery_evidence_class", "TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            """
            UPDATE a7_finalizations SET delivery_evidence_class = 'LEGACY_UNVERIFIED'
            WHERE delivery_status = 'DELIVERED' AND delivery_evidence_class = ''
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS a7_governance_events (
                event_id        TEXT PRIMARY KEY,
                finalization_id TEXT NOT NULL,
                event_kind      TEXT NOT NULL,
                previous_state  TEXT NOT NULL DEFAULT '',
                new_state       TEXT NOT NULL DEFAULT '',
                reason          TEXT NOT NULL DEFAULT '',
                actor           TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_a7_governance_fid "
            "ON a7_governance_events(finalization_id, created_at)"
        )
        # A8 pass-001: erasure-traversal ledger extension — per-store sweep
        # outcomes live in the ONE governance event table (no second ledger).
        _add_column_if_missing(conn, "a7_governance_events", "store_name", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "a7_governance_events", "sweep_outcome", "TEXT NOT NULL DEFAULT ''")
        # A8 pass-001: payload lineage on the legacy final-truth lane and on
        # derived useful outputs (additive; legacy rows keep '' — never
        # backfilled with fabricated lineage).
        _add_column_if_missing(conn, "finalized_responses", "finalization_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "finalized_responses", "request_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "useful_outputs", "finalization_id", "TEXT NOT NULL DEFAULT ''")
        # A8 law 11 (honest legacy): availability rows that predate this
        # migration window are stamped LEGACY_UNKNOWN ONCE — truthful
        # uncertainty, mirroring the delivery_evidence_class counterpattern
        # above. Never silently governed-as-AVAILABLE by a schema default.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS a8_migration_markers (
                marker_key TEXT PRIMARY KEY,
                stamped_at TEXT NOT NULL
            )
            """
        )
        ensure_operator_profile_tables(conn)
        marker = conn.execute(
            "SELECT 1 FROM a8_migration_markers WHERE marker_key = 'availability_legacy_unknown'"
        ).fetchone()
        if marker is None:
            conn.execute(
                "UPDATE a7_finalizations SET availability = 'LEGACY_UNKNOWN' "
                "WHERE availability = 'AVAILABLE'"
            )
            conn.execute(
                "INSERT INTO a8_migration_markers (marker_key, stamped_at) "
                "VALUES ('availability_legacy_unknown', ?)",
                (conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0],),
            )
        # K-04 (F0-C): durable A2 admission referent. Thin six-column
        # insert-once representation (D5). request_id correlation is fed from
        # A0 where bound; no legacy backfill (forward-only identity).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_admissions (
                sr_id                  TEXT PRIMARY KEY,
                request_id             TEXT NOT NULL DEFAULT '',
                admitted_at            TEXT NOT NULL,
                source_class           TEXT NOT NULL,
                obligation_set_version TEXT NOT NULL DEFAULT '',
                accepted               INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        # F0-D (K-05 producer): durable obligation sets. JSON snapshot, not
        # normalized rows; dispositions mutate the snapshot forward-only.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS obligation_sets (
                set_id       TEXT NOT NULL,
                version      TEXT NOT NULL,
                status       TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                closure_hash TEXT,
                created_at   TEXT NOT NULL,
                UNIQUE(set_id, version)
            )
            """
        )
        _add_column_if_missing(conn, "task_capsules", "parent_task_ref", "TEXT")
        _add_column_if_missing(conn, "task_capsules", "verification_of_task_id", "TEXT")
        _add_column_if_missing(conn, "finalized_responses", "anchored_signature", "TEXT")
        _add_column_if_missing(conn, "task_assignments", "capability_token_id", "TEXT")
        _add_column_if_missing(conn, "task_assignments", "lease_expires_at", "TEXT")
        _add_column_if_missing(conn, "task_assignments", "last_progress_state", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "task_assignments", "last_progress_note", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_verified_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "verification_kind", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_count", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "peer_endpoints", "proof_message_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_message_type", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_hash", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_timestamp", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_delivery_attempt_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_delivery_success_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_delivery_failure_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "consecutive_delivery_failures", "INTEGER NOT NULL DEFAULT 0")
        _rebuild_peer_endpoints_if_needed(conn)
        conn.execute(
            """
            UPDATE peer_endpoints
            SET proof_timestamp = COALESCE(
                NULLIF((
                    SELECT MAX(COALESCE(NULLIF(obs.proof_timestamp, ''), NULLIF(obs.last_verified_at, '')))
                    FROM peer_endpoint_observations AS obs
                    WHERE obs.peer_id = peer_endpoints.peer_id
                      AND obs.host = peer_endpoints.host
                      AND obs.port = peer_endpoints.port
                ), ''),
                NULLIF(last_verified_at, ''),
                NULLIF(last_seen_at, ''),
                ''
            )
            WHERE COALESCE(proof_timestamp, '') = ''
            """
        )
        _add_column_if_missing(conn, "peer_endpoint_candidates", "last_probe_attempt_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoint_candidates", "last_probe_delivery_ok", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "peer_endpoint_candidates", "consecutive_probe_failures", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "adaptation_corpora", "quality_score", "REAL NOT NULL DEFAULT 0.0")
        _add_column_if_missing(conn, "adaptation_corpora", "quality_details_json", "TEXT NOT NULL DEFAULT '{}'")
        _add_column_if_missing(conn, "adaptation_corpora", "content_hash", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "adaptation_corpora", "last_scored_at", "TEXT")
        _add_column_if_missing(conn, "adaptation_jobs", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
        _add_column_if_missing(conn, "adaptation_jobs", "rolled_back_at", "TEXT")
        _add_column_if_missing(conn, "useful_outputs", "summary", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "task_assignments", "progress_updated_at", "TEXT")
        _add_column_if_missing(conn, "task_assignments", "completed_at", "TEXT")
        _add_column_if_missing(conn, "model_provider_manifests", "runtime_dependency", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "local_tasks", "session_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "local_tasks", "share_scope", "TEXT NOT NULL DEFAULT 'local_only'")
        _add_column_if_missing(conn, "learning_shards", "origin_task_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "learning_shards", "origin_session_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "learning_shards", "share_scope", "TEXT NOT NULL DEFAULT 'local_only'")
        _add_column_if_missing(conn, "learning_shards", "restricted_terms_json", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "session_hive_watch_state", "seen_curiosity_topic_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "session_hive_watch_state", "seen_curiosity_run_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "session_hive_watch_state", "seen_agent_ids_json", "TEXT NOT NULL DEFAULT '[]'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_capsules_parent_ref ON task_capsules(parent_task_ref)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_capsules_verification_of ON task_capsules(verification_of_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_tokens_task_status ON capability_tokens(task_id, status, expires_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_assignments_helper_status ON task_assignments(helper_peer_id, status, updated_at DESC)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_contribution_ledger_finality "
            "ON contribution_ledger(finality_state, helper_peer_id, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_provider_manifests_enabled "
            "ON model_provider_manifests(enabled, provider_name, model_name)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_shards_session_scope ON learning_shards(origin_session_id, share_scope, updated_at DESC)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS dispatch_credit_escrow (
                escrow_id TEXT PRIMARY KEY,
                parent_task_id TEXT NOT NULL,
                poster_peer_id TEXT NOT NULL,
                total_escrowed REAL NOT NULL,
                total_released REAL NOT NULL DEFAULT 0,
                total_refunded REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_escrow_task ON dispatch_credit_escrow(parent_task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_escrow_poster ON dispatch_credit_escrow(poster_peer_id, status)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS voolbook_profiles (
                peer_id         TEXT PRIMARY KEY,
                handle          TEXT NOT NULL UNIQUE,
                canonical_handle TEXT NOT NULL UNIQUE,
                display_name    TEXT NOT NULL,
                bio             TEXT NOT NULL DEFAULT '',
                avatar_seed     TEXT NOT NULL DEFAULT '',
                profile_url     TEXT NOT NULL DEFAULT '',
                post_count      INTEGER NOT NULL DEFAULT 0,
                claim_count     INTEGER NOT NULL DEFAULT 0,
                glory_score     REAL NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'active',
                joined_at       TEXT NOT NULL,
                last_active_at  TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_profiles_handle ON voolbook_profiles(canonical_handle)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_profiles_status ON voolbook_profiles(status, last_active_at DESC)")

        with contextlib.suppress(Exception):
            conn.execute("ALTER TABLE voolbook_profiles ADD COLUMN twitter_handle TEXT NOT NULL DEFAULT ''")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS voolbook_tokens (
                token_id    TEXT PRIMARY KEY,
                peer_id     TEXT NOT NULL,
                token_hash  TEXT NOT NULL UNIQUE,
                scope       TEXT NOT NULL DEFAULT 'post,profile',
                status      TEXT NOT NULL DEFAULT 'active',
                issued_at   TEXT NOT NULL,
                expires_at  TEXT,
                last_used_at TEXT,
                revoked_at  TEXT,
                FOREIGN KEY (peer_id) REFERENCES voolbook_profiles(peer_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_tokens_hash ON voolbook_tokens(token_hash, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_tokens_peer ON voolbook_tokens(peer_id, status)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS voolbook_posts (
                post_id         TEXT PRIMARY KEY,
                peer_id         TEXT NOT NULL,
                handle          TEXT NOT NULL,
                content         TEXT NOT NULL,
                post_type       TEXT NOT NULL DEFAULT 'social',
                origin_kind     TEXT NOT NULL DEFAULT 'human',
                origin_channel  TEXT NOT NULL DEFAULT 'voolbook_token',
                origin_peer_id  TEXT NOT NULL DEFAULT '',
                parent_post_id  TEXT,
                hive_post_id    TEXT,
                topic_id        TEXT,
                link_url        TEXT NOT NULL DEFAULT '',
                link_title      TEXT NOT NULL DEFAULT '',
                upvotes         INTEGER NOT NULL DEFAULT 0,
                reply_count     INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'active',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                FOREIGN KEY (peer_id) REFERENCES voolbook_profiles(peer_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_created ON voolbook_posts(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_peer ON voolbook_posts(peer_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_handle ON voolbook_posts(handle)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_parent ON voolbook_posts(parent_post_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nb_posts_status ON voolbook_posts(status, created_at DESC)")
        _add_column_if_missing(conn, "voolbook_posts", "origin_kind", "TEXT NOT NULL DEFAULT 'human'")
        _add_column_if_missing(conn, "voolbook_posts", "origin_channel", "TEXT NOT NULL DEFAULT 'voolbook_token'")
        _add_column_if_missing(conn, "voolbook_posts", "origin_peer_id", "TEXT NOT NULL DEFAULT ''")

        exists = conn.execute(
            "SELECT 1 FROM persona_profiles WHERE persona_id = ? LIMIT 1",
            ("default",),
        ).fetchone()

        if not exists:
            now = _utcnow()
            conn.execute(
                """
                INSERT INTO persona_profiles (
                    persona_id, display_name, spirit_anchor, tone, verbosity,
                    risk_tolerance, explanation_depth, execution_style, strictness,
                    personality_locked, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "default",
                    "VOOL",
                    "VOOL is a local-first, sharp but protective intelligence. "
                    "She helps without surrendering the user's safety or identity.",
                    "direct",
                    "medium",
                    0.25,
                    0.75,
                    "advice_first",
                    0.85,
                    1,
                    now,
                    now,
                ),
            )

        conn.commit()

        # K-03 migration law: version stamp LAST. All schema/data changes above
        # are idempotent and rerun safely if a crash precedes this stamp.
        conn.execute(f"PRAGMA application_id = {STORE_APPLICATION_ID};")
        conn.execute(f"PRAGMA user_version = {ledger_head_version()};")
        conn.commit()
    except BaseException as exc:
        # M2 migration law: any failure — at any boundary — restores the pre-update database
        # bytes exactly, so the old app and old data stay coherent (updater rollback story).
        if conn is not None:
            conn.close()
            conn = None
        _restore_db_snapshot(snapshot)
        if isinstance(exc, StoreVersionError):
            raise
        raise StoreMigrationError(
            f"storage migration failed; database restored to its pre-update state: {exc}"
        ) from exc
    finally:
        if conn is not None:
            conn.close()
        _discard_db_snapshot(snapshot)



OPERATOR_PROFILE_TABLES = ("operator_profile_items", "operator_profile_history", "operator_profile_state")


def ensure_operator_profile_tables(conn) -> None:
    """Idempotent DDL for the Operator Profile store (P1).

    Called from ``run_migrations`` and, defensively, from ``core.operator_profile`` on
    first use of a connection — a fresh runtime home that has not run migrations yet
    (a scratch daemon, a focused test) must still find the ONE store, not a second one.
    """
    # Operator Profile (P1): the ONE typed store for what VOOL remembers
    # about the operator. Items are revisioned (CAS on `revision`),
    # historied (every change appends a row), tombstoned on forget
    # (status='deleted', never a physical delete while history refers to
    # it) and A8-lineaged (`request_id` binds the source turn). Governed
    # by the same availability predicate every other derivative store
    # consults (core.finalization.writer_may_persist_text).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_profile_items (
            item_id TEXT PRIMARY KEY,
            principal TEXT NOT NULL,
            category TEXT NOT NULL,
            value_json TEXT NOT NULL,
            value_text TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL DEFAULT 'global',
            scope_key TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT 'explicit',
            confidence REAL NOT NULL DEFAULT 1.0,
            sensitivity TEXT NOT NULL DEFAULT 'personal',
            status TEXT NOT NULL DEFAULT 'active',
            source_session_id TEXT NOT NULL DEFAULT '',
            source_turn_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            conflict_with TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operator_profile_items_principal "
        "ON operator_profile_items(principal, status, category)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_profile_history (
            history_id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            principal TEXT NOT NULL,
            revision INTEGER NOT NULL,
            action TEXT NOT NULL,
            previous_json TEXT NOT NULL DEFAULT '',
            next_json TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_operator_profile_history_item "
        "ON operator_profile_history(item_id, revision)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operator_profile_state (
            principal TEXT PRIMARY KEY,
            memory_paused INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )

def _table_exists(conn, table: str) -> bool:
    _SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if not _SAFE_IDENT.match(table):
        raise ValueError(f"Unsafe SQL identifier: table={table!r}")
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _preflight_legacy_schema_sql_tables(conn) -> None:
    """Patch very old local DB shapes before SCHEMA_SQL adds dependent indexes.

    Some long-lived local runtime homes still carry a `peer_endpoints` table from
    before the verification/proof columns existed. SCHEMA_SQL now creates indexes
    over those columns, so the bootstrap must add the missing columns first or the
    schema script dies before the normal dynamic patch/rebuild path can run.
    """

    if _table_exists(conn, "peer_endpoints"):
        _add_column_if_missing(conn, "peer_endpoints", "last_verified_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "verification_kind", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_count", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "peer_endpoints", "proof_message_id", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_message_type", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_hash", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "proof_timestamp", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_delivery_attempt_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_delivery_success_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "last_delivery_failure_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoints", "consecutive_delivery_failures", "INTEGER NOT NULL DEFAULT 0")
    if _table_exists(conn, "peer_endpoint_observations"):
        _add_column_if_missing(conn, "peer_endpoint_observations", "proof_signature", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoint_observations", "proof_timestamp", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoint_observations", "first_verified_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoint_observations", "last_verified_at", "TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "peer_endpoint_observations", "proof_count", "INTEGER NOT NULL DEFAULT 0")

# M2 upgrade authority (2026-09-02): the ONE ordered list of legacy-shape column additions.
# Every entry runs BEFORE SCHEMA_SQL so the schema script's dependent indexes can never query a
# column the authority has not added yet (the "no such column: attempt_role" boot defect). The
# list is table-existence-safe: SCHEMA_SQL itself creates complete tables for fresh installs.
LEGACY_SHAPE_STEPS: tuple[tuple[str, str, str], ...] = (
    ("agent_capabilities", "host_group_hint_hash", "TEXT"),
    ("agent_capabilities", "compute_class", "TEXT NOT NULL DEFAULT 'cpu_basic'"),
    ("agent_capabilities", "supported_models_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("agent_capabilities", "self_reported_trust", "REAL NOT NULL DEFAULT 0.5"),
    ("task_offers", "claimed_by", "TEXT NOT NULL DEFAULT ''"),
    ("task_claims", "host_group_hint_hash", "TEXT"),
    ("task_results", "result_hash", "TEXT"),
    ("capability_tokens", "granted_to", "TEXT NOT NULL DEFAULT ''"),
    ("capability_tokens", "task_id", "TEXT NOT NULL DEFAULT ''"),
    ("capability_tokens", "signature", "TEXT NOT NULL DEFAULT ''"),
    ("capability_tokens", "status", "TEXT NOT NULL DEFAULT 'active'"),
    ("capability_tokens", "updated_at", "TEXT NOT NULL DEFAULT ''"),
    ("capability_tokens", "used_at", "TEXT"),
    ("capability_tokens", "revoked_at", "TEXT"),
    ("contribution_ledger", "parent_host_group_hint_hash", "TEXT"),
    ("contribution_ledger", "helper_host_group_hint_hash", "TEXT"),
    ("contribution_ledger", "compute_credits_pending", "REAL NOT NULL DEFAULT 0"),
    ("contribution_ledger", "compute_credits_released", "REAL NOT NULL DEFAULT 0"),
    ("contribution_ledger", "finality_state", "TEXT NOT NULL DEFAULT 'pending'"),
    ("contribution_ledger", "finality_depth", "INTEGER NOT NULL DEFAULT 0"),
    ("contribution_ledger", "finality_target", "INTEGER NOT NULL DEFAULT 2"),
    ("contribution_ledger", "confirmed_at", "TEXT"),
    ("contribution_ledger", "finalized_at", "TEXT"),
    ("compute_credit_ledger", "receipt_hash", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_attempt_subtasks", "carried_forward_from_attempt_id", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_attempt_subtasks", "carried_forward_from_generation", "INTEGER NOT NULL DEFAULT 0"),
    ("runtime_attempt_subtasks", "rerun_reason", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_attempt_subtasks", "previous_result_version", "INTEGER NOT NULL DEFAULT 0"),
    ("runtime_attempts", "retry_idempotency_key", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_attempts", "attempt_role", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_attempts", "execution_slot", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_checkpoints", "outcome_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("finalized_responses", "content_hash", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_checkpoints", "final_response_hash", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_attempts", "execution_id", "TEXT NOT NULL DEFAULT ''"),
    ("a7_finalizations", "payload_ref", "TEXT"),
    ("a7_finalizations", "request_id", "TEXT NOT NULL DEFAULT ''"),
    ("a7_governance_events", "store_name", "TEXT NOT NULL DEFAULT ''"),
    ("a7_governance_events", "sweep_outcome", "TEXT NOT NULL DEFAULT ''"),
    ("finalized_responses", "finalization_id", "TEXT NOT NULL DEFAULT ''"),
    ("finalized_responses", "request_id", "TEXT NOT NULL DEFAULT ''"),
    ("useful_outputs", "finalization_id", "TEXT NOT NULL DEFAULT ''"),
    ("task_capsules", "parent_task_ref", "TEXT"),
    ("task_capsules", "verification_of_task_id", "TEXT"),
    ("finalized_responses", "anchored_signature", "TEXT"),
    ("task_assignments", "capability_token_id", "TEXT"),
    ("task_assignments", "lease_expires_at", "TEXT"),
    ("task_assignments", "last_progress_state", "TEXT NOT NULL DEFAULT ''"),
    ("task_assignments", "last_progress_note", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_verified_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "verification_kind", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_count", "INTEGER NOT NULL DEFAULT 0"),
    ("peer_endpoints", "proof_message_id", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_message_type", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_hash", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_timestamp", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_delivery_attempt_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_delivery_success_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_delivery_failure_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "consecutive_delivery_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("peer_endpoint_candidates", "last_probe_attempt_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoint_candidates", "last_probe_delivery_ok", "INTEGER NOT NULL DEFAULT 0"),
    ("peer_endpoint_candidates", "consecutive_probe_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("adaptation_corpora", "quality_score", "REAL NOT NULL DEFAULT 0.0"),
    ("adaptation_corpora", "quality_details_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("adaptation_corpora", "content_hash", "TEXT NOT NULL DEFAULT ''"),
    ("adaptation_corpora", "last_scored_at", "TEXT"),
    ("adaptation_jobs", "metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("adaptation_jobs", "rolled_back_at", "TEXT"),
    ("useful_outputs", "summary", "TEXT NOT NULL DEFAULT ''"),
    ("task_assignments", "progress_updated_at", "TEXT"),
    ("task_assignments", "completed_at", "TEXT"),
    ("model_provider_manifests", "runtime_dependency", "TEXT NOT NULL DEFAULT ''"),
    ("local_tasks", "session_id", "TEXT NOT NULL DEFAULT ''"),
    ("local_tasks", "share_scope", "TEXT NOT NULL DEFAULT 'local_only'"),
    ("learning_shards", "origin_task_id", "TEXT NOT NULL DEFAULT ''"),
    ("learning_shards", "origin_session_id", "TEXT NOT NULL DEFAULT ''"),
    ("learning_shards", "share_scope", "TEXT NOT NULL DEFAULT 'local_only'"),
    ("learning_shards", "restricted_terms_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("session_hive_watch_state", "seen_curiosity_topic_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("session_hive_watch_state", "seen_curiosity_run_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("session_hive_watch_state", "seen_agent_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("voolbook_posts", "origin_kind", "TEXT NOT NULL DEFAULT 'human'"),
    ("voolbook_posts", "origin_channel", "TEXT NOT NULL DEFAULT 'voolbook_token'"),
    ("voolbook_posts", "origin_peer_id", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_verified_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "verification_kind", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_count", "INTEGER NOT NULL DEFAULT 0"),
    ("peer_endpoints", "proof_message_id", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_message_type", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_hash", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "proof_timestamp", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_delivery_attempt_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_delivery_success_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "last_delivery_failure_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoints", "consecutive_delivery_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("peer_endpoint_observations", "proof_signature", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoint_observations", "proof_timestamp", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoint_observations", "first_verified_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoint_observations", "last_verified_at", "TEXT NOT NULL DEFAULT ''"),
    ("peer_endpoint_observations", "proof_count", "INTEGER NOT NULL DEFAULT 0"),
    ("reminder_requests", "source_kind", "TEXT NOT NULL DEFAULT 'reminder'"),
    ("reminder_requests", "source_ref", "TEXT NOT NULL DEFAULT ''"),
    ("reminder_requests", "schedule_key", "TEXT NOT NULL DEFAULT ''"),
    ("reminder_requests", "payload_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("reminder_requests", "snooze_count", "INTEGER NOT NULL DEFAULT 0"),
    ("reminder_requests", "fire_generation", "INTEGER NOT NULL DEFAULT 0"),
)
LEGACY_SHAPE_STEP_IDS = frozenset((table, column) for table, column, _ in LEGACY_SHAPE_STEPS)


def run_legacy_shape_steps(conn) -> None:
    """Bring every PRE-EXISTING table up to the column set SCHEMA_SQL's DDL depends on.
    Tables that do not exist yet are skipped: SCHEMA_SQL creates them complete."""
    for table, column, type_def in LEGACY_SHAPE_STEPS:
        _add_column_if_missing(conn, table, column, type_def)


def _add_column_if_missing(conn, table: str, column: str, type_def: str) -> None:
    _SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if not _SAFE_IDENT.match(table) or not _SAFE_IDENT.match(column):
        raise ValueError(f"Unsafe SQL identifier: table={table!r} column={column!r}")
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row["name"] for row in cursor.fetchall()]
    if not columns:
        return  # table does not exist on this shape; SCHEMA_SQL creates it complete
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}")


def _peer_endpoints_requires_rebuild(conn) -> bool:
    rows = conn.execute("PRAGMA table_info(peer_endpoints)").fetchall()
    if not rows:
        return False
    pk_columns = [item["name"] for item in sorted(rows, key=lambda item: int(item["pk"] or 0)) if int(item["pk"] or 0)]
    return pk_columns != ["peer_id", "host", "port"]


def _endpoint_source_priority(value: str) -> int:
    normalized = str(value or "").strip().lower()
    return {
        "self": 500,
        "api": 450,
        "observed": 400,
        "bootstrap": 300,
        "advertised": 200,
        "dht": 100,
        "block_found": 90,
    }.get(normalized, 0)


def _max_timestamp(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return right if right >= left else left


def _rebuild_peer_endpoints_if_needed(conn) -> None:
    if not _peer_endpoints_requires_rebuild(conn):
        return

    conn.execute("ALTER TABLE peer_endpoints RENAME TO peer_endpoints_legacy")
    conn.execute(
        """
        CREATE TABLE peer_endpoints (
            peer_id TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'direct',
            last_seen_at TEXT NOT NULL,
            last_verified_at TEXT NOT NULL DEFAULT '',
            verification_kind TEXT NOT NULL DEFAULT '',
            proof_count INTEGER NOT NULL DEFAULT 0,
            proof_message_id TEXT NOT NULL DEFAULT '',
            proof_message_type TEXT NOT NULL DEFAULT '',
            proof_hash TEXT NOT NULL DEFAULT '',
            proof_timestamp TEXT NOT NULL DEFAULT '',
            last_delivery_attempt_at TEXT NOT NULL DEFAULT '',
            last_delivery_success_at TEXT NOT NULL DEFAULT '',
            last_delivery_failure_at TEXT NOT NULL DEFAULT '',
            consecutive_delivery_failures INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (peer_id, host, port)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO peer_endpoints (
            peer_id, host, port, source, last_seen_at, last_verified_at,
            verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp,
            last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
            consecutive_delivery_failures, updated_at
        )
        SELECT
            peer_id,
            host,
            port,
            source,
            last_seen_at,
            last_verified_at,
            verification_kind,
            proof_count,
            proof_message_id,
            proof_message_type,
            proof_hash,
            '',
            '',
            '',
            '',
            0,
            updated_at
        FROM peer_endpoints_legacy
        """
    )
    observation_rows = conn.execute(
        """
        SELECT
            peer_id, host, port, source, verification_kind,
            proof_message_id, proof_message_type, proof_hash, proof_timestamp,
            last_verified_at, proof_count, updated_at
        FROM peer_endpoint_observations AS obs
        """
    ).fetchall()
    for row in observation_rows:
        existing = conn.execute(
            """
            SELECT source, last_seen_at, last_verified_at, verification_kind,
                   proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp, updated_at
            FROM peer_endpoints
            WHERE peer_id = ? AND host = ? AND port = ?
            LIMIT 1
            """,
            (row["peer_id"], row["host"], int(row["port"])),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO peer_endpoints (
                    peer_id, host, port, source, last_seen_at, last_verified_at,
                    verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp,
                    last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
                    consecutive_delivery_failures, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["peer_id"],
                    row["host"],
                    int(row["port"]),
                    row["source"],
                    row["last_verified_at"],
                    row["last_verified_at"],
                    row["verification_kind"],
                    int(row["proof_count"] or 0),
                    row["proof_message_id"],
                    row["proof_message_type"],
                    row["proof_hash"],
                    str(row["proof_timestamp"] or row["last_verified_at"] or ""),
                    "",
                    "",
                    "",
                    0,
                    row["updated_at"],
                ),
            )
            continue

        selected_source = str(existing["source"] or "")
        if _endpoint_source_priority(row["source"]) >= _endpoint_source_priority(selected_source):
            selected_source = str(row["source"] or "")
        selected_last_seen_at = _max_timestamp(str(existing["last_seen_at"] or ""), str(row["last_verified_at"] or ""))
        selected_last_verified_at = _max_timestamp(str(existing["last_verified_at"] or ""), str(row["last_verified_at"] or ""))
        selected_proof_count = max(int(existing["proof_count"] or 0), int(row["proof_count"] or 0))
        selected_verification_kind = str(existing["verification_kind"] or "")
        selected_proof_message_id = str(existing["proof_message_id"] or "")
        selected_proof_message_type = str(existing["proof_message_type"] or "")
        selected_proof_hash = str(existing["proof_hash"] or "")
        selected_proof_timestamp = _max_timestamp(str(existing["proof_timestamp"] or ""), str(row["proof_timestamp"] or row["last_verified_at"] or ""))
        if int(row["proof_count"] or 0) >= int(existing["proof_count"] or 0):
            selected_verification_kind = str(row["verification_kind"] or "") or selected_verification_kind
            selected_proof_message_id = str(row["proof_message_id"] or "") or selected_proof_message_id
            selected_proof_message_type = str(row["proof_message_type"] or "") or selected_proof_message_type
            selected_proof_hash = str(row["proof_hash"] or "") or selected_proof_hash

        conn.execute(
            """
            UPDATE peer_endpoints
            SET source = ?,
                last_seen_at = ?,
                last_verified_at = ?,
                verification_kind = ?,
                proof_count = ?,
                proof_message_id = ?,
                proof_message_type = ?,
                proof_hash = ?,
                proof_timestamp = ?,
                last_delivery_attempt_at = COALESCE(last_delivery_attempt_at, ''),
                last_delivery_success_at = COALESCE(last_delivery_success_at, ''),
                last_delivery_failure_at = COALESCE(last_delivery_failure_at, ''),
                consecutive_delivery_failures = COALESCE(consecutive_delivery_failures, 0),
                updated_at = ?
            WHERE peer_id = ? AND host = ? AND port = ?
            """,
            (
                selected_source,
                selected_last_seen_at,
                selected_last_verified_at,
                selected_verification_kind,
                selected_proof_count,
                selected_proof_message_id,
                selected_proof_message_type,
                selected_proof_hash,
                selected_proof_timestamp,
                _max_timestamp(str(existing["updated_at"] or ""), str(row["updated_at"] or "")),
                row["peer_id"],
                row["host"],
                int(row["port"]),
            ),
        )
    conn.execute("DROP TABLE peer_endpoints_legacy")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_peer_endpoints_peer_recent "
        "ON peer_endpoints(peer_id, last_verified_at DESC, last_seen_at DESC, updated_at DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_peer_endpoints_recent ON peer_endpoints(updated_at DESC)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run VOOL storage migrations.")
    parser.add_argument("--db-path", default=None, help="Optional SQLite path override.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_migrations(db_path=args.db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
