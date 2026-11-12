"""A test may only run against the build it means.

Found Nov 12: the phone's test VOOL served an old phone-branch commit (release 0.7.6-beta, plus
uncommitted edits) while the person was asked to test the current 0.7.8 chat line. The stamp
said so, but nothing compared it with the expected head, a dirty tree was one bit (two edited
trees on one commit look the same), and a plain source copy without .git reported no commit or,
inside another checkout, that checkout's HEAD. These tests drive real git checkouts and the real
CLI; nothing is stubbed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.build_provenance import worktree_diff_sha256
from core.web.api import runtime as rt

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "ops" / "build_identity.py"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "lane")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "user.email", "t@example.invalid")
    (root / "app.py").write_text("VERSION = 1\n")
    _git(root, "add", "app.py")
    _git(root, "commit", "-qm", "one")
    return root


def _commit(root: Path, text: str) -> str:
    (root / "app.py").write_text(text)
    _git(root, "commit", "-qam", text.strip())
    return _git(root, "rev-parse", "HEAD")


def _tool(*args: str, env_manifest: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(TOOL)]
    if env_manifest is not None:
        cmd += ["--manifest", str(env_manifest)]
    return subprocess.run([*cmd, *args], capture_output=True, text=True)


def _stamp(root: Path) -> dict:
    return rt.build_runtime_version_stamp(project_root=root, runtime_model_tag="m", workstation_version="w")


def test_two_different_edits_on_one_commit_get_different_fingerprints(tmp_path):
    root = _repo(tmp_path / "r")
    assert worktree_diff_sha256(root) == "", "a clean tree has no edit fingerprint"
    (root / "app.py").write_text("VERSION = 2\n")
    first = worktree_diff_sha256(root)
    (root / "app.py").write_text("VERSION = 3\n")
    second = worktree_diff_sha256(root)
    assert first and second and first != second
    (root / "app.py").write_text("VERSION = 2\n")
    assert worktree_diff_sha256(root) == first, "the same edits must give the same fingerprint"
    (root / "new_module.py").write_text("X = 1\n")
    assert worktree_diff_sha256(root) not in {"", first}, "an untracked source file is part of the edits"
    stamp = _stamp(root)
    assert stamp["dirty"] is True and stamp["diff_sha256"] == worktree_diff_sha256(root)


def test_a_plain_copy_inside_another_checkout_is_unidentified_until_stamped(tmp_path):
    outer = _repo(tmp_path / "outer")
    copy = outer / "missions" / "src"  # a plain copy that happens to sit inside another checkout
    copy.mkdir(parents=True)
    (copy / "app.py").write_text("VERSION = 1\n")
    stamp = _stamp(copy)
    assert stamp["identified"] is False, "the copy reported the enclosing checkout as its own build"
    assert stamp["commit_full"] == ""

    source = _repo(tmp_path / "source")
    (source / "app.py").write_text("VERSION = 9\n")  # the checkout has an uncommitted edit the copy lacks
    drifted = _tool("stamp-copy", "--from", str(source), "--to", str(copy))
    assert drifted.returncode == 3 and "app.py (content differs)" in drifted.stdout, drifted.stdout + drifted.stderr
    assert not (copy / "config" / "build-source.json").exists(), "a drifted copy must not get a stamp"

    (copy / "app.py").write_text("VERSION = 9\n")  # now the copy holds exactly the checkout's files
    done = _tool("stamp-copy", "--from", str(source), "--to", str(copy))
    assert done.returncode == 0, done.stdout + done.stderr
    stamped = _stamp(copy)
    assert stamped["identified"] is True
    assert stamped["commit_full"] == _git(source, "rev-parse", "HEAD")
    assert stamped["dirty"] is True
    assert stamped["diff_sha256"] == worktree_diff_sha256(source)



def test_a_trimmed_copy_is_stamped_and_a_later_edit_to_it_is_refused(tmp_path):
    """The phone test VOOL is a git-archive copy without tests/docs/the phone app, served with no .git:
    only its stamp says what it is, so the stamp must survive the trim and catch a later hand edit."""
    source = _repo(tmp_path / "source")
    (source / "tests").mkdir()
    (source / "tests" / "test_app.py").write_text("def test(): pass\n")
    _git(source, "add", "tests")
    _git(source, "commit", "-qm", "tests")
    head = _git(source, "rev-parse", "HEAD")
    copy = tmp_path / "v15" / "src"
    copy.mkdir(parents=True)
    (copy / "app.py").write_bytes((source / "app.py").read_bytes())  # the copy leaves tests/ out

    missing = _tool("stamp-copy", "--from", str(source), "--to", str(copy))
    assert missing.returncode == 3 and "tests/test_app.py (missing in the copy)" in missing.stdout

    trimmed = _tool("stamp-copy", "--from", str(source), "--to", str(copy), "--exclude", "tests")
    assert trimmed.returncode == 0, trimmed.stdout + trimmed.stderr
    assert "left out: tests" in trimmed.stdout
    stamp = json.loads((copy / "config" / "build-source.json").read_text())
    assert stamp["excluded_paths"] == ["tests"] and stamp["file_count"] == 1

    manifest = tmp_path / "CURRENT-HEADS.json"
    assert _tool("set-head", "--lane", "phone", "--commit", head, env_manifest=manifest).returncode == 0
    ok = _tool("check-tree", str(copy), "--lane", "phone", env_manifest=manifest)
    assert ok.returncode == 0, ok.stdout + ok.stderr

    (copy / "app.py").write_text("VERSION = 'edited by hand on the Mac'\n")
    edited = _tool("check-tree", str(copy), "--lane", "phone", env_manifest=manifest)
    assert edited.returncode == 3 and "app.py (changed)" in edited.stdout, edited.stdout

def test_check_tree_refuses_wrong_commit_unlisted_edits_and_a_stale_lane(tmp_path):
    repo = _repo(tmp_path / "repo")
    manifest = tmp_path / "CURRENT-HEADS.json"
    chat_1 = _commit(repo, "VERSION = 'chat 1'\n")
    assert _tool("set-head", "--lane", "chat", "--commit", chat_1, env_manifest=manifest).returncode == 0

    _git(repo, "checkout", "-qb", "phone")
    phone_1 = _commit(repo, "VERSION = 'phone on chat 1'\n")
    made = _tool("set-head", "--lane", "phone", "--commit", phone_1, "--based-on", "chat", "--repo", str(repo),
                 env_manifest=manifest)
    assert made.returncode == 0, made.stdout + made.stderr
    ok = _tool("check-tree", str(repo), "--lane", "phone", env_manifest=manifest)
    assert ok.returncode == 0 and ok.stdout.startswith("OK"), ok.stdout + ok.stderr

    # Wrong lane: the phone checkout is not the chat head.
    wrong = _tool("check-tree", str(repo), "--lane", "chat", env_manifest=manifest)
    assert wrong.returncode == 3 and "REFUSED" in wrong.stdout

    # Uncommitted edits: refused until that exact fingerprint is listed for the lane.
    (repo / "app.py").write_text("VERSION = 'phone, edited'\n")
    edited = _tool("check-tree", str(repo), "--lane", "phone", env_manifest=manifest)
    assert edited.returncode == 3 and "uncommitted edits" in edited.stdout
    listed = _tool("check-tree", str(repo), "--lane", "phone", "--allow-diff", worktree_diff_sha256(repo),
                   env_manifest=manifest)
    assert listed.returncode == 0, listed.stdout
    _git(repo, "checkout", "-q", "--", "app.py")

    # The chat line moves on: the phone lane, still built on chat 1, is now stale and refused.
    _git(repo, "checkout", "-q", "lane")
    chat_2 = _commit(repo, "VERSION = 'chat 2'\n")
    assert _tool("set-head", "--lane", "chat", "--commit", chat_2, env_manifest=manifest).returncode == 0
    _git(repo, "checkout", "-q", "phone")
    stale = _tool("check-tree", str(repo), "--lane", "phone", env_manifest=manifest)
    assert stale.returncode == 3 and "STALE" in stale.stdout, stale.stdout
    assert _tool("stale", env_manifest=manifest).returncode == 3

    # Recording a phone head that does not contain the new chat head is refused outright.
    refused = _tool("set-head", "--lane", "phone", "--commit", phone_1, "--based-on", "chat", "--repo", str(repo),
                    env_manifest=manifest)
    assert refused.returncode == 3 and "does not contain" in refused.stdout

    # Merge the chat line in, record it, and the phone lane is current again.
    _git(repo, "merge", "-q", "--no-edit", "-X", "theirs", "lane")
    phone_2 = _git(repo, "rev-parse", "HEAD")
    assert _tool("set-head", "--lane", "phone", "--commit", phone_2, "--based-on", "chat", "--repo", str(repo),
                 env_manifest=manifest).returncode == 0
    assert _tool("check-tree", str(repo), "--lane", "phone", env_manifest=manifest).returncode == 0
    assert _tool("stale", env_manifest=manifest).returncode == 0


def test_no_manifest_and_unknown_lane_fail_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("VOOL_HEADS_MANIFEST", raising=False)
    repo = _repo(tmp_path / "repo")
    missing = _tool("check-tree", str(repo), "--lane", "chat")
    assert missing.returncode == 2 and "manifest" in missing.stderr
    manifest = tmp_path / "CURRENT-HEADS.json"
    manifest.write_text(json.dumps({"schema": "vool-current-heads/1", "lanes": {}}))
    unknown = _tool("check-tree", str(repo), "--lane", "chat", env_manifest=manifest)
    assert unknown.returncode == 2 and "not in the heads manifest" in unknown.stderr



def test_a_build_running_another_checkouts_code_is_refused(tmp_path):
    """Approval proof, Nov 12: a daemon started by path from one checkout, with an interpreter
    whose editable install pointed at an older checkout, ran the older `core` while its stamp
    (read from the started folder) named the new commit, and `check` said OK. The stamp now
    says whether the imported core lives in the folder it names, and `check` refuses when not."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    own = _stamp(REPO)
    assert own["code_root_matches"] is True
    other = _repo(tmp_path / "other")
    mixed = _stamp(other)  # this process imported REPO's core, the stamp reads `other`
    assert mixed["code_root_matches"] is False and mixed["commit_full"] == _git(other, "rev-parse", "HEAD")

    class Version(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            raw = json.dumps(mixed).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Version)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        refused = _tool("check", "--url", url, "--expect-commit", mixed["commit_full"])
        assert refused.returncode == 3, refused.stdout + refused.stderr
        assert "another checkout" in refused.stdout
        mixed["code_root_matches"] = True
        accepted = _tool("check", "--url", url, "--expect-commit", mixed["commit_full"])
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        del mixed["code_root_matches"]
        older = _tool("check", "--url", url, "--expect-commit", mixed["commit_full"])
        assert older.returncode == 0 and "does not report where its code was loaded from" in older.stderr
    finally:
        server.shutdown()
