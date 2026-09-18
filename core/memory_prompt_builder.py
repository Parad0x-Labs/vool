from __future__ import annotations

from typing import Any

from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
from core.context_scope import ContextAccessPolicy
from core.fact_extractor import stable_text_embedding
from core.vool_memory import VoolMemory

MAX_MEMORY_CHARS = 2000


class MemoryPromptBuilder:
    def __init__(self, memory: VoolMemory, *, max_chars: int = MAX_MEMORY_CHARS) -> None:
        self._memory = memory
        self._max_chars = max(256, int(max_chars))

    def build_prefix(
        self,
        query: str | None = None,
        query_embedding: list[float] | None = None,
        top_k_nodes: int = 3,
        access_policy: ContextAccessPolicy | None = None,
    ) -> str:
        """Build a current-chat semantic prefix for explicit diagnostic use.

        Provider adapters must not call this helper. Context is assembled by the
        scoped loader before a provider request is prepared. Legacy memory blocks
        do not carry chat/project provenance and are therefore quarantined.
        """
        if access_policy is None or access_policy.namespace_state != "active":
            return ""
        parts: list[str] = []

        embedding = query_embedding
        if embedding is None and query:
            embedding = stable_text_embedding(query)
        if embedding and self._memory.node_count() > 0:
            snippets = []
            for node, score in self._memory.node_search(
                embedding,
                top_k=top_k_nodes,
                min_score=0.70,
                session_id=access_policy.chat_id,
            ):
                tags = ", ".join(node.tags) if node.tags else "memory"
                snippets.append(f"- [{tags}; score={score:.2f}] {node.context_description}")
            if snippets:
                parts.append("## Relevant Current-Chat Context\n" + "\n".join(snippets))

        full = "\n\n".join(part for part in parts if part.strip()).strip()
        if len(full) > self._max_chars:
            return full[: self._max_chars].rstrip() + "\n[memory truncated]"
        return full


def build_memory_prefix_for_request(request: Any) -> str:
    metadata = dict(getattr(request, "metadata", None) or {})
    config = dict(metadata.get("memory_prompt") or {})
    if not bool(config.get("enabled")):
        return ""
    policy = metadata.get("_context_access_policy")
    if not isinstance(policy, ContextAccessPolicy):
        return ""
    runtime_home = str(config.get("runtime_home") or "").strip() or None
# The partition the CHAT writer uses. Three readers defaulted to a bare "vool" literal while
# every chat write goes to SEMANTIC_MEMORY_AGENT_ID ("vool_chat"), and `memory_blocks` is keyed
# PRIMARY KEY (block_name, agent_id) -- so the reader opened an empty shelf. Measured on the live
# store 2026-08-18: 132 nodes and 3 blocks under "vool_chat" against 1 and 1 under "vool", and
# block_read("user_profile") returned the operator's stored name under the writer's id and None
# under the reader's. An explicit caller-supplied agent_id still wins; only the DEFAULT moves.
    agent_id = str(config.get("agent_id") or "").strip() or SEMANTIC_MEMORY_AGENT_ID
    max_chars = int(config.get("max_chars") or MAX_MEMORY_CHARS)
    query = str(getattr(request, "prompt", "") or "").strip()
    try:
        memory = VoolMemory(runtime_home=runtime_home, agent_id=agent_id)
        try:
            return MemoryPromptBuilder(memory, max_chars=max_chars).build_prefix(
                query=query,
                access_policy=policy,
            )
        finally:
            memory.close()
    except Exception:
        return ""


def apply_memory_prefix_to_messages(
    messages: list[dict[str, Any]],
    request: Any,
    *,
    prefix: str | None = None,
) -> list[dict[str, Any]]:
    if prefix is None:
        prefix = build_memory_prefix_for_request(request)
    if not prefix:
        return messages
    augmented = [dict(message) for message in list(messages or [])]
    for message in augmented:
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        original = str(message.get("content") or "").strip()
        message["content"] = f"{prefix}\n\n---\n\n{original}" if original else prefix
        return augmented
    return [{"role": "system", "content": prefix}, *augmented]


__all__ = [
    "MAX_MEMORY_CHARS",
    "MemoryPromptBuilder",
    "apply_memory_prefix_to_messages",
    "build_memory_prefix_for_request",
]
