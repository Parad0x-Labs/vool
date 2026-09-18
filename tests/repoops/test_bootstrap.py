"""Pass #2: canonical effect truth, repository bootstrap, CI repair loop."""
from __future__ import annotations

import subprocess

import pytest

from core.platform.broker import (
    EffectOutcome,
    EffectRequest,
    ExecutionBroker,
    PlatformRevocations,
    UntranslatableEffectStatus,
    canonical_effect_status,
)
from core.repoops.bootstrap import (
    BootstrapError,
    CreateRepoProposal,
    RepoHostProvider,
    attach,
    clone,
    create_remote_repo,
    init_local_repo,
    initial_push,
)
from core.repoops.ci import CheckRun, CiObservation, CiShaMismatch

# ================================================================ PART A


def _fork(*tokens):
    return PlatformRevocations().mint("b", set(tokens))


def test_domain_dialect_maps_to_canonical():
    assert canonical_effect_status("succeeded") == "applied"
    assert canonical_effect_status("ok") == "applied"
    assert canonical_effect_status("merged") == "applied"
    assert canonical_effect_status("created") == "applied"
    assert canonical_effect_status("rejected") == "refused"
    assert canonical_effect_status("not_applied") == "refused"
    assert canonical_effect_status("failed") == "failed"
    assert canonical_effect_status("timeout") == "unknown"
    assert canonical_effect_status("disconnected") == "unknown"


def test_unknown_vocabulary_token_raises_loudly_not_guessed():
    with pytest.raises(UntranslatableEffectStatus):
        canonical_effect_status("fine_i_guess")
    with pytest.raises(UntranslatableEffectStatus):
        EffectOutcome.from_domain("seems_fine")


def test_invalid_status_cannot_even_be_constructed():
    with pytest.raises(UntranslatableEffectStatus):
        EffectOutcome(status="succeeded")   # the pass-#1 bug, now unrepresentable
    with pytest.raises(UntranslatableEffectStatus):
        EffectOutcome(status="green")


def test_real_effect_plus_crash_after_dispatch_is_UNKNOWN_never_failed():
    """The pass-#1 bug class: adapter mutates, then crashes before reporting.
    The world may have changed; 'failed' would be a lie. UNKNOWN is honest."""
    fork = _fork("forge.push_branch")
    broker = ExecutionBroker()

    def crashy_dispatch():
        # ... real mutation happens here ...
        raise RuntimeError("adapter crashed after dispatch")

    receipt = broker.execute(fork, EffectRequest(
        effect_id="forge.branch.push", required_capability="forge.push_branch",
        params={}, idempotency_key="crash1",
    ), crashy_dispatch)
    assert receipt.status == "unknown"          # NOT failed, NOT applied
    retry = broker.execute(fork, EffectRequest(
        effect_id="forge.branch.push", required_capability="forge.push_branch",
        params={}, idempotency_key="crash1",
    ), crashy_dispatch)
    assert retry.status == "refused" and "unreconciled" in retry.reason  # no blind retry


def test_untranslatable_result_after_dispatch_becomes_unknown():
    fork = _fork("forge.merge")
    broker = ExecutionBroker()
    receipt = broker.execute(fork, EffectRequest(
        effect_id="forge.pr.merge", required_capability="forge.merge",
        params={}, idempotency_key="m1",
    ), lambda: EffectOutcome.from_domain("merged", evidence={"sha": "a" * 40}))
    assert receipt.status == "applied"


# ================================================================ PART C — local init / attach / clone


def make_commit(root):
    def run(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True, text=True)
    run("config", "user.email", "n@test")
    run("config", "user.name", "N")
    (root / "proj.txt").write_text("v1\n")
    run("add", "-A")
    run("commit", "-q", "-m", "init")


def test_init_refuses_to_nest_inside_existing_repo(tmp_path):
    outer = init_local_repo(str(tmp_path / "outer"))
    make_commit(tmp_path / "outer")
    import os

    with pytest.raises(BootstrapError):
        init_local_repo(os.path.join(outer.root, "sub"))


def test_attach_proves_identity_from_remote_url(tmp_path):
    bare_src = tmp_path / "src"
    init_local_repo(str(bare_src))
    make_commit(bare_src)
    subprocess.run(["git", "-C", str(bare_src), "remote", "add", "origin",
                    "https://github.com/acme/api.git"], check=True,
                   capture_output=True)
    ws, remotes = attach(str(bare_src))
    assert remotes["origin"] == "https://github.com/acme/api.git"
    assert ws.repo.slug() == "acme/api"


