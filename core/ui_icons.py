"""One small set of line icons for VOOL's own controls.

Every icon is a 16x16 outline drawn in the text colour through a CSS mask, so a row of controls never
mixes colour emoji with plain glyphs, and the icons follow the theme (dark, light) and hover colour
like the words beside them. Usage: ``class="vi" data-ico="files"`` on the control itself (the icon
sits before its text) or on an empty ``<span aria-hidden="true">`` inside it.

User content (chat emoji, the emoji palette, greetings) is not drawn from here: those are the
person's own choice.
"""
from __future__ import annotations

from urllib.parse import quote

_ICONS: dict[str, str] = {
    "home": "<path d='M2.5 7.5 8 3l5.5 4.5'/><path d='M4 6.5V13h8V6.5'/>",
    "addons": (
        "<rect x='2.5' y='2.5' width='4.5' height='4.5' rx='1'/><rect x='9' y='2.5' width='4.5' height='4.5' rx='1'/>"
        "<rect x='2.5' y='9' width='4.5' height='4.5' rx='1'/><rect x='9' y='9' width='4.5' height='4.5' rx='1'/>"
    ),
    "files": "<path d='M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z'/>",
    "folder-open": "<path d='M2.5 12V4.5a1 1 0 0 1 1-1h3L8 5h4.5a1 1 0 0 1 1 1v1'/><path d='M2.5 12l1.6-4.2a1 1 0 0 1 .9-.6h9l-1.7 4.8H2.5z'/>",
    "move": "<path d='M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z'/><path d='M6 9h4M8.5 7.5 10 9l-1.5 1.5'/>",
    "contacts": "<circle cx='8' cy='5.5' r='2.5'/><path d='M3 13.5c.6-2.5 2.6-4 5-4s4.4 1.5 5 4'/>",
    "settings": (
        "<circle cx='8' cy='8' r='2'/><path d='M6.9 2h2.2l.4 1.6 1.2.7 1.6-.5 1.1 1.9-1.2 1.1v1.4l1.2 1.1-1.1 1.9"
        "-1.6-.5-1.2.7-.4 1.6H6.9l-.4-1.6-1.2-.7-1.6.5-1.1-1.9 1.2-1.1V7.3L2.6 6.2l1.1-1.9 1.6.5 1.2-.7z'/>"
    ),
    "report": "<path d='M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z'/>",
    "pin": "<path d='M6 2.5h4l-.5 4 2 2h-7l2-2z'/><path d='M8 8.5v5'/>",
    "bell": "<path d='M4 11V7.5a4 4 0 0 1 8 0V11l1 1.5H3z'/><path d='M6.8 14a1.3 1.3 0 0 0 2.4 0'/>",
    "attach": "<path d='M13 7.5 8.2 12.3a3 3 0 0 1-4.3-4.3l5-5a2 2 0 0 1 2.9 2.9l-5 5a1 1 0 0 1-1.4-1.4L10 4.9'/>",
    "mic": "<rect x='6' y='2' width='4' height='7.5' rx='2'/><path d='M3.8 8a4.2 4.2 0 0 0 8.4 0M8 12.2V14'/>",
    "voice": "<path d='M2.5 6.5h2.5L8.5 3.5v9L5 9.5H2.5z'/><path d='M11 6a2.8 2.8 0 0 1 0 4M12.8 4.2a5.3 5.3 0 0 1 0 7.6'/>",
    "palette": (
        "<path d='M8 2.5a5.5 5.5 0 0 0 0 11c1 0 1.3-.7 1-1.4-.4-.8.1-1.6 1-1.6h1.5a2 2 0 0 0 2-2A5.5 5.5 0 0 0 8 2.5z'/>"
        "<circle cx='5.3' cy='7' r='.6'/><circle cx='8' cy='5' r='.6'/><circle cx='10.7' cy='7' r='.6'/>"
    ),
    "archive": "<rect x='2.5' y='3' width='11' height='3' rx='.7'/><path d='M3.5 6v6.5h9V6M6.5 8.5h3'/>",
    "trash": "<path d='M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 9h5.8l.6-9'/>",
    "rename": "<path d='M10.5 3 13 5.5 6 12.5H3.5V10z'/><path d='M9 4.5 11.5 7'/>",
    "restore": "<path d='M5.5 4 3 6.5 5.5 9'/><path d='M3 6.5h6.5a3.5 3.5 0 0 1 0 7H7'/>",
    "check": "<path d='M3 8.5 6.5 12 13 4.5'/>",
    "shield": "<path d='M8 2 13 4v4c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4z'/>",
    "key": "<circle cx='5.5' cy='10.5' r='2.5'/><path d='M7.3 8.7 13 3M10.5 5.5l1.5 1.5M12 4l1 1'/>",
    "mail": "<rect x='2' y='3.5' width='12' height='9' rx='1.2'/><path d='m2.5 4.5 5.5 4.5 5.5-4.5'/>",
    "models": "<rect x='4' y='4' width='8' height='8' rx='1.2'/><path d='M6.5 2v2M9.5 2v2M6.5 12v2M9.5 12v2M2 6.5h2M2 9.5h2M12 6.5h2M12 9.5h2'/>",
    "memory": "<path d='M4.5 2.5h7v11L8 11l-3.5 2.5z'/>",
    "backup": "<path d='M5 13V3M2.8 5.2 5 3l2.2 2.2M11 3v10M8.8 10.8 11 13l2.2-2.2'/>",
    "budgets": "<path d='M2.8 11.5a5.5 5.5 0 1 1 10.4 0'/><path d='M8 10l2.4-3'/><circle cx='8' cy='10.3' r='.8'/>",
    "calendar": "<rect x='2.5' y='3.5' width='11' height='10' rx='1.2'/><path d='M2.5 6.5h11M5.5 2v3M10.5 2v3'/>",
    "appearance": "<circle cx='8' cy='8' r='5.5'/><path d='M8 2.5v11'/><path d='M9.5 4.5h2M9.5 7h3M9.5 9.5h3M9.5 12h1.5'/>",
    "advanced": "<path d='M3 4.5h10M3 8h10M3 11.5h10'/><circle cx='6' cy='4.5' r='1.3'/><circle cx='10' cy='8' r='1.3'/><circle cx='5' cy='11.5' r='1.3'/>",
    "about": "<circle cx='8' cy='8' r='5.5'/><path d='M8 7.3v3.7M8 5v.1'/>",
    "lock": "<rect x='3.5' y='7' width='9' height='6.5' rx='1'/><path d='M5.5 7V5.2a2.5 2.5 0 0 1 5 0V7'/>",
    "globe": "<circle cx='8' cy='8' r='5.5'/><path d='M2.5 8h11M8 2.5c1.6 1.6 2.3 3.5 2.3 5.5S9.6 11.9 8 13.5C6.4 11.9 5.7 10 5.7 8S6.4 4.1 8 2.5z'/>",
    "wallet": "<path d='M2.5 5a1.5 1.5 0 0 1 1.5-1.5h7.5v2'/><rect x='2.5' y='5.5' width='11' height='7.5' rx='1.2'/><circle cx='10.8' cy='9.2' r='.7'/>",
    "smile": "<circle cx='8' cy='8' r='5.5'/><path d='M5.6 9.6a3 3 0 0 0 4.8 0'/><path d='M6 6.4v.1M10 6.4v.1'/>",
    "thumbs-down": "<path d='M5.5 9.5V3H3.5v6.5zM5.5 9.5l2.3 3.8c.9 0 1.5-.7 1.3-1.6L8.6 9.5h3.6a1.3 1.3 0 0 0 1.3-1.5l-.7-4A1.3 1.3 0 0 0 11.5 3h-6'/>",
}


def _mask(body: str) -> str:
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' fill='none' stroke='black' "
        "stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>" + body + "</svg>"
    )
    return 'url("data:image/svg+xml,' + quote(svg, safe="=' ") + '")'


ICON_NAMES = tuple(_ICONS)

ICON_CSS = (
    ".vi::before { content:''; display:inline-block; width:1.08em; height:1.08em; flex:0 0 auto;"
    " vertical-align:-0.17em; background:currentColor;"
    " -webkit-mask:var(--vi) center/contain no-repeat; mask:var(--vi) center/contain no-repeat; }\n"
    ".vi:not(:empty)::before { margin-inline-end:0.55em; }\n"
    "button.vi:empty::before, #pinBtn .pin-i::before, #vfBell > .vi::before { width:1.2em; height:1.2em; }\n"
    + "\n".join(f'.vi[data-ico="{name}"] {{ --vi:{_mask(body)}; }}' for name, body in _ICONS.items())
    + "\n"
)
