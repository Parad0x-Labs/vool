"""RenderIntent: surface + density + emoji mode. Presentation only."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Density = Literal["COMPACT", "NORMAL", "DETAILED"]
EmojiMode = Literal["MINIMAL", "STANDARD", "EXPRESSIVE"]


@dataclass(frozen=True)
class RenderIntent:
    surface: Literal["terminal", "markdown", "plain_text", "chat"] = "markdown"
    width: int = 80
    density: Density = "NORMAL"
    emoji: EmojiMode = "STANDARD"

    def narrow(self) -> bool:
        return self.width < 40


DEFAULT_INTENT = RenderIntent()
