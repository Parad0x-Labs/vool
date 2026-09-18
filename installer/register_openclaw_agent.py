"""Register VOOL as an agent in the OpenClaw config (~/.openclaw/openclaw.json)."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.openclaw_locator import OpenClawPaths, discover_openclaw_paths

VOOL_AGENT_ID = "vool"
VOOL_PROVIDER_ID = "vool"
VOOL_MODEL_ID = "vool"
OPENCLAW_MEMORY_EMBEDDING_MODEL = "nomic-embed-text"
OPENCLAW_MEMORY_README = """# VOOL Local Memory

Workspace memory notes for OpenClaw live here.

Keep secrets, private keys, API tokens, and machine-local credentials out of this directory.
"""
DEFAULT_LOCAL_PROVIDER_TIMEOUT_SECONDS = 600


def _vool_api_url() -> str:
    return str(os.environ.get("VOOL_OPENCLAW_API_URL") or "http://127.0.0.1:11435").strip()


def _ollama_api_url() -> str:
    raw = str(os.environ.get("VOOL_RAW_OLLAMA_API_URL") or "").strip()
    if raw:
        return raw
    host = str(os.environ.get("OLLAMA_HOST") or "").strip()
    if host:
        if host.startswith(("http://", "https://")):
            return host
        return f"http://{host}"
    return "http://127.0.0.1:11434"


def _gateway_port() -> int:
    raw = str(os.environ.get("VOOL_OPENCLAW_GATEWAY_PORT") or "").strip()
    if not raw:
        return 18789
    try:
        return max(1, min(int(raw), 65535))
    except ValueError:
        return 18789


def _local_provider_timeout_seconds() -> int:
    raw = str(os.environ.get("VOOL_OPENCLAW_PROVIDER_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_LOCAL_PROVIDER_TIMEOUT_SECONDS
    try:
        return max(60, min(int(raw), 3600))
    except ValueError:
        return DEFAULT_LOCAL_PROVIDER_TIMEOUT_SECONDS


def _openclaw_home(paths: OpenClawPaths) -> Path:
    return paths.home


def _openclaw_config_path(paths: OpenClawPaths) -> Path:
    return paths.config_path


def _openclaw_workspace_dir(paths: OpenClawPaths) -> Path:
    return paths.workspace_dir


def _openclaw_agent_dir(paths: OpenClawPaths, agent_id: str = VOOL_AGENT_ID) -> Path:
    if agent_id == VOOL_AGENT_ID:
        return paths.agent_dir
    return _openclaw_home(paths) / "agents" / agent_id


def _openclaw_agent_runtime_dir(paths: OpenClawPaths, agent_id: str = VOOL_AGENT_ID) -> Path:
    if agent_id == VOOL_AGENT_ID:
        return paths.agent_runtime_dir
    return _openclaw_agent_dir(paths, agent_id) / "agent"


def _openclaw_compat_bridge_dir(paths: OpenClawPaths) -> Path:
    env = os.environ.get("OPENCLAW_BRIDGE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return paths.compat_bridge_dir


def _detect_best_model() -> str:
    """Probe hardware and return the best Ollama Qwen tag for this machine."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from core.hardware_tier import probe_machine, select_qwen_tier

        probe = probe_machine()
        tier = select_qwen_tier(probe)
        return f"ollama/{tier.ollama_tag}"
    except Exception:
        return "ollama/qwen2.5:7b"


def _normalize_model_tag(model_tag: str | None) -> str:
    raw = str(model_tag or "").strip()
    if not raw:
        return _detect_best_model()
    if "/" not in raw:
        return f"ollama/{raw}"
    return raw


def _runtime_model_name(model_tag: str) -> str:
    return model_tag.split("/", 1)[-1]


def _model_size_label(model_tag: str) -> str:
    model_name = _runtime_model_name(model_tag)
    if ":" not in model_name:
        return "7B"
    _, size = model_name.split(":", 1)
    return size.upper()


_REASONING_MODEL_MARKERS = (
    "thinking",
    "-r1",
    "deepseek-r1",
    "qwq",
    "o1",
    "o3",
)


def _is_reasoning_model(model_tag: str) -> bool:
    """Whether the underlying model actually does extended step-by-step reasoning.

    OpenClaw treats this as a real capability flag, not a display toggle: it
    drives the default "reasoning level" (on/off) it shows for the agent. A
    blanket False is correct for today's non-reasoning tags (gemma3, qwen2.5)
    but would silently misreport a genuinely reasoning-tuned tag (e.g. a
    qwen3 "-thinking" variant or deepseek-r1) as non-reasoning too.
    """
    name = _runtime_model_name(model_tag).lower()
    return any(marker in name for marker in _REASONING_MODEL_MARKERS)


