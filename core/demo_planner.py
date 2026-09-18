"""Demo-video and image prompt planner.

Turns a product brief (typically distilled from a GitHub README or docs - see core.demo_source)
into a timed, shot-by-shot demonstration-video prompt plan: a presenter walking through the product
while the background represents what the product does, with burned-in subtitles and one shot per
feature. The plan is split into seconds and returned as editable structured data, so the user can
accept or tweak each shot before their local video model renders it. Also plans a matching image set.

Deterministic and offline: the plan is composed from templates + the visual playbook grammar
(core.visual_playbooks), no model call required. A model may later enrich subtitles/prompts, but the
skeleton stands on its own so it is fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# A demo always has a presenter plus on-screen UI/text, so the failure modes are consistent across
# products - unlike a genre playbook's negatives (e.g. product_ad's bottle-specific ones).
_DEMO_NEGATIVE = (
    "garbled or misspelled subtitle text, unreadable UI text, warped or extra fingers, distorted face, "
    "extra limbs, duplicated presenter, floating or clipped text, inconsistent lighting, flicker, "
    "low-resolution, jpeg artifacts, watermark, distorted logo, oversaturated colors"
)

_PRESENTERS = {
    "woman": "a poised professional woman presenter in smart-casual attire",
    "man": "a poised professional man presenter in smart-casual attire",
    "girl": "a friendly young woman presenter in casual attire",
    "boy": "a friendly young man presenter in casual attire",
    "person": "a friendly professional presenter",
}
_DEFAULT_PRESENTER = "person"
_DEFAULT_GENRE = "product_ad"


@dataclass(frozen=True)
class Feature:
    name: str
    blurb: str = ""


@dataclass
class DemoBrief:
    product_name: str
    tagline: str = ""
    features: tuple[Feature, ...] = ()
    presenter: str = _DEFAULT_PRESENTER  # woman | man | girl | boy | person
    style: str = "clean modern product demo, crisp studio lighting, tech-forward"
    aspect: str = "16:9"
    total_seconds: int = 30
    genre: str = _DEFAULT_GENRE          # a core.visual_playbooks key for background/camera grammar
    source_url: str = ""


@dataclass
class Shot:
    index: int
    beat: str            # hook | intro | feature | outro
    start_s: int
    end_s: int
    presenter_action: str
    background: str
    camera: str
    subtitle: str
    sound: str
    generation_prompt: str
    voiceover: str = ""     # the spoken narration line for this shot

    @property
    def duration_s(self) -> int:
        return self.end_s - self.start_s

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "index", "beat", "start_s", "end_s", "presenter_action", "background",
            "camera", "subtitle", "voiceover", "sound", "generation_prompt")}
        d["duration_s"] = self.duration_s
        return d


@dataclass
class DemoPlan:
    brief: DemoBrief
    shots: list[Shot] = field(default_factory=list)

    @property
    def total_seconds(self) -> int:
        return self.shots[-1].end_s if self.shots else 0

    @property
    def title(self) -> str:
        b = self.brief
        return f"{b.product_name} - {b.tagline}" if b.tagline else b.product_name

    @property
    def music(self) -> str:
        return "an upbeat, modern instrumental bed that builds into the reveal and resolves on the CTA"

    def narration_script(self) -> str:
        """The full voiceover, all shots joined - the script to record."""
        return " ".join(s.voiceover.strip() for s in self.shots if s.voiceover.strip())

    def to_dict(self) -> dict:
        return {
            "product": self.brief.product_name,
            "title": self.title,
            "tagline": self.brief.tagline,
            "aspect": self.brief.aspect,
            "total_seconds": self.total_seconds,
            "music": self.music,
            "narration_script": self.narration_script(),
            "shots": [s.to_dict() for s in self.shots],
        }

    def to_markdown(self) -> str:
        """A presentation-ready storyboard: title, runtime, music, the recording script, then shots."""
        b = self.brief
        lines = [f"# Demo video plan - {b.product_name} ({self.total_seconds}s, {b.aspect})"]
        if b.tagline:
            lines.append(f"_{b.tagline}_")
        lines.append(f"\n**Music:** {self.music}")
        lines.append(f"**Voiceover script (record this):** {self.narration_script()}\n")
        for s in self.shots:
            block = (
                f"\n## Shot {s.index} - {s.beat} [{s.start_s}-{s.end_s}s, {s.duration_s}s]\n"
                f"- Subtitle: \"{s.subtitle}\"\n"
            )
            if s.voiceover:
                block += f"- Voiceover: \"{s.voiceover}\"\n"
            block += f"- Sound: {s.sound}\n- Prompt: {s.generation_prompt}"
            lines.append(block)
        return "\n".join(lines)


def _presenter_text(presenter: str) -> str:
    return _PRESENTERS.get(str(presenter or "").strip().lower(), _PRESENTERS[_DEFAULT_PRESENTER])


def _timeline(total: int, weights: list[float]) -> list[tuple[int, int]]:
    """Split ``total`` seconds across segments by weight, each >= 2s, summing exactly to total."""
    n = len(weights)
    if n == 0:
        return []
    total = max(int(total), n * 2)
    wsum = sum(weights) or 1.0
    durs = [max(2, round(total * w / wsum)) for w in weights]
    # correct rounding so the durations sum to total, never dropping below 2s
    diff = total - sum(durs)
    step = 1 if diff > 0 else -1
    guard = 0
    while diff != 0 and guard < 10000:
        order = sorted(range(n), key=lambda i: durs[i], reverse=(step > 0))
        for i in order:
            if diff == 0:
                break
            if step < 0 and durs[i] <= 2:
                continue
            durs[i] += step
            diff -= step
        guard += 1
    spans: list[tuple[int, int]] = []
    cursor = 0
    for d in durs:
        spans.append((cursor, cursor + d))
        cursor += d
    return spans


def _background_for(feature: Feature | None, brief: DemoBrief, *, beat: str) -> str:
    product = brief.product_name
    if beat == "hook":
        return f"a bold title card revealing '{product}', abstract animated visualization of the product in motion behind it"
    if beat == "intro":
        return f"a clean studio set whose back wall is a large screen showing {product}'s interface / core visual"
    if beat == "outro":
        return f"a closing brand frame: the '{product}' name and a call-to-action, product visuals softly animating behind"
    detail = feature.blurb or feature.name if feature else "a key capability"
    return f"the back-wall screen animates to represent '{feature.name if feature else 'a feature'}': {detail}"


def _prompt_for_shot(
    *, presenter: str, action: str, background: str, camera: str, subtitle: str, brief: DemoBrief,
    negative: str, voiceover: str = "",
) -> str:
    vo = f" Voiceover (spoken narration, warm and clear): \"{voiceover.strip()}\"." if voiceover.strip() else ""
    return (
        f"{camera}. Subject: {presenter}, {action}. Environment: {background}. On-screen product "
        f"element clearly legible. Burned-in subtitle, clean sans-serif, lower third: \"{subtitle}\".{vo} "
        f"Style: {brief.style}, {brief.aspect}, sharp focus, readable text, coherent single scene. "
        f"Negative prompt: {negative}"
    )


def plan_video_demo(brief: DemoBrief) -> DemoPlan:
    """Build the timed, shot-by-shot demo-video prompt plan (hook, intro, one shot per feature, outro)."""
    presenter = _presenter_text(brief.presenter)
    negative = _DEMO_NEGATIVE
    cam_feature = "medium shot, slow lateral tracking dolly following the presenter, eye-level, 35mm"
    features = list(brief.features) or [Feature("Overview", brief.tagline or "what it does")]

    weights = [1.0, 1.3] + [1.5] * len(features) + [1.3]
    spans = _timeline(brief.total_seconds, weights)
    beats = ["hook", "intro"] + ["feature"] * len(features) + ["outro"]

    shots: list[Shot] = []
    feature_iter = iter(features)
    for i, (beat, (start, end)) in enumerate(zip(beats, spans, strict=True)):
        if beat == "hook":
            action = "not yet on screen; the title animates in, then the presenter steps into frame"
            camera = "slow push-in on the title card, then reveal, 35mm"
            subtitle = brief.product_name
            voiceover = f"Meet {brief.product_name}."
            sound = "rising synth swell, soft whoosh on the title reveal"
            feat = None
        elif beat == "intro":
            action = "walks confidently into the studio, gestures toward the screen, warm and welcoming"
            camera = "tracking dolly following the presenter, medium-wide, 35mm"
            subtitle = brief.tagline or f"Meet {brief.product_name}"
            voiceover = brief.tagline or f"This is {brief.product_name} - here's what it does."
            sound = "light upbeat bed, presenter voiceover begins"
            feat = None
        elif beat == "outro":
            action = "turns to camera with an inviting gesture as the brand frame settles"
            camera = "slow pull-back to a clean brand frame, 35mm"
            subtitle = "Get started today"
            voiceover = f"That's {brief.product_name}. Get started today."
            sound = "music resolves, gentle button click"
            feat = None
        else:
            feat = next(feature_iter)
            action = f"gestures to the screen and walks a step, presenting '{feat.name}'"
            camera = cam_feature
            subtitle = (feat.blurb or feat.name)[:90]
            voiceover = f"{feat.name} - {feat.blurb}." if feat.blurb else f"{feat.name}."
            sound = "subtle UI ticks tied to the on-screen change, voiceover continues"
        background = _background_for(feat, brief, beat=beat)
        shots.append(Shot(
            index=i, beat=beat, start_s=start, end_s=end,
            presenter_action=action, background=background, camera=camera,
            subtitle=subtitle, voiceover=voiceover, sound=sound,
            generation_prompt=_prompt_for_shot(
                presenter=presenter, action=action, background=background, camera=camera,
                subtitle=subtitle, brief=brief, negative=negative, voiceover=voiceover),
        ))
    return DemoPlan(brief=brief, shots=shots)


def compose_shot_prompt(shot: Shot, brief: DemoBrief) -> str:
    """Rebuild a shot's generation prompt from its current fields + the brief (used after an edit)."""
    return _prompt_for_shot(
        presenter=_presenter_text(brief.presenter), action=shot.presenter_action,
        background=shot.background, camera=shot.camera, subtitle=shot.subtitle,
        brief=brief, negative=_DEMO_NEGATIVE, voiceover=shot.voiceover,
    )


