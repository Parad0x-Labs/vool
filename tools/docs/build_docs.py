# VENDORED from the live vool.dev website toolchain (~/Desktop/hhfdsfdfsfdsdsfsdf/website/tools/build_docs.py,
# verified live 2026-09-19). The website copy stays authoritative for vool.dev deploys; this copy
# exists so the repository's own CI can build and gate /docs with zero package installs. Keep the
# two in sync deliberately: diff against the website copy when touching this file.
#!/usr/bin/env python3
"""
build_docs.py — VOOL documentation site builder.

Reads a GitBook-structured docs tree (.gitbook.yaml + SUMMARY.md + markdown)
and emits a static HTML site styled to match the VOOL website.

Design constraints, deliberate:
  * Python standard library only. No pip install, ever, on any machine.
  * Deterministic output — same input bytes produce the same output bytes.
  * The input contract is plain GitBook, so the same repo can be pointed at
    GitBook.com (GitHub Sync) without touching a single content file.

Usage:
    python3 build_docs.py --src ../docs-source --out ../docs-build
    python3 build_docs.py --src /srv/vool-docs/repo --out /srv/vool-docs/next \
                          --base /docs --site-url https://vool.dev
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Minimal YAML reader — only the small subset .gitbook.yaml actually uses
# (top-level scalars, one level of nesting, and a flat redirects map).
# --------------------------------------------------------------------------


def read_gitbook_yaml(path):
    cfg = {"root": "./docs/", "readme": "README.md", "summary": "SUMMARY.md",
           "redirects": {}}
    if not os.path.isfile(path):
        return cfg
    section = None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            key, _, val = line.strip().partition(":")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if indent == 0:
                section = key if not val else None
                if key == "root" and val:
                    cfg["root"] = val
            elif section == "structure" and key in ("readme", "summary"):
                cfg[key] = val
            elif section == "redirects" and val:
                cfg["redirects"][key] = val
    return cfg


# --------------------------------------------------------------------------
# Markdown -> HTML. A CommonMark subset plus the GitBook extensions that
# matter for product docs. Written out longhand so there is no dependency.
# --------------------------------------------------------------------------

CODE_SPAN = "\x00CODE%d\x00"
RAW_SPAN = "\x00RAW%d\x00"


def slugify(text):
    s = re.sub(r"<[^>]+>", "", text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "section"


class Inline:
    """Inline-level markdown. Code spans are pulled out first so their
    contents are never touched by emphasis or link rules."""

    def __init__(self, link_resolver):
        self.link = link_resolver

    def __call__(self, text):
        codes, raws = [], []

        def stash_code(m):
            codes.append(m.group(2).strip())
            return CODE_SPAN % (len(codes) - 1)

        text = re.sub(r"(`+)(.+?)\1", stash_code, text, flags=re.S)

        # Autolinks before escaping, so the angle brackets survive.
        def stash_auto(m):
            url = m.group(1)
            raws.append('<a href="%s" rel="nofollow noopener" '
                        'target="_blank">%s</a>' % (html.escape(url), html.escape(url)))
            return RAW_SPAN % (len(raws) - 1)

        text = re.sub(r"<((?:https?|mailto):[^>\s]+)>", stash_auto, text)

        text = html.escape(text, quote=False)

        # Images before links — the syntaxes overlap.
        def img(m):
            alt, src, title = m.group(1), m.group(2), m.group(3)
            t = ' title="%s"' % html.escape(title) if title else ""
            return ('<img src="%s" alt="%s"%s loading="lazy" decoding="async">'
                    % (html.escape(self.link(src, asset=True)), html.escape(alt), t))

        text = re.sub(r'!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)', img, text)

        def link(m):
            label, href, title = m.group(1), m.group(2), m.group(3)
            resolved = self.link(href)
            ext = resolved.startswith("http")
            attrs = ' rel="noopener" target="_blank"' if ext else ""
            t = ' title="%s"' % html.escape(title) if title else ""
            return '<a href="%s"%s%s>%s</a>' % (html.escape(resolved), t, attrs, label)

        text = re.sub(r'\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)', link, text)

        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text, flags=re.S)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text, flags=re.S)
        text = re.sub(r"(?<![\w_])__(.+?)__(?![\w_])", r"<strong>\1</strong>", text, flags=re.S)
        text = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", text, flags=re.S)
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text, flags=re.S)

        for i, raw in enumerate(raws):
            text = text.replace(RAW_SPAN % i, raw)
        for i, code in enumerate(codes):
            text = text.replace(CODE_SPAN % i,
                                "<code>%s</code>" % html.escape(code, quote=False))
        return text


HINT_STYLES = {"info": "info", "success": "success", "warning": "warning",
               "danger": "danger", "tip": "success"}


# Language set, matching the marketing site's picker exactly so the two never
# drift. English is the canonical tree and lives at the docs root; every other
# language is a subtree under docs/translations/<code>/ with its own SUMMARY.md.
LANGS = [
    ("en", "English"), ("zh", "\u7b80\u4f53\u4e2d\u6587"), ("fr", "Fran\u00e7ais"),
    ("de", "Deutsch"), ("es", "Espa\u00f1ol"), ("pt", "Portugu\u00eas (BR)"),
    ("ja", "\u65e5\u672c\u8a9e"), ("ko", "\ud55c\uad6d\uc5b4"),
    ("ru", "\u0420\u0443\u0441\u0441\u043a\u0438\u0439"),
    ("uk", "\u0423\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u0430"),
    ("pl", "Polski"), ("lt", "Lietuvi\u0173"), ("lv", "Latvie\u0161u"),
    ("et", "Eesti"), ("sv", "Svenska"), ("fi", "Suomi"), ("nb", "Norsk"),
]
LANG_NAME = dict(LANGS)


def discover_languages(root, base):
    """[(code, directory, url_base)] for every language that has a SUMMARY.md."""
    found = [("en", root, base)]
    tdir = os.path.join(root, "translations")
    if os.path.isdir(tdir):
        for code, _name in LANGS[1:]:
            d = os.path.join(tdir, code)
            if os.path.isfile(os.path.join(d, "SUMMARY.md")):
                found.append((code, d, "%s/%s" % (base, code)))
    return found


class Markdown:
    def __init__(self, link_resolver):
        self.inline = Inline(link_resolver)

    def convert(self, text):
        """Returns (html, toc) where toc is a list of (level, slug, title)."""
        self.toc = []
        self.seen_slugs = {}
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out = []
        self._blocks(lines, 0, len(lines), out)
        return "\n".join(out), self.toc

    # -- helpers ----------------------------------------------------------

    def _slug(self, title):
        base = slugify(title)
        n = self.seen_slugs.get(base, 0)
        self.seen_slugs[base] = n + 1
        return base if n == 0 else "%s-%d" % (base, n)

    def _blocks(self, lines, i, end, out):
        while i < end:
            line = lines[i]
            stripped = line.strip()

            if not stripped:
                i += 1
                continue

            # Fenced code
            m = re.match(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$", line)
            if m:
                indent, fence, lang = m.group(1), m.group(2)[0] * 3, m.group(3)
                i += 1
                buf = []
                while i < end and not re.match(r"^\s*%s+\s*$" % re.escape(fence[0]), lines[i]):
                    buf.append(lines[i][len(indent):] if lines[i].startswith(indent) else lines[i])
                    i += 1
                i += 1
                cls = ' class="language-%s"' % html.escape(lang) if lang else ""
                label = ('<span class="code-lang">%s</span>' % html.escape(lang)) if lang else ""
                out.append('<div class="code-block">%s<pre><code%s>%s</code></pre></div>'
                           % (label, cls, html.escape("\n".join(buf))))
                continue

            # GitBook hint
            m = re.match(r'^\s*\{%\s*hint\s+style="?(\w+)"?\s*%\}\s*$', line)
            if m:
                style = HINT_STYLES.get(m.group(1), "info")
                i += 1
                buf = []
                while i < end and not re.match(r"^\s*\{%\s*endhint\s*%\}", lines[i]):
                    buf.append(lines[i])
                    i += 1
                i += 1
                inner = []
                self._blocks(buf, 0, len(buf), inner)
                out.append('<div class="hint hint-%s">%s</div>' % (style, "\n".join(inner)))
                continue

            # Any other GitBook liquid tag: drop the tag, keep the body.
            if re.match(r"^\s*\{%.*%\}\s*$", line):
                i += 1
                continue

            # ATX heading
            m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
            if m:
                lvl = len(m.group(1))
                title = m.group(2)
                slug = self._slug(title)
                if lvl in (2, 3):
                    self.toc.append((lvl, slug, re.sub(r"[*`_]", "", title)))
                out.append('<h%d id="%s">%s<a class="anchor" href="#%s" '
                           'aria-label="Link to this section">#</a></h%d>'
                           % (lvl, slug, self.inline(title), slug, lvl))
                i += 1
                continue

            # Thematic break
            if re.match(r"^\s*([-*_])(\s*\1){2,}\s*$", line):
                out.append("<hr>")
                i += 1
                continue

            # Table
            if "|" in line and i + 1 < end and re.match(
                    r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]) and "-" in lines[i + 1]:
                i = self._table(lines, i, end, out)
                continue

            # Blockquote
            if re.match(r"^\s*>", line):
                buf = []
                while i < end and (re.match(r"^\s*>", lines[i]) or
                                   (lines[i].strip() and buf)):
                    buf.append(re.sub(r"^\s*> ?", "", lines[i]))
                    i += 1
                inner = []
                self._blocks(buf, 0, len(buf), inner)
                out.append("<blockquote>%s</blockquote>" % "\n".join(inner))
                continue

            # Lists
            if re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
                i = self._list(lines, i, end, out)
                continue

            # Raw HTML block. Must look like an actual tag — a bare autolink
            # such as <https://example.com> also starts with "<" and belongs
            # to the inline pass, not here.
            if re.match(r"^<(?:/|!|[a-zA-Z][\w-]*(?:[\s/>]|$))", stripped):
                buf = []
                while i < end and lines[i].strip():
                    buf.append(lines[i])
                    i += 1
                out.append("\n".join(buf))
                continue

            # Paragraph
            buf = []
            while i < end and lines[i].strip() and not re.match(
                    r"^(#{1,6}\s|\s*([-*+]|\d+[.)])\s|\s*>|\s*(`{3,}|~{3,})|\s*\{%)", lines[i]):
                buf.append(lines[i].strip())
                i += 1
            if buf:
                text = " ".join(buf)
                text = re.sub(r"  +$", "<br>", text)
                out.append("<p>%s</p>" % self.inline(text))

    def _table(self, lines, i, end, out):
        header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        aligns = []
        for cell in lines[i + 1].strip().strip("|").split("|"):
            c = cell.strip()
            if c.startswith(":") and c.endswith(":"):
                aligns.append("center")
            elif c.endswith(":"):
                aligns.append("right")
            else:
                aligns.append("left")
        i += 2
        rows = []
        while i < end and "|" in lines[i] and lines[i].strip():
            rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
            i += 1

        def cells(row, tag):
            parts = []
            for n, cell in enumerate(row):
                a = aligns[n] if n < len(aligns) else "left"
                style = ' style="text-align:%s"' % a if a != "left" else ""
                parts.append("<%s%s>%s</%s>" % (tag, style, self.inline(cell), tag))
            return "".join(parts)

        body = "".join("<tr>%s</tr>" % cells(r, "td") for r in rows)
        out.append('<div class="table-wrap"><table><thead><tr>%s</tr></thead>'
                   "<tbody>%s</tbody></table></div>"
                   % (cells(header, "th"), body))
        return i

    def _list(self, lines, i, end, out, depth=0):
        first = re.match(r"^(\s*)([-*+]|\d+[.)])\s+", lines[i])
        base_indent = len(first.group(1))
        ordered = first.group(2) not in ("-", "*", "+")
        items = []
        cur = None
        loose = False

        while i < end:
            line = lines[i]
            if not line.strip():
                # A blank line ends the list unless what follows still belongs
                # to it — either an indented continuation, or the next item.
                nxt = lines[i + 1] if i + 1 < end else ""
                cont = re.match(r"^\s{%d,}\S" % (base_indent + 1), nxt)
                sibling = re.match(r"^\s{%d}([-*+]|\d+[.)])\s+" % base_indent, nxt)
                if cont or sibling:
                    loose = True
                    if cont and cur is not None:
                        cur.append("")
                    i += 1
                    continue
                break
            m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
            if m and len(m.group(1)) == base_indent:
                cur = [m.group(3)]
                items.append(cur)
                i += 1
                continue
            if m and len(m.group(1)) > base_indent:
                if cur is None:
                    break
                cur.append(line[base_indent:])
                i += 1
                continue
            if len(line) - len(line.lstrip()) > base_indent and cur is not None:
                cur.append(line[base_indent:])
                i += 1
                continue
            break

        parts = []
        for item in items:
            if "" in item:
                loose = True
        for item in items:
            body = []
            # A task-list checkbox renders as a real disabled input.
            head = item[0]
            task = re.match(r"^\[([ xX])\]\s+(.*)$", head)
            prefix = ""
            if task:
                checked = " checked" if task.group(1).lower() == "x" else ""
                prefix = '<input type="checkbox" disabled%s> ' % checked
                item = [task.group(2), *item[1:]]
            self._blocks(item, 0, len(item), body)
            inner = "\n".join(body)
            # In a tight list the item's leading paragraph is unwrapped, so
            # "- a" is <li>a</li> and "- a\n  - b" is <li>a<ul>...</ul></li>.
            if not loose:
                inner = re.sub(r"^<p>(.*?)</p>\n?", r"\1", inner, count=1, flags=re.S)
            parts.append("<li>%s%s</li>" % (prefix, inner))

        tag = "ol" if ordered else "ul"
        cls = ' class="contains-task-list"' if any(
            "<input type=\"checkbox\"" in p for p in parts) else ""
        out.append("<%s%s>%s</%s>" % (tag, cls, "".join(parts), tag))
        return i


# --------------------------------------------------------------------------
# SUMMARY.md -> navigation tree
# --------------------------------------------------------------------------


class Entry:
    __slots__ = (
        "body",
        "children",
        "description",
        "external",
        "level",
        "section",
        "src",
        "title",
        "toc",
        "url",
    )

    def __init__(self, title, src, url, section, level, external=False):
        self.title, self.src, self.url = title, src, url
        self.section, self.level, self.external = section, level, external
        self.children = []
        self.toc, self.description, self.body = [], "", ""


def parse_summary(path, base):
    entries, section = [], ""
    if not os.path.isfile(path):
        return entries
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            m = re.match(r"^##\s+(.*)$", line)
            if m:
                section = m.group(1).strip()
                continue
            if re.match(r"^#\s+", line):
                continue
            m = re.match(r"^(\s*)[*+-]\s+\[([^\]]+)\]\(([^)]+)\)", line)
            if not m:
                continue
            indent, title, href = len(m.group(1)), m.group(2).strip(), m.group(3).strip()
            level = indent // 2
            if href.startswith("http"):
                entries.append(Entry(title, None, href, section, level, external=True))
            else:
                entries.append(Entry(title, href, url_for(href, base), section, level))
    return entries


def url_for(src, base):
    """docs-relative markdown path -> clean site URL."""
    p = src.split("#")[0].split("?")[0]
    p = re.sub(r"^\./", "", p).strip("/")
    if p.lower() in ("readme.md", "index.md", ""):
        return base + "/"
    p = re.sub(r"\.md$", "", p, flags=re.I)
    if p.lower().endswith("/readme"):
        p = p[: -len("/readme")]
    return "%s/%s/" % (base, p)


def out_path_for(url, base, outdir):
    rel = url[len(base):].strip("/")
    return os.path.join(outdir, rel, "index.html") if rel else os.path.join(outdir, "index.html")


# --------------------------------------------------------------------------
# Front matter + plain-text extraction
# --------------------------------------------------------------------------


def split_front_matter(text):
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n?", text, flags=re.S)
    if m:
        for line in m.group(1).split("\n"):
            k, _, v = line.partition(":")
            if v.strip():
                meta[k.strip()] = v.strip().strip('"').strip("'")
        text = text[m.end():]
    return meta, text


def plain_text(html_str, limit=None):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_str, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit] if limit else t


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def render_nav(entries, current_url):
    out, section = [], None
    for e in entries:
        if e.section != section:
            section = e.section
            if section:
                out.append('<div class="nav-group">%s</div>' % html.escape(section))
        cls = ["nav-link"]
        if e.level:
            cls.append("lvl-%d" % min(e.level, 3))
        if e.url == current_url:
            cls.append("is-current")
        attrs = ' rel="noopener" target="_blank"' if e.external else ""
        aria = ' aria-current="page"' if e.url == current_url else ""
        out.append('<a class="%s" href="%s"%s%s>%s</a>'
                   % (" ".join(cls), html.escape(e.url), attrs, aria, html.escape(e.title)))
    return "\n".join(out)


BOOT_JS = (
    "(function(){var t;try{t=localStorage.getItem('vool-theme')}catch(e){}"
    "if(t!=='light'&&t!=='dark'){t=(window.matchMedia&&"
    "matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light'}"
    "document.documentElement.setAttribute('data-theme',t)})();")

ICON_MOON = ('<svg class="icon-moon" viewBox="0 0 24 24" aria-hidden="true">'
             '<path d="M16.5 4.5 C10.4 5 5.7 10.1 6 16.2 C6.2 19.4 8 21.6 10.6 22.6 '
             'C9.5 20.9 8.9 18.8 9 16.5 C9.2 11 12.2 6.5 16.5 4.5"/></svg>')
ICON_SUN = ('<svg class="icon-sun" viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M12 7.6 C14.5 7.7 16.4 9.7 16.3 12.2 C16.2 14.7 14.2 16.5 11.7 16.4 '
            'C9.3 16.3 7.4 14.3 7.5 11.8 C7.6 9.5 9.5 7.5 12 7.6"/>'
            '<path d="M12 2 C12 2.8 12 3.6 12.1 4.4 M12 19.7 C12 20.5 12 21.3 11.9 22.1 '
            'M2.1 12.1 C2.9 12 3.7 12 4.5 12 M19.6 12 C20.4 12 21.2 12 22 11.9 '
            'M5 5.1 C5.6 5.7 6.2 6.3 6.7 6.9 M17.4 17.2 C18 17.8 18.6 18.4 19.1 19 '
            'M19 5 C18.4 5.6 17.8 6.2 17.3 6.8 M6.6 17.3 C6 17.9 5.4 18.5 4.9 19.1"/></svg>')


def render_lang_picker(lang, targets):
    """targets: [(code, name, url, translated_bool)] — always every language."""
    if len(targets) < 2:
        return ""
    items = []
    for code, name, url, translated in targets:
        cls = "lang-opt" + (" is-current" if code == lang else "") + \
              ("" if translated else " untranslated")
        items.append('<a class="%s" href="%s" lang="%s" hreflang="%s"%s>'
                     '<i>%s</i>%s</a>'
                     % (cls, html.escape(url), code, code,
                        ' aria-current="true"' if code == lang else "",
                        code.upper(), html.escape(name)))
    return ('<div class="lang-wrap">'
            '<button class="lang-btn" id="langBtn" type="button" aria-haspopup="true" '
            'aria-expanded="false" aria-label="Change language">'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
            '<path d="M3 12h18M12 3c2.6 2.6 2.6 15.4 0 18M12 3c-2.6 2.6-2.6 15.4 0 18"/>'
            '</svg><span>%s</span></button>'
            '<div class="lang-menu" id="langMenu" hidden>%s</div></div>'
            % (lang.upper(), "".join(items)))


def render_page(entry, entries, cfg, prev_e, next_e, lang="en", targets=(),
                alternates=()):
    base, site = cfg["base"], cfg["site_url"]
    canonical = site + entry.url
    title = "VOOL Docs" if entry.url == base + "/" else "%s \u00b7 VOOL Docs" % entry.title
    desc = entry.description or plain_text(entry.body, 155)

    toc_html = ""
    if len([t for t in entry.toc if t[0] == 2]) >= 2:
        items = "".join(
            '<a class="toc-l%d" href="#%s">%s</a>' % (lvl, slug, html.escape(t))
            for lvl, slug, t in entry.toc)
        toc_html = ('<aside class="toc" aria-label="On this page">'
                    '<div class="toc-head">On this page</div>%s</aside>' % items)

    crumbs = ['<a href="%s/">Docs</a>' % base]
    if entry.section:
        crumbs.append("<span>%s</span>" % html.escape(entry.section))
    if entry.url != base + "/":
        crumbs.append("<span>%s</span>" % html.escape(entry.title))

    pager = []
    if prev_e:
        pager.append('<a class="pager prev" href="%s"><span>Previous</span><b>%s</b></a>'
                     % (html.escape(prev_e.url), html.escape(prev_e.title)))
    if next_e:
        pager.append('<a class="pager next" href="%s"><span>Next</span><b>%s</b></a>'
                     % (html.escape(next_e.url), html.escape(next_e.title)))

    # hreflang alternates — only for languages that actually have this page.
    alt = "".join('<link rel="alternate" hreflang="%s" href="%s%s">'
                  % (c, site, html.escape(u)) for c, u in alternates)
    if alternates:
        en = dict(alternates).get("en")
        if en:
            alt += '<link rel="alternate" hreflang="x-default" href="%s%s">' % (
                site, html.escape(en))

    ld = {
        "@context": "https://schema.org",
        "@type": "TechArticle",
        "headline": entry.title,
        "description": desc,
        "url": canonical,
        "inLanguage": lang,
        "isPartOf": {"@type": "WebSite", "name": "VOOL Documentation",
                     "url": site + cfg["asset_base"] + "/"},
        "publisher": {"@type": "Organization", "name": "Parad0x Labs", "url": site},
    }

    return TEMPLATE.format(
        lang=lang,
        boot=BOOT_JS,
        title=html.escape(title),
        desc=html.escape(desc, quote=True),
        canonical=html.escape(canonical),
        alternates=alt,
        base=base,
        abase=cfg["asset_base"],
        site=site,
        nav=render_nav(entries, entry.url),
        crumbs='<span class="sep">/</span>'.join(crumbs),
        langpicker=render_lang_picker(lang, targets),
        moon=ICON_MOON, sun=ICON_SUN,
        h1="" if entry.body.lstrip().startswith("<h1") else "<h1>%s</h1>" % html.escape(entry.title),
        body=entry.body,
        toc=toc_html,
        pager='<nav class="pager-row">%s</nav>' % "".join(pager) if pager else "",
        jsonld=json.dumps(ld, ensure_ascii=False, separators=(",", ":")),
        year=datetime.now(timezone.utc).year,
    )


TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}">
{alternates}
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:url" content="{canonical}">
<meta property="og:site_name" content="VOOL">
<meta property="og:locale" content="{lang}">
<meta property="og:image" content="{site}/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=IBM+Plex+Mono:ital,wght@0,400;0,500;1,400&family=Noto+Sans+SC:wght@400;500;700&family=Noto+Sans+JP:wght@400;500;700&family=Noto+Sans+KR:wght@400;500;700&display=swap">
<link rel="stylesheet" href="{abase}/assets/docs.css">
<script>{boot}</script>
<script type="application/ld+json">{jsonld}</script>
</head>
<body data-search="{base}/search.json">
<a class="skip" href="#main">Skip to content</a>

<header class="topbar">
  <button class="menu-btn" id="menuBtn" aria-label="Open navigation" aria-expanded="false">
    <span></span><span></span><span></span>
  </button>
  <a class="brand" href="/"><span class="brand-mark">VOOL</span><span class="brand-sub">Docs</span></a>
  <div class="search">
    <input id="q" type="search" placeholder="Search documentation" autocomplete="off"
           aria-label="Search documentation" spellcheck="false">
    <div class="results" id="results" role="listbox" hidden></div>
  </div>
  <div class="tools">
    {langpicker}
    <button class="theme-toggle" id="themeBtn" type="button" aria-label="Switch theme">
      {moon}{sun}
    </button>
    <nav class="topnav"><a href="/">Website</a></nav>
  </div>
</header>

<div class="shell">
  <aside class="sidebar" id="sidebar" aria-label="Documentation">
    <nav class="nav">{nav}</nav>
  </aside>
  <div class="scrim" id="scrim" hidden></div>

  <main class="main" id="main">
    <nav class="crumbs" aria-label="Breadcrumb">{crumbs}</nav>
    <article class="prose">
      {h1}
      {body}
    </article>
    {pager}
    <footer class="foot">
      <span>VOOL \u2014 Parad0x Labs \u00b7 {year}</span>
    </footer>
  </main>

  {toc}
</div>

<script src="{abase}/assets/docs.js" defer></script>
</body>
</html>
"""


