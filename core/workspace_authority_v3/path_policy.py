"""Pure reserved-namespace validation. This module never touches the filesystem."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from core.workspace_authority_v3.base import ContractValidationError, require_digest

RESERVED_AUTHORITY_NAMESPACE = ".vool-authority"
_WINDOWS_DEVICE_PREFIX_RE = re.compile(r"^(?:[/\\]{2}[?.][/\\]|[/\\]??\\[?.]\\)", re.IGNORECASE)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_DOS_ALIAS_RE = re.compile(r"^\.?vool[-a-z0-9]{0,3}~[0-9]+(?:\..*)?$", re.IGNORECASE)


class ReservedNamespaceError(ContractValidationError):
    pass


def _windows_alias_key(component: str) -> str:
    normalized = unicodedata.normalize("NFKC", component)
    stream_base = normalized.split(":", 1)[0]
    return stream_base.rstrip(" .").casefold()


def validate_normal_plan_path(path: str, *, destructive: bool = False) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ReservedNamespaceError("REJECTED_RESERVED_NAMESPACE")
    normalized = unicodedata.normalize("NFKC", path)
    if _WINDOWS_DEVICE_PREFIX_RE.match(normalized) or _WINDOWS_DRIVE_RE.match(normalized) or normalized.startswith(("/", "\\")):
        raise ReservedNamespaceError("REJECTED_RESERVED_NAMESPACE")
    components = normalized.replace("\\", "/").split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ReservedNamespaceError("REJECTED_RESERVED_NAMESPACE")
    for component in components:
        alias_key = _windows_alias_key(component)
        if alias_key == RESERVED_AUTHORITY_NAMESPACE or _DOS_ALIAS_RE.fullmatch(alias_key):
            raise ReservedNamespaceError("REJECTED_RESERVED_NAMESPACE")
        if ":" in component:
            raise ReservedNamespaceError("REJECTED_RESERVED_NAMESPACE")
    if destructive and len(components) == 0:
        raise ReservedNamespaceError("REJECTED_RESERVED_NAMESPACE")
    return "/".join(components)


@dataclass(frozen=True, slots=True)
class NormalPlanPath:
    path: str
    destructive: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", validate_normal_plan_path(self.path, destructive=self.destructive))


@dataclass(frozen=True, slots=True)
class NormalPlan:
    plan_digest: str
    paths: tuple[NormalPlanPath, ...]

    def __post_init__(self) -> None:
        require_digest(self.plan_digest, "plan_digest")
        if not self.paths:
            raise ContractValidationError("normal plans require at least one typed path")
        if not isinstance(self.paths, tuple) or not all(isinstance(path, NormalPlanPath) for path in self.paths):
            raise TypeError("normal plans accept NormalPlanPath values only")


@dataclass(frozen=True, slots=True)
class AuthorityInternalTransition:
    """A distinct non-executable contract; there is no caller-controlled internal flag."""

    transition_digest: str

    def __post_init__(self) -> None:
        require_digest(self.transition_digest, "transition_digest")


__all__ = [
    "RESERVED_AUTHORITY_NAMESPACE",
    "AuthorityInternalTransition",
    "NormalPlan",
    "NormalPlanPath",
    "ReservedNamespaceError",
    "validate_normal_plan_path",
]
