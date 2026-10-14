"""JSON API for the website developer. No HTML rendering, arbitrary fetches or package execution."""
from __future__ import annotations

import asyncio
import hashlib
import time
import urllib.parse

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from core.registry.artifact import native_identity
from core.registry.categories import TAXONOMY, public_taxonomy
from core.registry.codec import MAX_DOCUMENT, RegistryError, decode, require, version_key
from core.registry.media import KINDS
from core.registry.requirements import public_requirements
from registry_service import community, reports
from registry_service.auth import Auth
from registry_service.curation import labels, load_curation, picture_approvals
from registry_service.store import Store

PREFIX = "/registry/api"
UPLOAD_BODY_TIMEOUT = 30
SORTS = {"stars", "updated", "downloads", "newest"}


def _stem(word: str) -> str:
    for ending in ("ing", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def _search_text(row: dict) -> str:
    category = TAXONOMY.get(row["category"], ("", {}))
    return " ".join([row["id"].replace("/", " ").replace("-", " "), row["summary"], category[0],
                     category[1].get(row["subcategory"], ""), row["category"], row["subcategory"]]).casefold()


def create_app(store: Store, auth: Auth, catalog, target: dict, *, clock=time.time, discovery=None,
               curation: dict | None = None, github=None) -> Starlette:
    curation = load_curation() if curation is None else curation
    approvals = picture_approvals(curation)

    def now():
        return int(clock())

    def account(request: Request, *, mutation=False):
        if mutation:
            require(request.headers.get("origin") == auth.origin, "same-origin request required")
            require(bool(request.headers.get("x-vool-csrf")), "CSRF token required")
        return store.authenticate(request.cookies.get("vool_registry", ""), now=now(),
            csrf=request.headers.get("x-vool-csrf") if mutation else None)

    def optional_account(request: Request):
        """Signed-in account for read routes, or None. Never an error for signed-out viewers."""
        if not request.cookies.get("vool_registry"):
            return None
        try:
            return store.authenticate(request.cookies["vool_registry"], now=now())
        except RegistryError:
            return None

    async def with_starred(request, rows):
        viewer = optional_account(request)
        if viewer:
            starred = await run_in_threadpool(store.starred, viewer)
            for row in rows:
                row["starred"] = row["id"] in starred
        return rows

    def entry(package_id, version):
        return catalog.resolve(package_id, version, target=target, now=now())

    async def body(request, *, limit=MAX_DOCUMENT):
        total, chunks = 0, []
        async for chunk in request.stream():
            total += len(chunk)
            require(total <= limit, "request exceeds byte limit")
            chunks.append(chunk)
        return b"".join(chunks)

    async def json_body(request):
        require(request.headers.get("content-type", "").split(";")[0] == "application/json", "JSON content type required")
        value = decode(await body(request, limit=32768), 32768)
        require(isinstance(value, dict), "JSON object required")
        return value

    async def upload_body(read):
        try:
            return await asyncio.wait_for(read, timeout=UPLOAD_BODY_TIMEOUT)
        except TimeoutError:
            raise RegistryError("upload body timed out") from None

    async def public_catalog(request):
        if request.query_params.get("channel") == "discovery":
            from core.addon_catalog import CATALOG

            return JSONResponse({"channel": "discovery", "entries": CATALOG if discovery is None else discovery,
                                 "notice": "Import compatibility is not public registry admission."})
        rows = await with_starred(request, await approved_rows())
        query = request.query_params
        require(len(query.multi_items()) == len(query) and set(query) <= {"q", "category", "subcategory", "origin", "sort"},
                "unknown catalog query")
        text = query.get("q", "")
        require(len(text) <= 100, "search text exceeds limit")
        words = [_stem(word) for word in text.casefold().split()][:8]
        if words:
            rows = [row for row in rows if all(word in _search_text(row) for word in words)]
        for field in ("category", "subcategory", "origin"):
            if field in query:
                rows = [row for row in rows if row[field] == query[field]]
        sort = query.get("sort", "stars")
        require(sort in SORTS, "unknown sort order")
        key = {"stars": lambda row: (-row["stars"], row["id"]),
               "updated": lambda row: (-row["updated_at"], row["id"]),
               "downloads": lambda row: (-row["download_requests"], row["id"]),
               "newest": lambda row: (-row["published_at"], row["id"])}[sort]
        rows.sort(key=key)
        return JSONResponse({"channel": "approved", "entries": rows, "target": target, "sort": sort,
                             "ranking": "Authenticated stars descending; stable id breaks ties. Download requests do not rank packages."})

    async def approved_rows():
        rows = []
        if catalog.current is None:
            require(False, "approved catalog is not configured")
        require(now() < catalog.current["expires"], "catalog freshness expired")
        # Dependencies are native plugin ids; name the ones that are registry packages by package id.
        packages = {native_identity(envelope["signed"]["metadata"]["id"]): envelope["signed"]["metadata"]["id"]
                    for envelope in catalog.current["entries"]}
        for envelope in catalog.current["entries"]:
            value = envelope["signed"]
            info = value["metadata"]
            try:
                approved = entry(info["id"], info["version"])
                boundaries = await run_in_threadpool(store.public_inspection, approved["artifact_sha256"])
            except RegistryError:
                continue
            stats = await run_in_threadpool(store.stats, info["id"])
            creator = await run_in_threadpool(store.public_profile, info["id"].split("/")[0])
            requirements = boundaries["requirements"]
            rows.append({**{key: val for key, val in info.items() if key not in KINDS},
                         **shown_pictures(info["id"], boundaries["pictures"]), **stats, "sha256": approved["artifact_sha256"], "reviewed": True,
                         "creator": creator, "publication": approved["publication"],
                         "requirements": {**requirements, "dependencies": sorted(
                             packages.get(item, item) for item in requirements["dependencies"])},
                         "targets": approved["targets"],
                         "authority_notice": boundaries["authority_notice"],
                         "native_control_overlaps": boundaries["native_control_overlaps"],
                         "discovery_only": False, "import_ready": True,
                         "policy_sha256": approved["policy_sha256"], "updated_at": approved["issued_at"]})
        # Popularity cannot confer approval; only resolved admissions enter this list.
        latest, first = {}, {}
        for row in rows:
            prior = latest.get(row["id"])
            first[row["id"]] = min(first.get(row["id"], row["updated_at"]), row["updated_at"])
            if prior is None or version_key(row["version"]) > version_key(prior["version"]):
                latest[row["id"]] = row
        rows = list(latest.values())
        held = await run_in_threadpool(reports.paused, store)
        for row in rows:
            if row["id"] in held:
                row["downloads_paused"] = True
        rows.sort(key=lambda row: (-row["stars"], row["id"]))
        category_counts = {}
        for rank, row in enumerate(rows, 1):
            row["global_rank"] = rank
            category_counts[row["category"]] = category_counts.get(row["category"], 0) + 1
            row["category_rank"] = category_counts[row["category"]]
            row["published_at"] = first[row["id"]]
            row.update(labels(curation, row["id"], row["version"]))
        return rows

    def shown_pictures(package_id: str, pictures: dict) -> dict:
        """Pictures approved for this skill as a page shows them; the rest are named as waiting for a person's look."""
        shown = {kind: {"sha256": item["sha256"], "type": item["type"], "width": item["width"], "height": item["height"],
                        "url": PREFIX + "/pictures/" + package_id + "/" + item["sha256"]}
                 for kind, item in pictures.items() if (package_id, item["sha256"]) in approvals}
        pending = [kind for kind in KINDS if kind in pictures and kind not in shown]
        return shown | ({"pictures_pending": pending} if pending else {})

    async def picture(request):
        package_id = request.path_params["publisher"] + "/" + request.path_params["name"]
        sha256 = request.path_params["sha256"]
        require((package_id, sha256) in approvals, "picture not found")
        # Only a picture this skill's currently listed versions carry, never any other stored file.
        require(catalog.current is not None and now() < catalog.current["expires"], "picture not found")
        listed = False
        for envelope in catalog.current["entries"]:
            info = envelope["signed"]["metadata"]
            if info["id"] != package_id or not any(info.get(kind, {}).get("sha256") == sha256 for kind in KINDS):
                continue
            try:
                approved = entry(info["id"], info["version"])
                # Also stores pictures of versions published before pictures were recorded.
                await run_in_threadpool(store.public_inspection, approved["artifact_sha256"])
            except RegistryError:
                continue
            listed = True
            break
        require(listed, "picture not found")
        media, data = await run_in_threadpool(store.picture, sha256)
        # Short public caching, so taking an approval away reaches browsers within minutes.
        return Response(data, media_type=media, headers={"Cache-Control": "public, max-age=300",
                        "Cross-Origin-Resource-Policy": "same-origin", "Content-Disposition": "inline"})

    async def profile_page(request):
        namespace = request.path_params["namespace"]
        require(isinstance(namespace, str) and len(namespace) <= 64, "invalid namespace")
        rows = await with_starred(request, [row for row in await approved_rows() if row["id"].split("/")[0] == namespace])
        profile = await run_in_threadpool(store.public_profile, namespace)
        return JSONResponse({"profile": profile, "origin": "official" if namespace in curation["official_namespaces"]
                             else "community", "packages": rows})

    async def comment_list(request):
        if request.method == "GET":
            return JSONResponse(await run_in_threadpool(community.comments, store, request.query_params.get("id"),
                                                        optional_account(request)))
        value = await json_body(request)
        require(set(value) == {"id", "body"}, "invalid comment fields")
        who = account(request, mutation=True)
        result = await run_in_threadpool(community.post_comment, store, who, value["id"], value["body"], now=now())
        return JSONResponse(result, status_code=201 if result["state"] != "refused" else 400)

    async def comment_report(request):
        who = account(request, mutation=True)
        return JSONResponse(await run_in_threadpool(community.report_comment, store, who,
                                                    request.path_params["identifier"], now=now()))

    async def skill_reports(request):
        if request.method == "GET":
            return JSONResponse(await run_in_threadpool(reports.status, store, request.query_params.get("id"),
                                                        optional_account(request), now=now()))
        who = account(request, mutation=True)
        value = await json_body(request)
        return JSONResponse(await run_in_threadpool(reports.file_report, store, who, value, now=now()), status_code=201)

    async def downloads_allowed(package_id):
        require(package_id not in await run_in_threadpool(reports.paused, store), reports.PAUSED)

    async def github_import(request):
        require(github is not None, "GitHub import is not configured")
        who = account(request, mutation=True)
        with store.upload_slot(who, now=now()) as slot:
            value = await upload_body(json_body(request))
            return JSONResponse(await run_in_threadpool(store.github_import, who, value, github, now=now(), _slot=slot),
                                status_code=201)

    async def publisher_link(request):
        require(github is not None, "GitHub linking is not configured")
        who = account(request, mutation=True)
        value = await json_body(request)
        require(set(value) == {"namespace"}, "invalid publisher link fields")
        return JSONResponse(await run_in_threadpool(store.link_github_publisher, who, value["namespace"], github))

    async def categories(request):
        return JSONResponse({"categories": public_taxonomy()})

    async def requirements(request):
        return JSONResponse({"requirements": public_requirements()})

    async def signed_catalog(request):
        from core.registry.codec import decode

        row = catalog.db.execute("SELECT snapshot FROM trust WHERE id=1").fetchone()
        require(row and row[0], "no approved snapshot")
        require(catalog.current and now() < catalog.current["expires"], "catalog freshness expired")
        return JSONResponse(decode(row[0]))

    async def own_profile(who, value=None):
        profile = await run_in_threadpool(store.profile, who, value)
        return profile | {"providers": await run_in_threadpool(store.providers, who)}

    async def me(request):
        who = optional_account(request)
        if who is None:
            response = JSONResponse({"profile": None, "csrf": None})
            if request.cookies.get("vool_registry"):
                response.delete_cookie("vool_registry", path=PREFIX)
            return response
        profile = await own_profile(who)
        # CSRF is intentionally visible to the authenticated same-origin frontend.
        with store.connection() as db:
            row = db.execute("SELECT csrf FROM sessions WHERE hash=?", (
                hashlib.sha256(request.cookies["vool_registry"].encode()).hexdigest(),)).fetchone()
        return JSONResponse({"profile": profile, "csrf": row[0]})

    async def profile(request):
        who = account(request, mutation=True)
        value = await json_body(request)
        return JSONResponse(await own_profile(who, value))

    async def submissions(request):
        who = account(request, mutation=request.method == "POST")
        if request.method == "GET":
            return JSONResponse({"submissions": await run_in_threadpool(store.submissions, who)})
        require(request.headers.get("content-type", "").split(";")[0] == "application/vnd.vool.registry+json",
                "registry artifact content type required")
        with store.upload_slot(who, now=now()) as slot:
            raw = await upload_body(body(request))
            return JSONResponse(await run_in_threadpool(store.upload, who, raw, now=now(), _slot=slot), status_code=201)

    async def submission(request):
        return JSONResponse(await run_in_threadpool(store.submission, account(request), request.path_params["identifier"]))

    async def draft(request):
        who = account(request, mutation=True)
        with store.upload_slot(who, now=now()) as slot:
            value = await upload_body(json_body(request))
            return JSONResponse(await run_in_threadpool(store.draft, who, value, now=now(), _slot=slot), status_code=201)

    async def consent(request):
        value = await json_body(request)
        return JSONResponse(await run_in_threadpool(store.consent, account(request, mutation=True),
            request.path_params["identifier"], value.get("sha256"), public=value.get("public")))

    async def star(request):
        value = await json_body(request)
        package_id, version = value.get("id"), value.get("version")
        entry(package_id, version)
        return JSONResponse(await run_in_threadpool(store.star, account(request, mutation=True), package_id,
                                                   value.get("starred"), now=now()))

    async def ticket(request):
        approved = entry(request.query_params.get("id"), request.query_params.get("version"))
        await downloads_allowed(approved["metadata"]["id"])
        token = await run_in_threadpool(store.ticket, approved["artifact_sha256"], now=now())
        return JSONResponse({"sha256": approved["artifact_sha256"], "length": approved["artifact_length"],
            "url": PREFIX + "/artifacts/" + approved["artifact_sha256"] + "?ticket=" + token,
            "metric": "download_requests"})

    async def download(request):
        sha256 = request.path_params["sha256"]
        _, admission = await run_in_threadpool(store.public_artifact, sha256)
        entry(admission["metadata"]["id"], admission["metadata"]["version"])
        await downloads_allowed(admission["metadata"]["id"])  # also tickets handed out before the pause
        raw = await run_in_threadpool(store.download, sha256, request.query_params.get("ticket", ""), now=now())
        # A signed-in website download makes the account eligible to comment.
        who = optional_account(request)
        if who:
            await run_in_threadpool(community.record_download, store, who, admission["metadata"]["id"], now=now())
        return Response(raw, media_type="application/vnd.vool.registry+json", headers={"X-Content-SHA256": sha256,
                        "Content-Disposition": 'attachment; filename="package.vool.json"'})

    async def auth_begin(request):
        link = ""
        if request.method == "POST":
            link = account(request, mutation=True)
        url, browser = await run_in_threadpool(auth.begin, request.path_params["provider"], now=now(), link_account=link,
                                             browser=request.cookies.get("vool_registry_oauth", ""))
        # Linking is a same-origin fetch carrying the CSRF header, so it gets the
        # provider URL to open; a plain sign-in link is followed as a redirect.
        response = JSONResponse({"url": url}) if link else RedirectResponse(url, status_code=303)
        # Apple posts its callback cross-site; this short-lived flow cookie has no
        # session authority and is bound to random state in the server's table.
        response.set_cookie("vool_registry_oauth", browser, max_age=600, secure=True, httponly=True,
                            samesite="none", path=PREFIX + "/auth/")
        return response

    async def auth_callback(request):
        if request.method == "POST":
            require(request.path_params["provider"] == "apple", "unsupported callback method")
            fields = urllib.parse.parse_qs((await body(request, limit=16384)).decode("utf-8"), max_num_fields=10)
            require(all(len(items) == 1 for items in fields.values()), "duplicate callback fields")
            params = {key: items[0] for key, items in fields.items()}
        else:
            require(len(request.query_params.multi_items()) == len(request.query_params), "duplicate callback fields")
            params = request.query_params
        token, _ = await run_in_threadpool(auth.finish, request.path_params["provider"], params.get("state", ""),
            params.get("code", ""), request.cookies.get("vool_registry_oauth", ""), now=now())
        response = RedirectResponse("/codex/skills/account", status_code=303)
        response.set_cookie("vool_registry", token, secure=True, httponly=True, samesite="lax", max_age=86400, path=PREFIX)
        response.delete_cookie("vool_registry_oauth", path=PREFIX + "/auth/")
        return response

    async def logout(request):
        who = account(request, mutation=True)
        if request.method == "DELETE":
            await run_in_threadpool(store.delete_account, who)
        else:
            await run_in_threadpool(store.logout, request.cookies["vool_registry"])
        response = JSONResponse({"status": "deleted" if request.method == "DELETE" else "signed_out"})
        response.delete_cookie("vool_registry", path=PREFIX)
        return response

    async def failure(request, exc):
        return JSONResponse({"error": str(exc) if isinstance(exc, RegistryError) else "invalid request"}, status_code=400)

    app = Starlette(routes=[
        Route(PREFIX + "/catalog", public_catalog), Route(PREFIX + "/snapshot", signed_catalog),
        Route(PREFIX + "/categories", categories), Route(PREFIX + "/requirements", requirements),
        Route(PREFIX + "/profiles/{namespace}", profile_page),
        Route(PREFIX + "/comments", comment_list, methods=["GET", "POST"]),
        Route(PREFIX + "/comments/{identifier}/report", comment_report, methods=["POST"]),
        Route(PREFIX + "/reports", skill_reports, methods=["GET", "POST"]),
        Route(PREFIX + "/imports", github_import, methods=["POST"]),
        Route(PREFIX + "/publisher-link", publisher_link, methods=["POST"]),
        Route(PREFIX + "/me", me), Route(PREFIX + "/profile", profile, methods=["PUT"]),
        Route(PREFIX + "/submissions", submissions, methods=["GET", "POST"]),
        Route(PREFIX + "/drafts", draft, methods=["POST"]),
        Route(PREFIX + "/submissions/{identifier}", submission),
        Route(PREFIX + "/submissions/{identifier}/consent", consent, methods=["POST"]),
        Route(PREFIX + "/stars", star, methods=["PUT"]), Route(PREFIX + "/download", ticket),
        Route(PREFIX + "/artifacts/{sha256}", download),
        Route(PREFIX + "/pictures/{publisher}/{name}/{sha256}", picture),
        Route(PREFIX + "/auth/{provider}/start", auth_begin, methods=["GET", "POST"]),
        Route(PREFIX + "/auth/{provider}/callback", auth_callback, methods=["GET", "POST"]),
        Route(PREFIX + "/session", logout, methods=["POST", "DELETE"]),
    ], exception_handlers={RegistryError: failure, ValueError: failure, TypeError: failure, KeyError: failure})

    async def response_policy(request, call_next):
        if request.headers.get("host") != urllib.parse.urlsplit(auth.origin).netloc:
            return JSONResponse({"error": "unexpected host"}, status_code=400)
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-store")  # approved pictures may be cached briefly
        response.headers.update({"X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=response_policy)
    return app
