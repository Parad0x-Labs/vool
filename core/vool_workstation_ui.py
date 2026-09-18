from __future__ import annotations

from html import escape

VOOL_WORKSTATION_DEPLOYMENT_VERSION = "vool-workstation-2026-03-13-v1"


_WORKSTATION_STYLES = """
  :root {
    --wk-bg: #060a12;
    --wk-bg-alt: #0a111d;
    --wk-panel: rgba(10, 16, 28, 0.94);
    --wk-panel-strong: rgba(15, 24, 40, 0.98);
    --wk-panel-soft: rgba(17, 27, 44, 0.82);
    --wk-line: rgba(144, 164, 209, 0.14);
    --wk-line-strong: rgba(144, 164, 209, 0.26);
    --wk-text: #edf2ff;
    --wk-muted: #97a6c6;
    --wk-accent: #61dafb;
    --wk-accent-strong: #2fc0ea;
    --wk-warn: #f5b25c;
    --wk-bad: #ff6d7e;
    --wk-good: #5fe5a6;
    --wk-chip: rgba(255, 255, 255, 0.05);
    --wk-chip-strong: rgba(97, 218, 251, 0.12);
    --wk-shadow: 0 24px 64px rgba(0, 0, 0, 0.38);
    --wk-radius: 18px;
    --wk-radius-lg: 22px;
    --wk-font-ui: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
    --wk-font-mono: "SFMono-Regular", "Cascadia Code", "JetBrains Mono", monospace;
  }

  body {
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(circle at top left, rgba(97, 218, 251, 0.08), transparent 22%),
      radial-gradient(circle at top right, rgba(245, 178, 92, 0.08), transparent 20%),
      linear-gradient(180deg, var(--wk-bg) 0%, var(--wk-bg-alt) 100%);
    color: var(--wk-text);
    font-family: var(--wk-font-ui);
  }

  .wk-app-shell {
    width: 100%;
    max-width: none;
    margin: 0;
    padding: 18px clamp(14px, 1.6vw, 28px) 32px;
  }

  .wk-topbar {
    display: grid;
    grid-template-columns: minmax(260px, 1.2fr) auto auto;
    gap: 16px;
    align-items: center;
    padding: 14px 18px;
    border: 1px solid var(--wk-line);
    border-radius: var(--wk-radius-lg);
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.01)),
      var(--wk-panel-strong);
    box-shadow: var(--wk-shadow);
    margin-bottom: 18px;
  }

  .wk-brand {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 14px;
    align-items: center;
  }

  .wk-brand-mark {
    width: 42px;
    height: 42px;
    border-radius: 12px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(135deg, rgba(97, 218, 251, 0.16), rgba(47, 192, 234, 0.04));
    border: 1px solid rgba(97, 218, 251, 0.2);
    color: var(--wk-accent);
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    font-weight: 700;
  }

  .wk-brand-title {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1.1;
  }

  .wk-brand-subtitle {
    margin-top: 4px;
    color: var(--wk-muted);
    font-size: 13px;
    line-height: 1.45;
  }

  .wk-brand-proof {
    margin-top: 8px;
    display: none;
    flex-wrap: wrap;
    gap: 8px;
  }
  body[data-view-mode="agent"] .wk-brand-proof,
  body[data-view-mode="raw"] .wk-brand-proof {
    display: flex;
  }

  .wk-proof-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border-radius: 999px;
    padding: 5px 9px;
    border: 1px solid var(--wk-line);
    background: rgba(255, 255, 255, 0.03);
    color: var(--wk-muted);
    font-size: 11px;
    line-height: 1;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: var(--wk-font-mono);
  }

  .wk-proof-chip--primary {
    border-color: rgba(97, 218, 251, 0.34);
    background: rgba(97, 218, 251, 0.12);
    color: var(--wk-accent);
  }

  .wk-mode-nav,
  .wk-view-toggle {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .wk-mode-link,
  .wk-view-button {
    border: 1px solid var(--wk-line);
    background: rgba(255, 255, 255, 0.03);
    color: var(--wk-muted);
    border-radius: 999px;
    padding: 9px 12px;
    font-size: 12px;
    line-height: 1;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    text-decoration: none;
    cursor: pointer;
    transition: border-color 0.14s ease, color 0.14s ease, background 0.14s ease, transform 0.14s ease;
  }

  .wk-mode-link:hover,
  .wk-mode-link:focus-visible,
  .wk-view-button:hover,
  .wk-view-button:focus-visible {
    border-color: rgba(97, 218, 251, 0.34);
    color: var(--wk-text);
    outline: none;
    transform: translateY(-1px);
  }

  .wk-mode-link.is-active,
  .wk-view-button.is-active {
    border-color: rgba(97, 218, 251, 0.4);
    background: var(--wk-chip-strong);
    color: var(--wk-accent);
  }

  .wk-mode-link.is-disabled {
    opacity: 0.58;
    cursor: default;
    pointer-events: none;
    border-style: dashed;
  }

  .wk-mode-link[data-workstation-fabric] {
    display: none;
  }

  .wk-layout {
    display: grid;
    gap: 16px;
    align-items: start;
  }

  .wk-layout--hive {
    grid-template-columns: 280px minmax(0, 1fr) 340px;
  }

  .wk-layout--trace {
    grid-template-columns: 340px minmax(0, 1fr) 340px;
  }

  .wk-panel {
    background:
      linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.01)),
      var(--wk-panel);
    border: 1px solid var(--wk-line);
    border-radius: var(--wk-radius);
    box-shadow: var(--wk-shadow);
  }

  .wk-rail,
  .wk-inspector {
    padding: 16px;
  }

  .wk-panel-eyebrow {
    margin-bottom: 8px;
    color: var(--wk-accent);
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
  }

  .wk-panel-title {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 10px;
  }

  .wk-panel-copy {
    color: var(--wk-muted);
    font-size: 13px;
    line-height: 1.55;
    margin: 0;
  }

  .wk-rail-group + .wk-rail-group,
  .wk-inspector-group + .wk-inspector-group {
    margin-top: 14px;
    padding-top: 14px;
    border-top: 1px solid var(--wk-line);
  }

  .wk-rail-label,
  .wk-inspector-label {
    margin-bottom: 8px;
    color: var(--wk-muted);
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }

  .wk-chip-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .wk-chip,
  .wk-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border-radius: 999px;
    padding: 6px 9px;
    font-size: 11px;
    line-height: 1;
    border: 1px solid var(--wk-line);
    background: var(--wk-chip);
    color: var(--wk-muted);
  }

  .wk-badge--source,
  .wk-badge--fresh {
    border-color: rgba(97, 218, 251, 0.22);
    color: var(--wk-accent);
  }

  .wk-badge--warn {
    border-color: rgba(245, 178, 92, 0.28);
    color: var(--wk-warn);
  }

  .wk-badge--bad {
    border-color: rgba(255, 109, 126, 0.28);
    color: var(--wk-bad);
  }

  .wk-badge--good {
    border-color: rgba(95, 229, 166, 0.28);
    color: var(--wk-good);
  }

  .wk-state-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }

  .wk-state-card {
    border: 1px solid var(--wk-line);
    border-radius: 16px;
    padding: 14px;
    background: rgba(255, 255, 255, 0.03);
  }

  .wk-state-card strong {
    display: block;
    font-size: 24px;
    line-height: 1.1;
    margin-bottom: 6px;
  }

  .wk-state-card span {
    display: block;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--wk-muted);
    margin-bottom: 8px;
  }

  .wk-state-card p {
    margin: 0;
    color: var(--wk-muted);
    font-size: 13px;
    line-height: 1.45;
  }

  .wk-main-column {
    min-width: 0;
  }

  .wk-inspector-title {
    font-size: 20px;
    font-weight: 700;
    letter-spacing: -0.03em;
    margin: 0 0 10px;
  }

  .wk-inspector-body {
    color: var(--wk-muted);
    font-size: 13px;
    line-height: 1.55;
  }

  .wk-inspector-meta {
    display: grid;
    gap: 8px;
    margin-top: 12px;
  }

  .wk-inspector-row {
    padding: 10px 12px;
    border-radius: 12px;
    border: 1px solid var(--wk-line);
    background: rgba(255, 255, 255, 0.03);
    color: var(--wk-text);
    font-size: 12px;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }

  .wk-inspector-row code,
  .wk-code {
    font-family: var(--wk-font-mono);
    font-size: 12px;
    color: var(--wk-text);
  }

  .wk-raw-panel {
    display: none;
    margin-top: 12px;
    padding: 14px;
    border-radius: 14px;
    background: rgba(0, 0, 0, 0.24);
    border: 1px solid var(--wk-line);
    color: var(--wk-text);
    font-family: var(--wk-font-mono);
    font-size: 12px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow: auto;
    max-height: 52vh;
  }

  body[data-view-mode="raw"] .wk-raw-panel {
    display: block;
  }

  body[data-view-mode="raw"] .wk-human-view,
  body[data-view-mode="raw"] .wk-agent-view {
    display: none;
  }

  body[data-view-mode="agent"] .wk-human-view[data-human-optional="1"] {
    display: none;
  }

  body[data-view-mode="human"] .wk-agent-view[data-agent-optional="1"] {
    display: none;
  }

  .wk-section-drawer {
    border: 1px solid var(--wk-line);
    border-radius: 16px;
    background: rgba(255, 255, 255, 0.03);
    overflow: hidden;
  }

  .wk-section-drawer summary {
    list-style: none;
    cursor: pointer;
    padding: 12px 14px;
    color: var(--wk-text);
    font-size: 13px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    background: rgba(255, 255, 255, 0.02);
  }

  .wk-section-drawer summary::-webkit-details-marker {
    display: none;
  }

  .wk-section-drawer-body {
    padding: 14px;
    border-top: 1px solid var(--wk-line);
  }

  .wk-link-button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border-radius: 999px;
    border: 1px solid var(--wk-line);
    padding: 7px 10px;
    background: rgba(255, 255, 255, 0.03);
    color: var(--wk-text);
    text-decoration: none;
    font-size: 12px;
  }

  @media (max-width: 1340px) {
    .wk-layout--hive,
    .wk-layout--trace {
      grid-template-columns: 1fr;
    }
  }

  @media (max-width: 980px) {
    .wk-topbar {
      grid-template-columns: 1fr;
      align-items: start;
    }

    .wk-state-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 640px) {
    .wk-app-shell {
      padding: 14px 12px 24px;
    }

    .wk-state-grid {
      grid-template-columns: 1fr;
    }
  }
"""


