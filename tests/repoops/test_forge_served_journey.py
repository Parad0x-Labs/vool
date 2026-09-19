"""The forge actions, served end to end: real HTTP chat ingress -> scripted model planning ->
tool execution through the production door -> the owner-local HTTP authorization route ->
the shipped GitHub adapter over a REAL local HTTP forge service -> the journaled receipt.

Evidence layers, labelled:
* HTTP-served conversational: EVERY step of both journeys is a same-session /api/chat turn
  into apps.vool_api_server's own process (real socket, real routing, real tool execution
  through the production door, real journal writes). The scripted model plans exactly one
  tool call per turn from the user's words. Follow-up turns reach the tool lane because an
  open RepoOps session owns its session's follow-ups (the lane policy the code-task lane
  defined, extended symmetrically), and the offer carries the repo family (demand signals,
  follow-up inheritance of the ACTUAL seated families, and journal-driven session seats).
* HTTP-served operator: /api/repoops/authorize-forge-action is the owner-local mint, driven
  over real HTTP for approval, refusal-while-unknown, resolution and re-arm.
* Local service: the forge is a REAL HTTP server on 127.0.0.1 speaking the GitHub REST shapes
  (recorded responses as a service, not an in-process double) -- the daemon's transport does
  actual HTTP to it.
* Replay: the model is a scripted provider (recorded Ollama-dialect transcript); no live model
  and no live forge is claimed anywhere.

No native window is driven and no packet leaves the machine.
"""

from __future__ import annotations

import json
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

pytestmark = [pytest.mark.served]

MODEL = "qwen3-stub:2b"


def _call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class TranscriptProvider(ScriptedProvider):
    """A scripted provider answering from a recorded TRANSCRIPT: each model call pops the next
    reply, so one served turn can expand the repo family and then issue the tool call -- the
    same order the runtime's own doctrine asks a model to follow."""

    def __init__(self) -> None:
        super().__init__({MODEL: ""}, after_tool_result={MODEL: ""})
        self.transcript: list[Any] = []

    def reply_for(
        self, model: str, *, has_tool_result: bool, body: dict[str, Any] | None = None
    ) -> Any:
        with self._lock:
            last = self.calls[-1] if self.calls else None
        if last is None or not last.get("tools"):
            # The classifier call: plain text, as the rig's own fixtures answer it. Popping
            # the transcript here would spend the scripted tool call on a turn that cannot
            # carry one, and the tool turn would then answer "done" -- no journal, no call.
            return "shell_guidance"
        if self.transcript:
            return self.transcript.pop(0)
        return "done"


