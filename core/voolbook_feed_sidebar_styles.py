from __future__ import annotations

VOOLBOOK_FEED_SIDEBAR_STYLES = r"""
.nb-sidebar { display: flex; flex-direction: column; gap: 16px; }
.nb-sidebar-card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 16px; padding: 18px;
}
.nb-sidebar-title {
  font-size: 12px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--text-muted); margin-bottom: 12px; font-family: var(--font-mono);
}
.nb-sidebar-stat { display: flex; justify-content: space-between; font-size: 13px; padding: 5px 0; color: var(--text-muted); }
.nb-sidebar-stat strong { color: var(--text); font-weight: 600; }
.nb-profile-mini { display: flex; align-items: center; gap: 10px; padding: 8px 0; }
.nb-profile-mini .nb-avatar { width: 32px; height: 32px; font-size: 13px; }
.nb-profile-mini-name { font-size: 13px; font-weight: 600; color: var(--text); }
.nb-profile-mini-detail { font-size: 11px; color: var(--text-dim); }

.nb-hero {
  background: linear-gradient(180deg, rgba(34,29,24,0.98) 0%, rgba(24,21,18,0.98) 100%);
  border: 1px solid var(--border-strong); border-radius: 20px;
  padding: 28px 28px 22px; text-align: left; margin-bottom: 12px;
  position: relative; overflow: hidden;
}
.nb-hero::before {
  content: "";
  position: absolute;
  inset: 18px 18px auto auto;
  width: 104px;
  height: 104px;
  border-top: 1px solid rgba(196,125,66,0.18);
  border-right: 1px solid rgba(196,125,66,0.18);
}
.nb-hero-kicker {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: 8px;
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.14em; color: var(--text-muted);
  background: rgba(196,125,66,0.08); border: 1px solid rgba(196,125,66,0.18);
  margin-bottom: 14px;
  font-family: var(--font-mono);
}
.nb-hero h2 {
  font-family: var(--font-display);
  font-size: 38px; line-height: 1.02; font-weight: 700; margin-bottom: 10px; max-width: 700px;
}
.nb-hero p { font-size: 14px; color: var(--text-muted); line-height: 1.7; max-width: 640px; margin: 0; }
.nb-hero-ledger {
  display: flex; flex-wrap: wrap; gap: 10px;
  margin-top: 18px;
}
.nb-hero-ledger-item {
  display: inline-flex; align-items: center;
  min-height: 32px; padding: 0 10px; border-radius: 8px;
  background: rgba(255,255,255,0.03); border: 1px solid var(--border);
  color: var(--text-muted); font-size: 12px; font-family: var(--font-mono);
}
.nb-hero-ledger-item strong {
  color: var(--paper-strong); margin-left: 6px;
}
.nb-hero-chips {
  display: flex; flex-wrap: wrap; gap: 10px;
  margin-top: 18px;
}
.nb-hero-chip {
  padding: 7px 12px; border-radius: 8px; font-size: 12px; font-weight: 600;
  color: var(--text); background: rgba(255,255,255,0.03); border: 1px solid var(--border); font-family: var(--font-mono);
}
.nb-section-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin: 16px 0 14px;
}
.nb-section-title {
  font-size: 12px; font-weight: 800; text-transform: uppercase;
  letter-spacing: 1.1px; color: var(--text-dim);
}
.nb-section-subtitle {
  font-size: 12px; color: var(--text-muted);
}
"""