def _model_display_name(model_tag: str) -> str:
    """Human-readable model identity for OpenClaw's model list, e.g. 'qwen2.5:7b (7B)'."""
    return f"{_runtime_model_name(model_tag)} ({_model_size_label(model_tag)})"


def _normalize_display_name(display_name: str | None) -> str:
    value = str(display_name or "").strip()
    return value[:40] if value else "VOOL"


def _base_openclaw_config(default_workspace: str) -> dict[str, Any]:
    gateway_port = _gateway_port()
    return {
        "agents": {
            "defaults": {
                "model": {
                    "primary": _detect_best_model(),
                },
                "workspace": default_workspace,
                # Compaction settings so a long session COMPACTS instead of hard-erroring
                # "Context overflow, use /reset". (The deeper levers for the fixed per-turn
                # overhead — the "full" tool-schema profile and the Ollama num_ctx window —
                # are tuned/validated on the live runtime.)
                "compaction": {
                    "mode": "safeguard",
                    # reserveTokens is the OpenClaw prompt-precheck output reserve: usable prompt
                    # budget = contextWindow - reserveTokens. The default 16384 left only 16384
                    # usable of the 32768 window, so a normal beginner chat overflowed ~turn 37.
                    # 8192 = the model's maxTokens (a full reply still fits) and roughly doubles it.
                    "reserveTokens": 8192,
                    # reserveTokensFloor bounds the *compaction* reserve: OpenClaw uses
                    # max(reserveTokens, reserveTokensFloor) for the compaction threshold and target
                    # (agent-settings.applyAgentCompactionSettingsFromConfig). Its default when
                    # absent is 20000, which forces compaction to fire at ~12768 tokens AND demand
                    # the compacted context fit under 12768 — usually impossible, so compaction
                    # reports "still exceeds target" and the next turn is blocked
                    # already_compacted_recently, i.e. no recovery. Pinning the floor to 8192 (=
                    # reserveTokens) keeps the compaction threshold/target at 24576, which a
                    # keepRecentTokens=6000 slice plus a small summary can actually reach.
                    "reserveTokensFloor": 8192,
                    "keepRecentTokens": 6000,
                },
                "maxConcurrent": 4,
                "subagents": {
                    "maxConcurrent": 8,
                },
                "memorySearch": _build_ollama_memory_search_config(),
            },
            "list": [],
        },
        "messages": {
            "ackReactionScope": "group-mentions",
        },
        "commands": {
            "bash": True,
            "native": "auto",
            "nativeSkills": "auto",
            "restart": True,
            "ownerDisplay": "raw",
        },
        "session": {
            "dmScope": "per-channel-peer",
        },
        "gateway": {
            "port": gateway_port,
            "mode": "local",
            "bind": "loopback",
            "auth": {
                "mode": "token",
                "token": secrets.token_hex(24),
            },
            "tailscale": {
                "mode": "off",
                "resetOnExit": False,
            },
        },
        "skills": {
            "install": {
                "nodeManager": "pnpm",
            }
        },
        "models": {
            "providers": {
                "ollama": _build_ollama_provider(_detect_best_model()),
            }
        },
        "tools": {
            "web": {
                "search": {
                    "enabled": False,
                },
            },
        },
    }


def _load_or_initialize_config(config_path: Path, *, paths: OpenClawPaths) -> dict[str, Any]:
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return _base_openclaw_config(default_workspace=str(_openclaw_workspace_dir(paths)))


