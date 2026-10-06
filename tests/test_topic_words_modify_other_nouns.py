"""A topic word that merely MODIFIES another noun is not a this-machine topic.

Live incidents this pins (q90 dev corpus, measured 2026-09-29 on e1dfdb63):
- F02-07 "Which linen thread spec was suggested for my atlas?" was refused by the
  tool-required gate as a this-machine hardware fact ("inspect this machine's hardware")
  because "spec" + "my" matched -- a memory question about a bindery thread.
- F08-10 "How many of the display falcons are molting?" was answered by the machine-read
  fast path with this host's display specs because "display" + "how" matched -- a memory
  question about museum birds.

The safe direction of error is declining: the model lane can still call the machine tool
when the question really is about this host; a false positive hijacks or refuses a memory
question outright.
"""

from __future__ import annotations

from core.execution.constants import local_fact_capability_required, machine_display_intent


class TestAttributiveDisplayIsNotTheMonitor:
    def test_reproduction_display_falcons(self) -> None:
        # F08-10 verbatim: museum birds on display, not this host's screen.
        assert machine_display_intent("How many of the display falcons are molting?") is None
        assert local_fact_capability_required("How many of the display falcons are molting?") is None

    def test_fresh_display_attributive_variants(self) -> None:
        assert machine_display_intent("How many of the display amaryllis are blooming?") is None
        assert machine_display_intent("What's sitting in the display cabinet by the stairs?") is None
        assert local_fact_capability_required("Are the display beetles still lit in the entomology hall?") is None

    def test_genuine_display_questions_still_route(self) -> None:
        assert machine_display_intent("what is my screen resolution?") == "machine.display_inspect"
        assert machine_display_intent("what's my display resolution") == "machine.display_inspect"
        assert machine_display_intent("what monitor am i using?") == "machine.display_inspect"
        assert machine_display_intent("what display do i have?") == "machine.display_inspect"
        assert machine_display_intent("is my screen retina?") == "machine.display_inspect"

    def test_genuine_display_questions_still_gated(self) -> None:
        assert local_fact_capability_required("what is my screen resolution?") == "the display"
        assert local_fact_capability_required("what refresh rate is my monitor running?") == "the display"


class TestAttributiveSpecIsNotTheHardware:
    def test_reproduction_thread_spec(self) -> None:
        # F02-07 verbatim: "my" binds to the atlas, "spec" modifies "thread".
        assert local_fact_capability_required("Which linen thread spec was suggested for my atlas?") is None

    def test_fresh_spec_attributive_variants(self) -> None:
        assert local_fact_capability_required("Which varnish ratio was suggested for my driftwood mirror?") is None
        assert local_fact_capability_required("What tire spec did the shop recommend for my touring bike?") is None
        assert local_fact_capability_required("Which thread spec did you suggest for the sail repair?") is None

    def test_machine_anchored_spec_still_gated(self) -> None:
        assert local_fact_capability_required("what spec is my computer?") == "this machine's hardware"
        assert local_fact_capability_required("what's my laptop's spec again?") == "this machine's hardware"
        assert local_fact_capability_required("what are my specs?") == "this machine's hardware"
        assert local_fact_capability_required("how much RAM does my machine have?") == "this machine's hardware"
