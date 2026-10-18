"""Release-layout proof using the real app routes, trust loader and HTTPS transports.

Only fixture keys/build provenance, the network resolver and paid verdict differ.
The resolver supplies an ephemeral loopback service port so this needs no root access.
"""
import contextlib
import datetime
import hashlib
import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


def test_shipped_release_journey_with_real_routes_and_transports(tmp_path):
    source = Path(__file__).resolve().parents[2]
    release = tmp_path / "release"
    tree = release / "source"
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=source).decode().split("\0")
    paths.append(str(Path(__file__).resolve().relative_to(source)))
    for name in set(paths) - {""}:
        path = source / name
        if not path.is_file():
            continue
        target = tree / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(path.read_bytes()).digest()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    (release / "BUILD_MANIFEST.json").write_text(json.dumps({
        "schema": "vool-build-manifest/1", "release": True, "source_tree_clean": True,
        "natively_verified": True, "exact_sha": "a" * 40, "version": "0.7.2", "platform": "macos", "arch": arch,
    }))
    env = os.environ.copy()
    env.update(PYTHONPATH=str(tree), VOOL_TEST_MODE="1", VOOL_UNATTENDED="1", VOOL_GATE="1")
    result = subprocess.run([sys.executable, str(tree / Path(__file__).relative_to(source)), str(tmp_path / "fixture")],
                            cwd=tree, env=env, capture_output=True, text=True, timeout=180)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr


def _certificate(folder, host):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = folder / "fixture-cert.pem", folder / "fixture-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    key_file.chmod(0o600)
    return cert_file, key_file