def _ensure_config_defaults(cfg: dict[str, Any], *, paths: OpenClawPaths) -> dict[str, Any]:
    defaults = _base_openclaw_config(default_workspace=str(_openclaw_workspace_dir(paths)))
    agents = cfg.setdefault("agents", {})
    agent_defaults = agents.setdefault("defaults", {})
    default_model = agent_defaults.setdefault("model", {})
    default_model.setdefault("primary", defaults["agents"]["defaults"]["model"]["primary"])
    agent_defaults.setdefault("workspace", str(_openclaw_workspace_dir(paths)))
    compaction = agent_defaults.setdefault("compaction", {"mode": "safeguard"})
    if isinstance(compaction, dict):
        compaction.setdefault("mode", "safeguard")
        # Cap the prompt-precheck output reserve at 8192 (= the model's maxTokens) so usable prompt
        # budget is contextWindow - 8192, not the 16384 default that overflowed a beginner chat. Lower
        # an existing higher value on upgrade.
        try:
            existing_reserve = int(compaction.get("reserveTokens") or 0)
        except (TypeError, ValueError):
            existing_reserve = 0
        compaction["reserveTokens"] = min(existing_reserve, 8192) if existing_reserve else 8192
        # Pin the compaction reserve floor at <= 8192. OpenClaw derives the compaction
        # threshold/target from max(reserveTokens, reserveTokensFloor), and an ABSENT floor
        # defaults to 20000 -- which forces compaction to target ~12768 tokens and then block
        # already_compacted_recently with no recovery. Writing 8192 (never leaving it absent),
        # and lowering any stale higher value (e.g. an earlier 20000), keeps the effective
        # compaction reserve at 8192 so long sessions recover instead of hard-erroring.
        try:
            existing_floor = int(compaction.get("reserveTokensFloor"))
        except (TypeError, ValueError):
            existing_floor = None
        compaction["reserveTokensFloor"] = min(existing_floor, 8192) if existing_floor is not None else 8192
        compaction.setdefault("keepRecentTokens", 6000)
    agent_defaults.setdefault("maxConcurrent", 4)
    agent_defaults.setdefault("subagents", {"maxConcurrent": 8})
    _ensure_ollama_memory_search_config(cfg)
    if not isinstance(agents.get("list"), list):
        agents["list"] = []

    messages = cfg.setdefault("messages", {})
    messages.setdefault("ackReactionScope", "group-mentions")

    commands = cfg.setdefault("commands", {})
    commands.setdefault("bash", True)
    commands.setdefault("native", "auto")
    commands.setdefault("nativeSkills", "auto")
    commands.setdefault("restart", True)
    commands.setdefault("ownerDisplay", "raw")

    session = cfg.setdefault("session", {})
    session.setdefault("dmScope", "per-channel-peer")

    gateway = cfg.setdefault("gateway", {})
    gateway.setdefault("port", _gateway_port())
    gateway.setdefault("mode", "local")
    gateway.setdefault("bind", "loopback")
    gateway_auth = gateway.setdefault("auth", {})
    gateway_auth.setdefault("mode", "token")
    gateway_auth.setdefault("token", secrets.token_hex(24))
    gateway.setdefault("tailscale", {"mode": "off", "resetOnExit": False})

    skills = cfg.setdefault("skills", {})
    install = skills.setdefault("install", {})
    install.setdefault("nodeManager", "pnpm")

    models = cfg.setdefault("models", {})
    models.setdefault("providers", {})
    _ensure_ollama_provider(cfg, _detect_best_model())
    _ensure_local_only_tools_config(cfg)
    _remove_stale_ollama_plugin_config(cfg)

    cfg.setdefault("auth", {"profiles": {}})
    return cfg


def _build_main_agent_entry(cfg: dict[str, Any], *, paths: OpenClawPaths) -> dict[str, Any]:
    workspace = str(cfg.get("agents", {}).get("defaults", {}).get("workspace") or _openclaw_workspace_dir(paths))
    model_primary = str(
        cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary")
        or _detect_best_model()
    )
    return {
        "id": "main",
        "default": False,
        "name": "Main",
        "workspace": workspace,
        "model": {
            "primary": model_primary,
        },
    }


def _ensure_agent_list(cfg: dict[str, Any], *, paths: OpenClawPaths) -> list[dict[str, Any]]:
    agents = cfg.setdefault("agents", {})
    agent_list = agents.setdefault("list", [])
    typed_entries = [entry for entry in agent_list if isinstance(entry, dict)]
    if not typed_entries:
        typed_entries = [_build_main_agent_entry(cfg, paths=paths)]
    agents["list"] = typed_entries
    return typed_entries


