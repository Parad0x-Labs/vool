"""Reporting a skill: a signed-in person's reason and note, three safety reports pause downloads, operators decide."""
import contextlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from starlette.testclient import TestClient

from core.registry.codec import DOWNLOADS_PAUSED, RegistryError
from registry_service import cli, community, reports
from registry_service.api import PREFIX
from tests.registry.helpers import NOW, TARGET
from tests.registry.test_skill_codex import DAY, ORIGIN, account, mutation, service

SKILLS = [("alice/notes", {}, NOW - 100), ("bob/plain", {}, NOW - 100)]
# A valid 12-word recovery phrase.
PHRASE = ["legal", "winner", "thank", "year", "wave", "sausage", "worth", "useful", "legal", "winner", "thank", "yellow"]
SECRET_REFUSAL = "take out any keys, passwords or recovery phrases"


def creator(store, subject, namespace):
    """An account that owns a publisher name, as linking the GitHub publisher leaves it."""
    who, token, csrf = account(store, subject, None)
    with store.connection() as db:
        db.execute("DELETE FROM namespace_owners WHERE namespace=?", (namespace,))
        db.execute("INSERT INTO namespace_owners(namespace,account,verified) VALUES (?,?,1)", (namespace, who))
    return who, token, csrf


def report(client, csrf, **body):
    return client.post(PREFIX + "/reports", json={"id": "alice/notes", "reason": "scam", **body}, headers=mutation(csrf))


def ticket(client, package_id="alice/notes"):
    return client.get(PREFIX + "/download", params={"id": package_id, "version": "1.0.0"})


def file(store, who, reason="scam", package_id="alice/notes", **extra):
    return reports.file_report(store, who, {"id": package_id, "reason": reason, **extra}, now=NOW)


def test_three_safety_reports_from_signed_in_people_pause_downloads_of_that_skill(tmp_path):
    store, catalog, app, *_ = service(tmp_path, SKILLS)
    alice = creator(store, "1", "alice")
    readers = [account(store, str(10 + index), f"reader{index}") for index in range(3)]
    with TestClient(app, base_url=ORIGIN) as client:
        signed_out = client.get(PREFIX + "/reports", params={"id": "alice/notes"}).json()
        assert signed_out == {"reasons": reports.public_reasons(), "note_limit": 500, "can_report": False}
        assert [reason["id"] for reason in signed_out["reasons"]] == ["scam", "harmful", "stolen", "picture", "broken", "other"]
        assert report(client, readers[0][2]).status_code == 400  # no session

        client.cookies.set("vool_registry", alice[1])
        refused = client.get(PREFIX + "/reports", params={"id": "alice/notes"}).json()
        assert refused["can_report"] is False and refused["can_report_reason"] == "own_skill"
        assert report(client, alice[2]).json() == {"error": "you cannot report your own skill"}

        # Ask for a download ticket before the reports arrive; the pause must stop it too.
        early = ticket(client).json()["url"]
        for index, (reason, (_, token, csrf)) in enumerate(zip(("scam", "harmful", "stolen"), readers, strict=True)):
            client.cookies.set("vool_registry", token)
            assert client.get(PREFIX + "/reports", params={"id": "alice/notes"}).json()["can_report"] is True
            sent = report(client, csrf, reason=reason, note="Step 2 asks for my wallet words." if index == 0 else "")
            assert sent.status_code == 201 and sent.json() == {"status": "received"}
            again = client.get(PREFIX + "/reports", params={"id": "alice/notes"}).json()
            assert again["can_report"] is False and again["can_report_reason"] == "already_reported"
            assert "already reported" in report(client, csrf, reason=reason).json()["error"]
            if index < 2:
                assert ticket(client).status_code == 200  # one or two reports pause nothing

        assert ticket(client).json() == {"error": DOWNLOADS_PAUSED}
        assert client.get(early).json() == {"error": DOWNLOADS_PAUSED}  # a ticket from before the pause
        assert ticket(client, "bob/plain").status_code == 200  # other skills are untouched
        rows = {row["id"]: row for row in client.get(PREFIX + "/catalog").json()["entries"]}
        assert rows["alice/notes"]["downloads_paused"] is True and "downloads_paused" not in rows["bob/plain"]
        client.cookies.clear()
        # Visitors learn only that downloads are paused: never how many reports, why, or from whom.
        public = json.dumps([client.get(PREFIX + "/catalog").json(), client.get(PREFIX + "/profiles/alice").json()])
        assert "wallet words" not in public and "reader" not in public and "report" not in public
        assert client.get(PREFIX + "/reports", params={"id": "alice/notes"}).json() == signed_out
    catalog.close()


