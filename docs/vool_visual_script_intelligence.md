# VOOL - Visual Script Intelligence Layer

An internal doctrine that turns a rough user idea into a strong, generation-aware prompt or script for AI image/video models, and lifts everyday writing (emails, posts, updates) with the same craft. It is technique, not copied text. **Hard rule:** every prompt must be *generation-aware* - one clear subject, zero contradictions, explicit camera + motion + lighting + scene hierarchy, concrete nouns over vague adjectives.

> Local-tool policy: VOOL runs on the user's own machine and does not apply cloud-style content moderation. Mature and explicit adult themes are supported at the user's requested level. The one and only hard line is that any sexual content depicts adults.


## 1. Image prompt doctrine

**Principles**

- One subject, named first. Front-load the single most important thing in the frame; generation models weight early tokens heavily and will blur, duplicate, or average the image when the lead subject is ambiguous or buried.
- Concrete nouns beat mood adjectives. 'brass lantern, salt-stained wool coat, wet cobblestones' render as pixels; 'epic, stunning, beautiful, masterpiece' render as nothing - they only spend token budget and dilute the real description.
- Zero contradictions. Never pair mutually exclusive instructions (wide shot + macro, noon sun + candlelight, dead-centered + rule-of-thirds, shallow DoF + everything in focus). The model averages conflicting cues into a muddy compromise.
- Every scene needs directed light: give it a direction, a quality (hard/soft), a source, and a color temperature. Lighting is the single biggest lever on realism and mood - name a scheme (Rembrandt, backlit rim, overcast soft) instead of writing 'good lighting'.
- Use camera language the model has actually been trained on: focal length, aperture, shot size, camera height, angle. '85mm, f/2.0, eye-level medium close-up' maps to learned optics and controls depth, compression, and framing far better than 'nice photo'.
- Build a depth hierarchy - foreground, midground, background. Naming what sits at each depth forces the model to compose space instead of flattening every element onto one plane.
- Specify count and spatial relationships. Say 'a single', 'two, side by side', 'to the left of' - vague plurals are where extra limbs, cloned subjects, and merged objects come from.
- Negatives subtract failure modes; they never add content. Put 'extra fingers, deformed hands, watermark, text, jpeg artifacts' in the negative prompt - never describe something you actually want to see there, because many models weight negatives inconsistently.
- Anchor style to a nameable medium, era, technique, or acknowledged master's method - not vibes. 'painterly, flat-layered ukiyo-e depth' or '35mm film grain, muted grade' gives the model a target; 'trending, award-winning, 8k artstation' gives it noise.
- Order the block by importance and keep aspect ratio matched to the composition: subject environment composition lighting lens style emotion detail. Token influence decays with position, so the frame's priorities should decay the same way.


**Image prompt block**

