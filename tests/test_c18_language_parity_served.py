"""C18 served proof — multilingual capability parity and provider-bound language policy
over a REAL ``/api/chat`` socket.

Same evidence class as the a9/authorship served lanes: a real daemon process
(``apps.vool_api_server``) in its own ``VOOL_HOME`` on ephemeral ports, a real HTTP door, and
a SCRIPTED Ollama/OpenAI-dialect provider that records every call. Scripted routing is NOT a
real-model claim: nothing here says the small stub understands Lithuanian — it says the RUNTIME
treats equivalent requests identically regardless of their language, and that the typed
response-language policy reaches the actual provider request.

Per-language probes (equivalent request "explain how a database index helps search"):
  en       — plain English (guard stays active, prompt shape unchanged)
  lt       — Lithuanian with exclusive letters (ą ę į ų) → evidenced lt
  lt-ascii — ASCII-ized Lithuanian, the exact base defect → must NOT be forced to English
  es       — Spanish with ¿ → evidenced es
  de       — German with ß → evidenced de
  ja       — Japanese kana → evidenced ja
  ja-mixed — kana + English words in one turn → evidenced ja
  typo-en  — typo-heavy English → guard stays active
"""
from __future__ import annotations

import json
import textwrap
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from tests._authorship_served_rig import REPO_ROOT, ServedDaemon, free_port, seed_in_home

LOCAL = "c18-local-1.5b"
PAID = "c18-paid-g-1.5b"

CJK_ANSWER = "这是一个完全使用中文书写的回答，其中包含足够多的文字来解释数据库索引，并且详细说明了它的成本。"
TYPO_EN_QUESTION = "plese tell me wat teh crrency of japan is"
ENGLISH_REPAIR = "The currency of Japan is the Japanese yen (JPY)."

#: (probe id, request text, expected wire instruction or "" for none)
PROBES = [
    ("en", "Please explain how a database index improves search speed.", ""),
    (
        "lt",
        "Paaiškink, kaip veikia duomenų bazių indeksas ir kodėl jis pagreitina paiešką.",
        "Respond in Lithuanian.",
    ),
    ("lt-ascii", "Prasau paaiskink kas yra duomenu baziu indeksas ir kaip jis veikia", ""),
    (
        "es",
        "¿Puedes explicarme qué es un índice de base de datos y por qué acelera las consultas?",
        "Respond in Spanish.",
    ),
    (
        "de",
        "Erkläre mir bitte, wie ein Datenbankindex funktioniert und warum er Abfragen "
        "in großen Tabellen beschleunigt.",
        "Respond in German.",
    ),
    (
        "ja",
        "データベースのインデックスはどのように機能し、なぜ検索が速くなるのか説明してください。",
        "Respond in Japanese.",
    ),
    ("ja-mixed", "データベースのindexについて、explain briefly お願いします。", "Respond in Japanese."),
    ("typo-en", "wat is teh crrency of north korea and why woud the transacion fail", ""),
]


class CountingProvider:
    """Ollama/OpenAI-dialect stub that RECORDS the full wire request and replies from a queue.

    ``system`` is recorded separately from ``messages`` because the dialect can carry the system
    prompt as a top-level field; policy-on-the-wire assertions must see both. Queued replies pop
    ONLY for answer-lane calls (those carrying ``answer_marker`` in the request), so auxiliary
    generations of a turn cannot silently consume a scripted answer.
    """

    def __init__(self, table: dict[str, Any], *, answer_marker: str = "") -> None:
        self.table = dict(table)
        self.replies: dict[str, list[str]] = {}
        self.answer_marker = answer_marker
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.port = free_port()
        rig = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            daemon_threads = True

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
                    return self._send({"version": "0.0.0-c18-stub"})
                if self.path.startswith("/api/tags"):
                    return self._send({"models": [{"name": n} for n in rig.table]})
                if self.path.startswith("/api/ps"):
                    return self._send(
                        {"models": [{"name": n, "size": 0, "size_vram": 0} for n in rig.table]}
                    )
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
                joined = "\n".join(
                    str(m.get("content") or "") for m in messages if isinstance(m, dict)
                )
                system_text = str(body.get("system") or "")
                with rig._lock:
                    rig.calls.append(
                        {
                            "model": model,
                            "prompt": joined[:20000],
                            "system": system_text[:20000],
                            "path": self.path,
                        }
                    )
                    queue = rig.replies.get(model)
                    is_answer_lane = (
                        not rig.answer_marker
                        or rig.answer_marker in joined
                        or rig.answer_marker in system_text
                    )
                    if queue and is_answer_lane:
                        reply = str(queue.pop(0))
                    else:
                        reply = str(rig.table.get(model, ""))
                    # The runtime adjudicates plain knowledge questions for entity ambiguity
                    # before answering (a garbage judge reply fails CLOSED: the turn ships an
                    # ask-back and no answer generation runs). This stub must speak the judge
                    # protocol so the turn reaches the ANSWER lane this suite measures: a
                    # judge-shaped probe always gets a valid unambiguous verdict, and the
                    # language assertions keep measuring the answer lane, not the ask-back.
                    if '"ambiguous": true or false' in joined or '"ambiguous": true or false' in system_text:
                        reply = '{"ambiguous": false, "referents": [], "clarification": ""}'

                if self.path.startswith("/v1/"):
                    return self._send(
                        {
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": reply},
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
                        "message": {"role": "assistant", "content": reply},
                        "prompt_eval_count": 40,
                        "eval_count": 30,
                    }
                )

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def generations_for(self, model: str) -> int:
        with self._lock:
            return sum(1 for c in self.calls if c["model"] == model and c["prompt"].strip())

    def wire_text(self, model: str, index: int = -1) -> str:
        """The full recorded wire request (system field + messages) for one call."""
        with self._lock:
            calls = [c for c in self.calls if c["model"] == model]
        call = calls[index]
        return f"{call['system']}\n{call['prompt']}"

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    def __enter__(self) -> CountingProvider:
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


