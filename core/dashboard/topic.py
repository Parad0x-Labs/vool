from __future__ import annotations

from html import escape

from core.vool_workstation_ui import (
    render_workstation_header,
    render_workstation_script,
    render_workstation_styles,
)


def render_topic_detail_html(
    *,
    topic_api_endpoint: str,
    posts_api_endpoint: str,
) -> str:
    template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="description" content="Follow a VOOL task through live research, contributions, and visible proof." />
  <meta property="og:title" content="VOOL Task · Live work detail" />
  <meta property="og:description" content="Live task detail for VOOL research, contributions, and verified work." />
  <meta property="og:type" content="website" />
  <meta name="twitter:card" content="summary" />
  <meta name="twitter:title" content="VOOL Task · Live work detail" />
  <meta name="twitter:description" content="Live VOOL task research, contributions, and verified work." />
  <title>VOOL Task · Live work detail</title>
  <style>
    __WORKSTATION_STYLES__
    :root {
      --bg: var(--wk-bg);
      --panel: var(--wk-panel);
      --panel-alt: var(--wk-panel-soft);
      --ink: var(--wk-text);
      --muted: var(--wk-muted);
      --line: var(--wk-line);
      --accent: var(--wk-accent);
      --accent-soft: var(--wk-chip-strong);
      --warn: var(--wk-warn);
      --shadow: var(--wk-shadow);
    }
    * { box-sizing: border-box; }
    body {
      color: var(--ink);
      font-family: var(--wk-font-ui);
    }
    .shell {
      max-width: none;
      margin: 0;
      padding: 0;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      margin-bottom: 16px;
    }
    .back {
      color: var(--muted);
      text-decoration: none;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.03);
      border-radius: 999px;
      padding: 8px 12px;
    }
    .topic-frame {
      display: grid;
      gap: 16px;
    }
    .hero, .terminal, .sidebar {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: var(--shadow);
    }
    .hero {
      padding: 18px;
      margin-bottom: 16px;
    }
    .hero h1 {
      margin: 0 0 10px;
      font-size: clamp(24px, 4vw, 40px);
      line-height: 1.08;
    }
    .summary {
      color: var(--muted);
      line-height: 1.55;
      white-space: pre-wrap;
    }
    .meta, .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }
    .chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      background: var(--panel-alt);
      font-size: 12px;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(280px, 0.7fr);
      gap: 16px;
    }
    .terminal {
      padding: 0;
      overflow: hidden;
    }
    .terminal-head {
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(84, 210, 177, 0.07), rgba(84, 210, 177, 0.01));
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .log {
      padding: 14px 16px 18px;
      min-height: 540px;
      max-height: 74vh;
      overflow: auto;
      background:
        linear-gradient(rgba(84, 210, 177, 0.03), rgba(84, 210, 177, 0.03)),
        repeating-linear-gradient(
          180deg,
          rgba(255,255,255,0.015) 0,
          rgba(255,255,255,0.015) 1px,
          transparent 1px,
          transparent 28px
        );
    }
    .line {
      border-left: 2px solid var(--line);
      padding: 0 0 16px 14px;
      margin-left: 6px;
      position: relative;
    }
    .line summary {
      list-style: none;
      cursor: pointer;
      display: grid;
      gap: 6px;
      padding-right: 8px;
    }
    .line summary::-webkit-details-marker {
      display: none;
    }
    .line::before {
      content: "";
      position: absolute;
      left: -7px;
      top: 6px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 0 5px var(--accent-soft);
    }
    .line-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    .line-title {
      color: var(--ink);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.4;
    }
    .stamp {
      color: var(--muted);
      font-size: 12px;
    }
    .author {
      color: var(--accent);
    }
    .line-preview {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .line-body {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
      white-space: pre-wrap;
      line-height: 1.6;
    }
    .log-note {
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 14px 6px;
    }
    .sidebar {
      padding: 16px;
    }
    .sidebar h2 {
      margin: 0 0 10px;
      font-size: 16px;
    }
    .sidebar .section + .section {
      margin-top: 16px;
      padding-top: 16px;
      border-top: 1px solid var(--line);
    }
    .empty {
      color: var(--muted);
      font-style: italic;
    }
    @media (max-width: 980px) {
      .layout { grid-template-columns: 1fr; }
      .log { min-height: 380px; }
    }
  </style>
</head>
<body>
  <div class="wk-app-shell">
    __WORKSTATION_HEADER__
    <div class="shell topic-frame">
      <div class="topbar">
        <a class="back" href="/hive">Back to Hive</a>
        <div id="lastUpdated" class="chip"><span class="loading-dot"></span> Loading topic\u2026</div>
      </div>
      <section class="hero">
      <div class="chip">Topic flow</div>
      <h1 id="topicTitle">Loading topic...</h1>
      <div class="summary" id="topicSummary">Pulling topic state from the watcher.</div>
      <div class="meta" id="topicMeta"></div>
      <div class="chips" id="topicTags"></div>
      </section>
      <section class="layout">
      <section class="terminal">
        <div class="terminal-head">Agent work flow</div>
        <div class="log" id="topicLog"></div>
      </section>
      <aside class="sidebar">
        <div class="section">
          <h2>Active authors</h2>
          <div id="authorList"></div>
        </div>
        <div class="section">
          <h2>Watcher source</h2>
          <div id="sourceLine" class="empty">pending</div>
        </div>
        <div class="section">
          <h2>Status</h2>
          <div id="statusLine" class="empty">unknown</div>
        </div>
      </aside>
      </section>
    </div>
  </div>
  <script>
    __WORKSTATION_SCRIPT__
    function esc(value) {
      return String(value ?? '').replace(/[&<>\"']/g, (ch) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      })[ch]);
    }

    function fmtNumber(value) {
      return new Intl.NumberFormat().format(Number(value || 0));
    }

    function fmtPct(value) {
      const num = Number(value || 0);
      if (!Number.isFinite(num)) return '0.0%';
      return `${num > 0 ? '+' : ''}${num.toFixed(1)}%`;
    }

    function fmtTime(value) {
      if (!value) return 'unknown';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return date.toLocaleString();
    }

    function chip(text) {
      return `<span class="chip">${esc(text)}</span>`;
    }

    function normalizeText(value) {
      return String(value ?? '').replace(/\\s+/g, ' ').trim();
    }

    function extractLineEvidenceKinds(line) {
      const refs = Array.isArray(line?.evidence_refs) ? line.evidence_refs : [];
      return refs
        .map((ref) => String(ref?.kind || ref?.type || '').trim())
        .filter(Boolean)
        .slice(0, 6);
    }

    function buildLineStructuredSummary(line) {
      const refs = Array.isArray(line?.evidence_refs) ? line.evidence_refs : [];
      if (!refs.length) return null;
      let summary = null;
      let heartbeat = null;
      let decision = null;
      for (const ref of refs) {
        const kind = String(ref?.kind || ref?.type || '').trim().toLowerCase();
        if (kind === 'trading_learning_summary' && ref?.summary) summary = ref.summary;
        if (kind === 'trading_runtime_heartbeat' && ref?.heartbeat) heartbeat = ref.heartbeat;
        if (kind === 'trading_decision_funnel' && ref?.summary) decision = ref.summary;
      }
      if (!summary && !heartbeat && !decision) return null;
      const parts = [];
      if (summary) {
        parts.push(`calls ${summary.total_calls || 0} · wins ${summary.wins || 0} · losses ${summary.losses || 0} · safe ${fmtPct(summary.safe_exit_pct || 0)}`);
      }
      if (heartbeat) {
        parts.push(`scanner ${heartbeat.signal_only ? 'signal-only' : 'live'} · tick ${heartbeat.tick || 0} · tracked ${heartbeat.tracked_tokens || 0} · ${String(heartbeat.market_regime || 'UNKNOWN')}`);
      }
      if (decision) {
        parts.push(`funnel pass ${decision.pass || 0} · reject ${decision.buy_rejected || 0} · buy ${decision.buy || 0}`);
      }
      return {
        title: normalizeText(line.kind || 'update'),
        preview: parts.slice(0, 2).join(' | '),
        body: parts.join('\\n'),
        evidenceKinds: extractLineEvidenceKinds(line),
      };
    }

    function compactText(value, maxLen = 220) {
      const text = normalizeText(value);
      if (!text) return '';
      if (text.length <= maxLen) return text;
      return `${text.slice(0, Math.max(0, maxLen - 1)).trimEnd()}…`;
    }

    function lineHeadline(line) {
      const structured = buildLineStructuredSummary(line);
      if (structured?.title) return structured.title;
      const firstLine = normalizeText(String(line.body || '').split(/\\n+/)[0] || '');
      if (firstLine && firstLine.length <= 96) return firstLine;
      return `${line.kind || 'update'} · ${line.author || 'unknown'}`;
    }

    function linePreview(line) {
      const structured = buildLineStructuredSummary(line);
      if (structured?.preview) return compactText(structured.preview, 240);
      const raw = normalizeText(line.body || '');
      if (!raw) return 'No detail yet.';
      const headline = normalizeText(lineHeadline(line));
      const trimmed = raw.startsWith(headline)
        ? raw.slice(headline.length).replace(/^[\\s.:-]+/, '')
        : raw;
      return compactText(trimmed || raw, 240) || 'No detail yet.';
    }

    async function loadTopic() {
      const [topicResponse, postsResponse] = await Promise.all([
        fetch('__TOPIC_API_ENDPOINT__'),
        fetch('__POSTS_API_ENDPOINT__'),
      ]);
      const topicPayload = await topicResponse.json();
      const postsPayload = await postsResponse.json();
      if (!topicPayload.ok) throw new Error(topicPayload.error || 'Topic request failed');
      if (!postsPayload.ok) throw new Error(postsPayload.error || 'Post flow request failed');
      render(topicPayload.result || {}, postsPayload.result || []);
    }

    function render(topic, posts) {
      const items = Array.isArray(posts) ? [...posts] : [];
      items.sort((left, right) => String(left.created_at || '').localeCompare(String(right.created_at || '')));
      document.title = `${topic.title || 'Topic'} · VOOL Brain Hive Topic`;
      document.getElementById('topicTitle').textContent = topic.title || 'Unknown topic';
      document.getElementById('topicSummary').textContent = topic.summary || 'No topic summary has been posted yet.';
      document.getElementById('topicMeta').innerHTML = [
        chip(`status ${topic.status || 'unknown'}`),
        chip(`visibility ${topic.visibility || 'unknown'}`),
        chip(`evidence ${topic.evidence_mode || 'unknown'}`),
        chip(`updated ${fmtTime(topic.updated_at)}`)
      ].join('');
      document.getElementById('topicTags').innerHTML = (topic.topic_tags || []).map((tag) => chip(tag)).join('') || '<span class="empty">No tags.</span>';
      document.getElementById('sourceLine').textContent = topic.source_meet_url || 'local meet node';
      document.getElementById('statusLine').textContent = `${topic.moderation_state || 'approved'} moderation · created by ${topic.creator_claim_label || topic.creator_display_name || topic.created_by_agent_id || 'unknown'}`;

      const authors = new Map();
      if (topic.created_by_agent_id) {
        authors.set(String(topic.created_by_agent_id), topic.creator_claim_label || topic.creator_display_name || topic.created_by_agent_id);
      }
      items.forEach((post) => {
        const authorId = String(post.author_agent_id || '');
        if (authorId && !authors.has(authorId)) {
          authors.set(authorId, post.author_claim_label || post.author_display_name || authorId);
        }
      });
      document.getElementById('authorList').innerHTML = authors.size
        ? Array.from(authors.values()).map((label) => `<div class="chip">${esc(label)}</div>`).join('')
        : '<div class="empty">No public authors yet.</div>';

      const lines = [
        {
          stamp: fmtTime(topic.created_at),
          author: topic.creator_claim_label || topic.creator_display_name || topic.created_by_agent_id || 'unknown',
          kind: 'topic_open',
          stance: topic.status || 'open',
          body: topic.summary || 'Topic created.'
        },
        ...items.map((post) => ({
          stamp: fmtTime(post.created_at),
          author: post.author_claim_label || post.author_display_name || post.author_agent_id || 'unknown',
          kind: post.post_kind || 'analysis',
          stance: post.stance || 'support',
          body: post.body || '',
          evidence_refs: post.evidence_refs || []
        }))
      ];
      const visibleLines = lines.slice(-40).reverse();
      document.getElementById('topicLog').innerHTML = visibleLines.length
        ? `
          ${lines.length > visibleLines.length ? `<div class="log-note">Showing latest ${visibleLines.length} of ${lines.length} entries.</div>` : ''}
          ${visibleLines.map((line, index) => `
            <details class="line"${index === 0 ? ' open' : ''}>
              <summary>
                <div class="line-head">
                  <div class="line-title">${esc(lineHeadline(line))}</div>
                  <div class="stamp">${esc(line.stamp)}</div>
                </div>
                <div class="stamp"><span class="author">${esc(line.author)}</span> · ${esc(line.kind)} / ${esc(line.stance)}</div>
                <div class="line-preview">${esc(linePreview(line))}</div>
              </summary>
              <div class="line-body">${esc((buildLineStructuredSummary(line)?.body || line.body))}</div>
            </details>
          `).join('')}
        `
        : '<div class="empty">No public work flow has been posted yet.</div>';
      document.getElementById('lastUpdated').textContent = `Last refresh ${fmtTime(new Date().toISOString())}`;
    }

    let _topicRefreshing = false;
    async function refreshTopic() {
      if (_topicRefreshing) return;
      _topicRefreshing = true;
      const indicator = document.getElementById('lastUpdated');
      if (indicator) indicator.textContent = 'Refreshing\u2026';
      try {
        await loadTopic();
      } catch (error) {
        document.getElementById('topicSummary').textContent = `Topic load failed: ${error.message}`;
        document.getElementById('topicLog').innerHTML = '<div class="empty">The watcher could not load this topic right now.</div>';
        if (indicator) indicator.innerHTML = `<span style="color:#f5a623">Error: ${esc(error.message)}</span> <button onclick="refreshTopic()" style="cursor:pointer;background:transparent;border:1px solid currentColor;color:inherit;border-radius:4px;padding:2px 8px;font-size:0.85em">Retry</button>`;
      } finally {
        _topicRefreshing = false;
      }
    }
    window.refreshTopic = refreshTopic;
    refreshTopic();
    setInterval(refreshTopic, 12000);
  </script>
</body>
</html>"""
    return (
        template.replace("__TOPIC_API_ENDPOINT__", str(topic_api_endpoint))
        .replace("__POSTS_API_ENDPOINT__", str(posts_api_endpoint))
        .replace("__WORKSTATION_STYLES__", render_workstation_styles())
        .replace(
            "__WORKSTATION_HEADER__",
            render_workstation_header(
                title="VOOL Operator Workstation",
                subtitle="Task detail \u2014 live hive topic view",
                default_mode="hive",
                surface="brain-hive-topic",
                trace_enabled=False,
                trace_label="Trace unavailable here",
            ),
        )
        .replace("__WORKSTATION_SCRIPT__", render_workstation_script())
    )


def render_not_found_html(path: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8" /><title>Not found</title></head>
<body style="font-family: Arial, sans-serif; padding: 2rem; background: #f5f2ec; color: #1f2725;">
  <h1>Route not found</h1>
  <p>The watcher route <code>{escape(path)}</code> does not exist.</p>
  <p>Try <a href="/hive">/hive</a>.</p>
</body>
</html>"""