DOCS_CSS = r""":root{
  --paper:#F4F1EA; --paper-2:#EDE9E0; --paper-3:#E6E2D8;
  --ink:#1C1A14; --char:#454136; --mut:#7C7668;
  --line:rgba(28,26,20,.16); --line-strong:rgba(28,26,20,.34);
  --red:#C2401C;
  --sans:"Archivo",-apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --sb:264px; --toc:212px; --bar:56px;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#131210; --paper-2:#1A1815; --paper-3:#232019;
    --ink:#EDEAE1; --char:#C6C1B4; --mut:#8C8678;
    --line:rgba(237,234,225,.14); --line-strong:rgba(237,234,225,.3);
    --red:#E4693F;
    color-scheme:dark;
  }
}
:root[data-theme="dark"]{
  --paper:#131210; --paper-2:#1A1815; --paper-3:#232019;
  --ink:#EDEAE1; --char:#C6C1B4; --mut:#8C8678;
  --line:rgba(237,234,225,.14); --line-strong:rgba(237,234,225,.3);
  --red:#E4693F; color-scheme:dark;
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth;scroll-padding-top:calc(var(--bar) + 18px)}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:16px;line-height:1.65;-webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:inherit}
img{max-width:100%;height:auto}
.skip{position:absolute;left:-9999px;top:0;background:var(--ink);color:var(--paper);
  padding:10px 16px;z-index:100}
.skip:focus{left:8px;top:8px}

/* ---------- top bar ---------- */
.topbar{position:sticky;top:0;z-index:40;height:var(--bar);display:flex;align-items:center;
  gap:16px;padding:0 clamp(14px,2.4vw,24px);background:color-mix(in srgb,var(--paper) 88%,transparent);
  backdrop-filter:saturate(140%) blur(10px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:baseline;gap:8px;text-decoration:none;flex:0 0 auto}
.brand-mark{font-weight:700;letter-spacing:.16em;font-size:15px}
.brand-sub{font-family:var(--mono);font-size:11px;color:var(--mut);letter-spacing:.14em;
  text-transform:uppercase}
.topnav{margin-left:auto;display:flex;gap:20px;flex:0 0 auto}
.topnav a{font-size:13.5px;color:var(--char);text-decoration:none}
.topnav a:hover{color:var(--ink)}
.menu-btn{display:none;flex-direction:column;gap:4px;width:34px;height:34px;padding:8px;
  border:1px solid var(--line);border-radius:7px;background:none;cursor:pointer}
.menu-btn span{display:block;height:1.5px;background:var(--ink);border-radius:2px}

/* ---------- header tools: language + theme ---------- */
.tools{margin-left:auto;display:flex;align-items:center;gap:8px;flex:0 0 auto}
.topnav{display:flex;gap:18px;margin-left:6px}
.theme-toggle{display:grid;place-items:center;width:34px;height:34px;padding:0;
  border:1px solid var(--line);border-radius:8px;background:none;cursor:pointer;color:var(--char)}
.theme-toggle:hover{color:var(--ink);border-color:var(--line-strong)}
.theme-toggle svg{width:17px;height:17px;display:none;fill:none;stroke:currentColor;
  stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
[data-theme="light"] .theme-toggle .icon-moon{display:block}
[data-theme="dark"] .theme-toggle .icon-sun{display:block}
html:not([data-theme]) .theme-toggle .icon-moon{display:block}

.lang-wrap{position:relative}
.lang-btn{display:flex;align-items:center;gap:6px;height:34px;padding:0 10px;
  font:inherit;font-family:var(--mono);font-size:11.5px;letter-spacing:.08em;
  color:var(--char);background:none;border:1px solid var(--line);border-radius:8px;cursor:pointer}
.lang-btn:hover{color:var(--ink);border-color:var(--line-strong)}
.lang-btn svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.5}
.lang-menu{position:absolute;top:calc(100% + 7px);right:0;width:210px;max-height:min(70vh,420px);
  overflow:auto;padding:5px;background:var(--paper);border:1px solid var(--line-strong);
  border-radius:10px;box-shadow:0 18px 44px rgba(28,26,20,.18);z-index:60}
.lang-opt{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:7px;
  font-size:13.5px;color:var(--char);text-decoration:none}
.lang-opt:hover{background:var(--paper-2);color:var(--ink)}
.lang-opt i{font-family:var(--mono);font-size:10px;font-style:normal;letter-spacing:.1em;
  color:var(--mut);min-width:20px}
.lang-opt.is-current{color:var(--ink);font-weight:600}
.lang-opt.is-current i{color:var(--red)}
.lang-opt.untranslated{opacity:.55}
:lang(zh),:lang(ja),:lang(ko){font-family:"Noto Sans SC","Noto Sans JP","Noto Sans KR",var(--sans)}

/* ---------- search ---------- */
.search{position:relative;flex:1 1 auto;max-width:420px;min-width:0}
.search input{width:100%;height:34px;padding:0 12px;font:inherit;font-size:13.5px;
  color:var(--ink);background:var(--paper-2);border:1px solid var(--line);border-radius:8px}
.search input:focus{outline:none;border-color:var(--line-strong);background:var(--paper)}
.results{position:absolute;top:calc(100% + 7px);left:0;right:0;max-height:min(66vh,460px);
  overflow:auto;background:var(--paper);border:1px solid var(--line-strong);border-radius:10px;
  box-shadow:0 18px 44px rgba(28,26,20,.16);padding:5px;z-index:50}
.results a{display:block;padding:9px 11px;border-radius:7px;text-decoration:none}
.results a:hover,.results a.sel{background:var(--paper-2)}
.results b{display:block;font-size:13.5px;font-weight:600}
.results span{display:block;font-size:12px;color:var(--mut);overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.results .empty{padding:14px;font-size:13px;color:var(--mut)}

/* ---------- shell ---------- */
.shell{display:grid;grid-template-columns:var(--sb) minmax(0,1fr) var(--toc);
  align-items:start;max-width:1480px;margin:0 auto}
.sidebar{position:sticky;top:var(--bar);height:calc(100svh - var(--bar));overflow-y:auto;
  padding:22px 12px 40px clamp(14px,2.4vw,24px);border-right:1px solid var(--line);min-width:0}
.nav{display:flex;flex-direction:column;gap:1px}
.nav-group{margin:20px 0 7px;padding:0 10px;font-family:var(--mono);font-size:10.5px;
  letter-spacing:.16em;text-transform:uppercase;color:var(--mut)}
.nav-group:first-child{margin-top:0}
.nav-link{display:block;padding:6px 10px;border-radius:7px;font-size:14px;color:var(--char);
  text-decoration:none;border-left:2px solid transparent}
.nav-link:hover{background:var(--paper-2);color:var(--ink)}
.nav-link.is-current{background:var(--paper-2);color:var(--ink);font-weight:600;
  border-left-color:var(--red)}
.nav-link.lvl-1{padding-left:24px;font-size:13.5px}
.nav-link.lvl-2{padding-left:38px;font-size:13px}
.scrim{position:fixed;inset:0;background:rgba(28,26,20,.4);z-index:29}

.main{min-width:0;padding:26px clamp(18px,3.6vw,52px) 72px;max-width:none}
.crumbs{display:flex;flex-wrap:wrap;gap:7px;align-items:center;font-family:var(--mono);
  font-size:11.5px;color:var(--mut);margin-bottom:16px}
.crumbs a{text-decoration:none}.crumbs a:hover{color:var(--ink)}
.crumbs .sep{opacity:.5}

/* ---------- prose ---------- */
.prose{max-width:74ch}
.prose h1{font-size:clamp(29px,3.6vw,40px);line-height:1.14;letter-spacing:-.022em;
  font-weight:700;margin:0 0 18px}
.prose h2{font-size:clamp(21px,2.2vw,26px);line-height:1.26;letter-spacing:-.014em;
  font-weight:650;margin:46px 0 12px;padding-top:14px;border-top:1px solid var(--line)}
.prose h3{font-size:17.5px;font-weight:650;margin:30px 0 9px}
.prose h4{font-size:15.5px;font-weight:650;margin:24px 0 7px;color:var(--char)}
.prose p{margin:0 0 15px;color:var(--char)}
.prose>p:first-of-type{font-size:17.5px;line-height:1.6;color:var(--ink)}
.prose a{color:var(--ink);text-decoration:underline;text-decoration-color:var(--line-strong);
  text-underline-offset:3px;text-decoration-thickness:1px}
.prose a:hover{text-decoration-color:var(--red);color:var(--red)}
.prose strong{font-weight:650;color:var(--ink)}
.prose ul,.prose ol{margin:0 0 15px;padding-left:22px;color:var(--char)}
.prose li{margin:5px 0}
.prose li::marker{color:var(--mut)}
.prose ul.contains-task-list{list-style:none;padding-left:2px}
.prose hr{border:0;border-top:1px solid var(--line);margin:34px 0}
.prose blockquote{margin:0 0 18px;padding:2px 0 2px 18px;border-left:2px solid var(--red);
  color:var(--char);font-style:italic}
.prose blockquote p:last-child{margin-bottom:0}
.anchor{margin-left:9px;color:var(--mut);text-decoration:none;opacity:0;font-weight:400;
  font-family:var(--mono);font-size:.72em}
h1:hover .anchor,h2:hover .anchor,h3:hover .anchor,h4:hover .anchor{opacity:.6}
.anchor:hover{opacity:1!important;color:var(--red)}

.prose :not(pre)>code{font-family:var(--mono);font-size:.87em;padding:.14em .4em;
  background:var(--paper-3);border:1px solid var(--line);border-radius:5px;
  word-break:break-word}
.code-block{position:relative;margin:0 0 20px;background:var(--paper-2);
  border:1px solid var(--line);border-radius:10px;overflow:hidden}
.code-lang{position:absolute;top:0;right:0;padding:4px 11px;font-family:var(--mono);
  font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);
  border-left:1px solid var(--line);border-bottom:1px solid var(--line);
  border-bottom-left-radius:8px}
.code-block pre{margin:0;padding:16px 18px;overflow-x:auto;font-family:var(--mono);
  font-size:13px;line-height:1.7}
.code-block code{font-family:inherit}
.copy{position:absolute;top:7px;right:8px;padding:4px 9px;font-family:var(--mono);
  font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);
  background:var(--paper);border:1px solid var(--line);border-radius:6px;cursor:pointer;
  opacity:0;transition:opacity .14s}
.code-block:hover .copy,.copy:focus{opacity:1}
.copy:hover{color:var(--ink);border-color:var(--line-strong)}
.code-block:has(.copy) .code-lang{display:none}

.table-wrap{overflow-x:auto;margin:0 0 20px;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:10px 14px;text-align:left;border-bottom:1px solid var(--line)}
th{font-weight:650;background:var(--paper-2);white-space:nowrap}
tbody tr:last-child td{border-bottom:0}

.hint{margin:0 0 20px;padding:14px 16px;border:1px solid var(--line);border-left-width:3px;
  border-radius:9px;background:var(--paper-2);font-size:14.5px}
.hint p:last-child{margin-bottom:0}
.hint-info{border-left-color:#3B6EA5}
.hint-success{border-left-color:#2E7D5B}
.hint-warning{border-left-color:#B07A1E}
.hint-danger{border-left-color:var(--red)}

/* ---------- pager + toc ---------- */
.pager-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;
  max-width:74ch;margin:54px 0 0}
.pager{padding:13px 16px;border:1px solid var(--line);border-radius:10px;text-decoration:none;
  background:var(--paper-2)}
.pager:hover{border-color:var(--line-strong);background:var(--paper)}
.pager span{display:block;font-family:var(--mono);font-size:10.5px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--mut);margin-bottom:3px}
.pager b{font-size:14.5px;font-weight:600}
.pager.next{text-align:right}
.foot{max-width:74ch;margin-top:44px;padding-top:18px;border-top:1px solid var(--line);
  font-family:var(--mono);font-size:11.5px;color:var(--mut)}

.toc{position:sticky;top:var(--bar);max-height:calc(100svh - var(--bar));overflow-y:auto;
  padding:26px 20px 40px 8px;min-width:0}
.toc-head{font-family:var(--mono);font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--mut);margin-bottom:9px}
.toc a{display:block;padding:4px 0 4px 11px;font-size:13px;color:var(--mut);
  text-decoration:none;border-left:1.5px solid var(--line)}
.toc a:hover{color:var(--ink)}
.toc a.active{color:var(--ink);border-left-color:var(--red)}
.toc .toc-l3{padding-left:24px;font-size:12.5px}

/* ---------- responsive ---------- */
@media (max-width:1180px){
  .shell{grid-template-columns:var(--sb) minmax(0,1fr)}
  .toc{display:none}
}
@media (max-width:860px){
  .shell{grid-template-columns:minmax(0,1fr)}
  .menu-btn{display:flex}
  .topnav{display:none}
  .topbar{gap:9px;padding:0 12px}
  .brand-sub{display:none}
  .lang-btn span{display:none}
  .lang-btn{padding:0 9px}
  .lang-menu{width:190px}
  .sidebar{position:fixed;top:var(--bar);left:0;width:min(86vw,var(--sb));z-index:30;
    background:var(--paper);transform:translateX(-102%);transition:transform .2s ease;
    border-right:1px solid var(--line-strong)}
  body.nav-open .sidebar{transform:none}
  .scrim[hidden]{display:none}
  .main{padding:20px 18px 60px}
}
@media (max-width:480px){
  .brand-mark{font-size:14px}
  .search input{font-size:16px}   /* 16px stops iOS zooming the page on focus */
}
@media (prefers-reduced-motion:reduce){.sidebar{transition:none}}
@media print{
  .topbar,.sidebar,.toc,.pager-row,.scrim{display:none!important}
  .shell{display:block}.main{padding:0}
}
"""


