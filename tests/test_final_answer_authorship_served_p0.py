"""P0 AMENDMENT (D) — the authorship boundary, driven over a real HTTP door.

Everything else in this work is in-process. This pack starts `apps.vool_api_server` in its own
process with its own `VOOL_HOME` and its own port, puts a SCRIPTED provider behind it on another
port, and drives `POST /api/chat` over a socket. Between the request and the response, every line
is production code.

Why a scripted endpoint rather than a real model: model execution on this machine is cloud-only
(three OOM freezes, 2026-08-28), and a served proof does not need weights. It needs a real daemon,
a real HTTP door and a real provider call over the wire -- and, crucially, an endpoint that can be
ASKED whether the prohibited call ever arrived. "It was refused before the spend" is a claim until
the other end of the socket says nothing came.

The probe models are tagged `:2b` on purpose: `core.resource_governor` estimates a load footprint
from the model NAME and refuses one that cannot fit in free RAM, which is the guard that protects
this machine. A `:8b` probe is refused by it before the authorship question is ever reached.
"""
from __future__ import annotations

import sqlite3
import tempfile
import textwrap
from pathlib import Path

from tests._authorship_served_rig import REPO_ROOT, ScriptedProvider, ServedDaemon, seed_in_home

UNCERTIFIED = "probe-uncertified:2b"
CERTIFIED = "probe-certified:2b"

DIRECT_REQUEST = "Explain in two sentences how a Bloom filter can report a false positive."

UNCERTIFIED_ANSWER = (
    "A Bloom filter reports a false positive when every bit position a query key hashes to was "
    "already set by other insertions. The structure therefore admits false positives but never "
    "false negatives."
)
CERTIFIED_ANSWER = (
    "A Bloom filter can report a member that was never inserted, because independent keys share "
    "bit positions. It cannot report a non-member for a key it did hold."
)

_SEED = textwrap.dedent(
    '''
    import sys
    sys.path.insert(0, "{root}")
    from storage.db import get_connection
    from storage.migrations import run_migrations
    from storage.model_provider_manifest import (
        ModelProviderManifest, list_provider_manifests, upsert_provider_manifest,
    )
    from tests._authorship_certification import certify_for_authorship

    run_migrations()

    def manifest(name):
        return ModelProviderManifest(
            provider_name="ollama-local", model_name=name, source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://ollama.com/library/qwen3",
            weight_location="external", runtime_dependency="ollama",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{base_url}", "timeout_seconds": 30}},
            metadata={{
                "runtime_family": "ollama", "cost_class": "free_local",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 2.0,
            }},
        )

    # The daemon seeds its own bundle lane at boot; this pack decides the candidate set, so the
    # registry is replaced wholesale AFTER boot. The registry is read from the database per call,
    # so no restart is needed.
    conn = get_connection()
    conn.execute("DELETE FROM model_provider_manifests")
    conn.commit()
    conn.close()
    for name in {registered!r}:
        upsert_provider_manifest(manifest(name))
    for name in {certified!r}:
        certify_for_authorship(manifest(name))
    print(sorted(m.provider_id for m in list_provider_manifests()))
    '''
)


def _reply_text(payload: dict) -> str:
    message = payload.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return str((choices[0].get("message") or {}).get("content") or "")
    return str(payload.get("response") or "")


class _Served:
    def __init__(
        self,
        registered: tuple[str, ...],
        certified: tuple[str, ...],
        *,
        table: dict | None = None,
        after_tool_result: dict | None = None,
        env_extra: dict | None = None,
        workspace_files: dict[str, str] | None = None,
    ):
        self.registered, self.certified = registered, certified
        self.home = Path(tempfile.mkdtemp(prefix="authorship-served-"))
        env = dict(env_extra or {})
        if workspace_files:
            workspace = self.home / "ws"
            workspace.mkdir(parents=True, exist_ok=True)
            for name, body in workspace_files.items():
                (workspace / name).write_text(body, encoding="utf-8")
            env["VOOL_WORKSPACE_ROOT"] = str(workspace)
        self.provider = ScriptedProvider(
            table or {UNCERTIFIED: UNCERTIFIED_ANSWER, CERTIFIED: CERTIFIED_ANSWER},
            after_tool_result=after_tool_result,
            ambiguity_verdict={"ambiguous": False, "referents": [], "clarification": ""},
        )
        self.daemon = ServedDaemon(self.home, env_extra=env)

    def __enter__(self) -> _Served:
        self.provider.__enter__()
        try:
            self.daemon.start(timeout=240)
            seed_in_home(
                self.home,
                _SEED.format(
                    root=REPO_ROOT,
                    base_url=self.provider.base_url,
                    registered=list(self.registered),
                    certified=list(self.certified),
                ),
            )
            self.provider.reset()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.daemon.stop()
        self.provider.__exit__(*exc)

    def events(self, *kinds: str) -> list[tuple[str, str]]:
        conn = sqlite3.connect(str(self.home / "data" / "vool_web0_v2.db"))
        try:
            rows = conn.execute(
                "SELECT event_type, COALESCE(details_json, '') FROM runtime_session_events "
                "ORDER BY rowid DESC LIMIT 400"
            ).fetchall()
        finally:
            conn.close()
        return [(row[0], row[1]) for row in rows if not kinds or row[0] in kinds]