def _build_agent_entry(
    model_tag: str,
    project_root: str,
    *,
    paths: OpenClawPaths,
    display_name: str = "VOOL",
) -> dict[str, Any]:
    normalized_name = _normalize_display_name(display_name)
    entry: dict[str, Any] = {
        "id": VOOL_AGENT_ID,
        "default": True,
        "name": normalized_name,
        "workspace": project_root or str(_openclaw_workspace_dir(paths)),
        "agentDir": str(_openclaw_agent_runtime_dir(paths)),
        "model": {
            "primary": f"{VOOL_PROVIDER_ID}/{VOOL_MODEL_ID}",
        },
        "identity": {
            "name": normalized_name,
            "emoji": "\u2205",
        },
        # VOOL is the reasoning provider and executes its OWN tools internally \u2014 it never emits
        # OpenClaw tool_calls, so OpenClaw's "full" tool schemas (~28-36k chars, per boundary-audit
        # telemetry) are dead weight that the model ignores yet still count against the context
        # precheck, overflowing an ordinary ~20-turn beginner chat. "minimal" is OpenClaw's own
        # recommended profile for an untrusted-input chat agent and drops that overhead.
        "tools": {
            "profile": "minimal",
        },
    }
    return entry


def _build_vool_provider(model_tag: str) -> dict[str, Any]:
    resolved_tag = _normalize_model_tag(model_tag)
    return {
        "baseUrl": _vool_api_url(),
        "api": "ollama",
        "models": [
            {
                "id": VOOL_MODEL_ID,
                "name": _model_display_name(resolved_tag),
                "reasoning": _is_reasoning_model(resolved_tag),
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 32768,
                "maxTokens": 8192,
            }
        ],
        "apiKey": "ollama-local",
        "timeoutSeconds": _local_provider_timeout_seconds(),
    }


def _build_ollama_provider(model_tag: str) -> dict[str, Any]:
    model_name = _runtime_model_name(model_tag)
    return {
        "baseUrl": _ollama_api_url(),
        "api": "ollama",
        "models": [
            {
                "id": model_name,
                "name": model_name,
                "reasoning": _is_reasoning_model(model_tag),
                "input": ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 32768,
                "maxTokens": 8192,
            }
        ],
        "apiKey": "ollama-local",
        "timeoutSeconds": _local_provider_timeout_seconds(),
    }


def _gateway_bind_env() -> str:
    return str(os.environ.get("VOOL_OPENCLAW_GATEWAY_BIND") or "").strip().lower()


def _gateway_custom_host_env() -> str:
    return str(os.environ.get("VOOL_OPENCLAW_GATEWAY_CUSTOM_HOST") or "").strip()


def _private_ipv4_candidates() -> list[str]:
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            ip = str(sock.getsockname()[0] or "").strip()
            if ip and not ip.startswith("127."):
                candidates.append(ip)
    except OSError:
        pass

    hostname = socket.gethostname()
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        infos = []
    for info in infos:
        ip = str(info[4][0] or "").strip()
        if not ip or ip.startswith("127."):
            continue
        candidates.append(ip)

    deduped: list[str] = []
    for ip in candidates:
        if ip not in deduped:
            deduped.append(ip)
    return deduped


def _ensure_vool_provider(cfg: dict[str, Any], model_tag: str) -> None:
    models = cfg.setdefault("models", {})
    providers = models.setdefault("providers", {})
    providers[VOOL_PROVIDER_ID] = _build_vool_provider(model_tag)


def _provider_looks_like_broken_ollama_alias(provider: Any) -> bool:
    if not isinstance(provider, dict):
        return False
    if str(provider.get("api") or "").strip() != "ollama":
        return False
    if str(provider.get("baseUrl") or "").strip() != _vool_api_url():
        return False
    models = provider.get("models")
    if not isinstance(models, list) or len(models) != 1:
        return False
    model = models[0]
    if not isinstance(model, dict):
        return False
    model_id = str(model.get("id") or "").strip()
    # `id` is the stable "vool" alias regardless of the underlying model; `name` is now
    # a descriptive label (e.g. "qwen2.5:7b (7B)") rather than a second "vool" literal,
    # so id alone (combined with the baseUrl check above) is the reliable signal here.
    return model_id == VOOL_MODEL_ID


def _ensure_ollama_provider(cfg: dict[str, Any], model_tag: str) -> None:
    models = cfg.setdefault("models", {})
    providers = models.setdefault("providers", {})
    current = providers.get("ollama")
    if current is None or _provider_looks_like_broken_ollama_alias(current):
        providers["ollama"] = _build_ollama_provider(model_tag)
    elif isinstance(current, dict):
        current.setdefault("timeoutSeconds", _local_provider_timeout_seconds())


