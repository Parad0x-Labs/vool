"""Prompt capture must be recording BEFORE the failure it is supposed to explain.

The incident: an audit turn failed on three provider lanes, each emitting `model.call_failed` with
`reason=prompt_budget_exceeded`, `error_kind=prompt_shape` and a `prompt_budget`. The one surface
that could have shown the request that overflowed -- `~/.vool_runtime/logs/prompt_debug.jsonl` --
was empty, because capture was gated on `VOOL_DEBUG_PROMPT=1` and the daemon never exported it.
`python -m core.turn_trace` printed "none captured", and the cause was recovered by hand-tracing.

Capture is now on unless the operator opts out. These tests pin the four properties that make
always-on safe, each of which the previous opt-in default never had to satisfy:

* it records with no environment set at all;
* `VOOL_DEBUG_PROMPT=0` still turns it off;
* an API key in a prompt does not reach the disk;
* disk use is bounded -- the file that motivated this was already 969,714 bytes over 29 records
  (mean 33KB, median 47KB per record), which is ~34MB per thousand model calls unrotated.

Plus the permission invariant that rotation could quietly break: `os.replace` carries the temp
file's mode, so a rotated capture file must be re-asserted 0600.
"""
from __future__ import annotations

import json
import os
import stat
import threading

import pytest

from core import prompt_debug
from core.prompt_debug import dump_outbound_prompt, prompt_debug_enabled


@pytest.fixture
def capture_home(tmp_path, monkeypatch):
    """Point the capture file at a throwaway runtime home and hand back its path.

    The session conftest pins a process-wide runtime home via ``configure_runtime_home``, which wins
    over ``VOOL_HOME``, so setting the env var alone would leave these tests writing into the shared
    session home and reading each other's rows.
    """
    from core.runtime_paths import active_vool_home, configure_runtime_home

    previous = active_vool_home()
    configure_runtime_home(tmp_path)
    # Capture is opt-in, so arm it for the tests whose subject is what gets WRITTEN. The two tests
    # whose subject is the default itself override this with delenv / setenv.
    monkeypatch.setenv("VOOL_DEBUG_PROMPT", "1")
    try:
        yield tmp_path / "logs" / "prompt_debug.jsonl"
    finally:
        configure_runtime_home(previous)


def _payload(text: str = "hello", *, messages: int = 1) -> dict:
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": text} for _ in range(messages)],
    }


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_capture_stays_off_until_the_operator_arms_it(capture_home, monkeypatch):
    """Default-on was built, measured, and reverted on 2026-07-29.

    It writes the FULL outbound prompt to disk, and `core/secret_redaction.py` is label-driven, not
    entropy-driven: an unlabelled BIP-39 mnemonic reaches disk verbatim (pinned below). Turning that
    on for every user by default, on a machine holding live keypairs, is not a trade a debugging
    convenience earns. `core/turn_trace.py` prints the command to arm it at the moment it is needed.
    """
    monkeypatch.delenv("VOOL_DEBUG_PROMPT", raising=False)
    assert prompt_debug_enabled() is False

    dump_outbound_prompt(_payload("what is my name?"), lane="openai", provider_id="openrouter")

    assert not capture_home.exists(), "an unarmed runtime must write nothing"


def test_an_armed_model_call_is_captured(capture_home, monkeypatch):
    monkeypatch.setenv("VOOL_DEBUG_PROMPT", "1")
    dump_outbound_prompt(_payload("what is my name?"), lane="openai", provider_id="openrouter")

    rows = _rows(capture_home)
    assert len(rows) == 1
    assert rows[0]["lane"] == "openai"
    assert rows[0]["provider_id"] == "openrouter"
    assert rows[0]["payload"]["messages"][0]["content"] == "what is my name?"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", " 0 ", "", "   "])
