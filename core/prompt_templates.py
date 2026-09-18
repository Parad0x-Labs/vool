"""Structured image/video prompt block templates for VOOL.

The strict field blocks a media prompt should fill (Subject/Environment/.../Negative for images;
Scene/Character/Action/.../Final for video), each with one line of how-to guidance, plus the
shot-duration logic and helpers to render a blank or filled block. Generated from the doctrine.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptField:
    name: str
    guidance: str


IMAGE_PRINCIPLES: tuple[str, ...] = (
    'One subject, named first. Front-load the single most important thing in the frame; generation models weight early tokens heavily and will blur, duplicate, or average the image when the lead subject is ambiguous or buried.',
    "Concrete nouns beat mood adjectives. 'brass lantern, salt-stained wool coat, wet cobblestones' render as pixels; 'epic, stunning, beautiful, masterpiece' render as nothing - they only spend token budget and dilute the real description.",
    'Zero contradictions. Never pair mutually exclusive instructions (wide shot + macro, noon sun + candlelight, dead-centered + rule-of-thirds, shallow DoF + everything in focus). The model averages conflicting cues into a muddy compromise.',
    "Every scene needs directed light: give it a direction, a quality (hard/soft), a source, and a color temperature. Lighting is the single biggest lever on realism and mood - name a scheme (Rembrandt, backlit rim, overcast soft) instead of writing 'good lighting'.",
    "Use camera language the model has actually been trained on: focal length, aperture, shot size, camera height, angle. '85mm, f/2.0, eye-level medium close-up' maps to learned optics and controls depth, compression, and framing far better than 'nice photo'.",
    'Build a depth hierarchy - foreground, midground, background. Naming what sits at each depth forces the model to compose space instead of flattening every element onto one plane.',
    "Specify count and spatial relationships. Say 'a single', 'two, side by side', 'to the left of' - vague plurals are where extra limbs, cloned subjects, and merged objects come from.",
    "Negatives subtract failure modes; they never add content. Put 'extra fingers, deformed hands, watermark, text, jpeg artifacts' in the negative prompt - never describe something you actually want to see there, because many models weight negatives inconsistently.",
    "Anchor style to a nameable medium, era, technique, or acknowledged master's method - not vibes. 'painterly, flat-layered ukiyo-e depth' or '35mm film grain, muted grade' gives the model a target; 'trending, award-winning, 8k artstation' gives it noise.",
    "Order the block by importance and keep aspect ratio matched to the composition: subject environment composition lighting lens style emotion detail. Token influence decays with position, so the frame's priorities should decay the same way.",
)

IMAGE_BLOCK: tuple[PromptField, ...] = (
    PromptField(name='Subject', guidance='Name ONE primary subject with concrete attributes - who/what, age or material, build, clothing, pose, expression - so the model knows exactly what to render first and largest.'),
    PromptField(name='Environment', guidance='Place the subject in a concrete setting with foreground/midground/background cues: location, surfaces, era, weather, time of day - never leave the backdrop implied.'),
    PromptField(name='Composition', guidance='State framing and layout explicitly: shot size (close-up/medium/wide), subject placement (centered/thirds), camera height and angle, and pick one - not two - spatial scheme.'),
    PromptField(name='Lighting', guidance="Give the light a direction, a quality (hard/soft), a named source, and a color temperature; specify the scheme (rim, Rembrandt, overcast) so the model doesn't default to flat frontal light."),
    PromptField(name='Lens/camera', guidance='Specify focal length, aperture / depth of field, and camera type; use real optical behavior (35mm wide deep focus, 85mm f/1.8 shallow, 100mm macro) to control compression and blur.'),
    PromptField(name='Style', guidance="Anchor to a nameable medium, era, or technique (photoreal cinematic still, painterly digital, film grain, master's method by name) - banish 'beautiful/epic/masterpiece' filler."),
    PromptField(name='Emotion', guidance='State the intended mood in one word plus how it should read visually (tension via hard shadow, calm via soft light), so the model has a target for expression and grade.'),
    PromptField(name='Details', guidance="Add 2-4 concrete supporting props, textures, or microdetails that reinforce the subject and setting; do not dump unrelated nouns that compete for the model's attention."),
    PromptField(name='Color palette', guidance='Name the dominant hues and the accent, ideally as a relationship (cold slate blues against warm amber) - this stabilizes grade and stops oversaturated defaults.'),
    PromptField(name='Negative prompt', guidance='List only failure modes to exclude - extra fingers, deformed hands, text, watermark, jpeg artifacts, duplicate subjects - and never introduce new content you actually want here.'),
    PromptField(name='Quality target', guidance="State fidelity and finish intent (sharp focus on the eyes, photoreal skin, clean edges, high detail) in terms that don't contradict the chosen style or lens."),
    PromptField(name='Aspect ratio / output', guidance='Set the frame shape to match the composition (4:5 portrait, 16:9 landscape, 1:1 hero) - a mismatched ratio forces the model to crop or stretch the intended layout.'),
)


VIDEO_PRINCIPLES: tuple[str, ...] = (
    'ONE SHOT = ONE SUBJECT + ONE ACTION + ONE CAMERA MOVE. Video models lose coherence when simultaneous motions stack. Give the model a single dominant motion vector per clip; split anything more into separate shots.',
    "GIVE EVERY MOTION A SOURCE AND A DIRECTION. Name what moves, where it starts, and where it goes ('steam rises off the mug and drifts left out of frame'). 'Dynamic, atmospheric motion' tells the model nothing and produces aimless warping.",
    "SEPARATE CAMERA MOTION FROM SUBJECT MOTION EXPLICITLY. State whether the camera moves, the subject moves, or both. When you leave it ambiguous the model fuses them into morphing and melting. 'Locked tripod, subject walks left' is unambiguous; 'moving shot' is not.",
    "ANCHOR THE WORLD SO ONLY THE INTENDED THING MOVES. Everything you do not name as moving should hold still. Call out anchors ('horizon stays level, table stays fixed, background buildings do not drift') to kill the background-melt and object-morph that define AI-slop video.",
    "CONCRETE VERBS WITH WEIGHT AND PACE BEAT ADJECTIVES. 'She sets the cup down slowly, ceramic clicks on wood' gives the model temporal grounding; 'cinematic, epic, dynamic' gives it none. Physics-bearing verbs (pour, flex, tip, drift, settle) are the real motion controls.",
    "EMOTION IS BEHAVIOR, NOT A LABEL. Never write 'she feels sad.' Write the observable tell: gaze drops, shoulders lower, a slow exhale. Models render actions, not internal states - naming the feeling directly produces the frozen-mask 'fake emotion' stare.",
    'CONTRADICTION IS THE NUMBER-ONE FAILURE. Every field must agree. A locked-off tripod cannot also orbit; golden-hour light cannot coexist with neon midnight; a 5-second clip cannot hold three actions. Resolve conflicts before you generate, or the model averages them into mush.',
    'MATCH ACTION COUNT TO CLIP LENGTH. A 5s clip holds roughly one beat. Cramming three actions into 5s forces the model to speed-run and warp. Budget actions against duration, not against ambition.',
    'FRONT-LOAD THE FIRST FRAME. The model commits hardest to what it renders first, so lead the final prompt with subject + primary action + shot size, then modifiers. A buried subject gets a hallucinated opening.',
    'GROUND IT WITH ONE REAL IMPERFECTION AND ONE REAL LENS. The AI-slop signature is plastic skin, hyper-symmetry, and everything in dreamy slow-mo. Counter it with a specified focal length, a named light source, film grain, or a small real-world flaw. Reserve slow-motion for inserts, not whole clips.',
    "PREFER CONCRETE NOUNS OVER VAGUE PRAISE. 'A chipped enamel kettle on scarred oak' constrains the model; 'a beautiful object' hands it freedom to hallucinate. Specificity is control.",
    'THE NEGATIVE PROMPT CARRIES THE ANTI-ARTIFACT LOAD. Push morphing, warping, extra limbs, flicker, embedded text, watermarks, and over-smoothing into the negative field so the positive prompt stays about intent, not about what to avoid.',
    'FOR LOOPS AND CUTS, PLAN THE SEAM. If a clip must loop, keep start and end framing compatible and avoid an irreversible state change. If it will be cut to another shot, choose a cut point on motion (a footstrike, a turn) so the edit reads as continuous.',
)

VIDEO_BLOCK: tuple[PromptField, ...] = (
    PromptField(name='Scene', guidance='One sentence: the single hero subject, the location, and the exact moment being captured - establish one clear subject, not a crowd of equals.'),
    PromptField(name='Character', guidance='Concrete physical description of the main subject - age, build, wardrobe, distinguishing features - written identically every time so the subject holds across shots.'),
    PromptField(name='Action', guidance="The one primary beat as a physical verb with a start and an end and a pace ('lifts, holds, sets down slowly') - a single action sized to the clip length."),
    PromptField(name='Camera', guidance="Shot size + focal length + one camera move (or 'locked'), with the move's speed, and an explicit note on whether the camera itself is static or moving."),
    PromptField(name='Lighting', guidance='Key source + its direction + quality (soft/hard) + time of day + color temperature - one consistent scheme with no competing light logic.'),
    PromptField(name='Environment', guidance="The world around the subject as concrete nouns arranged in foreground / midground / background, plus weather and set dressing - specific objects, not 'a nice room.'"),
    PromptField(name='Motion', guidance='Secondary and ambient motion (what else moves and how), followed by explicit anchors naming what must stay perfectly still to stop background drift.'),
    PromptField(name='Sound', guidance='Diegetic sound sources and ambience that match the action, timed to the beat - for audio-capable models, or as a spec for the edit and music.'),
    PromptField(name='Voiceover', guidance="The exact spoken line with tone and pacing, short enough to fit the duration - or 'none' when the emotion should be carried by behavior alone."),
    PromptField(name='Mood', guidance="The emotional register, expressed through how light and behavior read on screen ('calm, unhurried'), never as an instruction to the actor's inner state."),
    PromptField(name='Style', guidance='Visual treatment - medium, era, film stock, grade, animation style - referencing a named master or public technique for craft only, never a copyrighted title or character.'),
    PromptField(name='Continuity', guidance='The fields that must stay byte-identical across shots - wardrobe, prop, hair, light direction, color grade - so a multi-shot sequence reads as one world.'),
    PromptField(name='Negative prompt', guidance='Artifacts to exclude - morphing, warping, extra limbs, flicker, embedded text, watermark, over-smoothing, and any specific failure this subject invites (e.g. changing shoe color).'),
    PromptField(name='Final generation prompt', guidance="The compressed single-paragraph render string that fuses all fields, front-loading subject + action + shot size, then light, environment, style, and the 'only X moves' anchor."),
)

DURATION_LOGIC: tuple[str, ...] = (
    '5s - ONE BEAT. One subject, one action, one camera move (or none), one location, no cut. A single gesture completes start to finish. Front-load the subject; there is no room for a reaction shot or a second idea.',
    '10s - ONE DEVELOPED MOVE OR TWO LINKED BEATS. A slow push-in that resolves on a detail, or a main action plus a small reaction after it. Still one subject and one location; enough time for a beat to breathe but not to change premise.',
    '15s - A MINI-ARC: setup, action, resolution - or two clean shots joined by a single cut. Room for one reveal. Keep it to one location or one deliberate cut; do not scatter it across many places.',
    '20s - 2-3 SHOTS, SIMPLE A-TO-B PROGRESSION. Establishing + medium + insert, for example. Each sub-shot still obeys the 5s one-beat rule internally; generate them separately and edit, rather than asking for one 20s take.',
    '30s - COMMERCIAL STRUCTURE, 3-6 SHOTS: hook / build / payload / tag. Generate each shot as its own block and cut them together; lock the Continuity fields across all shots; let a music or VO through-line span the cuts to bind them.',
    '60s - FULL SEQUENCE, TREATED AS 8-12 DISCRETE STORYBOARDED SHOTS. Never one continuous generation. Define an act structure, a recurring subject with locked continuity anchors, and a single through-line (music, VO, or a repeating motif) that carries the eye across every cut.',
    'MULTI-SCENE - GENERATE EACH SHOT AS ITS OWN BLOCK. Hold Character, Style, and Continuity fields identical across every block; vary only Scene, Action, and Camera. Keep light direction consistent unless a time jump is intended, and place each cut on motion or on a matched action so the edit reads as one continuous world.',
)



def blank_template(kind: str = "image") -> str:
    """A blank labelled block the director (or a user) fills in."""
    fields = VIDEO_BLOCK if str(kind).lower().startswith("v") else IMAGE_BLOCK
    return "\n".join(f"{f.name}:" for f in fields)


def render_block(values: dict[str, str], kind: str = "image") -> str:
    """Render a filled block, skipping empty fields, in canonical field order."""
    fields = VIDEO_BLOCK if str(kind).lower().startswith("v") else IMAGE_BLOCK
    lines = []
    for f in fields:
        val = str(values.get(f.name, "") or "").strip()
        if val:
            lines.append(f"{f.name}: {val}")
    return "\n".join(lines)
