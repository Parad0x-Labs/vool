"""Web lookups switched on for one test, for the tests that exercise a lookup.

Tests run with `system.allow_web_fallback` off (conftest's
`default_test_policy_disables_web_fallback`), the fresh install's default. A lookup
answers to that switch at the shared door, so a test that drives a weather, price,
rate, news or search lane must switch it on, exactly as the person would. The
`enable_web` fixture is this for pytest-style tests; `web_lookups_on()` is the
same for `unittest.TestCase` setUp and for code that needs a `with`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def web_lookups_on() -> Iterator[None]:
    from core import policy_engine

    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    system = dict(base.get("system") or {})
    system["allow_web_fallback"] = True
    system["local_only_mode"] = False
    base["system"] = system
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous_cache


def switch_web_lookups_on(testcase) -> None:
    """For a `unittest.TestCase.setUp`: on for this test, restored by the test's own cleanup."""
    manager = web_lookups_on()
    manager.__enter__()
    testcase.addCleanup(manager.__exit__, None, None, None)


def switch_web_lookups_on_in_home(env: dict[str, str]) -> None:
    """For a served daemon in its own home: the same operator-policy write Settings makes when the
    person turns Web lookups on, run with that daemon's environment, before it starts."""
    import subprocess
    import sys
    from pathlib import Path

    subprocess.run(
        [sys.executable, "-c",
         "from core import policy_engine; policy_engine.set_operator_policy_values({'system.allow_web_fallback': True})"],
        env=env, cwd=str(Path(__file__).resolve().parents[1]), check=True, capture_output=True, timeout=120,
    )
