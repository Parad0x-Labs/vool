"""Credential-bearing requests refuse every redirect before the second server sees anything."""
import contextlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.registry.codec import RegistryError
from registry_service import auth, github_authority
from registry_service.authority import HEADER
from registry_service.mac_authority import Authority
from registry_service.model_review import ModelReview, NoAnswer


@contextlib.contextmanager
def servers(status):
    source_calls, target_calls = [], []

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            target_calls.append({key.lower(): value for key, value in self.headers.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        def do_POST(self):
            self.do_GET()

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Source(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            source_calls.append({key.lower(): value for key, value in self.headers.items()})
            self.send_response(status)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
            self.end_headers()

        def do_POST(self):
            self.do_GET()

    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (source, target)]
    for thread in threads:
        thread.start()
    try:
        yield f"http://127.0.0.1:{source.server_port}", source_calls, target_calls
    finally:
        for server in (source, target):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("transport", ["authority", "review", "github", "provider"])
def test_credentials_never_follow_a_redirect(tmp_path, monkeypatch, status, transport):
    with servers(status) as (url, source, target):
        if transport == "authority":
            with pytest.raises(RegistryError, match="authority redirect refused"):
                Authority._http(None, "POST", url, b'{}', {HEADER: "fixture-signature"})
            assert source[0][HEADER.lower()] == "fixture-signature"
        elif transport == "review":
            from registry_service import model_review

            monkeypatch.setattr(model_review, "OPENROUTER", url)
            review = ModelReview("openrouter", "test/model", ledger=tmp_path / "spend.json", key="fixture-token")
            with pytest.raises(NoAnswer, match="review redirect refused"):
                review.review({"metadata": {"id": "alice/notes", "summary": "Read supplied notes."}},
                              {"notes.txt": "Summarize the supplied notes."})
            assert review.spent() == 0
            assert source[0]["authorization"] == "Bearer fixture-token"
        elif transport == "github":
            monkeypatch.setattr(github_authority, "GITHUB_API", url)
            with pytest.raises(RegistryError, match="GitHub redirect refused"):
                github_authority._github("fixture-token")("/releases")
            assert source[0]["authorization"] == "Bearer fixture-token"
        else:
            monkeypatch.setattr(auth, "PROVIDERS", {"fixture": {"exchange": url}})
            with pytest.raises(RegistryError, match="provider redirect refused"):
                auth.provider_request(url, {"client_secret": "fixture-secret"}, token="fixture-token")
            assert source[0]["authorization"] == "Bearer fixture-token"
        assert len(source) == 1 and target == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_git_transport_refuses_redirects_with_its_credential_header(tmp_path, status):
    with servers(status) as (url, source, target):
        repository = github_authority.Repository(tmp_path, token="fixture-token")
        # Scope the synthetic credential to this fixture endpoint; production scopes it to its fixed GitHub host.
        repository._env.update(GIT_CONFIG_KEY_0="http.extraheader", GIT_CONFIG_COUNT="3",
                               GIT_CONFIG_KEY_1="credential.helper", GIT_CONFIG_VALUE_1="",
                               GIT_CONFIG_KEY_2="http.proxy", GIT_CONFIG_VALUE_2="",
                               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
        with pytest.raises(RegistryError, match="git redirect refused"):
            repository._git("ls-remote", url + "/repository.git")
        assert source and source[0]["authorization"].startswith("basic ")
        assert target == []