def _build_ollama_memory_search_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "ollama",
        "model": OPENCLAW_MEMORY_EMBEDDING_MODEL,
        "fallback": "none",
        "remote": {
            "baseUrl": _ollama_api_url(),
            "apiKey": "ollama-local",
        },
    }


def _ensure_ollama_memory_search_config(cfg: dict[str, Any]) -> None:
    agents = cfg.setdefault("agents", {})
    defaults = agents.setdefault("defaults", {})
    memory_search = defaults.setdefault("memorySearch", {})
    if not isinstance(memory_search, dict):
        memory_search = {}
        defaults["memorySearch"] = memory_search
    memory_search.update(_build_ollama_memory_search_config())


def _ensure_local_only_tools_config(cfg: dict[str, Any]) -> None:
    tools = cfg.setdefault("tools", {})
    web = tools.setdefault("web", {})
    search = web.setdefault("search", {})
    if not isinstance(search, dict):
        search = {}
        web["search"] = search
    search["enabled"] = False
    search.pop("provider", None)


def _remove_stale_ollama_plugin_config(cfg: dict[str, Any]) -> None:
    plugins = cfg.get("plugins")
    if not isinstance(plugins, dict):
        return
    entries = plugins.get("entries")
    if isinstance(entries, dict):
        entries.pop("ollama", None)
        if not entries:
            plugins.pop("entries", None)
    if not plugins:
        cfg.pop("plugins", None)


def _apply_gateway_bind_overrides(cfg: dict[str, Any]) -> None:
    bind_mode = _gateway_bind_env()
    if not bind_mode:
        return

    gateway = cfg.setdefault("gateway", {})
    gateway["bind"] = bind_mode
    gateway.setdefault("mode", "local")
    gateway.setdefault("port", _gateway_port())
    gateway_auth = gateway.setdefault("auth", {})
    gateway_auth.setdefault("mode", "token")
    gateway_auth.setdefault("token", secrets.token_hex(24))

    port = int(gateway.get("port") or _gateway_port())
    control_ui = gateway.setdefault("controlUi", {})
    current_allowed = control_ui.get("allowedOrigins")
    origins: list[str] = []
    if isinstance(current_allowed, list):
        origins.extend(str(item).strip() for item in current_allowed if str(item).strip())

    for origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
        if origin not in origins:
            origins.append(origin)

    custom_host = _gateway_custom_host_env()
    if bind_mode == "custom" and custom_host:
        gateway["customBindHost"] = custom_host
        origin = f"http://{custom_host}:{port}"
        if origin not in origins:
            origins.append(origin)
    elif bind_mode == "lan":
        for ip in _private_ipv4_candidates():
            origin = f"http://{ip}:{port}"
            if origin not in origins:
                origins.append(origin)

    if bind_mode != "loopback":
        control_ui["allowedOrigins"] = origins


def _create_auth_profiles(agent_agent_dir: Path) -> None:
    """Create auth-profiles.json so OpenClaw gateway can authenticate with Ollama."""
    auth_path = agent_agent_dir / "auth-profiles.json"
    if auth_path.is_file():
        return
    payload = {
        "version": 1,
        "profiles": {
            f"{VOOL_PROVIDER_ID}:local": {
                "type": "api_key",
                "provider": VOOL_PROVIDER_ID,
                "key": "ollama-local",
            }
        },
    }
    auth_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Auth profile created at {auth_path}")


def _create_models_json(agent_agent_dir: Path, model_tag: str) -> None:
    """Create models.json pointing at the VOOL API server (not raw Ollama)."""
    models_path = agent_agent_dir / "models.json"
    payload = {"providers": {VOOL_PROVIDER_ID: _build_vool_provider(model_tag)}}
    models_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Models config created at {models_path}")


def _write_bridge_launchers(base_dir: Path, project_root: str) -> None:
    root = str(project_root or "").strip()
    if not root:
        return

    if os.name == "nt":
        start_target = Path(root) / "OpenClaw_VOOL.bat"
        chat_target = Path(root) / "Talk_To_VOOL.bat"
        (base_dir / "Start_VOOL.bat").write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            f'call "{start_target}"\r\n'
            "endlocal\r\n",
            encoding="utf-8",
        )
        (base_dir / "Talk_To_VOOL.bat").write_text(
            "@echo off\r\n"
            "setlocal\r\n"
            f'call "{chat_target}"\r\n'
            "endlocal\r\n",
            encoding="utf-8",
        )
        return

    start_target = Path(root) / "OpenClaw_VOOL.sh"
    chat_target = Path(root) / "Talk_To_VOOL.sh"
    for filename, target in (("Start_VOOL.sh", start_target), ("Talk_To_VOOL.sh", chat_target)):
        path = base_dir / filename
        path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'exec bash "{target}" "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)