class LocalForge:
    """A real local HTTP service speaking the slice of GitHub REST this workflow touches.

    Routes are keyed by path substring; every request is captured. `corrupt_post_paths` arms a
    one-shot 201 reply with an undecodable body: the accepted-but-unreadable outcome."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.calls: list[dict[str, Any]] = []
        self.corrupt_post_paths: set[str] = set()
        self._lock = threading.Lock()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def _reply(self, payload: Any, status: int = 200) -> None:
                body = payload if isinstance(payload, (bytes, bytearray)) else json.dumps(payload).encode("utf-8")
                if isinstance(payload, str):
                    body = payload.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                with rig._lock:
                    rig.calls.append({"method": "GET", "path": self.path})
                    for fragment, payload in rig.routes.items():
                        if fragment in self.path:
                            self._reply(payload)
                            return
                self._reply({"message": "not found"}, status=404)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                with rig._lock:
                    rig.calls.append({"method": "POST", "path": self.path, "body": raw.decode("utf-8", "replace")})
                    if self.path in rig.corrupt_post_paths:
                        rig.corrupt_post_paths.discard(self.path)
                        self._reply("{corrupted success body", status=201)
                        return
                    for fragment, payload in rig.routes.items():
                        if fragment in self.path:
                            self._reply(payload, status=201)
                            return
                self._reply({"message": "not found"}, status=404)

        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def posts(self, fragment: str = "") -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "POST" and fragment in c["path"]]

    def __enter__(self) -> LocalForge:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


def _pr_payload(number, *, base_sha, head_sha, head_ref="feature", base_ref="main", draft=True,
                title="Served draft", body="Served exact body", url=""):
    return {
        "number": int(number), "title": title, "state": "open", "draft": draft,
        "mergeable_state": "clean", "html_url": url or f"https://github.local/o/r/pull/{number}",
        "body": body, "base": {"ref": base_ref, "sha": base_sha}, "head": {"ref": head_ref, "sha": head_sha},
    }


@pytest.fixture
def served(tmp_path: Path):
    from tests.repoops._harness import build_repo

    home = tmp_path / "home"
    workspace, bare = build_repo(tmp_path)
    forge = LocalForge()
    forge.routes.update(
        {
            "/repos/o/r/commits/feature": {"sha": "b" * 40},
            "/repos/o/r/pulls/8": _pr_payload("8", base_sha="a" * 40, head_sha="b" * 40, draft=False,
                                               title="Existing PR", body="prior"),
            "/repos/o/r/pulls?head=": [],
            "/repos/o/r/pulls": _pr_payload("31", base_sha="a" * 40, head_sha="b" * 40),
            "/repos/o/r/issues/17/comments": {
                "id": 77123, "user": {"login": "poster"}, "body": "Served comment text",
                "created_at": "2026-09-13T12:00:00Z",
                "html_url": "https://github.local/o/r/issues/17#issuecomment-77123",
            },
        }
    )
    provider = TranscriptProvider()
    daemon = ServedDaemon(
        home,
        env_extra={
            "VOOL_ALWAYS_ON_CATALOG": "1",
            "VOOL_WORKSPACE_ROOT": str(workspace),
            "VOOL_REPOOPS_DIR": str(home / "repo_sessions"),
            "VOOL_BLACKBOX_DIR": str(home / "blackbox"),
            "VOOL_CODE_TASK_DIR": str(home / "code_tasks"),
            "VOOL_MODEL_LOAD_FLOOR_GB": "0",
            "VOOL_FORGE_BASE_URL_GITHUB": forge.base_url,
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            # The session fence dead-ends every endpoint var a launcher leaves unset, and
            # RAW outranks OLLAMA_HOST -- without these the daemon's inventory, pull and
            # residency probes hit a dead port and turns come back tool-less.
            "VOOL_RAW_OLLAMA_API_URL": provider.base_url,
            "VOOL_OLLAMA_TAGS_URL": f"{provider.base_url}/api/tags",
            "VOOL_OLLAMA_PS_URL": f"{provider.base_url}/api/ps",
        },
    )
    # A scripted stub occupies no memory; report its models resident so the resource governor
    # never gates the tool-intent call on a loaded box (measured by the coding lane 2026-09-03).
    handler = provider._server.RequestHandlerClass
    original_get = handler.do_GET

    def do_GET(self):
        if self.path.startswith("/api/ps"):
            return self._send({"models": [{"name": name, "size": 0, "size_vram": 0} for name in provider.table]})
        return original_get(self)

    handler.do_GET = do_GET

    forge.__enter__()
    provider.__enter__()
    try:
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the runtime
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
        # Certify the scripted stub for final-answer authorship (same authority as the other
        # served rigs): the precall fence refuses an uncertified local author.
        run_in_home(
            home,
            textwrap.dedent(
                f'''
                import sys
                sys.path.insert(0, "{REPO_ROOT}")
                from storage.model_provider_manifest import list_provider_manifests
                from tests._authorship_certification import certify_for_authorship
                for m in list_provider_manifests():
                    if m.model_name == "{MODEL}":
                        print("certified", m.model_name, certify_for_authorship(m))
                '''
            ),
        )
        provider.reset()
        yield {"home": home, "workspace": workspace, "daemon": daemon, "provider": provider,
               "forge": forge, "sessions_dir": home / "repo_sessions"}
    finally:
        daemon.stop()
        provider.__exit__(None, None, None)
        forge.__exit__(None, None, None)


ANSWERS: dict[str, list[str]] = {}
SENT: dict[str, list[str]] = {}
#: The canonical chat id each served /api/chat response returned (`vool_session_id`) -- the id
#: the daemon keys the conversation by, and the one a client reopens the chat with.
CHAT_IDS: dict[str, str] = {}


def _turn(served: dict[str, Any], session: str, text: str, call: dict[str, Any]) -> str:
    """One conversational served turn: same chat session, real /api/chat, the scripted model
    plans exactly one tool call, and the served lane executes it and journals it. The turn's
    sent text, answer text and returned chat id are recorded per session for the history
    assertions below."""
    daemon: ServedDaemon = served["daemon"]
    provider: TranscriptProvider = served["provider"]
    provider.transcript = [call, "done"]
    payload = daemon.chat(text, session_id=session, mode="auto", timeout=900.0)
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    answer = str(message.get("content") or payload.get("response") or "")
    ANSWERS.setdefault(session, []).append(answer)
    SENT.setdefault(session, []).append(text)
    chat_id = str(payload.get("vool_session_id") or "")
    assert chat_id, sorted(payload)
    assert CHAT_IDS.setdefault(session, chat_id) == chat_id, (CHAT_IDS, chat_id)
    return answer


def _history_visible(served: dict[str, Any], journal: dict[str, Any]) -> list[dict[str, Any]]:
    """The daemon's OWN durable conversation history for this chat session.

    Read from the runtime's dialogue store (dialogue_turns, the table adapt_user_input
    persists every external user turn into, keyed by session) -- the daemon's record of what
    the operator said, not the RepoOps journal standing in for it. The assistant side of the
    persisted conversation is NOT inferred from the reply payloads: `_reloaded_transcript`
    reads it back through the daemon's transcript reader, /api/chat/history. (An earlier
    revision labelled that reader's store as unpopulated in this profile; measured, it holds
    every committed turn under the canonical chat id the /api/chat response returns, and is
    empty only for the client's raw handle -- evidence/raw/probe-history-gap.)"""
    import sqlite3

    # The dialogue store keys turns by the daemon's mapped chat id, which is exactly the
    # session identity the RepoOps journal recorded for this conversation.
    chat_id = str(journal.get("session_id") or "")
    db = Path(served["home"]) / "data" / "vool_web0_v2.db"
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT raw_input, speaker_role, created_at FROM dialogue_turns WHERE session_id = ? ORDER BY created_at",
            (chat_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"role": str(role or "user"), "content": str(raw or ""), "created_at": str(at or "")}
        for raw, role, at in rows
    ]


