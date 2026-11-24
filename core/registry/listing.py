"""The author's part of a listing: a long description, three links and a compatibility range.

These live in the package's own ``registry.json`` under ``listing``, so the package checksum covers
them and the signed row of an add-on gains no field (a VOOL build that meets a row field it does
not know refuses the whole signed list).

The description is written in a small part of Markdown: paragraphs, headings ``#`` to ``###``,
bullet and numbered lists, ``**bold**``, ``*italic*``, `` `code` `` and fenced code blocks. It is
read into a small tree here, and a page builds its nodes from that tree as text. Nothing an
author writes is ever handed to a page as markup: what this reader does not know stays the
plain characters the author typed.
"""
from __future__ import annotations

import re
import urllib.parse

from core.registry.codec import RegistryError, decode

FORMAT = "markdown-subset-1"
FILE = "registry.json"
MIN_DESCRIPTION, MAX_DESCRIPTION = 80, 6000
MAX_LINK = 200
LINKS = ("homepage", "support", "docs")
PLATFORMS = ("macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64")
FIELDS = ("description", "links", "compatibility")
# What a card shows as its one line, once a package carries a listing block.
MIN_TAGLINE, MAX_TAGLINE = 20, 140

_VOOL_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
_FENCE = re.compile(r" {0,3}```")
_HEADING = re.compile(r"(#{1,3})[ \t]+(\S.*)")
_BULLET = re.compile(r" {0,3}[-*][ \t]+(\S.*)")
_NUMBERED = re.compile(r" {0,3}[0-9]{1,3}[.)][ \t]+(\S.*)")
# Code first, so stars inside it stay stars; then bold before italic.
_INLINE = re.compile(r"`([^`\n]+)`|\*\*(?=\S)(.+?)(?<=\S)\*\*|\*(?=\S)([^*\n]+?)(?<=\S)\*")
_IMAGE = re.compile(r"!\[[^\]\n]*\]\(")
_TABLE_RULE = re.compile(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*")
_HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


class ListingProblem(RegistryError):
    """One thing to change in the listing block: ``where`` is the field, ``fix`` what to write instead."""

    where = ""


def _problem(where: str, message: str, fix: str) -> ListingProblem:
    error = ListingProblem(message)
    error.where, error.fix = where, fix
    return error


# ---- the description ----

def _spans(text: str) -> list[dict]:
    spans, at = [], 0
    for match in _INLINE.finditer(text):
        if match.start() > at:
            spans.append({"type": "text", "text": text[at:match.start()]})
        code, bold, italic = match.groups()
        spans.append({"type": "code", "text": code} if code is not None else
                     {"type": "bold", "text": bold} if bold is not None else {"type": "italic", "text": italic})
        at = match.end()
    if at < len(text):
        spans.append({"type": "text", "text": text[at:]})
    return spans


def parse(text: str) -> dict:
    """The description as a tree. Never refuses: anything outside the small Markdown part is plain text.

    ``{"format", "blocks"}``; a block is a heading (level 1 to 3), a paragraph, a list or a code block; the
    words inside are spans of plain text, bold, italic or code. Every leaf is a string for a text node.
    """
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, paragraph, index = [], [], 0

    def flush():
        if paragraph:
            blocks.append({"type": "paragraph", "spans": _spans(" ".join(paragraph))})
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if _FENCE.match(line):
            flush()
            end = next((at for at in range(index + 1, len(lines)) if _FENCE.fullmatch(lines[at].rstrip())), len(lines))
            blocks.append({"type": "code", "text": "\n".join(lines[index + 1:end])})
            index = end + 1
            continue
        heading = _HEADING.fullmatch(line.rstrip())
        if heading:
            flush()
            blocks.append({"type": "heading", "level": len(heading.group(1)), "spans": _spans(heading.group(2).strip())})
        elif _BULLET.fullmatch(line.rstrip()) or _NUMBERED.fullmatch(line.rstrip()):
            flush()
            ordered = not _BULLET.fullmatch(line.rstrip())
            pattern, items = (_NUMBERED if ordered else _BULLET), []
            while index < len(lines):
                item = pattern.fullmatch(lines[index].rstrip())
                if item:
                    items.append(item.group(1).strip())
                elif items and lines[index].startswith("  ") and lines[index].strip() and not _FENCE.match(lines[index]):
                    items[-1] += " " + lines[index].strip()  # a wrapped line of the item above
                else:
                    break
                index += 1
            blocks.append({"type": "list", "ordered": ordered, "items": [_spans(item) for item in items]})
            continue
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
        index += 1
    flush()
    return {"format": FORMAT, "blocks": blocks}


def plain_lines(tree: dict) -> list[str]:
    """The words a reader sees, one line per paragraph, heading, list item and code line, markers gone.

    The text rules read these lines, so a claim cannot hide behind stars or backticks put inside a word.
    """
    lines = []
    for block in tree["blocks"]:
        if block["type"] == "code":
            lines += block["text"].split("\n")
        elif block["type"] == "list":
            lines += ["".join(span["text"] for span in item) for item in block["items"]]
        else:
            lines.append("".join(span["text"] for span in block["spans"]))
    return lines


def _outside_code(tree: dict) -> list[str]:
    """Every run of words that is not code, as the author typed it."""
    runs = []
    for block in tree["blocks"]:
        if block["type"] == "code":
            continue
        for spans in (block["items"] if block["type"] == "list" else [block["spans"]]):
            runs.append("".join("\x00" if span["type"] == "code" else span["text"] for span in spans))
    return runs


def description(value: object) -> dict:
    """Check the long description and return its tree, or raise the one thing to change."""
    where = "listing.description"
    if not isinstance(value, str):
        raise _problem(where, "the description is not text", "Write description as one text value: \"description\": \"...\".")
    size = len(value.strip())
    if size < MIN_DESCRIPTION or len(value) > MAX_DESCRIPTION:
        raise _problem(where, "the description is too short or too long",
                       f"Write {MIN_DESCRIPTION} to {MAX_DESCRIPTION} characters that say what the add-on does and how to "
                       f"use it. This one has {size}.")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value) or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _problem(where, "the description holds control characters", "Retype the description as plain text.")
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if sum(1 for line in lines if _FENCE.match(line)) % 2:
        raise _problem(where, "a code block in the description is not closed",
                       "End every code block with a line that holds only ```.")
    tree = parse(value)
    for run in _outside_code(tree):
        if "<" in run or ">" in run:
            raise _problem(where, "the description holds < or > outside code",
                           "HTML is not shown. Take the tag out, or put the characters in `code` if you mean them "
                           "as text. Use # for a heading and - for a list.")
        if _IMAGE.search(run):
            raise _problem(where, "the description holds a picture",
                           "Pictures are not shown in a description. Take the ![...](...) out; the add-on's own "
                           "pictures are its icon and banner.")
    fenced = False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _TABLE_RULE.fullmatch(line):
            raise _problem(where, "the description holds a table",
                           "Tables are not shown. Write the rows as a list, one line each starting with - .")
    return tree


