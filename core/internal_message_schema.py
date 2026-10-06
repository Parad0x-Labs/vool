from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant", "context"]

# Opens the per-turn system message that follows the chat history (clock, skill pick, corrections,
# memory recall and other facts that change from turn to turn). The leading system message stays
# byte-stable so provider prompt caches can reuse it and the history behind it. Wire-level code that
# only sees plain dict messages (memory prefix placement, local context-window fitting) finds the
# message by this header.
TURN_DIRECTIVES_HEADER = "Context for this turn:"


def is_turn_directives_message(message: Any) -> bool:
    if not isinstance(message, dict) or str(message.get("role") or "").strip().lower() != "system":
        return False
    content = message.get("content")
    return isinstance(content, str) and content.startswith(TURN_DIRECTIVES_HEADER)


@dataclass
class InternalMessage:
    role: MessageRole
    content: str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_openai_message(self) -> dict[str, Any]:
        provider_role = "user" if self.role == "context" else self.role
        payload: dict[str, Any] = {"role": provider_role, "content": self.content}
        if self.name:
            payload["name"] = self.name
        return payload


@dataclass
class InternalModelRequest:
    task_kind: str
    task_class: str
    output_mode: str
    messages: list[InternalMessage]
    trace_id: str
    max_output_tokens: int
    temperature: float
    ambiguity_confidence: float
    constraints: list[str] = field(default_factory=list)
    context_summary: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)

    def system_prompt(self) -> str:
        for message in self.messages:
            if message.role == "system":
                return message.content
        return ""

    def instructions(self) -> str:
        """Every system instruction the model receives, in order: the stable leading system message
        and the per-turn system message after the history. Retrieved-evidence capsules (also sent
        with the system role) are evidence, not instructions, and are left out."""
        return "\n\n".join(
            message.content
            for message in self.messages
            if message.role == "system" and "<retrieved_context>" not in message.content
        )

    def user_prompt(self) -> str:
        parts = [message.content for message in self.messages if message.role in {"user", "context"}]
        return "\n\n".join(part for part in parts if part.strip())

    def as_openai_messages(self) -> list[dict[str, Any]]:
        return [message.as_openai_message() for message in self.messages]