DOCS_JS = r"""(function(){
  "use strict";
  var SEARCH_URL = document.body.getAttribute("data-search") || "/docs/search.json";

  /* ---- theme: shares the vool-theme key with the marketing site ---- */
  var root = document.documentElement, themeBtn = document.getElementById("themeBtn");
  function label(){
    var dark = root.getAttribute("data-theme") === "dark";
    if (themeBtn) themeBtn.setAttribute("aria-label",
      dark ? "Switch to light theme" : "Switch to dark theme");
  }
  label();
  if (themeBtn) themeBtn.addEventListener("click", function(){
    var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("vool-theme", next); } catch (e) {}
    label();
  });
  // Follow the system while the reader has not chosen for themselves.
  if (window.matchMedia) {
    var mq = matchMedia("(prefers-color-scheme: dark)");
    var onChange = function(e){
      var saved = null;
      try { saved = localStorage.getItem("vool-theme"); } catch (err) {}
      if (saved !== "light" && saved !== "dark") {
        root.setAttribute("data-theme", e.matches ? "dark" : "light");
        label();
      }
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  /* ---- language menu ---- */
  var langBtn = document.getElementById("langBtn"),
      langMenu = document.getElementById("langMenu");
  function setLang(open){
    if (!langMenu) return;
    langMenu.hidden = !open;
    langBtn.setAttribute("aria-expanded", open ? "true" : "false");
  }
  if (langBtn) {
    langBtn.addEventListener("click", function(e){
      e.stopPropagation(); setLang(langMenu.hidden);
    });
    document.addEventListener("click", function(e){
      if (!e.target.closest(".lang-wrap")) setLang(false);
    });
    document.addEventListener("keydown", function(e){
      if (e.key === "Escape") setLang(false);
    });
    langMenu.addEventListener("click", function(e){
      var a = e.target.closest("a[hreflang]");
      if (a) { try { localStorage.setItem("vool-lang", a.getAttribute("hreflang")); } catch (err) {} }
    });
  }

  /* ---- mobile nav ---- */
  var btn = document.getElementById("menuBtn"),
      scrim = document.getElementById("scrim");
  function setNav(open){
    document.body.classList.toggle("nav-open", open);
    if (btn) btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (scrim) scrim.hidden = !open;
  }
  if (btn) btn.addEventListener("click", function(){
    setNav(!document.body.classList.contains("nav-open"));
  });
  if (scrim) scrim.addEventListener("click", function(){ setNav(false); });

  /* ---- keep the current sidebar item in view ---- */
  var cur = document.querySelector(".nav-link.is-current");
  if (cur && cur.scrollIntoView) {
    var sb = document.getElementById("sidebar");
    if (sb && cur.offsetTop > sb.clientHeight - 80) {
      sb.scrollTop = cur.offsetTop - sb.clientHeight / 2;
    }
  }

  /* ---- copy buttons on code blocks ---- */
  if (navigator.clipboard) {
    document.querySelectorAll(".code-block").forEach(function(block){
      var b = document.createElement("button");
      b.className = "copy"; b.type = "button"; b.textContent = "Copy";
      b.addEventListener("click", function(){
        var code = block.querySelector("code");
        navigator.clipboard.writeText(code ? code.textContent : "").then(function(){
          b.textContent = "Copied";
          setTimeout(function(){ b.textContent = "Copy"; }, 1400);
        });
      });
      block.appendChild(b);
    });
  }

  /* ---- on-page TOC highlighting ---- */
  var links = [].slice.call(document.querySelectorAll(".toc a"));
  if (links.length && "IntersectionObserver" in window) {
    var map = {};
    links.forEach(function(a){ map[a.getAttribute("href").slice(1)] = a; });
    var heads = Object.keys(map).map(function(id){ return document.getElementById(id); })
                               .filter(Boolean);
    var seen = new Set();
    var io = new IntersectionObserver(function(ents){
      ents.forEach(function(e){
        if (e.isIntersecting) seen.add(e.target.id); else seen.delete(e.target.id);
      });
      var first = heads.find(function(h){ return seen.has(h.id); });
      links.forEach(function(a){ a.classList.remove("active"); });
      if (first && map[first.id]) map[first.id].classList.add("active");
    }, { rootMargin: "-70px 0px -72% 0px" });
    heads.forEach(function(h){ io.observe(h); });
  }

  /* ---- search ---- */
  var q = document.getElementById("q"),
      panel = document.getElementById("results"),
      index = null, loading = false, sel = -1;

  function load(){
    if (index || loading) return;
    loading = true;
    fetch(SEARCH_URL).then(function(r){ return r.json(); })
      .then(function(d){ index = d; loading = false; if (q.value) run(); })
      .catch(function(){ loading = false; });
  }
  function esc(s){ return String(s).replace(/[&<>"]/g, function(c){
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }

  function run(){
    var term = q.value.trim().toLowerCase();
    if (!term) { panel.hidden = true; panel.innerHTML = ""; return; }
    if (!index) { load(); return; }
    var words = term.split(/\s+/).filter(Boolean);
    var hits = [];
    index.forEach(function(p){
      var hay = (p.t + " " + p.s + " " + p.b).toLowerCase(), score = 0, ok = true;
      words.forEach(function(w){
        if (hay.indexOf(w) === -1) { ok = false; return; }
        if (p.t.toLowerCase().indexOf(w) !== -1) score += 12;
        if (p.s.toLowerCase().indexOf(w) !== -1) score += 4;
        score += 1;
      });
      if (ok) hits.push({ p: p, score: score });
    });
    hits.sort(function(a, b){ return b.score - a.score; });
    hits = hits.slice(0, 8);
    sel = -1;
    if (!hits.length) {
      panel.innerHTML = '<div class="empty">No matches for &ldquo;' + esc(term) + '&rdquo;</div>';
      panel.hidden = false; return;
    }
    panel.innerHTML = hits.map(function(h){
      var i = h.p.b.toLowerCase().indexOf(words[0]);
      var snip = i > -1 ? h.p.b.slice(Math.max(0, i - 34), i + 96) : h.p.b.slice(0, 120);
      return '<a href="' + esc(h.p.u) + '" role="option"><b>' + esc(h.p.t) +
             "</b><span>" + esc(snip) + "</span></a>";
    }).join("");
    panel.hidden = false;
  }

  if (q) {
    q.addEventListener("focus", load);
    q.addEventListener("input", run);
    q.addEventListener("keydown", function(e){
      var items = [].slice.call(panel.querySelectorAll("a"));
      if (e.key === "Escape") { panel.hidden = true; q.blur(); return; }
      if (!items.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items.forEach(function(a){ a.classList.remove("sel"); });
        items[sel].classList.add("sel");
        items[sel].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && sel > -1) {
        e.preventDefault(); items[sel].click();
      }
    });
    document.addEventListener("click", function(e){
      if (!e.target.closest(".search")) panel.hidden = true;
    });
    document.addEventListener("keydown", function(e){
      if ((e.key === "/" || (e.key === "k" && (e.metaKey || e.ctrlKey))) &&
          document.activeElement !== q) {
        e.preventDefault(); q.focus(); q.select();
      }
    });
  }
})();
"""


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


