from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from core.liquefy_models import LiquefySelfTestV1, ProofBundleV1, RestoreResultV1, SearchResultV1

_PACK_SCHEMA = "liquefy.tracevault.cli.v1"
_RESTORE_SCHEMA = "liquefy.tracevault.restore.cli.v1"
_SEARCH_SCHEMA = "liquefy.tracevault.search.cli.v1"
_GENERIC_SCHEMA = "liquefy.cli.v1"


class LiquefyClientV1:
    def __init__(self, *, env: dict[str, str] | None = None):
        self._env = dict(os.environ)
        if env is not None:
            self._env.update(env)
        self._generic_bin = self._resolve_bin("VOOL_LIQUEFY_BIN", ("liquefy",))
        self._pack_bin = self._resolve_bin("VOOL_LIQUEFY_PACK_BIN", ("liquefy-pack",))
        self._restore_bin = self._resolve_bin("VOOL_LIQUEFY_RESTORE_BIN", ("liquefy-restore",))
        self._search_bin = self._resolve_bin("VOOL_LIQUEFY_SEARCH_BIN", ("liquefy-search",))

    @property
    def available(self) -> bool:
        return bool(self._pack_bin or self._restore_bin or self._search_bin or self._generic_bin)

    def self_test(self) -> LiquefySelfTestV1:
        if self._pack_bin:
            command = [self._pack_bin, "--self-test", "--json"]
            payload = self._run_json(command, accept_exit_codes={0})
            return LiquefySelfTestV1(
                ok=bool(payload["ok"]),
                schema_version=str(payload["payload"].get("schema_version") or _PACK_SCHEMA),
                tool=str(payload["payload"].get("tool") or "tracevault_pack"),
                command=str(payload["payload"].get("command") or "self_test"),
                exit_code=int(payload["exit_code"]),
                error=str(payload.get("error") or ""),
                payload=dict(payload["payload"]),
            )
        if self._generic_bin:
            payload = self._run_json([self._generic_bin, "version", "--json"], accept_exit_codes={0})
            return LiquefySelfTestV1(
                ok=bool(payload["ok"]),
                schema_version=str(payload["payload"].get("schema_version") or _GENERIC_SCHEMA),
                tool=str(payload["payload"].get("tool") or "liquefy"),
                command=str(payload["payload"].get("command") or "version"),
                exit_code=int(payload["exit_code"]),
                error=str(payload.get("error") or ""),
                payload=dict(payload["payload"]),
            )
        return LiquefySelfTestV1(
            ok=False,
            schema_version=_GENERIC_SCHEMA,
            tool="liquefy",
            command="self_test",
            exit_code=127,
            error="Liquefy CLI is unavailable.",
            payload={},
        )

    def pack_run_bundle(self, input_dir: str | Path, out_dir: str | Path, org: str, metadata: dict[str, Any] | None) -> ProofBundleV1:
        if not self._pack_bin:
            return ProofBundleV1(
                ok=False,
                schema_version=_PACK_SCHEMA,
                tool="tracevault_pack",
                command="pack",
                source_dir=str(Path(input_dir).resolve()),
                out_dir=str(Path(out_dir).resolve()),
                exit_code=127,
                error="Liquefy pack command is unavailable.",
                metadata=dict(metadata or {}),
                payload={},
            )
        source_dir = Path(input_dir).resolve()
        target_out = Path(out_dir).resolve()
        target_out.parent.mkdir(parents=True, exist_ok=True)
        payload_metadata = dict(metadata or {})
        with self._stage_bundle_input(source_dir, payload_metadata) as staged_dir:
            payload = self._run_json(
                [self._pack_bin, str(staged_dir), "--org", str(org or "default"), "--out", str(target_out), "--json"],
                accept_exit_codes={0},
            )
        return ProofBundleV1(
            ok=bool(payload["ok"]),
            schema_version=str(payload["payload"].get("schema_version") or _PACK_SCHEMA),
            tool=str(payload["payload"].get("tool") or "tracevault_pack"),
            command=str(payload["payload"].get("command") or "pack"),
            source_dir=str(source_dir),
            out_dir=str(target_out),
            metadata=payload_metadata,
            exit_code=int(payload["exit_code"]),
            error=str(payload.get("error") or ""),
            payload=dict(payload["payload"]),
        )

    def restore_bundle(self, bundle_path: str | Path, out_dir: str | Path) -> RestoreResultV1:
        if not self._restore_bin:
            return RestoreResultV1(
                ok=False,
                schema_version=_RESTORE_SCHEMA,
                tool="tracevault_restore",
                command="restore",
                bundle_path=str(Path(bundle_path).resolve()),
                out_dir=str(Path(out_dir).resolve()),
                exit_code=127,
                error="Liquefy restore command is unavailable.",
                payload={},
            )
        target_bundle = Path(bundle_path).resolve()
        target_out = Path(out_dir).resolve()
        payload = self._run_json(
            [self._restore_bin, str(target_bundle), "--out", str(target_out), "--json"],
            accept_exit_codes={0},
        )
        return RestoreResultV1(
            ok=bool(payload["ok"]),
            schema_version=str(payload["payload"].get("schema_version") or _RESTORE_SCHEMA),
            tool=str(payload["payload"].get("tool") or "tracevault_restore"),
            command=str(payload["payload"].get("command") or "restore"),
            bundle_path=str(target_bundle),
            out_dir=str(target_out),
            exit_code=int(payload["exit_code"]),
            error=str(payload.get("error") or ""),
            payload=dict(payload["payload"]),
        )

    def search_bundle(self, bundle_path: str | Path, query: str, limit: int) -> SearchResultV1:
        if not self._search_bin and not self._generic_bin:
            return SearchResultV1(
                ok=False,
                schema_version=_SEARCH_SCHEMA,
                tool="tracevault_search",
                command="search",
                bundle_path=str(Path(bundle_path).resolve()),
                query=str(query or ""),
                limit=int(limit or 0),
                exit_code=127,
                error="Liquefy search command is unavailable.",
                payload={},
            )
        command = [self._search_bin, str(Path(bundle_path).resolve()), "--query", str(query or ""), "--limit", str(max(1, int(limit or 20))), "--json"] if self._search_bin else [self._generic_bin, "search", str(Path(bundle_path).resolve()), "--query", str(query or ""), "--limit", str(max(1, int(limit or 20))), "--json"]
        payload = self._run_json(command, accept_exit_codes={0, 1})
        raw = dict(payload["payload"])
        schema_version = str(raw.get("schema_version") or _SEARCH_SCHEMA)
        tool = str(raw.get("tool") or "tracevault_search")
        command_name = str(raw.get("command") or "search")
        return SearchResultV1(
            ok=bool(payload["ok"]),
            schema_version=schema_version,
            tool=tool,
            command=command_name,
            bundle_path=str(Path(bundle_path).resolve()),
            query=str(query or ""),
            limit=max(1, int(limit or 20)),
            exit_code=int(payload["exit_code"]),
            error=str(payload.get("error") or ""),
            payload=raw,
        )

    def _resolve_bin(self, env_name: str, names: tuple[str, ...]) -> str:
        explicit = str(self._env.get(env_name) or "").strip()
        if explicit:
            return explicit
        path = str(self._env.get("PATH") or "")
        if env_name == "VOOL_LIQUEFY_BIN":
            generic = shutil.which(names[0], path=path)
            return generic or ""
        for name in names:
            found = shutil.which(name, path=path)
            if found:
                return found
        return ""

    def _run_json(self, argv: list[str], *, accept_exit_codes: set[int]) -> dict[str, Any]:
        run_argv = self._execution_argv(argv)
        try:
            result = subprocess.run(
                run_argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._env,
            )
        except FileNotFoundError:
            return {"ok": False, "exit_code": 127, "error": f"Missing Liquefy executable: {argv[0]}", "payload": {}}
        stdout = str(result.stdout or "").strip()
        stderr = str(result.stderr or "").strip()
        payload: dict[str, Any] = {}
        if stdout:
            try:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
        ok = int(result.returncode) in accept_exit_codes and bool(payload or result.returncode == 0)
        if payload:
            payload.setdefault("ok", int(result.returncode) == 0)
        error = ""
        if not ok:
            error = stderr or stdout or f"Liquefy command failed with exit code {int(result.returncode)}."
        return {"ok": ok, "exit_code": int(result.returncode), "error": error, "payload": payload}

    def _execution_argv(self, argv: list[str]) -> list[str]:
        if os.name != "nt" or not argv:
            return argv
        executable = Path(str(argv[0] or ""))
        if executable.suffix.lower() in {".exe", ".bat", ".cmd", ".com"}:
            return argv
        if not executable.exists() or not executable.is_file():
            return argv
        try:
            first_line = executable.read_text(encoding="utf-8", errors="ignore").splitlines()[:1]
        except OSError:
            first_line = []
        looks_like_python = executable.suffix.lower() in {".py", ".pyw"} or (
            bool(first_line) and "python" in first_line[0].lower()
        )
        if looks_like_python:
            return [sys.executable, *argv]
        return argv

    def _stage_bundle_input(self, input_dir: Path, metadata: dict[str, Any]):
        if not metadata:
            return _IdentityContext(input_dir)
        temp_dir = tempfile.TemporaryDirectory()
        staged_root = Path(temp_dir.name) / input_dir.name
        shutil.copytree(input_dir, staged_root)
        (staged_root / "run_metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
        return _TemporaryPathContext(temp_dir, staged_root)


class _IdentityContext:
    def __init__(self, path: Path):
        self._path = path

    def __enter__(self) -> Path:
        return self._path

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _TemporaryPathContext:
    def __init__(self, temp_dir: tempfile.TemporaryDirectory, path: Path):
        self._temp_dir = temp_dir
        self._path = path

    def __enter__(self) -> Path:
        return self._path

    def __exit__(self, exc_type, exc, tb) -> None:
        self._temp_dir.cleanup()
