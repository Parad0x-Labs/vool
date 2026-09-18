from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from network import signer

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
_UNIX_PATH_RE = re.compile(r"(^|[\s(])/[^\s)]+")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+")
_TOKENISH_RE = re.compile(r"\b(?:[A-Fa-f0-9]{40,}|[A-Za-z0-9_]{40,})\b")


def _scan_for_leaks(value: str) -> None:
    if not value:
        return
    if _URL_RE.search(value):
        raise ValueError("URLs are not allowed in strict task capsules.")
    if _EMAIL_RE.search(value):
        raise ValueError("Emails are not allowed in task capsules.")
    if _UNIX_PATH_RE.search(value) or _WIN_PATH_RE.search(value):
        raise ValueError("Raw file paths are not allowed in task capsules.")
    if _TOKENISH_RE.search(value):
        raise ValueError("Token-like strings are not allowed in task capsules.")


def sanitize_for_capsule(text: str) -> str:
    """Rewrite the strict leak classes into typed placeholders, using the scan's own patterns.

    The capsule's scan stays the law; this is the sanitizer a BUILDER applies before handing
    user-derived text to a capsule, so strict privacy never turns into a dead turn. Measured
    live 2026-09-15: an interrupted-work resume carried its workspace path into the decomposer,
    `redact_text` passed it (its path list names only the sensitive absolute prefixes), and the
    turn died at `sanitized_context.abstract_inputs` -- "Raw file paths are not allowed in task
    capsules". One sanitizer, owned beside the scan it must satisfy, covers every class the
    scan rejects, for any wording.
    """
    value = str(text or "")
    value = _URL_RE.sub("<url>", value)
    value = _EMAIL_RE.sub("<email>", value)
    value = _UNIX_PATH_RE.sub(r"\1<path>", value)
    value = _WIN_PATH_RE.sub("<path>", value)
    value = _TOKENISH_RE.sub("<token>", value)
    return value


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RewardHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: int = Field(default=0, ge=0, le=1000)
    wnull_pending: int = Field(default=0, ge=0, le=1_000_000)


class SanitizedContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_class: str = Field(max_length=128)
    environment_tags: dict[str, str] = Field(default_factory=dict)
    abstract_inputs: list[str] = Field(default_factory=list, max_length=32)
    known_constraints: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("problem_class")
    @classmethod
    def validate_problem_class(cls, v: str) -> str:
        _scan_for_leaks(v)
        return v.strip()

    @field_validator("abstract_inputs")
    @classmethod
    def validate_abstract_inputs(cls, values: list[str]) -> list[str]:
        for item in values:
            _scan_for_leaks(item)
        return values

    @field_validator("known_constraints")
    @classmethod
    def validate_known_constraints(cls, values: list[str]) -> list[str]:
        for item in values:
            _scan_for_leaks(item)
        return values


