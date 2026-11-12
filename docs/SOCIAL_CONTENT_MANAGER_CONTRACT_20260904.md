# Social Content Manager — Typed Contract Family (2026-09-04)

Owner lane: `build/social-content-manager-p1-20260904` (base `2661343b`). Implementation authority: `core/social_content/` (orchestration) + `storage/social_content_store.py` (persistence) + `skills/vool-social-content-manager/SKILL.md` (skill entrypoint). This document defines the immutable typed contract family and its state laws. It follows the repository convention of typed records with monotonic versions; names match the goal contract.

## Laws that bound every contract

1. Records are immutable at decision boundaries: any semantic change produces a new version row; approval/decision rows are never mutated after write.
2. Every record is scoped by `project_id` and `operator` (principal). Cross-project reads require an explicit export path with privacy redaction.
3. Imported social text (posts, feeds, search results) is **untrusted evidence** — never instructions, never authority, never voice evidence.
4. Content of any kind cannot grant tools, schemas, permissions or posting authority.
5. Every externally-visible assertion of effect is typed; caller-supplied success fields are rejected at the store/API boundary.

## Contracts

### `SocialCampaign`
`campaign_id`, `project_id`, `objective`, `audience`, `platforms` (subset of the managed set), `timeframe_start/end`, `guardrails` (list of strings), `status` (`draft|active|paused|archived`), `created_at`, `updated_at`, `version` (monotonic int, CAS on update).

### `ContentBrief`
`brief_id`, `campaign_id`, `purpose`, `thesis`, `evidence_refs` (ids into the trend/evidence ledgers), `requested_channels`, `content_mode` (`evergreen|timely|reactive`), `voice_scope` (`none|global|project:<key>`), `deadline`, `constraints` (freeform bounded strings), `version`.

### `TrendObservation`
`observation_id`, `source_class` (`personal_feed|platform_trending|x_search|named_account|external_news`), `observed_text` (bounded excerpt), `link`, `account` (author handle where visible; reposter distinguished where visible), `observed_at`, `region`, `language`, `session_scope` (the grant id of the authorized read session, or `public`), `corroboration` (`uncorroborated|corroborated:<evidence_ref>`), `expires_at`, `relevance_note`.

### `ContentIdea`
`idea_id`, `campaign_id`, `angle`, `why_now`, `target_reader`, `evidence_ids`, `expected_action`, `risk_flags`, `dedup_signature` (normalized sha256 of angle+target+evidence ids), `status` (`candidate|rejected_duplicate|rejected_weak_evidence|archived`).

### `ContentDraft`
`draft_id`, `version` (monotonic per draft), `platform`, `format` (`post|thread|long_post|article|message` — X uses the XDrafT modes), `body_bytes` (exact bytes, hash-bound), `title`, `thread_parts`, `media_brief`, `anchor_lines` (operator-supplied lines with `preservation_mode` ∈ `verbatim|meaning_locked|inspiration_only`), `claim_ledger` (per-claim `supported|opinion|unknown|disputed` + evidence pointers), `source_ledger` (TrendObservation ids), `privacy_findings`, `validation_state` (`unvalidated|clean|needs_changes|refused|validation_unknown`), `content_hash` (sha256 of body_bytes).

### `ContentDecision`
`decision_id`, `draft_id`, `draft_version`, `content_hash`, `decision` (`approved|refused|needs_changes|NO_POST`), `platform`, `destination` (typed, if known), `operator_identity`, `decided_at`, `reason`, `expires_at`. An approval binds exact bytes + platform + version + expiry; any byte/platform/version change invalidates it (the store refuses to treat the old decision as covering new content).

### `PublicationHandoff`
`handoff_id`, `draft_id`, `draft_version`, `content_hash`, `destination`, `connector_availability` (`available|publishing_unavailable|scheduling_unavailable|authenticated_browser_session_unavailable|handoff_refused`), `permission_state`, `effect_receipt_ref`, `outcome` (typed; `externally_verified|externally_acknowledged|dispatch_attempted_unknown|not_attempted|refused` — never `success` from a caller).

### `MetricObservation`
`observation_id`, `content_ref` (draft id/version/hash) or `publication_ref` (handoff id), `metric_name`, `value`, `unit`, `window_start/end`, `observed_at`, `source` (`operator_entered|file_import|connector_observed`), `provenance` (file name/import batch or connector record id), `denominator_known` (bool), `raw_fields` (dict).

### `ContentHypothesis`
`hypothesis_id`, `campaign_id`, `prediction`, `evidence_ids`, `window`, `outcome` (`open|supported|contradicted|inconclusive|expired`), `confidence` (`low|medium|high` with stated basis), `disposition` (freeform bounded). One post can never set `supported` with `high` confidence alone; correlation never becomes causal truth automatically.

## State ownership and transitions

| State | Owner | Notes |
|---|---|---|
| Skill selection/injection | `core/native_skill_library.py` + `core/tool_offer_assembly.py` | manager adds no new selector |
| X editorial structure/limits/rendering | `core/x_editorial.py` | delegation only |
| Voice evidence | `core/x_voice_profile.py` | trend content never writes it |
| Operator stable preferences | `core/operator_profile.py` | manager reads only |
| Current-information decisions | `core/execution_requirements.py` + retrieval authority gate | manager requests, never decides |
| Claim support | `core/claim_support.py` | clean facts require support |
| Permissions/effects/budgets/receipts | `core/mode_permission_policy`, `core/effect_gateway.py`, `core/effect_budget.py` | every store mutation traverses once via command registry |
| External publication | `relay/channel_outbound.py` canonical path only | manager grants zero authority |
| Local campaign/draft/metric/hypothesis state | `storage/social_content_store.py` | this lane's only new authority |
| Procedure learning | `core/learning/*` (PB01) | no auto promotion from engagement |

Campaign: `draft → active → paused → archived` (archive is reversible restore; no silent delete; destructive purge only through canonical authority if ever added). Draft: versioned append; decisions immutable; approval invalidation is automatic on hash/version/platform mismatch.

## Failure map (typed outcomes)

`publishing_unavailable` (no canonical connector: X, LinkedIn, Bluesky, Mastodon, Threads) · `scheduling_unavailable` (no canonical content scheduler) · `authenticated_browser_session_unavailable` (no explicit selected-tab grant / attach unsafe) · `handoff_refused` (approval/effect chain not satisfied for Telegram/Discord) · `validation_unknown` (platform limit unpublished) · `secret_refusal` (privacy scan fails; nothing written) · `revision_conflict` (lost-update guard) · `NO_POST` (successful first-class terminal content decision) · `unsupported_platform` (existing outbound typed refusal).

## Non-goals

- No new publishing connectors, no live account access, no scheduler, no second memory/analytics authority, no scraper farm, no profile harvesting, no autonomous posting, no forking of X editorial logic, no per-platform regex piles in unrelated hot files.