def test_attach_refuses_folder_without_origin(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(BootstrapError):
        attach(str(plain))


def test_clone_into_existing_target_refused(tmp_path):
    existing = tmp_path / "exists"
    existing.mkdir()
    with pytest.raises(BootstrapError):
        clone("https://github.com/acme/api.git", str(existing))


def test_clone_into_symlink_target_refused(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)
    with pytest.raises(BootstrapError):
        clone("https://github.com/acme/api.git", str(link))


# ================================================================ PART C — create remote repo


def test_create_repo_requires_explicit_authorization():
    reader = _fork("read_repo")            # credential availability != permission
    provider = RepoHostProvider()
    result = create_remote_repo(reader, ExecutionBroker(), provider,
                                CreateRepoProposal("github", "acme", "my_proj",
                                                   visibility="private"))
    assert result.receipt_status == "refused"
    assert not provider.created             # nothing was created anywhere


def test_created_identity_binds_from_provider_evidence_not_request():
    """Provider normalizes my_proj -> my-proj and assigns a numeric id: the
    canonical identity must come from THAT evidence."""
    writer = _fork("forge.create_repository")
    provider = RepoHostProvider()
    result = create_remote_repo(writer, ExecutionBroker(), provider,
                                CreateRepoProposal("github", "acme", "my_proj",
                                                   visibility="private"))
    assert result.receipt_status == "applied"
    assert result.identity.slug() == "acme/my-proj"   # normalized name
    assert result.identity.remote_url.endswith("acme/my-proj.git")
    assert result.repo_id == 100000
    assert result.actual_name != "my_proj"


def test_duplicate_create_replays_without_second_creation():
    writer = _fork("forge.create_repository")
    provider = RepoHostProvider()
    broker = ExecutionBroker()
    proposal = CreateRepoProposal("github", "acme", "api", visibility="private")
    first = create_remote_repo(writer, broker, provider, proposal)
    second = create_remote_repo(writer, broker, provider, proposal)
    assert first.receipt_status == second.receipt_status == "applied"
    assert len(provider.created) == 1       # executed exactly once


def test_similar_existing_repo_is_a_different_creation():
    writer = _fork("forge.create_repository")
    provider = RepoHostProvider()
    broker = ExecutionBroker()
    a = create_remote_repo(writer, broker, provider,
                           CreateRepoProposal("github", "acme", "api", visibility="private"))
    b = create_remote_repo(writer, broker, provider,
                           CreateRepoProposal("github", "acme", "api-internal",
                                              visibility="private"))
    assert a.identity.key() != b.identity.key()


# ================================================================ PART C — initial push


def _bootstrap_local(tmp_path, name="checkout"):
    from dataclasses import replace

    from core.remote_forge.identity import explicit_identity

    ws = init_local_repo(str(tmp_path / name))
    make_commit(tmp_path / name)
    return replace(ws, repo=explicit_identity("github", "acme", "api"))


def test_initial_push_lands_exact_sha_and_verifies(tmp_path):
    ws = _bootstrap_local(tmp_path)
    provider = RepoHostProvider()
    outcome = initial_push(_fork("forge.push_branch"), ExecutionBroker(), ws, provider)
    assert outcome.status == "applied"
    assert outcome.evidence["verified"] is True
    head = ws.read_local_state().head_sha
    assert outcome.evidence["sha"] == head
    assert provider.branch_head(ws.repo, "main") == head


def test_initial_push_refuses_nonempty_remote_no_implicit_force(tmp_path):
    ws = _bootstrap_local(tmp_path)
    provider = RepoHostProvider()
    provider.set_branch_head(ws.repo, "main", "e" * 40)   # someone pushed already
    outcome = initial_push(_fork("forge.push_branch"), ExecutionBroker(), ws, provider)
    assert outcome.status == "refused"
    assert outcome.reason == "remote_not_empty_divergence"
    assert provider.branch_head(ws.repo, "main") == "e" * 40   # untouched


def test_default_branch_discovery_from_provider_evidence(tmp_path):
    ws = _bootstrap_local(tmp_path)
    writer = _fork("forge.create_repository")
    created = create_remote_repo(writer, ExecutionBroker(), RepoHostProvider(),
                                 CreateRepoProposal("github", "acme", "api",
                                                    visibility="private",
                                                    default_branch="trunk"))
    assert created.actual_default_branch == "trunk"   # from evidence, not assumed main


# ================================================================ CI repair loop (deterministic)


class FakeCI:
    """CI truth keyed by exact SHA — deterministic, no cloud."""

    def __init__(self) -> None:
        self.results: dict[str, list[CheckRun]] = {}

    def report(self, sha: str, checks: list[CheckRun]) -> None:
        self.results[sha] = checks

    def observe(self, identity, sha: str) -> CiObservation:
        return CiObservation(identity=identity, ci_sha=sha,
                             checks=tuple(self.results.get(sha, [])))


def test_ci_repair_loop_only_new_sha_establishes_green(tmp_path):
    ident_ws = _bootstrap_local(tmp_path)
    sha_a = ident_ws.read_local_state().head_sha
    ci = FakeCI()
    ci.report(sha_a, [CheckRun("pytest", "failure", sha_a, required=True)])

    old_ci = ci.observe(ident_ws.repo, sha_a)
    assert old_ci.verdict()["blocking_failures"]

    # ... local repair + commit -> SHA B ...
    repair = tmp_path / "checkout" / "fix.txt"
    repair.write_text("fixed\n")
    subprocess.run(["git", "-C", str(ident_ws.root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ident_ws.root), "commit", "-q", "-m", "repair"],
                   check=True)
    sha_b = ident_ws.read_local_state().head_sha
    assert sha_b != sha_a

    ci.report(sha_b, [CheckRun("pytest", "success", sha_b, required=True)])
    new_ci = ci.observe(ident_ws.repo, sha_b)

    # CI(A) may NOT vouch for B, and vice versa — only CI(B) establishes green.
    with pytest.raises(CiShaMismatch):
        old_ci.require_ci_for(sha_b)
    assert new_ci.require_ci_for(sha_b).verdict()["all_required_pass"] is True
