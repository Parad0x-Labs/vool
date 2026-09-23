"""Ollama-compatible VOOL API entrypoint with an ASGI app factory.

The runtime bootstrap and route logic now live under ``core.web.api``.
This module remains the stable facade for callers and tests that still
import legacy helpers from ``apps.vool_api_server``.
"""

from __future__ import annotations

from core.env_compat import apply_legacy_nulla_env

apply_legacy_nulla_env()  # NULLA_* shells keep working; VOOL_* wins (see core/env_compat.py)

import argparse
import contextlib
import contextvars
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _prioritize_project_root_on_sys_path(*, project_root: Path = PROJECT_ROOT) -> None:
    root_text = str(project_root)
    with contextlib.suppress(ValueError):
        sys.path.remove(root_text)
    sys.path.insert(0, root_text)


if __package__ in {None, ""}:
    _prioritize_project_root_on_sys_path()

# Hide child-process console windows on Windows BEFORE any import that might spawn one (e.g. the
# git-based version stamp at bootstrap). Without this the server -- though windowless itself --
# flashes a console window for every git/probe/tool subprocess, which looks like malware.
from core.windows_quiet_subprocess import enable_quiet_subprocess

enable_quiet_subprocess()

from core.runtime_capabilities import runtime_capability_snapshot
from core.runtime_provider_defaults import default_runtime_model_tag
from core.vool_workstation_ui import VOOL_WORKSTATION_DEPLOYMENT_VERSION
from core.web.api.app import create_api_app
from core.web.api.runtime import (
    MODEL_NAME,
    RuntimeServices,
    bootstrap_runtime_services,
    host_header_allowed,
)
from core.web.api.runtime import (
    daemon_runtime_config as _daemon_runtime_config_impl,
)
from core.web.api.runtime import (
    default_workspace_root as _default_workspace_root_impl,
)
from core.web.api.runtime import (
    ensure_default_provider as _ensure_default_provider_impl,
)
from core.web.api.runtime import (
    format_runtime_event_text as _format_runtime_event_text_impl,
)
from core.web.api.runtime import (
    normalize_chat_history as _normalize_chat_history_impl,
)
from core.web.api.runtime import (
    parameter_count_for_model as _parameter_count_for_model_impl,
)
from core.web.api.runtime import (
    parameter_size_for_model as _parameter_size_for_model_impl,
)
from core.web.api.runtime import (
    run_agent as _run_agent_impl,
)
from core.web.api.runtime import (
    stable_openclaw_session_id as _stable_openclaw_session_id_impl,
)
from core.web.api.runtime import (
    stream_agent_with_events as _stream_agent_with_events_impl,
)
from core.web.api.service import (
    apply_runtime_headers,
    dispatch_get,
    dispatch_post,
    dispatch_upload,
    is_raw_upload_path,
    json_response,
    raw_upload_body_ceiling,
)

logger = logging.getLogger("vool.api")

VOOL_API_PORT = 11435

_runtime_services: RuntimeServices | None = None
_agent = None
_daemon = None


def _sync_runtime_aliases(runtime: RuntimeServices) -> None:
    global _agent, _daemon
    _agent = runtime.agent
    _daemon = runtime.daemon


def _compat_runtime_services() -> RuntimeServices:
    runtime = _runtime_services or RuntimeServices()
    runtime_model_tag = str(runtime.runtime_model_tag or "").strip() or default_runtime_model_tag()
    return RuntimeServices(
        agent=_agent if _agent is not None else runtime.agent,
        daemon=_daemon if _daemon is not None else runtime.daemon,
        display_name=str(runtime.display_name or "VOOL"),
        runtime_model_tag=runtime_model_tag,
        runtime_parameter_size=str(runtime.runtime_parameter_size or _parameter_size_for_model(runtime_model_tag)),
        runtime_started_at=str(runtime.runtime_started_at or ""),
        runtime_home=str(runtime.runtime_home or ""),
        runtime_version_stamp=dict(runtime.runtime_version_stamp or {}),
        public_hive_auth=dict(runtime.public_hive_auth or {}),
        provider_capability_truth=tuple(dict(item) for item in tuple(runtime.provider_capability_truth or ())),
    )