def build(src, out, base, site_url, edit_base):
    cfg_yaml = read_gitbook_yaml(os.path.join(src, ".gitbook.yaml"))
    root = os.path.normpath(os.path.join(src, cfg_yaml["root"].lstrip("./") or "."))
    if not os.path.isdir(root):
        root = src

    languages = discover_languages(root, base)
    cfg_base = {"site_url": site_url.rstrip("/"), "edit_base": edit_base,
                "asset_base": base}

    # ---- pass 1: parse and convert every language ----------------------
    trees, missing_all = {}, {}
    for code, ldir, lbase in languages:
        entries = parse_summary(os.path.join(ldir, cfg_yaml["summary"]), lbase)
        if not entries:
            print("!! %s: no entries in %s — skipped" % (code, cfg_yaml["summary"]),
                  file=sys.stderr)
            continue
        missing = []
        for e in (x for x in entries if not x.external):
            path = os.path.join(ldir, e.src)
            if not os.path.isfile(path):
                missing.append(e.src)
                e.body = ("<p>This page is listed in <code>SUMMARY.md</code> but the "
                          "file <code>%s</code> does not exist yet.</p>"
                          % html.escape(e.src))
                continue
            with open(path, encoding="utf-8") as fh:
                meta, text = split_front_matter(fh.read())
            e.description = meta.get("description", "")
            md = Markdown(lambda h, asset=False, _s=e.src, _b=lbase:
                          resolve_link(h, _s, _b, asset, base))
            e.body, e.toc = md.convert(text)
        trees[code] = (lbase, entries)
        missing_all[code] = missing

    if not trees:
        print("!! nothing to build — no language has a usable SUMMARY.md",
              file=sys.stderr)
        return 1

    # Which languages actually carry each page, for hreflang and the picker.
    have = {code: {e.src: e.url for e in entries if not e.external}
            for code, (_lb, entries) in trees.items()}

    # ---- pass 2: emit ---------------------------------------------------
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    total = 0
    for code, (lbase, entries) in trees.items():
        outdir = out if code == "en" else os.path.join(out, code)
        cfg = dict(cfg_base, base=lbase)
        internal = [e for e in entries if not e.external]
        search = []

        for n, e in enumerate(internal):
            # The same logical page in every other language, for the picker.
            targets, alternates = [], []
            for lc, lname in LANGS:
                if lc not in trees:
                    continue
                lb = trees[lc][0]
                url = have[lc].get(e.src)
                translated = url is not None
                targets.append((lc, lname, url or (lb + "/"), translated))
                if translated:
                    alternates.append((lc, url))

            page = render_page(e, entries, cfg, internal[n - 1] if n else None,
                               internal[n + 1] if n + 1 < len(internal) else None,
                               lang=code, targets=targets, alternates=alternates)
            dest = out_path_for(e.url, lbase, outdir)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(page)
            search.append({"t": e.title, "s": e.section, "u": e.url,
                           "b": plain_text(e.body, 1400)})

        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, "search.json"), "w", encoding="utf-8") as fh:
            json.dump(search, fh, ensure_ascii=False, separators=(",", ":"))
        total += len(internal)
        print("  %-3s %3d pages -> %s" % (code, len(internal), lbase + "/"))

    # ---- shared assets --------------------------------------------------
    adir = os.path.join(out, "assets")
    os.makedirs(adir, exist_ok=True)
    with open(os.path.join(adir, "docs.css"), "w", encoding="utf-8") as fh:
        fh.write(DOCS_CSS)
    with open(os.path.join(adir, "docs.js"), "w", encoding="utf-8") as fh:
        fh.write(DOCS_JS)

    copied = 0
    for _code, ldir, _lb in languages:
        gb = os.path.join(ldir, ".gitbook", "assets")
        if os.path.isdir(gb):
            for name in sorted(os.listdir(gb)):
                f = os.path.join(gb, name)
                if os.path.isfile(f) and not name.startswith("."):
                    shutil.copy2(f, os.path.join(adir, name))
                    copied += 1

    # ---- llms.txt / llms-full.txt --------------------------------------
    # The convention answer engines (ChatGPT, Perplexity, Claude, AI Overviews)
    # look for: a curated map, and the full text as one fetchable file.
    en_entries = [e for e in trees.get("en", (None, []))[1] if not e.external]
    if en_entries:
        site = cfg_base["site_url"]
        lines = ["# VOOL Documentation", "",
                 "> Documentation for VOOL, a desktop AI assistant that runs AI models "
                 "locally on your computer or connects to cloud models with your own key.",
                 ""]
        section = None
        for e in en_entries:
            if e.section != section:
                section = e.section
                lines += ["", "## %s" % (section or "Overview"), ""]
            summary = e.description or plain_text(e.body, 150)
            lines.append("- [%s](%s%s): %s" % (e.title, site, e.url, summary))
        with open(os.path.join(out, "llms.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines).strip() + "\n")

        full = ["# VOOL Documentation — full text", "",
                "Every VOOL documentation page, concatenated. Source: %s%s/" % (site, base),
                ""]
        for e in en_entries:
            full += ["", "---", "", "# %s" % e.title,
                     "URL: %s%s" % (site, e.url), ""]
            if e.description:
                full += [e.description, ""]
            full.append(plain_text(e.body))
        with open(os.path.join(out, "llms-full.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(full).strip() + "\n")

    # ---- sitemap, every language, with hreflang alternates --------------
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = []
    for code, (_lb, entries) in trees.items():
        for n, e in enumerate(x for x in entries if not x.external):
            links = "".join(
                '<xhtml:link rel="alternate" hreflang="%s" href="%s%s"/>'
                % (lc, cfg_base["site_url"], html.escape(have[lc][e.src]))
                for lc, _n in LANGS if lc in have and e.src in have[lc])
            urls.append("<url><loc>%s%s</loc>%s<lastmod>%s</lastmod>"
                        "<changefreq>weekly</changefreq><priority>%s</priority></url>"
                        % (cfg_base["site_url"], html.escape(e.url), links, now,
                           "0.9" if n == 0 and code == "en" else "0.7"))
    with open(os.path.join(out, "sitemap.xml"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                 'xmlns:xhtml="http://www.w3.org/1999/xhtml">%s</urlset>\n'
                 % "".join(urls))

    # ---- redirects declared in .gitbook.yaml ----------------------------
    for old, new in cfg_yaml["redirects"].items():
        target = url_for(new, base)
        dest = out_path_for(url_for(old + ".md", base), base, out)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write('<!doctype html><meta charset="utf-8">'
                     '<meta name="robots" content="noindex">'
                     '<link rel="canonical" href="%s">'
                     '<meta http-equiv="refresh" content="0; url=%s">'
                     '<title>Moved</title><a href="%s">This page moved.</a>\n'
                     % (cfg_base["site_url"] + target, target, target))

    with open(os.path.join(out, "build.json"), "w", encoding="utf-8") as fh:
        json.dump({"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "languages": sorted(trees), "pages": total, "assets": copied,
                   "missing": {k: v for k, v in missing_all.items() if v}},
                  fh, indent=2)

    print("built %d pages in %d language(s), %d assets -> %s"
          % (total, len(trees), copied, out))
    for code, miss in missing_all.items():
        for mfile in miss:
            print("   %s: listed in SUMMARY.md but no file yet: %s" % (code, mfile))
    return 0


def resolve_link(href, from_src, base, asset=False, asset_base=None):
    """Rewrite a repo-relative markdown/asset link into a site URL.

    `base` is the current language's URL prefix; `asset_base` is the docs root,
    because every language shares one asset directory."""
    if re.match(r"^(https?:|mailto:|tel:|#|//)", href):
        return href
    frag = ""
    if "#" in href:
        href, _, frag = href.partition("#")
        frag = "#" + frag
    if not href:
        return frag
    joined = os.path.normpath(os.path.join(os.path.dirname(from_src), href))
    joined = joined.replace(os.sep, "/").lstrip("/")
    if joined.startswith(".gitbook/assets/") or asset:
        return "%s/assets/%s%s" % (asset_base if asset_base is not None else base,
                                   os.path.basename(joined), frag)
    if joined.lower().endswith(".md"):
        return url_for(joined, base) + frag
    return "%s/%s%s" % (base, joined, frag)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Build the VOOL docs site.")
    ap.add_argument("--src", default=os.path.join(here, "..", "docs-source"),
                    help="repo root containing .gitbook.yaml")
    ap.add_argument("--out", default=os.path.join(here, "..", "docs-build"),
                    help="output directory (erased and rewritten)")
    ap.add_argument("--base", default="/docs",
                    help="URL path the docs are served under ('' for a subdomain root)")
    ap.add_argument("--site-url", default="https://vool.dev")
    ap.add_argument("--edit-base", default="",
                    help="e.g. https://github.com/Parad0x-Labs/vool/edit/main/docs/")
    a = ap.parse_args()
    return build(os.path.abspath(a.src), os.path.abspath(a.out),
                 a.base.rstrip("/"), a.site_url, a.edit_base)


if __name__ == "__main__":
    sys.exit(main())
