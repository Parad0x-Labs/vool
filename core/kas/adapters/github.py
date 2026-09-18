"""GitHub, as a KAS adapter: wire shapes in, typed VOOL data out.

Nothing here decides anything. It has no socket, no credential, no policy and no opinion about
whether a call should happen — it builds a URL, hands the request to the transport VOOL gave it,
and reads the reply into the shared forge vocabulary. Swap it for GitLab and the RepoOps runtime
above it does not change one line, which is the point of the single contract.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from core.kas.adapters._forge_reads import json_payload as _json
from core.kas.adapters._forge_reads import listing_rows, object_payload, scoped_next_page
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

#: List endpoints page their answers; an adapter that reads page one and returns it as the
#: whole truth invents a completeness it does not have. The bound below is deliberately
#: small enough to stay a bounded operation and large enough that a legitimate listing
#: (100 rows/page) is never cut short. Hitting it is REPORTED as truncation, never hidden.
_MAX_LIST_PAGES = 10
_PER_PAGE = 100



def _header(response: KasResponse, name: str) -> str:
    """Case-insensitive header lookup: the wire's casing is the forge's choice, not ours."""
    wanted = str(name).lower()
    for key, value in dict(response.headers or {}).items():
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _next_page_url(link_header: str) -> str:
    """The `rel="next"` URL from an RFC 5988 Link header, or ""."""
    for chunk in str(link_header or "").split(","):
        segments = chunk.split(";")
        url = segments[0].strip().strip("<>").strip()
        for parameter in segments[1:]:
            if parameter.strip().lower().startswith('rel="next"') and url:
                return url
    return ""


@register_adapter
class GitHubForgeAdapter(ForgeAdapter):
    kind = "forge"
    provider_id = "github"

    def __init__(self, *, transport: Any, config: AdapterConfig) -> None:
        super().__init__(transport=transport, config=config)
        self._repo = str(config.namespace or "").strip("/")

    # -- request helpers ---------------------------------------------------------------

    def _url(self, suffix: str) -> str:
        return f"{self.config.base_url}/repos/{self._repo}{suffix}"

    def _get_url(self, url: str, *, purpose: str, accept: str = "application/vnd.github+json"):
        response = self.send(
            KasRequest(
                method="GET",
                url=url,
                purpose=purpose,
                headers={"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"},
                auth=self.config.auth_binding,
            )
        )
        if purpose != "forge.resolve_ref":
            self._refuse(response)
        return response

    def _get(self, suffix: str, *, purpose: str, accept: str = "application/vnd.github+json"):
        return self._get_url(self._url(suffix), purpose=purpose, accept=accept)

    def _post(self, suffix: str, *, purpose: str, body: dict[str, Any] | None = None):
        payload = json.dumps(body or {}).encode("utf-8")
        return self.send(
            KasRequest(
                method="POST",
                url=self._url(suffix),
                purpose=purpose,
                headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json"},
                body=payload,
                auth=self.config.auth_binding,
                mutating=True,
            )
        )

    def _patch(self, suffix: str, *, purpose: str, body: dict[str, Any] | None = None):
        payload = json.dumps(body or {}).encode("utf-8")
        return self.send(
            KasRequest(
                method="PATCH",
                url=self._url(suffix),
                purpose=purpose,
                headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json"},
                body=payload,
                auth=self.config.auth_binding,
                mutating=True,
            )
        )

    def _refuse(self, response: KasResponse) -> None:
        """Translate a definitive non-2xx answer into the typed refusal vocabulary.

        A 403/429 rate limit is the forge saying "I served you nothing"; anything else
        definitive (other than the 404 callers may lawfully read as absence) is a refusal,
        never a body to parse zeros out of.
        """
        if response.ok:
            return
        status = int(response.status)
        if status == 404:
            raise ForgeRefusedError(status, reason="not_found")
        remaining = _header(response, "x-ratelimit-remaining")
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
        object shaping, nested-field typing, typed-result construction -- is LOCAL processing
        of an accepted mutation. None of it can prove the write did not happen, so none of it
        may surface as a refusal a caller could classify as safe-to-resend: any failure in
        this boundary raises ForgeAcceptedUnreadableError and the outcome stays unknown.
        """

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

    def _pages(self, suffix: str, *, purpose: str, items_key: str | None = None, max_pages: int | None = None) -> ForgeListing[dict[str, Any]]:
        """Bounded Link-header pagination over a list endpoint.

        ``items_key`` names the wrapping key ("workflow_runs", "artifacts"); ``None`` means
        the body itself is the row array. Every page is refusal-checked: a rate limit on
        page two must refuse the listing, not vanish it.
        """
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
            rows.extend(listing_rows(_json(response.body), items_key))
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
            full_name=str(payload.get("full_name") or self._repo),
            default_branch=str(payload.get("default_branch") or ""),
            clone_url=str(payload.get("clone_url") or ""),
            private=bool(payload.get("private") or False),
        )

    def describe_pull_request(self, number: str) -> ForgePullRequest:
        ref = quote(str(number).strip(), safe="")
        payload = object_payload(self._get(f"/pulls/{ref}", purpose="forge.pull_request").body)
        return self._pull_request(payload, fallback_number=number)

    def _pull_request(self, payload: dict[str, Any], *, fallback_number: str = "") -> ForgePullRequest:
        """One GitHub pull-request payload into the shared vocabulary. The answer is what the
        forge says NOW — never an echo of what a caller asked for."""

        base = dict(payload.get("base") or {})
        head = dict(payload.get("head") or {})
        return ForgePullRequest(
            provider_id=self.provider_id,
            number=str(payload.get("number") or fallback_number),
            title=str(payload.get("title") or ""),
            state=str(payload.get("state") or ""),
            base_ref=str(base.get("ref") or ""),
            base_sha=str(base.get("sha") or ""),
            head_ref=str(head.get("ref") or ""),
            head_sha=str(head.get("sha") or ""),
            merge_state=str(payload.get("mergeable_state") or ""),
            draft=bool(payload.get("draft") or False),
            url=str(payload.get("html_url") or ""),
            body=str(payload.get("body") or ""),
        )

    def pull_request_diff(self, number: str) -> str:
        ref = quote(str(number).strip(), safe="")
        response = self._get(f"/pulls/{ref}", purpose="forge.diff", accept="application/vnd.github.v3.diff")
        return response.text()

    def resolve_ref(self, ref: str) -> str:
        clean = str(ref or "").strip()
        if not clean:
            return ""
        response = self._get(f"/commits/{quote(clean, safe='')}", purpose="forge.resolve_ref")
        if response.ok:
            payload = object_payload(response.body)
            identity = payload.get("sha")
            if not isinstance(identity, str) or not identity.strip():
                raise ForgeRefusedError(200, reason="missing_ref_identity")
            return identity
        if int(response.status) == 404:
            # The contract's one documented absence: "Empty string if absent."
            return ""
        # Every other non-2xx is the forge not serving the answer — reading THAT as absent
        # would let reconciliation call a landed push safe-to-retry on a rate-limited read.
        self._refuse(response)
        return ""

    def ci_jobs(self, sha: str) -> ForgeListing[ForgeCiJob]:
        clean = quote(str(sha or "").strip(), safe="")
        listing = self._pages(f"/actions/runs?head_sha={clean}", purpose="forge.ci_jobs", items_key="workflow_runs")
        for row in listing.rows:
            if row.get('id') is None or not isinstance(row.get('status'), str) or not row['status']:
                raise ForgeRefusedError(200, reason='malformed_ci_job')
            if not row.get('head_sha') or row['head_sha'] != str(sha).strip():
                raise ForgeRefusedError(200, reason='ci_revision_mismatch')
        jobs = [
            ForgeCiJob(
                provider_id=self.provider_id,
                job_id=str(row["id"]),
                name=str(row.get("name") or ""),
                status=str(row.get("status") or ""),
                conclusion=str(row.get("conclusion") or ""),
                head_sha=row["head_sha"],
                log_ref=str(row.get("logs_url") or ""),
            )
            for row in listing.rows
        ]
        return ForgeListing(rows=jobs, pages_read=listing.pages_read, truncated=listing.truncated)

    def ci_job_log(self, job_id: str) -> str:
        ref = quote(str(job_id).strip(), safe="")
        response = self._get(f"/actions/runs/{ref}/logs", purpose="forge.ci_log", accept="application/vnd.github+json")
        return response.text()

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
            listing = self._pages(f"/actions/runs/{ref}/artifacts", purpose="forge.ci_artifacts", items_key="artifacts", max_pages=_MAX_LIST_PAGES - pages)
            pages += listing.pages_read
            truncated = truncated or listing.truncated
            for row in listing.rows:
                artifacts.append(
                    ForgeArtifact(
                        provider_id=self.provider_id,
                        artifact_id=str(row.get("id") or ""),
                        name=str(row.get("name") or ""),
                        size_bytes=int(row.get("size_in_bytes") or 0),
                        download_ref=str(row.get("archive_download_url") or ""),
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
            number=str(payload.get("number") or number),
            title=str(payload.get("title") or ""),
            state=str(payload.get("state") or ""),
            author=str(dict(payload.get("user") or {}).get("login") or ""),
            body=str(payload.get("body") or ""),
            labels=tuple(str(dict(label or {}).get("name") or "") for label in list(payload.get("labels") or [])),
            comments_count=int(payload.get("comments") or 0),
        )

    def issue_comments(self, number: str) -> ForgeListing[ForgeIssueComment]:
        ref = quote(str(number).strip(), safe="")
        listing = self._pages(f"/issues/{ref}/comments", purpose="forge.issue_comments")
        rows = [
            ForgeIssueComment(
                provider_id=self.provider_id,
                comment_id=str(row.get("id") or ""),
                author=str(dict(row.get("user") or {}).get("login") or ""),
                body=str(row.get("body") or ""),
                created_at=str(row.get("created_at") or ""),
            )
            for row in listing.rows
        ]
        return ForgeListing(rows=rows, pages_read=listing.pages_read, truncated=listing.truncated)

    def rerun_ci(self, job_id: str) -> bool:
        ref = quote(str(job_id).strip(), safe="")
        return self._post(f"/actions/runs/{ref}/rerun", purpose="forge.ci_rerun").ok

    def cancel_ci(self, job_id: str) -> bool:
        ref = quote(str(job_id).strip(), safe="")
        return self._post(f"/actions/runs/{ref}/cancel", purpose="forge.ci_cancel").ok

    # -- the explicit-authorized writes -------------------------------------------------

    def create_pull_request(
        self, *, title: str, body: str, head_ref: str, base_ref: str, draft: bool
    ) -> ForgePullRequest:
        response = self._post(
            "/pulls",
            purpose="forge.pr_create",
            body={
                "title": str(title),
                "head": str(head_ref),
                "base": str(base_ref),
                "body": str(body),
                "draft": bool(draft),
            },
        )
        self._refuse(response)
        return self._accepted_call(response, self._pull_request)

    def update_pull_request(self, number: str, *, title: str = "", body: str = "") -> ForgePullRequest:
        changes: dict[str, Any] = {}
        if str(title).strip():
            changes["title"] = str(title)
        if str(body).strip():
            changes["body"] = str(body)
        if not changes:
            raise ForgeRefusedError(200, reason="empty_update")
        ref = quote(str(number).strip(), safe="")
        response = self._patch(f"/pulls/{ref}", purpose="forge.pr_update", body=changes)
        self._refuse(response)
        return self._accepted_call(response, lambda payload: self._pull_request(payload, fallback_number=str(number)))

    def post_comment(self, number: str, body: str, *, subject: str) -> ForgeIssueComment:
        clean_subject = str(subject or "").strip().lower()
        if clean_subject not in {"issue", "pull_request"}:
            raise ForgeRefusedError(200, reason="unknown_comment_subject")
        # GitHub unifies PR and issue comments under the issues endpoint, so both subjects are
        # the SAME wire call here; the subject stays in the contract because GitLab's wires
        # differ, and the runtime above must never learn which forge it is talking to.
        ref = quote(str(number).strip(), safe="")
        response = self._post(f"/issues/{ref}/comments", purpose="forge.comment_create", body={"body": str(body)})
        self._refuse(response)
        return self._accepted_call(response, self._comment_from)

    def _comment_from(self, payload: dict[str, Any]) -> ForgeIssueComment:
        return ForgeIssueComment(
            provider_id=self.provider_id,
            comment_id=str(payload.get("id") or ""),
            author=str(dict(payload.get("user") or {}).get("login") or ""),
            body=str(payload.get("body") or ""),
            created_at=str(payload.get("created_at") or ""),
            url=str(payload.get("html_url") or ""),
        )

    def find_pull_request(self, *, head_ref: str, base_ref: str) -> ForgePullRequest | None:
        owner = self._repo.partition("/")[0]
        head_filter = f"{owner}:{str(head_ref).strip()}"
        suffix = f"/pulls?head={quote(head_filter, safe='')}&base={quote(str(base_ref).strip(), safe='')}"
        listing = self._pages(suffix, purpose="forge.pr_lookup")
        for row in listing.rows:
            candidate = self._pull_request(row)
            if candidate.head_ref == str(head_ref).strip() and candidate.base_ref == str(base_ref).strip():
                if candidate.state not in {"open"}:
                    continue
                return candidate
        return None


__all__ = ["GitHubForgeAdapter"]
