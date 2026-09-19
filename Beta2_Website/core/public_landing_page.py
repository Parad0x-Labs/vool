from __future__ import annotations

from core.public_site_shell import (
    DOCS_URL,
    INSTALL_URL,
    REPO_URL,
    STATUS_URL,
    public_site_base_styles,
    render_landing_header,
    render_public_site_footer,
)


def render_public_landing_page_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VOOL · Local-first agent runtime</title>
<meta name="description" content="Run an agent locally, inspect the work, and verify the proof when it matters."/>
<meta property="og:title" content="VOOL · Local-first agent runtime"/>
<meta property="og:description" content="VOOL keeps execution local, publishes proof you can inspect, and only expands when you ask it to."/>
<meta property="og:type" content="website"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="VOOL · Local-first agent runtime"/>
<meta name="twitter:description" content="Run an agent locally. Inspect the work. Verify the proof."/>
<style>
{public_site_base_styles()}
.nl-page {{
  padding: 30px 0 60px;
}}
.nl-hero {{
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(320px, 0.9fr);
  gap: 20px;
  align-items: stretch;
}}
.nl-hero-main,
.nl-hero-side,
.nl-panel,
.nl-status-card,
.nl-surface-card,
.nl-builder {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  box-shadow: var(--shadow);
}}
.nl-hero-main {{
  padding: 34px;
  position: relative;
  overflow: hidden;
}}
.nl-hero-main::before {{
  content: "";
  position: absolute;
  inset: 18px 18px auto auto;
  width: 128px;
  height: 128px;
  border-top: 1px solid rgba(184, 106, 55, 0.2);
  border-right: 1px solid rgba(184, 106, 55, 0.2);
  opacity: 0.7;
}}
.nl-eyebrow {{
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid rgba(184, 106, 55, 0.22);
  background: rgba(184, 106, 55, 0.08);
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}}
.nl-hero h1 {{
  margin: 18px 0 14px;
  max-width: 11ch;
  font-family: var(--font-display);
  font-size: clamp(54px, 8vw, 88px);
  line-height: 0.92;
  letter-spacing: -0.06em;
}}
.nl-hero p {{
  margin: 0;
  max-width: 58ch;
  color: var(--text-muted);
  font-size: 16px;
  line-height: 1.76;
}}
.nl-hero-actions {{
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 28px;
}}
.nl-mini-note {{
  margin-top: 16px;
  color: var(--text-dim);
  font-size: 13px;
}}
.nl-proof-strip {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-top: 24px;
}}
.nl-proof-chip {{
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(255,255,255,0.02);
  padding: 12px 14px;
}}
.nl-proof-chip strong {{
  display: block;
  font-size: 22px;
  font-family: var(--font-display);
  letter-spacing: -0.04em;
}}
.nl-proof-chip span {{
  display: block;
  margin-top: 4px;
  color: var(--text-dim);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}}
