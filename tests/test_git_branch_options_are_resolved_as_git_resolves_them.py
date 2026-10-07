"""`git branch` options are classified by what git does with them, not by literal equality.

Pack 2b review, 2026-10-07: the read-only classifier matched mutating `git branch` flags by equality, so
`--set-upstream-to=origin/main` (equals-value form), `-uorigin/main` (packed short option) and
`--unset-up` / `--edit-des` (unambiguous long-option prefixes, which git accepts) were all read-only in
Plan/Review modes. `git branch --unset-up` rewrites `.git/config` in a real repository.
"""
from __future__ import annotations

import os
import shlex
import subprocess

import pytest

from core.execution_gate import ExecutionGate
from core.mode_permission_policy import command_is_read_only

MUTATING = [
    "git branch --set-upstream-to=origin/main",
    "git branch --set-upstream-to origin/main",
    "git branch -uorigin/main",
    "git branch -u origin/main",
    "git branch --unset-up",
    "git branch --unset-upstream",
    "git branch --edit-des",
    "git branch --del feature",
    "git branch -vd feature",
    "git branch -Dr origin/old",
    "git branch --mov old new",
    "git branch --track=direct new origin/main",
    "git branch --no-tr new origin/main",
    "git branch --create-ref new",
    "git branch -- new",
]

READS = [
    "git branch",
    "git branch -a -v",
    "git branch -vv",
    "git branch -r",
    "git branch --list",
    "git branch --show-current",
    "git branch --contains HEAD",
    "git branch --no-merged main",
    "git branch --merged=main",
    "git branch --sort=-committerdate",
    "git branch --sort -committerdate",
    "git branch --format=%(refname:short)",
    "git branch --points-at HEAD",
    "git branch --show-cur",
]


@pytest.mark.parametrize("command", MUTATING)
def test_a_mutating_branch_option_is_not_a_read(command):
    assert not command_is_read_only(command), command
    argv = shlex.split(command)
    assert not ExecutionGate._is_read_only_command(argv[0], argv), command


@pytest.mark.parametrize("command", READS)
def test_a_branch_listing_stays_a_read(command):
    assert command_is_read_only(command), command
    argv = shlex.split(command)
    assert ExecutionGate._is_read_only_command(argv[0], argv), command


def test_an_abbreviated_unset_changes_a_real_repository_and_is_not_a_read(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, env=env, text=True, capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "--allow-empty", "-qm", "fixture")
    git("config", "branch.main.remote", "origin")
    git("config", "branch.main.merge", "refs/heads/main")
    before = (tmp_path / ".git" / "config").read_bytes()
    git("branch", "--unset-up")
    assert (tmp_path / ".git" / "config").read_bytes() != before, "the control must really change tracking"
    assert not command_is_read_only("git branch --unset-up")