def render_workstation_styles() -> str:
    return _WORKSTATION_STYLES


def render_workstation_header(
    *,
    title: str,
    subtitle: str,
    default_mode: str,
    surface: str,
    overview_href: str = "/brain-hive?mode=overview",
    hive_href: str = "/brain-hive?mode=hive",
    trace_href: str = "/trace",
    trace_enabled: bool = True,
    trace_label: str = "Trace",
    fabric_href: str = "/brain-hive?mode=fabric&fabric=1",
) -> str:
    trace_class = "wk-mode-link" + ("" if trace_enabled else " is-disabled")
    trace_target = trace_href if trace_enabled else "#"
    return f"""
  <header class="wk-topbar" data-workstation-version="{escape(VOOL_WORKSTATION_DEPLOYMENT_VERSION)}" data-workstation-surface="{escape(surface)}">
    <div class="wk-brand">
      <div class="wk-brand-mark">VOOL</div>
      <div>
        <div class="wk-brand-title">{escape(title)}</div>
        <div class="wk-brand-subtitle">{escape(subtitle)}</div>
        <div class="wk-brand-proof">
          <span class="wk-proof-chip wk-proof-chip--primary" data-workstation-proof="generation">workstation v1</span>
          <span class="wk-proof-chip" data-workstation-proof="surface">surface {escape(surface)}</span>
          <span class="wk-proof-chip" data-workstation-proof="version">deploy {escape(VOOL_WORKSTATION_DEPLOYMENT_VERSION)}</span>
        </div>
      </div>
    </div>
    <nav class="wk-mode-nav" aria-label="VOOL workstation modes">
      <a class="wk-mode-link" data-workstation-mode-link="overview" data-mode-link="overview" href="{escape(overview_href)}">Overview</a>
      <a class="wk-mode-link" data-workstation-mode-link="hive" data-mode-link="hive" href="{escape(hive_href)}">Hive</a>
      <a class="{trace_class}" data-workstation-mode-link="trace" data-mode-link="trace" data-workstation-trace-state="{escape('live' if trace_enabled else 'not-live')}" href="{escape(trace_target)}">{escape(trace_label)}</a>
      <a class="wk-mode-link" data-workstation-mode-link="fabric" data-mode-link="fabric" data-workstation-fabric href="{escape(fabric_href)}">Fabric</a>
    </nav>
    <div class="wk-view-toggle" aria-label="VOOL workstation rendering mode">
      <button class="wk-view-button" type="button" data-workstation-view="human">Human</button>
      <button class="wk-view-button" type="button" data-workstation-view="agent">Agent</button>
      <button class="wk-view-button" type="button" data-workstation-view="raw">Raw</button>
    </div>
  </header>
  <script>
    window.VOOL_WORKSTATION_DEFAULT_MODE = {default_mode!r};
  </script>
"""