- **Subject:** Name ONE primary subject with concrete attributes - who/what, age or material, build, clothing, pose, expression - so the model knows exactly what to render first and largest.
- **Environment:** Place the subject in a concrete setting with foreground/midground/background cues: location, surfaces, era, weather, time of day - never leave the backdrop implied.
- **Composition:** State framing and layout explicitly: shot size (close-up/medium/wide), subject placement (centered/thirds), camera height and angle, and pick one - not two - spatial scheme.
- **Lighting:** Give the light a direction, a quality (hard/soft), a named source, and a color temperature; specify the scheme (rim, Rembrandt, overcast) so the model doesn't default to flat frontal light.
- **Lens/camera:** Specify focal length, aperture / depth of field, and camera type; use real optical behavior (35mm wide deep focus, 85mm f/1.8 shallow, 100mm macro) to control compression and blur.
- **Style:** Anchor to a nameable medium, era, or technique (photoreal cinematic still, painterly digital, film grain, master's method by name) - banish 'beautiful/epic/masterpiece' filler.
- **Emotion:** State the intended mood in one word plus how it should read visually (tension via hard shadow, calm via soft light), so the model has a target for expression and grade.
- **Details:** Add 2-4 concrete supporting props, textures, or microdetails that reinforce the subject and setting; do not dump unrelated nouns that compete for the model's attention.
- **Color palette:** Name the dominant hues and the accent, ideally as a relationship (cold slate blues against warm amber) - this stabilizes grade and stops oversaturated defaults.
- **Negative prompt:** List only failure modes to exclude - extra fingers, deformed hands, text, watermark, jpeg artifacts, duplicate subjects - and never introduce new content you actually want here.
- **Quality target:** State fidelity and finish intent (sharp focus on the eyes, photoreal skin, clean edges, high detail) in terms that don't contradict the chosen style or lens.
- **Aspect ratio / output:** Set the frame shape to match the composition (4:5 portrait, 16:9 landscape, 1:1 hero) - a mismatched ratio forces the model to crop or stretch the intended layout.

*Example - Cinematic character portrait - the lighthouse keeper (photoreal):*

```
Subject: A weathered lighthouse keeper, late 60s, silver stubble, deep-set grey eyes, wearing a salt-stained navy wool sweater under a yellow oilskin coat, one hand resting on a brass lantern, calm steady gaze toward the camera.
Environment: Inside the lamp room at the top of a stone lighthouse; curved glass panes behind him, a churning slate-grey sea far below, dusk storm clouds massing outside.
Composition: Tight medium close-up, subject on the right-hand thirds line, eye-level camera, head-and-shoulders framing.
Lighting: Warm lantern glow from lower-left as key, cold blue storm light through the windows as rim/back light, soft falloff, a Rembrandt triangle on the shadowed cheek.
Lens/camera: 85mm portrait lens, f/2.0 shallow depth of field, full-frame, background gently defocused.
Style: Photoreal cinematic still, muted maritime color grade, fine film grain.
Emotion: Quiet resilience - steadiness read through the level gaze and relaxed jaw.
Details: Wet individual strands of hair, brass patina and fingerprints on the lantern, faint condensation on the glass, visible wool fiber.
Color palette: Cold slate blues and greys against warm amber lantern highlights.
Negative prompt: extra fingers, deformed hands, text, watermark, plastic skin, oversaturation, duplicate face, blurry eyes.
Quality target: Sharp focus on the eyes, photoreal skin with visible pores, clean natural edges, high detail.
Aspect ratio / output: 4:5 portrait.
```

*Example - Fantasy environment illustration - floating dawn market (stylized):*

```
Subject: A single young vendor in a red hooded cloak arranging glowing paper lanterns on a small wooden boat-stall.
Environment: A market of drifting boats and floating wooden platforms on a calm misty river winding between tall karst cliffs at first light.
Composition: Wide establishing shot, the vendor small in the lower-left third, cliffs framing left and right, camera slightly elevated at a three-quarter angle.
Lighting: Soft pink-gold dawn backlight diffused through mist, warm lantern pools as secondary sources, low overall contrast, gentle volumetric haze.
Lens/camera: 35mm wide, deep depth of field at f/8, everything sharp, with atmospheric perspective softening the far cliffs.
Style: Painterly digital illustration, soft visible brushwork, flat-layered depth in the manner of ukiyo-e woodblock technique, limited palette.
Emotion: Serene wonder - stillness and quiet scale.
Details: Lantern reflections rippling on the water, drifting tendrils of mist, hanging bundles of dried herbs, a wake from one passing boat.
Color palette: Rose and peach sky, teal water, warm amber lantern accents.
Negative prompt: harsh shadows, neon colors, modern buildings, text, signature, cluttered foreground, boats merging into one another.
Quality target: Crisp lantern highlights, cohesive painterly finish, clean silhouette separation between boats.
Aspect ratio / output: 16:9 landscape.
```

*Example - Commercial product still life - ceramic pour-over cup (clean studio):*

```
Subject: A single matte-charcoal ceramic pour-over coffee cup, half-full of black coffee, with a thin curl of steam rising from the surface.
Environment: A minimal light-oak table surface set against a soft seamless warm-grey studio backdrop.
Composition: Centered hero product shot, cup sitting on the lower-third line, slight three-quarter front angle, camera just above rim height.
Lighting: Large softbox key from upper right, white bounce fill on the left, a subtle gradient falloff across the backdrop, one soft specular highlight running down the cup's edge.
Lens/camera: 100mm macro lens, f/5.6 for a fully sharp product with gentle background blur, tripod-steady.
Style: Clean commercial product photography, natural realistic rendering.
Emotion: Calm morning warmth.
Details: Fine steam wisps, subtle glaze texture on the ceramic, a single faint coffee ring on the wood, a soft contact shadow beneath the cup.
Color palette: Warm neutrals - charcoal, oak, greige, and black coffee.
Negative prompt: harsh reflections, blown highlights, busy background, text, logos, extra cups, spilled liquid.
Quality target: Tack-sharp product edges, photoreal materials, clean soft shadow, high resolution.
Aspect ratio / output: 1:1 square.
```

## 2. Video prompt doctrine

**Principles**

- ONE SHOT = ONE SUBJECT + ONE ACTION + ONE CAMERA MOVE. Video models lose coherence when simultaneous motions stack. Give the model a single dominant motion vector per clip; split anything more into separate shots.
- GIVE EVERY MOTION A SOURCE AND A DIRECTION. Name what moves, where it starts, and where it goes ('steam rises off the mug and drifts left out of frame'). 'Dynamic, atmospheric motion' tells the model nothing and produces aimless warping.
- SEPARATE CAMERA MOTION FROM SUBJECT MOTION EXPLICITLY. State whether the camera moves, the subject moves, or both. When you leave it ambiguous the model fuses them into morphing and melting. 'Locked tripod, subject walks left' is unambiguous; 'moving shot' is not.
- ANCHOR THE WORLD SO ONLY THE INTENDED THING MOVES. Everything you do not name as moving should hold still. Call out anchors ('horizon stays level, table stays fixed, background buildings do not drift') to kill the background-melt and object-morph that define AI-slop video.
- CONCRETE VERBS WITH WEIGHT AND PACE BEAT ADJECTIVES. 'She sets the cup down slowly, ceramic clicks on wood' gives the model temporal grounding; 'cinematic, epic, dynamic' gives it none. Physics-bearing verbs (pour, flex, tip, drift, settle) are the real motion controls.
- EMOTION IS BEHAVIOR, NOT A LABEL. Never write 'she feels sad.' Write the observable tell: gaze drops, shoulders lower, a slow exhale. Models render actions, not internal states - naming the feeling directly produces the frozen-mask 'fake emotion' stare.
- CONTRADICTION IS THE NUMBER-ONE FAILURE. Every field must agree. A locked-off tripod cannot also orbit; golden-hour light cannot coexist with neon midnight; a 5-second clip cannot hold three actions. Resolve conflicts before you generate, or the model averages them into mush.
- MATCH ACTION COUNT TO CLIP LENGTH. A 5s clip holds roughly one beat. Cramming three actions into 5s forces the model to speed-run and warp. Budget actions against duration, not against ambition.
- FRONT-LOAD THE FIRST FRAME. The model commits hardest to what it renders first, so lead the final prompt with subject + primary action + shot size, then modifiers. A buried subject gets a hallucinated opening.
- GROUND IT WITH ONE REAL IMPERFECTION AND ONE REAL LENS. The AI-slop signature is plastic skin, hyper-symmetry, and everything in dreamy slow-mo. Counter it with a specified focal length, a named light source, film grain, or a small real-world flaw. Reserve slow-motion for inserts, not whole clips.
- PREFER CONCRETE NOUNS OVER VAGUE PRAISE. 'A chipped enamel kettle on scarred oak' constrains the model; 'a beautiful object' hands it freedom to hallucinate. Specificity is control.
- THE NEGATIVE PROMPT CARRIES THE ANTI-ARTIFACT LOAD. Push morphing, warping, extra limbs, flicker, embedded text, watermarks, and over-smoothing into the negative field so the positive prompt stays about intent, not about what to avoid.
- FOR LOOPS AND CUTS, PLAN THE SEAM. If a clip must loop, keep start and end framing compatible and avoid an irreversible state change. If it will be cut to another shot, choose a cut point on motion (a footstrike, a turn) so the edit reads as continuous.


**Shot / duration logic**

- 5s - ONE BEAT. One subject, one action, one camera move (or none), one location, no cut. A single gesture completes start to finish. Front-load the subject; there is no room for a reaction shot or a second idea.
- 10s - ONE DEVELOPED MOVE OR TWO LINKED BEATS. A slow push-in that resolves on a detail, or a main action plus a small reaction after it. Still one subject and one location; enough time for a beat to breathe but not to change premise.
- 15s - A MINI-ARC: setup, action, resolution - or two clean shots joined by a single cut. Room for one reveal. Keep it to one location or one deliberate cut; do not scatter it across many places.
- 20s - 2-3 SHOTS, SIMPLE A-TO-B PROGRESSION. Establishing + medium + insert, for example. Each sub-shot still obeys the 5s one-beat rule internally; generate them separately and edit, rather than asking for one 20s take.
- 30s - COMMERCIAL STRUCTURE, 3-6 SHOTS: hook / build / payload / tag. Generate each shot as its own block and cut them together; lock the Continuity fields across all shots; let a music or VO through-line span the cuts to bind them.
- 60s - FULL SEQUENCE, TREATED AS 8-12 DISCRETE STORYBOARDED SHOTS. Never one continuous generation. Define an act structure, a recurring subject with locked continuity anchors, and a single through-line (music, VO, or a repeating motif) that carries the eye across every cut.
- MULTI-SCENE - GENERATE EACH SHOT AS ITS OWN BLOCK. Hold Character, Style, and Continuity fields identical across every block; vary only Scene, Action, and Camera. Keep light direction consistent unless a time jump is intended, and place each cut on motion or on a matched action so the edit reads as one continuous world.


**Video prompt block**

- **Scene:** One sentence: the single hero subject, the location, and the exact moment being captured - establish one clear subject, not a crowd of equals.
- **Character:** Concrete physical description of the main subject - age, build, wardrobe, distinguishing features - written identically every time so the subject holds across shots.
- **Action:** The one primary beat as a physical verb with a start and an end and a pace ('lifts, holds, sets down slowly') - a single action sized to the clip length.
- **Camera:** Shot size + focal length + one camera move (or 'locked'), with the move's speed, and an explicit note on whether the camera itself is static or moving.
- **Lighting:** Key source + its direction + quality (soft/hard) + time of day + color temperature - one consistent scheme with no competing light logic.
- **Environment:** The world around the subject as concrete nouns arranged in foreground / midground / background, plus weather and set dressing - specific objects, not 'a nice room.'
- **Motion:** Secondary and ambient motion (what else moves and how), followed by explicit anchors naming what must stay perfectly still to stop background drift.
- **Sound:** Diegetic sound sources and ambience that match the action, timed to the beat - for audio-capable models, or as a spec for the edit and music.
- **Voiceover:** The exact spoken line with tone and pacing, short enough to fit the duration - or 'none' when the emotion should be carried by behavior alone.
- **Mood:** The emotional register, expressed through how light and behavior read on screen ('calm, unhurried'), never as an instruction to the actor's inner state.
- **Style:** Visual treatment - medium, era, film stock, grade, animation style - referencing a named master or public technique for craft only, never a copyrighted title or character.
- **Continuity:** The fields that must stay byte-identical across shots - wardrobe, prop, hair, light direction, color grade - so a multi-shot sequence reads as one world.
- **Negative prompt:** Artifacts to exclude - morphing, warping, extra limbs, flicker, embedded text, watermark, over-smoothing, and any specific failure this subject invites (e.g. changing shoe color).
- **Final generation prompt:** The compressed single-paragraph render string that fuses all fields, front-loading subject + action + shot size, then light, environment, style, and the 'only X moves' anchor.

*Example - Morning Pour-Over - 10s product macro (single continuous shot):*

```
Scene: A slow pour of hot water over fresh coffee grounds in a clear glass dripper on a kitchen counter at dawn.
Character: No human; the hero subject is a clear glass pour-over cone half-full of dark, blooming grounds, with a thin steel gooseneck kettle spout entering the top of frame.
Action: The spout pours one thin, steady, slowly spiraling stream over the grounds; the coffee bed blooms and swells, then settles as the pour stops. One beat.
Camera: Extreme close-up macro, 100mm, shallow depth of field; camera locked on a tripod, no move; focus fixed on the pour point.
Lighting: Soft low side-key from a window at camera-left, warm 3200K dawn light, gentle falloff into shadow at camera-right, a bright rim catching the rising steam.
Environment: Sharp wooden counter in the foreground, scattered coffee grounds, a warm blurred kitchen behind, a faint ceramic-mug bokeh deep in back.
Motion: Steam rises and drifts slowly up and to the left, out of frame; only the water stream and the blooming bed are in sharp motion; counter, kettle body, and background stay fixed.
Sound: A soft trickle of water, a faint hiss of steam, quiet kitchen room tone.
Voiceover: None.
Mood: Calm, unhurried, warm early-morning ritual - carried by the slow pace and low warm light.
Style: Photoreal tabletop food-commercial macro, filmic grade, subtle grain, natural color; technique after classic product cinematography.
Continuity: Kettle finish, counter grain, light direction, and grind texture stay identical if intercut with any wider shot.
Negative prompt: no hands, no morphing or splitting liquid, no warping kettle, no extra spouts, no text, no watermark, no flicker, no plastic over-smoothing.
Final generation prompt: "Extreme macro, 100mm shallow focus, locked tripod: a thin steel gooseneck kettle pours one slow spiraling stream of hot water over dark coffee grounds blooming in a clear glass pour-over cone; steam rises and drifts up-left out of frame; warm 3200K dawn side-light from camera-left, soft shadow at right; sharp wooden counter foreground, blurred warm kitchen behind, filmic grain, photoreal food-commercial grade. Only the water, the bloom, and the steam move; everything else stays perfectly still."
```

*Example - The Held Breath - 5s character beat (behavior-driven emotion, no dialogue):*

```
Scene: A woman alone at a rain-streaked window finishes reading a folded note and lets her hand lower.
Character: A woman in her early 30s, dark hair loosely tied back, pale-grey knit sweater, a thin silver ring on her right hand; features and wardrobe kept consistent.
Action: She reaches the end of the note, her eyes lower away from it, and she exhales slowly as her shoulders drop a fraction. One beat, no cut.
Camera: Medium close-up, 50mm, eye level; an almost imperceptible slow push-in, otherwise near-locked.
Lighting: Soft overcast window key from camera-left, cool 5600K, low contrast, gentle wrap; a faint blue fill bouncing off the wet glass.
Environment: A rain-streaked window behind her, a soft out-of-focus grey room, one warm lamp bokeh deep in the background for contrast.
Motion: Rain runs down the glass behind her in slow diagonal streaks; only her breath and eye movement move on the subject; head and torso otherwise steady; background static except the rain.
Sound: Muffled rain on glass, a low room tone, one quiet exhale timed to the shoulder drop.
Voiceover: None - the emotion is carried by the dropped gaze and the exhale, not by words.
Mood: Quiet relief and resignation held together - shown, never labeled on the face.
Style: Naturalistic drama, muted desaturated palette, soft film grain, shallow depth of field; technique after available-light portrait cinematography.
Continuity: Sweater, ring, hair, window position, and cool light direction stay identical across every shot in the scene.
Negative prompt: no smile-to-neutral morph, no face warping, no extra fingers, no shifting eye color, no text, no watermark, no jitter, no over-smooth skin.
Final generation prompt: "Medium close-up, 50mm eye-level, an almost imperceptible slow push-in: a woman in her early 30s, dark hair tied back, grey knit sweater, finishes reading a folded note, lowers her eyes and exhales slowly as her shoulders drop a fraction; soft cool overcast light from a window at camera-left, rain streaking the glass behind her in slow diagonals; muted desaturated grade, soft film grain, shallow focus. Only her breath, her eyes, and the rain move; the rest holds still."
```

*Example - First Light Run - 30s multi-scene spot (4 shots, generate separately and edit):*

```
Scene: A runner's dawn training session across a city just waking up, told in four short shots.
Character: A lean runner, late 20s, short curly hair, teal running jacket, black shorts, grey trainers with a bright coral sole - identical in every shot.
Action: Shot 1 (0-6s) laces pulled tight, close on the trainer. Shot 2 (6-14s) she runs along an empty riverside path. Shot 3 (14-22s) her foot strikes wet pavement, coral sole flexing, brief slow-mo insert. Shot 4 (22-30s) she eases to a walk at a stone bridge as the sun clears the skyline. One beat per shot.
Camera: S1 macro, locked. S2 lateral tracking dolly matching her pace, 35mm. S3 high-speed macro, 85mm, slight slow-motion. S4 wide crane-up, 24mm, slow rise. One move per shot.
Lighting: Consistent low golden-hour key from the east (camera-right through the run), warm 3400K, long soft shadows, a bright rim on the runner - same direction in every shot.
Environment: An empty riverside path, pavement still wet from overnight rain, a low city skyline, thin morning mist, a stone bridge for the final beat.
Motion: The runner is the moving subject in each shot; ambient motion is drifting mist and a lightly rippling river; anchor the skyline and path geometry so backgrounds do not drift; cut on the footstrike between shots.
Sound: Rhythmic footfalls, steady breath, a distant early-city hum, a warm synth pad rising under the cuts.
Voiceover: One original line at 24s, calm and low - "Before the city, there's this."
Mood: Earned, meditative momentum - built from pace, warm light, and rhythm, not from slogans.
Style: Photoreal athletic-spot look, warm filmic grade, subtle grain, crisp motion; technique after modern sports cinematography.
Continuity: Runner's wardrobe, trainer and coral-sole color, hair, golden light direction, and grade stay identical across all four shots; the VO and synth pad span the cuts as the through-line.
Negative prompt: no changing shoe or sole color, no morphing limbs, no extra runners, no warping skyline, no floaty slow-mo on the non-insert shots, no text overlays, no watermark, no flicker.
Final generation prompt (Shot 2, generate each shot on its own with these locked values): "Lateral tracking dolly, 35mm, matching her pace: a lean late-20s runner in a teal jacket, black shorts, and grey trainers with a bright coral sole runs along an empty wet riverside path at golden hour; warm low east light from camera-right, long soft shadows, thin morning mist drifting, a low city skyline behind; photoreal warm filmic grade, subtle grain. Only the runner and the mist move; skyline and path stay fixed. Cut to and from this shot on a footstrike."
```

## 3. Prompt upgrader / rewrite engine

- **1. Normalize the brief** - Strip filler and vibe-adjectives, pull out the concrete nouns, and lock exactly ONE subject. Resolve ambiguity in the subject noun by choosing the most render-friendly reading and stating it out loud (e.g. 'dragon girl' = a young woman with dragon traits, not a dragon shaped like a girl). Kill contradictions before they reach the model: two protagonists fighting for focus, 'sunny midnight,' 'wide close-up.' A model cannot render a contradiction; it averages it into mud.
- **2. Infer genre, intent, emotion, and the visual hook** - From the sparse words, fix four things the user left blank: genre (e.g. dark fantasy), dominant emotion (e.g. menace + melancholy), delivery format (still vs 10-20s clip, aspect ratio), and - most important - commit to ONE visual hook: the single memorable shot the whole prompt exists to deliver. Everything downstream serves that hook. If you can't name the hook in one sentence, the prompt has no spine.
- **3. Build the scene hierarchy** - Layer the frame explicitly: name what occupies foreground, midground, and background. Choose shot size (close/medium/wide) and composition (thirds placement, leading lines, negative space) so the model knows what is primary and what is texture. One focal point; everything else supports it. Undirected scenes render as cluttered, competing-detail noise.
- **4. Specify camera and lens** - Give a real optical setup instead of the word 'cinematic': focal length, depth of field, camera height and angle, and - for video - ONE deliberate camera move with a defined start and end. Do not stack conflicting moves (a push-in that also orbits and cranes). Concrete optics ('40mm anamorphic, shallow DoF, low angle, slow dolly-in') give the model a lens to simulate; adjectives give it nothing to compute.
- **5. Direct light, color, and atmosphere** - Name the key light source and its direction, then fill, rim, color temperature, and any volumetric medium (fog, ash, god-rays). Lock a palette of 2-3 colors. Light direction is the single biggest lever on mood and depth - state where it comes from, never leave it to chance. A self-lit subject (chest-glow, torch) is stronger than 'dramatic lighting' because it tells the model exactly which planes to brighten.
- **6. Choreograph motion and beats (video)** - Break the 10-20s into 3-4 timed beats with explicit second ranges. Each beat states what the subject does + what the camera does + how the light shifts. Build toward a payoff beat - the hook from step 2 - so the clip has a setup, a turn, and a resolution instead of a looping idle. Beats prevent the two classic video failures: a static talking-statue, or aimless drift that never arrives anywhere.
- **7. Design sound (video)** - Layer three tracks: diegetic sound tied to subject actions, an ambient bed for the environment, and a score cue that enters and exits on purpose. Anchor key sounds to specific beats so audio and motion hit on the same frame (wings open = one deep leathery whoomph). Silence used deliberately (score cutting out on the final frame) is a tool, not an absence.
- **8. Write the failure-prevention negative prompt** - Pre-empt this subject's known failure modes, not a generic dump. For a winged humanoid: malformed/extra fingers and hands, asymmetric or cross-eyed faces, duplicate or extra limbs and extra wings, morphing/melting geometry and flicker/strobe on video, text/watermark/subtitle artifacts, and off-style drift (cartoon, chibi, plastic skin, oversaturation). Negatives are chosen from the subject and medium, so each prompt gets its own list.
- **9. Assemble and order the final prompt** - Front-load subject and hook, then scene hierarchy, camera, light, motion, sound, and negatives last. Token order signals priority to most models - the thing that must survive goes first. Final pass: one subject, zero contradictions, concrete nouns over adjectives, every token pointing at the same picture so the model has nothing to average away.
- **10. Deliver the why-stronger brief** - In two or three plain sentences, tell the user what was added and why the model now renders it better - the ambiguity that got resolved, the light/motion/hook that got specified. This teaches the moves so the user steers the next idea themselves instead of depending on the rewriter.


**Worked example** - input: `make dragon girl in dark castle`


Missing details to infer:

- What 'dragon girl' means - resolve to a young woman with dragon traits (charcoal scales on cheekbones/forearms, obsidian horns, vertical-slit amber eyes, leathery wings, tail), not a literal dragon
- Genre and emotional register (dark fantasy; menace + melancholy) - the raw input names none
- Which part of the castle, its era and materials (ruined vaulted throne hall, cracked basalt throne, broken stone, mountain fortress at night)
- Time of day and - critically - the light source (night; self-lit chest-glow as key, cold moonlight rim from a shattered window)
- Color palette (cold slate-blue/black, punctuated by molten orange/gold on ignition)
- Camera, lens, and the one camera move (40mm anamorphic, shallow DoF, slow dolly-in resolving to a crane-up reveal)
- What she actually DOES - the motion arc and its payoff beat (turn, ignite, wings unfurl, ember exhale, fade)
- Duration and aspect ratio (16s, 2.39:1)
- Sound design (sub-bass drone, wind, wing whoomph, ember hiss, a single cello swell that cuts to silence)
- Subject-specific failure modes to negate (extra wings, malformed hands, morphing, flicker)

Inferred: Genre: dark fantasy. Emotion: menace laced with melancholy. Format: 16s clip, 2.39:1. Visual hook: she stands grief-still before a ruined throne, then her own chest-fire builds and ignites every scale to molten gold as her wings unfurl - then the glow dies and the hall goes cold again. One transformation, grief-still to alive-and-dangerous, is the whole clip.

Final image prompt:

```
Dark fantasy portrait, single subject: a young dragon-blooded woman standing before a cracked basalt throne in a ruined mountain-castle hall at night. Matte charcoal scales tracing her cheekbones and forearms, two backswept obsidian horns, vertical-slit amber eyes, tattered leathery wings half-open behind her, soot-stained bronze-scaled gown. Three-quarter medium shot, 85mm portrait lens, shallow depth of field, low angle. Lighting: warm ember glow rising from her chest under-lighting her jaw and scales, cool blue moonlight rim from a shattered window behind, volumetric god-rays through drifting ash. Cold slate-blue and black palette with molten-gold accents on the scales. Physically based skin and scale texture, dramatic chiaroscuro, ultra-detailed, cinematic, 4k. NEGATIVE: extra fingers, malformed hands, warped face, asymmetric eyes, extra wings, duplicate limbs, text, watermark, blurry, plastic skin, cartoon, chibi, oversaturated.
```

Final video prompt:

```
SUBJECT (one, primary): a young dragon-blooded woman - matte charcoal scales tracing her cheekbones and forearms, two backswept obsidian horns, vertical-slit amber eyes, tattered leathery wings folded at her back, tail, wearing a soot-stained bronze-scaled gown. SCENE: a ruined vaulted throne hall of a mountain fortress at night; collapsed ceiling open to a starless sky, broken basalt pillars, a cracked basalt throne behind her, cold ash drifting. Foreground: drifting embers and ash. Midground: the woman. Background: the cracked throne and a shattered window. FORMAT: 16s, 24fps, 2.39:1 anamorphic. CAMERA: 40mm anamorphic equivalent, shallow depth of field, low angle, subtle handheld micro-shake. BEATS - 0-4s: slow dolly push-in from behind the throne; she stands motionless facing the shattered window, wings folded, embers floating. 4-9s: she turns her head toward camera; a low orange glow builds in her chest and throat, under-lighting her jaw and scales. 9-13s: her wings unfurl in one deliberate sweep, throwing ash into the air; camera cranes up and back to reveal the full wingspan against the throne. 13-16s: she exhales a slow plume of ember-lit smoke curling toward the lens; firelight ignites every scale to molten gold at the peak, then the glow fades and the hall drops back to cold blue (payoff: grief-still to alive-and-dangerous). LIGHTING: key = warm ember chest-glow from below; rim = cool blue moonlight from the broken window behind; volumetric god-rays through drifting ash; deep shadows. PALETTE: cold slate-blue and black, molten orange/gold only during ignition. SOUND: low sub-bass drone under the hall; distant wind through broken stone; a single deep leathery whoomph as the wings open; a soft ember hiss on the exhale; no score until 9s, then a slow low cello swell that cuts to silence on the final cold frame. NEGATIVE: extra fingers, fused or malformed hands, warped face, asymmetric or cross-eyed eyes, duplicate limbs, extra wings, floating limbs, morphing or melting geometry, flicker, strobe, jitter, text, watermark, logo, subtitles, blurry, low-res, plastic skin, cartoon, chibi, oversaturated, background people.
```

Why stronger: The raw input names a subject and a place but no shot, light, motion, mood, or focal moment, so a model would guess and average everything into a generic muddy fantasy frame. The upgrade locks ONE subject with concrete render-able nouns (slit amber eyes, obsidian horns, charcoal scales) instead of the ambiguous 'dragon girl,' hands the model a single unambiguous light source and direction (self-lit chest-glow key plus moon rim) that carves depth and mood, sets one lens and one camera move, and - for video - gives a beat-timed arc with a clear payoff (grief-still to ignited to cold) plus sound cues that hit on the same frames as the motion. The subject-specific negative prompt pre-empts the exact failure modes for a winged humanoid (extra wings, malformed hands, morphing). Every token now points at the same picture, so the model has nothing to average away.

## 4. Scoring / doctor layer

Scale: Every dimension is scored 1-10 on one shared anchor: 1-3 = absent or broken (the model has nothing usable, or cues actively conflict); 4-5 = present but weak (it renders, but generic and under-specified - the model fills the gaps by averaging); 6-7 = functional (ships, one clear read, still a bit stock); 8-9 = strong and model-ready (specific, front-loaded, nothing left for the model to guess); 10 = exemplary (specific AND original AND clean - good enough to keep as a reference prompt). Overall is a WEIGHTED mean, not a flat average, because the dimensions do not matter equally to whether a model renders well. Weights: model_compatibility x1.5, coherence_no_contradictions x1.5, visual_clarity x1.4, scene_motion x1.3 (video) or originality x1.3 (stills), anti_ai_slop x1.2, cinematic_strength x1.1, all others x1.0. Three dimensions are HARD GATES: model_compatibility, coherence_no_contradictions, and safety (an implicit pass/fail that sits under the rubric). If any gate scores below 6, the overall is capped at 6.0 no matter how high everything else scores - a beautiful prompt the model cannot parse, or one that contradicts itself, is not shippable. Score stills and video on the same card, but for a still image DROP scene_motion and sound_voice_usefulness from the weighted mean entirely (do not score them 1 and drag the average down for motion a still cannot have).

Threshold: Ship threshold: overall >= 8.5 AND no single scored dimension below 6 AND all three gates (model_compatibility, coherence_no_contradictions, safety) >= 7. Zones: 9.0-10 ship as-is; 8.5-8.9 ship but name the weakest dimension for the user; 7.0-8.4 rewrite only the flagged facets, do not regenerate the whole prompt; 5.0-6.9 targeted rewrite of every sub-6 dimension, then re-score; below 5.0 discard the draft and rebuild from the user's original intent. Safety is absolute and overrides every number: any unsafe content (minors in sexual or graphic-violent context, nonconsensual likeness of a real person, or real-world illegal how-to) is an instant 0 and blocks output regardless of all other scores - redirect, never ship-with-a-warning. Cap the auto-rewrite at 2 passes: if the prompt still misses 8.5 after two rewrites, return the best version plus a one-line note on the unresolved weakness rather than looping.


**Dimensions (1-10)**

- **visual_clarity** - high: One unambiguous subject the model can lock onto; foreground/midground/background each named so the model knows what goes where. A reader could sketch the frame from the words alone. / low: Subject undefined or two subjects competing for the same frame. The model averages the guess into mush, or renders the wrong thing centered.
- **originality** - high: A specific, unexpected combination, angle, or detail the target model has not seen ten thousand times. Feels authored, not sampled. / low: Stock defaults every model overfits to (rain-slick neon cyberpunk alley, lone astronaut on a dune). Renders competently and forgettably.
- **scene_motion** - high: For video: explicit physical action for the subject PLUS a directed camera move with direction and pace (slow push-in, whip-pan left, crane up). Something actually changes across the clip. / low: Static tableau. Verbs are 'stands,' 'is,' 'sits.' Camera unspecified, so the model holds a near-frozen shot or invents drift.
- **cinematic_strength** - high: Intentional camera language that serves the shot: shot size, lens/focal feel, and one composition choice (thirds placement, leading lines, foreground occlusion, depth staging). / low: Snapshot framing, no lens or blocking cues, flat and centered. Nothing tells the model how to see the subject.
- **emotional_hook** - high: A clear feeling carried by a visible cue - a facial expression, a gesture, a lighting contrast, a color temperature - not by naming the emotion. / low: Decorative and cold, no stakes. Or the emotion is asserted as an abstract word the model cannot render ('a feeling of hope').
- **genre_fit** - high: Lighting, palette, pace, wardrobe, and grain all consistent with the stated genre; the signals reinforce each other. / low: Genre named but the cues contradict it (horror described in cheerful noon daylight with no tension element). Model splits the difference and lands nowhere.
- **model_compatibility** - high: Syntax and length the target generator parses cleanly; subject and style front-loaded; no asks the model reliably fails (legible long text, exact object counts, ignored negations). / low: Novelistic paragraph, buried subject, in-image text, exact counts, or negations mixed into a positive prompt. The model drops half of it.
- **production_realism** - high: Achievable in a single generation: plausible physics and scale, one continuity, an action count a clip can actually show. / low: Needs an edit bay, not a generator - six simultaneous complex actions, impossible continuity, scale contradictions the model cannot resolve in one pass.
- **sound_voice_usefulness** - high: For audio-capable video: diegetic sound tied to on-screen action, one ambience layer, and VO/dialogue tone described in context (tempo, instrument, mood). / low: Absent, or generic 'epic music' - unusable direction that gives the audio model nothing specific to synthesize.
- **coherence_no_contradictions** - high: Time of day, light source, weather, season, and wardrobe all agree. Every cue points the same direction. / low: Internal contradictions (golden-hour sunset plus overhead noon shadows, rain plus dry ground). The model picks one at random per generation.
- **anti_ai_slop** - high: Concrete nouns and specific verbs; every word changes what renders. Reads like a human director's shot note. / low: Adjective soup and quality-tag filler ('stunning, breathtaking, ultra-detailed, 8k, masterpiece'), hedges, or tag-salad with no actual scene.

**Auto-rewrite triggers**

- if Weak or absent motion: scene_motion < 6 on a video prompt - subject verbs are static ('stands', 'is', 'sits') and no camera move is specified. -> Inject one concrete physical action for the subject plus one directed camera move with a pace (slow push-in, track left, tilt up), so both subject and frame actually change across the clip.
- if Too vague: visual_clarity or anti_ai_slop low - adjective-heavy with no concrete nouns, setting/time/texture unnamed. -> Replace each vague adjective with a specific noun, material, or measurable detail ('floor-length crimson silk gown', not 'beautiful dress'); name the setting, the time of day, and one load-bearing texture.
- if Overloaded: production_realism or coherence low because too many subjects, actions, or clauses are crammed into one shot. -> Compress to one subject + one primary action + one setting; split any remaining action into a separate shot; cut adjectives to the ones that change the render and front-load the subject.
- if Generic AI voice: anti_ai_slop < 6 - tag-salad or 'stunning / breathtaking / ultra-detailed / 8k / masterpiece' filler and hedge words. -> Delete the quality-tag filler and hedges; rewrite as a plain declarative scene a human director would speak; keep only words that change what the model renders.
- if Contradiction: coherence gate < 7 - conflicting lighting, time of day, weather, or scale cues. -> Pick one time-of-day, one light source, and one weather, then make every other cue agree; delete the losing half of each conflicting pair.
- if Model-incompatible: model_compatibility gate < 7 - novel-length paragraph, buried subject, in-image text, exact counts, or negations inside the positive prompt. -> Trim to the target model's parse window, front-load subject and style, move negations to a proper negative-prompt field or restate them positively, and drop asks the model reliably fails (legible long text, exact object counts).
- if Flat, no camera language: cinematic_strength < 6. -> Add a shot size (wide / medium / close), a lens or focal feel, and one composition choice (thirds placement, leading lines, foreground occlusion) that serves the subject.
- if No emotional read: emotional_hook < 6 - the feeling is named in the abstract or absent. -> Attach the intended feeling to a concrete visible cue - a facial expression, a gesture, a lighting contrast, or a color temperature - instead of stating the emotion as a word.
- if Dead audio: sound_voice_usefulness < 6 on an audio-capable video - silent or 'epic music'. -> Specify diegetic sound tied to the on-screen action, one ambience layer, and VO/dialogue tone if any; replace 'epic music' with a described cue (tempo, instrument, or mood-in-context).
- if Unsafe: any minors in a sexual or graphic-violent context, nonconsensual likeness of a real named person, or real-world illegal how-to. -> Hard-block the render - do not soften and ship. Redirect to a clearly adult, fictional, lawful version that preserves the legitimate creative intent (re-age to a stated adult, swap a real person for an original character) and state the swap plainly.

## 5. Genre playbooks


### Fantasy - Epic / High / Dark

**Visual ingredients:** One clear hero subject with a readable silhouette and lived-in, weathered costume; A single dominant motivated light source (torch, moon, arcane rift, god-ray); One accent magic hue that casts colored bounce onto nearby faces and surfaces; Three depth planes - foreground / midground / background - with haze between them; A scale anchor: a small figure against huge architecture, landscape, or creature; Tactile named materials: worn leather, forged and pitted steel, mossy stone, tarnished gold, rain-wet rock; Practical atmospherics: mist, drifting embers, dust motes, rain, spores; Fantastical architecture or flora grounded in a real historical/biological reference so it reads as plausible

**Motifs:** A lone figure dwarfed by a colossal ruin, gate, tree, or beast; Runes and sigils igniting in the air with ember-fall; God-rays raking through broken cathedral vaulting or forest canopy; A relic weapon or staff as the single bright point of the composition; Ancient stone reclaimed by moss and roots; bones half-sunk in mud (dark fantasy); Banners and standards over a distant marching host under storm light; A bioluminescent forest or debris orbiting a spell mid-cast; A throne, altar, or threshold/doorway opening onto somewhere far older

**Cliches to avoid:** Buzzword soup ('epic fantasy masterpiece 8k trending on artstation') instead of concrete description; Rainbow multicolor magic with no single hue and no light logic; Shiny plastic CGI armor and weapons with zero wear or weight; The identical stock bald-bearded grey wizard / generic chosen-one; Overcrowded frame with no clear focal subject; Neon glow slapped on everything with blown-out HDR halos; Symmetrical faces, dead doll eyes, stiff mannequin poses; Lazy purple-nebula skies used as a shorthand for 'fantasy'; Cartoonishly oversized pauldrons and spikes that break the silhouette

**Camera:** Fantasy wants WEIGHT, not energy - the camera behaves like it's in awe of something old. Shot sizes: extreme-wide/establishing to sell scale (a lone figure dwarfed by architecture, landscape, or a creature - the core myth-weight move); medium and medium-close for character gravity; inserts on hands, sigils, eyes, a relic edge. Lenses: 24-35mm for vistas and looming architecture (mild wide distortion reads as scale), 50-85mm for portraits of a king/witch/warlord to compress and dignify the face, anamorphic for the wide epic frame plus oval bokeh that flatters glowing magic. Angles: low angle to enthrone a figure, a tower, or a gate; high/aerial to shrink a hero against a battlefield or wasteland; Dutch tilt used sparingly for dread in dark fantasy - never as a default. Movements (video): slow push-in to build charging power, crane-up or tilt-up for a world-scale reveal, a patient orbit around a casting figure, a tracking dolly alongside a marching host, locked-off stillness for ritual and altars. Ban fast whip-pans and shaky action-cam; motion should feel inevitable, like a tide.

**Lighting & colour:** Lighting IS the emotion here - decide the feeling, then motivate one dominant source. High/epic fantasy: warm golden-hour god-rays, torch-amber interiors, silver moonlight - single dominant key, generous but directional. Dark fantasy: low-key chiaroscuro, deep crushed shadows, cold overcast or one guttering flame, heavy negative fill, separation carried by a thin rim light. Rule that sells magic as real: the spell should be nearly the ONLY saturated thing in an otherwise restrained frame - pick ONE accent hue (glacial cyan, ember orange, necrotic green, blood crimson) and let it spill colored bounce onto the caster's face, hands, and nearby stone; magic that lights its surroundings looks physical, magic that just glows on its own looks pasted on. Palettes: high fantasy = warm ambers + forest greens + tarnished gold; dark fantasy = slate, char, bone, rust, plus one bruise of accent color. Always specify light DIRECTION and give a backlight/rim for separation from murky backgrounds. Use atmospheric perspective - value and saturation fading with distance - plus volumetric haze, god-rays through vaulting or canopy, and dust/ember motes to build scale and myth.

**Sound & voice:** Sound design (video): sparse and low. A sustained sub-bass drone for dread and scale, distant rolling thunder, steady wind over rock, chainmail and leather rustle, stone grind. Magic should be a low resonant hum with a crackle-and-whoosh on ignition - never a sci-fi laser zap. Use silence as a tool: pull the mix to near-nothing in the beat before a spell lands, then hit the impact. Score: a single lonely instrument for myth-weight - cello, war-horn, low choir drone, a struck bell - not a wall-of-orchestra bed. Voice/dialogue: measured, low, unhurried; incantations in a slow half-whispered cadence with weight on the consonants; narration (if any) grave and understated. Avoid cartoonish 'ye olde' accents and rushed delivery - let ambience plus one hero sound carry the beat.

**Steering vocabulary:** myth-weight, weathered, windswept, colossal scale, volumetric god-rays, chiaroscuro, low-key lighting, rim-lit / backlit, anamorphic, motivated single light source, guttering torchlight, runic sigils, desaturated palette with one accent hue, tactile forged steel, moss-choked ancient stone, atmospheric haze / atmospheric perspective, filmic grain, painterly realism, solemn, ominous, enthroned low angle, god-rays through broken vaulting

**Example idea:** a wizard on a mountain casting a spell, epic fantasy

- Bad: epic fantasy wizard casting a powerful spell on top of a mountain, highly detailed, 4k, 8k, ultra HD, masterpiece, trending on artstation, beautiful, stunning, intricate, hyperrealistic, magical, colorful glowing magic everywhere, cinematic lighting, best quality

- Improved: An old sorcerer in weathered grey wool robes stands on a windswept granite peak at dusk, raising a gnarled oak staff as pale blue arcane light gathers at its tip. Storm clouds mass behind him. Wide shot, low angle, 35mm, cold slate-and-teal palette with a single cyan magic accent, dramatic rim light from the sky behind him, drifting mist, painterly realism.

- Elite (video):

```
Single subject: a weather-worn sorceress, mid-40s, silver-streaked black hair whipping in the wind, wearing layered charcoal wool and a rain-darkened leather mantle, standing at the crumbling edge of a granite cliff. She grips a cracked obsidian staff in both hands and drags it slowly upward; a lattice of pale cyan runes ignites in the air around the staff head, embers drifting down.

Composition (clear hierarchy): FOREGROUND - wet black rock and a few storm-bent heather stalks trembling in gusts. MIDGROUND - the sorceress, three-quarter back view turning toward camera, sharp focus. BACKGROUND - a vast valley swallowed by an advancing wall of grey thunderheads, distant lightning threading the clouds, fading to atmospheric haze for depth.

Camera: 35mm anamorphic look, eye-level, slow push-in from medium-wide to medium over 8 seconds with a subtle handheld sway; shallow depth so the runes bloom into soft oval bokeh.

Lighting: cold overcast key from the storm behind her (backlit rim on hair and staff for separation from the dark valley); the ONLY warm-ish fill is a faint cyan underlight thrown onto her jaw and hands by the runes; deep crushed shadow in the folds of the mantle. Palette: slate grey, gunmetal, bone, one accent of glacial cyan - everything else desaturated.

Motion: hair and mantle ripple continuously in gusting wind; runes rotate slowly and pulse once as she completes the gesture; a fine sheet of rain drifts left to right; a single distant lightning flash on the final beat.

Atmosphere: blowing mist, drifting embers, volumetric light raking through the rain. Mood: solemn, myth-weight, the calm before ruin. Photoreal, filmic grain, natural physics, one time of day, one weather system, sane hands and staff geometry.
```

**Negative prompt:** buzzword stacking (8k, masterpiece, trending on artstation), extra fingers, malformed hands, warped or bent staff, duplicate faces, floating disconnected limbs, plastic waxy skin, oversaturated neon, rainbow multicolor magic soup, cartoon glow, blown-out HDR halos, game UI, health bars, spell-icon overlays, watermark, text, logo, signature, modern clothing, wristwatch, zippers, shiny plastic armor with no wear, oversized pauldrons, symmetrical twin face, dead doll eyes, mannequin pose, cluttered frame with no focal subject, muddy flat low-contrast lighting, purple-nebula sky filler, motion blur smear, wrong-scale architecture

**Checklist:**
- One unmistakable subject with a clean, readable silhouette?
- Single dominant motivated light source, plus a rim/backlight for separation from the background?
- Magic limited to ONE accent hue that spills colored light onto nearby surfaces?
- Three depth planes (fg/mg/bg) with atmospheric haze to build scale?
- A scale anchor present so the world reads as huge?
- Concrete materials named (leather, steel, stone, gold) - not just 'highly detailed'?
- Zero contradictions - one time of day, one weather system, one lens?
- Camera fully specified: shot size + lens + angle + (video) one motion?
- Hands, fingers, and weapon/staff geometry sane and unbroken?
- Palette restrained everywhere except the deliberate magic accent?

### Science Fiction

**Visual ingredients:** a single human figure as a scale anchor against something vast; a believable engineered object with panel lines, wear, seams, and greebles (not smooth toy plastic); motivated practical light sources - glyph glow, console light, running lights, star/sun; atmosphere: dust, fog, or haze to carry volumetric light and depth; an environment that implies a whole world - geology, weather, alien flora, or a skyline; physics cues that read as real: cast shadows, reflections on the visor, footprints, floating debris in zero-g

**Motifs:** a lone monolith or ship silhouetted against a double sun or a ringed planet low on the horizon; dust or smoke hanging in a hard shaft of light; an alien landscape curved and reflected in a gold spacesuit visor; dense conduit, cable, and pipework texture on hull interiors; hazard-stripe warning paint and stenciled serial numbers on machinery; bioluminescent alien plant life glowing in the dark; frost and condensation creeping across a suit or a viewport; a single holographic readout floating in otherwise dim air; brutalist concrete-and-steel corridors receding into shadow; distant ship running-lights as soft bokeh in a hazy sky

**Cliches to avoid:** default cyberpunk teal-and-magenta neon rain on every night scene; chrome everything and spotless Apple-store-white interiors with no visible function; blue holograms floating over every surface as set dressing; lens-flare abuse as a substitute for lighting design; the lone astronaut shot from behind, back-to-camera, as the only idea in the frame; a ringed planet slapped into the sky for no in-world reason; franchise icons standing in for design (energy swords, sandworms, faction badges); gibberish 'alien' text that's just warped English; sleek slick tech with zero wear, weight, or wiring; 'epic masterpiece 4k trending' tag salad instead of an actual scene; brains in jars / generic 'AI = glowing blue orb'

**Camera:** Sci-fi lives on scale contrast - one small human against something vast, engineered, and older or larger than them. Lead with a WIDE ESTABLISHING shot on a 21-35mm lens (anamorphic if the model supports the flavor) to fit a megastructure and a human in one frame; place the human on a third, low in frame, with deep negative space above to sell size. Shoot the big object from a LOW ANGLE looking up for monumentality. Use a slow PUSH-IN (dolly, not zoom) for wonder/reveal, a slow PULL-BACK to disclose scale (start tight on a face/detail, retreat to expose the environment). For hard-sci-fi cityscapes/fleets use a LONG lens (85-135mm) to compress ships or towers into stacked layers. Reserve a DUTCH tilt for instability/threat only - never as default style. Interiors: tight over-the-shoulder in corridors, handheld micro-shake for tension, locked-off tripod for cold clinical control rooms. For video, commit to ONE move per shot (push, pull, orbit, or crane) and state its speed; a drifting orbit around a docked ship or a vertical crane up a tower both read instantly as sci-fi.

**Lighting & colour:** Every light must be motivated by something in the scene (a sun, a glyph, a console, running lights) - no floating fill from nowhere. Build on cold/warm contrast: cold void or cold tech against a warm human/practical source, or invert it (warm sunset world vs cold blue machine). Give the hero a hard rim/edge light to separate them from a dark environment. Use volumetric shafts through haze to carve depth. Hold a LIMITED palette - desaturated neutral base plus ONE accent hue for the tech (cyan, amber, or magenta), not all three. Separate planes by temperature: warm foreground, cool distance reads as depth to the model. HDR with genuinely deep blacks and controlled highlights; avoid flat even lighting, which kills scale and mood. If it's "clean" hard-sci-fi, keep contrast surgical and shadows sharp; if it's "used-future," dirty the whites and let bounce light get muddy.

**Sound & voice:** Foundation is low sub-bass drone and hull room-tone hum - the sound of a big space being big. Use silence as a tool: cut sound hard in vacuum, then let the drone return inside. Sparse diegetic detail - a single console beep, a relay clunk, a suit-regulator breath - reads as more real than a wall of sci-fi bleeps. Score restrained: a slow synth pad or a single held string, entering late, never wall-to-wall. Voice: comms should be compressed, band-limited (radio EQ), and intermittently broken by static; a ship AI is calm, neutral, lightly processed, never theatrically robotic. Keep dialogue clipped and functional - crews talk in shorthand under stress, they don't narrate the plot. For a single voiceover line, favor understatement; the wonder is on screen, so the voice should ground it, not oversell it.

**Steering vocabulary:** monolithic, megastructure, derelict, brutalist, retrofuture, biomechanical, kitbashed greebles, weathered hull plating, volumetric haze, anamorphic, twin-sun, sub-surface glow, iridescent, orbital scale, telephoto compression, hard rim light, matte industrial, hazard-striped, condensation frost, cold cyan accent

**Example idea:** a lonely astronaut looking up at a huge alien machine on another planet

- Bad: a cool astronaut looking at a big alien machine, sci-fi, futuristic, highly detailed, 8k, epic, cinematic, masterpiece, trending on artstation

- Improved: A lone astronaut in a white spacesuit standing before a massive alien machine on a desert planet, low camera angle for scale, dramatic side lighting, atmospheric dust haze, cinematic, detailed background, photorealistic.

- Elite (image):

```
Wide establishing shot, photorealistic. SUBJECT: a single suited astronaut, tiny, standing at the base of a colossal derelict alien machine - a ribbed obsidian tower half-buried in pale dust, rising out of frame into haze. CAMERA: 24mm anamorphic lens, low angle tilted up the structure, camera locked; astronaut placed on the lower-left third with deep empty sky above to sell scale. SCENE HIERARCHY - Foreground: cracked ochre regolith, the astronaut's fresh boot-prints trailing in. Midground: the astronaut in a scuffed white hardshell suit, gold visor down, one gloved hand half-raised toward the tower. Background: the machine's flank climbing into dust haze, its surface etched with cold cyan glyph-channels that glow faintly from within. LIGHTING: twin suns - a large warm amber sun low on the horizon and a small blue-white sun higher up - casting two long crossing shadows across the dust; hard amber rim light down one edge of the suit, soft cyan bounce from the glyphs onto the gold visor. ATMOSPHERE: fine airborne dust, volumetric god-rays raking through it, thin ground fog pooling at the tower base. PALETTE: desaturated ochre-and-grey terrain against a single cold cyan tech accent. FINISH: 35mm film grain, high dynamic range with deep blacks, natural haze-based depth falloff, restrained contrast. MOOD: silent, ancient, overwhelming scale - awe over threat.
```

**Negative prompt:** floating or gibberish text, garbled fake-language UI overlays, extra limbs, warped or melted helmet, a photographer/camera reflected in the visor, duplicated astronaut, spotless factory-clean suit, plastic cartoon proportions, oversaturated rainbow neon, blown-out lens flares, heavy chromatic aberration, modern Earth brand logos, contradictory day-and-night lighting, mismatched shadow directions, cluttered HUD graphics over the whole frame, franchise-specific icons (energy swords, sandworms, badge insignia), low-detail smeared background, "epic masterpiece 4k" style tags baked into the render

**Checklist:**
- Is there ONE clear subject plus a scale anchor (a human, a vehicle, a known-size object)?
- Are shot size, lens, and camera angle stated explicitly?
- Is every light source motivated, and do all cast shadows agree on direction?
- One accent color on a limited, mostly-desaturated palette - not rainbow neon?
- Are foreground / midground / background each described so the model can layer depth?
- Does the tech show wear, seams, panels, and function - not smooth toy plastic?
- Zero contradictions (no day+night, no two conflicting light rigs, no vacuum with wind)?
- Does the negative prompt block text, anatomy errors, and franchise leaks?
- Is a wider world implied through geology, weather, atmosphere, or skyline?
- If video: exactly one camera move, with its direction and speed named?

### Cyberpunk / Neon Dystopia

**Visual ingredients:** rain, and always the wet reflective surfaces it creates (puddles, glossy asphalt, beaded jackets); neon and holographic signage using short abstract glyphs rather than real sentences so text does not garble; a clear single subject with one or two believable cybernetic details, not a body covered in random implants; vertical layered depth: rain foreground, subject midground, fogged megacity background; steam and vents catching colored light to make the air visible; chrome, brushed metal and glass surfaces to bounce and streak the neon; background life: distant crowds with umbrellas, a noodle stall, flying vehicles, strung cables; a limited motivated light logic: one cold key, one warm accent

**Motifs:** neon signage doubled in a rain puddle; a lone silhouette dwarfed by a colossal glowing hologram of a face; rain streaking visibly through a single colored light beam; a chrome jaw or eye implant catching hard magenta rim light; an endless vertical canyon of stacked apartment megablocks; the warm amber glow of a street food stall against cold blue dark; tangled power and data cables sagging overhead; steam rising from a grate lit from below by neon

**Cliches to avoid:** the identical lone hooded figure shot from behind, back to camera, that every generator defaults to; garbled fake-Japanese or fake-Chinese text on signs - use short abstract glyphs or keep signage out of focus; rainbow oversaturation where ten neon colors fight and flatten the depth; generic floating game HUD, targeting reticles and stat overlays pasted over the frame; a body plastered with random symmetrical implants instead of one or two believable augments; teal-and-orange as the only palette - cyberpunk is cyan-magenta led, amber is the accent not the theme; literal frame-for-frame copies of famous films - borrow the technique, not the exact shot; clean, dry, well-lit 'daytime cyberpunk' that forgets the genre needs darkness, rain and pools of light; AI-slop tag soup ('masterpiece, 4k, 8k, trending on artstation') that steers nothing

**Camera:** Shoot the megacity as a vertical canyon, not a flat street. Core kit: anamorphic look (2.39:1) with horizontal blue streak flares; 24-35mm for environment and establishing frames, 85mm for shallow-DOF portraits of a transhuman character. Signature setups: (1) low-angle hero shot craning up a megastructure so it leans over the subject; (2) slight dutch tilt in alleys for unease; (3) telephoto compression down a crowded street to stack neon signage into a wall of light; (4) over-the-shoulder in rain with a giant hologram filling the background; (5) reflection shot framed on wet black asphalt or a puddle so the neon reads twice. Eye level for intimacy, low angle for dread, high or drone descent for the sprawl. For video, one primary move per shot: slow dolly-in on a still figure, crane-down into street level, FPV drone threading a cable-strung alley, or a lateral track that reveals parallax between foreground rain, midground subject, and background signage. Keep moves slow and motivated; frenetic hyper-cuts read as game trailer, not cinema. Always build three depth planes: rain/bokeh foreground, subject midground, fogged megacity background.

**Lighting & colour:** Duotone-plus-accent, not a rainbow. Anchor on cyan and magenta as the two dominant hues, then let a single warm amber or sodium-vapor pool (a noodle stall, a doorway, a streetlamp) break the cold to give the eye somewhere to rest. Motivate every source: name which sign or window is the key, which is the rim. Neon and holograms act as practical key and colored bounce, spilling saturated color onto wet skin, chrome and puddles. Crush the blacks hard for depth, keep highlights hot and slightly clipped on chrome and rain. Push volumetric haze so light beams and god rays are visible. High contrast, colored shadows (cyan shadow, magenta highlight), subtle chromatic aberration at the edges. Avoid clean flat exposure - cyberpunk lives in pools of light surrounded by darkness.

**Sound & voice:** Ambient bed: steady rain hiss, distant traffic drone and sirens, a low synth pad, and a felt sub-bass city hum. Spot effects for texture: sizzle of a street food stall, hydraulic hiss of a door, glitchy UI blips, the flutter of a passing drone, clipped multilingual crowd chatter kept low as ambience, not dialogue. Music leans dark synthwave or industrial - slow arpeggios, analog warmth, no bright pop hooks. Voiceover, if any: low, hushed, world-weary internal monologue, short sentences, no exposition dump, let the world imply the story. Spoken dialogue is sparse and clipped, half-swallowed by the rain.

**Steering vocabulary:** anamorphic, volumetric haze, rain-slicked, wet asphalt reflections, sodium-vapor pools, sub-surface neon bounce, chromatic aberration, holographic signage, cybernetic, chrome implants, megastructure, the sprawl, vertical city canyon, practical light sources, god rays, crushed blacks, cyan-magenta split, atmospheric perspective, bokeh, steam vents, translucent, brushed metal, photorealistic cinematic

**Example idea:** a hacker girl standing in a rainy neon city at night

- Bad: cyberpunk city, neon lights, rain, girl, futuristic, blade runner style, highly detailed, 4k, 8k, trending on artstation, masterpiece, beautiful, cinematic lighting

- Improved: A lone woman in a soaked black raincoat stands on a wet neon-lit street in a futuristic megacity at night. Rain is falling, glowing signs reflect in the puddles at her feet, cyan and magenta lighting. Wide 35mm lens, shallow depth of field, moody and isolated atmosphere, photorealistic, cinematic.

- Elite (image):

```
Medium-wide anamorphic photograph, 2.39:1, 35mm lens at eye level with a slight low angle. One clear subject: a lone transhuman courier, a woman in her mid-twenties, standing still and centered on a flooded pedestrian crossing in a rain-drenched megacity at 2 a.m. She wears a soaked matte-black techwear shell jacket with a translucent rain hood; a brushed-chrome cybernetic plate runs along her left jaw and catches magenta light; a hairline data-port scar sits behind her ear. Her weight is settled, chin slightly down, expression tired and wary. Foreground: rain streaking diagonally through the frame, out-of-focus red umbrella bokeh at the edges. Midground: the courier mirrored sharply in the black wet asphalt, one glowing cyan puddle under her boots. Background: a vertical canyon of stacked megastructures dissolving into fog, walls layered with holographic signage in short abstract glowing glyphs (no readable text), a colossal magenta advertisement of a slowly rotating face looming above, steam venting from street grates, tangled cables strung overhead. Lighting: cyan neon key from screen-left, a warm sodium-amber pool from a noodle stall at screen-right, hard magenta rim light separating her from the haze, deep crushed shadows. Volumetric god rays cut through drifting fog and rain. Palette limited to cyan, magenta and amber against near-black. Subtle film grain, gentle chromatic aberration on the highlights, shallow depth of field. Photorealistic, cinematic, quiet and tense.
```

**Negative prompt:** daylight, blue sky, sunshine, clean pristine surfaces, dry ground, rural or natural landscape, medieval, cheerful mood, pastel palette, oversaturated rainbow neon, garbled or misspelled sign text, floating gibberish characters, generic HUD overlay, video-game UI, plastic waxy skin, symmetrical pasted-on implants, flat even lighting, muddy grey shadows, low contrast, low resolution, blurry, out-of-focus subject, deformed hands, extra fingers, watermark, logo, signature, jpeg artifacts, lens dirt overload

**Checklist:**
- Is there exactly one clear subject and one focal point, not a crowd competing for attention?
- Is the light logic named and motivated - which sign is the cold key, which source is the warm accent?
- Are there three depth planes (foreground / midground / background) with haze separating them?
- Is every hard surface wet and reflecting neon, and does at least one puddle or window double the light?
- Is the palette held to two dominant hues plus one accent, not a rainbow?
- Is any signage text short and abstract (or defocused) so the model will not garble it?
- Are camera, lens and angle explicitly stated, and for video is there one primary slow move?
- Are the blacks crushed for depth rather than left flat grey?
- Any contradictions? (night but bright daylight, rain but dry ground, isolated but crowded) - remove them.
- Are cybernetic details limited to one or two believable augments rather than random full-body chrome?

### Dark Ages / Mud-and-Iron Medieval

**Visual ingredients:** riveted iron mail (hauberk) and simple nasal/spangenhelm helmets - never plate; quilted gambeson, undyed wool and linen, leather and rawhide; clinker-built longship: overlapping tarred oak strakes, iron rivets, dragon-head prow, wool sail with reefing bands, oars and rope; tallow/beeswax candles, rushlights, hearth and torch fire as the only warm source; wattle-and-daub, thatch, peat-smoke haze inside low timber halls; Romanesque round-arch stone monastery, thick walls, small deep windows; illuminated vellum manuscript with gold leaf, quill, oak-gall ink; tonsured monk, plain habit, rope belt; mud, bog, fjord water, wet shingle beach; cold breath-fog, drizzle, drifting mist, wet clinging wool

**Motifs:** a single candle flame carved out of total black stone; breath fogging in freezing air; rust bleeding in orange streaks down grey iron; bare or wrapped feet sucking in cold mud; gold leaf catching the one warm light in an otherwise grey cell; a dragon-head prow emerging prow-first from fog; wet wool and mail clinging to a body in the rain; woad-blue as the single color against oatmeal-grey crowds; peat and hearth smoke pooling flat under a thatch roof; the black glassy wake of oars on still water

**Cliches to avoid:** horned viking helmets (a 19th-century myth, never worn); shiny clean full plate armor (that is 15th-century, wrong era by 500 years); polished chrome/mirror-finish swords and metal; Hollywood teal-and-orange grade; CGI dragons, glowing magic runes, fantasy creatures; pristine white fairy-tale castles and Gothic cathedrals with rose windows (Gothic is 12th c.+); everyone with clean faces, perfect modern teeth, styled hair; god-ray / lens-flare overload sold as 'epic'; over-cranked arterial blood spray; RPG game-HUD or Assassin's-Creed asset look

**Camera:** Long lenses (85-135mm) to stack and compress mist/fjord layers into flat, painterly planes; a 40mm anamorphic for cramped stone interiors. Locked-off tripod for monastic stillness (the camera does not move while a monk works - the flame does). Handheld 50mm at medium-close for battle and rowing, with real micro-jitter, never a smooth gimbal glide. Low hero angle from water level on a longship prow; occasional top-down of oars biting black water. Motion is slow and singular - one slow push-in or one slow lateral drift per shot, motivated by the action, never a restless flythrough. Shoot T2.0-2.8 for shallow focus on a candle flame or a single face; 24fps, 180-degree shutter for honest motion blur. Deliberately underexpose to protect firelight and hold mood in the shadows.

**Lighting & colour:** Motivated fire is the only warm light: tallow candle, hearth, torch, or brazier at 1800-2200K, pooling amber against a cold 6500-7500K overcast daylight that behaves like a vast softbox - flat, sourceless, no hard sun. Chiaroscuro indoors: one candle carving a face out of black stone, everything else falling to shadow. Palette is desaturated earth - peat brown, bog black, oxblood, verdigris, bone, undyed oatmeal wool, ochre - with at most ONE saturated accent per frame (a woad-blue cloak, or gold leaf on vellum catching the single warm light). Kill jewel tones and teal-orange. Every warm highlight must trace to a visible flame; every exterior should feel damp, grey, and low-contrast. Grain and haze over clean digital sheen.

**Sound & voice:** Diegetic-only, sparse, no orchestral swell - let ambience carry the dread. Exterior: creak of oak strakes, rope groan, oars dipping and dripping, wind over water, distant surf, gull cries, rain ticking on wool and iron. Interior: the fat spit and hiss of a tallow flame, a quill scratching vellum, damp stone reverb, a far-off bell. Voice sits low and weathered, no modern cadence or slang; guttural reconstructed Old Norse for raiders or murmured ecclesiastical Latin plainchant for monks, subtitled rather than dubbed. For tension use a single bowed drone - tagelharpa, hurdy-gurdy, or a low male throat-chant - not percussion or synth. Silence and wind are valid choices; do not score every moment.

**Steering vocabulary:** clinker-built oak hull, riveted iron mail hauberk, tallow candlelight, peat smoke, woad-dyed wool, overcast Nordic light, damp stone chiaroscuro, wattle-and-daub and thatch, quilted gambeson, nasal helm / spangenhelm, vellum and gold leaf, iron-oxide rust bleed, bog mist, undyed oatmeal wool, verdigris and oxblood, rushlight, mud-and-iron realism, hand-forged, rain-soaked, grimed and weathered

**Example idea:** a viking longship coming out of the fog to raid at dawn

- Bad: epic viking warrior on a badass longship coming to raid, horned helmet, glowing eyes, highly detailed, 8k, ultra realistic, cinematic, dramatic god rays, teal and orange, trending on artstation, masterpiece, unreal engine

- Improved: A Norse longship approaching a rocky shore in morning fog, warriors in chainmail holding round wooden shields, carved dragon prow, grey overcast sky, cold desaturated color palette, cinematic natural lighting, wide shot, detailed weathered textures, moody atmosphere, no horns on helmets

- Elite (video):

```
Single continuous wide shot, slow dolly push-in on a 100mm anamorphic lens compressing three receding layers of bog mist. A clinker-built oak longship - overlapping tarred strakes, hand-forged iron rivets bleeding rust, a carved dragon-head prow - glides out of dense grey fog toward a shingle beach at first light. Fifteen oarsmen in undyed oatmeal wool and riveted iron mail hauberks pull in slow unison; a lone steersman in a plain nasal helmet and single woad-blue cloak leans on the steering-oar at the stern. Their breath fogs in the cold air, oar-blades dripping, a black glassy wake spreading behind them. Lighting: flat overcast Nordic dawn at 7000K acting as a giant softbox, low contrast, no visible sun; the only warm accent is a covered 1900K brazier glow amidships. Desaturated earth palette - peat brown, bog black, oxblood, oatmeal wool - broken by the one woad-blue cloak. Grimed, wind-chapped faces; wet wool clinging; drizzle haze and faint peat smoke drifting through frame. Camera: 24fps, 180-degree shutter, T2.8, subtle handheld micro-jitter, slight underexposure to protect highlight detail in the fog. Naturalistic mud-and-iron realism, single clear subject, foreground prow / midground crew / background dissolving shoreline. No horned helmets, no polished metal, no sun flare, no fantasy elements.
```

**Negative prompt:** horned helmets, shiny plate armor, polished chrome metal, mirror-finish swords, teal and orange grade, saturated colors, dragons, fantasy creatures, glowing runes, magic effects, gothic cathedral, stained glass rose window, clean faces, perfect modern teeth, styled hair, plastic skin, HDR glow, lens flare, god rays, artstation, unreal engine render, neon, gunpowder, guns, cobblestone streets, brick, concrete, nylon rope, plastic, sunny blue sky, watermark, text, logo, extra fingers, warped hands, deformed anatomy

**Checklist:**
- Era check: no plate armor, no horned helmets, no Gothic architecture - only mail, gambeson, nasal helms, Romanesque round arches
- Every warm highlight traces to a visible flame; no unmotivated warm light in daylight
- Faces and hands are grimed, wind-chapped, weathered - no clean modern skin
- Palette is desaturated earth with at most ONE saturated accent in frame
- One clear subject with foreground/midground/background hierarchy - not a cluttered epic wide
- Camera move is single, slow, and justified by the action (or locked off)
- Materials read as wool, iron, oak, leather - not plastic, chrome, or CGI
- Atmosphere present: mist, drizzle, breath-fog, or peat/hearth smoke
- No contradictions: overcast-day and firelit-interior are not mixed in one shot
- Props and dress are historically plausible for c. 5th-11th century Europe

### Ancient History & Antiquity (Rome, Egypt, Greece, Bronze Age)

**Visual ingredients:** Period-correct arms and armor (right era, not just 'ancient' - Republic mail vs Empire segmentata, bronze hoplite panoply, Bronze Age boar's-tusk helmet); Status- and gender-correct dress in natural fibers (wool, linen) with plausible natural dyes; Authentic architecture in its true material and, if newly built, its original paint - not the bare ruin we see today; Correct standards, insignia and everyday objects (aquila, vexillum, amphorae, oil lamps, wax tablets, papyrus); The right biome and landscape (Mediterranean scrub, Nile floodplain, Aegean coast, Anatolian upland); A single believable light source - hard sun, oil lamp, torch, brazier, or temple light-shaft; Crowds and military formations that obey real logic (phalanx depth, legion lines, procession order); Weathering and use - chipped stone, oiled iron, sweat, dust, worn leather

**Motifs:** Polychrome painted marble - statues and temples were vividly colored, not white; Oxidized green verdigris bronze contrasted with freshly polished gold-bright bronze; Sun-bleached limestone columns and fluted shafts against a hard blue sky; Fresco surfaces (Pompeian red, Villa-of-Mysteries figures) and tessellated mosaic floors; Terracotta amphorae, red-figure and black-figure pottery; Egyptian sunk relief, hieroglyph registers, nemes headdress, Egyptian-blue faience; Olive trees, cypress, date palms, dust and shimmering heat-haze; Torchlit stone interiors thick with lamp-smoke and deep shadow; Laurel and olive wreaths, purple-bordered cloth, standards raised above a marching column; Papyrus scrolls, wax tablets, and the frontier ditch-and-palisade

**Cliches to avoid:** Pristine snow-white marble statues and temples (they were painted - white is a Renaissance-era misreading); Gleaming spotless gold or chrome armor with no wear; Horned or winged 'barbarian' helmets on anyone; The same three 'Gladiator' Colosseum-and-dust shots; Orange-and-teal or oversaturated 'epic' color grading; Anachronistic stirrups, plate armor, or medieval crenellated castles standing in for Roman forts; Everyone draped in clean pressed bedsheet-white togas regardless of rank or job; Cleopatra or any figure rendered as a modern airbrushed beauty-standard pinup; Over-muscled oiled-bodybuilder physiques on every soldier; Glowing magic, floating runes, and CGI apocalypse skies; Mixing eras inside one frame (Republican kit with Imperial architecture, camels in the wrong place/time)

**Camera:** Shoot it like a grounded historical epic, not a fantasy poster. IMAGE: wide establishing landscape at 24-35mm for cities, temples, harbors and battle vistas (deep focus, everything sharp); 50-85mm at a low, near-eye-level angle for hero portraits so a single figure reads with quiet authority; 100mm macro for material detail - a bronze boss, fresco cracks, hieroglyph tool-marks, the weave of wool. VIDEO: slow crane-down or push-in to establish a temple/city; 135mm telephoto to compress an advancing phalanx or legion into one dense wall of shields; a low dolly tracking alongside a marching column at hobnail height; handheld with real shake only inside the melee; a locked-off symmetrical wide for temple architecture (let the columns do the work). Angle logic: low hero angle for power over a single figure, high wide angle to show the scale of an army or crowd. Favor golden-hour backlight and long shadows over flat noon.

**Lighting & colour:** Default to naturalism. Mediterranean and North African daylight is hard, high and warm, with airborne dust softening the far distance - long raking shadows at dawn and dusk read as 'antiquity' far better than flat noon. Interiors are lit by a single warm source: oil lamps, torches, or a brazier - low, flickering, smoke-hazed, with deep falloff into shadow (study Kubrick's Barry Lyndon for candlelit interiors). Egyptian temples get a hard shaft of sun cutting through gloom. Build the palette from real ancient pigments and dyes: ochre, madder red, terracotta, limestone cream, olive green, Egyptian blue and faience, saffron yellow, oxidized-bronze green and polished-bronze gold; reserve Tyrian purple for the elite. Keep saturation restrained and grounded - no neon, no synthetic-looking color, no digital HDR glow.

**Sound & voice:** Ambient first: wind over stone, crackling torches and lamp-flame, distant market or crowd murmur, hobnailed sandals on flagstone, the clatter and creak of bronze and leather, oars and a rowing-drum, temple chant, cicadas in heat. Score sparingly and with period-plausible instruments - lyre and kithara, the double-reed aulos, frame drums, the Egyptian sistrum, and Roman cornu/lituus horns for military weight; a low drone under tension. Avoid pop-orchestral blockbuster bombast unless a deliberately cinematic beat calls for it. Voice: measured, formal cadence with a period register - no modern slang or idiom. If spoken language is used, deploy reconstructed Latin, Koine Greek, or Egyptian briefly and correctly rather than at length; otherwise grounded English. Narration (documentary mode): restrained gravitas, unhurried, never breathless hype.

**Steering vocabulary:** period-authentic, mid-Imperial Roman, New Kingdom Egyptian, Classical Greek hoplite, Minoan / Mycenaean Bronze Age, polychrome painted marble, oxidized bronze / verdigris, lorica segmentata, Corinthian bronze helmet, linothorax, hobnailed caligae, sunk relief, nemes headdress, Tyrian purple (elite only), hand-woven wool with slubs, sun-bleached limestone, raking Mediterranean sunlight, oil-lamp interior, warm and smoky, dust and heat-haze, naturalistic muted earth palette, photorealistic, historically accurate

**Example idea:** a roman soldier standing on a wall at sunset

- Bad: epic roman warrior standing on castle wall at sunset, highly detailed, 8k, masterpiece, cinematic, trending on artstation, shiny gold armor, super muscular hero, white marble columns everywhere, dramatic epic lighting, ultra realistic, beautiful

- Improved: A Roman legionary of the Imperial period standing guard on the timber rampart of a frontier fort at sunset. He wears segmented iron lorica segmentata over a red wool tunic, an iron helmet with cheek guards, and rests his hands on a curved rectangular red scutum with a bronze boss; a pilum leans beside him. Warm low sunlight from the side, cool hills behind, dust in the air. Photorealistic, natural muted color, historically accurate Roman kit, one soldier, shallow depth of field.

- Elite (image):

```
Medium-wide, low-angle portrait of a single Roman legionary of the mid-Imperial period (2nd century) standing watch on the earth-and-timber rampart of a northern frontier fort at dusk. He wears segmented iron lorica segmentata over a madder-red wool tunic, an Imperial-Gallic iron helmet with hinged cheek-guards, a gladius sheathed high on his right hip, and hobnailed caligae; both hands rest on the top rim of a curved rectangular scutum painted deep red with a bronze central boss and a yellow winged-lightning blazon. A pilum leans against the pointed timber palisade beside him. Behind and below: a defensive ditch, a squat timber watchtower, and cold blue hills dissolving into evening haze. Single light source - a low golden sun raking from camera-left - throws hard warm rim light along the helmet crest and shoulder plates and lets cool blue shadow fill the rest; fine dust and the soldier's visible breath catch the light. Shot on 85mm, shallow depth of field with the fort softly out of focus, near-eye-level-to-low angle for quiet authority. Surfaces: weathered iron with faint scratches and oil sheen, worn leather straps, hand-woven wool with slubs - no chrome, no gloss. Naturalistic muted earth palette, subtle film grain, photorealistic, period-authentic mid-Empire Roman military kit, one subject, clean scene hierarchy.
```

**Negative prompt:** stirrups, medieval castle, stone crenellations, plate armor, chainmail on a Roman, horned helmet, viking, katana, longsword, fantasy runes, glowing eyes, magic effects, pristine snow-white marble statues, spotless mirror-gold or chrome armor, oiled bodybuilder physique, orange-and-teal grade, neon or oversaturated color, HDR halos, plastic skin, gibberish text on banners, watermark, logo, wristwatch, zippers, modern stitching, camels in Bronze Age Greece, togas worn by soldiers, two competing subjects, extra fingers, cartoon, illustration filter

**Checklist:**
- Right sub-period within the era? Don't cross-mix - Republic vs Empire kit, Old/Middle/New Kingdom, Archaic/Classical/Hellenistic, Minoan vs Mycenaean.
- Zero anachronisms: no stirrups, plate armor, medieval castles, later weapons or technology in frame.
- Marble and statuary painted or believably weathered/ruined - not pristine snow-white unless a deliberate ruin state.
- Metal and armor weathered, matte-to-oiled, scratched - never chrome-spotless.
- Dress correct for status, role and gender; dyes plausible (Tyrian purple = elite only).
- Architecture uses the correct order and material; if newly built it's polychrome, if ancient it's weathered.
- One clear subject and a readable scene hierarchy - no two focal points fighting for attention.
- Single believable light source; shadow direction, dust and haze all consistent with it.
- Standards, insignia and any inscriptions are correct or kept illegible-generic - never gibberish English text.
- Palette drawn from natural pigments; no neon, no orange-teal, no synthetic saturation.

### Modern History (19th-20th Century Realism)

**Visual ingredients:** Period-correct wardrobe showing genuine wear, patches, and hand-repair; Era-specific tools, machinery, and vehicles matched to the exact decade; Authentic architecture, hand-painted signage, and period typography; Hairstyles, facial hair, and grooming pinned to the decade; Hands and skin that show labor - calluses, soot, sunburn, dirt under nails; The correct photographic process and grain for the year depicted; A total absence of anachronisms in frame and background; Environmental context that tells the subject's trade and class

**Motifs:** The silver mirror sheen and cased edges of a daguerreotype; Hard flash-powder shadow thrown across a crowded tenement room; Drought-cracked earth and blowing dust of the Dust Bowl; Coal soot ground into skin and creased knuckles; Steam, factory haze, and looming iron machinery; Hand-painted shop signs and gold-leaf window lettering; Cobblestone streets, streetcar tracks, and overhead wires; Laundry strung on lines between crowded tenement blocks; Autochrome's soft pointillist color grain; Scratched, foxed, and chemically-stained print edges

**Cliches to avoid:** Everyone frowning on cue because 'history was sad' - vary the honest, neutral expressions of held poses.; A uniform brown sepia wash slapped over everything as a shortcut for 'old'.; Grimy face paired with clean, unworn clothing - the mismatch screams costume.; A single glistening tear rolling down a cheek for cheap pathos.; Perfectly centered, symmetrical, cinematic composition - period cameras framed plainer.; Modern faces with modern teeth, brows, and skincare wearing a period outfit.; Steampunk gears, goggles, and brass masquerading as actual 19th-century history.; Garish over-colorized tones on what should be a muted early-color or monochrome image.; Reenactor stiffness with crisp, factory-new 'costumes' and unweathered props.

**Camera:** Shoot at eye level for honesty; a slightly low angle lends dignity (Lewis Hine's child-labor portraits). Favor the environmental portrait - the subject plus the tools and space of their labor - framed medium-full to medium so trade and class read at a glance. Use normal-to-slightly-wide fields of view (35mm and 50mm equivalents) that match rangefinder and press cameras; avoid 85mm+ creamy-bokeh headshots and ultra-wide action-cam distortion - both read instantly modern. For pre-1900 scenes, mimic large-format 4x5/8x10: deep depth of field, everything sharp, locked-off framing that implies a long exposure (still subjects, slight motion blur on anything that moved, empty stares from held poses). For 1930s-40s reportage, allow a handheld Speed Graphic or Leica feel with a single hard flashbulb and its falling shadow. Straight-on typological framing (August Sander) for formal portraits; the candid decisive-moment (Cartier-Bresson, Robert Capa) for street and conflict. Keep horizons level and compositions plain - anti-cinematic framing is the period tell.

**Lighting & colour:** Light from a single believable source and let shadows fall dark - period film had little fill. North/window light or open overcast shade for portraits and fieldwork; a hard flash-powder or single flashbulb burst (Jacob Riis, press photography) for tenement and night interiors, with its characteristic blown highlight and sharp cast shadow. Match tone to the era's process: cool neutral grays and a broad tonal scale for silver-gelatin; warm, dense-shadowed, slightly-desaturated hues for early Kodachrome (1935+); muted pastel with a soft red-green pointillist grain for Autochrome (1907-1930s); a faint uniform sepia or selenium tone for albumen and toned prints. Keep saturation restrained - no modern teal-and-orange grade, no HDR local contrast. Add authentic degradation sparingly: fine grain, gentle edge vignette, occasional foxing, silver-mirroring, or emulsion scratches - not a filter dump.

**Sound & voice:** Keep it diegetic, sparse, and mono - no modern wide-stereo orchestral bed. Build era-true ambience: streetcar bells and horse hooves on cobblestone for a 1900s street; clattering looms, steam hiss, and a shift whistle for industry; wind, a screen-door creak, and a distant crystal radio for a 1930s farmhouse; artillery thud and rifle reports muffled under wind for wartime. If music is used, source it in-world from one period-correct instrument or a wax-cylinder / 78-rpm phonograph with surface crackle - a lone fiddle, upright piano, or brass band - band-limited and slightly distorted, never lush. Voiceover, if any, in the clipped cadence of period newsreel narration or plain first-person testimony; avoid contemporary slang, up-talk, and vocal-fry. For a found-footage feel, add optical-track hiss, projector flutter, and reel-change pops.

**Steering vocabulary:** silver-gelatin, wet-plate collodion, tintype, daguerreotype, albumen print, Autochrome, Kodachrome, 4x5 large-format, Speed Graphic, flash powder, FSA documentary, period-accurate, hand-mended, unposed, environmental portrait, sharecropper, tenement, newsreel, Tri-X grain, sepia-toned

**Example idea:** an old photo of a poor family during the great depression

- Bad: a sad poor family in the great depression, old vintage photo, black and white, very emotional, historical, cinematic, dramatic lighting, 4k, ultra detailed, masterpiece

- Improved: Black-and-white documentary photograph of a Depression-era farming family standing outside a weathered wooden cabin, around 1936. Worn patched clothing, tired unsmiling faces, dusty bare yard. Soft overcast daylight, deep focus, fine film grain, shot on large-format film in the style of 1930s FSA documentary photography.

- Elite (image):

```
Documentary black-and-white photograph, American Dust Bowl, spring 1936. Single clear subject: a gaunt tenant-farmer mother in her early thirties, weathered sunburned face, deep-set eyes, chapped lips, holding a swaddled infant on one hip while two barefoot children in flour-sack dresses press against her skirt. Environmental portrait, framed medium-full at eye level, subject slightly off-center to the left. Setting: the sagging porch of an unpainted clapboard shack, cracked dry-earth yard, a hand-pump well and a rusted galvanized washtub behind them, one drought-bare cottonwood on the flat horizon. Wardrobe strictly period-accurate: patched cotton work dress, a man's oversized worn boots, hand-mended seams - no synthetic fabric, no printed logos, no zippers. Lighting: overcast midday, soft and near-directionless, faint catchlight in the eyes, open shade filling the shadows under the porch. Rendered as a 4x5 large-format silver-gelatin negative: edge-to-edge sharp focus at deep depth of field, fine even silver grain, full tonal range from a bright hazy sky to dense porch shadow, neutral-to-faint-sepia tone, slight natural film-edge vignette. Naturalist and unposed - no smiles, no direct eye contact with the camera, hands and posture showing fatigue. In the documentary tradition of Dorothea Lange and Walker Evans.
```

**Negative prompt:** modern clothing, synthetic fabric, nylon, plastic, wristwatch, modern eyeglass frames, zippers, velcro, sneakers, printed logos, brand names, smartphone, earbuds, post-period vehicles, HDR glow, digital over-sharpening, creamy shallow bokeh, 85mm portrait blur, studio softbox, ring light, lens flare, neon, saturated modern color cast, colorized look on a black-and-white request, perfect white teeth, flawless airbrushed skin, contemporary hairstyles, shaped modern eyebrows, manicured nails, visible modern tattoos, steampunk goggles and gears, reenactor stiffness with brand-new spotless costumes, cluttered anachronistic background, text, watermark, signature, extra fingers, distorted hands

**Checklist:**
- Does the photographic process match the year? (no Kodachrome color for an 1870s scene, no 35mm grain for a wet-plate era)
- Scan for anachronisms: zippers, wristwatches, sneakers, plastic, modern glasses, printed logos, and background vehicles.
- Do wardrobe wear and the environment agree with the subject's stated class and trade? (no grimy face over spotless new clothes)
- One clear subject with a readable focal hierarchy - not a row of equally-weighted faces.
- Hairstyles, facial hair, and grooming correct for the exact decade, not generic 'old-timey'.
- Tonal range and grain consistent with the named film/process, not digital HDR or plastic CGI sheen.
- Do the faces read as period people rather than modern models in costume? (bone structure, teeth, brows)
- Any signage or text in period-correct typography, language, and spelling.
- If black-and-white was requested, confirm no color has crept in (and vice versa).

### Geopolitics - Explainer & Statecraft Visuals

**Visual ingredients:** Named map projection (orthographic globe, Robinson, or true nadir equirectangular) chosen to fit the claim; Choropleth or single-hue sequential shading tied to one variable; Great-circle arcs for trade / energy / migration / flight routes; Chokepoint callouts (Hormuz, Malacca, Bab-el-Mandeb, Suez, Panama, Bosphorus, Gibraltar); Simplified moving icons - tankers, cargo, pipelines - over the route; Graticule / lat-long lines and a day-night terminator on globes; Hairline dashed strokes for disputed or contested borders; ONE data panel carrying a single figure or one comparison, with a slim bar or dot; Small-caps sans-serif place labels, kept minimal so the model doesn't garble text; A source / credit footer strip

**Motifs:** A luminous line drawing itself across a globe (route reveal); Choropleth filling region-by-region by shade intensity; The orbit-to-street scale descent (globe country chokepoint); Terminator line sweeping across the day-night boundary; Pipeline / cable / shipping-lane network as thin glowing filaments; Situation-room map wall in cool desaturated light (used sparingly, never for drama); Redacted-document / dossier texture for statecraft and intelligence framing; Relief-shaded topography reading as calm authority rather than spectacle; A single small stat panel with a bar filling to a fraction; Restrained Go / chess board as a statecraft metaphor - rare, tasteful, never literal war

**Cliches to avoid:** Red arrows sweeping across a map like an invasion; Chess or Go pieces standing in for countries; Puppet strings over world leaders; A pulsing red hotspot / glowing danger zone; Flag-draped handshake stock photo; Dominoes of falling national flags; Eagle vs bear vs dragon animal-nation cartoons; Doomsday clock ticking to midnight; Cracked earth splitting between rival blocs; Countries as jigsaw puzzle pieces; Spinning newspaper headline; A tug-of-war rope over a flag or resource

**Camera:** Think satellite-desk, not battlefield. Establish with an extreme-wide orthographic globe from a satellite POV, then use ONE controlled move per shot. Core moves: (1) the slow linear push-in / "orbital descent" - dolly from orbit toward a region at a steady 6-10s crawl, never a crash-zoom; (2) the locked-off nadir (true top-down) for maps and choropleths so borders and coastlines read without perspective lie; (3) a gentle 5-15 orbit around a 3D relief globe to give dimensionality; (4) the scale match-cut - hard or morph cut from globe country chokepoint, each held long enough to read. Angles: nadir for data, high 20-30 satellite tilt for relief and depth, eye-level only for the rare human/podium insert. Lens feel: long/tele-equivalent (85-200mm look) to flatten the sphere and keep graticule lines near-parallel; reserve wide-angle for the dramatic orbital establisher only. Motion discipline: one move, constant velocity, dead-locked tripod feel - no handheld, no shake, no Dutch tilt, no whip pans. Hold on every number and label longer than feels comfortable; the eye is reading, not being thrilled.

**Lighting & colour:** Two lighting regimes. For flat 2D infographics: even, high-key, near-shadowless editorial light so every label and border reads with equal weight - no mood lighting fighting the data. For 3D globes and relief maps: a single soft directional key simulating a low sun, a gentle terminator, subtle atmospheric rim/haze at the limb, and faint city lights on the night side - one light, believable, never theatrical. COLOR is where neutrality lives: build on a desaturated base (navy, slate, warm grey, off-white) plus exactly ONE accent that carries the story's throughline (cyan or amber). Never map good/evil onto blue-vs-red; use a single-hue sequential ramp for magnitude and a muted qualitative set for categories. Reserve red only for a quantitative maximum, never a moral verdict, and never color-code countries by flag to imply allegiance. Keep it 3 hues on screen; nothing neon.

**Sound & voice:** Sound design is quiet infrastructure, not tension. Bed: low ambient room-tone or a soft neutral pad, mixed well under the voice. Motion cues: subtle UI ticks as labels and arcs draw in, a soft airy whoosh on scale-zoom transitions - restrained, never a boom. Music: minimal and non-partisan - light pulse, marimba, or ambient synth in the Bloomberg/Vox register, low in the mix, no war drums, no rising tension strings, no stingers on statistics. Voiceover: calm, measured, mid-paced, third-person analytic. Neutral diction, no alarmism, no editorializing adjectives; state figures and their source plainly, land a slight downward inflection on key numbers, and pause after each figure so it registers. Avoid the ominous conspiracy-whisper, the hype build, and any framing that picks a side.

**Steering vocabulary:** orthographic projection, choropleth, great-circle arc, chokepoint, graticule, terminator line, satellite POV / nadir, relief-shaded, editorial infographic, desaturated restrained palette, single-hue sequential scale, hairline dashed border, small-caps label, Sankey flow, documentary-analytic, neutral / non-partisan, source footer, muted navy and warm grey, one accent color

**Example idea:** make a video explaining how much of the world's oil goes through the Strait of Hormuz

- Bad: A map of the world showing the Strait of Hormuz with oil and ships, geopolitics, super detailed, 4k, cinematic, epic tension between nations, glowing red arrows, war looming, dramatic.

- Improved: Top-down editorial map of the Persian Gulf and the Strait of Hormuz. Muted navy water, light warm-grey land, one cyan shipping-lane line passing through the strait, a few simple tanker icons along it. Clean sans-serif labels for Iran, Oman, and the U.A.E. A small stat in the corner reads "~20% of global oil." Neutral infographic style, desaturated palette, 16:9.

- Elite (video):

```
Single continuous shot, slow cinematic push-in, documentary-analytic tone. SUBJECT: a stylized 3D relief map of the Persian Gulf and the Strait of Hormuz seen from a high 25-degree satellite angle. Land is matte warm grey with faint topographic relief shading; water is deep desaturated navy with a subtle specular ripple. A single luminous cyan great-circle line traces the tanker shipping lane from the open Arabian Sea, threads through the narrow chokepoint between the coasts of Iran to the north and Oman to the south, then fans into the Gulf. Five simplified oil-tanker silhouettes drift slowly along the lane leaving faint wakes. Thin white graticule lines cross the frame; hairline dashed strokes mark contested maritime edges. Minimal on-frame text: three small-caps labels only - STRAIT OF HORMUZ, IRAN, OMAN - in clean off-white sans-serif. A restrained data panel lower-left holds ONE figure, "~20% OF GLOBAL SEABORNE OIL," with a slim horizontal bar filled to one-fifth. LIGHTING: one soft key from upper right simulating a low sun, long gentle shadows off the coastal relief, faint atmospheric haze at the horizon, no lens flare. CAMERA: dead-locked, one steady 8-second dolly-in from wide strait context toward the chokepoint, constant velocity, no shake. PALETTE: navy, warm grey, one cyan accent, off-white type - desaturated, editorial, strictly neutral (no red/blue side-coding). A thin source-credit strip runs along the bottom edge. Photoreal-infographic hybrid, crisp, high detail, correct coastlines, legible undistorted labels. 16:9.
```

**Negative prompt:** propaganda coloring, red-vs-blue enemy coding, glowing red danger zones, explosions, missiles, mushroom clouds, soldiers, tanks, blood, waving national flags, chess pieces on a map, puppet strings, cartoon animal-nations (eagle/bear/dragon), doomsday clock, cracked splitting earth, warped or gibberish text, misspelled country names, distorted coastlines, invented borders, oversaturated neon, rainbow choropleth, lens flare, heavy film grain, Dutch tilt, motion blur streaks, crash zoom, cluttered dashboard, too many arrows, 3D beveled text, stock-photo handshake, sensational headline chyron, ominous vignette

**Checklist:**
- Can a viewer state the single takeaway in one sentence? One question per frame.
- Geography is real - coastlines, country positions, and borders are accurate; disputed borders are dashed, not asserted as fact.
- Color logic is neutral - no good/evil red-vs-blue; the accent marks the data throughline, not a side.
- Every on-screen label is spelled right and legible; keep model text minimal and add real type in post if it garbles.
- Exactly one figure or one comparison per panel - no dashboard clutter.
- One camera move and one light source; no internal contradictions (no 'top-down map' plus 'dramatic low angle').
- A source/credit footer is present and no statistic is implied as fact without attribution.
- Palette is 3 desaturated colors - nothing neon, nothing flag-coded.

### Thriller / Suspense

**Visual ingredients:** A single clear subject in flight or in tense wait - isolation over crowd; A pursuer/threat kept partly obscured: silhouette, focus-falloff, reflection, or half-frame; Wet, reflective ground that multiplies every light source; One hard directional light with deep unlit gaps around it; A ticking-clock element present in frame or in sound (clock, timer, phone, alarm); An enclosure closing in - fence, wall, corridor, locked door - or oppressive empty negative space; Body language reading fear/vigilance: glance over shoulder, tensed hands, held breath

**Motifs:** Sodium-vapor streetlamp pools on black wet asphalt; A lone figure dwarfed at the far end of a long corridor, bridge, or tunnel; Venetian-blind light bars striping a watching face; Over-the-shoulder view of a pursuer's headlights closing in; A hand hovering over a phone, a door handle, or a drawer; A clock face, wristwatch, or countdown in hard close-up; Fogged breath and rain caught in a single hard backlight; A silhouette filling a doorway

**Cliches to avoid:** Generic hooded figure holding a glowing knife; Neon-cyberpunk pink/blue everything (that reads sci-fi, not thriller); Slow-motion rain with no stakes attached to it; A literal red LED bomb timer counting down (over-used, reads as parody); Everyone sprinting flat-out - a calm, unhurried pursuer is far scarier; Fully lighting and explaining the threat in the first frame; Jump-scare face lunging into the lens; Cheesy lightning flashes that reveal the villain

**Camera:** Shot sizes: alternate wide "isolation" frames (subject small inside oppressive negative space) with tight singles on hands, eyes, a clock face, a door handle - the cut between scales IS the tension. Lenses: telephoto 85-135mm to compress a pursuer into the subject's back and flatten depth; wide 24-35mm only for claustrophobic interiors (stairwells, corridors, elevators) to stretch and warp space around the subject. Angles: high-angle/overhead to make the subject look like prey; low-angle reserved for the threat; Dutch tilt 5-15 to signal wrongness - do not level it out mid-chase. Movements: handheld-style controlled instability for pursuit; a slow creeping dolly push-in on a waiting/hiding subject as dread builds; whip-pan on a sudden reveal; backward tracking ahead of a runner so they charge the lens. Use rack focus to pull attention from a foreground hand to a background threat. Keep every move motivated - each answers "what does this character fear right now." Study Hitchcock for information asymmetry (let the viewer see the threat the character can't), Fritz Lang for expressionist shadow, and Roger Deakins / Gordon Willis for single-source low-key exposure.

**Lighting & colour:** Commit to low-key chiaroscuro: one dominant hard directional source (streetlamp, doorway spill, headlight, a single practical) and let everything else fall to crushed black - the unlit space is the threat. Give the light a surface to work on: wet asphalt, rain, fog, steam, or venetian-blind slats so beams and reflections read. Palette is restrained and cold-leaning: teal/blue shadows against sodium-amber or tungsten highlights, high contrast, deep blacks, low overall saturation with one warm accent. Rim/backlight to separate the subject from the dark without revealing detail. Avoid flat fill, even exposure, and pretty gradients - thriller light is uneven, motivated, and hides more than it shows.

**Sound & voice:** Sound is the ticking clock - build tension through audio, not orchestral stings. Layer close-mic'd dry breathing, wet-concrete footstep echo, and a recurring off-screen mechanical repeat (clock tick, alarm, elevator ding, dripping pipe) that becomes the metronome the scene races against. Weaponize silence: drop the mix to near-zero right before a reveal, then hit. Under the diegetic layer, a low sub-bass pulse or a single detuned drone does more than a full score. Dialogue is clipped, low, half-whispered, urgent - no exposition speeches; one word ("go," "run," "now") beats a sentence. Ban horror jump-scare stingers and swelling strings.

**Steering vocabulary:** low-key lighting, chiaroscuro, telephoto compression, Dutch tilt, rain-slicked wet asphalt, sodium-vapor amber, crushed blacks, high contrast, negative-space isolation, handheld instability, silhouetted pursuer, creeping push-in, rack focus, claustrophobic corridor, hard single-source key, fog diffusion, shallow depth of field, surveillance high-angle, rim-lit backlight

**Example idea:** a guy running from someone at night in a city

- Bad: a scary chase scene at night, man running from a bad guy, super tense, ticking clock, dramatic cinematic lighting, 8k, ultra realistic, masterpiece, trending on artstation

- Improved: Night-time foot chase in a city alley. A man in a dark coat runs away from a hooded figure behind him. Wet streets reflecting streetlights, low-key lighting with strong shadows, shallow depth of field. Handheld camera following the runner, telephoto lens compressing the background. Cool blue and amber color grade, tense urgent atmosphere.

- Elite (video):

```
Medium backward-tracking shot, telephoto ~85mm compression, low camera height. A lone man in a rain-soaked grey overcoat sprints toward the lens down a narrow industrial alley, clutching a hard-shell briefcase to his chest, snapping a glance back over his right shoulder. Twenty meters behind him a single silhouetted pursuer walks - never runs - closing the gap by sheer steady inertia. Environment: dead-end corridor between soot-black brick warehouses, overflowing dumpsters, a chain-link fence sealing the far end. Lighting: low-key, one sodium-vapor streetlamp throwing hard amber pools with deep black gaps between them; wet asphalt mirrors every light source; a distant blue security floodlight bleeds through thin fog to rim-light the runner's shoulders. Camera: handheld-style subtle instability, tracking backward just ahead of the runner; shallow depth of field, background soft and compressed; hold the pursuer in focus-falloff so he reads as a shape, not a face. Motion: hard rain, breath fogging, puddles bursting under each footfall, coat flaring; the pursuer stays deliberately unhurried. Color grade: teal shadows, sodium-amber highlights, crushed blacks, high contrast. Sound: close-mic'd labored breathing, footsteps slapping wet concrete, a distant repeating alarm ticking like a metronome, no music. 6-second shot, real-time pacing, ending the instant the runner hits the dead-end fence.
```

**Negative prompt:** flat even lighting, bright cheerful daylight, smiling or relaxed subject, calm neutral body language, clean dry streets, warm cozy tones, over-saturated colors, neon cyberpunk pink/blue wash, multiple competing focal points, crowd of bystanders, fully-lit clearly-revealed villain, cartoonish, plastic skin, lens-flare overload, two suns / contradictory shadow directions, motion blur smearing the subject's face, static posed portrait, text overlay, watermark, jump-scare face lunging at camera, glowing knife, red digital bomb countdown on screen

**Checklist:**
- One subject, one threat, one location - is the focal hierarchy unambiguous?
- Exactly one dominant light source, with all shadows consistent to it (no two-suns error)?
- Is the ground wet/reflective or the air hazy - does the light have a surface to bounce off?
- Is the threat under-revealed (silhouette / partial / soft-focus) rather than fully lit?
- Is a ticking-clock element present in image or sound?
- Video only: does every camera move answer a specific fear - nothing decorative?
- No contradictions - not 'locked-off static' AND 'handheld', not 'bright noon' AND 'deep shadow'?
- Does body language read tension (glance, tensed hands, held breath) rather than a neutral or smiling pose?
- Are blacks crushed and contrast high - did you avoid flat, even exposure?

### Horror

**Visual ingredients:** one clear, isolated subject placed small in a large frame; generous negative space and empty foreground the eye keeps scanning; a single wrongness detail (off proportion, unnatural stillness, a limb too long, a smile that doesn't move); a believable practical light source with everything else crushed to shadow; the threat concealed - facing away, cropped at frame edge, behind a doorway, or dissolved into darkness; texture of decay: peeling paint, damp, rust, dust in a light shaft; reflective or transparent surface (wet floor, window, mirror) that could hold a second presence; an ordinary object made ominous by context (empty chair, single shoe, running tap); stillness held past the point of comfort

**Motifs:** a long empty corridor receding into black; an open doorway leading to pure darkness; a figure standing motionless with its back to camera; a distant standing shape at the far edge of visible light; peeling paint and water-stained walls; a stuttering, dying fluorescent tube; net curtains or plastic sheeting breathing in still air; a mirror or dark window holding an extra reflection; wet floor reflections doubling the room; dust and particulate drifting through a single light shaft; an overexposed white window blowing out to nothing; a face obscured - turned away, in shadow, behind hair or cloth; an out-of-place everyday object (empty wheelchair, lone chair, single child's shoe)

**Cliches to avoid:** glowing red eyes in the dark; a face lunging at camera with an orchestral BANG; blood and viscera smeared everywhere as a shortcut to fear; the long-black-hair-over-the-face ghost girl; the backwards-crawling / joint-popping contortionist; on-the-nose text scrawled in blood on a wall; a fully-revealed CGI monster shown in bright, even light; fog-machine haze filling every frame; spooky magenta-and-lime color grading; the mirror-scare where the reflection turns on its own; a cat or falling object as a fake-out jump scare; rapid strobe cutting to fake tension the composition hasn't earned

**Camera:** Favor stillness and duration over coverage. Lock-off wide and full shots (28-40mm) that place a small, wrong subject inside a large empty frame - the space around the threat does the scaring. Slow, almost imperceptible push-ins (dolly or dead-slow zoom) that make the viewer feel dragged toward something they don't want to reach. Long unbroken holds - the scare lives in how long you refuse to cut. Eye-level as the default so the world reads as ordinary and real; break to a low angle only to make a figure loom, or a high angle only to make a victim small. Use the edge of frame and offscreen space as a weapon: keep the threat partly cropped, behind a doorframe, or just out of view so the audience's eye hunts for it. Rack focus to reveal a shape that was always in the soft background. Handheld ONLY for a panic beat - otherwise the tripod's calm is the dread. Avoid fast cutting, whip pans, snap zooms, and drone swoops; they read as action, not horror. Deep-focus the far background so a distant standing figure stays sharp enough to register but small enough to doubt.

**Lighting & colour:** Single motivated source, always. Light from a practical the viewer can believe - a failing bulb, a TV, an exit sign, a phone screen, moonlight through a window - and let everything else fall into deep shadow. Underexpose on purpose; keep shadows dark but retain a thread of detail so the eye keeps searching (pure black mush kills dread). Chiaroscuro and silhouette over illumination: reveal the threat as a shape, an edge, an absence, not a fully lit creature. Cold-vs-warm tension (blue-green pools against a lone sodium or ember-warm accent) reads as wrongness. Desaturate the whole frame and permit at most one accent color - a single red, a jaundiced yellow. Top-light or under-light faces to make them uncanny; never key a face flatteringly. Flicker, dying bulbs, and slow exposure shifts are your friends. Avoid: even three-point lighting, high-key brightness, saturated horror cliches (magenta/lime), and any setup where you can't name the in-world light source.

**Sound & voice:** Silence is the loudest tool - build long stretches of near-silence so the room's own presence becomes unbearable. Base layer: a low sub-bass drone or a barely-there room tone (refrigerator hum, distant HVAC, the ringing of a too-quiet space) that you can drop out entirely to make a held shot feel airless. Sparse, hyper-specific foley: a single dripping tap, a floorboard settling, cloth dragging, a breath that isn't the protagonist's. Refuse the musical stinger - let dread accumulate and resolve into stillness rather than a loud hit. If voice is used, keep it small and wrong: whispered fragments just under intelligibility, non-language, a child's rhyme sung flat and slow, breathing or wet swallowing close-mic'd (ASMR-adjacent proximity is unnerving). Dialogue delivery is hushed, halting, under-reacting - characters who stay too calm read as more disturbing than screamers. Never wall-to-wall score; the absence of music where a viewer expects it is itself a scare.

**Steering vocabulary:** dread, stillness, wrongness, the uncanny, derelict, liminal, motivated single-source light, deep shadow, negative space, underexposed, desaturated, sickly green, bruise-blue, sodium spill, flickering fluorescent, locked-off, held take, facing away, too still, slow push-in, clammy, hush, offscreen presence, 16mm grain, chiaroscuro, silhouette, implied not shown

**Example idea:** make a scary video of a haunted hospital with a ghost

- Bad: a scary haunted hospital at night, creepy ghost woman, glowing red eyes, blood everywhere, jump scare, horror, dark and spooky atmosphere, nightmare fuel, terrifying, ultra detailed, 8k, cinematic, trending on artstation

- Improved: An abandoned hospital corridor at night. Far down the hall, a still figure in a gray hospital gown stands facing the wall. Cold moonlight through broken windows, peeling paint, wet linoleum with faint reflections. Locked-off wide shot, eye-level, desaturated blue-green palette, deep shadows, film grain. Quiet, uneasy mood. No gore.

- Elite (video):

```
Static locked-off wide shot, 32mm lens, eye-level, of a long derelict hospital corridor at 3am. FOREGROUND: an empty wheelchair angled toward camera, one wheel slowly rotating to a stop, then still. MIDGROUND: peeling sea-green paint, a single overhead fluorescent tube stuttering on and off every few seconds. BACKGROUND, ~20 meters deep and held in sharp focus: a motionless woman in a pale gray hospital gown, facing the wall - a little too tall, arms hanging past her knees, face never shown. The camera never moves. On the second fluorescent flicker she is one step closer, though no step is seen; each blackout is fully black so the reposition hides inside the dark. On the third flicker she is gone from the background and now stands just beside the wheelchair, still facing away. Hold on her for three seconds. Then cut to black. LIGHTING: one failing overhead fluorescent, cold 5600K stutter, plus warm sodium spill from a distant exit sign; deep unlit shadows, no fill, threat lives half in darkness. PALETTE: desaturated sickly green and bruise-blue with a single dull red exit glow. Wet floor reflections, slow settling dust drifting through the light. Heavy 16mm grain, faint gate weave, deep focus. MOOD: dread, stillness, wrongness. No score, no gore, no sudden loud sting - the horror is the proximity and the hold.
```

**Negative prompt:** gore, blood splatter, viscera, glowing red eyes, screaming face lunging at camera, cheap jump scare, smiling ghost, full-body CGI monster shown in bright light, cartoon demon, contorted extra limbs, melted artifact faces, oversaturated colors, purple and neon-green fog, fog-machine haze everywhere, flat even lighting, bright cheerful daylight, clean modern renovated interior, lens flares, motion blur mush, watermark, on-screen "scary" text, multiple competing subjects, cluttered busy frame, orchestral sting

**Checklist:**
- Is there exactly one clear subject, with the frame uncluttered around it?
- Is the threat restrained - implied, partial, or facing away - rather than fully shown and lit?
- Can you name the single in-world light source, with everything else in deliberate shadow?
- Do the shadows keep a thread of detail instead of collapsing to black mush?
- Is the palette desaturated with at most one accent color?
- Any contradictions? (two light directions, two subjects, calm-mood + fast-cut) - remove them.
- Is there real negative space the eye can wander into and dread?
- Is camera motion justified - a slow push or a deliberate lock-off, not a swoop or whip?
- Is the fear carried by composition, stillness, and timing rather than gore?
- For video: does the scare land on a HELD shot or a hidden-in-darkness reposition, not a loud cut?
- Is grain/texture and a concrete decay detail specified?
- Sound plan = silence plus one low drone, no musical stinger?

### Documentary / Nonfiction (Observational, Narration-Led)

**Visual ingredients:** a real human subject anchored in genuine context (a trade, a home, a landscape they belong to); hands performing an actual task - the labor rendered in close detail; authentic wardrobe that shows wear, not costume; tools and objects of a real occupation; environmental detail that dates and locates the scene (weather, signage, wear on surfaces); background life continuing independently of the subject; natural light spilling from a nameable source; a catchlight in the eye for interviews; honest texture: grain, dust, salt, rust, sweat, condensation

**Motifs:** fine 16mm grain and faint gate weave; a slow push-in or pan across a still photograph (the Ken Burns move); rack focus from a foreground detail to the subject; dust or breath suspended in a shaft of raking light; condensation and weather on a window between camera and subject; a real sun flare that the operator didn't hide; the subject's hands mid-task, framed without the face; a locked interview frame with soft window key and shallow environmental bokeh; faded archival stock - Super 8 warmth, VHS chroma bleed, magenta-shifted Kodachrome

**Cliches to avoid:** opening on a soaring drone hero shot; the teal-orange 'epic' grade that kills realism; staged eye-contact with the lens while claiming fly-on-the-wall observation; swelling orchestral or sad-piano score manipulating the emotion; everything in slow motion; flawless three-point studio lighting on a supposedly candid moment; over-stabilized gimbal glide that feels like a real-estate ad; generic slow-motion silhouette-against-sunset; fake film-burn and light-leak overlays pasted on for 'authenticity'; glossy commercial polish and plastic skin

**Camera:** Shot sizes carry meaning here: open on a wide establishing shot to place the subject in a real location, live in medium shots (waist-to-chest) for interviews and work, and cut to tight close-ups on hands, faces, and worn texture for evidence. Documentary reads as documentary because the camera is a witness, not a director. Lenses: fast primes (35mm, 50mm, 85mm) for low available light and honest perspective; a long lens (135mm+ or 70-200 zoom) for compressed, unobtrusive observation from distance; the slight "documentary reframe zoom" is allowed. Angles: eye-level and non-editorializing - the camera does not flatter or condemn. Movement: shoulder-mounted handheld with organic micro-jitter for verite follow shots; a locked tripod for sit-down interviews; and for stills/archival, the slow patient push-in or pan across a photograph (the Ken Burns move). Golden rule of observational mode: the subject never looks into the lens and appears unaware of the camera. Interviews are the one exception - off-axis eyeline just past the lens, or straight down the barrel (Errol Morris Interrotron style) for confrontational intimacy. Prefer one continuous take over cuts; let action complete inside the frame.

**Lighting & colour:** Motivated, available light is the whole game - the light must look like it came from a real source in the scene, not a rig. Key sources: a window, an open overcast sky, golden-hour sun raking low, a bare practical bulb, the green-cyan cast of office fluorescents left honest rather than corrected. For interviews: a soft window key with gentle or zero fill, a visible catchlight in the eye, and shadow allowed to fall naturally. Color is naturalistic and restrained - true skin tones, slight desaturation, and NO fantasy grade. Overcast reads cool blue-gray; interiors read warm tungsten; midday reads neutral. Let one color cast dominate per location and keep it consistent. Archival looks have their own honest grades: faded Kodachrome with magenta shift, VHS chroma bleed and scanlines, Super 8 warmth and gate weave. The failure mode to reject is the "epic" cinematic color that turns nonfiction into a car commercial.

**Sound & voice:** Two voices carry the genre. First, narration: measured and human, never breathless - journalistic and plain (broadcast VO), essayistic and reflective (Herzog tradition), or hushed and awe-struck for natural history (Attenborough tradition). Pick one register and keep it. Write narration as spare fact over image, not as emotional coaching; let the picture do the feeling. Second, sync and wild sound: lavalier-mic dialogue with natural pauses, breaths, and imperfections left in; location room tone; ambient wild sound (water, wind, machinery, birds, distant traffic) that grounds the scene as real. Score is restrained or absent - if used, a single sustained drone, sparse piano, or low strings, never a swelling cue that tells the audience how to feel. For interviews, capture the pause before the answer; that silence is the truth. Keep audio unpolished enough to feel recorded on location, not mixed in a studio.

**Steering vocabulary:** observational, cinema verite, direct cinema, fly-on-the-wall, available light, motivated light, handheld, shoulder-mounted, run-and-gun, unstaged, candid, unaware of camera, environmental portrait, long-lens compression, patient long take, naturalistic color, 16mm grain, archival, lived-in texture, wild sound, room tone, unobtrusive, verite follow shot, slow push-in on a still

**Example idea:** a doc shot of an old fisherman getting his boat ready in the early morning

- Bad: A cinematic documentary of an old fisherman, beautiful, epic, 4k, ultra detailed, dramatic lighting, masterpiece, award winning, trending.

- Improved: Documentary-style shot of an elderly fisherman preparing his small wooden boat at a harbor in the early morning. Handheld camera, natural overcast light, medium shot, realistic skin and fabric, subtle film grain, muted colors. He is focused on his work and does not look at the camera.

- Elite (video):

```
Observational documentary, cinema verite. ONE subject: a weathered fisherman in his late sixties, gray stubble, an orange oilskin bib over a faded blue wool sweater, kneeling on the deck of a small wooden trawler to coil a wet mooring rope by hand. Location: a working harbor at first light, low tide, the hull streaked with salt and rust, gulls perched on barnacled pilings behind him. Shot size: medium shot, waist-up, subject framed slightly off-center and unaware of the camera - he never looks to lens. Lens: 50mm at shallow depth of field, background boats softening into bokeh. Camera: shoulder-mounted handheld with subtle organic micro-movement, holding first on his hands as they work the rope, then a slow patient reframe up to his face. Lighting: available light only - cold overcast dawn, a soft directional key from the open sky camera-left, a faint catchlight in his eyes, no fill, honest shadow under the brim of his cap. Color: naturalistic and slightly desaturated, true skin tones, a cool blue-gray morning cast; NO teal-orange grade. Texture: fine 16mm-style grain, real wet wood and coarse wool detail, his breath faintly visible in the cold. Motion inside the frame: the rope coiling in his hands, water lapping the hull, one gull lifting off in the background. Single continuous take, roughly 8 seconds, no cuts. Audio: wild ambient only - lapping water, distant gull calls, the creak of rope and timber; sparse measured narration in a low, matter-of-fact register over the top, no music.
```

**Negative prompt:** teal-orange cinematic grade, glossy commercial polish, over-stabilized gimbal glide, slow-motion, staged eye contact with camera (in observational shots), actor-perfect studio three-point lighting, softbox glamour, beauty-dish key, swelling orchestral score, sad manipulative piano, lens flare whip-pans, drone hero opener, fake film-burn and light-leak overlays, oversaturated colors, HDR halos, plastic AI skin, waxy over-smoothing, warped hands, extra fingers, duplicated subject, mismatched period props, floating text, watermark, logo, subtitle bar

**Checklist:**
- Exactly ONE clear subject, and in observational mode they do NOT look at the lens
- Light is motivated - you can name the real source (window, sky, practical, sun)
- No contradictions: overcast dawn can't also be warm golden hour; one time-of-day, one color cast
- Camera behavior is specified and honest - handheld micro-movement or locked tripod, not a floaty gimbal, unless the scene earns it
- Shot size + lens + movement are all stated, not left to the model to guess
- Color is naturalistic and restrained - no teal-orange or fantasy grade
- Concrete nouns beat vague adjectives: 'salt-streaked wooden hull' over 'beautiful boat'
- Texture and grain called out so the frame reads as recorded, not rendered
- For video: narration register and wild/sync sound specified; score restrained or absent
- Action completes inside the frame and can hold as a single take

### Nature & Wildlife

**Visual ingredients:** Precise species with correct field marks (plumage or pelage pattern, size, silhouette, sex/age); A live catchlight and tack-sharp focus on the near eye; A specific behavior or decisive moment (strike, pounce, feeding, grooming, display, yawn, take-off); Biome-correct habitat context (right plants, terrain, substrate, season); Backlit rim on fur, feather, breath, or spray; Fine micro-texture: individual fur strands, feather barbs, whiskers, scales, dew droplets; Frozen motion elements (snow burst, water-splash crown, dust, wingbeat); Negative space in the direction of gaze or movement; Telephoto background compression into soft bokeh; A natural, biome-accurate color palette with no oversaturation

**Motifs:** Catchlight glinting in a dark eye; Breath fog in cold dawn air; Water-splash crown as a bird lifts off; Backlit fur halo or translucent mane edge; The animal mirrored in still water; Dew or frost beaded on fur and whiskers; Wingtip fully spread at the top of a beat; A lone silhouette or fresh tracks against dawn mist; Dust kicked up by hooves in raking side-light; Locked eye-contact from ground level

**Cliches to avoid:** Anthropomorphized smiling or human facial expressions; Dead-center, static, front-facing portrait as the only framing; HDR clown-saturation and glowing neon eyes; Plastic, over-smoothed CGI fur/feathers with no individual strands; Wrong-biome mashups (penguins near palm trees, tigers on African savanna); The lone-lion-on-a-rock-at-sunset or eagle-head trophy shot by default; Zoo-fence/baiting tells and taxidermy stiffness; Requesting logos, watermarks, or 'National Geographic' / award badges; Impossible hybrid/chimera anatomy or wrong number of limbs; Cute-calendar framing that ignores real behavior

**Camera:** Live on long glass. Super-telephoto primes are native to the genre - 400mm f/2.8, 500mm f/4, 600mm f/4, 800mm; long focal length compresses the background into a clean wash and fills the frame without crowding the animal. Shoot at the animal's eye level or below: drop to ground/water height (beanbag, low tripod, floating hide) so you look into its world, not down at it - this one choice separates a snapshot from wildlife work. Aperture wide (f/2.8-f/5.6) to lift the subject off its habitat with creamy bokeh; stop to f/8 when two eyes or a whole group must stay sharp. Shutter is behavior-driven: 1/2000-1/4000 to freeze birds-in-flight or a splash, 1/1000 for a walking mammal, drop to 1/30-1/60 for a deliberate motion-blur pan on a running herd. Shot sizes: extreme close-up (eye + catchlight), tight portrait (head and shoulders), medium (full body in habitat), wide/environmental (animal small in landscape for scale and story). Compose negative space in the direction of gaze or travel; use animal-eye autofocus and a track-and-pan for flight. Video: slow-motion 120-240fps for wingbeats and predator strikes, gimbal or long-lens tripod pan to track, a static locked-off camera-trap for shy nocturnal species, drone top-down ONLY for herds and landscape scale, never low over animals.

**Lighting & colour:** Golden hour is home light: low, warm, raking sun at dawn or dusk models fur and feather, throws long shadows, and rims a backlit animal with a glowing halo. Backlight is the signature move - it lights the translucent edge of fur, the fringe of a mane, breath vapor, and kicked-up dust or spray into an outline. Overcast sky is a giant softbox: even, shadowless light that resolves every feather barb and fur strand and saturates wet-forest greens - best for detail and for dark or high-contrast animals. Blue hour and pre-dawn mist give cool, low-saturation atmosphere (herons, wetlands, deer in fog). Always put a catchlight in the eye - a live animal has a bright spec in a dark eye; without it the eye reads dead or taxidermied. Color: hold to the true palette of the biome - muted tundra tans and greys, boreal blues, desaturated savanna dust, saturated tropical canopy - and refuse HDR clown-saturation. Match color temperature to the hour (warm gold at sunrise, cool blue in shade and snow) and let warm subject against cool shadow do the separation.

**Sound & voice:** Design for restraint and realism. Bed: the specific biome ambience - dawn chorus for temperate woodland, cicada wall for the tropics, wind-over-tundra hiss, wetland reeds with distant calls, lapping water. Foreground the animal's own sounds at close mic perspective: wingbeats with audible air displacement, hoof-falls on the substrate, the crunch of feeding, a territorial call, breathing, snow compressing underfoot. Sync a subtle low-end whump to a pounce or take-off; let a predator strike land in near-silence, then snap. Avoid wall-to-wall orchestral score - if music is present keep it sparse and low, entering only under wide establishing beats and dropping out for behavior. Voiceover, if any: a calm, unhurried, low-register naturalist narration - plain declarative sentences about what the animal is doing and why, never anthropomorphizing, never over-dramatizing, with long pauses that let the natural sound breathe. Default to NO narration over the decisive moment; let the wingbeat or the splash carry it.

**Steering vocabulary:** telephoto compression, super-telephoto, eye-level low POV, catchlight, backlit rim light, shallow depth of field, creamy bokeh, tack-sharp eye, decisive moment, in situ / natural habitat, plumage, pelage, mid-stride, wingbeat frozen, breath vapor, field marks, biome-accurate, motion-blur pan, animal-eye autofocus, dappled light, golden hour, blue hour, documentary realism

**Example idea:** a fox in the snow

- Bad: a beautiful majestic fox in the snow, highly detailed, 4k, 8k, stunning, award winning nature photography, cinematic, hyperrealistic

- Improved: A red fox standing in fresh snow during golden hour, telephoto lens, shallow depth of field, sharp focus on the eyes, soft warm backlight, natural winter forest background, wildlife photography, muted natural colors

- Elite (image):

```
Wildlife photograph of a single wild red fox (Vulpes vulpes) in full winter coat, caught mid-pounce in a "mousing" dive: front legs tucked to the chest, muzzle angled straight down toward the snowpack, hind legs extended, body arched in a near-vertical leap about half a meter above an open snowfield, hunting a vole beneath the crust. Dense russet-orange pelage, white throat and belly, black-stockinged legs, bushy white-tipped tail streaming behind. Loose powder bursts upward from the launch point with individual snow crystals frozen in mid-air; faint breath vapor at the nostrils. Setting: open boreal meadow at the edge of a bare birch stand, early morning after fresh snowfall. Low winter sun rakes in from camera-left as warm backlight, rim-lighting the fur into a glowing halo and throwing one long cool-blue shadow across the snow. Bright catchlight in the dark amber eye; tack-sharp focus on the eye and muzzle. Shot on a 600mm f/4 super-telephoto from a low, near-ground eye-level position, aperture f/5.6, shutter 1/3200 to freeze the powder, shallow depth of field compressing the out-of-focus birch trunks into soft vertical bokeh. Cool blue shadow tones against warm gold highlights, restrained natural winter palette, fine fur-and-snow-crystal micro-texture, no oversaturation. Documentary wildlife realism.
```

**Negative prompt:** cartoon, anthropomorphic, human facial expression, smiling animal, extra limbs, missing limbs, wrong number of legs, fused paws, malformed anatomy, chimera, hybrid animal, two heads, duplicated animal, oversaturated, HDR clown colors, neon glowing eyes, plastic CGI fur, over-smoothed, waxy, blurry eye, soft focus on eye, dead eyes without catchlight, taxidermy stiffness, cluttered distracting background, wrong species markings, wrong biome, zoo fence, cage bars, leash, collar, human hands, watermark, logo, text, signature, award badge, frame border, motion smear on a static subject

**Checklist:**
- Species named precisely and field marks correct for that species, sex, and age?
- ONE clear subject with a live catchlight and the near eye tack-sharp?
- The animal does something specific (a named behavior/moment), not just poses?
- Habitat biome-correct - right plants, terrain, season, and light for where this animal actually lives?
- Light source named (golden hour / overcast / backlit) and consistent with the shadows and rim?
- Lens, aperture, shutter, and camera height stated - and does shutter match the motion (freeze vs pan)?
- Anatomy sane: correct limb/leg/wing count, natural pose, no fusion or extra parts?
- Color palette biome-accurate and un-HDR'd, with intentional warm/cool separation?
- No contradictions (backlight plus strong front-fill, f/2.8 plus everything sharp, midday plus long shadows)?
- Negative space toward gaze/travel and background compressed, not cluttered?

### Space & Cosmos

**Visual ingredients:** one dominant subject (astronaut, ship, planet, or deep-field object); a foreground object of known size as a scale reference; correct-density star field (sparse, sharp, not overcrowded); planetary limb with a hard day/night terminator; single hard star as the only key light; true-black shadow fill (no ambient bounce); thin atmospheric limb glow on habitable worlds; Milky Way band or deep-field galaxy scatter for depth

**Motifs:** the curved bright limb of a planet against black; the knife-edge terminator dividing day and night; a lone tethered astronaut adrift; a spacecraft silhouette crossing a star or sun; banded gas-giant cloud tops with a ring shadow; harsh-shadowed lunar/asteroid regolith; night-side city lights and aurora ovals; a distant sun as a small hard point with a controlled bloom

**Cliches to avoid:** oversaturated rainbow nebula as wallpaper; default purple-and-teal cosmic gradient; diffraction spikes on every single star; dense fly-through asteroid field like a demolition derby; two planets and five moons crammed together in one sky; audible explosions, whooshes, and engine roar in space; stars that twinkle or streak in a vacuum; a giant moon pasted impossibly large on a flat horizon; lens-flare and god-ray overload; 'epic 4k stunning breathtaking masterpiece' adjective filler

**Camera:** Space has no atmospheric perspective, so depth reads only through parallax and relative size - that governs every choice. Shot sizes: EXTREME WIDE to establish true scale (a planet's curved limb filling the frame with a tiny spacecraft speck for reference); WIDE/FULL for an astronaut against a planet; MEDIUM on a helmet, controls, or hull detail; MACRO for regolith, dust, ice grains, or hull rivets. Lenses: virtual 35-50mm for a natural, undistorted "eyes in a helmet" read; 85-135mm to compress a distant planet large behind a near subject; avoid wide-angle fisheye unless intentionally simulating a helmet visor or porthole. Angles: put the horizon-limb off-level - there is no "up" in orbit, and a tilted planetary curve instantly signals real spaceflight; low-across-the-hull angles emphasize mass. Movement: SLOW, GEOMETRIC, CONSTANT-VELOCITY only - orbital drift, a locked-off spin of the subject with fixed stars behind, a patient dolly/push-in, or a rotisserie orbit around a ship. Motion must obey physics: near objects parallax-shift against a FIXED star field; the stars themselves never blur, streak, or slide. No handheld shake in a vacuum (unless simulating a hull-mounted or helmet cam). Reveal scale by starting tight on a reference object then pulling back to expose the vastness.

**Lighting & colour:** One hard key, one star. In a vacuum there is NO ambient fill - the shadow side of any object goes to true, jet black, with a knife-edge terminator between lit and unlit. The star's spectral class sets the whole palette: hot O/B stars ~10,000K+ blue-white, Sun-like G ~5,800K warm-white, cool M-dwarf ~3,000K orange-red. Planets get a single bounce: a thin Rayleigh-blue atmospheric limb glow on Earth-likes, ochre haze on dusty worlds, banded cloud tops on gas giants with a hard ring-shadow if ringed. Nebula color must be physically motivated, not decorative: emission nebulae glow H-alpha RED (star-ionized hydrogen), reflection nebulae are BLUE (starlight scattered off dust), planetary nebulae lean teal-green (OIII). Deep-space background is near-black, not navy or purple. Grade for HDR with restraint - let highlights on a sunlit hull clip believably while shadows stay black; do NOT push global saturation. Faint city lights on a planet's night side and aurora ovals read only against that black. Kill any soft, even, "studio-lit" fill - it destroys the read of vacuum.

**Sound & voice:** Space is silent - the single strongest, most-broken rule. Sound design: near-total vacuum silence for anything exterior; NO whooshes for passing ships, no audible explosions, no engine roar in the void. All real sound is diegetic and inside a suit or hull: measured breathing, radio comms with light static and squelch, a low suit-fan or servo hum, the click of a switch. Under it, a sparse sub-bass drone or slow pad to carry scale and awe - think weight and vastness, not melody. Silence is the effect; let long beats breathe with nothing but a heartbeat or breath. Voiceover: calm, low-register, unhurried, documentary-naturalist restraint - the measured wonder of a great science broadcaster (Sagan-style cadence as a technique reference, never a quote). Short declarative lines, generous pauses, no bombastic movie-trailer bark, no hype adjectives. Let the images and the silence do the awe; the voice only points.

**Steering vocabulary:** single hard key light, razor-sharp terminator line, pin-sharp non-twinkling stars, orbital parallax drift, true-black vacuum shadows, Rayleigh-blue atmospheric limb glow, H-alpha red emission nebula, reflection-nebula blue, Milky Way dust lane, constant-velocity camera drift, foreground scale reference, photoreal HDR, restrained saturation, curved planetary limb, specular hotspot on visor, tilted horizon, no true up

**Example idea:** an astronaut looking at a planet, make it look epic

- Bad: epic space scene, beautiful galaxy, stars, planets, nebula, astronaut, 4k, stunning, cinematic, colorful cosmos, breathtaking, ultra detailed, wow amazing masterpiece

- Improved: A lone astronaut in a white spacesuit floating in orbit, looking down at a blue Earth-like planet below. Hard sunlight coming from the left, dark starry sky behind. Wide shot, realistic detail on the suit and visor reflection, cinematic photoreal look, high dynamic range.

- Elite (video):

```
Slow constant-velocity orbital tracking shot, 8 seconds, no cuts. SUBJECT: a single lone astronaut in a white EVA suit, tethered to the battered hull of an orbital station, positioned in the lower-left third of the frame, back-lit by a hard white-blue sun just outside frame right. LOWER RIGHT TWO-THIRDS: the curved limb of an ocean planet - deep sapphire sea, swirling white cloud bands, a razor-sharp day/night terminator line cutting diagonally across it, faint golden city lights sparkling on the night side, a thin Rayleigh-blue atmospheric glow along the limb. UPPER BACKGROUND: flat true-black vacuum scattered with pin-sharp, non-twinkling stars and the faint dust-lane band of the Milky Way arcing toward the top-right corner. LIGHTING: single hard key from the off-frame sun, zero fill, jet-black shadow on the astronaut's far side and the station's underside, a bright specular hotspot skating across the gold visor. MOTION: camera drifts slowly and smoothly to the right at constant velocity; the near astronaut and station parallax-shift against the fixed, motionless star field; planet rotates almost imperceptibly. Stars stay perfectly steady and un-blurred. Physically accurate vacuum - silent, no lens-dust streaks, no motion blur on stars, no atmospheric haze in the black. Virtual 50mm lens, shallow orbital drift, no handheld shake, tilted horizon (no true up). Photoreal HDR grade, restrained saturation, subtle film grain, clean highlight roll-off. One dominant subject, deep depth cue by scale.
```

**Negative prompt:** twinkling stars, star streaks, motion blur on stars, diffraction spikes on every star, oversaturated rainbow nebula, purple-and-teal gradient sky, dense Hollywood asteroid field, multiple planets or moons crowded impossibly close, atmospheric haze or fog in vacuum, soft even ambient fill light, gray or navy space instead of black, lens-flare spam, fisheye distortion, warped or wobbling horizon, audible sound cues, level tabletop horizon, cartoonish, plastic CGI look, extra limbs on astronaut, malformed helmet, floating text, watermark, logo, UI overlay, low dynamic range, blown-out flat exposure

**Checklist:**
- One dominant subject and a clear foreground scale reference?
- Single hard key light with true-black vacuum shadows and no fake fill?
- Stars pin-sharp, non-twinkling, and at believable (not overcrowded) density?
- Terminator line hard and correctly oriented to the light source?
- Nebula/planet colors physically motivated (red emission, blue reflection, spectral-correct star), not a rainbow?
- If video: silent vacuum, diegetic-only sound, no whooshes; stars fixed while near objects parallax?
- No impossible crowding of planets, moons, or asteroids?
- Horizon tilted (no false 'up'), no fisheye or warped limb?
- Color grade HDR but restrained - no global saturation slop, black stays black?
- No diffraction-spike spam, lens-flare overload, text, or watermark?

### Religion & Mythology - Sacred Gravitas, Symbol, Myth

**Visual ingredients:** One dominant divine or mythic subject with a clear scale relationship to any mortal/creature present; Sacred architecture as frame: columns, arches, altars, thresholds, carved reliefs; Ritual objects that carry meaning (chalice, censer, trident, scroll, mask, offering bowl); A single directional shaft of symbolic light (god-ray, altar fire, candle); Iconographic symmetry or deliberate hieratic composition; Aged, real materials: verdigris bronze, gold leaf, weathered stone, cracked fresco, oiled wood; Robes and drapery with weight and fold logic; A halo / aura / mandorla device used sparingly, not on everything; Natural elements standing in for divine force: fire, water, storm, stars, serpents; Hands frozen in a legible gesture - blessing, offering, warning, mudra

**Motifs:** God-ray cutting through incense or dust in a dark hall; Gold-leaf halo and lapis-blue robe of Byzantine icon painting; Cracked, faded fresco surface with flaking pigment; Verdigris-green oxidized bronze on a monumental statue; Candlelit gloom with a single warm ignition point; A colossal silhouette standing against an open sky; A processional line of small robed figures moving toward a shrine; A threshold or doorway framing the passage into sacred space; A cosmic star-map or wheel of constellations overhead; Archetypal symbols: the tree, the eye, the serpent, the flame, the wheel

**Cliches to avoid:** Generic winged angel with a bodybuilder torso and feathered CGI wings; Glowing blue 'magic energy' standing in for the sacred; Renaissance-face-swap realism pasted onto every deity; Symmetrical mandala kaleidoscope overload with no depth; 'epic 8k artstation masterpiece' keyword slop; Floating embers, sparkles and debris scattered over everything; Halo rendered as a cheap lens-flare ring; Culturally mashed-up symbol salad (cross + om + ankh + pentagram together); AI plastic-shiny fake gold with no age or wear; Hyper-symmetry so total it flattens the image into wallpaper

**Camera:** Reverence is built with the camera before it is built with the subject. Default to a LOW ANGLE looking up so the divine towers and the viewer kneels; use frontal SYMMETRY (altar / icon framing) when you want stillness and authority, and a slight three-quarter turn when you want a living deity rather than a flat idol. Reach for wide establishing shots (24-35mm) for temples, processions and cosmic scale; long lenses (85-135mm) for votive portraits and the reverent compression of a face or a pair of hands. Reserve extreme close-ups for the load-bearing details - eyes, a blessing gesture, a relic catching the only light. For video, keep every move slow and deliberate: a vertical tilt climbing a statue from feet to face, a locked dolly-in toward an altar, a patient orbit around a shrine, a god's-eye high-angle descent for a divine POV. Keep the horizon LOW in the frame. Deep focus for sacred architecture; shallow focus only to isolate one object of veneration. Hard rule: no handheld jitter, no whip-pans, no drone-swoop spectacle - motion must read as processional, not kinetic.

**Lighting & colour:** Commit to ONE dominant directional source - a god-ray, an altar fire, a single candle, a break in storm cloud - and let strong chiaroscuro do the emotional work; keep the scene low-key with a few deliberate highlights rather than flat, even fill. Use temperature contrast to separate the sacred from the profane: warm gold or amber on the divine against cold stone-grey or teal shadow. Pull the palette from a single tradition instead of mixing - Byzantine gold + lapis + oxblood; storm slate + verdigris + bone; desert ochre + white + indigo. Push volumetric light through smoke, incense or dust to make the beam visible, and rim-light the holy figure to lift it off a dark ground. Avoid uniform daylight, neon glow, and rainbow saturation - they kill gravitas.

**Sound & voice:** Build the bed from sustained low drones and slow sub-bass swells, punctuated by a single struck bell, gong or bowl that is allowed to fully decay into silence. Layer a distant, wordless choir or throat-singing pad low in the mix, plus close-recorded natural elements the scene actually contains (wind through stone, dripping water, guttering flame, rain on the sea). Treat SILENCE as an instrument - let it sit before and after the strike. Voice: sparse, low, unhurried, with stone-room reverb as if spoken inside a temple; never wall-to-wall narration, a handful of weighted lines. Avoid pop-score stingers, triumphant Hollywood brass hits, breathy ASMR whispering, and busy percussion.

**Steering vocabulary:** hieratic, iconographic, monumental, votive, liturgical, numinous, apotheosis, chiaroscuro, gilded, verdigris, weathered, cavernous, processional, consecrated, oracular, tenebrous, effigy, reliquary, aeonic, threshold

**Example idea:** a picture of the greek sea god rising out of a stormy ocean holding his trident

- Bad: poseidon god of the sea, epic, powerful, highly detailed, 8k, cinematic lighting, dramatic, masterpiece, trending on artstation, beautiful, ultra realistic, glowing energy

- Improved: Poseidon, ancient Greek sea god, rising from a stormy ocean and holding a trident, huge dark waves around him, storm light breaking through heavy clouds, low camera angle looking up at him, moody teal and grey color palette, oil painting style, cinematic and dramatic.

- Elite (image):

```
Low-angle wide shot of a single colossal elder sea-god rising waist-deep out of a black storm-ocean, his body filling the right two-thirds of the frame, turned three-quarters toward camera. Weathered bronze skin crusted with barnacles and kelp, a beard of streaming seawater, both hands gripping a corroded verdigris three-pronged trident held vertically so its tines catch the light. Foreground lower-left: a small wooden sailing ship tilting on a wave crest, tiny against him, giving scale. Background: a towering wall of green-black waves under a low, bruised storm horizon. SINGLE light source - one break in the thunderheads casting a cold silver god-ray straight down onto his face and the trident tines; deep teal shadow fills the wave troughs; foam edges are rim-lit white. Limited palette: slate grey, verdigris bronze, bone-white foam, with one warm amber glint in the eyes. Driving rain and sea-spray streak diagonally across the frame; volumetric mist hangs at the waterline. Mood: ancient, monumental, indifferent. Rendered as painterly oil on canvas in the tradition of J.M.W. Turner's storm-sea light - visible brush texture, atmospheric depth, matte finish, deep focus, horizon kept low. No text, no watermark, no modern objects.
```

**Negative prompt:** modern clothing, wristwatch, smartphone, eyeglasses, cars, logos, text, captions, watermark, signature, anime, chibi, cartoon, plastic shiny skin, waxy CGI sheen, extra fingers, fused hands, malformed hands, duplicated trident, two light sources, contradictory shadow directions, flat frontal flash, blown-out HDR, oversaturated rainbow colors, cluttered composition, floating embers everywhere, lens-flare overload, mashed-up sacred symbols from different religions, disrespectful caricature

**Checklist:**
- Is there ONE clear divine/mythic subject and ONE clear primary light source?
- Is scale hierarchy readable - do you feel the difference between mortal and divine?
- Do all symbols and dress belong to a single coherent tradition (no accidental mashups)?
- Do materials look aged and real (bronze, gold, stone, cloth) rather than plastic?
- Are hands, faces and the key gesture anatomically clean and legible?
- Zero modern objects, text, or watermark in frame?
- Is the palette limited and intentional, not oversaturated?
- Does the camera angle serve reverence (low, symmetrical, or god's-eye)?
- For video: is motion slow and processional, with silence given room?
- Does the depiction respect the tradition rather than caricature it?

### Biology & Science Visualization

**Visual ingredients:** One unambiguous focal structure that everything else supports (a single nucleus, one mitochondrion, one virion); An explicit scale cue so the viewer knows if this is molecular, cellular, tissue, or organ level; Anatomically correct internal structure - real organelles, correct counts, correct handedness; Membrane translucency and subsurface scatter that make interiors look wet and gel-like; A color scheme that obeys the named modality (H&E, DAPI/GFP, SEM false-color); Depth separation via shallow focus or true focus-stacking, not flat uniform sharpness; A clean, non-competing background (pure black for fluorescence, neutral haze for renders); Fine surface or ultrastructure detail: cristae folds, ribosome studding, pores, spike proteins

**Motifs:** Folded inner-membrane cristae inside a bean-shaped mitochondrion; Nuclear envelope studded with pores wrapping a dense nucleolus; Ribosome-dotted rough endoplasmic reticulum sheets; A right-handed DNA double helix with visible major and minor grooves; Biconcave, anucleate red blood cells tumbling in plasma; A branching neuron: soma, dendrites, myelinated axon, synaptic terminals; Glowing fluorophore-labeled structures on a black confocal field; Pink-and-purple H&E tissue section with densely packed nuclei; SEM surface topology - pollen, cilia, or a ruffled cell membrane in raking grayscale

**Cliches to avoid:** Glowing blue 'sci-fi' haze with floating particles standing in for 'science'; DNA helix spinning in a digital void with binary code or circuit traces; Left-handed DNA (the helix must twist right-handed); Cells drawn as featureless translucent bubbles with no internal organelles; Neurons rendered as literal lightning bolts or electric sparks; HUD overlays, hexagon tech grids, and targeting reticles pasted over the biology; Rainbow oversaturation instead of a modality-correct palette; Mammalian red blood cells drawn flat and nucleated (they are biconcave and have no nucleus); Garbled fake 'scientific labels' - AI text that reads as gibberish

**Camera:** This genre lives at macro and extreme-macro scale, so lens and focus do the heavy lifting. For stills, specify a "100mm macro look" or "focus-stacked deep field" - the first isolates one cell with shallow depth of field, the second keeps a whole organelle field sharp like a real focus-stacked micrograph. For a single hero structure (one mitochondrion, one virus, a protein), orbit or lock a 3/4 angle so the viewer reads its 3D form. For diagrams and cutaways, ask for an "orthographic cross-section" - no perspective distortion, clean labelable planes. For video, the signature move is a slow, continuous macro dolly-in or fly-through: the camera creeps forward through translucent cytoplasm while structures drift past in soft focus. Molecules want a slow orbit; tissue fields want a lateral tracking glide with a rack focus that hands off from one cell layer to the next. Shot sizes: extreme close-up is the default; a wider establishing "field of cells" only to set scale before pushing in. AVOID wide-angle lenses (they bend membranes unnaturally), handheld shake, fast whip pans, and hard cuts - science reads as calm, deliberate, optically clean. State frame rate and a short duration (3-6s) so a video model commits to one uninterrupted move instead of inventing shot changes.

**Lighting & colour:** Lighting must match the imaging modality you name, or it reads as fantasy. 3D scientific render: soft volumetric key from upper-front, strong subsurface scattering through gel-like translucent cytoplasm, and a thin rim/back light to separate each glossy wet membrane - biological interiors are jelly, not plastic. Confocal / immunofluorescence: structures are self-emissive glowing labels on pure black, restrained to 2-3 channels (DAPI blue nuclei, GFP green, a red membrane or actin marker) - never a full rainbow. SEM: grayscale surface topology raked by one hard directional light to exaggerate texture, optionally false-colored in a single hue family (teal, amber, or violet) rather than random colors. TEM: flat grayscale thin-section, low contrast, membrane bilayers as fine dark lines. Histology / brightfield: H&E convention - eosin-pink cytoplasm and extracellular matrix, hematoxylin purple-blue nuclei, even diffuse illumination. Golden rule: pick a palette of two or three colors that mean something, and hold it. Oversaturated rainbow gradients and "glowing blue everything" are the fastest tells of AI science slop.

**Sound & voice:** Keep it quiet and clinical, not cinematic-trailer. Sound bed: a low sub-bass hum or soft tonal drone suggesting the micro-world, plus subtle wet, viscous organic movement as structures drift - muted, close-miked, no reverb tail. No orchestral swell, no percussion hits, no rising riser into a "reveal." For narration, use a calm, measured, unhurried documentary-science register: precise terminology delivered plainly ("the inner membrane folds into cristae, where the cell generates energy"), medium-slow pace, no hype adjectives, no dramatic pauses for effect. A neutral, warm voice works better than a booming one. Often the strongest choice is no voiceover at all - pure ambient tone lets the imagery read as a meditative journey through the body. If you do narrate, one or two short sentences per clip; let the visuals breathe between them. Avoid whooshes on camera moves and stingers on cuts - this genre is observed, not sold.

**Steering vocabulary:** scanning electron micrograph (SEM), false-colored SEM, transmission electron micrograph (TEM), confocal fluorescence microscopy, immunofluorescence, DAPI-stained, H&E stained histology section, brightfield / phase-contrast, cryo-EM structure, photorealistic scientific 3D render, subsurface scattering, focus-stacked macro, cristae, phospholipid bilayer, ultrastructure, ribbon diagram, cross-section / cutaway, translucent cytoplasm

**Example idea:** make a picture of an animal cell showing the organelles inside

- Bad: a cell, science, super detailed, 4k, beautiful, glowing blue, futuristic biology, high quality, digital art, microscopic world, amazing

- Improved: Cross-section of a eukaryotic animal cell, clean medical-illustration style, clearly showing the nucleus with nucleolus, several mitochondria, rough endoplasmic reticulum, and the Golgi apparatus, accurate organelle shapes, soft neutral gradient background, even lighting, high detail, no text.

- Elite (video):

```
Slow, continuous macro dolly-in through the translucent interior of a single eukaryotic animal cell, rendered as a photorealistic scientific 3D animation in the style of high-end medical visualization. SUBJECT HIERARCHY: the camera drifts past one large ovoid nucleus in the foreground-left, wrapped in a double nuclear envelope pierced by visible nuclear pores, its dense nucleolus glowing faintly within; mid-ground holds three bean-shaped mitochondria with clearly folded inner cristae; the deep background is a soft, out-of-focus haze of ribosome-studded rough endoplasmic reticulum. MOTION: only the camera moves, a slow steady forward creep; organelles drift gently as if suspended in viscous cytosol - no morphing, no shape changes, membranes hold their form. LIGHTING: soft volumetric key from the upper right, strong subsurface scattering through the gel-like translucent cytoplasm, a thin rim light separating each glossy wet membrane, restrained teal-and-amber color grade. LENS: 100mm macro look, shallow depth of field; focus holds on the nuclear pores, then a gentle rack focus hands off to the mitochondria as they pass. SCALE: cellular, everything soft-edged and wet. Dark neutral background, no text, no labels. 5 seconds, 24fps, smooth and uninterrupted.
```

**Negative prompt:** text, labels, letters, numbers, gibberish writing, watermark, signature, left-handed DNA helix, sci-fi HUD, targeting reticle, circuit board pattern, binary code, glowing blue neon network, floating hexagons, lens flare, bokeh orbs, cartoon outlines, thick black outlines, featureless bubble cells, nucleated red blood cells, flat disc blood cells, extra or duplicated organelles, morphing shapes, warping membranes, unnatural perfect symmetry, oversaturated rainbow colors, dust particles, plastic surfaces, low detail, blurry mush

**Checklist:**
- Is there exactly one clear focal structure, with everything else demoted to support?
- Is the scale stated (molecular / cellular / tissue / organ) so nothing is ambiguous?
- Is an imaging modality named (SEM, TEM, confocal, H&E, or 3D render) and does the lighting/color obey it?
- Is the palette restrained to 2-3 meaningful colors - no rainbow, no default sci-fi blue?
- Are the structures anatomically correct: organelle types and counts, DNA right-handedness, RBCs anucleate?
- Did you exclude garbled text and labels unless a clean labeled diagram is actually the goal?
- Is the background clean and non-competing (pure black for fluorescence, neutral for renders)?
- (video) One continuous camera move, and do structures hold their shape instead of morphing?

### Mathematical & Abstract Visualization

**Visual ingredients:** one dominant form with an unmistakable density/value hierarchy; line-weight variation from hair-thin to bold within the same form; deliberate negative space / void around the subject; a single perceptual color gradient bound to a real variable; self-similar detail that survives a zoom; crisp anti-aliased edges - never blur to fake quality; a consistent, stated projection (orthographic or fixed isometric); one restrained accent hue at the extreme of the gradient; sub-pixel micro-texture / grain visible at 100%

**Motifs:** strange-attractor filament clouds (Lorenz / Clifford / de Jong); reaction-diffusion coral and labyrinth patterns; Voronoi / Delaunay shatter fields; flow-field contour rivers; phyllotactic spirals - sunflower packing at the 137.5 golden angle; recursive fractal branching and L-systems; wireframe torus knots and hypercube (tesseract) projections; Truchet tiles and Penrose non-periodic tilings; topographic isoline / marching-squares contour; nodal network graphs and force-directed layouts; moire interference and Op-Art line fields; gyroid and other minimal surfaces

**Cliches to avoid:** the glowing blue 'AI brain' network sphere; default-gradient rainbow Mandelbrot; gold-on-black sacred-geometry mandala with a lens flare; green 'digital rain' code; hexagon tech-HUD background; neon synthwave wireframe grid horizon standing in for real math; floating fake equations and random numbers overlaid for 'smartness'; depth-of-field blur faking depth on a flat 2D pattern; the spinning low-poly crystal; particles that spell out a word

**Camera:** Treat the camera as a precision instrument, not a cinematographer. Default to orthographic (parallel) projection for anything meant to read as "true" geometry - tilings, plots, fields, blueprints - because it keeps parallel lines parallel and strips out the "photo" feeling; reach for a mild long-lens perspective (85-135mm equivalent, compressed depth) only for 3D forms you want to feel volumetric, and even then flatten depth so the pattern stays legible. Shot sizes by subject: centered plan / top-down for fields (Voronoi, flow-field, reaction-diffusion, tessellation); a three-quarter hero framing for 3D objects (torus knots, polyhedra, minimal surfaces); extreme macro for fractal recursion. Movement (video), pick ONE and keep it machine-steady: (1) continuous constant-rate scale zoom - the signature fractal "infinite dive"; (2) slow linear orbit around a 3D form, 15-30 arc, no eased wobble; (3) parameter sweep - camera locked, the FORM morphs while the frame holds still; (4) slow push-in that reveals self-similar detail. No handheld, no whip pans, no dramatic dolly - the math should feel inevitable, not staged. Composition is governed by symmetry axis and center-of-mass, not rule-of-thirds: put the density peak exactly where you want the eye to land.

**Lighting & colour:** Prefer emissive, self-lit forms on a void: the structure is its own light source, with faint additive bloom where lines bundle - no external key, no cast shadow needed. When lighting real 3D geometry, use a single low rim/edge light against black to read silhouette, matte non-metal material, no plasticky specular. Color is data, not decoration: bind hue/value to a measured quantity (density, curvature, iteration count, height, velocity) on ONE perceptual gradient - viridis, magma, inferno, or a hand-picked duotone - so equal visual steps mean equal data steps. Restraint wins: one gradient plus at most one accent hue. Strong alternates: duotone ink-on-paper, single-ink risograph, or CMYK misregistration for a printed-plotter feel. Ban the full rainbow, the rotating HSV wheel, and neon-on-black saturation soup.

**Sound & voice:** No voiceover and no lyric music - the genre reads as a running system, not a narrated story. Score the motion, don't decorate it: map audio directly to the visible parameter. Sustained sine or triangle drones whose pitch tracks the sweep variable; granular or FM clicks on discrete events (a cell dividing, a node connecting, an iteration landing); filtered pink noise swelling with particle density; a subtle sub-bass tied to scale/zoom. Data-sonification aesthetic - a single evolving timbre, generous silence, no drop, no cinematic braaam, no stock "corporate tech" pad. If narration is truly required, use a calm, unhurried explainer register (an even 3Blue1Brown cadence), one idea per breath, zero hype, no rhetorical questions.

**Steering vocabulary:** orthographic projection, parametric, generative / algorithmic, flow field, strange attractor, reaction-diffusion, Voronoi cells, recursive subdivision, isolines / contour, wireframe, signed distance field, density-mapped, perceptual gradient (viridis / magma), phyllotaxis / golden angle, domain warping, hair-fine filament, pen-plotter, emissive, tessellation, moire interference

**Example idea:** i want a smart-looking techy math art thing, like a glowing shape made of lines, for a desktop wallpaper

- Bad: fractal, sacred geometry, mathematics, abstract background, colorful, glowing, symmetrical, 4k, ultra detailed, intricate, trending on artstation, digital art, beautiful, masterpiece

- Improved: A glowing generative structure on a dark background, fine intricate lines forming a symmetrical mathematical pattern, blue and purple color scheme, centered composition, soft glow, high detail, minimal and clean, abstract algorithmic art, orthographic view, no text.

- Elite (image):

```
A single strange-attractor filament structure centered in a deep black void - the traced path of a chaotic system rendered as roughly forty thousand hair-fine luminous threads that swirl into two nested focal lobes and never intersect. Density is the entire image: the two lobe centers are the brightest and most tightly woven, threads fanning outward into sparse, fading wisps toward the frame edges. Orthographic projection, no perspective distortion; the subject occupies the central ~70% of a square frame with clean, deliberate negative space around it. Color is bound to local thread density on ONE perceptual gradient - near-black indigo in the sparse outer field, rising through deep violet and teal to near-white cyan at the hottest cores - with a single restrained magenta rim on the highest-density crest only. Emissive material: the threads are their own light source, faint additive bloom where they bundle, no external key light, no cast shadow. Micro-detail: individual sub-pixel strands with anti-aliased falloff, a fine-grained pen-plotter texture at 100% zoom. Flat matte void background - no grid, no gradient sky, no floor. Mood: precise, cold, quiet, hypnotic. High-resolution generative plot, 1:1, crisp edges. No text, no numbers, no axes, no watermark.
```

**Negative prompt:** text, numbers, equations, labels, axis ticks, gridlines behind subject, watermark, signature; muddy rainbow gradient, HSV-wheel spin, oversaturated neon-on-black soup; blurry, soft, low-detail, jpeg artifacts; lens flare, bokeh, photographic depth-of-field on a flat pattern; cluttered or busy background; two competing focal subjects; broken or warped symmetry where symmetry is intended; melted globby forms, plasticky glossy 3D render, chrome specular; crossing threads in a non-crossing attractor; skewed perspective on an orthographic plot; the glowing "AI brain" network sphere; motivational-poster gloss

**Checklist:**
- One subject, one clear focal hierarchy - can you point to exactly where the eye lands?
- Zero contradictions: no 'orthographic' + 'shallow depth of field', no 'perfectly symmetrical' + 'organic chaos' unless intended
- Color gradient is perceptual and bound to a variable, not a decorative rainbow
- Line-weight and density actually vary - flat uniform texture is dead on arrival
- Negative space is deliberate; subject occupies a controlled % of the frame
- Projection is stated and held consistent across the whole image
- No text / axes / watermark unless the brief is explicitly a schematic
- Video only: name the ONE parameter being swept and the loop behavior (seamless vs one-shot)
- Edges are crisp, not blurred; grain and micro-detail survive at 100% zoom

### Fine Art / Museum Style

**Visual ingredients:** A named physical medium + support (oil on linen, egg tempera on poplar, watercolor on cold-press paper); Explicit technique / brushwork (glazing, impasto, sfumato, drybrush); A single, stated light logic with consistent shadows; A limited palette and clear value structure; One art-historical anchor by movement or technique; A composition armature (thirds, diagonal, triangle, repoussoir); Surface artifacts that prove it's an object (craquelure, canvas weave, varnish warmth, impasto ridges); A clearly hierarchied single subject with subordinate supporting elements; A frame contract: bare artwork or hung-in-gallery; Period-accurate costume, props, and setting

**Motifs:** Impasto ridges catching a raking light with tiny cast shadows; A fine web of craquelure across warm aged varnish; Visible canvas weave and directional brushstrokes; Chiaroscuro modeling on a cheek fading into shadow; Rich fabric drapery folds rendered in glaze and highlight; Vanitas still-life objects - pewter, ripe fruit, a skull, a guttering candle; Byzantine/Gothic gold-leaf ground with tooled halo; Watercolor pigment granulation and a deckled paper edge; A palette-knife scrape leaving a broken color edge; North-window light falling across a plaster interior

**Cliches to avoid:** Slop stack: 'masterpiece, highly detailed, trending on artstation, 4k, octane render'; Cloning a living or copyrighted artist's exact signature style instead of citing a technique; Generic fantasy drift: 'ethereal glowing goddess, magical, dreamlike'; A period figure wearing a modern Instagram-airbrushed face; A flat brown 'old painting' Photoshop filter standing in for real glazing and value structure; Ornate gold frame + dramatic museum spotlight when the user actually wanted the artwork itself; Dead-centered, perfectly symmetrical composition with no armature; Fake scribbled 'artist signature' in the corner

**Camera:** Treat "camera" as vantage + crop of a made object, not a photo. First decide the frame contract: bare artwork (image fills the frame edge-to-edge, no wall, no frame) OR museum-installation shot (the piece hung, gallery context, raking spot). Pick one and say it - mixing them muddies both. Shot sizes translate to painting scale: intimate bust/head study; half-length (waist up, the portrait workhorse); three-quarter length; full figure; expansive landscape vista with foreground-midground-distance staging. Perspective is your "lens": single-point linear perspective for depth, atmospheric/aerial perspective (cooler, paler, lower-contrast toward the horizon) for landscape, or a deliberately flattened picture plane for Gothic/early-Renaissance/modernist looks. Angle carries meaning: eye-level for dignity and directness, low-angle / di sotto in su for grandeur and ceiling foreshortening, slight high-angle for still-life tabletops. Build on a stated compositional armature - rule of thirds, golden spiral, a rising or recessional diagonal, a stable triangle for grouped figures, repoussoir (a darker framing object in the near corner that pushes the eye inward). For video: slow lateral dolly across the surface, a raking-light sweep that reveals impasto ridge shadows, a gentle push-in on the focal passage, or a rack focus from craquelure detail to full composition - always unhurried, tripod-smooth, never handheld.

**Lighting & colour:** Light is one committed source with obeyed shadow logic - not ambient wash. Reach for named schemes: chiaroscuro (strong light-to-dark modeling), tenebrism (near-black field, a shaft picking out the subject), cool north-window daylight, warm golden-hour glaze, or the flat even light of a fresco. Build value first, color second: establish a clear light-mid-shadow hierarchy so the image reads in grayscale before any hue matters. Prefer a disciplined limited palette named by pigment - lead/titanium white, yellow ochre, raw and burnt umber, terre verte, ultramarine, vermilion - with a single saturated accent doing the work instead of everything shouting. Underpainting method shapes the mood: warm imprimatura ground glowing through thin shadows, grisaille or verdaccio underpainting for cool flesh, dead-color blocking. Note surface optics: aged varnish warms and unifies; glazes deepen darks with luminous transparency; scumbled opaque lights sit chalky over dark. Kill HDR flatness, uniform saturation, and rim-light everywhere - those read as 3D render, not paint.

**Sound & voice:** For a museum/documentary treatment: quiet room tone, soft footsteps on parquet or stone, a distant hush of other visitors, and a sparse bed - a single sustained cello note, felt-piano, or restrained strings. Voiceover is a measured curator/art-historian register: unhurried, low and warm, precise, leaving silences; describe technique, period, and looking - never trailer-hype or breathless narration, no bombastic swells. For a living-painting / animated-portrait treatment: drop the narration and use diegetic ambience of the depicted scene - a crackling hearth, wind at the casement, distant birds, the faint creak of the room - kept low so the stillness carries.

**Steering vocabulary:** chiaroscuro, tenebrism, sfumato, impasto, glazing, scumbling, alla prima, grisaille, imprimatura, verdaccio, craquelure, raking light, repoussoir, oil on linen, egg tempera on panel, fresco, gouache, drybrush, palette-knife texture, canvas weave, aged varnish, limited earth palette, painterly, medium-accurate, gold-leaf ground

**Example idea:** a painting of a woman standing by a window with nice light

- Bad: beautiful masterpiece painting of a gorgeous woman by a window, highly detailed, intricate, stunning, 4k, 8k, octane render, unreal engine, trending on artstation, award winning, cinematic, hyperrealistic, volumetric lighting

- Improved: Oil painting of a young woman standing beside a window in soft daylight, Dutch Golden Age style, warm dim interior, detailed fabric folds, chiaroscuro lighting, muted earthy palette, portrait orientation, visible brushwork.

- Elite (image):

```
Oil-on-linen half-length portrait of a single solitary young woman standing at a leaded-glass casement window, turned three-quarters toward cool north light, gaze lowered. One light source only: soft daylight rakes from the left across her cheekbone and the folds of a slate-blue wool shawl, then falls to deep warm-umber shadow on the shaded side - restrained chiaroscuro in the manner of Dutch Golden Age interior painting. Technique: thin transparent glazes over a warm ochre imprimatura in the shadows, low impasto catching the light on linen highlights, the window lead, and the knuckles; soft sfumato transitions at the jaw and hairline; confident directional brushwork visible up close. Limited earth palette - lead white, yellow ochre, raw umber, a single restrained vermilion accent at the lips. Background: a dim plastered interior receding into shadow, a dull pewter jug on a bare oak table set as repoussoir in the lower-right corner. Composition on a rising diagonal, the figure on the left third, quiet negative space of the wall balancing the right. Surface reads as a real object: fine canvas weave, faint craquelure, the warm cast of aged varnish. Even, glare-free museum documentation lighting; bare artwork filling the frame, no wall or frame shown. Portrait orientation, 4:5.
```

**Negative prompt:** 3D render, octane, unreal engine, CGI, photorealistic photograph, HDR, airbrushed plastic skin, digital-smooth gradients, glossy sheen, oversaturated neon, modern clothing, wristwatch, smartphone, cartoon, anime, vector, clip-art, trending-on-artstation logo, watermark, printed signature text, gallery wall and ornate gold frame (when bare artwork is wanted), contradictory multiple light sources, lens flare, bokeh, motion blur, extra fingers, warped hands, asymmetrical melted eyes, muddy grey mush, over-centered dead-symmetrical layout

**Checklist:**
- Names one physical medium + support (oil on linen, egg tempera on poplar panel, watercolor on cold-press) with zero render/CGI contradictions
- Exactly one dominant light source; every shadow obeys it
- Palette stated as real pigments or one tight color family, not 'colorful' or 'vibrant'
- One art-historical anchor by technique or movement - never 'in the exact style of [living/copyrighted artist]'
- Composition armature named (thirds, rising diagonal, triangle, repoussoir)
- Surface artifacts present: brushwork/impasto/craquelure/canvas weave/varnish, so it reads as an object not a filter
- All slop tokens stripped (masterpiece, 4k, 8k, octane, trending on artstation, award winning)
- Anatomy sanity pass: hands, eye symmetry, plausible drapery
- Frame contract chosen and stated: bare artwork OR hung-in-gallery - not both
- Aspect ratio fits the subject (4:5 portrait, 3:2 landscape, 1:1 icon/tondo)

### Drama - the emotional human scene (realism, interiority, restraint)

**Visual ingredients:** a legibly lit human face with the shadow side left intact; eyes and gaze direction - looking down, away, or off-frame rather than at camera; hands as a second face - clenched, still, reaching, withholding; physical distance and blocking between two people to encode emotional distance; a single motivated light source (window, lamp, screen); negative space around the figure to carry loneliness; a specific lived-in environment with props that imply backstory; real skin texture - pores, age, tiredness, imperfection; one nameable emotional beat per frame (a decision, a realization, a withholding)

**Motifs:** a figure alone at a kitchen table, cold coffee, first light; two people in one frame not looking at each other; a face reflected in a dark window against city lights; a hand resting on a doorframe, hesitating to enter or leave; an unmade bed with only one side slept in; rain on the glass seen from inside a warm room (not a person crying in rain); a phone held but unused, screen gone dark; the back of a head refusing the camera; an empty chair still holding the shape of who sat there

**Cliches to avoid:** a single slow-motion tear rolling down a flawless cheek; sobbing alone in the pouring rain; sliding down a wall or shower tiles in anguish; sunset-silhouette embrace on a beach or hilltop; theatrical wailing and clutched chest; a grieving character in perfect glamour makeup and beauty lighting; orange-and-teal 'cinematic' grade standing in for mood; dutch angles and thriller shadows on a quiet scene; voiceover that states exactly how the character feels; a swelling orchestral score cueing the audience to cry

**Camera:** Drama lives at the face and the hands. Workhorse shots: the medium close-up (chest-up, reads the eyes while keeping context) and the close-up (isolates a single beat). Reserve the extreme close-up - an eye, a mouth, a clenched hand - for one decisive micro-moment, never the whole scene. Use the two-shot to stage relationship: physical distance in frame equals emotional distance, and a two-shot where one person is turned away carries more than any line. Wide shots are for isolation only - a small figure in a large empty room reads as loneliness. Lenses: go long (50mm, 85mm) for intimacy and compression with shallow depth of field to lift the subject off a soft background; never put a wide-angle on a face (the distortion breaks empathy). 35mm is the observational, fly-on-the-wall register. Angles: eye-level is the default because it treats the subject as an equal; break to a slight high angle only to diminish/expose a character, a slight low angle only to let them loom - and skip the dutch tilt entirely, it reads thriller, not drama. Movement (video): three moves only - the locked-off static hold that lets the performance carry, the slow push-in that creeps toward the face as a realization lands (the single most reliable emotional intensifier), and a barely-there handheld drift for documentary immediacy. No crane, no gimbal glide, no whip - a camera that announces itself kills interiority. For technique study by name: Bergman treated the face as landscape and the turned-away two-shot as a weapon; the Dardenne brothers' handheld proximity; Ozu's tatami-height static observation; Deakins' motivated naturalistic light.

**Lighting & colour:** Motivated and naturalistic, always - the light must come from something you could point to: a window, a lamp, a doorway, a TV, a phone screen, a candle. Keep sources soft and directional so they wrap the face and let the shadow side breathe; the shadowed half of a face is where interiority lives. Go low-key/chiaroscuro when the scene is heavy (shadow concealing part of the face), soft and near-even when the beat is tenderness or exposure. Put a practical in frame to anchor realism - a warm lamp against cool ambient daylight is the classic melancholy contrast. Color is restraint made visible: muted, desaturated, earthy neutrals, and skin tones kept true rather than pushed orange. Overcast flat light for grief and numbness; low warm window light for memory and warmth; the cool blue of a dark room lit only by a screen for isolation. Never glamour-light a dramatic face - soft-box symmetry and a clean beauty catchlight erase the very texture that sells the emotion.

**Sound & voice:** Sound design carries drama more than score. Lead with room tone and diegetic detail - a clock, a kettle, a refrigerator hum, a distant TV, traffic through glass, a chair scraping - and treat silence as a usable beat. Music, if any, arrives late and sparse: a single sustained piano note or a low cello, or nothing at all. Never lay wall-to-wall score that instructs the audience how to feel. Voice and dialogue are naturalistic and under-projected: half-finished sentences, overlaps, low volume, subtext over statement - characters say less than they mean. Delivery is spoken, not performed; keep the breath, the pause, the swallow before a hard line. If you use voiceover, make it oblique - it must not narrate the emotion the picture is already showing.

**Steering vocabulary:** restrained, quiet, unguarded, lived-in, naturalistic, candid, intimate, understated, motivated light, shallow depth of field, micro-expression, held gaze, still, documentary realism, unretouched, muted palette, observational, off-frame eyeline, negative space, single continuous take

**Example idea:** a woman getting bad news on the phone

- Bad: a beautiful woman crying on the phone because she got very sad news, tears streaming down her face, extremely emotional and heartbreaking, cinematic dramatic lighting, 4k ultra hd, masterpiece, trending on artstation, highly detailed, award winning photography

- Improved: Medium close-up of a woman in her late thirties standing at a kitchen counter, a phone held to her ear as she listens. Still expression, unfocused eyes, lips slightly parted - she is absorbing the news, not crying. Soft daylight from a window camera-left, muted natural color, true skin tones. 50mm, shallow depth of field, eye-level. Lived-in kitchen softened behind her. Quiet, restrained, realistic.

- Elite (video):

```
Single continuous 8-second take. Locked-off frame that pushes in slowly, roughly ten percent over the shot. Medium close-up, eye-level, 85mm-equivalent, shallow depth of field so the kitchen behind her dissolves into muted shapes. Subject: one woman, around forty, tired unretouched skin with visible pores and faint under-eye shadows, dark hair loosely tied, a worn grey cardigan. She stands at a kitchen counter in early morning, a phone held loosely to her ear, her free hand flat on the counter taking her weight. She is listening, not speaking. Across the shot her face barely moves - a slow blink, the jaw tightening, her gaze drifting down and off-frame to the left as the words land. She does not cry. Her breathing shortens once, a single caught breath. Lighting: one motivated source, cool overcast daylight through a window camera-left, soft and directional, wrapping the near side of her face and leaving the far side in gentle shadow; a dim practical lamp glows warm in the deep background. Color: desaturated, neutral true skin tones, no orange-and-teal grade, muted greens and greys in the room. Composition: she sits slightly off-center with negative space to her right; an out-of-focus mug and a scatter of unopened mail rest on the counter. Sound: room tone, refrigerator hum, a wall clock, faint traffic through glass; no music; the only swell is that one caught breath. Naturalistic documentary realism, restrained and quiet. No melodrama, no falling tears, no camera shake, no cuts, no text or captions.
```

**Negative prompt:** melodrama, theatrical overacting, exaggerated grief, single glycerin tear, tears streaming, crying in the rain, wailing, hands sliding down a wall, sunset silhouette embrace, glamour retouching, flawless plastic skin, heavy makeup on a grieving face, orange-and-teal grade, oversaturated, HDR halos, symmetrical studio beauty lighting, ring-light catchlights, dutch tilt, lens flare spam, blank stare into camera, on-the-nose expression, extra fingers, deformed hands, mismatched eyes, warped face, text, watermark, subtitles, fast cuts, whip pans, showy camera moves, motion-blur smear

**Checklist:**
- Can you name the single emotional beat of the frame in three words? If not, the shot is unfocused.
- Is the emotion shown through behavior - gaze, hands, breath, stillness - rather than stated as an adjective?
- One motivated light source, shadow side left intact - not glamour-lit or ring-lit?
- Skin reads real (pores, age, fatigue) with no plastic retouching?
- Palette muted and skin-tones true - no orange-and-teal, no oversaturation?
- Camera at eye-level unless a high/low angle is a deliberate power or vulnerability choice?
- Restraint pass: did you cut the tear, the rain, and the swelling score?
- Blocking and negative space chosen to carry the subtext (distance = distance)?
- For video: one continuous take, movement limited to a locked-off hold or a slow push-in?
- Contradiction scan: no conflicting cues (smiling + grief, bright sun + somber, at-camera stare + interiority)?

### Product Ad / Luxury Commercial

**Visual ingredients:** a single clearly-defined hero product as the sole subject; a deliberate premium surface (honed marble, brushed metal, raw stone slab, silk, still water); a seamless gradient or sweep backdrop; controlled reflections that describe the product's form; a clean catchlight and shaped specular highlights; generous negative space reserved for headline/logo; one brand-color accent against a restrained neutral palette; atmosphere for depth (low mist, fine haze, drifting particulate); supporting texture cues (condensation, water droplets, powder, a single ingredient garnish); a grounded, direction-matched contact shadow or reflection

**Motifs:** a liquid pour or splash frozen mid-air by high-speed capture; condensation beads sweating on cold glass or metal; a levitating / suspended product against a clean sweep; caustics and light streaks refracting through glass and liquid; a powder or pigment burst blooming around a cosmetic; a deconstructed / exploded ingredient layout around the product; gold or chrome specular glints tracking a slow camera move; silk or fabric rippling in slow motion behind the hero; a rack-focus reveal from a foreground element to the product; a turntable orbit describing every facet of the form

**Cliches to avoid:** a product floating on pure white with no lighting craft or shadow; lens flares and light streaks slapped over everything; over-crunched HDR with glowing halos and no true blacks; a splash that reads muddy, grey, or obviously CGI; fake circular bokeh balls filling the background; plastic waxy CGI sheen instead of real material response; 'LUXURY' / 'PREMIUM' text baked into the image; gilding every surface gold until it looks gaudy, not rich; cluttered prop salad that buries the hero; a drop shadow whose direction contradicts the key light; unreadable warped brand text or a melted logo; an oval, tilted, or asymmetric bottle mouth / rim

**Camera:** The product is the actor - frame it like a portrait, not a snapshot. SHOT SIZES: (1) Hero packshot - product fills 55-70% of frame, grounded on a surface with negative space above/beside for copy; (2) Extreme macro (ECU) on the one detail that sells craft - a bevel edge, stitching, a mechanical seam, a droplet; (3) Three-quarter hero showing two faces of the object so form reads dimensional; (4) In-context lifestyle medium shot for the "who uses this" beat. LENSES: 100mm macro is the commercial default - flattering compression, no edge distortion, true proportions; 90-135mm for tight compression on small objects; a macro-probe/periscope lens for gliding THROUGH or across a product in video; 24mm tilt-shift for flat-lays where you want a ruler-flat plane of focus; 50mm for lifestyle context. ANGLES: eye-level three-quarter is the safe hero; a slight low angle (product above the lens) makes it monumental; dead-on straight packshot for catalog/e-com; 90-degree top-down for flat lays. APERTURE: f/8-f/11 to keep the whole product tack-sharp - product ads live and die on sharpness; f/2.8 only for lifestyle bokeh, never for the hero object. MOVEMENT (video): locked-off tripod for the pure hero; a slow 3-5% push-in for gravitas; a 180-360 degree turntable orbit to reveal form; a top-down vertical descent onto a flat lay; a rack-focus reveal from a foreground element to the product; a macro-probe glide across the surface; high-speed slow-motion (120-1000 fps look) for any pour, splash, spray, or powder burst - motion only impresses when slowed. One deliberate, weighted camera move per shot; never combine moves.

**Lighting & colour:** Light the material, not the object. Glass and liquid want dark-field lighting - a bright rim/back light plus black flags so the SHAPE is drawn by dark edges and controlled speculars, not flat frontal fill. Chrome and polished metal are mirrors: you are lighting the REFLECTION, so use large soft sources and gradient panels; whatever the surface reflects IS its form. Matte/soft goods (leather, fabric, cosmetics cream) take a big soft key at 30-45 degrees plus a subtle rim for edge separation. Core tools: large softbox or gradient sweep as key, strip/rim light for separation, negative fill (black flags) to add contrast and shape on glossy surfaces, a gobo for shaped falloff, and a single deliberate catchlight. Palettes for premium read: restrained and near-monochrome with ONE brand-color accent; warm-metal (amber/gold/bronze) against cool neutral (graphite/charcoal/slate); high-key clean white for clinical/beauty/tech; low-key chiaroscuro for spirits/watch/fragrance gravitas. Keep saturation disciplined - luxury reads as controlled, not loud. Protect highlight detail; a clipped white blob looks cheap. Match every shadow and reflection to the key direction or the eye instantly reads "fake."

**Sound & voice:** For the video cut, restraint sells premium. Sound design is sparse and tactile: one hero foley beat tied to the product action - the machined click of a cap, the atomizer's soft spray hiss, a slow viscous liquid pour, a fabric swish, the snap of a watch clasp - recorded close and clean. Under it, a low sub-bass swell that blooms on the reveal, and a spare bed: a single sustained piano note, a warm ambient pad, or a minimal pulse. Leave silence around the hero moment; the pause is the luxury. Voiceover, if any: one short line, intimate and unhurried, low warm register, no hard-sell announcer energy, no rising infomercial cadence. End on the product and the brand name as the final spoken or on-screen beat - let it land in quiet, not over music. Avoid: busy stock music, comedic stingers, loud whooshes on every cut, and wall-to-wall narration.

**Steering vocabulary:** hero shot, packshot, studio seamless / gradient sweep, dark-field lighting, bright-field lighting, softbox key, strip / rim light, specular highlight, controlled reflection, negative fill / black flags, sub-surface scattering, caustics, catchlight, macro / macro-probe lens, tack sharp, high-speed capture / slow-motion, negative space for copy, commercial product photography, editorial still-life, brand-forward, premium / restrained palette, condensation beads, honed marble / brushed metal surface

**Example idea:** make an ad for my new perfume

- Bad: a beautiful perfume bottle, luxury, high quality, 4k, professional, amazing lighting, best quality, trending on instagram, cinematic, stunning, elegant ad, ultra detailed

- Improved: Luxury perfume bottle product shot. A rectangular faceted glass bottle filled with amber liquid, brushed-gold cap, standing on a black marble surface against a dark gradient background. Studio lighting from the side with a rim light behind the bottle to make the liquid glow, soft reflection on the marble beneath. Shot on a macro lens, sharp focus on the bottle, some empty space at the top for text. Premium, elegant, photorealistic commercial photography.

- Elite (image):

```
Commercial product hero shot, editorial luxury still-life. SUBJECT: one faceted rectangular glass perfume bottle, half-filled with warm amber liquid, brushed-gold cylindrical cap, standing dead-center in the lower-middle third of the frame. SCENE: the bottle rests on a honed black marble slab with fine grey veining; behind it, a seamless graphite-to-charcoal gradient sweep backdrop. LIGHTING: dark-field studio setup - a large softbox key from camera-left at 45 degrees sculpts a soft tonal gradient down the glass; a narrow strip rim light from behind-right defines the bottle's right edge and back-lights the amber liquid so it glows with sub-surface scattering; black flags on both sides keep the glass deep and glossy so the form reads through controlled specular highlights rather than flat fill; one crisp vertical catchlight runs down the front bevel. A faint cool mist drifts low across the marble. CAMERA: 100mm macro lens, f/9 for edge-to-edge sharpness, eye-level three-quarter angle; bottle tack-sharp, backdrop falling to a soft gradient. COMPOSITION: wide negative space in the upper third reserved for copy. PALETTE: warm amber and brushed gold against cool graphite and black, restrained and premium. Photoreal high-end commercial product photography, clean symmetric composition, undistorted glass, no text, no logo, no props.
```

**Negative prompt:** warped or asymmetric bottle, tilted or oval bottle opening, garbled text, misspelled brand name, distorted logo, extra caps or lids, duplicated product, floating product with no grounded shadow, shadow direction inconsistent with key light, plastic CGI sheen, waxy over-smooth surface, blown-out clipped highlights, muddy or warped reflections, fingerprints, dust specks, smudges, cluttered background, distracting props, oversaturated colors, HDR halos, chromatic aberration, lens flare, low-resolution, jpeg artifacts, wrong perspective, fisheye distortion, watermark

**Checklist:**
- Is there ONE unmistakable hero product, not competing subjects?
- Brand text/logo either absent or perfectly legible and correctly spelled (safest: render clean, add type in post)
- Bottle opening / rim / circular features symmetric, not ovalized or tilted
- Reflections and speculars consistent with a single key-light direction
- Contact shadow grounded and matching the light, not floating
- No duplicated product, extra caps, or spare parts in frame
- Highlights hold detail - no clipped white blobs
- Surface texture (marble/metal/fabric) reads real and premium, not plastic
- Deliberate negative space left for headline and logo lockup
- Palette restrained with at most one accent color; on-brand
- Product tack-sharp at the hero aperture (f/8-f/11 look)
- No fingerprints, dust, smudges, or stray particulate on the hero
- Perspective natural (100mm macro feel), no fisheye or edge distortion

### War & Strategy

**Visual ingredients:** one clear focal figure (commander or lone soldier) set against the mass; massed troops reading as dense shapes and rhythm, not a crowd of individuals; vertical repetition - spears, standards, banners - to build scale and cadence; layered atmospheric depth via fog/smoke/haze separating foreground, midground, background; period-consistent gear, weapons, armor and architecture with zero anachronism; weather and ground state (mud, frost, dust, churn) doing narrative work; a deliberate horizon line and sky mass that sets threat or aftermath; legible strategy props when relevant: maps, terrain models, signal flags, lantern-lit war table

**Motifs:** a forest of vertical pikes receding into haze; limp banners heavy with dew, or snapping once in cold wind; breath fogging in freezing air before contact; a single commander silhouetted on a ridgeline; a lantern-lit war-table map with hands moving markers; a column of troops as an unbroken tide crossing a valley; smoke and dust drifting across an otherwise still field; boot-churned mud and abandoned gear in the aftermath; carrion birds circling a distant, quiet field

**Cliches to avoid:** the slow-motion lone hero mowing down dozens; orange-teal blockbuster color grade; a rousing pre-battle speech over swelling strings; gratuitous blood splatter and dismemberment as spectacle; flaming arrows blackening the entire sky; the single banner falling in slow motion; 'epic / masterpiece / 4k' quality tags standing in for real direction; chaotic whip-pans hiding weak composition; clean, well-lit, video-game-lobby armies with no weather or wear; faux-Latin unreadable text stamped on every banner

**Camera:** Lead with the extreme-wide or aerial establishing shot to sell scale - armies must read as one mass, not a loose crowd. Rotate three registers: (1) god's-eye high angle / near-top-down for strategy legibility, so formations, terrain, and troop lines read as clean shapes; (2) eye-level or slightly low medium/close on the commander or a single soldier for gravity and dread; (3) long-lens telephoto (85-200mm) compression that stacks ranks into a dense wall and flattens a marching column into an unbroken tide. Movement: slow gimbal or dolly push-ins to build tension, locked-off static for weight and inevitability, restrained handheld ONLY at the moment of contact - never whip-pans. A slow crane-down from sky to a single face links scale to the individual. Deep focus for battlefield vistas so the whole field reads; shallow focus to isolate a commander against a soft army. Low horizon line to press a threatening massed sky down onto the frame; high horizon to show ground swallowed by troops.

**Lighting & colour:** Favor overcast, low-contrast natural light - dawn, dusk, or flat grey daylight - which reads as grave and keeps the eye on form and mass rather than spectacle. Palettes stay desaturated: steel-blue, ash-grey, mud-brown, bone. Introduce exactly one warm accent - sodium-orange horizon, a lantern, a burning structure, a signal fire - to anchor the eye and imply time of day. For war rooms and strategy beats: a single hard lantern or window key, deep shadows, chiaroscuro on faces bent over a map. Smoke and haze are structural, not decoration - they separate depth planes and unify color. Backlight fog and smoke for volumetric god-rays that sell scale. Let the sky carry the mood: heavy cloud for dread, light bleeding through for cost and aftermath. Avoid orange-teal blockbuster grade, cheerful saturation, and flat even lighting.

**Sound & voice:** Design for restraint and negative space - silence is the loudest tool in this genre. Bed: low wind, distant crow calls, creak of leather and armor, a single far-off horn or drum that establishes the army without showing it. Build tension with a slow near-subsonic drone and a lone drum pulse, not a bombastic orchestral swell. Let the moment of contact hit hard against the prior quiet - a muffled roar rather than a wall of clang. Voiceover, if any, is sparse, low, and weary: a commander's terse order or one grim line, never a rousing speech. Diegetic dialogue is clipped and functional ("Hold the line," "Signal the flank"). Avoid triumphant brass fanfares, modern whoosh sound-design, and wall-to-wall score - let the wind and the wait carry the gravity.

**Steering vocabulary:** massed ranks, forest of pikes, telephoto compression, low ground fog, ridgeline silhouette, god's-eye high angle, locked-off static, glacial push-in, atmospheric haze, desaturated steel-blue, banners heavy with dew, sodium-orange dawn accent, attritional weight, smoke-choked field, picket line, held breath before contact, mud-caked, volumetric backlit smoke

**Example idea:** make an epic medieval battle scene, huge army, looks cinematic

- Bad: epic medieval war, thousands of soldiers fighting, blood everywhere, dramatic, cinematic, 4k, ultra realistic, hyper detailed, masterpiece, best quality, trending on artstation

- Improved: Wide cinematic shot of a medieval battlefield at dawn, two armies of infantry with spears and banners massed on a muddy field under an overcast grey sky, low fog, desaturated color palette, shot on a long lens with shallow depth of field, tense and grave mood, subtle film grain

- Elite (video):

```
Cinematic wide shot, slow 8-second dolly push-in on a single weathered field commander standing at the crest of a frost-covered ridge at first light. She wears a mud-caked wool greatcoat, no helmet, breath fogging in the freezing air. Below and beyond her, a valley floor packed with roughly ten thousand massed pikemen stands motionless in low ground fog, their tall spears forming a dense vertical forest that recedes into atmospheric haze. Rows of dark banners hang limp, heavy with dew. Scene hierarchy - foreground: her gloved hand resting on a sheathed sword; midground: two signal officers waiting one step behind; background: the fog-drowned army and a pale grey dawn sky bleeding faint sodium-orange at the horizon. Camera: 85mm telephoto lens compressing the ranks so the army feels bottomless, medium-shallow depth of field, commander sharp, distant ranks softened by haze. Motion: near-still - only slowly drifting fog, banners stirring once, and the commander's fogged breath; the push-in is glacial, steady, gimbal-smooth. Lighting: cold overcast dawn, soft directional key from camera-left, desaturated steel-blue and ash palette with one warm sodium-orange accent at the horizon. Mood: grave, tense, the held breath before violence. Subtle 35mm film grain. No text, no on-screen graphics, no HUD.
```

**Negative prompt:** gratuitous gore, dismemberment, blood splatter as spectacle, anachronistic modern gear on period soldiers, duplicated or fused limbs, extra fingers, warped faces, bent or melting weapons, floating figures, disconnected shadows, fake unreadable banner text, faux-Latin gibberish, video-game HUD, health bars, on-screen graphics, motion-blur smear, oversaturated colors, orange-teal grade overkill, cluttered composition with no clear subject, lens-flare spam, cartoon proportions, plastic CGI sheen, low-detail mush in background ranks, cheerful even lighting, comic exaggeration, clean unweathered armor

**Checklist:**
- Is there ONE clear subject the eye lands on first, or does the mass swallow the frame?
- Does scale read through repetition plus atmospheric depth, not just a bigger crowd count?
- Is all gear, weaponry, and architecture consistent to a single era with no anachronism?
- One light logic and one color story - no contradictory light sources or clashing accents?
- Is the emotion gravity and tension rather than gore-spectacle? Cut gratuitous blood.
- Camera spec present and self-consistent: shot size + lens (mm) + angle + movement?
- For video: is motion mostly restrained (fog, banners, breath) with one deliberate camera move?
- Have 'epic / 4k / masterpiece' filler tags been replaced with concrete nouns?
- Does the sky, weather, and ground state actively carry the story?
- Does the negative prompt cover extra limbs, warped weapons, HUD, and fake banner text?

### Mystery / Noir

**Visual ingredients:** A single hard, named light source with everything else falling to black; Hard-edged cast shadows with a clear shape (blinds, railings, a long figure-shadow); Wet surfaces - rain-slick streets, puddles, dripping windows - that reflect and break up the light; A morally ambiguous lone figure, off-center, small against a large dark space; Atmosphere in the light beam: rain streaks, cigarette smoke, steam, haze with a visible source; A restrained palette (true B&W, or desaturated with one precise accent color); Cage-like framing devices: doorways, blinds, mesh, banisters between camera and subject; One motivated camera move or none at all - patient stillness

**Motifs:** Venetian-blind stripes raking across a face or wall; A lone streetlamp throwing an amber pool onto empty wet cobbles; A long shadow stretching down an empty corridor ahead of an unseen figure; Rain streaking through a hard cone of light; A cigarette ember and thin rising smoke in the dark; A single red neon sign reflected, smeared, in a rain puddle; A silhouette in a doorway backlit against a bright hallway; Headlights sweeping a hard moving shadow across a brick wall; Half a face lit, half swallowed by black - one catchlight in the shadowed eye

**Cliches to avoid:** Full fedora-and-trenchcoat cosplay played straight - it now reads as parody; imply the era through light and posture, not costume; The single perfect streetlight-puddle reflection as the ONLY idea in the frame; A literal neon sign that says NOIR / MYSTERY / CRIME; The femme fatale as inert set dressing rather than a person with intent; Rain rendered as flat white static or a screen-wide particle overlay with no source; Purple-and-teal cyberpunk palette masquerading as noir; Dutch tilt on every single shot until it's seasick instead of unsettling; Wall-to-wall smoky-sax score with no silence; Over-narration that explains the mood the image already shows

**Camera:** Noir is a lighting language before it is a camera language, but the frame carries the dread. Default to LOW-KEY, high-contrast setups and let composition do half the storytelling.

Shot sizes: favor the medium-wide "figure swallowed by the environment" (a lone person dwarfed by a dark street) and the tight interrogation close-up (face half in shadow, one eye catching light). Use the over-the-shoulder for suspicion, and the "empty" two-shot where one person sits in light while the other stays in the dark.

Lenses: 32-40mm for street work (natural, keeps the person embedded in the scene); 85mm for the close-up so the background falls to soft black; 24-28mm only for paranoia and distortion - a low wide angle up at a figure reads as menace. Deep focus (the Gregg Toland / Welles approach) is a signature move: foreground clue and background figure both sharp, so the eye has to choose.

Angles: low angle up = threat and dominance; high angle down = entrapment, the subject caged by the frame; Dutch tilt = moral vertigo, use once, not throughout. Eye-level for the honest beat, then break it.

Movement (video): stillness is the default - a locked-off, composed frame lets shadow build tension. Punctuate with ONE slow move: a creeping dolly-in on a face, a lateral track past foreground silhouettes (blinds, railings, pillars) that wipe the frame to black, or a slow crane descent from a rain-lit sign to the person below. Motivated camera only: it moves because a car passes, a door opens, a body turns. Avoid handheld jitter and fast whips - noir is patient.

Composition: place the subject off-center against a large field of black negative space; frame through doorways, blinds, banisters, or wire mesh so the world reads as a cage; let a hard shadow bar cut across the face or the wall behind.

**Lighting & colour:** The whole genre lives or dies on LOW-KEY, single-source, hard light. Craft rules:

1) One motivated key, then let the rest go dark. Name the source in the prompt - a bare bulb, a desk lamp, a streetlamp, a match, headlights, a window with venetian blinds. Hard light (small, undiffused) gives the sharp-edged shadows noir needs; soft light kills the look.

2) Crush the shadows. Explicitly say "no fill on the shadow side," "falls to near-black," "deep black background." Models default to filling shadows - you must fight it. High contrast, deep umbra.

3) Motivated shadow shapes are the signature: venetian-blind bars across a face or wall, window-frame crosses, railing stripes, a slow-swinging bulb, a figure's shadow thrown long down a corridor. Call the shape and where it lands.

4) Color: two valid routes. (a) True monochrome / silver-gelatin black-and-white for classic noir. (b) Restrained, heavily desaturated color - sodium-amber or cold tungsten highlights against blue-black shadow, plus one small saturated accent (a red neon sign, a lipstick, a taillight) that pops precisely because everything else is muted. Never a full rainbow. Neo-noir can add wet neon reflections, but keep the base desaturated.

5) Texture of light: light should look wet and atmospheric - rain streaks catching the beam, cigarette smoke drifting through it, steam off a grate, a hazy halo around the practical. Always give the haze a source; unmotivated fog reads fake.

**Sound & voice:** Sound design should feel sparse, wet, and lonely - negative space, not a wall of score.

Ambience (diegetic): steady rain, water dripping from a gutter, tires hissing on wet asphalt, a distant train or foghorn, the electrical hum and faint flicker-buzz of a failing streetlamp, a door creaking, footsteps echoing in an empty corridor. Let silence sit between sounds.

Music: restraint over saturation. A single mournful tenor saxophone, sparse upright-bass walk, or a few isolated piano notes with heavy reverb - entering low and late, never wall-to-wall. Absence of music during a tense beat is often stronger than adding it.

Voiceover / dialogue (if used): first-person, past tense, terse and world-weary. Low register, unhurried, a little defeated - a narrator who already knows how this ends and isn't impressed by it. Concrete, hard-boiled understatement; short declarative lines with a dry twist, no ornate speeches. Dialogue is clipped and loaded - people say less than they mean. Keep all VO original; do not quote existing films.

**Steering vocabulary:** chiaroscuro, low-key lighting, hard single-source key, deep shadow / crushed blacks, venetian-blind shadow bars, rain-slick, wet asphalt sheen, sodium-vapor lamp, cold tungsten, silhouette / rim light, deep focus, high contrast, desaturated, sodium-amber highlights, blue-black shadow, cigarette haze, long cast shadow, practical light source, silver-gelatin monochrome, Dutch tilt, off-center in negative space, morally ambiguous, cage framing

**Example idea:** a detective in the rain looking for a killer in the city at night

- Bad: a noir detective in the rain at night, cinematic, moody atmosphere, mysterious vibes, dramatic lighting, dark and gritty, highly detailed, 8k, ultra realistic, masterpiece, trending on artstation

- Improved: A lone detective in a dark rain-soaked overcoat and hat stands under a single streetlight on a wet cobblestone street at night, heavy rain falling. Hard amber light from the lamp overhead casts one long shadow across the stones; the rest of the street fades into darkness. Low-key lighting, high contrast, desaturated color, 40mm lens, eye-level, quiet and watchful.

- Elite (video):

```
Medium-wide shot, slow 12-second dolly-in on a single subject: a weary male detective, mid-40s, unshaven, standing alone under a failing sodium-vapor streetlamp on a rain-slick cobblestone street at 2 a.m. He wears a dark, rain-soaked wool overcoat with the collar turned up; water beads and runs off the brim of his hat. A lit cigarette is cupped low in one hand, a thin curl of smoke rising and bending in the wet air. He is not moving - he is waiting for someone who is late.

Camera: 40mm lens, starting eye-level and settling to a slight low angle as the dolly creeps in. Subject held left-of-center; the empty wet street recedes into deep black behind him. Deep focus so the far end of the street and the near cobbles both stay sharp. Aspect ratio 2.39:1, 24fps.

Lighting: single hard key from the overhead sodium lamp - warm amber, raking straight down, carving a long hard shadow across the cobbles. Everything beyond the pool of light crushes to near-black (low-key, chiaroscuro, high contrast). One faint cool rim from a distant shop window catches the edge of his right shoulder. No fill on the shadow side of his face - let half of it fall to black.

Motion: heavy rain falls in visible streaks through the cone of the lamp; puddles ripple and throw broken amber reflections; his breath fogs faintly. Midway through the push-in, an unseen car passes off-frame to the left - its headlights sweep one hard moving shadow across the wet brick wall behind him, then vanish, returning the street to darkness.

Palette: heavily desaturated - sodium-amber highlights against blue-black shadow, wet-asphalt sheen everywhere, no bright or saturated colors.

Filmic grain, subtle anamorphic character in the highlights. No text, no captions, no watermark, no on-screen titles.
```

**Negative prompt:** flat even lighting, bright daylight, overhead soft fill, clean dry streets, cheerful saturated colors, HDR glow, blown-out highlights, teal-orange over-grade, glossy plastic skin, smiling subject, cluttered busy background, modern glass skyscrapers, neon cyberpunk palette, lens flare spam, cartoon rain / white streak noise, fog-machine haze with no source, extra fingers, warped hands, distorted face, duplicated limbs, text, watermark, logo, captions, low contrast, muddy grey shadows, everything evenly lit

**Checklist:**
- Is there exactly ONE dominant, named light source - and does the prompt tell the model to let the rest fall to black?
- Are the shadows HARD-edged with a describable shape and a stated landing spot (face, wall, floor)?
- Is the palette restrained - true B&W or desaturated with at most one accent - with no rainbow creep?
- Is every bit of haze/rain/smoke given a visible source, so atmosphere doesn't read as a fake overlay?
- Is there ONE clear subject, off-center in negative space, with no contradictory second focal point?
- For video: is there at most one slow, motivated camera move (or deliberate stillness) - no jitter or whips?
- Are surfaces wet and reflective where light hits them (streets, glass, skin under rain)?
- Did you avoid keyword-stuffing (masterpiece/4k/trending) in favor of concrete nouns and one clear scene?
- Is the mood morally ambiguous and watchful - not action-poster dramatic?
- Is all text/voiceover original, with no quoted film or book lines?

### Comedy / Visual Humor

**Visual ingredients:** the reaction face - deadpan or wide-eyed - carrying the joke; one incongruous object treated with total seriousness; a straight-man anchor reacting normally for contrast; a frozen physical consequence: a spill, a fall, a topple caught mid-air; hyper-specific wardrobe and props (exact garment, exact size mismatch); an environment that stays indifferent to the chaos; a single clear focal subject with breathing room around it

**Motifs:** direct-to-lens deadpan stare; the frozen apex of a pratfall, an inch before impact; perfect symmetry with exactly one thing wrong; a tiny hat or comically oversized prop; an animal poised, straight-faced, in a human role; grand heroic staging on a trivial subject (bathos); the too-tight, too-big, ill-fitting costume

**Cliches to avoid:** generic 'funny meme face' with no actual gag; melted rubber-face grotesque distortion; clown makeup as a lazy shortcut for 'funny'; emoji-literal exaggerated expressions; baked-in caption text (models garble it and it does the joke's work for it); a laughing crowd inserted to tell the viewer it's funny; random 'wacky' noise with no single legible gag; stacking multiple competing gags that dilute the read; quirky-for-its-own-sake with no straight-man contrast

**Camera:** Hold the whole gag in ONE frame - comedy dies when the camera cuts cause away from effect. Default to a locked-off medium-wide "master" at eye level, dead-center and symmetrical (the Keaton / Wes Anderson deadpan tableau) so the joke reads flat to camera with nothing distracting. Keep the lens still through the beat; a drifting camera steals timing. Reserve motion for punctuation only: a fast push-in or snap-zoom onto a reaction is the classic "comedy zoom"; a whip-pan or rack-focus delivers a reveal (straight man in foreground, absurdity snapping into focus behind). Play angle against status for bathos - low angle to inflate something trivial into a hero, high angle to deflate a proud subject into a pathetic one. Lens: 35-50mm keeps the deadpan honest and undistorted; save wide-angle bulge for slapstick chaos only. For VIDEO, timing is the whole craft: overcrank (slow-mo) an undignified moment to milk it, undercrank (fast-mo) slapstick for cartoon energy, and above all HOLD the frame one extra beat before the reaction lands - the pause is the punchline.

**Lighting & colour:** Default to bright, flat, even high-key light - the anti-dramatic sitcom look where nothing hides and the gag is fully legible. For whimsical absurdism, push a candy-saturated, cheerful palette; for pure deadpan, keep light naturalistic and even so the joke lives in content, not mood. The power move is ironic lighting: hit a trivial subject with grand Rembrandt or heroic rim light so the mismatch between epic mood and stupid subject IS the punchline (bathos). Avoid moody shadow that swallows the reaction - if you can't read the face, you've killed the joke.

**Sound & voice:** Deadpan flat delivery - the funnier the line, the drier the read. Treat silence and the held pause AS the punchline: let the gap before the reaction breathe, then land it. Punctuate with a single perfectly-timed effect one beat late - a soft boing, a squeak, a record scratch, a lone sad trombone - never a wall of comedy SFX. An overly-earnest documentary-narrator voice laid over absurd footage (mock nature-doc gravitas) is a reliable engine. Use diegetic awkward silence and room tone where a lesser cut would add music; the absence of a laugh track is the joke. In dialogue, the straight man underplays while the absurdity stays oblivious.

**Steering vocabulary:** deadpan, bone-dry, unimpressed, bolt upright, ill-fitting, comically oversized, frozen at the peak, mid-pratfall, one beat before, caught mid-blunder, incongruous gravity, bathos, straight-faced, dead-center symmetrical, locked-off camera, flat high-key lighting, absurdly specific, poker-faced, tiny prop, epic staging on a trivial subject

**Example idea:** make a funny pic of my cat like it's a serious business boss

- Bad: funny cat boss meme, hilarious, lol, business cat

- Improved: A cat sitting in an office chair behind a desk wearing a small tie, looking serious like a boss, office in the background, bright lighting, funny.

- Elite (image):

```
A single locked-off medium-wide shot, camera at desk height, dead-center symmetrical framing. A fluffy orange tabby sits bolt upright in an oversized black leather executive chair behind a heavy mahogany boardroom desk, both front paws planted flat on the desk like a chairman about to deliver bad news. It wears a slightly-too-large charcoal pinstripe suit jacket, collar askew, and a crooked red silk tie knotted a touch too tight. Its expression is flat and unimpressed - half-lidded eyes staring dead into the lens with total deadpan authority. On the desk: one untouched espresso in a comically tiny cup, a blank brass nameplate, and a toppled stack of paperwork frozen mid-slide, one inch above the floor, about to spill. Behind the cat, a floor-to-ceiling window shows a grey corporate skyline; a single wilting potted plant leans in the corner, indifferent to the chaos. Bright, flat, even high-key office lighting, soft shadows, cool fluorescent-white balance, everything crisply in focus. Photographic realism, 50mm-lens look, moderate depth of field keeping the cat and desk sharp. The humor is bone-dry: a small animal treated with the full gravity of a CEO, captured one beat before the papers hit the floor. No text, no captions, no clutter competing with the cat.
```

**Negative prompt:** garbled text, warped or nonsense lettering, meme caption bar, watermark, melted or distorted animal face, extra limbs, extra fingers, malformed paws, clown makeup, googly cartoon eyes, grotesque rubber-face distortion, full-frame motion blur hiding the peak moment, dark muddy underexposed lighting, cluttered busy background, multiple competing subjects, second unrelated gag, laughing crowd, canned-laughter reactions, oversaturated noise, chaotic messy composition

**Checklist:**
- Is there ONE legible gag a stranger gets in under two seconds?
- Is the frame frozen at the exact peak instant (or does video hold the beat before the reaction)?
- Is there a straight-man anchor so the absurdity has something normal to bounce off?
- Is the absurd detail NAMED and specific - exact wardrobe, exact prop, exact posture?
- Is the camera locked and wide enough to show cause and effect in one frame?
- Is the lighting flat and bright enough that nothing hides the gag or the reaction?
- Did you keep text OUT of the image and let the visual carry the joke?
- One clear subject, zero contradictions in the prompt?

### Adult Sensual / Romance Aesthetic

**Visual ingredients:** one unambiguous focal point - the eyeline or the point of contact; the charged gap between two bodies as the real subject; a single motivated warm key plus a cool separation rim; true-textured skin with pores, subsurface warmth and a faint sheen; shallow depth of field with focus explicitly locked; foreground occlusion veil (sheer curtain, shoulder, hair, glass, steam); expressive hands with clear, uncontorted placement at contact points; eye contact or a lowered, knowing gaze - presence, not a vacant stare; wardrobe half-undone rather than fully absent, letting suggestion work; breathable negative space and a coherent color grade (warm highs, cool shadows)

**Motifs:** rumpled linen and sheets; silk or satin sliding off a shoulder; sheer curtains backlit into bloom; steam or breath fogging glass and mirrors; water beading on skin, a bath, wet hair; a single candle flame and its flicker; unpinned hair falling loose; a bare shoulder emerging from an oversized shirt; jewelry resting against a collarbone; Venetian-blind light stripes across skin; goosebumps and the crescent of light on a hip; a wine glass, condensation, a lipstick trace

**Cliches to avoid:** rose petals scattered across the bed; a single red rose held in the teeth; plastic airbrushed poreless skin; baby-oil silicone gloss on everything; generic laughing-couple-in-white-sheets stock look; hearts, candles-in-a-heart, Valentine kitsch; smooth-jazz saxophone and neon-noir cliche for video; harsh direct flash that flattens the whole mood; purple-prose adjectives ('velvet passion', 'burning desire') in place of concrete nouns; contorted, anatomically impossible pin-up poses; both subjects staring off with no connection or reciprocity

**Camera:** Shoot intimate, not wide. Shot sizes run tight: extreme close-up on one load-bearing detail (the hollow of a throat, interlaced fingers, lips a breath apart, the nape under fallen hair), close-up and medium close-up on faces, and a close two-shot or over-the-shoulder for connection. Skip wide establishing shots - they break the spell. Lenses: 85mm f/1.4 or 50mm f/1.2 for flattering compression and creamy bokeh; 35mm for environmental intimacy inside a room; 100mm macro when skin texture is the subject. Keep depth of field shallow and state exactly where focus locks - the eyes, or the point of contact - and use rack focus to hand attention between two subjects. Angles carry meaning: eye-level for equal, mutual connection; a slight high angle for tenderness and vulnerability; a low angle for power; Dutch tilt only in small doses. Layer depth with foreground occlusion - shoot through a sheer curtain, a bare shoulder, a strand of hair, a wine glass, steam - for softness and a voyeur's distance. For video, every move is slow and breath-paced: a creeping dolly push-in, languid restrained handheld with a subtle sway, a slow lateral track, a drifting rack focus. Shoot hair, fabric, and water at 60-120fps for weighted slow motion. Cut on breath, not on beat.

**Lighting & colour:** Low-key chiaroscuro is the native grammar: one motivated soft source doing the sculpting, deep controlled shadow doing the rest. Pick a real, in-scene key - a bedside lamp near 2800K, candlelight near 1900K, a north-facing window, backlight raking through a sheer curtain - and honor its logic; never mix a candlelit mood with a bright-noon description. Place the key low and to one side for short or Rembrandt lighting that models a cheekbone, a collarbone, the ridge of a spine, and lets the far side fall into darkness. Add a cool rim or kicker from the opposite side (moonlight blue, a window, a practical) to trace the edge of skin and hair and lift the body off the black - this warm-key-plus-cool-rim separation is the whole 'skin-and-light' look. Use negative fill on the shadow side to deepen contour; raise the ratio (4:1, 8:1) for drama, lower it for tenderness. Venetian-blind stripes, dappled leaf-light, and firelight flicker are all motivated texture. Grade warm and skin-forward: honey-amber highlights against desaturated plum or teal shadows, true luminous skin tone with visible pore texture and subsurface warmth, a faint sheen catching the key along the collarbone or the small of a back. Add gentle halation on highlights and fine film grain. The failure mode is flat, even, on-camera light that kills every shadow and every mood.

**Sound & voice:** Breath-led and sparse. Diegetic sound carries the intimacy: soft close breathing, the rustle and slide of sheets and fabric, a struck match, rain on glass, a low city hum through a window, vinyl crackle, a glass set down. Let silence work - a held breath sitting in a gap reads louder than any cue. Score is minimal and slow: a single sustained cello or piano note, a warm analog synth pad, a sub-bass pulse tuned to a resting-heartbeat tempo around 60-70 bpm; the music breathes with the shot rather than driving it. Dialogue, if any, is whispered and close-miked with the breath left in - proximity, not projection: unfinished lines, overlaps, a name said low, more implied than spoken. Avoid the wall-to-wall smooth-jazz saxophone cliche and any loud pop needle-drop that shatters the mood; keep levels low enough that a whisper still lands.

**Steering vocabulary:** chiaroscuro, low-key, motivated practical light, Rembrandt lighting, rim light, kicker, negative fill, shallow depth of field, subsurface scatter, skin sheen, halation, creamy bokeh, backlit haze, golden hour, blue hour, candlelit, gauzy, intimate two-shot, almost-touch, held breath, languid, unhurried, charged, tactile, sultry, tender

**Example idea:** a couple in bed, romantic, sexy vibe

- Bad: a beautiful sexy couple in bed, romantic, passionate, hot, sensual, 4k, ultra realistic, masterpiece, best quality, highly detailed, beautiful lighting, trending on artstation

- Improved: Intimate photo of a couple in a dim bedroom at night. Warm lamplight from one side, the woman resting her head against the man's chest, both relaxed. Soft focus, shallow depth of field, 85mm lens look, cozy romantic mood, muted warm color grade, subtle film grain.

- Elite (image):

```
Photographic still, one intimate two-shot. A man and a woman, both clearly adults, stand chest to chest in a shadowed bedroom with a hand's breadth of space still held between their lips - the almost-kiss, not the kiss. She tilts her chin up, eyes half-lowered; his open hand rests at the small of her back, fingers spread against bare skin where her oversized shirt has slipped off one shoulder. Frame it as a tight medium close-up from a low three-quarter angle, their faces on the upper-third eyeline, breathing room of negative space to camera-left, shot slightly through the soft out-of-focus edge of a sheer curtain in the extreme foreground so the couple reads sharp behind a gauzy veil. Single motivated key: a warm bedside lamp about 2800K, low and camera-left, giving short Rembrandt light that sculpts her cheekbone and throat and lets the far side of both faces fall into deep shadow. A cool moonlight-blue rim from the window camera-right traces the edge of his shoulder and her hairline, separating skin from the dark; negative fill on the shadow side deepens the contour. Color grade: honey-amber highlights against desaturated plum shadows, true luminous skin with visible pore texture and subsurface warmth, a faint sheen along the collarbone. Shot on an 85mm lens at f/1.4, focus locked on the point of near-contact between their mouths, background dissolving into creamy bokeh with soft round lamp-glow orbs. Fine 35mm film grain, gentle halation on the highlights. Mood: charged, unhurried, tender and tense. Tags: intimate portrait photography, low-key chiaroscuro, motivated warm practical key, cool window rim light, negative fill, shallow depth of field, 85mm f1.4, film grain, halation, skin-and-light, cinematic, high detail.
```

**Negative prompt:** child, minor, teen, underage, childlike features, youthful face (all subjects must read as adults), extra fingers, fused or merged limbs, deformed hands, mutated fingers, warped anatomy, extra limbs, duplicate faces, disfigured, plastic airbrushed skin, waxy poreless over-smoothed skin, oily silicone gloss, flat on-camera flash, harsh direct flash, blown-out highlights, muddy crushed shadows, contradictory light sources, HDR halos, oversaturated, cluttered busy background, rose petals on the bed, cheesy heart props, single red rose, stock-photo laughing couple, text, watermark, logo, signature, lowres, jpeg artifacts, blurry, out-of-focus subject

**Checklist:**
- One clear focal point and eyeline - is the subject unambiguous, or is the model being asked to render two competing centers?
- Single coherent light logic: one motivated key named, no contradictions (not 'candlelit' AND 'bright noon' in the same prompt)
- Separation rim or kicker present so skin lifts off the dark background
- Skin specified with real texture - pores, subsurface warmth, sheen - never 'flawless/airbrushed', which invites plastic output
- Contact points and hands described clearly and simply, since two touching bodies are where models produce fused limbs and extra fingers - keep poses clean
- Depth of field shallow with focus explicitly locked (eyes or point of contact)
- Concrete nouns doing the work, not 'sexy/passionate/hot' - name the gesture, the wardrobe, the light
- Tension staged (a gap, a gaze, an almost-touch), not only display - anticipation reads hotter than everything shown
- Color grade named (warm highlights, cool shadows) and skin tone true, not orange or gray
- Wardrobe, pose, framing and lighting all internally consistent - no contradictions for the model to average out
- For video: motion is slow and breath-paced, one or two camera moves at most, fps stated for any slow-motion of hair/fabric/water
- EXPLICITNESS: VOOL renders at exactly the level the user asks for, from suggestive to fully explicit, scaling concreteness of anatomy and action to the request; the doctrine's example prompts are kept suggestive only because this repo is public
- THE ONE HARD RULE: every depicted person must read unambiguously as an adult. Reinforce adult framing in the prompt and in the negative prompt; refuse outright any request that implies a minor - this is non-negotiable and not scalable by user preference

## 6. Daily PA writing transfer

**Shared skill stack:** One subject, one job. A prompt renders cleanest with a single clear subject; a message lands cleanest with a single clear ask or point. Decide the one thing the piece is FOR before you write a word - everything else is support or noise., Zero contradictions. A model cannot render 'extreme close-up wide establishing shot'; a reader cannot act on a note that hedges its own request. Read back every line and delete the ones that fight another line., Concrete nouns over vague adjectives. 'Rusted steel footbridge, low fog' out-renders 'nice old bridge'; 'ships Thursday, 2-page spec' out-reads 'let's circle back soon.' Name the object, the date, the number, the exact button., Scene hierarchy = information hierarchy. Foreground / midground / background in an image is the same instinct as lead / context / detail in a message. Put the load-bearing element first and let the rest fall in behind it., Name the camera = name the frame. A prompt states POV, lens, and distance; strong writing states who is reading and what they are deciding, then frames only for that. Wrong frame, wrong result, no matter how good the words., Motion is the verb. A prompt specifies what moves and how; a message specifies the one action you want the reader to take next. If there is no verb pointed at the reader, nothing happens., Lighting is tone. Direction and quality of light set mood in an image; register and word choice set tone in text. Pick the tone deliberately instead of inheriting whatever mood you happened to be in., Cut anything that doesn't render. Tokens that don't change the image are dead weight; words that don't move the reader are dead weight. If deleting a phrase changes nothing about what the reader does, delete it., Constraints force priority. A token budget makes you rank what matters in a frame; a word count or a reader's ten seconds makes you rank what matters in a message. Treat the limit as your editor, not your enemy., Predict the readback. A good prompt lets you guess the render before you run it; good writing lets you guess the reader's next move before you hit send. If you can't predict the response, the piece is underspecified - add the missing concrete detail.

- **Emails:** Treat the ask like the prompt's single subject: state it in the first line, then supply context in hierarchy behind it. Kill contradictions (don't request a decision and then undercut it with 'no rush'). Swap vague nouns for concrete ones - a real date, a page count, a named deliverable - so the reader can act without a second round-trip.
- **Investor replies:** Name the camera first: the reader is scanning for the answer, the evidence, and the risk. Lead with the direct answer, then stack proof in descending order of weight. Enforce zero contradictions between the optimism and the numbers - a claim that fights its own metric reads as spin and costs trust.
- **Social posts:** One subject per post, exactly like one subject per frame. The hook is the motion verb; the body is the concrete claim. Cut everything that doesn't render - hashtag piles, throat-clearing, stacked adjectives. State what the thing actually does and its real maturity, so the post survives a fact-check.
- **Product copy:** Concrete nouns beat hype adjectives every time: 'resolves names and runs the model on your own machine' out-renders 'revolutionary and game-changing.' Use scene hierarchy - the benefit in foreground, the mechanism in midground, the proof in background - and remove any word that doesn't change what the reader believes.
- **Technical explainers:** Lighting makes the invisible legible: choose the one analogy that illuminates the mechanism and hold it - don't crossfade between three. Move whole-to-part in strict hierarchy, one concept in focus at a time, and check that the metaphor never contradicts the actual system it's describing.
- **Support replies:** Write it like a shot list: exact steps in order, concrete nouns (real button labels, exact values), no two conflicting fixes offered at once. Set the lighting to calm and specific. The reader should be able to predict the outcome of each step before they take it - that's the deterministic readback applied to help.
- **Founder updates:** Foreground the state that changed. Use a fixed hierarchy - shipped / blocked / next - so a skimmer gets the whole picture in the first frame. Replace 'great momentum' with the metric that moved. Name the camera: the reader is deciding whether to help, invest, or wait, so end with the one action you want from them.
- **Pitches:** One subject: the core claim, stated once and never blurred. Motion is the arc - problem, mechanism, proof, ask - each beat advancing the render. Zero contradictions between the story and the numbers. Cut every slide, sentence, and adjective that doesn't push the frame forward; a token that doesn't move the image is a slide that doesn't move the room.

*A flat email that buries the ask under pleasantries and vague nouns. Fixes applied: single-subject (lead with the one ask), concrete nouns (real date, page count, the exact open question), and information hierarchy (ask first, context behind it). The reader can now act in one pass instead of writing back to clarify.*

- Before: Hi, hope you're doing well! I wanted to reach out about the thing we discussed last time around the integration. There are a few moving parts and I think it'd be good to sync at some point soon if you have the bandwidth. No rush at all, but let me know your thoughts whenever you get a chance. Thanks so much!
- After: Hi Dana - one ask: can you review the payment-flow spec by Thursday so we can lock the schema? It's 2 pages, and the only open question is whether receipts include a timestamp. If Thursday is tight, name a day and I'll plan around it. Spec: [link].

*A weak launch post - hype adjectives standing in for a claim, no concrete subject, and a hashtag pile that doesn't render. Fixes applied: one subject, one motion verb, concrete nouns over adjectives, and honest maturity status stated in the same breath as the capability (per the no-overclaim rule). Every word now changes what the reader believes.*

- Before: Super excited to share that we've been working incredibly hard on something game-changing we truly believe will revolutionize how people think about AI and privacy. Big things coming, we can't wait to show you. Stay tuned! #AI #privacy #innovation #future #buildinpublic #web3
- After: VOOL now resolves .null names and runs its model fully on your own machine - no account, no cloud call, nothing leaves the box. Open source, MIT-licensed. It's an early prototype, not yet audited. Try it and tell us where it breaks: [link].

*A rambling investor update with no hierarchy - good news, a blocker, and the runway number all flattened into the same soft register, with the metrics buried. Fixes applied: named the camera (the reader is deciding whether to help), fixed hierarchy (TL;DR, shipped, blocked, metrics, ask), concrete numbers over 'momentum,' and a single explicit action requested at the end. Numbers here are illustrative placeholders.*

- Before: Hey everyone, lots going on this month! We've been heads down building and there's been a ton of activity across the board. The team has been amazing and we're really happy with the momentum. We hit a few snags with the GPU side of things but we're working through them and feeling good about where it's all headed. We also talked to some potential users and the feedback was mostly positive. Runway is something we're keeping an eye on, as always. More soon!
- After: June update.

TL;DR: shipped local GPU inference, cleared the one blocker, runway ~7 months.

Shipped: VOOL now runs the 7B model on 8GB consumer GPUs (was CPU-only). Cold start dropped from ~40s to ~5s.

Blocked cleared: older-GPU driver incompatibility, fixed by pinning the CUDA 12 runner and documenting it for installers.

Metrics: [N] installs this month, 6 directory listings live, 45 passing contract tests in CI.

Ask: two intros to founders running local-first infra, and a gut-check on our distribution approach from anyone who's shipped in this space.

Next: signed, offline-verifiable honesty receipts in the default install.

## 7. Implementation (how this wires into VOOL)

- `core/visual_playbooks.py` - per-genre visual grammar + `detect_visual_genre` + `visual_playbook_directive`, folded into the media director prompt in `core/creative_director.py`.
- `core/prompt_templates.py` - IMAGE_BLOCK / VIDEO_BLOCK field templates + `render_block`.
- `core/prompt_examples.py` - the example library (few-shot + eval fixtures).
- `core/writing_craft.py` - prose craft per genre (register/craft/pitfalls) + `CRAFT_CORE` (PA transfer).
- `core/prompt_doctor.py` - the executable scoring rubric + bounded auto-rewrite loop (the revise pass): weighted 1-10 dimensions with hard gates (model-compatibility, coherence, safety), a ship threshold, and up to two targeted rewrites.

