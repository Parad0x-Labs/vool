RUNTIME_TASK_RAIL_SHELL_HTML = """
  <div class="wk-app-shell">
    __WORKSTATION_HEADER__
    <div class="trace-workbench">
      <aside class="panel trace-rail-shell">
        <div class="trace-rail-stack">
          <section class="trace-rail-section">
            <div class="panel-header">
              <div class="eyebrow">Session rail</div>
              <div class="title-row">
                <div class="title">VOOL Task Rail</div>
                <div class="status-pill" id="pollStatus">polling</div>
              </div>
              <div class="subtitle">
                Runtime causality rail for sessions, claims, retries, artifacts, and stop reasons.
              </div>
              <div class="link-line">Live URL: <code>http://127.0.0.1:11435/trace</code></div>
            </div>
            <div class="session-list" id="sessionList">
              <div class="empty-state">Waiting for recent sessions...</div>
            </div>
          </section>
          <section class="trace-rail-section">
            <div class="wk-panel-eyebrow">Execution rail</div>
            <div class="trace-strip" id="traceStrip">
              <div class="empty-state">No process rail yet.</div>
            </div>
          </section>
        </div>
      </aside>

      <main class="panel rail-body trace-center-shell">
        <section class="trace-stage-head">
          <div>
            <div class="wk-panel-eyebrow">Selected-step center</div>
            <h2>Trace workstation v1</h2>
            <p class="trace-stage-copy">What happened, why it happened, what changed, what failed, how often it retried, and why it stopped now live in one central execution view.</p>
          </div>
          <div class="trace-stage-proof">
            <span class="wk-proof-chip wk-proof-chip--primary">workstation v1</span>
            <span class="wk-proof-chip">session rail</span>
            <span class="wk-proof-chip">selected-step center</span>
            <span class="wk-proof-chip">session summary</span>
          </div>
        </section>
        <section class="detail-block detail-main" id="sessionDetail">
          <h2>No session selected</h2>
          <p>Run a task through OpenClaw. Recent sessions and their causal rails will appear here.</p>
        </section>
        <section class="detail-block trace-selected-step">
          <div class="wk-panel-eyebrow">Selected step</div>
          <h3 id="selectedStepTitle">No step selected</h3>
          <p id="selectedStepBody">Pick a runtime event or let the latest step stay in focus.</p>
          <div class="trace-selected-meta" id="selectedStepMeta"></div>
        </section>
        <section class="feed-grid trace-feed-grid">
          <section class="event-feed" id="eventFeed">
            <div class="empty-state">No runtime events yet.</div>
          </section>
        </section>
      </main>

      <aside class="panel trace-summary-shell">
        <div class="trace-summary-stack">
          <section class="trace-summary-section trace-human">
            <div class="wk-panel-eyebrow">Session summary</div>
            <div class="summary-grid" id="summaryGrid">
              <div class="empty-state">No session summary yet.</div>
            </div>
          </section>
          <section class="trace-summary-section trace-human" data-human-optional="1">
            <div class="wk-panel-eyebrow">Why / stop</div>
            <div class="ops-grid" id="opsGrid">
              <div class="empty-state">No stop, failure, or retry state yet.</div>
            </div>
          </section>
          <section class="trace-summary-section trace-agent" data-agent-optional="1">
            <div class="wk-panel-eyebrow">Agent pack</div>
            <div class="meta-row" id="metaRow"></div>
          </section>
          <section class="trace-summary-section">
            <div class="inspector-card">
              <h3>Focus</h3>
              <div class="inspector-list" id="focusList">
                <div class="inspector-item">No topic or claim selected yet.</div>
              </div>
            </div>
          </section>
          <section class="trace-summary-section">
            <div class="inspector-card">
              <h3>Artifacts</h3>
              <div class="inspector-list" id="artifactList">
                <div class="inspector-item">No packed artifacts yet.</div>
              </div>
            </div>
          </section>
          <section class="trace-summary-section">
            <div class="inspector-card">
              <h3>Retries / Queries</h3>
              <div class="inspector-list" id="queryList">
                <div class="inspector-item">No query runs yet.</div>
              </div>
            </div>
          </section>
          <pre class="trace-raw-panel" id="traceRawPanel"></pre>
        </div>
      </aside>
    </div>
  </div>
"""