def _beat_weight(beat: str) -> float:
    return {"hook": 1.0, "intro": 1.3, "outro": 1.3}.get(beat, 1.5)


def retime(plan: DemoPlan, total_seconds: int) -> DemoPlan:
    """Redistribute the whole video to a new total, keeping the per-beat weighting."""
    weights = [_beat_weight(s.beat) for s in plan.shots]
    spans = _timeline(max(4, int(total_seconds)), weights)
    shots = [replace(s, start_s=a, end_s=b) for s, (a, b) in zip(plan.shots, spans, strict=True)]
    return DemoPlan(brief=plan.brief, shots=shots)


@dataclass
class ImageShot:
    index: int
    label: str
    subtitle: str
    generation_prompt: str

    def to_dict(self) -> dict:
        return {"index": self.index, "label": self.label, "subtitle": self.subtitle,
                "generation_prompt": self.generation_prompt}


def plan_image_set(brief: DemoBrief) -> list[ImageShot]:
    """A matching still set: one hero image plus one image per feature."""
    presenter = _presenter_text(brief.presenter)
    negative = _DEMO_NEGATIVE
    shots = [ImageShot(
        index=0, label="hero", subtitle=brief.product_name,
        generation_prompt=(
            f"Hero product shot for '{brief.product_name}'. Subject: {presenter} beside a large screen "
            f"showing the product's core interface. Composition: centred, generous negative space for a "
            f"headline. Style: {brief.style}, {brief.aspect}, studio key light, crisp. "
            f"Negative prompt: {negative}"
        ),
    )]
    for i, feat in enumerate(brief.features, start=1):
        shots.append(ImageShot(
            index=i, label=feat.name, subtitle=(feat.blurb or feat.name)[:90],
            generation_prompt=(
                f"Feature image for '{feat.name}' ({brief.product_name}). Subject: {presenter} presenting "
                f"a screen that visualizes {feat.blurb or feat.name}. Clear single focal element, legible "
                f"UI. Style: {brief.style}, {brief.aspect}, sharp. Negative prompt: {negative}"
            ),
        ))
    return shots
