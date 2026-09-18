"""Turn OpenClaw's "Docs" nav item into a native "Web0" entry.

OpenClaw's Control UI is a compiled bundle whose nav can't be extended from config
or a plugin. To make the Web0 (.null) browser reachable from *inside* OpenClaw's own
interface, we relabel the sidebar "Docs" link to "Web0" and point it at the VOOL-
served browser at /web0.

Delivery detail that matters: OpenClaw serves a strict Content-Security-Policy
(`script-src 'self' 'sha256-...'`) that BLOCKS inline scripts. So we cannot inject an
inline <script> - it would be silently refused. Instead we drop a real file
(web0-pill.js) next to index.html and reference it with `<script src="./web0-pill.js">`,
which the CSP's `'self'` allows. The gateway serves that file from the same origin.

Idempotent + byte-stable + removable. The launcher re-applies it every start, so it
survives `npm install -g openclaw` upgrades that overwrite dist.

Usage:
    python -m installer.inject_openclaw_web0_pill [--index <path>] [--remove]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

START = "<!-- vool-web0-pill:start -->"
END = "<!-- vool-web0-pill:end -->"
PILL_JS_NAME = "web0-pill.js"

# External script referenced by src so it satisfies CSP `script-src 'self'`.
_INSERT = f'\n    {START}\n    <script src="./{PILL_JS_NAME}" defer></script>\n    {END}\n  '

# The pill/relabel logic. Runs in the page's own world (unlike the CSP-blocked inline
# path). OpenClaw's nav is plain light DOM (no shadow roots), but we keep a defensive
# shadow walk in case that changes, and fall back to a floating pill if the "Docs"
# item can't be found.
_PILL_JS = """\
(function () {
  "use strict";
  var WEB0_URL = "http://127.0.0.1:11435/web0";
  var DONE = "data-web0-nav", PILL_ID = "vool-web0-pill";
  var installed = null;

  function eachShadow(root, fn) {
    try { fn(root); } catch (e) {}
    var all = root.querySelectorAll ? root.querySelectorAll("*") : [];
    for (var i = 0; i < all.length; i++) { if (all[i].shadowRoot) eachShadow(all[i].shadowRoot, fn); }
  }
  function findDocsNav() {
    var hit = null;
    eachShadow(document, function (root) {
      if (hit || !root.querySelectorAll) return;
      var els = root.querySelectorAll('a,[role="link"],[role="menuitem"],button');
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var txt = (el.textContent || "").replace(/\\s+/g, " ").trim();
        var href = (el.getAttribute && el.getAttribute("href")) || "";
        if (txt === "Docs" || (href.indexOf("docs.openclaw.ai") !== -1 && txt.length <= 8)) { hit = el; return; }
      }
    });
    return hit;
  }
  function relabel(el) {
    var w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null), t, changed = false;
    while ((t = w.nextNode())) {
      if (t.nodeValue && t.nodeValue.trim() === "Docs") { t.nodeValue = t.nodeValue.replace("Docs", "Web0"); changed = true; }
    }
    if (!changed) el.textContent = "Web0";
  }
  function repurpose() {
    if (installed && installed.isConnected && installed.getAttribute(DONE)) return true;
    var el = findDocsNav();
    if (!el) return false;
    el.setAttribute(DONE, "1");
    relabel(el);
    if (el.tagName === "A") { el.setAttribute("href", WEB0_URL); el.setAttribute("target", "_blank"); el.setAttribute("rel", "noopener"); }
    el.setAttribute("title", "Open the Web0 (.null) browser \\u2014 default: web0.null");
    el.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      try { window.open(WEB0_URL, "_blank", "noopener"); } catch (err) { location.href = WEB0_URL; }
    }, true);
    installed = el;
    var pill = document.getElementById(PILL_ID); if (pill) pill.remove();
    return true;
  }
  function mountPill() {
    if (document.getElementById(PILL_ID) || !document.body) return;
    var b = document.createElement("button");
    b.id = PILL_ID; b.type = "button"; b.textContent = "\\u2205 Web0";
    b.title = "Open the Web0 (.null) browser \\u2014 default: web0.null";
    var s = b.style;
    s.position = "fixed"; s.top = "12px"; s.left = "50%"; s.transform = "translateX(-50%)";
    s.zIndex = "2147483647"; s.padding = "6px 16px"; s.borderRadius = "20px";
    s.border = "1px solid rgba(255,80,80,0.55)"; s.background = "linear-gradient(180deg,#181013,#0e0a0b)";
    s.color = "#ff6b6b"; s.font = "600 12px/1 ui-monospace,'Courier New',monospace";
    s.letterSpacing = "0.5px"; s.cursor = "pointer"; s.boxShadow = "0 2px 12px rgba(0,0,0,0.5)";
    b.addEventListener("click", function (e) { e.preventDefault(); try { window.open(WEB0_URL, "_blank", "noopener"); } catch (err) { location.href = WEB0_URL; } });
    document.body.appendChild(b);
  }
  var tries = 0;
  function tick() {
    if (repurpose()) return;
    if (++tries > 40) { mountPill(); return; }
    setTimeout(tick, 500);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tick, { once: true }); else tick();
  var pending = false;
  function schedule() { if (pending) return; pending = true; setTimeout(function () { pending = false; repurpose(); }, 400); }
  try { new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true }); } catch (e) {}
})();
"""


def _strip_existing(html: str) -> str:
    if _INSERT in html:
        return html.replace(_INSERT, "")
    if START in html and END in html:
        i = html.index(START)
        j = html.index(END) + len(END)
        return html[:i] + html[j:]
    return html


def inject_into_html(html: str) -> str:
    """Return html with exactly one <script src> reference just before </body>."""
    cleaned = _strip_existing(html)
    idx = cleaned.lower().rfind("</body>")
    if idx == -1:
        return cleaned + _INSERT
    return cleaned[:idx] + _INSERT + cleaned[idx:]


def _npm_openclaw_roots() -> list[Path]:
    """Global-npm node_modules dirs where the ``openclaw`` package may live."""
    roots: list[Path] = []
    for cmd in (["npm", "root", "-g"], ["npm.cmd", "root", "-g"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                roots.append(Path(out.stdout.strip()))
                break
        except Exception:
            continue
    which = shutil.which("openclaw") or shutil.which("openclaw.cmd")
    if which:
        roots.append(Path(which).resolve().parent / "node_modules")
    return roots


def _looks_like_control_ui(index_path: Path) -> bool:
    """Whether an index.html is the OpenClaw control-UI shell (boots a JS bundle and titles
    itself OpenClaw), used only to filter the glob fallback so it does not patch an unrelated
    doc/readme index.html. NOTE: the real shell's ``</body>`` is far down a large minified file,
    so this checks head markers (a script tag + the OpenClaw name), not the closing body tag."""
    try:
        head = index_path.read_text(encoding="utf-8", errors="ignore")[:8000].lower()
    except Exception:
        return False
    return "<script" in head and "openclaw" in head


def _candidate_index_files(openclaw_dist: Path) -> list[Path]:
    """Every index.html under dist/, control-UI-looking paths first. OpenClaw moves/renames the
    control-ui dir between releases, so a single hardcoded path silently misses other layouts
    (the cause of the pill appearing on one OpenClaw version but not another)."""
    try:
        found = [path for path in openclaw_dist.glob("**/index.html") if path.is_file()]
    except Exception:
        return []
    found.sort(
        key=lambda path: (
            0 if any(part.lower() in {"control-ui", "control", "ui", "app"} for part in path.parts) else 1,
            str(path),
        )
    )
    return found


def locate_control_ui_index() -> Path | None:
    env = os.environ.get("OPENCLAW_CONTROL_UI_INDEX")
    if env and Path(env).is_file():
        return Path(env)
    for candidate in _npm_openclaw_roots():
        dist = candidate / "openclaw" / "dist"
        if not dist.is_dir():
            continue
        # The canonical path is trusted as-is -- never let a heuristic reject the known-good file.
        exact = dist / "control-ui" / "index.html"
        if exact.is_file():
            return exact
        # Fallback for renamed/moved layouts: glob, keep only the OpenClaw app shell.
        for index_path in _candidate_index_files(dist):
            if _looks_like_control_ui(index_path):
                return index_path
    return None


def apply(index_path: Path, *, remove: bool = False) -> str:
    pill_js_path = index_path.parent / PILL_JS_NAME
    html = index_path.read_text(encoding="utf-8")

    if remove:
        new_html = _strip_existing(html)
        if pill_js_path.exists():
            pill_js_path.unlink()
        if new_html == html:
            return "already-correct"
        index_path.write_text(new_html, encoding="utf-8")
        return "removed"

    # Always (re)write the sidecar JS so its content stays in sync with this module.
    js_current = pill_js_path.read_text(encoding="utf-8") if pill_js_path.exists() else None
    if js_current != _PILL_JS:
        pill_js_path.write_text(_PILL_JS, encoding="utf-8")
    new_html = inject_into_html(html)
    if new_html == html and js_current == _PILL_JS:
        return "already-correct"
    if new_html != html:
        index_path.write_text(new_html, encoding="utf-8")
    return "injected"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inject_openclaw_web0_pill")
    parser.add_argument("--index", default="", help="path to control-ui/index.html (auto-detected if omitted)")
    parser.add_argument("--remove", action="store_true", help="remove the Web0 nav injection")
    args = parser.parse_args(argv)

    index_path = Path(args.index) if args.index else locate_control_ui_index()
    if index_path is None or not index_path.is_file():
        searched = ", ".join(str(root / "openclaw" / "dist") for root in _npm_openclaw_roots()) or "<no npm global root found>"
        print(
            "OpenClaw control-ui index.html not found; Web0 nav NOT injected. "
            f"Searched under: {searched}. This OpenClaw version's UI layout may differ -- "
            "set OPENCLAW_CONTROL_UI_INDEX to its index.html to force injection.",
            file=sys.stderr,
        )
        return 1
    result = apply(index_path, remove=args.remove)
    print(f"Web0 nav {result}: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