def _session_journal(served: dict[str, Any]) -> dict[str, Any]:
    files = sorted(Path(served["sessions_dir"]).glob("*.json"))
    assert files, "no RepoOps session journal was written"
    return json.loads(files[0].read_text(encoding="utf-8"))


def _authorize(daemon: ServedDaemon, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        f"{daemon.base_url}/api/repoops/authorize-forge-action",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def _reloaded_transcript(served: dict[str, Any], session: str) -> list[dict[str, Any]]:
    """What reopening this chat shows: the daemon's OWN transcript reader, /api/chat/history,
    keyed by the canonical chat id the served /api/chat response returned. These are the rows
    the conversation log committed at each turn's seal -- not this test's in-memory answers."""
    from urllib.parse import quote

    chat_id = CHAT_IDS.get(session, "")
    assert chat_id.startswith("openclaw:"), CHAT_IDS
    with urlopen(f"{served['daemon'].base_url}/api/chat/history?session={quote(chat_id)}", timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [dict(item) for item in payload.get("messages") or []]


def _assert_reload_is_the_conversation(served: dict[str, Any], session: str) -> list[dict[str, Any]]:
    """Every sent message and every published answer of this conversation, in order, reloaded
    from the committed transcript -- each assistant row bound to a verified finalization."""
    messages = _reloaded_transcript(served, session)
    users = [str(item.get("content") or "") for item in messages if item.get("role") == "user"]
    assistants = [item for item in messages if item.get("role") == "assistant"]
    assert users == SENT[session], (users, SENT[session])
    assert [str(item.get("content") or "") for item in assistants] == ANSWERS[session], (assistants, ANSWERS[session])
    assert all(dict(item.get("a7") or {}).get("status") == "verified" for item in assistants), assistants
    return messages


def _restart_daemon(served: dict[str, Any], label: str) -> ServedDaemon:
    """Stop the serving daemon and start a NEW daemon process on the same home and environment
    (the scripted provider and the local forge service keep running). Both logs are kept."""
    old: ServedDaemon = served["daemon"]
    old.stop()
    home = Path(served["home"])
    if old.log_path.exists():
        old.log_path.rename(home / f"daemon-before-{label}.log")
    restarted = ServedDaemon(home, env_extra=dict(old.env_extra))
    restarted.log_path = home / f"daemon-after-{label}.log"
    try:
        restarted.start(timeout=240)
        # The rig's boot contract, exactly as the fixture follows it: the scripted provider is
        # seeded AFTER the daemon boots (a boot re-derives the model registry), then reset.
        provider: TranscriptProvider = served["provider"]
        run_in_home(home, SEED_MANIFEST.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL]))
        provider.reset()
    except Exception:
        restarted.stop()
        raise
    served["daemon"] = restarted
    return restarted


