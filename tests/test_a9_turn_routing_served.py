"""A9 P0 — ONE turn-scoped routing authority, proven over a REAL socket.

Every test here drives `apps.vool_api_server` in its own process, its own VOOL_HOME,
against SCRIPTED providers that COUNT what they were asked. The provider-side call
count is the load-bearing assertion throughout: "the prohibited candidate received
zero generation calls" is only a claim until the endpoint on the other side of the
socket confirms nothing arrived.

RED at base: `core.turn_routing` does not exist, so collection of the first helper
import fails; the behaviors below (typed failure explanations, retry generations,
restart-surviving rules) are absent from the served surface.
"""
from __future__ import annotations

import json
import socket
import subprocess
import textwrap
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tests._authorship_served_rig import REPO_ROOT, ServedDaemon, free_port, seed_in_home

FREE_A = "a9-free-a-1.5b"
FREE_B = "a9-free-b-1.5b"
PAID = "a9-paid-g-1.5b"
REMOTE_MID = "a9-remote-d-1.5b"

LOCAL_TABLE = {
    FREE_A: "LOCAL-A ANSWER: the pinned local model served this turn. This is the second sentence the format gate needs.",
    FREE_B: "LOCAL-B ANSWER: the alternate local model served this turn. This is its second sentence.",
}
REMOTE_TABLE = {REMOTE_MID: "REMOTE-MID ANSWER: the remote lane served this turn. This is its second sentence."}

ORIGINAL_QUESTION = "Explain in two sentences how a Bloom filter can report a false positive."