def _legacy_handler_runtime(server: object | None = None) -> RuntimeServices:
    attached_runtime = getattr(server, "vool_runtime", None) if server is not None else None
    if isinstance(attached_runtime, RuntimeServices):
        return attached_runtime
    if isinstance(_runtime_services, RuntimeServices):
        return _runtime_services
    return RuntimeServices()


def _load_installed_plugins() -> None:
    """Register the tools declared by installed plugin manifests.

    `core/plugin_tools.py` was written, validated and tested, and then never called: measured
    2026-08-29, `load_all`/`register_plugin` had zero production callers, so `registered_tools()`
    returned only the built-ins and no plugin-declared tool could EVER be dispatched. The
    downstream consumers were already built for this — `operation_catalog` and `admission` read
    `registered_tools()` precisely so plugins are included — so this one call completes the lane.

    Fail-soft by construction: `load_all` already skips a bad pack and returns its error rather
    than raising, and the whole call is guarded so a plugin root that does not exist, or any
    unforeseen failure, can never stop the server from starting.

    BOUNDED by construction, too. Measured on the packaged 352ce68b app (2026-09-10): this call
    listed the plugin folder on the main thread before the port was bound, the directory open
    hung in the kernel, and the native host tore the child down after 90 s -- no window. The
    folder is now opened by `core.plugin_catalog.discover_and_register`'s killable probe within
    `BOOT_PROBE_BUDGET_S`; a folder that does not answer leaves the runtime SERVING with the
    storage state (stalled / denied / missing / failed) reported on /healthz, /api/plugins and
    the plugin panel, and a rescan loads the packs when it answers. Nothing is disabled, no
    configuration is touched, and no thread of this process is left parked in the kernel.
    """
    try:
        from core.plugin_catalog import (
            BOOT_PROBE_BUDGET_S,
            STORAGE_ACCESSIBLE,
            STORAGE_MISSING,
            discover_and_register,
        )

        state = discover_and_register(budget_s=BOOT_PROBE_BUDGET_S, reason="boot")
        status = str(state.get("state") or "")
        # The BUNDLED packs load through the same door, whatever the external folder's state:
        # a fresh profile with no Desktop installation still gets the packs the app ships.
        bundled = list(state.get("bundled_loaded") or [])
        if bundled:
            logger.info("Loaded %d bundled plugin(s): %s", len(bundled), ", ".join(bundled))
        for error in state.get("bundled_errors") or []:
            logger.warning("Bundled plugin skipped: %s", error)
        if status == STORAGE_ACCESSIBLE:
            loaded = list(state.get("loaded") or [])
            if loaded:
                logger.info("Loaded %d plugin(s): %s", len(loaded), ", ".join(sorted(loaded)))
            for error in state.get("errors") or []:
                logger.warning("Plugin skipped: %s", error)
        elif status == STORAGE_MISSING:
            if bundled:
                logger.info("No external plugins repo found; serving the bundled plugins only.")
            else:
                logger.info("No plugins repo found; the runtime serves its built-in tools only.")
        else:
            logger.warning(
                "Plugin storage %s at %s (%s); serving without plugins until a rescan succeeds. %s",
                status,
                state.get("plugins_dir") or state.get("root") or "?",
                state.get("detail") or "no detail",
                "The probe process is still exiting (pid {}).".format(state.get("probe_pid"))
                if state.get("probe_lingering")
                else "",
            )
    except Exception as exc:
        logger.warning("Plugin loading failed: %s", exc)


def _bootstrap(*, run_prewarm: bool = True) -> RuntimeServices:
    global _runtime_services
    _runtime_services = bootstrap_runtime_services(
        project_root=PROJECT_ROOT,
        workstation_version=VOOL_WORKSTATION_DEPLOYMENT_VERSION,
        run_prewarm=run_prewarm,
    )
    _sync_runtime_aliases(_runtime_services)
    _load_installed_plugins()
    return _runtime_services


def _daemon_runtime_config(*, capacity: int, local_worker_threads: int):
    return _daemon_runtime_config_impl(capacity=capacity, local_worker_threads=local_worker_threads)


def _ensure_default_provider(registry, model_tag: str) -> None:
    _ensure_default_provider_impl(registry, model_tag)


def _parameter_size_for_model(model_tag: str) -> str:
    return _parameter_size_for_model_impl(model_tag)


