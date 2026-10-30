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
    assert 'launchctl bootout "gui/${UID}" "${LAUNCH_AGENT_PATH}"' in script
    assert 'trash_or_remove "${target}"' in script
    assert 'remove_empty_tree() {' in script
    assert 'remove_empty_tree "${INSTALL_ROOT}"' in script
    assert 'remove_empty_tree "${RUNTIME_HOME}"' in script


def test_uninstall_script_stops_only_a_server_proven_ours_and_first() -> None:
    script = (PROJECT_ROOT / "installer" / "uninstall_vool_local.sh").read_text(encoding="utf-8")

    # The blanket command-line kills are gone; only the ownership-checked helper stops things.
    assert "pkill" not in script
    assert "uninstall_local_runtime.py" in script
    assert '--runtime-home "${RUNTIME_HOME}"' in script
    assert '--install-root "${INSTALL_ROOT}"' in script
    assert "stop_runtime_processes" in script
    # The KeepAlive agent goes out first so it cannot restart what the stop step ends.
    assert script.index("bootout_launch_agent\n") < script.index("stop_runtime_processes\n")
    # A stop that cannot be completed refuses with plain words and removes nothing.
    assert "so nothing was removed" in script
    assert "Close VOOL and run this uninstall again." in script


def test_uninstall_script_keeps_chats_by_default_and_offers_remove_data() -> None:
    script = (PROJECT_ROOT / "installer" / "uninstall_vool_local.sh").read_text(encoding="utf-8")

    assert "Your chats and settings were kept, in: ${RUNTIME_HOME}" in script
    assert "A fresh VOOL install will find them there again." in script
    assert "To delete them as well, run this uninstall again with --remove-data" in script
    assert "--remove-data)" in script
    assert "REMOVE_DATA=1" in script
    assert "Delete your saved chats and settings in ${RUNTIME_HOME} too?" in script
    assert "Deleted your saved chats and settings." in script
    # The kept runtime home and Ollama's own models are not removal targets.
    assert '    "${RUNTIME_HOME}"' not in script
    assert '    "${OLLAMA_MODELS_DIR}"' not in script


def test_stop_helper_reads_command_lines_whole_and_shares_the_linux_refusal() -> None:
    helper = (PROJECT_ROOT / "installer" / "uninstall_local_runtime.py").read_text(encoding="utf-8")
    linux = (PROJECT_ROOT / "installer" / "bundle" / "linux" / "install_bundle.py").read_text(encoding="utf-8")

    # ps(1) folds long argument lists unless told not to; both uninstalls read whole lines.
    assert '"-ww"' in helper
    assert '"ps", "-ww", "-p"' in linux
    # The plain refusal wording is shared, not duplicated by hand.
    assert "from installer.bundle.linux.install_bundle import" in helper
    assert "CANNOT_STOP_MESSAGE" in helper
