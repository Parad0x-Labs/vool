from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_uninstall_script_tracks_runtime_install_and_openclaw_paths() -> None:
    script = (PROJECT_ROOT / "installer" / "uninstall_vool_local.sh").read_text(encoding="utf-8")

    assert 'RUNTIME_HOME="${VOOL_HOME:-$HOME/.vool_runtime}"' in script
    assert 'INSTALL_ROOT="${VOOL_INSTALL_ROOT:-$HOME/vool-local}"' in script
    assert 'OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw-default}"' in script
    assert 'OPENCLAW_AGENT_DIR="${VOOL_OPENCLAW_AGENT_DIR:-$HOME/.openclaw/agents/main/agent/vool}"' in script
    assert 'LAUNCH_AGENT_PATH="${VOOL_LAUNCH_AGENT_PATH:-$HOME/Library/LaunchAgents/ai.vool.runtime.plist}"' in script
    assert 'pkill -f "apps.vool_api_server"' in script
    assert 'pkill -f "llama_cpp.server"' in script
    assert 'launchctl bootout "gui/${UID}" "${LAUNCH_AGENT_PATH}"' in script
    assert 'trash_or_remove "${target}"' in script
    assert 'remove_empty_tree() {' in script
    assert 'remove_empty_tree "${INSTALL_ROOT}"' in script
    assert 'remove_empty_tree "${RUNTIME_HOME}"' in script
