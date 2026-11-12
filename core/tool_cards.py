"""A tool's card: when a person needs it, said once, next to what it is.

A contract (`core.runtime_tool_contracts.RuntimeToolContract`) already says what a tool DOES, what
arguments it takes and how risky it is. What it never said is WHEN a person needs it: that lived as
hand-written regex in `core.tool_demand_signals` and `core.execution_requirements`, one rule per
tool family, so every new tool needed router edits and a plugin tool was offered only if the person
typed its intent token (``pack.tool``) -- in practice never.

A card closes that gap with data instead of code:

``use_when``  one plain sentence: the situation in which a person needs this tool
``examples``  short asks a person would actually type, any language
``not_for``   near misses that must NOT pick the tool (advice, how-to, talking about the topic)
``surfaces``  optional: the surfaces it is offered on; it can only NARROW, never widen

Builtin cards are JSON under ``core/tool_card_data/`` (one file per family, keyed by intent). A plugin
declares its card as one more object in its manifest ``tools[]`` entry (`core.plugin_tools`).

**A card grants nothing.** It carries no side-effect class, approval, permission action, credential,
network host or money field, and nothing that decides authority reads it: the permission
controller, wallet, caps, approvals and receipts read the contract, exactly as before. The most a
card can do is put a tool in front of the model, where every existing gate still applies.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where the builtin cards live: ``<family>.json`` -> ``{"<intent>": {card}}``.
BUILTIN_CARD_DIR = Path(__file__).with_name("tool_card_data")

SURFACES = frozenset({"pc", "phone", "chat_app"})

MIN_EXAMPLES = 3
MAX_EXAMPLES = 24
MAX_NOT_FOR = 16
MAX_ASK_CHARS = 200
MAX_USE_WHEN_CHARS = 300

_CARD_KEYS = frozenset({"use_when", "examples", "not_for", "surfaces"})


class ToolCardError(ValueError):
    """A card is malformed. Raised when it is read, never at turn time."""


@dataclass(frozen=True)
class ToolCard:
    use_when: str
    examples: tuple[str, ...]
    not_for: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()

    def offered_on(self, surface: str) -> bool:
        """Whether the card lets this surface see the tool. Empty means every surface."""

        return not self.surfaces or str(surface or "").strip().lower() in self.surfaces

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"use_when": self.use_when, "examples": list(self.examples)}
        if self.not_for:
            out["not_for"] = list(self.not_for)
        if self.surfaces:
            out["surfaces"] = list(self.surfaces)
        return out


def _asks(raw: Any, *, field: str, owner: str, limit: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ToolCardError(f"{owner}: card.{field} must be a list of short asks")
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split())
        if not text:
            raise ToolCardError(f"{owner}: card.{field} has an empty ask")
        if len(text) > MAX_ASK_CHARS:
            raise ToolCardError(f"{owner}: card.{field} ask is longer than {MAX_ASK_CHARS} characters: {text[:40]!r}...")
        if text.lower() not in {o.lower() for o in out}:
            out.append(text)
    if len(out) > limit:
        raise ToolCardError(f"{owner}: card.{field} has {len(out)} asks; at most {limit}")
    return tuple(out)


def parse_card(raw: Any, *, owner: str) -> ToolCard:
    """Validate a card object. ``owner`` names the tool in every error."""

    if not isinstance(raw, dict):
        raise ToolCardError(f"{owner}: `card` must be an object")
    unknown = sorted(set(raw) - _CARD_KEYS)
    if unknown:
        # Loud on purpose: a card that tries to carry trust ("approval", "permission_actions",
        # "network") must fail, not be silently ignored, so nobody believes it took effect.
        raise ToolCardError(
            f"{owner}: card has unknown field(s) {unknown}; a card only says when to use a tool "
            f"({sorted(_CARD_KEYS)}). Risk and approval stay in side_effect_class / approval_requirement."
        )
    use_when = " ".join(str(raw.get("use_when") or "").split())
    if len(use_when) < 12:
        raise ToolCardError(f"{owner}: card.use_when must be a real sentence")
    if len(use_when) > MAX_USE_WHEN_CHARS:
        raise ToolCardError(f"{owner}: card.use_when is longer than {MAX_USE_WHEN_CHARS} characters")
    examples = _asks(raw.get("examples"), field="examples", owner=owner, limit=MAX_EXAMPLES)
    if len(examples) < MIN_EXAMPLES:
        raise ToolCardError(f"{owner}: card.examples needs at least {MIN_EXAMPLES} asks a person would type")
    not_for = _asks(raw.get("not_for"), field="not_for", owner=owner, limit=MAX_NOT_FOR)
    clash = {e.lower() for e in examples} & {n.lower() for n in not_for}
    if clash:
        raise ToolCardError(f"{owner}: the same ask is both an example and not_for: {sorted(clash)[0]!r}")
    surfaces_raw = raw.get("surfaces")
    surfaces: tuple[str, ...] = ()
    if surfaces_raw is not None:
        if not isinstance(surfaces_raw, (list, tuple)) or not surfaces_raw:
            raise ToolCardError(f"{owner}: card.surfaces must be a non-empty list from {sorted(SURFACES)}")
        surfaces = tuple(dict.fromkeys(str(s).strip().lower() for s in surfaces_raw))
        bad = [s for s in surfaces if s not in SURFACES]
        if bad:
            raise ToolCardError(f"{owner}: card.surfaces has unknown surface(s) {bad}; use {sorted(SURFACES)}")
    return ToolCard(use_when=use_when, examples=examples, not_for=not_for, surfaces=surfaces)


_lock = threading.Lock()
_builtin: dict[str, ToolCard] | None = None


def _load_builtin_cards(directory: Path = BUILTIN_CARD_DIR) -> dict[str, ToolCard]:
    cards: dict[str, ToolCard] = {}
    if not directory.is_dir():
        return cards
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolCardError(f"{path.name}: not readable JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise ToolCardError(f"{path.name}: must map intent -> card")
        for intent, raw in data.items():
            if intent.startswith("_"):
                continue  # "_about" and similar notes for authors
            if intent in cards:
                raise ToolCardError(f"{path.name}: {intent} already has a card in another file")
            cards[intent] = parse_card(raw, owner=f"{path.name}:{intent}")
    return cards


def builtin_cards() -> dict[str, ToolCard]:
    """Every builtin card, by intent. Read once per process; `reset()` re-reads."""

    global _builtin
    with _lock:
        if _builtin is None:
            _builtin = _load_builtin_cards()
        return dict(_builtin)


def reset() -> None:
    global _builtin
    with _lock:
        _builtin = None


def card_for(contract: Any) -> ToolCard | None:
    """The card a registered contract carries (plugins, MCP), else the builtin card for its intent."""

    declared = getattr(contract, "card", None)
    if isinstance(declared, ToolCard):
        return declared
    if str(getattr(contract, "source", "builtin") or "builtin") != "builtin":
        # A plugin tool's card comes from its own manifest only: a builtin card file can never
        # describe (and so route words to) a tool someone else shipped under the same intent.
        return None
    return builtin_cards().get(str(getattr(contract, "intent", "") or ""))


__all__ = [
    "BUILTIN_CARD_DIR",
    "SURFACES",
    "ToolCard",
    "ToolCardError",
    "builtin_cards",
    "card_for",
    "parse_card",
    "reset",
]