def _thinking_mode_enabled() -> bool:
    """VOOL's own 'show your workflow/thinking' preference, distinct from the
    per-model `reasoning` capability flag sent to OpenClaw's provider config."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from core.user_preferences import load_preferences

        return bool(load_preferences().show_workflow)
    except Exception:
        return False


def _write_agent_metadata(
    project_root: str,
    vool_home: str,
    model_tag: str,
    display_name: str,
    *,
    paths: OpenClawPaths,
) -> None:
    agent_dir = _openclaw_agent_dir(paths)
    agent_agent_dir = _openclaw_agent_runtime_dir(paths)
    agent_agent_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "id": VOOL_AGENT_ID,
        "name": _normalize_display_name(display_name),
        "type": "external_bridge",
        "entrypoints": {
            "start": "Start_VOOL.bat" if os.name == "nt" else "Start_VOOL.sh",
            "chat": "Talk_To_VOOL.bat" if os.name == "nt" else "Talk_To_VOOL.sh",
        },
        "runtime_home": vool_home or "",
        "project_root": project_root or "",
        "api_url": _vool_api_url(),
        "runtime_model": model_tag,
        "thinking_mode_enabled": _thinking_mode_enabled(),
    }
    meta_path = agent_dir / "openclaw.agent.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    _write_bridge_launchers(agent_dir, project_root)

    _create_auth_profiles(agent_agent_dir)
    _create_models_json(agent_agent_dir, model_tag)


def _write_compat_bridge(
    project_root: str,
    vool_home: str,
    model_tag: str,
    display_name: str,
    *,
    paths: OpenClawPaths,
) -> None:
    bridge_dir = _openclaw_compat_bridge_dir(paths)
    bridge_agent_dir = bridge_dir / "agent"
    bridge_agent_dir.mkdir(parents=True, exist_ok=True)
    ext = ".bat" if os.name == "nt" else ".sh"
    payload = {
        "id": VOOL_AGENT_ID,
        "name": _normalize_display_name(display_name),
        "type": "external_bridge",
        "entrypoints": {
            "start": f"Start_VOOL{ext}",
            "chat": f"Talk_To_VOOL{ext}",
        },
        "runtime_home": vool_home or "",
        "project_root": project_root or "",
        "api_url": _vool_api_url(),
        "runtime_model": model_tag,
        "thinking_mode_enabled": _thinking_mode_enabled(),
    }
    (bridge_dir / "openclaw.agent.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_bridge_launchers(bridge_dir, project_root)
    _create_auth_profiles(bridge_agent_dir)
    _create_models_json(bridge_agent_dir, model_tag)


def _ensure_workspace_memory_seed(workspace_root: str) -> None:
    root = str(workspace_root or "").strip()
    if not root:
        return
    try:
        memory_dir = Path(root) / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        readme = memory_dir / "README.md"
        if not readme.exists():
            readme.write_text(OPENCLAW_MEMORY_README, encoding="utf-8")
    except OSError:
        return


def _backup_existing_config(config_path: Path) -> None:
    if not config_path.is_file():
        return
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    try:
        shutil.copy2(config_path, backup_path)
    except Exception:
        return


# OpenClaw seeds a default TOOLS.md into the agent workspace with camera/SSH/HomePod *examples*. The
# local beginner model parrots them as real capabilities (fabricated_tools_environment finding). Replace
# that default with a grounded one — but only when the file is missing or still the fake default, never
# a user-customized file.
_GROUNDED_TOOLS_MD = """# TOOLS.md - Your Local Notes

This file is for **your own** environment-specific notes - things unique to your setup that you choose to add. It is **empty by default**, and a fresh install has nothing here.

VOOL's actual capabilities are its built-in typed tool registry - read-only file and machine inspection, web fetch / search / browser, `.null` registration, and x402 payment preview. This notes file does **not** grant any new powers.

## Grounding rule for the assistant

> VOOL does NOT have cameras, SSH hosts, smart-home speakers, home-network devices, or a physical environment by default. Do not invent, list, or claim any such tools or devices, and do not present example configurations as if they exist. If this file has no user-added entries, there are none configured - say so plainly instead of fabricating a setup.

