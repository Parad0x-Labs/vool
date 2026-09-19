# VENDORED from the live vool.dev website toolchain (~/Desktop/hhfdsfdfsfdsdsfsdf/website/tools/check_docs_build.py,
# verified live 2026-09-19). The website copy stays authoritative for vool.dev deploys; this copy
# exists so the repository's own CI can build and gate /docs with zero package installs. Keep the
# two in sync deliberately: diff against the website copy when touching this file.
#!/usr/bin/env python3
"""
check_docs_build.py — validate a built docs site before it is published.

Run:  python3 tools/check_docs_build.py docs-build
Exit code 0 = safe to publish. sync-docs.sh runs this as a publish gate, so a
broken build never replaces a working one on the server.

Checks: page scaffolding, valid JSON-LD, no unconverted markdown leaking into
visible text, every internal link resolving to a real file, and a sane search
index.
"""

import glob
import json
import os
import re
import sys

FEATURES = [
    ("<table>", "tables"),
    ('class="hint hint-', "hints"),
    ('class="code-block"', "code blocks"),
    ("<blockquote>", "blockquotes"),
    ("<ul>", "lists"),
    ("<ol>", "ordered lists"),
    ("<strong>", "bold"),
    ("<code>", "inline code"),
    ('class="anchor"', "heading anchors"),
]

LEAKS = [
    (r"\]\(", "unconverted link"),
    (r"\*\*", "unconverted bold"),
    (r"^\s*\|", "unconverted table"),
    (r"\{%", "leaked liquid tag"),
    (r"^#{1,6}\s", "unconverted heading"),
]


def main(root, base="/docs"):
    if not os.path.isdir(root):
        print("no such directory: %s" % root)
        return 2
    os.chdir(root)

    files = sorted(glob.glob("**/index.html", recursive=True))
    if not files:
        print("no pages found in %s" % root)
        return 2
    def _read(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    pages = {f: _read(f) for f in files}
    fails = []

    for f, s in pages.items():
        if "<title>" not in s:
            fails.append("%s: no <title>" % f)
        if 'rel="canonical"' not in s:
            fails.append("%s: no canonical" % f)
        if s.count("<h1") != 1:
            fails.append("%s: %d <h1> (want exactly 1)" % (f, s.count("<h1")))
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', s, re.S)
        if not m:
            fails.append("%s: no JSON-LD" % f)
        else:
            try:
                json.loads(m.group(1))
            except Exception as e:
                fails.append("%s: invalid JSON-LD (%s)" % (f, e))

        art = re.search(r'<article class="prose">(.*?)</article>', s, re.S)
        if not art:
            fails.append("%s: no article body" % f)
            continue
        text = re.sub(r"<(pre|code)[^>]*>.*?</\1>", " ", art.group(1), flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        for pat, name in LEAKS:
            if re.search(pat, text, re.M):
                fails.append("%s: %s in visible text" % (f, name))

    joined = "".join(pages.values())
    for frag, name in FEATURES:
        if frag not in joined:
            fails.append("feature never rendered anywhere: %s" % name)

    for f, s in pages.items():
        for href in re.findall(r'href="(%s[^"#]*)"' % re.escape(base or "/"), s):
            if href.endswith(("search.json", "sitemap.xml")):
                continue
            rel = href[len(base):].strip("/") if base else href.strip("/")
            target = os.path.join(rel, "index.html") if rel else "index.html"
            if not os.path.isfile(target) and not os.path.isfile(rel):
                fails.append("broken internal link: %s -> %s" % (f, href))

    for f, s in pages.items():
        for href in re.findall(r'<link rel="alternate" hreflang="[a-z-]+" href="[^"]*?(%s[^"]*)"'
                               % re.escape(base or "/"), s):
            rel = href[len(base):].strip("/") if base else href.strip("/")
            if not os.path.isfile(os.path.join(rel, "index.html") if rel else "index.html"):
                fails.append("hreflang points at a missing page: %s -> %s" % (f, href))

    stale = {h for s in pages.values() for h in re.findall(r'href="([^"]*\.md[^"]*)"', s)}
    for h in sorted(stale):
        fails.append("href still points at markdown: %s" % h)

    # One search index per language: the root one covers English, and each
    # <lang>/search.json covers that language's own pages.
    indexes = sorted(glob.glob("search.json") + glob.glob("*/search.json"))
    if not indexes:
        fails.append("no search.json found")
    counted = 0
    for ix in indexes:
        prefix = os.path.dirname(ix)
        try:
            with open(ix, encoding="utf-8") as fh:
    idx = json.load(fh)
        except Exception as e:
            fails.append("%s: unreadable (%s)" % (ix, e))
            continue
        # Pages belonging to this index: everything under its directory that is
        # not under another language directory.
        others = [os.path.dirname(o) for o in indexes if o != ix and os.path.dirname(o)]
        own = [f for f in files
               if (f.startswith(prefix + "/") if prefix else
                   not any(f.startswith(o + "/") for o in others))]
        counted += len(own)
        if len(idx) != len(own):
            fails.append("%s has %d entries, its language has %d pages"
                         % (ix, len(idx), len(own)))
        if any(not (e.get("t") and e.get("u")) for e in idx):
            fails.append("%s: entry missing title or url" % ix)
        if "<" in "".join(e.get("b", "") for e in idx):
            fails.append("%s: contains raw HTML" % ix)
    if counted != len(files):
        fails.append("search indexes cover %d pages, site has %d" % (counted, len(files)))

    if not os.path.isfile("sitemap.xml"):
        fails.append("sitemap.xml missing")

    print("checked %d pages in %s" % (len(files), root))
    if fails:
        print("\nFAILED (%d):" % len(fails))
        for x in fails:
            print("  x %s" % x)
        return 1
    print("build is publishable")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs-build",
                  sys.argv[2] if len(sys.argv) > 2 else "/docs"))
