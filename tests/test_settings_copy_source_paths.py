"""Settings copy says what a thing does, never where its code lives.

The packaged Settings page told people that "one store holds the model providers AND the
web-search providers (core/credential_store.py)". A repository path means nothing to the
person reading Settings and goes stale the moment the file moves, and the same habit had
spread to other rows (autonomy, email accounts, the sending hold, learned facts, the
remembered-identity note) in every shipped catalog.

Guards, each answering one question:

- the detector flags the repository-path forms that actually leaked and spares ordinary
  copy (API routes, provider URLs, "read/send") -- so a green scan is not a blind scan;
- in EVERY shipped locale, the copy the served page renders from -- the localized settings
  model, the locale bundle, the provider key guide and the static markup -- names no
  repository path;
- a real browser that opens every Settings group through the daemon's own /settings route
  paints no repository path, which also covers widget strings built in the page script.

Code comments inside the served scripts are developer notes nobody sees; they are not copy
and are deliberately not scanned.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from core.i18n.catalog import CATALOGS_DIR
from core.i18n.locales import ui_catalog_tags

REPO = Path(__file__).resolve().parents[1]
_TOP_DIRS = frozenset(p.name for p in REPO.iterdir() if p.is_dir() and not p.name.startswith("."))
_SOURCE_SUFFIXES = (
    ".py", ".js", ".mjs", ".ts", ".sh", ".ps1", ".bat", ".cmd", ".iss", ".vbs",
    ".json", ".md", ".toml", ".yaml", ".yml", ".html", ".css",
)
# Path characters are ASCII on purpose: Korean and Japanese copy attaches particles straight
# to a path ("core/email_accounts.py가"), and a Unicode \w would read the particle as part of
# the file name and miss the leak.
# A slash path not inside a URL or an API route (no slash, dot, colon or word char before it).
_SLASH_PATH = re.compile(r"(?<![A-Za-z0-9_/.:-])([A-Za-z_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.-]+)+)")
# A dotted module path such as core.execution_gate.
_DOTTED = re.compile(r"(?<![A-Za-z0-9_.])([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+)")
# A bare module file name such as credential_store.py.
_BARE_MODULE = re.compile(r"(?<![A-Za-z0-9_/.-])[A-Za-z0-9_]+\.py(?![A-Za-z0-9_])")


def source_paths(text: str) -> list[str]:
    """Every repository source reference in ``text``.

    A slash path counts when it starts at a top-level directory of this repository and
    either exists in the checkout or ends in a source suffix (a renamed file still leaks).
    """
    found: list[str] = []
    for match in _SLASH_PATH.finditer(text):
        candidate = match.group(1).rstrip(".")
        if candidate.split("/", 1)[0] in _TOP_DIRS and (
            (REPO / candidate).exists() or candidate.endswith(_SOURCE_SUFFIXES)
        ):
            found.append(candidate)
    for match in _DOTTED.finditer(text):
        parts = match.group(1).split(".")
        if parts[0] in _TOP_DIRS and (
            REPO.joinpath(*parts).with_suffix(".py").is_file() or REPO.joinpath(*parts).is_dir()
        ):
            found.append(match.group(1))
    found.extend(_BARE_MODULE.findall(text))
    return found


def _strings(value, where: str):
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


def _embedded_json(html: str, marker: str):
    start = html.index(marker) + len(marker)
    value, _end = json.JSONDecoder().raw_decode(html, start)
    return value


_NOT_COPY = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", re.S | re.I)


def _served_copy(html: str) -> dict[str, list[tuple[str, str]]]:
    """The strings the served page renders from, grouped by where they are embedded."""
    return {
        "bundle": list(_strings(_embedded_json(html, "var B = "), "B")),
        "model": list(_strings(_embedded_json(html, "const MODEL = "), "MODEL")),
        "key_guide": list(_strings(_embedded_json(html, "const KEY_PROVIDER_GUIDE = "), "KEY_PROVIDER_GUIDE")),
        "markup": [("markup", _NOT_COPY.sub(" ", html))],
    }


def test_the_detector_flags_repository_paths_and_spares_ordinary_copy() -> None:
    leaked = {
        "one store holds the model providers AND the web-search providers (core/credential_store.py), and": "core/credential_store.py",
        "facts VOOL confirmed during chats (core/memory/entries.py), each shown": "core/memory/entries.py",
        "into this memory (core/user_preferences.py: migrate_legacy_profile_fields), because": "core/user_preferences.py",
        "the credential intake flow (core/credential_intelligence): the key is": "core/credential_intelligence",
        "it runs the way core.execution_gate orders actions": "core.execution_gate",
        "a store file named credential_store.py": "credential_store.py",
        "(불투명한 자격 증명 이름, core/email_accounts.py가 해석)": "core/email_accounts.py",
        "VOOL 在聊天中确认过的事实（core/memory/entries.py），每条": "core/memory/entries.py",
        "一个不透明的凭据名称，由core/email_accounts.py解析": "core/email_accounts.py",
    }
    for text, path in leaked.items():
        assert path in source_paths(text), (text, source_paths(text))
    ordinary = (
        "Add, test or remove your keys. Saving verifies the key with that one service.",
        "GET /api/cloud/models?provider=openrouter",
        "Open https://eyebrow.cc/dashboard to get a key.",
        "Every configured mailbox, what it can do (read/send), and its state.",
        "Version 0.6.0 — tools and plugins stay off until you enable them.",
    )
    for text in ordinary:
        assert source_paths(text) == [], (text, source_paths(text))


@pytest.mark.parametrize("tag", ui_catalog_tags())
def test_served_settings_copy_names_no_repository_path(tag: str) -> None:
    from core.vool_settings_page import render_vool_settings_html, settings_groups

    html = render_vool_settings_html(ui_locale=tag)
    copy = _served_copy(html)

    # Not a blind scan: the locale bundle carries the whole catalog, the model carries every
    # group in served order, and the key row the leak was reported on is among what was read.
    english = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
    bundle_keys = {where.split(".", 2)[2] for where, _ in copy["bundle"] if where.startswith("B.messages.")}
    assert set(english) <= bundle_keys, sorted(set(english) - bundle_keys)[:5]
    model = _embedded_json(html, "const MODEL = ")
    assert [group["id"] for group in model] == [group["id"] for group in settings_groups()]
    keys_help = next(r["help"] for g in model if g["id"] == "keys" for r in g["rows"] if r["id"] == "cloud_keys")
    assert keys_help in {text for _where, text in copy["model"]}
    assert copy["key_guide"], "the provider key guide was not embedded"

    leaks = [
        f"{source}:{where}: {paths} in {text[:120]!r}"
        for source, entries in copy.items()
        for where, text in entries
        if (paths := source_paths(text))
    ]
    assert not leaks, f"{tag}: Settings copy names repository paths:\n" + "\n".join(leaks)


def test_every_settings_group_paints_without_a_repository_path(monkeypatch) -> None:
    from core.vool_settings_page import settings_groups
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.served_browser import launch_chromium

    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    runtime = RuntimeServices(display_name="Settings copy fixture")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def answer(self, result):
            self.send_response(result.status)
            self.send_header("Content-Type", result.content_type)
            self.end_headers()
            self.wfile.write(result.body or b"")

        def do_GET(self):
            u = urlsplit(self.path)
            self.answer(dispatch_get(path=u.path, query=parse_qs(u.query), headers=dict(self.headers),
                                     runtime=runtime, model_name="fixture", client_host="127.0.0.1"))

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            self.answer(dispatch_post(path=urlsplit(self.path).path, body=body, headers=dict(self.headers),
                                      runtime=runtime, model_name="fixture", client_host="127.0.0.1"))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    manager, browser = launch_chromium()
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    # Count the page's own reads so each group is collected after its widgets have answered.
    page.add_init_script(
        "(() => { const f = window.fetch; window.__copyInflight = 0;"
        " window.fetch = function (...a) { window.__copyInflight++;"
        " return f.apply(this, a).finally(() => { window.__copyInflight--; }); }; })()"
    )
    collect = """() => {
      const body = document.body.cloneNode(true);
      body.querySelectorAll('script, style, template, noscript').forEach(n => n.remove());
      const parts = [body.textContent];
      for (const el of document.querySelectorAll('[title], [placeholder], [aria-label], [alt]'))
        for (const a of ['title', 'placeholder', 'aria-label', 'alt']) { const v = el.getAttribute(a); if (v) parts.push(v); }
      return parts.join('\\n');
    }"""
    painted: dict[str, str] = {}
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/settings")
        page.locator("#nav .nav-item").first.wait_for()
        groups = settings_groups()
        assert page.evaluate("MODEL.map(g => g.id)") == [g["id"] for g in groups]
        for group in groups:
            page.locator(f'#nav [data-group="{group["id"]}"]').click()
            page.wait_for_function(
                "t => document.getElementById('paneTitle').textContent === t && window.__copyInflight === 0",
                arg=group["title"],
            )
            painted[group["id"]] = page.evaluate(collect)
        assert not errors, errors
    finally:
        browser.close(); manager.stop(); server.shutdown(); server.server_close()

    # The walk really painted each group's own rows, not a stale or empty pane.
    for group in groups:
        assert group["rows"][0]["label"] in painted[group["id"]], group["id"]
    leaks = {gid: paths for gid, text in painted.items() if (paths := sorted(set(source_paths(text))))}
    assert not leaks, f"Settings groups paint repository paths: {leaks}"
