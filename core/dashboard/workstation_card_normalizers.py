from __future__ import annotations

"""Shared post/task-event normalization helpers for the workstation dashboard script."""

WORKSTATION_CARD_NORMALIZERS = """    function extractEvidenceKinds(post) {
      const direct = Array.isArray(post?.evidence_kinds) ? post.evidence_kinds.filter(Boolean) : [];
      if (direct.length) return direct.slice(0, 6);
      const refs = Array.isArray(post?.evidence_refs) ? post.evidence_refs : [];
      return refs
        .map((ref) => String(ref?.kind || ref?.type || '').trim())
        .filter(Boolean)
        .slice(0, 6);
    }

    function buildTradingEvidenceSummary(post) {
      const refs = Array.isArray(post?.evidence_refs) ? post.evidence_refs : [];
      if (!refs.length) return null;
      const evidenceKinds = extractEvidenceKinds(post);
      let summary = null;
      let heartbeat = null;
      let decision = null;
      let lab = null;
      let callCount = null;
      let athCount = null;
      let lessonCount = null;
      let missedCount = null;
      let discoveryCount = null;
      for (const ref of refs) {
        const kind = String(ref?.kind || ref?.type || '').trim().toLowerCase();
        if (kind === 'trading_learning_summary' && ref?.summary) summary = ref.summary;
        if (kind === 'trading_runtime_heartbeat' && ref?.heartbeat) heartbeat = ref.heartbeat;
        if (kind === 'trading_decision_funnel' && ref?.summary) decision = ref.summary;
        if (kind === 'trading_learning_lab_summary' && ref?.summary) lab = ref.summary;
        if (kind === 'trading_calls') callCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_ath_updates') athCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_lessons') lessonCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_missed_mooners') missedCount = Array.isArray(ref?.items) ? ref.items.length : 0;
        if (kind === 'trading_discoveries') discoveryCount = Array.isArray(ref?.items) ? ref.items.length : 0;
      }
      if (missedCount === null && lab && Number.isFinite(Number(lab.missed_opportunities))) {
        missedCount = Number(lab.missed_opportunities);
      }
      if (discoveryCount === null && lab && Number.isFinite(Number(lab.discoveries))) {
        discoveryCount = Number(lab.discoveries);
      }
      const hasTradingSignal = summary || heartbeat || decision || lab || callCount !== null || athCount !== null || lessonCount !== null || missedCount !== null || discoveryCount !== null;
      if (!hasTradingSignal) return null;
      const lines = [];
      if (summary) {
        lines.push(
          `calls ${fmtNumber(summary.total_calls || 0)} · wins ${fmtNumber(summary.wins || 0)} · losses ${fmtNumber(summary.losses || 0)} · pending ${fmtNumber(summary.pending || 0)} · safe ${fmtPct(summary.safe_exit_pct || 0)}`
        );
      }
      if (heartbeat) {
        lines.push(
          `scanner ${heartbeat.signal_only ? 'signal-only' : 'live'} · tick ${fmtNumber(heartbeat.tick || 0)} · tracked ${fmtNumber(heartbeat.tracked_tokens || 0)} · new ${fmtNumber(heartbeat.new_tokens_seen || 0)} · ${String(heartbeat.market_regime || 'UNKNOWN')}`
        );
      }
      if (decision) {
        lines.push(
          `funnel pass ${fmtNumber(decision.pass || 0)} · reject ${fmtNumber(decision.buy_rejected || 0)} · buy ${fmtNumber(decision.buy || 0)}`
        );
      }
      if (lab) {
        lines.push(
          `learn ${fmtNumber(lab.token_learnings || 0)} · missed ${fmtNumber(lab.missed_opportunities || 0)} · discoveries ${fmtNumber(lab.discoveries || 0)} · patterns ${fmtNumber(lab.mined_patterns || 0)}`
        );
      }
      const counters = [
        callCount != null ? `new calls ${fmtNumber(callCount)}` : '',
        athCount != null ? `ath updates ${fmtNumber(athCount)}` : '',
        lessonCount != null ? `lessons ${fmtNumber(lessonCount)}` : '',
        missedCount != null ? `missed ${fmtNumber(missedCount)}` : '',
        discoveryCount != null ? `discoveries ${fmtNumber(discoveryCount)}` : '',
      ].filter(Boolean);
      if (counters.length) lines.push(counters.join(' · '));
      const title = normalizeInlineText(post?.topic_title || post?.post_kind || 'trading update');
      return {
        title,
        preview: lines.slice(0, 2).join(' | ') || 'Structured trading update.',
        body: lines.join('\\n') || 'Structured trading update.',
        evidenceKinds,
      };
    }

    function compactText(value, maxLen = 180) {
      const text = normalizeInlineText(value);
      if (!text) return '';
      if (text.length <= maxLen) return text;
      return `${text.slice(0, Math.max(0, maxLen - 1)).trimEnd()}…`;
    }

    function postHeadline(post) {
      const structured = buildTradingEvidenceSummary(post);
      if (structured?.title) return structured.title;
      const raw = String(post?.body || post?.detail || '');
      const firstLine = normalizeInlineText(raw.split(/\\n+/)[0] || '');
      if (firstLine && firstLine.length <= 84) return firstLine;
      const kind = normalizeInlineText(post?.post_kind || post?.kind || 'update');
      const token = normalizeInlineText(post?.token_name || '');
      if (token) return `${kind} · ${token}`;
      const topic = normalizeInlineText(post?.topic_title || '');
      if (topic) return `${kind} · ${topic}`;
      return kind || 'update';
    }

    function postPreview(post, maxLen = 180) {
      const structured = buildTradingEvidenceSummary(post);
      if (structured?.preview) return compactText(structured.preview, maxLen);
      const raw = normalizeInlineText(post?.body || post?.detail || '');
      if (!raw) return 'No detail yet.';
      const headline = normalizeInlineText(postHeadline(post));
      const trimmed = raw.startsWith(headline)
        ? raw.slice(headline.length).replace(/^[\\s.:-]+/, '')
        : raw;
      return compactText(trimmed || raw, maxLen) || 'No detail yet.';
    }

    function taskEventLabel(eventType) {
      const normalized = String(eventType || '').toLowerCase();
      return {
        topic_created: 'topic_opened',
        task_claimed: 'claimed',
        task_released: 'released',
        task_completed: 'claim_done',
        task_blocked: 'blocked',
        progress_update: 'progress',
        evidence_added: 'evidence',
        challenge_raised: 'challenge',
        summary_posted: 'summary',
        result_submitted: 'result',
      }[normalized] || (normalized || 'event');
    }

    function taskEventKind(eventType) {
      const normalized = String(eventType || '').toLowerCase();
      if (normalized === 'task_completed' || normalized === 'result_submitted') return 'ok';
      if (normalized === 'task_blocked' || normalized === 'challenge_raised') return 'warn';
      return '';
    }

    function taskEventPreview(event) {
      const parts = [];
      if (event.agent_label) parts.push(event.agent_label);
      const detail = compactText(event.detail || '', 120);
      if (detail) parts.push(detail);
      return parts.join(' | ') || 'No task summary yet.';
    }
"""
