"""Read-only GitHub fetches for skill import: repository owner, exact commit, source archive.

Only fixed GitHub hosts are contacted, redirects are refused and sizes are bounded.
Nothing is executed; the archive is unpacked as inert data by the store.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

from core.registry.codec import RegistryError, decode, require
from registry_service.http_transport import open_request

REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}")
REF = re.compile(r"[A-Za-z0-9._/-]{1,100}")
MAX_ARCHIVE = 16 * 1024 * 1024


def check_repository(repository: object) -> str:
    require(isinstance(repository, str) and bool(REPOSITORY.fullmatch(repository)) and
            not repository.split("/")[1].startswith(".") and ".." not in repository, "invalid GitHub repository")
    return repository


def check_ref(ref: object) -> str:
    require(isinstance(ref, str) and bool(REF.fullmatch(ref)) and ".." not in ref and
            not ref.startswith(("/", "-")) and not ref.endswith("/"), "invalid branch, tag or commit")
    return ref


class GitHubSource:
    def __init__(self, *, timeout: int = 20):
        self.timeout = timeout

    def _get(self, url: str, accept: str, limit: int) -> bytes:
        request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "VOOL-registry"})
        try:
            with open_request(request, service="GitHub", timeout=self.timeout) as response:
                data = response.read(limit + 1)
        except (urllib.error.URLError, OSError):
            raise RegistryError("GitHub is unavailable or the repository was not found") from None
        require(len(data) <= limit, "GitHub response exceeds size limit")
        return data

    def repository(self, repository: str) -> dict:
        check_repository(repository)
        value = decode(self._get("https://api.github.com/repos/" + repository, "application/vnd.github+json", 262144), 262144)
        require(isinstance(value, dict) and isinstance(value.get("owner"), dict) and
                type(value["owner"].get("id")) is int and type(value.get("private")) is bool, "invalid GitHub repository response")
        return {"owner_id": value["owner"]["id"], "private": value["private"]}

    def commit(self, repository: str, ref: str) -> str:
        check_repository(repository)
        url = "https://api.github.com/repos/" + repository + "/commits/" + urllib.parse.quote(check_ref(ref), safe="")
        sha = self._get(url, "application/vnd.github.sha", 64).decode("ascii", "replace").strip()
        require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), "GitHub did not return an exact commit")
        return sha

    def archive(self, repository: str, commit: str) -> bytes:
        check_repository(repository)
        require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "exact commit required")
        return self._get("https://codeload.github.com/" + repository + "/tar.gz/" + commit, "application/x-gzip", MAX_ARCHIVE)
