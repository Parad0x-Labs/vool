from __future__ import annotations

"""Shared post/task-event HTML emitters for the workstation dashboard script."""

WORKSTATION_CARD_RENDER_SECTIONS = """    function renderCompactPostCard(post, options = {}) {
      const structured = buildTradingEvidenceSummary(post);
      const createdAt = post?.created_at || post?.ts || post?.timestamp || 0;
      const author = post?.author_label || post?.author_claim_label || post?.author_display_name || shortId(post?.author_agent_id || '', 18) || 'unknown';
      const topic = normalizeInlineText(post?.topic_title || '');
      const body = String(structured?.body || post?.body || post?.detail || '').trim() || 'No detail yet.';
      const evidenceKinds = structured?.evidenceKinds || extractEvidenceKinds(post);
      const commonsMeta = post?.commons_meta || {};
      const promotion = commonsMeta?.promotion_candidate || null;
      const href = post?.topic_id ? topicHref(post.topic_id) : '';
      const previewLen = Number(options.previewLen || 180);
      const detailKey = openKey('post', post?.post_id || '', post?.topic_id || '', createdAt, structured?.title || postHeadline(post));
      const inspectPayload = {
        post_id: post?.post_id || '',
        topic_id: post?.topic_id || '',
        title: structured?.title || postHeadline(post),
        summary: structured?.preview || postPreview(post, previewLen),
        body,
        source_label: 'watcher-derived',
        freshness: 'current',
        status: post?.post_kind || post?.kind || 'update',
        topic_title: topic,
        author,
        created_at: createdAt,
        evidence_kinds: evidenceKinds,
      };
      return `
        <details class="fold-card" data-open-key="${esc(detailKey)}" ${inspectAttrs('Observation', structured?.title || postHeadline(post), inspectPayload)}${options.defaultOpen ? ' open' : ''}>
          <summary>
            <div class="fold-title-row">
              <div class="fold-title">${esc(structured?.title || postHeadline(post))}</div>
              <div class="fold-stamp">${fmtTime(createdAt)}</div>
            </div>
            <div class="fold-preview">${esc(structured?.preview || postPreview(post, previewLen))}</div>
            <div class="row-meta">
              ${chip(post?.post_kind || post?.kind || 'update')}
              ${post?.stance ? chip(post.stance) : ''}
              ${post?.call_status ? chip(post.call_status, post.call_status === 'WIN' ? 'ok' : (post.call_status === 'LOSS' ? 'warn' : '')) : ''}
              ${commonsMeta?.support_weight ? chip(`support ${Number(commonsMeta.support_weight || 0).toFixed(1)}`, 'ok') : ''}
              ${commonsMeta?.comment_count ? chip(`${fmtNumber(commonsMeta.comment_count || 0)} comments`) : ''}
              ${promotion ? chip(`promotion ${promotion.status || 'draft'}`, promotion.status === 'approved' || promotion.status === 'promoted' ? 'ok' : '') : ''}
              ${topic ? `<span>${esc(topic)}</span>` : ''}
              <span>${esc(author)}</span>
            </div>
          </summary>
          <div class="fold-body">
            <div class="body-pre">${esc(body)}</div>
            <div class="row-meta">
              ${evidenceKinds.map((kind) => chip(kind)).join('')}
              ${commonsMeta?.challenge_weight ? chip(`challenge ${Number(commonsMeta.challenge_weight || 0).toFixed(1)}`, 'warn') : ''}
              ${promotion ? chip(`score ${Number(promotion.score || 0).toFixed(2)}`) : ''}
              ${promotion?.review_state ? chip(`review ${promotion.review_state}`) : ''}
              <button class="inspect-button" type="button" ${inspectAttrs('Observation', structured?.title || postHeadline(post), inspectPayload)}>Inspect</button>
              ${href && options.topicLink !== false ? `<a class="copy-button" href="${href}">Open topic</a>` : ''}
            </div>
          </div>
        </details>
      `;
    }

    function renderCompactPostList(posts, options = {}) {
      const items = Array.isArray(posts) ? posts : [];
      if (!items.length) {
        return `<div class="empty">${esc(options.emptyText || 'No posts yet.')}</div>`;
      }
      const limit = Math.max(1, Number(options.limit || 8));
      const visible = items.slice(0, limit);
      const note = items.length > limit
        ? `<div class="list-note">Showing latest ${fmtNumber(visible.length)} of ${fmtNumber(items.length)} posts.</div>`
        : '';
      return `${note}${visible.map((post, index) => renderCompactPostCard(post, {
        previewLen: options.previewLen || 180,
        topicLink: options.topicLink,
        defaultOpen: Boolean(options.defaultOpenFirst && index === 0),
      })).join('')}`;
    }

    function renderTaskEventFold(event) {
      const detailKey = openKey('task-event', event.topic_id || event.topic_title || '', event.timestamp || '', event.event_type || '', event.claim_id || event.agent_label || '');
      const inspectPayload = {
        topic_id: event.topic_id || '',
        title: event.topic_title || 'Hive task event',
        summary: taskEventPreview(event),
        detail: event.detail || '',
        truth_label: 'watcher-derived',
        freshness: event.presence_freshness || 'current',
        status: event.status || event.event_type || '',
        claim_id: event.claim_id || '',
        agent_label: event.agent_label || '',
        timestamp: event.timestamp || '',
        tags: event.tags || [],
        capability_tags: event.capability_tags || [],
        conflict_count: event.event_type === 'challenge_raised' || event.event_type === 'task_blocked' ? 1 : 0,
      };
      return `
        <details class="fold-card" data-open-key="${esc(detailKey)}" ${inspectAttrs('Observation', event.topic_title || 'Hive task event', inspectPayload)}>
          <summary>
            <div class="fold-title-row">
              <h3 class="fold-title">${esc(event.topic_title || 'Hive task event')}</h3>
              <div class="fold-stamp">${fmtTime(event.timestamp)}</div>
            </div>
            <p class="fold-preview">${esc(taskEventPreview(event))}</p>
            <div class="row-meta">
              ${chip(taskEventLabel(event.event_type), taskEventKind(event.event_type))}
              ${event.progress_state ? chip(event.progress_state, event.progress_state === 'blocked' ? 'warn' : '') : ''}
              ${event.status ? chip(event.status, event.status === 'solved' || event.status === 'completed' ? 'ok' : '') : ''}
            </div>
          </summary>
          <div class="fold-body">
            <p class="body-pre">${esc(event.detail || 'No task detail provided.')}</p>
            <div class="row-meta">
              <span>${esc(event.agent_label || 'unknown')}</span>
              ${event.claim_id ? `<span class="mono">${esc(shortId(event.claim_id, 16))}</span>` : ''}
              ${(event.tags || []).slice(0, 4).map((tag) => chip(tag)).join('')}
              ${(event.capability_tags || []).slice(0, 4).map((tag) => chip(tag)).join('')}
              <button class="inspect-button" type="button" ${inspectAttrs('Observation', event.topic_title || 'Hive task event', inspectPayload)}>Inspect</button>
            </div>
            ${event.topic_id ? `<div class="row-meta"><a class="copy-button" href="${topicHref(event.topic_id)}">Open topic</a></div>` : ''}
          </div>
        </details>
      `;
    }

    function renderTaskEvents(events, limit, emptyText) {
      if (!events.length) return `<div class="empty">${esc(emptyText)}</div>`;
      const visible = events.slice(0, limit).map(renderTaskEventFold).join('');
      const older = events.slice(limit, limit + 15);
      if (!older.length) return visible;
      const olderKey = openKey('task-events-older', limit, older[0]?.timestamp || '', older.length);
      return `
        ${visible}
        <details class="fold-card" data-open-key="${esc(olderKey)}">
          <summary>
            <div class="fold-title-row">
              <h3 class="fold-title">Older task events</h3>
              <div class="fold-stamp">${fmtNumber(older.length)}</div>
            </div>
            <p class="fold-preview">Collapsed by default. Recent ${fmtNumber(limit)} stay visible; older flow stays out of the way until needed.</p>
          </summary>
          <div class="fold-body">
            <div class="list">
              ${older.map(renderTaskEventFold).join('')}
            </div>
          </div>
        </details>
      `;
    }
"""
