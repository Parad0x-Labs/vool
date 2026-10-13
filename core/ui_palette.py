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

# ---- Appearance: Dark (default), Light, or Match system; and a conversation text size -----------
# A per-person presentation choice. It lives in one first-party cookie on this runtime's own origin
# so the server can paint the right theme in the very first frame (no dark flash before light) and
# every VOOL page (chat, Settings, setup) agrees. It never reaches a model, a provider or the
# network, and it changes no behaviour -- only colours and the size of conversation text.
APPEARANCE_COOKIE = "vool_appearance"
APPEARANCE_THEMES = ("dark", "light", "system")
APPEARANCE_TEXT_SCALES = (100, 112, 125)

_LIGHT_TOKENS = """
  color-scheme: light;
  --bg:#f3f1ed; --chat:#f9f8f6; --panel:#ffffff; --panel-solid:#ffffff;
  --field:#ece9e4; --active:#e6e2dc; --user:#e9e4dc;
  --ink:#1f1d1a; --text:#1f1d1a; --muted:#5c5750; --border:#d6d1c9;
  --accent:#6a5941; --accent2:#7b6a51; --accent-ink:#ffffff;
  --accent-rgb:106,89,65; --accent-soft:rgba(var(--accent-rgb),.12);
  --success:#15803d; --ok:var(--success); --bad:#b42318; --warn:#a15c07;
  --mk-bg:#ebe8e3; --mk-border:#cdc7be; --shadow:rgba(0,0,0,.12); --bg-elev:#ffffff;
"""

# The few chat-page parts that paint a fixed dark colour instead of a palette token. Selectors keep
# the page rule's own id so they outrank it.
_LIGHT_FIXED_RULES = (
    (".model-lane.auto", "color:#5b4b86; background:#ece7f6;"),
    (".model-lane.cloud", "color:#2f4f7a; background:#e3ebf5;"),
    # Status words tinted for a dark backdrop take the light palette's own status colours.
    (".proof-chip .pc-state-incomplete, :LIGHT .p-firstparty, :LIGHT .att-chip .att-warn, "
     ":LIGHT .pop-prov .pp-paid", "color:var(--warn);"),
    (".proof-chip .pc-state-unverified, :LIGHT .set-status.err, :LIGHT #updateChip.upd-warn, "
     ":LIGHT .att-chip .att-error, :LIGHT .tc-mode.bypass",
     "color:var(--bad);"),
    (".pop-prov .pp-free", "color:var(--success);"),
    (".pop-prov .pp-unknown, :LIGHT .council-live .cl-state, :LIGHT .council-cell .cv.none", "color:var(--accent);"),
    ("#input::placeholder", "color:var(--muted);"),
    ("#companionLayer .vn-bubble-title", "color:var(--ink);"),
    ("#companionLayer .vn-caption", "background:rgba(255,255,255,.94); border-color:var(--border);"),
    # The pet's status tones are pale for a dark backdrop; on light, darken the same hue.
    ("#companionLayer .vn-bubble-activity", "color:color-mix(in srgb, var(--vn-tone, #94a3b8) 50%, #1f1d1a);"),
    ("#companionLayer .vn-caption.vn-show", "color:color-mix(in srgb, var(--vn-tone, #94a3b8) 50%, #1f1d1a);"),
)


def _light_rules(prefix: str) -> str:
    # ":LIGHT" marks the start of each further selector in a group, so every one gets the prefix.
    return "\n".join(
        f"{prefix} {selector.replace(':LIGHT ', prefix + ' ')} {{ {body} }}" for selector, body in _LIGHT_FIXED_RULES
    )


# The calm light counterpart of the dark palette: the same warm neutrals and muted earth accent,
# never a bright or electric colour. "Match system" follows the operating system's own setting.
# The explicit Light choice alone, for a page whose light look is the host's call (setup, framed).
LIGHT_ONLY_PALETTE_CSS = ':root[data-theme="light"] {' + _LIGHT_TOKENS + "}\n" + _light_rules(':root[data-theme="light"]')

