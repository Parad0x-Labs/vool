"""VOOL Demo Studio — records the real served product, never a stand-in.

Additive ops lane (P1). The studio stages the product at an exact SHA,
verifies the boot against /healthz and the capability manifest, drives the
real chat page in a real browser with screen recording, derives captions and
focus/zoom ONLY from public typed runtime events, redacts everything it
emits, and renders MP4/WebM + subtitles + poster + a machine-readable
evidence manifest bound to the exact commit.

Truth rules:

* the product is never mocked: no scripted UI responses, no hand-authored
  success screens (the deterministic model lane is the repo's established
  provider stub and is labeled as such in every manifest);
* dry-run mode plans and validates only — it never claims a capture;
* every artifact failure aborts visibly with an abort report.
"""

from ops.demo_studio.journeys import (
    JOURNEY_IDS_REQUIRED,
    JOURNEYS,
    PUBLIC_CAPTION_EVENTS,
    STEP_ACTIONS,
    JourneySpec,
    StepSpec,
    journey_ids,
    journey_spec,
)

__all__ = [
    "JOURNEYS",
    "JOURNEY_IDS_REQUIRED",
    "PUBLIC_CAPTION_EVENTS",
    "STEP_ACTIONS",
    "JourneySpec",
    "StepSpec",
    "journey_ids",
    "journey_spec",
]
