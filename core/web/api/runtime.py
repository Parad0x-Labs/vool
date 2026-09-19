from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import queue
import re
import secrets
import subprocess
import threading
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import policy_engine
from core.agent_runtime.agent import VoolAgent
from core.agent_runtime.daemon import VoolDaemon
from core.app_version import VOOL_VERSION
from core.chat_session_identity import fold_chat_session_id, is_canonical_chat_session_id
from core.compute_mode import ComputeModeDaemon
from core.daemon import DaemonConfig
from core.error_surface import safe_error_text
from core.hardware_tier import probe_machine
from core.identity_manager import load_active_persona
from core.local_inference_autopilot import select_daily_residency_model_tag
from core.local_inference_evidence import hydrate_capability_truth_with_benchmarks
from core.local_model_policy import local_models_enabled
from core.local_ollama_inventory import (
    installed_ollama_model_inventory,
    installed_ollama_model_names,
    is_text_generation_ollama_model,
)
from core.local_worker_pool import resolve_local_worker_capacity

# The ONE definition of the private-memory recall predicate lives here so the whole-turn
# arbitration (`core.agent_runtime.answer_coverage`) can register the family and every consumer
# reads the same detector. The aliases below keep this module's call sites byte-identical.
from core.memory_recall_intent import (
    looks_like_private_memory_recall as _looks_like_private_memory_recall,
)
from core.model_registry import ModelRegistry
from core.onboarding import (
    ensure_bootstrap_identity,
    ensure_openclaw_registration,
    get_agent_display_name,
    is_first_boot,
)
from core.provider_env import merge_provider_env
from core.public_hive_bridge import ensure_public_hive_auth
from core.release_channel import release_manifest_snapshot
from core.runtime_backbone import build_provider_registry_snapshot
from core.runtime_bootstrap import bootstrap_runtime_mode
from core.runtime_install_profiles import (
    active_install_profile_id,
    installed_profile_selected_model,
    required_ollama_models_for_profile,
)
from core.runtime_paths import active_config_home_dir, resolve_workspace_root
from core.runtime_provider_defaults import default_runtime_model_tag, ensure_default_runtime_providers
from core.runtime_task_events import (
    new_runtime_event_stream_id,
    register_runtime_event_sink,
    unregister_runtime_event_sink,
)
from core.task_event_model import build_task_event
from core.web.api.response_control import apply_exact_response_control
from network.signer import get_local_peer_id

logger = logging.getLogger("vool.api")

MODEL_NAME = "vool"
BUILD_SOURCE_PATH = Path("config") / "build-source.json"
# Bounds a STALLED pull between reads, not the whole download: a multi-GB model on a slow link must
# not be killed part-way, but a dead socket must not hang the prewarm thread forever.
_OLLAMA_PULL_READ_TIMEOUT_SECONDS = 120.0
_MODEL_PULL_WAIT_SECONDS = 1800.0
_OPENCLAW_SENDER_WRAPPER_RE = re.compile(
    r"^Sender \(untrusted metadata\):\s*```json\s*\{.*?\}\s*```\s*\[[^\]]+\]\s*(.*)$",
    re.DOTALL,
)
# When a user sends a message while VOOL is still answering the previous one, the
# OpenClaw gateway does not deliver it on its own - it prepends this literal marker
# line and concatenates the new message ahead of the still-in-flight prompt. Kept in
# sync with QUEUED_USER_MESSAGE_MARKER in OpenClaw's dist/attempt.prompt-helpers.
_OPENCLAW_QUEUED_TURN_MARKER = "[Queued user message that arrived while the previous turn was still active]"
# OpenClaw also stamps merged/queued turns with a leading "[<weekday> <date> <time> GMT+N]"
# bracket. A human never opens a chat message with a GMT-stamped bracket, so a leading
# one is always machine scaffolding and safe to drop.
_OPENCLAW_TS_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]*\bGMT\b[^\]]*\]\s*)+", re.IGNORECASE)


@dataclass
class RuntimeServices:
    agent: VoolAgent | None = None
    daemon: VoolDaemon | None = None
    display_name: str = "VOOL"
    runtime_model_tag: str = field(default_factory=default_runtime_model_tag)
    runtime_parameter_size: str = field(
        default_factory=lambda: parameter_size_for_model(default_runtime_model_tag())
    )
    runtime_started_at: str = ""
    runtime_home: str = ""
    runtime_version_stamp: dict[str, Any] = field(default_factory=dict)
    public_hive_auth: dict[str, Any] = field(default_factory=dict)
    provider_capability_truth: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # Set when bootstrap was asked to DEFER provider prewarm (run_prewarm=False): the API server
    # binds the port first, then runs this on a background thread. None when prewarm ran inline.
    deferred_prewarm: Callable[[], None] | None = None
    # Live status of the first-run model pull, which runs on its own thread while the port binds.
    model_pull: ModelPullProgress | None = None

    def shutdown(self) -> None:
        # Background lanes first: the idle-commons thread (and the curiosity consumer on it)
        # reads the stop flag as its cancellation signal, so an in-flight topic is re-queued
        # rather than orphaned as `running`.
        agent = self.agent
        stop = getattr(agent, "stop_background_runtime_threads", None) if agent is not None else None
        if callable(stop):
            with contextlib.suppress(Exception):
                stop()
        if self.daemon:
            self.daemon.stop()


def default_agent_source_context() -> dict[str, Any]:
    """Return the context every ordinary web/API chat turn starts with.

    This is a production contract rather than a source-code shape. Callers receive a fresh mapping
    because request-specific context is merged into it before the agent boundary.

    INGRESS WAIVER — this module (with the /api/chat dispatch in
    core/web/api/service.py) is the declared compatibility lane of the canonical
    KAS→VOOL channel ingress contract (core/channel_gateway.process_channel_request;
    live-lane classification L2/L3). Its guarantees map onto the canonical ones as:
      * remote deny: the chat dispatch derives OWNER_LOCAL_KEY =
        loopback AND surface != "channel" — surface="channel" here can only ever
        DENY owner privileges, mirroring the gateway's explicit False stamp;
      * result identity: replies seal through `_response_commit` → finalize_answer
        (A7, the sole minting authority); committed bytes only on the wire,
        HASH-LAST re-hash per serve;
      * difference from canonical: per-platform framing/truncation policy is NOT
        applied by this lane; there is no second gateway and no second execution
        authority — external bridges using /api/chat are shims to THIS contract.
    """

    return {
        "surface": "channel",
        "platform": "openclaw",
        # Deliberately NOT setting allow_remote_fetch here: several downstream
        # checks (fast_live_info_runtime_preflight._explicit_remote_fetch_disabled,
        # research_tool_loop_facade, curiosity_roamer) treat an *explicit*
        # allow_remote_fetch key in source_context as a caller override that takes
        # precedence over the ambient policy_engine.allow_web_fallback() check.
        # Injecting the ambient policy value here unconditionally made every
        # OpenClaw chat turn look like it had an explicit override, which
        # short-circuited past the honest "live lookup is disabled" message and
        # silently fell through to the LLM hallucinating an answer with no data.
        # Leaving the key absent lets those checks fall back to their own
        # trusted-surface heuristic and lets policy_engine be the single source
        # of truth for the actual allow/deny decision.
    }


