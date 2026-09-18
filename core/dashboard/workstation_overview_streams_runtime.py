from __future__ import annotations

"""Topic, claim, region, and research list runtime fragment for the workstation dashboard."""

WORKSTATION_OVERVIEW_STREAMS_RUNTIME = '''
    function renderResearchGravityList(data) {
      const researchQueue = Array.isArray(data.research_queue) ? data.research_queue : [];
      document.getElementById('researchGravityList').innerHTML = researchQueue.length ? researchQueue.slice(0, 6).map((row) => `
        <a class="card-link" href="${topicHref(row.topic_id)}">
          <article class="card" ${inspectAttrs('Task', row.title || 'Research topic', {
            topic_id: row.topic_id || '',
            title: row.title || 'Research topic',
            summary: row.summary || '',
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: row.status || 'open',
            research_priority: row.research_priority || 0,
            active_claim_count: row.active_claim_count || 0,
            evidence_count: row.evidence_count || 0,
            steering_reasons: row.steering_reasons || [],
          })}>
            <h3>${esc(row.title || 'Research topic')}</h3>
            <p>${esc(compactText(row.summary || '', 200) || 'No summary yet.')}</p>
            <div class="row-meta">
              ${chip(`priority ${Number(row.research_priority || 0).toFixed(2)}`, Number(row.research_priority || 0) >= 0.7 ? 'ok' : '')}
              ${Number(row.commons_signal_strength || 0) > 0 ? chip(`commons ${Number(row.commons_signal_strength || 0).toFixed(2)}`, 'ok') : ''}
              ${chip(`claims ${fmtNumber(row.active_claim_count || 0)}`)}
              ${chip(`evidence ${fmtNumber(row.evidence_count || 0)}`)}
              <button class="inspect-button" type="button" ${inspectAttrs('Task', row.title || 'Research topic', {
                topic_id: row.topic_id || '',
                title: row.title || 'Research topic',
                summary: row.summary || '',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: row.status || 'open',
                research_priority: row.research_priority || 0,
                active_claim_count: row.active_claim_count || 0,
                evidence_count: row.evidence_count || 0,
                steering_reasons: row.steering_reasons || [],
              })}>Inspect</button>
            </div>
            <div class="row-meta">
              ${Array.isArray(row.steering_reasons) ? row.steering_reasons.slice(0, 4).map((reason) => chip(String(reason || '').replace(/_/g, ' '))).join('') : ''}
            </div>
          </article>
        </a>
      `).join('') : '<div class="empty">No research pressure is visible yet.</div>';
    }

    function renderTopicList(topics) {
      document.getElementById('topicList').innerHTML = topics.length ? topics.slice(0, 8).map((topic) => `
        <a class="card-link" href="${topicHref(topic.topic_id)}">
          <article class="card" ${inspectAttrs('Task', topic.title || 'Hive task', {
            topic_id: topic.topic_id || '',
            title: topic.title || 'Hive task',
            summary: topic.summary || '',
            truth_label: 'watcher-derived',
            freshness: 'current',
            status: topic.status || 'open',
            moderation_state: topic.moderation_state || '',
            creator_label: topic.creator_claim_label || topic.creator_display_name || shortId(topic.created_by_agent_id),
            updated_at: topic.updated_at || '',
          })}>
            <h3>${esc(topic.title)}</h3>
            <p>${esc(topic.summary)}</p>
            <div class="row-meta">
              ${chip(topic.status, topic.status === 'solved' ? 'ok' : '')}
              ${chip(topic.moderation_state, topic.moderation_state === 'approved' ? 'ok' : 'warn')}
              <span>${esc(topic.creator_claim_label || topic.creator_display_name || shortId(topic.created_by_agent_id))}</span>
              <span>${fmtTime(topic.updated_at)}</span>
              <button class="inspect-button" type="button" ${inspectAttrs('Task', topic.title || 'Hive task', {
                topic_id: topic.topic_id || '',
                title: topic.title || 'Hive task',
                summary: topic.summary || '',
                truth_label: 'watcher-derived',
                freshness: 'current',
                status: topic.status || 'open',
                moderation_state: topic.moderation_state || '',
                creator_label: topic.creator_claim_label || topic.creator_display_name || shortId(topic.created_by_agent_id),
                updated_at: topic.updated_at || '',
              })}>Inspect</button>
            </div>
          </article>
        </a>
      `).join('') : '<div class="empty">No visible topics yet.</div>';
    }

    function renderClaimStreamList(claims) {
      document.getElementById('claimStreamList').innerHTML = claims.length ? claims.slice(0, 8).map((claim) => `
        <article class="card" ${inspectAttrs('Claim', claim.topic_title || claim.claim_id || 'Hive claim', {
          claim_id: claim.claim_id || '',
          topic_id: claim.topic_id || '',
          title: claim.topic_title || 'Hive claim',
          summary: claim.note || '',
          truth_label: 'watcher-derived',
          freshness: 'current',
          status: claim.status || 'active',
          agent_label: claim.agent_claim_label || claim.agent_display_name || claim.agent_id || '',
          capability_tags: claim.capability_tags || [],
          updated_at: claim.updated_at || claim.created_at || '',
        })}>
          <h3>${esc(claim.topic_title || 'Hive claim')}</h3>
          <p>${esc(compactText(claim.note || '', 180) || 'No claim note yet.')}</p>
          <div class="row-meta">
            ${chip(claim.status || 'active', claim.status === 'completed' ? 'ok' : claim.status === 'blocked' ? 'warn' : '')}
            <span>${esc(claim.agent_claim_label || claim.agent_display_name || claim.agent_id || 'unknown')}</span>
            <span>${fmtTime(claim.updated_at || claim.created_at)}</span>
            <button class="inspect-button" type="button" ${inspectAttrs('Claim', claim.topic_title || claim.claim_id || 'Hive claim', {
              claim_id: claim.claim_id || '',
              topic_id: claim.topic_id || '',
              title: claim.topic_title || 'Hive claim',
              summary: claim.note || '',
              truth_label: 'watcher-derived',
              freshness: 'current',
              status: claim.status || 'active',
              agent_label: claim.agent_claim_label || claim.agent_display_name || claim.agent_id || '',
              capability_tags: claim.capability_tags || [],
              updated_at: claim.updated_at || claim.created_at || '',
            })}>Inspect</button>
          </div>
        </article>
      `).join('') : '<div class="empty">No live claims yet.</div>';
    }

    function renderRegionList(regionStats) {
      document.getElementById('regionList').innerHTML = regionStats.length ? regionStats.map((row) => `
        <article class="card">
          <h3>${esc(row.region)}</h3>
          <div class="row-meta">
            ${chip(`${fmtNumber(row.online_agents || 0)} online`, 'ok')}
            ${chip(`${fmtNumber(row.active_topics || 0)} active`)}
            ${chip(`${fmtNumber(row.solved_topics || 0)} solved`)}
          </div>
        </article>
      `).join('') : '<div class="empty">No regional activity yet.</div>';
    }
'''
