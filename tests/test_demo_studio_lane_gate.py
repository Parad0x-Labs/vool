"""The studio films a lane's head only, on the scripted lane it labels.

Two seams the v0.8 port added or repaired:

* ``--lane``: the studio stages the lane head from the heads manifest and every
  daemon it films must pass the line's own guard (``ops/build_identity.py check
  --lane``). These tests run the REAL guard script against a loopback server
  that speaks the runtime's ``/api/runtime/version`` shape.
* the stub wiring: the first boot carries the stub port (lanes aimed at the
  stub, network contained, exactly as the served-reality bench boots), and a
  restart relaunches with the very same arguments. Before the port the first
  boot left the stub port out and a restart dropped the bundle selection and
  the cloud stub lane.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import ops.demo_studio.runner as runner_mod
from ops.demo_studio.journeys import journey_spec
from ops.demo_studio.runner import (
    StudioConfig,
    StudioMode,
    StudioRefusal,
    check_build_identity,
    lane_head,
    relaunch_daemon,
    run_journey,
)
from ops.served_reality.provider_stub import STUB_MODEL_ID

LANE_HEAD = "247124ed052418bccd298e947a202ae855bb97c4"
OTHER_COMMIT = "fa4fcc92aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RELEASE = "0.8.0-beta"


def _write_manifest(path: Path, head: str = LANE_HEAD) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "vool-current-heads/1",
                "updated_at": "2026-11-12T00:00:00Z",
                "lanes": {"v08": {"head": head, "release": RELEASE, "allowed_diffs": []}},
            }
        ),
        encoding="utf-8",
    )
    return path


class _RuntimeVersion:
    """Loopback server answering /api/runtime/version like a running VOOL."""

    def __init__(self, *, commit_full: str, release: str = RELEASE, dirty: bool = False) -> None:
        payload = {
            "commit": commit_full[:12],
            "commit_full": commit_full,
            "dirty": dirty,
            "release_version": release,
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # http.server API
                if not self.path.startswith("/api/runtime/version"):
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # silence
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _cfg(tmp_path: Path, *, head: str = LANE_HEAD, **overrides: Any) -> StudioConfig:
    values: dict[str, Any] = {
        "repo": tmp_path / "repo",
        "out_dir": tmp_path / "out",
        "mode": StudioMode.CAPTURE,
        "lane": "v08",
    }
    values.update(overrides)
    if "heads_manifest" not in values:
        values["heads_manifest"] = str(_write_manifest(tmp_path / "heads.json", head=head))
    return StudioConfig(**values)


# ---------------------------------------------------------------------------
# --lane: the line's own guard decides


def test_lane_head_is_read_through_the_guard(tmp_path: Path) -> None:
    assert lane_head(_cfg(tmp_path)) == LANE_HEAD


def test_an_unknown_lane_is_refused_naming_the_known_lanes(tmp_path: Path) -> None:
    with pytest.raises(StudioRefusal) as excinfo:
        lane_head(_cfg(tmp_path, lane="phone-078"))
    assert "phone-078" in str(excinfo.value) and "v08" in str(excinfo.value)


def test_a_missing_heads_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StudioRefusal) as excinfo:
        lane_head(_cfg(tmp_path, heads_manifest=str(tmp_path / "absent.json")))
    assert "heads manifest" in str(excinfo.value)


def test_the_lane_head_build_passes_and_the_guard_line_is_kept(tmp_path: Path) -> None:
    server = _RuntimeVersion(commit_full=LANE_HEAD)
    try:
        verdict = check_build_identity(_cfg(tmp_path), server.url)
    finally:
        server.close()
    assert verdict["exit_code"] == 0
    assert verdict["lane"] == "v08"
    assert verdict["output"].startswith(f"OK {server.url} runs {LANE_HEAD[:8]} ({RELEASE})")


@pytest.mark.parametrize(
    ("served", "reason"),
    [
        ({"commit_full": OTHER_COMMIT}, f"it runs {OTHER_COMMIT[:8]}"),
        ({"commit_full": LANE_HEAD, "release": "0.7.8"}, "release 0.7.8"),
        ({"commit_full": LANE_HEAD, "dirty": True}, "uncommitted edits"),
    ],
)
def test_any_other_build_refuses_the_film(tmp_path: Path, served: dict[str, Any], reason: str) -> None:
    server = _RuntimeVersion(**served)
    try:
        with pytest.raises(StudioRefusal) as excinfo:
            check_build_identity(_cfg(tmp_path), server.url)
    finally:
        server.close()
    message = str(excinfo.value)
    assert "refused lane v08" in message and "exit 3" in message
    assert reason in message, message


def test_an_unreachable_daemon_refuses_the_film(tmp_path: Path) -> None:
    server = _RuntimeVersion(commit_full=LANE_HEAD)
    url = server.url
    server.close()
    with pytest.raises(StudioRefusal) as excinfo:
        check_build_identity(_cfg(tmp_path), url)
    assert "exit 4" in str(excinfo.value)


def _git_repo_with_two_commits(root: Path) -> tuple[str, str]:
    root.mkdir(parents=True)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    git("init", "-q")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "one")
    first = git("rev-parse", "HEAD")
    (root / "a.txt").write_text("two\n", encoding="utf-8")
    git("commit", "-q", "-am", "two")
    return first, git("rev-parse", "HEAD")


def test_a_lane_film_stages_the_lane_head_not_the_checkout_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _git_repo_with_two_commits(tmp_path / "repo")
    staged: list[str] = []

    def _boot(config: StudioConfig, run_dir: Path, spec: Any) -> Any:
        staged.append(config.sha)
        raise RuntimeError("stop after staging was chosen")

    monkeypatch.setattr(runner_mod, "_boot_staged_app", _boot)
    cfg = _cfg(tmp_path, head=first)
    with pytest.raises(RuntimeError, match="stop after staging"):
        run_journey("local_chat", cfg)
    assert staged == [first], "with --lane and no --sha the studio must stage the lane head"
    assert second != first


def test_an_explicit_sha_other_than_the_lane_head_is_refused_before_boot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _git_repo_with_two_commits(tmp_path / "repo")

    def _boot(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must never run
        raise AssertionError("a refused lane film must not boot anything")

    monkeypatch.setattr(runner_mod, "_boot_staged_app", _boot)
    cfg = _cfg(tmp_path, head=first, sha=second)
    with pytest.raises(StudioRefusal) as excinfo:
        run_journey("local_chat", cfg)
    assert second[:8] in str(excinfo.value) and first[:8] in str(excinfo.value)


# ---------------------------------------------------------------------------
# Stub wiring: first boot carries the stub, a restart repeats it exactly


class _Handle(SimpleNamespace):
    def terminate(self) -> None:
        self.terminated = True


def test_first_boot_wires_the_stub_and_a_restart_relaunches_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ops.served_reality.appstage as appstage
    import ops.served_reality.daemon as daemon

    launches: list[dict[str, Any]] = []

    def _launch(**kwargs: Any) -> _Handle:
        launches.append(kwargs)
        return _Handle(port=40000 + len(launches), home=kwargs["home"], terminated=False)

    staged = SimpleNamespace(path=tmp_path / "app", sha_full=LANE_HEAD)
    monkeypatch.setattr(appstage, "stage_app", lambda repo, sha, run_dir: staged)
    monkeypatch.setattr(daemon, "launch_daemon", _launch)
    # v0.8 seeds its ollama-local rows from the stub environment itself.
    monkeypatch.setattr(runner_mod, "_ollama_lanes_aimed_at", lambda home, base: True)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    bundle = runner_mod._boot_staged_app(
        StudioConfig(repo=tmp_path, out_dir=tmp_path, mode=StudioMode.CAPTURE),
        run_dir,
        journey_spec("local_chat"),
    )
    try:
        first = launches[0]
        stub_base = f"http://127.0.0.1:{bundle.stub.port}"
        assert first["stub_port"] == bundle.stub.port, "the first boot must carry the stub port"
        assert first["stub_model"] == STUB_MODEL_ID
        selected = json.loads(Path(first["bundle_manifest"]).read_text(encoding="utf-8"))
        assert selected == {"selected_model": STUB_MODEL_ID}
        assert first["extra_env"]["TETHER_BASE_URL"] == f"{stub_base}/v1"
        assert first["expected_sha"] == LANE_HEAD
        assert bundle.lane_wiring == "env"
        assert bundle.launch_kwargs == {k: v for k, v in first.items() if k != "wire_log"}

        old_handle = bundle.handle
        new_handle = relaunch_daemon(bundle, run_dir)
        assert old_handle.terminated is True
        assert bundle.handle is new_handle
        again = {k: v for k, v in launches[1].items() if k != "wire_log"}
        assert again == bundle.launch_kwargs, "a restart must relaunch with the first boot's arguments"
    finally:
        bundle.stub.stop()


# ---------------------------------------------------------------------------
# The lane check reads what the product persisted, and fails closed


def _manifest_db(home: Path, base_urls: list[str], name: str = "vool_web0_v2.db") -> None:
    data = home / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(str(data / name))
    conn.execute(
        "CREATE TABLE model_provider_manifests "
        "(provider_name TEXT, model_name TEXT, runtime_config_json TEXT)"
    )
    for index, url in enumerate(base_urls):
        conn.execute(
            "INSERT INTO model_provider_manifests VALUES ('ollama-local', ?, ?)",
            (f"m{index}", json.dumps({"base_url": url})),
        )
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    ("urls", "db_name", "aimed"),
    [
        (["http://127.0.0.1:5555", "http://127.0.0.1:5555/"], "vool_web0_v2.db", True),
        (["http://127.0.0.1:5555"], "nulla_web0_v2.db", True),
        (["http://127.0.0.1:5555", "http://127.0.0.1:11434"], "vool_web0_v2.db", False),
        ([], "vool_web0_v2.db", False),
    ],
)
def test_lane_check_needs_every_ollama_row_at_the_stub(
    tmp_path: Path, urls: list[str], db_name: str, aimed: bool
) -> None:
    _manifest_db(tmp_path, urls, db_name)
    assert runner_mod._ollama_lanes_aimed_at(tmp_path, "http://127.0.0.1:5555") is aimed


def test_lane_check_without_a_store_fails_closed(tmp_path: Path) -> None:
    assert runner_mod._ollama_lanes_aimed_at(tmp_path, "http://127.0.0.1:5555") is False
