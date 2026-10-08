"""Shared dark UI colors. Character artwork and semantic status colors stay separate."""
from __future__ import annotations

DARK_PALETTE_CSS = """:root {
  color-scheme: dark;
  --bg:#141416; --chat:#101216; --panel:#1b1b1e; --panel-solid:#1b1b1e;
  --field:#232326; --active:#232326; --user:#2b2b30;
  --ink:#e6e2dc; --text:#e6e2dc; --muted:#aaa5a0; --border:#39393f;
  --accent:#c3b8a8; --accent2:#b5a58f; --accent-ink:#171513;
  --accent-rgb:195,184,168; --accent-soft:rgba(var(--accent-rgb),.12);
  --grad:linear-gradient(135deg,var(--accent),var(--accent2));
  --success:#34d399; --ok:var(--success); --bad:#f87171; --warn:#fbbf24;
  --mk-bg:#18181b; --mk-border:#39393f; --shadow:rgba(0,0,0,.45);
}"""