# ---- links and compatibility ----

def link(name: str, value: object) -> str:
    where = "listing.links." + name
    fix = ("Give one full https address of at most " + str(MAX_LINK) + " characters, such as https://example.org/help: "
           "no user name or password in it, no spaces.")
    if not isinstance(value, str) or not 0 < len(value) <= MAX_LINK or value != value.strip() or \
            any(ord(char) <= 32 or char in "<>\"'`\\^{}|" or ord(char) == 127 for char in value):
        raise _problem(where, "this link is not a plain https address", fix)
    try:
        parsed = urllib.parse.urlsplit(value)
        host, port = parsed.hostname or "", parsed.port
    except ValueError:
        raise _problem(where, "this link is not a plain https address", fix) from None
    if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or "@" in parsed.netloc or \
            port not in (None, 443) or not _HOST.fullmatch(host) or not value.startswith("https://" + host):
        raise _problem(where, "this link is not a plain https address", fix)
    return value


def _vool_version(where: str, value: object) -> tuple:
    if not isinstance(value, str) or not _VOOL_VERSION.fullmatch(value):
        raise _problem(where, "this is not a VOOL version", "Write a released VOOL version as three numbers, such as 0.8.0.")
    return tuple(int(part) for part in value.split("."))


def compatibility(value: object) -> dict:
    where = "listing.compatibility"
    if not isinstance(value, dict) or not value or set(value) - {"min_vool", "max_vool", "platforms"}:
        raise _problem(where, "compatibility holds a field that is not known",
                       "compatibility may hold min_vool, max_vool and platforms, and nothing else.")
    oldest = _vool_version(where + ".min_vool", value["min_vool"]) if "min_vool" in value else None
    newest = _vool_version(where + ".max_vool", value["max_vool"]) if "max_vool" in value else None
    if oldest is not None and newest is not None and newest < oldest:
        raise _problem(where + ".max_vool", "max_vool is older than min_vool",
                       "Make max_vool the same as min_vool or newer, or leave max_vool out.")
    if "platforms" in value:
        platforms = value["platforms"]
        if not isinstance(platforms, list) or not platforms or len(set(map(str, platforms))) != len(platforms) or \
                any(item not in PLATFORMS for item in platforms):
            raise _problem(where + ".platforms", "platforms holds a name that is not known",
                           "List each platform once, from: " + ", ".join(PLATFORMS) + ".")
    return {key: value[key] for key in ("min_vool", "max_vool", "platforms") if key in value}


