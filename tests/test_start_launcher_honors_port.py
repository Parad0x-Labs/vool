"""The generated Start_VOOL.sh honors an exported API port in BOTH start paths.

Regression for the native-bundle launch defect (Goal 2 native gate, 2026-09-18): the
interactive (non-supervised) exec started ``apps.vool_api_server`` with NO ``--port``, so the
``VOOL_OPENCLAW_API_PORT`` the script itself exports at the top was silently dropped and every
wrapper/acceptance launch that dedicated a port bound the canonical 11435 instead (reproduced:
the wrapper-mode native bundle with port 11479 exported bound 11435 -- beside a live owner
runtime that is an attachment hazard, and it starves the supervisor polling the dedicated
origin). The supervised loop already passed the port; these pins hold BOTH generated paths.

The launcher's generated body is extracted from the installer template's quoted heredocs
(``LAUNCHER_HEAD`` + the appended tail), which is byte-for-byte what ``write_launcher`` emits.
"""
from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = PROJECT_ROOT / "installer" / "install_vool.sh"


def _launcher_template_body() -> str:
    """The generated Start_VOOL.sh content exactly as write_launcher emits it: the quoted
    LAUNCHER_HEAD heredoc followed by the unquoted EOF heredoc (whose ${...} escapes expand to
    the same ${...} in the emitted script)."""
    text = INSTALLER.read_text(encoding="utf-8")
    head = re.search(r"<<'LAUNCHER_HEAD'\n(.*?)\nLAUNCHER_HEAD\n", text, re.S)
    assert head, "LAUNCHER_HEAD heredoc not found in write_launcher"
    # the appended tail is the FIRST unquoted <<EOF heredoc after the head; the function span
    # cannot be cut on '^}' because the heredoc content itself defines bash functions
    tail = re.search(r'cat >>"\$\{target_path\}" <<EOF\n(.*?)\nEOF\n', text[head.end():], re.S)
    assert tail, "the launcher tail heredoc not found in write_launcher"
    # the tail heredoc is UNQUOTED: '\$' in the template emits '$' in the generated script
    return head.group(1) + "\n" + tail.group(1).replace("\\$", "$")


def test_every_api_server_start_carries_the_port_variable():
    body = _launcher_template_body()
    starts = [ln.strip() for ln in body.splitlines() if "apps.vool_api_server" in ln]
    assert len(starts) >= 2, f"expected the supervised and interactive starts, got: {starts}"
    for line in starts:
        assert "VOOL_OPENCLAW_API_PORT" in line, f"port variable missing from: {line!r}"


def test_the_interactive_exec_passes_the_port():
    body = _launcher_template_body()
    interactive = [ln for ln in body.splitlines() if ln.startswith("exec ") and "vool_api_server" in ln]
    assert interactive, "the generated launcher must exec the API server interactively"
    for line in interactive:
        assert '--port "${VOOL_OPENCLAW_API_PORT}"' in line, (
            f"an interactive exec of the API server drops the exported port: {line!r}"
        )


def test_the_announced_origin_follows_the_port():
    body = _launcher_template_body()
    announce = [ln for ln in body.splitlines() if "OpenClaw connects to" in ln]
    assert announce, "the launcher announces its origin"
    for line in announce:
        assert "VOOL_OPENCLAW_API_URL" in line, (
            f"the announcement hard-codes the canonical origin while the port is configurable: {line!r}"
        )