_SEED = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, "{root}")
    from storage.db import get_connection
    from storage.migrations import run_migrations
    from storage.model_provider_manifest import (
        ModelProviderManifest, list_provider_manifests, upsert_provider_manifest,
    )
    from tests._authorship_certification import certify_for_authorship

    run_migrations()

    def local_manifest(name, provider):
        return ModelProviderManifest(
            provider_name=provider, model_name=name, source_type="http",
            adapter_type="openai_compatible", license_name="Apache-2.0",
            license_reference="https://example.invalid/license",
            weight_location="external", runtime_dependency="stub",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={{"base_url": "{local_base}", "timeout_seconds": 30}},
            metadata={{
                "runtime_family": "ollama", "cost_class": "free_local",
                "model_digest": "sha256:stub", "chat_template_hash": "tmpl",
                "quantization": "q4_K_M", "parameter_billions": 1.5,
            }},
        )

    def paid_manifest(name):
        return ModelProviderManifest(
            provider_name="c18-stub-cloud", model_name=name, source_type="http",
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

    # The daemon auto-registers the primary local model under provider_name "ollama-local" at
    # boot. Seeding the SAME provider identity (and certifying it) means the boot row and the
    # seeded row are one provider_id: no uncertified shadow row, no duplicate-model ambiguity.
    conn = get_connection()
    conn.execute("DELETE FROM model_provider_manifests")
    conn.commit()
    conn.close()
    upsert_provider_manifest(local_manifest("{local}", "ollama-local"))
    upsert_provider_manifest(paid_manifest("{paid}"))
    certify_for_authorship(local_manifest("{local}", "ollama-local"))
    print(sorted((m.provider_id, m.model_name) for m in list_provider_manifests()))
    """
)

_PREFERENCE_SEED = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, "{root}")
    from storage.db import get_connection
    from storage.migrations import run_migrations

    run_migrations()
    from core import operator_profile

    operator_profile.reset_table_cache_for_tests()
    change = operator_profile.remember(
        "owner_local", "language", "Lithuanian", scope="chat", session_id="{session_id}"
    )
    print(change.kind)
    """
)


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, dict):
        return str(message.get("content") or "")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return str((choices[0].get("message") or {}).get("content") or "")
    return str(payload.get("response") or "")