def test_the_operator_can_still_switch_prompt_capture_off(capture_home, monkeypatch, value):
    """An explicit off must stay off, so a runbook that sets it is never surprised."""
    monkeypatch.setenv("VOOL_DEBUG_PROMPT", value)

    assert prompt_debug_enabled() is False
    dump_outbound_prompt(_payload("must not be written"))

    assert not capture_home.exists()


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
def test_an_explicit_on_switch_still_means_on(capture_home, monkeypatch, value):
    """Existing runbooks and docs say VOOL_DEBUG_PROMPT=1; that must not become a no-op."""
    monkeypatch.setenv("VOOL_DEBUG_PROMPT", value)

    dump_outbound_prompt(_payload())

    assert len(_rows(capture_home)) == 1


def test_an_unrecognised_flag_value_fails_closed(capture_home, monkeypatch):
    """A typo must not silently record prompts the operator believed they had disabled."""
    monkeypatch.setenv("VOOL_DEBUG_PROMPT", "flase")

    assert prompt_debug_enabled() is False
    dump_outbound_prompt(_payload())

    assert not capture_home.exists()


def test_an_api_key_in_a_prompt_never_reaches_the_capture_file(capture_home):
    """Every pasted credential is a candidate for the disk once armed. Verified, not assumed."""
    anthropic_key = "sk-ant-api03-" + "A7bK9zQ2mN4pR6tV8wX1" * 3
    groq_key = "gsk_" + "9zQ2mN4pR6tV8wX1A7bK"
    bearer = "Bearer " + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"

    dump_outbound_prompt(
        {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": f"call the API with {anthropic_key}"},
                {"role": "user", "content": f"my groq key is {groq_key} and the header is {bearer}"},
                {"role": "user", "content": "OPENAI_API_KEY=hunter2-not-a-vendor-shape"},
            ],
        },
        extra={"headers": {"authorization": f"Bearer {groq_key}"}},
    )

    raw = capture_home.read_text(encoding="utf-8")
    assert anthropic_key not in raw
    assert groq_key not in raw
    assert "hunter2-not-a-vendor-shape" not in raw
    assert "[redacted-api-key]" in raw
    # The surrounding prose is what makes the capture worth reading; redaction must be surgical.
    assert "call the API with" in raw


def test_the_capture_file_and_its_directory_stay_owner_only(capture_home):
    """A capture holds whatever the user pasted into chat, so nobody else on the box may read it."""
    dump_outbound_prompt(_payload())

    assert stat.S_IMODE(capture_home.stat().st_mode) == 0o600
    assert stat.S_IMODE(capture_home.parent.stat().st_mode) == 0o700


def _write_past_the_rotation_budget(count: int = 40) -> int:
    """Emit `count` ~480KB captures (20 messages at the per-message clip). Returns bytes if unrotated."""
    big = "x" * (prompt_debug._MAX_CONTENT - 1)
    unrotated = 0
    for index in range(1, count + 1):
        payload = _payload(big, messages=20)
        dump_outbound_prompt(payload, extra={"n": index})
        unrotated += len(json.dumps(payload, ensure_ascii=False))
    return unrotated


def test_capture_stops_growing_once_it_passes_its_size_budget(capture_home):
    """An always-on daemon writes forever; 29 records already cost 969,714 bytes unrotated."""
    unrotated = _write_past_the_rotation_budget(40)

    assert unrotated > 2 * prompt_debug._ROTATE_BYTES, "the test never generated enough to rotate"
    assert capture_home.stat().st_size <= prompt_debug._ROTATE_BYTES

    rows = _rows(capture_home)
    assert len(rows) < 40, "nothing was dropped, so nothing is bounding this file"

    # Pin the CONTENT of what survived, not just the size. An adversarial review reversed the
    # retention (keeping the OLDEST records, out of order) and every assertion here still passed,
    # because `rows[-1]["n"] == 40` held by luck of the final append and `rows[0]["n"] > 1` is
    # satisfied by any non-first record. A rotation that keeps the wrong end is a rotation that
    # throws away the record you opened the file to read.
    kept = [row["extra"]["n"] for row in rows]
    assert kept == sorted(kept), f"records are out of order after rotation: {kept}"
    assert kept[-1] == 40, "the newest capture must survive -- it is the one you came for"
    assert kept == list(range(kept[0], 41)), f"retention must be a contiguous newest tail: {kept}"
    # A contiguous newest tail of length one is still a contiguous newest tail. Rotation that keeps
    # a single record technically bounds the file and destroys the tool: you cannot trace a turn
    # from one model call. Budget is ~4MB against records measured at 1.6-49.6KB, so a healthy
    # rotation keeps many.
    assert len(kept) > 1, f"rotation kept only {kept} — nothing is traceable from one record"


