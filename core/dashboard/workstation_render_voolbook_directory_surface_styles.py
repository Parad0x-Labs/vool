from __future__ import annotations

"""VoolBook directory surface styles for the workstation dashboard."""

WORKSTATION_RENDER_VOOLBOOK_DIRECTORY_SURFACE_STYLES = """
    .nb-section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }
    .nb-butterfly {
      display: inline-block;
      animation: nb-float 3s ease-in-out infinite;
    }
    @keyframes nb-float {
      0%, 100% { transform: translateY(0) rotate(0deg); }
      50% { transform: translateY(-4px) rotate(3deg); }
    }
    .nb-empty {
      text-align: center;
      padding: 40px 20px;
      color: var(--muted);
      font-size: 14px;
    }
"""
