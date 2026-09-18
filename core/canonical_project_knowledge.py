"""Allowlisted, provenance-bearing retrieval from canonical repository docs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from core.prompt_assembly_report import ContextItem
from core.runtime_paths import project_path

_ALLOWLIST = (
    "README.md",
    "docs/SYSTEM_SPINE.md",
    "docs/STATUS.md",
    "docs/RUNTIME_ARCHITECTURE_CONTRACT.md",
)
_ENTITY_RE = re.compile(
    r"\b(?:vool|nulla|parad0x|web[\s-]?0|openrouter|ollama|"
    r"dna[\s-]?x402|x402|dark[\s-]?null|liquefy|solana|"
    r"arweave|openclaw)\b|\.null\b",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(
    r"""(?:^|[\s('"`])(?:"""
    r"""(?:~|\.{1,2})?/[^\s'"`,;)]{2,}|"""
    r"""[A-Za-z]:[\\/][^\s'"`,;)]{2,}|"""
    r"""\\\\[^\\/\s]+[\\/][^\s'"`,;)]{2,}"""
    r""")"""
)
_TOKEN_RE = re.compile(
    r"[a-z0-9][a-z0-9_.+-]{1,63}",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "are",
        "about",
        "can",
        "could",
        "does",
        "define",
        "describe",
        "did",
        "do",
        "explain",
        "from",
        "have",
        "how",
        "is",
        "into",
        "me",
        "please",
        "tell",
        "that",
        "the",
        "their",
        "this",
        "what",
        "where",
        "who",
        "which",
        "with",
        "would",
    }
)
_ENTITY_TOKENS = frozenset(
    {
        "vool",
        "vool",
        "parad0x",
        "web0",
        "openrouter",
        "ollama",
        "x402",
        "darknull",
        "liquefy",
        "solana",
        "arweave",
        "openclaw",
        "null",
    }
)


@dataclass(frozen=True)
class CanonicalPassage:
    source_path: str
    heading: str
    content: str
    content_hash: str
    score: float


def _root() -> Path:
    return project_path().resolve()


def canonical_query_text(text: str) -> str:
    """Remove filesystem paths before matching canonical project terms."""
    return _PATH_TOKEN_RE.sub(" ", str(text or ""))


def has_canonical_project_entity(text: str) -> bool:
    """Return whether user text names an allowlisted project entity outside a path."""
    return bool(_ENTITY_RE.search(canonical_query_text(text)))


def _allowed_path(relative_path: str) -> Path:
    root = _root()
    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("canonical source escaped project root")
    return candidate


def _tokens(text: str) -> set[str]:
    normalized = str(text or "").lower()
    normalized = re.sub(r"\.null\b", " null ", normalized)
    normalized = re.sub(r"\bweb[\s-]?0\b", " web0 ", normalized)
    normalized = re.sub(r"\bdna[\s-]?x402\b", " x402 ", normalized)
    normalized = re.sub(r"\bdark[\s-]?null\b", " darknull ", normalized)
    normalized = re.sub(r"\b(?:nulla|vool)[\s-]?local\b", " vool ", normalized)
    return {
        token.lower()
        for token in _TOKEN_RE.findall(normalized)
        if token.lower() not in _STOPWORDS
    }


def _sections(path: Path) -> list[tuple[str, str]]:
    try:
        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return []
    sections: list[tuple[str, str]] = []
    heading = path.name
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            if lines:
                content = "\n".join(lines).strip()
                if content:
                    sections.append((heading, content))
            heading = line.lstrip("#").strip() or path.name
            lines = []
            continue
        lines.append(line)
    content = "\n".join(lines).strip()
    if content:
        sections.append((heading, content))
    return sections


def retrieve_canonical_passages(
    query: str,
    *,
    limit: int = 3,
    max_chars: int = 1400,
) -> tuple[CanonicalPassage, ...]:
    query = canonical_query_text(query)
    if not _ENTITY_RE.search(str(query or "")):
        return ()
    query_tokens = _tokens(query)
    query_entities = query_tokens & _ENTITY_TOKENS
    if not query_entities:
        return ()
    ranked: list[CanonicalPassage] = []
    root = _root()
    for relative_path in _ALLOWLIST:
        path = _allowed_path(relative_path)
        if not path.is_file():
            continue
        for heading, raw_content in _sections(path):
            haystack = f"{heading}\n{raw_content}"
            section_tokens = _tokens(haystack)
            overlap = len(query_tokens & section_tokens)
            entity_overlap = len(
                query_entities & section_tokens
            )
            if overlap <= 0 or entity_overlap <= 0:
                continue
            heading_tokens = _tokens(heading)
            score = (
                overlap / max(1, len(query_tokens))
                + (0.25 * entity_overlap)
                + (
                    0.35
                    if query_entities & heading_tokens
                    else 0.0
                )
            )
            content = raw_content[
                : max(200, int(max_chars))
            ].rstrip()
            ranked.append(
                CanonicalPassage(
                    source_path=path.relative_to(root).as_posix(),
                    heading=heading,
                    content=content,
                    content_hash=hashlib.sha256(
                        raw_content.encode("utf-8")
                    ).hexdigest(),
                    score=round(score, 4),
                )
            )
    ranked.sort(
        key=lambda item: (
            -item.score,
            _ALLOWLIST.index(item.source_path),
            item.heading.lower(),
        )
    )
    return tuple(ranked[: max(1, int(limit))])


def canonical_context_items(
    query: str,
    *,
    limit: int = 3,
) -> list[ContextItem]:
    items: list[ContextItem] = []
    for index, passage in enumerate(
        retrieve_canonical_passages(query, limit=limit),
        start=1,
    ):
        source_id = (
            f"canonical:{passage.source_path}#{passage.heading}"
        )
        items.append(
            ContextItem(
                item_id=f"canonical-project-{index}",
                layer="bootstrap",
                source_type="canonical_document",
                title=(
                    f"{passage.heading} "
                    f"({passage.source_path})"
                ),
                content=passage.content,
                priority=min(
                    0.96,
                    0.84 + (passage.score * 0.08),
                ),
                confidence=0.94,
                must_keep=False,
                include_reason="canonical_repository_retrieval",
                metadata={
                    "scope": "project",
                    "source": "canonical_document",
                    "source_class": "canonical",
                    "source_id": source_id,
                    "source_path": passage.source_path,
                    "content_hash": passage.content_hash,
                    "status": "active",
                    "origin_chat_id": "",
                    "origin_project_id": "",
                },
                provenance={
                    "source_id": source_id,
                    "source_path": passage.source_path,
                    "content_hash": passage.content_hash,
                },
            )
        )
    return items


def canonical_context_text(
    query: str,
    *,
    limit: int = 3,
) -> str:
    return "\n\n".join(
        (
            f"[Canonical source: {passage.source_path}"
            f"#{passage.heading}; sha256={passage.content_hash}]\n"
            f"{passage.content}"
        )
        for passage in retrieve_canonical_passages(
            query,
            limit=limit,
        )
    )


__all__ = [
    "CanonicalPassage",
    "canonical_context_items",
    "canonical_context_text",
    "canonical_query_text",
    "has_canonical_project_entity",
    "retrieve_canonical_passages",
]
