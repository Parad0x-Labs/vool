from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant", "context"]


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

    def user_prompt(self) -> str:
        parts = [message.content for message in self.messages if message.role in {"user", "context"}]
        return "\n\n".join(part for part in parts if part.strip())

    def as_openai_messages(self) -> list[dict[str, Any]]:
        return [message.as_openai_message() for message in self.messages]
