from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import policy_engine
from core.backend_manager import BackendManager
from core.logging_config import setup_logging
from core.runtime_context import RuntimeContext, apply_runtime_context, build_runtime_context
from core.runtime_paths import ensure_runtime_dirs
from storage.db import healthcheck
from storage.migrations import run_migrations


@dataclass(frozen=True)
class RuntimeBackendSelection:
    backend_name: str
    device: str
    reason: str
    hardware: Any


@dataclass(frozen=True)
class BootstrappedRuntime:
    context: RuntimeContext
    backend_selection: RuntimeBackendSelection | None = None


def bootstrap_storage_environment(
    *,
    context: RuntimeContext | None = None,
    db_path: str | Path | None = None,
) -> RuntimeContext:
    runtime_context = apply_runtime_context(
        context or build_runtime_context(mode="storage", db_path=db_path)
    )
    ensure_runtime_dirs()
    run_migrations(runtime_context.paths.db_path)
    healthy = healthcheck(runtime_context.paths.db_path)
    if not healthy:
        raise RuntimeError("Database healthcheck failed.")
    return runtime_context


def bootstrap_runtime_environment(
    *,
    context: RuntimeContext | None = None,
    force_policy_reload: bool = False,
) -> RuntimeContext:
    runtime_context = bootstrap_storage_environment(context=context)
    policy_engine.load(force_reload=force_policy_reload)
    return runtime_context


def bootstrap_runtime_mode(
    *,
    mode: str,
    workspace_root: str | Path | None = None,
    db_path: str | Path | None = None,
    force_policy_reload: bool = False,
    configure_logging: bool = False,
    resolve_backend: bool = False,
    manager: BackendManager | None = None,
    allow_remote_only: bool | None = None,
) -> BootstrappedRuntime:
    context = build_runtime_context(
        mode=mode,
        workspace_root=workspace_root,
        db_path=db_path,
    )
    bootstrap_runtime_environment(context=context, force_policy_reload=force_policy_reload)
    if configure_logging:
        setup_logging(level=context.log_level, json_output=context.json_logs)
    backend_selection = (
        resolve_backend_selection(
            manager=manager,
            local_only=context.feature_flags.local_only_mode,
            allow_remote_only=(
                context.feature_flags.allow_remote_only_without_backend
                if allow_remote_only is None
                else allow_remote_only
            ),
        )
        if resolve_backend
        else None
    )
    return BootstrappedRuntime(context=context, backend_selection=backend_selection)


def resolve_backend_selection(
    *,
    manager: BackendManager | None = None,
    local_only: bool | None = None,
    allow_remote_only: bool | None = None,
) -> RuntimeBackendSelection:
    backend_manager = manager or BackendManager()
    hardware = backend_manager.detect_hardware()
    selection = backend_manager.select_backend(hardware)
    if backend_manager.healthcheck(selection):
        return RuntimeBackendSelection(
            backend_name=str(selection.backend_name),
            device=str(selection.device),
            reason=str(getattr(selection, "reason", "") or ""),
            hardware=hardware,
        )

    remote_allowed = (
        policy_engine.allow_remote_only_without_backend()
        if allow_remote_only is None
        else bool(allow_remote_only)
    )
    if not remote_allowed:
        # LOCAL ONLY MUST NOT MAKE THE APP UNSTARTABLE.
        #
        # `policy_engine.allow_remote_only_without_backend()` is
        # `... and not local_only_mode()`, so turning Local Only on withdraws the remote-only
        # fallback by design. That is correct. What was NOT correct is the consequence: on a
        # machine with no healthy local backend the daemon then died at boot with a bare
        # "No supported backend found." -- and since the pact offers Local Only as a FIRST-RUN
        # choice, an operator could take it and never start the app again. The crash removed the
        # only surface (Settings, the pact) from which the setting could be turned back off.
        #
        # Measured on the packaged bundle: fresh home boots remote_only; enable
        # `local_only_composite`; the next start raises here and the app is bricked.
        #
        # Booting is survivable and already proven: in exactly this state the runtime answers
        # deterministic turns and returns an honest typed refusal for anything needing a live
        # lookup. So boot, name the condition, and leave the operator a door. A turn that needs
        # a model still refuses honestly -- this widens nothing about what may be ANSWERED, only
        # about what may be REACHED.
        # Installation profiles and saved policy share the context flag at boot.
        # Direct callers retain the policy default; neither path enables inference.
        if (policy_engine.local_only_mode() if local_only is None else local_only):
            return RuntimeBackendSelection(
                backend_name="local_only_no_backend",
                device="cpu",
                reason=(
                    "Local Only is on and no local model backend is reachable. The app is "
                    "serving so this can be changed: install or start a local model, or turn "
                    "Local Only off in Settings."
                ),
                hardware=hardware,
            )
        raise RuntimeError("No supported backend found.")

    return RuntimeBackendSelection(
        backend_name="remote_only",
        device="cpu",
        reason=str(getattr(selection, "reason", "") or "no_supported_backend"),
        hardware=hardware,
    )
