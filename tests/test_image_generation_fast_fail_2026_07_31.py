"""Regression tests for the 2026-07-31 "image generation never starts" failure.

Measured live before the fix:

    ensure_comfyui(low_memory="lowvram") blocked for 181.0s and returned
    ok=False, "ComfyUI did not become ready within 180s"

That is longer than a chat turn survives, so the honest message never reached the user -- the turn
was cut off and answered with the generic "I couldn't resolve that cleanly."

Two independent defects, both fixed here.

1. ensure_service THREW AWAY the Popen handle it created, so a child that exited two seconds into
   startup was indistinguishable from one still loading. Nothing could tell "crashed" from "slow",
   so every failed start waited out the full 180s ready_timeout and then reported a timeout for a
   process that had already died. The handle is now kept and polled: a start that dies returns as
   soon as it dies, carrying the exit code and the tail of the engine's own log.

   Measured on this Mac by spawning the product's exact argv: ComfyUI binds :8188 in 10.0s, and it
   binds BEFORE loading any checkpoint. The in-turn wait is therefore a turn-sized budget, not 180s.

2. image_generation_intent required a media noun after the action verb, so the most natural ways to
   ask -- "draw me a cat", "sketch me a dragon", "paint a sunset" -- reached no image lane at all.
   Widening it must not swallow the idioms built on the same verbs; "draw your own conclusions" and
   "paint a picture of the market" are ordinary English, and the second was ALREADY being claimed
   before this change (it returned the prompt "the market").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.execution.constants import image_generation_intent
from core.local_service_autostart import LocalService, ensure_service


class _DeadProc:
    """A child that exits immediately, like a ComfyUI start that dies during import."""

    def __init__(self, code: int = 1) -> None:
        self._code = code

    def poll(self) -> int:
        return self._code


class _LiveProc:
    """A child that stays up but has not bound its port yet."""

    def poll(self) -> None:
        return None


class _Clock:
    """Virtual clock: the wait is measured, never actually slept.

    Without this the sabotage check takes 6.5 real minutes -- an implementation that does not watch
    the child spins on the real monotonic clock for the whole 180s budget. Driving time from the
    fake sleep makes both the fix and its absence resolve instantly and deterministically.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _service(tmp_path: Path, *, log_body: str = "", ready_timeout: float = 180.0) -> LocalService:
    log = tmp_path / "engine.log"
    if log_body:
        log.write_text(log_body, encoding="utf-8")
    return LocalService(
        name="ComfyUI",
        reachable=lambda: False,
        argv=["/bin/false"],
        ready_timeout=ready_timeout,
        poll=2.0,
        log_path=str(log),
    )


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    import core.local_service_autostart as lsa

    c = _Clock()
    monkeypatch.setattr(lsa.time, "monotonic", c.monotonic)
    return c


# --------------------------------------------------------------------------
# 1. A dead start must fail fast, and say why
# --------------------------------------------------------------------------

def test_a_start_that_dies_does_not_wait_out_the_180s_timeout(tmp_path, clock) -> None:
    svc = _service(tmp_path, log_body="loading\ncomfy-aimdo unsupported operating system: Darwin\n")
    ok, _msg = ensure_service(
        svc, sleep=clock.sleep, spawn=lambda *a, **k: _DeadProc(code=1),
    )
    assert ok is False
    # The whole point: it returned without burning the 180s budget (90 polls at 2.0s).
    assert len(clock.sleeps) <= 1, clock.sleeps
    assert clock.now < 5.0, clock.now


def test_a_dead_start_reports_the_exit_code_and_the_engines_own_log(tmp_path, clock) -> None:
    svc = _service(
        tmp_path, log_body="importing torch\ncomfy-aimdo unsupported operating system: Darwin\n",
    )
    ok, msg = ensure_service(
        svc, sleep=clock.sleep, spawn=lambda *a, **k: _DeadProc(code=9),
    )
    assert ok is False
    assert "exited during startup" in msg
    assert "code 9" in msg
    # The reason has to travel with the failure -- "couldn't bring it up" sent the user to check an
    # install that is present and fine.
    assert "comfy-aimdo unsupported operating system: Darwin" in msg


def test_a_slow_start_is_reported_as_still_starting_not_as_a_failure(tmp_path, clock) -> None:
    # A live-but-unbound child is genuinely still coming up; it is detached, so the next request
    # usually finds it ready. Saying "failed" there would be false.
    svc = _service(tmp_path, ready_timeout=8.0)
    ok, msg = ensure_service(svc, sleep=clock.sleep, spawn=lambda *a, **k: _LiveProc())
    assert ok is False
    assert "still starting" in msg
    assert "exited" not in msg


def test_the_in_turn_start_budget_fits_inside_a_turn(monkeypatch) -> None:
    # 180s outlived the turn, so the honest message was replaced by a generic non-answer. ComfyUI
    # was measured binding its port in 10.0s on this machine.
    import importlib

    import core.local_media_render as lmr

    monkeypatch.delenv("VOOL_COMFY_START_TIMEOUT", raising=False)
    importlib.reload(lmr)
    try:
        assert lmr._COMFY_START_TIMEOUT <= 60.0, lmr._COMFY_START_TIMEOUT
        assert lmr._COMFY_START_TIMEOUT >= 20.0, lmr._COMFY_START_TIMEOUT
    finally:
        importlib.reload(lmr)


# --------------------------------------------------------------------------
# 2. The detector
# --------------------------------------------------------------------------

