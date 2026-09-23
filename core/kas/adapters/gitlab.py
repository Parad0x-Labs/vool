"""GitLab, as a KAS adapter — the same contract, a different wire.

GitLab calls a pull request a merge request, numbers it per-project, keys pipelines by ref and
returns a diff as structured JSON rather than a patch. All of that difference stops here: what
leaves this module is the same `Forge*` vocabulary GitHub's adapter produces, so RepoOps never
learns which forge it is talking to.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from core.kas.adapters._forge_reads import json_payload, listing_rows, object_payload, scoped_next_page
from core.kas.contract import (
    AdapterConfig,
    ForgeAcceptedUnreadableError,
    ForgeAdapter,
    ForgeArtifact,
    ForgeCiJob,
    ForgeIssue,
    ForgeIssueComment,
    ForgeListing,
    ForgePullRequest,
    ForgeRateLimitedError,
    ForgeRefusedError,
    ForgeRepository,
    KasRequest,
    KasResponse,
)
from core.kas.registry import register_adapter

_MAX_LIST_PAGES = 10
_PER_PAGE = 100



def _header(response: KasResponse, name: str) -> str:
    wanted = str(name).lower()
    for key, value in dict(response.headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _next_page_url(link_header: str) -> str:
    for chunk in str(link_header or "").split(","):
        segments = chunk.split(";")
        url = segments[0].strip().strip("<>").strip()
        for parameter in segments[1:]:
            if parameter.strip().lower().startswith('rel="next"') and url:
                return url
    return ""


def _unified_diff(changes: list[dict[str, Any]]) -> str:
    """Rebuild a unified diff from GitLab's per-file change objects.

    GitLab hands back `{old_path, new_path, diff}` rather than a patch, so the file headers are
    reconstructed here. This is format translation — exactly what an adapter is for.
    """

    blocks: list[str] = []
    for change in changes:
        row = dict(change or {})
        old = str(row.get("old_path") or "")
        new = str(row.get("new_path") or old)
        body = str(row.get("diff") or "")
        if not body:
            continue
        blocks.append(f"diff --git a/{old} b/{new}\n--- a/{old}\n+++ b/{new}\n{body.rstrip()}")
    return "\n".join(blocks) + ("\n" if blocks else "")


@register_adapter
class GitLabForgeAdapter(ForgeAdapter):
    kind = "forge"
    provider_id = "gitlab"

    def __init__(self, *, transport: Any, config: AdapterConfig) -> None:
        super().__init__(transport=transport, config=config)
        self._project = quote(str(config.namespace or "").strip("/"), safe="")

    # -- request helpers ---------------------------------------------------------------

    def _url(self, suffix: str) -> str:
        return f"{self.config.base_url}/projects/{self._project}{suffix}"

    def _get_url(self, url: str, *, purpose: str):
        response = self.send(
            KasRequest(
                method="GET",
                url=url,
                purpose=purpose,
                headers={"Accept": "application/json"},
                auth=self.config.auth_binding,
            )
        )
        if purpose != "forge.resolve_ref":
            self._refuse(response)
        return response

    def _get(self, suffix: str, *, purpose: str):
        return self._get_url(self._url(suffix), purpose=purpose)

    def _post(self, suffix: str, *, purpose: str, body: dict[str, Any] | None = None):
        return self.send(
            KasRequest(
                method="POST",
                url=self._url(suffix),
                purpose=purpose,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                body=json.dumps(body or {}).encode("utf-8"),
                auth=self.config.auth_binding,
                mutating=True,
            )
        )

    def _put(self, suffix: str, *, purpose: str, body: dict[str, Any] | None = None):
        return self.send(
            KasRequest(
                method="PUT",
                url=self._url(suffix),
                purpose=purpose,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                body=json.dumps(body or {}).encode("utf-8"),
                auth=self.config.auth_binding,
                mutating=True,
            )
        )

    def _refuse(self, response: KasResponse) -> None:
        """A definitive non-2xx is the forge not serving the answer — never zeros to parse."""
        if response.ok:
            return
        status = int(response.status)
        if status == 404:
            raise ForgeRefusedError(status, reason="not_found")
        remaining = _header(response, "ratelimit-remaining")
        retry_after = _header(response, "retry-after")
        wait = 0.0
        try:
            wait = float(retry_after) if retry_after else 0.0
        except ValueError:
            wait = 0.0
        if status == 429 or (status == 403 and (remaining.strip() == "0" or retry_after)):
            raise ForgeRateLimitedError(status_code=status, retry_after=wait)
        raise ForgeRefusedError(status, reason=f"http_{status}")

    def _accepted_call(self, response: KasResponse, convert):
        """Decode AND convert a SUCCESSFUL write reply, or raise the accepted-but-unreadable truth.

        The forge answered 2xx: the write was accepted. Everything after that -- JSON decoding,
        object shaping, nested-field typing (GitLab's `author`/`diff_refs` shapes included),
        typed-result construction -- is LOCAL processing of an accepted mutation; none of it
        can prove the write did not happen, so any failure here raises
        ForgeAcceptedUnreadableError and the outcome stays unknown."""

        try:
            payload = object_payload(response.body)
            return convert(payload)
        except ForgeAcceptedUnreadableError:
            raise
        except ForgeRefusedError as exc:
            raise ForgeAcceptedUnreadableError(
                int(response.status), detail=str(exc.reason or "unreadable_accepted_reply")
            ) from exc
        except Exception as exc:
            raise ForgeAcceptedUnreadableError(
                int(response.status), detail=f"conversion_{type(exc).__name__.lower()}"
            ) from exc

    def _pages(self, suffix: str, *, purpose: str, max_pages: int | None = None) -> ForgeListing[dict[str, Any]]:
        """Bounded Link-header pagination; GitLab list bodies are bare row arrays."""
        separator = "&" if "?" in suffix else "?"
        first = current = self._url(f"{suffix}{separator}per_page={_PER_PAGE}")
        visited = set()
        limit = _MAX_LIST_PAGES if max_pages is None else max(0, min(_MAX_LIST_PAGES, max_pages))
        rows: list[dict[str, Any]] = []
        pages = 0
        while pages < limit:
            visited.add(current)
            response = self._get_url(current, purpose=purpose)
            self._refuse(response)
            rows.extend(listing_rows(json_payload(response.body)))
            pages += 1
            following = _next_page_url(_header(response, "link"))
            if not following:
                return ForgeListing(rows=rows, pages_read=pages, truncated=False)
            current = scoped_next_page(first, current, following, visited)
        return ForgeListing(rows=rows, pages_read=pages, truncated=True)

    # -- the contract ------------------------------------------------------------------

    def describe_repository(self) -> ForgeRepository:
        payload = object_payload(self._get("", purpose="forge.repository").body)
        return ForgeRepository(
            provider_id=self.provider_id,
            full_name=str(payload.get("path_with_namespace") or self.config.namespace),
            default_branch=str(payload.get("default_branch") or ""),
            clone_url=str(payload.get("http_url_to_repo") or ""),
            private=str(payload.get("visibility") or "") != "public",
        )

    def describe_pull_request(self, number: str) -> ForgePullRequest:
        ref = quote(str(number).strip(), safe="")
        payload = object_payload(self._get(f"/merge_requests/{ref}", purpose="forge.pull_request").body)
        return self._merge_request(payload, fallback_number=number)

    def _merge_request(self, payload: dict[str, Any], *, fallback_number: str = "") -> ForgePullRequest:
        title = str(payload.get("title") or "")
        draft = bool(payload.get("draft") or payload.get("work_in_progress") or False)
        if draft:
            # GitLab's draft state IS a title prefix; the shared vocabulary carries the state in
            # `draft`, so the prefix is stripped here or every exact-title comparison above the
            # adapter would have to know which forge it was talking to.
            for prefix in ("Draft:", "draft:", "WIP:", "wip:"):
                if title.startswith(prefix) and len(title) > len(prefix):
                    title = title[len(prefix) :].lstrip()
                    break
        return ForgePullRequest(
            provider_id=self.provider_id,
            number=str(payload.get("iid") or fallback_number),
            title=title,
            # GitLab says "opened"; the shared vocabulary says "open".
            state="open" if str(payload.get("state") or "") == "opened" else str(payload.get("state") or ""),
            base_ref=str(payload.get("target_branch") or ""),
            base_sha=str((payload.get("diff_refs") or {}).get("base_sha") or ""),
            head_ref=str(payload.get("source_branch") or ""),
            head_sha=str(payload.get("sha") or (payload.get("diff_refs") or {}).get("head_sha") or ""),
            merge_state=str(payload.get("merge_status") or ""),
            draft=draft,
            url=str(payload.get("web_url") or ""),
            body=str(payload.get("description") or ""),
        )

    def pull_request_diff(self, number: str) -> str:
        ref = quote(str(number).strip(), safe="")
        payload = object_payload(self._get(f"/merge_requests/{ref}/changes", purpose="forge.diff").body)
        return _unified_diff(list(payload.get("changes") or []))

    def resolve_ref(self, ref: str) -> str:
        clean = str(ref or "").strip()
        if not clean:
            return ""
        response = self._get(f"/repository/commits/{quote(clean, safe='')}", purpose="forge.resolve_ref")
        if response.ok:
            payload = object_payload(response.body)
            identity = payload.get("id")
            if not isinstance(identity, str) or not identity.strip():
                raise ForgeRefusedError(200, reason="missing_ref_identity")
            return identity
        if int(response.status) == 404:
            # The contract's one documented absence: "Empty string if absent."
            return ""
        # Anything else definitive is the forge refusing, not the ref missing.
        self._refuse(response)
        return ""

    def ci_jobs(self, sha: str) -> ForgeListing[ForgeCiJob]:
        clean = quote(str(sha or "").strip(), safe="")
        pipelines = self._pages(f"/pipelines?sha={clean}", purpose="forge.ci_pipelines")
        jobs: list[ForgeCiJob] = []
        pages = pipelines.pages_read
        truncated = pipelines.truncated
        for pipeline in pipelines.rows:
            if pages >= _MAX_LIST_PAGES:
                truncated = True
                break
            pipeline_id = quote(str(dict(pipeline or {}).get("id") or ""), safe="")
            if not pipeline_id:
                raise ForgeRefusedError(200, reason="missing_pipeline_identity")
            if pipeline.get('sha') != str(sha).strip():
                raise ForgeRefusedError(200, reason='ci_revision_mismatch')
            listing = self._pages(f"/pipelines/{pipeline_id}/jobs", purpose="forge.ci_jobs", max_pages=_MAX_LIST_PAGES - pages)
            pages += listing.pages_read
            truncated = truncated or listing.truncated
            for row in listing.rows:
                status = str(row.get("status") or "")
                if row.get('id') is None or not status:
                    raise ForgeRefusedError(200, reason='malformed_ci_job')
                commit = row.get('commit')
                if not isinstance(commit, dict) or not commit.get('id') or commit['id'] != str(sha).strip():
                    raise ForgeRefusedError(200, reason='ci_revision_mismatch')
                jobs.append(
                    ForgeCiJob(
                        provider_id=self.provider_id,
                        job_id=str(row.get("id") or ""),
                        name=str(row.get("name") or ""),
                        # GitLab folds status and conclusion into one field; the shared shape
                        # keeps them apart, so a terminal status is reported as both.
                        status="completed" if status in {"success", "failed", "canceled"} else status,
                        conclusion=status if status in {"success", "failed", "canceled"} else "",
                        head_sha=commit['id'],
                        log_ref=str(row.get("web_url") or ""),
                    )
                )
        return ForgeListing(rows=jobs, pages_read=pages, truncated=truncated)

    def ci_job_log(self, job_id: str) -> str:
        ref = quote(str(job_id).strip(), safe="")
        return self._get(f"/jobs/{ref}/trace", purpose="forge.ci_log").text()

    def ci_artifacts(self, sha: str) -> ForgeListing[ForgeArtifact]:
        runs = self.ci_jobs(sha)
        artifacts: list[ForgeArtifact] = []
        pages = runs.pages_read
        truncated = runs.truncated
        for job in runs.rows:
            if pages >= _MAX_LIST_PAGES:
                truncated = True
                break
            ref = quote(str(job.job_id).strip(), safe="")
            response = self._get(f"/jobs/{ref}", purpose="forge.ci_artifacts")
            self._refuse(response)
            pages += 1
            payload = object_payload(response.body)
            for entry in list(payload.get("artifacts") or []):
                row = dict(entry or {})
                artifacts.append(
                    ForgeArtifact(
                        provider_id=self.provider_id,
                        artifact_id=f"{job.job_id}:{row.get('filename') or ''}",
                        name=str(row.get("filename") or ""),
                        size_bytes=int(row.get("size") or 0),
                        download_ref=f"{self.config.base_url}/projects/{self._project}/jobs/{ref}/artifacts",
                    )
                )
        return ForgeListing(rows=artifacts, pages_read=pages, truncated=truncated)

    def describe_issue(self, number: str) -> ForgeIssue:
        ref = quote(str(number).strip(), safe="")
        response = self._get(f"/issues/{ref}", purpose="forge.issue")
        self._refuse(response)
        payload = object_payload(response.body)
        return ForgeIssue(
            provider_id=self.provider_id,
            number=str(payload.get("iid") or number),
            title=str(payload.get("title") or ""),
            state="open" if str(payload.get("state") or "") == "opened" else str(payload.get("state") or ""),
            author=str(dict(payload.get("author") or {}).get("username") or ""),
            body=str(payload.get("description") or ""),
            labels=tuple(str(label or "") for label in list(payload.get("labels") or [])),
            comments_count=0,
        )

    def issue_comments(self, number: str) -> ForgeListing[ForgeIssueComment]:
        ref = quote(str(number).strip(), safe="")
        listing = self._pages(f"/issues/{ref}/notes", purpose="forge.issue_comments")
        rows = [
            ForgeIssueComment(
                provider_id=self.provider_id,
                comment_id=str(row.get("id") or ""),
                author=str(dict(row.get("author") or {}).get("username") or ""),
                body=str(row.get("body") or ""),
                created_at=str(row.get("created_at") or ""),
            )
            for row in listing.rows
        ]
        return ForgeListing(rows=rows, pages_read=listing.pages_read, truncated=listing.truncated)

    def rerun_ci(self, job_id: str) -> bool:
        ref = quote(str(job_id).strip(), safe="")
        return self._post(f"/jobs/{ref}/retry", purpose="forge.ci_rerun").ok

    def cancel_ci(self, job_id: str) -> bool:
        ref = quote(str(job_id).strip(), safe="")
        return self._post(f"/jobs/{ref}/cancel", purpose="forge.ci_cancel").ok

    # -- the explicit-authorized writes -------------------------------------------------

    def create_pull_request(
        self, *, title: str, body: str, head_ref: str, base_ref: str, draft: bool
    ) -> ForgePullRequest:
        # GitLab has no draft FLAG on the create wire: a draft merge request is a title
        # convention. The prefix is applied here so the shared contract keeps `draft=True`
        # meaning draft on both forges, and the answer's own work_in_progress flag is what the
        # runtime reads back — if the forge ignored the convention, that is visible.
        clean_title = str(title).strip()
        if draft and not clean_title.lower().startswith("draft:"):
            clean_title = f"Draft: {clean_title}"
        response = self._post(
            "/merge_requests",
            purpose="forge.pr_create",
            body={
                "title": clean_title,
                "source_branch": str(head_ref),
                "target_branch": str(base_ref),
                "description": str(body),
            },
        )
        self._refuse(response)
        return self._accepted_call(response, self._merge_request)

    def update_pull_request(self, number: str, *, title: str = "", body: str = "") -> ForgePullRequest:
        changes: dict[str, Any] = {}
        if str(title).strip():
            changes["title"] = str(title).strip()
        if str(body).strip():
            changes["description"] = str(body)
        if not changes:
            raise ForgeRefusedError(200, reason="empty_update")
        ref = quote(str(number).strip(), safe="")
        # A title rewrite can silently flip a GitLab draft to ready (draft IS the title prefix
        # there), so the current remote record is read first and a draft's prefix is preserved
        # through a title update. The answer is still what the forge says after the write.
        if "title" in changes:
            current = self.describe_pull_request(str(number))
            if current.draft and not changes["title"].lower().startswith("draft:"):
                changes["title"] = f"Draft: {changes['title']}"
        response = self._put(f"/merge_requests/{ref}", purpose="forge.pr_update", body=changes)
        self._refuse(response)
        return self._accepted_call(response, lambda payload: self._merge_request(payload, fallback_number=str(number)))

    def post_comment(self, number: str, body: str, *, subject: str) -> ForgeIssueComment:
        clean_subject = str(subject or "").strip().lower()
        if clean_subject == "issue":
            suffix = "/issues"
        elif clean_subject == "pull_request":
            suffix = "/merge_requests"
        else:
            raise ForgeRefusedError(200, reason="unknown_comment_subject")
        ref = quote(str(number).strip(), safe="")
        response = self._post(f"{suffix}/{ref}/notes", purpose="forge.comment_create", body={"body": str(body)})
        self._refuse(response)
        return self._accepted_call(response, self._comment_from)

    def _comment_from(self, payload: dict[str, Any]) -> ForgeIssueComment:
        return ForgeIssueComment(
            provider_id=self.provider_id,
            comment_id=str(payload.get("id") or ""),
            author=str(dict(payload.get("author") or {}).get("username") or ""),
            body=str(payload.get("body") or ""),
            created_at=str(payload.get("created_at") or ""),
            url=str(payload.get("noteable_url") or payload.get("url") or ""),
        )

    def find_pull_request(self, *, head_ref: str, base_ref: str) -> ForgePullRequest | None:
        suffix = (
            f"/merge_requests?state=opened"
            f"&source_branch={quote(str(head_ref).strip(), safe='')}"
            f"&target_branch={quote(str(base_ref).strip(), safe='')}"
        )
        listing = self._pages(suffix, purpose="forge.pr_lookup")
        for row in listing.rows:
            candidate = self._merge_request(row)
            if candidate.head_ref == str(head_ref).strip() and candidate.base_ref == str(base_ref).strip():
                return candidate
        return None


__all__ = ["GitLabForgeAdapter"]