def test_new_muted_repeating_and_non_safety_reports_are_heard_but_cannot_pause_a_skill(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    fresh = account(store, "2", "fresh", created=NOW - 60)[0]
    muted = account(store, "3", "muted")[0]
    community.mute(store, "muted", True, now=NOW)
    broken, picture, other = account(store, "4", None)[0], account(store, "5", None)[0], account(store, "6", None)[0]
    counting = [account(store, str(10 + index), None)[0] for index in range(3)]
    for who, reason in ((fresh, "scam"), (muted, "harmful"), (broken, "broken"), (picture, "picture"),
                        (counting[0], "scam"), (counting[1], "stolen")):
        assert file(store, who, reason) == {"status": "received"}
    file(store, other, "other", note="The summary calls people names.")
    assert reports.paused(store) == frozenset()  # seven reports, two of them counting
    [package] = reports.open_reports(store)
    assert [item["counted"] for item in package["reports"]] == [False, False, False, False, True, True, False]
    file(store, counting[2], "harmful")
    assert reports.paused(store) == frozenset({"alice/notes"})
    with pytest.raises(RegistryError, match="not published"):
        file(store, fresh, package_id="alice/missing")
    catalog.close()


def test_the_same_accounts_cannot_pause_a_skill_again_after_an_operator_looked(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    readers = [account(store, str(10 + index), None)[0] for index in range(4)]
    for who in readers[:3]:
        file(store, who)
    reports.close(store, "alice/notes")
    for who in readers[:3]:
        file(store, who)  # heard again, so a real new problem still reaches the operator
    assert reports.paused(store) == frozenset()
    assert [item["counted"] for item in reports.open_reports(store)[0]["reports"]] == [False] * 3
    file(store, readers[3])  # someone new does count
    assert reports.open_reports(store)[0]["reports"][-1]["counted"] is True
    catalog.close()


def test_operators_mute_the_account_behind_a_report_without_learning_who_it_is(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    readers = [account(store, str(10 + index), f"reader{index}")[0] for index in range(3)]
    community.record_download(store, readers[0], "bob/plain", now=NOW)
    assert community.post_comment(store, readers[0], "bob/plain", "Clear and short", now=NOW)["state"] == "published"
    file(store, readers[0])
    file(store, readers[0], "broken", package_id="bob/plain")
    file(store, readers[1])
    alice, bob = reports.open_reports(store)
    first = alice["reports"][0]
    assert first["reporter"] == bob["reports"][0]["reporter"] != alice["reports"][1]["reporter"]  # same account, same handle
    assert len(first["reporter"]) == 12 and all(readers[0] not in json.dumps(item) for item in (alice, bob))
    reports.mute_reporter(store, first["id"], now=NOW)
    assert [item["counted"] for item in reports.open_reports(store)[0]["reports"]] == [False, True]
    assert community.comments(store, "bob/plain")["comments"] == []  # muted means muted everywhere
    file(store, readers[2])
    assert reports.paused(store) == frozenset()  # the muted report no longer helps reach three
    with pytest.raises(RegistryError, match="report not found"):
        reports.mute_reporter(store, "f" * 32, now=NOW)
    catalog.close()


def test_a_pause_never_drops_out_of_the_operators_list(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    readers = [account(store, str(10 + index), None)[0] for index in range(3)]
    for who in readers:
        file(store, who)
    for who in readers:
        store.delete_account(who)  # their reports go with them, the pause stays
    assert reports.open_reports(store) == [{"package_id": "alice/notes", "paused": "reports", "reports": []}]
    reports.pause(store, "bob/plain", now=NOW)
    assert [(item["package_id"], item["paused"]) for item in reports.open_reports(store)] == [
        ("alice/notes", "reports"), ("bob/plain", "operator")]
    catalog.close()


@pytest.mark.parametrize("body, message", [
    ({"id": "alice/notes"}, "invalid report fields"),
    ({"id": "alice/notes", "reason": "scam", "chat": "my last 20 messages"}, "invalid report fields"),
    ({"id": "alice/notes", "reason": "spam"}, "choose one of the listed reasons"),
    ({"id": "alice/notes", "reason": "other"}, "describe the problem in the note"),
    ({"id": "alice/notes", "reason": "scam", "note": "x" * 501}, "at most 500 characters"),
    ({"id": "alice/notes", "reason": "scam", "note": ["not", "text"]}, "must be text"),
    ({"id": "alice/notes", "reason": "scam", "note": "it rang\x07 a bell"}, "plain text"),
    ({"id": "alice/notes", "reason": "scam", "note": "fine ‮txt.exe"}, "plain text"),  # a direction override
    ({"id": "alice/notes", "reason": "scam", "note": "it asked for: " + " ".join(PHRASE)}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": " ".join(f"{i}. {w}" for i, w in enumerate(PHRASE, 1))}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": " ".join(f"{i}) {w}" for i, w in enumerate(PHRASE, 1))}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "-".join(PHRASE)}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": " / ".join(PHRASE)}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "\n".join(PHRASE)}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": " ".join(PHRASE[:11])}, SECRET_REFUSAL),  # one word short
    ({"id": "alice/notes", "reason": "scam", "note": " ".join([*PHRASE[:4], "wavee", *PHRASE[5:]])}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "my key sk-proj-" + "a1B2" * 8 + " leaked"}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "-----BEGIN PRIVATE KEY-----\nMIIE\n"}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "it showed " + "4c0883a69102937d" * 4}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "key: " + " ".join(["4c08", "83a6"] * 8)}, SECRET_REFUSAL),
    ({"id": "alice/notes", "reason": "scam", "note": "my password is Hunter22!"}, SECRET_REFUSAL),
    ({"id": ["alice/notes"], "reason": "scam"}, "invalid package id"),
])
def test_report_fields_and_notes_are_checked_and_refused_notes_are_never_kept(tmp_path, body, message):
    store, catalog, *_ = service(tmp_path, SKILLS)
    reader = account(store, "10", None)[0]
    with pytest.raises(RegistryError, match=message):
        reports.file_report(store, reader, body, now=NOW)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM skill_reports").fetchone()[0] == 0
    catalog.close()