# ---- the whole block ----

def problems(value: object) -> list[ListingProblem]:
    """Everything to change in one listing block, each with its field and its fix. Empty when it is fine."""
    if not isinstance(value, dict):
        return [_problem("listing", "the listing block is not an object",
                         "Write \"listing\": { ... } with description, and links and compatibility if you want them.")]
    found = []
    extra = sorted(str(key)[:40] for key in set(value) - set(FIELDS))
    if extra:
        found.append(_problem("listing." + extra[0], "the listing block holds a field that is not known",
                              "Take out: " + ", ".join(extra[:12]) + ". A listing block holds description, links and "
                              "compatibility."))
    if "description" not in value:
        found.append(_problem("listing.description", "the listing block has no description",
                              f"Add \"description\": {MIN_DESCRIPTION} to {MAX_DESCRIPTION} characters that say what the "
                              "add-on does and how to use it."))
    checks = [("description", lambda: description(value["description"])),
              ("compatibility", lambda: compatibility(value["compatibility"]))]
    links = value.get("links")
    if "links" in value and (not isinstance(links, dict) or not links or set(links) - set(LINKS)):
        found.append(_problem("listing.links", "links holds a field that is not known",
                              "links may hold homepage, support and docs, each one https address, and nothing else."))
    elif isinstance(links, dict):
        checks += [("links", lambda name=name: link(name, links[name])) for name in LINKS if name in links]
    for field, check in checks:
        if field in value:
            try:
                check()
            except ListingProblem as error:
                found.append(error)
    return found


def validate(value: object) -> dict:
    """The block as it is kept: ``{"description": {"format", "text", "tree"}, "links"?, "compatibility"?}``.

    Raises the first problem. ``problems`` lists them all.
    """
    found = problems(value)
    if found:
        raise found[0]
    result = {"description": {"format": FORMAT, "text": value["description"], "tree": parse(value["description"])}}
    if "links" in value:
        result["links"] = {name: value["links"][name] for name in LINKS if name in value["links"]}
    if "compatibility" in value:
        result["compatibility"] = compatibility(value["compatibility"])
    return result


def block(files: dict) -> tuple[bool, object]:
    """(present, value) of the listing block in a package's own registry.json. A package without one has none."""
    raw = files.get(FILE)
    if raw is None:
        return False, None
    try:
        value = decode(raw, 1024 * 1024)
    except RegistryError:
        return False, None
    if not isinstance(value, dict) or "listing" not in value:
        return False, None
    return True, value["listing"]


def tagline_problem(summary: object) -> ListingProblem | None:
    """A package with a listing block shows its summary as the card's one line, so the line has to fit a card."""
    text = summary if isinstance(summary, str) else ""
    if MIN_TAGLINE <= len(text.strip()) <= MAX_TAGLINE and "<" not in text and ">" not in text:
        return None
    return _problem("summary", "the summary does not fit a card",
                    f"With a listing block, summary is the card's one line: {MIN_TAGLINE} to {MAX_TAGLINE} characters, "
                    "no < or >. Put the longer text in listing.description.")


def kept(files: dict) -> dict | None:
    """The checked block of a published package, or None: no block, or one that does not pass, shows nothing."""
    present, value = block(files)
    if not present:
        return None
    try:
        return validate(value)
    except RegistryError:
        return None
