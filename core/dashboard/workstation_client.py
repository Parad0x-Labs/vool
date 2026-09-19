from __future__ import annotations

from core.dashboard.workstation_cards import WORKSTATION_CARD_RENDERERS
from core.dashboard.workstation_inspector_runtime import WORKSTATION_INSPECTOR_RUNTIME
from core.dashboard.workstation_overview_runtime import WORKSTATION_OVERVIEW_RUNTIME
from core.dashboard.workstation_trading_learning_runtime import WORKSTATION_TRADING_LEARNING_RUNTIME
from core.dashboard.workstation_voolbook_runtime import WORKSTATION_VOOLBOOK_RUNTIME

WORKSTATION_CLIENT_TEMPLATE = '''  <script>
    __WORKSTATION_SCRIPT__
    const state = __INITIAL_STATE__;
    let currentDashboardState = state;
    const uiState = { openDetails: Object.create(null) };

    function esc(value) {
      return String(value ?? '').replace(/[&<>\"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[ch]);
    }

    function fmtNumber(value) {
      return new Intl.NumberFormat().format(Number(value || 0));
    }

    function fmtUsd(value) {
      const num = Number(value || 0);
      if (!Number.isFinite(num) || num <= 0) return '$0';
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(num);
    }

    function fmtPct(value) {
      const num = Number(value || 0);
      if (!Number.isFinite(num)) return '0.0%';
      return `${num > 0 ? '+' : ''}${num.toFixed(1)}%`;
    }

    function fmtTime(value) {
      if (!value) return 'unknown';
      let raw = value;
      const numeric = Number(value);
      if (Number.isFinite(numeric) && numeric > 0) {
        raw = numeric < 1e12 ? numeric * 1000 : numeric;
      }
      const date = new Date(raw);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleString();
    }

    function fmtAgeSeconds(value) {
      const num = Number(value);
      if (!Number.isFinite(num) || num < 0) return 'unknown';
      if (num < 60) return `${Math.round(num)}s ago`;
      if (num < 3600) return `${Math.round(num / 60)}m ago`;
      if (num < 86400) return `${(num / 3600).toFixed(1)}h ago`;
      return `${(num / 86400).toFixed(1)}d ago`;
    }

    function parseDashboardTs(value) {
      if (!value) return 0;
      const numeric = Number(value);
      if (Number.isFinite(numeric) && numeric > 0) {
        return numeric < 1e12 ? numeric * 1000 : numeric;
      }
      const parsed = new Date(value).getTime();
      return Number.isFinite(parsed) ? parsed : 0;
    }

''' + WORKSTATION_TRADING_LEARNING_RUNTIME + '''

    function shortId(value, size = 12) {
      const text = String(value || '');
      if (text.length <= size) return text;
      return text.slice(0, size) + '...';
    }

    function chip(text, kind = '') {
      const klass = kind ? `chip ${kind}` : 'chip';
      return `<span class="${klass}">${esc(text)}</span>`;
    }

''' + WORKSTATION_INSPECTOR_RUNTIME + '''

    async function copyText(value, button) {
      const text = String(value || '');
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        if (button) {
          const old = button.textContent;
          button.textContent = 'Copied';
          window.setTimeout(() => { button.textContent = old; }, 1200);
        }
      } catch (_err) {
        window.prompt('Copy text', text);
      }
    }

    function topicHref(topicId) {
      return `__TOPIC_BASE_PATH__/${encodeURIComponent(String(topicId || ''))}`;
    }

    function normalizeInlineText(value) {
      return String(value ?? '').replace(/\\s+/g, ' ').trim();
    }

    function openKey(...parts) {
      const normalized = parts
        .map((part) => normalizeInlineText(part))
        .filter(Boolean)
        .join('::')
        .slice(0, 240);
      return normalized || 'detail';
    }

    function syncOpenIndicator(detail) {
      if (!detail) return;
      const chipNode = detail.querySelector('[data-open-chip]');
      if (chipNode) chipNode.textContent = detail.open ? 'expanded' : 'expand';
    }

    function captureOpenDetails(root) {
      if (!root) return;
      root.querySelectorAll('details[data-open-key]').forEach((detail) => {
        const key = String(detail.dataset.openKey || '').trim();
        if (key) uiState.openDetails[key] = Boolean(detail.open);
      });
    }

    function restoreOpenDetails(root) {
      if (!root) return;
      root.querySelectorAll('details[data-open-key]').forEach((detail) => {
        const key = String(detail.dataset.openKey || '').trim();
        if (key && Object.prototype.hasOwnProperty.call(uiState.openDetails, key)) {
          detail.open = Boolean(uiState.openDetails[key]);
        }
        syncOpenIndicator(detail);
        if (!detail.dataset.openBound) {
          detail.addEventListener('toggle', () => {
            const toggleKey = String(detail.dataset.openKey || '').trim();
            if (toggleKey) uiState.openDetails[toggleKey] = Boolean(detail.open);
            syncOpenIndicator(detail);
          });
          detail.dataset.openBound = '1';
        }
      });
    }

    function renderInto(containerId, html, {preserveDetails = false} = {}) {
      const root = document.getElementById(containerId);
      if (!root) return;
      if (preserveDetails) captureOpenDetails(root);
      root.innerHTML = html;
      if (preserveDetails) restoreOpenDetails(root);
    }
''' + WORKSTATION_CARD_RENDERERS + '''

    function isCommonsTopic(topic) {
      const tags = Array.isArray(topic?.topic_tags) ? topic.topic_tags.map((item) => String(item || '').toLowerCase()) : [];
      const combined = `${String(topic?.title || '')} ${String(topic?.summary || '')}`.toLowerCase();
      return (
        tags.includes('agent_commons') ||
        tags.includes('commons') ||
        tags.includes('brainstorm') ||
        tags.includes('curiosity') ||
        combined.includes('agent commons') ||
        combined.includes('brainstorm lane') ||
        combined.includes('idle curiosity')
      );
    }

    function renderBranding(data) {
      const brand = data.branding || {};
      document.getElementById('watchTitle').textContent = brand.watch_title || 'VOOL Watch';
      document.getElementById('legalName').textContent = brand.legal_name || 'Parad0x Labs';
      const xLink = document.getElementById('xHandle');
      if (xLink) {
        xLink.href = brand.x_url || 'https://x.com/Parad0x_Labs';
        xLink.textContent = 'Follow us on X';
      }
      const discordLink = document.getElementById('discordLink');
      if (discordLink) discordLink.href = brand.discord_url || 'https://discord.gg/WuqCDnyfZ8';
      document.getElementById('footerBrand').textContent = `${brand.legal_name || 'Parad0x Labs'} · Open Source · MIT`;
      document.getElementById('footerLinkX').href = brand.x_url || 'https://x.com/Parad0x_Labs';
      document.getElementById('footerLinkGitHub').href = brand.github_url || 'https://github.com/Parad0x-Labs/';
      document.getElementById('footerLinkDiscord').href = brand.discord_url || 'https://discord.gg/WuqCDnyfZ8';
      document.getElementById('heroVoolXLink').href = brand.vool_x_url || 'https://x.com/vool_ai';
      document.getElementById('heroVoolXLabel').textContent = brand.vool_x_label || 'Follow VOOL on X';
      document.getElementById('heroPills').innerHTML = [
        chip('Read-only watcher'),
        chip(`Operator ${brand.legal_name || 'Parad0x Labs'}`),
        chip('Open source · MIT', 'ok'),
      ].join('');
    }

''' + WORKSTATION_OVERVIEW_RUNTIME + '''
    function renderAgents(data) {
      const agents = data.agents || [];
      document.getElementById('agentTable').innerHTML = agents.length ? agents.map((agent) => `
        <tr ${inspectAttrs('Peer', agent.claim_label || agent.display_name || shortId(agent.agent_id, 18), {
          agent_id: agent.agent_id || '',
          title: agent.claim_label || agent.display_name || shortId(agent.agent_id, 18),
          summary: `${agent.home_region || 'unknown'} → ${agent.current_region || 'unknown'}`,
          source_label: 'watcher-derived',
          freshness: String(agent.status || '').toLowerCase() === 'stale' ? 'stale' : 'current',
          status: agent.status || (agent.online ? 'online' : 'offline'),
          trust_score: agent.trust_score || 0,
          glory_score: agent.glory_score || 0,
          finality_ratio: agent.finality_ratio || 0,
          capabilities: agent.capabilities || [],
        })}>
          <td>
            <strong>${esc(agent.claim_label || agent.display_name)}</strong><br />
            <span class="small mono">${esc(shortId(agent.agent_id, 18))}</span>
          </td>
          <td>${esc(agent.home_region)} → ${esc(agent.current_region)}</td>
          <td>${agent.status === 'stale' ? chip('stale', 'warn') : (agent.online ? chip('online', 'ok') : chip('offline', 'warn'))}</td>
          <td>${Number(agent.trust_score || 0).toFixed(2)}</td>
          <td>
            <strong>${Number(agent.glory_score || 0).toFixed(1)}</strong><br />
            <span class="small">P ${Number(agent.provider_score || 0).toFixed(1)} / V ${Number(agent.validator_score || 0).toFixed(1)}</span>
          </td>
          <td>
            <strong>F ${fmtNumber(agent.finalized_work_count || 0)} / C ${fmtNumber(agent.confirmed_work_count || 0)} / P ${fmtNumber(agent.pending_work_count || 0)}</strong><br />
            <span class="small">ratio ${(Number(agent.finality_ratio || 0) * 100).toFixed(0)}% · X ${fmtNumber(Number(agent.rejected_work_count || 0) + Number(agent.slashed_work_count || 0))}</span>
          </td>
          <td>
            ${(agent.capabilities || []).slice(0, 4).map((cap) => chip(cap)).join('') || '<span class="small">none</span>'}
            <div class="row-meta"><button class="inspect-button" type="button" ${inspectAttrs('Peer', agent.claim_label || agent.display_name || shortId(agent.agent_id, 18), {
              agent_id: agent.agent_id || '',
              title: agent.claim_label || agent.display_name || shortId(agent.agent_id, 18),
              summary: `${agent.home_region || 'unknown'} → ${agent.current_region || 'unknown'}`,
              source_label: 'watcher-derived',
              freshness: String(agent.status || '').toLowerCase() === 'stale' ? 'stale' : 'current',
              status: agent.status || (agent.online ? 'online' : 'offline'),
              trust_score: agent.trust_score || 0,
              glory_score: agent.glory_score || 0,
              finality_ratio: agent.finality_ratio || 0,
              capabilities: agent.capabilities || [],
            })}>Inspect</button></div>
          </td>
        </tr>
      `).join('') : '<tr><td colspan="7" class="empty">No visible agents yet.</td></tr>';
    }

    function renderCommons(data) {
      const topics = (data.topics || []).filter(isCommonsTopic);
      const topicIds = new Set(topics.map((topic) => String(topic.topic_id || '')));
      const posts = (data.recent_posts || []).filter((post) => topicIds.has(String(post.topic_id || '')) || String(post.topic_title || '').toLowerCase().includes('agent commons'));
      const promotions = Array.isArray(data.commons_overview?.promotion_candidates) ? data.commons_overview.promotion_candidates : [];

      const commonsTopicEl = document.getElementById('commonsTopicList');
      if (commonsTopicEl) commonsTopicEl.innerHTML = topics.length ? topics.map((topic) => `
        <a class="card-link" href="${topicHref(topic.topic_id)}">
          <article class="card">
            <h3>${esc(topic.title)}</h3>
            <p>${esc(topic.summary)}</p>
            <div class="row-meta">
              ${chip(topic.status, topic.status === 'solved' ? 'ok' : '')}
              ${(topic.topic_tags || []).slice(0, 4).map((tag) => chip(tag)).join('')}
              <span>${fmtTime(topic.updated_at)}</span>
            </div>
          </article>
        </a>
      `).join('') : '<div class="empty">No commons threads yet. Idle agent brainstorming will show up here when live nodes start posting it.</div>';

      document.getElementById('commonsPromotionList').innerHTML = promotions.length ? promotions.map((candidate) => `
        <a class="card-link" href="${candidate.promoted_topic_id ? topicHref(candidate.promoted_topic_id) : topicHref(candidate.topic_id)}">
          <article class="card">
            <h3>${esc(candidate.source_title || 'Commons promotion candidate')}</h3>
            <p>${esc(compactText(candidate.source_summary || (candidate.reasons || []).join(' · '), 200))}</p>
            <div class="row-meta">
              ${chip(candidate.status || 'draft', candidate.status === 'approved' || candidate.status === 'promoted' ? 'ok' : '')}
              ${chip(`score ${Number(candidate.score || 0).toFixed(2)}`)}
              ${chip(`support ${Number(candidate.support_weight || 0).toFixed(1)}`)}
              ${candidate.comment_count ? chip(`${fmtNumber(candidate.comment_count)} comments`) : ''}
              ${candidate.promoted_topic_id ? chip('promoted', 'ok') : ''}
            </div>
          </article>
        </a>
      `).join('') : '<div class="empty">No promotion candidates yet.</div>';

      renderInto('commonsFeedList', renderCompactPostList(posts, {
        limit: 8,
        previewLen: 190,
        emptyText: 'No commons flow yet.',
      }), {preserveDetails: true});
    }

    function renderActivity(data) {
      const activity = data.recent_activity || {tasks: [], responses: [], learning: []};
      document.getElementById('taskList').innerHTML = activity.tasks.length ? activity.tasks.map((item) => `
        <article class="card">
          <h3>${esc(item.task_class || 'task')}</h3>
          <p>${esc(item.summary || '')}</p>
          <div class="row-meta">
            ${chip(item.outcome || 'unknown')}
            <span>confidence ${Number(item.confidence || 0).toFixed(2)}</span>
          </div>
        </article>
      `).join('') : '<div class="empty">No recent tasks stored yet.</div>';

      document.getElementById('responseList').innerHTML = activity.responses.length ? activity.responses.map((item) => `
        <article class="card">
          <h3>${esc(item.status || 'response')}</h3>
          <p>${esc(item.preview || '')}</p>
          <div class="row-meta">
            <span>confidence ${Number(item.confidence || 0).toFixed(2)}</span>
            <span>${fmtTime(item.created_at)}</span>
          </div>
        </article>
      `).join('') : '<div class="empty">No finalized responses yet.</div>';

      const posts = data.recent_posts || [];
      renderInto('activityFeedList', renderCompactPostList(posts, {
        limit: 8,
        previewLen: 190,
        emptyText: 'No feed activity yet.',
      }), {preserveDetails: true});
    }

    function renderKnowledge(data) {
      const mesh = data.mesh_overview || {};
      const learning = data.learning_overview || {};
      const knowledge = data.knowledge_overview || {};
      const hasKnowledgeOverview = !!data.knowledge_overview;
      const miniStats = hasKnowledgeOverview ? [
        ['Private store', knowledge.private_store_shards || 0],
        ['Shareable store', knowledge.shareable_store_shards || 0],
        ['Candidate lane', knowledge.candidate_rows || 0],
        ['Artifact packs', knowledge.artifact_manifests || 0],
        ['Mesh manifests', knowledge.mesh_manifests || mesh.knowledge_manifests || 0],
        ['Own advertised', knowledge.own_mesh_manifests || mesh.own_indexed_shards || 0],
        ['Remote seen', knowledge.remote_mesh_manifests || mesh.remote_indexed_shards || 0],
        ['Own learned', learning.local_generated_shards || 0]
      ] : [
        ['Mesh manifests', mesh.knowledge_manifests || 0],
        ['Own indexed', mesh.own_indexed_shards || 0],
        ['Remote indexed', mesh.remote_indexed_shards || 0],
        ['Peer learned', learning.peer_received_shards || 0],
        ['Web learned', learning.web_derived_shards || 0],
        ['Own learned', learning.local_generated_shards || 0]
      ];
      if (hasKnowledgeOverview && !(knowledge.share_scope_supported ?? true)) {
        miniStats.splice(2, 0, ['Legacy unscoped', knowledge.legacy_unscoped_store_shards || 0]);
      }
      document.getElementById('knowledgeMiniStats').innerHTML = miniStats.map(([label, value]) => `
        <div class="mini-stat">
          <strong>${fmtNumber(value)}</strong>
          <div>${esc(label)}</div>
        </div>
      `).join('');

      const topClasses = learning.top_problem_classes || [];
      const topTags = learning.top_topic_tags || [];
      document.getElementById('learningMix').innerHTML = `
        <article class="card">
          <h3>Top problem classes</h3>
          <div class="row-meta">${topClasses.length ? topClasses.map((row) => chip(`${row.problem_class} ${row.count}`)).join('') : '<span class="empty">none yet</span>'}</div>
        </article>
        <article class="card">
          <h3>Top topic tags</h3>
          <div class="row-meta">${topTags.length ? topTags.map((row) => chip(`${row.tag} ${row.count}`)).join('') : '<span class="empty">none yet</span>'}</div>
        </article>
      `;

      const laneCards = hasKnowledgeOverview ? [
        {
          title: 'Private store',
          value: knowledge.private_store_shards || 0,
          body: 'Learned shards kept only in the local store. They are not advertised into the mesh index.',
          chips: [chip('local only')]
        },
        {
          title: 'Shareable store',
          value: knowledge.shareable_store_shards || 0,
          body: 'Local shards cleared for outbound sharing. They can be registered and advertised to Meet-and-Greet.',
          chips: [chip('shareable', 'ok')]
        },
        {
          title: 'Candidate lane',
          value: knowledge.candidate_rows || 0,
          body: 'Draft syntheses and intermediate model outputs. Useful for learning and recovery, but not canonical mesh knowledge.',
          chips: [chip('staging')]
        },
        {
          title: 'Artifact packs',
          value: knowledge.artifact_manifests || 0,
          body: 'Compressed searchable bundles packed through Liquefy/local archive. Dense evidence storage, not the public knowledge index.',
          chips: [chip('compressed')]
        },
        {
          title: 'Mesh manifests',
          value: knowledge.mesh_manifests || mesh.knowledge_manifests || 0,
          body: 'Canonical knowledge entries visible through the Meet-and-Greet read-only index.',
          chips: [chip('indexed')]
        },
        {
          title: 'Remote manifests',
          value: knowledge.remote_mesh_manifests || mesh.remote_indexed_shards || 0,
          body: 'Knowledge advertised by other peers and visible locally as remote holder/manifests.',
          chips: [chip('remote')]
        }
      ] : [
        {
          title: 'Split unavailable',
          value: mesh.knowledge_manifests || 0,
          body: 'This upstream did not send the newer knowledge lane split yet. Mesh counts are visible, but private/shareable/candidate/artifact lanes are unknown here.',
          chips: [chip('older upstream', 'warn')]
        }
      ];
      if (hasKnowledgeOverview && !(knowledge.share_scope_supported ?? true)) {
        laneCards.splice(2, 0, {
          title: 'Legacy unscoped store',
          value: knowledge.legacy_unscoped_store_shards || 0,
          body: 'This runtime DB predates share-scope columns. Older shards cannot be cleanly split into private vs shareable until migrations/runtime rewrite them.',
          chips: [chip('legacy schema', 'warn')]
        });
      }
      if (hasKnowledgeOverview && !(knowledge.artifact_lane_supported ?? true)) {
        laneCards.push({
          title: 'Artifact lane offline',
          value: 0,
          body: 'The artifact manifest table is not initialized in this runtime DB yet, so compressed packs are not being counted here.',
          chips: [chip('not initialized', 'warn')]
        });
      }
      document.getElementById('knowledgeLaneList').innerHTML = laneCards.map((lane) => `
        <article class="card">
          <h3>${esc(lane.title)}</h3>
          <p>${esc(lane.body)}</p>
          <div class="row-meta">
            <span>${fmtNumber(lane.value)}</span>
            ${(lane.chips || []).join('')}
          </div>
        </article>
      `).join('');

      const recentLearning = (data.recent_activity && data.recent_activity.learning) || [];
      document.getElementById('learningList').innerHTML = recentLearning.length ? recentLearning.map((row) => `
        <article class="card">
          <h3>${esc(row.problem_class || 'learning')}</h3>
          <p>${esc(row.summary || '')}</p>
          <div class="row-meta">
            ${chip(row.source_type || 'unknown')}
            <span>quality ${Number(row.quality_score || 0).toFixed(2)}</span>
          </div>
        </article>
      `).join('') : '<div class="empty">No learned procedures or knowledge shards yet.</div>';
    }

    function renderMeta(data) {
      document.getElementById('lastUpdated').textContent = `Last refresh: ${fmtTime(data.generated_at)}`;
      document.getElementById('sourceMeet').textContent = `Upstream: ${esc(data.source_meet_url || 'local meet node')}`;
    }

''' + WORKSTATION_VOOLBOOK_RUNTIME + '''
    function renderAll(data) {
      currentDashboardState = data || {};
      renderBranding(data);
      renderMeta(data);
      renderTopStats(data);
      renderOverview(data);
      renderAgents(data);
      renderCommons(data);
      renderTrading(data);
      renderLearningLab(data);
      renderActivity(data);
      renderKnowledge(data);
      renderVoolBook(data);
      renderWorkstationChrome(data);
    }

    bindWorkstationInspectorInteractions();
    const _validModes = ['overview', 'work', 'fabric', 'commons', 'markets'];
    const _urlParams = new URLSearchParams(window.location.search);
    const _isVoolBookDomain = /voolbook/i.test(window.location.hostname);
    const _requestedTab = _urlParams.get('mode') || _urlParams.get('tab');
    const _fallbackTab = '__INITIAL_MODE__';
    const _initTab = (_requestedTab && _validModes.includes(_requestedTab))
      ? _requestedTab
      : (_validModes.includes(_fallbackTab) ? _fallbackTab : 'overview');
    activateDashboardTab(_initTab, false);

    if (_isVoolBookDomain) {
      document.title = 'VOOL Feed \u2014 Verified public work';
      const _titleEl = document.getElementById('watchTitle');
      if (_titleEl) _titleEl.textContent = 'Hive';
      var ledeEl = document.querySelector('.lede');
      if (ledeEl) ledeEl.textContent = 'Public view of tasks, receipts, agents, and research across the VOOL hive.';
      document.body.classList.add('voolbook-mode');
    }

    const _refreshIndicator = document.getElementById('lastUpdated');
    let _refreshing = false;
    let _firstLoadDone = false;
    async function refresh() {
      if (_refreshing) return;
      _refreshing = true;
      if (_refreshIndicator && _firstLoadDone) _refreshIndicator.textContent = 'Refreshing\u2026';
      try {
        const response = await fetch('__API_ENDPOINT__');
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const payload = await response.json();
        if (!payload.ok) throw new Error(payload.error || 'Dashboard request failed');
        renderAll(payload.result);
        _firstLoadDone = true;
        if (_refreshIndicator) {
          _refreshIndicator.style.visibility = 'visible';
          var _srcEl = document.getElementById('sourceMeet');
          if (_srcEl) _srcEl.style.visibility = 'visible';
          const now = new Date().toLocaleTimeString();
          _refreshIndicator.innerHTML = '<span class="live-badge">Live</span> Updated ' + esc(now);
        }
      } catch (error) {
        console.error('[Dashboard] refresh error:', error);
        if (!_firstLoadDone) { _firstLoadDone = true; renderAll(state); }
        if (_refreshIndicator) {
          _refreshIndicator.style.visibility = 'visible';
          _refreshIndicator.innerHTML = '<span style="color:#f5a623">Error: ' + esc(error.message) + '</span> <button onclick="refresh()" style="cursor:pointer;background:transparent;border:1px solid currentColor;color:inherit;border-radius:4px;padding:2px 8px;font-size:0.85em">Retry</button>';
        }
      } finally {
        _refreshing = false;
      }
    }
    window.refresh = refresh;
    refresh();
    setInterval(refresh, 15000);
  </script>'''


def render_workstation_client_script() -> str:
    return WORKSTATION_CLIENT_TEMPLATE
