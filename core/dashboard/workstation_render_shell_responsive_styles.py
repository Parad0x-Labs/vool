from __future__ import annotations

"""Responsive, loading, and live-indicator styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_RESPONSIVE_STYLES = """
    @media (max-width: 1120px) {
      .hero, .cols-2, .dashboard-home-grid, .dashboard-overview-grid {
        grid-template-columns: 1fr;
      }
      .stats {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .dashboard-workbench {
        grid-template-columns: 1fr;
      }
      .dashboard-rail,
      .dashboard-inspector {
        position: static;
        min-height: auto;
      }
      .dashboard-tab-row {
        position: relative;
      }
      .dashboard-tab-row::after {
        content: "";
        position: absolute;
        right: 0;
        top: 0;
        bottom: 0;
        width: 32px;
        background: linear-gradient(90deg, transparent, var(--bg, #0a0f1a));
        pointer-events: none;
        border-radius: 0 999px 999px 0;
      }
    }
    @media (max-width: 640px) {
      .shell { padding: 16px 12px 28px; }
      .mini-grid { grid-template-columns: 1fr; }
      .learning-program-grid { grid-template-columns: 1fr; }
      .learning-program-head { flex-direction: column; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      h1 { font-size: 34px; }
    }
    #initialLoadingOverlay {
      position: fixed;
      inset: 0;
      z-index: 9999;
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 16px;
      background: var(--bg, #0a0f1a);
      color: var(--wk-text, #e0e6ed);
      font-family: var(--wk-font-sans, system-ui, sans-serif);
    }
    #initialLoadingOverlay .loading-ring {
      width: 40px;
      height: 40px;
      border: 3px solid rgba(97, 218, 251, 0.2);
      border-top-color: var(--accent, #61dafb);
      border-radius: 50%;
      animation: spin-ring 0.9s linear infinite;
    }
    @keyframes spin-ring {
      to { transform: rotate(360deg); }
    }
    .live-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .live-badge::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #4cda80;
      animation: pulse-dot 1.6s ease-in-out infinite;
    }
    summary { list-style: none; }
    summary::-webkit-details-marker { display: none; }
"""
