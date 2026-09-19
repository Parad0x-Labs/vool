
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
