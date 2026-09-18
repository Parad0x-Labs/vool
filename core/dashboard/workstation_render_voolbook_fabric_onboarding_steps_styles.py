from __future__ import annotations

WORKSTATION_RENDER_VOOLBOOK_FABRIC_ONBOARDING_STEPS_STYLES = """
    .nb-onboard {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
      gap: 16px;
      margin-bottom: 48px;
    }
    .nb-onboard-step {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 20px;
      position: relative;
    }
    .nb-onboard-num {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: linear-gradient(135deg, #61dafb, #a78bfa);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 13px;
      color: #0a0f1a;
      margin-bottom: 10px;
    }
    .nb-onboard-title {
      font-weight: 700;
      font-size: 15px;
      color: var(--ink);
      margin-bottom: 6px;
    }
    .nb-onboard-desc {
      font-size: 13px;
      color: var(--muted);
      line-height: 1.5;
    }
    .nb-onboard-link {
      display: inline-block;
      margin-top: 10px;
      font-size: 12px;
      color: var(--accent);
      text-decoration: none;
    }
    .nb-onboard-link:hover { text-decoration: underline; }
"""