def _parameter_count_for_model(model_tag: str) -> int:
    return _parameter_count_for_model_impl(model_tag)


def _normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    return _normalize_chat_history_impl(messages)


def _stable_openclaw_session_id(
    *,
    body: dict[str, Any],
    history: list[dict[str, str]],
    headers: Any,
    allow_canonical_resume: bool = True,
) -> str:
    normalized_headers = dict(headers.items()) if hasattr(headers, "items") else dict(headers or {})
    return _stable_openclaw_session_id_impl(
        body=body,
        history=history,
        headers=normalized_headers,
        allow_canonical_resume=allow_canonical_resume,
    )


def _format_runtime_event_text(event: dict[str, Any]) -> str:
    return _format_runtime_event_text_impl(event)


def _default_workspace_root() -> str:
    return _default_workspace_root_impl()


def _run_agent(
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_agent_impl(
        _compat_runtime_services(),
        user_text,
        session_id=session_id,
        source_context=source_context,
        workspace_root_provider=_default_workspace_root,
    )


def _stream_agent_with_events(
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
    emit_task_events: bool = False,
    ingress_context: contextvars.Context | None = None,
):
    # ingress_context (F-01): the served door captures the A0/A2 binding at ingress and hands the
    # SAME context to every stream provider so commit/finalization runs under the turn's own
    # identity. The apps-server provider lambda historically dropped this kwarg, so every real
    # streamed /api/chat turn through the starlette app raised TypeError and died as a bare 500
    # while the in-process suites (default provider) stayed green — the served UI always streams.
    return _stream_agent_with_events_impl(
        _compat_runtime_services(),
        user_text,
        session_id=session_id,
        source_context=source_context,
        model=model,
        include_runtime_events=include_runtime_events,
        emit_task_events=emit_task_events,
        ingress_context=ingress_context,
        run_agent_provider=lambda runtime, text, *, session_id=None, source_context=None: _run_agent(
            text,
            session_id=session_id,
            source_context=source_context,
        ),
    )


def _dispatch_get(
    *,
    path: str,
    query: dict[str, list[str]],
    runtime: RuntimeServices,
    model_name: str,
    client_host: str = "",
    headers: dict[str, Any] | None = None,
):
    return dispatch_get(
        path=path,
        query=query,
        runtime=runtime,
        model_name=model_name,
        capability_snapshot_provider=runtime_capability_snapshot,
        client_host=client_host,
        headers=headers,
    )


def _dispatch_post(
    *,
    path: str,
    body: dict[str, Any],
    headers: dict[str, Any],
    runtime: RuntimeServices,
    model_name: str,
    workspace_root_provider,
    client_host: str = "",
    request_id: str = "",
):
    return dispatch_post(
        path=path,
        body=body,
        headers=headers,
        runtime=runtime,
        model_name=model_name,
        workspace_root_provider=workspace_root_provider,
        client_host=client_host,
        normalize_chat_history_provider=_normalize_chat_history,
        stable_openclaw_session_id_provider=_stable_openclaw_session_id,
        run_agent_provider=lambda runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None: _run_agent(
            text,
            session_id=session_id,
            source_context=source_context,
        ),
        stream_agent_with_events_provider=lambda runtime, text, *, session_id, source_context, model, include_runtime_events=False, emit_task_events=False, ingress_context=None: _stream_agent_with_events(
            text,
            session_id=session_id,
            source_context=source_context,
            model=model,
            include_runtime_events=include_runtime_events,
            emit_task_events=emit_task_events,
            ingress_context=ingress_context,
        ),
        request_id=request_id,
    )


def create_app(runtime: RuntimeServices | None = None):
    # Council pin ownership is a fact about a live thread in THIS process. Stating the
    # reset at boot is what makes "a persisted round_open run cannot lock chat after a
    # restart" a decision rather than an accident of there being no loader.
    from core.council import pin_lock

    pin_lock.reset_on_startup()
    return create_api_app(
        runtime=runtime or _legacy_handler_runtime(),
        model_name=MODEL_NAME,
        get_dispatcher=_dispatch_get,
        post_dispatcher=_dispatch_post,
        workspace_root_provider=_default_workspace_root,
    )


class VoolAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not host_header_allowed(self.headers.get("Host", "")):
            self._write_response(json_response(403, {"error": "host not allowed"}))
            return
        parsed = urlparse(self.path)
        runtime = _legacy_handler_runtime(getattr(self, "server", None))
        response = _dispatch_get(
            path=parsed.path,
            query=parse_qs(parsed.query),
            runtime=runtime,
            model_name=MODEL_NAME,
            client_host=str(self.client_address[0] if self.client_address else ""),
            headers=dict(self.headers.items()),
        )
        self._write_response(response)

    def do_POST(self) -> None:
        if not host_header_allowed(self.headers.get("Host", "")):
            self._write_response(json_response(403, {"error": "host not allowed"}))
            return
        runtime = _legacy_handler_runtime(getattr(self, "server", None))
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            self._write_response(apply_runtime_headers(json_response(400, {"error": "empty body"}), runtime))
            return
        if is_raw_upload_path(urlparse(self.path).path):
            # Same door as the starlette shell: bytes, not JSON, bounded by the authority's ceiling
            # BEFORE the body is read.
            if content_length > raw_upload_body_ceiling():
                self._write_response(
                    apply_runtime_headers(
                        json_response(413, {"ok": False, "error": "too_large", "message": "The upload is over the attachment size limit."}),
                        runtime,
                    )
                )
                return
            self._write_response(
                dispatch_upload(
                    path=urlparse(self.path).path,
                    raw_body=self.rfile.read(content_length),
                    headers=dict(self.headers.items()),
                    runtime=runtime,
                    client_host=str(self.client_address[0] if self.client_address else ""),
                )
            )
            return
        raw = self.rfile.read(content_length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._write_response(apply_runtime_headers(json_response(400, {"error": "invalid JSON"}), runtime))
            return
        response = _dispatch_post(
            path=self.path,
            body=body,
            headers=dict(self.headers.items()),
            runtime=runtime,
            model_name=MODEL_NAME,
            workspace_root_provider=_default_workspace_root,
            client_host=str(
                self.client_address[0] if self.client_address else ""
            ),
        )
        self._write_response(response)

    def _write_response(self, response) -> None:
        self.send_response(int(response.status))
        self.send_header("Content-Type", str(response.content_type))
        for header, value in dict(response.headers or {}).items():
            self.send_header(str(header), str(value))
        if response.stream is None:
            payload = response.body or b""
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.end_headers()
        try:
            for chunk in response.stream:
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except BrokenPipeError:
                    break
        finally:
            close = getattr(response.stream, "close", None)
            if callable(close):
                close()

    def log_message(self, format: str, *args: Any) -> None:
        return


def _pidfile_path():
    from core.runtime_paths import active_data_dir

    return active_data_dir() / "vool_api.pid"


def _write_pidfile() -> None:
    # Lets the self-updater stop this exact process cleanly before swapping code.
    with contextlib.suppress(Exception):
        import os

        pf = _pidfile_path()
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(str(os.getpid()), encoding="utf-8")


def _remove_pidfile() -> None:
    with contextlib.suppress(Exception):
        _pidfile_path().unlink(missing_ok=True)


def _start_background_prewarm(runtime: RuntimeServices) -> None:
    """Warm providers on a background thread AFTER the socket is bound.

    Binding 11435 before the multi-minute provider prewarm is what stops OpenClaw (pinned to
    vool/vool) from getting ECONNREFUSED on the port while VOOL is still starting: /healthz
    answers immediately and a request that arrives during warmup lazy-loads the model instead of
    failing. Best-effort -- a prewarm error is logged and never crashes the server.
    """
    # HARD STOP (operator rule, 2026-08-28): a marker file kills every boot-time local-model
    # load. On a 24GB host the prewarm ladder cold-loaded 18-30GB models back to back and
    # OOM-froze the machine; with the marker present the runtime warms NOTHING locally and
    # models load only if a turn is explicitly routed to one. The canonical LocalModelPolicy now
    # owns this decision — the marker file and VOOL_LOCAL_MODELS_ENABLED are the same kill-switch.
    try:
        from core.local_model_policy import resolve_local_model_policy

        if resolve_local_model_policy().local_models_disabled:
            logger.info(
                "Local-model prewarm disabled by policy (%s)",
                resolve_local_model_policy().decided_by,
            )
            return
    except Exception:
        pass
    # Warm the tiny intent-arbiter model too (fail-soft, own thread): a cold 0.6b load measured
    # ~8.6s, so prewarming means a user's first ambiguous turn arbitrates in ~300ms instead.
    try:
        from core.intent_arbiter import prewarm_async

        prewarm_async()
    except Exception:
        pass

    prewarm = getattr(runtime, "deferred_prewarm", None)
    if prewarm is None:
        return

    def _worker() -> None:
        try:
            prewarm()
        except Exception as exc:
            logger.warning("Background provider prewarm failed: %s", exc)

    threading.Thread(target=_worker, name="vool-provider-prewarm", daemon=True).start()


def _start_parent_death_watch(server: Any) -> None:
    """Orphan defense for app-owned runtimes: when the native window host that spawned us dies,
    this daemon must not outlive it as a headless listener on 11435. macOS has no PR_SET_PDEATHSIG,
    so the child polls its own parentage instead — a dead parent is reaped and getppid() changes
    (launchd adopts us). Armed ONLY when the app supervisor spawned us (VOOL_OWNED_BY_WINDOW_PID
    names a real pid); updater relaunches and manual runs never carry the marker."""
    import os
    import time

    raw = str(os.environ.get("VOOL_OWNED_BY_WINDOW_PID") or "").strip()
    if not raw.isdigit():
        return
    parent = int(raw)
    if parent <= 1:
        return

    def _watch() -> None:
        while not getattr(server, "should_exit", False):
            if os.getppid() != parent:
                logger.warning(
                    "owning window host pid=%s is gone; stopping so no daemon outlives its window", parent
                )
                server.should_exit = True
                return
            time.sleep(1.0)

    threading.Thread(target=_watch, name="vool-parent-watch", daemon=True).start()


def main() -> int:
    # Diagnosability law (F46): a served turn spent a measured ~97 s idle between
    # task_classified and model_routing — no events, no model call, no network — and the
    # process sample showed a bare condition-variable wait nothing could attribute.
    # SIGUSR1 now dumps every thread's Python stack to stderr, so the next silent stall
    # is one signal away from a named frame. Registered before anything can block.
    import faulthandler
    import signal

    faulthandler.register(signal.SIGUSR1, all_threads=True, file=sys.stderr)
    # C15: the ONE bounded unattended preflight, before any bootstrap can reach an OS
    # credential API. A scratch/synthetic home gets non-interactive storage pinned; the
    # operator's established home is recorded, never rewritten.
    from core.unattended_preflight import preflight

    preflight("apps.vool_api_server")
    # Environment conformance (P0 release proof 2026-09-05): an under-installed
    # runtime must fail HERE — typed, naming the missing module and the repair —
    # never as a mid-request HTTP 500 from the command registry's import closure
    # (observed: POST /api/memory/forget 500'd on a missing zstandard while
    # /healthz served green). No escape hatch: fail closed before serving.
    from core.runtime_dependency_preflight import preflight_served_imports_or_exit

    preflight_served_imports_or_exit()
    parser = argparse.ArgumentParser(prog="vool-api-server")
    parser.add_argument("--port", type=int, default=VOOL_API_PORT)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger.info("Bootstrapping VOOL runtime...")
    runtime: RuntimeServices | None = None
    try:
        # Defer provider prewarm so the port binds first: this is the fix for OpenClaw getting
        # ECONNREFUSED on 11435 while VOOL is still warming models after boot.
        from core.bounded_keyring import native_bootstrap_authorization

        with native_bootstrap_authorization(os.environ.pop("VOOL_NATIVE_AUTHORIZATION_DEADLINE", None)):
            runtime = _bootstrap(run_prewarm=False)
        # _bootstrap ran setup_logging (console/stderr). Route runtime logs to a file now, so a
        # parent that captures our stdout/stderr without draining it cannot fill the pipe and block
        # the request path (Windows ~64KB pipe-buffer deadlock). Best-effort.
        with contextlib.suppress(Exception):
            from core.logging_config import route_logging_to_file
            from core.runtime_paths import active_data_dir

            route_logging_to_file(active_data_dir() / "logs" / "vool_api.log")
        app = create_app(runtime)
        import uvicorn

        _write_pidfile()
        # Signed-manifest update subsystem (2026-09-01 amendment): bounded, async, and
        # honestly UNAVAILABLE when no publisher key / feed is configured. Never blocks
        # boot, never raises into the server.
        try:
            from core.updater.runtime import boot_update_subsystem

            boot_update_subsystem()
        except Exception:
            logger.exception("update subsystem boot failed; server continues without it")
        # Operator Profile (P1): the one-time move of the legacy Settings name/signature fields
        # into the profile authority happens here at boot, never silently on a read.
        try:
            from core.user_preferences import migrate_legacy_profile_fields

            migrate_legacy_profile_fields()
        except Exception:
            logger.exception("legacy profile field migration failed; server continues without it")
        # Scheduling vertical: the due-reminder dispatcher. One daemon thread; its first sweep
        # runs immediately so reminders that came due while the process was down are delivered
        # late (visibly) instead of silently dropped. Fail-soft: boot never depends on it.
        try:
            from core.operator.reminder_dispatcher import start_default_dispatcher

            start_default_dispatcher()
        except Exception:
            logger.exception("reminder dispatcher failed to start; server continues without scheduled delivery")
        # Calendar alerts: the bounded background sync of opted-in calendars (a few accounts per tick, each
        # refreshed every 10 minutes, never on chat turns). Fail-soft like the dispatcher.
        try:
            from core.operator.calendar_alerts import start_default_sync

            start_default_sync()
        except Exception:
            logger.exception("calendar alert sync failed to start; server continues without calendar alerts")
        _start_background_prewarm(runtime)
        # Crypto Pilot: the transfer observer reads the chain for transfers whose outcome is not settled yet and
        # recovers claims whose request died. One daemon thread; it never transmits. Fail-soft: boot never depends on it.
        try:
            from core.wallet import settlement as wallet_settlement

            wallet_settlement.start_observer()
        except Exception:
            logger.exception("wallet transfer observer failed to start; server continues without it")
        # UsePod accountless x402: the Crypto Pilot wallet is the payment authority. Installed once, under its own label;
        # the networks it can pay on are recomputed on every read (Crypto switch, environment, a ready pilot wallet, the
        # row's proven genesis). Fail-soft: boot never depends on it, and x402 turns refuse at the pick without it.
        try:
            from core.wallet import usepod_x402

            usepod_x402.install_at_boot()
        except Exception:
            logger.exception("UsePod x402 payment authority failed to install; x402 turns refuse without it")
        # the browser lane and web.fetch never open this runtime's own service (core.runtime_served_origins)
        from core.runtime_served_origins import register_served_port

        register_served_port(int(args.port))
        logger.info("VOOL API listening on http://%s:%s", args.bind, args.port)
        logger.info("OpenClaw can connect to this as an Ollama provider.")
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=str(args.bind),
                port=int(args.port),
                access_log=False,
                log_level="info",
                # Don't let uvicorn install its own stdout/stderr handlers; let its logs propagate
                # to the root logger (routed to the file above), off the undrained parent pipe.
                log_config=None,
            )
        )
        _start_parent_death_watch(server)
        server.run()
    finally:
        _remove_pidfile()
        try:
            from core.operator.reminder_dispatcher import stop_default_dispatcher

            stop_default_dispatcher()
        except Exception:
            pass
        try:
            from core.operator.calendar_alerts import stop_default_sync

            stop_default_sync()
        except Exception:
            pass
        # The wallet transfer observer is the third boot-time service this entrypoint owns
        # (started fail-soft above). Without this stop, an in-process host of main() — every
        # test that exercises the real server lifecycle — kept the observer ticking for the
        # rest of the process, and each tick's wallet-store access brought its schema onto
        # whatever database the runtime-continuity authority then pointed at; a per-test
        # database switched underneath it mid-statement surfaced as
        # sqlite3.OperationalError("database schema has changed") in unrelated suites.
        # The import mirrors the start site's exact form: `from core.wallet import
        # settlement` resolves through the package even when a host's sys.modules churn has
        # left the submodule key absent, so start and stop always share one module instance.
        try:
            from core.wallet import settlement as wallet_settlement

            wallet_settlement.stop_observer()
        except Exception:
            pass
        if runtime is not None:
            runtime.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