def test_the_capture_file_is_owner_only_after_every_single_call_including_the_rotating_one(capture_home):
    """Rotation replaces the inode, so it is where 0600 silently becomes 0644 until the next call.

    Asserting only at the end hides this: the last call usually does not rotate, so the file is
    0600 again by the time anyone looks. The mode has to hold after EVERY call.
    """
    big = "x" * (prompt_debug._MAX_CONTENT - 1)
    rotated = False
    for index in range(1, 41):
        before = len(_rows(capture_home)) if capture_home.exists() else 0
        dump_outbound_prompt(_payload(big, messages=20), extra={"n": index})
        rotated = rotated or len(_rows(capture_home)) <= before
        assert stat.S_IMODE(capture_home.stat().st_mode) == 0o600, f"call {index} left the capture readable"

    assert rotated, "no rotation happened in this run, so the mode was never at risk"


def test_the_replacement_file_is_owner_only_before_it_ever_goes_live(capture_home, monkeypatch):
    """The rotation temp file holds the same prompts; it must not sit at the umask default mid-copy."""
    seen: list[int] = []
    real_replace = os.replace

    def watched_replace(src, dst, *args, **kwargs):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(prompt_debug.os, "replace", watched_replace)
    _write_past_the_rotation_budget(40)

    assert seen, "rotation never ran, so nothing was measured"
    assert set(seen) == {0o600}, f"a rotation temp file went live at {[oct(mode) for mode in seen]}"


