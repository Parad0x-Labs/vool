"""The readable text of a web page: its title and the words a person sees, in reading order.

`web.fetch` used to hand back the page's raw bytes. For an HTML page that is markup, scripts and
styles, and the chat printed it as the answer: measured on 1cbf433 (fresh-user test, 2026-10-29),
"can you summarise this page for me https://www.gov.uk/browse/driving" answered with 20,000
characters of HTML. A person asked what the page says; this is what the page says.

`tools.web.http_fetch.strip_html` keeps the visible text too, but joins it into one line. Here a
block element (paragraph, heading, list item, table row) ends a line, so a page keeps the shape a
reader sees and a model can tell a heading from the paragraph under it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

# Never shown to a reader: code, styling, embedded documents and form option lists.
_HIDDEN = frozenset({"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "select"})
# Each of these starts and ends a block of text on the rendered page.
_BLOCK = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "details", "dialog", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "td", "th", "tr", "ul",
})
_HEADING = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

_HTML_SNIFF_RE = re.compile(r"^\s*(?:<!--.*?-->\s*)*<(?:!doctype\s+html|html|head|body)\b", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class PageText:
    title: str
    text: str


class _ReadableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._in_title = False
        self._title: list[str] = []
        self._lines: list[str] = []
        self._line: list[str] = []
        self._heading = False

    def _break(self) -> None:
        line = " ".join(" ".join(self._line).split())
        self._line = []
        if not line:
            return
        if self._heading:
            line = f"## {line}"
        self._lines.append(line)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and not self._hidden_depth:
            self._in_title = True
            return
        if tag in _HIDDEN:
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return
        if tag in _BLOCK:
            self._break()
            self._heading = tag in _HEADING
        if tag == "li":
            self._line.append("-")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._hidden_depth and tag in _BLOCK:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in _HIDDEN:
            if self._hidden_depth:
                self._hidden_depth -= 1
            return
        if self._hidden_depth:
            return
        if tag in _BLOCK:
            self._break()
            if tag in _HEADING:
                self._heading = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
            return
        if self._hidden_depth:
            return
        if data and data.strip():
            self._line.append(data.strip())

    def result(self) -> PageText:
        self._break()
        lines: list[str] = []
        for line in self._lines:
            # A bare list marker is the bullet of an item whose words sat in a nested block.
            if line == "-" or (lines and line == lines[-1]):
                continue
            lines.append(line)
        return PageText(title=" ".join(" ".join(self._title).split()), text="\n".join(lines))


def looks_like_html(content_type: str, body: str) -> bool:
    """Whether a fetched body is an HTML page: by its declared type, or by its first tag when the
    server declared none (or declared text/plain for a page)."""
    kind = str(content_type or "").split(";", 1)[0].strip().lower()
    if kind in {"text/html", "application/xhtml+xml"}:
        return True
    if kind and kind not in {"text/plain", "application/octet-stream"}:
        return False
    return bool(_HTML_SNIFF_RE.match(str(body or "")[:2048]))


def readable_page(raw_html: str) -> PageText:
    """The page's title and readable text. Markup the parser cannot make sense of yields what it
    could read, never the markup itself."""
    parser = _ReadableParser()
    try:
        parser.feed(str(raw_html or ""))
        parser.close()
    except Exception:  # noqa: BLE001 - html.parser is lenient; a failure must not resurface markup
        pass
    return parser.result()


__all__ = ["PageText", "looks_like_html", "readable_page"]
