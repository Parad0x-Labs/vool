"""The one reader for a SKILL.md header (the ``---`` block at the top).

Every path that reads a skill's header -- the loader, a zip import, a GitHub or Browse inspect,
the validator, the identity digests -- reads it here, so one bound holds everywhere.

A plain ``yaml.safe_load`` is not safe enough for a file someone else wrote. YAML anchors and
aliases let a 300-byte header name the same list a billion times; nothing is allocated while the
header is parsed, but the first ``str()`` or ``json.dumps`` of a value expands it and the app runs
out of memory (red team t10/t11, 2026-11-10: a 262-byte zip froze the app for 42 s, then
``MemoryError``). So a header is refused, never trimmed, when it:

* uses an anchor (``&name``) or an alias (``*name``) -- the merge key ``<<`` needs one too;
* is longer than ``MAX_FRONTMATTER_BYTES``;
* nests deeper than ``MAX_DEPTH`` or holds more than ``MAX_NODES`` values;
* is not a mapping at the top.

The limits sit far above every shipped skill (the largest native header is 3.5 KB, three
levels deep) and far below anything that can hurt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

MAX_FRONTMATTER_BYTES = 16 * 1024
MAX_DEPTH = 6
MAX_NODES = 1000

#: Said to the person when a header is refused. Plain words; the detail goes in ``reason``.
_PLAIN = {
    "alias": "The skill's header uses YAML shortcuts (& or * markers) that can make it huge when read. "
             "Write each value out in full, without & or *, and try again.",
    "too_long": "The skill's header (the part between the --- lines at the top) is too long. "
                "Keep it to a name, a description and a few short settings.",
    "too_deep": "The skill's header has settings nested too deeply. Keep it to a name, a description "
                "and a few short settings.",
    "too_many": "The skill's header has too many settings. Keep it to a name, a description and a "
                "few short settings.",
    "not_mapping": "The skill's header should be a list of settings such as name: and description:.",
    "malformed": "The skill's header could not be read. Check the lines between the --- marks at the top.",
}


class FrontmatterRefused(ValueError):
    """A header VOOL will not read. ``code`` is stable; ``message`` is plain and safe to show."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _PLAIN[code]
        super().__init__(self.message)

    @property
    def unsafe(self) -> bool:
        """True when reading it could hurt the app (as opposed to a header that is merely broken)."""
        return self.code in {"alias", "too_long", "too_deep", "too_many"}


@dataclass(frozen=True)
class SplitSkill:
    header: str
    body: str


def split(text: str) -> SplitSkill | None:
    """The raw header and body of a SKILL.md text, or None when it has no ``---`` header."""
    match = FRONTMATTER_RE.match(str(text or ""))
    if match is None:
        return None
    return SplitSkill(header=match.group(1), body=match.group(2) or "")


_LOADER = None


def _bounded_loader():
    """A SafeLoader whose composer refuses anchors and aliases and bounds depth and node count.

    The pure-Python composer is used on purpose: it is the one whose ``compose_node`` can be hooked,
    and a header is at most ``MAX_FRONTMATTER_BYTES``, so speed does not matter here."""
    global _LOADER
    if _LOADER is None:
        import yaml
        from yaml.events import AliasEvent, CollectionStartEvent

        class _BoundedLoader(yaml.SafeLoader):
            def __init__(self, stream: str) -> None:
                super().__init__(stream)
                self._depth = 0
                self._nodes = 0

            def compose_node(self, parent, index):  # noqa: ANN001 - PyYAML signature
                if self.check_event(AliasEvent):
                    raise FrontmatterRefused("alias")
                event = self.peek_event()
                if getattr(event, "anchor", None):
                    raise FrontmatterRefused("alias")
                self._nodes += 1
                if self._nodes > MAX_NODES:
                    raise FrontmatterRefused("too_many")
                nested = isinstance(event, CollectionStartEvent)
                if nested:
                    self._depth += 1
                    if self._depth > MAX_DEPTH:
                        raise FrontmatterRefused("too_deep")
                try:
                    return super().compose_node(parent, index)
                finally:
                    if nested:
                        self._depth -= 1

        _LOADER = _BoundedLoader
    return _LOADER


def load_header(header: str) -> dict[str, Any]:
    """The header as a dict, or ``FrontmatterRefused``. Never expands an alias, never grows unbounded."""
    raw = str(header or "")
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_FRONTMATTER_BYTES:
        raise FrontmatterRefused("too_long")
    import yaml

    loader = _bounded_loader()(raw)
    try:
        loaded = loader.get_single_data()
    except FrontmatterRefused:
        raise
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise FrontmatterRefused("malformed") from exc
    finally:
        loader.dispose()
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise FrontmatterRefused("not_mapping")
    return loaded


def parse(text: str) -> tuple[dict[str, Any], str] | None:
    """``(header, body)`` of a SKILL.md, None without a header, ``FrontmatterRefused`` when unreadable."""
    parts = split(text)
    if parts is None:
        return None
    return load_header(parts.header), parts.body


def check(text: str) -> str:
    """The plain refusal for this SKILL.md's header, or "" when it is readable (or absent)."""
    try:
        parse(text)
    except FrontmatterRefused as refused:
        return refused.message
    return ""


__all__ = [
    "FRONTMATTER_RE",
    "FrontmatterRefused",
    "MAX_DEPTH",
    "MAX_FRONTMATTER_BYTES",
    "MAX_NODES",
    "check",
    "load_header",
    "parse",
    "split",
]