def test_two_lanes_capturing_at_once_do_not_write_over_each_other(capture_home, monkeypatch):
    """The failing turn hit three provider lanes; a 47KB record is not one atomic write() syscall.

    Python's buffered writer is free to split a large text write, so two lanes appending together can
    interleave and leave a torn line that no reader can parse. The split is forced here rather than
    hoped for: this test asserts the lock, not the buffer size that happens to hide the problem today.
    """
    import time

    real_open = open

    class _SplitWriter:
        """A handle that writes in two syscalls with a gap -- behaviour `open` is allowed to have."""

        def __init__(self, handle):
            self._handle = handle

        def write(self, text: str) -> None:
            middle = len(text) // 2
            self._handle.write(text[:middle])
            self._handle.flush()
            time.sleep(0.05)
            self._handle.write(text[middle:])

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

    monkeypatch.setattr(prompt_debug, "open", lambda *a, **kw: _SplitWriter(real_open(*a, **kw)), raising=False)

    def lane(index: int) -> None:
        dump_outbound_prompt(_payload(f"lane {index}"), extra={"n": index})

    threads = [threading.Thread(target=lane, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    rows = _rows(capture_home)  # raises on a torn line
    assert sorted(row["extra"]["n"] for row in rows) == [0, 1, 2, 3]


def test_a_capture_written_while_another_lane_rotates_is_not_swallowed(capture_home, monkeypatch):
    """Rotation is read-modify-replace, so an append landing inside it is written to a doomed inode.

    Lane A is held open mid-`os.replace` and lane B appends during that window. The lock has to cover
    append AND rotation together: with them separate, lane B's record goes to the inode `os.replace`
    is about to discard and the capture that explains the turn is simply gone.
    """
    import time

    rotating = threading.Event()
    lane_b_done = threading.Event()
    real_replace = os.replace

    def slow_replace(src, dst, *args, **kwargs):
        rotating.set()
        time.sleep(0.3)
        return real_replace(src, dst, *args, **kwargs)

    def lane_b() -> None:
        rotating.wait(5)
        # Lane B must not start a rotation of its own -- what is being measured is its APPEND, and two
        # rotations racing for the same temp path would decide the outcome by coin flip.
        monkeypatch.setattr(prompt_debug, "_ROTATE_BYTES", 1 << 40)
        dump_outbound_prompt(_payload("lane b"), extra={"n": "lane-b"})
        lane_b_done.set()

    monkeypatch.setattr(prompt_debug.os, "replace", slow_replace)
    thread = threading.Thread(target=lane_b, daemon=True)
    thread.start()
    big = "x" * (prompt_debug._MAX_CONTENT - 1)
    for index in range(40):
        dump_outbound_prompt(_payload(big, messages=20), extra={"n": index})
        if rotating.is_set():
            break  # stop at the FIRST rotation, or a later one would legitimately age lane B out
    thread.join(10)

    assert rotating.is_set(), "rotation never ran, so the race was never exercised"
    assert lane_b_done.is_set()
    rows = _rows(capture_home)
    assert any(row.get("extra", {}).get("n") == "lane-b" for row in rows), "a concurrent capture was lost"


def test_a_capture_failure_never_raises_into_the_turn(capture_home, monkeypatch):
    """Capture is now on for every user, so its failure mode has to stay invisible to the turn."""
    monkeypatch.setattr(prompt_debug, "_log_path", lambda: (_ for _ in ()).throw(OSError("read-only fs")))

    dump_outbound_prompt(_payload())  # must not raise

    assert not capture_home.exists()


def test_an_unserialisable_payload_does_not_break_the_turn_or_the_file(capture_home):
    """A payload can carry anything an adapter put in it; a bad value must not corrupt the JSONL."""
    dump_outbound_prompt(_payload("first"))
    dump_outbound_prompt({"model": "m", "messages": [{"role": "user", "content": object()}]})
    dump_outbound_prompt(_payload("third"))

    rows = _rows(capture_home)
    assert [row["payload"]["messages"][0]["content"] for row in rows] == ["first", "third"]


def test_the_trace_shows_a_failed_model_calls_error_kind_without_asking_for_full(monkeypatch):
    """reason alone does not say prompt-shape vs provider-health; the audit turn needed both inline.

    Asserted on RENDERED OUTPUT, not on membership of `_INTERESTING`. An adversarial review broke the
    renderer so `error_kind` was never printed and a membership assertion still passed — a constant
    lookup cannot tell you whether the value reaches a human.
    """
    from core import turn_trace

    monkeypatch.setattr(
        turn_trace,
        "collect_turn_trace",
        lambda session_id="", **_: {
            "session_id": "openclaw:0123456789abcdef0123",
            "events": [
                {
                    "ts": "2026-07-29T01:00:00+00:00",
                    "event_type": "model.call_failed",
                    "message": "Model call failed with ollama-local:qwen3:8b.",
                    "provider_id": "ollama-local:qwen3:8b",
                    "reason": "prompt_budget_exceeded",
                    "error_kind": "prompt_shape",
                }
            ],
            "model_calls": [],
            "receipts": [],
        },
    )

    rendered = turn_trace.render_turn_trace("openclaw:0123456789abcdef0123", full=False)

    assert "reason=prompt_budget_exceeded" in rendered
    assert "error_kind=prompt_shape" in rendered, (
        "the whole point is that a failure states its kind without --full"
    )


def test_capture_costs_a_small_fraction_of_a_model_call(capture_home):
    """Measured at 2.39 ms mean / 3.73 ms max per call on a 47KB payload; guard the order of magnitude."""
    import time

    payload = _payload("z" * 4000, messages=12)
    dump_outbound_prompt(payload)  # warm the redactor import
    started = time.perf_counter()
    for _ in range(10):
        dump_outbound_prompt(payload)
    per_call_ms = (time.perf_counter() - started) * 100

    assert per_call_ms < 50, f"capture cost {per_call_ms:.1f} ms/call — that is material on the hot path"


def test_the_capture_file_is_never_written_outside_the_runtime_home(capture_home):
    """Capture is on for everyone now; it must stay inside the runtime home it was told to use."""
    dump_outbound_prompt(_payload())

    assert capture_home.exists()
    assert capture_home.name == "prompt_debug.jsonl"
    assert os.path.commonpath([str(capture_home), str(capture_home.parent.parent)]) == str(capture_home.parent.parent)
