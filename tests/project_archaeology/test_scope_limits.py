"""RED tests for the C21 project-archaeology scope law (scope + limits).

One named test per law:

- PROTECTED PATHS:      protected stores are flagged, benign paths are not.
- GRANT LAW:            any root beyond the default scope needs an explicit
                        bounded ScopeGrant; metadata grants never read content;
                        grants are validated and can never buy a protected store.
- SYMLINK LAW:          an escaping symlink is never followed, and the skip is
                        disclosed.
- MOUNT LAW:            a subtree on a foreign device (different st_dev) is
                        refused with disclosure.
- ARCHIVE LAW:          nested archives are never descended (max_archive_depth 0).
- FILE-COUNT/BYTE LAW:  lowered byte limits skip big files with disclosure;
                        limits can only lower, never raise above the ceiling.
- TIME LAW:             a scan-time limit completes and truthfully discloses.
- ZERO PROTECTED-STORE ACCESS: neither the source text nor the runtime touches
                        keyring / keychain / ``security`` / osascript.
- HOME-CRAWL REFUSAL:   the home directory itself is never a crawl root, even
                        when (bogusly) granted.
- READ-ONLY LAW:        the archaeology run leaves the corpus byte-identical and
                        the module surface offers no mutation verbs at all.

core.project_archaeology is imported lazily inside every test: this pack is
written FIRST and is expected RED until the module lands.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from tests.project_archaeology.conftest import journal_count

#: default max_file_bytes ceiling (256 KiB) the module must clamp to
BYTE_CEILING = 262_144


def _assert_envelope(pa, env: dict) -> None:
    """Envelope discipline asserted throughout: honest, read-only, schema-tagged."""
    assert isinstance(env, dict)
    assert env["schema"] == pa.SCHEMA
    assert env["execution"] == "none"
    assert env["untrusted_content"] is True
    assert isinstance(env["results"], list)
    assert isinstance(env["truncated"], bool)
    scope = env["scope"]
    assert isinstance(scope, dict)
    assert "workspace_root" in scope
    assert isinstance(scope["store_roots"], dict)
    assert isinstance(scope["grants"], list)
    assert isinstance(scope["refused"], list)
    for rec in scope["refused"]:
        assert isinstance(rec, dict) and rec.get("path") and rec.get("reason")
    assert isinstance(scope["content_access"], bool)
    assert isinstance(scope["default_scope"], bool)
    assert isinstance(env["limits"], dict)


# ---------------------------------------------------------------------------
# A. PROTECTED PATHS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "notional",
    [
        pytest.param(str(Path.home()), id="home-itself"),
        pytest.param(str(Path.home() / ".ssh"), id="ssh-dir"),
        pytest.param(str(Path.home() / ".ssh" / "id_ed25519"), id="ssh-private-key"),
        pytest.param("/srv/any-project/.env", id="env-file"),
        pytest.param(str(Path.home() / "Library" / "Keychains"), id="keychains"),
        pytest.param(str(Path.home() / "Library" / "Cookies"), id="cookies"),
        pytest.param(
            str(Path.home() / "Library" / "Application Support" / "Google" / "Chrome"),
            id="browser-profile",
        ),
        pytest.param(str(Path.home() / "Library" / "Mail"), id="mail"),
        pytest.param(str(Path.home() / "Library" / "Messages"), id="messages"),
        pytest.param(
            str(Path.home() / "Pictures" / "Photos Library.photoslibrary"),
            id="photos-library",
        ),
        pytest.param(str(Path.home() / ".config" / "wallet"), id="wallet-dir"),
        pytest.param(
            str(Path.home() / "Library" / "Application Support" / "Ledger"),
            id="ledger-wallet",
        ),
    ],
)
def test_protected_paths_are_flagged_with_reason(notional):
    """PROTECTED PATHS: protected stores are (True, reason) — never crawled, never read."""
    from core import project_archaeology as pa

    protected, reason = pa.is_protected_path(notional)
    assert protected is True, f"{notional} must be protected"
    assert isinstance(reason, str) and reason.strip()


@pytest.mark.parametrize(
    "notional",
    [
        pytest.param("/definitely/a/workspace", id="workspace-root"),
        pytest.param("/tmp/anything", id="tmp-path"),
        pytest.param("/srv/project/src", id="project-src-dir"),
    ],
)
def test_benign_paths_are_not_protected(notional):
    """PROTECTED PATHS: benign paths are (False, \"\") — ordinary scope is walkable."""
    from core import project_archaeology as pa

    protected, reason = pa.is_protected_path(notional)
    assert protected is False, f"{notional} is benign, not protected"
    assert reason == ""


# ---------------------------------------------------------------------------
# B. GRANT LAW
# ---------------------------------------------------------------------------


def test_content_grant_extends_scope_and_finds_marker(corpus, tmp_path):
    """GRANT LAW: with an explicit content grant, a marker in the granted root is found."""
    from core import project_archaeology as pa

    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "found-here.txt").write_text(
        "leading line\nGRANTED-MARKER-3311\ntrailing line\n", encoding="utf-8"
    )
    env = pa.search(
        text="GRANTED-MARKER-3311",
        workspace_root=corpus["workspace_root"],
        grants=[{"root": str(outside), "access": "content"}],
    )
    _assert_envelope(pa, env)
    assert env["scope"]["content_access"] is True
    assert env["scope"]["default_scope"] is False
    assert env["scope"]["grants"], "accepted grant must be disclosed"
    assert any(str(outside) in json.dumps(g) for g in env["scope"]["grants"])
    hits = [r for r in env["results"] if str(outside) in json.dumps(r)]
    assert hits, "marker under a granted root must be found as a workspace item"
    assert any("GRANTED-MARKER-3311" in json.dumps(r) for r in hits)
    for r in hits:
        if "store" in r:
            assert r["store"] == "workspace"


