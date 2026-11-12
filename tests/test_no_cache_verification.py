"""The verification gate accepts disabling pytest's cache writer, and nothing else of its kind."""
from __future__ import annotations

from pathlib import Path

import pytest

from ops.pytest_shards import build_parser, run_collected_shards, validate_pytest_args

NO_CACHE = ("-p", "no:cacheprovider")


def _repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (repo_root / "tests" / "test_sample.py").write_text(
        "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n", encoding="utf-8")
    return repo_root


def _run(repo_root: Path, tmp_path: Path, pytest_args: tuple[str, ...], name: str) -> int:
    return run_collected_shards(repo_root=repo_root, paths=("tests/test_sample.py",), workers=1,
                                pytest_args=pytest_args, timeout_seconds=60, collection_timeout_seconds=60,
                                tail_lines=20, artifact_root=tmp_path / name)


def test_the_exact_no_cache_pair_is_accepted_with_display_options() -> None:
    assert validate_pytest_args(NO_CACHE) == NO_CACHE
    assert validate_pytest_args(("--tb=short", *NO_CACHE)) == ("--tb=short", *NO_CACHE)
    # The verify entry point forwards each item as --pytest-arg=<item>; argparse keeps the pair.
    parsed = build_parser().parse_args([f"--pytest-arg={item}" for item in NO_CACHE])
    assert tuple(parsed.pytest_arg) == NO_CACHE


@pytest.mark.parametrize("args", [
    ("-p",),
    ("no:cacheprovider",),
    ("no:cacheprovider", "-p"),
    ("-p=no:cacheprovider",),
    ("-pno:cacheprovider",),
    ("-p", "no:verification"),
    ("-p", "no:cacheprovider", "-p", "no:verification"),
    ("-p", "no:cacheprovider", "--lf"),
    ("-p", "no:cacheprovider", "-k", "one"),
    ("-p", "-p", "no:cacheprovider"),
    ("-p", "no:cacheprovider", "--deselect=tests/test_sample.py::test_two"),
])
def test_every_other_plugin_or_selection_control_is_still_refused(args) -> None:
    with pytest.raises(ValueError, match="unsafe pytest"):
        validate_pytest_args(args)


def test_real_shard_run_with_the_pair_passes_and_leaves_no_cache_in_the_checkout(tmp_path: Path) -> None:
    repo_root = _repo(tmp_path)
    assert _run(repo_root, tmp_path, ("--tb=short", *NO_CACHE), "no-cache") == 0
    assert not (repo_root / ".pytest_cache").exists()
    # Without the pair the same run writes the cache into the checkout, which is what it stops.
    assert _run(repo_root, tmp_path, ("--tb=short",), "with-cache") == 0
    assert (repo_root / ".pytest_cache").exists()


def test_a_disabled_verification_plugin_after_the_pair_runs_nothing(tmp_path: Path) -> None:
    repo_root = _repo(tmp_path)
    assert _run(repo_root, tmp_path, (*NO_CACHE, "-p", "no:verification"), "refused") == 1
    assert not (tmp_path / "refused").exists(), "a refused argument list must start no collection or shard"


def test_verify_entry_forwards_the_pair_to_the_shard_stage(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from ops.verify import REQUIRED_PYTHON, REQUIRED_TOOLS, StageResult, run_verification

    seen = []

    def stage(*, name, command, repo_root, log_path, timeout_seconds):
        seen.append((name, tuple(command)))
        log_path.write_text("ok\n", encoding="utf-8")
        return StageResult(name=name, command=tuple(command), returncode=0, log_path=log_path)

    run_verification(repo_root=Path(__file__).resolve().parents[1], pytest_args=NO_CACHE,
                     log_dir=tmp_path / "logs", version_info=SimpleNamespace(major=REQUIRED_PYTHON[0], minor=REQUIRED_PYTHON[1],
                                                               micro=REQUIRED_PYTHON[2]),
                     tool_versions=dict(REQUIRED_TOOLS), stage_runner=stage)
    shard = dict(seen)["pytest"]
    assert shard[-2:] == ("--pytest-arg=-p", "--pytest-arg=no:cacheprovider")
    receipt = (tmp_path / "logs" / "verification-receipt.json").read_text(encoding="utf-8")
    assert "unsafe_pytest_args" not in receipt