@pytest.mark.parametrize("note", [
    "Copied word for word from https://github.com/carol/notes-skill (MIT, no credit).",
    "It asks me to paste my twelve words into a box and then sends them to a server I don't know.",
    "Step 1: open it. Step 2: it asks for your seed phrase. Step 3: your money is gone.",
    "Great idea, but the 👩‍💻 emoji in the title breaks it",
    "המיומנות הזו לא עובדת‏",
    "Hindi: इस्तेमाल\tworks with a tab too",
])
def test_ordinary_notes_in_any_language_are_kept_as_written(tmp_path, note):
    store, catalog, *_ = service(tmp_path, SKILLS)
    reader = account(store, "10", None)[0]
    file(store, reader, "other", note="  " + note + "\n")
    assert reports.open_reports(store)[0]["reports"][0]["note"] == note
    catalog.close()


def test_notes_are_refused_while_the_key_check_cannot_run(tmp_path, monkeypatch):
    store, catalog, *_ = service(tmp_path, SKILLS)
    reader = account(store, "10", None)[0]
    monkeypatch.setattr(reports, "mnemonic_redaction_available", lambda: False)
    with pytest.raises(RegistryError, match="notes can't be sent right now"):
        file(store, reader, note="It is a fake.")
    file(store, reader)  # no note, nothing to check
    catalog.close()


