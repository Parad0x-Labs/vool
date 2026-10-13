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

from core.registry.categories import public_taxonomy
from core.registry.codec import MAX_DOCUMENT, RegistryError, decode, require, version_key
from registry_service.auth import Auth
from registry_service.store import Store

PREFIX = "/registry/api"
UPLOAD_BODY_TIMEOUT = 30


def create_app(store: Store, auth: Auth, catalog, target: dict, *, clock=time.time, discovery=None) -> Starlette:
    def now():
        return int(clock())

    def account(request: Request, *, mutation=False):
        if mutation:
            require(request.headers.get("origin") == auth.origin, "same-origin request required")
            require(bool(request.headers.get("x-vool-csrf")), "CSRF token required")
        return store.authenticate(request.cookies.get("vool_registry", ""), now=now(),
            csrf=request.headers.get("x-vool-csrf") if mutation else None)

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
        rows = []
        if catalog.current is None:
            require(False, "approved catalog is not configured")
        require(now() < catalog.current["expires"], "catalog freshness expired")
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
            rows.append({**info, **stats, "sha256": approved["artifact_sha256"], "reviewed": True,
                         "creator": creator, "publication": approved["publication"],
                         "authority_notice": boundaries["authority_notice"],
                         "native_control_overlaps": boundaries["native_control_overlaps"],
                         "discovery_only": False, "import_ready": True,
                         "policy_sha256": approved["policy_sha256"], "updated_at": approved["issued_at"]})
        # Popularity cannot confer approval; only resolved admissions enter this list.
        latest = {}
        for row in rows:
            prior = latest.get(row["id"])
            if prior is None or version_key(row["version"]) > version_key(prior["version"]):
                latest[row["id"]] = row
        rows = list(latest.values())
        rows.sort(key=lambda row: (-row["stars"], row["id"]))
        category_counts = {}
        for rank, row in enumerate(rows, 1):
            row["global_rank"] = rank
            category_counts[row["category"]] = category_counts.get(row["category"], 0) + 1
            row["category_rank"] = category_counts[row["category"]]
        return JSONResponse({"channel": "approved", "entries": rows, "target": target,
                             "ranking": "Authenticated stars descending; stable id breaks ties. Download requests do not rank packages."})

    async def categories(request):
        return JSONResponse({"categories": public_taxonomy()})

    async def signed_catalog(request):
        from core.registry.codec import decode

        row = catalog.db.execute("SELECT snapshot FROM trust WHERE id=1").fetchone()
        require(row and row[0], "no approved snapshot")
        require(catalog.current and now() < catalog.current["expires"], "catalog freshness expired")
        return JSONResponse(decode(row[0]))

    async def me(request):
        who = account(request)
        profile = await run_in_threadpool(store.profile, who)
        # CSRF is intentionally visible to the authenticated same-origin frontend.
        with store.connection() as db:
            row = db.execute("SELECT csrf FROM sessions WHERE hash=?", (
                hashlib.sha256(request.cookies["vool_registry"].encode()).hexdigest(),)).fetchone()
        return JSONResponse({"profile": profile, "csrf": row[0]})

    async def profile(request):
        who = account(request, mutation=True)
        value = await json_body(request)
        return JSONResponse(await run_in_threadpool(store.profile, who, value))

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
        token = await run_in_threadpool(store.ticket, approved["artifact_sha256"], now=now())
        return JSONResponse({"sha256": approved["artifact_sha256"], "length": approved["artifact_length"],
            "url": PREFIX + "/artifacts/" + approved["artifact_sha256"] + "?ticket=" + token,
            "metric": "download_requests"})

    async def download(request):
        sha256 = request.path_params["sha256"]
        _, admission = await run_in_threadpool(store.public_artifact, sha256)
        entry(admission["metadata"]["id"], admission["metadata"]["version"])
        raw = await run_in_threadpool(store.download, sha256, request.query_params.get("ticket", ""), now=now())
        return Response(raw, media_type="application/vnd.vool.registry+json", headers={"X-Content-SHA256": sha256,
                        "Content-Disposition": 'attachment; filename="package.vool.json"'})

    async def auth_begin(request):
        link = ""
        if request.method == "POST":
            link = account(request, mutation=True)
        url, browser = await run_in_threadpool(auth.begin, request.path_params["provider"], now=now(), link_account=link,
                                             browser=request.cookies.get("vool_registry_oauth", ""))
        response = RedirectResponse(url, status_code=303)
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
        Route(PREFIX + "/categories", categories),
        Route(PREFIX + "/me", me), Route(PREFIX + "/profile", profile, methods=["PUT"]),
        Route(PREFIX + "/submissions", submissions, methods=["GET", "POST"]),
        Route(PREFIX + "/drafts", draft, methods=["POST"]),
        Route(PREFIX + "/submissions/{identifier}", submission),
        Route(PREFIX + "/submissions/{identifier}/consent", consent, methods=["POST"]),
        Route(PREFIX + "/stars", star, methods=["PUT"]), Route(PREFIX + "/download", ticket),
        Route(PREFIX + "/artifacts/{sha256}", download),
        Route(PREFIX + "/auth/{provider}/start", auth_begin, methods=["GET", "POST"]),
        Route(PREFIX + "/auth/{provider}/callback", auth_callback, methods=["GET", "POST"]),
        Route(PREFIX + "/session", logout, methods=["POST", "DELETE"]),
    ], exception_handlers={RegistryError: failure, ValueError: failure, TypeError: failure, KeyError: failure})

    async def response_policy(request, call_next):
        if request.headers.get("host") != urllib.parse.urlsplit(auth.origin).netloc:
            return JSONResponse({"error": "unexpected host"}, status_code=400)
        response = await call_next(request)
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=response_policy)
    return app