## What you (the user) can add here (optional)

Only environment specifics you actually have and want the assistant to remember - for example a device nickname, a preferred TTS voice, or a host alias you personally use. Leave it empty if you have none.
"""


def _seed_grounded_tools_md(workspace_root: str) -> None:
    root = str(workspace_root or "").strip()
    if not root:
        return
    try:
        target = Path(root) / "TOOLS.md"
        if target.exists():
            existing = target.read_text(encoding="utf-8", errors="ignore")
            already_grounded = "Grounding rule for the assistant" in existing
            is_fake_default = any(m in existing for m in ("living-room", "home-server", "HomePod", "wide angle"))
            if already_grounded or not is_fake_default:
                return  # already grounded, or a user-customized file - leave it alone
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_GROUNDED_TOOLS_MD, encoding="utf-8")
    except Exception:
        pass


def register(
    project_root: str = "",
    vool_home: str = "",
    model_tag: str = "",
    display_name: str = "",
    *,
    openclaw_home: str = "",
    openclaw_config_path: str = "",
) -> bool:
    paths = discover_openclaw_paths(
        explicit_home=openclaw_home or None,
        explicit_config_path=openclaw_config_path or None,
        create_default=True,
    )
    config_path = _openclaw_config_path(paths)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _openclaw_workspace_dir(paths).mkdir(parents=True, exist_ok=True)

    cfg = _ensure_config_defaults(_load_or_initialize_config(config_path, paths=paths), paths=paths)
    normalized_model_tag = _normalize_model_tag(model_tag)
    normalized_display_name = _normalize_display_name(display_name)
    _ensure_vool_provider(cfg, normalized_model_tag)
    _apply_gateway_bind_overrides(cfg)
    agent_list = _ensure_agent_list(cfg, paths=paths)

    updated_list: list[dict[str, Any]] = []
    saw_vool = False
    for existing in agent_list:
        entry = dict(existing)
        if str(entry.get("id", "")).lower() == VOOL_AGENT_ID:
            saw_vool = True
            updated_list.append(
                _build_agent_entry(
                    normalized_model_tag,
                    project_root,
                    paths=paths,
                    display_name=normalized_display_name,
                )
            )
        else:
            entry["default"] = False
            updated_list.append(entry)

    if not saw_vool:
        updated_list.insert(
            0,
            _build_agent_entry(
                normalized_model_tag,
                project_root,
                paths=paths,
                display_name=normalized_display_name,
            ),
        )

    cfg["agents"]["list"] = updated_list

    agent_defaults = cfg["agents"]["defaults"]
    default_model = agent_defaults.setdefault("model", {})
    default_model.setdefault("primary", normalized_model_tag)
    agent_defaults.setdefault("workspace", str(_openclaw_workspace_dir(paths)))

    _backup_existing_config(config_path)
    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"VOOL registered in {config_path}")

    # Ground the agent-workspace TOOLS.md so the model stops fabricating a camera/SSH environment.
    # Target the OpenClaw agent WORKSPACE (the thing this replaces), never project_root: in a
    # packaged app project_root is the read-only .app source tree, and a running bundle must
    # stay byte-identical.
    _seed_grounded_tools_md(str(_openclaw_workspace_dir(paths)))

    _write_agent_metadata(
        project_root=project_root,
        vool_home=vool_home,
        model_tag=normalized_model_tag,
        display_name=normalized_display_name,
        paths=paths,
    )
    _write_compat_bridge(
        project_root=project_root,
        vool_home=vool_home,
        model_tag=normalized_model_tag,
        display_name=normalized_display_name,
        paths=paths,
    )
    # Seed the workspace memory README in the OpenClaw agent WORKSPACE, never project_root:
    # in a packaged app project_root is the read-only .app source tree, and a running bundle
    # must stay byte-identical.
    _ensure_workspace_memory_seed(str(_openclaw_workspace_dir(paths)))
    print(f"Agent directory created at {_openclaw_agent_dir(paths)}")
    return True


if __name__ == "__main__":
    proj = sys.argv[1] if len(sys.argv) > 1 else ""
    home = sys.argv[2] if len(sys.argv) > 2 else ""
    model = sys.argv[3] if len(sys.argv) > 3 else ""
    name = sys.argv[4] if len(sys.argv) > 4 else ""
    ok = register(project_root=proj, vool_home=home, model_tag=model, display_name=name)
    raise SystemExit(0 if ok else 1)