@contextlib.contextmanager
def _https(app, cert, key):
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off",
                                          ssl_certfile=str(cert), ssl_keyfile=str(key)))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield sock.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def _exercise(folder):
    folder.mkdir()
    runtime = folder / "app-home"
    os.environ.update(VOOL_HOME=str(runtime), VOOL_PLUGIN_LIFECYCLE_PATH=str(runtime / "plugin_lifecycle.json"),
                      VOOL_PLUGINS_DIR=str(runtime / "addons"), VOOL_BUNDLED_PLUGINS_DIR=str(folder / "no-bundled"),
                      VOOL_NATIVE_SKILLS_DIR=str(folder / "no-native"), VOOL_NO_BROWSER="1")
    from core.unattended_preflight import preflight

    preflight("tests.shipped_registry_journey")
    import httpx
    from playwright.sync_api import expect
    from starlette.responses import RedirectResponse, Response

    from core import plugin_catalog, plugin_lifecycle
    from core.registry import client
    from core.registry.codec import canonical, decode
    from core.registry.trust import TrustedCatalog
    from registry_service.api import PREFIX, create_app
    from registry_service.auth import Auth
    from registry_service.authority import AGREEMENT, OnlineCatalog, create_online_key
    from registry_service.github_authority import FOLDER, STEPS, RepoAuthority, Repository, setup
    from registry_service.model_review import ModelReview
    from registry_service.store import Store
    from tests.registry.test_skill_codex_authority import SKILL, Model, public
    from tests.registry.test_skill_codex_finish_journey import browse, guidance, native_http, open_codex, post
    from tests.registry.test_skill_codex_github_signer import git
    from tests.registry.test_skill_publish_handoff import SKILL as OWN_SKILL
    from tests.registry.test_skill_publish_handoff import install as own_skill
    from tests.served_browser import launch_chromium

    origin = client.ORIGIN
    host = urlsplit(origin).hostname
    cert, key = _certificate(folder, host)
    os.environ["SSL_CERT_FILE"] = str(cert)
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(name, None)
    anchor = folder / "anchor.json"
    os.environ["VOOL_REGISTRY_ROOT_FILE"] = str(anchor)
    target = {"version": "0.7.2", "commit": "a" * 40,
              "platform": "macos-" + ("arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64")}
    server_dir = folder / "website"
    server_dir.mkdir()
    online_key = create_online_key(server_dir / "catalog.key")
    catalog_file = server_dir / "catalog.json"
    catalog_file.write_bytes(canonical({"entries": []}))
    remote, owner = folder / "remote.git", folder / "owner"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(owner)], check=True)
    git(owner, "checkout", "-q", "-b", "main")
    secrets = {}
    setup(owner, github="fixture/signer", catalog_key=public(online_key), targets=[target],
          review={"provider": "openrouter", "model": "test/model", "monthly_cap": 1.0},
          passphrase="fixture passphrase", offline=folder / "offline", vool_commit="a" * 40,
          now=int(time.time()), set_secret=secrets.__setitem__)
    git(owner, "add", "-A")
    git(owner, "commit", "-qm", "Fixture signer")
    git(owner, "push", "-q", "origin", "main")
    root = decode((owner / FOLDER / "trusted-root.json").read_bytes())
    anchor.write_bytes(canonical(root))
    store = Store(server_dir / "accounts.sqlite")
    trust = TrustedCatalog(root, server_dir / "trust.sqlite", now=int(time.time()))
    online = OnlineCatalog(store, trust, online_key, catalog_file, target)
    app = create_app(store, Auth(store, origin, []), trust, target, authority=online)
    faults = {"redirect": False, "oversized": False}

    async def website(scope, receive, send):
        if faults["redirect"] and scope["path"].endswith("/snapshot"):
            return await RedirectResponse("https://unwanted.example/receive", status_code=302)(scope, receive, send)
        if faults["oversized"] and scope["path"].endswith("/snapshot"):
            return await Response(b"x" * (4 * 1024 * 1024 + 1))(scope, receive, send)
        return await app(scope, receive, send)

    connections, resolutions, outside = [], [], []
    def audit(event, args):
        if event != "socket.connect" or args[0].family not in (socket.AF_INET, socket.AF_INET6):
            return
        address = args[1]
        connections.append(address)
        if address[0] not in {"127.0.0.1", "::1", "localhost"}:
            outside.append(address)
            raise AssertionError("unexpected outside connection")
    sys.addaudithook(audit)
    original_resolver = socket.getaddrinfo
    runs = 0
    with _https(website, cert, key) as port:
        def resolve(name, service, *args, **kwargs):
            if name == host:
                assert service in (443, "443", "https")
                resolutions.append(name)
                return original_resolver("127.0.0.1", port, *args, **kwargs)
            assert name in {"127.0.0.1", "::1", "localhost"}, "unexpected outside name resolution"
            return original_resolver(name, service, *args, **kwargs)
        socket.getaddrinfo = resolve
        try:
            context = ssl.create_default_context(cafile=str(cert))
            with httpx.Client(base_url=origin, verify=context, trust_env=False) as http:
                now = int(time.time())
                who = store.identity("github", "fixture-author", now=now - 60 * 86400, since=now - 60 * 86400)
                store.profile(who, {"namespace": "alice", "bio": "", "handles": {}})
                token, csrf = store.session(who, now=now)
                http.cookies.set("vool_registry", token)

                def upload(version):
                    body = {"metadata": {"id": "alice/notes", "kind": "skill", "version": version,
                            "summary": "Turn meeting notes into decisions", "category": "productivity",
                            "subcategory": "meetings", "license": "MIT", "languages": ["en"]},
                            "skill_markdown": SKILL, "agreement": AGREEMENT}
                    headers = {"Origin": origin, "X-Vool-CSRF": csrf}
                    result = http.post(PREFIX + "/drafts", json=body, headers=headers)
                    assert result.status_code == 201, result.text
                    item = result.json()
                    assert item["state"] == "checking"
                    consent = http.post(PREFIX + "/submissions/" + item["id"] + "/consent",
                                        json={"sha256": item["sha256"], "public": True}, headers=headers)
                    assert consent.status_code == 200, consent.text
                    return item

                def sign():
                    nonlocal runs
                    runs += 1
                    checkout = folder / ("run-" + str(runs))
                    subprocess.run(["git", "clone", "-q", str(remote), str(checkout)], check=True)
                    repository = Repository(checkout)
                    repository.start()
                    review = ModelReview("openrouter", "test/model", ledger=folder / ("paid-" + str(runs)),
                                         monthly_cap=1.0, key="fixture-only", request=Model())
                    authority = RepoAuthority(checkout, env={**secrets, "SKILL_CODEX_SERVER": origin},
                                              review=review, repository=repository)
                    review.ledger = authority.dir / "spend.json"
                    return authority.run_once()

                first = upload("1.0.0")
                assert sign()["admitted"] == ["alice/notes 1.0.0"]
                manager, browser = launch_chromium()
                try:
                    page = browser.new_page(viewport={"width": 1280, "height": 960})
                    browser_outside = []
                    def route(request):
                        if urlsplit(request.request.url).hostname != "127.0.0.1":
                            browser_outside.append(request.request.url)
                            request.abort()
                        else:
                            request.continue_()
                    page.route("**/*", route)
                    before = len(resolutions)
                    with native_http() as address:
                        open_codex(page, address)
                        assert len(resolutions) == before and outside == [] and browser_outside == []
                        listed = browse(page)
                        if platform.system() != "Darwin":
                            assert listed.status == 400
                            assert "actual released platform build" in listed.json()["message"]
                            assert len(resolutions) == before
                            print("NON_MAC_RELEASE_REFUSAL_PROVEN")
                            return
                        assert listed.ok, listed.text()
                        assert client.runtime_target() == target
                        page.locator("#pluginsSearch").fill("a-missing-skill")
                        expect(page.locator("#pluginsBody .addon-card")).to_have_count(0)
                        page.locator("#pluginsSearch").fill("meetings")
                        expect(page.locator("#pluginsBody .addon-card")).to_have_count(1)
                        page.locator("#pluginsSearch").fill("")
                        page.get_by_role("button", name="Download for inspection", exact=True).click()
                        page.get_by_role("button", name="Install and enable", exact=True).wait_for()
                        page.get_by_text("Exact files, permissions and source", exact=True).click()
                        shown = page.locator("#pluginsBody details[open] pre").inner_text()
                        assert first["sha256"] in shown and '"permissions": []' in shown and "SKILL.md" in shown
                        assert not plugin_lifecycle.records()
                        page.get_by_role("button", name="Install and enable", exact=True).click()
                        page.wait_for_function("document.querySelector('#addonNav button[aria-pressed=true]')?.textContent === 'My add-ons'")
                        record, = plugin_lifecycle.records()
                        assert plugin_lifecycle.is_available(record.plugin_id)
                        guidance(record.plugin_id)
                    restarted = subprocess.run([
                        sys.executable, "-c",
                        "from core import plugin_lifecycle; from core.tool_offer_assembly import skill_guidance_for; "
                        "import sys; assert plugin_lifecycle.is_available(sys.argv[1]); "
                        "assert 'List each decision' in skill_guidance_for('/notes meeting notes').text",
                        record.plugin_id], capture_output=True, text=True, timeout=30)
                    assert restarted.returncode == 0, restarted.stderr
                    plugin_catalog.invalidate_storage_listing()
                    with native_http() as address:
                        open_codex(page, address)
                        assert plugin_lifecycle.is_available(record.plugin_id)
                        guidance(record.plugin_id)
                        second = upload("1.1.0")
                        assert sign()["admitted"] == ["alice/notes 1.1.0"]
                        assert browse(page).ok
                        prepared = post(page, {"action": "registry_prepare", "id": "alice/notes", "version": "1.1.0", "approved": True})
                        assert prepared["status"] == 200
                        stage = runtime / "registry_staging" / (second["sha256"] + ".json")
                        raw = stage.read_bytes()
                        stage.write_bytes(raw + b" ")
                        install = {"action": "registry_install", "id": "alice/notes", "version": "1.1.0",
                                   "review_id": second["sha256"], "accepted": True, "enable": True}
                        assert post(page, install)["status"] == 400
                        assert plugin_lifecycle.record_for(record.plugin_id).verified_digest == record.verified_digest
                        stage.write_bytes(raw)
                        installed_file = Path(record.root) / "skills/notes/SKILL.md"
                        old_text = installed_file.read_bytes()
                        installed_file.chmod(0o644)
                        installed_file.write_bytes(old_text + b"\nMy own note.\n")
                        edited = post(page, install)
                        assert edited["status"] == 400 and "edited" in edited["body"]["message"]
                        assert installed_file.read_bytes().endswith(b"My own note.\n")
                        installed_file.write_bytes(old_text)
                        installed_file.chmod(0o444)
                        assert post(page, install)["status"] == 200
                        assert browse(page).ok
                        page.locator("#pluginsBody .addon-card").filter(has_text="1.1.0").get_by_role(
                            "button", name="Download for inspection", exact=True).click()
                        page.get_by_role("button", name="Install and enable", exact=True).click()
                        expect(page.locator("#pluginsBody .addon-error")).to_have_text(
                            "This skill is already up to date. Check back for an update.")
                        page.get_by_role("button", name="Cancel", exact=True).click()
                        for version, digest in (("1.1.0", second["sha256"]), ("1.0.0", first["sha256"])):
                            assert post(page, {"action": "registry_prepare", "id": "alice/notes", "version": version,
                                               "approved": True})["status"] == 200
                            refused = post(page, {**install, "version": version, "review_id": digest})
                            assert refused["status"] == 400 and "higher" in refused["body"]["message"]
                        git(owner, "pull", "-q", "--ff-only", "origin", "main")
                        (owner / STEPS).write_text("removed alice/notes\n")
                        git(owner, "commit", "-qam", "Remove fixture skill")
                        git(owner, "push", "-q", "origin", "main")
                        assert sign()["catalog"]
                        removed = browse(page)
                        assert removed.ok and removed.json()["entries"] == []
                        assert not plugin_lifecycle.is_available(record.plugin_id)
                        expect(page.get_by_role("button", name="Download for inspection", exact=True)).to_have_count(0)
                        faults["redirect"] = True
                        redirected = browse(page)
                        assert redirected.status == 400 and "redirect refused" in redirected.json()["message"]
                        faults["redirect"] = False
                        faults["oversized"] = True
                        oversized = browse(page)
                        assert oversized.status == 400 and "byte limit" in oversized.json()["message"]
                        faults["oversized"] = False
                        before = len(resolutions)
                        os.environ["VOOL_REGISTRY_ROOT_FILE"] = str(folder / "missing-anchor")
                        own_skill("meeting-actions", OWN_SKILL)
                        open_codex(page, address)
                        page.locator("#addonNav").get_by_role("button", name="My add-ons", exact=True).click()
                        page.locator("#pluginsBody .addon-controls").get_by_role("button", name="Skills", exact=True).click()
                        page.locator("#pluginsBody .plugin-card").filter(has=page.locator(".p-name", has_text="Meeting Actions")).wait_for()
                        assert page.locator("#pluginsBody .skill-publish-btn").count() == 0
                        page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
                        closed = browse(page)
                        assert closed.status == 400 and closed.json()["message"] == client.CLOSED
                        assert len(resolutions) == before
                        assert outside == [] and browser_outside == []
                        print(json.dumps({"journey": "passed", "socket_connections": len(connections),
                                          "outside_connections": len(outside), "registry_resolutions": len(resolutions)}))
                finally:
                    browser.close()
                    manager.stop()
        finally:
            socket.getaddrinfo = original_resolver
            trust.close()


if __name__ == "__main__":
    _exercise(Path(sys.argv[1]))