def test_served_uncertified_author_never_gets_its_generation_and_the_wire_says_why():
    """S1. One uncertified local model and nothing else. The wire carries the typed authorship
    refusal, and the endpoint confirms it was never asked to generate."""

    with _Served(registered=(UNCERTIFIED,), certified=()) as served:
        payload = served.daemon.chat(DIRECT_REQUEST, session_id="served-s1")
        text = _reply_text(payload)

    assert served.provider.generations_for(UNCERTIFIED) == 0, (
        f"the prohibited generation was spent: {served.provider.calls}"
    )
    assert "never false negatives" not in text, text[:400]
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD in text, text[:400]


def test_served_escalation_calls_the_certified_author_and_not_the_uncertified_one():
    """S2. Both registered, one certified. The certified model writes; the uncertified one is
    refused at the seam and never generates. The escalation happens before the spend."""

    with _Served(registered=(UNCERTIFIED, CERTIFIED), certified=(CERTIFIED,)) as served:
        payload = served.daemon.chat(DIRECT_REQUEST, session_id="served-s2")
        text = _reply_text(payload)

    assert served.provider.generations_for(UNCERTIFIED) == 0, (
        f"the uncertified author was still generated from: {served.provider.calls}"
    )
    assert served.provider.generations_for(CERTIFIED) >= 1, served.provider.calls
    assert "never inserted" in text, text[:400]
    fenced = [
        details
        for _kind, details in served.events("model_lane_failed")
        if "author_not_certified_for_final_answer" in details
    ]
    assert fenced, "the wire has no record of the fence refusing the uncertified candidate"


def test_served_eligible_author_publishes_unchanged():
    """S3. A certified author's bytes reach the wire untouched."""

    with _Served(registered=(CERTIFIED,), certified=(CERTIFIED,)) as served:
        payload = served.daemon.chat(DIRECT_REQUEST, session_id="served-s3")
        text = _reply_text(payload)

    assert served.provider.generations_for(CERTIFIED) >= 1, served.provider.calls
    assert "never inserted" in text, text[:400]
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in text, text[:400]


def test_served_deterministic_answer_is_untouched_and_spends_nothing():
    """S4. The boundary's jurisdiction, from the outside: a turn the runtime answers itself
    reaches the wire unchanged and never touches a provider."""

    with _Served(registered=(UNCERTIFIED,), certified=()) as served:
        payload = served.daemon.chat("What is 37 * 19?", session_id="served-s4")
        text = _reply_text(payload)

    assert "703" in text, text[:400]
    assert served.provider.generations_for(UNCERTIFIED) == 0, served.provider.calls
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in text, text[:400]


def test_served_execution_truth_names_the_model_that_actually_wrote_the_wire_response():
    """S6. The record and the wire agree. Requested identity, selected identity and the author
    role are all in the turn's own events, and the model named there is the one the endpoint
    was actually asked to generate from."""

    with _Served(registered=(UNCERTIFIED, CERTIFIED), certified=(CERTIFIED,)) as served:
        payload = served.daemon.chat(DIRECT_REQUEST, session_id="served-s6")
        text = _reply_text(payload)
        rows = served.events()

    assert "never inserted" in text, text[:400]
    generated = {call["model"] for call in served.provider.calls if call["prompt"].strip()}
    assert generated == {CERTIFIED}, generated

    started = [details for kind, details in rows if kind == "model.call_completed"]
    assert any(CERTIFIED in details for details in started), (
        "no completed model call names the model that wrote the wire response"
    )
    assert not any(
        UNCERTIFIED in details and "call_completed" in kind
        for kind, details in rows
        if kind == "model.call_completed"
    ), "a model that never generated was recorded as having completed a call"


def test_served_a_real_tool_turn_is_executed_and_published_untouched():
    """S5. A turn where the model CALLS a tool and the runtime renders what the tool returned.

    The certified model emits a native `workspace__read_file` call over the wire, the daemon
    executes it against a real file in a real workspace root, and the bytes the wire returns are
    the file's own contents. The authorship boundary does not touch this: nothing here was
    authored out of a model's weights, which is exactly the carve-out that keeps a local-first
    runtime a working product rather than a switched-off one.
    """

    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "workspace__read_file", "arguments": {"path": "settings.log"}},
    }
    with _Served(
        registered=(CERTIFIED,),
        certified=(CERTIFIED,),
        table={CERTIFIED: tool_call},
        after_tool_result={CERTIFIED: "unused: the runtime renders the tool result itself"},
        env_extra={"VOOL_ALWAYS_ON_CATALOG": "1"},
        workspace_files={"settings.log": "retries = 5\ntimeout_seconds = 30\n"},
    ) as served:
        payload = served.daemon.chat(
            "What retry settings are written inside settings.log?", session_id="served-s5"
        )
        text = _reply_text(payload)
        offered = [call for call in served.provider.calls if call.get("tools")]

    assert offered, "the runtime never offered a tool catalogue; this proves nothing about tools"
    assert "workspace__read_file" in offered[0]["tools"], offered[0]["tools"]
    assert "retries = 5" in text and "timeout_seconds = 30" in text, text[:400]
    from core.final_answer_authorship import UNCERTIFIED_AUTHOR_NOTICE_LEAD

    assert UNCERTIFIED_AUTHOR_NOTICE_LEAD not in text, text[:400]