class TaskCapsule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capsule_id: str = Field(min_length=8, max_length=128)
    task_id: str = Field(min_length=8, max_length=128)
    parent_agent_id: str = Field(min_length=16, max_length=256)
    task_type: Literal[
        "research",
        "classification",
        "ranking",
        "validation",
        "planning",
        "code_reasoning",
        "documentation",
    ]
    subtask_type: str = Field(max_length=128)
    summary: str = Field(max_length=1024)
    sanitized_context: SanitizedContext
    allowed_operations: list[Literal["reason", "research", "compare", "rank", "summarize", "validate", "draft"]]
    forbidden_operations: list[Literal["execute", "write_files", "access_db", "request_secrets", "persist_data", "install_packages", "call_shell"]]
    privacy_level: Literal["strict", "standard", "relaxed"] = "strict"
    learning_allowed: bool = False
    is_benchmark: bool = False
    required_model: Optional[str] = None
    max_response_bytes: int = Field(ge=256, le=65536, default=8192)
    deadline_ts: datetime
    reward_hint: RewardHint = Field(default_factory=RewardHint)
    capsule_hash: str = Field(min_length=32, max_length=128)
    signature: str = Field(min_length=16, max_length=4096)

    @field_validator("subtask_type", "summary")
    @classmethod
    def validate_string_fields(cls, v: str) -> str:
        _scan_for_leaks(v)
        return " ".join(v.strip().split())

    @field_validator("deadline_ts")
    @classmethod
    def validate_deadline(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("deadline_ts must include timezone")
        if v <= datetime.now(timezone.utc):
            raise ValueError("deadline_ts must be in the future")
        return v

    @model_validator(mode="after")
    def validate_scope(self) -> TaskCapsule:
        if self.privacy_level != "strict":
            raise ValueError("v1 only allows privacy_level='strict'")
        if "execute" not in self.forbidden_operations:
            raise ValueError("Task capsule must explicitly forbid execution.")
        if "access_db" not in self.forbidden_operations:
            raise ValueError("Task capsule must explicitly forbid DB access.")
        if "call_shell" not in self.forbidden_operations:
            raise ValueError("Task capsule must explicitly forbid shell access.")
        return self


def _unsigned_dict(capsule: dict[str, Any]) -> dict[str, Any]:
    out = dict(capsule)
    out.pop("signature", None)
    return out


def canonical_unsigned_bytes(capsule: dict[str, Any]) -> bytes:
    return json.dumps(
        _unsigned_dict(capsule),
        sort_keys=True,
        separators=(",", ":"),
        default=lambda v: v.isoformat() if isinstance(v, datetime) else v,
    ).encode("utf-8")


def compute_capsule_hash(unsigned_capsule: dict[str, Any]) -> str:
    # hash excludes signature; includes capsule_hash field if already present? no.
    payload = dict(unsigned_capsule)
    payload.pop("capsule_hash", None)
    payload.pop("signature", None)
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda v: v.isoformat() if isinstance(v, datetime) else v,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_task_capsule(
    *,
    parent_agent_id: str,
    task_id: str,
    task_type: str,
    subtask_type: str,
    summary: str,
    sanitized_context: dict[str, Any],
    allowed_operations: list[str],
    deadline_ts: datetime,
    learning_allowed: bool = False,
    reward_hint: dict[str, int] | None = None,
) -> TaskCapsule:
    base = {
        "capsule_id": str(uuid.uuid4()),
        "task_id": task_id,
        "parent_agent_id": parent_agent_id,
        "task_type": task_type,
        "subtask_type": subtask_type,
        "summary": summary,
        "sanitized_context": sanitized_context,
        "allowed_operations": allowed_operations,
        "forbidden_operations": [
            "execute",
            "write_files",
            "access_db",
            "request_secrets",
            "persist_data",
            "install_packages",
            "call_shell",
        ],
        "privacy_level": "strict",
        "learning_allowed": learning_allowed,
        "is_benchmark": False,
        "required_model": None,
        "max_response_bytes": 8192,
        "deadline_ts": deadline_ts,
        "reward_hint": reward_hint or {"points": 0, "wnull_pending": 0},
    }

    capsule_hash = compute_capsule_hash(base)
    base["capsule_hash"] = capsule_hash
    signature = signer.sign(canonical_unsigned_bytes(base))
    base["signature"] = signature

    return TaskCapsule.model_validate(base)


def resign_task_capsule(capsule: TaskCapsule) -> TaskCapsule:
    """
    Recompute capsule_hash and signature after a locally-owned capsule was
    mutated. Without this, helpers reject the capsule with a hash mismatch.
    Only valid on the node that owns the parent signing key.
    """
    base = capsule.model_dump()
    base["capsule_hash"] = compute_capsule_hash(base)
    base["signature"] = signer.sign(canonical_unsigned_bytes(base))
    return TaskCapsule.model_validate(base)


def verify_task_capsule(capsule: dict[str, Any]) -> TaskCapsule:
    obj = TaskCapsule.model_validate(capsule)
    raw = obj.model_dump()

    expected_hash = compute_capsule_hash(raw)
    if expected_hash != obj.capsule_hash:
        raise ValueError("Task capsule hash mismatch.")

    if not signer.verify(canonical_unsigned_bytes(raw), obj.signature, obj.parent_agent_id):
        raise ValueError("Task capsule signature invalid.")

    return obj
