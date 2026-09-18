from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LiquefySelfTestV1:
    ok: bool
    schema_version: str
    tool: str
    command: str
    exit_code: int = 0
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.liquefy.self_test.v1",
            "ok": bool(self.ok),
            "schema_version": str(self.schema_version or ""),
            "tool": str(self.tool or ""),
            "command": str(self.command or ""),
            "exit_code": int(self.exit_code or 0),
            "error": str(self.error or ""),
            "payload": dict(self.payload or {}),
        }


@dataclass(frozen=True)
class ProofBundleV1:
    ok: bool
    schema_version: str
    tool: str
    command: str
    source_dir: str
    out_dir: str
    metadata: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.liquefy.proof_bundle.v1",
            "ok": bool(self.ok),
            "schema_version": str(self.schema_version or ""),
            "tool": str(self.tool or ""),
            "command": str(self.command or ""),
            "source_dir": str(self.source_dir or ""),
            "out_dir": str(self.out_dir or ""),
            "metadata": dict(self.metadata or {}),
            "exit_code": int(self.exit_code or 0),
            "error": str(self.error or ""),
            "payload": dict(self.payload or {}),
        }


@dataclass(frozen=True)
class RestoreResultV1:
    ok: bool
    schema_version: str
    tool: str
    command: str
    bundle_path: str
    out_dir: str
    exit_code: int = 0
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.liquefy.restore_result.v1",
            "ok": bool(self.ok),
            "schema_version": str(self.schema_version or ""),
            "tool": str(self.tool or ""),
            "command": str(self.command or ""),
            "bundle_path": str(self.bundle_path or ""),
            "out_dir": str(self.out_dir or ""),
            "exit_code": int(self.exit_code or 0),
            "error": str(self.error or ""),
            "payload": dict(self.payload or {}),
        }


@dataclass(frozen=True)
class SearchResultV1:
    ok: bool
    schema_version: str
    tool: str
    command: str
    bundle_path: str
    query: str
    limit: int
    exit_code: int = 0
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "vool.liquefy.search_result.v1",
            "ok": bool(self.ok),
            "schema_version": str(self.schema_version or ""),
            "tool": str(self.tool or ""),
            "command": str(self.command or ""),
            "bundle_path": str(self.bundle_path or ""),
            "query": str(self.query or ""),
            "limit": int(self.limit or 0),
            "exit_code": int(self.exit_code or 0),
            "error": str(self.error or ""),
            "payload": dict(self.payload or {}),
        }