def test_each_account_sends_at_most_ten_reports_a_day(tmp_path):
    skills = [(f"pub{index}/skill", {}, NOW - 100) for index in range(12)]
    store, catalog, *_ = service(tmp_path, skills)
    reader = account(store, "10", None)[0]
    for index in range(10):
        reports.file_report(store, reader, {"id": f"pub{index}/skill", "reason": "broken"}, now=NOW + index)
    status = reports.status(store, "pub10/skill", reader, now=NOW + 10)
    assert status["can_report"] is False and status["can_report_reason"] == "daily_limit"
    with pytest.raises(RegistryError, match="most reports allowed"):
        reports.file_report(store, reader, {"id": "pub10/skill", "reason": "broken"}, now=NOW + 10)
    reports.file_report(store, reader, {"id": "pub10/skill", "reason": "broken"}, now=NOW + DAY + 1)
    catalog.close()


def test_reports_sent_at_the_same_moment_still_count_once(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    reader = account(store, "10", None)[0]
    start, outcomes = threading.Barrier(12), []

    def send():
        start.wait()
        try:
            outcomes.append(file(store, reader)["status"])
        except RegistryError as error:
            outcomes.append(str(error))

    workers = [threading.Thread(target=send) for _ in range(12)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert sorted(outcomes) == ["received"] + ["you already reported this skill; a person will look at it"] * 11
    catalog.close()


def test_creators_cannot_report_a_skill_they_submitted_under_another_name(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    former = account(store, "20", "renamed")[0]
    with store.connection() as db:
        db.execute("INSERT INTO submissions(id, account, created, package_id, version, state) VALUES (?,?,?,?,?,?)",
                   ("a" * 32, former, NOW, "alice/notes", "1.0.0", "published"))
    assert reports.status(store, "alice/notes", former, now=NOW)["can_report_reason"] == "own_skill"
    catalog.close()


def operator(config, *command):
    return cli.main([command[0], "--config", str(config), *(["--target", command[1]] if len(command) > 1 else [])])


def test_operators_read_reports_then_close_them_or_keep_the_skill_paused(tmp_path, capsys, monkeypatch):
    store, catalog, app, *_ = service(tmp_path, SKILLS)
    config = tmp_path / "service.json"  # operator commands read only the private store
    config.write_text(json.dumps({"schema": "vool.registry.service.v1", "private_dir": str(tmp_path / "private")}))
    monkeypatch.setattr(cli, "time", SimpleNamespace(time=lambda: NOW))
    readers = [account(store, str(10 + index), None)[0] for index in range(5)]
    for who, reason in zip(readers[:3], ("scam", "harmful", "stolen"), strict=True):
        file(store, who, reason, note="Asks for wallet words." if reason == "scam" else "")
    file(store, readers[3], "broken", package_id="bob/plain")

    assert operator(config, "reports-open") == 0
    listed = json.loads(capsys.readouterr().out)
    assert [(item["package_id"], item["paused"], [report["reason"] for report in item["reports"]]) for item in listed] == [
        ("alice/notes", "reports", ["scam", "harmful", "stolen"]), ("bob/plain", None, ["broken"])]
    assert listed[0]["reports"][0]["note"] == "Asks for wallet words."

    # Looked at and nothing wrong: the reports close, their notes are wiped and downloads continue.
    assert operator(config, "reports-close", "alice/notes") == 0
    with store.connection() as db:
        assert [tuple(row) for row in db.execute("SELECT note, state FROM skill_reports WHERE package_id='alice/notes'")] == [
            ("", "closed")] * 3
    with TestClient(app, base_url=ORIGIN) as client:
        assert ticket(client).status_code == 200
        assert "downloads_paused" not in client.get(PREFIX + "/catalog").json()["entries"][0]

    # Looked at and it is bad: the operator keeps it paused, and closing later reports does not end that.
    capsys.readouterr()
    assert operator(config, "skill-pause", "alice/notes") == 0
    file(store, readers[4], "broken")
    assert operator(config, "reports-close", "alice/notes") == 0
    with TestClient(app, base_url=ORIGIN) as client:
        assert ticket(client).json() == {"error": DOWNLOADS_PAUSED}
    assert operator(config, "skill-resume", "alice/notes") == 0  # ending an operator's pause is its own step
    assert reports.paused(store) == frozenset()

    # Muting by report id, the only handle an operator has.
    report_id = reports.open_reports(store)[0]["reports"][0]["id"]
    assert operator(config, "reporter-mute", report_id) == 0
    with store.connection() as db:
        assert db.execute("SELECT 1 FROM muted_accounts WHERE account=?", (readers[3],)).fetchone()
    capsys.readouterr()
    for command, target, message in (("reports-close", "alice/typo", "no open reports or pause"),
                                     ("skill-pause", "alice/typo", "not published"),
                                     ("skill-resume", "bob/plain", "not paused"),
                                     ("reporter-mute", "nope", "report not found")):
        assert operator(config, command, target) == 1
        assert message in capsys.readouterr().out
    catalog.close()


def test_deleting_an_account_deletes_its_reports(tmp_path):
    store, catalog, *_ = service(tmp_path, SKILLS)
    reader = account(store, "10", None)[0]
    file(store, reader, note="Looks fake.")
    store.delete_account(reader)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM skill_reports").fetchone()[0] == 0
    catalog.close()


# -- the VOOL app ------------------------------------------------------------------------------


@contextlib.contextmanager
def bridge(service_client):
    """A real HTTP server in front of the registry app, so VOOL's own fetch reads the real answers."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            response = service_client.get(self.path)
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers["content-type"])
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    calls, base = [], f"http://127.0.0.1:{server.server_port}"

    def request(url, *, limit):
        from core.registry.client import fetch

        calls.append(url)
        return fetch(url.replace(ORIGIN, base), limit=limit)

    try:
        yield request, calls, base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def paused_service(tmp_path):
    store, catalog, app, policy, keys, root = service(tmp_path, SKILLS)
    readers = [account(store, str(10 + index), None)[0] for index in range(3)]
    for who in readers:
        reports.file_report(store, who, {"id": "alice/notes", "reason": "scam"}, now=NOW)
    return store, catalog, app, root


def test_vool_neither_downloads_nor_installs_a_paused_skill_and_shows_no_other_server_words(tmp_path, monkeypatch):
    from core import plugin_lifecycle
    from core.registry.client import Client, fetch
    from core.registry.native import home
    from tests.registry.audit_helpers import runtime

    runtime(tmp_path, monkeypatch)
    store, catalog, app, root = paused_service(tmp_path)
    with TestClient(app, base_url=ORIGIN) as service_client, bridge(service_client) as (request, calls, base):
        client = Client(root, TARGET, origin=ORIGIN, request=request, now=NOW)
        listing = client.refresh(approved=True, now=NOW)
        assert listing["report_page"] == ORIGIN + "/codex/skills/report"
        assert "downloads_paused" not in listing["entries"][0]  # the signed listing is unchanged by reports
        with pytest.raises(RegistryError) as refused:
            client.prepare("alice/notes", "1.0.0", approved=True, now=NOW)
        assert str(refused.value) == DOWNLOADS_PAUSED

        # Paused after it was downloaded for inspection: the local copy is not installed either.
        review = client.prepare("bob/plain", "1.0.0", approved=True, now=NOW)
        staged = home() / "registry_staging" / (review["review_id"] + ".json")
        assert staged.is_file()
        reports.pause(store, "bob/plain", now=NOW)
        with pytest.raises(RegistryError) as refused:
            client.accept("bob/plain", "1.0.0", review["review_id"], accepted=True, enable=False, now=NOW)
        assert str(refused.value) == DOWNLOADS_PAUSED
        assert staged.is_file() and not plugin_lifecycle.records()

        # A registry VOOL cannot reach cannot say whether the skill is paused, so nothing is installed.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed = f"http://127.0.0.1:{probe.getsockname()[1]}"
        client.request = lambda url, *, limit: fetch(url.replace(ORIGIN, closed), limit=limit)
        with pytest.raises(RegistryError, match="registry unavailable"):
            client.accept("bob/plain", "1.0.0", review["review_id"], accepted=True, enable=False, now=NOW)
        assert staged.is_file() and not plugin_lifecycle.records()

        # Once an operator resumes downloads, the copy the person already inspected installs.
        client.request = request
        reports.resume(store, "bob/plain")
        result = client.accept("bob/plain", "1.0.0", review["review_id"], accepted=True, enable=False, now=NOW)
        assert result["enabled"] is False and plugin_lifecycle.record_for(result["plugin_id"])
        assert not staged.exists()
        assert not any("/reports" in url for url in calls)  # VOOL itself never sends a report
        client.close()
        # Any other refusal stays generic: a server cannot put its own words in front of the person.
        with pytest.raises(RegistryError, match="registry unavailable"):
            fetch(base + PREFIX + "/pictures/alice/notes/" + "a" * 64, limit=65536)  # HTTP 400 "picture not found"
    catalog.close()

    class Odd(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            code, body = {
                "/paused": (400, json.dumps({"error": DOWNLOADS_PAUSED}).encode()),
                "/unavailable": (503, json.dumps({"error": DOWNLOADS_PAUSED}).encode()),
                "/longer": (400, json.dumps({"error": DOWNLOADS_PAUSED + ". Call +1 555 0100 to unlock it"}).encode()),
                "/cut": (400, None),
            }[self.path]
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            if body is None:  # the connection drops in the middle of the answer
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"200\r\n" + json.dumps({"error": DOWNLOADS_PAUSED}).encode()[:20])
            else:
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Odd)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        odd = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(RegistryError) as refused:
            fetch(odd + "/paused", limit=65536)
        assert str(refused.value) == DOWNLOADS_PAUSED
        for path in ("/unavailable", "/longer", "/cut"):  # wrong status, extra words, a body cut short
            with pytest.raises(RegistryError) as refused:
                fetch(odd + path, limit=65536)
            assert str(refused.value).startswith("registry unavailable"), path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_the_report_link_opens_the_website_page_and_vool_sends_nothing(tmp_path, monkeypatch):
    from core import plugin_lifecycle
    from core.registry import client as native
    from core.vool_chat_page import render_vool_chat_html
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post
    from tests.registry.audit_helpers import runtime
    from tests.served_browser import launch_chromium

    runtime(tmp_path, monkeypatch)
    store, catalog, app, root = paused_service(tmp_path)
    service_client = TestClient(app, base_url=ORIGIN)
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(tmp_path / "no-native"))
    monkeypatch.setattr(native, "time", SimpleNamespace(time=lambda: NOW))
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, content_type="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/chat":
                return self.reply(200, render_vool_chat_html().encode(), "text/html")
            if path in {"/api/addons", "/api/plugins", "/api/intake/providers"}:
                result = dispatch_get(path=path, query={}, runtime=runtime_services, model_name="fixture",
                                      client_host="127.0.0.1")
                return self.reply(result.status, result.body)
            return self.reply(200, b"{}")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path != "/api/addons":
                return self.reply(200, b"{}")
            posts.append(body.get("action"))
            result = dispatch_post(path=self.path, body=body, headers=dict(self.headers), runtime=runtime_services,
                                   model_name="fixture", workspace_root_provider=lambda: str(tmp_path),
                                   client_host="127.0.0.1")
            return self.reply(result.status, result.body)

    runtime_services = RuntimeServices(display_name="VOOL")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager, browser = launch_chromium()
    try:
        with bridge(service_client) as (request, calls, _):
            monkeypatch.setattr(native, "configured_client", lambda: native.Client(
                root, TARGET, origin=ORIGIN, request=request, now=NOW))
            context = browser.new_context(viewport={"width": 1280, "height": 960})
            website = []
            context.route(ORIGIN + "/**", lambda route: (website.append((route.request.method, route.request.url,
                                                                         route.request.post_data)),
                                                         route.fulfill(status=200, body="report page")))
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/chat", wait_until="domcontentloaded")
            page.locator("#homeToggle").click()
            page.get_by_role("button", name="Skills & Plugins", exact=True).click()
            page.locator("#addonNav").get_by_role("button", name="Skill Codex", exact=True).click()
            page.get_by_role("button", name="Browse Skill Codex", exact=True).click()

            def card(name):
                return page.locator("#pluginsBody .addon-card").filter(has=page.get_by_role("heading", name=name, exact=True))

            # A quiet link under each card, named for its skill so a screen reader can tell the links apart.
            report_link = card("alice/notes").locator(".skill-report-row").get_by_role(
                "link", name="Report this skill: alice/notes", exact=True)
            report_link.wait_for()
            assert report_link.inner_text() == "Report this skill"
            assert report_link.get_attribute("href") == ORIGIN + "/codex/skills/report?id=alice%2Fnotes"
            assert report_link.get_attribute("target") == "_blank"
            assert set(report_link.get_attribute("rel").split()) == {"noopener", "noreferrer"}
            assert website == [] and not any("/reports" in url for url in calls)

            before = list(posts)
            with page.expect_popup() as opened:
                report_link.click()
            opened.value.wait_for_load_state()
            assert website == [("GET", ORIGIN + "/codex/skills/report?id=alice%2Fnotes", None)]
            assert posts == before  # the click opens the page; VOOL sends nothing of its own
            assert not any("/reports" in url for url in calls)
            opened.value.close()

            # A paused skill says so calmly on its own card, in place of the download button, and no error code shows.
            card("alice/notes").get_by_role("button", name="Download for inspection", exact=True).click()
            note = card("alice/notes").locator(".skill-paused")
            note.wait_for()
            assert note.get_attribute("role") == "status"
            assert note.inner_text() == ("Downloads of this skill are paused while a person looks into it. "
                                         "Please check back later.")
            assert card("alice/notes").get_by_role("button", name="Download for inspection").count() == 0
            assert "registry_" not in page.locator("#pluginsBody").inner_text()
            assert card("alice/notes").locator(".skill-report-row a").count() == 1  # still reportable

            card("bob/plain").get_by_role("button", name="Download for inspection", exact=True).click()
            page.get_by_role("button", name="Install and enable", exact=True).wait_for()
            review = page.locator("#pluginsBody .skill-report")
            assert review.get_by_role("link", name="Report this skill: bob/plain", exact=True).get_attribute("href") == (
                ORIGIN + "/codex/skills/report?id=bob%2Fplain")
            assert "VOOL doesn't send anything itself" in review.inner_text()

            # Paused while the person was reading the review: nothing installs and the card says why.
            reports.pause(store, "bob/plain", now=NOW)
            page.get_by_role("button", name="Install and enable", exact=True).click()
            card("bob/plain").locator(".skill-paused").wait_for()
            assert not plugin_lifecycle.records()

            # A fresh Browse asks again, so a skill whose downloads resumed is offered again.
            reports.resume(store, "bob/plain")
            page.get_by_role("button", name="Browse Skill Codex", exact=True).click()
            card("bob/plain").get_by_role("button", name="Download for inspection", exact=True).wait_for()
            assert card("bob/plain").locator(".skill-paused").count() == 0
            assert not any("/reports" in url for url in calls)
            assert posts.count("registry_install") == 1
            assert not plugin_lifecycle.records()
    finally:
        browser.close()
        manager.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service_client.close()
        catalog.close()
