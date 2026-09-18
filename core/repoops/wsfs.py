"""WorkspaceFS: typed repository file operations behind path authority.

The failure this closes: Git Ninja asked to edit "src/app.py" resolving into a
NEIGHBOURING checkout, or out of the workspace entirely via ``..`` or a
symlink. Every path is resolved against the bound workspace root and must stay
inside it after resolution — traversal, absolute escapes and symlinked exits
are refused before any I/O happens.

Mutating operations are admitted by the platform ExecutionBroker (token
'wsfs.<op>'); this module implements only semantics and the path guard.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.platform.broker import EffectOutcome, EffectRequest, ExecutionBroker
from core.repoops.identity import RepositoryWorkspace

WSFS_TOKENS = {
    "read": "wsfs.read",
    "write": "wsfs.write",
    "delete": "wsfs.delete",
    "move": "wsfs.move",
}


class PathEscapeError(RuntimeError):
    """A path resolves outside the bound workspace (traversal/symlink/absolute)."""


class WorkspaceFS:
    """File authority for ONE RepositoryWorkspace."""

    def __init__(self, workspace: RepositoryWorkspace) -> None:
        self._root = Path(os.path.realpath(workspace.root))
        self._workspace = workspace

    # -- the guard ------------------------------------------------------------------
    def resolve(self, rel_path: str) -> Path:
        """Resolve rel_path inside the root; refuse anything that lands outside.

        Symlinks are resolved too: a link pointing out of the workspace is an
        escape, not a shortcut.
        """
        candidate = Path(rel_path)
        if candidate.is_absolute():
            raise PathEscapeError(f"absolute path {rel_path!r} escapes workspace authority")
        resolved = os.path.realpath(self._root / candidate)
        if not resolved.startswith(str(self._root) + os.sep):
            raise PathEscapeError(
                f"{rel_path!r} resolves to {resolved}, outside {self._root}"
            )
        return Path(resolved)

    def require_same_workspace(self, other: WorkspaceFS) -> None:
        if self._root != other._root:
            raise PathEscapeError(
                f"wrong worktree: bound to {self._root}, operation targeted {other._root}"
            )

    # -- reads ----------------------------------------------------------------------
    def read(self, rel_path: str) -> str:
        path = self.resolve(rel_path)
        try:
            return path.read_text()
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"{rel_path!r} not in workspace") from exc

    def list_dir(self, rel_path: str = ".") -> tuple[str, ...]:
        base = self.resolve(rel_path)
        return tuple(sorted(p.name for p in base.iterdir()))

    def stat(self, rel_path: str) -> dict[str, object]:
        st = self.resolve(rel_path).stat()
        return {"size": st.st_size, "is_file": self.resolve(rel_path).is_file()}

    def find(self, pattern: str) -> tuple[str, ...]:
        matches = self._root.glob(pattern)
        return tuple(sorted(
            str(m.relative_to(self._root)) for m in matches if m.is_file()
        ))

    # -- mutations (broker-admitted) ---------------------------------------------------
    def _admit(
        self,
        fork,
        broker: ExecutionBroker,
        op: str,
        params: dict[str, object],
        dispatch,
    ):
        request = EffectRequest(
            effect_id=f"wsfs.{op}",
            required_capability=WSFS_TOKENS[op],
            params={"workspace": self._workspace.key, **params},
            idempotency_key=_content_key(op, params),
        )
        return broker.execute(fork, request, dispatch)

    def write(self, fork, broker: ExecutionBroker, rel_path: str, content: str,
              *, expected_prior_sha256: str | None = None) -> object:
        """Create/overwrite one file. expected_prior_sha256 binds a replace race."""
        path = self.resolve(rel_path)
        prior = _sha256(path.read_bytes())[:16] if path.exists() else ""

        def dispatch() -> EffectOutcome:
            if expected_prior_sha256 is not None and prior != expected_prior_sha256:
                return EffectOutcome(
                    status="refused", reason="prior_state_mismatch",
                    evidence={"expected": expected_prior_sha256, "actual": prior},
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            return EffectOutcome(status="applied", evidence={
                "path": rel_path, "bytes": len(content.encode()),
                "new_sha256": _sha256(path.read_bytes())[:16],
            })

        return self._admit(fork, broker, "write", {"path": rel_path}, dispatch)

    def delete(self, fork, broker: ExecutionBroker, rel_path: str) -> object:
        path = self.resolve(rel_path)

        def dispatch() -> EffectOutcome:
            if not path.exists():
                return EffectOutcome(status="failed", reason=f"{rel_path} already absent")
            blob = path.read_bytes()
            path.unlink()
            return EffectOutcome(status="applied", evidence={
                "path": rel_path, "recoverable_sha256": _sha256(blob)[:16],
            })

        return self._admit(fork, broker, "delete", {"path": rel_path}, dispatch)


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _content_key(op: str, params: dict[str, object]) -> str:
    """Derived idempotency key: op + canonical params (+ content hash)."""
    import hashlib
    import json

    canonical = json.dumps(params, sort_keys=True)
    if isinstance(params.get("content"), str):
        canonical += ":" + params["content"]
    return hashlib.sha256(f"{op}:{canonical}".encode()).hexdigest()[:32]
