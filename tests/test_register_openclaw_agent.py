from __future__ import annotations

from installer import register_openclaw_agent as roa


def test_seed_grounded_tools_md_replaces_fake_default_only(tmp_path) -> None:
    tools = tmp_path / "TOOLS.md"
    # missing -> grounded
    roa._seed_grounded_tools_md(str(tmp_path))
    assert "Grounding rule for the assistant" in tools.read_text(encoding="utf-8")
    assert "does NOT have cameras" in tools.read_text(encoding="utf-8")
    # OpenClaw's fake-example default -> replaced
    tools.write_text("### Cameras\n- living-room -> Main area, 180 wide angle\n", encoding="utf-8")
    roa._seed_grounded_tools_md(str(tmp_path))
    assert "living-room" not in tools.read_text(encoding="utf-8")
    # a user-customized file is preserved, not clobbered
    tools.write_text("### My setup\n- my-desk-note\n", encoding="utf-8")
    roa._seed_grounded_tools_md(str(tmp_path))
    assert "my-desk-note" in tools.read_text(encoding="utf-8")


def test_vool_agent_uses_minimal_tool_profile() -> None:
    # VOOL executes its own tools and emits no OpenClaw tool_calls, so the "full" schema (~28-36k
    # chars) is dead weight that overflows the context precheck. "minimal" drops it to ~89 chars and
    # is OpenClaw's own recommended profile for an untrusted-input chat agent.
    from pathlib import Path

    home = Path("/tmp/oc-min-test/.openclaw")
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "vool",
        agent_runtime_dir=home / "agents" / "vool" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "vool",
        source="test",
        discovered_existing=False,
    )
    entry = roa._build_agent_entry("ollama/qwen2.5:7b", str(home / "workspace"), paths=paths)
    assert entry["tools"]["profile"] == "minimal"


def test_base_config_caps_compaction_reserve_to_widen_budget() -> None:
    # Usable prompt budget = contextWindow - reserveTokens. The 16384 default left only half the 32768
    # window and overflowed a beginner chat; reserveTokens must be capped at the model maxTokens (8192).
    cfg = roa._base_openclaw_config(default_workspace="/tmp/ws")
    compaction = cfg["agents"]["defaults"]["compaction"]
    assert compaction["mode"] == "safeguard"
    assert compaction["reserveTokens"] <= 8192
    # OpenClaw derives the compaction threshold/target from max(reserveTokens, reserveTokensFloor);
    # an absent floor defaults to 20000 and blocks recovery. The floor must be present and keep the
    # effective compaction reserve capped at 8192.
    assert "reserveTokensFloor" in compaction
    assert max(compaction["reserveTokens"], compaction["reserveTokensFloor"]) <= 8192
    assert compaction["keepRecentTokens"] >= 1


def test_ensure_config_defaults_backfills_compaction_reserve_floor(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")
    home = tmp_path / ".openclaw"
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "vool",
        agent_runtime_dir=home / "agents" / "vool" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "vool",
        source="test",
        discovered_existing=False,
    )
    cfg = {"agents": {"defaults": {"compaction": {"mode": "safeguard"}}}}
    repaired = roa._ensure_config_defaults(cfg, paths=paths)
    compaction = repaired["agents"]["defaults"]["compaction"]
    assert compaction["reserveTokens"] <= 8192
    # An absent floor must be backfilled (not left to OpenClaw's 20000 default) so the effective
    # compaction reserve stays 8192 and long sessions recover.
    assert compaction["reserveTokensFloor"] == 8192
    assert max(compaction["reserveTokens"], compaction["reserveTokensFloor"]) <= 8192
    assert compaction["keepRecentTokens"] >= 1


def test_ensure_config_defaults_lowers_stale_reserve_and_floor(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")
    home = tmp_path / ".openclaw"
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "vool",
        agent_runtime_dir=home / "agents" / "vool" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "vool",
        source="test",
        discovered_existing=False,
    )
    # A leftover high reserveTokens and a stale high reserveTokensFloor (e.g. 20000) from an older
    # config must both be lowered on upgrade so the effective compaction reserve is 8192.
    cfg = {
        "agents": {"defaults": {"compaction": {"mode": "safeguard", "reserveTokens": 16384, "reserveTokensFloor": 20000}}}
    }
    compaction = roa._ensure_config_defaults(cfg, paths=paths)["agents"]["defaults"]["compaction"]
    assert compaction["reserveTokens"] <= 8192
    assert compaction["reserveTokensFloor"] <= 8192
    assert max(compaction["reserveTokens"], compaction["reserveTokensFloor"]) <= 8192


