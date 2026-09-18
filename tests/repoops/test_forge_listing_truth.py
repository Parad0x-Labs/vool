"""Forge listing truth: pagination and rate limits must never read as complete silence.

The two defects this pack pins:

1. **One-page reads.** GitHub and GitLab list endpoints page their answers. An adapter that
   reads page one and returns it as THE answer invents a completeness it does not have: a
   failing required check on page two is invisible, and the plane above reports "0 failing"
   over a listing it never finished reading.

2. **Rate limits read as empty bodies.** A definitive 403/429 rate-limit answer is not JSON
   with zero rows and not an absent ref -- it is the forge saying "I served you nothing".
   Reading it as `[]` / `""` turns "unknown" into a false "absent", which is exactly the
   confusion the A6 push reconciliation exists to prevent.

The scripted transports here are wire-shape fakes: the ADAPTER under test is the shipped one,
building its own URLs and parsing real GitHub/GitLab payload shapes. No socket is touched and
none is claimed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.kas.contract import AdapterConfig, KasRequest, KasResponse


@pytest.fixture
def world(tmp_path, monkeypatch):
    from core import policy_engine
    from core.mode_permission_policy import reset_mode_permission_state
    from core.repoops import forge as forge_bridge
    from core.repoops.plane import repo_ops_runtime

    reset_mode_permission_state()
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    monkeypatch.setenv("VOOL_REPOOPS_DIR", str(tmp_path / "repo_sessions"))
    repo_ops_runtime().reset()
    from tests.repoops._forge_fixture import RecordedForge
    from tests.repoops._harness import build_repo

    root, bare = build_repo(tmp_path)
    forge = RecordedForge()
    forge_bridge.install_transport_factory(forge.factory)
    monkeypatch.setattr(
        policy_engine, "get", _policy_with(policy_engine.get, {"repo.real_push_enabled": True})
    )
    try:
        yield root, bare, forge
    finally:
        forge_bridge.install_transport_factory(None)
        repo_ops_runtime().reset()
        reset_mode_permission_state()


def _policy_with(original, overrides):
    def _get(key, default=None):
        if key in overrides:
            return overrides[key]
        return original(key, default)

    return _get


def _run(run_id: int, name: str, conclusion: str, sha: str) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "head_sha": sha,
        "logs_url": f"https://api.github.com/repos/o/r/actions/runs/{run_id}/logs",
    }


def _github_adapter(transport):
    from core.kas.adapters.github import GitHubForgeAdapter

    return GitHubForgeAdapter(
        transport=transport,
        config=AdapterConfig(provider_id="github", base_url="https://api.github.com", namespace="o/r"),
    )


class _ScriptedWire:
    """Callable standing in for the ONE transport: serves queued responses in order.

    Each entry is ``(status, payload, headers)``. Headers matter here because pagination and
    rate limits are HEADER truths on the real wire (``Link``, ``X-RateLimit-Remaining``).
    """

    def __init__(self, *responses: tuple[int, Any, dict[str, str]]) -> None:
        self.responses = list(responses)
        self.requests: list[KasRequest] = []

    def __call__(self, request: KasRequest) -> KasResponse:
        self.requests.append(request)
        if not self.responses:
            return KasResponse(status=404, body=b'{"message": "not found"}')
        status, payload, headers = self.responses.pop(0)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return KasResponse(status=int(status), body=body, headers=dict(headers))


def _link(page: int) -> str:
    return f'<https://api.github.com/repos/o/r/actions/runs?head_sha=abc&per_page=100&page={page + 1}>; rel="next"'


def _page_route_key(page: int) -> str:
    """A route key more specific than the page-1 prefix, so longest-match picks it."""
    return f"GET actions/runs?head_sha=abc&per_page=100&page={page}"


# ---------------------------------------------------------------------------
# Pagination: every page the forge offers must be read
# ---------------------------------------------------------------------------


def test_ci_jobs_reads_every_page_the_forge_offers() -> None:
    """The original failure: 30 passing runs on page 1, the ONE failing required check on
    page 2. The shipped adapter returned page 1 as the complete CI truth."""
    page1 = [_run(i, f"check-{i}", "success", "abc") for i in range(30)]
    page2 = [_run(9999, "required-e2e", "failure", "abc")]
    wire = _ScriptedWire(
        (200, {"workflow_runs": page1}, {"Link": _link(1)}),
        (200, {"workflow_runs": page2}, {}),
    )
    adapter = _github_adapter(wire)
    listing = adapter.ci_jobs("abc")
    names = [job.name for job in listing.rows]
    assert "required-e2e" in names, "the failing check on page 2 was invisible to a one-page read"
    assert len(listing.rows) == 31
    assert listing.truncated is False
    assert listing.pages_read == 2
    # Page size was requested, so the forge pages honestly rather than at its 30-row default.
    assert "per_page=100" in wire.requests[0].url


def test_ci_jobs_names_truncation_when_the_page_bound_stops_reading() -> None:
    """A bounded runtime must stop reading eventually; when it does, the listing SAYS SO
    instead of presenting the rows it has as the whole truth."""
    pages = [
        (200, {"workflow_runs": [_run(i, f"check-{i}", "success", "abc")]}, {"Link": _link(i + 1)})
        for i in range(12)
    ]
    wire = _ScriptedWire(*pages)
    adapter = _github_adapter(wire)
    listing = adapter.ci_jobs("abc")
    assert listing.truncated is True
    assert listing.pages_read >= 1
    assert len(listing.rows) >= 1


def test_ci_jobs_truncation_is_not_a_success_projection_at_the_door(world) -> None:
    """Plane level: a truncated listing must not produce `ok` CI truth. `failed == []` over a
    truncated read is silence, not greenness."""
    from tests.repoops._forge_fixture import github_pull_request
    from tests.repoops._harness import context, door, git, head

    root, bare, forge = world
    ctx = context(root)
    sha = head(root)
    base = git(root, "rev-parse", "HEAD~1").strip()
    forge.route("GET /pulls/7", github_pull_request("7", base_sha=base, head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})

    opened = door(
        "repo.session.open",
        {"objective": "repair the failing unit job", "pull_request": "7", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"},
        ctx,
    )
    sid = opened.details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)

    # The recorded forge speaks pagination through headers now.
    page1 = {"workflow_runs": [_run(1, "unit", "success", sha)]}
    forge.route("GET /actions/runs?head_sha=", page1, headers={"Link": _link(1).replace('head_sha=abc', 'head_sha=' + sha)})
    forge.route(_page_route_key(2).replace('head_sha=abc', 'head_sha=' + sha), {"workflow_runs": [_run(2, "required-e2e", "failure", sha)]}, headers={"Link": _link(2).replace('head_sha=abc', 'head_sha=' + sha)})
    for page in range(3, 14):
        forge.route(_page_route_key(page).replace('head_sha=abc', 'head_sha=' + sha), {"workflow_runs": [_run(page, f"check-{page}", "success", sha)]}, headers={"Link": _link(page).replace('head_sha=abc', 'head_sha=' + sha)})

    jobs = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert jobs.ok is False
    assert jobs.status == "listing_truncated"
    assert jobs.details["truncated"] is True
    # What WAS read is still attached as data -- never as the CI verdict.
    assert any(j["name"] == "required-e2e" for j in jobs.details["jobs"])


def test_ci_artifacts_reports_truncation_too() -> None:
    page1 = {"artifacts": [{"id": i, "name": f"a{i}", "size_in_bytes": 1, "archive_download_url": "u"} for i in range(30)]}
    wire = _ScriptedWire(
        (200, {"workflow_runs": [_run(4242, "unit", "success", "abc")]}, {}),
        (200, page1, {"Link": '<https://api.github.com/repos/o/r/actions/runs/4242/artifacts?per_page=100&page=2>; rel="next"'}),
        (200, {"artifacts": [{"id": 99, "name": "a99", "size_in_bytes": 2, "archive_download_url": "u"}]}, {}),
    )
    adapter = _github_adapter(wire)
    listing = adapter.ci_artifacts("abc")
    assert [a.name for a in listing.rows] and "a99" in [a.name for a in listing.rows]
    assert listing.truncated is False


# ---------------------------------------------------------------------------
# Rate limits: a definitive refusal is data-not-served, never absence
# ---------------------------------------------------------------------------


def test_rate_limited_ci_jobs_raises_the_typed_refusal() -> None:
    """The original failure: a 403 rate-limit body parsed as zero workflow_runs and the
    plane told the user `0 CI job(s); 0 failing` -- a success projection over nothing."""
    from core.kas.contract import ForgeRateLimitedError

    wire = _ScriptedWire(
        (
            403,
            {"message": "API rate limit exceeded"},
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "60"},
        )
    )
    adapter = _github_adapter(wire)
    with pytest.raises(ForgeRateLimitedError):
        adapter.ci_jobs("abc")


def test_secondary_rate_limit_429_is_typed_too() -> None:
    from core.kas.contract import ForgeRateLimitedError

    wire = _ScriptedWire((429, {"message": "You have exceeded a secondary rate limit"}, {"Retry-After": "30"}))
    adapter = _github_adapter(wire)
    with pytest.raises(ForgeRateLimitedError) as caught:
        adapter.ci_jobs("abc")
    assert caught.value.retry_after == 30.0


def test_rate_limited_resolve_ref_is_not_an_absent_ref() -> None:
    """The sharpest edge: the A6 push resolver reads `resolve_ref() == ""` as `the ref does
    not exist, the push never landed, safe to retry`. A rate-limited read returning "" there
    can turn an APPLIED push into a retry."""
    from core.kas.contract import ForgeRateLimitedError

    wire = _ScriptedWire((403, {"message": "API rate limit exceeded"}, {"X-RateLimit-Remaining": "0"}))
    adapter = _github_adapter(wire)
    with pytest.raises(ForgeRateLimitedError):
        adapter.resolve_ref("feature")


def test_absent_ref_still_reads_as_absent() -> None:
    """Preservation control: a genuine 404 remains the one truthful 'absent' answer."""
    wire = _ScriptedWire((404, {"message": "No commit found for SHA: aaa"}, {}))
    adapter = _github_adapter(wire)
    assert adapter.resolve_ref("aaa") == ""


def test_server_error_on_resolve_ref_is_not_an_absent_ref() -> None:
    """A 500 is the forge failing, not the ref missing -- it must refuse, not return ""."""
    from core.kas.contract import ForgeRefusedError

    wire = _ScriptedWire((500, {"message": "Server Error"}, {}))
    adapter = _github_adapter(wire)
    with pytest.raises(ForgeRefusedError):
        adapter.resolve_ref("feature")


def test_rate_limited_ci_jobs_is_typed_at_the_door(world) -> None:
    """Plane level: the user sees `provider_rate_limited`, never a green CI summary built
    from a body the forge refused to serve."""
    from tests.repoops._forge_fixture import github_pull_request
    from tests.repoops._harness import context, door, git, head

    root, bare, forge = world
    ctx = context(root)
    sha = head(root)
    base = git(root, "rev-parse", "HEAD~1").strip()
    forge.route("GET /pulls/7", github_pull_request("7", base_sha=base, head_sha=sha))
    forge.route("GET /commits/feature", {"sha": sha})
    forge.route(
        "GET /actions/runs?head_sha=",
        {"message": "API rate limit exceeded"},
        status=403,
        headers={"X-RateLimit-Remaining": "0"},
    )
    opened = door(
        "repo.session.open",
        {"objective": "repair the failing unit job", "pull_request": "7", "provider": "github", "namespace": "o/r", "reviewer_model": "anthropic/claude-sonnet-4"},
        ctx,
    )
    sid = opened.details["repo_session_id"]
    door("repo.inspect", {"repo_session_id": sid}, ctx)
    door("repo.bind", {"repo_session_id": sid}, ctx)
    jobs = door("repo.ci.jobs", {"repo_session_id": sid}, ctx)
    assert jobs.ok is False
    assert jobs.status == "provider_rate_limited"
    assert "rate" in jobs.response_text.lower()
    assert "jobs" not in jobs.details or not jobs.details.get("jobs")


# ---------------------------------------------------------------------------
# GitLab: the same laws on the other wire
# ---------------------------------------------------------------------------


def _gitlab_adapter(transport):
    from core.kas.adapters.gitlab import GitLabForgeAdapter

    return GitLabForgeAdapter(
        transport=transport,
        config=AdapterConfig(provider_id="gitlab", base_url="https://gitlab.com/api/v4", namespace="o/r"),
    )


def _pipeline(i: int, sha: str) -> dict[str, Any]:
    return {"id": i, "sha": sha, "status": "success", "web_url": f"https://gitlab.com/o/r/-/pipelines/{i}"}


def _job(i: int, name: str, status: str, sha: str) -> dict[str, Any]:
    return {"id": 1000 + i, "name": name, "status": status, "commit": {"id": sha}, "web_url": "u"}


def test_gitlab_ci_jobs_follows_pipeline_pages() -> None:
    from tests.repoops._forge_fixture import RecordedForge

    forge = RecordedForge()
    forge.route(
        "GET /pipelines?sha=",
        [_pipeline(i, "abc") for i in range(1, 3)],
        headers={"Link": '<https://gitlab.com/api/v4/projects/o%2Fr/pipelines?sha=abc&per_page=100&page=2>; rel="next"'},
    )
    forge.route("GET pipelines?sha=abc&per_page=100&page=2", [_pipeline(99, "abc")])
    forge.route("GET /jobs", [_job(1, "unit", "success", "abc")])
    forge.route("GET /pipelines/99/jobs", [_job(99, "required-e2e", "failed", "abc")])
    adapter = _gitlab_adapter(forge.factory())
    listing = adapter.ci_jobs("abc")
    names = [job.name for job in listing.rows]
    assert "required-e2e" in names
    assert listing.truncated is False
    assert listing.pages_read == 5


def test_gitlab_rate_limited_pipelines_refuses() -> None:
    from core.kas.contract import ForgeRateLimitedError
    from tests.repoops._forge_fixture import RecordedForge

    forge = RecordedForge()
    forge.route("GET /pipelines?sha=", {"message": "429 Too Many Requests"}, status=429, headers={"Retry-After": "60"})
    adapter = _gitlab_adapter(forge.factory())
    with pytest.raises(ForgeRateLimitedError) as caught:
        adapter.ci_jobs("abc")
    assert caught.value.retry_after == 60.0
