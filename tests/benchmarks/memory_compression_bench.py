#!/usr/bin/env python3
"""Benchmark the context path that the shipped chat runtime actually executes.

This benchmark measures prompt retention before model inference. It traverses
``_history_messages_for_chat`` -> ``canonical_runtime_transcript`` and then builds
the native Ollama payload through ``OpenAICompatibleAdapter``. It deliberately
does not score the removed ``ContextWindow`` prototype.

Retention here is produced by a live local summarizer that samples, so a single run
yields a single draw, not a constant. The run reports a distribution over repeated
samples and the number of samples behind it; quote the spread, not one number.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from adapters.base_adapter import ModelRequest
from adapters.openai_compatible_adapter import OpenAICompatibleAdapter
from core import conversation_summarizer, runtime_paths
from core.context_namespace import ensure_chat_namespace
from core.conversation_summarizer import token_estimate
from core.embedding_service import embedding_backend
from core.memory.entries import resolve_memory_access_policy
from core.prompt_normalizer import _history_messages_for_chat
from core.runtime_provider_defaults import _flat_ollama_context_window, default_runtime_model_tag
from storage.model_provider_manifest import ModelProviderManifest

FACTS = (
    ("The launch reference is Glacier-7749.", "What is the launch reference?", "Glacier-7749"),
    ("The database runs on port 5433.", "What port does the database use?", "5433"),
    ("The preferred implementation language is Rust.", "Which language is preferred?", "Rust"),
    ("The project deadline is 2026-11-01.", "When is the project deadline?", "2026-11-01"),
    ("The deployment region is north-cascade-7.", "Which deployment region is configured?", "north-cascade-7"),
)
SYSTEM_PROMPT = "You are VOOL. Preserve exact user facts and answer from grounded context."
WINDOW_SIZE = 10
CONTEXT_SUMMARY_MARKER = "<context_summary>"
BENCHMARK_CHAT_ID = "memory-compression-benchmark"


def _filler(index: int) -> str:
    return f"Unrelated discussion {index} about deployment hygiene, testing, and documentation."


def build_history(turns: int) -> list[dict[str, str]]:
    """Spread the planted facts evenly across the transcript.

    Planting every fact in the opening turns and appending only filler makes any last-N
    window score zero by construction: the result would describe this function's layout
    rather than what the window retains. Even spacing keeps the fact count and the total
    turn count fixed while letting each layer's score be earned.
    """
    filler_pairs = max(2, turns - 10)
    total_pairs = filler_pairs + len(FACTS)
    slots = {int((index + 0.5) * total_pairs / len(FACTS)): fact for index, fact in enumerate(FACTS)}
    if len(slots) != len(FACTS):
        raise ValueError(f"fact slots collided at turns={turns}: {sorted(slots)}")

    history: list[dict[str, str]] = []
    filler_index = 0
    for pair in range(total_pairs):
        planted = slots.get(pair)
        if planted is not None:
            history.extend(
                ({"role": "user", "content": planted[0]}, {"role": "assistant", "content": "Noted."})
            )
        else:
            history.extend(
                (
                    {"role": "user", "content": _filler(filler_index)},
                    {"role": "assistant", "content": "Acknowledged."},
                )
            )
            filler_index += 1
    return history


@contextmanager
def _isolated_l3_store() -> Iterator[Path]:
    """Point the L3 semantic store at a throwaway runtime home for the run.

    ``inject_retrieved`` opens ``VoolMemory`` at the active runtime home, so an
    unisolated run reads whatever the operator has accumulated and the score becomes a
    property of that machine's history rather than of the code under test.
    """
    with tempfile.TemporaryDirectory(prefix="vool-context-bench-") as tmp:
        home = Path(tmp) / "home"
        (home / "data" / "memory").mkdir(parents=True, exist_ok=True)
        previous_env = os.environ.get("VOOL_HOME")
        previous_override = runtime_paths._VOOL_HOME_OVERRIDE
        os.environ["VOOL_HOME"] = str(home)
        runtime_paths.configure_runtime_home(home)
        try:
            yield home
        finally:
            runtime_paths.configure_runtime_home(previous_override)
            if previous_env is None:
                os.environ.pop("VOOL_HOME", None)
            else:
                os.environ["VOOL_HOME"] = previous_env


def _l3_node_count() -> int:
    from core.context_retrieval import _AGENT_ID
    from core.vool_memory import VoolMemory

    try:
        memory = VoolMemory(agent_id=_AGENT_ID)
    except Exception:
        return -1
    try:
        return int(memory.node_count())
    finally:
        memory.close()


def _adapter() -> OpenAICompatibleAdapter:
    context_window = _flat_ollama_context_window("general")
    manifest = ModelProviderManifest(
        provider_name="ollama-local",
        model_name=default_runtime_model_tag(),
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://ollama.com/library/qwen3",
        license_url_or_reference="https://ollama.com/library/qwen3",
        weight_location="external",
        runtime_dependency="ollama",
        capabilities=["summarize", "long_context"],
        runtime_config={
            "base_url": "http://127.0.0.1:11434",
            "context_window": context_window,
        },
        metadata={"runtime_family": "ollama", "context_window": context_window},
        enabled=True,
    )
    return OpenAICompatibleAdapter(manifest)


def _live_payload(history: list[dict[str, str]], question: str) -> tuple[dict, str]:
    ensure_chat_namespace(
        BENCHMARK_CHAT_ID,
        grant_current_receipts=False,
    )
    access_policy = resolve_memory_access_policy(chat_id=BENCHMARK_CHAT_ID)
    history_messages, source = _history_messages_for_chat(
        {
            "chat_id": BENCHMARK_CHAT_ID,
            "conversation_history": history,
            "_context_access_policy": access_policy,
        },
        runtime_session_id=BENCHMARK_CHAT_ID,
        current_user_text=question,
        prompt_profile="chat_general",
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": message.role, "content": message.content} for message in history_messages)
    messages.append({"role": "user", "content": question})
    request = ModelRequest(
        task_kind="chat",
        prompt=question,
        messages=messages,
        max_output_tokens=128,
        metadata={"num_ctx": _flat_ollama_context_window("general")},
    )
    return _adapter()._build_ollama_payload(request, force_json=False, stream=False), source


def _retained_answers(messages: list[dict[str, str]]) -> int:
    blob = " ".join(str(message.get("content") or "") for message in messages).lower()
    return sum(answer.lower() in blob for _plant, _question, answer in FACTS)


class _SummarizerProbe:
    """Record whether the live summarizer actually ran.

    Without this the benchmark cannot distinguish "compacted and kept every fact" from "never
    compacted at all": ``summarize_messages`` swallows a failed model call and returns an
    extractive fallback, so an unreachable Ollama yields an uncompressed transcript that retains
    everything and scores a perfect result. That is the same unearned number this benchmark
    replaced, so the run has to be able to say the measurement did not happen.
    """

    def __init__(self) -> None:
        self.attempts = 0
        self.successes = 0
        self.model = ""
        self.error = ""
        self.fell_back = False
        self._real_call = None
        self._real_summarize = None

    def __enter__(self) -> _SummarizerProbe:
        self._real_call = conversation_summarizer._call_ollama
        self._real_summarize = conversation_summarizer.summarize_messages

        def _wrapped_call(model: str, messages: list[dict], timeout: int = 60) -> str:
            self.attempts += 1
            self.model = str(model)
            try:
                result = self._real_call(model, messages, timeout=timeout)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
                raise
            self.successes += 1
            return result

        def _wrapped_summarize(messages: list[dict], **kwargs: object) -> str:
            # Watching the summary itself, not just the model call: the summarizer declines to
            # call a model at all when none is big enough to be trusted, so a probe that only
            # wrapped _call_ollama would see no error and read that as a healthy run.
            result = self._real_summarize(messages, **kwargs)
            if conversation_summarizer.summary_is_extractive_fallback(result):
                self.fell_back = True
                self.error = (
                    conversation_summarizer.extractive_fallback_reason(result)
                    or self.error
                    or "summarizer fell back to an extractive excerpt"
                )
            return result

        conversation_summarizer._call_ollama = _wrapped_call
        conversation_summarizer.summarize_messages = _wrapped_summarize
        return self

    def __exit__(self, *_exc: object) -> None:
        conversation_summarizer._call_ollama = self._real_call
        conversation_summarizer.summarize_messages = self._real_summarize

    @property
    def ran(self) -> bool:
        return self.successes > 0 and not self.fell_back


def _spread(values: list[int]) -> dict[str, float | int]:
    """Report the observed range, never a lone point estimate.

    The summarizer samples, so these values differ run to run; collapsing them to one
    number would state a precision the measurement does not have.
    """
    if not values:
        return {"min": 0, "median": 0.0, "max": 0, "samples": 0}
    return {
        "min": min(values),
        "median": round(float(statistics.median(values)), 1),
        "max": max(values),
        "samples": len(values),
    }


def run_benchmark(turns: int = 30, repeats: int = 3) -> dict[str, dict[str, object]]:
    history = build_history(turns)
    raw_messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
    window_messages = [raw_messages[0], *history[-WINDOW_SIZE:]]
    live_peaks: list[int] = []
    live_recall: list[int] = []
    live_sources: set[str] = set()
    live_num_ctx: set[int] = set()
    compressed = False

    with _isolated_l3_store():
        l3_nodes = _l3_node_count()
        backend = embedding_backend()
        with _SummarizerProbe() as probe:
            for _repeat in range(repeats):
                # The summarizer memoizes per anchored range, so without this every repeat
                # replays the first draw and the spread reads as zero -- which would look more
                # precise than the single number the distribution is here to replace.
                conversation_summarizer.reset_summary_cache()
                for _plant, question, _answer in FACTS:
                    payload, source = _live_payload(history, question)
                    payload_messages = list(payload["messages"])
                    live_peaks.append(token_estimate(payload_messages))
                    live_recall.append(_retained_answers(payload_messages))
                    live_sources.add(source)
                    live_num_ctx.add(int(payload["options"]["num_ctx"]))
                    if any(
                        CONTEXT_SUMMARY_MARKER in str(message.get("content") or "")
                        for message in payload_messages
                    ):
                        compressed = True

    return {
        "raw": {
            "kind": "reference_ceiling",
            "note": "the whole transcript, uncompacted -- every planted fact is present by construction",
            "retained_facts": _retained_answers(raw_messages),
            "peak_tokens": token_estimate(raw_messages),
        },
        "sliding_window": {
            "kind": "illustrative_bound",
            "note": (
                f"a naive last-{WINDOW_SIZE}-message window over the same transcript: a floor for "
                "reference, not a competing implementation or a tuned baseline"
            ),
            "retained_facts": _retained_answers(window_messages),
            "peak_tokens": token_estimate(window_messages),
        },
        "live_runtime": {
            "kind": "measured_distribution",
            "retained_facts": _spread(live_recall),
            "peak_tokens": _spread(live_peaks),
            "facts_planted": len(FACTS),
            "repeats": repeats,
            "history_source": ",".join(sorted(live_sources)),
            "num_ctx": min(live_num_ctx) if live_num_ctx else 0,
            # The summarizer runs at the shipped sampling settings (temperature 0.1, no fixed
            # seed), so identical runs draw different retention figures; the spread is the
            # measurement, and a single run cannot stand in for it. The spread is itself a
            # sample: min/median/max shift between invocations at these sample counts, so it
            # bounds the observed draws rather than the true range.
            "sampling": (
                "live (unpinned) -- retention varies between identical runs; this spread is "
                "itself a sample and shifts between invocations, so treat it as the draws "
                "observed here, not as fixed bounds"
            ),
            "l3_store": "isolated-temp-home",
            "l3_nodes_visible": l3_nodes,
            "embedding_backend": backend,
            # Without these the retained_facts figure is unreadable: a perfect score means
            # "kept everything" when the summarizer ran and "compaction never happened" when it
            # did not, and the two are indistinguishable from the count alone.
            "summarizer_ran": probe.ran,
            "summarizer_model": probe.model or "none",
            "compression_fired": compressed,
            "fallback_reason": probe.error or "",
        },
    }


def _render_text(results: dict[str, dict[str, object]]) -> str:
    live = results["live_runtime"]
    retained = live["retained_facts"]
    peak = live["peak_tokens"]
    planted = live["facts_planted"]
    lines = [
        "VOOL live context benchmark",
        "",
        f"  live runtime   retained {retained['min']}-{retained['max']}/{planted} facts "
        f"(median {retained['median']}) over {retained['samples']} samples "
        f"/ {live['repeats']} repeat(s)",
        f"                 peak tokens {peak['min']}-{peak['max']} (median {peak['median']})",
        f"                 summarizer {live['summarizer_model']} ran={live['summarizer_ran']} "
        f"compression_fired={live['compression_fired']}",
        f"                 sampling: {live['sampling']}",
        f"                 L3 store: {live['l3_store']} ({live['l3_nodes_visible']} nodes visible), "
        f"embeddings: {live['embedding_backend']}",
        "",
        f"  reference ceiling ({results['raw']['kind']}): "
        f"{results['raw']['retained_facts']}/{planted} facts, {results['raw']['peak_tokens']} tokens",
        f"    {results['raw']['note']}",
        f"  illustrative bound ({results['sliding_window']['kind']}): "
        f"{results['sliding_window']['retained_facts']}/{planted} facts, "
        f"{results['sliding_window']['peak_tokens']} tokens",
        f"    {results['sliding_window']['note']}",
    ]
    if live["repeats"] < 2:
        lines.extend(
            [
                "",
                "  NOTE: a single repeat cannot show the spread. This figure is one draw from a "
                "sampling\n        summarizer -- re-run with --repeats to see the range before "
                "quoting it.",
            ]
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark VOOL's live context assembly and Ollama payload")
    parser.add_argument("--turns", type=int, default=30)
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Full measurement passes. Retention comes from a sampling summarizer, so one pass "
        "reports a draw rather than a reproducible number (default: 3)",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-degraded",
        action="store_true",
        help="Report the run even when the summarizer did not execute (the retention figure is "
        "then meaningless -- nothing was compacted)",
    )
    args = parser.parse_args()
    if args.turns < 12:
        parser.error("--turns must be at least 12")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    results = run_benchmark(args.turns, repeats=args.repeats)
    rendered = json.dumps(results, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    elif args.json:
        print(rendered)
    else:
        print(_render_text(results))
        print(f"\nResults can be saved with --output (temp root: {tempfile.gettempdir()})")

    live = results["live_runtime"]
    if not live["summarizer_ran"]:
        # A degraded run scores an UNCOMPACTED transcript, which retains every fact and reads as a
        # perfect result. Reporting that as a retention measurement is the exact unearned claim
        # this benchmark exists to replace, so say so and exit non-zero.
        print(
            "\nDEGRADED RUN -- the retention figure above measures nothing.\n"
            f"  The summarizer never executed ({live['fallback_reason'] or 'no model call'}), so no "
            "compaction happened\n  and the transcript was scored verbatim. Start Ollama and re-run.",
            file=sys.stderr,
        )
        if not args.allow_degraded:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