def test_build_vool_provider_honors_api_url_override(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OPENCLAW_API_URL", "http://127.0.0.1:21435")

    provider = roa._build_vool_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:21435"
    assert provider["models"][0]["id"] == "vool"
    assert provider["timeoutSeconds"] == 600


def test_build_vool_provider_reports_real_model_name_not_generic_alias() -> None:
    # OpenClaw's status bar renders this per-model `name`; it must show the actual
    # underlying model + size, not the constant "vool" (the id stays "vool" for
    # routing, but the name is what a human sees).
    provider = roa._build_vool_provider("ollama/qwen2.5:7b")

    model = provider["models"][0]
    assert model["id"] == "vool"
    assert model["name"] == "qwen2.5:7b (7B)"


def test_build_vool_and_ollama_provider_report_reasoning_false_for_non_reasoning_models() -> None:
    assert roa._build_vool_provider("ollama/gemma3:4b")["models"][0]["reasoning"] is False
    assert roa._build_ollama_provider("ollama/qwen2.5:7b")["models"][0]["reasoning"] is False


def test_build_vool_and_ollama_provider_report_reasoning_true_for_reasoning_models() -> None:
    # Previously this was hardcoded False for every model, which meant a genuinely
    # reasoning-tuned tag would still (incorrectly) report reasoning support as off.
    assert roa._build_vool_provider("ollama/deepseek-r1:14b")["models"][0]["reasoning"] is True
    assert roa._build_ollama_provider("ollama/qwen3:8b-thinking")["models"][0]["reasoning"] is True


def test_thinking_mode_enabled_reflects_show_workflow_preference(monkeypatch) -> None:
    from core.user_preferences import UserPreferences

    monkeypatch.setattr(
        "core.user_preferences.load_preferences",
        lambda: UserPreferences(show_workflow=True),
    )
    assert roa._thinking_mode_enabled() is True

    monkeypatch.setattr(
        "core.user_preferences.load_preferences",
        lambda: UserPreferences(show_workflow=False),
    )
    assert roa._thinking_mode_enabled() is False


def test_thinking_mode_enabled_defaults_false_on_read_failure(monkeypatch) -> None:
    def _boom():
        raise OSError("no runtime home")

    monkeypatch.setattr("core.user_preferences.load_preferences", _boom)
    assert roa._thinking_mode_enabled() is False


def test_ensure_ollama_provider_still_repairs_broken_vool_alias_after_name_change(monkeypatch) -> None:
    # Regression guard: _provider_looks_like_broken_ollama_alias must keep detecting
    # a broken alias by `id` alone now that `name` is a descriptive label rather than
    # a second literal "vool".
    monkeypatch.setenv("VOOL_OPENCLAW_API_URL", "http://127.0.0.1:21435")
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:11434")
    cfg = {
        "models": {
            "providers": {
                "ollama": roa._build_vool_provider("ollama/qwen2.5:7b"),
            }
        }
    }

    roa._ensure_ollama_provider(cfg, "ollama/qwen2.5:7b")

    provider = cfg["models"]["providers"]["ollama"]
    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"


def test_build_ollama_provider_uses_raw_ollama_host(monkeypatch) -> None:
    monkeypatch.delenv("VOOL_RAW_OLLAMA_API_URL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")

    provider = roa._build_ollama_provider("ollama/qwen2.5:7b")

    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"
    assert provider["timeoutSeconds"] == 600


def test_local_provider_timeout_honors_bounded_override(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OPENCLAW_PROVIDER_TIMEOUT_SECONDS", "1200")

    assert roa._build_vool_provider("ollama/qwen2.5:7b")["timeoutSeconds"] == 1200

    monkeypatch.setenv("VOOL_OPENCLAW_PROVIDER_TIMEOUT_SECONDS", "10")

    assert roa._build_vool_provider("ollama/qwen2.5:7b")["timeoutSeconds"] == 60


def test_gateway_port_honors_override(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OPENCLAW_GATEWAY_PORT", "28790")
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")

    cfg = roa._base_openclaw_config("/tmp/workspace")

    assert cfg["gateway"]["port"] == 28790
    assert cfg["models"]["providers"]["ollama"]["models"][0]["id"] == "qwen2.5:7b"


def test_base_openclaw_config_disables_web_search_for_local_only(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")

    cfg = roa._base_openclaw_config("/tmp/workspace")

    assert cfg["tools"]["web"]["search"] == {"enabled": False}
    assert cfg["agents"]["defaults"]["memorySearch"] == {
        "enabled": True,
        "provider": "ollama",
        "model": "nomic-embed-text",
        "fallback": "none",
        "remote": {
            "baseUrl": "http://127.0.0.1:11434",
            "apiKey": "ollama-local",
        },
    }


def test_ensure_config_defaults_repairs_invalid_local_search_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")
    home = tmp_path / ".openclaw"
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "vool",
        agent_runtime_dir=home / "agents" / "vool" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "vool",
        source="test",
        discovered_existing=False,
    )
    cfg = {
        "tools": {
            "web": {
                "search": {
                    "enabled": True,
                    "provider": "ollama",
                },
            },
        },
        "plugins": {
            "entries": {
                "ollama": {
                    "enabled": True,
                },
            },
        },
    }

    repaired = roa._ensure_config_defaults(cfg, paths=paths)

    assert repaired["tools"]["web"]["search"] == {"enabled": False}
    assert repaired["agents"]["defaults"]["memorySearch"]["provider"] == "ollama"
    assert repaired["agents"]["defaults"]["memorySearch"]["model"] == "nomic-embed-text"
    assert repaired["agents"]["defaults"]["memorySearch"]["fallback"] == "none"
    assert "plugins" not in repaired


def test_ensure_config_defaults_adds_timeout_to_existing_ollama_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OLLAMA_MODEL", "qwen2.5:7b")
    home = tmp_path / ".openclaw"
    paths = roa.OpenClawPaths(
        home=home,
        config_path=home / "openclaw.json",
        workspace_dir=home / "workspace",
        agent_dir=home / "agents" / "vool",
        agent_runtime_dir=home / "agents" / "vool" / "agent",
        compat_bridge_dir=home / "agents" / "main" / "agent" / "vool",
        source="test",
        discovered_existing=False,
    )
    cfg = {
        "models": {
            "providers": {
                "ollama": {
                    "baseUrl": "http://127.0.0.1:11434",
                    "api": "ollama",
                    "models": [{"id": "qwen2.5:7b", "name": "qwen2.5:7b"}],
                    "apiKey": "ollama-local",
                },
            },
        },
    }

    repaired = roa._ensure_config_defaults(cfg, paths=paths)

    assert repaired["models"]["providers"]["ollama"]["timeoutSeconds"] == 600


def test_workspace_memory_seed_creates_ignored_openclaw_memory_readme(tmp_path) -> None:
    roa._ensure_workspace_memory_seed(str(tmp_path))

    readme = tmp_path / "memory" / "README.md"
    assert readme.is_file()
    assert "Workspace memory notes for OpenClaw live here." in readme.read_text(encoding="utf-8")


def test_ensure_ollama_provider_repairs_broken_vool_alias(monkeypatch) -> None:
    monkeypatch.setenv("VOOL_OPENCLAW_API_URL", "http://127.0.0.1:21435")
    monkeypatch.setenv("VOOL_RAW_OLLAMA_API_URL", "http://127.0.0.1:11434")
    cfg = {
        "models": {
            "providers": {
                "ollama": roa._build_vool_provider("ollama/qwen2.5:7b"),
            }
        }
    }

    roa._ensure_ollama_provider(cfg, "ollama/qwen2.5:7b")

    provider = cfg["models"]["providers"]["ollama"]
    assert provider["baseUrl"] == "http://127.0.0.1:11434"
    assert provider["models"][0]["id"] == "qwen2.5:7b"
