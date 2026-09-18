from __future__ import annotations

"""Footer and social-call-to-action styles for the workstation dashboard shell."""

WORKSTATION_RENDER_SHELL_FOOTER_STYLES = """
    footer {
      margin-top: 0;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: space-between;
    }
    .footer-stack {
      display: grid;
      gap: 8px;
      justify-items: end;
      text-align: right;
    }
    .footer-link-row {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .social-link {
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      text-decoration: none;
    }
    .social-link:hover,
    .social-link:focus-visible {
      border-color: var(--accent);
      color: var(--accent-strong);
      outline: none;
    }
    .social-link svg {
      width: 16px;
      height: 16px;
      fill: currentColor;
    }
    .hero-follow-link {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      background: var(--panel-alt);
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 12px;
      text-decoration: none;
      line-height: 1;
      font-size: 12px;
      font-weight: 600;
    }
    .hero-follow-link:hover,
    .hero-follow-link:focus-visible {
      border-color: var(--accent);
      color: var(--accent-strong);
      outline: none;
    }
    .hero-action-row {
      margin-top: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .hero-follow-link svg {
      width: 14px;
      height: 14px;
      fill: currentColor;
    }
"""