def test_without_grant_outside_marker_is_never_found(corpus, tmp_path):
    """GRANT LAW: default scope is current task/project only — outside stays invisible."""
    from core import project_archaeology as pa

    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "found-here.txt").write_text(
        "leading line\nGRANTED-MARKER-3311\ntrailing line\n", encoding="utf-8"
    )
    env = pa.search(
        text="GRANTED-MARKER-3311",
        workspace_root=corpus["workspace_root"],
        grants=(),
    )
    _assert_envelope(pa, env)
    assert "GRANTED-MARKER-3311" not in json.dumps(env["results"])
    assert not any(str(outside) in json.dumps(r) for r in env["results"])
    assert not env["scope"]["grants"]


def test_ungranted_query_target_is_refused_and_never_read(corpus, tmp_path):
    """GRANT LAW: a query that targets an ungranted root is refused with a reason."""
    from core import project_archaeology as pa

    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "x.txt").write_text("OUT-OF-SCOPE-PAYMENT-9911\n", encoding="utf-8")
    env = pa.trace(
        path=str(outside / "x"),
        workspace_root=corpus["workspace_root"],
        grants=(),
    )
    _assert_envelope(pa, env)
    refused = env["scope"]["refused"]
    assert any(str(outside) in str(rec["path"]) for rec in refused), (
        "the ungranted root must appear in scope.refused"
    )
    # nothing outside the scope was read
    assert "OUT-OF-SCOPE-PAYMENT-9911" not in json.dumps(env["results"])
    assert not any(str(outside) in json.dumps(r) for r in env["results"])


def test_metadata_grant_lists_paths_but_never_reads_content(corpus, tmp_path):
    """GRANT LAW: metadata tier lists path/kind/size-class but excerpt stays empty."""
    from core import project_archaeology as pa

    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    target = outside / "metadata-only.txt"
    target.write_text("METADATA-ONLY-PAYLOAD-4477\n", encoding="utf-8")
    env = pa.search(
        path_prefix=str(outside),
        workspace_root=corpus["workspace_root"],
        grants=[{"root": str(target), "access": "metadata"}],
    )
    _assert_envelope(pa, env)
    assert env["scope"]["content_access"] is False, "metadata tier grants no content access"
    listed = [r for r in env["results"] if str(target) in json.dumps(r)]
    assert listed, "a metadata grant still lists the file (names are not content)"
    for r in listed:
        assert str(r.get("excerpt", "")) == "", "metadata tier must never read content"
        assert (
            "kind" in r or "size" in r or "size_class" in r
        ), "metadata items carry path/kind/size-class metadata"
    assert "METADATA-ONLY-PAYLOAD-4477" not in json.dumps(env["results"])