LIGHT_PALETTE_CSS = (
    LIGHT_ONLY_PALETTE_CSS + "\n"
    '@media (prefers-color-scheme: light) { :root[data-theme="system"] {' + _LIGHT_TOKENS + "}\n"
    + _light_rules(':root[data-theme="system"]') + " }"
)


def parse_appearance(raw: str | None) -> tuple[str, int]:
    """``"light.112"`` -> ``("light", 112)``. Anything unknown falls back to dark at normal size."""
    theme, _, text = str(raw or "").partition(".")
    theme = theme if theme in APPEARANCE_THEMES else "dark"
    try:
        scale = int(text)
    except ValueError:
        scale = 100
    return theme, (scale if scale in APPEARANCE_TEXT_SCALES else 100)


def appearance_from_cookie_header(raw_cookie: str | None) -> tuple[str, int]:
    from http.cookies import SimpleCookie

    jar = SimpleCookie()
    try:
        jar.load(str(raw_cookie or ""))
    except Exception:
        return parse_appearance("")
    morsel = jar.get(APPEARANCE_COOKIE)
    return parse_appearance(morsel.value if morsel else "")


def appearance_html_attrs(appearance: tuple[str, int] | None) -> str:
    """The attributes the page's ``<html>`` tag carries for one appearance (empty for the default)."""
    theme, scale = appearance or ("dark", 100)
    theme, scale = parse_appearance(f"{theme}.{scale}")
    attrs = ""
    if theme != "dark":
        attrs += f' data-theme="{theme}"'
    if scale != 100:
        attrs += f' style="--vool-text-scale:{scale / 100:g}"'
    return attrs


# Live switching inside an open page: Settings writes the cookie and tells every other open VOOL
# page (the chat window, a framed setup) on a same-origin BroadcastChannel, so a change shows at
# once without a reload. Embedded INSIDE each page's own script (no extra script element).
APPEARANCE_JS = """
(function () {
  var NAME = '%(cookie)s', THEMES = %(themes)s, SCALES = %(scales)s, root = document.documentElement;
  function clean(v) {
    v = v || {};
    var theme = THEMES.indexOf(v.theme) >= 0 ? v.theme : 'dark';
    var text = Number(v.text); if (SCALES.indexOf(text) < 0) text = 100;
    return { theme: theme, text: text };
  }
  function read() {
    var m = String(document.cookie || '').match(new RegExp('(?:^|;\\\\s*)' + NAME + '=([^;]*)'));
    var parts = (m ? decodeURIComponent(m[1]) : '').split('.');
    return clean({ theme: parts[0], text: parts[1] });
  }
  function apply(v) {
    var c = clean(v);
    if (c.theme === 'dark') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', c.theme);
    if (c.text === 100) root.style.removeProperty('--vool-text-scale'); else root.style.setProperty('--vool-text-scale', String(c.text / 100));
    return c;
  }
  var channel = null;
  try { channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('vool-appearance') : null; } catch (e) { channel = null; }
  if (channel) channel.onmessage = function (e) { apply(e && e.data); };
  // Node (used to run page scripts headless) keeps a process alive while a channel is open; a page does not.
  if (channel && typeof channel.unref === 'function') channel.unref();
  window.addEventListener('focus', function () { apply(read()); });
  window.VoolAppearance = {
    get: read,
    set: function (next) {
      var c = clean(Object.assign({}, read(), next || {}));
      document.cookie = NAME + '=' + c.theme + '.' + c.text + '; path=/; max-age=31536000; SameSite=Strict';
      apply(c);
      if (channel) { try { channel.postMessage(c); } catch (e) {} }
      return read().theme === c.theme && read().text === c.text;
    }
  };
})();
""" % {"cookie": APPEARANCE_COOKIE, "themes": list(APPEARANCE_THEMES), "scales": list(APPEARANCE_TEXT_SCALES)}