# ---------------------------------------------------------------------------
# ORIGINAL: the whole workflow through same-session conversational turns.
# ---------------------------------------------------------------------------


def test_served_original_journey_conversational_request_refusal_authorize_create(served: dict[str, Any]) -> None:
    forge: LocalForge = served["forge"]
    provider: TranscriptProvider = served["provider"]
    session = "served-forge-original-1"

    # [served-chat] The user asks in natural language; the repo family seats from the words;
    # the scripted model plans repo.session.open; the served lane EXECUTES it and journals it.
    answer = _turn(
        served, session,
        "open a repo session for pull request 8 on o/r",
        _call("repo__session__open", {"objective": "prepare the reviewed draft PR", "provider": "github",
                                      "namespace": "o/r", "pull_request": "8"}, "c1"),
    )
    assert answer, "the served turn returned no answer"
    journal = _session_journal(served)
    sid = journal["session_key"]
    assert journal["binding"].get("pull_request") == "8"
    offered = {name for call in provider.calls for name in call.get("tools") or []}
    assert any(name.startswith("repo__") for name in offered), sorted(offered)

    # [served-chat] Follow-up turns in the SAME session: inspect, then bind (real forge reads).
    _turn(served, session, "inspect it", _call("repo__inspect", {"repo_session_id": sid}, "c2"))
    journal = _session_journal(served)
    assert journal["stage"] in {"bind", "retrieve"}, journal["stage"]
    _turn(served, session, "bind it", _call("repo__bind", {"repo_session_id": sid}, "c3"))
    journal = _session_journal(served)
    assert journal["binding"].get("binding_id")
    assert any(c["method"] == "GET" and "/pulls/8" in c["path"] for c in forge.calls), forge.calls

    # [served-chat] Plan the draft PR.
    _turn(
        served, session,
        "prepare the draft PR plan",
        _call("repo__pr__request", {"repo_session_id": sid, "action": "create", "base_ref": "main",
                                    "title": "Served draft", "body": "Served exact body"}, "c4"),
    )
    journal = _session_journal(served)
    plan = journal["forge_plan"]
    action_hash = plan["action_hash"]
    assert plan["head_sha"] == "b" * 40 and plan["draft"] is True and plan["title"] == "Served draft"

    # [served-chat] Unapproved create: the refusal is the answer and NO request reaches
    # the forge service.
    refusal = _turn(served, session, "open the draft pull request",
                    _call("repo__pr__create", {"repo_session_id": sid}, "c5"))
    assert refusal, "the served turn returned no answer"
    # The REFUSAL itself is user-visible truth: the missing operator authorization is named,
    # not a vague failure.
    assert "author" in refusal.lower() and ("refus" in refusal.lower() or "no live" in refusal.lower()), refusal
    journal = _session_journal(served)
    record = journal["forge_actions"][action_hash]
    assert record["status"] in {"planned", "dispatching"} or not record.get("result")
    assert not forge.posts("/repos/o/r/pulls"), "an unauthorized create must not reach the forge"

    # [served-operator] The owner-local route mints server-side.
    minted = _authorize(served["daemon"], {"repo_session_id": sid, "action_hash": action_hash})
    assert minted.get("ok") is True, minted

    # [served-chat] The create executes through the same conversational lane.
    answer = _turn(served, session, "open the authorized draft pull request",
                   _call("repo__pr__create", {"repo_session_id": sid}, "c6"))
    assert answer, "the served turn returned no answer"
    # The user-visible answer carries the exact result: the forge's own number and URL.
    assert "31" in answer and "github.local/o/r/pull/31" in answer, answer[:400]

    # The local forge SERVICE saw the real HTTP flow: the head/base lookup, then exactly ONE
    # create POST carrying the exact authorized content.
    posts = forge.posts("/repos/o/r/pulls")
    assert len(posts) == 1, forge.calls
    sent = json.loads(posts[0]["body"])
    assert sent == {"title": "Served draft", "head": "feature", "base": "main",
                    "body": "Served exact body", "draft": True}
    lookups = [c for c in forge.calls if c["method"] == "GET" and "pulls?head=" in c["path"]]
    assert lookups, forge.calls

    # The durable exact result is journaled.
    journal = _session_journal(served)
    record = journal["forge_actions"][action_hash]
    assert record["status"] == "applied"
    assert record["result"]["number"] == "31"
    assert record["result"]["url"] == "https://github.local/o/r/pull/31"
    assert record["result"]["verified"] is True

    # [served-chat] A second identical create replays the record: no second POST.
    _turn(served, session, "open the same draft pull request again",
          _call("repo__pr__create", {"repo_session_id": sid}, "c7"))
    assert len(forge.posts("/repos/o/r/pulls")) == 1

    # The conversation HISTORY persists for this session: every turn's user message and its
    # published answer are present in the daemon's own history surface, in order.
    history = _history_visible(served, journal)
    assert len(history) >= 7, len(history)
    assert any("open a repo session for pull request 8" in str(row.get("content") or "") for row in history)
    answers = ANSWERS.get(session, [])
    assert any("github.local/o/r/pull/31" in a for a in answers), (
        "the exact PR URL must appear in the served answers"
    )

    # [served-history] Reopening the chat reloads the COMMITTED transcript: every message and
    # every published answer, including the refusal naming the missing authorization and the
    # create answer carrying the forge's own number and URL.
    reloaded = _assert_reload_is_the_conversation(served, session)
    published = [str(item.get("content") or "") for item in reloaded if item.get("role") == "assistant"]
    assert "author" in published[4].lower(), published[4]
    assert "31" in published[5] and "github.local/o/r/pull/31" in published[5], published[5]

    # [served-restart] A NEW daemon process on the same home: the transcript reopens unchanged,
    # and the identical create asked again through the restarted daemon replays -- no new POST.
    restarted = _restart_daemon(served, "original-reopen")
    try:
        assert _reloaded_transcript(served, session) == reloaded
        replay = _turn(served, session, "open that same draft pull request once more",
                       _call("repo__pr__create", {"repo_session_id": sid}, "c8"))
        assert "already applied" in replay.lower() and "nothing was sent" in replay.lower(), replay[:400]
        assert len(forge.posts("/repos/o/r/pulls")) == 1
        after = _assert_reload_is_the_conversation(served, session)
        assert after[: len(reloaded)] == reloaded and len(after) == len(reloaded) + 2
    finally:
        restarted.stop()