def _lan_ip() -> str:
    import subprocess as sp

    for interface in ("en0", "en1", "en2", "en3"):
        try:
            out = sp.run(["ipconfig", "getifaddr", interface], capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        candidate = out.stdout.strip()
        if candidate and not candidate.startswith("127."):
            return candidate
    return ""


class CountingProvider:
    """An Ollama-dialect scripted provider that counts prompts and can hang per model.

    Same contract as `tests._authorship_served_rig.ScriptedProvider`, plus:
    * full-prompt capture (context-preservation proofs need more than 400 chars);
    * `hang_models`: models whose /api/chat sleeps past the caller's deadline, which is
      how a provider TIMEOUT is injected from the far side of a real socket.
    """

    def __init__(self, table: dict[str, str], *, hang_models: set[str] | None = None,
                 bind_host: str = "127.0.0.1", advertise_host: str | None = None) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.table = dict(table)
        self.hang_models = set(hang_models or ())
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        self.advertise_host = advertise_host or bind_host
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args) -> None:
                return

            def _send(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/api/version"):
                    return self._send({"version": "0.0.0-stub"})
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": name} for name in rig.table]})
                if self.path.startswith("/api/ps"):
                    return self._send({"models": []})
                return self._send({"ok": True})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                model = str(body.get("model") or "")
                messages = body.get("messages") or []
                prompt = " ".join(
                    str(m.get("content") or "") for m in messages if isinstance(m, dict)
                )
                with rig._lock:
                    rig.calls.append({"model": model, "prompt": prompt[:16000], "path": self.path})
                from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT
                ambiguity_check = AMBIGUITY_SYSTEM_PROMPT in prompt
                # Hang the final-answer lane this journey measures; a malformed
                # preflight verdict tests a different failure and never reaches it.
                if model in rig.hang_models and not ambiguity_check:
                    time.sleep(45)
                    return self._send({"model": model, "done": True, "done_reason": "stop",
                                       "message": {"role": "assistant", "content": ""}})
                text = rig.table.get(model, "")
                # The routing journey must answer the existing structured ambiguity
                # request before its ordinary prose can exercise the selected lane.
                if ambiguity_check:
                    text = json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
                if self.path.startswith("/v1/"):
                    return self._send(
                        {
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": text},
                                }
                            ],
                            "usage": {"prompt_tokens": 40, "completion_tokens": 30},
                        }
                    )
                return self._send(
                    {
                        "model": model,
                        "done": True,
                        "done_reason": "stop",
                        "message": {"role": "assistant", "content": text},
                        "prompt_eval_count": 40,
                        "eval_count": 30,
                    }
                )

        # daemon_threads: a handler parked on a keep-alive connection must never block
        # server_close() at teardown -- that join-forever is a 40-minute wedge measured live.
        self._server = ThreadingHTTPServer((bind_host, self.port), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{self.advertise_host}:{self.port}"

    def generations_for(self, model: str) -> int:
        with self._lock:
            return sum(
                1 for call in self.calls
                if call["model"] == model and str(call["prompt"] or "").strip()
            )

    def prompts_for(self, model: str) -> list[str]:
        with self._lock:
            return [call["prompt"] for call in self.calls if call["model"] == model]

    def __enter__(self) -> CountingProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


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

    def local_manifest(name):
        return ModelProviderManifest(
            provider_name="stub-local", model_name=name, source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://example.invalid/license",
            weight_location="external", runtime_dependency="stub",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{local_base}", "timeout_seconds": {timeout}}},
            metadata={{
                "runtime_family": "ollama", "cost_class": "free_local",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 1.5,
            }},
        )

    def paid_manifest(name):
        return ModelProviderManifest(
            provider_name="stub-cloud", model_name=name, source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://example.invalid/license",
            weight_location="external", runtime_dependency="stub",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{paid_base}", "timeout_seconds": 20}},
            metadata={{
                "runtime_family": "openai", "cost_class": "paid_cloud",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 1.5,
                "deployment_class": "cloud",
            }},
        )

    def remote_manifest(name):
        return ModelProviderManifest(
            provider_name="stub-remote", model_name=name, source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://example.invalid/license",
            weight_location="external", runtime_dependency="stub",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{remote_base}", "timeout_seconds": 20}},
            metadata={{
                "runtime_family": "openai", "cost_class": "remote_unknown",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 1.5,
                "deployment_class": "cloud",
            }},
        )

    conn = get_connection()
    conn.execute("DELETE FROM model_provider_manifests")
    conn.commit()
    conn.close()
    upsert_provider_manifest(local_manifest("{free_a}"))
    upsert_provider_manifest(local_manifest("{free_b}"))
    upsert_provider_manifest(paid_manifest("{paid}"))
    if "{remote_base}":
        upsert_provider_manifest(remote_manifest("{remote_mid}"))
    for build in (local_manifest,):
        for name in ("{free_a}", "{free_b}"):
            certify_for_authorship(build(name))
    if "{remote_base}":
        certify_for_authorship(remote_manifest("{remote_mid}"))
    print(sorted((m.provider_id, m.model_name) for m in list_provider_manifests()))
    '''
)

_RULE_SEED = textwrap.dedent(
    '''
    import sys
    sys.path.insert(0, "{root}")
    from core.turn_routing import arm_rule

    rule = arm_rule(
        scope_kind="chat",
        scope_id="{chat_id}",
        directive="PROVIDER_ONLY",
        target="stub-remote/{remote_mid}",
        turns_remaining={turns},
    )
    print(rule.rule_id)
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


class _RoutingServed:
    """One daemon, three counting providers (local / paid / remote), isolated home."""

    def __init__(self, *, profile: str = "local-only", remote: bool = False, timeout: int = 4):
        import tempfile

        self.home = Path(tempfile.mkdtemp(prefix="a9-routing-served-"))
        self.local = CountingProvider(LOCAL_TABLE)
        self.paid = CountingProvider({PAID: "PAID ANSWER"})
        lan = _lan_ip()
        if remote and not lan:
            raise unittest.SkipTest("no non-loopback interface for the remote stub")
        # The remote stub binds the LAN interface directly so the runtime classifies it as
        # a genuine REMOTE provider (loopback base_urls are local by contract). Constructed
        # once — calling __exit__ on a never-started server here would block forever in
        # shutdown() waiting for a serve loop that does not exist.
        self.remote = (
            CountingProvider(REMOTE_TABLE, bind_host="0.0.0.0", advertise_host=lan)
            if remote
            else None
        )
        env = {"VOOL_INSTALL_PROFILE": profile}
        self.daemon = ServedDaemon(self.home, env_extra=env)
        self._timeout = timeout

    def __enter__(self) -> _RoutingServed:
        self.local.__enter__()
        self.paid.__enter__()
        if self.remote is not None:
            self.remote.__enter__()
        try:
            self.daemon.start(timeout=300)
            seed_in_home(
                self.home,
                _SEED.format(
                    root=REPO_ROOT,
                    local_base=self.local.base_url,
                    paid_base=self.paid.base_url,
                    remote_base=(self.remote.base_url if self.remote else ""),
                    timeout=self._timeout,
                    free_a=FREE_A,
                    free_b=FREE_B,
                    paid=PAID,
                    remote_mid=REMOTE_MID,
                ),
            )
            self.local.calls.clear()
            self.paid.calls.clear()
            if self.remote is not None:
                self.remote.calls.clear()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.daemon.stop()
        self.local.__exit__(*exc)
        self.paid.__exit__(*exc)
        if self.remote is not None:
            self.remote.__exit__(*exc)

    def chat(self, message: str, *, session_id: str, model: str = "", chat_id: str = "",
             timeout: float = 240.0) -> str:
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": message}],
            "stream": False,
            "session_id": session_id,
        }
        if model:
            payload["model"] = model
        if chat_id:
            payload["chat_id"] = chat_id
        # ONE bounded retry on a 5xx, and only while the answer never came: the first
        # concurrent chat right after boot intermittently 500s in ~190ms with no traceback
        # (measured at BASE, a daemon boot race outside this lane's ownership — recorded in
        # the lane report). A 5xx that persists still fails the test; this only stops the
        # boot race from masquerading as a routing verdict.
        last_error: Exception | None = None
        for attempt in range(2):
            request = Request(
                f"{self.daemon.base_url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    return _reply_text(json.loads(response.read().decode("utf-8")))
            except HTTPError as exc:
                if exc.code >= 500 and attempt == 0:
                    last_error = exc
                    time.sleep(2.0)
                    continue
                raise
            except URLError as exc:
                if attempt == 0:
                    last_error = exc
                    time.sleep(2.0)
                    continue
                raise
        raise last_error or RuntimeError("chat retries exhausted")

    def routing_events(self) -> list[dict[str, Any]]:
        path = self.home / "data" / "turn_routing_events.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text("utf-8", "replace").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def restart(self) -> None:
        self.daemon.stop()
        self.daemon.home = self.home
        self.daemon.log_path = self.home / "daemon.log"
        self.daemon.start(timeout=300)
        self.local.calls.clear()
        self.paid.calls.clear()
        if self.remote is not None:
            self.remote.calls.clear()


def _chat_async(served: _RoutingServed, message: str, session_id: str, model: str,
                results: dict, key: str) -> None:
    try:
        results[key] = served.chat(message, session_id=session_id, model=model)
    except Exception as exc:  # noqa: BLE001 — the assertion names the failure
        results[key] = f"<error: {exc}>"


class TestServedRoutingScope(unittest.TestCase):
    def test_concurrent_pinned_sessions_stay_scoped_and_paid_never_generates(self) -> None:
        """Two chats, two different explicit pins, fired CONCURRENTLY. Each session is served by
        its own pinned model; the paid provider receives ZERO generation calls the whole time."""
        with _RoutingServed() as served:
            results: dict[str, str] = {}
            threads = [
                threading.Thread(
                    target=_chat_async,
                    args=(served, ORIGINAL_QUESTION, "sess-a", FREE_A, results, "a"),
                ),
                threading.Thread(
                    target=_chat_async,
                    args=(served, "Summarize the tradeoffs of append-only logs.", "sess-b", FREE_B, results, "b"),
                ),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=240)

            self.assertIn("LOCAL-A ANSWER", results.get("a", ""), results)
            self.assertIn("LOCAL-B ANSWER", results.get("b", ""), results)
            self.assertGreaterEqual(served.local.generations_for(FREE_A), 1)
            self.assertGreaterEqual(served.local.generations_for(FREE_B), 1)
            self.assertEqual(
                served.paid.generations_for(PAID), 0,
                f"prohibited paid candidate was called: {served.paid.calls}",
            )

            plans = [row for row in served.routing_events() if row.get("event") == "plan"]
            self.assertEqual(len({row["turn_id"] for row in plans}), 2, plans)
            # The runtime namespaces session ids ("openclaw:<uuid>"), so the assertion keys
            # on the PIN each session sent, not on the raw session string: each concurrent
            # chat's plan is pinned to exactly the model that chat asked for, under two
            # DISTINCT sessions — one plan per turn, no shared scope.
            by_pin = {row["requested_model"]: row for row in plans}
            self.assertEqual(set(by_pin), {FREE_A, FREE_B}, plans)
            self.assertEqual(by_pin[FREE_A]["selected_model"], FREE_A, plans)
            self.assertEqual(by_pin[FREE_B]["selected_model"], FREE_B, plans)
            self.assertEqual(by_pin[FREE_A]["fallback_ladder"], [f"stub-local:{FREE_A}"], plans)
            self.assertEqual(by_pin[FREE_B]["fallback_ladder"], [f"stub-local:{FREE_B}"], plans)
            self.assertEqual(len({row["session_id"] for row in plans}), 2, plans)


class TestServedFailureLifecycle(unittest.TestCase):
    def test_fail_then_why_then_retry_over_the_wire(self) -> None:
        """One session: the pinned model times out; 'why did that fail?' resolves the exact typed
        failed execution; 'retry that exact request' replays the ORIGINAL question as a new
        generation linked to the original plan — and the paid lane is never touched."""
        with _RoutingServed(timeout=3) as served:
            served.local.hang_models = {FREE_A, FREE_B}
            failure_reply = served.chat(ORIGINAL_QUESTION, session_id="sess-f", model=FREE_A)
            self.assertTrue(failure_reply, "the failed turn must still answer honestly")

            served.local.hang_models = set()
            served.local.calls.clear()

            why_reply = served.chat("why did that fail?", session_id="sess-f")
            self.assertIn("stub-local", why_reply, why_reply[:400])
            self.assertIn(FREE_A, why_reply, why_reply[:400])
            self.assertIn("timed out", why_reply.lower(), why_reply[:400])
            # The explanation is deterministic: no generation was spent producing it.
            self.assertEqual(served.local.generations_for(FREE_A), 0, served.local.calls)
            self.assertEqual(served.paid.generations_for(PAID), 0)

            retry_reply = served.chat("retry that exact request", session_id="sess-f")
            self.assertIn("LOCAL-A ANSWER", retry_reply, retry_reply[:400])
            prompts = served.local.prompts_for(FREE_A)
            self.assertTrue(prompts, "the retry must reach the provider")
            last_prompt = prompts[-1]
            self.assertIn("Bloom filter", last_prompt, f"HEAD={last_prompt[:200]} TAIL={last_prompt[-900:]}")
            self.assertNotIn("retry that exact request", last_prompt, last_prompt[-900:])
            self.assertEqual(served.paid.generations_for(PAID), 0)

            plans = [row for row in served.routing_events() if row.get("event") == "plan"]
            self.assertGreaterEqual(len(plans), 2, plans)
            retried = [row for row in plans if row.get("generation", 1) > 1]
            self.assertTrue(retried, plans)
            self.assertTrue(retried[-1].get("retry_of_plan_id"), plans)
            first = plans[0]  # this home served one session; the first plan is the failed turn's
            self.assertEqual(retried[-1]["retry_of_plan_id"], first["plan_id"], plans)
            self.assertEqual(retried[-1]["generation"], 2, plans)
            # Canonical turn identity is PRESERVED, never reused: the retry generation is
            # its OWN canonical turn, linked to the original request -- and the linkage
            # names the original turn by id, not by merging the two turns into one.
            self.assertNotEqual(retried[-1]["turn_id"], first["turn_id"], plans)
            self.assertEqual(retried[-1]["original_request_id"], first["turn_id"], plans)
            self.assertEqual(retried[-1]["session_id"], first["session_id"], plans)


class TestServedLocalityHops(unittest.TestCase):
    def test_local_cloud_local_preserves_the_chats_context(self) -> None:
        """local → cloud → local inside ONE served chat: the third turn's generation carries the
        first turn's content, proving the hop did not fork the conversation context."""
        with _RoutingServed(profile="hybrid-fallback", remote=True) as served:
            codeword = "PINEAPPLE-HORIZON"
            served.chat(
                f"In one short paragraph, would the codeword {codeword} be a good password? Why or why not?",
                session_id="sess-c",
                model=FREE_A,
                chat_id="chat-c",
            )
            served.chat(
                "What is 2+2? Answer with just the number.",
                session_id="sess-c",
                model=REMOTE_MID,
                chat_id="chat-c",
            )
            third = served.chat(
                "Which codeword did I give you earlier in this conversation?",
                session_id="sess-c",
                model=FREE_A,
                chat_id="chat-c",
            )
            self.assertGreaterEqual(served.remote.generations_for(REMOTE_MID), 1,
                                    "the cloud leg must actually run")
            prompts = served.local.prompts_for(FREE_A)
            self.assertTrue(prompts, "the local legs must actually run")
            self.assertIn(codeword, prompts[-1], "context lost across the locality hops")
            self.assertEqual(served.paid.generations_for(PAID), 0)


class TestServedTemporaryRules(unittest.TestCase):
    def test_provider_only_rule_binds_expires_exactly_and_survives_restart(self) -> None:
        """A chat-scoped PROVIDER_ONLY rule (2 turns) steers routing off the natural local pick,
        still binds after a daemon restart, and is gone after exactly two turns.

        The rule is armed against the RUNTIME conversation id (the id the routing authority
        actually owns): one plain turn discovers it from that turn's plan provenance first."""
        with _RoutingServed(profile="hybrid-fallback", remote=True) as served:
            served.chat(
                "Describe one property of an LSM tree.", session_id="sess-r", chat_id="chat-r"
            )
            plans = [row for row in served.routing_events() if row.get("event") == "plan"]
            self.assertTrue(plans, "the discovery turn must mint a plan")
            runtime_chat_id = plans[-1]["conversation_ref"]
            self.assertTrue(runtime_chat_id, plans)

            seed_in_home(
                served.home,
                _RULE_SEED.format(
                    root=REPO_ROOT, chat_id=runtime_chat_id, remote_mid=REMOTE_MID, turns=2
                ),
            )
            served.remote.calls.clear()
            served.local.calls.clear()

            served.restart()
            second = served.chat(
                "Describe one disadvantage of an append-only log.", session_id="sess-r", chat_id="chat-r"
            )
            self.assertIn("REMOTE-MID ANSWER", second, second[:400])
            self.assertGreaterEqual(served.remote.generations_for(REMOTE_MID), 1,
                                    "the rule must survive the restart and bind turn 2")
            remote_after_two = served.remote.generations_for(REMOTE_MID)

            third = served.chat(
                "Describe one alternative to an append-only log.", session_id="sess-r", chat_id="chat-r"
            )
            self.assertIn("REMOTE-MID ANSWER", third, third[:400])
            self.assertGreaterEqual(served.remote.generations_for(REMOTE_MID), remote_after_two + 1,
                                    "the two-turn budget must cover exactly this turn as well")

            fourth = served.chat(
                "Describe one tradeoff of a B-tree instead.", session_id="sess-r", chat_id="chat-r"
            )
            self.assertIn("LOCAL", fourth, fourth[:400])
            self.assertGreaterEqual(served.local.generations_for(FREE_A) + served.local.generations_for(FREE_B), 1)
            self.assertEqual(
                served.remote.generations_for(REMOTE_MID), remote_after_two + 1,
                "the rule must be inert after its turn budget is spent",
            )
            self.assertEqual(served.paid.generations_for(PAID), 0)


if __name__ == "__main__":
    unittest.main()