def test_grant_access_level_is_validated(corpus, tmp_path):
    """GRANT LAW: access outside {"metadata","content"} is an input error."""
    from core import project_archaeology as pa

    root = str(tmp_path / "grant-root")
    grant = None
    try:
        grant = pa.ScopeGrant(root=root, access="all")
    except pa.ArchaeologyInputError:
        grant = None
    if grant is not None:  # validation may also happen at call time
        with pytest.raises(pa.ArchaeologyInputError):
            pa.search(text="x", workspace_root=corpus["workspace_root"], grants=[grant])
    with pytest.raises(pa.ArchaeologyInputError):
        pa.search(
            text="x",
            workspace_root=corpus["workspace_root"],
            grants=[{"root": root, "access": "all"}],
        )


def test_grant_cannot_buy_protected_store(corpus, tmp_path):
    """GRANT LAW: a grant rooted at a protected store is refused — grants cannot buy it."""
    from core import project_archaeology as pa

    protected = str(Path.home() / ".ssh")
    grant = None
    try:
        grant = pa.ScopeGrant(root=protected, access="content")
    except pa.ScopeRefused:
        grant = None
    if grant is not None:
        with pytest.raises(pa.ScopeRefused):
            pa.search(
                text="id_ed25519",
                workspace_root=corpus["workspace_root"],
                grants=[grant],
            )
    with pytest.raises(pa.ScopeRefused):
        pa.search(
            text="id_ed25519",
            workspace_root=corpus["workspace_root"],
            grants=[{"root": protected, "access": "content"}],
        )


# ---------------------------------------------------------------------------
# C. SYMLINK LAW
# ---------------------------------------------------------------------------


def test_escaping_symlink_is_never_followed_and_disclosed(corpus):
    """SYMLINK LAW: link-escape (/etc/hosts) is never followed; the skip is disclosed.

    The workspace fixture contains no "127.0.0.1"/"localhost" text, so any hit of
    those strings in results would prove the walker crossed the symlink.
    """
    from core import project_archaeology as pa

    ws = corpus["workspace_root"]
    env_text = pa.search(text="localhost", workspace_root=ws)
    env_all = pa.search(workspace_root=ws)
    for env in (env_text, env_all):
        _assert_envelope(pa, env)
        blob = json.dumps(env["results"])
        assert "127.0.0.1" not in blob, "escaping symlink content leaked into results"
        assert "localhost" not in blob, "escaping symlink content leaked into results"
        assert not any("/etc/hosts" in json.dumps(r) for r in env["results"])
    disclosed = (
        any("link-escape" in json.dumps(rec) for rec in env_all["scope"]["refused"])
        or env_all["truncated"]
    )
    assert disclosed, "skipping the escaping symlink must be disclosed"


# ---------------------------------------------------------------------------
# D. MOUNT LAW
# ---------------------------------------------------------------------------