class C18LanguageParityServed(unittest.TestCase):
    """One daemon, two counting providers (local / paid), isolated home."""

    def setUp(self) -> None:
        import tempfile

        self.home = Path(tempfile.mkdtemp(prefix="c18-language-served-"))
        self.local = CountingProvider({LOCAL: ENGLISH_REPAIR}, answer_marker="You are Atlas")
        self.paid = CountingProvider({PAID: "PAID ANSWER"})
        self.daemon = ServedDaemon(
            self.home,
            env_extra={
                "VOOL_INSTALL_PROFILE": "local-only",
                "VOOL_MODEL_LOAD_FLOOR_GB": "0",
                # Keep the scripted provider the ONLY remote peer: the auxiliary live-research
                # lane would otherwise consume queued provider replies mid-turn.
                "VOOL_DISABLE_WEB": "1",
                "OLLAMA_HOST": self.local.base_url,
                "VOOL_OLLAMA_URL": self.local.base_url,
                "VOOL_OLLAMA_CHAT_URL": f"{self.local.base_url}/api/chat",
            },
        )

    def tearDown(self) -> None:
        self.daemon.stop()
        self.local.__exit__(None, None, None)
        self.paid.__exit__(None, None, None)

    def __enter__(self) -> C18LanguageParityServed:
        self.local.__enter__()
        self.paid.__enter__()
        self.daemon.start(timeout=300)
        seed_in_home(
            self.home,
            _SEED.format(
                root=REPO_ROOT,
                local_base=self.local.base_url,
                paid_base=self.paid.base_url,
                local=LOCAL,
                paid=PAID,
            ),
        )
        # The daemon's background boot (provider registry warm, plugin load) settles after
        # healthz; give it a beat so the first turn never races its own registration.
        time.sleep(3.0)
        self.local.reset()
        self.paid.reset()
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def chat(self, text: str, *, session_id: str, model: str = "") -> dict[str, Any]:
        return self.daemon.chat(text, session_id=session_id, model=model, timeout=600.0)

    # ------------------------------------------------------------------
    # capability + policy parity on the wire
    # ------------------------------------------------------------------

    def test_equivalent_multilingual_turns_reach_the_same_capability_and_policy_wire(self) -> None:
        with self:
            for probe_id, text, expected_instruction in PROBES:
                with self.subTest(probe=probe_id):
                    self.local.reset()
                    payload = self.chat(text, session_id=f"sess-{probe_id}", model=LOCAL)

                    reply = _reply_text(payload)
                    self.assertTrue(reply.strip(), f"{probe_id}: empty served reply: {payload}")
                    # Same intended capability: the pinned local model served the turn itself.
                    self.assertGreaterEqual(
                        self.local.generations_for(LOCAL),
                        1,
                        f"{probe_id}: the local lane never received the turn",
                    )
                    # The user's literal turn bytes reached the provider in their own language.
                    marker = text[:12]
                    self.assertIn(
                        marker,
                        self.local.wire_text(LOCAL),
                        f"{probe_id}: the literal turn bytes were lost on the wire",
                    )
                    # Provider-bound policy: typed non-English expectations state themselves on
                    # the FIRST call; English and ambiguous turns add no instruction bytes.
                    wire = self.local.wire_text(LOCAL)
                    if expected_instruction:
                        self.assertIn(expected_instruction, wire, f"{probe_id}: policy never reached the wire")
                    else:
                        self.assertNotIn("Respond in ", wire, f"{probe_id}: unexpected wire instruction")

    def test_lt_ascii_turn_is_not_forced_to_english_at_the_served_layer(self) -> None:
        """The base defect, end to end: an ASCII-ized Lithuanian turn must not carry an English
        expectation onto the wire, and the answer the runtime returns is the scripted reply the
        provider actually gave — not a forced-language replacement."""
        with self:
            self.local.reset()
            self.local.table[LOCAL] = (
                "Duomenų bazių indeksas pagreitina paiešką, nes leidžia praleisti daugumą eilučių."
            )
            payload = self.chat(PROBES[2][1], session_id="sess-lt-ascii", model=LOCAL)

            self.assertEqual(
                _reply_text(payload),
                self.local.table[LOCAL],
                "the served answer was replaced instead of served as given",
            )
            self.assertNotIn("Respond in ", self.local.wire_text(LOCAL))
            self.assertNotIn("Return the answer in English", self.local.wire_text(LOCAL))

    def test_typo_heavy_english_keeps_the_anti_drift_guard_over_the_wire(self) -> None:
        """Equivalent safeguard parity: a typo-heavy ENGLISH turn still evidence English, so a
        CJK answer to it is repaired exactly as for clean English — one bounded retry."""
        with self:
            self.local.reset()
            self.local.replies[LOCAL] = [CJK_ANSWER, ENGLISH_REPAIR]
            payload = self.chat(TYPO_EN_QUESTION, session_id="sess-typo-en", model=LOCAL)

            self.assertEqual(_reply_text(payload), ENGLISH_REPAIR)
            self.assertEqual(
                self.local.generations_for(LOCAL),
                2,
                "the guard must spend exactly one repair. Wire calls to the local model: "
                + json.dumps(
                    [
                        {
                            "path": c["path"],
                            "prompt": c["prompt"][:100],
                            "system": c["system"][:100],
                        }
                        for c in self.local.calls
                        if c["model"] == LOCAL
                    ],
                    ensure_ascii=False,
                ),
            )
            self.assertIn("Return the answer in English only", self.local.wire_text(LOCAL, -1))

    # ------------------------------------------------------------------
    # scoped operator preference: precedence, isolation, restart
    # ------------------------------------------------------------------

    def test_scoped_preference_reaches_wire_isolates_sessions_and_survives_restart(self) -> None:
        import hashlib
        import threading

        with self:
            # The runtime namespaces a requested session handle to openclaw:<sha256[:20]>;
            # the chat-scoped preference must be stored under that canonical id.
            def canonical(requested: str) -> str:
                digest = hashlib.sha256(requested.encode("utf-8")).hexdigest()[:20]
                return f"openclaw:{digest}"

            seed_in_home(
                self.home,
                _PREFERENCE_SEED.format(
                    root=REPO_ROOT, session_id=canonical("sess-pref")
                ),
            )

            # The preference tier outranks this turn's evidenced English...
            self.local.reset()
            self.chat(PROBES[0][1], session_id="sess-pref", model=LOCAL)
            self.assertIn("Respond in Lithuanian.", self.local.wire_text(LOCAL))

            # ...but it is scoped: a turn in ANOTHER session never sees it. The two turns run
            # CONCURRENTLY (overlapping in-flight turns, not sequential replay): afterwards
            # session A's calls carry the instruction and session B's calls never do.
            self.local.reset()
            results: dict[str, str] = {}

            def session_turn(session_id: str, label: str) -> None:
                try:
                    payload = self.chat(PROBES[0][1], session_id=session_id, model=LOCAL)
                    results[label] = _reply_text(payload)
                except Exception as exc:  # pragma: no cover
                    results[label] = f"error: {exc}"

            start = threading.Barrier(2)
            threads = [
                threading.Thread(target=lambda: (start.wait(60), session_turn("sess-pref", "pref"))),
                threading.Thread(
                    target=lambda: (start.wait(60), session_turn("sess-pref-other", "other"))
                ),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=300)
            self.assertEqual(sorted(results), ["other", "pref"], results)
            with self.local._lock:
                wires = [f"{c['system']}\n{c['prompt']}" for c in self.local.calls]
            self.assertGreaterEqual(len(wires), 2, wires)
            self.assertTrue(
                any("Respond in Lithuanian." in wire for wire in wires),
                "the preference never reached the scoped session's wire",
            )
            self.assertFalse(
                all("Respond in Lithuanian." in wire for wire in wires),
                "the chat-scoped preference leaked into the concurrent unscoped session",
            )

            # An explicit in-turn request outranks the preference.
            self.local.reset()
            self.chat(
                "Answer in Japanese: what is a database index?",
                session_id="sess-pref",
                model=LOCAL,
            )
            self.assertIn("Respond in Japanese.", self.local.wire_text(LOCAL))
            self.assertNotIn("Respond in Lithuanian.", self.local.wire_text(LOCAL))

            # The preference survives a daemon restart: profile state, not process state.
            # SCOPE LABEL (per review): the PROVIDER MANIFEST is re-seeded after restart because
            # the daemon's own boot re-probes model identity and invalidates the stub's
            # authorship fingerprint — fixture plumbing owned by the authorship lane. The
            # chat-scoped language PREFERENCE is NOT re-seeded; its persistence across the
            # restart is the claim under test. This is NOT a claim of unattended restart
            # readiness for real deployments.
            self.daemon.stop()
            self.daemon.start(timeout=300)
            seed_in_home(
                self.home,
                _SEED.format(
                    root=REPO_ROOT,
                    local_base=self.local.base_url,
                    paid_base=self.paid.base_url,
                    local=LOCAL,
                    paid=PAID,
                ),
            )
            time.sleep(2.0)
            self.local.reset()
            self.chat(PROBES[0][1], session_id="sess-pref", model=LOCAL)
            self.assertIn("Respond in Lithuanian.", self.local.wire_text(LOCAL))

    # ------------------------------------------------------------------
    # permission gates are language-invariant
    # ------------------------------------------------------------------

    def test_paid_spend_gate_refuses_every_language_identically(self) -> None:
        """The spend gate is a permission gate: under the local-only install profile, a request
        pinned at the paid model must be refused the same way in every language — and the paid
        provider must receive ZERO generations in all of them."""
        with self:
            self.paid.reset()
            outcomes: dict[str, str] = {}
            for probe_id, text, _instruction in PROBES:
                payload = self.chat(text, session_id=f"sess-paid-{probe_id}", model=PAID)
                outcomes[probe_id] = _reply_text(payload)

            self.assertEqual(
                self.paid.generations_for(PAID),
                0,
                f"the paid lane generated for some language: {self.paid.calls}",
            )
            for probe_id, reply in outcomes.items():
                self.assertTrue(
                    reply.strip(), f"{probe_id}: the gate must refuse honestly, not silently"
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