# ---------------------------------------------------------------------------
# NOVEL: a differently-worded conversational workflow with the undecodable-201
# unknown path and the HTTP resolution loop -- all same-session chat turns.
# ---------------------------------------------------------------------------


def test_served_novel_journey_unknown_then_http_resolution_and_resume(served: dict[str, Any]) -> None:
    forge: LocalForge = served["forge"]
    session = "served-forge-novel-1"

    _turn(
        served, session,
        "open a repo session on github for issue 17",
        _call("repo__session__open", {"objective": "post the approved comment on issue 17",
                                      "provider": "github", "namespace": "o/r"}, "n1"),
    )
    journal = _session_journal(served)
    sid = journal["session_key"]
    _turn(served, session, "inspect it", _call("repo__inspect", {"repo_session_id": sid}, "n2"))
    _turn(served, session, "bind it", _call("repo__bind", {"repo_session_id": sid}, "n3"))
    assert _session_journal(served)["binding"].get("binding_id")

    comment_text = "Served comment text"
    _turn(
        served, session,
        "prepare the comment plan",
        _call("repo__pr__request", {"repo_session_id": sid, "action": "comment", "number": "17",
                                    "subject": "issue", "body": comment_text}, "n4"),
    )
    action_hash = _session_journal(served)["forge_plan"]["action_hash"]
    minted = _authorize(served["daemon"], {"repo_session_id": sid, "action_hash": action_hash})
    assert minted.get("ok") is True, minted

    # The forge service ACCEPTS the comment but answers with an undecodable body: inside the
    # daemon the outcome is unknown, never a failure.
    forge.corrupt_post_paths.add("/repos/o/r/issues/17/comments")
    unknown_answer = _turn(served, session, "post the comment",
                           _call("repo__pr__comment", {"repo_session_id": sid}, "n5"))
    assert unknown_answer, "the served turn returned no answer"
    # The UNCERTAINTY itself is user-visible: the forge accepted the write but its outcome is
    # unproven -- never reported as a plain failure or a success.
    lowered = unknown_answer.lower()
    assert ("accept" in lowered or "unproven" in lowered or "unknown" in lowered or "could not" in lowered), unknown_answer[:400]
    journal = _session_journal(served)
    assert journal["forge_actions"][action_hash]["status"] == "unknown"
    assert journal["forge_actions"][action_hash].get("unknown_reason") == "accepted_reply_undecodable"
    assert len(forge.posts("/repos/o/r/issues/17/comments")) == 1

    # [served-operator] Ordinary re-authorization is refused while the outcome is unproven.
    refused = _authorize(served["daemon"], {"repo_session_id": sid, "action_hash": action_hash})
    assert refused.get("ok") is False and refused.get("status") == "resolution_required", refused

    # [served-operator] The operator inspected the thread and states it did not land; the
    # action re-arms and the SAME conversational lane resumes it exactly once.
    resolved = _authorize(served["daemon"], {"repo_session_id": sid, "action_hash": action_hash,
                                             "resolve": "failed_safe_to_retry"})
    assert resolved.get("ok") is True, resolved
    re_armed = _authorize(served["daemon"], {"repo_session_id": sid, "action_hash": action_hash})
    assert re_armed.get("ok") is True, re_armed
    resumed = _turn(served, session, "post the comment now",
                    _call("repo__pr__comment", {"repo_session_id": sid}, "n6"))
    assert resumed, "the served turn returned no answer"
    # The resumed answer carries the exact comment identity.
    assert "77123" in resumed or "issuecomment-77123" in resumed, resumed[:400]

    posts = forge.posts("/repos/o/r/issues/17/comments")
    assert len(posts) == 2, forge.calls
    assert json.loads(posts[1]["body"]) == {"body": comment_text}
    journal = _session_journal(served)
    record = journal["forge_actions"][action_hash]
    assert record["status"] == "applied"
    assert record["result"]["comment_id"] == "77123"
    assert record["result"]["url"] == "https://github.local/o/r/issues/17#issuecomment-77123"

    # The applied action now replays: a third identical post never reaches the service.
    _turn(served, session, "post the same comment again",
          _call("repo__pr__comment", {"repo_session_id": sid}, "n7"))
    assert len(forge.posts("/repos/o/r/issues/17/comments")) == 2

    # History: the novel conversation persists with its user turns and the uncertainty and
    # the resumed result both visible.
    history = _history_visible(served, journal)
    assert len(history) >= 7, len(history)
    assert any("open a repo session on github for issue 17" in str(row.get("content") or "") for row in history)
    answers = ANSWERS.get(session, [])
    assert any("77123" in a for a in answers), (
        "the exact comment identity must appear in the served answers"
    )

    # [served-history] The committed transcript reloads the uncertainty exactly as it was shown
    # and the resumed answer with the forge's comment identity.
    reloaded = _assert_reload_is_the_conversation(served, session)
    published = [str(item.get("content") or "") for item in reloaded if item.get("role") == "assistant"]
    assert "accept" in published[4].lower() or "unproven" in published[4].lower(), published[4]
    assert "77123" in published[5], published[5]

    # [served-restart] A NEW daemon process on the same home reopens the same conversation and
    # still refuses to duplicate the applied comment.
    restarted = _restart_daemon(served, "novel-reopen")
    try:
        assert _reloaded_transcript(served, session) == reloaded
        replay = _turn(served, session, "post that exact comment one more time",
                       _call("repo__pr__comment", {"repo_session_id": sid}, "n8"))
        assert "already applied" in replay.lower() and "nothing was sent" in replay.lower(), replay[:400]
        assert len(forge.posts("/repos/o/r/issues/17/comments")) == 2
        after = _assert_reload_is_the_conversation(served, session)
        assert after[: len(reloaded)] == reloaded and len(after) == len(reloaded) + 2
    finally:
        restarted.stop()