def test_foreign_device_subtree_is_refused_as_mount(corpus, monkeypatch):
    """MOUNT LAW: a subtree on another device (foreign st_dev) is refused, not crawled."""
    from core import project_archaeology as pa

    ws = Path(corpus["workspace_root"])
    mt = ws / "mt"
    (mt / "deep").mkdir(parents=True, exist_ok=True)
    (mt / "deep" / "marker.txt").write_text("MOUNT-MARKER-5512\n", encoding="utf-8")

    real_stat = os.stat
    real_lstat = os.lstat
    foreign_dev = 999_999
    mount_prefix = str(mt)

    def _foreign(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == mount_prefix or str(path).startswith(mount_prefix + os.sep):
            fields = list(st)
            fields[2] = foreign_dev  # st_dev
            return os.stat_result(tuple(fields))
        return st

    def _foreign_lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if str(path) == mount_prefix or str(path).startswith(mount_prefix + os.sep):
            fields = list(st)
            fields[2] = foreign_dev  # st_dev
            return os.stat_result(tuple(fields))
        return st

    monkeypatch.setattr(os, "stat", _foreign)
    monkeypatch.setattr(os, "lstat", _foreign_lstat)

    env = pa.search(text="MOUNT-MARKER-5512", workspace_root=str(ws))
    _assert_envelope(pa, env)
    blob = json.dumps(env["results"])
    assert "MOUNT-MARKER-5512" not in blob, "foreign-device subtree must not be crawled"
    assert not any(
        str(r.get("path", "")).startswith(str(mt)) for r in env["results"]
    ), "no result may originate inside the foreign-device subtree"
    disclosed = (
        any("mt" in json.dumps(rec) for rec in env["scope"]["refused"])
        or env["truncated"]
        or "mount" in json.dumps(env).lower()
    )
    assert disclosed, "refusing the foreign-device subtree must be disclosed"


# ---------------------------------------------------------------------------
# E. ARCHIVE LAW
# ---------------------------------------------------------------------------


def test_nested_archive_is_never_descended_at_depth_zero(corpus):
    """ARCHIVE LAW: nested.zip is never extracted; depth limit 0 is disclosed."""
    from core import project_archaeology as pa

    env = pa.search(text="nested archive payload", workspace_root=corpus["workspace_root"])
    _assert_envelope(pa, env)
    blob = json.dumps(env["results"])
    assert "nested archive payload" not in blob, "nested archive must never be descended"
    for r in env["results"]:
        if str(r.get("path", "")).endswith(".zip"):
            assert r.get("kind") == "archive"
            assert str(r.get("excerpt", "")) == ""
    assert env["limits"].get("max_archive_depth") == 0, "archive depth limit must be disclosed"


# ---------------------------------------------------------------------------
# F. FILE-COUNT + BYTE LIMITS
# ---------------------------------------------------------------------------


def test_oversized_file_is_never_read_when_limit_lowers(corpus):
    """BYTE LIMIT: bloated.bin (300 KB) is skipped/metadata-only once max_file_bytes=1024."""
    from core import project_archaeology as pa

    env = pa.search(
        text="xxxxx",
        workspace_root=corpus["workspace_root"],
        limits={"max_file_bytes": 1024},
    )
    _assert_envelope(pa, env)
    assert env["limits"]["max_file_bytes"] == 1024
    blob = json.dumps(env["results"])
    assert "x" * 64 not in blob, "an oversized file must not be excerpted under the limit"
    bloated = [r for r in env["results"] if "bloated.bin" in json.dumps(r)]
    for r in bloated:
        assert str(r.get("excerpt", "")) == "", "bloated.bin may appear metadata-only at most"
    disclosed = (
        env["truncated"]
        or any("bloated.bin" in json.dumps(rec) for rec in env["scope"]["refused"])
        or "bloated.bin" in json.dumps(env["limits"])
    )
    assert disclosed, "skipping the oversized file must be disclosed truthfully"


def test_limits_above_ceiling_are_clamped_not_honored(corpus):
    """BYTE LIMIT: limits can only lower — a raise attempt is clamped to the ceiling."""
    from core import project_archaeology as pa

    env = pa.search(
        workspace_root=corpus["workspace_root"],
        limits={"max_file_bytes": 10**9},
    )
    _assert_envelope(pa, env)
    assert env["limits"]["max_file_bytes"] <= BYTE_CEILING, (
        "raising above the module ceiling must be clamped, not honored"
    )


# ---------------------------------------------------------------------------
# G. TIME LIMIT
# ---------------------------------------------------------------------------


def test_scan_time_limit_completes_with_truthful_disclosure(corpus):
    """TIME LIMIT: a tight scan budget returns an envelope fast and discloses truncation."""
    from core import project_archaeology as pa

    started = time.monotonic()
    env = pa.search(
        workspace_root=corpus["workspace_root"],
        limits={"max_scan_seconds": 0.0001},
    )
    elapsed = time.monotonic() - started
    assert elapsed < 30.0, "time-limited scan must not hang"
    _assert_envelope(pa, env)
    disclosed = (
        env["truncated"]
        or "max_scan_seconds" in json.dumps(env["limits"])
        or "scan" in json.dumps(env["limits"]).lower()
        or "scan" in json.dumps(env["scope"]).lower()
    )
    assert disclosed, "a scan cut short by the time budget must say so"


# ---------------------------------------------------------------------------
# H. ZERO PROTECTED-STORE ACCESS
# ---------------------------------------------------------------------------


def test_source_contains_no_protected_store_strings():
    """ZERO ACCESS (source): the module never references keyring/keychain/`security`/osascript."""
    module_path = Path(__file__).resolve().parents[2] / "core" / "project_archaeology.py"
    if not module_path.exists():
        pytest.fail("module missing")
    src = module_path.read_text(encoding="utf-8").lower()
    assert "keyring" not in src, "keyring must never be referenced"
    assert "keychain" not in src, "keychain must never be referenced"
    assert "osascript" not in src, "osascript must never be referenced"
    assert not re.search(r"[\"']security[\"']", src), (
        "the macOS `security` CLI must never be invoked"
    )


def test_runtime_never_touches_keyring_or_security_subprocess(corpus, monkeypatch):
    """ZERO ACCESS (runtime): locate/search/trace trip no protected-store tripwire."""
    import core.bounded_keyring as bk
    from core import project_archaeology as pa

    def _forbid(*_args, **_kwargs):
        raise AssertionError("protected store touched")

    for name in dir(bk):
        if name.startswith("_"):
            continue
        if callable(getattr(bk, name)):
            monkeypatch.setattr(bk, name, _forbid)
    # direct imports by the module would bypass the module-object patch:
    for name in ("bounded_keyring_call", "keychain_blocked", "note_keychain_blocked"):
        if hasattr(pa, name):
            monkeypatch.setattr(pa, name, _forbid)

    real_run = subprocess.run
    real_popen = subprocess.Popen

    def _touches_protected(args) -> bool:
        if isinstance(args, (list, tuple)):
            text = " ".join(str(a) for a in args)
        else:
            text = str(args)
        return "security" in text or "keychain" in text

    def _guarded_run(args, *args_rest, **kwargs):
        if _touches_protected(args):
            raise AssertionError("protected store touched via subprocess")
        return real_run(args, *args_rest, **kwargs)

    class _GuardedPopen(real_popen):
        def __init__(self, args, *args_rest, **kwargs):
            if _touches_protected(args):
                raise AssertionError("protected store touched via subprocess")
            super().__init__(args, *args_rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)

    ws = corpus["workspace_root"]
    envelopes = [
        pa.locate("eff-push-77", workspace_root=ws),
        pa.search(text="night", workspace_root=ws),
        pa.trace(turn_id="turn-night-2", workspace_root=ws),
    ]
    for env in envelopes:
        _assert_envelope(pa, env)
    assert any(env["results"] for env in envelopes), "the corpus is answerable without protected stores"


# ---------------------------------------------------------------------------
# I. HOME-CRAWL REFUSAL
# ---------------------------------------------------------------------------


def test_home_is_never_a_crawl_root_even_when_granted(tmp_path):
    """HOME REFUSAL: the home directory itself is protected — never a crawl root."""
    from core import project_archaeology as pa

    home = str(Path.home())
    with pytest.raises(pa.ScopeRefused):
        pa.search(workspace_root=home)
    with pytest.raises(pa.ScopeRefused):
        pa.locate("night", workspace_root=home)
    with pytest.raises(pa.ScopeRefused):
        pa.search(
            text="x",
            workspace_root=str(tmp_path),
            grants=[{"root": home, "access": "content"}],
        )


# ---------------------------------------------------------------------------
# J. READ-ONLY LAW
# ---------------------------------------------------------------------------


def test_read_only_law_corpus_unchanged_and_no_mutation_verbs(corpus):
    """READ-ONLY: the corpus is byte-identical afterwards and the surface has no mutation verbs."""
    from core import project_archaeology as pa

    ws = Path(corpus["workspace_root"])
    count_before = journal_count(corpus["blackbox_root"])
    assert count_before == 9

    def _ws_files() -> dict[Path, bytes]:
        return {
            p: p.read_bytes()
            for p in sorted(ws.rglob("*"))
            if p.is_file() and not p.is_symlink()
        }

    before = _ws_files()
    envelopes = [
        pa.locate("eff-config-08", workspace_root=str(ws)),
        pa.search(text="night", workspace_root=str(ws)),
        pa.trace(session_id="sess-night", workspace_root=str(ws)),
    ]
    for env in envelopes:
        _assert_envelope(pa, env)

    assert journal_count(corpus["blackbox_root"]) == count_before, "journal grew during a read"
    assert _ws_files() == before, "workspace bytes changed during a read"

    banned = ("restore", "delete", "checkout", "reset", "clean", "write")
    surface = [
        n
        for n in dir(pa)
        if not n.startswith("_")
        and callable(getattr(pa, n))
        and inspect.isfunction(getattr(pa, n))
    ]
    verbs = [n for n in surface if any(word in n.lower() for word in banned)]
    assert verbs == [], f"the archaeology surface must not offer mutation verbs: {verbs}"
    assert not any("restore" in n.lower() for n in dir(pa)), "no restore* symbol may exist"
