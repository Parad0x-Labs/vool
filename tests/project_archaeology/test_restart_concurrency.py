"""Project archaeology (C21): RESTART determinism and CONCURRENCY isolation.

The reader is STATELESS over the stores, so two strong properties must hold:

- RESTART: a fresh interpreter process reading the same homes must return the
  same evidence (same object_ids/hashes/results) — the reader is a pure view,
  not a cache. Proven with real subprocesses running the identical query.
- CONCURRENCY: concurrent readers must not interfere, must not mutate the
  journal, must return identical results per query, and must not leak data
  across sessions — even while a writer is appending.

Written FIRST (the module does not exist yet, RED): every use of
``core.project_archaeology`` is a lazy import inside a helper/test, so this
file always COLLECTS and fails at call time only.

Envelope comparison discipline: ``generated_at`` (and any per-item key whose
name contains "generated" or "latency") is volatile by contract; those keys
are popped recursively before two envelopes are compared across processes or
across threads.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.project_archaeology.conftest import (
    NIGHT_WINDOW,
    journal_count,
)

# Repo root: tests/project_archaeology/test_restart_concurrency.py -> parents[2].
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SUBPROCESS_TIMEOUT_SECONDS = 120

# Must mirror tests/project_archaeology/conftest.py::arch_home exactly.
KEY_PASSPHRASE = "arch-lane-test-only"

# Volatile envelope keys: popped (recursively) before any cross-run comparison.
VOLATILE_KEY_SUBSTRINGS = ("generated", "latency")


@pytest.fixture()
def arch_corpus(arch_home, blackbox, runtime_ledger, git_repo):
    """The store-side corpus restart/concurrency need: journal + ledger + git.

    Composed from the working fixtures directly instead of the shared
    ``corpus`` fixture, whose ``memory`` link is unrelated to these
    reader-over-stores properties.
    """
    return {
        **arch_home,
        "blackbox": blackbox,
        "runtime": runtime_ledger,
        "git": git_repo,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _import_pa():
    """Lazy import: the module does not exist yet (RED); failing here keeps collection green."""
    from core import project_archaeology as pa

    return pa


def normalize_envelope(value):
    """Recursively drop volatile keys so two runs of the same query compare equal."""
    if isinstance(value, dict):
        return {
            key: normalize_envelope(item)
            for key, item in value.items()
            if not (
                key == "generated_at"
                or any(fragment in key.lower() for fragment in VOLATILE_KEY_SUBSTRINGS)
            )
        }
    if isinstance(value, list):
        return [normalize_envelope(item) for item in value]
    return value


def freeze(envelope) -> str:
    """Canonical JSON of a normalized envelope: the unit of cross-run equality."""
    return json.dumps(
        normalize_envelope(envelope), sort_keys=True, ensure_ascii=False, default=str
    )


def _call(pa, operation: str, kwargs: dict) -> dict:
    return getattr(pa, operation)(**kwargs)


def _db_path(corpus) -> str:
    """The per-test SQLite home the conftest configured for this corpus."""
    return str(Path(str(corpus["home"])) / "arch.db")


def _reader_env(corpus) -> dict:
    """Child-process env: the same isolated homes the corpus was seeded under."""
    env = dict(os.environ)
    env.update(
        {
            "VOOL_HOME": str(corpus["home"]),
            "VOOL_BLACKBOX_DIR": str(corpus["blackbox_root"]),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": KEY_PASSPHRASE,
            "VOOL_LIQUEFY_LOGS": "0",
        }
    )
    return env


# The fresh-process probe: configure the identical homes/db the parent seeded,
# then run ONE query (params embedded as a JSON literal) and print the envelope.
_SUBPROCESS_READER_TEMPLATE = """
import json
import sys

sys.path.insert(0, __ROOT__)
params = json.loads(__PARAMS__)

from core.runtime_paths import configure_runtime_home
from core.runtime_continuity import configure_runtime_continuity_db_path
from storage.db import configure_default_db_path

configure_runtime_home(params["home"])
configure_default_db_path(params["db_path"])
configure_runtime_continuity_db_path(params["db_path"])

import core.project_archaeology as pa  # the module under test (RED until it lands)

