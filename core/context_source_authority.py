"""Typed scope and relevance decisions for non-transcript provider context."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum


class ContextSource(str, Enum):
    """Non-transcript source classes that need independent authority."""

    PERSISTENT_RETRIEVAL = "persistent_retrieval"
    SESSION_SUMMARY = "session_summary"
    USER_HEURISTIC = "user_heuristic"
    SHARED_CONTEXT = "shared_context"
    COLD_CONTEXT = "cold_context"
    TOOL_STATE = "tool_state"
    MISSION_STATE = "mission_state"
    SHORTHAND = "shorthand"


@dataclass(frozen=True)
class ContextSourceAuthority:
    """Separate import permission from turn-specific relevance.

    A source is admitted only when the trusted scope grants permission to consider it and an
    independent, source-specific decision marks it relevant. Transcript continuation is not an
    input to this type.
    """

    scoped_sources: frozenset[ContextSource]
    relevant_sources: frozenset[ContextSource]

    @classmethod
    def from_sources(
        cls,
        *,
        scoped: Iterable[ContextSource] = (),
        relevant: Iterable[ContextSource] = (),
    ) -> ContextSourceAuthority:
        scoped_sources = frozenset(scoped)
        return cls(
            scoped_sources=scoped_sources,
            relevant_sources=frozenset(relevant) & scoped_sources,
        )

    def admits(self, source: ContextSource) -> bool:
        return source in self.scoped_sources and source in self.relevant_sources

    def state(self, source: ContextSource) -> str:
        if source not in self.scoped_sources:
            return "scope_denied"
        if source not in self.relevant_sources:
            return "relevance_denied"
        return "admitted"

    def with_relevance(
        self,
        source: ContextSource,
        *,
        relevant: bool,
    ) -> ContextSourceAuthority:
        selected = set(self.relevant_sources)
        if relevant and source in self.scoped_sources:
            selected.add(source)
        else:
            selected.discard(source)
        return replace(self, relevant_sources=frozenset(selected))

    def telemetry(self) -> dict[str, str]:
        return {source.value: self.state(source) for source in ContextSource}


__all__ = ["ContextSource", "ContextSourceAuthority"]