def render_workstation_script() -> str:
    return """
    (function initVoolWorkstationShell() {
      const params = new URLSearchParams(window.location.search);
      const defaultMode = window.VOOL_WORKSTATION_DEFAULT_MODE || 'overview';
      const body = document.body;
      const viewButtons = Array.from(document.querySelectorAll('[data-workstation-view]'));
      const modeLinks = Array.from(document.querySelectorAll('[data-workstation-mode-link]'));
      const fabricNodes = Array.from(document.querySelectorAll('[data-workstation-fabric]'));
      const storageKey = 'vool.workstation.view';
      const fabricStorageKey = 'vool.workstation.fabric';

      const normalizeView = (value) => ['human', 'agent', 'raw'].includes(String(value || '')) ? String(value) : 'human';

      const applyView = (value) => {
        const mode = normalizeView(value);
        body.dataset.viewMode = mode;
        viewButtons.forEach((button) => {
          button.classList.toggle('is-active', button.dataset.workstationView === mode);
        });
        try {
          window.localStorage.setItem(storageKey, mode);
        } catch (_err) {
          // ignore storage failures
        }
      };

      const activeMode = String(params.get('mode') || defaultMode || 'overview');
      modeLinks.forEach((link) => {
        link.classList.toggle('is-active', link.dataset.workstationModeLink === activeMode);
      });

      const fabricEnabled = params.get('fabric') === '1' || (() => {
        try {
          return window.localStorage.getItem(fabricStorageKey) === '1';
        } catch (_err) {
          return false;
        }
      })();
      fabricNodes.forEach((node) => {
        node.style.display = fabricEnabled ? 'inline-flex' : 'none';
      });

      const initialView = normalizeView(params.get('view') || (() => {
        try {
          return window.localStorage.getItem(storageKey);
        } catch (_err) {
          return 'human';
        }
      })() || 'human');
      applyView(initialView);

      viewButtons.forEach((button) => {
        button.addEventListener('click', () => applyView(button.dataset.workstationView || 'human'));
      });
    })();
"""