def git_output(project_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except Exception:
        return ""
    return str(completed.stdout or "").strip()


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def build_source_metadata(project_root: Path) -> dict[str, Any]:
    metadata_path = project_root / BUILD_SOURCE_PATH
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    metadata: dict[str, Any] = {}
    for key in ("ref", "branch", "commit", "commit_full", "source_url", "source_kind"):
        value = str(payload.get(key) or "").strip()
        if value:
            metadata[key] = value
    dirty_state = _coerce_optional_bool(payload.get("dirty_state"))
    if dirty_state is not None:
        metadata["dirty_state"] = dirty_state
    return metadata


def git_checkout_state(project_root: Path) -> dict[str, Any]:
    commit_full = git_output(project_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", commit_full):
        return {"valid": False, "branch": "", "commit": "", "commit_full": "", "dirty": False}
    branch = git_output(project_root, "branch", "--show-current")
    short_commit = git_output(project_root, "rev-parse", "--short=12", "HEAD") or commit_full[:12]
    dirty = bool(git_output(project_root, "status", "--short"))
    return {
        "valid": True,
        "branch": branch,
        "commit": short_commit,
        # The unabbreviated SHA. The short commit alone cannot be pasted into a `git show` against a
        # repo that abbreviates differently, and the About panel has to report the exact build.
        "commit_full": commit_full,
        "dirty": dirty,
    }


def env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid integer env override %s=%r", name, raw)
        return int(default)


def env_text(name: str, default: str) -> str:
    return str(os.environ.get(name, default) or default).strip() or str(default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def daemon_runtime_config(*, capacity: int, local_worker_threads: int) -> DaemonConfig:
    return DaemonConfig(
        bind_host=env_text("VOOL_DAEMON_BIND_HOST", "0.0.0.0"),
        bind_port=env_int("VOOL_DAEMON_BIND_PORT", 49152),
        advertise_host=env_text("VOOL_DAEMON_ADVERTISE_HOST", "127.0.0.1"),
        health_bind_host=env_text("VOOL_DAEMON_HEALTH_BIND_HOST", "127.0.0.1"),
        health_bind_port=max(0, env_int("VOOL_DAEMON_HEALTH_PORT", 0)),
        capacity=int(capacity),
        local_worker_threads=max(2, int(local_worker_threads)),
    )


def parameter_size_for_model(model_tag: str) -> str:
    model_name = str(model_tag or "").strip().split("/", 1)[-1]
    if ":" not in model_name:
        return "7B"
    _, size = model_name.rsplit(":", 1)
    return size.upper()


def parameter_count_for_model(model_tag: str) -> int:
    label = parameter_size_for_model(model_tag).rstrip("B")
    try:
        return int(float(label) * 1_000_000_000)
    except ValueError:
        return 7_000_000_000


def _platform_release_tag() -> str:
    """Short platform marker for the build id, so an Apple/Linux build is distinguishable from the
    Windows .exe at runtime. Both lanes build from the same commit, so `release+commit` alone would
    be identical across platforms; the tag is detected here, needing no per-lane config edit."""
    import sys

    plat = sys.platform
    if plat == "darwin":
        return "macos"
    if plat.startswith("win"):
        return "windows"
    if plat.startswith("linux"):
        return "linux"
    return "".join(ch for ch in plat if ch.isalnum())[:12]


def build_runtime_version_stamp(*, project_root: Path, runtime_model_tag: str, workstation_version: str) -> dict[str, Any]:
    release = dict(release_manifest_snapshot())
    build_source = build_source_metadata(project_root)
    git_state = git_checkout_state(project_root)
    # A BAKED STAMP IS THE ARTIFACT'S OWN IDENTITY AND OUTRANKS ANY GIT QUERY.
    #
    # This used to prefer git_state whenever it was "valid", on the stated assumption that "a
    # packaged install has no .git". That assumption is false exactly where it matters: a bundle
    # is built into dist/ INSIDE the worktree that produced it, so `git rev-parse HEAD` run from
    # the bundle walks UP and answers about the enclosing checkout. Measured: a bundle baking
    # 9bb9f6920891 in build-source.json, Info.plist NULLASourceSHA and BUILD_MANIFEST.json served
    # /healthz commit_full 9b05dae8807e -- the worktree's live HEAD two commits later -- and would
    # have reported the developer's uncommitted edits as the ARTIFACT's dirty flag.
    #
    # That makes every packaged identity check vacuous while the bundle is tested in-tree, which
    # is the only place it is ever tested before release. config/build-source.json is gitignored
    # and written only by the bundle build, so its presence IS the "I am a packaged artifact"
    # signal: when it names a commit, that commit is the answer and the enclosing repo is not
    # this artifact's repo. A source checkout has no such file and keeps git state exactly as
    # before.
    packaged_commit = str(build_source.get("commit_full") or build_source.get("commit") or "").strip()
    if packaged_commit:
        branch = str(build_source.get("branch") or build_source.get("ref") or "")
        commit_full = str(build_source.get("commit_full") or "").strip()
        if not commit_full and re.fullmatch(r"[0-9a-f]{40}", packaged_commit):
            commit_full = packaged_commit
        commit = (commit_full or packaged_commit)[:12]
        dirty = bool(build_source.get("dirty_state"))
    elif bool(git_state.get("valid")):
        branch = str(git_state.get("branch") or build_source.get("branch") or build_source.get("ref") or "")
        commit = str(git_state.get("commit") or "").strip()
        commit_full = str(git_state.get("commit_full") or "").strip()
        dirty = bool(git_state.get("dirty"))
    else:
        branch = str(build_source.get("branch") or build_source.get("ref") or "")
        recorded_commit = str(build_source.get("commit") or "").strip()
        commit = recorded_commit[:12]
        # A packaged install has no .git, so the full SHA comes from config/build-source.json --
        # either its explicit commit_full, or its `commit` when that was already written unabbreviated.
        commit_full = str(build_source.get("commit_full") or "").strip()
        if not commit_full and re.fullmatch(r"[0-9a-f]{40}", recorded_commit):
            commit_full = recorded_commit
        dirty = bool(build_source.get("dirty_state"))
    release_version = str(release.get("release_version") or "").strip() or "unknown-release"
    platform_tag = _platform_release_tag()
    build_parts = [release_version]
    if platform_tag:
        build_parts.append(platform_tag)
    if commit:
        build_parts.append(commit)
    build_id = "+".join(build_parts)
    if dirty:
        build_id = f"{build_id}.dirty"
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "app_version": VOOL_VERSION,
        "release_version": release_version,
        "minimum_compatible_release": str(release.get("minimum_compatible_release") or "").strip(),
        "protocol_version": int(release.get("protocol_version") or 0),
        "rollout_stage": str(release.get("rollout_stage") or "").strip(),
        "channel_name": str(release.get("channel_name") or "").strip(),
        "branch": branch,
        "commit": commit,
        "commit_full": commit_full,
        "dirty": dirty,
        "platform": platform_tag,
        "build_id": build_id,
        "started_at": started_at,
        "pid": os.getpid(),
        "workstation_version": workstation_version,
        # The boot/pull target is a default input to Auto routing, not per-turn execution truth.
        # Keep `model_tag` as a compatibility alias while giving new consumers an honest label.
        "default_model_tag": runtime_model_tag,
        "model_tag": runtime_model_tag,
    }


class ModelPullProgress:
    """Live status of the first-run model pull.

    ``public`` is handed to the runtime version stamp BY REFERENCE and mutated in place, because
    /healthz shallow-copies the stamp per request: readers see the current percentage while the
    pull is still running, instead of a snapshot frozen at boot.
    """

    def __init__(self, model_tag: str) -> None:
        self._done = threading.Event()
        self.public: dict[str, Any] = {
            "model": str(model_tag or ""),
            "status": "starting",
            "percent": 0,
            "detail": "",
        }

    def update(self, *, status: str | None = None, percent: int | None = None, detail: str | None = None) -> None:
        if status is not None:
            self.public["status"] = str(status)
        if percent is not None:
            self.public["percent"] = max(0, min(100, int(percent)))
        if detail is not None:
            self.public["detail"] = str(detail)[:200]

    def finish(self) -> None:
        self._done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    @property
    def status(self) -> str:
        return str(self.public.get("status") or "")


def normalized_ollama_tag(model_tag: str) -> str:
    clean = str(model_tag or "").strip()
    if not clean:
        return ""
    return clean if ":" in clean.split("/")[-1] else f"{clean}:latest"


def ollama_base_url(env: dict[str, str] | None = None) -> str:
    """The one resolution of the local Ollama endpoint; see core.ollama_endpoint."""
    from core.ollama_endpoint import ollama_base_url as _resolve

    return _resolve(env)


def candidate_aware_daily_runtime_model_tag(
    configured_model_tag: str,
    *,
    env: dict[str, str],
    selection_pinned: bool = False,
    probe: Any | None = None,
) -> str:
    """Resolve boot's primary resident model from installed daily-Auto candidates.

    The installer/legacy profile model is a recommendation, not proof that the current daily Auto
    policy will use it. Bootstrap can cheaply inspect the same bounded local Ollama inventory the
    router will see; manifest-registration policy must not suppress that runtime truth. Reusing the
    daily policy here keeps pull, prewarm, the boot log, and ``default_model_tag`` aligned with the
    likely ordinary answer.

    A real operator pin is never replaced.  Discovery failure is fail-open to the configured tag,
    and the selected result must come verbatim from the installed inventory, so boot cannot claim
    a reliable fallback that is not actually available.
    """

    configured = str(configured_model_tag or "").strip()
    if selection_pinned:
        return configured
    # The canonical LocalModelPolicy: disabled means no Ollama inventory discovery even here —
    # this alignment exists to pick between LOCAL candidates, and there are none to pick.
    if not local_models_enabled(env=env):
        return configured
    try:
        installed = tuple(
            item
            for item in installed_ollama_model_inventory(
                env=env,
                base_url=ollama_base_url(env),
                timeout_seconds=2.0,
            )
            if is_text_generation_ollama_model(item.name)
        )
    except Exception:
        return configured
    if not installed:
        return configured
    try:
        selected = select_daily_residency_model_tag(
            [item.name for item in installed],
            default_model_tag=configured,
            resident_footprint_gb={
                item.name: float(item.size_bytes) / (1024.0 ** 3)
                for item in installed
                if item.size_bytes > 0
            },
            probe=probe,
        )
    except Exception:
        return configured
    installed_by_key = {item.name.strip().lower(): item.name.strip() for item in installed}
    return installed_by_key.get(str(selected).strip().lower(), configured)


def ollama_model_installed(model_tag: str, *, env: dict[str, str] | None = None) -> bool:
    """Exact-tag lookup against Ollama's /api/tags inventory.

    A substring match on `ollama list` stdout is wrong in both directions: `qwen3:4b` also matches
    an unrelated `qwen3:4b-instruct` row and skips a pull the runtime needs, while a bare `qwen3`
    names the `qwen3:latest` row it never compares equal to.
    """
    wanted = normalized_ollama_tag(model_tag)
    if not wanted:
        return False
    installed = {
        normalized_ollama_tag(name)
        for name in installed_ollama_model_names(env=env, base_url=ollama_base_url(env), timeout_seconds=5.0)
    }
    return wanted in installed


def _stream_ollama_pull(model_tag: str, *, progress: ModelPullProgress) -> None:
    """Pull over Ollama's HTTP API, reporting real progress.

    HTTP rather than `ollama pull`: the CLI's output is the only progress signal and this process
    has no console to stream it to, so it was captured and discarded. The per-read timeout bounds a
    STALLED download without capping the total, so a slow large pull is never killed mid-way.
    """
    body = json.dumps({"model": model_tag, "stream": True}).encode("utf-8")
    request = urllib.request.Request(
        f"{ollama_base_url()}/api/pull",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_OLLAMA_PULL_READ_TIMEOUT_SECONDS) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            error = str(event.get("error") or "").strip()
            if error:
                raise RuntimeError(error)
            total = event.get("total")
            completed = event.get("completed")
            percent = None
            if isinstance(total, (int, float)) and isinstance(completed, (int, float)) and total > 0:
                percent = int(completed / total * 100)
            progress.update(status="pulling", percent=percent, detail=str(event.get("status") or ""))


def ensure_ollama_model(model_tag: str | None = None, *, progress: ModelPullProgress | None = None) -> None:
    active_model = str(model_tag or "").strip() or default_runtime_model_tag()
    tracker = progress if progress is not None else ModelPullProgress(active_model)
    if ollama_model_installed(active_model):
        tracker.update(status="ready", percent=100, detail="already installed")
        return
    logger.info(
        "Ollama model '%s' missing — pulling in the background (this may take a few minutes on first run)...",
        active_model,
    )
    tracker.update(status="pulling", percent=0, detail="starting download")
    try:
        _stream_ollama_pull(active_model, progress=tracker)
    except Exception as exc:
        tracker.update(status="failed", detail=str(exc))
        logger.warning(
            "Failed to pull Ollama model '%s': %s — LLM responses will fall back to planning mode.",
            active_model,
            exc,
        )
        return
    tracker.update(status="ready", percent=100, detail="pull complete")
    logger.info("Ollama model '%s' pulled successfully.", active_model)


def start_ollama_model_pull(model_tag: str | None = None) -> ModelPullProgress:
    """Run the first-run pull on a background thread and return its live progress immediately.

    A pull of a multi-GB model in front of the listener is what left the first-run window showing
    ERR_CONNECTION_REFUSED for minutes: nothing bound the port until the download finished. The
    caller binds first and reports this progress instead.

    Under the canonical LocalModelPolicy's disabled mode this is a typed no-op: no thread, no
    Ollama traffic, no local model download — boot of a cloud-only runtime must never pull.
    """
    active_model = str(model_tag or "").strip() or default_runtime_model_tag()
    progress = ModelPullProgress(active_model)
    if not local_models_enabled():
        progress.update(status="skipped", detail="local models disabled by policy")
        progress.finish()
        return progress

    def _worker() -> None:
        try:
            ensure_ollama_model(active_model, progress=progress)
        except Exception as exc:
            progress.update(status="failed", detail=str(exc))
            logger.warning("Background pull of Ollama model '%s' failed: %s", active_model, exc)
        finally:
            progress.finish()

    threading.Thread(target=_worker, name="vool-ollama-model-pull", daemon=True).start()
    return progress


def ensure_default_provider(
    registry: ModelRegistry,
    model_tag: str,
    *,
    env: dict[str, str] | None = None,
    install_profile: str | None = None,
    runtime_home: str | None = None,
) -> None:
    for provider_id in ensure_default_runtime_providers(
        registry,
        model_tag=model_tag,
        env=env,
        install_profile=install_profile,
        runtime_home=runtime_home,
    ):
        logger.info("Auto-registered default provider: %s", provider_id)


def log_prewarm_results(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
) -> None:
    if str(os.environ.get("VOOL_SKIP_PROVIDER_PREWARM") or "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.info("Provider prewarm skipped by VOOL_SKIP_PROVIDER_PREWARM.")
        return
    try:
        snapshot = build_provider_registry_snapshot(
            registry,
            model_tag=model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
            honor_install_profile=bool(requested_profile or runtime_home),
            run_prewarm=True,
        )
        raw_results = snapshot.prewarm_results
    except Exception as exc:
        logger.warning("Provider prewarm enumeration failed: %s", exc)
        return
    if not isinstance(raw_results, (list, tuple)):
        return
    for result in raw_results:
        provider_id = str(result.get("provider_id") or "unknown-provider")
        status = str(result.get("status") or "unknown").strip() or "unknown"
        if result.get("ok") and status == "prewarmed":
            logger.info(
                "Provider prewarmed: %s | keep_alive=%s | load_duration=%s | total_duration=%s",
                provider_id,
                result.get("keep_alive"),
                result.get("load_duration"),
                result.get("total_duration"),
            )
            continue
        if result.get("ok") and status == "timed_out":
            logger.info(
                "Provider prewarm timed out; continuing without background warming: %s | reason=%s | keep_alive=%s | timeout_seconds=%s",
                provider_id,
                result.get("reason") or "unspecified",
                result.get("keep_alive"),
                result.get("timeout_seconds"),
            )
            continue
        if result.get("ok"):
            logger.info(
                "Provider prewarm skipped: %s | status=%s | reason=%s",
                provider_id,
                status,
                result.get("reason") or "unspecified",
            )
            continue
        logger.warning(
            "Provider prewarm failed: %s | status=%s | error=%s",
            provider_id,
            status,
            result.get("error") or "unknown_error",
        )


def startup_provider_capability_truth(
    registry: ModelRegistry,
    *,
    model_tag: str | None = None,
    runtime_home: str | None = None,
    requested_profile: str | None = None,
    env: dict[str, str] | None = None,
    model_pull_status: str = "",
    model_pull_detail: str = "",
) -> tuple[dict[str, Any], ...]:
    try:
        snapshot = build_provider_registry_snapshot(
            registry,
            model_tag=model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
            honor_install_profile=bool(requested_profile or runtime_home),
            env=env,
        )
    except Exception as exc:
        logger.warning("Startup provider capability snapshot failed: %s", exc)
        return tuple()
    capabilities = hydrate_capability_truth_with_benchmarks(snapshot.capability_truth)
    pull_status = str(model_pull_status or "").strip().lower()
    if pull_status in {"starting", "pulling", "failed", "error"}:
        availability = "blocked" if pull_status in {"failed", "error"} else "degraded"
        detail = str(model_pull_detail or pull_status).strip()[:240]
        capabilities = tuple(
            replace(
                item,
                availability_state=availability,
                last_error=detail or item.last_error,
                measurement_source="model_pull_status",
            )
            if str(item.provider_id or "").startswith("ollama-local:")
            else item
            for item in capabilities
        )
    return tuple(item.to_dict() for item in capabilities)


def public_hive_auth_snapshot(auth_result: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(auth_result or {})
    status = str(payload.get("status") or "unknown").strip() or "unknown"
    snapshot: dict[str, Any] = {
        "ok": bool(payload.get("ok")),
        "status": status,
    }
    requires_auth = payload.get("requires_auth")
    if requires_auth is not None:
        snapshot["requires_auth"] = bool(requires_auth)
    watch_host = str(payload.get("watch_host") or "").strip()
    if watch_host:
        snapshot["watch_host"] = watch_host
    suggested_remote_config_path = str(payload.get("suggested_remote_config_path") or "").strip()
    if suggested_remote_config_path:
        snapshot["remote_config_path"] = suggested_remote_config_path
    suggested_command = str(payload.get("suggested_command") or "").strip()
    if suggested_command:
        snapshot["next_step"] = suggested_command
    return snapshot


def bootstrap_runtime_services(
    *, project_root: Path, workstation_version: str, run_prewarm: bool = True
) -> RuntimeServices:
    boot = bootstrap_runtime_mode(
        mode="api_server",
        workspace_root=resolve_workspace_root(),
        force_policy_reload=True,
        configure_logging=True,
        resolve_backend=True,
    )

    # Explicit capability-graph bootstrap: index every registry contract so
    # model-visible tool discovery is family-true from the first turn instead
    # of lazily on the first query. Idempotent; raises loudly (never boots
    # silently empty) if the registry maps to zero implementations.
    from core.capability_graph import bootstrap_from_registry

    indexed_tool_contracts = bootstrap_from_registry()
    logger.info(
        "Capability graph bootstrapped: %d registry contracts indexed",
        indexed_tool_contracts,
    )

    if is_first_boot():
        ensure_bootstrap_identity(
            default_agent_name="VOOL",
            privacy_pact="Store memory locally by default. Never share secrets or personal identity without explicit approval.",
        )
    # First-Run Pact boot seed: idempotent, file-only, never blocks boot (LP1/LP6).
    # A failure here is logged and skipped — the card degrades to a typed 404 and the
    # page re-polls; boot itself is never held for onboarding.
    try:
        from core import first_run_pact as _first_run_pact

        _pact_seed = _first_run_pact.seed()
        logger.info(
            "First-run pact seeded: state=%s seed_reason=%s",
            _pact_seed.get("state"),
            _pact_seed.get("seed_reason"),
        )
    except Exception as exc:
        logger.warning("First-run pact seed skipped (boot continues): %s", type(exc).__name__)
    # Credential recovery at restart (revision-5 review R3): an operation a failed or interrupted
    # Settings Save left pending is decided from its durable journal record HERE — before the provider
    # lanes below register against the committed pair — so a restart recovers without a new paste.
    # Runs only when recovery work exists; a reconcile that cannot finish is logged and boot continues
    # with the pending intent still owning recovery.
    try:
        from core.credential_intelligence.store import recover_pending_operations

        _credential_recovery = recover_pending_operations()
        if _credential_recovery is not None:
            logger.info(
                "Credential recovery at boot: adopted=%s conflicts=%s unresolved=%s deletions_completed=%s",
                _credential_recovery.adopted,
                _credential_recovery.conflicts,
                _credential_recovery.unresolved,
                _credential_recovery.deletions_completed,
            )
    except Exception as exc:
        logger.warning(
            "Credential recovery at boot did not complete (boot continues; the operation stays pending): %s",
            type(exc).__name__,
        )

    peer_id = get_local_peer_id()
    from core.credit_ledger import ensure_starter_credits

    if ensure_starter_credits(peer_id):
        logger.info("Starter credits seeded for peer %s...", peer_id[:24])

    auth_target_path = active_config_home_dir() / "agent-bootstrap.json"
    auth_result = ensure_public_hive_auth(
        project_root=project_root,
        target_path=auth_target_path,
    )
    auth_snapshot = public_hive_auth_snapshot(auth_result)
    if not auth_result.get("ok"):
        auth_status = str(auth_result.get("status") or "unknown").strip() or "unknown"
        suggested_command = str(auth_result.get("suggested_command") or "").strip()
        suggested_remote_config_path = str(auth_result.get("suggested_remote_config_path") or "").strip()
        watch_host = str(auth_result.get("watch_host") or "").strip()
        if auth_status in {"missing_remote_config_path", "missing_watch_host", "missing_ssh_key"}:
            detail_parts = []
            if watch_host:
                detail_parts.append(f"watch_host={watch_host}")
            if suggested_remote_config_path:
                detail_parts.append(f"remote_config_path={suggested_remote_config_path}")
            if suggested_command:
                detail_parts.append(f"next_step={suggested_command}")
            detail_text = " | ".join(detail_parts) if detail_parts else "set Public Hive auth config explicitly"
            logger.info("Public Hive writes are not hydrated yet: %s | %s", auth_status, detail_text)
        else:
            logger.warning("Public Hive auth is not wired for writes: %s", auth_status)

    probe = probe_machine()
    runtime_home = (
        str(boot.context.paths.runtime_home)
        if getattr(getattr(boot, "context", None), "paths", None) is not None
        else None
    )
    # Honour a persisted GPU-inference verdict BEFORE the provider env/manifests are built. If a prior
    # install/boot proved the GPU cannot generate (old driver, too-small VRAM), this forces the Ollama
    # lane to CPU via VOOL_OLLAMA_NUM_GPU=0 so both the manifest and the router's CPU response budget
    # engage — instead of the agent silently shipping brain-dead on a GPU it cannot use. Fail-safe:
    # a missing/ok verdict is a no-op, and it never overrides an operator-set VOOL_OLLAMA_NUM_GPU.
    if runtime_home:
        from core.gpu_capability_state import apply_persisted_cpu_fallback, load_gpu_capability

        apply_persisted_cpu_fallback(runtime_home)
        _gpu_cap = load_gpu_capability(runtime_home)
        if _gpu_cap and str(_gpu_cap.get("outcome") or "") in {
            "driver_too_old",
            "vram_insufficient",
            "gpu_unsupported",
        }:
            from core.gpu_inference_probe import GpuInferenceVerdict, gpu_capability_advisory

            _advisory = gpu_capability_advisory(
                GpuInferenceVerdict(
                    outcome=str(_gpu_cap.get("outcome") or ""),
                    reason="",
                    raw_excerpt="",
                    recommend_cpu_fallback=bool(_gpu_cap.get("applied_cpu_fallback")),
                    recommended_num_gpu=0,
                    suggested_action="",
                ),
                driver_version=str(_gpu_cap.get("driver_version") or ""),
            )
            if _advisory:
                logger.warning("GPU capability: %s", _advisory)
    provider_env = merge_provider_env(runtime_home)
    requested_profile = active_install_profile_id(runtime_home=runtime_home, env=provider_env) if runtime_home else None
    profile_default_model = _profile_fast_default_model(
        requested_profile=requested_profile,
        runtime_home=runtime_home,
        env=provider_env,
    )
    persisted_selected_model = installed_profile_selected_model(runtime_home) if runtime_home else ""
    forced_model_tag = str(provider_env.get("VOOL_FORCE_OLLAMA_MODEL") or "").strip()
    configured_model_tag = (
        forced_model_tag
        or str(provider_env.get("VOOL_OLLAMA_MODEL") or "").strip()
        or persisted_selected_model
        or profile_default_model
        or default_runtime_model_tag(env=provider_env)
    )
    # The generated launcher exports its install-time recommendation as VOOL_OLLAMA_MODEL, so
    # presence alone cannot mean "operator pin". A FORCE value is always a pin. A process value
    # that differs from the persisted install record is also an explicit override; the matching
    # value is the launcher's inherited default and remains eligible for installed daily-policy
    # alignment. This preserves direct user overrides without freezing stale installer choices.
    process_model_tag = str(os.environ.get("VOOL_OLLAMA_MODEL") or "").strip()
    selection_pinned = bool(
        forced_model_tag
        or (
            process_model_tag
            and (
                not persisted_selected_model
                or process_model_tag.lower() != persisted_selected_model.lower()
            )
        )
    )
    runtime_model_tag = candidate_aware_daily_runtime_model_tag(
        configured_model_tag,
        env=provider_env,
        selection_pinned=selection_pinned,
        probe=probe,
    )
    if runtime_model_tag != configured_model_tag:
        logger.info(
            "Daily Auto boot primary aligned with installed routing policy: %s -> %s",
            configured_model_tag,
            runtime_model_tag,
        )
    runtime_parameter_size = parameter_size_for_model(runtime_model_tag)
    # Background, never inline: a first-run pull downloads gigabytes, and bootstrap runs BEFORE the
    # API server binds its port. The window that opens on first run must reach a server that reports
    # this progress, not a refused connection.
    model_pull = start_ollama_model_pull(runtime_model_tag)
    logger.info(
        "Hardware: %s | GPU: %s | Primary local model: %s",
        probe.accelerator,
        probe.gpu_name or "none",
        runtime_model_tag,
    )
    runtime_version_stamp = build_runtime_version_stamp(
        project_root=project_root,
        runtime_model_tag=runtime_model_tag,
        workstation_version=workstation_version,
    )
    runtime_started_at = str(runtime_version_stamp.get("started_at") or "")
    # By reference: /healthz copies the stamp per request but not this nested dict, so the pull
    # thread's updates are visible to a first-run client polling for progress.
    runtime_version_stamp["model_pull"] = model_pull.public
    logger.info(
        "Runtime build: %s | branch=%s | commit=%s | dirty=%s",
        runtime_version_stamp.get("build_id") or "unknown",
        runtime_version_stamp.get("branch") or "unknown",
        runtime_version_stamp.get("commit") or "unknown",
        runtime_version_stamp.get("dirty"),
    )

    compute_mode_disabled = env_bool("VOOL_DISABLE_COMPUTE_MODE") or (
        os.name == "nt" and not env_bool("VOOL_ENABLE_WINDOWS_COMPUTE_MODE")
    )
    if compute_mode_disabled:
        logger.info("Adaptive compute mode daemon disabled for this API runtime.")
    else:
        compute_daemon = ComputeModeDaemon(has_gpu=probe.accelerator != "cpu")
        compute_daemon.start()

    model_registry = ModelRegistry()
    ensure_default_provider(
        model_registry,
        runtime_model_tag,
        env=provider_env,
        install_profile=requested_profile,
        runtime_home=runtime_home,
    )
    for warning in model_registry.startup_warnings():
        logger.warning("Model warning: %s", warning)
    def _run_provider_prewarm() -> None:
        # Warming loads the model into memory, so it cannot start until the model is on disk.
        model_pull.wait(timeout=_MODEL_PULL_WAIT_SECONDS)
        log_prewarm_results(
            model_registry,
            model_tag=runtime_model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
        )

    # Provider prewarm loads models into memory and can take minutes. When run_prewarm is False the
    # API server defers it (binding the port first) and runs this closure on a background thread, so
    # 11435/healthz is reachable immediately instead of refusing connections during warmup.
    deferred_prewarm: Callable[[], None] | None = None
    if run_prewarm:
        _run_provider_prewarm()
    else:
        deferred_prewarm = _run_provider_prewarm
        logger.info("Provider prewarm deferred; binding the API port first and warming in the background.")

    selection = boot.backend_selection
    if selection is None:
        raise RuntimeError("API bootstrap did not resolve a backend selection.")
    if selection.backend_name == "remote_only":
        logger.warning("No local backend found. Continuing in remote-only mode.")

    persona = load_active_persona("default")
    display_name = get_agent_display_name()
    if ensure_openclaw_registration(display_name=display_name, model_tag=runtime_model_tag):
        logger.info("OpenClaw registration ensured for agent '%s'.", display_name)
    else:
        logger.warning("OpenClaw registration could not be refreshed automatically.")

    agent = VoolAgent(
        backend_name=selection.backend_name,
        device=selection.device,
        persona_id=persona.persona_id,
    )
    agent.start()

    from core.product_edition import edition_allows
    from core.runtime_mode import mesh_daemon_boot_allowed

    mesh_edition_ok, mesh_edition_reason = edition_allows("mesh_daemon")
    mesh_daemon_disabled = not mesh_daemon_boot_allowed(
        edition_allows_mesh=mesh_edition_ok,
        disable_env_set=bool(env_bool("VOOL_DISABLE_MESH_DAEMON")),
    ) or (os.name == "nt" and not env_bool("VOOL_ENABLE_WINDOWS_MESH_DAEMON"))
    daemon: VoolDaemon | None = None
    if mesh_daemon_disabled:
        if not mesh_edition_ok:
            logger.info("Mesh daemon disabled by product edition: %s", mesh_edition_reason)
        else:
            logger.info("Mesh daemon disabled: research networking not enabled (production build).")
    else:
        pool_cap = max(1, int(policy_engine.get("orchestration.local_worker_pool_max", 10)))
        daemon_capacity, _ = resolve_local_worker_capacity(requested=None, hard_cap=pool_cap)
        daemon = VoolDaemon(
            daemon_runtime_config(
                capacity=int(daemon_capacity),
                local_worker_threads=max(2, int(daemon_capacity) * 2),
            )
        )
        daemon.start()

    logger.info("%s API server ready.", display_name)
    logger.info("Peer ID: %s...", peer_id[:24])
    logger.info("Backend: %s | Device: %s", selection.backend_name, selection.device)
    if daemon is not None:
        logger.info("Mesh daemon: active on UDP %s", daemon.config.bind_port)
    else:
        logger.info("Mesh daemon: disabled")

    return RuntimeServices(
        agent=agent,
        daemon=daemon,
        display_name=display_name,
        runtime_model_tag=runtime_model_tag,
        runtime_parameter_size=runtime_parameter_size,
        runtime_started_at=runtime_started_at,
        runtime_home=str(runtime_home or ""),
        runtime_version_stamp=runtime_version_stamp,
        public_hive_auth=auth_snapshot,
        provider_capability_truth=startup_provider_capability_truth(
            model_registry,
            model_tag=runtime_model_tag,
            runtime_home=runtime_home,
            requested_profile=requested_profile,
            env=provider_env,
            model_pull_status=model_pull.status,
            model_pull_detail=str(model_pull.public.get("detail") or ""),
        ),
        deferred_prewarm=deferred_prewarm,
        model_pull=model_pull,
    )


def _profile_fast_default_model(
    *,
    requested_profile: str | None,
    runtime_home: str | None,
    env: dict[str, str],
) -> str:
    default_model = default_runtime_model_tag(env=env)
    required_models = required_ollama_models_for_profile(
        profile_id=requested_profile or "local-only",
        model_tag=default_model,
        runtime_home=runtime_home,
        env=env,
    )
    fast_model = "vool-qwen3-30b-a3b:nothink"
    return fast_model if fast_model in required_models else ""


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return _sanitize_openclaw_text(str(content).strip())
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "").strip().lower() == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
        return _sanitize_openclaw_text("\n".join(parts).strip())
    return _sanitize_openclaw_text(str(content or "").strip())


def _sanitize_openclaw_text(text: str) -> str:
    return strip_openclaw_turn_scaffolding(strip_openclaw_sender_wrapper(text))


def strip_openclaw_sender_wrapper(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("Sender (untrusted metadata):"):
        return stripped
    match = _OPENCLAW_SENDER_WRAPPER_RE.match(stripped)
    if not match:
        return stripped
    return match.group(1).strip() or stripped


def strip_openclaw_turn_scaffolding(text: str) -> str:
    """Recover the real user message when OpenClaw merges an overlapping turn.

    When a message arrives while the previous turn is still running, the OpenClaw
    gateway concatenates it ahead of the in-flight prompt, shaped like::

        [Queued user message that arrived while the previous turn was still active]
        <the newly-typed message>

        <the original in-flight prompt>

    Left untouched, that whole blob flows downstream as if the user typed it - it
    leaks the marker into replies and, worse, gets handed to the weather/live-lookup
    path as a "location" (this is exactly how a "weather in Riga" question came back
    as "Los Vargas, Mexico": the scaffolding blob was fuzzy-matched by wttr.in). Take
    the block immediately after the marker as the message, since that is what the
    user most recently asked, and drop any leading GMT-stamped scaffolding bracket.
    """
    stripped = str(text or "").strip()
    if _OPENCLAW_QUEUED_TURN_MARKER in stripped:
        after_marker = stripped.split(_OPENCLAW_QUEUED_TURN_MARKER, 1)[1]
        blocks = [block.strip() for block in re.split(r"\n\s*\n", after_marker)]
        newest = next((block for block in blocks if block), "")
        stripped = newest or stripped.replace(_OPENCLAW_QUEUED_TURN_MARKER, "").strip()
    stripped = _OPENCLAW_TS_PREFIX_RE.sub("", stripped).strip()
    return stripped


def normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        content = message_text(message.get("content", ""))
        if not content:
            continue
        history.append({"role": role, "content": content})
    return history


def extract_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(normalize_chat_history(messages)):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip()
    return ""


def _is_canonical_session_id(value: str) -> bool:
    """A VOOL-native session id: ``openclaw:`` + the 20 lowercase-hex digest form used everywhere.

    The /chat sidebar mints ids in exactly this shape and re-sends them to resume a thread, so a
    canonical value is passed through verbatim (below) rather than re-hashed into a brand-new id.

    The shape rule itself belongs to `core.chat_session_identity`, which every other door that
    resolves a chat handle -- the attachment doors especially -- resolves through, so this door
    and those doors cannot disagree about which chat a handle names.
    """
    return is_canonical_chat_session_id(value)


def stable_openclaw_session_id(
    *,
    body: dict[str, Any],
    history: list[dict[str, str]],
    headers: dict[str, Any],
    allow_canonical_resume: bool = True,
) -> str:
    for key in (
        "session_id",
        "sessionId",
        "session",
        "conversation_id",
        "conversationId",
        "chat_id",
        "chatId",
        "thread_id",
        "threadId",
    ):
        value = str(body.get(key) or "").strip()
        if value:
            # A client that already owns a canonical id (the sidebar reopening a thread) keeps it
            # verbatim, so the reopened conversation persists under the SAME id instead of a re-hash.
            if allow_canonical_resume and _is_canonical_session_id(value):
                return value
            return fold_chat_session_id(value)

    for header_name in ("X-Session-Id", "X-Conversation-Id", "X-Thread-Id", "X-OpenClaw-Session"):
        value = str(headers.get(header_name) or "").strip()
        if value:
            return fold_chat_session_id(value)

    # No explicit handle means this is a new stateless chat. Content must never become identity:
    # two independent conversations commonly begin with the same greeting, and a content-derived
    # id would let the second request hydrate the first chat's persisted history. A client that
    # needs persisted continuity must supply an explicit handle; otherwise its supplied history
    # applies only to the current stateless request.
    return f"openclaw:{secrets.token_hex(10)}"


def runtime_headers(runtime: RuntimeServices) -> dict[str, str]:
    stamp = dict(runtime.runtime_version_stamp or {})
    return {
        "X-Vool-Runtime-Version": str(stamp.get("release_version") or "unknown"),
        "X-Vool-Runtime-Build": str(stamp.get("build_id") or "unknown"),
        "X-Vool-Runtime-Started-At": str(stamp.get("started_at") or ""),
        "X-Vool-Runtime-Commit": str(stamp.get("commit") or ""),
        "X-Vool-Runtime-Dirty": "1" if bool(stamp.get("dirty")) else "0",
    }


def default_workspace_root() -> str:
    """The workspace an UNBOUND chat gets: the real Desktop in the packaged app.

    The owner's product decision (2026-09-18): "create a folder/file" in a chat with no
    bound project must land where a person looks -- ~/Desktop -- not inside the hidden
    internal runtime directory where every such request silently disappeared (measured
    across the beta: finalbot, pocketbot, stashbot all landed invisible to the operator).

    Precedence, all preserved: an explicit VOOL_WORKSPACE_ROOT override wins (test rigs
    and isolated homes rely on it), a chat bound to a project resolves to that project's
    folder upstream of this default, and a request that names its own location is honored
    by the planners. Development runs (no VOOL_PROJECT_ROOT) keep the working-directory
    workspace exactly as before. macOS folder privacy (TCC): the first Desktop write
    prompts once for access; a denial surfaces as the typed write refusal, never a silent
    fallback to the hidden directory.
    """
    import os as _os

    packaged = bool(str(_os.environ.get("VOOL_PROJECT_ROOT") or "").strip())
    override = str(_os.environ.get("VOOL_WORKSPACE_ROOT") or "").strip()
    if override or not packaged:
        return str(resolve_workspace_root())
    from pathlib import Path as _Path

    return str(_Path.home() / "Desktop")


_ALLOWED_HOST_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _host_only(host_value: str) -> str:
    host = str(host_value or "").strip().lower()
    if not host:
        return ""
    if host.startswith("["):  # bracketed IPv6, e.g. [::1] or [::1]:11435
        end = host.find("]")
        return host[1:end] if end != -1 else host.strip("[]")
    if host.count(":") == 1:  # host:port (bare IPv6 has multiple colons and is left intact)
        return host.split(":", 1)[0]
    return host


def host_header_allowed(host_value: str) -> bool:
    """Reject a Host header that is not loopback/localhost (or an explicit allow-list entry).

    This defeats DNS-rebinding: a malicious page that rebinds its own domain to 127.0.0.1 still
    sends that domain as the Host header, which is not on the allow-list, so it cannot reach the
    always-on local API and read back conversation transcripts. An absent Host (non-browser local
    tooling / HTTP/1.0) is allowed because a rebinding attack always presents a Host, so this does
    not weaken the guard. Set VOOL_ALLOWED_HOSTS (comma-separated) to permit extra hosts when the
    server is deliberately bound to a non-loopback address.
    """
    hostname = _host_only(host_value)
    if not hostname:
        return True
    if hostname in _ALLOWED_HOST_NAMES:
        return True
    extra = os.environ.get("VOOL_ALLOWED_HOSTS", "")
    return hostname in {entry.strip().lower() for entry in extra.split(",") if entry.strip()}


def run_agent(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> dict[str, Any]:
    """One session's turns run one at a time, in arrival order (MF-15).

    Concurrent same-session turns interleaved the transcript by completion
    order (a fast turn's answer landed between a slow turn's question and its
    answer) and ran the later turn without the earlier exchange in context.
    Both the buffered and streamed /api/chat paths funnel through here, so this
    is the single gate layer; the history refresh runs INSIDE the gate so a
    queued turn sees the exchange the turn ahead of it just persisted.
    """
    from core.session_turn_gate import hold_session_turn_gate

    with hold_session_turn_gate(session_id):
        if session_id and isinstance(source_context, dict):
            client_history = source_context.get("client_conversation_history")
            if isinstance(client_history, list):
                from core.persistent_memory import augment_history_from_session_log

                try:
                    refreshed = augment_history_from_session_log(
                        [dict(item) for item in client_history if isinstance(item, dict)],
                        session_id=session_id,
                        user_text=user_text,
                    )
                except Exception:
                    refreshed = None
                if refreshed:
                    source_context = {
                        **source_context,
                        "conversation_history": refreshed,
                        "history_message_count": len(refreshed),
                    }
        return _run_agent_locked(
            runtime,
            user_text,
            session_id=session_id,
            source_context=source_context,
            workspace_root_provider=workspace_root_provider,
        )


def _run_agent_locked(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str | None = None,
    source_context: dict[str, Any] | None = None,
    workspace_root_provider: Callable[[], str] = default_workspace_root,
) -> dict[str, Any]:
    if not runtime.agent or not user_text:
        return {"response": "", "confidence": 0.0}
    base_context = default_agent_source_context()
    if source_context:
        base_context.update(source_context)
    default_workspace = workspace_root_provider()
    base_context.setdefault("workspace", default_workspace)
    base_context.setdefault("workspace_root", default_workspace)
    context_policy_trace: dict[str, object] = {"state": "not_bound"}
    if session_id:
        base_context["runtime_session_id"] = session_id
        from core.context_scope import ContextAccessPolicy

        access_policy = ContextAccessPolicy.for_request(
            session_id=session_id,
            source_context=base_context,
        )
        base_context["chat_id"] = access_policy.chat_id
        if access_policy.project_id:
            base_context["_trusted_project_id"] = access_policy.project_id
        # Operator Profile (P1): bind the request's principal + chat for every profile reader on
        # this request thread -- the recall lane below reads the user's name BEFORE the agent's
        # front door runs, and a chat-scoped name must resolve for its own chat there too.
        try:
            from core.operator_profile import principal_for_request
            from core.operator_profile_turn import bind_turn_scope

            bind_turn_scope(principal_for_request(base_context), access_policy.chat_id, access_policy.project_id)
        except Exception:
            pass
        context_policy_trace = {
            "state": access_policy.namespace_state,
            "project_context_allowed": access_policy.allow_project_context,
            "profile_context_allowed": access_policy.allow_user_profile_context,
            "action_receipts_allowed": access_policy.allow_action_receipts,
            "cross_chat_import_count": len(access_policy.imported_chat_ids),
            "project_import_count": len(access_policy.imported_project_ids),
        }
    if runtime.runtime_home:
        base_context.setdefault("runtime_home", runtime.runtime_home)
    from core.runtime_task_events import emit_runtime_event

    def _completed_model_receipts() -> tuple[
        list[dict[str, str]], dict[str, Any], dict[str, Any]
    ]:
        """Recover redacted provider/output receipts from this turn's event ledger.

        Provider execution can cross a shallow request-context copy (for routing,
        retries, or concurrency).  The immutable provider receipt is still emitted
        before this terminal event, so join it by the server-created request/turn
        identifiers instead of silently losing the audit chain.
        """
        request_id = str(base_context.get("request_id") or "").strip()
        client_turn_id = str(base_context.get("cancel_turn_id") or "").strip()
        turn_id = str(base_context.get("turn_id") or "").strip()
        runtime_session_id = str(
            base_context.get("runtime_session_id")
            or base_context.get("session_id")
            or ""
        ).strip()
        if not runtime_session_id or (not request_id and not client_turn_id):
            return [], {}, {}
        try:
            from core.runtime_task_events import (
                list_recent_runtime_session_events,
            )

            events = list_recent_runtime_session_events(
                runtime_session_id,
                limit=200,
            )
        except Exception:
            return [], {}, {}
        links: list[dict[str, str]] = []
        response_control: dict[str, Any] = {}
        conductor_receipt: dict[str, Any] = {}
        for event in events:
            event_type = str(event.get("event_type") or "")
            if event_type == "conductor_plan_completed":
                if turn_id and str(event.get("turn_id") or "") != turn_id:
                    continue
                if not turn_id and request_id and str(event.get("request_id") or "") != request_id:
                    continue
                if client_turn_id and str(event.get("client_turn_id") or "") != client_turn_id:
                    continue
                if isinstance(event.get("receipt"), dict):
                    conductor_receipt = dict(event["receipt"])
                continue
            if request_id and str(event.get("request_id") or "") != request_id:
                continue
            if client_turn_id and str(event.get("client_turn_id") or "") != client_turn_id:
                continue
            if event_type != "model.call_completed":
                continue
            provider_manifest_id = str(
                event.get("provider_manifest_id") or ""
            ).strip()
            if not provider_manifest_id:
                continue
            link = {
                "context_manifest_id": str(
                    event.get("context_manifest_id") or ""
                ),
                "context_manifest_trace_id": str(
                    event.get("context_manifest_trace_id") or ""
                ),
                "provider_manifest_id": provider_manifest_id,
                "payload_hash": str(
                    event.get("provider_payload_hash") or ""
                ),
                "provider_id": str(event.get("provider_id") or ""),
                "model_id": str(event.get("model_id") or ""),
            }
            if link not in links:
                links.append(link)
            event_control = event.get("response_control")
            if isinstance(event_control, dict):
                response_control.update(event_control)
        return links, response_control, conductor_receipt

    def finalize_turn_trace(result: dict[str, Any]) -> dict[str, Any]:
        """Persist only link fields needed to audit one finished turn.

        Raw prompts, responses, headers, provider payloads, and credentials remain in their
        respective protected stores and are intentionally absent from this runtime event.
        """
        payload = dict(result or {})
        # A turn terminal trace is an ownership boundary, not merely a UI event.  If a scheduler
        # deadline abandoned a provider worker, close that exact started attempt first.  This
        # produces an honest terminal receipt even for an adapter that ignored cancellation, and
        # the ledger's single-assignment transition prevents a late return from relabelling it.
        from core.normalized_provider_result import ProviderErrorClass
        from core.turn_model_call_ledger import (
            fail_pending_provider_calls,
            turn_call_accounting,
        )

        terminal_error_class = ProviderErrorClass.PROVIDER_TIMEOUT.value
        for call in fail_pending_provider_calls(
            base_context,
            error_class=terminal_error_class,
        ):
            emit_runtime_event(
                base_context,
                event_type="model.call_failed",
                message="Model call exceeded the enclosing turn deadline.",
                details={
                    "request_id": str(base_context.get("request_id") or ""),
                    "turn_id": str(base_context.get("turn_id") or ""),
                    "task_id": str(base_context.get("task_id") or ""),
                    "model_call_id": str(call.get("model_call_id") or ""),
                    "provider_id": str(call.get("provider_id") or ""),
                    "model_id": str(call.get("model_id") or ""),
                    "call_role": str(call.get("call_role") or ""),
                    "reason": "provider deadline expired before the turn terminal trace",
                    "error_class": terminal_error_class,
                    "retryable": True,
                    "provider_health_recorded": False,
                    "terminalized_by": "turn_runtime",
                },
            )
        call_accounting = turn_call_accounting(base_context)
        if call_accounting:
            # The ledger is the invocation seam's measurement.  A result assembled by a fallback
            # lane may carry a stale zero; it has no authority to erase a call that really started.
            payload["model_calls"] = int(call_accounting.get("calls") or 0)
            payload["model_call_accounting"] = call_accounting
        event_provider_links, event_response_control, event_conductor_receipt = (
            _completed_model_receipts()
        )
        if event_conductor_receipt and not payload.get("conductor_receipt"):
            payload["conductor_receipt"] = event_conductor_receipt
        response_control = {
            **event_response_control,
            **dict(base_context.get("response_control") or {}),
            **dict(payload.get("response_control") or {}),
        }
        if response_control:
            payload["response_control"] = response_control
        from core.runtime_task_outcome import (
            FulfillmentStatus,
            terminal_fulfillment_outcome,
            terminal_trace_outcome,
        )

        fulfillment_outcome = terminal_fulfillment_outcome(
            payload,
            source_context=base_context,
            call_accounting=call_accounting,
        )
        payload["fulfillment_outcome"] = fulfillment_outcome.to_dict()
        # `run_once` owns the normal checkpoint close, but final response controls live at this
        # front door.  Re-close only an unfulfilled terminal result so the persisted task truth
        # matches the final canonical bytes while the transport status remains compatible.
        checkpoint_reclose_stages = {
            "conductor_execution",
            "output_validation",
            "provider_execution",
            "runtime_execution",
        }
        if (
            fulfillment_outcome.fulfillment_status is not FulfillmentStatus.FULFILLED
            and fulfillment_outcome.failure_stage in checkpoint_reclose_stages
            and str(base_context.get("runtime_checkpoint_id") or "").strip()
        ):
            runtime.agent._finalize_runtime_checkpoint(
                base_context,
                status="completed",
                final_response=str(payload.get("response") or ""),
                outcome=fulfillment_outcome.to_dict(),
            )
        requested_constraint = dict(
            base_context.get("response_constraint") or {}
        )
        final_constraint = dict(
            base_context.get("response_constraint_final") or {}
        )
        provider_links = [
            {
                "context_manifest_id": str(link.get("context_manifest_id") or ""),
                "context_manifest_trace_id": str(
                    link.get("context_manifest_trace_id") or ""
                ),
                "provider_manifest_id": str(link.get("provider_manifest_id") or ""),
                "payload_hash": str(link.get("payload_hash") or ""),
                "provider_id": str(link.get("provider_id") or ""),
                "model_id": str(link.get("model_id") or ""),
            }
            for link in [
                *list(base_context.get("provider_manifest_links") or []),
                *event_provider_links,
            ]
            if isinstance(link, dict)
        ]
        provider_links = [
            link
            for index, link in enumerate(provider_links)
            if link not in provider_links[:index]
        ]
        context_manifest_id = str(
            base_context.get("context_manifest_id")
            or next(
                (
                    link.get("context_manifest_id")
                    for link in provider_links
                    if link.get("context_manifest_id")
                ),
                "",
            )
            or ""
        )
        # NIA-014 (2026-08-30): machine-readable failure-stage verdict — every served
        # turn self-reports WHICH STAGE ended it, joined from events this turn already
        # emitted. Distinct field name by design: runtime_task_outcome already stamps
        # `failure_stage`; this finer taxonomy must not blur that vocabulary.
        stage_verdict: dict[str, str] = {}
        try:
            from core.runtime_task_events import list_recent_runtime_session_events
            from core.turn_failure_stage import (
                classify_turn,
                explain,
                observation_from_trace,
            )

            # base_context, not the sibling closure's local: finalize must be
            # self-sufficient (the live 11441 proof caught the NameError the
            # fail-soft was swallowing — an empty verdict, not a refusal).
            verdict_session = str(
                base_context.get("runtime_session_id")
                or base_context.get("session_id")
                or ""
            )
            verdict_events = (
                list_recent_runtime_session_events(verdict_session, limit=200)
                if verdict_session
                else []
            )
            # The trace events alone never carry the turn's final text, so a FULFILLED
            # answer was misstaged `synthesis_empty` (content exists only in the payload).
            # Feed the terminal payload's own bytes into the observation: the verdict then
            # distinguishes a delivered answer (success) from an actually-empty synthesis,
            # which is the distinction the sufficiency writer downstream is required to record.
            verdict_observation = observation_from_trace(verdict_events)
            final_text = str(
                payload.get("response") or payload.get("output_text") or ""
            )
            if final_text.strip():
                verdict_observation.raw_content = final_text
                verdict_observation.final_text = final_text
            # A validator-rejected answer is not a success that happens to have text: when the
            # turn's own response-control constraint refused the final bytes, name the guard —
            # the same truth `terminal_fulfillment_outcome` records as response_constraint
            # failure codes. Without this the classifier would read delivered-but-rejected
            # bytes as a success, and the sufficiency writer would learn the wrong signal.
            if not verdict_observation.validator_rejection:
                constraint_rejected = bool(
                    list(final_constraint.get("violations") or [])
                ) or any(
                    str(code).startswith("response_constraint")
                    for code in list(fulfillment_outcome.failure_codes or [])
                )
                if constraint_rejected:
                    verdict_observation.validator_rejection = "response_constraint_noncompliant"
            verdict_state = classify_turn(verdict_observation)
            stage_verdict = {"state": verdict_state.value, "explain": explain(verdict_state)}
        except Exception:
            stage_verdict = {}
        emit_runtime_event(
            base_context,
            event_type="turn.trace_completed",
            message="Turn trace completed.",
            details={
                "request_id": str(base_context.get("request_id") or ""),
                "trace_id": str(
                    base_context.get("request_id")
                    or base_context.get("task_id")
                    or ""
                ),
                "task_id": str(base_context.get("task_id") or ""),
                "chat_id": str(base_context.get("chat_id") or ""),
                "project_id": str(base_context.get("_trusted_project_id") or ""),
                "policy": context_policy_trace,
                "action_policy": str(base_context.get("action_policy") or ""),
                "context_manifest_id": context_manifest_id,
                "route": str(payload.get("route") or payload.get("mode") or ""),
                "stage_verdict": stage_verdict,
                "response_control_mode": str(
                    response_control.get("mode") or "none"
                ),
                "response_control": {
                    "ordinary_chat_output": dict(
                        response_control.get("ordinary_chat_output") or {}
                    ),
                    "response_language": dict(
                        response_control.get("response_language") or {}
                    ),
                    "final_ui": dict(response_control.get("final_ui") or {}),
                    "provider_completion": dict(response_control.get("provider_completion") or {}),
                    "verifier_presentation": dict(
                        response_control.get("verifier_presentation") or {}
                    ),
                    "retry_attempted": bool(
                        response_control.get("retry_attempted")
                    ),
                    "fallback_applied": bool(
                        response_control.get("fallback_applied")
                    ),
                    # Whether the evidence this turn retrieved is witnessed in the answer. Projected
                    # explicitly: this list is a whitelist, so a verdict that is not named here is
                    # invisible in the trace even when the runtime acted on it.
                    "evidence_binding": dict(
                        payload.get("evidence_binding")
                        or response_control.get("evidence_binding")
                        or {}
                    ),
                },
                "response_constraint": {
                    "shape": {
                        key: requested_constraint.get(key)
                        for key in (
                            "exact_words",
                            "max_words",
                            "exact_sentences",
                            "max_sentences",
                        )
                        if requested_constraint.get(key) is not None
                    },
                    "compliant": final_constraint.get("compliant"),
                    "violation_count": len(
                        list(final_constraint.get("violations") or [])
                    ),
                },
                "outcome": terminal_trace_outcome(fulfillment_outcome),
                "fulfillment_outcome": fulfillment_outcome.to_dict(),
                "model_calls": int(payload.get("model_calls") or 0),
                "web_calls": int(payload.get("web_calls") or 0),
                "fresh_data_retrieval_receipts": [
                    dict(receipt)
                    for receipt in list(
                        base_context.get("fresh_data_retrieval_receipts") or []
                    )
                    if isinstance(receipt, dict)
                ],
                "web_retrieval_receipts": [
                    dict(receipt)
                    for receipt in list(
                        base_context.get("web_retrieval_receipts") or []
                    )
                    if isinstance(receipt, dict)
                ],
                "provider_manifest_links": provider_links,
            },
        )
        # PB01 hook 3 — THE MODEL-SUFFICIENCY WRITER SEAM: the turn is terminal and its stage
        # verdict is durable above, so the join of THIS TURN'S OWN events (provider/model
        # identity from `model.call_completed`, carrying the routing task_kind, x the stage
        # verdict) writes one typed observation per (turn, provider) — deduplicated by turn_key
        # in the store, and scoped to this turn's own turn_key so no earlier turn's or another
        # session's events can join. A cancelled turn records nothing: the operator stopped it,
        # which says nothing about the provider's quality. Fail-soft: learning must never be able
        # to end a turn.
        try:
            from core.execution_truth import resolve_turn_key
            from core.learning_integration import record_turn_sufficiency_from_events
            from core.runtime_task_events import list_recent_runtime_session_events

            turn_cancelled = (
                fulfillment_outcome.fulfillment_status == FulfillmentStatus.CANCELLED
            )
            if verdict_session and not turn_cancelled:
                record_turn_sufficiency_from_events(
                    list_recent_runtime_session_events(verdict_session, limit=200),
                    session_id=verdict_session,
                    turn_key=resolve_turn_key(base_context, payload),
                )
        except Exception:
            pass
        return payload
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty, enforce_url_grounding
    from core.context_retrieval import (
        capsule_exact_response,
        get_last_retrieval_telemetry,
        reset_retrieval_telemetry,
        update_retrieval_telemetry,
        validate_capsule_exact_response,
    )
    from core.remote_fetch_policy import remote_fetch_attempt_count, remote_fetch_policy_scope

    reset_retrieval_telemetry()
    from core.memory_first_router import reset_turn_usage as _reset_turn_usage
    from core.turn_model_call_ledger import begin_turn as _begin_model_call_ledger

    _reset_turn_usage()
    # Open the turn's model-call ledger. It had NO production call site: `record_provider_call`
    # drops any call whose context carries no stamped ledger id, so every increment made by the
    # router seam and the intent arbiter went nowhere and `turn_model_calls` returned 0 on every
    # live turn -- the accounting existed, was tested, and was never once running. Opened here,
    # beside the turn-usage reset, because this is where one user message begins, and the id is
    # stamped into `base_context` so every shallow copy and every pool worker counts into it.
    _begin_model_call_ledger(base_context)
    with remote_fetch_policy_scope(base_context):
        # The scripted web0/.null topic-answer interception that lived here is REMOVED by the
        # owner's instruction (2026-09-16): its keyword evidence was far weaker than its
        # authority -- a provider-health checker asking to "capture the returned model CLAIM"
        # and "calculate the request COST" was answered, wholesale, with the `.null`
        # registration fee boilerplate, because "claim" sat on the registration-verb list and
        # "cost" on the fee list. Real questions always reach the ordinary lanes from here.
        # ANSWER COVERAGE. "what is your name?" alone is this lane's whole turn. The same question
        # sitting beside "1000 TRY to USD?" and "write a README for Thunder" is one clause of
        # three, and returning here would end the turn on it -- the other two would never reach a
        # lane at all. The name is still answered; it travels on as a slice answer and the turn
        # continues to `run_once`, which sees it as established fact.
        from core.agent_runtime.answer_coverage import (
            FAMILY_ASSISTANT_IDENTITY,
            claim_may_preempt_turn,
            record_slice_answer,
        )

        assistant_identity = _assistant_identity_response(user_text)
        if assistant_identity is not None:
            if claim_may_preempt_turn(user_text, FAMILY_ASSISTANT_IDENTITY):
                update_retrieval_telemetry(web_calls=remote_fetch_attempt_count())
                return finalize_turn_trace(assistant_identity)
            record_slice_answer(
                base_context,
                text=user_text,
                family=FAMILY_ASSISTANT_IDENTITY,
                response=str(assistant_identity.get("response") or ""),
                reason="local_assistant_identity",
            )
        memory_recall = _memory_recall_response(runtime, user_text=user_text, source_context=base_context)
        if memory_recall is not None:
            # Same answer-coverage contract as the identity lane above: a recall question alone is
            # this lane's whole turn; beside another family's clause it is one slice of the turn,
            # and returning here would end the turn with the sibling never asked. This was the one
            # pre-agent exit with no arbitration -- the answer is still produced; it travels on as
            # a slice answer and the turn continues to `run_once`.
            from core.agent_runtime.answer_coverage import FAMILY_MEMORY_RECALL, coverage_for

            memory_coverage = coverage_for(
                user_text,
                FAMILY_MEMORY_RECALL,
                probe=lambda clause: _looks_like_private_memory_recall(
                    clause, recent_user_texts=()
                ),
            )
            if memory_coverage.covers_whole_turn:
                update_retrieval_telemetry(web_calls=remote_fetch_attempt_count())
                return finalize_turn_trace(memory_recall)
            record_slice_answer(
                base_context,
                text=user_text,
                family=FAMILY_MEMORY_RECALL,
                response=str(memory_recall.get("response") or ""),
                reason="private_memory_recall",
            )
        from core.usepod.price_wait import running_task
        with running_task(session_id, user_text, queue_item_id=base_context.get("price_wait_queue_id", ""), source_context=base_context):
            result = runtime.agent.run_once(
                user_text,
                session_id_override=session_id,
                source_context=base_context,
            )
        # UNSEALED results only (a caller driving run_agent with its own agent that did not
        # commit): the sanitizer + capsule override are completed HERE, before any transport
        # commit. A result that already carries its response.commit was sealed inside the
        # agent and is untouchable -- the K-08 verifies below are exactly that law.
        if not isinstance((result or {}).get("vool_response_commit"), dict):
            _capsule = capsule_exact_response(
                user_text,
                get_last_retrieval_telemetry(),
                session_id=session_id,
            )
            result = {**dict(result or {}), "_model_answer_pre_capsule": str((result or {}).get("response") or "")}
            if _capsule:
                # The override is REVALIDATED: the chat sanitizer vets the capsule's own bytes,
                # and only a capsule that survives it (and the exact-response validator) ships --
                # a sanitized override that fails keeps the model's answer untouched.
                _sanitizer = getattr(runtime.agent, "_sanitize_user_chat_text", None)
                if callable(_sanitizer):
                    _response_class = getattr(
                        getattr(runtime.agent, "ResponseClass", None),
                        "GENERIC_CONVERSATION",
                        "generic_conversation",
                    )
                    try:
                        _sanitized_capsule = _sanitizer(_capsule, response_class=_response_class)
                    except TypeError:
                        _sanitized_capsule = _capsule
                else:
                    _sanitized_capsule = _capsule
                if _sanitized_capsule and validate_capsule_exact_response(
                    _sanitized_capsule, user_text, session_id=session_id
                ):
                    result = {
                        **dict(result or {}),
                        "response": _sanitized_capsule,
                        "response_control": {
                            "mode": "capsule_exact",
                            "original_response_excerpt": str((result or {}).get("response") or "")[:120],
                        },
                    }
        # K-08 ORDERING-FROZEN verify-only assert: semantic transforms ran
        # INSIDE the sealing context before A2 admission (see
        # apps/vool_agent.py:_seal_semantic_result). Re-applying them here must
        # be a byte-level no-op; any divergence means admitted bytes were about
        # to be mutated post-seal, which is refused loudly — never applied.
        _controlled = apply_exact_response_control(dict(result or {}), user_text)
        if str(_controlled.get("response") or "") != str((result or {}).get("response") or ""):
            raise RuntimeError(
                "K-08 violation: exact-response-control would mutate post-admission bytes"
            )
        result = _controlled
        capsule_response = capsule_exact_response(
            user_text,
            get_last_retrieval_telemetry(),
            session_id=session_id,
        )
        if capsule_response and str(capsule_response) != str(result.get("response") or ""):
            # The one legal non-match: an UNSEALED result whose capsule override was revalidated
            # and REFUSED (the chat sanitizer or the exact validator rejected the capsule's own
            # bytes). The model's answer stands; that is the override's contract, not a mutation.
            _unsealed_refused = (
                not isinstance((result or {}).get("vool_response_commit"), dict)
                and str(result.get("response") or "") == str((result or {}).get("_model_answer_pre_capsule") or "\0")
            )
            if not _unsealed_refused:
                raise RuntimeError(
                    "K-08 violation: capsule transform would mutate post-admission bytes"
                )
        if isinstance(result, dict):
            result.pop("_model_answer_pre_capsule", None)
        # A-12/R-5: honesty + URL grounding ran INSIDE the sealing context
        # (pre-admit); these boundary re-applications are verify-only — any
        # divergence means post-admission mutation was about to happen.
        _pre_honesty = str(result.get("response") or "")
        result = enforce_final_action_honesty(
            dict(result or {}),
            user_input=user_text,
            effective_input=user_text,
            session_id=session_id,
            source_context=base_context,
        )
        if str(result.get("response") or "") != _pre_honesty:
            raise RuntimeError(
                "K-08 violation: final-action-honesty would mutate post-admission bytes"
            )
        # Honesty backstop (verify-only here; the legal application is inside
        # the sealing context): invented URL summaries must already be replaced.
        _pre_grounding = str(result.get("response") or "")
        result = enforce_url_grounding(result, user_input=user_text, fetch_attempts=remote_fetch_attempt_count())
        if str(result.get("response") or "") != _pre_grounding:
            raise RuntimeError(
                "K-08 violation: url-grounding would mutate post-admission bytes"
            )
        schedule_memory_extraction(
            runtime,
            user_text=user_text,
            assistant_output=str(dict(result or {}).get("response") or ""),
            session_id=session_id,
            source_context=base_context,
        )
        _admit_turn_context_page(
            user_text=user_text,
            assistant_output=str(dict(result or {}).get("response") or ""),
            session_id=session_id,
            source_context=base_context,
        )
        update_retrieval_telemetry(web_calls=remote_fetch_attempt_count())
        return finalize_turn_trace(_finalize_turn_usage(result, base_context))


def _admit_turn_context_page(
    *,
    user_text: str,
    assistant_output: str,
    session_id: str | None,
    source_context: dict[str, Any] | None,
) -> None:
    """C09: admit this turn's exchange as scoped context for LATER turns.

    One bounded page per turn, admitted through the turn-context authority
    under the full scope identity (principal / session / project / turn /
    generation) with the request's A8 lineage attached, so the page is
    withhold- and erase-able and never serves once its source turn is
    governed unavailable. Owner-local, active-namespace, non-group turns
    only — the same admission surface memory extraction already uses.
    A failure here is logged, never allowed to fail a served turn.
    """
    try:
        if not _memory_capture_allowed(source_context):
            return
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(source_context):
            return
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id:
            return
        context = dict(source_context or {})
        from core.context_scope import ContextAccessPolicy

        access_policy = ContextAccessPolicy.for_request(
            session_id=clean_session_id,
            source_context=context,
        )
        if access_policy.namespace_state != "active":
            return

        from core.turn_context import (
            ADMISSION_CONTENT_CAP_CHARS,
            TurnScope,
            admit_turn_context,
            close_turn_scope,
            resolve_principal,
        )

        user_part = str(user_text or "").strip()
        assistant_part = str(assistant_output or "").strip()
        if not user_part and not assistant_part:
            return
        content = f"[user]\n{user_part}\n[/user]\n[assistant]\n{assistant_part}\n[/assistant]"
        if len(content) > ADMISSION_CONTENT_CAP_CHARS:
            content = (
                content[:ADMISSION_CONTENT_CAP_CHARS]
                + f"\n[truncated: admission cap {ADMISSION_CONTENT_CAP_CHARS} chars]"
            )
        scope = TurnScope(
            principal=resolve_principal(context),
            session_id=clean_session_id,
            project_id=str(context.get("_trusted_project_id") or "").strip(),
            turn_id=str(context.get("_canonical_user_turn_id") or "").strip(),
        )
        admit_turn_context(
            content,
            scope=scope,
            source_kind="chat_exchange",
            title=(user_part[:80] or "chat exchange"),
            origin="inferred",
            retention_class="PERSISTENT",
            trust_class="TRUSTED",
            request_id=str(context.get("request_id") or "").strip(),
            metadata={"surface": str(context.get("surface") or "")},
        )
        # The turn that produced this page has just closed: release its
        # residency pin so the page faces relevance and budget like every
        # other page. Residence itself is storage, not pins.
        if scope.turn_id:
            close_turn_scope(scope.principal, scope.session_id, scope.turn_id)
    except Exception:
        logger.debug("turn-context admission skipped", exc_info=True)


def schedule_memory_extraction(
    runtime: RuntimeServices,
    *,
    user_text: str,
    assistant_output: str,
    session_id: str | None,
    source_context: dict[str, Any] | None,
) -> None:
    if not _memory_capture_allowed(source_context):
        return
    from core.request_trust import request_is_owner_local

    if not request_is_owner_local(source_context):
        # This legacy extractor writes user-profile blocks. Until those blocks
        # carry per-user provenance, only the trusted private desktop owner may
        # mutate them.
        return
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id:
        return
    try:
        from core.context_scope import ContextAccessPolicy

        access_policy = ContextAccessPolicy.for_request(
            session_id=clean_session_id,
            source_context=source_context,
        )
    except (RuntimeError, TypeError, ValueError):
        return
    if access_policy.namespace_state != "active":
        return
    messages = _messages_for_memory_extraction(
        user_text=user_text,
        assistant_output=assistant_output,
        source_context=source_context,
    )
    if not messages:
        return
    try:
        from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
        from core.fact_extractor import FactExtractor
        from core.vool_memory import VoolMemory

        runtime_home = str(runtime.runtime_home or (source_context or {}).get("runtime_home") or "").strip() or None
        semantic_agent_id = str(
            (source_context or {}).get("semantic_memory_agent_id")
            or SEMANTIC_MEMORY_AGENT_ID
        ).strip() or SEMANTIC_MEMORY_AGENT_ID
        memory = VoolMemory(runtime_home=runtime_home, agent_id=semantic_agent_id)
        extractor = FactExtractor(
            memory=memory,
            ollama_url=ollama_base_url(),
            close_memory_on_finish=True,
            session_id=access_policy.chat_id,
            project_id=access_policy.project_id,
        )
        extractor.trigger_async(messages)
    except Exception:
        return


def _memory_capture_allowed(source_context: dict[str, Any] | None) -> bool:
    context = dict(source_context or {})
    platform = str(context.get("platform") or "").strip().lower()
    surface = str(context.get("surface") or "").strip().lower()
    group_like = (
        platform in {"discord", "telegram", "slack", "whatsapp", "group"}
        or surface in {"discord", "telegram", "slack", "whatsapp", "group"}
        or bool(context.get("is_group"))
        or bool(context.get("group_id"))
        or bool(context.get("channel_is_group"))
    )
    if group_like:
        return False
    explicit = context.get("memory_capture_enabled")
    if isinstance(explicit, bool):
        return explicit
    return platform in {"api", "openclaw", "web_companion", "cli", ""} or surface in {"api", "openclaw", "channel", "cli", ""}


def _assistant_identity_response(user_text: str) -> dict[str, Any] | None:
    """Answer "what is your name?" from the local identity registry, or return ``None``.

    Ahead of `_memory_recall_response` and deliberately gated on nothing it is gated on. The
    assistant's own name is not the user's private data: it needs no chat namespace, no profile
    access grant and no memory subsystem, so a group surface or a closed namespace -- either of
    which makes `_memory_recall_response` decline -- must not push this question onto a model.

    `asks_assistant_identity` existed on the classifier and was asserted in the suite, but nothing
    in the runtime read it: `_looks_like_private_memory_recall` and `_memory_recall_response` both
    branch on `asks_user_identity` alone. So the classifier correctly named the subject, no caller
    acted on it, and the turn fell through to a full model run.
    """
    from core.user_identity_authority import (
        ASSISTANT_NAME_PROVENANCE,
        assistant_identity_answer,
        classify_identity_question,
    )

    # The bare-pronoun follow-up this guards is a question about the USER's name, never the
    # assistant's, so this path never needs the conversation history to decide.
    identity = classify_identity_question(user_text)
    if not identity.asks_assistant_identity:
        return None
    response, name, provenance = assistant_identity_answer(identity)
    if not name:
        return None
    return {
        "response": response,
        "confidence": 0.95,
        "source": "local_agent_identity",
        "route": "assistant_identity",
        "route_reason": "local_assistant_identity",
        "identity_subject": "assistant",
        "identity_name": name,
        "identity_provenance": provenance or ASSISTANT_NAME_PROVENANCE,
        "identity_intent": identity.reason,
        "model_calls": 0,
        "web_calls": 0,
    }


def _memory_recall_response(
    runtime: RuntimeServices,
    *,
    user_text: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from core.user_identity_authority import (
        SETTINGS_PROVENANCE,
        classify_identity_question,
        needs_prior_turns_to_classify,
        user_identity_answer,
    )

    if not _memory_capture_allowed(source_context):
        return None
    context = dict(source_context or {})
    chat_id = str(
        context.get("chat_id")
        or context.get("runtime_session_id")
        or context.get("session_id")
        or ""
    ).strip()
    if not chat_id:
        return None
    # A follow-up whose subject is a bare pronoun ("i saved it already in settigns?!?! u dont
    # see?!") only resolves against what the user asked a moment ago, so the prior turns are
    # fetched before classifying rather than after -- but only for the messages that need them,
    # so an ordinary chat turn does not pay for a history read it will never use.
    recent_user_texts = _recent_user_texts(chat_id) if needs_prior_turns_to_classify(user_text) else ()
    if not _looks_like_private_memory_recall(user_text, recent_user_texts=recent_user_texts):
        return None
    identity = classify_identity_question(user_text, recent_user_texts=recent_user_texts)
    try:
        from core.context_scope import ContextAccessPolicy

        access_policy = ContextAccessPolicy.for_request(
            session_id=chat_id,
            source_context=context,
        )
    except Exception:
        return None
    if (
        access_policy.namespace_state != "active"
        or not access_policy.allow_user_profile_context
    ):
        return None
    # The user's saved name lives in settings, not in durable memory. Reading it must not be
    # hostage to the memory subsystem opening: a failure here degrades the OTHER recall fields,
    # never the settings-derived identity answer.
    values: dict[str, str] = {}
    try:
        from core.context_retrieval import SEMANTIC_MEMORY_AGENT_ID
        from core.vool_memory import VoolMemory

        runtime_home = str(runtime.runtime_home or context.get("runtime_home") or "").strip() or None
        agent_id = str(context.get("agent_id") or "").strip() or SEMANTIC_MEMORY_AGENT_ID
        with VoolMemory(runtime_home=runtime_home, agent_id=agent_id) as memory:
            values = _private_memory_values(
                memory,
                include_profile=True,
                # Legacy project blocks have no project provenance. They remain
                # stored for migration but are quarantined from active recall.
                include_project=False,
            )
    except Exception:
        values = {}
    if identity.asks_user_identity:
        # Answer the user's own name from the settings authority. The memory-block name is only a
        # fallback for installs that predate the settings field, and it is labelled as such.
        response, name, provenance = user_identity_answer(
            fallback_name=str(values.get("name") or ""),
            fallback_provenance="memory.user_profile.Name" if values.get("name") else "",
        )
        if not name and _chat_already_holds_a_name(chat_id):
            # Nothing is stored, but the user TOLD us their name in this very chat. Answering
            # "I don't have a name saved for you. Add it in Settings under 'Your name'" is not a
            # degraded answer, it is a wrong one, and it arrives with model_calls=0 so nothing
            # downstream gets a chance to read the transcript that holds the answer.
            #
            # Measured 2026-08-17, turn 20 of a 50-turn chat: the user had said "My name is
            # Alex" in turn 1 and was told to add it in Settings in turn 20.
            #
            # The settings guidance is kept for the case it was written for -- a chat where the
            # name was never given -- so declining is conditioned on transcript evidence rather
            # than on the store being empty.
            return None
        return {
            "response": response,
            "confidence": 0.95,
            "source": "local_user_settings" if provenance == SETTINGS_PROVENANCE else "local_private_memory",
            "identity_subject": "user",
            "identity_name": name,
            "identity_provenance": provenance,
            "identity_intent": identity.reason,
        }
    if not any(values.values()):
        return None
    response = _format_private_memory_recall(values, user_text=user_text)
    if not response:
        return None
    return {
        "response": response,
        "confidence": 0.95,
        "source": "local_private_memory",
    }


def _recent_user_texts(chat_id: str, *, limit: int = 4) -> tuple[str, ...]:
    """The user's own recent messages in this chat, newest first."""
    try:
        from storage.dialogue_memory import recent_dialogue_turns

        turns = recent_dialogue_turns(chat_id, limit=limit, speaker_roles=("user",))
    except Exception:
        return ()
    texts = [
        str(turn.get("raw_input") or turn.get("normalized_input") or "").strip()
        for turn in turns
    ]
    return tuple(text for text in texts if text)


#: How far back to look for the user having stated their own name. The pronoun-resolution read
#: above deliberately stops at 4 turns because a bare "it" only refers to the message before it.
#: A name, once given, holds for the whole chat, so this read has to span one -- and it is only
#: paid for on a turn that asks for a name the stores do not hold.
_NAME_DECLARATION_LOOKBACK_TURNS = 120


def _chat_already_holds_a_name(chat_id: str) -> bool:
    """True when the user stated their own name earlier in THIS chat.

    Only the fact that a name was given is decided here, never which one. Picking the name out of
    the transcript is a reading job -- the user may have corrected it, given a nickname, or been
    talking about someone else in the same breath -- and a regex that guesses would be the same
    class of defect as the confident wrong answer this exists to prevent. So the finding is used
    to DECLINE, handing the turn to something that reads the conversation properly.
    """
    if not chat_id:
        return False
    from core.user_identity_authority import classify_identity_question

    for text in _recent_user_texts(chat_id, limit=_NAME_DECLARATION_LOOKBACK_TURNS):
        if classify_identity_question(text).reason == "user_name_declaration":
            return True
    return False


def _private_memory_values(
    memory: Any,
    *,
    include_profile: bool,
    include_project: bool,
) -> dict[str, str]:
    blocks = {
        "user_profile": (
            str(memory.block_read("user_profile") or "")
            if include_profile
            else ""
        ),
        "preferences": (
            str(memory.block_read("preferences") or "")
            if include_profile
            else ""
        ),
        "project_context": (
            str(memory.block_read("project_context") or "")
            if include_project
            else ""
        ),
        "constraints": (
            str(memory.block_read("constraints") or "")
            if include_profile
            else ""
        ),
    }
    # user_address ("call me X") is the authoritative identity fact. Prefer it for the name so a
    # stale or model-poisoned user_profile "Name:" block can never override how the user asked to
    # be addressed -- the deterministic "what's my name" answer is user_address, not a harvested
    # guess (the live "-> Rick" failure).
    try:
        from core.user_preferences import user_address

        authoritative_name = user_address()
    except Exception:
        authoritative_name = ""
    return {
        # The Operator Profile is the ONE name authority: a harvested "Name:" block line is
        # never a fallback for it (that is how a chat-only or unconfirmed name leaked across chats).
        "name": authoritative_name,
        "answer_style": _first_block_value(blocks["preferences"], ("Answer style", "Response style", "Preferred answer style")),
        "project_codename": _first_block_value(blocks["project_context"], ("Active project codename", "Project codename")),
        "constraints": _compact_block_lines(blocks["constraints"]),
    }


def _first_block_value(block_text: str, labels: tuple[str, ...]) -> str:
    for raw_line in str(block_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for label in labels:
            prefix = f"{label}:"
            if line.lower().startswith(prefix.lower()):
                return line[len(prefix) :].strip()
    return ""


def _compact_block_lines(block_text: str, *, max_lines: int = 3) -> str:
    lines = [line.strip() for line in str(block_text or "").splitlines() if line.strip()]
    return "; ".join(lines[:max_lines])


def _format_private_memory_recall(values: dict[str, str], *, user_text: str = "") -> str:
    text = " ".join(str(user_text or "").lower().split())
    broad_recall = any(
        marker in text
        for marker in (
            "profile recall",
            "personal profile",
            "what do you remember",
            "remember about me",
            "stored about me",
            "persistent memory",
        )
    )
    # Field-scoped answer: for a single-field question, reply with just that field
    # rather than dumping the whole profile. A broad or multi-field recall falls
    # through to the summary below.
    asked: list[str] = []
    if values.get("name") and (
        "my name" in text or "who am i" in text or "who is the user" in text
    ):
        asked.append(f"Your name is {values['name']}.")
    if values.get("answer_style") and any(
        p in text for p in ("answer style", "response style", "preferred style")
    ):
        asked.append(f"Your preferred answer style is {values['answer_style']}.")
    if values.get("project_codename") and "codename" in text:
        asked.append(f"Your active project codename is {values['project_codename']}.")
    if not broad_recall and len(asked) == 1:
        return asked[0]
    # Broad or multi-field recall — summarise what is stored.
    parts: list[str] = []
    if values.get("name"):
        parts.append(f"name: {values['name']}")
    if values.get("answer_style"):
        parts.append(f"preferred answer style: {values['answer_style']}")
    if values.get("project_codename"):
        parts.append(f"active project codename: {values['project_codename']}")
    if values.get("constraints"):
        parts.append(f"constraints: {values['constraints']}")
    if not parts:
        return ""
    return "Here's what I have stored about you — " + "; ".join(parts) + "."


def _messages_for_memory_extraction(
    *,
    user_text: str,
    assistant_output: str,
    source_context: dict[str, Any] | None,
) -> list[dict[str, str]]:
    context = dict(source_context or {})
    history = list(context.get("conversation_history") or context.get("client_conversation_history") or [])
    messages: list[dict[str, str]] = []
    for item in history[-18:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = message_text(item.get("content") or "")
        if content:
            messages.append({"role": role, "content": content})
    clean_user = str(user_text or "").strip()
    if clean_user and (not messages or messages[-1] != {"role": "user", "content": clean_user}):
        messages.append({"role": "user", "content": clean_user})
    clean_assistant = str(assistant_output or "").strip()
    if clean_assistant:
        messages.append({"role": "assistant", "content": clean_assistant})
    return messages[-20:]


def _usage_footer_enabled() -> bool:
    return os.environ.get("VOOL_SHOW_USAGE_FOOTER", "1").strip().lower() not in {"0", "false", "no", "off", ""}


def _format_usage_footer(usage: dict[str, Any] | None) -> str:
    """Compact one-line tokens/cost footer: ``local | qwen2.5:7b | 1,203 tok`` or
    ``cloud | claude | 2,450 tok | $0.0123``. Empty when there is nothing to show.

    Delegates so the model lane's line has ONE rendering shared with the provenance footer -- two
    copies of the same format string is how the streamed transcript and the buffered one would come
    to disagree after the first edit to either.
    """
    from core.response_provenance import format_model_usage_footer

    return format_model_usage_footer(usage)


def _finalize_turn_usage(
    result: dict[str, Any],
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach the served response's usage summary and append the turn's provenance footer.

    The footer used to be gated on `get_turn_usage()` returning a nonzero token total, which made
    it a USAGE footer: an ordinary chat reply carried `local | qwen2.5:7b | 930 tok` and the exact
    file write in the very next turn carried nothing at all, because a deterministic action runs no
    model and so has no tokens. Absence was reading as "nothing to say" when it meant "no tokens".

    `core.response_provenance` answers the question the footer was always for -- what produced this
    answer -- for every response class, and says `no model` in the words rather than by omission.
    The usage summary is still attached exactly as before, and is still the authority for the model
    lane's own numbers.

    Round 2, 2026-08-12. `get_turn_usage()` is a `threading.local()` and the conductor and the tool
    planner both run provider calls on a `ThreadPoolExecutor`, so a turn whose model call ran on a
    worker arrived here with `usage = None` while its Activity ledger plainly showed the call. The
    footer then filled both blanks with defaults and printed `local | model | tokens unreported` over
    a cloud Nemotron call. The turn's own ledger is keyed by an id stamped into the context dict, so
    it crosses that hop; it is consulted whenever the thread-local came back empty, and its call
    accounting rides on the result so the streamed path renders from the same facts.
    """
    from core.memory_first_router import get_turn_usage
    from core.response_provenance import append_provenance_footer, read_turn_provenance
    from core.turn_model_call_ledger import (
        turn_call_accounting,
        turn_presentation_selection,
        turn_served_usage,
    )

    usage = get_turn_usage() or turn_served_usage(source_context)
    if usage:
        result.setdefault("usage_summary", usage)
    accounting = turn_call_accounting(source_context)
    if accounting:
        result.setdefault("model_call_accounting", accounting)
    # C19: the router filed the presentation-selection record in the turn's
    # call ledger (the channel that survives provider-lane context copies);
    # read it back onto the result so the transport's display_metadata
    # assembly carries it and finalize can re-stamp against committed bytes.
    filed_selection = turn_presentation_selection(source_context)
    if filed_selection:
        result.setdefault("_presentation_selection", filed_selection)
    # Read from the turn's own stamped context rather than from whatever the producing lane
    # happened to set, so EVERY local-only turn is disclosed -- a refusal, a deterministic fast
    # path and a local model answer alike. The footer prints only facts it has read; this is the
    # read.
    from core.auto_local_only_mode import turn_is_local_only

    if turn_is_local_only(source_context):
        result["local_only"] = True
    # Machine-readable answer authorship is part of the API result even when the operator disables
    # the visual footer.  Consumers must not reverse-engineer authorship from model selection or a
    # nonzero call count; the same turn can contain a routing call and a deterministic tool answer.
    result["answer_provenance"] = read_turn_provenance(
        result,
        usage,
        accounting,
    ).as_dict()
    if not _usage_footer_enabled():
        return result
    result["response"] = append_provenance_footer(
        str(result.get("response") or ""),
        result,
        usage,
    )
    return result


def openai_chat_response(
    result: dict[str, Any], model: str, source_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    # C11: the non-streaming door passed source_context=None, so the commit builder could not
    # see the turn's own demand ledger and every served turn certified turn-grain provenance
    # only. The context is in scope at the caller; it just was not handed over.
    response_commit = _response_commit(result, source_context=source_context)
    # A7 W4: serve the exact committed bytes — the post-hash .strip() that made
    # the envelope diverge from its own content_hash is gone.
    response_text = str(response_commit.get("canonical_content") or "")
    usage_summary = result.get("usage_summary") if isinstance(result.get("usage_summary"), dict) else {}
    prompt_tokens = int(usage_summary.get("prompt_tokens") or 0)
    completion_tokens = int(usage_summary.get("output_tokens") or 0)
    payload = {
        "id": f"chatcmpl-{hashlib.sha256(response_text.encode()).hexdigest()[:12]}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "vool_response_commit": response_commit,
    }
    _attach_root_cause_status(payload, response_commit)
    return payload


def _attach_root_cause_status(payload: dict[str, Any], commit: dict[str, Any]) -> None:
    """Rule 8 publication: the compact operator status beside the answer.

    Derived ONLY from the commit's validated root-cause section — the served
    surface can never disagree with the committed truth (rule 7), and turns
    without a diagnosis contract carry no key at all (rule 5).
    """
    root_cause = commit.get("root_cause") if isinstance(commit, dict) else None
    if isinstance(root_cause, dict) and str(root_cause.get("operator_status") or "").strip():
        payload["root_cause_status"] = str(root_cause["operator_status"])


def attach_wallet_status(payload: dict[str, Any]) -> None:
    """The wallet's public-safe status beside an ordinary chat reply, only when the wallet is on.

    A disabled wallet (the default) adds nothing, so ordinary turns stay byte-identical.
    """
    try:
        from core.wallet.config import wallet_enabled

        if not wallet_enabled():
            return
        from core.wallet.status import wallet_status

        payload["wallet_status"] = wallet_status()
    except Exception:
        return


def ollama_chat_response(
    result: dict[str, Any],
    model: str,
    runtime: RuntimeServices,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_commit = _response_commit(result, source_context=source_context)
    # A7 W4: serve the exact committed bytes — no post-hash mutation.
    response_text = str(response_commit.get("canonical_content") or "")
    usage_summary = result.get("usage_summary") if isinstance(result.get("usage_summary"), dict) else {}
    prompt_eval_count = int(usage_summary.get("prompt_tokens") or 0)
    eval_count = int(usage_summary.get("output_tokens") or 0)
    payload = {
        "model": model,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "message": {"role": "assistant", "content": response_text},
        "done": True,
        "done_reason": "stop",
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_duration": 0,
        "eval_count": eval_count,
        "eval_duration": 0,
        "vool_response_commit": response_commit,
    }
    _attach_root_cause_status(payload, response_commit)
    attach_wallet_status(payload)
    return payload


def ollama_stream_chunk(
    *,
    model: str,
    content: str,
    created_at: str,
    done: bool,
    eval_count: int = 0,
    response_commit: dict[str, Any] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "model": model,
        "created_at": created_at,
        "message": {"role": "assistant", "content": content},
        "done": done,
    }
    if done:
        payload.update(
            {
                "done_reason": "stop",
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": 0,
                "prompt_eval_duration": 0,
                "eval_count": eval_count,
                "eval_duration": 0,
            }
        )
        # Backwards-compatible terminal authority for clients that understand VOOL's extension.
        # It rides on the existing final Ollama frame rather than replaying a second answer or
        # inserting a new NDJSON record. Legacy clients keep reading message.content + done exactly
        # as before; the shipped client atomically replaces speculative text from this commit.
        if response_commit:
            payload["vool_response_commit"] = dict(response_commit)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def task_event_line(task_event: dict[str, Any], *, created_at: str | None = None) -> bytes:
    """One NDJSON line carrying a typed task event on its own ``vool_event`` key.

    A dedicated line (not muxed into ``message.content``) so the live status UI can
    demux progress events from the answer text without string markers.
    """
    payload = dict(task_event)
    payload.setdefault("ts", created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    return json.dumps({"vool_event": payload}, separators=(",", ":")).encode("utf-8") + b"\n"


def _no_answer_terminal(reason_code: str, detail: str) -> dict[str, Any]:
    from core.finalization import no_answer_terminal

    return no_answer_terminal(turn_id="", reason_code=reason_code, detail=detail)


def no_answer_terminal_line(terminal: dict[str, Any], *, created_at: str | None = None) -> bytes:
    """One NDJSON line carrying typed NO_ANSWER_TERMINAL truth (A7 LAW 6).

    Failure/refusal details travel on their own ``vool_terminal`` key — never as
    assistant answer bytes. Zero manufactured prose; the turn ends zero-content.
    """
    payload = dict(terminal)
    payload.setdefault("ts", created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    return json.dumps({"vool_terminal": payload}, separators=(",", ":")).encode("utf-8") + b"\n"


def _final_task_event(result_payload: dict[str, Any], *, cancelled: bool = False) -> dict[str, Any]:
    """Terminal task.completed/task.failed/task.cancelled event synthesized from the turn result,
    carrying the final model lane + cost so the card can collapse to a verified summary.

    `cancelled` is read by the caller from the committed bytes: a turn the operator cancelled
    before publication commits the typed cancel notice, and its terminal is `task.cancelled`,
    never a `task.completed` that would paint a finished answer under a cancelled turn."""
    usage = result_payload.get("usage_summary") if isinstance(result_payload.get("usage_summary"), dict) else {}
    ok = not bool(result_payload.get("error")) and str(result_payload.get("status") or "ok").lower() not in {"failed", "error"}
    if cancelled:
        event: dict[str, Any] = {
            "type": "task.cancelled",
            "raw_type": "stream_result",
            "stage": "Cancelled",
            "summary": "Cancelled before publication",
            "tool": None,
            "status": "cancelled",
        }
    else:
        event = {
            "type": "task.completed" if ok else "task.failed",
            "raw_type": "stream_result",
            "stage": "Completed" if ok else "Failed safely",
            "summary": "Complete" if ok else "Stopped safely",
            "tool": None,
            "status": "completed" if ok else "failed",
        }
    cost_class = str((usage or {}).get("cost_class") or "").strip()
    model_id = str((usage or {}).get("model_id") or "").split("/")[-1].strip()
    # A ':free' OpenRouter variant is registered paid_cloud (so the escalation gate still governs it)
    # but it costs nothing — it must not be shown or metered as "paid".
    is_free_variant = bool((usage or {}).get("verified_free")) or model_id.lower().endswith(":free")
    is_paid = ("paid" in cost_class.lower()) and not is_free_variant
    if cost_class or model_id:
        event["model"] = {
            "lane": "cloud" if "cloud" in cost_class.lower() else "local",
            "paid": is_paid,
            "provider_id": None,
            "model_id": model_id or None,
        }
    usd = usage.get("usd_actual") if isinstance(usage, dict) else None
    out_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
    if usd is not None or out_tokens is not None:
        event["cost"] = {
            "cost_class": cost_class or None,
            "paid": is_paid,
            "usd_actual": usd,
            "output_tokens": out_tokens,
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
        }
    return event


def ollama_stream_chunks(
    result: dict[str, Any],
    model: str,
    *,
    response_commit: dict[str, Any] | None = None,
) -> list[bytes]:
    if response_commit is None:
        response_commit = _response_commit(result, source_context=None)
    # A7 W4: replay EXACTLY the committed canonical bytes — no strip, no
    # normalization after the hash was taken.
    full_text = str(response_commit.get("canonical_content") if response_commit else "")
    if not full_text:
        # R-8 (K-11): the raw fallback is DELETED. Empty committed bytes are
        # typed no-answer truth — a single explicit no-answer frame, never a
        # raw unsealed .strip()'d payload.
        from core.finalization import no_answer_terminal

        _na = no_answer_terminal(
            turn_id=str((result or {}).get("turn_id") or ""),
            reason_code="provider_no_content",
            persist=False,
        )
        full_text = ""
        result = {**dict(result or {}), "_no_answer_terminal": _na}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    chunks: list[bytes] = []
    words = full_text.split(" ") if full_text else []
    for index, word in enumerate(words):
        token = word if index == 0 else " " + word
        chunks.append(ollama_stream_chunk(model=model, content=token, created_at=now, done=False))
    chunks.append(
        ollama_stream_chunk(
            model=model,
            content="",
            created_at=now,
            done=True,
            eval_count=len(words),
            response_commit=response_commit,
        )
    )
    return chunks


def _hashlast_gate(commit: dict[str, Any]) -> str | None:
    """A-13.4: return a violation description when commit bytes do not hash to
    their recorded content_hash; None when the gate passes (or cannot apply)."""
    import hashlib as _hl

    body = str(commit.get("canonical_content") or "")
    expected = str(commit.get("content_hash") or "")
    if not expected or ("sha256:" not in expected):
        return ""
    actual = "sha256:" + _hl.sha256(body.encode("utf-8")).hexdigest()
    if actual != expected:
        return f"canonical_content hashes {actual} != committed {expected}"
    return None


def _response_commit(
    result_payload: dict[str, Any],
    *,
    source_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Forward the turn's canonical A7 finalization (never mint a second one).

    The mainline spine finalizes inside the sealing context (A2 identity bound,
    hash over exact bytes) and attaches the commit to the payload. Lanes that
    arrive here without one (legacy/fast paths) finalize through the same
    shared authority — ``core.finalization`` is the ONLY minter; this shim is
    transport framing.
    """
    attached = result_payload.get("vool_response_commit")
    if isinstance(attached, dict) and attached.get("type") == "response.commit":
        # A sealed answer is immutable, so transport may not scrub it after the hash. If an
        # upstream lane somehow sealed foreign tool syntax, fail closed instead of faithfully
        # replaying a fake tool call to the user.
        from core.model_output_guard import foreign_markers

        if foreign_markers(str(attached.get("canonical_content") or "")):
            raise RuntimeError("MODEL_OUTPUT_GUARD: sealed response contains foreign tool syntax")
        # A-13.4 HASH-LAST per-serve gate: committed bytes re-hashed at serve
        # time — a commit whose canonical_content no longer hashes to its
        # content_hash never reaches the wire.
        _gate = _hashlast_gate(attached)
        if _gate is not None:
            raise RuntimeError(f"HASH-LAST per-serve gate: {_gate}")
        return attached
    from core.response_provenance import format_provenance_footer, strip_provenance_footer

    usage = result_payload.get("usage_summary") if isinstance(result_payload.get("usage_summary"), dict) else {}
    provenance = (
        result_payload.get("answer_provenance")
        if isinstance(result_payload.get("answer_provenance"), dict)
        else {}
    )
    footer = format_provenance_footer(result_payload, usage) if _usage_footer_enabled() else ""
    verifier_presentation = dict(
        dict((source_context or {}).get("response_control") or {}).get(
            "verifier_presentation"
        )
        or {}
    )
    from core.response_provenance import slot_receipts as _slot_receipts

    # C11: per-slot attribution rides beside the turn-grain block, from the demand ledger the
    # turn already wrote. Empty for a turn that minted no demand, so those commits are
    # byte-identical to before.
    per_slot = _slot_receipts(source_context, result_payload)
    # C19: the router's pre-gate selection record rides the payload stash;
    # it is copied EXPLICITLY into the display_metadata assembly here.
    # finalize_answer re-stamps it against the committed (post-gate) bytes.
    presentation_selection = dict(result_payload.get("_presentation_selection") or {})
    display_metadata = {
        "provenance": dict(provenance),
        "usage": dict(usage),
        "provenance_footer": footer,
        **({"slot_receipts": per_slot} if per_slot else {}),
        **(
            {"verifier_presentation": verifier_presentation}
            if verifier_presentation
            else {}
        ),
        **(
            {"presentation_selection": presentation_selection}
            if presentation_selection
            else {}
        ),
    }
    from core.finalization import finalize_answer
    from core.model_output_guard import foreign_markers, scrub_foreign_markers

    # R-6 (K-05): the verdict computed at seal time rides the payload and is
    # consumed here explicitly — the ContextVar scope may already have exited.
    stashed_verdict = result_payload.get("_closure_verdict")
    closure = (
        dict(stashed_verdict)
        if isinstance(stashed_verdict, dict)
        else None
    )
    # ROOT-CAUSE CONTRACT — the same stash law: the diagnosis the turn sealed
    # rides the payload as its dict projection. The live typed object wins
    # when the caller still holds the turn context (the streaming path).
    stashed_root_cause = result_payload.get("_root_cause_contract")
    root_cause = stashed_root_cause if isinstance(stashed_root_cause, dict) else None
    if root_cause is None and isinstance(source_context, dict):
        riding = source_context.get("root_cause_contract")
        if riding is not None and type(riding).__name__ == "RootCauseContract":
            root_cause = riding.to_dict()
        elif isinstance(riding, dict):
            root_cause = riding
    canonical_content = strip_provenance_footer(str(result_payload.get("response") or ""))
    # Legacy/fast providers can reach this transport seam without an attached A7 commit. This is
    # their final pre-seal choke point: remove only mechanically detected foreign tool envelopes,
    # preserving ordinary clean bytes exactly. A pure envelope becomes typed no-answer failure.
    if foreign_markers(canonical_content):
        canonical_content = scrub_foreign_markers(canonical_content)
        if not canonical_content:
            raise RuntimeError("MODEL_OUTPUT_GUARD: response contained only foreign tool syntax")
    return finalize_answer(
        turn_id=str((source_context or {}).get("cancel_turn_id") or "").strip(),
        canonical_content=canonical_content,
        source_context=source_context,
        display_metadata=display_metadata,
        closure=closure,
        root_cause=root_cause,
    )


def openai_sse_stream_from_ollama_chunks(stream: Iterable[bytes], model: str) -> Iterator[bytes]:
    chunk_id = f"chatcmpl-{hashlib.sha256(f'{model}:{datetime.now(timezone.utc).timestamp()}'.encode()).hexdigest()[:12]}"
    created = int(datetime.now(timezone.utc).timestamp())
    emitted_role = False
    pending_commit: dict[str, Any] | None = None
    for raw_chunk in stream:
        raw_text = raw_chunk.decode("utf-8", errors="replace").strip()
        if not raw_text:
            continue
        # A7 W4: typed non-answer frames (task events / no-answer terminals)
        # pass through as extension events on their own SSE data payloads —
        # never muxed into delta.content, never dropped.
        if raw_text.lstrip().startswith("{\"vool_"):
            yield b"data: " + raw_text.encode("utf-8") + b"\n\n"
            continue
        payload = json.loads(raw_text)
        message = dict(payload.get("message") or {})
        content = str(message.get("content") or "")
        done = bool(payload.get("done"))
        if payload.get("vool_response_commit"):
            # M08/S5 fix: the terminal event CARRIES the finalization identity —
            # the SSE route is no longer identity-blind.
            pending_commit = dict(payload["vool_response_commit"])
        delta: dict[str, Any] = {}
        if not emitted_role:
            delta["role"] = "assistant"
            emitted_role = True
        if content:
            delta["content"] = content
        event = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": "stop" if done else None,
                }
            ],
        }
        if done and pending_commit is not None:
            event["vool_response_commit"] = pending_commit
        yield b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n\n"
        if done:
            yield b"data: [DONE]\n\n"
            break


# Model event names are split across two spellings. The lane/routing family is underscored
# (`model_lane_proof`, `model_routing_failed`, `model_usage`, a749073, 2026-06-18); the per-call
# family added later is dotted (`model.call_started`, `model.call_failed`, 496ce19, 2026-07-14),
# and core/cloud_broker.py emits the dotted names too. The renderer below was written against the
# underscored family only, so `"model.call_failed".startswith("model_")` was False and every
# per-call event fell through to the bare-message tail. An audit turn that failed on three
# provider lanes therefore reached the screen as three copies of "Prompt did not fit ...'s
# context window." with no reason attached, and attributing it took hours of manual tracing.
_MODEL_EVENT_PREFIXES = ("model_", "model.")

# The budget telemetry is built in core/prompt_budget.py and carries 13 keys, most of them
# irrelevant to a failure line (dropped_memory, retrieval_present, system_prompt_preserved...).
# Project it down to the window, the allowance, the demand and the verdict instead of allowing
# the key wholesale: the surrounding allowlist only guarantees that vetted keys leave the process
# while it stays flat, and a nested dict owned by another module would hand that module a
# permanent unreviewed channel to the user's screen.
_PROMPT_BUDGET_TEXT_FIELDS = frozenset(
    {
        "num_ctx",
        "available_prompt_tokens",
        "estimated_prompt_tokens_before",
        "estimated_prompt_tokens_after",
        "grounding_dropped",
        "status",
    }
)

# The measured counterpart to the budget above, and the reason it is a SEPARATE key rather than
# more fields inside `prompt_budget`: every name in that dict is an ESTIMATE the fitter computed,
# and these are what the provider reported off the wire. `source` travels with them so a reader can
# tell "the provider said 0" from "the provider said nothing" — which is the whole difference
# between a measurement and a hole. Projected, not merged, for the same reason as above.
_TOKEN_USAGE_TEXT_FIELDS = frozenset({"input_tokens", "output_tokens", "source"})


def format_runtime_event_text(event: dict[str, Any]) -> str:
    if str(event.get("event_type") or "").strip() == "model_output_chunk":
        return str(event.get("message") or "")
    event_type = str(event.get("event_type") or "").strip()
    if event_type.startswith(_MODEL_EVENT_PREFIXES):
        visible = {
            key: value
            for key, value in dict(event or {}).items()
            if key
            in {
                "event_type",
                "message",
                "schema",
                "turn_id",
                "session_id",
                "task_class",
                "task_kind",
                "output_mode",
                "complexity",
                "lane",
                "lane_type",
                "provider_id",
                "model_id",
                "model_name",
                "planned_provider_id",
                "planned_model_id",
                "actual_adapter_provider_id",
                "actual_adapter_model_id",
                "backend",
                "role",
                "provider_role",
                "queue_depth",
                "tokens_per_second",
                "measurement_source",
                "cost_class",
                "prompt_tokens",
                "output_tokens",
                "usd_actual",
                "phase",
                "fallback_reason",
                "rejection_reason",
                "failure_reason",
                # The per-call failure family names its cause in `reason` (prompt_budget_exceeded,
                # circuit_open, a provider health string) and classifies it in `error_kind`. Neither
                # was allowlisted, so even under an underscored name the cause was dropped and the
                # user saw a failure with no attribution.
                "reason",
                "error_kind",
                "selected_provider_id",
                "selected_model",
                "ranked_candidates",
                "attempted",
                "error",
                "failover_used",
                "verifier_status",
                "verifier_provider_id",
                "verifier_model_id",
                "kv_cache_status",
                "backend_cache_proof",
                "speculative_status",
                "speculative_proof",
                "eagle_status",
                "eagle_proof",
                "mismatch",
            }
        }
        budget = event.get("prompt_budget")
        if isinstance(budget, dict):
            projected = {key: value for key, value in budget.items() if key in _PROMPT_BUDGET_TEXT_FIELDS}
            if projected:
                visible["prompt_budget"] = projected
        measured = event.get("token_usage")
        if isinstance(measured, dict):
            projected = {key: value for key, value in measured.items() if key in _TOKEN_USAGE_TEXT_FIELDS}
            if projected:
                visible["token_usage"] = projected
        return "VOOL_RUNTIME_EVENT " + json.dumps(visible, separators=(",", ":"), sort_keys=True) + "\n"
    message = str(event.get("message") or "").strip()
    return message + "\n" if message else ""


def stream_agent_with_events(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
    emit_task_events: bool = False,
    run_agent_provider: Callable[..., dict[str, Any]] | None = None,
    ingress_context: contextvars.Context | None = None,
) -> Iterator[bytes]:
    """The streamed turn, guaranteed to terminate the response even when it fails.

    Starlette commits status and headers BEFORE pulling the first item from a streaming body, so
    from that moment there is no way to send a 500 -- an exception here does not become an error the
    client can render, it truncates the body, and the browser reports a failed load with nothing to
    show. The buffered sibling in `core.web.api.service` wraps its work in try/except and returns a
    500; the streaming branch had no equivalent, and the served UI always streams.

    Measured contributors to exactly that: `format_provenance_footer` raising UnboundLocalError
    (fixed in 0cc98a05), and every unguarded raise site between the worker queue and the terminal
    chunk -- `task_event_line`, `_response_commit`, `release_gate.flush`, `json.dumps` on a payload
    holding anything non-serializable.

    So the failure is converted rather than prevented: whatever goes wrong, the client still
    receives a well-formed final `done` chunk carrying a safe message. A turn that fails becomes a
    turn that says it failed, which is renderable; a truncated stream is not.
    """

    from core.error_surface import safe_error_text

    last_chunk = b""
    try:
        for chunk in _stream_agent_with_events_inner(
            runtime,
            user_text,
            session_id=session_id,
            source_context=source_context,
            model=model,
            include_runtime_events=include_runtime_events,
            # A7 W4: the content channel carries committed answer bytes only, so a request for
            # runtime events is served on the typed `vool_event` channel -- the flag was accepted
            # and did nothing after the emitter gate landed (three provider lanes failed with no
            # cause reaching the client, measured 2026-09-07).
            emit_task_events=bool(emit_task_events or include_runtime_events),
            run_agent_provider=run_agent_provider,
            ingress_context=ingress_context,
        ):
            # Only the LAST chunk is remembered, and it is parsed only if something goes wrong.
            # The happy path stays O(1) per chunk, and the check cannot rot: a substring test was
            # tried first and silently never matched, because the payload serializes compactly
            # (`"done":true`, no space) while the test looked for `"done": true`.
            last_chunk = chunk
            yield chunk
    except GeneratorExit:
        # The client went away. Nothing to report to, and swallowing it would break generator
        # close semantics.
        raise
    except BaseException as exc:
        # Deliberately broad: a truncated body is worse for the user than any error text,
        # and this is the last frame that can still put bytes on the wire.
        terminated = False
        try:
            for line in last_chunk.decode("utf-8", errors="replace").splitlines():
                if line.strip() and json.loads(line).get("done") is True:
                    terminated = True
        except Exception:
            terminated = False
        if not terminated:
            # A7 W3: the exception handler is NOT a second answer author. The
            # terminal frame carries typed no-answer truth; failure detail rides
            # its own key, never as assistant content bytes.
            yield no_answer_terminal_line(
                _no_answer_terminal("turn_failed", safe_error_text(exc, prefix="This turn failed before it finished: "))
            )
            yield ollama_stream_chunk(
                model=model,
                content="",
                created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                done=True,
            )


def _stream_agent_with_events_inner(
    runtime: RuntimeServices,
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    model: str,
    include_runtime_events: bool = False,
    emit_task_events: bool = False,
    run_agent_provider: Callable[..., dict[str, Any]] | None = None,
    ingress_context: contextvars.Context | None = None,
) -> Iterator[bytes]:
    event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
    stream_context = dict(source_context or {})
    stream_id = ""
    stream_id = new_runtime_event_stream_id()
    stream_context["runtime_event_stream_id"] = stream_id

    # Per-turn cancellation: register a server-owned Event keyed by (session, client turn_id) and put
    # it on source_context so the router's _cancel_check actually stops the in-flight model/tool loop
    # (not just drops the HTTP stream).
    #
    # Registration is owned by the WORKER, not by this generator. Registered here, before the thread
    # starts, so a cancel racing the first instruction still finds something to set -- and released
    # in the worker's own `finally` below, on every exit path. It is deliberately NOT released in
    # this generator's `finally`: that fires when the CLIENT goes away, which since phase 2 is
    # ordinary (the user navigated to another chat) and says nothing about whether the turn is still
    # running. Releasing there made "can I stop this?" depend on where this generator happened to be
    # suspended at disconnect. See core/live_turns.py.
    from core.live_turns import register_turn, unregister_turn

    cancel_turn_id = str(stream_context.get("cancel_turn_id") or "").strip()
    if cancel_turn_id:
        stream_context["cancel_event"] = register_turn(session_id, cancel_turn_id)

    def sink(event: dict[str, Any]) -> None:
        event_queue.put(("event", dict(event)))

    def worker() -> None:
        def _body() -> None:
            try:
                agent_runner = run_agent_provider or run_agent
                result = agent_runner(runtime, user_text, session_id=session_id, source_context=stream_context)
                event_queue.put(("result", result))
            except Exception as exc:
                # Make it chat-safe HERE, where the exception object still exists, so the class name
                # (the genuinely useful, safe part) survives while paths/credentials do not.
                event_queue.put(("error", safe_error_text(exc)))
            finally:
                # The turn is genuinely over -- completed, failed or cancelled. This is the ONLY place
                # the live-turn entry is released, so it can neither leak nor disappear early.
                if cancel_turn_id:
                    unregister_turn(session_id, cancel_turn_id)

        # F-01 (independent-proof repair): execute the turn under the ingress
        # context captured at the accept door, so A0 request binding and the
        # A2 admission/execution identity ContextVars survive the thread
        # boundary (ContextVars do not cross thread creation on their own).
        # A Context object cannot be entered by two threads at once -- the
        # streaming iterator pulls under the original -- so the worker gets
        # its own copy.
        if ingress_context is not None:
            worker_context = ingress_context.copy()
            try:
                worker_context.run(_body)
            except BaseException:
                # _body already routes exceptions into the queue and releases the
                # live-turn entry in its own finally; a raise here can only come
                # from context machinery itself (before _body ever ran).
                if cancel_turn_id:
                    unregister_turn(session_id, cancel_turn_id)
                raise
        else:
            _body()

    if stream_id:
        register_runtime_event_sink(stream_id, sink)
    thread = threading.Thread(target=worker, name="vool-openclaw-stream", daemon=True)
    try:
        thread.start()
    except BaseException:
        # The worker never ran, so its `finally` never will: release here or the entry leaks and
        # the turn stays falsely cancellable forever.
        if cancel_turn_id:
            unregister_turn(session_id, cancel_turn_id)
        if stream_id:
            unregister_runtime_event_sink(stream_id)
        raise

    try:
        while True:
            kind, payload = event_queue.get()
            if kind == "event":
                event_payload = dict(payload or {})
                event_name = str(event_payload.get("event_type") or "").strip()
                if event_name == "model_output_chunk":
                    # A7 W4 hold-before-emission: speculative model deltas are
                    # NOT semantic answer bytes and never reach the content
                    # channel. The turn holds until finalization, then the
                    # committed canonical bytes are released in chunks below.
                    continue
                elif emit_task_events:
                    # Typed channel: progress events go out as their own `vool_event`
                    # line (not muxed into answer text). Unknown types map to None and
                    # are dropped, so no arbitrary text/reasoning can leak.
                    task_event = build_task_event(event_payload)
                    if task_event is not None:
                        yield task_event_line(task_event)
                    continue
                continue
            if kind == "error":
                # Never ship a raw exception into the chat bubble: str(exc) carries absolute
                # paths (operator username + layout) and anything the raising code interpolated.
                # A7 W3: no commit is synthesized OVER unguarded transport prose — the error
                # path ends as typed NO_ANSWER_TERMINAL with zero content frames.
                from core.finalization import no_answer_terminal

                yield no_answer_terminal_line(
                    no_answer_terminal(
                        turn_id=cancel_turn_id,
                        reason_code="stream_error",
                        detail=str(payload),
                    )
                )
                if emit_task_events:
                    yield task_event_line(
                        {
                            "type": "task.failed",
                            "raw_type": "stream_error",
                            "stage": "Failed safely",
                            "summary": "Stopped safely after an internal error",
                            "tool": None,
                            "status": "failed",
                        }
                    )
                break
            if kind == "result":
                result_payload = dict(payload or {})
                # A7 W4: finalization precedes ANY answer byte. The committed
                # canonical bytes are then released as chunk replay using the
                # proven ollama_stream_chunks framing (indistinguishable from
                # live streaming to legacy clients). No post-commit mutation:
                # the provenance footer travels in display_metadata, never as
                # answer-body content.
                response_commit = _response_commit(result_payload, source_context=stream_context)
                # A7 W6: delivery truth is created BEFORE the first transport
                # byte and is monotone. Once the terminal frame has been handed
                # to the transport the answer is DELIVERED; a crash mid-stream
                # honestly leaves ATTEMPTED_UNKNOWN.
                from core.finalization import (
                    DELIVERY_ATTEMPTED_UNKNOWN as _DELIV_ATTEMPTED,
                )
                from core.finalization import (
                    DELIVERY_DELIVERED as _DELIV_DONE,
                )
                from core.finalization import (
                    set_delivery_status as _set_delivery_status,
                )

                _fid = str(response_commit.get("finalization_id") or "")
                # Residue-3: delivery writers present the fence tuple when the
                # sealed payload carries it (onboarded lanes).
                _deliv_identity = (
                    result_payload.get("_execution_identity")
                    if isinstance(result_payload.get("_execution_identity"), dict)
                    else None
                )
                if _fid:
                    _set_delivery_status(
                        _fid, _DELIV_ATTEMPTED, execution_identity=_deliv_identity
                    )
                delivered = False
                # Operator Profile (P1): the chip / confirmation / "Used N preferences" frame
                # rides its own NDJSON record ahead of the answer chunks. Absent entirely on an
                # ordinary turn -- an ordinary stream is byte-identical.
                _profile_frame = None
                try:
                    from core.operator_profile_turn import profile_frame_for_result

                    _profile_frame = profile_frame_for_result(result_payload)
                except Exception:
                    _profile_frame = None
                if _profile_frame:
                    yield (json.dumps({"vool_profile": _profile_frame}, separators=(",", ":")) + "\n").encode("utf-8")
                try:
                    for chunk in ollama_stream_chunks(
                        result_payload,
                        model,
                        response_commit=response_commit,
                    ):
                        yield chunk
                    else:
                        delivered = True
                except (GeneratorExit, Exception):
                    # R-7 (A-6): a client disconnect / mid-stream crash is a
                    # typed FAILED_TRANSPORT — never a silent ATTEMPTED_UNKNOWN.
                    if _fid and not delivered:
                        from core.finalization import DELIVERY_FAILED_TRANSPORT

                        _set_delivery_status(
                            _fid,
                            DELIVERY_FAILED_TRANSPORT,
                            evidence_class="TRANSPORT_HANDOFF",
                            execution_identity=result_payload.get("_execution_identity")
                            if isinstance(result_payload.get("_execution_identity"), dict)
                            else None,
                        )
                    raise
                if _fid and delivered:
                    # R-0/A-5: stream fully drained to the transport = transport-
                    # handoff evidence, stated explicitly — no default upgrade.
                    _set_delivery_status(
                        _fid,
                        _DELIV_DONE,
                        evidence_class="TRANSPORT_HANDOFF",
                        execution_identity=_deliv_identity,
                    )
                if emit_task_events:
                    from core.finalization import CANCELLED_BEFORE_PUBLICATION

                    _committed = str((response_commit or {}).get("canonical_content") or "")
                    yield task_event_line(
                        _final_task_event(
                            result_payload,
                            cancelled=_committed.startswith(CANCELLED_BEFORE_PUBLICATION[:28]),
                        )
                    )
                break
    finally:
        # NOTE: the live-turn entry is deliberately NOT released here. This block runs when the
        # CLIENT stream ends -- including a plain navigation away from the chat -- and the worker
        # may well still be running. It releases only what the stream itself owns.
        if stream_id:
            unregister_runtime_event_sink(stream_id)
        # A generator abandoned by its consumer may be finalized by the worker that last held a
        # reference to it. Python forbids a thread from joining itself; shutdown then emitted an
        # ignored RuntimeError and obscured the real terminal event. The worker owns its own
        # lifecycle, so self-finalization has nothing to wait for.
        if thread is not threading.current_thread():
            thread.join(timeout=0.1)