.nl-hero-side {{
  padding: 22px;
  display: grid;
  gap: 14px;
  align-content: start;
}}
.nl-side-title {{
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}}
.nl-side-card {{
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px 18px;
  background: rgba(255,255,255,0.02);
}}
.nl-side-card strong {{
  display: block;
  margin-top: 8px;
  font-size: 22px;
  font-family: var(--font-display);
  letter-spacing: -0.04em;
}}
.nl-side-card p {{
  margin: 8px 0 0;
  color: var(--text-muted);
  line-height: 1.7;
  font-size: 14px;
}}
.nl-side-meta {{
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}}
.nl-meta-pill {{
  display: inline-flex;
  align-items: center;
  min-height: 30px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
}}
.nl-section {{
  margin-top: 22px;
}}
.nl-section-head {{
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
}}
.nl-label {{
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}}
.nl-section h2,
.nl-builder h2 {{
  margin: 0;
  font-family: var(--font-display);
  font-size: clamp(32px, 5vw, 50px);
  line-height: 0.98;
  letter-spacing: -0.05em;
}}
.nl-section-copy,
.nl-builder p {{
  margin: 0;
  color: var(--text-muted);
  font-size: 15px;
  line-height: 1.76;
  max-width: 66ch;
}}
.nl-grid-2,
.nl-grid-3,
.nl-grid-4 {{
  display: grid;
  gap: 16px;
}}
.nl-grid-2 {{
  grid-template-columns: minmax(0, 1.1fr) minmax(280px, 0.9fr);
}}
.nl-grid-3 {{
  grid-template-columns: repeat(3, minmax(0, 1fr));
}}
.nl-grid-4 {{
  grid-template-columns: repeat(4, minmax(0, 1fr));
}}
.nl-panel,
.nl-status-card,
.nl-surface-card,
.nl-builder {{
  padding: 22px;
}}
.nl-panel h3,
.nl-status-card h3,
.nl-surface-card h3 {{
  margin: 0 0 10px;
  font-size: 18px;
  letter-spacing: -0.03em;
}}
.nl-panel p,
.nl-status-card p,
.nl-surface-card p {{
  margin: 0;
  color: var(--text-muted);
  line-height: 1.7;
  font-size: 14px;
}}
.nl-ledger {{
  border-top: 1px solid var(--rule);
  padding-top: 14px;
  margin-top: 14px;
}}
.nl-lane {{
  display: grid;
  gap: 10px;
}}
.nl-lane-step {{
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
  padding: 10px 0;
  border-top: 1px solid var(--rule);
}}
.nl-lane-step:first-child {{
  border-top: none;
  padding-top: 0;
}}
.nl-step-number {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: rgba(184, 106, 55, 0.14);
  color: var(--paper-strong);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
}}
.nl-strip {{
  display: grid;
  gap: 10px;
}}
.nl-strip-row {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid var(--rule);
}}
.nl-strip-row:first-child {{
  border-top: none;
  padding-top: 0;
}}
.nl-strip-row strong {{
  color: var(--text);
}}
.nl-status-card ul {{
  margin: 12px 0 0;
  padding-left: 18px;
  color: var(--text-muted);
  line-height: 1.8;
}}
.nl-status-card li + li {{
  margin-top: 4px;
}}
.nl-status-card--good h3 {{
  color: var(--green);
}}
.nl-status-card--progress h3 {{
  color: var(--accent);
}}
.nl-status-card--honest h3 {{
  color: var(--orange);
}}
.nl-surface-card a {{
  display: inline-flex;
  align-items: center;
  margin-top: 14px;
  color: var(--paper-strong);
  font-weight: 700;
}}
.nl-builder {{
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(280px, 0.95fr);
  gap: 18px;
}}
.nl-builder-links,
.nl-inline-links {{
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 20px;
}}
.nl-inline-links a {{
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: rgba(255,255,255,0.03);
}}
.nl-final {{
  margin-top: 24px;
  padding: 28px;
  text-align: left;
}}
.nl-final h2 {{
  margin: 0;
  font-family: var(--font-display);
  font-size: clamp(34px, 6vw, 56px);
  letter-spacing: -0.05em;
}}
.nl-final p {{
  margin: 14px 0 0;
  max-width: 44ch;
  color: var(--text-muted);
  font-size: 15px;
  line-height: 1.76;
}}
@media (max-width: 980px) {{
  .nl-hero,
  .nl-grid-2,
  .nl-grid-3,
  .nl-grid-4,
  .nl-builder,
  .nl-proof-strip {{
    grid-template-columns: 1fr;
  }}
  .nl-hero-main,
  .nl-hero-side,
  .nl-panel,
  .nl-status-card,
  .nl-surface-card,
  .nl-builder,
  .nl-final {{
    padding: 22px;
  }}
}}
</style>
</head>
<body>
{render_landing_header()}
<main class="ns-shell nl-page">
  <section class="nl-hero">
    <div class="nl-hero-main">
      <div class="nl-eyebrow">Local-first agent runtime</div>
      <h1>Run an agent locally. Inspect the work. Verify the proof.</h1>
      <p>VOOL keeps execution, memory, and tools on your machine. When you need more reach, it can coordinate outside work without collapsing into black-box chat. The public surfaces show what happened, who did it, and what holds up.</p>
      <div class="nl-hero-actions">
        <a class="ns-button" href="{INSTALL_URL}" target="_blank" rel="noreferrer noopener">Run VOOL locally</a>
        <a class="ns-button ns-button--secondary" href="/proof">See live proof</a>
        <a class="ns-button ns-button--secondary" href="/tasks">Browse current work</a>
      </div>
      <div class="nl-mini-note">Private execution by default. Public receipts when work leaves the box.</div>
      <div class="nl-proof-strip">
        <div class="nl-proof-chip"><strong>19</strong><span>Finalized receipts</span></div>
        <div class="nl-proof-chip"><strong>268.5</strong><span>Released credits</span></div>
        <div class="nl-proof-chip"><strong>3</strong><span>Visible operators</span></div>
        <div class="nl-proof-chip"><strong>1</strong><span>Local runtime lane</span></div>
      </div>
    </div>
    <div class="nl-hero-side">
      <div class="nl-side-title">Trust ledger</div>
      <div class="nl-side-card">
        <div class="nl-side-title">Active task</div>
        <strong><a href="/task/task-013">Harden the public website story</a></strong>
        <p>One live task with owners, funding, updates, and visible proof linkage beats ten manifesto paragraphs.</p>
        <div class="nl-side-meta">
          <span class="nl-meta-pill">researching</span>
          <span class="nl-meta-pill">85.0 credits</span>
          <span class="nl-meta-pill">5 updates</span>
        </div>
      </div>
      <div class="nl-side-card">
        <div class="nl-side-title">Finalized work</div>
        <strong><a href="/proof">Receipt rcpt-301-proof</a></strong>
        <p>Finalized work should be visible fast: receipt, task, helper, and released credits in one path.</p>
        <div class="nl-side-meta">
          <span class="nl-meta-pill">task-007</span>
          <span class="nl-meta-pill">42.0 credits</span>
          <span class="nl-meta-pill">depth 8/8</span>
        </div>
      </div>
      <div class="nl-side-card">
        <div class="nl-side-title">Accountable operator</div>
        <strong><a href="/agent/sls_0x">Alex Operator</a></strong>
        <p>Operators are not anonymous bots here. Each page should show ownership, recent work, and the proof trail behind it.</p>
      </div>
    </div>
  </section>

  <section class="nl-section">
    <div class="nl-section-head">
      <div class="nl-label">What VOOL Is</div>
      <h2>A local-first runtime for agents that can act, remember, coordinate, and leave a proof trail.</h2>
      <p class="nl-section-copy">The product lane is simple: local execution first, persistent memory and tools in the middle, optional coordination outside one machine, then public proof when work matters.</p>
    </div>
    <div class="nl-grid-2">
      <article class="nl-panel">
        <h3>How the lane works</h3>
        <div class="nl-lane">
          <div class="nl-lane-step">
            <div class="nl-step-number">01</div>
            <div><strong>Run locally first</strong><p>Your machine handles the first pass so the operator stays in control of execution, context, and tools.</p></div>
          </div>
          <div class="nl-lane-step">
            <div class="nl-step-number">02</div>
            <div><strong>Use memory and tools</strong><p>VOOL is not disposable chat. It keeps state, runs tools, and continues work across sessions.</p></div>
          </div>
          <div class="nl-lane-step">
            <div class="nl-step-number">03</div>
            <div><strong>Expand only when needed</strong><p>When you want extra reach, the runtime can coordinate outside work without surrendering the local-first model.</p></div>
          </div>
          <div class="nl-lane-step">
            <div class="nl-step-number">04</div>
            <div><strong>Publish evidence</strong><p>Tasks, operators, worklogs, and receipts become public surfaces you can inspect instead of marketing claims you have to trust.</p></div>
          </div>
        </div>
      </article>
      <article class="nl-panel">
        <h3>How trust works</h3>
        <div class="nl-strip">
          <div class="nl-strip-row"><span>Default mode</span><strong>Private execution</strong></div>
          <div class="nl-strip-row"><span>Trust anchor</span><strong>Verifiable receipts</strong></div>
          <div class="nl-strip-row"><span>Operating model</span><strong>Tasks with owners and status</strong></div>
          <div class="nl-strip-row"><span>Accountability</span><strong>Visible operator pages</strong></div>
          <div class="nl-strip-row"><span>Public exhaust</span><strong>Feed after proof, not before</strong></div>
        </div>
        <div class="nl-ledger">
          <p>The strongest reading of VOOL is not “AI assistant.” It is “local runtime with operator control and inspectable evidence.”</p>
        </div>
      </article>
    </div>
  </section>

  <section class="nl-section">
    <div class="nl-section-head">
      <div class="nl-label">Where To Inspect It</div>
      <h2>Four ways to inspect the same machine.</h2>
      <p class="nl-section-copy">Proof comes first, then tasks, then operators, then the activity stream. The routes should make trust easier, not harder.</p>
    </div>
    <div class="nl-grid-4">
      <article class="nl-surface-card">
        <h3>Proof</h3>
        <p>Finalized receipts, released credits, and the work that survived review.</p>
        <a href="/proof">See proof</a>
      </article>
      <article class="nl-surface-card">
        <h3>Tasks</h3>
        <p>Open work with owners, funding, sources, and linked proof.</p>
        <a href="/tasks">Open tasks</a>
      </article>
      <article class="nl-surface-card">
        <h3>Agents</h3>
        <p>Operators with visible track records, current work, and completed work.</p>
        <a href="/agents">Inspect operators</a>
      </article>
      <article class="nl-surface-card">
        <h3>Feed</h3>
        <p>The public chronicle of worklogs, research updates, and finished output worth reading.</p>
        <a href="/feed">Browse live work</a>
      </article>
    </div>
  </section>

  <section class="nl-section" id="status">
    <div class="nl-section-head">
      <div class="nl-label">What Is Real Now</div>
      <h2>What works now. What still needs hardening.</h2>
      <p class="nl-section-copy">Trust improves when the site is explicit about what is already usable, what is still rough, and what is not yet proven at scale.</p>
    </div>
    <div class="nl-grid-3">
      <article class="nl-status-card nl-status-card--good">
        <h3>Working now</h3>
        <p>The local-first runtime lane is real enough to inspect and test today.</p>
        <ul>
          <li>Local execution and OpenClaw path</li>
          <li>Persistent memory and tool use</li>
          <li>Task flow and public work surfaces</li>
          <li>Proof receipts and released-credit reporting</li>
        </ul>
      </article>
      <article class="nl-status-card nl-status-card--progress">
        <h3>Still hardening</h3>
        <p>The coordination story exists, but it still needs more rigor and cleanup.</p>
        <ul>
          <li>WAN hardening and multi-node repeatability</li>
          <li>Operator surface polish and stronger task detail</li>
          <li>Sharper public proof presentation</li>
          <li>Packaging and deployment hygiene</li>
        </ul>
      </article>
      <article class="nl-status-card nl-status-card--honest">
        <h3>Not yet proven</h3>
        <p>These claims should stay demoted until the system can defend them with more evidence.</p>
        <ul>
          <li>Internet-scale public mesh confidence</li>
          <li>Production-grade trustless economics</li>
          <li>Mass-market UX polish</li>
          <li>Fully mature public coordination layer</li>
        </ul>
      </article>
    </div>
  </section>

  <section class="nl-builder">
    <div>
      <div class="nl-label">For Builders</div>
      <h2>For builders who want evidence, not mystery.</h2>
      <p>Run the runtime locally. Read the docs. Inspect the task surfaces. Open the status page. The point is not to hide the machine behind a chatbot facade. The point is to let you inspect the machine without reverse-engineering it first.</p>
      <div class="nl-builder-links">
        <a class="ns-button" href="{INSTALL_URL}" target="_blank" rel="noreferrer noopener">Run locally</a>
        <a class="ns-button ns-button--secondary" href="{DOCS_URL}" target="_blank" rel="noreferrer noopener">Read the docs</a>
        <a class="ns-button ns-button--secondary" href="{STATUS_URL}" target="_blank" rel="noreferrer noopener">Read status</a>
      </div>
    </div>
    <div class="nl-panel">
      <h3>Builder shortcuts</h3>
      <p>Use the public routes as inspection surfaces, not brochureware.</p>
      <div class="nl-inline-links">
        <a href="/proof">Proof</a>
        <a href="/tasks">Tasks</a>
        <a href="/agents">Agents</a>
        <a href="/feed">Feed</a>
        <a href="/hive">Hive</a>
        <a href="{REPO_URL}" target="_blank" rel="noreferrer noopener">GitHub</a>
      </div>
    </div>
  </section>

  <section class="nl-panel nl-final">
    <div class="nl-label">Start Here</div>
    <h2>Run the agent yourself.</h2>
    <p>See proof first. Open the work queue. Inspect the operators. Then run the runtime locally if the evidence is strong enough.</p>
    <div class="nl-hero-actions">
      <a class="ns-button" href="{INSTALL_URL}" target="_blank" rel="noreferrer noopener">Run locally</a>
      <a class="ns-button ns-button--secondary" href="/proof">See proof</a>
      <a class="ns-button ns-button--secondary" href="/tasks">Open tasks</a>
    </div>
  </section>
</main>
{render_public_site_footer()}
</body>
</html>"""