# The verbatim phrasings that reached no image lane at all before this fix.
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("draw me a cat", "cat"),
        ("sketch me a dragon", "dragon"),
        ("paint a sunset", "sunset"),
        ("draw me a cartoon robot holding a balloon", "cartoon robot holding a balloon"),
        ("sketch me a logo for a coffee shop", "logo for a coffee shop"),
        ("paint a sunset over water", "sunset over water"),
        ("draw a wizard casting a spell", "wizard casting a spell"),
        ("can you draw me a fox in a forest", "fox in a forest"),
        ("illustrate a dragon guarding treasure", "dragon guarding treasure"),
        ("doodle me a happy penguin", "happy penguin"),
    ],
)
def test_a_depictive_verb_asks_for_a_picture_without_naming_one(message: str, expected: str) -> None:
    assert image_generation_intent(message) == expected


def test_the_media_noun_phrasings_still_work() -> None:
    assert image_generation_intent("generate an image of a red bicycle on a beach") == "a red bicycle on a beach"
    assert image_generation_intent("make me a picture of a lighthouse") == "a lighthouse"
    assert image_generation_intent("render a scene of mountains") == "a scene of mountains"
    # A concrete subject behind "paint a picture of" is a real render request.
    assert image_generation_intent("paint a picture of a stormy sea") == "a stormy sea"


@pytest.mark.parametrize(
    "message",
    [
        # The two the widening must not swallow.
        "draw your own conclusions",
        "paint a picture of the market",
        # A modifier before the head noun: found live, still claimed after the first fix.
        "paint a picture of the housing market for me",
        "paint a picture of the current job market",
        # "paint a picture of the market" was ALREADY claimed before this change, returning "the
        # market" -- so these guard a pre-existing false positive as well as the new surface.
        "let me paint a picture for you",
        "this paints a worrying picture of the economy",
        "the report paints a grim picture",
        # Idioms built on the same verbs.
        "draw a line between the two ideas",
        "draw a comparison between the two approaches",
        "draw attention to the main issue",
        "draw up a contract",
        "can you draw on your knowledge of python",
        "sketch out a plan for the migration",
        # A chore, not a render: a definite subject with no request framing.
        "paint the fence",
        "painting the shed tomorrow",
        # Advisory framings stay chat.
        "how do I draw a cat in photoshop",
        "what is the best tool to generate images",
        "explain how image generation works",
    ],
)
def test_an_ordinary_sentence_is_not_an_image_request(message: str) -> None:
    assert image_generation_intent(message) is None


# --------------------------------------------------------------------------
# 3. The reply the user actually gets
# --------------------------------------------------------------------------

def _drive_unavailable_engine(monkeypatch, *, has_cloud_key: bool) -> str:
    import core.resource_governor as gov
    from core import local_media_render as lmr
    from core import media_tools
    from core.agent_runtime import fast_paths_media

    # Pin the governor. Unpinned, this asserts on live RAM: at 0.75 GB free -- which is where this
    # machine actually sat -- plan_render() returns no tier and the fast path answers "extremely low
    # on memory" before it ever reaches the engine, so the test passed alone and failed in the full
    # suite. The subject here is the message after a failed START, not the memory refusal.
    monkeypatch.setattr(
        gov, "plan_render",
        lambda: (gov.RENDER_TIERS[0], gov.GovernorDecision(ok=True, usable_after_gb=12.0)),
    )
    monkeypatch.setattr(gov, "reclaim_if_pressured", lambda: [])

    monkeypatch.setattr(lmr, "local_render_available", lambda: True)
    monkeypatch.setattr(lmr, "wants_local_render", lambda t: True)
    monkeypatch.setattr(lmr, "comfyui_reachable", lambda *a, **k: False)
    monkeypatch.setattr(lmr, "comfyui_autostart_enabled", lambda: True)
    monkeypatch.setattr(
        lmr, "ensure_comfyui",
        lambda **k: (False, "ComfyUI exited during startup (code 1): comfy-aimdo unsupported operating system: Darwin"),
    )
    monkeypatch.setattr(media_tools, "has_image_service", lambda: has_cloud_key)
    monkeypatch.setattr(media_tools, "generate_image", lambda **k: pytest.fail("cloud used silently"))

    class _Agent:
        def _fast_path_result(self, *, response: str, **kw):
            return {"response": response}

        def _emit_runtime_event(self, *a, **k):
            return None

    result = fast_paths_media.maybe_handle_image_generation(
        # Auto mode: the subject here is the engine-down message, and Auto permits the generation
        # so the turn reaches the engine branch instead of the permission prompt a modeless
        # context (which resolves Manual) now correctly raises at the authorization boundary.
        _Agent(), "draw me a cat locally", session_id="s",
        source_context={"operating_mode": "auto"},
    )
    assert result is not None
    return str(result["response"])


def test_the_user_is_told_why_on_device_rendering_is_unavailable(monkeypatch) -> None:
    body = _drive_unavailable_engine(monkeypatch, has_cloud_key=False)
    assert "On-device rendering isn't available" in body
    # The engine's own reason, not a generic line.
    assert "exited during startup" in body
    assert "Darwin" in body
    # With no key configured, name the way to get one.
    assert "image key" in body


def test_a_configured_cloud_key_is_offered_when_the_engine_is_down(monkeypatch) -> None:
    body = _drive_unavailable_engine(monkeypatch, has_cloud_key=True)
    assert "cloud" in body.lower()
    # The user said "locally", so the cloud is offered, never used behind their back -- the
    # generate_image stub above fails the test if it is called.
