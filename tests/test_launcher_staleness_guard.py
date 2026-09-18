"""Native launcher contract: the long-lived host owns an exact, fail-closed API child."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "installer" / "bundle" / "build_macos_app.sh"


def _unescape_unquoted_heredoc(body: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body) and body[index + 1] in "$`\\\n":
            following = body[index + 1]
            if following != "\n":
                out.append(following)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _launcher_bodies() -> dict[str, str]:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    quoted = re.search(r"<<'LAUNCHER'\n(.*?)\nLAUNCHER\n", source, re.DOTALL)
    unquoted = re.search(r"<<LAUNCHER\n(.*?)\nLAUNCHER\n", source, re.DOTALL)
    assert quoted and unquoted, "both launcher heredocs must be present"
    return {
        "self_contained": quoted.group(1),
        "wrapper": _unescape_unquoted_heredoc(unquoted.group(1)),
    }


def test_both_launchers_delegate_api_ownership_to_the_native_host() -> None:
    for name, body in _launcher_bodies().items():
        assert "VOOL_PROJECT_ROOT" in body, name
        assert "VOOL_RUNTIME_MODE" in body, name
        assert "VOOL_EXPECTED_COMMIT" in body, name
        # The origin is a RUN-TIME default: a launch may name another origin (an acceptance instance
        # beside a live window), a plain double-click still gets 11435. The baked literal this used to
        # pin overwrote the passed value (measured 2026-09-06).
        assert 'VOOL_NATIVE_API_URL="${VOOL_NATIVE_API_URL:-http://127.0.0.1:11435}"' in body, name
        assert 'VOOL_NATIVE_REQUIRE_OWNED_RUNTIME="1"' in body, name
        assert "native host owns runtime startup and teardown" in body.lower() or "runtime ownership belongs" in body.lower()
        assert "vool_window.py" in body
        assert re.search(r'^exec .*vool_window\.py"$', body, re.MULTILINE), name


def test_neither_launcher_detaches_the_vool_api() -> None:
    for name, body in _launcher_bodies().items():
        assert not re.search(r"nohup .*apps\.vool_api_server", body), name
        assert not re.search(r"nohup .*Start_VOOL\.sh", body), name
        assert "VOOL_RUNTIME_START_FAILED" not in body, name


def test_wrapper_exports_the_full_live_checkout_sha() -> None:
    body = _launcher_bodies()["wrapper"]
    assert 'git -C "${PROJECT_ROOT}" rev-parse HEAD' in body
    assert "rev-parse --short" not in body
    assert 'VOOL_RUNTIME_MODE="wrapper"' in body


def test_self_contained_launcher_uses_its_baked_build_identity() -> None:
    body = _launcher_bodies()["self_contained"]
    assert "NULLASourceSHA" in body
    assert "NULLABuildId" not in body
    assert 'VOOL_EXPECTED_COMMIT="${BUNDLED_COMMIT}"' in body
    assert 'VOOL_RUNTIME_MODE="self-contained"' in body


def test_every_emitted_launcher_is_valid_shell() -> None:
    for name, body in _launcher_bodies().items():
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
            handle.write(body)
            path = handle.name
        completed = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        Path(path).unlink(missing_ok=True)
        assert completed.returncode == 0, f"{name}: {completed.stderr}"


def test_runtime_stamp_remains_frozen_at_server_start() -> None:
    runtime = (Path(__file__).resolve().parents[1] / "core" / "web" / "api" / "runtime.py").read_text(
        encoding="utf-8"
    )
    assert "runtime_version_stamp = build_runtime_version_stamp(" in runtime
    assert 'git_output(project_root, "rev-parse", "HEAD")' in runtime