out = getattr(pa, params["op"])(**params["kwargs"])
print(json.dumps(out, sort_keys=True, default=str))
"""


def _reader_subprocess(corpus, operation: str, kwargs: dict) -> dict:
    """Run the IDENTICAL query in a brand-new interpreter; return its envelope."""
    params = {
        "home": str(corpus["home"]),
        "db_path": _db_path(corpus),
        "op": operation,
        "kwargs": kwargs,
    }
    script = _SUBPROCESS_READER_TEMPLATE.replace(
        "__ROOT__", repr(str(PROJECT_ROOT))
    ).replace("__PARAMS__", json.dumps(json.dumps(params)))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT),
        env=_reader_env(corpus),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "fresh-process reader failed "
            f"(rc={completed.returncode}) for {operation} {kwargs}\n"
            f"stderr:\n{completed.stderr[-4000:]}"
        )
    return json.loads(completed.stdout)


def _reader_queries(corpus) -> list[tuple[str, str, dict]]:
    """The four mixed queries cycled over by the concurrency tasks."""
    return [
        ("search_pre_receive", "search", {"text": "pre-receive", "store": "blackbox"}),
        ("locate_eff_report_01", "locate", {"reference": "eff-report-01"}),
        (
            "trace_sess_night",
            "trace",
            {
                "session_id": "sess-night",
                "since": NIGHT_WINDOW[0],
                "until": NIGHT_WINDOW[1],
            },
        ),
        (
            "recovery_eff_config_08",
            "recovery_plan",
            {"reference": "eff-config-08", "workspace_root": str(corpus["git_repo"])},
        ),
    ]


# ---------------------------------------------------------------------------
# RESTART: fresh-process determinism over unchanged stores
# ---------------------------------------------------------------------------


def test_restart_fresh_process_search_returns_identical_envelope(arch_corpus) -> None:
    pa = _import_pa()
    kwargs = {"text": "pre-receive", "store": "blackbox"}
    in_process = _call(pa, "search", kwargs)
    assert in_process["returned"] >= 1
    fresh = _reader_subprocess(arch_corpus, "search", kwargs)
    assert freeze(fresh) == freeze(in_process), "restart changed the evidence"
    assert [i["object_id"] for i in fresh["results"]] == [
        i["object_id"] for i in in_process["results"]
    ]
    assert [i["hash"] for i in fresh["results"]] == [
        i["hash"] for i in in_process["results"]
    ]


def test_restart_fresh_process_locate_returns_identical_envelope(arch_corpus) -> None:
    pa = _import_pa()
    kwargs = {"reference": "eff-push-77"}
    in_process = _call(pa, "locate", kwargs)
    assert in_process["returned"] >= 1
    fresh = _reader_subprocess(arch_corpus, "locate", kwargs)
    assert freeze(fresh) == freeze(in_process), "restart changed the evidence"
    assert [i["object_id"] for i in fresh["results"]] == [
        i["object_id"] for i in in_process["results"]
    ]


def test_restart_reads_live_store_no_stale_cache_across_processes(arch_corpus) -> None:
    pa = _import_pa()
    store = arch_corpus["blackbox"]
    kwargs = {"text": "pre-receive", "store": "blackbox"}
    baseline = _call(pa, "search", kwargs)
    fresh_baseline = _reader_subprocess(arch_corpus, "search", kwargs)
    assert freeze(fresh_baseline) == freeze(baseline)

    appended = store.append(
        {
            "schema": "blackbox_effect_v1",
            "kind": "effect_terminal",
            "effect_id": "eff-restart-99",
            "turn_id": "turn-restart-99",
            "session_id": "sess-restart-99",
            "trace_id": "trace-r99",
            "ts": "2026-09-03T23:50:00+00:00",
            "root": "/ws",
            "path": "reports/night-report.md",
            "operation": "push",
            "outcome": "failed",
            "status": "remote_rejected",
            "error": "pre-receive hook declined: restart probe eff-restart-99",
        }
    )
    assert appended.get("effect_id") == "eff-restart-99"

    fresh_after = _reader_subprocess(arch_corpus, "search", kwargs)
    assert fresh_after["returned"] == baseline["returned"] + 1, (
        "a fresh process must see the live journal, not a stale cache"
    )
    assert any(
        item["effect_id"] == "eff-restart-99" for item in fresh_after["results"]
    )
    report = store.verify()
    assert report.ok, report
    assert journal_count(arch_corpus["blackbox_root"]) == 10


# ---------------------------------------------------------------------------
# CONCURRENCY: readers do not interfere, mutate nothing, never cross sessions
# ---------------------------------------------------------------------------


def test_concurrent_mixed_readers_identical_nonmutating_and_execution_none(arch_corpus) -> None:
    pa = _import_pa()
    queries = _reader_queries(arch_corpus)
    assert journal_count(arch_corpus["blackbox_root"]) == 9

    def task(index: int) -> tuple[str, str, str]:
        name, operation, kwargs = queries[index % len(queries)]
        out = _call(pa, operation, kwargs)
        assert out["execution"] == "none", (name, out.get("execution"))
        assert out["returned"] == len(out["results"])
        return name, out["execution"], freeze(out)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(task, range(24)))

    assert len(outcomes) == 24  # every task succeeded, none raised
    by_query: dict[str, set[str]] = {}
    for name, _execution, frozen in outcomes:
        by_query.setdefault(name, set()).add(frozen)
    assert set(by_query) == {name for name, _op, _kw in queries}
    for name, variants in by_query.items():
        assert len(variants) == 1, f"{name} differed across threads: {len(variants)} variants"

    assert journal_count(arch_corpus["blackbox_root"]) == 9  # readers mutated nothing
    report = arch_corpus["blackbox"].verify()
    assert report.ok, report


def test_concurrent_cross_session_searches_never_bleed(arch_corpus) -> None:
    pa = _import_pa()
    sessions = ["sess-night", "sess-day"] * 4

    def task(session_id: str) -> tuple[str, dict]:
        return session_id, pa.search(session_id=session_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(task, sessions))

    nights = [out for session, out in outcomes if session == "sess-night"]
    days = [out for session, out in outcomes if session == "sess-day"]
    assert len(nights) == 4 and len(days) == 4

    assert len({freeze(out) for out in nights}) == 1, "sess-night varied across threads"
    assert len({freeze(out) for out in days}) == 1, "sess-day varied across threads"

    for out in nights:
        assert out["returned"] >= 8  # the eight night entries
        for item in out["results"]:
            assert item["session_id"] == "sess-night", item
            assert item["turn_id"] != "turn-day-1", item
            assert item["effect_id"] != "eff-day-01", item
    for out in days:
        assert out["returned"] >= 1
        assert all(item["session_id"] == "sess-day" for item in out["results"])
        assert all(item["effect_id"] != "eff-push-77" for item in out["results"])
        assert "sess-night" not in json.dumps(out["results"], ensure_ascii=False)


def test_concurrent_git_compare_identical_changed_paths(arch_corpus) -> None:
    pa = _import_pa()
    sha_a, sha_b = arch_corpus["git"]["sha_a"], arch_corpus["git"]["sha_b"]
    repo = str(arch_corpus["git_repo"])

    def task(_index: int) -> dict:
        return pa.compare(kind="git_commits", a=sha_a, b=sha_b, workspace_root=repo)

    with ThreadPoolExecutor(max_workers=6) as pool:
        envelopes = list(pool.map(task, range(6)))

    assert len(envelopes) == 6  # no thread raised; subprocess git under threads is safe
    assert len({freeze(out) for out in envelopes}) == 1, "compare varied across threads"

    def changed_paths(out: dict) -> list:
        comparison = out.get("comparison") if isinstance(out, dict) else None
        if isinstance(comparison, dict) and "changed_paths" in comparison:
            return comparison["changed_paths"]
        for item in out.get("results", []):
            if isinstance(item, dict) and "changed_paths" in item:
                return item["changed_paths"]
        raise AssertionError(f"no comparison.changed_paths in envelope: {sorted(out)}")

    assert changed_paths(envelopes[0]) == ["src/config.py"]


def test_concurrent_writer_appends_do_not_disturb_readers(arch_corpus) -> None:
    pa = _import_pa()
    store = arch_corpus["blackbox"]
    queries = _reader_queries(arch_corpus)
    writer_errors: list[Exception] = []

    def writer() -> None:
        try:
            for i in range(5):
                store.append(
                    {
                        "schema": "blackbox_effect_v1",
                        "kind": "effect_intended",
                        "effect_id": f"eff-cnc-{i}",
                        "turn_id": "turn-cnc",
                        "session_id": "sess-cnc",
                        "trace_id": f"trace-cnc-{i}",
                        "ts": "2026-09-03T23:55:00+00:00",
                        "root": "/ws",
                        "path": f"notes/cnc-{i}.md",
                        "operation": "create",
                        "intent": "workspace.write_file",
                        "tool": "workspace__write_file",
                        "provider": "qwen-local",
                    }
                )
        except Exception as exc:  # pragma: no cover - only on real failure
            writer_errors.append(exc)

    def reader(index: int) -> tuple[str, str, str]:
        name, operation, kwargs = queries[index % len(queries)]
        out = _call(pa, operation, kwargs)
        assert out["returned"] == len(out["results"]), (
            f"{name} envelope internally inconsistent: "
            f"returned={out['returned']} results={len(out['results'])}"
        )
        return name, out["execution"], freeze(out)

    with ThreadPoolExecutor(max_workers=9) as pool:
        writer_future = pool.submit(writer)
        reads = list(pool.map(reader, range(8)))
        writer_future.result()

    assert not writer_errors, writer_errors
    assert len(reads) == 8  # every reader finished without raising
    assert all(execution == "none" for _name, execution, _frozen in reads)
    report = store.verify()
    assert report.ok, report
    assert journal_count(arch_corpus["blackbox_root"]) == 14  # 9 corpus + 5 harmless appends
